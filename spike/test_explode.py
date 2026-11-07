#!/usr/bin/env python3
"""the `Explode` command -- the inverse of `Join`.

Run:
  tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_explode.py

`ExplodeCmd` is selection-driven with no prompts (commands/explode.htm: "Select
objects."), exactly like `Delete`. The kernel half (`bridge.explode_curve`) is
already tested in spike/test_join.py; this drives the COMMAND around it,
headlessly, through a FakeCtx backed by a real NarwhalModel -- the same
load-by-file-path trick as spike/test_a2_draw.py, so no Blender is needed.

Covered (per the task):
  * a joined polyline explodes into its segment lines,
  * a polycurve into its segment curves,
  * a plain line reports it is already a single segment (not success),
  * a non-curve is REPORTED, not silently dropped (house rule 4),
  * and the mutation is undoable (note_change fires BEFORE the document changes,
    and only when something actually explodes).
"""
import importlib.util
import math
import os
import sys
import types

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def _load(name, relpath):
    path = os.path.join(ROOT, relpath)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")
model = _load("narwhal3d.model", "narwhal3d/model.py")
# options + commands ride along; commands.py imports bridge/options relatively.
_load("narwhal3d.options", "narwhal3d/options.py")
commands = _load("narwhal3d.commands", "narwhal3d/commands.py")

checks = 0
failures = []


def check(cond, label):
    global checks
    checks += 1
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}")
        failures.append(label)


P = r3.Point3d


class FakeObj:
    def __init__(self, name):
        self.name = name


class FakeCtx:
    """A NarwhalContext stand-in for a selection-driven command, headless.

    Backed by a REAL NarwhalModel so `geom_by_name`, `_layer_index_of`,
    `object_id_by_name` and `remove_object` all behave exactly as in the app.
    The only bpy-touching methods are overridden: `selected_objects` returns
    plain name holders, `object_by_name` returns None (so the command's Blender
    cleanup is skipped), and `add_geometry` records the piece and mirrors it into
    the model instead of building a mesh.
    """

    def __init__(self):
        self.model = model.NarwhalModel()
        self._selected = []          # display names, in selection order
        self.changes = []            # note_change labels, in call order
        self.added = []              # (geom, base_name, layer_index, display)

    def add_input(self, geom, name, layer_index=0):
        """Place a document object and select it."""
        self.model.add_object(geom, name, layer_index)
        self._selected.append(name)
        return name

    # -- the NarwhalContext surface ExplodeCmd.execute touches
    def selected_objects(self):
        return [FakeObj(n) for n in self._selected]

    def geom_by_name(self, name):
        for obj in self.model.file.Objects:
            if (obj.Attributes.Name or "").lower() == name.lower():
                return obj.Geometry
        return None

    def object_by_name(self, name):
        return None                  # no Blender object headlessly

    def note_change(self, label=None):
        self.changes.append(label)

    def add_geometry(self, geom, base_name, layer_index=0):
        display = f"{base_name}{len(self.added) + 1:02d}"
        self.model.add_object(geom, display, layer_index)
        self.added.append((geom, base_name, int(layer_index), display))
        return display, display


def fresh_cmd():
    # a fresh instance per run; Explode holds no per-run state, but this keeps
    # each case independent and matches how the command line hands one out.
    return type(commands.COMMANDS["explode"])()


def ends(curve):
    return ((curve.PointAtStart.X, curve.PointAtStart.Y, curve.PointAtStart.Z),
            (curve.PointAtEnd.X, curve.PointAtEnd.Y, curve.PointAtEnd.Z))


def close(a, b, tol=1e-6):
    return math.dist(a, b) < tol


# ===========================================================================
print()
print("the command is registered and selection-driven, like Delete")
# ===========================================================================
check("explode" in commands.COMMANDS, "Explode is registered under 'explode'")
cmd = commands.COMMANDS["explode"]
check(cmd.name == "Explode", f"its name is Explode  ({cmd.name})")
# regression: Explode now ASKS for its selection (a single objects_multi set-step),
# instead of refusing when nothing is picked -- the empty-selection regression. Same
# shape as Delete/Hide/Lock (regression). `execute` still reads the live selection
# (a single set-step is never cleared before execute), which is why the
# execute-driven cases below are unchanged.
check([k for _p, k in cmd.steps] == [commands.OBJECTS_MULTI],
      "it asks for the selection (one objects_multi step)")
