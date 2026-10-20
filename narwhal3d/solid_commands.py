"""Solid and surface commands backed by exact geometry producers.

This module exposes edge fillets and chamfers, planar capping, sweeps, sub-object moves,
and additional analytic primitives. It registers its command classes in
``commands.COMMANDS`` when imported.

Every solid-producing command returns an analytic, airtight OCCT-backed result or reports
the kernel's refusal. These commands never substitute a faceted mesh operation for exact
solid geometry.
"""
import math
import types

import rhino3dm as r3

from . import bridge, commands
from . import options as opts
from .commands import OBJECTS, OBJECTS_MULTI, POINT, Command, _num

try:
    from . import kernel
except ImportError:  # pragma: no cover - kernel is a sibling package
    kernel = None


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

def _replace(ctx, name, geom, base):
    """Swap the document object `name` for `geom` (added as `base`N on the same layer).

    The undo snapshot is taken BEFORE the input is removed. If the snapshot came after
    the removal, as it would if only `add_geometry` triggered it, undo could not bring
    the input back.
    """
    layer = commands._layer_index_of(ctx, name)
    note = getattr(ctx, "note_change", None)
    if callable(note):
        note(base)
    ob = ctx.object_by_name(name) if hasattr(ctx, "object_by_name") else None
    if ob is not None:
        commands._remove_object_from_scene(ob)
    oid = ctx.model.object_id_by_name(name)
    if oid is not None:
        ctx.model.remove_object(oid)
    _, display = ctx.add_geometry(geom, base, layer)
    return display


def _edge_at(operand, point, tol):
    """The stable id of the edge of exact solid `operand` that `point` lies on.

    The pick must be within `tol` of an edge (exact distance). A point on a face, off the
    solid, or at a vertex or seam (on two edges at once) raises ValueError, naming the
    problem. Acting on the wrong edge of a part is a silent, expensive mistake.
    """
    occt = bridge._occt()
    near = min((occt.distance_to_shape(e, point)
                for e in occt._unique_edges(operand)), default=math.inf)
    where = tuple(round(c, 4) for c in point)
    if near > tol:
        raise ValueError(f"the pick {where} is not on an edge of the solid "
                         f"({near:.4g} away)")
    return bridge._edgepick().pick(occt.edge_curves(operand), point)


def _exact_or_none(out):
    """`out` if it is an honest, airtight OCCT-backed solid, else None.

    Both bridge producers already check airtightness. This repeats the check at the
    command boundary on purpose: if a producer ever regresses, the command must refuse
    rather than put an object in the document that only looks right.
    """
    if out is None or kernel is None:
        return out
    occt = bridge._occt()
    master = kernel.master_of(out)
    if master is None or occt is None or not occt.is_airtight(master):
        return None
    if kernel.honesty_problem(out):
        return None
    return out


# ---------------------------------------------------------------------------
# FilletEdge / ChamferEdge
# ---------------------------------------------------------------------------

