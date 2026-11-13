#!/usr/bin/env python3
"""RUST-UNIT-TABLE16 — owned adapter fault-injection tests (self-contained).

Verifies ``narwhal3d/native_unit_table.py`` against the integrated Rust
library AND against self-contained, in-process ctypes-compatible mocks that
each return one specific fault.  Every fault must raise ``RuntimeError`` — the
host checks the reply sizes BEFORE allocating any buffer, and no fault may
ever fall back to Python values.

PORTABILITY: the fault path uses NO prebuilt fixture and NO private cache.
The mocks are ordinary Python objects that satisfy the small ctypes surface
the adapter touches (assignable ``argtypes``/``restype`` + ``__call__``), and
they are installed by patching ``ctypes.CDLL`` for the duration of one call.
The happy path and the binding-lifetime tests still use the REAL integrated
library — only the fault *binding* is mocked.  The suite therefore runs from a
clean checkout with no ``~/.cache`` artifacts present.

Run with the bundled CPython (no bpy):

  NARWHAL_PURE_CORE=<integrated lib> \\
      tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 \\
      spike/test_unit_table_owned.py
"""
import ctypes
import importlib
import importlib.util
import math
import os
import sys
import types
import unittest
import weakref
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADAPTER = os.path.join(ROOT, 'narwhal3d', 'native_unit_table.py')
TEST_PATH = os.path.abspath(__file__)
INTEGRATED_LIB = os.environ.get('NARWHAL_PURE_CORE', '')
if not INTEGRATED_LIB or not os.path.isfile(INTEGRATED_LIB):
    INTEGRATED_LIB = os.path.join(
        ROOT, 'native', 'pure_core', 'target', 'x86_64-unknown-linux-gnu',
        'release', 'libnarwhal_pure_core.so')
if not os.path.isfile(INTEGRATED_LIB):
    sys.exit('integrated library not found: %s' % INTEGRATED_LIB)


def _fresh_adapter(env_value):
    """Load a fresh copy of the adapter module with the given env value.

    If ``env_value`` is ``None``, ``NARWHAL_PURE_CORE`` is deleted from the
    environment so the packaged library is used.
    """
    if env_value is None:
        os.environ.pop('NARWHAL_PURE_CORE', None)
    else:
        os.environ['NARWHAL_PURE_CORE'] = env_value
    if 'narwhal3d' not in sys.modules:
        pkg = types.ModuleType('narwhal3d')
        pkg.__path__ = [os.path.join(ROOT, 'narwhal3d')]
        sys.modules['narwhal3d'] = pkg
    mod_name = 'narwhal3d.native_unit_table'
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, ADAPTER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module  # register BEFORE exec (coords.py pattern)
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(mod_name, None)
        raise
    return module


# ---------------------------------------------------------------------------
# Self-contained ctypes-compatible mocks (no prebuilt .so, no private cache).
# ---------------------------------------------------------------------------
def _write_u64(byref_out, value):
    """Write a uint64 through a ``ctypes.byref`` out-pointer (as Rust would)."""
    ctypes.cast(byref_out, ctypes.POINTER(ctypes.c_uint64)).contents.value = value


class _MockFunc:
    """A callable satisfying the small ctypes surface the adapter sets/reads:
    ``argtypes``/``restype`` are assignable attributes; ``__call__`` runs the
    supplied behaviour.  It emulates one CDLL symbol (it is not a real CDLL)."""

    def __init__(self, call):
        self.argtypes = None
        self.restype = None
        self._call = call

    def __call__(self, *args):
        return self._call(*args)


class _MockLibrary:
    """Emulates the attributes the adapter reads off a ``ctypes.CDLL`` handle:
    ``narwhal_pure_core_abi_version`` and ``narwhal_pure_core_unit_table``."""

    def __init__(self, abi_version=1, table_call=None):
        self.narwhal_pure_core_abi_version = _MockFunc(lambda: abi_version)
        self.narwhal_pure_core_unit_table = _MockFunc(
            table_call if table_call is not None else (lambda *a: 0))


