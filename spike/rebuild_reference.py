"""Independent, test-only pre-retirement Python fit; never imported by production."""

def _basis(full, p, t, ncp):
    """All `ncp` B-spline basis values at `t` (Cox-de Boor over the FULL knot vector)."""
    lo, hi = full[p], full[ncp]
    t = min(max(t, lo), hi)
    span = p
    for k in range(p, ncp):
        if full[k] <= t < full[k + 1]:
            span = k
            break
    else:
        span = ncp - 1
    left, right = [0.0] * (p + 1), [0.0] * (p + 1)
    nb = [1.0] + [0.0] * p
    for j in range(1, p + 1):
        left[j] = t - full[span + 1 - j]
        right[j] = full[span + j] - t
        saved = 0.0
        for r in range(j):
            denom = right[r + 1] + left[j - r]
            tmp = nb[r] / denom if denom else 0.0
            nb[r] = saved + right[r + 1] * tmp
            saved = left[j - r] * tmp
        nb[j] = saved
    out = [0.0] * ncp
    for r in range(p + 1):
        out[span - p + r] = nb[r]
    return out


_SINGULAR = "the rebuild fit is singular -- use fewer points"


def _solve(a, b):
    """Solve the square system a x = b (Gaussian elimination, partial pivoting)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[piv][c]) < 1e-300:
            raise ValueError(_SINGULAR)
        m[c], m[piv] = m[piv], m[c]
        for r in range(c + 1, n):
            f = m[r][c] / m[c][c]
            if f:
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][k] * x[k] for k in range(r + 1, n))) / m[r][r]
    return x


def _least_squares(rows, rhs):
    """Minimise |A x - b| via the normal equations."""
    k = len(rows[0])
    ata = [[0.0] * k for _ in range(k)]
    atb = [0.0] * k
    for row, y in zip(rows, rhs):
        nz = [(i, v) for i, v in enumerate(row) if v]
        for i, vi in nz:
            atb[i] += vi * y
            for j, vj in nz:
                ata[i][j] += vi * vj
    return _solve(ata, atb)


def _fit_closed(pts, params, n, p):
    """The Python closed fit: ``(control points, rhino3dm knots)``, `p` points wrapped."""
    ncp = n + p                                   # wrapped control points
    full = [float(i) for i in range(-1, ncp + p)]  # uniform, periodic
    lo, hi = full[p], full[ncp]
    rows, rhs = [], []
    for (t, q), un in zip(pts, params):
        u = lo + (hi - lo) * (un % 1.0)
        b = _basis(full, p, u, ncp)
        folded = [0.0] * n
        for j, v in enumerate(b):
            folded[j % n] += v
        for axis in range(3):
            row = [0.0] * (3 * n)
            for j in range(n):
                row[axis * n + j] = folded[j]
            rows.append(row)
            rhs.append(q[axis])
    x = _least_squares(rows, rhs)
    ctrl = [(x[j], x[n + j], x[2 * n + j]) for j in range(n)]
    return ctrl + ctrl[:p], full[1:-1]


def _fit_open(pts, params, n, p, ends):
    """The Python open fit: ``(control points, rhino3dm knots)``. `ends` is ``(start,
    end)``, or ``(start, end, t0, t1)`` when the end tangents are preserved."""
    ncp = n
    inner = n - p - 1
    full = [0.0] * (p + 1) + [(i + 1) / (inner + 1) for i in range(inner)] + [1.0] * (p + 1)
    start, end = ends[0], ends[1]
    tangent = len(ends) == 4
    t0, t1 = (ends[2], ends[3]) if tangent else (None, None)
    free = list(range(2, n - 2)) if tangent else list(range(1, n - 1))
    # unknowns: [a, b] (tangent lengths, if constrained) + 3 coords of each free point
    nk = (2 if tangent else 0) + 3 * len(free)
    rows, rhs = [], []
    for (t, q), u in zip(pts, params):
        b = _basis(full, p, u, ncp)
        for axis in range(3):
            row = [0.0] * nk
            known = b[0] * start[axis] + b[n - 1] * end[axis]
            if tangent:
                # P1 = start + a t0,  P(n-2) = end - b t1
                known += b[1] * start[axis] + b[n - 2] * end[axis]
                row[0] = b[1] * t0[axis]
                row[1] = -b[n - 2] * t1[axis]
            off = 2 if tangent else 0
            for fi, j in enumerate(free):
                row[off + 3 * fi + axis] = b[j]
            rows.append(row)
            rhs.append(q[axis] - known)
    x = _least_squares(rows, rhs)
    ctrl = [None] * n
    ctrl[0], ctrl[n - 1] = start, end
    off = 2 if tangent else 0
    if tangent:
        ctrl[1] = tuple(start[i] + x[0] * t0[i] for i in range(3))
        ctrl[n - 2] = tuple(end[i] - x[1] * t1[i] for i in range(3))
    for fi, j in enumerate(free):
        ctrl[j] = tuple(x[off + 3 * fi + i] for i in range(3))
    return ctrl, full[1:-1]


