"""Tests for the colour picker + colour parsing. Pure Python, no bpy.

Pins the parsing contract a Layers-panel swatch depends on:
* every accepted form (`#rgb`, `#rrggbb`, `rgb(r,g,b)`, `rgba(r,g,b,a)`,
  `r,g,b` / `r g b`) parses to the right 0..255 triple;
* every refusal (out-of-range, wrong count, non-numeric, garbage, empty)
  returns `None` with a named error -- never a guess;
* `to_hex` is the exact inverse the desktop tools consume;
* layer-only initialization sets Value 255 without mutating the input colour;
* a fake backend drives `ask_color` (OK and CANCEL) without a desktop;
* a headless run (`NARWHAL3D_NO_DIALOGS=1`) answers UNAVAILABLE and never
  calls the backend -- no silent default.

Run: tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_colordialog.py
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


cd = _load("narwhal_colordialog", "colordialog.py")

results = []


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


def _ok(text, want):
    rgb, err = cd.parse_color(text)
    assert rgb == want and err is None, (text, rgb, err)


def _refuse(text):
    rgb, err = cd.parse_color(text)
    assert rgb is None and err, (text, rgb, err)


@check("module loads without bpy and all public names exist")
def _():
    assert "bpy" not in sys.modules
    for fn in (cd.ask_color, cd.parse_color, cd.to_hex, cd.set_backend,
               cd.layer_picker_initial):
        assert callable(fn)
    assert cd.OK == "ok" and cd.CANCEL == "cancel" and cd.UNAVAILABLE == "unavailable"


@check("#rrggbb parses (case-insensitive, all six digits used)")
def _():
    _ok("#ff0000", (255, 0, 0))
    _ok("#00ff00", (0, 255, 0))
    _ok("#0000ff", (0, 0, 255))
    _ok("#123456", (18, 52, 86))
    _ok("#FF0000", (255, 0, 0))
    _ok("#abcdef", (171, 205, 239))


@check("#rgb expands each digit (doubled)")
def _():
    _ok("#fff", (255, 255, 255))
    _ok("#000", (0, 0, 0))
    _ok("#f00", (255, 0, 0))
    _ok("#abc", (170, 187, 204))   # standard CSS short-hex: #aabbcc


@check("rgb(r,g,b) parses")
def _():
    _ok("rgb(255,0,0)", (255, 0, 0))
    _ok("rgb( 255 , 0 , 0 )", (255, 0, 0))
    _ok("rgb(0,255,0)", (0, 255, 0))
    _ok("RGB(10,20,30)", (10, 20, 30))


@check("rgba(r,g,b,a) reads and ignores the alpha")
def _():
    _ok("rgba(255,0,0,1)", (255, 0, 0))
    _ok("rgba(255,0,0,255)", (255, 0, 0))
    _ok("rgba(10,20,30,128)", (10, 20, 30))


@check("bare r,g,b and r g b parse")
def _():
    _ok("255,0,0", (255, 0, 0))
    _ok("10, 20, 30", (10, 20, 30))
    _ok("10 20 30", (10, 20, 30))
    _ok("255 0 0", (255, 0, 0))


@check("refusal: out-of-range components")
def _():
    _refuse("#ffg000")       # 'g' is not a hex digit -> bad hex
    _refuse("rgb(256,0,0)")  # 256 > 255
    _refuse("rgb(0,0,300)")
    _refuse("300,0,0")
    _refuse("-1,0,0")
    _refuse("rgba(10,20,30,256)")


@check("refusal: wrong component count")
def _():
    _refuse("#ff")           # 2 hex digits
    _refuse("#fffff")        # 5 hex digits
    _refuse("#fffffff")      # 7 hex digits
    _refuse("rgb(10,20)")    # 2
    _refuse("rgb(10,20,30,40)")  # 4 where 3 expected
    _refuse("rgba(10,20,30)")    # 3 where 4 expected
    _refuse("10,20")         # 2 bare
    _refuse("10 20 30 40")   # 4 bare


@check("refusal: non-numeric and garbage")
def _():
    _refuse("rgb(a,b,c)")
    _refuse("rgb(10,,30)")
    _refuse("rgba(255,0,0,0.5)")   # alpha must be an integer 0..255
    _refuse("red")
    _refuse("10.5,0,0")
    _refuse("")
    _refuse(None)
    _refuse("#zzz")


@check("to_hex is the inverse the desktop tools consume")
def _():
    assert cd.to_hex((255, 0, 0)) == "#ff0000"
    assert cd.to_hex((18, 52, 86)) == "#123456"
    assert cd.to_hex((0, 0, 0)) == "#000000"
    assert cd.to_hex((255, 255, 255)) == "#ffffff"


@check("to_hex refuses out-of-range and wrong shape")
def _():
    for bad in ((256, 0, 0), (0, 0, -1), (1, 2), (1, 2, 3, 4), "nope"):
        try:
            cd.to_hex(bad)
            raise AssertionError(f"to_hex should have refused {bad!r}")
        except ValueError:
            pass


@check("layer picker: black starts white; every nonblack default has Value 255")
def _():
    assert cd.layer_picker_initial((0, 0, 0)) == (255, 255, 255)
    assert cd.layer_picker_initial((128, 64, 32)) == (255, 128, 64)
    assert cd.layer_picker_initial((1, 6, 0)) == (43, 255, 0)  # 42.5 rounds up
    for rgb in ((255, 128, 64), (0, 255, 17), (12, 0, 255), (255, 255, 255)):
        assert cd.layer_picker_initial(rgb) == rgb, rgb
    for grey in (1, 64, 128, 254):
        assert cd.layer_picker_initial((grey, grey, grey)) == (255, 255, 255)


@check("layer picker: channel ratios preserved within one byte for every peak")
def _():
    for peak in range(1, 256):
        for c in range(peak + 1):
            for rgb in ((peak, c, 0), (0, peak, c), (c, 0, peak)):
                initial = cd.layer_picker_initial(rgb)
                assert isinstance(initial, tuple) and len(initial) == 3
                assert max(initial) == 255, (rgb, initial)
                for before, after in zip(rgb, initial):
                    assert type(after) is int and 0 <= after <= 255
                    assert abs(after * peak - before * 255) <= peak, (rgb, initial)


@check("layer picker: validation/coercion follows to_hex, invalid colours refuse")
def _():
    assert cd.layer_picker_initial(["128", "64", "32"]) == (255, 128, 64)
    for bad in (None, (256, 0, 0), (0, -1, 0), (), (1, 2), (1, 2, 3, 4),
                "nope", ("bad", 0, 0)):
        try:
            cd.layer_picker_initial(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"layer_picker_initial should have refused {bad!r}")


# --- fake backend ---------------------------------------------------------
class Fake:
    """A backend whose answer is scripted by the test. Records what it got."""
    def __init__(self, status, rgb, default_seen=None):
        self.status = status
        self.rgb = rgb
        self.default_seen = default_seen

    def ask(self, title, hex_color):
        self.default_seen.append(hex_color)
        return self.status, self.rgb


@check("fake backend drives ask_color: OK carries the colour")
def _():
    seen = []
    fake = Fake(cd.OK, (10, 20, 30), seen)
    cd.set_backend(fake)
    status, rgb = cd.ask_color("Pick", (255, 255, 255))
    assert status == cd.OK and rgb == (10, 20, 30), (status, rgb)
    assert seen == ["#ffffff"], seen  # initial_rgb passed as #rrggbb
    cd.set_backend(None)


@check("fake backend drives ask_color: CANCEL carries None")
def _():
    fake = Fake(cd.CANCEL, None, [])
    cd.set_backend(fake)
    status, rgb = cd.ask_color("Pick", (0, 0, 0))
    assert status == cd.CANCEL and rgb is None, (status, rgb)
    cd.set_backend(None)


@check("generic ask_color keeps black and dim defaults unchanged")
def _():
    seen = []
    cd.set_backend(Fake(cd.CANCEL, None, seen))
    try:
        for rgb in ((0, 0, 0), (128, 64, 32)):
            assert cd.ask_color("Pick", rgb) == (cd.CANCEL, None)
        assert seen == ["#000000", "#804020"], seen
    finally:
        cd.set_backend(None)


@check("layer default and backend answer never mutate supplied layer colour, including cancel")
def _():
    for original, default in (([0, 0, 0], "#ffffff"), ([128, 64, 32], "#ff8040")):
        for status, answer in ((cd.CANCEL, None), (cd.OK, (10, 20, 30))):
            layer = {"color": original.copy()}
            stored = layer["color"]
            seen = []
            cd.set_backend(Fake(status, answer, seen))
            try:
                initial = cd.layer_picker_initial(stored)
                assert stored == original and layer["color"] is stored
                result = cd.ask_color("Layer colour", initial)
                assert result == (status, answer), result
                assert seen == [default], seen
                assert stored == original and layer["color"] is stored
            finally:
                cd.set_backend(None)


@check("headless (NARWHAL3D_NO_DIALOGS=1), no backend set -> UNAVAILABLE, no tool spawned")
def _():
    cd.set_backend(None)  # auto-detect path, nothing injected
    os.environ["NARWHAL3D_NO_DIALOGS"] = "1"
    try:
        # auto-detection must not pick a real tool when there is no one to answer
        assert cd.backend() is None
        status, rgb = cd.ask_color("Pick", (1, 2, 3))
        assert status == cd.UNAVAILABLE and rgb is None, (status, rgb)
    finally:
        del os.environ["NARWHAL3D_NO_DIALOGS"]
        cd.set_backend(None)


@check("an explicitly set backend IS usable headless (a deliberate stub, not a spawned dialog)")
def _():
    os.environ["NARWHAL3D_NO_DIALOGS"] = "1"
    try:
        class Stub:
            def ask(self, title, hex_color):
                return cd.OK, (7, 8, 9)
        cd.set_backend(Stub())
        status, rgb = cd.ask_color("Pick", (1, 2, 3))
        assert status == cd.OK and rgb == (7, 8, 9), (status, rgb)
    finally:
        del os.environ["NARWHAL3D_NO_DIALOGS"]
        cd.set_backend(None)


@check("headless() reports True under NARWHAL3D_NO_DIALOGS=1")
def _():
    os.environ["NARWHAL3D_NO_DIALOGS"] = "1"
    try:
        assert cd.headless() is True
    finally:
        del os.environ["NARWHAL3D_NO_DIALOGS"]


# ---------------------------------------------------------------------------
failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
