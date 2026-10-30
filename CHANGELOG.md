# Changelog

All notable public changes will be recorded here. Narwhal 3D is an extremely early prerelease; compatibility may change between builds. Release numbering is intentionally frozen on the `0.4.x` line until the project owner explicitly lifts that cap.

## Unreleased

- Reviewed in-process Rust acceleration for nearest ACI colour queries and larger
  print-width files, preserving Python validation/I/O and complete compatibility
  fallbacks. Tiny files and cheap exact-colour lookups avoid FFI overhead.
- Refuse text wrapping that cannot progress instead of growing spans indefinitely.
- Keep unregistered native experiments out of exported UI source bundles.
- Track verified command-behavior findings and remaining exact Divide/Rotate/Arc
  gaps in [the review](docs/reviews/2026-10-30-rust-research.md); these are not all
  implemented commands or changes to existing0.4.8 release assets.

## 0.4.8

Changes since the published0.4.7 release. Linux and Windows are the initial package targets; macOS remains pending separate portability and package acceptance. See the GitHub release for available assets and their qualification notes.

### Release highlights

- Revised welcome/inspector UI, including the Version/update section, refreshed icons, role-aware AI messages and centered AI/Bug Report action labels.
- Automatic selected-object metadata for AI messages, remembered provider-scoped sharing permission with Settings revocation, and explicit OpenAI Responses API support.
- A tool-capable model can request one new world-aligned analytic box/cube. Creation requires separate human approval, a one-use Rust gate and a valid document/Undo boundary; other modeling execution remains unsupported.
- Polygon directional preview/count-memory fixes and bounded CageEdit safety improvements. Full CageEdit deformation parity is not claimed.
- Windows gains a managed portable-EXE format with verified per-user payload caching and outer-executable update staging. The portable ZIP remains available for compatibility with earlier updaters.
- Native window artwork is rebuilt from the current Narwhal logo. The automation library is included in Linux and Windows packages; the external MCP server remains Unix-only.

CageEdit, advanced surfacing and unrestricted AI modeling remain incomplete. Packages are unsigned; physical Windows GPU coverage remains limited. There was no0.4.7 macOS release, and no Mac package is claimed ready here.

### Added

- The opt-in read-only MCP entrypoint supports both **2025-11-25** initialization and **2026-07-28** request-local metadata/discovery. Nine bounded metadata reads share Rust schemas with the in-app assistant. Both eras have Linux visible-startup, document-replacement revocation and disconnect/app-survival acceptance. Arbitrary existing-app attachment, Windows transport and mutation tools remain unavailable.
- The AI panel uses native bounded conversation/tool-call policy, explicit metadata-read consent, correlated continuations, logical timeouts and stale-response invalidation on New Chat, settings or document replacement. Circle requests remain proposal-only; the separately approved additive box flow is the only enabled modeling action. Chat Completions-compatible, Anthropic Messages and explicit OpenAI Responses providers are supported. Physical HTTP cancellation is not guaranteed.
- Source builds with the optional exact-Brep writer accept a bounded **2–4-section Freeform Sweep1** route through names or individually ordered whole-curve picks, with direction-alignment markers. That writer is not redistributed, so this route explicitly refuses in the current binary packages. Physical station mapping, source UUID/content checks, one exact open rational result, Undo/Redo and project/STEP verification are covered in the source configuration. Unordered batch picks, multi-section components, arbitrary profiles and multi-section Roadlike remain explicitly unsupported. Existing single-section components and Sweep2 routes are preserved.
- Integrate the bounded Rust/OCCT parallel-section surfacing backend through the internal `bridge.sweep1_parallel` API, with exact master/document/recipe transforms and fresh-process `.narwhal`/`.3dm` round trips. Required-native focused integration passes 17/17. This is not generic multi-section Freeform/Roadlike Sweep1 and is not exposed as a Sweep1 UI option.

### Changed

