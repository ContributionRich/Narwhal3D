"""Exact curve inputs shared by rail and section prompts (OURS reference protocol).

Legacy references are an object-name string, ``('segment', owner, zero_based_index)``,
or ``('edge', owner, one_based_topology_id)``. Sweep1 individual mouse picks use
``WholeCurvePick``: captured document/UUID/content identity, never a later name lookup.
All references resolve afresh; none contains display geometry.

Python is deliberately only rhino3dm/OCCT interop here; construction and curve
math stay in the existing bridge/kernel. No display samples become inputs.
Bridge imports are late so command registration can import this module safely.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import rhino3dm as r3


@dataclass(frozen=True)
class WholeCurvePick:
    """Accepted UI pick identity/content, not a name lookup or persistent token."""
    object_id: str
    fingerprint: str
    label: str = field(compare=False)
    document: object = field(compare=False, repr=False)

    def __str__(self):
        return self.label


def display_object_id(model, display_name):
    """Resolve the existing UUID->display binding, never the first matching name."""
    ids = [oid for oid, name in model._blender_names.items() if name == display_name]
    if len(ids) != 1 or model.file.Objects.FindId(ids[0]) is None:
        raise CurveInputError("picked display has no unique current document UUID")
    return ids[0]


def capture_display_pick(model, display_name):
    from . import bridge

    oid = display_object_id(model, display_name)
    owner = model.file.Objects.FindId(oid)
    return WholeCurvePick(oid, bridge._freeform_source_hash(owner.Geometry),
                          owner.Attributes.Name or display_name, model.file)


def selection_stamp(model):
    """Reuse existing selection-state replacement identity as a mutation stamp.

    This service does NOT contain chronology. Holding its actual list (not id(list))
    detects even a same-membership select_by_names/SelPrev reapplication without
    modifying selection machinery or allowing pointer reuse after garbage collection.
    """
    from . import selection_state

    return selection_state._entry(model)["current"]


class PickSequence:
    """Command-local accepted individual picks; no selection engine or set sorting."""
    def __init__(self):
        self.picks = []
        self.stamp = None
        self.error = None

    def invalidate(self, reason):
        self.error = reason

    def observe(self, pick, selected_ids, stamp, action, batch=False, previous_stamp=None):
        selected = set(selected_ids)
        if batch:
            self.invalidate("whole-object selection order is ambiguous; use individual picks or typed names")
            return
        if action == "replace":
            old = {p.object_id: p for p in self.picks}
            self.picks = ([old.get(pick.object_id, pick)] if pick and pick.object_id in selected else [])
            self.error = None
        else:
            if self.stamp is not None and self.stamp is not previous_stamp:
                self.invalidate("whole-object selection order is ambiguous; selection was replaced or restored")
            known = {p.object_id for p in self.picks}
            if self.error or selected.difference(known | ({pick.object_id} if pick else set())):
                self.invalidate("whole-object selection order is ambiguous; selection contains unaccepted inputs")
                return
            self.picks = [p for p in self.picks if p.object_id in selected]
            if action == "add" and pick and pick.object_id in selected and pick.object_id not in known:
                self.picks.append(pick)
        if selected != {p.object_id for p in self.picks}:
            self.invalidate("whole-object selection order is ambiguous; selection is not an individual-pick sequence")
            return
        self.stamp = stamp

    def accepted(self, selected_ids, stamp):
        if self.error:
            raise CurveInputError(self.error)
        if self.stamp is not stamp or set(selected_ids) != {p.object_id for p in self.picks}:
            raise CurveInputError("whole-object selection order is ambiguous; selection was replaced or restored")
        return list(self.picks)


class CurveInputError(ValueError):
    """An invalid or unavailable curve input, with a user-facing explanation."""


def _validated(ref):
    if isinstance(ref, WholeCurvePick):
        return ref
    if isinstance(ref, str):
        if not ref.strip():
            raise CurveInputError("curve input needs a non-empty object name")
        return ref
    if not isinstance(ref, tuple) or len(ref) != 3:
        raise CurveInputError(
            "curve input must be a name, ('segment', owner, index), or ('edge', owner, id)")
    kind, owner, index = ref
    if kind not in ("segment", "edge"):
        raise CurveInputError(f"unknown curve input kind {kind!r}; expected segment or edge")
    if not isinstance(owner, str) or not owner.strip():
        raise CurveInputError("curve input owner must be a non-empty object name")
    minimum = 0 if kind == "segment" else 1
    if type(index) is not int or index < minimum:
        raise CurveInputError(
            f"{kind} of {owner}: index {index!r} must be an integer >= {minimum}")
    return ref


def reference_from_selection(triple):
    """Convert a UI ``(owner, 'Segment'|'Edge', index)`` pick into a reference.

    >>> reference_from_selection(('Joined1', 'Segment', 0))
    ('segment', 'Joined1', 0)
    >>> reference_from_selection(('Cone1', 'Edge', 3))
    ('edge', 'Cone1', 3)

    Validate kind, owner and index type/lower bound here; only ``resolve`` can
    check the index against current document topology. Edge IDs are NOT offsets.
    """
    if not isinstance(triple, (tuple, list)) or len(triple) != 3:
        raise CurveInputError("curve selection must be (owner, 'Segment' or 'Edge', index)")
    owner, kind, index = triple
    if kind not in ("Segment", "Edge"):
        raise CurveInputError(f"{kind!r} selection is not a curve segment or BRep edge")
    return _validated((kind.lower(), owner, index))


def owner_name(ref):
    """Return the document owner, NOT permission to delete it.

    ``owner_name(('segment', 'Joined1', 0)) == 'Joined1'``. For DeleteInput,
    protect the owners of ALL component references, even if the same owner also
    appears as a whole-object input. Only whole-object string references whose
    names are not protected may be deleted. Resolving never deletes anything.
    """
    ref = _validated(ref)
    return ref.label if isinstance(ref, WholeCurvePick) else ref if isinstance(ref, str) else ref[1]


def humanlabel(ref):
    """Diagnostic label, retaining the stored index (segments are zero-based).

    ``humanlabel(('edge', 'Cone1', 3)) == 'edge 3 of Cone1'``.
    """
    ref = _validated(ref)
    return ref.label if isinstance(ref, WholeCurvePick) else ref if isinstance(ref, str) else f"{ref[0]} {ref[2]} of {ref[1]}"


def _detached(curve):
    if not isinstance(curve, r3.Curve):
        raise CurveInputError("input is not a curve")
    result = curve.Duplicate()
    if not isinstance(result, r3.Curve) or not result.IsValid:
        raise CurveInputError("could not duplicate a valid exact curve")
    return result


def _segment(geom, index):
    from . import bridge

    if isinstance(geom, r3.PolyCurve):
        count = geom.SegmentCount
        if index >= count:
            raise CurveInputError(f"segment index {index} out of range; {count} segments")
        return _detached(geom.SegmentCurve(index))
    if not (isinstance(geom, r3.PolylineCurve)
            or isinstance(geom, r3.NurbsCurve) and geom.Degree == 1):
        raise CurveInputError("Segment requires a polycurve, polyline, or degree-1 NURBS curve")
    vertices = bridge._polyline_vertices(geom)
    if vertices is None:
        raise CurveInputError("exact polyline vertices are unavailable")
    count = len(vertices) - 1
    if index >= count:
        raise CurveInputError(f"segment index {index} out of range; {max(0, count)} segments")
    # Keep ORIGINAL indices, including degenerate runs. Removing repeated vertices
    # would silently resolve a later pick to the wrong segment.
    a, b = vertices[index], vertices[index + 1]
    if a == b:
        raise CurveInputError(f"segment index {index} is zero-length")
    return _detached(r3.LineCurve(r3.Point3d(*a), r3.Point3d(*b)))


def _edge(geom, index):
    from . import bridge, kernel

    if not isinstance(geom, (r3.Brep, r3.Extrusion, r3.Surface)):
        raise CurveInputError("Edge requires a surface or BRep, not a whole curve or mesh")
    known_proxy = (bool(kernel.master_key_of(geom))
                   or kernel.representation_of(geom) in
                   (kernel.ANALYTIC_MASTER, kernel.MESH_DERIVED))
    if known_proxy:
        # MEASURED bridge._subobject_source can return 'brep' for a stranded
        # master proxy. Refuse BEFORE subobject_parts can traverse its facets.
        source, topology = bridge._subobject_source(geom)
        if source not in ("master", "recipe") or topology is None:
            raise CurveInputError("analytic edge source is unavailable; refusing display proxy edges")
    parts = bridge.subobject_parts(geom)
    if not parts or index not in (edge_id for edge_id, _ in parts["edges"]):
        raise CurveInputError(f"edge id {index} is unavailable in the current topology")
    if known_proxy and parts["source"] not in ("master", "recipe"):
        raise CurveInputError("analytic edge source is unavailable; refusing display proxy edges")
    # IDs may be noncontiguous (cone: 2, 3). Hit-test polylines are NEVER inputs.
    return _detached(bridge.extract_subobject(geom, "edge", index))


def resolve(ctx, ref):
    """Return a detached exact ``r3.Curve`` from ``ctx.geom_by_name(owner)``.

    Examples: ``resolve(ctx, 'Rail1')``,
    ``resolve(ctx, ('segment', 'Joined1', 0))``,
    ``resolve(ctx, ('edge', 'Cone1', 3))``.

    No parent mutation, tessellation, fitting, or parent solid recipe copying.
    Producers should serialize the RESULT with ``bridge.curve_recipe(curve)``
    and nest that curve dict in their own recipe tree, not serialize its owner.
    Unknown names, wrong kinds, stale/out-of-range indices and unavailable exact
    sources raise ``CurveInputError`` (a ``ValueError``) with the input label.
    Names/indices cannot detect a topology edit that reuses the same valid ID.
    """
    ref = _validated(ref)
    label = humanlabel(ref)
    try:
        if isinstance(ref, WholeCurvePick):
            from . import bridge
            if ctx.model.file is not ref.document:
                raise CurveInputError("stale picked document")
            owner = ctx.model.file.Objects.FindId(ref.object_id)
            if owner is None:
                raise CurveInputError("stale picked source UUID is missing")
            if bridge._freeform_source_hash(owner.Geometry) != ref.fingerprint:
                raise CurveInputError("stale picked source geometry changed")
            return _detached(owner.Geometry)
        geom = ctx.geom_by_name(owner_name(ref))
        if geom is None:
            raise CurveInputError("owner no longer exists")
        if isinstance(ref, str):
            return _detached(geom)
        kind, _, index = ref
        return _segment(geom, index) if kind == "segment" else _edge(geom, index)
    except Exception as exc:
        raise CurveInputError(f"{label}: {exc}") from exc
