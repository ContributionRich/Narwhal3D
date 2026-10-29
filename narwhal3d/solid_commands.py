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

from . import bridge, commands, curve_inputs
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
    """Two-phase reference-compatible lifecycle for exact edge fillets and chamfers.

    A viewport edge hit enters through :meth:`pick_edge`. The stable edge id is selection
    state; its interior world point locates the scalar selection handle. The first
    accepted edge binds the command to its owning solid, so subsequent picks cannot silently
    cross into another part.

    Enter closes edge selection and builds an exact cached candidate without editing the
    document. A second Enter commits that candidate. ``preview_geometry`` is deliberately a
    side-effect-free getter suitable for a draw callback; all OCCT work happens while handling
    picks, options, or Enter.
    """
    SELECT_EDGES = "SELECT_EDGES"
    EDIT_HANDLES = "EDIT_HANDLES"

    chamfer = False
    verb = "fillet"
    past = "Filleted"
    size_option = "NextRadius"
    size_prompt = "Fillet radius"
    size_word = "radius"
    show_option = "ShowRadius"
    _handle_input_options = tuple(
        opts.Option(name, opts.MODE, help="Not implemented yet.")
        for name in ("FromCurve", "FromTwoPoints"))
    _handle_cache_fields = ("_candidate", "_candidate_key", "_candidate_error",
                            "_sections", "_section_curves", "_section_lines", "_section_error")
    # One synthetic step keeps Command.begin() interactive, but object selection is never
    # consumed: this command begins with an edge-only viewport prompt.
    steps = [("Select edges:", POINT)]
    preselect_names = 0

    def __init__(self):
        super().__init__()
        self.phase = self.SELECT_EDGES
        self._owner_name = None
        self._owner_geom = None
        self._owner_document = None
        self._owner_id = None
        self._handle_input = None
        self._operand = None
        self._edge_points = {}       # authoritative clicked edge id -> UI handle point
        self._affected_edges = set() # includes OCCT's automatic tangent-contour propagation
        self._picks = []             # compatibility mirrors; never authoritative
        self._edges = []
        self._candidate = None
        self._candidate_key = None
        self._candidate_error = None
        self._edge_radii = {}        # edge id -> (radius at anchor 0, radius at anchor 1)
        self._edge_endpoints = {}    # exact source anchors, retained across radius edits
        self._sections = {}
        self._section_curves = ()
        self._section_lines = ()
        self._section_error = None

    @property
    def prompt(self):
        return "".join(text for text, _ in self.prompt_segments())

    def prompt_segments(self):
        """One source for command text and clickable option labels, including subprompts."""
        if self.handle_input_active():
            session = self._handle_input
            radius = session["pair"][session["id"][2] or 0]
            # MEASURED live grip/held-drag capture (2026-10-28): retain the initial default.
            base = f"Select new {self.verb} {self.size_word} <{radius:.3f}>"
            return opts.prompt_segments(base, self._handle_input_options, self.opt)
        if self.pending_option is not None:
            base = (f"{self.size_prompt} <{_num(self.opt[self.size_option])}>:"
                    if self.pending_option.name == self.size_option
                    else opts.value_prompt(self.pending_option))
            return opts.prompt_segments(base, ())
        if self.phase == self.SELECT_EDGES:
            base = f"Select edges to {self.verb}. Press Enter when done:"
            visible = {self.show_option, self.size_option, "ChainEdges", "FaceEdges", "Preview", "Edit"}
        else:
            noun = "chamfer distance" if self.chamfer else "fillet"
            base = f"Select {noun} handle to edit. Press Enter when done:"
            visible = {self.show_option, "Preview", "SelectEdges", "TrimAndJoin"}
        return opts.prompt_segments(base, tuple(o for o in self.options if o.name in visible), self.opt)

    def _owner_is_current(self, ctx):
        # Geometry wrappers are recreated by File3dm lookups; use document + object identity.
        model = ctx.model
        return (self._owner_name is not None and model.file is self._owner_document
                and model.object_id_by_name(self._owner_name) == self._owner_id
                and self._owner_id is not None)

    def begin_handle_input(self, handle_id, ctx):
        """Begin a UI-only subedit. Python coordinates existing parser/OCCT APIs, not math.

        Cache references are intentional: setters replace these caches, so Escape can
        restore their exact identities without rebuilding or touching document geometry.
        """
        if (self.handle_input_active() or self.pending_option is not None
                or self._handle_id_error(handle_id) or not self._owner_is_current(ctx)):
            return False
        self._handle_input = {
            "id": handle_id, "phase": self.phase,
            "pair": self._edge_radii[handle_id[1]],
            "cache": tuple(getattr(self, name) for name in self._handle_cache_fields),
        }
        return True

    def handle_input_active(self):
        return self._handle_input is not None

    def _handle_input_is_current(self, ctx):
        session = self._handle_input
        return (session is not None and self.phase == session["phase"]
                and not self._handle_id_error(session["id"]) and self._owner_is_current(ctx))

    def finish_handle_input(self):
        """Finish a click/drag adjustment, never the command or its undo record."""
        active = self.handle_input_active()
        self._handle_input = None
        return active

    def cancel_handle_input(self, ctx):
        """Escape restores just this adjustment, including a previously declined cache."""
        if not self.handle_input_active():
            return False
        session = self._handle_input
        current = self._handle_input_is_current(ctx)
        self.finish_handle_input()
        if not current:
            self._invalidate_candidate()
            self._clear_sections()
            return False
        self._edge_radii[session["id"][1]] = session["pair"]
        for name, value in zip(self._handle_cache_fields, session["cache"]):
            setattr(self, name, value)
        return True

    def _solid(self, ctx):
        name = self._owner_name
        return name, (ctx.geom_by_name(name) if name else None)

    def _state_key(self):
        return (self._owner_name, tuple(self._edge_points.items()),
                tuple(self._edge_radii.items()), tuple(self._edge_endpoints.items()),
                bool(self.chamfer))

    def _invalidate_candidate(self):
        self._candidate = None
        self._candidate_key = None
        self._candidate_error = None

    def _bevel_parameters(self):
        """Keep the legacy scalar route when all selected radii are equal."""
        pairs = list(self._edge_radii.values())
        size = pairs[0][0] if pairs else float(self.opt[self.size_option])
        if all(pair == (size, size) for pair in pairs):
            return size, {}
        return size, {"edge_radii": dict(self._edge_radii),
                      "edge_endpoints": dict(self._edge_endpoints)}

    def _sync_pick_mirrors(self):
        self._edges = list(self._edge_points)
        self._picks = list(self._edge_points.values())

    def _refresh_affected_edges(self):
        if self._owner_geom is None or not self._edge_points:
            self._affected_edges = set()
            return
        self._affected_edges = set(bridge.bevel_affected_edges_occt(
            self._owner_geom, self._bevel_parameters()[0], list(self._edge_points),
            chamfer=self.chamfer))

    def _bind_owner(self, name, edge_id, ctx):
        geom = ctx.geom_by_name(name) if name else None
        if not isinstance(geom, (r3.Brep, r3.Extrusion)):
            raise LookupError(f"{name!r} is not a polysurface")
        occt = bridge._occt()
        if occt is None:
            raise LookupError("this command needs the OCCT kernel, which is not installed")
        operand = bridge._occt_operand(geom)
        if operand is None:
            raise LookupError(
                f"{name} has no exact solid (no registered master and no rebuildable recipe), "
                f"so it cannot be {self.past.lower()} exactly")
        curves = occt.edge_curves(operand)
        valid = {int(curve.get("index", i + 1)) for i, curve in enumerate(curves)}
        if int(edge_id) not in valid:
            raise ValueError(f"edge {edge_id} does not exist on {name}")
        self._owner_name = name
        self._owner_document = ctx.model.file
        self._owner_id = ctx.model.object_id_by_name(name)
        self._owner_geom = geom
        self._operand = operand

    def edge_selection_keys(self):
        """Stable ``(object name, edge id)`` keys currently selected by the command."""
        if self._owner_name is None:
            return frozenset()
        shown = self._affected_edges or set(self._edge_points)
        return frozenset((self._owner_name, edge_id) for edge_id in shown)

    def handle_records(self):
        """Draw-safe records. OURS ids are (owner, edge_id, endpoint), never list indices.

        endpoint is None for the scalar selection handle (also closed edges/chamfers),
        or 0/1 in EDIT_HANDLES, ordered by the exact source anchors. `point` is the
        grip location; `anchor` remains the source endpoint, NOT the circle center.
        """
        if not self.opt[self.show_option]:
            return ()
        records = []
        for edge_id, point in self._edge_points.items():
            anchors = self._edge_endpoints[edge_id]
            ends = (0, 1) if (self.phase == self.EDIT_HANDLES and not self.chamfer
                              and anchors[0] != anchors[1]) else (None,)
            for endpoint in ends:
                radius = self._edge_radii[edge_id][endpoint or 0]
                anchor = anchors[endpoint] if endpoint is not None else point
                sections = self._sections.get(edge_id, ())
                section = sections[endpoint] if endpoint is not None and sections else {}
                grip = tuple(section.get("grip_point", anchor))
                records.append({"name": self._owner_name, "edge_id": edge_id,
                                "edge": edge_id, "point": grip, "size": radius,
                                "radius": radius, "kind": self.size_word,
                                "id": (self._owner_name, edge_id, endpoint),
                                "anchor": anchor, "grip_point": grip,
                                "endpoint": endpoint,
                                "circle_center": section.get("circle_center")})
        return tuple(records)

    def preview_section_curves(self):
        """Cached exact rhino3dm section arcs, even with Preview=No. Never builds."""
        return self._section_curves

    def preview_lines(self):
        """Cached display-only polylines: tuple of tuple-of-XYZ points. Never builds."""
        return self._section_lines

    def preview_section_error(self):
        """Why endpoint sections are unavailable; scalar solid fillets may still work."""
        return self._section_error

    def _clear_sections(self):
        self._sections = {}
        self._section_curves = ()
        self._section_lines = ()
        self._section_error = None

    def _refresh_sections(self):
        self._clear_sections()
        if self.chamfer or not self._edge_points:
            return
        try:
            sections = bridge.bevel_edge_sections_occt(
                self._owner_geom, self._bevel_parameters()[0], list(self._edge_points),
                edge_radii=dict(self._edge_radii), edge_endpoints=dict(self._edge_endpoints))
            if sections is None:
                self._section_error = bridge.last_occt_decline() or "section preview declined"
                return
            curves = tuple(bridge.curve_from_recipe(section["curve"])
                           for records in sections.values() for section in records)
            # Python is UI/OCCT orchestration only. Reuse the bridge's existing display
            # sampler; these polylines NEVER become document geometry or recipes.
            lines = tuple(tuple((p.X, p.Y, p.Z) for p in bridge.sample_curve(curve))
                          for curve in curves)
            self._sections, self._section_curves, self._section_lines = sections, curves, lines
        except (ValueError, RuntimeError) as exc:
            self._section_error = str(exc)

    def _handle_id_error(self, handle_id):
        # Do not use handle_records for validation: ShowRadius=No may hide a hovered grip.
        if not isinstance(handle_id, tuple) or len(handle_id) != 3:
            return "invalid handle id"
        owner, edge_id, endpoint = handle_id
        if owner != self._owner_name or type(edge_id) is not int or edge_id not in self._edge_points:
            return "handle is not on a selected edge"
        anchors = self._edge_endpoints[edge_id]
        independent = self.phase == self.EDIT_HANDLES and not self.chamfer and anchors[0] != anchors[1]
        if (independent and (type(endpoint) is not int or endpoint not in (0, 1))
                or not independent and endpoint is not None):
            return "handle does not belong to the current phase"
        if self.phase not in (self.SELECT_EDGES, self.EDIT_HANDLES):
            return "handle does not belong to the current phase"
        return None

    def set_handle_radius(self, handle_id, value, ctx, *, preview_only=False):
        """Edit a stable handle; optionally simulate sections without building a solid.

        SELECT_EDGES sets that edge's scalar pair; EDIT_HANDLES sets only the named
        endpoint of an open fillet edge. Closed edges retain one scalar handle.
        The UI timer uses preview_only for live wires. Exact validation stays pending
        (no current candidate key) until release/typed acceptance or command execution.
        """
        error = self._handle_id_error(handle_id)
        if error:
            return f"{self.name}: {error}", False
        if not self._owner_is_current(ctx):
            self._invalidate_candidate()
            self._clear_sections()
            return f"{self.name}: the source document object changed", False
        if self.handle_input_active() and (
                not self._handle_input_is_current(ctx) or handle_id != self._handle_input["id"]):
            return f"{self.name}: finish the active handle input first", False
        _, edge_id, endpoint = handle_id
        independent = endpoint is not None
        self._invalidate_candidate()
        self._clear_sections()
        try:
            radius = float(value)
            if not math.isfinite(radius) or radius <= 0:
                raise ValueError("radius must be finite and positive")
        except (TypeError, ValueError, OverflowError):
            self._candidate_key = self._state_key()
            self._candidate_error = f"the {self.size_word} must be finite and positive"
            return f"{self.name}: {self._candidate_error}", False
        pair = list(self._edge_radii[edge_id])
        if independent:
            pair[endpoint] = radius
        else:
            pair = [radius, radius]
        self._edge_radii[edge_id] = tuple(pair)
        self._refresh_sections()
        if preview_only:
            self._candidate_error = "exact preview pending; release to validate"
            warning = (f"; section preview unavailable: {self._section_error}"
                       if self._section_error else "")
            return (f"{self.name}: {self.size_word} {_num(radius)} -- "
                    f"{self._candidate_error}{warning}"), False
        self._refresh_candidate(ctx)
        if self._candidate is None:
            return f"{self.name}: {self._candidate_error}. Nothing was changed.", False
        return f"{self.name}: {self.size_word} {_num(radius)}", False

    def preview_geometry(self):
        """The already-built exact preview, or None. This getter never calls OCCT."""
        if not self.opt["Preview"] or self._candidate_key != self._state_key():
            return None
        return self._candidate

    def _refresh_candidate(self, ctx):
        self._candidate = None
        self._candidate_key = self._state_key()
        self._candidate_error = None
        if self._owner_name is None or not self._edge_points:
            return None
        try:
            size, parameters = self._bevel_parameters()
            out = bridge.bevel_edges_occt(
                self._owner_geom, size, chamfer=self.chamfer,
                edge_indices=list(self._edge_points), **parameters)
            self._candidate = _exact_or_none(out)
        except (ValueError, RuntimeError) as e:
            self._candidate_error = str(e)
            return None
        if self._candidate is None:
            self._candidate_error = bridge.last_occt_decline() or "the kernel declined"
        return self._candidate

    def pick_edge(self, name, edge_id, interior_point, action, ctx):
        """Apply one viewport edge hit.

        Plain click and Shift+click add. Ctrl+click removes. Only the first accepted add binds
        the owner; every later action must name that same solid. ``edge_id`` remains the
        authoritative identity while ``interior_point`` locates its scalar selection handle.
        """
        if self.handle_input_active():
            return f"{self.name}: finish the active handle input first", False
        if self.phase != self.SELECT_EDGES:
            return f"{self.name}: choose SelectEdges before changing edge selection", False
        mode = (action or "plain").strip().lower()
        if mode in ("control", "remove"):
            mode = "ctrl"
        if mode in ("add", "none", ""):
            mode = "plain"
        if mode not in ("plain", "shift", "ctrl"):
            return f"{self.name}: unsupported edge action {action!r}", False
        if self._owner_name is not None and name != self._owner_name:
            return (f"{self.name}: edge {edge_id} belongs to {name}, but the first edge "
                    f"belongs to {self._owner_name}"), False
        if type(edge_id) is not int:
            return f"{self.name}: edge id must be an integer", False
        if mode == "ctrl":
            if self._owner_name is None or edge_id not in self._edge_points:
                return "that edge is not selected", False
            del self._edge_points[edge_id]
            del self._edge_radii[edge_id]
            del self._edge_endpoints[edge_id]
        else:
            try:
                # Validate every pick, not only the first one. Retain anchors for already
                # selected edges so a re-pick never reverses an edited endpoint law.
                self._bind_owner(name, edge_id, ctx)
                anchors = bridge.bevel_edge_endpoints_occt(self._owner_geom, [edge_id])[edge_id]
                point = tuple(float(v) for v in interior_point)
                if len(point) != 3 or not all(math.isfinite(v) for v in point):
                    raise ValueError("edge pick must be a finite XYZ point")
            except (LookupError, ValueError, RuntimeError) as e:
                return f"{self.name}: {e}. Nothing was changed.", isinstance(e, LookupError)
            self._edge_points[edge_id] = point
            self._edge_endpoints.setdefault(edge_id, tuple(tuple(p) for p in anchors))
            size = float(self.opt[self.size_option])
            self._edge_radii.setdefault(edge_id, (size, size))
        self._sync_pick_mirrors()
        self._invalidate_candidate()
        self._refresh_sections()
        try:
            self._refresh_affected_edges()
        except Exception as e:  # noqa: BLE001
            return f"{self.name}: unable to inspect the exact bevel contour -- {e}", False
        if self.opt["Preview"] and self._edge_points:
            self._refresh_candidate(ctx)
        selected = len(self._edge_points)
        affected = len(self._affected_edges)
        extra = f"; {affected} tangent-contour edges will be affected" if affected > selected else ""
        warning = f"; endpoint sections unavailable: {self._section_error}" if self._section_error else ""
        if self._candidate_error:
            warning += f"; {self._candidate_error}"
        return f"{selected} edge(s) selected{extra}{warning} -- press Enter when done", False

    def on_mode(self, name, ctx):
        if self.handle_input_active():
            return f"{self.name}: finish the active handle input first", False
        if name == "SelectEdges":
            self.phase = self.SELECT_EDGES
            return "Select more edges. Press Enter when done.", False
        return super().on_mode(name, ctx)

    def on_option_set(self, option, value, ctx):
        if option.name == self.size_option and (not math.isfinite(float(value)) or float(value) <= 0.0):
            self.opt[option.name] = option.default
            self._invalidate_candidate()
            self._clear_sections()
            self._candidate_key = self._state_key()
            self._candidate_error = f"the {self.size_word} must be positive and finite"
            return f"{self.name}: {self._candidate_error} (got {_num(value)})", False
        if option.name == self.size_option:
            # MEASURED filletedge.htm: NextRadius specifies the NEXT edge's radius.
            # Existing laws (and therefore the candidate key) do not change here.
            return f"{self.name}: next edge {self.size_word} {_num(value)}", False
        elif (option.name == "Preview" and value and self._edge_points
              and self._candidate_key != self._state_key()):
            self._refresh_candidate(ctx)
        return super().on_option_set(option, value, ctx)

    def _feed(self, text, ctx):
        raw = (text or "").strip()
        if self.handle_input_active():
            if not self._handle_input_is_current(ctx):
                self.cancel_handle_input(ctx)
                return f"{self.name}: the handle or source document object changed", False
            session = self._handle_input
            try:
                radius = (commands.parse_number(raw) if raw
                          else session["pair"][session["id"][2] or 0])
                if not math.isfinite(radius) or radius <= 0:
                    raise ValueError("not finite and positive")
            except (ValueError, OverflowError):
                match = opts.match(raw, self._handle_input_options, self.opt)
                if match.status == opts.BRANCH:
                    return f"{self.name}: {match.option.name} is not implemented yet", False
                if match.status != opts.NONE:
                    return match.message, False
                # Keep the subprompt, but never leave a successful pre-typo candidate
                # available for a subsequent release/commit. Escape still has the snapshot.
                return self.set_handle_radius(session["id"], float("nan"), ctx)
            result = self.set_handle_radius(session["id"], radius, ctx)
            if self._candidate is not None:
                self.finish_handle_input()
            return result
        if self.pending_option is not None:
            return super()._feed(raw, ctx)
        if raw:
            match = opts.match(raw, self.options, self.opt)
            if match.status != opts.NONE:
                return self._apply_option(match, ctx)
            return (f"{self.name}: select an edge in the viewport; whole-object and point "
                    f"input are not accepted"), False
        if self.phase == self.SELECT_EDGES:
            if not self._edge_points:
                return f"{self.name}: no edges selected", False
            self.phase = self.EDIT_HANDLES
            if self._candidate_key != self._state_key():
                self._refresh_candidate(ctx)
            return f"Edit {self.size_word} handles, then press Enter to accept", False
        return self.execute(ctx)

    def execute(self, ctx):
        if self.handle_input_active():
            return f"{self.name}: finish the active handle input first", False
        if not self._owner_is_current(ctx):
            self._invalidate_candidate()
            self._clear_sections()
            return f"{self.name}: the source document object changed. Nothing was changed.", True
        name, geom = self._solid(ctx)
        if not isinstance(geom, (r3.Brep, r3.Extrusion)):
            return f"{self.name}: {name!r} is not a polysurface", True
        if not self._edge_points:
            return f"{self.name}: no edges selected -- nothing changed", True
        if self._candidate_key != self._state_key():
            self._refresh_candidate(ctx)
        out = self._candidate
        sizes = {radius for pair in self._edge_radii.values() for radius in pair}
        size_text = (_num(min(sizes)) if len(sizes) == 1 else
                     f"{_num(min(sizes))}..{_num(max(sizes))}")
        if out is None:
            why = self._candidate_error or "the kernel declined"
            return (f"{self.name}: unable to {self.verb} {name} at {self.size_word} "
                    f"{size_text} -- {why}. Nothing was changed."), True
        display = _replace(ctx, name, out, self.past)
        return (f"{self.past} {len(self._edge_points)} edge(s) of {name} at "
                f"{self.size_word} {size_text}: {display}"), True


