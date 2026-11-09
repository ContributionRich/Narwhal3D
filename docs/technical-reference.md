# Narwhal 3D — technical reference

[← Back to the overview](../README.md)

Build commands below run from the repository root. For a quick introduction and current workspace screenshots, see the main README.

For the Linux, Windows and Mac release pipeline, use
[the current release procedure](../packaging/RELEASING.md). The detailed 0.4.7 asset
hashes, commands and acceptance results below are historical baselines, not proof
that a newly assembled package passed those gates.

Narwhal 3D is a free, open-source, command-driven NURBS CAD application. Release downloads are available for Linux x86_64 (AppImage) and as an early Windows x86_64 preview (portable EXE), both for 0.4.11. No Mac build has been made for this line. It ships as one application bundle: a Narwhal launcher and interface on top of a bundled, open-source viewport runtime.

**Licence:** [GPL-3.0-or-later](../LICENSE). Contributions and dependencies must be GPL-compatible; see [CONTRIBUTING.md](../CONTRIBUTING.md).

> **Project status:** extremely early prerelease. Save important work often and verify manufacturing output before production use. Release numbering is intentionally frozen on the `0.4.x` line until the project owner explicitly lifts that cap.

[Current status](../STATUS.md) · [Roadmap](../ROADMAP.md) · [Feature gaps](gaps.md) · [Windows build preparation](windows-build.md) · [Experimental macOS source build](macos-build.md) · [Compatibility and independence](../COMPATIBILITY.md) · [Contributing](../CONTRIBUTING.md) · [Support](../SUPPORT.md) · [Security](../SECURITY.md)

## Command workflow

The command line, viewport picks, menus, and toolbars all enter the same command system. The [archived Loft demonstration](readme-command-loft.gif) shows this workflow in an older UI; it is not a screenshot of the current release. Current Loft styles and multi-curve support are described in the [changelog](../CHANGELOG.md).

## Geometry model

Narwhal keeps document geometry separate from viewport tessellation:

- Curves and analytic primitives are stored as NURBS/Brep document objects.
- Open CASCADE (OCCT) provides exact booleans, fillets, sweeps, STEP I/O, and other solid operations.
- Triangles generated for shaded display are not treated as manufacturing geometry.
- STEP export refuses geometry that has no exact analytic representation.

Narwhal's active project format is `.narwhal`: a dependency-free Rust-validated container holding the complete `.3dm` metadata/document payload plus exact checksum-protected OCCT BReps for every analytic master. It reopens solids directly from those exact payloads without the optional openNURBS writer or recipe reconstruction. `.3dm` remains a supported Import/Open/Export interchange format and uses the MIT-licensed `rhino3dm` package. The public source includes Narwhal's independently authored, bounded ONBREP helper and a build recipe pinned to an exact official openNURBS revision. The Linux and Windows releases (0.4.10 onward) bundle the linked helper: official documentation says commercial use is encouraged and unrestricted while the repository `LICENSE` says “All Rights Reserved”, and the project owner decided on 2026-11-02 to redistribute on the basis of the documentation. The macOS package does not bundle it yet; there some OCCT-created multi-face solids cannot be encoded as exact `.3dm` Breps. Narwhal reports the limitation when saving; use STEP for exact solid interchange. It never presents a faceted STEP fallback as exact geometry.

## Install on Linux x86_64

**Release: 0.4.7.** The Linux x86_64 asset is `Narwhal_3D-0.4.7-x86_64.AppImage`.

The 0.4.7 AppImage has been built and its inventory verified. Final Linux source release gates, including the full suite, and AppDir/extracted-AppImage verification passed. Actual extracted-package GUI checks passed on NVIDIA/OpenGL, including Gumball extrusion then scale (24/24), rendering (47/47), preferences write/restore (183/193 checks), and window overlays (105/105). All 290 bundled ELFs were measured to require at most glibc 2.28, below the Ubuntu 22.04/glibc 2.35 compatibility ceiling enforced by the release verifier. This does not certify a pristine Ubuntu desktop or optional GPU backends; those checks remain open. The AppImage is unsigned.

