#!/usr/bin/env python3
"""Packaging/loader contract tests. Native execution is qualified separately."""
import ast
import ctypes
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
             and node.name in ("WorkflowUnavailable", "_load_workflow_library")]
    namespace = {"__file__": str(path)}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace["_load_workflow_library"]


class AutomationPackaging(unittest.TestCase):
    def test_builder_produces_every_required_native_component(self):
        self.assertEqual(set(linux.NATIVE_COMPONENTS),
                         {name for names in builder.COMPONENTS.values() for name in names})

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
            library = types.SimpleNamespace(narwhal_workflow_abi=abi, narwhal_workflow_json=exchange)
            with self.subTest(platform=platform), patch.object(sys, "platform", platform), \
                 patch("platform.machine", return_value=machine), \
                 patch.object(Path, "is_file", lambda p: p == expected), \
                 patch.object(ctypes, "CDLL", return_value=library) as cdll:
                self.assertIs(load(), library)
                cdll.assert_called_once_with(str(expected))
                self.assertIs(abi.restype, ctypes.c_uint32)

    def test_bad_abi_remains_unavailable(self):
        load = resolver()
        library = types.SimpleNamespace(narwhal_workflow_abi=lambda: 99)
        with patch.object(ctypes, "CDLL", return_value=library):
            with self.assertRaisesRegex(RuntimeError, "native_workflow_unavailable"):
                load("explicit-test-library")


if __name__ == "__main__":
    unittest.main()
