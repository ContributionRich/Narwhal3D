# Required native preview sampling, triangulation and DXF reading

These are source improvements after the published 0.4.13 packages. Existing downloads are unchanged; rebuild and stage matching native libraries before running this source.

## Ownership and compatibility

- Rust now owns the DXF reader grammar, numeric parsing and ordered entity/layer results. The Python parser is removed. Analytic LINE, CIRCLE, ARC, POLYLINE and SPLINE data remain explicit, not tessellated. Public read_dxf and FileFormatError remain. Independent pre-retirement data covers 606 outcomes (84 successes and 522 errors), including exact float bits, dictionary order, metadata and exception classes/messages.
- Rust owns planar-loop signed area and ear clipping at all supported sizes. Exact triangle ordering and partial/stalled results remain; bridge callers are unchanged. Independent original captures cover 108 loops, including 16 partial cases. This preserves existing uses; it introduces no new mesh-derived manufacturing route.
- Rust owns control-point preview sampling and selected circle, ring, rectangle, polygon, ellipse and ellipsoid display math. Arbitrary host plane callbacks and command orchestration remain host integration. Independent captures cover 978 cases and 82,035 output values, including signed zero, winding, phase and circle/ring arithmetic grouping. These are viewport polylines, never replacements for analytic document geometry.

Missing/stale libraries, invalid native replies and resource limits fail explicitly; no retired Python algorithm replays them. Originals and hashes are backed up only in locally ignored archives. Public computational tests use independent captured data and Rust engines, not executable Python reference copies.

## Native admission and platform evidence

Core ABI 1 and parser identity remain stable. Auxiliary retirement identity 24 requires 28 semantic exports; startup admission checks 32 symbols through real consumers. New boundaries validate layouts, capacities, metadata and outputs; original C exports remain. The semantic probe uses an isolated temporary script instead of an oversized command line, preserving assertions while working within Windows process limits.

Linux strict qualification passes formatting, all-target Clippy, 402 unit tests, 32 integration suites / 117 tests, 79 host suites and 179 fixture-closure paths. Actual Windows MSVC passes 516 Rust tests and the public 28-export probe, with host integration and startup-symbol checks. Independent Windows preview captures differ in 204 rows from Linux; each platform is checked against its own original answers, not a union of acceptable hashes or relaxed tolerances. Earclip captures agree across both platforms. macOS remains unqualified.

Genuine compiled and host mutations were observed RED, then restored GREEN, including triangulation order, signed-area math, B-spline blending/endpoints, polygon phase/frame, ring grouping, reader layout/numeric parsing, native admission and platform selection. Exact OCCT/openNURBS geometry, historical save support and the single Undo history are unchanged.

## Measured performance and tradeoffs

Full adapter costs are included. Representative control previews improve from 52.7 to 10.8 microseconds at four controls, and 1488.8 to 49.7 at thirty. A full ellipsoid preview improves from 153.6 to 25.0 microseconds. Ear clipping improves from 1752 to 45.4 microseconds for a 100-point star, and 8029 to 177.6 for a 256-point gear.

Tiny operations retain FFI overhead: a square triangulation rises from 1.70 to 6.25 microseconds, and a one-segment circle from 0.72 to 8.31. There is no Python size exemption.

The exact ordered DXF reader is not universally faster. A focused bulk LINE decoder reduces a 20,000-LINE file from 157.7 milliseconds in original Python to 87.5; the old optional native decoder took 53.0 but did not preserve original dictionary insertion order. Dense polylines and splines improve over Python, while layer-heavy data and tiny files can regress. Do not describe this as a universal speedup. Input parsing preserves legacy nonfinite file values; it does not certify those values as safe kernel geometry.

Net production Python decreases by 129 lines after accounting for the new ctypes marshalling adapters, rather than counting only the deleted engines. No language-statistics exclusions are added.
