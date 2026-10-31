# Narwhal application core — document metadata, ABI 1

This crate owns persistent, process-local **document user-text state**, not a
stateless optional accelerator. Its registry survives individual calls. Application
adapters integrate this core with document Undo/Redo, persistence and runtime
lifecycle. Persistence across process exit remains the adapter's save/load job;
geometry ownership is outside this component's scope.

- Crate: `narwhal-application-core`; library: `narwhal_application_core`.
- Rust 1.88+, edition 2024; `cdylib` + `rlib`; serde 1.0.229 / serde_json 1.0.154,
  matching the existing automation-core lockfile versions.
- C header: `include/narwhal_application_core.h`.
- Production Rust: `src/{lib,session,table,wire,ordinal_map}.rs`.
- No metadata-specific undo stack. Snapshots are handles for the existing unified
  document Undo/Redo implementation, not independent undo records.

## Wire contract

```c
uint32_t narwhal_session_abi(void); /* exactly 1 */
ptrdiff_t narwhal_session_json(const uint8_t *input, size_t length,
                              uint8_t *output, size_t capacity);
```

Rust return type is `isize`. The caller owns both byte buffers; no pointer or
allocation ownership crosses the ABI. JSON is UTF-8, without a terminating NUL.
Use `c_size_t` for lengths and `c_ssize_t` for the result in ctypes. Input and output
may overlap. Callers must supply valid memory and prevent concurrent access to their
buffers; the ABI cannot detect arbitrary dangling pointers.

**Always allocate at least 4096 output bytes before making a call.** Mutation
replies contain only fixed field names, booleans and integers. Every mutation reply
fits that minimum, regardless of key/value size. There is no successful mutation
followed by an output-capacity error. Even `create`, `snapshot`, `prepare`, `abort`,
`close`, and `release_snapshot` are mutations: never use a sizing call for them.

- Normal result: positive byte count; parse exactly that many bytes as JSON.
- **Read reply larger than a valid capacity:** return required byte count, with
  **no copy at all**. Allocate that size and retry the read. `rows`, `get`, `at` and
  `snapshot_rows` can exceed 4096. Reads are pure. Concurrent writes can change a
  subsequent live read's size/content; retry as needed, or use `snapshot_rows` for
  a stable view while retaining its snapshot handle.
- Non-null output with capacity `<4096`: do not execute the request. Deliver
  `output_too_small` JSON if it fits, otherwise return that error's required length
  without a copy. This is rejection, not mutation capacity negotiation.
- Null output or capacity `>512 MiB`: return **-1**, without any dereference or
  mutation. This is the unavoidable transport exception to JSON errors: an invalid
  output descriptor cannot safely receive JSON.
- Valid output and null input: `null_input` JSON. Input length `>128 MiB`:
  `request_too_large` JSON, without reading input or executing anything.
- All other errors: `{"status":"error","code":"fixed_code"}`. Never echo metadata,
  parse diagnostics or input text. All successes include `"status":"ok"`.

Typed operation enums reject unknown operations, unknown/duplicate fields, missing
required fields, wrong types, malformed UTF-8/JSON, floats for integer fields,
negative unsigned integers, and out-of-range integers. Booleans are actual JSON
booleans, not 0/1. `prepare.value` is **required**, but may explicitly be `null`.
JSON `\u0000` is refused with `nul_not_supported`, pending the main lane's NUL policy;
never silently truncate. No Unicode normalization is performed.

### Exact implemented requests and replies

The table shows fields **in addition to** `status:"ok"` in every success reply.
Session/snapshot/ticket/state IDs and generations are JSON unsigned 64-bit integers.
Every named request field is required. No other request fields are accepted.

