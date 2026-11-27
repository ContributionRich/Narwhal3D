"""Actual native-library, host packaging and source-admission contracts.

Pure ABI semantics, negative controls and driver lifecycle tests live in
native/source_tools/tests/pure_gate.rs and pure_mutations.rs. These tests retain
the Python packaging API/AST and real copied-library coverage.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import os
from contextlib import redirect_stdout
import io
import inspect
import json
import struct
import textwrap
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_pure_core as gate
import build_linux_native as linux_build
import build_macos_native as mac_build
import assemble_linux_release as linux_assemble
import assemble_macos_release as mac_assemble
import assemble_windows_release as win_assemble
import verify_linux_release as linux_verify
import verify_windows_release as win_verify


ROUND23_ADDED_EXPORTS = tuple("narwhal_pure_core_"+name for name in ('vector_encode_v1', 'vector_len_v1', 'vector_copy_v1', 'vector_free_v1', 'vector_validate_v1', 'dxf_owned_abi_version', 'dxf_owned_colors', 'dxf_owned_units', 'dxf_owned_capacity', 'dxf_owned_encode', 'tween_plan_v1', 'tween_knots_v1', 'tween_lengths_v1'))
ROUND24_ADDED_EXPORTS = tuple("narwhal_pure_core_"+name for name in ('ear_clip_display', 'earclip_signed_area_v1', 'control_point_curve_preview', 'preview_sample_v1', 'dxf_reader24_abi_version', 'dxf_reader24_decode'))
ROUND24_NATIVE_FILES = {
    'native/pure_core/tests/data/preview24_linux.tsv',
    'native/pure_core/tests/data/preview24_windows_msvc.tsv',
    'native/pure_core/tests/data/preview24_windows_msvc.provenance.json',
    'native/pure_core/tests/data/preview24_provenance.txt',
    'native/pure_core/tests/dxf_reader24.h',
    'native/pure_core/tests/dxf_reader24.rs',
    'native/pure_core/tests/earclip24_api.h',
    'native/pure_core/tests/earclip24_golden.rs',
    'native/pure_core/tests/fixtures/dxf_reader24/README.md',
    'native/pure_core/tests/fixtures/dxf_reader24/capture.json',
    'native/pure_core/tests/fixtures/dxf_reader24/corpus.bin',
    'native/pure_core/tests/fixtures/dxf_reader24/manifest.tsv',
    'native/pure_core/tests/fixtures/earclip24_original.txt',
    'native/pure_core/tests/fixtures/earclip24_provenance.json',
    'native/pure_core/tests/fixtures/earclip24_safety.json',
    'native/pure_core/tests/preview24_api.h',
    'native/pure_core/tests/preview24_golden.rs',
}
ROUND24_MODULES = {"native/pure_core/src/preview_primitives.rs"}


POLYLINE_EXPORTS = ("narwhal_pure_core_curve_curve_record_hits", "narwhal_pure_core_nearest_on_polyline", "narwhal_pure_core_polyline_samples", "narwhal_pure_core_polycurve_samples", "narwhal_pure_core_remove_join_cv_v1",
                    "narwhal_pure_core_offset_fit", "narwhal_pure_core_surface_offset", "narwhal_pure_core_surface_grid", "narwhal_pure_core_surface_display_params", "narwhal_pure_core_dxf_approx_points", "narwhal_pure_core_planar_area_centroid")


def pe_fixture(*, parser_abi=2, missing="", svg_abi=14, alias_abi=1, dxf_abi=2, reader_abi=1):
    """Minimal PE export/import tables; data fixture, never executable native code."""
    data = bytearray(8192)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3c, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", data, 0x84, 0x8664, 1, 0, 0, 0, 240, 0x2002)
    optional = 0x98
    struct.pack_into("<H", data, optional, 0x20b)
    struct.pack_into("<I", data, optional + 60, 0x200)
    struct.pack_into("<II", data, optional + 112, 0x1200, 0x600)
    struct.pack_into("<II", data, optional + 120, 0x2600, 40)
    struct.pack_into("<IIII", data, optional + 240 + 8, 0x1e00, 0x1000, 0x1e00, 0x200)
    names = [name for name in ("narwhal_pure_core_abi_version", *gate.PURE_CORE_PARSE_EXPORTS, *gate.PURE_CORE_RETIREMENT_EXPORTS, *POLYLINE_EXPORTS) if name != missing]
    assert len(names) <= 128, "PE fixture export table capacity exceeded"
    offset = lambda rva: rva - 0x1000 + 0x200
    struct.pack_into("<IIIII", data, offset(0x1200) + 20,
                     len(names), len(names), 0x1240, 0x1440, 0x1640)
    cursor = 0x1800
    for i, name in enumerate(names):
        struct.pack_into("<I", data, offset(0x1240) + i * 4, 0x2800 + i * 16)
        struct.pack_into("<I", data, offset(0x1440) + i * 4, cursor)
        struct.pack_into("<H", data, offset(0x1640) + i * 2, i)
        encoded = name.encode() + b"\0"
        data[offset(cursor):offset(cursor) + len(encoded)] = encoded
        cursor += len(encoded)
        assert cursor <= 0x2600, "PE export names overlap the import table"
        value = parser_abi if name == "narwhal_pure_core_parse_abi" else (2 if name in {"narwhal_pure_core_calc_abi2", "narwhal_pure_core_bearing_abi2"} else 1)
        if name == "narwhal_pure_core_svgpath_owned_version": value = svg_abi
        if name == "narwhal_pure_core_alias_identity": value = alias_abi
        if name == "narwhal_pure_core_dxf_owned_abi_version": value = dxf_abi
        if name == "narwhal_pure_core_dxf_reader24_abi_version": value = reader_abi
        data[offset(0x2800) + i * 16:offset(0x2800) + i * 16 + 6] = b"\xb8" + struct.pack("<I", value) + b"\xc3"
    struct.pack_into("<IIIII", data, offset(0x2600), 0x2680, 0, 0, 0x2640, 0x2680)
    data[offset(0x2640):offset(0x2640) + 13] = b"kernel32.dll\0"
    struct.pack_into("<Q", data, offset(0x2680), (1 << 63) | 1)
    return bytes(data)


class PackageTests(unittest.TestCase):
    def test_round20_public_closure_retains_host_suites(self):
        # Path/AST metadata only: never load the golden corpora into this test.
        closure = gate.require_rust_public_closure(ROOT / "native/pure_core")
        self.assertEqual(set(closure), set(gate.RUST_INTEGRATION_FILES))
        self.assertEqual(len(gate.RUST_INTEGRATION_SUITES), 40)
        for suite in ("interpolate32_properties", "options29_engine", "options29_ffi", "options29_prompt",
                      "options29_prompt_ffi", "options29_registration"):
            self.assertIn(suite, gate.RUST_INTEGRATION_SUITES)
        import source_tools
        public = set(source_tools.NATIVE_FILES + source_tools.TEST_FILES)
        for host in ("spike/test_native_nearest.py", "spike/test_native_display_samples.py",
                     "spike/test_native_curve_samples.py", "spike/test_native_arclength.py",
                     "spike/test_coil_owned.py", "spike/test_interpolate.py",
                     "spike/test_interpolate_owned_boundary.py", "spike/test_stl_import_boundary.py",
                     "spike/test_catenary.py", "spike/test_catenary_owned.py",
                     "spike/test_native_obj.py", "spike/test_obj_writer.py",
                     "spike/test_dxf_writer_owned.py", "spike/test_native_admission_owned.py"):
            self.assertIn(host, gate.PURE_CORE_PYTESTS)
            self.assertIn(host, public)
            self.assertTrue((ROOT / host).is_file(), host)

    def test_round22_required_obj_adapter_and_host_routes_are_public(self):
        import source_tools as public
        self.assertTrue((ROOT / "narwhal3d/native_obj.py").is_file())
        self.assertIn("narwhal3d/native_obj.py", public.recursive_files("narwhal3d"))
        for host in ("spike/test_catenary.py", "spike/test_catenary_owned.py",
                     "spike/test_obj_writer.py", "spike/test_native_obj.py"):
            self.assertIn(host, gate.PURE_CORE_PYTESTS)
        self.assertEqual(gate.PURE_CORE_RETIREMENT_IDENTITY["retirement_abi"], 26)
        for export in ("narwhal_pure_core_catenary_plan_v2", "narwhal_pure_core_obj_encode_into"):
            self.assertIn(export, gate.PURE_CORE_RETIREMENT_EXPORTS)

    def test_independent_binary_stl_capture_needs_no_public_audit_exception(self):
        import source_tools as audit_public_tree
        path = "native/pure_core/tests/fixtures/stl_prefix_headers.bin"
        self.assertEqual((ROOT / path).stat().st_size, 40960)
        data = (ROOT / path).read_bytes()
        self.assertEqual(len(data), 40960)
        self.assertEqual(hashlib.sha256(data).hexdigest(),
                         "c8ca18b3fb9048eb7a6a116e7aafdb7d36361ceded90aa2abd58de851df00b8a")
        self.assertEqual(audit_public_tree.audit_entries({path: ("100644", data)}, require_manifest=False), [])
        self.assertIn(path, gate.RUST_INTEGRATION_FILES)

    def test_linux_build_inventory_tracks_new_header_and_capture_bytes(self):
        paths = ("native/pure_core/tests/catenary_v2.h",
                 "native/pure_core/tests/fixtures/stl_prefix_headers.bin",
                 "native/pure_core/tests/fixtures/catenary_windows_msvc.provenance.json",
                 "native/pure_core/tests/vector23_api.h", "native/pure_core/tests/dxf_owned.h",
                 "native/pure_core/tests/tween_v1.h",
                 "native/pure_core/tests/fixtures/dxf_writer23/capture.json",
                 "native/pure_core/tests/fixtures/dxf_writer23/units-default.dxf",
                 "native/pure_core/tests/fixtures/dxf_writer23/random-coordinates.bin",
                 *sorted(ROUND24_NATIVE_FILES | ROUND24_MODULES))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in paths:
                file = root / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(b"fixture input")
            before = linux_build.inventory(root, "pure_core")
            self.assertEqual(set(before), set(paths))
            for name in paths:
                (root / name).write_bytes(b"changed input")
                after = linux_build.inventory(root, "pure_core")
                self.assertNotEqual(before[name], after[name])
                self.assertEqual(after[name], hashlib.sha256(b"changed input").hexdigest())
                (root / name).write_bytes(b"fixture input")
            self.assertEqual(linux_build.inventory(root, "pure_core"), before)

    def test_linux_build_refuses_before_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, output = base / "source", base / "output"
            for path in (source / "native/pure_core", output / "pure_core-target/release",
                         base / "seed", base / "occt", base / "libs"):
                path.mkdir(parents=True)
            (output / "pure_core-target/release/libnarwhal_pure_core.so").write_bytes(b"old core")
            argv = ["build", "--source-root", str(source), "--output", str(output),
                    "--seed-target", str(base / "seed"), "--occt-source", str(base / "occt"),
                    "--occt-libs", str(base / "libs"), "--crate", "pure_core"]
            with patch.object(sys, "argv", argv), patch.object(linux_build.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
                    patch.object(linux_build, "probe_pure_core_parser", side_effect=ValueError("parser refused")):
                with self.assertRaisesRegex(ValueError, "parser refused"):
                    linux_build.main()
            self.assertFalse((output / "bin/libnarwhal_pure_core.so").exists())

    def test_linux_assembler_rejects_parser_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for name in linux_assemble.NATIVE_COMPONENTS:
                (directory / name).write_bytes(b"fixture")
                (directory / name).chmod(0o755)
            (directory / "narwhal-welcome").write_bytes(b" ".join((
                b"--runtime is required", b"--bootstrap is required", b"NARWHAL3D_WELCOME_REQUEST",
                b"NARWHAL3D_VERSION", b"releases?per_page=20", b"download sha256 does not match",
                b"could not atomically replace current AppImage")))
            doc = directory / "narwhal-document-core"
            import verify_document_core
            manifest = {"artifacts": [{"platform": "linux-x86_64", "size": doc.stat().st_size,
                                      "sha256": linux_assemble.sha256_file(doc)}], "source_aggregate_sha256": "fixture"}
            with patch.object(verify_document_core, "verify_directory", return_value=manifest), \
                    patch.object(linux_assemble, "probe_supervisor_memory"), \
                    patch.object(linux_assemble, "scan_runtime_glibc", return_value=((2, 28), "fixture")), \
                    patch.object(linux_assemble, "probe_native_library_abi", return_value=1), \
                    patch.object(linux_assemble, "probe_pure_core_parser", side_effect=ValueError("parser refused")):
                with self.assertRaisesRegex(linux_assemble.ReleaseError, "parser refused"):
                    linux_assemble.validate_native_components(directory, directory, directory)

    def test_linux_verifier_executes_adjacent_parser(self):
        with patch.object(linux_verify, "probe_pure_core_parser", side_effect=ValueError("parser refused")) as probe:
            with self.assertRaisesRegex(linux_verify.VerificationError, "parser refused"):
                linux_verify.verify_native_library_abis(Path("app"), Path("python"), {"ENV": "value"})
            probe.assert_called_once_with(Path("app/narwhal3d/native/libnarwhal_pure_core.so"),
                                          Path("python"), env={"ENV": "value"})

    def test_linux_manifest_requires_parser_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / linux_verify.APP_ROOT_RELATIVE
            runtime, adjacent = app / linux_verify.NATIVE_COMPONENT_RELATIVE, app / "narwhal3d/native"
            runtime.mkdir(parents=True)
            adjacent.mkdir(parents=True)
            records = {"required_glibc": "2.28"}
            for name in linux_verify.NATIVE_COMPONENTS:
                file = runtime / name
                file.write_bytes(name.encode())
                records[name] = {"size": file.stat().st_size, "sha256": linux_verify.sha256_file(file)}
                if name in linux_verify.PACKAGED_ACCELERATORS:
                    (adjacent / name).write_bytes(file.read_bytes())
            for name, (_, abi) in linux_verify.NATIVE_LIBRARY_ABIS.items():
                records[name]["abi"] = abi
            records["libnarwhal_application_core.so"].update(history_abi=1, exports=list(linux_verify.APPLICATION_CORE_EXPORTS))
            records["narwhal-welcome"]["launch_contract"] = 1
            pure = records["libnarwhal_pure_core.so"]
            pure.update(gate.PURE_CORE_PARSE_IDENTITY)
            observer = adjacent / "document-observer"
            observer.mkdir()
            (observer / "_narwhal_document_observer.so").write_bytes(b"observer")
            (observer / "build.json").write_text("{}\n")
            records["document-observer"] = {"sha256": linux_verify.sha256_file(observer / "_narwhal_document_observer.so"),
                                            "qualification": "fixture"}
            linux_verify.verify_native_manifest(root, records)
            for key in ("parse_abi", "parser_exports", "reply_bytes"):
                saved = pure.pop(key)
                with self.assertRaisesRegex(linux_verify.VerificationError, "parser"):
                    linux_verify.verify_native_manifest(root, records)
                pure[key] = saved
            linux_verify.verify_native_manifest(root, records)

    def test_windows_static_identity_and_native_execution(self):
        exports = ("narwhal_pure_core_abi_version", *gate.PURE_CORE_PARSE_EXPORTS, *gate.PURE_CORE_RETIREMENT_EXPORTS, *POLYLINE_EXPORTS)
        identity = {"machine": "pe32plus-x86-64", "exports": exports}
        with patch.object(win_verify, "inspect_pe_dll", return_value=identity) as inspect:
            with patch.object(sys, "platform", "linux"), patch.object(win_verify, "probe_pure_core_parser") as probe:
                record = win_verify.pure_core_record(Path("core.dll"))
                self.assertEqual(record["parse_abi"], 2)
                probe.assert_not_called()
            self.assertEqual(inspect.call_args.kwargs["constant_exports"],
                             {"narwhal_pure_core_abi_version": 1, "narwhal_pure_core_parse_abi": 2,
                                  "narwhal_pure_core_calc_abi2": 2, "narwhal_pure_core_bearing_abi2": 2,
                                  "narwhal_pure_core_coords_owned_abi1": 1,
                                  "narwhal_pure_core_svgpath_owned_version": 14, "narwhal_pure_core_alias_identity": 1,
                          "narwhal_pure_core_dxf_owned_abi_version": 2,
                          "narwhal_pure_core_dxf_reader24_abi_version": 1})
            with patch.object(sys, "platform", "win32"), patch.object(win_verify, "probe_pure_core_parser", side_effect=ValueError("semantic mutant")):
                with self.assertRaisesRegex(win_verify.VerificationError, "semantic mutant"):
                    win_verify.pure_core_record(Path("core.dll"))
        for missing in gate.PURE_CORE_PARSE_EXPORTS:
            with patch.object(win_verify, "inspect_pe_dll", return_value={**identity, "exports": tuple(e for e in exports if e != missing)}):
                with self.assertRaisesRegex(win_verify.VerificationError, "parser ABI exports"):
                    win_verify.verify_pure_core_identity(Path("old.dll"))

    def test_windows_pe_missing_exports_and_wrong_parser_abi(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(sys, "platform", "linux"):
            path = Path(tmp) / "core.dll"
            path.write_bytes(pe_fixture())
            self.assertEqual(win_verify.pure_core_record(path)["parse_abi"], 2)
            for kwargs in ({"parser_abi": 1}, {"svg_abi": 1}, {"alias_abi": 14}, {"dxf_abi": 1}, {"reader_abi": 2},
                           *({"missing": name} for name in (*gate.PURE_CORE_PARSE_EXPORTS,*gate.PURE_CORE_RETIREMENT_EXPORTS, *POLYLINE_EXPORTS)),
                           {"missing": "narwhal_pure_core_parse_abi"},
                           {"missing": "narwhal_pure_core_parse_v2"}):
                path.write_bytes(pe_fixture(**kwargs))
                with self.assertRaises(win_verify.VerificationError):
                    win_verify.pure_core_record(path)
            path.write_bytes(pe_fixture())
            self.assertEqual(win_verify.pure_core_record(path)["parse_abi"], 2)

    def test_windows_manifest_parser_identity_is_rechecked(self):
        tree = ast.parse(inspect.getsource(win_verify.verify_directory))
        branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                      and ast.unparse(node.test) == "filename == 'narwhal_pure_core.dll'")
        code = compile(ast.Module(body=[branch], type_ignores=[]), "<windows-parser-record>", "exec")
        expected = {"abi": 1, "parse_abi": 2, "exports": list(gate.PURE_CORE_PARSE_EXPORTS),
                    "machine": "pe32plus-x86-64"}
        with patch.object(win_verify, "pure_core_record", return_value=expected) as probe:
            for field in (None, "parse_abi", "exports"):
                record = dict(expected)
                if field:
                    del record[field]
                scope = dict(vars(win_verify), filename="narwhal_pure_core.dll", path=Path("core.dll"), record=record)
                if field:
                    with self.assertRaisesRegex(win_verify.VerificationError, "parser manifest"):
                        exec(code, scope)
                else:
                    exec(code, scope)
            self.assertEqual(probe.call_count, 3)

    def test_windows_assembly_calls_parser_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "core.dll").write_bytes(b"fixture")
            with patch.dict(win_verify.NATIVE_COMPONENT_PATHS, {"narwhal_pure_core.dll": "core.dll"}, clear=True), \
                    patch.object(win_verify, "verify_application_core_copies"), patch.object(win_verify, "verify_application_core_source"), \
                    patch.object(win_verify, "pure_core_record", side_effect=win_verify.VerificationError("parser refused")):
                with self.assertRaisesRegex(win_verify.VerificationError, "parser refused"):
                    win_assemble.native_records(root)

    def test_macos_package_executes_runtime_parser(self):
        with patch.object(mac_assemble, "probe_pure_core_parser", side_effect=ValueError("parser refused")) as probe:
            with self.assertRaisesRegex(ValueError, "parser refused"):
                mac_assemble.verify_pure_core(Path("app"), Path("python"))
            probe.assert_called_once_with(Path("app/runtime/macos-arm64/bin/libnarwhal_pure_core.dylib"), Path("python"))

    def test_platform_entrypoints_retain_parser_and_existing_cores(self):
        # Source wiring coverage for build/DMG entrypoints that require native OS tools.
        for module, function, count in ((mac_build, "probe_pure_core_parser", 2),
                                        (mac_assemble, "verify_pure_core", 2)):
            tree = ast.parse(Path(module.__file__).read_text())
            calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Name) and n.func.id == function]
            self.assertEqual(len(calls), count)
        script = (ROOT / "scripts/build_windows_preview.ps1").read_text()
        self.assertIn("from verify_windows_release import pure_core_record", script)
        self.assertIn("print(pure_core_record(Path(sys.argv[2])))", script)
        self.assertEqual(script.count("$PureParserProbe, $NarwhalSource, $PureCoreTarget)"), 2)
        self.assertIn("libnarwhal_ui_core.so", linux_verify.NATIVE_COMPONENTS)
        self.assertIn("libnarwhal_application_core.so", linux_verify.NATIVE_COMPONENTS)
        self.assertIn("narwhal_ui_core.dll", win_verify.NATIVE_COMPONENT_PATHS)
        self.assertIn("narwhal_application_core.dll", win_verify.NATIVE_COMPONENT_PATHS)


class RetirementPackageTests(unittest.TestCase):
    def assert_entrypoint_refuses(self, module, method):
        # Reuse the same minimal package/build inputs as the established parser
        # refusal tests, but allow parser success and fail only the new probe.
        original = getattr(PackageTests,method)
        class SelectRetirement(ast.NodeTransformer):
            def visit_Constant(self,node):
                if node.value == "probe_pure_core_parser":
                    return ast.copy_location(ast.Constant("probe_pure_core_retirement"),node)
                return node
        tree = ast.fix_missing_locations(SelectRetirement().visit(ast.parse(textwrap.dedent(inspect.getsource(original)))))
        namespace = {}
        exec(compile(tree,"<retirement-entrypoint-test>","exec"),globals(),namespace)
        with patch.object(module,"probe_pure_core_parser",return_value=gate.PURE_CORE_PARSE_IDENTITY):
            if module is linux_build:
                with patch.object(module,"probe_pure_core_nearest_batch",return_value={}), \
                        patch.object(module,"probe_pure_core_curve_hits",return_value={}), \
                        patch.object(module,"probe_pure_core_curve_record_hits",return_value={}), \
                        patch.object(module,"probe_pure_core_polyline_nearest",return_value={}), \
                        patch.object(module,"probe_pure_core_nurbs_record",return_value={}), \
                        patch.object(module,"probe_pure_core_line_samples",return_value={}), \
                        patch.object(module,"probe_pure_core_polyline_samples",return_value={}), \
                        patch.object(module,"probe_pure_core_polycurve_samples",return_value={}), \
                        patch.object(module,"probe_pure_core_offset_fit",return_value={}), \
                        patch.object(module,"probe_pure_core_dxf_approx",return_value={}), \
                        patch.object(module,"probe_pure_core_surface_offset",return_value={}), \
                        patch.object(module,"probe_pure_core_planar_area",return_value={}), \
                        patch.object(module,"probe_pure_core_surface_grid",return_value={}), \
                        patch.object(module,"probe_pure_core_surface_display",return_value={}), \
                        patch.object(module,"probe_pure_core_remove_join_cv",return_value={}):
                    namespace[method](self)
            else:
                namespace[method](self)

    def test_linux_entrypoints_and_source_gate_refuse_retirement_failure(self):
        for module,method in (
            (linux_build,"test_linux_build_refuses_before_staging"),
            (linux_assemble,"test_linux_assembler_rejects_parser_failure"),
            (linux_verify,"test_linux_verifier_executes_adjacent_parser"),
        ):
            with self.subTest(module=module.__name__): self.assert_entrypoint_refuses(module,method)
        with patch.object(gate,"probe_pure_core_parser",return_value=gate.PURE_CORE_PARSE_IDENTITY), \
                patch.object(gate,"probe_pure_core_retirement",side_effect=ValueError("retirement refused")) as probe:
            with self.assertRaisesRegex(gate.GateError,"retirement refused"): gate.step_direct_abi(Path("core.so"))
            probe.assert_called_once()

    def test_windows_static_and_native_retirement_refusal(self):
        exports = ("narwhal_pure_core_abi_version",*gate.PURE_CORE_PARSE_EXPORTS,*gate.PURE_CORE_RETIREMENT_EXPORTS, *POLYLINE_EXPORTS)
        identity = {"machine":"pe32plus-x86-64","exports":exports}
        for missing in gate.PURE_CORE_RETIREMENT_EXPORTS:
            with self.subTest(missing=missing), patch.object(win_verify,"inspect_pe_dll",return_value={
                    **identity,"exports":tuple(e for e in exports if e != missing)}):
                with self.assertRaisesRegex(win_verify.VerificationError,"retirement"):
                    win_verify.verify_pure_core_identity(Path("core.dll"))
        with patch.object(win_verify,"inspect_pe_dll",return_value=identity), \
                patch.object(win_verify,"probe_pure_core_parser",return_value=gate.PURE_CORE_PARSE_IDENTITY), \
                patch.object(win_verify,"probe_pure_core_retirement",side_effect=ValueError("retirement refused")) as probe:
            with patch.object(sys,"platform","linux"):
                win_verify.pure_core_record(Path("core.dll"))
                probe.assert_not_called() # no fake cross-host semantic claim
            with patch.object(sys,"platform","win32"):
                with self.assertRaisesRegex(win_verify.VerificationError,"retirement refused"):
                    win_verify.pure_core_record(Path("core.dll"))
                probe.assert_called_once_with(Path("core.dll"))

    def test_windows_assembler_rejects_actual_pe_missing_new_owners_and_wrong_dxf_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.dict(win_verify.NATIVE_COMPONENT_PATHS,{'narwhal_pure_core.dll':'core.dll'},clear=True), \
                    patch.object(win_verify,'verify_application_core_copies'), \
                    patch.object(win_verify,'verify_application_core_source'), patch.object(sys,'platform','linux'):
                for missing in ROUND23_ADDED_EXPORTS + ROUND24_ADDED_EXPORTS:
                    (root/'core.dll').write_bytes(pe_fixture(missing=missing))
                    with self.subTest(missing=missing), self.assertRaises(win_verify.VerificationError):
                        win_assemble.native_records(root)
                (root/'core.dll').write_bytes(pe_fixture(dxf_abi=1))
                with self.assertRaises(win_verify.VerificationError):
                    win_assemble.native_records(root)

    def test_windows_actual_pe_admission_bypass_is_red_then_green(self):
        method = 'test_windows_assembler_rejects_actual_pe_missing_new_owners_and_wrong_dxf_version'
        with patch.object(win_verify,'pure_core_record',return_value={}):
            result = unittest.TestResult()
            RetirementPackageTests(method).run(result)
            self.assertFalse(result.wasSuccessful())
        restored = unittest.TestResult()
        RetirementPackageTests(method).run(restored)
        self.assertTrue(restored.wasSuccessful(),restored.errors or restored.failures)

    def test_required_probe_bypass_mutants_turn_refusal_tests_red(self):
        cases = ((linux_build,"main","test_linux_build_refuses_before_staging"),
                 (linux_assemble,"validate_native_components","test_linux_assembler_rejects_parser_failure"),
                 (linux_verify,"verify_native_library_abis","test_linux_verifier_executes_adjacent_parser"))
        for module,function,method in cases:
            class Bypass(ast.NodeTransformer):
                def visit_Call(self,node):
                    if isinstance(node.func,ast.Name) and node.func.id == "probe_pure_core_retirement":
                        return ast.copy_location(ast.parse(repr(gate.PURE_CORE_RETIREMENT_IDENTITY),mode="eval").body,node)
                    return self.generic_visit(node)
            tree = ast.fix_missing_locations(Bypass().visit(ast.parse(inspect.getsource(getattr(module,function)))))
            namespace = {}
            exec(compile(tree,"<retirement-bypass-mutant>","exec"),vars(module),namespace)
            with self.subTest(module=module.__name__), patch.object(module,function,namespace[function]), redirect_stdout(io.StringIO()):
                with self.assertRaises(AssertionError): self.assert_entrypoint_refuses(module,method)
            self.assert_entrypoint_refuses(module,method)
        # Native Windows semantic execution is independently load-bearing too.
        tree = ast.parse(inspect.getsource(win_verify.verify_pure_core_identity))
        tree = ast.fix_missing_locations(Bypass().visit(tree))
        namespace = {}
        exec(compile(tree,"<windows-retirement-bypass>","exec"),vars(win_verify),namespace)
        with patch.object(win_verify,"verify_pure_core_identity",namespace["verify_pure_core_identity"]):
            result = unittest.TestResult()
            RetirementPackageTests("test_windows_static_and_native_retirement_refusal").run(result)
            self.assertFalse(result.wasSuccessful(),"Windows retirement bypass survived")
        restored = unittest.TestResult()
        RetirementPackageTests("test_windows_static_and_native_retirement_refusal").run(restored)
        self.assertTrue(restored.wasSuccessful(),restored.errors or restored.failures)


ROUND23_NATIVE_FILES = {
    'native/pure_core/tests/dxf_owned.h',
    'native/pure_core/tests/dxf_owned.rs',
    'native/pure_core/tests/encoder_legacy_abi.rs',
    'native/pure_core/tests/fixtures/dxf_legacy23.txt',
    'native/pure_core/tests/fixtures/dxf_writer23/all-aci.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/capture.json',
    'native/pure_core/tests/fixtures/dxf_writer23/color-sentinel.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/decoder-mixed.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/decoder-no-layers.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/empty.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/key-alias.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/latin1.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/mixed-closed.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/poly-one.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/poly-open.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/poly-zero.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/random-coordinates.bin',
    'native/pure_core/tests/fixtures/dxf_writer23/random-floats.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/short-poly-chain.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/signed-exponents.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/spline-closed.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/unicode-frame.utf8',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Angstroms.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Centimeters.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Decimeters.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Dekameters.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Feet.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Gigameters.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Hectometers.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Inches.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Kilometers.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Meters.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Microinches.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Microns.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Miles.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Millimeters.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Mils.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Nanometers.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-UnknownUnit.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Yards.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-default.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-empty.dxf',
    'native/pure_core/tests/fixtures/encoder_legacy23_provenance.txt',
    'native/pure_core/tests/fixtures/tween_host23.json',
    'native/pure_core/tests/fixtures/tween_lengths23.txt',
    'native/pure_core/tests/fixtures/tween_original23.txt',
    'native/pure_core/tests/fixtures/tween_provenance23.txt',
    'native/pure_core/tests/fixtures/vector23_golden.txt',
    'native/pure_core/tests/fixtures/vector23_provenance.txt',
    'native/pure_core/tests/fixtures/vector23_titles.txt',
    'native/pure_core/tests/fixtures/vector_legacy23.txt',
    'native/pure_core/tests/obj_legacy_abi.rs',
    'native/pure_core/tests/tween_golden.rs',
    'native/pure_core/tests/tween_v1.h',
    'native/pure_core/tests/vector23_api.h',
    'native/pure_core/tests/vector23_golden.rs',
}


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux copied-ELF fixtures; native Windows semantics run in its verifier")
class ActualRound23ProbeTests(unittest.TestCase):
    """Execute the real ABI; malformed libraries are private temporary ELF copies."""
    def setUp(self):
        self.library = Path(os.environ.get('NARWHAL_PURE_CORE',
            ROOT/'native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so'))
        self.assertTrue(self.library.is_file(), 'build a matching qualification candidate first')
        self.original = self.library.read_bytes()
        spec = importlib.util.spec_from_file_location('qualification23_elf_fixture',ROOT/'spike/test_desktop_native_startup.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.entries = module.elf_export_entries(self.original)
        self.temp = tempfile.TemporaryDirectory(prefix='pure-owned23-')
        self.addCleanup(self.temp.cleanup)
        self.copied = Path(self.temp.name)/'core.so'

    def assert_refused_with_parser_intact(self, data):
        self.copied.write_bytes(data)
        self.assertEqual(gate.probe_pure_core_parser(self.copied), gate.PURE_CORE_PARSE_IDENTITY)
        with self.assertRaisesRegex(ValueError,'required retirement'):
            gate.probe_pure_core_retirement(self.copied)

    def test_matching_real_library_has_stable_parser_and_round23_semantics(self):
        self.assertEqual(gate.probe_pure_core_parser(self.library),gate.PURE_CORE_PARSE_IDENTITY)
        self.assertEqual(gate.probe_pure_core_retirement(self.library),gate.PURE_CORE_RETIREMENT_IDENTITY)
        self.assertEqual(gate.PURE_CORE_RETIREMENT_IDENTITY['retirement_abi'],26)
        self.assertEqual(len(gate.PURE_CORE_RETIREMENT_EXPORTS),30)

    def test_stale22_and_each_new_missing_export_refused_with_real_parser(self):
        old = {'stl_size_owned','stl_encode_owned','stl_file_size','stl_file_decode',
               'stl_import_mesh_decode','stl_normal','interpolate_helper_v1','catenary_plan_v2','obj_encode_into'}
        new = list(ROUND23_ADDED_EXPORTS)
        self.assertTrue(set(new) <= set(gate.PURE_CORE_RETIREMENT_EXPORTS))
        self.assertEqual(len(new),13)
        for missing in [[name] for name in new]+[new]:
            with self.subTest(missing=missing):
                data = bytearray(self.original)
                for name in missing:
                    data[self.entries[name][0]+4] &= 0x0f
                self.assert_refused_with_parser_intact(data)

    def test_linux_builder_real_stale_library_refused_before_staging(self):
        data = bytearray(self.original)
        for name in ROUND23_ADDED_EXPORTS:
            data[self.entries[name][0]+4] &= 0x0f
        base = Path(self.temp.name)
        source,output = base/'source',base/'output'
        for path in (source/'native/pure_core',output/'pure_core-target/release',
                     base/'seed',base/'occt',base/'libs'):
            path.mkdir(parents=True,exist_ok=True)
        library = output/'pure_core-target/release/libnarwhal_pure_core.so'
        library.write_bytes(data)
        real_run = subprocess.run
        def run(command,*args,**kwargs):
            if command[0] == 'podman': return SimpleNamespace(returncode=0)
            return real_run(command,*args,**kwargs)
        argv = ['build','--source-root',str(source),'--output',str(output),
                '--seed-target',str(base/'seed'),'--occt-source',str(base/'occt'),
                '--occt-libs',str(base/'libs'),'--crate','pure_core']
        with patch.object(sys,'argv',argv), patch.object(linux_build.subprocess,'run',side_effect=run):
            with self.assertRaisesRegex(ValueError,'required retirement'):
                linux_build.main()
        self.assertFalse((output/'bin/libnarwhal_pure_core.so').exists())
        receipt = json.loads((output/'pure_core-build-receipt.json').read_text())
        self.assertNotIn('pure_core_retirement',receipt)

    def test_no_write_new_semantic_exports_and_wrong_owned_version_refused(self):
        for suffix in ('vector_encode_v1','vector_len_v1','vector_copy_v1','vector_validate_v1',
                       'dxf_owned_colors','dxf_owned_units','dxf_owned_capacity','dxf_owned_encode',
                       'tween_plan_v1','tween_knots_v1','tween_lengths_v1'):
            with self.subTest(export=suffix):
                data = bytearray(self.original)
                _entry,value,size,section = self.entries['narwhal_pure_core_'+suffix]
                self.assertGreaterEqual(size,3)
                self.assertEqual(struct.unpack_from('<H',data,18)[0],62)
                offset = section[4]+value-section[3]
                data[offset:offset+3] = b'\x31\xc0\xc3'
                self.assert_refused_with_parser_intact(data)
        data = bytearray(self.original)
        entry = self.entries['narwhal_pure_core_dxf_owned_abi_version'][0]
        _,value,size,_ = self.entries['narwhal_pure_core_abi_version']
        struct.pack_into('<QQ',data,entry+8,value,size)
        self.assert_refused_with_parser_intact(data)


class Round23ClosureTests(unittest.TestCase):
    def test_new_sources_headers_data_hosts_and_required_exports_are_public(self):
        import source_tools as public
        self.assertTrue(ROUND23_NATIVE_FILES <= set(gate.require_rust_public_closure(ROOT/'native/pure_core')))
        self.assertTrue(ROUND23_NATIVE_FILES <= set(public.NATIVE_FILES))
        self.assertTrue({'dxf_owned','vector23_golden','tween_golden','obj_legacy_abi','encoder_legacy_abi'}
                        <= set(gate.RUST_INTEGRATION_SUITES))
        for host in ('spike/test_dxf_writer_owned.py','spike/test_native_admission_owned.py'):
            self.assertIn(host,gate.PURE_CORE_PYTESTS)
            self.assertIn(host,public.TEST_FILES)
        for module in ('narwhal3d/native_dxf.py','narwhal3d/native_vector_export.py'):
            self.assertIn(module,public.recursive_files('narwhal3d'))
        new = ('vector_encode_v1','vector_len_v1','vector_copy_v1','vector_free_v1','vector_validate_v1',
               'dxf_owned_abi_version','dxf_owned_colors','dxf_owned_units','dxf_owned_capacity','dxf_owned_encode',
               'tween_plan_v1','tween_knots_v1','tween_lengths_v1')
        self.assertEqual(gate.PURE_CORE_RETIREMENT_IDENTITY['retirement_abi'],26)
        for name in new:
            self.assertIn('narwhal_pure_core_'+name,gate.PURE_CORE_RETIREMENT_EXPORTS)

    def test_exact_dxf_capture_audit_preserves_path_hash_and_raw_secret_guards(self):
        import source_tools as audit
        paths = {path for path in ROUND23_NATIVE_FILES if '/dxf_writer23/' in path}
        self.assertEqual(set(audit.FROZEN_DXF23_FILES),paths)
        for path in sorted(paths):
            data = (ROOT/path).read_bytes()
            with self.subTest(path=path):
                self.assertEqual(audit.audit_entries({path:('100644',data)},require_manifest=False),[])
                self.assertTrue(audit.audit_entries({path:('120000',data)},require_manifest=False))
                changed = bytes([data[0]^1])+data[1:]
                problems = audit.audit_entries({path:('100644',changed)},require_manifest=False)
                self.assertTrue(any('provenance hash mismatch' in error for error in problems),problems)
                self.assertNotEqual(audit.frozen_dxf23_scan('unregistered.dxf',data),audit.frozen_dxf23_scan(path,data))
        path = 'native/pure_core/tests/fixtures/dxf_writer23/units-default.dxf'
        data = (ROOT/path).read_bytes()
        self.assertTrue(audit.audit_entries({path+'.unregistered':('100644',data)},require_manifest=False))
        secret = audit.SECRET_MARKERS[0]
        changed = data+secret
        with patch.dict(audit.FROZEN_DXF23_FILES,{path:(len(changed),hashlib.sha256(changed).hexdigest())}):
            errors = audit.audit_entries({path:('100644',changed)},require_manifest=False)
            self.assertTrue(any('possible secret marker' in error for error in errors),errors)

    def test_exact_capture_audit_guard_mutation_is_red_then_green(self):
        import source_tools as audit
        method = 'test_exact_dxf_capture_audit_preserves_path_hash_and_raw_secret_guards'
        with patch.object(audit,'frozen_dxf23_scan',return_value=b'claimed frozen data'):
            result = unittest.TestResult()
            Round23ClosureTests(method).run(result)
            self.assertFalse(result.wasSuccessful())
        restored = unittest.TestResult()
        Round23ClosureTests(method).run(restored)
        self.assertTrue(restored.wasSuccessful(),restored.errors or restored.failures)

    def test_import_fixture_respects_qualified_library_and_root_default(self):
        tree = ast.parse((ROOT/'spike/test_import.py').read_text())
        assignment = next(n for n in tree.body if isinstance(n,ast.Assign)
                          and any(isinstance(t,ast.Name) and t.id=='PURE_LIB' for t in n.targets))
        def require_environment(node):
            code = compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),
                           '<import-library-fixture>','exec')
            scope = {'os':os,'ROOT':'/owned/test-root'}
            with patch.dict(os.environ,{'NARWHAL_PURE_CORE':'/qualified/round23.so'}):
                exec(code,scope)
                self.assertEqual(scope['PURE_LIB'],'/qualified/round23.so')
            with patch.dict(os.environ,clear=True):
                exec(code,scope)
                self.assertEqual(scope['PURE_LIB'],'/owned/test-root/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so')
        require_environment(assignment)
        mutant = ast.parse(ast.unparse(assignment)).body[0]
        self.assertIsInstance(mutant.value,ast.BoolOp)
        mutant.value = mutant.value.values[1]
        with self.assertRaises(AssertionError): require_environment(mutant)
        require_environment(assignment)

    def test_new_closure_checks_are_load_bearing(self):
        method = 'test_new_sources_headers_data_hosts_and_required_exports_are_public'
        for host in ('spike/test_dxf_writer_owned.py','spike/test_native_admission_owned.py'):
            with patch.object(gate,'PURE_CORE_PYTESTS',tuple(p for p in gate.PURE_CORE_PYTESTS if p!=host)):
                result = unittest.TestResult()
                Round23ClosureTests(method).run(result)
                self.assertFalse(result.wasSuccessful())
        for export in ROUND23_ADDED_EXPORTS:
            with patch.object(gate,'PURE_CORE_RETIREMENT_EXPORTS',tuple(e for e in gate.PURE_CORE_RETIREMENT_EXPORTS if e!=export)):
                result = unittest.TestResult()
                Round23ClosureTests(method).run(result)
                self.assertFalse(result.wasSuccessful())
        restored = unittest.TestResult()
        Round23ClosureTests(method).run(restored)
        self.assertTrue(restored.wasSuccessful(),restored.errors or restored.failures)


@unittest.skipUnless(sys.platform.startswith('linux'), 'copied ELF faults; native Windows semantics run in its verifier')
class ActualRound24ProbeTests(unittest.TestCase):
    setUp = ActualRound23ProbeTests.setUp
    assert_refused_with_parser_intact = ActualRound23ProbeTests.assert_refused_with_parser_intact

    def test_stale23_and_each_new_required_export_refused_with_parser_working(self):
        newly_introduced = [name for name in ROUND24_ADDED_EXPORTS if name not in (
            'narwhal_pure_core_ear_clip_display','narwhal_pure_core_control_point_curve_preview')]
        self.assertEqual(len(newly_introduced),4)
        for missing in [[name] for name in ROUND24_ADDED_EXPORTS]+[newly_introduced]:
            with self.subTest(missing=missing):
                data = bytearray(self.original)
                for name in missing: data[self.entries[name][0]+4] &= 0x0f
                self.assert_refused_with_parser_intact(data)

    def test_new_no_write_exports_and_wrong_reader_identity_refused(self):
        for name in ROUND24_ADDED_EXPORTS:
            if name.endswith('abi_version'): continue
            with self.subTest(symbol=name):
                data = bytearray(self.original)
                _entry,value,size,section = self.entries[name]
                self.assertGreaterEqual(size,3)
                self.assertEqual(struct.unpack_from('<H',data,18)[0],62)
                offset = section[4]+value-section[3]
                data[offset:offset+3] = b'\x31\xc0\xc3'
                self.assert_refused_with_parser_intact(data)
        data = bytearray(self.original)
        entry = self.entries['narwhal_pure_core_dxf_reader24_abi_version'][0]
        _,value,size,_ = self.entries['narwhal_pure_core_dxf_owned_abi_version']
        struct.pack_into('<QQ',data,entry+8,value,size)
        self.assert_refused_with_parser_intact(data)

    def test_linux_builder_refuses_real_parser_working_stale23_before_staging(self):
        data = bytearray(self.original)
        for name in ROUND24_ADDED_EXPORTS:
            if name not in ('narwhal_pure_core_ear_clip_display','narwhal_pure_core_control_point_curve_preview'):
                data[self.entries[name][0]+4] &= 0x0f
        base = Path(self.temp.name)
        source,output = base/'source24',base/'output24'
        for path in (source/'native/pure_core',output/'pure_core-target/release',
                     base/'seed24',base/'occt24',base/'libs24'):
            path.mkdir(parents=True,exist_ok=True)
        library = output/'pure_core-target/release/libnarwhal_pure_core.so'
        library.write_bytes(data)
        real_run = subprocess.run
        def run(command,*args,**kwargs):
            return SimpleNamespace(returncode=0) if command[0]=='podman' else real_run(command,*args,**kwargs)
        argv = ['build','--source-root',str(source),'--output',str(output),
                '--seed-target',str(base/'seed24'),'--occt-source',str(base/'occt24'),
                '--occt-libs',str(base/'libs24'),'--crate','pure_core']
        with patch.object(sys,'argv',argv), patch.object(linux_build.subprocess,'run',side_effect=run):
            with self.assertRaisesRegex(ValueError,'required retirement'): linux_build.main()
        self.assertFalse((output/'bin/libnarwhal_pure_core.so').exists())


class Round24ClosureTests(unittest.TestCase):
    def test_new_header_data_producer_and_host_closure_is_real(self):
        import source_tools as public
        self.assertTrue(ROUND24_NATIVE_FILES <= set(gate.require_rust_public_closure(ROOT/'native/pure_core')))
        self.assertTrue(ROUND24_NATIVE_FILES | ROUND24_MODULES <= set(public.NATIVE_FILES))
        self.assertTrue(ROUND24_MODULES <= set(gate.RUST_REQUIRED_MODULE_FILES))
        self.assertTrue({'earclip24_golden','preview24_golden','dxf_reader24'} <= set(gate.RUST_INTEGRATION_SUITES))
        for host in ('spike/test_preview24_boundaries.py','spike/test_dxf_reader24_boundaries.py'):
            self.assertIn(host,gate.PURE_CORE_PYTESTS)
            self.assertIn(host,public.TEST_FILES)
        for support in ('spike/dxf_reader24_fixtures.py',):
            self.assertIn(support,public.TEST_FILES)
        for module in ('narwhal3d/native_preview.py','narwhal3d/native_dxf_reader.py','narwhal3d/dxf_reader.py'):
            self.assertIn(module,public.recursive_files('narwhal3d'))
        self.assertTrue(set(ROUND23_ADDED_EXPORTS + ROUND24_ADDED_EXPORTS) <= set(gate.PURE_CORE_RETIREMENT_EXPORTS))
        self.assertEqual(gate.PURE_CORE_RETIREMENT_IDENTITY['retirement_abi'],26)
        self.assertEqual(len(gate.PURE_CORE_RETIREMENT_EXPORTS),30)

    def test_new_configuration_guards_are_load_bearing(self):
        method = 'test_new_header_data_producer_and_host_closure_is_real'
        mutants = [patch.object(gate,'PURE_CORE_RETIREMENT_EXPORTS',tuple(
                       name for name in gate.PURE_CORE_RETIREMENT_EXPORTS if name!=missing))
                   for missing in ROUND24_ADDED_EXPORTS]
        mutants += [patch.object(gate,'PURE_CORE_PYTESTS',tuple(
                        name for name in gate.PURE_CORE_PYTESTS if name!=missing))
                    for missing in ('spike/test_preview24_boundaries.py','spike/test_dxf_reader24_boundaries.py')]
        import source_tools as public
        recursive = public.recursive_files
        mutants += [patch.object(public,'TEST_FILES',tuple(
                        name for name in public.TEST_FILES if name!='spike/dxf_reader24_fixtures.py')),
                    patch.object(public,'recursive_files',side_effect=lambda *args: [
                        name for name in recursive(*args) if name!='narwhal3d/dxf_reader.py']),
                    patch.object(gate,'RUST_REQUIRED_MODULE_FILES',()),
                    patch.dict(gate.PURE_CORE_RETIREMENT_IDENTITY,{'retirement_abi':23})]
        for mutant in mutants:
            with mutant:
                broken = unittest.TestResult()
                Round24ClosureTests(method).run(broken)
                self.assertFalse(broken.wasSuccessful())
        restored = unittest.TestResult()
        Round24ClosureTests(method).run(restored)
        self.assertTrue(restored.wasSuccessful(),restored.errors or restored.failures)


class DxfLegacyClosureTests(unittest.TestCase):
    def test_legacy_decoder_coverage_is_in_rust_and_public(self):
        import source_tools as public
        paths = {
            "native/pure_core/tests/dxf_legacy_abi.rs",
            "native/pure_core/tests/dxf_legacy_abi.h",
            "native/pure_core/tests/fixtures/dxf_legacy_abi/README.md",
            "native/pure_core/tests/fixtures/dxf_legacy_abi/analytic.dxf",
            "native/pure_core/tests/fixtures/dxf_legacy_abi/analytic.packed",
            "native/pure_core/tests/fixtures/dxf_legacy_abi/empty.packed",
            "native/pure_core/tests/fixtures/dxf_legacy_abi/specification.json",
        }
        self.assertTrue(paths <= set(gate.require_rust_public_closure(ROOT / "native/pure_core")))
        self.assertTrue(paths <= set(public.NATIVE_FILES))
        self.assertIn("dxf_legacy_abi", gate.RUST_INTEGRATION_SUITES)

    def test_legacy_decoder_suite_omission_is_red_then_green(self):
        method = "test_legacy_decoder_coverage_is_in_rust_and_public"
        with patch.object(gate, "RUST_INTEGRATION_SUITES", tuple(
                name for name in gate.RUST_INTEGRATION_SUITES if name != "dxf_legacy_abi")):
            broken = unittest.TestResult()
            DxfLegacyClosureTests(method).run(broken)
            self.assertFalse(broken.wasSuccessful(), "legacy decoder suite omission survived")
        restored = unittest.TestResult()
        DxfLegacyClosureTests(method).run(restored)
        self.assertTrue(restored.wasSuccessful(), restored.errors or restored.failures)


class GuardMutationTests(unittest.TestCase):
    def test_round20_host_retention_check_goes_red_when_manifest_is_trimmed(self):
        method = "test_round20_public_closure_retains_host_suites"
        for host in ("spike/test_native_nearest.py", "spike/test_native_display_samples.py",
                     "spike/test_native_curve_samples.py", "spike/test_native_arclength.py",
                     "spike/test_coil_owned.py", "spike/test_interpolate.py",
                     "spike/test_interpolate_owned_boundary.py", "spike/test_stl_import_boundary.py",
                     "spike/test_catenary.py", "spike/test_catenary_owned.py",
                     "spike/test_native_obj.py", "spike/test_obj_writer.py",
                     "spike/test_dxf_writer_owned.py", "spike/test_native_admission_owned.py"):
            with self.subTest(host=host), patch.object(
                gate, "PURE_CORE_PYTESTS", tuple(p for p in gate.PURE_CORE_PYTESTS if p != host)
            ):
                result = unittest.TestResult()
                PackageTests(method).run(result)
                self.assertFalse(result.wasSuccessful(), "host omission survived: " + host)
        restored = unittest.TestResult()
        PackageTests(method).run(restored)
        self.assertTrue(restored.wasSuccessful(), restored.errors or restored.failures)

    def test_round22_required_boundary_and_fixed_capture_guards_go_red(self):
        import source_tools as public
        method = "test_round22_required_obj_adapter_and_host_routes_are_public"
        cases = [(patch.object(gate, "PURE_CORE_RETIREMENT_EXPORTS", tuple(
                    e for e in gate.PURE_CORE_RETIREMENT_EXPORTS if e != missing)), method)
                 for missing in ("narwhal_pure_core_catenary_plan_v2", "narwhal_pure_core_obj_encode_into")]
        cases.extend([
            (patch.dict(gate.PURE_CORE_RETIREMENT_IDENTITY, {"retirement_abi":22}), method),
            (patch.object(public,"recursive_files",return_value=[]), method),
            (patch.object(gate,"PURE_CORE_PYTESTS",tuple(
                f for f in gate.PURE_CORE_PYTESTS if f != "spike/test_catenary_owned.py")), method),
        ])
        inventory = linux_build.inventory
        cases.append((patch.object(linux_build,"inventory",side_effect=lambda *args: {
            k:v for k,v in inventory(*args).items() if not k.endswith(".bin")}),
            "test_linux_build_inventory_tracks_new_header_and_capture_bytes"))
        original_read = Path.read_bytes
        capture = ROOT / "native/pure_core/tests/fixtures/stl_prefix_headers.bin"
        data = capture.read_bytes()
        def damaged(path):
            return bytes([data[0]^1])+data[1:] if path == capture else original_read(path)
        cases.append((patch.object(Path,"read_bytes",damaged),
                      "test_independent_binary_stl_capture_needs_no_public_audit_exception"))
        for mutant, method in cases:
            with mutant:
                result = unittest.TestResult()
                PackageTests(method).run(result)
                self.assertFalse(result.wasSuccessful(), "round22 qualification guard mutation survived")
            restored = unittest.TestResult()
            PackageTests(method).run(restored)
            self.assertTrue(restored.wasSuccessful(), restored.errors or restored.failures)

    def test_windows_additive_export_admission_is_load_bearing(self):
        original=win_verify.verify_pure_core_identity
        source=inspect.getsource(original)
        old='        if identity["exports"].count(name) != 1:'
        self.assertEqual(source.count(old),1)
        namespace={}
        exec(compile(source.replace(old,'        if False:',1),'<windows-additive-export-mutant>','exec'),
             vars(win_verify),namespace)
        with patch.object(win_verify,'verify_pure_core_identity',namespace['verify_pure_core_identity']):
            red=unittest.TestResult()
            PackageTests('test_windows_pe_missing_exports_and_wrong_parser_abi').run(red)
            self.assertTrue(red.failures,'omitted additive export admission must fail the actual PE refusal test')
            self.assertFalse(red.errors)
        green=unittest.TestResult()
        PackageTests('test_windows_pe_missing_exports_and_wrong_parser_abi').run(green)
        self.assertTrue(green.wasSuccessful(),green.errors or green.failures)

    def test_packaging_tests_go_red_when_parser_probes_are_bypassed(self):
        # Mutate copied function ASTs in memory: no live source/bytecode writes.
        cases = (
            (linux_build, "main", "probe_pure_core_parser", PackageTests, "test_linux_build_refuses_before_staging"),
            (linux_assemble, "validate_native_components", "probe_pure_core_parser", PackageTests, "test_linux_assembler_rejects_parser_failure"),
            (linux_verify, "verify_native_library_abis", "probe_pure_core_parser", PackageTests, "test_linux_verifier_executes_adjacent_parser"),
            (mac_assemble, "verify_pure_core", "probe_pure_core_parser", PackageTests, "test_macos_package_executes_runtime_parser"),
            (win_verify, "verify_pure_core_identity", "probe_pure_core_parser", PackageTests, "test_windows_static_identity_and_native_execution"),
        )
        for module, function, probe, case, method in cases:
            with self.subTest(function=module.__name__ + "." + function):
                class Bypass(ast.NodeTransformer):
                    def visit_Call(self, node):
                        if isinstance(node.func, ast.Name) and node.func.id == probe:
                            return ast.copy_location(ast.parse(repr(gate.PURE_CORE_PARSE_IDENTITY), mode="eval").body, node)
                        return self.generic_visit(node)
                original = getattr(module, function)
                tree = ast.fix_missing_locations(Bypass().visit(ast.parse(inspect.getsource(original))))
                namespace = {}
                exec(compile(tree, "<packaging-guard-mutant>", "exec"), vars(module), namespace)
                with patch.object(module, function, namespace[function]), redirect_stdout(io.StringIO()):
                    result = unittest.TestResult()
                    case(method).run(result)
                    self.assertFalse(result.wasSuccessful(), "mutant survived: " + function)
                restored = unittest.TestResult()
                case(method).run(restored)
                self.assertTrue(restored.wasSuccessful(), restored.errors or restored.failures)


class JointAdmissionTests(unittest.TestCase):
    def test_source_closure_and_all_package_entrypoints_require_joint(self):
        name = "native/pure_core/src/remove_join_cv.rs"
        self.assertIn(name, gate.RUST_REQUIRED_MODULE_FILES)
        import source_tools
        self.assertIn(name, source_tools.NATIVE_FILES)
        self.assertIn(name, source_tools.REQUIRED)
        for module, function in ((linux_build, "main"),
                                 (linux_assemble, "validate_native_components"),
                                 (linux_verify, "verify_native_library_abis")):
            tree = ast.parse(inspect.getsource(getattr(module, function)))
            calls = [node.func.id for node in ast.walk(tree)
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
            self.assertIn("probe_pure_core_remove_join_cv", calls)
            self.assertIn("probe_pure_core_polycurve_samples", calls)
            for probe in ("probe_pure_core_offset_fit", "probe_pure_core_surface_offset", "probe_pure_core_surface_grid", "probe_pure_core_surface_display", "probe_pure_core_dxf_approx", "probe_pure_core_planar_area"):
                self.assertIn(probe, calls)

    def test_actual_joint_probe_retains_data_and_refusals(self):
        library = Path(os.environ.get("NARWHAL_PURE_CORE") or
                       ROOT / "narwhal3d/native/libnarwhal_pure_core.so")
        self.assertTrue(gate.probe_pure_core_remove_join_cv(library)["retained_data"])
        self.assertTrue(gate.probe_pure_core_remove_join_cv(library)["refusals_no_write"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
