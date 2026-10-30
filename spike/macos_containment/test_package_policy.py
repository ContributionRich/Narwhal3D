#!/usr/bin/env python3
"""Mac package component policy: only the explicit Corel deferral is permitted."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import build_macos_native as build

class PolicyTests(unittest.TestCase):
    def test_reuse_checks_native_source_binaries_and_kernel_pins(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            (root / "native").mkdir(parents=True)
            (root / "launch").mkdir()
            (root / "launch/narwhal3d.png").write_bytes(b"artwork")
            output = Path(temporary) / "output"
            (output / "bin/occt").mkdir(parents=True)
            library = output / "bin/occt/kernel.dylib"
            library.write_bytes(b"kernel fixture")
            components = {}
            for names in build.COMPONENTS.values():
                for name in names:
                    file = output / "bin" / name
                    file.write_bytes(name.encode())
                    components[name] = {"sha256": build.sha(file), "architecture": "arm64"}
            receipt = dict(source_inputs=build.source_inputs(root), components=components,
                           unavailable_components=build.UNAVAILABLE_COMPONENTS,
                           occt_source=build.OCCT, occt_wheel_sha256=build.WHEEL_SHA,
                           occt_libraries={library.name: build.sha(library)})
            (output / "native-build.json").write_text(json.dumps(receipt))
            self.assertTrue(build.reuse_output(root, output))
            library.write_bytes(b"modified")
            self.assertFalse(build.reuse_output(root, output))
            library.write_bytes(b"kernel fixture")
            (root / "native/new.rs").write_text("new source")
            self.assertFalse(build.reuse_output(root, output))

    def test_numpy_replacement_keeps_version_and_supported_os(self):
        import json
        root = Path(__file__).resolve().parents[2]
        lock = json.loads((root / "packaging/macos/arm64-wheels.lock.json").read_text())
        numpy = next(a for a in lock["artifacts"] if a["name"] == "numpy")
        self.assertEqual(numpy["version"], "2.3.4")
        self.assertIn("cp313-cp313-macosx_14_0_arm64", numpy["filename"])
        self.assertEqual(numpy["sha256"], "3634093d0b428e6c32c3a69b78e554f0cd20ee420dcad5a9f3b2a63762ce4197")

    def test_macho_os_gate_rejects_newer_dependencies(self):
        import assemble_macos_release as assemble
        from unittest.mock import patch
        for minimum in ("14.0", "26.0"):
            def tool(*args):
                if "-l" in args: return "cmd LC_BUILD_VERSION\n    minos " + minimum
                if "-L" in args: return "module:\n/usr/lib/libSystem.B.dylib (compatibility version 1)"
                return ""
            with patch.object(assemble, "run", tool):
                if minimum == "26.0":
                    with self.assertRaisesRegex(ValueError, "requires macOS 26.0"):
                        assemble.verify_macho(Path("extension.so"))
                else:
                    assemble.verify_macho(Path("extension.so"))

    def test_failed_platform_tool_keeps_its_diagnostic(self):
        import assemble_macos_release as assemble
        with self.assertRaisesRegex(RuntimeError, "explicit tool failure"):
            assemble.run(sys.executable, "-c", "import sys; print('explicit tool failure', file=sys.stderr); sys.exit(7)")

    def test_worker_policy_is_mandatory(self):
        self.assertIn("libnarwhal_worker_policy.dylib", build.COMPONENTS["converter_supervisor"])
        expected = {name for names in build.COMPONENTS.values() for name in names}
        receipts = {name: {} for name in expected}
        build.require_components(receipts, {})
        del receipts["libnarwhal_worker_policy.dylib"]
        with self.assertRaisesRegex(ValueError, "missing components"):
            build.require_components(receipts, {})

    def test_only_named_corel_exception_is_allowed(self):
        self.assertEqual(set(build.UNAVAILABLE_COMPONENTS), {"cdr_multiversion"})
        self.assertNotIn("cdr_multiversion", build.COMPONENTS)
        self.assertTrue(build.UNAVAILABLE_COMPONENTS["cdr_multiversion"].startswith("Standalone Corel"))

    def test_failed_component_cannot_be_certified(self):
        receipts = {name: {} for names in build.COMPONENTS.values() for name in names}
        with self.assertRaisesRegex(ValueError, "geometry_core"):
            build.require_components(receipts, {"geometry_core": "failed"})

if __name__ == "__main__":
    unittest.main()
