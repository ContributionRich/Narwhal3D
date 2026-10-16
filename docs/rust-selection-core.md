# Rust selection-core build and release boundary

Narwhal's first Rust component accelerates **display-only** world-to-screen
selection projection and exact crossing-rectangle refinement. It does not own or
alter document geometry, OCCT masters, construction recipes, saved NURBS, or
manufacturing output.

## Pinned build

The crate is `native/selection_core/` and pins:

- Rust `1.95.0`;
- `x86_64-unknown-linux-gnu`;
- `x86_64-pc-windows-gnu`;
- locked, offline Cargo builds;
- no third-party Rust crates;
- deterministic ELF build-ID and PE timestamp settings.

Build and prove two clean builds identical:

```text
tools/blender-patched/5.2/python/bin/python3.13 scripts/build_selection_core.py --reproducible
```

The ignored `artifacts/selection-core/` output contains:

```text
selection-core-manifest.json
linux-x86_64/libnarwhal_selection_core.so
windows-x86_64/narwhal_selection_core.dll
```

Verify an existing output independently:

```text
tools/blender-patched/5.2/python/bin/python3.13 scripts/verify_selection_core.py artifacts/selection-core
```

## Fail-closed checks

The verifier binds both files to the exact crate source aggregate and checks:

- exact filenames and artifact set;
- complete size and SHA-256 records;
- ABI version 1 and exactly sixteen exported C symbols;
- ELF64/PE32+ x86-64 machine type;
- executable Linux ABI probe when running on Linux x86-64;
- Linux dependencies and maximum `GLIBC_2.35`;
- the measured Windows system-DLL import set;
- zero Windows COFF timestamp;
- no symlinks or unexpected files.

The Windows DLL is a GNU-target C ABI library. It does not require a Python ABI and
can be loaded by the MSVC-built Blender CPython through `ctypes`. Native Windows
execution acceptance is still required before release packaging.

## Release integration and activation

The public Linux and Windows assemblers consume only a directory that passes
`verify_selection_core.py`, copy the matching platform library under
`narwhal3d/native/`, and retain its source/hash/ABI provenance in the package
manifest. The binaries remain generated artifacts rather than committed files.

The additive ABI-1 rectangle-query symbol returns a hit result plus a separate
projectable-evidence byte. That distinction lets the UI accept native misses without
creating Python screen tuples while retaining conservative selection when no complete
segment can be projected. A second additive symbol classifies many curves in one call
using validated monotonic u64 segment offsets and one ordered status byte per curve.
The UI batches only uncached app-drawn crossing candidates; non-curves, existing warm
Python entries, broad-phase order, and later group expansion retain their established
behavior. A third additive symbol performs nearest drawn-curve click picking over the
same batched offsets, preserving per-curve eye-plane clipping and later-candidate tie
ordering. The adapter accepts both shaped `(E, 2, 3)` segments and Blender's actual
flat `(2E, 3)` GL-lines records through a zero-copy reshape; scalar fallback groups the
same consecutive endpoint pairs. Two further additive symbols query cached f64 display
points for nearest-within-radius and inclusive rectangle membership. The f64 source
buffers retain document precision, while projection exactly mirrors Blender's display
path: f32 matrix/vector products, sequential f64 accumulation, f32 clip components,
f64 viewport division, and f32 pixel output. They return only stable point indices, so
control-point and survey-style point-object picking avoid one Python/Blender projection
per marker without moving analytic geometry or changing saved coordinates. Point-object
inputs are filtered for named, unlocked rows before packing, and their array is rebuilt
when the document-row source or lock set changes. A further additive export projects
packed f64 curve-overlay points into f32-exact display pixels plus one visibility byte
per point. Command hover caches world points by document/visible-object signature and
pixels by viewport matrix and dimensions. The eleventh export queries those cached
screen polylines in one call using monotonic point offsets and the existing `1e-9`
degenerate-segment rule. It returns only a stable curve index and pixel distance; names
remain in Python, while strict `<` tolerance, earlier curve/segment ties, hidden
endpoints, and ray-first surface/solid behavior remain exact. Distance uses a
source-matched port of CPython 3.13's compensated two-component `vector_norm`, rather
than platform `hypot`, so adversarial one-ULP boundaries agree on Linux and Windows
without adding `libm`. The twelfth export reduces NumPy-produced homogeneous clip rows
to grouped screen rectangles without publishing one Python tuple per box corner. The
thirteenth fuses the preceding f64 world-to-homogeneous matrix multiplication into that
same reduction, removing the remaining NumPy clip allocation on current libraries. Both
use monotonic point offsets and point-object flags, ignore nonfinite or `w <= 0` rows,
keep the first visible pixel for points, and return canonical min/max rectangles plus
validity bytes in source order. Matrix row and term order match the established NumPy
path exactly. Names, world points, candidate policy, selection rules, and the projection
cache remain in Python. Inputs whose slice byte span exceeds `isize::MAX` are rejected
before Rust creates a slice. A missing or failed fused symbol falls back to the twelfth
export over NumPy clip rows; if that also declines, the original Python tuple publication
and grouped reduction remain authoritative. Invalid inputs or malformed native outputs
fail closed at every layer. The fourteenth export projects copied f64 world AABBs to
view-keyed screen rectangles for snap culling. It preserves all eight corners, the
relative eye-plane tolerance, conservative unbounded/straddling boxes and all-behind
classification. Python retains entry identity, cache lifetime, mouse/radius policy and
the rectangle query; missing or rejected native output uses the unchanged NumPy AABB
projector. The fifteenth export projects grouped f64 display polylines and returns each
target's nearest local segment, interpolation parameter and CPython-matched pixel
distance. It accelerates only Near Osnap's coarse screen search; Python retains target
identity, reconstructs the world point and performs exact openNURBS curve refinement.
Missing or malformed output reruns every target through the scalar projector/search.
The sixteenth export fuses discrete Osnap candidate projection, CPython-matched distance
and inclusive-radius classification, returning only compact source-ordered survivor
indices/distances. Python retains candidate objects, rounded-distance ordering, mode
priority and Near fallback; older libraries use the projected-pixel path unchanged.
Near's copied f64 polyline slab is retained across mouse moves only for the immutable
production shape (tuple polylines of tuple f64 points); mutable inputs always repack,
and cache lookup/publication is lock-protected while the native call runs lock-free.
The same conservative floating interpolation envelope used by curve hover now skips
Near-polyline segment distance calls that cannot beat that target's current winner;
projection, exact boundaries, earlier ties and final openNURBS refinement are unchanged.

Default activation still requires:

1. native testing on physical Windows graphics hardware;
2. captured large-document interaction acceptance;
3. a new verified `0.4.x` AppImage and Windows package containing the matching
   manifest-bound artifacts.

Viewport line/triangle data remains display-only. It must never become saved,
measured, booleaned, or exported geometry.
