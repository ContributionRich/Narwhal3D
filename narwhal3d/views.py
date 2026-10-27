"""Pure view-orientation to label mapping.

No Blender imports: this module must be importable from a plain python3
interpreter without `bpy`, keeping quaternion-to-label logic independent of
Blender. Keep it free of any
`bpy` reference and of relative (package) imports.

The quaternion constants mirror what `narwhal3d/ui.py` assigns each pane. `ui.py` imports them from here so there is
a single source of truth; `views.py` is the pure, testable one.
"""
from __future__ import annotations

try:
    from . import design
except ImportError:  # loaded by file path (pure tests): design.py is its sibling
    import importlib.util as _ilu
    import os as _os
    import sys as _sys
    _design_spec = _ilu.spec_from_file_location(
        "narwhal_design", _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "design.py"))
    design = _ilu.module_from_spec(_design_spec)
    _sys.modules.setdefault("narwhal_design", design)
    _design_spec.loader.exec_module(design)

# View quaternions used for `region_3d.view_rotation`.
VIEW_TOP = (1.0, 0.0, 0.0, 0.0)
VIEW_FRONT = (0.7071, 0.7071, 0.0, 0.0)
VIEW_RIGHT = (0.5, 0.5, 0.5, 0.5)
# The Perspective pane matches the reference CAD application 8's default camera: location
# (63.244, -109.544, 73.030), direction
# (-0.4330, +0.7500, -0.5000), up (0,0,1)), not a hand-picked symmetric 3/4
# view. It is a proper look-at rotation:
# Blender's `view_rotation` maps view space to world space with the camera
# looking down view -Z, so the basis is [right, up, back] with back = -dir and
# right = normalize(worldZ x back). The direction matches to 1.6e-07, the up vector
# has z>0, and the camera still looks downward (z<0).
#
# History (do not regress): this was once byte-for-byte VIEW_RIGHT, so the pane
# looked straight sideways (-X) and a center click returned None. It was
# then a symmetric (-0.577,+0.577,-0.577) 3/4 view -- 13.67 deg off the reference CAD application's.
VIEW_PERSP = (0.836518, 0.482962, 0.129406, 0.224139)

# Camera zoom and target. `view_rotation` alone only aims the camera; the
# pane still inherits Blender's default `view_distance` of 18.388, about 8x too
# close, which rendered the Perspective pane as an extreme close-up (the grid
# filled it). the reference CAD application's measured camera distances from the origin:
#   Perspective: |(63.244, -109.544, 73.030)| = 146.058
#   Top/Front/Right: 31.494 (loc (0,0,31.494) / (0,-31.494,0) / (31.494,0,0))
# All four the reference CAD application cameras aim at the origin:
# loc + dir*distance lands on the origin to 4 decimals), so the view target is
# the world origin.
PERSP_DISTANCE = 146.058
ORTHO_DISTANCE = 31.494
VIEW_TARGET = (0.0, 0.0, 0.0)

# Match table for ORTHO panes, in order. The Perspective pane uses its own
# 3/4 quaternion (see VIEW_PERSP) and is distinguished by `view_perspective`
# (ORTHO vs PERSP), which is handled before this table is consulted -- so
# Right only ever matches the ORTHO pane. The quaternions no longer collide,
# but the mode check remains the authoritative discriminator for the Perspective
# pane.
_ORTHOS = (
    ("Top", VIEW_TOP),
    ("Front", VIEW_FRONT),
    ("Right", VIEW_RIGHT),
)


def _quat_close(a, b, tol=1e-3):
    """True if quaternions `a` and `b` are the same rotation (either sign).

    A unit quaternion `q` and `-q` describe the identical rotation, so accept
    both -- otherwise a pane set from a sign-flipped quaternion would be
    mislabelled.
    """
    a = [c for c in a]
    b = [c for c in b]
    return (all(abs(x - y) <= tol for x, y in zip(a, b))
            or all(abs(x + y) <= tol for x, y in zip(a, b)))