| Request | Additional success fields |
|---|---|
| `{"op":"create","rows":[[key,value],...]}` | `session`, `generation:0`, `state_id`, `count` |
| `{"op":"close","session":s}` | none |
| `{"op":"info","session":s}` | `generation`, `state_id`, `count` |
| `{"op":"rows","session":s}` | `rows:[[key,value],...]` |
| `{"op":"document_user_text_count","session":s}` | `count:u64` (keys containing ASCII backslash, not total rows) |
| `{"op":"get","session":s,"key":k,"exact":false}` | `found`, `value:string|null`, `key:canonicalSpelling|null` |
| `{"op":"at","session":s,"index":i}` | `found`, `row:[key,value]|null` |
| `{"op":"snapshot","session":s}` | `snapshot`, `state_id` |
| `{"op":"release_snapshot","session":s,"snapshot":p}` | none |
| `{"op":"snapshot_rows","session":s,"snapshot":p}` | `rows:[[key,value],...]` |
| `{"op":"equal","session":s,"left":p,"right":q}` | `equal:bool` |
| `{"op":"prepare","session":s,"generation":g,"key":k,"value":v}` | `changed:bool`, `ticket:u64|null` |
| `{"op":"commit","session":s,"ticket":t}` | `generation`, `state_id` |
| `{"op":"abort","session":s,"ticket":t}` | none |
| `{"op":"restore","session":s,"snapshot":p}` | `generation`, `state_id` |

`key` and non-null `value` are strings. `exact:true` requires the stored key's exact
spelling; `exact:false` uses the pinned ordinal ignore-case mapping. Missing `get`
returns `found:false,value:null,key:null` (the main facade supplies native's empty
string). Out-of-range `at`, including `u64::MAX`, returns `found:false,row:null`.
Indexing is zero-based insertion order. An invalid session is an error, not a miss.
Native StringTable `get(string)` is exact-case despite case-insensitive Set; the main
facade must request `exact:true`. The existing core `exact:false` behavior is unchanged.

`document_user_text_count` is a pure, compact read: Rust counts each published row
whose key contains at least one ASCII backslash (`U+005C`), once regardless of how
many backslashes occur. Leading/trailing/backslash-only keys count. Forward slashes,
fullwidth backslashes and backslashes only in values do not. No Python row export or
iteration occurs; cost is O(total key bytes), without copying the table. `info.count`
and `create.count` continue to mean **all rows**. Pending tickets do not affect this
count; commit and restore select the counted table. Generation/state identity are
unchanged by counting. MEASURED against rhino3dm 8.35.0: the eight fixtures from
`spike/test_document_session_integration.py` give counts `0,0,1,1,2,3,4,5`; additional
value-only/fullwidth/double-backslash fixtures give `5,5,6`.

### State ownership and transactions

- A session owns a generation and an `Arc<State>` containing an immutable table and
  `state_id`. Each table has insertion-ordered rows and a normalized-key hash index.
  Row strings and index keys are reference-counted; no copies cross into Python
  except requested JSON data.
- `create` replays native Set in input order. Later case-equivalent keys replace
  values, keeping the **first key spelling and insertion position**. Empty key:
  no-op. Empty value: delete. Delete then reinsert: append at the end. Generation
  starts at zero even when replay contains updates/deletions.
- `prepare` checks the supplied generation first. An empty key, identical value,
  or missing deletion returns `changed:false,ticket:null`, changing neither state
  identity nor generation and cloning no table. Null and empty values both delete.
- An actual change creates an independent staged version and a single-use ticket.
  Current state is untouched until `commit`. Multiple prepares may share the same
  original generation; they never incorporate one another's staged changes.
- `commit` checks the ticket owner and original generation, then publishes the
  staged reference and increments generation once. A stale commit returns
  `generation_conflict` **and discards that ticket**. Reusing it is `invalid_ticket`.
  `abort` simply discards a valid owned ticket. Generation overflow refuses commit
  without consuming its otherwise-valid ticket.
- `snapshot` clones an Arc. `restore` assigns the snapshot's Arc and **always**
  increments generation, even for identical content or the already-current Arc.
  It restores the original immutable `state_id`; that ID is **not** a full document
  revision. Previously prepared tickets become stale through generation checking.
