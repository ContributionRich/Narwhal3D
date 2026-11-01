# Object registry — additive ABI1 foundation

Status: integrated into live model constructors, queries, display associations,
dirty tracking and the existing single Undo snapshot path. Linux development and
focused real-model/geometry gates pass; final combined publication gate is pending.
This is NOT a qualified full-document revision. External masters, settings, layers
and selection retain their separate contracts.

## Authority

OURS: Rust owns UUID membership, logical order, object-name/display-name associations,
opaque host payload keys, and immutable registry snapshots. Exact geometry stays in
openNURBS/OCCT, including masters and recipe trees. A payload key is a non-pointer,
owner-scoped integer referring to an immutable host geometry/attribute snapshot; it
is not geometry and cannot authorize an operation in another document.

The counter is `object_registry_revision`, NOT full-document generation. Layers,
materials, settings, selection, metadata and external master mutation are not
implicitly certified by this counter. Existing document metadata and the single
native Undo owner keep their contracts. Registry transactions are NOT Undo records.

## Transaction boundary

1. Prepare validates owner, expected revision, UUIDs, payload keys, batch limits and
   resulting state; reserves a single owner-scoped transaction and its memory.
2. Begin-apply occurs BEFORE calling host mutators. Readers cannot treat an applying
   state as authoritative. A failed/unknown begin reply is not blindly retried.
3. Host applies exactly the admitted edits. Returned UUIDs and observed membership
   must match the prepared result. In particular, openNURBS reminting a duplicate UUID
   is not a successful requested insertion.
4. Commit publishes the prepared immutable state only after host confirmation.
   A real change advances revision once; a no-op does not. Commit consumes the ticket.
5. Abort is allowed only before begin-apply. After begin-apply, failure/exception or
   unknown native result invalidates authority; it is not an ordinary abort/retry.
   Recovery requires an explicit rebuild from observed host state, with a new revision.

Unknown/direct writes invalidate authority before reuse. Notifications must be scoped
to the owning File3dm, not broadcast solely by UUID. Detached attributes sharing a
UUID must not mutate another document's registry. A retained pre-instrumentation
native callable is a bypass, not evidence that interception is complete.

## Lifetime, snapshots, bounds

Handles are monotonically allocated nonzero integers, never pointers or reused IDs.
Sessions, tickets and snapshots have distinct checked ownership. A second transaction,
stale revision, expired handle, cross-owner snapshot, malformed UUID or duplicate UUID
refuses before host changes. Snapshots retain immutable native states with bounded
aggregate memory; geometry references remain host-owned. Closing an owner releases
its tickets and snapshots. Undo restores through the same prepare/apply/commit path,
not an additional history stack. New/Open construct a candidate owner before adoption;
a failed load leaves the old owner and document intact.

Queries and edits are bounded batches. No per-frame full serialization and no
per-object FFI loop for import/restore. Read-only compatibility views may materialize
native results but cannot become independently writable Python registries. Retire
Python dirty/name bookkeeping only after all its consumers use the native owner.

## Measured baseline, 2026-10-31

Published main: b213e8235e5d5eae99da9b2fb843b2e0c381f733.
Installed application core SHA256:
`b156d9c049aa735e8b5c7c3b66e68fe87a3533cb0ac1eab73369bb02473cea21`.
Actual workspace model.py, src/lib.rs and src/session.rs equal published bytes.

A bounded bundled-Python probe measured:
- File3dmObjectTable.AddPoint with an existing explicit UUID remints the second UUID.
- Delete of a missing UUID returns None, yet the current watcher marks that UUID moved.
- Attribute Name mutation marks the existing object's Python dirty map.
- Repeated File3dm.Objects access returns distinct Python wrappers; File3dm is stable.
- Watch installation reports 752 wrapped members. This count is NOT coverage proof.

Source observations (not exercised coverage): model.py marks before geometry/attribute
calls and before Delete, but after Add; marks broadcast by UUID across tracking models.
Restore suspends per-call marks, batches affected UUIDs in finally, and keeps logical
order separate from physical order until save. `_blender_names` has external readers
in bridge.py and curve_inputs.py and a mutation in commands.py publication cleanup.
A native-backed compatibility facade can preserve those read interfaces without
editing other leases; the commands.py cleanup seam is in Main's authorized write set.

## Wire and implemented scope

