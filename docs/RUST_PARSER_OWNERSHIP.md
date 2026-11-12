# Native formula, bearing and macro-planner ownership

The current source retires three more Python execution paths. Manufacturing
geometry, file schemas, recovery and the single native Undo owner are unchanged.
Rust handles success **and rejection**; native failure never triggers Python replay.

## Formula evaluation

`calc_owned.rs` owns tokenization, precedence, functions, angle/length units,
feet/inches, exact numeric literals and rejection/error ordering. The ABI returns
native error identities and original code-point spans; `native_calc.py` only
frames UTF-32 input and renders Python repr/error strings. `calc.py` is a thin host.

Inputs are bounded to 4096 code points and 64 nested primary expressions. Results
must be finite. Existing native exports used by the coordinate parser remain for
ABI compatibility; they do not resurrect the retired Python expression engine.

## Bearings and surveyor coordinates

`bearing_owned.rs` owns Unicode decimal/DMS grammar, quadrant arithmetic, capture
spans, and classification. Case-insensitive N/S admits the documented Python
regex-compatible long-s spelling. Outer whitespace, trailing-newline anchors and
surveyor captures retain the measured contract. `bearing.py` is a thin host.
Input is bounded to 4096 code points and finite numbers.

Both owned parser adapters require bundled CPython 3.13 / Unicode 15.1 plus core
ABI1 and their specific ABI2. Missing/stale libraries or malformed replies are
installation/protocol errors, not successful-looking compatibility output.

## Checked macro string planning

`narwhal_pure_core_macro_plan_checked` returns actions or the **first semantic
refusal in token order**. A non-nestable transparent command includes its original
name; an empty option name has its own code. The string path is native at every
supported size, with no Python tokenizer/planner replay on errors.

Native admission checks caller spans/alignment and bounded output sizes. Short
buffers are not partially written. The host requires the shared response schema,
validates all lengths/statuses and permits only one bounded retry. Typed public
`NonNestableCommandError` / `MacroPlanError` behavior remains intact. Casefolded
nestable spellings such as long-s and sharp-s forms match the independent baseline.

The **distinct public `build_plan(tokens)` API remains Python**: it validates
hand-built Python token dictionaries and has no equivalent native input API yet.
It is no longer a fallback for string planning. JSON/packed ABI converters are
host data conversion, not duplicate planners. Do not delete that compatibility
API until its separate contract is replaced and tested.

## Qualification and retirement

Former production implementations are privately archived with SHA-256 receipts
under ignored `legacy/retired-rust12/`. No runtime imports the archive. Frozen
independent test-only oracles under `spike/` are not application payloads.

Qualification includes exact values/error strings, Unicode and capture edges,
domain/overflow errors, fault injection and real native call-ownership counters.
The calculator corpus contains 22,107 comparisons; bearings contain 28,190.
Native mutants (arithmetic, classification, quadrant direction and transparent
safety) must go RED and restored code GREEN. The source gate and Linux/Windows
packaging require the new exports and real semantic witnesses; Windows constant
ABI identities are also checked statically.

## Performance scope

No claim that every tiny FFI call is faster: native consolidation removes duplicate
engines at a small-call cost. Binding lookup now avoids Path construction/locking
on warm calls, retains CDLL owners across cache changes, and caches only bounded
ctypes **types**, never inputs/results. Environment transitions and concurrent cold
loads are tested. Local release-library measurements reduced bearing/surveyor
adapter calls from roughly 9 µs to 4 µs, and calculator calls from roughly 10–11 µs
to 5–6 µs. Complex formula and longer macro-plan workloads benefit more; very short
bearings still cost more than the old Python regex.

Windows cross-checks are not Windows runtime certification. No application release
version or downloadable binary is changed by this source update.
