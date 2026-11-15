# Required native catenary and OBJ; obsolete STL adapters retired

These are source changes after 0.4.13, not new release assets.

- Catenary planning now requires Rust. Original Linux/MSVC numeric and host captures preserve exact control points, knots, span counts, derivatives and named valid-input refusals. Nonfinite/malformed inputs and unresolved overflow refuse explicitly instead of publishing garbage. rhino3dm NURBS construction stays host integration; no curve becomes a polyline.
- OBJ writing now requires the existing Rust serializer. Valid Latin1 o/v/f output remains byte-identical; UnicodeEncodeError and host file errors are retained. Nonfinite vertices and floating face indices refuse before an existing destination can be truncated. No unsupported UV/normal/material features are implied.
- Removed 279 lines of obsolete optional STL adapters, caches, counters and binding slots from native_pure.py. Direct Rust tests retain old ABI compatibility and format distinctions. Shared OBJ/DXF/vector integration remains intact.
- Raw STL encoders now handle every Latin1 whitespace prefix before solid correctly. 512 independent original-prefix cases cover both raw exports and round trips. The required writer already protected this route, including Unicode-name compatibility; its host encoding guard remains.

## Validation and performance

Linux strict source/native gates, independent Rust golden data, typed error/atomicity probes, file/command/geometry boundaries and mutations qualify the new owners. Actual Windows MSVC passed 477 Rust tests and final unmodified OBJ 28 host checks,84 catenary outcomes and 578 host points/derivatives. Platform-specific expected catenary values come from independent original Windows calculations; Linux captures and assertions remain unchanged. Auxiliary retirement probe 22 requires 9 exports; stable core ABI 1 is unchanged.

Measured complete catenary construction is about 3–5.6 times faster over 8–128 spans. Complete OBJ file writing measured 1.03× at 100 vertices, 1.24× at 5,000 and 1.32× at 200,000. These are same-machine workloads, not universal speed promises.

Original engines and hashes are backed up locally under ignored legacy directories. Public tests use independent data, not executable Python reference engines or private backup paths. Exact OCCT/openNURBS geometry, historical save files and one Undo remain mandatory. macOS is unqualified and no release packages were changed.

Subsequent source work retires vector/DXF serialization and Tween computation; see [encoder ownership](RUST_RETIREMENT_23.md). Unconverted behavior is never removed just to change language statistics.
