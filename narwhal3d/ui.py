"""CAD-style UI for Blender: top command bar + left tool palette.

Layout strategy (verified against Blender 5.2):
- the reference CAD application's command window sits at the TOP of the window (measured:
  the measured default layout, ~95 px, pure white). In the GUI we carve a full-width
  INFO strip along the top and build the four viewports (2x2) below it.
  Under `blender -b` (where `area_close` hangs) the command bar falls back to
  the default bottom strip converted to INFO -- a documented, test-only
  difference (no headless test asserts the bar's position).
- Our GPU-drawn the reference CAD application command line (history + prompt + input) is hosted in that INFO area.
- A modal operator captures keyboard input for the command line.
- The VIEW_3D TOOLS region hosts a CAD-style tool palette panel.
- Viewport theme: medium blue-grey background (measured from real the reference CAD application 8),
  very low-contrast grid, pure-white command area — like the reference CAD application's default.
"""
from __future__ import annotations

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import Operator, Panel

from .commands import COMMANDS
# The module itself (not just COMMANDS): `_wants_point` reads
# `commands.CLICKABLE_KINDS` so the modal and the command layer cannot disagree
# about which prompts a viewport click can answer.
from . import commands
# the options framework. `prompt_segments` labels each piece of the
# rendered prompt so the bar's option text can be hit-tested and clicked.
from . import options as opts
from .state import app_state
# Pure command-bar layout arithmetic (no bpy) so it is unit-testable: draw
# handlers never fire under -b, which is how the prompt-off-screen bug hid.
from . import cmdbar
# standard construction planes + the pure ray/plane intersection live in
# the bpy-free `cplane` module so the picking math is unit-testable without
# Blender (spike/test_cplane.py).
from . import cplane
# turning a WORLD point back into text a command will read as world (not as
# CPlane) coordinates. Lives in the bpy-free `coords` module next to the parser it
# must round-trip with, so the invariant is unit-tested (spike/test_coords.py).
from . import coords
from . import pickconstraint
from . import ortho
from . import printdialog
from . import bugreport
from . import textfield
from . import sessionlog
from . import uiscale
from . import alias_table
from . import print2d
# the reference CAD application's OBJECT SELECTION rules -- Shift adds, Ctrl removes, window vs crossing.
# Blender's conventions differ on every one of those, so the semantics are ours
# and live in a bpy-free module (spike/test_select.py).
from . import select
# Optional Rust acceleration only projects display segments. It returns None when
# unavailable or invalid so selection retains the exact Python/cache path below.
from . import native_selection
# SUB-OBJECT selection (Ctrl+Shift+click). A different chord and a different rule
# from object selection -- the same chord both selects and deselects, so it
# TOGGLES -- which is why it is not folded into `select.py`.
from . import subobject
# The status bar's 14 panes, their kinds, and their text form a pure model
# (`spike/test_statusbar.py`); this file renders that model.
from . import statusbar as sbar
# Menu and toolbar compatibility data are committed as pure modules so drawing does
# not depend on external source bundles at runtime.
from . import menubar
from . import toolbars as tbar
# How the reference CAD application draws POINTS -- point objects and control points. A point has no
# extent, so it needs a fixed pixel-size marker rather than the line preview; the
# marker shapes are pure and bpy-free (spike/test_pointdisplay.py).
from . import pointdisplay
# the reference CAD application's view-navigation rules (which button/modifier does what, and how
# that depends on the pane's projection) live in the bpy-free `navigate` module
# so every row of the table is unit-testable without Blender
# (spike/test_navigate.py).
from . import navigate as nav
# view quaternions + the pure quaternion->label mapping live in the
# bpy-free `views` module so the label logic is unit-testable without Blender.
# Re-exported here for existing callers (tests, _assign_four_views).
# the pure preview module -- what wireframe to draw while a command runs.
# bpy-free so every command's preview is unit-tested without Blender
# (spike/test_preview.py).
from . import preview
# object snaps. The snap RULES (which point does the marker jump to, given
# these objects/cursor/modes/CPlane) are pure and live in `osnap`, unit-tested by
# spike/test_osnap.py; `bridge.snap_targets` adapts real rhino3dm geometry into the
# neutral records it consumes (spike/test_osnap_bridge.py). This module only
# supplies the cursor, converts the reference CAD application's PIXEL snap radius to world units, and
# draws the marker.
from . import osnap
# `bridge.snap_targets` adapts the rhino3dm document into the neutral snap
# records `osnap` consumes. (Imported here as well as in `commands`, since the
# snap query happens on mouse-move in this module.)
from . import bridge
from . import grid_settings as grid_store
# the Gumball transform widget. Its placement, drawable geometry,
# hit-testing and drag math are all pure and live in `gumball` (unit-tested by
# spike/test_gumball.py); this module only projects its polylines, feeds mouse
# numbers in, and turns the answers into document transforms.
from . import gumball
# view module, imported whole as well as by name: the viewport title's
# rectangle lives there (`views.title_rect`) so the drawn chip and the clickable
# chip are the same arithmetic.
from . import views
from . import viewport_menu_data
from . import rightpanel
from .object_properties import ObjectProperties
from .views import (
    VIEW_TOP, VIEW_FRONT, VIEW_RIGHT, VIEW_PERSP,
    PERSP_DISTANCE, ORTHO_DISTANCE, VIEW_TARGET,
    quat_to_label, title_style, _quat_close,
)

# Label text for the pane corner. Dark like the wireframe, a touch lighter
# than the selection colour so it reads on the near-white viewport.
LABEL_COLOR = (0.16, 0.16, 0.16, 1.0)
LABEL_FONT_SIZE = 13.0
LABEL_PAD = 8

# The dynamic command preview's line colour. the reference CAD application draws the in-progress
# curve in the layer/draw colour, which on the default layer is black; a slightly
# lifted near-black reads clearly against the measured viewport grey
# (157,163,170) without looking like finished geometry. `gpu` draws are NOT
# sRGB-encoded (see views.gpu_rgba), so this is a plain byte ratio.
PREVIEW_COLOR = (0.1, 0.1, 0.1, 1.0)

# The object-snap marker is a small constant-size box with the snap's name beside it.
# Its saturated application-defined colour remains legible against the measured
# viewport grey (157,163,170) and distinct from the near-black wireframe/preview
# (~26). `gpu`/`blf` draws are NOT sRGB-encoded (see `views.gpu_rgba`), so these are
# plain byte ratios.
SNAP_MARKER_COLOR = (0.80, 0.10, 0.40, 1.0)      # ~(204, 26, 102)
SNAP_LABEL_COLOR = (0.80, 0.10, 0.40, 1.0)
SNAP_MARKER_PX = 7          # half-width of the marker box, in pixels
SNAP_LABEL_FONT_SIZE = 12.0


# ---------------------------------------------------------------------------
# colors + viewport sampling (the reference CAD application 8 light look)
# ---------------------------------------------------------------------------

# Blender was left at its factory 8-sample viewport AA. Curves/Gumball use custom AA,
# but every shaded solid silhouette still showed stair-steps. Blender 5.2 exposes 32 as
# its highest multisample mode; use it globally for the reference CAD application's smooth object edges.
VIEWPORT_AA = "32"

CMD_BG = (1.0, 1.0, 1.0, 1.0)            # command area: pure white (measured 255,255,255)
CMD_TEXT = (0.12, 0.12, 0.12, 1.0)       # current input / prompt (dark, reads on white)
CMD_HISTORY = (0.35, 0.35, 0.36, 1.0)    # history lines (medium gray on white)
CMD_CURSOR = (0.85, 0.25, 0.15, 1.0)     # caret (the reference CAD application-ish red)
# command-line options are clickable buttons in the reference CAD application, so they are drawn in
# a link-like blue to say so, and the autocomplete candidate list needs its own
# colours. The first candidate is the one Enter/Tab completes to, so it is
# highlighted the way the reference CAD application highlights the selected entry.
CMD_OPTION = (0.05, 0.26, 0.63, 1.0)     # option text: clickable (blue)
CMD_COMPLETION = (0.12, 0.12, 0.12, 1.0)  # candidate command name
CMD_COMPLETION_SEL = (1.0, 1.0, 1.0, 1.0)  # the selected candidate's text
CMD_COMPLETION_HL = (0.16, 0.38, 0.75, 1.0)  # the selected candidate's bar
CMD_COMPLETION_BG = (0.93, 0.94, 0.96, 1.0)  # the candidate list's backdrop
# ---------------------------------------------------------------------------
# Compatibility viewport palette: a MEDIUM BLUE-GREY viewport --
# NOT near-white -- with a very low-contrast grid (minor lines only ~10/255
# darker than the background). The command area is pure white, so the dark
# text colours above read with full contrast.
#
# ⚠ COLOR SPACE: the values below are LINEAR, not raw sRGB measurements.
# Blender stores shading/theme colours in linear space and applies the sRGB EOTF when
# rendering: rendered_srgb = 1.055*linear^(1/2.4) - 0.055. Confirmed against two
# data points: linear 0.98 -> rendered 252, and linear 0.616 -> rendered 205.8
# (observed 205). Storing the sRGB-normalised measurement directly therefore renders
# too light. Each constant is ((srgb/255 + 0.055)/1.055)^2.4 for the measured target.
# Live-window checks assert rendered pixels; headless checks can only assert the
# stored linear values.
# ---------------------------------------------------------------------------
# NOTE the arity: in this Blender build `grid`/`grid_major` are RGBA (4-tuple)
# but `object_selected`/`object_active`/`wire` are RGB (3-tuple). Assigning a
# 4-tuple to an RGB property raises, so each constant matches its target.
VIEWPORT_BG = (0.337, 0.366, 0.402, 1.0) # measured (157,163,170) sRGB, linear
GRID_COLOR = (0.292, 0.318, 0.352, 1.0)  # measured (147,153,160) sRGB, linear -- subtle
GRID_MAJOR = (0.220, 0.238, 0.262, 1.0)  # measured (129,134,140) sRGB, linear
SELECT_COLOR = (1.0, 0.85, 0.0)          # the reference CAD application's yellow selection highlight (RGB)
WIRE_COLOR = (0.0, 0.0, 0.0)             # black wireframes (RGB)

# Reference-only world-axis colours: X is muted red (150,75,75), Y is muted green
# (75,150,75). Blender's `view_3d` theme exposes no per-axis colour property, only
# `grid_axis_brightness`, so the renderer controls the displayed axis colours and
# these values cannot be applied.
AXIS_X = (0.588, 0.294, 0.294)           # (150,75,75) muted red -- not themeable
AXIS_Y = (0.294, 0.588, 0.294)           # (75,150,75) muted green -- not themeable

# Text in a GPU draw handler is drawn with the `blf` module, NOT with a
# bpy.data.fonts datablock. `bpy.data.fonts.load` returns a VectorFont, which
# is a 3D-text datablock with no `.draw` -- using it raised
# "AttributeError: 'VectorFont' object has no attribute 'draw'" on every frame.
# font_id 0 is Blender's built-in UI font and always exists, so there is nothing
# to load or cache.
_DEFAULT_FONT_ID = 0

# Verified blf signatures on Blender 5.2 (probe them with wrong arity to print):
#  blf.size(font_id, size)     blf.color(font_id, r, g, b, a)
#  blf.position(font_id, x, y, z)  blf.draw(font_id, text)
#  blf.dimensions(font_id, text) -> (w, h)


def _draw_tri(shader, points, color):
    """Fill a triangle with `color` using an already-bound shader.

    Its own helper rather than three calls to `_draw_rect`, because the viewport
    title's drop-down arrow is the first genuinely triangular thing we draw and a
    stepped approximation out of rectangles would look like an artefact at 7 px.
    """
    try:
        from gpu_extras.batch import batch_for_shader
    except Exception:  # noqa: BLE001
        return
    if not points or len(points) != 3:
        return
    batch = batch_for_shader(shader, "TRIS", {"pos": list(points)})
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_rect(shader, x0, y0, x1, y1, color):
    """Fill an axis-aligned rectangle with `color` using an already-bound shader.

    Shared by the command bar's background and the active viewport title's chip
     so there is one definition of "fill a rect" rather than a copy per
    draw handler.
    """
    try:
        from gpu_extras.batch import batch_for_shader
    except Exception:  # noqa: BLE001
        return
    quad = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    batch = batch_for_shader(shader, "TRIS",
                             {"pos": [quad[0], quad[1], quad[3],
                                      quad[1], quad[2], quad[3]]})
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_rects(shader, rects, color):
    """`_draw_rect` for MANY rectangles of one colour, in ONE batch. Same pixels as
    drawing them one by one (they are all the same colour, so their order among
    themselves cannot show)."""
    if not rects:
        return
    try:
        from gpu_extras.batch import batch_for_shader
    except Exception:  # noqa: BLE001
        return
    pos = []
    for x0, y0, x1, y1 in rects:
        pos += [(x0, y0), (x1, y0), (x0, y1), (x1, y0), (x1, y1), (x0, y1)]
    batch = batch_for_shader(shader, "TRIS", {"pos": pos})
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_text(x, y, text, size=13.0, color=(0, 0, 0, 1), font_id=_DEFAULT_FONT_ID):
    """Draw a single line of text at pixel (x, y) in the current region."""
    import blf

    blf.size(font_id, size)
    blf.color(font_id, *color)
    blf.position(font_id, x, y, 0)
    blf.draw(font_id, text)


def _text_width(text, size=13.0, font_id=_DEFAULT_FONT_ID) -> float:
    """Measured width of `text`, for placing the caret after the input."""
    import blf

    blf.size(font_id, size)
    return blf.dimensions(font_id, text)[0]


# ---------------------------------------------------------------------------
# theme + layout
# ---------------------------------------------------------------------------

def _set_color(obj, name, value):
    """Set a theme color attribute if it exists on this Blender build.

    rhino3dm taught us never to assume an API; the same applies to theme attrs,
    which differ across versions. A missing one must not break startup.
    """
    try:
        setattr(obj, name, value)
    except Exception:  # noqa: BLE001
        pass


def _set_attr(obj, name, value):
    """Set one attribute, reporting failure instead of swallowing it.

    Deliberately per-attribute: the old code set shading type, background type,
    background colour and five overlay toggles inside ONE try/except, so the
    first failure silently skipped everything after it. That is exactly what
    happened — `background_color` is a 3-float property and we handed it a
    4-tuple, so the viewport kept Blender's dark default AND none of the overlay
    settings were applied.
    """
    try:
        setattr(obj, name, value)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] theme: could not set {name} = {value!r}: {e}")
        return False


def suppress_region_toggle_chips():
    """Remove Blender's collapsed-region chevrons.

    These are the small arrow chips Blender draws at a pane edge to reopen a hidden
    Sidebar or tool header. Narwhal 3D does not use them because those regions are
    intentionally hidden. Blender 5.2.2 exposes the controlling application flag:

        source/blender/editors/screen/area.cc:1299  (region_azone_edge_poll)
            if (is_hidden && (U.app_flag & USER_APP_HIDE_REGION_TOGGLE)) {
              return false;      <- no action zone is created, so no chip is drawn
            }

        source/blender/makesrna/intern/rna_userdef.cc:7598
            prop = RNA_def_property(srna, "show_regions_visibility_toggle", ...)
            RNA_def_property_boolean_negative_sdna(prop, nullptr, "app_flag",
                                                   USER_APP_HIDE_REGION_TOGGLE);

    The flag is exposed on `preferences.apps`. It is negative: setting the property
    to False sets `USER_APP_HIDE_REGION_TOGGLE` and suppresses the chips. The property
    is writable in the bundled 5.2.2 runtime, so no custom build is required.

    Returns True when the flag was applied. Reports and returns False otherwise
    rather than failing silently, because a Blender that drops this property would
    bring the chips back and the only symptom would be cosmetic.
    """
    apps = getattr(bpy.context.preferences, "apps", None)
    if apps is None or not hasattr(apps, "show_regions_visibility_toggle"):
        print("[narwhal3d] this runtime cannot suppress collapsed-region chips")
        return False
    try:
        # NEGATIVE flag: False == USER_APP_HIDE_REGION_TOGGLE set == no chips.
        apps.show_regions_visibility_toggle = False
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] could not suppress region toggle chips: {e!r}")
        return False
    return not apps.show_regions_visibility_toggle


def region_toggle_chips_suppressed():
    """True when the collapsed-region chevrons are suppressed (for the gate)."""
    apps = getattr(bpy.context.preferences, "apps", None)
    if apps is None or not hasattr(apps, "show_regions_visibility_toggle"):
        return False
    return not apps.show_regions_visibility_toggle


def apply_narwhal_theme():
    """Make the viewport look like the reference CAD application's default light scene.

    Every attribute is set independently and reported on failure, so a missing or
    re-typed attribute on some Blender build can neither break startup nor silently
    skip the rest of the theme.
    """
    try:
        bpy.context.preferences.system.viewport_aa = VIEWPORT_AA
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] could not set {VIEWPORT_AA}-sample viewport anti-aliasing: {e!r}")
    try:
        v3d = bpy.context.preferences.themes[0].view_3d
    except Exception:  # noqa: BLE001
        return

    # Grid + selection + wireframe colors (the the reference CAD application look).
    # NOTE the differing arity, verified via bl_rna.array_length:
    #  grid / grid_major -> 4 floats (RGBA)
    #  object_selected / object_active / wire -> 3 floats (RGB)
    _set_color(v3d, "grid", GRID_COLOR)
    _set_color(v3d, "grid_major", GRID_MAJOR)
    _set_color(v3d, "object_selected", SELECT_COLOR)
    _set_color(v3d, "object_active", SELECT_COLOR)
    _set_color(v3d, "wire", WIRE_COLOR)

    # The 3D view background. Two independent mechanisms have to agree:
    # * the THEME gradient (used when shading.background_type == 'THEME')
    # * the per-space shading colour (used when it is 'VIEWPORT')
    # Set both so the viewport is the measured the reference CAD application blue-grey no matter which
    # path is active.
    try:
        grad = v3d.space.gradients
        _set_attr(grad, "background_type", "SINGLE_COLOR")
        _set_color(grad, "high_gradient", VIEWPORT_BG[:3])
    except Exception:  # noqa: BLE001
        pass

    screen = getattr(bpy.context, "screen", None)
    if screen is None:
        return  # headless: no areas to configure
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        sp = next((s for s in area.spaces if s.type == "VIEW_3D"), None)
        if sp is None:
            continue
        _set_attr(sp.shading, "type", "SOLID")
        _set_attr(sp.shading, "background_type", "VIEWPORT")
        # 3 floats, NOT 4 -- passing VIEWPORT_BG whole raises ValueError
        _set_attr(sp.shading, "background_color", VIEWPORT_BG[:3])
        # the reference CAD application shows the ground grid + world axes (X red, Y green) and no
        # object-origin clutter. Blender draws those axes in the same red/green
        # convention as the reference CAD application, so we only toggle them on.
        # Blender's floor, ortho grid and axis lines are INFINITE. the reference CAD application's
        # grid is finite and lies on each pane's CPlane, so ours (`draw_cplane_grid`,
        # rules in grid.py) replaces all of them.
        _set_attr(sp.overlay, "show_floor", False)
        _set_attr(sp.overlay, "show_ortho_grid", False)
        _set_attr(sp.overlay, "show_axis_x", False)
        _set_attr(sp.overlay, "show_axis_y", False)
        _set_attr(sp.overlay, "show_axis_z", False)
        _set_attr(sp.overlay, "show_object_origins", False)


def _find_bottom_strip():
    """The default bottom strip (dopesheet) we convert into the command bar."""
    for a in bpy.context.screen.areas:
        if a.type == "DOPESHEET_EDITOR":
            return a
    # fallback: any short, wide area at the bottom
    best = None
    for a in bpy.context.screen.areas:
        if a.width > 800 and a.height < 150 and a.type not in ("VIEW_3D", "INFO"):
            best = a
    return best


# ---------------------------------------------------------------------------
# Four-viewport layout + top command bar
# ---------------------------------------------------------------------------
#
# the reference CAD application opens with four equal panes (below a top command bar):
#   Top     Perspective
#   Front    Right
#
# The command area sits at the TOP, approximately 95 px tall and pure white.
# Blender 5.2 runtime checks established the full close + carve-top + convert + 2x2
# sequence and the following constraints:
#  * `area.type = "VIEW_3D"` converts a PROPERTIES/OUTLINER area in BOTH -b and GUI.
#  * `bpy.ops.screen.area_split` works in both; HORIZONTAL -> top/bottom halves,
#   VERTICAL -> left/right halves. `factor` is measured from the BOTTOM, so a
#   LARGE factor (~0.90-0.94) carves a thin strip off the TOP (a small factor
#   lands it at the bottom). A small-factor split in the SAME tick is a 0x0
#   artifact that settles over reflow ticks, so keep one action per tick.
#  * `bpy.ops.screen.area_close` HANGS under `blender -b` and only works in the GUI
#   when done ONE close per reflow tick (two closes in one block raise 'Area set
#   with window & screen set to None').
#  * `bpy.ops.screen.area_join` silently does nothing in this build -- do not use.
#  * A reflow can leave `bpy.context.screen` momentarily None between ticks;
#   guard against it and re-fire rather than crashing.
# Consequence: the balanced 2x2 + top command bar (which needs closing the right
# sidebar AND the bottom strip to make the main viewport full width and height)
# is a GUI-only, multi-tick sequence. Under -b we build a testable four-viewport
# layout WITHOUT area_close: convert the right column to viewports and split the
# main viewport once. Both yield the SAME logical 2x2 and the same view
# assignment (top-left=Top, bottom-left=Front, top-right=Perspective,
# bottom-right=Right), so the headless test and the GUI agree. The one
# difference: the command bar is TOP in the GUI, BOTTOM under -b (headless
# cannot close the strips, and no headless test asserts its position).
#
# (The VIEW_TOP/FONT/RIGHT/PERSP constants and quat_to_label are imported
# from the bpy-free `views` module at the top of this file, where they are
# re-exported for existing callers.)


def _window_region(area):
    """The area's main (WINDOW, alignment NONE) region, or None."""
    for r in area.regions:
        if r.type == "WINDOW" and r.alignment == "NONE":
            return r
    return None


def _view3d_space(area):
    for s in area.spaces:
        if s.type == "VIEW_3D":
            return s
    return None


def _assign_view(area, quat, perspective, axis=None):
    """Set a viewport's orientation, mode, zoom and target. Returns True on success.

    `view_rotation` only AIMS the camera. Without also setting the zoom
    (`view_distance`) and target (`view_location`) the pane keeps Blender's
    default distance of 18.388 -- roughly 8x too close -- which rendered the
    Perspective pane as an extreme close-up with the grid filling it. the reference CAD application's
    measured cameras all aim at the origin, at 146.058 (Perspective) and 31.494
    (the three ortho panes). See narwhal3d/views.py.

    `axis` ("TOP"/"FRONT"/"RIGHT") additionally drives
    ``bpy.ops.view3d.view_axis``, which is the ONLY way to get Blender to draw the
    ortho construction grid. Measured (grid-band density of the Front pane):

        view_rotation = VIEW_FRONT              0.000  no grid
        + is_orthographic_side_view = True      0.000  no grid (the setter reads
                                                       back True and draws nothing)
        bpy.ops.view3d.view_axis(type="FRONT")  0.039  grid, matching Top's 0.0385

    Blender only draws that grid when it believes the view is axis-aligned, and
    assigning the quaternion directly never sets that internal state. the reference CAD application draws
    a grid in every viewport because the grid IS the construction plane
    (the reference CAD application documentation), so this is required for fidelity, not polish.

    The quaternion assignment is KEPT as a fallback: the operator needs a real
    region and can fail under ``blender -b``, where  headless
    assertions still depend on the rotations being correct.
    """
    sp = _view3d_space(area)
    if sp is None:
        return False
    try:
        r3d = sp.region_3d
        r3d.view_rotation = quat
        r3d.view_perspective = perspective
        r3d.view_location = VIEW_TARGET
        r3d.view_distance = (PERSP_DISTANCE if perspective == "PERSP"
                             else ORTHO_DISTANCE)
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] could not set view on {area.type}: {e!r}")
        return False

    if axis is not None:
        _apply_view_axis(area, quat)
    return True


def _apply_view_axis(area, quat):
    """Re-set an ortho pane through ``view3d.view_axis`` so its grid draws.

    Needs the area AND its WINDOW/NONE region in the override, and re-applies the
    the reference CAD application target/zoom afterwards because the operator resets them. Any failure is
    non-fatal: the quaternion already assigned by the caller stands, which is what
    keeps the headless suites green.

    `quat` is passed only to verify afterwards that the operator left the pane
    pointing where we asked -- if it did not, we restore the quaternion rather
    than leave the pane silently mis-aimed.
    """
    axis_by_quat = {
        tuple(round(c, 4) for c in VIEW_TOP): "TOP",
        tuple(round(c, 4) for c in VIEW_FRONT): "FRONT",
        tuple(round(c, 4) for c in VIEW_RIGHT): "RIGHT",
    }
    axis = axis_by_quat.get(tuple(round(c, 4) for c in quat))
    if axis is None:
        return
    region = next((r for r in area.regions
                   if r.type == "WINDOW" and r.alignment == "NONE"), None)
    if region is None:
        return
    sp = _view3d_space(area)
    if sp is None:
        return
    before = tuple(sp.region_3d.view_rotation)
    try:
        with bpy.context.temp_override(area=area, region=region):
            bpy.ops.view3d.view_axis(type=axis)
        # the operator resets target and zoom, so restore the reference CAD application's measured values
        sp.region_3d.view_location = VIEW_TARGET
        sp.region_3d.view_distance = ORTHO_DISTANCE
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] view_axis({axis}) unavailable, keeping quaternion: {e!r}")
        return
    # If the operator aimed the pane somewhere else, put it back: a correct grid
    # on the wrong view is worse than the right view with no grid.
    if not _quat_close(tuple(sp.region_3d.view_rotation), quat):
        print(f"[narwhal3d] view_axis({axis}) changed the orientation; reverting")
        sp.region_3d.view_rotation = before


def _hide_viewport_chrome(area):
    """Strip Blender's per-viewport chrome (headers/toolbar) for a clean pane.

    Each attribute is guarded independently; a missing one on some build must not
    abort the rest. `show_region_asset_shelf` is deliberately NOT touched -- it is
    read-only in this build and raising on it would be a spurious failure.
    """
    sp = _view3d_space(area)
    if sp is None:
        return
    for name in ("show_region_header", "show_region_tool_header",
                 "show_region_toolbar", "show_region_ui"):
        _set_attr(sp, name, False)


def _show_panel_region(area):
    """Show `area`'s UI region, which hosts the Osnap and Layers panels.

    `UI` (Blender's N-panel) is the only VIEW_3D region that hosts Python Panels;
    showing `TOOLS` instead gives Blender's icon-only toolbar. The region sits on the
    right, so it cannot host a bottom-left Osnap panel without a custom GPU strip.
    """
    sp = _view3d_space(area)
    if sp is None:
        return False
    ok = _set_attr(sp, "show_region_ui", True)
    # Open on OUR tab. The region also carries Blender's own categories (Tool, View,
    # Animation) and defaults to "Tool", so without this the panel opens on
    # Blender's tool settings and ours look absent.
    for region in area.regions:
        if region.type == "UI":
            try:
                region.active_panel_category = "Osnap"
            except Exception:  # noqa: BLE001
                pass            # read-only on some builds; the tab is still there
            break
    return ok


def _viewport_count():
    return sum(1 for a in bpy.context.screen.areas if a.type == "VIEW_3D")


def _assign_four_views(panes):
    """Assign the the reference CAD application default views to the four panes, by position.

    Panes are laid out as two columns. We split them into left/right columns by
    x (the two smallest-x panes are the left column, the two largest-x are the
    right column), then within each column the LARGER-y pane is the top one
    (Blender's screen y grows upward, so the top of the screen has the larger
    y coordinate).

    This is deliberately position-based rather than reference-based: bpy area
    proxies are not identity-stable across ``screen.areas`` accesses (``a is b``
    is unreliable), so matching panes across snapshots would silently misassign.
    Positions are deterministic in both -b and GUI.
    """
    if len(panes) < 4:
        # Not the expected four -- assign what we can, left-to-right, top-to-bottom.
        # Screen y grows upward, so top-to-bottom means DESCENDING y.
        for area, (quat, persp, axis) in zip(
                sorted(panes, key=lambda a: (a.x, -a.y)),
                [(VIEW_TOP, "ORTHO", "TOP"), (VIEW_FRONT, "ORTHO", "FRONT"),
                 (VIEW_PERSP, "PERSP", None), (VIEW_RIGHT, "ORTHO", "RIGHT")]):
            _assign_view(area, quat, persp, axis=axis)
        return

    # Split into the two columns by x, then order each column top->bottom by y
    # (Blender's y grows upward, so larger y = higher on screen = top pane).
    by_x = sorted(panes, key=lambda a: a.x)
    left = sorted(by_x[:2], key=lambda a: -a.y)     # top-left, bottom-left
    right = sorted(by_x[2:], key=lambda a: -a.y)    # top-right, bottom-right
    top_left, bot_left = left[0], left[1]
    top_right, bot_right = right[0], right[1]

    _assign_view(top_left, VIEW_TOP, "ORTHO", axis="TOP")       # Top (top-left)
    _assign_view(bot_left, VIEW_FRONT, "ORTHO", axis="FRONT")   # Front (bottom-left)
    _assign_view(top_right, VIEW_PERSP, "PERSP")                # Perspective (top-right)
    _assign_view(bot_right, VIEW_RIGHT, "ORTHO", axis="RIGHT")  # Right (bottom-right)


def _finish_viewports(panes):
    """Shared tail for both layout paths: assign views, strip chrome, hide status bar."""
    _assign_four_views(panes)
    for a in panes:
        _hide_viewport_chrome(a)
        # A freshly split pane starts with Blender's default SOLID settings, which can
        # omit solid edges until the display mode is explicitly applied.
        # with the edge overlay OFF (show_wireframes False, threshold 1). Only
        # `set_display_mode` turned the edges on, so they appeared after a mode switch.
        try:
            set_display_mode(a, display_mode_of(a) or "Shaded")
        except Exception as e:  # noqa: BLE001
            print(f"[narwhal3d] could not set up {a!r}'s display mode: {e!r}")
    # the panel region is NO LONGER re-opened on the rightmost pane. It used to
    # be, to host `NARWHAL_PT_layers` -- "which is where the reference CAD application's Right Container lives".
    # That was an approximation of the Right Container using a Blender N-panel, and it
    # produced unwanted vertical category tabs because Blender draws one for every
    # registered `bl_category`. The right dock is
    # now REAL chrome in its own dock band, so the panels are unregistered and
    # this must not re-open an empty sidebar on top of a viewport. `_hide_viewport_chrome`
    # above already turns `show_region_ui` off; re-opening it here undid that for one
    # pane, which is why exactly one pane still showed the chips.
    # the reference CAD application HAS a status bar -- "located at the bottom of the the reference CAD application window ...
    # displays the current coordinate system, the current location and delta of
    # the cursor, and the status bar panes" (the reference CAD application documentation). So
    # the strip is kept and its CONTENT replaced (see `_install_statusbar`), which
    # is why this no longer hides it. Blender's own status bar content is gone
    # either way.
    try:
        bpy.context.screen.show_statusbar = True
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] could not show the status bar: {e!r}")


def _build_viewports_headless():
    """Build a testable four-viewport layout WITHOUT area_close (which hangs in -b).

    Converts the right column (PROPERTIES + OUTLINER) to viewports and splits the
    main viewport once, giving a 2x2: top-left, bottom-left, top-right, bottom-right.
    Synchronous, so the headless test can call setup_layout once and inspect the
    result. (Verified: yields exactly 4 VIEW_3D + the correct quaternion set.)
    """
    for a in list(bpy.context.screen.areas):
        if a.type in ("PROPERTIES", "OUTLINER"):
            a.type = "VIEW_3D"

    v3d = [a for a in bpy.context.screen.areas if a.type == "VIEW_3D"]
    main = min(v3d, key=lambda a: a.x) if v3d else None
    if main is not None:
        reg = _window_region(main)
        if reg is not None:
            try:
                with bpy.context.temp_override(area=main, region=reg):
                    bpy.ops.screen.area_split(direction="HORIZONTAL", factor=0.5)
            except Exception as e:  # noqa: BLE001
                print(f"[narwhal3d] viewport split failed: {e!r}")

    all_v = [a for a in bpy.context.screen.areas if a.type == "VIEW_3D"]
    _finish_viewports(all_v)


def _build_viewports_gui():
    """Build the the reference CAD application layout in the GUI: a TOP command bar above a balanced 2x2.

    The command area is a full-width strip along the TOP of the window (the reference CAD application's
    layout), so we first close the right sidebar AND the bottom strip to make
    the main viewport full width and height, then carve a thin top strip from it
    and convert that strip to INFO (the command-bar host). Only then do we split
    the remaining area into a 2x2. area_close and area_split each need their own
    reflow tick to settle (one action per tick), so this is a multi-tick state
    machine, not a single block. Verified end to end in a focused runtime check.
    """
    state = {"tick": 0, "nones": 0, "wait": False, "retries": 0, "waits": 0}
    # The command area is 95 logical pixels high; `uiscale` scales every carved size.
    TARGET_BAR_PX = round(95 * uiscale.current())
    # Reserve 117 logical pixels for the left dock. Sidebar and Osnap each occupy
    # half its height, and the viewports begin to its right.
    TARGET_BAND_PX = round(117 * uiscale.current())
    # The right dock is one full-height tabbed panel; see `rightpanel.py`.
    TARGET_RIGHT_BAND_PX = rightpanel.BAND_W

    def _close_strip(ttype):
        for a in bpy.context.screen.areas:  # fresh re-fetch each tick
            if a.type == ttype:
                reg = _window_region(a)
                if reg is None:
                    return False
                try:
                    with bpy.context.temp_override(area=a, region=reg):
                        bpy.ops.screen.area_close()
                    return True
                except Exception as e:  # noqa: BLE001
                    print(f"[narwhal3d] close {ttype} failed: {e!r}")
                    return False
        return False

    def _main_view3d():
        v3d = [a for a in bpy.context.screen.areas if a.type == "VIEW_3D"]
        return max(v3d, key=lambda a: a.width * a.height) if v3d else None

    def _split(area, direction, factor=0.5):
        reg = _window_region(area)
        if reg is None:
            return
        with bpy.context.temp_override(area=area, region=reg):
            bpy.ops.screen.area_split(direction=direction, factor=factor)

    # how long a tick waits before the next one.
    #
    # This was a flat 0.4 s, which made the twelve-step build cost
    # 0.2 + 11 x 0.4 = 4.6 seconds of avoidable startup delay.
    #
    # Verified (a focused runtime check): after an `area_split`, the new area
    # reports `width 0` in the SAME pass (which is what a focused runtime check found
    # and why this is a tick machine at all) -- but on the NEXT callback with the timer
    # asking for **0.0**, both areas are settled with non-zero widths. One yield to the
    # event loop is enough, and Blender's own pacing makes that ~120 ms rather than 0.
    #
    # Returning 0.0 is only SAFE because every dependent step below now VERIFIES its
    # precondition and re-fires instead of proceeding (see `_retry`). Before, a step
    # whose predecessor had not settled printed "not found where expected" and carried
    # on with a broken layout; the 0.4 s was a guess that it would not come to that.
    TICK_INTERVAL = 0.0
    #: How many times one step may re-fire before we give up on it. At ~120 ms a
    #: callback this is ~6 s of patience for a single reflow, far more than any
    #: measured one needs, and it still cannot hang the app.
    MAX_RETRIES = 50

    def _top_strip():
        """The thin strip we carved from the top = the shortest VIEW_3D near top."""
        v3d = [a for a in bpy.context.screen.areas if a.type == "VIEW_3D"]
        if not v3d:
            return None
        top_y = max(a.y for a in v3d)
        near_top = [a for a in v3d if a.y + a.height >= top_y - 100]
        return min(near_top, key=lambda a: a.height) if near_top else None

    def _retry(what):
        """Stay on this step and re-fire. Returns the RETRY sentinel.

        Counts attempts so a precondition that never becomes true gives up loudly
        instead of spinning forever -- and names the step, because this machine is
        twelve steps long and "setup stalled" on its own sends you reading all of it.
        """
        state["retries"] += 1
        state["waits"] += 1
        if state["retries"] > MAX_RETRIES:
            print(f"[narwhal3d] viewport setup step {state['tick']} gave up waiting "
                  f"for: {what}")
            return False          # stop waiting; fall through with what we have
        return True               # wait another callback

    def tick():
        global _viewports_in_progress
        # A close/split reflow can leave `bpy.context.screen` momentarily None
        # between ticks (ops that need it would raise AttributeError); re-fire
        # with a cap rather than crash -- proven to recover (a focused runtime check).
        try:
            ok = bpy.context.screen is not None
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            state["nones"] += 1
            if state["nones"] > 12:
                print("[narwhal3d] viewport setup aborted: screen stayed None")
                _viewports_in_progress = False
                return None
            return TICK_INTERVAL
        state["nones"] = 0
        # only ADVANCE when the previous step's effect is visible. `state["wait"]`
        # is set by a step that found its precondition unmet; it keeps the step number
        # unchanged so the same branch runs again on the next callback.
        if state["wait"]:
            state["wait"] = False
        else:
            state["tick"] += 1
            state["retries"] = 0
        t = state["tick"]
        try:
            if t == 1:
                _close_strip("PROPERTIES")
            elif t == 2:
                _close_strip("OUTLINER")
            elif t == 3:
                _close_strip("DOPESHEET_EDITOR")
                m = _main_view3d()
                if m:
                    print(f"[narwhal3d] main full size {m.width}x{m.height}")
            elif t == 4:
                m = _main_view3d()
                if m:
                    factor = max(0.5, min(0.99, 1.0 - TARGET_BAR_PX / max(1, m.height)))
                    print(f"[narwhal3d] carving top command bar (factor={factor:.3f})")
                    _split(m, "HORIZONTAL", factor)
            elif t == 5:
                strip = _top_strip()
                if strip is None or strip.height >= 200:
                    # the t=4 split has not reflowed yet. WAIT for it. This used
                    # to print "no thin top strip found to convert" and carry on, which
                    # left the command bar as a VIEW_3D -- the flat 0.4 s sleep was the
                    # only thing making that unlikely.
                    state["wait"] = _retry("the top command-bar strip to appear")
                    if state["wait"]:
                        return TICK_INTERVAL
                    print("[narwhal3d] no thin top strip found to convert")
                else:
                    reg = _window_region(strip)
                    if reg is not None:
                        with bpy.context.temp_override(area=strip, region=reg):
                            bpy.ops.screen.space_type_set_or_cycle(space_type="INFO")
                        print(f"[narwhal3d] top strip -> INFO (h={strip.height})")
            elif t == 6:
                # carve the LEFT DOCK BAND off the main viewport. The factor
                # convention for a lopsided VERTICAL split is NOT measurable from
                # outside this machine -- a focused runtime check tried and the
                # new area reports width 0 until it has reflowed -- so this splits
                # here and the NEXT tick reads what it actually got. Do not turn this
                # into a predicted factor.
                m = _main_view3d()
                if m and m.width > TARGET_BAND_PX * 3:
                    factor = TARGET_BAND_PX / float(m.width)
                    print(f"[narwhal3d] carving left dock band (factor={factor:.4f} "
                          f"of {m.width} px)")
                    _split(m, "VERTICAL", factor)
                else:
                    print("[narwhal3d] window too narrow for the left dock band")
            elif t == 7:
                # Identify the band by GEOMETRY, not by assuming which side the split
                # put it on: the narrowest VIEW_3D that is also the leftmost. If the
                # split landed the narrow piece elsewhere, say so and carry on with a
                # four-pane layout rather than converting the wrong area.
                v3d = [a for a in bpy.context.screen.areas if a.type == "VIEW_3D"]
                band = min(v3d, key=lambda a: a.width) if v3d else None
                leftmost = min(v3d, key=lambda a: a.x) if v3d else None
                ok = (band is not None and band is leftmost
                      and band.width < TARGET_BAND_PX * 2)
                if ok:
                    reg = _window_region(band)
                    if reg is not None:
                        with bpy.context.temp_override(area=band, region=reg):
                            bpy.ops.screen.space_type_set_or_cycle(
                                space_type="INFO")
                        print(f"[narwhal3d] left band -> INFO (w={band.width})")
                        _set_left_band_key(band)
                else:
                    # WAIT for the t=6 split instead of abandoning the band.
                    state["wait"] = _retry("the left dock band to appear")
                    if state["wait"]:
                        return TICK_INTERVAL
                    print("[narwhal3d] left band not found where expected "
                          f"(narrowest={getattr(band, 'width', None)}, "
                          f"leftmost={getattr(leftmost, 'width', None)}) "
                          "-- continuing without it")
            elif t == 8:
                # Carve the RIGHT DOCK BAND. As with the left band, measure the result
                # instead of predicting it: split here, then read what it
                # actually got on the next tick.
                m = _main_view3d()
                if m and m.width > TARGET_RIGHT_BAND_PX * 3:
                    factor = 1.0 - TARGET_RIGHT_BAND_PX / float(m.width)
                    print(f"[narwhal3d] carving right dock band (factor={factor:.4f} "
                          f"of {m.width} px)")
                    _split(m, "VERTICAL", factor)
                else:
                    print("[narwhal3d] window too narrow for the right dock band")
            elif t == 9:
                # Mirror of tick 7: the narrowest VIEW_3D that is also the RIGHTMOST.
                # Identified by geometry rather than by assuming which side the split
                # left the small piece on -- that is not knowable from outside this
                # machine (a focused runtime check).
                v3d = [a for a in bpy.context.screen.areas if a.type == "VIEW_3D"]
                band = min(v3d, key=lambda a: a.width) if v3d else None
                rightmost = (max(v3d, key=lambda a: a.x + a.width) if v3d else None)
                ok = (band is not None and band is rightmost
                      and band.width < TARGET_RIGHT_BAND_PX * 2)
                if ok:
                    reg = _window_region(band)
                    if reg is not None:
                        with bpy.context.temp_override(area=band, region=reg):
                            bpy.ops.screen.space_type_set_or_cycle(
                                space_type="INFO")
                        print(f"[narwhal3d] right band -> INFO (w={band.width})")
                        _set_right_band_key(band)
                else:
                    # WAIT for the t=8 split, as on the left.
                    state["wait"] = _retry("the right dock band to appear")
                    if state["wait"]:
                        return TICK_INTERVAL
                    print("[narwhal3d] right band not found where expected "
                          f"(narrowest={getattr(band, 'width', None)}, "
                          f"rightmost={getattr(rightmost, 'width', None)}) "
                          "-- continuing without it")
            elif t == 10:
                m = _main_view3d()
                if m:
                    _split(m, "VERTICAL", 0.5)
            elif t == 11:
                cols = sorted((a for a in bpy.context.screen.areas
                               if a.type == "VIEW_3D"), key=lambda a: a.x)
                if len(cols) >= 2:
                    _split(min(cols, key=lambda a: a.x), "HORIZONTAL", 0.5)
                    _split(max(cols, key=lambda a: a.x), "HORIZONTAL", 0.5)
                else:
                    # the t=10 column split has not reflowed. Waiting here is
                    # what stops a one-column layout being split into two rows and
                    # finishing as a 1x2 instead of a 2x2.
                    state["wait"] = _retry("the two viewport columns to appear")
                    if state["wait"]:
                        return TICK_INTERVAL
                    print(f"[narwhal3d] only {len(cols)} column(s) to split into rows")
            elif t == 12:
                panes = [a for a in bpy.context.screen.areas if a.type == "VIEW_3D"]
                if len(panes) < 4:
                    # wait for the row splits before assigning the four views,
                    # or Top/Perspective/Front/Right land on the wrong panes.
                    state["wait"] = _retry("all four viewports to appear")
                    if state["wait"]:
                        return TICK_INTERVAL
                    print(f"[narwhal3d] finishing with only {len(panes)} viewport(s)")
                _finish_viewports(panes)
                # The top INFO area only exists from this tick on (created at
                # t == 5), so the command-bar header can only be hidden now --
                # setup_layout's synchronous call already no-oped (no INFO yet).
                # hid this header; it now carries the reference CAD application's toolbar, so it is
                # SHOWN with our content instead of hidden with Blender's.
                _show_cmdbar_header()
                # report how many extra callbacks were spent WAITING for a
                # reflow. This is the evidence that the verify-and-retry is
                # load-bearing rather than decorative: at TICK_INTERVAL = 0.0 the
                # machine genuinely does find steps whose predecessor has not settled,
                # and it waits for them instead of building a broken layout -- which is
                # what the old flat 0.4 s sleep was guessing about.
                print(f"[narwhal3d] layout built in {state['tick']} steps "
                      f"+ {state['waits']} reflow wait(s)")
        except Exception as e:  # noqa: BLE001
            # The tick NUMBER matters and used to be missing. This machine is twelve
            # steps long and every step touches the screen, so "a tick failed" on its
            # own sends you reading all twelve -- which is exactly what happened when
            # added two (and added two more).
            print(f"[narwhal3d] viewport setup tick {t} failed: {e!r}")
        # The sequence is a one-action-per-reflow-tick state machine: each
        # intermediate tick must return a re-fire interval or the timer stops and
        # the build halts mid-sequence (the original bug this replaces). Only the
        # final tick returns None (and clears the in-progress guard, which stays
        # set on every intermediate tick so a concurrent setup_layout call cannot
        # start a second, over-splitting build).
        if t >= 12:
            _viewports_in_progress = False
            return None
        return TICK_INTERVAL  # re-fire for the next tick

    bpy.app.timers.register(tick, first_interval=TICK_INTERVAL)


_viewports_in_progress = False


def setup_viewports():
    """Build the four the reference CAD application viewports: Top, Perspective, Front, Right.

    Dispatches on mode: the GUI gets a top command bar + balanced 2x2 (multi-tick,
    uses area_close); headless gets a testable four-viewport layout (synchronous, no
    area_close, which hangs under `blender -b`). Both produce the same logical 2x2
    and view assignment; the command bar is TOP in the GUI, BOTTOM under -b.
    Idempotent: does nothing if four viewports already exist or a build is in flight.
    """
    global _viewports_in_progress
    if bpy.context.screen is None:
        return
    if _viewport_count() >= 4:
        return  # already laid out
    if _viewports_in_progress:
        return  # a GUI build is already running; don't start a second one

    if bpy.app.background:
        _build_viewports_headless()
    else:
        _viewports_in_progress = True
        _build_viewports_gui()


def _setup_command_bar():
    """Ensure a command-bar INFO area exists (idempotent).

    In the GUI the GUI viewport build  already carved a full-width TOP
    strip and converted it to INFO, so this is normally a no-op there. Under
    `blender -b` area_close hangs, so the viewport build keeps the bottom strip
    and converts THAT to INFO instead -- the documented headless fallback.
    """
    for a in bpy.context.screen.areas:
        if a.type == "INFO":
            return  # already set up (GUI top strip, or a previous headless pass)

    if not bpy.app.background:
        # GUI without a strip (e.g. a non-default start layout): the top strip
        # is the GUI-only route, so leave it to the viewport build rather than
        # inventing a bottom bar that would clash with it.
        return

    strip = _find_bottom_strip()
    if strip is None:
        return
    region = _window_region(strip)
    if region is None:
        return
    with bpy.context.temp_override(area=strip, region=region):
        try:
            bpy.ops.screen.space_type_set_or_cycle(space_type="INFO")
        except Exception as e:  # noqa: BLE001
            print(f"[narwhal3d] command bar layout failed: {e!r}")


def _hide_cmdbar_header():
    """Remove the leftover Blender `View | Info` header above the command bar.

    Route 1 (primary): `space.show_region_header = False` -- the same pattern as
    `_hide_viewport_chrome`, so it is the consistent choice and works headless
    (the property exists on the INFO space; a focused runtime check). Route 2
    (fallback): the `region_toggle` operator, used only if route 1 is missing
    or fails on some build. Each route is guarded independently so neither can
    abort the other or the rest of layout setup.
    """
    info = _cmdbar_area()
    if info is None:
        return
    sp = next((s for s in info.spaces if s.type == "INFO"), None)
    if sp is None:
        return

    route1 = False
    if hasattr(sp, "show_region_header"):
        route1 = _set_attr(sp, "show_region_header", False)
    else:
        print("[narwhal3d] INFO space has no show_region_header; trying region_toggle")

    # The region list is only meaningful in a live window (headless shows a
    # degenerate 1x1 header that is not really "visible"), so only bother with
    # the operator fallback when route 1 was unavailable.
    if not route1:
        hdr = next((r for r in info.regions if r.type == "HEADER"), None)
        if hdr is None:
            return
        try:
            with bpy.context.temp_override(area=info, region=hdr):
                bpy.ops.screen.region_toggle(region_type="HEADER")
        except Exception as e:  # noqa: BLE001
            print(f"[narwhal3d] could not hide command bar header: {e!r}")


def setup_layout():
    """Build the full the reference CAD application layout: four viewports + a TOP command bar.

    Idempotent: safe to call repeatedly (the startup timer and the tests both do).
    In the GUI the viewport build carries the top command bar ; the command
    bar step below only fills in what the build did not produce (the headless
    bottom-strip fallback).
    """
    if bpy.context.screen is None:
        return  # headless with no screen: nothing to lay out
    setup_viewports()
    _setup_command_bar()
    # The command area's header is SHOWN, because it carries the reference CAD application's toolbar strip
    # now. hid it when it still held Blender's "View | Info" menus.
    _show_cmdbar_header()


# ---------------------------------------------------------------------------
# command bar drawing (GPU)
# ---------------------------------------------------------------------------

def _info_area_is_band(area):
    """True when this INFO area is a DOCK BAND rather than the command bar.

    ONE discriminator, read by everything that has to tell the INFO areas apart:
    `_cmdbar_area`, `draw_left_band`, `draw_right_band`, `left_band_area` and
    `right_band_area`. They were separate tests for about five minutes and that was
    long enough to break every command-bar gate at once -- `_cmdbar_area` returned
    whichever INFO area came first in `screen.areas`, often returning a dock band.

    A band is TALL AND NARROW (measured 114 x 862 in a 1920x1006 window) and the
    command bar is WIDE AND SHORT (1916 x 92), so the aspect ratio separates them
    with an enormous margin and needs no stored identity -- which matters, because
    area references do not survive a reflow.

    there are now THREE INFO areas, because the right dock band is one too,
    and it is tall and narrow as well. So this answers "is it a band", and
    `_band_side` answers "which one" -- do not try to make this one call do both.
    """
    return area is not None and area.height > area.width


def _band_side(area):
    """``"left"`` or ``"right"`` for a dock band, or None if it is not one.

    Decided by which half of the WINDOW the band sits in, not by remembering which
    was carved first: area references do not survive a reflow and the carve order is
    an implementation detail, while "the left band is on the left" cannot drift.

    This exists because `_info_area_is_band` is true of BOTH bands, so every draw
    handler and lookup that used it alone would have treated the right band as the
    left one and painted the Sidebar and Osnap panel into it.
    """
    if not _info_area_is_band(area):
        return None
    try:
        screen = bpy.context.screen
        total = max(a.x + a.width for a in screen.areas)
    except Exception:  # noqa: BLE001
        total = 0
    if total <= 0:
        return "left"
    return "right" if (area.x + area.width * 0.5) > total * 0.5 else "left"


def _cmdbar_area():
    for a in bpy.context.screen.areas:
        if a.type == "INFO" and not _info_area_is_band(a):
            return a
    return None


def redraw_cmdbar():
    area = _cmdbar_area()
    if area is not None:
        area.tag_redraw()
    # the autocomplete list is drawn in the VIEWPORTS, so every path that
    # repaints the command line must repaint them too or the list goes stale. This was
    # caught as a GHOST: a screenshot taken after a command started still showed the
    # candidate list, because that path tagged only the bar. Putting it here rather
    # than at each call site is the point -- there are a dozen callers and one of them
    # would always be forgotten. Tagging is just setting a flag.
    _tag_redraw_viewports()


def _tag_redraw_viewports():
    """Repaint every 3D pane and dock band.

    This includes the LEFT DOCK BAND. The Osnap panel moved out of
    the bottom-left viewport and into the band, so every path that changes a snap and
    then repaints "the viewports" would otherwise leave the panel stale -- which is
    exactly what happened: the panel drew correctly but its recorded hit-boxes went
    empty, because nothing had asked the band to redraw since the state changed.

    Done HERE rather than at each caller deliberately. There are many callers and the
    panel is chrome that belongs with the viewports conceptually; one extra
    `tag_redraw` is cheap, and the alternative is remembering this at every future
    call site.
    """
    import bpy

    screen = getattr(bpy.context, "screen", None)
    if screen is None:
        return
    for area in screen.areas:
        if (area.type == "VIEW_3D"
                or (area.type == "INFO" and _info_area_is_band(area))):
            area.tag_redraw()
    # re-assert each LAYER's On/Lock. This belongs here because
    # `rebuild_display` recreates every Blender object and resets its hide flags, so a
    # layer switched off would come back on the moment the user drew anything. Every
    # path that changes the document already calls this function, and
    # `apply_layer_state` early-outs when no layer is off or locked, so the common case
    # costs one scan of a short list. See that function for why it is not a hook in
    # `commands.py` instead.
    try:
        apply_layer_state()
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] layer state not applied: {e!r}")


# ---------------------------------------------------------------------------
# what the command bar last DREW, so a click can be mapped back to it.
#
# Hit-boxes must come from the draw pass, because only there are the real glyph
# widths known (blf). They are recorded here and read by the modal's click
# handler. Stale boxes are harmless: a click is only acted on when it still maps
# to a live option/candidate, and every state change redraws the bar.
# ---------------------------------------------------------------------------

_option_boxes = []        # [(option_name, x0, x1)] in bar-region coords
_option_line_y = 0        # the y of the prompt line those boxes sit on
_completion_rows = []     # [(command_name, y)] in bar-region coords
_completion_index = 0     # which candidate the highlight points at (row index)


def _set_option_boxes(boxes, line_y=0):
    global _option_boxes, _option_line_y
    _option_boxes = list(boxes or [])
    _option_line_y = line_y


def _set_completion_rows(rows):
    global _completion_rows
    _completion_rows = list(rows or [])


def _set_completion_index(index):
    global _completion_index
    _completion_index = int(index)


def get_completion_index():
    """Which candidate row the highlight points at (for tests/introspection)."""
    return _completion_index


def get_option_boxes():
    """The option hit-boxes the last draw produced (for tests/introspection)."""
    return list(_option_boxes)


def get_completion_rows():
    """The candidate rows the last draw produced (for tests/introspection)."""
    return list(_completion_rows)


def _click_command_bar(win_x, win_y):
    """Map a window-space click onto the command bar's buttons.

    Returns ``None`` when the click was not on the bar at all (the caller should
    go on to try the viewport), ``True`` when it activated an option or a
    candidate, and ``False`` when it landed on the bar but on nothing clickable.
    Both ``True`` and ``False`` are "consume it": a click on blank command-bar
    space does nothing in the reference CAD application, and letting it through would hand it to
    Blender's own Info editor underneath.
    """
    area = _cmdbar_area()
    if area is None:
        return None
    if not (area.x <= win_x < area.x + area.width
            and area.y <= win_y < area.y + area.height):
        return None                 # not our bar
    region = next((r for r in area.regions if r.type == "WINDOW"), None)
    if region is None:
        return None
    rx = win_x - region.x
    ry = win_y - region.y

    # 0. the TOOLBAR TAB ROW, across the top of this region. Tested FIRST because it
    #  is chrome sitting on top of the bar: the history lines run underneath it, and
    #  in the reference CAD application clicking a tab switches toolbars rather than doing whatever the bar
    #  would have done.
    tab = tbar.hit_tab_row(rx, ry, _toolbar_tab_boxes, _toolbar_tab_row_top)
    if tab is not None:
        if set_toolbar_tab(tab):
            app_state.log(f"{tab} toolbar")
        redraw_cmdbar()
        return True

    # 1. an autocomplete candidate? (only drawn when no command is running)
    #  A click on the fuzzy DIVIDER falls through here for free: its row name is
    #  `None`, so `hit_completion` returns None and the click is consumed doing
    #  nothing -- which is right, the blank line is not a command.
    name = cmdbar.hit_completion(ry, _completion_rows, cmdbar.LINE_H)
    if name is not None:
        app_state.input_buffer = ""
        app_state.history.append("> " + name)
        app_state.feed_line(name)
        return True

    # 2. an option on the prompt line? Require the click to be on that line
    #  vertically as well, so a click far above cannot trigger an option.
    if _option_line_y - 2 <= ry < _option_line_y + cmdbar.LINE_H:
        opt_name = cmdbar.hit_option(rx, _option_boxes)
        if opt_name is not None:
            line = _option_click_line(app_state.active_command, opt_name)
            app_state.history.append("> " + line)
            app_state.feed_line(line)
            return True
    return False


def _option_click_line(cmd, opt_name):
    """Return what clicking option `opt_name` types.

    A click on a two-choice option flips it directly using `Name=Other` --
    like a Yes/No toggle. Typing the bare name still asks for the value (the typed
    `Orientation` then `Portrait` form must keep working), and longer lists still ask."""
    from . import options as _opts
    for o in getattr(cmd, "options", None) or ():
        if o.name == opt_name and o.kind == _opts.ENUM and len(o.choices or ()) == 2:
            cur = (getattr(cmd, "opt", None) or {}).get(o.name, o.default)
            a, b = o.choices
            return f"{o.name}={b if cur == a else a}"
    return opt_name


def _prompt_segments_for(cmd):
    """The running command's prompt, split into clickable option segments.

    Mirrors `Command.prompt`: while a bare ENUM option is waiting for its value,
    each displayed choice is a clickable segment; a free-form VALUE prompt remains
    unlabelled. Otherwise this is the step prompt plus the bracketed option list.
    """
    pending = getattr(cmd, "pending_option", None)
    if pending is not None:
        return opts.value_prompt_segments(pending)
    if getattr(cmd, "variable_points", False):
        base = f"{cmd.name} Point {len(cmd.args) + 1}:"
    else:
        steps = getattr(cmd, "steps", [])
        idx = getattr(cmd, "step_index", 0)
        if idx >= len(steps):
            return []
        base = steps[idx][0]
    return opts.prompt_segments(base, getattr(cmd, "options", ()),
                                getattr(cmd, "opt", None))


def _completion_names_for(st):
    """The candidate names for the current buffer, or [] when none apply.

    Only offered while NO command is running -- the buffer is a command name
    then. Once a command owns the prompt the buffer is an argument (a point, a
    radius), and completing it against command names would be nonsense.

    Shared by the draw handler (which lays the names out into rows) and the
    modal (which navigates them), so the index the modal points at is always a
    real row the bar is drawing -- the two cannot drift apart.
    """
    if st.active_command is not None:
        return []
    prefix = (st.input_buffer or "").strip()
    if not prefix:
        return []
    names = sorted(c.name for c in COMMANDS.values())
    # Ordinary aliases autocomplete alongside commands. Only aliases whose target is built are
    # offered, avoiding entries that look usable but cannot run. A picked row is fed through
    # `resolve_command`, which already runs an alias. Inline Tab completion stays
    # commands-only because it decides what runs.
    have = {n.lower() for n in names}
    names = sorted(names + [a.name for a in alias_table.ALIAS_TABLE.values()
                            if a.implemented and a.name.lower() not in have])
    # the reference CAD application uses fuzzy autocomplete, not a bare prefix match. Its documentation describes
    # "two command
    # lists divided by a blank line" -- the names that start with what you typed,
    # then everything else whose letters appear in order. `completion_lists` returns
    # that flat list with `None` standing for the blank line, and its docstring puts
    # the job of rendering that row here, on the drawing side.
    #
    # Call the fuzzy completion model so non-prefix matches are visible.
    matches = cmdbar.completion_lists(prefix, names)
    # An exact and only match is already fully typed; a list of one identical
    # entry is noise rather than help.
    real = [m for m in matches if m is not None]
    if len(real) == 1 and real[0].lower() == prefix.lower():
        return []
    return list(matches)


def _completion_is_divider(name):
    """True for the blank-line row that separates the two fuzzy lists.

    `cmdbar.completion_lists` marks it with `None`. It is a drawn row (so the
    layout leaves a gap where the reference CAD application leaves one) but it is NOT a candidate: it must
    never be highlighted, selected by Enter/Tab, or activated by a click.
    """
    return name is None


def _completion_step(rows, index, delta):
    """The next selectable row index from `index` moving by `delta`, clamped.

    The arrow keys must STEP OVER the divider. Walking one row at a time and
    skipping dividers is what keeps "down, down, down" landing on five real
    candidates instead of stalling on the blank line -- and if the walk runs off
    either end, the original index is kept rather than wrapping, which is what the
    clamped behaviour did before the divider existed.
    """
    n = len(rows or ())
    if n == 0 or delta == 0:
        return index
    i = index
    while True:
        j = i + delta
        if j < 0 or j >= n:
            return index            # ran off the end: stay put, as clamping did
        i = j
        if not _completion_is_divider(rows[i][0]):
            return i


def _completion_selectable_index(rows, index):
    """`index` clamped into `rows` and nudged off a divider if it landed on one.

    Both the draw handler and Enter/Tab go through this, so the highlight the user
    sees and the name they accept can never be the blank line.
    """
    i = cmdbar.clamp_completion_index(len(rows or ()), index)
    if i < 0:
        return i
    if not _completion_is_divider(rows[i][0]):
        return i
    nxt = _completion_step(rows, i, 1)
    if nxt != i and not _completion_is_divider(rows[nxt][0]):
        return nxt
    prv = _completion_step(rows, i, -1)
    if prv != i and not _completion_is_divider(rows[prv][0]):
        return prv
    return i


def _selected_completion(st):
    """The candidate Enter/Tab would accept right now, or None."""
    rows = _completion_rows_now(st)
    if not rows:
        return None
    i = _completion_selectable_index(rows, _completion_index)
    if i < 0:
        return None
    name = rows[i][0]
    return None if _completion_is_divider(name) else name


def _completion_rows_for(st, height, line_h=None):
    """Autocomplete rows for what is currently typed, or [].

    Kept for the OVERLAY's row budget and for tests that want to ask "what would fit
    in a bar this tall?". `height` is a drawable height in pixels.

    The prompt line is derived inside `cmdbar.completion_rows_for_bar`, so there is no
    ``prompt_y`` to pass (and none to get wrong -- see that function's docstring).
    """
    matches = _completion_names_for(st)
    if not matches:
        return []
    rows = cmdbar.completion_rows_for_bar(
        matches, height, len(st.history),
        cmdbar.LINE_H if line_h is None else line_h, cmdbar.PAD_Y)
    # Drop a DIVIDER with nothing after it: a blank line separating one list from
    # nothing wastes a row. Trimming here, on the drawing side, keeps
    # `completion_lists` honest about what the two lists ARE.
    while rows and _completion_is_divider(rows[-1][0]):
        rows = rows[:-1]
    return rows


# ---------------------------------------------------------------------------
# THE AUTOCOMPLETE LIST FLOATS OVER THE VIEWPORT
#
# Verified, and it is why this exists: the command bar's WINDOW region is 60 px tall,
# which fits exactly ONE candidate row. Typing `ci` produced
# ['Circle', <divider>, 'Cylinder', 'BooleanIntersection'] and the user saw `Circle`.
# The fuzzy autocomplete was wired, correct, tested -- and invisible.
#
# the reference CAD application's list is not confined to the bar; it floats over the viewport next to the
# command line. Our bar sits at the TOP of the window (the layout map in
# the live-window layout report shows CMDBAR above the viewport rows), so the list drops
# DOWNWARD from the bar into the top row of panes, drawn POST_PIXEL exactly like the
# viewport tab strip -- which is the working template for this and was built first
# partly to prove the shape.
#
# Growing the bar is wrong twice: the input line must remain pinned on screen, and the
# bar is a user-resizable Blender `Area`, so any chosen height would not stay fixed.
# The overlay geometry remains with the draw handler because it depends on live viewport
# areas; the pure command-bar module retains only area-independent layout arithmetic.
# ---------------------------------------------------------------------------

#: Application-defined maximum rows for the floating list. `cmdbar.MAX_COMPLETIONS` (5) was
#: chosen when the bar could show one or two; with the viewport to draw in there is
#: room for more, and the reference CAD application's own list is longer than five. Kept modest so the list
#: never swallows the view.
COMPLETION_OVERLAY_MAX_ROWS = 12
COMPLETION_OVERLAY_WIDTH = 260
#: matches the command bar's own text size, because it is the same list of names
COMPLETION_OVERLAY_FONT_SIZE = 13.0

#: The boxes the overlay last DREW: ``[(label, y_bottom_window)]``. The hit-test and
#: the keyboard both read this, so what is navigable is exactly what is visible.
_completion_overlay_boxes = []
_completion_overlay_strip = None


def _top_row_panes():
    """The VIEW_3D panes whose TOP edge is the top of the viewport area.

    The list hangs from there. Found by geometry, not by remembering which areas they
    are, because `Area` references do not survive a layout change.
    """
    panes = [a for a in _view3d_areas() if a.height > 4 and a.width > 4]
    if not panes:
        return []
    ceiling = max(a.y + a.height for a in panes)
    return [a for a in panes if abs((a.y + a.height) - ceiling) <= 2]


def _completion_overlay_geometry():
    """``(x0, y_top, width, capacity)`` in WINDOW coords, or None.

    `capacity` is how many rows fit without running off the bottom of the top pane
    row -- a real bound, so a long list cannot draw outside the viewport.
    """
    panes = _top_row_panes()
    if not panes:
        return None
    x0 = min(a.x for a in panes)
    y_top = max(a.y + a.height for a in panes)
    usable = min(a.height for a in panes)
    capacity = max(0, int(usable) // cmdbar.LINE_H)
    return (x0, y_top, COMPLETION_OVERLAY_WIDTH,
            min(COMPLETION_OVERLAY_MAX_ROWS, capacity))


def _completion_overlay_rows(st=None):
    """``[(label, y_bottom_window)]`` for the floating list, top row first.

    A `None` label is the fuzzy divider; it takes a row's height and draws nothing.
    A trailing divider is trimmed for the same reason it was in the bar -- a blank
    line separating one list from nothing is a wasted row.
    """
    if st is None:
        st = app_state
    names = _completion_names_for(st)
    if not names:
        return []
    geom = _completion_overlay_geometry()
    if geom is None:
        return []
    _x0, y_top, _w, capacity = geom
    names = list(names)[:max(0, capacity)]
    while names and _completion_is_divider(names[-1]):
        names.pop()
    return [(name, y_top - (i + 1) * cmdbar.LINE_H)
            for i, name in enumerate(names)]


def get_completion_overlay_boxes():
    """The rows the overlay last drew (for the rendered gate and introspection)."""
    return list(_completion_overlay_boxes)


def draw_completion_overlay():
    """POST_PIXEL draw handler: the floating autocomplete list.

    Only the top-row pane that owns the list's x range draws it, so it appears once
    rather than in each pane. Same discipline as the tab strip: record in window
    coordinates what was drawn, and let the hit-test read only that.
    """
    import bpy

    global _completion_overlay_boxes, _completion_overlay_strip

    context = bpy.context
    area = getattr(context, "area", None)
    region = getattr(context, "region", None)
    if area is None or area.type != "VIEW_3D" or region is None:
        return
    rows = _completion_overlay_rows()
    geom = _completion_overlay_geometry()
    if not rows or geom is None:
        _completion_overlay_boxes = []
        _completion_overlay_strip = None
        return
    x0, y_top, width, _cap = geom
    # the pane that owns the list's left edge draws it, once
    if not (area.x <= x0 < area.x + area.width):
        return
    if abs((region.y + region.height) - y_top) > 2:
        return
    _completion_overlay_strip = geom
    _completion_overlay_boxes = rows

    try:
        import gpu
    except Exception:  # noqa: BLE001
        return
    shader = _uniform_color_shader()
    if shader is None:
        return
    sel = _completion_selectable_index(rows, _completion_index)
    rx = x0 - region.x
    prev_blend = gpu.state.blend_get()
    gpu.state.blend_set("ALPHA")
    try:
        shader.bind()
        bottom = rows[-1][1] - region.y
        top = y_top - region.y
        _draw_rect(shader, rx, bottom, rx + width, top, CMD_COMPLETION_BG)
        for i, (name, wy) in enumerate(rows):
            ry = wy - region.y
            if _completion_is_divider(name):
                continue            # Verified: "divided by a blank line"
            if i == sel:
                _draw_rect(shader, rx, ry, rx + width, ry + cmdbar.LINE_H,
                           CMD_COMPLETION_HL)
                _draw_text(rx + 12, ry + 3, name, COMPLETION_OVERLAY_FONT_SIZE,
                           CMD_COMPLETION_SEL)
            else:
                _draw_text(rx + 12, ry + 3, name, COMPLETION_OVERLAY_FONT_SIZE,
                           CMD_COMPLETION)
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] completion overlay draw failed: {e!r}")
    finally:
        gpu.state.blend_set(prev_blend)


def completion_overlay_at(win_x, win_y):
    """The candidate under a WINDOW-space point, or None.

    Returns None for the divider too (its label IS None), so a click on the blank
    line is consumed doing nothing -- it is not a command.
    """
    if _completion_overlay_strip is None or not _completion_overlay_boxes:
        return None
    x0, _y_top, width, _cap = _completion_overlay_strip
    if not (x0 <= win_x < x0 + width):
        return None
    for name, wy in _completion_overlay_boxes:
        if wy <= win_y < wy + cmdbar.LINE_H:
            return name
    return None


def _cmdbar_region_height():
    """The height of the command bar's WINDOW region, or None.

    The draw handler measures rows against its *region* height, so the modal must
    use the same number -- an area is slightly taller than its window region.
    """
    area = _cmdbar_area()
    if area is None:
        return None
    region = next((r for r in area.regions if r.type == "WINDOW"), None)
    if region is None or region.height < 4:
        return None
    return region.height


def _completion_rows_now(st):
    """The candidate rows on screen right now, or [].

    Now the OVERLAY's rows, not the bar's. Same helper the draw handler calls,
    so the modal navigates and the click handler hit-tests the SAME rows the user
    sees -- the index it points at is always a real drawn row. That invariant is why
    this function exists at all; moving the list changed where the rows come from and
    nothing else.
    """
    return _completion_overlay_rows(st)


# ---------------------------------------------------------------------------
# Anti-aliased lines 
#
# The flat `UNIFORM_COLOR` line is aliased. Blender's `POLYLINE_UNIFORM_COLOR` is
# unsuitable for ranged draws: in `polyline_draw_workaround`, for a RANGED draw
# Blender hands the shader the RANGE's vertex count as the buffer total, and the shader
# fetches a segment's second vertex as `(first + 1) % count` -- so every
# `draw_range(first > 0)` joins segment starts to unrelated vertices. Ten short
# horizontal segments drawn as `draw_range(12, 4)` put ink from y=10 to y=135 instead
# of y=117..135. The curve pass draws its static batches in ranges whenever a curve is
# dirty after zoom tessellation or an edit.
#
# So the app draws lines with its OWN shader: every segment becomes two triangles
# (six vertices, built once with numpy), each vertex knowing its own end, the other end
# and which corner it is. The vertex shader pushes the corner out in SCREEN pixels,
# perpendicular to the segment, by half the width plus a 1 px fringe; the fragment
# shader fades alpha with distance to the centre line, preserving a thin one-pixel
# stroke with a smooth edge. Every segment is independent, so a range
# of segments draws exactly those segments -- line vertex range (first, count) is
# triangle vertex range (3 * first, 3 * count) (`_line_range`).
# ---------------------------------------------------------------------------

_AA_LINE_VERT_SRC = """
void main()
{
  vec4 a = ModelViewProjectionMatrix * vec4(pos, 1.0);
  vec4 b = ModelViewProjectionMatrix * vec4(other, 1.0);
  float perp = (side > 0.0) ? 1.0 : -1.0;
  v_d = perp * (halfw + 1.0);
  /* clip the segment to the near plane (z >= -w) BEFORE projecting: an end behind the
   * eye projects to the wrong side of the screen and would smear the quad across it */
  float da = a.z + a.w;
  float db = b.z + b.w;
  if (da < 0.0 && db < 0.0) {
    gl_Position = vec4(0.0, 0.0, 2.0, 1.0);
    return;
  }
  if (da < 0.0) {
    a = mix(a, b, da / (da - db));
  }
  if (db < 0.0) {
    b = mix(b, a, db / (db - da));
  }
  vec2 half_vp = 0.5 * viewport;
  vec2 sa = a.xy / a.w * half_vp;
  vec2 sb = b.xy / b.w * half_vp;
  vec2 away = sa - sb;
  float len = length(away);
  away = (len > 1e-6) ? away / len : vec2(1.0, 0.0);
  /* the segment's start -> end direction, so `perp` names the same side at both ends */
  vec2 t = (abs(side) > 1.5) ? away : -away;
  vec2 n = vec2(-t.y, t.x);
  /* sideways by half the width + the fringe; lengthways by half the width, so the
   * pieces of a polyline overlap at their joints instead of leaving a notch */
  vec2 o = n * v_d + away * halfw;
  gl_Position = vec4((sa + o) / half_vp, a.z / a.w, 1.0);
}
"""

_AA_LINE_FRAG_SRC = """
void main()
{
  float cov = clamp(halfw + 0.5 - abs(v_d), 0.0, 1.0);
  if (cov <= 0.0) {
    discard;
  }
  /* our colours are sRGB; see `_CP_FRAG_SRC` for why a Python shader converts here */
  vec4 c = color;
  if (srgbTarget) {
    vec3 s = max(c.rgb, vec3(0.0));
    c.rgb = mix(s * (1.0 / 12.92), pow((s + 0.055) * (1.0 / 1.055), vec3(2.4)),
                step(vec3(0.04045), s));
    /* ...and that target BLENDS in linear light, so a part-covered pixel came out far
     * lighter than the same blend in sRGB (the display the old flat line and the reference CAD application
     * give): MEASURED, a 1 px blue line between two rows was (99,103,228) over
     * (156,162,169) -- 0.4 of the way to blue -- and read as faint. This alpha makes a
     * dark line over a lighter ground blend as it would in sRGB (exact for black;
     * OURS for colours, and a light line on a dark ground comes out a little bolder). */
    cov = 1.0 - pow(1.0 - cov, 2.2);
  }
  c.a *= cov;
  fragColor = c;
}
"""

#: the anti-aliased line shader + vertex format (built once), or why it could not be
_aa_line_gpu = {"shader": None, "failed": None, "fmt": None}
#: corner codes of a segment's six vertices: sign = side of the centre line, |code| 1 =
#: the START end, 2 = the END end. Triangles (S+, S-, E-) and (S+, E-, E+).
_AA_LINE_CORNERS = (1.0, -1.0, -2.0, 1.0, -2.0, 2.0)


def _aa_line_shader():
    """The anti-aliased line shader, or None (said ONCE on the console) when this GPU
    backend cannot build it -- lines are then drawn flat (aliased), never not at all."""
    g = _aa_line_gpu
    if g["shader"] is not None or g["failed"] is not None:
        return g["shader"]
    try:
        import gpu
        import numpy  # noqa: F401

        iface = gpu.types.GPUStageInterfaceInfo("narwhal_aa_line_iface")
        iface.no_perspective("FLOAT", "v_d")
        info = gpu.types.GPUShaderCreateInfo()
        # set by Blender itself on bind (`GPU_matrix_bind`), like `srgbTarget`: the very
        # matrix the flat shader used, in POST_VIEW and in an offscreen alike
        info.push_constant("MAT4", "ModelViewProjectionMatrix")
        info.push_constant("VEC4", "color")
        info.push_constant("VEC2", "viewport")
        info.push_constant("FLOAT", "halfw")
        info.push_constant("BOOL", "srgbTarget")
        info.vertex_in(0, "VEC3", "pos")
        info.vertex_in(1, "VEC3", "other")
        info.vertex_in(2, "FLOAT", "side")
        info.vertex_out(iface)
        info.fragment_out(0, "VEC4", "fragColor")
        info.vertex_source(_AA_LINE_VERT_SRC)
        info.fragment_source(_AA_LINE_FRAG_SRC)
        shader = gpu.shader.create_from_info(info)
        fmt = gpu.types.GPUVertFormat()
        fmt.attr_add(id="pos", comp_type="F32", len=3, fetch_mode="FLOAT")
        fmt.attr_add(id="other", comp_type="F32", len=3, fetch_mode="FLOAT")
        fmt.attr_add(id="side", comp_type="F32", len=1, fetch_mode="FLOAT")
    except Exception as e:  # noqa: BLE001
        g["failed"] = repr(e)
        print(f"[narwhal3d] anti-aliased line shader unavailable ({e!r}); curves are drawn "
              f"with flat (aliased) lines")
        return None
    g.update(shader=shader, info=info, iface=iface, fmt=fmt)
    return shader


def _aa_line_arrays(coords):
    """``(pos, other, side)`` vertex arrays for LINE vertex pairs `coords`: six
    vertices per segment (see `_AA_LINE_CORNERS`)."""
    import numpy as np

    lv = np.asarray(coords, dtype=np.float32).reshape(-1, 3)
    k = len(lv) // 2
    s, e = lv[0:2 * k:2], lv[1:2 * k:2]
    pos = np.empty((k, 6, 3), dtype=np.float32)
    other = np.empty((k, 6, 3), dtype=np.float32)
    at_s, at_e = [0, 1, 3], [2, 4, 5]
    pos[:, at_s] = s[:, None]
    pos[:, at_e] = e[:, None]
    other[:, at_s] = e[:, None]
    other[:, at_e] = s[:, None]
    side = np.tile(np.array(_AA_LINE_CORNERS, dtype=np.float32), k)
    return pos.reshape(-1, 3), other.reshape(-1, 3), side


def _line_shader():
    """The shader curves and Gumball lines are drawn with: the app's anti-aliased one
    (`_aa_line_shader`, ), or the flat `UNIFORM_COLOR` when smoothing is
    off or ours cannot be built. Returns ``(shader, is_aa)``."""
    if SMOOTH_LINES:
        shader = _aa_line_shader()
        if shader is not None:
            return shader, True
    return _uniform_color_shader(), False


def _line_batch(shader, is_aa, coords):
    """A batch drawing the LINE vertex pairs `coords` with `_line_shader`'s shader."""
    if not is_aa:
        from gpu_extras.batch import batch_for_shader

        return batch_for_shader(shader, "LINES", {"pos": coords})
    import gpu

    pos, other, side = _aa_line_arrays(coords)
    vbo = gpu.types.GPUVertBuf(_aa_line_gpu["fmt"], len(pos))
    vbo.attr_fill("pos", pos)
    vbo.attr_fill("other", other)
    vbo.attr_fill("side", side)
    return gpu.types.GPUBatch(type="TRIS", buf=vbo)


def _line_range(is_aa, first, count):
    """A ``(first, count)`` range of LINE vertices as a range of `_line_batch`'s."""
    return (first * 3, count * 3) if is_aa else (first, count)


def _line_begin(shader, is_aa):
    """Bind `shader` for line draws (ours needs the pixel size of the target)."""
    import gpu

    shader.bind()
    if is_aa:
        vp = gpu.state.viewport_get()
        shader.uniform_float("viewport", (float(max(vp[2], 1)), float(max(vp[3], 1))))


def _line_width(shader, is_aa, width):
    """Set the line width (pixels) for the next draws: a uniform of ours, GPU state for
    the flat shader."""
    import gpu

    if is_aa:
        shader.uniform_float("halfw", 0.5 * float(width))
    else:
        gpu.state.line_width_set(float(width))


def _uniform_color_shader():
    """The builtin flat-color shader, under whichever name this Blender uses.

    Blender 4.0 dropped the ``2D_`` prefix from builtin shader names, so
    ``2D_UNIFORM_COLOR`` raises on 5.x. Try the modern name first and fall back,
    so the command bar can't be taken out by a rename.
    """
    import gpu

    for name in ("UNIFORM_COLOR", "2D_UNIFORM_COLOR"):
        try:
            return gpu.shader.from_builtin(name)
        except Exception:  # noqa: BLE001
            continue
    return None


def draw_command_bar(context=None):
    """Draw handler for the INFO (command bar) region.

    ``Space.draw_handler_add(cb, args, ...)`` calls ``cb(*args)`` and does NOT
    inject a context, so with the empty args tuple we register this with, the
    parameter must be optional — otherwise every draw raises
    ``TypeError: draw_command_bar missing 1 required positional argument``
    and the command bar silently never appears. Headless tests cannot catch this
    because draw handlers never fire under ``-b``.
    """
    import bpy

    if context is None:
        context = bpy.context

    try:
        import gpu
        from gpu_extras.batch import batch_for_shader
    except Exception:  # noqa: BLE001
        return

    region = getattr(context, "region", None)
    if region is None:
        return
    # this handler is registered on the INFO SPACE TYPE, so it runs for the LEFT
    # DOCK BAND as well as the command bar. Without this it painted the band white and
    # laid a clipped tab row and prompt into a 114 px strip. Found by a mutation that
    # removed the BAND's own handler and saw nothing fail -- because this one was
    # covering for it, which also made the band's paint gate unable to distinguish
    # them.
    if _info_area_is_band(getattr(context, "area", None)):
        return
    w = region.width
    h = region.height
    if w < 4 or h < 4:
        return

    shader = _uniform_color_shader()
    if shader is None:
        return
    # Blender 5.2 has no gpu.state.color_mask_enable (that call raised
    # AttributeError on every draw). The state we actually want for UI drawing is
    # alpha blending; verified members are color_mask_set / blend_set.
    prev_blend = gpu.state.blend_get()
    gpu.state.blend_set("ALPHA")
    try:
        shader.bind()

        def fill(x0, y0, x1, y1, color):
            _draw_rect(shader, x0, y0, x1, y1, color)

        # background
        fill(0, 0, w, h, CMD_BG)

        line_h = cmdbar.LINE_H
        pad_x, pad_y = cmdbar.PAD_X, cmdbar.PAD_Y
        font_size = _fpx(13.0)        # the interface scale

        st = app_state
        # THE TOOLBAR TAB ROW takes the top of this region. the reference CAD application draws all sixteen
        # tab names on their own row with the button row against it. `INFO_HT_tool_header`
        # is None, so Blender will
        # not give this area a second header to put them in. The command bar's own
        # layout is therefore computed against a REDUCED height, which costs it one
        # history line and nothing else -- the prompt is pinned to the bottom.
        #
        # Keep `cmdbar.command_bar_layout` independent of toolbar chrome; passing it the
        # reduced drawable height is sufficient.
        #
        # int is LOAD-BEARING. TAB_ROW_H is a float, and a float height makes
        # `command_bar_layout`'s `usable // line_h` a float, which reaches `range`
        # and raises TypeError. This draw handler is wrapped in try/finally, so the
        # exception killed every line AFTER the tab row and the bar rendered as a
        # blank white box with the tabs above it -- caught by the "command bar draws
        # its text" pixel gate, not by anything structural.
        tab_row_h = _draw_toolbar_tab_row(shader, w, h, font_size)

        # The prompt is PINNED to the bottom line and history scrolls above it.
        # Previously history was allowed every line that fit and the prompt was
        # drawn one line below the last of them, so once history filled the bar
        # the prompt fell off the bottom and the user could not see what they
        # were typing. The arithmetic now lives in the bpy-free cmdbar module so
        # a headless test can cover it (draw handlers never run under -b).
        prompt_y, history_ys, max_history = cmdbar.command_bar_layout(
            int(h - tab_row_h), len(st.history), line_h, pad_y)

        # ---- autocomplete candidates ---------------------------
        # The list is NO LONGER DRAWN IN THE BAR. Verified: the bar's WINDOW region is
        # 60 px, which fits exactly ONE candidate row, so a fuzzy list of four was
        # being truncated to its first entry and the feature was invisible. the reference CAD application's
        # list FLOATS over the viewport beside the command line, so ours does too --
        # see `draw_completion_overlay`.
        #
        # `shown_names` survives as an empty set because the prompt block below still
        # reads it. With the list gone from the bar it covers nothing, so empty is the
        # correct value rather than a stub.
        shown_names = set()

        # ---- history --------------------------------------------------------
        # the candidate list used to BORROW these rows, so any history line it
        # covered was skipped. The list floats over the viewport now and covers
        # nothing here, so every visible history line draws. Leaving the old
        # `if rows and ...` guard behind was a NameError on every single draw -- the
        # bar rendered as a plain white rectangle with no text at all, which is
        # exactly what the "command bar draws its text" gate caught.
        for line, line_y in zip(cmdbar.visible_history(st.history, max_history),
                                history_ys):
            _draw_text(pad_x, line_y, line, font_size, CMD_HISTORY)

        # ---- the prompt line, drawn PIECE BY PIECE so options are clickable --
        # the reference CAD application's bracketed options are buttons. Building the line from labelled
        # segments (options.prompt_segments) means the boxes we hit-test are
        # measured from the very same glyphs we draw -- they cannot drift.
        cmd = st.active_command
        boxes = []
        x = float(pad_x)
        if cmd is not None:
            segments = _prompt_segments_for(cmd)
            # the MODE options in effect are UNDERLINED
            try:
                in_use = set(cmd.active_modes())
            except Exception:  # noqa: BLE001
                in_use = set()
            for text_part, opt_name in segments:
                width = _text_width(text_part, font_size)
                if opt_name is not None:
                    boxes.append((opt_name, x, x + width))
                    _draw_text(x, prompt_y, text_part, font_size, CMD_OPTION)
                    if opt_name in in_use:
                        shader.bind()
                        fill(x, prompt_y - 3, x + width, prompt_y - 1.5, CMD_OPTION)
                else:
                    _draw_text(x, prompt_y, text_part, font_size, CMD_TEXT)
                x += width
            # a trailing space before the typed text, as the old single-string
            # prompt had (state.prompt appended one)
            x += _text_width(" ", font_size)
        else:
            head = "Command: "
            _draw_text(x, prompt_y, head, font_size, CMD_TEXT)
            x += _text_width(head, font_size)
        _set_option_boxes(boxes, prompt_y)

        # the text the user has typed, after the prompt
        if st.input_buffer:
            _draw_text(x, prompt_y, st.input_buffer, font_size, CMD_TEXT)
            x += _text_width(st.input_buffer, font_size)

        # caret (block after the input text), placed by real measured width
        if cmd is not None or st.input_buffer:
            fill(x + 1, prompt_y - 2, x + 5, prompt_y + line_h - 4, CMD_CURSOR)
    finally:
        # never leak blend state back into Blender's own drawing
        gpu.state.blend_set(prev_blend)


def pane_label(area):
    """The view name shown in `area`'s title ("Top", "Perspective", ...)."""
    space = _view3d_space(area) if area is not None else None
    rv3d = getattr(space, "region_3d", None) if space is not None else None
    if rv3d is None:
        return "viewport"
    return quat_to_label(tuple(rv3d.view_rotation), rv3d.view_perspective)


def _pane_key(area):
    """A stable identity for a viewport pane.

    Area identity is NOT stable across ``screen.areas`` reads (a documented trap
    here), so panes are keyed by their position instead, which is stable for a
    given layout and is how every other part of this file re-finds an area.
    """
    return (area.x, area.y)


#: Which pane is the ACTIVE viewport, as a `_pane_key`. the reference CAD application always has
#: exactly one, and it decides which CPlane typed input is interpreted in ("If
#: you type only x and y coordinates, the point will lie on the construction
#: plane of the active view" -- the reference CAD application documentation).
#: None means "not chosen yet"; `active_pane_key` then falls back to the
#: Perspective pane, which is the one the reference CAD application's own screenshot shows active.
_active_pane = None


def _invalidate_grid_renderer():
    """Drop grid/extent caches and redraw every live viewport."""
    grid_store.invalidate_grid_caches(_grid_batch_cache, _extent_cache)
    _tag_redraw_viewports()


def _sync_grid_store():
    """Attach and synchronize the document's per-pane grid store lazily."""
    areas = _view3d_areas()
    if not areas:
        return None
    ctx = app_state.ctx
    store = getattr(ctx, "grid_settings_store", None)
    ids = tuple(_pane_key(area) for area in areas)
    active = _active_pane or active_pane_key()
    if store is None:
        store = grid_store.GridSettingsStore(
            {key: grid_store.GridSettings() for key in ids},
            active_viewport_id=active if active in ids else ids[0],
        )
        ctx.grid_settings_store = store
    else:
        store.sync_viewports(ids, active_viewport_id=active)
    store.active_viewport_id = active if active in ids else ids[0]
    ctx.grid_settings_changed = _invalidate_grid_renderer
    return store


def set_active_pane(area):
    """Make `area` the active viewport and update the grid store scope."""
    global _active_pane
    if area is None or area.type != "VIEW_3D":
        return False
    key = _pane_key(area)
    changed = key != _active_pane
    _active_pane = key
    store = _sync_grid_store()
    if store is not None:
        store.active_viewport_id = key
    return changed


def active_pane_key():
    """The active pane's key, defaulting to the Perspective pane."""
    if _active_pane is not None:
        return _active_pane
    import bpy

    for area in bpy.context.screen.areas:
        if area.type != "VIEW_3D":
            continue
        sp = next((s for s in area.spaces if s.type == "VIEW_3D"), None)
        if sp is not None and sp.region_3d is not None \
                and sp.region_3d.view_perspective == "PERSP":
            return _pane_key(area)
    return None


def active_pane_area():
    """The active viewport's area, or None."""
    import bpy

    key = active_pane_key()
    if key is None:
        return None
    for area in bpy.context.screen.areas:
        if area.type == "VIEW_3D" and _pane_key(area) == key:
            return area
    return None


#: Per-viewport CPlane OVERRIDES, keyed by `_pane_key`. the reference CAD application keeps a
#: construction plane per viewport and `CPlane` edits the active one, so a pane
#: whose plane has been changed can no longer be derived from its orientation.
#: A pane absent from this dict falls back to the standard plane for its view,
#: which is what the reference CAD application ships ("the reference CAD application's standard viewports come with construction
#: planes that correspond to the viewport").
_cplane_overrides = {}


def set_cplane(area, plane):
    """Give one viewport its own construction plane. Returns True on success."""
    if area is None or area.type != "VIEW_3D" or plane is None:
        return False
    _cplane_overrides[_pane_key(area)] = plane
    _tag_redraw_viewports()
    return True


def get_cplane(area):
    """One viewport's CPlane: its override if set, else the standard one."""
    if area is None or area.type != "VIEW_3D":
        return cplane.WORLD_TOP
    override = _cplane_overrides.get(_pane_key(area))
    if override is not None:
        return override
    sp = next((s for s in area.spaces if s.type == "VIEW_3D"), None)
    if sp is None or sp.region_3d is None:
        return cplane.WORLD_TOP
    label = quat_to_label(tuple(sp.region_3d.view_rotation),
                          sp.region_3d.view_perspective)
    return cplane.FRAME_BY_LABEL.get(label, cplane.WORLD_TOP)


def reset_cplanes():
    """Drop every override, so all panes return to their standard planes."""
    _cplane_overrides.clear()
    _tag_redraw_viewports()


def print_view_spec(area=None):
    """The active (or given) viewport as a `print2d.View`, for Print / 2-D export.

    Read from Blender's own view: the rows of the view matrix are the screen's right
    and up directions in world space, `view_location` is the world point at the
    centre, and the visible width/height are measured by un-projecting the region's
    edges at that depth -- so it is exactly what is on screen, at any zoom.
    """
    import bpy_extras.view3d_utils as v3u
    from mathutils import Vector

    from . import print2d

    area = area or active_pane_area()
    if area is None:
        raise ValueError("there is no active viewport to print")
    space = next((s for s in area.spaces if s.type == "VIEW_3D"), None)
    region = next((r for r in area.regions if r.type == "WINDOW"), None)
    if space is None or region is None:
        raise ValueError("the active viewport cannot be read")
    rv3d = space.region_3d
    vm = rv3d.view_matrix
    xaxis = tuple(vm[0][i] for i in range(3))
    yaxis = tuple(vm[1][i] for i in range(3))
    center = tuple(rv3d.view_location)
    anchor = Vector(center)
    w, h = region.width, region.height
    a = v3u.region_2d_to_location_3d(region, rv3d, (0, h / 2), anchor)
    b = v3u.region_2d_to_location_3d(region, rv3d, (w, h / 2), anchor)
    c = v3u.region_2d_to_location_3d(region, rv3d, (w / 2, 0), anchor)
    d = v3u.region_2d_to_location_3d(region, rv3d, (w / 2, h), anchor)
    return print2d.View(xaxis=xaxis, yaxis=yaxis, center=center,
                        width=(b - a).length, height=(d - c).length,
                        perspective=rv3d.view_perspective != "ORTHO")


def active_cplane():
    """The CPlane frame of the ACTIVE viewport.

    This is the plane the reference CAD application interprets a typed point in. Falls back to World Top,
    which is also what the reference CAD application's default Perspective viewport uses.
    """
    area = active_pane_area()
    if area is None:
        return cplane.WORLD_TOP
    return get_cplane(area)


def draw_viewport_label(context=None):
    """Draw handler for a VIEW_3D pane: the pane's name in its top-left corner.

    the reference CAD application labels each viewport ("Top", "Perspective", "Front", "Right") in the
    top-left. The label is derived from the region's own orientation every frame
    so it stays correct when the user orbits (see `quat_to_label`). Same
    discipline as `draw_command_bar`: callable with zero args, text via `blf`,
    blend state restored in a `finally`.

    the ACTIVE pane's title is drawn as a filled blue chip and the others
    as plain text; see `views.title_style`.
    """
    import bpy

    if context is None:
        context = bpy.context

    try:
        import gpu
    except Exception:  # noqa: BLE001
        return

    region = getattr(context, "region", None)
    if region is None:
        return
    w = region.width
    h = region.height
    if w < 4 or h < 4:
        return

    # Resolve this pane's orientation. `context.area` is the VIEW_3D area this
    # handler is attached to; its single VIEW_3D space carries region_3d.
    area = getattr(context, "area", None)
    if area is None or area.type != "VIEW_3D":
        return
    sp = next((s for s in area.spaces if s.type == "VIEW_3D"), None)
    if sp is None or sp.region_3d is None:
        return
    # (the viewport half): notice this pane's zoom so curves can be
    # re-tessellated against SCREEN size, the way the reference CAD application does. Hooked into the draw
    # pass rather than into any one navigation path, so it catches a scroll-wheel
    # zoom, our own navigate.py, a View command, Zoom Extents and a maximize alike.
    # Cheap and write-free: it records a number and arms a one-shot timer, because
    # writing a Mesh datablock during a draw is the kind of thing that crashes.
    note_view_zoom(region, sp.region_3d)
    # ...and the snap index is built in idle time, not on the first Gumball move
    _note_snap_index_needed()
    _note_undo_base_needed()
    label = quat_to_label(tuple(sp.region_3d.view_rotation),
                          sp.region_3d.view_perspective)

    shader = _uniform_color_shader()
    if shader is None:
        return
    # is THIS pane the active viewport?
    is_active = _pane_key(area) == active_pane_key()
    fill_rgba, text_rgba = title_style(is_active)

    prev_blend = gpu.state.blend_get()
    gpu.state.blend_set("ALPHA")
    try:
        shader.bind()
        # Measure the label so both the chip and the baseline can be positioned
        # from the real glyph extent.
        try:
            import blf
            blf.size(_DEFAULT_FONT_ID, LABEL_FONT_SIZE)
            text_w, text_h = blf.dimensions(_DEFAULT_FONT_ID, label)
        except Exception:  # noqa: BLE001
            text_w = LABEL_FONT_SIZE * 0.6 * len(label)
            text_h = LABEL_FONT_SIZE

        # `_draw_text` takes the BASELINE, and glyphs grow upward from it, so the
        # baseline must sit a full text height below the top or the label is
        # clipped by the top of the region. It was previously `h - LABEL_PAD`,
        # which cut the top off every pane label.
        baseline_y = h - LABEL_PAD - text_h

        rect = views.title_rect(text_w, text_h, h, label_pad=LABEL_PAD)
        arrow_box = views.title_arrow_rect(rect)
        if fill_rgba is not None:
            # Keep the arrow visually attached to the active title instead of leaving
            # a tiny triangle floating on the viewport background.
            _draw_rect(shader, *views.title_menu_rect(rect), fill_rgba)
        elif arrow_box is not None:
            # Inactive titles have no chip fill, so their menu affordance needs its own
            # quiet button plate to remain visible.
            _draw_rect(shader, *arrow_box, views.gpu_rgba(views.TITLE_ARROW_FILL))
        if arrow_box is not None:
            border = views.gpu_rgba(views.TITLE_ARROW_BORDER)
            _draw_rect(shader, arrow_box[0], arrow_box[1], arrow_box[0] + 1,
                       arrow_box[3], border)
            _draw_rect(shader, arrow_box[0], arrow_box[1], arrow_box[2],
                       arrow_box[1] + 1, border)
        _draw_text(LABEL_PAD, baseline_y, label, LABEL_FONT_SIZE, text_rgba)
        # Draw the drop-down arrow for every pane, active or not:
        # it advertises the viewport title menu, which every pane has. Same
        # `views.title_rect` that the chip and the hit-test share, so the arrow, the
        # chip and the clickable box cannot drift apart.
        tri = views.title_arrow_triangle(rect)
        if tri is not None:
            _draw_tri(shader, tri, text_rgba)
    finally:
        # never leak blend state back into Blender's own drawing
        gpu.state.blend_set(prev_blend)


#: application-defined: the tab text size. Matches the status bar's rather than the viewport
#: titles', because the strip sits against the status bar and reads as its sibling.
TAB_FONT_SIZE = 12.0


def _view3d_areas():
    """Every VIEW_3D area in the current screen.

    A helper rather than the inline comprehension used elsewhere in this file,
    because the tab strip asks for it three times per draw and the guard against
    `bpy.context.screen` being None for a tick (the documented restore trap) has to
    live in exactly one place.
    """
    import bpy

    screen = getattr(bpy.context, "screen", None)
    if screen is None:
        return []
    return [a for a in screen.areas if a.type == "VIEW_3D"]


# ---------------------------------------------------------------------------
# THE VIEWPORT TAB STRIP
#
# Verified: "Viewport tabs are located at the bottom of the main viewport frame by
# default. Each model and layout viewport has a tab." -- ONE strip across the whole
# viewport area, not one per pane. `views.py` holds the order, the labels and the
# two menus; this half is geometry, pixels and clicks.
#
# HOW IT IS DRAWN, and why: the strip is not a Blender Area (the Bottom dock site is
# empty in the reference CAD application's own layout too -- the reference layout). It is drawn as a
# POST_PIXEL overlay along the bottom edge of the BOTTOM ROW of panes, each pane
# drawing the slice of the strip that falls inside it. That makes one continuous
# band across a split layout and the whole width when a pane is maximized, with no
# layout surgery -- and layout surgery is the one thing in this file that has
# repeatedly gone wrong.
# ---------------------------------------------------------------------------

#: Cached strip geometry, rebuilt every draw: (x0_window, y_window, [(label,x0,x1)]).
#: The click and wheel handlers read it, so what is hit is exactly what was drawn --
#: the same one-source rule `views.title_rect` exists for.
_tab_boxes = []
_tab_strip = None
_tabs_visible = True


def viewport_tabs_visible():
    """True when the tab strip is showing.

    `ViewportTabs` takes Show/Hide/Toggle, and the tab context menu has "Hide Tabs".
    the reference CAD application also exposes a "Viewport tabs at start" preference; this application starts
    with tabs visible.
    """
    return _tabs_visible


def set_viewport_tabs_visible(on):
    """Show or hide the strip. Returns the state in force."""
    global _tabs_visible
    _tabs_visible = bool(on)
    _tag_redraw_viewports()
    return _tabs_visible


def _bottom_row_panes():
    """The VIEW_3D panes whose bottom edge is the bottom of the viewport area.

    The strip sits along that edge. With one maximized pane this is that pane; in
    the 2x2 layout it is the lower two. Found by GEOMETRY rather than by remembering
    which areas they are, because `Area` references do not survive a layout change
    (the documented trap).
    """
    panes = [a for a in _view3d_areas() if a.height > 4 and a.width > 4]
    if not panes:
        return []
    floor = min(a.y for a in panes)
    return [a for a in panes if abs(a.y - floor) <= 2]


def _tab_strip_geometry():
    """``(x0, x1, y, labels)`` for the strip in WINDOW coords, or None.

    `y` is the strip's bottom. The span is the viewport area's full width, which is
    what makes one band across a split layout: it is taken from the bottom row's
    leftmost and rightmost edges, not from any single pane.
    """
    panes = _bottom_row_panes()
    if not panes:
        return None
    x0 = min(a.x for a in panes)
    x1 = max(a.x + a.width for a in panes)
    y = min(a.y for a in panes)
    present = []
    for a in _view3d_areas():
        name = pane_label(a)
        if name not in present:
            present.append(name)
    return (x0, x1, y, views.tab_labels(present))


def _measure_tab(label):
    """Width of a tab label in pixels, via blf, with a fallback."""
    try:
        import blf

        blf.size(_DEFAULT_FONT_ID, TAB_FONT_SIZE)
        return blf.dimensions(_DEFAULT_FONT_ID, label)[0]
    except Exception:  # noqa: BLE001
        return TAB_FONT_SIZE * 0.6 * len(label)


def draw_viewport_tabs():
    """POST_PIXEL draw handler: this pane's slice of the viewport tab strip.

    Every bottom-row pane runs this and draws the part of the strip inside its own
    region, so the band is continuous across the split. The boxes are recorded in
    WINDOW coordinates for the click handler -- drawn and clickable from one list.
    """
    import bpy

    global _tab_boxes, _tab_strip

    if not _tabs_visible:
        return
    context = bpy.context
    area = getattr(context, "area", None)
    region = getattr(context, "region", None)
    if area is None or area.type != "VIEW_3D" or region is None:
        return
    geom = _tab_strip_geometry()
    if geom is None:
        return
    sx0, sx1, sy, labels = geom
    # only the bottom row draws it, and only the region that owns that bottom edge
    if abs(region.y - sy) > 2:
        return
    boxes = views.tab_boxes(labels, _measure_tab, x0=sx0, h=views.TAB_H)
    _tab_strip = geom
    _tab_boxes = boxes

    try:
        import gpu
    except Exception:  # noqa: BLE001
        return
    shader = _uniform_color_shader()
    if shader is None:
        return
    active = pane_label(active_pane_area())
    band = views.gpu_rgba(views.TAB_BAND_BG)
    act_bg = views.gpu_rgba(views.TAB_ACTIVE_BG)
    border = views.gpu_rgba(views.TAB_BORDER)

    prev_blend = gpu.state.blend_get()
    gpu.state.blend_set("ALPHA")
    try:
        shader.bind()
        # the band, across this region's whole width
        _draw_rect(shader, 0, 0, region.width, views.TAB_H, band)
        for label, bx0, bx1 in boxes:
            # window -> this region's coords
            rx0, rx1 = bx0 - region.x, bx1 - region.x
            if rx1 < 0 or rx0 > region.width:
                continue                    # this tab belongs to the other pane
            is_active = (label == active and label != views.TAB_ADD)
            if is_active:
                _draw_rect(shader, rx0, 0, rx1, views.TAB_H, act_bg)
                _draw_rect(shader, rx0, views.TAB_H - 1, rx1, views.TAB_H, border)
                _draw_rect(shader, rx0, 0, rx0 + 1, views.TAB_H, border)
                _draw_rect(shader, rx1 - 1, 0, rx1, views.TAB_H, border)
            colour = views.gpu_rgba(views.TAB_TEXT_ACTIVE if is_active
                                    else views.TAB_TEXT)
            _draw_text(rx0 + views.TAB_PAD_X, 5, label, TAB_FONT_SIZE, colour)
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] viewport tab draw failed: {e!r}")
    finally:
        gpu.state.blend_set(prev_blend)


def get_viewport_tab_boxes():
    """The tab boxes as last DRAWN: ``[(label, x0, x1)]`` in window coords.

    For the rendered gate and for introspection. Deliberately the same list the
    hit-test reads, so a test cannot assert geometry the user never saw.
    """
    return list(_tab_boxes)


def viewport_tab_at(win_x, win_y):
    """The tab label at a WINDOW-space point, or None.

    Reads the boxes the draw handler recorded, so a click can only ever land on a
    tab that was actually drawn.
    """
    if not _tabs_visible or _tab_strip is None or not _tab_boxes:
        return None
    _sx0, _sx1, sy, _labels = _tab_strip
    if not (sy <= win_y < sy + views.TAB_H):
        return None
    return views.hit_tab(win_x, _tab_boxes)


def _pane_by_label(label):
    """The VIEW_3D area whose view is named `label`, or None."""
    for a in _view3d_areas():
        if pane_label(a) == label:
            return a
    return None


def viewport_tab_click(label):
    """A tab was clicked. Returns the line to log.

    Verified: "The tabs allow you to switch between viewports especially when a
    viewport is maximized." Unmaximized, switching IS activating -- that is what
    decides the CPlane typed input is read in. While MAXIMIZED the other panes have
    been closed (`area_close`, one per tick), so switching means restore-then-
    re-maximize through the layout state machine; that is not built, and this says
    so rather than activating a pane the user cannot see.
    """
    if label == views.TAB_ADD:
        if show_tab_add_menu() is None:
            return "the Add Viewport menu could not open"
        return None
    if _maximized:
        return (f"switching to {label} while a viewport is maximized is not "
                f"implemented yet (double-click the title to restore first)")
    area = _pane_by_label(label)
    if area is None:
        return f"there is no {label} viewport"
    if set_active_pane(area):
        _tag_redraw_viewports()
        return f"Activated the {label} viewport"
    return None


def viewport_tab_wheel(delta):
    """Wheel over the strip. Returns the line to log, or None.

    Verified: "Scroll the mouse wheel when it is over the tabs to cycle through the
    tabs." The Add tab is skipped -- `views.cycle_tab` does that, because scrolling
    onto a button would switch you to no viewport at all.
    """
    if _tab_strip is None:
        return None
    _sx0, _sx1, _sy, labels = _tab_strip
    want = views.cycle_tab(labels, pane_label(active_pane_area()), delta)
    if want is None:
        return None
    return viewport_tab_click(want)


# ---------------------------------------------------------------------------
# THE OSNAP PANEL, BOTTOM-LEFT 
#
# Verified: the reference CAD application docks the Osnap control in the LOWER HALF of a 117 px LEFT band
# (`the reference layout description`), i.e. bottom-left, sharing a container with Selection Filters.
# Ours used to sit in Blender's right-hand `UI` region, which is the only region that
# hosts Python Panels -- so it was in the wrong place and said so in its docstring.
#
# It is drawn as a POST_PIXEL overlay in the bottom-left pane, the same shape as the
# viewport tab strip and the autocomplete list. **One honest difference, stated here
# and not papered over: the reference CAD application DOCKS this band, so its viewports start to the right of
# it; ours FLOATS, so it covers the bottom-left corner of that viewport.** Making it a
# true dock means another Area and therefore the layout state machine, which is the
# one part of this file that has repeatedly gone wrong -- so it is deliberately not
# attempted here.
# ---------------------------------------------------------------------------

#: Verified: the reference CAD application's default layout has the panel visible (`visible="True"` on the
#: OSnap dock bar in the reference layout description, and it is in both screenshots).
_osnap_panel_visible = True

#: Geometry the panel last DREW: (x0, y_bottom, [(label, x0,y0,x1,y1)]).
_osnap_panel_boxes = []
_osnap_panel_origin = None


def osnap_panel_visible():
    """Is the Osnap panel showing?"""
    return _osnap_panel_visible


def set_osnap_panel_visible(on):
    """Show or hide it. Returns the state in force."""
    global _osnap_panel_visible
    _osnap_panel_visible = bool(on)
    _tag_redraw_viewports()
    return _osnap_panel_visible


def get_osnap_panel_boxes():
    """The rows as last drawn, in window coords (for the gate and introspection)."""
    return list(_osnap_panel_boxes)


def _osnap_panel_row_state():
    """``[(label, on, implemented)]`` in the reference CAD application's Verified panel order.

    `Project` and `Disable` are panel-wide flags rather than snap modes, so their
    "on" comes from the settings object's own fields; both are implemented.
    """
    settings = snap_settings()
    if settings is None:
        return []
    enabled = set(settings.enabled)
    out = []
    for label in views.OSNAP_PANEL_ORDER:
        if label == "Disable":
            out.append((label, bool(settings.disabled), True))
        elif label == "Project":
            out.append((label, bool(settings.project), True))
        else:
            out.append((label, label in enabled, label in osnap.IMPLEMENTED))
    return out


# ---------------------------------------------------------------------------
# THE LEFT DOCK BAND
#
# The left dock is 117 pixels wide, with Sidebar above Osnap and viewports to its
# right.
#
# It is an INFO area, not a VIEW_3D, and that choice is load-bearing: `_view3d_areas`,
# `_pane_key`, `active_pane_key`, the viewport tab strip and the maximize machine all
# count VIEW_3D areas, so a fifth one would be read as a fifth VIEWPORT. INFO is also
# the pattern that already works -- the command bar is an INFO area whose WINDOW
# region we paint entirely ourselves.
#
# Identified by its AREA KEY (its x/y), not by holding the Area: area references do
# not survive a reflow, and `area_close` during a maximize invalidates every one of
# them (a documented pitfall of this file).
# ---------------------------------------------------------------------------

#: ``(x, y)`` of the left dock band's area, or None when it was not carved.
_left_band_key = None

#: Caption for the combined left dock band.
LEFT_BAND_CAPTION = "Sidebar"

#: Color of the small triangle marking a linked toolbar.
SIDEBAR_MARK_COLOR = (0.1, 0.1, 0.1, 1.0)
#: Reuse the dock/tab grey when hovering a linked-toolbar cell.
SIDEBAR_HOVER_COLOR = views.gpu_rgba(views.TAB_BAND_BG)
SIDEBAR_FLYOUT_HOVER_COLOR = views.gpu_rgba(views.TAB_ACTIVE_BG)
SIDEBAR_FLYOUT_PRESSED_COLOR = views.gpu_rgba(views.TAB_BORDER)
SIDEBAR_TOOLTIP_BG = (0.08, 0.08, 0.08, 0.94)
SIDEBAR_TOOLTIP_BORDER = views.gpu_rgba(views.TAB_BORDER)
SIDEBAR_TOOLTIP_TEXT = (0.95, 0.95, 0.95, 1.0)


def _set_left_band_key(area):
    global _left_band_key
    _left_band_key = (area.x, area.y) if area is not None else None
    return _left_band_key


def left_band_area():
    """The left dock band's Area, re-found by geometry, or None.

    Re-found rather than held, for the reason in the block comment above. Matched on
    being an INFO band on the LEFT (`_band_side`) and then on x -- the side test is
    what keeps this from returning the RIGHT band, which is equally tall and narrow.
    """
    if _left_band_key is None:
        return None
    try:
        areas = list(bpy.context.screen.areas)
    except Exception:  # noqa: BLE001
        return None
    cands = [a for a in areas
             if a.type == "INFO" and _band_side(a) == "left"]
    if not cands:
        return None
    want_x = _left_band_key[0]
    return min(cands, key=lambda a: abs(a.x - want_x))


def left_band_present():
    """True when the band was carved and is still there."""
    return left_band_area() is not None


# ---------------------------------------------------------------------------
# THE RIGHT DOCK BAND -- the reference CAD application's tabbed panel container.
#
# The right dock is one 200-pixel full-height tabbed panel with an icon-only vertical
# strip on its far-right edge.
#
# Same INFO-not-VIEW_3D choice as the left band, and for the same load-bearing
# reason: a fifth VIEW_3D would be counted as a fifth VIEWPORT by `_view3d_areas`,
# `active_pane_key`, the tab strip and the maximize machine.
# ---------------------------------------------------------------------------

#: ``(x, y)`` of the right dock band's area, or None when it was not carved.
_right_band_key = None

#: Index into `rightpanel.TABS` of the tab currently showing. Starts at the reference CAD application's own
#: `selected_item` (Layers).
_right_tab = rightpanel.DEFAULT_TAB


def _set_right_band_key(area):
    global _right_band_key
    _right_band_key = (area.x, area.y) if area is not None else None
    return _right_band_key


def right_band_area():
    """The right dock band's Area, re-found by geometry, or None."""
    if _right_band_key is None:
        return None
    try:
        areas = list(bpy.context.screen.areas)
    except Exception:  # noqa: BLE001
        return None
    cands = [a for a in areas
             if a.type == "INFO" and _band_side(a) == "right"]
    if not cands:
        return None
    want_x = _right_band_key[0]
    return min(cands, key=lambda a: abs(a.x - want_x))


def right_band_present():
    """True when the right band was carved and is still there."""
    return right_band_area() is not None


def right_tab_index():
    """Which of `rightpanel.TABS` is showing."""
    return _right_tab


def set_right_tab(index):
    """Show tab `index`. Returns the line to log, or None.

    Reports an unbuilt panel BY NAME rather than switching to a blank one, which is
    rule 4 applied to chrome: the tab really does change (the reference CAD application's would), and the
    message is how the user learns the body is not there yet.
    """
    global _right_tab
    if not (0 <= index < len(rightpanel.TABS)):
        return None
    _right_tab = index
    _tag_redraw_viewports()
    tab = rightpanel.TABS[index]
    if not tab.built:
        return f"{tab.name} panel is not implemented yet ({tab.reason})"
    return None


#: The tab boxes the last draw recorded, in WINDOW coordinates, for hit-testing.
#: Recorded by the draw for the reason the sidebar's cells and the command bar's
#: option boxes are: the thing hit-tested is then the thing that was drawn.
_right_tab_boxes = []


def right_tab_at(mouse_x, mouse_y):
    """Index of the right band's tab under the window cursor, or None."""
    return rightpanel.hit_tab(mouse_x, mouse_y, _right_tab_boxes)


#: Display panel state and last-drawn hit boxes (window coordinates).
_display_panel_boxes = {"scope": [], "modes": [], "viewports": []}
_display_panel_scope = "Active"


def display_panel_rows():
    """``[(viewport label, mode, active), ...]`` in visual pane order."""
    active = active_pane_area()
    areas = sorted(_view3d_areas(), key=lambda a: (-a.y, a.x))
    return [(pane_label(area), display_mode_of(area), area == active) for area in areas]


def display_panel_hit(mouse_x, mouse_y):
    if rightpanel.TABS[_right_tab].name != "Display":
        return None
    return rightpanel.display_panel_hit(mouse_x, mouse_y, _display_panel_boxes)


def display_panel_click(kind, value):
    """Apply one drawn Display-panel control. Returns the line to log."""
    global _display_panel_scope
    if kind == "scope":
        if value not in ("Active", "All"):
            return None
        _display_panel_scope = value
        _tag_redraw_bands()
        return f"Display panel: apply to {value.lower()} viewport{'s' if value == 'All' else ''}"
    if kind == "viewport":
        area = next((a for a in _view3d_areas() if pane_label(a) == value), None)
        if area is None:
            return f"Display panel: viewport {value!r} is unavailable"
        set_active_pane(area)
        _tag_redraw_viewports()
        _tag_redraw_bands()
        return f"Active viewport: {value}"
    if kind == "mode":
        msg = set_display_mode_scope(value, _display_panel_scope)
        _tag_redraw_viewports()
        _tag_redraw_bands()
        return msg
    return None


def _draw_display_panel(shader, region, box):
    """Draw the working common-mode subset of the reference CAD application's Display panel."""
    global _display_panel_boxes
    rows = display_panel_rows()
    modes = [name for name, _key, implemented, _shading in views.DISPLAY_MODES
             if implemented]
    lay = rightpanel.display_panel_layout(
        box["body"], [name for name, _mode, _active in rows], modes)
    _display_panel_boxes = {
        key: [(kind, value, region.x + x0, region.y + y0,
               region.x + x1, region.y + y1)
              for kind, value, x0, y0, x1, y1 in lay[key]]
        for key in ("scope", "modes", "viewports")
    }
    current = next((mode for _name, mode, active in rows if active), None)
    if _display_panel_scope == "All":
        unique = {mode for _name, mode, _active in rows}
        current = next(iter(unique)) if len(unique) == 1 else None
    active_view = next((name for name, _mode, active in rows if active), None)
    bg = views.gpu_rgba(views.LAYER_LIST_BG)
    hover = views.gpu_rgba(views.TAB_BAND_BG)
    selected = views.gpu_rgba(views.TAB_ACTIVE_BG)
    border = views.gpu_rgba(views.TAB_BORDER)
    ink = views.gpu_rgba(views.OSNAP_PANEL_TEXT)

    def paint(entries, selected_value):
        for _kind, value, x0, y0, x1, y1 in entries:
            _draw_rect(shader, x0, y0, x1, y1,
                       selected if value == selected_value else bg)
            _draw_rect(shader, x0, y0, x1, y0 + 1, border)
            _draw_rect(shader, x0, y1 - 1, x1, y1, border)
            _draw_rect(shader, x0, y0, x0 + 1, y1, border)
            _draw_rect(shader, x1 - 1, y0, x1, y1, border)

    shader.bind()
    paint(lay["scope"], _display_panel_scope)
    paint(lay["modes"], current)
    paint(lay["viewports"], active_view)
    for _name, x0, y0, _x1, _y1 in lay["labels"]:
        _draw_rect(shader, x0, y0, x0 + 3, y0 + 3, hover)
    for name, x0, y0, _x1, _y1 in lay["labels"]:
        _draw_text(x0, y0 + 3, name, 11, ink)
    for _kind, value, x0, y0, _x1, _y1 in lay["scope"] + lay["modes"]:
        _draw_text(x0 + 6, y0 + 7, value, 11, ink)
    by_name = {name: mode for name, mode, _active in rows}
    for _kind, value, x0, y0, _x1, _y1 in lay["viewports"]:
        _draw_text(x0 + 6, y0 + 7, f"{value} — {by_name.get(value) or '-'}", 11, ink)


def draw_right_band():
    """POST_PIXEL draw handler for the right dock band's WINDOW region.

    Shares `SpaceInfo` with the command bar AND with the left band, so it
    establishes which of the three it is in before painting anything -- the left
    band's handler had only the "is a band" test, which was enough while there was
    one band and wrong the moment there were two.

    Draws the band, its caption row, four-tab strip and the selected working body.
    Each body exposes only behavior actually wired to document/view state; unsupported
    deeper controls are omitted rather than drawn as dead chrome.
    """
    global _right_tab_boxes, _display_panel_boxes
    context = bpy.context
    area = getattr(context, "area", None)
    region = getattr(context, "region", None)
    if area is None or region is None or area.type != "INFO":
        return
    if _band_side(area) != "right":
        return
    if region.width < 4 or region.height < 4:
        return
    try:
        import gpu
    except Exception:  # noqa: BLE001
        return
    shader = _uniform_color_shader()
    if shader is None:
        return

    box = rightpanel.layout(region.width, region.height, title_h=0)
    _right_tab_boxes = [(region.x + x0, region.y + y0, region.x + x1, region.y + y1)
                        for x0, y0, x1, y1 in box["tabs"]]

    # re-assert each LAYER's On/Lock here, because this handler is
    # guaranteed to run AFTER anything that rebuilt the scene -- including
    # `rebuild_display`, which recreates every Blender object and resets its hide
    # flags. `_tag_redraw_viewports` alone was NOT enough and the gate caught it:
    # switching a layer off and then drawing something un-hid it again. Safe from a
    # draw handler because `apply_layer_state` writes only on a DIFFERENCE, so it
    # cannot spin redraw -> apply -> redraw, and early-outs entirely when every layer
    # is on and unlocked.
    try:
        apply_layer_state()
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] layer state not applied: {e!r}")

    prev_blend = gpu.state.blend_get()
    gpu.state.blend_set("ALPHA")
    try:
        shader.bind()
        # the panel ground
        _draw_rect(shader, 0, 0, region.width, region.height,
                   views.gpu_rgba(views.OSNAP_PANEL_BG))
        # the tab strip sits on a DARKER ground than the panel -- that contrast is
        # what makes the active tab (painted in the panel's colour) read as selected
        sx0, sy0, sx1, sy1 = box["strip"]
        _draw_rect(shader, sx0, sy0, sx1, sy1, views.gpu_rgba(views.TAB_BAND_BG))
        for i, (x0, y0, x1, y1) in enumerate(box["tabs"]):
            if i == _right_tab:
                _draw_rect(shader, x0, y0, x1, y1,
                           views.gpu_rgba(views.TAB_ACTIVE_BG))
            else:
                # a hairline between inactive tabs, so four of them are countable
                _draw_rect(shader, x0 + 3, y0, x1 - 3, y0 + 1,
                           views.gpu_rgba(views.TAB_BORDER))
        # the band's own LEFT edge, so it reads as a dock and not as a gap. Mirror
        # of the left band's right edge.
        _draw_rect(shader, 0, 0, 1, region.height,
                   views.gpu_rgba(views.OSNAP_PANEL_BORDER))
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] right band draw failed: {e!r}")
    finally:
        gpu.state.blend_set(prev_blend)

    # Tab ICONS, in their own blend scope: textured quads bind a different shader
    # and must not be nested inside the flat-colour pass above (the same rule the
    # left sidebar and the Osnap panel follow).
    prev2 = gpu.state.blend_get()
    gpu.state.blend_set("ALPHA")
    try:
        _draw_right_tab_icons(box)
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] right band tab icons failed: {e!r}")
    finally:
        gpu.state.blend_set(prev2)

    _draw_right_band_text(box)

    # The ACTIVE panel's body. Each branch owns and clears its own hit boxes.
    global _layer_rows, _layer_row_objs
    if rightpanel.TABS[_right_tab].name == "Layers":
        try:
            _draw_layers_panel(shader, region, box)
        except Exception as e:  # noqa: BLE001
            print(f"[narwhal3d] layers panel failed: {e!r}")
    else:
        _layer_rows, _layer_row_objs = [], []
    if rightpanel.TABS[_right_tab].name == "Properties":
        try:
            _draw_properties_panel(region, box)
        except Exception as e:  # noqa: BLE001
            print(f"[narwhal3d] properties panel failed: {e!r}")
    if rightpanel.TABS[_right_tab].name == "Display":
        try:
            _draw_display_panel(shader, region, box)
        except Exception as e:  # noqa: BLE001
            print(f"[narwhal3d] display panel failed: {e!r}")
    else:
        _display_panel_boxes = {"scope": [], "modes": [], "viewports": []}
    if rightpanel.TABS[_right_tab].name == "Bug Report":
        try:
            _draw_bug_panel(shader, region, box)
        except Exception as e:  # noqa: BLE001
            print(f"[narwhal3d] bug report panel failed: {e!r}")
    else:
        _bug["lay"] = None


#: the New Layer button's box (window coordinates), recorded by the draw
_layer_new_btn = None


def layer_new_button_at(mouse_x, mouse_y):
    b = _layer_new_btn
    return (b is not None and rightpanel.TABS[_right_tab].name == "Layers"
            and b[0] <= mouse_x < b[2] and b[1] <= mouse_y < b[3])


#: the layer ROW boxes the last draw recorded, in WINDOW coordinates,
#: paired with the `layers.Layer` each one shows. Recorded by the draw for the reason
#: the sidebar's cells and the tab boxes are: the thing hit-tested is then the thing
#: that was drawn.
_layer_rows = []
_layer_row_objs = []

#: True once `apply_layer_state` has enforced a non-default layer state. Needed so
#: that turning the LAST off layer back on still runs one final pass to un-hide its
#: objects -- without it the early-out would see "nothing to enforce" and leave them
#: hidden forever.
_layer_state_applied = False
#: ``[key, result]`` of the last `apply_layer_state` pass (see its early-out)
_layer_state_key = [None, (0, 0)]
#: how many FULL passes `apply_layer_state` has made (for tests: an early-out adds 0)
_layer_state_runs = [0]


def layer_panel_rows():
    """``[(Layer, box)]`` from the last draw, for the gates and the click path."""
    return list(zip(_layer_row_objs, _layer_rows))


def apply_layer_state():
    """Make the viewport obey each LAYER's On/Lock, not just each object's Mode.

    `bridge.apply_document_visibility`  already honours an
    OBJECT's own `Mode` -- Hidden objects are not drawn, Locked ones are drawn but
    unpickable. the reference CAD application also hides and locks by LAYER (the reference CAD application documentation: "On/Off --
    Controls global visibility of a layer"; "Lock/Unlock -- Controls the selectable
    state of the objects on a layer"), and nothing applied that, so toggling a layer
    in the panel would have changed the document and left the screen unchanged.

    Both conditions are evaluated HERE rather than OR-ed onto whatever the object-level
    pass happened to leave behind, so the result does not depend on which ran last:

        hidden      = object Mode is Hidden  OR  the layer is off
        unpickable  = object Mode is Locked  OR  the layer is locked

    Returns ``(hidden, locked)`` counts. `hide_select` is the lock mechanism for the
    same measured reason the object-level lock uses it: it makes the object
    click-through rather than invisible, which is what the reference CAD application's locked state does.

    Called from `_tag_redraw_viewports` so the toggle persists across `rebuild_display`,
    which recreates Blender objects and resets their hide flags. Reasserting state on
    existing repaint paths avoids coupling command execution to this display concern;
    the early-out keeps the common all-visible, unlocked case to one short layer scan.
    """
    global _layer_state_applied

    import bpy

    model = getattr(app_state, "model", None)
    file3dm = getattr(model, "file", None)
    if file3dm is None:
        return (0, 0)
    try:
        doc_layers = list(file3dm.Layers)
    except Exception:  # noqa: BLE001
        doc_layers = []
    # EARLY OUT: nothing changed since the last pass -- not the document (any recorded
    # change: Hide, Show, Lock, Unlock, undo, Open), not a layer's On/Lock, and not the
    # scene (`_curve_dg["vis"]` moves when objects are linked or removed, which is what
    # `rebuild_display` does). Without it the pass would walk every object every redraw.
    #
    # It used to early-out whenever every LAYER was on and unlocked, which also skipped
    # each OBJECT's own Mode: `Hide` set Mode = Hidden in the document and the viewport
    # never changed (found while making big files fast -- `bridge.apply_document_
    # visibility` was never called by anything).
    layer_flags = tuple((bool(l.Visible), bool(l.Locked)) for l in doc_layers)
    key = (_doc_key(), layer_flags, _curve_dg["vis"])
    if key == _layer_state_key[0]:
        return _layer_state_key[1]
    _layer_state_applied = any((not v) or lk for v, lk in layer_flags)
    _layer_state_runs[0] += 1
    import numpy as np

    # The three flags of EVERY object read in bulk (`foreach_get`), then compared in
    # Python. Reading them one RNA property at a time made this
    # pass 120-150 ms of the first frame after every document change.
    # names straight from C (`keys`), objects fetched only to WRITE: building 5,702
    # wrappers and reading each `.name` was most of this pass's ~15 ms (Verified,
    # on the first frame after a Delete)
    keys = bpy.data.objects.keys()
    index = dict(zip(keys, range(len(keys))))
    oblist = None
    flags = {}
    for prop in ("hide_viewport", "hide_render", "hide_select"):
        arr = np.zeros(len(keys), dtype=bool)
        if keys:
            bpy.data.objects.foreach_get(prop, arr)
        flags[prop] = arr
    # as plain lists: indexing a numpy array one element at a time (three per object,
    # below) costs several times a list's
    hv, hr, hs = (flags[p].tolist() for p in ("hide_viewport", "hide_render",
                                               "hide_select"))

    try:
        import rhino3dm as r3

        mode_hidden = int(r3.ObjectMode.Hidden)
        mode_locked = int(r3.ObjectMode.Locked)
    except Exception:  # noqa: BLE001
        mode_hidden = mode_locked = -1

    hidden = locked = 0
    wrote = False
    n_layers = len(layer_flags)
    # the shared document rows (`_doc_rows`): one walk per document state for everyone
    for name, mode, idx, _geom, _attrs in _doc_rows():
        if not name:
            continue
        i = index.get(name)
        if i is None:
            continue
        if mode is None or idx is None:
            continue
        layer_off = layer_locked = False
        if 0 <= idx < n_layers:
            layer_off = not layer_flags[idx][0]
            layer_locked = layer_flags[idx][1]
        is_hidden = (mode == mode_hidden) or layer_off
        is_locked = (mode == mode_locked) or layer_locked
        want_sel = is_locked or is_hidden
        hidden += 1 if is_hidden else 0
        locked += 1 if is_locked else 0
        if (hv[i] == is_hidden and hr[i] == is_hidden and hs[i] == want_sel
                and not is_hidden):
            continue                      # agrees with the document: touch nothing
        if oblist is None:
            oblist = bpy.data.objects.values()
        ob = oblist[i]
        # Write ONLY on a difference. This is what makes it safe to call from a draw
        # handler: an RNA write tags a depsgraph update and would redraw, so
        # unconditional writes would spin redraw -> apply -> redraw forever. Once the
        # flags agree with the document this loop performs no writes at all.
        if ob.hide_viewport != is_hidden:
            ob.hide_viewport = is_hidden
            wrote = True
        if ob.hide_render != is_hidden:
            ob.hide_render = is_hidden
        # a HIDDEN object must not also be grabbable through its hidden state -- nor stay
        # SELECTED: the reference CAD application's Hide leaves nothing hidden in the selection, and a selected
        # hidden curve went on drawing its control points
        if is_hidden:
            try:
                if ob.select_get():
                    ob.select_set(False)
                    wrote = True
            except Exception:  # noqa: BLE001
                pass
        if ob.hide_select != want_sel:
            ob.hide_select = want_sel
    if wrote:
        # the curve pass re-reads its hidden set THIS frame (the depsgraph will only
        # report the change after it), and every pane repaints with the new state
        _curve_dg["vis"] += 1
        try:
            for area in bpy.context.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
        except Exception:  # noqa: BLE001
            pass
        key = (key[0], key[1], _curve_dg["vis"])
    _layer_state_key[0], _layer_state_key[1] = key, (hidden, locked)
    return (hidden, locked)


# ---------------------------------------------------------------------------
# Double-clicking a layer name starts in-place rename.
# Verified (the reference CAD application documentation): the panel edits names in place -- "While editing a layer
# name, pressing Tab or Insert accepts the name editing and creates a new layer" -- and
# the context menu's "Rename: Edits the name of the selected layer".
# ---------------------------------------------------------------------------

#: the in-place name edit: ``{"name": original name, "field": TextField}`` or None
_layer_edit = None
#: (time, row) of the last click on a NAME cell -- double-click by timing, the one
#: detector `title_click` documents (only PRESS reaches the modal)
_last_layer_name_click = (0.0, None)


def layer_rename_begin(layer_name):
    """Start editing `layer_name` in place, its text selected. Returns True."""
    global _layer_edit
    f = textfield.TextField(layer_name, multiline=False, max_chars=255)
    f.select_all()
    _layer_edit = {"name": layer_name, "field": f}
    _tag_redraw_bands()
    return True


def layer_rename_editing():
    return _layer_edit is not None


def layer_rename_commit(and_new=False):
    """Accept the edit (an undoable `Layer Rename`). With `and_new` (Tab / Insert) also
    create a new layer and start editing ITS name, as the reference CAD application does. Returns a message."""
    global _layer_edit
    from . import layer_commands

    ed, _layer_edit = _layer_edit, None
    if ed is None:
        return None
    new = ed["field"].text.strip()
    msg = None
    if new and new != ed["name"]:
        app_state.ctx.begin_undo("Layer")
        try:
            msg = layer_commands.rename_layer(ed["name"], new)[1]
        finally:
            app_state.ctx.end_undo()
    if and_new:
        app_state.ctx.begin_undo("Layer")
        try:
            ok, msg2 = layer_commands.new_layer()
        finally:
            app_state.ctx.end_undo()
        msg = msg2 if msg is None else f"{msg}; {msg2}"
        if ok:
            layer_rename_begin(app_state.model.layers()[-1]["name"])
    _tag_redraw_bands()
    return msg


def layer_rename_cancel():
    global _layer_edit
    _layer_edit = None
    _tag_redraw_bands()


def layer_rename_key(kind, char="", shift=False):
    """A key while a layer name is being edited. Returns a message to log, "handled",
    or None when nothing is being edited."""
    if _layer_edit is None:
        return None
    f = _layer_edit["field"]
    if kind == "enter":
        return layer_rename_commit() or "handled"
    if kind in ("tab", "insert"):
        return layer_rename_commit(and_new=True) or "handled"
    if kind == "esc":
        layer_rename_cancel()
        return "handled"
    actions = {"back": f.backspace, "delete": f.delete, "select_all": f.select_all,
               "left": lambda: f.move(-1, select=shift),
               "right": lambda: f.move(1, select=shift),
               "home": lambda: f.home(select=shift), "end": lambda: f.end(select=shift)}
    if kind in actions:
        actions[kind]()
    elif kind in ("char", "paste"):
        f.insert(char)
    _tag_redraw_bands()
    return "handled"


def layer_panel_press(row_index, column_index, now=None):
    """A left PRESS on a layer row, with double-click detection on the NAME cell.
    Returns a line to log (or None). A second press on the same name within
    `DOUBLE_CLICK_SECONDS` starts the in-place rename."""
    global _last_layer_name_click
    import time

    if _layer_edit is not None:
        msg = layer_rename_commit()          # a click elsewhere accepts the edit
        if msg:
            app_state.log(msg)
    now = time.monotonic() if now is None else now
    cols = rightpanel.COLUMNS
    name_col = 0 <= column_index < len(cols) and cols[column_index] == "Name"
    if name_col and 0 <= row_index < len(_layer_row_objs):
        last_t, last_row = _last_layer_name_click
        _last_layer_name_click = (now, row_index)
        if last_row == row_index and now - last_t <= DOUBLE_CLICK_SECONDS:
            _last_layer_name_click = (0.0, None)
            layer_rename_begin(_layer_row_objs[row_index].name)
            return None
    else:
        _last_layer_name_click = (0.0, None)
    return layer_panel_click(row_index, column_index)


#: the layer a right-click menu was opened on (Blender menus' draw takes no arguments)
_layer_menu_target = None

#: Verified (the reference CAD application documentation, "Layer context menu options"), in the reference CAD application's order, for a
#: right-click on a layer's name. (label, action) -- action None = not built yet.
LAYER_MENU = (
    ("Select Objects", "select"),
    ("Select Sublayer Objects", None),
    ("Change Object Layer", "change"),
    ("Copy Objects to Layer", None),
    None,
    ("New Layer", "new"),
    ("New Sublayer", None),
    ("Duplicate Layer", None),
    ("Duplicate Layer and Objects", None),
    None,
    ("One Layer On", "one_on"),
    ("Match Properties", None),
    None,
    ("Delete", "delete"),
    ("Rename", "rename"),
)


def layer_menu_action(action, layer_name=None):
    """Run one Layers-panel context-menu action on `layer_name`. Returns a message."""
    from . import layer_commands

    name = layer_name or _layer_menu_target
    if not name:
        return None
    tree = app_state.layers
    if action == "rename":
        layer_rename_begin(name)
        return None
    if action == "change":
        # Verified: "Selects objects in the model to change to selected layer" -- the
        # selection if there is one, else ChangeLayer asks for objects, layer filled in
        chosen = sorted(selected_display_names())
        if chosen:
            app_state.ctx.begin_undo("ChangeLayer")
            try:
                return layer_commands.change_object_layer(chosen, name)[1]
            finally:
                app_state.ctx.end_undo()
        app_state.start_command("ChangeLayer")
        cmd = app_state.active_command
        if cmd is not None:
            cmd.steps = cmd.steps[:1]
            cmd.preset_layer = name
        return None
    if action == "select":
        # Verified: "Selects objects on the selected layers"
        idx = next((i["index"] for i in app_state.model.layers() if i["name"] == name),
                   None)
        n = 0
        for fobj in app_state.model.file.Objects:
            if int(fobj.Attributes.LayerIndex) != idx:
                continue
            ob = bpy.data.objects.get(fobj.Attributes.Name or "")
            if ob is not None and bridge.display_visible(ob) and not ob.hide_select:
                ob.select_set(True)
                n += 1
        _tag_redraw_viewports()
        return f"{n} object(s) selected on layer {name!r}"
    if action == "new":
        app_state.ctx.begin_undo("Layer")
        try:
            return layer_commands.new_layer()[1]
        finally:
            app_state.ctx.end_undo()
    if action == "delete":
        app_state.ctx.begin_undo("Layer")
        try:
            return layer_commands.delete_layer(name)[1]
        finally:
            app_state.ctx.end_undo()
    if action == "one_on":
        # Verified: "Sets the selected layer on and turns all others off"
        for L in tree.iter_all():
            want = L.name == name
            if L.visible != want:
                tree.set_visible(L, want)
                _sync_layer_to_document(L)
        apply_layer_state()
        _tag_redraw_viewports()
        return f"{name} is the only layer on"
    return f"{action} is not implemented yet"


def show_layer_menu(layer_name):
    """Right-click on a layer row: the reference CAD application's layer context menu. True if it opened."""
    global _layer_menu_target
    _layer_menu_target = layer_name
    try:
        bpy.ops.wm.call_menu(name="NARWHAL_MT_layer_context")
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] layer menu could not open: {e!r}")
        return None
    return True


def layer_panel_click(row_index, column_index):
    """Act on a click in the Layers list. Returns a line to log, or None.

    Verified behaviours (the reference CAD application documentation):

    * **Name / Current** -- "A check and shading indicates current layer." Clicking
      either makes that layer current, which is what a new object then lands on.
    * **On / Off** -- "Controls global visibility of a layer."
    * **Lock / Unlock** -- "Controls the selectable state of the objects on a layer."

    `Color` and `Material` report that they are not built rather than doing nothing:
    the reference CAD application opens a colour picker and a material browser, and we have neither.
    """
    if not (0 <= row_index < len(_layer_row_objs)):
        return None
    layer = _layer_row_objs[row_index]
    cols = rightpanel.COLUMNS
    name = cols[column_index] if 0 <= column_index < len(cols) else None
    tree = getattr(app_state, "layers", None)
    if tree is None:
        return None

    msg = None
    if name in ("Name", "Current"):
        try:
            app_state.set_current_layer(layer)
        except Exception as e:  # noqa: BLE001
            return f"could not make {layer.name!r} current: {e}"
        msg = f"{layer.name} is the current layer"
    elif name == "On":
        want = not layer.visible
        tree.set_visible(layer, want)
        _sync_layer_to_document(layer)
        msg = f"{layer.name} is {'on' if want else 'off'}"
    elif name == "Lock":
        want = not layer.locked
        tree.set_locked(layer, want)
        _sync_layer_to_document(layer)
        msg = f"{layer.name} is {'locked' if want else 'unlocked'}"
    elif name == "Color":
        # Verified (the reference CAD application documentation): clicking the colour swatch opens a colour
        # picker; the desktop's own (kdialog / zenity), via colordialog.
        from . import colordialog

        status, rgb = colordialog.ask_color(f"Layer color: {layer.name}",
                                            tuple(layer.color or (0, 0, 0))[:3])
        if status == colordialog.UNAVAILABLE:
            return ("no colour picker is available (kdialog / zenity) -- the layer "
                    "colour cannot be chosen here yet")
        if status != colordialog.OK:
            return None
        return set_layer_color(layer, rgb)
    elif name == "Material":
        return (f"the layer {name} picker is not implemented yet "
                f"({layer.name})")
    else:
        return None

    apply_layer_state()
    _tag_redraw_viewports()
    return msg


def _shaded_rgba(rgb):
    """The SHADED surface colour for a layer colour.

    Black keeps the light-grey default surface, while any other layer color tints it.
    """
    r, g, b = (int(c) for c in tuple(rgb)[:3])
    if (r, g, b) == (0, 0, 0):
        return (0.8, 0.8, 0.8, 1.0)
    return (r / 255.0, g / 255.0, b / 255.0, 1.0)


def set_layer_color(layer, rgb):
    """Give `layer` the colour `rgb` (0..255): the live tree, the .3dm layer (so it is
    SAVED), and the display -- surfaces through the layer material's viewport colour
    (Verified: Solid shading reads `Material.diffuse_color`, not the node colour), curves
    through `draw_curves`. Returns a line to log."""
    r, g, b = (max(0, min(255, int(c))) for c in rgb[:3])
    layer.color = (r, g, b)
    model = getattr(app_state, "model", None)
    # undoable: the snapshot carries the layer table 
    app_state.ctx.begin_undo("Layer color")
    app_state.ctx.note_change("Layer color")
    index = None
    if model is not None:
        for info in model.layers():
            if info["name"] == layer.name:
                index = info["index"]
                model.file.Layers[index].Color = (r, g, b, 255)
                break
    if index is not None:
        mat = bpy.data.materials.get(f"narwhal_layer_{index}")
        if mat is not None:
            mat.diffuse_color = _shaded_rgba((r, g, b))
            try:
                bsdf = mat.node_tree.nodes.get("Principled BSDF")
                if bsdf:
                    bsdf.inputs["Base Color"].default_value = (r / 255.0, g / 255.0,
                                                               b / 255.0, 1.0)
            except Exception:  # noqa: BLE001
                pass
    app_state.ctx.end_undo()
    _layer_curve_cache["key"] = None
    _tag_redraw_viewports()
    return f"{layer.name} color is now {r},{g},{b}"


# CURVES in their layer colour. Verified (probe): Blender draws a curve's display
# edges in the theme's wire colour whatever the object or its material says (object
# colour + wireframe_color_type OBJECT left the line unchanged). Blender no longer draws
# curves at all (`draw_curves`); this list supplies the layer colour of each curve on a
# non-black layer -- and keeps the layer materials' viewport colour in step.
#
# Gumball-drag performance: Verified 10.4 s for the first redraw after a one-curve
# Gumball move on a large document because ANY change re-sampled every curve. Points are
# now kept PER OBJECT (by id) with a content fingerprint (`_curve_fingerprint`). When
# the document key moved for a reason nobody described, every curve's fingerprint is
# re-checked and only curves whose CONTENT differs are re-sampled -- so an undo, a Move
# command or an Open is still exact. When the change was described
# (`_note_objects_changed`), only the named objects are re-read.
_layer_curve_cache = {"key": None, "items": [], "doc": None, "stale": set(),
                      "verify": False, "pts": {}, "resampled": 0}
_layer_curve_handlers = []


def _curve_fingerprint(geom):
    """A digest of a curve's full openNURBS serialisation, or None (= always re-sample).

    `Encode` is deterministic, so
    equal digests mean equal control points, knots, weights and segments. (It is
    `bridge.curve_fingerprint`, which an Open's workers key their curve points by.)
    """
    return bridge.curve_fingerprint(geom)


def _layer_curve_items():
    model = getattr(app_state, "model", None)
    if model is None:
        return []
    cache = _layer_curve_cache
    doc = _doc_key()
    layers = model.layers()
    key = (doc, tuple(tuple(i["color"]) for i in layers),
           tuple(i["visible"] for i in layers))
    if cache["key"] == key and not cache["stale"] and not cache["verify"]:
        return cache["items"]
    import rhino3dm as r3

    # points may be reused WITHOUT re-checking content only when the document is in the
    # state they were read in, apart from the reported `stale` names
    trust = cache["doc"] == doc and doc is not None and not cache["verify"]
    stale = cache["stale"]
    old_pts = cache["pts"]
    new_pts = {}
    # surfaces: each layer material's VIEWPORT colour follows its layer -- also for a
    # file just opened or an undo, where nothing went through `set_layer_color`
    for info in layers:
        mat = bpy.data.materials.get(f"narwhal_layer_{info['index']}")
        if mat is not None:
            want = _shaded_rgba(info["color"])
            if tuple(round(c, 6) for c in mat.diffuse_color) != tuple(
                    round(c, 6) for c in want):
                mat.diffuse_color = want
    items = []
    for fobj in model.file.Objects:
        geom = fobj.Geometry
        if not isinstance(geom, r3.Curve):
            continue
        li = int(fobj.Attributes.LayerIndex)
        if not (0 <= li < len(layers)) or not layers[li]["visible"]:
            continue
        col = layers[li]["color"][:3]
        if tuple(col) == (0, 0, 0):
            continue                      # the theme already draws black
        attrs = fobj.Attributes
        name = attrs.Name or ""
        oid = str(attrs.Id)
        prev = old_pts.get(oid)
        if trust and prev is not None and name.lower() not in stale:
            fp, pts = prev
        else:
            fp = _curve_fingerprint(geom)
            if prev is not None and fp is not None and prev[0] == fp:
                pts = prev[1]
            else:
                # Re-sampling every curve here dominated the
                # first redraw after Open -- so Open's workers compute exactly
                # `_curve_points` of each, keyed by this same fingerprint
                pts = bridge.take_prefetched_overlay(fp)
                if pts is None:
                    pts = _curve_points(geom)
                cache["resampled"] += 1
        new_pts[oid] = (fp, pts)
        if len(pts) >= 2:
            items.append((tuple(c / 255.0 for c in col) + (1.0,), pts, name))
    cache.update(key=key, items=items, doc=doc, stale=set(), verify=False, pts=new_pts)
    return items


def layer_curve_colors():
    """``{object name: rgba}`` the curve pass draws in a LAYER colour (for tests)."""
    return dict(_curve_colour_map())


#: ``{"key", "map"}`` behind `_curve_colour_map`; the map object is replaced (never
#: mutated) on a change, so the curve pass can tell by identity.
_curve_colour_cache = {"key": None, "map": {}, "builds": 0}


def _curve_colour_map():
    """``{curve name: rgba}`` for every curve on a visible, non-black layer -- what the
    curve pass draws in its layer colour (every other curve takes the theme wire).

    The curve pass used to get this from `_layer_curve_items`, the old layer overlay's
    list, which also SAMPLES every coloured curve (keyed by an `Encode` fingerprint
    per curve) for points the curve pass never draws. This reads the
    shared document rows instead: names, layers and an isinstance test. It also keeps
    each layer material's VIEWPORT colour in step with its layer, which that function
    did for surfaces (a file just opened or an undo never went through
    `set_layer_color`).
    """
    model = getattr(app_state, "model", None)
    if model is None:
        return {}
    layers = model.layers()
    key = (_doc_key(), tuple(tuple(i["color"]) for i in layers),
           tuple(i["visible"] for i in layers))
    c = _curve_colour_cache
    if key[0] is not None and c["key"] == key:
        return c["map"]
    import rhino3dm as r3

    for info in layers:
        mat = bpy.data.materials.get(f"narwhal_layer_{info['index']}")
        if mat is not None:
            want = _shaded_rgba(info["color"])
            if tuple(round(v, 6) for v in mat.diffuse_color) != tuple(
                    round(v, 6) for v in want):
                mat.diffuse_color = want
    rgba = []
    for info in layers:
        col = tuple(info["color"][:3])
        rgba.append(None if not info["visible"] or col == (0, 0, 0)
                    else tuple(v / 255.0 for v in col) + (1.0,))
    out = {}
    rows = _doc_rows()
    for name, _mode, li, geom, _attrs in rows:
        if li is None or not (0 <= li < len(rgba)) or rgba[li] is None:
            continue
        if isinstance(geom, r3.Curve):
            out[name] = rgba[li]
    # The SAME map object when no object's colour changed: the curve pass refolds every
    # curve when the map's identity moves (`_curve_sync`), while most document changes
    # leave every colour as it was. An old entry for a name the document no longer has
    # is harmless (the map is only read for objects that exist); a curve that LEFT the
    # map but still exists (moved to a black layer) is a change.
    old = c.get("map")
    if old is not None and all(old.get(k) == v for k, v in out.items()):
        gone = old.keys() - out.keys()
        if not gone or not (gone & {r[0] for r in rows}):
            out = old
    c.update(key=key, map=out)
    c["builds"] += 1
    return out


def _is_rendered_space(space):
    """True for our Rendered mode: SOLID + its white SINGLE-colour marker.

    measured 232 ms per four-pane Rendered frame with both Blender's shadows and
    our CAD-style ground-shadow pass enabled; the custom pass alone was still 241 ms.
    Blender's duplicate pass is unnecessary, and ``color_type`` is now the cheap stable
    mode discriminator: Rendered is SINGLE white; Shaded is TEXTURE.
    """
    sh = getattr(space, "shading", None)
    return sh is not None and sh.type == "SOLID" and sh.color_type == "SINGLE"


_shadow_cache = {}
_shadow_batch_cache = {"key": None, "z": None, "draw": None}


def ground_shadow_objects():
    """``(hull, zmin, zmax)`` per visible MESHABLE object (surfaces and solids), in world
    space -- the input `ground_shadow.scene_shadow` takes.

    Verified (the reference CAD application documentation): the automatic ground plane "ignores non-meshable
    object types" -- curves and points are edge/vertex-only meshes here, so a mesh with no
    faces casts nothing. Hulls are cached per object on its transform, vertex count, mesh
    and the undo serial, because this runs on every redraw.
    """
    from . import ground_shadow

    try:
        serial = app_state.ctx.undo.top_serial
    except Exception:  # noqa: BLE001
        serial = None
    out, live = [], set()
    for ob in bpy.data.objects:
        if ob.type != "MESH" or not ob.data.polygons:
            continue
        try:
            if not ob.visible_get():
                continue
        except Exception:  # noqa: BLE001 (not in this view layer)
            continue
        me = ob.data
        mw = ob.matrix_world
        key = (tuple(round(v, 9) for row in mw for v in row), len(me.vertices), me.name,
               serial)
        live.add(ob.name)
        hit = _shadow_cache.get(ob.name)
        if hit is None or hit[0] != key:
            pts = [mw @ v.co for v in me.vertices]
            hull = ground_shadow.convex_hull([(p.x, p.y) for p in pts])
            zs = [p.z for p in pts]
            hit = (key, (hull, min(zs), max(zs)))
            _shadow_cache[ob.name] = hit
        out.append(hit[1])
    for name in list(_shadow_cache):
        if name not in live:
            del _shadow_cache[name]
    return out


def _ground_shadow_key():
    """Complete signature of the visible object hulls currently in ``_shadow_cache``."""
    return tuple((name, _shadow_cache[name][0]) for name in sorted(_shadow_cache))


def _build_ground_shadow_draw(shader, pos, alpha):
    """Build the immutable ground-shadow batch; split out for the background gate."""
    from gpu_extras.batch import batch_for_shader

    return batch_for_shader(shader, "TRIS", {
        "pos": pos, "color": [(0.0, 0.0, 0.0, a) for a in alpha]})


def _ground_shadow_draw(boxes, key, shader):
    """Cached ``(ground altitude, GPU batch)`` for the current visible scene.

    During a Gumball drag the document/display key can change on every mouse event. Keep
    drawing the last valid shadow batch while the object moves, then rebuild once after
    `_gumball_end` clears the drag. A temporarily stale shadow is preferable to a 600 ms
    batch rebuild on every event, and unlike hiding it there is no visible flicker.
    """
    if _gumball_drag is not None and _shadow_batch_cache["draw"] is not None:
        return _shadow_batch_cache["z"], _shadow_batch_cache["draw"]
    if _shadow_batch_cache["key"] == key:
        return _shadow_batch_cache["z"], _shadow_batch_cache["draw"]
    from . import ground_shadow

    pos, alpha = ground_shadow.scene_shadow(boxes)
    z = ground_shadow.ground_altitude(boxes)
    batch = _build_ground_shadow_draw(shader, pos, alpha) if pos else None
    _shadow_batch_cache.update(key=key, z=z, draw=batch)
    return z, batch


def _ground_shadow_should_draw(space):
    """Rendered always: the cached shadow is cheap enough to keep stable during motion."""
    return _is_rendered_space(space)


def draw_ground_shadow():
    """Draw the soft ground shadow in Rendered panes during POST_VIEW."""
    space = getattr(bpy.context, "space_data", None)
    rv3d = getattr(bpy.context, "region_data", None)
    # Follow-up to #70: hiding the shadow on camera-change frames made it visibly flicker.
    # Its scene/GPU batch is cached now, so keep drawing that same cheap batch while moving.
    if space is None or rv3d is None or not _ground_shadow_should_draw(space):
        return
    from . import ground_shadow

    boxes = ground_shadow_objects()
    if not boxes:
        return
    z = ground_shadow.ground_altitude(boxes)
    inv = rv3d.view_matrix.inverted()
    if rv3d.is_perspective:
        seen = ground_shadow.visible_from(z, eye=tuple(inv.translation))
    else:
        seen = ground_shadow.visible_from(z, view_dir_z=float(inv.col[2][2]))
    if not seen:
        return
    try:
        import gpu

        shader = gpu.shader.from_builtin("SMOOTH_COLOR")
        _z, batch = _ground_shadow_draw(boxes, _ground_shadow_key(), shader)
        if batch is None:
            return
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("LESS_EQUAL")
        gpu.state.depth_mask_set(False)
        shader.bind()
        batch.draw(shader)
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] ground shadow draw failed: {e!r}")
    finally:
        try:
            gpu.state.blend_set("NONE")
            gpu.state.depth_test_set("NONE")
            gpu.state.depth_mask_set(True)
        except Exception:  # noqa: BLE001
            pass


#: Backwards-compatible legacy override used only by old pure/display fixtures. Live UI
#: rendering uses the document store below, one immutable value per stable pane key.
grid_settings = None
_grid_batch_cache = {}
#: sRGB of the minor / major lines -- the values the theme constants above were measured
#: from (GRID_COLOR / GRID_MAJOR), passed to `gpu` as plain byte ratios (views.gpu_rgba).
GRID_MINOR_RGB = (147, 153, 160)
GRID_MAJOR_RGB = (129, 134, 140)
#: Rendered viewport background. The grid multiplies it; see `draw_cplane_grid`.
GRID_BG_RGB = (157, 163, 170)


def grid_multiplier(rgb, bg=GRID_BG_RGB):
    """The MULTIPLY-blend factor that turns the background into `rgb`: rgb / bg, per
    channel, capped at one because multiplying can only darken."""
    return tuple(min(1.0, c / float(b)) for c, b in zip(rgb, bg)) + (1.0,)


def _grid_settings_for_area(area):
    """Adapt the  model to the existing finite grid renderer."""
    from . import grid as _grid

    store = _sync_grid_store()
    if store is None:
        s = grid_settings or _grid.GridSettings()
    else:
        model = store.settings_for(_pane_key(area))
        s = _grid.GridSettings(
            spacing=model.minor_spacing,
            line_count=model.grid_line_count,
            major_every=model.major_interval,
            show_grid=model.show_grid,
            show_axes=model.show_grid_axes,
        )
    return s


def grid_settings_for_area(area):
    """Return the validated store value for reporting and UI inspection."""
    store = _sync_grid_store()
    if store is None:
        return None
    return store.settings_for(_pane_key(area))


def grid_shown_in(space, area=None):
    """True when this pane's finite CPlane grid is visible."""
    area = area or getattr(bpy.context, "area", None)
    s = _grid_settings_for_area(area) if area is not None else None
    if s is None:
        from . import grid as _grid
        s = grid_settings or _grid.GridSettings()
        visible = s.show_grid or s.show_axes
    else:
        visible = s.show_grid or s.show_axes
    return visible and not _is_rendered_space(space)


def _grid_batches(plane, area=None):
    """GPU batches for `plane`'s grid, cached per plane + settings (four panes, four
    entries; the cache is dropped if CPlane edits ever pile it up)."""
    from . import grid as _grid
    from gpu_extras.batch import batch_for_shader

    s = _grid_settings_for_area(area or getattr(bpy.context, "area", None))
    key = (tuple(plane.origin), tuple(plane.xaxis), tuple(plane.yaxis), s.spacing,
           s.line_count, s.major_every, s.show_grid, s.show_axes)
    hit = _grid_batch_cache.get(key)
    if hit is not None:
        return hit
    if len(_grid_batch_cache) > 16:
        _grid_batch_cache.clear()
    shader = _uniform_color_shader()
    segs = _grid.grid_segments(plane, s)
    colours = {"minor": GRID_MINOR_RGB, "major": GRID_MAJOR_RGB,
               "xaxis": _grid.X_AXIS_RGB, "yaxis": _grid.Y_AXIS_RGB}
    batches = []
    for kind in ("minor", "major", "xaxis", "yaxis"):      # axes last, on top
        pts = [p for seg in segs[kind] for p in seg]
        if pts:
            batches.append((batch_for_shader(shader, "LINES", {"pos": pts}),
                            grid_multiplier(colours[kind])))
    _grid_batch_cache[key] = batches
    return batches


def draw_cplane_grid():
    """POST_VIEW: the reference CAD application's FINITE construction-plane grid on this pane's CPlane.

    Replaces Blender's floor / ortho grid, which are infinite and screen-aligned (bug
    report #3). The rules and their sources are in `grid.py`.

    Verified (probe, real window): a PRE_VIEW handler's lines are wiped by the viewport's
    background pass -- 3 stray pixels survived of a 5-px cross -- so the grid draws after
    the scene, depth-tested so objects in front hide it. It is pushed a hair AWAY from
    the camera first, so a curve lying exactly on a grid line (a rectangle snapped to the
    grid) is never overdrawn by it.
    """
    space = getattr(bpy.context, "space_data", None)
    area = getattr(bpy.context, "area", None)
    rv3d = getattr(bpy.context, "region_data", None)
    if space is None or area is None or rv3d is None:
        return
    _note_clip_range(area, space, rv3d)
    if not grid_shown_in(space, area):
        return
    try:
        import gpu

        batches = _grid_batches(get_cplane(area), area)
        shader = _uniform_color_shader()
        inv = rv3d.view_matrix.inverted()
        if rv3d.is_perspective:
            away = (rv3d.view_location - inv.translation).normalized()
        else:
            away = -inv.col[2].xyz.normalized()            # into the screen
        nudge = away * (1e-4 * max(rv3d.view_distance, 1e-3))
        gpu.matrix.push()
        gpu.matrix.translate(tuple(nudge))
        # Verified (live-window checks, and a focused runtime check in plain Blender):
        # Wireframe still writes the solids' FACE depth, which hid the grid inside a
        # cylinder outline, so Wireframe skips the depth test.
        see_through = getattr(space.shading, "type", "") == "WIREFRAME"
        gpu.state.depth_test_set("ALWAYS" if see_through else "LESS_EQUAL")
        gpu.state.depth_mask_set(False)
        # Drawing the grid opaque after the scene paints over every edge and curve it
        # crossed (most visibly in Wireframe), so they looked broken wherever the grid
        # was. MULTIPLY by grid/background: on the background that IS the grid colour,
        # on a black edge it is still black, on a coloured one a ~6 % darkening.
        gpu.state.blend_set("MULTIPLY")
        try:
            shader.bind()
            for batch, rgba in batches:
                shader.uniform_float("color", rgba)
                batch.draw(shader)
        finally:
            gpu.matrix.pop()
            gpu.state.blend_set("NONE")
            gpu.state.depth_test_set("NONE")
            gpu.state.depth_mask_set(True)
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] grid draw failed: {e!r}")


# ---------------------------------------------------------------------------
# Blender clips at a fixed clip_start / clip_end (0.01 .. 1000 by default), so zooming
# out, or modelling anything big, cut objects away. the reference CAD application shows no far clip in normal
# modelling. application-defined (the reference CAD application's near/far rule is not documented): each pane's range follows
# its own zoom and the scene's size, re-set from a timer (never inside the draw) and only
# when it is off by more than a factor of two, so it cannot churn redraws.
# ---------------------------------------------------------------------------

_clip_pending = {}
_clip_armed = False


clip_range_for = views.clip_range_for


def _scene_extent():
    """``(center, radius)`` of everything visible plus the grid."""
    from . import grid as _grid

    store = _sync_grid_store()
    if store is None:
        settings = (grid_settings or _grid.GridSettings(),)
    else:
        settings = tuple(
            _grid.GridSettings(
                spacing=value.minor_spacing,
                line_count=value.grid_line_count,
                major_every=value.major_interval,
                show_grid=value.show_grid,
                show_axes=value.show_grid_axes,
            )
            for value in store.snapshot().values())
    extent = max(s.extent() for s in settings)
    lo, hi = [-extent] * 3, [extent] * 3
    from mathutils import Vector

    # A curve's world box is already in its curve-pass record (cached, re-read only when
    # it changed). Repeating eight `matrix_world @ corner` products per object
    # in Python made this 53-57 ms of the first frame after every document change.
    import numpy as np

    coll = bpy.data.collections.get(bridge.CURVE_COLLECTION)
    app_drawn = set(coll.objects.keys()) if coll is not None else set()
    rec_lo, rec_hi = [], []
    # The curve pass's static boxes cover every folded, shown curve in one array;
    # only the curves it does not (changed since the fold, hidden) are read one by
    # one. Reading every record again added avoidable work to the
    # first frame after every document change.
    st = None
    if app_drawn:
        try:
            st = _curve_sync()
        except Exception:  # noqa: BLE001
            st = None
    if st is not None and len(st["pick_names"]):
        dirty = st["dirty"]
        keep = np.fromiter((n not in dirty for n in st["pick_names"]), dtype=bool,
                           count=len(st["pick_names"]))
        if keep.any():
            rec_lo.append(st["pick_lo"][keep].min(axis=0))
            rec_hi.append(st["pick_hi"][keep].max(axis=0))
        one_by_one = (app_drawn - set(st["ranges"])) | (set(dirty) & app_drawn)
    else:
        one_by_one = app_drawn
    # one map, built in C (`keys` / `values`), NOT `bpy.data.objects.get` per name:
    # each `get` scans the list -- Verified ~0.1 ms, 40-70 ms for this pass
    objs = dict(zip(bpy.data.objects.keys(), bpy.data.objects.values()))
    boxed = []                             # read from their bound box (below)
    for name in one_by_one:
        rec = _curve_record(objs.get(name))
        if rec is not None and rec["n"]:
            rec_lo.append(rec["lo"])
            rec_hi.append(rec["hi"])
        else:
            boxed.append(name)             # no edges (a point object): its box
    # everything else that has a box (surfaces, solids), found by NAME first
    others = (objs.keys() - app_drawn) | set(boxed)
    for name in others:
        ob = objs.get(name)
        if ob is None or ob.type != "MESH":
            continue
        try:
            corners = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
        except Exception:  # noqa: BLE001
            continue
        for c in corners:
            for i in range(3):
                lo[i], hi[i] = min(lo[i], c[i]), max(hi[i], c[i])
    if rec_lo:
        rl = np.min(np.array(rec_lo, dtype=np.float64), axis=0)
        rh = np.max(np.array(rec_hi, dtype=np.float64), axis=0)
        for i in range(3):
            lo[i], hi[i] = min(lo[i], float(rl[i])), max(hi[i], float(rh[i]))
    center = tuple((lo[i] + hi[i]) / 2 for i in range(3))
    radius = 0.5 * sum((hi[i] - lo[i]) ** 2 for i in range(3)) ** 0.5
    return center, radius


def _note_clip_range(area, space, rv3d):
    """From the draw pass: queue a clip change for this pane if it is far off."""
    global _clip_armed
    try:
        center, radius = _scene_extent_cached()
        want = clip_range_for(rv3d.view_distance, rv3d.view_location, radius, center)
        have = (space.clip_start, space.clip_end)
        if all(0.5 <= w / max(h, 1e-12) <= 2.0 for w, h in zip(want, have)):
            return
        _clip_pending[_pane_key(area)] = want
        if not _clip_armed:
            _clip_armed = True
            bpy.app.timers.register(_apply_clip_ranges, first_interval=0.0)
    except Exception:  # noqa: BLE001
        pass


_extent_cache = {"key": None, "value": ((0.0, 0.0, 0.0), 0.0)}


def _scene_extent_cached():
    try:
        serial = app_state.ctx.undo.top_serial
    except Exception:  # noqa: BLE001
        serial = None
    key = (serial, len(bpy.data.objects),
           tuple((key, value) for key, value in sorted(
               ((_pane_key(a), _grid_settings_for_area(a)) for a in _view3d_areas()),
               key=lambda item: repr(item[0]))))
    if _extent_cache["key"] != key:
        _extent_cache["key"], _extent_cache["value"] = key, _scene_extent()
    return _extent_cache["value"]


def _apply_clip_ranges():
    global _clip_armed
    _clip_armed = False
    pending = dict(_clip_pending)
    _clip_pending.clear()
    for area in _view3d_areas():
        want = pending.get(_pane_key(area))
        sp = _view3d_space(area)
        if want is None or sp is None:
            continue
        try:
            sp.clip_start, sp.clip_end = want
            area.tag_redraw()
        except Exception as e:  # noqa: BLE001
            print(f"[narwhal3d] could not set the clip range: {e!r}")
    return None


# ---------------------------------------------------------------------------
# THE CURVE PASS: every curve on screen is drawn HERE, not by Blender.
#
# Large-document profiling showed Blender's draw manager spending most of an orbit
# frame syncing thousands of separate curve
# objects. Face-less display objects now live in `bridge.CURVE_COLLECTION`, hidden from
# Blender's drawing, and this pass draws all of them in a handful of draw calls -- from
# the Blender DISPLAY MESHES (world = mesh @ matrix_world), not the document, so
# everything that already updates those meshes keeps showing: zoom re-tessellation,
# control-point drags (`rebuild_object_mesh`), the gumball's live `matrix_world`,
# `rebuild_display`.
#
# * STATIC arrays/batches, one per (colour, width): every shown curve as it was at the
#  last FOLD. A fold happens when the document key, the layer-colour items or the
#  hidden set move -- never on an idle redraw or a mouse move.
# * DIRTY objects -- reported by `depsgraph_update_post` (geometry or transform) since
#  the fold -- are cut out of the static batches with `draw_range` and drawn from
#  small per-object batches until the next fold. A drag rebuilds one small batch per
#  frame, never a million vertices.
# * SELECTED curves are drawn again on top in SELECT_COLOR (the view layer's selection
#  is read every frame -- it is cheap; its batch is rebuilt only when it changes).
#
# Colours are exactly the layer overlay's: a curve on a non-black layer in its layer
# colour (`_curve_colour_map`, which also keeps the layer materials' viewport colour
# in step), every other one in the theme wire colour Blender used to draw it in.
# ---------------------------------------------------------------------------

#: Use one-pixel lines for normal and selected curves so layer color changes color,
#: not visual weight.
CURVE_WIRE_WIDTH = 1.0
CURVE_LAYER_WIDTH = 1.0
CURVE_SELECTED_WIDTH = 1.0
#: Curves and the Gumball use the application's anti-aliased line shader (`_aa_line_shader`; NOT Blender's
#: polyline one --, see there) instead of the flat `UNIFORM_COLOR`, whose lines
#: are aliased stair-steps. False falls back to the flat shader (also used if ours
#: cannot be built on this GPU backend).
SMOOTH_LINES = True

#: More than this many objects changed since the last fold, and no change for
#: CURVE_SETTLE_S, and the next frame folds them back into the static batches (a zoom
#: re-tessellation rewrites many meshes without moving the document key). application-defined.
CURVE_DIRTY_FOLD = 64
CURVE_SETTLE_S = 0.3
#: application-defined -- the longest a stream of changes goes without a fold (see `_curve_sync`).
CURVE_FOLD_EVERY_S = 1.0

#: How close (pixels) a click must come to a curve's DRAWN polyline to pick it. application-defined:
#: no source we hold records the reference CAD application's pick-box radius; a few pixels, and tighter than
#: SUBOBJECT_PICK_PX because a curve within it wins over a surface (`pick_curve_object`).
CURVE_PICK_PX = 6.0

#: What `depsgraph_update_post` reported. `serial` moves on every update; `vis` on any
#: update that can change visibility or the set of objects (anything but an Object /
#: Mesh / Material / Scene -- a `hide_viewport`, `hide_select`, link or remove reports a
#: Collection, Verified in a focused runtime check and
#: a focused runtime check); `scene` on an update that names the Scene -- a
#: selection change, the active object, and also a `hide_set`, which reports ONLY the
#: Scene. `changed` maps each object whose geometry or transform changed since the last
#: fold to the serial of that update.
#:
#: `scene` is kept apart from `vis` because a click-select reports the Scene too:
#: Verified, counting it as `vis` re-ran `apply_layer_state` (a 150 ms walk of every
#: object) on every selection change, making large-document clicks expensive.
_curve_dg = {"serial": 0, "vis": 0, "scene": 0, "changed": {}, "t": 0.0}

_curve_pass = {
    "fold_key": None,     # (items version, hidden) the static arrays are for
    "fold_doc": None,     # the document key `_curve_reconcile` last checked against
    "present": frozenset(),  # every curve-collection name the last fold saw
    "items": None,        # the `_curve_colour_map` dict (= `cmap`)
    "items_ver": 0,
    "cmap": {},           # name -> layer rgba (curves on non-black, visible layers)
    "recs": {},           # name -> per-object record: world LINES coords + box
    "objects": {},        # name -> resolved Blender curve object from the last fold
    "groups": [],         # [(rgba, width, coords (2E, 3) float32 or None once uploaded)]
    "totals": [],         # vertex count of each group
    "ranges": {},         # name -> (group index, first vertex, vertex count)
    "pick_names": [], "pick_lo": None, "pick_hi": None,
    "hidden": frozenset(), "hidden_vis": -1,
    "dirty": frozenset(), "dirty_serial": -1,
    "plan": {},           # group index -> [(first, count)] still drawn from static
    "gpu": None,          # (fold number, [(rgba, width, batch)])
    "objb": {},           # name -> (record, batch) for dirty objects
    "sel_key": None, "sel_batch": None,
    "folds": 0,
    "builds": 0,          # static GPU batch builds -- the idle-redraw gates count them
    "errors": 0,
}

#: The overlay's batch record, which the curve pass replaced. The BIG-FILE REDRAW
#: gate in the live-window UI checks counts static-batch builds through this name, and the
#: same promise holds for the new pass: reused on an idle frame, rebuilt on a change.
_layer_curve_batches = _curve_pass


@bpy.app.handlers.persistent
def _curve_depsgraph_update(_scene, depsgraph):
    """`depsgraph_update_post`: note which objects' geometry/transform changed."""
    import time

    dg = _curve_dg
    dg["serial"] += 1
    s = dg["serial"]
    changed = dg["changed"]
    vis = scene = False
    try:
        for u in depsgraph.updates:
            idb = u.id
            if isinstance(idb, bpy.types.Object):
                if u.is_updated_geometry or u.is_updated_transform:
                    changed[idb.name] = s
            elif isinstance(idb, bpy.types.Scene):
                scene = True
            elif not isinstance(idb, (bpy.types.Mesh, bpy.types.Material)):
                vis = True
    except Exception:  # noqa: BLE001
        vis = scene = True
    if vis:
        dg["vis"] = s
    if scene:
        dg["scene"] = s
    dg["t"] = time.monotonic()


def _curve_display_changed(names, removed=False):
    """`bridge.note_display_changed` listener: the same record a depsgraph geometry /
    transform update makes. With the patched Blender, app-drawn objects are outside the
    dependency graph and raise no depsgraph update at all, so this is THE signal."""
    import time

    dg = _curve_dg
    dg["serial"] += 1
    s = dg["serial"]
    for n in names:
        dg["changed"][n] = s
    if removed:
        dg["vis"] = s
    dg["t"] = time.monotonic()


def _attach_curve_depsgraph_handler():
    if _curve_display_changed not in bridge.DISPLAY_CHANGE_LISTENERS:
        bridge.DISPLAY_CHANGE_LISTENERS.append(_curve_display_changed)
    handlers = bpy.app.handlers.depsgraph_update_post
    if not any(getattr(h, "__name__", "") == _curve_depsgraph_update.__name__
               for h in handlers):
        handlers.append(_curve_depsgraph_update)


def _detach_curve_depsgraph_handler():
    while _curve_display_changed in bridge.DISPLAY_CHANGE_LISTENERS:
        bridge.DISPLAY_CHANGE_LISTENERS.remove(_curve_display_changed)
    handlers = bpy.app.handlers.depsgraph_update_post
    for h in list(handlers):
        if getattr(h, "__name__", "") == _curve_depsgraph_update.__name__:
            handlers.remove(h)


_IDENTITY4 = None


def _curve_record(ob):
    """The curve pass's record for display object `ob`, re-read only when stale.

    ``{"key", "serial", "lines" (2E, 3) float32 world, "lo", "hi", "n"}``. Stale when the
    object or its mesh datablock was replaced, its vertex/edge count moved, or the
    depsgraph reported a geometry/transform update after it was read.
    """
    global _IDENTITY4
    import numpy as np

    if ob is None:
        return None
    me = ob.data
    if me is None or not hasattr(me, "vertices"):
        return None
    try:
        key = (ob.as_pointer(), me.as_pointer(), len(me.vertices), len(me.edges))
    except Exception:  # noqa: BLE001
        return None
    recs = _curve_pass["recs"]
    name = ob.name
    rec = recs.get(name)
    if (rec is not None and rec["key"] == key
            and rec["serial"] >= _curve_dg["changed"].get(name, 0)):
        return rec
    nv, ne = key[2], key[3]
    if ne:
        co = np.empty(nv * 3, dtype=np.float32)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        ed = np.empty(ne * 2, dtype=np.int32)
        me.edges.foreach_get("vertices", ed)
        if _IDENTITY4 is None:
            _IDENTITY4 = np.identity(4)
        m = np.array(ob.matrix_world, dtype=np.float64)
        if not np.array_equal(m, _IDENTITY4):
            co = (co.astype(np.float64) @ m[:3, :3].T + m[:3, 3]).astype(np.float32)
        lines = np.ascontiguousarray(co[ed])
        lo, hi = lines.min(axis=0), lines.max(axis=0)
    else:
        lines = np.zeros((0, 3), dtype=np.float32)
        lo = hi = None
    rec = {"key": key, "serial": _curve_dg["serial"], "lines": lines,
           "lo": lo, "hi": hi, "n": len(lines)}
    recs[name] = rec
    return rec


def _curve_style(name, cmap):
    """``(rgba, width)`` a curve is drawn in when not selected."""
    c = cmap.get(name)
    if c is not None:
        return c, CURVE_LAYER_WIDTH
    return WIRE_COLOR + (1.0,), CURVE_WIRE_WIDTH


def _curve_fold(coll, st, key):
    """Rebuild the static per-colour arrays from every shown curve (CPU side)."""
    import numpy as np

    dg = _curve_dg
    recs = st["recs"]
    for n, s in dg["changed"].items():
        r = recs.get(n)
        if r is not None and r["serial"] < s:
            del recs[n]
    dg["changed"] = {}
    hidden = st["hidden"]
    cmap = st["cmap"]
    present = set()
    object_refs = {}
    groups = {}
    for ob in coll.objects:
        name = ob.name
        present.add(name)
        object_refs[name] = ob
        if name in hidden:
            continue
        rec = _curve_record(ob)
        if rec is None or not rec["n"]:
            continue
        groups.setdefault(_curve_style(name, cmap), []).append((name, rec))
    for n in [n for n in recs if n not in present]:
        del recs[n]
    out, totals, ranges, names, los, his = [], [], {}, [], [], []
    for gi, ((rgba, width), members) in enumerate(groups.items()):
        off = 0
        arrs = []
        for name, rec in members:
            k = rec["n"]
            ranges[name] = (gi, off, k)
            off += k
            arrs.append(rec["lines"])
            names.append(name)
            los.append(rec["lo"])
            his.append(rec["hi"])
        out.append((rgba, width, np.concatenate(arrs)))
        totals.append(off)
    st.update(fold_key=key, groups=out, totals=totals, ranges=ranges, pick_names=names,
              present=frozenset(present), objects=object_refs,
              pick_lo=np.array(los, dtype=np.float64).reshape(-1, 3),
              pick_hi=np.array(his, dtype=np.float64).reshape(-1, 3),
              dirty=frozenset(), dirty_serial=-1, plan={}, objb={}, sel_key=None,
              sel_batch=None)
    st["folds"] += 1


def _curve_reconcile(coll, st):
    """Mark as changed every curve the static batches do not account for: present but
    neither folded nor already changed (a rename, an object linked without
    `note_display_changed`), or folded but gone. Names only -- one C-side `keys` and
    two set differences."""
    dg = _curve_dg
    present = set(coll.objects.keys())
    folded = st.get("present") or frozenset()     # every name the last fold saw
    changed = dg["changed"]
    odd = [n for n in present if n not in folded and n not in changed]
    odd += [n for n in folded if n not in present and n not in changed]
    if odd:
        dg["serial"] += 1
        for n in odd:
            changed[n] = dg["serial"]


def _curve_plan(st):
    """Which objects are dirty, and which ranges of each static batch still draw."""
    dirty = set()
    changed = _curve_dg["changed"]
    # one map, not `bpy.data.objects.get` per name (~0.1 ms each -- Verified 60-120 ms a
    # frame with a few hundred changed curves during a zoom)
    objs = ({ob.name: ob for ob in bpy.data.objects} if len(changed) > 32 else None)
    for n in changed:
        ob = objs.get(n) if objs is not None else bpy.data.objects.get(n)
        if ob is None:
            if n in st["ranges"]:
                dirty.add(n)               # GONE: its static range must not draw
        elif bridge.is_app_drawn(ob):
            dirty.add(n)
    excl = {}
    for n in dirty:
        r = st["ranges"].get(n)
        if r is not None:
            excl.setdefault(r[0], []).append((r[1], r[2]))
    plan = {}
    for gi, spans in excl.items():
        spans.sort()
        segs, pos = [], 0
        for a, c in spans:
            if a > pos:
                segs.append((pos, a - pos))
            pos = max(pos, a + c)
        total = st["totals"][gi]
        if total > pos:
            segs.append((pos, total - pos))
        plan[gi] = segs
    st.update(dirty=frozenset(dirty), plan=plan, dirty_serial=_curve_dg["serial"])


def _curve_sync():
    """Bring the curve pass's CPU state up to date; the state, or None (no curves)."""
    import time

    coll = bpy.data.collections.get(bridge.CURVE_COLLECTION)
    if coll is None:
        return None
    # The document decides what is shown (Hide/Show, Lock, layer On/Off). Applied HERE,
    # before any curve is drawn: the right band applied it too, but a band draws after
    # the panes, so the first frame after `Hide` still drew the hidden curve in the
    # selection colour (Verified, a focused runtime check). Cheap: it early-outs
    # unless the document, a layer flag or the scene changed.
    try:
        apply_layer_state()
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] layer state not applied: {e!r}")
    st = _curve_pass
    dg = _curve_dg
    items = _curve_colour_map()
    if st["items"] is not items:
        # held, and versioned: an id alone could be reused by the NEXT map
        st["items"] = items
        st["items_ver"] += 1
        st["cmap"] = items
    # the hidden set also reads `hide_get`, whose `hide_set` reports only the Scene
    vis_key = (dg["vis"], dg["scene"])
    if st["hidden_vis"] != vis_key:
        hid = set()
        for ob in coll.objects:
            try:
                if ob.hide_viewport or ob.hide_get():
                    hid.add(ob.name)
            except Exception:  # noqa: BLE001 (not in this view layer: not shown)
                hid.add(ob.name)
        if hid != st["hidden"]:
            st["hidden"] = frozenset(hid)
        st["hidden_vis"] = vis_key
    # The document key is NOT part of the fold key: a document change reaches the
    # pass as named display changes (`bridge.note_display_changed`: mesh written,
    # object added or removed), drawn per object until the next fold, and as colour /
    # hidden changes (both in the key). Refolding every curve
    # curves on every document change was 23-77 ms of the first frame after a Delete,
    # its undo, or a Gumball release. What no writer announces -- a rename, an object
    # linked by other code -- is found by `_curve_reconcile` once per document state.
    key = (st["items_ver"], st["hidden"])
    doc = _doc_key()
    doc_moved = doc is None or doc != st.get("fold_doc")
    if doc_moved:
        _curve_reconcile(coll, st)
        st["fold_doc"] = doc
    now = time.monotonic()
    # Fold the dirty objects back into the static batches once the changes settle --
    # or, while a long stream of them runs (the sliced zoom re-tessellation rewrites
    # thousands of meshes), at most every CURVE_FOLD_EVERY_S: Verified, letting them
    # pile up to 3,000 per-object batches made every frame of a zoom 170-400 ms.
    many = len(dg["changed"]) > CURVE_DIRTY_FOLD
    # ...and never while the user navigates: a view change is no depsgraph update, so
    # "settled" came true 0.3 s into a pan started after a zoom pass's last slice, and
    # the fold (~30 ms + re-uploading ~1M vertices) landed in the pan (Verified,
    # a focused runtime check). The document key still folds at once (below).
    settled = many and not navigating() and (
        now - dg["t"] > CURVE_SETTLE_S
        or now - st.get("fold_t", 0.0) > CURVE_FOLD_EVERY_S)
    # (a document change of MANY objects -- an Open, a rebuild -- folds at once, as
    # drawing thousands of per-object batches until it settles would be far slower)
    if key != st["fold_key"] or settled or (doc_moved and many):
        _curve_fold(coll, st, key)
        st["fold_t"] = now
    if st["dirty_serial"] != dg["serial"]:
        _curve_plan(st)
    return st


def curve_pass_stats():
    """Counters of the curve pass (for tests and probes)."""
    st = _curve_pass
    return {"folds": st["folds"], "builds": st["builds"], "dirty": len(st["dirty"]),
            "static": len(st["ranges"]), "groups": len(st["totals"]),
            "hidden": len(st["hidden"]), "errors": st["errors"]}


def curve_pass_names():
    """Names the curve pass draws right now (static + dirty, hidden ones excluded)."""
    st = _curve_sync()
    if st is None:
        return set()
    coll = bpy.data.collections.get(bridge.CURVE_COLLECTION)
    present = set(coll.objects.keys()) if coll is not None else set()
    # (a removed curve stays `dirty` -- its static range masked -- until the next fold)
    return (set(st["ranges"]) - st["dirty"]) | ((set(st["dirty"]) - st["hidden"])
                                                & present)


def draw_curves():
    """POST_VIEW: every shown curve -- and every other face-less display object."""
    try:
        import gpu
        import numpy as np
    except Exception:  # noqa: BLE001
        return
    st = _curve_pass
    # A deferred drag owns a stable selection snapshot. Curves in that snapshot are
    # drawn from their preserved world records, not from Blender matrices or the
    # current selection (which may change while the mouse is down).
    defer = _gumball_drag.get("defer") if _gumball_drag is not None else None
    preview_names = defer.get("curve_names", frozenset()) if defer else frozenset()
    try:
        if _curve_sync() is None:
            return
    except Exception as e:  # noqa: BLE001
        st["errors"] += 1
        if st["errors"] < 5:
            print(f"[narwhal3d] curve pass sync failed: {e!r}")
        return
    shader, aa = _line_shader()
    if shader is None:
        return
    prev_blend = None
    # Shaded and Rendered curves test against scene depth; those behind solids stay hidden.
    # Wireframe has no faces to hide behind, so it stays see-through, as in the reference CAD application.
    space = getattr(bpy.context, "space_data", None)
    occlude = getattr(getattr(space, "shading", None), "type", "") == "SOLID"
    if occlude:
        gpu.state.depth_test_set("LESS_EQUAL")
        gpu.state.depth_mask_set(False)
    try:
        # (a batch is built for ONE shader kind; `aa` changes only if SMOOTH_LINES does)
        if st["gpu"] is None or st["gpu"][0] != (st["folds"], aa):
            if any(c is None for _r, _w, c in st["groups"]):
                # uploaded (and freed) for the other shader kind: fold afresh
                st["fold_key"] = None
                if _curve_sync() is None:
                    return
            built = []
            for i, (rgba, width, coords) in enumerate(st["groups"]):
                built.append((rgba, width, _line_batch(shader, aa, coords)))
                st["groups"][i] = (rgba, width, None)    # uploaded: free the copy
            st["gpu"] = ((st["folds"], aa), built)
            st["builds"] += 1
        _line_begin(shader, aa)
        if aa:
            # anti-aliased lines blend their edges 
            prev_blend = gpu.state.blend_get()
            gpu.state.blend_set("ALPHA")
        plan = st["plan"]
        # Mask the snapshot curves out of the old static batches. Their original
        # positions must not remain visible behind the transformed preview.
        preview_spans = {}
        for n in preview_names:
            r = st["ranges"].get(n)
            if r is not None:
                preview_spans.setdefault(r[0], []).append((r[1], r[2]))
        for spans in preview_spans.values():
            spans.sort()
        for gi, (rgba, width, batch) in enumerate(st["gpu"][1]):
            spans = preview_spans.get(gi)
            draw_plan = plan.get(gi)
            if spans:
                base = draw_plan if draw_plan is not None else [(0, st["totals"][gi])]
                kept = []
                for first, count in base:
                    end = first + count
                    pos = first
                    for cut, size in spans:
                        if cut > pos:
                            kept.append((pos, min(cut, end) - pos))
                        pos = max(pos, cut + size)
                        if pos >= end:
                            break
                    if pos < end:
                        kept.append((pos, end - pos))
                draw_plan = kept
            if draw_plan == []:
                continue
            _line_width(shader, aa, width)
            shader.uniform_float("color", rgba)
            if draw_plan is None:
                batch.draw(shader)
                continue
            for first, count in draw_plan:
                if count > 0:
                    f, c = _line_range(aa, first, count)
                    batch.draw_range(shader, elem_start=f, elem_count=c)
        hidden = st["hidden"]
        dirty = st["dirty"]
        selected = {o.name for o in bridge.view_layer_selected()}
        objb = st["objb"]
        dirty_selected = []
        for n in dirty:
            if n in preview_names or n in hidden:
                continue
            rec = _curve_record(bpy.data.objects.get(n))
            if rec is None or not rec["n"]:
                continue
            cached = objb.get(n)
            if cached is None or cached[0] is not rec or cached[2] != aa:
                cached = objb[n] = (rec, _line_batch(shader, aa, rec["lines"]), aa)
            if n in selected:
                dirty_selected.append(cached[1])
                continue
            rgba, width = _curve_style(n, st["cmap"])
            _line_width(shader, aa, width)
            shader.uniform_float("color", rgba)
            cached[1].draw(shader)
        ranges = st["ranges"]
        # a HIDDEN curve is not drawn selected either (Hide leaves it in the selection)
        static_sel = tuple(sorted(n for n in selected
                                  if n in ranges and n not in dirty
                                  and n not in preview_names
                                  and n not in st["hidden"])) if selected else ()
        sel_key = (st["folds"], static_sel, aa)
        if st["sel_key"] != sel_key:
            recs = st["recs"]
            arrs = [recs[n]["lines"] for n in static_sel if n in recs]
            st["sel_batch"] = (_line_batch(shader, aa, np.concatenate(arrs))
                               if arrs else None)
            st["sel_key"] = sel_key
        if st["sel_batch"] is not None or dirty_selected:
            _line_width(shader, aa, CURVE_SELECTED_WIDTH)
            shader.uniform_float("color", SELECT_COLOR + (1.0,))
            if st["sel_batch"] is not None:
                st["sel_batch"].draw(shader)
            for b in dirty_selected:
                b.draw(shader)
        # One selected-color batch for the entire curve snapshot. Matrix state is
        # restored even when a backend rejects the multiply/draw operation.
        if defer and defer.get("curve_coords") is not None and preview_names:
            key = (id(shader), aa)
            if defer.get("curve_batch_key") != key:
                defer["curve_batch"] = _line_batch(shader, aa, defer["curve_coords"])
                defer["curve_batch_key"] = key
            if defer.get("curve_batch") is not None:
                pushed = False
                try:
                    gpu.matrix.push()
                    pushed = True
                    gpu.matrix.multiply_matrix(defer["acc"])
                    _line_width(shader, aa, CURVE_SELECTED_WIDTH)
                    shader.uniform_float("color", SELECT_COLOR + (1.0,))
                    defer["curve_batch"].draw(shader)
                finally:
                    if pushed:
                        gpu.matrix.pop()
    except Exception as e:  # noqa: BLE001
        st["errors"] += 1
        if st["errors"] < 5:
            print(f"[narwhal3d] curve draw failed: {e!r}")
    finally:
        gpu.state.line_width_set(1.0)
        if prev_blend is not None:
            gpu.state.blend_set(prev_blend)
        if occlude:
            gpu.state.depth_test_set("NONE")
            gpu.state.depth_mask_set(True)


def pick_curve_object(area, space, region, rx, ry, tolerance=CURVE_PICK_PX):
    """The name of the curve whose DRAWN polyline passes nearest region pixel (rx, ry)
    within `tolerance` pixels, or None.

    Blender's `view3d.select` cannot hit a curve any more (Blender does not draw them),
    so curves get their own picker. It tests the very coordinates `draw_curves`
    draws, culling first by each curve's world box projected to the screen
    (`osnap.boxes_reach`, vectorised), then measuring segments only for survivors.
    Hidden and locked curves are not pickable.

    application-defined, curve vs surface: a curve within tolerance WINS over a surface or solid at the
    same pixel -- curves are drawn on top of the faces they lie on, and that is the
    thing under the cursor. (A curve hidden behind a solid in Shaded is still pickable;
    the reference CAD application also lets you reach objects behind others.)
    """
    rv3d = getattr(space, "region_3d", None) if space is not None else None
    if rv3d is None or region is None:
        return None
    width, height = getattr(region, "width", 0), getattr(region, "height", 0)
    if not width or not height:
        return None
    try:
        st = _curve_sync()
    except Exception:  # noqa: BLE001
        return None
    if st is None:
        return None
    import numpy as np

    m = np.array(rv3d.perspective_matrix, dtype=np.float64)
    mouse = (float(rx), float(ry))
    locked = _unpickable_names_cached()
    hidden, dirty = st["hidden"], st["dirty"]
    cands = []
    names = st["pick_names"]
    if names:
        keep = osnap.boxes_reach(st["pick_lo"], st["pick_hi"], m, width, height,
                                 mouse, tolerance)
        for i in np.flatnonzero(keep):
            n = names[i]
            if n not in dirty and n not in locked:
                cands.append((n, st["recs"].get(n)))
    for n in dirty:
        if n not in hidden and n not in locked:
            cands.append((n, _curve_record(bpy.data.objects.get(n))))
    measurable = [(n, rec) for n, rec in cands if rec is not None and rec["n"]]
    native_pick = native_selection.nearest_projected_curve(
        [rec["lines"] for _name, rec in measurable],
        m, width, height, mouse, tolerance)
    if native_pick is not None:
        index, _distance = native_pick
        return None if index == native_selection.PICK_NO_HIT else measurable[index][0]
    best, best_d = None, float(tolerance)
    for n, rec in measurable:
        d = osnap.segments_screen_distance(rec["lines"], m, width, height, mouse)
        if d <= best_d:
            best, best_d = n, d
    return best


_locked_cache = {"key": None, "names": frozenset()}


def _locked_names_cached():
    """`locked_object_names`, re-read only when the document key moved.

    A full document scan dominates curve picking on large files.
    `Lock`/`Unlock` change `Mode` inside a recorded command, so the key moves with them.
    """
    key = _doc_key()
    if key is None or _locked_cache["key"] != key:
        _locked_cache["names"] = frozenset(locked_object_names())
        _locked_cache["key"] = key
    return _locked_cache["names"]


_unpickable_cache = {"key": None, "names": frozenset()}


def _unpickable_names_cached():
    """Names the curve picker must skip: a locked object OR one on a locked layer.

    From the DOCUMENT, not Blender's `hide_select`: that flag is re-synced only on some
    click paths (`sync_locked_selectability`), so after an Unlock it can still say
    "locked" (Verified: the unlocked curve stayed unclickable). Cached on the document
    key plus the layers' Locked flags (a panel lock toggle need not move the key).
    """
    model = getattr(app_state, "model", None)
    file3dm = getattr(model, "file", None)
    if file3dm is None:
        return frozenset()
    try:
        layer_locks = tuple(bool(l.Locked) for l in file3dm.Layers)
    except Exception:  # noqa: BLE001
        layer_locks = ()
    key = (_doc_key(), layer_locks)
    if _unpickable_cache["key"] != key:
        names = set(_locked_names_cached())
        if any(layer_locks):
            for name, _mode, li, _geom, _attrs in _doc_rows():
                if li is None:
                    continue
                if 0 <= li < len(layer_locks) and layer_locks[li]:
                    names.add(name)
        _unpickable_cache["names"] = frozenset(names)
        _unpickable_cache["key"] = key
    return _unpickable_cache["names"]


def _sync_layer_to_document(layer):
    """Push a tree node's On/Lock onto the matching rhino3dm layer.

    The live `LayerTree`  is the editable view; the `File3dm` layers are what
    gets SAVED and what `apply_layer_state` reads. Matched by name, which 
    established is exact for the flat layer list.
    """
    model = getattr(app_state, "model", None)
    if model is None:
        return False
    for info in model.layers():
        if info["name"] == layer.name:
            model.set_layer_visible(info["index"], layer.visible)
            model.set_layer_locked(info["index"], layer.locked)
            return True
    return False


def layer_row_at(mouse_x, mouse_y):
    """``(row_index, column_index)`` under the window cursor, or None.

    Tested against the boxes the last draw recorded, which is the rule every other
    piece of chrome in this file follows.
    """
    i = rightpanel.hit_row(mouse_x, mouse_y, _layer_rows)
    if i is None:
        return None
    col = rightpanel.hit_column(mouse_x, _layer_rows[i])
    if col is None:
        return None
    return (i, col)


def _document_layers():
    """The document's layers, in order, or [].  wired this tree."""
    tree = getattr(app_state, "layers", None)
    if tree is None:
        return []
    try:
        return list(tree.iter_all())
    except Exception:  # noqa: BLE001
        return []


def _draw_layers_panel(shader, region, box):
    """Draw the Layers list in the right band's body.

    Rows come from the document's live `LayerTree`; the current layer is marked with a
    bold tick, and On/Lock/Color show per-layer state. Clicking is handled by
    `layer_panel_click`.

    The search box and two 16 px toolbar rows are deliberately omitted: drawing a field
    that cannot accept input or eleven inert buttons would misrepresent unsupported
    controls as implemented.
    """
    global _layer_rows, _layer_row_objs

    import gpu

    layers = _document_layers()
    # a "New Layer" button along the bottom of the panel (Verified, the reference CAD application documentation:
    # "New Layer: Click to create a new layer"); the list gets the rest of the body
    bx0, by0, bx1, by1 = box["body"]
    bh = 22
    global _layer_new_btn
    _layer_new_btn = (region.x + bx0 + 4, region.y + by0 + 4,
                      region.x + bx1 - 4, region.y + by0 + 4 + bh)
    lay = rightpanel.list_layout((bx0, by0 + bh + 8, bx1, by1), len(layers))
    rows = lay["rows"]
    _layer_rows = [(region.x + x0, region.y + y0, region.x + x1, region.y + y1)
                   for x0, y0, x1, y1 in rows]
    _layer_row_objs = layers[:len(rows)]

    current = getattr(getattr(app_state, "layers", None), "current", None)

    prev = gpu.state.blend_get()
    gpu.state.blend_set("ALPHA")
    try:
        shader.bind()
        lx0, ly0, lx1, ly1 = lay["list"]
        _draw_rect(shader, lx0, ly0, lx1, ly1,
                   views.gpu_rgba(views.LAYER_LIST_BG))
        grid = views.gpu_rgba(views.LAYER_GRID)
        # Batched by colour (`_draw_rects`): Verified (a focused runtime check) this
        # panel issued ~1,330 one-rectangle batches -- mostly the dotted separators --
        # 13-18 ms of EVERY frame that repaints the band, i.e. every Gumball move.
        # Layering is kept: grid lines, then the swatch fills, then their outlines.
        lines = []
        # header's bottom rule
        hx0, hy0, hx1, _hy1 = lay["header"]
        lines.append((hx0, hy0, hx1, hy0 + 1))
        # column dividers, spanning header + rows
        for dx, dy0, dy1 in lay["dividers"]:
            lines.append((dx, dy0, dx + 1, dy1))
        fills, outlines = {}, []
        # per-row furniture
        for row, layer in zip(rows, _layer_row_objs):
            rx0, ry0, rx1, ry1 = row
            # Draw the dotted separator under each row
            # on the control-point display and the reference CAD application uses it here too (Verified: a
            # 1 px line at (222,226,233) covering ~29 % of the width, i.e. dotted)
            for dx in range(int(rx0), int(rx1), 3):
                lines.append((dx, ry0, dx + 1, ry0 + 1))
            # COLOR swatch: a filled square, which is what the screenshot shows
            cx0, cy0, cx1, cy1 = rightpanel.cell(row, rightpanel.COLUMNS.index("Color"))
            pad = 4
            if cx1 - cx0 > pad * 2:
                r, g, b = [c / 255.0 for c in (layer.color or (0, 0, 0))[:3]]
                fills.setdefault((r, g, b, 1.0), []).append(
                    (cx0 + pad, cy0 + pad, cx1 - pad, cy1 - pad))
                # an outline, so a WHITE layer colour is still visible on white
                outlines += [(cx0 + pad, cy0 + pad, cx1 - pad, cy0 + pad + 1),
                             (cx0 + pad, cy1 - pad - 1, cx1 - pad, cy1 - pad),
                             (cx0 + pad, cy0 + pad, cx0 + pad + 1, cy1 - pad),
                             (cx1 - pad - 1, cy0 + pad, cx1 - pad, cy1 - pad)]
        _draw_rects(shader, lines, grid)
        for rgba, rects in fills.items():
            _draw_rects(shader, rects, rgba)
        _draw_rects(shader, outlines, grid)
        # the New Layer button
        nb = _layer_new_btn
        shader.bind()
        _draw_rect(shader, nb[0] - region.x, nb[1] - region.y, nb[2] - region.x,
                   nb[3] - region.y, (0.86, 0.86, 0.88, 1.0))
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] layers panel draw failed: {e!r}")
    finally:
        gpu.state.blend_set(prev)
    try:
        nb = _layer_new_btn
        label = "+  New Layer"
        _draw_text(nb[0] - region.x + ((nb[2] - nb[0]) - _text_width(label, _fpx(11))) / 2,
                   nb[1] - region.y + _fpx(6), label, _fpx(11), (0.1, 0.1, 0.1, 1))
    except Exception:  # noqa: BLE001
        pass

    _draw_layers_panel_text(lay, rows, current)
    _draw_layer_name_edit(shader, rows)


def _draw_layer_name_edit(shader, rows):
    """The in-place name editor : a white field with a blue border over
    the edited row's Name cell, its selection and caret."""
    ed = _layer_edit
    if ed is None:
        return
    i = next((k for k, L in enumerate(_layer_row_objs) if L.name == ed["name"]), None)
    if i is None or i >= len(rows):
        return
    import gpu

    x0, y0, x1, y1 = rightpanel.cell(rows[i], rightpanel.COLUMNS.index("Name"))
    f = ed["field"]
    guard = _text_width("|", _fpx(11))

    def meas(t):
        return (_text_width(t + "|", _fpx(11)) - guard) if t else 0.0
    tx = x0 + 5
    prev = gpu.state.blend_get()
    gpu.state.blend_set("ALPHA")
    try:
        shader.bind()
        blue = (0.20, 0.45, 0.85, 1)
        _draw_rect(shader, x0 + 1, y0 + 1, x1 - 1, y1 - 1, blue)
        _draw_rect(shader, x0 + 2, y0 + 2, x1 - 2, y1 - 2, (1, 1, 1, 1))
        sel = f.selection()
        if sel is not None:
            _draw_rect(shader, tx + meas(f.text[:sel[0]]), y0 + 3,
                       tx + meas(f.text[:sel[1]]), y1 - 3, (0.70, 0.82, 1.0, 1))
        cx = tx + meas(f.text[:f.caret])
        _draw_rect(shader, cx, y0 + 4, cx + 1.5, y1 - 4, (0.08, 0.08, 0.08, 1))
    finally:
        gpu.state.blend_set(prev)
    _draw_text(tx, y0 + _fpx(6), f.text, _fpx(11), (0.08, 0.08, 0.08, 1))


def _draw_layers_panel_text(lay, rows, current):
    """The Layers list's glyphs: column headings, names, ticks and state marks."""
    try:
        import blf
    except Exception:  # noqa: BLE001
        return
    fid = 0
    ink = [c / 255.0 for c in views.OSNAP_PANEL_TEXT]
    dim = [c / 255.0 for c in views.LAYER_DIM]

    # --- column headings ---------------------------------------------------
    blf.size(fid, _fpx(11))
    blf.color(fid, *ink, 1.0)
    hx0, hy0, hx1, hy1 = lay["header"]
    for i, name in enumerate(rightpanel.COLUMNS):
        cx0, cx1 = rightpanel.column_x(i, hx1 - hx0)
        if hx0 + cx0 >= hx1:
            break
        # the reference CAD application heads the name column "Layer", not "Name" (Verified: the
        # screenshot's header reads "Layer"), and abbreviates the narrow ones to
        # whatever fits -- which at 20 px is one letter.
        label = "Layer" if i == 0 else name[0]
        blf.position(fid, hx0 + cx0 + (4 if i == 0 else 6), hy0 + 7, 0)
        blf.draw(fid, label)

    # --- one line per layer ------------------------------------------------
    for row, layer in zip(rows, _layer_row_objs):
        rx0, ry0, rx1, ry1 = row
        is_current = layer is current
        # NAME. the reference CAD application draws the current layer's name BOLD (Verified: "Default" is
        # bold in the screenshot). blf has no bold face here, so the current layer
        # is drawn in full ink and the others dimmer -- application-defined, and flagged: it is a
        # weight difference rendered as a contrast difference.
        blf.size(fid, _fpx(11))
        blf.color(fid, *(ink if is_current else [c * 0.75 for c in ink]), 1.0)
        name = layer.name or ""
        while name and blf.dimensions(fid, name)[0] > rightpanel.NAME_COL_W - 10:
            name = name[:-1]
        blf.position(fid, rx0 + 5, ry0 + 6, 0)
        blf.draw(fid, name)

        def _mark(col_name, glyph, on):
            i = rightpanel.COLUMNS.index(col_name)
            cx0, cy0, cx1, cy1 = rightpanel.cell(row, i)
            if cx1 - cx0 < 6:
                return
            blf.color(fid, *(ink if on else dim), 1.0)
            w = blf.dimensions(fid, glyph)[0]
            blf.position(fid, cx0 + max(2, ((cx1 - cx0) - w) * 0.5), cy0 + 6, 0)
            blf.draw(fid, glyph)

        # CURRENT: a tick, exactly as the screenshot shows for "Default".
        if is_current:
            _mark("Current", "\u2713", True)
        # ON / LOCK. the reference CAD application uses a light bulb and a padlock; we have neither glyph
        # nor artwork, so these are application-defined: a filled vs hollow marker, dimmed when
        # off/unlocked. The STATE is real -- it is the document's.
        _mark("On", "\u25cf" if layer.visible else "\u25cb", layer.visible)
        _mark("Lock", "\u25a0" if layer.locked else "\u25a1", layer.locked)


def _draw_right_tab_icons(box):
    """The four tab icons. Falls back to the tab's initial when an icon is missing,
    which is what happens on a machine without the help bundle."""
    img_shader = _image_shader()
    if img_shader is None:
        return
    try:
        from gpu_extras.batch import batch_for_shader
    except Exception:  # noqa: BLE001
        return
    for i, tab_box in enumerate(box["tabs"]):
        tab = rightpanel.TABS[i]
        tex = _sidebar_texture(tab.icon)
        if tex is None:
            continue
        x0, y0, x1, y1 = rightpanel.icon_box(tab_box)
        try:
            img_shader.bind()
            img_shader.uniform_sampler("image", tex)
            batch_for_shader(
                img_shader, "TRI_FAN",
                {"pos": ((x0, y0), (x1, y0), (x1, y1), (x0, y1)),
                 "texCoord": ((0, 0), (1, 0), (1, 1), (0, 1))},
            ).draw(img_shader)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# BUG REPORT panel
# ---------------------------------------------------------------------------
_bug = {"state": bugreport.ReportState(), "lay": None, "region": None}
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bug_report_state():
    return _bug["state"]


def bug_panel_open():
    return rightpanel.TABS[_right_tab].name == "Bug Report"


def _draw_bug_panel(shader, region, box):
    import gpu

    st = _bug["state"]
    sc = _ui_scale()
    x0, y0, x1, y1 = box["body"]
    fs = 11.0 * sc
    lay = bugreport.layout((x0, y0, x1, y1), sc, measure=lambda t_: _text_width(t_, fs))
    _bug["lay"] = lay
    _bug["region"] = (region.x, region.y)
    ink = (0.08, 0.08, 0.08, 1)
    blue = (0.20, 0.45, 0.85, 1)
    prev = gpu.state.blend_get()
    gpu.state.blend_set("ALPHA")
    try:
        t = lay["title"]
        n_open = bugreport.count_open(bugreport.report_dir(_ROOT))
        _draw_text(t[0], t[1] + 4 * sc, f"Reports: {n_open} open",
                   fs + 1, ink)
        for cid, r in lay["chips"]:
            shader.bind()
            on = cid == st.category
            _draw_rect(shader, *r, blue if on else (0.86, 0.86, 0.88, 1))
            label = dict(bugreport.CATEGORIES)[cid]
            _draw_text(r[0] + (r[2] - r[0] - _text_width(label, fs)) / 2,
                       r[1] + 5 * sc, label, fs, (1, 1, 1, 1) if on else ink)
        b = lay["box"]
        shader.bind()
        _draw_rect(shader, b[0] - 1, b[1] - 1, b[2] + 1, b[3] + 1,
                   blue if st.focus else (0.6, 0.6, 0.62, 1))
        _draw_rect(shader, *b, (1, 1, 1, 1))
        # the box is a real text field -- caret where you click, a
        # selection you can drag or Shift-extend. `bugreport.text_view` is the geometry
        # the click uses too, so the character drawn is the character hit.
        measure = _bug_measure(fs)
        view = bugreport.text_view(st, b, measure, sc)
        _bug["view"] = view
        fld = bugreport.field_of(st)
        first, rows, lh = view["first"], view["rows"], view["line_h"]
        base = view["top"] - lh + 3 * sc         # text baseline of the first row
        if not st.text and not st.focus:
            hint = bugreport.wrap("Click here and type what went wrong, what you "
                                  "expected, and how to make it happen. Ctrl+Enter "
                                  "or Report saves it, copies it, and opens GitHub.",
                                  view["width"], measure)
            for i, ln in enumerate(hint[:rows]):
                _draw_text(view["x0"], base - i * lh, ln, fs, (0.55, 0.55, 0.55, 1))
        else:
            sel = fld.selection()
            if sel is not None and st.focus:
                shader.bind()
                for line, sx0, sx1 in textfield.selection_rects(view["spans"], fld.text,
                                                                sel, measure):
                    if first <= line < first + rows:
                        yb = view["top"] - (line - first + 1) * lh
                        _draw_rect(shader, view["x0"] + sx0, yb,
                                   view["x0"] + max(sx1, sx0 + 3 * sc), yb + lh,
                                   (0.70, 0.82, 1.0, 1))
            for i, (s0, s1) in enumerate(view["spans"][first:first + rows]):
                _draw_text(view["x0"], base - i * lh, fld.text[s0:s1], fs, ink)
            if st.focus:
                cxp, cline = textfield.caret_xy(view["spans"], fld.text, fld.caret,
                                                measure, lh)
                if first <= cline < first + rows:
                    cx = view["x0"] + cxp
                    cy = base - (cline - first) * lh
                    shader.bind()
                    _draw_rect(shader, cx, cy - 2 * sc, cx + 1.5 * sc, cy + 11 * sc, ink)
        # the screenshot, the session log and the file are optional
        log_ok = sessionlog.available()
        for cid, r in lay.get("checks", ()):
            on = {"shot": st.include_shot, "file": st.include_file}.get(
                cid, st.include_log and log_ok)
            enabled = cid != "log" or log_ok
            sq = 12 * sc
            qx0, qy0 = r[0], r[1] + (r[3] - r[1] - sq) / 2
            shader.bind()
            _draw_rect(shader, qx0, qy0, qx0 + sq, qy0 + sq,
                       (0.45, 0.45, 0.48, 1) if enabled else (0.75, 0.75, 0.77, 1))
            _draw_rect(shader, qx0 + 1, qy0 + 1, qx0 + sq - 1, qy0 + sq - 1,
                       blue if on else (1, 1, 1, 1))
            label = dict(bugreport.CHECKS)[cid]
            if not enabled:
                label += " (none yet)"
            _draw_text(qx0 + sq + 5 * sc, qy0 + 2 * sc, label, fs - 1,
                       ink if enabled else (0.6, 0.6, 0.6, 1))
        for bid, r in lay["buttons"]:
            shader.bind()
            _draw_rect(shader, *r, blue if bid == "submit" else (0.86, 0.86, 0.88, 1))
            label = {"submit": "Report", "clear": "Clear"}[bid]
            _draw_text(r[0] + (r[2] - r[0] - _text_width(label, fs)) / 2,
                       r[1] + 7 * sc, label, fs,
                       (1, 1, 1, 1) if bid == "submit" else ink)
        if st.message:
            sr = lay["status"]
            for i, ln in enumerate(bugreport.wrap(st.message, sr[2] - sr[0],
                                                  lambda t_: _text_width(t_, fs - 1))[:2]):
                _draw_text(sr[0], sr[1] + 2 * sc - i * 12 * sc, ln, fs - 1,
                           (0.3, 0.3, 0.3, 1))
    finally:
        gpu.state.blend_set(prev)


def _bug_measure(fs):
    """Width of a string in the report font, trailing spaces INCLUDED (measured against
    a guard glyph, so a caret after a space sits after it), memoised per draw size."""
    cache = _bug.setdefault("measure_cache", {})
    if cache.get("fs") != fs:
        cache.clear()
        cache["fs"] = fs
    guard = _text_width("|", fs)

    def measure(t):
        w = cache.get(t)
        if w is None:
            w = _text_width(t + "|", fs) - guard if t else 0.0
            if len(cache) < 20000:
                cache[t] = w
        return w
    return measure


def _bug_context():
    """Information needed to reproduce the report, gathered at submit time."""
    import datetime
    import subprocess

    ctx = []
    cmd = app_state.active_command
    ctx.append(("Command running", getattr(cmd, "name", None) or "none"))
    ctx.append(("Recent command line (oldest first)",
                [ln for ln in app_state.history[-15:]]))
    ctx.append(("Selected", ", ".join(sorted(selected_display_names())) or "nothing"))
    model = app_state.model
    try:
        ctx.append(("Document", f"{app_state.doc_title()} -- "
                                f"{len(model.file.Objects)} object(s), units "
                                f"{model.unit_system()}, "
                                f"{'UNSAVED changes' if app_state.doc_modified() else 'saved'}"))
    except Exception:  # noqa: BLE001
        pass
    try:
        act = active_pane_area()
        ctx.append(("Viewports", ", ".join(
            f"{pane_label(a)}={display_mode_of(a)}" + (" (active)" if a == act else "")
            for a in _view3d_areas())))
    except Exception:  # noqa: BLE001
        pass
    try:
        head = subprocess.run(["git", "-C", _ROOT, "log", "-1", "--format=%h %s"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
        ctx.append(("Build", head or "unknown"))
    except Exception:  # noqa: BLE001
        ctx.append(("Build", "unknown"))
    ctx.append(("Window", f"{bpy.context.window.width} x {bpy.context.window.height}"
                if bpy.context.window else "?"))
    return ctx, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _bug_save_document(folder, num):
    """: save a COPY of the document beside report `num`. Returns
    ``(relative path, None)`` or ``(None, why not)``. Like the recovery copy
    (`file_commands.write_recovery`): written atomically, and the document keeps its
    own file and its modified state -- reporting a bug is not saving."""
    rel = bugreport.file_rel(num)
    target = os.path.join(folder, rel)
    tmp = f"{target}.saving-{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if not app_state.model.save(tmp) or not os.path.exists(tmp):
            raise OSError("the 3dm writer reported failure")
        os.replace(tmp, target)
    except Exception as e:  # noqa: BLE001
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return None, f"could not save the file: {e}"
    return rel, None


def submit_bug_report():
    """Save locally, copy sanitized Markdown, and open a user-reviewed GitHub issue."""
    st = _bug["state"]
    if not st.text.strip():
        st.message = "Type the problem first -- nothing was saved"
        return st.message
    folder = bugreport.report_dir(_ROOT)
    num = bugreport.next_number(folder)
    shot_rel = None
    if st.include_shot:                  # optional
        os.makedirs(os.path.join(folder, "shots"), exist_ok=True)
        shot_rel = os.path.join("shots", f"{num:04d}.png")
        try:
            bpy.ops.screen.screenshot(filepath=os.path.join(folder, shot_rel))
        except Exception:  # noqa: BLE001
            shot_rel = None
    log_lines = sessionlog.tail() if (st.include_log and sessionlog.available()) else None
    doc_rel = doc_error = None
    if st.include_file:                  # optional
        doc_rel, doc_error = _bug_save_document(folder, num)
    ctx, when = _bug_context()
    entry = bugreport.format_report(
        num, when, st.category, st.text, ctx, shot_rel, log_lines=log_lines,
        doc_rel=doc_rel, doc_error=doc_error)
    path = bugreport.write_report(folder, entry)
    public_body = bugreport.github_body(entry)
    issue_url = bugreport.github_issue_url(st.category, st.text, public_body)
    copied = opened = False
    try:
        bpy.context.window_manager.clipboard = public_body
        copied = True
    except Exception:  # noqa: BLE001
        pass
    try:
        import webbrowser

        opened = bool(webbrowser.open(issue_url, new=2))
    except Exception:  # noqa: BLE001
        opened = False
    st.extra["last_report_path"] = path
    st.extra["last_issue_url"] = issue_url
    st.text, st.focus = "", False
    if opened and copied:
        st.message = "Saved locally; GitHub opened. Review the copied report before submitting."
    elif copied:
        st.message = "Saved locally and copied. Open the Narwhal GitHub Issues page to submit."
    else:
        st.message = f"Saved locally at {path}; GitHub could not be opened automatically."
    if doc_error:
        st.message += f" Document copy failed: {doc_error}"
    app_state.log(f"Report #{num} saved locally to {path}; no attachment was uploaded"
                  + ("; GitHub issue page opened" if opened else ""))
    redraw_cmdbar()
    return st.message


def bug_panel_click(win_x, win_y, shift=False):
    """A click while the Bug Report panel is showing. Returns what it did, or None.

    In the text box it places the caret under the click (Shift extends the selection)
    and arms a drag-select --.
    """
    lay = _bug.get("lay")
    if lay is None or not bug_panel_open():
        return None
    rx, ry = _bug.get("region") or (0, 0)
    fs = 11.0 * _ui_scale()
    did = bugreport.click(_bug["state"], lay, win_x - rx, win_y - ry,
                          view=_bug.get("view"), measure=_bug_measure(fs), shift=shift,
                          log_ok=sessionlog.available())
    if did == "submit":
        submit_bug_report()
    if did is not None:
        _tag_redraw_bands()
    # a click elsewhere only takes the focus away -- it still does its own job
    return None if did == "blur" else did


def bug_panel_drag(win_x, win_y):
    """Mouse moved with the button held after a click in the box: drag-select."""
    view = _bug.get("view")
    if view is None or not bug_panel_open():
        return False
    rx, ry = _bug.get("region") or (0, 0)
    if bugreport.drag(_bug["state"], view, win_x - rx, win_y - ry,
                      _bug_measure(11.0 * _ui_scale())):
        _tag_redraw_bands()
        return True
    return False


def bug_panel_release():
    bugreport.release(_bug["state"])


def bug_panel_dragging():
    return bool(_bug["state"].extra.get("dragging"))


def bug_panel_key(kind, char="", shift=False):
    """A key while the report box has focus; None when it is not the panel's.
    "copy"/"cut" leave the text for the clipboard in ``state.extra["clipboard"]``."""
    if not bug_panel_open() or not _bug["state"].focus:
        return None
    view = _bug.get("view")
    did = bugreport.key(_bug["state"], kind, char, shift=shift,
                        measure=_bug_measure(11.0 * _ui_scale()),
                        width=view["width"] if view else None)
    if did == "submit":
        submit_bug_report()
    _tag_redraw_bands()
    return did


def _tag_redraw_bands():
    try:
        for a in bpy.context.screen.areas:
            if a.type == "INFO":
                a.tag_redraw()
    except Exception:  # noqa: BLE001
        pass


def _draw_right_band_text(box):
    """The honest "not implemented" line in the body.

    The CAPTION is not drawn here: the band is an INFO area and its own header
    region already carries one, which is why `rightpanel.layout` is called with
    `title_h=0`. `_draw_narwhal_toolbar` puts the active panel's name there --
    Verified that the reference CAD application captions the container with the ACTIVE panel's name (the
    screenshot's caption reads "Layers", the selected tab, not "Right Container").
    """
    try:
        import blf
    except Exception:  # noqa: BLE001
        return
    tab = rightpanel.TABS[_right_tab]
    if tab.built:
        return
    fid = 0
    bx0, by0, bx1, by1 = box["body"]
    blf.size(fid, _fpx(11))
    blf.color(fid, 0.45, 0.45, 0.45, 1.0)
    lines = (f"{tab.name} panel", "not implemented yet")
    y = (by0 + by1) * 0.5 + 8
    for line in lines:
        w = blf.dimensions(fid, line)[0]
        blf.position(fid, bx0 + max(4, ((bx1 - bx0) - w) * 0.5), y, 0)
        blf.draw(fid, line)
        y -= 14


# ---------------------------------------------------------------------------
# The Properties panel shows Type, Name, Layer, and Display Color immediately. Details
# come from exact geometry through `bridge.object_details`, never the display proxy.
# Linetype, print color/width, and render settings remain unimplemented.
# ---------------------------------------------------------------------------

#: (selection, undo serial) -> rows; `object_details` can walk an OCCT shape, so it is
#: computed once per document state, not every frame
_props_cache = {"key": None, "rows": []}

#: The Details button defers expensive geometry inspection until clicked. `key` is
#: the selection and undo serial for which measurements were requested;
#: `btn` the button's window rectangle, recorded by the draw. The label row value is
#: `DETAILS_BUTTON` so the draw knows where to put it.
_props_details = {"key": None, "rows": [], "btn": None}
DETAILS_BUTTON = "\x00details-button"


#: File3dm wrapper lookup for Properties and Gumball eligibility. The key is the
#: complete document state stamp, so wrappers from an old table are never retained.
#: The two maps preserve the two historical matching rules: `_document_object` is
#: exact/case-sensitive, while `geom_by_name_in_document` is case-insensitive.
_document_object_cache = {"key": None, "exact": None, "folded": None, "builds": 0}


def _document_object_index():
    key = _doc_key()
    cache = _document_object_cache
    if key is not None and cache["key"] == key:
        return cache["exact"], cache["folded"]
    exact = {}
    folded = {}
    try:
        model = getattr(app_state, "model", None)
        file3dm = getattr(model, "file", None)
        if file3dm is not None:
            for fobj in file3dm.Objects:
                try:
                    name = fobj.Attributes.Name or ""
                except Exception:  # noqa: BLE001
                    continue
                # Preserve TABLE order when duplicate names exist, matching the
                # former first-hit scans.
                exact.setdefault(name, fobj)
                folded.setdefault(str(name).lower(), fobj)
    except Exception:  # noqa: BLE001
        pass
    cache.update(key=key, exact=exact, folded=folded, builds=cache["builds"] + 1)
    return exact, folded


def _document_object(name):
    try:
        exact, _folded = _document_object_index()
        return exact.get(name)
    except Exception:  # noqa: BLE001
        return None


def _enum_name(value):
    """Normalize rhino3dm enum spellings to ObjectProperties source names."""
    if value is None:
        return None
    text = str(value).rsplit(".", 1)[-1].lower()
    for word in ("layer", "parent", "material", "custom", "display", "default", "specified"):
        if word in text:
            return word
    if "object" in text:
        return "custom"
    return None


def _color_value(value):
    try:
        return tuple(int(value[i]) for i in range(3))
    except Exception:  # noqa: BLE001
        return None


def _object_properties(name):
    """Build only values actually available from this File3dm object/document."""
    fobj = _document_object(name)
    if fobj is None:
        return ObjectProperties(name=name)
    attrs = getattr(fobj, "Attributes", None)
    if attrs is None:
        return ObjectProperties(name=name)
    layer_name = None
    layer = None
    try:
        layer = app_state.model.file.Layers[int(attrs.LayerIndex)]
        layer_name = getattr(layer, "Name", None)
    except Exception:  # noqa: BLE001
        pass
    geom = getattr(fobj, "Geometry", None)
    obj_type = None
    try:
        obj_type = dict(bridge.object_details(geom, measure=False)).get("Type")
    except Exception:  # noqa: BLE001
        pass
    color_source = _enum_name(getattr(attrs, "ColorSource", None))
    display_color = _color_value(getattr(attrs, "ObjectColor", None))
    if color_source == "layer" and layer is not None:
        display_color = _color_value(getattr(layer, "Color", None))
    lt_source = _enum_name(getattr(attrs, "LinetypeSource", None))
    lt_index = getattr(attrs, "LinetypeIndex", None)
    linetype = None
    try:
        if lt_index is not None and int(lt_index) >= 0:
            linetype = getattr(app_state.model.file.Linetypes[int(lt_index)], "Name", None)
        if lt_source == "layer" and layer is not None:
            layer_lt = getattr(layer, "LinetypeIndex", None)
            if layer_lt is not None and int(layer_lt) >= 0:
                linetype = getattr(app_state.model.file.Linetypes[int(layer_lt)], "Name", None)
    except Exception:  # noqa: BLE001
        pass
    return ObjectProperties(
        type=obj_type, name=name, layer=layer_name,
        display_color_source=color_source, display_color=display_color,
        linetype=linetype, linetype_source=lt_source,
        linetype_scale=getattr(attrs, "LinetypeScale", None),
        print_color_source=_enum_name(getattr(attrs, "PlotColorSource", None)),
        print_color=_color_value(getattr(attrs, "PlotColor", None)),
        print_width_source=_enum_name(getattr(attrs, "PlotWeightSource", None)),
        print_width=getattr(attrs, "PlotWeight", None),
        no_print=getattr(attrs, "NoPlot", None) if hasattr(attrs, "NoPlot") else None,
    )


def properties_rows():
    """``[(label, value), ...]`` for the Properties panel and the current selection."""
    names = sorted(selection_names())
    key = (tuple(names), _undo_serial())
    if _props_cache["key"] == key:
        return list(_props_cache["rows"])
    rows = []
    if not names:
        rows = [("", "No object selected")]
    elif len(names) > 1:
        rows = rightpanel.property_rows([_object_properties(n) for n in names])
    else:
        name = names[0]
        prop_rows = rightpanel.property_rows([_object_properties(name)])
        fobj = _document_object(name)
        geom = fobj.Geometry if fobj is not None else geom_by_name_in_document(name)
        try:
            details = (list(bridge.object_details(geom, measure=False))
                       if geom is not None else [])
        except Exception as e:  # noqa: BLE001
            details = [("Details", f"unavailable: {e}")]
        # Keep the existing cheap geometry rows (including Face types) after the
        # aggregate-backed Object page values.
        rows = prop_rows + [("", "")]
        rows += [(k, v) for k, v in details if k not in {"Type", "Name", "Layer"}]
        if geom is not None and not any(k == "Details" for k, _v in details):
            if _props_details["key"] == key:
                rows += [("", "")] + list(_props_details["rows"])
            else:
                rows += [("", ""), ("", DETAILS_BUTTON)]
    _props_cache["key"], _props_cache["rows"] = key, rows
    return list(rows)


def properties_details():
    """The Details button: measure the ONE selected object (bounding box, size, length,
    area, volume -- `bridge.MEASURE_ROWS`) and show the rows under the panel. Returns a
    message for the command line."""
    names = sorted(selection_names())
    if len(names) != 1:
        return "Details: select one object"
    geom = geom_by_name_in_document(names[0])
    if geom is None:
        return "Details: no geometry"
    try:
        full = bridge.object_details(geom)
    except Exception as e:  # noqa: BLE001
        full = [("Details", f"unavailable: {e}")]
    measured = [(k, v) for k, v in full if k in bridge.MEASURE_ROWS or k == "Details"]
    _props_details["key"] = (tuple(names), _undo_serial())
    _props_details["rows"] = measured or [("", "no measurements for this object")]
    _props_cache["key"] = None
    return None


def properties_details_button_at(x, y):
    """True when window point (x, y) is on the Details button as last drawn."""
    if rightpanel.TABS[_right_tab].name != "Properties":
        return False
    b = _props_details["btn"]
    return b is not None and b[0] <= x <= b[2] and b[1] <= y <= b[3]


def _draw_properties_panel(region, box):
    """The Properties body: label/value rows, top-down, clipped to the body."""
    try:
        import blf
    except Exception:  # noqa: BLE001
        return
    fid = 0
    bx0, by0, bx1, by1 = box["body"]
    ink = [c / 255.0 for c in views.OSNAP_PANEL_TEXT]
    dim = [c / 255.0 for c in views.LAYER_DIM]
    size = _fpx(11)
    blf.size(fid, size)
    line_h = rightpanel.ROW_H
    pad = _fpx(5)
    label_w = min((bx1 - bx0) * 0.42, _fpx(84))
    y = by1 - pad - size
    _props_details["btn"] = None
    for label, value in properties_rows():
        if y < by0 + pad:
            break
        if value == DETAILS_BUTTON:
            bw = _text_width("Details", size) + 4 * pad
            x0b, y0b = bx0 + pad, y - pad
            x1b, y1b = x0b + bw, y + size + pad
            sh = _uniform_color_shader()
            if sh is not None:
                import gpu

                prev = gpu.state.blend_get()
                gpu.state.blend_set("ALPHA")
                sh.bind()
                _draw_rect(sh, x0b, y0b, x1b, y1b, (0.86, 0.86, 0.88, 1.0))
                _draw_rect(sh, x0b, y0b, x1b, y0b + 1, (0.55, 0.55, 0.58, 1.0))
                _draw_rect(sh, x0b, y1b - 1, x1b, y1b, (0.55, 0.55, 0.58, 1.0))
                _draw_rect(sh, x0b, y0b, x0b + 1, y1b, (0.55, 0.55, 0.58, 1.0))
                _draw_rect(sh, x1b - 1, y0b, x1b, y1b, (0.55, 0.55, 0.58, 1.0))
                gpu.state.blend_set(prev)
            blf.color(fid, 0.1, 0.1, 0.1, 1.0)
            blf.position(fid, x0b + 2 * pad, y, 0)
            blf.draw(fid, "Details")
            _props_details["btn"] = (region.x + x0b, region.y + y0b,
                                     region.x + x1b, region.y + y1b)
            y -= line_h + pad
            continue
        if label:
            blf.color(fid, *dim, 1.0)
            blf.position(fid, bx0 + pad, y, 0)
            blf.draw(fid, _clip_text(label, label_w - pad, size))
        blf.color(fid, *ink, 1.0)
        vx = bx0 + (label_w if label else pad)
        blf.position(fid, vx, y, 0)
        blf.draw(fid, _clip_text(str(value), bx1 - vx - pad, size))
        y -= line_h


def _clip_text(text, width, size):
    """`text` cut to fit `width` px at `size`, ending in an ellipsis when cut."""
    if width <= 0:
        return ""
    if _text_width(text, size) <= width:
        return text
    while text and _text_width(text + "…", size) > width:
        text = text[:-1]
    return text + "…" if text else ""


def _image_shader():
    """The builtin textured-quad shader, under whichever name this Blender uses.

    Same reasoning and same fallback shape as `_uniform_color_shader`: Blender 4.0
    dropped the ``2D_`` prefix, so both names are tried rather than guessed at. This
    one cannot be probed headlessly either -- `gpu.shader.from_builtin` raises
    "requires the gpu module to be initialized" under `-b` for EVERY name, so a probe
    cannot tell a good name from a bad one and the fallback is the honest answer.
    """
    import gpu

    for name in ("IMAGE", "2D_IMAGE"):
        try:
            return gpu.shader.from_builtin(name)
        except Exception:  # noqa: BLE001
            continue
    return None


#: GPU textures for sidebar icons, keyed by filename. Built once per icon and kept:
#: `gpu.texture.from_image` is not free and the band redraws often.
_sidebar_textures = {}
#: Icons we failed to load, so a bad file is tried once rather than every draw --
#: the same guard `_toolbar_icon_bad` gives the strip.
_sidebar_tex_bad = set()


def _sidebar_texture(icon):
    """A GPU texture for one icon filename, or None.

    The PNGs are 96x96 (measured) and drawn into a 24 px cell, so the GPU scales them
    down -- which is why no separate small set is needed.
    """
    if not icon or icon in _sidebar_tex_bad:
        return None
    tex = _sidebar_textures.get(icon)
    if tex is not None:
        return tex
    path = tbar.icon_path(icon)
    if not path:
        _sidebar_tex_bad.add(icon)
        return None
    try:
        import gpu

        img = bpy.data.images.load(path, check_existing=True)
        tex = gpu.texture.from_image(img)
    except Exception:  # noqa: BLE001
        _sidebar_tex_bad.add(icon)
        return None
    _sidebar_textures[icon] = tex
    return tex


#: The sidebar cells from the last draw, for hit-testing, plus the buttons they map
#: to. Recorded by the draw for the same reason the command bar's option boxes are:
#: the thing we hit-test is then the thing that was drawn.
_sidebar_cells = []
_sidebar_buttons = []
_sidebar_hover_index = None
_sidebar_hover_part = None
_sidebar_hover_pos = None
_sidebar_region_box = None
_sidebar_pressed = None
_sidebar_flyout_button = None
_sidebar_flyout_state = None


def _draw_left_sidebar(shader, region):
    """Draw the Main sidebar grid in the band's upper half and return its height.

    Fixed-size icons reflow as the dock widens, and a small triangle marks cells linked
    to flyout toolbars. The grid is top-aligned above the Osnap panel.
    """
    global _sidebar_cells, _sidebar_buttons, _sidebar_hover_index, _sidebar_hover_part
    global _sidebar_region_box

    _sidebar_region_box = (region.x, region.y,
                           region.x + region.width, region.y + region.height)
    buttons = tbar.sidebar_buttons(set(COMMANDS))
    if not buttons:
        _sidebar_cells, _sidebar_buttons = [], []
        return 0.0
    # Icons keep their normal 24-pixel size while the grid reflows with dock width.
    columns = tbar.sidebar_columns(region.width)
    x_grid = tbar.sidebar_grid_x(region.width, columns)
    y_top = float(region.height)  # region coordinates grow upward
    cells = tbar.sidebar_cells(len(buttons), x_grid, y_top, columns=columns)
    _sidebar_cells = [(i, region.x + x0, region.y + y0,
                       region.x + x1, region.y + y1)
                      for i, x0, y0, x1, y1 in cells]
    _sidebar_buttons = buttons
    sidebar_flyout_reflow()
    if _sidebar_hover_pos is not None:
        action = tbar.sidebar_action_at(_sidebar_hover_pos[0], _sidebar_hover_pos[1],
                                        _sidebar_cells, _sidebar_buttons)
        _sidebar_hover_index = action[0] if action is not None else None
        _sidebar_hover_part = action[1] if action is not None else None
    else:
        _sidebar_hover_index = None
        _sidebar_hover_part = None

    img_shader = _image_shader()
    pad = tbar.SIDEBAR_PAD
    mark = tbar.SIDEBAR_MARK
    try:
        from gpu_extras.batch import batch_for_shader
    except Exception:  # noqa: BLE001
        return tbar.sidebar_height(len(buttons), columns=columns)

    for (i, x0, y0, x1, y1), btn in zip(cells, buttons):
        if i == _sidebar_hover_index:
            shader.bind()
            _draw_rect(shader, x0, y0, x1, y1, SIDEBAR_HOVER_COLOR)
        tex = _sidebar_texture(btn.icon)
        if tex is not None and img_shader is not None:
            ix0, iy0 = x0 + pad, y0 + pad
            ix1, iy1 = x1 - pad, y1 - pad
            try:
                img_shader.bind()
                img_shader.uniform_sampler("image", tex)
                batch_for_shader(
                    img_shader, "TRI_FAN",
                    {"pos": ((ix0, iy0), (ix1, iy0), (ix1, iy1), (ix0, iy1)),
                     "texCoord": ((0, 0), (1, 0), (1, 1), (0, 1))},
                ).draw(img_shader)
            except Exception:  # noqa: BLE001
                pass
        # The cascade marker. Verified (the reference CAD application documentation): this triangle means a
        # LINKED TOOLBAR, not merely a second right-click command. Its selectable box
        # is now visibly distinct on hover and press, using the SAME pure geometry as
        # hit-testing so feedback cannot point at a dead region.
        if btn.link:
            fly_box = tbar.sidebar_flyout_box((i, x0, y0, x1, y1))
            fly_state = (_sidebar_pressed == (i, "flyout")
                         or (i == _sidebar_hover_index
                             and _sidebar_hover_part == "flyout"))
            if fly_box is not None and fly_state:
                fx0, fy0, fx1, fy1 = fly_box
                color = (SIDEBAR_FLYOUT_PRESSED_COLOR
                         if _sidebar_pressed == (i, "flyout")
                         else SIDEBAR_FLYOUT_HOVER_COLOR)
                shader.bind()
                _draw_rect(shader, fx0, fy0, fx1, fy1, color)
                # One-pixel frame gives the small light hover plate a definite edge.
                border = views.gpu_rgba(views.TAB_BORDER)
                _draw_rect(shader, fx0, fy0, fx1, fy0 + 1, border)
                _draw_rect(shader, fx0, fy1 - 1, fx1, fy1, border)
                _draw_rect(shader, fx0, fy0, fx0 + 1, fy1, border)
                _draw_rect(shader, fx1 - 1, fy0, fx1, fy1, border)
            shader.bind()
            _draw_tri(shader,
                      ((x1 - pad, y0 + pad),
                       (x1 - pad, y0 + pad + mark),
                       (x1 - pad - mark, y0 + pad)),
                      SIDEBAR_MARK_COLOR)
    return tbar.sidebar_height(len(buttons), columns=columns)


def _sidebar_wrap_line(text, width, font_size):
    """Wrap one tooltip paragraph to the left dock's measured pixel width."""
    words = str(text or "").split()
    if not words:
        return []
    lines = []
    line = words.pop(0)
    for word in words:
        candidate = f"{line} {word}"
        if _text_width(candidate, font_size) <= width:
            line = candidate
        else:
            lines.append(_clip_text(line, width, font_size))
            line = word
    lines.append(_clip_text(line, width, font_size))
    return lines


def _sidebar_command_name(button):
    """Canonical registered command name for a toolbar button."""
    key = getattr(button, "left", None)
    command = COMMANDS.get(key)
    name = getattr(command, "name", None)
    return name or (str(key).replace("_", " ").title() if key else None)


def _sidebar_overlay_left():
    """First drawable viewport pixel to the right of the sidebar.

    Blender keeps a narrow divider between adjacent areas. The INFO region's right
    coordinate can therefore be one pixel left of the VIEW_3D region where floating
    chrome is actually drawn. Anchoring to the viewport boundary keeps the flyout's
    left border and first icon out from under that divider after any dock resize.
    """
    dock_right = (_sidebar_region_box[2] if _sidebar_region_box is not None
                  else max((cell[3] for cell in _sidebar_cells), default=0.0))
    screen = getattr(bpy.context, "screen", None)
    candidates = []
    if screen is not None:
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((item for item in area.regions
                           if item.type == "WINDOW" and item.width > 1), None)
            if region is not None and region.x >= dock_right:
                candidates.append(float(region.x))
    return max(float(dock_right), min(candidates)) if candidates else float(dock_right)


def sidebar_tooltip_layout():
    """The one-line sidebar hover card in window coordinates, or ``None``."""
    if (_sidebar_hover_index is None
            or not (0 <= _sidebar_hover_index < len(_sidebar_buttons))):
        return None
    source = next((cell for cell in _sidebar_cells
                   if cell[0] == _sidebar_hover_index), None)
    button = _sidebar_buttons[_sidebar_hover_index]
    lines = tbar.sidebar_tooltip_lines(
        button, _sidebar_hover_part or "command", _sidebar_command_name(button))
    window = getattr(bpy.context, "window", None)
    if source is None or not lines or window is None:
        return None
    # Always place the card just outside the responsive grid, while retaining the
    # hovered row's vertical center. A first-column icon therefore gets the same nearby
    # tooltip as a last-column icon instead of drawing a card inside the dock clipping area.
    anchor = (source[0], source[1], source[2], _sidebar_overlay_left(), source[4])
    text = lines[0]
    box = tbar.sidebar_tooltip_layout(
        anchor, _text_width(text, tbar.SIDEBAR_TOOLTIP_FONT),
        window.width, window.height)
    return {"box": box, "lines": lines} if box is not None else None


def draw_sidebar_tooltip_overlay():
    """Draw the compact sidebar command name over the adjacent viewport."""
    context = bpy.context
    area = getattr(context, "area", None)
    region = getattr(context, "region", None)
    layout = sidebar_tooltip_layout()
    if layout is None or area is None or region is None or area.type != "VIEW_3D":
        return
    box = layout["box"]
    region_box = (region.x, region.y, region.x + region.width, region.y + region.height)
    if (box[2] <= region_box[0] or box[0] >= region_box[2]
            or box[3] <= region_box[1] or box[1] >= region_box[3]):
        return
    try:
        import gpu
    except Exception:  # noqa: BLE001
        return
    shader = _uniform_color_shader()
    if shader is None:
        return
    x0, y0, x1, y1 = (box[0] - region.x, box[1] - region.y,
                      box[2] - region.x, box[3] - region.y)
    prev = gpu.state.blend_get()
    gpu.state.blend_set("ALPHA")
    try:
        shader.bind()
        _draw_rect(shader, x0, y0, x1, y1, SIDEBAR_TOOLTIP_BG)
        _draw_rect(shader, x0, y0, x1, y0 + 1, SIDEBAR_TOOLTIP_BORDER)
        _draw_rect(shader, x0, y1 - 1, x1, y1, SIDEBAR_TOOLTIP_BORDER)
        _draw_rect(shader, x0, y0, x0 + 1, y1, SIDEBAR_TOOLTIP_BORDER)
        _draw_rect(shader, x1 - 1, y0, x1, y1, SIDEBAR_TOOLTIP_BORDER)
        _draw_text(x0 + tbar.SIDEBAR_TOOLTIP_PAD,
                   y1 - tbar.SIDEBAR_TOOLTIP_PAD - tbar.SIDEBAR_TOOLTIP_FONT,
                   layout["lines"][0], tbar.SIDEBAR_TOOLTIP_FONT,
                   SIDEBAR_TOOLTIP_TEXT)
    finally:
        gpu.state.blend_set(prev)


def sidebar_hover_update(mouse_x, mouse_y):
    """Track CAD-style grey hover feedback for the responsive sidebar.

    Returns True only when the highlighted button OR its selectable part changes,
    allowing viewport mouse motion to avoid unnecessary dock redraws.
    """
    global _sidebar_hover_index, _sidebar_hover_part, _sidebar_hover_pos
    _sidebar_hover_pos = (mouse_x, mouse_y)
    action = tbar.sidebar_action_at(mouse_x, mouse_y, _sidebar_cells,
                                    _sidebar_buttons)
    idx = action[0] if action is not None else None
    part = action[1] if action is not None else None
    if (idx, part) == (_sidebar_hover_index, _sidebar_hover_part):
        return False
    _sidebar_hover_index, _sidebar_hover_part = idx, part
    return True


def sidebar_action_at(mouse_x, mouse_y):
    """The sidebar button and selectable part under the window cursor."""
    action = tbar.sidebar_action_at(mouse_x, mouse_y, _sidebar_cells,
                                    _sidebar_buttons)
    if action is None:
        return None
    index, part = action
    if not (0 <= index < len(_sidebar_buttons)):
        return None
    return (index, _sidebar_buttons[index], part)


def sidebar_hover_redraw_update(mouse_x, mouse_y):
    """Update sidebar hover and redraw both its cell and viewport tooltip."""
    changed = sidebar_hover_update(mouse_x, mouse_y)
    if changed:
        _tag_redraw_bands()
        _tag_redraw_viewports()
    return changed


def sidebar_press_begin(index, part):
    """Record visible flyout press feedback. True only when state changes."""
    global _sidebar_pressed
    state = (index, part) if part == "flyout" else None
    if state == _sidebar_pressed:
        return False
    _sidebar_pressed = state
    return True


def sidebar_press_clear():
    """Clear sidebar press feedback. True when a redraw is needed."""
    global _sidebar_pressed
    if _sidebar_pressed is None:
        return False
    _sidebar_pressed = None
    return True


def sidebar_button_at(mouse_x, mouse_y):
    """The sidebar `Button` under the window cursor, or None.

    Tested against the cells the last draw recorded, so a click can only hit a button
    that is actually on screen.
    """
    idx = tbar.hit_sidebar(mouse_x, mouse_y,
                           [(i, a, b, c, d) for i, a, b, c, d in _sidebar_cells])
    if idx is None or idx >= len(_sidebar_buttons):
        return None
    return _sidebar_buttons[idx]


def sidebar_click(btn, right=False):
    """Run a sidebar button. Returns a line to log, or None.

    Honours the reference CAD application's documented second command -- "Many icons have a second command
    that you can access by clicking the right mouse button" -- and reports an unbuilt
    command by name instead of doing nothing, the same rule the strip and the menus
    follow.
    """
    if btn is None:
        return None
    cmd = btn.right if right else btn.left
    label = (btn.right_tooltip if right else btn.tooltip) or btn.text or cmd
    if not cmd:
        return f"{label}: nothing bound to that button"
    if cmd not in COMMANDS:
        return f"{label} is not implemented yet"
    app_state.start_command(cmd)
    _ensure_input_open()
    return None


def sidebar_flyout_reflow():
    """Re-anchor an open flyout to the current responsive sidebar cell."""
    state = _sidebar_flyout_state
    window = getattr(bpy.context, "window", None)
    if state is None or window is None:
        return False
    index = tbar.sidebar_button_index(
        _sidebar_buttons, state.get("button"), state.get("source_index"))
    source = next((cell for cell in _sidebar_cells if cell[0] == index), None)
    button = (_sidebar_buttons[index]
              if index is not None and 0 <= index < len(_sidebar_buttons) else None)
    rows = tbar.sidebar_flyout_rows(button, set(COMMANDS))
    if source is None or not getattr(button, "link", None) or not rows:
        return False
    anchor_x = _sidebar_overlay_left()
    layout = tbar.flyout_popup_layout(
        anchor_x, source[4], rows, window.width, window.height)
    changed = layout["panel"] != state["layout"]["panel"]
    state.update(button=button, source_index=index, layout=layout)
    if changed:
        state["hover"] = None
    return changed


def show_sidebar_flyout(button, source_index=None):
    """Open ``button``'s linked tools as a floating five-column icon palette."""
    global _sidebar_flyout_button, _sidebar_flyout_state
    index = tbar.sidebar_button_index(_sidebar_buttons, button, source_index)
    source = next((cell for cell in _sidebar_cells if cell[0] == index), None)
    live_button = (_sidebar_buttons[index]
                   if index is not None and 0 <= index < len(_sidebar_buttons) else None)
    rows = tbar.sidebar_flyout_rows(live_button, set(COMMANDS))
    if not getattr(live_button, "link", None) or not rows:
        app_state.log(f"{getattr(button, 'link', None) or 'That'} toolbar is unavailable")
        redraw_cmdbar()
        return None
    window = getattr(bpy.context, "window", None)
    if source is None or window is None:
        app_state.log(f"{live_button.link} toolbar could not find its screen anchor")
        redraw_cmdbar()
        return None
    _sidebar_flyout_button = live_button
    anchor_x = _sidebar_overlay_left()
    layout = tbar.flyout_popup_layout(anchor_x, source[4], rows,
                                      window.width, window.height)
    _sidebar_flyout_state = {"button": live_button, "source_index": index,
                             "layout": layout, "hover": None}
    sidebar_press_clear()
    _tag_redraw_bands()
    _tag_redraw_viewports()
    return live_button.link


def sidebar_flyout_layout():
    """Return the currently drawn floating-toolbar layout for tests/introspection."""
    return None if _sidebar_flyout_state is None else _sidebar_flyout_state["layout"]


def sidebar_flyout_close():
    """Close the floating icon toolbar. Return whether visible state changed."""
    global _sidebar_flyout_state
    if _sidebar_flyout_state is None:
        return False
    _sidebar_flyout_state = None
    _tag_redraw_viewports()
    return True


def _inside_box(x, y, box):
    return box[0] <= x < box[2] and box[1] <= y < box[3]


def sidebar_flyout_action_at(mouse_x, mouse_y):
    """Return the icon action under a window point, or ``None``."""
    if _sidebar_flyout_state is None:
        return None
    layout = _sidebar_flyout_state["layout"]
    index = tbar.hit_flyout(mouse_x, mouse_y, layout["cells"])
    if index is None or not (0 <= index < len(layout["buttons"])):
        return None
    return layout["buttons"][index]


def sidebar_flyout_hover_update(mouse_x, mouse_y):
    """Update the floating toolbar's highlighted icon from a window point."""
    if _sidebar_flyout_state is None:
        return False
    layout = _sidebar_flyout_state["layout"]
    index = tbar.hit_flyout(mouse_x, mouse_y, layout["cells"])
    if index == _sidebar_flyout_state["hover"]:
        return False
    _sidebar_flyout_state["hover"] = index
    return True


def _draw_flyout_tooltip(shader, region, state):
    index = state["hover"]
    layout = state["layout"]
    if index is None or not (0 <= index < len(layout["buttons"])):
        return
    button = layout["buttons"][index]
    paragraphs = tbar.sidebar_tooltip_lines(
        button, "command", _sidebar_command_name(button))
    width = 230.0
    pad = tbar.SIDEBAR_TOOLTIP_PAD
    line_h = tbar.SIDEBAR_TOOLTIP_LINE
    lines = []
    for paragraph in paragraphs:
        lines.extend(_sidebar_wrap_line(paragraph, width - pad * 2.0,
                                        tbar.SIDEBAR_TOOLTIP_FONT))
    lines = lines[:6]
    if not lines:
        return
    cell = layout["cells"][index]
    height = pad * 2.0 + len(lines) * line_h
    window = bpy.context.window
    x0 = layout["panel"][2] + 8.0
    if x0 + width > window.width:
        x0 = max(0.0, layout["panel"][0] - width - 8.0)
    top = min(float(window.height), cell[4])
    y0 = max(0.0, top - height)
    x0 -= region.x
    y0 -= region.y
    x1, y1 = x0 + width, y0 + height
    shader.bind()
    _draw_rect(shader, x0, y0, x1, y1, SIDEBAR_TOOLTIP_BG)
    _draw_rect(shader, x0, y0, x1, y0 + 1, SIDEBAR_TOOLTIP_BORDER)
    _draw_rect(shader, x0, y1 - 1, x1, y1, SIDEBAR_TOOLTIP_BORDER)
    _draw_rect(shader, x0, y0, x0 + 1, y1, SIDEBAR_TOOLTIP_BORDER)
    _draw_rect(shader, x1 - 1, y0, x1, y1, SIDEBAR_TOOLTIP_BORDER)
    text_y = y1 - pad - tbar.SIDEBAR_TOOLTIP_FONT
    for line_index, line in enumerate(lines):
        _draw_text(x0 + pad, text_y - line_index * line_h, line,
                   tbar.SIDEBAR_TOOLTIP_FONT, SIDEBAR_TOOLTIP_TEXT)


def draw_sidebar_flyout_overlay():
    """POST_PIXEL floating toolbar drawn over whichever viewports it intersects."""
    state = _sidebar_flyout_state
    context = bpy.context
    area = getattr(context, "area", None)
    region = getattr(context, "region", None)
    if state is None or area is None or region is None or area.type != "VIEW_3D":
        return
    panel = state["layout"]["panel"]
    region_box = (region.x, region.y, region.x + region.width, region.y + region.height)
    if (panel[2] <= region_box[0] or panel[0] >= region_box[2]
            or panel[3] <= region_box[1] or panel[1] >= region_box[3]):
        return
    try:
        import gpu
        from gpu_extras.batch import batch_for_shader
    except Exception:  # noqa: BLE001
        return
    shader = _uniform_color_shader()
    image_shader = _image_shader()
    if shader is None:
        return
    layout = state["layout"]
    px0, py0, px1, py1 = panel
    local_panel = (px0 - region.x, py0 - region.y, px1 - region.x, py1 - region.y)
    tx0, ty0, tx1, ty1 = layout["title"]
    local_title = (tx0 - region.x, ty0 - region.y, tx1 - region.x, ty1 - region.y)
    prev = gpu.state.blend_get()
    gpu.state.blend_set("ALPHA")
    try:
        shader.bind()
        _draw_rect(shader, *local_panel, views.gpu_rgba(views.OSNAP_PANEL_BG))
        _draw_rect(shader, *local_title, views.gpu_rgba(views.TAB_BAND_BG))
        _draw_rect(shader, local_panel[0], local_panel[1], local_panel[2], local_panel[1] + 1,
                   SIDEBAR_TOOLTIP_BORDER)
        _draw_rect(shader, local_panel[0], local_panel[3] - 1, local_panel[2], local_panel[3],
                   SIDEBAR_TOOLTIP_BORDER)
        _draw_rect(shader, local_panel[0], local_panel[1], local_panel[0] + 1, local_panel[3],
                   SIDEBAR_TOOLTIP_BORDER)
        _draw_rect(shader, local_panel[2] - 1, local_panel[1], local_panel[2], local_panel[3],
                   SIDEBAR_TOOLTIP_BORDER)
        for cell, button in zip(layout["cells"], layout["buttons"]):
            index, x0, y0, x1, y1 = cell
            x0, x1 = x0 - region.x, x1 - region.x
            y0, y1 = y0 - region.y, y1 - region.y
            if index == state["hover"]:
                shader.bind()
                _draw_rect(shader, x0, y0, x1, y1, SIDEBAR_HOVER_COLOR)
            tex = _sidebar_texture(button.icon)
            if tex is not None and image_shader is not None:
                inset = max(2.0, (tbar.FLYOUT_CELL - tbar.SIDEBAR_ICON) * 0.5)
                ix0, iy0, ix1, iy1 = x0 + inset, y0 + inset, x1 - inset, y1 - inset
                image_shader.bind()
                image_shader.uniform_sampler("image", tex)
                batch_for_shader(
                    image_shader, "TRI_FAN",
                    {"pos": ((ix0, iy0), (ix1, iy0), (ix1, iy1), (ix0, iy1)),
                     "texCoord": ((0, 0), (1, 0), (1, 1), (0, 1))},
                ).draw(image_shader)
            if button.link:
                shader.bind()
                _draw_tri(shader, ((x1 - 3, y0 + 3), (x1 - 3, y0 + 8),
                                   (x1 - 8, y0 + 3)), SIDEBAR_MARK_COLOR)
        _draw_flyout_tooltip(shader, region, state)
    finally:
        gpu.state.blend_set(prev)


def draw_left_band():
    """POST_PIXEL draw handler for the left dock band's WINDOW region.

    Registered on `SpaceInfo`, which the COMMAND BAR also uses, so the first thing
    this does is establish that it is running in the band and not in the bar. Without
    that it would paint over the command bar.

    Paints the band's background so Blender's stock Info editor cannot show through.
    It paints the Sidebar on top and the Osnap panel below. Unsupported furniture is
    omitted rather than drawn as a placeholder that implies behavior does not exist.
    """
    context = bpy.context
    area = getattr(context, "area", None)
    region = getattr(context, "region", None)
    if area is None or region is None or area.type != "INFO":
        return
    # which of the THREE INFO areas is this? wide-and-short is the command bar, and
    # the right dock band is equally tall and narrow, so the SIDE test is required --
    # with `_info_area_is_band` alone this painted the Sidebar into the right band.
    if _band_side(area) != "left":
        return
    if region.width < 4 or region.height < 4:
        return
    try:
        import gpu
    except Exception:  # noqa: BLE001
        return
    shader = _uniform_color_shader()
    if shader is None:
        return
    prev_blend = gpu.state.blend_get()
    gpu.state.blend_set("ALPHA")
    try:
        shader.bind()
        _draw_rect(shader, 0, 0, region.width, region.height,
                   views.gpu_rgba(views.OSNAP_PANEL_BG))
        # the band's own right-hand edge, so it reads as a dock and not as a gap
        _draw_rect(shader, region.width - 1, 0, region.width, region.height,
                   views.gpu_rgba(views.OSNAP_PANEL_BORDER))
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] left band draw failed: {e!r}")
    finally:
        gpu.state.blend_set(prev_blend)
    # The SIDEBAR is the band's upper bar. It draws in its own blend scope because the
    # Osnap panel is -- it binds a different shader (textured quads) and must not be
    # nested inside the flat-colour pass above.
    prev2 = gpu.state.blend_get()
    gpu.state.blend_set("ALPHA")
    try:
        _draw_left_sidebar(shader, region)
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] left sidebar draw failed: {e!r}")
    finally:
        gpu.state.blend_set(prev2)
    # The Osnap panel is the band's lower bar. It draws after the band's ground and outside the
    # try/finally above so its own blend handling is not nested inside ours.
    draw_osnap_panel()


def draw_osnap_panel():
    """Draw the Osnap panel. Runs inside the LEFT DOCK BAND's WINDOW region.

    This used to be a POST_PIXEL handler on VIEW_3D that floated over the bottom-left
    viewport. It now draws inside the dock band, matching the docked layout.

    The Osnap panel occupies the lower half of the left dock, so it is bottom-aligned.

    Called from `draw_left_band` rather than registered on its own: the band already
    has a handler establishing that it is the band and not the command bar, and two
    handlers on `SpaceInfo` both re-deriving that previously let `draw_command_bar`
    paint the dock band.

    The panel's geometry and its hit-test are UNCHANGED. `views.osnap_panel_boxes`
    takes WINDOW coordinates and `osnap_panel_at` tests in window space, so only the
    origin moves -- which is what made this a rehousing rather than a rewrite, and why
    the thirteen measured rows, their order and the colours all come through untouched.
    """
    global _osnap_panel_boxes, _osnap_panel_origin

    if not _osnap_panel_visible:
        _osnap_panel_boxes = []
        _osnap_panel_origin = None
        return
    context = bpy.context
    area = getattr(context, "area", None)
    region = getattr(context, "region", None)
    # The Osnap panel is the LEFT band's lower bar. The side test matters: the right
    # dock band is equally tall and narrow, so `_info_area_is_band` alone would have
    # let this paint thirteen snap rows into the panel container.
    if region is None or _band_side(area) != "left":
        return

    rows = _osnap_panel_row_state()
    if not rows:
        return
    # bottom-aligned in the band: the reference CAD application's OSnap bar is the band's LOWER half.
    x0 = region.x
    y_bottom = region.y
    # The Osnap panel uses the dock's live width.
    # The previous hard-coded 117 px left a dead strip whenever the divider widened.
    panel_w = max(0, region.width)
    boxes = views.osnap_panel_boxes([r[0] for r in rows], x0, y_bottom,
                                    width=panel_w)
    _osnap_panel_origin = (x0, y_bottom)
    _osnap_panel_boxes = boxes

    try:
        import gpu
    except Exception:  # noqa: BLE001
        return
    shader = _uniform_color_shader()
    if shader is None:
        return
    total = views.osnap_panel_height(len(rows))
    bg = views.gpu_rgba(views.OSNAP_PANEL_BG)
    border = views.gpu_rgba(views.OSNAP_PANEL_BORDER)
    box_bg = views.gpu_rgba(views.OSNAP_BOX_BG)
    tick = views.gpu_rgba(views.OSNAP_TICK)
    text_c = views.gpu_rgba(views.OSNAP_PANEL_TEXT)

    prev_blend = gpu.state.blend_get()
    gpu.state.blend_set("ALPHA")
    try:
        shader.bind()
        rx = x0 - region.x
        ry = y_bottom - region.y
        _draw_rect(shader, rx, ry, rx + panel_w, ry + total, bg)
        # right edge, so the panel reads as a docked column rather than a floating box
        _draw_rect(shader, rx + panel_w - 1, ry,
                   rx + panel_w, ry + total, border)
        _draw_text(rx + views.OSNAP_PAD, ry + total - views.OSNAP_TITLE_H + 5,
                   "Osnap", views.OSNAP_FONT_SIZE, text_c)
        for (label, on, implemented), (_l, bx0, by0, bx1, _by1) in zip(rows, boxes):
            cy = by0 - region.y + (views.OSNAP_ROW_H - views.OSNAP_BOX) // 2
            cx = bx0 - region.x + views.OSNAP_PAD
            _draw_rect(shader, cx, cy, cx + views.OSNAP_BOX,
                       cy + views.OSNAP_BOX, box_bg)
            _draw_rect(shader, cx, cy, cx + views.OSNAP_BOX, cy + 1, border)
            _draw_rect(shader, cx, cy + views.OSNAP_BOX - 1,
                       cx + views.OSNAP_BOX, cy + views.OSNAP_BOX, border)
            _draw_rect(shader, cx, cy, cx + 1, cy + views.OSNAP_BOX, border)
            _draw_rect(shader, cx + views.OSNAP_BOX - 1, cy,
                       cx + views.OSNAP_BOX, cy + views.OSNAP_BOX, border)
            if on:
                _draw_rect(shader, cx + 2, cy + 2, cx + views.OSNAP_BOX - 2,
                           cy + views.OSNAP_BOX - 2, tick)
            # a snap the reference CAD application offers that we do not COMPUTE keeps its row and is marked,
            # rather than being hidden -- the same rule the menus follow
            name = label if implemented else f"{label} *"
            _draw_text(cx + views.OSNAP_BOX + 6, by0 - region.y + 4,
                       name, views.OSNAP_FONT_SIZE, text_c)
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] osnap panel draw failed: {e!r}")
    finally:
        gpu.state.blend_set(prev_blend)


def osnap_panel_at(win_x, win_y):
    """The panel row at a WINDOW-space point, or None.

    Reads the boxes the draw recorded, so a click can only hit a row that was drawn.
    """
    if not _osnap_panel_visible or not _osnap_panel_boxes:
        return None
    return views.hit_osnap_panel_row(win_x, win_y, _osnap_panel_boxes)


def osnap_panel_click(label):
    """Toggle the row `label`. Returns the line to log, or None.

    `Project` and `Disable` are the settings object's own flags; everything else is a
    persistent snap mode. An unimplemented mode still TOGGLES -- the reference CAD application's checkbox
    does -- but says that we do not compute it, which is the honest half.
    """
    settings = snap_settings()
    if settings is None or not label:
        return None
    if label == "Disable":
        settings.disabled = not settings.disabled
        _tag_redraw_viewports()
        return f"Osnap is {'off' if settings.disabled else 'on'}"
    if label == "Project":
        settings.project = not settings.project
        _tag_redraw_viewports()
        return f"Project is {'on' if settings.project else 'off'}"
    name, now = settings.toggle(label)
    if name is None:
        return f"{label} is not an object snap"
    _tag_redraw_viewports()
    if now and name not in osnap.IMPLEMENTED:
        return f"{name} on -- but {name} is not computed yet"
    return f"{name} {'on' if now else 'off'}"


# ---------------------------------------------------------------------------
# command-line input (modal operator)
# ---------------------------------------------------------------------------

# Tracks whether the modal command-line operator is running, so we never stack
# two of them (WindowManager has no `modal_operators` collection to query).
_input_active = False


def is_input_active() -> bool:
    return _input_active


#: Point-pick commands use Blender's CROSSHAIR cursor. Blender cannot load a custom
#: cursor bitmap from Python. The last applied value is cached; None restores Blender's
#: default.
PICK_CURSOR = "CROSSHAIR"
_pick_cursor = None


def _wanted_pick_cursor(win_x, win_y):
    """The cursor for this pointer position: PICK_CURSOR over a viewport while the
    running command takes a point there, else None."""
    cmd = app_state.active_command
    if cmd is None or not _wants_point(cmd):
        return None
    return PICK_CURSOR if _point_in_view3d(win_x, win_y) else None


def _update_pick_cursor(window, event):
    """Set or restore the pick cursor for `event`. Returns the cursor now in force."""
    global _pick_cursor
    if window is None or not hasattr(event, "mouse_x"):
        return _pick_cursor
    try:
        want = _wanted_pick_cursor(event.mouse_x, event.mouse_y)
    except Exception:  # noqa: BLE001
        want = None
    if want == _pick_cursor:
        return _pick_cursor
    try:
        if want is None:
            window.cursor_modal_restore()
        else:
            window.cursor_modal_set(want)
        _pick_cursor = want
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] could not set the cursor: {e!r}")
    return _pick_cursor


def pick_cursor():
    """The cursor the app last set (None = Blender's own), for the harness."""
    return _pick_cursor


def _point_in_view3d(win_x, win_y):
    """True when a WINDOW-space point really is inside some VIEW_3D area.

    Deliberately separate from `_view3d_under_mouse`, which FALLS BACK to the first
    pane when nothing contains the point. That fallback is right for its callers (they
    want *a* pane to work with) and wrong as a test of ownership -- conflating the two
    is what made every click on the status bar look like a click in pane one.
    """
    screen = getattr(bpy.context, "screen", None)
    if screen is None:
        return False
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        if (area.x <= win_x < area.x + area.width
                and area.y <= win_y < area.y + area.height):
            return True
    return False


def _view3d_under_mouse(win_x: float, win_y: float):
    """(area, space, region) of the 3D viewport containing a WINDOW-space point.

    `mouse_region_*` coordinates are relative to the region the click
    happened in, so they cannot tell us *which* pane was clicked. `event.mouse_x
    / mouse_y` are WINDOW-space and can: we containment-test each VIEW_3D area's
    rectangle against them and return the one that contains the point.

    Blender's y grows UPWARD (origin bottom-left, ), so containment is simply
    `area.x <= win_x < area.x + width` and `area.y <= win_y < area.y + height`
    (no top-to-bottom reversal needed).

    Falls back to the first pane if nothing matches (e.g. the click is over the
    command bar), and to (None, None, None) when there is no viewport at all.
    """
    fallback = None
    for area in bpy.context.screen.areas:
        if area.type != "VIEW_3D":
            continue
        sp = next((s for s in area.spaces if s.type == "VIEW_3D"), None)
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        if sp is None or region is None:
            continue
        if fallback is None:
            fallback = (area, sp, region)
        if (area.x <= win_x < area.x + area.width
                and area.y <= win_y < area.y + area.height):
            return area, sp, region
    return fallback if fallback is not None else (None, None, None)


def pick_point(area, space, region, region_x: float, region_y: float,
               constraint=None):
    """Convert a click to a world point on THAT pane's own construction plane.

    (replaces the old z=0-only `pick_point_on_ground`): the reference CAD application picks on
    each viewport's own CPlane -- Top->World Top (XY), Front->World Front (XZ),
    Right->World Right (YZ), Perspective->World Top (the reference CAD application documentation). So a
    Front click yields a real z, and a pane whose view ray is parallel to the
    z=0 plane (Front/Right) no longer returns None.

    the plane comes from `get_cplane(area)`, so a CPlane the user has
    CHANGED is honoured here too -- clicking and typing can no longer disagree.

    Returns a WORLD point. Callers feeding it to a command must say so with
    `coords.format_world`, or it will be re-read as CPlane coordinates and
    transformed twice (the  bug).

    `region_x` / `region_y` are coordinates relative to `region` (as a click in
    that region reports). We build the ray with `region_2d_to_origin_3d` /
    `region_2d_to_vector_3d` for *this* region (the `depth_location` of
    `region_2d_to_location_3d` is unreliable -- see ARCHITECTURE.md), then do the
    ray/plane intersection by hand against the pane's CPlane
    (`cplane.ray_plane_intersect`).

    Returns an (x, y, z) tuple, or None if the view ray is parallel to / behind
    the plane.
    """
    if space is None or region is None:
        return None
    try:
        import bpy_extras.view3d_utils as v3u

        r3d = space.region_3d
        origin = v3u.region_2d_to_origin_3d(region, r3d, (region_x, region_y))
        direction = v3u.region_2d_to_vector_3d(region, r3d, (region_x, region_y))
    except Exception:  # noqa: BLE001
        return None
    # pick against the pane's ACTUAL CPlane, overrides included. This used
    # to read `CPLANE_BY_LABEL[label]` -- the STANDARD plane for the view -- so a
    # pane whose plane the `CPlane` command had changed still picked on its
    # original plane. Typed points already went through `get_cplane` (which
    # honours overrides), so clicking and typing disagreed: after
    # `CPlane Elevation 5` a typed point landed at z=5 and a click at z=0. Both
    # now use one source of truth. For the standard views this is identical --
    # `FRAME_BY_LABEL` and `CPLANE_BY_LABEL` describe the same three planes.
    # a HEIGHT/DISTANCE step measures along an AXIS, so a click answering it must be
    # projected onto that axis rather than flattened onto the CPlane. Verified: in a
    # Perspective pane the CPlane is world XY, so every click landed at z = 0 and a height
    # along Z was always 0 -- ExtrudeCrv did nothing there, and Cylinder/Cone shared the bug.
    # Falling back to the plane when the ray is PARALLEL to the axis is deliberate: a click
    # then carries no information about distance along it, so the old behaviour is no worse.
    if constraint is not None:
        axis_origin, axis_dir = constraint
        on_axis = cplane.ray_axis_point(
            (origin.x, origin.y, origin.z),
            (direction.x, direction.y, direction.z),
            axis_origin, axis_dir)
        if on_axis is not None:
            return on_axis
    origin_c, normal = get_cplane(area).as_origin_normal()
    return cplane.ray_plane_intersect(
        (origin.x, origin.y, origin.z),
        (direction.x, direction.y, direction.z),
        origin_c, normal,
    )


def geom_by_name_in_document(name):
    """The document geometry called `name`, or None. Mirrors `NarwhalContext.geom_by_name`.

    Module level because BOTH the preview and the axis-constraint lookup need it; it began as a
    local closure inside the preview draw and was promoted rather than copied.
    """
    try:
        model = app_state.model
        if model is None or not name:
            return None
        _exact, folded = _document_object_index()
        obj = folded.get(str(name).lower())
        return obj.Geometry if obj is not None else None
    except Exception:  # noqa: BLE001
        return None
    return None


def _command_axis_constraint(cmd, area):
    """The active command's axis constraint for its CURRENT step, or None ."""
    if cmd is None or area is None:
        return None
    fn = getattr(cmd, "axis_constraint", None)
    if not callable(fn):
        return None

    def resolve(name):
        if not name:
            chosen = sorted(selected_display_names())
            name = chosen[0] if chosen else None
        return geom_by_name_in_document(name)

    try:
        return fn(get_cplane(area), resolve)
    except Exception:  # noqa: BLE001
        # A constraint is an improvement, never a prerequisite: if it cannot be worked out the
        # click still lands on the CPlane as before.
        return None


def _command_hover(cmd, mouse_x, mouse_y, alt=False, shift=False):
    """A running command's point step follows the cursor: the snap marker and the
    live preview at window position (mouse_x, mouse_y), exactly where a click there
    would land (the click handler applies the same snap, Ortho and constraints).
    Called on every mouse move, and when Shift is pressed or released mid-hover --
    Shift toggles Ortho at once, without waiting for the mouse to move (#50/#51)."""
    global _constraint_cursor
    _last_hover[0] = (mouse_x, mouse_y)
    area, sp, region = _view3d_under_mouse(mouse_x, mouse_y)
    if region is not None:
        # the SAME constraint as the click handler, so the snap marker and the
        # preview show exactly where the pick will land
        pt = pick_point(area, sp, region,
                        mouse_x - region.x,
                        mouse_y - region.y,
                        constraint=_command_axis_constraint(cmd, area))
        # track the snap under the cursor so the marker can show
        # it BEFORE the click -- "moving the cursor near a specified
        # point on an object causes the marker to jump to that point".
        # The click handler reads this same value, so what is shown is
        # what will be picked.
        snapped = snap_at(area, sp, region, pt,
                          first_point=_snap_base(cmd) is None,
                          suspend=alt,
                          base=_snap_base(cmd))
        changed = set_active_snap(snapped,
                                  _pane_key(area) if area else None)
        # the preview should follow the SNAPPED point, or it would show
        # a shape the click will not produce -- and the Ortho-restricted
        # one when no snap is in range (the click does the same)
        effective = (snapped.point if snapped is not None
                     else apply_ortho(cmd, pt, area, shift))
        # remember the unconstrained marker, then constrain it
        _constraint_cursor = effective
        effective = constrain_pick(cmd, effective)
        # a size being TYPED shapes the preview too
        sized = typed_size_cursor(cmd, effective, area)
        if sized is not None:
            effective = sized
        if _tracks_cursor(cmd):
            if set_preview_cursor(
                    effective, _pane_key(area) if area else None):
                changed = True
        if changed:
            # The preview and marker show in every viewport, so
            # repaint all of them -- not just the pane under the cursor.
            _tag_redraw_viewports()


#: The window position `_command_hover` last ran at (for a Shift press/release).
_last_hover = [None]


def _wants_point(cmd) -> bool:
    """True if a viewport CLICK can answer the command's current prompt.

    Includes `DISTANCE` steps, not just `POINT` ones: the reference CAD application's radius/height
    prompts accept a picked location as well as a typed number, so a click must
    reach them (see `commands.DISTANCE`). The kind list is imported from
    `commands` so the modal and the commands cannot drift apart.

    This predicate also gates the live preview, so getting it wrong made the
    rubber band vanish after the first click of `Circle` as well as making the
    click itself do nothing.
    """
    if getattr(cmd, "variable_points", False):
        return True
    steps = getattr(cmd, "steps", [])
    idx = getattr(cmd, "step_index", 0)
    if idx < len(steps):
        return steps[idx][1] in commands.CLICKABLE_KINDS
    return False


class NARWHAL_OT_command_input(Operator):
    """Capture keyboard for the CAD-style command line."""
    bl_idname = "narwhal.command_input"
    bl_label = "Narwhal 3D Command Input"
    bl_description = "Type into the Narwhal 3D command bar (Enter submits, Esc cancels)"

    # When True, invoke preserves the existing buffer instead of clearing it.
    # ``narwhal.begin_typing`` sets this so the character it just seeded survives
    # the hand-off to the modal (which is where the rest of the typing lands).
    keep_buffer: BoolProperty(default=False)

    def invoke(self, context, event):
        global _input_active
        if not self.keep_buffer:
            app_state.input_buffer = ""
        _input_active = True
        # Without this the modal callback is never invoked at all.
        context.window_manager.modal_handler_add(self)
        redraw_cmdbar()
        return {"RUNNING_MODAL"}

    def _redraw(self):
        redraw_cmdbar()
        # the autocomplete list is drawn in the VIEWPORTS now, not in the bar,
        # so anything that changes what is typed or which row is highlighted has to
        # repaint them too. Without this the list went stale on every keystroke --
        # the bar redrew and the floating list did not.
        _tag_redraw_viewports()

    def _clear_transient(self):
        """Drop the overlays a cancelled command leaves behind.

        Split out of `_finish` because Esc now cancels WITHOUT ending the modal:
        the preview band and the snap marker must still go, but the command line
        keeps focus.
        """
        # a preview left behind would keep drawing a band for a command
        # that is no longer running.
        if clear_preview_cursor():
            _tag_redraw_viewports()
        # the same goes for a snap marker.
        if clear_active_snap():
            _tag_redraw_viewports()

    def _finish(self):
        global _input_active, _pick_cursor
        _input_active = False
        self._clear_transient()
        if _pick_cursor is not None:
            try:
                bpy.context.window.cursor_modal_restore()
            except Exception:  # noqa: BLE001
                pass
            _pick_cursor = None

    def modal(self, context, event):
        st = app_state
        # the cursor shows a crosshair while a command waits for a POINT
        _update_pick_cursor(getattr(context, "window", None), event)
        # Bug Report: while its text box has focus, the keyboard types the REPORT,
        # not commands (Enter is a new line; Ctrl+Enter submits; Ctrl+V pastes)
        # while a layer name is being edited in place, the keyboard edits
        # it (Enter accepts, Esc cancels, Tab / Insert accept and add a new layer)
        if layer_rename_editing() and event.value in ("PRESS", "REPEAT"):
            kmap = {"RET": "enter", "NUMPAD_ENTER": "enter", "ESC": "esc",
                    "TAB": "tab", "INSERT": "insert", "BACK_SPACE": "back",
                    "DEL": "delete", "LEFT_ARROW": "left", "RIGHT_ARROW": "right",
                    "HOME": "home", "END": "end"}
            kind, ch = kmap.get(event.type), ""
            if kind is None and event.ctrl and event.type in ("A", "V"):
                kind = "select_all" if event.type == "A" else "paste"
                ch = context.window_manager.clipboard or ""
            elif kind is None and event.ascii and event.ascii.isprintable() \
                    and not event.ctrl and not event.alt:
                kind, ch = "char", event.ascii
            if kind is not None:
                msg = layer_rename_key(kind, ch, shift=event.shift)
                if msg and msg != "handled":
                    st.log(msg)
                redraw_cmdbar()
                return {"RUNNING_MODAL"}
        # Right-click opens the layer context menu; F2 starts rename.
        if event.value == "PRESS" and event.type in ("RIGHTMOUSE", "F2"):
            lrow_ = layer_row_at(event.mouse_x, event.mouse_y)
            if lrow_ is not None and 0 <= lrow_[0] < len(_layer_row_objs):
                name_ = _layer_row_objs[lrow_[0]].name
                if event.type == "F2":
                    layer_rename_begin(name_)
                else:
                    show_layer_menu(name_)
                return {"RUNNING_MODAL"}
        # The floating sidebar toolbar overlays viewports, so its clicks must be
        # claimed before viewport selection/navigation sees them.
        if _sidebar_flyout_state is not None:
            if event.type in ("ESC", "RIGHTMOUSE") and event.value == "PRESS":
                sidebar_flyout_close()
                return {"RUNNING_MODAL"}
            if event.type == "LEFTMOUSE" and event.value == "PRESS":
                flyout_item = sidebar_flyout_action_at(event.mouse_x, event.mouse_y)
                inside = _inside_box(event.mouse_x, event.mouse_y,
                                     _sidebar_flyout_state["layout"]["panel"])
                if flyout_item is not None:
                    sidebar_flyout_close()
                    msg = sidebar_click(flyout_item)
                    if msg:
                        st.log(msg)
                    self._redraw()
                    return {"RUNNING_MODAL"}
                if inside:
                    return {"RUNNING_MODAL"}
                sidebar_flyout_close()
        # a drag that began in the report box selects text
        if bug_panel_dragging():
            if event.type == "MOUSEMOVE":
                bug_panel_drag(event.mouse_x, event.mouse_y)
                return {"RUNNING_MODAL"}
            if event.type == "LEFTMOUSE" and event.value == "RELEASE":
                bug_panel_release()
                return {"RUNNING_MODAL"}
        if _bug["state"].focus and bug_panel_open() and event.value in ("PRESS",
                                                                         "REPEAT"):
            kind, ch = None, ""
            motion = {"LEFT_ARROW": "left", "RIGHT_ARROW": "right", "UP_ARROW": "up",
                      "DOWN_ARROW": "down", "HOME": "home", "END": "end"}
            if event.type in ("RET", "NUMPAD_ENTER"):
                kind = "submit" if event.ctrl else "enter"
            elif event.type == "ESC":
                kind = "esc"
            elif event.type == "BACK_SPACE":
                kind = "back"
            elif event.type == "DEL":
                kind = "delete"
            elif event.type in motion:
                kind = motion[event.type]
            elif event.ctrl and event.type == "A":
                kind = "select_all"
            elif event.ctrl and event.type in ("C", "X"):
                kind = "copy" if event.type == "C" else "cut"
            elif event.type == "V" and event.ctrl:
                kind, ch = "paste", context.window_manager.clipboard or ""
            elif event.ascii and event.ascii.isprintable() and not event.ctrl \
                    and not event.alt:
                kind, ch = "char", event.ascii
            if kind is not None:
                bug_panel_key(kind, ch, shift=event.shift)
                if kind in ("copy", "cut"):
                    clip = _bug["state"].extra.get("clipboard")
                    if clip:
                        context.window_manager.clipboard = clip
                return {"RUNNING_MODAL"}
        # while the Print dialog is open it owns the mouse and Enter/Esc --
        # a click must not fall through and select or pick behind it
        if _print_dialog is not None:
            if event.type == "MOUSEMOVE":
                print_dialog_move(event.mouse_x, event.mouse_y)
                return {"PASS_THROUGH"}
            if event.type == "LEFTMOUSE" and event.value == "RELEASE":
                print_dialog_release()
            if event.value in ("PRESS", "REPEAT"):
                kmap = {"ESC": "esc", "RET": "enter", "NUMPAD_ENTER": "enter",
                        "TAB": "tab", "BACK_SPACE": "back", "DEL": "delete",
                        "LEFT_ARROW": "left", "RIGHT_ARROW": "right",
                        "HOME": "home", "END": "end"}
                kind = kmap.get(event.type)
                ch = event.ascii or ""
                if kind is None and event.ctrl:
                    kind = {"A": "select_all", "C": "copy", "X": "cut",
                            "V": "paste"}.get(event.type)
                    if kind == "paste":
                        ch = context.window_manager.clipboard or ""
                if kind is None and event.ascii and event.ascii.isprintable() \
                        and not event.ctrl and not event.alt:
                    kind = "char"
                if kind is not None:
                    did = print_dialog_key(kind, ch, shift=event.shift)
                    if kind in ("copy", "cut"):
                        clip = _print_dialog["state"].extra.get("clipboard")
                        if clip:
                            context.window_manager.clipboard = clip
                    if did is None and kind == "esc":
                        close_print_dialog()
                    elif did is None and kind == "enter":
                        print_dialog_print()
                    self._redraw()
                    return {"RUNNING_MODAL"}
                if event.type in ("LEFTMOUSE", "RIGHTMOUSE") and event.value == "PRESS":
                    step = -1 if event.type == "RIGHTMOUSE" else 1
                    did = print_dialog_click(event.mouse_x, event.mouse_y, step,
                                             shift=event.shift)
                    if did is not None:
                        self._redraw()
                        return {"RUNNING_MODAL"}
                if event.type in ("WHEELUPMOUSE", "WHEELDOWNMOUSE"):
                    region = _print_dialog_region()
                    if region is not None and printdialog.hit(
                            _print_dialog_layout(region),
                            event.mouse_x - region.x,
                            event.mouse_y - region.y) is not None:
                        return {"RUNNING_MODAL"}
        if event.type == "RET" and event.value == "PRESS":
            # The autocomplete list is up? Then Enter ACCEPTS the highlighted
            # row (the reference CAD application: "Press the Enter key or click the command name on the
            # auto-complete list" -- the reference CAD application documentation). The arrow
            # keys move that highlight, so Enter must take the row the user
            # pointed at, not always the top one. With no list the buffer is
            # submitted as a line, as before.
            if st.active_command is None:
                sel = _selected_completion(st)
                if sel is not None:
                    st.input_buffer = ""
                    st.history.append("> " + sel)
                    st.feed_line(sel)
                    self._redraw()
                    return {"RUNNING_MODAL"}
            line = st.input_buffer
            # Enter on an EMPTY line with no command running repeats the last
            # command -- the reference CAD application's documented Enter. This used to be the
            # `narwhal.repeat_last` keymap item, but now that this modal is always
            # running it consumes RET first, so the behaviour has to live here or
            # it would simply vanish.
            if not line.strip() and st.active_command is None:
                if st.last_command:
                    st.start_command(st.last_command)
                self._redraw()
                return {"RUNNING_MODAL"}
            st.input_buffer = ""
            if line.strip():
                st.history.append("> " + line)
            # "While typing the distance, pressing Shift + /Enter/
            # Space/Tab moves the object in the opposite direction"
            # (the reference CAD application documentation). Only the Gumball's numeric prompt reads it,
            # so no other command's Shift+Enter behaviour changes.
            if event.shift and gumball_numeric_active():
                st.active_command.reverse = True
            # a bare number / <angle at a point prompt is a pick CONSTRAINT,
            # and Enter with one armed places the point at the marker.
            if handle_constraint_line(line):
                self._redraw()
                return {"RUNNING_MODAL"}
            st.feed_line(line)
            self._redraw()
            # keep the input open for chained commands (the reference CAD application stays in command mode)
            return {"RUNNING_MODAL"}

        if event.type == "ESC" and event.value == "PRESS":
            if _trim_drag is not None:
                _trim_drag_end()
            # Verified (the reference CAD application keyboard documentation) Esc "Cancels a
            # command", "Clears the command prompt" and "Deselects all selected
            # objects". It does NOT take keyboard focus away from the command line
            # -- "When the the reference CAD application window is active, what you type will input to the
            # command-line" (the reference CAD application documentation) is unconditional.
            #
            # So this stays RUNNING_MODAL. Returning FINISHED is what used to make
            # every left-click behaviour dead until the user typed something: ALL
            # of it -- selection, the Gumball, control points, sub-objects, pane
            # activation, the title's double-click -- is handled in this modal, and
            # there is no LEFTMOUSE keymap item to restart it.
            st.cancel()
            clear_subobject_selection()
            clear_control_point_selection()
            self._clear_transient()
            self._redraw()
            return {"RUNNING_MODAL"}

        # Tab completes what you have typed to the best-matching command,
        # which is how the reference CAD application's autocomplete is accepted from the keyboard.
        # With the arrow keys in play (this task) Tab completes the HIGHLIGHTED
        # row, so the inline completion matches what the user pointed at; with
        # no list it falls back to the top-ranked match as before.
        if event.type == "TAB" and event.value == "PRESS":
            # during a point pick with nothing typed, Tab is the reference CAD application's direction
            # lock (the reference CAD application documentation), not autocomplete
            if st.active_command is not None and not st.input_buffer.strip():
                if constraint_tab() is not False:
                    self._redraw()
                    return {"RUNNING_MODAL"}
            if st.active_command is None and st.input_buffer.strip():
                sel = _selected_completion(st)
                if sel is None:
                    names = sorted(c.name for c in COMMANDS.values())
                    sel = cmdbar.autocomplete(st.input_buffer.strip(), names)
                if sel:
                    st.input_buffer = sel
                    self._redraw()
            return {"RUNNING_MODAL"}

        # a Gumball drag owns the mouse until the button comes up. Tested
        # before every other mouse branch so a drag cannot be interrupted by the
        # command bar or a point pick.
        if _gumball_drag is not None:
            # "Copy - Tap Alt -- While dragging, tap Alt to make a copy of the
            # object. Tap Alt again to leave copy mode." A TAP, so it is the key
            # PRESS that toggles, not the modifier being held.
            if event.type in ("LEFT_ALT", "RIGHT_ALT") and event.value == "PRESS":
                msg = _gumball_toggle_copy()
                if msg:
                    st.log(msg)
                    redraw_cmdbar()
                return {"RUNNING_MODAL"}
            # Alt supports both a tap-to-toggle and a momentary hold during a drag.
            #
            # A tap and a hold are indistinguishable at PRESS time, so the press above
            # always enters copy mode and the RELEASE decides what the gesture was --
            # the same shape `navigate.rmb_release_action` uses for the right button.
            # A hold is momentary (release leaves copy mode); a tap is a toggle
            # (release changes nothing).
            if event.type in ("LEFT_ALT", "RIGHT_ALT") and event.value == "RELEASE":
                msg = _gumball_alt_released()
                if msg:
                    st.log(msg)
                    redraw_cmdbar()
                return {"RUNNING_MODAL"}
            if event.type == "MOUSEMOVE":
                if _gumball_update(event.mouse_x, event.mouse_y,
                                   shift_held=bool(event.shift)):
                    _tag_redraw_viewports()
                return {"RUNNING_MODAL"}
            # pressing or releasing Shift mid-drag re-decides the mode
            # at once, without waiting for the mouse to move
            if event.type in ("LEFT_SHIFT", "RIGHT_SHIFT") and event.value in (
                    "PRESS", "RELEASE"):
                last = (_gumball_drag or {}).get("mouse_last")
                if last is not None and _gumball_update(
                        last[0], last[1], shift_held=(event.value == "PRESS")):
                    _tag_redraw_viewports()
                return {"RUNNING_MODAL"}
            if event.type == "LEFTMOUSE" and event.value == "RELEASE":
                msg = _gumball_end()
                if msg:
                    st.log(msg)
                _tag_redraw_viewports()
                self._redraw()
                return {"RUNNING_MODAL"}
            if event.type == "ESC" and event.value == "PRESS":
                # Abandon the drag. The document keeps what has been applied so
                # far -- there is no undo yet, and pretending to revert
                # would be a lie about state we cannot restore.
                _gumball_end()
                _tag_redraw_viewports()
                return {"RUNNING_MODAL"}

        # An ARMED RELOCATE owns the mouse too, and for longer than a drag does:
        # the reference CAD application's gesture deliberately survives the button coming up ("Move the
        # mouse and click"), so it cannot live inside the drag branch above.
        if _gumball_relocate is not None:
            if event.type == "MOUSEMOVE":
                if _gumball_relocate_motion(event.mouse_x, event.mouse_y):
                    _tag_redraw_viewports()
                return {"RUNNING_MODAL"}
            if event.type == "LEFTMOUSE" and event.value == "PRESS":
                msg = _gumball_relocate_commit()
                if msg:
                    st.log(msg)
                _tag_redraw_viewports()
                self._redraw()
                return {"RUNNING_MODAL"}
            if event.type == "LEFTMOUSE" and event.value == "RELEASE":
                # Either the release of the double-click that armed the gesture or
                # the release of the committing press. Neither means anything here,
                # and letting either fall through would reach the selection path
                # and deselect the very object being relocated around.
                return {"RUNNING_MODAL"}
            if (event.type in ("ESC", "RIGHTMOUSE")
                    and event.value == "PRESS"):
                # Abandoning puts the widget back: unlike a transform drag, nothing
                # has entered the document, so a true revert IS possible here.
                # RIGHTMOUSE is included because it would otherwise fall through to
                # "repeat the last command" in the middle of a gesture.
                if _gumball_relocate_cancel():
                    st.log("Relocate cancelled")
                    _tag_redraw_viewports()
                    self._redraw()
                return {"RUNNING_MODAL"}

        # A control-point drag owns the mouse the same way, and for the same
        # reason: it must not be interrupted by the command bar or a point pick.
        if _cp_drag is not None:
            if event.type == "MOUSEMOVE":
                if _cp_drag_update(event.mouse_x, event.mouse_y):
                    _tag_redraw_viewports()
                return {"RUNNING_MODAL"}
            if event.type == "LEFTMOUSE" and event.value == "RELEASE":
                msg = _cp_drag_end()
                if msg:
                    st.log(msg)
                _tag_redraw_viewports()
                self._redraw()
                return {"RUNNING_MODAL"}
            if event.type == "ESC" and event.value == "PRESS":
                # Undo exists now, so Esc can genuinely revert the drag
                # rather than leaving it half-applied.
                _cp_drag_end()
                name = app_state.ctx.undo_last()
                if name:
                    st.log(f"{name} cancelled")
                _tag_redraw_viewports()
                self._redraw()
                return {"RUNNING_MODAL"}

        # Trim owns a held left button after its normal first pick. Mouse movement
        # sweeps the existing curve picker; release merely closes the gesture.
        if _trim_drag is not None:
            if event.type == "MOUSEMOVE":
                if _trim_drag_update(event.mouse_x, event.mouse_y):
                    _tag_redraw_viewports()
                    self._redraw()
                return {"RUNNING_MODAL"}
            if event.type == "LEFTMOUSE" and event.value == "RELEASE":
                _trim_drag_end(event.mouse_x, event.mouse_y)
                _tag_redraw_viewports()
                self._redraw()
                return {"RUNNING_MODAL"}

        # A selection drag (window / crossing) likewise owns the mouse until the
        # button comes up, because only the release tells us whether it was a drag
        # at all.
        if _sel_drag is not None:
            if event.type == "MOUSEMOVE":
                _sel_drag_update(event.mouse_x, event.mouse_y)
                _tag_redraw_viewports()      # the rubber band follows the cursor
                return {"RUNNING_MODAL"}
            if event.type == "LEFTMOUSE" and event.value == "RELEASE":
                sel, mode = _sel_drag_end()
                if mode is not None:
                    n = len(sel or [])
                    st.log(f"{mode.capitalize()} selection: "
                           f"{n} object{'s' if n != 1 else ''}")
                _tag_redraw_viewports()
                self._redraw()
                return {"RUNNING_MODAL"}
            if event.type == "ESC" and event.value == "PRESS":
                # abandon the rectangle without changing the selection
                globals()["_sel_drag"] = None
                _tag_redraw_viewports()
                return {"RUNNING_MODAL"}

        # A MENU armed by a left PRESS opens on the RELEASE. See `_defer_menu` for
        # why it cannot open on the press itself. Placed after every drag branch
        # above (each of which is gated on its own in-progress state, so none can be
        # active at the same time as a pending menu) and before the press handlers
        # below, so nothing else consumes the release first.
        if (_pending_menu is not None and event.type == "LEFTMOUSE"
                and event.value == "RELEASE"):
            _flush_pending_menu()
            if sidebar_press_clear():
                _tag_redraw_bands()
            return {"RUNNING_MODAL"}

        # WHEEL over the TAB STRIP cycles viewports. Verified: "Scroll the mouse
        # wheel when it is over the tabs to cycle through the tabs." Everywhere else
        # the wheel must still reach Blender's zoom, so this is the narrowest
        # possible interception -- only when the pointer is actually on the strip,
        # and it passes through otherwise.
        #
        # application-defined: which way is "forward". The help says "cycle" and names no direction;
        # scrolling down advances, matching how the same gesture moves down a list.
        if event.type in ("WHEELUPMOUSE", "WHEELDOWNMOUSE"):
            if viewport_tab_at(event.mouse_x, event.mouse_y) is not None:
                msg = viewport_tab_wheel(
                    1 if event.type == "WHEELDOWNMOUSE" else -1)
                if msg:
                    st.log(msg)
                    redraw_cmdbar()
                self._redraw()
                return {"RUNNING_MODAL"}
            _note_view_wheel(False)
            return {"PASS_THROUGH"}

        # a click on the COMMAND BAR is a click on a button. the reference CAD application's
        # bracketed options are clickable, and so is its autocomplete list, so
        # this must be tested BEFORE the viewport branch (the bar is not a
        # viewport, so that branch would only pass the click through to Blender).
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            handled = _click_command_bar(event.mouse_x, event.mouse_y)
            if handled is not None:
                self._redraw()
                return {"RUNNING_MODAL"}

        # Left-click in the viewport supplies a point when the command is asking
        # for one. The two input paths coexist exactly like the reference CAD application: typing
        # still works, and clicking feeds the picked coordinate as if typed.
        # find the pane under the mouse from the WINDOW coords, then pick
        # on THAT pane's own construction plane (a Front/Right click now yields
        # a real z instead of None).
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            cmd = st.active_command
            # Status-bar clicks must be handled before viewport fallback. The trap is
            # in `_view3d_under_mouse`, which falls back to
            # the first pane when the point is in no viewport at all. So a click on the
            # status bar (or the toolbar, or anywhere else that is not a pane) arrived
            # here as a click in pane one, and the branches below consumed it with
            # RUNNING_MODAL -- and a modal operator gets events BEFORE region UI
            # handlers, so the status-bar button never saw the press.
            #
            # The fallback is deliberate for its other callers (a point pick started in
            # a pane, say), so it stays; what was missing is this guard. A click that is
            # in NO viewport is not ours, and must pass through to whatever editor owns
            # those pixels.
            #
            # The docked Osnap panel is outside VIEW_3D. It used to be drawn inside the
            # bottom-left viewport, so it was always on the
            # viewport side of this guard. Now it lives in the left dock band, which is
            # not a viewport -- so this guard passed its clicks straight through to
            # Blender and the checkboxes went dead. The panel has to be claimed BEFORE
            # the guard, exactly as the command bar is above.
            #
            # My gate did not catch it, and the reason is worth recording: it called
            # `osnap_panel_at` + `osnap_panel_click` directly, so it tested the panel's
            # own logic and not the route a mouse actually takes. That is the third time
            # this project has been bitten by a gate that drives a helper instead of the
            # production path.
            osnap_row = osnap_panel_at(event.mouse_x, event.mouse_y)
            if osnap_row is not None:
                msg = osnap_panel_click(osnap_row)
                if msg:
                    st.log(msg)
                _tag_redraw_viewports()
                self._redraw()
                return {"RUNNING_MODAL"}
            # the SIDEBAR, in the band's upper half. Claimed here for
            # exactly the same reason as the Osnap panel -- it is chrome in the dock
            # band, which is NOT a viewport, so the guard below would pass its clicks
            # through to Blender.
            side_action = sidebar_action_at(event.mouse_x, event.mouse_y)
            if side_action is not None:
                side_index, side_btn, part = side_action
                if part == "flyout":
                    # the reference CAD application opens a linked toolbar by clicking the black triangle.
                    # Defer to RELEASE so Blender treats this as a persistent popup,
                    # not a press-drag-release hold menu.
                    sidebar_press_begin(side_index, part)
                    _tag_redraw_bands()
                    _defer_menu(lambda b=side_btn, i=side_index: show_sidebar_flyout(b, i))
                else:
                    msg = sidebar_click(side_btn)
                    if msg:
                        st.log(msg)
                    redraw_cmdbar()
                self._redraw()
                return {"RUNNING_MODAL"}
            # the RIGHT dock band's tab strip. Claimed here for the third time
            # for the same reason -- the band is not a viewport, so the guard below
            # would pass these clicks to Blender and leave the tabs as dead chrome.
            tab_i = right_tab_at(event.mouse_x, event.mouse_y)
            if tab_i is not None:
                msg = set_right_tab(tab_i)
                if msg:
                    st.log(msg)
                redraw_cmdbar()
                self._redraw()
                return {"RUNNING_MODAL"}
            # a row in the Layers list. Claimed here for the same
            # reason as the tab strip and the Osnap panel before it -- the band is
            # not a viewport, so the guard below would pass these clicks to Blender
            # and the list would be dead chrome.
            if bug_panel_click(event.mouse_x, event.mouse_y,
                               shift=event.shift) is not None:
                self._redraw()
                return {"RUNNING_MODAL"}
            display_hit = display_panel_hit(event.mouse_x, event.mouse_y)
            if display_hit is not None:
                msg = display_panel_click(*display_hit)
                if msg:
                    st.log(msg)
                redraw_cmdbar()
                self._redraw()
                return {"RUNNING_MODAL"}
            if properties_details_button_at(event.mouse_x, event.mouse_y):
                msg = properties_details()
                if msg:
                    st.log(msg)
                _tag_redraw_bands()
                self._redraw()
                return {"RUNNING_MODAL"}
            if layer_new_button_at(event.mouse_x, event.mouse_y):
                from . import layer_commands

                st.ctx.begin_undo("Layer")
                try:
                    st.log(layer_commands.new_layer()[1])
                finally:
                    st.ctx.end_undo()
                _tag_redraw_bands()
                redraw_cmdbar()
                self._redraw()
                return {"RUNNING_MODAL"}
            lrow = layer_row_at(event.mouse_x, event.mouse_y)
            if lrow is None and layer_rename_editing():
                msg = layer_rename_commit()           # a click elsewhere accepts it
                if msg:
                    st.log(msg)
            if lrow is not None:
                msg = layer_panel_press(*lrow)
                if msg:
                    st.log(msg)
                redraw_cmdbar()
                self._redraw()
                return {"RUNNING_MODAL"}
            if not _point_in_view3d(event.mouse_x, event.mouse_y):
                return {"PASS_THROUGH"}
            area, sp, region = _view3d_under_mouse(event.mouse_x, event.mouse_y)
            # clicking in a pane makes it the ACTIVE viewport, which is what
            # decides the CPlane a typed point is read in. Do this even when no
            # command is running, so the highlight always tracks where you work.
            if set_active_pane(area):
                _tag_redraw_viewports()
            if cmd is not None and _wants_point(cmd):
                if region is not None:
                    rx = event.mouse_x - region.x
                    ry = event.mouse_y - region.y
                    pt = pick_point(area, sp, region, rx, ry,
                                    constraint=_command_axis_constraint(cmd, area))
                    if pt is not None:
                        trim_drag_latched = (_trim_drag_command(cmd)
                                             and pick_curve_object(
                                                 area, sp, region, rx, ry) is not None)
                        # if a snap is in range, THAT is the point. the reference CAD application:
                        # "the object snaps constrain the marker to an exact
                        # location on an object". Alt suspends snapping for this
                        # pick, per the reference CAD application documentation.
                        snapped = snap_at(area, sp, region, pt,
                                          first_point=_snap_base(cmd) is None,
                                          suspend=event.alt,
                                          base=_snap_base(cmd))
                        if snapped is not None:
                            pt = snapped.point
                        else:
                            # Shift (or the Ortho mode) restricts the
                            # pick to ortho directions. Inferred: an object snap in
                            # range wins -- it is an exact location on an object.
                            pt = apply_ortho(cmd, pt, area, event.shift)
                        # the armed distance/angle/Tab constraint has the
                        # last word -- it is what the marker showed. A length still
                        # being TYPED counts too, and the click takes it with it.
                        typed_taken = pick_consumes_typed(cmd)
                        pt = constrain_pick(cmd, pt)
                        if typed_taken:
                            st.input_buffer = ""
                        # a pick is ALREADY a world point, so it must be fed
                        # as one. Bare text is read in the active CPlane (correct
                        # for something the user typed), which transformed the
                        # point a second time: a click at world (5,0,3) in Front
                        # became (5,-3,0), dropping the centre onto z=0. Top's CPlane is
                        # world XY, so the double transform was the identity there
                        # and the bug only showed in Front/Right.
                        text = coords.format_world(pt)
                        st.history.append("> " + text)
                        st.feed_line(text)
                        if _trim_drag_command(st.active_command):
                            _trim_drag_begin(
                                area, sp, region, rx, ry,
                                latched=trim_drag_latched)
                        if clear_active_snap():
                            _tag_redraw_viewports()
                        self._redraw()
                        return {"RUNNING_MODAL"}
            # THE FLOATING AUTOCOMPLETE LIST wins over everything in the viewport
            #. It is chrome hanging over the scene, and the reference CAD application's list is
            # clickable: "Press the Enter key or click the command name on the
            # auto-complete list." Tested before the tab strip and the pane title
            # because it is drawn on top of both.
            if region is not None:
                pick = completion_overlay_at(event.mouse_x, event.mouse_y)
                if pick is not None:
                    st.input_buffer = ""
                    st.history.append("> " + pick)
                    st.feed_line(pick)
                    _set_completion_index(0)
                    _tag_redraw_viewports()
                    self._redraw()
                    return {"RUNNING_MODAL"}
                # a click INSIDE the list's box but on the blank divider, or on the
                # box's padding, must not fall through into the scene and deselect
                if _completion_overlay_strip is not None:
                    ox, oy_top, ow, _c = _completion_overlay_strip
                    rows = _completion_overlay_boxes
                    low = rows[-1][1] if rows else oy_top
                    if (ox <= event.mouse_x < ox + ow
                            and low <= event.mouse_y < oy_top):
                        return {"RUNNING_MODAL"}
            # THE OSNAP PANEL is claimed EARLIER now -- above the `_point_in_view3d`
            # guard -- because moved it out of the viewports and into the left dock
            # band. The duplicate test that used to live here would never fire (the
            # earlier one always wins) so it is gone rather than left to rot.
            # THE TAB STRIP wins over everything below it, for the same reason the
            # PANE TITLE does: it is chrome sitting on top of the scene, and the
            # bottom edge of a pane is both. Tested before the title so a strip that
            # overlaps nothing else cannot be shadowed.
            if region is not None:
                tab = viewport_tab_at(event.mouse_x, event.mouse_y)
                if tab is not None:
                    msg = viewport_tab_click(tab)
                    if msg:
                        st.log(msg)
                        redraw_cmdbar()
                    self._redraw()
                    return {"RUNNING_MODAL"}
            # THE PANE TITLE wins over everything below, because it is CHROME
            # sitting on top of the scene: the Gumball or an object can be under
            # the same pixels, and in the reference CAD application clicking the title does the title's
            # job. Verified: "Click the title of a viewport to activate the
            # viewport WITHOUT DESELECTING objects" -- so it must never reach
            # `narwhal_click_select`, which deselects by design -- and "Double-click
            # a viewport title to maximize the viewport, or restore the
            # multi-viewport layout".
            #
            # Only PRESS gets here (the enclosing condition filters on it), so
            # Blender's own DOUBLE_CLICK event value never arrives and
            # `title_click`'s timing is the only double-click detector. That is
            # deliberate: acting on both would toggle maximize twice.
            if (st.active_command is None and region is not None
                    and pane_title_at(event.mouse_x, event.mouse_y) is not None):
                if title_click(area, sp, region,
                               event.mouse_x, event.mouse_y) == "activate":
                    st.log(f"Activated the {pane_label(area)} viewport")
                    redraw_cmdbar()
                self._redraw()
                return {"RUNNING_MODAL"}
            # The title's drop-down arrow opens its menu on a left click. Test it after
            # the title chip and before
            # everything else: the arrow sits just outside the chip so the two rects
            # cannot both match, but it is still chrome and must win over the scene.
            # The menu is, unchanged -- this is the affordance, not a second
            # menu. Activating the pane first matches what clicking the title does,
            # so the menu always acts on the pane you aimed at.
            if (st.active_command is None and region is not None
                    and pane_title_arrow_at(event.mouse_x, event.mouse_y)
                    is not None):
                if set_active_pane(area):
                    _tag_redraw_viewports()
                # ARMED, not opened: a menu raised while the button is still down
                # becomes a hold-menu. See `_pending_menu`.
                _defer_menu(lambda a=area: show_caption_menu(a))
                return {"RUNNING_MODAL"}
            # DOUBLE-CLICK on a move arrow or a Move-2D handle RELOCATES the
            # widget. Double-clicking either an arrow or Move-2D handle begins the same
            # relocation feature. Tested
            # BEFORE `_gumball_begin` so the second press of the pair cannot start
            # an ordinary transform drag.
            #
            # The FIRST press of the pair has already installed the numeric-entry
            # prompt (clicking a handle without moving means "type a value"), so
            # that prompt is the one command this path may displace -- otherwise the
            # double-click could never be completed at all.
            if region is not None and sp is not None and (
                    st.active_command is None or gumball_numeric_active()):
                _dc_hit = _gumball_hit(area, sp, region,
                                       event.mouse_x, event.mouse_y)
                if _gumball_is_double_click(
                        _dc_hit.key if _dc_hit is not None else None):
                    msg = _gumball_relocate_begin(area, sp, region,
                                                  event.mouse_x, event.mouse_y)
                    if msg:
                        if gumball_numeric_active():
                            st.active_command = None
                            st.input_buffer = ""
                        st.log(msg)
                        redraw_cmdbar()
                        _tag_redraw_viewports()
                        return {"RUNNING_MODAL"}
            # not a point prompt (or no command) -> the Gumball gets first refusal
            # on the click, then the viewport orbits. : pressing on a handle
            # starts a transform drag; anywhere else falls through to Blender's
            # own click-to-select, which is how the selection the widget follows
            # gets made in the first place.
            if st.active_command is None and _gumball_begin(
                    area, sp, region, event.mouse_x, event.mouse_y,
                    shift=event.shift, ctrl=event.ctrl):
                # Alt already held at mouse-down starts the drag in copy mode.
                # click on any handle -- copy with numeric input"). Its release is
                # then a HOLD and ends copy mode, as tap/hold rule says.
                if event.alt and _gumball_drag is not None:
                    msg = _gumball_toggle_copy()
                    if msg:
                        st.log(msg)
                        redraw_cmdbar()
                _tag_redraw_viewports()
                return {"RUNNING_MODAL"}
            # Nothing else wanted the click, so it is a SELECTION click. the reference CAD application's
            # rules, not Blender's: a plain click selects what it hit and clicking
            # away deselects, Shift ADDS, Ctrl REMOVES
            # (the reference CAD application documentation). Passing through to Blender used to
            # be the behaviour here and it was wrong on all three counts -- Shift
            # toggles in Blender and Ctrl does something else entirely.
            if st.active_command is None and region is not None:
                rx = event.mouse_x - region.x
                ry = event.mouse_y - region.y
                # SUB-OBJECT selection gets first refusal, because its chord is
                # unambiguous. Verified (the reference CAD application keyboard documentation):
                # "Ctrl (CMD) + Shift + Left mouse click" both selects AND
                # deselects a sub-object part, so it toggles. It is checked before
                # anything else so the combination can never fall through to the
                # object rules, where Ctrl means REMOVE.
                if subobject.is_subobject_chord(shift=event.shift,
                                                ctrl=event.ctrl):
                    _sel, msg = subobject_click(area, sp, region, rx, ry)
                    if msg:
                        st.log(msg)
                    self._redraw()
                    return {"RUNNING_MODAL"}
                # Clicking empty space deselects sub-objects. A plain
                # click -- on empty space or on another object -- drops the sub-object
                # selection, then selects as usual. Shift/Ctrl clicks edit the OBJECT
                # selection and leave sub-objects alone.
                if not (event.shift or event.ctrl) and _selected_subobjects:
                    clear_subobject_selection()
                    _tag_redraw_viewports()
                # Control points get FIRST refusal when they are shown: they are
                # drawn on top of the curve that owns them, so a click near one
                # means the grip, not the object underneath.
                cp_hit = pick_control_point(area, sp, region, rx, ry)
                if cp_hit is not None or selected_control_points():
                    if cp_hit is not None:
                        select_control_point(cp_hit, shift=event.shift,
                                             ctrl=event.ctrl)
                        if _cp_drag_begin(area, sp, region, rx, ry):
                            self._redraw()
                            return {"RUNNING_MODAL"}
                    elif not (event.shift or event.ctrl):
                        # clicking away clears the grip selection, as it does for
                        # objects
                        clear_control_point_selection()
                    self._redraw()
                    return {"RUNNING_MODAL"}
                # A click and a window/crossing selection START IDENTICALLY -- you
                # cannot tell them apart until the button comes up -- so the press
                # only RECORDS, and the decision is made in _sel_drag_end on
                # release. That is also why the reference CAD application cannot decide any earlier.
                _sel_drag_begin(area, region, rx, ry,
                                shift=event.shift, ctrl=event.ctrl)
                self._redraw()
                return {"RUNNING_MODAL"}
            # a command waiting at an OBJECT prompt takes the click through OUR
            # selection rules too. It used to pass through to Blender's own select, so
            # the pick location was lost and the step never advanced on the click.
            if (st.active_command is not None and region is not None
                    and _at_selection_step(st.active_command)
                    and subobject.is_subobject_chord(shift=event.shift, ctrl=event.ctrl)):
                # Ctrl+Shift picks a sub-object at a selection prompt; a command that
                # takes sub-objects (`accepts_subobjects`) is then answered at once.
                _sel, msg = subobject_click(area, sp, region, event.mouse_x - region.x,
                                            event.mouse_y - region.y)
                if msg:
                    st.log(msg)
                if (_sel and getattr(st.active_command, "accepts_subobjects", False)):
                    st.feed_line("")
                self._redraw()
                return {"RUNNING_MODAL"}
            if (st.active_command is not None and region is not None
                    and _at_selection_step(st.active_command)):
                _sel_drag_begin(area, region, event.mouse_x - region.x,
                                event.mouse_y - region.y,
                                shift=event.shift, ctrl=event.ctrl)
                self._redraw()
                return {"RUNNING_MODAL"}
            return {"PASS_THROUGH"}

        # NOTE: Blender's event-type identifiers are BACK_SPACE and *_ARROW, not
        # BACKSPACE/UP/DOWN/LEFT/RIGHT. All five were wrong here, so every one of
        # these branches was dead code and line editing never worked. The names are
        # asserted against bpy.types.Event's real enum by the headless UI checks so a
        # typo like this cannot silently return.
        # #50/#51: Shift toggles Ortho while held -- the marker and preview of a point
        # step re-decide at once, without waiting for the mouse to move (the Gumball's
        # Shift does the same mid-drag). Never consumed: Shift is also a modifier.
        if (event.type in ("LEFT_SHIFT", "RIGHT_SHIFT")
                and event.value in ("PRESS", "RELEASE")
                and st.active_command is not None and _wants_point(st.active_command)
                and _last_hover[0] is not None):
            _command_hover(st.active_command, *_last_hover[0], alt=event.alt,
                           shift=(event.value == "PRESS"))
            return {"PASS_THROUGH"}

        if event.type == "MOUSEMOVE":
            # The status bar's x/y/z readout follows the cursor whether or not a
            # command is running -- "The x/y/z coordinates of the mouse cursor
            # location" is unconditional in the help. Tracked here because this is
            # the only place we see the mouse. Outside a viewport the readout is
            # cleared rather than frozen at the last value, which would claim the
            # cursor is somewhere it is not.
            # When this modal is running it sits ABOVE the always-on tracker and
            # consumes MOUSEMOVE, so it must do the tracking itself -- same
            # function, so the readout cannot behave differently in the two cases.
            _track_cursor(event.mouse_x, event.mouse_y)
            # Grey the sidebar button under the
            # pointer, including buttons marked with a flyout/second-command arrow.
            sidebar_hover_redraw_update(event.mouse_x, event.mouse_y)
            if sidebar_flyout_hover_update(event.mouse_x, event.mouse_y):
                _tag_redraw_viewports()
            # reopened #20: highlight what an object/edge/face pick would take
            if _update_hover_pick(event.mouse_x, event.mouse_y):
                _tag_redraw_viewports()

            # keep the dynamic preview following the cursor. Only worth any
            # work while a command is actually asking for a point that previews.
            cmd = st.active_command
            if cmd is not None and _wants_point(cmd):
                _command_hover(cmd, event.mouse_x, event.mouse_y, alt=event.alt,
                               shift=event.shift)
            else:
                if clear_active_snap():
                    _tag_redraw_viewports()
                if _preview_cursor is not None:
                    clear_preview_cursor()
                    _tag_redraw_viewports()
                # with no command running, highlight the Gumball handle
                # under the cursor. the reference CAD application highlights a handle on hover ("Point the
                # mouse cursor to a handle to show the tooltip for actions"), and
                # it is also the only feedback that a handle is grabbable at all.
                if _gumball_enabled:
                    g_area, g_sp, g_region = _view3d_under_mouse(
                        event.mouse_x, event.mouse_y)
                    if _gumball_hover_update(g_area, g_sp, g_region,
                                             event.mouse_x, event.mouse_y):
                        _tag_redraw_viewports()
            # never consume a move: navigation and Blender's own hover need it
            return {"PASS_THROUGH"}

        if event.type in ("LEFT_ARROW", "RIGHT_ARROW") and event.value == "PRESS":
            # MVP: caret stays at end; arrows are no-ops but consumed
            return {"RUNNING_MODAL"}

        if event.type == "UP_ARROW" and event.value == "PRESS":
            if st.active_command is None and _completion_rows_now(st):
                # move the highlight UP the autocomplete list (the user's side
                # task). Clamped at the top so the highlight never leaves the
                # list, and stepping OVER the fuzzy divider rather than onto it.
                _rows = _completion_rows_now(st)
                _here = _completion_selectable_index(_rows, _completion_index)
                _set_completion_index(_completion_step(_rows, _here, -1))
                self._redraw()
                return {"RUNNING_MODAL"}
            # no list to navigate: recall last command from history (the reference CAD application)
            prev = [l for l in st.history if l.startswith("> ")]
            if prev:
                st.input_buffer = prev[-1][2:]
                _set_completion_index(0)
                self._redraw()
            return {"RUNNING_MODAL"}

        if event.type == "DOWN_ARROW" and event.value == "PRESS":
            if st.active_command is None and _completion_rows_now(st):
                # move the highlight DOWN the autocomplete list, clamped at the
                # bottom of the drawn rows and stepping over the fuzzy divider.
                _rows = _completion_rows_now(st)
                _here = _completion_selectable_index(_rows, _completion_index)
                _set_completion_index(_completion_step(_rows, _here, 1))
                self._redraw()
                return {"RUNNING_MODAL"}
            # no list to navigate: clear the buffer (existing behavior)
            st.input_buffer = ""
            _set_completion_index(0)
            self._redraw()
            return {"RUNNING_MODAL"}

        if event.type == "BACK_SPACE" and event.value == "PRESS":
            st.input_buffer = st.input_buffer[:-1]
            # a new prefix restarts the candidate list at its top row
            _set_completion_index(0)
            typed_changed()
            self._redraw()
            return {"RUNNING_MODAL"}

        # the reference CAD application's documented undo/redo shortcuts -- Ctrl+Z for Undo and
        # Ctrl+Y for Redo (the reference CAD application documentation lists "Ctrl (CMD) + Z" and
        # "Ctrl (CMD) + Y" against Edit > Undo / Edit > Redo). Handled here,
        # before the printable-character branch, so the key never reaches
        # Blender's own undo -- which knows nothing about the .3dm document and
        # would desynchronise the two.
        if event.value == "PRESS" and event.ctrl and event.type in ("Z", "Y"):
            # Shift+Ctrl+Z is Blender's redo; the reference CAD application uses Ctrl+Y, so treat both as
            # redo rather than letting the combination fall through.
            want_redo = event.type == "Y" or event.shift
            if st.active_command is not None:
                # the reference CAD application does not undo the document from inside a running command;
                # say so rather than silently doing something else.
                st.log(f"{st.active_command.name} is running -- press Esc first")
            else:
                st.start_command("Redo" if want_redo else "Undo")
            self._redraw()
            return {"RUNNING_MODAL"}

        # printable characters (ignore modifiers other than shift)
        if (event.value == "PRESS" and event.ascii and event.ascii.isprintable()
                and not event.ctrl and not event.alt):
            st.input_buffer += event.ascii
            # a new prefix restarts the candidate list at its top row
            _set_completion_index(0)
            typed_changed()
            self._redraw()
            return {"RUNNING_MODAL"}

        # let navigation (orbit/pan/zoom) reach the viewport
        return {"PASS_THROUGH"}

    def cancel(self, context):
        self._finish()
        app_state.cancel()
        redraw_cmdbar()


class NARWHAL_OT_begin_typing(Operator):
    """Seed one character into the command buffer and open the command line.

    This is the entry point for "just start typing" — the defining the reference CAD application
    interaction. Each letter key in the 3D View is bound to this
    operator with its character in ``char``. It appends the character to the
    command buffer, then hands off to the command-line modal (``narwhal.command_
    input``), telling it to keep the buffer (``keep_buffer``) so the seed
    survives. From that point on the modal captures the rest of the typing, so
    only the first character of a cold-start command comes through here.
    """
    bl_idname = "narwhal.begin_typing"
    bl_label = "Begin Typing Command"
    bl_description = "Type a character into the Narwhal 3D command line"

    char: StringProperty(default="")

    def execute(self, context):
        if not self.char:
            return {"CANCELLED"}
        # The keymap passes the canonical lowercase character, so the buffer
        # holds lowercase command names (the reference CAD application's lookup is case-insensitive).
        app_state.input_buffer += self.char
        typed_changed()
        # A cold-start seed is a new prefix, so the autocomplete highlight
        # restarts at the top row (the modal's printable branch does this too;
        # this path seeds the buffer OUTSIDE the modal, so it must do it itself).
        _set_completion_index(0)
        # Open the modal only if it isn't already capturing keys; otherwise we
        # would stack a second modal on the running one. When the modal is
        # already active it has consumed this key itself, so this path is the
        # cold-start case.
        if not is_input_active():
            try:
                bpy.ops.narwhal.command_input("INVOKE_DEFAULT", keep_buffer=True)
            except Exception:  # noqa: BLE001
                # Headless (-b) has no modal support; the buffer is still
                # seeded, which is the observable effect the tests assert.
                pass
        redraw_cmdbar()
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# start-command operator (tool palette buttons)
# ---------------------------------------------------------------------------

def _command_items(_self, _context):
    """EnumProperty items callback. Blender calls this with (self, context)."""
    items = []
    for key in sorted(COMMANDS.keys()):
        cmd = COMMANDS[key]
        items.append((key, cmd.name, cmd.description))
    return items


class NARWHAL_OT_start_command(Operator):
    bl_idname = "narwhal.start_command"
    bl_label = "Start Command"
    command: EnumProperty(items=_command_items)

    def execute(self, context):
        app_state.start_command(self.command)
        # make sure the command line is capturing input for the next prompt
        if not is_input_active() and app_state.active_command is not None:
            bpy.ops.narwhal.command_input("INVOKE_DEFAULT")
        redraw_cmdbar()
        return {"FINISHED"}


class NARWHAL_OT_open_input(Operator):
    """Focus the command line (called on viewport click / shortcut)."""
    bl_idname = "narwhal.open_input"
    bl_label = "Command Line"
    bl_description = "Open the Narwhal 3D command line for input"

    def execute(self, context):
        # avoid stacking multiple modal inputs
        if not is_input_active():
            bpy.ops.narwhal.command_input("INVOKE_DEFAULT")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# keyboard-shortcut operators
# ---------------------------------------------------------------------------

def _ensure_input_open():
    """Open the command-line modal if a command is waiting for arguments."""
    if not is_input_active() and app_state.active_command is not None:
        bpy.ops.narwhal.command_input("INVOKE_DEFAULT")


class NARWHAL_OT_run_command(Operator):
    """Run a the reference CAD application command by name.

    Deliberately uses a ``StringProperty`` rather than the dynamic
    ``EnumProperty`` on ``narwhal.start_command``: keymap items serialize enum
    values by identifier, so a dynamic item list makes saved keymaps break
    whenever the command table changes. ``AppState.start_command`` already
    resolves names case-insensitively.
    """
    bl_idname = "narwhal.run_command"
    bl_label = "Run Narwhal 3D Command"
    command: StringProperty(default="")

    def execute(self, context):
        if not self.command:
            return {"CANCELLED"}
        # a shortcut may carry its argument, as a the reference CAD application macro does
        # ("SetDisplayMode Wireframe"): name, then the rest of the line
        head, _sp, rest = self.command.strip().partition(" ")
        app_state.start_command(head, rest or None)
        _ensure_input_open()
        redraw_cmdbar()
        return {"FINISHED"}


class NARWHAL_OT_cancel(Operator):
    """Esc: cancel the running command (the reference CAD application behavior)."""
    bl_idname = "narwhal.cancel"
    bl_label = "Cancel Command"
    bl_description = "Cancel the current Narwhal 3D command"

    def execute(self, context):
        # Verified (the reference CAD application keyboard documentation), Esc has five jobs and
        # these are three of them: "Cancels a command", "Deselects all selected
        # objects", "Turns off control and edit point display". A sub-object
        # selection is invisible to Blender's own deselect, so it is cleared here
        # or it would survive Esc and keep the Gumball on it.
        dropped = clear_subobject_selection()
        if app_state.active_command is None and not app_state.input_buffer:
            if dropped:
                app_state.log(f"deselected {dropped} sub-object(s)")
                redraw_cmdbar()
                return {"FINISHED"}
            # nothing of ours to cancel -> let Esc do its normal Blender job
            return {"PASS_THROUGH"}
        app_state.cancel()
        app_state.input_buffer = ""
        redraw_cmdbar()
        return {"FINISHED"}


class NARWHAL_OT_repeat_last(Operator):
    """Enter / Space: re-run the last command (the reference CAD application behavior)."""
    bl_idname = "narwhal.repeat_last"
    bl_label = "Repeat Last Command"
    bl_description = "Repeat the last Narwhal 3D command"

    def execute(self, context):
        # mid-command, Enter/Space belong to the command line, not to us
        # "The user is mid-input" is about there being TEXT, not about a modal
        # existing: the command-line modal now runs for the whole session, so
        # `is_input_active` would be True forever and this would never fire.
        if app_state.active_command is not None or app_state.input_buffer:
            return {"PASS_THROUGH"}
        if not app_state.last_command:
            return {"PASS_THROUGH"}
        app_state.start_command(app_state.last_command)
        _ensure_input_open()
        redraw_cmdbar()
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# tool palette + layers panels (VIEW_3D TOOLS region)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# THE OSNAP PANEL (the reference CAD application documentation: "Osnap panel -- Contains
# object snap toggles. The Selection Filters panel is grouped with the Osnap panel
# in a container.")
#
# The modes, and which of them PERSIST, are already measured in `osnap.py`
# (`PERSISTENT`, from the reference CAD application documentation). This is the panel that
# toggles them, and it drives the SAME `SnapSettings` object the picking code reads
# -- so a box ticked here changes what the next click snaps to, immediately.
#
# `Disable` and `Project` are the reference CAD application's own two extra controls on this panel:
#  "When the Osnap panel is off, clicking the Osnap pane brings up the panel. If
#  the panel is already on, the Disable checkbox is toggled."  (the reference CAD application documentation)
#  "Project -- projects any object snap to the current construction plane."
#                            (the reference CAD application documentation)
#
# `Vertex` is the one mode we do not compute (it needs mesh/SubD objects, which we
# do not build). It is SHOWN -- the panel would misrepresent the reference CAD application without it --
# and ticking it reports that it is not implemented, which is what
# `SnapSettings.unimplemented_enabled` is for.
# ---------------------------------------------------------------------------

class NARWHAL_OT_osnap_toggle(Operator):
    """Tick or untick one object snap on the Osnap panel."""
    bl_idname = "narwhal.osnap_toggle"
    bl_label = "Object Snap"
    bl_description = "Toggle this object snap"
    bl_options = {"INTERNAL"}

    mode: StringProperty(default="")

    def execute(self, context):
        settings = snap_settings()
        if settings is None or not self.mode:
            return {"CANCELLED"}
        now = settings.toggle(self.mode)
        state = "on" if self.mode in settings.enabled else "off"
        if (self.mode in osnap.NOT_IMPLEMENTED
                and self.mode in settings.enabled):
            # Rule 4: it is tickable because the reference CAD application has it, and it says what it is.
            app_state.log(f"{self.mode} snap is not implemented yet "
                          f"(it needs mesh or SubD objects)")
        else:
            app_state.log(f"{self.mode} snap {state}")
        redraw_cmdbar()
        invalidate_snap_cache()
        _tag_redraw_viewports()
        return {"FINISHED"}


class NARWHAL_OT_osnap_flag(Operator):
    """The Osnap panel's two non-mode controls: Disable and Project."""
    bl_idname = "narwhal.osnap_flag"
    bl_label = "Object Snap Option"
    bl_options = {"INTERNAL"}

    flag: StringProperty(default="")

    def execute(self, context):
        settings = snap_settings()
        if settings is None:
            return {"CANCELLED"}
        if self.flag == "disable":
            settings.disabled = not settings.disabled
            app_state.log(f"Object snaps {'disabled' if settings.disabled else 'on'}")
        elif self.flag == "project":
            settings.project = not settings.project
            app_state.log(f"Osnap projection "
                          f"{'on' if settings.project else 'off'}")
        else:
            return {"CANCELLED"}
        redraw_cmdbar()
        invalidate_snap_cache()
        _tag_redraw_viewports()
        return {"FINISHED"}


def osnap_panel_rows():
    """``[(mode, on, implemented)]`` for the persistent snap MODES only.

    Kept for the `Osnap` command and for tests. The ORDER here is `osnap.PERSISTENT`,
    the help's prose list; the PANEL draws `views.OSNAP_PANEL_ORDER` instead, which is
    the order measured from the reference CAD application's own control. Those are different questions and
    conflating them previously produced the wrong panel order; see
    `views.OSNAP_PANEL_ORDER`.
    """
    settings = snap_settings()
    enabled = set(settings.enabled) if settings is not None else set()
    return [(m, m in enabled, m in osnap.IMPLEMENTED)
            for m in osnap.PERSISTENT]


class NARWHAL_PT_osnap(Panel):
    """the reference CAD application's Osnap panel.

    In the viewport's **UI** region (Blender's N-panel), not `TOOLS`. Verified the
    hard way: `TOOLS` in a VIEW_3D is Blender's icon-only tool bar, and showing it
    produced a column of Blender's own select/move/rotate/annotate icons with none
    of our panels in it. `UI` is the only VIEW_3D region that hosts Python Panels,
    and it supports tabs via `bl_category`.

    That puts it on the right instead of the bottom-left. Matching the intended position
    requires a custom GPU strip like the command bar; this legacy Panel class does not
    attempt to imitate the dock.
    """
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Osnap"
    bl_label = "Object Snaps"
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        settings = snap_settings()
        if settings is None:
            layout.label(text="no viewport session")
            return
        col = layout.column(align=True)
        for mode, on, implemented in osnap_panel_rows():
            row = col.row(align=True)
            op = row.operator(
                "narwhal.osnap_toggle",
                text=mode,
                depress=on,
                icon="CHECKBOX_HLT" if on else "CHECKBOX_DEHLT")
            op.mode = mode
            if not implemented:
                # named as unbuilt without being hidden -- the panel has to show
                # what the reference CAD application's shows
                row.label(text="", icon="ERROR")
        col.separator()
        flags = col.column(align=True)
        op = flags.operator("narwhal.osnap_flag", text="Disable",
                            depress=settings.disabled,
                            icon="CHECKBOX_HLT" if settings.disabled
                            else "CHECKBOX_DEHLT")
        op.flag = "disable"
        op = flags.operator("narwhal.osnap_flag", text="Project",
                            depress=settings.project,
                            icon="CHECKBOX_HLT" if settings.project
                            else "CHECKBOX_DEHLT")
        op.flag = "project"


class NARWHAL_PT_toolpalette(Panel):
    """A flat list of every implemented command, used only as a toolbar fallback."""
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Tools"
    bl_label = "Commands (OURS)"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        for key in sorted(COMMANDS.keys()):
            cmd = COMMANDS[key]
            # short labels so they fit the narrow tools region
            label = cmd.name if len(cmd.name) <= 6 else cmd.name[:5] + "."
            op = col.operator("narwhal.start_command", text=label, icon="NONE")
            op.command = key


class NARWHAL_OT_toggle_layer(Operator):
    bl_idname = "narwhal.toggle_layer"
    bl_label = "Toggle Layer Visibility"
    index: bpy.props.IntProperty(default=0)

    def execute(self, context):
        layers = app_state.model.layers()
        if 0 <= self.index < len(layers):
            cur = layers[self.index]["visible"]
            app_state.model.set_layer_visible(self.index, not cur)
        return {"FINISHED"}


class NARWHAL_PT_layers(Panel):
    """the reference CAD application's Layers panel -- one of the Right Container's panels.

    Verified (the reference CAD application documentation): "The Right Container has a collection
    of panels that provide quick access to some global options, document properties,
    or command dialogs." The dedicated right dock hosts these quick-access panels.

    Moved from `TOOLS` to `UI` for the reason `NARWHAL_PT_osnap` documents: `TOOLS` is
    Blender's icon bar and never showed this panel at all.
    """
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Layers"
    bl_label = "Layers"

    def draw(self, context):
        layout = self.layout
        for layer in app_state.model.layers():
            row = layout.row(align=True)
            op = row.operator("narwhal.toggle_layer", text="",
                              icon="CHECKMARK" if layer["visible"] else "X")
            op.index = layer["index"]
            row.label(text=layer["name"][:10])
        # the command was renamed NewLayer -> Layer (real the reference CAD application name), so
        # the registry key is now "layer".
        layout.operator("narwhal.start_command", text="New Layer").command = "layer"


#: the reference CAD application's "Context menu delay ___ milliseconds" (the reference CAD application documentation). The default
#: is application-defined -- see `navigate.DEFAULT_CONTEXT_MENU_DELAY_MS`.
_context_menu_delay_ms = nav.DEFAULT_CONTEXT_MENU_DELAY_MS


def context_menu_delay_ms():
    """How long the right button must be held for the context menu."""
    return _context_menu_delay_ms


def set_context_menu_delay_ms(ms):
    """Set the delay. 0 means "menu immediately, and no Enter", as documented."""
    global _context_menu_delay_ms

    try:
        value = int(ms)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    _context_menu_delay_ms = value
    return _context_menu_delay_ms


def _right_click_enter():
    """A right-CLICK, which the reference CAD application treats as Enter. Returns what it did.

    Verified (the reference CAD application keyboard documentation): "To accept command-line input,
    do one of the following: Press the Enter key. Press the Spacebar. Click the
    right mouse button." So this must behave exactly as Enter does -- submit what is
    typed, or with nothing typed repeat the last command.
    """
    st = app_state
    line = st.input_buffer
    if not line.strip() and st.active_command is None:
        if st.last_command:
            st.start_command(st.last_command)
            redraw_cmdbar()
            return "repeat"
        return "nothing"
    st.input_buffer = ""
    # the right-click is Enter, so it places a constrained point too
    if handle_constraint_line(line):
        redraw_cmdbar()
        return "submit"
    if line.strip():
        st.history.append("> " + line)
    st.feed_line(line)
    redraw_cmdbar()
    return "submit"


class NARWHAL_OT_navigate(Operator):
    """Navigate the viewport the way the reference CAD application does.

    the reference CAD application drags the view with the **right** button, and what that does depends on
    the pane's projection: it *rotates* a perspective view but *pans* a parallel
    one (Top/Front/Right). Blender orbits with the middle button and opens a
    context menu with the right, so before this operator existed the app still
    used Blender's navigation instead of the application's explicit mapping.

    The decision lives in the pure, bpy-free ``navigate`` module (unit-tested by
    ``spike/test_navigate.py`` against the table in
    `the reference CAD application documentation`). This operator only does the two things
    that genuinely need Blender: find which pane the pointer is over and read its
    projection, then hand off to the matching Blender navigation modal, which
    takes over the drag until the button is released.

    An action the reference CAD application has but we have not built reports itself in the command bar
    rather than falling back to something that merely looks similar.
    """
    bl_idname = "narwhal.navigate"
    bl_label = "Narwhal 3D View Navigation"
    bl_description = "Rotate, pan or zoom the Narwhal 3D viewport"

    def invoke(self, context, event):
        # helper: `mouse_region_*` cannot say WHICH pane is under the
        # pointer, so find it from WINDOW-space coordinates.
        area, sp, region = _view3d_under_mouse(event.mouse_x, event.mouse_y)
        if area is None or sp is None or region is None:
            return {"PASS_THROUGH"}

        # A right-press on the TAB STRIP is the tab's own menu, not navigation.
        # Verified: "Right-click a tab to display a context menu for managing
        # viewports." Tested first, because the strip lies inside a viewport region
        # and would otherwise start a right-drag orbit under the user's cursor.
        if viewport_tab_at(event.mouse_x, event.mouse_y) is not None:
            if show_tab_menu() is None:
                app_state.log("the viewport tab menu could not open")
                redraw_cmdbar()
            return {"FINISHED"}

        # A right-press on the pane TITLE is the reference CAD application's viewport-caption menu.
        # Same reasoning as the tab strip: the title is chrome lying inside a viewport
        # region, so it must be claimed before the drag-to-orbit path. Its rows come
        # from the compatibility data; see `_draw_caption_items`.
        if pane_title_at(event.mouse_x, event.mouse_y) is not None:
            show_caption_menu(area)
            return {"FINISHED"}

        # navigating a pane also makes it the active viewport, the same way
        # clicking does -- otherwise the highlight would lie about where you are.
        if set_active_pane(area):
            _tag_redraw_viewports()

        # A right-button press is not yet a navigation: the reference CAD application gives the button
        # THREE jobs and which one this is cannot be known until it moves or comes
        # up (see navigate.rmb_release_action). So record and go modal.
        import time

        # background display work yields from the PRESS, not from the first frame
        # the view has moved (`note_user_interaction`)
        note_user_interaction()
        self._press = (event.mouse_x, event.mouse_y)
        self._press_time = time.monotonic()
        self._area_key = _pane_key(area)
        self._moved = 0.0
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _moved_px(self, event):
        dx = event.mouse_x - self._press[0]
        dy = event.mouse_y - self._press[1]
        return (dx * dx + dy * dy) ** 0.5

    def _start_drag(self, context, event):
        """Hand the drag to Blender's navigation modal, as before."""
        area, sp, region = _view3d_under_mouse(*self._press)
        if area is None or sp is None or region is None:
            return {"CANCELLED"}
        perspective = bool(sp.region_3d.is_perspective)
        action = nav.rmb_action(perspective, ctrl=event.ctrl,
                               shift=event.shift, alt=event.alt)
        op_id = nav.blender_op(action)
        if op_id is None:
            # the reference CAD application does this; we do not. Say so (rule 4) instead of doing
            # something else that would look like it worked.
            app_state.log(f"View {action}: not implemented yet")
            redraw_cmdbar()
            return {"CANCELLED"}
        module, func = op_id.split(".", 1)
        operator = getattr(getattr(bpy.ops, module), func)
        # The navigation modals act on the context's area/region, which is not
        # necessarily the pane under the pointer when four panes are open, so
        # override both explicitly.
        with context.temp_override(area=area, region=region):
            operator("INVOKE_DEFAULT")
        return {"FINISHED"}

    def modal(self, context, event):
        import time

        if event.type == "MOUSEMOVE":
            note_user_interaction()
            self._moved = max(self._moved, self._moved_px(event))
            # Past the threshold this IS a drag, and it must start NOW rather than
            # at release -- the user is already sweeping and expects the view to
            # follow. Blender's navigation modal then owns the button.
            if self._moved > nav.RMB_DRAG_THRESHOLD_PX:
                return self._start_drag(context, event)
            return {"RUNNING_MODAL"}

        if event.type == "RIGHTMOUSE" and event.value == "RELEASE":
            held_ms = (time.monotonic() - self._press_time) * 1000.0
            outcome = nav.rmb_release_action(held_ms, self._moved,
                                            delay_ms=context_menu_delay_ms())
            if outcome == nav.RMB_DRAG:
                return self._start_drag(context, event)
            if outcome == nav.RMB_MENU:
                menu = nav.context_menu_for(
                    has_selection=bool(selection_names()),
                    has_control_points=bool(selected_control_points()))
                if show_context_menu(menu) is None:
                    app_state.log(f"the {menu} context menu could not be raised")
                    redraw_cmdbar()
                return {"FINISHED"}
            # RMB_ENTER: "To accept command-line input ... Click the right mouse
            # button." Feed the command line exactly as Enter does, which with an
            # empty buffer repeats the last command.
            _right_click_enter()
            return {"FINISHED"}

        if event.type == "ESC" and event.value == "PRESS":
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}


#: The MMB mode in force. the reference CAD application makes this a preference (Tools > Options > Mouse);
#: the project default lives in `navigate.DEFAULT_MMB_MODE`.
_mmb_mode = nav.DEFAULT_MMB_MODE


def mmb_mode():
    """The middle-mouse-button mode in force."""
    return _mmb_mode


def set_mmb_mode(mode):
    """Set the MMB mode. Returns the mode in force afterwards, or None if refused.

    A mode the reference CAD application does not offer is REFUSED rather than stored, the same rule
    `set_gumball_alignment` follows: storing it and then behaving like something
    else would leave us claiming a mode we are not in.
    """
    global _mmb_mode

    if mode not in nav.MMB_MODES:
        return None
    _mmb_mode = mode
    return _mmb_mode


class NARWHAL_OT_navigate_mmb(Operator):
    """Middle-button view manipulation, as `the reference CAD application documentation documents it.

    the reference CAD application makes MMB configurable with four settings -- an MRU popup menu, a popup
    toolbar, a macro, or "Manipulate view" -- and under "Manipulate view" three
    sub-modes with their own modifier tables. All of that lives in the pure
    `navigate` module (`mmb_action`), which is where the tables are quoted and
    unit-tested; this operator only finds the pane and hands off to Blender's
    navigation modal, exactly as `NARWHAL_OT_navigate` does for the right button.

    The three non-view modes are REPORTED, not substituted: an MRU popup menu we
    have not built must not silently pan instead (house rule 4).
    """
    bl_idname = "narwhal.navigate_mmb"
    bl_label = "Narwhal 3D Middle-Button Navigation"
    bl_description = "Pan, rotate or zoom with the middle mouse button"

    def invoke(self, context, event):
        area, sp, region = _view3d_under_mouse(event.mouse_x, event.mouse_y)
        if area is None or sp is None or region is None:
            return {"PASS_THROUGH"}

        message = nav.mmb_message(_mmb_mode)
        if message is not None:
            app_state.log(message)
            redraw_cmdbar()
            return {"CANCELLED"}

        note_user_interaction()
        # Navigating a pane makes it active, the same as clicking or right-drag.
        if set_active_pane(area):
            _tag_redraw_viewports()
        action = nav.mmb_action(_mmb_mode, ctrl=event.ctrl,
                               shift=event.shift, alt=event.alt)
        op_id = nav.blender_op(action)
        if op_id is None:
            app_state.log(f"View {action}: not implemented yet")
            redraw_cmdbar()
            return {"CANCELLED"}
        module, func = op_id.split(".", 1)
        operator = getattr(getattr(bpy.ops, module), func)
        with context.temp_override(area=area, region=region):
            operator("INVOKE_DEFAULT")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# THE STATUS BAR (the reference CAD application documentation)
#
#  "The status bar is located at the bottom of the the reference CAD application window. It displays the
#  current coordinate system, the current location and delta of the cursor, and
#  the status bar panes."
#
# `statusbar.py` is the pure model for the 14 panes, their order, kinds, and active
# states. This section renders that model.
#
# WHY IT IS A HEADER OVERRIDE AND NOT A GPU OVERLAY
# ------------------------------------------------
# The command bar is a GPU/blf overlay because it needs the reference CAD application's exact white
# background and a caret. The status bar needs neither, and Blender already owns a
# full-window-width strip at the bottom of the window with real, clickable
# widgets: `STATUSBAR_HT_header`. Replacing its `draw` gets the geometry, the
# hit-testing, the hover highlight and the "lit" (depressed) look for free.
# Blender exposes no `SpaceStatusBar`, so `draw_handler_add` is unavailable for that
# region at all -- the header override is the ONLY supported route.
#
# WHAT IS REAL AND WHAT IS NOT
# ----------------------------
# Six panes read live state we actually have: CPlane/World, the cursor readout,
# units, the layer, Osnap and Gumball. The other eight are modes we have not
# built (Grid Snap, Ortho, Planar, SmartTrack, Auto CPlane, Record History,
# Filter, Info). They are DRAWN -- the bar would be a lie about the reference CAD application's shell
# without them -- but clicking one reports that it is not implemented rather than
# doing nothing or pretending. `STATUSBAR_UNIMPLEMENTED` is the
# list, and it is asserted against the pure model so a pane cannot quietly become
# "implemented" by being forgotten here.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# THE MENU BAR (the reference CAD application documentation)
#
#  "The menu bar groups the reference CAD application commands by functions. Most usually used commands
#  can be found on the menus."
#
# Drawn by REPLACING `TOPBAR_HT_upper_bar.draw`, which is what Blender's
# `File Edit Render Window Help / Layout Modeling Sculpting ...` strip uses. That
# route was measured first (a focused runtime check) and it comes with one hard
# constraint: **the override must be installed before the topbar is first drawn.**
# Nothing can make that strip repaint afterwards -- five separate nudges were tried,
# including the `status_text_set` trick that works for the status bar, and the
# pixels never changed. So this installs from `register`, NOT from the `_setup`
# timer where `_install_statusbar` lives.
#
# `Screen.show_topbar` does not exist, so the strip cannot be hidden -- only
# replaced. Which is exactly where the reference CAD application's menu bar goes.
#
# EVERY item is drawn, and the ones we cannot run are drawn DISABLED. See
# `menubar`'s docstring: listing 739 items as if they worked would be the project's
# biggest rule-4 violation, and hiding the 670-odd we lack would be a different lie
# -- the bar would look complete while being a different program's menus.
# ---------------------------------------------------------------------------

#: One dynamically-built menu class per menu-bar entry, registered on demand.
_menu_classes = {}


def _menu_idname(path):
    """A Blender class name for a menu path. Stable, and a valid identifier."""
    safe = "_".join(
        "".join(ch if ch.isalnum() else "_" for ch in part) for part in path)
    return f"NARWHAL_MT_menu_{safe}"[:60]


def _implemented_keys():
    """The command keys we can actually run."""
    return set(COMMANDS)


def _draw_menu_items(layout, path):
    """Draw the rows at `path`, recursing into submenus.

    Rows come from `menubar.resource_rows` in authored order, including separators.

    `path[0]` is the top-level menu's verbatim label; the rest are popup labels.
    """
    if not path:
        layout.label(text="(empty)")
        return
    menu, inner = path[0], tuple(path[1:])
    rows = menubar.collapse_separators(menubar.resource_rows(menu, inner))
    if not rows:
        layout.label(text="(empty)")
        return

    # A submenu's children may be VARIANTS of the parent's command ('Circle >
    # 2 Points'), so the parent's command and option list have to come down with us.
    parent_command = None
    if inner:
        outer = menubar.resource_rows(menu, inner[:-1])
        want = menubar.plain_label(inner[-1]).casefold()
        for row in outer:
            if (row[0] == "popup"
                    and menubar.plain_label(row[1]).casefold() == want):
                parent_command = menubar.popup_command(row)
                break
    option_names = ()
    if parent_command and parent_command.lower() in COMMANDS:
        option_names = [o.name for o in
                        getattr(COMMANDS[parent_command.lower()], "options", ())]
    else:
        parent_command = None

    for row, command, option in menubar.submenu_actions(rows, parent_command,
                                                        option_names):
        kind, label, accel = row[0], row[1], row[2]
        if kind == "sep":
            layout.separator()
            continue
        text = menubar.plain_label(label)
        if accel:
            text = f"{text}    {accel}"
        if kind == "popup":
            layout.menu(_ensure_menu(tuple(path)
                                     + (menubar.plain_label(label),)), text=text)
            continue
        if (command == "__recent_files__"
                or (command is None and menubar.plain_label(label) == "Recent File")):
            layout.menu("NARWHAL_MT_recent_files", text=text)
            continue
        if command is None and menubar.plain_label(label) == "Exit":
            # the resource row carries no command name; the reference CAD application's is Exit
            command = "Exit"
        if command and command.lower() in COMMANDS:
            if option:
                op = layout.operator("narwhal.run_command_option", text=text)
                op.command = COMMANDS[command.lower()].name
                op.option = option
            else:
                op = layout.operator("narwhal.run_command", text=text)
                op.command = COMMANDS[command.lower()].name
            continue
        # NOT drawn `enabled = False`. That was the first version and it was a bad
        # kind of honest: most items are unbuilt, so almost anything the user clicked
        # was greyed with no explanation.
        op = layout.operator("narwhal.menu_todo", text=text)
        op.label = menubar.plain_label(label)
        op.command = command or ""


def _ensure_menu(path):
    """The bl_idname of the Menu class for `path`, registering it if needed.

    Registration normally happens up front in `_register_menu_classes`, because
    `bpy.utils.register_class` during a DRAW callback does not work -- the first
    version of this registered lazily from the draw and the whole strip came out
    blank. The lazy path is kept only as a fallback for a path that appears after
    startup, and it reports rather than failing silently.
    """
    idname = _menu_idname(path)
    if idname in _menu_classes:
        return idname
    if _registering_menus[0]:
        return _register_one_menu(path)
    print(f"[narwhal3d] menu {idname} was not pre-registered; "
          f"registering from a draw may not take effect")
    return _register_one_menu(path)


#: True only while `_register_menu_classes` is walking the tree, so `_ensure_menu`
#: can tell "expected" registration from registration during a draw.
_registering_menus = [False]


def _register_one_menu(path):
    idname = _menu_idname(path)
    if idname in _menu_classes:
        return idname
    frozen = tuple(path)

    def draw(self, context):
        _draw_menu_items(self.layout, frozen)

    cls = type(idname, (bpy.types.Menu,), {
        "bl_idname": idname,
        "bl_label": menubar.plain_label(frozen[-1]),
        "draw": draw,
    })
    try:
        bpy.utils.register_class(cls)
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] could not register menu {idname}: {e!r}")
        return idname
    _menu_classes[idname] = cls
    return idname


def _register_menu_classes():
    """Pre-register a Menu class for the bar and every submenu. Returns the count.

    the tree is now the reference CAD application's own (resource 128), which has **101 submenus** rather
    than the help tree's handful. Still done here and not lazily, for the measured
    reason: `bpy.utils.register_class` inside a DRAW callback does not take, and the
    toolbar strip came out blank when it was tried.
    """
    _registering_menus[0] = True
    try:
        def walk(path):
            _register_one_menu(path)
            menu, inner = path[0], tuple(path[1:])
            for row in menubar.resource_rows(menu, inner):
                if row[0] == "popup":
                    walk(tuple(path) + (menubar.plain_label(row[1]),))

        for name in menubar.resource_menu_names():
            walk((name,))
        _register_caption_menus()
    finally:
        _registering_menus[0] = False
    return len(_menu_classes)


# ---------------------------------------------------------------------------
# THE VIEWPORT-CAPTION MENU 
#
# Right-clicking a viewport TITLE raises the reference CAD application's caption menu. Its rows are the
# generated `viewport_menu_data` compatibility module; `views.py` holds the pure model.
#
# It reuses the menu bar's dynamic-class machinery rather than inventing a second one,
# because the tree is nested three deep (`Set View > Isometric > NE`) and
# `register_class` inside a draw callback does not take -- measured, it is what left
# the toolbar strip blank. So every submenu gets a pre-registered class too.
#
# Verified: the resource has FOUR top-level popups -- `&View`, `Layout`, `Detail`,
# `Linked`. Inferred (and flagged in the generated module): those are one per VIEWPORT
# KIND, not four submenus of one menu. We have only model viewports, so `&View` is the
# one shown; `_caption_kind_message` reports the other three rather than hiding them.
# ---------------------------------------------------------------------------

#: Prefix for the caption menus' generated class names, kept distinct from the menu
#: bar's so the two trees cannot collide on a shared label like "View".
CAPTION_MENU_PREFIX = "NARWHAL_MT_cap_"


def _caption_idname(path):
    """A stable, legal bl_idname for a caption-menu `path`."""
    key = "_".join(views.menu_command_key(p) or "x" for p in path) or "root"
    return f"{CAPTION_MENU_PREFIX}{key}"[:60]


def _caption_tree():
    """The rows of the menu a MODEL viewport uses, or  if the data is missing."""
    try:
        return viewport_menu_data.MENUS.get(viewport_menu_data.MODEL_KIND, ())
    except Exception:  # noqa: BLE001
        return ()


def caption_kinds_not_implemented():
    """The caption-menu kinds we cannot show, because we have no such viewport.

    Rule 4: the reference CAD application's resource carries four and we serve one. Naming the other three
    keeps that a stated limit rather than a silent omission.
    """
    try:
        kinds = viewport_menu_data.KINDS
        model = viewport_menu_data.MODEL_KIND
    except Exception:  # noqa: BLE001
        return ()
    return tuple(k for k in kinds if k != model)


def _draw_caption_items(layout, path):
    """Draw the caption menu's rows at `path`, recursing into popups.

    the reference CAD application's ORDER and its SEPARATORS are real data here (they came out of the
    resource), so they are preserved -- unlike the menu bar, which sorts because the
    help gave it no order.

    Dispatch is `views.caption_action`, an explicit verified table. A label HEURISTIC
    was tried first and rejected: it matched 3 of 97 rows and all three were wrong
    (`Set CPlane > Rotate` and `Background Bitmap > Move/Scale` would have run the
    object transform commands). See that function.
    """
    rows = views.collapse_menu_separators(
        views.viewport_menu_rows(_caption_tree(), path))
    if not rows:
        layout.label(text="(empty)")
        return
    plain_path = tuple(views.strip_accel(p) for p in path)
    for kind, label, accel, payload in rows:
        if kind == "sep":
            layout.separator()
            continue
        text = views.strip_accel(label)
        if accel:
            text = f"{text}    {accel}"
        if kind == "popup":
            layout.menu(_ensure_caption_menu(tuple(path) + (label,)), text=text)
            continue
        if label == views.DISPLAY_MODE_PLACEHOLDER:
            # the reference CAD application fills this slot with its display modes; the current one ticked
            cur = display_mode_of(active_pane_area()) if active_pane_area() else None
            for name, _key, implemented, _sh in views.DISPLAY_MODES:
                op = layout.operator("narwhal.set_display_mode",
                                     text=name + ("" if implemented else "  *"),
                                     icon="CHECKMARK" if name == cur else "NONE")
                op.mode = name
            continue
        action = views.caption_action(plain_path, label)
        if action == views.ACTION_MAXIMIZE:
            layout.operator("narwhal.maximize_viewport", text=text)
        elif action and action.lower() in COMMANDS:
            op = layout.operator("narwhal.run_command", text=text)
            op.command = COMMANDS[action.lower()].name
        else:
            op = layout.operator("narwhal.menu_todo", text=text)
            op.label = views.strip_accel(label)
            op.command = ""


def _register_one_caption_menu(path):
    idname = _caption_idname(path)
    if idname in _menu_classes:
        return idname
    frozen = tuple(path)

    def draw(self, context):
        _draw_caption_items(self.layout, frozen)

    cls = type(idname, (bpy.types.Menu,), {
        "bl_idname": idname,
        "bl_label": views.strip_accel(frozen[-1]) if frozen else "Viewport",
        "draw": draw,
    })
    try:
        bpy.utils.register_class(cls)
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] could not register caption menu {idname}: {e!r}")
        return idname
    _menu_classes[idname] = cls
    return idname


def _ensure_caption_menu(path):
    idname = _caption_idname(path)
    if idname in _menu_classes:
        return idname
    return _register_one_caption_menu(path)


def _register_caption_menus():
    """Pre-register the caption menu and every popup inside it. Returns the count."""
    before = len(_menu_classes)

    def walk(path):
        _register_one_caption_menu(path)
        for kind, label, _accel, _payload in views.viewport_menu_rows(
                _caption_tree(), path):
            if kind == "popup":
                walk(tuple(path) + (label,))

    walk(())
    return len(_menu_classes) - before


#: A menu armed by a left PRESS, waiting for the RELEASE to open it. A zero-argument
#: callable, or None.
#:
#: Menus open on click and close when clicking away.
#:
#: THE CAUSE. `bpy.ops.wm.call_menu` opens its menu wherever the mouse is *now*, and
#: if the mouse BUTTON IS STILL DOWN when it opens, Blender runs it as a
#: press-drag-release "hold" menu: the release picks whatever the cursor is over and
#: dismisses it. Both offenders called it straight from a left-PRESS branch, so the
#: button was always still down. Nothing about the menu itself was wrong -- only when
#: it was raised.
#:
#: THE FIX. Arm on press, open on release, so the button is already up by the time
#: the menu appears and Blender gives it ordinary click-to-open / click-away-to-close
#: behaviour. Note `_gumball_relocate` already relies on the same distinction for a
#: different reason ("Move the mouse and click" -- its gesture outlives the button),
#: so deferring to the release is an established shape in this file, not a new one.
#:
#: RIGHT-click menus are deliberately NOT deferred. the reference CAD application's right-button behaviour is
#: genuinely press-and-hold -- "Right-click and hold on any part of the gumball" is
#: measured -- so those keep opening on the press.
_pending_menu = None


def _defer_menu(fn):
    """Arm `fn` to open a menu on the next left-button RELEASE. See `_pending_menu`."""
    global _pending_menu
    _pending_menu = fn
    return True


def _flush_pending_menu():
    """Open the armed menu, if any. Returns True when one was opened.

    Cleared BEFORE the call, not after: `wm.call_menu` blocks while the menu is up,
    and leaving the slot armed across that would re-open the menu on the release that
    dismisses it.
    """
    global _pending_menu
    fn = _pending_menu
    _pending_menu = None
    if fn is None:
        return False
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        app_state.log(f"the menu could not open ({e!r})")
        redraw_cmdbar()
        return False
    return True


def pending_menu_armed():
    """True when a left press has armed a menu that its release will open."""
    return _pending_menu is not None


def show_caption_menu(area=None):
    """Raise the viewport-caption menu. Returns its class name, or None.

    `area` becomes the active pane first: the reference CAD application's caption menu acts on the viewport
    whose title you clicked, and "Click the title of a viewport to activate the
    viewport" is measured behaviour anyway.
    """
    if area is not None and set_active_pane(area):
        _tag_redraw_viewports()
    if not _caption_tree():
        app_state.log("the viewport menu data is missing from this installation")
        redraw_cmdbar()
        return None
    name = _ensure_caption_menu(())
    try:
        bpy.ops.wm.call_menu(name=name)
    except Exception as e:  # noqa: BLE001
        app_state.log(f"the viewport menu could not open ({e!r})")
        redraw_cmdbar()
        return None
    return name


def _draw_narwhal_menubar(self, context):
    """Replacement body for `TOPBAR_HT_upper_bar.draw`.

    Left-aligned, one menu per authored entry. Nothing else goes on this strip so
    the application menu remains visually distinct from host chrome.
    """
    # Verified: this draw is called once per topbar REGION, and the topbar has two
    # (left and right). Drawing unconditionally rendered the whole menu bar TWICE,
    # once from each end -- visible in a focused runtime check's screenshot and
    # noted there as an artifact. Blender's own topbar branches on the same flag.
    region = getattr(context, "region", None)
    if region is not None and region.alignment == "RIGHT":
        return                       # the reference CAD application puts nothing at the right of the bar
    layout = self.layout
    layout.alignment = "LEFT"
    row = layout.row(align=True)
    for name in menubar.resource_menu_names():
        row.menu(_ensure_menu((name,)), text=name)


#: The stock topbar draw, kept so `_uninstall_menubar` can put Blender's back.
_menubar_original_draw = None


def _install_menubar():
    """Swap Narwhal's menus in for the host topbar. True if installed.

    Must run BEFORE the topbar is first drawn -- see the block comment above.
    """
    global _menubar_original_draw

    header = getattr(bpy.types, "TOPBAR_HT_upper_bar", None)
    if header is None or _menubar_original_draw is not None:
        return False
    _menubar_original_draw = header.draw
    header.draw = _draw_narwhal_menubar
    return True


def _uninstall_menubar():
    """Restore Blender's topbar and drop the generated menu classes."""
    global _menubar_original_draw

    header = getattr(bpy.types, "TOPBAR_HT_upper_bar", None)
    if header is not None and _menubar_original_draw is not None:
        header.draw = _menubar_original_draw
    _menubar_original_draw = None
    for cls in list(_menu_classes.values()):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:  # noqa: BLE001
            pass
    _menu_classes.clear()
    return True


def menubar_installed():
    """True when Narwhal's menu bar is what the topbar draws."""
    return _menubar_original_draw is not None


# ---------------------------------------------------------------------------
# THE TOOLBAR STRIP (the reference CAD application documentation)
#
#  "A toolbar container is a collection of toolbars. Each toolbar in the container
#  displays as a tab. Click the tabs to switch between toolbars."
#  "Toolbars contain graphical icons for initiating commands. Many icons have a
#  second command that you can access by clicking the right mouse button. The
#  tooltip that appears when you hover over the icon tells you what the left and
#  right mouse button do."
#
# Drawn by replacing `INFO_HT_header.draw` -- the header of the command-bar area --
# which puts it directly under the menu bar and above the command lines. That is
# The command bar occupies row 0 and toolbars row 1.
#
# hid that header because it held Blender's leftover "View | Info" menus. The
# header is shown again here with OUR content; the gate that protected is
# re-expressed as the invariant it was really about -- no BLENDER chrome above the
# command bar.
#
# Runtime icons are generated from the licensed Narwhal source-artwork directory;
# actions without finished artwork receive deterministic placeholders. Missing icon
# files fall back to readable labels rather than making a command inaccessible.
# ---------------------------------------------------------------------------

#: The tab whose buttons are showing; the first tab is the default.
_toolbar_tab = None

#: Blender preview collection holding the loaded icon PNGs, or None.
_toolbar_icons = None
#: Icon filenames we failed to load, so a bad file is tried once and not every draw.
_toolbar_icon_bad = set()

#: application-defined, and flagged: the reference CAD application shows the 16 toolbars as TABS across the strip, and so do
#: we now -- see `_draw_toolbar_tab_row`. The one thing still unlike the reference CAD application is the
#: ORDER of the two rows: the reference CAD application puts the tab row above its button row, and ours sits
#: below the buttons, because the buttons live in the area's single header
#: (`INFO_HT_tool_header` is None -- measured, a focused runtime check) and the
#: tabs have to come out of the region underneath it. Matching the reference CAD application exactly needs the
#: toolbars in their own dock row, which is the layout state machine.
TOOLBAR_TABS_AS_DROPDOWN = False

#: Colours for the tab row. application-defined -- measured only as "the active tab is highlighted".
TAB_ROW_BG = (0.16, 0.16, 0.16, 1.0)
TAB_ROW_ACTIVE_BG = (0.30, 0.42, 0.62, 1.0)
TAB_ROW_TEXT = (0.72, 0.72, 0.72, 1.0)
TAB_ROW_ACTIVE_TEXT = (1.0, 1.0, 1.0, 1.0)

#: The tab row's boxes from the last draw, for hit-testing. Recorded rather than
#: recomputed for the same reason the prompt's option boxes are: the row we test is
#: then measured from the very glyphs we drew and cannot drift from it.
_toolbar_tab_boxes = []
_toolbar_tab_row_top = 0.0


def _draw_toolbar_tab_row(shader, w, h, font_size):
    """Draw the toolbar tab row across the top of the command-bar region.

    Returns the height it used, so the command bar can lay itself out in what is
    left. Returns 0.0 when there is nothing to draw, so the bar is unaffected.

    Verified (the reference CAD application documentation): "Each toolbar in the container
    displays as a tab. Click the tabs to switch between toolbars." All sixteen are
    drawn, not just the active one -- which is the whole point of this over the
    dropdown it replaces.
    """
    global _toolbar_tab_boxes, _toolbar_tab_row_top

    names = tbar.tab_names()
    if not names or h < tbar.TAB_ROW_H * 2:
        _toolbar_tab_boxes = []
        return 0.0
    widths = [_text_width(n, tbar.TAB_FONT_SIZE) for n in names]
    boxes = tbar.tab_row_boxes(names, widths, w)
    row_top = float(h)
    row_bot = row_top - tbar.TAB_ROW_H
    _toolbar_tab_boxes = boxes
    _toolbar_tab_row_top = row_top

    _draw_rect(shader, 0, row_bot, w, row_top, TAB_ROW_BG)
    active = toolbar_tab()
    for name, x0, x1 in boxes:
        is_active = name == active
        if is_active:
            _draw_rect(shader, x0, row_bot, x1, row_top, TAB_ROW_ACTIVE_BG)
        _draw_text(x0 + tbar.TAB_PAD_X, row_bot + 6.0, name, tbar.TAB_FONT_SIZE,
                   TAB_ROW_ACTIVE_TEXT if is_active else TAB_ROW_TEXT)
    # An unimplemented thing must never look implemented: if the window is too
    # narrow for the whole row, say how many tabs are missing rather than letting
    # them vanish. the reference CAD application scrolls its row; we do not have that yet.
    hidden = tbar.tabs_hidden(names, boxes)
    if hidden:
        _draw_text(max(0.0, w - 34.0), row_bot + 6.0, f"+{hidden}",
                   tbar.TAB_FONT_SIZE, TAB_ROW_TEXT)
    return float(tbar.TAB_ROW_H)


def toolbar_tab_row_at(mouse_x, mouse_y):
    """The tab name under the WINDOW-space cursor on the tab row, or None.

    Tested against the boxes recorded by the last draw, so the row that responds is
    the row that is visible.
    """
    area, region = _info_region_at(mouse_x, mouse_y)
    if region is None:
        return None
    return tbar.hit_tab_row(mouse_x - region.x, mouse_y - region.y,
                            _toolbar_tab_boxes, _toolbar_tab_row_top)


def _info_region_at(mouse_x, mouse_y):
    """``(area, region)`` for the command-bar WINDOW region under the cursor.

    Its own helper because `_view3d_under_mouse` deliberately FALLS BACK to the first
    viewport, which would claim clicks that belong to the bar -- the trap that made
    every status-bar click dead once already.
    """
    try:
        areas = list(bpy.context.screen.areas)
    except Exception:  # noqa: BLE001
        return None, None
    for area in areas:
        if area.type != "INFO":
            continue
        for region in area.regions:
            if region.type != "WINDOW":
                continue
            if (region.x <= mouse_x < region.x + region.width
                    and region.y <= mouse_y < region.y + region.height):
                return area, region
    return None, None


def toolbar_tab():
    """The active toolbar tab."""
    global _toolbar_tab

    if _toolbar_tab is None:
        _toolbar_tab = tbar.default_tab()
    return _toolbar_tab


def set_toolbar_tab(name):
    """Switch tabs. Returns the tab in force, or None if `name` is not one."""
    global _toolbar_tab

    if name not in tbar.tab_names():
        return None
    _toolbar_tab = name
    _tag_redraw_cmdbar_header()
    return _toolbar_tab


def _tag_redraw_cmdbar_header():
    """Repaint the toolbar strip. Unlike the status bar, its area IS addressable."""
    try:
        for area in bpy.context.screen.areas:
            if area.type == "INFO":
                area.tag_redraw()
    except Exception:  # noqa: BLE001
        pass


def _toolbar_icon_id(icon):
    """Host icon id for a manifested project-owned icon, or 0 when unavailable."""
    global _toolbar_icons

    if not icon or icon in _toolbar_icon_bad:
        return 0
    path = tbar.icon_path(icon)
    if path is None:
        _toolbar_icon_bad.add(icon)
        return 0
    if _toolbar_icons is None:
        try:
            # `import bpy.utils.previews` would bind the name `bpy` LOCALLY and
            # shadow the module-level import, so any other `bpy.` reference in this
            # function would raise UnboundLocalError. The `as` form binds only the
            # alias. (That bug really happened, in `_uninstall_toolbar`.)
            import bpy.utils.previews as _previews

            _toolbar_icons = _previews.new()
        except Exception:  # noqa: BLE001
            _toolbar_icon_bad.add(icon)
            return 0
    key = icon
    if key not in _toolbar_icons:
        try:
            _toolbar_icons.load(key, path, "IMAGE")
        except Exception:  # noqa: BLE001
            _toolbar_icon_bad.add(icon)
            return 0
    try:
        return _toolbar_icons[key].icon_id
    except Exception:  # noqa: BLE001
        return 0


class NARWHAL_MT_toolbar_tabs(bpy.types.Menu):
    """The toolbar container's sixteen tabs in compatibility order."""
    bl_idname = "NARWHAL_MT_toolbar_tabs"
    bl_label = "Toolbars"

    def draw(self, context):
        layout = self.layout
        active = toolbar_tab()
        for name in tbar.tab_names():
            op = layout.operator("narwhal.toolbar_tab",
                                 text=f"{name}  *" if name == active else name)
            op.tab = name


class NARWHAL_OT_toolbar_tab(Operator):
    """Switch the toolbar strip to a tab."""
    bl_idname = "narwhal.toolbar_tab"
    bl_label = "Toolbar"
    bl_options = {"INTERNAL"}

    tab: StringProperty(default="")

    def execute(self, context):
        if set_toolbar_tab(self.tab) is None:
            app_state.log(f"{self.tab!r} is not a Narwhal 3D toolbar")
            redraw_cmdbar()
        return {"FINISHED"}


class NARWHAL_OT_toolbar_button(Operator):
    """A toolbar button.

    the reference CAD application's buttons carry TWO commands -- "Many icons have a second command that you
    can access by clicking the right mouse button." A Blender layout button has no
    right-click action, so only the LEFT command runs here; the right one is named in
    the tooltip (`toolbars.Button.hover_text`) and is reported when asked for, rather
    than being silently dropped.
    """
    bl_idname = "narwhal.toolbar_button"
    bl_label = "Toolbar Button"

    command: StringProperty(default="")
    label: StringProperty(default="")

    @classmethod
    def description(cls, context, props):
        return props.label or props.command or "Narwhal 3D toolbar button"

    def execute(self, context):
        if not self.command:
            return {"CANCELLED"}
        if self.command not in COMMANDS:
            app_state.log(f"{self.label or self.command} is not implemented yet")
            redraw_cmdbar()
            return {"FINISHED"}
        app_state.start_command(self.command)
        _ensure_input_open()
        redraw_cmdbar()
        return {"FINISHED"}


def _draw_narwhal_toolbar(self, context):
    """Replacement body for `INFO_HT_header.draw` -- the reference CAD application's toolbar strip.

    BUTTONS ONLY. The tab selector used to live here as a dropdown; the sixteen tabs
    are now a row of their own across the top of the region below
    (`_draw_toolbar_tab_row`), which is what the reference CAD application shows. Keeping the dropdown as well
    would give two ways to do the same thing and imply the row was incomplete.

    this header class is shared by EVERY INFO area, and since the left dock band
    is one, the toolbar was being drawn into it too -- clipped to its 114 px, showing
    the first five buttons sideways in the band's top corner. The band gets a caption
    instead, which is what the reference CAD application puts on a docked panel.
    """
    layout = self.layout
    area = getattr(context, "area", None)
    side = _band_side(area)
    if side == "right":
        # the right dock band is a TABBED container, and the reference CAD application captions it with
        # the active panel's name rather than the container name.
        layout.label(text=rightpanel.TABS[right_tab_index()].name)
        return
    if side == "left":
        # Name the dock without implying additional contents.
        layout.label(text=LEFT_BAND_CAPTION)
        return
    row = layout.row(align=True)
    implemented = set(COMMANDS)
    for button in tbar.tab_rows(toolbar_tab(), implemented):
        if button.is_separator:
            row.separator()
            continue
        icon_id = _toolbar_icon_id(button.icon)
        cell = row.row(align=True)
        # Unbuilt commands stay CLICKABLE and report themselves, the same choice the
        # menu bar makes: 524 of these buttons are unbuilt, and greying them said
        # no explanation.
        op = (cell.operator("narwhal.toolbar_button", text="", icon_value=icon_id)
              if icon_id else
              cell.operator("narwhal.toolbar_button",
                            text=(button.text or button.label)[:10]))
        op.command = button.left or ""
        op.label = button.hover_text() or button.label


#: The stock INFO header draw, kept so `_uninstall_toolbar` can restore it.
_toolbar_original_draw = None


def _install_toolbar():
    """Put the reference CAD application's toolbar in the command area's header. True if installed."""
    global _toolbar_original_draw

    header = getattr(bpy.types, "INFO_HT_header", None)
    if header is None or _toolbar_original_draw is not None:
        return False
    _toolbar_original_draw = header.draw
    header.draw = _draw_narwhal_toolbar
    return True


def _uninstall_toolbar():
    """Restore Blender's INFO header and free the loaded icons."""
    global _toolbar_original_draw, _toolbar_icons

    header = getattr(bpy.types, "INFO_HT_header", None)
    if header is not None and _toolbar_original_draw is not None:
        header.draw = _toolbar_original_draw
    _toolbar_original_draw = None
    if _toolbar_icons is not None:
        try:
            import bpy.utils.previews as _previews

            _previews.remove(_toolbar_icons)
        except Exception:  # noqa: BLE001
            pass
        _toolbar_icons = None
    _toolbar_icon_bad.clear()
    return True


def toolbar_installed():
    """True when the reference CAD application's toolbar is what the command area's header draws."""
    return _toolbar_original_draw is not None


def _show_cmdbar_header():
    """Show the command area's header, which now holds the reference CAD application's toolbar.

    HID this header because it carried Blender's leftover "View | Info" menus.
    The content is ours now, so the header is wanted -- see `_draw_narwhal_toolbar`.
    """
    info = _cmdbar_area()
    if info is None:
        return False
    sp = next((s for s in info.spaces if s.type == "INFO"), None)
    if sp is None:
        return False
    return _set_attr(sp, "show_region_header", True)


#: How many of Blender's UI units one character of pane text needs.
#:
#: application-defined, and it is a conversion rather than a measurement of the reference CAD application: a UI unit is
#: 20 px at scale 1.0 and the status-bar font averages a little over 8 px per
#: character at that scale, so 0.42 leaves each cell able to hold its widest text
#: without padding the bar out. Too small clips text; too large wastes the strip.
#: The gate that matters is not this number but that the row does not MOVE, which
#: the live-window UI checks asserts from real pixels.
STATUSBAR_UNITS_PER_CHAR = 0.42

#: Panes whose MODE we have not built. Drawn, but clicking one says so.
STATUSBAR_UNIMPLEMENTED = frozenset({
    "grid_snap", "planar", "smarttrack",
    "auto_cplane", "record_history", "filter", "info",
})

#: True when the coordinate readout shows CPlane coordinates, False for world.
#: "The construction plane/world coordinates toggle."
_statusbar_cplane_mode = True

#: The last cursor location seen in a viewport, in world coordinates, or None.
#: "The x/y/z coordinates of the mouse cursor location."
_statusbar_cursor = None


def statusbar_cplane_mode():
    """True when the readout is in CPlane coordinates, False for World."""
    return _statusbar_cplane_mode


def set_statusbar_cursor(point):
    """Record the cursor's world location for the readout. Returns True if changed."""
    global _statusbar_cursor
    new = tuple(point) if point is not None else None
    if new == _statusbar_cursor:
        return False
    _statusbar_cursor = new
    return True


#: `_statusbar_layer_name`'s last answer for the selection: the rows it read (by
#: identity -- a new document state is a new rows list), the selected names, the layer
#: indices they are on.
_statusbar_layer_cache = {"rows": None, "sel": None, "idxs": frozenset()}


def _statusbar_layer_name():
    """The layer the bar should show.

    Verified: "If objects are selected, the Layer pane displays the layer of the
    selected objects. If no objects are selected, the Layer pane displays the
    current layer."
    """
    model = getattr(app_state, "model", None)
    if model is None:
        return None
    try:
        layers = model.layers()
    except Exception:  # noqa: BLE001
        return None
    if not layers:
        return None
    names = [getattr(l, "name", None) or (l.get("name") if isinstance(l, dict) else None)
             for l in layers]
    names = [n for n in names if n]
    if not names:
        return None
    # the selected objects' layer, when they agree on one
    try:
        selected = [o.name for o in bridge.selected_display_objects()]
    except Exception:  # noqa: BLE001
        selected = []
    if selected:
        # From the shared document rows, kept while rows / selection / layers stand.
        # Large-document profiling showed that
        # walking all 5,702 objects with a list lookup was 55 ms of EVERY status-bar
        # redraw -- every Gumball move -- over half the time between moves.
        rows = _doc_rows()
        sel = frozenset(selected)
        c = _statusbar_layer_cache
        if c["rows"] is rows and c["sel"] == sel:
            idxs = set(c["idxs"])
        else:
            idxs = {r[2] for r in rows if r[0] in sel and r[2] is not None}
            c.update(rows=rows, sel=sel, idxs=frozenset(idxs))
        if len(idxs) == 1:
            i = idxs.pop()
            if 0 <= i < len(names):
                return names[i]
        elif len(idxs) > 1:
            return "varies"
    return names[0]


def statusbar_state():
    """Live values for every pane, in the shape `statusbar.render` expects.

    A pane we have no state for is left ABSENT rather than given a placeholder:
    the pure model already renders an absent readout as its own label ("x y z",
    "Units"), which is exactly how an idle the reference CAD application bar reads.
    """
    state = {}

    # "The construction plane/world coordinates toggle."
    state["cplane"] = "CPlane" if _statusbar_cplane_mode else "World"

    # "The x/y/z coordinates of the mouse cursor location."
    if _statusbar_cursor is not None:
        point = _statusbar_cursor
        # "The construction plane/world coordinates toggle" -- in CPlane mode the
        # readout is in the ACTIVE pane's plane, which is the same transform a
        # typed point goes through (`CPlane.from_world` is `to_world`'s inverse).
        if _statusbar_cplane_mode:
            plane = get_cplane(active_pane_area())
            if plane is not None:
                try:
                    point = plane.from_world(point)
                except Exception:  # noqa: BLE001
                    pass
        state["coordinates"] = point

    # "The current units setting." -- the DOCUMENT's: an Inches file opened from real
    # the reference CAD application showed "Millimeters" here (the app default was read instead).
    try:
        from . import units

        model = getattr(app_state, "model", None)
        doc_unit = model.unit_system() if model is not None else None
        state["units"] = doc_unit or units.default_tolerances()["unit"]
    except Exception:  # noqa: BLE001
        pass

    layer = _statusbar_layer_name()
    if layer:
        state["layer"] = layer

    # Osnap: the pane is lit when snapping is in effect. `SnapSettings.disabled`
    # is the reference CAD application's own Disable checkbox, so "in effect" is "not disabled".
    settings = snap_settings()
    if settings is not None:
        state["osnap"] = not settings.disabled

    # Gumball is an ALIGNMENT pane: "Gumball (CPlane)" in the screenshot.
    state["gumball"] = {"on": gumball_enabled(), "alignment": gumball_alignment()}

    # Ortho: lit while the persistent mode is on (Shift only flips it for a pick)
    state["ortho"] = bool(_ortho_on)

    return state


def statusbar_click(pane_id):
    """Act on a click in pane `pane_id`. Returns the message to log, or None.

    Every branch either changes real state or says it cannot. Nothing here may
    silently do nothing: a pane that looks like a button and answers nothing is
    the exact failure mode rule 4 exists to prevent.
    """
    global _statusbar_cplane_mode

    if not sbar.is_clickable(pane_id):
        return None

    if pane_id in STATUSBAR_UNIMPLEMENTED:
        label = sbar.pane(pane_id).label
        return f"{label} is not implemented yet"

    if pane_id == "cplane":
        _statusbar_cplane_mode = not _statusbar_cplane_mode
        mode = "CPlane" if _statusbar_cplane_mode else "World"
        return f"Coordinate readout: {mode}"

    if pane_id == "osnap":
        settings = snap_settings()
        if settings is None:
            return "Osnap: no viewport session"
        # Verified (the reference CAD application documentation, "Osnap pane"): "The Osnap controls
        # let you select which object snaps are currently in effect. When the Osnap
        # PANEL IS OFF, clicking the Osnap pane BRINGS UP THE PANEL. If the panel is
        # already on, the DISABLE checkbox is toggled."
        #
        # We used to toggle Disable unconditionally, which is the second half of that
        # rule applied in both cases -- so with the panel hidden the pane could never
        # bring it back, the same shape of one-way door the Gumball had.
        if not osnap_panel_visible():
            set_osnap_panel_visible(True)
            return "Osnap panel shown"
        settings.disabled = not settings.disabled
        _tag_redraw_viewports()
        return f"Osnap is {'off' if settings.disabled else 'on'}"

    if pane_id == "gumball":
        on = set_gumball(not gumball_enabled())
        return f"Gumball is {'on' if on else 'off'}"

    if pane_id == "ortho":
        # "clicking Ortho in the status bar ... [is a] toggle" (the reference CAD application documentation)
        return toggle_ortho()

    if pane_id == "layer":
        # "Click the Layer pane to access quick controls for setting the selected
        # objects' layer or to change layer visibility and status." That popup is
        # not built; report the current layer rather than opening nothing.
        return (f"Current layer: {_statusbar_layer_name() or 'none'} "
                f"(the layer popup is not implemented yet)")

    return f"{sbar.pane(pane_id).label}: not implemented yet"


#: Which status-bar panes answer a RIGHT-click, and with what.
#:
#: Verified (the reference CAD application documentation): "Right-click the pane to show the
#: Gumball menu" for the Gumball pane, and the SmartTrack / Grid Snap / Osnap panes
#: each document their own right-click menu too. Only the Gumball one is built,
#: because only the Gumball menu exists -- the rest report themselves.
#:
#: This is not a nicety. `Gumball Off` is reached FROM the menu ball, which is part
#: of the widget, so switching the Gumball off removes the only thing that could
#: switch it back on. the reference CAD application's answer is this pane: left-click toggles, right-click
#: raises the menu.
STATUSBAR_RIGHT_CLICK_MENUS = {
    "gumball": "NARWHAL_MT_gumball",
}


def statusbar_right_click(pane_id):
    """Right-click on a status-bar pane. Returns the menu raised, or a message.

    Returns the menu's class name when one opened, a string when the pane has no
    built menu, and None when the pane is not clickable at all -- three distinct
    answers, so a caller can tell "opened" from "said why not".
    """
    if not sbar.is_clickable(pane_id):
        return None
    name = STATUSBAR_RIGHT_CLICK_MENUS.get(pane_id)
    if name is None:
        label = sbar.pane(pane_id).label
        return f"the {label} pane's right-click menu is not implemented yet"
    try:
        bpy.ops.wm.call_menu(name=name)
    except Exception as e:  # noqa: BLE001
        return f"that menu could not open ({e!r})"
    return name


def _statusbar_button_context_menu(self, context):
    """Appended to Blender's button right-click menu: the reference CAD application's pane menus.

    Verified: "Right-click the pane to show the Gumball menu." A header button has
    no right-click hook of its own, but Blender routes every button's right-click
    through `UI_MT_button_context_menu`, and `context.button_operator` says WHICH
    button. So the entry is added only when the button under the cursor really is
    one of our panes -- other buttons' menus are untouched.

    HONEST LIMIT, stated because it is visible: Blender's own entries (Copy Data
    Path, Add to Quick Favourites, ...) still appear below ours. Replacing the menu
    wholesale would mean overriding `UI_MT_button_context_menu.draw`, which would
    strip those entries from every button in the application, not just ours. the reference CAD application's
    pane menu has nothing under it; ours has Blender's tail.
    """
    op = getattr(context, "button_operator", None)
    if op is None or getattr(op, "bl_rna", None) is None:
        return
    if op.bl_rna.identifier != "NARWHAL_OT_statusbar_click":
        return
    pane_id = getattr(op, "pane", "") or ""
    menu = STATUSBAR_RIGHT_CLICK_MENUS.get(pane_id)
    layout = self.layout
    if menu is None:
        if sbar.is_clickable(pane_id):
            row = layout.row()
            row.enabled = False
            row.label(text=f"{sbar.pane(pane_id).label} menu: not implemented yet")
            layout.separator()
        return
    # Drawn INLINE rather than as a submenu: the reference CAD application's right-click puts you straight
    # in the Gumball menu, and this is the path back when the widget (and therefore
    # its menu ball) has been switched off.
    _draw_gumball_menu_into(layout)
    layout.separator()


def _install_statusbar_context_menu():
    """Append our pane menus to Blender's button context menu. True if installed."""
    global _statusbar_ctx_installed
    if _statusbar_ctx_installed:
        return True
    try:
        bpy.types.UI_MT_button_context_menu.append(_statusbar_button_context_menu)
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] status-bar right-click menu not installed: {e!r}")
        return False
    _statusbar_ctx_installed = True
    return True


def _uninstall_statusbar_context_menu():
    global _statusbar_ctx_installed
    if not _statusbar_ctx_installed:
        return
    try:
        bpy.types.UI_MT_button_context_menu.remove(_statusbar_button_context_menu)
    except Exception:  # noqa: BLE001
        pass
    _statusbar_ctx_installed = False


_statusbar_ctx_installed = False


def _track_cursor(mouse_x, mouse_y):
    """Feed the status bar's x/y/z readout from a window-space mouse position.

    Returns True when the readout changed. Shared by the always-on tracker below
    and by the command-line modal, so the readout behaves the same whether or not
    a command is running -- the help makes it unconditional.
    """
    area, sp, region = _view3d_under_mouse(mouse_x, mouse_y)
    if region is None:
        # Outside every viewport there is no location to report. Cleared rather
        # than frozen at the last value, which would claim the cursor is
        # somewhere it is not.
        if set_statusbar_cursor(None):
            _tag_redraw_statusbar()
            return True
        return False
    point = pick_point(area, sp, region,
                      mouse_x - region.x, mouse_y - region.y)
    if set_statusbar_cursor(point):
        _tag_redraw_statusbar()
        return True
    return False


#: True while the cursor tracker's modal is running, so it is never stacked twice.
_tracker_running = False
#: Set by `unregister` to ask the tracker to end on its next event.
_tracker_stop = False


def tracker_running():
    """True when the always-on cursor tracker is live."""
    return _tracker_running


class NARWHAL_OT_track_mouse(Operator):
    """Always-on cursor tracking, for the status bar's x/y/z readout.

    The readout was previously fed only from `NARWHAL_OT_command_input.modal`, and that modal
    is only started when the user types or clicks the command bar -- Verified: at
    launch `is_input_active` is False, so **no MOUSEMOVE reached us at all** and
    the pane sat on its idle label forever.

    A modal is the only source of mouse position in `bpy`: there is no global
    getter (Verified -- `Window` has no `mouse_x`; `cursor_warp` is a setter only).
    So this is a modal whose entire job is to watch MOUSEMOVE, and which returns
    **PASS_THROUGH for every event** so it can never swallow one. It is added at
    setup, which puts it at the BOTTOM of the modal stack -- the command-line modal
    is pushed above it later and, when it consumes a MOUSEMOVE, feeds the readout
    itself through the same `_track_cursor`.
    """
    bl_idname = "narwhal.track_mouse"
    bl_label = "Narwhal 3D Track Cursor"
    bl_description = "Track the cursor for the status bar readout"
    bl_options = {"INTERNAL"}

    def invoke(self, context, event):
        global _tracker_running, _tracker_stop
        if _tracker_running:
            return {"CANCELLED"}       # exactly one, ever
        _tracker_stop = False
        _tracker_running = True
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        global _tracker_running
        if _tracker_stop:
            _tracker_running = False
            return {"FINISHED"}
        if event.type == "MOUSEMOVE":
            try:
                _track_cursor(event.mouse_x, event.mouse_y)
            except Exception:  # noqa: BLE001
                # A tracker that raises would tear itself down and take the
                # readout with it; a missed frame is the lesser failure.
                pass
        # NEVER consume: this operator observes, it does not handle.
        return {"PASS_THROUGH"}


def _open_command_line():
    """Give the command line keyboard focus. True if the modal is running after.

    A no-op under `blender -b`: background mode has no modal support, so the call
    fails with "Invalid operator call" and logs an error for every run of every
    headless suite. Guarded on `bpy.app.background` for the same reason
    `_pick_object_at` is -- that is the canonical test.
    """
    if _input_active or bpy.app.background:
        return _input_active
    try:
        bpy.ops.narwhal.command_input("INVOKE_DEFAULT", keep_buffer=True)
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] could not open the command line: {e!r}")
    return _input_active


def _start_cursor_tracker():
    """Start the always-on tracker. True if it is running afterwards."""
    if _tracker_running:
        return True
    try:
        bpy.ops.narwhal.track_mouse("INVOKE_DEFAULT")
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] could not start the cursor tracker: {e!r}")
    return _tracker_running


def _stop_cursor_tracker():
    """Ask the tracker to end (it finishes on its next event)."""
    global _tracker_stop
    _tracker_stop = True


class NARWHAL_OT_menu_todo(Operator):
    """A visible menu action for a feature Narwhal has not built.

    Keeping individually named unavailable actions in the authored menus makes the UI
    complete and honest: the label says the feature is unavailable, and clicking it explains rather
    than silently doing nothing or dispatching a different command.
    """
    bl_idname = "narwhal.menu_todo"
    bl_label = "Not Implemented"
    bl_description = "This Narwhal 3D command is not implemented yet"
    bl_options = {"INTERNAL"}

    label: StringProperty(default="")
    command: StringProperty(default="")

    def execute(self, context):
        what = self.label or self.command or "that"
        # BOTH must be set before naming both. With an empty label this used to
        # produce " (CopyToClipboard) is not implemented yet" -- a leading space and
        # no name -- which the clipboard shortcuts hit immediately, because a
        # keymap item can carry only ONE property and they pass `command`.
        if self.label and self.command \
                and self.command.lower() != self.label.lower():
            what = f"{self.label} ({self.command})"
        app_state.log(f"{what} is not implemented yet")
        redraw_cmdbar()
        return {"FINISHED"}


class NARWHAL_OT_run_command_option(Operator):
    """Run a command and immediately choose one of its options.

    the reference CAD application's menus reach a command's VARIANTS through submenu rows -- `Curve >
    Circle > 2 Points` is the `Circle` command with its `2Point` option. So a menu row
    needs to start the command AND answer its first option, which `narwhal.run_command`
    cannot do.

    The option is fed through the SAME path a typed option takes (`feed_line`), so a
    menu row and typing the option behave identically -- including reporting "the X
    option is not implemented yet" for options we declare but have not built. A menu
    row must not be a second way in that behaves differently.
    """
    bl_idname = "narwhal.run_command_option"
    bl_label = "Run Narwhal 3D Command Option"
    bl_options = {"INTERNAL"}

    command: StringProperty(default="")
    option: StringProperty(default="")

    def execute(self, context):
        if not self.command:
            return {"CANCELLED"}
        app_state.start_command(self.command)
        if self.option and app_state.active_command is not None:
            app_state.feed_line(self.option)
        _ensure_input_open()
        redraw_cmdbar()
        return {"FINISHED"}


def set_display_mode(area, name):
    """Put viewport `area` into display mode `name`. Returns ``(ok, message)``.

    Verified mappings (probes, real window):
    * Wireframe -> Blender WIREFRAME with `wireframe_threshold = 1`. Patched Blender's
      reserved `Mesh.narwhal_boundary_wires` factor still hides CAD display tessellation,
      while split Brep boundaries and real mesh-object topology remain visible.
    * Shaded -> SOLID, colour from the TEXTURE source. Verified: an ordinary object
      reads (161,163,164) under both MATERIAL and TEXTURE, so nothing else changes,
      and a Picture's image shows ( request: in Solid it shows only with TEXTURE).
    * Rendered -> SOLID with studio light and our custom soft ground shadow (application-defined; see
      views.DISPLAY_MODES -- MATERIAL preview darkened the whole viewport). Blender's own
      duplicate shadow pass is deliberately off:  measured 232 ms/frame on 0070.3dm.
    """
    mode, err = views.display_mode(name)
    if mode is None:
        return False, err
    label, implemented, shading = mode
    if not implemented:
        return False, f"the {label} display mode is not implemented yet"
    space = next((s_ for s_ in area.spaces if s_.type == "VIEW_3D"), None)
    if space is None:
        return False, "that is not a viewport"
    sh = space.shading
    ov = space.overlay
    lit = shading == "SOLID_LIT"
    # Compatibility display rules:
    # * Shaded: a light-grey object with BLACK EDGES over the normal grid -- the edges
    #  matter: sub-object picking (Ctrl+Shift) needs them visible.
    # * Rendered: WHITE background, NO grid or axes, a white object, no edges, soft
    #  shading (faces 226/200/253 grey).
    # Patched Blender's CAD mesh flag reserves factor 1 to hide manifold display tessellation.
    # Shaded uses threshold 0 for useful boundaries/creases only; Wireframe uses 1 so actual
    # mesh objects retain all polygon topology. `the rendered boundary-wire checks` gates both.
    ov.wireframe_threshold = 1.0 if shading == "WIREFRAME" else 0.0
    # Blender's own (infinite) grid stays off in every mode: ours draws instead, and
    # `grid_shown_in` leaves it out of Rendered.
    ov.show_floor = False
    ov.show_ortho_grid = False
    ov.show_axis_x = False
    ov.show_axis_y = False
    if shading == "WIREFRAME":
        sh.type = "WIREFRAME"
        ov.show_wireframes = False
        sh.background_type = "VIEWPORT"
        sh.background_color = VIEWPORT_BG[:3]
    else:
        sh.type = "SOLID"
        sh.light = "STUDIO"
        # Verified face greys of a 10-cube, Perspective (top / right / left):
        #  target Shaded 114/200/133, target Rendered 253/226/200;
        #  studio "Default" 152/63/144 (one face near-black), "studio.sl"
        #  137/94/178, "paint.sl" 216/172/193 -- the closest to each.
        try:
            sh.studio_light = "paint.sl" if lit else "studio.sl"
        except Exception:  # noqa: BLE001
            pass
        # `draw_ground_shadow` already paints the reference CAD application's soft automatic ground
        # shadow. Keep Blender's duplicate pass off; the old combined path was 232 ms/frame
        # on the attached 241-object file and provided no second useful visual effect.
        sh.show_shadows = False
        ov.show_wireframes = not lit
        ov.wireframe_opacity = 1.0
        sh.show_cavity = False
        sh.background_type = "VIEWPORT"
        if lit:
            # white object, soft shadow side; the reference CAD application's ground shadow is not built
            sh.color_type = "SINGLE"
            sh.single_color = (1.0, 1.0, 1.0)
            sh.shadow_intensity = 0.25
            sh.background_color = (1.0, 1.0, 1.0)
        else:
            sh.color_type = "TEXTURE"
            sh.background_color = VIEWPORT_BG[:3]
    area.tag_redraw()
    return True, f"{pane_label(area)}: {label}"


def display_mode_of(area):
    space = next((s_ for s_ in area.spaces if s_.type == "VIEW_3D"), None)
    if space is None:
        return None
    return views.display_mode_for_shading(
        space.shading.type,
        lit=(space.shading.type == "SOLID" and space.shading.color_type == "SINGLE"))


def set_display_mode_scope(name, scope="Active"):
    """`SetDisplayMode`: the ACTIVE viewport, or ALL of them. Returns a message."""
    areas = _view3d_areas() if scope == "All" else [active_pane_area()]
    areas = [a for a in areas if a is not None]
    if not areas:
        return "there is no viewport"
    msgs = []
    for a in areas:
        ok, msg = set_display_mode(a, name)
        if not ok:
            return msg
        msgs.append(msg)
    return "; ".join(msgs)


class NARWHAL_MT_recent_files(bpy.types.Menu):
    """File > Recent File: the newest files first; a click runs Open on it."""
    bl_idname = "NARWHAL_MT_recent_files"
    bl_label = "Recent File"

    def draw(self, context):
        from . import file_commands

        items = file_commands.recent_items()
        if not items:
            self.layout.label(text="(no recent files)")
            return
        for path in items:
            op = self.layout.operator("narwhal.run_command",
                                      text=os.path.basename(path))
            op.command = f"Open {path}"


class NARWHAL_OT_set_display_mode(Operator):
    """A display-mode row of the viewport title menu, or Ctrl+Alt+W/S/R."""
    bl_idname = "narwhal.set_display_mode"
    bl_label = "Set Display Mode"
    mode: StringProperty(default="")

    def execute(self, context):
        msg = set_display_mode_scope(self.mode, "Active")
        app_state.log(msg)
        redraw_cmdbar()
        return {"FINISHED"}


def _unit_items(self, context):
    from . import units as _units
    return [(u, u, "") for u in _units.UNIT_SCALE_MM]


class NARWHAL_OT_units_dialog(Operator):
    """Show document unit and tolerance settings.

    `Units` exposes model units, absolute tolerance, and
    "When you change from one units system to another, the reference CAD application asks if you want to have
    the geometry automatically scaled". This window holds those three; OK runs the
    tested command-line `-Units` with them, so both routes share one implementation.
    Not built (and not shown): layout units, angle tolerance, distance display."""
    bl_idname = "narwhal.units_dialog"
    bl_label = "Units"
    bl_options = {"INTERNAL"}

    model_units: EnumProperty(name="Model units", items=_unit_items)
    absolute_tolerance: bpy.props.FloatProperty(
        name="Absolute tolerance", min=1e-9, default=0.001, precision=6)
    scale_model: BoolProperty(
        name="Scale the model to match the new units", default=True)

    def invoke(self, context, event):
        try:
            self.model_units = app_state.model.unit_system()
            self.absolute_tolerance = float(app_state.model.absolute_tolerance())
        except Exception:  # noqa: BLE001
            pass
        return context.window_manager.invoke_props_dialog(self, width=380)

    def draw(self, context):
        col = self.layout.column()
        col.label(text="Units and tolerances")
        col.prop(self, "model_units")
        col.prop(self, "absolute_tolerance")
        col.prop(self, "scale_model")

    def execute(self, context):
        apply_units_dialog(self.model_units, self.absolute_tolerance, self.scale_model)
        redraw_cmdbar()
        _tag_redraw_viewports()
        return {"FINISHED"}


def apply_units_dialog(unit, tolerance, scale):
    """What OK in the Units window does: the command-line `-Units` with these answers.
    Returns the line the command logged."""
    app_state.active_command = None
    app_state.feed_line("-Units")
    app_state.feed_line(f"AbsoluteTolerance={float(tolerance):.12g}")
    app_state.feed_line(str(unit))
    if app_state.active_command is not None:          # the scale question
        app_state.feed_line("Yes" if scale else "No")
    return app_state.history[-1] if app_state.history else None


def _open_units_dialog():
    """`Units` typed (or clicked): open the window. True when it opened."""
    if bpy.app.background:
        return False
    win = getattr(bpy.context, "window", None) or (
        bpy.context.window_manager.windows[0] if bpy.context.window_manager.windows
        else None)
    if win is None:
        return False
    with bpy.context.temp_override(window=win, screen=win.screen):
        bpy.ops.narwhal.units_dialog("INVOKE_DEFAULT")
    return True


class NARWHAL_OT_maximize_viewport(Operator):
    """The caption menu's `Maximize Viewport` row.

    Not a the reference CAD application COMMAND we have -- it is behaviour we already had, reached from a menu.
    Verified (the reference CAD application documentation): "Double-click a viewport title to
    maximize the viewport, or restore the multi-viewport layout", which is the same
    toggle, so this is the same function rather than a second implementation.
    """
    bl_idname = "narwhal.maximize_viewport"
    bl_label = "Maximize Viewport"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        area = active_pane_area()
        if area is None:
            app_state.log("no active viewport to maximize")
            redraw_cmdbar()
            return {"CANCELLED"}
        toggle_maximize(area)
        app_state.log("Restoring the viewport layout" if _maximized
                      else "Maximizing the viewport")
        redraw_cmdbar()
        return {"FINISHED"}


class NARWHAL_OT_statusbar_click(Operator):
    """Clicking a status-bar pane."""
    bl_idname = "narwhal.statusbar_click"
    bl_label = "Status Bar"
    bl_description = "Narwhal 3D status bar pane"
    bl_options = {"INTERNAL"}

    pane: bpy.props.StringProperty(default="")

    def execute(self, context):
        message = statusbar_click(self.pane)
        if message:
            app_state.log(message)
            redraw_cmdbar()
        _tag_redraw_viewports()
        return {"FINISHED"}


def _draw_narwhal_statusbar(self, context):
    """Replacement body for `STATUSBAR_HT_header.draw`.

    Left-to-right in the model's canonical order. A clickable pane is an operator
    button; a lit pane is
    drawn `depress`ed, which is Blender's rendering of "this is on" and the
    closest thing it has to the reference CAD application's "the pane lights up when the mode is turned
    on".
    """
    layout = self.layout
    try:
        state = statusbar_state()
    except Exception as exc:  # noqa: BLE001
        layout.label(text=f"status bar failed: {exc!r}")
        return
    row = layout.row(align=True)
    for pane in sbar.panes():
        try:
            text = sbar.render(pane.id, state)
        except Exception:  # noqa: BLE001
            continue
        # Each pane gets its own fixed-width cell so changing coordinate digits cannot
        # shift the rest of the bar.
        #
        # Padding the coordinate STRING to a constant character count was the first
        # attempt and it was not enough: the status bar uses a PROPORTIONAL font, so
        # equal character counts are not equal pixel widths ("1" is narrower than
        # "8"). The row kept shifting. Pinning the CELL with `ui_units_x` is what
        # actually fixes it, and it fixes every pane at once rather than only the
        # readout -- a long layer name would have moved the row too.
        cell = row.row(align=True)
        cell.ui_units_x = sbar.pane_width_chars(pane.id) * STATUSBAR_UNITS_PER_CHAR
        if pane.clickable:
            op = cell.operator("narwhal.statusbar_click", text=text,
                               depress=sbar.is_active(pane.id, state))
            op.pane = pane.id
        else:
            cell.label(text=text)


#: The stock header draw, kept so `_uninstall_statusbar` can put Blender's back.
_statusbar_original_draw = None


def _install_statusbar():
    """Swap the reference CAD application's panes in for Blender's status-bar content. True if installed."""
    global _statusbar_original_draw

    header = getattr(bpy.types, "STATUSBAR_HT_header", None)
    if header is None or _statusbar_original_draw is not None:
        return False
    _statusbar_original_draw = header.draw
    header.draw = _draw_narwhal_statusbar
    return True


def _uninstall_statusbar():
    """Restore Blender's own status bar (used by unregister and by the tests)."""
    global _statusbar_original_draw

    header = getattr(bpy.types, "STATUSBAR_HT_header", None)
    if header is None or _statusbar_original_draw is None:
        return False
    header.draw = _statusbar_original_draw
    _statusbar_original_draw = None
    return True


def statusbar_installed():
    """True when the reference CAD application's status bar is the one being drawn."""
    return _statusbar_original_draw is not None


def _tag_redraw_statusbar():
    """Ask the status-bar strip to repaint. Returns True if we could.

    Verified, and it is a trap worth writing down: **Blender's status bar is not
    an `Area`.** `STATUSBAR` is a valid `Area.type` value, but no area of that
    type exists in any screen -- probed across `bpy.data.screens`, the count is
    **0**, while the window still reserves the bottom ~23 px for the strip. It is
    global window chrome, so there is nothing to call `tag_redraw` on, and the
    first version of this function looped over `screen.areas` looking for it and
    was a silent no-op.

    There is also no `SpaceStatusBar`, so `draw_handler_add` cannot reach that
    region either -- which is why the strip is a `STATUSBAR_HT_header.draw`
    override rather than a GPU overlay like the command bar.

    Verified which levers actually work (a focused runtime check, comparing
    the grey-mean of the bottom 23 px of a real screenshot):

      * tagging every area in the screen  -> mean UNCHANGED (0.218973 both ways).
        No effect at all; this was the first attempt and it silently did nothing.
      * `workspace.status_text_set(...)` then `(None)` -> mean CHANGES
        (0.218973 -> 0.221427). This is the lever.

    `status_text_set` is Blender's "temporary message in the status bar", so
    setting and immediately clearing it is a no-op for content but does fire the
    notifier that repaints the strip. Our header override means the message itself
    is never drawn, so nothing flickers.

    Returns True when the nudge went through.
    """
    try:
        ws = bpy.context.workspace
        ws.status_text_set("")
        ws.status_text_set(None)
        return True
    except Exception:  # noqa: BLE001
        return False


CLASSES = (
    NARWHAL_OT_command_input,
    NARWHAL_OT_navigate,
    NARWHAL_OT_navigate_mmb,
    NARWHAL_OT_begin_typing,
    NARWHAL_OT_start_command,
    NARWHAL_OT_open_input,
    NARWHAL_OT_set_display_mode,
    NARWHAL_MT_recent_files,
    NARWHAL_OT_run_command,
    NARWHAL_OT_cancel,
    NARWHAL_OT_repeat_last,
    NARWHAL_OT_toggle_layer,
    NARWHAL_OT_statusbar_click,
    NARWHAL_OT_menu_todo,
    NARWHAL_OT_run_command_option,
    NARWHAL_OT_maximize_viewport,
    NARWHAL_OT_units_dialog,
    NARWHAL_OT_track_mouse,
    NARWHAL_OT_osnap_toggle,
    NARWHAL_OT_osnap_flag,
    # `NARWHAL_PT_osnap` is deliberately NOT registered. the reference CAD application's Osnap control is
    # docked bottom-LEFT (the reference layout description), and it is drawn there now by
    # `draw_osnap_panel`. the reference CAD application's RIGHT container holds Layers / Properties / Display /
    # Help and no Osnap, so a second copy on the right would be a panel the reference CAD application does not
    # have. The class is kept in the file, unregistered, because its docstring records
    # the measured reason `UI` is the only region that can host a Panel at all -- which
    # is exactly why the overlay had to be built instead.
    #
    # SAME TREATMENT, SAME REASON: `NARWHAL_PT_toolpalette` and `NARWHAL_PT_layers`
    # are NOT registered either, preventing Blender category tabs over the viewport.
    #
    # THOSE TABS WERE application-defined. Blender draws a vertical category tab in a VIEW_3D's UI
    # region for every `bl_category` that has a registered Panel, so these two classes
    # WERE the "Tools" and "Layers" chips -- exactly the two in the screenshot. I had
    # previously recorded that they were Blender's collapsed-region chevrons with "no
    # Python lever" and that only patching Blender's C source could remove them. That
    # was wrong twice: wrong about the mechanism (they are not action zones) and wrong
    # about the cause (they are not Blender's). A rebuild was never needed.
    #
    # Both are superseded scaffolding:
    #  * `NARWHAL_PT_toolpalette` is replaced by the toolbar strip, 16-tab row, and
    #   37-button left Sidebar.
    #  * `NARWHAL_PT_layers` -- "one of the Right Container's panels". The Right
    #   Container now exists, so the Layers panel belongs IN it, not in a
    #   Blender N-panel tab on top of a viewport.
    # Kept in the file, unregistered, for their measured docstrings -- same as
    # `NARWHAL_PT_osnap`.
)


# (keymap name, space_type, key, modifiers, operator, command property)
#
# The keymap NAME matters: addon keymaps are looked up by name against the
# active keyconfig, so an invented name (e.g. "THE REFERENCE CAD APPLICATION Shortcuts") registers
# without error but is never consulted and the shortcuts silently never fire.
# "3D View" and "Window" both exist in the default keyconfig.
SHORTCUTS = (
    ("3D View", "VIEW_3D", "ESC", {}, "narwhal.cancel", None),
    ("3D View", "VIEW_3D", "RET", {}, "narwhal.repeat_last", None),
    ("3D View", "VIEW_3D", "SPACE", {}, "narwhal.repeat_last", None),
    ("3D View", "VIEW_3D", "DEL", {}, "narwhal.run_command", "Delete"),
    ("Window", "EMPTY", "S", {"ctrl": True}, "narwhal.run_command", "Save"),
    ("Window", "EMPTY", "O", {"ctrl": True}, "narwhal.run_command", "Open"),
    # Verified (the reference CAD application documentation) "New Ctrl (CMD) + N". Claimed here so it
    # does not fall through to Blender's own File > New, which would replace the
    # Blender scene behind the document's back.
    ("Window", "EMPTY", "N", {"ctrl": True}, "narwhal.run_command", "New"),
    # Verified (the File menu resource) "E&xit Ctrl+Q". Claimed so it does not
    # reach Blender's own quit, whose prompt is about the .blend scene.
    ("Window", "EMPTY", "Q", {"ctrl": True}, "narwhal.run_command", "Exit"),
    # Verified (SetDisplayMode) "Ctrl (CMD) + Alt + G Ghosted, R Rendered,
    # S Shaded, W Wireframe, X XRay" -- the active viewport's display mode.
    ("Window", "EMPTY", "W", {"ctrl": True, "alt": True}, "narwhal.run_command",
     "SetDisplayMode Wireframe"),
    ("Window", "EMPTY", "S", {"ctrl": True, "alt": True}, "narwhal.run_command",
     "SetDisplayMode Shaded"),
    ("Window", "EMPTY", "R", {"ctrl": True, "alt": True}, "narwhal.run_command",
     "SetDisplayMode Rendered"),
    ("Window", "EMPTY", "G", {"ctrl": True, "alt": True}, "narwhal.run_command",
     "SetDisplayMode Ghosted"),
    ("Window", "EMPTY", "X", {"ctrl": True, "alt": True}, "narwhal.run_command",
     "SetDisplayMode XRay"),
    # Verified (the reference CAD application documentation) "Print Ctrl (CMD) + P".
    ("Window", "EMPTY", "P", {"ctrl": True}, "narwhal.run_command", "Print"),
    # the reference CAD application's undo/redo shortcuts. Verified -- the reference CAD application documentation lists
    # "Ctrl (CMD) + Z" against Edit > Undo and "Ctrl (CMD) + Y" against
    # Edit > Redo.
    #
    # These MUST be claimed rather than left to Blender. Blender's Ctrl+Z runs
    # `ed.undo`, which reverts the Blender scene and knows nothing about the
    # .3dm document -- so it would silently desynchronise the two: the viewport
    # would show one model and the saved file another. The suppression walk
    # deliberately ignores Ctrl combos ("the rest are harmless"), which was true
    # until undo existed; binding ours in the addon keyconfig takes precedence.
    #
    # Shift+Ctrl+Z is Blender's redo. It is bound here too, to Redo, so the
    # habit does not fall through to `ed.redo`.
    ("Window", "EMPTY", "Z", {"ctrl": True}, "narwhal.run_command", "Undo"),
    ("Window", "EMPTY", "Y", {"ctrl": True}, "narwhal.run_command", "Redo"),
    ("Window", "EMPTY", "Z", {"ctrl": True, "shift": True},
     "narwhal.run_command", "Redo"),
    ("Window", "EMPTY", "F1", {}, "narwhal.run_command", "Help"),
    # the reference CAD application's CLIPBOARD shortcuts. Verified (the reference CAD application documentation, which carries
    # all three): "Cut ... Edit > Cut ... Ctrl (CMD) + X", "CopyToClipboard ...
    # Edit > Copy ... Ctrl (CMD) + C", and Paste on the same page.
    #
    # Blender owns Ctrl+C/Ctrl+V as `view3d.copybuffer`/`view3d.pastebuffer`, which
    # duplicate only display objects and never touch the document. Those operators remain
    # suppressed; these bindings run the document-fragment commands instead. Text editors
    # and the bug/print fields intercept their own clipboard keys earlier in the modal path.
    ("Window", "EMPTY", "C", {"ctrl": True}, "narwhal.run_command",
     "CopyToClipboard"),
    ("Window", "EMPTY", "V", {"ctrl": True}, "narwhal.run_command", "Paste"),
    ("Window", "EMPTY", "X", {"ctrl": True}, "narwhal.run_command", "Cut"),
    # Join. Verified: the reference CAD application documentation lists "Ctrl (CMD) + J" against
    # Edit > Join. Blender's Ctrl+J is `object.join`, which welds Blender MESHES
    # and knows nothing about the .3dm document -- exactly the desynchronisation
    # trap the Ctrl+Z note above describes, so this one is claimed for the same
    # reason.
    ("Window", "EMPTY", "J", {"ctrl": True}, "narwhal.run_command", "Join"),
    # Control points on/off. Verified: the reference CAD application documentation lists F10 against
    # Edit > Control Points > Control Points On, and F11 against Control Points
    # Off (whose page is the PointsOff anchor of the same topic).
    ("Window", "EMPTY", "F10", {}, "narwhal.run_command", "PointsOn"),
    ("Window", "EMPTY", "F11", {}, "narwhal.run_command", "PointsOff"),
    # Ortho. Verified (the reference CAD application documentation): "The Ortho command, clicking Ortho in the
    # status bar, and the F8 key, are all toggles."
    ("Window", "EMPTY", "F8", {}, "narwhal.run_command", "Ortho"),
)

# Characters the user can "just start typing" to begin a command.
# Letters a-z only: every command in the table starts with a letter, and
# binding digits would clobber Blender's 1/2/3/7 viewport-navigation shortcuts
# (worth keeping). Add digits here for full the reference CAD application digit-typing if ever wanted.
TYPEABLE = [chr(c) for c in range(ord("a"), ord("z") + 1)]

# Blender key types are uppercase ("A".."Z"); TYPEABLE holds the lowercase
# characters the buffer receives. The suppression walk needs the key
# types, so keep both spellings next to each other.
TYPEABLE_UPPER = [c.upper() for c in TYPEABLE]

# the modifier combinations the reference CAD application's right-button navigation uses. A Blender
# keymap item matches an EXACT modifier set, so a bare RIGHTMOUSE binding does
# NOT fire for Ctrl+RIGHTMOUSE -- every combination needs its own item. Derived
# from the table in `navigate.py` so the two cannot drift apart.
NAV_MODIFIERS = (
    {},
    {"shift": True},
    {"ctrl": True},
    {"alt": True},
    {"ctrl": True, "shift": True},
    {"shift": True, "alt": True},
    {"ctrl": True, "alt": True},
    {"ctrl": True, "shift": True, "alt": True},
)


_keymap_entries = []  # (keymap, keymap_item) pairs we own


def _register_keymaps():
    """Install the reference CAD application's shortcuts into the addon keyconfig."""
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:  # can happen in some headless configurations
        return
    keymaps = {}

    def _keymap(name, space_type):
        km = keymaps.get(name)
        if km is None:
            km = kc.keymaps.new(name=name, space_type=space_type)
            keymaps[name] = km
        return km

    def _add(km_name, space_type, key, mods, idname, prop, value):
        km = _keymap(km_name, space_type)
        kmi = km.keymap_items.new(idname, key, "PRESS", **mods)
        if prop is not None:
            # operator properties live on kmi.properties, never on the kmi
            setattr(kmi.properties, prop, value)
        _keymap_entries.append((km, kmi))

    for km_name, space_type, key, mods, idname, command in SHORTCUTS:
        _add(km_name, space_type, key, mods, idname,
             "command" if command is not None else None, command)

    # "just start typing" — bind each letter to begin_typing(char=...).
    # Letters are uppercase keys in Blender (Shift is a modifier); we bind the
    # unshifted key and pass the lowercase character so the buffer holds
    # lowercase command names.
    # bind the SHIFTED key too, with the same lowercase char. the reference CAD application's
    # command line is case-insensitive (BOX/Box/box), so a capital must do
    # exactly what its lowercase twin does. disabled 14 of Blender's
    # Shift+letter shortcuts (Shift+D duplicate, Shift+X delete, ...), so
    # without this binding Shift+letter is a dead key.
    # bind into BOTH the viewport ("3D View") and the command bar
    # ("Info") keymaps. Keymaps are region-scoped, so without the "Info" copy
    # typing only started a command when the pointer happened to hover a
    # viewport. "Info" is the real keymap for the INFO area (space_type=INFO,
    # region=WINDOW), so it is in the active keyconfig and fires over the bar.
    for km_name, space_type in (("3D View", "VIEW_3D"), ("Info", "INFO")):
        for ch in TYPEABLE:
            _add(km_name, space_type, ch.upper(), {}, "narwhal.begin_typing",
                 "char", ch.lower())
            _add(km_name, space_type, ch.upper(), {"shift": True},
                 "narwhal.begin_typing", "char", ch.lower())

    # the reference CAD application navigates by dragging the RIGHT button. One binding per
    # modifier combination, because a keymap item matches an EXACT modifier set:
    # a bare RIGHTMOUSE item does not fire for Ctrl+RIGHTMOUSE. The operator
    # itself decides what each combination means (and whether the pane is
    # perspective), so this list only has to cover the combinations the reference CAD application uses.
    for mods in NAV_MODIFIERS:
        _add("3D View", "VIEW_3D", "RIGHTMOUSE", mods, "narwhal.navigate",
             None, None)

    # The MIDDLE button, same shape and the same reason: one item per modifier
    # combination, because a keymap item matches an EXACT modifier set. the reference CAD application's
    # MMB tables use Ctrl, Shift and Alt (the reference CAD application documentation), and NAV_MODIFIERS
    # already enumerates exactly those combinations.
    #
    # These MUST be claimed. Blender's MMB is `view3d.rotate` and its Shift/Ctrl
    # variants are pan and zoom -- close enough to look right and wrong in the
    # details, which is the same trap documents for the right button.
    for mods in NAV_MODIFIERS:
        _add("3D View", "VIEW_3D", "MIDDLEMOUSE", mods, "narwhal.navigate_mmb",
             None, None)

    # Ctrl+wheel zooms about the VIEW CENTRE (the bare wheel zooms about the
    # cursor -- `apply_navigation_preferences`). Verified (the reference CAD application documentation, Keys):
    # "Ctrl (CMD) + Mouse wheel -- Zoom In and Out (Focus on view center)". Blender's
    # own Ctrl+wheel PANS sideways. `use_cursor_init=False` is what makes Blender's
    # zoom ignore the cursor (view3d_navigate.cc clears ZOOM_TO_MOUSE without it).
    for key, delta in (CTRL_WHEEL_ZOOM_KEYS):
        km = _keymap("3D View", "VIEW_3D")
        kmi = km.keymap_items.new("view3d.zoom", key, "PRESS", ctrl=True)
        kmi.properties.delta = delta
        kmi.properties.use_cursor_init = False
        _keymap_entries.append((km, kmi))


#: 's Ctrl+wheel bindings (key, zoom step): in = closer, out = farther, the same
#: event types Blender's bare-wheel zoom uses
CTRL_WHEEL_ZOOM_KEYS = (("WHEELINMOUSE", 1), ("WHEELOUTMOUSE", -1))

#: Wheel zoom focuses on the initial cursor location. The bare wheel uses Blender's
#: `view3d.zoom`,
#: which zooms about the cursor when this preference is on (Verified: the point under
#: the cursor stayed within 0.1 px of it; off, it moved ~50 px).
ZOOM_TO_MOUSE = True


def apply_navigation_preferences():
    """Set Blender's input preferences the way the reference CAD application navigates. Preferences
    are session-only in this app (`launch/bootstrap.py`), so nothing is written to the
    user's Blender settings."""
    try:
        inputs = bpy.context.preferences.inputs
        if inputs.use_zoom_to_mouse != ZOOM_TO_MOUSE:
            inputs.use_zoom_to_mouse = ZOOM_TO_MOUSE
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] could not set the wheel to zoom at the cursor: {e!r}")
        return False


# ---------------------------------------------------------------------------
# silence Blender's competing bare-letter shortcuts
#
# Blender's G/S/R/X (transform.translate/resize/rotate, object.delete) live in
# the "Object Mode" keymap, which is a DIFFERENT keymap from our "3D View"
# bindings. Whether ours win is decided by C-level handler order Python cannot
# inspect — and a stray Blender transform would move the mesh WITHOUT touching
# the rhino3dm document, so Save would write stale coordinates. Rather than
# gamble on precedence, switch the competitors off. Then it does not matter
# who would have won.
#
# Measured in a real GUI session (the live keymap checks, and the review
# audit that produced these exact numbers):
#  * kmi.active is writable on wm.keyconfigs.user items and survives update
#  * 29 competing items: 21 in "Object Mode", 8 in "3D View", 0 in
#   "Object Non-modal". ALL 29 are active in a factory config — none are
#   off by default.
#  * They collapse to 25 recorded DESCRIPTORS because 4 (name,key,idname)
#   triples are shared by two items each that differ only in their operator
#  * Their operator properties distinguish A/object.select_all (select vs deselect),
#    X/object.delete,
#   M/wm.call_menu, H/object.hide_view_set. Suppress disables both sharers
#   and restore re-enables both, so this is symmetric *today*; it would stop
#   being exact only if a user config left one sharer off.
#  * 14 of the 29 are SHIFT+letter (Shift+A add menu, Shift+D duplicate,
#   Shift+X delete, Shift+C view_all, Shift+B zoom border, ...): the
#   predicate deliberately ignores `shift` — see _is_competing_letter.
#  * "Object Mode" does NOT exist under `blender -b` (per-mode keymaps are
#   created lazily on region init) -> the code must tolerate missing keymaps
#   and the real assertion has to run in a GUI window.
#  * launch/bootstrap.py pins use_preferences_save=False, so none of this
#   persists into the user's real Blender prefs
# ---------------------------------------------------------------------------

SUPPRESS_TARGET_KEYMAPS = ("Object Mode", "3D View", "Object Non-modal")

_suppressed = []  # (keymap name, key, idname) descriptors of items we switched off
_suppress_done = False


def _is_competing_letter(kmi):
    """An A-Z key press, with or without Shift, bound to a non-reference CAD application operator.

    `shift` is deliberately NOT excluded: 14 of the 29 competitors are
    Shift+letter (Shift+A add menu, Shift+D duplicate, Shift+X delete,
    Shift+C view-all, Shift+B zoom-border), and a the reference CAD application clone wants those gone
    too. Ctrl/Alt/OS combos ARE excluded — Ctrl+S/Ctrl+O are ours and the rest
    are harmless.

    Consequence to be aware of: with Blender's Shift+letter actions disabled and
    our own bindings being unshifted-only, Shift+letter is currently a dead key.
    the reference CAD application types a capital letter into the command line instead.
    """
    return (kmi.map_type == "KEYBOARD"
            and kmi.type in TYPEABLE_UPPER
            and not kmi.ctrl and not kmi.alt and not kmi.oskey
            and not kmi.idname.startswith("narwhal."))


def _is_competing_destructive(kmi):
    """A non-letter key bound to a Blender operator that would edit geometry.

    Blender's `object.delete` is bound to **DEL** in the `Object Mode` keymap and bypasses
    keymap -- twice -- and `_is_competing_letter` could not see it, because that
    predicate matches `kmi.type in TYPEABLE_UPPER` and `DEL` is not a letter. So the
    Delete key removed the BLENDER object and left the .3dm entry untouched, and the
    next `rebuild_display` (which every control-point move ends with) recreated it
    from the document. The object was never deleted at all; it only looked deleted
    until something forced a rebuild.

    This is the same class of hazard the letter predicate exists for -- "a stray
    Blender edit that does not reach the document, so Save writes stale
    coordinates" -- so it is suppressed the same way, through the same descriptor
    recording, and restored by the same code.

    Ctrl/Alt combinations are excluded for the same reason as there: Ctrl+Z is ours
    and the rest are harmless.
    """
    return (kmi.map_type == "KEYBOARD"
            and kmi.type in DESTRUCTIVE_KEYS
            and not kmi.ctrl and not kmi.alt and not kmi.oskey
            and not kmi.idname.startswith("narwhal."))


#: Non-letter keys whose Blender bindings would change geometry behind the
#: document's back. `DEL`/`BACK_SPACE` are Blender's `object.delete`; both are
#: the reference CAD application's Delete (the reference CAD application documentation: "Press the Delete key").
DESTRUCTIVE_KEYS = ("DEL", "BACK_SPACE")

#: Blender OPERATORS that add or remove objects without telling the rhino3dm
#: document. Matched by operator name rather than by key, deliberately: a rebind
#: changes the key but not the operator, and the operator is what does the damage.
#:
#: Blender's `view3d.copybuffer` / `view3d.pastebuffer` duplicate only the display
#: object and never update the document, leaving no corresponding `File3dm` entry.
#: `_BooleanCmd` then asks `ctx.geom_by_name` for it, gets None, and reports "No
#: solid named ...". It would not have been SAVED either, which is the worse half.
#:
#: Verified (a focused runtime check, in a real window because headless loads
#: only ~108 of 280 keymaps): after our existing suppression, `3D View Ctrl+C ->
#: view3d.copybuffer`, `3D View Ctrl+V -> view3d.pastebuffer`, `Object Mode Alt+D ->
#: object.duplicate_move_linked` and `Object Mode Ctrl+J -> object.join` were all
#: still ACTIVE, while `Shift+D` duplicate and `DEL`/`X` delete were already off.
#:
#: This also corrects `_is_competing_letter`'s claim that Ctrl combinations "are
#: harmless" apart from Ctrl+Z. Three of the four above are Ctrl or Alt, which is
#: exactly why they slipped through a predicate that only looks at bare letters.
DOCUMENT_BYPASS_OPERATORS = (
    "view3d.copybuffer",
    "view3d.pastebuffer",
    "object.duplicate_move_linked",
    "object.duplicate_move",
    "object.join",
)

#: Operator PREFIXES whose operators edit object data. Broader than the explicit list
#: above, and it exists because widening the audit found fourteen more siblings the
#: moment Ctrl/Alt combinations were included -- among them `Alt+G`
#: (`object.location_clear`), `Alt+R` (`object.rotation_clear`), `Alt+S`
#: (`object.scale_clear`) and `Ctrl+M` (`transform.mirror`). Each of those MOVES or
#: RESHAPES the Blender object while the `.3dm` keeps the old coordinates, so Save
#: would write a model that does not match the screen.
#:
#: Matched by prefix rather than enumerated, so Blender adding another one is covered
#: by default. `the live-window UI checks` imports these two tuples instead of restating them,
#: so the audit and the suppression cannot drift apart.
DOCUMENT_MUTATOR_PREFIXES = (
    "object.", "mesh.", "curve.", "transform.",
    "view3d.copybuffer", "view3d.pastebuffer",
)

#: Operators matching :data:`DOCUMENT_MUTATOR_PREFIXES` that are nonetheless SAFE --
#: they change selection or mode, not geometry. Keeping them live matters: the
#: selection is how every the reference CAD application command takes its input.
DOCUMENT_MUTATOR_SAFE = (
    "object.select", "object.mode_set", "object.hide_view_clear",
    "object.editmode_toggle",
)

#: Keymaps whose bindings can actually fire in our app. The suppression is limited to
#: these on purpose: the same operators appear in Armature/Mesh/Sequencer/Node
#: keymaps, and we never enter those modes, so disabling them would be over-broad --
#: and over-broad suppression is harder to reason about than targeted suppression.
DOCUMENT_BYPASS_KEYMAPS = ("3D View", "Object Mode", "Object Non-modal")


def _claimed_key_combos():
    """``{(key, ctrl, shift, alt)}`` that WE bind, from :data:`SHORTCUTS`.

    A combination we claim ourselves must not be suppressed -- our addon keymap takes
    precedence over Blender's, so disabling Blender's copy as well would be harmless
    but pointless, and disabling application-defined would be a bug. Computed from the table rather
    than hand-listed so claiming a new key excuses it automatically.
    """
    out = set()
    for _km, _sp, key, mods, _idn, _cmd in SHORTCUTS:
        out.add((key, bool(mods.get("ctrl")), bool(mods.get("shift")),
                 bool(mods.get("alt"))))
    return out


def _is_competing_document_bypass(kmi, keymap_name):
    """A Blender binding that would edit object data outside the document.

    See :data:`DOCUMENT_BYPASS_OPERATORS` for the affected bindings,
    and :data:`DOCUMENT_MUTATOR_PREFIXES` for the fourteen siblings that turned up as
    soon as modifier combinations were audited at all.

    Matched on the OPERATOR, so a user rebinding Ctrl+C elsewhere is still covered, and
    scoped to :data:`DOCUMENT_BYPASS_KEYMAPS` so we do not reach into Armature, Mesh,
    Sequencer or Node keymaps for modes we never enter -- over-broad suppression is
    harder to reason about than targeted suppression.

    NOT restricted by modifier, unlike `_is_competing_letter`: the whole point is that
    these ARE modifier combinations, and that predicate's claim that Ctrl combos "are
    harmless" is what let Ctrl+C/Ctrl+V through.
    """
    if keymap_name not in DOCUMENT_BYPASS_KEYMAPS:
        return False
    if kmi.map_type != "KEYBOARD" or kmi.idname.startswith("narwhal."):
        return False
    if kmi.idname in DOCUMENT_BYPASS_OPERATORS:
        return True
    if kmi.idname.startswith(DOCUMENT_MUTATOR_SAFE):
        return False
    if not kmi.idname.startswith(DOCUMENT_MUTATOR_PREFIXES):
        return False
    # A BARE letter is `_is_competing_letter`'s job; leave it there so the two
    # predicates do not both claim the same item and confuse the restore bookkeeping.
    if (kmi.type in TYPEABLE_UPPER
            and not (kmi.ctrl or kmi.alt or kmi.oskey)):
        return False
    return (kmi.type, bool(kmi.ctrl), bool(kmi.shift),
            bool(kmi.alt)) not in _claimed_key_combos()


def _is_competing_rmb(kmi, keymap_name):
    """A RIGHTMOUSE binding that would fight the reference CAD application's right-button navigation.

    the reference CAD application drags the view with the right button; Blender uses it for the
    context menu, the 3D cursor, lasso select and -- worst -- a Shift+RMB drag
    bound to ``transform.translate``, which would MOVE geometry in Blender
    without updating the rhino3dm document, so Save would write stale
    coordinates. That is the same data-integrity hazard the shortcut suppression
    closes, so navigation cannot simply be added alongside these bindings.

    The list of (keymap, operator) pairs is measured, and lives in the pure
    ``navigate`` module so it is unit-tested (spike/test_navigate.py) and shared with
    runtime validation.
    """
    return (kmi.map_type == "MOUSE"
            and kmi.type == "RIGHTMOUSE"
            and not kmi.idname.startswith("narwhal.")
            and nav.is_rmb_competitor(keymap_name, kmi.idname))


def _target_keymaps():
    """The target keymaps, as a {name: KeyMap} dict (absent ones skipped).

    NOTE: `keyconfigs.user` is a bpy_struct — only its `.keymaps` COLLECTION
    supports `.get(name)`. `user.get(name)` raises TypeError ("this type
    doesn't support IDProperties"). Verified in focused runtime checks.
    """
    kms = bpy.context.window_manager.keyconfigs.user.keymaps
    return {name: kms.get(name) for name in SUPPRESS_TARGET_KEYMAPS}


def suppress_blender_shortcuts() -> bool:
    """Disable Blender's bare-letter shortcuts in the keymaps we sit in.

    Returns True once every target keymap has been inspected at least once.
    "Object Mode" is created lazily on first region init, so a single pass on
    the first tick may miss it; the caller retries on later ticks until all
    targets exist (or a cap is reached).

    The items are recorded by *descriptor* (keymap name, key, idname), NOT by
    Python KeyMapItem wrapper: Blender re-materialises the merged user keymaps
    (e.g. on keyconfigs.update), which invalidates stale wrappers — writing
    through one is a silent no-op, and holding one across unregister is a
    use-after-free (segfault, observed). A descriptor re-resolves against the
    live keymap each pass, so suppress/restore stay exact inverses.
    """
    global _suppress_done
    pending = []
    for name, km in _target_keymaps().items():
        if km is None:
            pending.append(name)
            continue
        for kmi in km.keymap_items:
            # adds the RIGHTMOUSE competitors to the same walk, so they are
            # recorded and restored by exactly the same descriptor machinery.
            if ((_is_competing_letter(kmi) or _is_competing_rmb(kmi, name)
                    or _is_competing_destructive(kmi)
                    or _is_competing_document_bypass(kmi, name))
                    and kmi.active):
                kmi.active = False
                desc = (name, kmi.type, kmi.idname)
                if desc not in _suppressed:
                    _suppressed.append(desc)
    _suppress_done = not pending
    return _suppress_done


def _start_suppression_timer():
    """Run suppress_blender_shortcuts on later ticks until every target
    keymap exists (or the cap is hit), mirroring the viewport state machine.
    Headless mode never gets a window, so the cap matters there too.

    If the cap is hit with targets still missing, report it: silently giving up would
    re-open the data-integrity hole this suppression closes (a
    Blender transform moving the mesh without updating the rhino3dm document).
    Expected headlessly — per-mode keymaps never materialise under `-b` — so the
    warning is suppressed in background mode to keep test logs honest.
    """
    state = {"tick": 0}
    cap = 10

    def tick():
        state["tick"] += 1
        try:
            done = suppress_blender_shortcuts()
        except Exception as e:  # noqa: BLE001
            print(f"[narwhal3d] shortcut suppression failed: {e!r}")
            done = False
        if done:
            return None
        if state["tick"] >= cap:
            if not bpy.app.background:
                missing = [n for n, km in _target_keymaps().items() if km is None]
                print(f"[narwhal3d] WARNING: gave up suppressing host shortcuts "
                      f"after {cap} tries; keymap(s) never appeared: {missing}. "
                      f"Host letter shortcuts may still fire and can move geometry "
                      f"without updating the .3dm document.")
            return None
        return 0.4

    bpy.app.timers.register(tick, first_interval=0.2)


# ---------------------------------------------------------------------------
# BLENDER'S OWN VIEWPORT SIDEBAR PANELS
#
# Blender's VIEW_3D UI region carries host-specific panels and category tabs that do
# not belong in Narwhal 3D's viewport. The dedicated right dock replaces that sidebar,
# so all host sidebar panels are suppressed.
#
# They are UNREGISTERED rather than hidden, and put back on unregister, which is
# the same record-what-you-changed-and-undo-exactly-that discipline
# `suppress_blender_shortcuts` uses. Overriding each `poll` was the alternative and
# it is worse: 82 monkey-patched classmethods that Blender may re-evaluate at any
# time, versus one list.
# ---------------------------------------------------------------------------

#: The categories Blender puts in the VIEW_3D sidebar. Ours (`Osnap`, `Tools`,
#: `Layers`) are deliberately NOT here -- suppression is by category so that a
#: Blender version adding a panel to an existing tab is covered without a code
#: change, while a new tab of OUR OWN can never be caught by it.
BLENDER_SIDEBAR_CATEGORIES = ("Tool", "View", "Item", "Animation")

#: What we unregistered, innermost child first. Restored in reverse.
_suppressed_panels = []


def _blender_sidebar_panels():
    """Blender's VIEW_3D sidebar panel classes, deepest CHILD first.

    Child-first matters: `bl_parent_id` is resolved by NAME, so unregistering a
    parent while its children are still registered leaves Blender holding dangling
    parent references. Depth is computed by walking the parent chain rather than
    assumed to be one level -- there are three-level chains in stock Blender
    (`..._brush_falloff_frontface` -> `..._brush_falloff` -> `..._brush_settings`).
    """
    found = {}
    for name in dir(bpy.types):
        cls = getattr(bpy.types, name, None)
        if not isinstance(cls, type):
            continue
        try:
            if not issubclass(cls, bpy.types.Panel):
                continue
        except TypeError:                       # not a class we can test
            continue
        if name.startswith("NARWHAL_"):           # never our own
            continue
        if getattr(cls, "bl_space_type", None) != "VIEW_3D":
            continue
        if getattr(cls, "bl_region_type", None) != "UI":
            continue
        if (getattr(cls, "bl_category", "") or "") not in BLENDER_SIDEBAR_CATEGORIES:
            continue
        found[name] = cls

    def depth(cls):
        d, seen = 0, set()
        pid = getattr(cls, "bl_parent_id", "") or ""
        while pid and pid not in seen:
            seen.add(pid)
            d += 1
            parent = found.get(pid)
            pid = (getattr(parent, "bl_parent_id", "") or "") if parent else ""
        return d

    return sorted(found.values(), key=depth, reverse=True)


def suppress_blender_sidebar_panels():
    """Unregister Blender's own VIEW_3D sidebar panels. Returns how many went.

    Idempotent: a second call finds nothing left and returns 0, so it is safe from a
    timer. A class that refuses to unregister is skipped and NOT recorded, which
    keeps the restore exactly as wide as the suppression and avoids keymap asymmetry.
    """
    gone = 0
    for cls in _blender_sidebar_panels():
        try:
            bpy.utils.unregister_class(cls)
        except Exception:  # noqa: BLE001
            continue
        _suppressed_panels.append(cls)
        gone += 1
    return gone


def _restore_blender_sidebar_panels():
    """Put back exactly what `suppress_blender_sidebar_panels` removed.

    Reverse order, so a parent is registered before the children that name it.
    """
    while _suppressed_panels:
        cls = _suppressed_panels.pop()
        try:
            bpy.utils.register_class(cls)
        except Exception:  # noqa: BLE001
            pass


def blender_sidebar_panel_count():
    """How many of Blender's sidebar panels are still registered (for the gate)."""
    return len(_blender_sidebar_panels())


def _restore_blender_shortcuts():
    """Re-enable every item suppress_blender_shortcuts switched off.

    Descriptor-based (see suppress_blender_shortcuts): re-resolve each recorded
    (name, key, idname) against the *live* keymap, so a stale Python wrapper can
    neither no-op the restore nor cause a use-after-free. Only items in the
    recorded set are touched, so this is the exact inverse of suppress.

    The recorded descriptor set is the ONLY filter here, deliberately. This used
    to also re-test `_is_competing_letter`, which silently made restore narrower
    than suppress: RIGHTMOUSE competitors were once added to suppression without being
    added to restoration, so they were disabled but never re-enabled ("'3D View' RIGHTMOUSE
    (view3d.select_lasso) still inactive after unregister"). Whatever we
    recorded is exactly what we undo, so any future predicate stays symmetric
    for free.
    """
    global _suppress_done
    for name, km in _target_keymaps().items():
        if km is None:
            continue
        for kmi in km.keymap_items:
            if (not kmi.active
                    and (name, kmi.type, kmi.idname) in _suppressed):
                kmi.active = True
    _suppressed.clear()
    _suppress_done = False


def _unregister_keymaps():
    for km, kmi in _keymap_entries:
        try:
            km.keymap_items.remove(kmi)
        except Exception:  # noqa: BLE001
            pass
    _keymap_entries.clear()


_cmdbar_handlers = []  # (space type, handler_id) pairs registered here


def _attach_cmdbar_handler():
    """Register the command-bar draw handler on the INFO **space type**.

    ``Space.draw_handler_add`` is a CLASSMETHOD: registering through any
    instance already applies to every space of that type. The old code looked
    up the INFO *area* first and bailed when it did not exist yet -- which is
    exactly what happened after the command bar moved to a top strip that
    the GUI build only creates on a later timer tick (~2s), long after this
    runs (~0.2s). Result: the handler was never registered, the strip rendered
    as Blender's stock dark Info editor, and the command bar drew nothing.

    Registering on ``bpy.types.SpaceInfo`` needs no existing area, so drawing no
    longer depends on layout timing and that whole class of ordering bug is gone.
    (Verified: the type-level add/remove works even with zero INFO areas.)
    """
    if _cmdbar_handlers:
        return True  # already registered (type-level, so once is enough)
    handler_id = bpy.types.SpaceInfo.draw_handler_add(
        draw_command_bar, (), "WINDOW", "POST_PIXEL")
    _cmdbar_handlers.append((bpy.types.SpaceInfo, handler_id))
    # the LEFT DOCK BAND is the other INFO area, so its handler goes on the
    # same space type and in the same place. Both handlers run for both areas and
    # each establishes which one it is in by geometry -- the band is tall and narrow,
    # the bar wide and short. Registered here rather than on area creation for the
    # reason in this function's docstring: the band is carved on a later timer tick,
    # and waiting for an area to exist would recreate the same ordering bug.
    band_id = bpy.types.SpaceInfo.draw_handler_add(
        draw_left_band, (), "WINDOW", "POST_PIXEL")
    _cmdbar_handlers.append((bpy.types.SpaceInfo, band_id))
    # and the RIGHT DOCK BAND is the third. Same space type, same place; it
    # tells itself apart from the other two with `_band_side`, which is why that
    # helper exists rather than reusing `_info_area_is_band` (true of both bands).
    right_id = bpy.types.SpaceInfo.draw_handler_add(
        draw_right_band, (), "WINDOW", "POST_PIXEL")
    _cmdbar_handlers.append((bpy.types.SpaceInfo, right_id))
    return True


_view_label_handlers = []   # (space type, handler_id) pairs for pane labels

#: the live preview's cursor, as a WORLD point, plus the pane it came from.
#: The command modal updates this on mouse-move; `draw_command_preview` reads it.
#: Kept here rather than on `app_state` so the pure command/state layer stays free
#: of anything this view-only.
_preview_cursor = None
_preview_pane = None
_preview_handlers = []


def set_preview_cursor(point, pane_key=None):
    """Record where the cursor is, in world space, for the running preview."""
    global _preview_cursor, _preview_pane
    if point == _preview_cursor and pane_key == _preview_pane:
        return False
    _preview_cursor = tuple(point) if point is not None else None
    _preview_pane = pane_key
    return True


def clear_preview_cursor():
    """Forget the cursor, so no stale preview survives the command ending."""
    return set_preview_cursor(None, None)


# ---------------------------------------------------------------------------
# pick constraints -- distance, <angle, Tab direction/circle lock
# ---------------------------------------------------------------------------
#
# Every point prompt with a previous point accepts a precise typed length, so the rule
# is applied here between the cursor
# and the command, rather than taught to each command. The rules and their sources
# are in `pickconstraint`.

_pick_constraint = pickconstraint.PickConstraint()
_pick_tokens = __import__("itertools").count(1)
#: the last cursor point BEFORE the constraint (snapped if a snap was in range), so
#: arming a constraint can re-place the marker without waiting for the mouse to move
_constraint_cursor = None


def _constraint_prompt(cmd):
    """``(key, base)`` when `cmd` is at a POINT prompt that has a previous point.

    Only POINT prompts: at a DISTANCE or NUMBER prompt (Circle's radius, Cylinder's
    height) a typed number IS the answer and must reach the command untouched. A
    prompt with no previous point has nothing to measure from, so a bare number there
    also goes to the command, which explains itself.
    """
    if cmd is None:
        return None
    if getattr(cmd, "pending_option", None) is not None:
        return None
    if getattr(cmd, "variable_points", False):
        kind = commands.POINT
    else:
        steps = getattr(cmd, "steps", [])
        idx = getattr(cmd, "step_index", 0)
        if idx >= len(steps):
            return None
        kind = steps[idx][1]
    if kind != commands.POINT:
        return None
    last = getattr(cmd, "_last_point", None)
    base = last() if callable(last) else None
    if base is None:
        return None
    # NOT id(cmd): CPython reuses an id once the object is freed, so the next Line
    # could inherit the last one's constraint. A per-command token cannot collide.
    token = _cmd_token(cmd)
    if token is None:
        return None
    key = (token, len(getattr(cmd, "args", ())), getattr(cmd, "step_index", 0))
    return key, base


def _cmd_token(cmd):
    """A per-RUN identity for a command object (never reused, unlike `id`)."""
    token = getattr(cmd, "_pick_token", None)
    if token is None:
        token = next(_pick_tokens)
        try:
            cmd._pick_token = token
        except Exception:  # noqa: BLE001
            return None
    return token


def _typed_constraint(cmd):
    """The constraint the command line would arm if Enter were pressed NOW, or None.

    A number or ``<angle`` already present in the command line shapes the marker and
    preview before Enter is pressed.
    Built on a COPY of the armed constraint, so a typed angle combines with an
    armed distance (and vice versa) exactly as Enter would combine them -- and
    nothing is armed until Enter is actually pressed.
    """
    import copy as _copy

    prompt = _constraint_prompt(cmd)
    if prompt is None:
        return None
    parsed = pickconstraint.parse(app_state.input_buffer)
    if parsed is None or parsed[0] == "error":
        return None
    key, base = prompt
    c = _copy.copy(_pick_constraint)
    if not c.active_for(key):
        c = pickconstraint.PickConstraint()
    plane = active_cplane()
    kind, value = parsed
    if kind == "distance":
        c.set_distance(key, base, plane, value)
    else:
        c.set_angle(key, base, plane, value)
    return c


# ---------------------------------------------------------------------------
# ORTHO. The pure `ortho` module owns the rule; this is the shared state and the one
# path every point pick uses.
# ---------------------------------------------------------------------------

#: The persistent Ortho mode (status bar / F8 / `Ortho`). Off at start, as the reference CAD application's
#: default status bar shows it.
_ortho_on = False
#: The ortho angle in degrees. application-defined until `OrthoAngle` is built: the reference CAD application's documented
#: default (`ortho.DEFAULT_ANGLE`).
_ortho_angle = ortho.DEFAULT_ANGLE


def ortho_on():
    return bool(_ortho_on)


def set_ortho(on):
    """Set the persistent Ortho mode. Returns the new state."""
    global _ortho_on
    _ortho_on = bool(on)
    _tag_redraw_viewports()
    return _ortho_on


def toggle_ortho():
    """Flip Ortho; returns the line to log."""
    on = set_ortho(not _ortho_on)
    return f"Ortho {'on' if on else 'off'}"


def apply_ortho(cmd, point, area, shift_held):
    """`point` restricted by Ortho for the running command's NEXT pick, or unchanged.

    In effect when the persistent mode XOR Shift is ("Shift ... Toggles Ortho mode",
    the reference CAD application keyboard documentation), and only once there is a previous point -- Ortho is
    measured "from the last point created" (the reference CAD application documentation), so a command's first pick is
    never restricted. In the plane of the pane the pick is made in."""
    if point is None or cmd is None or not ortho.effective(_ortho_on, shift_held):
        return point
    base = _snap_base(cmd)
    if base is None:
        return point
    return ortho.constrain(base, point, get_cplane(area), _ortho_angle)


def constrain_pick(cmd, point):
    """Apply the armed constraint -- or the one being TYPED -- to a world point."""
    prompt = _constraint_prompt(cmd)
    if prompt is None or point is None:
        return point
    key, _base = prompt
    typed = _typed_constraint(cmd)
    if typed is not None:
        return typed.apply(point)
    if not _pick_constraint.active_for(key):
        return point
    return _pick_constraint.apply(point)


def pick_consumes_typed(cmd):
    """True when a click now would use a constraint still sitting in the command
    line -- the click then takes it, and the text must not be left behind."""
    return _typed_constraint(cmd) is not None


def typed_changed():
    """The command-line text changed: re-shape the live preview without a mouse move."""
    cmd = app_state.active_command
    if cmd is not None:
        _reapply_constraint_preview(cmd)


def pick_constraint():
    """The live constraint object (for tests and the report)."""
    return _pick_constraint


def typed_size_cursor(cmd, cursor, area=None):
    """Where the preview cursor goes for a SIZE being typed at a DISTANCE prompt, or None.

    Typed values update distance previews before Enter. The typed-length rule above
    covers point prompts only; circle radius and cylinder dimensions are distance
    prompts, where the number IS the answer -- so nothing moved until Enter.

    The cursor is placed where that size would land, using the SAME arithmetic the
    command applies to the typed number:
    * a step with an AXIS (a height; `axis_constraint`): origin + axis * value, signed,
      as `_parse_distance` measures along the axis;
    * otherwise (a radius): `_distance_base` + value along the cursor's direction, with
      a Diameter / Circumference / Area size mode converted by `radius_from`.
    Preview only: the typed text still goes to the command on Enter.
    """
    if cmd is None or cursor is None or getattr(cmd, "pending_option", None) is not None:
        return None
    steps = getattr(cmd, "steps", [])
    idx = getattr(cmd, "step_index", 0)
    if idx >= len(steps) or steps[idx][1] != commands.DISTANCE:
        return None
    text = (app_state.input_buffer or "").strip()
    if not text:
        return None
    try:
        value = commands.parse_number(text)
    except Exception:  # noqa: BLE001 (not a number yet: "1.", "<", a name)
        return None
    axis = _command_axis_constraint(cmd, area or active_pane_area())
    if axis is not None:
        (ox, oy, oz), d = axis
        return (ox + d[0] * value, oy + d[1] * value, oz + d[2] * value)
    base_fn = getattr(cmd, "_distance_base", None)
    base = base_fn() if callable(base_fn) else None
    if base is None:
        return None
    if getattr(cmd, "size_mode", None) and idx == getattr(cmd, "_size_step_index", -1):
        value = cmd.radius_from(value, cmd.size_mode)
    plane = get_cplane(area) if area is not None else active_cplane()
    v = [cursor[i] - base[i] for i in range(3)]
    n = plane.zaxis
    along = sum(v[i] * n[i] for i in range(3))
    v = [v[i] - along * n[i] for i in range(3)]        # in the plane, as a radius is
    length = sum(c * c for c in v) ** 0.5
    u = [c / length for c in v] if length > 1e-9 else list(plane.xaxis)
    return tuple(base[i] + u[i] * value for i in range(3))


def _pane_area(key):
    return next((a for a in _view3d_areas() if _pane_key(a) == key), None)


def _reapply_constraint_preview(cmd):
    """Re-place the preview after a constraint changed, without a mouse move."""
    if _constraint_cursor is None:
        return
    pt = constrain_pick(cmd, _constraint_cursor)
    sized = typed_size_cursor(cmd, pt, _pane_area(_preview_pane))
    if sized is not None:
        pt = sized
    if pt is not None and _tracks_cursor(cmd):
        if set_preview_cursor(pt, _preview_pane):
            _tag_redraw_viewports()


def _feed_constrained_point(st, point):
    text = coords.format_world(point)
    st.history.append("> " + text)
    st.feed_line(text)


def handle_constraint_line(line):
    """Intercept a submitted line that is a pick constraint. True if it was consumed.

    * a bare number -> distance constraint (negative = incremental);
    * ``<angle``     -> angle constraint;
    * an EMPTY line while a constraint is armed -> place the point where the marker
      is ("Right-click or press Enter", the reference CAD application documentation).

    Everything else returns False and goes to the command exactly as before.
    """
    st = app_state
    cmd = st.active_command
    prompt = _constraint_prompt(cmd)
    if prompt is None:
        return False
    key, base = prompt
    text = (line or "").strip()
    if not text:
        if not _pick_constraint.active_for(key):
            return False
        point = _pick_constraint.fixed_point()
        if point is None and _constraint_cursor is not None:
            point = _pick_constraint.apply(_constraint_cursor)
        if point is None:
            st.log("move the mouse to choose a direction, then click")
            redraw_cmdbar()
            return True
        _feed_constrained_point(st, point)
        return True
    parsed = pickconstraint.parse(text)
    if parsed is None:
        return False
    kind, value = parsed
    if kind == "error":
        st.history.append("> " + text)
        st.log(value)
        redraw_cmdbar()
        return True
    st.history.append("> " + text)
    plane = active_cplane()
    if kind == "distance":
        done = _pick_constraint.set_distance(key, base, plane, value)
        if done is not None:
            _feed_constrained_point(st, done)
            return True
    else:
        _pick_constraint.set_angle(key, base, plane, value)
    st.log(f"Constraint: {_pick_constraint.describe()} -- click, or press Enter")
    _reapply_constraint_preview(cmd)
    redraw_cmdbar()
    return True


def constraint_tab():
    """Tab during a point pick: direction lock, then circle lock, then release.

    Returns the new lock state, or False when Tab does not apply here (no command at
    a point prompt with a previous point, or the cursor has not been seen yet).
    """
    st = app_state
    cmd = st.active_command
    prompt = _constraint_prompt(cmd)
    if prompt is None or _constraint_cursor is None:
        return False
    key, base = prompt
    marker = _constraint_cursor
    if _pick_constraint.active_for(key) and _pick_constraint.lock is None:
        # a distance/angle is armed: lock the direction through the CONSTRAINED marker
        marker = _pick_constraint.apply(marker)
    state = _pick_constraint.tab(key, base, active_cplane(), marker)
    note = _pick_constraint.describe() or "lock released"
    st.log(f"Constraint: {note}")
    _reapply_constraint_preview(cmd)
    redraw_cmdbar()
    return state


# ---------------------------------------------------------------------------
# object snaps -- state, the pixel->world radius, and the marker
# ---------------------------------------------------------------------------

#: The Osnap control's persistent state (which snaps are on, Disable, Project,
#: and the pixel radius). One per session, exactly as the reference CAD application's panel is.
_snap_settings = osnap.SnapSettings()

#: The snap the cursor is currently within range of, or None. Set by the command
#: modal on mouse-move and read by the marker draw handler and the click handler,
#: so what you SEE is provably what you GET -- they read one value.
_active_snap = None
_snap_pane = None
_snap_handlers = []

#: Re-sampling every object for snapping stalls the first move of a large-document
#: Gumball drag because each release
#: threw the whole cache away. Now the cache is an INDEX -- one entry per snap SOURCE
#: (an object, or one edge of a Brep: `bridge.object_snap_sources`),
#: ``[name, source, extent, targets, rect_view, rect, is_edge]`` -- whose `targets` are
#: sampled lazily, only for sources whose `extent` projects near the cursor
#: (`osnap.box_screen_rect`). Valid for one `_doc_key`; any other
#: document state rebuilds it, so an undo, a Move command, a Delete or an Open can never
#: be snapped to stale. `invalidate_snap_cache(names)` re-reads only those objects.
_snap_index = {"key": None, "entries": None, "stale": set()}
#: the last pane view the per-entry screen rectangles were computed for (see `snap_at`)
_snap_view = [None]
#: per-model token for `_doc_key` -- `id` alone may be reused by a later model
_doc_tokens = [0]


def _doc_key():
    """A stamp of the document's state that changes with every recorded change.

    The model (and its File3dm), the undo serial (every recorded change, undo and
    redo), the object count and `_cp_version` (in-place grip edits inside one open
    record). The same ingredients `_control_point_locations` already trusts. Changes
    made INSIDE an open record (the serial moves only when it closes) are reported
    explicitly through `invalidate_snap_cache`, as they always were.
    """
    model = getattr(app_state, "model", None)
    file3dm = getattr(model, "file", None)
    if file3dm is None:
        return None
    tok = getattr(model, "_narwhal_cache_token", None)
    if tok is None:
        _doc_tokens[0] += 1
        tok = _doc_tokens[0]
        try:
            model._narwhal_cache_token = tok
        except Exception:  # noqa: BLE001
            tok = id(model)
    try:
        n = len(file3dm.Objects)
    except Exception:  # noqa: BLE001
        n = None
    return (tok, id(file3dm), _undo_serial(), n, _cp_version[0])


#: ONE walk of the document per document state, shared by every consumer that used to
#: walk it independently. Large-document profiling showed that rhino3dm attribute and
#: name access dominates such walks, and the
#: first frame after a Delete walked it SIX times -- layer state 150 ms, curve colours
#: 480 ms, the control-point lookup 55, the point prune 55, point objects 46, the
#: density list -- 1.1 s. Rows are ``(name, mode, layer index, geometry, attributes)``.
#: `patch` says what the last `_patch_doc_rows` did (for tests and probes).
_doc_rows_cache = {"key": None, "rows": [], "by_name": None, "builds": 0, "patch": None}


def _doc_rows():
    """Every document object as ``(name, mode, layer index, geometry, attributes)``, in
    TABLE order, read once per `_doc_key` (and dropped by `invalidate_snap_cache` /
    `_note_objects_changed` for edits made inside an open undo record). Mode / layer
    index are None when unreadable. The list is the cache itself: callers must not
    mutate it."""
    key = _doc_key()
    c = _doc_rows_cache
    if key is not None and c["key"] == key:
        return c["rows"]
    model = getattr(app_state, "model", None)
    file3dm = getattr(model, "file", None)
    if key is not None and file3dm is not None and _doc_rows_from_journal(c, file3dm, key):
        return c["rows"]
    rows = []
    # the Ids too, while each object is in hand: the journal patch needs them, and
    # reading 5,702 of them afterwards was ~40 ms of the first frame after a Delete
    # while each object is already in hand
    ids = []
    if file3dm is not None:
        for fobj in file3dm.Objects:
            try:
                a = fobj.Attributes
                name = a.Name or ""
                oid = str(a.Id)
            except Exception:  # noqa: BLE001
                continue
            try:
                mode = int(a.Mode)
            except Exception:  # noqa: BLE001
                mode = None
            try:
                layer = int(a.LayerIndex)
            except Exception:  # noqa: BLE001
                layer = None
            rows.append((name, mode, layer, fobj.Geometry, a))
            ids.append(oid)
    c.update(key=key, rows=rows, by_name=None, ids=ids, ids_of=rows)
    c["builds"] += 1
    _doc_rows_stamp(c, file3dm)
    return rows


#: application-defined -- more changed objects than this since the rows were read, and one walk of the
#: document is cheaper than patching them one by one.
DOC_ROWS_PATCH_MAX = 256


def _doc_rows_stamp(c, file3dm):
    """Remember the document change journal's position the rows are valid at."""
    try:
        from . import model as _model

        serial, all_, active = _model.journal_state()
    except Exception:  # noqa: BLE001
        serial, all_, active = None, None, False
    c.update(file=file3dm, jserial=serial, jall=all_, jactive=active)


def _doc_row(fobj):
    a = fobj.Attributes
    name = a.Name or ""
    try:
        mode = int(a.Mode)
    except Exception:  # noqa: BLE001
        mode = None
    try:
        layer = int(a.LayerIndex)
    except Exception:  # noqa: BLE001
        layer = None
    return (name, mode, layer, fobj.Geometry, a)


def _doc_rows_from_journal(c, file3dm, key):
    """Bring the rows up to date from `model`'s change JOURNAL (every rhino3dm call that
    changes, adds or deletes a document object marks it) instead of walking all of the
    document. True when done; False -> the caller walks.

    Patch one changed row rather than walking the full document after Delete or undo.
    A changed object is
    re-read in place; a deleted one leaves; a (re-)added one is at the END of the table
    (rhino3dm appends; `model.restore` re-adds only what changed, `normalize_order` a
    tail in order), so the table's tail is matched against the added ids. Anything that does not add up -> a walk."""
    if c.get("file") is not file3dm or not c.get("jactive") or c.get("jserial") is None:
        return False
    try:
        from . import model as _model

        serial, all_, active = _model.journal_state()
        if not active or all_ != c["jall"]:
            return False
        if serial == c["jserial"]:
            c.update(key=key, patch="journal: nothing changed")
            return True
        changes = _model.journal_since(c["jserial"])
        table = file3dm.Objects
        n = len(table)
        rows = list(c["rows"])
        ids = c.get("ids")
        if ids is None or c.get("ids_of") is not c["rows"]:
            ids = [str(r[4].Id) for r in rows]
        ids = list(ids)
        pos = {oid: i for i, oid in enumerate(ids)}
        moved = {oid for oid, mv in changes if mv}
        if len(changes) > DOC_ROWS_PATCH_MAX:
            # Re-ordering the table (`model.normalize_order`, before a save) re-adds every
            # object after the first misplaced one -- thousands of journal entries.
            # Those rows are still only the table's TAIL: keep the untouched head and
            # re-read from the first changed row so an unchanged head is retained.
            first = min((pos[oid] for oid, _mv in changes if oid in pos), default=len(rows))
            if first == 0:
                return False
            # (the head rows have no journal entry, so nothing touched them; rhino3dm
            # only deletes from the table or appends to it, and both are journalled)
            head, head_ids = rows[:first], ids[:first]
            if first > n:
                return False
            # by ITERATING: rhino3dm's `table[i]` walks the table from the start
            # (Verified 0.8 ms each near i=4,000), so indexing a long tail is quadratic
            import itertools
            tail = list(itertools.islice(iter(table), first, n))
            rows = head + [_doc_row(f) for f in tail]
            ids = head_ids + [str(f.Attributes.Id) for f in tail]
            c.update(key=key, rows=rows, by_name=None, ids=ids, ids_of=rows,
                     patch=f"journal: tail from {first} ({n - first} rows)")
            c["jserial"] = serial
            return True
        for oid, mv in changes:
            if mv or oid not in pos:
                continue
            fobj = table.FindId(oid)
            if fobj is None:
                moved.add(oid)                 # gone without a recorded move: re-check
                continue
            rows[pos[oid]] = _doc_row(fobj)
        if moved:
            keep = [i for i, oid in enumerate(ids) if oid not in moved]
            rows = [rows[i] for i in keep]
            ids = [ids[i] for i in keep]
            present = [oid for oid in moved if table.FindId(oid) is not None]
            k = len(present)
            if k == 1:
                # one re-added object (an undone Delete): it is the table's LAST one --
                # Use FindId rather than walking to the end of a large table.
                tail = [table.FindId(present[0])]
            else:
                import itertools
                tail = list(itertools.islice(iter(table), n - k, n)) if k else []
            tail_ids = [str(f.Attributes.Id) for f in tail]
            if set(tail_ids) != set(present):
                return False
            rows.extend(_doc_row(f) for f in tail)
            ids.extend(tail_ids)
        if len(rows) != n:
            return False
    except Exception:  # noqa: BLE001
        return False
    c.update(key=key, rows=rows, by_name=None, patch=f"journal: {len(changes)} object(s)",
             ids=ids, ids_of=rows)
    c["jserial"] = serial
    return True


def _patch_doc_rows(low, before, now, in_place=False):
    """`_note_objects_changed`'s half for the shared rows: the document went from
    `before` to `now` by changing exactly the objects named in `low` (lower case).

    A transform either changes the geometry IN PLACE (`fobj.Geometry` is a live
    reference, so the row already reads the new shape) or goes through
    `model.replace_geometry`, which deletes the object and adds it back -- it then
    reappears at the END of the table with the same Id, and every other row keeps its
    order (Verified in rhino3dm). So the run of changed-name objects at the end of the
    table is re-read and replaces those Ids' rows; anything that does not add up -- rows
    not valid at `before`, a different object count -- drops the rows and the next use
    walks the document.
    This avoids re-walking the document after a one-curve Gumball change."""
    c = _doc_rows_cache
    if before is None or now is None or c["key"] != before:
        c["key"], c["patch"] = None, f"not valid before ({c['key']} vs {before})"
        return
    if not low or in_place:
        # `in_place`: the caller edited those objects' geometry IN PLACE (a grip drag)
        # -- the rows hold live references, so they already read the new shape, and
        # the SAME list is kept so caches keyed on it stay valid.
        c["key"], c["patch"] = now, "carried" if not low else "in place"
        return
    rows = c["rows"]
    model = getattr(app_state, "model", None)
    file3dm = getattr(model, "file", None)
    try:
        n = len(file3dm.Objects)
    except Exception:  # noqa: BLE001
        n = None
    if n != len(rows):
        c["key"], c["patch"] = None, f"count ({n} vs {len(rows)})"
        return
    # the run of changed-name objects at the end of the table, newest last; names may
    # repeat in a real file, so the replaced objects are told apart by their Id
    tail = []
    try:
        i = n - 1
        while i >= 0:
            fobj = file3dm.Objects[i]
            a = fobj.Attributes
            name = a.Name or ""
            if name.lower() not in low:
                break
            try:
                mode = int(a.Mode)
            except Exception:  # noqa: BLE001
                mode = None
            try:
                layer = int(a.LayerIndex)
            except Exception:  # noqa: BLE001
                layer = None
            tail.append((name, mode, layer, fobj.Geometry, a))
            i -= 1
        tail.reverse()
        tail_ids = [str(r[4].Id) for r in tail]
        ids = set(tail_ids)
        # the row Ids ride along when known (`_doc_rows_from_journal` needs them; see
        # `_doc_rows`), so a later Delete does not re-read every object's Id
        row_ids = c.get("ids") if c.get("ids_of") is rows else None
        if row_ids is not None and len(row_ids) == len(rows):
            kept = [(r, oid) for r, oid in zip(rows, row_ids)
                    if r[0].lower() not in low or oid not in ids]
            keep = [r for r, _oid in kept]
            new_ids = [oid for _r, oid in kept] + tail_ids
        else:
            keep = [r for r in rows if r[0].lower() not in low or str(r[4].Id) not in ids]
            new_ids = None
    except Exception as e:  # noqa: BLE001
        c["key"], c["patch"] = None, f"failed: {e!r}"
        return
    # an empty tail is fine: a curve is transformed IN PLACE (`fobj.Geometry` is a live
    # reference, so the row's wrapper already reads the moved curve) and nothing moved
    if len(ids) != len(tail) or len(keep) + len(tail) != n:
        c["key"], c["patch"] = None, (f"tail of {len(tail)} does not account for the "
                                      f"change ({len(keep)} kept of {n})")
        return
    new_rows = keep + tail
    c.update(key=now, rows=new_rows, by_name=None, patch="patched", ids=new_ids,
             ids_of=new_rows if new_ids is not None else None)
    _doc_rows_stamp(c, file3dm)


def _doc_rows_by_name():
    """``{name: [(table index, row), ...]}`` over `_doc_rows` (names may repeat)."""
    rows = _doc_rows()
    c = _doc_rows_cache
    if c["by_name"] is None or c["by_name"][0] is not rows:
        m = {}
        for i, row in enumerate(rows):
            m.setdefault(row[0], []).append((i, row))
        c["by_name"] = (rows, m)
    return c["by_name"][1]


def snap_settings():
    """The live Osnap settings (for the `Osnap` command, tests and the UI)."""
    return _snap_settings


def invalidate_snap_cache(names=None):
    """Forget cached document-derived data; the next use re-reads the document.

    With no `names`, everything goes: the snap index, the point-object and
    control-polygon caches, and the layer-curve overlay re-checks every curve's
    content (`_layer_curve_items`). With `names` -- the objects one edit touched, in
    the CURRENT document state -- only those are re-read; see `_note_objects_changed`
    for the stricter form that also carries the cache across a key change.
    """
    _doc_rows_cache["key"] = None
    _document_object_cache["key"] = None
    _snap_build["key"] = None
    if names is None:
        _snap_index.update(key=None, entries=None, stale=set())
        _layer_curve_cache["verify"] = True
    else:
        low = {str(n).lower() for n in names}
        if _snap_index["entries"] is not None:
            _snap_index["stale"] |= low
        _layer_curve_cache["stale"] |= low
    _point_loc_cache["key"] = None
    _polygon_cache["key"] = None
    _selection_projection_cache["entries"].clear()
    _selection_projection_cache["doc"] = None
    _cp_obj_forget(names)
    _gumball_pts_cache["key"] = None


def _note_objects_changed(names, before, in_place=False):
    """The document went from state `before` (a `_doc_key` taken just before) to the
    current one by changing EXACTLY the objects `names` (possibly none).

    A per-object cache that was valid at `before` stays valid for every other object,
    so it re-reads only `names` and moves to the current key. A cache that was not
    valid at `before` has missed some other change, so it is dropped -- this is never
    allowed to paper over an edit nobody reported. Only call it when `names` really is
    the whole change (the Gumball's deferred transform, and closing its undo record).
    """
    now = _doc_key()
    low = {str(n).lower() for n in names}
    # the shared rows hold each object's geometry: re-read exactly the changed objects
    _patch_doc_rows(low, before, now, in_place=in_place)
    if before is None or now is None:
        invalidate_snap_cache()
        return
    if _snap_index["entries"] is not None:
        if _snap_index["key"] == before:
            _snap_index["stale"] |= low
            _snap_index["key"] = now
        else:
            _snap_index.update(key=None, entries=None, stale=set())
    lc = _layer_curve_cache
    if lc["doc"] is not None:
        if lc["doc"] == before and not lc["verify"]:
            lc["stale"] |= low
            lc["doc"] = now
        else:
            lc["verify"] = True
    _cp_obj_forget(names, before)
    _snap_build["key"] = None
    # A transform changes geometry only -- never an object's Mode, layer, kind or name,
    # nor the set of objects -- so what was valid at `before` and depends on nothing
    # else stays valid: carry it to `now` instead of re-walking the document on the
    # release frame.
    if _layer_state_key[0] is not None and _layer_state_key[0][0] == before:
        _layer_state_key[0] = (now,) + tuple(_layer_state_key[0][1:])
    cc = _curve_colour_cache["key"]
    if cc is not None and cc[0] == before:
        _curve_colour_cache["key"] = (now,) + tuple(cc[1:])
    if _locked_cache["key"] == before:
        _locked_cache["key"] = now
    uk = _unpickable_cache["key"]
    if uk is not None and uk[0] == before:
        _unpickable_cache["key"] = (now,) + tuple(uk[1:])
    dc = _density_curve_cache
    if dc["key"] is not None and dc["key"] == before:
        # re-read when next ASKED (by the zoom pass, which waits out a drag): measuring
        # a curve's length was ~4 ms of every grip move
        dc["stale"] |= low
        dc["key"] = now
    if low:
        _gumball_pts_cache["key"] = None
        _point_loc_cache["key"] = None
        _polygon_cache["key"] = None
    else:
        # no object changed: what was valid at `before` is valid now
        if _point_loc_cache["key"] == before:
            _point_loc_cache["key"] = now
        pk = _polygon_cache["key"]
        if pk is not None and pk[0] == before:
            _polygon_cache["key"] = (now, pk[1])


def _in_progress_snap_targets():
    """Snap targets for the geometry the RUNNING command has not committed yet.

    In-progress polylines must snap to their own picked points so they can close.
    `bridge.snap_targets` reads the
    DOCUMENT, and a command's picked points are not in the document until it
    finishes -- so until then there was nothing to snap to.

    the reference CAD application snaps to the points you have already placed in the current command; that
    is how you close a polyline on its own start point. The picked points are
    vertices of the curve being drawn, so they go in `vertices`, which is exactly
    what `End` snaps to ("the end of a curve ... interior vertices of polylines").
    They are also handed to `points` so `Near` and `Mid` work along the segments
    drawn so far.

    Returns [] when no command is running or it has picked nothing.
    """
    cmd = app_state.active_command
    if cmd is None:
        return []
    picked = [a for a in getattr(cmd, "args", []) or []
              if isinstance(a, (tuple, list)) and len(a) == 3
              and all(isinstance(c, (int, float)) for c in a)]
    if not picked:
        return []
    return [osnap.SnapTarget(points=picked, vertices=picked,
                            name=f"{getattr(cmd, 'name', 'command')} (in progress)")]


def _snap_index_entries():
    """The snap index for the current document state (see `_snap_index`).

    Rebuilt from scratch (`bridge.object_snap_sources` per object) when the document
    key moved; when only some objects were reported changed (`stale`), the table is
    walked once and just those are re-read. Either way the entries come out in TABLE
    order, which is the order `bridge.snap_targets` produces, so tie-breaks between
    equal snaps are unchanged.
    """
    key = _doc_key()
    idx = _snap_index
    if idx["entries"] is not None and idx["key"] == key and not idx["stale"]:
        return idx["entries"]
    full = idx["entries"] is None or idx["key"] != key
    if full:
        # from scratch: finish the idle-time build if one is under way for this state
        # (`_snap_prebuild_tick`), else do it all now -- the same code either way
        try:
            _snap_build_step(None)
        except Exception as e:  # noqa: BLE001
            print(f"[narwhal3d] snap target extraction failed: {e!r}")
            idx.update(key=key, entries=[], stale=set(), groups={}, counts={})
        return idx["entries"]
    stale = idx["stale"]
    # an object's entries are reused only when its name belonged to exactly ONE object
    # before and does now, and it was not reported; anything else is simply re-read
    old_groups = {} if full else idx.get("groups") or {}
    old_counts = {} if full else idx.get("counts") or {}
    groups, counts, entries = {}, {}, []
    try:
        rows = [(row[0], row[3]) for row in _doc_rows()]
        for name, _g in rows:
            counts[name] = counts.get(name, 0) + 1
        for name, geom in rows:
            group = None
            if (name and counts[name] == 1 and old_counts.get(name) == 1
                    and name.lower() not in stale):
                group = old_groups.get(name)
            if group is None:
                group = [[name, src, ext, None, None, None, is_edge]
                         for src, is_edge, ext
                         in bridge.object_snap_sources(geom)]
            groups[name] = group
            entries.extend(group)
    except Exception as e:  # noqa: BLE001
        # Snapping is an aid; a failure here must not break picking.
        print(f"[narwhal3d] snap target extraction failed: {e!r}")
        groups, counts, entries = {}, {}, []
    idx.update(key=key, entries=entries, stale=set(), groups=groups, counts=counts)
    idx["builds"] = idx.get("builds", 0) + (1 if full else 0)
    return entries


#: The from-scratch build of the snap index, resumable: ``{"key": the `_doc_key` it
#: is for, "rows", "pos", "groups", "counts", "entries"}``.
_snap_build = {"key": None, "rows": None, "pos": 0, "groups": {}, "counts": {},
               "entries": []}
#: True while `_snap_prebuild_tick` is registered.
_snap_prebuild_armed = [False]
#: application-defined -- the idle-time snap-index build's slice length (as `RETESS_SLICE_S`).
SNAP_BUILD_SLICE_S = 0.012


def _snap_build_step(budget_s):
    """Advance the from-scratch build of the snap index by about `budget_s` seconds
    (None: to the end). True once the index for the current document state is
    installed. Entries come out in TABLE order, exactly as a one-go build made them."""
    import time

    key = _doc_key()
    idx = _snap_index
    if idx["entries"] is not None and idx["key"] == key:
        return True
    b = _snap_build
    if b["key"] != key or b["key"] is None:
        rows = _doc_rows()
        counts = {}
        for row in rows:
            counts[row[0]] = counts.get(row[0], 0) + 1
        b.update(key=key, rows=rows, pos=0, groups={}, counts=counts, entries=[])
    rows = b["rows"]
    t0 = time.perf_counter()
    while b["pos"] < len(rows):
        name, _mode, _layer, geom, _attrs = rows[b["pos"]]
        group = [[name, src, ext, None, None, None, is_edge]
                 for src, is_edge, ext in bridge.object_snap_sources(geom)]
        b["groups"][name] = group
        b["entries"].extend(group)
        b["pos"] += 1
        # at least one object per call, so any budget makes progress
        if (budget_s is not None and b["pos"] < len(rows)
                and time.perf_counter() - t0 >= budget_s):
            return False
    idx.update(key=key, entries=b["entries"], stale=set(), groups=b["groups"],
               counts=b["counts"])
    idx["builds"] = idx.get("builds", 0) + 1
    b.update(key=None, rows=None, pos=0, groups={}, counts={}, entries=[])
    return True


def _snap_wanted():
    """Could a snap be asked for at all (any object snap on, or Gumball snappy drag)?"""
    try:
        return bool(_snap_settings.enabled) or bool(_gumball_snappy)
    except Exception:  # noqa: BLE001
        return False


#: the exact geometry kernel is warmed during app registration, before the
#: user can start a Gumball scale. The measured status is public for tests/reporting.
_kernel_warmup = {"called": False, "ready": False, "ms": None, "error": None}


def warm_geometry_kernel():
    """Load OCCT before interaction.

    Startup profiling on a one-box document showed the first Gumball Scale1D release
    paying the one-time kernel import cost;
    `model.snapshot` was 0.4 ms and the first `bridge._occt` was 682.6 ms. Calling
    it before the drag removes that first-release stall, so the one-time import belongs at app startup,
    synchronously: no random later timer stall and no C-extension import on a worker.
    """
    import time

    st = _kernel_warmup
    if st["called"]:
        return st["ready"]
    st["called"] = True
    started = time.perf_counter()
    try:
        st["ready"] = bridge._occt() is not None
    except Exception as e:  # noqa: BLE001
        st["error"] = repr(e)
        st["ready"] = False
    st["ms"] = (time.perf_counter() - started) * 1000.0
    if st["error"]:
        print(f"[narwhal3d] geometry-kernel warm-up failed: {st['error']}")
    return st["ready"]


def kernel_warmup_stats():
    """Return a copy of the kernel startup warm-up status."""
    return dict(_kernel_warmup)


#: True while the idle undo-base timer is armed
_undo_base_armed = [False]


def _note_undo_base_needed():
    """From the draw pass (cheap): when the model has no copy-on-write BASE snapshot
    (just opened, or its change tracking was reset), take one in idle time.

    The first recorded change after Open can otherwise pay for a full document copy
    while creating its undo record
    (283 ms of a 386 ms release), because only a base lets later records share the
    unchanged objects. Taken while nobody is interacting, the first change costs ~1 ms.
    """
    if _undo_base_armed[0] or bpy.app.background:
        return
    model = getattr(app_state, "model", None)
    if model is None or getattr(model, "_base", 0) is not None:
        return
    _undo_base_armed[0] = True
    try:
        bpy.app.timers.register(_undo_base_tick, first_interval=1.0)
    except Exception:  # noqa: BLE001
        _undo_base_armed[0] = False


def _undo_base_tick():
    """Take the base snapshot once nobody is navigating, dragging or mid-command."""
    if navigating() or _gumball_drag is not None or app_state.active_command is not None:
        return NAV_QUIET_S
    try:
        model = app_state.model
        if model is not None and getattr(model, "_base", 0) is None:
            model.snapshot()
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] idle undo base failed: {e!r}")
    _undo_base_armed[0] = False
    return None


def _note_snap_index_needed():
    """From the draw pass (cheap): arm the idle-time snap-index build when the index
    is missing or out of date for this document state.

    Building the full snap index on the first Gumball move visibly stalls large documents,
    so warm it before interaction."""
    if _snap_prebuild_armed[0] or bpy.app.background:
        return
    idx = _snap_index
    if idx["entries"] is not None and idx["key"] == _doc_key():
        return
    if getattr(app_state, "model", None) is None or not _snap_wanted():
        return
    _snap_prebuild_armed[0] = True
    try:
        bpy.app.timers.register(_snap_prebuild_tick, first_interval=NAV_QUIET_S)
    except Exception:  # noqa: BLE001
        _snap_prebuild_armed[0] = False


def _snap_prebuild_tick():
    """One idle-time slice of the snap-index build; waits while the user interacts
    (`navigating`), like the zoom re-tessellation."""
    if navigating() or _gumball_drag is not None:
        return NAV_QUIET_S
    try:
        if not _snap_build_step(SNAP_BUILD_SLICE_S):
            return RETESS_SLICE_GAP_S
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] idle snap index build failed: {e!r}")
        _snap_build["key"] = None
    _snap_prebuild_armed[0] = False
    return None


def _entry_targets(entry):
    """One index entry's `osnap.SnapTarget`s, sampled on first use and kept."""
    if entry[3] is None:
        try:
            entry[3] = bridge.snap_source_targets(entry[1], entry[6], name=entry[0])
        except Exception as e:  # noqa: BLE001
            print(f"[narwhal3d] snap target extraction failed for {entry[0]!r}: {e!r}")
            entry[3] = []
    return entry[3]


# ---------------------------------------------------------------------------
# Snap targets around the Gumball, sampled BEFORE the drag
#
# Sampling all curves near the cursor on the first Snappy Gumball move stalls the drag.
# Sampling every curve up
# front would cost 100+ MB on that file, so only the entries within
# `GUMBALL_WARM_PX` of the widget's origin, in each pane's CURRENT view, are sampled --
# in idle slices, after the selection settles, never while the user navigates or drags.
# The answer is unchanged: a warmed entry holds exactly what the drag would have
# sampled (`_entry_targets` is the only producer).

#: application-defined -- the reach (pixels, around the Gumball origin's projection) whose snap targets
#: are sampled ahead of a drag: twice the widget (a handle is pressed up to one widget
#: size from the origin and the first moves go on from there). The snap radius is added.
GUMBALL_WARM_PX = 2.0 * gumball.DEFAULT_SIZE_PX
#: application-defined -- the pause between warm-up slices. Shorter than the zoom pass's
#: (`RETESS_SLICE_GAP_S`): this runs only while nobody interacts, and the sooner it is
#: through, the likelier the drag finds its neighbourhood sampled. Events still run
#: between slices, so a click or an orbit waits at most one slice (`SNAP_BUILD_SLICE_S`).
GUMBALL_WARM_GAP_S = 0.01

#: ``{"key", "origin", "todo", "armed", "warmed", "plans", "panes", "seen",
#: "planned"}`` -- see `_note_gumball_warm`.
_gumball_warm = {"key": None, "origin": None, "todo": None, "armed": False,
                 "warmed": 0, "plans": 0, "panes": [], "seen": {}, "planned": None}


def _note_gumball_warm(origin, plane=None):
    """From `draw_gumball` (cheap): arm the idle sampling of the snap targets around
    the widget at `origin` (axes `plane`), once per (document state, origin, view
    motion)."""
    w = _gumball_warm
    if _gumball_drag is not None or not _gumball_snappy:
        return
    w["plane"] = plane
    try:
        origin = tuple(float(c) for c in origin)
    except Exception:  # noqa: BLE001
        return
    key = (_doc_key(), origin, _view_motion.get("t"))
    if w["key"] == key:
        return
    # a running warm-up re-plans for the NEW origin (the selection changed under it)
    w.update(key=key, origin=origin)
    if w["armed"]:
        return
    w["todo"] = None
    if bpy.app.background:
        return                             # tests drive `_gumball_warm_tick` directly
    w["armed"] = True
    try:
        bpy.app.timers.register(_gumball_warm_tick, first_interval=NAV_QUIET_S)
    except Exception:  # noqa: BLE001
        w["armed"] = False


def _gumball_warm_panes():
    """``(region, rv3d)`` of every VIEW_3D pane (from a timer: no `context.screen`)."""
    out = []
    try:
        active = active_pane_key()
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                if area.type != "VIEW_3D":
                    continue
                space = _view3d_space(area)
                region = _window_region(area)
                rv3d = getattr(space, "region_3d", None)
                if region is not None and rv3d is not None:
                    # the ACTIVE pane first: that is where the user is working
                    out.append((0 if _pane_key(area) == active else 1, len(out),
                                (region, rv3d)))
    except Exception:  # noqa: BLE001
        pass
    return [p for _a, _i, p in sorted(out)]


def _gumball_warm_tips(origin, region, rv3d):
    """The pixel positions of the Gumball's three axis-handle tips in this pane (the
    frame `draw_gumball` last showed), for `_gumball_warm_plan_pane`'s order."""
    plane = _gumball_warm.get("plane")
    out = []
    try:
        size = _gumball_size(region, rv3d, origin)
        if plane is None or not size:
            return out
        for axis in (plane.xaxis, plane.yaxis, plane.zaxis):
            p = _project_2d(region, rv3d, tuple(o + size * a for o, a in zip(origin, axis)))
            if p is not None:
                out.append(p)
    except Exception:  # noqa: BLE001
        pass
    return out


def _gumball_warm_plan_pane(origin, pane, seen, todo):
    """Add the unsampled index entries a drag from `origin` would reach in ONE pane to
    `seen` (``{id: [nearest squared px distance, entry]}``) and re-order `todo` by it,
    nearest at the end. Each pane is a separate idle slice. The selection itself is
    excluded from drag targets."""
    import numpy as np

    entries = _snap_index_entries()
    if not entries:
        return
    region, rv3d = pane
    mouse = _project_2d(region, rv3d, origin)
    if mouse is None:
        return
    drop = set(selection_names())
    radius = GUMBALL_WARM_PX + float(_snap_settings.radius_px or 0.0)
    reach = _snap_reach(region, rv3d, mouse, radius)
    idx = np.flatnonzero(_snap_reach_mask(entries, reach))
    # NEAREST first, by an entry's distance from the widget in WHICHEVER pane it is
    # closest: the drag may start in any pane, and its first move lands next to the
    # handle -- so if the user grabs the widget before the warm-up is through, what it
    # reaches first is done. (`_snap_reach_mask` just cached this view's rectangles.)
    # Plan pane by pane so one viewport's ordering cannot dominate another's drag.
    # The distance is to the nearest of the widget's centre and its three axis-handle
    # tips, where a drag is grabbed. Center-only ordering leaves too many entries near
    # an axis handle unsampled.
    rects = _snap_boxes[0][5] if _snap_boxes[0] is not None else None
    d2 = np.zeros(len(idx))
    if rects is not None and len(idx):
        d2 = osnap.rects_distance2(rects, mouse)[idx]
        for tip in _gumball_warm_tips(origin, region, rv3d):
            d2 = np.minimum(d2, osnap.rects_distance2(rects, tip)[idx])
    for i, d in zip(idx.tolist(), d2.tolist()):
        e = entries[i]
        if e[3] is not None or e[2] is None or e[0] in drop:
            continue
        k = id(e)
        if k in seen:
            if d < seen[k][0]:
                seen[k][0] = d
        else:
            seen[k] = [d, e]
    # `todo` is popped from its END: farthest first in the list, nearest last
    todo[:] = [e for _d, e in sorted(seen.values(), key=lambda de: -de[0])
               if e[3] is None]
    _gumball_warm["plans"] += 1


def _gumball_warm_tick(budget_s=None, panes=None):
    """One idle slice of `_note_gumball_warm`'s sampling. Waits while the user
    navigates or drags; re-plans when the document or the view moved meanwhile."""
    import time

    w = _gumball_warm
    budget = SNAP_BUILD_SLICE_S if budget_s is None else budget_s
    if navigating() or _gumball_drag is not None:
        w["todo"] = None                   # the view may move: plan again afterwards
        return NAV_QUIET_S
    try:
        idx = _snap_index
        if idx["entries"] is None or idx["key"] != _doc_key():
            return NAV_QUIET_S             # the idle index build comes first
        key = (_doc_key(), w["origin"], _view_motion.get("t"))
        if w["todo"] is None or w.get("planned") != key:
            if w["origin"] is None:
                w["armed"] = False
                return None
            w.update(todo=[], seen={}, planned=key,
                     panes=list(_gumball_warm_panes() if panes is None else panes))
        if w["panes"]:
            _gumball_warm_plan_pane(w["origin"], w["panes"].pop(0), w["seen"], w["todo"])
            if budget_s is None:
                return GUMBALL_WARM_GAP_S   # a pane's plan is this slice's work
        todo = w["todo"]
        t0 = time.perf_counter()
        while todo:
            _entry_targets(todo.pop())
            w["warmed"] += 1
            if todo and time.perf_counter() - t0 >= budget:
                return GUMBALL_WARM_GAP_S
        if w["panes"]:
            return GUMBALL_WARM_GAP_S
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] gumball snap warm-up failed: {e!r}")
    w["armed"] = False
    return None


_control_point_snap_cache = {"key": None, "by_name": {}}


def _control_point_snap_targets(names):
    """Point-Osnap targets for the control points currently drawn in the viewport.

    Point Osnap includes control points, but invisible control points must not become
    hidden snap traps. ``_cp_rows`` is already the single source of truth for markers
    shown by selection/PointsOn. Group them once per control-point cache key so a large
    selection is not walked again on every mouse move; the normal object-extent cull
    supplies the small set of nearby ``names`` to read.
    """
    showing = frozenset(effective_points_on())
    key = (showing, _control_point_doc_key())
    cache = _control_point_snap_cache
    if cache["key"] != key:
        by_name = {}
        for name, _index, point in _cp_rows():
            by_name.setdefault(name, []).append(
                osnap.SnapTarget.point(point, name=name))
        cache["key"], cache["by_name"] = key, by_name
    return [target for name in names for target in cache["by_name"].get(name, ())]


def _snap_targets(exclude_names=(), reach=None):
    """Every snappable target: the document (cached) plus the running command.

    The in-progress part is deliberately NOT cached -- it changes with every pick,
    and caching it is what would make "snap to the point I just placed" fail again.

    `exclude_names` drops the targets belonging to those object names. A Gumball
    drag needs it: the objects being transformed sit directly under the widget, so
    without the exclusion the first snap test finds the dragged object's OWN
    vertices and the drag sticks where it started.

    `reach(entry)`, when given, says whether an index entry can possibly offer a snap
    near the cursor; entries it rejects are neither sampled nor returned. An entry
    with no extent is always kept. With no `reach` every object is returned, exactly
    as `bridge.snap_targets` would.
    """
    drop = set(exclude_names) if exclude_names else ()
    out = []
    kept_names = set()
    entries = _snap_index_entries()
    # A pane's reach (`_snap_reach`) is tested for EVERY entry at once with
    # `osnap.boxes_reach` -- the same rule box by box (spike/test_curve_pick.py holds
    # the two equal). Batch testing avoids a Python loop on the first Gumball move.
    keep = None
    if reach is not None and getattr(reach, "view", None) is not None and entries:
        try:
            keep = _snap_reach_mask(entries, reach)
        except Exception:  # noqa: BLE001
            keep = None
    for i, e in enumerate(entries):
        if drop and e[0] in drop:
            continue
        if e[2] is not None and reach is not None:
            if keep is not None:
                if not keep[i]:
                    continue
            elif not reach(e):
                continue
        kept_names.add(e[0])
        out.extend(_entry_targets(e))
    out += _control_point_snap_targets(kept_names)
    out += _in_progress_snap_targets()
    if drop:
        out = [t for t in out if getattr(t, "name", "") not in drop]
    return out


def _snap_reach(region, rv3d, mouse, radius_px):
    """`_snap_targets`' `reach` for this pane: keep an entry only if its extent's
    screen rectangle comes within `radius_px` of `mouse` (`osnap.rect_reaches`).

    Each entry remembers its rectangle for the view it was computed in, so a drag --
    where the view does not move -- projects every extent once, not once per move.
    """
    pm = rv3d.perspective_matrix
    view = (tuple(tuple(float(v) for v in pm[r]) for r in range(4)),
            float(region.width), float(region.height))
    if view == _snap_view[0]:
        view = _snap_view[0]              # the SAME object, so entries compare by `is`
    else:
        _snap_view[0] = view
    matrix, width, height = view

    def reach(e):
        if e[4] is not view:
            lo, hi = e[2]
            e[5] = osnap.box_screen_rect(lo, hi, matrix, width, height)
            e[4] = view
        return osnap.rect_reaches(e[5], mouse, radius_px)

    # what `_snap_targets` needs to apply the same test to every entry at once
    reach.view, reach.mouse, reach.radius_px = view, mouse, radius_px
    return reach


#: ``(entries list, lo (N, 3), hi (N, 3), has extent (N,))`` for `_snap_reach_mask`.
_snap_boxes = [None]


def _snap_reach_mask(entries, reach):
    """Boolean per entry of `entries`: True where `reach` would keep it (entries with
    no extent come out True and are never culled -- see `_snap_targets`)."""
    import numpy as np

    c = _snap_boxes[0]
    if c is None or c[0] is not entries or len(c[1]) != len(entries):
        n = len(entries)
        lo = np.zeros((n, 3), dtype=np.float64)
        hi = np.zeros((n, 3), dtype=np.float64)
        has = np.zeros(n, dtype=bool)
        for i, e in enumerate(entries):
            if e[2] is not None:
                lo[i], hi[i] = e[2]
                has[i] = True
        c = _snap_boxes[0] = [entries, lo, hi, has, None, None]
    _entries, lo, hi, has, rect_view, rects = c
    if reach.radius_px is None or reach.mouse is None:
        return np.ones(len(entries), dtype=bool)
    # the projected rectangles depend on the VIEW only (`_snap_reach` hands out one
    # view object per distinct view), so a drag projects them once
    if rect_view is not reach.view:
        matrix, width, height = reach.view
        rects = native_selection.screen_rects_from_aabbs(
            lo, hi, matrix, width, height)
        if rects is None:
            rects = osnap.boxes_screen_rects(lo, hi, matrix, width, height)
        c[4], c[5] = reach.view, rects
    return osnap.rects_reach(rects, reach.mouse, reach.radius_px) | ~has


def active_snap():
    """The snap currently under the cursor, or None (for tests/introspection)."""
    return _active_snap


def set_active_snap(snap, pane_key=None):
    """Record the snap under the cursor. Returns True when it changed."""
    global _active_snap, _snap_pane
    same = (snap is None and _active_snap is None) or (
        snap is not None and _active_snap is not None
        and snap == _active_snap and pane_key == _snap_pane)
    if same:
        return False
    _active_snap = snap
    _snap_pane = pane_key
    return True


def clear_active_snap():
    return set_active_snap(None, None)


def _pixels_per_unit(region, rv3d, depth_point):
    """How many screen pixels one world unit covers, near `depth_point`.

    the reference CAD application's snap radius is in PIXELS (the reference CAD application documentation), so it must be
    converted at the current zoom -- a world-unit radius would grow and shrink as
    you zoom, which is not the reference CAD application's behaviour.

    Measured by inverting the projection: ask what world distance spans 100 screen
    pixels at this depth. That works for parallel AND perspective panes without
    special-casing either. Returns None when it cannot be determined, which
    `osnap.best` reads as "no radius limit".

    reuses this for the zoom-driven curve re-tessellation trigger, which is the
    same question asked for a different reason.
    """
    try:
        import bpy_extras.view3d_utils as v3u
        from mathutils import Vector

        span = 100.0
        cx, cy = region.width / 2.0, region.height / 2.0
        anchor = Vector(depth_point)
        a = v3u.region_2d_to_location_3d(region, rv3d, (cx, cy), anchor)
        b = v3u.region_2d_to_location_3d(region, rv3d, (cx + span, cy), anchor)
        if a is None or b is None:
            return None
        world = (a - b).length
        if world <= 0.0:
            return None
        return span / world
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# (the viewport half): RE-TESSELLATE CURVES WHEN THE VIEW ZOOMS.
#
# Small curves can still look segmented when zoomed in. A world-space chord tolerance
# is scale-dependent, so a baked tessellation must be revisited at display scale.
# This is the OTHER half: our tessellation is baked once in WORLD units, while the reference CAD application
# re-tessellates against SCREEN size, so zooming far enough into any curve eventually
# reveals facets however good the baked count was.
#
# The pure tessellation policy is separate from this viewport trigger:
#  tessellate.screen_segments_for(length, ppu)  -> count at this zoom
#  tessellate.should_retessellate(old, new)     -> 1.5x hysteresis
#  bridge.retessellate_curve_for_view(name, curve, ppu) -> rebuilds one object in place
#
# TWO DESIGN POINTS WORTH KEEPING:
#
# 1. Detection happens in the DRAW pass but the MESH WRITE does not. Re-tessellating
#  inside a draw handler means writing a Mesh datablock while Blender is drawing
#  it, which is exactly the class of thing that crashes rather than erroring. So
#  the draw handler only compares numbers and arms a one-shot timer.
#
# 2. The trigger is the DRAW pass rather than our own navigation code, and that is
#  the point: a zoom can come from `navigate.py`, from Blender's scroll wheel, from
#  a View command, from Zoom Extents or from a maximize. Hooking the draw catches
#  all of them for free, where hooking each source would miss whichever one was
#  added next.
# ---------------------------------------------------------------------------

#: ``{display object name: (segment count, mesh vertex count)}``. The hysteresis in
#: `tessellate.should_retessellate` is measured against the segment count, so it must
#: record what was actually DRAWN and not what was requested.
#:
#: The vertex count is how this detects its own staleness. `rebuild_display` (in
#: `commands.py`) deletes and recreates every display object at
#: the WORLD tolerance, which would leave this dict describing meshes that no longer
#: exist -- and the hysteresis would then suppress the first legitimate rebuild and
#: leave the curve coarse. Asking Blender how many vertices the mesh actually has is a
#: MEASUREMENT of the live state; requiring `rebuild_display` to call an invalidation
#: hook would create unnecessary coupling between display rebuilding and this cache.
_curve_display_density = {}

#: True while a re-tessellation timer is armed, so a burst of draw passes during a
#: scroll arms ONE pass and not one per frame.
_retess_armed = False

#: Set by `note_view_zoom`, read by the timer. The pane that changed is irrelevant to
#: the work (a curve has one display mesh shared by all four panes), so only the
#: scale is kept -- and the LARGEST wins, because a curve must be smooth enough for
#: the most zoomed-in pane that shows it.
_retess_ppu = None

#: ``{region pointer: pixels_per_unit}`` -- every pane's LATEST scale. The pass runs at
#: the largest, because one display mesh serves all four panes.
_pane_ppu = {}

#: ``{"doc": _doc_key, "ppu": scale, "deferred": how many off-screen curves it left
#: too coarse, "moved": the `_view_motion` stamp it planned at}`` of the last COMPLETED
#: pass.
_retess_done = {"doc": None, "ppu": None, "deferred": 0, "moved": None}

#: application-defined -- a pass is armed only when the largest pane scale has moved by more than this
#: ratio since the last pass or the document changed. Without this threshold, large
#: documents arm an expensive pass after orbit, pan, and idle redraws even when no curve
#: needs rebuilding.
#: Below `tessellate.RETESSELLATE_FACTOR` (1.5), so no rebuild that hysteresis allows is
#: skipped by more than this; orbiting a perspective pane (which moves its scale at the
#: world origin by a few %) no longer triggers a pass at all.
RETESS_PPU_STEP = 1.1


def note_view_zoom(region, rv3d):
    """Called from the pane draw pass: record this pane's scale, arm a rebuild.

    Cheap by design -- it must be, because it runs on every pane on every redraw.
    It does no mesh work at all (see design point 1 above); it remembers this pane's
    `pixels_per_unit` and arms a one-shot timer ONLY when the largest pane scale moved
    by more than `RETESS_PPU_STEP` since the last pass, or the document changed.
    """
    global _retess_armed, _retess_ppu
    if region is None or rv3d is None:
        return
    # measured at the ORBIT CENTRE: in a perspective pane the scale at the world origin
    # changes when orbiting around another point and can arm work despite no zoom. At
    # the pivot, an orbit leaves the scale unchanged.
    ppu = _pixels_per_unit(region, rv3d, tuple(rv3d.view_location))
    if ppu is None or ppu <= 0.0:
        return
    _note_view_motion(region, rv3d)
    try:
        _pane_ppu[region.as_pointer()] = ppu
    except Exception:  # noqa: BLE001
        _pane_ppu[id(region)] = ppu
    # the most zoomed-in pane sets the requirement, because one display mesh serves
    # all four panes -- tessellating for the coarsest would leave the closest faceted
    target = max(_pane_ppu.values())
    _retess_ppu = target
    if _retess_armed:
        return
    done = _retess_done["ppu"]
    if (done is not None and _retess_done["doc"] == _doc_key()
            and max(target / done, done / target) < RETESS_PPU_STEP
            # ...unless the last pass DEFERRED off-screen curves that are too coarse
            # for this zoom and the view has moved since, which may have brought them
            # on screen (see `_retess_queue`). Nothing deferred: a pan or orbit arms
            # nothing at all.
            and not (_retess_done.get("deferred")
                     and _retess_done.get("moved") != _view_motion["t"])):
        return
    _retess_armed = True
    try:
        bpy.app.timers.register(_retess_tick, first_interval=0.05)
    except Exception:  # noqa: BLE001
        _retess_armed = False


def _live_region_pointers():
    out = set()
    try:
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                if area.type == "VIEW_3D":
                    for r in area.regions:
                        if r.type == "WINDOW":
                            out.add(r.as_pointer())
    except Exception:  # noqa: BLE001
        return None
    return out


#: Zoom tessellation runs in short slices between frames, with on-screen curves first,
#: so one scroll step cannot freeze the window while rebuilding off-screen curves.
RETESS_SLICE_S = 0.012
#: application-defined -- the pause between slices, so frames (and navigation) run in between.
RETESS_SLICE_GAP_S = 0.03

#: The running sliced job: ``{"ppu", "doc", "queue": [(prio, name, geom, want)]}``.
_retess_job = {"ppu": None, "doc": None, "queue": [], "moved": None, "deferred": 0}


#: ``{region pointer: view-matrix fingerprint}`` and when any of them last changed.
_view_motion = {"views": {}, "t": 0.0}

#: application-defined -- background display work (the sliced zoom re-tessellation) waits until no
#: viewport has moved for this long, so it never competes with navigation frames.
NAV_QUIET_S = 0.25


def _note_view_motion(region, rv3d):
    """From the draw pass: remember when any viewport last MOVED (orbit, pan, zoom)."""
    import time

    try:
        key = tuple(round(v, 9) for row in rv3d.view_matrix for v in row) + (
            round(rv3d.view_distance, 9), bool(rv3d.is_perspective))
        ptr = region.as_pointer()
    except Exception:  # noqa: BLE001
        return
    if _view_motion["views"].get(ptr) != key:
        _view_motion["views"][ptr] = key
        _view_motion["t"] = time.monotonic()


def navigating():
    """True while some viewport moved -- or the user pressed to navigate or dragged the
    Gumball (`note_user_interaction`) -- within the last `NAV_QUIET_S` seconds."""
    import time

    return (time.monotonic() - max(_view_motion["t"], _view_motion.get("input", 0.0))
            < NAV_QUIET_S)


def _note_view_wheel(over_tabs):
    """Mark a wheel as navigation unless the viewport-tab strip consumed it."""
    if over_tabs:
        return False
    note_user_interaction()
    return True


def note_user_interaction():
    """The user has started (or is in) an interaction that wants every frame: a right /
    middle press that may become a view drag, or a Gumball drag. Background display
    work (`_retess_tick`, the curve pass's periodic fold) waits from NOW.

    Large-document interaction profiling showed that with the
    zoom re-tessellation running, a Shift+right drag's first ~0.5 s ran at 100-135 ms
    a frame, because `navigating` only noticed once a frame had DRAWN the view
    moved -- and each slice in between cost a depsgraph evaluation and a 4-pane
    redraw. Kept apart from `_view_motion["t"]`: a press is not a view change, so it
    must not make the zoom pass re-plan what is on screen."""
    import time

    _view_motion["input"] = time.monotonic()


def _retess_tick():
    """One SLICE of the zoom re-tessellation (a repeating timer while work remains).

    Waits while the user is navigating (`navigating`): Verified, slices interleaved with
    a scroll or an orbit turned every frame into 170-400 ms, because each slice's rebuilt
    curves are drawn one by one until they fold back into the batches."""
    import time

    global _retess_armed, _retess_ppu
    if navigating():
        return NAV_QUIET_S
    live = _live_region_pointers()
    if live is not None:
        # a pane that was closed (a maximize, a layout change) no longer counts
        for key in [k for k in _pane_ppu if k not in live]:
            _pane_ppu.pop(key, None)
    ppu = max(_pane_ppu.values()) if _pane_ppu else _retess_ppu
    job = _retess_job
    try:
        if not ppu:
            _retess_armed = False
            return None
        doc = _doc_key()
        moved = _view_motion["t"]
        if (job["ppu"] is None or job["doc"] != doc
                or max(ppu / job["ppu"], job["ppu"] / ppu) >= RETESS_PPU_STEP
                or job.get("moved") != moved
                or not job["queue"]):
            # (re)plan at the CURRENT zoom and view: a scroll that is still going
            # re-plans, a pan re-plans what is on screen now, and the slices already
            # done are simply re-judged by the hysteresis
            deferred = []
            job.update(ppu=ppu, doc=doc, moved=moved,
                       queue=_retess_queue(ppu, _visible_curve_names(), deferred),
                       deferred=len(deferred))
        objects = None
        t0 = time.perf_counter()
        while job["queue"] and time.perf_counter() - t0 < RETESS_SLICE_S:
            if objects is None:
                objects = {ob.name: ob for ob in bpy.data.objects}
            _prio, name, geom, want = job["queue"].pop()
            if bridge.retessellate_curve_for_view(name, geom, job["ppu"]):
                _curve_display_density[name] = (want, _mesh_vertex_count(name, objects))
        if job["queue"]:
            return RETESS_SLICE_GAP_S                  # more slices to come
        _retess_done.update(doc=doc, ppu=job["ppu"], deferred=job.get("deferred", 0),
                            moved=job.get("moved"))
        job["ppu"] = None
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] zoom re-tessellation failed: {e!r}")
        job.update(ppu=None, queue=[])
    _retess_armed = False
    _retess_ppu = None
    return None


def _visible_curve_names():
    """Names of curve display objects whose box is on screen in ANY viewport, or None
    when that cannot be told (then every curve counts as visible)."""
    try:
        import numpy as np

        st = _curve_sync()
        if st is None or not st["pick_names"]:
            return None
        names = st["pick_names"]
        seen = np.zeros(len(names), dtype=bool)
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                if area.type != "VIEW_3D":
                    continue
                sp = next((s for s in area.spaces if s.type == "VIEW_3D"), None)
                reg = next((r for r in area.regions if r.type == "WINDOW"), None)
                if sp is None or reg is None or not reg.width or not reg.height:
                    continue
                m = np.array(sp.region_3d.perspective_matrix, dtype=np.float64)
                w, h = reg.width, reg.height
                seen |= osnap.boxes_reach(st["pick_lo"], st["pick_hi"], m, w, h,
                                          (w / 2.0, h / 2.0), 0.5 * (w * w + h * h) ** 0.5)
        return {names[i] for i in np.flatnonzero(seen)}
    except Exception:  # noqa: BLE001
        return None


def _retess_queue(pixels_per_unit, visible=None, deferred=None):
    """The curves a zoom pass must rebuild, as a STACK (the next one popped is the most
    urgent): on-screen curves that are too COARSE first, then on-screen ones that are
    needlessly dense, then off-screen ones in the same order. Coarse-on-screen is the only
    kind anyone can see; the rest is housekeeping done in the background.

    With a `deferred` list (the LIVE, sliced pass) only on-screen curves that are too
    COARSE are queued: an off-screen curve is appended to `deferred` instead (the pass
    re-plans once the view has moved -- `note_view_zoom`), and a needlessly DENSE curve
    is left as it is. Deferring off-screen and needlessly dense curves avoids prolonged
    background work and interaction stutter. Neither kind of work
    can be SEEN: an off-screen curve is not drawn, and a dense one looks the same.
    (`retessellate_curves_for_zoom`, the explicit whole-document pass, still does all.)
    """
    try:
        from . import tessellate
    except Exception:  # noqa: BLE001
        return []
    objects = {ob.name: ob for ob in bpy.data.objects}
    out = []
    for name, geom, length in _density_curves():
        want = tessellate.screen_segments_for(length, pixels_per_unit)
        have = _recorded_density(name, objects)
        if have is None:
            # Never drawn at a zoom yet (just opened / rebuilt): keep the mesh it HAS
            # unless it is too COARSE for this zoom. Rebuilding every unknown curve on
            # first draw can spend substantial time coarsening meshes that are already
            # visually sufficient.
            verts = _mesh_vertex_count(name, objects)
            if verts and want < verts * tessellate.RETESSELLATE_FACTOR:
                _curve_display_density[name] = (want, verts)
                continue
            finer = True
        elif not tessellate.should_retessellate(have, want):
            continue
        else:
            finer = want > have
        on = visible is None or name in visible
        if deferred is not None:
            if not finer:
                continue
            if not on:
                deferred.append(name)
                continue
        prio = (0 if on else 2) + (0 if finer else 1)
        out.append((prio, name, geom, want))
    out.sort(key=lambda t: -t[0])          # popped from the END: priority 0 last
    return out


def retessellate_curves_for_zoom(pixels_per_unit):
    """Re-tessellate every document CURVE whose density is wrong at this zoom.

    Returns the list of display object names actually rebuilt, which is what the
    gates assert -- "nothing was rebuilt" and "everything was rebuilt" are both
    failures and a boolean cannot tell them apart.

    Walks the document's object table rather than Blender's, for the reason
    `bridge.snap_targets` gives: the document holds the real geometry and the Blender
    meshes are only a tessellation of it. Only curves are considered -- surfaces got
    their own adaptive treatment in  and are not keyed off this.

    The hysteresis is the load-bearing part. Without it this would rebuild every
    curve on every scroll frame, which would be a worse performance bug than the
    cosmetic issue it fixes, so the work must remain selective.
    """
    rebuilt = []
    if not pixels_per_unit or pixels_per_unit <= 0.0:
        return rebuilt
    # Build one object dictionary per pass instead of calling `bpy.data.objects.get`
    # once per curve. One dict of the
    # display objects per pass, and the curve list (name, geometry, length) once per
    # document state, make a pass that rebuilds nothing a few ms of arithmetic.
    # (The LIVE path is `_retess_tick`, which does this same work in slices.)
    queue = _retess_queue(pixels_per_unit)
    objects = {ob.name: ob for ob in bpy.data.objects}
    while queue:
        _prio, name, geom, want = queue.pop()
        if bridge.retessellate_curve_for_view(name, geom, pixels_per_unit):
            _curve_display_density[name] = (want, _mesh_vertex_count(name, objects))
            rebuilt.append(name)
    return rebuilt


#: ``{"key": _doc_key, "items": [(name, geometry, density length), ...]}``
_density_curve_cache = {"key": None, "items": [], "stale": set()}


def _density_curves():
    """Every named document CURVE as ``(name, geometry, length for density)``, computed
    once per document state (`_doc_key`: any recorded change, undo, Open, or an in-record
    grip edit makes a new list)."""
    model = getattr(app_state, "model", None)
    file3dm = getattr(model, "file", None)
    if file3dm is None:
        return []
    key = _doc_key()
    if key is not None and _density_curve_cache["key"] == key:
        if _density_curve_cache["stale"]:
            _density_patch(_density_curve_cache["stale"])
            _density_curve_cache["stale"] = set()
        return _density_curve_cache["items"]
    _density_curve_cache["stale"] = set()
    items = []
    for name, _mode, _layer, geom, _attrs in _doc_rows():
        if not name:
            continue
        # (None: not a curve, a curve we cannot measure, or a polyline -- drawn
        # through its EXACT vertices at every zoom, `bridge._curve_display_points`,
        # so a zoom can never change its mesh)
        it = _density_item(name, geom)
        if it is not None:
            items.append(it)
    _density_curve_cache["key"], _density_curve_cache["items"] = key, items
    return items


def _density_patch(low):
    """Re-read `_density_curves`' entries for the objects named `low` (lower case) from
    the current document rows, keeping every other entry and the table order."""
    fresh = {}
    for name, _mode, _layer, geom, _attrs in _doc_rows():
        if name and name.lower() in low:
            fresh.setdefault(name, []).append(_density_item(name, geom))
    items = []
    done = set()
    for it in _density_curve_cache["items"]:
        if it[0].lower() not in low:
            items.append(it)
        elif it[0] not in done:
            done.add(it[0])
            items.extend(x for x in fresh.get(it[0], ()) if x is not None)
    for name, xs in fresh.items():
        if name not in done:
            items.extend(x for x in xs if x is not None)
    _density_curve_cache["items"] = items


def _density_item(name, geom):
    """One `_density_curves` entry, or None for an object it does not list."""
    length = _curve_length_for_density(geom)
    if length is None:
        return None
    try:
        if geom.IsPolyline():
            return None
    except Exception:  # noqa: BLE001
        pass
    return (name, geom, length)


def _mesh_vertex_count(name, objects=None):
    """Vertices in that display object's mesh right now, or None. `objects` is an
    optional ``{name: object}`` map, for a caller asking about thousands."""
    try:
        ob = objects.get(name) if objects is not None else bpy.data.objects.get(name)
        return len(ob.data.vertices) if ob is not None and ob.data else None
    except Exception:  # noqa: BLE001
        return None


def _recorded_density(name, objects=None):
    """The segment count this curve was last drawn at, or None if that is unknown.

    Returns None -- i.e. "rebuild it" -- when the live mesh's vertex count no longer
    matches what was recorded, which is how a `rebuild_display` behind our back is
    detected without `commands.py` having to tell us. See `_curve_display_density`.
    """
    rec = _curve_display_density.get(name)
    if rec is None:
        return None
    count, verts = rec
    if verts is not None and _mesh_vertex_count(name, objects) != verts:
        _curve_display_density.pop(name, None)
        return None
    return count


def _curve_length_for_density(geom):
    """The curve's length for density purposes, or None if it is not a curve.

    None for a surface/brep/mesh/point is the filter, not an error: this returns the
    one number `screen_segments_for` needs and nothing else, so the caller does not
    have to know how to interrogate rhino3dm.
    """
    try:
        import rhino3dm as r3
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(geom, r3.Curve):
        return None
    # A STRAIGHT curve cannot benefit from a denser display mesh -- two vertices draw
    # it exactly at any zoom. Skipping it here matters because `curve_segments_for`
    # models every curve as a circle of the same length, so a line is ASKED for ~72
    # segments at a close zoom; `bridge.curve_display_bmesh` then short-circuits it
    # back to 2, so the work was wasted rather than wrong. Measured: before this, a
    # zoom rebuilt every line in the document to produce an identical mesh.
    try:
        if geom.IsLinear():
            return None
    except Exception:  # noqa: BLE001
        pass
    # On large imported documents the exact
    # `arclength.length_of` here cost 27.4 s on the first redraw after Open. This number
    # only picks a DISPLAY density, so an estimate is enough: the chord length through
    # 17 samples (a circle reads 0.6 % short -- far inside `should_retessellate`'s
    # hysteresis), cached per curve and re-measured when its start, middle or end moves.
    try:
        d = geom.Domain
        t0, t1 = float(d.T0), float(d.T1)
        a, mid, b = geom.PointAt(t0), geom.PointAt(0.5 * (t0 + t1)), geom.PointAt(t1)
        fp = (t0, t1, a.X, a.Y, a.Z, mid.X, mid.Y, mid.Z, b.X, b.Y, b.Z)
    except Exception:  # noqa: BLE001
        return None
    key = id(type(geom)), fp
    hit = _density_length_cache.get(key)
    if hit is not None:
        return hit
    n = 16
    pts = [geom.PointAt(t0 + (t1 - t0) * i / n) for i in range(n + 1)]
    length = sum(pts[i].DistanceTo(pts[i + 1]) for i in range(n))
    if len(_density_length_cache) > 50000:
        _density_length_cache.clear()
    _density_length_cache[key] = length if length > 0.0 else None
    return _density_length_cache[key]


#: (geometry type, start/mid/end fingerprint) -> estimated length (see above)
_density_length_cache = {}


def forget_curve_densities():
    """Drop the recorded densities. For tests, and for a document reset.

    Not required for correctness after a `rebuild_display` -- `_recorded_density`
    detects that case by measuring the live mesh (see `_curve_display_density`) --
    but a cheap explicit reset is still worth having where one is natural.
    """
    _curve_display_density.clear()
    _retess_done["doc"] = _retess_done["ppu"] = None
    _retess_job.update(ppu=None, doc=None, queue=[])


def snap_at(area, space, region, cursor_world, first_point=False, suspend=False,
            exclude_names=(), base=None):
    """The snap for a cursor at `cursor_world` in this pane, or None.

    Ties the pieces together: the pane's CPlane decides `Quad` and `Project`, the
    pane's zoom converts the reference CAD application's pixel radius, and the pure engine picks the snap.

    **A snap with no radius is refused.** `osnap.best` treats ``radius=None`` as
    "no limit", which its own docstring calls useful only in tests -- and without a
    pane scale there is no honest way to judge whether the cursor is NEAR anything.
    Snapping from an arbitrary distance silently moves the user's pick somewhere
    they did not click, so "no scale, no snap" is the correct answer. This was
    latent until the in-progress targets below gave a distant cursor something to
    grab: it made the second click of a `Line` jump back onto the first.
    """
    modes = _snap_settings.active(suspend=suspend, first_point=first_point)
    if not modes or cursor_world is None:
        return None
    plane = get_cplane(area)
    rv3d = getattr(space, "region_3d", None) if space is not None else None
    if rv3d is None or region is None:
        return None
    # the radius is in PIXELS, so distance is measured ON SCREEN. Measuring it
    # in world units from the cursor's CPlane point made everything off the CPlane
    # unreachable from Top (a box's top corners, a raised curve) -- see
    # `osnap.best_on_screen`.
    try:
        import bpy_extras.view3d_utils as v3u
        from mathutils import Vector
    except Exception:  # noqa: BLE001
        return None

    def project(point):
        q = v3u.location_3d_to_region_2d(region, rv3d, Vector(point))
        return None if q is None else (q.x, q.y)

    def project_batch(points):
        try:
            import numpy as np

            matrix = np.asarray(rv3d.perspective_matrix, dtype=np.float64)
            return native_selection.project_display_points(
                points, matrix, region.width, region.height)
        except Exception:  # noqa: BLE001 -- scalar projection remains authoritative
            return None

    def project_distance_batch(points, mouse, radius):
        try:
            import numpy as np

            matrix = np.asarray(rv3d.perspective_matrix, dtype=np.float64)
            return native_selection.projected_snap_distances(
                points, matrix, region.width, region.height, mouse, radius)
        except Exception:  # noqa: BLE001 -- projected-pixel scoring remains authoritative
            return None

    def project_near_batch(polylines, mouse):
        try:
            import numpy as np

            matrix = np.asarray(rv3d.perspective_matrix, dtype=np.float64)
            return native_selection.nearest_projected_polylines(
                polylines, matrix, region.width, region.height, mouse)
        except Exception:  # noqa: BLE001 -- scalar Near remains authoritative
            return None

    # Only objects whose extent projects within the radius can snap, so only those are
    # sampled and handed to the engine (see `_snap_reach`). No mouse -> no cull, and
    # `best_on_screen` returns None for it anyway.
    mouse = project(cursor_world)
    reach = None
    if mouse is not None and _snap_settings.radius_px is not None:
        try:
            reach = _snap_reach(region, rv3d, mouse, _snap_settings.radius_px)
        except Exception:  # noqa: BLE001
            reach = None
    # `base` is the previous pick: Perp and Tan are computed from it. It used to be
    # dropped here, so those two snaps could never fire in the app.
    snap = osnap.best_on_screen(_snap_targets(exclude_names, reach=reach),
                                cursor_world, modes,
                                project, _snap_settings.radius_px, plane,
                                base=base,
                                snap_to_locked=_snap_settings.snap_to_locked,
                                project_batch=project_batch,
                                project_near_batch=project_near_batch,
                                project_distance_batch=project_distance_batch)
    return osnap.apply_projection(snap, plane, _snap_settings.project)


def _snap_base(cmd):
    """The previous picked point of the running command, for Perp/Tan (or None)."""
    last = getattr(cmd, "_last_point", None)
    try:
        return last() if callable(last) else None
    except Exception:  # noqa: BLE001
        return None


def draw_snap_marker():
    """POST_PIXEL draw handler: the reference CAD application's snap marker and its name.

    the reference CAD application shows a small constant-size box at the snap point with the snap's name
    beside it -- that label is how you know WHICH snap you are about to take, so
    it is not decoration. Drawn in POST_PIXEL (not POST_VIEW like the preview)
    precisely because the marker must stay the same size on screen at any zoom, so
    the world point is projected to region coordinates by hand.
    """
    import bpy

    snap = _active_snap
    if snap is None:
        return
    context = bpy.context
    area = getattr(context, "area", None)
    region = getattr(context, "region", None)
    if area is None or area.type != "VIEW_3D" or region is None:
        return
    # Unlike the PREVIEW (one world shape, shown in every pane), the snap marker
    # belongs only to the viewport the cursor is in -- that is where the reference CAD application shows
    # it, and showing four markers for one cursor would be noise.
    if _snap_pane is not None and _pane_key(area) != _snap_pane:
        return
    space = next((s for s in area.spaces if s.type == "VIEW_3D"), None)
    rv3d = getattr(space, "region_3d", None) if space is not None else None
    if rv3d is None:
        return

    try:
        import bpy_extras.view3d_utils as v3u
        from mathutils import Vector
        import gpu

        pos = v3u.location_3d_to_region_2d(region, rv3d, Vector(snap.point))
    except Exception:  # noqa: BLE001
        return
    if pos is None:
        return

    shader = _uniform_color_shader()
    if shader is None:
        return
    x, y = float(pos[0]), float(pos[1])
    h = float(SNAP_MARKER_PX)
    prev_blend = gpu.state.blend_get()
    gpu.state.blend_set("ALPHA")
    try:
        shader.bind()
        shader.uniform_float("color", SNAP_MARKER_COLOR)
        # an open box, as the reference CAD application draws it -- four edges, so the snapped geometry
        # stays visible through the middle
        corners = ((x - h, y - h), (x + h, y - h), (x + h, y + h), (x - h, y + h))
        coords = []
        for i in range(4):
            coords.append(corners[i])
            coords.append(corners[(i + 1) % 4])
        from gpu_extras.batch import batch_for_shader

        batch_for_shader(shader, "LINES", {"pos": coords}).draw(shader)
        _draw_text(x + h + 4, y + h - 2, snap.mode, SNAP_LABEL_FONT_SIZE,
                   SNAP_LABEL_COLOR)
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] snap marker draw failed: {e!r}")
    finally:
        gpu.state.blend_set(prev_blend)


def _attach_snap_handler():
    """Register the snap-marker handler on the VIEW_3D space TYPE, in POST_PIXEL.

    Type-level for the same reason as every other handler here: it must cover
    panes that do not exist yet when this runs.
    """
    if _snap_handlers:
        return False
    handler_id = bpy.types.SpaceView3D.draw_handler_add(
        draw_snap_marker, (), "WINDOW", "POST_PIXEL")
    _snap_handlers.append((bpy.types.SpaceView3D, handler_id))
    return True


def _detach_snap_handler():
    while _snap_handlers:
        owner, handler_id = _snap_handlers.pop()
        try:
            owner.draw_handler_remove(handler_id, "WINDOW")
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Point display: point OBJECTS and control points
#
# `Point` and `Points` build valid `r3.Point` geometry, but a
# single-vertex mesh draws NOTHING in a Blender object-mode viewport, so a point
# object was invisible from the moment it existed. And nothing drew the CONTROL
# points of a curve at all.
#
# A point has no extent, so it cannot go through the POST_VIEW line preview -- it
# needs a fixed PIXEL-size marker, which is why this is POST_PIXEL like the snap
# marker. The marker shapes are pure and live in `pointdisplay`.
# ---------------------------------------------------------------------------

_point_handlers = []

#: PointsOn/PointsOff state: the display NAMES whose control points are shown.
#: A set of names rather than a flag, because the reference CAD application turns control points on for
#: the objects you selected, not globally (the reference CAD application documentation: "Select
#: objects.").
_points_on = set()

#: A `PointsOff` on the ONE selected curve suppresses its automatic (selection-driven)
#: grips until the selection changes. Kept separate from `_points_on`: automatic display
#: is not persistent `PointsOn` state; keeping them separate prevents stale markers.
_auto_points_off = set()
_auto_points_selection = [frozenset()]

#: Colour of a point OBJECT's marker. application-defined -- the reference CAD application colours objects by layer and
#: we have no per-object colour plumbing yet, so this is a deliberate stand-in.
POINT_OBJECT_COLOR = (0.05, 0.05, 0.05, 1.0)

#: Colour of a CONTROL point marker. application-defined.
CONTROL_POINT_COLOR = (0.10, 0.10, 0.55, 1.0)

#: Colour of a SELECTED control point. application-defined -- the reference CAD application's selection colour is a
#: setting we have not plumbed; yellow reads as "selected" the way the Gumball's
#: grabbed-handle highlight does.
ACTIVE_CONTROL_POINT_COLOR = (1.0, 0.85, 0.1, 1.0)

#: The white of a "with white center" style. Verified that it is white (the style
#: is literally named that); the exact value is trivially white.
POINT_WHITE = (1.0, 1.0, 1.0, 1.0)

#: Colour of the CONTROL POLYGON -- the dashed line joining an object's control
#: points. application-defined. the reference CAD application draws one (see `pointdisplay`'s measured citations for that)
#: but the help states neither its colour nor its dash pattern. Deliberately a
#: a dark neutral line drawn UNDER markers so it reads as structure without competing
#: with the geometry.
CONTROL_POLYGON_COLOR = (0.22, 0.22, 0.25, 0.9)


def points_on_names():
    """Display names whose control points are currently shown.

    Pruned first, so a DELETED object can never still be named here -- see
    `prune_point_state`.
    """
    prune_point_state()
    return sorted(_points_on)


def set_points_on(names, on=True):
    """Turn control points on or off for `names`. Returns how many changed.

    the reference CAD application's `PointsOn` acts on the objects you selected, so this is per-object
    rather than a global switch. `PointsOff` also suppresses the transient automatic
    grips of the current one-curve selection ; that suppression expires as
    soon as the selection changes, so selecting the curve again restores the default.
    """
    keys = {str(name) for name in names or []}
    changed = 0
    if on:
        for key in keys:
            if key not in _points_on:
                _points_on.add(key)
                changed += 1
            _auto_points_off.discard(key)
    else:
        selected = frozenset(selected_display_names())
        _auto_points_selection[0] = selected
        for key in keys:
            if key in _points_on:
                _points_on.discard(key)
                changed += 1
            if key in selected and key not in _auto_points_off:
                _auto_points_off.add(key)
                changed += 1
    if changed:
        _cp_cache["key"] = None
        _tag_redraw_viewports()
    return changed


def clear_points_on():
    """Turn every object's control points off (what `PointsOff` with no selection
    means: "Turns off control points for all objects")."""
    n = len(_points_on) + len(_auto_points_off)
    _points_on.clear()
    _auto_points_off.clear()
    _auto_points_selection[0] = frozenset()
    if n:
        _cp_cache["key"] = None
        _tag_redraw_viewports()
    return n


def _document_point_locations():
    """``(name, (x, y, z))`` for every POINT OBJECT in the document.

    Read from the File3dm, not from Blender: the document is the source of truth,
    and a single-vertex Blender mesh is only its (invisible) tessellation. The name
    comes along so a picked marker can be traced back to the object it is.
    """
    out = []
    model = getattr(app_state, "model", None)
    file3dm = getattr(model, "file", None)
    if file3dm is None:
        return out
    try:
        import rhino3dm as r3
    except Exception:  # noqa: BLE001
        return out
    # Cache point locations per document state; walking every object in every pane on
    # each drag frame is prohibitively expensive.
    key = _doc_key()
    if key is not None and _point_loc_cache["key"] == key:
        return list(_point_loc_cache["out"])
    for name, _mode, _layer, geom, _attrs in _doc_rows():
        if isinstance(geom, r3.Point):
            loc = geom.Location
            out.append((name, (loc.X, loc.Y, loc.Z)))
    _point_loc_cache["key"], _point_loc_cache["out"] = key, list(out)
    return out


#: the cache behind `_document_point_locations` (key = `_doc_key`)
_point_loc_cache = {"key": None, "out": []}


#: Point-object rows and their f64 coordinates after unnamed/locked filtering. The
#: document cache keeps one stable source list until its key changes, so a survey file's
#: points are packed once rather than on every click.
_native_point_objects = {"source": None, "locked": None, "rows": [], "points": None}


def _packed_point_objects(rows, locked):
    """``(eligible rows, contiguous f64 XYZ)`` cached for point-object picking."""
    cache = _native_point_objects
    if cache["source"] is not rows or cache["locked"] != locked:
        import numpy as np

        eligible = [(name, world) for name, world in rows
                    if name and name not in locked]
        points = np.fromiter(
            (coordinate for _name, world in eligible for coordinate in world),
            dtype=np.float64,
            count=len(eligible) * 3,
        ).reshape(-1, 3)
        cache.update(source=rows, locked=locked, rows=eligible, points=points)
    return cache["rows"], cache["points"]


#: How close (pixels) the cursor must come to a POINT OBJECT's marker to pick it.
#: application-defined, and matched to the control-point tolerance so the two feel the same.
POINT_OBJECT_PICK_PX = 9.0


def pick_point_object(area, space, region, rx, ry,
                      tolerance=POINT_OBJECT_PICK_PX):
    """The name of the POINT OBJECT nearest region pixel (rx, ry), or None.

    Point objects need their own picker for the same reason control points do: a
    point has no extent, its Blender mesh is a single vertex with no faces, and
    `view3d.select` cannot hit it. So clicking a point object selected nothing --
    it drew (previous commit) but could not be picked. Screen-space distance to the
    drawn marker is the answer, exactly as for control points.
    """
    rv3d = getattr(space, "region_3d", None) if space is not None else None
    if rv3d is None or region is None:
        return None
    # Locked point objects are not pickable either. This picker is entirely ours, so
    # Blender's `hide_select` does not reach it -- the names have to be filtered.
    locked = _locked_names_cached()
    rows = _document_point_locations()
    key = _doc_key()
    source = (_point_loc_cache["out"] if key is not None
              and _point_loc_cache["key"] == key else rows)
    if source:
        try:
            if native_selection.available():
                eligible, points = _packed_point_objects(source, locked)
                if not eligible:
                    return None
                native = native_selection.nearest_projected_point(
                    points, rv3d.perspective_matrix, region.width, region.height,
                    (rx, ry), tolerance)
                if native is not None:
                    index, _distance = native
                    if index == native_selection.PICK_NO_HIT:
                        return None
                    if type(index) is not int or not 0 <= index < len(eligible):
                        raise ValueError("invalid native point-object index")
                    return eligible[index][0]
        except Exception:  # noqa: BLE001 -- any native/UI issue keeps scalar picking
            pass
    best, best_d = None, float(tolerance)
    for name, world in rows:
        if not name or name in locked:
            continue
        pos = _project_2d(region, rv3d, world)
        if pos is None:
            continue
        d = ((pos[0] - rx) ** 2 + (pos[1] - ry) ** 2) ** 0.5
        if d <= best_d:
            best, best_d = name, d
    return best


#: The document object names `prune_point_state` last reconciled against. Kept so the
#: prune only does work when the document's population has actually changed.
_point_state_names = None


_point_state_stamp = [None]


def prune_point_state():
    """Forget point state for objects that are no longer in the document.

    `_points_on` and `_selected_cps` are keyed by object name, so deleted names must be
    pruned before state can outlive the geometry.

    There are two consequences and the second is the nastier one. The obvious one is
    stale entries: `points_on_names` kept naming a deleted object. The subtle one is
    NAME RECYCLING -- `ctx.rebuild_display` resets its name counters from whatever is
    in the file, so after deleting `Circle1` the next circle is called `Circle1`
    again, and it would have inherited the dead object's points and point SELECTION
    without anyone turning them on.

    Reconciled against the DOCUMENT, not `bpy.data.objects`: `rebuild_display` empties
    the Blender object table and refills it, so a prune that happened to run in the
    middle of one would throw away live state.

    Returns True when something was dropped.
    """
    global _point_state_names

    model = getattr(app_state, "model", None)
    file3dm = getattr(model, "file", None)
    if file3dm is None:
        return False
    # Real-file import: Verified 0.68 s per redraw on a 5,700-object file -- this reads
    # every object's name, several times a frame. The population can only change with the
    # document (a new file, or a recorded change), so that is checked first, for free.
    try:
        stamp = (id(file3dm), len(file3dm.Objects), _undo_serial())
    except Exception:  # noqa: BLE001
        stamp = None
    if stamp is not None and stamp == _point_state_stamp[0]:
        return False
    # Nothing to forget: no walk at all (it was a 55 ms walk of every name after every
    # document change). The remembered population is invalidated, so the
    # next real reconcile cannot mistake a later population for an unchanged one.
    _point_state_stamp[0] = stamp
    if not _points_on and not _auto_points_off and not _selected_cps:
        _point_state_names = None
        return False
    try:
        live = set(_doc_rows_by_name())
    except Exception:  # noqa: BLE001
        return False
    if live == _point_state_names:
        return False                  # population unchanged; nothing to reconcile
    _point_state_names = live
    dropped = False
    for name in [n for n in _points_on if n not in live]:
        _points_on.discard(name)
        dropped = True
    for name in [n for n in _auto_points_off if n not in live]:
        _auto_points_off.discard(name)
        dropped = True
    for key in [k for k in _selected_cps if k[0] not in live]:
        _selected_cps.discard(key)
        dropped = True
    return dropped


def selected_display_names():
    """Names of the currently SELECTED display objects.

    Read from Blender's selection, which is the single source of truth for what is
    selected -- `select.py` and the gumball both go through it.
    """
    try:
        import bpy

        return {ob.name for ob in bridge.selected_display_objects()}
    except Exception:  # noqa: BLE001
        return set()


def effective_points_on():
    """Every display name whose control points should be DRAWN right now.

    This is the union of two different things, and keeping them separate is the
    whole point:

    * `_points_on` -- the PERSISTENT set, from the `PointsOn` command. Stays on
      until `PointsOff`, regardless of what is selected.
    * the current SELECTION -- a TRANSIENT display. Selecting a curve shows its
      control points; deselecting hides them again.

    Selecting one curve shows its control points transiently; deselecting hides them.
    Explicit `PointsOn` state remains persistent until `PointsOff`.

    Computed at DRAW time from the live selection rather than hooked to a
    selection-changed event, for exactly the reason `prune_point_state` is:
    `_control_point_locations` already runs on every viewport redraw, so "after
    anything that could have changed the selection" is free here, while a hook
    would have to be added to every present and future path that selects something
    (click, `SelAll`, `Invert`, a command's own preselection, the gumball...). One
    of those would always be missed.

    Objects with no control points contribute nothing, so selecting a box or a
    polysurface shows nothing -- which is correct today for an honest reason:
    `bridge.control_points` handles NURBS curves only, and a surface's
    control-point grid is a traversal we do not do. That is reported by name by
    `PointsOn` rather than silently ignored.

    Automatic display applies only to an exactly one-object selection. Explicit
    `PointsOn` still enables points for any number of selected objects.
    """
    selected = selected_display_names()
    stamp = frozenset(selected)
    if stamp != _auto_points_selection[0]:
        # `PointsOff` is an action on the current selection, not a permanent opt-out.
        # Clicking away or selecting anything else resets the normal auto-grip behavior.
        _auto_points_off.clear()
        _auto_points_selection[0] = stamp
    if len(selected) != 1:
        selected = set()
    else:
        selected -= _auto_points_off
    return set(_points_on) | selected


def _control_point_locations():
    """World control points of every object whose points are switched on.

    Returns a list of ``(name, index, (x, y, z))`` so a picked marker can be traced
    back to the exact control point it belongs to.

    Only NURBS curves for now. A surface's control-point grid is a different
    traversal and is NOT guessed at here -- `PointsOn` reports what it cannot show
    rather than silently showing nothing.
    """
    return list(_cp_rows())


def _cp_rows():
    """`_control_point_locations`' rows WITHOUT the defensive copy -- the cached list
    itself, which the caller must not mutate. For the per-frame draw path: with a big
    file's Select All this is 194k rows, and copying them per pane per frame is waste."""
    out = []
    # Reconcile FIRST. This function already walks the document, so the prune is free
    # here, and it runs on every viewport redraw -- exactly after anything that could
    # have deleted an object. A `Delete` hook would be insufficient because every future
    # command that removes geometry would need a matching hook.
    prune_point_state()
    # The PERSISTENT PointsOn set, UNION the current selection -- see
    # `effective_points_on`. This is what makes clicking a curve show its points and
    # clicking away hide them again, without hooking a selection-changed event.
    showing = effective_points_on()
    # A control point cannot stay SELECTED once its object's points are no longer
    # drawn. `prune_point_state` cannot do this: it early-returns when the document
    # population is unchanged, and deselecting an object changes no population at
    # all. Without it, clicking away from a curve whose control point was grabbed
    # would hide the point while leaving the gumball attached to it -- acting on
    # something invisible.
    for key in [k for k in _selected_cps if k[0] not in showing]:
        _selected_cps.discard(key)
    if not showing:
        return out
    model = getattr(app_state, "model", None)
    file3dm = getattr(model, "file", None)
    if file3dm is None:
        return out
    # Joined curves can make this walk expensive when it runs multiple times per frame.
    # blends) selected, this walk cost ~20 ms and ran several times per frame (frame,
    # polygon, handles, markers) -- a 282 ms redraw against 23 ms with nothing selected.
    # The answer only changes with the document, so it is cached per document state:
    # the undo serial (every recorded change), `_cp_version` (in-place grip edits, which
    # happen INSIDE one open undo record during a drag) and the object count.
    # `_doc_key` carries the same three ingredients this key always had, plus the
    # model's identity -- so a different file with the same counts cannot be served
    # the previous file's points.
    key = (frozenset(showing), _control_point_doc_key())
    if _cp_cache.get("key") == key:
        return _cp_cache["out"]
    # Big-file selection: the points are ALSO kept per object for the current document
    # state, so changing the selection re-reads only the objects that were not shown
    # before. Re-reading every shown object on each selection change
    # shown object -- 5 s to Ctrl-click one curve out of a Select All.
    doc = _control_point_doc_key()
    if doc is None or _cp_obj_cache["doc"] != doc:
        _cp_obj_cache.update(doc=doc, base=_doc_key(), pts={})
    per = _cp_obj_cache["pts"]
    # the shown objects, found through the shared name map in TABLE order -- not by
    # walking every object on each selection change
    by_name = _doc_rows_by_name()
    hits = sorted(e for n in showing for e in by_name.get(n, ()))
    for _i, (name, _mode, _layer, geom, attrs) in hits:
        oid = (name, str(attrs.Id))
        pts = per.get(oid)
        if pts is None:
            pts = per[oid] = bridge.control_points(geom)
        for i, pt in enumerate(pts):
            out.append((name, i, pt))
    _cp_cache["key"], _cp_cache["out"] = key, out
    _cp_cache["serial"] += 1
    return out


def object_ids_for_names(names):
    """``{name: str Id}`` for the document objects named `names` -- matched as
    `model.object_id_by_name` matches (case-insensitively, the FIRST object with that
    name in table order); a name with no object is left out.

    For `commands.DeleteCmd`, which looked each name up with `object_id_by_name` -- a
    walk of the whole document per name, which is prohibitively expensive near
    the end of the table, so Select All + Delete made 5,702 such walks. This reads the
    shared document rows (and their Ids, `_doc_rows`)."""
    rows = _doc_rows()
    c = _doc_rows_cache
    ids = c.get("ids") if c.get("ids_of") is rows else None
    want = {str(n).lower() for n in names or ()}
    out = {}
    seen = set()
    for i, row in enumerate(rows):
        low = row[0].lower()
        if low in want and low not in seen:
            seen.add(low)
            out[low] = ids[i] if ids is not None else str(row[4].Id)
    return {n: out[str(n).lower()] for n in names or () if str(n).lower() in out}


def split_by_control_points(names):
    """``(shown, empty)``: which of the display objects `names` have control points we
    can show, and which not -- the split `PointsOn` reports.

    For `commands.PointsOnCmd`, which today looks each name up with
    `ctx.geom_by_name` -- a walk of the whole document per name. On large documents
    (a focused runtime check): Select All + `PointsOn` made 5,702 of those walks at
    19 ms and +0.45 MB RSS each, and was OOM-killed at a 5 GB cap. This reads the
    shared document rows (one walk per document state) and keeps each object's points
    in the per-object cache `_cp_rows` draws from, so the first frame after `PointsOn`
    does not read them a second time. Names match the way `geom_by_name` matches
    them: case-insensitively, the first object with that name.
    """
    first = {}
    for i, row in enumerate(_doc_rows()):
        first.setdefault(row[0].lower(), row)
    doc = _control_point_doc_key()
    if doc is None or _cp_obj_cache["doc"] != doc:
        _cp_obj_cache.update(doc=doc, base=_doc_key(), pts={})
    per = _cp_obj_cache["pts"]
    shown, empty = [], []
    for name in names or []:
        row = first.get(str(name).lower())
        if row is None:
            empty.append(name)
            continue
        dname, _mode, _layer, geom, attrs = row
        oid = (dname, str(attrs.Id))
        pts = per.get(oid)
        if pts is None:
            pts = per[oid] = bridge.control_points(geom)
        (shown if pts else empty).append(name)
    return shown, empty


#: the cache behind `_control_point_locations`, and the counter every in-place
#: control-point edit bumps (`_bump_cp_version`)
_cp_cache = {"key": None, "out": [], "serial": 0}
_cp_version = [0]
#: big-file selection: each shown object's control points, keyed ``(name, Id)``, valid
#: for ONE `_control_point_doc_key` (dropped by name by `_cp_obj_forget` for edits).
_cp_obj_cache = {"doc": None, "base": None, "pts": {}}


def _control_point_doc_key():
    """A collision-free key for geometry-derived grip caches only.

    during open Gumball record N+1, the global `_doc_key` still contains top
    serial N; Undo returns to N, so moved grips could alias restored geometry. Adding
    `next_serial` and redo state distinguishes them. This must NOT become the global key:
    `next_serial` advances when a drag merely begins, and doing that rebuilt the entire
    snap index on release (`test_snap_cache`, a measured large-file regression).
    """
    base = _doc_key()
    try:
        stack = app_state.ctx.undo
        return (base, stack.next_serial, bool(stack.can_redo))
    except Exception:  # noqa: BLE001
        return (base, None, None)


def _cp_obj_forget(names=None, before=None):
    """Drop per-object control points: all of them, or only objects `names`. With
    `before` (the `_doc_key` the edit started from) the survivors move to the current
    key -- only if the cache was valid at `before`, so an unreported edit is never
    papered over (the `_note_objects_changed` contract)."""
    c = _cp_obj_cache
    if names is None or (before is not None and c["base"] != before):
        c.update(doc=None, base=None, pts={})
    else:
        low = {str(n).lower() for n in names}
        for k in [k for k in c["pts"] if k[0].lower() in low]:
            del c["pts"][k]
        if before is not None:
            c.update(doc=_control_point_doc_key(), base=_doc_key())
    _cp_cache["key"] = None


def _bump_cp_version():
    _cp_version[0] += 1


def _drag_display_point(name, world):
    """`world` as currently SHOWN: through the running Gumball drag's display-only
    transform during a deferred drag if object `name` is being dragged, else unchanged."""
    st = _gumball_drag
    d = st.get("defer") if st is not None else None
    if not d or name not in d["start"]:
        return world
    from mathutils import Vector
    p = d["acc"] @ Vector((world[0], world[1], world[2], 1.0))
    return (p.x, p.y, p.z)


def _rebuild_one(name, geom):
    """Re-tessellate ONE object in place from WORLD-coordinate document geometry, and
    reset its matrix to identity. Returns False if it does not exist.

    `commands.apply_transform` moves document geometry and multiplies the Blender
    object's `matrix_world`, leaving the mesh in its old
    coordinates. `bridge.rebuild_object_mesh` writes the mesh in WORLD coordinates and
    (by its contract) leaves the matrix alone -- so after any Gumball move, the first
    in-place rebuild (a grip drag, #35; a part drag, #29) showed the object shifted
    TWICE. The mesh is world-space, so the matrix must be the identity."""
    if not bridge.rebuild_object_mesh(name, geom):
        return False
    ob = bpy.data.objects.get(name)
    if ob is not None:
        from mathutils import Matrix
        if ob.matrix_world != Matrix.Identity(4):
            ob.matrix_world = Matrix.Identity(4)
            bridge.note_display_changed(ob.name)
    return True


#: Which control points are SELECTED, as {(object_name, index)}. Control points
#: are grips, not objects, so they cannot live in the Blender selection -- they
#: need their own set.
_selected_cps = set()

#: How close (pixels) the cursor must come to a control point to grab it. application-defined.
CONTROL_POINT_PICK_PX = 9.0


#: The f64 world-coordinate array handed to optional native grip queries. `_cp_rows`
#: returns the same cached list until its key changes, so packing 194k points is paid
#: once rather than on every click or selection rectangle.
_native_cp_points = {"rows": None, "points": None}


def _packed_control_points(rows):
    """Cached contiguous f64 ``(N, 3)`` coordinates for `_cp_rows()` output."""
    cache = _native_cp_points
    if cache["rows"] is not rows:
        import numpy as np

        points = np.fromiter(
            (coordinate for _name, _index, world in rows for coordinate in world),
            dtype=np.float64,
            count=len(rows) * 3,
        ).reshape(-1, 3)
        cache.update(rows=rows, points=points)
    return cache["points"]


def selected_control_points():
    """The selected control points, as a sorted list of (name, index).

    Pruned first: a grip on a deleted object must not stay selected, or the Gumball
    would attach itself to geometry that is no longer there.
    """
    prune_point_state()
    return sorted(_selected_cps)


def clear_control_point_selection():
    n = len(_selected_cps)
    _selected_cps.clear()
    if n:
        _tag_redraw_viewports()
    return n


def pick_control_point(area, space, region, rx, ry,
                       tolerance=CONTROL_POINT_PICK_PX):
    """The control point nearest region pixel (rx, ry), or None.

    Picked in SCREEN space, against the very markers that were drawn, for the same
    reason the Gumball hit-tests in 2-D: that is where the cursor lives, and a
    control point has no 3-D extent to hit. Returns ``(name, index, location)``.
    """
    rv3d = getattr(space, "region_3d", None) if space is not None else None
    if rv3d is None or region is None:
        return None
    rows = _cp_rows()
    if rows:
        try:
            native = None
            if native_selection.available():
                native = native_selection.nearest_projected_point(
                    _packed_control_points(rows), rv3d.perspective_matrix,
                    region.width, region.height, (rx, ry), tolerance)
            if native is not None:
                index, _distance = native
                return None if index == native_selection.PICK_NO_HIT else rows[index]
        except Exception:  # noqa: BLE001 -- any native/UI issue keeps scalar picking
            pass
    best, best_d = None, float(tolerance)
    for name, index, world in _control_point_locations():
        pos = _project_2d(region, rv3d, world)
        if pos is None:
            continue
        d = ((pos[0] - rx) ** 2 + (pos[1] - ry) ** 2) ** 0.5
        if d <= best_d:
            best, best_d = (name, index, world), d
    return best


def select_control_point(hit, shift=False, ctrl=False):
    """Apply the reference CAD application's selection rules to a picked control point.

    The SAME rules as objects (`select.py`): Shift adds, Ctrl removes, a plain
    click replaces, and clicking away clears -- the reference CAD application documents those for "single
    objects or using a window or crossing" and control points are selected the same
    way. Returns the new selection.
    """
    action = select.modifier_action(shift=shift, ctrl=ctrl)
    key = (hit[0], hit[1]) if hit else None
    current = sorted(_selected_cps)
    hits = [key] if key else []
    wanted = select.apply(current, hits, action)
    _selected_cps.clear()
    _selected_cps.update(wanted)
    _tag_redraw_viewports()
    return sorted(_selected_cps)


def rebuild_display_keeping_selection():
    """`ctx.rebuild_display`, but the SELECTION survives it.

    `rebuild_display` deletes and recreates Blender objects, and newly created objects
    are not selected. Preserve selection so a control-point drag does not hide its own
    points after rebuilding.

    A focused runtime check selects a circle and drags control point 2:
    the drag reports "Moved 1 control point" and succeeds, and then
    `_control_point_locations` returns ZERO rows, because the curve is no longer
    selected.

    Losing selection whenever a point moves is incorrect. Restoring by name matches the
    rebuild contract: `ctx.select_by_names` re-finds recreated objects, while a name that
    no longer exists remains unselected. This wrapper applies that behavior to the three
    control-point paths that rebuild their display objects.
    """
    ctx = app_state.ctx
    try:
        import bpy

        keep = [ob.name for ob in bridge.selected_display_objects()]
        active = getattr(bpy.context.view_layer.objects, "active", None)
        active_name = active.name if active is not None else None
    except Exception:  # noqa: BLE001
        keep, active_name = [], None
    ctx.rebuild_display()
    if not keep:
        return 0
    try:
        found = ctx.select_by_names(keep)
        if active_name:
            import bpy

            ob = bpy.data.objects.get(active_name)
            if ob is not None:
                bpy.context.view_layer.objects.active = ob
        return len(found or [])
    except Exception:  # noqa: BLE001
        return 0


def move_control_points(delta):
    """Move every SELECTED control point by world `delta`. Returns how many moved.

    Edits the document geometry in place and rebuilds the display, so the curve
    visibly changes shape. One call is one undo step: the whole move is bracketed
    in a single record.
    """
    if not _selected_cps or not any(delta):
        return 0
    dx, dy, dz = (float(c) for c in delta)
    return _edit_control_points("Drag control point",
                                lambda p: (p[0] + dx, p[1] + dy, p[2] + dz))


def _edit_control_points(label, fn):
    """Move each SELECTED control point to ``fn(its location)`` (None: leave it), as
    ONE undo record named `label`, then re-draw just the edited objects. Returns how
    many moved. The grip drag (`move_control_points`) and a Gumball drag of grips
    (`_apply_xform_to_control_points`) both come here."""
    if not _selected_cps:
        return 0
    model = getattr(app_state, "model", None)
    file3dm = getattr(model, "file", None)
    if file3dm is None:
        return 0
    ctx = app_state.ctx
    by_name = {}
    for name, index in _selected_cps:
        by_name.setdefault(name, []).append(index)
    # The edited objects from the shared document rows (their geometry is the live
    # document object), not by walking the table for every selected name.
    rows_by = _doc_rows_by_name()
    edited = [(name, row[3]) for name in by_name for _i, row in rows_by.get(name, ())]
    before = _doc_key()
    ctx.begin_undo(label)
    moved = 0
    try:
        ctx.note_change(label)
        for name, geom in edited:
            # NOT gated on `geom.Points`: a PolylineCurve has no such attribute,
            # which is exactly why polylines and rectangles used to show and move
            # nothing. bridge handles both shapes.
            #
            # Every target is computed from the original positions
            # BEFORE any of them is written. This loop used to read-then-write one
            # index at a time, which double-moves a CLOSED curve's seam: a radius-5
            # circle's `cv[0]` and `cv[8]` are coincident, `set_control_point` moves
            # BOTH when either is given (and that is correct -- it is what keeps
            # the curve closed), so the second index then read its ALREADY-MOVED
            # partner and moved the pair again. Verified
            # (a focused runtime check): the seam travelled 2.0 while an ordinary
            # point in the same drag travelled 1.0.
            #
            # The curve stayed CLOSED throughout -- gap 0.0 -- which is exactly why
            # every existing gate passed and why this reads as "deforms" rather than
            # "tears". Snapshotting first fixes it without special-casing seams:
            # coincident CVs produce the SAME target, so applying both is idempotent.
            # (`bridge.map_control_points` plans from the originals the same way.)
            moved += bridge.map_control_points(geom, by_name[name], fn)
    finally:
        ctx.end_undo()
    if moved:
        _bump_cp_version()
        # the curve's shape changed, so its tessellation and every cached snap
        # target are stale. This used to be
        # `rebuild_display_keeping_selection` -- EVERY object deleted, recreated and
        # re-tessellated, on every mouse move of a drag. Only the edited objects are
        # re-tessellated now, in place; the full rebuild is the fallback.
        full = False
        for name, geom in edited:
            try:
                if not _rebuild_one(name, geom):
                    full = True
            except Exception:  # noqa: BLE001
                full = True
        if full:
            rebuild_display_keeping_selection()
            invalidate_snap_cache()
        else:
            # exactly these objects changed, in place: every per-object cache keeps
            # the rest (`invalidate_snap_cache` dropped them all -- the snap index,
            # the document rows -- on every mouse move)
            _note_objects_changed(set(by_name), before, in_place=True)
        _tag_redraw_viewports()
    return moved


def delete_selected_control_points(ctx=None):
    """: Delete with control points selected removes THOSE points, not the
    curve ("when points are off, i select a curve, then select a point, it should
    delete the point i selected. right now it delets th whole curve").

    Each curve goes through  `bridge.remove_control_points` -- exact, never a
    refit -- and is swapped in with `model.replace_geometry`, which keeps its Id, name
    and layer. A curve the backend refuses is left alone and the reason is said. The
    caller brackets this in ONE undo record (`DeleteCmd`). The grip selection is
    cleared: the remaining grips are numbered afresh. Returns the line to log, or None
    when no grip is selected (Delete then deletes objects, as before)."""
    grips = selected_control_points()
    if not grips:
        return None
    ctx = ctx or app_state.ctx
    model = ctx.model
    by_name = {}
    for name, index in grips:
        by_name.setdefault(name, []).append(index)
    rows_by = _doc_rows_by_name()
    done, lines, total = [], [], 0
    for name in sorted(by_name):
        hits = rows_by.get(name, ())
        if not hits:
            lines.append(f"{name}: no such object")
            continue
        row = hits[0][1]
        geom, attrs = row[3], row[4]
        new, msg = bridge.remove_control_points(geom, by_name[name])
        if new is None:
            lines.append(f"{name}: {msg}")
            continue
        ctx.note_change("Delete")              # before the first mutation
        if not model.replace_geometry(attrs.Id, new):
            lines.append(f"{name}: no such object")
            continue
        done.append((name, str(attrs.Id)))
        total += len(set(by_name[name]))
    _selected_cps.clear()
    if done:
        _bump_cp_version()
        ids = {i for _n, i in done}
        fresh = {str(f.Attributes.Id): f.Geometry for f in model.file.Objects
                 if str(f.Attributes.Id) in ids}
        full = False
        for name, oid in done:
            try:
                if oid not in fresh or not _rebuild_one(name, fresh[oid]):
                    full = True
            except Exception:  # noqa: BLE001
                full = True
        if full:
            rebuild_display_keeping_selection()
        invalidate_snap_cache()
    _tag_redraw_viewports()
    head = ("Deleted 1 control point." if total == 1
            else f"Deleted {total} control points.") if done else "No control points deleted."
    return "\n".join([head] + lines)


#: An in-progress control-point drag, or None. Shaped like `_gumball_drag`: the
#: record is opened at drag start and closed at release, so the whole drag is ONE
#: undo step however many incremental moves it applies.
_cp_drag = None


def control_point_dragging():
    return _cp_drag is not None


def _cp_drag_begin(area, space, region, rx, ry):
    """Start dragging the selected control points. True if a drag started.

    Tracked on the active CPlane, like the Gumball's free-move origin handle: that
    is the surface the reference CAD application drags on ("A construction plane is like a tabletop that
    the cursor normally moves on").
    """
    global _cp_drag

    if not _selected_cps:
        return False
    start = pick_point(area, space, region, rx, ry)
    if start is None:
        return False
    note_user_interaction()
    _cp_drag = {"last": start, "pane": _pane_key(area), "moved": 0}
    app_state.ctx.begin_undo("Drag control point")
    return True


def _cp_drag_update(mouse_x, mouse_y):
    """Advance the drag, applying the INCREMENT since the last event."""
    st = _cp_drag
    if st is None:
        return False
    # holds the idle passes (zoom re-tessellation, snap warm-up) for the drag, as the
    # Gumball's does: otherwise the zoom pass replans on
    # every grip move -- each one a new document state -- at ~150 ms a tick
    note_user_interaction()
    area, space, region = _area_by_pane_key(st["pane"])
    if region is None or space is None:
        return False
    now = pick_point(area, space, region,
                    mouse_x - region.x, mouse_y - region.y)
    if now is None:
        return False
    last = st["last"]
    delta = (now[0] - last[0], now[1] - last[1], now[2] - last[2])
    if not any(delta):
        return False
    st["last"] = now
    n = move_control_points(delta)
    st["moved"] += n
    return n > 0


def _cp_drag_end():
    """Finish the drag. Returns a line to log, or None if nothing moved."""
    global _cp_drag

    st = _cp_drag
    _cp_drag = None
    try:
        app_state.ctx.end_undo()
    except Exception:  # noqa: BLE001
        pass
    if st is None or not st["moved"]:
        return None
    n = len(_selected_cps)
    return f"Moved {n} control point{'s' if n != 1 else ''}"


def _control_polygons():
    """``{display_name: [world_point, ...]}`` -- each object's control polygon.

    The polyline through the object's control points **in index order**, which is
    what the reference CAD application's control polygon is: "This creates polylines ... that have vertices
    at the original objects' control points" (the reference CAD application documentation).

    Built by grouping `_control_point_locations`, so it shows exactly the objects
    whose points are on and it inherits that function's stale-object prune for free.
    Objects contributing fewer than two points are dropped -- a one-point polygon is
    not a line.
    """
    return dict(_control_polygons_cached())


def _control_polygons_cached(rows=None):
    """`_control_polygons` without the copy (the caller must not mutate it), rebuilt
    only when the shown control points or the closed-curve cache change. Big-file
    selection: regrouping 194k points per pane per frame was part of a 6 s frame.
    `rows`: the caller's `_cp_rows` of this same moment, to save re-deriving them."""
    if rows is None:
        rows = _cp_rows()
    pc = _polygon_cache
    token = (_cp_cache["serial"], id(rows), len(rows))
    if pc["polys_token"] == token and pc["key"] is not None and pc["key"] == pc["polys_key"]:
        return pc["polys"]
    groups = {}
    for name, index, world in rows:
        groups.setdefault(name, []).append((index, world))
    out = {}
    closed = set()
    if groups:
        # Cache closed state; walking the whole document in every pane on every redraw
        # with one curve selected -- this walk reads every object. Which of the shown
        # objects are closed changes only with the document, so it is cached on the
        # document key plus the names asked about.
        key = (_doc_key(), frozenset(groups))
        if key[0] is not None and _polygon_cache["key"] == key:
            closed = set(_polygon_cache["closed"])
        else:
            # through the shared name map, not a walk of every object: Verified 57 ms of
            # after click-selecting one curve in a large document
            by_name = _doc_rows_by_name()
            for name in groups:
                for _i, row in by_name.get(name, ()):
                    if bool(getattr(row[3], "IsClosed", False)):
                        closed.add(name)
            _polygon_cache["key"], _polygon_cache["closed"] = key, frozenset(closed)
    for name, pairs in groups.items():
        pairs.sort(key=lambda p: p[0])
        if len(pairs) >= 2:
            poly = [w for _i, w in pairs]
            # A periodic curve lists only distinct control points; wrapped copies are
            # not grips), so its polygon must close back to the first point.
            if name in closed and poly[0] != poly[-1]:
                poly.append(poly[0])
            out[name] = poly
    pc["polys"], pc["polys_token"], pc["polys_key"] = out, token, pc["key"]
    pc["builds"] += 1
    return out


# ---------------------------------------------------------------------------
# For degree-1 curves the control polygon is the curve itself, so drawing it again as
# dashes obscures the actual line. Those spans are left
# out of the DRAWN polygon; the polygon's data (`_control_polygons`, one vertex per
# control point) is unchanged.
# ---------------------------------------------------------------------------

#: application-defined -- above this many shown objects the coincidence test is skipped and every
#: polygon is drawn whole: telling which spans are straight reads each object's
#: segments, and a large selection would otherwise pay that cost on every change.
POLYGON_FLAT_MAX_OBJECTS = 200


def _flat_polygon_spans(geom):
    """Which spans of `geom`'s control polygon lie ON the curve: ``"all"`` for a
    degree-1 curve (line, polyline, degree-1 NURBS), else a set of grip-index pairs
    ``frozenset({a, b})`` -- for a joined curve, the spans inside its degree-1
    segments. Grip indices are `bridge.control_points`' (joints counted once)."""
    import rhino3dm as r3

    def straight(c):
        if isinstance(c, (r3.LineCurve, r3.PolylineCurve)):
            return True
        return isinstance(c, r3.NurbsCurve) and int(getattr(c, "Degree", 0)) == 1

    if isinstance(geom, r3.PolyCurve):
        got = bridge._polycurve_table(geom)
        if got is None:
            return frozenset()
        leaves, grips, leaf_pts = got
        where = {}
        for g, entries in enumerate(grips):
            for kj in entries:
                where[kj] = g
        out = set()
        for k, leaf in enumerate(leaves):
            if straight(leaf):
                for j in range(len(leaf_pts[k]) - 1):
                    a, b = where.get((k, j)), where.get((k, j + 1))
                    if a is not None and b is not None and a != b:
                        out.add(frozenset((a, b)))
        return frozenset(out)
    return "all" if straight(geom) else frozenset()


#: ``{"src": the polygons dict, "key", "runs": [(name, [world, ...]), ...]}``
_polygon_runs_cache = {"src": None, "key": None, "runs": []}


def _polygon_draw_runs(polys):
    """The dashed polylines to DRAW for `polys` (`_control_polygons_cached`): each
    object's polygon minus the spans that lie on its curve, split into
    ``(name, points)`` runs. Cached while `polys` and the document stand."""
    c = _polygon_runs_cache
    key = (_doc_key(), len(polys))
    if c["src"] is polys and c["key"] == key:
        return c["runs"]
    flat = {}
    if 0 < len(polys) <= POLYGON_FLAT_MAX_OBJECTS:
        by_name = _doc_rows_by_name()
        for name in polys:
            hits = by_name.get(name, ())
            if hits:
                try:
                    flat[name] = _flat_polygon_spans(hits[0][1][3])
                except Exception:  # noqa: BLE001 (draw it whole)
                    pass
    runs = []
    for name, pl in polys.items():
        f = flat.get(name)
        if not f:
            runs.append((name, pl))
            continue
        if f == "all":
            continue
        n = len(pl) - 1 if len(pl) > 2 and pl[0] == pl[-1] else len(pl)  # grips
        cur = [pl[0]]
        for i in range(len(pl) - 1):
            a, b = i % n, (i + 1) % n
            if frozenset((a, b)) in f:
                if len(cur) >= 2:
                    runs.append((name, cur))
                cur = [pl[i + 1]]
            else:
                cur.append(pl[i + 1])
        if len(cur) >= 2:
            runs.append((name, cur))
    c.update(src=polys, key=key, runs=runs)
    return runs


#: the cache behind `_control_polygons`' closed-curve test, and its whole result
#: (`polys`, valid while the rows token and the closed-test key both still match)
_polygon_cache = {"key": None, "closed": frozenset(), "polys": {}, "polys_token": None,
                  "polys_key": None, "builds": 0}


# ---------------------------------------------------------------------------
# BIG-FILE CONTROL POINTS: markers and the control polygon as cached GPU batches
#
# Projecting and rebuilding hundreds of thousands of control-point markers in Python
# for every pane makes each frame unusably slow.
#
# Now the markers are ONE batch per style holding each point's WORLD position plus the
# marker template's pixel offsets (`pointdisplay.marker_template` -- the very triangles
# and outline `marker` produces). A small shader projects the point and adds the offset
# in pixels, so the batch is built once per change of the shown points and reused by
# every pane and every frame; an idle redraw does no per-point Python at all
# (the live-window UI checks holds that by counting). The control polygon's dash phase depends
# on the view, so its batch is rebuilt per pane only when that pane's VIEW changes,
# with numpy (`pointdisplay.dash_phases`, the twin of `dashed_polyline`).
#
# The look is unchanged: same template, same colours, same 1 px lines, same pixel size
# at the UI scale -- and the live-window checks A/B diffs this path against the per-point one.
# ---------------------------------------------------------------------------

_CP_VERT_SRC = """
void main()
{
  vec4 p = persmat * vec4(pos, 1.0);
  v_arc = arc;
  if (p.w <= 0.0) {
    /* behind the eye: `location_3d_to_region_2d` gives None, so draw nothing */
    gl_Position = vec4(0.0, 0.0, 2.0, 1.0);
  }
  else {
    /* + 0.01 px: the POST_PIXEL ortho the per-point path draws in, exactly --
     * `ED_region_pixelspace` -> `wmOrtho2_region_pixelspace` -> offset -0.01
     * (wm_subwindow.cc). Without any offset, outlines on whole pixels fell on pixel
     * edges and came out broken. It was + 0.375 (GLA_PIXEL_OFS, the WINDOW ortho's):
     * 0.365 px off, so any marker or dash edge mid-pixel landed a pixel away from the
     * per-point drawing -- MEASURED by uishot's A/B at interface scale 0.95. */
    gl_Position = vec4(p.xy / p.w + (ofs + 0.01) * 2.0 / viewport, 0.0, 1.0);
  }
}
"""

_CP_FRAG_SRC = """
void main()
{
  if (dash > 0.0 && mod(v_arc, dash + gap) >= dash) {
    discard;
  }
  /* Blender's `blender_srgb_to_framebuffer_space`, which the builtin UNIFORM_COLOR
   * shader applies and a Python shader cannot include: our colours are sRGB, and the
   * viewport's overlay target is an sRGB buffer that expects LINEAR writes. Blender
   * sets the builtin `srgbTarget` uniform on bind. MEASURED without it: the navy
   * control-point outline came out visibly lighter than the per-point drawing. */
  vec4 c = color;
  if (srgbTarget) {
    vec3 s = max(c.rgb, vec3(0.0));
    c.rgb = mix(s * (1.0 / 12.92), pow((s + 0.055) * (1.0 / 1.055), vec3(2.4)),
                step(vec3(0.04045), s));
  }
  fragColor = c;
}
"""

#: the shader + vertex format (built once), or why it could not be built
_cp_gpu = {"shader": None, "failed": None}
#: per marker job ("doc", "plain", "active"): {"key", "fill", "line"}
_cp_marker_batches = {}
#: per pane (`_pane_key`): {"key", "batch"} for the control polygon
_cp_poly_batches = {}
#: numpy views of the shown points / polygons, keyed by the rows they came from
_cp_arrays = {"token": None, "xyz": None, "ride": None, "active": None,
              "poly_token": None, "poly": None}
#: counters the live-window checks gate reads (an idle redraw must not move the build counts)
_cp_draw_stats = {"marker_builds": 0, "polygon_builds": 0, "batch_draws": 0}
#: TEST HOOK (the live-window checks look A/B): draw markers the old per-point way
_CP_FORCE_PYTHON = [False]


def _cp_gpu_shader():
    """The marker/polygon shader, or None (said ONCE on the console) when this GPU
    backend cannot build it -- the draw then uses the per-point Python path, which is
    correct but slow on big selections. Never a silent downgrade."""
    g = _cp_gpu
    if g["shader"] is not None or g["failed"] is not None:
        return g["shader"]
    try:
        import gpu
        import numpy  # noqa: F401

        iface = gpu.types.GPUStageInterfaceInfo("narwhal_cp_iface")
        iface.no_perspective("FLOAT", "v_arc")
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant("MAT4", "persmat")
        info.push_constant("VEC4", "color")
        info.push_constant("VEC2", "viewport")
        info.push_constant("FLOAT", "dash")
        info.push_constant("FLOAT", "gap")
        info.push_constant("BOOL", "srgbTarget")
        info.vertex_in(0, "VEC3", "pos")
        info.vertex_in(1, "VEC2", "ofs")
        info.vertex_in(2, "FLOAT", "arc")
        info.vertex_out(iface)
        info.fragment_out(0, "VEC4", "fragColor")
        info.vertex_source(_CP_VERT_SRC)
        info.fragment_source(_CP_FRAG_SRC)
        shader = gpu.shader.create_from_info(info)
        fmt = gpu.types.GPUVertFormat()
        fmt.attr_add(id="pos", comp_type="F32", len=3, fetch_mode="FLOAT")
        fmt.attr_add(id="ofs", comp_type="F32", len=2, fetch_mode="FLOAT")
        fmt.attr_add(id="arc", comp_type="F32", len=1, fetch_mode="FLOAT")
    except Exception as e:  # noqa: BLE001
        g["failed"] = repr(e)
        print(f"[narwhal3d] control-point batch shader unavailable ({e!r}); markers are "
              f"drawn point by point in Python -- correct, but slow on big selections")
        return None
    g.update(shader=shader, info=info, iface=iface, fmt=fmt)
    return shader


def _cp_vbo(pos, ofs, arc):
    """A vertex buffer in the marker shader's format, from float arrays."""
    import gpu
    import numpy as np

    vbo = gpu.types.GPUVertBuf(_cp_gpu["fmt"], len(pos))
    vbo.attr_fill("pos", np.ascontiguousarray(pos, dtype=np.float32))
    vbo.attr_fill("ofs", np.ascontiguousarray(ofs, dtype=np.float32))
    vbo.attr_fill("arc", np.ascontiguousarray(arc, dtype=np.float32))
    return vbo


def _cp_batch(prim, vbo, index=None):
    """A GPU batch of `prim` over `vbo` (`index`: optional element array)."""
    import gpu
    import numpy as np

    if index is None:
        return gpu.types.GPUBatch(type=prim, buf=vbo)
    ibo = gpu.types.GPUIndexBuf(type=prim, seq=np.ascontiguousarray(index, dtype=np.int32))
    return gpu.types.GPUBatch(type=prim, buf=vbo, elem=ibo)


def _cp_marker_batch(job, key, xyz, style):
    """``(fill_batch, line_batch)`` drawing `style` markers at every row of `xyz`,
    cached per `job` until `key` changes. Either may be None (nothing to draw)."""
    import numpy as np

    hit = _cp_marker_batches.get(job)
    if hit is not None and hit["key"] == key:
        return hit["fill"], hit["line"]
    fill = line = None
    n = len(xyz)
    offs, tris, lines = pointdisplay.marker_template(style, pointdisplay.DEFAULT_SIZE_PX)
    if n and offs:
        k = len(offs)
        pos = np.repeat(np.asarray(xyz, dtype=np.float32), k, axis=0)
        ofs = np.tile(np.asarray(offs, dtype=np.float32), (n, 1))
        # ONE vertex buffer; the fill and the outline index into it
        vbo = _cp_vbo(pos, ofs, np.zeros(n * k, dtype=np.float32))
        base = np.arange(n, dtype=np.int32) * k
        if tris:
            idx = (np.asarray(tris, dtype=np.int32)[None] + base[:, None, None])
            fill = _cp_batch("TRIS", vbo, idx.reshape(-1, 3))
        if lines:
            idx = (np.asarray(lines, dtype=np.int32)[None] + base[:, None, None])
            line = _cp_batch("LINES", vbo, idx.reshape(-1, 2))
    _cp_marker_batches[job] = {"key": key, "fill": fill, "line": line}
    _cp_draw_stats["marker_builds"] += 1
    return fill, line


def _cp_ride():
    """``(signature, defer)`` of the running Gumball drag's display-only transform that
    grips must ride (`_drag_display_point`), or ``(None, None)``."""
    st = _gumball_drag
    d = st.get("defer") if st is not None else None
    if not d:
        return None, None
    return (id(d), tuple(tuple(r) for r in d["acc"])), d


def _cp_apply_ride(xyz, names, d):
    """`xyz` with the rows whose name `d` is dragging moved by its transform --
    `_drag_display_point`, vectorised (same 4x4, same "drop w")."""
    import numpy as np

    mask = np.fromiter((n in d["start"] for n in names), dtype=bool, count=len(names))
    if not mask.any():
        return xyz
    a = np.array(d["acc"], dtype=np.float64)
    out = np.array(xyz, dtype=np.float64, copy=True)
    sel = out[mask]
    out[mask] = sel @ a[:3, :3].T + a[:3, 3]
    return out


def _cp_marker_arrays(rows):
    """``(token, plain_xyz, active_xyz)`` for the shown control points: split by
    `_selected_cps`, riding a Gumball drag. Cached on the rows, the control-point
    selection and the drag transform -- an idle frame reuses the arrays and the key."""
    import numpy as np

    c = _cp_arrays
    token = (_cp_cache["serial"], id(rows), len(rows))
    if c["token"] != token:
        c["token"] = token
        c["xyz"] = np.array([w for _n, _i, w in rows], dtype=np.float64).reshape(-1, 3)
        c["ride"] = [n for n, _i, _w in rows]        # names, for `_cp_apply_ride`
        c["active"] = None
    ride_sig, d = _cp_ride()
    sel_key = frozenset(_selected_cps)
    key = (token, sel_key, ride_sig)
    if c["active"] is not None and c["active"][0] == key:
        return c["active"][1]
    xyz = c["xyz"]
    if d is not None:
        xyz = _cp_apply_ride(xyz, c["ride"], d)
    if sel_key:
        act = np.fromiter(((n, i) in sel_key for n, i, _w in rows), dtype=bool,
                          count=len(rows))
    else:
        act = np.zeros(len(rows), dtype=bool)
    got = (key, xyz[~act], xyz[act])
    c["active"] = (key, got)
    return got


def _cp_polygon_batch(area, region, rv3d, polys):
    """This pane's control-polygon batch (LINES with a per-vertex dash phase), or None.
    Rebuilt only when the polygons, the drag transform or THIS pane's view changed."""
    import numpy as np

    dash = float(pointdisplay.CONTROL_POLYGON_DASH_PX)
    gap = float(pointdisplay.CONTROL_POLYGON_GAP_PX)
    if not polys or dash <= 0 or gap <= 0:
        return None          # `dashed_polyline`: a non-positive dash or gap draws nothing
    c = _cp_arrays
    # `polys`: the DRAWN runs, ``[(name, points)]`` (`_polygon_draw_runs`, )
    ptoken = (id(polys), _polygon_cache["builds"])
    if c["poly_token"] != ptoken:
        names, verts, first = [], [], []
        for name, pl in polys:
            for j, w in enumerate(pl):
                names.append(name)
                verts.append(w)
                first.append(j == 0)
        c["poly_token"] = ptoken
        c["poly"] = (names, np.array(verts, dtype=np.float64).reshape(-1, 3),
                     np.array(first, dtype=bool))
    names, xyz, first = c["poly"]
    ride_sig, d = _cp_ride()
    m = rv3d.perspective_matrix
    view = (tuple(tuple(r) for r in m), region.width, region.height)
    key = (ptoken, ride_sig, view, dash, gap)
    pane = _pane_key(area)
    hit = _cp_poly_batches.get(pane)
    if hit is not None and hit["key"] == key:
        return hit["batch"]
    if d is not None:
        xyz = _cp_apply_ride(xyz, names, d)
    # `location_3d_to_region_2d`, vectorised: None (here: invalid) when w <= 0
    mat = np.array(m, dtype=np.float64)
    clip = xyz @ mat[:, :3].T + mat[:, 3]
    w = clip[:, 3]
    valid = w > 0.0
    ndc = clip[:, :2] / np.where(valid, w, 1.0)[:, None]
    half = np.array([region.width / 2.0, region.height / 2.0])
    px = half + half * ndc
    seg, a0, a1 = pointdisplay.dash_phases(px, valid, first, dash + gap)
    batch = None
    if len(seg):
        pos = np.empty((2 * len(seg), 3))
        pos[0::2], pos[1::2] = xyz[seg], xyz[seg + 1]
        arc = np.empty(2 * len(seg))
        arc[0::2], arc[1::2] = a0, a1
        batch = _cp_batch("LINES", _cp_vbo(pos, np.zeros((len(pos), 2)), arc))
    _cp_poly_batches[pane] = {"key": key, "batch": batch}
    _cp_draw_stats["polygon_builds"] += 1
    return batch


def _draw_point_batches(area, region, rv3d, doc_pts, rows, polys):
    """Draw the control polygon, then point objects and control points, from cached
    batches (see the section comment). The caller has set the blend state."""
    import numpy as np

    shader = _cp_gpu["shader"]
    size = pointdisplay.DEFAULT_SIZE_PX
    jobs = []
    if doc_pts:
        dkey = (id(_point_loc_cache["out"]), len(doc_pts), pointdisplay.DEFAULT_POINT_STYLE,
                size)
        jobs.append(("doc", dkey, lambda: np.array([w for _n, w in doc_pts], dtype=np.float64),
                     pointdisplay.DEFAULT_POINT_STYLE, POINT_OBJECT_COLOR))
    if rows:
        key, plain, active = _cp_marker_arrays(rows)
        if len(plain):
            jobs.append(("plain", (key, pointdisplay.CONTROL_POINT_STYLE, size),
                         lambda: plain, pointdisplay.CONTROL_POINT_STYLE,
                         CONTROL_POINT_COLOR))
        if len(active):
            jobs.append(("active", (key, pointdisplay.ACTIVE_CONTROL_POINT_STYLE, size),
                         lambda: active, pointdisplay.ACTIVE_CONTROL_POINT_STYLE,
                         ACTIVE_CONTROL_POINT_COLOR))
    shader.bind()
    shader.uniform_float("persmat", rv3d.perspective_matrix)
    shader.uniform_float("viewport", (float(region.width), float(region.height)))
    # the polygon FIRST, under the markers (see `draw_point_markers`)
    poly = _cp_polygon_batch(area, region, rv3d, polys)
    if poly is not None:
        shader.uniform_float("dash", float(pointdisplay.CONTROL_POLYGON_DASH_PX))
        shader.uniform_float("gap", float(pointdisplay.CONTROL_POLYGON_GAP_PX))
        shader.uniform_float("color", CONTROL_POLYGON_COLOR)
        poly.draw(shader)
        _cp_draw_stats["batch_draws"] += 1
    shader.uniform_float("dash", 0.0)
    shader.uniform_float("gap", 0.0)
    for job, key, xyz_of, style, color in jobs:
        hit = _cp_marker_batches.get(job)
        if hit is not None and hit["key"] == key:
            fill, line = hit["fill"], hit["line"]
        else:
            fill, line = _cp_marker_batch(job, key, xyz_of(), style)
        if fill is not None:
            shader.uniform_float(
                "color", POINT_WHITE if pointdisplay.fill_is_white(style) else color)
            fill.draw(shader)
        if line is not None:
            shader.uniform_float("color", color)
            line.draw(shader)
        _cp_draw_stats["batch_draws"] += 1


def draw_point_markers():
    """POST_PIXEL draw handler: point objects, control points, and the selection
    rectangle.

    All three are pixel-space overlays, so they share one handler rather than
    registering three that would each have to re-derive the pane.
    """
    context = bpy.context
    area = getattr(context, "area", None)
    region = getattr(context, "region", None)
    if area is None or region is None or area.type != "VIEW_3D":
        return
    space = next((s for s in area.spaces if s.type == "VIEW_3D"), None)
    rv3d = getattr(space, "region_3d", None) if space is not None else None
    if rv3d is None:
        return

    doc_pts = _document_point_locations()
    rows = _cp_rows()
    # The control polygon, dashed, joining each object's control points in order.
    # Drawn FIRST, under the markers -- see below.
    raw_polys = _control_polygons_cached(rows)
    # the live selection rectangle, drawn only in the pane the drag started in
    drag = selection_drag_rect()
    if drag is not None and drag[0] != _pane_key(area):
        drag = None
    highlight = bool(_selected_subobjects)
    hovering = hover_pick() is not None or bool(
        _gumball_drag is not None and _gumball_drag.get("ext_ghost"))
    if not doc_pts and not rows and drag is None and not highlight and not raw_polys \
            and not hovering:
        return

    shader = _uniform_color_shader()
    if shader is None:
        return
    try:
        import gpu
        from gpu_extras.batch import batch_for_shader
    except Exception:  # noqa: BLE001
        return

    # Big-file selection: cached GPU batches (see `_draw_point_batches`) unless this
    # backend cannot build their shader -- then the per-point path below, said once.
    batched = not _CP_FORCE_PYTHON[0] and _cp_gpu_shader() is not None
    jobs, polygons = [], {}
    if not batched:
        pts = [world for _name, world in doc_pts]
        if pts:
            jobs.append((pts, pointdisplay.DEFAULT_POINT_STYLE, POINT_OBJECT_COLOR))
        # Control points, split by selection: a SELECTED one is drawn with the reference CAD application's
        # ActivePoint style, which is "like a control point but includes vertical/
        # horizontal crosshair lines" (the reference CAD application.Display.PointStyle). That is how you can
        # see which grips a drag will move.
        plain, active = [], []
        # during a Gumball drag the objects move on screen before the
        # document does (#31), so their grips and polygon must ride the same display
        # transform (the batch path does the same in `_cp_apply_ride`)
        ride = _drag_display_point
        for name, index, world in rows:
            (active if (name, index) in _selected_cps else plain).append(ride(name, world))
        if plain:
            jobs.append((plain, pointdisplay.CONTROL_POINT_STYLE, CONTROL_POINT_COLOR))
        if active:
            jobs.append((active, pointdisplay.ACTIVE_CONTROL_POINT_STYLE,
                         ACTIVE_CONTROL_POINT_COLOR))
        polygons = [(n, [ride(n, w) for w in pl]) for n, pl in _polygon_draw_runs(raw_polys)]

    prev_blend = gpu.state.blend_get()
    gpu.state.blend_set("ALPHA")
    try:
        if batched and (doc_pts or rows or raw_polys):
            _draw_point_batches(area, region, rv3d, doc_pts, rows,
                                _polygon_draw_runs(raw_polys) if raw_polys else [])
        shader.bind()
        # The control polygon goes down FIRST so the point markers land on top of
        # it. Drawn after the markers it would cross every one of them, which is
        # the opposite of "subtle".
        if polygons:
            segs = []
            for _pname, world_pts in polygons:
                # The dash lengths are passed EXPLICITLY rather than left to
                # `dashed_polyline`'s defaults. Python binds default arguments once,
                # at definition time, so relying on them would freeze the pattern at
                # import and make `CONTROL_POLYGON_DASH_PX` a dead constant that
                # looks live. (A gate caught exactly that: setting it to 0 changed
                # nothing on screen.) Reading it here keeps "set 0 to turn it off"
                # working and leaves room for it to become a real setting.
                segs.extend(pointdisplay.dashed_polyline(
                    [_project_2d(region, rv3d, w) for w in world_pts],
                    pointdisplay.CONTROL_POLYGON_DASH_PX,
                    pointdisplay.CONTROL_POLYGON_GAP_PX))
            if segs:
                shader.uniform_float("color", CONTROL_POLYGON_COLOR)
                batch_for_shader(shader, "LINES", {"pos": segs}).draw(shader)
        for locations, style, color in jobs:
            fills, outlines = [], []
            for world in locations:
                pos = _project_2d(region, rv3d, world)
                if pos is None:
                    continue        # behind the camera: nothing to draw
                fill, outline = pointdisplay.marker(
                    style, pos[0], pos[1], pointdisplay.DEFAULT_SIZE_PX)
                fills.extend(fill)
                outlines.extend(outline)
            if fills:
                shader.uniform_float(
                    "color",
                    POINT_WHITE if pointdisplay.fill_is_white(style) else color)
                batch_for_shader(shader, "TRIS", {"pos": fills}).draw(shader)
            if outlines:
                shader.uniform_float("color", color)
                batch_for_shader(shader, "LINES", {"pos": outlines}).draw(shader)
        if highlight:
            draw_subobject_highlight(region, rv3d, shader, batch_for_shader)
        if hovering:
            draw_hover_pick(region, rv3d, shader, batch_for_shader)
        if drag is not None:
            _pane, rect, mode = drag
            x0, y0, x1, y1 = rect
            corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
            segs = []
            for i in range(4):
                segs.append(corners[i])
                segs.append(corners[(i + 1) % 4])
            stroke = (CROSSING_RECT_COLOR if mode == select.CROSSING
                      else WINDOW_RECT_COLOR)
            # the translucent FILL first (the reference CAD application's SelectionWindowFillColor),
            # then a heavier stroke over it
            shader.uniform_float("color", tuple(stroke[:3]) + (SELECTION_RECT_FILL_ALPHA,))
            batch_for_shader(shader, "TRIS", {"pos": [corners[0], corners[1], corners[2],
                                                      corners[0], corners[2], corners[3]]}
                             ).draw(shader)
            shader.uniform_float("color", stroke)
            gpu.state.line_width_set(SELECTION_RECT_STROKE_PX)
            batch_for_shader(shader, "LINES", {"pos": segs}).draw(shader)
            gpu.state.line_width_set(1.0)
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] point marker draw failed: {e!r}")
    finally:
        gpu.state.blend_set(prev_blend)


# ---------------------------------------------------------------------------
# SUB-OBJECT selection (Ctrl+Shift+click)
#
# Verified (the reference CAD application keyboard documentation):
#  "To select sub-object parts with a mouse ... Ctrl (CMD) + Shift + Left mouse
#  click, window, or crossing."
#  "To deselect sub-object parts with a mouse -- Ctrl (CMD) + Shift + Left mouse
#  click."
# The same chord for both, so it is a TOGGLE. The rules are in `subobject.py`.
#
# Only CURVE SEGMENTS are pickable. That is the sub-object our geometry actually
# has: `Join` makes multi-segment curves and `Explode` takes them apart, so a
# segment is a real, nameable part. Polysurface faces and surface/mesh edges are
# recognised and reported as not implemented (`subobject.NOT_IMPLEMENTED`) rather
# than quietly selecting the whole object.
# ---------------------------------------------------------------------------

#: Which sub-objects are SELECTED, as {(object_name, kind, index)}. Like control
#: points, sub-objects are parts rather than objects, so they cannot live in the
#: Blender selection and need their own set.
_selected_subobjects = set()

#: How close (pixels) the cursor must come to a segment to pick it. application-defined -- the reference CAD application
#: documents the chord but not its pick radius. Wider than CONTROL_POINT_PICK_PX
#: because a segment is a line you aim at, not a marker you land on.
SUBOBJECT_PICK_PX = 10.0

#: the reference CAD application draws a selected sub-object in the same yellow it uses for a selected
#: object (SELECT_COLOR), and this highlight is drawn OVER the wire.
SUBOBJECT_COLOR = (1.0, 0.85, 0.0, 1.0)


def _subobject_candidates(names=None):
    """Every pickable sub-object in the document -- or only those of the objects in
    `names` (a set), which is what the SELECTION readers need.

    Returns ``[((name, kind, index), world_points, grips)]``. Recomputed per pick
    rather than cached: `Join`, `Explode`, undo and every control-point drag change
    it, and a stale candidate list would select a segment that no longer exists --
    the same reason in-progress snap targets are not cached.

    On large imports, scanning the whole document on every redraw dominates Gumball
    rendering even when no sub-object is selected. Callers now pass `names`.
    """
    out = []
    if names is not None and not names:
        return out
    model = getattr(app_state, "model", None)
    file3dm = getattr(model, "file", None)
    if file3dm is None:
        return out
    for fobj in file3dm.Objects:
        name = fobj.Attributes.Name or ""
        if names is not None and name not in names:
            continue
        for index, points, grips in bridge.curve_segments(fobj.Geometry):
            out.append(((name, subobject.SEGMENT, index), points, grips))
    return out


def _selected_object_names_of_subobjects():
    """The names of objects with a selected sub-object (cheap: no geometry read)."""
    return {k[0] for k in _selected_subobjects}


def selected_subobjects():
    """The selected sub-objects, as a sorted list of (name, kind, index)."""
    return sorted(_selected_subobjects)


def clear_subobject_selection():
    """Drop the whole sub-object selection. Returns how many were dropped.

    Verified: Esc "Deselects all selected objects" (the reference CAD application keyboard documentation), so
    the cancel path calls this.
    """
    n = len(_selected_subobjects)
    _selected_subobjects.clear()
    if n:
        _tag_redraw_viewports()
    return n


# Faces, edges, and vertices of solids and polysurfaces are selectable sub-objects,
# from exact topology (`bridge.subobject_parts` -- the master's, never the display
# proxy's triangles). Keys are (object name, "Face" | "Edge" | "Vertex", id).
_BREP_KINDS = (subobject.FACE, subobject.EDGE, subobject.VERTEX)
_brep_parts_cache = {"serial": object(), "parts": {}}


def _brep_parts_all():
    """``{name: parts}`` for every visible solid / polysurface, re-read when the
    document changes -- and, then, brep sub-objects already selected are dropped: 
    ids are only valid for an UNCHANGED object."""
    serial = _undo_serial()
    cache = _brep_parts_cache
    if cache["serial"] != serial:
        cache["serial"], cache["parts"] = serial, {}
        stale = {k for k in _selected_subobjects if k[1] in _BREP_KINDS}
        _selected_subobjects.difference_update(stale)
    model = getattr(app_state, "model", None)
    file3dm = getattr(model, "file", None)
    if file3dm is None:
        return {}
    out = {}
    for fobj in file3dm.Objects:
        name = fobj.Attributes.Name or ""
        ob = bpy.data.objects.get(name)
        if not name or ob is None:
            continue
        try:
            if not ob.visible_get():
                continue
        except Exception:  # noqa: BLE001
            continue
        if name not in cache["parts"]:
            try:
                cache["parts"][name] = bridge.subobject_parts(fobj.Geometry)
            except Exception:  # noqa: BLE001
                cache["parts"][name] = None
        if cache["parts"][name]:
            out[name] = cache["parts"][name]
    return out


def _view_depth(rv3d, point):
    """Distance in front of the camera (grows away from the viewer)."""
    from mathutils import Vector
    return -(rv3d.view_matrix @ Vector(point)).z


def _project_with_depth(region, rv3d, point):
    q = _project_2d(region, rv3d, point)
    return None if q is None else (q[0], q[1], _view_depth(rv3d, point))


# ---------------------------------------------------------------------------
# Every selection step follows the mouse. Object, edge, and face steps highlight what the
# click WOULD take is highlighted under the cursor. application-defined: the colour and the rule that
# an "edge"/"face" prompt picks parts of the object already chosen.
# ---------------------------------------------------------------------------

#: (pane key, ("object", name) | ("part", (name, kind, id))) or None
_hover_pick = None
HOVER_PICK_COLOR = (1.0, 0.55, 0.15, 1.0)


def hover_pick():
    return _hover_pick[1] if _hover_pick else None


_hover_curve_cache = {
    "key": None, "rows": None, "names": (), "offsets": None, "points": None,
    "view": None, "pixels": None, "visible": None,
}


def _hover_curve_world_data():
    """Packed visible curve-overlay points, cached by document and visibility."""
    import numpy as np
    import rhino3dm as r3

    model = getattr(app_state, "model", None)
    file3dm = getattr(model, "file", None)
    if file3dm is None:
        return None
    rows = _doc_rows()
    curves = []
    signature = []
    for name, _mode, _layer_index, geom, attrs in rows:
        if not isinstance(geom, r3.Curve):
            continue
        ob = bpy.data.objects.get(name)
        if ob is None or not bridge.display_visible(ob):
            continue
        curves.append((name, geom))
        signature.append((str(attrs.Id), name))
    cache = _hover_curve_cache
    key = (_doc_key(), tuple(signature))
    if cache["rows"] is not rows or cache["key"] != key:
        names = []
        offsets = [0]
        world_points = []
        for name, geom in curves:
            points = _curve_points(geom)
            if len(points) < 2:
                continue
            names.append(name)
            world_points.extend(points)
            offsets.append(len(world_points))
        packed = np.fromiter(
            (coordinate for point in world_points for coordinate in point),
            dtype=np.float64,
            count=len(world_points) * 3,
        ).reshape(-1, 3)
        cache.update(
            key=key,
            rows=rows,
            names=tuple(names),
            offsets=np.ascontiguousarray(offsets, dtype=np.uint64),
            points=packed,
            view=None,
            pixels=None,
            visible=None,
        )
    return cache


def _hover_curve_native_data(region, rv3d):
    """``(names, offsets, pixels, visibility)`` or ``None`` for scalar fallback."""
    cache = _hover_curve_world_data()
    if cache is None:
        return None
    matrix = tuple(tuple(float(value) for value in row)
                   for row in rv3d.perspective_matrix)
    view = (matrix, float(region.width), float(region.height))
    if cache["view"] != view:
        projected = native_selection.project_display_points(
            cache["points"], matrix, region.width, region.height)
        if projected is None:
            return None
        cache["view"] = view
        cache["pixels"], cache["visible"] = projected
    return cache["names"], cache["offsets"], cache["pixels"], cache["visible"]


def _hover_curve_object(region, rv3d, rx, ry):
    """Curve-only half of `_hover_object`, with an exact optional native projector."""
    try:
        if native_selection.available():
            data = _hover_curve_native_data(region, rv3d)
            if data is not None:
                names, offsets, pixels, visible = data
                native_pick = native_selection.nearest_screen_polyline(
                    pixels, visible, offsets, (rx, ry), SUBOBJECT_PICK_PX)
                if native_pick is not None:
                    curve_index, distance = native_pick
                    if (curve_index == native_selection.PICK_NO_HIT
                            and distance == float("inf")):
                        return None
                    if (type(curve_index) is int and 0 <= curve_index < len(names)
                            and type(distance) in (int, float)
                            and 0.0 <= distance < SUBOBJECT_PICK_PX):
                        return names[curve_index]
                best, best_d = None, SUBOBJECT_PICK_PX
                for curve_index, name in enumerate(names):
                    start = int(offsets[curve_index])
                    end = int(offsets[curve_index + 1])
                    for index in range(start, end - 1):
                        if not visible[index] or not visible[index + 1]:
                            continue
                        a = (float(pixels[index, 0]), float(pixels[index, 1]))
                        b = (float(pixels[index + 1, 0]), float(pixels[index + 1, 1]))
                        distance = gumball._point_segment_distance_2d((rx, ry), a, b)
                        if distance < best_d:
                            best, best_d = name, distance
                return best
    except Exception:  # noqa: BLE001 -- optional projection never breaks hover
        pass

    # The original scalar route remains the complete fallback.
    import rhino3dm as r3
    best, best_d = None, SUBOBJECT_PICK_PX
    for fobj in app_state.model.file.Objects:
        geom = fobj.Geometry
        if not isinstance(geom, r3.Curve):
            continue
        name = fobj.Attributes.Name or ""
        ob = bpy.data.objects.get(name)
        if ob is None or not bridge.display_visible(ob):
            continue
        points = [_project_2d(region, rv3d, world) for world in _curve_points(geom)]
        for a, b in zip(points, points[1:]):
            if a is None or b is None:
                continue
            distance = gumball._point_segment_distance_2d((rx, ry), a, b)
            if distance < best_d:
                best, best_d = name, distance
    return best


def _hover_object(area, space, region, rx, ry):
    """The object under the cursor, cheaply: a ray hit for surfaces/solids, else the
    nearest curve within SUBOBJECT_PICK_PX on screen."""
    rv3d = getattr(space, "region_3d", None)
    if rv3d is None:
        return None
    try:
        import bpy_extras.view3d_utils as v3u
        o = v3u.region_2d_to_origin_3d(region, rv3d, (rx, ry))
        d = v3u.region_2d_to_vector_3d(region, rv3d, (rx, ry))
        ok, _l, _n, _i, ob, _m = bpy.context.scene.ray_cast(
            bpy.context.evaluated_depsgraph_get(), o, d)
        if ok and ob is not None and geom_by_name_in_document(ob.name) is not None:
            return ob.name
    except Exception:  # noqa: BLE001
        pass
    return _hover_curve_object(region, rv3d, rx, ry)


def _hover_target(cmd, area, space, region, rx, ry):
    steps = getattr(cmd, "steps", None) or []
    idx = getattr(cmd, "step_index", 0)
    if idx >= len(steps) or getattr(cmd, "pending_option", None) is not None:
        return None
    prompt, kind = steps[idx][0].lower(), steps[idx][1]
    args = getattr(cmd, "args", None) or []
    if kind == commands.POINT and ("edge" in prompt or "face" in prompt):
        owner = args[0] if args and isinstance(args[0], str) else None
        hit = pick_brep_part(area, space, region, rx, ry)
        want = subobject.EDGE if "edge" in prompt else subobject.FACE
        if hit is not None and hit[1] == want and (owner is None or hit[0] == owner):
            return ("part", hit)
        return None
    if kind in commands.SELECTION_KINDS or kind == commands.NAME:
        name = _hover_object(area, space, region, rx, ry)
        return ("object", name) if name else None
    return None


def _update_hover_pick(mouse_x, mouse_y):
    """Recompute the hover target. True when it changed (the caller redraws)."""
    global _hover_pick
    cmd = app_state.active_command
    new = None
    if cmd is not None and _point_in_view3d(mouse_x, mouse_y):
        area, space, region = _view3d_under_mouse(mouse_x, mouse_y)
        if region is not None:
            try:
                t = _hover_target(cmd, area, space, region,
                                  mouse_x - region.x, mouse_y - region.y)
            except Exception:  # noqa: BLE001
                t = None
            if t is not None:
                new = (_pane_key(area), t)
    changed = (new[1] if new else None) != hover_pick()
    _hover_pick = new
    return changed


def _hover_world_lines(target):
    """World polylines (and triangles) that draw the hover target."""
    lines, tris = [], []
    if target is None:
        return lines, tris
    import rhino3dm as r3
    if target[0] == "object":
        g = geom_by_name_in_document(target[1])
        if isinstance(g, r3.Curve):
            lines.append(_curve_points(g))
        else:
            parts = _brep_parts_all().get(target[1]) or {}
            lines.extend(pl for _i, pl in parts.get("edges", ()))
    else:
        name, kind, sid = target[1]
        parts = _brep_parts_all().get(name) or {}
        if kind == subobject.EDGE:
            lines.extend(pl for i, pl in parts.get("edges", ()) if i == sid)
        else:
            tris.extend(t for i, ts in parts.get("faces", ()) if i == sid for t in ts)
    return lines, tris


def draw_hover_pick(region, rv3d, shader, batch_for_shader):
    lines, tris = _hover_world_lines(hover_pick())
    # the extrude ghost follows the cursor through the same drawing
    if _gumball_drag is not None and _gumball_drag.get("ext_ghost"):
        lines = list(lines) + list(_gumball_drag["ext_ghost"])
    segs = []
    for pl in lines:
        pos = [_project_2d(region, rv3d, w) for w in pl]
        for a, b in zip(pos, pos[1:]):
            if a is not None and b is not None:
                segs.extend(((a[0], a[1]), (b[0], b[1])))
    flat = []
    for t in tris:
        c = [_project_2d(region, rv3d, p) for p in t]
        if None not in c:
            flat.extend((q[0], q[1]) for q in c)
    if flat:
        shader.uniform_float("color", HOVER_PICK_COLOR[:3] + (0.35,))
        batch_for_shader(shader, "TRIS", {"pos": flat}).draw(shader)
    if segs:
        shader.uniform_float("color", HOVER_PICK_COLOR)
        batch_for_shader(shader, "LINES", {"pos": segs}).draw(shader)


def pick_brep_part(area, space, region, rx, ry, tolerance=SUBOBJECT_PICK_PX):
    """The face / edge / vertex of a solid under region pixel (rx, ry), or None.

    All visible solids are tested together, so an edge of one solid hidden behind a
    face of another is not pickable in Shaded/Rendered (`subobject.pick_part`).
    """
    rv3d = getattr(space, "region_3d", None) if space is not None else None
    if rv3d is None or region is None:
        return None
    faces, edges, verts = [], [], []
    for name, parts in _brep_parts_all().items():
        for fid, tris in parts.get("faces", ()):
            pt = []
            for tri in tris:
                c = [_project_with_depth(region, rv3d, p) for p in tri]
                if None not in c:
                    pt.append(tuple(c))
            faces.append(((name, fid), pt))
        for eid, pl in parts.get("edges", ()):
            edges.append(((name, eid), [_project_with_depth(region, rv3d, p) for p in pl]))
        for vid, p in parts.get("vertices", ()):
            verts.append(((name, vid), _project_with_depth(region, rv3d, p)))
    occlude = getattr(getattr(space, "shading", None), "type", "SOLID") != "WIREFRAME"
    got = subobject.pick_part(faces, edges, verts, (rx, ry), tolerance, occlude=occlude)
    if got is None:
        return None
    kind, (name, sid) = got
    return (name, kind, sid)


def pick_subobject(area, space, region, rx, ry, tolerance=SUBOBJECT_PICK_PX):
    """The sub-object nearest region pixel (rx, ry), or None.

    Projected then measured in 2-D, because that is where the user aimed -- the
    same reasoning as `pick_control_point`. Curve SEGMENTS first (a curve lying on a
    face must stay pickable), then the parts of solids.
    """
    rv3d = getattr(space, "region_3d", None) if space is not None else None
    if rv3d is None or region is None:
        return None
    candidates = []
    for key, points, _grips in _subobject_candidates():
        candidates.append((key, [_project_2d(region, rv3d, w) for w in points]))
    hit = subobject.nearest(candidates, (rx, ry), tolerance)
    if hit is not None:
        return hit
    return pick_brep_part(area, space, region, rx, ry, tolerance)


def toggle_subobject(hit):
    """Ctrl+Shift+click on `hit`. Returns the new selection.

    A hit of None leaves the selection alone -- see `subobject.toggle` for why
    that is the honest reading of the help rather than a clear.
    """
    wanted = subobject.toggle(sorted(_selected_subobjects), hit)
    _selected_subobjects.clear()
    _selected_subobjects.update(wanted)
    _tag_redraw_viewports()
    return sorted(_selected_subobjects)


def subobject_click(area, space, region, rx, ry):
    """Handle one Ctrl+Shift+click. Returns (selection, message|None).

    The message is what the command bar should say. It is not always empty: a
    sub-object we recognise but cannot pick has to SAY so (house rule 4), and so
    does a successful pick, because a highlighted segment is otherwise the only
    feedback that anything happened.
    """
    hit = pick_subobject(area, space, region, rx, ry)
    if hit is None:
        name = (pick_curve_object(area, space, region, rx, ry)
                or _pick_object_at(area, space, region, rx, ry))
        if name is None:
            return selected_subobjects(), None
        # Something IS under the cursor, but it has no sub-objects to pick (a single
        # curve has none until it is joined; a point has none at all).
        return selected_subobjects(), f"{name} has no selectable sub-objects"
    before = set(_selected_subobjects)
    selection = toggle_subobject(hit)
    verb = "deselected" if hit in before else "selected"
    msg = f"{verb} {hit[1].lower()} {hit[2]} of {hit[0]}"
    if hit[1] in _BREP_KINDS and verb == "selected":
        # rule 4: the Gumball / Delete on a solid's part would need the solid re-solved
        # moving one is built (the Gumball's move handles); deleting is not
        msg += "  (drag the Gumball to move it; deleting a solid's part is not implemented yet)"
    return selection, msg


def _selected_subobject_grips():
    """{object_name: sorted control-point indices} the selected sub-objects use.

    Empty for a selection whose segments expose no grips (a PolyCurve's do not --
    it has no control points at all), which is what stops a Gumball drag from
    pretending to move something.
    """
    out = {}
    for key, _points, grips in _subobject_candidates(_selected_object_names_of_subobjects()):
        if key in _selected_subobjects and grips:
            out.setdefault(key[0], set()).update(grips)
    return {k: sorted(v) for k, v in out.items()}


def _selected_subobject_points():
    """World points of the selected sub-objects, for the Gumball's frame."""
    pts = []
    for key, points, _grips in _subobject_candidates(_selected_object_names_of_subobjects()):
        if key in _selected_subobjects:
            pts.extend(points)
    # a solid's selected face/edge/vertex is a Gumball target too, so the widget
    # sits on the PART (its box centre) rather than needing the whole object selected
    if any(k[1] in _BREP_KINDS for k in _selected_subobjects):
        for name, parts in _brep_parts_all().items():
            for fid, ftris in parts.get("faces", ()):
                if (name, subobject.FACE, fid) in _selected_subobjects:
                    pts.extend(p for tri in ftris for p in tri)
            for eid, pl in parts.get("edges", ()):
                if (name, subobject.EDGE, eid) in _selected_subobjects:
                    pts.extend(pl)
            for vid, p in parts.get("vertices", ()):
                if (name, subobject.VERTEX, vid) in _selected_subobjects:
                    pts.append(p)
    return pts


def gumball_targets_subobjects():
    """True when the Gumball is acting on a sub-object selection."""
    return bool(_selected_subobjects) and bool(_selected_subobject_points())


def _apply_xform_to_subobjects(xform):
    """Apply `xform` to the control points the selected sub-objects are made of.

    A segment has no independent existence -- it is a span between two control
    points -- so moving it means moving those. Two consequences, both deliberate:

    * a shared vertex moves once, because the grips are collected into a SET, so
      dragging two adjacent segments keeps the curve connected;
    * a segment with no grips (a PolyCurve's) moves NOTHING and says so, rather
      than moving the whole object instead.
    """
    grips = _selected_subobject_grips()
    if not grips:
        return False
    model = getattr(app_state, "model", None)
    file3dm = getattr(model, "file", None)
    if file3dm is None:
        return False
    moved = 0
    for fobj in file3dm.Objects:
        name = fobj.Attributes.Name or ""
        if name not in grips:
            continue
        geom = fobj.Geometry
        # Read every target FIRST, then write. Reading as we go would feed an
        # already-moved point into a later read when two grips coincide -- which
        # they do at a closed curve's seam.
        targets = []
        for index in grips[name]:
            loc = bridge.control_point_at(geom, index)
            if loc is None:
                continue
            new = bridge.transform_point(xform, loc)
            if new is not None:
                targets.append((index, new))
        for index, new in targets:
            if bridge.set_control_point(geom, index, new):
                moved += 1
                _bump_cp_version()
    if not moved:
        return False
    rebuild_display_keeping_selection()
    invalidate_snap_cache()
    _tag_redraw_viewports()
    return True


def draw_subobject_highlight(region, rv3d, shader, batch_for_shader):
    """Draw the selected segments over the wire, in the reference CAD application's selection yellow.

    Drawn as 2-D screen-space lines by the point-marker handler, which already
    runs POST_PIXEL and already has a projection to hand. A 3-D overlay would need
    its own depth handling to sit on top of the curve it highlights.
    """
    segs = []
    for key, points, _grips in _subobject_candidates(_selected_object_names_of_subobjects()):
        if key not in _selected_subobjects:
            continue
        pos = [_project_2d(region, rv3d, w) for w in points]
        for a, b in zip(pos, pos[1:]):
            if a is not None and b is not None:
                segs.extend(((a[0], a[1]), (b[0], b[1])))
    # selected FACES (a translucent fill), EDGES and VERTICES of solids
    tris, dots = [], []
    if any(k[1] in _BREP_KINDS for k in _selected_subobjects):
        # during a Gumball drag the part's highlight follows the cursor; the
        # solid itself is rebuilt on release
        off = _brep_part_offset() or (0.0, 0.0, 0.0)

        def _sh(p):
            return (p[0] + off[0], p[1] + off[1], p[2] + off[2])

        for name, parts in _brep_parts_all().items():
            for fid, ftris in parts.get("faces", ()):
                if (name, subobject.FACE, fid) in _selected_subobjects:
                    for tri in ftris:
                        c = [_project_2d(region, rv3d, _sh(p)) for p in tri]
                        if None not in c:
                            tris.extend((q[0], q[1]) for q in c)
            for eid, pl in parts.get("edges", ()):
                if (name, subobject.EDGE, eid) in _selected_subobjects:
                    pos = [_project_2d(region, rv3d, _sh(w)) for w in pl]
                    for a, b in zip(pos, pos[1:]):
                        if a is not None and b is not None:
                            segs.extend(((a[0], a[1]), (b[0], b[1])))
            for vid, p in parts.get("vertices", ()):
                if (name, subobject.VERTEX, vid) in _selected_subobjects:
                    q = _project_2d(region, rv3d, _sh(p))
                    if q is not None:
                        dots.append(q)
    if tris:
        shader.uniform_float("color", SUBOBJECT_COLOR[:3] + (0.35,))
        batch_for_shader(shader, "TRIS", {"pos": tris}).draw(shader)
    if dots:
        sq = []
        for x, y in dots:
            sq.extend(((x - 4, y - 4), (x + 4, y - 4), (x + 4, y + 4),
                       (x - 4, y - 4), (x + 4, y + 4), (x - 4, y + 4)))
        shader.uniform_float("color", SUBOBJECT_COLOR)
        batch_for_shader(shader, "TRIS", {"pos": sq}).draw(shader)
    if not segs:
        return
    shader.uniform_float("color", SUBOBJECT_COLOR)
    gpu_mod = __import__("gpu")
    gpu_mod.state.line_width_set(3.0)
    batch_for_shader(shader, "LINES", {"pos": segs}).draw(shader)
    gpu_mod.state.line_width_set(1.0)


def _attach_point_handler():
    """Register the point-marker handler on the VIEW_3D space TYPE, POST_PIXEL."""
    if _point_handlers:
        return False
    handler_id = bpy.types.SpaceView3D.draw_handler_add(
        draw_point_markers, (), "WINDOW", "POST_PIXEL")
    _point_handlers.append((bpy.types.SpaceView3D, handler_id))
    # Register the picture ghost so 1:1 placement updates immediately.
    ghost_id = bpy.types.SpaceView3D.draw_handler_add(
        draw_picture_ghost, (), "WINDOW", "POST_VIEW")
    _point_handlers.append((bpy.types.SpaceView3D, ghost_id))
    return True


def picture_ghost():
    """``(image path, (origin, u, v))`` of the picture the running `Picture` command would
    place at the cursor, or None."""
    cmd = app_state.active_command
    fn = getattr(cmd, "live_frame", None)
    if not callable(fn) or _preview_cursor is None:
        return None
    try:
        return fn(_preview_cursor, app_state.ctx)
    except Exception:  # noqa: BLE001
        return None


def _ghost_image(path):
    import os
    path = os.path.abspath(path)
    for img in bpy.data.images:
        try:
            if os.path.abspath(bpy.path.abspath(img.filepath)) == path:
                return img
        except Exception:  # noqa: BLE001
            continue
    try:
        return bpy.data.images.load(path, check_existing=True)
    except Exception:  # noqa: BLE001
        return None


_picture_shader_cache = [None]


def _picture_shader():
    """3-D image shader for Blender image textures, which sample in scene-linear space.

    Blender's builtin ``IMAGE`` shader is the non-scene-linear variant: its source calls
    ``blender_rec709_srgb_to_output_space`` on the texture sample. That incorrectly treats
    an already-linear image sample as sRGB and darkens midtones a second time. The viewport
    target expects linear writes, so passing the sampled value through unchanged preserves
    the PNG/JPEG's original display colour after the framebuffer conversion.
    """
    if _picture_shader_cache[0] is not None:
        return _picture_shader_cache[0]
    try:
        import gpu

        iface = gpu.types.GPUStageInterfaceInfo("narwhal_picture_iface")
        iface.smooth("VEC2", "uv_interp")
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant("MAT4", "ModelViewProjectionMatrix")
        info.sampler(0, "FLOAT_2D", "image")
        info.vertex_in(0, "VEC3", "pos")
        info.vertex_in(1, "VEC2", "texCoord")
        info.vertex_out(iface)
        info.fragment_out(0, "VEC4", "fragColor")
        info.vertex_source("""
void main()
{
  uv_interp = texCoord;
  gl_Position = ModelViewProjectionMatrix * vec4(pos, 1.0);
}
""")
        info.fragment_source("""
void main()
{
  fragColor = texture(image, uv_interp);
}
""")
        _picture_shader_cache[0] = gpu.shader.create_from_info(info)
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] picture colour shader failed: {e!r}")
        return None
    return _picture_shader_cache[0]


def draw_picture_ghost():
    """The image itself, at the size and place the next click gives it, plus its outline.
    Drawn on top (no depth test): it is a preview, like every other command preview."""
    g = picture_ghost()
    if g is None:
        return
    path, (o, u, v) = g
    corners = [tuple(o[i] + a * u[i] + b * v[i] for i in range(3))
               for a, b in ((0, 0), (1, 0), (1, 1), (0, 1))]
    try:
        import gpu
        from gpu_extras.batch import batch_for_shader
    except Exception:  # noqa: BLE001
        return
    img = _ghost_image(path)
    try:
        gpu.state.blend_set("ALPHA")
        if img is not None:
            shader = _picture_shader()
            if shader is None:
                return
            shader.bind()
            shader.uniform_sampler("image", gpu.texture.from_image(img))
            pos = [corners[0], corners[1], corners[2], corners[0], corners[2], corners[3]]
            uvs = [(0, 0), (1, 0), (1, 1), (0, 0), (1, 1), (0, 1)]
            batch_for_shader(shader, "TRIS", {"pos": pos, "texCoord": uvs}).draw(shader)
        line = _uniform_color_shader()
        if line is not None:
            line.bind()
            line.uniform_float("color", (0.1, 0.1, 0.1, 1.0))
            seg = []
            for i in range(4):
                seg += [corners[i], corners[(i + 1) % 4]]
            batch_for_shader(line, "LINES", {"pos": seg}).draw(line)
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] picture ghost draw failed: {e!r}")
    finally:
        gpu.state.blend_set("NONE")


# ---------------------------------------------------------------------------
# Pictures in Shaded and Rendered modes must retain their source brightness. A Blender
# Principled material lights its base-color texture, so
# Shaded was necessarily darker than the source image. This pass paints Picture triangles
# unlit in both modes, just in front of the surface (depth-tested, so foreground geometry
# still hides it). also measured 94 Pictures: each immutable GPU batch is cached
# until that Picture's mesh, UVs, image or transform changes, rather than rebuilt per frame.
# Keeping one batch per object is intentional: Verified on 0070.3dm, combining its 51k
# image vertices into four source-image batches made a four-pane draw 10x slower (213 ms).
# ---------------------------------------------------------------------------

_picture_batch_cache = {}


def _picture_image(ob):
    for slot in getattr(ob, "material_slots", ()):
        mat = slot.material
        if mat is None or not mat.name.startswith("narwhal_picture:") or not mat.node_tree:
            continue
        for node in mat.node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image is not None:
                return node.image
    return None


def _picture_mesh_key(ob, image):
    """A cheap complete display signature for one Picture.

    's 94 imported Pictures contain 51k display vertices, so rereading positions and
    UVs in every pane made the cache itself cost ~200 ms/frame. Geometry/UV edits and app
    rebuilds already increment ``_curve_dg.changed`` through the shared depsgraph/display
    listener. Combine that serial with identity/counts, the world matrix and image identity:
    no full-mesh scan, but Gumball, undo, UV edits and material replacement all invalidate.
    """
    me = ob.data
    uv = me.uv_layers.active
    return (ob.name, me.as_pointer(), len(me.vertices), len(me.polygons),
            len(uv.data) if uv is not None else 0,
            _curve_dg["changed"].get(ob.name, 0),
            tuple(c for row in ob.matrix_world for c in row),
            image.as_pointer(), image.filepath, tuple(image.size))


def _picture_tris(ob):
    """World-space triangle corners and UVs for Picture object ``ob``."""
    me = ob.data
    uv = me.uv_layers.active
    if uv is None:
        return [], []
    me.calc_loop_triangles()
    mw = ob.matrix_world
    pos, uvs = [], []
    for tri in me.loop_triangles:
        for li, vi in zip(tri.loops, tri.vertices):
            pos.append(tuple(mw @ me.vertices[vi].co))
            uvs.append(tuple(uv.data[li].uv))
    return pos, uvs


def rendered_picture_objects(space):
    """Pictures painted unlit in Shaded/Rendered; none in Wireframe."""
    if display_mode_of_space(space) not in ("Shaded", "Rendered"):
        return []
    return [ob for ob in bpy.data.objects
            if ob.get("narwhal_picture") and ob.type == "MESH" and ob.visible_get()]


def display_mode_of_space(space):
    try:
        return views.display_mode_for_shading(
            space.shading.type,
            lit=(space.shading.type == "SOLID" and space.shading.color_type == "SINGLE"))
    except Exception:  # noqa: BLE001
        return None


def _build_picture_draw(shader, image, pos, uvs):
    """Build one immutable GPU texture/batch pair; split out for the background gate."""
    from gpu_extras.batch import batch_for_shader
    import gpu

    texture = gpu.texture.from_image(image)
    batch = batch_for_shader(shader, "TRIS", {"pos": pos, "texCoord": uvs})
    return texture, batch


def _picture_draws(obs, shader):
    """Cached ``(texture, batch)`` per visible Picture; stale objects are discarded."""
    draws, live = [], set()
    for ob in obs:
        image = _picture_image(ob)
        if image is None:
            continue
        live.add(ob.name)
        key = _picture_mesh_key(ob, image)
        hit = _picture_batch_cache.get(ob.name)
        if hit is None or hit[0] != key:
            pos, uvs = _picture_tris(ob)
            if not pos:
                continue
            try:
                hit = (key, _build_picture_draw(shader, image, pos, uvs))
            except Exception:  # noqa: BLE001
                continue
            _picture_batch_cache[ob.name] = hit
        draws.append(hit[1])
    for name in list(_picture_batch_cache):
        if name not in live:
            del _picture_batch_cache[name]
    return tuple(draws)


def draw_rendered_pictures():
    space = getattr(bpy.context, "space_data", None)
    rv3d = getattr(space, "region_3d", None) if space is not None else None
    if rv3d is None:
        return
    obs = rendered_picture_objects(space)
    if not obs:
        return
    try:
        import gpu
        shader = _picture_shader()
    except Exception:  # noqa: BLE001
        return
    if shader is None:
        return
    inv = rv3d.view_matrix.inverted()
    toward = ((inv.translation - rv3d.view_location).normalized() if rv3d.is_perspective
              else inv.col[2].xyz.normalized())
    nudge = toward * (2e-4 * max(rv3d.view_distance, 1e-3))
    gpu.matrix.push()
    gpu.matrix.translate(tuple(nudge))
    gpu.state.depth_test_set("LESS_EQUAL")
    gpu.state.depth_mask_set(False)
    try:
        for texture, batch in _picture_draws(obs, shader):
            shader.bind()
            shader.uniform_sampler("image", texture)
            batch.draw(shader)
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] rendered picture draw failed: {e!r}")
    finally:
        gpu.matrix.pop()
        gpu.state.depth_test_set("NONE")
        gpu.state.depth_mask_set(True)


def _detach_point_handler():
    while _point_handlers:
        owner, handler_id = _point_handlers.pop()
        try:
            owner.draw_handler_remove(handler_id, "WINDOW")
        except Exception:  # noqa: BLE001
            pass


def _preview_cplane():
    """The CPlane the preview cursor is in -- the pane you are working in.

    The preview is ONE world-space wireframe, so it must be built in the CPlane
    the cursor was actually picked in, not in each pane's own CPlane (that would
    invent a different circle in every pane). It is then drawn from every pane's
    camera, where Blender's view matrices show it full-on or edge-on, exactly as
    the reference CAD application projects the in-progress geometry into all viewports at once.

    Returns the working pane's CPlane, falling back to the active pane's if the
    recorded pane has disappeared (a layout change).
    """
    import bpy

    if _preview_pane is not None:
        for area in bpy.context.screen.areas:
            if area.type == "VIEW_3D" and _pane_key(area) == _preview_pane:
                return get_cplane(area)
    area = active_pane_area()
    return get_cplane(area) if area is not None else None


# ---------------------------------------------------------------------------
# the live TRANSFORM GHOST -- the objects follow the cursor
# ---------------------------------------------------------------------------
#
# Move preview draws the selected geometry at its prospective destination, not only the
# from-to band.
# selection dragged along; CAD SDK's GetTransform is built the same way --
# `CalculateTransform(viewport, point)` returns the transform for a cursor point, and
# the objects are redrawn through it on every mouse move.
#
# So a transform command exposes `live_xform(cursor) -> r3.Transform | None` (the
# contract the geometry layer built in ; it mirrors CalculateTransform), and
# this module draws the SELECTED objects' wireframes through it in every pane.
#
# The wireframes are built ONCE per command run and handed to the GPU with the
# transform as a model matrix, so a mouse move costs one matrix, not a re-tessellation.
# Solids are drawn by their REAL edges (`bridge.extract_wireframe` reads the OCCT
# master), never by the display proxy's triangles.
#
# application-defined, flagged: the ghost is drawn in the selection yellow. The help documents the
# Feedback colour only for "the line you see when you drag objects" (the band, which
# keeps PREVIEW_COLOR), not the colour of the dragged objects themselves.

_ghost_cache = {"key": None, "polys": (), "batch": None}
GHOST_COLOR = (SELECT_COLOR[0], SELECT_COLOR[1], SELECT_COLOR[2], 1.0)


def _tracks_cursor(cmd):
    """True when the cursor must be followed: a shape preview, or a transform ghost."""
    if cmd is None:
        return False
    if preview.has_preview(getattr(cmd, "name", None)):
        return True
    return callable(getattr(cmd, "live_xform", None))


def _curve_points(curve):
    """World points along a curve, exact at its corners (a polyline keeps its kinks).

    The code is `bridge.overlay_curve_points`, so an Open's worker processes (no bpy) can
    compute the identical points for the layer-colour overlay."""
    return bridge.overlay_curve_points(curve)


def ghost_polylines(geom):
    """World-space polylines that draw `geom` as a wireframe, for the ghost."""
    import rhino3dm as r3

    if geom is None:
        return []
    curves = []
    if isinstance(geom, r3.Curve):
        curves = [geom]
    else:
        g = geom
        if isinstance(g, r3.Extrusion):
            try:
                g = g.ToBrep(True)
            except Exception:  # noqa: BLE001
                g = None
        if g is not None:
            try:
                curves, _src = bridge.extract_wireframe(g)
            except Exception:  # noqa: BLE001
                curves = []
    out = [pts for pts in (_curve_points(c) for c in curves) if len(pts) >= 2]
    if out:
        return out
    # last resort: the bounding box, so SOMETHING follows the cursor (a mesh, a point)
    try:
        bb = geom.GetBoundingBox()      # rhino3dm takes no argument (Verified)
        lo, hi = bb.Min, bb.Max
    except Exception:  # noqa: BLE001
        return []
    c = [(x, y, z) for z in (lo.Z, hi.Z) for y in (lo.Y, hi.Y) for x in (lo.X, hi.X)]
    edges = ((0, 1), (1, 3), (3, 2), (2, 0), (4, 5), (5, 7), (7, 6), (6, 4),
             (0, 4), (1, 5), (2, 6), (3, 7))
    return [[c[i], c[j]] for i, j in edges]


def live_transform(cmd, cursor):
    """The transform the command would apply for `cursor`, or None."""
    if cmd is None or cursor is None:
        return None
    fn = getattr(cmd, "live_xform", None)
    if not callable(fn):
        return None
    try:
        return fn(tuple(cursor))
    except Exception:  # noqa: BLE001
        return None


def _ghost_sources(cmd):
    """The selected objects' wireframes, built once per command run."""
    names = tuple(sorted(selected_display_names()))
    key = (_cmd_token(cmd), names)
    if _ghost_cache["key"] != key:
        polys = []
        for name in names:
            polys.extend(ghost_polylines(geom_by_name_in_document(name)))
        _ghost_cache.update(key=key, polys=tuple(polys), batch=None)
    return _ghost_cache["polys"]


def transform_ghost(cmd=None, cursor=None):
    """``(xform, polylines)`` -- the ghost as WORLD polylines, transformed (for tests)."""
    cmd = app_state.active_command if cmd is None else cmd
    cursor = _preview_cursor if cursor is None else cursor
    xf = live_transform(cmd, cursor)
    if xf is None:
        return None, []
    rows = _xform_rows(xf)
    out = []
    for poly in _ghost_sources(cmd):
        moved = []
        for x, y, z in poly:
            v = [rows[r][0] * x + rows[r][1] * y + rows[r][2] * z + rows[r][3]
                 for r in range(4)]
            w = v[3] or 1.0
            moved.append((v[0] / w, v[1] / w, v[2] / w))
        out.append(moved)
    return xf, out


def _xform_rows(xf):
    return [[xf.M00, xf.M01, xf.M02, xf.M03], [xf.M10, xf.M11, xf.M12, xf.M13],
            [xf.M20, xf.M21, xf.M22, xf.M23], [xf.M30, xf.M31, xf.M32, xf.M33]]


def _draw_transform_ghost(cmd, shader, gpu, batch_for_shader):
    xf = live_transform(cmd, _preview_cursor)
    if xf is None:
        return
    polys = _ghost_sources(cmd)
    if not polys:
        return
    batch = _ghost_cache.get("batch")
    if batch is None:
        coords = []
        for poly in polys:
            for a, b in zip(poly, poly[1:]):
                coords.append(a)
                coords.append(b)
        if not coords:
            return
        batch = batch_for_shader(shader, "LINES", {"pos": coords})
        _ghost_cache["batch"] = batch
    from mathutils import Matrix

    gpu.matrix.push()
    try:
        gpu.matrix.multiply_matrix(Matrix(_xform_rows(xf)))
        shader.bind()
        shader.uniform_float("color", GHOST_COLOR)
        batch.draw(shader)
    finally:
        gpu.matrix.pop()


def draw_command_preview():
    """POST_VIEW draw handler: the running command's dynamic preview.

    the reference CAD application gives continuous feedback while a command runs -- after the first click
    of `Line` a band follows the cursor. What to draw is decided by the pure
    `preview` module; this only turns the polylines it returns into GPU batches.

    Registered in **POST_VIEW**, not POST_PIXEL, so the coordinates handed to the
    shader are WORLD coordinates and Blender's own view matrices place them -- no
    manual 3D-to-2D projection, and it is automatically correct in all four panes
    at any zoom.

    Drawn in **every** viewport. The in-progress geometry is one world-space
    object, and the reference CAD application projects it into all viewports at once -- a circle drawn on
    the Front plane reads as a full circle in Front and edge-on in Top/Right.
    So the shape is built once, in the CPlane the cursor is in (see
    :func:`_preview_cplane`), and this handler draws that same world-space
    shape from each pane's camera rather than gating itself out.
    """
    import bpy

    cmd = app_state.active_command
    if cmd is None or _preview_cursor is None:
        return
    context = bpy.context
    area = getattr(context, "area", None)
    if area is None or area.type != "VIEW_3D":
        return

    plane = _preview_cplane()
    if plane is None:
        return
    # `ExtrudeCrv`'s preview needs the CURVE, which its args name rather than
    # contain, so a resolver is passed in. It mirrors the command's own lookup (a typed
    # name, else the first selected object), because a preview that resolved the curve
    # differently from `execute` would draw a shape the user does not get.
    def _resolve_preview_geom(name):   # noqa: D401 (thin wrapper over the shared lookup)
        # Mirrors `NarwhalContext.geom_by_name` (a case-insensitive scan of the document)
        # plus the command's selection fallback. Verified: there is no
        # `model.geometry_by_name` -- the document is walked directly, which is what
        # commands.py does too.
        try:
            model = app_state.model
            if model is None:
                return None
            if not name:
                chosen = sorted(selected_display_names())
                name = chosen[0] if chosen else None
            if not name:
                return None
            for obj in model.file.Objects:
                if (obj.Attributes.Name or "").lower() == str(name).lower():
                    return obj.Geometry
            return None
        except Exception:  # noqa: BLE001
            return None

    polylines = preview.segments(getattr(cmd, "name", None), cmd.args,
                                _preview_cursor, plane,
                                resolve=_resolve_preview_geom, cmd=cmd)
    ghost = live_transform(cmd, _preview_cursor) is not None
    if not polylines and not ghost:
        return

    try:
        import gpu
        from gpu_extras.batch import batch_for_shader
    except Exception:  # noqa: BLE001
        return
    shader = _uniform_color_shader()
    if shader is None:
        return

    prev_blend = gpu.state.blend_get()
    gpu.state.blend_set("ALPHA")
    try:
        if ghost:
            # the objects themselves, then the band on top of them
            _draw_transform_ghost(cmd, shader, gpu, batch_for_shader)
        shader.bind()
        shader.uniform_float("color", PREVIEW_COLOR)
        for poly in polylines or ():
            # LINE_STRIP is not available on every builtin shader, so build the
            # segment pairs explicitly and draw LINES.
            coords = []
            for a, b in zip(poly, poly[1:]):
                coords.append(a)
                coords.append(b)
            if not coords:
                continue
            batch = batch_for_shader(shader, "LINES", {"pos": coords})
            batch.draw(shader)
    except Exception as e:  # noqa: BLE001
        # A preview is cosmetic; never let it break the viewport draw.
        print(f"[narwhal3d] preview draw failed: {e!r}")
    finally:
        gpu.state.blend_set(prev_blend)


# ---------------------------------------------------------------------------
# the PRINT DIALOG -- settings on the left, a live preview on the right
# ---------------------------------------------------------------------------
#
# Print uses a settings dialog with a live preview pane.
# A Blender props dialog cannot do the "constantly updating" half (Verified: its
# layout is built once -- see `printdialog`), so the dialog is drawn by us, in the
# ACTIVE viewport, from the pure model in `printdialog`.
#
# Interaction (application-defined -- the reference CAD application's dropdowns are native widgets): click a value (or turn
# the wheel over it) for the next choice, right-click for the previous one; Print asks
# for the file; Close or Esc closes. The preview is rebuilt from `print2d` after EVERY
# change, from the very Page the file will be written from.

_print_dialog = None


def print_dialog_state():
    """The open dialog's settings (`printdialog.DialogState`), or None."""
    return _print_dialog["state"] if _print_dialog else None


def print_dialog_open():
    return _print_dialog is not None


def _print_dialog_area():
    if _print_dialog is None:
        return None
    key = _print_dialog["pane"]
    return next((a for a in _view3d_areas() if _pane_key(a) == key), None)


def _print_dialog_refresh():
    """Rebuild the page from the current settings -- the preview IS this page."""
    d = _print_dialog
    st = d["state"]
    st.message = ""
    d["page"] = None
    view = d["views"].get(st.view)
    if view is None:
        st.message = "no viewport to print"
        return
    objs = None
    if st.selected_only:
        names = {n.lower() for n in selected_display_names()}
        objs = [o for o in app_state.model.file.Objects
                if (o.Attributes.Name or "").lower() in names]
    try:
        page = print2d.build_page(app_state.model, view, st.setup(), objects=objs)
    except ValueError as e:
        st.message = str(e)
        return
    d["page"] = page
    note = "; ".join(page.notes)
    st.message = (f"{len(page.strokes)} curve(s), {page.width_mm:.1f} x "
                  f"{page.height_mm:.1f} mm" + (f".  NOTE: {note}" if note else ""))
    _tag_redraw_viewports()


def open_print_dialog():
    """Open the dialog over the active viewport. Returns True when it opened."""
    global _print_dialog
    areas = _view3d_areas()
    if not areas:
        return False
    active = active_pane_area() or areas[0]
    views = {}
    for a in areas:
        try:
            views[pane_label(a)] = print_view_spec(a)
        except Exception:  # noqa: BLE001
            continue
    labels = tuple(views)
    st = printdialog.DialogState(views=labels,
                                 view=pane_label(active) if pane_label(active)
                                 in views else (labels[0] if labels else ""))
    _print_dialog = {"state": st, "views": views, "pane": _pane_key(active),
                     "page": None}
    _print_dialog_refresh()
    _tag_redraw_viewports()
    return True


def close_print_dialog():
    global _print_dialog
    was = _print_dialog is not None
    _print_dialog = None
    _tag_redraw_viewports()
    return was


def print_dialog_print(path=None):
    """The Print button: choose the file (unless given) and write it. Returns a message."""
    d = _print_dialog
    if d is None:
        return None
    st = d["state"]
    if d.get("page") is None:
        return st.message or "nothing to print"
    ext = st.extension()
    if path is None:
        from . import filedialog

        base = os.path.splitext(app_state.doc_title())[0] or "Untitled"
        start = os.path.join(filedialog.start_dir(app_state.doc_path), base + ext)
        filt = next(((n, f"*{e}") for n, e in printdialog.DESTINATIONS if e == ext),
                    ("PDF", "*.pdf"))
        status, path = filedialog.ask_save_path("Print", start, (filt,))
        if status == filedialog.UNAVAILABLE:
            st.message = ("No file dialog is available -- close this and use the "
                          "Print command's typed file name")
            return st.message
        if status != filedialog.OK:
            return "Print cancelled"
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.splitext(path)[1]:
        path += ext
    print2d.write(path, d["page"], os.path.splitext(os.path.basename(path))[0])
    msg = f"Printed {len(d['page'].strokes)} curve(s) to {path}"
    app_state.log(msg)
    close_print_dialog()
    redraw_cmdbar()
    return msg


def _print_dialog_region():
    area = _print_dialog_area()
    if area is None:
        return None
    return next((r for r in area.regions if r.type == "WINDOW"), None)


def _print_dialog_layout(region):
    return printdialog.layout(region.width, region.height, _ui_scale(),
                              _print_dialog["state"])


def _print_dialog_apply(result):
    """Act on what `printdialog.click` / `key` reported. Returns the result."""
    if result is None:
        return None
    if result.startswith("chose:"):
        _print_dialog_refresh()
    elif result == "button:close":
        close_print_dialog()
    elif result == "button:print":
        return "print:" + str(print_dialog_print())
    _tag_redraw_viewports()
    return result


def _print_field_measure():
    """Width in the dialog's field font (13 px x scale), trailing spaces included."""
    fs = 13.0 * _ui_scale()
    guard = _text_width("|", fs)
    return lambda t: (_text_width(t + "|", fs) - guard) if t else 0.0


def print_dialog_click(win_x, win_y, step=1, shift=False):
    """A click in the window while the dialog is open. Returns what it did (see
    `printdialog.click`), or None when the dialog is closed. Right-clicks inside the
    dialog are swallowed (step=-1): there is nothing on it a right-click does. A click
    in a size field places the caret there."""
    region = _print_dialog_region()
    if region is None:
        return None
    lay = _print_dialog_layout(region)
    x, y = win_x - region.x, win_y - region.y
    if step < 0:
        return "outside" if printdialog.hit(lay, x, y) is None else "panel"
    return _print_dialog_apply(printdialog.click(_print_dialog["state"], lay, x, y,
                                                 measure=_print_field_measure(),
                                                 shift=shift))


def print_dialog_move(win_x, win_y):
    region = _print_dialog_region()
    if region is None:
        return False
    lay = _print_dialog_layout(region)
    if printdialog.move(_print_dialog["state"], lay, win_x - region.x,
                        win_y - region.y, measure=_print_field_measure()):
        _tag_redraw_viewports()
        return True
    return False


def print_dialog_release():
    if _print_dialog is not None:
        printdialog.release(_print_dialog["state"])


def print_dialog_key(kind, char="", shift=False):
    """A key while the dialog is open. Returns the dialog's answer or None (not its)."""
    if _print_dialog is None:
        return None
    return _print_dialog_apply(printdialog.key(_print_dialog["state"], kind, char,
                                               shift=shift))


def _ui_scale():
    """THE interface scale  -- `uiscale.current`, which Blender's own
    effective scale is set to match (`apply_interface_scale`). It used to read Blender's
    Blender's DPI-driven `system.ui_scale` while the rest of our
    chrome drew at 1.0, so two parts of the app disagreed."""
    return uiscale.current()


def _fpx(size):
    """A font size / pixel length at scale 1 -> at the interface scale."""
    return float(size) * uiscale.current()


def interface_scale_for_window(w, h):
    """$NARWHAL3D_UI_SCALE if set (an explicit choice, and how the harness tests other
    scales), else `uiscale.auto_scale` for this window."""
    env = os.environ.get("NARWHAL3D_UI_SCALE")
    if env:
        try:
            return max(uiscale.MIN_SCALE, min(uiscale.MAX_SCALE, float(env)))
        except ValueError:
            pass
    return uiscale.auto_scale(w, h)


def apply_interface_scale(scale=None):
    """Apply one interface scale to every application and Blender UI dimension.

    Project pixel constants
    (`uiscale.apply`) and Blender's own widgets (`view.ui_scale` chosen so its effective
    scale -- DPI included -- equals ours). Run before the layout is carved. Returns it."""
    win = getattr(bpy.context, "window", None)
    if scale is None:
        scale = interface_scale_for_window(getattr(win, "width", 1920),
                                           getattr(win, "height", 1048))
    uiscale.apply(scale)
    try:
        prefs = bpy.context.preferences
        want = uiscale.blender_view_scale(scale, prefs.system.ui_scale,
                                          prefs.view.ui_scale)
        if abs(prefs.view.ui_scale - want) > 1e-4:
            prefs.view.ui_scale = want
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] could not match the host UI scale: {e!r}")
    print(f"[narwhal3d] interface scale {scale:g}")
    return scale


# the scale follows the window when it is RESIZED, not only at startup. application-defined: a size
# must hold for two polls (~1 s) before it counts, so dragging a window edge does not
# rescale on every intermediate size. Verified (a focused runtime check): Blender itself
# rescales the docked areas in proportion to the window (command bar 121 -> 130 px when the
# window went 1348 -> 1440 high), and `screen.area_move` cannot be driven from a timer
# (its poll needs no active region), so the AREAS are left to Blender -- only the scale is
# re-applied here.
RESIZE_POLL_S = 0.5
RESIZE_STABLE_POLLS = 2
_resize_watch = {"size": None, "stable": 0, "running": False}


def _window_scale_tick():
    st = _resize_watch
    try:
        wm = bpy.context.window_manager
        win = wm.windows[0] if wm is not None and len(wm.windows) else None
        if win is None:
            return RESIZE_POLL_S
        size = (int(win.width), int(win.height))
        if size != st["size"]:
            st["size"], st["stable"] = size, 0
            return RESIZE_POLL_S
        st["stable"] += 1
        if st["stable"] < RESIZE_STABLE_POLLS or _viewports_in_progress:
            return RESIZE_POLL_S
        want = interface_scale_for_window(*size)
        if abs(want - uiscale.current()) > 1e-6:
            print(f"[narwhal3d] window is now {size[0]}x{size[1]} -- rescaling")
            apply_interface_scale(want)
            for a in win.screen.areas:
                a.tag_redraw()
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] interface-scale watch failed: {e!r}")
    return RESIZE_POLL_S


def start_window_scale_watch():
    """Start the  resize watch (once). Returns True if it is running."""
    if not _resize_watch["running"]:
        bpy.app.timers.register(_window_scale_tick, first_interval=RESIZE_POLL_S)
        _resize_watch["running"] = True
    return _resize_watch["running"]


def draw_print_dialog():
    """POST_PIXEL: the dialog, in the viewport it was opened over."""
    if _print_dialog is None:
        return
    context = bpy.context
    area = getattr(context, "area", None)
    region = getattr(context, "region", None)
    if area is None or region is None or _pane_key(area) != _print_dialog["pane"]:
        return
    try:
        import gpu
        from gpu_extras.batch import batch_for_shader
    except Exception:  # noqa: BLE001
        return
    shader = _uniform_color_shader()
    if shader is None:
        return
    sc = _ui_scale()
    st = _print_dialog["state"]
    lay = printdialog.layout(region.width, region.height, sc, st)
    fs = 13.0 * sc
    ink = (0.05, 0.05, 0.05, 1)
    blue = (0.20, 0.45, 0.85, 1)

    def box(r, fill, border=(0.6, 0.6, 0.62, 1)):
        shader.bind()
        _draw_rect(shader, r.x - 1, r.y - 1, r.x + r.w + 1, r.y + r.h + 1, border)
        _draw_rect(shader, r.x, r.y, r.x + r.w, r.y + r.h, fill)

    gpu.state.blend_set("ALPHA")
    try:
        shader.bind()
        _draw_rect(shader, 0, 0, region.width, region.height, (0, 0, 0, 0.35))
        p = lay.panel
        _draw_rect(shader, p.x, p.y, p.x + p.w, p.y + p.h, (0.94, 0.94, 0.94, 1))
        t = lay.title
        _draw_rect(shader, t.x, t.y, t.x + t.w, t.y + t.h, (0.85, 0.85, 0.87, 1))
        _draw_text(t.x + 10 * sc, t.y + 9 * sc, "Print Setup", fs + 1, ink)
        for rid, lr, vr in lay.rows:
            kind = printdialog.ROW_KINDS[rid]
            _draw_text(lr.x, lr.y + 8 * sc, printdialog.ROW_LABELS[rid], fs,
                       (0.1, 0.1, 0.1, 1))
            if kind == "check":
                cb = printdialog.Rect(vr.x, vr.y + (vr.h - 16 * sc) / 2, 16 * sc,
                                      16 * sc)
                box(cb, blue if st.selected_only else (1, 1, 1, 1))
                if st.selected_only:
                    _draw_text(cb.x + 3 * sc, cb.y + 3 * sc, "\u2713", fs,
                               (1, 1, 1, 1))
                continue
            focused = kind == "field" and st.focus == rid
            opened = st.open_dropdown == rid
            box(vr, (1, 1, 1, 1), blue if (focused or opened) else (0.6, 0.6, 0.62, 1))
            text = st.shown(rid)
            tx0 = vr.x + printdialog.FIELD_TEXT_PAD * sc
            if focused:
                # the selection, then the caret where it really is
                fld = printdialog.field_of(st)
                meas = _print_field_measure()
                sel = fld.selection()
                if sel is not None:
                    shader.bind()
                    _draw_rect(shader, tx0 + meas(text[:sel[0]]), vr.y + 4 * sc,
                               tx0 + meas(text[:sel[1]]), vr.y + vr.h - 4 * sc,
                               (0.70, 0.82, 1.0, 1))
            _draw_text(tx0, vr.y + 8 * sc, text, fs, ink)
            if focused:
                cx = tx0 + meas(text[:fld.caret])
                shader.bind()
                _draw_rect(shader, cx, vr.y + 5 * sc, cx + 1.5 * sc,
                           vr.y + vr.h - 5 * sc, ink)
            if kind == "choice":
                _draw_text(vr.x + vr.w - 16 * sc, vr.y + 8 * sc, "\u25be", fs,
                           (0.3, 0.3, 0.3, 1))
        pv = lay.preview
        shader.bind()
        _draw_rect(shader, pv.x, pv.y, pv.x + pv.w, pv.y + pv.h, (0.55, 0.56, 0.58, 1))
        page = _print_dialog.get("page")
        if page is not None:
            sheet = printdialog.page_rect(pv, page.width_mm, page.height_mm)
            _draw_rect(shader, sheet.x + 4, sheet.y - 4, sheet.x + sheet.w + 4,
                       sheet.y + sheet.h - 4, (0.3, 0.3, 0.3, 1))
            _draw_rect(shader, sheet.x, sheet.y, sheet.x + sheet.w, sheet.y + sheet.h,
                       (1, 1, 1, 1))
            gpu.state.scissor_test_set(True)
            gpu.state.scissor_set(int(sheet.x), int(sheet.y), int(sheet.w) + 1,
                                  int(sheet.h) + 1)
            try:
                for pts, rgb, _wpx in printdialog.preview_lines(page, sheet):
                    coords = []
                    for a_, b_ in zip(pts, pts[1:]):
                        coords.append(a_)
                        coords.append(b_)
                    if not coords:
                        continue
                    batch = batch_for_shader(shader, "LINES", {"pos": coords})
                    shader.uniform_float("color", (rgb[0], rgb[1], rgb[2], 1.0))
                    batch.draw(shader)
            finally:
                gpu.state.scissor_test_set(False)
        if st.message:
            # word-wrapped into the free space under the controls (a single line ran
            # off the panel -- Verified in a screenshot)
            nr = printdialog.notes_rect(lay)
            lines = printdialog.wrap(st.message, nr.w,
                                     lambda t_: _text_width(t_, fs - 1))
            line_h = 17 * sc
            y_ = nr.y + nr.h - line_h
            for ln in lines:
                if y_ < nr.y:
                    break
                _draw_text(nr.x, y_, ln, fs - 1, (0.25, 0.25, 0.25, 1))
                y_ -= line_h
        for bid, b in lay.buttons:
            shader.bind()
            fill = blue if bid == "print" else (0.88, 0.88, 0.9, 1)
            _draw_rect(shader, b.x, b.y, b.x + b.w, b.y + b.h, fill)
            label = dict(printdialog.BUTTONS)[bid]
            col = (1, 1, 1, 1) if bid == "print" else (0, 0, 0, 1)
            _draw_text(b.x + (b.w - _text_width(label, fs)) / 2, b.y + 9 * sc,
                       label, fs, col)
        # the open dropdown LAST, so it sits over everything it overlaps
        if st.open_dropdown and lay.dropdown:
            rid = st.open_dropdown
            opts = st.choices(rid)
            ys = [r.y for _i, r in lay.dropdown]
            r0 = lay.dropdown[0][1]
            frame = printdialog.Rect(r0.x, min(ys), r0.w,
                                     max(ys) + r0.h - min(ys))
            box(frame, (1, 1, 1, 1), (0.45, 0.45, 0.48, 1))
            cur = getattr(st, rid)
            for i, r in lay.dropdown:
                if i == st.hover:
                    shader.bind()
                    _draw_rect(shader, r.x, r.y, r.x + r.w, r.y + r.h, blue)
                col = (1, 1, 1, 1) if i == st.hover else ink
                mark = "\u2713 " if opts[i] == cur else "    "
                _draw_text(r.x + 6 * sc, r.y + 6 * sc,
                           mark + st.label_of(rid, opts[i]), fs, col)
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] print dialog draw failed: {e!r}")
    finally:
        gpu.state.blend_set("NONE")


_exit_installed = False


def _exit_handler(*_args):
    """bpy exit_pre: on ANY exit (even the window's close button, which Python cannot
    intercept) keep unsaved work in a recovery copy. Never raises."""
    from . import file_commands

    path = file_commands.write_recovery()
    if path:
        print(f"[narwhal3d] unsaved changes kept in {path}")


_autosave_last = [None]


def _autosave_tick():
    """Check once per minute whether `autosave.due` requires an unsaved-model copy.

    The interval defaults to an application-defined 20 minutes because the reference CAD application's default
    is not documented.
    """
    import datetime

    from . import autosave, file_commands

    try:
        minutes = float(os.environ.get("NARWHAL3D_AUTOSAVE_MINUTES", "20"))
        # `autosave.due` takes DATETIMES. This passed time.time floats, so every tick
        # raised "'float' object has no attribute 'total_seconds'" and prevented autosave.
        now = datetime.datetime.now()
        if _autosave_last[0] is None:
            _autosave_last[0] = now
        elif autosave.due(now, _autosave_last[0], minutes):
            path = file_commands.write_recovery()
            _autosave_last[0] = now
            if path:
                app_state.log(f"Autosaved to {path}")
                redraw_cmdbar()
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] autosave failed: {e!r}")
    return 60.0


def _install_exit_handling():
    """. Once per session: Exit can quit; Blender's .blend save prompt is off (it asks
    about the wrong file -- its Save writes a .blend); exits keep a recovery copy; and a
    recovery copy left by an earlier session is announced on the command line."""
    global _exit_installed
    if _exit_installed:
        return
    _exit_installed = True
    from . import file_commands

    # keep this session's output, so a report can attach it
    sessionlog.install()
    file_commands.quitter = lambda: bpy.ops.wm.quit_blender()
    try:
        bpy.context.preferences.view.use_save_prompt = False
    except Exception:  # noqa: BLE001
        pass
    if _exit_handler not in bpy.app.handlers.exit_pre:
        bpy.app.handlers.exit_pre.append(_exit_handler)
    if not bpy.app.timers.is_registered(_autosave_tick):
        bpy.app.timers.register(_autosave_tick, first_interval=60.0, persistent=True)
    note = file_commands.recovery_notice()
    if note:
        app_state.log(note)


def _attach_preview_handler():
    """Register the preview draw handler on the VIEW_3D space TYPE, in POST_VIEW.

    Type-level for the same reason as the labels and the command bar: it must
    cover panes that do not exist yet when this runs.
    """
    if _preview_handlers:
        return True
    # the reference CAD application's finite CPlane grid. FIRST, because POST_VIEW handlers draw
    # in registration order and the overlays below write no depth: registered later, the
    # grid would paint over a coloured curve lying on the CPlane.
    gr_id = bpy.types.SpaceView3D.draw_handler_add(
        draw_cplane_grid, (), "WINDOW", "POST_VIEW")
    _preview_handlers.append((bpy.types.SpaceView3D, gr_id))
    # A Picture is an unlit image pass in Shaded/Rendered. Register it before BOTH live
    # previews and completed curves: POST_VIEW handlers draw in registration order, so
    # reference images remain behind every line the user is drawing or has committed.
    pic_id = bpy.types.SpaceView3D.draw_handler_add(
        draw_rendered_pictures, (), "WINDOW", "POST_VIEW")
    _preview_handlers.append((bpy.types.SpaceView3D, pic_id))
    handler_id = bpy.types.SpaceView3D.draw_handler_add(
        draw_command_preview, (), "WINDOW", "POST_VIEW")
    _preview_handlers.append((bpy.types.SpaceView3D, handler_id))
    # every curve (Blender no longer draws them -- see `draw_curves`), layer
    # colours included
    lc_id = bpy.types.SpaceView3D.draw_handler_add(
        draw_curves, (), "WINDOW", "POST_VIEW")
    _preview_handlers.append((bpy.types.SpaceView3D, lc_id))
    _attach_curve_depsgraph_handler()

    # Rendered mode's soft ground shadow
    gs_id = bpy.types.SpaceView3D.draw_handler_add(
        draw_ground_shadow, (), "WINDOW", "POST_VIEW")
    _preview_handlers.append((bpy.types.SpaceView3D, gs_id))
    # the Print dialog is pixel-space chrome over a viewport
    dlg_id = bpy.types.SpaceView3D.draw_handler_add(
        draw_print_dialog, (), "WINDOW", "POST_PIXEL")
    _preview_handlers.append((bpy.types.SpaceView3D, dlg_id))
    if not bpy.app.background:
        from . import print_commands

        print_commands.dialog_opener = open_print_dialog
    # the SetDisplayMode command acts on real viewports through this hook
    from . import display_commands

    display_commands.mode_setter = set_display_mode_scope
    if not bpy.app.background:
        _install_exit_handling()
    return True


# ---------------------------------------------------------------------------
# the Gumball transform widget
#
# the reference CAD application's AutoGumball is ON by default ("Gumball On -- Turns the gumball widget on
# any time objects are selected" -- the reference CAD application documentation), so the widget appears
# as soon as something is selected; the `Gumball` command's On/Off/Toggle options
# drive the flag below.
#
# Everything decidable is in the pure `gumball` module. What lives here is only
# the part that genuinely needs Blender: projecting world handles to region
# pixels, reading the mouse, and baking the resulting Transform into the document.
# ---------------------------------------------------------------------------

_gumball_enabled = True
#: The active `GumballAlignment` (the reference CAD application documentation). CPlane is the reference CAD application's default.
_gumball_alignment = gumball.DEFAULT_ALIGNMENT
_gumball_handlers = []
#: the key of the handle under the cursor, so it can highlight BEFORE you grab it
#: (the reference CAD application: "Point the mouse cursor to a handle to show the tooltip").
_gumball_hover = None
#: where the cursor was when `_gumball_hover` was last computed, in WINDOW coords,
#: and which pane it was in. A POST_PIXEL draw handler receives no event, so the
#: tooltip has no other way to know where to put itself.
_gumball_hover_pos = None
_gumball_hover_pane = None

#: application-defined: tooltip placement and appearance. The help says a tooltip appears and what
#: it says, never how it looks. Offset down-right of the cursor so it does not cover
#: the handle being pointed at, and the lines run DOWNWARD from the first.
TOOLTIP_OFFSET_PX = (16.0, -6.0)
TOOLTIP_LINE_PX = 14.0
TOOLTIP_FONT_SIZE = 11
TOOLTIP_COLOR = (0.88, 0.88, 0.88, 1.0)
#: in-progress drag state, or None. See `_gumball_begin` for its shape.
_gumball_drag = None

#: In-progress RELOCATE gesture, or None. See `_gumball_relocate_begin`. Separate
#: from `_gumball_drag` on purpose: a relocate moves the WIDGET and must never
#: touch the document, and the reference CAD application's gesture is not a drag at all -- the button is
#: already up by the time it starts ("Move the mouse and click").
_gumball_relocate = None

#: (time, handle key) of the last press on a Gumball handle, for our own
#: double-click detection -- the same approach and the same reason as
#: `_last_title_click`: only PRESS reaches the modal, so Blender's DOUBLE_CLICK
#: event value never arrives.
_last_gumball_click = (0.0, None)


#: Relocation offset from the widget's default (bounding-box) origin, in world
#: units. Verified (the reference CAD application documentation, Gumball menu): "Relocate Gumball -- Drag
#: to relocate gumball widget away from its default position." / "Reset Gumball --
#: Restores the default placement and orientation for the gumball." Kept as an
#: OFFSET, not an absolute point, because the default moves with the selection and
#: the reference CAD application's relocated gumball does not snap back to the box centre on its own.
_gumball_offset = gumball.ZERO_OFFSET

#: "Drag Strength -- Sets the amount of the gumball drag as a percentage of mouse
#: movement". Verified default 100%.
_gumball_drag_strength = gumball.DEFAULT_DRAG_STRENGTH

#: "Snappy Dragging -- Object snaps are used while dragging." / "Smooth Dragging --
#: Object snaps are ignored while dragging." The default is application-defined; see the module.
_gumball_snappy = gumball.DEFAULT_SNAPPY_DRAGGING


def gumball_offset():
    """The widget's relocation offset from its default origin."""
    return _gumball_offset


def relocate_gumball(delta):
    """Move the widget off its default position by `delta`. Returns the new offset."""
    global _gumball_offset

    _gumball_offset = gumball.relocate_offset(_gumball_offset, delta)
    _tag_redraw_viewports()
    return _gumball_offset


def reset_gumball():
    """"Restores the default placement and orientation for the gumball."

    Returns True when something actually changed, so the caller can say so rather
    than reporting a reset that did nothing.
    """
    global _gumball_offset, _gumball_turn

    changed = gumball.is_relocated(_gumball_offset) or _gumball_turn is not None
    _gumball_offset = gumball.ZERO_OFFSET
    _gumball_turn = None
    if changed:
        _tag_redraw_viewports()
    return changed


#: the widget's KEPT orientation after a rotate drag (or a double-click
#: turn of an arc): ``{"plane": frame, "sel": frozenset(selection)}``, or None. Kept only
#: for the selection it was made on -- a different selection starts from the alignment.
_gumball_turn = None
#: "AutoReset Gumball -- When an object is rotated, resets Gumball to align with CPlane or
#: World axes based on the initial alignment." Off by default (gumball.DEFAULT_AUTORESET).
_gumball_autoreset = gumball.DEFAULT_AUTORESET


def gumball_autoreset():
    return _gumball_autoreset


def set_gumball_autoreset(on):
    global _gumball_autoreset, _gumball_turn
    _gumball_autoreset = bool(on)
    if _gumball_autoreset:
        _gumball_turn = None
    _tag_redraw_viewports()
    return _gumball_autoreset


def _undo_serial():
    try:
        return app_state.ctx.undo.top_serial
    except Exception:  # noqa: BLE001
        return None


def _gumball_kept_plane():
    """The kept (turned) frame for the CURRENT selection, or None."""
    global _gumball_turn
    t = _gumball_turn
    if t is None:
        return None
    if frozenset(selection_names()) != t["sel"]:
        _gumball_turn = None
        return None
    return t["plane"]


def gumball_drag_strength():
    return _gumball_drag_strength


def set_gumball_drag_strength(percent):
    """Set the drag strength. Returns the value in force, or None if refused."""
    global _gumball_drag_strength

    value = gumball.clamp_drag_strength(percent)
    if value is None:
        return None
    _gumball_drag_strength = value
    return _gumball_drag_strength


def gumball_snappy():
    """True when object snaps are used while dragging ("Snappy Dragging")."""
    return _gumball_snappy


def set_gumball_snappy(on):
    global _gumball_snappy

    _gumball_snappy = bool(on)
    return _gumball_snappy


def gumball_copy_mode():
    """True when the running drag is in Alt-tap copy mode."""
    return bool(_gumball_drag and _gumball_drag.get("copy"))


def gumball_enabled():
    """Is the Gumball widget switched on?"""
    return _gumball_enabled


def set_gumball(on):
    """Turn the widget on/off (the `Gumball` command's On/Off/Toggle)."""
    global _gumball_enabled
    changed = bool(on) != _gumball_enabled
    _gumball_enabled = bool(on)
    if changed:
        _tag_redraw_viewports()
    return _gumball_enabled


def gumball_dragging():
    """True while a handle is being dragged (tests and the modal both ask)."""
    return _gumball_drag is not None


def gumball_hover():
    """The key of the highlighted handle (tests read this)."""
    return _gumball_hover


def _selection_world_points():
    """World points the Gumball should centre itself on.

    When CONTROL POINTS are selected those are what the widget follows -- the reference CAD application
    puts the Gumball on the grips you picked, not on the whole object, and dragging
    it then edits the curve.

    Otherwise it is the world corners of every selected object's bounding box.
    `bound_box` is in the object's LOCAL space, so each corner must go through
    `matrix_world` -- otherwise a moved object would place the gumball where it
    used to be.
    """
    from mathutils import Vector

    # control points win: they are a finer selection inside an object that is
    # itself still selected, so using the object's box would put the widget in the
    # wrong place and move the wrong thing.
    # (`_cp_rows`, uncopied, and only when a grip IS selected: with a big file's Select
    # All this list is 194k rows, and the gumball asks every pane, every frame)
    cps = [world for name, index, world in _cp_rows()
           if (name, index) in _selected_cps] if _selected_cps else []
    if cps:
        return cps

    # Sub-objects win for the same reason: a selected SEGMENT is a finer selection
    # inside an object that is usually still selected, so the object's bounding box
    # would put the widget in the wrong place and move the wrong thing.
    subs = _selected_subobject_points()
    if subs:
        return subs

    pts = []
    try:
        selected = bridge.selected_display_objects()
    except Exception:  # noqa: BLE001
        return pts
    for ob in selected:
        mw = ob.matrix_world
        for corner in bridge.local_box_corners(ob):
            v = mw @ Vector(corner)
            pts.append((v.x, v.y, v.z))
    return pts


def gumball_targets_control_points():
    """True when the Gumball is acting on control points rather than objects."""
    return bool(_selected_cps) and any(
        (name, index) in _selected_cps for name, index, _w in _cp_rows())


def _view_frame(area):
    """The pane's view plane as a CPlane frame, or None.

    Needed by `GumballAlignment View` -- "Gumball widgets align to the view plane
    of the current viewport". `cplane.from_camera` already turns a camera frame
    into a plane, and it is the same rule the standard view CPlanes were derived
    from, so View alignment reuses validated math rather than new math.
    """
    if area is None:
        return None
    space = _view3d_space(area)
    rv3d = getattr(space, "region_3d", None) if space is not None else None
    if rv3d is None:
        return None
    try:
        from mathutils import Vector

        direction = rv3d.view_rotation @ Vector((0.0, 0.0, -1.0))
        up = rv3d.view_rotation @ Vector((0.0, 1.0, 0.0))
    except Exception:  # noqa: BLE001
        return None
    return cplane.from_camera((direction.x, direction.y, direction.z),
                             (up.x, up.y, up.z))


def gumball_alignment():
    """The active `GumballAlignment` value (the reference CAD application's default is CPlane)."""
    return _gumball_alignment


def set_gumball_alignment(name):
    """Set the alignment. Returns the name in force afterwards, or None if refused.

    An alignment we do not implement is REFUSED rather than stored -- storing it
    and then falling back to CPlane would leave the widget claiming an alignment it
    is not using.
    """
    global _gumball_alignment, _gumball_turn
    key = gumball.canonical_alignment(name)
    if key is None:
        return None
    if not gumball.alignment_is_implemented(key):
        return None
    _gumball_alignment = key
    _gumball_turn = None                  # choosing an alignment re-aligns the widget
    _tag_redraw_viewports()
    return _gumball_alignment


#: `gumball_frame`'s selection points and their box centre per plane, valid for one
#: (document state, object / grip / sub-object selection) -- see the comment there
_gumball_pts_cache = {"key": None, "pts": [], "centres": {}, "builds": 0}


def gumball_frame():
    """``(origin, plane)`` for the widget, or None when there is nothing to show.

    ONE frame shared by all four panes, for the same reason the preview has one
    (`_preview_cplane`): the widget is a single world-space object that every pane
    looks at from its own camera.

    Which frame depends on `GumballAlignment` (the reference CAD application documentation): the active
    pane's CPlane by default, world xyz, or the pane's view plane. Note that the
    alignment also decides the frame the BOUNDING BOX is measured in -- "The
    Gumball origin will be at the CPlane oriented bounding box center" versus "at
    the World oriented bounding box center" -- which comes out right for free,
    because `bbox_center` takes the same frame.

    While a drag is running the frame is **frozen** to what the drag started with.
    Recomputing it mid-drag would feed the object's own motion back into the
    widget's origin -- a rotate would then chase its own bounding box.

    Frozen, but not immobile: a translating drag adds its applied delta to
    ``_gumball_drag["widget_shift"]`` and that shift is applied here, so the widget
    travels with the object exactly as the reference CAD application's does, without reopening the feedback
    loop the freeze exists to prevent. See `_gumball_begin` for why the shift is kept
    separate from the drag's own ``origin``.
    """
    if _gumball_drag is not None:
        plane = _gumball_drag["plane"]
        # Turn the widget with a rotate drag as it progresses, using the
        # angle applied so far -- the same `rotate_frame` the release keeps (#18), so
        # the widget does not jump on release (AutoReset, if on, re-aligns it THEN).
        # Only the orientation follows: the origin is the rotation centre and does not
        # move, so the freeze above still holds.
        if ((_gumball_drag.get("mode") or _gumball_drag.get("kind")) == gumball.ROTATE
                and _gumball_drag.get("axis_dir")):
            plane = gumball.rotate_frame(plane, _gumball_drag["axis_dir"],
                                         _gumball_drag.get("total", 0.0))
        return (gumball.relocated_origin(_gumball_drag["origin"],
                                         _gumball_drag.get("widget_shift")),
                plane)
    if not _gumball_enabled:
        return None
    # Cache selection bounds; recomputing them for thousands of curves is expensive
    # (44k box corners, then each taken into the plane), asked by all four panes every
    # frame. The points depend only on the document and on what is selected (objects,
    # grips, sub-objects), and the centre on those plus the plane -- so both are kept
    # for that state; the plane-independent part is shared by every alignment.
    skey = (_doc_key(), frozenset(selected_display_names()), frozenset(_selected_cps),
            frozenset(_selected_subobjects))
    gc = _gumball_pts_cache
    if skey[0] is None or gc["key"] != skey:
        gc["key"], gc["pts"], gc["centres"] = skey, _selection_world_points(), {}
        gc["builds"] += 1
    pts = gc["pts"]
    if not pts:
        return None
    area = active_pane_area()
    cp = get_cplane(area) if area is not None else cplane.WORLD_TOP
    plane = gumball.frame_for_alignment(
        _gumball_alignment,
        cplane_frame=cp,
        world_frame=cplane.WORLD_TOP,
        view_frame=_view_frame(area))
    if plane is None:
        # the alignment could not be honoured -- fall back to the CPlane, which is
        # the reference CAD application's documented default, rather than drawing nothing
        plane = cp
    if _selected_subobjects and gumball_targets_brep_part():
        face_plane = _brep_part_frame(plane)    # reopened #29: aligned to the face
        if face_plane is not None:
            plane = face_plane
    kept = _gumball_kept_plane()          # a rotation is kept
    if kept is not None:
        plane = kept
        t = _gumball_turn or {}
        pinned = t.get("origin")
        if pinned is not None and t.get("serial") != _undo_serial():
            pinned = None                 # the document changed since: re-measure
        if pinned is not None:
            # a widget-only turn: the object did not move, so neither does the centre
            # (its box measured in the turned frame would have a different middle)
            return pinned, plane
    try:
        pk = (tuple(plane.origin), tuple(plane.xaxis), tuple(plane.yaxis))
    except Exception:  # noqa: BLE001
        pk = None
    origin = gc["centres"].get(pk) if pk is not None else None
    if origin is None:
        origin = gumball.bbox_center(plane, pts)
        if pk is not None and origin is not None:
            gc["centres"][pk] = origin
    if origin is None:
        return None
    # A relocation survives until Reset, so it is applied to whatever the default
    # origin currently is -- which is what stops the widget snapping back to the box
    # centre as soon as the selection changes.
    return gumball.relocated_origin(origin, _gumball_offset), plane


# ---------------------------------------------------------------------------
# THE VIEWPORT TITLE (the reference CAD application documentation)
#
# Three behaviours, all Verified on that page, none of them previously implemented:
#
#  "Click the title of a viewport to activate the viewport WITHOUT DESELECTING
#  objects."
#  "Double-click a viewport title to maximize the viewport, or restore the
#  multi-viewport layout."
#  "Click the triangle button or right-click on the viewport title to show the
#  menu for view management commands."
#
# The first two are here. The third is NOT built: a menu of view commands we do not
# have would be a menu of dead items, so `MAXIMIZE_*` says what is missing instead
# (house rule 4).
# ---------------------------------------------------------------------------

def _title_hit_rect(area, region=None):
    """The title chip's rect in region pixels for `area`, or None.

    Measured with the same font the label is drawn with, then handed to
    `views.title_rect` -- the SAME function the draw uses, so the box cannot drift
    from the chip on screen.
    """
    if area is None:
        return None
    if region is None:
        region = _window_region(area)
    if region is None or region.height < 4:
        return None
    space = _view3d_space(area)
    rv3d = getattr(space, "region_3d", None) if space is not None else None
    if rv3d is None:
        return None
    label = quat_to_label(tuple(rv3d.view_rotation), rv3d.view_perspective)
    try:
        import blf

        blf.size(_DEFAULT_FONT_ID, LABEL_FONT_SIZE)
        text_w, text_h = blf.dimensions(_DEFAULT_FONT_ID, label)
    except Exception:  # noqa: BLE001
        text_w = LABEL_FONT_SIZE * 0.6 * len(label)
        text_h = LABEL_FONT_SIZE
    return views.title_rect(text_w, text_h, region.height, label_pad=LABEL_PAD)


def pane_title_at(mouse_x, mouse_y):
    """``(area, space, region)`` when the window cursor is on a pane TITLE, else None."""
    area, space, region = _view3d_under_mouse(mouse_x, mouse_y)
    if region is None:
        return None
    rect = _title_hit_rect(area, region)
    if views.hit_title(mouse_x - region.x, mouse_y - region.y, rect):
        return area, space, region
    return None


def pane_title_arrow_at(mouse_x, mouse_y):
    """``(area, space, region)`` when the cursor is on a title's DROP-DOWN ARROW.

    The title arrow exposes the existing viewport menu on left-click without changing
    route, not a second menu.

    Uses `_point_in_view3d`-style ownership through `_view3d_under_mouse` exactly as
    `pane_title_at` does. Note the hit rect is the ARROW's, which sits just outside
    the title chip, so a click can never be both.
    """
    area, space, region = _view3d_under_mouse(mouse_x, mouse_y)
    if region is None:
        return None
    rect = _title_hit_rect(area, region)
    if views.hit_title_arrow(mouse_x - region.x, mouse_y - region.y, rect):
        return area, space, region
    return None


# --- maximize / restore ----------------------------------------------------
#
# Verified WHY IT IS A STATE MACHINE (a focused runtime check):
#
#  * `screen.screen_full_area` is the WRONG lever. It builds a new screen
#   ("Layout-nonnormal") holding ONE area at 95.4% of the window, so the command
#   bar disappears -- the reference CAD application keeps it. Restoring also leaves `bpy.context.screen`
#   None for a tick.
#  * `screen.area_close` is the RIGHT lever: the command bar survives and no new
#   screen is created.
#  * BUT closing one area invalidates every other `Area` reference
#   ("TypeError: Area set with window & screen set to None"), so the panes must
#   be closed ONE PER TICK and re-found each tick. Same constraint the layout
#   builder lives under, which is why this mirrors its shape.
#
# The pane to KEEP is identified by a POINT inside it rather than by reference:
# references go stale, and as the others close the keeper only grows, so a point
# inside it stays inside it.

#: True while a pane is maximized. the reference CAD application's double-click toggles between the two.
_maximized = False
#: Saved per-pane camera state, in canonical position order, so restore puts the
#: views back as they were instead of resetting them to the four defaults.
_saved_pane_views = []
#: Guard: one maximize/restore sequence at a time.
_maximize_busy = False

#: the reference CAD application's own double-click interval is not documented. application-defined: 0.5 s is the common
#: platform default and is generous enough not to lose a deliberate double-click.
DOUBLE_CLICK_SECONDS = 0.5

#: What the viewport title does NOT do yet, reported rather than silently missing.
MAXIMIZE_NOT_IMPLEMENTED = (
    "the triangle button's view menu",
    "dragging a title to move the viewport",
)


def is_maximized():
    """True when one pane is filling the viewport frame."""
    return _maximized


def _canonical_panes():
    """The VIEW_3D panes in the order `_assign_four_views` uses.

    By column (ascending x), then top-to-bottom within a column (descending y --
    Blender's y grows upward). Position-based for the reason that function
    documents: area proxies are not identity-stable.
    """
    panes = [a for a in bpy.context.screen.areas if a.type == "VIEW_3D"]
    by_x = sorted(panes, key=lambda a: a.x)
    if len(by_x) != 4:
        return sorted(panes, key=lambda a: (a.x, -a.y))
    left = sorted(by_x[:2], key=lambda a: -a.y)
    right = sorted(by_x[2:], key=lambda a: -a.y)
    return [left[0], left[1], right[0], right[1]]


def _pane_view_state(area):
    """One pane's camera as a plain dict, or None."""
    space = _view3d_space(area) if area is not None else None
    rv3d = getattr(space, "region_3d", None) if space is not None else None
    if rv3d is None:
        return None
    return {
        "rotation": tuple(rv3d.view_rotation),
        "perspective": rv3d.view_perspective,
        "location": tuple(rv3d.view_location),
        "distance": float(rv3d.view_distance),
        # restore re-SPLITS the panes, and a split copies the maximized pane's
        # shading -- so each pane's own display mode has to be put back with its camera.
        "display": display_mode_of(area),
    }


def _capture_pane_views():
    """Each pane's camera, in canonical order, so restore is not a reset."""
    out = []
    for area in _canonical_panes():
        space = _view3d_space(area)
        rv3d = getattr(space, "region_3d", None) if space is not None else None
        if rv3d is None:
            out.append(None)
            continue
        out.append(_pane_view_state(area))
    return out


def _apply_pane_views(states):
    """Put saved cameras back, by position. True when all four were applied."""
    panes = _canonical_panes()
    if len(panes) != len(states):
        return False
    applied = 0
    for area, st in zip(panes, states):
        if st is None:
            continue
        space = _view3d_space(area)
        rv3d = getattr(space, "region_3d", None) if space is not None else None
        if rv3d is None:
            continue
        try:
            rv3d.view_perspective = st["perspective"]
            rv3d.view_rotation = st["rotation"]
            rv3d.view_location = st["location"]
            rv3d.view_distance = st["distance"]
            if st.get("display"):
                set_display_mode(area, st["display"])
            applied += 1
        except Exception:  # noqa: BLE001
            pass
    return applied == len([s for s in states if s is not None])


#: The running maximize/restore job, or None. A module-level dict rather than a
#: closure so the step can be DRIVEN from a test: `the live-window UI checks` calls
#: `_maximize_step` in a loop, because a test cannot block the main thread
#: waiting for `bpy.app.timers` (blocking is exactly what stops them firing).
_maximize_job = None


def _maximize_step():
    """Advance the running maximize/restore job by ONE action. True when finished.

    One action per call because `area_close` and `area_split` each need their own
    reflow, and because closing an area invalidates every other `Area` reference --
    so every step re-finds what it needs instead of holding anything.
    """
    global _maximize_job, _maximized, _maximize_busy

    job = _maximize_job
    if job is None:
        return True
    try:
        if bpy.context.screen is None:
            # A reflow can leave the screen momentarily None; wait it out.
            job["waits"] = job.get("waits", 0) + 1
            return job["waits"] > 20
        job["tick"] += 1

        if job["kind"] == "maximize":
            v3d = [a for a in bpy.context.screen.areas if a.type == "VIEW_3D"]
            if len(v3d) <= 1:
                # The surviving area is whichever one absorbed the others --
                # `area_close` decides that, not us. What the user asked for is a
                # VIEW, so the clicked pane's camera is applied to the survivor.
                # Which Blender area object it is is an implementation detail.
                want = job.get("target_view")
                if v3d and want is not None:
                    space = _view3d_space(v3d[0])
                    rv3d = getattr(space, "region_3d", None) if space else None
                    if rv3d is not None:
                        try:
                            rv3d.view_perspective = want["perspective"]
                            rv3d.view_rotation = want["rotation"]
                            rv3d.view_location = want["location"]
                            rv3d.view_distance = want["distance"]
                            if want.get("display"):
                                set_display_mode(v3d[0], want["display"])
                        except Exception:  # noqa: BLE001
                            pass
                _maximized = True
                return True

            # WHICH pane to close, and it is the whole difficulty. Verified by
            # dumping the area geometry after every close:
            #
            #  `area_close` merges an area into the neighbour it can merge with
            #  CLEANLY, and there is no way to tell it which one to use. In a 2x2
            #  the clean neighbour is always the SAME-ROW sibling, so closing a
            #  top pane produces a FULL-WIDTH top row -- whose remaining
            #  neighbours are the command bar above and the bottom row below.
            #  Blender picks the BAR: its height went 92 -> 524 and the
            #  "maximized" pane was left at 42.7% of the window.
            #
            # So the order must leave Blender no choice:
            #
            #  1. while any area still has a same-row sibling, close one of those.
            #   That is a HORIZONTAL merge, which can never involve the
            #   full-width bar. Repeating it collapses each row to full width.
            #  2. with two full-width rows left, neither has a sibling. Close the
            #   LOWER one, so it merges UPWARD into the row above. The bottom row
            #   does not touch the bar, so the bar cannot be the recipient.
            #
            # The top row is therefore never the area being closed -- it is the one
            # close whose recipient is ambiguous, and the one that loses.
            def _rows_overlap(a, b):
                return (min(a.y + a.height, b.y + b.height)
                        - max(a.y, b.y)) > 4

            def _geom(a):
                return (a.x, a.y, a.width, a.height)

            siblings = [a for a in v3d
                        if any(_rows_overlap(a, b) and _geom(b) != _geom(a)
                               for b in v3d)]
            if siblings:
                # rightmost, purely so the choice is deterministic
                victim = max(siblings, key=lambda a: a.x)
            else:
                victim = min(v3d, key=lambda a: a.y)
            reg = _window_region(victim)
            if reg is not None:
                with bpy.context.temp_override(area=victim, region=reg):
                    bpy.ops.screen.area_close()
            return False

        # restore: the split order is lifted from _build_viewports_gui ticks 6-7,
        # which is the sequence already proven to produce a balanced 2x2.
        v3d = [a for a in bpy.context.screen.areas if a.type == "VIEW_3D"]
        if job["tick"] == 1:
            if v3d:
                _split_area(max(v3d, key=lambda a: a.width * a.height),
                            "VERTICAL", 0.5)
            return False
        if job["tick"] == 2:
            cols = sorted(v3d, key=lambda a: a.x)
            if len(cols) >= 2:
                _split_area(cols[0], "HORIZONTAL", 0.5)
                _split_area(cols[-1], "HORIZONTAL", 0.5)
            return False
        panes = [a for a in bpy.context.screen.areas if a.type == "VIEW_3D"]
        for a in panes:
            _hide_viewport_chrome(a)
        # Put the saved cameras back. Falling back to the four standard views is
        # only for the case where we did not end up with four panes -- a coherent
        # layout beats four copies of one view.
        if not _apply_pane_views(job.get("saved") or []):
            _assign_four_views(panes)
        _maximized = False
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] {job['kind']} step failed: {e!r}")
        return job["tick"] > 20
    finally:
        if job["tick"] > 24:
            _maximize_job = None


def _split_area(area, direction, factor=0.5):
    """`screen.area_split` on `area`, with the override it needs."""
    reg = _window_region(area)
    if reg is None:
        return
    with bpy.context.temp_override(area=area, region=reg):
        bpy.ops.screen.area_split(direction=direction, factor=factor)


def _finish_maximize_job(message):
    global _maximize_job, _maximize_busy

    _maximize_job = None
    _maximize_busy = False
    _tag_redraw_viewports()
    app_state.log(message)
    redraw_cmdbar()


def _run_maximize_job(message):
    """Drive the job on Blender's timer, one action per tick."""
    def tick():
        if _maximize_step():
            _finish_maximize_job(message)
            return None
        return 0.05

    bpy.app.timers.register(tick, first_interval=0.0)


def maximize_pane(area):
    """Maximize `area` by closing the other panes, one per tick. True if started."""
    global _maximize_busy, _maximize_job, _saved_pane_views

    if _maximize_busy or _maximized or area is None:
        return False
    if len([a for a in bpy.context.screen.areas if a.type == "VIEW_3D"]) < 2:
        return False
    _maximize_busy = True
    # Kept on the module as well as on the job: `_finish_maximize_job` clears the
    # job, so by the time the user double-clicks again to RESTORE there is nowhere
    # else for the saved cameras to have survived.
    _saved_pane_views = _capture_pane_views()
    global _maximized_slot
    try:
        key = _pane_key(area)
        _maximized_slot = next((i for i, a in enumerate(_canonical_panes())
                                if _pane_key(a) == key), None)
    except Exception:  # noqa: BLE001
        _maximized_slot = None
    _maximize_job = {
        "kind": "maximize",
        "saved": _saved_pane_views,
        "target_view": _pane_view_state(area),
        "tick": 0,
    }
    _run_maximize_job("Viewport maximized (double-click the title to restore)")
    return True


#: Which canonical slot (0..3) the maximized pane came from, for `restore_panes` (#34).
_maximized_slot = None


def restore_panes():
    """Rebuild the 2x2 and put the saved cameras back. True if started."""
    global _maximize_busy, _maximize_job

    if _maximize_busy or not _maximized:
        return False
    saved = list((_maximize_job or {}).get("saved") or _saved_pane_views)
    # Preserve display-mode and camera changes made while maximized. Saved states are
    # captured at maximize time, so whatever changed
    # in the maximized pane -- its display mode, and its camera -- was overwritten on
    # restore. Its slot now takes the pane's state as it is NOW.
    v3d = [a for a in bpy.context.screen.areas if a.type == "VIEW_3D"]
    slot = _maximized_slot
    if len(v3d) == 1 and slot is not None and 0 <= slot < len(saved):
        now = _pane_view_state(v3d[0])
        if now is not None:
            saved[slot] = now
    _maximize_busy = True
    _maximize_job = {"kind": "restore", "saved": saved, "tick": 0}
    _run_maximize_job("Viewport layout restored")
    return True


def toggle_maximize(area):
    """the reference CAD application's double-click: maximize this pane, or restore the layout."""
    if _maximized:
        return restore_panes()
    return maximize_pane(area)


#: (time, pane key) of the last title click, for our own double-click detection.
_last_title_click = (0.0, None)


def title_click(area, space, region, mouse_x, mouse_y, now=None):
    """A left click landed on a pane title. Returns what it did, as a string.

    Verified: "Click the title of a viewport to activate the viewport **without
    deselecting objects**." So this activates the pane and returns -- it must NOT
    fall through to the selection path, which is exactly what deselects.

    Double-click is detected by TIMING, not by Blender's `DOUBLE_CLICK` event
    value. `DOUBLE_CLICK` is in `Event.value`'s enum, but the caller only ever
    reaches here on `PRESS` (its enclosing condition filters on it), so that event
    never arrives -- and honouring both it and a timer would toggle maximize twice.
    One detector, here.
    """
    global _last_title_click

    if now is None:
        import time

        now = time.monotonic()
    key = _pane_key(area)
    last_t, last_key = _last_title_click
    _last_title_click = (now, key)

    set_active_pane(area)
    if last_key == key and (now - last_t) <= DOUBLE_CLICK_SECONDS:
        _last_title_click = (0.0, None)      # a 3rd click starts a new pair
        toggle_maximize(area)
        return "restore" if not _maximized else "maximize"
    return "activate"


#: application-defined -- how near (pixels) an object's projected world box must come to a click for
#: `_pick_object_at` to ask Blender's picker at all. Blender's GPU pick looks within
#: 14 px of the click (`mixed_bones_object_selectbuffer`, view3d_select.cc); the rest is
#: slack. Only ever makes the cull keep MORE.
PICK_OBJECT_CULL_PX = 24.0

#: `_pick_could_hit`'s world boxes of the pickable non-curve objects, per `key`.
_pick_boxes = {"key": None, "unbounded": False, "lo": None, "hi": None}


def _pick_could_hit(region, rv3d, rx, ry):
    """False only when NO object Blender's picker could hit lies near region pixel
    (rx, ry): every visible non-curve object's world box is projected (`osnap.
    boxes_reach`, exact for boxes in front of the eye, conservative otherwise) and none
    comes within `PICK_OBJECT_CULL_PX`. Curves are not Blender's to pick (app-drawn).
    Anything that is not a mesh cannot be bounded this way, so it keeps the picker on.

    Avoiding `view3d.select` for known-empty clicks keeps large-document selection responsive."""
    import numpy as np

    # the boxes, kept while nothing could have moved, shown or hidden an object (any
    # depsgraph update or display change bumps the serial / vis)
    key = (_doc_key(), _curve_dg["serial"], _curve_dg["vis"], len(bpy.data.objects))
    c = _pick_boxes
    if c["key"] != key:
        coll = bpy.data.collections.get(bridge.CURVE_COLLECTION)
        app = set(coll.objects.keys()) if coll is not None else set()
        keys = bpy.data.objects.keys()
        obs = None
        los, his, unbounded = [], [], False
        for i, k in enumerate(keys):
            if k in app:
                continue
            if obs is None:
                obs = bpy.data.objects.values()
            ob = obs[i]
            try:
                if ob.hide_viewport or ob.hide_get():
                    continue
            except Exception:  # noqa: BLE001 (not in this view layer: not pickable)
                continue
            if ob.type != "MESH":
                unbounded = True
                break
            try:
                m = np.array(ob.matrix_world, dtype=np.float64)
                bb = np.array(ob.bound_box, dtype=np.float64) @ m[:3, :3].T + m[:3, 3]
            except Exception:  # noqa: BLE001
                unbounded = True
                break
            los.append(bb.min(axis=0))
            his.append(bb.max(axis=0))
        c.update(key=key, unbounded=unbounded,
                 lo=np.array(los, dtype=np.float64).reshape(-1, 3),
                 hi=np.array(his, dtype=np.float64).reshape(-1, 3))
    if c["unbounded"]:
        return True
    los, his = c["lo"], c["hi"]
    if not len(los):
        return False
    try:
        matrix = np.array(rv3d.perspective_matrix, dtype=np.float64)
        mouse = (float(rx), float(ry))
        rects = native_selection.screen_rects_from_aabbs(
            los, his, matrix, float(region.width), float(region.height))
        if rects is None:
            keep = osnap.boxes_reach(
                los, his, matrix, float(region.width), float(region.height),
                mouse, PICK_OBJECT_CULL_PX)
        else:
            keep = osnap.rects_reach(rects, mouse, PICK_OBJECT_CULL_PX)
    except Exception:  # noqa: BLE001
        return True
    return bool(keep.any())


def _pick_object_at(area, space, region, rx, ry):
    """The name of the object under region pixel (rx, ry), or None.

    Blender's `view3d.select` is borrowed purely as an occlusion-aware PICKER --
    it knows what is in front of what, which a bounding-box test does not. What it
    must NOT decide is the selection semantics, because Blender's Shift/Ctrl
    conventions are not the reference CAD application's (see `select.py`). So the current selection is
    saved, the operator is run in EXEC mode at an explicit location to find the
    hit, and the selection is put straight back; `select.apply` then decides.

    EXEC_DEFAULT with `location=` is used deliberately: INVOKE mode reads the
    real mouse position from the event instead of the location we pass, which is
    wrong here and returns CANCELLED when driven from a script (measured in
    a focused runtime check).
    """
    if area is None or region is None or space is None:
        return None
    import bpy

    # Background Blender (`-b`) SEGFAULTS when `view3d.select` is driven through a
    # fabricated override -- measured: it crashed the headless UI checks. Checking for a
    # missing window/screen is NOT enough, because in background mode both exist
    # (`bpy.context.window` and `bpy.context.screen` are real objects there); the
    # canonical test is `bpy.app.background`. There is nothing to pick in a
    # viewport that was never drawn anyway.
    if bpy.app.background:
        return None
    window = getattr(bpy.context, "window", None)
    screen = getattr(bpy.context, "screen", None)
    if window is None or screen is None:
        return None
    if not getattr(region, "width", 0) or not getattr(region, "height", 0):
        return None
    rv3d = getattr(space, "region_3d", None)
    if rv3d is not None and not _pick_could_hit(region, rv3d, rx, ry):
        return None

    # the VIEW LAYER's selection, not `selected_objects`: the latter omits every curve
    # (Blender does not draw them), and a curve selected before the pick must survive it
    before = bridge.view_layer_selected()
    active_before = bpy.context.view_layer.objects.active
    hit = None
    try:
        with bpy.context.temp_override(window=window, screen=screen,
                                      area=area, region=region,
                                      space_data=space):
            bpy.ops.view3d.select("EXEC_DEFAULT",
                                  location=(int(rx), int(ry)),
                                  deselect_all=True)
        picked = [ob for ob in bpy.context.selected_objects]
        if picked:
            hit = picked[0].name
    except Exception:  # noqa: BLE001
        return None
    finally:
        # put the selection back exactly as it was; the caller owns what happens
        for ob in bridge.view_layer_selected():
            try:
                ob.select_set(False)
            except Exception:  # noqa: BLE001
                pass
        for ob in before:
            try:
                ob.select_set(True)
            except Exception:  # noqa: BLE001
                pass
        try:
            bpy.context.view_layer.objects.active = active_before
        except Exception:  # noqa: BLE001
            pass
    return hit


def locked_object_names():
    """Display names of every LOCKED object in the document.

    Reads `ObjectAttributes.Mode` straight from the document, the same attribute
    `Lock` and `Unlock` write. Keeping this query local avoids coupling document lock
    state to geometry conversion.
    """
    out = set()
    model = getattr(app_state, "model", None)
    file3dm = getattr(model, "file", None)
    if file3dm is None:
        return out
    try:
        import rhino3dm as r3
        locked = int(r3.ObjectMode.Locked)
    except Exception:  # noqa: BLE001
        return out
    for name, mode, _layer, _geom, _attrs in _doc_rows():
        if not name:
            continue
        try:
            if mode == locked:
                out.add(name)
        except Exception:  # noqa: BLE001
            continue
    return out


def sync_locked_selectability():
    """Mark every locked object un-pickable in Blender. Returns how many are locked.

    **Verified that locked objects cannot be selected.** The help never puts it in
    one sentence, but it says it repeatedly from the other side -- every `Sel*`
    command is documented as selecting objects "that are **not locked or hidden**"
    (`SelSubD`, `SelOpenSubD`, `SelClosedSubD`), and `FindText` cannot reach
    "hidden or locked annotations". Locking is not hiding: a locked object stays
    visible ("Locked objects appear solid / ... appear transparent / Gray locked
    object displayed behind others" -- the reference CAD application documentation), it
    just cannot be picked.

    Implemented with Blender's own `hide_select` rather than by filtering our pick
    RESULT, and that choice buys the behaviour the reference CAD application actually has: `view3d.select`
    skips unselectable objects entirely, so a click on a locked object picks
    whatever is BEHIND it instead of picking nothing. Filtering the result
    afterwards could only ever turn the click into a miss, because the picker
    returns one hit and cannot be asked for the next one.

    Called from the selection paths rather than from a redraw handler on purpose:
    this walks the document, and doing that every frame is what made control-point
    dragging slow. A click is rare; a frame is not.
    """
    import bpy

    locked = _locked_names_cached()
    for ob in bpy.data.objects:
        want = ob.name in locked
        try:
            if bool(ob.hide_select) != want:
                ob.hide_select = want
        except Exception:  # noqa: BLE001
            continue
    return len(locked)


def apply_selection(names, already_expanded=False):
    """Make exactly ``names`` the selection. Returns the names actually found."""
    if already_expanded:
        return app_state.ctx.select_by_names(names, already_expanded=True)
    return app_state.ctx.select_by_names(names)


def selection_names():
    """The current selection, as object names."""
    try:
        return [ob.name for ob in app_state.ctx.selected_objects()]
    except Exception:  # noqa: BLE001
        return []


def narwhal_click_select(area, space, region, rx, ry, shift=False, ctrl=False):
    """One CAD-style selecting click. Returns the new selection.

    Verified (the reference CAD application documentation): a plain click selects what it hit
    and clicking away deselects; Shift ADDS; Ctrl REMOVES. Blender does none of
    those three the same way, which is why this is ours.

    POINT OBJECTS are tried first. They have no extent, so their Blender mesh is a
    single vertex with no faces and `view3d.select` cannot hit one -- a point drew
    but could not be clicked. They are also drawn on top of everything, so a click
    within a few pixels of a point marker means the point, not whatever solid is
    behind it.

    CURVES next: Blender does not draw them (`draw_curves` does), so `view3d.select`
    cannot hit them; `pick_curve_object` tests the drawn polylines, and a curve within
    its tolerance wins over a surface (see there).
    """
    # Locked objects must not be pickable, and marking them unselectable here (rather
    # than discarding the hit afterwards) is what lets the click reach whatever is
    # BEHIND one. See `sync_locked_selectability`. FIRST, before any picker: a stale
    # `hide_select` left by an earlier click would also block selecting an object
    # that has since been unlocked (Verified with a curve: Lock, click, Unlock, click).
    sync_locked_selectability()
    hit = pick_point_object(area, space, region, rx, ry)
    if hit is None:
        hit = pick_curve_object(area, space, region, rx, ry)
    if hit is None:
        hit = _pick_object_at(area, space, region, rx, ry)
    global _last_click_hit
    _last_click_hit = hit
    action = select.modifier_action(shift=shift, ctrl=ctrl)
    hits = [hit] if hit else []
    # the reference CAD application Group: selecting one member selects the whole group and commands apply to it.
    # Expand the HIT before modifier semantics so Ctrl removes the whole clicked group;
    # expanding only the final selection would immediately re-add the clicked member from
    # its still-selected partners.
    if hits:
        hits = app_state.ctx.expand_group_names(hits)
    wanted = select.apply(selection_names(), hits, action)
    apply_selection(wanted, already_expanded=True)
    _tag_redraw_viewports()
    return wanted


#: The object name the last `narwhal_click_select` hit, or None.
_last_click_hit = None

#: Samples along a curve when finding the point nearest a click. application-defined: enough that
#: the nearest sample lies on the right SEGMENT of any polycurve the app makes; the
#: result is then refined on the exact curve, so this is not the accuracy.
PICK_CURVE_SAMPLES = 400


def object_pick_location(area, space, region, rx, ry, name):
    """: the WORLD point on object `name` nearest the click at region pixel (rx, ry).

    the reference CAD application's object prompts are position-aware -- the reference CAD application documentation: *"Select the first curve
    near the end"* -- so a command needs WHERE its object was picked, not only which.
    A curve is searched on its exact document geometry (sampled in screen space, then
    refined), so the point lies ON the curve and on the segment that was clicked. A
    surface or solid takes the ray hit on its display mesh: a location only, never
    geometry. Returns ``(x, y, z)`` or None.
    """
    rv3d = getattr(space, "region_3d", None) if space is not None else None
    geom = geom_by_name_in_document(name)
    if rv3d is None or region is None or geom is None:
        return None
    import rhino3dm as r3
    if isinstance(geom, r3.Point):
        loc = geom.Location
        return (loc.X, loc.Y, loc.Z)
    if isinstance(geom, r3.Curve):
        t0, t1 = geom.Domain.T0, geom.Domain.T1

        def screen_d2(t):
            p = geom.PointAt(t)
            s = _project_2d(region, rv3d, (p.X, p.Y, p.Z))
            if s is None:
                return float("inf")
            return (s[0] - rx) ** 2 + (s[1] - ry) ** 2

        n = PICK_CURVE_SAMPLES
        ts = [t0 + (t1 - t0) * i / n for i in range(n + 1)]
        best = min(range(len(ts)), key=lambda i: screen_d2(ts[i]))
        lo, hi = ts[max(0, best - 1)], ts[min(n, best + 1)]
        for _ in range(40):              # golden-section on the exact curve
            m1 = hi - (hi - lo) * 0.618
            m2 = lo + (hi - lo) * 0.618
            if screen_d2(m1) < screen_d2(m2):
                hi = m2
            else:
                lo = m1
        p = geom.PointAt(0.5 * (lo + hi))
        return (p.X, p.Y, p.Z)
    try:
        import bpy_extras.view3d_utils as v3u
        origin = v3u.region_2d_to_origin_3d(region, rv3d, (rx, ry))
        direction = v3u.region_2d_to_vector_3d(region, rv3d, (rx, ry))
        depsgraph = bpy.context.evaluated_depsgraph_get()
        ok, loc, _n, _i, ob, _m = bpy.context.scene.ray_cast(depsgraph, origin, direction)
        if ok and ob is not None and ob.name == name:
            return (loc.x, loc.y, loc.z)
    except Exception:  # noqa: BLE001
        pass
    return None


def _answer_objects_step_with_click(area, space, region, rx, ry):
    """: a click that hit an object while a command waits at a single-object step.

    Inferred from the help's wording: single-object prompts are answered by the pick
    itself (the reference CAD application documentation: *"Select the first curve near the end. Select the second curve
    near the end."* -- no Enter between), while set prompts say so (the reference CAD application documentation:
    *"Select the objects and press Enter."*). So an `OBJECTS` step takes the clicked
    object at once, and records WHERE in ``cmd.pick_points[step]``; an `OBJECTS_MULTI`
    step keeps the selection and waits for Enter. Returns the command's message or None.
    """
    cmd = app_state.active_command
    hit = _last_click_hit
    if cmd is None or hit is None or getattr(cmd, "pending_option", None) is not None:
        return None
    steps = getattr(cmd, "steps", None) or []
    idx = getattr(cmd, "step_index", 0)
    if idx >= len(steps) or steps[idx][1] != commands.OBJECTS:
        return None
    where = object_pick_location(area, space, region, rx, ry, hit)
    picks = dict(getattr(cmd, "pick_points", None) or {})
    if where is not None:
        picks[idx] = tuple(float(c) for c in where)
    else:
        picks.pop(idx, None)
    cmd.pick_points = picks
    app_state.history.append("> " + hit)
    before = len(app_state.history)
    app_state.feed_line(hit)
    return app_state.history[-1] if len(app_state.history) > before else None


def _at_selection_step(cmd):
    """True while `cmd` is waiting at an object-selection step (single or set)."""
    steps = getattr(cmd, "steps", None) or []
    idx = getattr(cmd, "step_index", 0)
    return (idx < len(steps) and steps[idx][1] in commands.SELECTION_KINDS
            and getattr(cmd, "pending_option", None) is None)


def _screen_segment_crosses_rect(a, b, rect):
    """Whether 2-D segment ``a``-``b`` touches or enters ``rect`` (inclusive)."""
    x0, y0, x1, y1 = rect
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    lo, hi = 0.0, 1.0
    # Liang-Barsky clipping against left/right/bottom/top. It covers the important
    # #73 case a bbox cannot: a rectangle inside a closed curve's EMPTY interior.
    for p, q in ((-dx, ax - x0), (dx, x1 - ax),
                 (-dy, ay - y0), (dy, y1 - ay)):
        if p == 0:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            lo = max(lo, t)
        else:
            hi = min(hi, t)
        if lo > hi:
            return False
    return True


# Selection-release projection cache. The release path is deliberately separate from
# the curve draw cache: it stores screen-space data for one complete view/document state,
# while the draw cache stores world-space line records. Keeping the key explicit matters:
# a stale projection changes both window/crossing semantics and the #73 empty-interior
# refinement.
_SELECTION_PROJECTION_MAX = 8
_selection_projection_cache = {"entries": {}, "doc": None}


def _selection_projection_key(region, rv3d):
    """Return the complete state key for selection projection data."""
    try:
        matrix = tuple(float(v) for row in rv3d.perspective_matrix for v in row)
        dimensions = (int(region.width), int(region.height))
    except Exception:  # noqa: BLE001
        return None
    model = getattr(app_state, "model", None)
    file3dm = getattr(model, "file", None)
    try:
        layers = tuple((bool(layer.Visible), bool(layer.Locked)) for layer in file3dm.Layers)
    except Exception:  # noqa: BLE001
        layers = ()
    # Visibility/selectability changes are versioned by the depsgraph/display listener;
    # never walk bpy.data.objects here. `_unpickable_names_cached` is itself keyed by
    # the document/layer state, so including both its identity and contents preserves
    # explicit invalidation without making every projection key O(number of objects).
    unpickable = _unpickable_names_cached()
    locked = (id(unpickable), tuple(sorted(unpickable)))
    doc = _doc_key()
    dg = _curve_dg
    cache = _selection_projection_cache
    # A model replacement gets a clean cache immediately, rather than waiting for the
    # bounded LRU-like map to evict old document entries.
    if cache["doc"] != doc:
        cache["entries"].clear()
        cache["doc"] = doc
    return (doc, layers, locked, (dg["vis"], dg["scene"]), dimensions, matrix)


def _selection_projection_entry(region, rv3d):
    key = _selection_projection_key(region, rv3d)
    if key is None:
        return None, None
    entries = _selection_projection_cache["entries"]
    entry = entries.get(key)
    if entry is None:
        if len(entries) >= _SELECTION_PROJECTION_MAX:
            entries.pop(next(iter(entries)))
        entry = {"boxes": None, "segments": {}, "segment_indexes": {}}
        entries[key] = entry
    return key, entry


def _projected_selection_segments(name, region, rv3d, entry):
    """Get one curve's projected segments, computing each endpoint at most once per view."""
    cached = entry["segments"].get(name)
    if cached is not None:
        return cached
    ob = bpy.data.objects.get(name)
    if ob is None or not bridge.is_app_drawn(ob):
        entry["segments"][name] = ()
        return ()
    rec = _curve_record(ob)
    if rec is None or not rec.get("n"):
        entry["segments"][name] = ()
        return ()
    result = native_selection.project_segments(
        rec["lines"], rv3d.perspective_matrix, region.width, region.height)
    if result is None:
        projected = []
        lines = rec["lines"]
        segments = (lines.reshape(-1, 2, 3)
                    if getattr(lines, "ndim", 0) == 2 else lines)
        for segment in segments:
            a = _project_2d(region, rv3d, segment[0])
            b = _project_2d(region, rv3d, segment[1])
            if a is not None and b is not None:
                projected.append((a, b))
        result = tuple(projected)
    entry["segments"][name] = result
    entry["segment_indexes"][name] = _build_selection_segment_index(
        result, region.width, region.height)
    return result


def _build_selection_segment_index(segments, width, height):
    """Build a conservative screen-space index for one curve's projected segments.

    The index is only a candidate filter: callers still run the exact inclusive
    Liang--Barsky predicate on every returned segment.  The overflow bucket keeps
    long spans bounded in memory and makes the index conservative for segments
    covering much of the viewport.  Coordinates are not clipped, so offscreen
    endpoints remain queryable.
    """
    import math

    # A stable pixel-sized grid avoids making the number of bins depend on curve
    # count. At most 16 x 16 ordinary cells cover a viewport-sized rectangle;
    # larger rectangles naturally visit more cells and remain exact.
    span = max(float(width), float(height), 1.0)
    cell = max(32.0, min(128.0, span / 16.0))
    bins = {}
    overflow = []
    max_cells = 256
    for index, (a, b) in enumerate(segments):
        lo_x, hi_x = min(a[0], b[0]), max(a[0], b[0])
        lo_y, hi_y = min(a[1], b[1]), max(a[1], b[1])
        bx0, bx1 = math.floor(lo_x / cell), math.floor(hi_x / cell)
        by0, by1 = math.floor(lo_y / cell), math.floor(hi_y / cell)
        count = (bx1 - bx0 + 1) * (by1 - by0 + 1)
        if count > max_cells:
            overflow.append(index)
            continue
        for bx in range(bx0, bx1 + 1):
            for by in range(by0, by1 + 1):
                bins.setdefault((bx, by), []).append(index)
    return {"cell": cell, "bins": bins, "overflow": tuple(overflow),
            "count": len(segments)}


def _indexed_selection_segment_numbers(index, rect):
    """Return conservative segment numbers whose screen boxes meet ``rect``."""
    import math

    if index is None:
        return ()
    x0, y0, x1, y1 = rect
    cell = index["cell"]
    bx0, bx1 = math.floor(x0 / cell), math.floor(x1 / cell)
    by0, by1 = math.floor(y0 / cell), math.floor(y1 / cell)
    found = set(index["overflow"])
    for bx in range(bx0, bx1 + 1):
        for by in range(by0, by1 + 1):
            found.update(index["bins"].get((bx, by), ()))
    return tuple(sorted(found))


def _curve_crosses_selection_rect(name, region, rv3d, rect, projection=None):
    """True unless ``name`` is a known curve whose drawn stroke misses ``rect``.

    ``projection`` is acquired once by the drag-release path and shared with every
    candidate. Rebuilding its key here per hit scans all objects and turns crossing
    selection quadratic on large drawings.
    """
    ob = bpy.data.objects.get(name)
    if ob is None or not bridge.is_app_drawn(ob):
        return True                         # points/surfaces keep their bbox behavior
    entry = projection
    if entry is None:
        _key, entry = _selection_projection_entry(region, rv3d)
    if entry is None:
        return True                         # unavailable evidence must not hide an object
    if name not in entry["segments"]:
        rec = _curve_record(ob)
        if rec is None or not rec.get("n"):
            return True
        native_result = native_selection.projected_segments_rect_query(
            rec["lines"], rv3d.perspective_matrix,
            region.width, region.height, rect)
        if native_result == native_selection.RECT_HIT:
            return True
        if native_result == native_selection.RECT_MISS:
            return False
        if native_result == native_selection.RECT_NO_EVIDENCE:
            return True
    segments = _projected_selection_segments(name, region, rv3d, entry)
    if not segments:
        return True
    index = entry.get("segment_indexes", {}).get(name)
    numbers = (range(len(segments)) if index is None
               else _indexed_selection_segment_numbers(index, rect))
    return any(_screen_segment_crosses_rect(segments[i][0], segments[i][1], rect)
               for i in numbers)


def _curves_cross_selection_rect(names, region, rv3d, rect, projection=None):
    """Filter broad-phase ``names`` with one native query when possible.

    Result order is unchanged. Non-curves and unavailable evidence remain
    conservative, existing Python screen caches stay authoritative, and any batch
    failure reruns the established per-curve path for the complete input.
    """
    names = list(names)
    if not names:
        return []
    entry = projection
    if entry is None:
        _key, entry = _selection_projection_entry(region, rv3d)
    if entry is None:
        return [name for name in names
                if _curve_crosses_selection_rect(name, region, rv3d, rect, entry)]
    decisions = {}
    batch_names = []
    batch_lines = []
    for name in names:
        ob = bpy.data.objects.get(name)
        if ob is None or not bridge.is_app_drawn(ob):
            decisions[name] = True
            continue
        if name in entry["segments"]:
            decisions[name] = _curve_crosses_selection_rect(
                name, region, rv3d, rect, entry)
            continue
        rec = _curve_record(ob)
        if rec is None or not rec.get("n"):
            decisions[name] = True
            continue
        batch_names.append(name)
        batch_lines.append(rec["lines"])
    if batch_names:
        statuses = native_selection.projected_curves_rect_query(
            batch_lines, rv3d.perspective_matrix,
            region.width, region.height, rect)
        if statuses is None or len(statuses) != len(batch_names):
            return [name for name in names
                    if _curve_crosses_selection_rect(
                        name, region, rv3d, rect, entry)]
        for name, status in zip(batch_names, statuses):
            decisions[name] = status != native_selection.RECT_MISS
    return [name for name in names if decisions.get(name, True)]


def _rectangle_selection_hits(candidates, rect, mode, curve_crosses=None,
                              curve_crosses_many=None, expand_groups=None):
    """Rectangle hits refined by actual curve strokes and document groups."""
    hits = select.in_rect(candidates, rect, mode)
    if mode == select.CROSSING and curve_crosses_many is not None:
        hits = list(curve_crosses_many(hits))
    elif mode == select.CROSSING and curve_crosses is not None:
        hits = [name for name in hits if curve_crosses(name)]
    if hits and expand_groups is not None:
        hits = list(expand_groups(hits))
    return hits


def _project_many_2d(region, rv3d, points):
    """Project world ``points`` in one batch, returning ``None`` for hidden points.

    This is the NumPy equivalent of Blender's ``location_3d_to_region_2d`` for the
    projection used by selection: homogeneous matrix multiplication, reject points
    with non-positive ``w``, then map normalized device coordinates to region pixels.
    The scalar path is intentionally retained for environments without NumPy or a
    usable 4x4 matrix.
    """
    points = list(points)
    if not points:
        return []
    try:
        import numpy as np
        matrix = np.asarray(rv3d.perspective_matrix, dtype=float)
        coords = np.asarray(points, dtype=float)
        if matrix.shape != (4, 4) or coords.ndim != 2 or coords.shape[1] != 3:
            raise ValueError("invalid projection inputs")
        if not np.isfinite(matrix).all() or not np.isfinite(coords).all():
            raise ValueError("non-finite projection inputs")
        homogeneous = np.concatenate(
            (coords, np.ones((coords.shape[0], 1), dtype=float)), axis=1)
        clip = homogeneous @ matrix.T
        w = clip[:, 3]
        good = np.isfinite(clip).all(axis=1) & (w > 0.0)
        out = [None] * len(points)
        if np.any(good):
            ndc = clip[good, :2] / w[good, None]
            width, height = float(region.width), float(region.height)
            pixels = np.column_stack(((ndc[:, 0] + 1.0) * width * 0.5,
                                      (ndc[:, 1] + 1.0) * height * 0.5))
            for index, pixel in zip(np.flatnonzero(good), pixels):
                if np.isfinite(pixel).all():
                    out[int(index)] = (float(pixel[0]), float(pixel[1]))
        return out
    except Exception:  # noqa: BLE001
        return [_project_2d(region, rv3d, point) for point in points]


def _selection_world_corners(ob, rec=None):
    """World corners for a cached curve record or an ordinary Blender object."""
    from mathutils import Vector
    if rec is not None and rec.get("n"):
        lo_, hi_ = rec["lo"], rec["hi"]
        return [(hi_[0] if i & 4 else lo_[0],
                 hi_[1] if i & 2 else lo_[1],
                 hi_[2] if i & 1 else lo_[2]) for i in range(8)]
    return [tuple(ob.matrix_world @ Vector(c)) for c in bridge.local_box_corners(ob)]


def _curve_selection_candidates(point_at, locked):
    """Return ordered cached curve candidates, or ``None`` if the cache is unusable.

    The collection key set is the app-drawn membership index.  In particular, do not
    ask each of the thousands of curve objects for ``users_collection`` here: that
    property synchronizes Blender's collection users and was the #76 cold-release
    bottleneck.  ``pick_*`` are the folded world boxes; dirty names use their current
    records instead.  The caller supplies Blender object order so ties retain the old
    document order.
    """
    import bpy
    st = _curve_pass
    coll = bpy.data.collections.get(bridge.CURVE_COLLECTION)
    if coll is None:
        return None
    try:
        object_refs = st.get("objects") or {}
        # A first selection can race the first completed curve fold. Seed resolved
        # references with ONE collection iteration rather than falling back to 5,702
        # per-object `users_collection` classifications.
        if not object_refs:
            object_refs = {ob.name: ob for ob in coll.objects}
        app_names = set(object_refs)
        names = list(st["pick_names"])
        los, his = st["pick_lo"], st["pick_hi"]
        if len(names) != len(los) or len(names) != len(his) or len(set(names)) != len(names):
            return None
        hidden = set(st["hidden"])
        dirty = set(st["dirty"])
        if not set(names).issubset(app_names) or not dirty.issubset(app_names):
            return None
        bounds = {n: (los[i], his[i]) for i, n in enumerate(names)}
        if not set(names).issubset(object_refs):
            # Fold metadata may precede the object-reference addition after reload.
            # One direct collection walk repairs that state without querying each
            # object's collection users.
            object_refs = {ob.name: ob for ob in coll.objects}
            app_names = set(object_refs)
        if not set(names).issubset(object_refs):
            return None
        candidates = {}
        for name in names:
            if name in hidden or name in locked or name in point_at or name in dirty:
                continue
            ob = object_refs.get(name)
            if ob is None or ob.name != name:
                return None
            rec = st["recs"].get(name)
            if rec is None or not rec.get("n"):
                lo_, hi_ = bounds[name]
                rec = {"lo": lo_, "hi": hi_, "n": 1}
            candidates[name] = (ob, rec)
        for name in dirty - hidden - locked - set(point_at):
            ob = object_refs.get(name)
            rec = st["recs"].get(name)
            if ob is None or rec is None or not rec.get("n"):
                return None
            candidates[name] = (ob, rec)
        # Object keys preserve Blender's document insertion order without walking
        # app-drawn objects; only the ~200 non-curve objects are fetched below.
        ordered = []
        for name in bpy.data.objects.keys():
            if name in candidates:
                ordered.append((name, candidates[name][0], candidates[name][1]))
        if set(n for n, _ob, _rec in ordered) != set(candidates):
            return None
        return ordered, app_names
    except Exception:  # noqa: BLE001
        return None


def _native_selection_screen_boxes(region, rv3d, points, slices):
    """Ordered ``(name, box)`` rows, or ``None`` for the established NumPy fallback."""
    try:
        if not native_selection.available():
            return None
        import numpy as np

        matrix = np.asarray(rv3d.perspective_matrix, dtype=np.float64)
        coordinates = np.asarray(points, dtype=np.float64)
        if (matrix.shape != (4, 4) or coordinates.ndim != 2
                or coordinates.shape[1:] != (3,)
                or not np.isfinite(matrix).all()
                or not np.isfinite(coordinates).all()):
            return None
        offsets = [0]
        flags = []
        expected_start = 0
        for _name, start, end, is_point in slices:
            if start != expected_start or end < start or end > len(coordinates):
                return None
            offsets.append(end)
            flags.append(int(bool(is_point)))
            expected_start = end
        if expected_start != len(coordinates):
            return None
        packed_offsets = np.ascontiguousarray(offsets, dtype=np.uint64)
        packed_flags = np.ascontiguousarray(flags, dtype=np.uint8)
        reduced = native_selection.screen_boxes_from_world(
            coordinates,
            matrix,
            packed_offsets,
            packed_flags,
            region.width,
            region.height,
        )
        if reduced is None:
            homogeneous = np.concatenate(
                (coordinates, np.ones((len(coordinates), 1), dtype=np.float64)), axis=1)
            clip = homogeneous @ matrix.T
            reduced = native_selection.screen_boxes_from_clip(
                clip,
                packed_offsets,
                packed_flags,
                region.width,
                region.height,
            )
        if reduced is None:
            return None
        boxes, valid = reduced
        return [(slices[index][0], tuple(float(value) for value in boxes[index]))
                for index in range(len(slices)) if valid[index]]
    except Exception:  # noqa: BLE001 -- optional reduction never breaks selection
        return None


def _object_screen_bboxes(area, space, region, projection=None):
    """``(name, (x0, y0, x1, y1))`` in region pixels, for every visible object.

    Curve-pass arrays provide the fast candidate path.  If they are absent or fail
    consistency checks, the conservative object walk below remains authoritative.
    """
    import bpy
    from mathutils import Vector

    rv3d = getattr(space, "region_3d", None) if space is not None else None
    if rv3d is None or region is None:
        return []
    if projection is None:
        _key, projection = _selection_projection_entry(region, rv3d)
    if projection is not None and projection["boxes"] is not None:
        return list(projection["boxes"])
    out, locked = [], _unpickable_names_cached()
    point_locations = _document_point_locations()
    point_at = {name: world for name, world in point_locations if name}
    pending, slices = [], []
    fast = _curve_selection_candidates(point_at, locked)
    if fast is not None:
        curve_candidates, app_names = fast
        for name, ob, rec in curve_candidates:
            # Membership in the synchronized curve collection plus `hidden` above is
            # the visibility test for app-drawn objects; calling display_visible here
            # would re-enter Blender's per-object collection-user synchronization.
            start = len(pending)
            pending.extend(_selection_world_corners(ob, rec))
            slices.append((name, start, len(pending), False))
        # Points and ordinary display objects retain the old visibility and local-box
        # semantics. App-drawn membership is already known from collection keys.
        for name in bpy.data.objects.keys():
            if name in app_names:
                continue
            ob = bpy.data.objects.get(name)
            if ob is None or name in locked or not bridge.display_visible(ob):
                continue
            if name in point_at:
                start = len(pending)
                pending.append(point_at[name])
                slices.append((name, start, start + 1, True))
                continue
            start = len(pending)
            corners = [tuple(ob.matrix_world @ Vector(c))
                       for c in bridge.local_box_corners(ob)]
            pending.extend(tuple(float(v) for v in corner) for corner in corners)
            slices.append((name, start, len(pending), False))
    else:
        # Conservative fallback: preserve the pre-cache path exactly.
        for ob in bpy.data.objects:
            if ob.name in locked or not bridge.display_visible(ob):
                continue
            if ob.name in point_at:
                start = len(pending)
                pending.append(point_at[ob.name])
                slices.append((ob.name, start, start + 1, True))
                continue
            rec = _curve_record(ob) if bridge.is_app_drawn(ob) else None
            start = len(pending)
            corners = _selection_world_corners(ob, rec)
            pending.extend(tuple(float(v) for v in corner) for corner in corners)
            slices.append((ob.name, start, len(pending), False))
    native_boxes = _native_selection_screen_boxes(region, rv3d, pending, slices)
    if native_boxes is not None:
        out.extend(native_boxes)
    else:
        projected = _project_many_2d(region, rv3d, pending)
        for name, start, end, is_point in slices:
            visible = [pos for pos in projected[start:end] if pos is not None]
            if not visible:
                continue
            xs, ys = [pos[0] for pos in visible], [pos[1] for pos in visible]
            box = ((xs[0], ys[0], xs[0], ys[0]) if is_point
                   else (min(xs), min(ys), max(xs), max(ys)))
            out.append((name, box))
    if projection is not None:
        projection["boxes"] = tuple(out)
    return out


#: A held Trim click. The initial press still takes the normal point-pick route;
#: subsequent mouse movement sweeps the existing Rust-accelerated curve picker.
_trim_drag = None
TRIM_DRAG_SAMPLE_PX = max(1.0, CURVE_PICK_PX * 0.5)

#: An in-progress selection drag, or None. A click and a window selection start
# identically -- you cannot tell them apart until the button comes up -- so the
# decision is deferred to RELEASE, which is also how the reference CAD application must do it.
_sel_drag = None

#: Rubber-band colours. **application-defined.** the reference CAD application names the settings
#: (`SelectionWindowStrokeColor` / `SelectionWindowFillColor`, and the
#: `...Crossing...` pair, in `Tools > Options > Advanced`) but no file we hold
#: records their values. Window and crossing are deliberately DIFFERENT colours,
#: because the reference CAD application gives them separate settings and the whole point is to see which
#: one you are making.
WINDOW_RECT_COLOR = (0.20, 0.45, 0.95, 1.0)
CROSSING_RECT_COLOR = (0.15, 0.70, 0.30, 1.0)
#: Selection rectangles use both fill and a one-pixel stroke -- `SelectionWindowFillColor` /
#: `SelectionWindowCrossingFillColor`. The fill is the stroke's hue, translucent; the
#: values (alpha, width) are application-defined.
SELECTION_RECT_FILL_ALPHA = 0.16
SELECTION_RECT_STROKE_PX = 2.0


def selection_dragging():
    """True while a selection rectangle is being dragged."""
    return _sel_drag is not None and _sel_drag.get("active", False)


def selection_drag_rect():
    """``(pane_key, rect, mode)`` for the live rectangle, or None."""
    st = _sel_drag
    if st is None or not st.get("active"):
        return None
    x0, y0 = st["start"]
    x1, y1 = st["now"]
    return (st["pane"], select.normalise_rect(x0, y0, x1, y1),
            select.drag_mode(x0, x1))


def _trim_drag_command(cmd):
    """True only while Trim is waiting for curve/surface parts to remove."""
    return isinstance(cmd, commands.TrimCmd) and getattr(cmd, "step_index", -1) == 1


def _trim_drag_begin(area, space, region, rx, ry, latched=False):
    """Keep the already-processed Trim press alive for drag-over curve picks."""
    global _trim_drag

    _trim_drag = {
        "pane": _pane_key(area), "start": (float(rx), float(ry)),
        "last": (float(rx), float(ry)), "active": False,
        "latched": bool(latched), "trimmed": 0,
    }
    return True


def _trim_drag_update(mouse_x, mouse_y):
    """Feed each newly crossed drawn curve part to the running Trim command."""
    global _trim_drag
    import math

    st = _trim_drag
    if st is None:
        return False
    if not _trim_drag_command(app_state.active_command):
        _trim_drag = None
        return False
    area, space, region = _area_by_pane_key(st["pane"])
    if region is None or space is None:
        return False
    now = (float(mouse_x - region.x), float(mouse_y - region.y))
    if not st["active"]:
        x0, y0 = st["start"]
        if select.is_click(x0, y0, now[0], now[1]):
            return False
        st["active"] = True
    x0, y0 = st["last"]
    dx, dy = now[0] - x0, now[1] - y0
    steps = max(1, int(math.ceil(math.hypot(dx, dy) / TRIM_DRAG_SAMPLE_PX)))
    changed = False
    for index in range(1, steps + 1):
        rx = x0 + dx * index / steps
        ry = y0 + dy * index / steps
        hit = pick_curve_object(area, space, region, rx, ry)
        if hit is None:
            st["latched"] = False
            continue
        if st["latched"]:
            continue
        st["latched"] = True
        cmd = app_state.active_command
        if not _trim_drag_command(cmd):
            _trim_drag = None
            return changed
        point = object_pick_location(area, space, region, rx, ry, hit)
        if point is None:
            continue
        app_state.history.append("> " + coords.format_world(point))
        app_state.feed_line(coords.format_world(point))
        st["trimmed"] += 1
        changed = True
        if not _trim_drag_command(app_state.active_command):
            _trim_drag = None
            return changed
    st["last"] = now
    return changed


def _trim_drag_end(mouse_x=None, mouse_y=None):
    """Finish a held Trim gesture; a click was already handled on press."""
    global _trim_drag

    st = _trim_drag
    if st is None:
        return 0
    if mouse_x is not None and mouse_y is not None and st.get("active"):
        _trim_drag_update(mouse_x, mouse_y)
    trimmed = int(st.get("trimmed", 0))
    _trim_drag = None
    return trimmed


def _sel_drag_begin(area, region, rx, ry, shift=False, ctrl=False):
    """Record the press. Always succeeds -- it may turn out to be a click."""
    global _sel_drag

    _sel_drag = {"start": (rx, ry), "now": (rx, ry), "pane": _pane_key(area),
                 "shift": shift, "ctrl": ctrl, "active": False}
    return True


def _sel_drag_update(mouse_x, mouse_y):
    """Track the cursor. Returns True once this has become a real drag."""
    st = _sel_drag
    if st is None:
        return False
    _area, _space, region = _area_by_pane_key(st["pane"])
    if region is None:
        return False
    st["now"] = (mouse_x - region.x, mouse_y - region.y)
    x0, y0 = st["start"]
    x1, y1 = st["now"]
    # a wobble is still a click; only a real move is a rectangle
    st["active"] = not select.is_click(x0, y0, x1, y1)
    return st["active"]


def _control_points_in_rect(region, rv3d, rect):
    """Visible grip keys whose projected markers lie in ``rect``.

    Control points are fixed-size screen markers, not Blender objects, so the object
    bounding-box rectangle path cannot see them. A point has no extent: window and
    crossing therefore agree on whether its centre is inside the rectangle.
    """
    rows = _cp_rows()
    if rows:
        try:
            indices = None
            if native_selection.available():
                indices = native_selection.projected_points_in_rect(
                    _packed_control_points(rows), rv3d.perspective_matrix,
                    region.width, region.height, rect)
            if indices is not None:
                return [(rows[index][0], rows[index][1]) for index in indices]
        except Exception:  # noqa: BLE001 -- any native/UI issue keeps scalar selection
            pass
    x0, y0, x1, y1 = rect
    hits = []
    for name, index, world in _control_point_locations():
        pos = _project_2d(region, rv3d, world)
        if pos is not None and x0 <= pos[0] <= x1 and y0 <= pos[1] <= y1:
            hits.append((name, index))
    return hits


def _select_control_points_in_rect(region, rv3d, rect, shift=False, ctrl=False):
    """Apply a window/crossing rectangle to visible grips using the reference CAD application modifiers."""
    hits = _control_points_in_rect(region, rv3d, rect)
    action = select.modifier_action(shift=shift, ctrl=ctrl)
    wanted = select.apply(sorted(_selected_cps), hits, action)
    _selected_cps.clear()
    _selected_cps.update(wanted)
    _tag_redraw_viewports()
    return sorted(_selected_cps)


def _sel_drag_end():
    """Resolve the press: a click, or a window/crossing selection.

    Returns ``(selection, mode)`` where mode is None for a click. When grips are visible,
    a real rectangle selects those grips; a mere click keeps the existing object/click
    path so clicking another curve still works normally.
    """
    global _sel_drag

    st = _sel_drag
    _sel_drag = None
    if st is None:
        return None, None
    area, space, region = _area_by_pane_key(st["pane"])
    if region is None or space is None:
        return None, None
    x0, y0 = st["start"]
    x1, y1 = st["now"]
    shift, ctrl = st["shift"], st["ctrl"]

    if not st.get("active") or select.is_click(x0, y0, x1, y1):
        wanted = narwhal_click_select(area, space, region, x0, y0,
                                    shift=shift, ctrl=ctrl)
        if app_state.active_command is not None and not (shift or ctrl):
            _answer_objects_step_with_click(area, space, region, x0, y0)
        return wanted, None

    mode = select.drag_mode(x0, x1)
    rect = select.normalise_rect(x0, y0, x1, y1)
    rv3d = getattr(space, "region_3d", None)
    # Visible grips own a selection rectangle. This is deliberately decided only after
    # the gesture became a drag: an ordinary click while one curve is selected must still
    # be able to select another object rather than being trapped in grip mode.
    if rv3d is not None and effective_points_on():
        wanted = _select_control_points_in_rect(region, rv3d, rect,
                                                 shift=shift, ctrl=ctrl)
        return wanted, mode
    _projection_key, projection = _selection_projection_entry(region, rv3d)
    candidates = _object_screen_bboxes(area, space, region, projection)
    hits = _rectangle_selection_hits(
        candidates, rect, mode,
        curve_crosses=lambda name: _curve_crosses_selection_rect(
            name, region, rv3d, rect, projection),
        curve_crosses_many=lambda names: _curves_cross_selection_rect(
            names, region, rv3d, rect, projection),
        # A rectangle that catches one member catches the same transitive group unit
        # as a click. The old path skipped this expansion and visibly desynced groups.
        expand_groups=app_state.ctx.expand_group_names)
    action = select.modifier_action(shift=shift, ctrl=ctrl)
    wanted = select.apply(selection_names(), hits, action)
    apply_selection(wanted, already_expanded=True)
    _tag_redraw_viewports()
    return wanted, mode


def _gumball_size(region, rv3d, origin):
    """The widget's size in WORLD units, so it looks constant on screen.

    the reference CAD application's gumball keeps its pixel size as you zoom, so the documented pixel size
    is converted at this pane's zoom -- the same conversion the snap radius uses.
    """
    ppu = _pixels_per_unit(region, rv3d, origin)
    if not ppu or ppu <= 0:
        return None
    return gumball.DEFAULT_SIZE_PX / ppu


def _project_2d(region, rv3d, point):
    """A world point -> region pixels, or None if it will not project."""
    try:
        import bpy_extras.view3d_utils as v3u
        from mathutils import Vector

        co = v3u.location_3d_to_region_2d(region, rv3d, Vector(point))
    except Exception:  # noqa: BLE001
        return None
    if co is None:
        return None
    return (co.x, co.y)


def _view_direction(rv3d):
    """The pane's view direction in world space, or None.

    Blender's `view_rotation` aims the camera; the camera looks along its own
    local -Z, so rotating (0, 0, -1) by that quaternion gives where the pane is
    looking. Needed by the Gumball's Move-2D handles, which the reference CAD application hides unless
    the plane is close to face-on (gumball.plane_visible).
    """
    if rv3d is None:
        return None
    try:
        from mathutils import Vector

        v = rv3d.view_rotation @ Vector((0.0, 0.0, -1.0))
        return (v.x, v.y, v.z)
    except Exception:  # noqa: BLE001
        return None


def _gumball_handles(area, space, region):
    """``((origin, plane, size), handles)`` for one pane, or ``(None, [])``."""
    frame = gumball_frame()
    if frame is None or region is None or space is None:
        return None, []
    origin, plane = frame
    rv3d = getattr(space, "region_3d", None)
    if rv3d is None:
        return None, []
    size = _gumball_size(region, rv3d, origin)
    if size is None:
        return None, []
    # The view direction decides which Move-2D plane handles are face-on enough to show.
    return (origin, plane, size), gumball.build(
        origin, plane, size, view_dir=_view_direction(rv3d),
        extrude=_extrude_handle_available())


def _gumball_projected(handles, region, rv3d):
    """Each handle paired with its hit-test geometry in region pixels."""
    out = []
    for h in handles:
        out.append((h, [[_project_2d(region, rv3d, p) for p in pl]
                        for pl in h.hot]))
    return out


def _gumball_hit(area, space, region, mouse_x, mouse_y):
    """The handle under the window-space mouse position, or None."""
    _frame, handles = _gumball_handles(area, space, region)
    if not handles:
        return None
    rv3d = space.region_3d
    return gumball.pick(_gumball_projected(handles, region, rv3d),
                        (mouse_x - region.x, mouse_y - region.y))


def _area_by_pane_key(key):
    """Re-find a pane by its key -> ``(area, space, region)``.

    Area identity is NOT stable across `screen.areas` reads (a documented
    pitfall), so a drag stores the pane KEY and re-finds the area every event
    instead of holding a reference.
    """
    try:
        areas = list(bpy.context.screen.areas)
    except Exception:  # noqa: BLE001
        return None, None, None
    for area in areas:
        if area.type == "VIEW_3D" and _pane_key(area) == key:
            return area, _view3d_space(area), _window_region(area)
    return None, None, None


def _ray_plane_point(space, region, rx, ry, plane_origin, plane_normal):
    """Where the cursor's view ray crosses an arbitrary plane, in world.

    `pick_point` always uses the pane's CPlane; a rotate drag needs the plane the
    ROTATION happens in, which is a different plane, so the ray has to be built
    against that one.
    """
    try:
        import bpy_extras.view3d_utils as v3u

        rv3d = space.region_3d
        o = v3u.region_2d_to_origin_3d(region, rv3d, (rx, ry))
        d = v3u.region_2d_to_vector_3d(region, rv3d, (rx, ry))
    except Exception:  # noqa: BLE001
        return None
    return cplane.ray_plane_intersect((o.x, o.y, o.z), (d.x, d.y, d.z),
                                     plane_origin, plane_normal)


def _gumball_begin(area, space, region, mouse_x, mouse_y, shift=False, ctrl=False):
    """Start a drag if a handle is under the cursor. True if one started.

    `shift`/`ctrl` select what the drag MEANS (gumball.drag_mode): Shift turns a
    Move-2D drag into Scale2D and a scale drag into Scale3D, and Ctrl asks for
    the reference CAD application's Extrude handle, which we report as unimplemented rather than silently
    performing the plain drag.
    """
    global _gumball_drag

    if not _gumball_enabled or area is None or region is None or space is None:
        return False
    frame, handles = _gumball_handles(area, space, region)
    if not handles:
        return False
    origin, plane, size = frame
    rv3d = space.region_3d
    hit = gumball.pick(_gumball_projected(handles, region, rv3d),
                       (mouse_x - region.x, mouse_y - region.y))
    if hit is None:
        return False

    axes = gumball.plane_axes(plane)

    # THE MENU BALL is a click, not a drag: "Click on the circle to show the Gumball
    # menu." Handled before drag_mode, which has nothing to say about it.
    #
    # ARMED for the button's RELEASE rather than opened here. Opening it on the press
    # made it a hold-menu instead of click-to-open; see `_pending_menu`. Returning True still claims
    # the press, so it cannot fall through to a selection click.
    if hit.kind == gumball.MENU:
        _defer_menu(_show_gumball_menu)
        return True

    # what the drag MEANS, from the handle plus the modifiers.
    mode = gumball.drag_mode(hit.kind, shift=shift, ctrl=ctrl)
    if not gumball.mode_is_implemented(mode):
        # A real the reference CAD application mode we have not built. Say so and do NOT start a drag --
        # performing the unmodified drag instead would look like Ctrl worked.
        app_state.log(gumball.mode_not_implemented_message(mode, hit.kind))
        redraw_cmdbar()
        return False
    # Ctrl on a move arrow extrudes -- it needs something to extrude
    ext_targets = None
    if mode == gumball.EXTRUDE_AXIS:
        ext_targets = _extrude_targets()
        if ext_targets is None:
            app_state.log("Gumball extrude: select a curve, a surface or a solid's face")
            redraw_cmdbar()
            return True
    # a solid's face/edge/vertex can be MOVED; anything else says why not
    brep_part = None
    if mode != gumball.EXTRUDE_AXIS and gumball_targets_brep_part():
        why = _brep_part_refusal(mode)
        if why:
            app_state.log(why)
            redraw_cmdbar()
            return True               # the press is claimed: it must not deselect
        brep_part = _selected_brep_parts()[0]
        _bp_obj = _document_object(brep_part[0])
        if _bp_obj is None:
            return False

    state = {
        "kind": hit.kind,
        "mode": mode,
        "axis": hit.axis,
        "key": hit.key,
        "label": hit.label,
        "origin": origin,
        "plane": plane,
        "size": size,
        "pane": _pane_key(area),
        "mouse0": (mouse_x, mouse_y),
        "total": 0.0,
        # Snappy-dragging targets to IGNORE: the objects this drag transforms.
        # They sit directly under the widget, so leaving them in would let the
        # very first snap test grab the drag's own starting geometry. Captured
        # here because the selection can change under a long-running drag.
        # Over-broad for a control-point drag (the object's other points are
        # legitimate targets); narrowing that is not worth a second mechanism.
        "exclude": tuple(selection_names()),
        "brep_part": brep_part,
        "extrude": ext_targets,
        "brep_vec": (0.0, 0.0, 0.0),
        # the ORIGINAL solid: every live rebuild starts from it, never from a candidate
        "brep_geom": _bp_obj.Geometry if brep_part is not None else None,
    }
    if hit.kind in (gumball.MOVE, gumball.SCALE, gumball.EXTRUDE_HANDLE):
        axis = axes[hit.axis]
        # A SCALE drag is measured along the direction its HANDLE lies in, which is
        # the opposite side of the axis from the Move arrow (gumball's measured
        # SCALE_HANDLE_SIGN). Using the arrow's direction for both would invert the
        # scale: dragging the square outward, away from the origin, would shrink the
        # object. `scale_axis_direction` is the one definition both this and
        # `_scale_handle` read.
        drag_dir = (gumball.scale_axis_direction(axis)
                    if hit.kind == gumball.SCALE else axis)
        tip = tuple(origin[i] + drag_dir[i] * size for i in range(3))
        p0 = _project_2d(region, rv3d, origin)
        p1 = _project_2d(region, rv3d, tip)
        if p0 is None or p1 is None:
            return False
        state["axis_dir"] = drag_dir
        state["axis_2d"] = (p0, p1)
        state["axis_len"] = size
        state["last"] = 0.0
        if hit.kind == gumball.SCALE:
            # where the handle sits along the axis IS the reference length the
            # scale ratio is taken against
            state["scale_at"] = size * gumball.PROPORTIONS["scale_at"]
            state["accum"] = 1.0
    elif hit.kind == gumball.ROTATE:
        axis = axes[hit.axis]
        pt = _ray_plane_point(space, region, mouse_x - region.x,
                              mouse_y - region.y, origin, axis)
        if pt is None:
            return False
        state["axis_dir"] = axis
        state["last_world"] = pt
    elif hit.kind == gumball.PLANE:
        # a Move-2D drag. `hit.axis` is the axis the plane is
        # NORMAL to, and the drag is tracked on that very plane through the
        # widget origin, so the cursor and the object stay together.
        normal = axes[hit.axis]
        pt = _ray_plane_point(space, region, mouse_x - region.x,
                              mouse_y - region.y, origin, normal)
        if pt is None:
            return False
        state["axis_dir"] = normal
        state["last_world"] = pt
        # the two axes the plane SPANS -- what Scale2D scales along
        j, k = (hit.axis + 1) % 3, (hit.axis + 2) % 3
        state["plane_axes"] = (axes[j], axes[k])
        if mode == gumball.SCALE_2D:
            # "The Gumball origin is the base point of scaling", so the ratio is
            # taken against how far the grab point already is from the origin,
            # measured IN the plane.
            ref = gumball.plane_delta(normal, origin, pt)
            state["ref_dist"] = (ref[0] ** 2 + ref[1] ** 2 + ref[2] ** 2) ** 0.5
            state["accum"] = 1.0
    else:  # the origin marker -- free movement on the CPlane
        pt = pick_point(area, space, region,
                        mouse_x - region.x, mouse_y - region.y)
        if pt is None:
            return False
        state["last_world"] = pt
    _gumball_drag = state
    # Keep the Gumball display origin synchronized with the dragged object.
    #
    # `gumball_frame` freezes the widget frame for the duration of a drag, and that
    # freeze is load-bearing -- see its docstring: recomputing the bounding-box centre
    # mid-drag feeds the object's own motion back into the origin, and a rotate then
    # chases its own box. So the fix is NOT to unfreeze it. Instead the widget is
    # frozen and then SHIFTED by exactly the translation we have already applied to
    # the document -- a quantity we know exactly, and which no feedback loop reaches.
    #
    # Kept OUT of `state["origin"]` deliberately. That origin is the reference point
    # for the drag maths -- `project_onto_axis` measures a signed distance FROM it and
    # `_ray_plane_point` builds the drag plane THROUGH it -- so advancing it would
    # silently change the accumulation in `_gumball_update`. This is a display-only
    # offset, read in `gumball_frame` and nowhere else.
    #
    # Only the three TRANSLATING modes (MOVE, PLANE, ORIGIN) touch it. Rotate and
    # scale must not: the reference CAD application's gumball origin is the rotation centre, and measured,
    # "The Gumball origin is the base point of scaling" -- both stay put by definition.
    state["widget_shift"] = gumball.ZERO_OFFSET
    note_user_interaction()
    # one drag is ONE undo step. The record opens here and closes in
    # `_gumball_end`, so the stream of incremental transforms `_gumball_update`
    # applies collapses into a single reversible change -- which is exactly what
    # a Gumball drag had no way to be before.
    try:
        app_state.ctx.begin_undo(f"Gumball {hit.label or hit.kind}")
    except Exception:  # noqa: BLE001
        pass
    return True


# ---------------------------------------------------------------------------
# RELOCATING THE WIDGET BY DOUBLE-CLICKING A HANDLE
#
# Verified (the reference CAD application documentation):
#  "Move Gumball -- Double-click on an arrow to relocate Gumball along the
#  direction. Move the mouse and click, or type a distance and press Enter."
#  "Move gumball planar - Double click -- Double-click on a Move 2D handle to
#  relocate Gumball on the plane."
# Both handles use one relocation gesture.
#
# the reference CAD application's gesture is NOT a drag: the double-click arms it, the button comes up, you
# move, and a CLICK commits. That is why this has its own state rather than reusing
# `_gumball_drag`, which lives and dies with the button.
#
# NOT IMPLEMENTED, and said out loud in the prompt: "or type a distance and press
# Enter". Typed entry runs through `commands.GumballNumericCmd`, and `commands.py`
# requires a command with a point-input step. Until that command exists, this path reports
# the available gesture rather than faking typed relocation.
# ---------------------------------------------------------------------------

#: application-defined: the same window as the viewport-title double-click. the reference CAD application's is the system
#: double-click speed, which we cannot read; reusing one constant at least keeps the
#: app internally consistent.
def _gumball_is_double_click(key, now=None):
    """True when this press on handle `key` completes a double-click on it.

    A third rapid press starts a new pair rather than counting as another
    double-click, exactly as `title_click` does -- otherwise click-click-click
    would relocate twice.
    """
    global _last_gumball_click

    if now is None:
        import time

        now = time.monotonic()
    last_t, last_key = _last_gumball_click
    _last_gumball_click = (now, key)
    if key is not None and last_key == key and (now - last_t) <= DOUBLE_CLICK_SECONDS:
        _last_gumball_click = (0.0, None)
        return True
    return False


def gumball_relocating():
    """True while a relocate gesture is armed and waiting for its committing click."""
    return _gumball_relocate is not None


def _gumball_relocate_begin(area, space, region, mouse_x, mouse_y):
    """Arm a relocate gesture if a MOVE arrow or Move-2D handle is under the cursor.

    Returns the message to log, or None when the gesture does not apply -- either
    because no handle is there or because the handle has no documented relocate
    (see `gumball.relocate_drag_delta`).
    """
    global _gumball_relocate

    if not _gumball_enabled or area is None or region is None or space is None:
        return None
    frame, handles = _gumball_handles(area, space, region)
    if not handles:
        return None
    origin, plane, _size = frame
    hit = gumball.pick(_gumball_projected(handles, region, space.region_3d),
                       (mouse_x - region.x, mouse_y - region.y))
    if hit is None or hit.kind not in (gumball.MOVE, gumball.PLANE, gumball.ROTATE):
        return None
    axes = gumball.plane_axes(plane)
    axis_dir = axes[hit.axis]
    if hit.kind == gumball.ROTATE:
        # Double-clicking a rotation arc turns only the Gumball around that axis. The
        # object does not move; the widget's kept orientation (`_gumball_turn`) does.
        start = _ray_plane_point(space, region, mouse_x - region.x,
                                 mouse_y - region.y, origin, axis_dir)
        if start is None:
            return None
        _gumball_relocate = {
            "kind": hit.kind, "axis": hit.axis, "axis_dir": axis_dir,
            "origin": origin, "pane": _pane_key(area), "offset0": _gumball_offset,
            "label": hit.label or hit.kind, "start": start, "plane0": plane,
            "turn0": _gumball_turn, "sel": frozenset(selection_names()),
        }
        return (f"Turn the Gumball on {hit.label or hit.kind}: move the mouse and click "
                "(the object does not move)")
    # The gesture is measured from the widget ORIGIN, not from the cursor: "relocate
    # Gumball along the direction" means the widget travels along the axis, and the
    # projection of the cursor onto that axis is where it goes.
    _gumball_relocate = {
        "kind": hit.kind,
        "axis": hit.axis,
        "axis_dir": axis_dir,
        "origin": origin,
        "pane": _pane_key(area),
        "offset0": _gumball_offset,
        "label": hit.label or hit.kind,
    }
    return (f"Relocate the Gumball on {hit.label or hit.kind}: move the mouse and "
            "click  (typing a distance is not implemented yet)")


def _gumball_relocate_motion(mouse_x, mouse_y):
    """Preview the armed relocation at this cursor position. True if it moved.

    The offset is recomputed from `offset0` every time rather than accumulated, so
    the widget follows the cursor absolutely and the preview cannot drift.
    """
    global _gumball_offset

    global _gumball_turn

    st = _gumball_relocate
    if st is None:
        return False
    area, space, region = _area_by_pane_key(st["pane"])
    if region is None or space is None:
        return False
    if st["kind"] == gumball.ROTATE:
        pt = _ray_plane_point(space, region, mouse_x - region.x, mouse_y - region.y,
                              st["origin"], st["axis_dir"])
        if pt is None:
            return False
        ang = gumball.rotate_angle(st["origin"], st["axis_dir"], st["start"], pt)
        _gumball_turn = {"plane": gumball.rotate_frame(st["plane0"], st["axis_dir"],
                                                        ang),
                         "sel": st["sel"], "origin": st["origin"],
                         "serial": _undo_serial()}
        st["angle"] = ang
        return True
    if st["kind"] == gumball.PLANE:
        cursor = _ray_plane_point(space, region, mouse_x - region.x,
                                  mouse_y - region.y, st["origin"], st["axis_dir"])
    else:
        cursor = pick_point(area, space, region,
                            mouse_x - region.x, mouse_y - region.y)
    if cursor is None:
        return False
    delta = gumball.relocate_drag_delta(st["kind"], st["axis_dir"],
                                        st["origin"], cursor)
    if delta is None:
        return False
    base = st["offset0"]
    want = (base[0] + delta[0], base[1] + delta[1], base[2] + delta[2])
    if want == _gumball_offset:
        return False
    _gumball_offset = want
    return True


def _gumball_relocate_commit():
    """The committing click. Returns the line to log.

    The preview has already been applied to `_gumball_offset`, so committing is just
    letting go of the gesture -- there is nothing to bake. Nothing in the DOCUMENT
    changed, which is why no undo record is opened anywhere in this gesture.
    """
    global _gumball_relocate

    st = _gumball_relocate
    _gumball_relocate = None
    if st is None:
        return None
    if st["kind"] == gumball.ROTATE:
        import math as _m
        return (f"Gumball turned {_m.degrees(st.get('angle', 0.0)):g} deg about "
                f"{'xyz'[st['axis']]}  (Reset Gumball to undo)")
    if not gumball.is_relocated(_gumball_offset):
        return "Gumball is at its default position"
    off = _gumball_offset
    return (f"Gumball relocated to offset "
            f"{off[0]:g},{off[1]:g},{off[2]:g}  (Reset Gumball to undo)")


def _gumball_relocate_cancel():
    """Abandon the gesture and put the widget back where it was. True if armed."""
    global _gumball_relocate, _gumball_offset, _gumball_turn

    st = _gumball_relocate
    _gumball_relocate = None
    if st is None:
        return False
    _gumball_offset = st["offset0"]
    if st["kind"] == gumball.ROTATE:
        _gumball_turn = st.get("turn0")
    return True


def _draw_gumball_menu_into(layout):
    """Draw the Gumball menu's rows into `layout`.

    Factored out of `NARWHAL_MT_gumball.draw` so the status-bar pane's right-click can
    show the SAME menu. Verified: "Right-click the pane to show the Gumball menu" --
    the same menu, so it must be the same code, not a second list that can drift.
    """
    for label, key, implemented in gumball.MENU_ENTRIES:
        text = label
        # show the state of the things that HAVE one, so the menu is a readout
        # as well as a set of actions
        if key == "on":
            text = f"{label}  {'*' if gumball_enabled() else ''}".rstrip()
        elif key == "off":
            text = f"{label}  {'*' if not gumball_enabled() else ''}".rstrip()
        elif key == "snappy":
            text = f"{label}  {'*' if gumball_snappy() else ''}".rstrip()
        elif key == "smooth":
            text = f"{label}  {'*' if not gumball_snappy() else ''}".rstrip()
        elif key.startswith("align_"):
            want = key.split("_", 1)[1]
            if gumball_alignment().lower() == want:
                text = f"{label}  *"
        elif key == "drag_strength":
            text = f"{label} ({gumball_drag_strength():g}%)"
        elif key == "relocate" and gumball.is_relocated(gumball_offset()):
            text = f"{label}  *"
        elif key == "autoreset":
            text = f"{label}  {'*' if gumball_autoreset() else ''}".rstrip()
        op = layout.operator("narwhal.gumball_menu", text=text)
        op.entry = key
        if key in ("reset", "align_view", "smooth", "drag_strength"):
            layout.separator()


class NARWHAL_MT_gumball(bpy.types.Menu):
    """The Gumball menu -- the reference CAD application's "menu ball" / bunny tail popup.

    Every entry in `gumball.MENU_ENTRIES` is drawn, in the help's own order. The five
    we have not built are drawn too and report themselves, for the same reason the
    main menu bar does it: a menu that hides what the reference CAD application offers misrepresents the
    widget.
    """
    bl_idname = "NARWHAL_MT_gumball"
    bl_label = "Gumball"

    def draw(self, context):
        _draw_gumball_menu_into(self.layout)


class NARWHAL_OT_gumball_menu(Operator):
    """One entry of the Gumball menu."""
    bl_idname = "narwhal.gumball_menu"
    bl_label = "Gumball Menu"
    bl_options = {"INTERNAL"}

    entry: StringProperty(default="")

    def execute(self, context):
        message = _gumball_menu_action(self.entry)
        if message:
            app_state.log(message)
            redraw_cmdbar()
        _tag_redraw_viewports()
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# THE THREE VIEWPORT CONTEXT MENUS
#
# Verified: "One of the three context menus appears when you click, hold, and
# release the right mouse button in a viewport" (the reference CAD application documentation).
# Which one, and what is IN it, is decided in `navigate.py` -- see the long
# compatibility tables there.
#
# Three classes rather than one parameterised menu, because `bpy.ops.wm.call_menu`
# takes a class NAME and Blender gives a Menu's draw no arguments -- so the only way
# to say WHICH menu is to have three. They are pre-registered with everything else:
# `register_class` inside a draw callback does not take (measured; it is what left
# the toolbar strip blank).
# ---------------------------------------------------------------------------

def _draw_context_menu(layout, kind):
    """Draw one context menu's rows. Shared by the three Menu classes.

    Every row either RUNS its command or reports itself -- we can run 2 of the 14.
    Listing only the two would make this a different program's context menu, and
    greying the rest says nothing, so they say what they are (Rule 4, and the same
    decision the menu bar's 673 items already record).
    """
    items = nav.context_menu_items(kind, last_command=app_state.last_command)
    for item in items:
        if item is nav.SEPARATOR:
            layout.separator()
            continue
        text = nav.display_label(item.label)
        if item.action == nav.ACTION_REPEAT:
            # "Click the right mouse button" accepts command-line input, and with an
            # empty buffer that repeats the last command -- the same operator the
            # quick right-click uses, so the menu row and the gesture cannot drift.
            layout.operator("narwhal.repeat_last", text=text)
            continue
        if item.action == nav.ACTION_CUSTOMISE:
            # "! _OptionsPage _ViewportContextMenu". We have no Options dialog.
            op = layout.operator("narwhal.menu_todo", text=text)
            op.label = text
            op.command = "OptionsPage"
            continue
        if item.command and item.command.lower() in COMMANDS:
            op = layout.operator("narwhal.run_command", text=text)
            op.command = item.command
        else:
            op = layout.operator("narwhal.menu_todo", text=text)
            op.label = text
            op.command = item.command or ""


class NARWHAL_MT_context_viewport(bpy.types.Menu):
    """the reference CAD application's Viewport context menu -- raised when nothing is selected."""
    bl_idname = "NARWHAL_MT_context_viewport"
    bl_label = "Viewport"

    def draw(self, context):
        _draw_context_menu(self.layout, nav.MENU_VIEWPORT)


class NARWHAL_MT_context_object(bpy.types.Menu):
    """the reference CAD application's Object context menu -- raised when objects are selected."""
    bl_idname = "NARWHAL_MT_context_object"
    bl_label = "Object"

    def draw(self, context):
        _draw_context_menu(self.layout, nav.MENU_OBJECT)


class NARWHAL_MT_context_control_point(bpy.types.Menu):
    """the reference CAD application's Control Point context menu -- raised when grips are selected."""
    bl_idname = "NARWHAL_MT_context_control_point"
    bl_label = "Control Point"

    def draw(self, context):
        _draw_context_menu(self.layout, nav.MENU_CONTROL_POINT)


#: menu kind -> the registered class name `wm.call_menu` needs.
CONTEXT_MENU_CLASSES = {
    nav.MENU_VIEWPORT: "NARWHAL_MT_context_viewport",
    nav.MENU_OBJECT: "NARWHAL_MT_context_object",
    nav.MENU_CONTROL_POINT: "NARWHAL_MT_context_control_point",
}


def _draw_tab_menu(layout, entries):
    """Draw a viewport-tab menu from a `views` table of ``(label, command)``.

    We have none of the commands these name, so every row reports itself. Listed
    anyway, for the reason the whole shell is built this way: the menu is measured,
    and showing an empty one would hide what the reference CAD application offers here.
    """
    for label, command in entries:
        if command and command.lower() in COMMANDS:
            op = layout.operator("narwhal.run_command", text=label)
            op.command = command
            continue
        op = layout.operator("narwhal.menu_todo", text=label)
        op.label = label
        op.command = command or ""


class NARWHAL_MT_viewport_tab(bpy.types.Menu):
    """Right-clicking a viewport tab. Verified verbatim -- `views.TAB_MENU`."""
    bl_idname = "NARWHAL_MT_viewport_tab"
    bl_label = "Viewport"

    def draw(self, context):
        _draw_tab_menu(self.layout, views.TAB_MENU)


class NARWHAL_MT_viewport_tab_add(bpy.types.Menu):
    """The `+` tab's menu. Verified verbatim -- `views.TAB_ADD_MENU`."""
    bl_idname = "NARWHAL_MT_viewport_tab_add"
    bl_label = "Add Viewport"

    def draw(self, context):
        _draw_tab_menu(self.layout, views.TAB_ADD_MENU)


def show_tab_add_menu():
    """Pop the `+` tab's menu. Returns True, or None if it could not open."""
    try:
        bpy.ops.wm.call_menu(name="NARWHAL_MT_viewport_tab_add")
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] add-viewport menu could not open: {e!r}")
        return None
    return True


def show_tab_menu():
    """Pop a viewport tab's right-click menu. Returns True, or None."""
    try:
        bpy.ops.wm.call_menu(name="NARWHAL_MT_viewport_tab")
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] viewport tab menu could not open: {e!r}")
        return None
    return True


def show_context_menu(kind):
    """Pop context menu `kind` at the cursor. Returns the kind shown, or None."""
    name = CONTEXT_MENU_CLASSES.get(kind)
    if name is None:
        return None
    try:
        bpy.ops.wm.call_menu(name=name)
    except Exception as e:  # noqa: BLE001
        app_state.log(f"the {kind} context menu could not open ({e!r})")
        redraw_cmdbar()
        return None
    return kind


def _gumball_menu_action(key):
    """Perform Gumball menu entry `key`. Returns the message to log.

    Every branch either changes real state or says it cannot -- the five entries
    `gumball.MENU_ENTRIES` marks unimplemented all land in the last return.
    """
    if not gumball.menu_is_implemented(key):
        return gumball.menu_not_implemented_message(key)

    if key == "reset":
        # "Restores the default placement and orientation for the gumball."
        changed = reset_gumball()
        return ("Gumball reset to its default position" if changed
                else "Gumball is already at its default position")
    if key == "relocate":
        # "Drag to relocate gumball widget away from its default position." The
        # gesture lives on the HANDLES -- see `_gumball_relocate_begin`: "Double-click
        # on an arrow to relocate Gumball along the direction." the reference CAD application ALSO has a
        # GumballRelocate command that asks for the new location outright; that needs
        # a point-input command; this reports the available handle gesture rather than
        # pretending to start an unsupported command.
        return ("Relocate the Gumball by double-clicking a move arrow or a Move-2D "
                "handle" + ("  (currently relocated -- Reset Gumball to undo)"
                            if gumball.is_relocated(gumball_offset()) else ""))
    if key == "on":
        set_gumball(True)
        return "Gumball on"
    if key == "off":
        set_gumball(False)
        return "Gumball off"
    if key.startswith("align_"):
        want = {"align_cplane": "CPlane", "align_world": "World",
                "align_view": "View"}.get(key)
        if want is None:
            return gumball.alignment_not_implemented_message("Object")
        got = set_gumball_alignment(want)
        if got is None:
            return gumball.alignment_not_implemented_message(want)
        return f"Gumball aligned to {got}"
    if key == "autoreset":
        on = set_gumball_autoreset(not gumball_autoreset())
        return ("AutoReset Gumball on: after a rotation the Gumball re-aligns" if on
                else "AutoReset Gumball off: the Gumball keeps a rotation")
    if key == "snappy":
        set_gumball_snappy(True)
        return "Snappy dragging: object snaps are used while dragging"
    if key == "smooth":
        set_gumball_snappy(False)
        return "Smooth dragging: object snaps are ignored while dragging"
    if key == "drag_strength":
        # the reference CAD application opens a slider; we have no dialog, so the command line asks -- which
        # is where every other numeric value in this app is typed.
        #
        # Set as the active command DIRECTLY rather than through `start_command`,
        # because `GumballDragStrengthCmd` is deliberately not in the registry (it is
        # not a the reference CAD application command name, so it must not be typeable or autocomplete to).
        # Same precedent as `GumballNumericCmd`.
        app_state.active_command = commands.GumballDragStrengthCmd()
        _open_command_line()
        redraw_cmdbar()
        return None
    return gumball.menu_not_implemented_message(key)


def _show_gumball_menu():
    """Pop the Gumball menu at the cursor, as clicking the menu ball does."""
    try:
        bpy.ops.wm.call_menu(name="NARWHAL_MT_gumball")
        return True
    except Exception as e:  # noqa: BLE001
        app_state.log(f"Gumball menu could not open ({e!r})")
        redraw_cmdbar()
        return False


class NARWHAL_OT_layer_menu_action(Operator):
    """One row of the Layers panel's right-click menu."""
    bl_idname = "narwhal.layer_menu_action"
    bl_label = "Layer"
    bl_options = {"INTERNAL"}

    action: StringProperty(default="")

    def execute(self, context):
        msg = layer_menu_action(self.action)
        if msg:
            app_state.log(msg)
        _tag_redraw_bands()
        redraw_cmdbar()
        return {"FINISHED"}


class NARWHAL_MT_sidebar_flyout(bpy.types.Menu):
    """Compact icon toolbar opened from a left-palette cascade arrow."""
    bl_idname = "NARWHAL_MT_sidebar_flyout"
    bl_label = "Toolbar"

    def draw(self, context):
        button = _sidebar_flyout_button
        buttons = tbar.sidebar_flyout_rows(button, set(COMMANDS))
        groups = tbar.flyout_icon_groups(buttons)
        if not groups:
            self.layout.label(text="(empty)")
            return
        self.layout.label(text=getattr(button, "link", None) or "Toolbar")
        self.layout.separator()
        for group_index, group in enumerate(groups):
            if group_index:
                self.layout.separator()
            for icon_row in group:
                strip = self.layout.row(align=True)
                strip.scale_x = 1.15
                strip.scale_y = 1.15
                for item in icon_row:
                    icon_id = _toolbar_icon_id(item.icon)
                    label = item.hover_text() or item.label
                    op = (strip.operator("narwhal.toolbar_button", text="", icon_value=icon_id)
                          if icon_id else
                          strip.operator("narwhal.toolbar_button", text=item.label[:3]))
                    op.command = item.left or ""
                    op.label = label


class NARWHAL_MT_layer_context(bpy.types.Menu):
    """Right-clicking a layer's name -- the reference CAD application's layer context menu (`LAYER_MENU`)."""
    bl_idname = "NARWHAL_MT_layer_context"
    bl_label = "Layer"

    def draw(self, context):
        for item in LAYER_MENU:
            if item is None:
                self.layout.separator()
                continue
            label, action = item
            if action is None:
                op = self.layout.operator("narwhal.menu_todo", text=label)
                op.label = label
            else:
                op = self.layout.operator("narwhal.layer_menu_action", text=label)
                op.action = action


# The Gumball menu classes are defined here, well after `CLASSES` is built, so they
# are appended rather than listed there -- a module-level tuple cannot forward-
# reference a class. (Listing them in CLASSES raised NameError at import.)
CLASSES = CLASSES + (NARWHAL_OT_layer_menu_action, NARWHAL_MT_layer_context,
                     NARWHAL_MT_sidebar_flyout,
                     NARWHAL_OT_gumball_menu, NARWHAL_MT_gumball,
                     NARWHAL_OT_toolbar_tab, NARWHAL_OT_toolbar_button,
                     NARWHAL_MT_toolbar_tabs,
                     NARWHAL_MT_context_viewport, NARWHAL_MT_context_object,
                     NARWHAL_MT_context_control_point,
                     NARWHAL_MT_viewport_tab, NARWHAL_MT_viewport_tab_add)


def _gumball_toggle_copy():
    """Alt tapped during a drag. Returns the message to log, or None.

    Verified (the reference CAD application documentation): "Copy - Tap Alt -- While dragging, tap Alt to
    make a copy of the object. Tap Alt again to leave copy mode."

    Turning copy mode ON leaves a copy of the selection at the position the drag
    STARTED from, and the drag carries on with the originals. That is the visible
    result the reference CAD application gives, and it is why the inverse of the accumulated transform is
    the thing applied to the copies: the copies have to go back to where the objects
    were when the drag began.

    Only the translation handles support it. On a rotate or scale the inverse is not
    a negated vector, and doing something approximate there would be worse than
    saying so (rule 4).
    """
    st = _gumball_drag
    if st is None:
        return None
    kind = st.get("mode") or st.get("kind")
    if not gumball.copy_is_available(kind):
        return gumball.copy_not_available_message(kind)

    now = gumball.toggle_copy(bool(st.get("copy")))
    st["copy"] = now
    if not now:
        st["alt_down_at"] = None
        return "Gumball: copy mode off"
    # remember WHEN Alt went down, so the release can tell a tap from a hold.
    import time as _time

    st["alt_down_at"] = _time.monotonic()

    # Leave the copies behind, at the drag's starting position. : the document
    # must hold the drag so far before copies are made from it.
    _gumball_flush(st)
    back = _gumball_drag_inverse(st)
    if back is None:
        st["copy"] = False
        return gumball.copy_not_available_message(kind)
    made = []
    try:
        ctx = app_state.ctx
        for ob in bridge.selected_display_objects():
            name = commands._duplicate_with_transform(ctx, ob.name, back, "Copy")
            if name:
                made.append(name)
    except Exception as e:  # noqa: BLE001
        st["copy"] = False
        return f"Gumball: copy failed ({e!r})"
    if not made:
        st["copy"] = False
        return "Gumball: nothing to copy"
    _tag_redraw_viewports()
    invalidate_snap_cache()
    return (f"Gumball: copy mode on -- left {len(made)} copy(ies) behind "
            f"(tap Alt again to leave copy mode)")


def _gumball_alt_released():
    """Alt released during a gumball drag. Returns a message to log, or None.

    A HOLD is momentary, so releasing leaves copy mode; a TAP is a toggle, so
    releasing changes nothing. `gumball.alt_release_ends_copy` owns that rule and the
    250 ms threshold (application-defined -- see its docstring).
    """
    st = _gumball_drag
    if st is None or not st.get("copy"):
        return None
    down_at = st.get("alt_down_at")
    if down_at is None:
        return None
    import time as _time

    held_ms = (_time.monotonic() - down_at) * 1000.0
    if not gumball.alt_release_ends_copy(held_ms):
        return None          # a tap: copy mode stays on until the next tap
    st["alt_down_at"] = None
    return _gumball_toggle_copy()


def _gumball_drag_inverse(st):
    """A Transform undoing what this drag has applied so far, or None.

    Translation only, which is what `gumball.COPYABLE_KINDS` allows: the accumulated
    amount is a signed distance along `axis_dir` for a move, and an in-plane vector
    for a Move-2D or origin drag.
    """
    kind = st.get("mode") or st.get("kind")
    try:
        if kind == gumball.MOVE:
            total = float(st.get("total") or 0.0)
            return bridge.translation_xform(
                gumball.translation(st["axis_dir"], -total))
        if kind in (gumball.PLANE, gumball.ORIGIN):
            moved = st.get("moved_total") or (0.0, 0.0, 0.0)
            return bridge.translation_xform((-moved[0], -moved[1], -moved[2]))
    except Exception:  # noqa: BLE001
        return None
    return None


# ---------------------------------------------------------------------------
# Gumball sub-object drags support solid faces and edges while keeping object-level
# transforms separate.
# "Select a face. Pick a point to move from. Pick a point to move to." -- the neighbours
# follow, staying attached. `bridge.move_subobject` does that EXACTLY for solids
# with planar faces, and refuses (by name) a curved part or a move that would bend a face.
#
# How the Gumball drives it -- application-defined, each choice flagged:
# * TRANSLATION only (Move arrows, Move-2D, the origin): `move_subobject` moves; it does
#  not rotate or scale. Rotate/Scale handles on a solid's part say "not implemented yet"
#  instead of starting -- "accounted for like the scale options".
# * ONE part at a time: the ids change after every move, so several parts cannot be
#  moved as one in a single exact step yet. Refused by name.
# * The solid is rebuilt ONCE, on release, from the drag's total; while dragging, the
#  widget and the highlighted part follow the cursor. A new exact solid per mouse move
#  would rebuild the OCCT shape and the whole display dozens of times a second.
# ---------------------------------------------------------------------------

def _selected_brep_parts():
    """The selected sub-objects that are faces/edges/vertices of solids."""
    return sorted(k for k in _selected_subobjects if k[1] in _BREP_KINDS)


def gumball_targets_brep_part():
    """True when the Gumball is acting on a solid's face/edge/vertex."""
    return bool(_selected_brep_parts()) and gumball_targets_subobjects()


def _brep_part_refusal(mode):
    """Why the Gumball cannot start `mode` on the selected solid parts, or None."""
    parts = _selected_brep_parts()
    if not parts:
        return None
    if mode not in (gumball.MOVE, gumball.PLANE, gumball.ORIGIN):
        return (f"Gumball: {str(mode).replace('_', ' ')} of a solid's "
                f"{parts[0][1].lower()} is not implemented yet -- only moving it is")
    if len(parts) > 1 or len(_selected_subobjects) > 1:
        return ("Gumball: moving more than one part of a solid at once is not "
                "implemented yet -- select one face, edge or vertex")
    return None


#: application-defined: the live redraw of a part drag rebuilds the exact solid at most this often (s).
#: Verified cost of one rebuild of a 10-face booleaned solid is a few ms, so 30 Hz keeps
#: the drag fluid without queueing rebuilds behind the mouse.
BREP_PART_LIVE_INTERVAL = 1.0 / 30.0


def _brep_part_frame(default_plane):
    """Reopened : the Gumball on ONE selected FACE is aligned to that face -- its
    z is the face normal, x the default x laid into the face. Verified
    (a focused runtime check): every face of a booleaned flat solid moves
    along its own normal, while along a world axis most are refused (a neighbour would
    bend) -- which is what "booleaned flat objects cannot be moved" was. Edges and
    vertices keep the default alignment (application-defined). Returns a CPlane or None."""
    parts = _selected_brep_parts()
    if len(parts) != 1 or parts[0][1] != subobject.FACE:
        return None
    name, _k, fid = parts[0]
    tris = dict(_brep_parts_all().get(name, {}).get("faces", ())).get(fid)
    if not tris:
        return None
    n = [0.0, 0.0, 0.0]
    for p, q, r in tris:
        u = [q[i] - p[i] for i in range(3)]
        v = [r[i] - p[i] for i in range(3)]
        c = cplane.cross(u, v)
        n = [n[i] + c[i] for i in range(3)]
    n = cplane.normalize(n)
    if n is None:
        return None
    for cand in (default_plane.xaxis, default_plane.yaxis, default_plane.zaxis):
        d = cplane.dot(cand, n)
        x = cplane.normalize([cand[i] - d * n[i] for i in range(3)])
        if x is not None:
            break
    else:
        return None
    y = cplane.cross(n, x)
    try:
        return cplane.CPlane(default_plane.origin, x, y)
    except ValueError:
        return None


def _brep_part_live(st, force=False):
    """Rebuild the exact solid from the drag's original geometry and running total,
    then show it by
    re-tessellating that one object in place. The document is not touched until release.
    A refused move shows the solid unmoved and says why, once per new reason."""
    import time
    now = time.monotonic()
    if not force and now - st.get("brep_t", 0.0) < BREP_PART_LIVE_INTERVAL:
        return
    st["brep_t"] = now
    name, kind, sub_id = st["brep_part"]
    vec = st.get("brep_vec") or (0.0, 0.0, 0.0)
    try:
        cand = bridge.move_subobject(st["brep_geom"], kind.lower(), sub_id, vec)
        why = None
    except ValueError as e:
        cand, why = None, str(e)
    st["brep_candidate"] = (vec, cand)
    try:
        _rebuild_one(name, cand if cand is not None else st["brep_geom"])
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] live part redraw failed: {e!r}")
    if why and why != st.get("brep_why"):
        app_state.log(f"Gumball: {kind.lower()} {sub_id} of {name} cannot move there -- {why}")
        redraw_cmdbar()
    st["brep_why"] = why


def _brep_part_offset():
    """The running drag's translation of a solid part (world), or None -- for the
    highlight, which follows the cursor while the solid itself waits for the release."""
    st = _gumball_drag
    if st is None or st.get("brep_part") is None:
        return None
    return st.get("brep_vec")


def _commit_brep_part_move(st):
    """Release of a solid-part drag: one exact rebuild. Returns the line to log."""
    name, kind, sub_id = st["brep_part"]
    vec = st.get("brep_vec") or (0.0, 0.0, 0.0)
    if max(abs(c) for c in vec) <= 1e-12:
        return None
    fobj = _document_object(name)
    if fobj is None:
        return f"Gumball: {name} is no longer in the document"
    done_vec, new = st.get("brep_candidate") or (None, None)
    if new is None or done_vec != vec:
        try:
            new = bridge.move_subobject(st.get("brep_geom") or fobj.Geometry,
                                        kind.lower(), sub_id, vec)
        except ValueError as e:
            # rule 4: the solid stays as it was (its live mesh is put back), and says why
            try:
                _rebuild_one(name, fobj.Geometry)
            except Exception:  # noqa: BLE001
                pass
            return f"Gumball: {kind.lower()} {sub_id} of {name} was not moved -- {e}"
    try:
        app_state.ctx.note_change("Gumball")
    except Exception:  # noqa: BLE001
        pass
    app_state.model.replace_geometry(fobj.Attributes.Id, new)
    rebuild_display_keeping_selection()
    invalidate_snap_cache()
    _tag_redraw_viewports()
    return (f"Gumball: moved {kind.lower()} {sub_id} of {name} by "
            f"{vec[0]:g},{vec[1]:g},{vec[2]:g}")


# Running `commands.apply_transform` on every sub-object mouse move creates a new
# OCCT master and a re-triangulated proxy per object, dozens of times a second. Now a
# drag moves only the DISPLAY (each Blender object's matrix) and accumulates the
# transform; the document gets ONE exact transform on release (or when Alt-copy needs
# the document current). Same outcome, one kernel call per object per drag.

def _mm_from_r3(xform):
    from mathutils import Matrix
    return Matrix([tuple(getattr(xform, f"M{r}{c}") for c in range(4)) for r in range(4)])


def _r3_from_mm(mm):
    import rhino3dm as r3
    t = r3.Transform(1.0)
    for r in range(4):
        for c in range(4):
            setattr(t, f"M{r}{c}", float(mm[r][c]))
    return t


def _gumball_defer(st, xform):
    """Apply `xform` to the selection's DISPLAY only, and accumulate it for release."""
    from mathutils import Matrix
    d = st.get("defer")
    if d is None:
        # A drag has one selection snapshot. Besides matching the reference CAD application's stable
        # drag semantics, this avoids rebuilding the selection list on every
        # mouse update (large imported documents make that query expensive).
        display_objects = tuple(bridge.selected_display_objects())
        # Curves are outside Blender's draw/depsgraph path. Snapshot their already
        # world-space line records once and let draw_curves move one combined batch;
        # rebuilding 1,409 per-object records on every mouse event was the #75 stall.
        curve_names = []
        curve_lines = []
        try:
            import numpy as np
            _curve_sync()
            for ob in display_objects:
                if not bridge.is_app_drawn(ob):
                    continue
                rec = _curve_record(ob)
                if rec is not None and rec["n"]:
                    curve_names.append(ob.name)
                    curve_lines.append(rec["lines"])
            curve_coords = (np.concatenate(curve_lines)
                            if curve_lines else np.zeros((0, 2, 3), dtype=np.float32))
        except Exception:
            curve_names = []
            curve_coords = None
        d = st["defer"] = {
            "display_objects": display_objects,
            "start": {ob.name: ob.matrix_world.copy() for ob in display_objects},
            "selected_names": tuple(ob.name for ob in display_objects),
            "acc": Matrix.Identity(4),
            "curve_names": frozenset(curve_names),
            "curve_coords": curve_coords,
            "curve_batch": None,
            "curve_batch_key": None,
        }
    mm = _mm_from_r3(xform)
    modal_drag = _gumball_drag is st
    for ob in d["display_objects"]:
        if ob.name in d["start"]:
            # Curves remain at their original matrix: draw_curves applies the
            # accumulated transform to their single preview batch below.
            if ob.name not in d["curve_names"] or not modal_drag:
                ob.matrix_world = mm @ ob.matrix_world
                if ob.name in d["curve_names"]:
                    # Standalone helper/probe callers have no modal draw state; retain
                    # the legacy matrix path for them. Real modal drags use the batch.
                    bridge.note_display_changed(ob.name)
    d["acc"] = mm @ d["acc"]
    return True


def _gumball_flush(st):
    """Put the deferred drag into the document: restore the display matrices and run
    ONE `apply_transform` of the accumulated transform. Returns True if it ran."""
    d = st.pop("defer", None) if st is not None else None
    if not d:
        return False
    for name, m in d["start"].items():
        ob = bpy.data.objects.get(name)
        if ob is not None:
            ob.matrix_world = m
    # `apply_transform` transforms the SELECTION and nothing else, so those names are
    # the whole change. Reporting them (rather than dropping every cache) is what keeps
    # the next drag's first move from re-sampling the entire document -- Verified 14 s
    # on large documents before.
    moved = set(d["start"])
    try:
        moved |= {ob.name for ob in bridge.selected_display_objects()}
    except Exception:  # noqa: BLE001
        moved = None
    # A click/cancel or an exactly zero drag is display-only and must not create a
    # document change or an Undo entry.
    from mathutils import Matrix
    if d["acc"] == Matrix.Identity(4):
        return False
    before = _doc_key()
    listeners = list(bridge.DISPLAY_CHANGE_LISTENERS)
    bridge.DISPLAY_CHANGE_LISTENERS[:] = []
    commit_xform = _r3_from_mm(d["acc"])
    mode = st.get("mode") or st.get("kind")
    if mode == gumball.ROTATE and st.get("axis_dir") and st.get("origin"):
        # `mathutils.Matrix` is float32. Thousands of small preview rotations drift
        # enough that the exact-geometry classifier sees a non-uniform transform and
        # routes a pure rotation through OCCT GTransform's NURBS conversion, which can
        # segfault on trimmed solids. The preview may accumulate incrementally, but the
        # document commit is one canonical double-precision rotation from drag state.
        commit_xform = bridge.rotation_xform(
            st.get("total", 0.0), st["axis_dir"], st["origin"])
    try:
        commands.apply_transform(app_state.ctx, commit_xform)
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] gumball transform failed: {e!r}")
        invalidate_snap_cache()        # it may have got part way: trust nothing
        return False
    finally:
        bridge.DISPLAY_CHANGE_LISTENERS[:] = listeners
    if moved is None:
        invalidate_snap_cache()
    else:
        # The exact commit intentionally suppresses 1 notification per object above,
        # but the app-drawn curve pass still needs ONE batched invalidation. Without
        # this, document/control points/Gumball move while the static curve GPU batch
        # remains at its old position.
        # Curve rendering is owned by this module, so invalidate it directly rather
        # than depending on the optional bridge listener being attached in this exact
        # lifecycle/background state. Notify every other listener once as a batch.
        _curve_display_changed(moved)
        for listener in listeners:
            if listener is _curve_display_changed:
                continue
            try:
                listener(moved)
            except Exception:  # noqa: BLE001
                pass
        _note_objects_changed(moved, before)
    return True


# ---------------------------------------------------------------------------
# Ctrl-dragging a move arrow extrudes eligible curves and surfaces or pushes/pulls a
# solid face. Exact work is delegated to
# `bridge.gumball_extrude`. While dragging, a GHOST of the result follows the cursor; on
# release the result is added (curve/surface: a NEW object, the input kept -- application-defined, as
# ExtrudeCrv's DeleteInput=No) or replaces the solid (a face push/pull).
# ---------------------------------------------------------------------------

def _extrude_handle_available():
    """Whether the current selection should show 's Extrude dots.

    the reference CAD application shows them for curves, one-face surfaces, and a picked solid face. The exact
    backend makes the final eligibility decision by name; this cheap display predicate
    avoids advertising them on a whole polysurface or a point/mesh."""
    import rhino3dm as r3

    targets = _extrude_targets()
    if targets is None:
        return False
    kind, what = targets
    if kind == "part":
        return True
    if not what:
        return False
    for name in what:
        fobj = _document_object(name)
        if fobj is None:
            return False
        geom = fobj.Geometry
        if isinstance(geom, r3.Curve):
            continue
        if isinstance(geom, r3.Surface):
            continue
        if isinstance(geom, r3.Extrusion):
            try:
                if len(geom.ToBrep(False).Faces) == 1:
                    continue
            except Exception:  # noqa: BLE001
                pass
        if isinstance(geom, r3.Brep) and len(geom.Faces) == 1:
            continue
        return False
    return True


def _extrude_targets():
    """("part", (name, id)) for one selected face of a solid, else ("objects", names)."""
    parts = _selected_brep_parts()
    if len(parts) == 1 and parts[0][1] == subobject.FACE and len(_selected_subobjects) == 1:
        return ("part", (parts[0][0], parts[0][2]))
    names = sorted(selection_names())
    return ("objects", names) if names else None


def _extrude_compute(st, force=False):
    """Recompute the extrude candidates for the drag so far (throttled) and their ghost."""
    import time
    now = time.monotonic()
    if not force and now - st.get("ext_t", 0.0) < BREP_PART_LIVE_INTERVAL:
        return
    st["ext_t"] = now
    d = float(st.get("total") or 0.0)
    cands, ghost, why = [], [], None
    if abs(d) > 1e-9:
        kind, what = st["extrude"]
        jobs = ([(what[0], ("face", what[1]))] if kind == "part"
                else [(n, None) for n in what])
        if kind == "objects" and len(what) > 1:
            import rhino3dm as r3

            rows = [(name, _document_object(name)) for name in what]
            if all(fobj is not None and isinstance(fobj.Geometry, r3.Curve)
                   for name, fobj in rows):
                try:
                    regions = commands.extrude_planar_profile_regions(
                        [fobj.Geometry for _name, fobj in rows], d,
                        direction=st["axis_dir"])
                except ValueError as e:
                    why = f"selected profiles: {e}"
                    jobs = []
                else:
                    jobs = []
                    for new in regions:
                        cands.append((tuple(what), None, new))
                        try:
                            ghost.extend(pl for _i, pl in (bridge.subobject_parts(new) or {})
                                         .get("edges", ()))
                        except Exception:  # noqa: BLE001
                            pass
        for name, part in jobs:
            fobj = _document_object(name)
            if fobj is None:
                continue
            try:
                new = bridge.gumball_extrude(fobj.Geometry, st["axis_dir"], d, part=part)
            except ValueError as e:
                why = f"{name}: {e}"
                continue
            cands.append((name, part, new))
            try:
                ghost.extend(pl for _i, pl in (bridge.subobject_parts(new) or {})
                             .get("edges", ()))
            except Exception:  # noqa: BLE001
                pass
    st["ext_cands"], st["ext_ghost"], st["ext_d"] = cands, ghost, d
    if why and why != st.get("ext_why"):
        app_state.log(f"Gumball extrude: {why}")
        redraw_cmdbar()
    st["ext_why"] = why


def _extrude_commit(st):
    """Release of an extrude drag. Returns the line to log."""
    if abs(float(st.get("total") or 0.0)) <= 1e-9:
        return None
    if st.get("ext_d") != st.get("total"):
        _extrude_compute(st, force=True)
    cands = st.get("ext_cands") or []
    if not cands:
        return f"Gumball extrude: nothing was extruded -- {st.get('ext_why') or 'no input'}"
    ctx = app_state.ctx
    made = []
    for name, part, new in cands:
        if part is not None:
            fobj = _document_object(name)
            if fobj is None:
                continue
            ctx.note_change("Gumball extrude")
            app_state.model.replace_geometry(fobj.Attributes.Id, new)
            made.append(name)
        else:
            _, display = ctx.add_geometry(new, "Extrusion")
            made.append(display)
    rebuild_display_keeping_selection()
    invalidate_snap_cache()
    clear_subobject_selection()
    return f"Gumball: extruded {st['total']:g} -> {', '.join(made)}"


def _gumball_apply(xform):
    """Bake one incremental Transform into the document and the viewport.

    When CONTROL POINTS are selected the transform is applied to THEM, not to the
    whole object: the Gumball sits on the grips you picked, so dragging it must move
    those grips. Transforming the object instead would move the entire curve, which
    is what "the gumball doesn't attach to it" looked like from the outside.
    """
    if gumball_targets_control_points():
        return _apply_xform_to_control_points(xform)
    st = _gumball_drag
    if st is not None and st.get("brep_part") is not None:
        # accumulate; the exact solid is rebuilt once, on release
        try:
            d = (float(xform.M03), float(xform.M13), float(xform.M23))
        except Exception:  # noqa: BLE001
            return False
        v = st.get("brep_vec") or (0.0, 0.0, 0.0)
        st["brep_vec"] = (v[0] + d[0], v[1] + d[1], v[2] + d[2])
        _brep_part_live(st)
        _tag_redraw_viewports()
        return True
    if gumball_targets_subobjects():
        return _apply_xform_to_subobjects(xform)
    if st is not None and not st.get("copy"):
        return _gumball_defer(st, xform)
    try:
        commands.apply_transform(app_state.ctx, xform)
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] gumball transform failed: {e!r}")
        return False
    # the geometry moved, so every cached snap target is stale
    invalidate_snap_cache()
    return True


def _apply_xform_to_control_points(xform):
    """Apply `xform` to the SELECTED control points only. True if any moved.

    The transform is applied to each grip's own location, so it works for every
    Gumball mode -- move, rotate and the three scales -- rather than only for
    translation. Bracketed as one undo record like every other Gumball drag.
    (every target from the ORIGINAL positions) and  (only the edited
    curves re-drawn) hold as in `move_control_points` -- the same `_edit_control_points`.
    """
    if not _selected_cps:
        return False
    return _edit_control_points("Gumball control point",
                                lambda p: bridge.transform_point(xform, p)) > 0


def _gumball_snap_point(st, area, space, region, mouse_x, mouse_y):
    """The object snap under the cursor during this drag, or None.

    Verified (the reference CAD application documentation): "Snappy gumball -- Snaps to
    object snap locations." / "Smooth gumball -- Ignores object snap locations."

    Three things keep this honest:

    * :func:`gumball.snappy_applies` gates it, so the SCALE modes -- which consume
      a ratio, not a location -- are never quietly snapped. The pure module states
      that scope limit; this function only obeys it.
    * the dragged objects are EXCLUDED as targets (`st["exclude"]`). They sit
      under the widget, so leaving them in would snap every drag to its own
      starting point and the gumball would appear frozen.
    * the snap is published through `set_active_snap`, so the marker and the
      snap's NAME appear while dragging exactly as they do while picking. A snap
      that moves your geometry without showing itself is a surprise, not an aid.

    Returns the `osnap.Snap`, not just its point, because the caller needs to know
    whether a snap happened at all: a snapped drag must skip Drag Strength, which
    exists to scale free mouse motion and would drag the object PAST the exact
    location the snap just asked for.
    """
    if not _gumball_snappy:
        return None
    mode = st.get("mode") or st["kind"]
    if not gumball.snappy_applies(mode):
        return None
    cursor = pick_point(area, space, region,
                        mouse_x - region.x, mouse_y - region.y)
    if cursor is None:
        return None
    snap = snap_at(area, space, region, cursor,
                   exclude_names=st.get("exclude") or ())
    if set_active_snap(snap, _pane_key(area) if snap is not None else None):
        _tag_redraw_viewports()
    return snap


#: Shift is read on every update rather than only at mouse-down. Rotate:
#: Shift is Ortho, so the angle snaps to multiples of the ortho angle. Inferred: "The
#: default angle is 90 degrees" (the reference CAD application documentation).
GUMBALL_SHIFT_ROTATE_DEG = 90.0


def _gumball_update(mouse_x, mouse_y, shift_held=None):
    """Advance the running drag to this mouse position.

    Each event applies the **increment** since the last one, so the document is
    always current and no "original state" has to be stored and restored.
    `shift_held` (None = unknown, keep the last) re-decides the Shift modes live.
    """
    st = _gumball_drag
    if st is None:
        return False
    note_user_interaction()
    st["mouse_last"] = (mouse_x, mouse_y)
    if shift_held is not None:
        st["shift_now"] = bool(shift_held)
    if st["kind"] == gumball.SCALE and shift_held is not None:
        # Shift selects Scale3D while held; release returns to Scale1D.
        # The ratio so far is undone in the old mode and redone in the new one.
        want_mode = gumball.SCALE_3D if shift_held else gumball.SCALE
        if (st.get("mode") or st["kind"]) != want_mode:
            acc = st.get("accum", 1.0) or 1.0
            old = st.get("mode") or st["kind"]
            if abs(acc - 1.0) > 1e-12:
                undo_x = (bridge.scale_3d_xform(1.0 / acc, st["origin"])
                          if old == gumball.SCALE_3D else
                          bridge.scale_1d_xform(1.0 / acc, st["axis_dir"], st["origin"]))
                redo_x = (bridge.scale_3d_xform(acc, st["origin"])
                          if want_mode == gumball.SCALE_3D else
                          bridge.scale_1d_xform(acc, st["axis_dir"], st["origin"]))
                _gumball_apply(undo_x)
                _gumball_apply(redo_x)
            st["mode"] = want_mode
    # dispatch on the MODE (what the drag means with its modifiers),
    # not the handle kind. `mode` defaults to the kind for drags started before
    # modes existed, so the plain behaviours are untouched.
    kind = st.get("mode") or st["kind"]
    xform = None
    # The world-space translation this event applied, if any. Collected here and
    # added to the widget's display shift at the end, so the widget travels with the
    # object. Rotate and scale leave it None on purpose; see
    # `_gumball_begin`.
    shift = None

    if kind == gumball.EXTRUDE_AXIS:
        # the distance along the arrow, exactly as a move measures it; nothing
        # is transformed -- the ghost shows the extrusion, the release builds it
        d = gumball.move_distance(st["axis_2d"][0], st["axis_2d"][1], st["axis_len"],
                                  st["mouse0"], (mouse_x, mouse_y))
        d = gumball.apply_drag_strength(d, _gumball_drag_strength)
        st["total"] = d
        _extrude_compute(st)
        _tag_redraw_viewports()
        return True

    if kind == gumball.MOVE:
        d = None
        if _gumball_snappy:
            g_area, g_space, g_region = _area_by_pane_key(st["pane"])
            if g_region is not None and g_space is not None:
                snap = _gumball_snap_point(st, g_area, g_space, g_region,
                                           mouse_x, mouse_y)
                if snap is not None:
                    # A move arrow is ONE-dimensional, so the snap's world POINT is
                    # reduced to a distance along the arrow: the widget origin
                    # travels to the snap's projection on the axis. Drag Strength is
                    # deliberately skipped here -- see `_gumball_snap_point`.
                    d = gumball.project_onto_axis(st["origin"], st["axis_dir"],
                                                  snap.point)
        if d is None:
            d = gumball.move_distance(st["axis_2d"][0], st["axis_2d"][1],
                                      st["axis_len"], st["mouse0"],
                                      (mouse_x, mouse_y))
            # "Drag Strength -- Sets the amount of the gumball drag as a percentage
            # of mouse movement." At the documented default of 100 this is the
            # identity, so the ordinary drag is unchanged.
            d = gumball.apply_drag_strength(d, _gumball_drag_strength)
        step = d - st["last"]
        st["last"] = d
        st["total"] = d
        if step != 0.0:
            shift = gumball.translation(st["axis_dir"], step)
            xform = bridge.translation_xform(shift)

    elif kind == gumball.SCALE:
        d = gumball.move_distance(st["axis_2d"][0], st["axis_2d"][1],
                                  st["axis_len"], st["mouse0"],
                                  (mouse_x, mouse_y))
        want = gumball.scale_from_distance(st["scale_at"], d)
        step = want / st["accum"] if st["accum"] else 1.0
        st["accum"] = want
        st["total"] = want
        if abs(step - 1.0) > 1e-12:
            xform = bridge.scale_1d_xform(step, st["axis_dir"], st["origin"])

    elif kind == gumball.SCALE_3D:
        # Shift on a scale handle: "scale the object in three dimensions". Same
        # screen-space distance -> ratio math as Scale1D, applied uniformly.
        d = gumball.move_distance(st["axis_2d"][0], st["axis_2d"][1],
                                  st["axis_len"], st["mouse0"],
                                  (mouse_x, mouse_y))
        want = gumball.scale_from_distance(st["scale_at"], d)
        step = want / st["accum"] if st["accum"] else 1.0
        st["accum"] = want
        st["total"] = want
        if abs(step - 1.0) > 1e-12:
            xform = bridge.scale_3d_xform(step, st["origin"])

    elif kind == gumball.SCALE_2D:
        # Shift on a Move-2D handle: "re-size the object in two axes with the
        # same scale. The Gumball origin is the base point of scaling." The ratio
        # is the cursor's in-plane distance from the origin over the grab point's.
        _area, space, region = _area_by_pane_key(st["pane"])
        if region is None or space is None:
            return False
        pt = _ray_plane_point(space, region, mouse_x - region.x,
                              mouse_y - region.y, st["origin"], st["axis_dir"])
        if pt is None:
            return False
        now = gumball.plane_delta(st["axis_dir"], st["origin"], pt)
        dist = (now[0] ** 2 + now[1] ** 2 + now[2] ** 2) ** 0.5
        ref = st.get("ref_dist") or 0.0
        if ref <= 1e-9:
            return False            # grabbed at the origin: no ratio to take
        want = max(dist / ref, 1e-4)
        step = want / st["accum"] if st["accum"] else 1.0
        st["accum"] = want
        st["total"] = want
        if abs(step - 1.0) > 1e-12:
            ax_a, ax_b = st["plane_axes"]
            xform = bridge.scale_2d_xform(step, ax_a, ax_b, st["origin"])

    elif kind == gumball.ROTATE:
        _area, space, region = _area_by_pane_key(st["pane"])
        if region is None or space is None:
            return False
        pt = _ray_plane_point(space, region, mouse_x - region.x,
                              mouse_y - region.y, st["origin"], st["axis_dir"])
        if pt is None:
            return False
        snap = _gumball_snap_point(st, _area, space, region, mouse_x, mouse_y)
        if snap is not None:
            # `rotate_angle` takes only the components in the rotation plane, so a
            # snap off that plane still yields the angle THROUGH the snap.
            pt = snap.point
        raw = gumball.rotate_angle(st["origin"], st["axis_dir"],
                                   st["last_world"], pt)
        st["last_world"] = pt
        st["raw_total"] = st.get("raw_total", st["total"]) + raw
        target = st["raw_total"]
        if st.get("shift_now"):
            # Shift snaps the angle to whole ortho steps
            import math as _m
            inc = _m.radians(GUMBALL_SHIFT_ROTATE_DEG)
            target = round(target / inc) * inc
        ang = target - st["total"]
        st["total"] = target
        if abs(ang) > 1e-15:
            xform = bridge.rotation_xform(ang, st["axis_dir"], st["origin"])

    elif kind == gumball.PLANE:
        # Move-2D. Track the cursor on the plane the handle names
        # and translate by the in-plane part of the motion. `plane_delta`
        # projects out the normal component, so the object cannot drift off the
        # plane however the cursor moves.
        _area, space, region = _area_by_pane_key(st["pane"])
        if region is None or space is None:
            return False
        pt = _ray_plane_point(space, region, mouse_x - region.x,
                              mouse_y - region.y, st["origin"], st["axis_dir"])
        if pt is None:
            return False
        snap = _gumball_snap_point(st, _area, space, region, mouse_x, mouse_y)
        if snap is not None:
            # snappy moves the CENTRE onto the snap (in the plane)
            pt = snap.point
            centre = gumball.relocated_origin(st["origin"], st.get("widget_shift"))
            delta = gumball.plane_delta(st["axis_dir"], centre, pt)
        else:
            delta = gumball.plane_delta(st["axis_dir"], st["last_world"], pt)
            delta = gumball.apply_drag_strength(delta, _gumball_drag_strength)
        st["last_world"] = pt
        st["total"] = st["total"] + (delta[0] ** 2 + delta[1] ** 2
                                     + delta[2] ** 2) ** 0.5
        # A VECTOR accumulator as well as the scalar one: Alt-tap copy needs the
        # inverse of what has been applied, and a distance cannot be negated back
        # into a direction.
        mt = st.get("moved_total") or (0.0, 0.0, 0.0)
        st["moved_total"] = (mt[0] + delta[0], mt[1] + delta[1], mt[2] + delta[2])
        if any(delta):
            shift = delta
            xform = bridge.translation_xform(delta)

    else:  # ORIGIN
        area, space, region = _area_by_pane_key(st["pane"])
        if region is None or space is None:
            return False
        pt = pick_point(area, space, region,
                        mouse_x - region.x, mouse_y - region.y)
        if pt is None:
            return False
        snap = _gumball_snap_point(st, area, space, region, mouse_x, mouse_y)
        if snap is not None:
            # Snappy free movement places the widget center on the snap point.
            pt = snap.point
            centre = gumball.relocated_origin(st["origin"], st.get("widget_shift"))
            delta = gumball.snapped_origin_delta(centre, pt)
        else:
            delta = gumball.free_translation(st["last_world"], pt)
            delta = gumball.apply_drag_strength(delta, _gumball_drag_strength)
        st["last_world"] = pt
        st["total"] = st["total"] + (delta[0] ** 2 + delta[1] ** 2
                                     + delta[2] ** 2) ** 0.5
        mt = st.get("moved_total") or (0.0, 0.0, 0.0)
        st["moved_total"] = (mt[0] + delta[0], mt[1] + delta[1], mt[2] + delta[2])
        if any(delta):
            shift = delta
            xform = bridge.translation_xform(delta)

    if xform is None:
        return False
    if shift is not None:
        st["widget_shift"] = gumball.relocate_offset(
            st.get("widget_shift"), shift)
    return _gumball_apply(xform)


def _gumball_end():
    """Finish the drag. Returns a line to log, or None if nothing changed."""
    global _gumball_drag

    st = _gumball_drag
    _gumball_drag = None
    # the drag's ONE document transform, inside its undo record
    _gumball_flush(st)
    # an extrude drag builds its result once, here, inside the undo record
    if st is not None and st.get("extrude") is not None:
        line = _extrude_commit(st)
        try:
            app_state.ctx.end_undo()
        except Exception:  # noqa: BLE001
            pass
        return line or "Gumball extrude: drag along the arrow to extrude (typed values " \
                       "are not implemented yet)"
    # A Snappy drag leaves the snap marker showing. Clear it here, or it hangs in
    # the viewport after the drag that found it has finished.
    if clear_active_snap():
        _tag_redraw_viewports()
    # a solid part's move is rebuilt ONCE, here -- inside the drag's undo
    # record, so the whole drag is one undo step
    if st is not None and st.get("brep_part") is not None:
        line = _commit_brep_part_move(st)
        try:
            app_state.ctx.end_undo()
        except Exception:  # noqa: BLE001
            pass
        if line is None:
            # a click, not a drag: the reference CAD application's numeric entry is not built for a part
            mode = st.get("mode") or st["kind"]
            return (f"Gumball: typing a value for a solid's part is not implemented "
                    f"yet -- drag the {str(mode).replace('_', ' ')} handle instead")
        return line
    # close the record opened in `_gumball_begin`. A drag that grabbed a
    # handle but never moved it captured nothing, so the record is discarded and
    # no empty undo step appears.
    before = _doc_key()
    try:
        app_state.ctx.end_undo()
    except Exception:  # noqa: BLE001
        pass
    # closing a record moves the undo serial but changes no object, so the caches the
    # flush above kept current stay current (anything else in the record was already
    # reported, or dropped them)
    _note_objects_changed((), before)
    if st is None:
        return None
    mode = st.get("mode") or st["kind"]
    # the scale modes accumulate a RATIO (1.0 = unchanged); everything else
    # accumulates a signed amount (0.0 = unchanged)
    if mode in (gumball.SCALE, gumball.SCALE_2D, gumball.SCALE_3D):
        amount = st.get("accum", 1.0)
        changed = abs(amount - 1.0) > 1e-9
    else:
        amount = st.get("total", 0.0)
        changed = abs(amount) > 1e-9
    if not changed:
        # a CLICK, not a drag -- the handle was grabbed and released
        # without moving. the reference CAD application reads that as numeric entry: "Click on any handle.
        # Type a number for distance, rotation angle, or scale factor"
        # (the reference CAD application documentation). Raise the prompt instead of doing nothing.
        _gumball_numeric_begin(st)
        return None
    if mode == gumball.ROTATE and not _gumball_autoreset:
        # the widget keeps the orientation it was dragged to
        global _gumball_turn
        _gumball_turn = {"plane": gumball.rotate_frame(st["plane"], st["axis_dir"],
                                                        st["total"]),
                         "sel": frozenset(selection_names())}
    return gumball.drag_label(mode, st["axis"], amount)


def _gumball_numeric_begin(st):
    """A handle was clicked: install the numeric-entry prompt as the command.

    Reuses the ordinary command-bar plumbing (prompt rendering, typed input,
    Enter) by making `GumballNumericCmd` the active command -- so numeric entry
    behaves exactly like any other prompt, including Esc to cancel.

    Handles the reference CAD application documents no typed value for (a plain Move-2D click, the origin)
    report why rather than inventing a direction for the number to mean.
    """
    if st is None:
        return False
    mode = st.get("mode") or st.get("kind")
    if gumball.numeric_kind(mode) is None:
        app_state.log(gumball.numeric_not_available_message(mode))
        redraw_cmdbar()
        return False
    if app_state.active_command is not None:
        return False            # a command is already prompting; do not hijack it
    try:
        cmd = commands.GumballNumericCmd().configure(
            mode, st.get("axis"), st.get("origin"), st.get("axis_dir"),
            st.get("plane_axes"))
    except Exception as exc:  # noqa: BLE001
        print(f"[narwhal3d] gumball numeric entry failed to start: {exc!r}")
        return False
    app_state.active_command = cmd
    app_state.input_buffer = ""
    app_state.log(f"{cmd.prompt} (Shift+Enter reverses)")
    redraw_cmdbar()
    return True


def gumball_numeric_active():
    """True while the Gumball's numeric prompt is waiting for a value."""
    return isinstance(app_state.active_command, commands.GumballNumericCmd)


def _gumball_hover_update(area, space, region, mouse_x, mouse_y):
    """Track which handle the cursor is over. True if the highlight changed.

    The cursor POSITION is recorded alongside the handle because the tooltip is
    drawn beside the cursor, and a POST_PIXEL draw handler is given no event to ask
    where the mouse is.
    """
    global _gumball_hover, _gumball_hover_pos, _gumball_hover_pane

    key = None
    if _gumball_enabled and area is not None and region is not None:
        hit = _gumball_hit(area, space, region, mouse_x, mouse_y)
        key = hit.key if hit is not None else None
    _gumball_hover_pos = (mouse_x, mouse_y)
    _gumball_hover_pane = _pane_key(area) if area is not None else None
    if key != _gumball_hover:
        _gumball_hover = key
        return True
    return False


def draw_gumball_tooltip():
    """POST_PIXEL draw handler: the hovered handle's tooltip, beside the cursor.

    Verified (the reference CAD application documentation): "To show tooltip for handle actions -- Point
    the mouse cursor to a handle to show the tooltip for actions and modifier keys."

    The lines come from `gumball.TOOLTIPS`, which names the modifier keys we have
    NOT built as "not implemented" -- so the tooltip cannot promise more than the
    widget does. That is the whole reason the text lives in the pure module and this
    function only positions it.

    Drawn only in the pane the cursor is in, and never during a drag: once you have
    grabbed a handle the tooltip is describing something you are already doing, and
    it would sit on top of the geometry you are watching.
    """
    import bpy

    if _gumball_hover is None or _gumball_drag is not None:
        return
    if _gumball_relocate is not None:
        return
    lines = gumball.tooltip(_gumball_hover[0])
    if not lines:
        return
    context = bpy.context
    area = getattr(context, "area", None)
    region = getattr(context, "region", None)
    if area is None or area.type != "VIEW_3D" or region is None:
        return
    if _gumball_hover_pane is not None and _pane_key(area) != _gumball_hover_pane:
        return
    if _gumball_hover_pos is None:
        return
    # window coords -> this region's coords, the same conversion every mouse path
    # in this module makes
    x = float(_gumball_hover_pos[0] - region.x) + TOOLTIP_OFFSET_PX[0]
    y = float(_gumball_hover_pos[1] - region.y) + TOOLTIP_OFFSET_PX[1]
    try:
        for i, line in enumerate(lines):
            _draw_text(x, y - i * TOOLTIP_LINE_PX, line,
                       TOOLTIP_FONT_SIZE, TOOLTIP_COLOR)
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] gumball tooltip draw failed: {e!r}")


#: application-defined: how dark a face seen edge-on is, relative to one seen face-on.
GUMBALL_SOLID_SHADE_MIN = 0.55


def gumball_solid_triangles(handles, rv3d, active=None):
    """``(positions, colours)`` for the widget's filled parts as seen from `rv3d`.

    Back faces are dropped (the solids are closed, so the front faces are exactly what
    is visible) and each front face is shaded by ``|n. view|`` between
    `GUMBALL_SOLID_SHADE_MIN` and 1. Pure apart from reading the view, so the harness
    can call it.
    """
    import math

    pos, col = [], []
    if rv3d is None:
        return pos, col
    try:
        persp = bool(rv3d.is_perspective)
        eye = tuple(rv3d.view_matrix.inverted().translation)
    except Exception:  # noqa: BLE001
        return pos, col
    view = _view_direction(rv3d)
    for h in handles:
        base = gumball.HIGHLIGHT_COLOR if h.key == active else h.color
        for p0, p1, p2, n in getattr(h, "solids", ()):
            if persp:
                m = [(p0[k] + p1[k] + p2[k]) / 3.0 for k in range(3)]
                v = [m[k] - eye[k] for k in range(3)]
            else:
                v = list(view or (0.0, 0.0, -1.0))
            vl = math.sqrt(sum(c * c for c in v)) or 1.0
            facing = -sum(n[k] * v[k] for k in range(3)) / vl
            if facing <= 0.0:
                continue                                  # a back face
            s = GUMBALL_SOLID_SHADE_MIN + (1.0 - GUMBALL_SOLID_SHADE_MIN) * facing
            c = (base[0] * s, base[1] * s, base[2] * s, base[3])
            pos.extend((p0, p1, p2))
            col.extend((c, c, c))
    return pos, col


def draw_gumball():
    """POST_VIEW draw handler: the Gumball widget on the current selection.

    POST_VIEW so the shader receives WORLD coordinates and Blender's own view
    matrices place them -- the same reason the preview uses it, and what makes the
    widget automatically correct in all four panes at any zoom.
    """
    if not _gumball_enabled and _gumball_drag is None:
        return
    context = bpy.context
    area = getattr(context, "area", None)
    region = getattr(context, "region", None)
    if area is None or area.type != "VIEW_3D" or region is None:
        return
    space = _view3d_space(area)
    if space is None:
        return
    _frame, handles = _gumball_handles(area, space, region)
    if not handles:
        return
    # a drag's first snap should not have to sample its neighbourhood (see above)
    _note_gumball_warm(_frame[0], _frame[1])

    try:
        import gpu
        from gpu_extras.batch import batch_for_shader
    except Exception:  # noqa: BLE001
        return
    # Use the anti-aliased shader for Gumball lines too.
    shader, aa = _line_shader()
    if shader is None:
        return

    active = _gumball_drag["key"] if _gumball_drag is not None else _gumball_hover
    # the FILLED parts first (cone tips, scale and origin cubes), so the
    # lines draw over them. No depth buffer is used -- the widget stays on top of the
    # model, as the reference CAD application's does -- so a solid reads as 3-D by drawing only its FRONT faces,
    # each shaded by how squarely it faces the viewer.
    tris = gumball_solid_triangles(handles, getattr(space, "region_3d", None), active)
    if tris[0]:
        try:
            smooth = gpu.shader.from_builtin("SMOOTH_COLOR")
            batch_for_shader(smooth, "TRIS", {"pos": tris[0], "color": tris[1]}).draw(
                smooth)
        except Exception as e:  # noqa: BLE001
            print(f"[narwhal3d] gumball solid draw failed: {e!r}")
    prev_blend = gpu.state.blend_get()
    gpu.state.blend_set("ALPHA")
    try:
        _line_begin(shader, aa)
        _line_width(shader, aa, gumball.AXIS_THICKNESS_PX)
        for h in handles:
            color = gumball.HIGHLIGHT_COLOR if h.key == active else h.color
            shader.uniform_float("color", color)
            coords_ = []
            for poly in h.polylines:
                for a, b in zip(poly, poly[1:]):
                    coords_.append(a)
                    coords_.append(b)
            if coords_:
                _line_batch(shader, aa, coords_).draw(shader)
    except Exception as e:  # noqa: BLE001
        # the widget is cosmetic until grabbed; never break the viewport draw
        print(f"[narwhal3d] gumball draw failed: {e!r}")
    finally:
        try:
            gpu.state.line_width_set(1.0)
        except Exception:  # noqa: BLE001
            pass
        gpu.state.blend_set(prev_blend)


def _attach_gumball_handler():
    """Register the widget's draw handlers: POST_VIEW for the widget, POST_PIXEL
    for its tooltip.

    Two handlers because they need different spaces. The widget is world geometry
    (POST_VIEW, so Blender's own view matrix applies); the tooltip is screen text
    beside the cursor, which must not scale with zoom.
    """
    if _gumball_handlers:
        return True
    handler_id = bpy.types.SpaceView3D.draw_handler_add(
        draw_gumball, (), "WINDOW", "POST_VIEW")
    _gumball_handlers.append((bpy.types.SpaceView3D, handler_id))
    tip_id = bpy.types.SpaceView3D.draw_handler_add(
        draw_gumball_tooltip, (), "WINDOW", "POST_PIXEL")
    _gumball_handlers.append((bpy.types.SpaceView3D, tip_id))
    return True


def _detach_gumball_handler():
    for owner, handler_id in list(_gumball_handlers):
        try:
            owner.draw_handler_remove(handler_id, "WINDOW")
        except Exception:  # noqa: BLE001
            pass
    _gumball_handlers.clear()


def _attach_view_labels():
    """Register the pane-label draw handler on the VIEW_3D **space type**.

    Same reasoning as ``_attach_cmdbar_handler``: ``draw_handler_add`` is a
    classmethod, so one type-level registration covers all four panes -- including
    panes that do not exist yet when this runs (the GUI build splits them in over
    several later ticks). The old per-area loop only worked by luck, because a
    single default VIEW_3D happens to exist at startup; it would have silently
    drawn nothing had the panes been built the way the command bar now is.
    """
    if _view_label_handlers:
        return True  # already registered (type-level, so once is enough)
    handler_id = bpy.types.SpaceView3D.draw_handler_add(
        draw_viewport_label, (), "WINDOW", "POST_PIXEL")
    _view_label_handlers.append((bpy.types.SpaceView3D, handler_id))
    # The TAB STRIP rides along on the same registration, for the same reason: it is
    # per-pane chrome in POST_PIXEL, and it must cover panes that do not exist yet
    # when this runs (the GUI build splits them over several later ticks).
    tab_id = bpy.types.SpaceView3D.draw_handler_add(
        draw_viewport_tabs, (), "WINDOW", "POST_PIXEL")
    _view_label_handlers.append((bpy.types.SpaceView3D, tab_id))
    # the Osnap panel is NO LONGER registered here. It used to be a
    # VIEW_3D handler drawing into the bottom-left pane -- i.e. floating over a
    # viewport -- and it is now drawn inside the LEFT DOCK BAND by `draw_left_band`,
    # which is where the reference CAD application docks it (the reference layout description, the band's lower bar).
    # the floating autocomplete list. LAST, so it draws ON TOP of the labels and
    # the tab strip -- POST_PIXEL handlers run in registration order.
    comp_id = bpy.types.SpaceView3D.draw_handler_add(
        draw_completion_overlay, (), "WINDOW", "POST_PIXEL")
    _view_label_handlers.append((bpy.types.SpaceView3D, comp_id))
    # Sidebar icon names are compact cards beside the dock, drawn in viewport space so
    # the left INFO region cannot clip them.
    tooltip_id = bpy.types.SpaceView3D.draw_handler_add(
        draw_sidebar_tooltip_overlay, (), "WINDOW", "POST_PIXEL")
    _view_label_handlers.append((bpy.types.SpaceView3D, tooltip_id))
    # The cascade popup must be last so its floating toolbar and hover card stay
    # above viewport labels, command completion chrome, and the anchor tooltip.
    flyout_id = bpy.types.SpaceView3D.draw_handler_add(
        draw_sidebar_flyout_overlay, (), "WINDOW", "POST_PIXEL")
    _view_label_handlers.append((bpy.types.SpaceView3D, flyout_id))
    return True


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)

    # The MENU BAR is installed here, not in the `_setup` timer, and that is not a
    # style choice: nothing can make Blender's topbar repaint once it has been
    # drawn (measured -- five nudges, no pixel changed), so the override has to be
    # in place before the first draw. register is the last moment that is true.
    #
    # The Menu classes are registered FIRST, for a separate measured reason:
    # `register_class` inside a draw callback does not take, and the strip came out
    # blank when they were created lazily from `_draw_narwhal_menubar`.
    _register_menu_classes()
    _install_menubar()
    # The TOOLBAR replaces INFO_HT_header.draw. Installed here for the same reason as
    # the menu bar: get in before the first draw rather than hope for a repaint.
    _install_toolbar()

    # Blender's own sidebar panels go here, AFTER ours are registered: the filter is
    # by category and skips `NARWHAL_*`, so the order does not actually matter, but
    # doing it here means the first draw of the sidebar already shows the reference CAD application's tabs
    # only -- there is no frame in which the user sees `Tool / View / Animation`.
    # One call, at register: panels an addon registers later are not covered, which
    # is the honest limit of this and is why the gate counts what is LIVE.
    try:
        n_gone = suppress_blender_sidebar_panels()
        if n_gone:
            print(f"[narwhal3d] hid {n_gone} host viewport sidebar panels")
    except Exception as e:  # noqa: BLE001
        print(f"[narwhal3d] sidebar panel suppression failed: {e!r}")

    # pay OCCT's one-time ~683 ms import during startup, not on the first
    # Gumball scale release. Real app only; background tests load it when needed.
    if not bpy.app.background:
        warm_geometry_kernel()
    _register_keymaps()
    apply_navigation_preferences()        # the wheel zooms at the cursor

    # silence Blender's competing bare-letter shortcuts. "Object Mode"
    # may not exist yet on the first tick (per-mode keymaps are created lazily
    # on region init), so this retries on later ticks until every target exists.
    _start_suppression_timer()

    # apply theme + layout once the UI exists (timer runs after startup)
    def _setup():
        try:
            if not bpy.app.background:
                apply_interface_scale()      # - before the layout
                start_window_scale_watch()   # -- and again whenever it is resized
            setup_layout()
            apply_narwhal_theme()
            # Remove Blender's collapsed-region chevrons after setup, because the chips only
            # exist once the corresponding regions are hidden, and the RNA
            # property carries `rna_userdef_screen_update` so setting it here
            # re-initialises the action zones that are already there.
            suppress_region_toggle_chips()
            _attach_cmdbar_handler()
            _attach_view_labels()
            _attach_preview_handler()
            _attach_snap_handler()
            _attach_point_handler()
            _attach_gumball_handler()
            # `Units` opens its window (`-Units` stays on the command line).
            # Looked up by name at call time, so the harness can stand in for it.
            app_state.dialog_openers["units"] = lambda: globals()["_open_units_dialog"]()
            # the reference CAD application's status bar replaces Blender's. Done here rather than in
            # register so a failure cannot stop the classes registering.
            _install_statusbar()
            # The pane RIGHT-click menus ride along with the bar, because they are
            # part of it: "Right-click the pane to show the Gumball menu."
            _install_statusbar_context_menu()
            # The readout needs a mouse feed, and a modal is the only source.
            _start_cursor_tracker()
            # And the command line takes focus immediately. the reference CAD application's does: "When
            # the the reference CAD application window is active, what you type will input to the
            # command-line". Until this existed, nothing started that modal until
            # the user typed a letter or clicked the bar -- and since EVERY
            # left-click behaviour is handled inside it, none of them worked from a
            # cold start, making clicks work before any command has been typed.
            _open_command_line()
            app_state.log("Narwhal 3D ready. Type a command below, or click a tool.")
            redraw_cmdbar()
        except Exception as e:  # noqa: BLE001
            print(f"[narwhal3d] layout setup failed: {e!r}")
        return None

    bpy.app.timers.register(_setup, first_interval=0.2)


def unregister():
    # Restore the suppressed Blender shortcuts BEFORE removing our own keymap
    # entries. Removing items from the "3D View" keymap makes Blender
    # re-materialise it, which invalidates the KeyMapItem references recorded
    # by suppress_blender_shortcuts: restoring *after* the removal would
    # write through stale wrappers (a no-op that leaves Blender's shortcuts
    # disabled, or a segfault — both observed in the live keymap checks).
    _restore_blender_shortcuts()
    try:
        if bpy.app.timers.is_registered(_window_scale_tick):
            bpy.app.timers.unregister(_window_scale_tick)
    except Exception:  # noqa: BLE001
        pass
    _resize_watch["running"] = False
    # ...and put Blender's own sidebar panels back, for the same reason: this module
    # is the only record of what was removed, so it has to undo it before it goes.
    _restore_blender_sidebar_panels()
    _unregister_keymaps()
    # drop the draw handlers we own. Leaving a handler registered after the
    # module is gone means Blender calls into freed code on the next redraw, so
    # this is a crash, not a leak. The lists are cleared so a re-register does not
    # double-add.
    for owner, handler_id in list(_preview_handlers):
        try:
            owner.draw_handler_remove(handler_id, "WINDOW")
        except Exception:  # noqa: BLE001
            pass
    _preview_handlers.clear()
    _detach_curve_depsgraph_handler()
    clear_preview_cursor()
    # same for the snap marker handler -- a stale draw handler is a crash.
    _detach_snap_handler()
    # point objects and control points
    _detach_point_handler()
    # and the Gumball's.
    _detach_gumball_handler()
    # Put Blender's own status bar back. Leaving our draw installed after the
    # module is gone is the same class of crash as a stale draw handler: Blender
    # would call into freed code on the next redraw.
    _uninstall_statusbar()
    _uninstall_statusbar_context_menu()
    _uninstall_menubar()
    _uninstall_toolbar()
    _stop_cursor_tracker()
    clear_active_snap()
    invalidate_snap_cache()
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# every PIXEL size of our own chrome follows the interface scale
# (`uiscale`, applied by `apply_interface_scale` at startup). Listed here, in one place,
# so a new pixel constant has an obvious home -- and `spike/test_uiscale.py` checks that
# each name exists, so a rename cannot silently drop one.
# ---------------------------------------------------------------------------
import sys as _sys  # noqa: E402

SCALED_CONSTANTS = {
    cmdbar: ("LINE_H", "PAD_X", "PAD_Y"),
    rightpanel: ("BAND_W", "TAB_STRIP_W", "TAB_PITCH", "TAB_ICON", "TITLE_H", "HEADER_H",
                 "ROW_H", "NAME_COL_W", "NARROW_COL_W", "DISPLAY_PAD",
                 "DISPLAY_LABEL_H", "DISPLAY_ROW_H", "DISPLAY_GAP"),
    views: ("TITLE_CHIP_PAD_X", "TITLE_CHIP_PAD_Y", "TITLE_LABEL_PAD", "TITLE_ARROW_W",
            "TITLE_ARROW_HALF", "TITLE_ARROW_H", "TAB_H", "TAB_PAD_X", "TAB_GAP",
            "OSNAP_PANEL_W", "OSNAP_ROW_H", "OSNAP_BOX", "OSNAP_PAD", "OSNAP_TITLE_H",
            "OSNAP_FONT_SIZE"),
    tbar: ("TAB_ROW_H", "TAB_PAD_X", "TAB_FONT_SIZE", "SIDEBAR_ICON", "SIDEBAR_PAD",
           "SIDEBAR_CELL", "SIDEBAR_MARK", "SIDEBAR_FLYOUT_HOT",
           "SIDEBAR_TOOLTIP_PAD", "SIDEBAR_TOOLTIP_GAP", "SIDEBAR_TOOLTIP_LINE",
           "SIDEBAR_TOOLTIP_FONT", "FLYOUT_CELL", "FLYOUT_MARGIN", "FLYOUT_TITLE_H",
           "FLYOUT_OVERLAP"),
    gumball: ("DEFAULT_SIZE_PX", "PICK_TOLERANCE_PX", "MENU_PICK_MARGIN_PX"),
    pointdisplay: ("DEFAULT_SIZE_PX", "CONTROL_POLYGON_DASH_PX", "CONTROL_POLYGON_GAP_PX"),
    _sys.modules[__name__]: ("LABEL_FONT_SIZE", "LABEL_PAD", "SNAP_MARKER_PX",
                             "SNAP_LABEL_FONT_SIZE", "COMPLETION_OVERLAY_FONT_SIZE",
                             "TAB_FONT_SIZE", "POINT_OBJECT_PICK_PX",
                             "CONTROL_POINT_PICK_PX", "SUBOBJECT_PICK_PX",
                             "TOOLTIP_LINE_PX", "TOOLTIP_FONT_SIZE"),
}
for _mod, _names in SCALED_CONSTANTS.items():
    uiscale.register(_mod, _names)
