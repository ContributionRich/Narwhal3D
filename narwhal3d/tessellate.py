"""Pure tessellation helpers for the display bridge.

The functions operate on coordinate tuples without bpy or rhino3dm. ``bridge.py`` handles
rhino3dm-specific trim loops and edge sampling, then calls this module to project planar
boundaries and triangulate them.

A full-domain grid is incorrect for a planar face trimmed to a circle or polygon because it
would draw the face's rectangular parameter domain. Planar trimmed faces are therefore
triangulated inside their actual boundary.

``ear_clip`` supports one simple polygon without holes. Planar faces with inner loops fall
back to the bridge's grid route. Trimmed non-planar faces also use that fallback because they
have no single planar projection. Primitive non-planar faces are untrimmed, so their display
grids cover their exact domains.
"""
import math


def signed_area(poly):
    """Twice-halved signed area of a 2-D polygon; positive when CCW."""
    s = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return 0.5 * s


def _point_in_triangle(p, a, b, c):
    """True if `p` lies inside or on the boundary of triangle a-b-c.

    Ear clipping uses this to reject a candidate ear that would swallow another
    (typically reflex) vertex. The test is INCLUSIVE of the boundary on purpose:
    a reflex vertex lying exactly on the ear's diagonal must still block it,
    otherwise the clip produces overlapping triangles (a concave polygon then
    tessellates to too much area).
    """
    px, py = p
    ax, ay = a
    bx, by = b
    cx, cy = c
    d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(d) < 1e-14:
        return False
    l1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d
    l2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d
    l3 = 1.0 - l1 - l2
    eps = 1e-9
    return l1 >= -eps and l2 >= -eps and l3 >= -eps


def _convex_fan(poly, idx):
    """Return a linear-time fan for a strictly convex loop, or ``None``.

    This is deliberately a conservative recogniser, not a replacement for ear
    clipping: holes, concavities, repeated points, and non-simple loops must
    retain the old safe path.  ``idx`` is already CCW, so the returned triangles
    have the same winding and original vertex indices as:func:`ear_clip`.
    """
    n = len(idx)
    if n < 3:
        return None
    positive = False
    for i, i1 in enumerate(idx):
        i0, i2 = idx[i - 1], idx[(i + 1) % n]
        a, b, c = poly[i0], poly[i1], poly[i2]
        cross = ((b[0] - a[0]) * (c[1] - a[1])
                 - (b[1] - a[1]) * (c[0] - a[0]))
        # A zero corner is not safely strictly convex: leave it to the
        # established ear clipper, which preserves its existing fallback rules.
        if cross <= 1e-14:
            return None
        positive = True
    if not positive:
        return None
    anchor = idx[0]
    return [(anchor, idx[i], idx[i + 1]) for i in range(1, n - 1)]


def ear_clip(poly):
    """Triangulate a simple 2-D polygon by ear clipping.

    `poly` is a list of (x, y). Returns a list of (i, j, k) index triples into
    `poly`. Either winding is accepted (the polygon is oriented CCW internally).
    Strictly convex loops use a linear-time fan; concave and degenerate loops
    use the conservative ear-clipping path. Holes are not represented by this
    single-loop API and remain a deliberate fallback in the bridge.
    Returns [] for fewer than 3 vertices. If the polygon is non-simple and
    clipping stalls, the triangles found so far are returned rather than looping
    forever -- the caller treats an incomplete result (len != n-2) as a failure
    and falls back to the grid.
    """
    n = len(poly)
    if n < 3:
        return []
    idx = list(range(n))
    if signed_area(poly) < 0:
        idx.reverse()

    fast = _convex_fan(poly, idx)
    if fast is not None:
        return fast

    triangles = []
    guard = 0
    limit = n * n + 1
    while len(idx) > 3 and guard < limit:
        guard += 1
        m = len(idx)
        clipped = False
        # For a CCW simple polygon, only reflex/collinear vertices can lie
        # inside a candidate ear. Avoid testing every convex vertex: this is
        # still conservative for concavity and keeps non-simple loops subject
        # to the same stalled-loop fallback below.
        reflex = set()
        for q in range(m):
            q0, q1, q2 = idx[q - 1], idx[q], idx[(q + 1) % m]
            qa, qb, qc = poly[q0], poly[q1], poly[q2]
            qcross = ((qb[0] - qa[0]) * (qc[1] - qa[1])
                      - (qb[1] - qa[1]) * (qc[0] - qa[0]))
            if qcross <= 0:
                reflex.add(q1)
        for i in range(m):
            i0 = idx[(i - 1) % m]
            i1 = idx[i]
            i2 = idx[(i + 1) % m]
            a, b, c = poly[i0], poly[i1], poly[i2]
            # convex corner? (cross > 0 for CCW winding)
            cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            if cross <= 0:
                continue
            # a real ear holds no other reflex/collinear vertex
            if any(_point_in_triangle(poly[j], a, b, c)
                   for j in reflex if j not in (i0, i1, i2)):
                continue
            triangles.append((i0, i1, i2))
            del idx[i]
            clipped = True
            break
        if not clipped:
            break  # non-simple / stalled -> caller falls back to the grid
    if len(idx) == 3:
        triangles.append((idx[0], idx[1], idx[2]))
    return triangles