- The existing AI Assistant Settings button opens a small side-anchored popup, without a new trigger or full-window backdrop. Provider/model discovery, manual IDs, environment-variable credential names and preferences reuse existing services; saved/live settings match after normalization. Focused popup checks pass in normal and narrow windows; discovery transport was mocked, not provider reliability-certified.
- FilletEdge held drags coalesce current section previews instead of rebuilding full fillet solids for each motion. Release validates the latest radius exactly once; later command acceptance reuses that candidate. Section preview is not a validated solid, and failed final validation cannot commit an older candidate.
- Gumball extrusion uses a reusable GPU display guide during held motion and builds exact manufacturing geometry once on release. Focused circle/annulus acceptance retains caps, holes, recipes and one Undo; the guide never becomes document geometry. Preview timing is measured to current-value draw submission, not GPU completion or a universal frame-rate guarantee.

### Fixed

- Portable Windows updates detach without retaining scheduler pipe handles and correctly recognize an already-exited launcher while Windows still retains its process object.
- Welcome completes its loading state after slow startup when the native presentation watchdog fired before the final layout was ready. Both signals are required; an incomplete watchdog frame alone does not announce readiness.
- Guarded surface publication rolls back only its own document/display/master allocations, including auto-renamed meshes. A surviving external edit stays dirty and undoable rather than being hidden by a failed Sweep1 record.
- Refuse self-intersecting prism results rather than relying on closure alone.
- Adapt native Point3d samples in overlap component picking.
- Validate actual decoded multi-resolution ICO images, alpha coverage and corruption instead of using compressed file size as a quality proxy. Restore the existing Undo regression to the current context/model names without removing its assertions.

- FilletEdge grip clicks now open command-line radius input, while a deliberate drag adjusts the radius until release. Enter, numpad Enter, Space and right-click accept the radius subprompt without prematurely committing the whole fillet. Escape restores the current adjustment, and invalid values remain correctable without committing a stale preview.
- FilletEdge's displayed prompt and clickable options now follow edge selection, handle editing and radius input instead of continuing to display the initial selection step.
- Cancel or invalidate held Gumball extrusion guides before release, preventing a canceled drag or changed source from producing an extrusion. The complete focused preview integration passes 98/98; this is not the full project gate.
- Keep Trim's held drag rectangle visible in cutter and remove-parts stages, redraw it during empty-space motion, and clear it on release/Escape. Existing held-motion trimming is preserved; this does not introduce rectangle-based part removal. Focused GUI checks pass 189/189, with legacy drag checks 19/19.

## 0.4.7 — 2026-10-27

