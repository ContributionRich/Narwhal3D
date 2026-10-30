#!/usr/bin/env python3
"""Unregistered Rust experiments are not automatically design-bundle/public inputs."""
import importlib.util
import inspect
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("review_bundle", ROOT / "scripts/build_ui_bundle.py")
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)

class BundleTests(unittest.TestCase):
    def test_drafts_are_excluded_without_losing_reviewed_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, text in {
                "scripts/create_public_branch.py": 'NATIVE_FILES=("native/pure_core/Cargo.toml","native/pure_core/src/lib.rs")\nWELCOME_FILES=()\n',
                "native/pure_core/Cargo.toml": "[package]\n",
                "native/pure_core/src/lib.rs": "// reviewed source\n",
                "native/pure_core/src/draft.rs": "// not registered, not reviewed\n",
            }.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            with patch.object(bundle, "ENTRY_POINTS", ()), patch.object(bundle, "SURFACES", {}), \
                 patch.object(bundle, "EXTRA_SOURCE", ("scripts/create_public_branch.py",)), \
                 patch.object(bundle, "NATIVE_ROOTS", ("native/pure_core",)):
                sources = bundle.source_inventory(root)
            self.assertIn("native/pure_core/src/lib.rs", sources)
            self.assertNotIn("native/pure_core/src/draft.rs", sources)

    def test_removed_filter_fails(self):
        source = inspect.getsource(bundle.source_inventory)
        old = "            if rel not in native_public:\n                continue\n"
        self.assertEqual(source.count(old), 1)
        with patch.dict(bundle.__dict__):
            exec(source.replace(old, ""), bundle.__dict__)
            result = unittest.TestResult()
            BundleTests("test_drafts_are_excluded_without_losing_reviewed_sources").run(result)
            self.assertFalse(result.wasSuccessful())
        result = unittest.TestResult()
        BundleTests("test_drafts_are_excluded_without_losing_reviewed_sources").run(result)
        self.assertTrue(result.wasSuccessful())

if __name__ == "__main__":
    unittest.main()
