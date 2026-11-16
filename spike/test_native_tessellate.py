"""Required planar-loop host/ctypes contracts; no build, replay engine or GUI.

Numerical/order coverage is in Rust earclip24_golden over independent frozen
original data. Python checks loading, ownership of buffers and malformed replies.
"""
import ast
import ctypes as C
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = os.environ.get('NARWHAL_PURE_CORE') or str(
    ROOT/'native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so')
SQUARE = [(0.,0.),(1.,0.),(1.,1.),(0.,1.)]
FAN = [(0,1,2),(0,2,3)]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name,ROOT/relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


native = load('earclip24_host_native','narwhal3d/native_tessellate.py')
tessellate = load('earclip24_host_public','narwhal3d/tessellate.py')


def fake_library(version=1, missing=()):
    calls = {'narwhal_pure_core_abi_version':Mock(return_value=version),
             'narwhal_pure_core_ear_clip_display':Mock(),
             'narwhal_pure_core_earclip_signed_area_v1':Mock()}
    return types.SimpleNamespace(**{k:v for k,v in calls.items() if k not in missing})


def fake_clip(mode='good'):
    def call(points,n,out,capacity,count):
        assert n==4 and capacity==6
        assert list(points)==[0.,0.,1.,0.,1.,1.,0.,1.]
        if mode=='no-write': return 0
        if mode=='raises': raise OSError('injected native call failure')
        if isinstance(mode,int): return mode
        used = C.cast(count,C.POINTER(C.c_uint64))
        used[0] = 3 if mode=='count' else 0 if mode=='empty' else 1 if mode=='partial' else 2
        if mode=='empty': return 0
        out[:6] = [0,1,2,0,2,3]
        if mode=='index': out[2] = n
        if mode=='negative-as-u64': out[2] = (1<<64)-1
        if mode=='duplicate': out[2] = 1
        return 0
    return call


def fake_area(mode='good'):
    def call(points,n,out,count):
        if mode=='no-write': return 0
        if mode=='raises': raise OSError('injected area failure')
        if isinstance(mode,int): return mode
        C.cast(out,C.POINTER(C.c_double))[0] = 1.
        if mode!='value-without-count':
            C.cast(count,C.POINTER(C.c_uint64))[0] = 2 if mode=='count' else 1
        return 0
    return call


