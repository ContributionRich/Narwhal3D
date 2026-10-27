"""Pure status-bar model: pane order, kinds, and rendered text.

This module has no `bpy` or rhino3dm dependency. `ui.py` owns drawing, while this
plain-Python model defines the fourteen fixed cells and their toggle, alignment,
or readout behavior. Gumball and Auto CPlane include their current alignment in
the displayed text. Cursor coordinates and units are passive readouts; Layer and
Info are actionable readouts; the remaining mode cells toggle state.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# pane kinds
#
# Three shapes cover all 14 panes:
#   * "toggle"     -- a boolean on/off mode; the label is constant and the pane
#                     "lights up" when on (Grid Snap, Ortho, Planar, Osnap,
#                     SmartTrack, Record History, Filter).
#   * "alignment"  -- a toggle that ALSO carries an alignment, and shows it in
#                     the text: "Gumball (CPlane)", "Auto CPlane (Object)".
#   * "readout"    -- displays a value rather than a mode (the CPlane/World
#                     toggle, x/y/z, the units/delta, the Layer, and the Info
#                     pane). Some are clickable (Layer, Info), some are not
#                     (x/y/z, units).
# ---------------------------------------------------------------------------
KIND_TOGGLE = "toggle"
KIND_ALIGNMENT = "alignment"
KIND_READOUT = "readout"

#: the four alignment names `GumballAlignment` documents
#: (commands/gumball.htm, "Gumball menu"), used by the two alignment panes.
ALIGNMENTS = ("CPlane", "Object", "World", "View")


class Pane:
    """A single status-bar pane: identity, kind, and whether it is clickable.

    Immutable by convention -- the pane list is fixed data, and a pane's
    *state* (on/off, value, alignment) is never stored on the pane itself.
    """

    __slots__ = ("id", "label", "kind", "clickable")

    def __init__(self, id, label, kind, clickable):
        self.id = id
        self.label = label
        self.kind = kind
        self.clickable = clickable

    def __repr__(self):  # pragma: no cover - debugging aid
        return (f"Pane(id={self.id!r}, label={self.label!r}, "
                f"kind={self.kind!r}, clickable={self.clickable!r})")


#: The fourteen panes in canonical left-to-right order.
PANES = [
    # "The construction plane/world coordinates toggle." -> a readout of the
    # current mode name ("CPlane" or "World"); clicking it toggles the mode.
    Pane("cplane", "CPlane", KIND_READOUT, clickable=True),
    # "The x/y/z coordinates of the mouse cursor location." A plain display:
    # the help gives it no click action, so it is not clickable.
    Pane("coordinates", "x y z", KIND_READOUT, clickable=False),
    # OURS: v4 promotes the document-unit readout to an interactive selector.
    Pane("units", "Units", KIND_READOUT, clickable=True),
    # "If objects are selected, the Layer pane displays the layer of the
    # selected objects. If no objects are selected, ... the current layer.
    # Click the Layer pane to access quick controls ...". Readout, clickable.
    Pane("layer", "Layer", KIND_READOUT, clickable=True),
    # "Click the Grid Snap pane to toggle grid snap. The pane lights up when
    # the mode is turned on." Boolean toggle.
    Pane("grid_snap", "Grid Snap", KIND_TOGGLE, clickable=True),
    # "Click the Ortho pane to toggle Ortho mode. The pane lights up ..."
    Pane("ortho", "Ortho", KIND_TOGGLE, clickable=True),
    # "Click the Planar pane to toggle Planar mode. The pane lights up ..."
    Pane("planar", "Planar", KIND_TOGGLE, clickable=True),
    # "The Osnap controls let you select which object snaps are currently in
    # effect. When the Osnap panel is off, clicking the Osnap pane brings up
    # the panel. If the panel is already on, the Disable checkbox is toggled."
    # Behaves as a toggle (on = snaps active) that also opens a panel.
    Pane("osnap", "Osnap", KIND_TOGGLE, clickable=True),
    # "Click the SmartTrack pane to toggle SmartTrack mode. The pane lights
    # up ..."
    Pane("smarttrack", "SmartTrack", KIND_TOGGLE, clickable=True),
    # "Click the Gumball pane to toggle Gumball widget display ... The pane
    # lights up when Gumball is turned on." Plus an alignment (CPlane/Object/
    # World/View), shown in the text as "Gumball (CPlane)".
    Pane("gumball", "Gumball", KIND_ALIGNMENT, clickable=True),
    # "Click the Auto CPlane pane to toggle it on and off. The pane lights up
    # when the mode is turned on." Plus an alignment shown in the pane text.
    Pane("auto_cplane", "Auto CPlane", KIND_ALIGNMENT, clickable=True),
    # "Click the Record History pane to toggle history recording on/off. The
    # pane lights up when History recording is turned on."
    Pane("record_history", "Record History", KIND_TOGGLE, clickable=True),
    # "When the Selection Filters panel is off, clicking the Filter pane
    # brings up the panel. If the panel is already on, the Disable checkbox
    # is toggled." Toggle that also opens a panel.
    Pane("filter", "Filter", KIND_TOGGLE, clickable=True),
    # "Click the Info pane to display information about the current the reference CAD application
    # session. The Info pane cycles through the list of specified categories."
    # The reference state uses the "Memory use" category.
    Pane("info", "Info", KIND_READOUT, clickable=True),
    # OURS (UI design): the document's absolute tolerance, on the bar because it is
    # what every exact operation is held to. Clicking opens the Units window, where
    # the tolerance is set.
    Pane("tolerance", "Absolute Tolerance", KIND_READOUT, clickable=True),
]

_PANE_BY_ID = {p.id: p for p in PANES}


def pane(pane_id):
    """The :class:`Pane` with this id, or ``KeyError`` naming the valid ids.

    A KeyError (not a silent ``None``) on an unknown id is deliberate: the
    caller passed a pane that does not exist, and guessing one of the real
    panes would hide the typo.
    """
    try:
        return _PANE_BY_ID[pane_id]
    except KeyError:
        valid = ", ".join(_PANE_BY_ID)
        raise KeyError(f"unknown status-bar pane {pane_id!r}; valid ids: {valid}")


def panes():
    """All fourteen panes in canonical left-to-right order."""
    return list(PANES)


def pane_ids():
    """Pane ids in the bar's canonical left-to-right order."""
    return [p.id for p in PANES]


