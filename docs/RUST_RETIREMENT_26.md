# Required native nearest-curve search and Rust selection acceptance

These are source changes after published 0.4.13 packages; existing downloads are unchanged. Matching native libraries must be rebuilt/staged before using the required curve-search adapter.

## Computation and host boundaries

Rust now owns curve-search sampling, seed selection and Newton iteration for packed rational NURBS and original SDK-backed curves. Non-NURBS curves retain their original PointAt/DerivativeAt callbacks: no silent ToNurbsCurve conversion changes parameterization or evaluated geometry. Python retains SDK access, ctypes marshalling, analytic SDK shortcuts and exception transport. Surface-nearest algorithms are outside this retirement.

Missing/stale support, invalid replies and resource violations refuse explicitly, without replaying the retired Python search. Original SDK exceptions retain their identities. Numeric overflow in the retained analytic-distance recipe is translated to a typed input refusal rather than escaping as an unrelated OverflowError on a scalar-only platform.

The work limit counts actual weighted native evaluations. A mandatory-sample preflight rejects requests that cannot fit; subsequent evaluations consume the same fixed budget and results remain transactional. Charging every possible Newton iteration before doing any work incorrectly refused a normal 100-point Rebuild. The repaired accounting preserves that operation, measured at 0.82 seconds with all existing operation-count and under-ten-second assertions intact. A previously invalid nonuniform test curve is now valid; its former invalid SDK derivative behavior has separate explicit refusal/unchanged-geometry coverage.

Core ABI 1 and parser identity remain stable. Required nearest scalar/callback exports extend semantic retirement identity 26 to 30 exports; startup checks 34 required symbols through real consumers. Windows batch routing remains unqualified and uses required native scalar calls, not Python searching.

## Exactness and platform evidence

Existing 762-curve / 5,678-query numeric captures and 31 host groups / 3,096 queries remain covered. New independent original-engine captures include 234 full-result and callback-transcript cases. Linux fixture bytes are unchanged.

Actual Windows validation exposed a Linux-only host baseline. Independent original Windows answers match the required native Windows implementation exactly on all 3,096 queries; 176 differ from Linux answers. Windows now selects its own frozen original answers and exact hash. No union of acceptable hashes or widened tolerances is used. Raw capture hashes and CRLF-to-LF provenance are retained; callback transcripts retain meaningful empty fields under narrow whitespace metadata and exact hash guards.

Actual Windows MSVC validation passes the new callback goldens, host nearest/Rebuild/Offset checks, all 30 public semantic probes and all 34 startup symbols. Unchanged native sources and earlier passing suites are explicitly source-accounted rather than represented as rerun. macOS remains unqualified.

## Selection acceptance moved out of Python

The standalone selection acceptance suite shrinks from 75,781 bytes / 1,612 lines to 5,109 bytes / 118 lines. It now keeps actual CDLL discovery, 26 exported symbols, ABI identity and a tiny real projection smoke check. Numerical models, seeded random loops and C ABI boundary cases run in Rust instead.

Rust covers 70 static tests, 11 tests replaying 2,500 independently captured random cases and a truncation guard, plus six mandatory source/public-registration checks. Fixed inputs/answers are binary data, not generated Rust source or a public Python reference engine. Actual Windows executes 121 selection tests including its 34 existing unit tests. Producer mutations and omitted registration/capture guards were observed RED, then restored GREEN.

Selection production source/binaries are unchanged. Its new test targets are lint-clean with the existing production type_complexity warning scoped out; that pre-existing snap_clip.rs warning is not presented as newly resolved. Pure-core all-target Clippy remains strict.

## Footprint and performance accounting

The selection Python file decreases by 70,672 bytes, but integration adapters, host tests and qualification checks still cost Python bytes. The complete repository reduction is approximately 38 KB after counting those additions, not the gross file deletion. No language-statistics exclusions are added. Removed originals remain in local ignored archives only.

Prepared packed-NURBS queries and batches remain substantially faster; original SDK callback paths can be about 10–22 percent slower due to boundary overhead. No size threshold or Python fallback hides that tradeoff. Exact OCCT/openNURBS manufacturing geometry, historical saves and the single Undo history are unchanged.

## Shell right-click routing

A related UI fix claims both right-button press and release on custom shell bands after their actual controls have first refusal. Empty top-toolbar, command-bar and left-bar clicks cannot fall through to native Info menus. Existing right-dock, layer and viewport behavior remains. Blender host-region routing and its GUI probe remain Python integration; no numerical engine is added. Thirty-two Linux GUI checks pass, and a PASS_THROUGH mutation fails the modal-return witness before restored code passes. This UI delta is not represented as an actual Windows GUI run.
