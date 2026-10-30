#!/usr/bin/env python3
"""Focused host tests of build orchestration; not macOS runtime acceptance."""
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("runtime", Path(__file__).with_name("build_macos_runtime.py"))
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
nspec = importlib.util.spec_from_file_location("native", Path(__file__).with_name("build_macos_native.py"))
native = importlib.util.module_from_spec(nspec)
nspec.loader.exec_module(native)


class RuntimeTests(unittest.TestCase):
    def test_mac_helpers_include_all_eleven_current_components(self):
        actual = {name for names in native.COMPONENTS.values() for name in names}
        self.assertEqual(actual, {
            "libnarwhal_pure_core.dylib", "libnarwhal_selection_core.dylib",
            "libnarwhal_cdr_core.dylib", "libnarwhal_platform_core.dylib",
            "libnarwhal_geometry_core.dylib", "libnarwhal_automation.dylib",
            "narwhal-document-core", "narwhal-converter-supervisor",
            "narwhal-cdr-multiversion", "narwhal-automation", "narwhal-welcome",
        })

    def test_xcode_requirement_is_checked_before_expensive_work(self):
        runtime.require_xcode("Xcode 16.2\nBuild version 16C5032a")
        for bad in ("Xcode 15.4\nBuild version 15F31d", "unknown"):
            with self.assertRaises(ValueError):
                runtime.require_xcode(bad)

    def test_authoritative_remote_includes_the_required_lfs_store(self):
        self.assertEqual(runtime.BLENDER_REMOTE, "https://projects.blender.org/blender/blender.git")

    def test_every_runtime_input_is_bound_and_missing_inputs_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "blender_patches"
            inputs.mkdir()
            for name in runtime.PATCHES:
                (inputs / name).write_text(name)
            before = runtime.inputs(root)
            self.assertEqual(len(before), 8)
            artwork = inputs / "narwhal3d_window_icons.json"
            artwork.write_text("changed artwork")
            self.assertNotEqual(before, runtime.inputs(root))
            artwork.unlink()
            with self.assertRaises(ValueError):
                runtime.inputs(root)
            artwork.symlink_to(inputs / "narwhal3d_app_icon.svg")
            with self.assertRaises(ValueError):
                runtime.inputs(root)

    def test_every_patch_runs_with_its_correct_source_contract(self):
        calls = []
        def module(spec):
            name = Path(spec.origin).name
            obj = SimpleNamespace()
            obj.apply = lambda *args: calls.append((name, args))
            obj.main = lambda: calls.append((name, (obj.SRC,)))
            return obj
        def spec(name, path):
            return SimpleNamespace(origin=str(path), loader=SimpleNamespace(exec_module=lambda _: None))
        with patch.object(runtime.importlib.util, "spec_from_file_location", spec), \
             patch.object(runtime.importlib.util, "module_from_spec", module):
            runtime.patch(Path("/source"), Path("/runtime"))
        self.assertEqual([name for name, _ in calls], list(runtime.PATCHES[:6]))
        self.assertEqual(calls[0][1], ("/runtime", "/source/blender_patches/narwhal3d_app_icon.svg"))
        for _, args in calls[1:4]:
            self.assertEqual(args, ("/runtime/source/blender",))
        for _, args in calls[4:]:
            self.assertEqual(args, (Path("/runtime/source/blender"),))

    def test_native_host_is_required_before_any_work(self):
        with patch("sys.argv", ["build", "--work", "/unused"]), \
             patch.object(runtime.platform, "system", return_value="Linux"), \
             patch.object(runtime, "run") as run:
            with self.assertRaises(SystemExit):
                runtime.main()
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