- Snapshot and restore reference ownership is O(1), not a table copy. Dropping the
  last reference may still free O(n) index/row storage. A real write clones table
  containers only, sharing existing string allocations; unchanged rows are not
  decoded/rebuilt from a Python list. `get` is expected O(key length); `at` O(1).
  Actual writes are O(row count + changed text), not a persistent tree optimization.
- `equal` requires two valid snapshots owned by the supplied session. Same Arc is
  an O(1) fast path; otherwise compare ordered rows, exact spelling and values.
  Identical contents with different `state_id`s compare equal. Reordering does not.
- All session/snapshot/ticket/state IDs share one global, monotonically increasing
  namespace, starting at 1. IDs are never reused within the loaded library process,
  including after close/abort/release. Allocations reserve all required IDs together;
  overflow cannot partially publish. IDs are not stable across process restarts.
- `close` releases the session and all its owned snapshots/tickets. Handles from
  another session are never accepted, and errors never steal another session's handle.
- One Mutex protects the registry. Replies retain immutable Arcs while being
  serialized; concurrent calls cannot corrupt state. Catchable Rust panics cannot
  unwind across the ABI; a poisoned registry fails closed instead of guessing at
  recovery. This cannot recover from process termination, invalid FFI memory, or
  allocator OOM aborts. Release builds retain `panic="unwind"`.

### Bounds (OURS, not reference-application limits)

| Resource | Limit / behavior |
|---|---|
| Encoded input | 128 MiB absolute, checked before reading |
| Decoded key or value | 8 MiB each, UTF-8 bytes |
| Input replay rows / live table rows | 100,000 each; duplicate/no-op replay rows still count |
| Input replay key+value bytes | 64 MiB summed before replay, including deleted/duplicate rows |
| Live table key+value bytes | 64 MiB; first retained key spelling determines key byte count |
| Supplied output capacity | 4096..512 MiB for normal calls |
| Live sessions | 256 globally |
| Live snapshots | 100,000 globally |
| Outstanding tickets | 1024 globally |
| Retained distinct-table admission | 256 MiB conservative charge: `2*table_bytes + 192*row_count + 128` per version |
| JSON preflight | At most 300,064 value/key nodes, 16 fields per object, 100,000 entries per array; serde recursion limit retained |

Snapshots do not consume another table admission charge. Distinct staged/retained
versions do, even if their strings share allocations; this is conservative admission,
**not measured RSS**. Near-limit tables may require releasing old snapshots/tickets
before another write is admitted. An actual input/reply allocation can temporarily
exceed retained-state accounting. All admission errors are explicit; there is no
truncation, eviction, approximation or implicit handle expiry.

Codes: `invalid_utf8`, `invalid_json`, `invalid_request`, `nul_not_supported`,
`string_limit`, `row_limit`, `table_bytes_limit`, `json_structure_limit`,
`request_too_large`, `null_input`, `output_too_small`, `invalid_session`,
`invalid_snapshot`, `snapshot_owner_mismatch`, `invalid_ticket`,
`ticket_owner_mismatch`, `generation_conflict`, `generation_overflow`, `id_overflow`,
`session_limit`, `snapshot_limit`, `ticket_limit`, `retained_state_limit`, `panic`,
`registry_poisoned`. More than one invalid property may exist; validation order,
not client-supplied field order assumptions, determines the first error.

## Exact openNURBS ordinal mapping

**MEASURED source:** `vendor/opennurbs/opennurbs_string_compare.cpp`, SHA-256
`a34fea6150cd2830925c159bc87c42da54ded0c6093cb2e5b8a0775b627c2cbe`.
`ON_UserStringList::SetUserString` / `GetUserString` in
`vendor/opennurbs/opennurbs_userdata.cpp` call `CompareOrdinal(key,true)` and
preserve the first key spelling. The core uses exactly **one pass** of
`OrdinalUnsignedToIgnoreCase(c,0xffffffff)`: ASCII LowerOrdinal, otherwise
MapCodePointOrdinal MinimumOrdinal. It does **not** use Rust lowercase, Unicode
casefolding, repeated mapping, NFC, or locale rules.

