"""Sweep1 (a section along one rail) and Pipe (a round tube along a curve).

MEASURED (commands/sweep1.htm): a frame is built at stations along the rail and the
cross-section is oriented into each frame; the surface passes through the swept
sections. Two frame styles are worth having and are built here:

* **Freeform** (Rhino's default) -- "the cross-section rotates to maintain its angle
  to the rail throughout the sweep": a rotation-minimising frame (double-reflection
  RMF, Wang et al.), i.e. parallel transport with no artificial twist.
* **Roadlike** -- a fixed up-axis. The help's exact construction: ``x = tangent x up``,
  ``y = x x tangent``, ``z = tangent``. For a planar rail the default up is the rail
  plane normal; for a non-planar rail it is world Z (MEASURED, sweep1.htm).

Any other style raises with its own name.

Stations are evenly spaced by arc length through `arclength.parameter_at_length`, and
the surface is built by lofting the swept sections through `bridge.loft_brep`, which
now takes N sections) with the STRAIGHT style so the surface passes through EVERY
section. Between stations the surface is linear -- a sectioned/ruled approximation of
the smooth sweep that converges as the station count rises; the section count is sized
from the rail's turning so a curved rail reads smooth.
"""
import math

try:
    import rhino3dm as r3
except ImportError:  # pragma: no cover
    r3 = None

SWEEP_MAX_STATIONS = 96          # never flood the loft, however curved the rail
SWEEP_STATION_ANGLE_DEG = 5.0    # approximately one station per 5° of rail turning


def _arclength():
    try:
        from . import arclength
    except ImportError:
        import os
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import arclength
    return arclength


def _bridge():
    try:
        from . import bridge
    except ImportError:
        import os
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import bridge
    return bridge