def quat_to_label(view_rotation, view_perspective, tol=1e-3):
    """Map a viewport's orientation to its the reference CAD application label.

    `view_perspective == "PERSP"` wins outright -- the Perspective pane uses an
    iso quaternion that collides with Right, so the mode is the discriminator
    (spec: fall back to "Perspective" when the mode is PERSP). Otherwise the
    rotation quaternion is compared against the documented views. A user-orbited
    ORTHO view that matches none falls back to "Custom".
    """
    if view_perspective == "PERSP":
        return "Perspective"
    quat = [c for c in view_rotation]
    for name, ref in _ORTHOS:
        if _quat_close(quat, ref, tol):
            return name
    return "Custom"


# ---------------------------------------------------------------------------
# The active viewport title and sRGB-to-linear conversion
#
# the reference CAD application marks the active viewport by the COLOUR OF ITS TITLE, not with a border
# (options/appearance_colors.htm documents exactly two settings: "Active viewport
# title -- The color for the title of the current viewport" and "Inactive
# viewport title -- The color for the titles of the viewports that are not the
# current").
#
# The active title uses a light-blue filled chip with dark text; inactive titles
# use dark text with no fill. Values below are sRGB bytes.
# ---------------------------------------------------------------------------

# Modern dark design (`design.py`): every title sits on a rounded chip; the active
# one is accent blue.
ACTIVE_TITLE_FILL = design.ACCENT_TITLE
ACTIVE_TITLE_TEXT = design.ACTIVE_TITLE_TEXT
INACTIVE_TITLE_FILL = design.TITLE_CHIP_BG
INACTIVE_TITLE_TEXT = design.CHIP_TEXT


def srgb_to_linear(component):
    """One 0-255 sRGB channel -> Blender's linear 0.0-1.0.

    Correct for **theme** colours: Blender stores those linear and sRGB-encodes
    them on output, so a colour sampled from display output must be converted or it
    renders far too bright. The documented datapoint: stored 0.34 renders as ~157.

    **Do NOT use this for `gpu` drawing** -- see :func:`gpu_rgba`.
    """
    c = component / 255.0
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def srgb_rgba(rgb, alpha=1.0):
    """An (r, g, b) sRGB byte triple -> a LINEAR RGBA tuple, for theme colours."""
    return (srgb_to_linear(rgb[0]), srgb_to_linear(rgb[1]),
            srgb_to_linear(rgb[2]), alpha)


def gpu_rgba(rgb, alpha=1.0):
    """An (r, g, b) sRGB byte triple -> the RGBA tuple to hand a `gpu` shader.

    MEASURED, because this is the opposite of the theme-colour rule and guessing
    it wrong is invisible in unit tests: colours drawn through `gpu` (our command
    bar and viewport titles) are written to the UI framebuffer **without** sRGB
    encoding, so they must be passed as the plain byte ratio. Passing a linearised
    value renders far too dark -- the active title chip came out (42,93,255)
    instead of the intended (107,163,255).

    Both rules are real and they are not in conflict: theme colours go through
    Blender's colour management, immediate-mode `gpu` draws do not.
    """
    return (rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0, alpha)


def clip_range_for(view_distance, view_location, scene_radius, scene_center=(0, 0, 0)):
    """Return a pane's ``(clip_start, clip_end)`` range.

    The far plane holds the whole scene and grid from the camera; the near plane
    preserves depth precision for curves drawn on surfaces. The compatibility rule shows no far clip in normal
    modelling): far enough to hold the whole scene and the grid from the camera -- four
    times the reach, which also covers an ortho pane's symmetric range. The near plane
    sits at 1% of the orbit distance (nothing you are looking AT is closer than that),
    never so near that the far/near ratio passes 1e6, because depth precision is what
    keeps a curve on a face from flickering through it.
    """
    d = max(float(view_distance), 1e-6)
    off = sum((float(view_location[i]) - float(scene_center[i])) ** 2
              for i in range(3)) ** 0.5
    reach = d + off + max(float(scene_radius), 0.0)
    end = max(1000.0, 4.0 * reach)
    start = max(1e-5, 0.01 * d, end * 1e-6)
    return start, end


#: Padding around title text; the chip follows the measured glyph extent.
TITLE_CHIP_PAD_X = design.TITLE_CHIP_PAD_X
TITLE_CHIP_PAD_Y = design.TITLE_CHIP_PAD_Y

#: Distance from the pane's top-left corner to the title text. Mirrors `ui.LABEL_PAD`
#: -- passed in by the caller, this is only the default so the pure tests have one.
TITLE_LABEL_PAD = design.TITLE_CHIP_INSET + TITLE_CHIP_PAD_X