def is_clickable(pane_id):
    """Whether clicking this pane does something (per cad_window.htm).

    The two cursor/unit readouts are the only panes the help documents with no
    click action; everything else toggles a mode, opens a panel, or (Info)
    cycles its category.
    """
    return pane(pane_id).clickable


#: Characters reserved for each coordinate number, INCLUDING its sign.
#:
#: The readout is the only pane whose text changes width constantly. Fixed-width
#: coordinate fields prevent the remaining status cells from shifting with the cursor.
#:
#: 10 fits a sign, five integer digits and three decimals ("-12345.678"), which
#: covers any model a millimetre document is likely to hold; wider values still
#: render correctly, they just push the row as before.
COORD_FIELD_WIDTH = 10


def _coordinate_text(value):
    """Format a cursor location as ``x     25.075 y    -12.500 z      0.000``.

    Accepts a 3-tuple ``(x, y, z)`` or a dict with ``x``/``y``/``z`` keys.
    Three decimals provide stable millimetre-model precision.

    Each number sits in a FIXED field (:data:`COORD_FIELD_WIDTH`) so the pane's
    width does not change as the cursor moves -- see that constant for why.

    LEFT-aligned inside that field, which matters visually: right-aligning put the
    padding between each label and its number, so the bar read "x        14.667"
    with a gap wide enough that the label looked detached from the value.
    Left-aligning keeps every LABEL at a fixed position -- which stops the row moving --
    while the number stays next to the label it belongs to.
    """
    if isinstance(value, dict):
        x, y, z = value["x"], value["y"], value["z"]
    else:
        x, y, z = value
    w = COORD_FIELD_WIDTH
    return (f"x {float(x):<{w}.3f} "
            f"y {float(y):<{w}.3f} "
            f"z {float(z):<{w}.3f}").rstrip() + " " * _coord_trailing(x, y, z, w)


def _coord_trailing(x, y, z, w):
    """How many spaces the rstrip above removed, so the length stays constant.

    `rstrip` keeps the string from ending in a ragged tail of padding, which some
    renderers centre; the count is added back so :func:`pane_width_chars` and the
    constant-width invariant still hold exactly.
    """
    full = (f"x {float(x):<{w}.3f} "
            f"y {float(y):<{w}.3f} "
            f"z {float(z):<{w}.3f}")
    return len(full) - len(full.rstrip())


def compact_coordinates(value):
    """v7 text for a pixel-fixed slot; padding must not resize adjacent controls."""
    if value is None:
        return 'x -  y -  z -'
    values = (value['x'], value['y'], value['z']) if isinstance(value, dict) else value
    return '  '.join(f'{axis} {float(number):.3f}' for axis, number in zip('xyz', values))


def coordinate_placeholder():
    """The idle readout, padded to the same width as a real one.

    Without this the pane still jumps the moment the cursor enters a viewport,
    because "x y z" is far narrower than the formatted numbers that replace it.
    """
    w = COORD_FIELD_WIDTH
    return (f"x {'-':<{w}} y {'-':<{w}} z {'-':<{w}}")