# --- small vector helpers (tuples) ------------------------------------------
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _norm(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    n = _norm(a)
    if n < 1e-15:
        return None
    return (a[0] / n, a[1] / n, a[2] / n)


def _tan(rail, t):
    v = rail.TangentAt(t)
    return _unit((v.X, v.Y, v.Z))


def _pt(rail, t):
    p = rail.PointAt(t)
    return (p.X, p.Y, p.Z)


def _any_perp(z):
    """A unit vector perpendicular to `z`, chosen from the world axis least aligned
    with it (so the cross product never degenerates)."""
    ax = min(((abs(z[0]), (1.0, 0.0, 0.0)), (abs(z[1]), (0.0, 1.0, 0.0)),
              (abs(z[2]), (0.0, 0.0, 1.0))), key=lambda kv: kv[0])[1]
    return _unit(_cross(z, ax))


def _rail_up(rail, params):
    """The default Roadlike up-axis: the rail's plane normal if it is planar, else
    world Z (MEASURED, sweep1.htm)."""
    pts = [_pt(rail, t) for t in params]
    # Newell normal of the sampled polygon.
    nx = ny = nz = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0, z0 = pts[i]
        x1, y1, z1 = pts[(i + 1) % n]
        nx += (y0 - y1) * (z0 + z1)
        ny += (z0 - z1) * (x0 + x1)
        nz += (x0 - x1) * (y0 + y1)
    up = _unit((nx, ny, nz))
    return up if up is not None else (0.0, 0.0, 1.0)


def _default_stations(rail, params_probe=64):
    """Station count sized from the rail's TURNING: a straight rail needs 2, a curved
    one about one station per SWEEP_STATION_ANGLE_DEG of total turning."""
    try:
        if rail.IsLinear(1e-9):
            return 2
    except Exception:  # noqa: BLE001
        pass
    d = rail.Domain
    prev = None
    turning = 0.0
    for i in range(params_probe + 1):
        t = d.T0 + (d.T1 - d.T0) * i / params_probe
        z = _tan(rail, t)
        if z is None:
            continue
        if prev is not None:
            c = max(-1.0, min(1.0, _dot(prev, z)))
            turning += math.degrees(math.acos(c))
        prev = z
    n = int(math.ceil(turning / SWEEP_STATION_ANGLE_DEG)) + 1
    return max(2, min(SWEEP_MAX_STATIONS, n))


def _station_params(rail, n):
    al = _arclength()
    total = al.length_of(rail)
    d = rail.Domain
    out = []
    for i in range(n):
        s = total * i / (n - 1)
        if i == 0:
            out.append(d.T0)
        elif i == n - 1:
            out.append(d.T1)
        else:
            out.append(al.parameter_at_length(rail, s))
    return out


def _roadlike_frames(rail, params, up):
    frames = []
    for t in params:
        z = _tan(rail, t)
        if z is None:
            raise ValueError(f"sweep: the rail has no tangent at t={t:.4f}")
        x = _unit(_cross(z, up))
        if x is None:
            raise ValueError(
                "sweep (Roadlike): the rail tangent is parallel to the up axis, so "
                "the frame is under-defined -- choose a different up axis")
        y = _unit(_cross(x, z))
        frames.append((_pt(rail, t), x, y, z))
    return frames


def _freeform_frames(rail, params):
    """Rotation-minimising frames by the double-reflection method (Wang et al.)."""
    z0 = _tan(rail, params[0])
    if z0 is None:
        raise ValueError("sweep: the rail has no tangent at its start")
    x0 = _any_perp(z0)
    y0 = _unit(_cross(z0, x0))
    frames = [(_pt(rail, params[0]), x0, y0, z0)]
    for k in range(1, len(params)):
        p_prev = frames[-1][0]
        x_prev = frames[-1][1]
        z_prev = frames[-1][3]
        p_i = _pt(rail, params[k])
        z_i = _tan(rail, params[k])
        if z_i is None:
            z_i = z_prev
        v1 = _sub(p_i, p_prev)
        c1 = _dot(v1, v1)
        if c1 < 1e-18:
            x_i = x_prev
        else:
            rL = _sub(x_prev, _scale(v1, 2.0 / c1 * _dot(v1, x_prev)))
            tL = _sub(z_prev, _scale(v1, 2.0 / c1 * _dot(v1, z_prev)))
            v2 = _sub(z_i, tL)
            c2 = _dot(v2, v2)
            x_i = rL if c2 < 1e-18 else _sub(rL, _scale(v2, 2.0 / c2 * _dot(v2, rL)))
        x_i = _unit(x_i) or _any_perp(z_i)
        y_i = _unit(_cross(z_i, x_i))
        frames.append((p_i, x_i, y_i, z_i))
    return frames


def _frame_plane(frame):
    o, x, y, _z = frame
    return r3.Plane(r3.Point3d(*o), r3.Vector3d(*x), r3.Vector3d(*y))


def sweep1_sections(rail, section, style="Freeform", up=None, stations=None):
    """The cross-section curve oriented into the frame at each station along `rail`.

    Returns a list of NurbsCurve copies of `section`, one per station, each rigidly
    moved from the rail-start frame into that station's frame (so the section keeps
    its shape and its relationship to the rail). This is the frame half of the sweep;
    :func:`sweep1` lofts the result.
    """
    st = style.lower()
    if st not in ("freeform", "roadlike"):
        raise ValueError(
            f"sweep frame style '{style}' not implemented; only 'Freeform' "
            f"(rotation-minimising) and 'Roadlike' (fixed up-axis) are built")
    n = _default_stations(rail) if stations is None else int(stations)
    n = max(2, n)
    params = _station_params(rail, n)
    if st == "roadlike":
        axis = _rail_up(rail, params) if up is None else _unit((up[0], up[1], up[2]))
        if axis is None:
            raise ValueError("sweep (Roadlike): the up axis is degenerate")
        frames = _roadlike_frames(rail, params, axis)
    else:
        frames = _freeform_frames(rail, params)
    base = section if isinstance(section, r3.NurbsCurve) else section.ToNurbsCurve()
    plane0 = _frame_plane(frames[0])
    out = []
    for fr in frames:
        xf = r3.Transform.PlaneToPlane(plane0, _frame_plane(fr))
        sec = base.Duplicate()
        sec.Transform(xf)
        out.append(sec)
    return out


def sweep1(rail, section, style="Freeform", up=None, stations=None):
    """Sweep `section` along `rail`. Returns a Brep.

    The section is oriented into the frame at each station (`sweep1_sections`) and the
    swept sections are SKINNED -- interpolated across, so the surface passes through every
    station AND is smooth (degree 3) along the rail.

    THIS USED TO LOFT 'Straight' (degree 1 in V), which made a sweep along a CURVED rail a
    fan of FLAT STRIPS. A circle swept along a quarter-circle rail of radius 10 came back
    degree 2x1 with 18 strips, missing the true torus by 0.0114 units mid-strip. A
    straight-rail check cannot expose this because degree 1 is exact there. Curved surfaces
    must not be subdivided into flat strips, so 'Smooth' is the loft style.

    The stations are still SAMPLES of the rail, so the surface remains an approximation of
    the ideal swept surface -- but a SMOOTH one that passes through its samples, which is
    what Rhino's own tolerance-fitted sweep is. The approximation's deviation is measured.

    A CLOSED rail yields a geometrically closed surface automatically: its first and last
    stations coincide, so the first and last sections do too (use `Roadlike` with a
    fixed up for a clean seam -- a rotation-minimising frame has holonomy around a loop
    and would meet itself twisted). Raises ValueError for an unbuilt style.
    """
    st = str(style).lower()
    if st not in ("freeform", "roadlike"):
        sweep1_sections(rail, section, style=style, up=up, stations=2)   # raises by name
    # EXACT FIRST. OCCT's MakePipeShell sweeps the section itself: a circle along a quarter
    # arc is ONE TORUS face, exact to
    # 3.8e-15, where the skinned sweep below missed the ideal torus by 1.7e-5. It carries a
    # `sweep1` recipe, so it booleans and survives save/reload. An explicit `stations` asks
    # for the sampled construction, which is kept for that.
    if stations is None:
        exact = _sweep1_occt(rail, section, st, up)
        if exact is not None:
            return exact
    sections = sweep1_sections(rail, section, style=style, up=up, stations=stations)
    return _bridge().loft_brep(sections, style="Smooth")


def _sweep1_occt(rail, section, style, up):
    """The OCCT sweep, or None when the kernel or a curve recipe is unavailable.
    Freeform = corrected Frenet (rotation-minimising); Roadlike = a constant binormal, which
    IS sweep1.htm's construction (x = tangent x up, y = x x tangent: y is `up` projected
    perpendicular to the tangent, OCCT's binormal)."""
    b = _bridge()
    occt = b._occt()
    if occt is None:
        return None
    rail_r, sec_r = b.curve_recipe(rail), b.curve_recipe(section)
    if rail_r is None or sec_r is None:
        return None
    # ONLY A PLANAR RAIL. For a rectangle swept along a non-planar cubic, OCCT's frames
    # differ: corrected Frenet sits 5.7e-3 off
    # the converged rotation-minimising frame, and its constant-binormal law 2.8e-2 off
    # Rhino's Roadlike construction. On a planar rail both coincide with the sampled frames to the
    # sweep tolerance (6.7e-8), because the binormal is then the constant plane normal. A
    # non-planar rail keeps the sampled construction (which `loft_brep` gives an exact
    # recipe), rather than silently changing what Freeform/Roadlike mean.
    params = _station_params(rail, _default_stations(rail))
    normal = _rail_up(rail, params)
    try:
        planar = bool(rail.IsPlanar())
    except Exception:  # noqa: BLE001
        planar = False
    if not planar or normal is None:
        return None
    binormal = None
    if style == "roadlike":
        axis = normal if up is None else _unit((up[0], up[1], up[2]))
        if axis is None or abs(abs(_dot(axis, normal)) - 1.0) > 1e-9:
            return None              # an up that is not the plane normal: OCCT differs
        binormal = list(normal)
    recipe = {"kind": "sweep1", "rail": rail_r, "section": sec_r, "binormal": binormal}
    try:
        shape = occt.from_recipe(recipe)
        return b.occt_backed(shape, recipe)
    except Exception as e:  # noqa: BLE001
        b._decline(f"sweep1: {type(e).__name__}: {e}")
        return None


# ---------------------------------------------------------------------------
# Pipe -- a round tube along a curve. Sweep1 with a circular section and a cap.
# ---------------------------------------------------------------------------

def _perp_circle(rail, radius):
    """A circle of `radius` centred on the rail's START, in the plane PERPENDICULAR
    to the rail there. A circle is rotationally symmetric, so any perpendicular plane
    gives the same pipe; the sweep then keeps it perpendicular along the rail."""
    if radius <= 0:
        raise ValueError(f"pipe radius must be positive (got {radius})")
    t0 = rail.Domain.T0
    z = _tan(rail, t0)
    if z is None:
        raise ValueError("pipe: the rail has no tangent at its start")
    o = _pt(rail, t0)
    x = _any_perp(z)
    y = _unit(_cross(z, x))
    base = r3.Circle(float(radius)).ToNurbsCurve()           # XY plane, centre origin
    xy = r3.Plane(r3.Point3d(0, 0, 0), r3.Vector3d(1, 0, 0), r3.Vector3d(0, 1, 0))
    target = r3.Plane(r3.Point3d(*o), r3.Vector3d(*x), r3.Vector3d(*y))
    base.Transform(r3.Transform.PlaneToPlane(xy, target))
    return base


def pipe_sections(rail, radius, style="Freeform", up=None, stations=None):
    """The circular sections of a pipe along `rail` (the frame half; `pipe` lofts
    and caps them). Parallels :func:`sweep1_sections`."""
    return sweep1_sections(rail, _perp_circle(rail, radius), style=style, up=up,
                           stations=stations)


def pipe(rail, radius, cap="None", style="Freeform", up=None, stations=None):
    """A round tube of `radius` along `rail` -- Sweep1 with a circular section.

    `cap` (MEASURED, pipe.htm): ``None`` leaves the ends open; ``Flat`` closes each
    planar end into a solid via `bridge.cap_planar_holes` (needs bpy); ``Round``
    (a hemispherical cap) is NOT implemented and raises by name. A `Flat`-capped pipe
    is a closed solid (`IsSolid == True`); `None` is not. Radius is constant (a
    tapered start/end radius is a separate Rhino option, not built here).
    """
    # ANALYTIC FIRST. OCCT sweeps a circle along a rail into a single exact surface --
    # MEASURED, a quarter-circle rail gives one TORUS face, and capped its volume matches an
    # exact quarter-torus to 1.4e-14 %. The skinned loft below is smooth but can never
    # be exact, because its stations are samples: it missed the ideal torus by 3.8e-05.
    #
    # Only the default frame styles go this way. `Roadlike` with an explicit `up`, or a caller
    # supplying its own `stations`, asks for a specific framing that MakePipe does not take, so
    # those keep the loft route rather than silently ignoring the request.
    cp = str(cap).lower()
    if up is None and stations is None and str(style).lower() == "freeform":
        if cp in ("none", "flat"):
            analytic = _bridge().pipe_occt(rail, radius, cap=(cp == "flat"))
            if analytic is not None:
                return analytic
    brep = sweep1(rail, _perp_circle(rail, radius), style=style, up=up,
                  stations=stations)
    if cp == "none":
        return brep
    if cp == "flat":
        return _bridge().cap_planar_holes(brep).brep
    if cp == "round":
        raise ValueError(
            "pipe Cap=Round (hemispherical) not implemented; only 'None' and 'Flat' "
            "are built")
    raise ValueError(f"pipe cap '{cap}' unknown; use 'None', 'Flat' or 'Round'")


# ---------------------------------------------------------------------------
# Sweep2 -- a section along two rails. The two-rail correspondence problem.
# ---------------------------------------------------------------------------

def _rail_point_at_fraction(rail, f):
    """The point at NORMALISED ARC-LENGTH fraction `f` (0..1) along `rail` -- NOT
    parameter `f`, which on a non-uniform curve lands somewhere else. This is the
    correspondence that keeps the two rails in step."""
    al = _arclength()
    d = rail.Domain
    if f <= 0.0:
        return _pt(rail, d.T0)
    if f >= 1.0:
        return _pt(rail, d.T1)
    total = al.length_of(rail)
    return _pt(rail, al.parameter_at_length(rail, f * total))


def _transport_up(up, direction):
    """`up` made perpendicular to `direction` (minimal twist), or a fresh perpendicular
    if it collapses."""
    d = _unit(direction)
    if d is None:
        return (0.0, 0.0, 1.0)
    perp = _sub(up, _scale(d, _dot(up, d)))
    return _unit(perp) or _any_perp(d)


def sweep2_sections(rail_a, rail_b, section, stations=None):
    """The cross-section scaled+oriented to SPAN the two rails at each station.

    At station `i` the two rails are sampled at the SAME normalised arc-length
    fraction (`_rail_point_at_fraction`) giving `A_i` on rail A and `B_i` on rail B;
    the section is mapped by a SIMILARITY (uniform scale about its start, then a
    rigid frame change) so its start lands on `A_i` and its end on `B_i`. The uniform
    scale is Sweep2's DEFAULT -- the section scales in both height and width with the
    rail spacing (sweep2.htm). Returns the list of section curves; `sweep2` lofts them.
    """
    base = section if isinstance(section, r3.NurbsCurve) else section.ToNurbsCurve()
    p0 = base.PointAtStart
    p1 = base.PointAtEnd
    P0 = (p0.X, p0.Y, p0.Z)
    P1 = (p1.X, p1.Y, p1.Z)
    base_len = _norm(_sub(P1, P0))
    if base_len < 1e-9:
        raise ValueError(
            "sweep2: the section's endpoints coincide, so it cannot span two rails "
            "(its start follows rail A and its end follows rail B)")
    dir_base = _unit(_sub(P1, P0))
    up_base = _any_perp(dir_base)
    plane0 = r3.Plane(r3.Point3d(*P0), r3.Vector3d(*dir_base), r3.Vector3d(*up_base))

    n = stations if stations is not None else max(_default_stations(rail_a),
                                                  _default_stations(rail_b))
    n = max(2, int(n))
    out = []
    for i in range(n):
        f = i / (n - 1)
        a = _rail_point_at_fraction(rail_a, f)
        b = _rail_point_at_fraction(rail_b, f)
        span = _sub(b, a)
        length = _norm(span)
        if length < 1e-9:
            raise ValueError(
                f"sweep2: the two rails meet at station {i} (fraction {f:.3f}), so the "
                f"section has zero width there")
        dir_i = _unit(span)
        up_i = _transport_up(up_base, dir_i)
        plane_i = r3.Plane(r3.Point3d(*a), r3.Vector3d(*dir_i), r3.Vector3d(*up_i))
        xf = r3.Transform.Multiply(r3.Transform.PlaneToPlane(plane0, plane_i),
                                   r3.Transform.Scale(r3.Point3d(*P0),
                                                      length / base_len))
        sec = base.Duplicate()
        sec.Transform(xf)
        out.append(sec)
    return out


def sweep2(rail_a, rail_b, section, stations=None, maintain_height=False,
           closed=False):
    """Sweep `section` along TWO rails. Returns a Brep.

    The section's START follows `rail_a` and its END follows `rail_b`; both rails are
    stationed by NORMALISED ARC LENGTH so they stay in correspondence, and the section
    is scaled (in both dimensions -- Sweep2's default) to span the rails at each
    station. The swept sections are lofted (STRAIGHT, so the surface passes through
    every one).

    Unbuilt options raise with their own name: `maintain_height=True` (scale width
    only -- sweep2.htm's non-default) and `closed=True` are not implemented.
    """
    if maintain_height:
        raise ValueError(
            "sweep2 maintain_height=True not implemented; the default scales the "
            "section in both height and width")
    if closed:
        raise ValueError("sweep2 closed=True not implemented")
    sections = sweep2_sections(rail_a, rail_b, section, stations=stations)
    # SKINNED, not ruled: lofting 'Straight' made a sweep along curved rails a fan of flat
    # strips. `loft_brep` records the result's own faces as its recipe (`brep_data`), so a
    # Sweep2 now has an OCCT master and booleans exactly. OCCT has no two-rail sweep that
    # SCALES the section to span the rails (MakePipeShell's auxiliary spine only orients),
    # so the geometry stays this construction.
    return _bridge().loft_brep(sections, style="Smooth")