class _EdgeBevelCmd(Command):
    """Shared implementation of ``FilletEdge`` and ``ChamferEdge``.

    The size option applies one radius or distance to every picked edge, and the exact
    result replaces the input solid. Other declared modes report that they are not yet
    implemented and leave the document unchanged.

    The solid is selected first, then each edge is identified by a point on it. Picks must
    lie on exactly one edge within document tolerance; face, off-solid, vertex, and seam
    picks are refused to avoid modifying the wrong edge. The size defaults to 1. Enter with
    no edge selected ends the command without changing the document.
    """
    chamfer = False
    verb = "fillet"                 # "Select edges to <verb>"
    past = "Filleted"
    size_option = "NextRadius"
    size_prompt = "Fillet radius"
    size_word = "radius"
    preselect_names = 1

    def __init__(self):
        super().__init__()
        self._picks = []            # world points, one per picked edge
        self._edges = []            # the edge index each pick resolved to
        self._operand = None        # the exact OCCT solid the picks are resolved against

    @property
    def prompt(self):
        if self.pending_option is not None and self.pending_option.name == self.size_option:
            return f"{self.size_prompt} <{_num(self.opt[self.size_option])}>:"
        return super().prompt

    def _solid(self, ctx):
        name = self.args[0] if self.args else None
        return name, (ctx.geom_by_name(name) if name else None)

    def on_option_set(self, option, value, ctx):
        if option.name == self.size_option and float(value) <= 0.0:
            self.opt[option.name] = option.default
            return (f"{self.name}: the {self.size_word} must be positive "
                    f"(got {_num(value)})"), False
        return super().on_option_set(option, value, ctx)

    def _resolve_pick(self, point, ctx):
        """The edge index `point` picks on the solid. Raises ValueError, naming the problem."""
        occt = bridge._occt()
        if self._operand is None:
            name, geom = self._solid(ctx)
            if not isinstance(geom, (r3.Brep, r3.Extrusion)):
                raise LookupError(f"{name!r} is not a polysurface")
            if occt is None:
                raise LookupError("this command needs the OCCT kernel, which is not installed")
            self._operand = bridge._occt_operand(geom)
            if self._operand is None:
                raise LookupError(
                    f"{name} has no exact solid (no registered master and no rebuildable "
                    f"recipe), so it cannot be {self.past.lower()} exactly")
        return _edge_at(self._operand, point, commands._document_tolerance(ctx))

    def _feed(self, text, ctx):
        if self.step_index == 1 and self.pending_option is None:
            raw = (text or "").strip()
            if not raw:
                if not self._picks:
                    return f"{self.name}: no edges selected -- nothing changed", True
                return self.execute(ctx)
            is_option = opts.match(raw, self.options, self.opt).status != opts.NONE
            if not is_option:
                try:
                    point = self._parse(POINT, raw, ctx)
                    index = self._resolve_pick(point, ctx)
                except LookupError as e:          # the OBJECT is unusable: stop
                    return f"{self.name}: {e}. Nothing was changed.", True
                except ValueError as e:           # just this pick: say so and stay
                    return f"{self.name}: {e}", False
                if index in self._edges:
                    return f"that edge is already selected ({len(self._edges)} edge(s))", False
                self._picks.append(point)
                self._edges.append(index)
                return f"{len(self._edges)} edge(s) selected -- press Enter when done", False
        return super()._feed(text, ctx)

    def execute(self, ctx):
        name, geom = self._solid(ctx)
        if not isinstance(geom, (r3.Brep, r3.Extrusion)):
            return f"{self.name}: {name!r} is not a polysurface", True
        size = float(self.opt[self.size_option])
        try:
            out = bridge.bevel_edges_occt(geom, size, chamfer=self.chamfer,
                                          edge_points=list(self._picks))
        except ValueError as e:
            return f"{self.name}: {e}. Nothing was changed.", True
        out = _exact_or_none(out)
        if out is None:
            why = bridge.last_occt_decline() or "the kernel declined"
            return (f"{self.name}: unable to {self.verb} {name} at {self.size_word} "
                    f"{_num(size)} -- {why}. Nothing was changed."), True
        display = _replace(ctx, name, out, self.past)
        return (f"{self.past} {len(self._picks)} edge(s) of {name} at {self.size_word} "
                f"{_num(size)}: {display}"), True


def _not_built(names):
    return tuple(opts.Option(n, opts.MODE, help="Not implemented yet.") for n in names)


class FilletEdgeCmd(_EdgeBevelCmd):
    __doc__ = _EdgeBevelCmd.__doc__
    name = "FilletEdge"
    description = "Round the picked edges of a solid with an exact rolling-ball fillet."
    steps = [("Select the polysurface to fillet:", OBJECTS),
             ("Select edges to fillet. Press Enter when done:", POINT)]
    options = (
        opts.Option("NextRadius", opts.VALUE, default=1.0, value_kind="number",
                    help="Specifies the radius for the next edge."),
    ) + _not_built(("ShowRadius", "ChainEdges", "FaceEdges", "PreviousEdgeSelection",
                    "TrimAndJoin", "History", "SelectEdges", "Preview", "Edit"))


class ChamferEdgeCmd(_EdgeBevelCmd):
    __doc__ = _EdgeBevelCmd.__doc__
    name = "ChamferEdge"
    description = "Bevel the picked edges of a solid with an exact flat chamfer."
    chamfer = True
    verb = "chamfer"
    past = "Chamfered"
    size_option = "NextChamferDistance"
    size_prompt = "Chamfer distance"
    size_word = "distance"
    steps = [("Select the polysurface to chamfer:", OBJECTS),
             ("Select edges to chamfer. Press Enter when done:", POINT)]
    options = (
        opts.Option("NextChamferDistance", opts.VALUE, default=1.0, value_kind="number",
                    help="Specifies the chamfer distance for the next edge."),
    ) + _not_built(("ShowChamferDistance", "ChainEdges", "FaceEdges",
                    "PreviousEdgeSelection", "TrimAndJoin", "History", "SelectEdges",
                    "Preview", "Edit"))


