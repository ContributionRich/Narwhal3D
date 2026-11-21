"""Narrow runtime admission / encoder-adapter-retirement source and host contracts.

Python only checks ctypes/host boundaries. No numerical oracle, native build,
GUI launch, private archive, or developer-script runtime dependency.
"""
import ast
import hashlib
import importlib.util
import inspect
import os
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OBJRetirement(unittest.TestCase):
    def setUp(self):
        self.native = load('admission23_required_obj', ROOT/'narwhal3d/native_obj.py')
        self.shared = self.native._packer()
        self.shared._reset_for_tests()

    def tearDown(self):
        self.shared._reset_for_tests()

    def test_obsolete_encoder_names_absent_even_in_dead_code_and_packer_frozen(self):
        source = (ROOT/'narwhal3d/native_pure.py').read_text()
        tree = ast.parse(source)
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names.update(n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
        obsolete = (
            'encode_obj', '_pack_obj', '_native_obj_encode_into',
            '_obj_native_active', '_obj_encode_into_binding', '_objp_signature',
            '_objp_binding', '_OBJ_NATIVE_MIN_VERTS', '_native_obj_calls',
            '_pack_dxf', 'encode_dxf', '_dxf_encode_into_binding',
            '_native_dxf_encode_into', '_dxf_native_active', '_DXF_TYPE_CODES',
            '_dxp_signature', '_dxp_binding', '_DXF_NATIVE_MIN_ENTITIES',
            '_native_dxf_calls', '_pdf_encode_into_binding', '_svg_encode_into_binding',
            '_svg_unit_encode_into_binding', '_pack_pdf', '_pdf_capacity_bound',
            '_valid_pdf_bytes', '_native_pdf_encode_into', '_pdf_native_active',
            'encode_pdf', '_svg_vector_capacity_bound', '_native_svg_encode_into',
            '_svg_vector_native_active', 'encode_svg', '_svg_unit_capacity_bound',
            '_native_svg_unit_encode_into', '_svg_unit_native_active', 'encode_svg_unit',
            '_pdfp_signature', '_pdfp_binding', '_svgvp_signature',
            '_svgvp_binding', '_svgup_signature', '_svgup_binding',
            '_native_pdf_calls', '_native_svg_vector_calls', '_native_svg_unit_calls',
            '_PDF_NATIVE_MIN_STROKES', '_SVG_VECTOR_NATIVE_MIN_STROKES',
            '_decode_dxf_raw', 'decode_dxf', 'decode_dxf_packed', '_parse_dxf_packed',
            '_dxf_import_native_active', '_DXF_IMPORT_NATIVE_MIN_BYTES',
            '_MAX_DXF_LAYERS', '_MAX_DXF_ENTITIES', '_MAX_DXF_COORDS',
            '_native_decode_dxf_calls', 'dxf_dsize', 'dxf_dec', '_dxf_dsize', '_dxf_dec',
        )
        for name in obsolete:
            self.assertNotIn(name, names)
            self.assertFalse(hasattr(self.shared, name), name)
        for symbol in ('narwhal_pure_core_dxf_decode_size', 'narwhal_pure_core_dxf_decode'):
            self.assertNotIn(symbol, source)
        packer = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name=='_pack_obj_unchecked')
        self.assertEqual(hashlib.sha256(ast.get_source_segment(source, packer).encode()).hexdigest(),
                         'f7f1796f50ff62be3ae00a154503e73b3c9bbc476f00b44e737212e2042993f5')

    def test_required_obj_still_uses_stable_host_packer(self):
        self.assertIn('_pack_obj_unchecked', inspect.getsource(self.native.encode_obj))
        meshes = [('sample',[(0,0,0),(1,0,0),(0,1,0)],[(0,1,2)])]
        with patch.object(self.shared,'_pack_obj_unchecked',wraps=self.shared._pack_obj_unchecked) as pack:
            data, n = self.native.encode_obj(meshes)
        pack.assert_called_once()
        self.assertEqual(bytes(data[:n]),b'# Narwhal 3D\no sample\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
        self.assertEqual(n, len(b'# Narwhal 3D\no sample\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n'))

    def test_three_slot_cache_no_retired_format_dependency(self):
        operations = ('obj_decode_size','obj_decode')
        for missing in ((), operations):
            functions = {'narwhal_pure_core_abi_version':Mock(return_value=1)}
            functions.update({'narwhal_pure_core_'+name:Mock() for name in operations if name not in missing})
            self.shared._reset_for_tests()
            with self.subTest(missing=missing), patch.dict(os.environ,{'NARWHAL_PURE_CORE':__file__}), \
                    patch.object(self.shared.ctypes,'CDLL',return_value=types.SimpleNamespace(**functions)) as loader:
                binding = self.shared._binding()
                self.assertIsNotNone(binding)
                self.assertEqual(len(binding),3)
                for i,name in enumerate(operations):
                    self.assertIs(binding[i],functions.get('narwhal_pure_core_'+name))
                self.assertIs(self.shared._binding(),binding)
                self.assertEqual(loader.call_count,1)
                self.shared._reset_for_tests()
                self.assertIsNone(self.shared._native_binding)
                self.assertEqual(len(self.shared._binding()),3)
                self.assertEqual(loader.call_count,2)
                with patch.dict(os.environ,{'NARWHAL_PURE_CORE':str(ROOT/'narwhal3d/native_pure.py')}):
                    self.assertEqual(self.shared._binding()[-1],ROOT/'narwhal3d/native_pure.py')
                    self.assertEqual(loader.call_count,3)
        self.shared._reset_for_tests()
        with patch.dict(os.environ,{'NARWHAL_PURE_CORE':__file__}), \
                patch.object(self.shared.ctypes,'CDLL',side_effect=OSError('loader refused')):
            self.assertIsNone(self.shared._binding())
            self.assertIn('loader refused',self.shared.backend_status())

    def test_live_obj_decoder_keeps_results_and_counter(self):
        source = b'o sample\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n'
        before = self.shared._native_decode_obj_calls
        self.assertEqual(self.shared.decode_obj(source,'probe'),
            ([('sample',[(0.,0.,0.),(1.,0.,0.),(0.,1.,0.)],[(0,1,2)])],{}))
        self.assertEqual(self.shared._native_decode_obj_calls,before+1)
        self.assertIsNone(self.shared.decode_obj(source.replace(b'f 1 2 3',b'f 1 2 9'),'probe'))
        self.assertEqual(self.shared._native_decode_obj_calls,before+1)



class AdmissionSource(unittest.TestCase):
    def test_exact_join_is_required_before_startup_success(self):
        text = (ROOT / "launch/check_native.py").read_text()
        tree = ast.parse(text)
        check = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "check")
        calls = [n.value.func.id for n in check.body if isinstance(n, ast.Expr)
                 and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name)]
        self.assertIn("check_geometry_consumers", calls)
        self.assertLess(calls.index("check_geometry_consumers"), calls.index("print"))
        consumer = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == "check_geometry_consumers")
        source = ast.unparse(consumer)
        for call in ("join_exact_available", "join_breps", "from_recipe", "is_airtight", "face_types", "volume"):
            self.assertIn(call, source)
        self.assertNotIn("test_join", source)

    def test_runtime_consumers_are_called_before_success_without_developer_scripts(self):
        text = (ROOT/'launch/check_native.py').read_text()
        tree = ast.parse(text)
        check = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='check')
        admission = next(i for i,n in enumerate(check.body) if isinstance(n,ast.Expr)
                         and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name)
                         and n.value.func.id=='check_pure_consumers')
        success = next(i for i,n in enumerate(check.body) if isinstance(n,ast.Expr)
                       and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name)
                       and n.value.func.id=='print')
        self.assertLess(admission,success)
        self.assertNotIn('check_pure_core',text)
        for n in ast.walk(tree):
            if isinstance(n,ast.ImportFrom):
                self.assertFalse((n.module or '').startswith('scripts'))


    def test_new_admission_uses_real_direct_consumers_without_developer_scripts(self):
        source = (ROOT/'launch/check_native.py').read_text()
        tree = ast.parse(source)
        required = {
            'native_tessellate': {'ear_clip', 'signed_area'},
            'native_bspline': {'control_point_curve_polyline'},
            'native_preview': {'rectangle', 'ring'},
            'native_dxf_reader': {'decode_dxf'},
            'native_nearest': {'PackedCurve', 'search', 'pack'},
        }
        calls = {(n.func.value.id, n.func.attr) for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and isinstance(n.func.value, ast.Name)}
        for module, methods in required.items():
            for method in methods:
                self.assertIn((module, method), calls)
        self.assertNotIn(('dxf_reader', 'read_dxf'), calls)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertFalse((node.module or '').startswith(('scripts', 'spike')))

