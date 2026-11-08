"""Exact-to-tolerance arc length and point-at-arc-length (pure).

rhino3dm has NO `Curve.GetLength` and NO `Curve.DivideByCount` (MEASURED), so the
tree estimates length by sampling a chord polyline in two places at two sample
counts that therefore disagree -- `analysis.curve_length(curve, n=1024)` and
`bridge._curve_length_estimate(curve, samples=64)`. A chord always UNDER-estimates
an arc, so both are biased low and the 64-sample one more so.

Arc length is the integral of ``|C'(t)|``. `DerivativeAt` gives ``C'`` exactly, so
this integrates by GAUSS-LEGENDRE quadrature PER KNOT SPAN: the integrand is
smooth within a span and generally not across one, so integrating span by span
(spans taken from the KNOT VECTOR, not `SpanCount`)
converges to machine precision with a handful of nodes, where chord sampling needs
thousands and is still biased. `parameter_at_length` is then Newton on
``length_of(0, t) - s`` whose derivative is ``|C'(t)|`` -- already in hand, so the
Newton step is free and converges quadratically.

Analytic shortcuts are taken for the whole-curve cases -- `IsLinear` -> chord
length, a full arc/circle -> ``radius * angle`` -- both because they are exact and
because they provide an independent exact path.

Tolerance comes from `units.py` (the document's absolute tolerance), never a
literal.
"""
import bisect
import math

import rhino3dm as r3

try:                                  # normal import, inside the addon package
    from . import units
except ImportError:                   # direct file-path loading, without a package
    import units


def default_tolerance():
    """The document's absolute tolerance, in model units (via units.py)."""
    # Use the document tolerance in model units, not an unconditional 0.01 default.
    return units.document_tolerance()


# Gauss-Legendre nodes/weights are cached per order once computed.
_GL_CACHE = {}
_GL_ORDER = 24                        # ample for a rational-quadratic span's |C'|


def length_of(curve, t0=None, t1=None, tolerance=None):
    """Arc length of `curve` between parameters `t0` and `t1` (whole curve if None).

    Exact for a line (chord) and a full arc/circle (radius*angle); otherwise the
    integral of |C'(t)| by Gauss-Legendre per knot span, to ~machine precision.
    """
    d = curve.Domain
    a = d.T0 if t0 is None else float(t0)
    b = d.T1 if t1 is None else float(t1)
    if b < a:
        a, b = b, a
    if b - a <= _PARAM_EPS:
        return 0.0

    whole = (abs(a - d.T0) <= _PARAM_EPS and abs(b - d.T1) <= _PARAM_EPS)
    if whole:
        exact = _analytic_length(curve)
        if exact is not None:
            return exact

    total = 0.0
    for (lo, hi) in _spans(curve, a, b):
        total += _gauss_span(curve, lo, hi)
    return total


def parameter_at_length(curve, s, tolerance=None):
    """The parameter at arc length `s` measured from the curve's start.

    Newton on ``length_of(t0, t) - s`` whose derivative is ``|C'(t)|``. `s` must
    be within ``[0, length_of(curve)]``; a value outside is REFUSED (ValueError),
    not silently clamped. To invert many lengths on one curve, use
    :class:`PreparedLength`, which computes the span integrals once.
    """
    return PreparedLength(curve).parameter_at(s, tolerance)


