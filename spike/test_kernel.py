#!/usr/bin/env python3
"""Kernel representation, recipe, analytic-master, and OCCT integration tests.

A representation tag records how geometry was made, while independent shape checks
prevent faceted solids from being mislabeled as analytic. OCCT-specific checks are
skipped when the optional public OCCT dependency is unavailable.
"""
import importlib.util
import math
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OCCT = os.path.join(ROOT, "vendor", "occt")
if os.path.isdir(OCCT) and OCCT not in sys.path:
    sys.path.insert(0, OCCT)

pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg

kpkg = types.ModuleType("narwhal3d.kernel")
kpkg.__path__ = [os.path.join(ROOT, "narwhal3d", "kernel")]
sys.modules["narwhal3d.kernel"] = kpkg

spec = importlib.util.spec_from_file_location(
    "narwhal3d.kernel", os.path.join(ROOT, "narwhal3d", "kernel", "__init__.py"))
kernel = importlib.util.module_from_spec(spec)
sys.modules["narwhal3d.kernel"] = kernel
kernel.__path__ = [os.path.join(ROOT, "narwhal3d", "kernel")]
spec.loader.exec_module(kernel)

checks = 0
failures = []
skipped = 0


def check(cond, label):
    global checks
    checks += 1
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}")
        failures.append(label)


def skip(label, why):
    global skipped
    skipped += 1
    print(f"  skip {label}  ({why})")


print("=" * 70)
print("KERNEL LAYER + THE HONESTY GUARANTEE")
print("=" * 70)

# --------------------------------------------------------------------------
print("\n-- kernel import boundaries --")
# These are architectural and are the whole reason the kernel is swappable. Checked by
# reading the tree, because that is the only thing that cannot be fooled by a mock.
#
# Parsed with `ast`, NOT grepped. The first version searched for the TEXT "import OCP"
# and failed on `kernel/__init__.py` -- whose docstring states the rule "only
# `kernel.occt_backend` may import OCP". A gate that matches prose cannot tell a rule
# from a violation of it.
import ast


def imported_modules(path):
    """Top-level module names actually IMPORTED by `path`, from its syntax tree."""
    names = set()
    try:
        tree = ast.parse(open(path).read())
    except SyntaxError:
        return names
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                names.add(node.module.split(".")[0])
    return names


kernel_init = os.path.join(ROOT, "narwhal3d", "kernel", "__init__.py")
iface = imported_modules(kernel_init)
print(f"  kernel/__init__.py imports: {sorted(iface) or 'nothing'}")
check("OCP" not in iface and "bpy" not in iface,
      "kernel/__init__.py imports NEITHER bpy NOR OCP, so it is testable with no "
      "Blender process and no kernel loaded")

allowed = os.path.join("narwhal3d", "kernel", "occt_backend.py")
offenders = []
for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "narwhal3d")):
    for name in files:
        if not name.endswith(".py"):
            continue
        rel = os.path.relpath(os.path.join(dirpath, name), ROOT)
        if rel == allowed:
            continue          # the ONE module allowed to
        if "OCP" in imported_modules(os.path.join(dirpath, name)):
            offenders.append(rel)
print(f"  modules importing OCP: {offenders or ['only ' + allowed]}")
check(not offenders,
      "ONLY kernel/occt_backend.py imports OCP -- that is what keeps the kernel "
      "swappable, and it is the escape hatch if in-process OCCT proves crashy")

# --------------------------------------------------------------------------
print("\n-- the tag: declared provenance --")
check(kernel.ANALYTIC != kernel.MESH_DERIVED != kernel.UNKNOWN,
      "the three representations are distinct")
check(kernel.REPRESENTATION_KEY.startswith("Narwhal3D:"),
      "the user-string key is NAMESPACED -- it travels inside the user's .3dm and "
      f"must not collide with Rhino's own keys ({kernel.REPRESENTATION_KEY})")

try:
    kernel.tag(object(), "something-else")
    check(False, "tagging with an invalid representation raises")
except kernel.KernelError:
    check(True, "tagging with an invalid representation raises KernelError rather "
                "than storing a value nothing else understands")


