# Contributing with an AI coding assistant

This page is written for **AI coding agents** and for people who want to point one at this
repository. Everything an agent needs to pick a task, set up, change code and prove the change is
here or linked from here. If you are an agent: read this page completely before editing anything.

## 1. What Narwhal 3D is

Narwhal 3D is free, GPL-licensed CAD for exact curves, surfaces and solids, built for people who
make real, physical objects. It runs inside a patched Blender runtime, but Blender is only the host
window and event loop. The pieces:

| Piece | Where | Role |
|---|---|---|
| Application package | `narwhal3d/` (Python) | Commands, document model, UI glue, file I/O |
| Commands | `narwhal3d/commands.py` (`Command` class, registry `_build_registry()`), `solid_commands.py`, `surface_commands.py`, `display_commands.py`, `import_commands.py` | Prompt-driven commands and their options |
| Exact geometry | OpenCASCADE (OCCT) via `narwhal3d/kernel/occt_backend.py`; openNURBS documents via `rhino3dm` | The geometry that gets saved, measured and manufactured |
| Bridge | `narwhal3d/bridge.py` | Creates and transforms exact geometry for commands |
| Native crates | `native/<crate>/` (Rust), loaded through `narwhal3d/native_*.py` | Parsing, selection, picking, history, document session, UI text, AI/MCP policy |
| Tests | `spike/test_*.py`, `spike/audit_representation.py`, `run_tests.sh` | Behaviour suites, the manufacturing audit, the full gate |

Feature status lives in [`STATUS.md`](../STATUS.md) and [`docs/gaps.md`](gaps.md). The plan is in
[`ROADMAP.md`](../ROADMAP.md). Open work is in **GitHub issues** (section 4).

## 2. Rules every change must follow

1. **Geometry must stay exact.** A cylinder is three faces: two planar circles and one cylindrical
   surface. Never split a curved surface into flat facets, except in intentional mesh commands.
   Tessellating for the *viewport* is fine; a tessellation must never become the saved, measured or
   exported geometry. `spike/audit_representation.py` enforces this.
2. **Solids carry a recipe.** If you add a producer of solids, record how it was built
   (`kernel.record_recipe`) so it rebuilds exactly and survives save and reload.
3. **Move geometry with `bridge.transform_geometry`, never `geom.Transform`.** A bare transform moves
   only the display proxy and leaves the exact master behind.
4. **Nothing unimplemented may look implemented.** If a command or option is not built, it must say
   "<name> is not implemented yet". Never silently do something else.
5. **One user action is one Undo step.** Commands wrap document changes in the existing undo
   boundary (`begin_undo` / `end_undo` on the command context).
6. **Every change needs a test, and you must see it fail.** Add or extend a suite in `spike/`. Then
   re-break the behaviour on purpose, confirm the test goes red, and restore it. A test you have not
   seen fail is not a test.
7. **Never weaken or delete an existing test to make something pass.** If a test blocks you, explain
   in the pull request which of the two is wrong and why.
8. **Rust first for computation.** New parsing, picking, searching, iteration and other hot-path
   logic belongs in a Rust crate under `native/`. Python is for Blender/UI glue and orchestration.
   Rust may accelerate display and coordination, but never replaces exact OCCT/openNURBS geometry
   with an approximation.
9. **Public text rules.** Do not name the commercial CAD product this project is modeled on (say
   "the reference CAD application" if you must). Do not commit local paths, downloaded runtimes,
   build output, private documents or third-party material without clear redistribution terms.
   `python3 scripts/audit_public_tree.py --root . --git-ref HEAD` checks a commit.
10. **Clear stale bytecode** before trusting a result after edits:
    `find . -name __pycache__ -type d -not -path './.git/*' -not -path './tools/*' -exec rm -rf {} +`

## 3. Set up a working copy (Linux x86_64)

Measured on a fresh clone in November 2026: about 455 MB of downloads, 2.4 GB on disk, under two
minutes plus cargo builds. You need `git`, Python 3, `curl`, and Rust via `rustup` with the pinned
toolchain (`rustup toolchain install 1.95.0`).

```bash
git clone https://github.com/ContributionRich/Narwhal3D.git
cd Narwhal3D
./scripts/bootstrap_dev_linux.sh        # official Blender 5.2.2 runtime + pinned rhino3dm/OCP wheels
R=$PWD
for c in pure_core selection_core platform_core application_core ui_core document_core; do
  cargo +1.95.0 build --release --locked --manifest-path native/$c/Cargo.toml
done
export NARWHAL_PURE_CORE=$R/native/pure_core/target/release/libnarwhal_pure_core.so
export NARWHAL_SELECTION_CORE=$R/native/selection_core/target/release/libnarwhal_selection_core.so
export NARWHAL_PLATFORM_CORE=$R/native/platform_core/target/release/libnarwhal_platform_core.so
export NARWHAL_APPLICATION_CORE=$R/native/application_core/target/release/libnarwhal_application_core.so
export NARWHAL_UI_CORE_LIBRARY=$R/native/ui_core/target/release/libnarwhal_ui_core.so
export NARWHAL_DOCUMENT_CORE=$R/native/document_core/target/release/narwhal-document-core
export LD_LIBRARY_PATH=$R/tools/blender-5.2.2-linux-x64/5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs
P=$R/tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13    # Python with rhino3dm + OCCT
B=$R/tools/blender-5.2.2-linux-x64/blender                      # headless runner: $B -b --factory-startup --python-exit-code 1 --python <file>
```

