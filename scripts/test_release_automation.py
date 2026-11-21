#!/usr/bin/env python3
"""Packaging/loader contract tests. Native execution is qualified separately."""
import ast
import ctypes
import json
import tempfile
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import assemble_linux_release as linux
import verify_linux_release as linux_verify
import verify_windows_release as windows
import build_linux_native as builder


def resolver():
    path = ROOT / "narwhal3d/ai_assistant.py"
    tree = ast.parse(path.read_text())
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))
             and node.name in ("WorkflowUnavailable", "_refresh_workflow_library", "_load_workflow_library")]
    namespace = {"__file__": str(path), "_workflow_library_refreshes": {}}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace["_load_workflow_library"]


class AutomationPackaging(unittest.TestCase):
    def test_builder_produces_every_required_native_component(self):
        self.assertEqual(set(linux.NATIVE_COMPONENTS),
                         {name for names in builder.COMPONENTS.values() for name in names})

    def test_windows_geometry_exports_cover_current_surfacing_abi(self):
        self.assertTrue({"narwhal_geometry_core_prepare_freeform_v1",
                         "narwhal_geometry_core_sweep1_freeform_approx_v1",
                         "narwhal_geometry_core_sweep1_parallel_v1"}
                        <= set(windows.GEOMETRY_CORE_EXPORTS))
        self.assertEqual(len(windows.GEOMETRY_CORE_EXPORTS), 15)
        self.assertEqual(tuple(windows.GEOMETRY_CORE_EXPORTS), builder.GEOMETRY_CORE_EXPORTS)
        self.assertTrue({"narwhal_geometry_core_join_exact_v1",
                         "narwhal_geometry_core_join_mapped_v1",
                         "narwhal_geometry_core_verify_join_v1"} <= set(windows.GEOMETRY_CORE_EXPORTS))

    def test_geometry_join_probe_refuses_missing_capability_exports_and_semantics(self):
        semantics = dict(suite="exact Join startup", exact_faces=3, closed=True,
                         operand_count=3, recipe_verified=True)
        good = dict(abi=4, capabilities=1023, exports=list(builder.GEOMETRY_CORE_EXPORTS),
                    join_semantics=semantics)
        with patch.object(builder.subprocess, "run", return_value=types.SimpleNamespace(stdout=json.dumps(good))) as run:
            self.assertEqual(builder.probe_geometry_core_join(Path("candidate.so"), ROOT), good)
            self.assertIn("check_geometry_consumers", run.call_args.args[0][3])
        variants = [dict(good, capabilities=511), dict(good, exports=good["exports"][:-1]),
                    dict(good, join_semantics=None),
                    dict(good, join_semantics=dict(semantics, exact_faces=4)),
                    dict(good, join_semantics=dict(semantics, closed=False)),
                    dict(good, join_semantics=dict(semantics, recipe_verified=False))]
        for invalid in variants:
            with self.subTest(invalid=invalid), patch.object(builder.subprocess, "run",
                    return_value=types.SimpleNamespace(stdout=json.dumps(invalid))):
                with self.assertRaises(ValueError):
                    builder.probe_geometry_core_join(Path("candidate.so"), ROOT)
        with patch.object(builder.subprocess, "run", return_value=types.SimpleNamespace(stdout=json.dumps(good))):
            self.assertEqual(builder.probe_geometry_core_join(Path("candidate.so"), ROOT), good)

    def test_geometry_source_receipt_binds_native_include_and_join_probe(self):
        with tempfile.TemporaryDirectory(prefix="geometry-source-receipt-") as folder:
            root = Path(folder)
            crate = root / "native/geometry_core"
            (crate / "src").mkdir(parents=True)
            (crate / "cpp").mkdir()
            for name in builder.GEOMETRY_CORE_REQUIRED_SOURCE:
                path = crate / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("source closure fixture")
            source, include = crate / "src/join.rs", crate / "cpp/join_adapter.inc"
            source.write_text("exact join source")
            include.write_text("exact join adapter")
            binary = root / "libnarwhal_geometry_core.so"
            binary.write_bytes(b"qualified synthetic identity")
            probe = dict(abi=4, capabilities=1023, exports=list(builder.GEOMETRY_CORE_EXPORTS),
                join_semantics=dict(suite="exact Join startup", exact_faces=3, closed=True,
                                    operand_count=3, recipe_verified=True))
            record = dict(inputs=builder.inventory(root, "geometry_core"),
                          sha256=builder.digest(binary), geometry_core=probe)
            receipt = root / builder.GEOMETRY_CORE_SOURCE
            receipt.write_text(json.dumps(record))
            self.assertEqual(builder.verify_geometry_core_source(root, root), record)
            source.write_text("changed producer")
            with self.assertRaisesRegex(ValueError, "source/binary"):
                builder.verify_geometry_core_source(root, root)
            source.write_text("exact join source")
            include.unlink()
            with self.assertRaisesRegex(ValueError, "missing geometry-core source"):
                builder.verify_geometry_core_source(root, root)
            include.write_text("exact join adapter")
            receipt.write_text(json.dumps(dict(record, geometry_core=dict(probe, capabilities=511))))
            with self.assertRaisesRegex(ValueError, "Join qualification"):
                builder.verify_geometry_core_source(root, root)
            receipt.write_text(json.dumps(record))
            self.assertEqual(builder.verify_geometry_core_source(root, root), record)

    def test_geometry_manifest_requires_current_sources_and_join_semantics(self):
        record = dict(source_inputs=builder.geometry_source_inventory(ROOT),
            geometry_core=dict(abi=4, capabilities=1023, exports=list(builder.GEOMETRY_CORE_EXPORTS),
                join_semantics=dict(suite="exact Join startup", exact_faces=3, closed=True,
                                    operand_count=3, recipe_verified=True)))
        linux_verify.verify_geometry_core_manifest({"libnarwhal_geometry_core.so": record})
        for key in ("source_inputs", "geometry_core"):
            damaged = dict(record)
            del damaged[key]
            with self.assertRaises(linux_verify.VerificationError):
                linux_verify.verify_geometry_core_manifest({"libnarwhal_geometry_core.so": damaged})
        linux_verify.verify_geometry_core_manifest({"libnarwhal_geometry_core.so": record})

    def test_windows_automation_exports_are_required(self):
        good = {"machine": 0x8664, "exports": ["narwhal_workflow_abi", "narwhal_workflow_json"]}
        with patch.object(windows, "inspect_pe_dll", return_value=good):
            self.assertEqual(windows.verify_automation_core_identity(Path("fixture.dll")), good)
        for exports in (["narwhal_workflow_abi"], ["narwhal_workflow_json"], []):
            with patch.object(windows, "inspect_pe_dll", return_value=dict(good, exports=exports)):
                with self.assertRaises(windows.VerificationError):
                    windows.verify_automation_core_identity(Path("fixture.dll"))

    def test_manifest_sets_include_library_and_unix_server(self):
        for module in (linux, linux_verify):
            self.assertIn("libnarwhal_automation.so", module.NATIVE_COMPONENTS)
            self.assertIn("narwhal-automation", module.NATIVE_COMPONENTS)
            self.assertEqual(module.NATIVE_LIBRARY_ABIS["libnarwhal_automation.so"], ("narwhal_workflow_abi", 1))
        self.assertEqual(linux.NATIVE_COMPONENTS, linux_verify.NATIVE_COMPONENTS)
        self.assertEqual(windows.NATIVE_COMPONENT_PATHS["narwhal_automation.dll"],
                         "app/runtime/windows-x86_64/bin/narwhal_automation.dll")
        self.assertNotIn("narwhal-automation.exe", windows.NATIVE_COMPONENT_PATHS)

    def test_installed_libraries_resolve_on_all_three_platforms(self):
        load = resolver()
        for platform, machine, system, arch, name in (
            ("linux", "x86_64", "linux", "x86_64", "libnarwhal_automation.so"),
            ("darwin", "arm64", "macos", "arm64", "libnarwhal_automation.dylib"),
            ("win32", "AMD64", "windows", "x86_64", "narwhal_automation.dll"),
        ):
            expected = ROOT / "runtime" / f"{system}-{arch}" / "bin" / name
            abi = lambda: 1
            exchange = lambda: None
            revision = lambda: 9
            library = types.SimpleNamespace(narwhal_workflow_abi=abi, narwhal_workflow_json=exchange, narwhal_workflow_api_revision=revision)
            with self.subTest(platform=platform), patch.object(sys, "platform", platform), \
                 patch("platform.machine", return_value=machine), \
                 patch.object(Path, "is_file", lambda p: p == expected), \
                 patch.object(ctypes, "CDLL", return_value=library) as cdll:
                self.assertIs(load(), library)
                cdll.assert_called_once_with(str(expected))
                self.assertIs(abi.restype, ctypes.c_uint32)

    def test_missing_or_old_json_contract_requires_update(self):
        load = resolver()
        for version in (None, 8):
            library = types.SimpleNamespace(narwhal_workflow_abi=lambda: 1)
            if version is not None:
                library.narwhal_workflow_api_revision = lambda: version
            with self.subTest(version=version), patch.object(sys, "platform", "win32"), \
                 patch.object(ctypes, "CDLL", return_value=library):
                with self.assertRaisesRegex(RuntimeError, "native_modeling_update_required"):
                    load("explicit-test-library")

    def test_bad_abi_remains_unavailable(self):
        load = resolver()
        library = types.SimpleNamespace(narwhal_workflow_abi=lambda: 99)
        with patch.object(ctypes, "CDLL", return_value=library):
            with self.assertRaisesRegex(RuntimeError, "native_workflow_unavailable"):
                load("explicit-test-library")


if __name__ == "__main__":
    unittest.main()
