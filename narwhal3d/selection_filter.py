"""the reference CAD application's Selection Filter model. Pure Python, no bpy.

MEASURED (``commands/selectionfilter.htm``):

  "The Selection Filter restricts any selection mode (SelWindow, SelCrossing,
   SelAll, etc.) to specified object types."

  Options: Points, Curves, Surfaces, Polysurfaces, SubDs, Meshes, Annotations
  (Dimensions, Leaders, Dots, Text), Lights, Blocks, Control points, Point
  clouds, Hatches, Others (clipping planes, cages, detail views, ...).

  "Disable -- Turns off selection filters."
  "Sub-objects (When enabled): Points/Vertices, Curves/Edges, Surfaces/Faces."
  "Selection filters will be automatically disabled after four failed picks"
  (Advanced Settings ``RepeatDisableCount``, default 4, 0 to disable) -- a
  session/UI behaviour, not modelled here.

A pure model of WHICH OBJECT TYPES are on, whether the filter is disabled
(the "Disable" option), and the optional sub-object mode ("Sub-objects").
``allows(object_type)`` gates a selection: when the filter is disabled every
type passes; when it is on, only the enabled types pass.  An unrecognised
object type is refused with ``ValueError`` (never a guess).
"""
from __future__ import annotations

# The 13 documented Selection Filter object types (commands/selectionfilter.htm).
# Canonical string identifiers corresponding to the documented display names.
OBJECT_TYPES = (
    "point",          # Points
    "curve",          # Curves
    "surface",        # Surfaces
    "polysurface",    # Polysurfaces
    "subd",           # SubDs
    "mesh",           # Meshes
    "annotation",     # Annotations (Dimensions, Leaders, Dots, Text)
    "light",          # Lights
    "block",          # Blocks
    "control_point",  # Control points
    "point_cloud",    # Point clouds
    "hatch",          # Hatches
    "other",          # Others (clipping planes, cages, detail views, ...)
)

# The three documented "Sub-objects" modes (Points/Vertices, Curves/Edges,
# Surfaces/Faces).
SUBOBJECT_TYPES = ("vertex", "edge", "face")


class SelectionFilter:
    """Which object types a selection is restricted to."""

    def __init__(self, on=(), disabled=False, subobject=None):
        self._on = set()
        self.disabled = bool(disabled)
        self.subobject = self._check_subobject(subobject)
        for t in on:
            self.enable_type(t)

    # -- validation (never a guess) -------------------------------------
    @staticmethod
    def _check_type(object_type):
        if not isinstance(object_type, str) or object_type not in OBJECT_TYPES:
            raise ValueError(
                f"unknown object type {object_type!r} -- use one of {OBJECT_TYPES}")
        return object_type

    @staticmethod
    def _check_subobject(subobject):
        if subobject is None or subobject in SUBOBJECT_TYPES:
            return subobject
        raise ValueError(
            f"unknown sub-object mode {subobject!r} -- use one of "
            f"{SUBOBJECT_TYPES} or None")

    # -- which types are on ---------------------------------------------
    @property
    def types(self):
        """The enabled object types, in documented order."""
        return tuple(t for t in OBJECT_TYPES if t in self._on)

    def enable_type(self, object_type):
        self._on.add(self._check_type(object_type))

    def disable_type(self, object_type):
        self._on.discard(self._check_type(object_type))

    def set_types(self, object_types):
        """Replace the enabled set with ``object_types`` (an iterable)."""
        self._on.clear()
        for t in object_types:
            self.enable_type(t)

    def clear(self):
        self._on.clear()

    # -- the gate --------------------------------------------------------
    def allows(self, object_type):
        """Whether ``object_type`` passes the current filter.

        Disabled filter -> every type passes.  Enabled filter -> only the
        enabled types pass.  Raises ``ValueError`` on an unrecognised type.
        """
        self._check_type(object_type)
        if self.disabled:
            return True
        return object_type in self._on

    # -- reporting -------------------------------------------------------
    def describe(self):
        if self.disabled:
            base = "Selection filter: off (all object types allowed)"
        else:
            on = ", ".join(self.types) if self.types else "no types (nothing selected)"
            base = f"Selection filter: on -- {on}"
        if self.subobject:
            base += f"; sub-object: {self.subobject}"
        return base

    # -- round-trip ------------------------------------------------------
    def to_dict(self):
        return {
            "on": list(self.types),
            "disabled": self.disabled,
            "subobject": self.subobject,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(on=d.get("on", ()), disabled=d.get("disabled", False),
                   subobject=d.get("subobject"))
