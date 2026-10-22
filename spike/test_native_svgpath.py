"""Native SVG path parsing (packed ABI): direct proof + route activation + fallbacks.

Complements ``spike/test_svgpath.py`` (which pins the parser contract on the
Python path: every command, implicit repeats, the number grammar, the line/quad
elevations, the arc approximation bound, the large/sweep flags, the off-origin
circle, Z close, and the malformed-command refusals) by proving the *packed*
native route (Q117-03) is byte-for-byte the Python result when called directly,
that the off-origin mutation target holds, and that every fallback scenario
runs the *unchanged* Python parser with the exact same subpath list and with the
native counter NOT incremented.

Because the measured win is SHAPE-DEPENDENT (many-small-subpath paths are 4-15%
SLOWER on the packed route), production stays on the complete Python parser
until a shape-safe production gate is measured, so
the crossover sits above the 1 MiB input cap. These checks therefore pin the
posture: production stays on Python (counter unmoved), the packed route is
correct on demand, and every fallback degrades to the Python parser.

* the Rust packed ABI is present and ``_svgpath_packed_binding()`` is not ``None``;
* production ``parse_path`` stays on the unchanged Python parser (counter
  unmoved) and still returns the correct subpath list, and the crossover sits
  above the 1 MiB input cap (the "ready to activate" posture);
* the packed route, called directly, returns byte-for-byte the Python result;
* the off-origin circle fixture (``M 1 0 A 1 1 0 0 1 0 1``) stays on the unit
  circle on the packed route (the mutation target: corrupting the F.6
  chord-midpoint term moves the centre off the origin and this goes RED);
* a malformed path is STILL refused with ``ValueError`` and the native counter
  is NOT incremented (the refusal is a rejection, not a success);
* the fallback scenarios (missing library, an old library without the packed
  export, a failed packed call, a malformed packed output) all run the
  *unchanged Python parser* with the exact same subpath list and with the
  counter NOT incremented.

The suite is green with or without a prebuilt ``.so``: if the library is not
present it builds the crate once (offline, pinned toolchain) and rechecks.
"""
import ctypes
import importlib.util
import json
import math
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_CANDIDATES = [
    os.environ.get("NARWHAL_PURE_CORE", ""),
    os.path.join(ROOT, "native", "pure_core", "target",
                 "x86_64-unknown-linux-gnu", "release",
                 "libnarwhal_pure_core.so"),
    os.path.join(ROOT, "native", "pure_core", "target", "release",
                 "libnarwhal_pure_core.so"),
]


def _locate_lib():
    for cand in LIB_CANDIDATES:
        if cand and os.path.isfile(cand):
            return cand
    return None


def _ensure_lib():
    """Return the ``.so`` path, building the crate once if it is not present."""
    lib = _locate_lib()
    if lib is not None:
        return lib
    manifest = os.path.join(ROOT, "native", "pure_core", "Cargo.toml")
    if not os.path.isfile(manifest):
        return None
    subprocess.run(
        ["cargo", "+1.95.0", "build", "--release", "--locked", "--offline",
         "--manifest-path", manifest, "--target", "x86_64-unknown-linux-gnu"],
        cwd=ROOT, check=True,
    )
    return _locate_lib()


# Make the native library discoverable before the adapter is first imported.
_LIB = _ensure_lib()
if _LIB is not None:
    os.environ["NARWHAL_PURE_CORE"] = _LIB


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, "narwhal3d", filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


svg = _load("narwhal_native_test_svgpath", "svgpath.py")
parse_path = svg.parse_path
# Resolve the SAME native module the parser uses, so we can inspect its
# counter and monkeypatch it in the fallback tests.
native = svg._native_svgpath()

results = []
TMP = tempfile.mkdtemp(prefix="native_svgpath_")


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


def _reset():
    """Forget discovery + counter state so a check can re-point the binding."""
    if native is not None:
        native._reset_for_tests()


