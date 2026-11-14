# Rust test retirement 19 — shared integration lane

## Scope and ownership

This lane changes only `scripts/check_pure_core.py`,
`scripts/create_public_branch.py`, `spike/test_pure_core_gate.py`,
`spike/test_pure_core_packaging.py`, and this document. Production source,
exports, ABI identities and runtime behavior are unchanged by this lane.
The worker-owned Rust and host tests are integrated, not rewritten here.

The deleted `spike/test_python_norm_owned.py` is removed from the dedicated
Python test manifest, public files and the self-test's expected manifest.
This transfers coverage; it does not delete the norm contract. Rust replays
184,015 original exact norm/distance cases (NaNs retain their original is-NaN
contract), 9,000 threshold decisions over 3,000 full-length arms, and invalid
metadata/no-write cases. The required `narwhal_pure_core_norm_v1` export and
existing exact package witness remain mandatory. The package self-test now
also disables that witness guard in memory and observes its negative test fail.

Blend's pure computational proof now lives in Rust: independent Bernstein
verification across all 25 continuity pairs and all 623 fixed original records.
The Python host test still replays all 623 records through the wrapper/ctypes,
checks transport/error handling, and checks five live openNURBS curves. This is
not full Python removal. No new Python computational engine is introduced;
Python changes here are gate orchestration and package-test integration.

The correct ignored private Blend archive is `legacy/retired-rust19/blend/`,
not a private-legacy path. Public tests must not depend on this archive.

## Explicit public closure

`blend_golden` and `norm_golden` join the 11 existing required integration
targets. The closure is **63 paths = 13 Rust integration sources + 50 fixtures**:
53 existing paths plus two sources and eight fixtures. Inventory uses path
names and byte sizes only; no fixture contents were opened in this lane.

New paths under `native/pure_core/tests/` (measured byte sizes):

| Path | Bytes |
| --- | ---: |
| `blend_golden.rs` | 4962 |
| `norm_golden.rs` | 18084 |
| `fixtures/blend_numeric.txt` | 606730 |
| `fixtures/blend_curves.txt` | 3015 |
| `fixtures/blend_provenance.txt` | 4947 |
| `fixtures/norm_rows_2.txt` | 3077734 |
| `fixtures/norm_rows_3.txt` | 4103480 |
| `fixtures/norm_rows_6.txt` | 7537718 |
| `fixtures/norm_thresholds.txt` | 897261 |
| `fixtures/norm_witness.txt` | 519 |

Each required target must provide its own Cargo compiler-artifact record with
test profile and executable, and a nonzero passing harness summary with zero
failed, ignored, measured or filtered tests. Focused self-tests independently
reject missing compilation, discovery-only, wrong-target, zero-test, filtered
and failing evidence for every target. Disabling the evidence guard in an
in-memory AST makes the negative assertion go RED. Each of the ten new public
paths is independently removed from an in-memory manifest and rejected; the
existing closure-guard bypass mutation also remains tested. No live source is
mutated for these checks.

## Production-unused resolver cleanup

The obsolete optional `_native_macro_plan` resolver is archived and removed from
production macro_plan.py. Its only remaining caller was a COMPAT test, which now
uses the tokenizer's already-loaded native_pure module directly. The active
checked planner, separate hand-built-token API and compatibility converters are
unchanged. Missing native support never activates the deleted resolver.

## Qualification

Pinned Rust formatting and Clippy are enforced for all targets. The focused
native source gate includes all402 unit tests and every required integration
executable; Python host/policy/package regressions remain mandatory. Worker lint
findings were corrected without changing arithmetic ordering or test tolerances.

No relative-performance claim is made: fixture replay speed is not a replacement
benchmark. Original report-only Blend benchmarks were rerun privately against
unchanged source. Windows source checks are not runtime certification. Public
language statistics are not hidden or relabeled, and no release/version changes
are included. Main records actual source/whole-gate results in the checkpoint
receipt rather than treating focused worker results as deployed certification.
