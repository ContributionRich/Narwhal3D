"""Tests for ACI <-> RGB. Pure Python, no bpy.

Pins the nine STANDARD ACI colours (the only ones every DXF reader agrees on)
and the two refusal rules:
* indices 10-255 -> ``(None, "ACI 10-255 not implemented yet")`` (never a guess);
* 0 (ByBlock) and 256 (ByLayer) are refused by name (never a concrete colour).
``rgb_to_aci``: exact match -> ``(idx, True)``; anything else -> NEAREST of 1-9
by Euclidean RGB -> ``(idx, False)``; never returns 0 or 256.
"""
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


ac = _load("narwhal_aci_colors", "aci_colors.py")

results = []


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


# the nine standard colours (MEASURED -- the tables agree on exactly these)
STANDARD = {
    1: (255, 0, 0),
    2: (255, 255, 0),
    3: (0, 255, 0),
    4: (0, 255, 255),
    5: (0, 0, 255),
    6: (255, 0, 255),
    7: (255, 255, 255),
    8: (128, 128, 128),
    9: (192, 192, 192),
}


@check("module loads without bpy and exposes both functions")
def _():
    assert "bpy" not in sys.modules
    assert callable(ac.aci_to_rgb) and callable(ac.rgb_to_aci)


@check("aci_to_rgb: all nine standard colours are exact")
def _():
    for i, rgb in STANDARD.items():
        got, err = ac.aci_to_rgb(i)
        assert err is None and got == tuple(rgb), (i, got, err)


@check("aci_to_rgb: 10-255 is NOT built -> (None, 'ACI 10-255 not implemented yet')")
def _():
    for i in (10, 11, 200, 255):
        rgb, err = ac.aci_to_rgb(i)
        assert rgb is None, (i, rgb)
        assert err == "ACI 10-255 not implemented yet", (i, err)


@check("aci_to_rgb: 0 (ByBlock) and 256 (ByLayer) are refused by name")
def _():
    for i in (0, 256):
        rgb, err = ac.aci_to_rgb(i)
        assert rgb is None and isinstance(err, str) and err, (i, rgb, err)
        assert "ByBlock" in err or "ByLayer" in err, (i, err)


@check("aci_to_rgb: out-of-range and non-integers -> error (never a guess)")
def _():
    for bad in (257, 300, -1, 1.5, "7", None, True, [1], 9.0):
        rgb, err = ac.aci_to_rgb(bad)
        assert rgb is None and isinstance(err, str) and err, (bad, rgb, err)


@check("rgb_to_aci: exact matches give (index, True) for all nine")
def _():
    for i, rgb in STANDARD.items():
        idx, exact = ac.rgb_to_aci(tuple(rgb))
        assert idx == i and exact is True, (rgb, idx, exact)
    # float form of the same colour is still exact
    idx, exact = ac.rgb_to_aci((255.0, 0.0, 0.0))
    assert idx == 1 and exact is True, (idx, exact)


@check("rgb_to_aci: non-exact colours give the NEAREST of 1-9 with exact=False")
def _():
    cases = {
        (10, 10, 10): 8,       # near dark grey
        (200, 200, 200): 9,    # near light grey
        (250, 10, 10): 1,      # near red
        (250, 250, 250): 7,    # near white
        (0, 0, 0): 8,          # black -> dark grey (not white)
        (127, 127, 127): 8,    # one step from dark grey
    }
    for rgb, want in cases.items():
        idx, exact = ac.rgb_to_aci(rgb)
        assert idx == want and exact is False, (rgb, idx, exact, want)


@check("rgb_to_aci: never returns 0 or 256, and is total over valid input")
def _():
    for rgb in ((0, 0, 0), (255, 255, 255), (128, 128, 128), (1, 2, 3)):
        idx, exact = ac.rgb_to_aci(rgb)
        assert 1 <= idx <= 9, (rgb, idx)


@check("rgb_to_aci: a structurally invalid rgb raises ValueError (never a guess)")
def _():
    for bad in (
        (255, 0),            # 2 components
        (255, 0, 0, 1),      # 4 components
        (255, 0, "0"),       # non-numeric
        (255, 0, None),
        (256, 0, 0),         # out of range
        (0, 0, -1),
        "255,0,0",           # a string
        True,
    ):
        try:
            ac.rgb_to_aci(bad)
            raise AssertionError(f"expected ValueError for rgb {bad!r}")
        except ValueError:
            pass


# ---------------------------------------------------------------------------
failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