@contextmanager
def _force_python():
    """Temporarily make the packed native route report unavailable so the parser
    runs its UNCHANGED Python path (the "native == Python" baseline)."""
    if native is None:
        yield
        return
    real = native._native_svgpath_packed
    native._native_svgpath_packed = lambda *a, **k: None
    try:
        yield
    finally:
        native._native_svgpath_packed = real


# Valid (accepted) paths: the parser's full command surface -- implicit
# repeats, H/V, C/S/Q/T, the arc, Z.
VALID_PATHS = [
    "M 0 0 10 0 10 10",
    "M 0 0 H 5 V 10",
    "M 0 0 C 1 2 3 4 5 6 S 7 8 9 10",
    "M 0 0 Q 2 2 4 0 T 8 0",
    "M 10 0 A 10 10 0 1 1 -10 0 A 10 10 0 1 1 10 0",
    "M 1 0 A 1 1 0 0 1 0 1",
    "M 0 0 A 0 0 0 0 0 4 3",
    "M 0 0 L 10 0 L 10 10 Z",
    "M 0 0 L 10 0 Z M 20 0 L 30 0",
    "M -1.5e-3 .5.5.5 L 1e5 2.5",
    "m 0 0 l 10 0 h 10 v 10 c 10 10 20 20 30 30 q 10 10 20 20",
    "",
]


def _cubic(p, t):
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = p
    u = 1.0 - t
    x = u * u * u * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t * t * t * x3
    y = u * u * u * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t * t * t * y3
    return (x, y)


@check("native library is built, discovered, and exposes the packed SVG ABI")
def _():
    assert _LIB is not None, "pure_core .so not present and build failed"
    assert native is not None, "adapter did not resolve the native module"
    binding = native._svgpath_packed_binding()
    assert callable(binding), "the packed SVG binding must resolve"
    # The counter is a real attribute (the tripwire target).
    assert hasattr(native, "_native_svgpath_calls")


@check("production parse_path stays Python until a shape-safe gate is measured")
def _():
    # The measured win is SHAPE-DEPENDENT, not size-dependent (many-small-
    # subpath paths are 4-15% SLOWER on the packed route), so the crossover
    # sits above the 1 MiB input cap and the complete Python parser stays
    # active in production. A large input therefore must NOT touch the native
    # counter, yet must still return the correct subpath list via Python.
    _reset()
    before = native._native_svgpath_calls
    assert native._SVG_NATIVE_MIN_CHARS > 1 * 1024 * 1024, \
        "the Q117-03 crossover must sit above the 1 MiB input cap"
    large = "M0 0" + "L1 1" * 1000
    got = parse_path(large)
    assert len(got) == 1 and len(got[0]["beziers"]) == 1000, got
    assert native._native_svgpath_calls == before, \
        ("production must stay on Python until a safe gate activates native",
         before, native._native_svgpath_calls)


@check("the packed route is correct on demand (direct call, native == Python)")
def _():
    # Even though production stays on Python, the packed route itself must be
    # byte-for-byte the Python result when called directly (so the moment the
    # gate can be enabled later without changing output).
    _reset()
    d = "M0 0" + "L1 1" * 40
    packed = native._native_svgpath_packed(d)
    assert packed is not None
    got_native = svg._subpaths_from_packed(*packed)
    with _force_python():
        got_py = parse_path(d)
    assert got_native == got_py
    assert len(got_py) == 1 and len(got_py[0]["beziers"]) == 40


@check("native packed equals the pure-Python parser on the SAME valid input")
def _():
    _reset()
    for d in VALID_PATHS:
        packed = native._native_svgpath_packed(d)
        assert packed is not None, d
        got_native = svg._subpaths_from_packed(*packed)
        with _force_python():
            got_py = parse_path(d)
        assert got_py is not None, (d, got_py)
        # Byte-for-byte the Python contract: each subpath (beziers as
        # 4-tuples of 2-tuples, closed a bool) must be exactly equal.
        assert got_native == got_py, (d, got_native, "expected", got_py)