def title_rect(text_w, text_h, region_h,
               label_pad=None, pad_x=None, pad_y=None):
    """The viewport title's box, as ``(x0, y0, x1, y1)`` in region pixels.

    ONE function for two jobs, deliberately: it is both the chip that
    `ui.draw_viewport_label` fills and the rect `ui.pane_title_at` hit-tests. They
    were separate arithmetic at first, which is a drift waiting to happen -- the
    visible title and the clickable title have to be the same rectangle or the
    user is aiming at something that is not there.

    `text_w`/`text_h` come from a real glyph measurement (`blf.dimensions`), which
    is why they are arguments rather than computed here: this module has no fonts.

    The y arithmetic follows the draw: glyphs grow UP from the baseline, so the
    baseline sits a full text height below the top of the region.
    """
    # uiscale updates these globals; defaults bound at import kept padding at
    # 1x while the text moved/scaled, producing inconsistent HiDPI title chips.
    label_pad = TITLE_LABEL_PAD if label_pad is None else label_pad
    pad_x = TITLE_CHIP_PAD_X if pad_x is None else pad_x
    pad_y = TITLE_CHIP_PAD_Y if pad_y is None else pad_y
    baseline_y = region_h - label_pad - text_h
    return (label_pad - pad_x, baseline_y - pad_y,
            label_pad + text_w + pad_x, baseline_y + text_h + pad_y)


def hit_title(x, y, rect):
    """True when region pixel ``(x, y)`` is inside a :func:`title_rect` box.

    Inclusive on both edges: a click exactly on the chip's border is on the chip.
    the reference CAD application gives no sub-pixel rule here and excluding the edge only ever loses a
    click the user meant.
    """
    if rect is None:
        return False
    x0, y0, x1, y1 = rect
    return x0 <= x <= x1 and y0 <= y <= y1


# ---------------------------------------------------------------------------
# THE TITLE'S DROP-DOWN ARROW
#
# The title is a first-class menu host. A small arrow exposes the same menu on a
# left-click while right-click remains supported. Its box and triangle dimensions
# are project UI values.
# ---------------------------------------------------------------------------

#: Arrow box width, and the triangle's half-width and height, in pixels. OURS.
TITLE_ARROW_W = 20.0
TITLE_ARROW_HALF = 4.0
TITLE_ARROW_H = 4.0
TITLE_ARROW_FILL = design.TITLE_CHIP_BG
TITLE_ARROW_BORDER = design.BORDER_STRONG


def title_arrow_rect(rect, width=None):
    """The arrow's own clickable box, just right of the title chip.

    Returned SEPARATELY from :func:`title_rect` rather than by widening it, because
    the two do different things on a left-click: the chip's own left-click is already
    taken (a double-click there maximizes the pane), so the arrow needs a rect of its
    own to be distinguishable. Right-click keeps working on both.
    """
    width = TITLE_ARROW_W if width is None else width   # read now: uiscale may have rescaled it
    if rect is None:
        return None
    x0, y0, x1, y1 = rect
    return (x1, y0, x1 + width, y1)


def title_menu_rect(rect):
    """Visual union of the title chip and its adjacent arrow button."""
    arrow = title_arrow_rect(rect)
    if rect is None or arrow is None:
        return None
    return (rect[0], rect[1], arrow[2], rect[3])


def title_arrow_triangle(rect, half=None, height=None):
    """The down-pointing triangle to draw, as three ``(x, y)`` points, or None.

    Centred in :func:`title_arrow_rect`. Points DOWN, the near-universal convention
    for "a menu drops from here" -- and the direction the reference CAD application's own draws.
    """
    half = TITLE_ARROW_HALF if half is None else half   # read now: uiscale may have rescaled it
    height = TITLE_ARROW_H if height is None else height   # read now: uiscale may have rescaled it
    box = title_arrow_rect(rect)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    return ((cx - half, cy + height / 2.0),
            (cx + half, cy + height / 2.0),
            (cx, cy - height / 2.0))


def hit_title_arrow(x, y, rect):
    """True when region pixel ``(x, y)`` is on the title's drop-down arrow.

    Tested against the arrow's BOX, not the triangle: a triangle is a fiddly target
    and the reference CAD application's own arrow is clickable across its whole cell. Same inclusive-edge
    rule as :func:`hit_title`.
    """
    return hit_title(x, y, title_arrow_rect(rect))


