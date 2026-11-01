# Rust document registry and coordinated integration

Status: combined product qualification passed:316 top-level passing registrations
(315 distinct labels), across the initial run and nine source-accounted continuations,
not one uninterrupted run. Final publication uses the audited public-source workflow;
no release assets or tags are changed.

## Ownership and exact geometry

Rust now owns model object membership/logical order, name lookup, display associations,
copy-on-write dirty marks, immutable snapshot plans and restore differences. Python
retains exact host geometry/attribute objects and Blender orchestration. The existing
single Undo history still groups geometry, metadata, layers and settings.

The read-only native observer uses the pinned rhino3dm/openNURBS boundary. It detects
retained-call edits that bypass Python watchers, including same-UUID/same-count geometry
replacement and order-only changes. Such unaccounted changes refuse and require explicit
observed-state rebuild. Failed confirmation does not publish a falsely successful state.

Exact OCCT/openNURBS geometry and recipe trees are not replaced by display meshes. The
native observer's canonical comparison bytes are NOT geometry archives or export data.
Redundant native CRC fields and userdata copy counters are normalized. Lazy Brep
solidity/bounding-box caches are evaluated on private copies; document geometry is
not changed. Private dummy points solely own complete attribute snapshots and
never enter a document or export. Full attributes, including plot weight and user text,
now survive snapshot/restore rather than relying only on the old explicit field subset.

No authoritative full-document revision is advertised. External master internals,
selection and separate settings/layer authorities are outside this counter's scope.
Render-light and unsupported native-userdata observation still fail explicitly rather
than silently dropping objects or approximating geometry.

## Integrated agent work

- ACI nearest-colour conversion is native-required; exact standard-colour lookup stays
  a table lookup. Missing/wrong native support does not run a duplicate Python engine.
- Command search prepares ranking keys once, validates bounded inputs, and improves
  adapter packing/cache costs. ASCII semantics, stable duplicate/index ordering and
  existing unsupported-domain compatibility remain. The measured128-name threshold
  is retained; tiny cold calls are not advertised as faster.
- Assistant display eviction is labelled truthfully; narrow review refusal text wraps;
  assistant input uses Unicode. These are UI fixes, not expanded CAD authority. Normal
  Sphere/Cylinder/Cone catalogs remain disabled. The agent's unfinished test matrix is
  not presented as completed development.
- Research's narrow checkbox guard work was reviewed separately. Main sent no guest
  input. An unresolved research input marker must be accounted before research resumes;
  historical idle/PID statements are not fresh input authority.

## Build and platform boundary

Required observer/core libraries are qualified before live constructor use. Source
startup checks the actual native operations before launching graphical code. Developer
builds use pinned source, bounded one-worker compilation and source/artifact receipts.
Release assembly refuses a missing or mismatched observer pair.

Current observer runtime qualification is Linux x86_64/CPython3.13. Windows/macOS source
assembly must wait for separately qualified observer pairs; existing released packages
and release assets are unchanged. A local host build is not a portable release claim.

## Focused evidence

- Live model raw-alias refusal/rebuild, complete attributes, native query/journal ownership,
  no-op sharing, failed adoption and payload-copy refusal have targeted tests and mutations.
-228 document Undo checks and104 analytic geometry Undo/Redo checks passed.
- Metadata integration:19 model and11 actual command tests passed.
- The representation audit passed, including exact recipe-backed fresh-process reopening.
- Native state/ABI/adapter/observer gates include assertion RED/restored-GREEN mutations.
- The initial5000-sphere workload exhausted retention at snapshot17. Per-object immutable
  witness sharing fixed that failure:5000 spheres and50 retained snapshots completed,
  including restoration of both endpoints, without raising the memory limit.

With tracemalloc enabled, the measured5000-sphere workload had472.9ms median /485.4ms p95
edit-snapshot latency and435752KiB process peak RSS. This remains O(document size) work
at explicit snapshot boundaries, NOT a whole-frame speedup. Stable count/display hot
reads do not scan or serialize the whole document. The combined gate also passed the
real editor/popup and transcript tests, both actual OS-close cases, all15 newly added
AI lifecycle cells, viewport/interaction gates, Sweep1 and welcome-style startup.
The separately staged automation-startup gate also passed8/8 cases across both
protocol eras (four positive cases and four intended negative controls).

Integration found and fixed false observation changes from lazy Brep solidity and
bounding-box caches; dedicated tests were seen RED, compiled mutants failed by
assertion, and the unchanged Picture suite then passed45/45 including Split, Trim,
materials, UVs and reopening. The affected model, Undo and analytic geometry gates
were revalidated after that narrow correction.

Other continuation fixes updated stale test assumptions (foreign snapshot transfer,
old native-library hash, newly registered suites and scoped mutation modes), without
removing behavioral assertions. Public audit exceptions for Rust ownership fields
are syntactic and file-scoped; comments, strings and unapproved types remain blocked.
Optional resource/format skips remain explicitly reported; Linux qualification is
not a Windows/macOS runtime claim or live-provider modeling-quality certification.
