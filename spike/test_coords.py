"""Unit tests for documented coordinate entry. Pure, no Blender.

Every expectation is a documented example from
the locally held reference-help accurate-modeling page. If a case here
disagrees with that page, **the page wins**.

The headline behaviour: a typed point is read in the ACTIVE VIEWPORT'S
CONSTRUCTION PLANE, not in world coordinates. In a Front viewport ``1,2`` is
world ``(1, 0, 2)``.

Run: tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_coords.py
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, "narwhal3d", filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


cplane = _load("narwhal_test_cplane", "cplane.py")
coords = _load("narwhal_test_coords", "coords.py")

results = []


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


def close(a, b, tol=1e-9):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


TOP = cplane.WORLD_TOP
FRONT = cplane.WORLD_FRONT
RIGHT = cplane.WORLD_RIGHT


@check("coords.py imports without bpy")
def _():
    assert "bpy" not in sys.modules


# ---------------------------------------------------------------------------
# THE headline case: CPlane coordinates, per viewport
# ---------------------------------------------------------------------------

@check("Top: 1,2 -> world (1,2,0)  (CPlane == world here, so it looks normal)")
def _():
    p, err = coords.resolve("1,2", TOP)
    assert err is None, err
    assert close(p, (1, 2, 0)), p


@check("Front: 1,2 -> world (1,0,2)  -- NOT (1,2,0)")
def _():
    # The bug this fixes: the old parse_point returned (1,2,0) in every viewport.
    p, err = coords.resolve("1,2", FRONT)
    assert err is None, err
    assert close(p, (1, 0, 2)), f"Front 1,2 -> {p}, expected (1,0,2)"


@check("Right: 1,2 -> world (0,1,2)")
def _():
    p, err = coords.resolve("1,2", RIGHT)
    assert err is None, err
    assert close(p, (0, 1, 2)), f"Right 1,2 -> {p}, expected (0,1,2)"


@check("the same text gives DIFFERENT world points per viewport")
def _():
    a, _ = coords.resolve("3,4", TOP)
    b, _ = coords.resolve("3,4", FRONT)
    c, _ = coords.resolve("3,4", RIGHT)
    assert a != b and b != c and a != c, f"{a} {b} {c}"


@check("documented example: 3,4,10 from the CPlane origin")
def _():
    # "To place a point 3 units in the x direction, four units in the y
    # direction, and 10 units in the z direction from the construction plane
    # origin, type 3,4,10"
    p, err = coords.resolve("3,4,10", TOP)
    assert err is None, err
    assert close(p, (3, 4, 10)), p


@check("omitting z sets it to 0 (documented: 2,-11 is 2,-11,0)")
def _():
    p, err = coords.resolve("2,-11", TOP)
    assert err is None, err
    assert close(p, (2, -11, 0)), p


@check("an elevated CPlane shifts a typed point (Elevation + typing combine)")
def _():
    p, err = coords.resolve("1,2", TOP.elevated(5))
    assert err is None, err
    assert close(p, (1, 2, 5)), p


# ---------------------------------------------------------------------------
# world coordinates: the `w` prefix
# ---------------------------------------------------------------------------

@check("w3,4,10 is WORLD, whatever the CPlane is")
def _():
    for plane in (TOP, FRONT, RIGHT):
        p, err = coords.resolve("w3,4,10", plane)
        assert err is None, err
        assert close(p, (3, 4, 10)), f"{plane}: {p}"


@check("w12,6,10 -- the documented world Line example")
def _():
    p, err = coords.resolve("w12,6,10", FRONT)
    assert err is None, err
    assert close(p, (12, 6, 10)), p


@check("the w prefix is case-insensitive")
def _():
    p, _ = coords.resolve("W5,5", FRONT)
    assert close(p, (5, 5, 0)), p


# ---------------------------------------------------------------------------
# the documented origin shortcuts
# ---------------------------------------------------------------------------

@check("0 is the documented shortcut for 0,0 (CPlane origin)")
def _():
    p, err = coords.resolve("0", FRONT)
    assert err is None, err
    assert close(p, (0, 0, 0)), p


@check("w0 is the documented shortcut for w0,0,0")
def _():
    p, err = coords.resolve("w0", FRONT)
    assert err is None, err
    assert close(p, (0, 0, 0)), p


@check("0 on an ELEVATED CPlane is that plane's origin, not the world origin")
def _():
    p, err = coords.resolve("0", TOP.elevated(7))
    assert err is None, err
    assert close(p, (0, 0, 7)), p
    # ...whereas w0 is always the world origin
    p, _ = coords.resolve("w0", TOP.elevated(7))
    assert close(p, (0, 0, 0)), p


# ---------------------------------------------------------------------------
# relative coordinates: r / @ , and rw / @w
# ---------------------------------------------------------------------------

@check("documented example: r2,3 is 2 in x and 3 in y from the previous point")
def _():
    p, err = coords.resolve("r2,3", TOP, last_point=(10, 10, 0))
    assert err is None, err
    assert close(p, (12, 13, 0)), p


@check("@3,4 means the same as r3,4")
def _():
    a, _ = coords.resolve("@3,4", TOP, last_point=(1, 1, 1))
    b, _ = coords.resolve("r3,4", TOP, last_point=(1, 1, 1))
    assert a == b, f"{a} != {b}"


@check("a relative CPlane offset follows the CPlane's AXES, not world axes")
def _():
    # In Front, +y on the CPlane is world +Z, so r0,5 must move UP in world z.
    p, err = coords.resolve("r0,5", FRONT, last_point=(0, 0, 0))
    assert err is None, err
    assert close(p, (0, 0, 5)), f"Front r0,5 -> {p}, expected (0,0,5)"


@check("rw is relative in WORLD axes (not the CPlane's)")
def _():
    p, err = coords.resolve("rw0,5", FRONT, last_point=(0, 0, 0))
    assert err is None, err
    assert close(p, (0, 5, 0)), f"rw0,5 -> {p}, expected (0,5,0)"


@check("the rw prefix is not swallowed by the r prefix")
def _():
    # If `r` were matched first, `rw0,5` would try to parse "w0,5" as numbers.
    _, _, is_rel, err = coords.parse("rw1,2")
    assert err is None, err
    coords_, is_world, is_rel, _ = coords.parse("rw1,2")
    assert is_world and is_rel, f"world={is_world} relative={is_rel}"


@check("a relative coordinate without a previous point is refused, not guessed")
def _():
    p, err = coords.resolve("r1,2", TOP, last_point=None)
    assert p is None and err, "should refuse"
    assert "previous point" in err, err


# ---------------------------------------------------------------------------
# honesty: documented-but-unimplemented forms must REFUSE, never mis-parse
# ---------------------------------------------------------------------------

# RE-EXPRESSED, not weakened (F-A): this used to assert polar was REFUSED, because
# while it was unimplemented the danger was a silent misread. Polar is now built, so
# the same danger is now a WRONG point -- and the check is stricter for it: the exact
# value, in a CPlane where a hard-coded world XY reading would differ.
@check("polar (17<45) is READ as distance 17 at 45 deg in the CPlane, not misread")
def _():
    import math as _m
    p, err = coords.resolve("17<45", TOP)
    r = 17 / _m.sqrt(2)
    assert err is None and all(abs(a - b) < 1e-9 for a, b in zip(p, (r, r, 0))), (p, err)
    front = cplane.WORLD_FRONT
    p, err = coords.resolve("10<90", front)
    assert err is None and all(abs(a - b) < 1e-9 for a, b in zip(p, (0, 0, 10))), (p, err)


@check("spherical (5<30<45) is READ now (exact values pinned in test_pickconstraint)")
def _():
    # RE-EXPRESSED: was "refused" while unbuilt
    p, err = coords.resolve("5<30<45", TOP)
    assert err is None and abs(sum(c * c for c in p) ** 0.5 - 5.0) < 1e-9, (p, err)


@check("point filters (.x, .wxy) are refused")
def _():
    for text in (".x", ".wxy"):
        p, err = coords.resolve(text, TOP)
        assert p is None and coords.UNSUPPORTED in err, f"{text}: {err}"


# Fractions, units and formulas are read through the shared calculator/number grammar;
# pin exact values so a parser divergence cannot silently move a point.
@check("fractions are READ: 5/16,1-3/4 -> (0.3125, 1.75)")
def _():
    p, err = coords.resolve("5/16,1-3/4", TOP)
    assert err is None and close(p, (0.3125, 1.75, 0.0)), (p, err)


@check("unit suffixes CONVERT to the model unit; formulas evaluate; commas inside "
       "functions are not separators")
def _():
    p, err = coords.resolve("1.234cm,2", TOP)
    assert err is None and close(p, (12.34, 2.0, 0.0)), (p, err)
    p, err = coords.resolve("16'5\",0", TOP)
    assert err is None and close(p, (5003.8, 0.0, 0.0)), (p, err)
    p, err = coords.resolve("10*sin(30d),pow(2,3)", TOP)
    assert err is None and close(p, (5.0, 8.0, 0.0)), (p, err)
    coords.set_model_unit("Inches")
    try:
        p, err = coords.resolve("25.4mm,1", TOP)
        assert err is None and close(p, (1.0, 1.0, 0.0)), (p, err)
    finally:
        coords.set_model_unit("Millimeters")
    p, err = coords.resolve("abc,1", TOP)
    assert p is None and err, "garbage must still be refused"


@check("a bare number is a distance constraint, so it is refused")
def _():
    # The old parser incorrectly turned a distance constraint into a point.
    p, err = coords.resolve("5", TOP)
    assert p is None, f"bare 5 should not be a coordinate, got {p}"
    assert "distance constraint" in err, err


@check("a coordinate may not contain spaces (documented)")
def _():
    p, err = coords.resolve("1, 2", TOP)
    assert p is None and "space" in err, err


@check("more than three coordinates is refused")
def _():
    p, err = coords.resolve("1,2,3,4", TOP)
    assert p is None and err, err


@check("empty input is refused cleanly")
def _():
    for text in ("", "   ", None):
        p, err = coords.resolve(text, TOP)
        assert p is None and err, f"{text!r}"


@check("a prefix with no coordinates is refused")
def _():
    p, err = coords.resolve("w", TOP)
    assert p is None and err, err


# ---------------------------------------------------------------------------
# round-trip against the CPlane itself
# ---------------------------------------------------------------------------

@check("resolve agrees with CPlane.to_world for every named CPlane")
def _():
    for name, plane in cplane.WORLD_CPLANES.items():
        p, err = coords.resolve("2,3,4", plane)
        assert err is None, f"{name}: {err}"
        assert close(p, plane.to_world(2, 3, 4)), name


# ---------------------------------------------------------------------------
# format_world must round-trip through resolve in every construction plane.
#
# A viewport click is already a world point; feeding it as bare text would read it
# in the active construction plane and transform it twice. So
# the point was transformed twice -- a click at world (5,0,3) in Front became
# (5,-3,0), dropping the centre to z=0 ("it always goes down"), and Right
# scrambled to (3,0,5). Top's CPlane IS world XY, so the double transform was the
# identity and the bug was invisible there.
#
# This is the invariant that makes that class of bug impossible.
# ---------------------------------------------------------------------------

PICKS = (
    (0.0, 0.0, 0.0),
    (5.0, 0.0, 3.0),
    (0.0, 5.0, 3.0),
    (-2.5, 7.25, -1.75),
    (123.4567, -89.0123, 45.6789),
)


@check("format_world round-trips through resolve in every named CPlane")
def _():
    for name, plane in cplane.WORLD_CPLANES.items():
        for pick in PICKS:
            text = coords.format_world(pick)
            got, err = coords.resolve(text, plane)
            assert err is None, f"{name} {pick}: {err}"
            assert close(got, pick), (
                f"{name}: picked {pick}, fed {text!r}, got {got}")


@check("format_world round-trips in a ROTATED custom CPlane too")
def _():
    # not axis-aligned, so a double transform cannot accidentally be the identity
    import math

    a = math.radians(37.0)
    plane = cplane.CPlane((1.0, 2.0, 3.0),
                          (math.cos(a), math.sin(a), 0.0),
                          (-math.sin(a), math.cos(a), 0.0))
    for pick in PICKS:
        got, err = coords.resolve(coords.format_world(pick), plane)
        assert err is None, f"{pick}: {err}"
        assert close(got, pick), f"picked {pick}, got {got}"


@check("format_world round-trips EXACTLY -- a snap at 1/3 must not become 0.3333")
def _():
    # Every PICKS entry above has <= 4 decimals, so a 4-decimal formatter passed
    # them all while rounding every real snap. These cannot survive rounding.
    import math
    hard = ((1.0 / 3.0, math.pi, -2.0 / 7.0),
            (3.0 / math.sqrt(2.0), 3.0 / math.sqrt(2.0), 1e-7),
            (123456.789012345, -0.000123456789, 5e-12))
    for name, plane in cplane.WORLD_CPLANES.items():
        for pick in hard:
            got, err = coords.resolve(coords.format_world(pick), plane)
            assert err is None, f"{name} {pick}: {err}"
            assert all(abs(a - b) <= 1e-12 * max(1.0, abs(b))
                       for a, b in zip(got, pick)), (
                f"{name}: picked {pick}, got {got} -- precision lost in transit")


@check("format_world emits the documented world prefix")
def _():
    text = coords.format_world((5.0, 0.0, 3.0))
    assert text.startswith("w"), text
    # and it must NOT be read as CPlane coordinates in a non-Top plane
    bare, _ = coords.resolve(text[1:], cplane.WORLD_FRONT)
    world, _ = coords.resolve(text, cplane.WORLD_FRONT)
    assert not close(bare, world), (
        "the bare and world readings are identical, so this test proves nothing "
        "-- pick a point/plane where they differ")
    assert close(world, (5.0, 0.0, 3.0)), world


# ---------------------------------------------------------------------------
failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
