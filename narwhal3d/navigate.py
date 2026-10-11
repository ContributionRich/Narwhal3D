"""Pure view-navigation rules with no `bpy` dependency.

Narwhal 3D uses projection-sensitive right-button navigation: an unmodified drag
rotates perspective views and pans parallel views. Modifier combinations select
pan, zoom, dolly, tilt, camera rotation, or lens adjustment. This module keeps
that mapping testable without launching Blender; `ui.py` maps supported actions
to Blender operators.

======================================  ==========================================
Input                                   Rhino does
======================================  ==========================================
RMB-drag                                **rotate** a perspective view,
                                        **pan** a parallel view
Shift + RMB-drag                        pan a perspective view
Ctrl + RMB-drag up/down                  zoom the view in/out
Alt + RMB-drag up/down                   pan a perspective view in/out
Shift + Alt + RMB-drag up/down           tilt the view
Ctrl + Shift + RMB-drag                  force a parallel view to rotate
Ctrl + Alt + RMB-drag                    rotate the camera
Ctrl + Shift + Alt + RMB-drag up/down    adjust the camera lens length
Wheel / MMB scroll                       zoom the view in/out
Alt + scroll                             pan a perspective view in/out
======================================  ==========================================

Not every row is implemented, and this module does not pretend otherwise:
:data:`BLENDER_OP` maps an action to the Blender operator that performs it and
holds ``None`` for the ones we have not built. ``ui.py`` reports those as "not
implemented yet" rather than silently doing something else -- the same honesty
rule the command options follow.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# actions (what the viewport should do)
# ---------------------------------------------------------------------------

#: orbit the camera about the target
ROTATE = "rotate"
#: slide the view in its own plane
PAN = "pan"
#: zoom in/out
ZOOM = "zoom"
#: Rhino calls this "pan the perspective view in/out" -- moving the camera
#: along its view axis, which is a dolly, not a zoom.
DOLLY = "dolly"
#: roll about the view axis
TILT = "tilt"
#: change the lens focal length of a perspective view
LENS = "lens"
#: orbit the camera about itself
ROTATE_CAMERA = "rotate_camera"
#: nothing to do
NONE = "none"

ACTIONS = (ROTATE, PAN, ZOOM, DOLLY, TILT, LENS, ROTATE_CAMERA, NONE)

# ---------------------------------------------------------------------------
# action -> the Blender operator that performs it
#
# `None` means Rhino supports the action but no equivalent interaction is implemented.
# Blender 5.2.2 provides view3d.rotate, view3d.move, view3d.zoom, view3d.dolly,
# and view3d.view_roll.
# ---------------------------------------------------------------------------

BLENDER_OP = {
    ROTATE: "view3d.rotate",
    PAN: "view3d.move",
    ZOOM: "view3d.zoom",
    DOLLY: "view3d.dolly",
    # Rhino tilts by DRAGGING (with 5-degree snapping when you drag along the
    # view border). Blender's view3d.view_roll takes a fixed `angle`/`type` and
    # is not a drag-modal: rotate/move/zoom/dolly expose
    # `use_cursor_init` (the signature of a drag-modal), view_roll exposes
    # ('angle', 'type'). Binding a drag to it would roll a fixed step per click
    # and look implemented while behaving nothing like Rhino, so it stays None
    # until it gets a real modal implementation.
    TILT: None,
    # Rhino's RotateCamera orbits the camera in place. Blender's view3d.rotate
    # orbits about the pivot instead, so it is NOT a substitute -- mapping it
    # here would make the option look implemented while doing something else.
    ROTATE_CAMERA: None,
    # Lens length is space.lens, not a navigation operator; needs its own modal.
    LENS: None,
    NONE: None,
}


def blender_op(action):
    """The Blender operator id for `action`, or ``None`` if unimplemented."""
    return BLENDER_OP.get(action)


def is_implemented(action):
    """True when we can actually perform `action`."""
    return BLENDER_OP.get(action) is not None


# ---------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------

def rmb_action(perspective: bool, ctrl: bool = False, shift: bool = False,
               alt: bool = False):
    """What a right-button drag does, per the table in this module's docstring.

    `perspective` is the dragged pane's projection -- in Blender,
    ``space.region_3d.is_perspective``. It is only consulted for an unmodified
    drag, which is the one rule that depends on it:

    >>> rmb_action(True)            # a perspective pane orbits
    'rotate'
    >>> rmb_action(False)           # Top/Front/Right pan instead
    'pan'

    Combinations are tested most-specific first, so ``Ctrl+Shift+Alt`` cannot be
    captured by the ``Ctrl+Shift`` rule.
    """
    if ctrl and shift and alt:
        return LENS
    if ctrl and alt:
        return ROTATE_CAMERA
    if shift and alt:
        return TILT
    if ctrl and shift:
        # "Force parallel view to rotate" -- so this rotates whatever the
        # projection is, which is exactly why it must beat the bare rule below.
        return ROTATE
    if ctrl:
        return ZOOM
    if alt:
        return DOLLY
    if shift:
        # "Press Shift to pan perspective views". A parallel view already pans
        # on a bare drag, so Shift is a no-change there rather than a conflict.
        return PAN
    # THE core Rhino rule, and the one users feel immediately.
    return ROTATE if perspective else PAN


# ---------------------------------------------------------------------------
# WHAT A RIGHT-BUTTON PRESS TURNS OUT TO HAVE BEEN
#
# The right button has THREE jobs in Rhino, and which one you get is decided by
# what happens after the press -- so none of them can be settled at press time.
#
# MEASURED (user_interface/keyboard modifiers.htm), under "Enter":
#     "To accept command-line input, do one of the following: Press the Enter key.
#      Press the Spacebar. Click the right mouse button."
#   -> a right-CLICK is Enter. With nothing typed, Rhino's Enter repeats the last
#      command, so a short stationary right-click acts as Enter.
#
# MEASURED (options/mouse.htm), under "Right mouse button":
#     "Enable click-and-hold context menus -- Displays a context menu with right
#      mouse button click, hold and RELEASE in a viewport."
#     "Context menu delay ___ milliseconds -- Number of milliseconds you must click
#      and hold the right mouse button to display the context menus. Set 0 to make
#      a right-click immediately display the context menu and stop repeating the
#      last command."
#   -> the menu appears on RELEASE, not during the hold. So measuring the elapsed
#      time at release is not an approximation of Rhino, it is what Rhino does.
#      And delay 0 is a documented special case that removes the Enter meaning
#      entirely ("stop repeating the last command").
#
# MEASURED (user_interface/shortcuts.htm, already in `rmb_action`): dragging the
# right button navigates the view.
#
# So: moved -> drag; held past the delay -> menu; otherwise -> Enter.
# ---------------------------------------------------------------------------

#: The right button turned out to be a view drag.
RMB_DRAG = "drag"
#: It turned out to be Enter (accept input / repeat the last command).
RMB_ENTER = "enter"
#: It turned out to be a context menu.
RMB_MENU = "menu"

RMB_OUTCOMES = (RMB_DRAG, RMB_ENTER, RMB_MENU)

#: Default delay before a stationary right-button press opens a context menu.
DEFAULT_CONTEXT_MENU_DELAY_MS = 250

#: OURS. How far the pointer may move and still count as a click rather than a
#: drag. `select.is_click` uses 3 px for the same distinction on the LEFT button;
#: the right button gets a little more room because a navigation drag is a
#: deliberate sweep, and a few stray pixels during a click should not orbit the
#: view. `options/mouse.htm` documents drag thresholds for OBJECTS and CONTROL
#: POINTS, not for this.
RMB_DRAG_THRESHOLD_PX = 4.0


def rmb_release_action(held_ms, moved_px,
                       delay_ms=DEFAULT_CONTEXT_MENU_DELAY_MS,
                       drag_threshold_px=RMB_DRAG_THRESHOLD_PX):
    """What a right-button press turned out to be, decided at release.

    Returns one of :data:`RMB_OUTCOMES`. See the block comment above for the three
    quotes this encodes.

    Movement wins over time: a slow navigation drag is still a drag, however long
    it took, because the view has already been moved by the time the button comes
    up and calling it a menu would both discard that and open something.

    `delay_ms == 0` is the documented special case -- "Set 0 to make a right-click
    immediately display the context menu and stop repeating the last command" --
    so a stationary press can then never be Enter.
    """
    if moved_px > drag_threshold_px:
        return RMB_DRAG
    # `delay_ms == 0` needs no branch of its own: any hold satisfies `>= 0`, so a
    # stationary press can never come out as Enter, which is exactly what "Set 0 to
    # make a right-click immediately display the context menu and stop repeating the
    # last command" asks for. An explicit `delay_ms <= 0` test was here first and
    # mutation testing showed it was unreachable -- removing it changed no answer.
    # (Negative delays cannot arrive: `ui.set_context_menu_delay_ms` refuses them.)
    if held_ms >= delay_ms:
        return RMB_MENU
    return RMB_ENTER


#: The three context menus, MEASURED from user_interface/rhino_window.htm:
#: "One of the three context menus appears when you click, hold, and release the
#: right mouse button in a viewport." -- and which one depends on the selection.
MENU_VIEWPORT = "Viewport"
MENU_OBJECT = "Object"
MENU_CONTROL_POINT = "Control Point"

CONTEXT_MENUS = (MENU_VIEWPORT, MENU_OBJECT, MENU_CONTROL_POINT)

#: Each context menu is represented explicitly so unsupported rows can be reported
#: rather than silently skipped.
CONTEXT_MENUS_IMPLEMENTED = CONTEXT_MENUS


# ---------------------------------------------------------------------------
# WHAT IS *IN* THE THREE CONTEXT MENUS
#
# Compatibility labels retain their `&` accelerator marks; :func:`display_label`
# removes those marks for drawing. Unsupported rows remain visible and report that
# they are unavailable instead of silently disappearing.
# ---------------------------------------------------------------------------

#: A separator row. :func:`collapse_separators` removes leading, repeated, and
#: trailing separators because the conditional head can otherwise create runs.
SEPARATOR = None

#: Item actions that are NOT a Rhino command name.
ACTION_REPEAT = "repeat"        #: the `&Repeat X` / `&Enter` slot
ACTION_CUSTOMISE = "customise"  #: `Add items to this menu...` -> an Options page


class ContextItem:
    """One row of a context menu.

    `label` is Rhino's verbatim text, `command` the Rhino command the row runs (or
    None), and `action` marks the two rows that are not commands at all.
    """

    __slots__ = ("label", "command", "action")

    def __init__(self, label, command=None, action=None):
        self.label = label
        self.command = command
        self.action = action

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"ContextItem({self.label!r}, command={self.command!r})"

    def __eq__(self, other):
        return (isinstance(other, ContextItem) and self.label == other.label
                and self.command == other.command and self.action == other.action)


#: Cut/Copy/Paste form the common head of all three menus.
_HEAD_CLIPBOARD = (
    ContextItem("Cu&t", "Cut"),
    ContextItem("&Copy", "CopyToClipboard"),
    ContextItem("&Paste", "Paste"),
)

#: The per-menu tail, in code order. Keyed by the same names as :data:`CONTEXT_MENUS`.
_MENU_TAILS = {
    MENU_VIEWPORT: (
        ContextItem("Sho&w Selected Objects", "ShowSelected"),
        ContextItem("&Unlock Selected Objects", "UnlockSelected"),
        ContextItem("Show &Hidden Points", "ShowPt"),
        ContextItem("Points O&ff", "PointsOff"),
    ),
    MENU_OBJECT: (
        ContextItem("&Hide Objects", "Hide"),
        ContextItem("&Lock Objects", "Lock"),
        ContextItem("C&ontrol Points On", "PointsOn"),
        SEPARATOR,
        # These three entries are restricted to the Object menu.
        ContextItem("Object Propertie&s", "Properties"),
        ContextItem("Object &Information", "What"),
        ContextItem("Object &Material", "MaterialEditor"),
    ),
    MENU_CONTROL_POINT: (
        ContextItem("&Hide Selected Points", "HidePt"),
        ContextItem("Points O&ff", "PointsOff"),
    ),
}

#: Default user-customisable block between the Repeat slot and Cut.
_MENU_USER_BLOCK = {
    MENU_VIEWPORT: (
        ContextItem("Add items to this menu...", action=ACTION_CUSTOMISE),
    ),
    MENU_OBJECT: (
        ContextItem("Add items to this menu...", action=ACTION_CUSTOMISE),
    ),
    MENU_CONTROL_POINT: (
        ContextItem("Select connected points", "SelConnected"),
        ContextItem("Add items to this menu...", action=ACTION_CUSTOMISE),
    ),
}


def display_label(label):
    """Rhino's verbatim label with its `&` accelerator marks removed.

    `&&` is a literal ampersand in a Win32 menu string, so it collapses to one --
    no current label needs that, and getting it wrong later would be invisible.
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


