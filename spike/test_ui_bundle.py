#!/usr/bin/env python3
"""Focused bundle tests. No Blender, GUI, product-source edits or final dist build.
Python tests exercise the existing Python packaging/Blender orchestration boundary.
Mutation cases corrupt isolated fixtures, assert RED, then restore and assert GREEN.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ui_bundle_builder", ROOT / "scripts/build_ui_bundle.py")
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


def require_key(case, name, inventory):
    # unittest.assertIn formats the whole mapping on failure, including binary
    # assets and source bytes. Never render inventory values in diagnostics.
    case.assertTrue(name in inventory, f"required bundle entry missing: {name}")


class DiagnosticTests(unittest.TestCase):
    def test_builder_caps_entire_service_lifetime(self):
        with patch.object(sys, "argv", ["build_ui_bundle.py"]), \
                patch.object(b.subprocess, "run") as launch:
            launch.return_value.returncode = 0
            with self.assertRaises(SystemExit):
                b.main()
        command = launch.call_args.args[0]
        for required in ("MemoryMax=3G", "MemorySwapMax=0", "TasksMax=128",
                         "RuntimeMaxSec=330", "TimeoutStopSec=3", "KillMode=control-group"):
            self.assertIn(required, command)
        self.assertEqual(launch.call_args.kwargs["timeout"], 360)

    def test_missing_entry_never_formats_inventory(self):
        class UnprintableInventory(dict):
            formatted = False

            def __repr__(self):
                self.formatted = True
                raise RuntimeError("inventory repr must not run")
        inventory = UnprintableInventory({"present": b"fixture"})
        require_key(self, "present", inventory)
        with self.assertRaises(AssertionError) as failure:
            require_key(self, "missing.png", inventory)
        self.assertFalse(inventory.formatted, "inventory repr must not run")
        self.assertIn("missing.png", str(failure.exception))
        self.assertLess(len(str(failure.exception)), 200)


class BundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = b.source_inventory()
        cls.assets = b.asset_inventory()

    def fixture(self, stage):
        files = {
            "index.html": b'<link href="playground/style.css"><script src="playground/app.js"></script>',
            "playground/app.js": b'"use strict";',
            "playground/style.css": b'body { color: black; }',
            "source/narwhal3d/ai_settings_popup.py": self.sources["narwhal3d/ai_settings_popup.py"],
            "assets/icons/v4-panel-ai-assistant.png": self.assets["narwhal3d/icons/v4-panel-ai-assistant.png"],
            "data/ui_data.json": json.dumps({"inspector_tabs": [{"icon": "v4-panel-ai-assistant.png"}]}).encode(),
            "data/coverage.json": json.dumps({"surfaces": {"AI settings": ["source/narwhal3d/ai_settings_popup.py"]}}).encode(),
        }
        for path, data in files.items():
            target = stage / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        inputs = {
            "narwhal3d/ai_settings_popup.py": {"bundle_path": "source/narwhal3d/ai_settings_popup.py",
                "sha256": b.digest(files["source/narwhal3d/ai_settings_popup.py"]),
                "bytes": len(files["source/narwhal3d/ai_settings_popup.py"])},
            "narwhal3d/icons/v4-panel-ai-assistant.png": {"bundle_path": "assets/icons/v4-panel-ai-assistant.png",
                "sha256": b.digest(files["assets/icons/v4-panel-ai-assistant.png"]),
                "bytes": len(files["assets/icons/v4-panel-ai-assistant.png"])},
        }
        self.reseal(stage, inputs)
        return inputs

    def reseal(self, stage, inputs):
        b.write_json(stage / "MANIFEST.json", {"inputs": inputs, "files": {
            p.relative_to(stage).as_posix(): {"sha256": b.digest(p.read_bytes()), "bytes": p.stat().st_size}
            for p in stage.rglob("*") if p.is_file() and p.name != "MANIFEST.json"}})

    def test_real_inventory_covers_ui_and_all_authored_assets(self):
        for names in b.SURFACES.values():
            for name in names:
                require_key(self, "narwhal3d/" + name, self.sources)
        for name in ("ai_assistant", "ai_settings_popup", "ai_transport", "shell_popup", "filedialog", "printdialog", "colordialog"):
            require_key(self, "narwhal3d/" + name + ".py", self.sources)
        self.assertGreater(len(self.assets), 2000)
        for base in b.ASSET_ROOTS:
            expected = {p.relative_to(ROOT).as_posix() for p in (ROOT / base).rglob("*") if p.is_file()}
            for name in sorted(expected):
                require_key(self, name, self.assets)
        require_key(self, "native/welcome/src/main.rs", self.sources)
        for name in (*self.sources, *self.assets):
            b.safe_name(name)
        # ICON_SOURCES values name original artwork, not runtime files (e.g.
        # fin.png -> narwhal-fin.png). Use the same resolved inventory as the UI.
        spec = importlib.util.spec_from_file_location("bundle_toolbar_data", ROOT / "narwhal3d/toolbar_data.py")
        toolbar = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(toolbar)
        for value in toolbar.ICON_NAMES:
            require_key(self, "narwhal3d/icons/" + value, self.assets)
        import ast
        values = {n.value for n in ast.walk(ast.parse(self.sources["narwhal3d/rightpanel.py"]))
                  if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.endswith(".png")}
        for value in values:
            require_key(self, "narwhal3d/icons/" + value, self.assets)

    def test_new_imported_picker_automatically_included_and_missing_import_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "narwhal3d"
            package.mkdir()
            (package / "__init__.py").write_text("")
            ui = b"from . import overlap_picker\n"
            picker = package / "overlap_picker.py"
            picker.write_text("class Picker: pass\n")
            self.assertIn("narwhal3d/overlap_picker.py", b.dependencies(root, "narwhal3d/ui.py", ui))
            picker.unlink()  # mutation: the new module is accidentally left out
            with self.assertRaisesRegex(ValueError, "required source module missing"):
                b.dependencies(root, "narwhal3d/ui.py", ui)
            picker.write_text("class Picker: pass\n")
            self.assertIn("narwhal3d/overlap_picker.py", b.dependencies(root, "narwhal3d/ui.py", ui))

    def test_mutation_source_omission_detected_even_if_manifest_resealed(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            inputs = self.fixture(stage)
            b.validate_stage(stage, inputs)
            path = stage / "source/narwhal3d/ai_settings_popup.py"
            original = path.read_bytes()
            path.unlink()
            self.reseal(stage, inputs)
            with self.assertRaisesRegex(ValueError, "required source/asset omitted"):
                b.validate_stage(stage, inputs)
            path.write_bytes(original)
            self.reseal(stage, inputs)
            b.validate_stage(stage, inputs)

    def test_mutation_referenced_icon_omission_detected_without_manifest_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            inputs = self.fixture(stage)
            path = stage / "assets/icons/v4-panel-ai-assistant.png"
            original = path.read_bytes()
            # Remove even the input receipt: the live data still demands this icon.
            receipt = inputs.pop("narwhal3d/icons/v4-panel-ai-assistant.png")
            path.unlink()
            self.reseal(stage, inputs)
            with self.assertRaisesRegex(ValueError, "required UI icon missing"):
                b.validate_stage(stage, inputs)
            path.write_bytes(original)
            inputs["narwhal3d/icons/v4-panel-ai-assistant.png"] = receipt
            self.reseal(stage, inputs)
            b.validate_stage(stage, inputs)

    def test_mutation_content_tamper_detected_then_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            inputs = self.fixture(stage)
            path = stage / "source/narwhal3d/ai_settings_popup.py"
            original = path.read_bytes()
            path.write_bytes(b"# accidental truncation\n")
            with self.assertRaisesRegex(ValueError, "hash/size mismatch"):
                b.validate_stage(stage, inputs)
            path.write_bytes(original)
            b.validate_stage(stage, inputs)

    def test_zip_integrity_and_omission_traversal_duplicate_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / "stage"
            stage.mkdir()
            self.fixture(stage)
            path = Path(tmp) / "bundle.zip"
            b.make_zip(path, stage)
            with zipfile.ZipFile(path) as archive:
                entries = [(n, archive.read(n)) for n in archive.namelist()]
            for bad_entries in (entries[:-1], entries + [("../escape", b"bad")], entries + [entries[0]],
                                entries + [(b.NAME + ("/More_" + "Data/private.png"), b"bad")]):
                with zipfile.ZipFile(path, "w") as archive:
                    import warnings
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", UserWarning)
                        for name, data in bad_entries:
                            archive.writestr(name, data)
                with self.assertRaises(ValueError):
                    b.validate_zip(path, stage)
            b.make_zip(path, stage)

    def test_paths_and_symlinks_fail_closed(self):
        for name in ("../escape", "/absolute", "a/../../escape", "a\\b", "C:/escape", "a//b",
                     "More_" + "Data/private.png", "source/vendor/font.woff2", "bug_" + "reports/BUG_REPORTS.md",
                     "assets/model.3dm", "source/mod.pyc", "assets/.env"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                b.safe_name(name)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "safe.py").write_text("# fixture\n")
            (root / "linked.py").symlink_to(root / "safe.py")
            with self.assertRaisesRegex(ValueError, "symlink"):
                b.read_input(root, "linked.py")

    def test_clean_environment_never_inherits_preferences_or_credentials(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-secret-never-export",
                                     "NARWHAL3D_SETTINGS_FILE": "/private/preferences.json",
                                     "PYTHONPATH": "/private/hooks", "DISPLAY": ":99"}):
            env = b.clean_env(Path("/isolated"))
        for name in ("OPENAI_API_KEY", "NARWHAL3D_SETTINGS_FILE", "PYTHONPATH", "DISPLAY"):
            self.assertNotIn(name, env)
        self.assertEqual(env["HOME"], "/isolated")
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")

    def test_mock_is_labelled_and_sidebar_has_controls_not_shot_pointers(self):
        html = (ROOT / "scripts/ui_bundle/index.html").read_text()
        js = (ROOT / "scripts/ui_bundle/app.js").read_text()
        self.assertIn("EDITABLE UI PREVIEW", js)
        self.assertNotIn('class="mock-banner"', html)
        for name in ('welcome.js', 'welcome.css', 'dialogs.js', 'dialogs.css'):
            self.assertIn('playground/' + name, html)
            self.assertIn(name, b.PLAYGROUND_FILES)
        for name in ('fonts/Inter.woff2', 'fonts/Ubuntu-Light.ttf', 'fonts/OFL-1.1.txt', 'fonts/UFL.txt'):
            self.assertIn(name, b.PLAYGROUND_FILES)
            self.assertTrue((ROOT / 'scripts/ui_bundle' / name).is_file(), name)
        self.assertIn("connect-src 'none'", html)
        for name in ("renderAI", "renderProperties", "renderDisplay", "renderBug", "openAISettings", "openInventory"):
            self.assertIn("function " + name + "(", js)
        for text in ("No provider contacted", "Tool actions are disabled", "Mock discovery", "Cancel"):
            self.assertIn(text, js)
        self.assertNotIn("The real panel is shown in screenshots/", js)
        self.assertNotIn("pick a point (this playground", js)

    def test_dom_image_urls_are_document_relative_not_script_relative(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            inputs = self.fixture(stage)
            js = stage / 'playground/app.js'
            js.write_text("const image={}; image.src='assets/icons/v4-panel-ai-assistant.png';")
            self.reseal(stage, inputs)
            b.validate_stage(stage, inputs)
            js.write_text("const image={}; image.src='assets/icons/absent.png';")
            self.reseal(stage, inputs)
            with self.assertRaisesRegex(ValueError, 'browser resource'):
                b.validate_stage(stage, inputs)

    def test_offline_reference_missing_and_network_mutations_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            inputs = self.fixture(stage)
            path = stage / "playground/app.js"
            original = path.read_bytes()
            path.write_text('fetch("https://example.invalid");')
            self.reseal(stage, inputs)
            with self.assertRaisesRegex(ValueError, "network API"):
                b.validate_stage(stage, inputs)
            path.write_bytes(original)
            css = stage / "playground/style.css"
            css.write_text('body { background: url(missing.png); }')
            self.reseal(stage, inputs)
            with self.assertRaisesRegex(ValueError, "browser resource"):
                b.validate_stage(stage, inputs)
            css.write_text("body { color: black; }")
            self.reseal(stage, inputs)
            b.validate_stage(stage, inputs)


if __name__ == "__main__":
    if "--under-cap" not in sys.argv:
        result = subprocess.run(["systemd-run", "--user", "--quiet", "--wait", "--pipe",
                                 "-p", "MemoryMax=768M", "-p", "MemorySwapMax=0",
                                 "-p", "TasksMax=16", "-p", "RuntimeMaxSec=60",
                                 "-p", "TimeoutStopSec=2", "-p", "KillMode=control-group",
                                 "-p", "WorkingDirectory=" + str(ROOT),
                                 sys.executable, "-B", str(Path(__file__).resolve()),
                                 "--under-cap", *sys.argv[1:]], timeout=70)
        raise SystemExit(result.returncode)
    sys.argv.remove("--under-cap")
    unittest.main(verbosity=2)
