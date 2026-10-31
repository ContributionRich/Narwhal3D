# Persistent command transcript — additive ABI 1

Round07, reviewed source baseline `50267dd6`. This owns **command-line text**, not
Undo, parametric history, document content revisions, selection or approval authority.
Editor ABI1/State88 and popup ABI1 layouts/exports remain unchanged.

## Runtime owner and integration

`AppState.__init__` now creates `command_transcript.Transcript()` instead of `[]`.
The only state.py edits are its import and that initialization. AppState.log is
unchanged: append first, then prune to200. Direct UI/edge-handler appends still
bypass that pruning. No UI caller is edited. Existing New/Open set_model retains
this session transcript; model/Undo logic and Enter/Space repeat are unchanged.

`command_history.CommandHistory` uses the same storage with the **separate**
standalone policy: default500, positive-int limit validation, strip ALL trailing
CR/LF (the original implementation's rstrip, not its old prose's “single”),
case-insensitive lower-substring search, detached lines(last), and atomic save.
There is no new F2 window or search UI, and no command is executed from transcript
storage/query code. File save uses the existing sibling .tmp/write/os.replace
contract. Its cleanup/error behavior is preserved, not silently redesigned.

Rust stores immutable code-point rows in an ordered VecDeque. Handles and snapshot
IDs are independent, monotonic, non-pointer and never reused. Each field has one
writable authority. Python retains bounded immutable projections, not a writable
shadow list, and never sends complete history to repaint/search/recall. Append_many
is one transaction after complete argument admission. Prefix deletion/clear release
old rows, unless copied owners still share their immutable contents.

## Facade contract

Live facade supports append(str), len/truth, integer indexing, arbitrary read slices,
iteration, prefix slice deletion, clear, equality and detached copy. Returned read
slices are ordinary lists. Iteration is a batched snapshot, deliberately not an FFI
call per line. Assignments and non-prefix deletion raise named NotImplementedError;
other list mutation methods are not advertised. append rejects nonstrings by name.

Approved additions:
- `append_many(iterable)`: realize/validate exact strings before one atomic append;
  iterator/type failure cannot publish part of a batch.
- `tail(n)`: detached last n; nonnegative exact int, zero gives empty.
- `last_command()`: last line beginning `> `, minus those two characters; None when
  absent, empty string when the last command line is exactly `> `. Never execute it.
- `search(text)` and `to_text()` are Rust query/projection operations.
- Transcript copy/deepcopy clone independent native owners; `copy()` returns a list.
  CommandHistory copy/deepcopy likewise clone storage rather than aliasing a mutable
  native handle. No new caller relies on implicit aliasing of its old private _lines.
- Explicit close and weakref finalization release owners and all retained snapshot
  tokens. Closed native facades reject use. Detached Python values remain valid.
  AppState collection releases unescaped storage; escaped Transcript references own
  their own continued lifetime. There is no New/Open teardown or new session policy.

Raw live strings retain CR/LF, embedded NUL, supplementary characters and lone
surrogates. Transport uses Python code points, not lossy UTF-8 replacement.
Unicode15.1 and16 lowercase mappings/Cased/Case_Ignorable data are generated from
those actual CPython interpreters via the existing qualified generator. The reused
final-sigma algorithm is adapted to code-point slices. This is **lower, not casefold**;
İ expansion and context-dependent Σ are covered. Both interpreter versions were
exhaustively compared in runtime substring/round-trip tests. Other Unicode versions
fail before native owner construction. String subclasses are explicitly refused before
mutation: arbitrary rstrip/lower callbacks are not supported. Integer subclasses retain
the public limit value; their integer payload is admitted to Rust, not custom arithmetic.
CommandHistory subclasses still use native storage. No Python backend/handover remains.

Python is necessary for ctypes, list protocol/coercion, GC, immutable decoding and
filesystem integration. Normalization, search, pruning, ordering and snapshot
serialization run in Rust. Original sources/tests are archived exactly under
`legacy/retired-python-50267dd6/transcript/manifest.json` (ignored/local only).
Public save tests use golden bytes/error fields; the AppState lease test uses its
measured normalized AST digest, not executable retired code or a Git dependency.

## Loader and deployment requirement