def collapse_separators(items):
    """`items` with leading, trailing and repeated separators removed.

    Reproduces Rhino's own appender, which refuses to add a separator when the last
    item already is one. Needed rather than decorative: the head's first slot is
    conditional, so real runs of separators occur.
    """
    out = []
    for item in items or ():
        if item is SEPARATOR:
            if not out:
                continue            # nothing to separate yet
            if out[-1] is SEPARATOR:
                continue            # Rhino's early return
            out.append(SEPARATOR)
            continue
        out.append(item)
    while out and out[-1] is SEPARATOR:
        out.pop()
    return tuple(out)


def context_menu_items(kind, last_command=None):
    """The rows of context menu `kind`, in Rhino's own order. () for an unknown kind.

    Structure, all MEASURED as code order in `CRhinoView::OnContextMenu`:

        Repeat/Enter, separator, [user block], separator, Cut/Copy/Paste,
        separator, [the menu's own items]

    `last_command` fills the first slot: DERIVED that the slot reads `&Enter` with
    nothing to repeat and `&Repeat <name>` otherwise -- the two localised strings
    (0x180912F2D, 0x1809131B1) share one buffer and only one can be shown, but the
    predicate that chooses between them was not pinned, so this is flagged.
    """
    if kind not in CONTEXT_MENUS:
        return ()
    if last_command:
        head = ContextItem(f"&Repeat {last_command}", action=ACTION_REPEAT)
    else:
        head = ContextItem("&Enter", action=ACTION_REPEAT)
    items = ((head, SEPARATOR)
             + tuple(_MENU_USER_BLOCK.get(kind, ())) + (SEPARATOR,)
             + _HEAD_CLIPBOARD + (SEPARATOR,)
             + tuple(_MENU_TAILS.get(kind, ())))
    return collapse_separators(items)


