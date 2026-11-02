"""the `SelDup` command -- select objects duplicated onto themselves.

=============================================================================
What this pins
=============================================================================

`SelDup` (MEASURED via the command reference SelDup` ->
`commands/selection_commands.htm#SelDup`, section "Select Objects > Duplicate
Objects"):

    "The SelDup command selects objects that are geometrically identical with
    another object, visible, and in the same location regardless of other
    object properties."

So this is a SELECTION command, not a duplication command: it finds objects
that have a geometrically identical twin at the same location -- the "a copy
left on top of the original" case. It is zero-step (runs on the naming Enter,
like `SelAll`/`SelCrv`), scans the WHOLE document, and selects EXACTLY the
objects that share a geometry fingerprint with at least one OTHER visible
object. A unique object is NOT selected, and hidden objects are out of scope
(the MEASURED "visible"). Layer and other properties do not matter.

OURS (flagged in the command's docstring): rhino3dm has no value equality
(MEASURED: `a == b` is always False), so identity is decided by
`commands._geometry_fingerprint` -- type + ordered control points (curves,
meshes) / face list + tight box (brep) / location (point).

This test pins:

  * `SelDup` is registered, zero-step, and records no undo step;
  * a pair of identical lines / circles / arcs / polylines / points / boxes /
    cylinders: ONE selected, the original (first) left unselected -- MEASURED,
    Research13 #137: two identical lines gave "Found 1 duplicate. 1 curve added
    to selection." (which member the reference CAD application keeps was not measured; OURS: the first);
  * the SAME SHAPE at a different location: NOT selected (the "same location"
    half of the MEASURED wording);
  * the SAME LOCATION with a different shape: NOT selected;
  * a different type at the same location: NOT selected;
  * three identical objects: the two later copies selected;
  * a hidden twin does not count: a lone visible object with only a hidden
    copy at its location is NOT selected; two visible objects with a hidden
    third copy still select the later visible one;
  * identical objects on DIFFERENT layers: still duplicates ("regardless of
    other object properties"), so the later one is selected;
  * a mixed document selects exactly the duplicated set, nothing else;
  * running it leaves no undo record.

Run: tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_sel_dup.py
"""
import importlib.util
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
pkg.__package__ = "narwhal3d"
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
undo = _load("narwhal3d.undo", "narwhal3d/undo.py")
commands = _load("narwhal3d.commands", "narwhal3d/commands.py")
r3 = bridge.r3

checks = 0
failures = []


def check(cond, label):
    global checks
    checks += 1
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        failures.append(label)


class Ctx:
    """A headless stand-in: a REAL NarwhalModel plus a mutable selection and a
    REAL UndoStack (so the no-undo-record check is exercised for real)."""

    def __init__(self):
        self.model = model.NarwhalModel()
        self.undo = undo.UndoStack()
        self._selected = []
        self._named = []

    def add(self, geom, base, layer_index=0):
        name = f"{base}{len(self._named) + 1:02d}"
        self.model.add_object(geom, name, layer_index)
        self._named.append(name)
        return name

    def hide(self, name):
        for fobj in self.model.file.Objects:
            if fobj.Attributes.Name == name:
                fobj.Attributes.Mode = r3.ObjectMode.Hidden
                return
        raise AssertionError(f"{name!r} not found to hide")

    def select_by_names(self, names):
        self._selected = [str(n) for n in names]
        return list(self._selected)

    def selected_objects(self):
        class _O:
            def __init__(self, n):
                self.name = n
        return [_O(n) for n in self._selected]

    def begin_undo(self, name):
        return self.undo.begin(name)

    def end_undo(self):
        return self.undo.end()


def run(ctx):
    """Drive the zero-step command: it runs on the naming Enter (via `begin`)."""
    c = commands.SelDupCmd()
    return c.begin(ctx)


# ---------------------------------------------------------------------------
# DECLARATION / FIDELITY
# ---------------------------------------------------------------------------
check("seldup" in commands.COMMANDS,
      "`seldup` is registered in the COMMANDS registry")
check(commands.SelDupCmd.steps == [],
      "SelDup is a zero-step command (runs on the naming Enter)")
check(commands.SelDupCmd.records_undo is False,
      "SelDup records no undo step (selection is not a document change)")

