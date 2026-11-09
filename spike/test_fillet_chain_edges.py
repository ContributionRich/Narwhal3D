"""ChainEdges for FilletEdge/ChamferEdge: the OCCT chain walk and the command's selection.

Headless, no GUI, no Blender window:  scripts/run_suite.sh spike/test_fillet_chain_edges.py

Oracles are read straight from OCP (endpoints, midpoints, face types), never from the code
under test. Where the stated rule and the reported expectation differ, the test asserts what
the rule does and says so in its docstring.
"""
import math
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg

from narwhal3d import bridge, commands, kernel, model, solid_commands as solid
from narwhal3d.kernel import KernelError
from narwhal3d.kernel import occt_backend as ob
from narwhal3d.undo import UndoStack
from OCP.BRep import BRep_Builder
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp
from OCP.TopoDS import TopoDS, TopoDS_Compound
from OCP.collections import IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as _Map
from OCP.gp import gp_Pnt


def ends(shape):
    """Independent oracle: ``{edge index: (start xyz, end xyz)}`` read from OCP."""
    emap = _Map()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, emap)
    rows = {}
    for k in range(1, emap.Extent() + 1):
        a = BRepAdaptor_Curve(TopoDS.Edge(emap.FindKey(k)))
        p, q = a.Value(a.FirstParameter()), a.Value(a.LastParameter())
        rows[k] = ((p.X(), p.Y(), p.Z()), (q.X(), q.Y(), q.Z()))
    return rows


def near(a, b, tol=1e-7):
    return math.dist(a, b) <= tol


def top_edges(rows, z):
    return sorted(k for k, (p, q) in rows.items()
                  if abs(p[2] - z) < 1e-7 and abs(q[2] - z) < 1e-7)


def vertical_edges(rows):
    return sorted(k for k, (p, q) in rows.items()
                  if abs(p[0] - q[0]) < 1e-9 and abs(p[1] - q[1]) < 1e-9
                  and abs(p[2] - q[2]) > 1e-9)


def contiguous(order, rows):
    """True when consecutive edges in `order` meet at a shared end point (oracle)."""
    for a, b in zip(order, order[1:]):
        ends_a, ends_b = rows[a], rows[b]
        if not any(near(p, q, 1e-6) for p in ends_a for q in ends_b):
            return False
    return True


def filleted_box():
    """A 10 mm box whose four vertical edges carry radius-1 fillets: the top outline is a
    tangent loop of 4 lines and 4 arcs."""
    box = ob.make_box(10, 10, 10)
    return ob.bevel_edges(box, 1.0, indices=vertical_edges(ends(box)))


def compound(*edges):
    comp = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(comp)
    for e in edges:
        builder.Add(comp, e)
    return comp


def line_edge(a, b):
    return BRepBuilderAPI_MakeEdge(gp_Pnt(*a), gp_Pnt(*b)).Edge()


# ---------------------------------------------------------------------------
# OCCT chain walk
# ---------------------------------------------------------------------------

