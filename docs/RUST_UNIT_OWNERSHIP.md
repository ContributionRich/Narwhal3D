# Required native unit handling

Rust is the sole source of the24 fixed unit scales (UNIT_SYSTEM_MM) and the3
angle factors. Python retains the public27 enum names, documented abbreviation
metadata, document/model callbacks, host object coercion, custom-unit admission
and error presentation. The duplicate scale-value literal, Python unit matching
algorithm and executable conversion arithmetic are retired from the application.
The obsolete optional unit-table reader in native_pure is also retired; there is
one required checked table adapter. Originals are privately archived with hashes
under ignored legacy/retired-rust16. Independent data fixtures and Rust tests never derive expected values from native output.

## Grammar and exact arithmetic

Native parsing follows the original order: whitespace stripping, lowercase,
documented abbreviation, singular/canonical name with ASCII spaces removed,
then unique prefix. Ambiguous matches retain declaration order and exact errors.
An exhaustive Unicode15.1 probe shows Kelvin sign is the only non-ASCII character
whose lowercase maps to ASCII. The native grammar handles it explicitly and
otherwise preserves nonmatching input for exact errors, including surrogates.
The integration suite exercises every non-ASCII codepoint, not just that probe.

Conversions retain multiply-then-divide order. Custom zero scales still raise the
same ZeroDivisionError when used as divisors; signed zero, valid infinity/NaN,
float conversion and unit-error evaluation order are preserved. No new policy
silently changes physical scales or tolerances. Document geometry, recipes,
historical file formats and the single Undo owner are unchanged.

## Required transport and safety

Core ABI1, existing unit_table and the three unit_ops exports are mandatory.
Both table passes validate statuses/counts/capacities before allocations and
validate unchanged sizes, names/order and finite-positive values before use.
Parsing validates status-specific IDs, masks, raw bounds and original-byte
provenance. Angle failures leave their sentinel untouched; successful angle
factors must be finite-positive. Scale failures are typed and zero-divisor
refusals must not write output. Rust checks null/aligned/nonoverlapping spans,
lengths and capacities before reads/writes; malformed replies never replay Python.

Names are limited to4096 codepoints (OURS explicit safety bound). Native matching
requires the bundled Unicode15.1 runtime. Exact environment signatures include
empty strings, which refuse rather than falling back to the packaged library.
Immutable binding records retain CDLL owners through calls. Matching artifacts
are qualified and staged before live unit imports or isolated GUI launches.

## Qualification and scalar overhead

397 Rust tests include pointer/overlap/bounds/refusal regressions. Owned suites
cover exact errors, aliases/prefixes, all576 fixed conversions, all9 angle pairs,
custom units and coercion order. Public fault tests use in-process mocks, never
private prebuilt libraries. Source/package probes execute semantic witnesses,
including table order, Kelvin matching, ambiguity masks, operation ordering and
untouched signed-zero refusals. RED-to-restored-GREEN mutations cover these
contracts and native ownership.

Small scalar calls are slower through ctypes: measured native parse mm6.6us
versus0.14us in the Python oracle, canonical Millimeters6.9us versus1.6us,
mm-to-inches2.6us versus0.16us, angle conversion7.4us versus0.37us. This change
consolidates ownership and removes duplicate engines; it is not a speedup claim.
Bulk workloads should cross a batched native boundary rather than repeat scalar
FFI calls. No release/version/download change is included. Windows source
cross-checks are not Windows runtime certification.
