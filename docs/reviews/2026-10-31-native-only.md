# Required native owners and startup integration

## Retired application implementations

The application no longer ships alternative Python state machines for document
Undo, document metadata, text editing, popup menus or the command transcript.
Number/length parsing, including rejection classification, is also native-only.
The thirteen affected Python implementation/adapter files are 1,162 lines smaller
in total than their preserved pre-retirement originals.
Python remains for Blender/OCCT API calls, host object lifetime, file operations,
ctypes validation, drawing and exact font callbacks. Algorithms not yet replaced
by qualified Rust implementations are not mislabelled as retired.

Original implementations and reference tests were preserved with SHA-256 manifests
and restoration instructions in a local-only archive. It is outside the application
package, ignored by Git, rejected by public-source auditing, and never imported by
runtime code or public tests. Earlier Git history and released assets are unchanged.
Public comparisons use frozen expected DATA rather than executable retired models.

Missing, old or incompatible native components now produce explicit installation
errors. There is no retry through another implementation after a native failure.
Normal editor operations and sequence consumers remain supported. Pathological
out-of-range editor assignments and transcript string subclasses/custom coercions
are rejected before mutation rather than selecting a Python state machine.

## Additional Rust parsing work

The additive parser ABI2 owns grammar, Unicode decimal classification, unit
conversion and exact error classification. Original ordinary values and messages
are covered by 5,168 pre-retirement cases across Unicode15.1 and16. Simple decimal
fractions use exact integer division followed by IEEE ties-to-even rounding;
large numerators/denominators do not first become rounded floating-point operands.
Mixed numbers retain the previous staged whole-plus-fraction rounding behavior.

Explicit safety boundaries are 4,096 code points per input and finite results.
Oversized/nonfinite values return errors rather than passing infinity downstream.
An unwritten or malformed ABI reply cannot masquerade as a successful zero.
The parser introduces no dependency and leaves existing fused native interfaces
intact. Its Python wrapper does not retain regex parsing or success-only fallthrough.

Independent checks covered 6,332 exact-rational boundary cases, exhaustive Unicode
normalization, grammar/error samples, malformed ABI data and meaningful mutations.
Tiny end-to-end calls remain slower than the retired in-process Python parser:
approximately 5–7 microseconds versus 1–3 microseconds in local warm samples. This
is an ownership/consolidation change, not a claimed tiny-input speedup.

## Transcript, assistant and startup

The command transcript now has one persistent native owner, bounded immutable
projections and distinct raw/live versus normalized standalone policies. It does
not own document Undo or represent an authoritative geometry revision. Cached
reads avoid full history transfers; small append operations have measured FFI
cost and are not described as faster than Python list operations.

Assistant review gains keyboard access tied to the painted review, complete label
fit checks, explicit unverified-provider presentation and bounded display of long
replies without truncating canonical results or replay. Generic application exit
now calls contained assistant cleanup before preference/recovery work. New primitive
catalogs remain disabled; test-only admission is not a production feature flag.

A startup regression was reproduced: source code required transcript ABI while the
installed UI core lacked it. The matching editor/popup/transcript library was
qualified and installed. The source launcher now checks required native owners
before starting a welcome or modeling window, so the same mismatch cannot silently
open a default Blender interface. Real welcome-style X11 startup passed five layout
checks using installed libraries without a UI-library override: self-signalled
readiness, four viewports, command bar and both docks, without watchdog release.

## Packaging and qualification

UI packages require all18 exports: editor ABI1/State88, popup ABI1, transcript
ABI1/capabilities7/Request40/Reply56. Native parser ABI2 and semantic parse probes
are mandatory. Old binaries cannot satisfy these contracts merely by retaining
an unchanged crate-wide ABI number. Source receipts bind all required inputs.

Final qualification passed 284 registered gates (283 distinct labels) across recorded
continuation runs, not one uninterrupted run. All 3,118 frozen source files were
checked for drift, and the public-source audit passed. Transcript startup/recall
passed 27 checks, final welcome-style startup passed five checks, and both actual
OS-close cases passed: pending review and in-flight request. Cleanup closed native
handles and removed polling, detached/cancelled work, preserved preferences/recovery,
and each process exited successfully. Detachment is not a claim that remote
execution or the HTTP worker was synchronously stopped.

Focused ownership, ABI, archive-exclusion, parser and package tests include meaningful
RED/restored-GREEN mutations. After staging the qualified AI library/server pair,
separate installed CLI acceptance passed eight cases across both protocol eras,
and normal-CABI projection/replay passed three checks. Optional external icon/raster,
converter, TraceDraw and external CDR checks remain explicitly unqualified, not
counted as passes. This is source/Linux-development qualification,
not a new release or all-platform package certification. The editor-restart sampling
gap is recorded; the surviving test process was monitored rather than duplicated.

Research recovery was reviewed separately. Its submitted Sweep command was
accounted, the owned options dialog cancelled, and all ten fixture descriptions
verified. No successful Sweep output was inferred from opening that dialog.
Standing authority now covers qualified ordinary-dialog recovery without repeated
integration approval; licensing restrictions and no-blind-replay rules remain.
Private native research evidence and control tools are not published here.