# ---------------------------------------------------------------------------
# Cap
# ---------------------------------------------------------------------------

def _count_msg(n, one, many):
    return one if n == 1 else many.replace("%d", str(n))


class CapCmd(Command):
    """Fill planar openings with exact planar faces.

    ``bridge.cap_planar_holes_occt`` preserves existing surfaces and adds analytic plane
    faces. Objects without an exact master or recipe are refused with the kernel's reason;
    mesh capping is not implemented because it would replace surface geometry with facets.
    A successful result replaces the input object.
    """
    name = "Cap"
    description = "Close the planar holes of surfaces and polysurfaces with exact planar faces."
    steps = [("Select objects to cap:", OBJECTS_MULTI)]
    preselect_names = 1
    cancel_on_empty_set = True

    def execute(self, ctx):
        names = []
        for a in self.args:
            for n in (a if isinstance(a, (list, tuple)) else [a]):
                if n not in names:
                    names.append(n)
        occt = bridge._occt()
        capped, closed, unable, why = [], 0, 0, []
        for name in names:
            geom = ctx.geom_by_name(name)
            if isinstance(geom, r3.Mesh):
                unable += 1
                why.append(f"{name}: capping a mesh is not implemented yet")
                continue
            if not isinstance(geom, (r3.Brep, r3.Extrusion)):
                unable += 1
                why.append(f"{name}: not a surface or polysurface")
                continue
            operand = bridge._occt_operand(geom) if occt is not None else None
            if operand is not None:
                was_closed = occt.is_airtight(operand)
            else:
                was_closed = bool(getattr(geom, "IsSolid", False))
            if was_closed:
                closed += 1
                continue
            out = _exact_or_none(bridge.cap_planar_holes_occt(geom))
            if out is None:
                unable += 1
                why.append(f"{name}: {bridge.last_occt_decline() or 'the result was not closed'}")
                continue
            capped.append(_replace(ctx, name, out, "Capped"))
        lines = []
        if capped:
            lines.append(f"Capped {len(capped)} object(s): {', '.join(capped)}")
        if unable:
            lines.append(_count_msg(unable, "Unable to cap one object",
                                    "Unable to cap %d objects")
                         + " (" + "; ".join(why) + ")")
        if closed:
            lines.append(_count_msg(
                closed, "Unable to cap one object that was closed prior to starting.",
                "Unable to cap %d objects that were closed prior to starting."))
        return " ".join(lines) or "Cap: nothing selected", True


# ---------------------------------------------------------------------------
# MoveFace / MoveEdge
# ---------------------------------------------------------------------------