`generate_ordinal.rs` validates the source hash, extracts the actual unmodified C++
function bodies into external scratch, compiles a tiny reference program, evaluates
all **1,112,064 Unicode scalars**, and emits the **205 nonidentity mappings** in
`src/ordinal_map.rs`. The generated source retains McNeel's attribution/disclaimer;
`OPENNURBS_LICENSE` retains the pinned vendor license. This is derived data, not a
claim that Narwhal authored the source algorithm. Normal Cargo builds need neither
vendor sources nor a C++ compiler.

**MEASURED wchar16/32:** exhaustive generation also verifies every BMP scalar has
identical UTF-16/UTF-32 mapping, every surrogate code unit stays unchanged, and every
supplementary scalar stays unchanged. Therefore equality for valid scalar strings
matches across those wchar widths. This does not claim identical sorting order or
support for malformed UTF-16/lone surrogates. Fullwidth lowercase `ａ` is deliberately
excluded by the source's `<=0xFF41` boundary; do not “correct” it.

**MEASURED rhino3dm 8.35.0**, direct File3dm.Strings Set/replay probe in this session:

| Collapse | Remain distinct |
|---|---|
| `A/a`, `Σ/σ`, `İ/i`, `é/É`, `Ｂ/ｂ` | `ß/ss`, `Σ/ς`, `K/k`, `ı/I`, `ŉ/N`, `Ａ/ａ`, `Ё/ё`, `𐐀/𐐨`, `𞤀/𞤢` |

The integration test includes these and further pinned-source cases, including
composed/decomposed accents, long S, and Y-diaeresis. The scalar test checks the Rust
lookup applies the generated map once, including non-idempotent mappings.

## Build, regeneration and focused validation

Artifacts are external; the directory-grant tool rejected both external creation
requests in this session, so the owner-permitted `/tmp` fallback was used. No
installed runtime staging. Run from the repository root. Require at least **5 GiB
MemAvailable** (4 GiB desktop reserve + 1 GiB compiler); no extra swap; one job.
Acquire the shared heavy lock around the actual bounded compiler command, not a
runner that already acquires it.

```sh
awk '/MemAvailable:/ {print; if ($2 < 5242880) exit 1}' /proc/meminfo &&
flock -w 30 /tmp/narwhal3d-heavy-build.lock systemd-run --user --scope \
  -p MemoryMax=1024M -p MemorySwapMax=0 timeout --kill-after=5s 110s \
  env CARGO_TARGET_DIR=/tmp/narwhal3d-application-core/target \
  cargo test --locked --offline --manifest-path native/application_core/Cargo.toml \
  -j 1 -- --test-threads=1
```

Use the same command envelope with `cargo build --release --locked --offline
--manifest-path native/application_core/Cargo.toml -j 1` for the optimized library.
Do not run a full project/Blender build for this lane.

For regeneration, compile `generate_ordinal.rs` with standalone `rustc --edition=2024`
under the same lock/memory scope to an external executable. Its arguments are
`SOURCE_CPP EXTERNAL_SCRATCH_DIR OUTPUT_RS`. Generate to an external file and `cmp`
it against `src/ordinal_map.rs` to verify reproducibility without rewriting source.

For mutation tests, compile `mutation_check.rs` similarly and invoke it with
`native/application_core /tmp/narwhal3d-application-core/mutations-FRESH_RUN`.
The runner creates only external source copies, refuses existing per-mutant paths,
uses a separate Cargo target from baseline, and takes the heavy lock/1 GiB scope
around **each actual Cargo invocation**. It requires a named test assertion failure,
not a compile/startup failure, for every mutant. Logs remain under each mutant's
`observed.log`. No production source is broken/restored; rerun the baseline after.

