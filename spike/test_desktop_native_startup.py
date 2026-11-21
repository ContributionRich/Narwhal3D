"""Real source-launcher/preflight contract using owned fake graphical executables.

Actual native libraries run in a child; no Blender or welcome window is launched.
The complementary startup_layout_gui.sh checks the real welcome-style GUI route.
"""
from pathlib import Path
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCES=('native_textfield','native_popup','command_transcript','native_transcript',
         'native_document_session','native_object_registry','native_document_observer',
         'native_history','undo','numparse','native_pure',
         'native_interpolate','native_coil','native_stl','native_obj',
         'native_vector_export','native_dxf','native_tween',
         'native_tessellate','native_bspline','native_preview','native_dxf_reader',
         'native_nearest','options','bridge','native_geometry')


REQUIRED_PURE_EXPORTS = {
    'narwhal_pure_core_interpolate_helper_v1': 'interpolation',
    'narwhal_pure_core_interpolate': 'interpolation',
    'narwhal_pure_core_interpolate_periodic': 'interpolation',
    'narwhal_pure_core_skin_control_net': 'interpolation',
    'narwhal_pure_core_coil_plan': 'coil',
    'narwhal_pure_core_catenary_plan_v2': 'catenary',
    'narwhal_pure_core_stl_size_owned': 'STL',
    'narwhal_pure_core_stl_encode_owned': 'STL',
    'narwhal_pure_core_stl_file_size': 'STL',
    'narwhal_pure_core_stl_file_decode': 'STL',
    'narwhal_pure_core_stl_import_mesh_decode': 'STL',
    'narwhal_pure_core_stl_normal': 'STL',
    'narwhal_pure_core_obj_encode_into': 'OBJ',
}


# Preserve all thirteen original no-write protocol checks. Additional bindings
# are presence-checked below; void free, valid-input validators and Tween's
# binding-only require_support cannot be exposed by an integer-zero mutation.
PROTOCOL_EXPORTS = dict(REQUIRED_PURE_EXPORTS)
REQUIRED_PURE_EXPORTS.update({
    'narwhal_pure_core_vector_encode_v1': 'vector',
    'narwhal_pure_core_vector_len_v1': 'vector',
    'narwhal_pure_core_vector_copy_v1': 'vector',
    'narwhal_pure_core_vector_free_v1': 'vector',
    'narwhal_pure_core_vector_validate_v1': 'vector',
    'narwhal_pure_core_dxf_owned_abi_version': 'DXF',
    'narwhal_pure_core_dxf_owned_colors': 'DXF',
    'narwhal_pure_core_dxf_owned_units': 'DXF',
    'narwhal_pure_core_dxf_owned_capacity': 'DXF',
    'narwhal_pure_core_dxf_owned_encode': 'DXF',
    'narwhal_pure_core_tween_plan_v1': 'Tween',
    'narwhal_pure_core_tween_knots_v1': 'Tween',
    'narwhal_pure_core_tween_lengths_v1': 'Tween',
})
PROTOCOL_EXPORTS.update({name: owner for name, owner in REQUIRED_PURE_EXPORTS.items()
                         if name.endswith(('vector_encode_v1', 'vector_len_v1', 'vector_copy_v1',
                                           'dxf_owned_units', 'dxf_owned_capacity', 'dxf_owned_encode'))})


REQUIRED_PURE_EXPORTS.update({
    'narwhal_pure_core_ear_clip_display': 'earclip',
    'narwhal_pure_core_earclip_signed_area_v1': 'earclip',
    'narwhal_pure_core_control_point_curve_preview': 'control preview',
    'narwhal_pure_core_preview_sample_v1': 'preview',
    'narwhal_pure_core_dxf_reader24_abi_version': 'DXF reader',
    'narwhal_pure_core_dxf_reader24_decode': 'DXF reader',
})
PROTOCOL_EXPORTS.update({name: REQUIRED_PURE_EXPORTS[name] for name in (
    'narwhal_pure_core_ear_clip_display', 'narwhal_pure_core_earclip_signed_area_v1',
    'narwhal_pure_core_control_point_curve_preview', 'narwhal_pure_core_preview_sample_v1',
    'narwhal_pure_core_dxf_reader24_decode')})


