#!/usr/bin/env python3
"""Surface display grids evaluated in Rust: the same points and normals, bit for bit.

Run with the bundled runtime (it carries rhino3dm):
  scripts/run_suite.sh spike/test_surface_grid.py

`bridge._build_grid` (the display mesh of a surface, and of each curved brep face that OCCT
does not tessellate) asks `native_nearest.surface_grid` for PointAt / NormalAt at every grid
node. Rust (narwhal_pure_core_surface_grid, native/pure_core/src/surface_offset.rs) reads a
NurbsSurface's Encode() record and evaluates with openNURBS's ON_NurbsSurface::Evaluate and
ON_Surface::EvNormal (with its singular-point branch) ported. A BrepFace goes through its
underlying surface only where the face gives the same bits at a few nodes, since openNURBS can
hold a face's surface transposed and rhino3dm does not say so. The route needs a platform in
`native_nearest.SURFACE_GRID_QUALIFIED_PLATFORMS` AND the loaded rhino3dm passing the parity
check on the surface offset witnesses. Malformed calls go to the export in a child process.
"""
import base64
import importlib.util
import json
import math
import os
import platform
import random
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
checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


qualified = (sys.platform, platform.machine()) in native.SURFACE_GRID_QUALIFIED_PLATFORMS


def routed(surface, us, vs):
    before = native._native_surface_grid_calls
    got = native.surface_grid(surface, us, vs)
    return got, native._native_surface_grid_calls == before + 1


def grid_params(surface, nu, nv):
    du, dv = surface.Domain(0), surface.Domain(1)
    return ([du.T0 + (du.T1 - du.T0) * i / nu for i in range(nu + 1)],
            [dv.T0 + (dv.T1 - dv.T0) * j / nv for j in range(nv + 1)])


def random_surface(rng, kind):
    if kind == "sphere":
        return r3.Sphere(r3.Point3d(rng.uniform(-5, 5), 0, 0), rng.uniform(1, 8)).ToNurbsSurface()
    if kind == "cylinder":
        return r3.Cylinder(r3.Circle(rng.uniform(1, 5)), rng.uniform(2, 9)).ToNurbsSurface()
    rational = kind == "rational"
    s = r3.NurbsSurface.Create(3, rational, 4, 3, 7, 6)
    for i in range(7):
        for j in range(6):
            w = rng.uniform(0.7, 1.3) if rational else 1.0
            s.Points[i, j] = r3.Point4d(i * 2.0 * w, j * 2.0 * w, rng.uniform(-1, 1) * w, w)
    for knots, o, c in ((s.KnotsU, 4, 7), (s.KnotsV, 3, 6)):
        a, b = rng.uniform(-9, 0), rng.uniform(1, 9)
        inner = sorted(rng.uniform(0, 1) for _ in range(c - o))
        for i, k in enumerate([0.0] * (o - 1) + inner + [1.0] * (o - 1)):
            knots[i] = a + (b - a) * k
    return s


print("\n-- the route is live where qualified --")
start = time.perf_counter()
capability = native.surface_grid_capability()
cost = time.perf_counter() - start
if qualified:
    check(capability == "qualified",
          f"{sys.platform}/{platform.machine()} qualifies (one-time parity check {cost * 1e3:.0f} ms)")
else:
    check(capability.startswith("not qualified"), f"{capability!r} on an unqualified platform")

print("\n-- random surfaces and brep faces: Rust's grid is PointAt / NormalAt's, bit for bit --")
rng = random.Random(20261127)
KINDS = ("sphere", "cylinder", "bump", "rational")
mismatches, decided = [], {"surface": 0, "face": 0}
rust_s = python_s = 0.0
for trial in range(60):
    surface = random_surface(rng, KINDS[trial % 4])
    target = r3.Brep.CreateFromSurface(surface).Faces[0] if trial % 2 else surface
    us, vs = grid_params(target, rng.randint(8, 48), rng.randint(8, 48))
    t = time.perf_counter()
    got, by_rust = routed(target, us, vs)
    rust_s += time.perf_counter() - t
    t = time.perf_counter()
    want = native._point_normal_grid(target, us, vs)
    python_s += time.perf_counter() - t
    if got is not None and got != want:
        mismatches.append(trial)
    decided["face" if trial % 2 else "surface"] += by_rust
check(not mismatches, f"60 grids (spheres with their poles, cylinders, rational and "
      f"non-rational bumps; bare and as brep faces) give PointAt's bits (mismatches: {mismatches})")
check(not qualified or decided == {"surface": 30, "face": 30},
      f"Rust evaluated every one: {decided}")
