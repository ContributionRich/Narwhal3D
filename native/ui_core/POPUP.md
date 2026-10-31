# Persistent popup addition — development popup ABI 1

Owner-requested extra Rust round, after Main's selection/extrude/move regression
repairs. **Ready for Main integration review; no installed artifact, package,
public manifest, global harness, commit or release changed.** Editor ABI stays 1,
`NarwhalEditorState` stays **88 bytes**. The editor's native algorithms and the
unqualified/test-only text-layout paths are unchanged.

## What runs

Real package `shell_popup.open` calls `_make_model`, which selects `NativePopup`
when the existing UI core loader finds a valid library. This is one persistent
Rust owner, not an event-by-event reconstruction. Standalone source-loaded
`PopupModel` remains the complete pure reference. Missing default library selects
that reference **before construction/open**. Configured/installed bad libraries,
wrong popup ABI, or missing popup exports raise explicit errors, not fallback.
An older editor-only installed library therefore needs an additive rebuild by Main.
The existing loader override is `NARWHAL_UI_CORE_LIBRARY`; no second library slot,
loader dependency, package dependency, or production activation switch is added.

Rust owns popup lifetime, the ordered forest of structural rows, panel stack,
selection, geometry, scroll offsets, press matching, keyboard policy and every
release guard. Python owns copied immutable row dictionaries and opaque action
payloads (identity preserved, including RNA), action callbacks, BLF font calls,
window/context routing, and GPU drawing. Row dictionaries contain no Rust object
pointers. Host action dispatch resolves enabled/action/explanation only after the
Rust activation has closed the popup; callbacks may reopen or raise, exactly once.

Main's `if window: register()` fix is intact, and no draw algorithm changed. Native
layout uses the same **`content - body_height > 1e-6 * max(1, scale)`** tolerance.
The public `current()['menu'].panels[0].selected`, `row_rects`, title/body/scroll
properties remain usable. Held panel references are live read projections, not
stale snapshots. `selected = index` is also an explicit native edit for compatibility
with the original draw test. Structural dictionaries, offsets and layout are not
writable authorities; change the popup through open/resize/events. Views from a
closed/replaced tree are stale and must not be used to issue edits.

Dismissal retains the native owner and guards but releases host-held opaque
payloads/callbacks immediately (activation keeps only its local dispatch snapshot).
Disposal during a measurement is refused before invalidating the finalizer, so a
busy native handle cannot leak. Reopening the same model retains
its handle and guards, resetting the stack/press. A new runtime model inherits
same-window guards directly in Rust, then disposes the superseded native owner.
Changing owner windows does not inherit guards. Unregister and explicit disposal
release the handle even if a Python caller retained the model. Weakref GC is the
last-resort destructor. Handles are nonzero, monotonic, never recycled or pointers;
stale handles and read revisions are rejected. Popup/editor registries are separate.

## Exact font callback boundary

The panel algorithm requests these measurements **once, in order**:

1. Root title (or the empty string for a submenu).
2. For each direct row, its complete label string, then complete shortcut string.

The host calls the supplied `measure(text, 12 * scale)` for each request; normal
runtime passes the existing `ui._text_width` BLF callback. Rust adds label width to
shortcut width only because they are separately painted strings, as the reference
does. **It never sums glyph widths or substitutes an additive shaping model.**
Children are measured only when their panel opens; a still-open submenu is not
remeasured, and a closed/reopened submenu is. No purity/epoch promise, font cache,
algorithm restart, callback-answer replay or upfront descendant measurement is used.
The bpy-free default estimate is the existing `len(text)*size*.6` only when no
measurer was supplied; it is not used by the real UI path.

The synchronous ctypes callback is retained for the duration of the call and
catches all Python exceptions. Rust releases the registry mutex before calling the
host, marking that handle busy; recursive access to it is rejected rather than
blocked. Host recursive model operations fail explicitly. Other handles/registries
remain independent. Metric values must be finite and bounded (absolute value at
most 1e12); a failure is terminal and the original Python exception is propagated
without invoking another metric. This is not the unresolved general editor
substring-layout/cache interface: those production functions remain Python.

## Traffic and projections