def plane_basis(normal):
    """Two orthonormal in-plane axes (u, v) for a plane with the given `normal`.

    `normal` and both returned axes are (x, y, z) tuples. A reference axis that
    is not parallel to the normal is chosen, so the basis never degenerates.
    """
    nx, ny, nz = normal
    ln = math.sqrt(nx * nx + ny * ny + nz * nz)
    if ln < 1e-15:
        return (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)
    nx, ny, nz = nx / ln, ny / ln, nz / ln
    ref = (1.0, 0.0, 0.0) if abs(nx) < 0.9 else (0.0, 1.0, 0.0)
    ux = ref[1] * nz - ref[2] * ny
    uy = ref[2] * nx - ref[0] * nz
    uz = ref[0] * ny - ref[1] * nx
    lu = math.sqrt(ux * ux + uy * uy + uz * uz)
    ux, uy, uz = ux / lu, uy / lu, uz / lu
    vx = ny * uz - nz * uy
    vy = nz * ux - nx * uz
    vz = nx * uy - ny * ux
    return (ux, uy, uz), (vx, vy, vz)


def project_to_plane(points3d, origin, u_axis, v_axis):
    """Project 3-D points to in-plane (u, v) coordinates relative to `origin`."""
    ox, oy, oz = origin
    ux, uy, uz = u_axis
    vx, vy, vz = v_axis
    out = []
    for (x, y, z) in points3d:
        dx, dy, dz = x - ox, y - oy, z - oz
        out.append((dx * ux + dy * uy + dz * uz, dx * vx + dy * vy + dz * vz))
    return out


#: Largest turn allowed between adjacent display chords, in degrees. Five degrees gives
#: 72 segments around a full circle and a scale-independent sagitta of about 0.095% of
#: the radius.
CURVE_MAX_ANGLE_DEG = 5.0


