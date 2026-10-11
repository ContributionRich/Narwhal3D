"""Spike: verify rhino3dm can do everything we need for the reference CAD application parity on Linux.

Uses ONLY APIs verified against rhino3dm 8.35.0 (see probe*.py history).
Run: .venv/bin/python spike/test_geometry.py
"""
import sys
import time

import rhino3dm as r3

results = []


def check(name):
    def deco(fn):
        t0 = time.perf_counter()
        try:
            fn()
            dt = (time.perf_counter() - t0) * 1000
            results.append((name, "PASS", f"{dt:.1f}ms"))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
        return fn

    return deco


def sample_curve(curve, n=64):
    """Sample a curve to points (our Blender-viewport path)."""
    d = curve.Domain
    return [curve.PointAt(d.T0 + (d.T1 - d.T0) * i / n) for i in range(n + 1)]


def grid_sample(face, nu=32, nv=32):
    """Grid-sample a brep face domain to points (untrimmed primitives only)."""
    du, dv = face.Domain(0), face.Domain(1)
    pts = []
    for i in range(nu + 1):
        row = []
        for j in range(nv + 1):
            u = du.T0 + (du.T1 - du.T0) * i / nu
            v = dv.T0 + (dv.T1 - dv.T0) * j / nv
            row.append(face.PointAt(u, v))
        pts.append(row)
    return pts


# ---------------------------------------------------------------- curves

@check("Line -> NurbsCurve")
def _():
    curve = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(10, 0, 0)).ToNurbsCurve()
    assert curve is not None and curve.IsValid
    pts = sample_curve(curve)
    assert len(pts) == 65


@check("Circle -> NurbsCurve (full)")
def _():
    circle = r3.Circle(r3.Point3d(0, 0, 0), 5.0)
    curve = circle.ToNurbsCurve()
    assert curve is not None and curve.IsValid and curve.IsClosed
    pts = sample_curve(curve)
    # sampled points should all be ~radius from center
    for p in pts:
        d = (p.X ** 2 + p.Y ** 2) ** 0.5
        assert abs(d - 5.0) < 1e-6, f"sampled point off circle: {p}"


@check("Circle on arbitrary plane (translate)")
def _():
    c = r3.Circle(r3.Point3d(0, 0, 7), 2.0).ToNurbsCurve()
    p = c.PointAt(c.Domain.T1 / 2)
    assert abs(p.Z - 7.0) < 1e-9


@check("Polyline (control-point curve)")
def _():
    # degree must be < point count: cubic needs >= 4 points
    pts = [r3.Point3d(0, 0, 0), r3.Point3d(2, 4, 0), r3.Point3d(5, 1, 0), r3.Point3d(7, 6, 0)]
    crv = r3.NurbsCurve.CreateControlPointCurve(pts, 3)
    assert crv is not None and crv.IsValid
    # quadratic works with 3 points
    crv2 = r3.NurbsCurve.CreateControlPointCurve(pts[:3], 2)
    assert crv2 is not None and crv2.IsValid


@check("Arc -> NurbsCurve")
def _():
    arc = r3.Arc(r3.Circle(r3.Point3d(0, 0, 0), 5.0), 1.57)
    n = r3.NurbsCurve.CreateFromArc(arc)
    assert n is not None and n.IsValid


@check("curve.Trim / Split")
def _():
    lc = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(10, 0, 0)).ToNurbsCurve()
    t = lc.Trim(2.5, 7.5)
    assert t is not None and abs(t.Domain.T1 - t.Domain.T0 - 5.0) < 1e-9


@check("curve.TryGetCircle")
def _():
    c = r3.Circle(r3.Point3d(0, 0, 0), 3.0).ToNurbsCurve()
    circle = c.TryGetCircle()
    assert abs(circle.Radius - 3.0) < 1e-9


@check("curve offset via FrameAt sampling")
def _():
    """No Curve.Offset in rhino3dm — implement with frame normals."""
    c = r3.Circle(r3.Point3d(0, 0, 0), 3.0).ToNurbsCurve()
    d = c.Domain
    off_pts = []
    n = 64
    for i in range(n + 1):
        t = d.T0 + (d.T1 - d.T0) * i / n
        ok, frame = c.FrameAt(t)
        assert ok
        p = c.PointAt(t)
        # offset left of tangent: normal = ZAxis x Tangent? use plane YAxis
        off_pts.append(r3.Point3d(
            p.X + frame.YAxis.X * 2.0,
            p.Y + frame.YAxis.Y * 2.0,
            p.Z + frame.YAxis.Z * 2.0))
    # offset of a circle by 2 -> radius 5 (or 1 depending on side)
    r = (off_pts[0].X ** 2 + off_pts[0].Y ** 2) ** 0.5
    assert abs(r - 5.0) < 1e-6 or abs(r - 1.0) < 1e-6, f"offset radius {r}"