# The two required native nearest exports (scalar + host-callback).
# The batch export (narwhal_pure_core_nearest_on_nurbs_many) is optional and
# platform-qualified, so it is not a required startup export.
REQUIRED_PURE_EXPORTS.update({
    'narwhal_pure_core_nearest_on_nurbs': 'nearest',
    'narwhal_pure_core_nearest_on_callbacks_v1': 'nearest',
})
PROTOCOL_EXPORTS.update({name: REQUIRED_PURE_EXPORTS[name] for name in (
    'narwhal_pure_core_nearest_on_nurbs',
    'narwhal_pure_core_nearest_on_callbacks_v1')})


REQUIRED_PURE_EXPORTS.update({
    'narwhal_pure_core_options_abi': 'options',
    'narwhal_pure_core_options_resolve': 'options',
    'narwhal_pure_core_options_prompt': 'options',
})
PROTOCOL_EXPORTS.update({name: owner for name, owner in REQUIRED_PURE_EXPORTS.items()
                         if owner == 'options' and not name.endswith('_abi')})


def elf_export_entries(data):
    """Test-only ELF64 metadata, not a loader; modify only temporary copied SOs.

    Localizing a dynsym entry hides it from dlsym while retaining internal
    relocations. Renaming strings would invalidate GNU hashes and could make
    an otherwise healthy parser unloadable, a vacuous missing-owner fixture.
    """
    if data[:6] != b'\x7fELF\x02\x01':
        raise AssertionError('startup fixtures require a Linux little-endian ELF64 build')
    offset = struct.unpack_from('<Q', data, 40)[0]
    stride, count = struct.unpack_from('<HH', data, 58)
    if stride != 64 or offset + stride * count > len(data):
        raise AssertionError('invalid fixture ELF section table')
    sections = [struct.unpack_from('<IIQQQQIIQQ', data, offset+i*stride) for i in range(count)]
    exports = {}
    for section in sections:
        if section[1] != 11:  # SHT_DYNSYM
            continue
        strings = sections[section[6]]
        for entry in range(section[4], section[4]+section[5], section[9]):
            name, info, _visibility, index, value, size = struct.unpack_from('<IBBHQQ', data, entry)
            begin = strings[4]+name
            end = data.find(b'\0',begin,strings[4]+strings[5])
            if end < 0:
                raise AssertionError('unterminated fixture ELF symbol')
            if 0 < index < len(sections) and info >> 4:
                exports[bytes(data[begin:end]).decode('ascii')] = (entry,value,size,sections[index])
    return exports