def curve_segments_for(length, tolerance, minimum=16, maximum=1024,
                       max_angle_deg=None):
    """Segment count to display a curve of the given LENGTH, from TWO criteria.

    The display applies two criteria and takes the larger count:

    * CHORD height -- the max distance a straight display segment may fall from the
      true curve (the sagitta), `tolerance`. This is scale-DEPENDENT: a world-space
      0.01 is trivially met by a small circle, so for anything under ~radius 1 the
      ``minimum`` floor was the only thing deciding the count, producing a visible
      16-sided approximation.
    * MAX ANGLE -- the max turn between adjacent chords, `max_angle_deg` (default
      `CURVE_MAX_ANGLE_DEG`). This is scale-INVARIANT: a full circle turns 360
      degrees whatever its radius, so it needs `ceil(360 / angle)` segments at EVERY
      size. This keeps circles smooth even when they are drawn very small.

    The result is `max(chord_count, angle_count)` clamped to [`minimum`, `maximum`]
    -- strictest wins in BOTH directions (a large curve at a tight chord tolerance
    still gets its chord-driven, >angle count).

    MODELS THE CURVE AS A CIRCLE of the same length (radius = length/(2*pi)), so it
    is a pure function of length and CANNOT tell a straight segment from a curved
    one: it OVER-tessellates a line (72 points for a long line). That is harmless
    because the display short-circuits a line/polyline through its exact vertices
    (`bridge._curve_display_points`) and never calls this for one; a caller that
    tessellates by this count directly should guard `Curve.IsLinear` itself.

    The calculation uses length rather than parameter domain because a NURBS domain does
    not describe physical size. The chord sagitta formula is:

        sagitta s = R * (1 - cos(theta / 2))  ->  theta = 2 * acos(1 - s / R)
        chord_count = ceil(2*pi / theta)

    Degenerate input (non-positive length or tolerance) returns `minimum` and the
    angle criterion is NOT applied -- a zero-length curve is not a tiny circle.

    The chord component is an equal-arc estimate that a uniform-parameter display walk can
    overshoot by about 1.2x on a rational quadratic circle or arc. The angle component is
    exact for the circle model.
    """
    if length <= 0.0 or tolerance <= 0.0:
        return minimum
    angle = CURVE_MAX_ANGLE_DEG if max_angle_deg is None else max_angle_deg
    angle_count = math.ceil(360.0 / angle) if angle and angle > 0.0 else minimum
    radius = length / (2.0 * math.pi)
    ratio = 1.0 - tolerance / radius if radius > 0.0 else -2.0
    if ratio <= -1.0:                     # tolerance >= 2R: one chord suffices
        chord_count = minimum
    else:
        theta = 2.0 * math.acos(ratio)
        chord_count = maximum if theta <= 0.0 else math.ceil(2.0 * math.pi / theta)
    n = max(int(chord_count), int(angle_count))
    return max(minimum, min(maximum, n))


#: Screen-space chord tolerance for view-dependent retessellation, in pixels. A sub-pixel
#: tolerance keeps curves visually smooth as the view zooms in.
SCREEN_PIXEL_TOL = 0.5

#: Retessellation hysteresis factor. The display mesh is rebuilt only when the required
#: segment count changes by more than this ratio, avoiding work on minor zoom changes.
RETESSELLATE_FACTOR = 1.5


def screen_segments_for(length, pixels_per_unit, pixel_tol=None, minimum=1,
                        maximum=1024, max_angle_deg=None):
    """Segment count needed to display a curve smoothly at the current zoom.

    A screen chord tolerance of `pixel_tol` pixels is just a world chord tolerance
    of ``pixel_tol / pixels_per_unit`` -- it SHRINKS as you zoom in
    (``pixels_per_unit`` grows), so the count rises as needed. This defers to:func:`curve_segments_for` with that screen-tolerance and the same
    `maximum` clamp (a huge zoom caps there, never a million). `pixel_tol` defaults
    to ``SCREEN_PIXEL_TOL``.

    It is the CHORD count only -- no max-angle floor and a minimum of 1 unless one
    is passed. Its one job is the zoom HYSTERESIS signal (`should_retessellate`
    compares two of these), and the angle criterion is scale-free: it is the same
    at every zoom, so it carries no information about whether the zoom changed.
    Keeping the 72-segment angle floor here would return the same count across several
    zoom levels and prevent retessellation. Pass ``max_angle_deg`` to restore that floor.
    """
    pt = SCREEN_PIXEL_TOL if pixel_tol is None else pixel_tol
    angle = max_angle_deg if max_angle_deg is not None else 360.0
    if pixels_per_unit <= 0.0 or pt <= 0.0:
        return curve_segments_for(length, 0.0, minimum, maximum, angle)
    world_tol = pt / pixels_per_unit
    return curve_segments_for(length, world_tol, minimum, maximum, angle)


#: Relative floor on display chord tolerance as a fraction of the curve's bounding-box
#: diagonal. A curve filling a roughly 2000-pixel viewport is drawn to the configured
#: screen tolerance, while closer zoom levels trigger explicit view-dependent refinement.
CURVE_REL_CHORD = SCREEN_PIXEL_TOL / 2000.0

#: Stop angle-based subdivision once a chord would be shorter than this many chord
#: tolerances. The chord criterion remains uncapped, so visible deviation still stays
#: within tolerance while unnecessary subdivision of tiny curve pieces is avoided.
CURVE_ANGLE_MIN_CHORD = 4.0