### Known setup gaps (fixing these is the most valuable work right now)

A clean clone **cannot yet** build the *document observer*, a small C++ extension that every
document-based suite and the app itself need. The official runtime ships no CPython 3.13 headers.
Until that is fixed, the full `./run_tests.sh` gate, the GUI launch and most command suites cannot
run in a clean clone. These issues carry the label
[`area: onboarding`](https://github.com/ContributionRich/Narwhal3D/issues?q=is%3Aissue+is%3Aopen+label%3A%22area%3A+onboarding%22);
start there if you can.

## 4. Choose a task

The pinned issue [#101 "Start here"](https://github.com/ContributionRich/Narwhal3D/issues/101) lists the current priority order.

- **[`agent-ready`](https://github.com/ContributionRich/Narwhal3D/issues?q=is%3Aissue+is%3Aopen+label%3Aagent-ready)**:
  fully specified. Expected behaviour, files to read and the test to extend are in the issue. An
  agent can complete these autonomously.
- **[`status: needs-info`](https://github.com/ContributionRich/Narwhal3D/issues?q=is%3Aissue+is%3Aopen+label%3A%22status%3A+needs-info%22)**:
  part of the behaviour is not defined yet. Do this first: post a short design comment on the issue
  (inputs, prompts, defaults, result, how you will test it), or put it at the top of your pull
  request. Mark behaviour you chose yourself with an `OURS:` code comment so reviewers can find it.
  Keep the change small enough to review.
- **Umbrella issues** (checklists of missing commands, label `roadmap`): pick one item, open a new
  issue for it that links the umbrella, then work on that.
- Areas: [`area: onboarding`](https://github.com/ContributionRich/Narwhal3D/issues?q=is%3Aissue+is%3Aopen+label%3A%22area%3A+onboarding%22) ·
  [`type: command`](https://github.com/ContributionRich/Narwhal3D/issues?q=is%3Aissue+is%3Aopen+label%3A%22type%3A+command%22) ·
  [`type: rust-port`](https://github.com/ContributionRich/Narwhal3D/issues?q=is%3Aissue+is%3Aopen+label%3A%22type%3A+rust-port%22) ·
  [`area: ai-mcp`](https://github.com/ContributionRich/Narwhal3D/issues?q=is%3Aissue+is%3Aopen+label%3A%22area%3A+ai-mcp%22) ·
  [`area: mesh-subd`](https://github.com/ContributionRich/Narwhal3D/issues?q=is%3Aissue+is%3Aopen+label%3A%22area%3A+mesh-subd%22) ·
  [`area: drafting-render`](https://github.com/ContributionRich/Narwhal3D/issues?q=is%3Aissue+is%3Aopen+label%3A%22area%3A+drafting-render%22)

Comment on the issue when you start, so two agents do not do the same work.

## 5. Make the change

**Adding or finishing a command.** Read an existing command of the same kind first: the
visibility commands (`HideCmd`, `LockCmd`) for selection-only commands, `CircleCmd` for curve
construction with options, a primitive in `solid_commands.py` for solids. A new command is a
`Command` subclass registered in `_build_registry()` in `narwhal3d/commands.py`. Toolbar buttons
map to commands through `narwhal3d/toolbar_macro_data.py`; an unregistered key shows "not
implemented yet". Options are declared with `opts.Option(...)`; an option without a handler must
keep refusing by name.

**Moving code to Rust.** Add the function to the closest crate in `native/`, expose it through the
crate's C ABI, call it from the matching `narwhal3d/native_*.py` wrapper, and add Rust unit tests
(`cargo +1.95.0 test --manifest-path native/<crate>/Cargo.toml`) plus a Python suite that compares
the Rust result with the old behaviour before you remove the Python version.

## 6. Prove it works

Run the narrowest suite first.

| What | Command (after section 3) |
|---|---|
| Rust crate tests | `cargo +1.95.0 test --manifest-path native/<crate>/Cargo.toml --locked` |
| Public-tree and docs checks | `$P spike/test_public_project.py` |
| Pure suites (examples) | `$P spike/test_macro.py`, `$P spike/test_coords.py`, `$P spike/test_numparse.py`, `$P spike/test_toolbars.py` |
| Blender geometry suites (examples) | `$B -b --factory-startup --python-exit-code 1 --python spike/test_boolean.py` (also `test_sweep.py`, `test_loft.py`, `test_tessellate.py`) |
| Document-based command suites | Need the document observer (see section 3). If you cannot run them, say so in the pull request; never claim they passed. |
| Full gate (maintainers, or once onboarding is fixed) | `./run_tests.sh` |

Look in `run_tests.sh` for the exact arguments and environment each suite expects.

## 7. Open the pull request

- One issue per pull request; reference it (`Fixes #123`).
- Describe what changed, which tests you ran with their output, the mutation you used to see the
  new test fail, and anything you could not run.
- Commit messages describe the change in plain words, with no product names of other CAD software.
- Do not commit `tools/`, `vendor/`, `native/*/target/`, `artifacts/`, or other generated files.
- Keep unrelated refactors out.
