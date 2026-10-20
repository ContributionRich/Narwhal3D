r"""Tests for the SVG path parser. Pure Python, no bpy.

Pins the contract:
* every command (M L H V C S Q T A Z, absolute + relative) parses;
* implicit repeats: `M 0 0 10 0 10 10` = M then two L;
* number formats: `-1.5e-3`, `.5.5` (= .5 .5), `1e5`, commas optional;
* lines -> cubics with control points at 1/3, 2/3;
* Q elevates EXACTLY to a cubic (P0+2/3(P1-P0), P2+2/3(P1-P2));
* S reflects the previous C/S second control point (current point if the
  previous command was not a curve); T reflects the previous Q/T control
  point (same default);
* A full circle from two `A` commands stays within 3e-4 r of the true circle
  at 50 samples per segment (the documented arc-approximation bound);
* A's large/sweep flags pick the correct arc (verified against the exact
  circle it lies on);
* relative commands give the same geometry as the equivalent absolute ones;
* Z closes (closed=True, closing line added); an unclosed path that ends at
  its start is NOT closed.

Run: tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_svgpath.py
"""
import importlib.util
import math
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


svg = _load("narwhal_test_svgpath", "svgpath.py")

results = []


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


def _cubic(p, t):
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = p
    u = 1.0 - t
    x = u*u*u*x0 + 3*u*u*t*x1 + 3*u*t*t*x2 + t*t*t*x3
    y = u*u*u*y0 + 3*u*u*t*y1 + 3*u*t*t*y2 + t*t*t*y3
    return (x, y)


def _assert_eq(a, b, tol=1e-9, msg=""):
    assert abs(a - b) <= tol, f"{msg} {a!r} != {b!r}"


@check("module loads without bpy and parse_path exists")
def _():
    assert "bpy" not in sys.modules
    assert callable(svg.parse_path)


@check("M 0 0 10 0 10 10 -> M then two L (implicit repeats)")
def _():
    got = svg.parse_path("M 0 0 10 0 10 10")
    assert len(got) == 1, got
    sp = got[0]
    assert len(sp["beziers"]) == 2, sp
    # first line (0,0)->(10,0)
    b0 = sp["beziers"][0]
    _assert_eq(b0[0][0], 0.0); _assert_eq(b0[0][1], 0.0)
    _assert_eq(b0[3][0], 10.0); _assert_eq(b0[3][1], 0.0)
    _assert_eq(b0[1][0], 10.0 / 3.0); _assert_eq(b0[2][0], 20.0 / 3.0)
    # second line (10,0)->(10,10)
    b1 = sp["beziers"][1]
    _assert_eq(b1[0][0], 10.0); _assert_eq(b1[0][1], 0.0)
    _assert_eq(b1[3][0], 10.0); _assert_eq(b1[3][1], 10.0)
    assert sp["closed"] is False


@check("H and V: horizontal / vertical lines as cubics")
def _():
    got = svg.parse_path("M 0 0 H 5 V 10")
    assert len(got) == 1
    sp = got[0]
    assert len(sp["beziers"]) == 2, sp
    b0 = sp["beziers"][0]  # (0,0)->(5,0)
    _assert_eq(b0[0][0], 0.0); _assert_eq(b0[0][1], 0.0)
    _assert_eq(b0[3][0], 5.0); _assert_eq(b0[3][1], 0.0)
    b1 = sp["beziers"][1]  # (5,0)->(5,10)
    _assert_eq(b1[0][0], 5.0); _assert_eq(b1[0][1], 0.0)
    _assert_eq(b1[3][0], 5.0); _assert_eq(b1[3][1], 10.0)


@check("C: cubic stored directly")
def _():
    got = svg.parse_path("M 0 0 C 1 2 3 4 5 6")
    sp = got[0]
    b = sp["beziers"][0]
    assert b[0] == (0.0, 0.0)
    assert b[1] == (1.0, 2.0)
    assert b[2] == (3.0, 4.0)
    assert b[3] == (5.0, 6.0)


@check("Q elevates EXACTLY to a cubic")
def _():
    # quadratic (0,0) ctrl (2,2) -> (4,0)
    got = svg.parse_path("M 0 0 Q 2 2 4 0")
    b = got[0]["beziers"][0]
    assert b[0] == (0.0, 0.0)
    # c1 = P0 + 2/3 (Q - P0)
    _assert_eq(b[1][0], 0.0 + (2.0 / 3.0) * (2.0 - 0.0))
    _assert_eq(b[1][1], 0.0 + (2.0 / 3.0) * (2.0 - 0.0))
    # c2 = P2 + 2/3 (Q - P2)
    _assert_eq(b[2][0], 4.0 + (2.0 / 3.0) * (2.0 - 4.0))
    _assert_eq(b[2][1], 0.0 + (2.0 / 3.0) * (2.0 - 0.0))
    assert b[3] == (4.0, 0.0)