def _not_built(names):
    return tuple(opts.Option(n, opts.MODE, help="Not implemented yet.") for n in names)


def _edge_options(show_name, size_name, size_help):
    return (
        opts.Option(show_name, opts.TOGGLE, default=True,
                    help="Show size handles in the viewport."),
        opts.Option(size_name, opts.VALUE, default=1.0, value_kind="number", help=size_help),
    ) + _not_built(("ChainEdges", "FaceEdges", "PreviousEdgeSelection")) + (
        opts.Option("Preview", opts.TOGGLE, default=False,
                    help="Display the cached exact result while editing."),
    ) + _not_built(("Edit", "TrimAndJoin", "History")) + (
        opts.Option("SelectEdges", opts.MODE,
                    help="Return to edge selection after it has been closed."),
    )


class FilletEdgeCmd(_EdgeBevelCmd):
    __doc__ = _EdgeBevelCmd.__doc__
    name = "FilletEdge"
    description = "Round the picked edges of a solid with an exact rolling-ball fillet."
    options = _edge_options("ShowRadius", "NextRadius",
                            "Specifies the radius for the next edge.")


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
    show_option = "ShowChamferDistance"
    options = _edge_options("ShowChamferDistance", "NextChamferDistance",
                            "Specifies the chamfer distance for the next edge.")


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
    accepts_subobjects = True

    @staticmethod
    def _selected_subobjects(ctx):
        getter = getattr(ctx, "selected_subobjects", None)
        if not callable(getter):
            try:
                from . import ui
                getter = ui.ordered_selected_subobjects
            except ImportError:
                getter = None
        try:
            return list(getter() or []) if callable(getter) else []
        except Exception:  # noqa: BLE001
            return []

    @staticmethod
    def _clear_subobjects(ctx):
        clearer = getattr(ctx, "clear_subobject_selection", None)
        if callable(clearer):
            try:
                clearer()
                return
            except Exception:  # noqa: BLE001
                pass
        try:
            from . import ui
            ui.clear_subobject_selection()
        except (ImportError, AttributeError):
            pass

    def _selection_refs(self, ctx):
        # Python only orchestrates the UI reference protocol; exact extraction belongs
        # to curve_inputs / OCCT. Preserve the getter's order, never sort by owner/id.
        selected = self._selected_subobjects(ctx)
        if selected:
            refs = [curve_inputs.reference_from_selection(item) for item in selected]
            for ref in refs:
                curve_inputs.resolve(ctx, ref)
            return list(dict.fromkeys(refs))
        getter = getattr(ctx, "selected_objects", None)
        return [obj.name for obj in (getter() or [])] if callable(getter) else []

    def _clear_selection(self, ctx):
        selector = getattr(ctx, "select_by_names", None)
        if callable(selector):
            selector([])
        self._clear_subobjects(ctx)

    def _feed_objects_step(self, text, ctx):
        try:
            refs = [text] if text else self._selection_refs(ctx)
        except curve_inputs.CurveInputError as exc:
            return f"{self.name}: {exc}", False
        if not refs:
            return "Nothing selected", False
        if len(refs) != 1:
            return f"{self.name}: select one curve, segment or surface edge for this rail", False
        self.args.append(refs[0])
        self.step_index += 1
        self._clear_selection(ctx)
        return None, False

    def _feed_objects_multi_step(self, text, ctx):
        if text:
            self._pending_multi.append(text)
            return f"{text} added -- press Enter when done", False
        try:
            # Explicit components beat stale whole-object selection, including the
            # selected parent. Typed names remain deliberate inputs, not UI mirrors.
            if self._selected_subobjects(ctx):
                refs = list(self._pending_multi) + self._selection_refs(ctx)
            else:
                refs = list(self._pending_multi) or self._selection_refs(ctx)
        except curve_inputs.CurveInputError as exc:
            return f"{self.name}: {exc}", False
        refs = list(dict.fromkeys(refs))
        if not refs:
            return "Nothing selected", False
        self._pending_multi = []
        self.args.append(refs)
        self.step_index += 1
        self._clear_selection(ctx)
        return self.execute(ctx)

    def _curves(self, ctx):
        """(rails, sections) as geometry, or a refusal string."""
        rail_names = list(self.args[:self.rails])
        section_names = [n for n in (self.args[self.rails] if len(self.args) > self.rails
                                     else []) if n not in rail_names]
        rails = []
        for i, ref in enumerate(rail_names):
            try:
                rails.append(curve_inputs.resolve(ctx, ref))
            except curve_inputs.CurveInputError as exc:
                which = ("Rail curve is not valid" if self.rails == 1 else
                         ("First", "Second")[i] + " rail curve is not valid")
                return f"{which} ({exc})"
        if not section_names:
            return "no cross-section curve was selected"
        if len(section_names) > 1:
            return (f"sweeping through {len(section_names)} cross-sections is not "
                    f"implemented yet -- one cross-section is")
        try:
            sec = curve_inputs.resolve(ctx, section_names[0])
        except curve_inputs.CurveInputError as exc:
            return f"Shape curve 1 is invalid ({exc})"
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
    """Sweep1 keeps the established single-section Freeform/Roadlike routes.

    OURS bounded multi-section Freeform accepts two to four already-oriented whole
    curves in explicit typed traversal order. No component-generation or arbitrary
    profile/seam solver is available; unsupported inputs refuse without fallback.
    """
    name = "Sweep1"
    description = "Sweep cross-section curves along one rail into a smooth surface."
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
        self._accepted_sources = {}
        self._source_environment = None

    def _capture_source(self, ctx, ref):
        # Keep the legacy component/single-section route unchanged. Components
        # cannot authorize the new branch without owner topology-generation checks.
        if ref in self._accepted_sources:
            return
        try:
            if not isinstance(ref, str):
                raise ValueError("multi-section components are not implemented yet; use whole curves")
            owners = [o for o in ctx.model.file.Objects
                      if (o.Attributes.Name or "").lower() == ref.lower()]
            if len(owners) != 1:
                raise ValueError(f"source {ref}: missing or ambiguous same-name owners")
            owner = owners[0]
            curve = curve_inputs.resolve(ctx, ref)
            if curve.IsClosed or curve.IsPeriodic:
                raise ValueError("multi-section closed/periodic sources are not implemented yet")
            self._accepted_sources[ref] = (str(owner.Attributes.Id), bridge._freeform_source_hash(curve))
        except Exception as exc:
            self._accepted_sources[ref] = str(exc)
        if self._source_environment is None:
            from .state import app_state
            model = ctx.model
            self._source_environment = (app_state, ctx, model, model.file,
                                        app_state.doc_path, model.path, self._source_settings(model))

    @staticmethod
    def _source_settings(model):
        settings = model.file.Settings
        return (settings.ModelUnitSystem, settings.ModelAbsoluteTolerance,
                settings.ModelRelativeTolerance, settings.ModelAngleToleranceRadians,
                model.current_layer_index)

    def _feed_objects_step(self, text, ctx):
        result = super()._feed_objects_step(text, ctx)
        if self.step_index == 1 and self.args:
            self._capture_source(ctx, self.args[0])
        return result

    def _feed_objects_multi_step(self, text, ctx):
        if text:
            self._capture_source(ctx, text)
        elif not self._pending_multi and not self._selected_subobjects(ctx):
            refs = self._selection_refs(ctx)
            if len(refs) > 1:
                return ("Sweep1: whole-object selection order is ambiguous; type each "
                        "section name in traversal order, then press Enter"), False
            for ref in refs:
                self._capture_source(ctx, ref)
        return super()._feed_objects_multi_step(text, ctx)

    def _resolve_accepted_sources(self, ctx, refs):
        from .state import app_state

        env = self._source_environment
        if env is None:
            raise ValueError("Freeform requires ordered source capture through the command")
        state, original_ctx, model, file, path, model_path, settings = env
        if (app_state is not state or state.ctx is not ctx or ctx is not original_ctx
                or state.model is not model or ctx.model is not model or model.file is not file
                or state.active_command is not self or state.doc_path != path or model.path != model_path):
            raise ValueError("stale or cancelled Freeform command/document")
        if self._source_settings(model) != settings or self.style != "Freeform":
            raise ValueError("Freeform units/tolerance/layer or frame style changed")
        ids, curves = [], []
        for ref in refs:
            accepted = self._accepted_sources.get(ref)
            if not isinstance(accepted, tuple):
                raise ValueError(accepted or "multi-section components are not implemented yet; use whole curves")
            oid, fingerprint = accepted
            owner = file.Objects.FindId(oid)
            if owner is None:
                raise ValueError("stale Freeform source UUID is missing")
            curve = owner.Geometry
            if bridge._freeform_source_hash(curve) != fingerprint:
                raise ValueError("stale Freeform source geometry changed after acceptance")
            ids.append(oid)
            curves.append(curve)
        if len(set(ids)) != len(ids):
            raise ValueError("Freeform requires distinct source UUIDs")
        return tuple(ids), curves

    def execute(self, ctx):
        sections = self.args[1] if len(self.args) > 1 else []
        if len(sections) <= 1:
            return super().execute(ctx)
        if self.style != "Freeform" or not 2 <= len(sections) <= 4:
            return (f"Sweep1: {self.style} with {len(sections)} cross-sections is not implemented yet; "
                    "bounded Freeform supports two to four. Nothing was created."), True
        refs = (self.args[0], *sections)
        for ref in refs:
            if not isinstance(ref, str):
                return "Sweep1: multi-section components are not implemented yet; use whole curves", True
            error = self._accepted_sources.get(ref)
            if isinstance(error, str):
                return f"Sweep1: {error}. Nothing was created.", True
        out = None
        master = None
        stack = getattr(ctx, "undo", None)
        record = getattr(stack, "_open", None)
        before = record.before if record is not None else None
        file = (self._source_environment[3] if self._source_environment is not None
                else ctx.model.file)
        baseline = before
        try:
            resolve = lambda: self._resolve_accepted_sources(ctx, refs)
            ids, curves = resolve()
            if stack is None or not stack.enabled or record is None:
                raise ValueError("bounded Freeform requires an active Undo record")
            baseline = ctx.model.snapshot()
            required = float(ctx.model.file.Settings.ModelAbsoluteTolerance)
            if not math.isfinite(required) or required < 1e-9:
                raise ValueError("Freeform document precision is tighter than supported [1e-9, 1e-3]")
            tolerance = min(1e-7, required)
            out = bridge.sweep1_freeform_from_sources(
                curves[0], curves[1:], source_ids=ids, resolve_sources=resolve, tolerance=tolerance)
            master = kernel.master_of(out)
            resolve()
            _, display = ctx.add_geometry(out, "Sweep", publication_check=resolve)
            return f"Created {display}", True
        except Exception as exc:
            if out is not None:
                kernel.discard_master_binding(kernel.master_key_of(out), master)
            if baseline is not None:
                ctx._settle_failed_publication(record, before, file, baseline)
            from .state import app_state
            return (f"Sweep1 failed: {exc}. Nothing was created.",
                    app_state.active_command is self)

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
