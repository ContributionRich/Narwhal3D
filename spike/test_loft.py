"""Loft (kernel half): the two-section STRAIGHT (ruled) loft, N sections, every
loft style, automatic section alignment, and honest refusals.
Pure, bpy-free (runs under the rhino3dm interpreter).

Run: tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_loft.py
"""
import importlib.util
import math
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import rhino3dm as r3  # noqa: E402

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

failures = []
checks = 0


def check(cond, msg):
    global checks
    checks += 1
    if not cond:
        failures.append(msg)


def edge_error(face, curve, vparam):
    """Max distance between the face's iso-curve at `vparam` and `curve`,
    compared at matching normalised parameters (0..1)."""
    fd_u = face.Domain(0)
    cd = curve.Domain
    worst = 0.0
    for i in range(21):
        f = i / 20.0
        sp = face.PointAt(fd_u.T0 + (fd_u.T1 - fd_u.T0) * f, vparam)
        cp = curve.PointAt(cd.T0 + (cd.T1 - cd.T0) * f)
        worst = max(worst, math.dist((sp.X, sp.Y, sp.Z), (cp.X, cp.Y, cp.Z)))
    return worst


def refused(fn):
    """(raised?, message) for a call expected to raise ValueError."""
    try:
        fn()
    except ValueError as e:
        return True, str(e)
    return False, ""


# --- 1. loft a degree-1 LINE to a degree-3 curve: works after the kernel's
#        own compatibility, and the surface edges match the inputs to ~1e-9 ------
line = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(10, 0, 0)).ToNurbsCurve()
wave = r3.NurbsCurve.CreateControlPointCurve(
    [r3.Point3d(0, 0, 5), r3.Point3d(3, 2, 5), r3.Point3d(7, -2, 5),
     r3.Point3d(10, 0, 5)], 3)
brep = bridge.loft_brep([line, wave], style="Straight")
check(brep is not None and len(brep.Faces) == 1,
      f"line->degree3 loft: expected a 1-face Brep, got {brep}")
if brep is not None and len(brep.Faces) == 1:
    face = brep.Faces[0]
    dv = face.Domain(1)
    e0 = min(edge_error(face, line, dv.T0), edge_error(face, line, dv.T1))
    e1 = min(edge_error(face, wave, dv.T0), edge_error(face, wave, dv.T1))
    check(e0 < 1e-9, f"loft edge does not match the line: err {e0:.2e}")
    check(e1 < 1e-9, f"loft edge does not match the degree-3 curve: err {e1:.2e}")
    print(f"loft (deg1->deg3) edge match: line {e0:.2e}, curve {e1:.2e}")

# --- 2. loft two lines -> a flat quad, corners at the four endpoints -----
la = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(10, 0, 0)).ToNurbsCurve()
lb = r3.LineCurve(r3.Point3d(0, 8, 0), r3.Point3d(10, 8, 0)).ToNurbsCurve()
brep = bridge.loft_brep([la, lb])
check(brep is not None and len(brep.Faces) == 1, f"two-line loft failed: {brep}")
if brep is not None:
    bb = brep.GetBoundingBox()
    check(abs(bb.Min.X) < 1e-9 and abs(bb.Max.X - 10) < 1e-9
          and abs(bb.Min.Y) < 1e-9 and abs(bb.Max.Y - 8) < 1e-9,
          f"two-line loft bbox wrong: X[{bb.Min.X},{bb.Max.X}] Y[{bb.Min.Y},{bb.Max.Y}]")

# ===========================================================================
# -- N sections, LOOSE style, honest refusals.
# ===========================================================================

# --- 3. STRAIGHT through THREE curves passes through ALL THREE, not just
#        the outer two. The middle line is offset in Y so a 2-section ruled
#        surface (which would read y=0 at mid-V) cannot fake this. --------------
s0 = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(10, 0, 0)).ToNurbsCurve()
s1 = r3.LineCurve(r3.Point3d(0, 5, 5), r3.Point3d(10, 5, 5)).ToNurbsCurve()
s2 = r3.LineCurve(r3.Point3d(0, 0, 10), r3.Point3d(10, 0, 10)).ToNurbsCurve()
brep = bridge.loft_brep([s0, s1, s2], style="Straight")
check(brep is not None and len(brep.Faces) == 1,
      f"3-section straight loft: expected 1-face Brep, got {brep}")
if brep is not None and len(brep.Faces) == 1:
    face = brep.Faces[0]
    dv = face.Domain(1)
    vmid = 0.5 * (dv.T0 + dv.T1)
    eA = edge_error(face, s0, dv.T0)
    eB = edge_error(face, s1, vmid)
    eC = edge_error(face, s2, dv.T1)
    check(eA < 1e-9, f"3-section straight: first section not interpolated: {eA:.2e}")
    check(eB < 1e-9,
          f"3-section straight: MIDDLE section not passed through (y=5): {eB:.2e}")
    check(eC < 1e-9, f"3-section straight: last section not interpolated: {eC:.2e}")
    print(f"straight-through-3: ends {eA:.1e}/{eC:.1e}, MIDDLE {eB:.1e}")