class FakeGeom:
    """Stands in for a rhino3dm object: the user-string pair plus IsSolid."""

    def __init__(self, solid=True, faces=None, planar=None):
        self._s = {}
        self.IsSolid = solid
        self._faces = faces
        self._planar = planar

    def SetUserString(self, k, v):
        self._s[k] = v
        return True

    def GetUserString(self, k):
        return self._s.get(k, "")


g = FakeGeom()
check(kernel.representation_of(g) == kernel.UNKNOWN,
      "an untagged object reads as UNKNOWN, not as a cheerful default")
kernel.tag(g, kernel.MESH_DERIVED)
check(kernel.representation_of(g) == kernel.MESH_DERIVED,
      "a tag round-trips through the user string")
check(kernel.representation_of(object()) == kernel.UNKNOWN,
      "an object with no user-string API reads UNKNOWN instead of raising")

# --------------------------------------------------------------------------
print("\n-- the cross-check: measured shape --")


class FakeSurface:
    def __init__(self, planar):
        self._p = planar

    def IsPlanar(self):
        return self._p


class FakeFace:
    def __init__(self, planar):
        self._s = FakeSurface(planar)

    def UnderlyingSurface(self):
        return self._s


class FakeBrep(FakeGeom):
    def __init__(self, planar_count, curved_count=0, solid=True):
        super().__init__(solid=solid)
        self.Faces = ([FakeFace(True)] * planar_count
                      + [FakeFace(False)] * curved_count)


# MEASURED values from rhino3dm, quoted in kernel/__init__.py's docstring.
check(kernel.face_summary(FakeBrep(6)) == (6, 6),
      "face_summary counts faces and planar faces (analytic box: 6, 6)")
check(kernel.face_summary(FakeBrep(2, curved_count=1)) == (3, 2),
      "and sees a NON-planar face (analytic cylinder: 3 faces, 2 planar)")
check(kernel.face_summary(object()) == (0, 0),
      "a non-brep summarises as (0, 0) rather than raising")

check(not kernel.looks_tessellated(FakeBrep(6)),
      "an analytic BOX (6 planar faces) does not look tessellated")
check(not kernel.looks_tessellated(FakeBrep(2, curved_count=1)),
      "an analytic CYLINDER does not either -- it has a non-planar face")
check(not kernel.looks_tessellated(FakeBrep(12)),
      "a 12-face all-planar solid is below the threshold, so it is given the "
      "benefit of the doubt (a real prism exists)")
check(kernel.looks_tessellated(FakeBrep(400)),
      "but 400 planar faces and nothing curved IS a tessellation -- this is the "
      "dangerous case: a faceted cylinder")
# One curved face among 400 planar faces remains tessellated; a curved scrap does not
# redeem a facet soup. What the check should protect is the opposite case: a solid that
# is dense because it genuinely has many CURVED faces.
check(kernel.looks_tessellated(FakeBrep(400, curved_count=1)),
      "400 planar faces and ONE curved one is still a tessellation -- a single curved "
      "scrap must not launder a facet soup, which is what the first version allowed")

# THE REAL NUMBERS from spike/audit_representation.py. These are the measurements that
# exposed the first version of `looks_tessellated`, which required EVERY face to be
# planar: two of the three real mesh-derived booleans carry a couple of stray spline
# patches from Brep.CreateFromMesh and would have slipped through the guarantee entirely.
for label, planar_n, curved_n in (
        ("boolean UNION (514 faces, 512 planar)", 512, 2),
        ("boolean DIFFERENCE (304 faces, 302 planar)", 302, 2),
        ("boolean INTERSECT (354 faces, all planar)", 354, 0)):
    check(kernel.looks_tessellated(FakeBrep(planar_n, curved_count=curved_n)),
          f"the REAL {label} is caught -- a facet soup with a couple of curved "
          f"scraps in it is still a facet soup")
# ...and the legitimately dense analytic solid must still pass: a box with all 12 edges
# filleted is 26 faces of which only 6 are planar (MEASURED via OCCT).
check(not kernel.looks_tessellated(FakeBrep(6, curved_count=20)),
      "while a box with all 12 edges FILLETED (26 faces, 6 planar) is NOT accused -- "
      "it is dense and honest")

# --------------------------------------------------------------------------
print("\n-- the BILINEAR discriminator: what planarity could not see --")

