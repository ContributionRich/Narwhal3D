"""`FilletEdge`, `ChamferEdge` and `Cap` as COMMANDS -- exact, or refused by name.
`Sweep1` and `Sweep2` as commands -- one smooth face with a master, on CURVED rails.

These gates reject missing command registrations and inexact results.
The producers they call were already exact
(`bridge.bevel_edges_occt`, `bridge.cap_planar_holes_occt`); these gates hold the COMMAND
to them, and to never reaching the faceted mesh route (`edgefillet.fillet_edges`).

Every number is a CLOSED FORM, not a value read back from a previous run:
* a cube of side a, every edge filleted r: the Minkowski sum of a cube of side s = a - 2r
  with a ball of radius r -> s^3 + 6 s^2 r + 3 s pi r^2 + 4/3 pi r^3;
* every edge chamfered d: a^3 - 12 (a - 2d) d^2 / 2 - 8 (5/6) d^3 (each corner cube keeps
  the region x+y, y+z, z+x >= d, which is 1/6 of it);
* one edge filleted: a^3 - (1 - pi/4) r^2 a;
* a cylinder's top circle (radius R) filleted r: Pappus -- the removed spandrel has area
  (1 - pi/4) r^2, and its centroid lies r (5/6 - pi/4) / (1 - pi/4) inside the corner.

Run: PYTHONPATH=vendor/occt tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 \
         spike/test_solid_commands.py
"""
import importlib.util
import math
import os
import sys
import tempfile
import types

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load("narwhal3d.cplane", "narwhal3d/cplane.py")
bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")
model = _load("narwhal3d.model", "narwhal3d/model.py")
_load("narwhal3d.options", "narwhal3d/options.py")
commands = _load("narwhal3d.commands", "narwhal3d/commands.py")
from narwhal3d import kernel  # noqa: E402
from narwhal3d.kernel import occt_backend as ob  # noqa: E402

checks = 0
failures = []


def check(cond, label):
    global checks
    checks += 1
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        failures.append(label)


print("-- the commands exist --")
for key in ("filletedge", "chamferedge", "cap", "sweep1", "sweep2"):
    check(key in commands.COMMANDS, f"{key!r} is a registered command")
if failures:
    print(f"\n{checks - len(failures)}/{checks} checks passed")
    for f_ in failures:
        print("FAIL:", f_)
    sys.exit(1)

if not ob.available():
    print(f"  skip  geometry gates need OCCT ({ob.unavailable_reason()})")
    sys.exit(0)


class Ctx:
    def __init__(self, named):
        self.model = model.NarwhalModel()
        self.added = []
        self.snapshots = []          # the object names present at each note_change
        self.selection = []          # names, as a viewport selection would hold them
        self.subobjects = []         # (owner, kind, index), as Ctrl+Shift selection provides
        for nm, g in named:
            self.model.add_object(g, nm, 0)

    def names(self):
        return [o.Attributes.Name for o in self.model.file.Objects]

    def add_geometry(self, geom, base_name, layer_index=None):
        self.note_change(base_name)
        nm = f"{base_name}_{len(self.added) + 1}"
        self.model.add_object(geom, nm, 0)
        self.added.append(geom)
        return nm, nm

    def geom_by_name(self, name):
        for o in self.model.file.Objects:
            if o.Attributes.Name == name:
                return o.Geometry
        return None

    def object_by_name(self, name):
        return None

    def selected_objects(self):
        return [types.SimpleNamespace(name=n) for n in self.selection]

    def select_by_names(self, names):
        self.selection = list(names)

    def selected_subobjects(self):
        return list(self.subobjects)

    def clear_subobject_selection(self):
        self.subobjects = []

    def active_cplane(self):
        return None

    def note_change(self, label=None):
        self.snapshots.append(self.names())


def run(key, named, lines):
    ctx = Ctx(named)
    c = type(commands.COMMANDS[key])()
    msg, done = c.begin(ctx)
    last = msg
    for line in lines:
        if done:
            break
        msg, done = c.feed(line, ctx)
        last = msg or last
    g = ctx.added[0] if ctx.added else None
    return g, (kernel.master_of(g) if g is not None else None), last, ctx, c, done


def rel(a, b):
    return abs(a - b) / abs(b)


def P(*c):
    return ",".join(str(v) for v in c)


def edge_id_at(geom, point):
    operand = bridge._occt_operand(geom)
    return bridge._edgepick().pick(ob.edge_curves(operand), point)


def run_bevel(key, named, picks, options=(), enters=2):
    """Run the edge-only API. Each pick is (name, point[, action[, explicit_edge_id]])."""
    ctx = Ctx(named)
    c = type(commands.COMMANDS[key])()
    msg, done = c.begin(ctx)
    last = msg
    for option in options:
        msg, done = c.feed(option, ctx)
        last = msg or last
    for pick_spec in picks:
        name, point = pick_spec[:2]
        action = pick_spec[2] if len(pick_spec) > 2 else "plain"
        edge_id = pick_spec[3] if len(pick_spec) > 3 else edge_id_at(ctx.geom_by_name(name), point)
        msg, done = c.pick_edge(name, edge_id, point, action, ctx)
        last = msg or last
    for _ in range(enters):
        if done:
            break
        msg, done = c.feed("", ctx)
        last = msg or last
    g = ctx.added[0] if ctx.added else None
    return g, (kernel.master_of(g) if g is not None else None), last, ctx, c, done


def no_mesh_derived(ctx):
    """Nothing in the document is MESH_DERIVED, and everything the command ADDED is honest.

    (The test's own bare inputs are untagged on purpose, which `honesty_problem` flags --
    so honesty is asked of the command's output, not of the input it was given.)"""
    if any(kernel.representation_of(o.Geometry) == kernel.MESH_DERIVED
           for o in ctx.model.file.Objects):
        return False
    return not any(kernel.honesty_problem(g) for g in ctx.added)


A, R = 4.0, 0.5
BOX = bridge.make_box((0, 0, 0), (A, A, 0), A)
h = A / 2
EDGE_MIDS = ([(h, y, z) for y in (0, A) for z in (0, A)]
             + [(x, h, z) for x in (0, A) for z in (0, A)]
             + [(x, y, h) for x in (0, A) for y in (0, A)])
ALL = [P(*p) for p in EDGE_MIDS]

print("\n-- FilletEdge lifecycle: edge-only, modifiers, phases, handles, preview --")
pick = (h, 0.0, 0.0)
pick2 = (0.0, h, 0.0)
eid = edge_id_at(BOX, pick)
eid2 = edge_id_at(BOX, pick2)
cx = Ctx([("Box", BOX), ("Other", BOX.Duplicate())])
cx.selection = ["Box"]                  # whole-object preselection must not be consumed
c = type(commands.COMMANDS["filletedge"])()
msg, done = c.begin(cx)
check(not done and c.phase == c.SELECT_EDGES and not c.args and c._owner_name is None
      and c.prompt.startswith("Select edges to fillet.")
      and "polysurface" not in c.prompt.lower(),
      f"command begins edge-only in SELECT_EDGES, with no whole-object prompt ({c.prompt!r})")