`native_transcript.library()` calls the existing `native_textfield.library()` and
binds additive transcript exports on the SAME CDLL handle. No second library slot,
shared-loader edit or production enable flag. Development override remains
`NARWHAL_UI_CORE_LIBRARY=/absolute/path/libnarwhal_ui_core.so`.

Main08 supersedes round07 optional admission: all native support is required.
Discovery belongs solely to the shared editor loader; there is no duplicate slot
probe/cache, second CDLL, compatibility list or backend switch. Missing default,
configured bad/empty paths, wrong ABI/capabilities and missing exports fail explicitly
before owner construction. An old editor/popup-only library cannot admit transcripts.

The owner authorizes atomic Linux DEV staging after strict fmt/Clippy/tests/build,
editor ABI1/State88, popup ABI1 and all seven transcript exports/ABI1 qualification;
no full-suite wait, desktop restart or release qualification is implied. Preserve
binary/source-receipt backups and inventory ALL ui_core files (including docs and
generator), not only Rust compilation inputs. Shared packaging registration is Main-owned.

A native mutation error or malformed/decode-uncertain response freezes that adapter.
No append is retried through Python. Raised FFI exceptions also freeze the adapter;
release/close exceptions or nonzero close failure statuses cannot leave a usable stale projection. If host finalizer
allocation fails after native create/clone, the newly admitted native handle is
explicitly closed before re-raising. Explicit close remains available for disposal.
Input validation performed before native entry leaves a healthy owner unchanged.
Consumers are UI-thread-confined, matching existing application usage; the native
registry mutex serializes FFI, but the facade does not promise compound Python
operations are thread-safe. No Python callback executes under that native mutex.

## C ABI and wire format

Authoritative declarations: `narwhal_ui_core.h`.
- `narwhal_transcript_abi() == 1`, `capabilities() & 7 == 7`:
  raw/normalized policies, supported Unicode tables, revision/snapshot operations.
- Request:40 bytes (op/flags u32, expected revision u64, a/b/c i64).
- Reply:56 bytes (handle, revision, len, token, count, bytes, kind: all u64).
- Struct fields are native-endian. Encoded payloads are explicitly little-endian.
- Buffers are caller-owned, valid for their declared sizes, aligned where required,
  exclusive and non-overlapping. Null/size checks cannot validate arbitrary pointers.

| op | meaning | arguments |
|---|---|---|
| 0 | create | handle/revision0; a=limit (0 unlimited), flags normalize1 / Unicode16=2 |
| 1 | append_many | framed line batch; expected current revision |
| 2 | prefix deletion | a=nonnegative count, capped to length |
| 3 | clear | expected current revision |
| 4 | immutable read-slice snapshot | normalized Python a=start,b=stop,c=step (nonzero) |
| 5 | immutable search-index snapshot | raw UTF-32LE needle, lower applied natively |
| 6 | clone | new owner ID, same immutable contents/policy/revision |
| 7 | immutable save-text snapshot | every stored line followed by LF |
| 8 | immutable last-command snapshot | zero or one prefix-stripped line |

A line batch encodes u32 count, then for each row u32 code-point count and that many
u32 values. Snapshot kind1 uses this same framing. Kind2 is count u64 indices,
strictly increasing and in-range relative to the snapshot revision. Kind3 is raw
UTF-32LE save text. Unknown operations/flags, invalid code points, malformed lengths,
trailing input and resource bounds are refused. Unused scalar slots are ignored
except create; the adapter sends zero. Mutations require a FULL fixed-size Reply;
wrong size is rejected before state mutation, never negotiated by repeating an edit.

Snapshots are immutable serialized native values pinned by token. Queries do not
change transcript revisions. Read takes owner/token/snapshot revision. Later owner
append/prune/clear does not invalidate an existing token; wrong revision or owner is
refused. Read(null,0) reports required size with status3, without writing data or
changing state. Repeated read sizing is safe/revision-bound; the normal adapter uses
the size already returned by snapshot creation and does not retry. Release verifies
owner/token; owner close releases every remaining token, including interrupted reads.

Status0 success;1 bad descriptor/argument;2 stale/closed/wrong owner or revision;
3 read output too small;4 resource bound;5 poisoned registry. Errors from native
operations do not publish partial transcript mutations. Host decode failure after a
successful mutation is uncertain, NOT a reason to repeat append. Native allocation
exhaustion can abort like other Rust allocator users; fallible container reservations
and protocol limits are not a general hostile-process isolation boundary.