def context_menu_commands(kind):
    """Every Rhino command name menu `kind` can run, deduplicated, in order.

    For the audit that checks each row either runs or reports -- it must never be
    possible to add a row whose command nobody has checked for.
    """
    seen = []
    for item in context_menu_items(kind, last_command="X"):
        if item is SEPARATOR or not item.command:
            continue
        if item.command not in seen:
            seen.append(item.command)
    return tuple(seen)


def context_menu_for(has_selection=False, has_control_points=False):
    """Which of the three context menus a right-click should raise.

    MEASURED (user_interface/rhino_window.htm):
        "When nothing is in selection, the Viewport Context Menu appears."
        "When objects are in selection, the Object Context Menu appears."
        "When control points are in selection, the Control Point Context menu
         appears."

    Control points win over objects: an object with its points on is still
    selected, so testing the object first would never reach the grip menu.
    """
    if has_control_points:
        return MENU_CONTROL_POINT
    if has_selection:
        return MENU_OBJECT
    return MENU_VIEWPORT


# ---------------------------------------------------------------------------
# THE MIDDLE MOUSE BUTTON  (options/mouse.htm, "Middle mouse button")
#
# Rhino makes MMB configurable, and the page lists FOUR things it can be set to:
#
#   * "Most-recently used commands popup menu -- Pops up two-part menu at the
#      cursor location. You can list your favorite commands in the top section.
#      The bottom section is the list of most recent commands used."
#   * "Popup this Toolbar -- Choose a toolbar or a container to pop up at the
#      cursor location."
#   * "Run this Macro -- Lists a series of commands that run when you click the
#      middle mouse button."
#   * "Manipulate view -- Click and hold to access the default middle button
#      actions: Popup menu, Popup this toolbar, or Run this macro."
#
# Under "Manipulate view" there are three sub-modes, and the help gives the full
# modifier table for each, verbatim:
#
#   "Pan (press Alt or Shift to rotate; press Ctrl to zoom)"
#   "Rotate (press Shift to pan; press Ctrl to zoom)"
#   "Swap pan and zoom (press Ctrl to pan; press Shift to zoom)"
#
#: The compatibility documentation names the available modes but not a factory
#: default, so :data:`DEFAULT_MMB_MODE` is a project choice kept in one place.
# ---------------------------------------------------------------------------