#: The viewport's corner control (UI design): a maximize/restore button at the
#: top-right, the same size and inset as the title chip so the two read as a pair.
CORNER_BUTTON = 20.0
CORNER_INSET = 6.0


def corner_button_rect(region_w, region_h, size=None, inset=None):
    """The maximize/restore button's box in region pixels (top-right corner)."""
    size = CORNER_BUTTON if size is None else size
    inset = CORNER_INSET if inset is None else inset
    x1 = float(region_w) - inset
    y1 = float(region_h) - inset
    return (x1 - size, y1 - size, x1, y1)


def hit_corner_button(x, y, region_w, region_h, size=None, inset=None):
    """True when region pixel ``(x, y)`` is on the corner maximize/restore button."""
    return hit_title(x, y, corner_button_rect(region_w, region_h, size, inset))


def corner_glyph(rect, maximized):
    """Outline segments ``[(p, q), ...]`` for the button's glyph: one square to
    maximize, two offset squares to restore."""
    x0, y0, x1, y1 = rect
    m = (x1 - x0) * 0.28

    def square(a0, b0, a1, b1):
        return [((a0, b0), (a1, b0)), ((a1, b0), (a1, b1)),
                ((a1, b1), (a0, b1)), ((a0, b1), (a0, b0))]
    if not maximized:
        return square(x0 + m, y0 + m, x1 - m, y1 - m)
    d = (x1 - x0) * 0.12
    return square(x0 + m - d, y0 + m - d, x1 - m - d, y1 - m - d) + [
        ((x0 + m, y1 - m + d * 0.0 + d), (x1 - m + d, y1 - m + d)),
        ((x1 - m + d, y1 - m + d), (x1 - m + d, y0 + m))]


def title_style(is_active):
    """How to draw a viewport title: ``(fill_rgba_or_None, text_rgba)``.

    Only the active pane gets a fill, which is what makes "which viewport am I
    in?" answerable at a glance.
    Colours are `gpu`-ready (not linearised); see :func:`gpu_rgba`.
    """
    if is_active:
        return gpu_rgba(ACTIVE_TITLE_FILL), gpu_rgba(ACTIVE_TITLE_TEXT)
    return gpu_rgba(INACTIVE_TITLE_FILL), gpu_rgba(INACTIVE_TITLE_TEXT)


# ---------------------------------------------------------------------------
# VIEWPORT TABS
#
# MEASURED (user_interface/cad_window.htm, "Viewport tabs"):
#   "Viewport tabs are located at the bottom of the main viewport frame by default.
#    Each model and layout viewport has a tab. The tabs allow you to switch between
#    viewports especially when a viewport is maximized."
# and commands/layout.htm: "These viewports are accessible on the tabs at the
# bottom of the modeling area."
#
# One strip spans the bottom of the whole viewport area, not each pane. Tab order is
# intentionally independent of layout order: `Perspective | Top | Front | Right`.
# ---------------------------------------------------------------------------

#: The four default model viewports, in TAB order (not layout order -- see above).
TAB_ORDER = ("Perspective", "Top", "Front", "Right")

#: The Add Viewport tab sits immediately after the final viewport tab.
TAB_ADD = "+"

#: OURS: strip metrics follow the command bar's line height for internal consistency.
TAB_H = 18
TAB_PAD_X = 10
TAB_GAP = 1

#: Viewport tab palette: a grey band with a lighter active tab and frame border.
TAB_BAND_BG = design.PANEL_HEADER_BG
TAB_ACTIVE_BG = design.ACCENT_SOFT
TAB_BORDER = design.BORDER
#: Tab text matches viewport titles so the chrome reads as one family.
TAB_TEXT = design.TEXT
TAB_TEXT_ACTIVE = design.TEXT_STRONG