# --- 4. LOOSE through two identical circles reproduces their control
#        points EXACTLY (the help's wording is a literal equality here). --------
c0 = r3.Circle(r3.Point3d(0, 0, 0), 5).ToNurbsCurve()
c1 = r3.Circle(r3.Point3d(0, 0, 7), 5).ToNurbsCurve()
brep = bridge.loft_brep([c0, c1], style="Loose")
check(brep is not None and len(brep.Faces) == 1,
      f"loose circle loft: expected 1-face Brep, got {brep}")
if brep is not None and len(brep.Faces) == 1:
    nsurf = brep.Surfaces[0].ToNurbsSurface()
    pts = nsurf.Points
    cvs0 = list(c0.Points)
    cvs1 = list(c1.Points)
    check(pts.CountU == len(cvs0) and pts.CountV == 2,
          f"loose net wrong size: {pts.CountU}x{pts.CountV}, "
          f"expected {len(cvs0)}x2")
    worst = 0.0
    if pts.CountU == len(cvs0) and pts.CountV == 2:
        for i in range(pts.CountU):
            for row, cv in ((0, cvs0[i]), (1, cvs1[i])):
                scp = pts.GetControlPoint(i, row)
                # EXACT equality -- _loft_surface_from_net copies the curve CV
                # (weighted X/Y/Z and weight) verbatim into the surface net.
                exact = (scp.X == cv.X and scp.Y == cv.Y and scp.Z == cv.Z
                         and getattr(scp, "W", 1.0) == getattr(cv, "W", 1.0))
                check(exact,
                      f"loose CV[{i},{row}] not exact: "
                      f"({scp.X},{scp.Y},{scp.Z},{getattr(scp, 'W', 1.0)}) != "
                      f"({cv.X},{cv.Y},{cv.Z},{getattr(cv, 'W', 1.0)})")
                worst = max(worst, abs(scp.X - cv.X), abs(scp.Y - cv.Y),
                            abs(scp.Z - cv.Z))
        print(f"loose-two-circles: worst CV diff {worst:.2e} (want 0.0)")

# --- 5. LOOSE through sections of DIFFERENT degree still produces a valid
#        surface; the compatibility step is what we assert -- both sections, once
#        made compatible, share one degree and one CV count (a rectangular net). -
compat = bridge._loft_compatible_sections([line, wave])
degs = {nc.Degree for nc in compat}
counts = {len(list(nc.Points)) for nc in compat}
check(degs == {3}, f"compat sections not all degree 3: {degs}")
check(len(counts) == 1, f"compat sections differ in CV count: {counts}")
brep = bridge.loft_brep([line, wave], style="Loose")
check(brep is not None and len(brep.Faces) == 1,
      f"loose diff-degree loft: expected 1-face Brep, got {brep}")
if brep is not None and len(brep.Faces) == 1:
    nsurf = brep.Surfaces[0].ToNurbsSurface()
    check(nsurf.Degree(0) == 3,
          f"loose diff-degree: surface degU {nsurf.Degree(0)}, expected 3")
    check(nsurf.Points.CountU == next(iter(counts)),
          f"loose diff-degree: net CountU {nsurf.Points.CountU} != "
          f"compat CV count {next(iter(counts))}")
    print(f"loose-diff-degree: degU {nsurf.Degree(0)}, "
          f"net {nsurf.Points.CountU}x{nsurf.Points.CountV}")

# --- 6. BOTH styles interpolate the first AND last section (a loft that
#        misses its own end curves is the classic failure). --------------------
for style, secs in (("Straight", [c0, c1]), ("Loose", [c0, c1])):
    b = bridge.loft_brep(secs, style=style)
    if b is not None and len(b.Faces) == 1:
        face = b.Faces[0]
        dv = face.Domain(1)
        eF = edge_error(face, secs[0], dv.T0)
        eL = edge_error(face, secs[-1], dv.T1)
        check(eF < 1e-9, f"{style}: first section not interpolated: {eF:.2e}")
        check(eL < 1e-9, f"{style}: last section not interpolated: {eL:.2e}")
    else:
        check(False, f"{style} loft of two circles failed: {b}")

