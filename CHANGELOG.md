# Changelog

All notable public changes will be recorded here. Narwhal 3D is an extremely early prerelease; compatibility may change between builds. Release numbering is intentionally frozen on the `0.4.x` line until the project owner explicitly lifts that cap.

## Unreleased

### Added

- Embed pinned, licensed TraceDraw Rust reader crates in a bundled, supervised Narwhal helper for CDR v16, v17, and v18 Import/Open without any separately installed executable or service.

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
