# Retired Python test engines: first Rust coverage transfer

Replaced Python engines belong in local ignored archives, not public executable
reference modules. This checkpoint removes the published alias and unit reference
engines and avoids publishing the newly prepared interpolation reference. Their
original files, generators and SHA256 receipts remain locally under ignored
legacy/retired-rust17. They are not runtime/test dependencies of a fresh clone.

## Rust now owns computational regression execution

- alias_golden:5364 captured grammar cases, including5000 seeded inputs, exact
  prefix/command/options spans, defaults and Unicode predicates.
- unit_golden:4437 grammar records, all1113984 non-ASCII codepoints (including
  surrogatepass inputs),6108 seeded arithmetic records, all576 fixed conversion
  pairs at nine inputs, custom scales, angles, signed zero and typed refusals.
- interpolate_golden:2013 bit-exact successful open/periodic/skinning cases and
 20 original refusals, original size/style cases and performance input fixtures.

All expected data was captured from the independent original Python algorithms,
never from the native functions under test. Lossless hex floats preserve signed
zero. Static fixtures are data, not executable copies of the algorithms. Counts,
fingerprints and actual execution evidence are mandatory source-gate checks.
The source gate refuses absent Rust integration binaries, filtered/zero test
runs, missing fixture publication and stale interpolation arithmetic.

Python remains at real integration boundaries: ctypes framing, arbitrary Python
object coercion/error text, live API dispatch, state/CRUD, and Blender/openNURBS
host APIs. The remaining tests do not contain the retired numeric/grammar engines.
They still verify production routing, counters and malformed/missing native support.

## Interpolation correctness repair

Pre-measurement found bit mismatches in126/180 open cases,61/180 periodic cases
and116/180 skins. Rust now uses compensated sums, Python-compatible libm square
operations, first-tie pivot selection and zero-factor skips. No tolerance is
widened, no analytic surface is tessellated, and Undo/save ownership is unchanged.
Exact bit qualification is Linux/glibc-specific; Windows source cross-checks do
not establish runtime libm parity. The existing production Python interpolation
path is NOT retired in this checkpoint: public helper/error APIs still need
complete native equivalents before that engine can be removed safely.

## Performance evidence and limits

The original same-host Python/native10%-win benchmarks were preserved privately
and rerun unchanged against this source; all nine passed. Inputs and measured
baselines are public fixed data. The Rust runaway guard is deliberately documented
as a separate bound, NOT a portable replacement for that relative speed criterion.
Future interpolation performance changes must repeat same-host qualification;
a green correctness gate alone is not proof of the original speedup.

## Remaining work, not hidden by language statistics

Other public Python reference engines (SVG, calculator, bearing, macro/planner,
coordinates, Join, formatting and Rebuild) still need equivalent coverage transfers.
This is the first bounded removal, not a claim that every Python test is gone.
No linguist exclusions or generated-language labels are used to hide Python.
No release/version change accompanies this source checkpoint.