Make the AppImage executable and run it directly:

```bash
chmod +x Narwhal_3D-0.4.7-x86_64.AppImage
./Narwhal_3D-0.4.7-x86_64.AppImage
```

The AppImage contains the native startup welcome window, natively branded runtime, all nine mandatory native components, locked Python dependencies, application package, icons, and license notices. It does not install a separate host application.

## Early Windows x86_64 preview

**Release: 0.4.11.** The Windows asset is `Narwhal_3D-0.4.11-Windows-x86_64.exe`, a portable EXE published on the v0.4.11 release with a `.sha256` checksum. No ZIP is published for this line; 0.4.8 ZIP users download the EXE once. The EXE manages its payload in a per-user cache, so keep documents outside it. It was built from source commit `25572ef7f70acc5e3361ec8c1d614631e9e0f6ba` (the tagged v0.4.11 source plus a Windows-only Save fix), includes the qualified document observer and the exact-save helper `narwhal3d/kernel/bin/brep_writer-win32-amd64.exe`, and carries its openNURBS license at `licenses/openNURBS-LICENSE.txt`. It is unsigned. Its acceptance ran in a Windows 11 virtual machine with a software OpenGL driver, not on a physical GPU. Its byte size and SHA-256 are in the release's `.sha256` file and are not repeated here. See the [platform evidence and limitations](windows-build.md).

**Historical 0.4.7 ZIP.** The Windows asset name for 0.4.7 is `Narwhal_3D-0.4.7-Windows-x86_64.zip`. Extract the complete folder and start `Narwhal3D.exe`; the native welcome window remains visible while the bundled runtime initializes. Do not move that executable away from its adjacent `app` and `runtime` folders.

This is an unsigned, portable **early preview**, not an installer or supported production build. Windows SmartScreen may require **More info → Run anyway**. The 0.4.7 engineering build completed using a verified, unchanged native runtime; directory and ZIP verifiers passed. Actual extracted-final-ZIP WinBoat acceptance passed: Gumball mouse press/drag/release (24/24), preferences write/restore (184/194 checks), window overlays (112/112), and all six representation gates. GUI acceptance used temporary Mesa llvmpipe software rendering, not a simulated UI; it is **not physical-GPU certification**. The temporary driver was removed and the exact package inventory restored; it is not bundled. Physical-GPU acceptance remains open. The launcher has original Narwhal icon/version resources, but the internal viewport runtime retains some Blender-derived PE resource strings/icons. There are no file associations or automatic installation yet. See the [platform evidence and limitations](windows-build.md).

## Release source and integrity

Both 0.4.7 shipping assets were assembled from public source revision `94534c954dc77f1f1d22f2be6064bedcc8cd1f5b`, source epoch `1793144099`. The release tag identifies that asset-build revision; this documentation follow-up is separate from the build, not a repackage or a change to the assets' source identity.

| Asset | Bytes | SHA-256 |
|---|---:|---|
| `Narwhal_3D-0.4.7-x86_64.AppImage` | 437,832,184 | `6097aa60cdc8702e59370f83b66d13a6de42da2c3b10c0a4cc3cded753cd67a9` |
| `Narwhal_3D-0.4.7-Windows-x86_64.zip` | 355,197,176 | `23015f7338358f7fd62fcabada325bd615d06f0eb9f0898cac3279d0eb638290` |

SHA-256 checks integrity, not publisher authentication. Neither asset is cryptographically signed.

## Experimental macOS source build

Narwhal does not yet publish a macOS application. Mac users on macOS 14 or newer can try the [experimental source-build guide](macos-build.md), which builds the pinned Blender runtime and Narwhal Rust helpers for the Mac's current architecture. Exact CPython 3.13 geometry wheels are available for both Apple Silicon and Intel.