# ---------------------------------------------------------------- solids

@check("Box -> Brep")
def _():
    box = r3.Box(r3.BoundingBox(r3.Point3d(0, 0, 0), r3.Point3d(4, 2, 1)))
    b = r3.Brep.CreateFromBox(box)
    assert b is not None and b.IsValid and len(b.Faces) == 6


@check("Sphere -> Brep")
def _():
    s = r3.Sphere(r3.Point3d(0, 0, 0), 3.0).ToBrep()
    assert s is not None and s.IsValid


@check("Cylinder (finite) -> Brep")
def _():
    cyl = r3.Cylinder(r3.Circle(r3.Point3d(0, 0, 0), 2.0), 5.0)
    b = cyl.ToBrep(True, True)
    assert b is not None and b.IsValid


@check("Cone via RevSurface (no Cone ctor)")
def _():
    profile = r3.LineCurve(r3.Point3d(2, 0, 0), r3.Point3d(0, 0, 5)).ToNurbsCurve()
    axis = r3.Line(r3.Point3d(0, 0, 0), r3.Point3d(0, 0, 10))
    rev = r3.RevSurface.Create(profile, axis, 0.0, 6.283185307179586)
    b = r3.Brep.CreateFromRevSurface(rev, False, True)
    assert b is not None and b.IsValid


@check("Extrude closed curve -> solid Brep")
def _():
    c = r3.Circle(r3.Point3d(0, 0, 0), 3.0).ToNurbsCurve()
    e = r3.Extrusion.Create(c, 5.0, True)
    b = e.ToBrep(False)
    assert b is not None and b.IsValid and b.IsSolid


@check("Revolve open profile -> Brep")
def _():
    profile = r3.LineCurve(r3.Point3d(2, 0, 0), r3.Point3d(4, 0, 6)).ToNurbsCurve()
    axis = r3.Line(r3.Point3d(0, 0, 0), r3.Point3d(0, 0, 10))
    rev = r3.RevSurface.Create(profile, axis, 0.0, 6.283185307179586)
    b = r3.Brep.CreateFromRevSurface(rev, False, False)
    assert b is not None and b.IsValid


@check("Loft (ruled surface through 2 curves)")
def _():
    c1 = r3.Circle(r3.Point3d(0, 0, 0), 4.0).ToNurbsCurve()
    c2 = r3.Circle(r3.Point3d(0, 0, 6), 2.0).ToNurbsCurve()
    loft = r3.NurbsSurface.CreateRuledSurface(c1, c2)
    assert loft is not None and loft.IsValid
    # mid-section radius should be ~3 (linear blend of 4 -> 2)
    du, dv = loft.Domain(0), loft.Domain(1)
    p = loft.PointAt((du.T0 + du.T1) / 2, (dv.T0 + dv.T1) / 2)
    r = (p.X ** 2 + p.Y ** 2) ** 0.5
    assert abs(r - 3.0) < 1e-6, f"loft mid radius {r}"


# ---------------------------------------------------------------- meshing

@check("Brep face grid sampling (mesh path)")
def _():
    box = r3.Box(r3.BoundingBox(r3.Point3d(0, 0, 0), r3.Point3d(2, 1, 1)))
    b = r3.Brep.CreateFromBox(box)
    total_pts = 0
    for face in b.Faces:
        grid = grid_sample(face, nu=8, nv=8)
        assert len(grid) == 9 and len(grid[0]) == 9
        total_pts += len(grid) * len(grid[0])
    assert total_pts > 100


@check("Brep face normals (shading)")
def _():
    s = r3.Sphere(r3.Point3d(0, 0, 0), 2.0).ToBrep()
    f = s.Faces[0]
    du, dv = f.Domain(0), f.Domain(1)
    n = f.NormalAt((du.T0 + du.T1) / 2, (dv.T0 + dv.T1) / 2)
    assert abs(n.Length() - 1.0) < 1e-6


@check("SubD exists")
def _():
    sd = r3.SubD()
    assert hasattr(sd, "Vertices") and hasattr(sd, "Faces")