#: Window chrome and drop-down menus, from the modern dark design (`design.py`). The
#: menu LAYOUT follows reference screenshots (flat groups, square-cornered list, a
#: filled accent row on hover); its colours are the design's.
CHROME_BG = design.BAR_BG
CHROME_TEXT = design.BAR_TEXT
MENU_BG = design.MENU_BG
MENU_BORDER = design.BORDER_STRONG
MENU_SEPARATOR = design.BORDER_STRONG
MENU_HOVER = design.ACCENT
MENU_HOVER_TEXT = design.BAR_TEXT_STRONG
MENU_TEXT = design.TEXT
MENU_TEXT_DISABLED = design.TEXT_DISABLED
MENU_ROUNDNESS = 0.2
TOOL_PRESSED = design.ACCENT


def ui_rgba(rgb, alpha=1.0):
    """An sRGB byte triple -> a Blender UI WIDGET theme colour. Widget colours are
    plain sRGB fractions (Blender's own default menu back 0.094 is #181818), unlike the
    colour-managed viewport colours `srgb_rgba` converts."""
    return (rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0, alpha)


def menu_theme():
    """The Blender widget theme of the modern design (see `design.blender_theme`)."""
    return design.blender_theme()


def tab_labels(present=None, order=TAB_ORDER):
    """The tab labels, in the reference CAD application's tab order, plus the Add tab last.

    `present` filters to the viewports that actually exist, so a layout with a pane
    missing does not offer a tab that goes nowhere. Anything present but NOT in
    `order` is appended after the known four rather than dropped -- a viewport with
    no tab would be unreachable, which is worse than an unmeasured position.
    """
    if present is None:
        names = list(order)
    else:
        have = list(present)
        names = [n for n in order if n in have]
        names += [n for n in have if n not in order]
    return tuple(names) + (TAB_ADD,)


def tab_boxes(labels, measure, x0=0, h=None, pad_x=None, gap=None):
    """Lay the tabs out left to right: ``[(label, x0, x1), ...]``.

    `measure(label) -> text width in pixels` is injected for the same reason
    `cmdbar`'s option hit-testing injects one: only Blender's `blf` can measure
    glyphs, and keeping it out of here is what makes the layout unit-testable.

    Widths follow the text, so `Perspective` is wider than `Top`.
    """
    h = TAB_H if h is None else h   # read now: uiscale may have rescaled it
    pad_x = TAB_PAD_X if pad_x is None else pad_x   # read now: uiscale may have rescaled it
    gap = TAB_GAP if gap is None else gap   # read now: uiscale may have rescaled it
    out = []
    x = x0
    for label in labels or ():
        w = max(0.0, float(measure(label))) + 2 * pad_x
        out.append((label, x, x + w))
        x += w + gap
    return out


def hit_tab(x, boxes):
    """Which tab contains strip-x `x`, or None.

    Half-open on the right so two abutting tabs cannot both claim one pixel --
    with `gap` 0 that is a real possibility, and a click belonging to two tabs is
    a bug that only shows up once someone sets the gap to zero.
    """
    for label, a, b in boxes or ():
        if a <= x < b:
            return label
    return None


def cycle_tab(labels, current, delta):
    """The tab `delta` steps from `current`, wrapping. None when there is none.

    MEASURED (commands/new_viewport_arrangements.htm, ViewportTabs): "Scroll the
    mouse wheel when it is over the tabs to cycle through the tabs."

    The Add tab is skipped: it is a button, not a viewport, and landing on it while
    scrolling would switch you to nothing. Wrapping is OURS -- "cycle" implies it
    and clamping would make the gesture dead at both ends.
    """
    real = [l for l in (labels or ()) if l != TAB_ADD]
    if not real:
        return None
    if current in real:
        i = real.index(current)
    else:
        i = 0 if delta > 0 else len(real) - 1
        return real[i]
    return real[(i + int(delta)) % len(real)]


#: The tab RIGHT-CLICK menu. MEASURED verbatim from
#: commands/new_viewport_arrangements.htm ("Right-click a tab to display a context
#: menu for managing viewports", then its "Context menu options" list). The
#: parenthesised notes are the help's own restrictions.
#: Each entry is ``(label, narwhal_command_or_None)``.
TAB_MENU = (
    ("New Viewport", "NewViewport"),          # "(Model viewports only)"
    ("New Detail", "Detail"),                 # "(Layout viewports only)"
    ("Delete", "CloseViewport"),
    ("Rename", "RenameView"),
    ("Move or Copy", None),                   # a submenu, layouts only
    ("Combined Model Tab", None),
    ("Combined Layout Tab", None),
    ("Tab Orientation", None),
    ("Hide Tabs", "ViewportTabs"),
    ("Layout Properties", None),              # "(Layout viewports only)"
    ("Print", "Print"),
)

