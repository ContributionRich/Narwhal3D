"""Python host boundary only; numeric corpus is native/pure_core/tests/interpolate_golden.rs.

No interpolation algorithm/oracle/fallback lives here. Expected ABI framing comes
from independently captured lossless fixtures. Original same-host 10%-win
qualification is retained offline; see fixtures/interpolate_provenance.txt.
"""
import hashlib
import importlib.util
import os
from pathlib import Path
import struct
import sys
import types

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg
native = load("narwhal3d.native_interpolate", ROOT / "narwhal3d/native_interpolate.py")
pkg.native_interpolate = native
live = load("narwhal3d.interpolate", ROOT / "narwhal3d/interpolate.py")
pkg.interpolate = live
raw = (ROOT / "native/pure_core/tests/fixtures/interpolate_host.txt").read_bytes()
assert hashlib.sha256(raw).hexdigest() == "fd7e76c5d0bc5052e8ffaaaf19ba7cfb92b4e2323a4af06d1d4774cd0ef6a139"
records = {line.split("|", 1)[0]: line.split("|") for line in raw.decode().splitlines() if not line.startswith("#")}
assert len(records) == 39
styles = ("Uniform", "Chord", "SqrtChrd")


def decode(field):
    return [] if field == "-" else [struct.unpack(">d", bytes.fromhex(v))[0] for v in field.split(",")]


def arguments(record):
    pts = decode(record[8])
    pts = [tuple(pts[i:i + 3]) for i in range(0, len(pts), 3)]
    if record[1] == "skin":
        width = int(record[3])
        pts = [pts[i:i + width] for i in range(0, len(pts), width)]
    return (pts, styles[int(record[5])]) if record[1] == "periodic" else (pts, int(record[4]), styles[int(record[5])])


def flatten(value):
    if isinstance(value, (tuple, list)):
        for item in value:
            yield from flatten(item)
    else:
        yield float(value)


def framed(got, record):
    assert got is not None, record[0]
    assert got[2] == int(record[7]), record[0]
    rows, width = int(record[2]), int(record[3])
    if record[1] == "skin":
        assert len(got[0]) == width and all(len(col) == rows for col in got[0])
        assert all(len(point) == 3 for col in got[0] for point in col)
    else:
        assert len(got[0]) == rows + (3 if record[1] == "periodic" else 0)
        assert all(len(point) == 3 for point in got[0])
    for value, field in ((got[0], record[9]), (got[1], record[10])):
        assert [struct.pack(">d", x).hex() for x in flatten(value)] == field.split(","), record[0]


checks = 0
# ORIGINAL production calls at 32 open / 8x8 skin / 4 periodic: never replace
# these with adapter calls. Counter +1 prevents a Python fallback masking drift.
for label, fn, counter in (
    ("host-open-32-1", live.interpolate_points, "_native_calls"),
    ("host-skin-8-8-1", live.skin_control_net, "_native_skin_calls"),
    ("production-periodic-4", live.interpolate_points_periodic, "_native_periodic_calls"),
):
    record = records[label]
    before = getattr(native, counter)
    framed(fn(*arguments(record)), record)
    assert getattr(native, counter) == before + 1, (label, "production did not route through Rust")
    checks += 1
assert (live.NATIVE_INTERPOLATE_MIN_POINTS, live.NATIVE_SKIN_MIN_VALUES, live.NATIVE_PERIODIC_MIN_POINTS) == (16, 64, 3)
checks += 1

for label, fn, counter in (
    ("host-open-16-1", native.interpolate, "_native_calls"),
    ("host-skin-8-8-1", native.skin_control_net, "_native_skin_calls"),
    ("host-periodic-6-1", native.interpolate_periodic, "_native_periodic_calls"),
):
    record = records[label]
    args = arguments(record)
    before = getattr(native, counter)
    framed(fn(*args), record)
    assert getattr(native, counter) == before + 1
    checks += 1
    # Host conversion/refusal tests, not numeric interpolation computations.
    malformed = ([],) + args[1:]
    assert fn(*malformed) is None
    assert fn(*(args[:-1] + ("not-a-style",))) is None
    bad = [[(float("nan"), 0., 0.)] for _ in range(3)] if record[1] == "skin" else [(float("nan"), 0., 0.)] * 3
    assert fn(*((bad,) + args[1:])) is None
    short = [[(0.,)] for _ in range(3)] if record[1] == "skin" else [(0.,)] * 3
    assert fn(*((short,) + args[1:])) is None
    assert getattr(native, counter) == before + 1, "refusal incremented success counter"
    checks += 4

for label in ("host-two-points", "host-repeat-first"):
    assert native.interpolate_periodic(*arguments(records[label])) is None
    checks += 1
assert native.skin_control_net([[(0., 0., 0.)], []], 3, "Chord") is None
checks += 1

# Simulate malformed C framing without executing or reimplementing any math.
# Restore bindings even on failure; the actual production routing above is unmocked.
for label, fn, binding, counter in (
    ("host-open-16-1", native.interpolate, "_binding", "_native_calls"),
    ("host-skin-8-8-1", native.skin_control_net, "_skin_binding", "_native_skin_calls"),
    ("host-periodic-6-1", native.interpolate_periodic, "_periodic_binding", "_native_periodic_calls"),
):
    original = getattr(native, binding)
    before = getattr(native, counter)
    try:
        for code in (0, -2, -4):
            # Return success with untouched zero counts, or explicit refusal.
            setattr(native, binding, lambda *unused, code=code: code)
            assert fn(*arguments(records[label])) is None, (label, "accepted malformed ABI output")
            assert getattr(native, counter) == before
            checks += 1
    finally:
        setattr(native, binding, original)

active = os.environ.get("NARWHAL_PURE_CORE") or str(ROOT / "narwhal3d/native/libnarwhal_pure_core.so")
print(f"{checks} interpolation host-boundary checks passed; active core: {active}")
