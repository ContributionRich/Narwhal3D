"""Object snaps through selection_core: the same snap as the Python pipeline, and faster.

Run under Blender (the no-library reference measures with Blender's own
`location_3d_to_region_2d`):
  NARWHAL_SELECTION_CORE=$PWD/native/selection_core/target/release/libnarwhal_selection_core.so \
      tools/blender-patched/blender -b --factory-startup --python-exit-code 1 \
      --python spike/test_osnap_native_parity.py

Drives the real `osnap.best_on_screen` with the batch hooks `ui.snap_at` passes,
once with the library loaded and once with it absent (the path names a missing
file, so every native wrapper declines and the Python pipeline runs). For END,
MID, CEN, QUAD, KNOT, POINT, INT, PERP and TAN, alone and together, in three
views and at hundreds of cursor positions, the chosen (mode, point, target,
distance) must be identical. The fixtures include exact ties within a mode (a
shared end, coincident midpoints, Perp feet and intersections on overlapping
curves), so the target order that breaks them is checked too. With the library
loaded, `osnap.candidates` must not run. VERTEX is not implemented and Near is
the nearest-point search's (spike/test_native_nearest.py); Near as a fallback
to END, INT and PERP is checked at the end of this file.
"""
import math
import os
import random
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("NARWHAL_SELECTION_CORE", os.path.join(
    ROOT, "artifacts", "selection-core", "linux-x86_64", "libnarwhal_selection_core.so"))
LIBRARY = os.environ["NARWHAL_SELECTION_CORE"]
sys.path.insert(0, ROOT)

import bpy_extras.view3d_utils as v3u  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402
import numpy as np  # noqa: E402

from narwhal3d import cplane, native_selection, osnap  # noqa: E402

checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


def perspective(eye, width=1200, height=800, fov_deg=50.0, target=(4.0, 1.0, 0.0)):
    eye, target = Vector(eye), Vector(target)
    forward = (target - eye).normalized()
    right = forward.cross(Vector((0, 0, 1)))
    right = right.normalized() if right.length > 1e-9 else Vector((1, 0, 0))
    up = right.cross(forward)
    view = Matrix(((*right, -right.dot(eye)), (*up, -up.dot(eye)),
                   (*(-forward), forward.dot(eye)), (0, 0, 0, 1)))
    f = 1.0 / math.tan(math.radians(fov_deg / 2))
    near, far = 0.5, 5000.0
    proj = Matrix(((f * height / width, 0, 0, 0), (0, f, 0, 0),
                   (0, 0, (far + near) / (near - far), 2 * far * near / (near - far)),
                   (0, 0, -1, 0)))
    return (types.SimpleNamespace(perspective_matrix=proj @ view),
            types.SimpleNamespace(width=width, height=height, x=0, y=0))


def top_ortho(width=1000, height=700, scale=0.09):
    """An orthographic Top view centred near (4, 1)."""
    aspect = height / width
    matrix = Matrix(((scale * aspect, 0, 0, -4.0 * scale * aspect),
                     (0, scale, 0, -1.0 * scale), (0, 0, -0.001, 0), (0, 0, 0, 1)))
    return (types.SimpleNamespace(perspective_matrix=matrix),
            types.SimpleNamespace(width=width, height=height, x=0, y=0))


def exact_top(size=512):
    """An orthographic Top view in which every fixture coordinate projects to an
    exact pixel (power-of-two scale), so equal distances are exactly equal and
    only the candidate order decides between them."""
    matrix = Matrix(((1 / 16, 0, 0, 0), (0, 1 / 16, 0, 0), (0, 0, -1 / 1024, 0), (0, 0, 0, 1)))
    return (types.SimpleNamespace(perspective_matrix=matrix),
            types.SimpleNamespace(width=size, height=size, x=0, y=0))


def line(a, b, n=24, **kwargs):
    points = [tuple(a[i] + (b[i] - a[i]) * k / n for i in range(3)) for k in range(n + 1)]
    return osnap.SnapTarget(points=points, vertices=(a, b), **kwargs)