#: MMB set to manipulate the view. The three sub-modes are the help's own names.
MMB_PAN = "Pan"
MMB_ROTATE = "Rotate"
MMB_SWAP = "Swap pan and zoom"

#: MMB set to do something other than manipulate the view. Recognised so a caller
#: can report them rather than silently panning instead.
MMB_POPUP_MENU = "Most-recently used commands popup menu"
MMB_POPUP_TOOLBAR = "Popup this Toolbar"
MMB_MACRO = "Run this Macro"

MMB_MODES = (MMB_PAN, MMB_ROTATE, MMB_SWAP,
             MMB_POPUP_MENU, MMB_POPUP_TOOLBAR, MMB_MACRO)

#: The view-manipulation modes, which are the ones :func:`mmb_action` can answer.
MMB_VIEW_MODES = (MMB_PAN, MMB_ROTATE, MMB_SWAP)

#: Modes that require unavailable UI: an MRU popup menu, floating toolbar, or macro
#: runner. They are reported rather than substituted with pan.
MMB_NOT_IMPLEMENTED = (MMB_POPUP_MENU, MMB_POPUP_TOOLBAR, MMB_MACRO)

#: `Pan` is the application default because the help describes it first and its
#: unmodified action cannot otherwise be reached in this UI; rotate already uses RMB drag.
DEFAULT_MMB_MODE = MMB_PAN