# ---------------------------------------------------------------- file I/O

@check(".3dm round-trip with layers + names")
def _():
    model = r3.File3dm()
    layer = r3.Layer()
    layer.Name = "spike-layer"
    idx = model.Layers.Add(layer)
    assert isinstance(idx, int) and idx >= 0

    c = r3.Circle(r3.Point3d(0, 0, 0), 5.0).ToNurbsCurve()
    attr = r3.ObjectAttributes()
    attr.Name = "spike-circle"
    attr.LayerIndex = idx
    model.Objects.Add(c, attr)

    path = "/tmp/spike_test.3dm"
    assert model.Write(path, 8)
    loaded = r3.File3dm.Read(path)
    assert loaded is not None and len(loaded.Objects) == 1
    obj = list(loaded.Objects)[0]
    assert obj.Attributes.Name == "spike-circle"
    assert obj.Attributes.LayerIndex == idx


# ---------------------------------------------------------------- analysis

import os as _os  # noqa: E402
import sys as _sys  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
# Load analysis.py directly by path: importing the package would run its
# __init__, which imports ui -> bpy, and this suite must stay Blender-free.
_spec = _ilu.spec_from_file_location(
    "narwhal_analysis", _os.path.join(_ROOT, "narwhal3d", "analysis.py"))
analysis = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(analysis)


@check("analysis: line length exact")
def _():
    curve = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(10, 0, 0)).ToNurbsCurve()
    assert abs(analysis.curve_length(curve) - 10.0) < 1e-9


@check("analysis: circle circumference")
def _():
    curve = r3.Circle(r3.Point3d(0, 0, 0), 5.0).ToNurbsCurve()
    assert abs(analysis.curve_length(curve) - 2 * 3.141592653589793 * 5) < 1e-3


@check("analysis: closed curve area (shoelace)")
def _():
    curve = r3.Circle(r3.Point3d(0, 0, 0), 5.0).ToNurbsCurve()
    assert abs(analysis.closed_curve_area(curve) - 25 * 3.141592653589793) < 0.1
    # open curve -> None
    line = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(10, 0, 0)).ToNurbsCurve()
    assert analysis.closed_curve_area(line) is None


@check("analysis: tilted-plane circle area")
def _():
    import math as _math
    curve = r3.Circle(r3.Point3d(0, 0, 0), 5.0).ToNurbsCurve()
    # rotate the plane by an arbitrary angle about X and Y
    xform = r3.Transform.Rotation(_math.radians(40), r3.Vector3d(1, 0, 0),
                                  r3.Point3d(0, 0, 0))
    curve.Transform(xform)
    got = analysis.closed_curve_area(curve)
    assert abs(got - 25 * _math.pi) < 0.1, f"got {got}"


@check("analysis: box area + volume exact")
def _():
    brep = r3.Brep.CreateFromBox(
        r3.Box(r3.BoundingBox(r3.Point3d(0, 0, 0), r3.Point3d(2, 2, 2))))
    assert abs(analysis.surface_area(brep) - 24.0) < 1e-9
    assert abs(analysis.solid_volume(brep) - 8.0) < 1e-9


@check("analysis: sphere volume within tolerance")
def _():
    brep = r3.Sphere(r3.Point3d(0, 0, 0), 2.0).ToBrep()
    expected = 4 / 3 * 3.141592653589793 * 8
    got = analysis.solid_volume(brep)
    assert abs(got - expected) / expected < 0.01, f"got {got}"


@check("analysis: bounding box")
def _():
    brep = r3.Brep.CreateFromBox(
        r3.Box(r3.BoundingBox(r3.Point3d(1, -2, 3), r3.Point3d(4, 5, 6))))
    lo, hi = analysis.bounding_box(brep)
    assert (lo.X, lo.Y, lo.Z) == (1.0, -2.0, 3.0)
    assert (hi.X, hi.Y, hi.Z) == (4.0, 5.0, 6.0)


# ---------------------------------------------------------------- report

print(f"\n{'=' * 58}")
print(" rhino3dm spike — the reference CAD application-parity geometry check")
print(f"{'=' * 58}")
widths = max(len(n) for n, _, _ in results)
for name, status, detail in results:
    mark = "✓" if status == "PASS" else "✗"
    print(f" {mark} {name.ljust(widths)}  {detail}")

fails = [r for r in results if r[1] != "PASS"]
print(f"\n {len(results) - len(fails)}/{len(results)} passed")
sys.exit(1 if fails else 0)
