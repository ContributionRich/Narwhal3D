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
| DXF decode | Rust | 3.23× faster on 20k entities |
| length parsing | Rust | about 1.14× faster |
| calculator | Rust only, including grammar errors and domain/overflow classification | exact owned literals; no Python expression replay; mixed corpus faster, tiny literals retain FFI overhead |
| coordinate/scalar/angle parsing | Rust only, including all grammar failures and top-level split | exact CPython-compatible norm, finite-result safety; CPlane callbacks/presentation stay host glue |
| Join endpoint chaining | Rust only (empty sets still check binding) | indexed search replaces Python quadratic scans; compensated distance preserves exact tolerance decisions |
| macro tokenize | Rust only at every supported length | Python tokenizer retired; ~259-byte probe about 1.4× faster, tiny 5-byte repeated probe about 3.6× slower (~8 µs versus ~2 µs), not a universal speedup |
| macro string plan | checked Rust at every size and semantic refusal | no tokenization/build-plan replay; separate public hand-built-token compatibility API remains |
| print-width file parsing | Rust only | short-file Python bypass and parser retired; Python retains file decoding |
| length display formatting | Rust only | decimal/fraction/feet-inch rounding, reduction, rendering and rejection in Rust; Python retains coercion/exception mapping |
| closed (periodic) curve interpolation | Rust from 3 points | 1.4× faster end to end at 4 points, 3.3× at 64 |
| Curve control-point preview sampling | Rust from 3 points | 5× faster per redraw at 4 points, 23× at 30; bit-identical samples |
| planar face ear clipping (display) | Rust for concave loops from 6 points, any loop from 64 | concave 3.6× faster at 12 points, 40× at 100; convex 1.8× at 360; identical triangles |
| Near snap nearest point on a curve | Rust for a `NurbsCurve` (its analytic line/arc/circle targets included); other curves stay in Python | 8× faster on a circle, 45× on a 4-point cubic, 44× at 40 points; bit-identical point (openNURBS evaluation order) |
| uniform curve samples for previews and wireframes (`bridge.sample_curve_xyz`) | Rust for a `NurbsCurve`; other curves stay in Python | 5.7× faster at 49 samples, 11× at 217, 14× at 553 (InterpCrv preview); bit-identical to `PointAt` |
| curve display tessellation (`bridge._display_samples`) | Rust for a `NurbsCurve` on Linux/glibc only; other curves, Windows and macOS stay in Python | 11.9× faster over 300 open cubics, 5.7× on a circle, 14.8× on a 40-point zigzag; bit-identical parameters and points on Linux/glibc (libm `pow`/`atan2` as CPython calls them); unqualified elsewhere, see below |
| Rebuild least-squares fit (`rebuild._fit`) | Rust; the Python fit is the no-library fallback | 30× faster per fit (40 points, 801 samples: 21.7 → 0.73 ms); bit-identical control points |
| point at arc length (`arclength.PreparedLength.parameters_at`) | Rust for a `NurbsCurve`; other curves, refusals and the table's end branch stay in Python | 58× faster for 799 lengths on a 30-point cubic; Rebuild to 40 points 2.0 s → 0.64 s; bit-identical parameters, measured on Linux/glibc (only `+ − × ÷` and `sqrt`, no libm transcendental; Windows/macOS runtime parity unmeasured) |
| STL decode API | Python | generic triangle-tuple reshaping was 24.9% slower |
| STL Import parse+weld | Rust at ≥50 kB | fused indexed output measured 3.8–4.3× faster at 1k–100k triangles |
| OBJ encode | Python | native path was 2.18× slower |
| curve/curve intersection recursion (`intersect.curve_curve` after its overlap test) | Rust for two valid `NurbsCurve`s on a qualified platform (Linux; `check_pure_core.py` pins the witness hits); other curves, other platforms and the overlap test stay in Python | 56-97× faster (two 20-point cubics 54.9 → 0.66 ms); bit-identical hits on Linux/glibc (`Split`, `GetBoundingBox`, `Intersection.LineLine` ported from the openNURBS rhino3dm 8.35.0 pins) |
| DXF encode | Python | two-pass output build never reached a stable 10% win |
| standalone number / bearing / surveyor parsing | Rust only | grammar and rejection classification owned natively; host type/error presentation only |
| SVG path parsing | required Rust owned ABI14 | tokenizer, numeric conversion, cubic construction and error identity native-only; historical elliptical-arc cubic approximation unchanged; tiny calls retain FFI overhead |
| alias macro grammar | required Rust identity1 | prefix/command/option spans and invalid classification native-only; AliasTable state/CRUD remain host code |
| display segment counts (`screen_segments_for`) | Python | 0.5 µs per call, below one ctypes call; not ported |
| short convex face loops (under 64 points) | Python | the linear fan beats packing (3.4× slower at 3 points) |

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
`UNIT_SCALE_MM` from it through `narwhal_pure_core_unit_table`; its Python literal is only the
no-library fallback, held identical by `spike/test_native_unit_table.py`.

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

The crate is held to a **mandatory source gate** (``scripts/check_pure_core.py``,
registered in ``run_tests.sh`` beside the welcome-window gate) that runs, in order:
``cargo fmt --check``; ``cargo clippy --all-targets -- -D warnings``;
``cargo test --offline --locked``; one Linux ``--release`` build (a temporary
build, not a committed artifact); a direct ``ctypes`` probe of the stable C ABI;
the dedicated pure-core Python suites (an explicit manifest, not a
``test_native_*.py`` glob); and a Windows GNU ``cargo check`` when the pinned
target is installed. A skipped clippy step is a hard gate failure. The gate's
load-bearing self-test is ``spike/test_pure_core_gate.py``, which proves on a
temporary copied crate that a clippy ``-D warnings`` mutation fails the gate and
that a skipped clippy step fails it -- the live tree is never mutated.

Build and validate from the repository root:

```text
scripts/check_pure_core.py          # the mandatory source gate (all steps above)
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