# --- 7. Normal / Tight / Uniform. These used to be refused as "described by feel";
#        the reference API defines them exactly (LoftType: Normal "uses chord-length
#        parameterization in the loft direction", Tight "square root of chord-length",
#        Uniform "knot vectors will be uniform"). Each must place section j EXACTLY at
#        the V parameter its definition gives -- unequally spaced sections make the
#        three answers differ, so one style cannot pass as another. ----------------
zs = (0.0, 1.0, 10.0, 11.0)
spaced = [r3.LineCurve(r3.Point3d(0, 0, z), r3.Point3d(10, 0, z)).ToNurbsCurve() for z in zs]
chord = [z / zs[-1] for z in zs]
roots = [0.0]
for a, b in zip(zs, zs[1:]):
    roots.append(roots[-1] + math.sqrt(b - a))
expected = {"Normal": chord, "Tight": [r / roots[-1] for r in roots],
            "Uniform": [j / 3.0 for j in range(4)]}
for style, params in expected.items():
    b = bridge.loft_brep(spaced, style=style)
    face = b.Faces[0]
    dv = face.Domain(1)
    worst = max(edge_error(face, sec, dv.T0 + (dv.T1 - dv.T0) * v)
                for sec, v in zip(spaced, params))
    check(worst < 1e-9, f"{style}: every section lies at its {style} V parameter "
                        f"(worst {worst:.2e})")
wrong = bridge.loft_brep(spaced, style="Normal").Faces[0]
dvw = wrong.Domain(1)
check(edge_error(wrong, spaced[1], dvw.T0 + (dvw.T1 - dvw.T0) * expected["Uniform"][1]) > 1.0,
      "the parameter check can fail: Normal's surface is NOT at the uniform parameter")

# --- 7b. Automatic alignment: a reversed open section or a shifted closed seam must
#         not fold or twist the surface. The aligned loft is the same surface as the
#         one built from already-aligned input (equal area), while the unaligned
#         build of the same input is not (MEASURED: 261 vs 423 for the lines). ------
def _area(b):
    from narwhal3d.kernel import occt_backend as _ob
    shape = kernel_master(b)
    return _ob.mass_properties(shape, solid=False)["area"]


def kernel_master(b):
    from narwhal3d import kernel as _k
    return _k.master_of(b) or bridge._occt_operand(b)


rows = [r3.LineCurve(r3.Point3d(0, y, z), r3.Point3d(20, y, z)).ToNurbsCurve()
        for y, z in ((0, 0), (10, 3), (20, 0))]
flipped = [rows[0], rows[1].Duplicate(), rows[2]]
flipped[1].Reverse()
try:
    good = _area(bridge.loft_brep(rows, style="Normal"))
    fixed = _area(bridge.loft_brep(flipped, style="Normal"))
    folded = _area(bridge.loft_brep(flipped, style="Normal", align=False))
    check(abs(fixed - good) < 1e-6 * good and abs(folded - good) > 0.1 * good,
          f"a reversed open section is re-aligned (aligned {fixed:.3f}, want {good:.3f}, "
          f"unaligned {folded:.3f})")
    circles = [bridge.make_circle((0, 0, z), r) for z, r in ((0, 5), (10, 8), (20, 5))]
    shifted = [c.Duplicate() for c in circles]
    shifted[1].Reverse()
    d = shifted[1].Domain
    shifted[1].ChangeClosedCurveSeam(d.T0 + 0.375 * (d.T1 - d.T0))
    good = _area(bridge.loft_brep(circles, style="Normal"))
    fixed = _area(bridge.loft_brep(shifted, style="Normal"))
    check(abs(fixed - good) < 1e-6 * good,
          f"a reversed circle with a moved seam is re-aligned ({fixed:.3f} vs {good:.3f})")
except ImportError as e:
    print(f"  .. alignment area checks skipped ({e})")

# --- 7c. The alignment and corner decisions run in RUST when the pure core is present,
#         and agree with the Python fallback on every case above. ------------------
try:
    native_surface = sys.modules.get("narwhal3d.native_surface") or _load(
        "narwhal3d.native_surface", "narwhal3d/native_surface.py")
    if native_surface._lib() is None:
        print("  .. Rust surface decisions skipped (NARWHAL_PURE_CORE not built)")
    else:
        before = native_surface.calls
        bridge.loft_brep(flipped, style="Normal")
        bridge.loft_brep(shifted, style="Normal")
        check(native_surface.calls > before, "loft alignment is decided in Rust")
        cases = [(bridge._samples(a), bridge._samples(b), closed)
                 for a, b, closed in ((rows[0], flipped[1], False), (rows[0], rows[1], False),
                                      (circles[0], shifted[1], True),
                                      (circles[0], circles[1], True))]
        check(all(native_surface.should_reverse(*c) == bridge._should_reverse_python(*c)
                  for c in cases) and [native_surface.should_reverse(*c) for c in cases]
              == [True, False, True, False],
              "Rust and Python agree on every reversal decision")
        corner_cases = ([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 0, 0), (1, 0, 0)],
                        [(0, 0, 0), (1, 0, 0), (1, 0, 0)], [(0, 0, 0), (1, 0, 0), (2, 0, 0)])
        check([native_surface.corner_problem(c, 1e-9) for c in corner_cases]
              == [bridge._corner_problem_python(c, 1e-9) for c in corner_cases]
              == [(0, (0, 0)), (1, (0, 0)), (2, (2, 3)), (3, (0, 0))],
              "Rust and Python agree on every corner-surface verdict")