check(c.opt["ShowRadius"] is True and c.opt["Preview"] is False,
      f"ShowRadius defaults Yes and Preview defaults No ({c.opt})")
msg, done = c.pick_edge("Box", eid, pick, "plain", cx)
check(not done and c.edge_selection_keys() == frozenset({("Box", eid)})
      and c._owner_name == "Box",
      f"first edge binds its owner and its supplied id is authoritative ({msg!r})")
msg, done = c.pick_edge("Other", edge_id_at(cx.geom_by_name("Other"), pick2), pick2,
                        "shift", cx)
check(not done and "first edge" in msg and c.edge_selection_keys() == {("Box", eid)},
      f"an edge on another solid is refused ({msg!r})")
c.pick_edge("Box", eid2, pick2, "shift", cx)
check(c.edge_selection_keys() == {("Box", eid), ("Box", eid2)},
      "plain/Shift add edge ids")
c.pick_edge("Box", eid, pick, "ctrl", cx)
check(c.edge_selection_keys() == {("Box", eid2)}, "Ctrl removes an edge id")
c.pick_edge("Box", eid, pick, "plain", cx)
original_names = cx.names()
msg, done = c.feed("", cx)
check(not done and c.phase == c.EDIT_HANDLES and cx.names() == original_names
      and not cx.added and not cx.snapshots,
      f"FIRST Enter only enters EDIT_HANDLES and does not touch the document ({msg!r})")
check(len(c.handle_records()) == 4
      and {record["id"] for record in c.handle_records()} == {
          ("Box", edge, end) for edge in (eid, eid2) for end in (0, 1)},
      "EDIT_HANDLES exposes two independently identified endpoint records per open edge")
c.feed("ShowRadius=No", cx)
check(c.handle_records() == (), "ShowRadius=No really hides handle records")
msg, done = c.feed("SelectEdges", cx)
check(not done and c.phase == c.SELECT_EDGES and c.edge_selection_keys() == {
          ("Box", eid), ("Box", eid2)},
      f"SelectEdges returns to edge selection without discarding it ({msg!r})")
c.pick_edge("Box", eid2, pick2, "ctrl", cx)

# Build counting proves preview_geometry() is a draw-safe getter and commit reuses the exact
# candidate rather than running OCCT again.
real_bevel = bridge.bevel_edges_occt
build_calls = []
def counted_bevel(*args, **kwargs):
    build_calls.append((args, kwargs))
    return real_bevel(*args, **kwargs)
bridge.bevel_edges_occt = counted_bevel
try:
    c.feed("Preview=Yes", cx)
    before_enter = len(build_calls)
    msg, done = c.feed("", cx)
    preview = c.preview_geometry()
    after_build = len(build_calls)
    same_preview = c.preview_geometry() is preview and len(build_calls) == after_build
    document_still_original = cx.names() == original_names and not cx.added
    msg, done = c.feed("", cx)
finally:
    bridge.bevel_edges_occt = real_bevel
pmaster = kernel.master_of(preview) if preview is not None else None
check(before_enter == 1 and after_build == 1 and same_preview
      and build_calls[0][1].get("edge_indices") == [eid],
      f"exact preview uses the authoritative stable edge id and is built on command events, "
      f"never by its getter ({build_calls})")
check(preview is not None and pmaster is not None and ob.is_airtight(pmaster)
      and document_still_original,
      "preview candidate is exact and airtight while the document remains unchanged")
check(done and len(build_calls) == after_build and cx.added and cx.added[0] is preview,
      "SECOND Enter commits and reuses the current cached candidate")

print("\n-- FilletEdge independent endpoint radii and cached section grips --")
from narwhal3d.undo import UndoStack


class UndoCtx(Ctx):
    def __init__(self, named):
        super().__init__(named)
        self.undo = UndoStack()

    def begin_undo(self, name):
        self.undo.begin(name)

    def end_undo(self):
        self.undo.end()

    def note_change(self, label=None):
        super().note_change(label)
        if not self.undo.captured:
            self.undo.capture(self.model.snapshot())


cx = UndoCtx([("Box", BOX)])
c = type(commands.COMMANDS["filletedge"])()
c.begin(cx)
c.feed("NextRadius=0.4", cx)
c.pick_edge("Box", eid, pick, "plain", cx)
scalar_id = ("Box", eid, None)
check(len(c.handle_records()) == 1 and c.handle_records()[0]["id"] == scalar_id
      and c.handle_records()[0]["radius"] == 0.4,
      "SELECT_EDGES has exactly one scalar-radius handle with a phase-specific stable id")
msg, done = c.set_handle_radius(scalar_id, 0.5, cx)
check(not done and c._edge_radii[eid] == (0.5, 0.5) and c._candidate is not None,
      "editing a selection handle changes only that edge's scalar pair and caches a candidate")
c.feed("NextRadius=0.7", cx)
check(c._edge_radii[eid] == (0.5, 0.5), "NextRadius does not overwrite already selected edges")
other_point = (h, A, A)
other_id = edge_id_at(BOX, other_point)
c.pick_edge("Box", other_id, other_point, "shift", cx)
check(c._edge_radii[other_id] == (0.7, 0.7) and c._edge_radii[eid] == (0.5, 0.5),
      "NextRadius binds to the next unselected edge")
c.pick_edge("Box", eid, pick, "shift", cx)
check(c._edge_radii[eid] == (0.5, 0.5), "re-picking a selected edge never resets its radius law")
c.pick_edge("Box", other_id, other_point, "ctrl", cx)
check(c.handle_records()[0]["id"] == scalar_id and other_id not in c._edge_radii,
      "removing another edge does not renumber the retained handle")
c.feed("", cx)
records = c.handle_records()
ids = tuple(record["id"] for record in records)
anchors = bridge.bevel_edge_endpoints_occt(cx.geom_by_name("Box"), [eid])[eid]
check(ids == (("Box", eid, 0), ("Box", eid, 1))
      and [record["anchor"] for record in records] == [tuple(p) for p in anchors],
      "first Enter creates two endpoint records anchored in exact backend order")
msg, done = c.set_handle_radius(scalar_id, 0.9, cx)
check(not done and "current phase" in msg and c._edge_radii[eid] == (0.5, 0.5),
      "the scalar selection id cannot accidentally edit both endpoints in EDIT_HANDLES")