print(f"  Rust route {rust_s * 1e3:.0f} ms, PointAt/NormalAt {python_s * 1e3:.0f} ms in total")

print("\n-- a face that does not evaluate as its surface keeps PointAt --")
sphere = r3.Sphere(r3.Point3d(1, 0, 0), 3).ToNurbsSurface()
face = r3.Brep.CreateFromSurface(sphere).Faces[0]
us, vs = grid_params(face, 12, 9)
real_point, real_normal = r3.BrepFace.PointAt, r3.BrepFace.NormalAt
with patch.object(r3.BrepFace, "PointAt", lambda self, u, v: real_point(self, v, u)), \
        patch.object(r3.BrepFace, "NormalAt", lambda self, u, v: real_normal(self, v, u)):
    got, by_rust = routed(face, us, vs)
check(got is None and not by_rust, "a face read transposed from its surface is left to PointAt")
plane = r3.PlaneSurface(r3.Plane.WorldXY(), r3.Interval(0, 2), r3.Interval(0, 3))
got, by_rust = routed(plane, *grid_params(plane, 4, 4))
check(got is None and not by_rust, "a PlaneSurface (not a NURBS record) is left to PointAt")

print("\n-- bridge._build_grid: the display mesh is unchanged --")


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


def mesh(surface, us, vs):
    bm = types.SimpleNamespace(verts=Verts(), faces=Faces())
    bridge._build_grid(bm, surface, us, vs)
    ids = {id(v): k for k, v in enumerate(bm.verts.made)}
    return ([(v.co, tuple(v.normal)) for v in bm.verts.made],
            [tuple(ids[i] for i in f) for f in bm.faces.made])


for label, surface in (("sphere", sphere), ("sphere face", face),
                       ("bump", random_surface(rng, "bump"))):
    us, vs = bridge._surface_axis_params(surface, 0, bridge.CURVE_CHORD_TOL), \
        bridge._surface_axis_params(surface, 1, bridge.CURVE_CHORD_TOL)
    before = native._native_surface_grid_calls
    fast = mesh(surface, us, vs)
    used = native._native_surface_grid_calls == before + 1
    with patch.object(native, "surface_grid", lambda *args: None):
        slow = mesh(surface, us, vs)
    check(fast == slow and (used or not qualified),
          f"{label}: {len(fast[0])} vertices and {len(fast[1])} quads, the same as through PointAt")

print("\n-- qualification: the probe, the platform set, and the parity check --")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import check_pure_core as gate  # noqa: E402

library = os.environ["NARWHAL_PURE_CORE"]
try:
    identity = gate.probe_pure_core_surface_grid(gate.Path(library))
except ValueError as exc:
    identity = str(exc)
check(identity == gate.PURE_CORE_SURFACE_GRID_IDENTITY,
      "the probe gives the pinned witness grids, and the refusals their pinned statuses")
libm = next((q for q in ("/lib64/libm.so.6", "/usr/lib64/libm.so.6",
                         "/usr/lib/x86_64-linux-gnu/libm.so.6") if os.path.isfile(q)), None)
if libm:
    try:
        gate.probe_pure_core_surface_grid(gate.Path(libm))
        refused = False
    except ValueError:
        refused = True
    check(refused, "libm, which lacks the pure-core exports, is refused by the probe")
check(native.SURFACE_GRID_QUALIFIED_PLATFORMS == gate.PURE_CORE_SURFACE_GRID_QUALIFIED_PLATFORMS,
      "the runtime platform set equals the qualification record in check_pure_core")
us, vs = grid_params(sphere, 12, 9)
real_platform, real_machine = sys.platform, platform.machine
try:
    for plat, machine in (("win32", "AMD64"), ("linux", "aarch64"), ("darwin", "arm64")):
        sys.platform, platform.machine = plat, (lambda m=machine: m)
        native._surface_grid_state = None
        check(native.surface_grid_capability().startswith(f"not qualified on {plat}/{machine}")
              and native.surface_grid(sphere, us, vs) is None,
              f"{plat}/{machine} keeps PointAt")
finally:
    sys.platform, platform.machine = real_platform, real_machine
    native._surface_grid_state = None
real_grid = native._point_normal_grid


def nudged(surface, us, vs):
    points, normals = real_grid(surface, us, vs)
    x, y, z = points[1][1]
    points[1][1] = (math.nextafter(x, math.inf), y, z)
    return points, normals


native._point_normal_grid = nudged
try:
    native._surface_grid_state = None
    check("differs" in native.surface_grid_capability()
          and native.surface_grid(sphere, us, vs) is None,
          "an SDK whose PointAt rounds differently on the witnesses keeps PointAt")