class PreparedLength:
    """Arc length of one curve, prepared once for many :meth:`parameter_at` queries.

    The span integrals are computed when the object is made. It works on a private copy
    of the curve, so later edits to `curve` cannot change its answers: they equal
    :func:`parameter_at_length` on the curve as it was when prepared. Nothing is cached
    beyond the object, which belongs to one operation.
    """

    __slots__ = ("_curve", "_table", "_packed")

    def __init__(self, curve):
        self._curve = curve.Duplicate()
        self._table = _prepare(self._curve)
        self._packed = _native_pack(self._curve, self._table)

    @property
    def total(self):
        """The arc length of the whole curve, as :func:`length_of` gives it."""
        return self._table.total

    def parameter_at(self, s, tolerance=None):
        """The parameter at arc length `s`; see :func:`parameter_at_length`."""
        return self.parameters_at((s,), tolerance)[0]

    def parameters_at(self, lengths, tolerance=None):
        """:meth:`parameter_at` for each of `lengths`, in one native call when it can.

        With the pure-core library loaded a `NurbsCurve` is inverted in Rust
        (`native_arclength`), which repeats `_invert` bit for bit. MEASURED (i7-9700K,
        a 30-point cubic): 1.15 ms per length in Python, most of it the `DerivativeAt`
        calls of each Newton step's span integral. A length Rust hands back (outside
        the curve, or the table's end branch) is answered by `_invert` here, so its
        result or error is the Python's.
        """
        tol = default_tolerance() if tolerance is None else float(tolerance)
        lengths = list(lengths)
        native = None
        if self._packed is not None:
            try:
                from . import native_arclength
            except ImportError:       # direct file-path loading, without a package
                import native_arclength
            native = native_arclength.parameters_at(self._packed, lengths, tol)
        if native is None:
            return [_invert(self._curve, self._table, s, tol) for s in lengths]
        return [t if done else _invert(self._curve, self._table, s, tol)
                for s, (t, done) in zip(lengths, native)]


def _native_pack(curve, table):
    """The Rust packing of a prepared curve, or None (not a NurbsCurve, no library)."""
    if not isinstance(curve, r3.NurbsCurve):
        return None
    try:
        from . import native_arclength
    except ImportError:               # direct file-path loading, without a package
        import native_arclength
    nodes, weights = _gauss_legendre(_GL_ORDER)
    return native_arclength.pack(curve, table, nodes, weights)


def _invert(curve, table, s, tolerance):
    tol = default_tolerance() if tolerance is None else float(tolerance)
    d = curve.Domain
    total = table.total
    length_to = lambda t: _length_to(curve, table, t)
    if s < -tol or s > total + tol:
        raise ValueError(f"arc length {s} is outside the curve [0, {total}]")
    s = min(max(s, 0.0), total)
    if s <= tol:
        return d.T0
    if s >= total - tol:
        return d.T1

    # seed with the linear-in-t guess, then Newton; bisection-guarded so a near-
    # cusp (tiny |C'|) cannot throw the step out of the domain.
    lo, hi = d.T0, d.T1
    t = d.T0 + (d.T1 - d.T0) * (s / total)
    for _ in range(60):
        f = length_to(t) - s
        if abs(f) <= tol * 1e-3:
            break
        if f > 0:
            hi = t
        else:
            lo = t
        speed = _speed(curve, t)
        if speed > _PARAM_EPS:
            step = t - f / speed
        else:
            step = 0.5 * (lo + hi)
        t = step if lo < step < hi else 0.5 * (lo + hi)
    return t


# ---------------------------------------------------------------------------
# integration
# ---------------------------------------------------------------------------

_PARAM_EPS = 1e-12


def _spans(curve, a, b):
    """(lo, hi) sub-intervals of [a, b] split at every interior knot inside it, so
    each piece is integrated over a region where |C'| is smooth."""
    knots = _interior_knots(curve)
    cuts = [a] + [k for k in knots if a + _PARAM_EPS < k < b - _PARAM_EPS] + [b]
    return list(zip(cuts[:-1], cuts[1:]))


def _interior_knots(curve):
    """The parameters where `curve` may stop being smooth -- its interior knots.

    A `PolyCurve` (what `Join` makes) and a `PolylineCurve` have NO `.Knots`, and this
    used to return [] for them -- so Gauss-Legendre integrated straight ACROSS a joint's
    kink. MEASURED: a fillet joined to its two lines (8 + pi + 8 = 19.14159) measured
    19.68116. Their NURBS form has the same parameterisation (MEASURED: `PointAt` agrees
    at every t) and carries a knot at every segment joint, so its knots are used.
    """
    try:
        vals = list(curve.Knots.ToList())
    except Exception:  # noqa: BLE001
        try:
            vals = list(curve.ToNurbsCurve().Knots.ToList())
        except Exception:  # noqa: BLE001
            return []
    d = curve.Domain
    eps = (d.T1 - d.T0) * 1e-12
    return sorted({v for v in vals if d.T0 + eps < v < d.T1 - eps})