class RequiredEarclip(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ,{'NARWHAL_PURE_CORE':LIBRARY})
        self.environment.start()
        native._reset_for_tests()

    def tearDown(self):
        native._reset_for_tests()
        self.environment.stop()

    def test_original_capture_provenance_is_independent_and_hash_pinned(self):
        fixtures = ROOT/'native/pure_core/tests/fixtures'
        provenance = json.loads((fixtures/'earclip24_provenance.json').read_text())
        self.assertEqual(provenance['original_loops'],75)
        self.assertEqual(provenance['adversarial_loops'],33)
        self.assertEqual(provenance['total_loops'],108)
        self.assertEqual(provenance['original_sha256'],
                         'd5b19449f846d5d4c9d11f4da9f1ee70497887d269162a70ddeb8681c3546501')
        for name,field,digest in (
            ('earclip24_original.txt','data_sha256','602978c2b2c08dc56b27efe09ffb25ec7824e8cd712717b4bcec482669abab65'),
            ('earclip24_safety.json','safety_sha256','f2cfc291f0d1b6109061281696c6cfafcef561079ae687e9f6b5be70501b09d3')):
            self.assertEqual(provenance[field],digest)
            self.assertEqual(hashlib.sha256((fixtures/name).read_bytes()).hexdigest(),digest)

    def test_real_required_abi_all_small_sizes_and_counts(self):
        self.assertTrue(native.require_support())
        for points,answer in (([],[]),([(1.,2.)],[]),([(1.,2.),(3.,4.)],[]),
                              (SQUARE[:3],[(0,1,2)]),(SQUARE,FAN)):
            before = native._native_calls
            self.assertEqual(native.ear_clip(points),answer)
            self.assertEqual(native._native_calls,before+1)
        self.assertEqual(native.signed_area([]),0.)
        self.assertEqual(native.signed_area(SQUARE),1.)
        self.assertEqual(native._native_area_calls,2)

    def test_fixed_host_tuple_and_ieee_bit_conversion_including_partial(self):
        names = {'triangle','square CW','figure eight (stalls)','duplicate point (stalls)',
                 'one signed zero','tiny square','overflow square'}
        seen = set()
        for row in (ROOT/'native/pure_core/tests/fixtures/earclip24_original.txt').read_text().splitlines():
            name,bits,area,indices = row.split('|')
            if name not in names: continue
            seen.add(name)
            values = [struct.unpack('>d',bytes.fromhex(v))[0] for v in bits.split(',')] if bits else []
            points = list(zip(values[::2],values[1::2]))
            idx = [int(v) for v in indices.split(',')] if indices else []
            expected = list(zip(idx[::3],idx[1::3],idx[2::3]))
            result = native.ear_clip(points)
            self.assertEqual(result,expected,name)
            self.assertIsInstance(result,list)
            self.assertTrue(all(type(tri) is tuple and all(type(i) is int for i in tri) for tri in result))
            self.assertEqual(struct.pack('>d',native.signed_area(points)).hex(),area,name)
        self.assertEqual(seen,names)

    def test_missing_support_never_reuses_last_healthy_binding_even_for_empty(self):
        self.assertEqual(native.ear_clip(SQUARE),FAN)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{'NARWHAL_PURE_CORE':str(Path(directory)/'absent.so')}):
            for call in (native.require_support,lambda:native.ear_clip([]),lambda:native.signed_area([])):
                with self.assertRaisesRegex(native.NativeTessellateError,'missing or stale'):
                    call()
        self.assertEqual(native.ear_clip(SQUARE),FAN)

    def test_selected_path_normalization_and_raw_path_mutation(self):
        from pathlib import PureWindowsPath
        class WindowsPath(PureWindowsPath):
            def expanduser(self): return self
        path = '/selected/stale-core.so'
        normalized = str(WindowsPath(path).expanduser())
        self.assertNotEqual(path, normalized)
        with patch.dict(os.environ, {'NARWHAL_PURE_CORE':path}), patch.object(native, 'Path', WindowsPath):
            native._reset_for_tests()
            with patch.object(native.ctypes, 'CDLL', return_value=fake_library()) as loader:
                self.assertTrue(native.require_support())
                loader.assert_called_once_with(normalized)
                with self.assertRaises(AssertionError): loader.assert_called_once_with(path)
            source = inspect.getsource(native._ear_clip_binding)
            self.assertIn('ctypes.CDLL(str(path))', source)
            namespace = dict(vars(native))
            exec(compile(source.replace('ctypes.CDLL(str(path))', 'ctypes.CDLL(key)'),
                         '<earclip-raw-path-mutant>', 'exec'), namespace)
            native._reset_for_tests()
            with patch.object(native.ctypes, 'CDLL', return_value=fake_library()) as loader:
                namespace['_cache'] = None
                namespace['_ear_clip_binding']()
                loader.assert_called_once_with(path)
                with self.assertRaises(AssertionError): loader.assert_called_once_with(normalized)
            native._reset_for_tests()
            with patch.object(native.ctypes, 'CDLL', return_value=fake_library()) as loader:
                self.assertTrue(native.require_support())
                loader.assert_called_once_with(normalized)

    def test_each_missing_export_wrong_identity_and_no_secondary_search(self):
        path = '/selected/stale-core.so'
        for version,missing in ((2,()),(1,('narwhal_pure_core_abi_version',)),
                 (1,('narwhal_pure_core_ear_clip_display',)),(1,('narwhal_pure_core_earclip_signed_area_v1',))):
            native._reset_for_tests()
            lib = fake_library(version,missing)
            with self.subTest(version=version,missing=missing), patch.dict(os.environ,{'NARWHAL_PURE_CORE':path}), \
                    patch.object(native.ctypes,'CDLL',return_value=lib) as loader:
                with self.assertRaises(native.NativeTessellateError): native.require_support()
                loader.assert_called_once_with(str(Path(path).expanduser()))

    def test_discovery_cache_signature_reset_and_same_path_repair(self):
        path = '/selected/repaired-core.so'
        healthy = fake_library()
        with patch.dict(os.environ,{'NARWHAL_PURE_CORE':path}), patch.object(native.ctypes,'CDLL',side_effect=[fake_library(2),healthy]) as loader:
            with self.assertRaises(native.NativeTessellateError): native.require_support()
            self.assertTrue(native.require_support())
            self.assertTrue(native.require_support())
            self.assertEqual(loader.call_count,2)
            binding = native._ear_clip_binding()
            self.assertIs(binding[0],healthy)
            self.assertEqual(binding[1].argtypes[1],C.c_size_t)
            self.assertEqual(binding[1].restype,C.c_int32)
            self.assertEqual(len(binding[2].argtypes),4)
        native._reset_for_tests()
        with patch.object(native.ctypes,'CDLL',return_value=healthy) as loader:
            self.assertTrue(native.require_support())
            self.assertTrue(native.require_support())
            loader.assert_called_once()
            with patch.dict(os.environ,{'NARWHAL_PURE_CORE':'/second/core.so'}):
                self.assertTrue(native.require_support())
            self.assertEqual(loader.call_count,2)
        native._reset_for_tests()
        self.assertIsNone(native._cache)
        self.assertEqual((native._native_calls,native._native_area_calls),(0,0))

    def test_parallel_admission_has_one_successful_load(self):
        with patch.object(native.ctypes,'CDLL',return_value=fake_library()) as loader:
            with ThreadPoolExecutor(max_workers=4) as pool:
                self.assertEqual(list(pool.map(lambda _:native.require_support(),range(16))),[True]*16)
            loader.assert_called_once()

    def test_input_shapes_changed_counts_and_resources_refuse_before_call(self):
        class TooMany:
            def __len__(self): return (1<<20)+1
            def __iter__(self): raise AssertionError('oversized input was iterated')
        class Changed:
            def __len__(self): return 1
            def __iter__(self): return iter(SQUARE)
        clip = Mock(side_effect=AssertionError('invalid input reached native'))
        with patch.object(native,'_ear_clip_binding',return_value=(None,clip,clip)):
            for value in (TooMany(),Changed(),[(0,0,1)]*4,[(0,)]*4,[('bad',0)]*4):
                with self.subTest(value=type(value).__name__), self.assertRaises(native.TessellationInputError):
                    native.ear_clip(value)
            clip.assert_not_called()
        self.assertEqual(native._native_calls,0)

    def test_malformed_success_counts_indices_and_refusals_never_count_or_replay(self):
        for mode in ('no-write','count','index','negative-as-u64','duplicate','raises',-1,-3,99):
            before = native._native_calls
            with self.subTest(mode=mode), patch.object(native,'_ear_clip_binding',return_value=(None,fake_clip(mode),fake_area())):
                with self.assertRaises(native.NativeTessellateError): native.ear_clip(SQUARE)
            self.assertEqual(native._native_calls,before)
        with patch.object(native,'_ear_clip_binding',return_value=(None,fake_clip(-2),fake_area())):
            with self.assertRaises(native.TessellationInputError): native.ear_clip(SQUARE)
        for mode,answer in (('partial',[(0,1,2)]),('empty',[]),('good',FAN)):
            with patch.object(native,'_ear_clip_binding',return_value=(None,fake_clip(mode),fake_area())):
                self.assertEqual(native.ear_clip(SQUARE),answer)

    def test_area_count_and_call_failures_are_explicit(self):
        for mode in ('no-write','value-without-count','count','raises',-1,-3,99):
            before = native._native_area_calls
            with self.subTest(mode=mode), patch.object(native,'_ear_clip_binding',return_value=(None,fake_clip(),fake_area(mode))):
                with self.assertRaises(native.NativeTessellateError): native.signed_area(SQUARE)
            self.assertEqual(native._native_area_calls,before)

    def test_nonfinite_clip_refusal_is_named_and_area_ieee_behavior_is_preserved(self):
        rows = json.loads((ROOT/'native/pure_core/tests/fixtures/earclip24_safety.json').read_text())
        for row in rows:
            values = [struct.unpack('>d',bytes.fromhex(v))[0] for v in row['points_bits']]
            points = list(zip(values[::2],values[1::2]))
            with self.assertRaisesRegex(native.TessellationInputError,'nonfinite'):
                native.ear_clip(points)
            self.assertEqual(struct.pack('>d',native.signed_area(points)).hex(),row['area_bits'])
        self.assertEqual(native._native_calls,0)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'copied ELF fixtures; Windows replay is separately coordinated')
    def test_actual_selected_stale_and_no_write_libraries_are_explicitly_refused(self):
        helper = load('earclip24_elf_fixture','spike/test_desktop_native_startup.py')
        original = Path(LIBRARY).read_bytes()
        entries = helper.elf_export_entries(original)
        with tempfile.TemporaryDirectory(prefix='earclip24-native-faults-') as directory:
            root = Path(directory)
            (root/'fresh-secondary.so').write_bytes(original)
            for kind in ('missing-area','no-write-clip','no-write-area'):
                data = bytearray(original)
                symbol = ('narwhal_pure_core_ear_clip_display' if kind=='no-write-clip'
                          else 'narwhal_pure_core_earclip_signed_area_v1')
                entry,value,size,section = entries[symbol]
                if kind=='missing-area':
                    data[entry+4] &= 0x0f
                else:
                    self.assertEqual(struct.unpack_from('<H',data,18)[0],62)
                    self.assertGreaterEqual(size,3)
                    offset = section[4]+value-section[3]
                    data[offset:offset+3] = b'\x31\xc0\xc3'
                path = root/(kind+'.so')
                path.write_bytes(data)
                native._reset_for_tests()
                with self.subTest(kind=kind), patch.dict(os.environ,{'NARWHAL_PURE_CORE':str(path)}):
                    if kind=='missing-area':
                        # Prove the fixture remains a working old clip ABI, not
                        # an unreadable DLL that makes the stale test vacuous.
                        lib = C.CDLL(str(path))
                        version = lib.narwhal_pure_core_abi_version
                        version.argtypes,version.restype = [],C.c_uint32
                        self.assertEqual(version(),1)
                        clip = lib.narwhal_pure_core_ear_clip_display
                        clip.argtypes = [C.POINTER(C.c_double),C.c_size_t,C.POINTER(C.c_uint64),C.c_size_t,C.POINTER(C.c_uint64)]
                        clip.restype = C.c_int32
                        out,count = (C.c_uint64*6)(),C.c_uint64(99)
                        points = (C.c_double*8)(0,0,1,0,1,1,0,1)
                        self.assertEqual(clip(points,4,out,6,C.byref(count)),0)
                        self.assertEqual((count.value,list(out)),(2,[0,1,2,0,2,3]))
                    operation = native.signed_area if kind=='no-write-area' else native.ear_clip
                    with self.assertRaises(native.NativeTessellateError): operation(SQUARE)

    def test_public_only_delegates_and_old_engine_is_absent_even_in_dead_ast(self):
        source = (ROOT/'narwhal3d/tessellate.py').read_text()
        tree = ast.parse(source)
        names = {n.id for n in ast.walk(tree) if isinstance(n,ast.Name)}
        names.update(n.name for n in ast.walk(tree) if isinstance(n,ast.FunctionDef))
        for obsolete in ('_point_in_triangle','_convex_fan','_native_ear_clip',
                         'NATIVE_EAR_CLIP_MIN_POINTS','NATIVE_CONCAVE_MIN_POINTS'):
            self.assertNotIn(obsolete,names)
        for name in ('ear_clip','signed_area'):
            node = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
            self.assertFalse(any(isinstance(n,(ast.For,ast.While,ast.BinOp)) for n in ast.walk(node)))
        with patch.object(tessellate,'_triangulation_native',return_value=native):
            self.assertEqual(tessellate.ear_clip(SQUARE),FAN)
            self.assertEqual(tessellate.signed_area(SQUARE),1.)
            with patch.dict(os.environ,{'NARWHAL_PURE_CORE':'/no/such/required-core.so'}):
                with self.assertRaises(native.NativeTessellateError): tessellate.ear_clip([])
                with self.assertRaises(native.NativeTessellateError): tessellate.signed_area([])