finally:
    native._point_normal_grid = real_grid
    native._surface_grid_state = None
check(native.surface_grid_capability() == "qualified" or not qualified,
      "the parity check passes again with the real SDK")

print("\n-- malformed calls to the export are refused, writing nothing (child process) --")
PROBE = r"""
import ctypes as c, json, sys
fn = c.CDLL(sys.argv[1]).narwhal_pure_core_surface_grid
Z = c.c_size_t
fn.argtypes = [c.c_void_p, Z, c.c_void_p, Z, c.c_void_p, Z, c.c_void_p, c.c_void_p, Z]
fn.restype = c.c_int32
rec = bytes.fromhex(sys.argv[2])
buf = c.create_string_buffer(rec, len(rec))
us, vs = (c.c_double * 4)(0.0, 0.3, 0.6, 1.0), (c.c_double * 3)(0.0, 0.5, 1.0)
pts, nrm = (c.c_double * 64)(*[-9.0] * 64), (c.c_double * 64)(*[-9.0] * 64)
bad = (c.c_double * 3)(0.0, float("nan"), 1.0)
pd, nd = c.cast(pts, c.c_void_p), c.cast(nrm, c.c_void_p)


def call(record=buf, size=len(rec), u=us, nu=4, v=vs, nv=3, out_p=pd, out_n=nd, capacity=64):
    return fn(record, size, u, nu, v, nv, out_p, out_n, capacity)


got = {
    "a NaN parameter": call(v=bad),
    "short buffers": call(capacity=35),
    "too many nodes": call(nu=1 << 11, nv=1 << 10, capacity=64),
    "not a record": call(record=b"not a record", size=12),
    "truncated record": call(size=len(rec) - 1),
    "null record": call(record=None),
    "null parameters": call(u=None),
    "null points": call(out_p=None),
    "misaligned normals": call(out_n=c.c_void_p(c.addressof(nrm) + 1), capacity=36),
    "normals overlapping the points": call(out_n=c.c_void_p(c.addressof(pts) + 8 * 8), capacity=36),
    "points overlapping the parameters": call(out_p=c.cast(us, c.c_void_p), capacity=36),
}
untouched = list(pts) == [-9.0] * 64 and list(nrm) == [-9.0] * 64
got["a good call"] = call()
print(json.dumps({"statuses": got, "untouched": untouched,
                  "written": list(pts)[36:] == [-9.0] * 28 and -9.0 not in list(pts)[:36]}))
"""
record = base64.b64decode(sphere.Encode()["data"])
child = subprocess.run([sys.executable, "-I", "-c", PROBE, os.environ["NARWHAL_PURE_CORE"],
                        record.hex()], capture_output=True, text=True, timeout=60)
result = json.loads(child.stdout or "{}")
want = {"a NaN parameter": -2, "short buffers": -3, "too many nodes": -3, "not a record": 1,
        "truncated record": 1, "null record": -1, "null parameters": -1, "null points": -1,
        "misaligned normals": -1, "normals overlapping the points": -1,
        "points overlapping the parameters": -1, "a good call": 0}
check(child.returncode == 0 and result.get("statuses") == want and result.get("untouched")
      and result.get("written"),
      f"{len(want) - 1} malformed calls refused with their statuses, nothing written; a good "
      f"call writes exactly its nodes (exit {child.returncode}: {result} {child.stderr[-300:]})")

if qualified:
    print("\n-- cost of one display grid (best of 3) --")

    def best(fn, reps):
        out = math.inf
        for _ in range(3):
            start = time.perf_counter()
            for _ in range(reps):
                fn()
            out = min(out, (time.perf_counter() - start) / reps)
        return out

    for label, surface in (("sphere", sphere), ("sphere as a brep face", face)):
        us, vs = bridge._surface_axis_params(surface, 0, bridge.CURVE_CHORD_TOL), \
            bridge._surface_axis_params(surface, 1, bridge.CURVE_CHORD_TOL)
        slow = best(lambda: native._point_normal_grid(surface, us, vs), 1)
        fast = best(lambda: native.surface_grid(surface, us, vs), 5)
        print(f"  {label}, {len(us)} x {len(vs)} nodes: PointAt/NormalAt {slow * 1e3:5.1f} ms, "
              f"Rust {fast * 1e3:5.2f} ms ({slow / fast:.0f}x)")

passed = sum(ok for ok, _ in checks)
print(f"\n{passed}/{len(checks)} passed")
sys.exit(0 if passed == len(checks) else 1)