@check("S reflects the previous C's second control point")
def _():
    # C: c1=(1,2) c2=(3,4) end=(5,6); then S: x2=7,y2=8,x=9,y=10
    # -> c1 = 2*(5,6) - (3,4) = (7,8); c2 = (7,8); end = (9,10)
    got = svg.parse_path("M 0 0 C 1 2 3 4 5 6 S 7 8 9 10")
    sp = got[0]
    assert len(sp["beziers"]) == 2, sp
    b1 = sp["beziers"][1]
    _assert_eq(b1[0][0], 5.0); _assert_eq(b1[0][1], 6.0)
    _assert_eq(b1[1][0], 7.0); _assert_eq(b1[1][1], 8.0)   # reflected c1
    _assert_eq(b1[2][0], 7.0); _assert_eq(b1[2][1], 8.0)   # c2 (from params)
    _assert_eq(b1[3][0], 9.0); _assert_eq(b1[3][1], 10.0)  # end


@check("S after a non-curve uses the current point as c1")
def _():
    # M 0 0 L 2 0 S 3 1 4 0 : previous command is L (not C/S) -> c1 = current (2,0)
    got = svg.parse_path("M 0 0 L 2 0 S 3 1 4 0")
    sp = got[0]
    b1 = sp["beziers"][1]
    _assert_eq(b1[0][0], 2.0); _assert_eq(b1[0][1], 0.0)
    _assert_eq(b1[1][0], 2.0); _assert_eq(b1[1][1], 0.0)   # = current point
    _assert_eq(b1[2][0], 3.0); _assert_eq(b1[2][1], 1.0)
    _assert_eq(b1[3][0], 4.0); _assert_eq(b1[3][1], 0.0)


@check("T reflects the previous Q's control point")
def _():
    # Q 2 2 4 0 -> qctrl (2,2), end (4,0); then T 8 0 -> q = 2*(4,0)-(2,2)=(6,-2)
    got = svg.parse_path("M 0 0 Q 2 2 4 0 T 8 0")
    sp = got[0]
    b1 = sp["beziers"][1]
    # elevated from quadratic with control (6,-2):
    # c1 = (4,0)+2/3*((6,-2)-(4,0)) = (4+4/3, 0-4/3) = (5.333.., -1.333..)
    _assert_eq(b1[0][0], 4.0); _assert_eq(b1[0][1], 0.0)
    _assert_eq(b1[1][0], 4.0 + (2.0 / 3.0) * (6.0 - 4.0))
    _assert_eq(b1[1][1], 0.0 + (2.0 / 3.0) * (-2.0 - 0.0))
    _assert_eq(b1[2][0], 8.0 + (2.0 / 3.0) * (6.0 - 8.0))
    _assert_eq(b1[2][1], 0.0 + (2.0 / 3.0) * (-2.0 - 0.0))
    _assert_eq(b1[3][0], 8.0); _assert_eq(b1[3][1], 0.0)


@check("T after a non-curve uses the current point as control")
def _():
    # M 0 0 L 2 0 T 4 0 : previous is L -> q = current (2,0)
    got = svg.parse_path("M 0 0 L 2 0 T 4 0")
    sp = got[0]
    b1 = sp["beziers"][1]
    # quadratic (2,0) ctrl (2,0) -> (4,0): c1 = P0 + 2/3 (Q-P0) = (2,0);
    # c2 = P2 + 2/3 (Q-P2) = (4,0) + 2/3 ((2,0)-(4,0)) = 4 - 4/3 = 8/3
    _assert_eq(b1[0][0], 2.0); _assert_eq(b1[0][1], 0.0)
    _assert_eq(b1[1][0], 2.0); _assert_eq(b1[1][1], 0.0)
    _assert_eq(b1[2][0], 8.0 / 3.0); _assert_eq(b1[2][1], 0.0)
    _assert_eq(b1[3][0], 4.0); _assert_eq(b1[3][1], 0.0)