class _MoveSubObjectCmd(Command):
    """Shared implementation of ``MoveFace`` and ``MoveEdge``.

    The solid is rebuilt exactly and replaced in place with the same name in one undo step.
    Moves that would bend unsupported faces are refused without changing the document.

    The solid is selected first and the sub-object is identified by a point on it. Face
    picks must be inside one face, while edge picks must lie on exactly one edge within
    document tolerance. One sub-object is moved per command run.
    """
    kind = "face"
    preselect_names = 1

    def __init__(self):
        super().__init__()
        self._sub_id = None

    def _solid(self, ctx):
        name = self.args[0] if self.args else None
        return name, (ctx.geom_by_name(name) if name else None)

    def _resolve(self, geom, point, ctx):
        raise NotImplementedError

    def _feed(self, text, ctx):
        raw = (text or "").strip()
        if self.step_index == 1 and self.pending_option is None and raw \
                and opts.match(raw, self.options, self.opt).status == opts.NONE:
            name, geom = self._solid(ctx)
            if not isinstance(geom, (r3.Brep, r3.Extrusion)):
                return f"{self.name}: {name!r} is not a polysurface", True
            try:
                point = self._parse(POINT, raw, ctx)
                self._sub_id = self._resolve(geom, point, ctx)
            except LookupError as e:
                return f"{self.name}: {e}. Nothing was changed.", True
            except ValueError as e:
                return f"{self.name}: {e}", False       # just this pick: stay
            self.args.append(point)
            self.step_index += 1
            return f"{self.kind} {self._sub_id} of {name} selected", False
        return super()._feed(text, ctx)

    def _constrain(self, vec, geom, ctx):
        """The move vector after the DirectionConstraint option, or ValueError."""
        return vec

    def execute(self, ctx):
        name, geom = self._solid(ctx)
        if geom is None or self._sub_id is None or len(self.args) < 4:
            return f"{self.name}: nothing to move", True
        a, b = self.args[2], self.args[3]
        vec = tuple(b[i] - a[i] for i in range(3))
        try:
            vec = self._constrain(vec, geom, ctx)
            if max(abs(c) for c in vec) <= 0.0:
                return f"{self.name}: the move is zero -- nothing changed", True
            new = bridge.move_subobject(geom, self.kind, self._sub_id, vec)
        except ValueError as e:
            return f"{self.name}: {e}. Nothing was changed.", True
        oid = ctx.model.object_id_by_name(name)
        note = getattr(ctx, "note_change", None)
        if callable(note):
            note(self.name)
        ctx.model.replace_geometry(oid, new)
        try:
            if not bridge.rebuild_object_mesh(name, new) and hasattr(ctx, "rebuild_display"):
                ctx.rebuild_display()
        except ImportError:
            pass                                       # headless: no display to update
        try:
            from . import ui

            ui.invalidate_snap_cache()
        except Exception:  # noqa: BLE001
            pass
        return (f"Moved {self.kind} {self._sub_id} of {name} by "
                f"{_num(vec[0])},{_num(vec[1])},{_num(vec[2])}"), True


class MoveFaceCmd(_MoveSubObjectCmd):
    __doc__ = _MoveSubObjectCmd.__doc__ + """
    DirectionConstraint: None ("The face can be moved any direction") and Normal
    ("the face can only be moved in the positive or normal direction") are both
    built; the default is None, as listed first. ToBoundary is declared, not built.
    """
    name = "MoveFace"
    description = "Move a face of a solid; the surrounding faces follow, exactly."
    kind = "face"
    steps = [("Select the polysurface:", OBJECTS), ("Select a face:", POINT),
             ("Point to move from:", POINT), ("Point to move to:", POINT)]
    options = (
        opts.Option("DirectionConstraint", opts.ENUM, choices=("None", "Normal"),
                    default="None", help="Restrict the move to the face normal."),
        opts.Option("ToBoundary", opts.MODE, help="Not implemented yet."),
    )

    def _resolve(self, geom, point, ctx):
        if bridge._occt_operand(geom) is None:
            raise LookupError(f"that object has no exact solid, so its faces cannot be moved")
        return bridge.faces_at_points(geom, [point])[0]

    def _constrain(self, vec, geom, ctx):
        if self.opt.get("DirectionConstraint") != "Normal":
            return vec
        occt = bridge._occt()
        face = occt.unique_faces(bridge._occt_operand(geom))[self._sub_id - 1]
        plane = occt.face_plane(face)
        if plane is None:
            raise ValueError("a curved face has no single normal")
        n = plane[3]
        d = sum(v * c for v, c in zip(vec, n))
        return tuple(c * d for c in n)


