# Contributing to Narwhal 3D

Narwhal 3D welcomes focused fixes, tests, documentation, and one-command feature contributions.

**AI coding agents, and people directing one:** start with [docs/contributing-with-ai.md](docs/contributing-with-ai.md). It has the measured setup steps, the checks that run in a clean clone, the rules every change must follow, and how to pick an issue.

## Licence

Narwhal 3D project code is **GPL-3.0-or-later**. By submitting a contribution, you agree that it may be distributed under that licence. Contributions and new dependencies must be GPL-compatible. Third-party components keep their own licences and must be recorded in `THIRD_PARTY_NOTICES` and `licenses/` when applicable.

## Implementation policy: Rust first

**The active work is replacing Python, not expanding it.** This applies to human and AI contributors now, not only after feature completion.

- Implement new computation, parsing, iteration, selection, projection, data processing, state/queue policy and performance-sensitive logic in the closest Rust crate under `native/`.
- Python is limited to necessary Blender/UI, ctypes, rhino3dm/OCCT host integration and orchestration that Rust cannot reasonably reach yet. Explain the concrete API constraint for every new Python implementation. Existing Python is not permission to add more computational Python.
- Put computational regression engines and case loops in Rust tests. Keep independently captured expectations; Python host/GUI/process tests are appropriate only for the boundary they actually exercise.
- Do not add a second Python implementation or silently replay legacy algorithms when Rust is missing, stale or refuses input. Required native paths must report actionable installation/protocol errors without partially changing the document.
- Retire replaced code only after independent parity, refusal/error, mutation, performance and supported-platform qualification. Preserve originals in a local ignored backup, not executable public reference engines. Do not delete necessary host glue or unconverted behavior to improve language statistics.
- Preserve exact OCCT/openNURBS manufacturing geometry, historical project compatibility and the existing single Undo history. Rust coordination/display acceleration must never substitute a tessellated proxy for exact geometry.
- New or changed native exports must have safe metadata/capacity/error contracts and matching qualified native libraries before live callers are activated. Account for platform math and package/runtime effects; Linux tests alone do not certify Windows.

The long-term goal is **basically zero Python throughout the public repository**:
application code, tests, probes, build/release scripts, tooling and startup code.
Remaining host integration is a migration constraint, not a permanent exemption.
Do not offset application retirement with large new Python validation suites.

At each published conversion checkpoint, report **whole-repository tracked Python
bytes and net change**, separately from application-only changes. Explain any net
increase. Include removal of superseded implementations, redundant tests and unused
helpers once equivalent coverage and real mutation failures are proven; retain
necessary actual-host/API coverage until its replacement is qualified.

GitHub must reflect genuine source removal: never use Linguist exclusions, file
renaming, vendoring or generated-file labels to disguise remaining Python. No
blanket rewrite, relaxed test or deletion of working behavior is acceptable.

## Start with one command

Issues labelled [`good first issue`](https://github.com/ContributionRich/Narwhal3D/labels/good%20first%20issue) are intentionally bounded to one command. Please claim an issue before starting and keep the pull request focused on that command and its tests.

A command contribution should include:

1. A clear prompt and explicit handling for unsupported options.
2. Focused tests covering success, invalid input, selection behavior, and Undo/Redo when the document changes.
3. A mutation check: temporarily break the new behavior, confirm the focused test fails, then restore it.
4. No silent fallback to a different command or geometry type.
5. Updated menu or toolbar wiring only when the command is actually runnable.

## Geometry rule

Manufacturing geometry must remain exact. Curved document geometry must not be replaced with planar facets. Tessellation is appropriate for viewport display, but it must never become the object used for measurement, booleans, save, or manufacturing export.

When a command creates or modifies solids:

- use the existing Open CASCADE master/recipe path;
- preserve analytic surfaces and airtightness;
- refuse unsupported exact operations by name instead of silently producing a mesh;
- add representation and round-trip coverage where relevant.

## Running the checks

Set up as described in [docs/contributing-with-ai.md](docs/contributing-with-ai.md#3-set-up-a-working-copy-linux-x86_64). In short:

```bash
./scripts/bootstrap_dev_linux.sh
./scripts/setup_dev_native.sh
./run_tests.sh
./uishot.sh
```

During development, run the narrowest relevant test first: `scripts/run_suite.sh spike/<suite>.py` runs one suite with the environment `./run_tests.sh` gives it. The full suite includes the manufacturing representation audit and a real-window UI acceptance report.

For a quick check that needs only `./scripts/bootstrap_dev_linux.sh` and the pinned Rust toolchain, run `./scripts/fast_checks.sh`. It builds the pure-core library, runs the packaging self-checks, the pure-core source gate and the suites that work without the patched runtime or the document observer, prints each step's name and exits non-zero if any step fails. It is a subset of `./run_tests.sh`, not a replacement.

Before submitting:

```bash
find . -name __pycache__ -type d -not -path './.git/*' -not -path './tools/*' -exec rm -rf {} +
git diff --check
```

Do not commit downloaded runtimes, build trees, private documents, generated test artifacts, or third-party material without verified redistribution terms.

## Native automation mutation tooling

The Linux-only Rust `native/automation_test_driver` replaces six retired Python
orchestration drivers. `./run_tests.sh` requires format, strict Clippy,
Rust/CLI/source-registration tests and all 71 actual mutations across box, plan,
V4, V6, V7 and V8. Actual SDK clients remain Python and remain required.

For focused invocations, external targets, explicit SDK interpreter, resource/lock
admission, immutable catalogs and optional legacy-fixture limits, see
[the driver guide](native/automation_test_driver/README.md). Never put mutation
targets or scratch worktrees inside the repository. This is source-test tooling,
not a Windows/macOS application capability or a published-release change.

### Authored UI content qualification

Rust UI content/menu changes require the independent content13 typed corpus,
source/provenance guards, actual-host facade tests and eight producer mutations,
as well as all existing editor/popup/transcript checks. `run_tests.sh` registers
the explicit host and mutation targets. Do not replace frozen expected content
with output recaptured from the new implementation. The mandatory UI ABI now
contains 20 exports; old content-less libraries must fail closed.

### Exact joint editing and native close qualification

`narwhal_pure_core_remove_join_cv_v1` is a required additive ABI-1 export. Source,
portable-builder and startup probes must preserve retained coefficients and reject
invalid joints/short outputs without writes. The actual-host regression is
`spike/test_polycurve_joint_delete.py`; it also exercises Delete, Undo and reload.

The Linux close hook is patched by `blender_patches/apply_preclose.rs`. Its Rust
patcher tests are mandatory; an explicitly supplied `NARWHAL_BLENDER_SOURCE` also
runs `spike/test_preclose.rs` against the actual patched C++. A qualified candidate
can be tested without replacing the installed runtime using `NARWHAL_RUNTIME_ROOT`.
The isolated GUI qualifier requires the explicit `NARWHAL_GUI_*` inputs described
by `native/application_core/examples/qualify_preclose_gui.rs`, including exact
application/binary hashes. It creates its own display and processes; never point it
at an owner session. Keep the original candidate-build provenance and record new
integrated-source/release-core qualification separately.
