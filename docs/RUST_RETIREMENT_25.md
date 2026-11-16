# Retired obsolete DXF Python adapters

This source cleanup follows the required Rust DXF reader. Published 0.4.13 packages are unchanged.

- Removed obsolete shared DXF decoder bindings, counters, thresholds, validators and packed-result parsing from native_pure.py. OBJ discovery, caching, errors and helpers remain; its private binding tuple now contains only the two OBJ operations and the library path.
- Removed the inactive packed DXF import route. Its activation threshold exceeded its own input limit, so it could not produce an import. The existing required-reader to analytic-entity construction path remains unchanged; this does not introduce a new geometry conversion or protocol.
- Preserved import assertions for entity order, layer colors, polyline closure, skipped/bulge reporting, and exact scaled rational SPLINE controls, weights and knots. A real-reader spy replaces forced activation of the retired branch.
- Added independent Rust tests for the retained legacy DXF C exports: handwritten analytic packet bytes, metadata, signed zero, capacity/no-write behavior and the original null versus nonnull empty-input distinction. Old C exports and all production Rust source remain unchanged.

Focused validation passes 64 import checks, 8 admission tests, 15 STL-boundary tests, 28 OBJ-boundary tests and 5 independent Rust legacy-ABI tests. The strict source gate includes formatting, all-target Clippy, 33 Rust integration suites and existing host suites. Real import mutations and binding/coverage omissions were observed RED and restored GREEN. Existing Linux/MSVC native qualification is retained for the unchanged Rust sources and matching binaries; this cleanup does not claim a fresh actual Windows runtime run.

Complete file-import timings, including real geometry construction, show overlapping ranges before and after cleanup for four analytic objects, 5,000 LINEs and 500 rational SPLINEs; no universal speedup is claimed. Production Python decreases by 368 lines. Removed originals remain locally ignored, not public executable reference engines. Exact OCCT/openNURBS geometry, historical project support and the single Undo history are unchanged.

For required native nearest-curve search and selection acceptance in Rust, see [the subsequent ownership update](RUST_RETIREMENT_26.md).