#: The `+` (Add Viewport) menu. MEASURED verbatim from the same page's "Details":
#: "Click the [+] tab for options: New Layout / Import Layout / New Floating
#: Viewport / Split Horizontal / Split Vertical". All five name real the reference CAD application commands
#: and we have none of them, so every row reports itself.
TAB_ADD_MENU = (
    ("New Layout", "Layout"),
    ("Import Layout", "ImportLayout"),
    ("New Floating Viewport", "NewFloatingViewport"),
    ("Split Horizontal", "SplitViewportHorizontal"),
    ("Split Vertical", "SplitViewportVertical"),
)


# ---------------------------------------------------------------------------
# THE VIEWPORT-CAPTION MENU  (right-click a viewport title)
#
# Rows live in the generated `viewport_menu_data` compatibility module. This file
# cleans labels for display and maps supported rows to application actions.
# ---------------------------------------------------------------------------

def strip_accel(label):
    """the reference CAD application's verbatim label without its `&` accelerator marks.

    Reuses the same rule as the context menus (`navigate.display_label`) rather than
    inventing a second one: `&&` is a literal ampersand, a single `&` marks the next
    character and is dropped.
    """
    if not label:
        return ""
    out = []
    i = 0
    while i < len(label):
        if label[i] == "&":
            if i + 1 < len(label) and label[i + 1] == "&":
                out.append("&")
                i += 2
                continue
            i += 1
            continue
        out.append(label[i])
        i += 1
    return "".join(out)


def menu_command_key(label):
    """A normalised key from a menu label: `"Zoom &Extents"` -> `zoomextents`.

    Used for building stable menu CLASS NAMES, and for nothing else.

    **It is deliberately NOT used to decide which command a row runs**, and that is a
    measured decision rather than caution. Matching the caption menu's 97 items against
    our command registry by cleaned label produced exactly **three** hits, and all
    three were WRONG:

        Set CPlane > &Rotate        -> would have run the object `Rotate` command
        Background Bitmap > &Move   -> would have run the object `Move` command
        Background Bitmap > S&cale  -> would have run the object `Scale` command

    Those rows rotate a construction plane and move a background image. A row that
    silently does something else is worse than a row that says it is not built, so the
    dispatch is an explicit, verified table instead -- :data:`CAPTION_ACTIONS`.
    """
    plain = strip_accel(label or "")
    # an ellipsis means "opens a dialog", never a bare command
    plain = plain.replace("...", "")
    return "".join(ch for ch in plain if ch.isalnum()).lower()


#: Caption-menu rows whose meaning we have CHECKED, keyed by
#: ``(path_of_plain_popup_labels, plain_label)``. Anything not in here reports itself.
#:
#: Deliberately small and deliberately not generated. The whole point is that each
#: entry has been read in context -- the key includes the PATH precisely because
#: `Rotate` means one thing under `Set CPlane` and another on its own.
#:
#: Values are either a the reference CAD application command name we can run, or one of the `ACTION_*`
#: constants for rows whose behaviour we have but which are not commands.
ACTION_MAXIMIZE = "__maximize__"

CAPTION_ACTIONS = {
    # Only rows that already run something are routed; every other row is greyed and
    # reports that it is not implemented yet.
    (("Pan, Zoom, and Rotate",), "Zoom"): "Zoom",
    ((), "Set CPlane"): "CPlane",
    ((), "Grid Options..."): "Grid",
    # Double-clicking the title and this menu row share the same maximize behavior.
    ((), "Maximize"): ACTION_MAXIMIZE,
}


def caption_action(path, label):
    """What a caption row does: a command name, an ``ACTION_*``, or None to report.

    `path` is the tuple of PLAIN (accelerator-stripped) popup labels above the row.
    """
    return CAPTION_ACTIONS.get((tuple(path), strip_accel(label or "")))


