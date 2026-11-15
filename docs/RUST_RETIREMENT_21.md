# Required native interpolation, coil planning and STL I/O

This source checkpoint retires duplicate Python computation. It does not change published 0.4.13 packages; rebuild/stage matching native libraries for source use.

## Ownership

- Interpolation parameters, basis/collocation, LU/cyclic solves, periodic curves and skinning use Rust. Public helpers and rhino3dm construction remain available through host marshalling.
- Helix/Spiral planning requires Rust. Independent original Linux/MSVC captures pin exact control points, knots and errors; geometric approximation tolerance is tested separately. Catenary remains unconverted.
- STL file encoding/decoding requires Rust. File-only data retains legacy full-f64/nonfinite grammar; geometry import rejects nonfinite/float32-overflowing vertices before welding, mesh construction or document mutation. No clamping or replacement geometry.
- Missing/stale libraries and malformed native framing raise RuntimeError, never Python replay or a geometric decline. Actual interpolation geometry refusals retain InterpolationError.
- Original engines/hashes are locally backed up under ignored legacy directories. Independent expected data and Rust regression tests stay public. Exact kernels and one Undo are unchanged.

## Qualification and tradeoffs

Strict fmt/clippy, the complete pure-core gate, semantic/capacity/atomicity probes and host boundaries passed on Linux. Actual Windows MSVC qualification passed 465 Rust tests,66 exact coil outcomes and2,465 helper captures. Windows coil answers were independently captured from original calculations. Mutations cover math, output ordering, required-native guards, framing, missing exports, nonfinite/overflow imports and failure atomicity.

Full coil construction and large STL reads/imports improve on measured workloads. Tiny calls pay FFI overhead. Interpolation beats retired Python solvers but is 6–18 percent slower than the previous optional native fast paths; small helper calls also regress. This is not a blanket speedup claim. Do not restore hidden Python replay to disguise these tradeoffs.

Catenary, vector export and DXF/OBJ remain separately unconverted. macOS is unqualified for this batch. See CONTRIBUTING.md for required parity, error, mutation, performance, platform and retirement rules.
