"""ExtrudeCrv options and shared exact profile regions.

MEASURED (commands/extrudecrv.htm, extrudecrvtapered.htm; the reference application's macros
`!_ExtrudeCrv _Pause _Solid=_Yes`, prompt "Base point for direction"). Earlier
ExtrudeCrv had NO options (each answered "not an option") and refused an open curve;
ExtrudeCrvTapered did not exist. Every gate below fails on that code.

Run: tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_extrude_options.py
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
preview = _load("narwhal3d.preview", "narwhal3d/preview.py")
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


if not ob.available():
    print(f"  skip  extrusion tests need OCCT ({ob.unavailable_reason()})")
    sys.exit(0)


class Ctx:
    def __init__(self, named, selected=()):
        self.model = model.NarwhalModel()
        self.added = []
        self._selected = list(selected)
        for nm, g in named:
            self.model.add_object(g, nm, 0)

    def add_geometry(self, geom, base_name, layer_index=None):
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
        return [types.SimpleNamespace(name=name) for name in self._selected]

    def select_by_names(self, names):
        self._selected = list(names)

    def active_cplane(self):
        return None

    def note_change(self, label=None):
        pass


def run(key, named, lines, selected=()):
    ctx = Ctx(named, selected=selected)
    c = type(commands.COMMANDS[key])()
    msg, done = c.begin(ctx)
    last = msg
    for line in lines:
        if done:
            break
        msg, done = c.feed(line, ctx)
        last = msg or last
    g = ctx.added[0] if ctx.added else None
    return g, (kernel.master_of(g) if g is not None else None), last, ctx


def rel(a, b):
    return abs(a - b) / abs(b)


def area(shape):
    return ob.mass_properties(shape, solid=False)["area"]


CIRC = bridge.make_circle((0, 0, 0), 3.0)
SQ = r3.PolylineCurve([r3.Point3d(*p) for p in
                       [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0), (0, 0, 0)]])
C5 = bridge.make_circle((0, 0, 0), 5.0)

print("-- ExtrudeCrv --")
g, m, msg, _ = run("extrudecrv", [("Prof", CIRC)], ["Prof", "BothSides", "5"])
lo, hi = ob.tight_bbox(m) if m is not None else ((0, 0, 0), (0, 0, 0))
check(m is not None and ob.face_types(m) == {"CYLINDER": 1, "PLANE": 2}
      and abs(lo[2] + 5) < 1e-9 and abs(hi[2] - 5) < 1e-9
      and rel(ob.volume(m), 2 * math.pi * 9 * 5) < 1e-12,
      f"BothSides: z -5 .. 5, volume 2 pi r^2 h, {{CYLINDER: 1, PLANE: 2}} ({msg!r})")
g, m, msg, _ = run("extrudecrv", [("Prof", CIRC)], ["Prof", "Solid=No", "5"])
check(m is not None and ob.face_types(m) == {"CYLINDER": 1} and not ob.is_airtight(m)
      and rel(area(m), 2 * math.pi * 3 * 5) < 1e-12,
      f"Solid=No: the open wall only, area 2 pi r h ({m is not None and ob.face_types(m)})")
g, m, msg, _ = run("extrudecrv", [("Prof", bridge.make_line((0, 0, 0), (4, 0, 0)))],
                       ["Prof", "2"])
check(m is not None and ob.face_types(m) == {"PLANE": 1} and rel(area(m), 8.0) < 1e-12,
      f"an OPEN curve extrudes to a surface (along the CPlane Z): area 4 x 2 ({msg!r})")
g, m, msg, _ = run("extrudecrv", [("Prof", CIRC)],
                       ["Prof", "Direction", "0,0,0", "1,0,1", "4"])
want = math.pi * 9 * 4 / math.sqrt(2)
check(m is not None and ob.is_airtight(m) and rel(ob.volume(m), want) < 1e-9,
      f"Direction (0,0,0) -> (1,0,1), distance 4: an OBLIQUE prism, volume pi r^2 4 cos 45 "
      f"({m is not None and ob.volume(m)!r} vs {want!r}; {msg!r})")
lo, hi = ob.tight_bbox(m) if m is not None else ((0, 0, 0), (0, 0, 0))
check(m is not None and abs(hi[2] - 4 / math.sqrt(2)) < 1e-9 and abs(hi[0] - (3 + 4 / math.sqrt(2)))
      < 1e-9, f"...leaning along +X as picked ({hi})")
# Bug #63: "extruding required i typed a negative number to extrude up". MEASURED: a
# CLOCKWISE curve's plane normal is -Z, so +5 went down. A positive distance goes to the
# CPlane's Z side whichever way the curve was drawn -- on BOTH routes.
SQ_CW = r3.PolylineCurve([r3.Point3d(*p) for p in
                          [(0, 0, 0), (0, 10, 0), (10, 10, 0), (10, 0, 0), (0, 0, 0)]])
check(bridge.profile_normal(SQ_CW)[2] < 0 and bridge.profile_normal(SQ)[2] > 0,
      "(the input really is wound both ways: the clockwise square's own normal is -Z)")
for label, prof, extra in (("clockwise", SQ_CW, []), ("counter-clockwise", SQ, []),
                           ("clockwise, an option set", SQ_CW, ["BothSides=No", "Solid=No"])):
    g, m, msg, _ = run("extrudecrv", [("Prof", prof)], ["Prof"] + extra + ["5"])
    bb = g.GetBoundingBox() if g is not None else None
    check(bb is not None and abs(bb.Min.Z) < 1e-9 and abs(bb.Max.Z - 5) < 1e-9,
          f"bug #63: a {label} curve extruded by +5 goes UP, z 0..5 "
          f"({bb and (bb.Min.Z, bb.Max.Z)}; {msg!r})")
_c = type(commands.COMMANDS["extrudecrv"])()
_c.args = [["Prof"]]
_ax = _c.axis_constraint(None, resolve=lambda n: SQ_CW)
check(_ax is not None and _ax[1][2] > 0.99,
      f"...and a click is measured along +Z for it too (the axis {_ax and _ax[1]})")
# (no preview check: the preview follows the CURSOR whichever way the normal points --
# mutation-tested, it cannot see this bug; the typed distance is what went wrong)

g, m, msg, ctx = run("extrudecrv", [("Prof", CIRC)],
                         ["Prof", "DeleteInput=Yes", "5"])
check(ctx.geom_by_name("Prof") is None and g is not None,
      "DeleteInput=Yes removes the curve, and the extrusion is made")
g, m, msg, _ = run("extrudecrv", [("Prof", CIRC)], ["Prof", "5"])
check(g is not None and (kernel.recipe_of(g) or {}).get("kind") == "extrusion"
      and "both" not in kernel.recipe_of(g) and kernel.master_of(g) is None,
      "no options: the unchanged plain route (rhino3dm extrusion + its recipe)")
g, m, msg, _ = run("extrudecrv", [("Prof", CIRC)], ["Prof", "Output=SubD"])
check("not implemented" in (msg or ""), f"Output=SubD says so ({msg!r})")

print("\n-- selected closed coplanar profiles become exact regions --")
OUTER = bridge.make_circle((0, 0, 0), 10.0)
HOLE = bridge.make_circle((0, 0, 0), 4.0)
profiles = [("Outer", OUTER), ("Hole", HOLE)]
ring_volume = math.pi * (10.0 ** 2 - 4.0 ** 2) * 5.0

print("\n-- shared exact region backend --")
direct_results = commands.extrude_planar_profile_regions(
    [OUTER, HOLE], 5.0, (0.0, 0.0, 1.0), both=False, solid=True)
direct_ring = direct_results[0] if len(direct_results) == 1 else None
direct_master = kernel.master_of(direct_ring) if direct_ring is not None else None
direct_recipe = (kernel.recipe_of(direct_ring) or {}) if direct_ring is not None else {}
check(direct_master is not None
      and ob.face_types(direct_master) == {"CYLINDER": 2, "PLANE": 2}
      and ob.is_airtight(direct_master)
      and rel(ob.volume(direct_master), ring_volume) < 1e-12
      and direct_recipe.get("kind") == "prism"
      and len((direct_recipe.get("a") or {}).get("loops") or []) == 2,
      "direct shared helper: one exact airtight 4-face annular prism with two-loop recipe")

# A selected group can contain a second numerical copy of every boundary. Keeping both
# copies creates duplicate hole wires and invalid topology; equivalent loops collapse while
# genuinely offset/overlapping profiles remain separate.
near_outer = bridge.transform_geometry(
    OUTER.Duplicate(), r3.Transform.Translation(1e-8, -1e-8, 0.0))
near_hole = bridge.transform_geometry(
    HOLE.Duplicate(), r3.Transform.Translation(1e-8, -1e-8, 0.0))
deduplicated = commands.extrude_planar_profile_regions(
    [OUTER, HOLE, near_outer, near_hole], 5.0, (0.0, 0.0, 1.0),
    both=False, solid=True)
deduplicated_master = kernel.master_of(deduplicated[0]) if len(deduplicated) == 1 else None
check(deduplicated_master is not None and ob.is_airtight(deduplicated_master)
      and ob.face_types(deduplicated_master) == {"CYLINDER": 2, "PLANE": 2}
      and rel(ob.volume(deduplicated_master), ring_volume) < 1e-12,
      "near-coincident duplicate profile loops collapse to one exact airtight annular prism")
try:
    commands.extrude_planar_profile_regions(
        [OUTER], 5.0, (0.0, 0.0, 1.0), both=False, solid=True)
except ValueError as exc:
    helper_refusal = str(exc)
else:
    helper_refusal = ""
check("at least two profile curves" in helper_refusal,
      f"the shared helper explicitly raises ValueError outside its 2+ profile contract "
      f"({helper_refusal!r})")

selection_ctx = Ctx(profiles, selected=("Outer", "Hole"))
selection_cmd = commands.ExtrudeCmd()
selection_msg, selection_done = selection_cmd.begin(selection_ctx)
check(selection_cmd.steps[0][1] == commands.OBJECTS_MULTI
      and selection_cmd.args == [["Outer", "Hole"]]
      and selection_cmd.prompt.startswith("Distance") and not selection_done,
      "ExtrudeCrv consumes the WHOLE selected profile set, then asks for distance")

region_calls = []
_original_region_builder = commands.extrude_planar_profile_regions


def _record_region_builder(profile_geometries, distance, direction, both=False, solid=True):
    region_calls.append((list(profile_geometries), float(distance), tuple(direction),
                         bool(both), bool(solid)))
    return _original_region_builder(
        profile_geometries, distance, direction, both=both, solid=solid)


setattr(commands, "extrude_planar_profile_regions", _record_region_builder)
try:
    ring, ring_m, ring_msg, ring_ctx = run(
        "extrudecrv", profiles, ["5"], selected=("Outer", "Hole"))
finally:
    setattr(commands, "extrude_planar_profile_regions", _original_region_builder)
check(len(region_calls) == 1 and len(region_calls[0][0]) == 2
      and region_calls[0][1] == 5.0 and region_calls[0][2][2] > 0.99
      and region_calls[0][3:] == (False, True),
      f"ExtrudeCmd routes the multi-profile request through the shared helper once "
      f"({region_calls})")
ring_recipe = kernel.recipe_of(ring) or {}
ring_base = ring_recipe.get("a") or {}
check(len(ring_ctx.added) == 1 and ring_m is not None
      and ob.face_types(ring_m) == {"CYLINDER": 2, "PLANE": 2}
      and ob.is_airtight(ring_m) and rel(ob.volume(ring_m), ring_volume) < 1e-12,
      f"R10/r4/h5: one airtight exact master, 4 faces "
      f"{{CYLINDER:2, PLANE:2}}, volume pi(100-16)5 ({ring_msg!r})")
check(ring_recipe.get("kind") == "prism" and ring_base.get("kind") == "planar_face"
      and len(ring_base.get("loops") or []) == 2,
      f"the recipe is prism -> planar_face with two loops ({ring_recipe})")

# Save/reload is a NEW process boundary for process-local OCCT masters. The nested recipe
# must rebuild the exact annular prism, not leave the triangulated display proxy as geometry.
roundtrip_dir = tempfile.mkdtemp(prefix="round55_extrude_")
roundtrip_path = os.path.join(roundtrip_dir, "annulus.3dm")
roundtrip_file = r3.File3dm()
roundtrip_file.Objects.Add(ring, None)
check(roundtrip_file.Write(roundtrip_path, 8), "the multi-profile solid saves to .3dm")
kernel.forget_masters()
roundtrip_geom = r3.File3dm.Read(roundtrip_path).Objects[0].Geometry
restored, lost = bridge.rehydrate_document([roundtrip_geom])
roundtrip_master = kernel.master_of(roundtrip_geom)
roundtrip_recipe = kernel.recipe_of(roundtrip_geom) or {}
check(restored == 1 and lost == 0 and roundtrip_master is not None
      and ob.face_types(roundtrip_master) == {"CYLINDER": 2, "PLANE": 2}
      and ob.is_airtight(roundtrip_master)
      and rel(ob.volume(roundtrip_master), ring_volume) < 1e-12
      and roundtrip_recipe.get("kind") == "prism"
      and len((roundtrip_recipe.get("a") or {}).get("loops") or []) == 2,
      f"save/reload rebuilds the exact two-loop annular prism ({restored}/{lost})")

left = bridge.make_circle((-20, 0, 0), 3.0)
right = bridge.make_circle((20, 0, 0), 3.0)
_first, _first_m, disjoint_msg, disjoint_ctx = run(
    "extrudecrv", [("Left", left), ("Right", right)], ["5"],
    selected=("Left", "Right"))
disjoint_masters = [kernel.master_of(g_) for g_ in disjoint_ctx.added]
check(len(disjoint_masters) == 2
      and all(m_ is not None and ob.is_airtight(m_)
              and ob.face_types(m_) == {"CYLINDER": 1, "PLANE": 2}
              and rel(ob.volume(m_), math.pi * 9.0 * 5.0) < 1e-12
              for m_ in disjoint_masters),
      f"two disjoint profiles create two separate exact solids ({disjoint_msg!r})")

_deleted, _deleted_m, delete_msg, delete_ctx = run(
    "extrudecrv", [("Outer", OUTER.Duplicate()), ("Hole", HOLE.Duplicate())],
    ["DeleteInput=Yes", "5"], selected=("Outer", "Hole"))
check(delete_ctx.geom_by_name("Outer") is None and delete_ctx.geom_by_name("Hole") is None
      and len(delete_ctx.added) == 1,
      f"DeleteInput=Yes deletes EVERY selected profile after building ({delete_msg!r})")

mixed, mixed_m, mixed_msg, mixed_ctx = run(
    "extrudecrv", [("Outer", OUTER.Duplicate()),
                   ("Point", bridge.make_point((0, 0, 0)))], ["5"],
    selected=("Outer", "Point"))
check(mixed is None and mixed_m is None and not mixed_ctx.added
      and "only closed coplanar curves" in (mixed_msg or ""),
      f"a mixed curve/non-curve set is explicitly refused ({mixed_msg!r})")

_open, _open_m, open_msg, open_ctx = run(
    "extrudecrv", [("Outer", OUTER.Duplicate()),
                   ("Open", bridge.make_line((20, 0, 0), (25, 0, 0)))], ["5"],
    selected=("Outer", "Open"))
check(not open_ctx.added and "CLOSED curves" in (open_msg or ""),
      f"a mixed closed/open profile set is explicitly refused ({open_msg!r})")

noncoplanar, noncoplanar_m, noncoplanar_msg, noncoplanar_ctx = run(
    "extrudecrv", [("Outer", OUTER.Duplicate()),
                   ("Raised", bridge.make_circle((0, 0, 2), 4.0))], ["5"],
    selected=("Outer", "Raised"))
check(noncoplanar is None and noncoplanar_m is None and not noncoplanar_ctx.added
      and "not all in the same plane" in (noncoplanar_msg or ""),
      f"noncoplanar profiles are explicitly refused ({noncoplanar_msg!r})")

class _PreviewPlane:
    zaxis = (0.0, 0.0, 1.0)
    normal = (0.0, 0.0, 1.0)


preview_doc = {"Outer": OUTER, "Hole": HOLE}
preview_cmd = commands.ExtrudeCmd()
preview_cmd.args = [["Outer", "Hole"]]
preview_polys = preview.segments(
    "extrudecrv", preview_cmd.args, (10.0, 0.0, 5.0), _PreviewPlane(),
    resolve=preview_doc.get, cmd=preview_cmd)
base_loops = [poly for poly in preview_polys if len(poly) > 8
              and all(abs(p[2]) < 1e-9 for p in poly)]
top_loops = [poly for poly in preview_polys if len(poly) > 8
             and all(abs(p[2] - 5.0) < 1e-9 for p in poly)]
base_radii = sorted(round(max(math.hypot(p[0], p[1]) for p in poly), 6)
                    for poly in base_loops)
top_radii = sorted(round(max(math.hypot(p[0], p[1]) for p in poly), 6)
                   for poly in top_loops)
check(base_radii == [4.0, 10.0] and top_radii == [4.0, 10.0],
      f"multi-profile preview includes BOTH loops at the base and top "
      f"({base_radii}; {top_radii})")

print("\n-- ExtrudeCrvTapered --")
H, A = 4.0, 10.0
t = math.tan(math.radians(A))
r_top = 5 - H * t
V_CONE = math.pi * H * (25 + 5 * r_top + r_top ** 2) / 3
g, m, msg, _ = run("extrudecrvtapered", [("Prof", C5)],
                       ["Prof", f"DraftAngle={A}", f"{H}"])
check(m is not None and ob.face_types(m) == {"CONE": 1, "PLANE": 2} and ob.is_airtight(m)
      and rel(ob.volume(m), V_CONE) < 1e-12,
      f"a circle tapered 10 deg over 4: a CONE frustum, top r = 5 - 4 tan 10, volume exact "
      f"({m is not None and ob.volume(m)!r} vs {V_CONE!r}; {msg!r})")
a_top = 10 - 2 * H * t
V_PYR = H * (100 + a_top ** 2 + 10 * a_top) / 3
g, m, msg, _ = run("extrudecrvtapered", [("Prof", SQ)],
                       ["Prof", f"DraftAngle={A}", f"{H}"])
check(m is not None and ob.face_types(m) == {"PLANE": 6} and rel(ob.volume(m), V_PYR) < 1e-12,
      f"a square: a pyramid frustum (Corners=Sharp), volume exact "
      f"({m is not None and ob.volume(m)!r} vs {V_PYR!r})")
SQR = SQ.Duplicate()
SQR.Reverse()
g, m, msg, _ = run("extrudecrvtapered", [("Prof", SQR)],
                       ["Prof", f"DraftAngle={A}", f"{H}"])
check(m is not None and rel(ob.volume(m), V_PYR) < 1e-12,
      "the same square drawn the OTHER way round still tapers inward")
a_out = 10 + 2 * H * t
V_OUT = H * (100 + a_out ** 2 + 10 * a_out) / 3
g, m, msg, _ = run("extrudecrvtapered", [("Prof", SQ)],
                       ["Prof", "FlipAngle=Yes", f"DraftAngle={A}", f"{H}"])
check(m is not None and rel(ob.volume(m), V_OUT) < 1e-12,
      f"FlipAngle tapers OUTWARD: top side 10 + 2 h tan a ({m is not None and ob.volume(m)!r} vs "
      f"{V_OUT!r})")
g, m, msg, _ = run("extrudecrvtapered", [("Prof", SQ)],
                       ["Prof", "Corners=Round", f"DraftAngle={A}",
                        "FlipAngle=Yes", f"{H}"])
ft = ob.face_types(m) if m is not None else {}
check(m is not None and ob.is_airtight(m) and ft.get("PLANE") == 6
      and ft.get("REVOLUTION") == 4 and V_PYR < ob.volume(m) < V_OUT,
      f"Corners=Round (outward): 4 exact surfaces of REVOLUTION fill the corners, airtight, "
      f"smaller than the sharp one "
      f"({ft}, {m is not None and ob.volume(m)!r})")
# the DEFINING property of a round corner: at height z, a corner face's points are z tan(a)
# from the ORIGINAL square's corner (a cone about that corner's vertical line)
if m is not None:
    from OCP.BRepAdaptor import BRepAdaptor_Surface  # noqa: E402
    from OCP.GeomAbs import GeomAbs_SurfaceOfRevolution  # noqa: E402
    worst = 0.0
    for f in ob.unique_faces(m):
        ad = BRepAdaptor_Surface(f)
        if ad.GetType() != GeomAbs_SurfaceOfRevolution:
            continue
        for i in range(9):
            for j in range(9):
                p = ad.Value(ad.FirstUParameter() + (ad.LastUParameter() - ad.FirstUParameter()) * i / 8,
                             ad.FirstVParameter() + (ad.LastVParameter() - ad.FirstVParameter()) * j / 8)
                cx = 0.0 if p.X() < 5 else 10.0
                cy = 0.0 if p.Y() < 5 else 10.0
                worst = max(worst, abs(math.hypot(p.X() - cx, p.Y() - cy) - p.Z() * t))
    check(worst < 1e-9, f"...each round corner is the cone r = z tan(a) about its corner "
                        f"(max {worst:.1e})")
g, m, msg, _ = run("extrudecrvtapered", [("Prof", SQ)],
                       ["Prof", "Corners=Smooth"])
check("not implemented" in (msg or ""), f"Corners=Smooth says so ({msg!r})")
g, m, msg, _ = run("extrudecrvtapered", [("Prof", C5)], ["Prof", f"{H}"])
check(m is not None and rel(ob.volume(m), math.pi * H * (25 + 5 * (5 - H * math.tan(
    math.radians(5))) + (5 - H * math.tan(math.radians(5))) ** 2) / 3) < 1e-12,
      "the default DraftAngle is 5 deg (OURS, flagged)")

print("\n-- they boolean, and reload from their recipes --")
_g_cone, cone_m, _msg, _c = run(
    "extrudecrvtapered", [("Prof", C5)], ["Prof", f"DraftAngle={A}", f"{H}"])
drilled = bridge.boolean_occt(_g_cone, bridge.make_cylinder((0, 0, -1), 1.0, 6.0), "DIFFERENCE")
dm = kernel.master_of(drilled)
check(dm is not None and rel(ob.volume(dm), V_CONE - math.pi * H) < 1e-12
      and bridge.last_occt_decline() is None,
      "the tapered frustum minus an r1 cylinder: exact")
both_g, both_m, _m2, _c2 = run(
    "extrudecrv", [("Prof", CIRC)], ["Prof", "BothSides", "5"])
TMP = tempfile.mkdtemp(prefix="g93_")
f3 = r3.File3dm()
for x in (_g_cone, both_g):
    f3.Objects.Add(x, None)
path = os.path.join(TMP, "g93.3dm")
f3.Write(path, 8)
want = [ob.volume(kernel.master_of(x)) for x in (_g_cone, both_g)]
kernel.forget_masters()
back = [o.Geometry for o in r3.File3dm.Read(path).Objects]
restored, lost = bridge.rehydrate_document(back)
got = [ob.volume(kernel.master_of(x)) if kernel.master_of(x) is not None else None for x in back]
check(restored == 2 and lost == 0 and all(g_ is not None and abs(g_ - w) < 1e-9
                                         for g_, w in zip(got, want)),
      f"tapered and BothSides extrusions rebuild exactly ({restored}/{lost}; {got})")

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    for f_ in failures:
        print("FAIL:", f_)
    sys.exit(1)
print("EXTRUDECRV OPTIONS + EXTRUDECRVTAPERED PASSED")