def circle(center, radius, n=72, z=0.0, **kwargs):
    points = [(center[0] + radius * math.cos(2 * math.pi * k / n),
               center[1] + radius * math.sin(2 * math.pi * k / n), z) for k in range(n)]
    points.append(points[0])
    return osnap.SnapTarget(points=points, vertices=(points[0],),
                            knots=tuple(points[k * n // 4] for k in (1, 2, 3)),
                            center=(center[0], center[1], z), closed=True, **kwargs)


def fixtures():
    square = ((-4.0, -3.0, 0.0), (-1.0, -3.0, 0.0), (-1.0, 0.0, 0.0), (-4.0, 0.0, 0.0))
    wave = [(6.0 + 0.25 * k, 3.0 + math.sin(0.5 * k), 2.0) for k in range(25)]
    return (
        line((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), name="A"),
        line((10.0, 0.0, 0.0), (10.0, 8.0, 0.0), name="B"),        # END tie with A
        line((2.0, 0.0, 0.0), (8.0, 0.0, 0.0), name="C"),          # on A: MID/Perp/Int ties
        line((5.0, -3.0, 0.0), (5.0, 3.0, 0.0), name="D"),         # crosses A and C
        line((0.0, 4.0, 0.0), (10.0, -1.0, 0.0), name="E"),        # crosses A, B, D
        osnap.SnapTarget(points=square + square[:1], vertices=square + square[:1],
                         center=(-2.5, -1.5, 0.0), closed=True, name="Square"),
        circle((3.0, 4.0), 2.0, name="Circle"),
        circle((3.0, 4.0), 2.0, name="Circle2"),                   # coincident circle
        osnap.SnapTarget(points=wave, vertices=(wave[0], wave[-1]),
                         knots=tuple(wave[k] for k in (6, 12, 18)), name="Wave"),
        osnap.SnapTarget.point((0.0, 0.0, 0.0), name="P0"),
        osnap.SnapTarget.point((7.0, 6.0, 1.0), name="P1"),
        osnap.SnapTarget.point((7.0, 6.0, 1.0), name="P2"),        # POINT tie
        # Two Int points 4 px apart in the exact view, binary-exact throughout.
        line((0.0, -6.0, 0.0), (4.0, -6.0, 0.0), n=8, name="H"),
        line((1.0, -7.0, 0.0), (1.0, -5.0, 0.0), n=4, name="F"),
        line((1.5, -7.0, 0.0), (1.5, -5.0, 0.0), n=4, name="G"),
    )


def hooks(region, rv3d):
    """`ui.snap_at`'s closures, on a fake region."""
    matrix = np.asarray(rv3d.perspective_matrix, dtype=np.float64)

    def project(point):
        q = v3u.location_3d_to_region_2d(region, rv3d, Vector(point))
        return None if q is None else (q.x, q.y)

    def project_batch(points):
        return native_selection.project_display_points(
            points, matrix, region.width, region.height)

    def project_distance_batch(points, mouse, radius):
        return native_selection.projected_snap_distances(
            points, matrix, region.width, region.height, mouse, radius)

    def project_near_batch(polylines, mouse):
        return native_selection.nearest_projected_polylines(
            polylines, matrix, region.width, region.height, mouse)

    def discrete_batch(targets, modes, plane, mouse, radius):
        return native_selection.discrete_snap_survivors(
            targets, modes, plane, matrix, region.width, region.height, mouse, radius)

    return project, dict(project_batch=project_batch, project_near_batch=project_near_batch,
                         project_distance_batch=project_distance_batch,
                         discrete_batch=discrete_batch)


def unproject(region, rv3d, pixel, depth_point):
    """The world point at `pixel`, at `depth_point`'s depth: a cursor that
    projects onto `pixel`, as the snap engine only measures its projection."""
    matrix = rv3d.perspective_matrix
    clip = matrix @ Vector((*depth_point, 1.0))
    ndc = Vector((2.0 * pixel[0] / region.width - 1.0,
                  2.0 * pixel[1] / region.height - 1.0, clip.z / clip.w, 1.0))
    world = matrix.inverted() @ ndc
    return tuple(world.xyz / world.w)


def key(snap):
    if snap is None:
        return None
    return (snap.mode, snap.point, snap.target.name, snap.distance)


def use_library(on):
    os.environ["NARWHAL_SELECTION_CORE"] = LIBRARY if on else LIBRARY + ".absent"


S = osnap
STATIC = (S.END, S.MID, S.CEN, S.QUAD, S.KNOT, S.POINT)
MODE_SETS = ([(mode,) for mode in STATIC + (S.INT, S.PERP, S.TAN)]
             + [STATIC + (S.INT, S.PERP, S.TAN), (S.END, S.INT), (S.MID, S.PERP, S.TAN),
                (S.INT, S.PERP), (S.CEN, S.QUAD, S.TAN), (S.PERP, S.TAN)])
RADIUS = 10.0

targets = fixtures()
plane = cplane.WORLD_TOP
views = [("exact", *exact_top()), ("top", *top_ortho()), ("persp", *perspective((14.0, -16.0, 11.0))),
         ("low", *perspective((-9.0, 12.0, 3.0)))]
bases = (None, (3.0, 5.0, 0.0), (6.0, -2.0, 0.0), (1.0, 1.0, 0.0))

use_library(True)
check(native_selection.available(), f"selection_core loads from {LIBRARY}")

real_candidates = osnap.candidates
candidate_calls = [0]


def counted_candidates(*args, **kwargs):
    candidate_calls[0] += 1
    return real_candidates(*args, **kwargs)


osnap.candidates = counted_candidates

rng = random.Random(73)
compared = mismatched = hits = tie_hits = equidistant = 0
native_calls = fallback_calls = 0
first_mismatch = None
all_modes = STATIC + (S.INT, S.PERP, S.TAN)
# Which targets offer each (mode, point), per base: a hit on a point that two
# targets offer is an exact tie that only the target order breaks.
offered = {}
for base in bases:
    for snap in real_candidates(targets, (0.0, 0.0, 0.0), all_modes, plane, base=base):
        offered.setdefault((base, snap.mode, snap.point), set()).add(snap.target.name)
for view_name, rv3d, region in views:
    project, batch = hooks(region, rv3d)
    # Cursor positions: on a sample of the candidates (exactly and jittered within
    # the radius), plus random ones across the region.
    spots = {}
    for (base, mode, point) in offered:
        pixel = project(point)
        if pixel is not None:
            spots.setdefault((round(pixel[0], 3), round(pixel[1], 3)), (pixel, point))
    spots = sorted(spots.values())
    rng.shuffle(spots)
    cursors = []
    for pixel, depth in spots[:80]:
        cursors.append(unproject(region, rv3d, pixel, depth))
        jitter = (pixel[0] + rng.uniform(-12, 12), pixel[1] + rng.uniform(-12, 12))
        cursors.append(unproject(region, rv3d, jitter, depth))
    for _ in range(60):
        pixel = (rng.uniform(0, region.width), rng.uniform(0, region.height))
        cursors.append(unproject(region, rv3d, pixel, (4.0, 1.0, 0.0)))
    # In the exact view, halfway between two different points one mode offers:
    # both are exactly as far, so only the order breaks the tie.
    halfway = set()
    if view_name == "exact":
        by_mode = {}
        for (base, mode, point) in offered:
            by_mode.setdefault(mode, set()).add(project(point))
        for pixels in by_mode.values():
            pixels = sorted(pixels)
            for i, a in enumerate(pixels):
                for b in pixels[i + 1:]:
                    if math.dist(a, b) < 2 * RADIUS:
                        halfway.add(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
        halfway = sorted(halfway)
        rng.shuffle(halfway)
        halfway = halfway[:150]
        for pixel in halfway:
            cursors.append(unproject(region, rv3d, pixel, (0.0, 0.0, 0.0)))
        halfway = set(cursors[-len(halfway):]) if halfway else set()
    for modes in MODE_SETS:
        for base in (bases if set(modes) & {S.PERP, S.TAN} else (None,)):
            for cursor in cursors:
                use_library(False)
                before = candidate_calls[0]
                expected = osnap.best_on_screen(targets, cursor, modes, project, RADIUS,
                                                plane, base=base, **batch)
                fallback_calls += candidate_calls[0] - before
                use_library(True)
                before = candidate_calls[0]
                actual = osnap.best_on_screen(targets, cursor, modes, project, RADIUS,
                                              plane, base=base, **batch)
                native_calls += candidate_calls[0] - before
                compared += 1
                if expected is not None:
                    hits += 1
                    tie_hits += len(offered.get(
                        (base, expected.mode, expected.point), ())) > 1
                    equidistant += cursor in halfway
                if key(actual) != key(expected):
                    mismatched += 1
                    if first_mismatch is None:
                        first_mismatch = (view_name, modes, base, cursor,
                                          key(expected), key(actual))

print(f"  {compared} snaps compared ({hits} hits, {tie_hits} on a tied point, "
      f"{equidistant} halfway between two)")
check(compared > 5000 and hits > compared // 5,
      "the matrix covers every mode and view with many hits")
check(tie_hits > 100, "the fixtures produce exact ties between targets in one mode")
check(equidistant > 50, "the exact view produces exact ties between points in one mode")
check(mismatched == 0, "native and Python pipelines choose the identical snap"
      + ("" if first_mismatch is None else f": first mismatch {first_mismatch}"))
check(native_calls == 0, "with the library loaded the Python candidate pipeline never runs")
check(fallback_calls > 0, "with the library absent the Python candidate pipeline runs")

# Mid's points are `_target_mids`' (with `arc_length_midpoint`), bit for bit, on uneven
# polylines (CPython's `math.dist` and compensated `sum`, not a naive norm/sum).
use_library(True)
arc_rng = random.Random(7300)
arc_checked = arc_differ = 0
# Every point projects onto the mouse, so no candidate leaves the radius.
ON_MOUSE = np.zeros((4, 4))
ON_MOUSE[3, 3] = 1.0
for _ in range(400):
    count = arc_rng.randint(2, 40)
    scale = 10.0 ** arc_rng.uniform(-3, 4)
    points = [tuple(arc_rng.uniform(-scale, scale) for _ in range(3)) for _ in range(count)]
    target = osnap.SnapTarget(points=points, vertices=points, closed=arc_rng.random() < 0.3)
    rows = native_selection.discrete_snap_survivors(
        (target,), (S.MID,), plane, ON_MOUSE, 100, 100, (50.0, 50.0), 1.0)
    arc_checked += 1
    arc_differ += [row[0] for row in rows or ()] != list(osnap._target_mids(target))
check(arc_checked == 400 and arc_differ == 0,
      f"native Mid points are bit-identical ({arc_differ} of {arc_checked} differ)")

# Every Int/Perp/Tan reached the snap in at least one view, from the native route.
seen = set()
use_library(True)
for view_name, rv3d, region in views:
    project, batch = hooks(region, rv3d)
    for snap in real_candidates(targets, (0.0, 0.0, 0.0), all_modes, plane,
                                base=(3.0, 5.0, 0.0)):
        pixel = project(snap.point)
        if pixel is None:
            continue
        cursor = unproject(region, rv3d, pixel, snap.point)
        got = osnap.best_on_screen(targets, cursor, (snap.mode,), project, RADIUS,
                                   plane, base=(3.0, 5.0, 0.0), **batch)
        if got is not None:
            seen.add(got.mode)
check(set(all_modes) <= seen, f"each mode is chosen through the native route ({sorted(seen)})")

# A failed native part sends every mode back to the Python pipeline, unchanged.
project, batch = hooks(views[0][2], views[0][1])
cursor = unproject(views[0][2], views[0][1], project((5.0, 0.0, 0.0)), (5.0, 0.0, 0.0))
use_library(True)
reference = osnap.best_on_screen(targets, cursor, all_modes, project, RADIUS, plane,
                                 base=(3.0, 5.0, 0.0), **batch)
real_int = native_selection.intersection_points
native_selection.intersection_points = lambda *_a, **_k: None
before = candidate_calls[0]
declined = osnap.best_on_screen(targets, cursor, all_modes, project, RADIUS, plane,
                                base=(3.0, 5.0, 0.0), **batch)
native_selection.intersection_points = real_int
check(candidate_calls[0] > before and key(declined) == key(reference),
      "a declined native Int falls back to candidates with the same snap")
real_pt = native_selection.perpendicular_tangent_points
native_selection.perpendicular_tangent_points = (
    lambda *_a, **_k: (((0.0, 0.0, 0.0), 99, "Perp"),))
before = candidate_calls[0]
malformed = osnap.best_on_screen(targets, cursor, all_modes, project, RADIUS, plane,
                                 base=(3.0, 5.0, 0.0), **batch)
native_selection.perpendicular_tangent_points = real_pt
check(candidate_calls[0] > before and key(malformed) == key(reference),
      "a malformed native Perp row falls back to candidates with the same snap")
no_distance = dict(batch, project_distance_batch=None)
before = candidate_calls[0]
unscored = osnap.best_on_screen(targets, cursor, (S.INT,), project, RADIUS, plane,
                                **no_distance)
check(candidate_calls[0] > before and unscored is not None and unscored.mode == S.INT,
      "without the distance batch Int is scored by the Python pipeline")
osnap.candidates = real_candidates

# Per move: the old route with Int/Perp/Tan on (Python candidates, native scoring)
# against the fused native route, on a scene of many targets.
many = []
for k in range(400):
    x, y = (k % 20) * 1.3, (k // 20) * 1.1
    many.append(line((x, y, 0.0), (x + 1.7, y + 0.9, 0.0), n=16, name=f"L{k}"))
    if k % 4 == 0:
        many.append(circle((x + 0.5, y + 0.5), 0.4, n=48, name=f"C{k}"))
many = tuple(many)
rv3d, region = top_ortho(scale=0.06)
project, batch = hooks(region, rv3d)
old_route = dict(batch, discrete_batch=None)
moves = [unproject(region, rv3d, (rng.uniform(0, region.width),
                                  rng.uniform(0, region.height)), (0, 0, 0))
         for _ in range(60)]
modes = (S.END, S.MID, S.CEN, S.QUAD, S.INT, S.PERP, S.TAN)
use_library(True)
for hook in (old_route, batch):        # warm the pack caches
    osnap.best_on_screen(many, moves[0], modes, project, RADIUS, plane,
                         base=(1.0, 1.0, 0.0), **hook)


def per_move(hook):
    best = math.inf
    for _ in range(3):
        start = time.perf_counter()
        for cursor in moves:
            osnap.best_on_screen(many, cursor, modes, project, RADIUS, plane,
                                 base=(1.0, 1.0, 0.0), **hook)
        best = min(best, (time.perf_counter() - start) / len(moves))
    return best


old_time, new_time = per_move(old_route), per_move(batch)
same = all(key(osnap.best_on_screen(many, c, modes, project, RADIUS, plane,
                                    base=(1.0, 1.0, 0.0), **old_route))
           == key(osnap.best_on_screen(many, c, modes, project, RADIUS, plane,
                                       base=(1.0, 1.0, 0.0), **batch)) for c in moves)
print(f"  per move, {len(many)} targets: old route {old_time * 1e3:.2f} ms, "
      f"native {new_time * 1e3:.2f} ms ({old_time / new_time:.1f}x)")
check(same, "the timed scene picks the same snap on both routes")
check(new_time < old_time, "the native route is faster per move than the old route")

# Near is a FALLBACK to the discrete snaps. A discrete snap in reach is the answer,
# even where Near is closer (the Python pipeline's `pool` rule; the native route
# returns before Near is considered). With none in reach, Near answers, and it may
# use the scalar pipeline. END, INT and PERP each with NEAR, per view and cursor.
NEAR_SETS = ((S.END, S.NEAR), (S.INT, S.NEAR), (S.PERP, S.NEAR))
NEAR_BASES = (bases[1], bases[2])


def near_jitter(pixel, rng_local):
    return (pixel[0] + rng_local.uniform(-1.5 * RADIUS, 1.5 * RADIUS),
            pixel[1] + rng_local.uniform(-1.5 * RADIUS, 1.5 * RADIUS))


def near_cursors(view_index, rv3d, region):
    """Cursors with a discrete snap in reach (on one, exact and jittered) and cursors
    on the curves, where none may be in reach. Deterministic per view."""
    rng_local = random.Random(9100 + view_index)
    project, _batch = hooks(region, rv3d)
    spots = {}
    for (_base, _mode, point) in offered:
        pixel = project(point)
        if pixel is not None:
            spots.setdefault((round(pixel[0], 3), round(pixel[1], 3)), (pixel, point))
    spots = sorted(spots.values())
    rng_local.shuffle(spots)
    cursors = []
    for pixel, depth in spots[:40]:
        cursors.append(unproject(region, rv3d, pixel, depth))
        cursors.append(unproject(region, rv3d, near_jitter(pixel, rng_local), depth))
    curve = [point for target in targets if not target.is_point for point in target.points[::12]]
    rng_local.shuffle(curve)
    for point in curve[:80]:
        pixel = project(point)
        if pixel is not None:
            cursors.append(unproject(region, rv3d, pixel, point))
            cursors.append(unproject(region, rv3d, near_jitter(pixel, rng_local), point))
    return cursors


def near_differential():
    """Library-on `best_on_screen` against the library-off pipeline, for every
    NEAR set. Returns the counts and the first mismatch."""
    counts = {"cursors": 0, "discrete_in_reach": 0, "near_wins": 0, "mismatches": 0,
              "near_beats_discrete": 0, "non_near_without_discrete": 0}
    first = None
    for index, (view_name, rv3d, region) in enumerate(views):
        project, batch = hooks(region, rv3d)
        for cursor in near_cursors(index, rv3d, region):
            for modes in NEAR_SETS:
                discrete_modes = tuple(m for m in modes if m != S.NEAR)
                for base in (NEAR_BASES if S.PERP in modes else (None,)):
                    use_library(False)
                    discrete = osnap.best_on_screen(targets, cursor, discrete_modes, project,
                                                    RADIUS, plane, base=base, **batch)
                    expected = osnap.best_on_screen(targets, cursor, modes, project,
                                                    RADIUS, plane, base=base, **batch)
                    use_library(True)
                    actual = osnap.best_on_screen(targets, cursor, modes, project,
                                                  RADIUS, plane, base=base, **batch)
                    counts["cursors"] += 1
                    if discrete is not None:
                        counts["discrete_in_reach"] += 1
                        if any(s is None or s.mode == S.NEAR for s in (expected, actual)):
                            counts["near_beats_discrete"] += 1
                    elif actual is not None:
                        if actual.mode == S.NEAR:
                            counts["near_wins"] += 1
                        else:
                            counts["non_near_without_discrete"] += 1
                    if key(actual) != key(expected):
                        counts["mismatches"] += 1
                        if first is None:
                            first = (view_name, modes, base, cursor, key(expected), key(actual))
    return counts, first


use_library(True)
near_counts, near_first = near_differential()
print(f"  Near with discrete: {near_counts}")
check(near_counts["mismatches"] == 0,
      "Near with a discrete mode: native and Python pipelines choose the identical snap"
      + ("" if near_first is None else f": first mismatch {near_first}"))
check(near_counts["near_beats_discrete"] == 0 and near_counts["non_near_without_discrete"] == 0,
      "Near is a fallback: a discrete snap in reach always wins, and Near answers only without one")
check(near_counts["discrete_in_reach"] > 500,
      "the Near cases include many with a discrete snap in reach (Near must not win)")
check(near_counts["near_wins"] > 200,
      "the Near cases include many with no discrete snap in reach (Near answers)")

# The differential must be able to see a Near-priority change. Flip the Python
# pipeline's rule so Near competes with the discrete snaps, and require a difference.
saved_rule = osnap.NEAR_IS_FALLBACK
osnap.NEAR_IS_FALLBACK = False
try:
    use_library(True)
    mutant_counts, _mutant_first = near_differential()
finally:
    osnap.NEAR_IS_FALLBACK = saved_rule
print(f"  Near as a competitor (mutation): {mutant_counts}")
check(mutant_counts["mismatches"] + mutant_counts["near_beats_discrete"] > 0,
      "mutation witness: with NEAR_IS_FALLBACK=False the Near differential detects the change")
check(osnap.NEAR_IS_FALLBACK is saved_rule, "NEAR_IS_FALLBACK is restored after the mutation")

failed = [label for ok, label in checks if not ok]
print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
if failed:
    raise SystemExit(1)