class MoveEdgeCmd(_MoveSubObjectCmd):
    __doc__ = _MoveSubObjectCmd.__doc__ + """
    DirectionConstraint: None and Vertical ("Restricts movement vertical to the
    construction plane") are built. FirstFaceNormal, SecondFaceNormal and
    AverageFacesNormal are declared and refused: they depend on the reference CAD application's face-pick order.
    """
    name = "MoveEdge"
    description = "Move an edge of a solid; the faces on either side follow, exactly."
    kind = "edge"
    steps = [("Select the polysurface:", OBJECTS), ("Select an edge:", POINT),
             ("Point to move from:", POINT), ("Point to move to:", POINT)]
    options = (
        opts.Option("DirectionConstraint", opts.ENUM,
                    choices=("None", "FirstFaceNormal", "SecondFaceNormal",
                             "AverageFacesNormal", "Vertical"),
                    default="None", help="Restrict the direction of the move."),
    )

    def on_option_set(self, option, value, ctx):
        if option.name == "DirectionConstraint" and value not in ("None", "Vertical"):
            self.opt[option.name] = option.default
            return f"{self.name}: DirectionConstraint={value} is not implemented yet", False
        return super().on_option_set(option, value, ctx)

    def _resolve(self, geom, point, ctx):
        operand = bridge._occt_operand(geom)
        if operand is None:
            raise LookupError("that object has no exact solid, so its edges cannot be moved")
        return _edge_at(operand, point, commands._document_tolerance(ctx))

    def _constrain(self, vec, geom, ctx):
        if self.opt.get("DirectionConstraint") != "Vertical":
            return vec
        z = getattr(self._active_cplane(ctx), "zaxis", (0.0, 0.0, 1.0))
        z = tuple(float(c) for c in z)
        d = sum(v * c for v, c in zip(vec, z))
        return tuple(c * d for c in z)


# ---------------------------------------------------------------------------
# Sweep1 / Sweep2
# ---------------------------------------------------------------------------

class _SweepCmd(Command):
    """Shared implementation of ``Sweep1`` and ``Sweep2``.

    One cross-section is swept along one or two rails to create a smooth OCCT-backed
    surface. Input curves are retained. Multiple cross-sections and unsupported options
    are refused by name without adding geometry.

    Rail selection is cleared before cross-section selection begins, preventing a rail
    from also being consumed as the section curve.
    """
    rails = 1
    preselect_names = 0     # rails and sections must be told apart; never guess from a set

    def _feed_objects_step(self, text, ctx):
        before = self.step_index
        out = super()._feed_objects_step(text, ctx)
        if self.step_index > before and self.step_index < len(self.steps):
            selector = getattr(ctx, "select_by_names", None)
            if callable(selector):
                try:
                    selector([])
                except Exception:  # noqa: BLE001
                    pass
        return out

    def _curves(self, ctx):
        """(rails, sections) as geometry, or a refusal string."""
        rail_names = list(self.args[:self.rails])
        section_names = [n for n in (self.args[self.rails] if len(self.args) > self.rails
                                     else []) if n not in rail_names]
        rails = []
        for i, n in enumerate(rail_names):
            g = ctx.geom_by_name(n)
            if not isinstance(g, r3.Curve):
                which = ("Rail curve is not valid" if self.rails == 1 else
                         ("First", "Second")[i] + " rail curve is not valid")
                return f"{which} ({n!r} is not a curve)"
            rails.append(g)
        if not section_names:
            return "no cross-section curve was selected"
        if len(section_names) > 1:
            return (f"sweeping through {len(section_names)} cross-sections is not "
                    f"implemented yet -- one cross-section is")
        sec = ctx.geom_by_name(section_names[0])
        if not isinstance(sec, r3.Curve):
            return f"Shape curve 1 is invalid ({section_names[0]!r} is not a curve)"
        return rails, sec

    def _build(self, rails, section):
        raise NotImplementedError

    def execute(self, ctx):
        got = self._curves(ctx)
        if isinstance(got, str):
            return f"{self.name}: {got}. Nothing was created.", True
        rails, section = got
        try:
            out = self._build(rails, section)
        except (ValueError, RuntimeError) as e:
            return f"{self.name} failed: {e}", True
        if not isinstance(out, r3.Brep) or len(out.Faces) == 0:
            return f"{self.name} failed: no surface was produced", True
        if kernel is not None:
            problem = kernel.honesty_problem(out)
            if problem or kernel.representation_of(out) == kernel.MESH_DERIVED:
                return f"{self.name} failed: {problem or 'the result is faceted'}", True
        _, display = ctx.add_geometry(out, "Sweep")
        return f"Created {display}", True