def _fault_call(fault):
    """A two-pass ``narwhal_pure_core_unit_table`` reply carrying one fault.

    ``fault`` is one of: status, capacity, count_zero, nonfinite, dup, empty,
    schema, pass2_rc.  (``stale`` is a core-ABI fault, handled at the library
    level by :func:`_mock_library`.)

    The reply is table-driven so pass-1 and pass-2 sizes agree for every fault
    EXCEPT ``schema`` (where pass-2 sizes differ, on purpose).  Pass 1 is
    detected by ``names is None`` and reports the fault-specific sizes;
    pass 2 fills the buffers and applies the fault-specific reply.
    """
    # fault -> (pass1 names_len, pass1 count, pass1 rc,
    #          pass2 payload, pass2 values, pass2 (onl, oc), pass2 rc)
    #   valid  : two real entries, both finite and positive
    #   dup    : two entries, both 'Alpha'            (duplicate)
    #   empty  : one real entry + one empty           (empty name)
    #   nonfinite: values[1] = +inf                   (non-finite)
    #   schema : pass-2 sizes differ from pass-1      (schema mismatch)
    #   pass2_rc: pass-2 rc != 0                      (bad reply code)
    _SPECS = {
        'valid':     (10, 2, -3, b'Alpha\nBeta',   (1.0, 1.0),  None, 0),
        'dup':       (11, 2, -3, b'Alpha\nAlpha',  (1.0, 1.0),  None, 0),
        'empty':     ( 6, 2, -3, b'Alpha\n',       (1.0, 1.0),  None, 0),
        'nonfinite': (10, 2, -3, b'Alpha\nBeta',   (1.0, math.inf), None, 0),
        'schema':    (10, 2, -3, b'Alpha\nBeta',   (1.0, 1.0),  (999, 999), 0),
        'pass2_rc':  (10, 2, -3, b'Alpha\nBeta',   (1.0, 1.0),  None, 7),
        # pass-1-only faults (no pass 2 is reached):
        'status':     (10, 2,    5, None, None, None, None),
        'capacity':   (10, 1000, -3, None, None, None, None),
        'count_zero': (10, 0,    -3, None, None, None, None),
    }
    (p1_len, p1_count, p1_rc, p2_payload, p2_values,
     p2_sizes, p2_rc) = _SPECS[fault]

    def call(names, nc, values, vc, onl, oc):
        if names is None:
            # ---- pass 1 (query sizes, zero capacity) ----
            _write_u64(onl, p1_len)
            _write_u64(oc, p1_count)
            return p1_rc
        # ---- pass 2 (fill the buffers; sizes must match pass 1) ----
        n = len(p2_payload)
        for i, b in enumerate(p2_payload):
            names[i] = b
        for i, v in enumerate(p2_values):
            values[i] = v
        if p2_sizes is not None:
            _write_u64(onl, p2_sizes[0])
            _write_u64(oc, p2_sizes[1])
        else:
            _write_u64(onl, n)
            _write_u64(oc, len(p2_values))
        return p2_rc
    return call


def _mock_library(fault):
    """Build a mock CDLL handle for a fault.  ``stale`` is a core-ABI-2 lib;
    every other fault is a valid core-ABI-1 lib whose unit-table reply is bad."""
    if fault == 'stale':
        return _MockLibrary(abi_version=2)
    return _MockLibrary(abi_version=1, table_call=_fault_call(fault))