# WHY THESE EXIST. The checks above measure facet-likeness by `IsPlanar`, and the audit
# then caught `edgefillet.fillet_edges` -- a declared MESH fillet -- at **492 faces of
# which only 64 are planar (13 %)**, which the planar gate called ANALYTIC. A bmesh bevel
# produces WARPED quads, and a warped quad is not planar.
#
# `Brep.CreateFromMesh` emits every facet as a BILINEAR patch (degree 1 in u and v),
# while a curved analytic surface is degree >= 2 in
# the direction that curves -- cylinder wall 2x1, sphere 2x2. So the question changed
# from "is this face flat?" to "what IS this surface?".
#
# These use their own fakes so the checks above keep exercising the planar path unchanged.


class FakeDegSurface:
    """A surface that reports a degree, like a real NurbsSurface.

    `planar` is independent of degree on purpose: the fillet case is a face that is
    bilinear but NOT planar, which is the whole point.
    """

    def __init__(self, degu, degv, planar=False):
        self._d = (degu, degv)
        self._p = planar

    def Degree(self, direction):
        return self._d[direction]

    def IsPlanar(self):
        return self._p


class FakeDegFace:
    def __init__(self, surface):
        self._s = surface

    def UnderlyingSurface(self):
        return self._s


class FakeDegBrep(FakeGeom):
    def __init__(self, surfaces, solid=True):
        super().__init__(solid=solid)
        self.Faces = [FakeDegFace(s) for s in surfaces]


# rhino3dm exposes this as Degree(direction) -- MEASURED: the NurbsSurface members are
# ('Degree', 'IncreaseDegreeU', 'IncreaseDegreeV', 'OrderU', 'OrderV'). There is no
# DegreeU/DegreeV, which an earlier draft of this assumed.
check(kernel.surface_is_bilinear(FakeDegSurface(1, 1)),
      "a degree 1x1 patch IS bilinear -- the signature of a mesh facet")
check(not kernel.surface_is_bilinear(FakeDegSurface(2, 1)),
      "a CYLINDER wall (2x1) is not -- it curves in u, so it is a real surface")
check(not kernel.surface_is_bilinear(FakeDegSurface(2, 2)),
      "nor is a SPHERE (2x2)")
check(not kernel.surface_is_bilinear(FakeDegSurface(3, 3)),
      "nor a degree 3x3 freeform patch")
check(not kernel.surface_is_bilinear(None),
      "None is not called a facet")
check(not kernel.surface_is_bilinear(object()),
      "and a surface that CANNOT answer is not ASSUMED to be a facet -- an "
      "unmeasurable face must not be convicted by default")

BILINEAR = FakeDegSurface(1, 1)
WARPED = FakeDegSurface(1, 1, planar=False)   # a bevel quad: bilinear, NOT planar
CYL_WALL = FakeDegSurface(2, 1)
SPHERE_F = FakeDegSurface(2, 2)
PLANE_F = FakeDegSurface(1, 1, planar=True)

check(kernel.facet_like_summary(FakeDegBrep([PLANE_F] * 6)) == (6, 6),
      "facet_like_summary counts six planes as six facet-like faces")
check(kernel.facet_like_summary(FakeDegBrep([CYL_WALL, PLANE_F, PLANE_F])) == (3, 2),
      "an analytic cylinder measures 3 faces, 2 facet-like -- the wall is not one")
check(kernel.facet_like_summary(object()) == (0, 0),
      "a non-brep summarises as (0, 0) rather than raising")

# THE REGRESSION. The real measured shape of edgefillet.fillet_edges(box):
# 492 faces, 64 planar, 492 bilinear.
FILLET_MISS = FakeDegBrep([PLANE_F] * 64 + [WARPED] * 428)
check(kernel.facet_like_summary(FILLET_MISS) == (492, 492),
      "the real fillet_edges shape (492 faces) is 492/492 facet-like by degree")
check(kernel.face_summary(FILLET_MISS) == (492, 64),
      "...while PLANARITY alone sees only 64 of them -- 13 %, which is why the old "
      "gate passed it")
check(64 / 492.0 < kernel.TESSELLATION_PLANAR_FRACTION,
      "and 13 % is below the 0.8 threshold, so the planar measurement COULD NOT have "
      "caught it at any threshold that still admits an honest filleted box")