class GuardMutations(unittest.TestCase):
    def test_exact_join_startup_omission_is_red_and_restored(self):
        path = ROOT / "launch/check_native.py"
        original = path.read_text()
        read = Path.read_text
        anchor = "    check_geometry_consumers()\n"
        self.assertEqual(original.count(anchor), 1)
        def omitted(item, *args, **kwargs):
            return original.replace(anchor, "    pass\n", 1) if item == path else read(item, *args, **kwargs)
        with patch.object(Path, "read_text", omitted):
            result = unittest.TestResult()
            AdmissionSource("test_exact_join_is_required_before_startup_success").run(result)
            self.assertTrue(result.failures)
            self.assertFalse(result.errors)
        restored = unittest.TestResult()
        AdmissionSource("test_exact_join_is_required_before_startup_success").run(restored)
        self.assertTrue(restored.wasSuccessful(), restored.errors or restored.failures)

    def test_round24_consumer_call_guards_go_red_and_restore(self):
        path = ROOT/'launch/check_native.py'
        read = Path.read_text
        source = read(path)
        method = 'test_new_admission_uses_real_direct_consumers_without_developer_scripts'
        calls = ('native_tessellate.ear_clip', 'native_tessellate.signed_area',
                 'native_bspline.control_point_curve_polyline',
                 'native_preview.rectangle', 'native_preview.ring',
                 'native_dxf_reader.decode_dxf')
        for call in calls:
            self.assertIn(call, source)
            def changed(item, *args, **kwargs):
                return source.replace(call, call.replace('.', '.unused_', 1)) if item == path else read(item, *args, **kwargs)
            with self.subTest(call=call), patch.object(Path, 'read_text', changed):
                result = unittest.TestResult()
                AdmissionSource(method).run(result)
                self.assertFalse(result.wasSuccessful(), 'removed runtime consumer survived')
            restored = unittest.TestResult()
            AdmissionSource(method).run(restored)
            self.assertTrue(restored.wasSuccessful(), restored.errors or restored.failures)

    def test_nearest_consumer_call_guards_go_red_and_restore(self):
        # The required nearest consumers are real runtime bindings, not optional.
        # Removing any of them from the startup probe must turn the admission check
        # red, then restore green. (CallbackCurve is a type guard, not a called
        # binding.)
        path = ROOT/'launch/check_native.py'
        read = Path.read_text
        source = read(path)
        method = 'test_new_admission_uses_real_direct_consumers_without_developer_scripts'
        calls = ('native_nearest.PackedCurve', 'native_nearest.search',
                 'native_nearest.pack')
        for call in calls:
            self.assertIn(call, source)
            def changed(item, *args, **kwargs):
                # Remove every occurrence so the admission test genuinely goes red,
                # even when the consumer appears more than once (e.g. nearest.search).
                return source.replace(call, call.replace('.', '.unused_', 1)) if item == path else read(item, *args, **kwargs)
            with self.subTest(call=call), patch.object(Path, 'read_text', changed):
                result = unittest.TestResult()
                AdmissionSource(method).run(result)
                self.assertFalse(result.wasSuccessful(), 'removed nearest consumer survived')
            restored = unittest.TestResult()
            AdmissionSource(method).run(restored)
            self.assertTrue(restored.wasSuccessful(), restored.errors or restored.failures)

    def test_source_and_shared_binding_guards_are_load_bearing(self):
        read = Path.read_text
        source_path = ROOT/'narwhal3d/native_pure.py'
        source = source_path.read_text()
        def hidden(path,*args,**kwargs):
            return source+'\nif False:\n    def encode_obj(*args): return None\n' if path==source_path else read(path,*args,**kwargs)
        method = 'test_obsolete_encoder_names_absent_even_in_dead_code_and_packer_frozen'
        with patch.object(Path,'read_text',hidden):
            broken = unittest.TestResult()
            OBJRetirement(method).run(broken)
            self.assertFalse(broken.wasSuccessful())
        restored = unittest.TestResult()
        OBJRetirement(method).run(restored)
        self.assertTrue(restored.wasSuccessful(),restored.errors or restored.failures)
        native = load('admission23_mutation_obj',ROOT/'narwhal3d/native_obj.py')
        shared = native._packer()
        original = inspect.getsource(shared._binding)
        cases = (
            ('if _signature == signature:', 'if False:'),
            ('version = library.narwhal_pure_core_abi_version',
             'unused_encoder = library.narwhal_pure_core_obj_encode\n                version = library.narwhal_pure_core_abi_version'),
            ('obj_dsize, obj_dec', 'obj_dec, obj_dsize'),
            ('version = library.narwhal_pure_core_abi_version',
             'unused_encoder = library.narwhal_pure_core_dxf_encode\n                version = library.narwhal_pure_core_abi_version'),
        )
        method = 'test_three_slot_cache_no_retired_format_dependency'
        for old,new in cases:
            self.assertIn(old,original)
            namespace = dict(vars(shared))
            exec(compile(original.replace(old,new),'<admission23-binding-mutant>','exec'),namespace)
            mutant = types.FunctionType(namespace['_binding'].__code__,vars(shared))
            with self.subTest(mutation=old), patch.object(shared,'_binding',mutant):
                broken = unittest.TestResult()
                OBJRetirement(method).run(broken)
                self.assertFalse(broken.wasSuccessful())
            restored = unittest.TestResult()
            OBJRetirement(method).run(restored)
            self.assertTrue(restored.wasSuccessful(),restored.errors or restored.failures)


if __name__=='__main__':
    unittest.main(verbosity=2)