### Initial slice validation receipt — 2026-10-30

- **19 tests passed:** 6 unit + 13 exported-ABI protocol tests, one test thread;
  includes 100k-row import/write boundaries, 8 MiB string boundary, 64 MiB table
  boundary, all live-handle admission limits, UTF-8/strict JSON, snapshot Arc
  identity, overflow/retained accounting, concurrent callers, panic/poison behavior,
  input/output overlap, read sizing/no partial copy and mutation buffer safety.
- **25 isolated mutants killed**, all by observed test failures (not compilation
  errors), in `/tmp/narwhal3d-application-core/mutations-01`. Production untouched.
- Exhaustive C++ map generation: **1,112,064 scalars / 205 mappings**, UTF16/UTF32
  equality equivalence verified. Direct rhino3dm probe: **14 casing pairs matched**.
- Restored **debug baseline 19/19 passed**, then **release tests 19/19 passed**.
  `cargo clippy --all-targets -- -D warnings` and `cargo fmt -- --check` passed.
  Clippy initially flagged only a test's default-then-field initializer; corrected
  without changing test behavior, and reran successfully.
- Release build succeeded with 1 job, 1024 MiB MemoryMax, no extra swap, shared lock.
  `nm -D` shows exactly the two intended exported functions. A real ctypes CDLL smoke
  check against the release `.so` passed ABI version, dotted-I casing, oversized
  read retry/no partial copy, prepare/commit, restore generation/identity and cleanup.
- Regenerated the map into external scratch and `cmp` verified byte-for-byte equality
  with the checked-in generated table. No vendor code was changed.
- Full product suite and Blender were not run, per the owner-approved lane boundary.
  No installed runtime was staged. All existing concurrent project changes were left
  untouched; this new crate is left uncommitted for main-lane integration.

### DocumentUserTextCount follow-up receipt — 2026-10-30

- Added the pure `document_user_text_count` request and Rust key predicate; `get`
  behavior and `info.count` remain unchanged. README and C header document the API.
- **20/20 debug and 20/20 release tests passed**, including native fixture counts,
  create replay, read-only identity/generation, staging/abort, case-insensitive
  update/delete, empty-value deletion, restore and strict request/handle errors.
- **28/28 isolated mutants killed** in
  `/tmp/narwhal3d-application-core/mutations-count-01`: the existing 25 plus three
  counter mutants (all rows, forward slash, exactly one separator).
- Rebuilt the same release `.so` after guarded tests; one job, shared heavy lock,
  1024 MiB/no extra swap. Clippy with warnings denied and formatting check passed.
- Direct rebuilt-release CDLL/native comparison matched rhino3dm 8.35.0 for all
  **11 count fixtures**: `0,0,1,1,2,3,4,5,5,5,6`, without changing state identity.
  No Python/global source edits, installed staging, Blender or full-project suite.

### Public-source naming cleanup receipt — 2026-10-30

- Private snapshot/ticket session references and helper arguments use `session_id`;
  JSON request fields and existing mismatch error codes are unchanged. Mutation
  anchors follow the renamed identifiers. The introduction is component-specific
  and resource-bound prose uses reference-application terminology.
- **20/20 debug and release tests passed**, **28/28 isolated mutants killed** in
  `/tmp/narwhal3d-application-core/mutations-public-01`; Clippy and formatting passed.
  Release rebuilt with the same guarded one-job configuration.
- SHA-256 checks confirm `src/ordinal_map.rs`, `generate_ordinal.rs`, and
  `OPENNURBS_LICENSE` are byte-for-byte unchanged, preserving all upstream legal text.

**Ready for main integration:**

`/tmp/narwhal3d-application-core/target/release/libnarwhal_application_core.so`

SHA-256: `1ff893e02972a7657493fd3447db05ead82130e2935f69c6ed8716d15d47d4b6`

