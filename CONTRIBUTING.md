# Contributing to Narwhal 3D

Narwhal 3D welcomes focused fixes, tests, documentation, and one-command feature contributions.

## Licence

Narwhal 3D project code is **GPL-3.0-or-later**. By submitting a contribution, you agree that it may be distributed under that licence. Contributions and new dependencies must be GPL-compatible. Third-party components keep their own licences and must be recorded in `THIRD_PARTY_NOTICES` and `licenses/` when applicable.

## Architecture today and the native target

The current 0.x application deliberately uses two layers:

- **Python:** command behavior, document orchestration, application UI, import/export coordination, and most automated tests. Python keeps feature development and behavioral correction fast while the CAD workflow is still evolving.
- **Compiled runtime:** the patched Blender viewport/application runtime and the Open CASCADE geometry kernel. Exact solid operations already run through compiled kernel code; display tessellation is kept separate from document geometry.

After the command set and interaction behavior are feature-complete and stable, the production application layer is intended to be integrated into the Blender source tree and built as one native Narwhal 3D executable. The shipped product should no longer bootstrap as a Python add-on. Python may remain useful for tests, build tooling, and prototypes, while the application shell, command runtime, UI, and performance-sensitive bridges move into the compiled build.

Work contributed now is not throwaway work: command semantics, regression tests, file-format behavior, geometry invariants, and UI contracts define the native implementation that follows.

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

After bootstrapping the locked development runtime:

```bash
./run_tests.sh
./uishot.sh
```

During development, run the narrowest relevant test first. The full suite includes the manufacturing representation audit and a real-window UI acceptance report.

Before submitting:

```bash
find . -name __pycache__ -type d -not -path './.git/*' -not -path './tools/*' -exec rm -rf {} +
git diff --check
```

Do not commit downloaded runtimes, build trees, private documents, generated test artifacts, or third-party material without verified redistribution terms.