OURS bounds:262,144 rows;16,777,216 code points per owner/batch; maximal wire size
`4 + 4*262144 + 4*16777216`;65,536 owners;4,096 snapshot tokens and256MiB pinned
snapshot payloads per registry. IDs/revisions refuse overflow. Oversized requests
fail visibly. Host projection cache holds at most8 entries with at most65,536 decoded
characters/indices per entry and4KiB search keys; it is invalidated after mutation.
Counters distinguish text payload bytes from fixed Request40/Reply56/scalar traffic.

## Build and focused checks (no shared registration)

Use existing disk-backed scratch, at least4GiB desktop reserve, one compiler worker.
The following private path is NOT an installed library slot:

```sh
TMPDIR=/var/tmp/nwp systemd-run --user --scope --quiet -p MemoryMax=1G -p MemorySwapMax=0 flock -w 60 /tmp/narwhal3d-heavy-build.lock cargo test --manifest-path native/ui_core/Cargo.toml --target-dir /var/tmp/nwp/transcript-target -j 1
TMPDIR=/var/tmp/nwp systemd-run --user --scope --quiet -p MemoryMax=1G -p MemorySwapMax=0 flock -w 60 /tmp/narwhal3d-heavy-build.lock cargo build --release --manifest-path native/ui_core/Cargo.toml --target-dir /var/tmp/nwp/transcript-target -j 1
```

Set `NARWHAL_UI_CORE_LIBRARY` to that target's release library for these scripts:
- `python3 -B spike/test_command_history.py` (unchanged19 controls).
- `python3 -B spike/test_ui_transcript_model.py` (also run with Blender's python3.13).
- `spike/test_ui_transcript_consumers.py`, `test_ui_transcript_benchmark.py`:
  Blender python3.13, `PYTHONPATH=vendor/occt`, OMP/OPENBLAS_NUM_THREADS=1.
- `python3 -B spike/test_ui_transcript_mutations.py`: owns compiler lock, external
  copied sources, assertion-only kills and restored controls. Never nest an outer lock.
- `spike/test_ui_transcript_stress.py`: seed70717,200 owners/6,000 mixed steps.
- `python3 -B spike/test_ui_transcript_gui.py --launch`: unchanged safe launcher,
  private profile/document, bwrap, no network, own app window, actual event_simulate,
  both GUI/build locks and2GiB/no-swap cap with4GiB desktop reserve. No VM interaction.
- Existing editor/popup/caller controls remain unmodified and run on this artifact.

`test_ui_transcript_unicode.py --generate` regenerates both tables using the existing
`native/ui_core/transcript_unicode.py` and actual15.1/16 interpreters;
`--check` compares regenerated data without writing. Format only transcript.rs after
generation. No Cargo dependency/lockfile change. Standalone native allocation probe
compiles the same transcript sources with an instrumented allocator; it is not an
installed feature or a claim that Python and native heap metrics are interchangeable.

## Measured status and limitations

Real normal-startup/event/framebuffer acceptance passed on the private Linux artifact:
27 checks,73 events, same transcript owner across commands/recall/repaints; composed
framebuffer contained196 glyph pixels for `SelAll: 0 objects selected`. An append
suppression mutant was observed RED and real restored events GREEN. This is actual
Blender event/render acceptance, not physical keyboard/monitor presentation.

Windows GNU DLL links; macOS x86_64/aarch64 compile checks pass. No cross-platform
runtime claim. Small operations are slower: roughly11us AppState.log and51–59us
combined mutation/read/prune/context cycle; hot repeated visible tails need zero FFI.
These are ownership/safety results, not an advertised speedup. Main must review the
absolute/relative costs. The owner permits focused-qualified DEV staging for startup
repair before global/public/package acceptance; the latter remain Main follow-up.
Detailed receipts, failed-attempt history, exact source/artifact manifest and remaining
Main actions are in `docs/agent_tasks/FRONTIER_RUST_UI_02_PROGRESS.md` and
`docs/reviews/RUST_UI_02_HANDOFF.md`.

License: GPL-3.0-or-later, same crate/repository. Unicode generation and lower/sigma
logic reuse the existing qualified pure_core work, not its full-state transport.
No new external dependency, geometry conversion, document authority or AI activation.