check(cmd.preselect_names == 1 and cmd.cancel_on_empty_set is True,
      "it consumes a pre-selection and cancels on an empty confirm")
check(cmd.options == (), "it declares no options (none are implementable yet)")

# ===========================================================================
print()
print("a joined polyline explodes into its segment lines")
# ===========================================================================
ctx = FakeCtx()
# a degree-1 polyline through 3 points -> 2 segments (what Join produces)
poly = bridge.make_polyline([(0, 0, 0), (5, 0, 0), (5, 5, 0)], degree=1)
ctx.add_input(poly, "Polyline01")
msg, done = fresh_cmd().execute(ctx)
check(done is True, "the command completes")
check(len(ctx.added) == 2,
      f"the polyline becomes two segments  ({len(ctx.added)})")
check(all(isinstance(g, r3.LineCurve) for g, *_ in ctx.added),
      "each segment is a line")
# the two segments span the original polyline's two legs
seg_ends = sorted(ends(g) for g, *_ in ctx.added)
want = sorted([((0, 0, 0), (5, 0, 0)), ((5, 0, 0), (5, 5, 0))])
check(all(close(a[0], b[0]) and close(a[1], b[1]) for a, b in zip(seg_ends, want)),
      f"the segments are the polyline's two legs  ({seg_ends})")
check(ctx.changes == ["Explode"],
      f"note_change fired once, BEFORE mutating (undoable)  ({ctx.changes})")
# the original polyline is gone from the model, replaced by the two segments
check(ctx.model.object_id_by_name("Polyline01") is None,
      "the exploded polyline is removed from the document")
check("Exploded 1 object" in msg and "2 segments" in msg,
      f"the message reports the explosion  ({msg})")

# ===========================================================================
print()
print("a polycurve explodes into its segment curves")
# ===========================================================================
ctx = FakeCtx()
pc = r3.PolyCurve()
pc.Append(r3.LineCurve(P(0, 0, 0), P(4, 0, 0)))
pc.Append(r3.LineCurve(P(4, 0, 0), P(4, 3, 0)))
ctx.add_input(pc, "Curve01")
msg, done = fresh_cmd().execute(ctx)
check(len(ctx.added) == 2,
      f"the polycurve becomes its two segments  ({len(ctx.added)})")
check(ctx.changes == ["Explode"], "the polycurve explosion is undoable")
check("Exploded 1 object" in msg, f"the message reports it  ({msg})")

# ===========================================================================
print()
print("a plain line has nothing to explode -- and says so (not 'success')")
# ===========================================================================
ctx = FakeCtx()
ctx.add_input(r3.LineCurve(P(0, 0, 0), P(3, 0, 0)), "Line01")
msg, done = fresh_cmd().execute(ctx)
check(ctx.added == [], "a single line produces no segments")
check(ctx.changes == [],
      "nothing was mutated, so note_change did NOT fire (no empty undo record)")
check("single segment" in msg.lower() and "explod" in msg.lower(),
      f"it reports the line is already a single segment  ({msg})")
check(ctx.model.object_id_by_name("Line01") is not None,
      "the line is left untouched in the document")

# ===========================================================================
print()
print("a non-curve is REPORTED, not silently dropped (house rule 4)")
# ===========================================================================
ctx = FakeCtx()
ctx.add_input(bridge.make_point((1, 2, 3)), "Point01")
msg, done = fresh_cmd().execute(ctx)
check(ctx.added == [] and ctx.changes == [], "a point is not exploded")
check("only curves" in msg.lower() and "not implemented" in msg.lower(),
      f"it says curves-only, honestly  ({msg})")

# ===========================================================================
print()
print("nothing selected")
# ===========================================================================
ctx = FakeCtx()
msg, done = fresh_cmd().execute(ctx)
check(ctx.added == [] and "select" in msg.lower(),
      f"with nothing selected it asks for a selection  ({msg})")

# ===========================================================================
print()
print("a mixed selection: explode what can be, report the rest")
# ===========================================================================
ctx = FakeCtx()
ctx.add_input(bridge.make_polyline([(0, 0, 0), (2, 0, 0), (2, 2, 0)], degree=1),
              "Polyline01")
ctx.add_input(r3.LineCurve(P(10, 0, 0), P(13, 0, 0)), "Line01")   # already single
ctx.add_input(bridge.make_point((5, 5, 5)), "Point01")            # not a curve
msg, done = fresh_cmd().execute(ctx)
check(len(ctx.added) == 2,
      f"only the polyline explodes (into 2)  ({len(ctx.added)})")
