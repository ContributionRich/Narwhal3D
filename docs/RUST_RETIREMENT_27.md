# UI acceptance retirement and imported-extrusion library snapshots

## Scope

The full independently captured editor acceptance corpus now runs through the
existing Rust editor FFI: 12 traces, 500 steps each, 6,000 transitions. Expected
states and results remain pre-retirement captures, not a new implementation used
as an oracle. The corpus pins its original source revision, counts, action
inventory and SHA-256; jointly truncating counts and rows is rejected.

The Python editor suite retains a 59-step captured adapter smoke exercising all
11 method routes, plus its actual ctypes, ownership, coercion, lifetime, Unicode,
projection, error and transport checks. The other original DATA sections remain
semantically unchanged. Production Python layout and its callback coverage have
**not** been retired. Popup migration is not complete; its tests remain intact.

This checkpoint migrates **test acceptance and its independent data**, not a new
production editor algorithm. Its public Rust integration test and fixtures are
mandatory source inputs for the existing UI-core source gate and public export.
No production Rust exports, native ABI or packaged release assets change.

## Library correctness

Imported openNURBS Extrusion objects do not expose Brep.Edges. Exact-proxy
validation and load hydration now expand only their temporary SDK validation
view with ToBrep(False); the document object is not replaced by that expansion.

Library copies of master-backed extrusions become independent exact snapshots
using the existing master/display-proxy and native_master-recipe mechanisms.
This avoids fragile coincident-surface Boolean re-proofs of imported history.
The exact OCCT master remains the geometry, including analytic curved faces;
a display mesh, when needed, never becomes manufacturing geometry. This affects
only the private library candidate. Source object types, groups, properties and
the working document's history remain unchanged. Insertion uses one Undo entry.

The small Python additions are unavoidable existing rhino3dm/Blender host glue
and actual-SDK boundary regression checks, not a second computational engine.
The local nine-object reported group was reproduced failing before the fix and
then saved and reinserted successfully. Private documents are not public fixtures.

## Validation evidence

- Rust editor replay: all 6,000 transitions; producer backspace mutation RED,
  restored GREEN; independent count-truncation guard.
- Python adapter mutation suite: 15 mutants RED, restored GREEN after reduction.
- Actual Blender library suite: 25 tests passed, including real thumbnails,
  frozen background capture, exact masters, groups and Undo/Redo.
- Targeted SDK-expansion, load-expansion and library-snapshot producer mutations:
  each RED, restored GREEN.
- Full checkpoint qualification and publication are recorded separately; the
  above focused results are not a claim of new Windows runtime acceptance.

## Whole-repository Python footprint

Relative to be97ef87587b6a2a63efa3e63e4292a2a8ae8403:

- Tracked Python: **13,584,311 bytes**, net **28,836 bytes removed**.
- Production Python: **1,044 bytes added** for the SDK/library correctness fix.
- The compressed Python golden DATA shrank by **35,246 bytes** after the full
  editor corpus moved to independently replayed Rust test data. Necessary host
  regression and registration changes are included in the net figure above.

This is real acceptance migration, not production-algorithm retirement or a
language-label trick. No Linguist exclusions or generated-file classifications
were added. Originals are backed up only in local ignored storage. The permanent
repository-wide target includes tests and tooling: basically zero Python, while
preserving exact geometry, historical-save compatibility and one Undo history.
