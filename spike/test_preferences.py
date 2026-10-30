"""Pure persistence tests. Run: python3 -B spike/test_preferences.py

A lightweight package avoids narwhal3d.__init__'s intentional UI startup. Real
preferences, AI validation, toolbar data and UserSettings are loaded unmodified.
"""
import ast
import copy
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_preferences_test_app"
pkg = types.ModuleType(PACKAGE)
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules[PACKAGE] = pkg
prefs = importlib.import_module(PACKAGE + ".preferences")
us = importlib.import_module(PACKAGE + ".user_settings")


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="narwhal_preferences_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "config" / "preferences.json"
        self.env = patch.dict(os.environ, {"NARWHAL3D_SETTINGS_FILE": str(self.path)}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        prefs.reset_cache()
        self.addCleanup(prefs.reset_cache)

    def write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_import_boundary(self):
        for name in ("bpy", "rhino3dm", PACKAGE + ".design", PACKAGE + ".ui", PACKAGE + ".printdialog"):
            self.assertNotIn(name, sys.modules)

    def test_defaults_and_no_read_side_effects(self):
        expected = {
            "theme": "light", "show_unavailable": True, "toolbar": "Standard",
            "inspector": "Layers", "grid_snap": False, "ortho": False,
            "gumball": True, "gumball_alignment": "CPlane", "viewport_tabs": True,
            "osnap_modes": ["End", "Point", "Mid", "Cen", "Quad"],
            "osnap_disabled": False, "osnap_project": False,
            "ai_config": {}, "ai_include_selection": True, "ai_metadata_permission": {}, "print_settings": {},
        }
        self.assertEqual(prefs.load(), expected)
        self.assertEqual({key: prefs.get(key) for key in expected}, expected)
        self.assertFalse(self.path.parent.exists())
        prefs.update({})
        self.assertFalse(self.path.exists())

    def test_metadata_permission_is_explicit_bounded_and_recipient_scoped(self):
        config = prefs.ai_assistant.set_provider('openai_compatible', 'model-a')
        permission = prefs.ai_assistant.metadata_permission(config)
        self.assertEqual(prefs.get('ai_metadata_permission'), {})
        prefs.update({'ai_metadata_permission': permission})
        prefs.reset_cache()
        self.assertEqual(prefs.load()['ai_metadata_permission'], permission)
        self.assertEqual(prefs.ai_assistant.metadata_permission(
            prefs.ai_assistant.set_provider('openai_compatible', 'model-b')), permission)
        self.assertNotEqual(prefs.ai_assistant.metadata_permission(
            prefs.ai_assistant.set_provider('openai_compatible', 'model-a', 'http://127.0.0.1:4321/v1')), permission)
        for bad in (dict(permission, version=True), dict(permission, api_key='secret'),
                    dict(permission, endpoint='http://127.0.0.1:1234/v1/'),
                    dict(permission, provider_id='unknown')):
            with self.assertRaises(ValueError):
                prefs.update({'ai_metadata_permission': bad})
        prefs.update({'ai_metadata_permission': {}})
        prefs.reset_cache()
        self.assertEqual(prefs.load()['ai_metadata_permission'], {})

    def test_corrupt_files_and_wrong_types(self):
        self.path.parent.mkdir(parents=True)
        for raw in (b"{broken", b"[]", b"null", b"true", b"42", b'"text"', b"\xff",
                    b"[" * 2000):
            with self.subTest(raw=raw[:20]):
                self.path.write_bytes(raw)
                self.assertEqual(prefs.load(), prefs.DEFAULTS)
        for key, default in prefs.DEFAULTS.items():
            for bad in (None, 0, 1, "true", [], {}):
                if type(bad) is type(default):
                    continue
                with self.subTest(key=key, bad=bad):
                    self.write({key: bad, "recent_limit": 77})
                    self.assertEqual(prefs.load()[key], default)

    def test_bad_enum_does_not_hide_other_valid_settings(self):
        for key, bad in (("theme", "blue"), ("toolbar", "Made up"),
                         ("inspector", "Materials"), ("gumball_alignment", "Object"),
                         ("osnap_modes", ["End", "fake"]), ("osnap_modes", [{}])):
            self.write({key: bad, "ortho": True})
            values = prefs.load()
            self.assertEqual(values[key], prefs.DEFAULTS[key])
            self.assertTrue(values["ortho"])

    def test_updates_only_supplied_keys_and_fresh_merges(self):
        self.write({"theme": "dark", "future": {"v": [1, 2]}, "recent_limit": 50})
        self.assertEqual(prefs.get("theme"), "dark")
        # Another consumer writes after this client's cached read.
        self.write({"theme": "light", "future": {"v": [3]}, "recent_limit": 60,
                    "welcome": {"dismissed": True}, "toolbar": "Transform"})
        result = prefs.update({"ortho": True})
        self.assertEqual(self.read(), {
            "theme": "light", "future": {"v": [3]}, "recent_limit": 60,
            "welcome": {"dismissed": True}, "toolbar": "Transform", "ortho": True,
        })
        self.assertEqual(result["theme"], "light")
        self.assertEqual(prefs.get("toolbar"), "Transform")
        prefs.update({"theme": "dark"})
        self.assertTrue(self.read()["ortho"])
        self.assertEqual(self.read()["future"], {"v": [3]})

    def test_legacy_store_coexists_with_preferences(self):
        legacy = us.UserSettings(self.path, us.DEFAULTS)
        legacy.load()
        prefs.update({"theme": "dark"})
        legacy.set("recent_limit", 22)
        legacy.save()
        self.assertEqual(self.read()["theme"], "dark")
        prefs.update({"inspector": "AI Assistant"})
        self.assertEqual(self.read()["recent_limit"], 22)
        self.assertEqual(self.read()["default_print_size"], "Letter")

    def test_unknown_and_invalid_updates_fail_without_writing(self):
        prefs.update({"theme": "dark"})
        before = self.path.read_bytes()
        with self.assertRaises(KeyError):
            prefs.get("welcome")
        with self.assertRaises(KeyError):
            prefs.update({"theme": "light", "welcome": True})
        with self.assertRaises(TypeError):
            prefs.update([("theme", "light")])
        for value in ("blue", 1, None):
            with self.assertRaises(ValueError):
                prefs.update({"theme": value})
        with self.assertRaises(ValueError):
            prefs.update({"theme": "light", "ortho": 1})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(prefs.get("theme"), "dark")

    def test_valid_choices_booleans_and_snaps(self):
        for key, choices in prefs._ENUMS.items():
            for choice in choices:
                prefs.update({key: choice})
                self.assertEqual(prefs.get(key), choice)
        for key, value in prefs.DEFAULTS.items():
            if type(value) is bool:
                prefs.update({key: not value})
                self.assertIs(prefs.get(key), not value)
        prefs.update({"osnap_modes": list(prefs._OSNAP_MODES) + ["End"]})
        self.assertEqual(prefs.get("osnap_modes"), list(prefs._OSNAP_MODES))
        prefs.update({"osnap_modes": []})
        self.assertEqual(prefs.get("osnap_modes"), [])

    def test_ai_whitelist_and_endpoint_normalization(self):
        config = {"provider_id": "openai", "model": " gpt-example ",
                  "endpoint": "https://api.openai.com/v1/", "credential_env": "OPENAI_API_KEY",
                  "allow_insecure_remote": False, "api_key": "secret-one",
                  "password": "secret-two", "rawkey": "secret-three",
                  "credential_configured": True, "arbitrary": {"secret": "four"}}
        original = copy.deepcopy(config)
        prefs.update({"ai_config": config})
        expected = {"provider_id": "openai", "model": "gpt-example",
                    "endpoint": "https://api.openai.com/v1", "credential_env": "OPENAI_API_KEY",
                    "allow_insecure_remote": False}
        self.assertEqual(prefs.get("ai_config"), expected)
        self.assertEqual(self.read(), {"ai_config": expected})
        self.assertNotIn("secret", self.path.read_text())
        self.assertEqual(config, original)
        self.write({"ai_config": config})
        self.assertEqual(prefs.load()["ai_config"], expected)
        prefs.update({"ai_config": {"api_key": "secret"}})
        self.assertEqual(self.read()["ai_config"], {})

    def test_ai_rejects_unsafe_or_malformed_config(self):
        bad_configs = [
            {"provider_id": "invented"}, {"model": "x" * 201}, {"model": 9},
            {"model": "bad\nname"}, {"credential_env": "raw-key"},
            {"credential_env": ""}, {"credential_env": "9KEY"},
            {"allow_insecure_remote": "false"}, {"endpoint": None},
        ]
        for endpoint in ("", "file:///tmp/key", "https://user:pass@example.com/v1",
                         "https://user@example.com", "https://example.com/?api_key=secret",
                         "https://example.com/?%74oken=secret", "https://example.com/#secret",
                         "https://example.com:bad", "http://remote.example/v1",
                         "https://example.com/\nsecret", "https://[invalid"):
            bad_configs.append({"endpoint": endpoint})
        for config in bad_configs:
            with self.subTest(config=config):
                self.write({"ai_config": config, "theme": "dark"})
                self.assertEqual(prefs.load()["ai_config"], {})
                self.assertEqual(prefs.get("theme"), "dark")
                before = self.path.read_bytes()
                with self.assertRaises(ValueError):
                    prefs.update({"ai_config": config})
                self.assertEqual(self.path.read_bytes(), before)

    def test_ai_localhost_and_explicit_http_opt_in(self):
        for endpoint in ("http://127.0.0.1:1234/v1", "http://localhost:8080/v1",
                         "http://[::1]:1234/v1"):
            prefs.update({"ai_config": {"endpoint": endpoint}})
            self.assertEqual(prefs.get("ai_config"), {"endpoint": endpoint})
        config = {"endpoint": "http://remote.example/v1", "allow_insecure_remote": True}
        prefs.update({"ai_config": config})
        self.assertEqual(prefs.get("ai_config"), config)

    def test_print_settings_actual_fields_only(self):
        settings = {"destination": 1, "cdr_version": "17", "size": "Custom",
                    "orientation": "Portrait", "color": "BlackWhite", "view": "My view",
                    "area": "Extents", "scale": "1:5", "width": "Hairline",
                    "selected_only": True, "custom_w": 400.5, "custom_h": 300}
        transient = {"views": ["My view"], "open_dropdown": "size", "focus": "custom_w",
                     "edit_text": "999", "message": "private", "extra": {"secret": "x"},
                     "api_key": "secret", "made_up": 3}
        prefs.update({"print_settings": {**settings, **transient}})
        self.assertEqual(prefs.get("print_settings"), settings)
        self.assertEqual(self.read(), {"print_settings": settings})
        # Nested dictionaries deliberately replace rather than retaining stale fields.
        prefs.update({"print_settings": {"size": "A4"}})
        self.assertEqual(self.read()["print_settings"], {"size": "A4"})

    def test_invalid_print_values(self):
        for key, value in (("destination", True), ("destination", 4), ("cdr_version", 17),
                           ("size", "Drawing"), ("orientation", "Sideways"),
                           ("color", "red"), ("area", "Unknown"), ("scale", "bogus"),
                           ("width", "500"), ("selected_only", 1), ("view", []),
                           ("custom_w", 0), ("custom_h", 5001), ("custom_w", True),
                           ("custom_h", float("nan")), ("custom_w", float("inf"))):
            with self.subTest(key=key, value=value):
                self.write({"print_settings": {key: value}})
                self.assertEqual(prefs.load()["print_settings"], {})
                with self.assertRaises(ValueError):
                    prefs.update({"print_settings": {key: value}})

    def test_schema_tracks_pure_app_declarations_without_importing_ui(self):
        tree = ast.parse((ROOT / "narwhal3d" / "printdialog.py").read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "DialogState")
        declared = {n.target.id for n in cls.body if isinstance(n, ast.AnnAssign)}
        self.assertTrue(prefs._PRINT_FIELDS <= declared)
        rows = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "ROWS" for t in n.targets))
        self.assertEqual(prefs._PRINT_FIELDS, {row[0] for row in ast.literal_eval(rows)})
        tree = ast.parse((ROOT / "narwhal3d" / "rightpanel.py").read_text())
        tabs = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "TABS" for t in n.targets))
        self.assertEqual(prefs._ENUMS["inspector"], tuple(n.args[0].value for n in tabs.elts))

    def test_defensive_copies_and_cache_refresh(self):
        value = prefs.get("osnap_modes")
        value.clear()
        self.assertEqual(prefs.get("osnap_modes"), prefs.DEFAULTS["osnap_modes"])
        data = {"print_settings": {"size": "A4"}}
        snapshot = prefs.update(data)
        data["print_settings"]["size"] = "A0"
        snapshot["print_settings"]["size"] = "A3"
        self.assertEqual(prefs.get("print_settings"), {"size": "A4"})
        self.write({"theme": "dark"})
        self.assertEqual(prefs.get("theme"), "light")
        prefs.reset_cache()
        self.assertEqual(prefs.get("theme"), "dark")
        self.write({"theme": "light"})
        self.assertEqual(prefs.load()["theme"], "light")

    def test_atomic_failures_leave_file_and_cache_unchanged(self):
        prefs.update({"theme": "dark"})
        old = self.path.read_bytes()
        for operation in ("fsync", "replace"):
            with self.subTest(operation=operation):
                with patch.object(us.os, operation, side_effect=OSError("injected")):
                    with self.assertRaises(OSError):
                        prefs.update({"theme": "light"})
                self.assertEqual(self.path.read_bytes(), old)
                self.assertEqual(prefs.get("theme"), "dark")
                self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        with patch.object(us.json, "dump", side_effect=TypeError("serialization failed")):
            with self.assertRaises(TypeError):
                prefs.update({"theme": "light"})
        self.assertEqual(self.path.read_bytes(), old)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        prefs.update({"theme": "light"})
        self.assertEqual(prefs.get("theme"), "light")

    def test_unreadable_file_cannot_be_overwritten(self):
        prefs.update({"theme": "dark"})
        old = self.path.read_bytes()
        with patch("builtins.open", side_effect=PermissionError("injected read failure")):
            with self.assertRaises(PermissionError):
                prefs.update({"ortho": True})
            self.assertEqual(prefs.get("theme"), "dark")
            self.assertEqual(prefs.load(), prefs.DEFAULTS)
        self.assertEqual(self.path.read_bytes(), old)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_same_directory_atomic_replace(self):
        original = us.os.replace
        calls = []

        def replace(src, dest):
            self.assertEqual(Path(src).parent, self.path.parent)
            self.assertEqual(Path(dest), self.path)
            self.assertEqual(json.loads(Path(src).read_text()), {"ortho": True})
            calls.append(src)
            original(src, dest)

        with patch.object(us.os, "replace", side_effect=replace):
            prefs.update({"ortho": True})
        self.assertEqual(len(calls), 1)

    def test_path_override_switches_cache_automatically(self):
        prefs.update({"theme": "dark"})
        other = self.root / "other.json"
        with patch.dict(os.environ, {"NARWHAL3D_SETTINGS_FILE": str(other)}):
            self.assertEqual(prefs.settings_path(), other)
            self.assertEqual(prefs.get("theme"), "light")
            prefs.update({"ortho": True})
        self.assertEqual(prefs.get("theme"), "dark")
        self.assertEqual(self.read(), {"theme": "dark"})
        self.assertEqual(json.loads(other.read_text()), {"ortho": True})

    def test_platform_path_contract(self):
        home = self.root / "home"
        with patch.dict(os.environ, {}, clear=True), patch.object(us.Path, "home", return_value=home):
            with patch.object(us.sys, "platform", "linux"):
                self.assertEqual(prefs.settings_path(), home / ".config/narwhal3d/preferences.json")
                with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "xdg")}):
                    self.assertEqual(prefs.settings_path(), self.root / "xdg/narwhal3d/preferences.json")
            with patch.object(us.sys, "platform", "darwin"):
                self.assertEqual(prefs.settings_path(), home / "Library/Application Support/Narwhal3D/preferences.json")
            with patch.object(us.sys, "platform", "win32"):
                self.assertEqual(prefs.settings_path(), home / "AppData/Roaming/Narwhal3D/preferences.json")
                with patch.dict(os.environ, {"APPDATA": str(self.root / "roaming")}):
                    self.assertEqual(prefs.settings_path(), self.root / "roaming/Narwhal3D/preferences.json")
            for platform in ("linux", "darwin", "win32"):
                with patch.object(us.sys, "platform", platform), patch.dict(os.environ, {
                    "NARWHAL3D_SETTINGS_FILE": str(self.path), "XDG_CONFIG_HOME": "/unused",
                    "APPDATA": "/unused",
                }):
                    self.assertEqual(prefs.settings_path(), self.path)

    def test_home_and_xdg_paths_in_real_environment(self):
        with patch.dict(os.environ, {"HOME": str(self.root)}, clear=True), patch.object(us.sys, "platform", "linux"):
            prefs.update({"theme": "dark"})
            path = self.root / ".config/narwhal3d/preferences.json"
            self.assertEqual(json.loads(path.read_text()), {"theme": "dark"})
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "xdg")}):
                self.assertEqual(prefs.get("theme"), "light")
                prefs.update({"ortho": True})
                self.assertTrue((self.root / "xdg/narwhal3d/preferences.json").exists())

    def test_subprocess_roundtrip_and_stale_parent_merge(self):
        prefs.update({"theme": "dark", "osnap_modes": ["End", "Near"]})
        script = '''import importlib, json, sys, types
p = types.ModuleType("headless_app")
p.__path__ = [sys.argv[1]]
sys.modules[p.__name__] = p
prefs = importlib.import_module("headless_app.preferences")
assert "bpy" not in sys.modules and "rhino3dm" not in sys.modules
assert prefs.get("theme") == "dark"
assert prefs.get("osnap_modes") == ["End", "Near"]
prefs.update({"toolbar": "Curve Tools", "ai_include_selection": False})
print(json.dumps(prefs.load()))
'''
        proc = subprocess.run([sys.executable, "-B", "-c", script, str(ROOT / "narwhal3d")],
                              env=dict(os.environ), capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["toolbar"], "Curve Tools")
        prefs.update({"ortho": True})
        self.assertEqual(self.read()["toolbar"], "Curve Tools")
        self.assertIs(self.read()["ai_include_selection"], False)
        prefs.reset_cache()
        self.assertEqual(prefs.get("osnap_modes"), ["End", "Near"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
