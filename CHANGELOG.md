# Changelog

All notable public changes will be recorded here. Narwhal 3D is an extremely early prerelease; compatibility may change between builds. Release numbering is intentionally frozen on the `0.4.x` line until the project owner explicitly lifts that cap.

## Unreleased

### Added

- Add exact Rust/OCCT local movement for all box faces, edges, and vertices, repeated edits of the resulting curved NURBS solids, axial cylinder caps, and axial circular cylinder rims.
- Apply Grid Snap to Gumball translation independently of Snappy/Smooth object snapping.

### Fixed

- Keep solid-part dragging smooth with a display-only translated face/edge/vertex preview and perform one fully certified exact Rust/OCCT solve on release.
- Prevent a point-edited solid followed by Gumball scaling from entering OCCT's crash-prone optional `ShapeFix_Shape` recognition pass; the exact transformed NURBS must still be valid and airtight.
- Rebuild deferred Scale1D, Scale2D, and Scale3D commits from canonical double-precision drag state instead of accumulated float32 preview matrices.
- Keep exact `local_move` results editable on subsequent operations instead of reporting a generic curved-solid limitation.
- Load source-built Rust cores from normal development launches when packaged runtime copies are absent.

## 0.4.4 — 2026-10-22

### Fixed

- Restore the native welcome window in both Linux and Windows packages. The 0.4.3 assemblers omitted `narwhal-welcome`, and the Windows launcher bypassed it entirely.
- Route normal Windows startup through `narwhal-welcome.exe` with the adjacent runtime/bootstrap contract while retaining direct deterministic headless/version test routes.
- Require and marker-verify every resolver-backed native component: Linux and Windows packages now each fail assembly unless all 9/9 mandatory native libraries/helpers are present.
- Expand package completeness checks across application and launch source, icons/resources, licenses, locked Python distributions, document/project workers, launcher entry points and explicitly named optional exclusions.

### Measured startup note

- Winboat measured the deterministic headless package at 4.08 seconds after deleting application bytecode and 2.03 seconds immediately warm. Release packages continue to exclude generated `.pyc`; the restored welcome presents native feedback while the runtime initializes and waits for the UI-ready marker before fading.

## 0.4.3 — 2026-10-22

### Added

- Add exact NURBS `Helix`, variable-radius/flat `Spiral`, rational `Conic`, exact quadratic `Parabola`/`Parabola3Pt`, rational `Hyperbola`, tolerance-qualified `Catenary`, `CurveThroughPolyline`, `CurveThroughPt`, `TweenCurves`, C1 `HandleCurve`, and `CurveThroughSrfControlPt`, with live previews and explicit refusals for unsupported construction modes.
- Add `Snap`, `SetSnap`, and `SnapSize`; Grid Snap is computed in Rust on each viewport's active CPlane, yields to exact object snaps, is bypassed by typed coordinates, and can be suspended with Alt.
- Add an AI Assistant panel with bounded selected-object context, transcript/input state, asynchronous OpenAI, Anthropic and OpenAI-compatible transport, model discovery, local-server support, timeout/size limits, and environment-variable-only credentials. It does not execute model-proposed commands.
- Add a Finder-runnable macOS build/evidence collector that returns separate sanitized Build and Report archives. It remains an unsigned, unnotarized source build rather than a Mac release.
- Add native Windows Explorer Open/Save dialogs and Yes/No/Cancel message boxes. Open, Save/Save As, Import, Export, Picture, command Print and print-dialog output all use the centralized Windows backend.
- Add a native MSVC build/package path for the Rust exact-geometry core against pinned OCCT 8.0.1 wheel DLLs, including a real airtight BinTools move probe.
- Add amber command-option activation letters derived from the same key map used by matching; alphabetic shortcuts are visible and digits remain numeric input. Thanks to EXOgreen in PR #21.
- Add Space-to-repeat for the last command when the command line is idle and empty; Space remains literal while typing or during a command. Thanks to EXOgreen in PR #20.

### Changed

