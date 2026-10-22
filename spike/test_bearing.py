"""Tests for surveyor bearings.

A bearing names a quadrant: N/S + an angle in [0,90] + E/W. Angles are
counter-clockwise from +X. A failure must be an ERROR STRING, never a number.

Run: tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_bearing.py
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


brg = _load("narwhal_test_bearing", "bearing.py")

results = []


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


def okb(name, text, expected, tol=1e-9):
    @check(name)
    def _():
        val, err = brg.bearing_angle(text)
        assert err is None, (text, err)
        assert abs(val - expected) <= tol, (text, val, expected)
    return _


def badb(text, name=None):
    name = name or ("refuses %r" % text)

    @check(name)
    def _():
        val, err = brg.bearing_angle(text)
        assert val is None, (text, "expected error, got", val)
        assert isinstance(err, str) and err, (text, err)
    return _


def okp(name, text, dist, angle, tol=1e-9):
    @check(name)
    def _():
        val, err = brg.parse_surveyor(text)
        assert err is None, (text, err)
        d, a = val
        assert abs(d - dist) <= tol, (text, d)
        assert abs(a - angle) <= tol, (text, a, angle)
    return _


def badp(text, name=None):
    name = name or ("refuses %r" % text)

    @check(name)
    def _():
        val, err = brg.parse_surveyor(text)
        assert val is None, (text, "expected error, got", val)
        assert isinstance(err, str) and err, (text, err)
    return _


# Documented reference example: N30d22'54.43"W = 90 + 30 + 22/60 + 54.43/3600
DMS_ANGLE = 90 + 30 + 22 / 60 + 54.43 / 3600

# ---------------------------------------------------------------------------
# quadrant -> CCW angle
# ---------------------------------------------------------------------------
okb("N0E = 90", "N0E", 90.0)
okb("N90E = 0", "N90E", 0.0)
okb("N45W = 135", "N45W", 135.0)
okb("S45W = 225", "S45W", 225.0)
okb("S45E = 315", "S45E", 315.0)
okb("S0W = 270", "S0W", 270.0)
okb("S90E = 360->0", "S90E", 0.0)
okb("N90W = 180", "N90W", 180.0)
okb("N30d22'54.43\"W = 90+30+22/60+54.43/3600 (exact)",
   'N30d22\'54.43"W', DMS_ANGLE, tol=1e-12)
okb("N30d22'W (no seconds)", "N30d22'W", 90 + 30 + 22 / 60, tol=1e-12)
okb("N30W plain degrees", "N30W", 120.0)
okb("lowercase n30e", "n30e", 60.0)
okb("spaces tolerated N 30 E", "N 30 E", 60.0)

# ---------------------------------------------------------------------------
# surveyor: distance<N/Sangle E/W
# ---------------------------------------------------------------------------
okp("11<N30d22'54.43\"W", '11<N30d22\'54.43"W', 11.0, DMS_ANGLE, tol=1e-12)
okp("5<N45E", "5<N45E", 5.0, 45.0)
okp("2.5<S30W", "2.5<S30W", 2.5, 240.0)

# ---------------------------------------------------------------------------
# errors (never a number)
# ---------------------------------------------------------------------------
badb("N95E", "bearing angle > 90 is an error")
badb("E30N", "bearing with E/W first is an error")
badb("N30", "bearing missing E/W is an error")
badb("30W", "bearing missing N/S is an error")
badb("N-5E", "negative quadrant angle is an error")
badb("NabcE", "non-numeric angle is an error")

badp("5<N95E", "surveyor with angle > 90 is an error")
badp("5<E30N", "surveyor with E/W first is an error")
badp("5<N30", "surveyor bearing missing E/W is an error")
badp("5<30W", "surveyor bearing missing N/S is an error")
badp("N30E", "no distance is an error")
badp("", "empty is an error")
badp("11<", "distance without a bearing is an error")


# ---------------------------------------------------------------------------
failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
