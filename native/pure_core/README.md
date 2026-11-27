# Narwhal pure-core

This dependency-free Rust crate contains checked implementations for Narwhal 3D's
pure interchange and parsing modules. Optional accelerators are performance-qualified.
Owner-approved native-only retirements additionally consolidate complete computation and
rejection ownership in Rust; their small-call FFI costs are reported, never hidden behind
a duplicate Python implementation. See `docs/RUST_RETIREMENT_11.md`.

Current production routing:

| operation | production route | measured reason |
|---|---|---|
| STL encode | Rust | 1.31× faster at 100k triangles |
| OBJ decode | Rust | 1.67× faster on a 1.8 MB file |
| DXF decode | required Rust, exact ordered results and errors | 20k LINEs: 157.7 ms original Python, 87.5 ms required native; old optional native 53.0 ms but different dictionary order; layer-heavy and tiny inputs can regress |
| length parsing | Rust | about 1.14× faster |
| calculator | Rust only, including grammar errors and domain/overflow classification | exact owned literals; no Python expression replay; mixed corpus faster, tiny literals retain FFI overhead |
| coordinate/scalar/angle parsing | Rust only, including all grammar failures and top-level split | exact CPython-compatible norm, finite-result safety; CPlane callbacks/presentation stay host glue |
| Blend endpoint control-point solve | required Rust for all25 continuity pairs | exact Bernstein endpoint identities; high orders faster, low-order FFI cost intentionally retained without duplicate Python solver |
| Join endpoint chaining | Rust only (empty sets still check binding) | indexed search replaces Python quadratic scans; compensated distance preserves exact tolerance decisions |
| macro tokenize | Rust only at every supported length | Python tokenizer retired; ~259-byte probe about 1.4× faster, tiny 5-byte repeated probe about 3.6× slower (~8 µs versus ~2 µs), not a universal speedup |
| macro string plan | checked Rust at every size and semantic refusal | no tokenization/build-plan replay; separate public hand-built-token compatibility API remains |
| print-width file parsing | Rust only | short-file Python bypass and parser retired; Python retains file decoding |
| length display formatting | Rust only | decimal/fraction/feet-inch rounding, reduction, rendering and rejection in Rust; Python retains coercion/exception mapping |
| closed (periodic) curve interpolation | Rust from 3 points | 1.4× faster end to end at 4 points, 3.3× at 64 |
| Curve control-point preview sampling | required Rust at all supported sizes | full adapter: 52.7 to 10.8 us at four controls, 1488.8 to 49.7 at thirty; exact platform captures |
| planar-loop ear clipping and signed area | required Rust at all supported sizes | exact partial/order results; 100-point star 1752 to 45.4 us; tiny square 1.70 to 6.25 us |
| Near snap nearest point on a curve | Rust for a `NurbsCurve` (its analytic line/arc/circle targets included); other curves stay in Python | 8× faster on a circle, 45× on a 4-point cubic, 44× at 40 points; bit-identical point (openNURBS evaluation order) |
| uniform curve samples for previews and wireframes (`bridge.sample_curve_xyz`) | Rust for a `NurbsCurve`; other curves stay in Python | 5.7× faster at 49 samples, 11× at 217, 14× at 553 (InterpCrv preview); bit-identical to `PointAt` |
| curve display tessellation (`bridge._display_samples`) | Rust for a `NurbsCurve` on Linux/glibc only; other curves, Windows and macOS stay in Python | 11.9× faster over 300 open cubics, 5.7× on a circle, 14.8× on a 40-point zigzag; bit-identical parameters and points on Linux/glibc (libm `pow`/`atan2` as CPython calls them); unqualified elsewhere, see below |
| Rebuild least-squares fit (`rebuild._fit`) | required Rust; no Python numeric replay | 30× faster per fit (40 points, 801 samples: 21.7 → 0.73 ms); bit-identical control points |
| point at arc length (`arclength.PreparedLength.parameters_at`) | Rust for a `NurbsCurve`; other curves, refusals and the table's end branch stay in Python | 58× faster for 799 lengths on a 30-point cubic; Rebuild to 40 points 2.0 s → 0.64 s; bit-identical parameters, measured on Linux/glibc (only `+ − × ÷` and `sqrt`, no libm transcendental; Windows/macOS runtime parity unmeasured) |
| STL decode API | Python | generic triangle-tuple reshaping was 24.9% slower |
| STL Import parse+weld | Rust at ≥50 kB | fused indexed output measured 3.8–4.3× faster at 1k–100k triangles |
| OBJ encode | Python | native path was 2.18× slower |
| curve/curve intersection recursion (`intersect.curve_curve` after its overlap test) | Rust for two valid `NurbsCurve`s on a qualified platform (Linux; `check_pure_core.py` pins the witness hits); other curves, other platforms and the overlap test stay in Python | 56-97× faster (two 20-point cubics 54.9 → 0.66 ms); bit-identical hits on Linux/glibc (`Split`, `GetBoundingBox`, `Intersection.LineLine` ported from the openNURBS rhino3dm 8.35.0 pins) |
| curve/curve intersection recursion with a `PolylineCurve`, `LineCurve` or `PolyCurve` (what Polyline, Rectangle, Line and Join make; `native_intersect.point_hits`, behind Trim, Split and Intersect) | Rust when each curve is a valid `NurbsCurve`, `PolylineCurve`, `LineCurve` or `PolyCurve` of those (nested up to 8 deep), read from its `Encode()` record, on a qualified platform (Linux x86-64; `check_pure_core.py` pins the witness hits) whose loaded rhino3dm passes a one-time Split/PointAt parity check on witness pairs; `ArcCurve` pairs (and polycurves holding one), other platforms and SDK builds, and the overlap test stay in Python | Polyline × 12-point cubic recursion 442 → 1.2 ms (`curve_curve` 9 ms, the rest is the Python overlap test), Line × cubic `curve_curve` 245 → 1.3 ms, Split of a cubic by four lines 720 → 7.4 ms, 60 polycurve pairs 17.2 s → 70 ms; bit-identical hits (`ON_PolylineCurve::Evaluate`, `GetBBox`, `Split`/`Trim` with `ParameterSearch` snapping, `ON_LineCurve::Evaluate`, `GetBBox` and `Split`, and `ON_PolyCurve::Evaluate`, `GetBBox` and `Split` with `TransformParameterTo`, ported from the openNURBS rhino3dm 8.35.0 pins, no fused multiply-add) |
| closest points on a `PolylineCurve` (what Polyline and Rectangle make; `native_nearest.search` / `search_many`, behind Near, overlap tests and `nearest.PreparedCurve`) | Rust for a valid `PolylineCurve`, read from its `Encode()` record, on a qualified platform (Linux x86-64; `check_pure_core.py` pins the witness answers) whose loaded rhino3dm passes a one-time PointAt/DerivativeAt parity check (~12 ms); any refusal, other curves, platforms and SDK builds use the SDK callback search | 121 queries on a Rectangle 71 → 0.4 ms (`closest_many`, one call); bit-identical to the callback search (same Rust search; `ON_PolylineCurve::Evaluate` for the point and first derivative, no fused multiply-add) |
| NURBS curve read for the Rust adapters (`nurbs_data.arrays`) | Rust checks the whole `curve.Encode()` record (framing, CRCs, every `ON_NurbsCurve::Write` field) on a qualified platform (Linux; `check_pure_core.py` pins the witness doubles); unrecognised records, other platforms and a missing library read point by point | 9.3× faster at 67 control points (481 → 52 µs), 12.6× at 200; byte copies only, bit-identical to `Knots`/`Points` |
| LineCurve samples for snap targets, previews and wireframes (`native_nearest.sample`) | Rust for a `LineCurve` on a qualified platform (Linux x86-64; `check_pure_core.py` pins the witness samples) whose loaded rhino3dm passes a one-time `PointAt` parity check on witness lines; other curves, platforms and SDK builds call `PointAt` | 17× faster for 129 samples (827 → 48 µs), parity check ~9 ms once; bit-identical to rhino3dm's `PointAt` (openNURBS `ON_LineCurve::Evaluate` / `ON_Line::PointAt` order, no fused multiply-add) |
| PolylineCurve samples for snap targets, previews and wireframes (what Polyline and Rectangle make; `native_nearest.sample`) | Rust for a `PolylineCurve` of at most 8 points per sample, read from its `Encode()` record, on a qualified platform (Linux x86-64; `check_pure_core.py` pins the witness samples) whose loaded rhino3dm passes a one-time `PointAt` parity check on witness polylines; longer polylines (their record costs more than `PointAt` saves), other curves, platforms and SDK builds call `PointAt` | 25× faster for 129 samples on a Rectangle (828 → 34 µs; its snap target 885 → 111 µs), parity check ~6 ms once; bit-identical to rhino3dm's `PointAt` (openNURBS `ON_PolylineCurve::Evaluate`, ported in `curvex.rs`, no fused multiply-add) |
| PolyCurve samples for snap targets, previews and wireframes (what Join and Trace Picture make; `native_nearest.sample`) | Rust for a `PolyCurve` of `LineCurve`, `PolylineCurve`, `NurbsCurve` and nested `PolyCurve` segments with at most one span per 2 samples, read from its `Encode()` record, on a qualified platform (Linux x86-64; `check_pure_core.py` pins the witness samples) whose loaded rhino3dm passes a one-time `PointAt` parity check on witness polycurves; other segment classes (an `ArcCurve` from a file), more spans (their record costs more than `PointAt` saves), invalid `m_t`, other curves, platforms and SDK builds call `PointAt` | 18× faster for 129 samples of a line + polyline + cubic (849 → 48 µs; its snap target 858 → 89 µs), 2.3× at 64 cubic segments, parity check ~5 ms once; bit-identical to rhino3dm's `PointAt` (openNURBS `ON_PolyCurve::Evaluate` over the line, polyline and NURBS evaluators, no fused multiply-add) |
| free-form curve offset fit (`offset._compact_offset` through `native_nearest.offset_fit`, behind Offset and its preview) | Rust for a `NurbsCurve` of degree >= 2 read from its `Encode()` record: the smooth fit, and the boundary-preserving fit for full-multiplicity interior knots, offset cusps and kinked closed seams (`offset_boundary.rs`, with `ON_NurbsCurve::Trim` and the Sharp apex's line-line intersection in `nurbs_trim.rs`), including its refusals; on a qualified platform (Linux x86-64; `check_pure_core.py` pins the witness fits) whose loaded rhino3dm passes a one-time parity check against the Python route (~0.57 s); degenerate tangents, near-singular apexes and every other curve keep the Python route | 52x faster for a 12-point open cubic (162 -> 3.1 ms), 45x for a 14-point closed cubic (230 -> 5.1 ms), 82x for a cubic with two C0 corners (146 -> 1.8 ms), 66x for a wave through two cusps (168 -> 2.6 ms); bit-identical control points, knots and deviation |
| DXF curve approximation (`export_commands._approx_points` through `native_nearest.dxf_approx_points`, the POLYLINE Export writes for a free-form curve) | Rust for a curve whose `Encode()` record is an `ON_NurbsCurve`: the chord test at the quarter, half and three-quarter points and the halving to depth 18, with `nearest::Nurbs` as `PointAt`; on a qualified platform (Linux x86-64; `check_pure_core.py` pins the witness points) whose loaded rhino3dm passes a one-time parity check against the Python route (~0.1 s, on the first DXF export); other curves, platforms and SDK builds keep the Python route | 80-90x faster over random curves (a 20-point cubic at 1e-3: 36 -> 0.4 ms); bit-identical points |
| surface offset (`offset.surface_offset` through `native_nearest.surface_offset_fit`, behind OffsetSrf) | Rust for a `NurbsSurface` read from its `Encode()` record: the refinement rounds (`ON_NurbsSurface::InsertKnot`), the control net moved along `ON_Surface::EvNormal` (with its singular-point branch) at the Greville abscissae, the 25 x 25 check against the true offset and the fold test, returned as an archive record `CommonObject.Decode` reads; on a qualified platform (Linux x86-64; `check_pure_core.py` pins the witness surfaces) whose loaded rhino3dm passes a one-time parity check against the Python route (~0.25 s); periodic knot vectors, zero weights and every other surface keep the Python route | 36x faster for a 6x6 cubic bump at tolerance 1e-2 (137 -> 3.8 ms), 33x for a 10x10 bump (276 -> 8.3 ms), 37x for a sphere (134 -> 3.7 ms); records byte-identical to the Python route's surfaces' `Encode()` |
| closed planar curve area and centroid (`analysis.planar_curve_area_centroid` through `native_nearest.planar_area_centroid`, behind Area, AreaCentroid and the object details' Area row) | Rust for a closed planar `NurbsCurve`, `PolyCurve` (what Join makes) or `PolylineCurve` (what Rectangle and Polyline make), read from its `Encode()` record and, for the latter two, its `ToNurbsCurve()` record (`IsClosed` and `IsPlanar` stay rhino3dm queries): Newell's plane from 64 points on the curve itself, and Green's theorem at 12- and 24-point Gauss-Legendre per knot span with its error estimate, nodes and weights from libm `cos` starts as `arclength._gauss_legendre`; on a qualified platform (Linux x86-64; `check_pure_core.py` pins the witness results) whose loaded rhino3dm passes a one-time parity check against the Python route (~45 ms); other curves (arcs read from files, polycurves with such segments), platforms and SDK builds keep the Python route | 75x faster for a periodic cubic through 12 points (8.7 -> 0.12 ms), 103x through 300 points (197 -> 1.9 ms), 45x for a rectangle (3.4 -> 0.08 ms), 60x for a 40-segment joined curve (28 -> 0.47 ms); bit-identical area, centroid and errors |
| surface display grid (`bridge._build_grid` through `native_nearest.surface_grid`, the display mesh of a surface and of each curved brep face OCCT does not tessellate) | Rust for a `NurbsSurface`, or a `BrepFace` whose underlying `NurbsSurface` gives the face's own bits at an off-diagonal node and four grid nodes, read from its `Encode()` record with the surface offset's `Evaluate` / `EvNormal` ports; on a qualified platform (Linux x86-64; `check_pure_core.py` pins the witness grids) whose loaded rhino3dm passes a one-time parity check (~3 ms); other surface types and transposed faces keep `PointAt` / `NormalAt` | 12x faster for a sphere's 73 x 73 display grid (80 -> 6.8 ms, as a surface or a brep face); bit-identical points and normals |
| surface display grid lines (`bridge._surface_axis_params` through `native_nearest.surface_display_params`, where the display grid of a surface and of each curved brep face OCCT does not tessellate falls in each direction) | Rust for a `NurbsSurface`, or a `BrepFace` with its underlying `NurbsSurface`'s domains and closure that evaluates as it at two off-diagonal points, read from its `Encode()` record: the middle iso-curve's length, the knot lines, and the turn route over three iso-curves with its bisection, with `ON_NurbsSurface::IsoCurve` / `IsClosed` ported in `surface_display.rs`; on a qualified platform (Linux x86-64; `check_pure_core.py` pins the witness parameters) whose loaded rhino3dm passes a one-time parity check against the Python route (~60 ms); other surface types and transposed faces keep the Python route | 35x faster for a sphere's two directions (5.1 -> 0.15 ms), 40x for a 14x14 cubic bump (18.1 -> 0.45 ms), 16x as a brep face; bit-identical parameters |
| DXF encode | required Rust, analytic entity dictionaries retained | exact original captures; large workloads improve, tiny calls retain explicit FFI cost |
| standalone number / bearing / surveyor parsing | Rust only | grammar and rejection classification owned natively; host type/error presentation only |
| SVG path parsing | required Rust owned ABI14 | tokenizer, numeric conversion, cubic construction and error identity native-only; historical elliptical-arc cubic approximation unchanged; tiny calls retain FFI overhead |
| alias macro grammar | required Rust identity1 | prefix/command/option spans and invalid classification native-only; AliasTable state/CRUD remain host code |
| display segment counts (`screen_segments_for`) | Python | 0.5 µs per call, below one ctypes call; not ported |

**Math-library parity.** Curve display tessellation (`src/display.rs`) is bit-identical
to the Python only because both call the same platform math library. CPython's float
`x ** 2` is libm `pow(|x|, 2.0)`, not `x * x`: on glibc the two differ by one ulp in about
1 of 1500 values (1,942 of ~3M measured), so Rust calls libm `pow` too, with an opaque
exponent so the compiler cannot fold it back to `x * x`. `math.atan2` likewise reaches libm
`atan2` after CPython's special cases. Parity is measured on Linux glibc only, so
`narwhal3d/native_display.py` routes the display there only. Elsewhere it is NOT
assumed: the Windows GNU DLL under Wine (msvcrt) gave three of 586 reference display
cases different values (up to 227 ulp), and the shipped Windows CPython/UCRT and macOS
are unmeasured. There the complete Python route runs until
`spike/test_native_display_samples.py` passes against that platform's CPython runtime.
A difference would only move a display vertex or change a segment count, never
document geometry.

Optional slower Rust prototypes remain directly tested but inactive. Native-only retirement
choices above intentionally remove duplicate engines; consult their measured cost rather
than treating all Rust routes as performance wins.

Unit scales have one definition: `UNIT_SYSTEM_MM` in `src/numparse.rs` (all 24 fixed-scale
unit systems). The typed-length parser's model units read it, and `narwhal3d/units.py` builds
`UNIT_SCALE_MM` through the required `native_unit_table` adapter; no Python scale literal
or no-library fallback remains. Typed unit matching, angle factors and conversion
arithmetic are required Rust operations too; `spike/test_native_unit_table.py` and
`spike/test_unit_ops_owned.py` retain independent exact fixtures.

The CPython adapter is `narwhal3d/native_pure.py`. It loads the crate only from
the `NARWHAL_PURE_CORE` environment path or an adjacent packaged slot. Every active
optional acceleration route may retain a complete Python fallback. Required native-only
entry points do not replay Python on missing, stale or failed native support. Matching
platform libraries must be staged and qualified before live entry hooks are activated. The stable
C ABI (version 1, additive) lets Blender's CPython 3.13 and other CPython
versions load the same platform library through `ctypes`.

The crate never receives, creates, or modifies document geometry. Its inputs
are packed numeric copies of triangles already produced for export; its output
is an encoded file body. This must never replace analytic OCCT/openNURBS
geometry — the display and kernel routes are untouched.

The crate is held to a **mandatory source gate** (``scripts/source_tools.sh pure-gate``,
registered in ``run_tests.sh`` beside the welcome-window gate) that runs, in order:
``cargo fmt --check``; ``cargo clippy --all-targets -- -D warnings``;
``cargo test --offline --locked``; one Linux ``--release`` build (a temporary
build, not a committed artifact); Rust-supervised typed C-ABI probes;
the dedicated pure-core Python suites (an explicit manifest, not a
``test_native_*.py`` glob); and a Windows GNU ``cargo check`` when the pinned
target is installed. A skipped clippy step is a hard gate failure. The gate's
load-bearing self-test is ``spike/test_pure_core_gate.py``, which proves on a
temporary copied crate that a clippy ``-D warnings`` mutation fails the gate and
that a skipped clippy step fails it -- the live tree is never mutated.

Build and validate from the repository root:

```text
scripts/source_tools.sh pure-gate  # the mandatory native source gate (all steps above)
cargo +1.95.0 test --offline --manifest-path native/pure_core/Cargo.toml
cargo +1.95.0 build --release --locked --offline --manifest-path native/pure_core/Cargo.toml --target x86_64-unknown-linux-gnu
tools/blender-patched/5.2/python/bin/python3.13 spike/test_native_stl.py
tools/blender-patched/5.2/python/bin/python3.13 spike/test_native_obj.py
tools/blender-patched/5.2/python/bin/python3.13 spike/test_native_dxf.py
```

The verified output remains ignored. A later release-integration task must
explicitly own AppImage/Windows package copying, public allowlists, and
native-Windows acceptance.

The coordinate and Join ownership qualification includes 20,293 exact coordinate
comparisons and 184,015 exact CPython norm/distance witnesses. Sequential hypot
was not safe at Join thresholds: see `docs/RUST_COORDINATE_JOIN_OWNERSHIP.md`.
The compensated norm port is under the PSF license (retained source attribution
and packaged `licenses/CPython-PSF-License.txt`). No tolerance is widened.

## Required round-21 owners

STL codecs/safe mesh import, coil planning and public interpolation helpers require matching Rust support. The additive retirement probe21 checks six STL exports and interpolate_helper_v1, including errors, capacity and failure atomicity, separate from stable core ABI 1. Old libraries refuse explicitly. Exact independent Linux/MSVC captures and source/package checks are mandatory. See ../../docs/RUST_RETIREMENT_21.md for scope and performance tradeoffs.

Catenary and OBJ ownership extend the auxiliary required-retirement probe to 22 with 9 exports, including typed catenary v2 and OBJ serialization. Stable core ABI 1 does not change. Old libraries lacking v2 refuse explicitly; Linux and actual MSVC original captures qualify the new owner. See ../../docs/RUST_RETIREMENT_22.md.

Required vector/DXF encoders and Tween plans extend the auxiliary retirement probe to 23 with 22 exports; core ABI 1 and parser identity stay stable. Real-consumer startup admission requires 26 exports, including legacy solver/coil entry points. See ../../docs/RUST_RETIREMENT_23.md for exact outputs, ordinary file I/O, platform qualification and performance tradeoffs.

Required DXF reading, triangulation and selected preview sampling extend the retirement identity to 24 with 28 semantic exports and startup admission to 32 symbols. Platform-specific exact captures preserve Linux/MSVC answers. See ../../docs/RUST_RETIREMENT_24.md for scope, host boundaries and performance costs.