# ---------------------------------------------------------------------------
# FIXTURES: identical twins and near-misses
# ---------------------------------------------------------------------------
LINE = bridge.make_line((0, 0, 0), (10, 0, 0))
LINE_SHIFTED = bridge.make_line((100, 0, 0), (110, 0, 0))   # same shape, other place
LINE_LONGER = bridge.make_line((0, 0, 0), (20, 0, 0))       # same place, other shape
CIRCLE = bridge.make_circle((0, 0, 0), 5.0)
CIRCLE_OTHER = bridge.make_circle((100, 0, 0), 5.0)         # same shape, other place
ARC = bridge.make_arc((0, 0, 0), (5, 5, 0), (10, 0, 0))
POLY = bridge.make_polyline([(0, 0, 0), (1, 1, 0), (2, 0, 0)])
POINT = bridge.make_point((3, 4, 0))
POINT_OTHER = bridge.make_point((9, 9, 0))
BOX = bridge.make_box((0, 0, 0), (10, 10, 0), 10)
BOX_SHIFTED = bridge.make_box((100, 0, 0), (110, 10, 0), 10)  # same shape, other place
CYL = bridge.make_cylinder((0, 0, 0), 5.0, 10.0)

# Sanity on the fixtures themselves, so a wrong premise fails loudly.
check(isinstance(LINE, r3.Curve) and LINE.IsClosed is False,
      "fixture: the line is an open curve")
check(isinstance(CIRCLE, r3.Curve) and CIRCLE.IsClosed,
      "fixture: the circle is a closed curve")
check(isinstance(ARC, r3.Curve), "fixture: the arc is a curve")
check(isinstance(POLY, r3.Curve), "fixture: the polyline is a curve")
check(isinstance(POINT, r3.Point), "fixture: the point is a point object")
check(isinstance(BOX, r3.Brep) and len(BOX.Faces) == 6,
      "fixture: the box is a 6-face brep")
check(isinstance(CYL, r3.Brep) and len(CYL.Faces) == 3,
      "fixture: the cylinder is a 3-face brep (2 planar + 1 curved)")

# ---------------------------------------------------------------------------
# IDENTICAL TWINS: the later copy selected (MEASURED Research13 #137)
# ---------------------------------------------------------------------------
def twins(label, geom_a, geom_b):
    ctx = Ctx()
    a = ctx.add(geom_a, "A")
    b = ctx.add(geom_b, "B")
    msg, done = run(ctx)
    got = {o.name for o in ctx.selected_objects()}
    check(done, f"{label}: runs on the naming Enter")
    check(got == {b},
          f"{label}: only the copy selected, original kept (got {sorted(got)}, want {[b]})")
    check(msg == "Found 1 duplicate. 1 object added to selection.",
          f"{label}: the reference CAD application-style count message ({msg!r})")

twins("two identical lines", LINE, LINE)
twins("two identical circles", CIRCLE, CIRCLE)
twins("two identical arcs", ARC, ARC)
twins("two identical polylines", POLY, POLY)
twins("two identical points", POINT, POINT)
twins("two identical boxes", BOX, BOX)
twins("two identical cylinders", CYL, CYL)

# ---------------------------------------------------------------------------
# NEAR MISSES: not selected
# ---------------------------------------------------------------------------
def no_twins(label, objs, expected=None):
    ctx = Ctx()
    names = [ctx.add(geom, name) for name, geom in objs]
    msg, done = run(ctx)
    got = {o.name for o in ctx.selected_objects()}
    check(done, f"{label}: runs on the naming Enter")
    want = expected if expected is not None else set()
    check(got == want,
          f"{label}: selects {sorted(want)} (got {sorted(got)})")
    if not want:
        check(msg == "No duplicates found. Nothing added to the selection.",
              f"{label}: the reference CAD application's no-duplicate message, MEASURED Research13 #137 ({msg!r})")

no_twins("same shape at a different location",
         [("line", LINE), ("line2", LINE_SHIFTED)])
no_twins("same location with a different shape",
         [("line", LINE), ("longer", LINE_LONGER)])
no_twins("circle vs circle at another location",
         [("c", CIRCLE), ("c2", CIRCLE_OTHER)])
no_twins("box vs box at another location",
         [("box", BOX), ("box2", BOX_SHIFTED)])
no_twins("point vs point at another location",
         [("pt", POINT), ("pt2", POINT_OTHER)])