class GuardMutations(unittest.TestCase):
    def test_captured_data_guard_is_red_then_green(self):
        path = ROOT/'native/pure_core/tests/fixtures/earclip24_original.txt'
        original_read = Path.read_bytes
        data = path.read_bytes()
        def damaged(file,*args,**kwargs):
            return bytes([data[0]^1])+data[1:] if file==path else original_read(file,*args,**kwargs)
        method = 'test_original_capture_provenance_is_independent_and_hash_pinned'
        with patch.object(Path,'read_bytes',damaged):
            broken = unittest.TestResult()
            RequiredEarclip(method).run(broken)
            self.assertFalse(broken.wasSuccessful())
        restored = unittest.TestResult()
        RequiredEarclip(method).run(restored)
        self.assertTrue(restored.wasSuccessful(),restored.errors or restored.failures)
        print('MUTATION RED -> restored GREEN: frozen original data drift')

    def test_hidden_python_engine_guard_is_red_then_green(self):
        original_read = Path.read_text
        path = ROOT/'narwhal3d/tessellate.py'
        original = path.read_text()
        def hidden(file,*args,**kwargs):
            if file==path:
                return original+'\nif False:\n    def _point_in_triangle(*args): return False\n'
            return original_read(file,*args,**kwargs)
        method = 'test_public_only_delegates_and_old_engine_is_absent_even_in_dead_ast'
        with patch.object(Path,'read_text',hidden):
            broken = unittest.TestResult()
            RequiredEarclip(method).run(broken)
            self.assertFalse(broken.wasSuccessful())
        restored = unittest.TestResult()
        RequiredEarclip(method).run(restored)
        self.assertTrue(restored.wasSuccessful(),restored.errors or restored.failures)
        print('MUTATION RED -> restored GREEN: hidden Python engine')

    def test_boundary_guards_go_red_then_green(self):
        cases = [('_ear_clip_binding','if version() != 1:','if False:',
                  'test_each_missing_export_wrong_identity_and_no_secondary_search'),
                 ('_ear_clip_binding','if _cache is not None and _cache[0] == key:','if False:',
                  'test_discovery_cache_signature_reset_and_same_path_repair'),
                 ('_pack','if n > _MAX_POINTS:','if False:',
                  'test_input_shapes_changed_counts_and_resources_refuse_before_call'),
                 ('ear_clip','if count.value > max(0, n - 2):','if False:',
                  'test_malformed_success_counts_indices_and_refusals_never_count_or_replay'),
                 ('ear_clip','if any(i >= n or j >= n or k >= n or i == j or j == k or i == k for i, j, k in triples):','if False:',
                  'test_malformed_success_counts_indices_and_refusals_never_count_or_replay'),
                 ('signed_area','if count.value != 1:','if False:',
                  'test_area_count_and_call_failures_are_explicit')]
        for function,old,new,method in cases:
            original = inspect.getsource(getattr(native,function))
            self.assertIn(old,original)
            namespace = dict(vars(native))
            exec(compile(original.replace(old,new),'<earclip24-mutant>','exec'),namespace)
            mutant = types.FunctionType(namespace[function].__code__,vars(native))
            with self.subTest(function=function,guard=old), patch.object(native,function,mutant):
                result = unittest.TestResult()
                RequiredEarclip(method).run(result)
                self.assertFalse(result.wasSuccessful(),'mutation survived: '+old)
            restored = unittest.TestResult()
            RequiredEarclip(method).run(restored)
            self.assertTrue(restored.wasSuccessful(),restored.errors or restored.failures)
            print('MUTATION RED -> restored GREEN:',function,old)


if __name__=='__main__':
    unittest.main(verbosity=2)
