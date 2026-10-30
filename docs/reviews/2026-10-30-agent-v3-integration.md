# V3 agent integration review — next-release development

## Scope and decisions

This source update builds on public main `a231ac8f33988c78f9c7af542a4d8374403c08a6`.
It does not change the v0.4.8 tag, version or published platform assets.

### Research

GEN20 collected **zero new live cases**: VM startup was memory-blocked. Its
78/78 resolved GEN19 receipt references establish evidence pointers, not 78 new
successful behavior measurements. Conflicting coverage totals need reconciliation.
Historical session identity and pending input must not be replayed.

An earlier same-control-points Degree2/3 experiment supports the bounded Curve
integration: quadratic/cubic real NURBS, matching preview, reduced degree when too
few points are supplied, and degree memory after successful creation. Cancellation
memory is explicitly a conservative Narwhal policy, not a measured reference-CAD claim.
Other degrees are not offered by this limited implementation. Exact geometry
producers are unchanged. Private research screenshots/settings remain private.

### Rust conversion

Main reran all fourteen in-process seam differentials: 14/14 green. That does not
qualify all malformed-response paths. Several prototype clients read an unchecked
native reported length; they remain unregistered. The V3 mutation accounting is
243 RED of 248 attempted, with five survivors requiring precise classification.

The native metadata-import candidate primarily removed quadratic duplicate
search. At 1,600 rows the original Python baseline measured 23.68 ms, a linear
Python comparison 2.20 ms, and the native complete path 5.17 ms. These are focused
harness measurements, not an application-wide speed claim. Production import now
uses a temporary duplicate set, preserves first-error order and overridden Python
constructor/add/get hooks, and retains the Python CSV/Row boundary. No persistent
index or new native port is activated. Existing ACI/printwidths native paths stay.
Other candidates need real consumers and complete-path crossover measurements.

### AI/MCP

Accepted native lifecycle cleanup, bounded diagnostics, recipient pinning,
`narwhal_plan`, `narwhal_verify`, and evidence correlation. Plans do not execute or
approve actions. Verification still explicitly reports incomplete current-geometry
verification; receipt/UUID correlation is not proof of current dimensions/topology.
The chat shows a separate trusted-status row rather than treating provider prose
as execution evidence. Existing separately approved Box/Cube creation remains.

Sphere/Cylinder/Cone publishers remain **disabled prototypes**. Their pending-HTTP
request token is not a qualified accepted-call/human-review lifecycle. Selection
and authoritative revision/component identity and bounded measurements remain
integration prerequisites, not counters to invent. Existing external MCP protocol
compatibility is retained. No new unrestricted geometry or command executor.

## Validation

Focused review completed:
- Plan: 19 tests, 17 compiled mutations RED and restored GREEN.
- Primitive prototype native and real Blender geometry checks, including nine
  rollback subcases and four publisher mutations; this does not enable the tools.
- Sustained lifecycle terminal/capacity/matrix/IDs/diagnostics/catalog controls and
  seven compiled mutations. Fixed an ambiguous completion-expiry mutation anchor.
- HTTP transport: 21 tests, 18 mutations; native crate: 43 library + five binary tests.
- Curve pure checks and 44 existing Curve option assertions.
- Actual Blender: 183 GUI checks of Degree2/3, commit, Undo/Redo and preview draw
  inputs, with forced-cubic-preview and missing-evidence mutations RED→GREEN.
  AI completion was injected into the real hook: this is presentation acceptance,
  not provider/Send/approval end-to-end acceptance or pixel parity.
- Metadata import: four new tests including duplicate-guard mutation and custom
  hooks, plus 17 existing checks. AI evidence helper: three tests including mutation.
- Public manifest closure includes the five new Rust modules and focused tests.

Final curated-source gate: **235/235 suites passed** in one uninterrupted 32m24s
run after correcting the public source manifest and configuring the already-built
B-rep writer. Earlier partial runs stopped at source-audit and missing-helper
failures; they are not counted as passing runs. The unchanged solid suite passed
159/159 with `NARWHAL3D_BREP_WRITER` pointing to the verified existing helper.
The published baseline reproduced the same missing-helper failure.

Fresh-installed AI GUI acceptance passed 111/111. The broad harness explicitly
skips staged-product startup acceptance; that ran separately afterward: **8/8
cases across both MCP eras**, 20 malformed-operation-catalog controls RED, restored
GREEN. Its old Circle-only metadata assertion was corrected to retain the exact
nine-read external catalog while checking approved in-app-only Box metadata and
disabled prototypes. Arbitrary `narwhal.execute` remains refused. Reviewed native
AI binaries were installed only into local development artifacts, with verified
backups; published release binaries were not touched.

Internal opt-in/external-fixture checks remain skipped (icon raster/reference,
Inkscape/converter, TraceDraw and external/embedded CDR integration). Strict Clippy
was not qualified by this review; the agent reported eight findings.

## Next assignments

The owner receives separate local V4 task files for live research, safe Rust
clients/real-call profiling, and the AI backend approval/evidence lifecycle. They
are sequential sustained queues with bounded checkpoints and genuine safety stops,
not promises of all-night progress despite insufficient RAM or unavailable APIs.
No agent is automatically started. No work here changes the frozen release.