#: How wide each pane's CELL must be, in characters, to hold the widest text it
#: can ever show. Used to fix the cell geometry so the row cannot move.
#:
#: Padding the STRING was not enough, and this is why: the status bar uses a
#: PROPORTIONAL font, so equal character counts are not equal pixel widths -- "1"
#: is narrower than "8", so the cell geometry must be pinned rather than the text.
#:
#: The three values marked OURS below are for readouts whose content is arbitrary
#: (a layer name, a unit name, an Info category). A value wider than its cell makes
#: that cell grow, exactly as before -- the guarantee is only that the CURSOR
#: readout, which is the one that changes constantly, never moves anything.
_PANE_WIDTH_CHARS = {
    # "CPlane" / "World"
    "cplane": 6,
    # the fixed-width coordinate readout, whose length is arithmetic, not a guess
    "coordinates": None,          # computed below from _coordinate_text
    "units": 12,                  # OURS: "Millimeters" is 11, "Centimeters" 11
    "layer": 12,                  # OURS: a long layer name will widen this cell
    "grid_snap": 9,
    "ortho": 5,
    "planar": 6,
    "osnap": 5,
    "smarttrack": 10,
    # "Gumball (CPlane)" -- CPlane is the longest of CPlane/Object/World/View
    "gumball": 16,
    "auto_cplane": 20,            # "Auto CPlane (CPlane)"
    "record_history": 14,
    "filter": 6,
    "info": 12,                   # OURS: Info cycles through categories
    "tolerance": 26,              # OURS: "Absolute Tolerance: 0.001"
}


def pane_width_chars(pane_id):
    """How many characters wide `pane_id`'s cell must be.

    The coordinate pane's width is derived from the formatter rather than written
    down, so the two cannot drift: change :data:`COORD_FIELD_WIDTH` and the cell
    follows.
    """
    pane(pane_id)                 # raises for an unknown id, as elsewhere
    width = _PANE_WIDTH_CHARS.get(pane_id)
    if width is None:
        width = len(_coordinate_text((0.0, 0.0, 0.0)))
    return int(width)


def render(pane_id, state):
    """The text this pane shows for ``state``.

    ``state`` maps pane id -> its value:
      * toggle      -> a bool (on/off). The label is constant; on/off only
                       changes whether the pane *lights up* (see
                       :func:`is_active`), never the text.
      * alignment   -> a dict ``{"on": bool, "alignment": str}`` or just the
                       alignment string. Rendered as ``"Label (Alignment)"``.
      * readout     -> the value string (or a 3-tuple/dict for coordinates).
                       When the value is absent, the pane's base label is
                       shown -- which is exactly how an idle bar reads:
                       "x y z" with no cursor, "Units" with no value, etc.
    """
    p = pane(pane_id)
    value = state.get(pane_id)

    if p.kind == KIND_TOGGLE:
        return p.label

    if p.kind == KIND_ALIGNMENT:
        alignment = value.get("alignment") if isinstance(value, dict) else value
        alignment = alignment or "CPlane"
        return f"{p.label} ({alignment})"

    # readout
    if pane_id == "coordinates":
        if value is None:
            # NOT `p.label` ("x y z"): that is far narrower than a formatted
            # reading, so the pane -- and every pane after it -- would jump the
            # instant the cursor entered a viewport.
            return coordinate_placeholder()
        return _coordinate_text(value)
    if value is None:
        return p.label
    return str(value)


def is_active(pane_id, state):
    """Whether this pane "lights up" for ``state``.

    Toggles light up when on (cad_window.htm: "The pane lights up when the
    mode is turned on"). Alignment panes light up when the widget is on.
    Readouts are never "lit" -- they display, they do not toggle -- so this
    is always False for them.
    """
    p = pane(pane_id)
    value = state.get(pane_id)

    if p.kind == KIND_TOGGLE:
        return bool(value)
    if p.kind == KIND_ALIGNMENT:
        if isinstance(value, dict):
            return bool(value.get("on", True))
        return value is not None
    return False


def screenshot_state():
    """Return the canonical fourteen-pane reference state.

    Toggles are off because their text is unchanged by state. The two alignment
    panes use Gumball on CPlane and Auto CPlane on Object.
    """
    return {
        "cplane": "CPlane",
        "coordinates": None,
        "units": "Millimeters",
        "layer": "Default",
        "grid_snap": False,
        "ortho": False,
        "planar": False,
        "osnap": False,
        "smarttrack": False,
        "gumball": {"on": True, "alignment": "CPlane"},
        "auto_cplane": {"on": True, "alignment": "Object"},
        "record_history": False,
        "filter": False,
        "info": "Memory use",
    }