`/tmp` artifacts are ephemeral; the main lane owns durable build/
packaging integration. NUL policy remains explicitly refused pending main's probe;
no Unicode casing approximation or supplementary-case limitation is hidden.

## Persistent history service — MAIN-RUST-HISTORY-02

Additive exports `narwhal_history_abi()` (=1) and `narwhal_history_json()` implement
all operations and exact result/header shapes in [HISTORY.md](HISTORY.md).
Metadata ABI1, its authority, normalization and exports are unchanged. The history
registry has its own mutex/poison boundary. All requests are typed Rust schemas;
unknown/duplicate fields and omitted required nullable fields are rejected. No
per-call timeline snapshot, geometry serialization, or Python object pointer is
used. Only explicit `record` / `records` reads project record data to JSON.

The facade owns opaque payload objects; Rust owns persistent record arenas,
undo/redo order, nesting, identity, refcounts, leases and navigation tickets.
Record IDs remain stable through stack transfers, pruning and explicit leases.
History close invalidates every resource, including leases and pending tickets.
Reads do not implicitly lease records; the host must explicitly retain escaped
views and release them. History/record/payload/ticket/state IDs share one monotonic
non-reused history-service namespace; metadata handles use their separate ABI.

**Concrete limits (OURS, process-wide unless stated otherwise):**

| Resource | Limit |
|---|---:|
| Live histories | 256 |
| Live records, including open and leased pruned records | 100,000 |
| Payload registrations, including unacknowledged garbage | 200,000 |
| Record leases | 1,000,000 |
| Stored record-name UTF-8 bytes | 16 MiB |
| One record name | 16,384 UTF-8 bytes |
| Pending tickets | One/history, therefore at most 256 |
| Configured oldest-undo-record count limit | 1..100,000/history |
| Garbage batch / unique acknowledgment IDs | 1..128 / 0..128 |
| Request / output descriptor | 1 MiB / 512 MiB |
| Minimum output capacity before any operation | 4096 bytes |

These are admission bounds on logical native storage, **not geometry memory or
an allocator/RSS guarantee**. Host payload memory is opaque and not measured by
Rust. Arenas/stack allocations may retain bounded high-water capacity. Admission
refusals never evict another record; close frees the history's containers. The
configured record count is the only automatic pruning policy. Garbage is a
bounded ordered set, not an ever-growing log of stale retirement events; repeat
retirement is deduplicated and reattachment removes the ID immediately.

**Semantics important to the adapter:**

- `current_serial` is the open serial or0; `top_serial` is newest undo serial or0.
  Neither is saved-position identity. Compare `state_id` instead. Empty/abandoned
  records consume serials, not state IDs. An ordinary clear preserves state ID;
  a clear recovering unknown restoration assigns a fresh identity.
- As in the existing `UndoStack.capture`, an eligible `capture(null)` returns
  `captured:true`, but header `captured` remains false. Another capture is still
  eligible. Capture with no open record or after a non-null before returns false.
- `begin` checks enabled before nesting. Capture/end of an existing open record
  still work when recording is disabled. Name strings are preserved exactly,
  including empty strings and NUL; host name defaults are facade policy. The
  metadata ABI's separate NUL refusal remains unchanged.
- Real record/end, depth, enable, field/name edits, prepare/commit/abort and clear
  advance generation when they change logical history. Read/retain/release,
  register/drop payload, garbage/ack and idempotent edits do not advance it.
  Releasing a final lease may still change resource counts/GC in the header.
- Prepare pins current payload without modifying either record field, reserves
  completion generation and destination stack capacity, then freezes edits.
  Commit consumes the ticket once and refreshes the opposite payload. Abort
  leaves fields/stacks/state unchanged; unknown restoration additionally blocks
  edits/navigation until clear. Wrong/cross-history tickets never consume one.
  Close and safe lifetime/GC operations remain available while pending/uncertain.