@check("relative vs absolute: L, H, V, C, Q give the same geometry")
def _():
    abs_p = svg.parse_path("M 0 0 L 10 0 H 20 V 10 C 30 20 40 30 50 40 Q 60 50 70 60")
    rel_p = svg.parse_path("m 0 0 l 10 0 h 10 v 10 c 10 10 20 20 30 30 q 10 10 20 20")
    # rel: M0,0 L(0+10,0)=(10,0) H(10+10)=(20,0) V(0+10)=(20,10)
    #      C(20+10,10+10)=(30,20) (20+20,10+20)=(40,30) (20+30,10+30)=(50,40)
    #      Q(20+40? ...) -> careful: relative Q adds to current (50,40)
    #      q 10 10 20 20 -> ctrl (60,50) end (70,60)
    assert len(abs_p) == 1 and len(rel_p) == 1
    a = abs_p[0]["beziers"]
    b = rel_p[0]["beziers"]
    assert len(a) == len(b), (len(a), len(b))
    for ai, bi in zip(a, b):
        for pt_a, pt_b in zip(ai, bi):
            _assert_eq(pt_a[0], pt_b[0])
            _assert_eq(pt_a[1], pt_b[1])


@check("relative M offsets from the current point")
def _():
    # m 10 10 is a NEW moveto (1,0)+(10,10)=(11,10); the extra group 5 5 in
    # the SAME m command is the implicit l -> (16,15).  A separate `m 5 5`
    # would be another moveto, not a lineto (per the SVG spec).
    got = svg.parse_path("M 0 0 L 1 0 m 10 10 5 5")
    assert len(got) == 2, got
    assert got[0]["beziers"][0][0] == (0.0, 0.0)
    assert got[0]["beziers"][0][3] == (1.0, 0.0)
    # the second subpath is the line (11,10) -> (16,15)
    assert got[1]["beziers"][0][0] == (11.0, 10.0)
    assert got[1]["beziers"][0][3] == (16.0, 15.0)


@check("a full circle from two A commands stays within 3e-4 r of the circle")
def _():
    r = 10.0
    d = (f"M {r} 0 "
         f"A {r} {r} 0 1 1 {-r} 0 "
         f"A {r} {r} 0 1 1 {r} 0")
    got = svg.parse_path(d)
    assert len(got) == 1, got
    bez = got[0]["beziers"]
    # two 180-degree arcs, each split into two 90-degree segments -> 4 beziers
    assert len(bez) == 4, f"expected 4 arc beziers, got {len(bez)}"
    cx, cy = 0.0, 0.0
    worst = 0.0
    for b in bez:
        for k in range(51):
            t = k / 50.0
            x, y = _cubic(b, t)
            ddev = math.hypot(x - cx, y - cy) - r
            worst = max(worst, abs(ddev))
    assert worst <= 3e-4 * r, f"max radial deviation {worst} exceeds {3e-4*r}"


@check("A large/sweep flags pick the correct arc (verified against the circle)")
def _():
    # unit circle, (1,0) -> (-1,0): sweep=1 large=0 = upper half (through (0,1));
    # sweep=0 large=0 = lower half (through (0,-1)); large=1 sweep=1 = lower
    # (the long way, through (0,-1)).
    up = svg.parse_path("M 1 0 A 1 1 0 0 1 -1 0")
    low = svg.parse_path("M 1 0 A 1 1 0 0 0 -1 0")
    # each is one 180° arc (two 90° segments).  The arc's END is (-1,0), so
    # the midpoint of each segment is the top (sweep=1) or bottom
    # (sweep=0) of the circle: (0,1) / (0,-1) exactly.
    bu = up[0]["beziers"][0][3]
    bl = low[0]["beziers"][0][3]
    _assert_eq(bu[0], 0.0, 1e-9); _assert_eq(bu[1], 1.0, 1e-9)
    _assert_eq(bl[0], 0.0, 1e-9); _assert_eq(bl[1], -1.0, 1e-9)
    # and both midpoints lie (approximately) on the unit circle
    assert abs(math.hypot(*bu) - 1.0) <= 3e-4, bu
    assert abs(math.hypot(*bl) - 1.0) <= 3e-4, bl