def viewport_menu_rows(tree, path=()):
    """The rows at `path` inside a caption-menu `tree`, in THE REFERENCE CAD APPLICATION'S OWN ORDER.

    Unlike `menubar.menu_rows`, this preserves source order and separators.

    `path` is a tuple of popup labels. Returns [] for an unknown path rather than
    raising, so a renderer cannot be taken down by a stale path.
    """
    rows = tree
    for step in path:
        found = None
        for kind, label, _accel, payload in rows or ():
            if kind == "popup" and label == step:
                found = payload
                break
        if found is None:
            return []
        rows = found
    return list(rows or ())


def collapse_menu_separators(rows):
    """`rows` with leading, trailing and repeated separators removed.

    Empty groups can create separator runs, so this applies the same cleanup rule as
    :func:`navigate.collapse_separators`.
    """
    out = []
    for row in rows or ():
        if row[0] == "sep":
            if not out or out[-1][0] == "sep":
                continue
            out.append(row)
            continue
        out.append(row)
    while out and out[-1][0] == "sep":
        out.pop()
    return tuple(out)


# ---------------------------------------------------------------------------
# THE OSNAP PANEL, WHERE THE REFERENCE CAD APPLICATION PUTS IT
#
# The Osnap control is docked in the lower half of the left band and shares its
# container with Selection Filters. Its thirteen rows form one uninterrupted list.
#
# This replaces a real bug. The panel used to draw `osnap.PERSISTENT`, which is the
# order of a SENTENCE in the help ("Only Cen, End, Int, Knot, Mid, Near, Perp, Point,
# Quad, Tan, and Vertex object snaps can persist") -- alphabetical, and not the order
# of any control the reference CAD application ships. The old docstring called it "the help's own list -- not
# a sort we invented", which was true and still wrong: it was the right list for the
# wrong question.
# ---------------------------------------------------------------------------

#: The Osnap panel's rows, top to bottom. MEASURED -- see the block above.
OSNAP_PANEL_ORDER = ("End", "Near", "Point", "Mid", "Cen", "Int", "Perp", "Tan",
                     "Quad", "Knot", "Vertex", "Project", "Disable")

#: The two rows that are NOT persistent snap modes but panel-wide flags. They sit in
#: the same list as the modes, with no separator (measured).
OSNAP_PANEL_FLAGS = ("Project", "Disable")

#: Osnap palette: light panel, white checkbox interiors, and grey borders.
OSNAP_PANEL_BG = design.PANEL_BG
OSNAP_PANEL_BORDER = design.BORDER
OSNAP_BOX_BG = design.CHIP_BG
OSNAP_PANEL_TEXT = design.TEXT_MUTED
#: OURS: Project tick color; the reference image does not show a checked box.
OSNAP_TICK = (0x1B, 0x4D, 0x8F)

#: OURS: logical-pixel metrics. UI design: a roomier checkbox list (about 21 px rows
#: with 13 px boxes) under its own header.
OSNAP_PANEL_W = 117
OSNAP_ROW_H = 20
OSNAP_BOX = 13
OSNAP_PAD = 6
OSNAP_TITLE_H = 24
OSNAP_FONT_SIZE = 12.0

#: The Layers list in the right dock band.
#:
#: Layer list and header use white; dividers and dotted row separators use a light
#: blue-grey.
LAYER_LIST_BG = design.LIST_BG
LAYER_GRID = design.BORDER
#: Application-defined colours for the current-layer tick and a layer that is
#: switched OFF. the reference CAD application uses a check glyph and a half-lit bulb; we have neither
#: artwork nor a measured value for them.
LAYER_TICK = (0x20, 0x20, 0x20)
LAYER_DIM = (0x9A, 0x9A, 0x9A)


def osnap_panel_height(n_rows=len(OSNAP_PANEL_ORDER), row_h=None,
                       title_h=None, pad=None):
    """Total pixel height of the panel for `n_rows` rows."""
    row_h = OSNAP_ROW_H if row_h is None else row_h   # read now: uiscale may have rescaled it
    title_h = OSNAP_TITLE_H if title_h is None else title_h   # read now: uiscale may have rescaled it
    pad = OSNAP_PAD if pad is None else pad   # read now: uiscale may have rescaled it
    return title_h + pad + max(0, int(n_rows)) * row_h + pad