class ChainWalkTests(unittest.TestCase):
    def test_filleted_box_tangency_follows_the_whole_top_loop(self):
        shape = filleted_box()
        rows = ends(shape)
        top = top_edges(rows, 10.0)
        self.assertEqual(len(top), 8, "4 top lines + 4 top arcs")
        rep = {}
        chain = ob.edge_chain(shape, top[0], continuity="Tangency", report=rep)
        self.assertEqual(sorted(chain), top, "tangent continuation reaches every top edge")
        self.assertEqual(rep["branch_points"], [], "a tangent loop has no branch")
        self.assertTrue(contiguous(chain, rows), "the order is a walk, not a bag")

    def test_plain_box_tangency_is_only_the_picked_edge(self):
        box = ob.make_box(10, 10, 10)
        self.assertEqual(ob.edge_chain(box, 1, continuity="Tangency"), [1],
                         "a 90 degree corner is not tangent, so nothing joins")

    def test_plain_box_position_stops_at_a_corner_branch(self):
        """The reported expectation was a 4-edge top loop under Position. It is not produced: at each
        box corner three edges meet, so the two other top edges and the vertical edge all
        qualify as Position continuations. That is a branch, and the walk refuses to choose.
        The report names the two corners where it stopped."""
        box = ob.make_box(10, 10, 10)
        rows = ends(box)
        rep = {}
        self.assertEqual(ob.edge_chain(box, 1, continuity="Position", report=rep), [1])
        self.assertEqual(sorted(rep["branch_points"]), sorted(rows[1]),
                         "stopped at both ends of the picked edge")

    def test_angle_tolerance_decides_the_90_degree_corner(self):
        box = ob.make_box(10, 10, 10)
        rep = {}
        self.assertEqual(ob.edge_chain(box, 1, continuity="Tangency", angle_tolerance_deg=89,
                                       report=rep), [1])
        self.assertEqual(rep["branch_points"], [], "89 degrees: no continuation qualifies")
        rep = {}
        self.assertEqual(ob.edge_chain(box, 1, continuity="Tangency", angle_tolerance_deg=95,
                                       report=rep), [1])
        self.assertEqual(len(rep["branch_points"]), 2, "95 degrees: every corner is a branch")

    def test_direction_forward_backward_both(self):
        shape = filleted_box()
        rows = ends(shape)
        top = top_edges(rows, 10.0)
        start = top[0]
        fwd = ob.edge_chain(shape, start, direction="Forward")
        back = ob.edge_chain(shape, start, direction="Backward")
        both = ob.edge_chain(shape, start, direction="Both")
        self.assertEqual(fwd[0], start)
        self.assertEqual(back[-1], start)
        self.assertEqual(len(fwd), 8, "forward walks the whole ring from the start")
        self.assertEqual(len(back), 8, "backward walks the same ring the other way")
        self.assertEqual(sorted(both), top, "both = the ring, each edge once")
        self.assertTrue(contiguous(both, rows))
        self.assertTrue(contiguous(back, rows))

    def test_gap_tolerance_bridges_a_small_gap(self):
        a = line_edge((0, 0, 0), (10, 0, 0))
        b = line_edge((10.004, 0, 0), (20, 0, 0))
        shape = compound(a, b)
        self.assertEqual(ob.edge_chain(shape, 1, continuity="Tangency", gap_tolerance=0.0), [1],
                         "a 0.004 gap is not bridged at zero tolerance")
        self.assertEqual(sorted(ob.edge_chain(shape, 1, continuity="Tangency",
                                              gap_tolerance=0.005)), [1, 2],
                         "a 0.005 gap is bridged, and the collinear pair is tangent")

    def test_curvature_is_refused_by_name(self):
        with self.assertRaises(KernelError) as cm:
            ob.edge_chain(ob.make_box(10, 10, 10), 1, continuity="Curvature")
        self.assertIn("Curvature is not implemented yet", str(cm.exception))

    def test_seam_edges_never_chain(self):
        cyl = ob.make_cylinder(3, 5)
        rows = ends(cyl)
        seams = [k for k, (p, q) in rows.items() if abs(p[2] - q[2]) > 1e-6]
        self.assertEqual(len(seams), 1, "one seam line on the cylinder")
        with self.assertRaises(KernelError):
            ob.edge_chain(cyl, seams[0])
        circle = [k for k in rows if k not in seams][0]
        self.assertEqual(ob.edge_chain(cyl, circle, continuity="Position"), [circle],
                         "the circle's only other neighbour is the seam, which is excluded")

    def test_edge_midpoint_lies_on_the_arc_not_on_the_chord(self):
        shape = filleted_box()
        rows = ends(shape)
        axes = [(1.0, 1.0), (9.0, 1.0), (9.0, 9.0), (1.0, 9.0)]
        arcs = [k for k in top_edges(rows, 10.0)
                if not (abs(rows[k][0][0] - rows[k][1][0]) < 1e-9
                        or abs(rows[k][0][1] - rows[k][1][1]) < 1e-9)]
        self.assertEqual(len(arcs), 4)
        for k in arcs:
            mx, my, mz = ob.edge_midpoint(shape, k)
            self.assertAlmostEqual(mz, 10.0, places=9)
            self.assertTrue(any(abs(math.hypot(mx - cx, my - cy) - 1.0) < 1e-9 for cx, cy in axes),
                            "the midpoint is exactly on the radius-1 fillet arc")
            chord = [(p + q) / 2 for p, q in zip(*rows[k])]
            self.assertGreater(math.dist((mx, my, mz), chord), 0.05,
                               "a chord midpoint would be off the arc")


