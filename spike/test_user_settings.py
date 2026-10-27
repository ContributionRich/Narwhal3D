"""Tests for persistent user preferences. Pure Python, no bpy.

Pins the typed-settings contract:
* with no file, every key returns its default (the schema is the defaults dict);
* set/save/load round-trips the values;
* a value of the WRONG TYPE in the file falls back to the default for THAT key
  (only that key -- the other keys still load their stored values);
* a key not in the defaults dict is refused by both get and set (KeyError);
* a missing or corrupt store file loads as all-defaults and never raises.

Run: tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_user_settings.py
"""
import importlib.util
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, "narwhal3d", filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


us = _load("narwhal_user_settings", "user_settings.py")

results = []
TMP = tempfile.mkdtemp(prefix="settings_")


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


def _settings(sub="s1", **defaults):
    d = os.path.join(TMP, sub)
    os.makedirs(d, exist_ok=True)
    defs = defaults if defaults else us.DEFAULTS
    return us.UserSettings(os.path.join(d, "settings.json"), defs)


@check("module loads without bpy and UserSettings is constructible")
def _():
    assert "bpy" not in sys.modules
    s = _settings("ctor")
    assert callable(s.get) and callable(s.set)
    assert callable(s.save) and callable(s.load)


@check("the documented defaults are present and the right types")
def _():
    d = us.DEFAULTS
    assert d["autosave_minutes"] == 20 and type(d["autosave_minutes"]) is int
    assert d["recent_limit"] == 10 and type(d["recent_limit"]) is int
    assert d["default_print_size"] == "Letter" and type(d["default_print_size"]) is str


@check("no file: every key returns its default")
def _():
    s = _settings("nofile")
    s.load()                       # file does not exist yet
    assert s.get("autosave_minutes") == 20
    assert s.get("recent_limit") == 10
    assert s.get("default_print_size") == "Letter"


@check("set + save + load round-trips the values (each type)")
def _():
    s = _settings("round")
    s.load()
    s.set("autosave_minutes", 30)
    s.set("recent_limit", 5)
    s.set("default_print_size", "A4")
    s.save()
    s2 = _settings("round")
    s2.load()
    assert s2.get("autosave_minutes") == 30
    assert s2.get("recent_limit") == 5
    assert s2.get("default_print_size") == "A4"


@check("a wrong-type value in the file falls back to the default for THAT key only")
def _():
    s = _settings("wrongtype")
    s.load()
    s.set("autosave_minutes", 25)
    s.set("recent_limit", 7)
    s.save()
    # corrupt only recent_limit's type in the file (a string where an int belongs)
    with open(s.path) as fh:
        data = json.load(fh)
    data["recent_limit"] = "7"      # wrong type: str where int is the contract
    with open(s.path, "w") as fh:
        json.dump(data, fh)
    s2 = _settings("wrongtype")
    s2.load()
    assert s2.get("recent_limit") == 10          # fell back to the default
    assert s2.get("autosave_minutes") == 25      # the OTHER key still loads its value
    assert s2.get("default_print_size") == "Letter"   # untouched default


@check("a value of the wrong type where a str belongs also falls back")
def _():
    s = _settings("wrongstr")
    s.load()
    s.set("default_print_size", "A3")
    s.save()
    with open(s.path) as fh:
        data = json.load(fh)
    data["default_print_size"] = 3               # wrong type: int where str belongs
    with open(s.path, "w") as fh:
        json.dump(data, fh)
    s2 = _settings("wrongstr")
    s2.load()
    assert s2.get("default_print_size") == "Letter"


@check("an unknown key is refused by get AND by set (KeyError)")
def _():
    s = _settings("unknown")
    s.load()
    try:
        s.get("not_a_setting")
        raise AssertionError("get of unknown key should raise KeyError")
    except KeyError:
        pass
    try:
        s.set("not_a_setting", 1)
        raise AssertionError("set of unknown key should raise KeyError")
    except KeyError:
        pass


@check("a corrupt store file loads as all defaults (never raises)")
def _():
    s = _settings("corrupt")
    with open(s.path, "w") as fh:
        fh.write("{ not valid json at all")
    s.load()
    assert s.get("autosave_minutes") == 20
    assert s.get("recent_limit") == 10
    assert s.get("default_print_size") == "Letter"