def osnap_panel_boxes(labels, x0, y_bottom, width=None,
                      row_h=None, title_h=None, pad=None):
    """``[(label, x0, y0, x1, y1)]`` for each row, TOP row first.

    `y_bottom` is the bottom edge of the whole panel. Rows are laid out downward from
    just under the title, so the first label in `labels` is the topmost -- which is
    what makes the measured order visible rather than reversed.

    Returned in the same order as `labels`, so a caller can zip them together.
    """
    width = OSNAP_PANEL_W if width is None else width   # read now: uiscale may have rescaled it
    row_h = OSNAP_ROW_H if row_h is None else row_h   # read now: uiscale may have rescaled it
    title_h = OSNAP_TITLE_H if title_h is None else title_h   # read now: uiscale may have rescaled it
    pad = OSNAP_PAD if pad is None else pad   # read now: uiscale may have rescaled it
    labels = list(labels or ())
    total = osnap_panel_height(len(labels), row_h, title_h, pad)
    y_top = y_bottom + total
    out = []
    for i, label in enumerate(labels):
        top = y_top - title_h - pad - i * row_h
        out.append((label, x0, top - row_h, x0 + width, top))
    return out


def hit_osnap_panel_row(x, y, boxes):
    """Which panel row contains `(x, y)`, or None.

    Half-open on the top edge so two stacked rows cannot both claim one pixel.
    """
    for label, x0, y0, x1, y1 in boxes or ():
        if x0 <= x < x1 and y0 <= y < y1:
            return label
    return None


# ---------------------------------------------------------------------------
# VIEWPORT DISPLAY MODES
# ---------------------------------------------------------------------------
#
# Supported display modes include "Wireframe
# Sets the viewport display to unshaded wireframe", "Shaded Sets the viewport to opaque
# shaded mode", "Rendered Shades the viewport with a simulation of the rendered view",
# "Ghosted ... translucent", "X-Ray"; SetDisplayMode shortcuts "Ctrl (CMD) + Alt + G
# Ghosted, R Rendered, S Shaded, W Wireframe, X XRay"; options "Viewport Active / All".
# Ghosted and X-Ray are listed so the menu reads like the reference CAD application's, and say they are not
# built. The other built-ins (Technical, Artistic, Pen, ...) are not listed at all.

#: (name, Ctrl+Alt shortcut letter, implemented, Blender shading type)
DISPLAY_MODES = (
    ("Wireframe", "W", True, "WIREFRAME"),
    ("Shaded", "S", True, "SOLID"),
    # OURS, MEASURED: Blender's MATERIAL preview put a dark studio backdrop behind
    # every viewport and drew an unmaterialled cylinder near-black -- not the reference CAD application's
    # Rendered, which keeps the viewport background. "SOLID_LIT" is Solid shading with
    # studio light, shadows, specular and cavity: lit and shadowed, background intact.
    ("Rendered", "R", True, "SOLID_LIT"),
    ("Ghosted", "G", False, None),
    ("X-Ray", "X", False, None),
)
#: the caption-menu placeholder the reference CAD application fills with its display-mode list (MEASURED in the
#: resource tree: the row's label is literally this string)
DISPLAY_MODE_PLACEHOLDER = "display_modes_don't_localize"


def display_mode(text):
    """``(name, implemented, shading)`` for typed text, or ``(None, error)``.

    Case-insensitive; the reference CAD application's `_`/`-` prefixes and a hyphen-less "XRay" accepted; a
    unique prefix is enough (`sh` -> Shaded)."""
    t = (text or "").strip().lstrip("_-").replace("-", "").lower()
    if not t:
        return None, "no display mode given"
    exact = [m for m in DISPLAY_MODES if m[0].replace("-", "").lower() == t]
    hits = exact or [m for m in DISPLAY_MODES
                     if m[0].replace("-", "").lower().startswith(t)]
    if len(hits) == 1:
        name, _k, impl, shading = hits[0]
        return (name, impl, shading), None
    if not hits:
        return None, f"unknown display mode {text!r}"
    return None, f"{text!r} is ambiguous: " + ", ".join(m[0] for m in hits)


def display_mode_for_shading(shading_type, lit=False):
    """The mode a viewport is in, read back from its Blender shading (and whether its
    Solid shading has the Rendered extras -- shadows -- switched on)."""
    if shading_type == "WIREFRAME":
        return "Wireframe"
    if shading_type in ("MATERIAL", "RENDERED") or lit:
        return "Rendered"
    return "Shaded"
