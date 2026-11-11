# Rust interaction and exact curve-blend round

## Scope

This round adds bounded numerical engines to the existing pure-core ABI. It does
not replace OCCT/openNURBS geometry, create a second document model or history,
or alter persistence. The engines use caller-owned fixed buffers, not JSON or
per-mouse-move document serialization. Both source modules are registered in the
normal crate and their Python suites in the source gate and application harness.

### Point constraints and Ortho

`native/pure_core/src/pickconstraint.rs` implements projection, fixed points,
direction/circle constraints and incremental distance with ties-to-even rounding.
The versioned typed-event ABI validates the packet size, flags and finite data;
errors do not publish partial output. Its loader requires ABI 1 and matching
capabilities before dispatch. Missing capabilities select compatibility before
execution; native errors raise instead of replaying through Python.

Python retains prompt identity, input parsing, CPlane integration and the simple
numerical paths where an FFI crossing would cost more than the original work.
Rust handles angle+distance, circle+distance and Ortho. Reusable buffers and fixed
result caching avoid allocation and redundant computation. Cache identities
snapshot all frame axes, including mutable Z vectors.

### Exact Bezier blend control points

`native/pure_core/src/blend.rs` solves endpoint derivative constraints for every
pair of continuity orders 0–4, producing degree 1–9 Bezier control points.
openNURBS still extracts derivatives and creates the actual NURBS curve. There
is no curve sampling, mesh conversion or approximation of manufacturing geometry.

The ABI checks orders, exact lengths, alignment, overlapping buffers and finite
inputs/results before publishing output. The adapter checks the core ABI and
required export. The public route uses Rust when the sum of the two continuity
orders is at least four; lower orders retain the faster existing loop.

## Complete-adapter measurements

MEASURED on Linux, bundled CPython 3.13. These are microbenchmarks, **not** claims
about frame rate or complete BlendCrv command duration. Exact curve extraction,
construction and application redraw are outside the blend-solve timing.

| Workload | Current public call | Original Python | Approximate gain |
|---|---:|---:|---:|
| Blend Position/Position | 0.5 µs | 0.4 µs | no gain; Python route |
| Blend Tangency/Tangency | 4.5 µs | 4.4 µs | essentially unchanged; Python route |
| Blend Curvature/Curvature | 8.3 µs | 10.3 µs | 1.2× |
| Blend G4/G4 | 9.9 µs | 29.4 µs | 3.0× |
| Fixed angle+distance | 0.61 µs | 1.09 µs | 1.8× |
| Angle+increment | 1.67 µs | 2.04 µs | 1.2× |
| Circle+distance | 1.67 µs | 1.83 µs | 1.1× |
| Ortho | 1.71 µs | 2.01 µs | 1.2× |

The blend baseline preserves the original tuple-generator helpers. An optimized
test-only algebra implementation is not presented as the original performance
baseline. Simple distance/angle/circle constraints remain within measurement
noise of their original Python calls.

## Qualification and limitations

- Rust unit tests cover fixed results, rounding and FFI refusal/no-partial-write.
- Seeded differential tests cover 1,200 constraint cases and all 25 blend-order
  pairs; independent Bernstein derivative reconstruction checks exact continuity.
- Mutation evidence covers rounding, mutable frame invalidation, blend signs and
  dispatch thresholds. Restored implementations pass again.
- Windows/macOS runtime parity is not qualified by the Linux measurements.
  Compatibility remains necessary for existing libraries lacking new exports.
- This is a selective computation migration, **not** complete native ownership of
  point-prompt state or curve construction. No Python code is called redundant
  while it still serves lower-cost or unsupported-platform behavior.

Reproduce focused tests and timings through `scripts/run_suite.sh` with
`spike/test_native_pickconstraint.py` or `spike/test_native_blend.py`. Add
`--benchmark` for timings; those timings are not part of normal test assertions.

## Zoom PR integration

Zoom 1to1 uses an explicitly **assumed 96 DPI reference**, not physical monitor
calibration. The corrected orthographic scale includes the viewport lens and
sensor fit. A disposable real-Blender test measures actual projection matrices
at two lens values, both aspect orientations and millimeter/inch documents.
It also asserts unchanged encoded document and Undo/Redo state. Invalid inputs
and perspective/camera views refuse rather than claim an unavailable scale.
Dynamic zoom remains explicitly unimplemented.
