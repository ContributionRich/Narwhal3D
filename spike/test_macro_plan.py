"""Focused tests for the pure macro execution planner."""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, "narwhal3d", filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


macro = _load("narwhal_test_macro_for_plan", "macro.py")
# Expose the dependency under the fallback name used by file-path loading.
sys.modules["macro"] = macro
# macro_plan supports this file-path loading style as well as package imports.
plan_mod = _load("narwhal_test_macro_plan", "macro_plan.py")

results = []


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as exc:  # noqa: BLE001
            results.append((name, "FAIL", repr(exc)))
    return deco


@check("module is pure and exposes plan + refusal error")
def _():
    assert "bpy" not in sys.modules
    assert callable(plan_mod.plan)
    assert issubclass(plan_mod.MacroPlanError, ValueError)


@check("preserves cancel, apostrophe, underscore, and dash prefixes")
def _():
    got = plan_mod.plan("! '-_Zoom")
    assert got == [
        {"action": "cancel", "prefixes": ("!",)},
        {"action": "command", "name": "Zoom",
         "prefixes": ("'", "-", "_"), "dash": True,
         "underscore": True, "transparent": True},
    ], got


@check("transparent measured view/CPlane commands are accepted")
def _():
    got = plan_mod.plan("'_Zoom '_CPlane")
    assert [a["name"] for a in got] == ["Zoom", "CPlane"]
    assert all(a["transparent"] for a in got)


@check("transparent geometry command is refused by name")
def _():
    try:
        plan_mod.plan("'_Box")
    except plan_mod.NonNestableCommandError as exc:
        assert "Box" in str(exc)
    else:
        raise AssertionError("transparent Box must be refused")


@check("Pause/MultiPause and Enter/EnterEnd retain their spelling")
def _():
    got = plan_mod.plan("_Pause _MultiPause _Enter _EnterEnd")
    assert got == [
        {"action": "pause", "special": "Pause", "prefixes": ("_",)},
        {"action": "pause", "special": "MultiPause", "prefixes": ("_",)},
        {"action": "enter", "special": "Enter", "prefixes": ("_",)},
        {"action": "enter", "special": "EnterEnd", "prefixes": ("_",)},
    ], got


@check("options and text remain explicit data actions")
def _():
    assert plan_mod.plan('_-_SetDisplayMode _Mode=_Shaded "My Mode"') == [
        {"action": "command", "name": "SetDisplayMode",
         "prefixes": ("_", "-", "_"), "dash": True,
         "underscore": True, "transparent": False},
        {"action": "option", "name": "Mode", "value": "Shaded",
         "prefixes": ("_",)},
        {"action": "text", "value": "My Mode"},
    ]


@check("invalid input and unknown token kinds are refused")
def _():
    for bad in (None, 5, "not-a-token-list"):
        try:
            plan_mod.build_plan(bad)
        except plan_mod.MacroPlanError:
            pass
        else:
            raise AssertionError("invalid tokens accepted")
    try:
        plan_mod.build_plan([{"kind": "future"}])
    except plan_mod.MacroPlanError:
        pass
    else:
        raise AssertionError("unknown token kind accepted")


failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