real_sections, real_sample = bridge.bevel_edge_sections_occt, bridge.sample_curve
section_calls, sample_calls, build_calls = [], [], []
def counted_sections(*args, **kwargs):
    section_calls.append(kwargs)
    return real_sections(*args, **kwargs)
def counted_sample(*args, **kwargs):
    sample_calls.append(1)
    return real_sample(*args, **kwargs)
def edit_counted_bevel(*args, **kwargs):
    check(c._candidate is None and c.preview_geometry() is None,
          "candidate is invalidated BEFORE the geometry producer runs")
    build_calls.append(kwargs)
    return real_bevel(*args, **kwargs)
bridge.bevel_edge_sections_occt = counted_sections
bridge.sample_curve = counted_sample
bridge.bevel_edges_occt = edit_counted_bevel
try:
    msg0, _ = c.set_handle_radius(ids[0], 0.3, cx)
    check(c._edge_radii[eid] == (0.3, 0.5),
          f"endpoint 0 changes independently ({msg0})")
    msg1, _ = c.set_handle_radius(ids[1], 0.8, cx)
    check(c._edge_radii[eid] == (0.3, 0.8)
          and tuple(record["id"] for record in c.handle_records()) == ids,
          f"endpoint 1 changes independently without moving handle identity ({msg1})")
    section_curves, lines = c.preview_section_curves(), c.preview_lines()
    counts = (len(build_calls), len(section_calls), len(sample_calls))
    for _ in range(10):
        c.handle_records()
        check(c.preview_geometry() is None and c.preview_section_curves() is section_curves
              and c.preview_lines() is lines,
              "Preview=No getters retain the section cache but hide solid preview")
    check(counts == (2, 2, 4)
          and counts == (len(build_calls), len(section_calls), len(sample_calls)),
          "two radius edits build twice; every draw getter performs ZERO OCCT/sampling work")
    check(len(section_curves) == 2 and all(g.ToNurbsCurve().IsRational for g in section_curves)
          and len(lines) == 2 and all(len(line) > 2 for line in lines)
          and [record["radius"] for record in c.handle_records()] == [0.3, 0.8]
          and all(math.dist(record["grip_point"], record["circle_center"]) > 0
                  and abs(math.dist(record["grip_point"], record["circle_center"])
                          - record["radius"]) < 1e-8 for record in c.handle_records()),
          "Preview=No exposes exact rational section arcs, sampled wires and radial grips")
    check(build_calls[-1]["edge_radii"] == {eid: (0.3, 0.8)}
          and build_calls[-1]["edge_endpoints"] == {eid: tuple(tuple(p) for p in anchors)},
          "candidate producer receives both endpoint radii and the retained source anchors")
    check(cx.names() == ["Box"] and not cx.snapshots and not cx.undo.can_undo,
          "all picks, options, first Enter and handle edits leave the document/undo untouched")
    candidate = c._candidate
    msg, done = c.feed("", cx)
    check(done and candidate is not None and cx.added == [candidate]
          and len(build_calls) == counts[0] and cx.undo.undo_names() == ["FilletEdge"]
          and "0.3..0.8" in msg,
          f"second Enter commits the cached variable candidate in ONE undo record ({msg})")
finally:
    bridge.bevel_edges_occt = real_bevel
    bridge.bevel_edge_sections_occt = real_sections
    bridge.sample_curve = real_sample
recipe = kernel.recipe_of(candidate) if candidate is not None else None
master = kernel.master_of(candidate) if candidate is not None else None
rebuilt = ob.from_recipe(recipe) if recipe is not None else None
check(master is not None and ob.is_airtight(master) and no_mesh_derived(cx)
      and recipe["edge_radii"] == [[0.3, 0.8]]
      and recipe["edge_endpoints"] == [[list(p) for p in anchors]]
      and rebuilt is not None and ob.is_airtight(rebuilt)
      and rel(ob.volume(rebuilt), ob.volume(master)) < 1e-10,
      "committed variable fillet has an honest airtight master and replayable anchored radius recipe")
record = cx.undo.undo(cx.model.snapshot())
if record is not None:
    cx.model.restore(record.before)
check(record is not None and cx.names() == ["Box"] and not cx.undo.can_undo
      and kernel.recipe_of(cx.geom_by_name("Box")) == kernel.recipe_of(BOX),
      "one Undo restores the original exact box, not an intermediate handle edit")

for bad_radius in (100.0, 0, -1, float("nan"), float("inf"), "not a radius"):
    cx = UndoCtx([("Box", BOX)])
    c = type(commands.COMMANDS["filletedge"])()
    c.begin(cx)
    c.feed("NextRadius=0.5", cx)
    c.feed("Preview=Yes", cx)
    c.pick_edge("Box", eid, pick, "plain", cx)
    c.feed("", cx)
    check(c.preview_geometry() is not None, "stale-candidate regression starts with a valid preview")
    msg, done = c.set_handle_radius(("Box", eid, 0), bad_radius, cx)
    check(not done and c._candidate is None and c.preview_geometry() is None,
          f"failed radius {bad_radius!r} clears the previous candidate ({msg})")
    msg, done = c.feed("", cx)
    check(done and not cx.added and cx.names() == ["Box"] and not cx.undo.can_undo
          and "Nothing was changed" in msg,
          f"Enter after failed radius {bad_radius!r} cannot commit a stale candidate")

cx = UndoCtx([("Box", BOX)])
c = type(commands.COMMANDS["filletedge"])()
c.begin(cx)
c.feed("NextRadius=0.5", cx)
c.pick_edge("Box", eid, pick, "plain", cx)
c.pick_edge("Box", other_id, other_point, "shift", cx)
c.feed("", cx)
before_pairs = dict(c._edge_radii)
msg, _ = c.set_handle_radius(("Box", other_id, 1), 0.7, cx)
check(c._edge_radii == {eid: before_pairs[eid], other_id: (0.5, 0.7)}
      and c._candidate is not None,
      f"endpoint editing leaves the other selected edge entirely unchanged ({msg})")
c.feed("Preview=Yes", cx)
real_bevel = bridge.bevel_edges_occt
def failing_bevel(*args, **kwargs):
    raise RuntimeError("deliberate kernel failure")
bridge.bevel_edges_occt = failing_bevel
try:
    msg, _ = c.set_handle_radius(("Box", other_id, 0), 0.6, cx)
finally:
    bridge.bevel_edges_occt = real_bevel
check(c._candidate is None and "deliberate kernel failure" in msg,
      "a raising producer invalidates the candidate just like a declined oversized radius")
msg, done = c.feed("", cx)
check(done and not cx.added and not cx.undo.can_undo,
      "Enter after a producer exception cannot resurrect its stale candidate")

