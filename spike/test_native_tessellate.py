#!/usr/bin/env python3
"""Rust display ear clipping: identical triangles to the Python clipper, routing and timing.

Run (the library defaults to the gate's release build):
  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \
      python3 spike/test_native_tessellate.py

`narwhal3d/tessellate.py` imports only the standard library, so this runs under the
system python3 as well as the bundled runtime. Parity is exact: the same triangles,
as the same index triples, in the same order, including the partial result of a
non-simple loop that stalls.
"""
import importlib.util
import math
import os
import random
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIBRARY = os.environ.setdefault("NARWHAL_PURE_CORE", os.path.join(
    ROOT, "native", "pure_core", "target", "x86_64-unknown-linux-gnu", "release",
    "libnarwhal_pure_core.so"))
MISSING = os.path.join(ROOT, "no-such-library.so")
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


tessellate = load("tessellate")
native = load("native_tessellate")
checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


def python_ear_clip(poly):
    """The Python clipper alone: the library path is pointed at nothing."""
    os.environ["NARWHAL_PURE_CORE"] = MISSING
    try:
        return tessellate.ear_clip(poly)
    finally:
        os.environ["NARWHAL_PURE_CORE"] = LIBRARY


def polar(radii):
    n = len(radii)
    return [(r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n))
            for i, r in enumerate(radii)]


def fixtures():
    rng = random.Random(76)
    yield "triangle", [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
    yield "square CCW", [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    yield "square CW", [(0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)]
    yield "L-shape", [(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]
    yield "collinear edge points", [(0, 0), (1, 0), (2, 0), (2, 1), (2, 2), (1, 2), (0, 2), (0, 1)]
    yield "comb", [(0, 0), (9, 0), (9, 3), (8, 3), (8, 1), (7, 1), (7, 3), (6, 3), (6, 1),
                   (5, 1), (5, 3), (4, 3), (4, 1), (3, 1), (3, 3), (2, 3), (2, 1), (1, 1),
                   (1, 3), (0, 3)]
    yield "figure eight (stalls)", [(0, 0), (2, 2), (4, 0), (4, 2), (2, 0), (0, 2)]
    yield "duplicate point (stalls)", [(0, 0), (1, 0), (1, 0), (1, 1), (0, 1), (0.5, 0.5)]
    for n in (3, 4, 5, 8, 72, 360, 1024):
        yield f"circle {n}", polar([1.0] * n)
    for n in (5, 6, 7, 12, 20, 64, 100, 257):
        yield f"star {n}", polar([1.0 if i % 2 else 0.45 for i in range(n)])
    for n in (64, 256):
        yield f"gear {n}", polar([1.15 if (i // 4) % 2 else 0.85 for i in range(n)])
    for k in range(40):
        n = rng.randint(4, 300)
        poly = polar([rng.uniform(0.2, 1.0) for _ in range(n)])
        yield f"random star-shaped {k} ({n})", poly[::-1] if k % 2 else poly
    for k in range(10):
        n = rng.randint(5, 40)
        yield f"random non-simple {k} ({n})", [(rng.uniform(-1, 1), rng.uniform(-1, 1)) for _ in range(n)]


check(native.available(), f"the pure-core library loads ({LIBRARY})")

print("\n-- parity: same triangles, same order --")
mismatched, stalled, total = [], 0, 0
for name, poly in fixtures():
    poly = [tuple(map(float, p)) for p in poly]
    want = python_ear_clip(poly)
    got = native.ear_clip(poly)
    total += 1
    stalled += len(want) != len(poly) - 2
    if got != want:
        mismatched.append(name)
check(not mismatched, f"{total} loops, {stalled} of them stalled: native == Python {mismatched[:4]}")
check(stalled >= 3, "the fixture set includes loops that stall")

print("\n-- routing --")
before = native._native_calls
square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
check(tessellate.ear_clip(square) == [(0, 1, 2), (0, 2, 3)] and native._native_calls == before,
      "a short convex loop keeps the Python fan")
star = polar([1.0 if i % 2 else 0.45 for i in range(20)])
check(tessellate.ear_clip(star) == python_ear_clip(star) and native._native_calls == before + 1,
      f"a concave loop of {tessellate.NATIVE_CONCAVE_MIN_POINTS}+ points is clipped in Rust")
circle = polar([1.0] * 100)
check(tessellate.ear_clip(circle) == python_ear_clip(circle) and native._native_calls == before + 2,
      f"any loop of {tessellate.NATIVE_EAR_CLIP_MIN_POINTS}+ points is clipped in Rust")
check(tessellate.ear_clip(square[:2]) == [], "fewer than three points is still []")

print("\n-- end-to-end timing (best of 3) --")


def best(fn, reps):
    out = math.inf
    for _ in range(3):
        start = time.perf_counter()
        for _ in range(reps):
            fn()
        out = min(out, (time.perf_counter() - start) / reps)
    return out


for name, poly, reps in (("star 12", polar([1.0 if i % 2 else 0.45 for i in range(12)]), 300),
                         ("star 100", polar([1.0 if i % 2 else 0.45 for i in range(100)]), 20),
                         ("gear 256", polar([1.15 if (i // 4) % 2 else 0.85 for i in range(256)]), 5),
                         ("circle 360", polar([1.0] * 360), 100)):
    rust = best(lambda: tessellate.ear_clip(poly), reps)
    os.environ["NARWHAL_PURE_CORE"] = MISSING
    try:
        python = best(lambda: tessellate.ear_clip(poly), reps)
    finally:
        os.environ["NARWHAL_PURE_CORE"] = LIBRARY
    print(f"  [perf] {name}: native {rust * 1e6:.1f} us, python {python * 1e6:.1f} us, "
          f"ratio {rust / python:.3f}")
    check(rust / python <= 0.9, f"the routed clipper wins by at least 10% on {name}")

failed = sum(not ok for ok, _ in checks)
print(f"\n{len(checks) - failed}/{len(checks)} passed")
raise SystemExit(1 if failed else 0)