class DesktopNativeStartup(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='desktop-native-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'launch').mkdir()
        (self.root/'narwhal3d/native').mkdir(parents=True)
        shutil.copy2(ROOT/'narwhal3d.sh',self.root/'narwhal3d.sh')
        shutil.copy2(ROOT/'launch/check_native.py',self.root/'launch/check_native.py')
        for name in SOURCES:
            shutil.copy2(ROOT/'narwhal3d'/(name+'.py'),self.root/'narwhal3d'/(name+'.py'))
        shutil.copytree(ROOT / "narwhal3d/kernel", self.root / "narwhal3d/kernel",
                        ignore=shutil.ignore_patterns("__pycache__", "bin"))
        writer = self.root / "narwhal3d/kernel/bin/brep_writer-linux-x86_64"
        writer.parent.mkdir(parents=True)
        shutil.copy2(ROOT / "narwhal3d/kernel/bin/brep_writer-linux-x86_64", writer)
        libraries={
            'libnarwhal_ui_core.so':Path(os.environ.get('NARWHAL_UI_CORE_LIBRARY',ROOT/'artifacts/ui-core/linux-x86_64/libnarwhal_ui_core.so')),
            "libnarwhal_geometry_core.so": Path(os.environ.get("NARWHAL_GEOMETRY_CORE", ROOT / "native/geometry_core/target/release/libnarwhal_geometry_core.so")),
            'libnarwhal_application_core.so':Path(os.environ.get('NARWHAL_APPLICATION_CORE',ROOT/'artifacts/application-core/linux-x86_64/libnarwhal_application_core.so')),
            'libnarwhal_pure_core.so':Path(os.environ.get('NARWHAL_PURE_CORE',ROOT/'native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so')),
        }
        for name,source in libraries.items():shutil.copy2(source,self.root/'narwhal3d/native'/name)
        shutil.copytree(ROOT/'artifacts/document-observer/linux-x86_64',
                        self.root/'narwhal3d/native/document-observer')
        runtime=self.root/'tools/blender-patched/blender'
        python=runtime.parent/'5.2/python/bin/python3.13'
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
        self.started=self.root/'graphical-started'
        for executable in (runtime,self.root/'artifacts/welcome/linux-x86_64/narwhal-welcome'):
            executable.parent.mkdir(parents=True,exist_ok=True)
            executable.write_text('#!/bin/sh\nprintf started > "'+str(self.started)+'"\n')
            executable.chmod(0o755)
        self.env={k:v for k,v in os.environ.items() if not k.startswith('NARWHAL')}
        self.env.update(HOME=str(self.root),NARWHAL3D_APPIMAGE='0',NARWHAL3D_NO_DIALOGS='1',PYTHONDONTWRITEBYTECODE='1')

    def run_launcher(self):
        return subprocess.run(['bash',str(self.root/'narwhal3d.sh')],env=self.env,text=True,
                              capture_output=True,timeout=20)

    def pure_library(self):
        return self.root/'narwhal3d/native/libnarwhal_pure_core.so'

    def assert_parser_still_works(self):
        script = (
            "import sys,types,pathlib; p=types.ModuleType('narwhal3d'); "
            "p.__path__=[str(pathlib.Path(sys.argv[1])/'narwhal3d')]; sys.modules['narwhal3d']=p; "
            "from narwhal3d.numparse import parse_length; "
            "assert parse_length('27cm','Meters') == (0.27,None)"
        )
        result = subprocess.run([sys.executable,'-I','-B','-c',script,str(self.root)],
                                env=self.env,text=True,capture_output=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def hide_pure_exports(self, original, names):
        data = bytearray(original)
        entries = elf_export_entries(data)
        for name in names:
            self.assertIn(name,entries)
            entry = entries[name][0]
            data[entry+4] &= 0x0f  # STB_LOCAL; preserve function type/address.
        self.pure_library().write_bytes(data)

    def assert_no_graphical_start(self, result):
        self.assertEqual(result.returncode,2,result.stdout+result.stderr)
        self.assertIn('graphical runtime not launched',result.stderr)
        self.assertNotIn('required native startup checks passed',result.stdout)
        self.assertFalse(self.started.exists())

    def test_parser_working_partial_core_each_owner_blocks_welcome_and_direct_runtime(self):
        original = self.pure_library().read_bytes()
        welcome = self.root/'artifacts/welcome/linux-x86_64/narwhal-welcome'
        welcome_bytes = welcome.read_bytes()
        secondary = self.root/'native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so'
        secondary.parent.mkdir(parents=True)
        secondary.write_bytes(original)  # Must not silently rescue stale adjacent selection.
        cases = [([name],owner) for name,owner in REQUIRED_PURE_EXPORTS.items()]
        cases.append((list(REQUIRED_PURE_EXPORTS),'interpolation'))
        for missing,owner in cases:
            with self.subTest(missing=missing):
                self.hide_pure_exports(original,missing)
                self.assert_parser_still_works()
                for direct in (False,True):
                    with self.subTest(direct=direct):
                        if direct:
                            welcome.unlink()
                        else:
                            welcome.write_bytes(welcome_bytes)
                            welcome.chmod(0o755)
                        result = self.run_launcher()
                        self.assert_no_graphical_start(result)
                        self.assertIn('required '+owner,result.stderr)
                self.pure_library().write_bytes(original)
                welcome.write_bytes(welcome_bytes)
                welcome.chmod(0o755)
        healthy = self.run_launcher()
        self.assertEqual(healthy.returncode,0,healthy.stdout+healthy.stderr)
        self.assertTrue(self.started.exists())

    def test_parser_working_wrong_core_abi_blocks_graphical_start(self):
        data = bytearray(self.pure_library().read_bytes())
        _entry,value,size,section = elf_export_entries(data)['narwhal_pure_core_abi_version']
        offset = section[4]+value-section[3]
        self.assertGreaterEqual(size,6)
        self.assertEqual(data[offset:offset+6],b'\xb8\x01\x00\x00\x00\xc3')
        data[offset+1:offset+5] = struct.pack('<I',2)
        self.pure_library().write_bytes(data)
        self.assert_parser_still_works()
        result = self.run_launcher()
        self.assert_no_graphical_start(result)
        self.assertIn('required interpolation',result.stderr)

    def test_parser_working_wrong_dxf_owned_version_blocks_graphical_start(self):
        data = bytearray(self.pure_library().read_bytes())
        entries = elf_export_entries(data)
        entry = entries['narwhal_pure_core_dxf_owned_abi_version'][0]
        _core_entry, value, size, _section = entries['narwhal_pure_core_abi_version']
        # Retarget only this symbol, not possibly aliased return-2 machine code.
        struct.pack_into('<QQ', data, entry + 8, value, size)
        self.pure_library().write_bytes(data)
        self.assert_parser_still_works()
        result = self.run_launcher()
        self.assert_no_graphical_start(result)
        self.assertIn('required DXF', result.stderr)

    def test_parser_working_wrong_reader24_version_blocks_graphical_start(self):
        data = bytearray(self.pure_library().read_bytes())
        entries = elf_export_entries(data)
        entry = entries['narwhal_pure_core_dxf_reader24_abi_version'][0]
        _source,value,size,_section = entries['narwhal_pure_core_dxf_owned_abi_version']
        # Retarget only this dlsym leaf; do not mutate possibly aliased core-1 code.
        struct.pack_into('<QQ',data,entry+8,value,size)
        self.pure_library().write_bytes(data)
        self.assert_parser_still_works()
        result = self.run_launcher()
        self.assert_no_graphical_start(result)
        self.assertIn('required DXF reader',result.stderr)

    def test_parser_working_no_write_protocol_each_owner_is_refused(self):
        original = self.pure_library().read_bytes()
        entries = elf_export_entries(original)
        for name,owner in PROTOCOL_EXPORTS.items():
            with self.subTest(owner=name):
                data = bytearray(original)
                _entry,value,size,section = entries[name]
                offset = section[4]+value-section[3]
                self.assertGreaterEqual(size,3)
                # Test-only x86_64 integer success with no caller-output writes.
                self.assertEqual(struct.unpack_from('<H',data,18)[0],62)
                data[offset:offset+3] = b'\x31\xc0\xc3'
                self.pure_library().write_bytes(data)
                self.assert_parser_still_works()
                result = self.run_launcher()
                self.assert_no_graphical_start(result)
                self.assertIn('required '+owner,result.stderr)
        self.pure_library().write_bytes(original)

    def test_each_new_owner_admission_bypass_is_detected_and_restored(self):
        library = self.pure_library().read_bytes()
        preflight = self.root/'launch/check_native.py'
        source = preflight.read_text()
        names = {'interpolation':'interpolation', 'coil':'coil', 'catenary':'catenary', 'STL':'stl', 'OBJ':'obj',
                 'vector':'vector', 'DXF':'dxf', 'Tween':'tween',
                 'earclip':'earclip', 'control preview':'control_preview',
                 'preview':'preview', 'DXF reader':'dxf_reader',
                 'nearest':'nearest', 'options':'option_framework'}
        for symbol,owner in REQUIRED_PURE_EXPORTS.items():
            with self.subTest(symbol=symbol):
                marker = "('"+owner+"', "+names[owner]+")"
                self.assertEqual(source.count(marker),1)
                self.hide_pure_exports(library,[symbol])
                self.assert_parser_still_works()
                preflight.write_text(source.replace(marker,"('"+owner+"', lambda: None)"))
                mutated = self.run_launcher()
                self.assertEqual(mutated.returncode,0,mutated.stdout+mutated.stderr)
                self.assertTrue(self.started.exists())
                with self.assertRaises(AssertionError):
                    self.assert_no_graphical_start(mutated)
                preflight.write_text(source)
                self.started.unlink()
                self.assert_no_graphical_start(self.run_launcher())
        self.pure_library().write_bytes(library)

    def test_appimage_welcome_delegates_preflight_without_bypassing_admission(self):
        # SDK fixture only: emulate the welcome child process, not its native UI.
        welcome = self.root / "artifacts/welcome/linux-x86_64/narwhal-welcome"
        welcome.write_text("#!" + sys.executable + "\n" +
            "import os, pathlib, subprocess, sys\n"
            "root = pathlib.Path(__file__).resolve().parents[3]\n"
            "python = os.environ.get('NARWHAL3D_WELCOME_PREFLIGHT')\n"
            "assert python, 'AppImage welcome must receive preflight interpreter'\n"
            "status = subprocess.run([python, '-I', '-B', str(root / 'launch/check_native.py')]).returncode\n"
            "if status:\n"
            "    print('graphical runtime not launched', file=sys.stderr)\n"
            "    sys.exit(2)\n"
            "(root / 'graphical-started').write_text('started')\n")
        welcome.chmod(0o755)
        self.env["NARWHAL3D_APPIMAGE"] = "1"
        original = self.pure_library().read_bytes()
        self.hide_pure_exports(original, ["narwhal_pure_core_nearest_on_callbacks_v1"])
        self.assert_no_graphical_start(self.run_launcher())
        self.pure_library().write_bytes(original)
        healthy = self.run_launcher()
        self.assertEqual(healthy.returncode, 0, healthy.stdout + healthy.stderr)
        self.assertTrue(self.started.exists())

    def test_exact_join_capability_exports_and_verifier_are_required(self):
        path = self.root / "narwhal3d/native/libnarwhal_geometry_core.so"
        original = path.read_bytes()
        entries = elf_export_entries(original)
        for symbol in ("narwhal_geometry_core_join_mapped_v1", "narwhal_geometry_core_verify_join_v1"):
            data = bytearray(original)
            data[entries[symbol][0] + 4] &= 0x0f
            path.write_bytes(data)
            result = self.run_launcher()
            self.assert_no_graphical_start(result)
            self.assertIn("exact Join unavailable", result.stderr)
        for symbol, value in (("narwhal_geometry_core_capabilities", 511),
                              ("narwhal_geometry_core_verify_join_v1", 0)):
            data = bytearray(original)
            _entry, address, size, section = entries[symbol]
            self.assertEqual(struct.unpack_from("<H", data, 18)[0], 62)
            self.assertGreaterEqual(size, 6)
            offset = section[4] + address - section[3]
            data[offset:offset+6] = bytes([0xb8]) + struct.pack("<I", value) + bytes([0xc3])
            path.write_bytes(data)
            result = self.run_launcher()
            self.assert_no_graphical_start(result)
            expected = "exact Join unavailable" if value else "verifier accepted changed frozen geometry"
            self.assertIn(expected, result.stderr)
        path.write_bytes(original)
        restored = self.run_launcher()
        self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
        self.assertTrue(self.started.exists())

    def test_matching_native_components_launch_direct_runtime(self):
        (self.root/'artifacts/welcome/linux-x86_64/narwhal-welcome').unlink()
        result = self.run_launcher()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('required native startup checks passed',result.stdout)
        self.assertTrue(self.started.exists())

    def test_matching_native_components_launch_welcome(self):
        result=self.run_launcher()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('required native startup checks passed',result.stdout)
        self.assertTrue(self.started.exists())

    def test_missing_ui_never_launches_graphical_runtime(self):
        (self.root/'narwhal3d/native/libnarwhal_ui_core.so').unlink()
        result=self.run_launcher()
        self.assertEqual(result.returncode,2,result.stdout+result.stderr)
        self.assertIn('graphical runtime not launched',result.stderr)
        self.assertFalse(self.started.exists())

    def test_broken_numeric_core_never_launches_graphical_runtime(self):
        (self.root/'narwhal3d/native/libnarwhal_pure_core.so').write_bytes(b'invalid library')
        result=self.run_launcher()
        self.assertEqual(result.returncode,2,result.stdout+result.stderr)
        self.assertIn('number parser unavailable',result.stderr)
        self.assertFalse(self.started.exists())

    def test_missing_observer_never_launches_graphical_runtime(self):
        (self.root/'narwhal3d/native/document-observer/_narwhal_document_observer.so').unlink()
        result=self.run_launcher()
        self.assertEqual(result.returncode,2,result.stdout+result.stderr)
        self.assertIn('observer',result.stderr)
        self.assertFalse(self.started.exists())

    def test_stale_primary_lacking_callback_cannot_be_rescued_by_fresh_secondary(self):
        # A stale primary library that carries the required scalar export but lacks
        # the required host-callback export must refuse startup even when a qualified
        # fresh secondary build sits beside it: the consumer binds the primary path
        # and nothing may silently fall back to the secondary. Both fixtures are
        # compiled here from the same healthy source, so the test does not depend on
        # any particular installed build.
        original = self.pure_library().read_bytes()
        entries = elf_export_entries(original)
        stale = bytearray(original)
        stale[entries['narwhal_pure_core_nearest_on_callbacks_v1'][0] + 4] &= 0x0f
        self.pure_library().write_bytes(bytes(stale))
        fresh_secondary = (self.root/'native/pure_core/target/x86_64-unknown-linux-gnu/release'
                           '/libnarwhal_pure_core.so')
        fresh_secondary.parent.mkdir(parents=True)
        fresh_secondary.write_bytes(original)  # The qualified build must not rescue the stale primary.
        result = self.run_launcher()
        self.assert_no_graphical_start(result)
        self.assertIn('required nearest', result.stderr)
        self.assertIn('nearest_on_callbacks_v1', result.stderr)
        # The refusal came from the stale primary, not from any other consumer:
        # once the primary is the qualified build again, the same launcher passes.
        self.pure_library().write_bytes(original)
        healthy = self.run_launcher()
        self.assertEqual(healthy.returncode, 0, healthy.stdout+healthy.stderr)
        self.assertIn('required native startup checks passed', healthy.stdout)
        self.assertTrue(self.started.exists())

    def test_gui_core_receipt_rejects_wrong_binary_and_source(self):
        import ast
        import hashlib
        import json
        text=(ROOT/'spike/test_ui_core_review_gui.py').read_text()
        tree=ast.parse(text)
        function=next(node for node in tree.body if isinstance(node,ast.FunctionDef)
                      and node.name=='application_core_source_matches')
        source=ast.get_source_segment(text,function)
        namespace=dict(Path=Path,hashlib=hashlib,json=json)
        exec(compile(source,'<GUI core receipt check>','exec'),namespace)
        verify=namespace['application_core_source_matches']
        library=self.root/'probe.so';library.write_bytes(b'qualified binary')
        item=self.root/'input.rs';item.write_bytes(b'qualified source')
        receipt={'library_sha256':hashlib.sha256(library.read_bytes()).hexdigest(),
                 'source_inputs':{'input.rs':hashlib.sha256(item.read_bytes()).hexdigest()}}
        (self.root/'build.json').write_text(json.dumps(receipt))
        self.assertTrue(verify(self.root,library))
        library.write_bytes(b'wrong binary')
        self.assertFalse(verify(self.root,library))
        mutant=dict(namespace)
        exec(compile(source.replace("digest == receipt['library_sha256']",'True'),'<receipt mutant>','exec'),mutant)
        with self.assertRaises(AssertionError):
            self.assertFalse(mutant['application_core_source_matches'](self.root,library))
        library.write_bytes(b'qualified binary')
        item.write_bytes(b'changed source')
        self.assertFalse(verify(self.root,library))
        item.write_bytes(b'qualified source')
        self.assertTrue(verify(self.root,library))

    def test_bypass_mutation_is_detected(self):
        (self.root/'narwhal3d/native/libnarwhal_ui_core.so').unlink()
        launcher=self.root/'narwhal3d.sh'
        source=launcher.read_text()
        marker='! "$native_python" -I -B "$ROOT/launch/check_native.py"'
        self.assertEqual(source.count(marker),1)
        launcher.write_text(source.replace(marker,'false'))
        result=self.run_launcher()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(self.started.exists(),'mutant must demonstrate the forbidden launch')
        with self.assertRaises(AssertionError):
            self.assertEqual(result.returncode,2,'startup admission regression')
        launcher.write_text(source)
        self.started.unlink()
        result=self.run_launcher()
        self.assertEqual(result.returncode,2,result.stderr)
        self.assertFalse(self.started.exists())


if __name__=='__main__':unittest.main(verbosity=2)