Changes since historical public v0.4.6 (`5a40d6d1eee16b26f4e6b5714862c711cafad797`). Release assets are named `Narwhal_3D-0.4.7-x86_64.AppImage` and `Narwhal_3D-0.4.7-Windows-x86_64.zip`. Both use public source revision `94534c954dc77f1f1d22f2be6064bedcc8cd1f5b`, source epoch `1793144099`; the release tag stays on that asset-build revision. This documentation follow-up does not repackage the assets. See [checksums and sizes](docs/technical-reference.md#release-source-and-integrity); platform evidence and remaining limitations follow.

### Added

- Loft now lofts any number of curves in the order they are picked, with a Style option: Normal, Loose, Tight, Straight and Uniform. Before, it lofted exactly two curves with a ruled surface and had no options. Normal, Tight and Uniform pass through every curve with chord-length, square-root-chord or uniform spacing, as the reference API defines them. Every result is an exact NURBS surface that can be moved, booleaned after capping, and reloaded.
- Loft aligns its curves automatically. A curve drawn in the opposite direction, or a closed curve whose seam starts elsewhere, no longer folds or twists the surface.
- SrfPt creates a surface from three or four corner points: the exact bilinear patch through four corners, or the exact triangle when Enter is pressed at the fourth.
- CageEdit deforms curves, points, meshes and untrimmed surfaces with a bounding-box control cage. PointCount and Degree can be set per direction. Dragging the cage's points with the normal point tools reshapes every captive object. Only control points move, so a captive circle stays an exact rational curve with the same degree, knots and weights. Each edit is computed from the original shape, so repeated edits never accumulate error. Trimmed surfaces, polysurfaces, solids and the Line, Rectangle and Box control objects say they are not implemented yet.
- The cage volume, loft section alignment and corner checks run in the Rust core. CageEdit has no Python copy of its math: without the Rust core it says so instead of running slowly.
- Sweep1 and Sweep2 accept exact Segment/Edge inputs for both rails and sections. Loft, EdgeSrf and PlanarSrf share the same non-destructive component selection: source curves, surfaces and solids are not split or replaced with viewport meshes.
- FilletEdge supports independent start and end radii on supported edges, with exact kernel geometry and replayable recipes. Radius handles support scalar and endpoint drags and numeric editing; cached section arcs remain available with Preview=No. Invalid candidates are rejected rather than committed. Unequal radii explicitly refuse unsupported closed or multi-edge tangent contours.
- Theme and visibility preferences can be changed in the interface and restored on the next launch. The selected tool category and inspector tab, viewport-tab visibility, Grid Snap, Ortho, object snaps, Gumball settings, AI provider/selection choices and Print settings also persist. AI preferences store the credential environment-variable name, not the secret value; document units and tolerance remain document settings.
- Sidebar tools have authored multiline tooltips describing primary actions, alternate actions and flyouts, with pointer anchoring, a 120 ms hover transition and separate arrow hover feedback.

### Changed

- Redesigned the workspace around a shared light/dark palette: slate chrome, light panels and a grey viewport by default, or charcoal chrome and panels in dark mode. The command line sits in a rounded, framed box; viewport titles use rounded chips with a blue active-view accent. Menus, controls, checkboxes, tooltips and status-bar chips use the same semantic colours. Readability checks retain the 4.5:1 small-text contrast requirement in both themes.
- Tool categories now use a full-height clickable strip and a matching drop-down above the contextual left toolbar. The tool list has a visible draggable scrollbar when it overflows. The inspector has a labelled tab rail; Layers has search, add/more controls, visibility and lock controls, and row menus. Object snaps use a headed list with real checkboxes.
- Bug Report, AI Assistant, AI Settings, Print controls and the native welcome window follow the chosen theme. The physical Print page stays white in either theme.
- The File menu and viewport menu use flat, grouped layouts. Display modes are listed directly with the current one marked. Unavailable actions can be hidden; when shown, they remain visibly unavailable and explain the limitation instead of pretending to work.
- Viewport menus open only from the title arrow. Double-clicking a title or using the corner control maximizes/restores the viewport while preserving camera, CPlane and display state.
- The status bar uses consistent toggle chips, a fixed coordinate slot and an Absolute Tolerance readout with a native menu.
- Gumball removes the mid-plane scale square and passive tooltips. Shafts avoid extrusion balls, and filled caps draw above shafts so the controls remain distinct.
- Release tooling records and verifies the new native UI patch inputs. The Windows engineering builder can explicitly rebuild a cached runtime; resume-packaging rejects missing or stale native provenance rather than treating an old executable as current.

### Fixed

- PlanarSrf, ExtrudeSrf and EdgeSrf command checks now run in the test suite. They had silently stopped running, which hid a test that named its surface after a command option.
- Restore Delete and command-field editing in the redesigned shell.
- Gumball-created curve extrusions now scale correctly after being displayed. Nonuniform transforms operate on a detached exact master without inherited display triangulation; source geometry, smooth surfaces, recipes and Undo remain intact. Refused or failed Gumball transforms report the failure instead of claiming a successful scale.
- Keep startup viewport layout and title-chip padding correct at higher interface scales instead of producing stray viewplanes or mismatched title hit areas.
- Draw tooltips and popups after native area borders through a window-level overlay, eliminating seams when an overlay crosses viewport boundaries.
- Filter finite CPlane grids in pixel space and use the same antialiasing path for preview and committed lines. Correct UI PNG colour-space handling and scaling.
- Ctrl+Shift component picking accepts rhino3dm Point3d coordinates and preserves pick order, so sequential rail and section choices reach the command correctly.
- Start the layer colour picker's HSV Value at 255 and leave the layer unchanged until the colour is confirmed.
- Include SrfPt in the representation audit without exemptions; focused audit coverage includes all six geometry gates.

### Packaging and platform limitations

- The Linux 0.4.7 AppImage has been built and its inventory verified. All 290 bundled ELFs require at most GLIBC 2.28, below the GLIBC 2.35 release ceiling. The portable runtime and welcome contain the current native UI patches; the separate GLIBC 2.43 source-development executables must not ship.
- Final Linux source release gates, including the full suite, and AppDir/extracted-AppImage verification passed. Actual shipping-archive NVIDIA/OpenGL acceptance passed Gumball mouse extrusion then scale (24/24), rendering (47/47), preferences write/restore (183/193 checks), window overlays (105/105), component/fillet interaction (74/74), and shell interaction (73/73). All 28 meshed-master transform/round-trip combinations passed; clean-copy and overlay mutations went RED, then restored paths passed. The package inventory and archive digest stayed unchanged. Pristine Ubuntu-desktop and optional GPU-backend acceptance remain open; the AppImage is unsigned.
- Windows remains an unsigned, portable early preview, not an installer. The 0.4.7 engineering build completed using a verified, unchanged native runtime; directory and ZIP verifiers passed. Actual extracted-final-ZIP WinBoat acceptance passed Gumball mouse press/drag/release (24/24), preferences write/restore (184/194 checks), window overlays (112/112), all 28 meshed-master transform/round-trip combinations, and all six representation gates. GUI acceptance used temporary Mesa llvmpipe software rendering, not a simulated UI; it is not physical-GPU certification. The temporary driver was removed and the exact package inventory restored; no test driver ships. Physical-GPU acceptance, complete internal PE identity cleanup, installer/file associations and optional signing remain open.

## 0.4.6 — 2026-10-24

Changes from 0.4.5:

### Added

- Make points, edges and faces movable on every kind of solid. Moves were refused on cylinders, cones, tubes, extruded circles and ellipses, spheres, tori, and any face with a hole.
  - A wall that closes on itself through a seam is rebuilt as the exact ruled surface between its rims. A cylinder whose top slides sideways becomes an exact oblique cylinder.
  - Faces with holes are rebuilt as one smooth surface that keeps every hole.
  - Cone tips and sphere poles move, stretching the surface smoothly.
  - A move that carries every vertex translates the solid exactly.
  - Moving a circle's single point carries the whole circle.

  A test covers 20 kinds of solid with every part moved in six directions (2,094 moves). The one remaining refusal is a sideways ellipsoid pole stretch, which OCCT's self-intersection check cannot certify for that surface.
- Make points, edges and faces movable on open surfaces and polysurfaces, not just solids. Planar surfaces (with or without holes), lofts, edge surfaces, Sweep1, Sweep2, open pipes, open extrusions, open cones and partial revolves previously refused every move. Each edit is sewn back together and must keep the same boundary. A curved wall between two rims keeps its curvature when one rim moves: moving the end of a bent pipe no longer straightens the tube, and moving the rim of a revolved arc no longer flattens its bulge. A new test covers 15 kinds of surface (1,008 moves).
- Make Revolve exact. A revolved curve is now an exact OCCT shape with a `revolve` recipe, so it can be moved part by part, booleaned, exported and reloaded exactly. Before this it was a display-only surface with no kernel shape. A full turn of a closed profile becomes a closed solid; a revolved rectangle is two planes and two cylinders with the exact ring volume. An open cone (`Solid=No`) is now an exact revolve as well.
- Move every point, edge and face of extruded profiles with any number of segments. Previously caps with more than six edges were refused, so extrusions of profiles with arcs, such as a keyhole, could not move anything across the cap plane. Arcs that continue each other tangentially keep their tangency when a shared point moves.

### Changed

- Finished moves are certified best-first, which brings a lifted fillet face from 9 s to under 1 s.
- Edge-length and volume measurements use adaptive integration, which is accurate for rational B-splines.
- The Revolve command reports a clear message instead of failing when a curve cannot be revolved exactly.
- Every native Narwhal component in the AppImage is now built in a glibc 2.28 container, so the whole package requires at most glibc 2.28 (0.4.5 needed 2.35 for some components).

### Fixed

- Route curved faces, edges, and vertices on exact extrusion solids through the certified Rust/OCCT local-move core instead of obsolete recipe-specific blanket refusals.
- Move vertices out of six-edge extrusion caps by rebuilding one exact composite NURBS cap from all six real boundary curves; no triangular or planar subdivision is introduced.
- Prevent six-edge point edits from freezing the window on first release by trying the topology-oriented exact partition before the exhaustive fallback; the reported exact solve drops from about 14 seconds to 2 seconds.
- Rebuild a six-edge cap lifted by a point edit as one smooth exact bicubic NURBS height field over the original cap plane instead of a folded composite patch. A new Rust solver fits the surface; OCCT builds the exact face with planar p-curves and edge tolerances that are re-measured to bound the true surface gap. Multi-span curved edges attached to the moved point now bend smoothly along their whole length instead of kinking in their last span. On the reported model the cap area matches the reference result within 0.1%, and the edit commits in about 1.2 seconds instead of 3.6.
- Keep sequential point edits on a curved cap smooth and fast. A cap that is already a fitted surface is now refit over its original plane, which is recovered from its unmoved straight edges. Before this, a second point on it could not be moved, and moving the first point back folded the cap and took 7–15 seconds. The fitted cap now stays on one side of its curved edges, so the solid check no longer stalls (one edit took 139 seconds). Edge tolerances are measured against the part size and no longer grow with each edit. Lines and arcs on the cap stay analytic. A saved model whose edit history was recorded by an earlier build still rebuilds its exact geometry, because each moved point is matched by its exact position.
- Lifted arcs on an extruded cap now rise with continuous curvature, so the rebuilt cap meets them closely, and translated arcs keep their identity check through an accurate arc-length measure. On the reported multi-arc model all 400 checked moves succeed.
- The welcome window's update check no longer gives up after four seconds on a slower connection (it failed about half the time on an ordinary home connection), and a failed check now logs its real cause, such as a DNS, TLS or timeout error.
- Restore live Gumball extrusion previews after the direct-edit preview optimization removed a shared throttle constant; unexpected candidate failures now stay inside the drag and no longer poison the modal operator.

## 0.4.5 — 2026-10-23

Changes from 0.4.4:

### Added

- Add exact Rust/OCCT local movement for all box faces, edges, and vertices, repeated edits of the resulting curved NURBS solids, axial cylinder caps, and axial circular cylinder rims.
- Allow Sweep1 and Sweep2 rail prompts to select exact analytic/NURBS edges directly from surfaces and solids without converting rails to viewport meshes.
- Apply Grid Snap to Gumball translation independently of Snappy/Smooth object snapping, including native U/V/W lattice snapping for free three-dimensional Perspective movement.
- Check GitHub releases asynchronously after the native welcome window paints, show concise release news, and offer an explicit SHA-256-verified update action. AppImages replace atomically; Windows portable updates validate and stage the ZIP, wait for Narwhal to exit, and retain rollback recovery.

### Changed

- Use a complete prospective boundary wireframe—not an expensive rebuilt shaded solid—for point, edge, and face drag previews; exact geometry is solved once on release.
- Display only real analytic BRep boundary curves after solid-part edits; remove the experimental fake midpoint U/V surface lines.
- Prefer a smooth exact direct-edit result whenever one exists, while accepting a real NURBS crease only when the requested edit has no smooth candidate and all exactness, topology, validity, self-intersection, and airtightness certificates pass.

### Fixed

- Restore live drag previews for points, boundary curves, and faces; edge movement stretches every incident real boundary from both moved endpoints.
- Keep Grid Snap from flattening point/edge/face movement onto the bottom plane in Perspective, snap Gumball translation by grid increments, and allow axis-constrained height stages such as Box to complete.
- Prevent a point-edited solid followed by Gumball scaling from entering OCCT's crash-prone optional `ShapeFix_Shape` recognition pass; the exact transformed NURBS must still be valid and airtight.
- Rebuild deferred Scale1D, Scale2D, and Scale3D commits from canonical double-precision drag state instead of accumulated float32 preview matrices.
- Keep exact `local_move` results editable on subsequent operations instead of reporting a generic curved-solid limitation.
- Move faces, edges, and vertices on repeatedly edited and selectively filleted curved NURBS solids through the certified native OCCT core, with valid airtight results and deterministic recipe replay.
- Move points, boundary curves, and the whole surface of standalone one-face Sweep1 BReps, preserving one valid exact face and honestly reporting that an open surface is not airtight.
- Resolve selective fillet/chamfer edges by persisted geometric signatures rather than unstable topology indices.
- Close sub-tolerance UV trim endpoint round-off in the source-built `.3dm` BRep writer while preserving exact 3-D edges and surfaces.
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