except NameError as e:
    print(f"  .. Rust surface decisions skipped ({e})")

# The obsolete Developable style is refused and names itself.
raised, msg = refused(lambda: bridge.loft_brep([line, wave], style="Developable"))
check(raised and "Developable" in msg,
      f"unknown style must refuse by name (raised={raised}, msg={msg!r})")

# --- 8. closed loft refused by name; <2 sections refused. --------------
raised, msg = refused(lambda: bridge.loft_brep([line, wave], closed=True))
check(raised and "closed" in msg.lower(),
      f"closed loft must refuse by name (raised={raised}, msg={msg!r})")

for n in (0, 1):
    raised, msg = refused(lambda k=n: bridge.loft_brep([line] * k))
    check(raised, f"loft of {n} sections must be refused")

# --- 9. the lofted surface TESSELLATES. Under Blender we DRIVE the real
#        display path, geometry_to_bmesh, and assert the mesh is non-empty, finite,
#        and SPANS the sections (z 0->7, radius ~5) -- a collapsed/flattened loft
#        would lose that span. Under the bpy-free interpreter there is no bmesh, so
#        we fall back to sampling the same grid brep_to_bmesh would (Surface.PointAt
#        over the face domain) and assert every point is finite -- NurbsSurface.Create
#        leaves knots uninitialised (-> NaN) unless every knot is set, so a NaN here
#        is the real tessellation failure mode. ./uishot.sh is the end-to-end gate. --
try:
    import bpy  # noqa: F401
    HAVE_BPY = True
except Exception:
    HAVE_BPY = False

if HAVE_BPY:
    b = bridge.loft_brep([c0, c1], style="Loose")
    bm = bridge.geometry_to_bmesh(b)
    nverts, nfaces = len(bm.verts), len(bm.faces)
    check(nverts > 0 and nfaces > 0,
          f"loose loft tessellated to an empty mesh: {nverts} verts, {nfaces} faces")
    xs = [v.co.x for v in bm.verts]
    ys = [v.co.y for v in bm.verts]
    zs = [v.co.z for v in bm.verts]
    finite = all(math.isfinite(c) for c in xs + ys + zs)
    check(finite, "loose loft mesh has a non-finite vertex")
    if xs:
        # Two radius-5 circles at z=0 and z=7: the mesh must SPAN both planes and
        # reach ~radius 5 (tessellation inscribes, so x/y undershoot a little).
        check(abs(min(zs) - 0.0) < 1e-6 and abs(max(zs) - 7.0) < 1e-6,
              f"loose loft mesh does not span z 0->7: z[{min(zs)},{max(zs)}]")
        check(max(xs) > 4.7 and min(xs) < -4.7 and max(ys) > 4.7 and min(ys) < -4.7,
              f"loose loft mesh does not reach radius ~5: "
              f"X[{min(xs):.2f},{max(xs):.2f}] Y[{min(ys):.2f},{max(ys):.2f}]")
    print(f"tessellate (bpy): {nverts} verts, {nfaces} faces, "
          f"z[{min(zs):.1f},{max(zs):.1f}]")
else:
    for label, secs, style in (
            ("straight-3", [s0, s1, s2], "Straight"),
            ("loose-circles", [c0, c1], "Loose"),
            ("loose-diff-degree", [line, wave], "Loose")):
        all_finite = True
        try:
            b = bridge.loft_brep(secs, style=style)
        except ValueError:
            # A surface whose knots/CVs are left uninitialised samples to NaN,
            # which CreateFromSurface rejects -- a tessellation failure too.
            b = None
        if b is not None and len(b.Faces) == 1:
            face = b.Faces[0]
            du, dvv = face.Domain(0), face.Domain(1)
            for iu in range(9):
                for iv in range(9):
                    u = du.T0 + (du.T1 - du.T0) * iu / 8.0
                    v = dvv.T0 + (dvv.T1 - dvv.T0) * iv / 8.0
                    p = face.PointAt(u, v)
                    if not (math.isfinite(p.X) and math.isfinite(p.Y)
                            and math.isfinite(p.Z)):
                        all_finite = False
        else:
            all_finite = False
        check(all_finite,
              f"{label} loft does not tessellate cleanly (NaN/invalid grid)")

# --- report ------------------------------------------------------------------
print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    for f in failures:
        print("FAIL:", f)
    sys.exit(1)
print("LOFT (+ ) PASSED")