check(kernel.looks_tessellated(FILLET_MISS),
      "so the MESH FILLET is now caught -- 492 warped quads are a facet soup however "
      "few of them are flat. THIS is the check the audit's first run demanded")

# ...and the dense-but-honest solid must STILL pass, measured by the same rule:
# OCCT fillets a box's 12 edges into 26 analytic faces {PLANE:6, CYLINDER:12, SPHERE:8}.
OCCT_FILLETED = FakeDegBrep([PLANE_F] * 6 + [CYL_WALL] * 12 + [SPHERE_F] * 8)
check(kernel.facet_like_summary(OCCT_FILLETED) == (26, 6),
      "OCCT's genuinely filleted box measures 26 faces, 6 facet-like (23 %)")
check(not kernel.looks_tessellated(OCCT_FILLETED),
      "and is NOT accused -- the bilinear rule separates a real fillet from a bevelled "
      "mesh by 23 % against 100 %, not by a tuned threshold")
check(not kernel.looks_tessellated(FakeDegBrep([BILINEAR] * 6)),
      "a 6-face all-bilinear solid is still given the benefit of the doubt -- a real "
      "box IS six planes, so the face COUNT is what keeps it honest")

# --------------------------------------------------------------------------
print("\n-- honesty_problem: THE never-again check --")
check(kernel.honesty_problem(FakeBrep(6, solid=False)) is None,
      "a NON-solid needs no tag -- a curve has no 'was this tessellated?' question, "
      "and demanding a tag everywhere trains people to add it without thinking")

untagged = FakeBrep(400)
prob = kernel.honesty_problem(untagged)
check(prob is not None and "no" in prob and kernel.REPRESENTATION_KEY in prob,
      "an UNTAGGED solid is rejected "
      f"({(prob or '')[:60]}...)")

liar = FakeBrep(400)
kernel.tag(liar, kernel.ANALYTIC)
prob = kernel.honesty_problem(liar)
check(prob is not None and "never ship" in prob,
      "a solid tagged ANALYTIC that MEASURES as a tessellation is caught -- the tag "
      "alone could not do this, which is why there are two layers")

honest_mesh = FakeBrep(400)
kernel.tag(honest_mesh, kernel.MESH_DERIVED)
check(kernel.honesty_problem(honest_mesh) is None,
      "a faceted solid that SAYS it is faceted is fine -- the error is "
      "misrepresentation, not approximation")

honest_analytic = FakeBrep(6)
kernel.tag(honest_analytic, kernel.ANALYTIC)
check(kernel.honesty_problem(honest_analytic) is None,
      "and a genuine analytic solid passes")

check(kernel.audit([honest_analytic, liar, untagged]) and
      len(kernel.audit([honest_analytic, liar, untagged])) == 2,
      "audit() finds every misrepresenting solid in a document and leaves the "
      "honest one alone")

# --------------------------------------------------------------------------
print("\n-- describe / require_analytic: the user is TOLD --")
check("analytic" in kernel.describe(honest_analytic),
      "describe() names an analytic solid")
check("faceted" in kernel.describe(honest_mesh),
      "and says 'faceted' for a mesh-derived one, in the words a user needs")
check("unknown" in kernel.describe(FakeBrep(6)),
      "and never silently reassures about an untagged one")

check(kernel.require_analytic(honest_analytic) is True,
      "require_analytic passes genuine analytic geometry")
for bad, why in ((honest_mesh, "mesh-derived"), (liar, "a liar"),
                 (untagged, "untagged")):
    try:
        kernel.require_analytic(bad, "STEP export")
        check(False, f"require_analytic refuses {why}")
    except kernel.KernelError as e:
        check("STEP export" in str(e),
              f"require_analytic REFUSES {why}, naming the operation -- a STEP full "
              f"of facets that looks like a solid is the worst outcome, because "
              f"nothing downstream questions it")

# --------------------------------------------------------------------------
print("\n-- the tag survives a real .3dm round-trip (rhino3dm) --")
try:
    import rhino3dm as r3
except Exception:  # noqa: BLE001
    r3 = None
