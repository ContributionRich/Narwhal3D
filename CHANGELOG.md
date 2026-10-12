# Changelog

All notable public changes will be recorded here. Narwhal 3D is currently in preview development; compatibility may change between preview releases.

## Unreleased

### Fixed

- Preserve image textures and original UV registration when imported Picture surfaces are split or trimmed.
- Make pending command enum values, including Orient scale modes, clickable in the command bar.
- Extrude selected nested closed planar profiles as exact solids with holes; disjoint regions produce separate solids.
- Apply the same nested-profile hole behavior to Gumball extrusion.
- Draw live line and curve previews above reference Picture images.

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
