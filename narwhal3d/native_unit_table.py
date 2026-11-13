"""Required native unit-table ABI. Host admission only — no scale values in Python.

The Rust core owns the 24 fixed unit scales in ``UNIT_SYSTEM_MM``
(``native/pure_core/src/numparse.rs``).  This module reads that table out of
the integrated library via the two-pass ``narwhal_pure_core_unit_table`` C ABI
and returns a validated result — never a scale value invented on the Python side.
``units.py`` (production) uses this adapter as the single source for
``UNIT_SCALE_MM`` with ``UNIT_SCALE_SOURCE == 'rust'``.

Missing/stale libraries and malformed replies are installation/protocol errors —
they are raised as ``RuntimeError``, never replayed in Python.  There is NO
fallback: the caller must handle the exception.

The two-pass protocol bounds all allocations BEFORE they happen:
pass 1 queries the exact ``names_len`` and ``count`` with zero capacity;
the host validates those sizes against fixed caps BEFORE allocating any buffer;
pass 2 allocates exactly the reported sizes and writes the data.
The adapter validates every field (count, names, values) and the schema
(pass-1 vs pass-2 sizes) BEFORE returning.

The immutable binding retains its CDLL for the life of the process.
The warm path checks the exact ``('env', exact)`` / ``('packaged', None)``
signature BEFORE any ``Path`` work or lock acquisition.  There is NO input or
result caching: every call to :func:`unit_table` re-reads the table from the
library.
"""
import ctypes
import math
import os
import sys
import threading
from pathlib import Path
from typing import Any, NamedTuple

__all__ = ['unit_table']

# ---------------------------------------------------------------------------
# ABI constants (numparse.rs narwhal_pure_core_unit_table)
# ---------------------------------------------------------------------------
_CORE_ABI_VERSION_REQUIRED = 1

# Rust status codes (numparse.rs L224–247):
_RC_OK = 0
_RC_SHORT = -3  # insufficient capacity — expected for pass 1 with cap=0

# Host-side bounds BEFORE allocation (mirror Rust's own caps with headroom):
_MAX_COUNT = 256
_MAX_NAMES_LEN = 64 * 256  # 16 KiB


def _library_name():
    if sys.platform == 'win32':
        return 'narwhal_pure_core.dll'
    if sys.platform == 'darwin':
        return 'libnarwhal_pure_core.dylib'
    return 'libnarwhal_pure_core.so'


_ENV_PATH = 'NARWHAL_PURE_CORE'
_PACKAGED_LIBRARY = Path(__file__).resolve().parent / 'native' / _library_name()
_PACKAGED_LIBRARY_STR = str(_PACKAGED_LIBRARY)


# ---------------------------------------------------------------------------
# Immutable binding (ONE record; retains CDLL for the life of the process)
# ---------------------------------------------------------------------------
class _Binding(NamedTuple):
    signature: tuple  # ('env', exact_value) or ('packaged', None)
    library: ctypes.CDLL  # retained for process life
    function: Any  # the bound narwhal_pure_core_unit_table


_binding: _Binding | None = None
_lock = threading.Lock()


def _signature_from_env():
    """Compute the binding signature from the environment.

    Returns ``('env', exact_value)`` when ``NARWHAL_PURE_CORE`` is set (even to
    an empty string — which is an error, not a fallback trigger), or
    ``('packaged', None)`` when the variable is unset.

    Raises ``RuntimeError`` when the variable is set to an empty string:
    an empty path is not a valid library location, and falling through to the
    packaged library would be a silent misresolution.
    """
    explicit = os.environ.get(_ENV_PATH)
    if explicit is None:
        return ('packaged', None)
    if explicit == '':
        raise RuntimeError(
            'NARWHAL_PURE_CORE is set to an empty string; '
            'refusing to fall back to the packaged library; not replayed')
    return ('env', explicit)


def _required_binding():
    """Return the immutable :class:`_Binding` record, or raise.

    The warm path checks the signature BEFORE any ``Path`` work or lock
    acquisition.  An empty ``NARWHAL_PURE_CORE`` raises immediately — it is
    never treated as unset.
    """
    global _binding
    signature = _signature_from_env()
    # Warm path: no Path, no lock.
    if _binding is not None and _binding.signature == signature:
        return _binding
    with _lock:
        # Recheck after acquiring the lock.
        if _binding is not None and _binding.signature == signature:
            return _binding
        if signature[0] == 'env':
            path = Path(signature[1]).expanduser()
        else:
            path = _PACKAGED_LIBRARY
        _binding = None  # clear any stale binding for the new signature
        try:
            library = ctypes.CDLL(str(path))
            core = library.narwhal_pure_core_abi_version
            core.argtypes = []
            core.restype = ctypes.c_uint32
            if core() != _CORE_ABI_VERSION_REQUIRED:
                raise RuntimeError(
                    'Stale Rust core ABI (need {}, got {}); not replayed'
                    .format(_CORE_ABI_VERSION_REQUIRED, core()))
            binding = library.narwhal_pure_core_unit_table
            _ByteP = ctypes.POINTER(ctypes.c_ubyte)
            _DoubleP = ctypes.POINTER(ctypes.c_double)
            binding.argtypes = [
                _ByteP, ctypes.c_size_t,
                _DoubleP, ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_uint64),
                ctypes.POINTER(ctypes.c_uint64),
            ]
            binding.restype = ctypes.c_int32
        except (OSError, AttributeError) as error:
            raise RuntimeError(
                'Required Rust unit-table ABI unavailable; not replayed'
            ) from error
        _binding = _Binding(signature, library, binding)
        return _binding