# Unsupported variable contour laws must never degrade to the scalar geometry route.
closed_cyl = bridge.make_cylinder((0, 0, 0), 2, 10)
closed_id = edge_id_at(closed_cyl, (0, 2, 10))
cx = UndoCtx([("Cyl", closed_cyl)])
c = type(commands.COMMANDS["filletedge"])()
c.begin(cx)
c.feed("NextRadius=0.5", cx)
c.pick_edge("Cyl", closed_id, (0, 2, 10), "plain", cx)
c.feed("", cx)
check(len(c.handle_records()) == 1 and c.handle_records()[0]["endpoint"] is None
      and c.preview_section_error() and not c.preview_section_curves(),
      "closed contours retain scalar handles and explicitly report unavailable endpoint sections")
msg, done = c.set_handle_radius(("Cyl", closed_id, 0), 0.3, cx)
check(not done and "current phase" in msg,
      "closed contour independent endpoint edits are explicitly refused")
msg, done = c.feed("", cx)
check(done and cx.added and ob.is_airtight(kernel.master_of(cx.added[0])),
      "unsupported section preview does not disable the existing exact scalar circular fillet")

cx = UndoCtx([("Box", BOX)])
c = type(commands.COMMANDS["filletedge"])()
c.begin(cx)
c.pick_edge("Box", eid, pick, "plain", cx)
c.feed("", cx)
c.set_handle_radius(("Box", eid, 0), 0.4, cx)
check(c._candidate is not None, "cancel regression starts with a real edited candidate")
# state.AppState.cancel drops the active command; no command callback mutates geometry.
c = None
check(cx.names() == ["Box"] and not cx.snapshots and not cx.undo.can_undo
      and kernel.recipe_of(cx.geom_by_name("Box")) == kernel.recipe_of(BOX),
      "discarding an edited command (Escape) leaves exact document geometry and undo unchanged")

print("\n-- FilletEdge: every edge of a 4-box at 0.5, through the command --")
all_picks = [("Box", p) for p in EDGE_MIDS]
g, m, msg, ctx, _c, _d = run_bevel(
    "filletedge", [("Box", BOX)], all_picks, options=("NextRadius=0.5",))
s = A - 2 * R
want = s ** 3 + 6 * s * s * R + 3 * s * math.pi * R * R + 4.0 / 3.0 * math.pi * R ** 3
check(m is not None and ob.face_types(m) == {"PLANE": 6, "CYLINDER": 12, "SPHERE": 8}
      and ob.is_airtight(m),
      f"26 faces {{PLANE:6, CYLINDER:12, SPHERE:8}}, airtight "
      f"({m is not None and ob.face_types(m)}; {msg!r})")
check(m is not None and rel(ob.volume(m), want) < 1e-9,
      f"volume = s^3 + 6s^2 r + 3 s pi r^2 + 4/3 pi r^3 = {want:.6f} "
      f"({m is not None and ob.volume(m)!r})")
check(ctx.geom_by_name("Box") is None and len(ctx.added) == 1,
      "the filleted solid REPLACES the box (one object added, the input gone)")
check(ctx.snapshots and "Box" in ctx.snapshots[0],
      f"the undo snapshot is taken BEFORE the box is removed ({ctx.snapshots[:1]})")
check(no_mesh_derived(ctx), "nothing in the document is mesh-derived")

print("\n-- FilletEdge: ONE picked edge --")
g, m, msg, ctx, _c, _d = run_bevel(
    "filletedge", [("Box", BOX)], [("Box", pick)], options=("NextRadius=0.5",))
want = A ** 3 - (1 - math.pi / 4) * R * R * A
check(m is not None and ob.face_types(m) == {"PLANE": 6, "CYLINDER": 1} and ob.is_airtight(m),
      f"exactly ONE cylinder face ({m is not None and ob.face_types(m)}; {msg!r})")
check(m is not None and rel(ob.volume(m), want) < 1e-12,
      f"volume a^3 - (1 - pi/4) r^2 a = {want:.9f} ({m is not None and ob.volume(m)!r})")
others = [p for p in EDGE_MIDS if p != pick]
gone = ob.distance_to_shape(m, pick) if m is not None else 0.0
kept = max(ob.distance_to_shape(m, p) for p in others) if m is not None else 1.0
check(m is not None and abs(gone - R * (math.sqrt(2) - 1)) < 1e-9 and kept < 1e-9,
      f"the PICKED edge is rounded off (its midpoint now {gone:.6f} = r(sqrt2 - 1) away) and "
      f"the other 11 edges are untouched (max distance {kept:.2e})")