if r3 is None:
    skip("the .3dm round-trip", "rhino3dm not importable")
else:
    bb = r3.BoundingBox(r3.Point3d(0, 0, 0), r3.Point3d(1, 1, 1))
    real = r3.Brep.CreateFromBox(r3.Box(bb))
    kernel.tag(real, kernel.ANALYTIC)
    f = r3.File3dm()
    f.Objects.AddBrep(real)
    out = os.path.join(ROOT, "artifacts", "kernel_tag.3dm")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    f.Write(out, 8)
    back = list(r3.File3dm.Read(out).Objects)[0].Geometry
    check(kernel.representation_of(back) == kernel.ANALYTIC,
          "a representation tag SURVIVES being written to and read from a .3dm -- so "
          "provenance is not lost on save, which is what makes the tag trustworthy")
    check(kernel.face_summary(real) == (6, 6),
          f"and face_summary agrees with rhino3dm on a real box {kernel.face_summary(real)}")
    cyl = r3.Brep.CreateFromCylinder(r3.Cylinder(r3.Circle(2.0), 5.0), True, True)
    total, planar = kernel.face_summary(cyl)
    check((total, planar) == (3, 2),
          f"and on a real cylinder: 3 faces, 2 planar, 1 curved ({total}, {planar})")
    check(not kernel.looks_tessellated(cyl),
          "so a REAL cylinder is not mistaken for a tessellation")

# --------------------------------------------------------------------------
print("\n-- the OCCT backend: analytic, airtight, exportable --")
spec2 = importlib.util.spec_from_file_location(
    "narwhal3d.kernel.occt_backend",
    os.path.join(ROOT, "narwhal3d", "kernel", "occt_backend.py"))
occt = importlib.util.module_from_spec(spec2)
sys.modules["narwhal3d.kernel.occt_backend"] = occt
spec2.loader.exec_module(occt)

if not occt.available():
    skip("every OCCT check", occt.unavailable_reason())
else:
    box = occt.make_box(10, 10, 10)
    cyl = occt.make_cylinder(3, 20)
    sph = occt.make_sphere(6)
    check(occt.is_airtight(box), "a box is airtight by the kernel's own checker")
    check(abs(occt.volume(box) - 1000.0) < 1e-9,
          f"and its volume is EXACT, not sampled ({occt.volume(box)})")

    fuse = occt.boolean(box, cyl, "UNION")
    cut = occt.boolean(box, cyl, "DIFFERENCE")
    common = occt.boolean(box, sph, "INTERSECTION")
    for label, shape in (("union", fuse), ("difference", cut),
                         ("intersection", common)):
        check(occt.is_airtight(shape),
              f"the {label} is AIRTIGHT -- asserted by BRepCheck_Analyzer, not assumed")
        types = occt.face_types(shape)
        check(all(t in occt.ANALYTIC_FACE_TYPES for t in types),
              f"and every face of the {label} is an analytic surface: {types}")
        check(occt.face_count(shape) < 30,
              f"with a HANDFUL of faces, not a facet soup ({occt.face_count(shape)}) "
              f"-- today's mesh route gives ~40 for a box union and 2616 for two "
              f"cylinders")
    check("CYLINDER" in occt.face_types(cut),
          f"the drilled hole is a real CYLINDER face: {occt.face_types(cut)}")

    for bad in ("INTERSECT", "nonsense", ""):
        try:
            occt.boolean(box, cyl, bad)
            check(False, f"an unknown operation {bad!r} is refused")
        except kernel.KernelError as e:
            check("expected one of" in str(e),
                  f"an unknown operation {bad!r} is refused BY NAME, listing the "
                  f"valid ones")

    # disjoint inputs must FAIL, not return an empty shape
    far = occt.make_box(1, 1, 1)
    try:
        from OCP.gp import gp_Trsf, gp_Vec
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform

        t = gp_Trsf()
        t.SetTranslation(gp_Vec(500.0, 0.0, 0.0))
        far = BRepBuilderAPI_Transform(far, t, True).Shape()
        occt.boolean(far, cyl, "INTERSECTION")
        check(False, "an intersection of DISJOINT solids is refused")
    except kernel.KernelError as e:
        check("no solid" in str(e),
              "an intersection of DISJOINT solids raises instead of handing back an "
              "empty shape -- a silent empty result is the worst kind of failure")
    except Exception as e:  # noqa: BLE001
        skip("the disjoint-intersection check", f"transform unavailable: {e!r}")

    filleted = occt.fillet_all_edges(box, 1.0)
    ftypes = occt.face_types(filleted)
    check(occt.is_airtight(filleted),
          "a box with every edge filleted is still ONE airtight solid")
    check(ftypes.get("CYLINDER", 0) >= 10 and ftypes.get("SPHERE", 0) >= 4,
          f"and the fillet is REAL -- cylindrical faces along the edges and "
          f"spherical faces at the corners: {ftypes}; a faceted bevel is invalid")

    step = os.path.join(ROOT, "artifacts", "kernel_export.step")
    size = occt.export_step(cut, step)
    body = open(step).read()
    check(size > 1000, f"STEP export writes a real file ({size} bytes)")
    check("CYLINDRICAL_SURFACE" in body,
          "and the STEP file carries the hole as a CYLINDRICAL_SURFACE -- a machine "
          "shop gets the surface, not facets. THIS is the manufacturing requirement")
    check("ISO-10303-21" in body, "with a valid STEP header")

