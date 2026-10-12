"""Unit tests for pure viewport labels, dimensions, and menu geometry.

Runs under the bundled *plain* python (tools/.../python3.13) -- NO bpy. This is
the point of keeping `quat_to_label` in the bpy-free `narwhal3d/views.py`:
the label logic is testable without launching Blender. `views.py` is loaded
directly by path (as test_geometry.py does for analysis.py) so importing it does
NOT trigger the package __init__, which would import ui -> bpy.

Run: tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_views.py
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Load views.py by path (NOT `import narwhal3d.views`): the package __init__
# imports ui, which needs bpy -- and this suite must stay Blender-free.
_spec = importlib.util.spec_from_file_location(
    "narwhal_views", os.path.join(ROOT, "narwhal3d", "views.py"))
views = importlib.util.module_from_spec(_spec)
sys.modules["narwhal_views"] = views
_spec.loader.exec_module(views)

# Load `osnap` too so the panel rows can be checked against the snap model.
# Same by-path trick and the same reason.
_ospec = importlib.util.spec_from_file_location(
    "narwhal_osnap", os.path.join(ROOT, "narwhal3d", "osnap.py"))
osnap = importlib.util.module_from_spec(_ospec)
sys.modules["narwhal_osnap"] = osnap
_ospec.loader.exec_module(osnap)

results = []


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


# --- the function is genuinely bpy-free ------------------------------------
@check("views.py imports without bpy")
def _():
    # If `bpy` were required, loading it under plain python above would have
    # already raised and this whole file would be red. Belt: confirm no bpy
    # module slipped into the import namespace through this module.
    assert "bpy" not in sys.modules, "views.py pulled in bpy"


# --- each documented view maps to the right label --------------------------
@check("Top (ORTHO) -> 'Top'")
def _():
    assert views.quat_to_label(views.VIEW_TOP, "ORTHO") == "Top"


@check("Front (ORTHO) -> 'Front'")
def _():
    assert views.quat_to_label(views.VIEW_FRONT, "ORTHO") == "Front"


@check("Right (ORTHO) -> 'Right'")
def _():
    assert views.quat_to_label(views.VIEW_RIGHT, "ORTHO") == "Right"


# --- the Perspective pane: the mode is the discriminator -------------------
# The Perspective pane has its own 3/4 quaternion rather than duplicating
# Right. PERSP mode still authoritatively labels it
# 'Perspective' regardless of the quaternion -- keep that guarantee tested.
@check("Perspective (PERSP, 3/4 quat) -> 'Perspective'")
def _():
    assert views.quat_to_label(views.VIEW_PERSP, "PERSP") == "Perspective"
    # and the Perspective quaternion must be distinct from Right's, so the
    # two panes are not identical views.
    assert views.VIEW_PERSP != views.VIEW_RIGHT, "Perspective duplicates Right"


# --- sign invariance: q and -q are the same rotation ------------------------
@check("sign-invariant: -Top (ORTHO) -> 'Top'")
def _():
    neg = tuple(-c for c in views.VIEW_TOP)
    assert views.quat_to_label(neg, "ORTHO") == "Top"


@check("sign-invariant: -Front (ORTHO) -> 'Front'")
def _():
    neg = tuple(-c for c in views.VIEW_FRONT)
    assert views.quat_to_label(neg, "ORTHO") == "Front"


# --- a user-orbited ORTHO view that matches no standard view ---------------
@check("unrecognised ORTHO -> 'Custom'")
def _():
    # identity-ish rotation is not any of the documented views
    assert views.quat_to_label((1.0, 0.0, 0.0, 0.0), "ORTHO", tol=1e-9) == "Top"
    # a genuinely different orientation
    assert views.quat_to_label((0.9, 0.1, 0.1, 0.1), "ORTHO") == "Custom"


# --- tolerance: a small numerical drift still matches -----------------------
@check("tolerance: Top + tiny drift -> 'Top'")
def _():
    q = (1.0 + 1e-4, 1e-4, 0.0, 0.0)
    assert views.quat_to_label(q, "ORTHO") == "Top"


# --- camera zoom and target compatibility values ----------------------------
# Aiming the camera (view_rotation) is not enough: without a distance the panes
# kept the host runtime's default 18.388, about 8x too close, and the Perspective
# pane rendered as an extreme close-up. Pin the chosen compatibility values so a
# future edit cannot quietly undo the fix.
@check("Perspective camera distance remains 146.058")
def _():
    import math
    loc = (63.244, -109.544, 73.030)
    expected = math.sqrt(sum(c * c for c in loc))
    assert abs(views.PERSP_DISTANCE - expected) < 0.01, (
        f"PERSP_DISTANCE {views.PERSP_DISTANCE} != |{loc}| = {expected:.3f}")
    # It must not regress to the host runtime's much closer default.
    assert abs(views.PERSP_DISTANCE - 18.388) > 1.0, "still using host default zoom"


@check("ortho camera distance remains 31.494")
def _():
    assert abs(views.ORTHO_DISTANCE - 31.494) < 0.01, (
        f"ORTHO_DISTANCE {views.ORTHO_DISTANCE} != 31.494")


@check("all four default cameras aim at the world origin")
def _():
    assert tuple(views.VIEW_TARGET) == (0.0, 0.0, 0.0), (
        f"VIEW_TARGET {views.VIEW_TARGET} is not the origin")


@check("VIEW_PERSP reproduces the default view direction")
def _():
    # Rotate view-space -Z by the quaternion by hand (no bpy/mathutils here):
    # v' = q * (0,0,-1) * q^-1 for a unit quaternion.
    w, x, y, z = views.VIEW_PERSP
    norm = (w * w + x * x + y * y + z * z) ** 0.5
    assert abs(norm - 1.0) < 1e-4, f"VIEW_PERSP is not a unit quaternion ({norm})"
    # closed form for rotating (0,0,-1)
    vx = -2.0 * (x * z + w * y)
    vy = -2.0 * (y * z - w * x)
    vz = -(1.0 - 2.0 * (x * x + y * y))
    measured = (-0.4330, 0.7500, -0.5000)
    for got, want, axis in zip((vx, vy, vz), measured, "xyz"):
        assert abs(got - want) < 2e-3, (
            f"view direction {axis}: got {got:+.4f}, expected {want:+.4f}")
    # The Perspective pane must look down or a centre click
    # cannot hit the World-Top construction plane.
    assert vz < -0.2, f"Perspective view must look down; z={vz:+.4f}"


# ===========================================================================
# ACTIVE viewport title and sRGB-to-linear conversion
#
# Narwhal marks the active viewport by its title colour rather than a border.
# These values are part of the public UI design.
# ===========================================================================

@check("srgb_to_linear matches Blender's documented 0.34 -> 157")
def _():
    # The pitfall this encodes: Blender stores colours LINEAR and sRGB-encodes
    # them on output, so a colour measured off a screenshot must be converted.
    # The known-good datapoint from docs/: stored 0.34 renders as ~157.
    lin = views.srgb_to_linear(157)
    assert abs(lin - 0.34) < 0.005, f"srgb_to_linear(157)={lin:.4f}, want ~0.34"


@check("srgb_to_linear pins the endpoints")
def _():
    assert views.srgb_to_linear(0) == 0.0
    assert abs(views.srgb_to_linear(255) - 1.0) < 1e-12


@check("srgb_to_linear is monotonic and never brightens")
def _():
    prev = -1.0
    for byte in range(0, 256, 5):
        lin = views.srgb_to_linear(byte)
        assert lin > prev, f"not monotonic at {byte}"
        # sRGB encoding brightens, so linear must be <= the naive ratio
        assert lin <= byte / 255.0 + 1e-12, f"{byte}: {lin} > {byte / 255}"
        prev = lin


@check("the ACTIVE title gets a fill, an inactive one does not")
def _():
    active_fill, active_text = views.title_style(True)
    idle_fill, idle_text = views.title_style(False)
    assert active_fill is not None, "the active title must be filled"
    assert idle_fill is None, "only the active title is filled"
    assert active_text != idle_text, "active and inactive text must differ"


@check("the active fill is the measured blue, gpu-ready")
def _():
    fill, _ = views.title_style(True)
    want = views.gpu_rgba(views.ACTIVE_TITLE_FILL)
    assert fill == want, f"{fill} != {want}"
    # and it really is blue: b > g > r
    assert fill[2] > fill[1] > fill[0], f"not blue-dominant: {fill}"


@check("title colours are NOT linearised (gpu draws bypass colour management)")
def _():
    # This assertion is deliberately the OPPOSITE of what it first said. The
    # theme-colour rule (Blender stores linear, sRGB-encodes on output) does NOT
    # apply to immediate-mode gpu drawing: measured, a linearised fill rendered as
    # (42,93,255) instead of the intended (107,163,255). The rendered-pixel gate
    # in spike/ui_report.py caught it after this unit test had happily enshrined
    # the wrong assumption -- which is why the pixel gate exists.
    fill, _ = views.title_style(True)
    assert abs(fill[0] - 107 / 255.0) < 1e-9, \
        f"fill should be the raw byte ratio for gpu, got {fill[0]}"
    linear = views.srgb_to_linear(107)
    assert abs(fill[0] - linear) > 1e-6, "fill looks linearised (too dark to draw)"


@check("srgb_to_linear is still available and correct for THEME colours")
def _():
    # Not dead code: theme colours such as the viewport background do go through
    # Blender's colour management and must be linearised.
    assert abs(views.srgb_to_linear(157) - 0.34) < 0.005
    assert views.srgb_rgba((157, 163, 170))[0] == views.srgb_to_linear(157)


@check("all title channels are within 0..1")
def _():
    for is_active in (True, False):
        fill, text = views.title_style(is_active)
        for rgba in (fill, text):
            if rgba is None:
                continue
            assert all(0.0 <= c <= 1.0 for c in rgba), f"not 0-1: {rgba}"


@check("the active title text is DARK, as measured (not white as it appears)")
def _():
    # At display scale the light blue chip reads as white-on-blue; the pixel
    # measurement showed the glyphs are near-black (46,47,48). Guard the
    # counter-intuitive fact so nobody "corrects" it back to white. The bound is
    # in gpu byte-ratio space, so (46,47,48) -> ~0.18.
    _, text = views.title_style(True)
    assert max(text[:3]) < 0.25, f"active title text should be dark, got {text}"
    assert abs(text[0] - 46 / 255.0) < 1e-9, f"expected the measured 46, got {text[0]}"


# --- the viewport title's clickable rect ------------------------------------
#
# A click activates the viewport without deselecting objects. A double-click
# maximizes it or restores the multi-viewport layout.
#
# `title_rect` is the ONE source for both the chip that gets drawn and the box that
# gets hit-tested, so these tests are also what stops those two drifting apart.

@check("title_rect sits at the TOP-LEFT of the pane")
def _():
    x0, y0, x1, y1 = views.title_rect(60.0, 12.0, 400, label_pad=8)
    assert x0 == 8 - views.TITLE_CHIP_PAD_X, x0
    # glyphs grow UP from the baseline, so the baseline is a text height below the
    # top -- the box must therefore be near region_h, not near 0.
    assert y1 > 400 - 40, f"title should hug the top, got y1={y1}"
    assert y0 < y1 and x0 < x1, (x0, y0, x1, y1)


@check("title_rect is sized from the TEXT, so a short name gets a short chip")
def _():
    short = views.title_rect(40.0, 12.0, 400)
    long_ = views.title_rect(120.0, 12.0, 400)
    assert (long_[2] - long_[0]) > (short[2] - short[0]), (short, long_)
    assert (long_[2] - long_[0]) - (short[2] - short[0]) == 80.0, (short, long_)


@check("title_rect pads the text by the measured chip padding")
def _():
    x0, y0, x1, y1 = views.title_rect(50.0, 14.0, 300, label_pad=8)
    assert (x1 - x0) == 50.0 + 2 * views.TITLE_CHIP_PAD_X, (x1 - x0)
    assert (y1 - y0) == 14.0 + 2 * views.TITLE_CHIP_PAD_Y, (y1 - y0)


@check("hit_title accepts a point inside, including the border")
def _():
    rect = views.title_rect(60.0, 12.0, 400)
    x0, y0, x1, y1 = rect
    mid = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    assert views.hit_title(mid[0], mid[1], rect), "centre must hit"
    assert views.hit_title(x0, y0, rect), "the corner is ON the chip"
    assert views.hit_title(x1, y1, rect), "and so is the far corner"


@check("hit_title rejects points outside -- including just past each edge")
def _():
    rect = views.title_rect(60.0, 12.0, 400)
    x0, y0, x1, y1 = rect
    for px, py, why in ((x0 - 1, (y0 + y1) / 2, "left"),
                        (x1 + 1, (y0 + y1) / 2, "right"),
                        ((x0 + x1) / 2, y0 - 1, "below"),
                        ((x0 + x1) / 2, y1 + 1, "above")):
        assert not views.hit_title(px, py, rect), why
    # the middle of the viewport is emphatically not the title
    assert not views.hit_title(200, 200, rect), "pane centre must not hit"


@check("hit_title on a missing rect is False, not a crash")
def _():
    assert views.hit_title(10, 10, None) is False


# ---------------------------------------------------------------------------
# VIEWPORT TABS
#
# MEASURED: "Viewport tabs are located at the bottom of the main viewport frame by
# default. Each model and layout viewport has a tab." The ORDER is the interesting
# part and the part a re-typed list would lose -- see views.TAB_ORDER.
# ---------------------------------------------------------------------------

#: a fake glyph measurer: 7 px per character. Enough to assert the layout, and it
#: keeps this file free of `blf` -- the same trick `test_cmdbar` uses for options.
def _measure(label):
    return 7.0 * len(label)


@check("the authored tab order is not the layout order")
def _():
    assert views.TAB_ORDER == ("Perspective", "Top", "Front", "Right"), \
        views.TAB_ORDER
    # the 2x2 layout reads Top / Perspective / Front / Right; if someone "tidies"
    # the tab order to match the layout, this is the gate that objects
    assert views.TAB_ORDER[0] == "Perspective", \
        "Perspective leads the tabs in all three screenshots we hold"


@check("tab_labels puts the Add tab last, and only offers viewports that exist")
def _():
    labels = views.tab_labels()
    assert labels == ("Perspective", "Top", "Front", "Right", views.TAB_ADD), labels
    # a layout missing a pane must not offer a tab that goes nowhere
    two = views.tab_labels(present=["Top", "Perspective"])
    assert two == ("Perspective", "Top", views.TAB_ADD), two
    # ...and a viewport we have no measured position for is still REACHABLE,
    # appended rather than dropped -- an untabbed viewport is worse than an
    # unmeasured position
    odd = views.tab_labels(present=["Top", "Bottom"])
    assert odd == ("Top", "Bottom", views.TAB_ADD), odd


@check("tab_boxes lays tabs left to right, sized to their text")
def _():
    labels = ("Perspective", "Top")
    boxes = views.tab_boxes(labels, _measure, x0=100, gap=2, pad_x=10)
    assert [b[0] for b in boxes] == ["Perspective", "Top"], boxes
    # first starts at x0
    assert boxes[0][1] == 100, boxes
    # width follows the text: 11 chars * 7 + 2*10 = 97
    assert boxes[0][2] - boxes[0][1] == 97, boxes
    # the next begins after the gap
    assert boxes[1][1] == boxes[0][2] + 2, boxes
    # and a long name really is wider than a short one (fixed widths would be wrong)
    assert (boxes[0][2] - boxes[0][1]) > (boxes[1][2] - boxes[1][1]), boxes
    assert views.tab_boxes((), _measure) == []


@check("hit_tab is half-open, so abutting tabs cannot both claim a pixel")
def _():
    boxes = views.tab_boxes(("Top", "Front"), _measure, x0=0, gap=0, pad_x=10)
    a_end = boxes[0][2]
    assert views.hit_tab(0, boxes) == "Top"
    assert views.hit_tab(a_end - 1, boxes) == "Top"
    # the boundary pixel belongs to exactly ONE tab
    assert views.hit_tab(a_end, boxes) == "Front", views.hit_tab(a_end, boxes)
    assert views.hit_tab(-1, boxes) is None
    assert views.hit_tab(boxes[-1][2] + 1, boxes) is None
    assert views.hit_tab(5, []) is None
    assert views.hit_tab(5, None) is None


@check("cycle_tab wraps and SKIPS the Add tab (it is a button, not a viewport)")
def _():
    labels = views.tab_labels()          # includes "+"
    assert views.cycle_tab(labels, "Perspective", 1) == "Top"
    assert views.cycle_tab(labels, "Top", -1) == "Perspective"
    # from the LAST real tab, forward wraps to the first -- and must not land on "+"
    assert views.cycle_tab(labels, "Right", 1) == "Perspective"
    assert views.cycle_tab(labels, "Perspective", -1) == "Right"
    for cur in ("Perspective", "Top", "Front", "Right"):
        for d in (1, -1, 3, -3):
            assert views.cycle_tab(labels, cur, d) != views.TAB_ADD, (cur, d)
    # an unknown current tab still gives a sensible first step rather than None
    assert views.cycle_tab(labels, "Nonsense", 1) == "Perspective"
    assert views.cycle_tab(labels, "Nonsense", -1) == "Right"
    # nothing to cycle through
    assert views.cycle_tab((views.TAB_ADD,), "Top", 1) is None
    assert views.cycle_tab((), "Top", 1) is None
    assert views.cycle_tab(None, "Top", 1) is None


@check("the tab right-click menu is the 11 items the help lists, in order")
def _():
    labels = [l for l, _c in views.TAB_MENU]
    assert labels == ["New Viewport", "New Detail", "Delete", "Rename",
                      "Move or Copy", "Combined Model Tab", "Combined Layout Tab",
                      "Tab Orientation", "Hide Tabs", "Layout Properties",
                      "Print"], labels


@check("the + menu is the 5 items the help lists, and every one names a real command")
def _():
    labels = [l for l, _c in views.TAB_ADD_MENU]
    assert labels == ["New Layout", "Import Layout", "New Floating Viewport",
                      "Split Horizontal", "Split Vertical"], labels
    # All five map to commands; none may be left as a dead label.
    assert all(c for _l, c in views.TAB_ADD_MENU), views.TAB_ADD_MENU


@check("the tab band colour is the MEASURED InactiveTabBackground, not a guess")
def _():
    # Public design value for InactiveTabBackground: #D8D9DD.
    assert views.TAB_BAND_BG == (0xD8, 0xD9, 0xDD), views.TAB_BAND_BG
    assert views.TAB_ACTIVE_BG != views.TAB_BAND_BG, \
        "the active tab must be distinguishable from the band"


@check("the tab metrics are flagged OURS, because no source gives a pixel size")
def _():
    src = open(os.path.join(ROOT, "narwhal3d", "views.py")).read()
    lines = src.splitlines()
    idx = next(n for n, l in enumerate(lines) if l.startswith("TAB_H = "))
    above = "\n".join(lines[max(0, idx - 8):idx])
    assert "OURS" in above, (
        "the screenshot is at an unknown DPI scale, so no absolute tab pixel size "
        f"can be read off it -- say so. Got:\n{above}")


# ---------------------------------------------------------------------------
# THE VIEWPORT-TITLE CONTEXT MENU
# ---------------------------------------------------------------------------

_vspec = importlib.util.spec_from_file_location(
    "narwhal_viewport_menu",
    os.path.join(ROOT, "narwhal3d", "viewport_menu_data.py"))
assert _vspec is not None and _vspec.loader is not None
viewport_menu_data = importlib.util.module_from_spec(_vspec)
_vspec.loader.exec_module(viewport_menu_data)
_CAP = viewport_menu_data.MENUS[viewport_menu_data.MODEL_KIND]

_SYNTHETIC_NESTED = (
    ("item", "First", "", "first"),
    ("popup", "Group", "", (
        ("item", "Second", "", "second"),
        ("popup", "More", "", (
            ("item", "Third", "", "third"),
        )),
    )),
)


@check("strip_accel remains compatible with optional mnemonic text")
def _():
    assert views.strip_accel("&Maximize Viewport") == "Maximize Viewport"
    assert views.strip_accel("B&ack") == "Back"
    assert views.strip_accel("A && B") == "A & B"
    assert views.strip_accel("") == ""
    assert views.strip_accel(None) == ""


@check("menu_command_key still creates legal class-name fragments")
def _():
    assert views.menu_command_key("Maximize Viewport") == "maximizeviewport"
    assert views.menu_command_key("Grid Options...") == "gridoptions"
    assert views.menu_command_key("Pan, Zoom, and Rotate") == "panzoomandrotate"
    assert views.menu_command_key("") == ""
    assert views.menu_command_key(None) == ""


@check("the viewport menu groups display modes with implemented view actions")
def _():
    assert viewport_menu_data.KINDS == ("Viewport",)
    assert viewport_menu_data.MODEL_KIND == "Viewport"
    assert _CAP == (
        ("popup", "Display Mode", "", (
            ("item", views.DISPLAY_MODE_PLACEHOLDER, "", None),
        )),
        ("sep", "", "", None),
        ("item", "Zoom", "", None),
        ("item", "Construction Plane", "", None),
        ("item", "Grid Settings", "", None),
        ("sep", "", "", None),
        ("item", "Maximize Viewport", "", None),
    ), _CAP
    mode_rows = views.viewport_menu_rows(_CAP, ("Display Mode",))
    assert mode_rows == [("item", views.DISPLAY_MODE_PLACEHOLDER, "", None)]
    modes = tuple(name for name, _key, implemented, _shading in views.DISPLAY_MODES
                  if implemented)
    assert modes == ("Wireframe", "Shaded", "Rendered"), modes
    actions = [views.caption_action((), row[1]) for row in _CAP if row[0] == "item"]
    assert actions == ["Zoom", "CPlane", "Grid", views.ACTION_MAXIMIZE], actions
    assert views.caption_action((), "Unknown Action") is None


@check("the viewport menu carries no imported shortcuts, mnemonics, or numeric ids")
def _():
    pending = list(_CAP)
    while pending:
        kind, label, shortcut, payload = pending.pop(0)
        assert kind in {"item", "popup", "sep"}, (kind, label)
        assert shortcut == "", (label, shortcut)
        assert "&" not in label, label
        assert not isinstance(payload, int), (label, payload)
        if kind == "popup":
            assert isinstance(payload, tuple), (label, payload)
            pending[0:0] = list(payload)
        else:
            assert payload is None, (label, payload)
        if kind == "sep":
            assert label == "", label


@check("the viewport menu source has no imported-data provenance markers")
def _():
    path = os.path.join(ROOT, "narwhal3d", "viewport_menu_data.py")
    source = open(path, encoding="utf-8").read().casefold()
    forbidden = (
        "legacy_product_core",
        "." + "rui",
        "vendor/legacy-help",
        "gen_" + "viewport_menu",
        "trans" + "cribed",
    )
    assert not [marker for marker in forbidden if marker.casefold() in source]


@check("viewport_menu_rows preserves the authored row order")
def _():
    rows = views.viewport_menu_rows(_SYNTHETIC_NESTED)
    assert [row[1] for row in rows] == ["First", "Group"], rows


@check("viewport_menu_rows keeps nested popup lookup compatible")
def _():
    one = views.viewport_menu_rows(_SYNTHETIC_NESTED, ("Group",))
    assert [row[1] for row in one] == ["Second", "More"], one
    two = views.viewport_menu_rows(_SYNTHETIC_NESTED, ("Group", "More"))
    assert [row[1] for row in two] == ["Third"], two
    assert views.viewport_menu_rows(_SYNTHETIC_NESTED, ("Nope",)) == []
    assert views.viewport_menu_rows(_SYNTHETIC_NESTED, ("Group", "Nope")) == []
    assert views.viewport_menu_rows((), ("Group",)) == []


@check("collapse_menu_separators drops leading, doubled and trailing separators")
def _():
    sep = ("sep", "", "", None)
    first = ("item", "First", "", "first")
    second = ("item", "Second", "", "second")
    rows = views.collapse_menu_separators((sep, sep, first, sep, sep, second, sep))
    assert rows == (first, sep, second), rows
    assert views.collapse_menu_separators(()) == ()
    assert views.collapse_menu_separators(None) == ()
    assert views.collapse_menu_separators((sep, sep)) == ()


# ---------------------------------------------------------------------------
# THE OSNAP PANEL
#
# The ROW ORDER is the measured fact and the reason this exists: the panel used to
# draw `osnap.PERSISTENT`, which is the order of a SENTENCE in the help, not of any
# control. The authored UI order is pinned here so it cannot regress.
# ---------------------------------------------------------------------------

@check("the Osnap panel keeps its authored control order")
def _():
    assert views.OSNAP_PANEL_ORDER == (
        "End", "Near", "Point", "Mid", "Cen", "Int", "Perp", "Tan",
        "Quad", "Knot", "Vertex", "Project", "Disable"), views.OSNAP_PANEL_ORDER
    # the bug this replaces: the help's prose list is ALPHABETICAL, and sorting is
    # exactly what made the old panel wrong
    modes = [m for m in views.OSNAP_PANEL_ORDER
             if m not in views.OSNAP_PANEL_FLAGS]
    assert modes != sorted(modes), "the panel order must not be alphabetical"
    assert modes[0] == "End", modes
    # Project and Disable are the last two rows, in that order, in the SAME list
    assert views.OSNAP_PANEL_ORDER[-2:] == ("Project", "Disable")


@check("every panel row is either a persistent snap or a named flag -- nothing else")
def _():
    for label in views.OSNAP_PANEL_ORDER:
        assert (label in osnap.PERSISTENT or label in views.OSNAP_PANEL_FLAGS), label
    # Every persistent snap Narwhal supports has a row; a missing one would be a
    # snap the user could never switch on
    for mode in osnap.PERSISTENT:
        assert mode in views.OSNAP_PANEL_ORDER, mode


@check("osnap_panel_boxes lays rows downward from the title, first label on top")
def _():
    labels = ("End", "Near", "Point")
    boxes = views.osnap_panel_boxes(labels, x0=0, y_bottom=100)
    assert [b[0] for b in boxes] == list(labels), boxes
    # top row really is highest on screen (Blender's y grows upward)
    assert boxes[0][2] > boxes[1][2] > boxes[2][2], boxes
    # rows are contiguous and one row high
    for a, b in zip(boxes, boxes[1:]):
        assert a[2] == b[4], (a, b)
        assert a[4] - a[2] == views.OSNAP_ROW_H, a
    # The panel sits on y_bottom and uses the authored dock-band width.
    total = views.osnap_panel_height(len(labels))
    assert boxes[-1][2] >= 100, boxes
    assert boxes[0][4] <= 100 + total, (boxes[0], total)
    assert boxes[0][3] - boxes[0][1] == views.OSNAP_PANEL_W, boxes[0]
    widened = views.osnap_panel_boxes(labels, x0=0, y_bottom=100, width=217)
    assert all(b[3] - b[1] == 217 for b in widened), widened
    assert views.osnap_panel_boxes((), 0, 0) == []


@check("osnap_panel_height grows with the row count and counts the title")
def _():
    one = views.osnap_panel_height(1)
    two = views.osnap_panel_height(2)
    assert two - one == views.OSNAP_ROW_H, (one, two)
    assert views.osnap_panel_height(0) > 0, "the title still takes space"
    assert views.osnap_panel_height(13) > views.osnap_panel_height(3)


@check("hit_osnap_panel_row is half-open, so stacked rows cannot both claim a pixel")
def _():
    boxes = views.osnap_panel_boxes(("End", "Near"), x0=0, y_bottom=0)
    _l, x0, y0, x1, y1 = boxes[0]
    assert views.hit_osnap_panel_row(x0 + 2, y0, boxes) == "End"
    assert views.hit_osnap_panel_row(x0 + 2, y1 - 1, boxes) == "End"
    # the shared boundary belongs to exactly ONE row
    assert views.hit_osnap_panel_row(x0 + 2, y1, boxes) is None, "above the top row"
    assert views.hit_osnap_panel_row(x0 + 2, y0 - 1, boxes) == "Near"
    # outside the band's width is not a hit, which is what keeps the rest of the
    # viewport clickable
    assert views.hit_osnap_panel_row(x1 + 1, y0, boxes) is None
    assert views.hit_osnap_panel_row(-1, y0, boxes) is None
    assert views.hit_osnap_panel_row(0, 0, []) is None
    assert views.hit_osnap_panel_row(0, 0, None) is None


@check("the panel colours are MEASURED and its metrics are flagged OURS")
def _():
    # Public design values: body #E9EBEF, white box interior, borders #C0C0C0.
    assert views.OSNAP_PANEL_BG == (0xE9, 0xEB, 0xEF), views.OSNAP_PANEL_BG
    assert views.OSNAP_BOX_BG == (0xFF, 0xFF, 0xFF), views.OSNAP_BOX_BG
    assert views.OSNAP_PANEL_BORDER == (0xC0, 0xC0, 0xC0), views.OSNAP_PANEL_BORDER
    # Pin the public dock-band width.
    assert views.OSNAP_PANEL_W == 117, views.OSNAP_PANEL_W
    src = open(os.path.join(ROOT, "narwhal3d", "views.py")).read()
    lines = src.splitlines()
    idx = next(n for n, l in enumerate(lines) if l.startswith("OSNAP_PANEL_W = "))
    above = "\n".join(lines[max(0, idx - 8):idx])
    assert "OURS" in above, (
        "the row height and box size cannot be read off a screenshot at an unknown "
        f"DPI scale -- say so. Got:\n{above}")
    # The tick colour is an authored value rather than an imported claim.
    tidx = next(n for n, l in enumerate(lines) if l.startswith("OSNAP_TICK = "))
    assert "OURS" in "\n".join(lines[max(0, tidx - 4):tidx]), (
        "no screenshot shows a ticked box, so the tick colour is ours")


# --- the title's drop-down arrow --------------------------------------------
#
# MEASURED that the menu exists and hangs off the title: commands/print.htm --
# "...or on the **viewport title menu**, click Print Preview" -- and
# commands/setmaximizedviewport.htm -- "Or **Right-click the viewport title** and
# click Maximize." The arrow's size and shape are OURS.

_RECT = views.title_rect(80.0, 14.0, 500.0)


@check("the arrow box sits immediately right of the title chip")
def _():
    box = views.title_arrow_rect(_RECT)
    assert box is not None
    # it starts exactly where the chip ends -- no gap, no overlap, so a click can
    # never be ambiguous between the two
    assert box[0] == _RECT[2], (box, _RECT)
    assert box[2] > box[0], box


@check("and shares the chip's vertical extent, so it lines up with the text")
def _():
    box = views.title_arrow_rect(_RECT)
    assert (box[1], box[3]) == (_RECT[1], _RECT[3]), (box, _RECT)


@check("the visible title control includes the arrow button")
def _():
    box = views.title_arrow_rect(_RECT)
    control = views.title_menu_rect(_RECT)
    assert control == (_RECT[0], _RECT[1], box[2], _RECT[3]), (control, box)
    assert views.title_menu_rect(None) is None


@check("the arrow is large enough to read but remains a compact control")
def _():
    box = views.title_arrow_rect(_RECT)
    assert 14.0 <= box[2] - box[0] <= 18.0, box
    assert views.TITLE_ARROW_HALF >= 4.0 and views.TITLE_ARROW_H >= 4.0


@check("the arrow and the title chip do NOT overlap")
def _():
    box = views.title_arrow_rect(_RECT)
    # a point in the middle of the arrow is not on the chip, and vice versa
    ax = (box[0] + box[2]) / 2.0
    ay = (box[1] + box[3]) / 2.0
    assert views.hit_title_arrow(ax, ay, _RECT), "arrow centre misses the arrow"
    assert not views.hit_title(ax, ay, _RECT), "arrow centre also hit the chip"
    cx = (_RECT[0] + _RECT[2]) / 2.0
    cy = (_RECT[1] + _RECT[3]) / 2.0
    assert views.hit_title(cx, cy, _RECT), "chip centre misses the chip"
    assert not views.hit_title_arrow(cx, cy, _RECT), "chip centre also hit the arrow"


@check("the triangle points DOWN and fits inside its box")
def _():
    tri = views.title_arrow_triangle(_RECT)
    box = views.title_arrow_rect(_RECT)
    assert tri is not None and len(tri) == 3, tri
    # two points level at the top, one below them -> it points down
    ys = sorted(p[1] for p in tri)
    assert ys[1] == ys[2], f"two vertices should share the top edge: {tri}"
    assert ys[0] < ys[1], f"the apex must be BELOW the top edge: {tri}"
    for x, y in tri:
        assert box[0] <= x <= box[2], f"{(x, y)} outside {box} in x"
        assert box[1] <= y <= box[3], f"{(x, y)} outside {box} in y"


@check("the triangle is horizontally centred in its box")
def _():
    tri = views.title_arrow_triangle(_RECT)
    box = views.title_arrow_rect(_RECT)
    mid = (box[0] + box[2]) / 2.0
    assert abs(tri[2][0] - mid) < 1e-9, (tri, mid)


@check("a longer viewport name pushes the arrow right, not through the text")
def _():
    short = views.title_arrow_rect(views.title_rect(30.0, 14.0, 500.0))
    long_ = views.title_arrow_rect(views.title_rect(120.0, 14.0, 500.0))
    assert long_[0] > short[0], (short, long_)
    # and it stays the same size -- the arrow is a fixed cell, not a stretched one
    assert (short[2] - short[0]) == (long_[2] - long_[0])


@check("None in, None out -- a pane too short to have a title has no arrow")
def _():
    assert views.title_arrow_rect(None) is None
    assert views.title_arrow_triangle(None) is None
    assert views.hit_title_arrow(5.0, 5.0, None) is False


@check("the arrow's size constants are flagged OURS")
def _():
    src = open(os.path.join(ROOT, "narwhal3d", "views.py"),
               encoding="utf-8").read()
    lines = src.splitlines()
    idx = next(n for n, l in enumerate(lines) if l.startswith("TITLE_ARROW_W = "))
    near = "\n".join(lines[max(0, idx - 6):idx])
    assert "OURS" in near, "the arrow's pixel sizes must be flagged OURS"


# ---------------------------------------------------------------- report
print(f"\n{'=' * 54}")
print(" view and viewport-control tests (pure, bpy-free)")
print(f"{'=' * 54}")
widths = max(len(n) for n, _, _ in results)
for name, status, detail in results:
    mark = "OK " if status == "PASS" else "FAIL"
    print(f" [{mark}] {name.ljust(widths)}  {detail}")

fails = [r for r in results if r[1] != "PASS"]
print(f"\n {len(results) - len(fails)}/{len(results)} passed")
sys.exit(1 if fails else 0)