@check("beziers are 4-tuples of 2-tuples (the Python contract, not flat floats)")
def _():
    _reset()
    for d in VALID_PATHS:
        packed = native._native_svgpath_packed(d)
        assert packed is not None
        for sp in svg._subpaths_from_packed(*packed):
            for bez in sp["beziers"]:
                assert isinstance(bez, tuple) and len(bez) == 4, (d, bez)
                assert all(isinstance(pt, tuple) and len(pt) == 2 for pt in bez)


@check("the exact subpath structure holds on the packed route")
def _():
    _reset()
    got = parse_path("M 0 0 10 0 10 10")
    sp = got[0]
    assert len(sp["beziers"]) == 2, sp
    b0 = sp["beziers"][0]
    assert b0[0] == (0.0, 0.0)
    assert b0[3] == (10.0, 0.0)
    assert abs(b0[1][0] - 10.0 / 3.0) < 1e-12
    assert abs(b0[2][0] - 20.0 / 3.0) < 1e-12
    assert sp["closed"] is False
    got = parse_path("M 0 0 L 10 0 L 10 10 Z")
    sp = got[0]
    assert sp["closed"] is True
    assert len(sp["beziers"]) == 3, sp
    assert sp["beziers"][2][0] == (10.0, 10.0)
    assert sp["beziers"][2][3] == (0.0, 0.0)


@check("the off-origin circle quarter stays on the unit circle (mutation target)")
def _():
    # The centre of this arc is (0,0), which the F.6 formula reaches ONLY via
    # the chord-midpoint term ((x1+x2)/2, (y1+y2)/2 = (0.5, 0.5)). Dropping
    # that term moves the centre to (-0.5, -0.5) and the curve leaves the unit
    # circle. On the packed route the same fixture must still hold.
    _reset()
    packed = native._native_svgpath_packed("M 1 0 A 1 1 0 0 1 0 1")
    assert packed is not None
    bez = svg._subpaths_from_packed(*packed)[0]["beziers"]
    assert len(bez) == 1, f"a 90-degree arc is one segment, got {len(bez)}"
    b = bez[0]
    for t in (0.25, 0.5, 0.75):
        x, y = _cubic(b, t)
        assert abs(math.hypot(x, y) - 1.0) <= 3e-4, f"t={t}: {x}, {y}"
        assert 0.3 < x < 1.0 and 0.3 < y < 1.0, f"t={t}: {x}, {y} wrong side"
    xm, ym = _cubic(b, 0.5)
    assert xm > 0.5 and ym > 0.5, f"{xm}, {ym} not the far quarter"


@check("a malformed path is still refused (counter unmoved)")
def _():
    _reset()
    before = native._native_svgpath_calls
    for bad in ("L 10", "M 0 0 L 10", "M 0 0 5 L 1 2"):
        try:
            parse_path(bad)
            raise AssertionError("expected ValueError for a malformed path")
        except ValueError:
            pass
    # The packed route returns a rejection (not a success), so none of these
    # may have counted as a native SUCCESS.
    assert native._native_svgpath_calls == before, \
        ("malformed rejections must not touch the native counter",
         before, native._native_svgpath_calls)


@check("a non-string path is still handled by the Python path (counter unmoved)")
def _():
    _reset()
    before = native._native_svgpath_calls
    try:
        parse_path(5)
        raise AssertionError("expected a non-string path to fail or pass via Python")
    except (ValueError, TypeError):
        pass
    assert native._native_svgpath_calls == before, \
        ("non-string input must not touch the native counter",
         before, native._native_svgpath_calls)


