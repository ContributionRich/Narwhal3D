#!/usr/bin/env python3
"""Mac package component policy: only the explicit Corel deferral is permitted."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import build_macos_native as build

class PolicyTests(unittest.TestCase):
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