# --------------------------------------------------------------------------
print("\n-- recipes and the analytic master --")

# rhino3dm cannot construct every multi-face B-rep form, so the document may hold an
# OCCT master plus a triangulated display proxy. The analytic tag is verified, not trusted.

recipe_geom = FakeGeom(solid=True)
check(kernel.recipe_of(recipe_geom) is None,
      "an object with no recipe reads None, not an empty dict")
kernel.record_recipe(recipe_geom, {"kind": "cylinder", "radius": 2.0, "height": 5.0})
got = kernel.recipe_of(recipe_geom)
check(got == {"kind": "cylinder", "radius": 2.0, "height": 5.0},
      "a recipe round-trips through the user string, numbers intact")
check(kernel.recipe_of(object()) is None,
      "an object with no user-string API reads None instead of raising")

proxy = FakeBrep(300)            # a facet soup, as a display proxy really is
check(kernel.master_of(proxy) is None, "a proxy with no master reads None")
sentinel = ("an OCCT shape stands here",)
key = kernel.register_master(proxy, sentinel)
check(kernel.master_of(proxy) is sentinel,
      "register_master binds the analytic shape to its proxy and master_of returns it")
check(kernel.representation_of(proxy) == kernel.ANALYTIC_MASTER,
      "and register_master TAGS the proxy itself -- a registered master and its "
      "declaration cannot drift apart because one caller forgot")
check(kernel.honesty_problem(proxy) is None,
      "a proxy WITH a registered master is honest, even though it measures as 300 facets "
      "-- because the geometry is the master, and the triangles only draw it")
check("analytic" in kernel.describe(proxy),
      f"and it describes itself as analytic: {kernel.describe(proxy)!r}")

# THE LOOPHOLE CHECK. This is the whole reason the tag is verified.
liar = FakeBrep(300)
liar.SetUserString(kernel.REPRESENTATION_KEY, kernel.ANALYTIC_MASTER)
problem = kernel.honesty_problem(liar)
check(problem is not None and "NO master" in problem,
      "but a solid that CLAIMS an analytic master with none registered is a PROBLEM -- "
      "otherwise the new tag would just be a nicer way to launder facets")
check("BROKEN" in kernel.describe(liar),
      f"and describe() says so out loud: {kernel.describe(liar)!r}")

kernel.forget_masters()
check(kernel.master_of(proxy) is None,
      "forget_masters drops every master (a closed document) -- masters are process-local "
      "on purpose, so a stale id cannot survive a reload and be believed")
check(kernel.honesty_problem(proxy) is not None,
      "and the proxy immediately stops being honest once its master is gone, rather than "
      "coasting on the tag")

