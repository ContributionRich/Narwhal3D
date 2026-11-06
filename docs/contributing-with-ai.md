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

Measured on a fresh clone in November 2026: about 900 MB of downloads, about 5 GB on disk, and
roughly five minutes on a fast connection. You need `git`, `curl`, Python 3 with `venv`, CMake,
`make`, a C and C++ compiler, Rust via `rustup` with the pinned toolchain
(`rustup toolchain install 1.95.0 --component rustfmt,clippy`), and a desktop Linux session with
systemd (the native gates use `systemd-run --user`). podman or docker is used when available.

```bash
git clone https://github.com/ContributionRich/Narwhal3D.git
cd Narwhal3D
./scripts/bootstrap_dev_linux.sh   # official Blender 5.2.2 runtime + pinned rhino3dm/OCP wheels
./scripts/setup_dev_native.sh      # native components: see below
./narwhal3d.sh                     # launch the app
./run_tests.sh                     # the full gate (about an hour; GUI suites need a display)
```

`setup_dev_native.sh` downloads only hash-pinned inputs (`packaging/linux-x86_64-dev.lock.json`)
and stages everything in gitignored paths:

- CPython 3.13.13 headers (`tools/cpython-3.13-include`), configured from the python.org source;
- the patched application runtime (`tools/blender-patched`), extracted from the pinned published
  AppImage, so GUI suites behave like the released app;
- the pinned OCCT source (`vendor/occt-src`) and the Rust geometry core;
- the Rust cores, the application core, the UI core and the automation library;
- the qualified document observer (`artifacts/document-observer`);
- the exact-save helper (`artifacts/brep-writer`) and a Pillow environment for icon tests.

It ends by running the application's own native startup checks. It is safe to re-run; finished
steps are skipped. `run_tests.sh` and `narwhal3d.sh` find all of these without extra variables.

For quick iteration run one suite with `scripts/run_suite.sh`. It exports the environment
`run_tests.sh` gives the suites (the Rust cores, the application core, the exact-geometry
runtime) and runs the suite in headless Blender when `run_tests.sh` does, else in the runtime's
Python:

```bash
scripts/run_suite.sh spike/test_macro.py
scripts/run_suite.sh spike/test_boolean.py       # a BLENDER_SUITES entry: runs in Blender
```

Run with the bare interpreter, suites that need a Rust core fail instead (for example
`spike/test_numparse.py`: "Required Rust number parser unavailable"). Look in `run_tests.sh`
for any extra arguments a suite expects.

### Known setup limits

- Linux x86_64 only for now. Containers without a systemd user session cannot run the native gates
  yet (they bound compiler memory with `systemd-run --user`).
- There is no automatic CI on pull requests yet; run the checks locally (see
  [`area: onboarding`](https://github.com/ContributionRich/Narwhal3D/issues?q=is%3Aissue+is%3Aopen+label%3A%22area%3A+onboarding%22)).

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
| One suite (Python or Blender-backed) | `scripts/run_suite.sh spike/<suite>.py` |
| Public-tree and docs checks | `tools/blender-patched/5.2/python/bin/python3.13 spike/test_public_project.py` |
| Fast checks (bootstrap only) | `./scripts/fast_checks.sh` |
| Full gate | `./run_tests.sh` (stops at the first failing suite) |

Look in `run_tests.sh` for any extra arguments a suite expects.

## 7. Open the pull request

- One issue per pull request; reference it (`Fixes #123`).
- Describe what changed, which tests you ran with their output, the mutation you used to see the
  new test fail, and anything you could not run.
- Commit messages describe the change in plain words, with no product names of other CAD software.
- Do not commit `tools/`, `vendor/`, `native/*/target/`, `artifacts/`, or other generated files.
- Keep unrelated refactors out.