class Sweep1Cmd(_SweepCmd):
    __doc__ = _SweepCmd.__doc__ + """
    Sweep1 supports Freeform framing, which preserves the section's angle to the rail,
    and Roadlike framing, which uses a fixed axis. Roadlike's custom axis is not built.
    """
    name = "Sweep1"
    description = "Sweep a cross-section curve along one rail into a smooth surface."
    steps = [("Select rail:", OBJECTS),
             ("Select cross-section curves. Press Enter when done:", OBJECTS_MULTI)]
    options = (
        opts.Option("Freeform", opts.MODE,
                    help="The cross-section keeps its angle to the rail (the default)."),
        opts.Option("Roadlike", opts.MODE,
                    help="The cross-section rotates about a fixed axis."),
    ) + _not_built(("ChainEdges", "Point", "ClosedSweep", "ShapeBlending",
                    "UntrimmedMiters", "RefitRail"))

    def __init__(self):
        super().__init__()
        self.style = "Freeform"

    def on_mode(self, name, ctx):
        if name in ("Freeform", "Roadlike"):
            self.style = name
            return f"{self.name}: frame style {name}", False
        return super().on_mode(name, ctx)

    def active_modes(self):
        return {self.style}

    def _build(self, rails, section):
        from . import sweep

        return sweep.sweep1(rails[0], section, style=self.style)


class Sweep2Cmd(_SweepCmd):
    __doc__ = _SweepCmd.__doc__ + """
    Sweep2: the section's START follows the first rail and its END the second. It is
    scaled in both height and width to span them. A section whose ends coincide (a closed
    curve) cannot span two rails,
    so it is refused.
    """
    name = "Sweep2"
    description = "Sweep a cross-section curve along two rails into a smooth surface."
    rails = 2
    steps = [("Select first rail:", OBJECTS), ("Select second rail:", OBJECTS),
             ("Select cross-section curves. Press Enter when done:", OBJECTS_MULTI)]
    options = _not_built(("ChainEdges", "Point", "MaintainHeight", "ClosedSweep",
                          "RefitRails"))

    def _build(self, rails, section):
        from . import sweep

        return sweep.sweep2(rails[0], rails[1], section)


# ---------------------------------------------------------------------------
# Torus, Ellipsoid, TruncatedCone, Tube, Pyramid
# ---------------------------------------------------------------------------

def _frame(plane):
    """(x, y, z) unit axes of the active CPlane (world XY when there is none)."""
    if plane is None:
        return (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    x = tuple(float(c) for c in plane.xaxis)
    y = tuple(float(c) for c in plane.yaxis)
    z = tuple(float(c) for c in getattr(plane, "zaxis", (
        x[1] * y[2] - x[2] * y[1], x[2] * y[0] - x[0] * y[2], x[0] * y[1] - x[1] * y[0])))
    return x, y, z


def _dot(a, b):
    return sum(p * q for p, q in zip(a, b))


class _PrimitiveCmd(commands.HeightAxisMixin, Command):
    """Shared body of the primitives. Each builds an EXACT OCCT solid through its
    `bridge.make_*` (master + recipe), in the ACTIVE CPlane at the picked centre, with the
    height along the CPlane normal (`HeightAxisMixin` constrains a height click to that
    axis). The direction/size options the reference CAD application has beyond the default flow are declared and
    refused: "not implemented yet", nothing built."""

    base_name = ""
    not_built = ("Vertical", "AroundCurve", "2Point", "3Point", "Tangent", "FitPoints",
                 "Diameter", "DirectionConstraint")

    def _distance_base(self):
        return self.args[0] if self.args else None      # every size is from the centre

    def _make(self, ctx, plane):
        raise NotImplementedError

    def execute(self, ctx):
        plane = self._active_cplane(ctx)
        try:
            geom = self._make(ctx, plane)
        except ValueError as e:
            return f"{self.name}: {e}", True
        _, display = ctx.add_geometry(geom, self.base_name)
        return f"Created {display}", True


def _declared(names):
    return tuple(opts.Option(n, opts.MODE, help="Not implemented yet.") for n in names)


class TorusCmd(_PrimitiveCmd):
    """Draw a torus from its centre, major radius, and cross-section radius.

    A picked cross-section radius is from the base circle. ``FixInnerDimension``
    is declared but not implemented.
    """
    name = "Torus"
    base_name = "Torus"
    description = "Draw a torus: centre, radius, second radius."
    steps = [("Center of torus:", POINT), ("Radius:", commands.DISTANCE),
             ("Second radius:", commands.DISTANCE)]
    _height_step_index = -1                     # no height step
    options = _declared(_PrimitiveCmd.not_built + ("FixInnerDimension",))

    def _parse_distance(self, text, ctx):
        if len(self.args) != 2:
            return super()._parse_distance(text, ctx)
        try:
            return commands.parse_number((text or "").strip())
        except ValueError:
            pass
        c = self.args[0]
        p = commands.parse_point((text or "").strip(), self._active_cplane(ctx), c)
        _x, _y, z = _frame(self._active_cplane(ctx))
        v = tuple(p[i] - c[i] for i in range(3))
        h = _dot(v, z)
        rho = math.sqrt(max(0.0, _dot(v, v) - h * h))
        return math.hypot(rho - float(self.args[1]), h)

    def _make(self, ctx, plane):
        center, major, minor = self.args
        return bridge.make_torus(center, float(major), float(minor), plane)


class EllipsoidCmd(_PrimitiveCmd):
    """Draw an analytic ellipsoid from the half-length of each axis.

    The first picked axis sets a direction in the construction plane, the second is
    perpendicular to it in that plane, and the third follows the plane normal. Additional
    declared construction modes are not implemented.
    """
    name = "Ellipsoid"
    base_name = "Ellipsoid"
    description = "Draw an ellipsoid: centre, then the end of each of its three axes."
    steps = [("Ellipsoid center:", POINT), ("End of first axis:", POINT),
             ("End of second axis:", commands.DISTANCE),
             ("End of third axis:", commands.DISTANCE)]
    _height_step_index = -1
    options = _declared(("Corner", "Diameter", "FromFoci", "Vertical", "AroundCurve",
                         "MarkFoci"))

    def _make(self, ctx, plane):
        center, end1, b, c = self.args
        x0, _y0, z = _frame(plane)
        v = tuple(end1[i] - center[i] for i in range(3))
        v = tuple(v[i] - _dot(v, z) * z[i] for i in range(3))       # into the CPlane
        a = math.sqrt(_dot(v, v))
        if a <= 0.0:
            raise ValueError("the first axis has no length in the construction plane")
        x = tuple(c_ / a for c_ in v)
        y = (z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0])
        frame = types.SimpleNamespace(xaxis=x, yaxis=y, zaxis=z)
        return bridge.make_ellipsoid(center, a, float(b), float(c), frame)