The result is an unsigned, unnotarized community build—not a supported release or universal binary—and has not yet passed Narwhal's macOS GUI/GPU, exact-geometry, save/reload, packaging, signing, or notarization gates. Reports from people who compile it are welcome.

## Develop from source

Requirements: Python 3, Bash, HTTPS access, a C/C++ build toolchain, CMake, Ninja, and enough disk space for the runtime and source build.

```bash
./scripts/bootstrap_dev_linux.sh
./scripts/setup_dev_native.sh
```

`setup_dev_native.sh` builds the native development components (CPython headers, Rust cores, the document observer, the exact-save helper) and extracts the patched runtime from the pinned published AppImage into `tools/blender-patched/`, so a source checkout can launch and run `./run_tests.sh`. See [the contributor guide](contributing-with-ai.md#3-set-up-a-working-copy-linux-x86_64).

This development bootstrap downloads only SHA-256-locked artifacts and installs the locked wheels into an official development runtime. That runtime is useful for iteration but is deliberately rejected by release assembly because it is not natively branded.

### Current Python layer and native destination

During the 0.x feature-development phase, command behavior, document orchestration, UI, and most tests live in Python. Exact solid operations already use the compiled Open CASCADE kernel, while a small set of Blender source patches provides native application identity and CAD-specific viewport behavior.

After the command set and interaction behavior are feature-complete and stable, the production application layer is intended to move into the Blender source tree and compile as one native Narwhal 3D application. The final product should not require a separately installed or bootstrapped Python add-on. Python may remain for tests, build tooling, and prototypes. Work contributed now remains durable as the behavioral specification, regression coverage, file-format logic, and geometry contract for that native implementation.

See [CONTRIBUTING.md](../CONTRIBUTING.md) for the architecture boundaries, exact-geometry rules, and one-command contribution workflow.

`scripts/build_blender_linux.sh` builds the publishable runtime from the exact Blender 5.2.2 source commit and applies the Narwhal title, icon, app-drawn-object, and CAD-boundary patches. See `packaging/README.md` for the required arguments and the current native-build provenance limitation. Install the three wheels from the locked cache into the resulting runtime before packaging.

Once `tools/blender-patched/` exists, run:

```bash
./narwhal3d.sh
./narwhal3d.sh --install
```

## Build a one-click AppImage

The examples below build **0.4.7**. Use the audited public release source revision and the portable runtime containing all current native patches, plus matching native components and the locked appimagetool. The 0.4.7 portable Linux runtime and welcome have been built and verified; the current GLIBC 2.43 source-development runtime and welcome binary are separate inputs and must not ship. The historical 0.4.6 runtime cannot stand in for the new UI APIs. All bundled ELFs must pass the GLIBC 2.35 ceiling before assembly and after extraction.

```bash
python3 scripts/fetch_locked.py \
  --cache-dir ~/.cache/narwhal3d/artifacts \
  appimagetool appimage-runtime

REVISION=$(git rev-parse HEAD)
EPOCH=$(git show -s --format=%ct HEAD)

python3 scripts/assemble_linux_release.py \
  --version 0.4.7 \
  --source-revision "$REVISION" \
  --source-date-epoch "$EPOCH" \
  --source-root . \
  --runtime-dir tools/blender-patched \
  --native-components-dir runtime/linux-x86_64/bin \
  --document-core-artifacts artifacts/document-core \
  --appimagetool ~/.cache/narwhal3d/artifacts/appimagetool-x86_64.AppImage \
  --appimage-runtime ~/.cache/narwhal3d/artifacts/runtime-x86_64 \
  --output-dir dist

python3 scripts/verify_linux_release.py \
  dist/Narwhal_3D-0.4.7-x86_64.AppImage \
  --expected-version 0.4.7 \
  --expected-source-revision "$REVISION" \
  --expected-source-date-epoch "$EPOCH"
```

Assembly fails closed unless the native binary identity, patch hashes, source commit, Python dependencies, notices, paths, and symlinks all pass. It emits the AppImage, a SHA-256 sidecar, and a retained AppDir for diagnostics.

## AI assistant and MCP

The in-app assistant accepts four provider identifiers: `openai`, `anthropic`, `openai_compatible`, and `openai_responses`. See the [provider registry](../narwhal3d/ai_assistant.py#L23-L29).

Sharing workspace metadata requires explicit consent for the configured recipient. The assistant includes bounded selected-object metadata with each message. To revoke remembered consent, open AI Settings and choose **Forget workspace-sharing permission**. See the [permission model](../SECURITY.md#ai-permissions-in-current-source) and [settings control](../narwhal3d/ai_settings_popup.py#L353).

A tool-capable model can request one new world-aligned box or cube. Creating it requires a separate, one-use human approval in the app. Circle is proposal-only. Sphere, cylinder and cone are disabled, and the assistant has no executor for curve, loft, pipe or Boolean operations. It cannot edit selected objects. Logical cancellation invalidates stale responses, but physical HTTP cancellation is not guaranteed. See the [source status](../STATUS.md#current-source-and-0411-feature-set), [disabled action catalog](../native/automation_core/src/catalog.rs) and [changelog](../CHANGELOG.md).

The external MCP launcher uses Unix stdio and requires `--read-only`. It starts one new app instance per launch and supports metadata reads only. It cannot attach to an existing app or execute modeling actions. See the [launcher](../scripts/narwhal_mcp.py) and [source status](../STATUS.md#current-source-and-0411-feature-set).

The [read-tool catalog](../native/automation_core/src/catalog.rs#L8-L54) defines these nine dotted MCP names:

- `narwhal.application_info`
- `narwhal.document_summary`
- `narwhal.selection_summary`
- `narwhal.object_summary`
- `narwhal.objects`
- `narwhal.layers`
- `narwhal.workspace`
- `narwhal.components`
- `narwhal.operations`

## Sketch previews and grouped picking

Curve-sketch previews follow the current layer's colour, with the same theme-wire fallback
as completed curves on black layers. Circle 2Point and 3Point previews use the exact commit
solver, including tilted three-point planes; a collinear third point draws no circle.
The preliminary endpoint guide is application-defined because no captured hover evidence
covered that stage.

An ordinary click selects a group as one unit, and overlapping members of that group are
one overlap-menu choice. Unrelated overlapping objects remain distinct choices. Shift-click
selects individual group members as an application-specific shortcut; the existing
Ctrl+Shift sub-object route remains available. Selecting members never dissolves the group.

## Curve Rebuild dialog

`Rebuild` opens a curve-rebuild window after selection (a preselected curve opens it
immediately); `-Rebuild` retains the command-line workflow. The window has point count,
degree, Delete input, Create new object on current layer, Preserve end tangent directions,
OK and Cancel. Point count and degree adjust each other so point count is at least degree
+ 1. Editing settings does not run a fit. Cancel makes no document change, and OK uses the
same fitting implementation and single document Undo history as the command line.
The dialog intentionally has no Preview section. Changing the document while the window
is open invalidates it.

Point count 10 and degree 3 were observed in a captured dialog; they are starting values,
not a claim about factory defaults. Checkbox starting states are application defaults.
Master-curve selection, SubD-friendly rebuilding and surface rebuilding are not implemented;
the window says so explicitly. Live remeasurement of the reference dialog was blocked by
the VM's memory admission limit; the controls follow the recorded dialog and offline help.

Rebuild prepares immutable, per-operation arc-length and closest-point data instead of
re-integrating and repacking each sample. NURBS closest-point searches reuse the Rust core;
analytic and missing-core compatibility routes remain available. The prepared objects do
not retain stale geometry after edits. Fit control points, knots and deviation are compared
bit-for-bit against the previous pipeline in the regression suite.

The Rebuild window uses the application's themed popup and native-backed text editing.
The Point count and Degree fields show separate **Current** readouts for the input curves;
mixed selections show **varies**. These stay distinct from the requested output settings.
Rebuild and Print are centered over the combined modeling workspace, not inside one pane.
They use the existing native window-overlay bridge above editor dividers; the viewport
layout and cameras are not rearranged. Dialog input uses the same window coordinates as
painting, including controls that lie over different panes. A runtime without the native
window-overlay capability reports that explicitly rather than pretending to open a dialog.

View > Interface Scale saves a percentage or Automatic for the next startup. The menu
and confirmation state that a restart is required: changing only fonts and hit targets
without resizing panel areas would clip the running interface. Window resizing keeps
the current session's choice until restart. An explicit `NARWHAL3D_UI_SCALE` environment
override still takes precedence.

## Selection, Import and Print

- `Length` measures selected solid edges using their exact kernel curves, including
  circular edges, not the sampled display polyline. Shift-click can pick an edge on an
  already-selected ungrouped solid or during the Length selection prompt. Group-member
  Shift selection and the existing component-selection chord remain available.
- `Import` opens the file browser immediately. Inline filenames still work; a typed
  filename prompt remains available when no native browser can be opened. Cancel does
  not modify the document; successful imports use one Undo record.
- Print starts from the active pane rather than a previous dialog's remembered view;
  saved paper and scale settings remain available. Toggling Only selected objects updates
  the preview page immediately. Labels and dropdowns use measured text widths and stay
  within the combined workspace at supported dialog sizes. With Area set to
  Viewport, Fit fits that view; use Extents and Only selected objects to fit a selection.
  Perspective printing remains explicitly unsupported; select
  a parallel view such as Top, Front or Right in the Print dialog.

## Native saved-file compatibility contract

A saved `.narwhal` project is a permanent wire format, not a snapshot of the current
viewport renderer. New readers must retain schema-1/container-1 support. A release may
add a schema only with an explicit reader/migration and permanent historical fixtures;
never reinterpret or silently overwrite an older format. Unknown future encodings fail
explicitly, leaving the source file and active document unchanged.

- Application and OCCT **producer versions are metadata**, not equality locks on reading.
  The actual supported binary encoding controls admission. BinTools versions 1, 2, 3 and 4
  are exercised with real encoded exact solids, not only modified version labels.
- OCCT masters are authoritative. Container/master hashes, stable object IDs, recipes,
  topology, closure, bounds and volume are validated in the isolated worker before the
  candidate document is published. No master is replaced by display triangles.
- Historical triangulated proxies are compared as oriented piecewise-planar surfaces by
  the dependency-free Rust document core. Face/edge numbering and coplanar diagonals do
  not change a surface. Changed nonplanar faces, missing/duplicate faces and reversed
  winding do. The fixed float32 precision budget belongs only to legacy render vertices,
  never to manufacturing geometry. Proxy codecs 1 and 2 are frozen separately from
  viewport tessellation settings and must be retained, as must their historical coarse
  retry profiles (codecs 3 and 4).
- Exact-proxy correlation compares matching NURBS representations. OCCT's integral of
  an extrusion surface can differ from that of its exact NURBS form; this must not be
  mistaken for file corruption. The stored master and its historical summary are retained.
- Older additive boxes that omitted the document-form marker are admitted only after
  full exact-proxy correlation, then receive the explicit exact marker in the candidate.
  New assistant boxes write that marker at publication, including for exit recovery.
  Unknown nonempty form markers remain refused.
- A narrow legacy planar/BSpline recognition migration uses the original bounded sewing
  tolerance and independently correlates both representations with the saved proxy.
  It never converts or simplifies the authoritative saved surfaces.
- Writers pin a tested binary encoding rather than blindly adopting a newer kernel's
  CURRENT value. Historical summary measurements have a frozen `schema1-v1` codec,
  independent of changes to normal measurement commands.
- Save runs the reader's admission checks on private staging **before** atomic replacement,
  so a newly serialized unreadable document cannot replace a previous good save.
  Open validates into an inert candidate; failed validation
  cannot partially install geometry or replace the current document.

`spike/fixtures/document_schema1_historical.json` contains fixed, SHA-256-pinned bytes from
an earlier writer with synthetic moved-display and analytic-extrusion cases. **Do not
regenerate it when implementation changes.** Append fixtures for new formats instead.
`spike/test_document_compatibility.py` is part of the full gate, alongside the unchanged
corruption and same-bounds/volume substitution tests in `spike/test_narwhal_document.py`.
The native comparator capability is qualified before use; an old pack/unpack helper cannot
silently satisfy the new hook.

Open shares the exact intersection preparation for reciprocal solid-correlation checks
and avoids rebuilding already-completed cuts. Both directional checks remain mandatory.
A measured eight-master project opened in about 11 seconds cold, versus 20 seconds before
this change; timing depends on geometry and hardware. Isolated exact validation remains
part of Open and is deliberately not bypassed for speed.

## Tests

Fast public model tests can be run with the bundled interpreter after bootstrap. The full application and UI suites use the bundled runtime:

```bash
./run_tests.sh
./uishot.sh
```

The public branch intentionally excludes non-public development corpora, resource-mining tools, internal reports, and tests that require them.

## Report a bug

![Narwhal 3D bug report](images/bugreport.png)

Open the **Bug Report** tab in the right dock, choose a category, and describe what happened. Press **Report** or `Ctrl+Enter`. Narwhal then:

1. saves a local Markdown report and any opted-in attachments;
2. copies a sanitized report to the clipboard;
3. opens a prefilled [GitHub issue](https://github.com/ContributionRich/Narwhal3D/issues/new) for review.

Narwhal never uploads a screenshot, log, or model automatically. Review the browser form and attach only files that are safe to share. If the browser cannot open, the local report remains saved and the clipboard contains the text to paste manually.

## Supported interchange

Core, self-contained source functionality includes exact `.narwhal` project Save/Open plus `.3dm`, STEP, DXF, SVG, OBJ, STL, and CorelDRAW vector interchange. Built-in v15 Import/Open/Export/Print preserves curves, layers, colours, ordinary widths, and Hairline; v16, v17, and v18 Import/Open and opt-in v17 Export/Print use pinned TraceDraw Rust codec crates compiled into Narwhal's bundled helper. None of those paths requires a separately installed executable or service. Other CDR generations and unsupported structures can use an optional local CLI fallback; PDF and Adobe Illustrator conversion can use an optional converter pack. No vector import route uploads files or silently rasterizes them.

## Repository layout

- `narwhal3d/` — Narwhal commands, document model, geometry bridge, and interface
- `launch/` — startup, desktop integration, MIME registration, and app icon
- `packaging/` — immutable Linux artifact lock and release notes
- `scripts/` — verified bootstrap, assembly, verification, and native-runtime build tools
- `blender_patches/` — focused source patches used by the optional native build
- `spike/` — selected self-contained tests retained on the public branch
- `STATUS.md` — what works in the latest release
- `COMPATIBILITY.md` — narrowly scoped target and independence statement
- `ROADMAP.md` — outcome-based development phases and release gates
- `docs/gaps.md` — detailed living inventory of incomplete and missing capabilities

Internal names such as `narwhal3d`, `narwhal.*`, `rhino3dm`, and `.3dm` are compatibility or file-format interfaces; they are not product branding.

## License and third-party software

Narwhal 3D project code is licensed under **GPL-3.0-or-later**. See `LICENSE`.

The application bundle contains separately licensed open-source components, including Blender, rhino3dm, cadquery-ocp, and Open CASCADE Technology. See `THIRD_PARTY_NOTICES`, `licenses/`, and the bundled runtime's own `license/` directory.

Narwhal 3D is an independent project and is not affiliated with the vendors of the supported file formats or third-party libraries.

## Security and provenance

Release artifacts are built from audited public source revisions. Before publishing, run `scripts/audit_public_tree.py` and verify that each package's recorded source revision matches its release tag. Keep private development history and local research material out of public source exports.