- Every supplied non-null payload must belong to that history, even for an
  otherwise ineligible capture/navigation. Unattached registrations are not
  garbage. `drop_payload` accepts an existing zero-ref registration (also if
  retired); it refuses live references. `ack_garbage` validates the whole unique
  batch before removing anything. Garbage enumeration is ascending ID order.
- Mutations return only compact typed results/headers, including large clears.
  Only `record` and `records` can require a read retry with a valid4096-byte output.
  The host must never retry a mutation as an output-sizing strategy.

Error codes are fixed strings: `invalid_history`, `invalid_record`,
`invalid_payload`, `invalid_ticket`, `invalid_limit`, `payload_in_use`,
`record_not_retained`, `navigation_busy`, `history_uncertain`, `invalid_garbage`,
`duplicate_payload`, `garbage_limit`, `history_limit`, `record_limit`,
`payload_limit`, `lease_limit`, `name_limit`, `name_storage_limit`, `id_exhausted`,
`serial_exhausted`, `depth_exhausted`, `generation_exhausted`, `allocation_failed`,
and the existing descriptor/JSON/UTF-8/panic/poison error codes. Panic containment
is not an allocator-OOM recovery mechanism; a poisoned registry fails closed.

### Focused history validation

`src/history_tests.rs` checks admission boundaries, ID/serial/depth/generation
exhaustion and transactional refusal, global close accounting and payload refs.
`tests/history_protocol.rs` exercises the actual ABI, lifetime and saved-state
transitions, null/disabled/nested recording, field edits, no-op redo preservation,
pruning, bounded GC, cross-owner handles, racing navigation consumers, unknown
restoration blocking, strict schemas and every mutation's capacity preflight.

`history_mutations.rs` copies only this crate's source/tests/manifests to external
scratch directories; it never edits production source. It accepts
`CRATE_DIR SCRATCH_DIR TARGET_DIR [FIRST [COUNT]]`, checks reserve *inside* the
shared heavy lock, and requires actual assertion failures (not compile errors)
for every mutant. Cargo invocations are offline/locked, one job, bounded90s,
`MemoryMax=1024M`, `MemorySwapMax=0`. Use a fresh scratch directory per campaign.
Restore baseline GREEN afterward; do not install a mutant build artifact.

History artifact target for Main integration:
`/tmp/narwhal3d-history-core/target/release/libnarwhal_application_core.so`.
No Python, global build manifest, staging or installed artifact changes belong
to this slice. End-to-end host restoration remains Main's integration gate.

### History implementation validation receipt

- **40/40 debug and release tests GREEN:**20 new history tests and20 unchanged
  metadata tests. Release build GREEN; Clippy all targets with `-D warnings`
  GREEN; crate and standalone mutation-runner formatting checks GREEN.
- **32/32 history mutants observed RED at assertions**, isolated campaign
  `/tmp/narwhal3d-history-core/mutations-02`, followed by restored production
  GREEN. The first campaign exposed a Cargo fingerprint/output-cache collision
  when different source copies shared a target. The runner now puts each mutant
  in its own fresh `TARGET_DIR/history-mutants/<campaign>/<mutant>` directory;
  the entire campaign was rerun, and production was explicitly rebuilt. No
  production source was replaced by a mutant.
- All build invocations used the shared heavy lock, offline/locked dependencies,
  one job,1GiB/no-extra-swap cap, bounded runtime and4GiB desktop reserve. No
  full project suite, GUI or VM validation was run, as requested.
- `nm -D --defined-only` verifies exactly the two history and two metadata
  exports. Final release library SHA-256:
  `a7fe4fd33516cea0c99be2fe516fb89cd54f090cef6554965cfe7a4d4f825c8d`.
- `HISTORY.md` was not modified. Changes are confined to this crate: new
  `src/history.rs`, `src/history_tests.rs`, `tests/history_protocol.rs`,
  `history_mutations.rs`, additive `src/lib.rs`/C header exports and this README.
  No Cargo dependency/manifests, existing metadata implementation/tests,
  installed library, staging, commit or other-agent files were changed.
