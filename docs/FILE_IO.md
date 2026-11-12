# Native file I/O and background loading

These changes are in current source, not the published 0.4.12 binaries.

## What Rust owns

- `native/document_core`: bounded `.narwhal` container reading/writing, checksums,
  member/path validation and atomic file publication. The container format stays
  at version 1; no migration or conversion of existing projects is required.
- `native/application_core/src/load_jobs.rs`: persistent load ordering, cancellation,
  newest-pending replacement, document/history guards and publication decisions.
- `native/application_core/src/save_jobs.rs`: one active save, newest queued user
  save, AutoSave admission and terminal job ownership. A queued request can never
  replace the active save. Busy AutoSave never displaces a user save.
- `native/converter_supervisor`: resource-bounded validator processes and teardown.

There is no new production Python load-queue module or Python queue-policy fallback.
The discarded prototype is outside application imports. Existing Python files
retain Blender scheduling, opaque host-object lifetimes and the required
openNURBS/OCCT binding calls. This is **not** a claim that the entire application
or exact-geometry validator is Python-free.

## Native Open and cold Library insertion

1. Rust allocates a guarded job. The current document remains active.
2. A file-only background worker unpacks and validates a private candidate using
   the existing exact-geometry checks. No live model or Blender object is accessed.
3. Main-thread candidate construction is stepped between exact masters. Its
   temporary bounds context is isolated from other UI/geometry operations.
4. Rust rechecks eligibility before each construction step and final publication.
   Open cancels if the original document, path or history changed. Library insertion
   permits intervening edits but cancels if the document itself was replaced.
5. Only a fully admitted candidate can commit. Library insertion still creates one
   ordinary Undo record; Open starts the new document's ordinary history.

Only one load worker runs at once; only the newest pending load is retained.
Cancellation prevents publication and cleans private files. A running isolated
validation process is bounded but is not immediately killed by queue cancellation;
a newer pending request waits for it to finish.

Input-file identity is checked during preparation, after hydration and immediately
before publication. Failed/cancelled candidates do not replace the current model.
A bad or missing native queue ABI fails explicitly; it does not silently switch
an installed background path to a Python coordinator.

The synchronous `narwhal_document.load_model()` API uses the same staged admission
and host checks for headless integrations. It is intentionally synchronous.

**Scope:** native `.narwhal` Open and canonical `.narwhal` Library assets use this
pipeline. Foreign-format readers (`.3dm`, STEP, DXF, etc.) have not all been converted
to asynchronous imports. Thumbnail rendering, individual exact-kernel calls,
Blender object construction and final scene publication still require the main
thread, so zero pauses for arbitrarily large objects are not promised.

## Save and recovery

The existing frozen staging, native validation, atomic packing, exact repair,
resource retries, emergency copies and AutoSave recovery behavior remain intact.
Only request policy moved from `save_queue.py` into Rust. Python retains opaque
job payloads and main-thread repair callbacks; it does not own a second queue.

Edits made while a save runs are not silently marked as saved. A repair/revalidation
attempt retains the same active native job. Exit drains active saves before recovery
handling; native save queues refuse to close while work is active.

The prior coordinator is backed up privately outside application imports with a
hash receipt. It is not shipped as a fallback.

## Worker startup and runtime identity

Linux and Windows source builds can run validation in the same runtime's bundled
Python instead of launching another full Blender process. The package imports
its UI lazily, and Python workers use `-P` so an application module such as
`select.py` cannot shadow the standard library.

Worker runtime identity has **not** been weakened for speed. Parent-issued,
digest-qualified TBB image witnesses reproduce Blender's loaded threading runtime
in the lightweight worker. Archive metadata and ambient environment claims do not
supply those witnesses. Existing fresh-worker bounds proofs still require the
same decoder/kernel/helper identity and exact native witnesses.

## Native job ABI1

The existing application-core library exports separate pairs:

- `narwhal_load_jobs_abi`, `narwhal_load_jobs_json`
- `narwhal_save_jobs_abi`, `narwhal_save_jobs_json`

All descriptors are caller-owned. JSON input/output is bounded to 64 KiB; normal
calls allocate at least 4096 output bytes. Mutation replies fit that minimum.
Small-output rejection never executes a mutation; never replay a mutation after
an unknown transport failure.

Load operations: `create`, `request`, `tick`, `complete`, `hydrated`, `cancel`,
`close`. Rust replies with `start`, `wait`, `hydrate`, `commit`, `cancel`, `error`
or `idle` actions. A load cannot commit merely because its file worker finished.

Save operations: `create`, `enqueue`, `next`, `finish`, `info`, `intended`, `close`.
`info` reports the active and pending opaque payload IDs; `intended` selects the
newest user-save target ID. No geometry or host pointer is dereferenced by Rust.

## Validation and measured limits

Focused tests cover exact files, historical encodings, source-change refusal,
edits during reading/hydration, cancellation, queue replacement, UI heartbeat,
Save repair/AutoSave/emergency behavior, runtime identity and bounds-proof reuse.
Rust mutation probes confirm that breaking load guards or AutoSave priority makes
the tests fail. Packaging checks require the new exports before activation.

Local Linux probes (not universal timing guarantees):

- Validator startup: approximately 1.83 s with full Blender versus 1.10 s with
  bundled Python before the small additional runtime-witness setup cost.
- Previously captured box: first insertion improved from 1.89 s to 0.086 s by
  reusing its private validated capture rather than reopening it immediately.
- Read-only owner-project probe: 11 objects, 8 exact masters; about 6.8 s of
  background preparation with over 600 main-loop ticks, followed by incremental
  host construction. The largest observed construction step was about 0.25 s;
  larger or more complex solids can take longer.

Linux host and actual GUI acceptance are qualified locally. Windows source/ABI
packaging checks and cross-compilation are not Windows runtime certification.
No new packaged release is implied by these source changes.