def relative_tolerance(size, tolerance):
    """The default display chord tolerance for a curve whose bbox diagonal is `size`:
    ``max(tolerance, size * CURVE_REL_CHORD)``. Never
    finer than `tolerance`, so small curves are unchanged."""
    return max(tolerance, size * CURVE_REL_CHORD) if size > 0.0 else tolerance


def _turn(a, b):
    """Angle in radians between two direction vectors (0 if either is degenerate)."""
    cx = a[1] * b[2] - a[2] * b[1]
    cy = a[2] * b[0] - a[0] * b[2]
    cz = a[0] * b[1] - a[1] * b[0]
    dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
    cr = math.sqrt(cx * cx + cy * cy + cz * cz)
    if cr == 0.0 and dot == 0.0:
        return 0.0
    return math.atan2(cr, dot)


def piece_segments_for(points, tangents, tolerance, max_angle_deg=None,
                       maximum=1024):
    """Segments to draw ONE smooth curve piece, from probe samples of the piece itself.

    `points` / `tangents` are k+1 samples taken UNIFORMLY IN PARAMETER over the piece
    (a piece is smooth: it runs between C0 corners). The display then walks the piece
    uniformly in parameter with the returned count n, so n/k segments fall in each
    probe interval and each interval i must get at least its own need::

        chord   need_i = sqrt(turn_i * len_i / (8 * tol))     sagitta ~ kappa*l^2/8
        angle   need_i = min(turn_i / max_angle, len_i / (CURVE_ANGLE_MIN_CHORD*tol))
        n = ceil(k * max_i max(chord, angle)),  clamped to [1, maximum]

    This replaces `curve_segments_for`'s model of "a full circle of the same length"
    for display curves: that model gave EVERY curved piece the 360-degree angle count
    (72) however little it turned, and could not tell a gentle piece from a tight one.
    Here the count follows the piece's real TURN and CURVATURE: a straight piece gets
    1, a 30-degree arc ~1/12 of the full circle, a full circle exactly what
    `curve_segments_for` gave it (both reduce to the same sagitta formula). Using the
    WORST interval also covers non-uniform parameter speed (a rational circle), which
    is what the old equal-arc estimate overshooting on.

    `kappa*l^2/8` over-estimates the true sagitta `R*(1-cos(l/2R))`, so the chord
    criterion errs fine. A non-positive tolerance disables the chord criterion and the
    angle cap and returns the angle count alone.
    """
    needs = piece_needs(points, tangents, tolerance, max_angle_deg)
    if not needs:
        return 1
    n = math.ceil(len(needs) * max(needs) - 1e-9)
    return max(1, min(int(maximum), n))