def _gauss_span(curve, lo, hi):
    nodes, weights = _gauss_legendre(_GL_ORDER)
    half = 0.5 * (hi - lo)
    mid = 0.5 * (hi + lo)
    acc = 0.0
    for x, w in zip(nodes, weights):
        acc += w * _speed(curve, mid + half * x)
    return half * acc


def _speed(curve, t):
    """|C'(t)| -- the magnitude of the first derivative."""
    d = curve.DerivativeAt(t, 1)
    v = d[1]
    return math.sqrt(v.X * v.X + v.Y * v.Y + v.Z * v.Z)


# ---------------------------------------------------------------------------
# prepared inversion tables (NURBS curves)
# ---------------------------------------------------------------------------


class _Table:
    """One NURBS curve's span integrals, summed in `length_of`'s order."""

    __slots__ = ("t0", "t1", "knots", "cuts", "cum", "total")

    def __init__(self, t0, t1, knots, cuts, cum, total):
        self.t0, self.t1, self.knots, self.cuts = t0, t1, knots, cuts
        self.cum, self.total = cum, total



def _prepare(curve):
    """The Gauss integral of each knot span, accumulated in the order `_spans` uses.

    ``cum[k]`` is the sum of the first ``k`` spans, so a partial length is one prefix sum
    plus one partial span, with the same floating-point additions as `length_of`.
    """
    d = curve.Domain
    t0, t1 = d.T0, d.T1
    knots = [k for k in _interior_knots(curve) if t0 + _PARAM_EPS < k < t1 - _PARAM_EPS]
    cuts = [t0] + knots + [t1]
    cum = [0.0]
    for lo, hi in zip(cuts[:-1], cuts[1:]):
        cum.append(cum[-1] + _gauss_span(curve, lo, hi))
    if t1 - t0 <= _PARAM_EPS:
        total = length_of(curve)
    else:
        # length_of's whole-curve sum is these same span integrals, in the same order
        exact = _analytic_length(curve)
        total = cum[-1] if exact is None else exact
    return _Table(t0, t1, knots, cuts, cum, total)


def _length_to(curve, table, t):
    """``length_of(curve, table.t0, t)``, from the table where it applies.

    The edge and reversed cases take `length_of` itself, which handles them.
    """
    if not (t - table.t0 > _PARAM_EPS and abs(t - table.t1) > _PARAM_EPS):
        return length_of(curve, table.t0, t)
    k = bisect.bisect_left(table.knots, t - _PARAM_EPS)
    return table.cum[k] + _gauss_span(curve, table.cuts[k], t)


def _gauss_legendre(n):
    """Nodes and weights of the n-point Gauss-Legendre rule on [-1, 1].

    Roots of the Legendre polynomial P_n by Newton (Newton-Cotes would need
    thousands of points for the same accuracy). Cached per order.
    """
    if n in _GL_CACHE:
        return _GL_CACHE[n]
    nodes = [0.0] * n
    weights = [0.0] * n
    for i in range(n):
        # initial guess for the i-th root (Chebyshev-like)
        x = math.cos(math.pi * (i + 0.75) / (n + 0.5))
        for _ in range(100):
            p0, p1 = 1.0, 0.0
            for k in range(1, n + 1):
                p0, p1 = ((2 * k - 1) * x * p0 - (k - 1) * p1) / k, p0
            # p0 = P_n(x), p1 = P_{n-1}(x); derivative:
            dp = n * (x * p0 - p1) / (x * x - 1.0)
            dx = p0 / dp
            x -= dx
            if abs(dx) <= 1e-15:
                break
        nodes[i] = x
        weights[i] = 2.0 / ((1.0 - x * x) * dp * dp)
    _GL_CACHE[n] = (nodes, weights)
    return nodes, weights


# ---------------------------------------------------------------------------
# analytic shortcuts (whole-curve only)
# ---------------------------------------------------------------------------

def _analytic_length(curve):
    try:
        if curve.IsLinear():
            a, b = curve.PointAtStart, curve.PointAtEnd
            return math.dist((a.X, a.Y, a.Z), (b.X, b.Y, b.Z))
    except Exception:  # noqa: BLE001
        pass
    try:
        arc = curve.TryGetArc()
        if arc is not None:
            return float(arc.AngleRadians) * float(arc.Radius)
    except Exception:  # noqa: BLE001
        pass
    return None