- Open uploads each structural row exactly once as a **24-byte** descriptor. Labels,
  shortcut text, opaque actions, callback functions and scene data are not uploaded.
- Every mutation supplies a **64-byte Request** and receives a fixed **48-byte Reply**;
  events additionally supply at most 64 ASCII key bytes. There is no output-size
  retry after mutation and no full-row upload on mouse/keyboard/layout operations.
- `panels` reads **192-byte** native panel headers, revision checked. Immutable host
  row bindings are cached per tree/range: scalar projection does not copy the full
  row tuple each time.
- Row rectangles are pure range reads, **32 bytes per rectangle**, cached by layout
  revision/depth/range. Selection-only mouse motion reuses the exact tuple. Layout
  changes invalidate it. These are read-only projections, never replayed into Rust.
  The current Python painter still iterates its rows; no viewport/scene migration is
  claimed. Keyboard navigation may relayout and thus download a fresh projection.
- Guard projection is a pure optional read, used by diagnostics/legacy interop;
  normal same-backend runtime replacement uses native guard inheritance.

MEASURED focused traffic test: 10,000 root rows plus one lazy child upload 240,024
bytes once. Across 100 hovers, the native handle and row/rectangle tuple identities
stay unchanged, each mutation reply is 48 bytes, with **zero additional row upload
or rectangle download**.

## C ABI (authoritative declarations: `narwhal_ui_core.h`)

`narwhal_popup_abi()` returns 1. Integers/doubles use native C layout/endianness.
Caller buffers must be valid, aligned, sufficiently sized and nonaliasing. Rust
exports no pointers, allocations or ownership of buffers. Callback implementations
must not unwind through C. Genuine allocation exhaustion can abort as in the
existing editor; this is not a hostile-process isolation boundary.

`narwhal_popup_apply(handle, request, request_bytes, key, key_len, rows, row_count,
measure, output, output_bytes)` validates exact Request/Reply sizes before mutation.
Unused request fields should be zero. Input row/key buffers are borrowed only for
the duration of the call. A callback is required for open/event, since either may
create a panel. Other operations do not invoke it.

Request: `op:u32, value:u32, index:i64, x:f64, y:f64, scale:f64,
source:u64, root_count:u64, title:u64`.

| op | Meaning | Fields / payload |
|---|---|---|
| 0 | create closed owner | handle=0, x/y=window dimensions, scale |
| 1 | open/replace tree | x/y=anchor, title=has nonempty title, root_count; structural rows |
| 2 | one event | key bytes, value enum, x/y=window mouse coordinates |
| 3 | resize | x/y=window dimensions; changed size cancels a pending press |
| 4 | dismiss | clear stack/press, retain guards |
| 5 | explicit layout | no row upload/measurement |
| 6 | raw selection compatibility | value=depth, index=row or -1=None |
| 7 | inherit guards | source=other live popup handle; bounded union |
| 8 | add guard | key bytes; legacy-to-native transfer only |

Event values: NOTHING=0, PRESS=1, RELEASE=2, CLICK=3, DOUBLE_CLICK=4, other=5.
Keys are 1..64 bytes from `A-Z`, `0-9`, `_`; Rust classifies mouse, wheel, keyboard,
trackpad and housekeeping policy from those keys, not host-selected actions.

Rows are `child_start:u64, child_count:u64, separator:u64`. Direct siblings occupy
contiguous ranges; roots occupy `[0, root_count)`. No children means start=count=0.
The forest is checked for out-of-range ranges, cycles, aliases, orphans and depth.
No enabled/action field is needed for the native activation token: disabled leaves
still activate the existing host explanation, never their action.

Measurement function: `(id:i64, kind:u32, size:f64, *width:f64) -> status:u32`.
`id=-1` requests root title, `id=-2` empty submenu title, nonnegative ids select the
uploaded row; kind=0 label, kind=1 shortcut. 0 means success. Any nonzero code or
nonfinite/out-of-bound width produces status 5 and halts the operation.

Reply: `handle:u64, revision:u64, layout_revision:u64, panels:u64, action:i64,
consumed:u64`. `action=-1` means none; otherwise it is a global row id and the native
stack is already closed. `consumed` is 0/1. Revisions begin at 1, mutations advance
the operation revision, and layout passes advance the layout revision. They never
wrap. No callback/result token is emitted twice by the native model.