def unit_table():
    """Read the 24-entry unit scale table from the integrated Rust library.

    Two-pass protocol (bounded BEFORE allocations):

    1. Query the exact ``names_len`` and ``count`` with zero capacity.
       Rust fills the two output pointers and returns ``-3`` (insufficient
       capacity — expected for pass 1).
    2. Validate ``count`` and ``names_len`` against the host caps
       (``_MAX_COUNT``, ``_MAX_NAMES_LEN``) BEFORE allocating any buffer.
    3. Allocate exactly the reported sizes and read the table.
    4. Validate the schema (pass-1 sizes == pass-2 sizes), the names
       (ASCII, no duplicates, no empty entries), and the values
       (finite, positive) BEFORE returning.

    Returns a dict with keys:

    * ``status``  — ``'ok'`` (always, or the function raises).
    * ``count``   — number of entries (24).
    * ``length``  — ``names_len`` in bytes.
    * ``schema``  — ``(count, names_len, value_bytes)``.
    * ``table``   — ``dict[str, float]`` mapping unit name to mm-per-unit.
    * ``names``   — ``tuple[str, ...]`` in Rust declaration order.

    Raises ``RuntimeError`` when the library is missing, the core ABI version
    does not match, any reply field is out of range, the schema is inconsistent,
    a name is duplicated or empty, or a value is non-finite or non-positive.
    There is NO fallback: the caller must handle the exception.
    """
    record = _required_binding()
    binding = record.function

    # Pass 1: query sizes with zero capacity.
    names_len = ctypes.c_uint64(0)
    count = ctypes.c_uint64(0)
    try:
        rc = binding(None, 0, None, 0,
                     ctypes.byref(names_len), ctypes.byref(count))
    except (OSError, ValueError, ctypes.ArgumentError) as error:
        raise RuntimeError(
            'unit_table native call failed; not replayed'
        ) from error
    if rc != _RC_SHORT:
        raise RuntimeError(
            'unit_table pass-1 rc={} (expected {}); not replayed'
            .format(rc, _RC_SHORT))

    n_len = names_len.value
    n_count = count.value

    # Bound checks BEFORE any allocation.
    if not (0 < n_count <= _MAX_COUNT):
        raise RuntimeError(
            'unit_table count={} out of range (1..{}); not replayed'
            .format(n_count, _MAX_COUNT))
    if not (0 < n_len <= _MAX_NAMES_LEN):
        raise RuntimeError(
            'unit_table names_len={} out of range (1..{}); not replayed'
            .format(n_len, _MAX_NAMES_LEN))

    # Pass 2: allocate exactly the reported sizes and read the table.
    names_buf = (ctypes.c_ubyte * n_len)()
    values_buf = (ctypes.c_double * n_count)()
    names_len2 = ctypes.c_uint64(0)
    count2 = ctypes.c_uint64(0)
    try:
        rc = binding(names_buf, n_len, values_buf, n_count,
                     ctypes.byref(names_len2), ctypes.byref(count2))
    except (OSError, ValueError, ctypes.ArgumentError) as error:
        raise RuntimeError(
            'unit_table native call failed; not replayed'
        ) from error
    if rc != _RC_OK:
        raise RuntimeError(
            'unit_table pass-2 rc={} (expected {}); not replayed'
            .format(rc, _RC_OK))

    # Schema consistency: pass-2 sizes must match pass-1.
    if names_len2.value != n_len or count2.value != n_count:
        raise RuntimeError(
            'unit_table schema mismatch (pass1=({},{}), pass2=({},{})); not replayed'
            .format(n_len, n_count, names_len2.value, count2.value))

    # Decode names (ASCII, newline-separated, no trailing newline).
    try:
        names = bytes(names_buf).decode('ascii').split('\n')
    except (UnicodeDecodeError, ValueError) as error:
        raise RuntimeError(
            'unit_table names are not valid ASCII: {}; not replayed'
            .format(error)
        ) from error

    if len(names) != n_count:
        raise RuntimeError(
            'unit_table name count {} != value count {}; not replayed'
            .format(len(names), n_count))

    # Validate: no duplicates, all names non-empty.
    if any(not n for n in names):
        raise RuntimeError('unit_table has an empty name; not replayed')
    if len(set(names)) != len(names):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise RuntimeError(
            'unit_table has duplicate names {}; not replayed'.format(dupes))

    # Validate: all values finite and positive.
    values = list(values_buf)
    bad = [names[i] for i, v in enumerate(values)
           if not math.isfinite(v) or v <= 0.0]
    if bad:
        raise RuntimeError(
            'unit_table has non-finite or non-positive values for {}; not replayed'
            .format(bad))

    table = dict(zip(names, values))
    schema = (n_count, n_len, n_count * 8)
    return {
        'status': 'ok',
        'count': n_count,
        'length': n_len,
        'schema': schema,
        'table': table,
        'names': tuple(names),
    }