#: True, and it matters: `Ctrl` means ZOOM in BOTH `Pan` and `Rotate` mode
#: ("press Ctrl to zoom" appears in both), so that one binding is safe whatever the
#: default turns out to be. Only `Swap pan and zoom` moves it.
CTRL_MMB_IS_ZOOM_IN_BOTH_DEFAULTS = True


def mmb_action(mode=DEFAULT_MMB_MODE, ctrl: bool = False,
               shift: bool = False, alt: bool = False):
    """What an MMB drag does, for `mode` and the held modifiers.

    Returns one of :data:`ACTIONS`. A `mode` that is not a view-manipulation mode
    returns :data:`NONE` -- the caller must then report that mode as not
    implemented rather than doing something else.

    The three tables are transcribed from `options/mouse.htm` and nothing is
    inferred; note that Rhino gives `Pan` mode TWO rotate modifiers ("press Alt or
    Shift to rotate"), which is why alt and shift are both tested there.
    """
    if mode not in MMB_VIEW_MODES:
        return NONE

    if mode == MMB_PAN:
        # "Pan (press Alt or Shift to rotate; press Ctrl to zoom)"
        if ctrl:
            return ZOOM
        if alt or shift:
            return ROTATE
        return PAN

    if mode == MMB_ROTATE:
        # "Rotate (press Shift to pan; press Ctrl to zoom)"
        if ctrl:
            return ZOOM
        if shift:
            return PAN
        return ROTATE

    # "Swap pan and zoom (press Ctrl to pan; press Shift to zoom)" -- the page
    # spells out that this swaps Shift and Ctrl relative to the mode above:
    # "Shift + middle mouse button zooms the view. Ctrl + middle mouse button
    # pans the view."
    if ctrl:
        return PAN
    if shift:
        return ZOOM
    return ROTATE


def mmb_message(mode):
    """What to tell the user when `mode` is one we cannot honour, else None."""
    if mode in MMB_NOT_IMPLEMENTED:
        return (f"the middle mouse button's {mode!r} mode is not implemented yet "
                f"(it needs UI we have not built)")
    if mode not in MMB_MODES:
        return f"{mode!r} is not a supported middle-mouse-button mode"
    return None


def scroll_action(ctrl: bool = False, shift: bool = False, alt: bool = False):
    """What the wheel does. Alt+scroll dollies; everything else zooms.

    Blender already binds a bare wheel to ``view3d.zoom``, which matches Rhino,
    so this exists for the Alt case and to make the agreement explicit and
    tested rather than accidental.
    """
    if alt:
        return DOLLY
    return ZOOM


# ---------------------------------------------------------------------------
# Blender bindings we must switch off, or they fight Rhino's right button
#
# A `blender -b` read is not trustworthy for keymaps because per-mode keymaps
# materialize lazily; a background read reported zero RIGHTMOUSE and wheel items,
# which cannot be true of a factory configuration. Recorded as (keymap name, ctrl, shift, alt, idname).
#
# `transform.translate` is the dangerous one: a Shift+RMB drag would MOVE
# geometry in Blender without updating the rhino3dm document, so Save would
# write stale coordinates. Navigation therefore cannot simply be added alongside
# these bindings.
# ---------------------------------------------------------------------------

RMB_COMPETITORS = (
    ("Object Mode", False, False, False, "wm.call_menu"),        # context menu
    ("3D View", False, True, False, "view3d.cursor3d"),          # Shift+RMB
    ("3D View", False, True, False, "transform.translate"),      # Shift+RMB drag
    ("3D View", True, False, False, "view3d.select_lasso"),      # Ctrl+RMB
    ("3D View", True, True, False, "view3d.select_lasso"),       # Ctrl+Shift+RMB
)

#: keymaps that hold a competitor, so callers know what to walk
RMB_COMPETITOR_KEYMAPS = tuple(dict.fromkeys(k[0] for k in RMB_COMPETITORS))


def is_rmb_competitor(keymap_name: str, idname: str) -> bool:
    """True if this keymap item must be disabled for Rhino navigation to work.

    Matched on (keymap, operator) rather than on the modifiers: Blender ships
    several items that differ only in modifiers or in ``value``
    (PRESS vs CLICK_DRAG), and every one of them competes with the right button.
    """
    return any(name == keymap_name and idname == op
               for name, _c, _s, _a, op in RMB_COMPETITORS)
