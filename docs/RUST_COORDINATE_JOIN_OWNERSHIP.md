# Native coordinate and Join ownership

Coordinate entry now delegates grammar, numeric literals, formulas, angles,
world/relative prefix policy, polar conversion, scalar parsing and top-level
splitting to Rust. The Python wrapper retains model-unit state, CPlane/last-point
callbacks and text presentation. Those are host integration, not numeric fallback
parsers. Saved exact geometry, OCCT/openNURBS masters and the unified Undo owner
are unchanged.

Join endpoint chaining uses the existing indexed Rust solver at every supported
nonempty size. The Python quadratic search and its size-based routing policy are
retired. Python only admits host input, packs arrays, maps segment indices back to
original caller keys, and exposes `Chain` objects. Empty input verifies that the
native binding exists, then returns an empty collection (the legacy packed ABI
has no zero-segment execution mode).

## A real tolerance-boundary defect, not a tolerance adjustment

The old native solver compared `dx.hypot(dy).hypot(dz)` against the document
absolute tolerance. CPython's `math.dist` uses compensated arithmetic instead;
1–2 ULP differences can change which curves join. A first test accidentally used
segments shorter than its large tolerance, so each seed was already closed and
never exercised continuation. That test was replaced, not treated as evidence.

The non-vacuous fixture uses long arms from (-10,-10,-10) to the origin and from
a random nearby endpoint to (10,10,10). Each arm is asserted longer than the
probed tolerance. Main measured 68 mismatches in 1000 such pairs under sequential
hypot. The full regression covers three signed scales and the exact distance plus
both adjacent floating-point tolerances.

`python_norm.rs` now ports CPython's compensated/scaled norm with upstream source
attribution and the PSF license. Both Join distance predicates and coordinate
`x,y<angle` projection use it. No tolerance is widened; no disconnected geometry
is silently accepted. The helper is allocation-free and handles subnormals,
finite overflowed differences, infinity/NaN ordering and positive zero.

## Qualification and safety bounds

- 184,015 exact-bit norm/distance comparisons, including exponent-wide inputs.
- 20,293 bit-exact coordinate comparisons; the former 2-ULP allowance is gone.
- Long-arm Join threshold decisions at exact/below/above distances, ordering,
  reversals, rings, duplicate keys and signed zero.
- Native ownership counters, malformed reply/span tests, and RED/restored-GREEN
  mutants for arithmetic, prefix order, error identity, uncompensated norm and
  short-input replay.

Coordinates require the bundled Unicode15.1 runtime and a specific owned ABI1;
input is bounded to 4096 code points, inherited formula nesting to 64. Nonfinite
coordinate results are explicitly refused: the old scalar float fast path cannot
smuggle NaN/inf into a placement.

Join admits finite 3D endpoints and finite nonnegative tolerance. Invalid inputs
and coordinates outside the spatial-index range are explicitly refused instead
of re-running Python. The native adapter retains its one-million-segment ceiling.
This changes the old permissive behavior for invalid tolerances and nonfinite
points, deliberately and visibly. Tiny calls may cost more through FFI; Rust-only
consolidation is not a universal speedup claim. Indexed large-chain workloads
avoid the retired quadratic scan.

## Retirement and packages

Previous engines and routing helpers are privately backed up with hashes under
ignored `legacy/retired-rust13/`. Independent test-only references remain under
`spike/`, not the application payload. No application resolves archived code.
Package/source gates require the coordinate/split/norm/Join exports and real
witnesses, including the actual Join tolerance-boundary decision. The CPython
license is required in Linux and Windows payload inventories. Matching libraries
must be qualified/staged before activating these Python entry hooks or launching
isolated GUI children.

Windows source cross-checks are not Windows runtime certification. No release
version/download was changed by this source update.
