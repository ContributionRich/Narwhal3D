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

The current application still contains Python host integration and unconverted implementations. The goal is a native application with as little Python as possible. No blanket rewrite, relaxed test, hidden language-statistics rule or deletion of working behavior is acceptable.

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