print("\n-- FilletEdge: OCCT tangent propagation is visible before commit (#93) --")
report_box = bridge.make_box((0, 0, 0), (20, 10, 0), 33.8393)
first = bridge.bevel_edges_occt(report_box, 1.0, edge_indices=[4])
first_master = kernel.master_of(first)
report_edges = dict(bridge.subobject_parts(first)["edges"])
edge13 = report_edges[13]
point13 = (tuple((edge13[0][i] + edge13[1][i]) * 0.5 for i in range(3))
           if len(edge13) == 2 else edge13[len(edge13) // 2])
report_ctx = Ctx([("Filleted1", first)])
report_cmd = type(commands.COMMANDS["filletedge"])()
report_cmd.begin(report_ctx)
report_msg, report_done = report_cmd.pick_edge(
    "Filleted1", 13, point13, "plain", report_ctx)
shown_ids = {edge_id for _name, edge_id in report_cmd.edge_selection_keys()}
check(not report_done and report_cmd._edges == [13] and shown_ids == {4, 10, 13}
      and "3 tangent-contour edges will be affected" in report_msg,
      f"one clicked edge stays authoritative while all three OCCT contour edges are exposed "
      f"before Enter ({report_cmd._edges}; {shown_ids}; {report_msg!r})")
report_cmd.feed("", report_ctx)
_report_msg, report_done = report_cmd.feed("", report_ctx)
report_result = report_ctx.added[0] if report_ctx.added else None
report_master = kernel.master_of(report_result) if report_result is not None else None
check(first_master is not None and ob.face_types(first_master) == {"PLANE": 6, "CYLINDER": 1}
      and report_done and report_master is not None
      and ob.face_types(report_master) == {"PLANE": 6, "CYLINDER": 3, "SPHERE": 1}
      and ob.is_airtight(report_master),
      f"the displayed [4, 10, 13] contour matches the exact committed topology "
      f"({report_master is not None and ob.face_types(report_master)})")

cx = UndoCtx([("Filleted1", first)])
c = type(commands.COMMANDS["filletedge"])()
c.begin(cx)
c.pick_edge("Filleted1", 13, point13, "plain", cx)
c.feed("", cx)
msg, done = c.set_handle_radius(("Filleted1", 13, 0), 0.4, cx)
check(not done and c._candidate is None and "contour" in msg and "unsupported" in msg,
      f"variable multi-edge tangent contour is explicitly declined, never scalar-fallback ({msg})")
msg, done = c.feed("", cx)
check(done and not cx.added and not cx.undo.can_undo,
      "unsupported variable contour cannot commit its previous scalar candidate")

print("\n-- FilletEdge: a CURVED edge (a cylinder's top circle) --")
CR, CH = 2.0, 10.0
cyl = bridge.make_cylinder((0, 0, 0), CR, CH)
g, m, msg, ctx, _c, _d = run_bevel(
    "filletedge", [("Cyl", cyl)], [("Cyl", (0, CR, CH))],
    options=("NextRadius=0.5",))
area = (1 - math.pi / 4) * R * R
xbar = R * (5.0 / 6.0 - math.pi / 4) / (1 - math.pi / 4)
want = math.pi * CR * CR * CH - 2 * math.pi * (CR - xbar) * area
check(m is not None and ob.face_types(m) == {"CYLINDER": 1, "PLANE": 2, "TORUS": 1}
      and ob.is_airtight(m) and rel(ob.volume(m), want) < 1e-9,
      f"one TORUS blend, airtight, Pappus volume {want:.9f} "
      f"({m is not None and ob.face_types(m)}, {m is not None and ob.volume(m)!r}; {msg!r})")

print("\n-- ChamferEdge: every edge at 0.5 --")
g, m, msg, ctx, _c, _d = run_bevel(
    "chamferedge", [("Box", BOX)], all_picks, options=("NextChamferDistance=0.5",))
want = A ** 3 - 12 * (A - 2 * R) * R * R / 2 - 8 * (5.0 / 6.0) * R ** 3
check(m is not None and set(ob.face_types(m)) == {"PLANE"} and ob.face_types(m)["PLANE"] == 26
      and ob.is_airtight(m),
      f"26 faces, all PLANE, airtight ({m is not None and ob.face_types(m)}; {msg!r})")
check(m is not None and rel(ob.volume(m), want) < 1e-12,
      f"volume a^3 - 6(a-2d)d^2 - 20/3 d^3 = {want:.6f} ({m is not None and ob.volume(m)!r})")
check(_c.opt["ShowChamferDistance"] is True and _c.opt["Preview"] is False,
      "ChamferEdge exposes the corresponding real handle/preview toggles")

print("\n-- FilletEdge / ChamferEdge: refusals and options --")
g, m, msg, ctx, c, done = run_bevel(
    "filletedge", [("Box", BOX)], all_picks, options=("NextRadius=3",))
check(g is None and done and "Nothing was changed" in (msg or "")
      and ctx.geom_by_name("Box") is not None,
      f"a radius too big for the box is refused with the kernel's reason, box intact ({msg!r})")
ctx = Ctx([("Box", BOX)])
c = type(commands.COMMANDS["filletedge"])()
c.begin(ctx)
msg, done = c.feed("NextRadius=0", ctx)
check(not done and "must be positive" in (msg or "") and c.opt["NextRadius"] == 1.0,
      f"a zero radius is refused and a positive value retained ({msg!r})")
msg, done = c.feed("", ctx)
check(not done and c.phase == c.SELECT_EDGES and not ctx.added,
      f"Enter with no edge selected stays in selection, nothing changed ({msg!r})")
msg, done = c.feed("ChainEdges", ctx)
check("not implemented" in (msg or ""), f"ChainEdges says so ({msg!r})")
ctx = Ctx([("Box", BOX)])
c = type(commands.COMMANDS["chamferedge"])()
c.begin(ctx)
msg, done = c.feed("TrimAndJoin", ctx)
check("not implemented" in (msg or ""), f"TrimAndJoin says so ({msg!r})")
c = type(commands.COMMANDS["filletedge"])()
cx = Ctx([("Box", BOX)])
c.begin(cx)
c.feed("NextRadius", cx)
check(c.prompt == "Fillet radius <1>:",
      f"the size sub-prompt is the MEASURED 'Fillet radius' ({c.prompt!r})")

print("\n-- a bare r3.Brep (no master, no recipe) is REFUSED by name --")
bare = r3.Brep.CreateFromBox(r3.Box(r3.BoundingBox(r3.Point3d(0, 0, 0), r3.Point3d(A, A, A))))
for key in ("filletedge", "chamferedge"):
    cx = Ctx([("Bare", bare)])
    c = type(commands.COMMANDS[key])()
    c.begin(cx)
    msg, done = c.pick_edge("Bare", 1, (h, 0.0, 0.0), "plain", cx)
    check(done and not cx.added and "no exact solid" in msg and no_mesh_derived(cx)
          and cx.geom_by_name("Bare") is not None,
          f"{key} refuses a bare brep by name, with no faceted stand-in ({msg!r})")

print("\n-- Cap --")
rail = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(0, 0, 10)).ToNurbsCurve()
pipe = bridge.pipe_occt(rail, 2.0, cap=False)
check(pipe is not None and not ob.is_airtight(kernel.master_of(pipe)),
      "the input pipe is OPEN (otherwise the cap gate proves nothing)")
g, m, msg, ctx, c, done = run("cap", [("Pipe", pipe)], ["Pipe", ""])
check(m is not None and ob.face_types(m) == {"CYLINDER": 1, "PLANE": 2} and ob.is_airtight(m),
      f"the open pipe caps to {{CYLINDER:1, PLANE:2}}, airtight "
      f"({m is not None and ob.face_types(m)}; {msg!r})")
check(m is not None and rel(ob.volume(m), math.pi * 4 * 10) < 1e-9,
      f"volume pi r^2 h = {math.pi * 40:.9f} ({m is not None and ob.volume(m)!r})")
check(ctx.geom_by_name("Pipe") is None and ctx.snapshots and "Pipe" in ctx.snapshots[0],
      "the capped solid replaces the pipe, and undo can bring the pipe back")
g, m, msg, ctx, c, done = run("cap", [("Box", BOX)], ["Box", ""])
check(done and g is None and not ctx.added
      and "Unable to cap one object that was closed prior to starting." in (msg or ""),
      f"an already-closed solid: measured message, nothing added ({msg!r})")
open_cyl = r3.Brep.CreateFromCylinder(r3.Cylinder(r3.Circle(2.0), 10.0), False, False)
g, m, msg, ctx, c, done = run("cap", [("Open", open_cyl)], ["Open", ""])
check(done and g is None and not ctx.added and "Unable to cap one object" in (msg or "")
      and "no registered master" in (msg or "") and no_mesh_derived(ctx)
      and ctx.geom_by_name("Open") is not None,
      f"a bare open brep: refused WITH the reason, no faceted stand-in ({msg!r})")
g, m, msg, ctx, c, done = run("cap", [("Pipe", pipe), ("Box", BOX)], ["Pipe", "Box", ""])
check(len(ctx.added) == 1 and "Capped 1 object" in (msg or "")
      and "closed prior to starting" in (msg or ""),
      f"two objects: the open one capped, the closed one reported ({msg!r})")

print("\n-- they reload from their recipes --")
one, onem, *_ = run_bevel(
    "filletedge", [("Box", BOX)], [("Box", pick)], options=("NextRadius=0.5",))
capd, capm, *_ = run("cap", [("Pipe", pipe)], ["Pipe", ""])
TMP = tempfile.mkdtemp(prefix="g97_")
f3 = r3.File3dm()
for x in (one, capd):
    f3.Objects.Add(x, None)
path = os.path.join(TMP, "g97.3dm")
f3.Write(path, 8)
want = [(ob.face_types(m_), round(ob.volume(m_), 9)) for m_ in (onem, capm)]
kernel.forget_masters()
back = [o.Geometry for o in r3.File3dm.Read(path).Objects]
restored, lost = bridge.rehydrate_document(back)
got = [(ob.face_types(kernel.master_of(x)), round(ob.volume(kernel.master_of(x)), 9))
       if kernel.master_of(x) is not None else None for x in back]
check(restored == 2 and lost == 0 and got == want,
      f"the one-edge fillet and the capped pipe rebuild identically ({restored}/{lost}; {got})")

# ===========================================================================
# Sweep1 / Sweep2. Gate 4's lesson: a STRAIGHT rail is exact even at degree 1, so it
# cannot expose a sweep that is flat strips. Every rail below is CURVED, and one is
# NON-PLANAR (OCCT declines that one, so it takes the sampled construction, which is the
# route that once really was 18 flat strips).
# ===========================================================================
from narwhal3d import nearest, sweep  # noqa: E402



def surface_samples(brep, n=30):
    f = brep.Faces[0]
    du, dv = f.Domain(0), f.Domain(1)
    return [f.PointAt(du.T0 + (du.T1 - du.T0) * i / n, dv.T0 + (dv.T1 - dv.T0) * j / n)
            for i in range(n + 1) for j in range(n + 1)]


def tube_dev(brep, rail, radius):
    """max | distance(surface point, rail) - radius | -- 0 for the ideal swept tube."""
    return max(abs(nearest.closest_point_on_curve(rail, (p.X, p.Y, p.Z)).distance - radius)
               for p in surface_samples(brep))


def degrees(brep):
    ns = brep.Faces[0].ToNurbsSurface()
    return ns.Degree(0), ns.Degree(1)


SR = 10.0


def qarc(rad):
    return bridge.make_arc((rad, 0, 0), (rad * math.cos(math.pi / 4), rad * math.sin(math.pi / 4), 0),
                           (0, rad, 0))


ARC = qarc(SR)
CIRC = bridge.make_circle((SR, 0, 0), 1.0, types.SimpleNamespace(xaxis=(1, 0, 0), yaxis=(0, 0, 1)))

print("\n-- Sweep1: a circle along a CURVED (planar) rail --")
g, m, msg, ctx, c, done = run("sweep1", [("Rail", ARC), ("Sec", CIRC)], ["Rail", "Sec", ""])
check(g is not None and len(g.Faces) == 1 and m is not None and ob.face_types(m) == {"TORUS": 1},
      f"ONE face, its master a TORUS ({m is not None and ob.face_types(m)}; {msg!r})")
check(g is not None and min(degrees(g)) >= 2,
      f"degree >= 2 in BOTH directions, so smooth along the sweep ({g is not None and degrees(g)})")
check(g is not None and tube_dev(g, ARC, 1.0) < 1e-9,
      f"every surface point is exactly 1 from the rail "
      f"({g is not None and tube_dev(g, ARC, 1.0):.1e})")
want = 2 * math.pi * 1.0 * (SR * math.pi / 2)
got = ob.mass_properties(ob.unique_faces(m)[0], solid=False)["area"] if m is not None else 0
check(rel(got, want) < 1e-9, f"area 2 pi r (pi R / 2) = {want:.9f} ({got!r})")
check(ctx.geom_by_name("Rail") is not None and ctx.geom_by_name("Sec") is not None
      and no_mesh_derived(ctx), "the input curves are kept, and nothing is faceted")

print("\n-- Sweep1: a NON-PLANAR rail (the sampled route) --")
NP = r3.Curve.CreateControlPointCurve([r3.Point3d(0, 0, 0), r3.Point3d(5, 0, 3),
                                       r3.Point3d(10, 5, 0), r3.Point3d(10, 10, 4)], 3)
_t = NP.TangentAtStart
_t = (_t.X, _t.Y, _t.Z)
_x = (-_t[1], _t[0], 0.0)
_n = math.hypot(*_x)
_x = tuple(v / _n for v in _x)
_y = (_t[1] * _x[2] - _t[2] * _x[1], _t[2] * _x[0] - _t[0] * _x[2], _t[0] * _x[1] - _t[1] * _x[0])
NPC = bridge.make_circle((0, 0, 0), 1.0, types.SimpleNamespace(xaxis=_x, yaxis=_y))
check(not NP.IsPlanar(), "the rail really is non-planar (else OCCT takes it and this proves nothing)")
g, m, msg, ctx, c, done = run("sweep1", [("Rail", NP), ("Sec", NPC)], ["Rail", "Sec", ""])
check(g is not None and len(g.Faces) == 1 and m is not None and min(degrees(g)) >= 2,
      f"ONE face with a master, degree >= 2 along the sweep ({g is not None and degrees(g)}; "
      f"{msg!r})")
dev = tube_dev(g, NP, 1.0) if g is not None else math.inf
check(dev < 1e-3,
      f"within 1e-3 of the ideal tube ({dev:.2e}; MEASURED 8.9e-5. Lofting the same stations "
      f"STRAIGHT -- flat strips -- misses by 1.1e-2)")

print("\n-- Sweep1: the Roadlike option reaches the sweep --")
BAR = bridge.make_line((0, -0.5, 0), (0, 0.5, 0))
free = sweep.sweep1(NP, BAR, style="Freeform")
road = sweep.sweep1(NP, BAR, style="Roadlike")


def cp_gap(a, b):
    pa, pb = a.Faces[0].ToNurbsSurface().Points, b.Faces[0].ToNurbsSurface().Points
    if (pa.CountU, pa.CountV) != (pb.CountU, pb.CountV):
        return math.inf
    return max(math.dist((pa[i, j].X, pa[i, j].Y, pa[i, j].Z), (pb[i, j].X, pb[i, j].Y, pb[i, j].Z))
               for i in range(pa.CountU) for j in range(pa.CountV))


check(cp_gap(free, road) > 1e-3, f"(the two styles really differ here: {cp_gap(free, road):.2e})")
g, m, msg, ctx, c, done = run("sweep1", [("Rail", NP), ("Bar", BAR)],
                              ["Roadlike", "Rail", "Bar", ""])
check(g is not None and cp_gap(g, road) < 1e-12 and "Roadlike" in c.active_modes(),
      f"Roadlike builds sweep1.htm's fixed-axis sweep ({g is not None and cp_gap(g, road):.1e})")
g, m, msg, ctx, c, done = run("sweep1", [("Rail", NP), ("Bar", BAR)], ["Rail", "Bar", ""])
check(g is not None and cp_gap(g, free) < 1e-12 and c.active_modes() == {"Freeform"},
      "Freeform is the default")

print("\n-- Sweep2: a semicircle between two concentric CURVED rails --")
RA, RB = qarc(10.0), qarc(12.0)
SEMI = bridge.make_arc((10, 0, 0), (11, 0, 1), (12, 0, 0))
g, m, msg, ctx, c, done = run("sweep2", [("RailA", RA), ("RailB", RB), ("Sec", SEMI)],
                              ["RailA", "RailB", "Sec", ""])
check(g is not None and len(g.Faces) == 1 and m is not None and min(degrees(g)) >= 2,
      f"ONE smooth face with a master ({g is not None and degrees(g)}; {msg!r})")
mid = qarc(11.0)
dev = tube_dev(g, mid, 1.0) if g is not None else math.inf
check(dev < 1e-4, f"on the ideal half torus to 1e-4 ({dev:.2e}; MEASURED 1.5e-5)")
ch = [ctx.geom_by_name(n) is not None for n in ("RailA", "RailB", "Sec")]
check(all(ch) and no_mesh_derived(ctx), "the inputs are kept, nothing is faceted")

print("\n-- Sweep1 / Sweep2: refusals --")
CIRC2 = bridge.make_circle((0, SR, 0), 1.0, types.SimpleNamespace(xaxis=(0, 1, 0), yaxis=(0, 0, 1)))
g, m, msg, ctx, c, done = run("sweep1", [("Rail", ARC), ("Sec", CIRC), ("Sec2", CIRC2)],
                              ["Rail", "Sec", "Sec2", ""])
check(done and not ctx.added and "not implemented" in (msg or ""),
      f"two cross-sections are refused by name, nothing added ({msg!r})")
box = bridge.make_box((0, 0, 0), (1, 1, 0), 1)
g, m, msg, ctx, c, done = run("sweep1", [("Blk", box), ("Sec", CIRC)], ["Blk", "Sec", ""])
check(done and not ctx.added and "Rail curve is not valid" in (msg or ""),
      f"a whole solid rail is refused without guessing one of its edges ({msg!r})")

print("\n-- Sweep1 / Sweep2: exact surface edges as rails --")
edge1 = bridge.extract_subobject(box, "edge", 1)
edge2 = bridge.extract_subobject(box, "edge", 2)
ctx = Ctx([("Blk", box), ("Sec", CIRC)])
c = type(commands.COMMANDS["sweep1"])()
c.begin(ctx)
ctx.subobjects = [("Blk", "Edge", 1)]
msg, done = c.feed("", ctx)
check(not done and c.args == [("edge", "Blk", 1)] and not ctx.subobjects,
      f"Sweep1 consumes one Ctrl+Shift surface edge and clears it before sections ({msg!r})")
c.args.append(["Sec"])
got = c._curves(ctx)
check(not isinstance(got, str)
      and bridge.curve_recipe(got[0][0]) == bridge.curve_recipe(edge1),
      "Sweep1 resolves the selected solid edge to its exact rail curve")

ctx = Ctx([("Blk", box), ("Sec", CIRC)])
c = type(commands.COMMANDS["sweep2"])()
c.begin(ctx)
ctx.subobjects = [("Blk", "Edge", 1)]
c.feed("", ctx)
ctx.subobjects = [("Blk", "Edge", 2)]
msg, done = c.feed("", ctx)
check(not done and c.args == [("edge", "Blk", 1), ("edge", "Blk", 2)]
      and not ctx.subobjects,
      f"Sweep2 consumes two distinct surface-edge rail prompts in order ({msg!r})")
c.args.append(["Sec"])
got = c._curves(ctx)
check(not isinstance(got, str)
      and bridge.curve_recipe(got[0][0]) == bridge.curve_recipe(edge1)
      and bridge.curve_recipe(got[0][1]) == bridge.curve_recipe(edge2),
      "Sweep2 resolves both selected solid edges to exact rail curves")

CLOSED = bridge.make_circle((11, 0, 0), 1.0, types.SimpleNamespace(xaxis=(1, 0, 0), yaxis=(0, 0, 1)))
g, m, msg, ctx, c, done = run("sweep2", [("RailA", RA), ("RailB", RB), ("Sec", CLOSED)],
                              ["RailA", "RailB", "Sec", ""])
check(done and not ctx.added and "coincide" in (msg or ""),
      f"Sweep2 with a closed section cannot span two rails: refused ({msg!r})")
for key, opt in (("sweep1", "ClosedSweep"), ("sweep2", "MaintainHeight")):
    g, m, msg, ctx, c, done = run(key, [("Rail", ARC)], [opt])
    check("not implemented" in (msg or "") and not ctx.added, f"{key} {opt} says so ({msg!r})")

print("\n-- selecting: the rail is never also taken as a cross-section --")
cx = Ctx([("Rail", ARC), ("Sec", CIRC)])
c = type(commands.COMMANDS["sweep1"])()
c.begin(cx)
cx.selection = ["Rail"]
c.feed("", cx)                      # confirm the rail by selection
after_rail = list(cx.selection)
cx.selection = ["Sec"]
msg, done = c.feed("", cx)          # confirm the section by selection
check(after_rail == [] and done and len(cx.added) == 1,
      f"the selection is cleared once the rail is taken, so the section set starts empty "
      f"({after_rail}; {msg!r})")

print("\n-- typed component rails AND sections, exact rational inputs --")
joined_rails = r3.PolyCurve()
joined_rails.Append(RA)
joined_rails.Append(r3.LineCurve(RA.PointAtEnd, RB.PointAtStart))
joined_rails.Append(RB)
joined_section = r3.PolyCurve()
joined_section.Append(SEMI)
joined_section.Append(bridge.make_line((12, 0, 0), (13, 0, 0)))
from narwhal3d import curve_inputs
for key, rail_indices in (("sweep1", [0]), ("sweep2", [0, 2])):
    cx = Ctx([("Rails", joined_rails), ("Sections", joined_section), ("Stale", BOX)])
    c = type(commands.COMMANDS[key])()
    c.begin(cx)
    for index in rail_indices:
        cx.selection = ["Stale", "Rails"]
        cx.subobjects = [("Rails", "Segment", index)]
        msg, done = c.feed("", cx)
        check(not done and c.args[-1] == ("segment", "Rails", index)
              and not cx.selection and not cx.subobjects,
              f"{key} rail {index} retains identity over stale parents and clears both selections")
    cx.selection = ["Sections", "Stale"]
    cx.subobjects = [("Sections", "Segment", 0)]
    check(c.step_index == len(rail_indices) and not cx.added,
          f"{key} section selection waits for Enter")
    msg, done = c.feed("", cx)
    result = cx.added[0] if cx.added else None
    exact = kernel.master_of(result) if result is not None else None
    check(done and result is not None and exact is not None and no_mesh_derived(cx)
          and c.args == [("segment", "Rails", i) for i in rail_indices]
                           + [[("segment", "Sections", 0)]],
          f"{key} commits a smooth exact master using component sections ({msg})")
    inputs = c._curves(cx)
    check(not isinstance(inputs, str)
          and all(g.ToNurbsCurve().IsRational for g in inputs[0] + [inputs[1]])
          and bridge.curve_recipe(inputs[1]) == bridge.curve_recipe(SEMI)
          and all(bridge.curve_recipe(g) == bridge.curve_recipe(source)
                  for g, source in zip(inputs[0], (RA, RB))),
          f"{key} receives the detached rational arcs, never parent curves or sampled wires")
    check(cx.names()[:3] == ["Rails", "Sections", "Stale"]
          and bridge.curve_recipe(cx.geom_by_name("Rails")) == bridge.curve_recipe(joined_rails),
          f"{key} never deletes or modifies any component owner")

# A section can also be an exact BRep edge, not merely a polycurve segment.
cx = Ctx([("Cyl", bridge.make_cylinder((10, 0, 0), 1, 2)), ("Rail", ARC)])
section_id = next(i for i, _ in bridge.subobject_parts(cx.geom_by_name("Cyl"))["edges"]
                  if curve_inputs.resolve(cx, ("edge", "Cyl", i)).IsClosed)
c = type(commands.COMMANDS["sweep1"])()
c.begin(cx)
c.feed("Rail", cx)
cx.subobjects = [("Cyl", "Edge", section_id)]
msg, done = c.feed("", cx)
check(c.args[-1] == [("edge", "Cyl", section_id)]
      and not isinstance(c._curves(cx), str)
      and bridge.curve_recipe(c._curves(cx)[1]) == bridge.curve_recipe(
          curve_inputs.resolve(cx, ("edge", "Cyl", section_id))),
      "BRep section edge retains its 1-based id and resolves exactly")

for at_section in (False, True):
    for kind, index in (("Face", 0), ("Vertex", 0), ("Segment", -1),
                        ("Segment", 50), ("Segment", 0.5), ("Edge", 0), ("Edge", 500)):
        cx = Ctx([("Rails", joined_rails), ("Sec", SEMI)])
        c = type(commands.COMMANDS["sweep1"])()
        c.begin(cx)
        if at_section:
            c.feed("Rails", cx)
        step = c.step_index
        cx.selection = ["Sec"]       # invalid components MUST NOT fall back to this curve
        cx.subobjects = [("Rails", kind, index)]
        msg, done = c.feed("", cx)
        check(not done and c.step_index == step and not cx.added and "Sweep1:" in msg,
              f"{'section' if at_section else 'rail'} refuses {kind}/{index} with no parent fallback")

cx = Ctx([("Rails", joined_rails), ("Sections", joined_section)])
c = type(commands.COMMANDS["sweep1"])()
c.begin(cx)
cx.subobjects = [("Rails", "Segment", 0)]
c.feed("", cx)
cx.subobjects = [("Sections", "Segment", 1), ("Sections", "Segment", 0)]
msg, done = c.feed("", cx)
check(done and not cx.added and "not implemented" in msg
      and c.args[-1] == [("segment", "Sections", 1), ("segment", "Sections", 0)],
      "multiple component sections retain pick order but remain explicitly unsupported")
cx = Ctx([("Rail", ARC), ("Sec", CIRC), ("Stale", SEMI)])
c = type(commands.COMMANDS["sweep1"])()
c.begin(cx)
c.feed("Rail", cx)
cx.selection = ["Stale"]
c.feed("Sec", cx)
msg, done = c.feed("", cx)
check(done and cx.added and c.args[-1] == ["Sec"],
      "explicit typed sections are not contaminated by stale whole-object selection")

# Identity is resolved afresh at execution: a deleted rail must not use a cached curve.
cx = Ctx([("Rails", joined_rails), ("Sec", SEMI)])
c = type(commands.COMMANDS["sweep1"])()
c.begin(cx)
cx.subobjects = [("Rails", "Segment", 0)]
c.feed("", cx)
cx.model.remove_object(cx.model.object_id_by_name("Rails"))
c.feed("Sec", cx)
msg, done = c.feed("", cx)
check(done and not cx.added and "owner no longer exists" in msg,
      "a removed component owner is refused when the sweep executes")

print("\n-- the sweeps reload from their recipes --")
s1, s1m, *_ = run("sweep1", [("Rail", NP), ("Sec", NPC)], ["Rail", "Sec", ""])
s2, s2m, *_ = run("sweep2", [("RailA", RA), ("RailB", RB), ("Sec", SEMI)],
                  ["RailA", "RailB", "Sec", ""])
s3, s3m, *_ = run("sweep1", [("Rail", ARC), ("Sec", CIRC)], ["Rail", "Sec", ""])
f3 = r3.File3dm()
for x in (s1, s2, s3):
    f3.Objects.Add(x, None)
path = os.path.join(TMP, "g98.3dm")
f3.Write(path, 8)
want = [(ob.face_types(m_), round(ob.mass_properties(ob.unique_faces(m_)[0], solid=False)["area"], 9))
        for m_ in (s1m, s2m, s3m)]
kernel.forget_masters()
back = [o.Geometry for o in r3.File3dm.Read(path).Objects]
restored, lost = bridge.rehydrate_document(back)
got = [(ob.face_types(kernel.master_of(x)),
        round(ob.mass_properties(ob.unique_faces(kernel.master_of(x))[0], solid=False)["area"], 9))
       if kernel.master_of(x) is not None else None for x in back]
check(restored == 3 and lost == 0 and got == want,
      f"both Sweep1s and the Sweep2 rebuild identically ({restored}/{lost}; {got})")

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    for f_ in failures:
        print("FAIL:", f_)
    sys.exit(1)
print("FILLETEDGE / CHAMFEREDGE / CAP + SWEEP1 / SWEEP2 COMMANDS PASSED")
