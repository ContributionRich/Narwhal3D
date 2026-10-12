"""Geometry bridge: rhino3dm (McNeel's kernel) <-> Blender bmesh.

This is the heart of "same math as the reference CAD application 8". All geometry is authored with
rhino3dm (identical NURBS math to the reference CAD application), then tessellated by sampling into
Blender meshes for the viewport.

Pure-rhino3dm helpers (sample_*) are testable without Blender; *_to_bmesh need bpy.
"""
from __future__ import annotations

import importlib
import importlib.util
from collections import OrderedDict

import math

import rhino3dm as r3


# ---------------------------------------------------------------------------
# Pure rhino3dm sampling (no bpy needed)
# ---------------------------------------------------------------------------

def sample_curve(curve, n: int = 64):
    """Sample a curve to a list of Point3d along its domain."""
    d = curve.Domain
    return [curve.PointAt(d.T0 + (d.T1 - d.T0) * i / n) for i in range(n + 1)]


def distance_to_curve(curve, point, n: int = 256):
    """The shortest distance from `point` to `curve`, by sampling.

    `point` is a world (x, y, z) tuple. rhino3dm has no `NearestPoint` on
    curves, so we sample densely and take the closest point -- the same
    sampling `sample_curve_offset` relies on. Pure, no bpy.
    """
    import math

    px, py, pz = point
    best = math.inf
    for p in sample_curve(curve, n):
        best = min(best, math.dist((px, py, pz), (p.X, p.Y, p.Z)))
    return best


def sample_curve_offset(curve, distance: float, n: int = 64):
    """Offset a curve by `distance` using its frame normals.

    rhino3dm has no Curve.Offset, so we offset each sampled point along the
    frame's Y axis (left of travel). Good enough for CAD-style Offset MVP.
    """
    d = curve.Domain
    out = []
    for i in range(n + 1):
        t = d.T0 + (d.T1 - d.T0) * i / n
        ok, frame = curve.FrameAt(t)
        if not ok:
            continue
        p = curve.PointAt(t)
        out.append(r3.Point3d(
            p.X + frame.YAxis.X * distance,
            p.Y + frame.YAxis.Y * distance,
            p.Z + frame.YAxis.Z * distance))
    return out


def _uniform_params(dom, n):
    return [dom.T0 + (dom.T1 - dom.T0) * i / n for i in range(n + 1)]


def _grid_points(surface, us, vs):
    """Surface points at the parameter lists `us` x `vs` (rows of Point3d)."""
    return [[surface.PointAt(u, v) for v in vs] for u in us]


def _grid_normals(surface, us, vs):
    """Surface normals at `us` x `vs`, matching `_grid_points` layout."""
    grid = []
    for u in us:
        row = []
        for v in vs:
            try:
                row.append(surface.NormalAt(u, v))
            except Exception:  # noqa: BLE001
                row.append(r3.Vector3d(0, 0, 1))
        grid.append(row)
    return grid


def sample_surface_grid(surface, nu: int = 32, nv: int = 32):
    """Grid-sample a surface (or brep face) domain to rows of Point3d, UNIFORMLY.

    Kept uniform for the boolean input path (which must stay byte-identical). The
    DISPLAY path uses the adaptive, knot-aware parameter lists from
    `_surface_axis_params` instead -- see `surface_to_bmesh`.
    """
    du, dv = surface.Domain(0), surface.Domain(1)
    return _grid_points(surface, _uniform_params(du, nu), _uniform_params(dv, nv))


def sample_surface_normals(surface, nu: int = 32, nv: int = 32):
    """Grid-sample surface normals matching sample_surface_grid layout (uniform)."""
    du, dv = surface.Domain(0), surface.Domain(1)
    return _grid_normals(surface, _uniform_params(du, nu), _uniform_params(dv, nv))


# ---------------------------------------------------------------------------
# bmesh assembly (needs bpy / bmesh)
# ---------------------------------------------------------------------------

def _new_bmesh():
    import bmesh
    return bmesh.new()


def curve_to_bmesh(curve, n: int = 64):
    """A curve as an edge chain in a fresh bmesh, at a FIXED segment count.

    This is the fixed-density primitive. The VIEWPORT path uses
    ``curve_display_bmesh`` instead, which sizes the count to a chord tolerance
     and draws polylines through their exact corners.
    """
    bm = _new_bmesh()
    pts = sample_curve(curve, n)
    verts = [bm.verts.new((p.X, p.Y, p.Z)) for p in pts]
    for a, b in zip(verts[:-1], verts[1:]):
        bm.edges.new((a, b))
    return bm


# Chord tolerance in document units for displaying smooth curves. A value of 0.01
# gives roughly 50 segments around a radius-5 circle, scales with object size, and
# remains bounded by ``curve_segments_for``.
CURVE_CHORD_TOL = 0.01

#: - the per-direction ceiling on an ADAPTIVE surface grid. A
#: degenerate (near-zero) tolerance would otherwise drive `curve_segments_for` to
#: its own 1024 maximum in both directions and ask the viewport for ~10^6 quads;
#: 200 keeps even the worst case to 200x200 = 40k, which draws a radius-5 cylinder
#: smoothly many times over. The minimum is 1 (a plane is two triangles).
SURFACE_MAX_SEGMENTS = 200


def _curve_length_estimate(curve, samples: int = 64) -> float:
    """Curve length for sizing the display tessellation, EXACT to tolerance.

    Was a 64-chord sum that under-estimated a curved input (and disagreed with
    `analysis.curve_length`'s 1024-chord version). Now delegates to
    `arclength.length_of` (Gauss-Legendre per knot span). A longer, more accurate
    length means a slightly higher `curve_segments_for` count -- never lower --
    so the display cannot regress to coarser. `samples` is kept for signature
    compatibility; the chord sum survives only as the fallback when the exact
    route raises.
    """
    try:
        from . import arclength
    except ImportError:  # support direct-file loading outside the package
        import os as _os
        import sys as _sys
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        import arclength
    try:
        return arclength.length_of(curve)
    except Exception:  # noqa: BLE001
        pts = sample_curve(curve, samples)
        total = 0.0
        for a, b in zip(pts[:-1], pts[1:]):
            total += _dist3((a.X, a.Y, a.Z), (b.X, b.Y, b.Z))
        return total


def curve_display_segments(curve, tolerance: float = CURVE_CHORD_TOL) -> int:
    """Adaptive display segment count for a smooth curve: exactly the number of
    segments `_curve_display_points` walks it with (see `_display_params`)."""
    return max(1, len(_display_params(curve, tolerance)) - 1)


def _tess():
    try:
        from . import tessellate
    except ImportError:  # support direct-file loading outside the package
        import tessellate
    return tessellate


def _knot_groups(curve, lo, hi):
    """[value, multiplicity] of every DISTINCT interior knot of a NurbsCurve, plus its
    degree; ([], 0) for a curve with no knot list (an ArcCurve, a brep edge). One read
    of the knot vector serves both the corner list and the probe sizing."""
    knots = getattr(curve, "Knots", None)
    if knots is None:
        return [], 0
    try:
        vals = knots.ToList()
        degree = int(curve.Degree)
    except Exception:  # noqa: BLE001
        return [], 0
    eps = (hi - lo) * 1e-9
    groups = []
    for v in sorted(vals):
        if lo + eps < v < hi - eps:
            if groups and abs(v - groups[-1][0]) <= eps:
                groups[-1][1] += 1
            else:
                groups.append([v, 1])
    return groups, degree


#: - probe samples per knot span when sizing a display span (a span is one
#: polynomial piece, so 4 intervals see its bends), the probe for an ArcCurve (uniform
#: in angle, so 8 x 45 degrees covers a full circle), and for any other curve with no
#: knot list (a brep edge's proxy), which may hide many spans. Derivative evaluation
#: dominates this path on large files, so probe counts stay deliberately bounded.
PROBE_PER_SPAN = 4
PROBE_ARC = 8
PROBE_NO_KNOTS = 24
PROBE_MAX = 512


def _display_params(curve, tolerance: float):
    """Display sample parameters for ONE smooth (non-polyline, non-PolyCurve) curve.

    The curve is cut into PIECES at its genuine C0 corners (`_interior_knot_params`,
    the full-multiplicity knots -- ), so every corner is a sample exactly and is
    never chorded away. Each piece is probed uniformly in parameter (`PROBE_PER_SPAN`
    per knot span) and the probe's real turn and curvature size it
    (`tessellate.piece_needs`):

    * a one-span piece (almost every piece of a real file) is walked uniformly with
      `piece_segments_for`'s count, aligned to the probe so its samples are reused;
    * a many-span (or degree > 3) piece places its vertices evenly in NEED
      (`need_positions`), so a gentle stretch is not drawn at the density of the
      piece's tightest bend, then bisects any segment whose midpoint is off by more
      than the tolerance (`_need_spaced`).

    Simple knots are not forced.
    """
    return _display_samples(curve, tolerance)[0]


def _display_samples(curve, tolerance: float):
    """(params, points) of `_display_params`. The probe samples are REUSED as display
    vertices wherever the count lines up (n divides the probe count), so a piece that
    needs 1, 2, 4 or 8 segments costs no evaluation beyond its probe -- rhino3dm
    evaluations dominate the cost on large files."""
    tess = _tess()
    d = curve.Domain
    lo, hi = d.T0, d.T1
    if not hi > lo:
        p = curve.PointAt(lo)
        return [lo], [(p.X, p.Y, p.Z)]
    groups, degree = _knot_groups(curve, lo, hi)
    corners = [v for v, m in groups if degree >= 1 and m >= degree]
    breaks = [lo] + corners + [hi]
    params, points = [], []
    for a, b in zip(breaks[:-1], breaks[1:]):
        spans = 1 + sum(1 for v, _m in groups if a < v < b) if degree else 1
        if degree:
            k = min(PROBE_MAX, PROBE_PER_SPAN * spans)
        elif isinstance(curve, r3.ArcCurve):
            k = PROBE_ARC
        else:
            k = PROBE_NO_KNOTS
        pts, tans = [], []
        for j in range(k + 1):
            t = b if j == k else a + (b - a) * j / k   # the ends EXACTLY (corners)
            p, v = curve.DerivativeAt(t, 1)            # point, 1st derivative
            pts.append((p.X, p.Y, p.Z))
            tans.append((v.X, v.Y, v.Z))
        start = 0 if not params else 1          # a shared corner is one vertex
        if spans > 1 or degree > 3:
            # space by NEED, so a gentle stretch is not drawn at the density of the
            # piece's tightest bend ('s concern), then verify
            sub_t, sub_p = _need_spaced(curve, a, b, k, pts, tans, tolerance, tess)
            params.extend(sub_t[start:])
            points.extend(sub_p[start:])
            continue
        n = tess.align_count(tess.piece_segments_for(pts, tans, tolerance), k)
        if k % n == 0:                          # every vertex is a probe sample
            step = k // n
            for i in range(start, n + 1):
                params.append(a + (b - a) * i / n)
                points.append(pts[i * step])
        else:                                   # n = m*k: every m-th vertex is one
            m = n // k
            for i in range(start, n + 1):
                params.append(a + (b - a) * i / n)
                if i % m == 0:
                    points.append(pts[i // m])
                else:
                    p = curve.PointAt(a + (b - a) * i / n)
                    points.append((p.X, p.Y, p.Z))
        params[-1] = b                          # the corner itself, to machine precision
    return params, points


#: - bisection rounds for `_need_spaced`'s verify pass; each round can halve a
#: segment's error by ~4x, so 4 rounds absorb a 256x under-estimate.
REFINE_ROUNDS = 4


def _need_spaced(curve, a, b, k, pts, tans, tolerance, tess):
    """(params, points) over [a, b] for a MANY-SPAN or HIGH-DEGREE piece: vertices
    spaced evenly in need (`tess.need_positions`), then VERIFIED -- every segment whose
    true midpoint is farther than `tolerance` from its chord is bisected, up to
    `REFINE_ROUNDS` times. The probe estimates need from a few samples per span; that is
    reliable for one-span degree <= 3 pieces (worst 1.08x tol over 150 random
    curves) but not here (worst observed 2.1x tolerance over random multi-span and
    degree-5 curves), so these pieces pay one evaluation per segment."""
    us = tess.need_positions(tess.piece_needs(pts, tans, tolerance))
    ts = [a] + [a + (b - a) * u / k for u in us] + [b]
    xs = [pts[0]]
    for t in ts[1:-1]:
        p = curve.PointAt(t)
        xs.append((p.X, p.Y, p.Z))
    xs.append(pts[k])
    if tolerance <= 0.0:
        return ts, xs
    for _round in range(REFINE_ROUNDS):
        nt, nx, split = [ts[0]], [xs[0]], False
        for i in range(len(ts) - 1):
            tm = 0.5 * (ts[i] + ts[i + 1])
            p = curve.PointAt(tm)
            m = (p.X, p.Y, p.Z)
            if tess.point_segment_distance(m, xs[i], xs[i + 1]) > tolerance:
                nt.append(tm)
                nx.append(m)
                split = True
            nt.append(ts[i + 1])
            nx.append(xs[i + 1])
        ts, xs = nt, nx
        if not split:
            break
    return ts, xs


def display_tolerance(curve, tolerance: float = CURVE_CHORD_TOL) -> float:
    """The DEFAULT display chord tolerance for `curve`: `tolerance`, relaxed for a
    large curve to `tessellate.CURVE_REL_CHORD` of its bounding-box diagonal (-
    0.5 px when the curve fills a 2000 px view; see that constant)."""
    try:
        bb = curve.GetBoundingBox()
        size = _dist3(_xyz(bb.Min), _xyz(bb.Max))
    except Exception:  # noqa: BLE001
        return tolerance
    return _tess().relative_tolerance(size, tolerance)


#: The segment tessellations of the PolyCurves edited in place lately (a grip drag):
#: ``{object name: {segment fingerprint: (tolerance, points)}}``, the most recent
#: `SEG_TESS_OBJECTS` objects. A grip belongs to only one or two segments, so caching
#: avoids re-tessellating every segment of a joined curve on each mouse move.
_SEG_TESS = {}
SEG_TESS_OBJECTS = 8
#: - a cached segment is reused when its tolerance is within this ratio of the one
#: asked for: `display_tolerance` follows the curve's size, which a drag changes a
#: little on every move, and a 25 % finer/coarser chord cannot be seen (it is a
#: fraction of a pixel by construction -- `tessellate.CURVE_REL_CHORD`). Display only.
SEG_TESS_TOL_RATIO = 1.25


def _seg_cache_for(name):
    """The (most-recently-used) segment cache of display object `name`."""
    c = _SEG_TESS.pop(name, None)
    if c is None:
        c = {}
    _SEG_TESS[name] = c
    while len(_SEG_TESS) > SEG_TESS_OBJECTS:
        _SEG_TESS.pop(next(iter(_SEG_TESS)))
    return c


def _curve_display_points(curve, tolerance: float, seg_cache=None):
    """3-D points for displaying `curve`, with every CORNER preserved.

    Three cases, in order:

    * a polyline (all straight runs) -> its EXACT vertices, so corners are never
      rounded off;
    * a PolyCurve (what `Join` produces from mixed straight + curved pieces) ->
      each segment is tessellated on its own and the pieces are concatenated, so
      the interior joints stay sharp. Uniform sampling across the whole curve
      would MISS those joints and "lose the shape" (a line+arc+line
      join dropped its two line/arc corners);
    * any other curve (circle, arc, ellipse, free NURBS) -> adaptive sampling sized
      by each piece's real turn and curvature (`_display_params`) that ALSO puts a
      vertex onto every FULL-MULTIPLICITY interior knot. A
      circle/arc is a single NurbsCurve with interior knots of multiplicity ==
      degree; moving a control point there makes a real C0 corner (49.64
      deg on a circle). The plain uniform walk lands on such a knot only for special
      counts, so it tessellated the corner away at ~75% of radii. Only
      full-multiplicity knots are forced, not every knot, so a smooth many-knot
      curve keeps its adaptive count.
    """
    verts3d = _polyline_vertices(curve)
    if verts3d is not None and len(verts3d) >= 2:
        return verts3d
    segments = _explode_to_segments(curve)
    if segments is not None and len(segments) > 1:
        pts = []
        fresh = {} if seg_cache is not None else None
        for seg in segments:
            seg_pts = fp = None
            if seg_cache is not None:
                # `seg_cache` (`_seg_cache_for`): a segment whose serialisation is
                # unchanged keeps its points; only the edited ones are re-tessellated
                fp = curve_fingerprint(seg)
                hit = seg_cache.get(fp) if fp is not None else None
                if (hit is not None and tolerance > 0.0
                        and 1.0 / SEG_TESS_TOL_RATIO <= hit[0] / tolerance
                        <= SEG_TESS_TOL_RATIO):
                    seg_pts = hit[1]
                    fresh[fp] = hit
            if seg_pts is None:
                seg_pts = _curve_display_points(seg, tolerance)
                if fresh is not None and fp is not None:
                    fresh[fp] = (tolerance, seg_pts)
            if pts and seg_pts and _dist3(pts[-1], seg_pts[0]) < 1e-9:
                seg_pts = seg_pts[1:]        # the shared joint is one vertex
            pts.extend(seg_pts)
        if seg_cache is not None:
            seg_cache.clear()                # only the segments the curve has NOW
            seg_cache.update(fresh)
        if len(pts) >= 2:
            return pts
    return _display_samples(curve, tolerance)[1]


def _interior_knot_params(curve):
    """Interior knot parameters that are a genuine C0 CORNER: distinct knots STRICTLY
    inside a NurbsCurve's domain whose MULTIPLICITY reaches the curve's degree.

    A NURBS curve is only C0 -- a tangent discontinuity, a real corner -- where an
    interior knot has multiplicity == degree ("full multiplicity"). A circle/arc is
    one NurbsCurve with such knots at its quadrant junctions; moving the control point
    there makes a visible kink (moving a circle's on-axis CV lands a sample
    on the knot to 1.66e-9 and a 12.6 deg corner). Forcing a display vertex onto each
    full-multiplicity knot is what keeps that corner from being chorded away.

    MULTIPLICITY IS THE DISCRIMINATOR (correcting 's superset): a SIMPLE
    interior knot (multiplicity < degree) is still C1+, i.e. SMOOTH, so it is NOT
    forced -- a smooth free-form NURBS with many simple knots keeps its adaptive count
    instead of gaining a vertex per knot, and a smooth circle's full-mult quadrant
    knots are forced but, lying on a smooth curve, add no spurious corner. Non-
    NurbsCurve inputs (a line, an arc-curve) expose no knot list and return [].
    """
    knots = getattr(curve, "Knots", None)
    if knots is None:
        return []
    try:
        vals = list(knots.ToList())
        degree = int(curve.Degree)
        d = curve.Domain
        lo, hi = d.T0, d.T1
    except Exception:  # noqa: BLE001
        return []
    if hi <= lo or degree < 1:
        return []
    eps = (hi - lo) * 1e-9
    interior = sorted(v for v in vals if lo + eps < v < hi - eps)
    # Group equal knots to count multiplicity, but keep the EXACT knot value (not a
    # rounded one) so the forced sample lands on the knot to machine precision.
    groups = []  # [value, count]
    for v in interior:
        if groups and abs(v - groups[-1][0]) <= eps:
            groups[-1][1] += 1
        else:
            groups.append([v, 1])
    return [val for val, m in groups if m >= degree]


def _sample_params(domain, n, extra):
    """n+1 uniform parameters over `domain`, merged with `extra` (sorted, deduped).

    With `extra` empty this is exactly the old uniform walk, so smooth curves are
    unchanged; with interior knots in `extra` it forces a sample onto each.
    """
    lo, hi = domain.T0, domain.T1
    params = [lo + (hi - lo) * i / n for i in range(n + 1)]
    params.extend(extra)
    params.sort()
    span_tol = (hi - lo) * 1e-9
    out = [params[0]]
    for t in params[1:]:
        if t - out[-1] > span_tol:
            out.append(t)
    return out


def _explode_to_segments(curve):
    """A PolyCurve's segment curves, else None.

    A join of mixed pieces is a `PolyCurve`; its `Explode` returns the segment
    curves (- the same call `explode_curve` uses). Only PolyCurves are
    exploded here. A plain NurbsCurve's interior C0 corners (a control point moved
    at a multiplicity>=degree knot) are NOT joints between segments -- they are
    handled by forcing a sample onto every interior knot (`_interior_knot_params`),
    not by exploding.
    """
    if not isinstance(curve, r3.PolyCurve):
        return None
    try:
        segs = curve.Explode()
    except Exception:  # noqa: BLE001
        return None
    return list(segs) if segs else None


def curve_curve_intersections(a, b, tol: float = 1e-6):
    """Thin wrapper over `intersect.curve_curve`.

    rhino3dm has no general curve/curve intersection -- only analytic primitives.
    The recursive-subdivision algorithm is pure (rhino3dm only, no bpy) and lives
    in ``intersect.py`` so it remains independent of Blender; this hands it the two
    rhino3dm curves. Returns a list of ``intersect.CurveX`` values, empty when
    disjoint and with an ``overlap`` entry for coincident curves. Raises
    ``ValueError`` when ``a is b``.
    """
    try:
        from . import intersect
    except ImportError:  # support direct-file loading outside the package
        import intersect
    return intersect.curve_curve(a, b, tol)


def curve_offset(curve, distance, normal=(0.0, 0.0, 1.0), corner="Sharp",
                 tolerance=1e-3):
    """Thin wrapper over `offset.curve_offset`.

    rhino3dm has no `Curve.Offset`. The pure module offsets a planar curve in the
    plane with the given `normal`: exact for lines/circles/arcs/polylines, a
    fitted approximation (with reported deviation) for general NURBS. Returns an
    `offset.OffsetResult`. Command wiring (Distance/Corner/BothSides/... options,
    quoted in `offset.py`) is the command layer's, in commands.py.
    """
    try:
        from . import offset as _offset
    except ImportError:  # support direct-file loading outside the package
        import offset as _offset
    return _offset.curve_offset(curve, distance, normal, corner, tolerance)


def offset_curve(curve, distance, plane_normal=None, corner="Sharp", tolerance=1e-3):
    """THE curve offset for the Offset command -- exact wherever an exact offset
    exists. Returns an `offset.OffsetResult` (`.curve`, `.exact`, `.max_deviation`,
    `.warning`). Raises ValueError, naming the reason, when there is no offset (an inward
    offset past a radius, or an unbuilt corner style).

    Compared at distance 1 with the previous sampled-and-fitted route
    (`sample_curve_offset` plus a fit through 65 points):

        input            old route max dev   this
        line             2e-16               0 (exact)
        L polyline       0.645               0 (exact; Sharp corner)
        closed rectangle 1.0                 2e-15 (exact)
        arc r5           4.5e-4              3e-15 (exact: a concentric rational arc)
        S-curve          0.948               4.6e-4 (approx, within tolerance)

    The old route's offset direction was `FrameAt.YAxis`. That is undefined at a polyline
    corner, and on a free-form curve it follows the CURVATURE, so it FLIPPED SIDE at an
    inflection (the S-curve went right, then left).

    * exact for lines, arcs, circles and polylines (`offset.curve_offset`); APPROXIMATE only
      for a free-form curve (`offset._offset_free_form_approx`, which says why). Then
      `.exact` is False and `.warning` starts with "approx";
    * the offset lies in the curve's own plane: `plane_normal` defaults to that plane's
      normal, turned to face world +Z (a curve in a vertical plane: +Y, then +X), and to
      world Z for a non-planar curve. the reference CAD application's InCPlane passes the CPlane normal;
    * SIDE, (the reference CAD application picks it with the mouse): a positive distance is to the LEFT of
      travel seen from +normal. For a CLOSED planar curve it is OUTWARD whichever way the
      curve runs, as the old command treated a circle.
    """
    # rhino3dm has no `TryGetPlane`; `analysis.curve_plane` is Newell's normal of
    # points ON the curve -- exact for a planar curve, and its sign is the curve's winding.
    winding = _analysis().curve_plane(curve)
    if plane_normal is not None:
        n = [float(c) for c in plane_normal]
    elif winding is not None:
        n = list(winding[1])
        for k in (2, 1, 0):
            if abs(n[k]) > 1e-12:
                if n[k] < 0:
                    n = [-c for c in n]
                break
    else:
        n = [0.0, 0.0, 1.0]               # a straight line, or a non-planar curve
    d = float(distance)
    if bool(curve.IsClosed) and winding is not None \
            and sum(a * b for a, b in zip(winding[1], n)) > 0.0:
        d = -d                            # counter-clockwise about n: left is INSIDE
    res = curve_offset(curve, d, tuple(n), corner, tolerance)
    if res.curve is None:
        raise ValueError(res.warning or "the offset is degenerate")
    if not res.exact:
        res.warning = ("approx: a free-form curve has no exact offset curve; fitted to "
                       f"max deviation {res.max_deviation:.2e}"
                       + (f" ({res.warning})" if res.warning else ""))
    return res


def split_curve(curve, cutters, tol: float = 1e-6):
    """Thin wrapper over `intersect.split_curve_by` (the reference CAD application's Split).

    Splits `curve` where the `cutters` cross it; returns pieces in order (a
    single-element list holding the original when nothing cuts). Closed curves
    are reopened/rejoined at the seam so N cuts give N pieces. Command wiring is
    the command layer's, in commands.py.
    """
    return _intersect().split_curve_by(curve, cutters, tol)


def trim_curve(curve, cutters, keep_point, tol: float = 1e-6):
    """Thin wrapper over `intersect.trim_curve_by` (the reference CAD application's Trim).

    Splits `curve` by `cutters` and discards the piece nearest `keep_point` (the
    clicked "trim away" part). Returns the remaining pieces.
    """
    return _intersect().trim_curve_by(curve, cutters, keep_point, tol)


def _intersect():
    try:
        from . import intersect
    except ImportError:  # support direct-file loading outside the package
        import intersect
    return intersect


def curve_plane_intersections(curve, origin, normal, tol: float = 1e-6):
    """Thin wrapper over `intersect.curve_plane` for exact curve/plane intersection.

    Returns a list of `intersect.CurveX`.
    """
    return _intersect().curve_plane(curve, origin, normal, tol)


def curve_surface_intersections(curve, surface, tol: float = 1e-6):
    """Thin wrapper over `intersect.curve_surface`. Approximate curve/
    general-surface intersection (tolerance stated in the module). Cannot decide
    trim membership -- rhino3dm exposes no trim pcurves; reported, not faked.
    """
    return _intersect().curve_surface(curve, surface, tol)


def surface_surface_intersections(a, b, tol: float = 1e-6):
    """Thin wrapper over `intersect.surface_surface`. Returns a list of
    `intersect.SurfaceX`, one per branch -- the curve(s) where two surfaces meet.
    Plane/plane is exact; every other pair is a marched polyline with its deviation
    reported. This is the curve `trim_surface` cuts along.
    """
    return _intersect().surface_surface(a, b, tol)


def _trim():
    try:
        from . import trim
    except ImportError:  # support direct-file loading outside the package
        import trim
    return trim


def trim_surface(surface, keep_point, field, nu: int = 64, nv: int = 64):
    """Trim `surface` along the signed `field`'s zero set, keeping the side
    containing `keep_point`. Returns a `trim.TrimResult`.

    `field` is ``f(point) -> signed scalar`` whose zero set is the cutter; build it
    with `trim.plane_field`/`cylinder_field`/`sphere_field` for a primitive cutter
    (re-exported as `bridge.trim.*`). This is a MESH trim, an APPROXIMATION at
    tessellation density -- rhino3dm has no Brep/Surface Split, so an exact NURBS
    trimmed face is impossible through this API. When
    the cutter misses the surface the whole surface comes back with `missed=True`,
    never an empty mesh. Command wiring is the command layer's; draw it with
    `trim_result_to_bmesh`.
    """
    return _trim().trim_surface_mesh(surface, keep_point, field, nu, nv)


def trim_result_to_bmesh(result):
    """Turn a `trim.TrimResult`'s vertices/faces into a bmesh for display."""
    bm = _new_bmesh()
    bverts = [bm.verts.new(v) for v in result.vertices]
    for tri in result.faces:
        try:
            bm.faces.new([bverts[i] for i in tri])
        except ValueError:
            # a duplicate face (shared cell edge) is harmless -- skip it
            pass
    return bm


def fillet_curves(curve_a, curve_b, radius, trim: bool = True):
    """Thin wrapper over `fillet.fillet_curves` (the reference CAD application's Fillet).

    Builds the arc of `radius` tangent to both curves in their corner: closed-form for
    lines and arcs, Newton on the exact distance for free-form curves. Raises ValueError
    for parallel lines, concentric circles, or a radius that does not fit. Returns a
    `fillet.CornerResult` (arc + the inputs trimmed to the tangent points when `trim`).

    this used to call the LINE-only `fillet_lines`, so the general kernel built was unreachable from here -- a line + circle fillet was refused.
    """
    return _fillet().fillet_curves(curve_a, curve_b, radius, trim)


def chamfer_curves(curve_a, curve_b, dist_a, dist_b, trim: bool = True):
    """Thin wrapper over `fillet.chamfer_curves` (the reference CAD application's Chamfer): exact for
    two lines, an arc-length walk-back for any other curve. (was `chamfer_lines`.)"""
    return _fillet().chamfer_curves(curve_a, curve_b, dist_a, dist_b, trim)


def _fillet():
    try:
        from . import fillet
    except ImportError:  # support direct-file loading outside the package
        import fillet
    return fillet


def curve_display_bmesh(curve, tolerance=None, seg_cache=None):
    """A curve as an edge-chain bmesh for the VIEWPORT, sized to a chord tolerance.

    `tolerance=None` (the document display, e.g. after Open) means
    `display_tolerance(curve)`: CURVE_CHORD_TOL, relaxed for a large curve so it is
    never drawn finer than ~0.5 px when it fills the view. An explicit tolerance (the
    zoom path, ) is honoured exactly.

    Smooth curves (circle, arc, ellipse, free NURBS) get an adaptive segment
    count from their length and the chord tolerance, so a big circle is smooth
    and a small one is not over-tessellated. Polylines are drawn through
    their exact vertices, and a joined PolyCurve is tessellated segment by
    segment, so no corner is ever rounded off (see `_curve_display_points`).
    """
    verts3d = None
    if tolerance is None:
        if _PREFETCH_DISPLAY:
            # computed by an Open's worker for a curve with this exact serialisation
            verts3d = _take_prefetched(_PREFETCH_DISPLAY, curve_fingerprint(curve))
        tolerance = display_tolerance(curve) if verts3d is None else None
    if verts3d is None:
        verts3d = _curve_display_points(curve, tolerance, seg_cache)
    bm = _new_bmesh()
    vs = [bm.verts.new(p) for p in verts3d]
    for a, b in zip(vs[:-1], vs[1:]):
        bm.edges.new((a, b))
    return bm


def surface_to_bmesh(surface, nu=None, nv=None):
    """Grid-sample any surface (NurbsSurface / RevSurface / face) to a mesh.

    With `nu`/`nv` left None (the DISPLAY default) the grid is ADAPTIVE and
    KNOT-AWARE: each direction's sample count comes from the chord
    tolerance via `_iso_segments` (a plane collapses to the minimum, a cylinder
    gets ~50 facets around but 1 up its straight height) and the sample lines fall
    on the surface's own knots. Passing explicit `nu`/`nv` forces a uniform grid,
    for callers that need a fixed one.
    """
    import bmesh

    bm = _new_bmesh()
    if nu is None or nv is None:
        us = _surface_axis_params(surface, 0, CURVE_CHORD_TOL)
        vs = _surface_axis_params(surface, 1, CURVE_CHORD_TOL)
    else:
        us = _uniform_params(surface.Domain(0), nu)
        vs = _uniform_params(surface.Domain(1), nv)
    _build_grid(bm, surface, us, vs)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    _shade_smooth(bm)
    return bm


def _dist3(a, b) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _surface_closed(surface, direction: int) -> bool:
    """True if the surface wraps on itself in `direction` (0=u, 1=v)."""
    try:
        return bool(surface.IsClosed(direction))
    except Exception:  # noqa: BLE001
        return False


def _build_grid(bm, surface, us, vs):
    """Add a grid of verts + quads for a surface patch over the parameter lists
    `us` x `vs`, WELDING closed seams.

    `us`/`vs` are the sample parameters in each direction (uniform for the boolean
    path, adaptive + knot-aware for display -- ). For backward compatibility
    an INT may be passed in either slot and is expanded to that many uniform
    samples. A cylinder wall is closed in u: its first and last columns of grid
    points sit at the same place. If they are separate vertices the smooth-shaded
    normals do not average across the seam, producing a visible shading line. Reusing
    the first column or row in a closed direction removes that seam.
    Positions are unchanged, so nothing that reads the mesh geometry is affected.
    """
    if isinstance(us, int):
        us = _uniform_params(surface.Domain(0), us)
    if isinstance(vs, int):
        vs = _uniform_params(surface.Domain(1), vs)
    grid_pts = _grid_points(surface, us, vs)
    grid_nrm = _grid_normals(surface, us, vs)
    nu, nv = len(us) - 1, len(vs) - 1
    closed_u = _surface_closed(surface, 0)
    closed_v = _surface_closed(surface, 1)
    vgrid = []
    for i in range(nu + 1):
        row = []
        for j in range(nv + 1):
            if closed_u and i == nu:
                v = vgrid[0][j]            # seam in u: reuse the first column
            elif closed_v and j == nv:
                v = row[0]                 # seam in v: reuse the first row
            else:
                p = grid_pts[i][j]
                nrm = grid_nrm[i][j]
                v = bm.verts.new((p.X, p.Y, p.Z))
                v.normal = (nrm.X, nrm.Y, nrm.Z)
            row.append(v)
        vgrid.append(row)
    for i in range(nu):
        for j in range(nv):
            try:
                bm.faces.new([vgrid[i][j], vgrid[i + 1][j],
                              vgrid[i + 1][j + 1], vgrid[i][j + 1]])
            except ValueError:
                pass


def _surface_interior_knots(surface, direction):
    """Distinct knot values STRICTLY inside a surface's domain in `direction`.

    These are the span breaks the adaptive grid must land a sample line on, the
    surface twin of `_interior_knot_params` for curves. A surface with no
    readable knot list (a primitive face) returns [] and the grid is then a plain
    adaptive division.
    """
    try:
        kl = surface.KnotsU if direction == 0 else surface.KnotsV
        vals = sorted(set(kl.ToList()))
    except Exception:  # noqa: BLE001
        return []
    dom = surface.Domain(direction)
    eps = (dom.T1 - dom.T0) * 1e-9
    return [v for v in vals if dom.T0 + eps < v < dom.T1 - eps]


def _surface_axis_params(surface, direction, tolerance):
    """Adaptive, KNOT-AWARE sample parameters along a surface direction.

    The count comes from `_iso_segments` (chord tolerance; a straight direction
    collapses to one), and `tessellate.distribute_params` lays that many segments
    down while forcing a sample line onto every interior knot -- so a non-uniform
    surface is sampled where it needs it, not uniformly in parameter.
    """
    try:
        from . import tessellate
    except ImportError:  # support direct-file loading outside the package
        import tessellate
    dom = surface.Domain(direction)
    total = _iso_segments(surface, direction, tolerance)
    total = max(1, min(total, SURFACE_MAX_SEGMENTS))   # clamp: never 0, never a flood
    knots = _surface_interior_knots(surface, direction)
    base = tessellate.distribute_params(knots, total, dom.T0, dom.T1)
    if total > 1:
        by_turn = _surface_turn_params(surface, direction, tolerance, knots, base)
        if by_turn is not None:
            return by_turn
    return base


#:  the most two neighbouring display facets of ONE smooth surface may
#: turn. (Blender's `extract_edge_factor_mesh` / `edge_factor_calc`): Shaded's
#: edge overlay, at `wireframe_threshold = 0`, draws every mesh edge whose two faces meet
#: at more than acos(0.995) = 5.73 deg -- so a coarser grid shows its facet joints as
#: lines. Surface placement starts from the curves' own 5 deg max-angle rule and verifies
#: to 5.5 deg (below Blender's 5.73 deg, but above a 72-facet circle's 5.27).
SURFACE_EDGE_SHOWN_DEG = 5.73
#: iso-curves probed across the other direction (both ends and the middle): a surface's
#: turn can differ along it (a loft), and each probe interval takes the worst of them
SURFACE_TURN_ISOS = 3
#: the verify pass splits a facet joint only above this -- under the 5.73 deg Blender
#: draws, above the 5.27 deg a 72-facet RATIONAL circle reaches (a cylinder's
#: joints vary around 5 deg because a rational circle is not uniform in parameter), so
#: a cylinder keeps today's 72 facets.
SURFACE_SPLIT_DEG = 5.5


def _surface_turn_params(surface, direction, tolerance, knots, base):
    """Place display samples along ``direction`` according to actual surface turn.

    The curve need-spacing rule is applied to surface iso-curves while retaining every
    interior knot as a sample line. If iso-curves cannot be read, the caller uses the
    length-based grid.

    Length-only sizing can undersample tight bends, allowing neighbouring display facets
    to meet above Blender's visible-edge threshold. Turn-based placement concentrates
    columns in those regions.

    The angle rule is applied UNCAPPED here (curves cap it below 4x the chord
    tolerance, `CURVE_ANGLE_MIN_CHORD`, because an over-fine polyline is invisible):
    on a surface every facet joint over 5.73 deg is drawn, however short.
    Display only: the geometry is never touched."""
    tess = _tess()
    try:
        dom, oth = surface.Domain(direction), surface.Domain(1 - direction)
        lo, hi = dom.T0, dom.T1
        if not hi > lo:
            return None
        k = max(PROBE_NO_KNOTS, min(PROBE_MAX, PROBE_PER_SPAN * (1 + len(knots))))
        isos = []
        for j in range(SURFACE_TURN_ISOS):
            c = oth.T0 + (oth.T1 - oth.T0) * j / max(1, SURFACE_TURN_ISOS - 1)
            iso = surface.IsoCurve(direction, c)
            if iso is None:
                return None
            isos.append(iso)
        # Keep today's grid when it already cannot show facet edges. In particular,
        # every cylinder (large or tiny) stays at its existing 72 facets -- and avoids
        # the derivative probes below. fixes a locally tight bend, not globally
        # raises surface density.
        if not _surface_turn_exceeds(isos, base, SURFACE_SPLIT_DEG):
            return base
        needs = None
        useful_isos = []
        for iso in isos:
            pts, tans = [], []
            for i in range(k + 1):
                p, v = iso.DerivativeAt(hi if i == k else lo + (hi - lo) * i / k, 1)
                pts.append((p.X, p.Y, p.Z))
                tans.append((v.X, v.Y, v.Z))
            if sum(_dist3(a, b) for a, b in zip(pts, pts[1:])) <= 1e-9:
                continue          # a POLE (a sphere's end iso is one point): no turn
            useful_isos.append(iso)
            chord = tess.piece_needs(pts, tans, tolerance)
            angle = tess.piece_needs(pts, tans, 0.0)        # the angle rule, uncapped
            these = [max(a, b) for a, b in zip(chord, angle)]
            needs = these if needs is None else [max(a, b) for a, b in zip(needs, these)]
    except Exception:  # noqa: BLE001
        return None
    if needs is None:
        return None
    us = tess.need_positions(needs, maximum=SURFACE_MAX_SEGMENTS)
    params = sorted({lo, hi} | set(knots) | {lo + (hi - lo) * u / k for u in us})
    try:
        return _surface_turn_verify(useful_isos, params)
    except Exception:  # noqa: BLE001
        return params


def _vector_turn(a, b):
    """Angle in radians between two vectors (0 if either is degenerate)."""
    cx = a[1] * b[2] - a[2] * b[1]
    cy = a[2] * b[0] - a[0] * b[2]
    cz = a[0] * b[1] - a[1] * b[0]
    dot = sum(x * y for x, y in zip(a, b))
    cross = math.sqrt(cx * cx + cy * cy + cz * cz)
    return 0.0 if cross == 0.0 and dot == 0.0 else math.atan2(cross, dot)


def _surface_turn_exceeds(isos, params, limit_deg):
    """Whether any neighbouring chords of `isos`, sampled at `params`, turn by more
    than `limit_deg` (including the seam of a closed iso-curve)."""
    limit = math.radians(limit_deg)
    for iso in isos:
        pts = [iso.PointAt(t) for t in params]
        dirs = [(b.X - a.X, b.Y - a.Y, b.Z - a.Z) for a, b in zip(pts, pts[1:])]
        pairs = list(zip(dirs, dirs[1:]))
        if getattr(iso, "IsClosed", False) and len(dirs) > 1:
            pairs.append((dirs[-1], dirs[0]))
        if any(_vector_turn(a, b) > limit for a, b in pairs):
            return True
    return False


def _surface_turn_verify(isos, params):
    """Bisect, up to `REFINE_ROUNDS` times, both segments at any vertex where an
    iso-curve's polyline turns by more than `SURFACE_SPLIT_DEG` -- the angle two
    neighbouring facets meet at. The probe estimates the turn from a few samples per
    span, which a tight bend between samples can defeat, for the same reason
    `_need_spaced` verifies curves."""
    limit = math.radians(SURFACE_SPLIT_DEG)
    for _round in range(REFINE_ROUNDS):
        split = set()
        for iso in isos:
            pts = [iso.PointAt(t) for t in params]
            dirs = [(b.X - a.X, b.Y - a.Y, b.Z - a.Z) for a, b in zip(pts, pts[1:])]
            for i in range(1, len(dirs)):
                if _vector_turn(dirs[i - 1], dirs[i]) > limit:
                    split.add(i - 1)
                    split.add(i)
            if (getattr(iso, "IsClosed", False) and len(dirs) > 1
                    and _vector_turn(dirs[-1], dirs[0]) > limit):
                split.add(len(dirs) - 1)
                split.add(0)
        if not split or len(params) + len(split) > SURFACE_MAX_SEGMENTS + 1:
            break
        params = sorted(set(params)
                        | {0.5 * (params[i] + params[i + 1]) for i in split})
    return params


def _iso_segments(face, direction: int, tolerance: float, probe: int = 24) -> int:
    """Adaptive grid density along a surface direction (for surfaces).

    Many segments where the surface CURVES, one where it is straight -- so a
    cylinder gets ~50 facets around (matching the circle curve) but only one up
    its straight height, instead of the old fixed 32x32. Keyed off the iso-curve's
    LENGTH and the same chord tolerance as curves.
    """
    try:
        dmain = face.Domain(direction)
        dother = face.Domain(1 - direction)
    except Exception:  # noqa: BLE001
        return 32
    mid = 0.5 * (dother.T0 + dother.T1)
    pts = []
    for k in range(probe + 1):
        t = dmain.T0 + (dmain.T1 - dmain.T0) * k / probe
        p = face.PointAt(t, mid) if direction == 0 else face.PointAt(mid, t)
        pts.append((p.X, p.Y, p.Z))
    length = 0.0
    for a, b in zip(pts, pts[1:]):
        length += _dist3(a, b)
    if length <= 1e-9:
        return 1
    if not _surface_closed(face, direction):
        chord = _dist3(pts[0], pts[-1])
        if (length - chord) <= 1e-4 * length:      # essentially straight
            return 1
    return _curve_segments_for_length(length, tolerance)


def _curve_segments_for_length(length: float, tolerance: float) -> int:
    try:
        from . import tessellate
    except ImportError:  # support direct-file loading outside the package
        import tessellate
    return tessellate.curve_segments_for(length, tolerance)


def _edge_display_segments(edge, tolerance: float, probe: int = PROBE_NO_KNOTS) -> int:
    """Adaptive segment count for one 3-D edge, from its real TURN and curvature
    (`tessellate.piece_segments_for`), not "a full circle of the same length" -- that
    model gave every curved edge >= 72 segments, while trimmed-cap ear clipping grows
    super-linearly with the segment count."""
    d = edge.Domain
    pts, tans = [], []
    for k in range(probe + 1):
        t = d.T0 + (d.T1 - d.T0) * k / probe
        p, v = edge.PointAt(t), edge.TangentAt(t)
        pts.append((p.X, p.Y, p.Z))
        tans.append((v.X, v.Y, v.Z))
    return _tess().piece_segments_for(pts, tans, tolerance)



# Below this dihedral angle neighbouring facets of ONE surface are shaded smooth.
# Kept for reference/measurement; our display meshes are unwelded per brep face,
# so hard creases BETWEEN faces already come for free (separate vertex islands).
SMOOTH_ANGLE_DEG = 40.0


def _shade_smooth(bm):
    """Smooth-shade the display mesh: the reason a curved surface looks round.

    Every face is marked smooth, so Blender shades it from normals averaged
    across neighbouring facets of the SAME connected surface. A cylinder wall is
    one connected grid, so it reads as round instead of a faceted prism.

    Hard edges need no special handling here: brep_to_bmesh builds each brep face
    as its OWN vertex island (faces are not welded together), so the crease
    between a box's sides, or between a cylinder wall and its flat cap, is already
    a discontinuity in the mesh -- smooth shading cannot bleed across it. A flat
    face (a box side, a triangulated cap) is coplanar, so smoothing it still
    reads flat. Shading flags only; no vertex moves, so booleans are unaffected.
    """
    for f in bm.faces:
        f.smooth = True


def _weld_shading(bm, sharp_deg: float = 40.0):
    """Make a MESH-display mesh shade smoothly WITHOUT losing its hard edges. `brep_to_bmesh` builds each brep face as its own vertex island -- correct
    for a primitive (one connected grid per analytic face) but fatal for a boolean
    result, which is thousands of LONE faces: no two share a vertex, so smooth shading
    can average nothing and a unioned cylinder reads faceted even though every face's
    smooth flag is set (2616 faces / 10464 verts = 4.0 per face, unwelded).

    Fix: WELD every coincident vertex so a smooth region becomes one connected island
    whose normals average, then SPLIT back only the edges whose two faces meet at more
    than `sharp_deg` -- a box corner, a cylinder wall meeting its flat cap -- so those
    creases stay a mesh discontinuity and keep their hard shading. `sharp_deg` is (40 deg): comfortably above the few-degree angle between adjacent facets of one
    smooth surface, comfortably below a 90 deg structural edge.

    Shading/topology only -- no vertex MOVES, so the geometry (bbox, volume) is
    unchanged; this touches the DISPLAY mesh, never the brep.
    """
    import bmesh

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    _weld(bm)
    thresh = math.radians(sharp_deg)
    sharp = []
    for e in bm.edges:
        if len(e.link_faces) == 2:
            try:
                if e.calc_face_angle() > thresh:
                    sharp.append(e)
            except Exception:  # noqa: BLE001
                pass
    if sharp:
        bmesh.ops.split_edges(bm, edges=sharp)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    for f in bm.faces:
        f.smooth = True


def _loop_points_3d(brep, loop, tolerance: float = CURVE_CHORD_TOL, tol: float = 1e-7):
    """Ordered 3-D boundary points of a brep face loop, from its edge curves.

    There is no parameter-space trim curve in rhino3dm, but each trim's
    `EdgeIndex` points at a real 3-D edge. We walk the loop's trims in order,
    sample each edge (respecting
    `IsReversed`), and concatenate, dropping duplicate join points and the
    closing duplicate. A linear edge needs one segment; a curved edge is sampled
    ADAPTIVELY from its length and `tolerance`, so a cap's circular boundary is
    as smooth as the circle curve that made it.
    """
    pts = []
    for ti in range(len(loop.Trims)):
        trim = loop.Trims[ti]
        ei = trim.EdgeIndex
        if ei < 0:                      # singular/seam trim: no 3-D edge
            continue
        edge = brep.Edges[ei]
        try:
            linear = edge.IsLinear()
        except Exception:  # noqa: BLE001
            linear = False
        steps = 1 if linear else _edge_display_segments(edge, tolerance)
        d = edge.Domain
        samples = [edge.PointAt(d.T0 + (d.T1 - d.T0) * k / steps)
                   for k in range(steps + 1)]
        if getattr(trim, "IsReversed", False):
            samples.reverse()
        for p in samples:
            xyz = (p.X, p.Y, p.Z)
            if pts and _dist3(pts[-1], xyz) < tol:
                continue
            pts.append(xyz)
    while len(pts) > 1 and _dist3(pts[0], pts[-1]) < tol:
        pts.pop()
    return pts


def _add_trimmed_planar_face(bm, brep, face) -> bool:
    """Triangulate one PLANAR face inside its trim loop. True iff it did.

    Returns False -- so the caller keeps the grid -- when the face has inner
    loops (a hole; not supported, see tessellate.py), when the boundary is
    degenerate, or when ear clipping cannot resolve the polygon. An unsupported
    face is thus never silently drawn wrong (the explicit-refusal policy).
    """
    try:
        from . import tessellate
    except ImportError:  # support direct-file loading outside the package
        import tessellate

    if len(face.Loops) != 1:            # holes not supported -> keep the grid
        return False
    pts3d = _loop_points_3d(brep, face.OuterLoop)
    if len(pts3d) < 3:
        return False
    du, dv = face.Domain(0), face.Domain(1)
    um, vm = (du.T0 + du.T1) / 2.0, (dv.T0 + dv.T1) / 2.0
    nrm = face.NormalAt(um, vm)
    u_axis, v_axis = tessellate.plane_basis((nrm.X, nrm.Y, nrm.Z))
    origin = pts3d[0]
    poly2d = tessellate.project_to_plane(pts3d, origin, u_axis, v_axis)
    tris = tessellate.ear_clip(poly2d)
    if len(tris) != len(pts3d) - 2:     # stalled / non-simple -> grid fallback
        return False
    verts = [bm.verts.new(p) for p in pts3d]
    for (i, j, k) in tris:
        try:
            bm.faces.new((verts[i], verts[j], verts[k]))
        except ValueError:              # a duplicate/degenerate tri
            pass
    return True


# Display-only cache for native imported Extrusions. The key contains no document
# object or OCCT master: it is a value fingerprint, so entries are safe to share across
# documents. Values are canonical mesh arrays only; every hit gets a fresh BMesh.
_EXTRUSION_DISPLAY_CACHE = OrderedDict()
_EXTRUSION_DISPLAY_CACHE_BYTES = 0
_EXTRUSION_DISPLAY_CACHE_MAX_BYTES = 64 * 1024 * 1024
_EXTRUSION_DISPLAY_CACHE_MAX_ENTRIES = 32
_EXTRUSION_DISPLAY_CACHE_HITS = 0
_EXTRUSION_DISPLAY_CACHE_MISSES = 0


def clear_extrusion_display_cache():
    """Clear viewport-only native-extrusion mesh reuse state and counters."""
    global _EXTRUSION_DISPLAY_CACHE_BYTES, _EXTRUSION_DISPLAY_CACHE_HITS
    global _EXTRUSION_DISPLAY_CACHE_MISSES
    _EXTRUSION_DISPLAY_CACHE.clear()
    _EXTRUSION_DISPLAY_CACHE_BYTES = 0
    _EXTRUSION_DISPLAY_CACHE_HITS = 0
    _EXTRUSION_DISPLAY_CACHE_MISSES = 0


def extrusion_display_cache_stats():
    """Return counters for the viewport cache (not document geometry)."""
    return {"entries": len(_EXTRUSION_DISPLAY_CACHE),
            "bytes": _EXTRUSION_DISPLAY_CACHE_BYTES,
            "hits": _EXTRUSION_DISPLAY_CACHE_HITS,
            "misses": _EXTRUSION_DISPLAY_CACHE_MISSES}


def _unit3(v):
    length = math.sqrt(sum(float(x) * float(x) for x in v))
    if length <= 1e-15:
        return None
    return tuple(float(x) / length for x in v)


def _dot3(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub3(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _extrusion_display_frame(extrusion):
    """Canonical profile-plane frame for a proper rigid display transform.

    The profile plane supplies the stable in-plane axes; the path supplies the third
    axis and scale. Reflections and non-uniform scales never enter this cache because
    the target is represented only by this orthonormal frame plus path length.
    """
    try:
        # GetProfilePlane is not reliable as a transform frame for every native
        # extrusion (transformed profile controls can leave that metadata
        # behind). Derive the frame from the actual first profile controls instead.
        profile = extrusion.Profile3d(0, 0).ToNurbsCurve()
        if len(profile.Points) < 2:
            return None
        origin = (profile.Points[0].X, profile.Points[0].Y, profile.Points[0].Z)
        z = _unit3((extrusion.PathTangent.X, extrusion.PathTangent.Y,
                    extrusion.PathTangent.Z))
        if z is None:
            return None
        x = None
        for point_index in range(1, len(profile.Points)):
            point = profile.Points[point_index]
            candidate = _sub3((point.X, point.Y, point.Z), origin)
            normal_part = _dot3(candidate, z)
            candidate = (candidate[0] - normal_part * z[0],
                         candidate[1] - normal_part * z[1],
                         candidate[2] - normal_part * z[2])
            x = _unit3(candidate)
            if x is not None:
                break
        if x is None:
            return None
        # A proper right-handed frame; mirrors/non-uniform scales cannot match this
        # orthonormal construction and therefore miss conservatively.
        y = (z[1] * x[2] - z[2] * x[1],
             z[2] * x[0] - z[0] * x[2],
             z[0] * x[1] - z[1] * x[0])
        height = math.dist((extrusion.PathStart.X, extrusion.PathStart.Y,
                            extrusion.PathStart.Z),
                           (extrusion.PathEnd.X, extrusion.PathEnd.Y,
                            extrusion.PathEnd.Z))
        if height <= 1e-15:
            return None
        return origin, x, y, z, height
    except Exception:  # noqa: BLE001
        return None


def _canonical_profile(extrusion, frame):
    """Value-only profile signature in the canonical profile-plane coordinates."""
    origin, x, y, z, _height = frame
    profiles = []
    for index in range(int(extrusion.ProfileCount)):
        curve = extrusion.Profile3d(index, 0).ToNurbsCurve()
        controls = []
        for point in curve.Points:
            d = _sub3((point.X, point.Y, point.Z), origin)
            controls.append((round(_dot3(d, x), 12), round(_dot3(d, y), 12),
                             round(_dot3(d, z), 12), round(float(point.W), 12)))
        profiles.append((int(curve.Degree), bool(curve.IsRational),
                         tuple(round(float(k), 12) for k in curve.Knots.ToList()),
                         tuple(controls), bool(curve.IsClosed)))
    return tuple(profiles)


def _extrusion_display_descriptor(extrusion):
    """Return ``(key, frame)`` or ``None`` when rigid reuse is not provable."""
    frame = _extrusion_display_frame(extrusion)
    if frame is None:
        return None
    _origin, _x, _y, _z, height = frame
    key = (_canonical_profile(extrusion, frame), round(height, 12),
           int(extrusion.ProfileCount), int(extrusion.CapCount),
           bool(extrusion.IsCappedAtBottom), bool(extrusion.IsCappedAtTop),
           bool(extrusion.IsSolid), bool(extrusion.IsMiteredAtStart),
           bool(extrusion.IsMiteredAtEnd))
    return key, frame


def _canonical_point(point, frame):
    origin, x, y, z, _height = frame
    d = _sub3(point, origin)
    return (_dot3(d, x), _dot3(d, y), _dot3(d, z))


def _world_point(point, frame):
    origin, x, y, z, _height = frame
    return (origin[0] + point[0] * x[0] + point[1] * y[0] + point[2] * z[0],
            origin[1] + point[0] * x[1] + point[1] * y[1] + point[2] * z[1],
            origin[2] + point[0] * x[2] + point[1] * y[2] + point[2] * z[2])


def _world_normal(normal, frame):
    _origin, x, y, z, _height = frame
    return (normal[0] * x[0] + normal[1] * y[0] + normal[2] * z[0],
            normal[0] * x[1] + normal[1] * y[1] + normal[2] * z[1],
            normal[0] * x[2] + normal[1] * y[2] + normal[2] * z[2])


def _cache_mesh_arrays(bm, frame):
    bm.normal_update()
    bm.verts.ensure_lookup_table()
    vertices = tuple(_canonical_point(tuple(v.co), frame) for v in bm.verts)
    normals = tuple((_dot3(tuple(v.normal), frame[1]),
                     _dot3(tuple(v.normal), frame[2]),
                     _dot3(tuple(v.normal), frame[3])) for v in bm.verts)
    faces = tuple(tuple(v.index for v in face.verts) for face in bm.faces)
    return vertices, faces, normals


def _mesh_array_bytes(arrays):
    vertices, faces, normals = arrays
    return 24 * (len(vertices) + len(normals)) + 4 * sum(len(f) for f in faces)


def _cached_extrusion_bmesh(arrays, frame):
    import bmesh
    vertices, faces, normals = arrays
    bm = _new_bmesh()
    verts = []
    for point, normal in zip(vertices, normals):
        v = bm.verts.new(_world_point(point, frame))
        v.normal = _world_normal(normal, frame)
        verts.append(v)
    for indices in faces:
        try:
            bm.faces.new(tuple(verts[i] for i in indices))
        except ValueError:
            pass
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.normal_update()
    for vertex, normal in zip(verts, normals):
        vertex.normal = _world_normal(normal, frame)
    _shade_smooth(bm)
    return bm


def _get_cached_extrusion(key, frame):
    global _EXTRUSION_DISPLAY_CACHE_HITS, _EXTRUSION_DISPLAY_CACHE_MISSES
    entry = _EXTRUSION_DISPLAY_CACHE.pop(key, None)
    if entry is None:
        _EXTRUSION_DISPLAY_CACHE_MISSES += 1
        return None
    _EXTRUSION_DISPLAY_CACHE[key] = entry
    _EXTRUSION_DISPLAY_CACHE_HITS += 1
    return _cached_extrusion_bmesh(entry[0], frame)


def _store_cached_extrusion(key, arrays):
    global _EXTRUSION_DISPLAY_CACHE_BYTES
    size = _mesh_array_bytes(arrays)
    if size > _EXTRUSION_DISPLAY_CACHE_MAX_BYTES:
        return
    old = _EXTRUSION_DISPLAY_CACHE.pop(key, None)
    if old is not None:
        _EXTRUSION_DISPLAY_CACHE_BYTES -= old[1]
    while (_EXTRUSION_DISPLAY_CACHE
           and (_EXTRUSION_DISPLAY_CACHE_BYTES + size > _EXTRUSION_DISPLAY_CACHE_MAX_BYTES
                or len(_EXTRUSION_DISPLAY_CACHE) >= _EXTRUSION_DISPLAY_CACHE_MAX_ENTRIES)):
        _key, (_arrays, old_size) = _EXTRUSION_DISPLAY_CACHE.popitem(last=False)
        _EXTRUSION_DISPLAY_CACHE_BYTES -= old_size
    _EXTRUSION_DISPLAY_CACHE[key] = (arrays, size)
    _EXTRUSION_DISPLAY_CACHE_BYTES += size


def brep_to_bmesh(brep, nu: int = 32, nv: int = 32, trim_planar: bool = False,
                  adaptive: bool = False):
    """Sample every face of a brep into one bmesh.

    Non-planar faces (and, by default, every face) are grid-sampled over their
    full parametric domain. All primitive breps we build have UNTRIMMED
    non-planar faces, so that grid is exact for them.

    When ``trim_planar`` is True -- the DISPLAY path, for primitive breps -- each
    PLANAR face is instead triangulated inside its trim loop (see
    ``_add_trimmed_planar_face``). That is what stops a capped extrusion drawing
    its bounding rectangle over a circular or polygonal cap. Any planar face the
    triangulator cannot resolve (holes, a
    non-simple boundary) falls back to the grid.

    When ``adaptive`` is True (also display-only) each curved face's grid density
    is chosen per direction from the chord tolerance (``_iso_segments``): a
    cylinder gets ~50 facets around but only one up its straight height, so it
    reads round without a 32x32 grid. Booleans call this with the defaults
    (``trim_planar=False``, ``adaptive=False``, fixed 32x32) so their input
    meshing -- and every boolean test -- is unchanged.
    """
    import bmesh

    bm = _new_bmesh()
    for face in brep.Faces:
        if trim_planar:
            try:
                if face.IsPlanar() and _add_trimmed_planar_face(bm, brep, face):
                    continue
            except Exception:  # noqa: BLE001
                pass            # any trouble -> fall through to the grid below
        if adaptive:
            us = _surface_axis_params(face, 0, CURVE_CHORD_TOL)
            vs = _surface_axis_params(face, 1, CURVE_CHORD_TOL)
        else:
            us = _uniform_params(face.Domain(0), nu)
            vs = _uniform_params(face.Domain(1), nv)
        _build_grid(bm, face, us, vs)

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    _shade_smooth(bm)
    return bm


def _display_is_mesh_derived(brep) -> bool:
    """Whether ``brep``'s faces really ARE facets, independent of face count.

    A SubD/NURBS conversion can contain thousands of degree-4 patches. A former
    ``len(Faces) > 64`` shortcut called those mesh-derived and sampled each curved patch at
    only its four corners, visibly changing the surface. Provenance wins when present; an
    untagged foreign brep must independently look tessellated *and* consist of mesh facets.
    """
    k = _kernel()
    if k.representation_of(brep) == k.MESH_DERIVED:
        return True
    try:
        if brep.GetUserString(k.DOCUMENT_FORM_KEY) == "triangulated":
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        return bool(k.looks_tessellated(brep) and _faces_are_mesh_facets(brep))
    except Exception:  # noqa: BLE001
        return False


def _display_density(brep) -> int:
    """Legacy fixed density: one sample only for verified facet geometry.

    Analytic breps use the adaptive per-face path, regardless of whether they contain 3 or
    3,000 NURBS faces. ``32`` is only its fallback density when adaptive probing fails.
    """
    return 1 if _display_is_mesh_derived(brep) else 32


def _master_bmesh(master):
    """The display mesh of an analytic MASTER: OCCT's tessellation, welded, smooth-shaded
    with its hard edges kept -- what the triangulated proxy used to be drawn from."""
    hit = _PREFETCH_TESS.pop(id(master), None) if _PREFETCH_TESS else None
    if hit is not None and hit[0] is master:
        # an Open's worker tessellated this very master (`prefetch_document`)
        edges = _rows2(hit[3]) if len(hit) > 3 and hit[3] is not None else None
        return _tessellation_bmesh(_rows3(hit[1]), _rows3(hit[2]), edges)
    return _tessellation_bmesh(*display_tessellation(master))


def display_tessellation(master):
    """``(verts, tris, real_edges)`` -- THE display mesh of an analytic master, welded:
    what `_master_bmesh` draws and an Open's workers prefetch. Never the geometry."""
    return _weld_tessellation_edges(*_occt().tessellate_with_edges(master))


def _rows2(flat):
    return list(zip(flat[0::2], flat[1::2]))


def _tessellation_bmesh(verts, tris, edges=None):
    """`_master_bmesh`'s mesh from a welded tessellation.

    With ``edges``—vertex pairs on the solid's real edges—the mesh is split exactly at
    those boundaries. Shaded mode can then draw brep edges while each face remains smooth
    up to its boundary. Without explicit edges, the angle-based ``_weld_shading`` fallback
    is used and can expose extra sliver boundaries on curved walls.
    """
    bm = _new_bmesh()
    bv = [bm.verts.new(v) for v in verts]
    for a, b, c in tris:
        try:
            bm.faces.new((bv[a], bv[b], bv[c]))
        except ValueError:
            pass
    import bmesh

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    if edges is None:
        _weld_shading(bm)
        return bm
    bm.verts.ensure_lookup_table()
    real = []
    for a, b in edges:
        e = bm.edges.get((bv[a], bv[b]))
        if e is not None:
            real.append(e)
    if real:
        bmesh.ops.split_edges(bm, edges=real)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    for f in bm.faces:
        f.smooth = True
    return bm


def geometry_to_bmesh(geom):
    """Dispatch any rhino3dm geometry to a bmesh (point / curve / surface / brep)."""
    frame = picture_frame(geom) if isinstance(geom, r3.Brep) else None
    # a master-backed object is drawn from its MASTER. Its document form may now be
    # exact polysurface (trimmed NURBS faces the grid sampler cannot honour) or a
    # triangulated proxy; either way the master is the geometry.
    master = (_kernel().master_of(geom)
              if isinstance(geom, (r3.Brep, r3.Extrusion)) else None)
    source = geom
    native_extrusion_cache = None
    if isinstance(geom, r3.Extrusion) and master is None:
        native_extrusion_cache = _extrusion_display_descriptor(geom)
        if native_extrusion_cache is not None:
            cached = _get_cached_extrusion(native_extrusion_cache[0],
                                            native_extrusion_cache[1])
            if cached is not None:
                return cached
    if master is not None and _occt() is not None:
        try:
            bm = _master_bmesh(master)
        except Exception:  # noqa: BLE001
            # Some very small clockwise trim loops produce no OCCT triangles. Fall back
            # to drawing the brep's own faces so the object remains visible.
            bm = None
        if bm is not None:
            if frame is not None:
                _apply_picture_uvs(bm, frame)
            return bm
    if isinstance(geom, r3.Extrusion):
        # No master (OCCT missing, or declined): draw its BREP form -- caps, holes and
        # every side face. Falling through to the surface path below drew only the
        # extrusion's first profile swept, omitting caps and holes.
        brep = geom.ToBrep(True)
        if brep is not None:
            geom = brep
    if frame is not None:
        # a Picture is planar faces only -- triangulated inside their trims, with
        # UVs from the stored frame so the image is registered to the surface
        bm = brep_to_bmesh(geom, 1, 1, trim_planar=True)
        _apply_picture_uvs(bm, frame)
        return bm
    if (isinstance(geom, r3.Brep)
            and (id(geom) in _DECLINED_ANALYTIC or _has_holes(geom))
            and _occt() is not None):
        # A brep can fail solid adoption while every trimmed NURBS face remains exact.
        # Draw that sewn shell through OCCT, but do not register it as a master: it
        # is still refused for manufacturing operations until it becomes airtight.
        try:
            hit = None
            if _PREFETCH_DECLINED:
                lst = _PREFETCH_DECLINED.get(curve_fingerprint(source))
                hit = lst.pop() if lst else None
            if hit is not None:
                bm = _tessellation_bmesh(_rows3(hit[0]), _rows3(hit[1]),
                                          _rows2(hit[2]) if len(hit) > 2 else None)
            else:
                shape = _occt().shape_from_brep_data(
                    brep_data(geom), sew_tolerance=max(_DOC_TOLERANCE[0] or 0.0, 1e-6))
                bm = _master_bmesh(shape)
            if len(bm.faces):
                return bm
            bm.free()
        except Exception:  # noqa: BLE001
            pass
    if isinstance(geom, r3.Brep):
        nu = _display_density(geom)
        # For analytic breps (nu > 1): honour trimmed caps AND size the grid
        # adaptively per face, so a cylinder reads round (~50 facets around, 1 up
        # its straight height) instead of a fixed 32x32. Mesh-breps
        # (nu == 1) are already an exact grid of flat patches -- resampling or
        # ear-clipping them would be slow and pointless; see `_display_density`.
        analytic = nu > 1
        bm = brep_to_bmesh(geom, nu, nu, trim_planar=analytic, adaptive=analytic)
        if native_extrusion_cache is not None and analytic:
            _store_cached_extrusion(
                native_extrusion_cache[0],
                _cache_mesh_arrays(bm, native_extrusion_cache[1]))
        if not analytic:
            # A mesh-brep (a boolean result) is thousands of LONE faces --
            # brep_to_bmesh leaves each its own vertex island, so smooth shading can
            # average no normals and a unioned cylinder looks faceted. Weld across
            # the smooth joins and keep only the sharp creases.
            _weld_shading(bm)
        return bm
    if isinstance(geom, r3.Mesh):
        return mesh_to_bmesh(geom)
    if isinstance(geom, r3.Text):
        return text_to_bmesh(geom)
    # A bare point is a single vertex (no edges, no faces). It is checked before
    # the curve path because a Point is not a Curve and would otherwise raise.
    if isinstance(geom, r3.Point):
        bm = _new_bmesh()
        loc = geom.Location
        bm.verts.new((loc.X, loc.Y, loc.Z))
        return bm
    # Surfaces expose Domain(int); curves do not.
    try:
        geom.Domain(0)
        return surface_to_bmesh(geom)
    except TypeError:
        pass
    if isinstance(geom, r3.Curve):
        return curve_display_bmesh(geom)
    raise TypeError(f"unsupported geometry type: {type(geom).__name__}")


# ---------------------------------------------------------------------------
# Curves are drawn by the APP, not by Blender.
#
# In a 5,494-curve model, a pane frame while orbiting spent most of its time syncing
# thousands of separate Blender objects. Hiding those objects from Blender's view-layer
# drawing reduced the frame cost substantially. Every display object with no
# FACES (curves, point objects, annotation outlines) is linked into CURVE_COLLECTION,
# whose LAYER collection is hidden in every view layer, and ui.py draws all of them in
# a few batched draw calls (`ui.draw_curves`). The objects still exist, select, carry a
# matrix and update their meshes exactly as before -- Blender just never draws them.
#
# Two consequences every reader of Blender state must respect:
#  * `bpy.context.selected_objects` does NOT list them -> `selected_display_objects`;
#  * `ob.visible_get` is False for them               -> `display_visible(ob)`.
# ---------------------------------------------------------------------------

CURVE_COLLECTION = "narwhal_curves"

#: Listeners told which app-drawn display objects changed (their mesh or matrix), as a
#: set of names. ui.py's curve pass registers here.
#:
#: Patched Blender builds keep curve-collection objects outside the dependency graph.
#: Such objects emit no ``depsgraph_update_post`` event when their mesh or matrix changes,
#: so every writer explicitly notifies listeners here.
DISPLAY_CHANGE_LISTENERS = []


def note_display_changed(names, removed=False):
    """Tell the curve pass that these display objects' mesh or matrix changed (or, with
    `removed`, that they are about to be deleted)."""
    if isinstance(names, str):
        names = (names,)
    names = [n for n in names if n]
    if not names:
        return
    for fn in list(DISPLAY_CHANGE_LISTENERS):
        try:
            fn(names, removed) if removed else fn(names)
        except Exception:  # noqa: BLE001
            pass


def app_drawn_ok():
    """Whether Blender exposes the app-drawn collection property.

    ``Collection.narwhal_app_drawn`` is retained as the internal compatibility identifier.
    """
    import bpy

    return hasattr(bpy.types.Collection, "bl_rna") and (
        "narwhal_app_drawn" in bpy.types.Collection.bl_rna.properties)


def curve_collection(create=True):
    """The collection of app-drawn (face-less) display objects, hidden from Blender's
    own drawing in every view layer. Created lazily; None if absent and not `create`."""
    import bpy

    scene = bpy.context.scene
    coll = bpy.data.collections.get(CURVE_COLLECTION)
    if coll is None:
        if not create:
            return None
        coll = bpy.data.collections.new(CURVE_COLLECTION)
    if scene.collection.children.get(coll.name) is None:
        scene.collection.children.link(coll)
    for vl in scene.view_layers:
        for lc in vl.layer_collection.children:
            if lc.collection == coll and not lc.hide_viewport:
                lc.hide_viewport = True
    # the patched Blender keeps these objects out of its dependency graph entirely
    if app_drawn_ok() and not coll.narwhal_app_drawn:
        coll.narwhal_app_drawn = True
    return coll


def is_app_drawn(ob) -> bool:
    """True for a display object Blender does not draw (it lives in CURVE_COLLECTION)."""
    try:
        return any(c.name == CURVE_COLLECTION for c in ob.users_collection)
    except Exception:  # noqa: BLE001  (a removed object)
        return False


def local_box_corners(ob):
    """`ob.bound_box` -- the 8 local-space corners -- read from the MESH for an app-drawn
    object. `bound_box` reads Blender's evaluated-bounds cache, and with the patched
    Blender an app-drawn object is never evaluated (`app_drawn_ok`); the cache
    still answers from the original mesh, but that is an implementation detail this does
    not lean on."""
    if not is_app_drawn(ob):
        return [tuple(c) for c in ob.bound_box]
    me = getattr(ob, "data", None)
    n = len(me.vertices) if me is not None else 0
    if not n:
        return [(0.0, 0.0, 0.0)] * 8
    import numpy as np

    co = np.empty(n * 3, dtype=np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    lo, hi = co.min(axis=0), co.max(axis=0)
    return [(float((hi if i & 4 else lo)[0]), float((hi if i & 2 else lo)[1]),
             float((hi if i & 1 else lo)[2])) for i in range(8)]


def display_visible(ob) -> bool:
    """Is `ob` SHOWN on screen -- by Blender or by the app's curve pass?

    The replacement for `ob.visible_get` wherever the question is "is it shown /
    pickable": Blender reports False for every object in the hidden curve collection,
    although the app draws them. Such an object is shown when it is in the view layer,
    not `hide_viewport` (document Hide, a layer switched off) and not `hide_get`.
    """
    try:
        if ob.visible_get():
            return True
        if ob.hide_viewport or not is_app_drawn(ob):
            return False
        return not ob.hide_get()
    except Exception:  # noqa: BLE001  (not in this view layer / removed)
        return False


def view_layer_selected():
    """Every selected object of the view layer (curves included), as a list.

    (Blender 5.2, -b): right after `bpy.data.objects.remove`, iterating
    `view_layer.objects.selected` yields None for the removed object until the view
    layer re-syncs its bases -- which reading `objects.active` does. So: sync, then
    filter. A list, so callers may deselect while walking it.
    """
    import bpy

    objs = bpy.context.view_layer.objects
    _ = objs.active                    # re-syncs the bases (see above)
    return [ob for ob in objs.selected if ob is not None]


def selected_display_objects():
    """The selected display objects, curves included.

    `bpy.context.selected_objects` lists only objects Blender itself can see, so it
    silently drops app-drawn curves; the view layer's own selection includes them.
    Hidden objects (`hide_viewport` /
    `hide_get`) are filtered out, which is what `selected_objects` did for them.
    """
    out = []
    for ob in view_layer_selected():
        try:
            if ob.hide_viewport or ob.hide_get():
                continue
        except Exception:  # noqa: BLE001
            continue
        out.append(ob)
    return out


class blender_sees_curves:
    """Context manager: let Blender's OWN operators see the app-drawn objects.

    `view3d.view_all` / `view_selected` (the Zoom command) consider only objects
    Blender draws, so Zoom Extents on a curves-only file framed nothing. This un-hides
    the curve collection's layer collection for the duration (with a view-layer update
    so the operators see the new visibility) and hides it again afterwards.
    """

    def __enter__(self):
        import bpy

        self._lcs = []
        self._coll = None
        coll = curve_collection(create=False)
        if coll is None:
            return self
        vl = bpy.context.view_layer
        for lc in vl.layer_collection.children:
            if lc.collection == coll and lc.hide_viewport:
                lc.hide_viewport = False
                self._lcs.append(lc)
        # the patched Blender keeps these objects out of evaluation; the framing
        # operators read evaluated bounds, so put them back in for the duration
        if app_drawn_ok() and coll.narwhal_app_drawn:
            coll.narwhal_app_drawn = False
            self._coll = coll
        if self._lcs or self._coll is not None:
            vl.update()
        return self

    def __exit__(self, *exc):
        import bpy

        for lc in self._lcs:
            lc.hide_viewport = True
        if self._coll is not None:
            self._coll.narwhal_app_drawn = True
        if self._lcs or self._coll is not None:
            try:
                bpy.context.view_layer.update()
            except Exception:  # noqa: BLE001
                pass
        return False


def bmesh_to_object(name: str, bm, collection=None):
    """Turn a bmesh into a Blender mesh object linked to `collection` (or scene).

    With no `collection`, a bmesh with NO faces (a curve, a point) goes into the
    app-drawn CURVE_COLLECTION, which Blender never draws -- see the block above.
    """
    import bpy

    faceless = len(bm.faces) == 0
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    if collection is None:
        collection = (curve_collection() if faceless
                      else bpy.context.scene.collection)
    collection.objects.link(ob)
    if faceless:
        note_display_changed(ob.name)
    return ob


def rebuild_object_mesh(obj_name: str, geom) -> bool:
    """Re-tessellate one existing display object *in place*, from `geom`.

    The full `NarwhalContext.rebuild_display` deletes and recreates EVERY Blender
    object in the document and re-tessellates the whole model. A control-point
    drag calls it on every frame, so dragging one point would otherwise re-tessellate
    the entire scene on every update.

    This updates a single object: it writes a fresh bmesh into that object's
    EXISTING `mesh` datablock (`bm.to_mesh(ob.data)`) rather than making a new
    mesh or a new object. Two consequences matter:

    * No object or datablock is deleted or created, so **no held reference goes
      stale** -- which is the exact failure mode `rebuild_display` is documented
      for (a drag holds the dragged object). The material slots the layer colour
      lives in are on the datablock, so they survive too.
    * The object's position stays baked in the mesh; `matrix_world` is left
      untouched (still the (0,0,0) translation the Gumball, snaps and pickers all
      assume). This function never writes `ob.location` / `ob.matrix_world`.

    Returns False when no object named `obj_name` exists, so the caller can fall
    back to the full rebuild instead of silently doing nothing. Returns True
    after a successful in-place update.
    """
    import bpy

    ob = bpy.data.objects.get(obj_name)
    if ob is None or ob.data is None:
        return False
    if isinstance(geom, r3.PolyCurve):
        # (what `geometry_to_bmesh` does for any curve) with the object's segment
        # cache: an in-place edit is usually a grip drag, re-run on every mouse move
        bm = curve_display_bmesh(geom, seg_cache=_seg_cache_for(ob.name))
    else:
        bm = geometry_to_bmesh(geom)
    bm.to_mesh(ob.data)
    bm.free()
    ob.data.update()
    note_display_changed(ob.name)
    return True


def remove_display_objects(objects):
    """Delete Blender objects `objects` (and meshes left without users) in one batch.

    Individual `bpy.data.objects.remove` calls repeatedly scan scene relations;
    `bpy.data.batch_remove` performs that work together."""
    import bpy

    obs = [ob for ob in objects if ob is not None]
    if not obs:
        return 0
    note_display_changed([ob.name for ob in obs if is_app_drawn(ob)], removed=True)
    meshes = {ob.data for ob in obs if ob.data is not None}
    bpy.data.batch_remove(obs)
    orphans = [m for m in meshes if getattr(m, "users", 1) == 0]
    if orphans:
        bpy.data.batch_remove(orphans)
    return len(obs)


def refresh_display_objects(ctx, changes) -> bool:
    """Bring the display in step with an UNDO / REDO that changed only some objects.

    `changes` is `NarwhalModel.last_restore`: ``{"removed": {id: display name},
    "added": [id,...]}``. The display objects of removed (and replaced) objects are
    deleted, the added ones are made exactly as `rebuild_display` makes them
    (`ctx._make_display_object`), and every other display object -- unchanged in the
    document -- is left alone. Name counters are re-from the document's names,
    as `rebuild_display` derives them. `changes` None means the whole table was
    replaced: every display object is removed in one batch, then `ctx.rebuild_display`.

    Incremental refresh avoids deleting and re-tessellating every display object when only
    a subset changed. Returns False only without bpy (headless), so the caller can perform
    its own rebuild.
    """
    import re

    try:
        import bpy
    except ImportError:
        return False

    if changes is None:
        remove_display_objects(list(bpy.data.objects))
        ctx.rebuild_display()
        return True
    model = ctx.model
    gone = [bpy.data.objects.get(n) for n in (changes.get("removed") or {}).values() if n]
    remove_display_objects(gone)
    added = changes.get("added") or []
    if added:
        layers = model.layers()
        table = model.file.Objects
        for oid in added:
            fobj = table.FindId(oid)
            if fobj is None:
                continue
            a = fobj.Attributes
            name = a.Name or ""
            if not name:
                continue
            try:
                maker = getattr(ctx, "_make_file_object_display", None)
                if callable(maker):
                    maker(fobj, layers)
                else:
                    ctx._make_display_object(fobj.Geometry, name, int(a.LayerIndex),
                                             fobj=fobj, layers=layers)
            except Exception:  # noqa: BLE001
                continue          # as rebuild_display: one bad object never aborts
            model.register_blender_object(oid, name)
    counters = {}
    for name in model._blender_names.values():
        m = re.match(r"^(.*?)(\d+)$", name or "")
        if m and int(m.group(2)) > counters.get(m.group(1), 0):
            counters[m.group(1)] = int(m.group(2))
    ctx.counters = counters
    return True


def retessellate_curve_for_view(obj_name, curve, pixels_per_unit,
                                pixel_tol=None) -> bool:
    """Re-tessellate ONE curve's display object at the current zoom.

    `pixels_per_unit` is the viewport scale (screen pixels per world unit); the
    curve is drawn to a SCREEN chord tolerance (`tessellate.screen_segments_for`),
    so zooming in adds segments and the curve never facets. Reuses 's in-place
    path -- it writes a fresh bmesh into the EXISTING datablock, touching no other
    object and creating/deleting nothing -- so a scroll never re-tessellates the
    document. The viewport layer (ui.py) owns the trigger and the zoom level; this
    is the pure, mesh-level half it calls. Returns False if no such object exists.

    THRESHOLD: the caller should gate this with `tessellate.should_retessellate`
    (also pure/tested) so it only fires on a real zoom change, not every frame.
    """
    import bpy

    ob = bpy.data.objects.get(obj_name)
    if ob is None or ob.data is None:
        return False
    try:
        from . import tessellate
    except ImportError:  # support direct-file loading outside the package
        import tessellate
    pt = tessellate.SCREEN_PIXEL_TOL if pixel_tol is None else pixel_tol
    world_tol = pt / pixels_per_unit if pixels_per_unit > 0.0 else CURVE_CHORD_TOL
    bm = curve_display_bmesh(curve, world_tol)
    # Document curves are in world coordinates, while mesh vertices are object-local.
    # A deferred Gumball commit keeps the old local mesh and carries the committed
    # transform on the display object; re-tessellating the already-transformed document
    # curve under that matrix would apply the move a second time. Preserve the matrix and
    # express the fresh tessellation in its local space before uploading it.
    matrix = ob.matrix_world
    if not matrix.is_identity:
        bm.transform(matrix.inverted_safe())
    # `bm.to_mesh` tags the mesh's geometry for the depsgraph itself, which is all a
    # face-less display mesh needs. No `Mesh.update`: it re-tags the mesh broadly and
    # makes the next dependency-graph evaluation unnecessarily expensive on each zoom step.
    bm.to_mesh(ob.data)
    bm.free()
    note_display_changed(ob.name)
    return True


def apply_document_visibility(model):
    """Show/hide each display object from its document `ObjectAttributes.Mode`.

    the reference CAD application's `Hide`/`Show`/`Lock`/`Unlock` write `Mode` on the document object, but
    the viewport also has to obey it: a **Hidden** object must not be drawn, and a
    **Locked** one must STAY drawn (locking is not hiding -- the reference CAD application draws locked
    objects and merely makes them unpickable; the unpickable half is `ui.py`'s and
    is left alone here). `rebuild_display` (commands.py, the command layer's) draws
    every object regardless of Mode, so it should call this once at the end, and
    each of the four visibility commands should call it after mutating Mode -- a
    one-line hook each, the same ready-to-wire shape as `rebuild_object_mesh`. This function OWNS only the drawing decision.

    Walks the File3dm object table and matches Blender objects by name, setting
    `hide_viewport`/`hide_render`. A hidden object thus contributes nothing to the
    view but is NOT deleted, so `Show` restores it instantly with no
    re-tessellation and no stale reference. Returns the number hidden.

    a Locked object is drawn NORMALLY here -- the reference CAD application greys it, but the grey
    is a display-colour setting we have not plumbed, so "drawn, not greyed yet".
    """
    import bpy

    file3dm = getattr(model, "file", None)
    if file3dm is None:
        return 0
    hidden_value = int(r3.ObjectMode.Hidden)
    hidden = 0
    for fobj in file3dm.Objects:
        name = fobj.Attributes.Name or ""
        if not name:
            continue
        ob = bpy.data.objects.get(name)
        if ob is None:
            continue
        is_hidden = int(fobj.Attributes.Mode) == hidden_value
        ob.hide_viewport = is_hidden
        ob.hide_render = is_hidden
        if is_hidden:
            hidden += 1
    return hidden


# ---------------------------------------------------------------------------
# Solid constructors (verified rhino3dm 8.35.0 signatures)
# ---------------------------------------------------------------------------

def make_line(p1, p2):
    return r3.LineCurve(r3.Point3d(*p1), r3.Point3d(*p2)).ToNurbsCurve()


def make_point(location):
    """A single point object at world `location` (the command behavior).

    A point is a *location*, not a plane-dependent shape, so there is no CPlane
    to build in -- the picked point IS the object. `r3.Point` is rhino3dm's point
    object; it round-trips through a File3dm and `geometry_to_bmesh` draws it as
    a single vertex (verified: Point.Location survives a save/reload).
    """
    return r3.Point(r3.Point3d(*location))


# ---------------------------------------------------------------------------
# Construction-plane-aware building
#
# Picking resolves a point through the active CPlane, but construction must also
# orient plane-dependent geometry in that frame:
# `r3.Circle(Point3d, radius)` always builds in world XY, and rhino3dm 8.35.0 has
# NO plane overload (verified: only `Circle(radius)` and `Circle(center, radius)`).
# So a circle drawn on Front got a correct centre and a world-XY plane -- edge-on
# in the very viewport you drew it in.
#
# `make_line`/`make_polyline` are built from real 3D points, which is why Line
# always worked on Front and Circle never did.
#
# Build the shape canonically in world XY about the origin, then map it onto the
# target frame with `Transform.PlaneToPlane`. This preserves the intended centre,
# orientation, and dimensions on any construction plane.
# ---------------------------------------------------------------------------

def _r3_plane(origin, frame):
    """Our `cplane.CPlane` frame + a world origin -> an ``r3.Plane``.

    Only the frame's axes are taken from `frame`; the origin is passed separately
    because the caller has already resolved it to a world point (the picked
    centre), which is generally NOT the CPlane's own origin.
    """
    return r3.Plane(r3.Point3d(*origin),
                    r3.Vector3d(*frame.xaxis),
                    r3.Vector3d(*frame.yaxis))


def _world_xy_at_origin():
    """The canonical frame shapes are built in before being mapped onto a CPlane."""
    return r3.Plane(r3.Point3d(0.0, 0.0, 0.0),
                    r3.Vector3d(1.0, 0.0, 0.0),
                    r3.Vector3d(0.0, 1.0, 0.0))


def to_plane(geom, center, plane):
    """Move `geom` (built in world XY about the origin) onto `plane` at `center`.

    Returns `geom` mutated in place (rhino3dm transforms in place) for chaining.
    A `plane` of ``None`` means "world XY", so callers can pass the active CPlane
    straight through without branching.
    """
    if plane is None:
        xform = r3.Transform.Translation(float(center[0]), float(center[1]),
                                        float(center[2]))
    else:
        xform = r3.Transform.PlaneToPlane(_world_xy_at_origin(),
                                         _r3_plane(center, plane))
    geom.Transform(xform)
    return geom


def make_circle(center, radius, plane=None):
    """A circle of `radius` at world `center`, lying in `plane` (the active CPlane).

    With `plane=None` this is the old world-XY behaviour, so existing callers and
    saved files are unaffected.
    """
    crv = r3.Circle(float(radius)).ToNurbsCurve()   # world XY, about the origin
    return to_plane(crv, center, plane)


def make_polyline(points, degree: int = 3, closed: bool = False):
    pts = [r3.Point3d(*p) for p in points]
    # cubic needs >=4 control points; fall back to quadratic / polyline
    if len(pts) < 2:
        raise ValueError("polyline needs at least 2 points")
    if closed:
        return _make_periodic_curve(pts, degree)
    deg = min(degree, len(pts) - 1)
    crv = r3.NurbsCurve.CreateControlPointCurve(pts, max(1, deg))
    if crv is None or not crv.IsValid:
        crv = r3.PolylineCurve(pts).ToNurbsCurve()
    return crv


def _make_periodic_curve(points, degree: int = 3):
    """A SMOOTH closed (periodic) curve through `points`.

    A clamped curve forced through a repeated seam point creates a visible C0 corner.
    rhino3dm has no ``MakePeriodic``, and the clamped builder
    (``CreateControlPointCurve``) cannot
    express a periodic knot vector (it always clamps, forcing interpolation of the
    seam), so the curve is built by hand: WRAP the first `degree` control points and
    lay down a UNIFORM (unclamped) knot vector, which is what makes the seam periodic.
    seam tangent 0.0 deg, matching curvature across the seam (G2),
    `IsPeriodic == True`, and the curve does NOT interpolate its control net.

    A periodic degree-d curve needs at least d control points, so the degree is
    dropped to fit when fewer are given; a closed curve below a triangle (3 points)
    is refused by name -- there is nothing to enclose.
    """
    if len(points) < 3:
        raise ValueError(
            f"a closed (periodic) curve needs at least 3 points (got {len(points)})")
    deg = max(1, min(int(degree), len(points)))   # periodic degree-d needs >= d pts
    ctrl = list(points) + list(points[:deg])      # wrap the first `deg` points
    c = r3.NurbsCurve(3, False, deg + 1, len(ctrl))   # dim, rational, ORDER, cvCount
    for i, p in enumerate(ctrl):
        c.Points[i] = r3.Point4d(p.X, p.Y, p.Z, 1.0)
    for i in range(len(c.Knots)):
        c.Knots[i] = float(i)                     # uniform -> unclamped -> periodic
    if not c.IsValid:
        raise ValueError("periodic curve construction produced an invalid curve")
    return c


def make_interpolated_curve(points, degree: int = 3, style=None, closed: bool = False):
    """A curve PASSING THROUGH `points` -- what `InterpCrv` builds.

    (the command behavior): *"The InterpCrv command draws a curve **through**
    selected locations in space... Curve matches input."* That is a different command from
    `Curve`, which *"draws a curve from **control point** locations"* and is what:func:`make_polyline` builds.

    THE BUG THIS FIXES: `InterpCrv` called `make_polyline(pts, degree=3)`, i.e. rhino3dm's
    `CreateControlPointCurve`, so the curve was smooth but did NOT pass through the picked
    points. on a 6-point sample, the old route missed them by **1.5865 units**.
    rhino3dm has no interpolation primitive, so the algorithm lives in
    `narwhal3d/interpolate.py`.

    `closed=True` is NOT interpolation yet -- see below -- and says so.
    """
    interp = _interpolate()
    if style is None:
        style = interp.DEFAULT_KNOT_STYLE
    if closed:
        # PERIODIC interpolation is not implemented. Rather than silently hand back a
        # curve that does not pass through the points (the very bug being fixed), this
        # refuses by name. The caller may explicitly fall back to the smooth periodic
        # control-point curve from ``make_polyline(..., closed=True)``, but it must
        # choose that knowingly.
        raise ValueError(
            "InterpCrv: closed (periodic) interpolation is not implemented yet; a closed "
            "curve is currently built as a smooth periodic control-point curve, which "
            "does NOT pass through the picked points")
    return interp.interpolate_curve(points, degree=degree, style=style)


def make_box(corner1, corner2, height, plane=None):
    """Box from two base corners + height, built in `plane`.

    The corners arrive as WORLD points (already resolved through the CPlane), so
    they are first expressed in the plane's own coordinates -- otherwise a box
    drawn on Front would take its extent from world X/Y and collapse. The box is
    built axis-aligned in those local coordinates and then mapped onto the frame,
    so `height` runs along the CPlane NORMAL, which is what the reference CAD application means by
    height.

    With `plane=None` this is the original world-XY behaviour.
    """
    if plane is None:
        x0, y0 = min(corner1[0], corner2[0]), min(corner1[1], corner2[1])
        x1, y1 = max(corner1[0], corner2[0]), max(corner1[1], corner2[1])
        bbox = r3.BoundingBox(r3.Point3d(x0, y0, 0.0),
                              r3.Point3d(x1, y1, float(height)))
        brep = r3.Brep.CreateFromBox(r3.Box(bbox))
        # this EARLY RETURN is why the first attempt recorded no recipe for a box --
        # the world-XY path bypassed the recording below entirely, so every boolean
        # involving a box silently fell back to the mesh route.
        _record(brep, "box", r3.Transform.Translation(float(x0), float(y0), 0.0),
                dx=float(x1 - x0), dy=float(y1 - y0), dz=float(height))
        return brep

    u1, v1, _w1 = plane.from_world(corner1)
    u2, v2, _w2 = plane.from_world(corner2)
    u0, u1_ = min(u1, u2), max(u1, u2)
    v0, v1_ = min(v1, v2), max(v1, v2)
    bbox = r3.BoundingBox(r3.Point3d(u0, v0, 0.0),
                          r3.Point3d(u1_, v1_, float(height)))
    brep = r3.Brep.CreateFromBox(r3.Box(bbox))
    # local coordinates are relative to the PLANE ORIGIN, so that is the anchor
    # the recipe is the box AT THE ORIGIN plus the transform that puts it here, so
    # the corner offset has to travel inside the transform.
    base = _plane_xform(plane.origin, plane)
    shift = r3.Transform.Translation(float(u0), float(v0), 0.0)
    _record(brep, "box", r3.Transform.Multiply(base, shift),
            dx=float(u1_ - u0), dy=float(v1_ - v0), dz=float(height))
    return to_plane(brep, plane.origin, plane)


def make_sphere(center, radius):
    brep = r3.Sphere(r3.Point3d(*center), float(radius)).ToBrep()
    # a sphere is rotationally symmetric, so only the translation matters.
    _record(brep, "sphere",
            r3.Transform.Translation(float(center[0]), float(center[1]),
                                     float(center[2])),
            radius=float(radius))
    return brep


def make_cylinder(center, radius, height, plane=None):
    """A cylinder of `radius` and `height` at world `center`, built in `plane`.

    The base disc lies in `plane` and the height runs along its normal -- the
    active CPlane. That is what the reference CAD application means by height (the command behavior:
    "The CPlane +Z direction defines the positive direction when you type a
    number to set the height").

    It is built canonically (world XY about the origin, height along +Z) and
    then mapped onto the frame, exactly as `make_circle`/`make_box` do -- the
    old version hard-coded the base to `z=0`, which threw away the z of the
    centre you picked, so a cylinder drawn on Front was in the wrong place and
    the wrong orientation.

    With `plane=None` this is the old world-XY behaviour.
    """
    base = r3.Circle(r3.Point3d(0.0, 0.0, 0.0), float(radius))   # world XY, about origin
    brep = r3.Cylinder(base, float(height)).ToBrep(True, True)
    _record(brep, "cylinder", _plane_xform(center, plane),
            radius=float(radius), height=float(height))
    return to_plane(brep, center, plane)


def make_cone(center, base_radius, height, plane=None, solid=True):
    """A cone (base `base_radius`, `height`) at world `center`, built in `plane`.

    Built canonically -- base disc in world XY about the origin, apex along +Z
    -- then mapped onto `plane` (the active CPlane) at `center`, so the height
    runs along the plane normal (the command behavior: "The CPlane +Z direction is
    the positive direction when you type a number to set the height").

    The old version translated by `(center[0], center[1], 0.0)` only, discarding
    the z of the picked centre and fixing the orientation to world XY.

    `solid` is the reference CAD application's own option (the command behavior: *"Solid --
    Decides if the bottom will be capped to create a solid or open cone"*),
    default Yes as in the reference CAD application.

    CAP ORIENTATION -- this was a real, bug. The profile runs from the
    BASE RIM ``(r,0,0)`` to the APEX ``(0,0,h)``, so in
    ``CreateFromRevSurface(rev, capStart, capEnd)`` the base is **capStart** and
    the apex is capEnd. The flags were passed ``(False, True)``: that asked to cap
    the degenerate apex -- which needs no cap -- and left the BASE DISC OPEN. The
    result was 1 face with ``IsSolid == False``: a cone you cannot manufacture.
    Face topology alone cannot detect a missing lid:

        capStart=False capEnd=True   faces=1  IsSolid=False   <-- the bug
        capStart=True  capEnd=False  faces=2  IsSolid=True    <-- correct

    Capping the apex is a no-op either way, so only capStart carries `solid`.

    With `plane=None` this is the old world-XY behaviour.
    """
    profile = r3.LineCurve(
        r3.Point3d(base_radius, 0, 0), r3.Point3d(0, 0, float(height))
    ).ToNurbsCurve()
    axis = r3.Line(r3.Point3d(0, 0, 0), r3.Point3d(0, 0, max(float(height), 1.0) * 2))
    rev = r3.RevSurface.Create(profile, axis, 0.0, 6.283185307179586)
    brep = r3.Brep.CreateFromRevSurface(rev, bool(solid), False)
    # only a CAPPED cone is a solid, so only that one gets a recipe -- an open cone
    # has no volume to boolean and must not pretend otherwise.
    if solid:
        _record(brep, "cone", _plane_xform(center, plane),
                radius=float(base_radius), height=float(height))
    # built at the origin in XY -- map it onto the active CPlane at `center`
    return to_plane(brep, center, plane)


# --------------------------------------------------------------------------
# exact primitives the reference CAD application has and we lacked
# --------------------------------------------------------------------------

def _occt_primitive(label, recipe):
    """Build `recipe` in OCCT and return the document object (master + recipe), or raise
    ValueError naming what is wrong. Every one must come back ONE closed solid."""
    occt = _occt()
    if occt is None:
        raise ValueError(f"{label} needs the OCCT kernel, which is not installed")
    try:
        shape = occt.from_recipe(recipe)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"{label}: {e}") from e
    if not occt.is_airtight(shape):
        raise ValueError(f"{label}: the result is not a closed solid")
    return occt_backed(shape, recipe)


def make_torus(center, major_radius, minor_radius, plane=None):
    """a TORUS (the command behavior, "draws a torus (doughnut shape)"): the base
    circle of `major_radius` about `center` in `plane`, swept by a circle of `minor_radius`.
    ONE `TORUS` face, volume 2 pi^2 R r^2. Recipe `torus`."""
    return _occt_primitive("Torus", {
        "kind": "torus", "major": float(major_radius), "minor": float(minor_radius),
        "xform": _xform_matrix(_plane_xform(center, plane))})


def make_truncated_cone(center, base_radius, top_radius, height, plane=None):
    """a TRUNCATED CONE (the command behavior): base circle in `plane` at
    `center`, the end circle `height` along the plane normal. `{CONE: 1, PLANE: 2}`, volume
    pi h (R^2 + R r + r^2) / 3. Recipe `truncated_cone`."""
    return _occt_primitive("TruncatedCone", {
        "kind": "truncated_cone", "radius": float(base_radius), "top": float(top_radius),
        "height": float(height), "xform": _xform_matrix(_plane_xform(center, plane))})


def make_tube(center, radius_a, radius_b, height, plane=None):
    """a TUBE (the command behavior: the base circle, then "the radius/diameter for the
    second tube wall", then the height). Either radius may be the outer one. `{CYLINDER: 2,
    PLANE: 2}`, volume pi h (R^2 - r^2). Recipe `tube`."""
    ro, ri = max(float(radius_a), float(radius_b)), min(float(radius_a), float(radius_b))
    return _occt_primitive("Tube", {
        "kind": "tube", "outer": ro, "inner": ri, "height": float(height),
        "xform": _xform_matrix(_plane_xform(center, plane))})


def make_ellipsoid(center, rx, ry, rz, plane=None):
    """an ELLIPSOID (the command behavior: "a solid NURBS ellipsoid"; Radius = "the
    half-length of each axis"). `rx`/`ry` lie along the plane's x/y axes, `rz` along its
    normal. Volume 4/3 pi a b c.

    EXACT: a unit `sphere` recipe whose xform is the placement times diag(rx, ry, rz). A
    non-uniform affine map of the sphere's rational NURBS is exact (GTransform), so
    the face is ONE smooth `BSPLINE` -- OCCT has no ellipsoid type -- never facets."""
    a, b, c = float(rx), float(ry), float(rz)
    if min(a, b, c) <= 0.0:
        raise ValueError(f"Ellipsoid: every half-axis must be positive (got {a}, {b}, {c})")
    scale = [a, 0.0, 0.0, 0.0, 0.0, b, 0.0, 0.0, 0.0, 0.0, c, 0.0, 0.0, 0.0, 0.0, 1.0]
    xf = _mat_mul(_xform_matrix(_plane_xform(center, plane)), scale)
    return _occt_primitive("Ellipsoid", {"kind": "sphere", "radius": 1.0, "xform": xf})


def make_pyramid(center, radius, side_count, height, inscribed=True, plane=None):
    """a PYRAMID (the command behavior: "Draw the base polygon" -- `make_polygon`'s
    rules, Inscribed or Circumscribed -- then the apex `height` along the plane normal).
    `{PLANE: n + 1}`, volume A_base h / 3. Recipe `polyhedron`  with world vertices,
    every face wound outward."""
    n = int(side_count)
    r, h = float(radius), float(height)
    if n < 3 or r <= 0.0 or h == 0.0:
        raise ValueError(f"Pyramid needs at least 3 sides, a positive radius and a non-zero "
                         f"height (got {n}, {r}, {h})")
    circle = r3.Circle(r3.Point3d(0.0, 0.0, 0.0), r)
    pl = (r3.Polyline.CreateInscribedPolygon(circle, n) if inscribed
          else r3.Polyline.CreateCircumscribedPolygon(circle, n))
    xf = _plane_xform(center, plane)
    pts = []
    for i in range(n):                       # the polyline repeats its first point
        p = r3.Point3d(pl[i].X, pl[i].Y, 0.0)
        p.Transform(xf)
        pts.append([p.X, p.Y, p.Z])
    apex = r3.Point3d(0.0, 0.0, h)
    apex.Transform(xf)
    pts.append([apex.X, apex.Y, apex.Z])
    a = n + 1                                # 1-based apex index
    up = h > 0.0
    base = list(range(n, 0, -1)) if up else list(range(1, n + 1))
    sides = [[i + 1, (i + 1) % n + 1, a] if up else [(i + 1) % n + 1, i + 1, a]
             for i in range(n)]
    faces = [[base]] + [[s] for s in sides]
    return _occt_primitive("Pyramid", {"kind": "polyhedron", "vertices": pts, "faces": faces})


def make_rectangle(corner1, corner2, plane=None):
    """A closed rectangle from two OPPOSITE corners, lying in `plane`.

    The corners arrive as WORLD points (already resolved through the CPlane), so
    they are first expressed in the plane's own coordinates -- otherwise a
    rectangle drawn on Front would take its extent from world X/Y and collapse
    into the same degenerate shape `make_box` used to build.

    The four sides run along the plane's x/y axes, which is exactly what
    `preview.rectangle_polyline` draws, so the preview and the committed geometry
    agree in every viewport. Built as a closed polyline (degree 1): the five
    points are the four corners plus the first corner again, giving a valid closed
    curve (verified: `IsClosed == True`).

    With `plane=None` this is the old world-XY behaviour.
    """
    if plane is None:
        u0, v0 = min(corner1[0], corner2[0]), min(corner1[1], corner2[1])
        u1, v1 = max(corner1[0], corner2[0]), max(corner1[1], corner2[1])
    else:
        u1c, v1c, _w1 = plane.from_world(corner1)
        u2c, v2c, _w2 = plane.from_world(corner2)
        u0, u1 = min(u1c, u2c), max(u1c, u2c)
        v0, v1 = min(v1c, v2c), max(v1c, v2c)
    pts = [plane.to_world(u, v) if plane else (u, v, 0.0) for u, v in
           [(u0, v0), (u1, v0), (u1, v1), (u0, v1), (u0, v0)]]
    return make_polyline(pts, degree=1)


def make_rectangle_3point(corner_a, corner_b, side_point, plane=None):
    """A closed rectangle from two ADJACENT corners + a point on the far side.

    This is the reference CAD application's `Rectangle 3Point` mode. (the command behavior):
    "3Point -- Draws the rectangle using two adjacent corner locations and a
    location on the opposite side."

    Two ADJACENT corners define one whole EDGE, at any angle to the CPlane axes,
    so the result is generally NOT axis-aligned and `make_rectangle` (which takes
    two OPPOSITE corners and runs its sides along the plane's x/y axes) cannot
    express it. That is why this is a separate builder: correctly refused to
    ship an axis-aligned stand-in.

    The maths is short and exact (from the sentence above):

    * the first edge is ``b - a``;
    * the width is the distance from `side_point` to the *LINE* through `a`,`b`
      -- not the distance to `b`. "A location on the opposite side" fixes which
      side the rectangle is on and how wide it is, and nothing more: how far
      along the edge that location happens to sit is irrelevant;
    * the other two corners are `a` and `b` displaced by that width along the
      in-plane perpendicular to the edge, on `side_point`'s side (the SIGNED dot
      product carries the side, so no branch is needed).

    All three points arrive as WORLD points (already resolved through the CPlane),
    so they are first expressed in the plane's own coordinates -- exactly as
    `make_rectangle` does -- and the perpendicular is then taken IN THE PLANE.
    A world-space perpendicular would need a normal and would tilt the rectangle
    out of the CPlane in Front/Right (the family of bugs). Any component of an
    input along the plane normal is dropped, which is the same projection
    `make_rectangle` applies.

    Built as a closed degree-1 polyline through the four corners plus the first
    corner again -- the SAME convention as `make_rectangle`, deliberately: showed that closure here means literally coincident seam points, so a second
    convention would be a seam bug waiting to happen.

    Refuses (the explicit-refusal policy: an unimplemented/impossible thing must not look built):

    * ``a == b`` -- no edge, so no rectangle;
    * `side_point` lying ON the line through `a`,`b` -- zero width. A
      zero-width "rectangle" is a degenerate double line, not a rectangle, so
      this raises rather than building one.

    `tol` is rhino3dm has no documented degeneracy threshold for this, and
    the document's absolute tolerance (0.01 mm) is far too coarse -- it would
    refuse legitimately thin rectangles. 1e-9 is a pure numerical-degeneracy
    guard, well below any modelling tolerance.
    """
    tol = 1e-9                                   # - see the docstring

    def local(pt):
        if plane is None:
            return float(pt[0]), float(pt[1])    # world XY, z dropped
        u, v, _w = plane.from_world(pt)
        return u, v

    ax, ay = local(corner_a)
    bx, by = local(corner_b)
    cx, cy = local(side_point)

    ex, ey = bx - ax, by - ay
    edge = math.hypot(ex, ey)
    if edge < tol:
        raise ValueError(
            "3Point rectangle: the two adjacent corners must differ "
            f"(they are {edge:.3g} apart)")

    # in-plane perpendicular to the edge, unit length
    px, py = -ey / edge, ex / edge
    # SIGNED distance from `side_point` to the LINE through a,b -- sign = side
    width = (cx - ax) * px + (cy - ay) * py
    if abs(width) < tol:
        raise ValueError(
            "3Point rectangle: the third point lies on the line through the "
            f"two corners (width {width:.3g}), so there is no rectangle to build")

    dx, dy = px * width, py * width
    corners = [(ax, ay), (bx, by), (bx + dx, by + dy), (ax + dx, ay + dy),
               (ax, ay)]                          # closed: first corner repeated
    pts = [plane.to_world(u, v) if plane else (u, v, 0.0) for u, v in corners]
    return make_polyline(pts, degree=1)


def make_rounded_rectangle(corners, radius):
    """A closed rectangle whose corners are rounded with TRUE arcs of `radius`.

    (the command behavior): "Rounded -- Rounds the corners of the rectangle
    with arcs or conic sections", sub-option `Corner=Arc`: "Rounded corners are
    circular arcs." This builds the Arc form (Conic is not built).

    `corners` are the four WORLD corners in order around the rectangle (what
    `make_rectangle` / `make_rectangle_3point` produce), so any orientation works and no
    plane is needed: at a right-angle corner C with unit edge directions a, b the arc's
    tangent points are C + r a and C + r b, its centre C + r (a + b). Each arc is a
    rational degree-2 NURBS (`r3.Arc` -> `ToNurbsCurve`), never a polyline.

    Returns a closed `PolyCurve` (line, arc, line, arc,...). `radius == 0` is the plain
    rectangle. Raises ValueError for a radius or one larger than half the
    shorter side (the corners would overlap).
    """
    r = float(radius)
    pts = [tuple(float(c) for c in p) for p in corners]
    if len(pts) != 4:
        raise ValueError("a rounded rectangle needs its four corners")
    sides = [math.dist(pts[i], pts[(i + 1) % 4]) for i in range(4)]
    if r < 0:
        raise ValueError("Rounded rectangle: the corner radius cannot be negative")
    if r > min(sides) / 2.0 + 1e-12:
        raise ValueError(
            f"Rounded rectangle: radius {r:g} is larger than half the shorter side "
            f"({min(sides) / 2.0:g})")
    if r == 0.0:
        return make_polyline(pts + [pts[0]], degree=1)

    def unit(a, b):
        d = math.dist(a, b)
        return tuple((b[k] - a[k]) / d for k in range(3))

    def at(p, u, s):
        return tuple(p[k] + u[k] * s for k in range(3))

    arcs = []                     # (start tangent point, arc, end tangent point)
    for i in range(4):
        c, prev, nxt = pts[i], pts[i - 1], pts[(i + 1) % 4]
        a, b = unit(c, prev), unit(c, nxt)
        t0, t1 = at(c, a, r), at(c, b, r)
        centre = tuple(c[k] + r * (a[k] + b[k]) for k in range(3))
        mid = at(centre, unit(centre, c), r)
        arc = r3.Arc(r3.Point3d(*t0), r3.Point3d(*mid), r3.Point3d(*t1)).ToNurbsCurve()
        arcs.append((t0, arc, t1))
    pc = r3.PolyCurve()
    for i in range(4):
        _t0, arc, t1 = arcs[i]
        pc.Append(arc)
        nxt_t0 = arcs[(i + 1) % 4][0]
        if math.dist(t1, nxt_t0) > 1e-12:          # a side shorter than 2r has no line
            pc.Append(r3.LineCurve(r3.Point3d(*t1), r3.Point3d(*nxt_t0)))
    return pc


def make_ellipse(center, axis1, axis2, plane=None):
    """An ellipse: `center` + the two ENDPOINTS of its semi-axes.

    `axis1` and `axis2` are world points, each the end of one semi-axis from `center`. the reference CAD application's default flow is "pick the center, pick the end of the
    first axis, pick the end of the second axis" (the command behavior), so this
    is the natural signature.

    rhino3dm 8.35.0 has NO `Ellipse` constructor (the type exists but
    cannot be instantiated, and `TryGetEllipse` on a scaled circle returns None),
    so the working route -- verified: `PointAt` evaluates to true ellipse points
    -- is an affine map of a unit circle. A unit circle in world XY is
    ``(cos t, sin t, 0)``; the affine sends that to
    ``center + cos t * u + sin t * v`` where ``u``/``v`` are the two semi-axis
    vectors. The matrix rows are therefore the three components of u, of v, and
    of the zero z-axis (the circle is flat), with the translation column the
    center. `Transform` Mij fields are settable.

    `plane` is accepted for signature parity with the other builders but is not
    used: the axes are already world vectors, so the ellipse is fully 3D and
    inherently plane-independent.
    """
    u = (axis1[0] - center[0], axis1[1] - center[1], axis1[2] - center[2])
    v = (axis2[0] - center[0], axis2[1] - center[1], axis2[2] - center[2])
    crv = r3.Circle(r3.Point3d(0.0, 0.0, 0.0), 1.0).ToNurbsCurve()
    t = r3.Transform.Identity()
    t.M00 = u[0]; t.M01 = v[0]; t.M02 = 0.0; t.M03 = center[0]
    t.M10 = u[1]; t.M11 = v[1]; t.M12 = 0.0; t.M13 = center[1]
    t.M20 = u[2]; t.M21 = v[2]; t.M22 = 0.0; t.M23 = center[2]
    t.M30 = 0.0;  t.M31 = 0.0;  t.M32 = 0.0;  t.M33 = 1.0
    crv.Transform(t)
    return crv


def make_polygon(center, radius, side_count, inscribed=True, plane=None):
    """A regular polygon of `radius` and `side_count` sides at world `center`.

    `inscribed` (the reference CAD application's default) means the CORNERS touch an imaginary circle of
    the given radius; `circumscribed` means the MIDPOINTS of the sides do
    (the command behavior: "Inscribed -- the corners of the sides touch an
    imaginary circle of a specified radius"). The two differ by a factor of
    ``cos(pi/n)`` in the apothem, so the radius you type is the one the user
    meant, not a value we silently chose.

    Built with `Polyline.CreateInscribedPolygon` / `CreateCircumscribedPolygon`
    (both take a `Circle` and a side count, verified), converted to a closed
    NURBS curve, then mapped onto the active CPlane exactly as the other
    builders do.

    With `plane=None` the polygon lies in world XY, translated to `center`.
    """
    circle = r3.Circle(r3.Point3d(0.0, 0.0, 0.0), float(radius))
    if inscribed:
        pl = r3.Polyline.CreateInscribedPolygon(circle, int(side_count))
    else:
        pl = r3.Polyline.CreateCircumscribedPolygon(circle, int(side_count))
    crv = pl.ToNurbsCurve()
    return to_plane(crv, center, plane)


def make_arc(start, point_on_arc, end, plane=None):
    """An arc through three points: `start`, `point_on_arc`, `end`.

    This is the reference CAD application's default mode (the command behavior: "Pick the start point. Pick
    the end point. Pick a point on the arc"), and `r3.Arc` has a matching
    three-point constructor `Arc(startPoint, pointOnInterior, endPoint)`. `NurbsCurve.CreateFromArc` turns it into a curve.

    The three points are already world coordinates (resolved through the CPlane
    by the caller), so no `to_plane` is applied -- the arc is inherently 3D, in
    the plane those three points define.

    Raises `ValueError` when the points are collinear: they cannot define an arc,
    and the constructor would return a degenerate result we must not ship.
    """
    # the two edge vectors from `start`; their cross product is zero when the
    # three points are collinear, in which case no arc exists.
    e1 = (point_on_arc[0] - start[0], point_on_arc[1] - start[1], point_on_arc[2] - start[2])
    e2 = (end[0] - start[0], end[1] - start[1], end[2] - start[2])
    cx = e1[1] * e2[2] - e1[2] * e2[1]
    cy = e1[2] * e2[0] - e1[0] * e2[2]
    cz = e1[0] * e2[1] - e1[1] * e2[0]
    if (cx * cx + cy * cy + cz * cz) < 1e-18:
        raise ValueError("the three points are collinear and cannot define an arc")
    arc = r3.Arc(r3.Point3d(*start), r3.Point3d(*point_on_arc), r3.Point3d(*end))
    if arc is None or not arc.IsValid:
        raise ValueError("failed to build an arc from the three points")
    crv = r3.NurbsCurve.CreateFromArc(arc)
    if crv is None or not crv.IsValid:
        raise ValueError("failed to convert the arc to a curve")
    return crv


def _newell_normal(points):
    """The plane normal of a planar polygon/sample loop (Newell's method). Needed because rhino3dm has **no** `Curve.TryGetPlane` (the introspection
    methods are IsPlanar/IsCircle/IsArc/TryGetCircle/..., with no plane getter), yet an
    extrusion's direction IS the profile plane's normal -- `Extrusion.Create(crv, h, True)`
    extrudes along it, and a height reverses it (z 0..5 vs -5..0).

    Newell's method is used rather than one cross product of three points because it is
    stable for nearly-collinear samples, where a single cross product collapses.
    """
    nx = ny = nz = 0.0
    n = len(points)
    for i in range(n):
        ax, ay, az = points[i]
        bx, by, bz = points[(i + 1) % n]
        nx += (ay - by) * (az + bz)
        ny += (az - bz) * (ax + bx)
        nz += (ax - bx) * (ay + by)
    length = (nx * nx + ny * ny + nz * nz) ** 0.5
    if length < 1e-12:
        return None
    return (nx / length, ny / length, nz / length)


def profile_normal(crv, samples=48):
    """The normal of `crv`'s plane, or None when it is not usefully planar."""
    try:
        if crv.IsCircle():
            z = crv.TryGetCircle().Plane.ZAxis
            return (z.X, z.Y, z.Z)
    except Exception:  # noqa: BLE001
        pass
    try:
        d = crv.Domain
        pts = []
        for i in range(samples):
            t = d.T0 + (d.T1 - d.T0) * i / float(samples)
            p = crv.PointAt(t)
            pts.append((p.X, p.Y, p.Z))
        return _newell_normal(pts)
    except Exception:  # noqa: BLE001
        return None


def curve_recipe(crv):
    """Serialize a planar profile `crv` for a recipe, or None if it cannot be carried.

    Recognised BY TYPE first, because the type decides what OCCT builds and therefore
    whether the result is analytic. A circle rebuilt as a generic B-spline extrudes to a
    `SURFACEOFEXTRUSION` whose volume is
    **3.2 % wrong**, while the same circle rebuilt as a `Geom_Circle` extrudes to
    `{CYLINDER: 1, PLANE: 2}` with **0.00e+00** error. So a circle must travel as a circle.

    Control points are divided by their weight on the way out: a rhino3dm `Point4d` stores
    X/Y/Z **premultiplied by W** for curves as well as surfaces (- the corner pole
    of a radius-5 circle reads 5.0 but is really 5*sqrt(2) = 7.0711). Storing the raw values
    would make every rational profile silently too small.
    """
    try:
        if crv.IsCircle():
            ci = crv.TryGetCircle()
            c, z = ci.Center, ci.Plane.ZAxis
            return {"ctype": "circle", "radius": float(ci.Radius),
                    "center": [c.X, c.Y, c.Z], "normal": [z.X, z.Y, z.Z]}
    except Exception:  # noqa: BLE001
        pass
    try:
        # An ARC before the generic path: a trimmed circle keeps a pipe exactly a TORUS, where
        # a generic B-spline rail gave {BSPLINE: 1} and a volume 5.5e-05 % off. `IsCircle` is
        # False for an arc, so this needs its own branch rather than falling through.
        if crv.IsArc() and not crv.IsCircle():
            a = crv.TryGetArc()
            pl_, c_ = a.Plane, a.Center
            z, x = pl_.ZAxis, pl_.XAxis
            return {"ctype": "arc", "radius": float(a.Radius),
                    "center": [c_.X, c_.Y, c_.Z],
                    "normal": [z.X, z.Y, z.Z], "xaxis": [x.X, x.Y, x.Z],
                    "start": float(a.StartAngle), "end": float(a.EndAngle)}
    except Exception:  # noqa: BLE001
        pass
    try:
        pl = crv.TryGetPolyline()
        if pl is not None and len(pl) >= 2:
            return {"ctype": "polyline",
                    "points": [[pl[i].X, pl[i].Y, pl[i].Z] for i in range(len(pl))]}
    except Exception:  # noqa: BLE001
        pass
    if isinstance(crv, r3.PolyCurve):
        segs = _polycurve_segment_recipes(crv)
        if segs is not None:
            return segs[0] if len(segs) == 1 else {"ctype": "polycurve", "segments": segs}
    try:
        nc = crv if isinstance(crv, r3.NurbsCurve) else crv.ToNurbsCurve()
        poles, weights = [], []
        for i in range(len(nc.Points)):
            cp = nc.Points[i]
            w = cp.W if nc.IsRational else 1.0
            if abs(w) < 1e-15:
                return None
            poles.append([cp.X / w, cp.Y / w, cp.Z / w])
            weights.append(w)
        return {"ctype": "nurbs", "degree": int(nc.Degree), "poles": poles,
                "weights": weights,
                "knots": [nc.Knots[i] for i in range(len(nc.Knots))],
                "periodic": bool(nc.IsPeriodic)}
    except Exception:  # noqa: BLE001
        return None


def _polycurve_segment_recipes(crv):
    """Serialize a joined curve one segment at a time in head-to-tail order.

    Nested polycurves are flattened. Converting the whole curve to one NURBS can create
    internal C0 joints that OCCT handles poorly during extrusion, producing incorrect
    volume and folded display faces. Preserving one edge per segment retains the polycurve
    structure and produces an airtight result without folds. Returns ``None`` if any
    segment cannot be represented.
    """
    leaves = []

    def walk(pc):
        for i in range(int(pc.SegmentCount)):
            seg = pc.SegmentCurve(i)
            if isinstance(seg, r3.PolyCurve):
                walk(seg)
            elif seg is not None:
                leaves.append(seg)
    try:
        walk(crv)
    except Exception:  # noqa: BLE001
        return None
    if not leaves:
        return None
    out, prev_end = [], None
    for seg in leaves:
        a, b = seg.PointAtStart, seg.PointAtEnd
        if prev_end is not None and prev_end.DistanceTo(a) > prev_end.DistanceTo(b):
            seg = seg.Duplicate()
            seg.Reverse()
            a, b = b, a
        d = curve_recipe(seg)
        if d is None:
            return None
        out.append(d)
        prev_end = b
    return out


def make_extrusion(profile_curve, height):
    """Extrude a closed planar curve straight up by `height`.

    Records an OCCT recipe so subsequent Boolean operations remain analytic instead of
    falling back to a heavily faceted display-derived result.
    """
    e = r3.Extrusion.Create(profile_curve, float(height), True)
    brep = e.ToBrep(False)
    profile = curve_recipe(profile_curve)
    normal = profile_normal(profile_curve)
    if profile is not None and normal is not None:
        h = float(height)
        _record(brep, "extrusion", r3.Transform.Identity(), profile=profile,
                dir=[normal[0] * h, normal[1] * h, normal[2] * h])
    return brep


def extrude_curve(curve, distance, direction=None, both=False, solid=True, draft_deg=None,
                  corners="Sharp", flip=False):
    """ExtrudeCrv with its options (BothSides, Solid, Direction) and the tapered
    form (ExtrudeCrvTapered: DraftAngle, Corners, FlipAngle) -- EXACT, via OCCT, with an
    `extrusion` recipe carrying the options so it booleans and reloads.

    `direction` (a unit vector) defaults to the command behavior: the curve plane's
    normal for a planar curve, else the caller's CPlane Z (pass it). An OPEN curve, or
    Solid=No, gives a surface; a closed planar curve with Solid=Yes a capped solid.
    Raises ValueError by name."""
    occt = _occt()
    if occt is None:
        raise ValueError("extruding with options needs the OCCT kernel, which is not "
                         "installed")
    profile = curve_recipe(curve)
    if profile is None:
        raise ValueError("the curve could not be carried to the kernel")
    if direction is None:
        direction = profile_normal(curve) if curve.IsClosed else None
        if direction is None:
            raise ValueError("a non-planar or open curve needs an extrusion direction")
    d = float(distance)
    if d == 0.0:
        raise ValueError("the extrusion distance must not be zero")
    vec = [direction[0] * d, direction[1] * d, direction[2] * d]
    recipe = {"kind": "extrusion", "profile": profile, "dir": vec, "both": bool(both),
              "solid": bool(solid)}
    if draft_deg not in (None, 0, 0.0):
        recipe.update(draft=float(draft_deg), corners=str(corners), flip=bool(flip))
    try:
        shape = occt.from_recipe(recipe)
    except Exception as e:  # noqa: BLE001
        raise ValueError(str(e)) from e
    return occt_backed(shape, recipe)


def make_revolve(profile_curve, axis_p1, axis_p2, start_deg=0.0, end_deg=360.0):
    import math

    profile = profile_curve if isinstance(profile_curve, r3.Curve) else profile_curve
    axis = r3.Line(r3.Point3d(*axis_p1), r3.Point3d(*axis_p2))
    rev = r3.RevSurface.Create(
        profile, axis, math.radians(start_deg), math.radians(end_deg))
    return r3.Brep.CreateFromRevSurface(rev, False, False)


def make_loft(curve_a, curve_b):
    """Ruled (straight) loft between two curves — the reference CAD application Loft MVP."""
    return r3.NurbsSurface.CreateRuledSurface(curve_a, curve_b)


def loft_brep(sections, style="Straight", closed=False):
    """Loft a Brep through N section curves (extended by ).

    Styles (the command behavior):
    * **Straight** -- a ruled (piecewise-linear in V) surface through all sections.
      Two sections use `CreateRuledSurface` (exact); N>2 build a degree-1-in-V net.
    * **Loose** -- "the surface control points are created at the same locations as
      the control points of the original": the sections' control nets ARE the
      surface's V control rows (degree min(k-1, 3) in V, uniform knots). This is a
      literal constructive spec, so it is built exactly where the sections are
      compatible.
    * **Normal / Tight / Uniform** -- described in the help only by feel ("average
      stretching", "closely follows", "uniform knots") with no measurable algorithm,
      so each is REFUSED by name rather than guessed.

    N sections of DIFFERENT degree/knots are first made compatible (reparametrize
    to [0,1], degree-elevate to the max, merge knot vectors) -- all three rhino3dm
    primitives exist (`Curve.Domain` setter, `NurbsCurve.IncreaseDegree`,
    `NurbsKnotList.InsertKnot`). The result is EXACT (no sampling): a `Loose` loft of
    two identical circles reproduces their control points bit-for-bit, and a degree-1
    `Straight` surface passes through every section because a degree-1 curve
    interpolates its control points.

    Raises ValueError for <2 sections, a closed loft, or a non-buildable style.
    """
    st = style.lower()
    if st not in ("straight", "loose", "smooth"):
        raise ValueError(
            f"loft style '{style}' not implemented; buildable styles are 'Straight' "
            f"(ruled), 'Loose' (control-point) and 'Smooth' (interpolating) -- "
            f"the reference CAD application's 'Normal', 'Tight' and 'Uniform' are described by feel, not "
            f"algorithm")
    if closed:
        raise ValueError("closed loft not implemented")
    if len(sections) < 2:
        raise ValueError(f"loft needs at least 2 sections (got {len(sections)})")

    if st == "straight" and len(sections) == 2:
        rs = r3.NurbsSurface.CreateRuledSurface(sections[0], sections[1])
        if rs is None:
            raise ValueError("CreateRuledSurface failed for the given sections")
        brep = r3.Brep.CreateFromSurface(rs)
        if brep is None:
            raise ValueError("Brep.CreateFromSurface failed")
        record_exact_recipe(brep)
        return brep

    nurbs = _loft_compatible_sections(sections)
    if st == "smooth":
        # INTERPOLATE across the sections, so the surface passes through every one
        # AND is smooth (degree 3) in V. This is what a swept surface needs; see
        # `_skin_surface_from_sections`. This is named 'Smooth' rather than 'Normal'
        # because the latter's behavior is not specified precisely enough to claim a match.
        surf = _skin_surface_from_sections(nurbs, 3)
    else:
        degree_v = 1 if st == "straight" else min(len(nurbs) - 1, 3)
        surf = _loft_surface_from_net(nurbs, degree_v)
    brep = r3.Brep.CreateFromSurface(surf)
    if brep is None:
        raise ValueError("Brep.CreateFromSurface failed on the lofted net")
    # every loft style is already an EXACT NURBS construction (ruled, control-point
    # net, interpolating skin), so its recipe is the RESULT's own faces -- an OCCT master
    # built from them bit-for-bit, which booleans (after Cap), measures and survives a
    # reload. OCCT's ThruSections matches Straight but not Smooth, where it produces an
    # approximating skin, so
    # rebuilding through ThruSections would have silently changed a Smooth loft.
    record_exact_recipe(brep)
    return brep


def _loft_compatible_sections(sections):
    """NurbsCurve copies of `sections` made COMPATIBLE: reparametrized to [0,1],
    degree-elevated to the common max, and knot-merged so all share one knot vector
    and CV count. (`Curve.Domain` setter, `IncreaseDegree`, `Knots.InsertKnot` all
    present in rhino3dm 8.35.)"""
    nurbs = []
    for c in sections:
        nc = c if isinstance(c, r3.NurbsCurve) else c.ToNurbsCurve()
        nc = nc.Duplicate()
        nc.Domain = r3.Interval(0.0, 1.0)
        nurbs.append(nc)
    max_deg = max(nc.Degree for nc in nurbs)
    for nc in nurbs:
        if nc.Degree < max_deg:
            nc.IncreaseDegree(max_deg)

    def interior_mults(nc):
        d = nc.Domain
        m = {}
        for v in nc.Knots.ToList():
            if d.T0 + 1e-9 < v < d.T1 - 1e-9:
                key = round(v, 9)
                m[key] = m.get(key, 0) + 1
        return m

    target = {}
    for nc in nurbs:
        for v, mu in interior_mults(nc).items():
            target[v] = max(target.get(v, 0), mu)
    for nc in nurbs:
        cur = interior_mults(nc)
        for v, mu in target.items():
            add = mu - cur.get(v, 0)
            if add > 0:
                nc.Knots.InsertKnot(v, add)
    return nurbs


def _clamped_uniform_knots(n, degree):
    """openNURBS clamped-uniform knot vector for `n` CVs of `degree` (length
    n+degree-1: `degree` zeros, interior 1..n-degree-1, `degree` copies of n-degree)."""
    knots = [0.0] * degree
    knots += [float(i) for i in range(1, n - degree)]
    knots += [float(n - degree)] * degree
    return knots


def _loft_surface_from_net(nurbs, degree_v):
    """Build a NurbsSurface whose V control rows are the (compatible) sections."""
    k = len(nurbs)
    degree_v = max(1, min(degree_v, k - 1))
    cv_u = len(list(nurbs[0].Points))
    degree_u = nurbs[0].Degree
    rational = any(bool(nc.IsRational) for nc in nurbs)
    surf = r3.NurbsSurface.Create(3, rational, degree_u + 1, degree_v + 1, cv_u, k)
    u_knots = list(nurbs[0].Knots.ToList())
    for i, val in enumerate(u_knots):
        surf.KnotsU[i] = val
    v_knots = _clamped_uniform_knots(k, degree_v)
    for j, val in enumerate(v_knots):
        surf.KnotsV[j] = val
    for j, nc in enumerate(nurbs):
        pts = list(nc.Points)
        for i in range(cv_u):
            cp = pts[i]                       # NurbsCurve Point4d: X/Y/Z already WEIGHTED
            w = getattr(cp, "W", 1.0) or 1.0
            surf.Points[i, j] = r3.Point4d(cp.X, cp.Y, cp.Z, w)
    return surf


def _interpolate():
    try:
        from . import interpolate
    except ImportError:  # support direct-file loading outside the package
        import interpolate
    return interpolate


def _skin_surface_from_sections(nurbs, degree_v=3):
    """A SMOOTH surface that PASSES THROUGH every section.

    The difference from:func:`_loft_surface_from_net` is the whole point: that one puts
    the sections' control points AT the surface's control points, so for degree_v > 1 the
    surface does NOT pass through the sections (that is the reference CAD application's `Loose`). This one
    INTERPOLATES across V, so the surface contains every section exactly AND is degree 3
    in the sweep direction.

    WHY IT EXISTS. A degree-1 V loft along a curved rail produces a fan of flat strips
    rather than a smooth swept surface. Interpolating the sampled stations in V preserves
    the sections while providing the required sweep-direction smoothness.

    WEIGHTS. rhino3dm stores a `Point4d` control point with X/Y/Z **already multiplied by
    W** (both for curves and for surfaces -- the sibling builder above relies on the same
    fact). Interpolation is a linear operation on EUCLIDEAN points, so each pole is
    divided by its weight on the way in and re-multiplied on the way out. Skipping that
    step silently shrinks every rational section.

    All sections must share a weight pattern, which is automatic for a sweep (the section
    is moved rigidly, so its weights travel with it). Differing weights are REFUSED by
    name rather than averaged into a shape nobody asked for.
    """
    interp = _interpolate()
    k = len(nurbs)
    cv_u = len(list(nurbs[0].Points))
    degree_u = nurbs[0].Degree
    rational = any(bool(nc.IsRational) for nc in nurbs)

    rows = []          # rows[j][i] = euclidean control point i of section j
    weights = None
    for j, nc in enumerate(nurbs):
        pts = list(nc.Points)
        euclid, ws = [], []
        for i in range(cv_u):
            cp = pts[i]
            w = getattr(cp, "W", 1.0) or 1.0
            euclid.append((cp.X / w, cp.Y / w, cp.Z / w))
            ws.append(w)
        if weights is None:
            weights = ws
        elif any(abs(a - b) > 1e-9 for a, b in zip(weights, ws)):
            raise ValueError(
                "skinned loft: the sections have different control-point WEIGHTS, so a "
                "single rational surface cannot pass through all of them; this is not "
                "implemented (a sweep's sections always share weights)")
        rows.append(euclid)

    net, v_knots_full, degree_v = interp.skin_control_net(rows, degree_v)

    surf = r3.NurbsSurface.Create(3, rational, degree_u + 1, degree_v + 1, cv_u, k)
    for i, val in enumerate(list(nurbs[0].Knots.ToList())):
        surf.KnotsU[i] = val
    # rhino3dm's knot array omits the first and last of the full clamped vector
    interior = v_knots_full[1:-1]
    if len(interior) != len(surf.KnotsV):
        raise ValueError(
            f"skinned loft: built {len(interior)} V knots but rhino3dm wants "
            f"{len(surf.KnotsV)} for {k} rows at degree {degree_v}")
    for j, val in enumerate(interior):
        surf.KnotsV[j] = float(val)
    for i in range(cv_u):
        w = weights[i]
        for j in range(k):
            ex, ey, ez = net[i][j]
            surf.Points[i, j] = r3.Point4d(ex * w, ey * w, ez * w, w)
    return surf


# ---------------------------------------------------------------------------
# Boolean operations (routed through Blender's mesh solver)
#
# rhino3dm has no booleans. We tessellate both inputs to welded meshes, run a
# BOOLEAN modifier with the EXACT solver, and convert the result back to a Brep
# via CreateFromMesh. The result is therefore a *mesh-* brep: it loses
# analytic surfaces (a union of two boxes comes back as faceted NURBS patches,
# not flat planes). Documented tradeoff — see README "Known gaps".
#
# Two verified gotchas that silently break the EXACT solver:
#   1. Duplicate vertices make CreateFromMesh hard-crash under Blender, so both
#      inputs and the output must be welded first (Blender 5.x: merge_by_dist).
#   2. The solver needs consistent outward normals. brep_to_bmesh recalcs
#      normals *before* welding, on still-non-manifold topology, which leaves
#      some faces pointing inward -> wrong result with no error. So we weld,
#      THEN recalc normals.
# ---------------------------------------------------------------------------

BOOLEAN_NU = 32  # tessellation density for boolean inputs (see note above)


def _weld(bm, dist: float = 1e-6):
    """Weld coincident verts. Blender 5.x renamed remove_doubles -> merge_by_dist."""
    import bmesh

    if hasattr(bmesh.ops, "merge_by_dist"):
        bmesh.ops.merge_by_dist(bm, verts=bm.verts, dist=dist)
    else:  # older Blender
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=dist)


def _welded_brep_mesh(geom, nu: int = BOOLEAN_NU):
    """Tessellate a brep to a welded, outward-normalled bmesh (boolean / cap input).

    ADAPTIVE + ear-clipped planar faces: a PLANAR face is triangulated inside
    its boundary (`tessellate.ear_clip`) with NO interior grid, and a CURVED face gets
    an `_iso_segments` grid sized to the chord tolerance -- the SAME path the display
    (`geometry_to_bmesh`) uses. This replaces a blanket `BOOLEAN_NU x BOOLEAN_NU` grid
    that turned a 6-face box into thousands of triangles. The adaptive route keeps a
    box union compact, samples curved operands densely enough to remain round, and produces
    watertight solver input. ``nu`` remains for signature compatibility and as the
    grid density the adaptive path still falls back to for a face it cannot ear-clip.
    """
    bm = brep_to_bmesh(geom, nu, nu, trim_planar=True, adaptive=True)
    _weld(bm)  # weld FIRST so the recalc below sees manifold topology
    import bmesh

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return bm


class ProxyBuildError(RuntimeError):
    """The analytic result was fine but its DISPLAY mesh could not be built.

    A separate type from `ValueError` on purpose. `boolean_occt` re-raises `ValueError`,
    because that is how a NAMED user-facing refusal reaches the command line (e.g. "the
    result is 2 separate solids"). An internal display failure must instead DECLINE, so the
    mesh route still produces something the user can see. Conflating the two made a proxy
    failure abort the whole boolean -- the first boolean-of-a-boolean raised
    "could not build a display proxy" straight out of the command.
    """


def _kernel():
    try:
        from . import kernel
    except ImportError:  # support direct-file loading outside the package
        import kernel
    return kernel


def _occt():
    """The OCCT backend module, or None when the kernel is not installed."""
    try:
        try:
            from .kernel import occt_backend
        except ImportError:
            from kernel import occt_backend
    except Exception:  # noqa: BLE001
        return None
    return occt_backend if occt_backend.available() else None


def _xform_matrix(xf):
    """A row-major 16-float list from an `r3.Transform`, in DOUBLE precision.

    Read off the `M00..M33` accessors rather than `ToFloatArray`, which is to be
    single precision -- a float32 transform would put the OCCT master a few microns from
    the rhino3dm solid, and microns are exactly what this project cannot afford to lose.
    """
    return [getattr(xf, f"M{r}{c}") for r in range(4) for c in range(4)]


def _plane_xform(center, plane):
    """The transform `to_plane` would apply -- captured so a recipe can reproduce it."""
    if plane is None:
        return r3.Transform.Translation(float(center[0]), float(center[1]),
                                        float(center[2]))
    return r3.Transform.PlaneToPlane(_world_xy_at_origin(), _r3_plane(center, plane))


def _record(geom, kind, xf, **params):
    """Tag `geom` ANALYTIC and record the recipe that rebuilds it in OCCT."""
    k = _kernel()
    k.tag(geom, k.ANALYTIC)
    recipe = dict(params)
    recipe["kind"] = kind
    recipe["xform"] = _xform_matrix(xf)
    k.record_recipe(geom, recipe)
    return geom


def _occt_operand(geom):
    """The OCCT shape for `geom`: its registered master, or rebuilt from its recipe.

    Returns None when neither is available, which is how an OCCT boolean declines rather
    than guessing. The MASTER is preferred so booleans COMPOSE -- the result of a boolean
    has no primitive recipe, only a master, and that is precisely the case a recipe-only
    scheme would lose.
    """
    occt = _occt()
    if occt is None:
        return None
    k = _kernel()
    master = k.master_of(geom)
    if master is not None:
        return master
    recipe = k.recipe_of(geom)
    if not recipe:
        return None
    try:
        return occt.from_recipe(recipe)
    except Exception:  # noqa: BLE001
        return None


def curve_from_recipe(d):
    """An `r3.NurbsCurve` from a serialized curve dict (the inverse of `curve_recipe`).

    THE CONVENTION, and it runs the opposite way to `curve_recipe`: the serialized poles are
    EUCLIDEAN (that is how OCCT stores them), while rhino3dm's `Point4d` wants them
    **PREMULTIPLIED by W**. So this MULTIPLIES where `curve_recipe` divided. Getting it backwards would shrink every rational curve by its
    weight and nothing would raise -- the same silent class of bug as the 15 %-too-small
    surface.

    also the TYPED forms `occt_backend.face_loops` now emits -- ``line`` and
    ``arc`` (a full circle is an arc of 2pi, its seam at the start angle) -- each rebuilt
    EXACTLY (an arc is the rational quadratic, not a fit).
    """
    ctype = d.get("ctype", "nurbs")
    if ctype == "polycurve":            # a joined curve, segment by segment
        pc = r3.PolyCurve()
        for s in d["segments"]:
            pc.Append(curve_from_recipe(s))
        return pc
    if ctype == "line":
        return r3.LineCurve(r3.Point3d(*d["a"]), r3.Point3d(*d["b"])).ToNurbsCurve()
    if ctype == "polyline":
        return r3.PolylineCurve([r3.Point3d(*p) for p in d["points"]]).ToNurbsCurve()
    if ctype in ("arc", "circle"):
        import math as _m
        import types as _t

        c, n, r = d["center"], d["normal"], float(d["radius"])
        x = d.get("xaxis") or _any_perpendicular(n)
        y = [n[1] * x[2] - n[2] * x[1], n[2] * x[0] - n[0] * x[2], n[0] * x[1] - n[1] * x[0]]
        t0, t1 = float(d.get("start", 0.0)), float(d.get("end", 2 * _m.pi))

        def at(t):
            return tuple(c[i] + r * (_m.cos(t) * x[i] + _m.sin(t) * y[i]) for i in range(3))
        if ctype == "circle" or abs((t1 - t0) - 2 * _m.pi) < 1e-9:
            xs = [_m.cos(t0) * x[i] + _m.sin(t0) * y[i] for i in range(3)]
            ys = [n[1] * xs[2] - n[2] * xs[1], n[2] * xs[0] - n[0] * xs[2],
                  n[0] * xs[1] - n[1] * xs[0]]
            return make_circle(tuple(c), r, _t.SimpleNamespace(xaxis=tuple(xs),
                                                                yaxis=tuple(ys)))
        return r3.NurbsCurve.CreateFromArc(r3.Arc(r3.Point3d(*at(t0)),
                                                  r3.Point3d(*at((t0 + t1) / 2)),
                                                  r3.Point3d(*at(t1))))
    poles = d["poles"]
    weights = d.get("weights") or [1.0] * len(poles)
    degree = int(d["degree"])
    rational = any(abs(w - 1.0) > 1e-12 for w in weights)
    curve = r3.NurbsCurve(3, rational, degree + 1, len(poles))
    for i, (q, w) in enumerate(zip(poles, weights)):
        curve.Points[i] = r3.Point4d(q[0] * w, q[1] * w, q[2] * w, w)
    knots = d["knots"]
    if len(knots) != len(curve.Knots):
        raise ValueError(
            f"curve_from_recipe: {len(knots)} knots but rhino3dm wants "
            f"{len(curve.Knots)} for {len(poles)} poles at degree {degree}")
    for i, kv in enumerate(knots):
        curve.Knots[i] = float(kv)
    if not curve.IsValid:
        raise ValueError("curve_from_recipe produced an invalid curve")
    return curve


def _any_perpendicular(n):
    a = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
    v = [n[1] * a[2] - n[2] * a[1], n[2] * a[0] - n[0] * a[2], n[0] * a[1] - n[1] * a[0]]
    m = sum(t * t for t in v) ** 0.5
    return [t / m for t in v]


def visible_outline_curves(geoms, normal, xaxis):
    """per geometry, the `r3.NurbsCurve`s a parallel view of it SHOWS -- visible
    edges, tangent edges and silhouette outlines, hidden lines removed, every geometry
    hiding the others -- or None for one with no exact OCCT shape (master or recipe).
    See `occt_backend.visible_outlines`. Returns None for all when OCCT is missing."""
    occt = _occt()
    if occt is None:
        return [None] * len(geoms)
    shapes = [_occt_operand(g) for g in geoms]
    present = [s for s in shapes if s is not None]
    if not present:
        return [None] * len(geoms)
    per = iter(occt.visible_outlines(present, normal, xaxis))
    out = []
    for s in shapes:
        out.append(None if s is None else [curve_from_recipe(d) for d in next(per)])
    return out


def extract_wireframe(geom):
    """Return the analytic edge curves of a solid and their source.

    ``source`` is ``"master"`` for OCCT analytic edges or ``"brep"`` for rhino3dm brep
    edges. A master-backed union therefore yields its real arcs and lines rather than
    hundreds of display-triangle edges.
    """
    k = _kernel()
    master = k.master_of(geom)
    occt = _occt()
    if master is not None and occt is not None:
        out = []
        for d in occt.edge_curves(master):
            try:
                out.append(curve_from_recipe(d))
            except Exception:  # noqa: BLE001
                continue
        return out, "master"
    # No master: fall back to the brep's own `Brep.Edges`, which works for a primitive
    # even without a kernel.
    out = []
    edges = getattr(geom, "Edges", None)
    if edges is not None:
        for i in range(len(edges)):
            try:
                ec = edges[i].ToNurbsCurve()
                if ec is not None:
                    out.append(ec)
            except Exception:  # noqa: BLE001
                continue
    return out, "brep"


def explode_brep(geom):
    """Split a polysurface into its SURFACES -- the reference CAD application's `Explode`.

    (the command behavior, results table): *"Polysurface -- Surfaces."* Returns
    ``(pieces, source)`` with `source` ``"master"`` or ``"brep"``, or None when there is
    nothing to explode (fewer than two faces -- a single surface is not a polysurface).

    TWO ROUTES, chosen by what the GEOMETRY is, never by what is convenient:

    * **A master-backed solid** (boolean, fillet, pipe, cap) explodes from its MASTER. Its
      document object is a triangulated display proxy, so duplicating the PROXY's faces would
      hand back hundreds of triangles and call them surfaces -- the mistake the master/proxy
      split keeps inviting. Each OCCT face becomes its own master + proxy, with a `face`
      recipe naming its parent, so a piece survives save/reload and moves correctly.
    * **A plain multi-face brep** (box, cylinder, extrusion...) explodes NATIVELY with
      `BrepFace.DuplicateFace(False)`. a cylinder's caps come back as real TRIMMED
      faces -- every edge point at the radius, not at the 2*sqrt(2)*r a square plane would
      reach -- and they survive a.3dm round-trip. No kernel is involved, so this route works
      without OCCT at all.

    rhino3dm cannot construct a new trimmed face, but that does not block these routes. The
    master route builds through OCCT, while the native route duplicates an existing face
    rather than constructing one.
    """
    k = _kernel()
    occt = _occt()
    master = k.master_of(geom)
    if master is not None and occt is not None:
        faces = occt.unique_faces(master)
        if len(faces) < 2:
            return None
        parent = k.recipe_of(geom)
        out = []
        for index, face in enumerate(faces, start=1):
            proxy = _proxy_from_occt(face)
            k.register_master(proxy, face)
            if parent:
                k.record_recipe(proxy, {"kind": "face", "index": index, "a": parent})
            out.append(proxy)
        return out, "master"
    faces = getattr(geom, "Faces", None)
    if faces is None:
        return None
    try:
        count = len(faces)
    except Exception:  # noqa: BLE001
        return None
    if count < 2:
        return None
    out = []
    for i in range(count):
        piece = faces[i].DuplicateFace(False)
        if piece is not None:
            out.append(piece)
    return (out, "brep") if len(out) >= 2 else None


# --------------------------------------------------------------------------
# sub-object PARTS (faces / edges / vertices) for Ctrl+Shift picking
# --------------------------------------------------------------------------

class SubObjectError(ValueError):
    """A sub-object that does not exist (bad kind, id out of range), refused by name."""


SUBOBJECT_KINDS = ("face", "edge", "vertex")


def _subobject_source(geom):
    """``(source, topology)`` for `geom`'s parts -- ``("master"|"recipe", occt_shape)`` or
    ``("brep", r3.Brep)`` -- or ``(None, None)`` when `geom` has no faces (a curve, a
    point, a mesh).

    The ORDER is the whole point. A master-backed solid's document object may be a
    triangulated proxy, and counting ITS faces would hand the user hundreds of triangles
    to pick from (the mistake the master/proxy split keeps inviting, cf. `explode_brep`).
    So: the MASTER, else the shape rebuilt from the RECIPE (a primitive; same topology the
    boolean and STEP routes see), else the r3.Brep's own exact topology.
    """
    if not isinstance(geom, (r3.Brep, r3.Extrusion, r3.Surface)):
        return None, None
    k = _kernel()
    if _occt() is not None:
        source = "master" if k.master_of(geom) is not None else "recipe"
        shape = _occt_operand(geom)
        if shape is not None:
            return source, shape
    if isinstance(geom, r3.Brep):
        return "brep", geom
    if isinstance(geom, r3.Extrusion):
        return "brep", geom.ToBrep(True)
    return "brep", r3.Brep.CreateFromSurface(geom)


def _brep_face_triangles(brep, face):
    """Hit-test triangles for one rhino3dm brep face (the no-master route). A planar face
    with one loop is ear-clipped INSIDE its trim (a circular cap is a disc, not its square
    surface); anything else is an adaptive grid over the face's surface domain."""
    try:
        from . import tessellate
    except ImportError:  # support direct-file loading outside the package
        import tessellate

    def xyz(p):
        return (p.X, p.Y, p.Z)

    try:
        planar = face.IsPlanar() and len(face.Loops) == 1
    except Exception:  # noqa: BLE001
        planar = False
    if planar:
        pts = _loop_points_3d(brep, face.OuterLoop)
        if len(pts) >= 3:
            du, dv = face.Domain(0), face.Domain(1)
            n = face.NormalAt((du.T0 + du.T1) / 2.0, (dv.T0 + dv.T1) / 2.0)
            u_axis, v_axis = tessellate.plane_basis((n.X, n.Y, n.Z))
            tris = tessellate.ear_clip(tessellate.project_to_plane(pts, pts[0], u_axis,
                                                                   v_axis))
            if len(tris) == len(pts) - 2:
                return [(pts[i], pts[j], pts[k]) for i, j, k in tris]
    us = _surface_axis_params(face, 0, CURVE_CHORD_TOL)
    vs = _surface_axis_params(face, 1, CURVE_CHORD_TOL)
    grid = _grid_points(face, us, vs)
    out = []
    for i in range(len(us) - 1):
        for j in range(len(vs) - 1):
            a, b = xyz(grid[i][j]), xyz(grid[i + 1][j])
            c, d = xyz(grid[i + 1][j + 1]), xyz(grid[i][j + 1])
            out.extend([(a, b, c), (a, c, d)])
    return out


def _brep_edge_polyline(edge):
    """Samples of a rhino3dm brep edge's exact curve: the two ends if it is straight, else
    as many as the display chord tolerance needs."""
    try:
        steps = 1 if edge.IsLinear() else _edge_display_segments(edge, CURVE_CHORD_TOL)
    except Exception:  # noqa: BLE001
        steps = 64
    d = edge.Domain
    out = []
    for i in range(steps + 1):
        p = edge.PointAt(d.T0 + (d.T1 - d.T0) * i / steps)
        out.append((p.X, p.Y, p.Z))
    return out


def subobject_parts(geom):
    """The faces, edges, and vertices of a surface, polysurface, or solid for
    Ctrl+Shift sub-object picking. Returns None for geometry with no faces.

    Return shape -- WORLD coordinates, every point an ``(x, y, z)`` float tuple::

        {"faces":    [(face_id,   [(p, q, r),...]),...],   # triangles, hit-test only
         "edges":    [(edge_id,   [p0, p1,...]),...],      # polyline ON the exact curve
         "vertices": [(vertex_id, p),...],
         "source":   "master" | "recipe" | "brep"}

    Ids are positive ints, STABLE for an unchanged object (the same every call, and after a
    save + reload), and are exactly what `extract_subobject(geom, kind, id)` takes. Ids are
    per KIND (face 1 and edge 1 are different things) and need not be contiguous: a
    degenerated edge (a cone apex) is omitted rather than renumbering the rest.

    Topology comes from the MASTER / recipe shape (see `_subobject_source`), so a box minus
    a cylinder is its 7 real faces, never its proxy's triangles; a cylinder is 3 faces, 3
    edges (its seam and two circles, each ONE edge) and 2 vertices.

    Not cached: the caller should keep the result for an object until it changes.
    """
    source, topo = _subobject_source(geom)
    if source is None or topo is None:
        return None
    if source in ("master", "recipe"):
        parts = _occt().subobject_topology(topo)
        parts["source"] = source
        return parts
    faces = [(i + 1, _brep_face_triangles(topo, topo.Faces[i])) for i in range(len(topo.Faces))]
    edges = [(i + 1, _brep_edge_polyline(topo.Edges[i])) for i in range(len(topo.Edges))]
    verts = []
    for i in range(len(topo.Vertices)):
        p = topo.Vertices[i].Location
        verts.append((i + 1, (p.X, p.Y, p.Z)))
    return {"faces": faces, "edges": edges, "vertices": verts, "source": "brep"}


def extract_subobject(geom, kind, sub_id):
    """the EXACT geometry of one part of `geom` -- what the reference CAD application's ExtractSrf / DupEdge
    give. `kind` is ``"face"``, ``"edge"`` or ``"vertex"``; `sub_id` an id from
    `subobject_parts(geom)`.

    * ``"edge"``   -> an `r3.NurbsCurve` built from the exact edge (a circle is the reference CAD application's
      9-point rational quadratic, a straight edge a degree-1 line).
    * ``"face"``   -> a single-face `r3.Brep`. On the master/recipe route it carries a
      ``face`` recipe (``{"kind": "face", "index": id, "a": parent}``) and its own OCCT
      master, exactly as `explode_brep`'s pieces do, so it is exact, survives save/reload,
      booleans and moves. On the brep route it is `BrepFace.DuplicateFace` -- the real
      trimmed face, copied.
    * ``"vertex"`` -> an `r3.Point3d`.

    The source document object is never modified. Raises `SubObjectError` for an unknown
    kind, an id out of range, or geometry with no faces.
    """
    if kind not in SUBOBJECT_KINDS:
        raise SubObjectError(f"unknown sub-object kind {kind!r}; expected one of "
                             f"{', '.join(SUBOBJECT_KINDS)}")
    source, topo = _subobject_source(geom)
    if source is None or topo is None:
        raise SubObjectError(f"{type(geom).__name__} has no faces, edges or vertices to pick")
    i = int(sub_id)
    if source in ("master", "recipe"):
        occt = _occt()
        k = _kernel()
        try:
            if kind == "edge":
                return curve_from_recipe(occt.edge_at(topo, i))
            if kind == "vertex":
                return r3.Point3d(*occt.vertex_at(topo, i))
            face = occt.face_at(topo, i)
        except occt.KernelError as e:
            raise SubObjectError(str(e)) from e
        parent = k.recipe_of(geom)
        if parent:
            return occt_backed(face, {"kind": "face", "index": i, "a": parent})
        proxy = _proxy_from_occt(face)
        k.register_master(proxy, face)
        return proxy
    table = {"face": topo.Faces, "edge": topo.Edges, "vertex": topo.Vertices}[kind]
    if i < 1 or i > len(table):
        raise SubObjectError(f"{kind} id {i} out of range; this object has {len(table)} "
                             f"{kind}s numbered 1..{len(table)}")
    if kind == "face":
        return table[i - 1].DuplicateFace(False)
    if kind == "edge":
        return table[i - 1].ToNurbsCurve()
    return r3.Point3d(table[i - 1].Location.X, table[i - 1].Location.Y,
                      table[i - 1].Location.Z)


# --------------------------------------------------------------------------
# Move a sub-object of a solid
# --------------------------------------------------------------------------

#: A moved face's corners count as coplanar within this fraction of the solid's size.
#: Far below any visible bend, and well above double-precision noise.
SUBOBJECT_PLANAR_TOL = 1e-9


def _polyhedron_signed_volume(occt, verts, faces):
    """Signed volume of `faces` (planar_polyhedron loops, outer wound outward, holes
    opposite) over `verts`: sum of Newell(loop). p0 / 6. Positive for an outward solid."""
    total = 0.0
    for loops in faces:
        for loop in loops:
            pts = [verts[j - 1] for j in loop]
            n = occt._newell(pts)
            total += n[0] * pts[0][0] + n[1] * pts[0][1] + n[2] * pts[0][2]
    return total / 6.0


def _matrix_vector(matrix, vector):
    """The affine matrix's linear part applied to ``vector`` (no translation)."""
    return tuple(sum(matrix[r * 4 + c] * float(vector[c]) for c in range(3))
                 for r in range(3))


def _translation_matrix(vector):
    out = list(_IDENTITY16)
    out[3], out[7], out[11] = (float(c) for c in vector)
    return out


def _copy_geometry_user_strings(source, target):
    """Copy document metadata without copying representation/master bookkeeping."""
    getter = getattr(source, "GetUserStrings", None)
    if not callable(getter):
        return
    try:
        for key, value in getter() or ():
            if key not in _REPRESENTATION_KEYS:
                target.SetUserString(key, value)
    except Exception:  # noqa: BLE001
        pass


def _move_extruded_cap(geom, shape, kind, sub_id, vector, occt):
    """'s narrow exact case: stretch one end of a straight recipe extrusion.

    The construction recipe is the geometry authority.  Changing its height/direction and,
    for the base cap, its placement rebuilds the same profile with an analytically extended
    side surface.  No display-proxy vertex participates in the operation.
    """
    if kind != "face":
        raise ValueError("moving an edge or vertex of a solid with CURVED faces is not "
                         "implemented yet")
    recipe = _kernel().recipe_of(geom)
    recipe_kind = recipe.get("kind") if isinstance(recipe, dict) else None
    if recipe_kind not in ("cylinder", "extrusion"):
        raise ValueError("moving a part of this CURVED solid is not implemented yet -- only "
                         "a planar end cap of a direct cylinder or straight extrusion recipe "
                         "can be stretched exactly")
    if recipe_kind == "extrusion":
        if recipe.get("both"):
            raise ValueError("moving a cap of a BothSides extrusion is not implemented yet")
        if recipe.get("draft") not in (None, 0, 0.0):
            raise ValueError("moving a cap of a drafted extrusion is not implemented yet")
        if not bool(recipe.get("solid", True)):
            raise ValueError("the extrusion is not a closed solid")

    faces = occt.unique_faces(shape)
    i = int(sub_id)
    if i < 1 or i > len(faces):
        raise SubObjectError(f"face {i} is out of range; this solid has {len(faces)} faces "
                             f"numbered 1..{len(faces)}")
    selected = occt.face_plane(faces[i - 1])
    if selected is None:
        raise ValueError("moving a CURVED lateral wall is not implemented yet -- select one "
                         "planar end cap")

    matrix = recipe.get("xform") or _IDENTITY16
    if recipe_kind == "cylinder":
        local_axis = (0.0, 0.0, float(recipe["height"]))
    else:
        local_axis = tuple(float(c) for c in recipe["dir"])
    axis = _matrix_vector(matrix, local_axis)
    old_length = math.sqrt(sum(c * c for c in axis))
    if old_length <= 1e-12:
        raise ValueError("the extrusion axis has zero length")
    u = tuple(c / old_length for c in axis)

    caps = []
    for fid, face in enumerate(faces, start=1):
        plane = occt.face_plane(face)
        if plane is None:
            continue
        alignment = abs(sum(a * b for a, b in zip(plane[3], u)))
        if alignment >= 1.0 - 1e-8:
            centre = occt.surface_centroid(face)
            caps.append((sum(a * b for a, b in zip(centre, u)), fid, centre))
    caps.sort()
    if len(caps) != 2 or i not in (caps[0][1], caps[1][1]):
        raise ValueError("that planar face is not one of the extrusion's two end caps -- "
                         "moving side faces is not implemented yet")

    v = tuple(float(c) for c in vector)
    along = sum(a * b for a, b in zip(v, u))
    residual = math.sqrt(sum((a - along * b) ** 2 for a, b in zip(v, u)))
    tol = 1e-9 * max(1.0, old_length, math.sqrt(sum(c * c for c in v)))
    if residual > tol:
        raise ValueError("an end cap can only move parallel to the extrusion axis; a lateral "
                         "move would not stretch the joined surface exactly")
    if abs(along) <= tol:
        raise ValueError("the requested cap move has zero distance along the extrusion axis")

    moving_top = i == caps[1][1]
    new_length = old_length + along if moving_top else old_length - along
    if new_length <= tol:
        raise ValueError("that move would collapse the extrusion or turn it inside out")
    ratio = new_length / old_length
    updated = dict(recipe)
    if recipe_kind == "cylinder":
        updated["height"] = float(recipe["height"]) * ratio
    else:
        updated["dir"] = [float(c) * ratio for c in recipe["dir"]]
    if not moving_top:
        updated["xform"] = _mat_mul(_translation_matrix(v), matrix)

    try:
        solid = occt.from_recipe(updated)
    except _kernel().KernelError as e:
        raise ValueError(f"that cap move does not leave a valid exact extrusion ({e})") from e
    if not occt.is_airtight(solid):
        raise ValueError("that cap move did not leave one airtight solid")
    before_types, after_types = occt.face_types(shape), occt.face_types(solid)
    if after_types != before_types:
        raise ValueError(f"that cap move changed the analytic face types from {before_types} "
                         f"to {after_types}; it was refused rather than approximated")
    out = occt_backed(solid, updated)
    _copy_geometry_user_strings(geom, out)
    return out


def move_subobject(geom, kind, sub_id, vector):
    """`geom` with one FACE, EDGE or VERTEX moved, as a NEW exact object.

    (the command behavior, the command behavior): "Select a face. Pick a point to move
    from. Pick a point to move to." The neighbouring faces follow, staying attached.
    `kind` is one of `SUBOBJECT_KINDS` and `sub_id` is 's id (`subobject_parts`).

    Returns a NEW proxy, with its master registered and a ``polyhedron`` recipe, so it
    survives save + reload. The caller puts it in place of the old object
    (`NarwhalModel.replace_geometry`) and must re-read the sub-object ids, which change.
    `geom` is not modified.

    BUILT for solids whose faces are all PLANES with straight edges (holes in a face are
    fine). Every corner on the picked part moves, and every face must still be planar
    afterwards. Raises ValueError,
    naming the reason and changing nothing, for:
    * a curved face or edge -- "not implemented yet";
    * a move that would bend a face out of plane (most vertex moves do);
    * a move that would turn the whole solid inside out, collapse a face, or make the
      solid pass through itself (a single face reversing is NOT refused because a pocket
      floor raised past the rim can validly become a boss whose walls face the other way);
    * no exact solid behind `geom`, a bad kind, or an id out of range.
    """
    occt = _occt()
    if occt is None:
        raise ValueError("moving a sub-object needs the OCCT kernel, which is not installed")
    if kind not in SUBOBJECT_KINDS:
        raise SubObjectError(f"unknown sub-object kind {kind!r}; expected one of "
                             f"{', '.join(SUBOBJECT_KINDS)}")
    source, shape = _subobject_source(geom)
    if source not in ("master", "recipe"):
        raise ValueError("that object has no exact solid (no master and no recipe), so its "
                         "parts cannot be moved exactly")
    try:
        verts, faces, edges = occt.planar_polyhedron(shape)
    except _kernel().KernelError as e:
        try:
            return _move_extruded_cap(geom, shape, kind, sub_id, vector, occt)
        except ValueError as cap_error:
            raise cap_error from e
    table = {"face": faces, "edge": edges, "vertex": verts}[kind]
    i = int(sub_id)
    if i < 1 or i > len(table):
        raise SubObjectError(f"{kind} {i} is out of range; this solid has {len(table)} "
                             f"{kind}s numbered 1..{len(table)}")
    if kind == "face":
        moved = {j for loop in faces[i - 1] for j in loop}
    else:
        moved = set(edges[i - 1]) if kind == "edge" else {i}
    dx, dy, dz = (float(c) for c in vector)
    new = [((x + dx, y + dy, z + dz) if j in moved else (x, y, z))
           for j, (x, y, z) in enumerate(verts, start=1)]
    size = max(max(p[a] for p in verts) - min(p[a] for p in verts) for a in range(3))
    tol = SUBOBJECT_PLANAR_TOL * max(1.0, size)
    for fid, loops in enumerate(faces, start=1):
        n = occt._newell([new[j - 1] for j in loops[0]])
        pts = [new[j - 1] for loop in loops for j in loop]       # holes must stay in plane too
        length = math.sqrt(sum(c * c for c in n))
        if length <= tol * tol:
            raise ValueError(f"that move would collapse face {fid} to nothing")
        unit = tuple(c / length for c in n)
        c0 = tuple(sum(p[a] for p in pts) / len(pts) for a in range(3))
        off = max(abs(sum((p[a] - c0[a]) * unit[a] for a in range(3))) for p in pts)
        if off > tol:
            raise ValueError(
                f"that move would bend face {fid} out of plane (a corner {off:.4g} off its "
                f"plane) -- only moves that keep every face planar are built")

    # inside-out is a property of the SOLID, not of one face. A face's normal may
    # legitimately reverse -- a pocket floor raised past the rim turns the
    # pocket into a boss, so its walls now face outward the other way, and the result is
    # a valid airtight solid (20x20x10 box, 10x10 pocket 6 deep, floor +7 -> volume
    # 4000 + 100). The old per-face test refused exactly that. A solid that really turns
    # inside out (a box's top pushed through its bottom) has signed volume with
    # the original windings; one that passes through itself is refused by the kernel's
    # self-intersection check in `make_polyhedron`.
    if _polyhedron_signed_volume(occt, new, faces) <= tol * size * size:
        raise ValueError("that move would turn the solid inside out")
    recipe = {"kind": "polyhedron", "vertices": [list(p) for p in new],
              "faces": [[list(loop) for loop in loops] for loops in faces]}
    try:
        solid = occt.from_recipe(recipe)
    except _kernel().KernelError as e:
        raise ValueError(f"that move does not leave a valid solid ({e})") from e
    out = occt_backed(solid, recipe)
    _copy_geometry_user_strings(geom, out)
    return out


# --------------------------------------------------------------------------
# object DETAILS -- the data behind the Properties panel
# --------------------------------------------------------------------------

def _analysis():
    try:
        from . import analysis
    except ImportError:  # support direct-file loading outside the package
        import analysis
    return analysis


def _num(v):
    """A measurement as text: 12 significant digits, so an exact value reads exact."""
    return f"{float(v) + 0.0:.12g}"          # + 0.0 turns -0.0 into 0


def _pt3(p):
    x, y, z = (p.X, p.Y, p.Z) if hasattr(p, "X") else p
    return f"({_num(x)}, {_num(y)}, {_num(z)})"


def _curve_details(geom, measure=True):
    """Rows for a curve, in the words of the reference CAD application's `What` in the command UI."""
    rows = []
    closed = bool(geom.IsClosed)
    if isinstance(geom, r3.LineCurve):
        desc = "Line"
    elif isinstance(geom, r3.ArcCurve):
        desc = "Circle" if geom.IsCircle() else "Arc"
    elif isinstance(geom, r3.PolylineCurve):
        desc = f"{'Closed' if closed else 'Open'} polyline with {geom.PointCount} points."
    elif isinstance(geom, r3.PolyCurve):
        desc = (f"{'Closed' if closed else 'Open'} polycurve with {geom.SegmentCount} "
                f"curve segments.")
    elif geom.IsCircle():
        desc = "NURBS circle"
    elif geom.IsArc():
        desc = "NURBS arc"
    elif geom.IsPeriodic:
        desc = "Periodic NURBS curve"
    else:
        desc = f"{'Closed' if closed else 'Open'} NURBS curve"
    rows.append(("Description", desc))
    # the SHAPE in plain words, whatever the curve's class -- every curve this app
    # draws is a NurbsCurve, so the class alone would call a straight line a NURBS curve.
    if geom.IsCircle():
        shape = "circle"
    elif geom.IsArc():
        shape = "arc"
    elif geom.IsEllipse():
        shape = "ellipse"
    elif geom.IsLinear():
        shape = "line"
    elif geom.IsPolyline():
        shape = "polyline"
    else:
        shape = "free-form"
    rows.append(("Shape", shape))
    nc = geom if isinstance(geom, r3.NurbsCurve) else geom.ToNurbsCurve()
    if nc is not None:
        rows.append(("Degree", str(nc.Degree)))
        # wording: "control points: rational, count=%d" / "non-rational"
        rows.append(("Control points", f"{'rational' if nc.IsRational else 'non-rational'}, "
                                       f"count={len(nc.Points)}"))
    if shape in ("circle", "arc"):
        c = geom.TryGetCircle(1e-9) if shape == "circle" else geom.TryGetArc(1e-9)
        if c is not None:
            rows.append(("Center", _pt3(c.Center)))
            rows.append(("Radius", _num(c.Radius)))
    rows.append(("Closed", "yes" if closed else "no"))
    if not measure:
        return rows
    an = _analysis()
    rows.append(("Length", _num(an.curve_length(geom))))
    if closed:
        got = an.planar_curve_area_centroid(geom)
        if got is not None:
            rows.append(("Area", _num(got[0])))
    return rows


#: Measurement rows omitted by ``object_details(..., measure=False)`` because tight
#: bounding boxes and mass properties can dominate the cost for complex solids.
MEASURE_ROWS = ("Length", "Area", "Volume", "Bounding box", "Size")


def object_details(geom, measure=True):
    """Return ordered ``(label, value)`` rows for the Object Properties panel.

    ``measure=False`` omits ``MEASURE_ROWS`` so expensive geometric measurements are only
    computed when the Details view requests them.

    the reference CAD application's Object properties page has a Details button, "Details about the
    geometry mathematics, edges, vertices, and meshes". That is the `What` command's text
    (the command behavior, the command behavior). The wording here is taken from
    the command UI's own strings: "Valid polysurface.", "closed solid %s" / "open %s" and
    "%s with %d surfaces.", "Open/Closed/Periodic NURBS curve", "NURBS circle", "Line",
    "Open polyline with %d points.", "control points: rational, count=%d", "Closed polygon
    mesh:" + "%d vertices, %d faces", "Point at (...)".

    Rows, in order:
    * always ``Type`` (point / curve / surface / polysurface / extrusion / mesh, the
      `Valid %s.` word) and ``Description``;
    * a curve: Shape, Degree, Control points, Center + Radius for an arc or
      circle, Closed, Length (exact), and Area for a closed planar curve (exact);
    * a surface or polysurface: Faces, Edges, Vertices, Face types (``PLANE 6, CYLINDER
      1``), Airtight, Representation (`kernel.describe`), Source;
    * Bounding box and Size; then Area and, for a closed solid, Volume.

    THE RULE, which is what this function is for: a solid is described from its MASTER
    (or the shape rebuilt from its recipe), never from the display proxy. A proxy that
    claims a master that is MISSING (and has no recipe) gets no face count and no
    measurement at all: its triangles are not the shape, so the rows say "unknown".
    A brep with no master or recipe is described from its own faces. If it is
    MESH_DERIVED, the Representation row says "faceted", because the facets ARE that
    object.
    """
    if geom is None:
        raise ValueError("object_details: no geometry")
    an = _analysis()
    if isinstance(geom, (r3.Point, r3.Point3d)):
        loc = geom.Location if isinstance(geom, r3.Point) else geom
        return [("Type", "point"), ("Description", f"Point at {_pt3(loc)}.")]
    if isinstance(geom, r3.Curve):
        rows = [("Type", "curve")] + _curve_details(geom, measure)
        if not measure:
            return rows
        lo, hi = an.bounding_box(geom)
        return rows + _bbox_rows(lo, hi)
    if isinstance(geom, r3.Mesh):
        closed = bool(geom.IsClosed)
        rows = [("Type", "mesh"),
                ("Description", f"{'Closed' if closed else 'Open'} polygon mesh: "
                                f"{len(geom.Vertices)} vertices, {len(geom.Faces)} faces"),
                ("Closed", "yes" if closed else "no")]
        if not measure:
            return rows
        bb = geom.GetBoundingBox()
        return rows + _bbox_rows(bb.Min, bb.Max)
    if not isinstance(geom, (r3.Brep, r3.Extrusion, r3.Surface)):
        return [("Type", type(geom).__name__),
                ("Description", "details for this object type are not implemented yet")]

    k = _kernel()
    occt = _occt()
    source, topo = _subobject_source(geom)
    broken = (source == "brep" and k.representation_of(geom) == k.ANALYTIC_MASTER)
    is_extrusion = isinstance(geom, r3.Extrusion)
    rows = []
    if broken:
        # The proxy says it stands for a master that is not registered, and there is no
        # recipe to rebuild one from. Its faces are triangles, not the shape.
        unknown = "unknown -- the analytic master is missing; this is only its display mesh"
        rows += [("Type", "polysurface"), ("Description", unknown), ("Faces", unknown),
                 ("Edges", unknown), ("Vertices", unknown),
                 ("Representation", k.describe(geom))]
        return rows
    if source in ("master", "recipe"):
        faces = len(occt.unique_faces(topo))
        edges = len(occt._unique_edges(topo))
        verts = len(occt._unique_vertices(topo))
        types = occt.face_types(topo)
        closed = bool(occt.is_airtight(topo))
    else:
        faces, edges, verts = len(topo.Faces), len(topo.Edges), len(topo.Vertices)
        types = None
        closed = bool(getattr(topo, "IsSolid", False))
    if is_extrusion:
        kind = "extrusion"
    elif faces == 1:
        kind = "surface"
    else:
        kind = "polysurface"
    if faces == 1:
        desc = "closed surface" if closed else "surface"
    else:
        phrase = "closed solid polysurface" if closed else "open polysurface"
        desc = f"{phrase} with {faces} surfaces."
    rows += [("Type", kind), ("Description", desc)]
    if is_extrusion:
        rows += _extrusion_rows(geom)
    rows += [("Faces", str(faces)), ("Edges", str(edges)), ("Vertices", str(verts))]
    if types is not None:
        rows.append(("Face types", ", ".join(f"{t} {n}" for t, n in sorted(
            types.items(), key=lambda kv: (-kv[1], kv[0])))))
    rows.append(("Airtight", "yes" if closed else "no"))
    rows.append(("Representation", k.describe(geom)))
    rows.append(("Source", {"master": "analytic master (OCCT)",
                            "recipe": "rebuilt from its recipe (OCCT)",
                            "brep": "its own faces (no master, no recipe)"}[source]))
    if not measure:
        return rows
    if source in ("master", "recipe"):
        lo, hi = occt.tight_bbox(topo)
        rows += _bbox_rows(lo, hi)
        mp = occt.mass_properties(topo, solid=closed)
        rows.append(("Area", _num(mp["area"])))
        if closed:
            rows.append(("Volume", _num(mp["volume"])))
    else:
        lo, hi = an.bounding_box(topo)
        rows += _bbox_rows(lo, hi)
        # No exact shape: the sampled grid is exact only for faces of degree <= 1 (facets,
        # flat quads). Anything else is SAID to be approximate.
        n = an.analysis_density(topo)
        note = "" if n == 1 else " (approximate: sampled, no exact shape)"
        rows.append(("Area", _num(an.surface_area(topo, n, n)) + note))
        if closed:
            rows.append(("Volume", _num(an.solid_volume(topo, n, n)) + note))
    return rows


def _extrusion_rows(geom):
    """Describe an extrusion's caps and outer and inner profile curves."""
    rows = []
    try:
        top, bottom = bool(geom.IsCappedAtTop), bool(geom.IsCappedAtBottom)
        caps = ("start and end capped" if top and bottom else
                "start capped" if bottom else "end capped" if top else "not capped")
        rows.append(("End caps", caps))
        n = int(geom.ProfileCount)
        if n >= 1:
            prof = geom.Profile3d(0, 0.0)
            words = "Closed" if prof.IsClosed else "Open"
            if isinstance(prof, r3.PolyCurve):
                desc = f"{words} polycurve with {prof.SegmentCount} curve segments."
            else:
                desc = f"{words} {type(prof).__name__}"
            rows.append(("Outer profile", desc))
        if n > 1:
            rows.append(("Inner profiles", str(n - 1)))
        a, b = geom.PathStart, geom.PathEnd
        rows.append(("Path", f"{_pt3(a)} to {_pt3(b)}"))
    except Exception:  # noqa: BLE001
        pass
    return rows


def _bbox_rows(lo, hi):
    lo = (lo.X, lo.Y, lo.Z) if hasattr(lo, "X") else tuple(lo)
    hi = (hi.X, hi.Y, hi.Z) if hasattr(hi, "X") else tuple(hi)
    spans = [b - a for a, b in zip(lo, hi)]
    # a flat object's thickness is rounding noise (a circle read 1.8e-15 tall): show 0
    floor = 1e-12 * max(1.0, max(spans))
    return [("Bounding box", f"{_pt3(lo)} to {_pt3(hi)}"),
            ("Size", " x ".join(_num(s if s > floor else 0.0) for s in spans))]


def analytic_face_report(geom):
    """``[(surface_type, area)]`` for a master-backed solid, else None.

    What `Explode` can honestly tell the user about a polysurface it cannot yet split.
    """
    k = _kernel()
    master = k.master_of(geom)
    occt = _occt()
    if master is None or occt is None:
        return None
    return occt.face_summary_list(master)


def rehydrate_master(geom):
    """Rebuild and re-register `geom`'s analytic master from its recipe. True if restored.

    THE SAVE/RELOAD STORY. Masters live in a process-local registry, deliberately:
    a stale id must not survive a reload and be believed. So a reopened document holds a
    display proxy whose master id dangles, and `kernel.honesty_problem` correctly reports

        solid is tagged 'analytic-master' but NO master is registered for it

    in a fresh process reading a file the previous one wrote. The honesty layer was
    telling the truth -- the analytic geometry really was gone.

    What persists is the RECIPE, because user strings survive the `.3dm` (tag,
    master id and recipe all round-trip). Since a boolean records its whole
    construction TREE, so this rebuilds the master exactly and re-registers it.

    Returns False when there is no recipe to rebuild from, and DEMOTES the proxy to
    MESH_DERIVED in that case -- a solid must not keep claiming an analytic master it cannot
    produce.
    """
    k = _kernel()
    if k.master_of(geom) is not None:
        return True
    # Only a PROXY gets a master. A primitive brep is analytic on its own and must keep its
    # own ANALYTIC tag rather than being relabelled a proxy. The test is "does it carry a
    # master key" -- replaced a test on the recipe KIND (`== "boolean"`), which left every
    # newer master kind (chamfer, pipe, cap) BROKEN after a reload. of fillet,
    # chamfer, pipe+cap and boolean saved together, only the boolean came back.
    if not k.master_key_of(geom):
        return False
    recipe = k.recipe_of(geom)
    occt = _occt()
    if not recipe or occt is None:
        if k.master_key_of(geom):
            k.drop_master(geom)
        return False
    try:
        shape = occt.from_recipe(recipe)
    except Exception:  # noqa: BLE001
        if k.master_key_of(geom):
            k.drop_master(geom)
        return False
    k.register_master(geom, shape)
    k.record_recipe(geom, recipe)
    return True


def rehydrate_document(objects, tolerance=None, source=None):
    """Restore every analytic master in a just-loaded document. Returns (restored, lost).

    Call this right after reading a file. `objects` is any iterable of geometry.

    a brep with NO recipe -- every polysurface in a file from real the reference CAD application, and our own
    recipe-less exact results -- is ADOPTED from its own faces (`adopt_brep`), so it
    measures, booleans, exports and draws like anything we made. `last_adoption` says how
    many were adopted and why any were not. `tolerance` is the file's absolute tolerance.

    `source`, when `objects` are a file's objects in order, lets the per-object work run in
    worker processes (`prefetch_document`): ``{"path", "stat": os.stat taken BEFORE the
    file was read, "workers": start_open_workers(path), "meanwhile": callables to run
    here while they work}``. Every master is still registered here, before this returns.
    """
    restored = lost = 0
    adopted, declined = 0, []
    _DOC_TOLERANCE[0] = tolerance
    _DECLINED_ANALYTIC.clear()
    objects = list(objects)
    prebuilt = {}
    if source is not None:
        prebuilt = prefetch_document(source["path"], source.get("stat"), objects, tolerance,
                                     source.get("workers"), source.get("meanwhile"))
    else:
        clear_prefetch()
    for i, geom in enumerate(objects):
        if _kernel().master_key_of(geom):
            if rehydrate_master(geom):
                restored += 1
            elif adopt_brep(geom, tolerance):
                restored += 1            # its recipe was gone, but its faces were exact
            else:
                lost += 1
        elif isinstance(geom, (r3.Brep, r3.Extrusion)):
            if adopt_brep(geom, tolerance, prebuilt=prebuilt.pop(i, None)):
                adopted += 1
            elif _LAST_ADOPT[0]:
                declined.append(_LAST_ADOPT[0])
                _DECLINED_ANALYTIC[id(geom)] = geom
    _LAST_ADOPTION[:] = [adopted, declined]
    return restored, lost


#: The open document's absolute tolerance (set by `rehydrate_document`), for display-only
#: conversions that have no model to ask.
_DOC_TOLERANCE = [None]
#: why the last `adopt_brep` declined (None when it adopted or was not eligible).
_LAST_ADOPT = [None]
#: Object identities of exact foreign Breps whose faces converted but whose closed shell did
#: not become airtight. Held strongly so ids cannot be reused; viewport-only shell drawing.
_DECLINED_ANALYTIC = {}
#: ``[adopted_count, [reasons...]]`` for the last `rehydrate_document`.
_LAST_ADOPTION = [0, []]


def last_adoption():
    """``(adopted, [why each declined])`` for the last document opened."""
    return _LAST_ADOPTION[0], list(_LAST_ADOPTION[1])


def _nurbs_curve_dict(nc):
    """A rhino3dm NurbsCurve as the kernel's nurbs dict (EUCLIDEAN poles: rhino3dm stores
    them premultiplied by W).

    Each rhino3dm property read builds a new wrapper, so the point list, rational flag,
    and knots are read only once."""
    poles, weights = [], []
    points = nc.Points
    rational = nc.IsRational
    for i in range(len(points)):
        cp = points[i]
        w = cp.W if rational else 1.0
        poles.append([cp.X / w, cp.Y / w, cp.Z / w])
        weights.append(w)
    return {"ctype": "nurbs", "degree": int(nc.Degree), "poles": poles,
            "weights": weights, "knots": list(nc.Knots.ToList()),
            "periodic": bool(nc.IsPeriodic)}


def brep_data(brep):
    """An `r3.Brep`'s own faces, edges and loops as plain data for
    `occt_backend.shape_from_brep_data`. Nothing is resampled: every surface and
    edge travels as its exact NURBS form."""
    bedges = brep.Edges
    edges = [_nurbs_curve_dict(bedges[i].ToNurbsCurve()) for i in range(len(bedges))]
    faces = []
    bfaces = brep.Faces
    for fi in range(len(bfaces)):
        face = bfaces[fi]
        s = face.ToNurbsSurface()
        spts = s.Points
        rational = s.IsRational
        cu, cv = spts.CountU, spts.CountV
        poles, weights = [], []
        for i in range(cu):
            prow, wrow = [], []
            for j in range(cv):
                p = spts[i, j]
                w = p.W if rational else 1.0
                prow.append([p.X / w, p.Y / w, p.Z / w])
                wrow.append(w)
            poles.append(prow)
            weights.append(wrow)
        loops = []
        floops = face.Loops
        for li in range(len(floops)):
            loop = floops[li]
            trims = loop.Trims
            loops.append({"outer": loop.LoopType == r3.BrepLoopType.Outer,
                          "trims": [[tr.EdgeIndex, bool(tr.IsReversed)]
                                    for tr in (trims[t] for t in range(len(trims)))]})
        faces.append({"surface": {"degree_u": s.Degree(0), "degree_v": s.Degree(1),
                                  "poles": poles, "weights": weights,
                                  "knots_u": list(s.KnotsU.ToList()),
                                  "knots_v": list(s.KnotsV.ToList())},
                      "reversed": bool(face.OrientationIsReversed), "loops": loops})
    return {"edges": edges, "faces": faces}


def adopt_brep(geom, tolerance=None, prebuilt=None):
    """Give a brep with no recipe and no master -- a polysurface from REAL the reference CAD application -- an OCCT
    master built from its OWN faces. Returns True when adopted.

    `prebuilt` is a worker process's outcome of THIS function for the same object
    (`prefetch_document`): ``("ok", shape)`` -- converted and checked airtight there -- or
    ``("declined", why)``. Every eligibility check below still runs here; only the
    conversion is taken from the worker.

    before: such a solid had no master and no recipe, so `exact_mass` returned
    None (no Volume), `honesty_problem` called it untagged, a boolean on it declined to the
    MESH route, STEP export refused it, and the display grid-sampled its NURBS faces
    ignoring the trims. Adopted, it is master-backed like anything we built: it measures,
    booleans, exports STEP and is drawn from the master. The document object is left
    exactly as the file had it -- it IS the exact polysurface, which is also what persists:
    a reopened file adopts it again.

    Never launders facets: a brep tagged mesh-marked triangulated, or measuring as
    a tessellation is declined, as is anything the kernel cannot build airtight when the
    brep says it is a solid. The reason is in `_LAST_ADOPT`.
    """
    _LAST_ADOPT[0] = None
    brep = _adoption_candidate(geom)
    if brep is None:
        return False
    if prebuilt is None or prebuilt[0] not in ("ok", "declined"):
        prebuilt = _conversion_outcome(brep, tolerance)
    if prebuilt[0] != "ok":
        _LAST_ADOPT[0] = prebuilt[1]
        return False
    # replaces any stale key from a past session
    _kernel().register_master(geom, prebuilt[1])
    return True


def _adoption_candidate(geom):
    """`adopt_brep`'s eligibility checks: the brep form of `geom` when it may be adopted,
    else None (with the reason in `_LAST_ADOPT` when it is one the user should hear)."""
    _LAST_ADOPT[0] = None
    k, occt = _kernel(), _occt()
    if occt is None or not isinstance(geom, (r3.Brep, r3.Extrusion)):
        return None
    if k.master_of(geom) is not None or k.recipe_of(geom) or picture_frame(geom) is not None:
        return None
    if k.representation_of(geom) == k.MESH_DERIVED:
        return None
    try:
        if geom.GetUserString(k.DOCUMENT_FORM_KEY) == "triangulated":
            return None
    except Exception:  # noqa: BLE001
        pass
    brep = geom if isinstance(geom, r3.Brep) else geom.ToBrep(False)
    if brep is None or len(brep.Faces) == 0:
        return None
    if k.looks_tessellated(brep) and _faces_are_mesh_facets(brep):
        _LAST_ADOPT[0] = (f"a {len(brep.Faces)}-face brep that measures as a tessellation "
                          f"was left as it is -- it is not analytic geometry")
        return None
    return brep


def _conversion_outcome(brep, tolerance):
    """`adopt_brep`'s conversion: ``("ok", the OCCT shape of brep's own faces)``, or
    ``("declined", why)`` -- it could not be built, or a closed brep did not come out an
    airtight solid."""
    occt = _occt()
    sew = max(float(tolerance or 0.0), 1e-6)
    report = {}
    try:
        shape = occt.shape_from_brep_data(brep_data(brep), sew_tolerance=sew, report=report)
    except Exception as e:  # noqa: BLE001
        return ("declined", f"a {len(brep.Faces)}-face brep could not be converted: {e}")
    # `shape_from_brep_data` hands back a solid only after asserting it airtight, and
    # otherwise the sewn shell or compound, which holds no solid. Reuse that verdict
    # rather than traversing the shape again with `is_airtight`.
    airtight = report.get("airtight")
    if airtight is None:
        airtight = occt.is_airtight(shape)
    if brep.IsSolid and not airtight:
        return ("declined", f"a {len(brep.Faces)}-face closed brep did not convert to an "
                            f"airtight solid")
    return ("ok", shape)


def adoption_outcome(geom, tolerance=None):
    """What `adopt_brep(geom, tolerance)` would do, without registering anything:
    ``("ok", shape)``, ``("declined", why)``, or ``("skip", None)`` (not a candidate)."""
    brep = _adoption_candidate(geom)
    if brep is None:
        return ("declined", _LAST_ADOPT[0]) if _LAST_ADOPT[0] else ("skip", None)
    return _conversion_outcome(brep, tolerance)


# ---------------------------------------------------------------------------
# OPENING A FILE IN PARALLEL. Performance only: every result is the one the serial path
# gives, and anything the workers do not deliver is computed here exactly as before.
#
# Large documents can spend substantial time adopting solids, drawing curves, and
# resampling overlay geometry on one core. Each object's work is independent, so
# `prefetch_document` starts worker processes that each read the SAME file and do a share:
#
# * a solid: `adopt_brep` itself, unchanged; the master comes back in OCCT's binary BRep
#   format (`occt_backend.shape_to_bytes`: every double written bit for bit -- OCCT only
#   re-normalises unit directions as it reads, by an ulp at most, ), with the
#   display tessellation `_master_bmesh` would compute from it;
# * a curve: its display points (`_curve_display_points` at `display_tolerance`), keyed
#   by `curve_fingerprint` -- its full openNURBS serialisation -- so they can only ever be
#   used for an identical curve. (Its layer-overlay points were computed too, for
#   `ui._layer_curve_items`; nothing in the app calls that any more -- the curve pass
#   draws from ``_curve_colour_map``—so the unused overlay payload is omitted.
#
# Nothing is deferred: every master is registered before `NarwhalModel.load` returns, and
# every eligibility check of `adopt_brep` still runs in this process. A worker result is
# used only when the file it read is byte-for-byte the file this document was read from
# (same inode, size and modification time, before and after both reads) and the object
# at that index has the same type and face count.
# ---------------------------------------------------------------------------

#: Maximum worker processes an Open starts; one core is left for the app, and every
#: worker must read the entire file.
PREFETCH_MAX_WORKERS = 8
#: - estimated milliseconds of work below which one more worker is not worth
#: starting (each costs ~1 s to start, import and read the file).
PREFETCH_MS_PER_WORKER = 1500.0
#: Empirical cost model for distributing work: solid adoption scales by face count,
#: curve display work by segment count, and a polyline only needs to be read.
PREFETCH_MS_PER_FACE = 3.25
PREFETCH_MS_PER_SEGMENT = 0.13
PREFETCH_MS_PER_POLYLINE = 0.05
#: - how long an Open waits for its workers before doing their share itself.
PREFETCH_TIMEOUT_S = 600.0

#: id(master) -> (master, flat vertex array, flat triangle array) for `_master_bmesh`;
#: the master itself is held so its id cannot be reused while the entry exists.
_PREFETCH_TESS = {}
#: curve fingerprint -> [flat point arrays] for `curve_display_bmesh` / the overlay
_PREFETCH_DISPLAY = {}
_PREFETCH_OVERLAY = {}
#: fingerprint -> [(verts, tris, real edges)]: a declined analytic Brep, drawn from its
#: exact trimmed shell without claiming that shell is an airtight master.
_PREFETCH_DECLINED = {}
# Compatibility names used by the existing Open-worker display gate. The store is the same;
# new entries may additionally carry real-edge pairs at tuple index 2.
_PREFETCH_HOLES = _PREFETCH_DECLINED
#: what the last `prefetch_document` did (`last_prefetch`)
_LAST_PREFETCH = {}

_ADDON_DIR = __import__("os").path.dirname(__import__("os").path.abspath(__file__))

#: A worker loads this module BY PATH under a bare package (as the spike suites do), so
#: the add-on's `__init__` -- which needs bpy -- never runs.
_WORKER_BOOT = (
    "import importlib.util, os, sys, types\n"
    "pkg = types.ModuleType('narwhal3d')\n"
    "pkg.__path__ = [sys.argv[1]]\n"
    "sys.modules['narwhal3d'] = pkg\n"
    "spec = importlib.util.spec_from_file_location(\n"
    "    'narwhal3d.bridge', os.path.join(sys.argv[1], 'bridge.py'))\n"
    "mod = importlib.util.module_from_spec(spec)\n"
    "sys.modules['narwhal3d.bridge'] = mod\n"
    "spec.loader.exec_module(mod)\n"
    "mod._prefetch_worker_main(sys.argv[2], sys.argv[3])\n")  # path, output file


def clear_prefetch():
    """Forget every prefetched result (a new document is being opened)."""
    _PREFETCH_TESS.clear()
    _PREFETCH_DISPLAY.clear()
    _PREFETCH_OVERLAY.clear()
    _PREFETCH_DECLINED.clear()


def last_prefetch():
    """What the last Open's workers did: ``{"workers", "seconds", "masters", "curves",
    "why"}`` -- `why` says why it ran serially or what it had to redo, else None."""
    return dict(_LAST_PREFETCH)


def curve_fingerprint(geom):
    """A digest of a curve's full openNURBS serialisation, or None (= always re-sample).

    `Encode` is deterministic, so equal digests mean equal control points, knots, weights,
    and segments.
    """
    import hashlib

    try:
        data = geom.Encode()["data"]
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, str) or not data:
        return None
    return (type(geom).__name__, len(data),
            hashlib.blake2b(data.encode(), digest_size=16).digest())


def overlay_curve_points(curve):
    """World points along a curve, exact at its corners (a polyline keeps its kinks) --
    what the viewport's layer-colour curve overlay and the ghosts draw (`ui._curve_points`,
    which calls this; it lives here so an Open's workers can compute it)."""
    try:
        if isinstance(curve, r3.LineCurve):
            a, b = curve.PointAtStart, curve.PointAtEnd
            return [(a.X, a.Y, a.Z), (b.X, b.Y, b.Z)]
        if isinstance(curve, r3.PolylineCurve):
            return [(curve.Point(i).X, curve.Point(i).Y, curve.Point(i).Z)
                    for i in range(curve.PointCount)]
        if isinstance(curve, r3.PolyCurve):
            out = []
            for seg in curve.Explode():
                pts = overlay_curve_points(seg)
                out.extend(pts if not out else pts[1:])
            return out
        if isinstance(curve, r3.NurbsCurve) and curve.Degree == 1:
            # control points ARE the shape; Point4d is premultiplied by W
            out = []
            for i in range(len(curve.Points)):
                q = curve.Points[i]
                w = q.W or 1.0
                out.append((q.X / w, q.Y / w, q.Z / w))
            return out
        try:
            n = int(curve_display_segments(curve)) + 1
        except Exception:  # noqa: BLE001
            n = 64
        n = max(8, min(n, 512))
        return [(q.X, q.Y, q.Z) for q in sample_curve(curve, n)]
    except Exception:  # noqa: BLE001
        return []


def _flat(rows, code="d"):
    """A list of equal-length tuples as one flat `array` (exact: doubles stay doubles)."""
    from array import array

    out = array(code)
    for row in rows:
        out.extend(row)
    return out


def _rows3(flat):
    """`_flat`'s inverse for 3-tuples."""
    return list(zip(flat[0::3], flat[1::3], flat[2::3]))


def _take_prefetched(store, fp):
    """Pop one prefetched point list for fingerprint `fp`, or None."""
    if fp is None:
        return None
    lst = store.get(fp)
    if not lst:
        return None
    flat = lst.pop()
    if not lst:
        del store[fp]
    return _rows3(flat)


def take_prefetched_overlay(fp):
    """The overlay points an Open's worker computed for the curve with fingerprint `fp`
    (exactly `overlay_curve_points` of it), or None -- then compute them."""
    return _take_prefetched(_PREFETCH_OVERLAY, fp) if _PREFETCH_OVERLAY else None


def _stat_key(path):
    import os

    st = os.stat(path)
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def _prefetch_tasks(objects, with_solids):
    """``(cost_ms, kind, index, type name, face count)`` for every object worth handing to
    a worker: each un-keyed Brep/Extrusion (`adopt_brep`'s candidates) and each curve."""
    k = _kernel()
    tasks = []
    for i, g in enumerate(objects):
        if isinstance(g, (r3.Brep, r3.Extrusion)):
            if not with_solids or k.master_key_of(g):
                continue
            try:
                b = g if isinstance(g, r3.Brep) else g.ToBrep(False)
                faces = len(b.Faces)
            except Exception:  # noqa: BLE001
                continue
            tasks.append((faces * PREFETCH_MS_PER_FACE, "brep", i, type(g).__name__, faces))
        elif isinstance(g, r3.Curve):
            if isinstance(g, (r3.PolylineCurve, r3.LineCurve)):
                cost = PREFETCH_MS_PER_POLYLINE
            elif isinstance(g, r3.PolyCurve):
                cost = PREFETCH_MS_PER_SEGMENT * max(1, int(g.SegmentCount))
            else:
                cost = PREFETCH_MS_PER_SEGMENT
            tasks.append((cost, "curve", i, type(g).__name__, 0))
    return tasks


def _prefetch_worker_count(cost_ms):
    """How many workers `cost_ms` of work is worth; 0 = do it here. The environment
    variable NARWHAL3D_OPEN_WORKERS forces a count (0 = never use workers)."""
    import os

    forced = os.environ.get("NARWHAL3D_OPEN_WORKERS")
    if forced is not None:
        try:
            return max(0, int(forced))
        except ValueError:
            pass
    n = min(PREFETCH_MAX_WORKERS, (os.cpu_count() or 1) - 1,
            int(cost_ms // PREFETCH_MS_PER_WORKER))
    return n if n >= 2 else 0


def _worker_python():
    """The Python interpreter to run workers with (Blender's own, which has rhino3dm),
    or None."""
    import os
    import sys

    exe = sys.executable or ""
    if os.path.basename(exe).lower().startswith("python") and os.path.isfile(exe):
        return exe
    return None



#: - a file smaller than this is opened without workers: each costs ~1.5 s of CPU to
#: start and read the file before the plan can even say whether it is needed.
PREFETCH_MIN_FILE_BYTES = 4 * 1024 * 1024
#: Conservative worker-memory estimate per input byte, plus memory always reserved for the app.
PREFETCH_WORKER_BYTES_PER_FILE_BYTE = 8
PREFETCH_MEMORY_RESERVE = 1536 * 1024 * 1024


def _memory_available():
    """Bytes this process may still allocate: MemAvailable, capped by any cgroup limit.

    Returns None when the available amount cannot be determined.
    """
    avail = None
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    avail = int(line.split()[1]) * 1024
                    break
    except (OSError, ValueError):
        pass
    try:
        with open("/proc/self/cgroup") as fh:
            rel = next((ln.split(":", 2)[2].strip() for ln in fh if ln.startswith("0::")), None)
        if rel:
            base = "/sys/fs/cgroup" + rel
            with open(base + "/memory.max") as fh:
                limit = fh.read().strip()
            if limit != "max":
                with open(base + "/memory.current") as fh:
                    used = int(fh.read().strip())
                room = int(limit) - used
                avail = room if avail is None else min(avail, room)
    except (OSError, ValueError, StopIteration):
        pass
    return avail


def start_open_workers(path):
    """Start an Open's worker processes BEFORE this process reads `path`, so their start-up
    and their own read of the file overlap Each waits on its stdin for its share of
    the plan (`prefetch_document`) and gets nothing to do if workers turn out not to be
    worth it. Returns a handle, or None (open serially). Pair with `cancel_open_workers`."""
    import os
    import subprocess
    import tempfile

    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    forced = os.environ.get("NARWHAL3D_OPEN_WORKERS")
    if forced is not None:
        try:
            n = max(0, int(forced))
        except ValueError:
            n = 0
    else:
        n = (min(PREFETCH_MAX_WORKERS, (os.cpu_count() or 1) - 1)
             if size >= PREFETCH_MIN_FILE_BYTES else 0)
        room = _memory_available()
        if room is not None:
            each = max(256 * 1024 * 1024, PREFETCH_WORKER_BYTES_PER_FILE_BYTE * size)
            n = min(n, max(0, int((room - PREFETCH_MEMORY_RESERVE) // each)))
        if n < 2:
            n = 0
    exe = _worker_python()
    if n < 1 or exe is None:
        return None
    tmp = tempfile.mkdtemp(prefix="narwhal3d-open-")
    handle = {"procs": [], "tmp": tmp, "n": 0, "path": path}
    try:
        for w in range(n):
            out_path = os.path.join(tmp, f"out{w}.pkl")
            err_path = os.path.join(tmp, f"err{w}.txt")
            with open(err_path, "wb") as err:
                p = subprocess.Popen([exe, "-c", _WORKER_BOOT, _ADDON_DIR, path, out_path],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=err)
            handle["procs"].append((p, out_path, err_path))
    except Exception:  # noqa: BLE001
        cancel_open_workers(handle)
        return None
    handle["n"] = len(handle["procs"])
    return handle


def cancel_open_workers(handle):
    """Stop any worker still running and remove their scratch folder. Safe to call twice."""
    import shutil

    if not handle:
        return
    for p, _o, _e in handle.get("procs", ()):
        for pipe in (p.stdin, p.stdout):
            try:
                if pipe is not None and not pipe.closed:
                    pipe.close()
            except OSError:
                pass
        if p.poll() is None:
            p.kill()
        p.wait()
    handle["procs"] = []
    if handle.get("tmp"):
        shutil.rmtree(handle["tmp"], ignore_errors=True)
        handle["tmp"] = None


#: Estimated size (ms) of one chunk of work handed to a worker. Work is dealt
#: out a chunk at a time to whichever worker is free -- largest first -- because a fixed
#: split leaves the whole Open waiting on its slowest worker even when shares have similar
#: estimated cost. This process takes the smallest chunks and hands out more between them.
PREFETCH_CHUNK_MS = 150.0
#: Chunks queued at each worker at once, so it never waits for the next one
PREFETCH_IN_FLIGHT = 2
#: the line a worker prints when a chunk is written (anything else on stdout is ignored)
_DONE = b"NARWHAL3D-PREFETCH-DONE "


def _chunks(tasks, target_ms=PREFETCH_CHUNK_MS):
    """`tasks` as chunks of about `target_ms` estimated work, LARGEST first (an item bigger
    than that is a chunk of its own)."""
    out, cur, cost = [], [], 0.0
    for t in sorted(tasks, key=lambda t: -t[0]):
        if t[0] >= target_ms:
            out.append([t[1:]])
            continue
        cur.append(t[1:])
        cost += t[0]
        if cost >= target_ms:
            out.append(cur)
            cur, cost = [], 0.0
    if cur:
        out.append(cur)
    return out


def _run_chunks(handle, chunks, tolerance, local, timeout, meanwhile=()):
    """Deal `chunks` out to the workers -- `PREFETCH_IN_FLIGHT` at a time to each, so none
    waits for the next -- while this process runs the `meanwhile` steps and then does the
    SMALLEST chunks with `local(items)`, handing out more between them. Returns ``(worker
    outputs, local outputs, problems)``. A chunk whose worker dies goes back on the queue;
    whatever is unfinished at `timeout` is simply missing -- the caller then computes it as
    before."""
    import os
    import pickle
    import selectors
    import time

    deadline = time.monotonic() + timeout
    procs = handle["procs"]
    queue = list(range(len(chunks)))             # largest first
    busy = {k: [] for k in range(len(procs))}
    dead = set()
    bufs = {k: b"" for k in range(len(procs))}
    outs, mine, problems = [], [], []
    steps = list(meanwhile)
    sel = selectors.DefaultSelector()
    for k, (p, _o, _e) in enumerate(procs):
        os.set_blocking(p.stdout.fileno(), False)
        sel.register(p.stdout, selectors.EVENT_READ, k)

    def feed():
        for k in range(len(procs)):
            while k not in dead and queue and len(busy[k]) < PREFETCH_IN_FLIGHT:
                c = queue.pop(0)
                try:
                    procs[k][0].stdin.write(pickle.dumps(
                        {"id": c, "tolerance": tolerance, "items": chunks[c]},
                        protocol=pickle.HIGHEST_PROTOCOL))
                    procs[k][0].stdin.flush()
                    busy[k].append(c)
                except OSError:
                    queue.insert(0, c)
                    lost(k, "could not be sent work")

    def lost(k, why):
        if k in dead:
            return
        dead.add(k)
        if busy[k]:
            queue[:0] = busy[k]
            busy[k] = []
            try:
                with open(procs[k][2], "rb") as fh:
                    tail = fh.read()[-300:].decode("utf-8", "replace").strip()
            except OSError:
                tail = ""
            problems.append(f"a worker {why} ({tail})")

    def poll(wait):
        for key, _ev in sel.select(timeout=wait):
            k = key.data
            try:
                data = os.read(key.fileobj.fileno(), 65536)
            except BlockingIOError:
                continue
            if not data:                             # it exited or died
                sel.unregister(key.fileobj)
                lost(k, "died")
                continue
            bufs[k] += data
            while b"\n" in bufs[k]:
                line, bufs[k] = bufs[k].split(b"\n", 1)
                if not line.startswith(_DONE):
                    continue
                c = int(line[len(_DONE):])
                if c in busy[k]:
                    busy[k].remove(c)
                try:
                    with open(f"{procs[k][1]}.{c}", "rb") as fh:
                        outs.append(pickle.load(fh))
                except (OSError, pickle.UnpicklingError, EOFError) as e:
                    problems.append(f"chunk {c}: {e}")

    try:
        while time.monotonic() < deadline:
            feed()
            in_flight = any(busy.values())
            if not queue and not in_flight and not steps:
                break
            if steps:
                steps.pop(0)()
            elif queue:
                mine.append(local(chunks[queue.pop()]))    # the SMALLEST left
            else:
                poll(max(0.0, min(0.5, deadline - time.monotonic())))
                continue
            poll(0)
        unfinished = len(queue) + sum(len(b) for b in busy.values())
        if unfinished:
            problems.append(f"{unfinished} chunk(s) unfinished at the time limit")
    finally:
        sel.close()
    return outs, mine, problems


def prefetch_document(path, stat_before, objects, tolerance, workers, meanwhile=None):
    """Do the expensive per-object work of opening `path` in the `workers` started by
    `start_open_workers` (see the section comment), this process doing a share too.

    Returns ``{object index: ("ok", master) | ("declined", why)}`` for
    `adopt_brep(..., prebuilt=)`, and stores the display tessellations and curve points for
    `_master_bmesh`, `curve_display_bmesh`, `geometry_to_bmesh` and the overlay. Returns {}
    -- the serial path -- when workers are not worth it or not possible; `last_prefetch`
    says which. `meanwhile` -- zero-argument callables -- run here between handing out
    work. The workers are always finished or stopped when this returns.

    `stat_before` is ``os.stat(path)`` taken BEFORE the document was read.
    """
    import time

    t0 = time.perf_counter()
    clear_prefetch()
    _LAST_PREFETCH.clear()
    _LAST_PREFETCH.update(workers=0, seconds=0.0, masters=0, curves=0, why=None)
    try:
        occt = _occt()
        tasks = _prefetch_tasks(objects, with_solids=occt is not None)
        n = 0
        if workers and workers.get("n") and stat_before is not None:
            n = min(workers["n"], _prefetch_worker_count(sum(t[0] for t in tasks)))
        if not tasks or n < 1:
            _LAST_PREFETCH["why"] = ("serial: " + ("nothing to share" if not tasks else
                                                   "no workers" if not workers else
                                                   "too little work"))
            cancel_open_workers(workers)
            for step in meanwhile or ():
                step()
            return {}
        want = (stat_before.st_dev, stat_before.st_ino, stat_before.st_size,
                stat_before.st_mtime_ns)
        # only `n` of the started workers are worth using
        cancel_open_workers({"procs": workers["procs"][n:]})
        workers["procs"] = workers["procs"][:n]
        chunks = _chunks(tasks)
        outs, mine, problems = _run_chunks(
            workers, chunks, tolerance,
            lambda items: _prefetch_local(objects, items, tolerance, want),
            PREFETCH_TIMEOUT_S, meanwhile or ())
    except Exception as e:  # noqa: BLE001
        _LAST_PREFETCH["why"] = f"serial: the workers could not run ({e!r})"
        clear_prefetch()
        return {}
    finally:
        cancel_open_workers(workers)
    t_wait = time.perf_counter() - t0
    expect = {t[2]: t for t in tasks}
    prebuilt = {}
    _LAST_PREFETCH.update(chunks=len(chunks), local_chunks=len(mine),
                          local_seconds=round(sum(m["seconds"][1] for m in mine), 3))
    for out in mine + outs:
        if out.get("stat") != (want, want):
            problems.append("a worker read a different version of the file")
            continue
        for i, res in out["breps"].items():
            t = expect.get(i)
            verdict, payload, faces, tess, holes = res
            if t is None or t[1] != "brep" or faces != t[4]:
                continue
            if holes is not None:
                _PREFETCH_DECLINED.setdefault(holes[0], []).append(tuple(holes[1:]))
            if verdict == "declined":
                prebuilt[i] = ("declined", payload)
            elif verdict == "ok":
                try:
                    shape = (occt.shape_from_bytes(*payload)
                             if isinstance(payload, tuple) else payload)
                except Exception as e:  # noqa: BLE001
                    problems.append(f"object {i}: {e}")
                    continue
                prebuilt[i] = ("ok", shape)
                if tess is not None:
                    _PREFETCH_TESS[id(shape)] = (shape, tess[0], tess[1],
                                                 tess[2] if len(tess) > 2 else None)
        for i, res in out["curves"].items():
            t = expect.get(i)
            if res is None or t is None or t[1] != "curve":
                continue
            fp, disp, over = res
            if disp is not None:
                _PREFETCH_DISPLAY.setdefault(fp, []).append(disp)
            if over is not None:
                _PREFETCH_OVERLAY.setdefault(fp, []).append(over)
            _LAST_PREFETCH["curves"] += 1
    _LAST_PREFETCH.update(workers=n, seconds=time.perf_counter() - t0, waited=t_wait,
                          masters=sum(1 for v in prebuilt.values() if v[0] == "ok"),
                          why="; ".join(problems)[:500] or None)
    return prebuilt


def _prefetch_brep(geom, tolerance, serialise=True):
    """One solid's share: ``(verdict, payload, face count, tessellation, holes)``.

    `verdict`/`payload` are `adoption_outcome` (the master as bytes when `serialise`);
    `tessellation` is what `_master_bmesh` draws an adopted one from; `holes` is
    ``(fingerprint, verts, tris)`` -- what `geometry_to_bmesh` draws an UN-adopted brep
    with inner loops from -- else None."""
    occt = _occt()
    brep = geom if isinstance(geom, r3.Brep) else geom.ToBrep(False)
    faces = len(brep.Faces) if brep is not None else -1
    if occt is None:
        return ("skip", None, faces, None, None)
    verdict, payload = adoption_outcome(geom, tolerance)
    if verdict != "ok":
        return (verdict, payload, faces, None,
                _prefetch_declined_display(geom, tolerance, occt)
                if verdict == "declined" and payload else None)
    try:
        # tessellated BEFORE it is written, so the master arrives carrying the same
        # triangulation `_master_bmesh` would have left on it
        verts, tris, edges = display_tessellation(payload)
        tess = (_flat(verts), _flat(tris, "q"), _flat(edges, "q"))
    except Exception:  # noqa: BLE001
        tess = None                    # the app draws it the usual way (and fails alike)
    if serialise:
        payload = (occt.shape_to_bytes(payload), type(payload).__name__)
    return ("ok", payload, faces, tess, None)


def _prefetch_declined_display(geom, tolerance, occt):
    """Display tessellation of an un-adopted analytic Brep's exact trimmed shell."""
    try:
        if picture_frame(geom) is not None:
            return None
        shown = geom.ToBrep(True) if isinstance(geom, r3.Extrusion) else geom
        if not isinstance(shown, r3.Brep):
            return None
        fp = curve_fingerprint(geom)
        if fp is None:
            return None
        shape = occt.shape_from_brep_data(brep_data(shown),
                                          sew_tolerance=max(tolerance or 0.0, 1e-6))
        verts, tris, edges = display_tessellation(shape)
    except Exception:  # noqa: BLE001
        return None                    # the app tries itself (and fails alike)
    return (fp, _flat(verts), _flat(tris, "q"), _flat(edges, "q"))


def _prefetch_holes(geom, tolerance, occt):
    """Compatibility wrapper for the former holes-only prefetch helper."""
    result = _prefetch_declined_display(geom, tolerance, occt)
    return result[:3] if result is not None else None


def _prefetch_curve(geom, overlay):
    """One curve's share: ``(fingerprint, display points, overlay points)`` as flat arrays
    (None where not computed), or None. An Open asks for NO overlay points (see the
    section comment); `overlay=True` still computes them.

    The overlay sizes each smooth segment with `curve_display_segments` -- the SAME
    `_display_samples` the display points just ran on that segment whenever the two
    tolerances agree. Within this call, samples are remembered per segment serialization
    and tolerance so each combination is computed once. Without the overlay there is no
    repeated sampling, so per-segment fingerprints are skipped."""
    global _display_samples

    fp = curve_fingerprint(geom)
    if fp is None:
        return None
    if not overlay:
        try:
            disp = _flat(_curve_display_points(geom, display_tolerance(geom)))
        except Exception:  # noqa: BLE001
            disp = None                # the app computes it (and fails alike)
        return (fp, disp, None)
    real, memo = _display_samples, {}

    def remembered(curve, tolerance):
        key = curve_fingerprint(curve)
        if key is None:
            return real(curve, tolerance)
        key = (key, tolerance)
        hit = memo.get(key)
        if hit is None:
            hit = memo[key] = real(curve, tolerance)
        return hit

    _display_samples = remembered
    try:
        try:
            disp = _flat(_curve_display_points(geom, display_tolerance(geom)))
        except Exception:  # noqa: BLE001
            disp = None                # the app computes it (and fails alike)
        over = _flat(overlay_curve_points(geom)) if overlay else None
    finally:
        _display_samples = real
    return (fp, disp, over)


def _prefetch_local(objects, items, tolerance, stat):
    """THIS process's share, on the document's own objects (no file read, nothing
    serialised) -- the same result shape as a worker's `prefetch_compute`."""
    import time

    t0 = time.perf_counter()
    breps, curves = {}, {}
    for kind, i, tname, _faces in items:
        geom = objects[i]
        if type(geom).__name__ != tname:
            continue
        if kind == "brep":
            breps[i] = _prefetch_brep(geom, tolerance, serialise=False)
        else:
            curves[i] = _prefetch_curve(geom, False)
    return {"stat": (stat, stat), "breps": breps, "curves": curves,
            "seconds": (0.0, round(time.perf_counter() - t0, 3))}


def prefetch_compute(job, file3dm, stat):
    """A worker's whole share of an Open: `job` is ``{"tolerance", "items": [(kind, index,
    type name, face count),...]}``, `file3dm` the worker's own read of the file and `stat`
    ``(before, after)`` that read. Pure rhino3dm + OCCT -- no bpy."""
    import time

    t0 = time.perf_counter()
    objs = file3dm.Objects
    breps, curves = {}, {}
    for kind, i, tname, _faces in job["items"]:
        fobj = objs[i]
        geom = fobj.Geometry
        if type(geom).__name__ != tname:
            continue
        if kind == "brep":
            breps[i] = _prefetch_brep(geom, job["tolerance"])
        else:
            curves[i] = _prefetch_curve(geom, False)   # no overlay points (unused)
    return {"stat": stat, "breps": breps, "curves": curves,
            "seconds": (0.0, round(time.perf_counter() - t0, 3))}


def _prefetch_worker_main(path, out_path):
    """Entry point of a worker process (`_WORKER_BOOT`): load the kernel, read the file,
    then do each chunk it is sent on stdin -- writing ``<out_path>.<id>`` and printing
    `_DONE` id -- until it is sent None or stdin closes."""
    import os
    import pickle
    import sys

    _occt()
    before = _stat_key(path)
    f = r3.File3dm.Read(path)
    after = _stat_key(path)
    while True:
        try:
            job = pickle.load(sys.stdin.buffer)
        except EOFError:
            return
        if not job:
            return
        if f is None:
            raise OSError(f"could not read {path}")
        out = prefetch_compute(job, f, (before, after))
        target = f"{out_path}.{job['id']}"
        with open(target + ".part", "wb") as fh:
            pickle.dump(out, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(target + ".part", target)
        sys.stdout.buffer.write(_DONE + str(job["id"]).encode() + b"\n")
        sys.stdout.buffer.flush()


# ---------------------------------------------------------------------------
# TEXT. Imported.3dm files can carry annotation text. rhino3dm provides the string,
# plane, and—through the object's
# DIMENSION STYLE -- the height and font, but no glyph outlines, so the glyphs come from
# Blender's font engine. Display only: the document keeps the Text object untouched,
# so it saves back exactly as it was read.
#
# Placement, from openNURBS' own bounding box for these objects: it spans plane
# Y from (origin - height x model-space scale) up to the origin, i.e. the plane origin
# is the TOP of the text. Help (the command behavior): "Model space scale... The text
# height... [is] multiplied by this number". left-aligned (rhino3dm does not
# expose the alignment), and when the named font is missing fontconfig's substitute is
# used (Arial -> Liberation Sans, metric-compatible).
# ---------------------------------------------------------------------------

#: str(DimensionStyle.Id) -> DimensionStyle of the open document (see note_document_styles)
_DIMSTYLES = {}
#: font family -> (blender font, cap height at size 1)
_FONT_CACHE = {}


def note_document_styles(file3dm):
    """Remember the open file's dimension styles: a Text's height and font live there."""
    _DIMSTYLES.clear()
    try:
        for i in range(len(file3dm.DimStyles)):
            ds = file3dm.DimStyles[i]
            _DIMSTYLES[str(ds.Id)] = ds
    except Exception:  # noqa: BLE001
        pass


def text_style(text):
    """The DimensionStyle `text` uses (its own, else the document's first, else default)."""
    ds = _DIMSTYLES.get(str(getattr(text, "DimensionStyleId", "")))
    if ds is None and _DIMSTYLES:
        ds = next(iter(_DIMSTYLES.values()))
    return ds if ds is not None else r3.DimensionStyle()


def text_layout(text):
    """``(string, origin, x_axis, y_axis, height, font_family)`` of an `r3.Text`, in world
    units -- `height` is the drawn cap height (text height x model-space scale)."""
    ds = text_style(text)
    h = float(text.GetTextHeight(ds)) * float(text.GetDimensionScale(ds) or 1.0)
    p = text.Plane
    fam = None
    try:
        font = text.GetFont(ds)
        fam = font.FamilyName or font.QuartetName
    except Exception:  # noqa: BLE001
        pass
    vec = lambda v: (float(v.X), float(v.Y), float(v.Z))  # noqa: E731
    body = (text.PlainText or "").replace("\r\n", "\n").replace("\r", "\n")
    return body, vec(p.Origin), vec(p.XAxis), vec(p.YAxis), h, fam or "Arial"


def _text_font(family):
    """(blender font or None, cap height at size 1) for `family`, cached."""
    hit = _FONT_CACHE.get(family)
    if hit is not None:
        return hit
    import subprocess

    import bpy

    font = None
    try:
        path = subprocess.run(["fc-match", "-f", "%{file}", family], capture_output=True,
                              text=True, timeout=5).stdout.strip()
        if path:
            font = bpy.data.fonts.load(path, check_existing=True)
    except Exception:  # noqa: BLE001
        font = None
    cap = _glyph_top(font, "H") or 0.7
    _FONT_CACHE[family] = (font, cap)
    return font, cap


def _font_mesh(body, font, size):
    """Blender's filled glyph mesh for `body` as ``(verts, faces)`` in the font's own
    2-D frame (first baseline at y = 0)."""
    import bpy

    cu = bpy.data.curves.new("_narwhal_text", "FONT")
    try:
        cu.body = body
        if font is not None:
            cu.font = font
        cu.size = size
        ob = bpy.data.objects.new("_narwhal_text", cu)
        try:
            me = ob.to_mesh()
            verts = [tuple(v.co) for v in me.vertices]
            faces = [tuple(p.vertices) for p in me.polygons]
            ob.to_mesh_clear()
        finally:
            bpy.data.objects.remove(ob)
    finally:
        bpy.data.curves.remove(cu)
    return verts, faces


def _glyph_top(font, ch):
    try:
        verts, _faces = _font_mesh(ch, font, 1.0)
        return max(v[1] for v in verts) if verts else None
    except Exception:  # noqa: BLE001
        return None


def text_to_bmesh(text):
    """The glyphs of an `r3.Text`, filled, on its plane (see the TEXT note above)."""
    import bmesh

    body, o, xa, ya, h, family = text_layout(text)
    bm = _new_bmesh()
    if not body.strip() or h <= 0.0:
        return bm
    font, cap = _text_font(family)
    verts, faces = _font_mesh(body, font, h / cap)
    # cap height h; the first line's cap TOP sits on the plane origin
    bv = [bm.verts.new(tuple(o[i] + x * xa[i] + (y - h) * ya[i] for i in range(3)))
          for x, y, _z in verts]
    for f in faces:
        try:
            bm.faces.new([bv[i] for i in f])
        except ValueError:
            pass
    # one flat face per glyph region instead of a triangle fan (cleaner in Wireframe)
    bmesh.ops.dissolve_limit(bm, angle_limit=1e-3, verts=bm.verts[:], edges=bm.edges[:])
    return bm


def _has_holes(brep):
    """True when any face of `brep` has an inner loop."""
    try:
        return any(len(brep.Faces[i].Loops) > 1 for i in range(len(brep.Faces)))
    except Exception:  # noqa: BLE001
        return False


def _faces_are_mesh_facets(brep):
    """True when every face could be ONE mesh facet: a single loop of 3 or 4 trims.

    ``looks_tessellated`` measures planarity, but a modeled polysurface can be mostly
    planar without being a mesh. Complex extrusions may contain many planar faces, polygonal
    caps, and numerous holes. A brep made from a mesh (``Brep.CreateFromMesh``) has one face
    per facet, so no
    face of it has a hole or more than four sides; any face that does proves the brep was
    modelled, not tessellated. Both conditions must hold to decline.
    """
    try:
        for i in range(len(brep.Faces)):
            loops = brep.Faces[i].Loops
            if len(loops) != 1 or len(loops[0].Trims) > 4:
                return False
    except Exception:  # noqa: BLE001
        return True                      # unreadable: keep the conservative answer
    return True


#: Vertices closer than this (per axis) are merged when welding a display mesh.
#: Tight enough never to merge two genuinely distinct corners, loose enough to catch the
#: duplicates OCCT emits on a shared edge, which it computes per-face.
PROXY_WELD_TOL = 1e-7


def _weld_tessellation(verts, tris):
    """Merge coincident vertices in a raw tessellation. Returns ``(verts, tris)``.

    WHY THIS IS NECESSARY, OCCT triangulates each FACE independently, so every
    vertex along a shared edge appears once per adjoining face. Feeding that straight to
    `r3.Brep.CreateFromMesh` worked for a 264-triangle union and returned **None** for a
    785-triangle one -- the failure the original `_proxy_from_occt` hit on the first
    boolean-of-a-boolean it was asked to draw. bridge's own boolean notes already recorded
    that duplicate vertices break `CreateFromMesh`; the proxy builder simply did not weld.

    Welding also makes SMOOTH SHADING work: normals can only be averaged across a join if
    the two faces actually share the vertex, which is the same reason `_weld_shading`
    exists for the mesh route.

    Pure Python on purpose -- `bmesh` would drag `bpy` into a path that probes and tests
    exercise without a Blender window.
    """
    out_verts, out_tris, _remap = _weld_with_remap(verts, tris)
    return out_verts, out_tris


def _weld_tessellation_edges(verts, tris, edges):
    """`_weld_tessellation` that also carries the REAL-edge vertex pairs
    (`occt_backend.tessellate_with_edges`) through the weld. Returns
    ``(verts, tris, edges)``."""
    out_verts, out_tris, remap = _weld_with_remap(verts, tris)
    out_edges = sorted({(min(remap[a], remap[b]), max(remap[a], remap[b]))
                        for a, b in edges if remap[a] != remap[b]})
    return out_verts, out_tris, out_edges


def _weld_with_remap(verts, tris):
    quant = 1.0 / PROXY_WELD_TOL
    index = {}
    out_verts = []
    remap = [0] * len(verts)
    for i, v in enumerate(verts):
        key = (int(round(v[0] * quant)), int(round(v[1] * quant)),
               int(round(v[2] * quant)))
        j = index.get(key)
        if j is None:
            j = len(out_verts)
            index[key] = j
            out_verts.append((float(v[0]), float(v[1]), float(v[2])))
        remap[i] = j
    out_tris = []
    for a, b, c in tris:
        ra, rb, rc = remap[a], remap[b], remap[c]
        # A triangle whose corners collapsed onto each other has no area and would make
        # the mesh non-manifold for no benefit.
        if ra == rb or rb == rc or ra == rc:
            continue
        out_tris.append((ra, rb, rc))
    return out_verts, out_tris, remap


#: what the last `_proxy_from_occt` put in the document -- "exact" (the real
#: polysurface, via openNURBS) or "triangulated" (the old display proxy) -- and why.
_LAST_DOC_FORM = [None, None]


def last_document_form():
    """``("exact" | "triangulated", reason_or_None)`` for the last OCCT result stored."""
    return tuple(_LAST_DOC_FORM)


def _onbrep():
    try:
        from .kernel import onbrep
    except ImportError:
        from kernel import onbrep
    return onbrep


def occt_backed(shape, recipe):
    """the document object for an OCCT `shape` built from `recipe`, with the master
    registered and the recipe recorded. The object is the exact polysurface, else the
    triangulated stand-in -- marked, so saving says so."""
    k = _kernel()
    proxy = _proxy_from_occt(shape)
    k.register_master(proxy, shape)
    k.record_recipe(proxy, recipe)
    return proxy


def record_exact_recipe(geom, tolerance=1e-6):
    """give an EXACT rhino3dm surface/polysurface (a Loose or Smooth loft, a Sweep2) a
    recipe that rebuilds it BIT-FOR-BIT -- its own faces (`brep_data`, kind ``brep_data``)
    -- and register the OCCT master built from it. The document object is unchanged.
    Returns True when the master was built; False (and the reason in `last_occt_decline`)
    when OCCT could not take it, in which case nothing is recorded."""
    occt = _occt()
    if occt is None or not isinstance(geom, r3.Brep):
        return False
    k = _kernel()
    data = brep_data(geom)
    try:
        shape = occt.shape_from_brep_data(data, sew_tolerance=tolerance)
    except Exception as e:  # noqa: BLE001
        _decline(f"{type(e).__name__}: {e}")
        return False
    _decline(None)
    k.register_master(geom, shape)
    k.record_recipe(geom, {"kind": "brep_data", "data": data, "tolerance": tolerance})
    geom.SetUserString(k.DOCUMENT_FORM_KEY, "exact")
    return True


def _proxy_from_occt(shape):
    """The DOCUMENT object for an OCCT shape: its EXACT brep when possible, else a
    triangulated stand-in. The OCCT shape is registered as the master either way.

    rhino3dm cannot construct a multi-face brep, so this used to ALWAYS return
    triangles -- and that is what reached the.3dm (box-union-cylinder saved as
    100 planar triangles; real the reference CAD application opened a faceted polysurface). Now the openNURBS helper
    (`kernel/onbrep.py`) builds the real polysurface from the master and rhino3dm decodes it,
    so the FILE holds exact NURBS faces with their trims. When the helper is not available
    the triangulated proxy is used as before, and `last_document_form` says so. The
    display never reads this object for master-backed geometry -- `geometry_to_bmesh`
    tessellates the MASTER -- so both forms draw identically.
    """
    occt = _occt()
    k = _kernel()
    exact = _onbrep().exact_brep(shape, occt)
    if exact is not None:
        _LAST_DOC_FORM[:] = ["exact", None]
        exact.SetUserString(k.DOCUMENT_FORM_KEY, "exact")
        return exact

    _LAST_DOC_FORM[:] = ["triangulated", _onbrep().last_error()]
    # MARK it, so saving reports it every time and `describe` says "faceted in the
    # file" -- a small planar union triangulates to < 24 faces and `looks_tessellated` alone
    # would call it exact.
    proxy = _triangulated_proxy(shape)
    proxy.SetUserString(k.DOCUMENT_FORM_KEY, "triangulated")
    return proxy


def _triangulated_proxy(shape):
    """A triangulated `r3.Brep` that DRAWS an OCCT shape -- the pre-document form, and
    still the fallback. The same thing the reference CAD application's render mesh is; never the geometry."""
    occt = _occt()
    verts, tris = _weld_tessellation(*occt.tessellate(shape))
    mesh = r3.Mesh()
    for v in verts:
        mesh.Vertices.Add(v[0], v[1], v[2])
    for t in tris:
        mesh.Faces.AddFace(int(t[0]), int(t[1]), int(t[2]))
    brep = r3.Brep.CreateFromMesh(mesh, False)
    if brep is None or len(brep.Faces) == 0:
        raise ProxyBuildError(
            f"Brep.CreateFromMesh refused a welded {len(verts)}-vertex "
            f"{len(tris)}-triangle display mesh")
    return brep


#: Our names for the three booleans -> the kernel backend's names. `commands.py` and
#: Blender both say INTERSECT; OCCT says INTERSECTION.
_OCCT_OPS = {"UNION": "UNION", "DIFFERENCE": "DIFFERENCE", "INTERSECT": "INTERSECTION"}

#: Why the last OCCT boolean declined, or None if it succeeded. See `_decline`.
_LAST_OCCT_DECLINE = [None]


def _decline(reason):
    """Record why the analytic route declined, so the fallback is explicable."""
    _LAST_OCCT_DECLINE[0] = reason


def last_occt_decline():
    """Why the most recent boolean fell back to the mesh route, or None.

    Exists because a SILENT fallback is how the faceted-boolean defect survived: the
    result looked like a solid, nothing complained, and the facets only showed up at
    export. Callers can query the reason explicitly.
    """
    return _LAST_OCCT_DECLINE[0]


_IDENTITY16 = [1.0, 0.0, 0.0, 0.0,
               0.0, 1.0, 0.0, 0.0,
               0.0, 0.0, 1.0, 0.0,
               0.0, 0.0, 0.0, 1.0]


def _mat_mul(a, b):
    """Row-major 4x4 ``a @ b`` as a flat 16-list."""
    out = [0.0] * 16
    for i in range(4):
        for j in range(4):
            out[i * 4 + j] = sum(a[i * 4 + k] * b[k * 4 + j] for k in range(4))
    return out


class NonUniformTransformError(ValueError):
    """A non-uniform scale/shear that could not be applied EXACTLY: the object is
    left unchanged rather than given a wrong shape."""


def _occt_is_similarity(matrix):
    occt = _occt()
    if occt is not None:
        return occt.is_similarity(matrix)
    m = matrix
    cols = [(m[0], m[4], m[8]), (m[1], m[5], m[9]), (m[2], m[6], m[10])]
    g = [[sum(a * b for a, b in zip(cols[i], cols[j])) for j in range(3)] for i in range(3)]
    s2 = (g[0][0] + g[1][1] + g[2][2]) / 3.0
    return s2 > 0 and all(abs(g[i][j] - (s2 if i == j else 0.0)) <= 1e-12 * s2
                          for i in range(3) for j in range(3))


#: user strings that describe the OLD representation and must not ride onto the new one
_REPRESENTATION_KEYS = ("Narwhal3D:KernelShape", "Narwhal3D:Representation",
                        "Narwhal3D:DocumentForm", "Narwhal3D:Recipe")


def _transform_nonuniform(geom, matrix):
    """`geom` under a non-similarity, as a NEW exact document object."""
    occt = _occt()
    if occt is None:
        raise NonUniformTransformError(
            "a non-uniform scale of a surface or solid needs the OCCT kernel, which is not "
            "installed -- the object was left unchanged")
    k = _kernel()
    shape, recipe = _occt_operand(geom), k.recipe_of(geom)
    if shape is None or not recipe:
        work = geom if isinstance(geom, r3.Brep) else geom.ToBrep(False)
        if work is not None and record_exact_recipe(work):
            shape, recipe = k.master_of(work), k.recipe_of(work)
    if shape is None or not recipe:
        raise NonUniformTransformError(
            "this object has no exact shape, so a non-uniform scale cannot be applied "
            "exactly -- it was left unchanged")
    try:
        moved = occt.transform(shape, matrix)
    except Exception as e:  # noqa: BLE001
        raise NonUniformTransformError(f"the non-uniform scale failed ({e}) -- the object "
                                       f"was left unchanged") from e
    updated = dict(recipe)
    updated["xform"] = _mat_mul(matrix, recipe.get("xform") or _IDENTITY16)
    new = occt_backed(moved, updated)
    getter = getattr(geom, "GetUserStrings", None)
    if callable(getter):
        try:
            for key, value in getter() or ():
                if key not in _REPRESENTATION_KEYS:
                    new.SetUserString(key, value)
        except Exception:  # noqa: BLE001
            pass
    frame = picture_frame(geom)
    if frame is not None:
        rows = [matrix[r * 4:(r + 1) * 4] for r in range(4)]
        pic = _picture()
        new.SetUserString(pic.KEY, pic.frame_to_json(pic.transform_frame(frame, rows)))
    return new


def transform_geometry(geom, xform):
    """Move `geom` AND everything that must move with it. Use this, never `.Transform`.

    Boolean results pair an OCCT master with a display proxy. A bare
    ``fobj.Geometry.Transform(xform)`` moves only the proxy, silently leaving the master
    and construction recipe behind:

      1. Move a unioned solid 100 units: the viewport showed it at x=102 while the master
         stayed at x=2. A STEP export would have sent a machine shop the WRONG POSITION.
      2. A primitive's recorded RECIPE did not move either, so rebuilding it put it back at
         its original place.
      3. Worst: ``box MINUS a cylinder moved 20 units AWAY`` returned 7 faces
         {PLANE: 6, CYLINDER: 1}, volume 35.7257 -- it DRILLED A HOLE the user never asked
         for, where the right answer was the untouched box at 64.0. Plausible-looking and
         completely wrong.

    So all three representations move together here:

    * the **proxy** (or the plain brep/curve), by `rhino3dm`;
    * the **master**, by OCCT, and re-registered under a FRESH key. Fresh, not in place,
      because `Duplicate` copies user strings: a COPIED proxy shares its original's master
      id, and mutating that entry would silently corrupt the original object too. OCCT
      transforms return a new shape, so the two never alias.
    * the **recipe**, by composing ``xform @ recipe_xform`` -- the recipe is "build it
      canonically, then apply this matrix", so a later move simply pre-multiplies.

    Returns the geometry the document must now hold -- `geom` itself, EXCEPT for a
    non-uniform scale or shear of a surface/solid (below), where it is a NEW object
    the caller must put in place of the old one (`NarwhalModel.replace_geometry`).

    rhino3dm's ``Brep.Transform`` cannot reliably apply a non-similarity to
    surfaces of revolution -- a cylinder scaled (2,1,1) came back `IsValid False` and still
    CIRCULAR (+-6 in X and Y); a sphere reported valid and was equally wrong. And the OCCT
    side projected the matrix onto a uniform scale. So under a non-similarity a brep is
    rebuilt from its exact OCCT shape (master, recipe, or its own faces), transformed by
    `occt_backend.transform` (now `gp_GTrsf` for these), as a fresh exact document object.
    Raises `NonUniformTransformError` -- never returns a wrong solid -- when it cannot.
    """
    k = _kernel()
    matrix = _xform_matrix(xform)

    if isinstance(geom, (r3.Brep, r3.Extrusion)) and not _occt_is_similarity(matrix):
        return _transform_nonuniform(geom, matrix)

    master = k.master_of(geom)
    recipe = k.recipe_of(geom)

    geom.Transform(xform)

    if master is not None:
        occt = _occt()
        if occt is not None:
            try:
                k.register_master(geom, occt.transform(master, matrix))
            except Exception:  # noqa: BLE001
                # If the master cannot follow, the proxy must NOT keep claiming one --
                # a stale master is the exact failure this function exists to stop.
                k.drop_master(geom)
        else:
            k.drop_master(geom)

    # EVERY recipe records the move, not only those that already carried an `xform`.
    #
    # This used to be `if recipe and recipe.get("xform")`. Primitive recipes carry an xform, so
    # moving a cylinder worked; but boolean, fillet, chamfer, cap and pipe recipes are built
    # without one, so moving THEM updated the master in memory and left the recipe behind.
    # a unioned solid moved +100 in X, saved, and reopened in a fresh process came
    # back with its proxy at x = 100..104 and its MASTER rebuilt at centroid x = 2 -- the part a
    # machine shop receives, silently back where it started.
    #
    # `occt_backend.from_recipe` already applies `xform` after building ANY kind, so composing
    # onto identity here is all a compound recipe needs: rebuild the boolean from its operands
    # where they were, then apply the move.
    if recipe:
        updated = dict(recipe)
        updated["xform"] = _mat_mul(matrix, recipe.get("xform") or _IDENTITY16)
        k.record_recipe(geom, updated)

    # a Picture's image FRAME moves with it, or the texture would slide off the
    # surface (UVs are solved against the stored frame, not the surface's own domain).
    frame = picture_frame(geom)
    if frame is not None:
        rows = [matrix[r * 4:(r + 1) * 4] for r in range(4)]
        pic = _picture()
        geom.SetUserString(pic.KEY, pic.frame_to_json(pic.transform_frame(frame, rows)))
    return geom


# ---------------------------------------------------------------------------
# ProjectToCPlane -- exact affine document geometry
# ---------------------------------------------------------------------------

def project_to_cplane_geometry(geom, plane):
    """Project supported document geometry orthogonally onto ``plane``.

    (`the command behavior`): the reference CAD application projects every control point and keeps
    the same control-point structure.  Projection onto a plane is affine, so this is
    exact for points, curves and NURBS surfaces; meshes retain their vertex/face
    topology.  The input is always duplicated.

    A singular transform makes a ``Brep`` invalid and an ``Extrusion`` rejects the
    transform in rhino3dm 8.35.0.  Those types are therefore refused by name rather
    than flattening a display mesh, dropping trims, or pretending a collapsed solid
    is manufacturable.
    """
    project = _sibling("project")
    label = type(geom).__name__

    try:
        if isinstance(geom, r3.Point):
            xyz = project.project_point(geom.Location, plane)
            return r3.Point(r3.Point3d(*xyz)), "Projected point to the construction plane."
        if isinstance(geom, r3.Brep):
            frame = picture_frame(geom)
            if frame is not None:
                try:
                    origin, u, v = _picture_frame_components(frame)
                except ValueError as exc:
                    return None, f"Picture: invalid stored frame -- {exc}"
                projected_origin = project.project_point(origin, plane)
                projected_u_point = project.project_point(
                    tuple(origin[i] + u[i] for i in range(3)), plane)
                projected_v_point = project.project_point(
                    tuple(origin[i] + v[i] for i in range(3)), plane)
                projected_frame = dict(frame)
                projected_frame["origin"] = list(projected_origin)
                projected_frame["u"] = [projected_u_point[i] - projected_origin[i]
                                          for i in range(3)]
                projected_frame["v"] = [projected_v_point[i] - projected_origin[i]
                                          for i in range(3)]
                try:
                    _picture_frame_components(projected_frame)
                except ValueError:
                    return None, ("Picture: projection is edge-on and collapses its "
                                  "frame to a line")
                return make_picture(projected_frame), \
                    "Projected Picture to the construction plane."
        if isinstance(geom, r3.Point3d):
            xyz = project.project_point(geom, plane)
            return r3.Point3d(*xyz), "Projected point to the construction plane."
        if isinstance(geom, r3.Curve):
            out = project.project_curve(geom, plane)
            return out, "Projected curve to the construction plane."
        if isinstance(geom, r3.NurbsSurface):
            out = geom.Duplicate()
            if out is None or not out.Transform(project.projection_transform(plane)) \
                    or not out.IsValid:
                return None, f"{label}: projection did not produce a valid surface"
            return out, "Projected NURBS surface to the construction plane."
        if isinstance(geom, r3.Mesh):
            out = geom.Duplicate()
            if out is None or not out.Transform(project.projection_transform(plane)) \
                    or not out.IsValid:
                return None, f"{label}: projection did not produce a valid mesh"
            return out, "Projected mesh to the construction plane."
    except ValueError as exc:
        return None, f"{label}: {exc}"

    if isinstance(geom, r3.Brep):
        reason = "a singular projection invalidates its trims or solid topology"
    elif isinstance(geom, r3.Extrusion):
        reason = "rhino3dm rejects a singular transform of an extrusion"
    elif isinstance(geom, r3.Surface):
        reason = "only NurbsSurface control structure has been proved preserved"
    else:
        reason = "this document geometry type has not been proved safe to project"
    return None, f"{label}: not implemented yet -- {reason}"


# ---------------------------------------------------------------------------
# Picture -- an image on an exact planar surface
# ---------------------------------------------------------------------------

def _picture():
    """The pure `picture` module however `bridge` was loaded (see `_edgepick`)."""
    return _sibling("picture")


def _sibling(modname):
    try:
        return importlib.import_module(f"{__package__}.{modname}") if __package__ \
            else importlib.import_module(modname)
    except ImportError:
        pass
    import os
    import sys

    name = f"narwhal3d.{modname}"
    if name in sys.modules:
        return sys.modules[name]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"{modname}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def picture_frame(geom):
    """The image frame stored on a Picture object (dict), or None for anything else."""
    try:
        text = geom.GetUserString("Narwhal3D:Picture")
    except Exception:  # noqa: BLE001
        return None
    return _picture().frame_from_json(text) if text else None


def _picture_frame_components(frame):
    """Validated ``(origin, u, v)`` tuples for an affine Picture frame."""
    try:
        values = tuple(tuple(float(c) for c in frame[key])
                       for key in ("origin", "u", "v"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Picture: origin, u and v must be numeric 3-D vectors") from exc
    if any(len(value) != 3 or not all(math.isfinite(c) for c in value)
           for value in values):
        raise ValueError("Picture: origin, u and v must be finite 3-D vectors")
    origin, u, v = values
    length_u = math.sqrt(sum(c * c for c in u))
    length_v = math.sqrt(sum(c * c for c in v))
    if length_u <= 1e-12 or length_v <= 1e-12:
        raise ValueError("Picture: the rectangle has no size")
    cross = (u[1] * v[2] - u[2] * v[1],
             u[2] * v[0] - u[0] * v[2],
             u[0] * v[1] - u[1] * v[0])
    area = math.sqrt(sum(c * c for c in cross))
    if area <= 1e-12 * length_u * length_v:
        raise ValueError("Picture: the frame vectors are collinear")
    return origin, u, v


def make_picture(frame):
    """The Picture's EXACT planar surface: a one-face brep spanning the affine frame
    (origin, origin + u, origin + u + v, origin + v), with the frame stored on it.

    (`the command behavior`): "draws a rectangular planar surface". A 2x2 degree-1
    `r3.NurbsSurface` also preserves a frame that an affine operation has sheared; there is
    nothing to facet. Raises ValueError for a degenerate frame.
    """
    origin, u, v = _picture_frame_components(frame)
    corners = (
        origin,
        tuple(origin[i] + u[i] for i in range(3)),
        tuple(origin[i] + u[i] + v[i] for i in range(3)),
        tuple(origin[i] + v[i] for i in range(3)),
    )
    surface = r3.NurbsSurface.Create(3, False, 2, 2, 2, 2)
    if surface is None:
        raise ValueError("Picture: could not allocate the planar surface")
    surface.KnotsU[0], surface.KnotsU[1] = 0.0, 1.0
    surface.KnotsV[0], surface.KnotsV[1] = 0.0, 1.0
    for indices, point in (((0, 0), corners[0]), ((1, 0), corners[1]),
                           ((1, 1), corners[2]), ((0, 1), corners[3])):
        surface.Points[indices] = r3.Point4d(*point, 1.0)
    if not surface.IsValid:
        raise ValueError("Picture: could not build a valid planar surface")
    brep = r3.Brep.CreateFromSurface(surface)
    if brep is None or not brep.IsValid:
        raise ValueError("Picture: could not build the planar surface")
    stored_frame = dict(frame)
    stored_frame["origin"], stored_frame["u"], stored_frame["v"] = \
        [list(value) for value in (origin, u, v)]
    brep.SetUserString(_picture().KEY, _picture().frame_to_json(stored_frame))
    return brep


# ---------------------------------------------------------------------------
# Trim / Split a PLANAR surface by curves -- exactly
# ---------------------------------------------------------------------------

def _planar_face_brep(geom):
    """The single PLANAR face of `geom` (a one-face brep), or None."""
    faces = getattr(geom, "Faces", None)
    if not isinstance(geom, r3.Brep) or faces is None or len(faces) != 1:
        return None
    try:
        return faces[0] if faces[0].IsPlanar() else None
    except Exception:  # noqa: BLE001
        return None


def planar_face_shape(geom):
    """The exact OCCT face of a planar surface object, or None when it is not one.

    A master-backed piece (a trimmed face with a hole, ) answers with its MASTER --
    its display proxy is triangles and must never be read. A plain one-face planar brep (a
    Picture, a trimmed plane, a box face from Explode) is rebuilt from its own boundary:
    every trim's 3-D edge serialized by `curve_recipe`, so a circular edge stays a
    `Geom_Circle`.
    """
    k = _kernel()
    occt = _occt()
    if occt is None:
        return None
    master = k.master_of(geom)
    if master is not None:
        faces = occt.unique_faces(master)
        if len(faces) == 1 and occt.face_plane(faces[0]) is not None:
            return faces[0]
        return None
    face = _planar_face_brep(geom)
    if face is None:
        return None
    loops = []
    ordered = [face.OuterLoop] + [lp for lp in face.Loops
                                  if lp.LoopType != r3.BrepLoopType.Outer]
    for lp in ordered:
        curves = []
        for ti in range(len(lp.Trims)):
            ei = lp.Trims[ti].EdgeIndex
            if ei < 0:
                continue
            d = curve_recipe(geom.Edges[ei].ToNurbsCurve())
            if d is None:
                return None
            curves.append(d)
        loops.append(curves)
    try:
        return occt.face_from_curve_loops(loops)
    except Exception:  # noqa: BLE001
        return None


def _project_to_plane(curve, origin, normal):
    """`curve` projected along `normal` onto the plane through `origin` -- exact for a
    NURBS (a parallel projection is affine, so it maps the control points)."""
    n = normal
    t = r3.Transform(1.0)
    for r in range(3):
        for c in range(3):
            setattr(t, f"M{r}{c}", (1.0 if r == c else 0.0) - n[r] * n[c])
        k = sum(n[c] * origin[c] for c in range(3))
        setattr(t, f"M{r}3", n[r] * k)
    dup = curve.Duplicate()
    dup.Transform(t)
    return dup


def _piece_geometry(face, source):
    """The document geometry for one OCCT planar piece, carrying `source`'s Picture frame.

    ONE outer loop: `r3.Brep.CreateTrimmedPlane` builds the exact trimmed face natively
    (valid, one face, its curved edges intact, survives a.3dm round-trip).
    WITH HOLES: rhino3dm cannot build one, so the piece is a MASTER (the exact OCCT face) +
    display PROXY, like a boolean -- and, like a boolean, it reaches the.3dm as triangles
    until writes real breps. Either way a `planar_face` recipe records the exact
    boundary, so it rebuilds after a reload and measures exactly.
    """
    occt = _occt()
    k = _kernel()
    loops = occt.face_loops(face)
    recipe = {"kind": "planar_face", "loops": loops}
    geom = None
    if len(loops) == 1:
        o, x, y, _n = occt.face_plane(face)
        pc = r3.PolyCurve()
        for d in loops[0]:
            pc.Append(curve_from_recipe(d))
        trimmed = r3.Brep.CreateTrimmedPlane(
            r3.Plane(r3.Point3d(*o), r3.Vector3d(*x), r3.Vector3d(*y)), pc)
        if trimmed is not None and trimmed.IsValid and len(trimmed.Faces) == 1:
            geom = trimmed
    if geom is None:
        geom = _proxy_from_occt(face)
        k.register_master(geom, face)
    k.record_recipe(geom, recipe)
    frame = picture_frame(source)
    if frame is not None:
        geom.SetUserString("Narwhal3D:Picture", _picture().frame_to_json(frame))
    return geom


def split_planar_surface(geom, cutters):
    """the reference CAD application's Split on a PLANAR surface: the pieces the `cutters` (curves) make.

    The cutters are projected into the surface's plane and the face is split EXACTLY by
    OCCT (`occt_backend.split_face`). Returns the list of piece geometries -- one piece (the
    surface unchanged) when nothing separates it. Raises ValueError, by name, for a surface
    that is not planar or when the kernel is unavailable; the alternative mesh splitter
    would facet curved geometry.
    """
    occt = _occt()
    if occt is None:
        raise ValueError("splitting a surface needs the OCCT kernel, which is not installed")
    face = planar_face_shape(geom)
    if face is None:
        raise ValueError("only PLANAR surfaces can be split or trimmed so far -- a curved "
                         "surface would need the mesh-derived trimmer, which facets it")
    o, _x, _y, n = occt.face_plane(face)
    wires = []
    for c in cutters:
        d = curve_recipe(_project_to_plane(c, o, n))
        if d is None:
            raise ValueError("a cutting curve could not be carried to the kernel")
        wires.append(occt.wire_from_curve(d))
    pieces = occt.split_face(face, wires)
    if len(pieces) < 2:
        return [geom]
    return [_piece_geometry(p, geom) for p in pieces]


def trim_planar_surface(geom, cutters, pick):
    """the reference CAD application's Trim on a PLANAR surface: split, then DROP the piece nearest `pick`
    ("Select the parts... to trim away", ). Returns the pieces kept, or the
    surface unchanged (one-element list) when the cutters do not separate it."""
    pieces = split_planar_surface(geom, cutters)
    if len(pieces) < 2:
        return pieces
    occt = _occt()
    k = _kernel()

    def dist(g):
        shape = k.master_of(g) or occt.from_recipe(k.recipe_of(g))
        return occt.distance_to_shape(shape, pick)

    drop = min(range(len(pieces)), key=lambda i: dist(pieces[i]))
    return [p for i, p in enumerate(pieces) if i != drop]


def surface_face_shape(geom, record=True):
    """``(face, recipe)`` -- the exact OCCT face of a SINGLE-FACE surface object
    (planar or curved) and the recipe it rebuilds from, or ``(None, reason)``.

    The master when there is one, else the recipe; a plain rhino3dm surface with neither
    (a Revolve, a NURBS patch) gets its own faces as its recipe (`record_exact_recipe`)."""
    occt = _occt()
    if occt is None:
        return None, "the OCCT kernel is not installed"
    if not isinstance(geom, r3.Brep):
        return None, "not a surface"
    k = _kernel()
    shape, recipe = _occt_operand(geom), k.recipe_of(geom)
    if shape is None or not recipe:
        if record:
            if record_exact_recipe(geom):
                shape, recipe = k.master_of(geom), k.recipe_of(geom)
        elif shape is None:
            # a PICK test must not change the document: build the shape, record nothing
            try:
                shape = occt.shape_from_brep_data(brep_data(geom))
            except Exception:  # noqa: BLE001
                shape = None
            recipe = recipe or "transient"
    if shape is None or not recipe:
        return None, "it has no exact shape"
    faces = occt.unique_faces(shape)
    if len(faces) != 1:
        return None, (f"it is a polysurface ({len(faces)} faces) -- splitting a "
                      f"polysurface is not implemented yet")
    return faces[0], recipe


def split_surface(geom, cutters, direction=None):
    """the reference CAD application's Split on ANY single-face surface: the pieces the `cutters` (curves) make,
    each EXACT (; a planar surface keeps 's route).

    How each curve meets the surface follows the command behavior (`occt_backend.curve_tools_on_face`):
    on the surface -> as is; planar -> projected along its plane normal; a line (no unique
    plane) -> along `direction` (the command passes the active CPlane Z -- the plan-view
    case); 3-D -> pulled by closest points. Returns ``(pieces, report)``; one piece (the
    surface itself) when nothing separates it. Raises ValueError by name."""
    if is_planar_surface(geom):
        return split_planar_surface(geom, cutters), ["planar surface"]
    occt = _occt()
    face, recipe = surface_face_shape(geom)
    if face is None:
        raise ValueError(f"that surface cannot be split: {recipe}")
    curves = []
    for c in cutters:
        d = curve_recipe(c)
        if d is None:
            raise ValueError("a cutting curve could not be carried to the kernel")
        # a LINE has no unique plane (`profile_normal` still returns one for it),
        # so it goes along `direction` -- the reference CAD application's plan-view rule
        if c.IsLinear():
            n = tuple(float(v) for v in direction) if direction is not None else None
        else:
            n = _true_plane_normal(c)
        curves.append([d, list(n) if n is not None else None])
    tools, report = occt.curve_tools_on_face(face, [(d, n) for d, n in curves])
    if not tools:
        return [geom], report
    pieces = occt.merge_seam_pieces(face, occt.split_face(face, tools))
    if len(pieces) < 2:
        return [geom], report
    out = []
    for p in pieces:
        out.append(occt_backed(p, {"kind": "split_piece", "a": recipe, "curves": curves,
                                   "signature": occt.face_signature(p)}))
    return out, report


def _true_plane_normal(c, rel_tol=1e-9):
    """The curve's plane normal ONLY if it really is planar. `profile_normal` returns a Newell normal for ANY closed curve -- a 3-D loop winding
    around a cylinder got Z and was \"projected\" along Z, missing the wall entirely."""
    n = profile_normal(c)
    if n is None:
        return None
    d = c.Domain
    pts = [c.PointAt(d.T0 + (d.T1 - d.T0) * i / 64) for i in range(65)]
    o = pts[0]
    size = max(max(abs(p.X - o.X), abs(p.Y - o.Y), abs(p.Z - o.Z)) for p in pts) or 1.0
    dev = max(abs((p.X - o.X) * n[0] + (p.Y - o.Y) * n[1] + (p.Z - o.Z) * n[2]) for p in pts)
    return n if dev <= rel_tol * max(1.0, size) else None


def trim_surface(geom, cutters, pick, direction=None):
    """the reference CAD application's Trim on any single-face surface: split, then DROP the piece nearest `pick`."""
    pieces, report = split_surface(geom, cutters, direction)
    if len(pieces) < 2:
        return pieces, report
    occt = _occt()
    k = _kernel()

    def dist(g):
        shape = k.master_of(g) or occt.from_recipe(k.recipe_of(g))
        return occt.distance_to_shape(shape, pick)

    drop = min(range(len(pieces)), key=lambda i: dist(pieces[i]))
    return [p for i, p in enumerate(pieces) if i != drop], report


def surface_distance(geom, point):
    """Distance from `point` to a single-face surface object's exact face (inf if none)."""
    if is_planar_surface(geom):
        return planar_surface_distance(geom, point)
    face, _r = surface_face_shape(geom, record=False) if isinstance(geom, r3.Brep) \
        else (None, None)
    return _occt().distance_to_shape(face, point) if face is not None else float("inf")


def planar_surface_distance(geom, point):
    """Distance from `point` to a planar surface object's exact face (inf if not one)."""
    occt = _occt()
    face = planar_face_shape(geom) if occt is not None else None
    return occt.distance_to_shape(face, point) if face is not None else float("inf")


def is_single_surface(geom):
    """True for a one-face surface object, planar or curved (not a polysurface, not a
    solid). Read from the document object's own faces -- no kernel call, no side effect."""
    if not isinstance(geom, r3.Brep) or geom.IsSolid:
        return False
    master = _kernel().master_of(geom)
    if master is not None:
        occt = _occt()
        return occt is not None and len(occt.unique_faces(master)) == 1
    return len(geom.Faces) == 1


def is_planar_surface(geom):
    """True for a one-face planar surface object (plain or master-backed)."""
    if _planar_face_brep(geom) is not None and _kernel().master_of(geom) is None:
        return True
    occt = _occt()
    master = _kernel().master_of(geom)
    if master is None or occt is None:
        return False
    faces = occt.unique_faces(master)
    return len(faces) == 1 and occt.face_plane(faces[0]) is not None


def _apply_picture_uvs(bm, frame):
    """Give a Picture's display mesh UVs solved against its frame (0..1 across the
    ORIGINAL rectangle, so a trimmed piece keeps its part of the image)."""
    pic = _picture()
    layer = bm.loops.layers.uv.verify()
    for face in bm.faces:
        for loop in face.loops:
            co = loop.vert.co
            loop[layer].uv = pic.uv_of(frame, (co.x, co.y, co.z))


def _edgepick():
    """The edge picker, however `bridge` itself was loaded.

    Package and top-level imports do not cover direct-file loading when no parent package is
    available and the module directory is absent from `sys.path`. The third fallback loads
    the sibling module from this file's directory, independent of how `bridge` was loaded.
    """
    try:
        from . import edgepick
        return edgepick
    except ImportError:
        pass
    try:
        import edgepick
        return edgepick
    except ImportError:
        pass
    import importlib.util
    import os
    import sys

    name = "narwhal3d.edgepick"
    if name in sys.modules:
        return sys.modules[name]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "edgepick.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def edge_curve_data(geom):
    """The serialized edge curves of a solid, with their picking `index`.

    From the OCCT master when there is one. Returns [] otherwise -- the picking code is
    kernel-independent, but the EDGES still have to come from somewhere, and a rhino3dm
    display proxy's edges are the triangulation's, which are not the real ones.
    """
    k = _kernel()
    occt = _occt()
    master = k.master_of(geom)
    if master is None or occt is None:
        return []
    return occt.edge_curves(master)


def pipe_occt(rail, radius, cap=False):
    """An ANALYTIC pipe along `rail`, or None if OCCT cannot carry the rail.

    Returns the display proxy with its master registered, like the booleans. a
    circular profile along a quarter-circle rail is **exactly one TORUS face**, and capped the
    volume matches an exact quarter-torus to 1.4e-14 % -- against a skinned loft that missed
    the ideal by 3.8e-05 and could never be exact, because its stations are samples.
    """
    occt = _occt()
    if occt is None:
        return None
    profile = curve_recipe(rail)
    if profile is None:
        _decline("the rail could not be serialized for the kernel")
        return None
    recipe = {"kind": "pipe", "rail": profile, "radius": float(radius)}
    if cap:
        recipe = {"kind": "cap", "a": recipe}
    try:
        shape = occt.from_recipe(recipe)
        proxy = _proxy_from_occt(shape)
    except Exception as e:  # noqa: BLE001
        _decline(f"{type(e).__name__}: {e}")
        return None
    _decline(None)
    k = _kernel()
    k.register_master(proxy, shape)
    k.record_recipe(proxy, recipe)
    return proxy


def document_object_from_shape(shape):
    """the document object for an IMPORTED OCCT shape (a STEP solid, shell or face):
    its exact polysurface  or the triangulated stand-in, with the shape registered as
    its master. It has no recipe -- like a polysurface from real the reference CAD application -- and after a save
    and reopen it is ADOPTED again from its own exact faces."""
    proxy = _proxy_from_occt(shape)
    _kernel().register_master(proxy, shape)
    return proxy


def mesh_from_triangles(vertices, faces):
    """An `r3.Mesh` from vertices and 3- or 4-sided faces (0-based).

    The result is explicitly mesh geometry rather than an analytic surface.
    """
    mesh = r3.Mesh()
    for v in vertices:
        mesh.Vertices.Add(float(v[0]), float(v[1]), float(v[2]))
    for f in faces:
        if len(f) == 3:
            mesh.Faces.AddFace(int(f[0]), int(f[1]), int(f[2]))
        elif len(f) == 4:
            mesh.Faces.AddFace(int(f[0]), int(f[1]), int(f[2]), int(f[3]))
        else:                                   # an n-gon: a fan, exactly its area
            for i in range(1, len(f) - 1):
                mesh.Faces.AddFace(int(f[0]), int(f[i]), int(f[i + 1]))
    return mesh


def mesh_to_bmesh(mesh):
    """A document MESH drawn as itself (no smoothing across its facets -- it IS facets)."""
    bm = _new_bmesh()
    bv = [bm.verts.new((v.X, v.Y, v.Z)) for v in mesh.Vertices]
    for i in range(len(mesh.Faces)):
        f = mesh.Faces[i]
        idx = [f[0], f[1], f[2]] + ([f[3]] if f[3] != f[2] else [])
        try:
            bm.faces.new([bv[j] for j in idx])
        except ValueError:
            pass                                # a duplicate face: bmesh refuses it
    return bm


def make_planar_srf(curves):
    """PlanarSrf -- the planar surfaces bounded by closed coplanar `curves`, holes
    from curves wholly inside others (the command behavior). Each piece is
    EXACT: one outer loop is a native trimmed plane, a holed piece a master + exact
    polysurface ('s `_piece_geometry`), both with a `planar_face` recipe.
    Raises ValueError by name: an open or non-planar curve, curves not in one plane."""
    occt = _occt()
    if occt is None:
        raise ValueError("PlanarSrf needs the OCCT kernel, which is not installed")
    if not curves:
        raise ValueError("select planar curves to build the surface")
    plane = None                       # (origin, unit normal) of the first curve
    loops = []
    for c in curves:
        if not c.IsClosed:
            raise ValueError("PlanarSrf needs CLOSED curves (an open curve bounds nothing)")
        n = profile_normal(c)
        if n is None:
            raise ValueError("PlanarSrf needs PLANAR curves")
        if plane is None:
            o = c.PointAtStart
            plane = ((o.X, o.Y, o.Z), n)
        (ox, oy, oz), (nx, ny, nz) = plane
        dom = c.Domain
        for i in range(33):
            q = c.PointAt(dom.T0 + (dom.T1 - dom.T0) * i / 32)
            if abs((q.X - ox) * nx + (q.Y - oy) * ny + (q.Z - oz) * nz) > 1e-7:
                raise ValueError("the curves are not all in the same plane")
        d = curve_recipe(c)
        if d is None:
            raise ValueError("a curve could not be carried to the kernel")
        loops.append([d])
    pieces = []
    for face_loops in occt.planar_regions(loops):
        face = occt.face_from_curve_loops(face_loops)
        pieces.append(_piece_geometry(face, None))
    return pieces


def make_extrude_srf(geom, distance, direction=None, both=False, solid=True):
    """ExtrudeSrf -- a surface (or one face) extruded straight into a SOLID.

    (the command behavior): "A planar surface extrudes vertically to the
    surface plane by default"; `direction` (a unit vector) overrides that, and is REQUIRED
    for a non-planar surface (the command passes the CPlane Z, as the help says). `both`
    = BothSides; `solid=False` gives the open side surfaces only. Exact (OCCT
    `MakePrism`), with a `prism` recipe nesting the operand's, so it booleans and reloads.
    """
    occt = _occt()
    if occt is None:
        raise ValueError("ExtrudeSrf needs the OCCT kernel, which is not installed")
    k = _kernel()
    shape = planar_face_shape(geom)
    base = k.recipe_of(geom)
    if shape is not None:
        if not base:
            base = {"kind": "planar_face", "loops": occt.face_loops(shape)}
    else:
        shape = _occt_operand(geom)
        if (shape is None or not base) and isinstance(geom, r3.Brep) \
                and record_exact_recipe(geom):
            shape, base = k.master_of(geom), k.recipe_of(geom)
    if shape is None or not base:
        raise ValueError("that surface has no exact shape to extrude")
    if direction is None:
        faces = occt.unique_faces(shape)
        pl = occt.face_plane(faces[0]) if len(faces) == 1 else None
        if pl is None:
            raise ValueError("a non-planar surface needs an extrusion direction")
        direction = pl[3]
    d = float(distance)
    if abs(d) <= 0.0:
        raise ValueError("the extrusion distance must not be zero")
    vec = [direction[0] * d, direction[1] * d, direction[2] * d]
    recipe = {"kind": "prism", "a": base, "dir": vec, "both": bool(both),
              "solid": bool(solid)}
    try:
        out = occt.make_prism(shape, vec, both=both, solid=solid)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"ExtrudeSrf: {e}") from e
    if solid and not occt.is_airtight(out):
        raise ValueError("ExtrudeSrf: the result is not a closed solid")
    return occt_backed(out, recipe)


def gumball_extrude(geom, axis_dir, distance, part=None, both=False, solid=True,
                    extend=False):
    """ what a Gumball EXTRUDE drag makes. Returns a NEW exact object
    (master + recipe) or raises ValueError, naming what is not built.

    (the command behavior, extrude): "A closed planar curve or a surface can be
    extruded as a solid. An inner edge loop of a surface can be extruded as a separate
    solid. Drag with Ctrl to force extruding a closed planar curve or a surface as an open
    object." "Both sides -- Shift... only available when a surface or a curve is
    selected." "Extend Face -- Shift... only available when all the surrounding faces are
    planar."

    * `axis_dir` is the Gumball axis that was dragged (any length), `distance` the signed
      drag along it;
    * a CURVE: a closed planar curve with `solid` gives a capped solid, anything else a
      surface (`extrude_curve`, recipe `extrusion`);
    * a SURFACE (one face, no `part`): `make_extrude_srf` gives a solid, or the open sides
      when `solid` is False (Ctrl);
    * `part=("face", id)` of a SOLID (id): PUSH/PULL. That planar face is extruded
      along the axis as a `prism` and UNIONED when it moves outward, SUBTRACTED when it
      moves inward. The result must be ONE airtight solid. Its recipe nests the solid's
      recipe, so it reloads and booleans;
    * not implemented, and refused by name: `extend` (Extend Face), inner loops / edges /
      vertices, a curved face, `both` on a solid's face (the help allows Shift only for
      curves and surfaces), and an axis lying in the face's plane.
    """
    occt = _occt()
    if occt is None:
        raise ValueError("a Gumball extrude needs the OCCT kernel, which is not installed")
    if extend:
        raise ValueError("Gumball Extend Face is not implemented yet")
    ax = [float(c) for c in axis_dir]
    n = math.sqrt(sum(c * c for c in ax))
    if n <= 0.0:
        raise ValueError("the extrude axis has no direction")
    ax = [c / n for c in ax]
    d = float(distance)
    if d == 0.0:
        raise ValueError("the extrusion distance must not be zero")
    if part is None:
        if isinstance(geom, r3.Curve):
            closed_planar = bool(geom.IsClosed) and profile_normal(geom) is not None
            return extrude_curve(geom, d, direction=ax, both=both,
                                 solid=bool(solid) and closed_planar)
        if isinstance(geom, (r3.Brep, r3.Extrusion, r3.Surface)):
            source, shape = _subobject_source(geom)
            faces = occt.unique_faces(shape) if source in ("master", "recipe") else None
            count = len(faces) if faces is not None else len(getattr(shape, "Faces", ()))
            if count != 1:
                raise ValueError("extruding a whole POLYSURFACE is not implemented yet -- "
                                 "pick one of its faces (Ctrl+Shift) to push or pull it")
            return make_extrude_srf(geom, d, direction=ax, both=both, solid=bool(solid))
        raise ValueError(f"a {type(geom).__name__} cannot be extruded")

    kind, sub_id = part
    if kind != "face":
        raise ValueError(f"extruding a {kind} with the Gumball is not implemented yet "
                         f"(inner edge loops included) -- a face is")
    if both:
        raise ValueError("extruding a solid's face to both sides is not implemented "
                         "(the help allows Shift only for a curve or a surface)")
    source, shape = _subobject_source(geom)
    base = _kernel().recipe_of(geom) if source in ("master", "recipe") else None
    if source not in ("master", "recipe") or not base:
        raise ValueError("that object has no exact solid (no recipe), so its face cannot "
                         "be pushed or pulled exactly")
    if not occt.is_airtight(shape):
        raise ValueError("push/pull works on a closed solid")
    faces = occt.unique_faces(shape)
    i = int(sub_id)
    if i < 1 or i > len(faces):
        raise SubObjectError(f"face {i} is out of range; this solid has {len(faces)} faces "
                             f"numbered 1..{len(faces)}")
    plane = occt.face_plane(faces[i - 1])
    if plane is None:
        raise ValueError("pushing or pulling a CURVED face is not implemented yet")
    along = sum(a * b for a, b in zip(ax, plane[3]))
    if abs(along) < 1e-9:
        raise ValueError("the extrude axis lies in the face's plane, so it cannot push or "
                         "pull it")
    vec = [c * d for c in ax]
    outward = sum(a * b for a, b in zip(vec, plane[3])) > 0.0
    # The tool is the face REBUILT from its own exact loops (`planar_face`), not the solid's
    # face itself. on a drilled box's top face: a prism of the solid's own face
    # shares its edges with the solid, and the union then kept the hole as TWO coaxial
    # CYLINDER faces that UnifySameDomain would not merge. The same prism built from the
    # rebuilt face unions to ONE cylinder. It is also self-contained, so the recipe does
    # not depend on face numbering.
    tool = {"kind": "prism",
            "a": {"kind": "planar_face", "loops": occt.face_loops(faces[i - 1])},
            "dir": vec, "both": False, "solid": True}
    recipe = {"kind": "boolean", "op": "UNION" if outward else "DIFFERENCE",
              "a": base, "b": tool}
    try:
        out = occt.from_recipe(recipe)
    except Exception as e:  # noqa: BLE001
        if "no solid" in str(e):
            raise ValueError("that push leaves nothing -- it goes right through the solid, "
                             "so it does not leave ONE closed solid; nothing changed") from e
        raise ValueError(f"the push/pull failed ({e})") from e
    if occt.solid_count(out) != 1 or not occt.is_airtight(out):
        raise ValueError("that push/pull does not leave ONE closed solid (pushed right "
                         "through?) -- nothing changed")
    return occt_backed(out, recipe)


def make_edge_srf(curves):
    """EdgeSrf -- the surface whose edges ARE the 2, 3 or 4 `curves` (the command behavior: "The edges of the created surface can precisely match the input
    curves"). The curves may be picked in any order and direction: they are chained into a
    loop by their end points (within 1e-7). Two curves are closed with the straight
    segments between their ends -- a RULED surface. Exact Coons patch (OCCT
    `GeomFill_BSplineCurves`, see `occt_backend.make_edge_surface`), recipe `edge_surface`.
    """
    occt = _occt()
    if occt is None:
        raise ValueError("EdgeSrf needs the OCCT kernel, which is not installed")
    if len(curves) not in (2, 3, 4):
        raise ValueError("EdgeSrf needs 2, 3 or 4 curves")
    nc = [c.ToNurbsCurve() if not isinstance(c, r3.NurbsCurve) else c.Duplicate()
          for c in curves]
    if any(c.IsClosed for c in nc):
        raise ValueError("EdgeSrf needs OPEN curves (\"Select 2, 3, or 4 open curves\")")
    if len(nc) == 2:
        # a RULED surface: orient b parallel to a, then loop a -> b's end -> back along b
        a, b = nc
        if a.PointAtStart.DistanceTo(b.PointAtEnd) < a.PointAtStart.DistanceTo(b.PointAtStart):
            b.Reverse()
        back = b.Duplicate()
        back.Reverse()
        nc = [a, r3.LineCurve(a.PointAtEnd, b.PointAtEnd).ToNurbsCurve(), back,
              r3.LineCurve(b.PointAtStart, a.PointAtStart).ToNurbsCurve()]
    ordered = [nc[0]]
    rest = nc[1:]
    tol = 1e-7
    while rest:
        end = ordered[-1].PointAtEnd
        hit = None
        for i, c in enumerate(rest):
            if c.PointAtStart.DistanceTo(end) <= tol:
                hit = (i, False)
                break
            if c.PointAtEnd.DistanceTo(end) <= tol:
                hit = (i, True)
                break
        if hit is None:
            raise ValueError("the curves do not meet end to end, so they bound no surface")
        c = rest.pop(hit[0])
        if hit[1]:
            c.Reverse()
        ordered.append(c)
    if ordered[-1].PointAtEnd.DistanceTo(ordered[0].PointAtStart) > tol:
        raise ValueError("the curves do not close into a loop, so they bound no surface")
    dicts = []
    for c in ordered:
        d = curve_recipe(c)
        if d is None:
            raise ValueError("a curve could not be carried to the kernel")
        dicts.append(d)
    recipe = {"kind": "edge_surface", "curves": dicts}
    try:
        shape = occt.from_recipe(recipe)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"EdgeSrf: {e}") from e
    return occt_backed(shape, recipe)


#: a face pick is AMBIGUOUS when the second-nearest face is within this factor of
#: the nearest (as `edgepick.AMBIGUITY_RATIO` for edges) -- refused, never guessed.
FACE_PICK_AMBIGUITY = 1e-6


def faces_at_points(geom, points):
    """the 1-based `unique_faces` indices of `geom`'s exact solid nearest each picked
    point (a pick ON a face). Raises ValueError, by name, for a pick off the solid or one
    equally near two faces (an edge)."""
    occt = _occt()
    shape = _occt_operand(geom) if occt is not None else None
    if shape is None:
        raise ValueError("that object has no exact solid")
    faces = occt.unique_faces(shape)
    out = []
    for p in points:
        d = sorted((occt.distance_to_shape(f, p), i) for i, f in enumerate(faces, start=1))
        if d[0][0] > 1e-6 * max(1.0, max(abs(c) for c in p)):
            raise ValueError(f"the pick {tuple(round(c, 4) for c in p)} is not on the solid")
        if len(d) > 1 and d[1][0] - d[0][0] <= FACE_PICK_AMBIGUITY:
            raise ValueError(f"the pick {tuple(round(c, 4) for c in p)} is on an EDGE -- "
                             f"pick inside a face")
        out.append(d[0][1])
    return sorted(set(out))


def shell_occt(geom, remove_points, thickness):
    """Shell -- `geom`'s exact solid hollowed inward to `thickness`, the faces under
    `remove_points` removed. Exact (OCCT MakeThickSolid), recipe `shell`. Raises ValueError
    by name."""
    occt = _occt()
    if occt is None:
        raise ValueError("Shell needs the OCCT kernel, which is not installed")
    k = _kernel()
    shape = _occt_operand(geom)
    base = k.recipe_of(geom)
    if (shape is None or not base) and isinstance(geom, r3.Brep) and record_exact_recipe(geom):
        shape, base = k.master_of(geom), k.recipe_of(geom)
    if shape is None or not base:
        raise ValueError("that object has no exact solid")
    if occt.solid_count(shape) != 1:
        raise ValueError("Shell works on a closed solid (\"Shell only operates on simple, "
                         "solid, manifold polysurfaces\")")
    remove = faces_at_points(geom, remove_points)
    recipe = {"kind": "shell", "a": base, "remove": remove, "thickness": float(thickness)}
    try:
        out = occt.from_recipe(recipe)
    except Exception as e:  # noqa: BLE001
        raise ValueError(str(e)) from e
    if not occt.is_airtight(out):
        raise ValueError("the shelled result is not a closed solid -- try a thinner wall")
    return occt_backed(out, recipe)


def offset_surface_solid(geom, distance):
    """OffsetSrf Solid=Yes -- the closed solid between a single surface and its
    offset. Exact (OCCT MakeThickSolidBySimple), recipe `thick_surface`."""
    occt = _occt()
    if occt is None:
        raise ValueError("OffsetSrf Solid=Yes needs the OCCT kernel, which is not installed")
    face, recipe = surface_face_shape(geom)
    if face is None:
        raise ValueError(f"that surface cannot be offset: {recipe}")
    rec = {"kind": "thick_surface", "a": recipe, "distance": float(distance)}
    try:
        out = occt.from_recipe(rec)
    except Exception as e:  # noqa: BLE001
        raise ValueError(str(e)) from e
    if not occt.is_airtight(out):
        raise ValueError("the offset solid is not closed")
    return occt_backed(out, rec)


def cap_planar_holes_occt(geom):
    """Close `geom`'s planar ends ANALYTICALLY, or None if it has no master/recipe.

    The mesh version rebuilds the WHOLE brep, so capping a 1-curved-face open cylinder gave
    212 facet-like faces -- it destroyed the curved wall to add two lids. This touches only the
    lids: a pipe caps to `{TORUS: 1, PLANE: 2}`.
    """
    occt = _occt()
    if occt is None:
        return None
    operand = _occt_operand(geom)
    if operand is None:
        _decline("the shape has no registered master and no rebuildable recipe")
        return None
    try:
        shape = occt.cap_planar_holes(operand)
        proxy = _proxy_from_occt(shape)
    except Exception as e:  # noqa: BLE001
        _decline(f"{type(e).__name__}: {e}")
        return None
    _decline(None)
    k = _kernel()
    k.register_master(proxy, shape)
    base = k.recipe_of(geom)
    if base:
        k.record_recipe(proxy, {"kind": "cap", "a": base})
    return proxy


def bevel_edges_occt(geom, size, chamfer=False, edge_points=None):
    """An ANALYTIC edge fillet/chamfer via OpenCASCADE, or None if not possible.

    Returns the display proxy with its analytic master registered, exactly like
    `boolean_occt`. Returns None -- rather than approximating -- when `geom` has no master
    and no rebuildable recipe, so the caller falls back to the mesh bevel AND SAYS SO.

    against the mesh route it replaces, a 4x4x4 box with every edge beveled by 0.5:

        route          faces  surface types
        bmesh.ops.bevel  492  64 planar + 428 warped quads -- a facet soup
        OCCT fillet       26  {PLANE: 6, CYLINDER: 12, SPHERE: 8}, airtight, vol 61.5922
        OCCT chamfer      26  {PLANE: 26}, airtight, vol 58.6667

    A fillet's blend is a real CYLINDER along each edge and a real SPHERE at each corner,
    which is what the reference CAD application produces and what `bmesh.ops.bevel` fundamentally cannot. A chamfer
    is all PLANEs, which is correct rather than a shortcut: a flat cut has no curvature.
    """
    occt = _occt()
    if occt is None:
        return None
    if size <= 0:
        return None
    operand = _occt_operand(geom)
    if operand is None:
        _decline("the solid has no registered master and no rebuildable recipe")
        return None

    # resolve world points to EDGE INDICES with our own picker, not a kernel one.
    # The edges are read off `operand` (not `geom`'s master) so the indices refer to the very
    # shape being beveled -- reading them from a different shape would be a silent off-by-one
    # waiting to happen.
    indices = None
    if edge_points:
        try:
            curves = occt.edge_curves(operand)
            indices = _edgepick().pick_all(curves, edge_points)
        except Exception as e:  # noqa: BLE001
            # An AMBIGUOUS pick is a real answer the user needs to see, so it propagates as a
            # ValueError rather than silently falling back to beveling every edge.
            raise ValueError(str(e)) from e

    try:
        result = occt.bevel_edges(operand, size, indices, chamfer=chamfer)
        if not occt.is_airtight(result):
            _decline("the beveled result was not airtight")
            return None
        proxy = _proxy_from_occt(result)
    except Exception as e:  # noqa: BLE001
        _decline(f"{type(e).__name__}: {e}")
        return None
    _decline(None)
    k = _kernel()
    k.register_master(proxy, result)
    base = k.recipe_of(geom)
    if base:
        recipe = {"kind": "chamfer" if chamfer else "fillet",
                  "size": float(size), "a": base}
        if indices is not None:
            # Record WHICH edges, so a selectively beveled solid rebuilds identically after a
            # reload. This relies on the operand rebuilding to the same edge ordering, which
            # it does because it is rebuilt from the same recipe by the same kernel.
            recipe["edges"] = list(indices)
        k.record_recipe(proxy, recipe)
    return proxy


def boolean_occt(geom_a, geom_b, operation):
    """An ANALYTIC, AIRTIGHT boolean via OpenCASCADE, or None if not possible.

    Returns the display proxy with its analytic master registered, so the document draws a
    mesh and MEASURES, BOOLEANS and EXPORTS the real surfaces. Returns None -- rather than
    approximating -- when either operand has no master and no rebuildable recipe, which is
    how the caller knows to fall back and SAY it fell back.

    against the mesh route it replaces (box 4x4x4, cylinder r=1.5 through it):

        operation      mesh route                    OCCT
        UNION          514 faces, IsSolid=False      9 faces {PLANE:8, CYLINDER:1}, airtight
        DIFFERENCE     304 faces, IsSolid=False      7 faces {PLANE:6, CYLINDER:1}, airtight
        INTERSECT      354 faces                     3 faces {PLANE:2, CYLINDER:1}, airtight

    and the INTERSECT volume is 28.2743 = pi * 1.5^2 * 4 exactly.
    """
    occt = _occt()
    if occt is None:
        return None
    op = _OCCT_OPS.get(operation)
    if op is None:
        return None
    a = _occt_operand(geom_a)
    b = _occt_operand(geom_b)
    if a is None:
        _decline("operand A has no registered master and no rebuildable recipe")
        return None
    if b is None:
        _decline("operand B has no registered master and no rebuildable recipe")
        return None
    try:
        result = occt.boolean(a, b, op)
        # A boolean that SPLITS its operand into several pieces is a legitimate the reference CAD application
        # outcome (BooleanDifference can cut one object into two) that this document model
        # cannot hold yet. subtracting a sphere of r=2 from a cylinder of r=1.5
        # severs it, giving 2 solids, 14 faces {PLANE:11, SPHERE:2, CYLINDER:1}.
        #
        # Falling back to the mesh route here would be the worst of the options: it would
        # hand back a FACETED two-piece blob and call it done. So this is refused BY NAME
        # by name instead of declining into an approximation.
        pieces = occt.solid_count(result)
        if occt.is_valid(result) and pieces > 1:
            raise ValueError(
                f"Boolean{operation.title()}: the result is {pieces} separate solids; "
                f"multiple results are not implemented yet. The analytic result exists "
                f"and is correct -- what is missing is adding several objects to the "
                f"document at once")
        if not occt.is_airtight(result):
            # Refuse a leaky result outright. This is the defect the mesh route shipped
            # silently (UNION and DIFFERENCE both came back IsSolid=False), and an open
            # solid cannot be manufactured.
            _decline("the OCCT result was not airtight")
            return None
        proxy = _proxy_from_occt(result)
    except ValueError:
        # A named refusal must reach the user, not turn into a silent mesh fallback.
        raise
    except Exception as e:  # noqa: BLE001
        # Declining is legitimate (the caller falls back and says so), but declining for a
        # reason nobody can see is how the original defect stayed hidden for months. The
        # Record the reason so callers can explain why the exact route declined.
        _decline(f"{type(e).__name__}: {e}")
        return None
    _decline(None)
    k = _kernel()
    k.register_master(proxy, result)
    # record the CONSTRUCTION TREE as well, so this solid can rebuild itself after a
    # save and reload. Masters are process-local on purpose; the recipe is what persists.
    ra, rb = k.recipe_of(geom_a), k.recipe_of(geom_b)
    if ra and rb:
        k.record_recipe(proxy, {"kind": "boolean", "op": operation, "a": ra, "b": rb})
    return proxy


def boolean_brep(geom_a, geom_b, operation: str, nu: int = BOOLEAN_NU) -> r3.Brep:
    """Boolean of two solids. ANALYTIC via OpenCASCADE when it can be; mesh otherwise.

    ``operation`` is one of ``"UNION" | "DIFFERENCE" | "INTERSECT"`` (Blender's
    modifier enum values). Returns a new Brep in world coordinates. Raises
    RuntimeError if the solver produces no geometry (e.g. disjoint inputs for
    INTERSECT).

    OCCT IS TRIED FIRST. When both operands carry a registered master or a
    rebuildable recipe, the boolean happens in OpenCASCADE and the result keeps analytic
    surfaces and is verified airtight -- a box unioned with a cylinder is 9 faces
    {PLANE: 8, CYLINDER: 1} instead of 514 faces of which 512 are planar, and it is
    `IsSolid` where the mesh route was NOT.

    The returned object is the display PROXY with its analytic master registered
    (`kernel.master_of`), so the viewport, osnaps and hit-tests keep working on a mesh
    while measurement, further booleans and STEP export use the real surfaces. That split
    is what the reference CAD application does too -- it draws a render mesh over analytic geometry.

    THE FALLBACK IS AUDIBLE, not silent: if OCCT is unavailable or either operand has no
    recipe (a loft, a sweep, an imported mesh), this drops to the Blender mesh solver and
    the result is tagged MESH_DERIVED, so `kernel.describe` reports "faceted" and STEP
    export refuses it. A quiet fallback is how the original defect stayed hidden.
    """
    valid = ("UNION", "DIFFERENCE", "INTERSECT")
    if operation not in valid:
        raise ValueError(
            f"boolean operation {operation!r} unknown; use one of {valid} "
            f"(Blender's modifier enum values -- note 'INTERSECT', not 'INTERSECTION')")

    analytic = boolean_occt(geom_a, geom_b, operation)
    if analytic is not None:
        return analytic

    import bpy
    import bmesh

    objs: list = []
    try:
        # 1. tessellate + weld both inputs, link as scene objects
        for i, geom in enumerate((geom_a, geom_b)):
            bm = _welded_brep_mesh(geom, nu)
            me = bpy.data.meshes.new(f"narwhal_bool_{i}")
            bm.to_mesh(me)
            bm.free()
            ob = bpy.data.objects.new(f"narwhal_bool_{i}", me)
            bpy.context.scene.collection.objects.link(ob)
            objs.append(ob)

        # 2. run the boolean on object A against B, evaluate out of the depsgraph
        mod = objs[0].modifiers.new("NarwhalBool", "BOOLEAN")
        mod.operation = operation
        mod.object = objs[1]
        mod.solver = "EXACT"
        depsgraph = bpy.context.evaluated_depsgraph_get()
        out_me = objs[0].evaluated_get(depsgraph).to_mesh()

        # 3. weld the output, fan-triangulate into a rhino3dm Mesh
        bm2 = bmesh.new()
        bm2.from_mesh(out_me)
        _weld(bm2)
        mesh = r3.Mesh()
        for v in bm2.verts:
            mesh.Vertices.Add(v.co.x, v.co.y, v.co.z)
        for f in bm2.faces:
            idx = [v.index for v in f.verts]
            if len(idx) == 3:
                mesh.Faces.AddFace(*idx)
            else:  # fan-triangulate any n-gon the solver left behind
                for k in range(1, len(idx) - 1):
                    mesh.Faces.AddFace(idx[0], idx[k], idx[k + 1])
        bm2.free()

        if len(mesh.Vertices) == 0 or len(mesh.Faces) == 0:
            raise RuntimeError("boolean produced no geometry")

        # arg False = exact bounds (True inflates the bbox to the NURBS hull).
        # Note: mesh-breps report IsValid=False even when usable
        # (correct bbox + IsSolid=True), so we only reject a truly empty result.
        brep = r3.Brep.CreateFromMesh(mesh, False)
        if brep is None or len(brep.Faces) == 0:
            raise RuntimeError("CreateFromMesh returned an empty brep")
        # This result is mesh-derived and must identify itself accordingly. A mesh boolean
        # subdivides curved walls into planar facets; the tag exposes that approximation to
        # `kernel.honesty_problem` and `kernel.require_analytic`, allowing manufacturing
        # exports to reject it rather than silently shipping faceted geometry.
        try:
            from .kernel import tag as _tag, MESH_DERIVED as _MD

            _tag(brep, _MD)
        except Exception:  # noqa: BLE001
            pass            # never let provenance bookkeeping break a boolean
        return brep
    finally:
        for ob in objs:
            me = ob.data
            bpy.context.scene.collection.objects.unlink(ob)
            bpy.data.objects.remove(ob, do_unlink=True)
            if me is not None and me.users == 0:
                bpy.data.meshes.remove(me)


# ---------------------------------------------------------------------------
# Cap -- close PLANAR open boundary loops to make a closed solid.
#
# the walls: Extrusion already caps; the pure-brep route is
# impossible (no CapPlanarHoles/JoinBreps/Trims/Loops/Valence); r3.Mesh has no
# weld. So, like booleans, Cap goes through bmesh: weld, read the NAKED boundary
# loops bmesh hands us (edge.is_boundary -- the step rhino3dm cannot do),
# triangulate each PLANAR loop with the pure tessellate.ear_clip, and CreateFromMesh
# -> a closed solid. A non-planar loop is left open and reported (the explicit-refusal policy).
# ---------------------------------------------------------------------------

class CapResult:
    """The outcome of:func:`cap_planar_holes`.

    * ``brep``    -- the (possibly) capped Brep.
    * ``capped``  -- how many planar holes were closed.
    * ``warning`` -- non-empty when something was left open or the input was
      already solid, saying why (the explicit-refusal policy: a refusal/limitation must be audible).
    """

    __slots__ = ("brep", "capped", "warning")

    def __init__(self, brep, capped=0, warning=""):
        self.brep = brep
        self.capped = capped
        self.warning = warning

    def __repr__(self):  # pragma: no cover - debugging aid
        w = f" WARNING: {self.warning}" if self.warning else ""
        return f"CapResult(capped={self.capped}){w}"


def cap_planar_holes(geom, tolerance: float = 1e-6):
    """Close the PLANAR open boundary loops of a brep, returning a:class:`CapResult`.

    ANALYTIC FIRST. The mesh path below rebuilds the ENTIRE brep through a mesh, so
    capping a 1-curved-face open cylinder returned 212 facet-like faces -- it destroyed the
    curved wall in order to add two lids. OCCT caps only the lids: a pipe becomes
    `{TORUS: 1, PLANE: 2}`. The mesh path remains as an AUDIBLE fallback for a brep with no
    master and no recipe, where there is simply nothing for the kernel to work from.

    An already-closed solid is returned unchanged (with a note); a brep with no
    open boundary is refused (ValueError). Each open boundary loop whose vertices
    are coplanar to `tolerance` is triangulated and closed; a NON-planar loop is
    left open and reported in `.warning` rather than approximated with a flat patch.
    """
    import bmesh

    if bool(getattr(geom, "IsSolid", False)):
        return CapResult(geom, capped=0, warning="already a closed solid; unchanged")
    analytic = cap_planar_holes_occt(geom)
    if analytic is not None:
        return CapResult(analytic, capped=1, warning="")
    try:
        from . import tessellate
    except ImportError:  # support direct-file loading outside the package
        import tessellate

    bm = _welded_brep_mesh(geom)
    try:
        loops = _boundary_loops(bm)
        if not loops:
            raise ValueError("cap: the brep has no open boundary loop to close")
        capped = 0
        left_open = 0
        for loop in loops:
            pts = [(v.co.x, v.co.y, v.co.z) for v in loop]
            plane = _loop_plane(pts, tessellate)
            if plane is None or not _loop_coplanar(pts, plane, tolerance):
                left_open += 1
                continue
            origin, uax, vax, _n = plane
            poly2d = tessellate.project_to_plane(pts, origin, uax, vax)
            tris = tessellate.ear_clip(poly2d)
            if len(tris) != len(pts) - 2:        # triangulation incomplete -> honest
                left_open += 1
                continue
            for (i, j, k) in tris:
                try:
                    bm.faces.new([loop[i], loop[j], loop[k]])
                except ValueError:
                    pass                         # face already exists (shared edge)
            capped += 1
        if capped == 0:
            raise ValueError(
                "cap: no PLANAR hole to close (every open loop is non-planar)")
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        mesh = _bmesh_to_narwhal_mesh(bm)
    finally:
        bm.free()
    brep = r3.Brep.CreateFromMesh(mesh, False)
    if brep is None or len(brep.Faces) == 0:
        raise ValueError("cap: CreateFromMesh returned an empty brep")
    warning = "" if left_open == 0 else \
        f"{left_open} non-planar boundary loop(s) left open"
    return CapResult(brep, capped=capped, warning=warning)


def _boundary_loops(bm):
    """Ordered vertex loops of the mesh's NAKED (boundary) edges -- the open holes.

    bmesh gives naked edges directly via `edge.is_boundary`, the step rhino3dm's
    brep API cannot do (no Valence/AdjacentFaces). Each boundary vertex joins
    exactly two boundary edges on a manifold border, so the loops are walked by
    following unvisited boundary neighbours.
    """
    adj = {}
    for e in bm.edges:
        if not e.is_boundary:
            continue
        a, b = e.verts
        adj.setdefault(a.index, []).append(b)
        adj.setdefault(b.index, []).append(a)
    visited = set()
    loops = []
    for start_idx in list(adj):
        if start_idx in visited:
            continue
        loop = []
        cur = None
        for e in bm.edges:
            if e.is_boundary and (e.verts[0].index == start_idx
                                  or e.verts[1].index == start_idx):
                cur = e.verts[0] if e.verts[0].index == start_idx else e.verts[1]
                break
        prev_idx = None
        while cur is not None and cur.index not in visited:
            visited.add(cur.index)
            loop.append(cur)
            nxt = None
            for w in adj.get(cur.index, ()):
                if w.index != prev_idx and w.index not in visited:
                    nxt = w
                    break
            prev_idx = cur.index
            cur = nxt
        if len(loop) >= 3:
            loops.append(loop)
    return loops


def _loop_plane(pts, tessellate):
    """(origin, u_axis, v_axis, normal) best-fit plane of a loop via Newell's
    method (exact normal for a planar loop), or None if degenerate."""
    nx = ny = nz = 0.0
    cx = cy = cz = 0.0
    m = len(pts)
    for i in range(m):
        x0, y0, z0 = pts[i]
        x1, y1, z1 = pts[(i + 1) % m]
        nx += (y0 - y1) * (z0 + z1)
        ny += (z0 - z1) * (x0 + x1)
        nz += (x0 - x1) * (y0 + y1)
        cx += x0
        cy += y0
        cz += z0
    ln = math.sqrt(nx * nx + ny * ny + nz * nz)
    if ln < 1e-12:
        return None
    normal = (nx / ln, ny / ln, nz / ln)
    origin = (cx / m, cy / m, cz / m)
    uax, vax = tessellate.plane_basis(normal)
    return origin, uax, vax, normal


def _loop_coplanar(pts, plane, tolerance):
    origin, _u, _v, n = plane
    for p in pts:
        d = (p[0] - origin[0]) * n[0] + (p[1] - origin[1]) * n[1] \
            + (p[2] - origin[2]) * n[2]
        if abs(d) > tolerance:
            return False
    return True


def _bmesh_to_narwhal_mesh(bm):
    """A rhino3dm Mesh from a bmesh, fan-triangulating any n-gon (as booleans do)."""
    mesh = r3.Mesh()
    for v in bm.verts:
        mesh.Vertices.Add(v.co.x, v.co.y, v.co.z)
    for f in bm.faces:
        idx = [v.index for v in f.verts]
        if len(idx) == 3:
            mesh.Faces.AddFace(*idx)
        elif len(idx) == 4:
            mesh.Faces.AddFace(*idx)
        else:
            for k in range(1, len(idx) - 1):
                mesh.Faces.AddFace(idx[0], idx[k], idx[k + 1])
    return mesh


# ---------------------------------------------------------------------------
# Object snaps: real geometry -> the neutral records the snap engine consumes
#
# `osnap.py` is deliberately pure (no rhino3dm, no bpy) so every snap rule
# is unit-testable against hand-built inputs. This is the adapter: it knows the
# rhino3dm API, and produces `osnap.SnapTarget` records.
#
# Every API used here was probed against rhino3dm 8.35.0 rather than assumed:
#   curve.TryGetCircle / TryGetArc / TryGetEllipse  -> object or None
#   curve.IsPolyline + TryGetPolyline                 -> a Polyline of Point3d
#   curve.PointAtStart / PointAtEnd / IsClosed
#   brep.Edges  -> BrepEdgeList of BrepEdge, each of which IS a curve
#   brep.Vertices -> the corner points
#   r3.Point.Location
# ---------------------------------------------------------------------------

#: How densely a curve is sampled for snapping. `Near` and `Quad` read these
#: samples, so too coarse a sample visibly mis-places a snap on a tight arc.
SNAP_SAMPLES = 128


def _xyz(p):
    """A rhino3dm Point3d -> a plain tuple (the snap engine takes only tuples)."""
    return (p.X, p.Y, p.Z)


def _curve_center(curve, corners, closed):
    """The `Cen` snap point for a curve, or None.

    The documented cases (the command behavior, "Center (Cen)"):
      * "The center point of an exact or approximate circle, ellipse, or arc."
      * "The average coordinates of the corners of a closed planar polyline."
    Anything else has no centre, and None says so rather than inventing one.
    """
    for getter in ("TryGetCircle", "TryGetArc", "TryGetEllipse"):
        fn = getattr(curve, getter, None)
        if fn is None:
            continue
        try:
            shape = fn()
        except Exception:  # noqa: BLE001 - rhino3dm raises on some degenerate curves
            continue
        if shape is None:
            continue
        # Circle/Ellipse expose Center; Arc exposes it via its Plane origin.
        center = getattr(shape, "Center", None)
        if center is not None:
            return _xyz(center)
        plane = getattr(shape, "Plane", None)
        if plane is not None and getattr(plane, "Origin", None) is not None:
            return _xyz(plane.Origin)

    if closed and len(corners) >= 3:
        # a closed polyline repeats its first point last; averaging with the
        # duplicate would bias the centre toward that corner
        pts = list(corners)
        if len(pts) > 1 and math.dist(pts[0], pts[-1]) < 1e-9:
            pts = pts[:-1]
        if not pts:
            return None
        n = float(len(pts))
        return (sum(p[0] for p in pts) / n,
                sum(p[1] for p in pts) / n,
                sum(p[2] for p in pts) / n)
    return None


def curve_snap_target(curve, name="", samples=SNAP_SAMPLES):
    """One curve -> an `osnap.SnapTarget`, or None if it cannot be read.

    `vertices` is what the `End` snap uses, and the help is specific about what
    that includes: "the end of a curve,... interior vertices of polylines and
    joined curves, the seam point on closed curves". So a polyline contributes
    every vertex, and a smooth curve just its two ends.
    """
    from . import osnap

    try:
        # Inline `_xyz` to avoid one Python call per sample during interactive moves.
        d = curve.Domain
        t0, span = d.T0, d.T1 - d.T0
        at = curve.PointAt
        pts = [(p.X, p.Y, p.Z) for p in (at(t0 + span * i / samples)
                                         for i in range(samples + 1))]
    except Exception:  # noqa: BLE001
        return None
    if not pts:
        return None

    closed = bool(getattr(curve, "IsClosed", False))

    corners = []
    try:
        if curve.IsPolyline():
            poly = curve.TryGetPolyline()
            if poly is not None:
                corners = [_xyz(p) for p in poly]
    except Exception:  # noqa: BLE001
        corners = []
    if not corners:
        try:
            corners = [_xyz(curve.PointAtStart), _xyz(curve.PointAtEnd)]
        except Exception:  # noqa: BLE001
            corners = [pts[0], pts[-1]]

    # A closed curve's start and end coincide -- that single location is the seam,
    # and offering it twice would make it win ties against real vertices.
    if len(corners) > 1 and math.dist(corners[0], corners[-1]) < 1e-9:
        corners = corners[:-1]

    # every coordinate above is already a Python float (rhino3dm's X/Y/Z, or sums of
    # them), so the engine's per-coordinate normalisation is skipped
    return osnap.SnapTarget.from_floats(pts, corners,
                                        center=_curve_center(curve, corners, closed),
                                        closed=closed, name=name, curve=curve)


def geometry_snap_targets(geom, name="", samples=SNAP_SAMPLES):
    """Every `osnap.SnapTarget` one piece of geometry offers.

    A Brep contributes one target per EDGE, so `End` reaches a box's corners and
    `Mid`/`Near` reach its edges -- which is what makes solids snappable at all.
    Geometry we cannot snap to yet (meshes, SubD) returns an empty list rather
    than a wrong one.
    """
    from . import osnap

    if geom is None:
        return []

    # a point object -- the `Point` snap's target
    location = getattr(geom, "Location", None)
    if location is not None and hasattr(location, "X"):
        return [osnap.SnapTarget.point(_xyz(location), name=name)]

    # a Brep: snap to its edges (and therefore its corners)
    edges = getattr(geom, "Edges", None)
    if edges is not None:
        out = []
        try:
            count = len(edges)
        except Exception:  # noqa: BLE001
            count = 0
        for i in range(count):
            try:
                target = curve_snap_target(edges[i], name=name, samples=samples)
            except Exception:  # noqa: BLE001
                target = None
            if target is not None:
                out.append(target)
        return out

    # a curve
    if hasattr(geom, "Domain") and hasattr(geom, "PointAt"):
        target = curve_snap_target(geom, name=name, samples=samples)
        return [target] if target is not None else []

    return []


# ---------------------------------------------------------------------------
# Gumball transforms
#
# The Gumball's drag math is decided in the pure `gumball` module; these turn its
# answers into the rhino3dm Transforms `_apply_transform_to_selection` bakes into
# the document. Kept here because this is the only module that may touch r3.
#
# All three constructors were VERIFIED present in rhino3dm 8.35.0 (probed, not
# recalled):
#   Transform.Translation(Vector3d)
#   Transform.Rotation(angleRadians, Vector3d axis, Point3d center)
#   Transform.Scale(Plane, x, y, z)   <- the plane overload, which is what makes
#                                        a ONE-dimensional scale possible at all
# ---------------------------------------------------------------------------

def translation_xform(delta):
    """Translate by the world vector `delta`."""
    return r3.Transform.Translation(
        r3.Vector3d(float(delta[0]), float(delta[1]), float(delta[2])))


def rotation_xform(angle_radians, axis, center):
    """Rotate `angle_radians` about `axis`, through `center`.

    Right-handed about `axis`, matching `gumball.rotate_angle`'s sign convention,
    so dragging an arc the way it is drawn turns the object the same way.
    """
    return r3.Transform.Rotation(
        float(angle_radians),
        r3.Vector3d(float(axis[0]), float(axis[1]), float(axis[2])),
        r3.Point3d(float(center[0]), float(center[1]), float(center[2])))


def _perpendicular(axis):
    """Any unit vector perpendicular to `axis`.

    Cross with whichever world axis `axis` is *least* aligned to, so the result is
    never near-degenerate (crossing with a fixed axis fails when `axis` happens to
    be that axis -- the classic trap here).
    """
    ax, ay, az = (abs(axis[0]), abs(axis[1]), abs(axis[2]))
    if ax <= ay and ax <= az:
        other = (1.0, 0.0, 0.0)
    elif ay <= az:
        other = (0.0, 1.0, 0.0)
    else:
        other = (0.0, 0.0, 1.0)
    x = axis[1] * other[2] - axis[2] * other[1]
    y = axis[2] * other[0] - axis[0] * other[2]
    z = axis[0] * other[1] - axis[1] * other[0]
    n = (x * x + y * y + z * z) ** 0.5
    if n < 1e-12:
        return (0.0, 1.0, 0.0)
    return (x / n, y / n, z / n)


def scale_1d_xform(factor, axis, center):
    """Scale by `factor` along `axis` ONLY, anchored at `center`.

    the reference CAD application's Gumball Scale1D: "Drag a scale handle to scale in one dimension along
    the arrow. The Gumball origin is the base point of scaling."

    rhino3dm has no one-dimensional scale entry point, but `Transform.Scale` has a
    **plane** overload, so a plane whose x axis IS the scaling axis with factors
    ``(factor, 1, 1)`` is exactly a 1-D scale along that axis -- and it works for
    an arbitrary axis, not just the world ones.
    """
    u = _perpendicular(axis)
    plane = r3.Plane(
        r3.Point3d(float(center[0]), float(center[1]), float(center[2])),
        r3.Vector3d(float(axis[0]), float(axis[1]), float(axis[2])),
        r3.Vector3d(u[0], u[1], u[2]))
    return r3.Transform.Scale(plane, float(factor), 1.0, 1.0)


def scale_2d_xform(factor, axis_a, axis_b, center):
    """Scale by `factor` in the PLANE spanned by `axis_a`/`axis_b`, anchored at `center`.

    the reference CAD application's Gumball Scale2D, reached by Shift-dragging a Move-2D handle:
    "Drag with Shift to re-size the object in two axes with the same scale. The
    Gumball origin is the base point of scaling" (the command behavior).

    Built the same way as:func:`scale_1d_xform`: `Transform.Scale`'s **plane**
    overload scales along the plane's own x/y/z, so a plane whose x and y are the
    two chosen axes with factors ``(factor, factor, 1)`` scales in that plane and
    leaves the normal direction alone. That is what "two axes with the same scale"
    means, and it works for an arbitrary pair of axes, not just world ones.
    """
    plane = r3.Plane(
        r3.Point3d(float(center[0]), float(center[1]), float(center[2])),
        r3.Vector3d(float(axis_a[0]), float(axis_a[1]), float(axis_a[2])),
        r3.Vector3d(float(axis_b[0]), float(axis_b[1]), float(axis_b[2])))
    return r3.Transform.Scale(plane, float(factor), float(factor), 1.0)


def scale_3d_xform(factor, center):
    """Uniform scale by `factor` about `center`.

    the reference CAD application's Gumball Scale3D, reached by Shift-dragging a scale handle: "Drag with
    Shift to scale the object in three dimensions" (the command behavior). The
    Gumball origin is the base point, as it is for Scale1D.

    `Transform.Scale(Point3d, double)` is the uniform overload, so no plane is
    needed here -- a uniform scale has no preferred direction.
    """
    return r3.Transform.Scale(
        r3.Point3d(float(center[0]), float(center[1]), float(center[2])),
        float(factor))


# ---------------------------------------------------------------------------
# Control points: two different shapes in rhino3dm
#
# `NurbsCurve` exposes `.Points[i]` as a **Point4d** whose X/Y/Z are WEIGHTED, and
# is written by item assignment.
# `PolylineCurve` -- which is what `make_polyline(degree=1)` returns, so every
# Polyline AND every Rectangle -- has no `.Points` at all: it uses `PointCount`,
# `Point(i)` and `SetPoint(i, Point3d)`, with no weights.
#
# Reading only the NurbsCurve representation omits polyline and rectangle control
# points. Both curve shapes are readable and writable through the shared accessors below.
# ---------------------------------------------------------------------------

def _is_polyline_shape(geom):
    """True for the `PointCount`/`Point(i)` flavour (PolylineCurve)."""
    return (getattr(geom, "Points", None) is None
            and hasattr(geom, "PointCount") and hasattr(geom, "Point"))


def _periodic_wrap(geom):
    """How many trailing control points of a PERIODIC curve are copies of its first.

    A periodic degree-d NURBS curve stores its first d control points again at the end;
    this wrap makes the seam smooth. The copies are not separate grips. Showing and moving
    them independently would tear the seam. Returns 0 for non-periodic geometry.
    """
    try:
        if not bool(getattr(geom, "IsPeriodic", False)):
            return 0
        return int(geom.Degree)
    except Exception:  # noqa: BLE001
        return 0


# ---------------------------------------------------------------------------
# Control points for joined curves
#
# A joined non-linear curve is an ``r3.PolyCurve`` without a direct ``Points`` collection.
# The accessors below expose each editable segment's control points.
#
# (rhino3dm 8.35): `PolyCurve.SegmentCurve(i)` returns a LIVE handle -- an edit
# through it persists in the polycurve, through a NESTED polycurve too, and in a document
# object (`fobj.Geometry`). The leaves this app makes are NurbsCurve (`Points[j]`),
# PolylineCurve (`SetPoint`) and LineCurve (`SetStartPoint`/`SetEndPoint`, which return
# True). An `ArcCurve` cannot even be constructed in rhino3dm, and its control points are
# not stored, so a polycurve holding any other leaf type reports NO grips rather than
# offering points it cannot move.
# ---------------------------------------------------------------------------

#: Two segment ends closer than this are ONE joint (one grip). `PolyCurve.Append`
#: snaps a joined end exactly (a 0.004 gap within Join's tolerance came back
#: 0), so anything joined is coincident to rounding.
POLYCURVE_JOINT_TOL = 1e-9


def _polycurve_leaves(geom):
    """The polycurve's single segments in order, flattening nested polycurves (live
    handles), or None if any leaf is a type whose points cannot be edited in place."""
    out = []
    try:
        n = int(geom.SegmentCount)
    except Exception:  # noqa: BLE001
        return None
    for i in range(n):
        seg = geom.SegmentCurve(i)
        if isinstance(seg, r3.PolyCurve):
            sub = _polycurve_leaves(seg)
            if sub is None:
                return None
            out.extend(sub)
        elif isinstance(seg, (r3.NurbsCurve, r3.PolylineCurve, r3.LineCurve)):
            out.append(seg)
        else:
            return None
    return out


def _leaf_points(leaf):
    if isinstance(leaf, r3.LineCurve):
        a, b = leaf.PointAtStart, leaf.PointAtEnd
        return [(a.X, a.Y, a.Z), (b.X, b.Y, b.Z)]
    if isinstance(leaf, r3.PolylineCurve):
        out = []
        for j in range(int(leaf.PointCount)):
            p = leaf.Point(j)               # ONE wrapper per point (it was built 3 times)
            out.append((p.X, p.Y, p.Z))
        return out
    return _nurbs_points(leaf.Points)


def _nurbs_points(pts, count=None):
    """Every location of a NurbsCurve point list, weights divided out -- the same values
    as `control_point_location(pts, j)` for each j, read in one pass. The per-index route
    would re-fetch `leaf.Points` for every point, allocating a new wrapper each time."""
    n = _control_point_count(pts) if count is None else count
    out = []
    for j in range(n):
        cp = pts[j]
        w = getattr(cp, "W", 1.0) or 1.0
        out.append((cp.X / w, cp.Y / w, cp.Z / w))
    return out


def _polycurve_grips(geom):
    """``(leaves, grips)`` -- each grip a list of ``(leaf index, point index)`` that are ONE
    point: a joint between consecutive segments is shared, and so is a closed curve's seam.
    None when the polycurve holds a leaf that cannot be edited."""
    got = _polycurve_table(geom)
    return None if got is None else got[:2]


def _polycurve_table(geom):
    """`_polycurve_grips` plus each leaf's points: ``(leaves, grips, leaf_points)``, every
    leaf read exactly ONCE -- `control_points` needs the points the grip table was built
    from, and re-reading them doubled the cost of selecting a big file's curves."""
    leaves = _polycurve_leaves(geom)
    if not leaves:
        return None
    grips = []
    # each leaf's points read ONCE (they were re-read for every joint test)
    leaf_pts = [_leaf_points(leaf) for leaf in leaves]
    for k, pts in enumerate(leaf_pts):
        for j, p in enumerate(pts):
            if j == 0 and grips:
                last_k, last_j = grips[-1][-1]
                prev = leaf_pts[last_k][last_j]
                if math.dist(prev, p) <= POLYCURVE_JOINT_TOL:
                    grips[-1].append((k, 0))
                    continue
            grips.append([(k, j)])
    if len(grips) > 1:
        first = leaf_pts[grips[0][0][0]][grips[0][0][1]]
        lk, lj = grips[-1][0]
        if math.dist(first, leaf_pts[lk][lj]) <= POLYCURVE_JOINT_TOL:
            grips[0].extend(grips.pop())        # closed: the seam is ONE grip
    return leaves, grips, leaf_pts


def _set_leaf_point(leaf, j, x, y, z):
    if isinstance(leaf, r3.LineCurve):
        p = r3.Point3d(x, y, z)
        return bool(leaf.SetStartPoint(p) if j == 0 else leaf.SetEndPoint(p))
    if isinstance(leaf, r3.PolylineCurve):
        leaf.SetPoint(j, r3.Point3d(x, y, z))     # returns None even on success
        q = leaf.Point(j)
        return math.dist((q.X, q.Y, q.Z), (x, y, z)) < 1e-9
    w = getattr(leaf.Points[j], "W", 1.0) or 1.0
    leaf.Points[j] = r3.Point4d(x * w, y * w, z * w, w)
    return True


def control_point_count(geom):
    """How many control points `geom` has (0 when it has none we can read).

    For a PERIODIC curve, the DISTINCT ones (the wrapped copies are not counted --
    see `_periodic_wrap`). For a POLYCURVE, its segments' points numbered in
    order with each joint counted ONCE: two cubic segments of 4 points give 7."""
    if isinstance(geom, r3.PolyCurve):
        got = _polycurve_grips(geom)
        return len(got[1]) if got is not None else 0
    if isinstance(geom, r3.LineCurve):
        # a LineCurve has neither `Points` nor `PointCount`, so it read as 0
        # grips. `make_polyline` of 2 points returns a LineCurve (that is
        # what `CreateControlPointCurve` gives at degree 1). Its grips are its 2 ends.
        return 2
    pts = getattr(geom, "Points", None)
    if pts is not None:
        return _control_point_count(pts) - _periodic_wrap(geom)
    if _is_polyline_shape(geom):
        try:
            return int(geom.PointCount)
        except Exception:  # noqa: BLE001
            return 0
    return 0


def control_points(geom):
    """World control points of `geom`, or [] when it has none we can read.

    the reference CAD application's `PointsOn` "displays control points or grips of objects... used as
    'grips' on objects such as curves, surfaces, lights, hatch boundaries and
    dimensions" (the command behavior).

    **Weights matter** on the NurbsCurve shape. A control point there is a
    `Point4d`, and on a RATIONAL curve its `X`/`Y`/`Z` are the *weighted*
    coordinates -- the actual location is ``X/W``. on a circle, which
    rhino3dm builds rational: control point 1 reads ``(3.536, 3.536)`` with
    ``W = 0.7071``, and its true location is ``(5, 5)``.

    Surfaces are still NOT read: their control points form a 2-D grid with a
    different traversal. Nor are lights, hatch boundaries or dimensions, which
    the reference CAD application also gives grips to and which we do not build. `PointsOn` REPORTS those
    rather than showing an empty set as though the object had no points.
    """
    if isinstance(geom, r3.PolyCurve):
        # Read every leaf once. Asking `control_point_at` for each index rebuilds the
        # whole grip table per point, making the operation quadratic.
        got = _polycurve_table(geom)
        if got is None:
            return []
        _leaves, grips, pts = got
        return [pts[k][j] for (k, j), *_rest in grips]
    if isinstance(geom, r3.NurbsCurve):
        # one pass over ONE point list (see `_nurbs_points`), minus a periodic curve's
        # wrapped copies exactly as `control_point_count` counts them
        pts = geom.Points
        return _nurbs_points(pts, max(0, _control_point_count(pts) - _periodic_wrap(geom)))
    if isinstance(geom, r3.PolylineCurve):
        return _leaf_points(geom)
    out = []
    for i in range(control_point_count(geom)):
        loc = control_point_at(geom, i)
        if loc is not None:
            out.append(loc)
    return out


def _control_point_count(pts):
    """How many control points a NurbsCurve point list holds."""
    try:
        return len(pts)
    except TypeError:
        return int(getattr(pts, "Count", 0) or 0)


def control_point_at(geom, index):
    """The world location of `geom`'s control point `index`, or None."""
    if isinstance(geom, r3.PolyCurve):
        got = _polycurve_grips(geom)
        if got is None or not 0 <= int(index) < len(got[1]):
            return None
        k, j = got[1][int(index)][0]
        return _leaf_points(got[0][k])[j]
    if isinstance(geom, r3.LineCurve):
        return _leaf_points(geom)[int(index)] if 0 <= int(index) < 2 else None
    pts = getattr(geom, "Points", None)
    if pts is not None:
        return control_point_location(pts, index)
    if _is_polyline_shape(geom):
        # `PolylineCurve.Point(i)` does NOT raise out of range (it returns a
        # point for -1 and for PointCount), so the range is checked here.
        if not 0 <= int(index) < control_point_count(geom):
            return None
        try:
            p = geom.Point(int(index))
            return (p.X, p.Y, p.Z)
        except Exception:  # noqa: BLE001
            return None
    return None


def control_point_location(pts, index):
    """The world location of NurbsCurve control point `index`, or None.

    Divides out the weight -- see:func:`control_points` for why that is not
    optional.
    """
    try:
        cp = pts[index]
    except Exception:  # noqa: BLE001
        return None
    # A Point4d carries the weight; be tolerant of a plain Point3d too.
    w = getattr(cp, "W", 1.0) or 1.0
    try:
        return (cp.X / w, cp.Y / w, cp.Z / w)
    except AttributeError:
        return None


def set_control_point(geom, index, location):
    """Move control point `index` of `geom` to world `location`. True on success.

    Two shapes, both * **NurbsCurve** -- rhino3dm 8.35.0 has no `NurbsCurvePointList.SetPoint`; item
      assignment is the only route (``curve.Points[i] = Point4d(...)``). Rebuilding
      the curve is NOT an alternative: `CreateControlPointCurve` discards weights,
      which would silently turn an exact circle into an approximation. The weight is
      read back and preserved, and coordinates are stored *weighted* (``x*w``) --
      writing unweighted ones into a rational curve is a silent distortion, as moving a circle's control point from ``(5, 5)`` to ``(7.07, 7.07)``.
    * **PolylineCurve** -- has `SetPoint(i, Point3d)` and no weights, so the
      location is stored as given.

    CLOSED CURVES: a clamped circle, ellipse, or closed polyline closes by making
    ``cv[0]`` and ``cv[last]`` coincident. Moving only one opens the seam, so the matching
    seam control point is moved too by ``_closed_seam_partners``. An open curve's
    ends are never joined, and periodic curves are detected and left alone.

    Surface control points are NOT editable through this function
    (`control_point_count` is 0 for a surface -- its points form a 2-D grid we do
    not traverse here), so a closed surface's coincident seam COLUMN cannot be
    dragged through it; that is a separate, un-built capability, reported not
    guessed.
    """
    index = int(index)
    if not 0 <= index < control_point_count(geom):
        # Belt-and-braces, and honestly so: rhino3dm raises IndexError for a
        # OR out-of-range index (- it does NOT wrap the way a
        # Python list would), so the try below would refuse either way. The check
        # is kept because relying on an exception for an ordinary range test reads
        # as an accident.
        return False
    x, y, z = (float(c) for c in location)
    if isinstance(geom, r3.PolyCurve):
        # EVERY segment end at this grip moves, so a joint stays joined and a
        # closed curve's seam stays closed. Only the segments that own the grip change.
        leaves, grips = _polycurve_grips(geom)
        try:
            return all(_set_leaf_point(leaves[k], j, x, y, z) for k, j in grips[index])
        except Exception:  # noqa: BLE001
            return False
    if isinstance(geom, r3.LineCurve):
        # one end moves, the other stays. Verified by reading back rather
        # than trusting the setter's return value.
        try:
            _set_leaf_point(geom, index, x, y, z)
        except Exception:  # noqa: BLE001
            return False
        return math.dist(_leaf_points(geom)[index], (x, y, z)) < 1e-9
    targets = [index] + _closed_seam_partners(geom, index)
    pts = getattr(geom, "Points", None)
    if pts is not None:
        try:
            for i in targets:
                w = getattr(pts[i], "W", 1.0) or 1.0
                pts[i] = r3.Point4d(x * w, y * w, z * w, w)
        except Exception:  # noqa: BLE001
            return False
        return True
    if _is_polyline_shape(geom):
        try:
            for i in targets:
                geom.SetPoint(i, r3.Point3d(x, y, z))
        except Exception:  # noqa: BLE001
            return False
        # `PolylineCurve.SetPoint` returns None even when it succeeds,
        # so its return value cannot be trusted -- reporting it directly claimed
        # failure on every successful move. Verify by reading the point back, which
        # is the only honest confirmation available.
        now = control_point_at(geom, index)
        return now is not None and all(abs(a - b) < 1e-9
                                       for a, b in zip(now, (x, y, z)))
    return False


def offset_control_points(geom, indices, delta):
    """Move control points `indices` of `geom` by world `delta`. Returns how many moved.

    Exactly `control_point_at` + `set_control_point` per index, with every target taken
    from the ORIGINAL positions before any is written (a closed curve's seam pair
    must not be moved twice). For a PolyCurve the grip table is built once rather than
    separately for counting, reading, and writing each index."""
    dx, dy, dz = (float(c) for c in delta)
    return map_control_points(geom, indices, lambda p: (p[0] + dx, p[1] + dy, p[2] + dz))


def map_control_points(geom, indices, fn):
    """Move each control point `indices` of `geom` to ``fn(its location)`` (a world
    point, or None to leave it). Returns how many moved. `offset_control_points` is
    the translation case; a Gumball rotate/scale of grips passes its transform."""
    if isinstance(geom, r3.PolyCurve):
        got = _polycurve_table(geom)
        if got is None:
            return 0
        leaves, grips, leaf_pts = got
        plan = []
        for index in indices:
            index = int(index)
            if not 0 <= index < len(grips):
                continue
            k, j = grips[index][0]
            target = fn(leaf_pts[k][j])
            if target is not None:
                plan.append((index, tuple(float(c) for c in target)))
        moved = 0
        for index, (x, y, z) in plan:
            try:
                if all(_set_leaf_point(leaves[k], j, x, y, z) for k, j in grips[index]):
                    moved += 1
            except Exception:  # noqa: BLE001
                pass
        return moved
    plan = []
    for index in indices:
        loc = control_point_at(geom, index)
        if loc is not None:
            target = fn(loc)
            if target is not None:
                plan.append((index, target))
    return sum(1 for index, target in plan if set_control_point(geom, index, target))


def _closed_seam_partners(geom, index):
    """Indices that must move WITH `index` to keep a CLOSED curve closed.

    Empty for open curves and for interior points. A clamped closed curve
    (circle/ellipse/closed polyline) closes by making cv[0] and cv[last]
    COINCIDENT (a radius-5 circle has cv[0] == cv[8] to 1e-12); moving
    one without the other opens a seam gap and flips `IsClosed` to False. So
    moving either seam point returns the other as its partner.

    It only fires when the two are CURRENTLY coincident, so:
    * an open curve's unrelated ends are never joined (moving a line's cv[0]
      leaves cv[last] alone), and
    * a closed curve whose seam was already pulled apart is left as-is -- there is
      nothing coincident to keep together.

    PERIODIC curves wrap over the first/last `Degree` control points, not a single
    pair. That is DETECTED and left alone (only the requested point moves) rather
    than applying the clamped rule and hoping -- we build no periodic curves, so
    this is a documented gap, not silently-wrong closure logic.
    """
    try:
        if not bool(geom.IsClosed):
            return []
    except Exception:  # noqa: BLE001
        return []
    wrap = _periodic_wrap(geom)
    if wrap:
        # a periodic curve's first `wrap` points are stored again at the end; the copy
        # must move with its original or the seam tears
        n = control_point_count(geom)
        return [index + n] if index < wrap else []
    count = control_point_count(geom)
    if count < 2 or index not in (0, count - 1):
        return []
    other = count - 1 if index == 0 else 0
    a = control_point_at(geom, index)
    b = control_point_at(geom, other)
    if a is None or b is None or math.dist(a, b) >= 1e-9:
        return []
    return [other]


# ---------------------------------------------------------------------------
#  REMOVE selected control points, exactly
# ---------------------------------------------------------------------------

class _Refuse(ValueError):
    """A removal that is not done, with the reason to show the user."""


def _straight_leaf(leaf):
    """A LineCurve, a PolylineCurve, or a degree-1 NurbsCurve: its points ARE its vertices."""
    return isinstance(leaf, (r3.LineCurve, r3.PolylineCurve)) or (
        isinstance(leaf, r3.NurbsCurve) and int(leaf.Degree) == 1)


def _knots(nc):
    return [float(nc.Knots[i]) for i in range(len(nc.Knots))]


def _build_nurbs(cvs4, degree, knots):
    """A NurbsCurve from WEIGHTED control points (`Point4d`s, as `Points[i]` holds them),
    a degree and rhino3dm's stored knot list (n + d - 1 values)."""
    rational = any(abs(float(p.W) - 1.0) > 1e-15 for p in cvs4)
    nc = r3.NurbsCurve(3, rational, int(degree) + 1, len(cvs4))
    for i, p in enumerate(cvs4):
        nc.Points[i] = r3.Point4d(p.X, p.Y, p.Z, p.W)
    for i, k in enumerate(knots):
        nc.Knots[i] = float(k)
    if not nc.IsValid:
        raise _Refuse("removing that point leaves an invalid curve")
    return nc


def _clamped(nc):
    k, d = _knots(nc), int(nc.Degree)
    return (len(k) >= 2 * d and all(abs(x - k[0]) < 1e-12 for x in k[:d])
            and all(abs(x - k[-1]) < 1e-12 for x in k[-d:]))


def _nurbs_drop_cv(nc, idx):
    """`nc` (clamped) without control point `idx`. Exact: the remaining CVs and weights are
    the old ones, untouched.

    (B-spline locality): to keep the degree, ONE interior knot goes with the CV --
    the one nearest the removed CV's Greville abscissa, the parameter that CV "owns". So
    only the curve near the removed point changes; the rest of a circle stays exactly the
    old arcs. When the count no longer supports the degree (n - 1 < d + 1), the degree
    drops to n - 2 with a clamped Bezier knot vector over the same domain.
    """
    n, d = len(nc.Points), int(nc.Degree)
    m = n - 1
    if m < 2:
        raise _Refuse("a curve needs at least 2 control points")
    cvs = [nc.Points[i] for i in range(n) if i != idx]
    k = _knots(nc)
    if m >= d + 1:
        g = sum(k[idx:idx + d]) / d                  # Greville abscissa of CV idx
        interior = range(d, n - 1)                   # stored knots between the clamps
        drop = min(interior, key=lambda i: (abs(k[i] - g), i))
        knots = k[:drop] + k[drop + 1:]
        return _build_nurbs(cvs, d, knots)
    nd = m - 1
    return _build_nurbs(cvs, nd, [k[0]] * nd + [k[-1]] * nd)


def _remove_from_nurbs(nc, indices):
    if bool(getattr(nc, "IsPeriodic", False)):
        if nc.IsRational:
            raise _Refuse("removing a point from a RATIONAL periodic curve is not "
                          "implemented yet")
        distinct = control_point_count(nc)
        keep = [control_point_at(nc, i) for i in range(distinct) if i not in indices]
        if len(keep) < 3:
            raise _Refuse("a closed curve needs at least 3 control points")
        return _make_periodic_curve([r3.Point3d(*p) for p in keep], int(nc.Degree))
    if not _clamped(nc):
        raise _Refuse("removing a point from an unclamped, non-periodic curve is not "
                      "implemented yet")
    n = len(nc.Points)
    first, last = control_point_at(nc, 0), control_point_at(nc, n - 1)
    closed = bool(nc.IsClosed) and math.dist(first, last) < 1e-9
    idx = sorted({(0 if (closed and i == n - 1) else i) for i in indices}, reverse=True)
    if closed and n - 1 - len(idx) < 3:
        raise _Refuse("a closed curve needs at least 3 distinct control points")
    out = nc
    for i in idx:
        if closed and i == 0:
            # the SEAM: it moves to the next point, so the curve stays closed
            cnt = len(out.Points)
            k = _knots(out)
            d = int(out.Degree)
            cvs = [out.Points[j] for j in range(1, cnt - 1)] + [out.Points[1]]
            if cnt - 1 >= d + 1:
                knots = k[:d] + k[d + 1:]            # the knot the seam CV owned
                out = _build_nurbs(cvs, d, knots)
            else:
                nd = cnt - 2
                out = _build_nurbs(cvs, nd, [k[0]] * nd + [k[-1]] * nd)
        else:
            out = _nurbs_drop_cv(out, i)
    return out


def _remove_from_polyline(pl, indices):
    verts = [pl.Point(i) for i in range(int(pl.PointCount))]
    closed = len(verts) > 2 and verts[0].DistanceTo(verts[-1]) < 1e-9
    n = len(verts)
    drop = {(0 if (closed and i == n - 1) else i) for i in indices}
    if closed:
        keep = [verts[i] for i in range(n - 1) if i not in drop]
        if len(keep) < 3:
            raise _Refuse("a closed polyline needs at least 3 distinct points")
        return r3.PolylineCurve(keep + [keep[0]])   # a removed seam moves to the next point
    keep = [verts[i] for i in range(n) if i not in drop]
    if len(keep) < 2:
        raise _Refuse("a polyline needs at least 2 points")
    return r3.PolylineCurve(keep)


def _remove_leaf_point(leaf, j):
    """`leaf` without point `j`, or None when the leaf would vanish (a 2-point leaf)."""
    if isinstance(leaf, r3.LineCurve):
        return None
    count = int(leaf.PointCount) if isinstance(leaf, r3.PolylineCurve) else len(leaf.Points)
    if count <= 2:
        return None
    if isinstance(leaf, r3.PolylineCurve):
        return _remove_from_polyline(leaf, [j])
    return _remove_from_nurbs(leaf, [j])


def _leaf_vertices(leaf):
    return [r3.Point3d(*p) for p in _leaf_points(leaf)]


def _without_end(leaf, at_start):
    """A STRAIGHT leaf without its first (or last) vertex, or None if nothing is left."""
    v = _leaf_vertices(leaf)
    v = v[1:] if at_start else v[:-1]
    return r3.PolylineCurve(v) if len(v) >= 2 else None


def _remove_from_polycurve_once(pc, index):
    got = _polycurve_grips(pc)
    if got is None:
        raise _Refuse("this joined curve has a segment whose points cannot be edited")
    leaves, grips = got
    leaves = [lf.Duplicate() for lf in leaves]
    owners = grips[index]
    closed = len(owners) == 2 and owners[0] == (0, 0) and owners[1][0] == len(leaves) - 1 \
        and len(leaves) > 1
    if len(owners) == 1:
        k, j = owners[0]
        new = _remove_leaf_point(leaves[k], j)
        if new is None:
            if bool(pc.IsClosed):
                raise _Refuse("that point is the only interior point of a straight segment "
                              "of a closed curve")
            leaves.pop(k)                           # an open END segment drops off
        else:
            leaves[k] = new
    else:
        (ka, ja), (kb, jb) = (owners[1], owners[0]) if closed else (owners[0], owners[1])
        a, b = leaves[ka], leaves[kb]
        if not (_straight_leaf(a) and _straight_leaf(b)):
            raise _Refuse("that point is the JOINT between a curved segment and its "
                          "neighbour -- removing it would have to reshape both, which is "
                          "not implemented yet (remove a point inside one segment, or "
                          "Explode first)")
        # straight on both sides: the joint vertex goes, a straight bridge spans the gap --
        # exactly what removing a polyline vertex does
        p, q = _leaf_vertices(a)[-2], _leaf_vertices(b)[1]
        a2, b2 = _without_end(a, at_start=False), _without_end(b, at_start=True)
        bridge_seg = r3.LineCurve(p, q)
        if closed:
            leaves = ([b2] if b2 else []) + leaves[1:-1] + ([a2] if a2 else []) + [bridge_seg]
        else:
            leaves = (leaves[:ka] + ([a2] if a2 else []) + [bridge_seg]
                      + ([b2] if b2 else []) + leaves[kb + 1:])
    if not leaves:
        raise _Refuse("nothing would be left of the curve")
    if len(leaves) == 1:
        return leaves[0]
    out = r3.PolyCurve()
    for lf in leaves:
        if not out.Append(lf):
            raise _Refuse("the segments no longer join after that removal")
    return out


def remove_control_points(geom, indices):
    """Return `geom` without the control points `indices`.

    Returns ``(new_curve, message)``. The caller swaps the NEW curve in
    (`model.replace_geometry`); `geom` itself is never changed. Or it returns
    ``(None, reason)``, leaving the curve unchanged and returning the refusal reason.

    `indices` are grip indices exactly as `control_points(geom)` numbers them:
    * a closed clamped curve's seam appears there as index 0 AND n-1; both mean the seam;
    * a periodic curve's wrapped copies are not counted;
    * a PolyCurve's joints are counted once.

    (the command behavior): "removes control points
    from a curve... Removing control points affects the shape of the curve". The success
    message is the command UI's "Deleted 1 control point." / "Deleted %d control points."

    * NurbsCurve: the CVs and their weights go, and every other CV is kept EXACTLY. It is
      a new exact NURBS, never a refit (`_nurbs_drop_cv` documents the knot choice).
      * A closed clamped curve stays closed: removing the seam moves it to the next CV.
      * A periodic curve stays periodic: it is rebuilt from its remaining distinct points
        (uniform knots, ).
      * A degree-1 NURBS (our `make_line`, `make_polyline(degree=1)`) loses exactly its
        vertices.
    * PolylineCurve: the vertices go, and a closed one stays closed.
    * LineCurve or any 2-point curve: refused. there is no evidence that the reference CAD application
      turns a curve into a point, and a line has only its ends.
    * PolyCurve: only the segment that owns the point changes. A 2-point segment at an
      open END drops off. A JOINT between two STRAIGHT segments goes, and a straight
      segment bridges the gap (polyline behaviour). A joint touching a CURVED segment is
      REFUSED by name: removing it would have to reshape both segments.

    Refused, by name: too few points (an open curve needs 2, a closed one 3 distinct), an
    index out of range, no indices, and any non-curve.
    """
    try:
        idx = sorted({int(i) for i in indices})
    except (TypeError, ValueError):
        return None, "control point indices must be whole numbers"
    if not idx:
        return None, "no control points were selected"
    if not isinstance(geom, r3.Curve):
        return None, f"a {type(geom).__name__} has no curve control points to remove"
    count = control_point_count(geom)
    bad = [i for i in idx if i < 0 or i >= count]
    if bad:
        return None, (f"control point {bad[0]} does not exist (this curve has {count}, "
                      f"numbered 0..{count - 1})")
    try:
        if isinstance(geom, r3.LineCurve):
            raise _Refuse("a line has only its two end points -- removing one would leave "
                          "no curve")
        if isinstance(geom, r3.PolylineCurve):
            out = _remove_from_polyline(geom, idx)
        elif isinstance(geom, r3.NurbsCurve):
            out = _remove_from_nurbs(geom.Duplicate(), idx)
        elif isinstance(geom, r3.PolyCurve):
            out = geom.Duplicate()
            for i in reversed(idx):                  # highest first: lower grips keep
                if not isinstance(out, r3.PolyCurve):  # their numbers
                    out, msg = remove_control_points(out, [i])
                    if out is None:
                        raise _Refuse(msg)
                    continue
                out = _remove_from_polycurve_once(out, i)
        else:
            raise _Refuse(f"removing control points from a {type(geom).__name__} is not "
                          f"implemented yet")
    except _Refuse as e:
        return None, str(e)
    n = len(idx)
    return out, ("Deleted 1 control point." if n == 1 else f"Deleted {n} control points.")


# ---------------------------------------------------------------------------
# surface control-point grid, with seam-preserving edits
# ---------------------------------------------------------------------------

def surface_control_points(surface):
    """The 2-D grid of a NurbsSurface's control points, `grid[iu][iv]` in world
    coordinates (weights divided out). Dimensions are `Points.CountU` by
    `Points.CountV`. [] for anything without a surface control-point list.

    handled a closed CURVE's seam; `control_points` reads curves only, and
    `ui._control_point_locations` said a surface's grid "is a different traversal
    and is NOT guessed at here". This is that traversal.
    """
    pts = getattr(surface, "Points", None)
    if pts is None or not hasattr(pts, "CountU"):
        return []
    grid = []
    for i in range(pts.CountU):
        row = []
        for j in range(pts.CountV):
            cp = pts.GetControlPoint(i, j)
            w = getattr(cp, "W", 1.0) or 1.0
            row.append((cp.X / w, cp.Y / w, cp.Z / w))
        grid.append(row)
    return grid


def set_surface_control_point(surface, iu, iv, location):
    """Move surface control point (iu, iv) to world `location`, preserving SEAM
    closure (- the surface half of ). True on success.

    A NurbsSurface closed in U has its first and last control-point COLUMNS
    coincident (the seam); closed in V, its first and last ROWS. Moving a seam
    control point must move its twin(s), or the surface tears open along the
    seam. It fires only when the twin is CURRENTLY coincident (an open surface is
    untouched), and NEVER at a singular POLE (`IsAtSingularity`) -- a pole is a
    whole edge collapsed to a point, not a seam, and pairing only two of its
    coincident points would tear it. A torus (closed in both directions) moves
    the diagonal corner twin as well. Display-layer wiring is handled separately.
    """
    pts = getattr(surface, "Points", None)
    if pts is None or not hasattr(pts, "CountU"):
        return False
    if not (0 <= iu < pts.CountU and 0 <= iv < pts.CountV):
        return False
    x, y, z = (float(c) for c in location)
    try:
        for (i, j) in _surface_seam_twins(surface, iu, iv):
            w = getattr(pts.GetControlPoint(i, j), "W", 1.0) or 1.0
            pts[i, j] = r3.Point4d(x * w, y * w, z * w, w)
    except Exception:  # noqa: BLE001
        return False
    return True


def _surface_seam_twins(surface, iu, iv):
    """The set of (i, j) indices to move together with (iu, iv) to keep a closed
    surface closed. Always includes (iu, iv) itself; empty of twins for an open
    surface, an interior point, or a singular pole."""
    pts = surface.Points
    nu, nv = pts.CountU, pts.CountV
    targets = {(iu, iv)}

    def coincident(a, b):
        ca, cb = pts.GetControlPoint(*a), pts.GetControlPoint(*b)
        wa = getattr(ca, "W", 1.0) or 1.0
        wb = getattr(cb, "W", 1.0) or 1.0
        return math.dist((ca.X / wa, ca.Y / wa, ca.Z / wa),
                         (cb.X / wb, cb.Y / wb, cb.Z / wb)) < 1e-9

    closed_u = _srf_closed(surface, 0)
    closed_v = _srf_closed(surface, 1)
    # A pole ROW (a singular V-end) makes column 0 and last coincide for reasons
    # that are NOT the U-seam; a pole COLUMN likewise for the V-seam. Skip those.
    on_pole_row = iv in (0, nv - 1) and _edge_is_singular(surface, "v", iv)
    on_pole_col = iu in (0, nu - 1) and _edge_is_singular(surface, "u", iu)

    if closed_u and iu in (0, nu - 1) and not on_pole_row:
        other = nu - 1 if iu == 0 else 0
        if coincident((iu, iv), (other, iv)):
            targets.add((other, iv))
    if closed_v and iv in (0, nv - 1) and not on_pole_col:
        other = nv - 1 if iv == 0 else 0
        if coincident((iu, iv), (iu, other)):
            targets.add((iu, other))
    if (closed_u and closed_v and iu in (0, nu - 1) and iv in (0, nv - 1)
            and not on_pole_row and not on_pole_col):
        ou = nu - 1 if iu == 0 else 0
        ov = nv - 1 if iv == 0 else 0
        if coincident((iu, iv), (ou, ov)):
            targets.add((ou, ov))
    return targets


def _srf_closed(surface, direction):
    try:
        return bool(surface.IsClosed(direction))
    except Exception:  # noqa: BLE001
        return False


def _edge_is_singular(surface, which, index):
    """Is the U- or V-domain end at `index` a singular pole (a collapsed edge)?

    `IsAtSingularity(u, v, exact)` is the documented way to tell, so the
    pole is identified from the surface, not from coordinates.
    """
    try:
        du, dv = surface.Domain(0), surface.Domain(1)
        if which == "v":
            v = dv.T0 if index == 0 else dv.T1
            return bool(surface.IsAtSingularity(du.T0, v, True))
        u = du.T0 if index == 0 else du.T1
        return bool(surface.IsAtSingularity(u, dv.T0, True))
    except Exception:  # noqa: BLE001
        return False


def transform_point(xform, point):
    """Apply a rhino3dm `Transform` to a world ``(x, y, z)``. Returns a tuple.

    Needed because the Gumball's transforms are built for whole objects, but when
    control points are selected they have to be applied to each GRIP's location
    instead. Doing it by hand keeps every Gumball mode -- move, rotate, and the
    three scales -- working on control points without a special case per mode.

    `Transform` is row-major with the translation in column 3, the same convention
    `_apply_transform_to_selection` relies on, so the rows multiply through
    directly. The homogeneous w is divided out when it is not 1, which a
    perspective-like transform would produce; the reference CAD application's gumball never makes one, but
    silently ignoring it would be a latent bug.
    """
    try:
        x, y, z = (float(c) for c in point)
        nx = xform.M00 * x + xform.M01 * y + xform.M02 * z + xform.M03
        ny = xform.M10 * x + xform.M11 * y + xform.M12 * z + xform.M13
        nz = xform.M20 * x + xform.M21 * y + xform.M22 * z + xform.M23
        w = xform.M30 * x + xform.M31 * y + xform.M32 * z + xform.M33
    except Exception:  # noqa: BLE001
        return None
    if w and abs(w - 1.0) > 1e-12:
        return (nx / w, ny / w, nz / w)
    return (nx, ny, nz)


# ---------------------------------------------------------------------------
# Join (the command behavior)
#
#     "The Join command connects objects together to form a single object. Join
#     turns lines into polylines, curves into polycurves"... "You can join
#     curves that touch end-to-end."
#
# The ORDERING and REVERSAL decisions are pure arithmetic and live in
# `joinchain.py`, which also records why rhino3dm cannot be trusted to make them
# (`PolyCurve.Append` neither checks the gap nor reverses a backwards
# curve, and reports `HasGap == False` in both cases). This half owns only the
# kernel calls.
# ---------------------------------------------------------------------------

def curve_ends(geom):
    """``(start, end)`` world tuples of a curve, or None when `geom` is not one."""
    if not isinstance(geom, r3.Curve):
        return None
    try:
        return _xyz(geom.PointAtStart), _xyz(geom.PointAtEnd)
    except Exception:  # noqa: BLE001
        return None


def _polyline_vertices(geom):
    """`geom`'s vertices in order when it is made only of straight runs, else None.

    This is what decides polyline-vs-polycurve, which is the reference CAD application's own distinction:
    *"Join turns lines into polylines, curves into polycurves"*.

    The test is `Curve.IsPolyline` rather than the concrete class, because our
    own `make_line` stores a line as a **NurbsCurve**
    (``LineCurve(...).ToNurbsCurve``) -- so an `isinstance` check made joining
    two of our own `Line`s produce a PolyCurve, which the help says it should not.
    a NurbsCurve built from a line reports ``IsLinear == True`` and
    ``IsPolyline == True``; one built from a polyline reports ``IsPolyline ==
    True`` and ``IsLinear == False``; one built from an arc reports **False**
    for both. So `IsPolyline` is exactly the "only straight runs" question,
    whatever the class -- and `IsLinear` would be wrong, since it is False for a
    bent polyline.

    An arc, a free NURBS curve or a mixed PolyCurve therefore returns None and
    goes down the PolyCurve route rather than being flattened into line segments,
    which would silently destroy curvature.
    """
    if isinstance(geom, r3.PolylineCurve):
        try:
            return [_xyz(geom.Point(i)) for i in range(int(geom.PointCount))]
        except Exception:  # noqa: BLE001
            return None
    try:
        if not geom.IsPolyline():
            return None
        pline = geom.TryGetPolyline()
    except Exception:  # noqa: BLE001
        return None
    if pline is None:
        return None
    try:
        return [_xyz(p) for p in pline]
    except Exception:  # noqa: BLE001
        return None


def _build_polyline_chain(members, geoms, tolerance):
    """A PolylineCurve for a chain of straight runs, or None if any isn't straight."""
    verts = []
    for key, reverse in members:
        run = _polyline_vertices(geoms[key])
        if run is None:
            return None
        if reverse:
            run = list(reversed(run))
        if verts and math.dist(verts[-1], run[0]) <= tolerance:
            run = run[1:]          # the shared joint is one vertex, not two
        verts.extend(run)
    if len(verts) < 2:
        return None
    return r3.PolylineCurve([r3.Point3d(*v) for v in verts])


def _build_polycurve_chain(members, geoms):
    """A PolyCurve for a chain, reversing the members that need it.

    Every member is `Duplicate`d first: `Reverse` mutates in place,
    and the originals still belong to the document until the command decides the
    join succeeded.

    `RemoveNesting` is called at the end because appending a PolyCurve into a
    PolyCurve NESTS it -- two curves of 1 and 2 segments append to
    `SegmentCount == 2` with `IsNested == True`, and flatten to 3 afterwards.
    """
    out = r3.PolyCurve()
    for key, reverse in members:
        piece = geoms[key].Duplicate()
        if reverse and not piece.Reverse():
            return None
        if not out.Append(piece):
            return None
    if out.IsNested:
        out.RemoveNesting()
    return out


def join_curves(items, tolerance):
    """Join what can be joined among `items`.

    `items` is ``[(key, curve_geometry)]`` in **selection order** -- the help makes
    that order significant (*"The order the objects are selected determines the
    layer of the new joined object"*), so it is preserved rather than sorted.

    Returns ``[(keys, geometry, closed)]``, one entry per chain of **two or more**
    curves that touch end-to-end within `tolerance`. A curve that joins to nothing
    produces no entry at all, so the caller never deletes and re-adds an object
    for a join that did not happen.
    """
    from . import joinchain

    segs = []
    geoms = {}
    for key, geom in items:
        ends = curve_ends(geom)
        if ends is None:
            continue           # not a curve; surfaces/meshes are a separate job
        segs.append((key, ends[0], ends[1]))
        geoms[key] = geom

    out = []
    for chain in joinchain.joinable(segs, tolerance):
        geom = _build_polyline_chain(chain.members, geoms, tolerance)
        if geom is None:
            geom = _build_polycurve_chain(chain.members, geoms)
        if geom is not None:
            out.append((chain.keys, geom, chain.closed))
    return out


def explode_curve(geom):
    """`Explode`'s pieces for a curve, or None when it has nothing to explode.

    (the command behavior) -- Explode's own results table:

        Polycurve -> "Single segment curves."
        Polyline  -> "Single segment lines/curves."

    Returns a list of NEW curves; the input is never mutated. None (not an empty
    list) means "this curve is already a single segment", which is a different
    answer from "it exploded into nothing" and lets the caller say so honestly.

    Two shapes, both * `PolyCurve.Explode` exists and returns a tuple of its segment curves.
    * `PolylineCurve` has **no** `Explode` (AttributeError), and neither does
      `NurbsCurve`. A polyline of any flavour is therefore taken apart through
      `_polyline_vertices`, which is the same `IsPolyline`/`TryGetPolyline`
      route `Join` uses -- so `Explode` undoes exactly what `Join` produced.
    """
    if isinstance(geom, r3.PolyCurve):
        try:
            parts = list(geom.Explode() or ())
        except Exception:  # noqa: BLE001
            return None
        return parts or None
    verts = _polyline_vertices(geom)
    if verts is None or len(verts) < 3:
        return None          # a single line/arc/circle is already one segment
    return [r3.LineCurve(r3.Point3d(*a), r3.Point3d(*b))
            for a, b in zip(verts, verts[1:])
            if math.dist(a, b) > 0.0]


def curve_segments(geom, samples=16):
    """One entry per SUB-OBJECT segment of `geom`, for sub-object selection.

    Returns ``[(index, points, grips)]``:

    * `index` is the segment's position along the curve, which is what the user
      sees and what `Explode` would produce in the same order.
    * `points` is a world-space sample of just that segment, dense enough to
      hit-test against the pixels it draws.
    * `grips` is the tuple of CONTROL-POINT indices that segment is made of, or
      ```` when they cannot be worked out. A Gumball can only move a segment by
      moving its grips, so an empty tuple is the honest way to say "selectable,
      but not movable" rather than moving the wrong thing.

    Returns ``[]`` for a curve with nothing to sub-select -- a single line, arc or
    circle IS one segment, and offering to sub-select the whole of an object is
    not sub-object selection.

    Grips are known only for the polyline shapes, where vertex *i* of the curve is
    control point *i* and segment *i* spans ``(i, i+1)`` -- which is exactly the
    representation `_polyline_vertices` returns. A `PolyCurve` has no `.Points` at
    all (neither `Points` nor `PointCount`), so its segments come back
    with no grips.
    """
    verts = _polyline_vertices(geom)
    if verts is not None:
        if len(verts) < 3:
            return []
        # The grip mapping holds only when there really is one control point per
        # vertex. That is true of a PolylineCurve and of a degree-1 NurbsCurve, but
        # `_polyline_vertices` also answers for curves reached through
        # `TryGetPolyline`, so it is CHECKED rather than assumed -- a wrong grip
        # index would move a different part of the curve.
        aligned = control_point_count(geom) == len(verts)
        return [(i, [a, b], (i, i + 1) if aligned else ())
                for i, (a, b) in enumerate(zip(verts, verts[1:]))]
    if isinstance(geom, r3.PolyCurve):
        out = []
        try:
            count = int(geom.SegmentCount)
        except Exception:  # noqa: BLE001
            return []
        if count < 2:
            return []
        for i in range(count):
            try:
                seg = geom.SegmentCurve(i)
            except Exception:  # noqa: BLE001
                continue
            out.append((i, sample_curve(seg, samples), ()))
        return out
    return []


def snap_targets(model, samples=SNAP_SAMPLES):
    """Every snappable target in a `NarwhalModel`.

    Walks the File3dm object table, so it sees exactly the geometry the document
    holds -- not the Blender meshes, which are only a tessellation of it.
    """
    out = []
    file3dm = getattr(model, "file", None)
    if file3dm is None:
        return out
    for fobj in file3dm.Objects:
        name = ""
        try:
            name = fobj.Attributes.Name or ""
        except Exception:  # noqa: BLE001
            pass
        out.extend(geometry_snap_targets(fobj.Geometry, name=name,
                                        samples=samples))
    return out


def _bbox_corners(geom):
    """``(lo, hi)`` of rhino3dm's `GetBoundingBox`, or None.

    For a curve that box contains the whole curve (a NURBS curve lies inside the hull
    of its control points, and openNURBS boxes at least that hull), so it contains
    every sample, vertex and exact `Near` point taken from it.
    """
    bb = geom.GetBoundingBox()
    if bb is None or not bb.IsValid:
        return None
    lo, hi = bb.Min, bb.Max
    return (lo.X, lo.Y, lo.Z), (hi.X, hi.Y, hi.Z)


def _curve_extent(curve):
    """The box holding every snap `curve_snap_target(curve)` can offer, or None.

    The curve's own box, PLUS its analytic centre: an arc's (or elliptical arc's)
    centre lies OUTSIDE the arc, and `Cen` snaps to it. `_curve_center` is called
    with no corners, so it answers exactly the analytic half of what the target gets
    (a closed polyline's corner average lies inside the box anyway).
    """
    box = _bbox_corners(curve)
    if box is None:
        return None
    lo, hi = list(box[0]), list(box[1])
    c = _curve_center(curve, (), False)
    if c is not None:
        for k in range(3):
            lo[k] = min(lo[k], c[k])
            hi[k] = max(hi[k], c[k])
    return tuple(lo), tuple(hi)


def _safe_extent(fn, geom):
    """`fn(geom)`, or None (= "never cull") if it raises."""
    try:
        return fn(geom)
    except Exception:  # noqa: BLE001
        return None


def _point_extent(geom):
    p = _xyz(geom.Location)
    return p, p


def object_snap_sources(geom):
    """`geometry_snap_targets(geom)` split into its independent SOURCES, cheaply:
    ``[(source, is_edge, extent)]``.

    `snap_source_targets(source, is_edge, name)` turns one source into its targets, and
    concatenating that over the list, in order, gives exactly `geometry_snap_targets`
    -- the same dispatch (point, then Brep edges one by one, then curve). A Brep is one
    source per edge because one solid can have thousands of edges, and eagerly sampling
    all of them is expensive.

    `extent` is a world box ``(lo, hi)`` holding EVERY snap point that source can
    produce, or None when no box can be stated -- callers must read None as "never
    cull this". Anything that raises gives None rather than a box that might be too
    small: an over-kept source costs time, an under-kept one silently loses a snap.
    """
    if geom is None:
        return []
    location = getattr(geom, "Location", None)
    if location is not None and hasattr(location, "X"):
        return [(geom, False, _safe_extent(_point_extent, geom))]
    edges = getattr(geom, "Edges", None)
    if edges is not None:
        try:
            count = len(edges)
        except Exception:  # noqa: BLE001
            count = 0
        out = []
        for i in range(count):
            try:
                edge = edges[i]
            except Exception:  # noqa: BLE001
                continue       # `geometry_snap_targets` skips it too
            out.append((edge, True, _safe_extent(_curve_extent, edge)))
        return out
    if hasattr(geom, "Domain") and hasattr(geom, "PointAt"):
        return [(geom, False, _safe_extent(_curve_extent, geom))]
    return []


def snap_source_targets(source, is_edge, name="", samples=SNAP_SAMPLES):
    """The `osnap.SnapTarget`s of one `object_snap_sources` entry."""
    if not is_edge:
        return geometry_snap_targets(source, name=name, samples=samples)
    try:
        target = curve_snap_target(source, name=name, samples=samples)
    except Exception:  # noqa: BLE001
        target = None
    return [target] if target is not None else []


def snap_index(model):
    """``[(name, source, is_edge, extent)]`` for every object, in table order -- the
    CHEAP half of `snap_targets`.

    Eager `snap_targets` work is dominated by sampling every curve, although only sources
    whose extent projects near the cursor can snap. The UI therefore keeps this index and
    calls `snap_source_targets` lazily, for those alone.
    """
    out = []
    file3dm = getattr(model, "file", None)
    if file3dm is None:
        return out
    for fobj in file3dm.Objects:
        name = ""
        try:
            name = fobj.Attributes.Name or ""
        except Exception:  # noqa: BLE001
            pass
        for source, is_edge, extent in object_snap_sources(fobj.Geometry):
            out.append((name, source, is_edge, extent))
    return out