class TruncatedConeCmd(_PrimitiveCmd):
    """Draw a truncated cone from base circle, height, and end-circle radius.

    The end radius is from the end circle's centre. Closed solids are supported;
    open output and alternate direction modes are declared but not implemented.
    """
    name = "TruncatedCone"
    base_name = "TruncatedCone"
    description = "Draw a truncated cone: base centre, base radius, height, end radius."
    steps = [("Base of truncated cone:", POINT), ("Radius:", commands.DISTANCE),
             ("End of truncated cone:", commands.DISTANCE), ("Radius:", commands.DISTANCE)]
    _height_step_index = 2
    options = (opts.Option("Solid", opts.TOGGLE, default=True,
                           help="Caps the top and bottom openings or leave them open."),
               ) + _declared(_PrimitiveCmd.not_built)

    def on_option_set(self, option, value, ctx):
        if option.name == "Solid" and not value:
            self.opt["Solid"] = True
            return f"{self.name}: Solid=No (an open truncated cone) is not implemented yet", False
        return super().on_option_set(option, value, ctx)

    def _distance_base(self):
        if len(self.args) == 3:                       # the END radius: from the end centre
            _x, _y, z = _frame(getattr(self, "_plane", None))
            c, h = self.args[0], float(self.args[2])
            return tuple(c[i] + h * z[i] for i in range(3))
        return super()._distance_base()

    def _parse_distance(self, text, ctx):
        self._plane = self._active_cplane(ctx)
        return super()._parse_distance(text, ctx)

    def _make(self, ctx, plane):
        center, r1, h, r2 = self.args
        return bridge.make_truncated_cone(center, float(r1), float(r2), float(h), plane)


class TubeCmd(_PrimitiveCmd):
    """Draw a tube from its base centre, two wall radii, and height.

    Either radius may describe the outer wall. ``WallThickness`` and ``BothSides`` are
    declared but not implemented.
    """
    name = "Tube"
    base_name = "Tube"
    description = "Draw a tube: base centre, radius, second radius, height."
    steps = [("Base of tube:", POINT), ("Radius:", commands.DISTANCE),
             ("Second radius:", commands.DISTANCE), ("End of tube:", commands.DISTANCE)]
    _height_step_index = 3
    options = _declared(_PrimitiveCmd.not_built + ("WallThickness", "BothSides"))

    def _make(self, ctx, plane):
        center, r1, r2, h = self.args
        if abs(float(r1) - float(r2)) <= 0.0:
            raise ValueError("the two walls have the same radius -- a tube needs a wall")
        return bridge.make_tube(center, float(r1), float(r2), float(h), plane)