@check("a store that is valid JSON but not an object loads as all defaults")
def _():
    s = _settings("notobj")
    with open(s.path, "w") as fh:
        json.dump([1, 2, 3], fh)     # valid JSON, but a list not a dict
    s.load()
    assert s.get("autosave_minutes") == 20
    assert s.get("recent_limit") == 10


@check("a store with an EXTRA unknown key ignores it (the schema is authoritative)")
def _():
    s = _settings("extra")
    s.load()
    with open(s.path, "w") as fh:
        json.dump({"autosave_minutes": 40, "rogue_key": "x"}, fh)
    s2 = _settings("extra")
    s2.load()
    assert s2.get("autosave_minutes") == 40
    try:
        s2.get("rogue_key")
        raise AssertionError("rogue key from the file must not become a setting")
    except KeyError:
        pass


@check("set still accepts wrong types and reload self-heals, including bool versus int")
def _():
    s = _settings("set_wrong_type")
    s.set("autosave_minutes", "30")
    s.set("recent_limit", True)
    assert s.get("autosave_minutes") == "30"
    s.save()
    s.load()
    assert s.get("autosave_minutes") == 20
    assert s.get("recent_limit") == 10


@check("save preserves unknown fields added or changed after load")
def _():
    s = _settings("fresh_unknown")
    with open(s.path, "w") as fh:
        json.dump({"theme": "dark", "future": {"version": 1}}, fh)
    s.load()
    with open(s.path, "w") as fh:
        json.dump({"theme": "light", "future": {"version": 2}, "welcome": False}, fh)
    s.set("recent_limit", 12)
    s.save()
    with open(s.path) as fh:
        data = json.load(fh)
    assert data["theme"] == "light" and data["future"] == {"version": 2}
    assert data["welcome"] is False and data["recent_limit"] == 12
    try:
        s.get("future")
        raise AssertionError("unknown disk fields must stay private")
    except KeyError:
        pass


@check("fsync and replace failures preserve original bytes and clean sibling temps")
def _():
    from pathlib import Path
    from unittest.mock import patch
    s = _settings("atomic")
    s.save()
    path = Path(s.path)
    old = path.read_bytes()
    s.set("recent_limit", 99)
    for operation in ("fsync", "replace"):
        with patch.object(us.os, operation, side_effect=OSError("injected failure")):
            try:
                s.save()
                raise AssertionError("write failure must propagate")
            except OSError:
                pass
        assert path.read_bytes() == old
        assert list(path.parent.iterdir()) == [path]
    s.save()
    assert json.loads(path.read_text())["recent_limit"] == 99


@check("unreadable file is safe at startup but cannot be overwritten on save")
def _():
    from pathlib import Path
    from unittest.mock import patch
    s = _settings("read_failure")
    s.set("recent_limit", 22)
    s.save()
    old = Path(s.path).read_bytes()
    with patch("builtins.open", side_effect=PermissionError("injected read failure")):
        s.load()
        assert s.get("recent_limit") == 10
        try:
            s.save()
            raise AssertionError("must not overwrite an unreadable file")
        except PermissionError:
            pass
    assert Path(s.path).read_bytes() == old


@check("serialization failure preserves original and removes incomplete temp")
def _():
    from pathlib import Path
    s = _settings("serialization")
    s.save()
    path = Path(s.path)
    old = path.read_bytes()
    s.set("recent_limit", object())
    try:
        s.save()
        raise AssertionError("non-JSON values cannot be saved")
    except TypeError:
        pass
    assert path.read_bytes() == old
    assert list(path.parent.iterdir()) == [path]


@check("invalid UTF-8 loads defaults without raising")
def _():
    s = _settings("unicode")
    with open(s.path, "wb") as fh:
        fh.write(b"\xff")
    s.load()
    assert s.get("recent_limit") == 10


@check("mutable defaults do not leak between instances or reloads")
def _():
    defaults = {"items": []}
    a = _settings("mutable_a", **defaults)
    b = _settings("mutable_b", **defaults)
    a.get("items").append("a")
    assert b.get("items") == [] and defaults == {"items": []}
    a.load()
    assert a.get("items") == []


# ---------------------------------------------------------------------------
failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
import shutil  # noqa: E402
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