check(ctx.changes == ["Explode"], "the mixed run is a single undo record")
# RE-EXPRESSED (regression): this used to match the phrase "not an explodable curve". Once
# polysurfaces became explodable that wording was no longer TRUE -- a point is not excluded for
# being "not a curve" but for being something Explode does not handle -- so the message now
# says "not explodable". The check still demands that BOTH objects are accounted for, which is
# what it was always for.
check("1 already a single segment" in msg and "1 not explodable" in msg,
      f"the message accounts for BOTH the line and the point  ({msg})")
check(ctx.model.object_id_by_name("Line01") is not None
      and ctx.model.object_id_by_name("Point01") is not None,
      "the un-exploded objects are left in the document")
check(ctx.model.object_id_by_name("Polyline01") is None,
      "only the exploded polyline is removed")

# ===========================================================================
print()
print("regression: a POLYSURFACE explodes into SURFACES")
# ===========================================================================
# MEASURED (commands/explode.htm): "Polysurface -- Surfaces." Earlier rounds recorded this as
# BLOCKED because rhino3dm cannot construct a trimmed face. That was only true of one route:
# a plain brep's faces already exist and can be COPIED with their trims, and a master-backed
# solid explodes from its analytic master, which never needs rhino3dm to build a face.
import math as _m  # noqa: E402


def _edge_radii(brep):
    out = []
    for e in range(len(brep.Edges)):
        c = brep.Edges[e]
        d = c.Domain
        for i in range(9):
            p = c.PointAt(d.T0 + (d.T1 - d.T0) * i / 8.0)
            out.append(_m.hypot(p.X, p.Y))
    return out


# -- 1. a PLAIN brep: the native route, no kernel involved
ctx = FakeCtx()
ctx.add_input(bridge.make_cylinder((0, 0, 0), 2.0, 5.0), "Cylinder01")
msg, done = fresh_cmd().execute(ctx)
check(len(ctx.added) == 3,
      f"a cylinder explodes into its 3 surfaces: two caps and the wall ({len(ctx.added)})")
check(all(base == "Surface" for _, base, _, _ in ctx.added),
      "and they are added as Surfaces, not as curves")
check(all(len(g.Faces) == 1 for g, _, _, _ in ctx.added),
      "each piece is ONE face")
caps = [g for g, _, _, _ in ctx.added
        if g.Faces[0].UnderlyingSurface().IsPlanar()]
check(len(caps) == 2,
      f"two of them are the planar caps ({len(caps)})")
# THE CHECK THAT MATTERS for the native route. A cap's underlying surface is a SQUARE plane
# (MEASURED, the trimmed-face kernel limitation); the disc exists only as a trim. A piece that lost its trim
# would have edges reaching 2*sqrt(2) = 2.83 -- a square lid on a round part.
check(all(abs(r - 2.0) < 1e-9 for c in caps for r in _edge_radii(c)),
      "and each cap KEEPS ITS TRIM: every edge point is at the radius 2.0, not at the "
      "2.83 a square plane would reach -- the trim barrier that blocked the old plan")
check("3 surfaces" in msg and "Cylinder01" not in
      [str(o.Attributes.Name) for o in ctx.model.file.Objects],
      f"the message says 3 surfaces and the cylinder is gone  ({msg})")
check(ctx.changes == ["Explode"], "one undo record")

# -- 2. a single surface is NOT a polysurface
ctx = FakeCtx()
single = bridge.make_cylinder((0, 0, 0), 2.0, 5.0).Faces[0].DuplicateFace(False)
ctx.add_input(single, "Surface01")
msg, done = fresh_cmd().execute(ctx)
check(ctx.added == [] and ctx.changes == [],
      "a single surface produces nothing and records no undo")
check("single segment or surface" in msg,
      f"and it says it is already a single surface  ({msg})")

# -- 3. a MASTER-BACKED solid: the analytic route
occt = bridge._occt()
if occt is None:
    print("  .. OCCT unavailable; master-route checks skipped")
