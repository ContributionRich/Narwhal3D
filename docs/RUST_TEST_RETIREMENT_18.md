# Rust test retirement 18 — shared integration

## Ownership and public closure

The explicit `RUST_INTEGRATION_SUITES` in `scripts/check_pure_core.py` now covers
11 integration executables: `alias_golden`, `unit_golden`, `interpolate_golden`,
`svgpath_golden`, `bearing_golden`, `calc_golden`, `coords_golden`,
`unitformat_golden`, `macro_golden`, `joinchain_golden`, and `rebuild_golden`.
The last eight are new in this round. `RUST_INTEGRATION_FILES` and the public
`NATIVE_FILES` explicitly enumerate the complete 53-path closure (11 Rust test
files and 42 fixtures). Inventory used names and byte sizes only; integration
sources and fixture contents were not read by this integration task.

For every executable the gate requires a Cargo compiler-artifact record naming
that integration target, a test profile and executable path, plus a successful
nonzero test summary with zero failures, ignored tests, measured tests and
filtered tests. Discovery or compilation alone is insufficient. The gate checks
on-disk closure equality and public manifest inclusion using path metadata.
Focused self-tests reject missing compilation, discovery-only, zero-test and
filtered evidence for each target independently; an in-memory guard mutation
also proves the negative assertion goes RED. Public-fixture removal is likewise
mutation-tested without opening any fixture.

The public manifest drops deleted `bearing_reference.py`,
`unitformat_reference.py`, `joinchain_reference.py`, and `rebuild_reference.py`.
It retains `svgpath_reference.py`, `calc_reference.py`, `coords_reference.py`,
`macro_reference.py`, and `macro_plan_reference.py`: workers converted these to
compact static fixture decoders, not duplicate computational engines. No new
Python computational test implementation was added here.

## Mandatory semantic package qualification

MEASURED against `native/pure_core/target/release/libnarwhal_pure_core.so`:

- `!=-15` tokenizes as one option with empty name and value `15`; checked
  planning refuses it with status -5 and no payload.
- `!_Mode=x` tokenizes and plans as one `Mode=x` option, not Cancel plus option.
- `!_Box` still tokenizes as Cancel followed by the Box command.

These are now mandatory witnesses in the shared package parser probe, preserving
all existing exports, ABI identities and package evidence contracts. No new
identity marker is required. ABI-compatible old cores that synthesize Cancel
for option prefixes fail semantic qualification. Package mocks include stale
planner success, stale planner error handling, synthetic tokenizer Cancel and
missing command Cancel negatives. Each semantic guard is disabled in memory to
observe the negative assertion fail, then checked restored. Existing package
probe-bypass mutations remain mandatory on Linux, macOS and Windows.

## Coverage transferred and honest boundary scope

Rust owns 3,124 SVG strings, 28,190 bearing records, 22,107 original calculator
comparisons plus transport anchors, 20,293 original coordinate comparisons and
9,045 top-level splits, 43,791 formatting records, 6,573 macro records, 9,429 Join
records and 222 Rebuild fits. Static fixtures freeze original inputs, float bits,
metadata and error strings. Counters/fingerprints protect corpus integrity.
Expected answers were captured from the original engines, not native outputs.
Original calculator numeric-literal and coordinate calculator/bearing native
dependencies are explicitly disclosed in the fixture provenance.

Formatting's 4,124 host-only rows are ALL replayed in Python with exact context,
exception type and message; Rust never pretends to test arbitrary Python objects.
Other remaining Python checks exercise actual host/ctypes/openNURBS integration.

Two weak mutation inputs were found and fixed without widening tolerances:
Join now has exact/below/above closure-distance records, independently from its
9,000 continuation-boundary cases. Rebuild now has an independently captured
nonsingular fit with a true equal-magnitude pivot and observably different
first/last-pivot rounding. Each previously surviving mutation now goes RED.

All original engines, capture tools and mutation/benchmark receipts are backed
up locally under ignored legacy/retired-rust18. No public test imports archives.
Original relative benchmarks remain same-host offline qualification, not replaced
by fixture lookup speed or a different Rust runaway bound. This round reran them
against the matching source. No language-statistics exclusions are added.

## Remaining Python and platform qualification

This retires the nine named public reference engines, not every Python test or
all production Python. Compact fixture decoders remain in five former reference
paths. Inline independent engines in other tests (including Blend) and production
modules without complete native equivalents still need separate bounded ports.
Blender/openNURBS callbacks and ctypes/error presentation remain host code.
Exact analytic geometry, historic files and the one Undo owner are unchanged.
Windows source checks are not Windows runtime certification. No release/version
change accompanies this source update.
