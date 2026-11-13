"""Owned SVG qualification; defaults to live public path and configured core.

python3 spike/test_svgpath_owned.py --candidate /path/to/inactive-wrapper.py --library /path/core.so
No private archive or scratch dependency. Oracle is frozen and native-disabled.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes as C
import importlib.util
import math
import os
from pathlib import Path
import random
import struct
import time
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
args = argparse.ArgumentParser()
args.add_argument('--candidate', type=Path, default=ROOT / 'narwhal3d/svgpath.py')
args.add_argument('--library', type=Path)
args.add_argument('--benchmark', action='store_true')
OPTIONS, REST = args.parse_known_args()
if OPTIONS.library:
    os.environ['NARWHAL_PURE_CORE'] = str(OPTIONS.library.resolve())


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REF = load(ROOT / 'spike/svgpath_reference.py', 'svgpath_frozen')
SUBJECT = load(OPTIONS.candidate, 'svgpath_subject')
# Patch the very adapter reached by the public wrapper, not a second instance.
HOST = (SUBJECT._adapter() if hasattr(SUBJECT, '_adapter') else
        SUBJECT if hasattr(SUBJECT, '_required_binding') else
        load(ROOT / 'narwhal3d/native_svgpath.py', 'svgpath_host'))


def freeze(x):
    if isinstance(x, float):
        return struct.pack('>d', x)
    if isinstance(x, dict):
        return {k: freeze(v) for k, v in x.items()}
    if isinstance(x, (tuple, list)):
        return tuple(map(freeze, x))
    return x


def outcome(fn, text):
    try:
        return ('ok', freeze(fn(text)))
    except Exception as exc:
        return type(exc).__name__, str(exc)


class OwnedSVG(unittest.TestCase):
    def parity(self, text):
        self.assertEqual(outcome(REF.parse_path, text), outcome(SUBJECT.parse_path, text), repr(text))

    def test_public_wrapper_delegates_to_one_adapter(self):
        self.assertTrue(callable(getattr(SUBJECT, '_adapter', None)),
                        'public SVG must be a thin required-adapter wrapper')
        self.assertIs(SUBJECT._adapter(), HOST)
        # A fresh file-path import must share the canonical adapter as well.
        again = load(OPTIONS.candidate, 'svgpath_subject_again')
        self.assertIs(again._adapter(), HOST)
        for name, values in [('parse_path', ('M0 0',)),
                             ('_subpaths_from_packed', ([], [], [], 0, 0)),
                             ('_subpaths_from_json', ('[]',))]:
            sentinel = object()
            with self.subTest(helper=name), patch.object(HOST, name, return_value=sentinel) as forwarded:
                self.assertIs(getattr(SUBJECT, name)(*values), sentinel)
                forwarded.assert_called_once_with(*values)
        for text in ['', 'M0 0', 'L']:
            with patch.object(HOST, 'parse_path', side_effect=RuntimeError('wrapper bypass sentinel')) as forwarded:
                with self.assertRaisesRegex(RuntimeError, 'wrapper bypass sentinel'):
                    SUBJECT.parse_path(text)
                forwarded.assert_called_once_with(text)

    def test_standard(self):
        cases = ['', 'M', 'Zz', 'M0 0', 'M0 0Zl3 4z', 'm1 2 3 4h2v-3z',
                 'M0 0C1 2 3 4 5 6s7 8 9 10S1 2 3 4',
                 'M0 0Q2 3 4 5t6 7T8 9', 'M1 2Q2 3 4 5L6 7T8 9',
                 'M0 0C1 2 3 4 5 6L3 4S1 2 3 4',
                 'M0 0A0 2 0 0 1 3 4', 'M1 2A2 3 45 0 1 1 2',
                 'M.5.5L1e-3,2E+3;M-0 -0L0 -0', 'ignored',
                 'M0 0Z1', 'M0 0M', 'M0 0LZ', 'L', '1', '+', '-',
                 'M0 0L1', 'M0 0C1 2 3 4 5', 'M0 0A1 2 3 4 5 6',
                 'M١ ٢L٣ ４', 'M𝟙 𝟚L३ ৪', '²', 'M0 0L1² 3',
                 'M0 0L1e² 3', 'M0 0L.² 3', 'L0EjQkSj𝟚e321T+un١𝟚jVmA.2Tu𝟚L', '\ud800M0 0', None, b'', b'M0 0', [], [1]]
        for text in cases:
            with self.subTest(text=repr(text)):
                self.parity(text)

    def test_generated_arcs_exact_bits(self):
        rng = random.Random(14)
        for i in range(2000):
            v = [rng.uniform(-100, 100) for _ in range(7)]
            text = 'M%s %s A%s %s %s %s %s %s %s' % (*v[:5], i%2, (i//2)%2, *v[5:])
            self.parity(text)

    def test_generated_cubics_exact_bits(self):
        rng = random.Random(1401)
        for i in range(1000):
            def nums(n):
                return ' '.join(repr(rng.uniform(-1e6, 1e6)) for _ in range(n))
            self.parity('M'+nums(2)+'c'+nums(6)+'s'+nums(4)+'q'+nums(4)+'t'+nums(2)+'Zl'+nums(2))

    def test_generated_errors(self):
        for cmd, arity in dict(L=2, H=1, V=1, C=6, S=4, Q=4, T=2, A=7).items():
            self.parity(cmd)
            for n in range(1, arity):
                self.parity('M0 0'+cmd+' '.join(['1'] * n))
        for digit in ['²', '³', '⁹', '①', '⑨', '፩', '𐩀']:
            for token in [digit, '+'+digit, '1'+digit, '.'+digit, '1e'+digit]:
                self.parity('M0 0L'+token+' 2')

    def test_finite_extremes(self):
        for text in ['M1e-300 2e-300L3e-300 4e-300', 'M1e150 -1e150Q0 0 -1e150 1e150',
                     'M0 0A1e50 2e50 30 0 1 2e50 1e50', 'M-0 -0L-0 0Z']:
            self.parity(text)

    def test_ours_safety_no_fallback(self):
        for text in ['M1e999 0', 'M0 0L1e308 0L-1e308 0', 'M0 0A1e308 1e308 1e308 1 1 2 3', 'M' * ((1<<20)+1)]:
            with self.subTest(text=text[:80]):
                with self.assertRaisesRegex(ValueError, 'safety bounds'):
                    SUBJECT.parse_path(text)

    def test_missing_stale_core(self):
        with patch.dict(os.environ, NARWHAL_PURE_CORE='/not/a/library.so'):
            with self.assertRaises(RuntimeError):
                HOST.parse_path('M0 0')
        class Old:
            pass
        with patch.object(HOST, '_binding', None), patch.object(HOST.C, 'CDLL', return_value=Old()):
            with self.assertRaises(RuntimeError):
                HOST.parse_path('M0 0')

    def test_abi_null_alignment_overlap_capacity(self):
        lib, fn = HOST._load()
        text = 'M0 0L2 3Z'
        inp = (C.c_uint32 * len(text))(*map(ord, text))
        report, err = (C.c_size_t * 2)(), HOST._Error()
        def call(p=None, pc=0, o=None, oc=0, f=None, fc=0, t=inp, n=len(text)):
            return fn(t,n,p,pc,o,oc,f,fc,report,C.byref(err))
        self.assertEqual(call(),1)
        n,m = report
        self.assertEqual((n,m),(16,1))
        pts,offs,flags = (C.c_double*n)(*([123]*n)),(C.c_size_t*m)(123),(C.c_uint8*m)(123)
        self.assertEqual(call(pts,n-1,offs,m,flags,m),1)
        self.assertEqual(list(pts),[123]*n)
        self.assertEqual(list(offs),[123])
        self.assertEqual(list(flags),[123])
        self.assertEqual(call(pts,n,offs,m,flags,m),0)
        self.assertEqual(list(offs),[0]); self.assertEqual(list(flags),[1])
        self.assertEqual(call(t=None,n=1),-1)
        bad=C.cast(C.addressof(inp)+1,C.POINTER(C.c_uint32))
        self.assertEqual(call(t=bad,n=1),-1)
        self.assertEqual(call(pc=64*(1<<20)+1),-2)
        self.assertEqual(call(pc=(64<<20)//8, oc=1, fc=1), -2)
        self.assertEqual(call(p=C.cast(inp,C.POINTER(C.c_double)),pc=1),-2)
        self.assertEqual(fn(inp,len(text),None,0,None,0,None,0,None,C.byref(err)),-1)

    def test_host_fault_reports(self):
        for mode in ['status','size','error','partition','finite','flag','changed']:
            def fake(text,length,pts,n,offs,m,flags,f,counts,errptr):
                err=C.cast(errptr,C.POINTER(HOST._Error)).contents
                if mode=='status': return -99
                if mode=='size': counts[0]=3; return 1
                if mode=='error': err.kind=99; return 2
                counts[0]=8; counts[1]=1
                if pts is None: return 1
                if mode=='partition': offs[0]=1
                if mode=='finite': pts[0]=math.inf
                if mode=='flag': flags[0]=2
                if mode=='changed': counts[0]=0
                return 0
            with self.subTest(mode=mode), patch.object(HOST,'_load',return_value=(object(),fake)):
                with self.assertRaises(RuntimeError): HOST.parse_path('M0 0L1 2')

    def test_binding_warm_env_and_concurrent_cold_misses(self):
        library, fn = HOST._load()
        explicit = os.environ.get('NARWHAL_PURE_CORE') or library._name
        real_cdll = C.CDLL
        with patch.object(HOST, '_binding', None), patch.object(HOST.C, 'CDLL', wraps=real_cdll) as loader:
            barrier = threading.Barrier(12)
            def cold(_):
                barrier.wait()
                return HOST.parse_path('M0 0L1 2')
            with ThreadPoolExecutor(max_workers=12) as pool:
                results = list(pool.map(cold, range(12)))
            self.assertEqual(loader.call_count, 1)
            self.assertTrue(all(result == REF.parse_path('M0 0L1 2') for result in results))
            record = HOST._required_binding()
            with patch.object(HOST, 'Path', side_effect=AssertionError('warm Path')), patch.object(HOST, '_lock', None):
                HOST.parse_path('')
                self.assertIs(record, HOST._required_binding())
            self.assertEqual(loader.call_count, 1)
            with tempfile.TemporaryDirectory() as folder:
                other = Path(folder) / 'other-core.so'
                other.symlink_to(Path(explicit).resolve())
                with patch.dict(os.environ, NARWHAL_PURE_CORE=str(other)):
                    newer = HOST._required_binding()
                    self.assertEqual(newer.signature, ('env', str(other)))
                    self.assertIsNot(newer, record)
                    self.assertIsNotNone(record.library)  # original binding keeps its owner
                    HOST.parse_path('M0 0')
                HOST.parse_path('M0 0')
                self.assertEqual(loader.call_count, 3)

    def test_packaged_paths_and_both_identities(self):
        class Function:
            def __init__(self, value): self.value = value
            def __call__(self, *args): return self.value
        class Library:
            def __init__(self, core=1, owned=14):
                self.narwhal_pure_core_abi_version = Function(core)
                self.narwhal_pure_core_svgpath_owned_version = Function(owned)
                self.narwhal_pure_core_svgpath_owned = Function(0)
        for platform, suffix in [('linux', 'libnarwhal_pure_core.so'), ('darwin', 'libnarwhal_pure_core.dylib'), ('win32', 'narwhal_pure_core.dll')]:
            with patch.dict(os.environ):
                os.environ.pop('NARWHAL_PURE_CORE', None)
                with patch.object(HOST, '_binding', None), patch.object(HOST.sys, 'platform', platform), patch.object(HOST.C, 'CDLL', return_value=Library()) as loader:
                    record = HOST._required_binding()
                    self.assertEqual(record.signature, ('packaged', None))
                    self.assertEqual(Path(loader.call_args.args[0]), Path(HOST.__file__).resolve().parent / 'native' / suffix)
        for core, owned in [(2, 14), (1, 13)]:
            with patch.object(HOST, '_binding', None), patch.object(HOST.C, 'CDLL', return_value=Library(core, owned)):
                with self.assertRaises(RuntimeError): HOST._required_binding()

    def test_unicode_qualification_and_buffer(self):
        with patch.object(HOST.unicodedata, 'unidata_version', '16.0.0'):
            with self.assertRaisesRegex(RuntimeError, 'Unicode'): HOST.parse_path('M0 0')
        self.assertEqual(list(HOST._buffer('𝟚\ud800١')), [0x1d7da, 0xd800, 0x661])
        self.assertLessEqual(HOST._array_type.cache_info().maxsize, 128)
        with patch.dict(os.environ, NARWHAL_PURE_CORE='/not/a/core.so'):
            for text in ['', 'M', '1']:
                with self.assertRaises(RuntimeError): HOST.parse_path(text)

    def test_output_byte_budget_before_allocator(self):
        def query(text, length, pts, n, offs, m, flags, fc, report, err):
            report[0] = (64 << 20)//8 + 8
            report[1] = 1
            self.assertLess(report[0], 64 * length)
            return 1
        with patch.object(HOST, '_load', return_value=(object(), query)), patch.object(HOST, '_allocate_output', side_effect=AssertionError('allocator reached')) as allocation:
            with self.assertRaisesRegex(RuntimeError, 'output sizes'): HOST.parse_path('x' * 200000)
            allocation.assert_not_called()

    def test_error_fields_counts_and_semantic_counter(self):
        for text in ['', 'M0 0L1 2', '1', 'L', 'M0 0L1', '²', 'M1e999 0']:
            before = HOST._native_svgpath_calls
            outcome(HOST.parse_path, text)
            self.assertEqual(HOST._native_svgpath_calls, before + 1, text)
        variants = [HOST._Error(kind=1, value=1), HOST._Error(kind=2, command=ord('L')),
                    HOST._Error(kind=3, command=ord('L'), expected=2, actual=1),
                    HOST._Error(kind=4, start=0, end=1), HOST._Error(kind=5)]
        used = [{ 'kind', 'value'}, {'kind', 'command'}, {'kind', 'command', 'expected', 'actual'},
                {'kind', 'start', 'end'}, {'kind'}]
        for base, fields in zip(variants, used):
            self.assertIsInstance(HOST._error_exception(base, '²'), ValueError)
            for name, _ in HOST._Error._fields_:
                if name in fields: continue
                invalid = HOST._Error.from_buffer_copy(base)
                setattr(invalid, name, 1)
                with self.subTest(kind=base.kind, field=name):
                    with self.assertRaises(RuntimeError): HOST._error_exception(invalid, '²')
        for phase in ['query', 'write']:
            for field, _ in HOST._Error._fields_:
                def fake(text,length,pts,n,offs,m,flags,fc,counts,errptr):
                    counts[0], counts[1] = 8, 1
                    if phase == 'query' or pts is not None:
                        setattr(C.cast(errptr, C.POINTER(HOST._Error)).contents, field, 1)
                    return 1 if pts is None else 0
                before = HOST._native_svgpath_calls
                with patch.object(HOST, '_load', return_value=(object(), fake)):
                    with self.assertRaises(RuntimeError): HOST.parse_path('M0 0L1 2')
                self.assertEqual(HOST._native_svgpath_calls, before)
        # Unused -0.0 has a nonzero sign bit: zero-by-value is insufficient.
        for kind in [2, 3, 4, 5]:
            invalid = HOST._Error.from_buffer_copy(variants[kind-1])
            invalid.value = -0.0
            with self.assertRaises(RuntimeError): HOST._error_exception(invalid, '²')
        for value in [math.inf, math.nan]:
            with self.assertRaises(RuntimeError): HOST._error_exception(HOST._Error(kind=1, value=value), '1')
        def bad_counts(text,length,pts,n,offs,m,flags,fc,counts,errptr):
            counts[1] = 1
            C.cast(errptr, C.POINTER(HOST._Error)).contents.kind = 5
            return 2
        before = HOST._native_svgpath_calls
        with patch.object(HOST, '_load', return_value=(object(), bad_counts)):
            with self.assertRaises(RuntimeError): HOST.parse_path('M0 0')
        self.assertEqual(HOST._native_svgpath_calls, before)

    def test_call_exceptions_are_protocol_errors(self):
        for phase in ['query', 'write']:
            for exception in [OSError('call failed'), C.ArgumentError('bad pointer')]:
                def fake(text,length,pts,n,offs,m,flags,fc,counts,errptr):
                    if phase == 'query' or pts is not None: raise exception
                    counts[0], counts[1] = 8, 1
                    return 1
                before = HOST._native_svgpath_calls
                with patch.object(HOST, '_load', return_value=(object(), fake)):
                    with self.assertRaisesRegex(RuntimeError, 'call failed'): HOST.parse_path('M0 0L1 2')
                self.assertEqual(HOST._native_svgpath_calls, before)


if __name__ == '__main__':
    if OPTIONS.benchmark:
        for k in (1, 100, 2000):
            text='M0 0'+'c1 2 3 4 5 6s1 2 3 4' * k
            for module in (REF,SUBJECT):
                start=time.perf_counter()
                for _ in range(30): module.parse_path(text)
                print(module.__name__,len(text),'bytes', (time.perf_counter()-start)/30, 'seconds end-to-end')
    unittest.main(argv=[__file__]+REST)