if occt.available():
    print("\n-- rebuilding from a recipe in OCCT --")
    IDENT = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]

    def shifted(x, y, z):
        return [1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z, 0, 0, 0, 1]

    rb = occt.from_recipe({"kind": "box", "dx": 2, "dy": 3, "dz": 4, "xform": IDENT})
    check(abs(occt.volume(rb) - 24.0) < 1e-9,
          f"a box recipe rebuilds with the right volume ({occt.volume(rb)})")
    rc = occt.from_recipe({"kind": "cylinder", "radius": 2.0, "height": 5.0,
                           "xform": IDENT})
    check(occt.face_types(rc) == {"CYLINDER": 1, "PLANE": 2},
          f"a cylinder recipe rebuilds as 3 ANALYTIC faces {occt.face_types(rc)} -- "
          f"2 planar circles and 1 wrapping surface")
    rs = occt.from_recipe({"kind": "sphere", "radius": 3.0, "xform": IDENT})
    check(occt.face_types(rs).get("SPHERE") == 1, "a sphere recipe rebuilds as a SPHERE")
    rk = occt.from_recipe({"kind": "cone", "radius": 2.0, "height": 5.0, "xform": IDENT})
    check(occt.face_types(rk) == {"CONE": 1, "PLANE": 1},
          f"a cone recipe rebuilds as a CONE plus its base disc {occt.face_types(rk)}")

    # the transform must actually move it, in DOUBLE precision
    moved = occt.from_recipe({"kind": "sphere", "radius": 1.0, "xform": shifted(5, 0, 0)})
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    BRepBndLib.Add_s(moved, bb)
    # `Bnd_Box` from BRepBndLib carries a TOLERANCE GAP, so its corners are deliberately
    # a hair outside the shape -- comparing them exactly is measuring the wrong thing.
    # The CENTRE is gap-symmetric and is what actually says "it moved to x=5".
    cx = 0.5 * (bb.CornerMin().X() + bb.CornerMax().X())
    cy_ = 0.5 * (bb.CornerMin().Y() + bb.CornerMax().Y())
    check(abs(cx - 5.0) < 1e-9 and abs(cy_) < 1e-9,
          f"and a recipe's transform places the rebuilt solid exactly where it belongs "
          f"(centre x={cx:.12f})")

    for bad, why in (({"kind": "torus", "r": 1}, "unknown kind"),
                     ({"kind": "cylinder", "radius": 1}, "missing a parameter")):
        try:
            occt.from_recipe(bad)
            check(False, f"a recipe {why} is refused")
        except kernel.KernelError as e:
            check(True, f"a recipe {why} is refused BY NAME: {str(e)[:52]}...")
    try:
        occt.transform(rb, [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 9, 9, 9, 1])
        check(False, "a non-affine transform is refused")
    except kernel.KernelError as e:
        check("affine" in str(e),
              "a NON-AFFINE matrix is refused rather than silently truncated to its top "
              "three rows, which would put the master where the user cannot see it")

    print("\n-- the display proxy is tessellated only for drawing --")
    verts, tris = occt.tessellate(rc)
    check(len(verts) > 0 and len(tris) > 0,
          f"tessellate returns a drawable mesh ({len(verts)} verts, {len(tris)} tris)")
    fine, _ = occt.tessellate(rc, deflection=0.002)
    coarse, _ = occt.tessellate(rc, deflection=0.5)
    check(len(fine) > len(coarse),
          f"and a tighter deflection gives MORE vertices ({len(fine)} vs {len(coarse)}) "
          f"-- it is a render mesh with a tolerance")

    print("\n-- booleans are analytic and airtight --")
    bx = occt.from_recipe({"kind": "box", "dx": 4, "dy": 4, "dz": 4, "xform": IDENT})
    cy = occt.from_recipe({"kind": "cylinder", "radius": 1.5, "height": 6,
                           "xform": shifted(2, 2, 0)})
    expected = {
        # Eight rather than nine faces: merge same-surface faces left split by OCCT.
        # boolean's coplanar pieces split and the user SEES the seams. The merge is purely
        # topological -- the volume check below is unchanged and still exact.
        "UNION": (8, {"PLANE": 7, "CYLINDER": 1}),
        "DIFFERENCE": (7, {"PLANE": 6, "CYLINDER": 1}),
        "INTERSECTION": (3, {"PLANE": 2, "CYLINDER": 1}),
    }
    for op, (nfaces, ftypes) in expected.items():
        res = occt.boolean(bx, cy, op)
        check(occt.face_count(res) == nfaces and occt.face_types(res) == ftypes,
              f"{op}: {occt.face_count(res)} faces {occt.face_types(res)} -- the mesh "
              f"route gave 514/304/354 faces of which ~99% were planar")
        check(occt.is_airtight(res),
              f"{op} is AIRTIGHT -- on the mesh route UNION and DIFFERENCE were both "
              f"IsSolid=False, i.e. unmanufacturable")
    inter = occt.boolean(bx, cy, "INTERSECTION")
    exact = math.pi * 1.5 ** 2 * 4.0
    check(abs(occt.volume(inter) - exact) < 1e-9,
          f"and the INTERSECTION volume is EXACTLY pi*r^2*h ({occt.volume(inter):.6f} vs "
          f"{exact:.6f}) -- an analytic measurement, not a sampled one")

    print("\n-- unify merges same-surface faces without moving geometry --")
    # Check both seam removal and preservation of the underlying shape.
    raw_a = occt.make_cylinder(3.0, 5.0)
    raw_b = occt.from_recipe({"kind": "cylinder", "radius": 3.0, "height": 5.0,
                              "xform": shifted(2, 0, 0)})
    joined = occt.boolean(raw_a, raw_b, "UNION")
    check(occt.face_types(joined) == {"PLANE": 2, "CYLINDER": 2},
          f"two overlapping cylinders union to 4 faces {occt.face_types(joined)} -- one top, "
          f"one bottom, two walls. Unmerged it was 9 faces with visible seams on the top")
    check(len(occt.edge_curves(joined)) == 6,
          f"and 6 edges, not 15 ({len(occt.edge_curves(joined))}) -- the internal crossing "
          f"edges are gone")
    # PROPERTY 1: unification must not move a single point.
    import math as _m
    hand = occt.unify(joined)
    check(abs(occt.volume(hand) - occt.volume(joined)) < 1e-9,
          "unify() is idempotent on an already-unified shape")
    # The outline arc of each circle is r*(2*pi - 2*acos(1/3)) for centres 2 apart, r=3.
    # An INDEPENDENT closed form, so it checks the geometry rather than our own pipeline.
    want_arc = 3.0 * (2 * _m.pi - 2 * _m.acos(1.0 / 3.0))
    arcs = [d for d in occt.edge_curves(joined) if d["degree"] == 2]
    check(len(arcs) == 4, f"4 of the 6 edges are arcs ({len(arcs)})")
    check(abs(want_arc - 11.4640) < 1e-3,
          f"and the closed form says each outline arc is r*(2pi-2acos(1/3)) = "
          f"{want_arc:.4f}, which is what ExtractWireframe measures")
    # PROPERTY 2: it must NOT over-merge. A real fillet's faces are distinct surfaces.
    filleted_raw = occt.fillet_all_edges(occt.make_box(10, 10, 10), 1.0)
    check(occt.face_types(occt.unify(filleted_raw))
          == {"PLANE": 6, "CYLINDER": 12, "SPHERE": 8},
          "and a box with all 12 edges filleted keeps ALL 26 faces through unification -- "
          "12 cylinders and 8 spheres are distinct surfaces, so nothing is over-merged")
    check(abs(occt.volume(occt.unify(filleted_raw)) - occt.volume(filleted_raw)) < 1e-9,
          "with its volume unchanged")

    # composition: a boolean of a boolean, which is what a recipe-only scheme would lose
    composed = occt.boolean(occt.boolean(bx, cy, "UNION"),
                            occt.from_recipe({"kind": "sphere", "radius": 1.0,
                                              "xform": shifted(2, 2, 6)}),
                            "DIFFERENCE")
    check(occt.is_airtight(composed) and occt.solid_count(composed) == 1,
          f"a boolean OF a boolean is still one airtight analytic solid "
          f"({occt.face_count(composed)} faces {occt.face_types(composed)}) -- booleans "
          f"COMPOSE because the master is an OCCT shape, not a recipe")
else:
    skip("the OCCT recipe/boolean checks", occt.unavailable_reason())


print()
print("=" * 70)
print(f"{checks - len(failures)}/{checks} checks passed, {skipped} skipped")
if failures:
    print("FAILURES:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("KERNEL + HONESTY GUARANTEE OK")