Pure reads take handle and exact operation revision:

- `narwhal_popup_panel`: one panel header with start/count, parent/selected (-1=None),
  rect/body/up/down, offset/max_scroll/width and scrolling (0/1).
- `narwhal_popup_rows`: copy an exact `[start, start+count)` rectangle range at depth;
  there is no mutation, sizing retry or unchecked native-sized host allocation.
- `narwhal_popup_hit`: depth=-1 outside; part=-1 border, -2 up, -3 down, else row.
- `narwhal_popup_guards`: sorted NUL-terminated key names and bytes written; a buffer
  of 33,280 bytes always suffices under the native bounds.
- `narwhal_popup_destroy(handle)`: explicit release; repeated destruction is stale.

Statuses: **0** success; **1** argument/descriptor; **2** stale handle/revision;
**3** resource/range bound; **4** busy/poisoned registry; **5** metric failure.
Argument/bound failures are checked before mutation. A measurement error may follow
partial event state changes and is terminal: **destroy, report, never replay**.
The facade disposes failed owners for status errors, invalid replies/read projections,
callback failures and FFI exceptions after possible mutation. It cannot return
`consumed=True` from such a failed operation. `is_open` then returns false and later
events raise rather than consume input without a model. A failed new runtime open
leaves the previously published drawing model intact; no half-open model is published.
Action callback exceptions are different: activation is complete and closed, so they
propagate normally, and a callback's own newly opened popup is preserved.

OURS resource limits: 65,536 rows, 64 panel levels, 512 simultaneous guard keys,
65,536 live owners. Window dimensions are positive finite <=1e12, scale is positive
finite <=1e6; anchors/mouse coordinates have absolute value <=1e12. These are named
native bounds, not silent approximations or mid-operation legacy handovers.

## Exact integration/write manifest and provenance

New:

- `native/ui_core/src/popup.rs` — persistent model/policy/layout.
- `native/ui_core/src/popup_ffi.rs` — separate registry and additive typed exports.
- `native/ui_core/src/popup_tests.rs` — Rust/ABI regressions.
- `native/ui_core/POPUP.md` — this contract and receipts.
- `narwhal3d/native_popup.py` — ctypes/metric/action/lifetime/read-view glue.
- `spike/test_ui_core_popup.py` — unchanged original tests rebound to native plus
  differential/ownership/failure/traffic/C-header checks.
- `spike/test_ui_core_popup_mutations.py` — disposable copied-source mutants.
- `spike/test_ui_core_popup_gui.py` — explicit-backend wrapper around Main's
  **unchanged** `test_selection_visibility_07.py` body.

Additive edits only: `native/ui_core/src/lib.rs` module declarations,
`native/ui_core/narwhal_ui_core.h` popup declarations, `native/ui_core/README.md`
index, `narwhal3d/shell_popup.py` model/factory/lifetime integration and module prose.
Main's pure PopupModel/Panel, fractional tolerance, runtime registration fix, draw,
window event routing and GPU/font implementation are retained. Neither `ui.py` nor
`selection_menu.py`, Main's tests, editor implementation, layout implementation,
Cargo dependencies or lockfile were edited by this round.

Policy/layout provenance: direct port of the existing working-tree
`shell_popup.PopupModel`/Panel (original model lines 94–349), including Main's
fractional-scroll fix. MEASURED palette/anchor history remains documented in that
module; submenu, keyboard and input shielding are **OURS**, not newly researched
reference-application claims. Tests import existing `test_shell_popup.py` without editing its test
bodies/assertions. No external code or dependency was introduced. License remains
GPL-3.0-or-later, same as this crate and repository. Main owns publication/notices.

## Qualification receipts (Linux, 2026-10-31)

- Rust **9 tests + doctests GREEN** (4 popup tests, 5 existing tests).
- Focused native Python **42 GREEN**, including all **24 unchanged original popup
  tests**, 900 seeded mixed-event transitions against the retained reference, lazy
  stateful/nonadditive measurements, malformed/native failure paths and C header.
- **23 new mutants RED by assertion**, restored copied Rust/Python controls GREEN:
  `/var/tmp/nwp/pm-uxwkub1v/{results.json,*-rust.log,*-python.log}`.