# ---------------------------------------------------------------------------
# The FilletEdge / ChamferEdge command
# ---------------------------------------------------------------------------

class Ctx:
    """Real model and undo, as in test_fillet_handle_input.py."""

    def __init__(self, geom):
        self.model = model.NarwhalModel()
        self.model.add_object(geom, "Box", 0)
        self.added = []
        self.undo = UndoStack()

    def names(self):
        return [obj.Attributes.Name for obj in self.model.file.Objects]

    def geom_by_name(self, name):
        return next((obj.Geometry for obj in self.model.file.Objects
                     if obj.Attributes.Name == name), None)

    def object_by_name(self, name):
        return None

    def selected_objects(self):
        return []

    def begin_undo(self, name):
        self.undo.begin(name)

    def end_undo(self):
        self.undo.end()

    def note_change(self, label=None):
        if not self.undo.captured:
            self.undo.capture(self.model.snapshot())

    def add_geometry(self, geom, base_name, layer_index=None):
        self.note_change(base_name)
        name = f"{base_name}_{len(self.added) + 1}"
        self.model.add_object(geom, name, 0)
        self.added.append(geom)
        return name, name


def document_box(vertical_filleted):
    box = bridge.make_box((0, 0, 0), (10, 10, 0), 10)
    if vertical_filleted:
        operand = bridge._occt_operand(box)
        box = bridge.bevel_edges_occt(box, 1.0, edge_indices=vertical_edges(ends(operand)))
    return box


def pick(cmd, ctx, operand, edge_id, action="plain"):
    midpoint = ob.edge_midpoint(operand, edge_id)
    return cmd.pick_edge("Box", edge_id, midpoint, action, ctx)