- Rework `FilletEdge`/`ChamferEdge` into direct 3D edge selection with Shift-add/Ctrl-remove, tangent-contour disclosure, transient highlights, radius grip/label, exact cached preview, and second-Enter commit—never a mesh fallback.
- Move curved B-rep face/edge/vertex editing into a Rust-owned OCCT ABI with exact BinTools payloads, generation-checked edit sessions, LIVE preview certification and stricter COMMIT certification. Unsupported or crease-producing edits refuse explicitly.
- Improve measured native curved-part updates from roughly 1.38 s to 542 ms for faces and 1.13 s to 240 ms for five-edge live updates; vertex live updates measure roughly 140–190 ms.
- Preserve tangent boundaries between distinct fillet faces in Shaded mode while still hiding same-face periodic seams.
- Extend exact planar-face movement through nested fillet recipes by changing the underlying primitive and replaying exact modifiers.
- Preserve modern DXF SPLINE geometry, source layers, true RGB colours and physical inch/mm size through Import/Export; DXF Print emits exact cubic SPLINE entities instead of polylines.
- Extend CDR v15 reading for metadata-less packages, page-size recovery, nested group transforms and correct outline semantics.
- Emit genuine compressed nine-entry CDR v15 packages accepted by real CorelDRAW, with correct document/layer graphs, physical units, previews, Hairline output, object bounds, and black no-outline registration fills.
- Move command-prefix/fuzzy completion, coil planning/previews, large curve interpolation, Catenary planning and surface skinning into measured-winning Rust paths while retaining complete compatibility fallbacks where specified.
- Keep direct packed OBJ/DXF consumption and compatible-tween native generation disabled where complete materialization did not clear the project's 10% production threshold.
- Generate complete audited public roots containing the geometry/platform/Grid Snap sources and focused tests while excluding binaries, targets and private fixtures.

### Fixed

- Fix sequential viewport selections for `TweenCurves`, `CurveThroughSrfControlPt`, and `Offset`, including preselected-first workflows.
- Prevent selected-object control points and overlays from leaking onto Print preview sheets.
- Center the native welcome window on the application's actual monitor, including negative-origin multi-monitor layouts.
- Make Windows document validation workers distinguish Blender from embedded `python.exe` and preserve structured refusal reasons.
- Make resumable Windows builds reuse an already verified pinned OCCT checkout offline and report success only after the final package ZIP is inventoried and hashed.

### Packaging and known limitations

- Publish Linux x86_64 AppImage and unsigned Windows x86_64 portable ZIP from one audited source root. Windows signing, installer/file associations, complete internal runtime PE resource replacement, and physical-GPU/real-window acceptance remain open.
- The Windows portable package includes native document, CDR, supervisor, selection, Grid Snap/pure, Explorer-dialog/platform, and exact geometry components.
- The macOS helper remains source-only; no macOS binary is published.

## 0.4.2 — 2026-10-19

### Added

- Add the dependency-free Rust pure-core source and package its measured-winning paths on Linux and Windows.
- Add the built-in `.narwhal` project format: a Rust-validated atomic container with the complete document payload and checksum-protected exact OCCT BReps for every analytic master. `.3dm` remains interchange.
- Add pinned multi-version CDR support and opt-in v17 Export/Print through the supervised bundled Rust helper.
- Add an experimental macOS 14+ source-build kit for Apple Silicon and Intel contributors; it remains unsigned, unnotarized, native-architecture-only, and unaccepted as a release.

### Changed

- Fuse STL parsing and first-seen vertex welding in Rust for large mesh imports, measured 3.8–4.3× faster at 1k–100k triangles.
- Batch exact-curve coarse pick projection in Rust, measured 2.02× faster while retaining exact curve refinement.
- Return compact native snap-reach indices, measured 9.65× faster on 11,325 cached entries with 16 nearby hits.
- Resolve native discrete snaps before Near fallback, avoiding about 121 ms of unnecessary exact refinement on the measured 270-target mixed-mode hit.
- Keep packed SVG Import experimental because complete curve construction improved only 3–7%, below the required 10% production threshold.

## 0.4.1 — 2026-10-16

### Added

- Add a native startup welcome window with New-model unit choices, Open, Recent Files, About, immediate close, interaction pinning, and ready-state progress.
- Add a checked Rust CorelDRAW v15 reader/writer so supported `.cdr` Import, Open, Export, and Print no longer require external software.
- Preserve CDR source layers, per-object colours, ordinary plot widths, and exact 0.003-inch Hairline; use TraceDraw only as an explicit fallback for other generations/unsupported structures, never malformed input.
- Add a bounded Rust converter supervisor with process-tree teardown and behavior-equivalent Python fallback.
- Add unsigned Linux x86_64 AppImage and Windows x86_64 portable prerelease packages from one audited public source revision.

### Changed

- Move selection projection, crossing tests, point/control-point picking, snapping distance/coarse rejection, curve hover, Near queries, AABB reduction, and high-cardinality matrix preparation into checked Rust paths where available.
- Defer the first native window presentation until the Narwhal interface is ready, replacing the previous startup flash with the welcome workflow.
- Give sidebar flyout commands distinct icons, labels, hover descriptions, and actions.
- Keep release numbering on the owner-required early `0.4.x` prerelease line.

