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
- ABI version 1 and exactly nine exported C symbols;
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
when the document-row source or lock set changes. Inputs whose slice byte span exceeds
`isize::MAX` are rejected before Rust creates a slice. An older ABI-1
library without an additive symbol, a load or call error, invalid offsets/input/output,
or an unexpected status rejects the native result and uses the unchanged
per-curve/Python fallback.

Default activation still requires:

1. native testing on physical Windows graphics hardware;
2. captured large-document interaction acceptance;
3. a new verified `0.4.x` AppImage and Windows package containing the matching
   manifest-bound artifacts.

Viewport line/triangle data remains display-only. It must never become saved,
measured, booleaned, or exported geometry.
