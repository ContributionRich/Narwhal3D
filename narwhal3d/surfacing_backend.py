"""Bounded exact parallel-section Sweep1 integration seam (not registered in UI).

OURS: interpolation of rational section control offsets in a fixed frame, NOT
general freeform sweeping. Rust owns all numerical construction, tolerance/regularity policy.
Python only packs existing curve recipes and transports opaque native BRep bytes.
See docs/surfacing/SWEEP1_PARALLEL.md for guarantees and unsupported cases.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json

from . import native_geometry
from .kernel import KernelError
from .kernel import occt_backend as occt


@dataclass(frozen=True)
class SweepSurface:
    """A construction result, not a second document or geometry registry."""
    shape: object
    recipe: dict
    report: dict


def _bezier(curve, degree, label):
    if not isinstance(curve, dict):
        raise KernelError(f"{label}: expected an exact curve recipe")
    if curve.get("ctype") != "nurbs" or curve.get("degree") != degree:
        raise KernelError(f"{label}: only degree-{degree} single-span NURBS Bezier input is supported")
    if curve.get("periodic") or curve.get("closed"):
        raise KernelError(f"{label}: closed/periodic curves and seam shifts are unsupported")
    if curve.get("knots") != [0.] * degree + [1.] * degree:
        raise KernelError(f"{label}: expected clamped single-span domain [0, 1]; no implicit refit")
    poles = curve.get("poles")
    weights = curve.get("weights", [1.] * (degree + 1))
    if (not isinstance(poles, (list, tuple)) or len(poles) != degree + 1
            or any(not isinstance(p, (list, tuple)) or len(p) != 3 for p in poles)
            or not isinstance(weights, (list, tuple)) or len(weights) != degree + 1):
        raise KernelError(f"{label}: malformed poles/weights")
    return poles, weights


def sweep1_parallel(rail, sections, parameters, *, axis, across, reverse=None,
                    tolerance=1e-7):
    """Return one valid open exact BRep, its replay recipe, and native evidence.

    Two to four quadratic sections with common weights [1,w,1], w in [0.1,1],
    at explicitly ordered cubic rail parameters including 0 and 1. First pole
    (after explicit reversal) is anchored on the rail. All section planes are
    perpendicular to axis; across specifies the monotonic section direction.
    Unsupported inputs raise before any document or master registration.
    """
    rp, rw = _bezier(rail, 3, "rail")
    if list(rw) != [1.] * 4:
        raise KernelError("parallel-section sweep requires a nonrational cubic rail")
    if not isinstance(sections, (list, tuple)) or not 2 <= len(sections) <= 4:
        raise KernelError("parallel-section sweep supports two to four sections")
    n = len(sections)
    if not isinstance(parameters, (list, tuple)) or len(parameters) != n:
        raise KernelError("one explicit rail parameter is required per section")
    reverse = [False] * n if reverse is None else reverse
    if (not isinstance(reverse, (list, tuple)) or len(reverse) != n
            or any(type(r) is not bool for r in reverse)):
        raise KernelError("one explicit boolean orientation is required per section")
    if (not isinstance(axis, (list, tuple)) or len(axis) != 3
            or not isinstance(across, (list, tuple)) or len(across) != 3):
        raise KernelError("parallel-section axis/across must be xyz vectors")
    profiles = [_bezier(s, 2, f"section {i+1}") for i, s in enumerate(sections)]
    weights = list(profiles[0][1])
    if weights[0] != 1. or weights[2] != 1. or any(list(w) != weights for _, w in profiles):
        raise KernelError("sections require common symmetric weights [1, w, 1]; no implicit rebuild")
    packed = ([tolerance, n, weights[1]] + list(axis) + list(across)
              + [c for p in rp for c in p] + list(parameters) + [int(r) for r in reverse]
              + [c for poles, _ in profiles for p in poles for c in p])
    result, report = native_geometry.sweep1_parallel(packed)
    if not result.ok:
        raise KernelError(result.detail)
    shape = occt.shape_from_bytes(result.data)
    recipe = deepcopy(dict(kind="sweep1_parallel", version=1, rail=rail,
                           sections=list(sections), parameters=list(parameters),
                           axis=list(axis), across=list(across), reverse=list(reverse),
                           tolerance=tolerance))
    return SweepSurface(shape, recipe, report)


def sweep1_freeform_approx(rail, sections, parameters, *, reverse=None, tolerance=1e-7):
    """C2 corrected-Frenet Freeform sweep, with tolerance-qualified fitted transport.

    Exact curves/local rational interpolation are retained. OCCT fits their moving
    frame composition as a NURBS face; no viewport geometry is used. Station values
    are native rail parameters, including endpoints, not arc-length fractions.
    """
    if not isinstance(sections, (list, tuple)) or not 2 <= len(sections) <= 4:
        raise KernelError("Freeform requires two to four exact sections")
    if not isinstance(parameters, (list, tuple)) or len(parameters) != len(sections):
        raise KernelError("one explicit native rail parameter is required per section")
    reverse = [False] * len(sections) if reverse is None else reverse
    if (not isinstance(reverse, (list, tuple)) or len(reverse) != len(sections)
            or any(type(v) is not bool for v in reverse)):
        raise KernelError("one explicit boolean orientation is required per section")
    for curve in [rail] + list(sections):
        if not isinstance(curve, dict) or curve.get("closed") or curve.get("periodic"):
            raise KernelError("closed/periodic curves and seam adjustment are unsupported")
    data = occt.freeform_inputs_bytes(rail, sections)
    result, report = native_geometry.sweep1_freeform_approx(data, parameters, reverse, tolerance)
    if not result.ok:
        raise KernelError(result.detail)
    recipe = deepcopy(dict(kind="sweep1_freeform_approx", version=1, rail=rail,
                           sections=list(sections), parameters=list(parameters),
                           reverse=list(reverse), tolerance=tolerance))
    return SweepSurface(occt.shape_from_bytes(result.data), recipe, report)


@dataclass(frozen=True)
class PreparedFreeform:
    """Immutable construction snapshot, not a document store or authorization token."""
    source_ids: tuple
    source_hashes: tuple
    source_domains: tuple
    station_points: tuple
    parameters: tuple
    native_domain: tuple
    reverse: tuple
    tolerance: float
    max_station_error: float
    _inputs_json: str


def _source_snapshot(curves, source_ids):
    import rhino3dm as r3

    if not isinstance(curves, (list, tuple)) or not 3 <= len(curves) <= 5:
        raise KernelError("Freeform requires the rail and two to four source curves")
    if (not isinstance(source_ids, (list, tuple)) or len(source_ids) != len(curves)
            or any(not isinstance(key, str) or not key or len(key) > 512 for key in source_ids)
            or len(set(source_ids)) != len(source_ids)):
        raise KernelError("Freeform requires distinct stable source IDs in selection order")
    encoded = []
    for curve in curves:
        if not isinstance(curve, r3.Curve) or not curve.IsValid:
            raise KernelError("Freeform source must be a valid application curve")
        if curve.IsClosed or curve.IsPeriodic:
            raise KernelError("closed/periodic source curves and seam adjustment are unsupported")
        text = json.dumps(curve.Encode(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(text) > 16 * 1024 * 1024:
            raise KernelError("Freeform source snapshot exceeds 16 MiB")
        encoded.append(hashlib.sha256(text.encode("utf-8")).hexdigest())
    return tuple(encoded)


def prepare_freeform(rail, sections, *, source_ids, station_points=None,
                     reverse=None, tolerance=1e-7):
    """Prepare real application curves using PHYSICAL station intent.

    Points default to each section's start after explicit reversal. Caller-supplied
    points must agree with those unchanged anchors at roundoff scale. Original
    curve parameter values are not native station parameters: callers with a picked
    source parameter first obtain its physical point via that curve's PointAt API.
    Rust selects unique parameters from exact OCCT projection candidates; Python
    only snapshots/serializes application curve handles and coordinates. No surface is built.
    """
    from . import bridge

    if not isinstance(sections, (list, tuple)) or not 2 <= len(sections) <= 4:
        raise KernelError("Freeform preparation requires two to four sections")
    if reverse is not None and not isinstance(reverse, (list, tuple)):
        raise KernelError("one explicit boolean orientation is required per section")
    reverse = tuple([False] * len(sections) if reverse is None else reverse)
    if len(reverse) != len(sections) or any(type(flag) is not bool for flag in reverse):
        raise KernelError("one explicit boolean orientation is required per section")
    sources = [rail] + list(sections)
    hashes = _source_snapshot(sources, source_ids)
    copies = [curve.Duplicate() for curve in sources]
    if _source_snapshot(copies, source_ids) != hashes:
        raise KernelError("stale Freeform snapshot copy differs from source content")
    if _source_snapshot(sources, source_ids) != hashes:
        raise KernelError("stale Freeform source changed during snapshot")
    if station_points is None:
        station_points = [curve.PointAtEnd if flag else curve.PointAtStart
                          for curve, flag in zip(copies[1:], reverse)]
    if not isinstance(station_points, (list, tuple)) or len(station_points) != len(sections):
        raise KernelError("one physical station point is required per section")
    try:
        points = tuple(tuple(float(v) for v in (p.X, p.Y, p.Z)) if hasattr(p, "X")
                       else tuple(float(v) for v in p) for p in station_points)
    except (TypeError, ValueError, AttributeError) as exc:
        raise KernelError(f"invalid physical station point: {exc}") from exc
    recipes = [bridge.curve_recipe(curve) for curve in copies]
    if any(recipe is None for recipe in recipes):
        raise KernelError("source curve has no exact serialization")
    payload = occt.freeform_inputs_bytes(recipes[0], recipes[1:])
    outcome, report = native_geometry.prepare_freeform_stations(payload, points, reverse, tolerance)
    if not outcome.ok:
        raise KernelError(outcome.detail)
    if _source_snapshot(sources, source_ids) != hashes:
        raise KernelError("stale Freeform source changed during preparation")
    return PreparedFreeform(tuple(source_ids), hashes,
                            tuple((c.Domain.T0, c.Domain.T1) for c in copies), points,
                            report["parameters"], report["domain"], reverse, float(tolerance),
                            report["max_error"], json.dumps(recipes, sort_keys=True, allow_nan=False))


def validate_prepared_sources(prepared, source_ids, curves):
    """Check freshly resolved application sources, not a cached selection snapshot."""
    if not isinstance(source_ids, (list, tuple)) or tuple(source_ids) != prepared.source_ids:
        raise KernelError("stale Freeform selection: source IDs/order changed")
    if _source_snapshot(curves, source_ids) != prepared.source_hashes:
        raise KernelError("stale Freeform source geometry changed")


def build_prepared_freeform(prepared, *, resolve_sources):
    """Construct once sources are re-resolved; no publication or Undo side effects.

    resolve_sources() returns (ordered stable IDs, ordered real source curves), rail
    first. It is called before and after construction. Main must additionally check
    document/command generation and cancellation immediately before publication.
    """
    def check():
        try:
            ids, curves = resolve_sources()
        except Exception as exc:
            raise KernelError(f"stale or cancelled Freeform sources: {exc}") from exc
        validate_prepared_sources(prepared, ids, curves)
    check()
    inputs = json.loads(prepared._inputs_json)
    result = sweep1_freeform_approx(inputs[0], inputs[1:], list(prepared.parameters),
                                    reverse=list(prepared.reverse), tolerance=prepared.tolerance)
    check()
    result.recipe["source_provenance"] = dict(
        ids=list(prepared.source_ids), geometry_sha256=list(prepared.source_hashes),
        source_domains=list(prepared.source_domains), station_points=list(prepared.station_points))
    return result


def replay_freeform(recipe):
    if recipe.get("version") != 1:
        raise KernelError("unsupported Freeform sweep recipe version")
    return sweep1_freeform_approx(recipe["rail"], recipe["sections"], recipe["parameters"],
                                  reverse=recipe["reverse"], tolerance=recipe["tolerance"]).shape


def replay(recipe):
    """Reconstruct canonical geometry only; occt.from_recipe applies outer xform."""
    if recipe.get("version") != 1:
        raise KernelError("unsupported parallel-section sweep recipe version")
    return sweep1_parallel(recipe["rail"], recipe["sections"], recipe["parameters"],
                           axis=recipe["axis"], across=recipe["across"],
                           reverse=recipe["reverse"], tolerance=recipe["tolerance"]).shape