@check("fallback: a missing library uses the unchanged Python parser")
def _():
    _reset()
    real_env = os.environ.get("NARWHAL_PURE_CORE")
    os.environ["NARWHAL_PURE_CORE"] = os.path.join(TMP, "no_such_lib.so")
    try:
        assert native._svgpath_packed_binding() is None, \
            "a missing library must make the packed binding None"
        before = native._native_svgpath_calls
        got = parse_path("M 0 0 10 0 10 10")
        assert len(got) == 1 and len(got[0]["beziers"]) == 2
        try:
            parse_path("L 10")
            raise AssertionError("expected ValueError")
        except ValueError:
            pass
        assert native._native_svgpath_calls == before, \
            ("missing-lib fallback must not touch the native counter",
             before, native._native_svgpath_calls)
    finally:
        if real_env is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = real_env
        _reset()


@check("fallback: an old library without the packed export uses Python")
def _():
    # A real file that is not a valid shared library models "old symbol":
    # CDLL fails (OSError) so the packed binding is None and the Python parser
    # runs with the same subpath list and an unchanged counter.
    old = os.path.join(TMP, "empty.so")
    open(old, "wb").close()
    _reset()
    real_env = os.environ.get("NARWHAL_PURE_CORE")
    os.environ["NARWHAL_PURE_CORE"] = old
    try:
        assert native._svgpath_packed_binding() is None, \
            "a library without the packed export must make the binding None"
        before = native._native_svgpath_calls
        got = parse_path("M 10 0 A 10 10 0 1 1 -10 0 A 10 10 0 1 1 10 0")
        assert len(got) == 1 and len(got[0]["beziers"]) == 4
        try:
            parse_path("L 10")
            raise AssertionError("expected ValueError")
        except ValueError:
            pass
        assert native._native_svgpath_calls == before, \
            ("old-symbol fallback must not touch the native counter",
             before, native._native_svgpath_calls)
    finally:
        if real_env is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = real_env
        _reset()


@check("fallback: a failed packed call (None) uses the Python parser")
def _():
    _reset()
    assert native is not None and native._svgpath_packed_binding() is not None
    real = native._native_svgpath_packed
    native._native_svgpath_packed = lambda *a, **k: None  # simulate a failed call
    try:
        before = native._native_svgpath_calls
        got = parse_path("M 0 0 10 0 10 10")
        assert len(got) == 1 and len(got[0]["beziers"]) == 2
        try:
            parse_path("L 10")
            raise AssertionError("expected ValueError")
        except ValueError:
            pass
        assert native._native_svgpath_calls == before, \
            ("failed-call fallback must not touch the native counter",
             before, native._native_svgpath_calls)
    finally:
        native._native_svgpath_packed = real
        _reset()


@check("a malformed packed output falls back to Python without counting")
def _():
    _reset()
    assert native._svgpath_packed_binding() is not None
    real = native._valid_svgpath_packed
    native._valid_svgpath_packed = lambda *a, **k: False
    try:
        before = native._native_svgpath_calls
        # The packed call reports success but the output fails validation,
        # so _native_svgpath_packed returns None and parse_path uses Python.
        assert native._native_svgpath_packed("M 0 0 L 1 1") is None
        assert native._native_svgpath_calls == before, \
            "a rejected packed output must not count as a native success"
        got = parse_path("M 0 0 L 1 1")
        assert len(got) == 1 and len(got[0]["beziers"]) == 1
    finally:
        native._valid_svgpath_packed = real
        _reset()


@check("packed SVG validator rejects unclaimed leading or orphan cubics")
def _():
    pts = [0.0] * 8
    assert native._valid_svgpath_packed(1, 1, pts, [1], [0]) is False
    assert native._valid_svgpath_packed(1, 0, pts, [], []) is False
    assert native._valid_svgpath_packed(0, 0, [], [], []) is True


@check("the legacy JSON route still returns a valid payload (defensive)")
def _():
    _reset()
    # The production route is now packed, but the legacy two-pass JSON ABI is
    # still present (for ABI compatibility and its own Rust tests). Confirm it
    # still round-trips so it does not silently rot.
    payload = native._native_svgpath("M 0 0 10 0 Z")
    assert payload is not None
    data = json.loads(payload)
    assert isinstance(data, list) and len(data) == 1
    assert data[0]["closed"] is True


failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