Exports: `narwhal_object_registry_abi() == 1`, `narwhal_object_registry_json`.
The latter uses the existing pointer/length and caller-owned output-buffer convention.
Input is at most32 MiB, output capacity4096 bytes through512 MiB. Too-small output
never executes a request. Large read replies return required size without a partial
copy; only reads may retry. Mutation replies fit4096 bytes. No caller pointer is kept.

A row is `{id, name, display, payload}`: canonical lowercase non-nil UUID, UTF-8 name,
nullable display name and nullable nonzero u64 host key. Rust checks key representation,
NOT host geometry existence or provenance: the integrating host must validate that key
against its own owner-scoped immutable payload references before preparation. Native
snapshots cannot be used with another owner. None of these keys certifies exact geometry.

Requests are tagged by `op`, reject unknown fields, and use `owner` except create:
- `create(rows)`, `close`, `info`.
- `reserve_payloads(count)` reserves1..100000 never-reused non-pointer keys in one
  native call and returns first/count. It does not change document revision or prove
  host payload existence. Host-owned immutable payload lifetime validation is still
  required; reservations avoid introducing a second Python identity counter.
- `rows(offset, limit)` returns a page plus revision/next; `get(id)` returns a row or
  null; `find_name(name)` returns the first matching logical UUID or null. Native name
  indexing uses Rust lowercase. Full host Unicode parity remains an integration gate.
- `snapshot`, `release_snapshot(snapshot)` retain/release immutable native state.
- `prepare(revision, rows, rebuild)` reserves complete adoption/reconciliation.
- `prepare_edits(revision, edits)` sends ONLY changed rows in a batch. Edits are
  `add(row)`, `replace(row, append)`, `delete(id)` and `bind(id, display)`. Duplicate
  additions and missing replacement/binding targets refuse atomically. Missing delete
  is a no-op. Replace can preserve logical position or append, matching the host route.
- `prepare_restore(revision, snapshot)`, `begin_apply(ticket)`, `commit(ticket)`,
  `abort(ticket)` and `invalidate` follow the transaction boundary above.

Info returns revision, count, authoritative and pending. `authoritative` describes
this registry's publication state according to the host's transaction reports, NOT
an independent assertion about all File3dm/OCCT mutations. The model adapter must not
expose it as a full-document revision. Invalidation consumes pending work and requires
explicit observed-state rebuild. An applying ticket cannot be aborted as though the
host certainly remained unchanged. Failed/unknown mutation replies are not retried.

OURS limits:64 owners,100,000 rows/edits,1024 rows per page,4096 bytes per name/display
string,4096 snapshot handles globally,64 MiB accounting per state and256 MiB retained
native-state accounting globally. Accounting conservatively includes row/index/key
storage, shares immutable states once, and is NOT an RSS or host-geometry measurement.
Parsing/temporary candidate storage is separately bounded by input/table limits.
Snapshots retain Arc states in O(1); the current implementation copies/reindexes native
rows on an edited candidate, so edits are not claimed O(1) or structurally persistent.
Batches avoid a per-object registry FFI loop and full-table input serialization for
small edits. Workload/memory acceptance in the real model remains outstanding.

## Live model index, journal and snapshot plans

A fresh owner enters live mode with `track`. `mark(ids,moved)` and `mark_all` are
host-boundary notifications, not geometry implementations. `dirty` is a read-only
journal view. The model no longer has an independently writable Python dirty/name
table; its legacy `_dirty` property materializes diagnostic data only.

The observed `index` operation consumes NWIDX01 membership/name rows. Actual metadata
and physical-order changes must match known notifications, initial adoption, or the
native logical order being normalized after Undo; otherwise the owner invalidates.
The last confirmed physical order is distinct from immutable logical snapshot order.
Count/display hot reads use native count/UUID checks instead of serializing the model.

Observed `capture` computes logical rows and payload reuse in Rust from complete
canonical witnesses. Changed objects without notifications refuse. Unchanged payload
keys and witness records are shared, including no-op captures. `ticket_rows` gives
Python only the bounded plan needed to retain/copy exact host payload references.
`bind_display` changes the native association without another Python registry.

`ticket_diff` computes restore removals/additions from native payload identities;
`order_pending` and `normalize_plan` compute deferred backing-table normalization.
Restore and normalization require confirmation of actual host contents before commit.
The host validates snapshot payload IDs before mutation. All of this is coordinated
with the existing Undo owner; there is no additional user history.