class ChainCommandTests(unittest.TestCase):
    def test_chainedges_opens_a_subprompt_with_honest_defaults(self):
        ctx = Ctx(document_box(False))
        cmd = solid.FilletEdgeCmd()
        cmd.begin(ctx)
        self.assertNotIn("AutoChain", cmd.prompt)
        msg, done = cmd.feed("ChainEdges", ctx)
        self.assertFalse(done)
        self.assertIn("AutoChain=Yes", msg)
        self.assertIn("ChainContinuity=Tangency", msg)
        self.assertIn("Direction=Both", msg)
        for name in ("AutoChain", "ChainContinuity", "Direction", "AngleTolerance",
                     "GapTolerance"):
            self.assertIn(name, cmd.prompt)
        self.assertIs(cmd.opt["AutoChain"], True)
        self.assertEqual(cmd.opt["ChainContinuity"], "Tangency")
        self.assertEqual(cmd.opt["Direction"], "Both")
        self.assertAlmostEqual(cmd.opt["AngleTolerance"],
                               math.degrees(ctx.model.file.Settings.ModelAngleToleranceRadians))
        self.assertEqual(cmd.opt["GapTolerance"], commands._document_tolerance(ctx))

    def test_curvature_is_refused_and_tangency_kept(self):
        ctx = Ctx(document_box(False))
        cmd = solid.FilletEdgeCmd()
        cmd.begin(ctx)
        cmd.feed("ChainEdges", ctx)
        msg, done = cmd.feed("ChainContinuity=Curvature", ctx)
        self.assertFalse(done)
        self.assertIn("not implemented yet", msg)
        self.assertEqual(cmd.opt["ChainContinuity"], "Tangency")

    def test_one_pick_adds_the_whole_tangent_loop(self):
        ctx = Ctx(document_box(True))
        operand = bridge._occt_operand(ctx.geom_by_name("Box"))
        cmd = solid.FilletEdgeCmd()
        cmd.begin(ctx)
        cmd.feed("NextRadius=0.5", ctx)
        cmd.feed("ChainEdges", ctx)
        top = top_edges(ends(operand), 10.0)
        msg, done = pick(cmd, ctx, operand, top[0])
        self.assertFalse(done)
        self.assertTrue(msg.startswith("8 edge(s) selected by chain (this pick added 8)"), msg)
        self.assertEqual(sorted(cmd._edge_points), top)
        self.assertEqual(set(cmd._edge_radii.values()), {(0.5, 0.5)}, "NextRadius per edge")
        for k in top:
            self.assertEqual(cmd._edge_points[k], ob.edge_midpoint(operand, k))

    def test_plain_box_tangency_pick_is_one_edge(self):
        ctx = Ctx(document_box(False))
        operand = bridge._occt_operand(ctx.geom_by_name("Box"))
        cmd = solid.FilletEdgeCmd()
        cmd.begin(ctx)
        cmd.feed("ChainEdges", ctx)
        msg, _ = pick(cmd, ctx, operand, 1)
        self.assertEqual(sorted(cmd._edge_points), [1])
        self.assertTrue(msg.startswith("1 edge(s) selected by chain (this pick added 1)"), msg)

    def test_autochain_no_picks_single_edges(self):
        ctx = Ctx(document_box(True))
        operand = bridge._occt_operand(ctx.geom_by_name("Box"))
        cmd = solid.FilletEdgeCmd()
        cmd.begin(ctx)
        cmd.feed("ChainEdges", ctx)
        cmd.feed("AutoChain=No", ctx)
        msg, _ = pick(cmd, ctx, operand, top_edges(ends(operand), 10.0)[0])
        self.assertEqual(len(cmd._edge_points), 1)
        self.assertTrue(msg.startswith("1 edge(s) selected") and "by chain" not in msg, msg)

    def test_branch_is_reported_not_followed(self):
        ctx = Ctx(document_box(False))
        operand = bridge._occt_operand(ctx.geom_by_name("Box"))
        cmd = solid.FilletEdgeCmd()
        cmd.begin(ctx)
        cmd.feed("ChainEdges", ctx)
        cmd.feed("ChainContinuity=Position", ctx)
        msg, _ = pick(cmd, ctx, operand, 1)
        self.assertEqual(sorted(cmd._edge_points), [1])
        self.assertIn("stopped at 2 branch(es)", msg)

    def test_ctrl_click_still_removes_a_single_edge(self):
        ctx = Ctx(document_box(True))
        operand = bridge._occt_operand(ctx.geom_by_name("Box"))
        cmd = solid.FilletEdgeCmd()
        cmd.begin(ctx)
        cmd.feed("ChainEdges", ctx)
        top = top_edges(ends(operand), 10.0)
        pick(cmd, ctx, operand, top[0])
        pick(cmd, ctx, operand, top[0], action="ctrl")
        self.assertEqual(sorted(cmd._edge_points), sorted(set(top) - {top[0]}))

    def test_chain_fillet_is_exact_and_airtight(self):
        ctx = Ctx(document_box(True))
        operand = bridge._occt_operand(ctx.geom_by_name("Box"))
        cmd = solid.FilletEdgeCmd()
        cmd.begin(ctx)
        cmd.feed("NextRadius=0.5", ctx)
        cmd.feed("ChainEdges", ctx)
        pick(cmd, ctx, operand, top_edges(ends(operand), 10.0)[0])
        cmd.feed("", ctx)
        self.assertIsNotNone(cmd._candidate, cmd._candidate_error)
        msg, done = cmd.feed("", ctx)
        self.assertTrue(done, msg)
        self.assertTrue(msg.startswith("Filleted 8 edge(s) of Box at radius 0.5"), msg)
        # Commit adds the result as a new object and leaves the input in place.
        added = [n for n in ctx.names() if n != "Box"]
        self.assertEqual(len(added), 1, ctx.names())
        result = ctx.geom_by_name(added[0])
        # The exact solid behind the document object, rebuilt from its recipe if needed.
        master = bridge._occt_operand(result)
        self.assertIsNotNone(master, "the committed solid is analytic, not a mesh")
        self.assertTrue(ob.is_airtight(master))
        # MEASURED: 6 planes, 8 cylinders (4 vertical + 4 top-edge fillets), 4 torus corners.
        self.assertEqual(ob.face_types(master), {"PLANE": 6, "CYLINDER": 8, "TORUS": 4})


if __name__ == "__main__":
    unittest.main()