### Fixed

- Restore endpoint, midpoint, and related object snaps on individual sections of joined curves.
- Add drag-over Trim so a gesture can remove every crossed segment instead of requiring one click per piece.
- Keep the welcome window open after user interaction, close it promptly on request, and ensure New/Open actions appear above it before dismissal.
- Correct CDR Import/Open plot-width and Hairline propagation and preserve same-layer colour conflicts explicitly.
- Rebuild the Linux native runtime against glibc 2.28 so the AppImage no longer requires the host's glibc 2.43.

## 0.4.0 — 2026-10-12

### Added

- Add CorelDRAW `.cdr` Import, non-3dm Open, Export, Print, and Print Setup routes through an optional local TraceDraw CLI. CDR output defaults to genuine version 17 for CorelDRAW 2015/X7 and remains readable by 2016/X8.
- Preserve TraceDraw page geometry, source layers, colours, transforms, and disconnected compound paths through structured `.tdraw` data instead of a lossy intermediate SVG.
- Add exact document Cut, Copy, and Paste with layers, materials, groups, attributes, recipes, fresh identities, repeated paste, and Undo/Redo support.
- Restore 22 public-neutral built-in command aliases.

### Changed

- Expand the desktop menus to 532 detailed leaves: 169 runnable commands, Recent Files, and 362 individually named `(Not implemented)` actions.
- Give every viewport title a visible, separately clickable dropdown arrow while retaining independent title activation and maximize behavior.
- Keep sidebar tooltips beside their icons and reflow open flyouts when dock dimensions change.
- Extrude selected nested closed planar profiles as exact solids with holes; disjoint regions produce separate solids. Gumball extrusion uses the same region backend.

### Fixed

- Preserve imported Picture textures and original UV registration through Split, Trim, and exact projection to the active construction plane.
- Prevent accumulated float32 Gumball rotation previews from entering the non-uniform OCCT transform path when released.
- Keep selected visible control points eligible for Point object snaps.
- Increase viewport-only sampling where small many-span NURBS curves otherwise displayed as sharp segments, without changing document geometry.
- Retry viewport tessellation at one bounded coarser setting when a valid exact split surface initially yields no display triangles.
- Collapse only geometrically equivalent duplicate profile loops before exact region extrusion.
- Import `.3dm` files into the already-open document with source layers, unit scaling, and selection of every imported object.
- Make pending command enum values, including Orient scale modes, clickable in the command bar.
- Draw live line and curve previews above reference Picture images.

### Packaging note

- The AppImage does not yet bundle TraceDraw. CDR commands report the missing optional dependency unless `tracedraw-cli` is installed on `PATH` or in Narwhal's converter runtime directory. A bundled converter requires an immutable release or reproducible pinned source build for both Linux and Windows.

## 0.3.0 — 2026-10-12

### Added

- Complete fifteen-tab toolbar workspace, 37-cell left palette, and 28 linked command flyouts.
- Five-column floating icon flyouts with command tooltips, implementation status, click-away dismissal, and reliable anchors after redraw.
- Expanded contributor-authored icon set covering 555 of 561 runtime symbols.
- Responsive left toolbar and right dock layouts with fixed-size toolbar icons.

### Changed

- Refreshed the README Loft demonstration with the current interface and visible pointer movement.
- Improved large-selection and grouped-curve Gumball performance while preserving display/document transform coherence.
- Expanded public contributor guidance, issue organization, roadmap, feature-gap inventory, and privacy-preserving bug reporting.
- Updated the natively branded Linux x86_64 AppImage build.

### Platform note

- A Windows binary is not included. It requires a native Windows build, packaging, verification, and real Windows GUI/GPU validation before publication.

## 0.2.1 — 2026-10-11

### Added

- Comprehensive public roadmap and feature-gap inventory.
- Public support, security, governance, conduct, pull-request, and issue-triage documents.
- Token-free in-app GitHub bug-report workflow and bug issue form.
- Bug-report logo and privacy-reviewed browser submission flow.
- Focused UI, performance, import/export, and feature issue forms.

## 0.2.0

### Added

- Expanded original CAD menu and toolbar organization.
- Dense favorites palette and licensed command icon artwork.
- Explicit `Not implemented` roadmap actions.

### Changed

- Project-owned legacy identifiers migrated to Narwhal naming.
- Public provenance audit strengthened.

## 0.1.0

### Added

- Initial Linux x86_64 AppImage preview.
- Command-driven NURBS CAD workflow, exact solid kernel integration, public tests, and release verification.