class PyramidCmd(_PrimitiveCmd):
    """Draw a pyramid from a base polygon and apex height.

    A picked corner rotates the polygon to place a corner there. ``NumSides``, ``Inscribed``,
    and ``Circumscribed`` are supported; open output, alternate directions, and star bases
    are declared but not implemented. ``NumSides`` defaults to 5.
    """
    name = "Pyramid"
    base_name = "Pyramid"
    description = "Draw a pyramid: base centre, corner, apex height."
    steps = [("Center of inscribed pyramid:", POINT), ("Corner of pyramid:", commands.DISTANCE),
             ("End of pyramid:", commands.DISTANCE)]
    _height_step_index = 2
    options = (opts.Option("NumSides", opts.VALUE, default=5.0, value_kind="number",
                           help="Specifies the number of sides."),
               opts.Option("Inscribed", opts.MODE, help="Corners touch the circle."),
               opts.Option("Circumscribed", opts.MODE, help="Side midpoints touch it."),
               opts.Option("Solid", opts.TOGGLE, default=True,
                           help="Fills the base with a surface to form a closed solid."),
               ) + _declared(("DirectionConstraint", "Vertical", "AroundCurve", "Star"))

    def __init__(self):
        super().__init__()
        self.inscribed = True
        self._corner = None

    def on_mode(self, name, ctx):
        if name in ("Inscribed", "Circumscribed"):
            self.inscribed = name == "Inscribed"
            self.steps[0] = (f"Center of {name.lower()} pyramid:", POINT)
            return f"{self.name}: {name}", False
        return super().on_mode(name, ctx)

    def active_modes(self):
        return {"Inscribed" if self.inscribed else "Circumscribed"}

    def on_option_set(self, option, value, ctx):
        if option.name == "Solid" and not value:
            self.opt["Solid"] = True
            return f"{self.name}: Solid=No (an open pyramid) is not implemented yet", False
        if option.name == "NumSides" and (float(value) < 3 or float(value) != int(value)):
            self.opt["NumSides"] = option.default
            return f"{self.name}: NumSides must be a whole number >= 3", False
        return super().on_option_set(option, value, ctx)

    def _parse_distance(self, text, ctx):
        if len(self.args) == 1:
            try:
                commands.parse_number((text or "").strip())
                self._corner = None
            except ValueError:
                self._corner = commands.parse_point((text or "").strip(),
                                                    self._active_cplane(ctx), self.args[0])
        return super()._parse_distance(text, ctx)

    def _make(self, ctx, plane):
        center, r, h = self.args
        x, y, z = _frame(plane)
        frame = plane
        if self._corner is not None:
            v = tuple(self._corner[i] - center[i] for i in range(3))
            v = tuple(v[i] - _dot(v, z) * z[i] for i in range(3))
            n = math.sqrt(_dot(v, v))
            if n > 0.0:
                # rhino3dm's inscribed polygon has a corner on +x and its circumscribed one
                # a side midpoint on +x, so turning +x to the pick puts the
                # corner (or the midpoint) there in both modes.
                x = tuple(c / n for c in v)
                y = (z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2],
                     z[0] * x[1] - z[1] * x[0])
                frame = types.SimpleNamespace(xaxis=x, yaxis=y, zaxis=z)
        return bridge.make_pyramid(center, float(r), int(self.opt.get("NumSides", 5)),
                                   float(h), self.inscribed, frame)


PROVIDED = (FilletEdgeCmd, ChamferEdgeCmd, CapCmd, Sweep1Cmd, Sweep2Cmd, MoveFaceCmd,
            MoveEdgeCmd, TorusCmd, EllipsoidCmd, TruncatedConeCmd, TubeCmd, PyramidCmd)
for _cls in PROVIDED:
    _inst = _cls()
    commands.COMMANDS[_inst.name.lower()] = _inst