else:
    union = bridge.boolean_brep(bridge.make_box((0, 0, 0), (4, 4, 0), 4.0),
                                bridge.make_cylinder((2, 2, 0), 1.5, 6.0), "UNION")
    k = bridge._kernel()
    union_master = k.master_of(union)
    want_faces = occt.face_types(union_master)
    want_area = sum(a for _, a in occt.face_summary_list(union_master))
    proxy_faces = len(union.Faces)
    ctx = FakeCtx()
    ctx.add_input(union, "Boolean01")
    msg, done = fresh_cmd().execute(ctx)
    pieces = [g for g, _, _, _ in ctx.added]
    check(len(pieces) == occt.face_count(union_master),
          f"a boolean explodes into its {occt.face_count(union_master)} ANALYTIC faces "
          f"({len(pieces)}) -- NOT into the {proxy_faces} triangles of its display proxy, "
          f"which is what duplicating the proxy's faces would have produced")
    got = {}
    for g in pieces:
        m = k.master_of(g)
        for t, n in (occt.face_types(m) if m is not None else {"NONE": 1}).items():
            got[t] = got.get(t, 0) + n
    check(got == want_faces,
          f"each piece carries its own analytic master, and together they are exactly the "
          f"solid's faces: {got} (wanted {want_faces})")
    got_area = sum(sum(a for _, a in occt.face_summary_list(k.master_of(g))) for g in pieces)
    check(abs(got_area - want_area) < 1e-6,
          f"with the same total area {got_area:.6f} (wanted {want_area:.6f}) -- nothing lost "
          f"or duplicated")
    check("analytic faces from the kernel" in msg,
          f"and the message SAYS they are the kernel's analytic faces  ({msg})")

    # -- 4. a piece REBUILDS from its recipe: it survives save/reload
    rec = k.recipe_of(pieces[0])
    check(rec is not None and rec.get("kind") == "face",
          f"a piece records a `face` recipe naming its parent ({(rec or {}).get('kind')})")
    rebuilt = occt.from_recipe(rec)
    check(occt.face_types(rebuilt) == occt.face_types(k.master_of(pieces[0])),
          "and that recipe rebuilds the SAME face from the parent's construction tree")
    check(max(abs(a - b) for a, b in zip(occt.surface_centroid(rebuilt),
                                         occt.surface_centroid(k.master_of(pieces[0]))))
          < 1e-9,
          "in the same PLACE -- a face of the same type somewhere else would also match on "
          "faces alone, which is why the centroid is checked")

    # -- 5. moving a piece moves its master AND its recipe (regression + regression)
    piece = pieces[0]
    before = occt.surface_centroid(k.master_of(piece))
    bridge.transform_geometry(piece, r3.Transform.Translation(50.0, 0.0, 0.0))
    after_master = occt.surface_centroid(k.master_of(piece))
    after_recipe = occt.surface_centroid(occt.from_recipe(k.recipe_of(piece)))
    check(abs(after_master[0] - before[0] - 50.0) < 1e-9,
          "moving an exploded piece moves its master")
    check(abs(after_recipe[0] - before[0] - 50.0) < 1e-9,
          f"AND its recipe, so it would reopen where it was moved to (x {after_recipe[0]:.3f})"
          f" -- a `face` recipe has no xform of its own, the case regression fixed")

    # -- 6. a single piece cannot be exploded again
    ctx = FakeCtx()
    ctx.add_input(pieces[1], "Surface02")
    msg, done = fresh_cmd().execute(ctx)
    check(ctx.added == [],
          f"an exploded piece is a single surface and does not explode further  ({msg})")

# Bug #113: old TracePicture output is one cubic NURBS with packed Bezier spans.
from narwhal3d import trace_picture as tp
controls = [(0, 0, 0), (1, 2, 0), (3, 2, 0), (4, 0, 0),
            (3, -2, 0), (1, -2, 0), (0, 0, 0)]
for source in (tp.nurbs_curve(r3, controls), tp.bezier_loop(r3, controls)):
    ctx = FakeCtx()
    ctx.add_input(source, "Trace01")
    msg, done = fresh_cmd().execute(ctx)
    check(done and len(ctx.added) == 2,
          f"Explode separates old and new traced loops into exact cubic pieces ({msg})")
    check(ctx.model.object_id_by_name("Trace01") is None and len(ctx.changes) == 1,
          "Explode replaces the trace in one document change")
    check(all(g.Degree == 3 and g.IsValid for g, *_ in ctx.added),
          "the traced pieces remain real cubic curves, never sampled lines")

print()
print("=" * 60)
if failures:
    print(f"{len(failures)} of {checks} CHECKS FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print(f"EXPLODE TESTS PASSED ({checks} checks)")
