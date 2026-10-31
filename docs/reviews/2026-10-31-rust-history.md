# Persistent Rust document history

This development-source update adds a real Rust history service to application-core.
It does not change published release packages or move exact geometry out of its
native kernels. The editor, AI and research work streams remain separate.

## Ownership and integration

Rust now owns the live Undo/Redo timeline: named records, serials, nested grouping,
lazy capture, limits, redo invalidation, state identity, record leases, opaque
payload reference accounting and navigation transactions. Metadata ABI1 remains
unchanged; the same library adds history ABI1.

Python retains actual opaque geometry snapshot references and runs Blender/model
restoration callbacks. Those payloads are not serialized through the history ABI.
Stable Record views preserve existing scoped-publication rollback semantics,
including mutable before/name fields. There is no second Python-editable timeline.

A complete legacy implementation is selected only when the history service is
absent at construction. Native errors never switch an existing timeline to it.
Future packages require both metadata and history services, not merely a library
with the expected filename.

## Behavior and safety

- Prepare navigation, restore through the host, then commit the exact transaction.
  A refused pure restore admission keeps history retryable and unchanged.
- An error after model/display restoration starts is conservatively uncertain,
  not a claim of successful rollback. Further history mutation/navigation is blocked
  until an explicit recovery action. Dirty-state checks cannot trust a stale cache.
- Separate saved-position identity survives ordinary history clearing and pruning.
  Captured open records are dirty even before commit. Save or explicitly settled
  Exit can acknowledge uncertain contents without silently repairing/unlocking history.
- Failed New/Open publication retains the old model, context, history and saved markers.
- Failed payload registration is rolled back; garbage acknowledgment cannot expose
  stale payload IDs to destructor-time reentrancy. Fatal native replies and local
  post-call allocation failures invalidate cached-state certification.
- Hot status reads use immutable cached headers. Repeated note_change/capture calls
  do not re-copy the document snapshot. History names are one batched native read.

History generations/positions are NOT authoritative full-document revision or
selection/topology tokens. Geometry snapshots remain host/native-library references
until their own domain migrates. The core does not compute or approximate geometry.

## Validation

- Native core:43 debug/release tests;32 history mutants plus the names-order mutant
  observed failing, followed by restored controls; strict Clippy and formatting.
- Native history facade:41 tests,38 assertion-killed mutations and restored baseline.
- Metadata ABI regression:24 tests,13 mutations.
- History integration:6 host +22 actual Blender tests. The16-case mutation portfolio
  plus the additional captured-open-state mutation were killed and restored.
- Existing Undo suite:227 assertions; existing Set/Get metadata checks passed.
- Package history-service checks:19 tests,30 mutation controls; metadata service
  requirements and artifact names retained.
- Final uninterrupted `./run_tests.sh`: **245 top-level gates passed**, zero failures;
  10 nested GUI checks also passed. Runtime32m30s. All3,038 monitored source files
  stayed unchanged; minimum available memory5.632GiB and no observed OOM events.
- Windows GNU and Apple Silicon Rust compile checks pass. Actual MSVC/macOS runtime
  and package acceptance are still required before a future release.

Explicit optional skips remain: separate staged-product automation startup,
external icon reference, opt-in raster parity, unavailable Inkscape/PDF/AI helpers,
staged TraceDraw certification and external/embedded CDR prerequisites. They are
not counted as passing. The initial broad attempt stopped at a linker crash in
RAM-backed temporary storage; a short disk-backed scratch path passed the focused
check and final complete run. No product assertion was removed to address it.

## Performance boundary

A controlled host probe used200 samples per case, excluding geometry snapshot,
restoration and rendering work. Native begin/capture/end cost roughly83–84µs,
100 history names43µs, and an Undo/Redo control cycle175µs. Results stayed flat
between zero-byte and8MiB opaque payloads. Observed p95 control time stayed below
0.21ms in that probe.

The old Python list controls are faster in isolation (roughly1µs,2µs and0.6µs).
This is deliberately an ownership/portability migration, not a microbenchmark
speedup claim. The benefit is a single native state machine, stronger transaction
boundaries and no snapshot-data marshalling. Status reads and repeated capture
checks require no FFI, and repeated context capture avoids unnecessary model work.
No whole-application or cross-platform latency claim is made.

## Deployment

The reviewed Linux development application-core artifact is staged with backup and
source provenance. An already-running process may still hold its old mapped library;
restart the application when convenient to use the new native history service.
No automatic document migration, VM restart, new release or published-asset change
is performed. The next core domain remains a separately designed ownership slice.
