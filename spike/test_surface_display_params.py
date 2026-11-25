#!/usr/bin/env python3
"""Surface display grid lines placed in Rust: the same parameters, bit for bit.

Run with the bundled runtime (it carries rhino3dm):
  scripts/run_suite.sh spike/test_surface_display_params.py

`bridge._surface_axis_params` places the lines of a surface's display grid (and of each curved
brep face OCCT does not tessellate) along one direction: a count from the middle iso-curve's
length, a line on every interior knot, and, where three iso-curves turn too far between lines,
lines placed by their turn and chord needs and bisected until they do not. Rust
(narwhal_pure_core_surface_display_params, native/pure_core/src/surface_display.rs) reads a
NurbsSurface's Encode() record with openNURBS's ON_NurbsSurface::IsoCurve and IsClosed ported.
A BrepFace goes through its underlying surface only where the face has its domains and closure
and evaluates as it, since openNURBS can hold a face's surface transposed and rhino3dm does not
say so; a face shows Python no knots, so none are lines there either. The route needs a platform
in `native_nearest.SURFACE_DISPLAY_QUALIFIED_PLATFORMS` AND the loaded rhino3dm passing the
parity check against `bridge._python_surface_axis_params` on its witnesses. Malformed calls go
to the export in a child process.
"""
import base64
import importlib.util
import json
import math
import os
import platform
import random
import struct
import subprocess
import sys
import time
import types
from unittest.mock import patch

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("NARWHAL_PURE_CORE", os.path.join(
    ROOT, "native", "pure_core", "target", "x86_64-unknown-linux-gnu", "release",
    "libnarwhal_pure_core.so"))
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def load(name):
    spec = importlib.util.spec_from_file_location(
        f"narwhal3d.{name}", os.path.join(ROOT, "narwhal3d", f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    setattr(pkg, name, module)
    spec.loader.exec_module(module)
    return module


for name in ("nurbs_data", "native_nearest", "bridge"):
    load(name)
native = sys.modules["narwhal3d.native_nearest"]
bridge = sys.modules["narwhal3d.bridge"]
TOL = bridge.CURVE_CHORD_TOL
reference = bridge._python_surface_axis_params
checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


def bits(values):
    return struct.pack(f"<{len(values)}d", *values)


qualified = (sys.platform, platform.machine()) in native.SURFACE_DISPLAY_QUALIFIED_PLATFORMS


def routed(surface, direction):
    before = native._native_surface_display_calls
    got = bridge._surface_axis_params(surface, direction, TOL)
    return got, native._native_surface_display_calls == before + 1


def random_surface(rng, kind):
    if kind == "sphere":
        return r3.Sphere(r3.Point3d(rng.uniform(-5, 5), rng.uniform(-5, 5), 0),
                         rng.uniform(0.2, 30)).ToNurbsSurface()
    if kind == "cylinder":
        return r3.Cylinder(r3.Circle(r3.Point3d(rng.uniform(-3, 3), 0, 0), rng.uniform(0.1, 40)),
                           rng.uniform(0.5, 30)).ToNurbsSurface()
    if kind == "revolved":
        points = [r3.Point3d(rng.uniform(1, 4) + 2 * math.sin(k), 0, k * rng.uniform(0.5, 2))
                  for k in range(5)]
        profile = r3.NurbsCurve.Create(False, 3, points)
        axis = r3.Line(r3.Point3d(0, 0, 0), r3.Point3d(0, 0, 1))
        return r3.RevSurface.Create(profile, axis, 0, rng.uniform(1, 2 * math.pi)).ToNurbsSurface()
    rational = kind == "rational"
    ou, ov = rng.randint(2, 5), rng.randint(2, 5)
    nu, nv = rng.randint(ou, ou + 9), rng.randint(ov, ov + 9)
    s = r3.NurbsSurface.Create(3, rational, ou, ov, nu, nv)
    amp = rng.choice([0.0, 0.3, 2.0, 8.0])
    sx, sy = rng.uniform(0.2, 5), rng.uniform(0.2, 5)
    for i in range(nu):
        for j in range(nv):
            w = rng.uniform(0.5, 2.0) if rational else 1.0
            z = amp * math.sin(i * rng.uniform(0.5, 2.0)) * math.cos(j * 0.8) \
                + rng.uniform(-0.1, 0.1) * amp
            s.Points[i, j] = r3.Point4d(i * sx * w, j * sy * w, z * w, w)
    for knots, o, n in ((s.KnotsU, ou, nu), (s.KnotsV, ov, nv)):
        a, b = rng.uniform(-9, 0), rng.uniform(1, 9)
        inner = sorted(rng.uniform(0.3, 9.7) for _ in range(n - o))
        if inner and rng.random() < 0.3:      # a repeated interior knot
            inner[len(inner) // 2:len(inner) // 2 + 1] = [inner[len(inner) // 2]] * min(o - 1, 2)
            inner = sorted(inner)[:n - o]
        for i, k in enumerate([0.0] * (o - 1) + inner + [10.0] * (o - 1)):
            knots[i] = a + (b - a) * k / 10.0
    return s


print("\n-- the route is live where qualified --")
start = time.perf_counter()
capability = native.surface_display_capability(reference, TOL)
cost = time.perf_counter() - start
if qualified:
    check(capability == "qualified",
          f"{sys.platform}/{platform.machine()} qualifies (one-time parity check {cost * 1e3:.0f} ms)")
else:
    check(capability.startswith("not qualified"), f"{capability!r} on an unqualified platform")

print("\n-- random surfaces and brep faces: Rust's lines are the Python route's, bit for bit --")
rng = random.Random(20261125)
KINDS = ("sphere", "cylinder", "revolved", "bump", "rational", "rational")
real_verify = bridge._surface_turn_verify
reached = {"turn route": 0, "bisected": 0, "pole skipped": 0, "closed iso-curves": 0}


def counting_verify(isos, params):
    out = real_verify(isos, params)
    reached["turn route"] += 1
    reached["bisected"] += len(out) != len(params)
    reached["pole skipped"] += len(isos) < bridge.SURFACE_TURN_ISOS
    reached["closed iso-curves"] += any(iso.IsClosed for iso in isos)
    return out


mismatches, decided, total = [], {"surface": 0, "face": 0}, {"surface": 0, "face": 0}
rust_s = python_s = 0.0
with patch.object(bridge, "_surface_turn_verify", counting_verify):
    for trial in range(120):
        surface = random_surface(rng, KINDS[trial % len(KINDS)])
        as_face = trial % 3 == 2
        target = r3.Brep.CreateFromSurface(surface).Faces[0] if as_face else surface
        for direction in (0, 1):
            t = time.perf_counter()
            got, by_rust = routed(target, direction)
            rust_s += time.perf_counter() - t
            t = time.perf_counter()
            want = reference(target, direction, TOL)
            python_s += time.perf_counter() - t
            if bits(got) != bits(want):
                mismatches.append((trial, direction))
            decided["face" if as_face else "surface"] += by_rust
            total["face" if as_face else "surface"] += 1
check(not mismatches, f"240 directions (spheres with poles, cylinders, revolved profiles, rational "
      f"and non-rational bumps with repeated knots; bare and as brep faces) give the Python "
      f"route's bits (mismatches: {mismatches})")
check(not qualified or decided == total, f"Rust placed every one: {decided} of {total}")
check(all(reached.values()), f"every branch is reached: {reached}")
print(f"  Rust route {rust_s * 1e3:.0f} ms, Python route {python_s * 1e3:.0f} ms in total")

print("\n-- a knot-dense surface needs a second call with more room --")
dense = r3.NurbsSurface.Create(3, False, 4, 2, 1400, 2)
for i in range(1400):
    for j in range(2):
        dense.Points[i, j] = r3.Point4d(i * 0.1, j * 3.0, math.sin(i * 0.07) * 4.0, 1.0)
for k in range(len(dense.KnotsU)):
    dense.KnotsU[k] = min(max(k - 2, 0), 1397) * 0.1 + (k % 7) * 1e-4 * (0 < k - 2 < 1397)
dense.KnotsV[0], dense.KnotsV[1] = 0.0, 1.0
got, by_rust = routed(dense, 0)
want = reference(dense, 0, TOL)
check(len(want) > 1024 and bits(got) == bits(want) and (by_rust or not qualified),
      f"{len(want)} parameters (over the first call's 1024) through Rust, the Python route's bits")

print("\n-- what keeps the Python route --")
sphere = r3.Sphere(r3.Point3d(1, 0, 0), 3).ToNurbsSurface()
face = r3.Brep.CreateFromSurface(sphere).Faces[0]
real_point = r3.BrepFace.PointAt
with patch.object(r3.BrepFace, "PointAt", lambda self, u, v: real_point(self, v, u)):
    before = native._native_surface_display_calls
    got = native.surface_display_params(face, 0, TOL, reference)
check(got is None and native._native_surface_display_calls == before,
      "a face read transposed from its surface is left to the Python route")
real_closed = r3.BrepFace.IsClosed
with patch.object(r3.BrepFace, "IsClosed", lambda self, d: not real_closed(self, d)):
    got = native.surface_display_params(face, 0, TOL, reference)
check(got is None, "a face that closes differently from its surface is left to the Python route")
plane = r3.PlaneSurface(r3.Plane.WorldXY(), r3.Interval(0, 2), r3.Interval(0, 3))
check(native.surface_display_params(plane, 0, TOL, reference) is None,
      "a PlaneSurface (not a NURBS record) is left to the Python route")
plain = face.UnderlyingSurface()
got, by_rust = routed(plain, 1)
check(type(plain) is r3.Surface and bits(got) == bits(reference(plain, 1, TOL))
      and (by_rust or not qualified),
      "a plain Surface holding a NurbsSurface shows no knots, through Rust as through Python")

print("\n-- bridge.surface_to_bmesh: the display mesh is unchanged --")


class Verts:
    def __init__(self):
        self.made = []

    def new(self, co):
        vert = types.SimpleNamespace(co=tuple(co), normal=None)
        self.made.append(vert)
        return vert


class Faces:
    def __init__(self):
        self.made = []

    def new(self, verts):
        self.made.append(tuple(id(v) for v in verts))


def mesh(surface):
    bm = types.SimpleNamespace(verts=Verts(), faces=Faces())
    us, vs = (bridge._surface_axis_params(surface, d, TOL) for d in (0, 1))
    bridge._build_grid(bm, surface, us, vs)
    ids = {id(v): k for k, v in enumerate(bm.verts.made)}
    return ([(v.co, tuple(v.normal)) for v in bm.verts.made],
            [tuple(ids[i] for i in f) for f in bm.faces.made])


for label, surface in (("sphere", sphere), ("sphere face", face),
                       ("rational bump", random_surface(rng, "rational"))):
    before = native._native_surface_display_calls
    fast = mesh(surface)
    used = native._native_surface_display_calls == before + 2
    with patch.object(native, "surface_display_params", lambda *args: None):
        slow = mesh(surface)
    check(fast == slow and (used or not qualified),
          f"{label}: {len(fast[0])} vertices and {len(fast[1])} quads, the same as through Python")

print("\n-- qualification: the probe, the platform set, and the parity check --")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import check_pure_core as gate  # noqa: E402

library = os.environ["NARWHAL_PURE_CORE"]
try:
    identity = gate.probe_pure_core_surface_display(gate.Path(library))
except ValueError as exc:
    identity = str(exc)
check(identity == gate.PURE_CORE_SURFACE_DISPLAY_IDENTITY,
      "the probe gives the pinned witness parameters, and the refusals their pinned statuses")
witness_bits = b""
for obj, _source, _visible in native._surface_display_witnesses():
    for direction in (0, 1):
        params = reference(obj, direction, TOL)
        witness_bits += struct.pack(f"<Q{len(params)}d", len(params), *params)
check(__import__("hashlib").sha256(witness_bits).hexdigest()
      == gate.PURE_CORE_SURFACE_DISPLAY_IDENTITY["params_sha256"],
      "the pinned digest is the Python route's on the loaded rhino3dm")
records = gate._SURFACE_WITNESS_RECORDS + gate.PURE_CORE_SURFACE_DISPLAY_PROBE
records = records.replace('"\n    "', "")
check(all(base64.b64decode(s.Encode()["data"]).hex() in records
          for s in (*(w[0] for w in native._surface_offset_witnesses()),
                    native._surface_display_wave())),
      "the probe's records are the runtime witnesses as rhino3dm encodes them")
libm = next((q for q in ("/lib64/libm.so.6", "/usr/lib64/libm.so.6",
                         "/usr/lib/x86_64-linux-gnu/libm.so.6") if os.path.isfile(q)), None)
if libm:
    try:
        gate.probe_pure_core_surface_display(gate.Path(libm))
        refused = False
    except ValueError:
        refused = True
    check(refused, "libm, which lacks the pure-core exports, is refused by the probe")
check(native.SURFACE_DISPLAY_QUALIFIED_PLATFORMS
      == gate.PURE_CORE_SURFACE_DISPLAY_QUALIFIED_PLATFORMS,
      "the runtime platform set equals the qualification record in check_pure_core")
real_platform, real_machine = sys.platform, platform.machine
try:
    for plat, machine in (("win32", "AMD64"), ("linux", "aarch64"), ("darwin", "arm64")):
        sys.platform, platform.machine = plat, (lambda m=machine: m)
        native._surface_display_state = None
        check(native.surface_display_capability(reference, TOL).startswith(
                  f"not qualified on {plat}/{machine}")
              and native.surface_display_params(sphere, 0, TOL, reference) is None,
              f"{plat}/{machine} keeps the Python route")
finally:
    sys.platform, platform.machine = real_platform, real_machine
    native._surface_display_state = None


def nudged(surface, direction, tolerance):
    params = reference(surface, direction, tolerance)
    params[1] = math.nextafter(params[1], math.inf)
    return params


native._surface_display_state = None
check("differs" in native.surface_display_capability(nudged, TOL)
      and native.surface_display_params(sphere, 0, TOL, nudged) is None,
      "an SDK whose Python route rounds one parameter differently keeps the Python route")
native._surface_display_state = None


def signed_zero(surface, direction, tolerance):
    params = reference(surface, direction, tolerance)
    return [-0.0 if p == 0.0 and math.copysign(1.0, p) > 0 else p for p in params]


check(not qualified or "differs" in native.surface_display_capability(signed_zero, TOL),
      "an SDK differing only in a signed zero keeps the Python route")
native._surface_display_state = None
check(native.surface_display_capability(reference, TOL) == "qualified" or not qualified,
      "the parity check passes again with the real SDK")

print("\n-- malformed calls to the export are refused, writing nothing (child process) --")
PROBE = r"""
import ctypes as c, json, sys
fn = c.CDLL(sys.argv[1]).narwhal_pure_core_surface_display_params
Z = c.c_size_t
fn.argtypes = [c.c_void_p, Z, c.c_uint32, c.c_double, c.c_uint8, c.c_void_p, Z, c.c_void_p]
fn.restype = c.c_int32
rec = bytes.fromhex(sys.argv[2])
buf = c.create_string_buffer(rec, len(rec))
out = (c.c_double * 512)(*[-9.0] * 512)
count = (Z * 2)(7, 7)
od, cd = c.cast(out, c.c_void_p), c.cast(count, c.c_void_p)


def call(record=buf, size=len(rec), direction=0, tol=0.01, visible=1, params=od, capacity=512,
         n=cd):
    return fn(record, size, direction, tol, visible, params, capacity, n)


got = {
    "direction 2": call(direction=2),
    "a NaN tolerance": call(tol=float("nan")),
    "an infinite tolerance": call(tol=float("inf")),
    "visible flag 2": call(visible=2),
    "not a record": call(record=b"not a record", size=12),
    "truncated record": call(size=len(rec) - 1),
    "null record": call(record=None),
    "null parameters": call(params=None),
    "null count": call(n=None),
    "misaligned parameters": call(params=c.c_void_p(c.addressof(out) + 1), capacity=8),
    "misaligned count": call(n=c.c_void_p(c.addressof(count) + 1)),
    "count inside the parameters": call(n=c.c_void_p(c.addressof(out) + 8 * 8)),
    "parameters over the record": call(params=c.cast(buf, c.c_void_p), capacity=8),
}
untouched = list(out) == [-9.0] * 512 and list(count) == [7, 7]
room = call(capacity=3)
room_ok = list(out) == [-9.0] * 512 and count[0] > 3 and count[1] == 7
need = count[0]
good = call()
print(json.dumps({"statuses": got, "untouched": untouched, "room": room, "room_ok": room_ok,
                  "good": good, "written": count[0] == need and -9.0 not in list(out)[:need]
                  and list(out)[need:] == [-9.0] * (512 - need)}))
"""
record = base64.b64decode(sphere.Encode()["data"])
child = subprocess.run([sys.executable, "-I", "-c", PROBE, os.environ["NARWHAL_PURE_CORE"],
                        record.hex()], capture_output=True, text=True, timeout=60)
result = json.loads(child.stdout or "{}")
want = {"direction 2": -2, "a NaN tolerance": -2, "an infinite tolerance": -2,
        "visible flag 2": -2, "not a record": 1, "truncated record": 1, "null record": -1,
        "null parameters": -1, "null count": -1, "misaligned parameters": -1,
        "misaligned count": -1, "count inside the parameters": -1,
        "parameters over the record": -1}
check(child.returncode == 0 and result.get("statuses") == want and result.get("untouched")
      and result.get("room") == 4 and result.get("room_ok") and result.get("good") == 0
      and result.get("written"),
      f"{len(want)} malformed calls refused with their statuses, nothing written; a short buffer "
      f"gets only the count, and a good call writes exactly its parameters (exit "
      f"{child.returncode}: {result} {child.stderr[-300:]})")

if qualified:
    print("\n-- cost of both directions' lines (best of 3) --")

    def best(fn, reps):
        out = math.inf
        for _ in range(3):
            start = time.perf_counter()
            for _ in range(reps):
                fn()
            out = min(out, (time.perf_counter() - start) / reps)
        return out

    for label, surface in (("sphere", sphere), ("sphere as a brep face", face),
                           ("rational bump", random_surface(random.Random(5), "rational"))):
        slow = best(lambda: [reference(surface, d, TOL) for d in (0, 1)], 2)
        fast = best(lambda: [bridge._surface_axis_params(surface, d, TOL) for d in (0, 1)], 10)
        print(f"  {label}: Python route {slow * 1e3:5.1f} ms, Rust {fast * 1e3:5.2f} ms "
              f"({slow / fast:.0f}x)")

passed = sum(ok for ok, _ in checks)
print(f"\n{passed}/{len(checks)} passed")
sys.exit(0 if passed == len(checks) else 1)