Witness storage is immutable and shared PER OBJECT across snapshots. The global
retention charge follows actual record/index/row-state lifetimes, not the full input
batch size multiplied by history depth. A5000-sphere/50-snapshot real-model workload
passes after the initial whole-batch implementation failed at snapshot17. Logical
state/input/owner bounds still apply; this is not an unlimited-memory claim.

## Additive witnessed-observation boundary

`narwhal_object_observation_abi() == 1` qualifies the additive interface.
`narwhal_object_registry_observe(header, header_length, observation, observation_length,
output, capacity)` accepts strict JSON plus NWOBS01 bytes from the separately qualified
read-only native host observer (`native/document_observer/`). The Rust decoder checks
framing, canonical/non-nil/unique UUIDs, UTF-8 names, archive-field bounds and exact
consumption. Archives remain opaque witnesses, not substitute document geometry.

The header is one of:
- `prepare`: owner, revision, rows, rebuild. Observed membership/names must match rows;
  every row needs a distinct nonzero payload key. If an observed object changed, its
  previous payload key cannot be reused. The host must still prove the new key refers
  to the corresponding immutable exact payload. Per-object canonical witnesses are
  retained/shared in immutable states and charged against state/global memory limits.
- `check`: owner, revision. Returns revision and matches. An actual content mismatch
  INVALIDATES the owner and advances its revision before returning matches=false.
  Names/membership and complete geometry/attribute archives are compared, keyed by
  UUID. Physical table order may lag logical registry order after Undo; that alone
  does not fail confirmation. This is a mutating operation, not a retryable read.
- `commit`: owner, ticket. After begin_apply, confirms observed host contents against
  the prepared witness before committing. Mismatch or malformed observation consumes
  the ticket and invalidates the owner; it cannot be treated as an unapplied abort.

Snapshots retain the witness with their Arc state. Restoring one also requires observed
confirmation. Plain JSON commit cannot bypass confirmation; ordinary prepare/edits or
restoring an old unwitnessed snapshot cannot downgrade a witnessed owner. A real no-op
preserves revision. These operations have fixed-size replies fitting4096 bytes; NEVER
retry a lost/oversized/unknown reply. Header <=32MiB and observation <=128MiB, with
retained per-state64MiB/global256MiB accounting imposing tighter adoption limits.

Thin adapter methods: `prepare_observed`, `check_observation`, `Ticket.commit_observed`.
An observation check failing does not itself repair the host or provide a new snapshot.
Recovery is an explicit observed-state rebuild with fresh verified host payloads.
The observer does NOT witness external OCCT master internals, layers/settings or full
history; this remains precisely scoped object-registry state, not a full-doc revision.
Both component tests and real model constructor/query/snapshot/restore tests exercise
this boundary. A native observation witness is NOT an importable geometry archive:
redundant CRC words and userdata copy counters are normalized using the native archive
stack. Exact host geometry is never reconstructed from these comparison bytes.

## Qualification reached and outstanding

Compiled native tests cover identity/order/duplicates, no-op and stale revisions,
owners/tickets/snapshots, batch atomicity, interrupted apply, memory/handle limits,
output negotiation and buffer descriptor refusal. Native assertion mutants and
restored controls passed. Candidate-backed Python tests cover marshalling, pagination,
unknown/lost replies, interruption quarantine and lifetime cleanup; assertion mutants
passed. Existing metadata/history ABI controls remain green. Registry plus metadata
snapshots were exercised through ONE existing Rust Undo owner, with nested grouping,
Undo/Redo and pre-restore refusal. This is component qualification, not real document
geometry/history qualification.

Live integration now covers model membership/name/order consumers, native display
associations and dirty tracking, owner-scoped host notifications, missed-write refusal/
explicit rebuild, full attribute copies, single-history geometry+metadata Undo, failed
adoption and save/reload. Focused real tests include228 Undo checks,104 analytic Undo/
Redo checks, and the representation audit's exact fresh-process recipes. The workload
records absolute p50/p95, memory and FFI traffic; it does not claim a whole-app speedup.
Still outstanding at this checkpoint: remaining integrated command/GUI/package gates,
frozen final product validation and audited publication. The previous published
integration gate must not be repeated merely to establish this baseline.