@check("arc off the origin: M 1 0 A 1 1 0 0 1 0 1 is the unit-circle quarter")
def _():
    # The centre of this arc is (0,0) -- the F.6 formula needs the chord
    # midpoint term ((x1+x2)/2, (y1+y2)/2 = (0.5, 0.5)) to get there.
    # sweep=1 is the positive-angle (math CCW) quarter through
    # (sqrt(2)/2, sqrt(2)/2).  A dropped midpoint term moves the centre to
    # (-0.5, -0.5) and the curve leaves the unit circle.
    got = svg.parse_path("M 1 0 A 1 1 0 0 1 0 1")
    bez = got[0]["beziers"]
    assert len(bez) == 1, f"a 90-degree arc is one segment, got {len(bez)}"
    b = bez[0]
    _assert_eq(b[0][0], 1.0); _assert_eq(b[0][1], 0.0)
    _assert_eq(b[3][0], 0.0); _assert_eq(b[3][1], 1.0)
    for t in (0.25, 0.5, 0.75):
        x, y = _cubic(b, t)
        assert abs(math.hypot(x, y) - 1.0) <= 3e-4, f"t={t}: {x}, {y}"
        assert 0.3 < x < 1.0 and 0.3 < y < 1.0, f"t={t}: {x}, {y} wrong side"
    # the midpoint of the cubic sits exactly on the far quarter
    xm, ym = _cubic(b, 0.5)
    assert xm > 0.5 and ym > 0.5, f"{xm}, {ym} not the far quarter"
    # the sweep=0 arc is the OTHER quarter circle (centre (1,1)); its
    # midpoint is (1-1/sqrt(2))^2... ~ (0.293, 0.293), off the unit circle
    other = _cubic(svg.parse_path("M 1 0 A 1 1 0 0 0 0 1")[0]["beziers"][0], 0.5)
    assert abs(math.hypot(*other) - 1.0) > 0.1, other


@check("zero-radius arc degenerates to a line (SVG spec)")
def _():
    got = svg.parse_path("M 0 0 A 0 0 0 0 0 4 3")
    b = got[0]["beziers"][0]
    assert b[0] == (0.0, 0.0)
    assert b[3] == (4.0, 3.0)
    # control points on the straight line at 1/3 and 2/3
    _assert_eq(b[1][0], 4.0 / 3.0); _assert_eq(b[1][1], 3.0 / 3.0)
    _assert_eq(b[2][0], 8.0 / 3.0); _assert_eq(b[2][1], 6.0 / 3.0)


@check("number formats: -1.5e-3, .5.5 (= .5 .5), 1e5, commas optional")
def _():
    # -1.5e-3 = -0.0015 ; ".5.5.5" = the three numbers 0.5 0.5 0.5 ; 1e5 = 100000
    # M(-0.0015, 0.5) -> implicit L(0.5, 0.5) -> L(100000, 2.5)
    got = svg.parse_path("M -1.5e-3 .5.5.5 L 1e5 2.5")
    sp = got[0]
    b0 = sp["beziers"][0]
    _assert_eq(b0[0][0], -0.0015)
    _assert_eq(b0[0][1], 0.5)
    _assert_eq(b0[3][0], 0.5)
    _assert_eq(b0[3][1], 0.5)
    b1 = sp["beziers"][1]
    _assert_eq(b1[3][0], 100000.0)
    _assert_eq(b1[3][1], 2.5)


@check("comma and space are interchangeable separators")
def _():
    a = svg.parse_path("M 0 0 L 10 0")
    b = svg.parse_path("M0,0L10,0")
    c = svg.parse_path("M 0,0 L 10,0")
    for other in (b, c):
        assert len(a) == len(other) == 1
        _assert_eq(a[0]["beziers"][0][3][0], other[0]["beziers"][0][3][0])
        _assert_eq(a[0]["beziers"][0][3][1], other[0]["beziers"][0][3][1])


@check("Z closes the path (closing line added, closed=True)")
def _():
    got = svg.parse_path("M 0 0 L 10 0 L 10 10 Z")
    sp = got[0]
    # three lines: (0,0)->(10,0), (10,0)->(10,10), (10,10)->(0,0)
    assert len(sp["beziers"]) == 3, sp
    assert sp["closed"] is True
    last = sp["beziers"][2]
    assert last[0] == (10.0, 10.0)
    assert last[3] == (0.0, 0.0)


@check("a path that ends at its start without Z is NOT closed")
def _():
    got = svg.parse_path("M 0 0 L 10 0 L 10 10 L 0 0")
    sp = got[0]
    assert sp["closed"] is False, sp
    # the last segment (0,0)->(0,0) is a zero-length line; the path is open
    assert len(sp["beziers"]) == 3


@check("a new M after Z starts a fresh subpath")
def _():
    got = svg.parse_path("M 0 0 L 10 0 Z M 20 0 L 30 0")
    assert len(got) == 2, got
    assert got[0]["closed"] is True
    assert got[1]["closed"] is False
    assert got[1]["beziers"][0][0] == (20.0, 0.0)


@check("a bad command (too few params) raises ValueError")
def _():
    try:
        svg.parse_path("L 10")  # L needs 2 params
        raise AssertionError("expected ValueError for an under-parametrized L")
    except ValueError as e:
        assert "L" in str(e), str(e)


# ---------------------------------------------------------------------------
failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