no_twins("a cylinder vs a box (different shapes)",
         [("cyl", CYL), ("box", BOX)])

# A curve and a brep that share the same bounding box are NOT twins: the
# circle spans -5..5 on both axes, and does the square surface.
SQ = bridge.make_polyline([(-5, -5, 0), (5, -5, 0), (5, 5, 0), (-5, 5, 0)],
                          closed=True)
SQ_SRF = bridge.make_planar_srf([SQ])[0]
check(isinstance(SQ_SRF, r3.Brep) and len(SQ_SRF.Faces) == 1,
      "fixture: the square surface is a single-face brep")
no_twins("curve vs surface at the same location",
         [("c", CIRCLE), ("srf", SQ_SRF)])

# ---------------------------------------------------------------------------
# THREE IDENTICAL: the two copies selected
# ---------------------------------------------------------------------------
ctx = Ctx()
a1 = ctx.add(LINE, "L1")
a2 = ctx.add(LINE, "L2")
a3 = ctx.add(LINE, "L3")
u = ctx.add(LINE_LONGER, "U")
msg, done = run(ctx)
got = {o.name for o in ctx.selected_objects()}
check(done, "triple: runs on the naming Enter")
check(got == {a2, a3},
      f"triple: the two later copies selected, original and unique not (got {sorted(got)})")

# ---------------------------------------------------------------------------
# HIDDEN OBJECTS ARE OUT OF SCOPE (MEASURED: "visible")
# ---------------------------------------------------------------------------
ctx = Ctx()
v = ctx.add(LINE, "V")
h = ctx.add(LINE, "H")
ctx.hide(h)
msg, done = run(ctx)
got = {o.name for o in ctx.selected_objects()}
check(done, "hidden twin: runs on the naming Enter")
check(got == set(),
      f"a lone visible object with only a HIDDEN copy at its location is not selected (got {sorted(got)})")

ctx = Ctx()
v1 = ctx.add(LINE, "V1")
v2 = ctx.add(LINE, "V2")
h = ctx.add(LINE, "H")
ctx.hide(h)
msg, done = run(ctx)
got = {o.name for o in ctx.selected_objects()}
check(done, "hidden third: runs on the naming Enter")
check(got == {v2},
      f"two visible twins: the later is selected; the hidden third copy is not (got {sorted(got)})")

# ---------------------------------------------------------------------------
# REGARDLESS OF OTHER OBJECT PROPERTIES: different layers still match
# ---------------------------------------------------------------------------
ctx = Ctx()
ctx.model.add_layer("B")
la = ctx.add(LINE, "A", layer_index=0)
lb = ctx.add(LINE, "B", layer_index=1)
msg, done = run(ctx)
got = {o.name for o in ctx.selected_objects()}
check(done, "layers: runs on the naming Enter")
check(got == {lb},
      f"identical objects on DIFFERENT layers are still duplicates (got {sorted(got)})")

# ---------------------------------------------------------------------------
# MIXED DOCUMENT: exactly the duplicated set, nothing else
# ---------------------------------------------------------------------------
ctx = Ctx()
l1 = ctx.add(LINE, "L1")
l2 = ctx.add(LINE, "L2")
l3 = ctx.add(LINE, "L3")
c1 = ctx.add(CIRCLE, "C1")
c2 = ctx.add(CIRCLE, "C2")
u1 = ctx.add(LINE_LONGER, "U1")
u2 = ctx.add(BOX, "U2")
u3 = ctx.add(POINT, "U3")
msg, done = run(ctx)
got = {o.name for o in ctx.selected_objects()}
check(done, "mixed: runs on the naming Enter")
check(got == {l2, l3, c2},
      (f"mixed document: exactly the copies selected "
       f"(got {sorted(got)}, want {sorted({l2, l3, c2})})"))
check(msg == "Found 3 duplicates. 3 objects added to selection.", f"mixed: count message ({msg!r})")

# ---------------------------------------------------------------------------
# NO UNDO RECORD
# ---------------------------------------------------------------------------
ctx = Ctx()
ctx.add(LINE, "A")
ctx.add(LINE, "B")
run(ctx)
check(ctx.undo.undo_names() == [],
      f"SelDup leaves no undo record (got {ctx.undo.undo_names()})")

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    for f_ in failures:
        print("FAIL:", f_)
    sys.exit(1)
print("(SelDup) PASSED")