def piece_needs(points, tangents, tolerance, max_angle_deg=None,
                angle_min_chord_factor=None):
    """Per probe interval, the segments THAT interval alone needs (a float; the rule is
    in `piece_segments_for`). 0 for an interval that does not turn.

    ``angle_min_chord_factor`` defaults to :data:`CURVE_ANGLE_MIN_CHORD`, preserving
    the short-chord density cap for ordinary one-span pieces. Pass 0 to keep the angle
    criterion scale-invariant when a complex piece can hide a tight bend among many
    spans; the chord criterion is unchanged either way.
    """
    k = len(points) - 1
    if k < 1:
        return []
    max_angle = math.radians(CURVE_MAX_ANGLE_DEG if max_angle_deg is None
                             else max_angle_deg)
    min_chord_factor = (CURVE_ANGLE_MIN_CHORD if angle_min_chord_factor is None
                        else float(angle_min_chord_factor))
    needs = []
    for i in range(k):
        p, q = points[i], points[i + 1]
        seg = math.sqrt((q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2 + (q[2] - p[2]) ** 2)
        turn = _turn(tangents[i], tangents[i + 1])
        if turn <= 0.0:
            needs.append(0.0)
            continue
        angle_need = turn / max_angle if max_angle > 0.0 else 0.0
        if tolerance > 0.0:
            chord_need = math.sqrt(turn * seg / (8.0 * tolerance))
            if min_chord_factor > 0.0:
                angle_need = min(angle_need, seg / (min_chord_factor * tolerance))
        else:
            chord_need = 0.0
        needs.append(max(chord_need, angle_need))
    return needs


def need_positions(needs, maximum=4096):
    """Interior vertex positions for a piece whose k equal probe intervals have the
    given `needs`, as fractional interval coordinates u in (0, k).

    Vertices are spaced EVENLY IN NEED, not in parameter: n = ceil(sum(needs))
    segments, the j-th vertex where the cumulative need reaches j*sum/n. Interval i is
    then cut at most every 1/needs[i] of its width, which is exactly its own need, and a
    straight interval (need 0) receives no vertex. This is for a piece of MANY spans:
    walking it uniformly at its worst interval's density (`piece_segments_for`) is
    `k*max` -- 1037 vertices for a 40-point zigzag that needs ~sum.
    """
    total = sum(needs)
    n = max(1, min(int(maximum), math.ceil(total - 1e-9)))
    if n == 1 or total <= 0.0:
        return []
    out = []
    i, cum = 0, 0.0
    for j in range(1, n):
        target = total * j / n
        while i < len(needs) - 1 and cum + needs[i] < target:
            cum += needs[i]
            i += 1
        frac = (target - cum) / needs[i] if needs[i] > 0.0 else 1.0
        out.append(i + min(1.0, max(0.0, frac)))
    return out


def point_segment_distance(p, a, b):
    """Distance from point `p` to the segment a-b (3-D tuples)."""
    ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    ap = (p[0] - a[0], p[1] - a[1], p[2] - a[2])
    L2 = ab[0] * ab[0] + ab[1] * ab[1] + ab[2] * ab[2]
    u = 0.0 if L2 <= 0.0 else max(0.0, min(1.0, (ap[0] * ab[0] + ap[1] * ab[1]
                                                  + ap[2] * ab[2]) / L2))
    d = (ap[0] - u * ab[0], ap[1] - u * ab[1], ap[2] - u * ab[2])
    return math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2])


def align_count(n, k):
    """The smallest count >= `n` that is a divisor or a multiple of the probe count `k`,
    so every probe sample lands on a display vertex and is reused rather than
    evaluated again (k=4: 3 -> 4, 5 -> 8, 9 -> 12). Only ever ADDS segments."""
    n, k = max(1, int(n)), max(1, int(k))
    if n >= k:
        return -(-n // k) * k
    for d in range(n, k + 1):
        if k % d == 0:
            return d
    return k


def should_retessellate(current_count, new_count, factor=None):
    """Hysteresis for True only when `new_count` differs from `current_count`
    by more than `factor`x (up or down), so a small zoom change is suppressed and a
    real one fires. `factor` defaults to `RETESSELLATE_FACTOR`. A
    non-positive current count always re-tessellates (there is nothing to keep).
    """
    f = RETESSELLATE_FACTOR if factor is None else factor
    if current_count <= 0:
        return True
    ratio = new_count / float(current_count)
    return ratio >= f or ratio <= 1.0 / f


def distribute_params(breakpoints, total, t0, t1):
    """Sorted sample parameters over ``[t0, t1]`` for an adaptive, KNOT-AWARE grid
    line set (the surface analogue of a curve's knot sampling).

    Every interior `breakpoint` (a KNOT value) becomes a sample parameter, so a
    knot is always a grid line and a region of DENSELY packed knots gets densely
    packed samples -- the surface version of the fix, where a plain
    uniform-in-parameter division samples a non-uniform surface wrongly. The spans
    BETWEEN consecutive breakpoints are then subdivided to reach about `total`
    segments overall, each span taking a share proportional to its width and at
    least one segment. The mandatory breakpoints may push the final count a little
    above `total`; that only ever adds detail, never removes it.
    """
    span = t1 - t0
    if span <= 0.0:
        return [t0]
    bps = sorted({t0, t1} | {b for b in breakpoints if t0 < b < t1})
    total = max(int(total), len(bps) - 1)      # >= one segment per mandatory span
    params = set(bps)
    for a, b in zip(bps[:-1], bps[1:]):
        k = max(1, int(round(total * (b - a) / span)))
        for i in range(1, k):
            params.add(a + (b - a) * i / k)
    return sorted(params)