class TestUnitTableAdapter(unittest.TestCase):

    # ------------------------------------------------------------------
    # Happy path — REAL integrated library (not mocked)
    # ------------------------------------------------------------------
    def test_happy_path(self):
        m = _fresh_adapter(INTEGRATED_LIB)
        r = m.unit_table()
        self.assertEqual(r['status'], 'ok')
        self.assertEqual(r['count'], 24)
        self.assertEqual(r['length'], 245)
        self.assertEqual(r['schema'], (24, 245, 192))
        self.assertEqual(len(r['names']), 24)
        self.assertEqual(len(r['table']), 24)
        for v in r['table'].values():
            self.assertTrue(math.isfinite(v))
            self.assertGreater(v, 0.0)
        self.assertEqual(r['table']['Millimeters'], 1.0)
        self.assertEqual(r['table']['Inches'], 25.4)
        self.assertEqual(r['table']['Miles'], 1_609_344.0)
        self.assertEqual(len(set(r['names'])), 24)

    def test_warm_path_second_call(self):
        m = _fresh_adapter(INTEGRATED_LIB)
        r1 = m.unit_table()
        r2 = m.unit_table()
        self.assertEqual(r1['table'], r2['table'])
        self.assertEqual(r1['count'], r2['count'])
        self.assertEqual(r1['names'], r2['names'])
        self.assertIsNotNone(m._binding)
        self.assertIsNotNone(m._binding.library)

    def test_signature_retention(self):
        m = _fresh_adapter(INTEGRATED_LIB)
        m.unit_table()
        sig = m._binding.signature
        self.assertIsNotNone(sig)
        self.assertEqual(len(sig), 2)
        self.assertEqual(sig[0], 'env')
        self.assertEqual(sig[1], INTEGRATED_LIB)

    # ------------------------------------------------------------------
    # Env resolution (REAL libs — packaged / explicit / refusal)
    # ------------------------------------------------------------------
    def test_unset_env_uses_packaged(self):
        """With NARWHAL_PURE_CORE unset, the packaged library is loaded."""
        m = _fresh_adapter(None)
        r = m.unit_table()
        self.assertEqual(r['status'], 'ok')
        self.assertEqual(r['count'], 24)
        self.assertEqual(m._binding.signature, ('packaged', None))

    def test_empty_env_refuses(self):
        """NARWHAL_PURE_CORE='' must raise, NOT fall through to packaged."""
        m = _fresh_adapter('')
        with self.assertRaises(RuntimeError) as ctx:
            m.unit_table()
        self.assertIn('empty', str(ctx.exception).lower())
        self.assertIn('not replayed', str(ctx.exception))
        self.assertIsNone(m._binding)

    def test_literal_packaged_string(self):
        """NARWHAL_PURE_CORE='<packaged>' is a literal path, not a sentinel."""
        m = _fresh_adapter('<packaged>')
        sig = m._signature_from_env()
        self.assertEqual(sig, ('env', '<packaged>'))
        with self.assertRaises(RuntimeError):
            m.unit_table()

    def test_warm_path_no_path_touched(self):
        """On a warm hit, Path.expanduser and Path.resolve must NOT be called.

        The warm path returns the retained record after a signature compare only;
        it must not touch Path and must not take the lock.
        """
        import pathlib
        m = _fresh_adapter(INTEGRATED_LIB)
        m.unit_table()  # cold path — legitimately calls Path.expanduser
        real_expanduser = pathlib.Path.expanduser
        real_resolve = pathlib.Path.resolve
        calls = {'e': 0, 'r': 0}

        def spy_expanduser(self, *a, **k):
            calls['e'] += 1
            return real_expanduser(self, *a, **k)

        def spy_resolve(self, *a, **k):
            calls['r'] += 1
            return real_resolve(self, *a, **k)

        with mock.patch.object(pathlib.Path, 'expanduser', spy_expanduser), \
             mock.patch.object(pathlib.Path, 'resolve', spy_resolve):
            m.unit_table()  # warm path
        self.assertEqual(calls['e'], 0, 'warm path must not call Path.expanduser')
        self.assertEqual(calls['r'], 0, 'warm path must not call Path.resolve')

    def test_owner_retained_across_switch(self):
        """The _Binding record is the CDLL's strong reference for its life.

        Release the module's binding (as a signature switch does); the CDLL must
        stay alive because the record still holds it.
        """
        import gc
        m = _fresh_adapter(INTEGRATED_LIB)
        m.unit_table()
        old_record = m._binding
        self.assertIsNotNone(old_record)
        self.assertIsNotNone(old_record.library)
        self.assertIsNotNone(old_record.function)
        lib_ref = weakref.ref(old_record.library)
        m._binding = None
        gc.collect()
        self.assertIsNotNone(lib_ref(), 'old CDLL must be retained by its record')
        del old_record
        gc.collect()

    # ------------------------------------------------------------------
    # Fault: missing library (real CDLL -> OSError; no mock, no cache)
    # ------------------------------------------------------------------
    def test_missing_library_raises(self):
        m = _fresh_adapter('/no/such/library.so')
        with self.assertRaises(RuntimeError) as ctx:
            m.unit_table()
        self.assertIn('not replayed', str(ctx.exception))

    # ------------------------------------------------------------------
    # Faults (self-contained mocks — the binding is mocked, not a .so)
    # ------------------------------------------------------------------
    def test_stale_abi_raises(self):
        m = _fresh_adapter(INTEGRATED_LIB)
        with mock.patch('ctypes.CDLL', return_value=_mock_library('stale')):
            with self.assertRaises(RuntimeError) as ctx:
                m.unit_table()
        self.assertIn('Stale Rust core ABI', str(ctx.exception))

    def test_status_fault_raises(self):
        m = _fresh_adapter(INTEGRATED_LIB)
        with mock.patch('ctypes.CDLL', return_value=_mock_library('status')):
            with self.assertRaises(RuntimeError) as ctx:
                m.unit_table()
        self.assertIn('pass-1', str(ctx.exception))
        self.assertIn('not replayed', str(ctx.exception))

    def test_capacity_fault_raises(self):
        m = _fresh_adapter(INTEGRATED_LIB)
        with mock.patch('ctypes.CDLL', return_value=_mock_library('capacity')):
            with self.assertRaises(RuntimeError) as ctx:
                m.unit_table()
        self.assertIn('out of range', str(ctx.exception))
        self.assertIn('not replayed', str(ctx.exception))

    def test_count_zero_raises(self):
        m = _fresh_adapter(INTEGRATED_LIB)
        with mock.patch('ctypes.CDLL', return_value=_mock_library('count_zero')):
            with self.assertRaises(RuntimeError) as ctx:
                m.unit_table()
        self.assertIn('count', str(ctx.exception).lower())
        self.assertIn('out of range', str(ctx.exception))

    def test_nonfinite_value_raises(self):
        m = _fresh_adapter(INTEGRATED_LIB)
        with mock.patch('ctypes.CDLL', return_value=_mock_library('nonfinite')):
            with self.assertRaises(RuntimeError) as ctx:
                m.unit_table()
        self.assertIn('non-finite', str(ctx.exception))
        self.assertIn('not replayed', str(ctx.exception))

    def test_duplicate_names_raises(self):
        m = _fresh_adapter(INTEGRATED_LIB)
        with mock.patch('ctypes.CDLL', return_value=_mock_library('dup')):
            with self.assertRaises(RuntimeError) as ctx:
                m.unit_table()
        self.assertIn('duplicate', str(ctx.exception).lower())
        self.assertIn('not replayed', str(ctx.exception))

    def test_empty_name_raises(self):
        m = _fresh_adapter(INTEGRATED_LIB)
        with mock.patch('ctypes.CDLL', return_value=_mock_library('empty')):
            with self.assertRaises(RuntimeError) as ctx:
                m.unit_table()
        self.assertIn('empty name', str(ctx.exception).lower())
        self.assertIn('not replayed', str(ctx.exception))

    def test_schema_mismatch_raises(self):
        m = _fresh_adapter(INTEGRATED_LIB)
        with mock.patch('ctypes.CDLL', return_value=_mock_library('schema')):
            with self.assertRaises(RuntimeError) as ctx:
                m.unit_table()
        self.assertIn('schema mismatch', str(ctx.exception))
        self.assertIn('not replayed', str(ctx.exception))

    def test_pass2_rc_fault_raises(self):
        m = _fresh_adapter(INTEGRATED_LIB)
        with mock.patch('ctypes.CDLL', return_value=_mock_library('pass2_rc')):
            with self.assertRaises(RuntimeError) as ctx:
                m.unit_table()
        self.assertIn('pass-2', str(ctx.exception))
        self.assertIn('not replayed', str(ctx.exception))

    # ------------------------------------------------------------------
    # No input/result caching (REAL lib)
    # ------------------------------------------------------------------
    def test_no_result_caching(self):
        m = _fresh_adapter(INTEGRATED_LIB)
        r1 = m.unit_table()
        r1['table']['Inches'] = 999.0
        r2 = m.unit_table()
        self.assertEqual(r2['table']['Inches'], 25.4)
        self.assertIsNot(r1, r2)

    # ------------------------------------------------------------------
    # Count check: count from 24 fixed names, not values (REAL lib)
    # ------------------------------------------------------------------
    def test_count_matches_name_list_length(self):
        m = _fresh_adapter(INTEGRATED_LIB)
        r = m.unit_table()
        self.assertEqual(r['count'], 24)
        self.assertEqual(len(r['names']), 24)
        self.assertEqual(len(r['table']), 24)

    # ------------------------------------------------------------------
    # Portability: the fault path needs NO prebuilt fixture / private cache
    # ------------------------------------------------------------------
    def test_suite_is_self_contained(self):
        """The whole fault path is in-process and runs from a clean checkout.

        An earlier revision required prebuilt C shared objects (one per
        fault) kept in a private scratch directory under the user's home,
        plus a startup gate that exited the suite if those objects were
        missing.  A public test gate must not depend on out-of-project
        artifacts.

        Here the mocks are pure Python objects (see ``_MockLibrary`` /
        ``_MockFunc``) installed by patching ``ctypes.CDLL`` for a single
        call.  This test asserts the mock is an in-process Python object
        (not a file-backed fixture) and that a fault mock still drives the
        adapter to a fail-closed ``RuntimeError``.  No ``.so`` is loaded
        for the fault path, so the suite runs without any pre-built
        artifact and without reading anything from disk outside the repo.
        """
        # 1) The mock is a pure Python object, not a file-backed fixture.
        fake = _mock_library('dup')
        self.assertIsInstance(fake, _MockLibrary,
                              'the fault binding must be an in-process '
                              'Python mock, not a prebuilt shared object')
        self.assertIsInstance(fake.narwhal_pure_core_unit_table, _MockFunc)
        self.assertIsInstance(fake.narwhal_pure_core_abi_version, _MockFunc)

        # 2) The fault path is genuinely in-process: a mock still drives the
        #    adapter to a fail-closed raise, with no file I/O for the fixture.
        m = _fresh_adapter(INTEGRATED_LIB)
        with mock.patch('ctypes.CDLL', return_value=_mock_library('dup')):
            with self.assertRaises(RuntimeError) as ctx:
                m.unit_table()
        self.assertIn('duplicate', str(ctx.exception).lower())
        self.assertIn('not replayed', str(ctx.exception))

        # 3) No prebuilt fixture file is loaded for the fault path: the mock
        #    is the binding, not a .so path.  (The happy path still uses the
        #    real integrated library, which is the point of the portability
        #    split.)
        self.assertIsInstance(fake.narwhal_pure_core_unit_table, _MockFunc)


if __name__ == '__main__':
    unittest.main(verbosity=2)
