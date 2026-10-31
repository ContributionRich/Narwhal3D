# First Rust-owned document-session slice

This is a development-source change, not a new packaged release. Published0.4.8
assets and version tags are unchanged.

## What moved

`native/application_core` now owns persistent document user text: ordered keys,
native-compatible key matching, staged writes, commit/abort, mutation generations
and immutable snapshot contents. It is a stateful application core, not a stateless
helper that receives and returns the whole table on every operation.

The actual SetDocumentUserText/GetDocumentUserText commands use it. Existing
File3dm.Strings callers get a compatibility view; native File3dm object identity is
preserved. Unowned File3dms keep their original behavior. Serialization materializes
the Rust state into the file projection. A raw native table altered behind the
view is detected and refused, never silently merged or overwritten.

Snapshots retain native state handles and participate in the existing unified
metadata/geometry Undo/Redo history. No separate metadata undo stack was added.
Failed restoration does not advance history; stale/foreign snapshots are rejected
before changing layers. Reentrant metadata/adoption callbacks are refused before
inner mutation, and case-variant no-ops no longer create false history records.

Internal layer/group round trips preserve the logical metadata session. External
document adoption creates a new session and retires stale views. An escaped file
whose former model was collected can be transferred without losing unsaved metadata;
a file belonging to another live model cannot be taken over.

## Persistence and compatibility

- Whole-document .3dm Open and SaveAs now preserve document strings, including
  metadata-only files. Selected-object Export/Import remain separate operations.
- .narwhal save/reopen uses the same serialization boundary and retains exact geometry.
- Native ordinal casing is generated from pinned openNURBS logic, exhaustively checked
  over Unicode scalars. Get remains exact-case; Set/Delete use native ordinal matching.
- Embedded NUL is handled at the compatibility interface as the native API's measured
  string terminator; the Rust protocol itself rejects embedded NUL rather than truncating.
- Missing default application-core chooses the complete legacy File3dm route at
  document adoption. A configured but broken library is an error. A live Rust session
  never silently falls back to a separate Python table.
- Future Linux/Windows/macOS packages require the new library, ABI and license notices;
  package omission/hash/ABI checks were added. See `docs/application-core-packaging.md`.

The local Linux development library is staged under artifacts/application-core.
Existing already-open legacy documents are not silently migrated in place; new
models or reopening use the library when available.

## Validation

- Rust:20 debug and release tests,28 isolated mutants killed, strict Clippy/format.
- Native binding:24 tests,13 in-memory mutants caught; malformed buffers, stale
  handles, snapshot ownership and no mutation replay.
- Model/application integration:29 tests,13 mutation controls RED→restored GREEN;
  actual Blender command execution, mixed Undo/Redo, saved state, both formats,
  replacement, callback failures, stale aliases and orphan-file transfer.
- Core build/staging gate:20 tests with4 mutation controls.
- Packaging:14 tests with13 mutation controls, existing platform contract checks.
- Final uninterrupted public-candidate `./run_tests.sh`: **242 gates passed**,
  **zero failures**,31m59.8s. All199 monitored source hashes unchanged.
- Cargo cross-checks passed for x86_64-pc-windows-gnu and aarch64-apple-darwin.
  These are compile checks, NOT MSVC linking, native OS execution or package acceptance.

The full run retains explicit optional/external skips: staged-product automation
startup acceptance, optional icon raster/reference checks, unavailable Inkscape/
PDF/AI converters, real TraceDraw certification and external/embedded CDR fixtures.
No skipped case is presented as passing. Earlier partial gate attempts exposed
packaging assertions, a test's implicit cross-document cloning shortcut and orphan-
file adoption; those were corrected before the final uninterrupted pass.

## Performance boundary

Instrumentation verified zero row exports during24 native snapshots,128 key reads
and64 indexed reads. Snapshot wrappers held80 bytes of Python state in that probe,
not copies of the metadata table.

A separate synthetic real-model probe (200 calls, local bundled Python, no Blender
rendering) measured native exact-key lookup medians around15.4µs and model snapshot
medians around100µs for both10 and1,000 metadata entries. These are small controlled
workloads, not whole-application or cross-platform speed claims. The improvement is
about persistent ownership and avoiding repeated native-table enumeration; it is
not evidence that every Rust implementation automatically beats Python.

Actual writes still clone table containers while sharing string allocations; this
is not yet a persistent-tree implementation. Native snapshot/restore ownership is
O(1), but full model restore can also validate/project metadata and restore geometry.
Compatibility projection checks intentionally retain work needed to detect raw escapes.

## What has not moved yet

Object/layer/group ownership, command execution, the complete transaction engine
and frontend remain in their existing locations. The generation exposed here is
**document-user-text scoped**, not an authoritative whole-document revision or
selection/topology token. OCCT/openNURBS remain the exact geometry authority.

Next: extend this same persistent core through another real ownership slice,
starting with explicit object/layer records and transaction contracts, rather than
returning to isolated helper-by-helper conversion. Platform runtime acceptance is
still required before shipping the new component in release packages.