- Original reference suite **24 GREEN**, its **9 existing mutants RED**, restored
  GREEN. Main's original `--layout-only` check GREEN at scale .85.
- Existing editor suite **11 GREEN**; existing layout parity **1 GREEN**. Additive
  primitive C/Python checks confirm editor ABI=1/State=88 and a real native edit.
- Actual GUI native: **195 checks GREEN**, `/var/tmp/nwp/pg-oulae9qw`.
- Actual GUI fallback control: **194 checks GREEN**, `/var/tmp/nwp/pg-6lyc32qk`.
  Each executes Main's unchanged test body with object first in a fresh process,
  then vertex/edge/face acceptance/hover/cancel/release and unstuck subsequent input.
  Native has one additional editor ABI check. Seven chooser opens record native
  handles 1..7, persistent across their events, and the real BLF callback identity.
  First object chooser in both runs: rect `(352, 600.55, 522, 663.45)`, 112 text pixels,
  menu-background fraction .876923, hover accent 1.0, 96 changed candidate pixels
  outside the popup. These are composed Window POST_PIXEL framebuffer witnesses,
  **not a claim about physical monitor presentation**. PNGs, `result.json`, command,
  memory reserve and unchanged source hashes remain in each evidence directory.

Main's standalone GUI launcher strips all `NARWHAL*` overrides, so merely invoking
it with the new library environment would not qualify this artifact. The wrapper
sets the explicit library after launcher native-path discovery, runs its unmodified
body, and adds backend/handle/metric-identity checks. Legacy control chooses the
unavailable library path before open, not a replay of native operations.

Private development artifact: `/var/tmp/nwp/target/release/libnarwhal_ui_core.so`
(518,464 bytes in this build; SHA-256
`9c93ad85db61a784461aee8b1b262cd333452bf495bd4031b1d5f5594db6990e`);
no installed/shared artifact overwritten. One worker,
1 GiB/no-swap compiler scopes under the shared heavy lock; GUI 2 GiB/no swap, both
heavy/GUI locks, measured >=6 GiB available before launch (4 GiB reserve), read-only
source mounts, disposable HOME/settings and no network. An initial Rust doctest
failed with `/tmp` quota; setting **TMPDIR as well as the target directory** to
`/var/tmp/nwp` resolved it. No VM interaction or full-product suite was run.

Reproduce from repository root (create `/var/tmp/nwp` as a private directory first):

```sh
TMPDIR=/var/tmp/nwp systemd-run --user --scope --quiet -p MemoryMax=1G -p MemorySwapMax=0 flock -w 60 /tmp/narwhal3d-heavy-build.lock cargo test --manifest-path native/ui_core/Cargo.toml --target-dir /var/tmp/nwp/target -j 1
TMPDIR=/var/tmp/nwp systemd-run --user --scope --quiet -p MemoryMax=1G -p MemorySwapMax=0 flock -w 60 /tmp/narwhal3d-heavy-build.lock cargo build --release --manifest-path native/ui_core/Cargo.toml --target-dir /var/tmp/nwp/target -j 1
TMPDIR=/var/tmp/nwp NARWHAL_UI_CORE_LIBRARY=/var/tmp/nwp/target/release/libnarwhal_ui_core.so python3 -B spike/test_ui_core_popup.py
TMPDIR=/var/tmp/nwp NARWHAL_UI_CORE_LIBRARY=/var/tmp/nwp/target/release/libnarwhal_ui_core.so python3 -B spike/test_ui_core_popup_mutations.py
TMPDIR=/var/tmp/nwp NARWHAL_UI_CORE_LIBRARY=/var/tmp/nwp/target/release/libnarwhal_ui_core.so python3 -B spike/test_ui_core_popup_gui.py --launch
TMPDIR=/var/tmp/nwp NARWHAL_UI_CORE_LIBRARY=/var/tmp/nwp/target/release/libnarwhal_ui_core.so python3 -B spike/test_ui_core_popup_gui.py --launch --legacy
```

Do not put an outer lock around the mutation/GUI drivers; they take their own
locks and timeout. No cross-platform runtime qualification or packaging is claimed.
