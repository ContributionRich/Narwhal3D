# Narwhal 3D roadmap

Narwhal 3D is building a free, independently developed, command-driven NURBS CAD application. The long-term goal is broad workflow, command, geometry, and file-compatibility parity with established professional NURBS CAD tools while remaining honest about incomplete behavior.

This roadmap is outcome-based rather than date-based. [STATUS.md](STATUS.md) describes the latest release; [docs/gaps.md](docs/gaps.md) is the detailed living inventory.

## Principles

Every phase follows these rules:

1. **Independent implementation.** Do not copy unauthorized source, binaries, UI resources, icons, documentation, or other protected assets.
2. **Exact manufacturing geometry.** Viewport tessellation is display-only. Curved document geometry must remain analytic or NURBS geometry.
3. **Honest capability.** Missing behavior is visibly marked `Not implemented`; no silent substitution.
4. **One backend per behavior.** Menus, toolbar buttons, command input, panels, and automation call the same implementation.
5. **Tests that can fail.** Every behavior change receives a focused test and a deliberate breaking mutation.
6. **Portable application.** Required runtime dependencies must be bundled, versioned, and integrity-checked.
7. **Privacy by default.** Reports and diagnostics are reviewed by the user before anything leaves the machine.

## Current foundation

The current application provides:

- four model viewports with Wireframe, Shaded, and Rendered modes;
- a command line with prompts, options, typed coordinates, selection, object snaps, and history;
- fifteen original menu groups exposing all currently registered commands;
- fifteen desktop toolbar tabs, a 37-cell left tool palette, and linked command flyouts;
- layers, object properties inspection, display controls, and local/GitHub bug reporting;
- exact NURBS curves, analytic primitives, exact solid operations through Open CASCADE, and display-only tessellation;
- `.3dm`, STEP, DXF, SVG, optional TraceDraw-backed CorelDRAW, PDF/AI, OBJ, and STL workflows within documented limits;
- undo/redo, groups, autosave, recovery, recent files, units, and atomic save paths;
- a verified Linux x86_64 AppImage release process.

This is a substantial preview, not feature parity. See [docs/gaps.md](docs/gaps.md).

---

## Phase 1 — Public project foundation

**Goal:** make project scope, contribution paths, provenance, and release quality understandable without internal planning material.

### Deliverables

- Complete public roadmap and gaps inventory.
- Changelog, support, security, governance, conduct, and contribution policies.
- Focused issue forms for bugs, commands, UI/accessibility, performance, import/export, and feature proposals.
- Pull-request checklist covering tests, mutation testing, geometry exactness, privacy, accessibility, and platform effects.
- Consistent labels, milestones, and a public GitHub Project organized by area, priority, effort, and status.
- Required CI for public-tree audit, Python compilation, pure tests, headless integration, and Linux GUI smoke tests.
- Original or clearly licensed assets with attribution and source files.

### Exit criteria

- Every roadmap milestone maps to public issues or project items.
- A new contributor can identify a bounded task and validation command from public documentation.
- Pull requests cannot merge with missing required tests, forbidden assets, or broken public packaging metadata.

---

## Phase 2 — Backend coherence and persistence

**Goal:** ensure every settings or document UI edits the same state the runtime consumes.

### Deliverables

- One persistent alias store used by editing, import/export, autocomplete, and command execution.
- Runtime macro execution with cancellation, pauses, Enter semantics, nesting, and deterministic failure recovery.
- Save/Save As option models connected to real writers, plus Save a Copy.
- Template catalog connected to New Model and document construction.
- Document Notes persisted with lock state, print placement, undo, and a working panel.
- Layer states persisted across save/reopen with conflict handling and panel actions.
- Unified document units: model/layout units, absolute and angular tolerance, display precision, and custom units.
- Editable object properties with correct mixed-selection behavior and persistence capability reporting.
- Grid settings persisted and connected to snap spacing.
- One versioned, atomic user-settings service for aliases, interface preferences, recent files, and templates.

### Exit criteria

- User-scoped settings survive restart.
- Document-scoped settings survive save/reopen where supported.
- UI and command routes produce identical state changes and undo behavior.
- Unsupported persistence is reported before mutation.

---

## Phase 3 — Complete high-use registered commands

**Goal:** finish the commands already visible before adding broad new families.

### Deliverables

- Complete common construction modes for line, circle, arc, rectangle, ellipse, polygon, box, sphere, cylinder, and cone.
- Complete high-use curve editing: offset, extend, fillet, chamfer, rebuild, degree change, knot insertion, blend, split, trim, and intersection.
- Complete surface/solid options for extrusion, revolve, loft, sweeps, pipe, shell, surface offset, edge fillet/chamfer, and booleans.
- Complete zoom, construction-plane, named-plane, gumball, display-mode, and object-snap option surfaces.
- Complete selection, subobject, group, layer, analysis, and purge behaviors.
- Standardized prompts, preselection, interactive selection, typed input, Enter, Escape, and option handling.

### Exit criteria

- Every registered command is publicly classified as complete or partial.
- Every visible option either works or refuses by name before changing the document.
- Document mutations form the expected single undo unit.
- Geometry-producing paths pass representation, coherence, validity, and round-trip gates.

---

## Phase 4 — Advanced exact modeling

**Goal:** add missing professional modeling breadth without degrading geometry into facets.

### Curves

- fit, smooth, fair, tween, helix, spiral, conics, rail revolve, knot removal, control-point positioning, and planar curve-region operations;
- complete pull/projection and overlap/intersection output;
- exact arc-length division and improved closest-parameter solving.

### Surfaces

- multi-section and closed lofts with seam control;
- multi-section one- and two-rail sweeps;
- blend, fillet, network, patch, connect, match, pull, and untrim;
- surface rebuild, degree/knot editing, fairing, and general offsets with measured tolerance.

### Solids

- Boolean split and transactional multi-result operations;
- variable-radius fillets and asymmetric chamfers;
- broader push/pull and subobject editing;
- exact cap, merge-face, repair, and topology validation;
- bend, twist, taper, flow, and symmetry only through exact or explicitly tolerance-fitted geometry—not viewport vertex deformation.

### Exit criteria

- Every new operation has an exact or explicitly tolerance-fitted backend before UI registration.
- Solids are valid and airtight.
- Continuity promises are measured geometrically.
- Recipes survive transforms, later booleans, undo, save/reopen, and exact export.

---

## Phase 5 — Mesh, SubD, drafting, and rendering domains

**Goal:** make currently visible roadmap workspaces real, separate modeling domains.

### Mesh

- explicit B-rep-to-mesh conversion with tolerance controls;
- mesh primitives, join/split, normals, weld/unweld, hole repair, booleans, reduce, remesh, smoothing, and print-preparation checks;
- no mesh operation may silently replace exact document geometry.

### SubD

- define the document representation and persistence contract first;
- primitives, conversion, selection, editing, creases, subdivision display, and interoperability;
- exact B-rep conversion only where deviation is measured and disclosed.

### Drafting

- text, dimensions, leaders, hatches, annotation styles, layouts, detail views, title blocks, and print-scale workflows.

### Rendering

- materials, textures, lights, environments, render settings, image output, and viewport/render consistency.

### Exit criteria

- Each domain has explicit document types, undo, persistence, selection, properties, and export behavior.
- Roadmap toolbar entries become active only when the underlying command is real.

---

## Phase 6 — Complete document-facing UI

**Goal:** make every backend discoverable and editable without losing the command-first workflow.

### Deliverables

- searchable command browser with status, description, shortcut, and documentation;
- editable Layers, Properties, Notes, Layer States, aliases, units, user text, templates, blocks, and grid controls;
- toolbar customization and persisted favorites;
- responsive overflow for toolbar tabs, status panes, and narrow docks;
- scrollable panel bodies and resizable/floating panel architecture;
- full keyboard navigation and visible focus for all custom controls;
- semantic accessibility strategy for GPU-drawn controls, with documented host-runtime limitations;
- high-contrast and scalable interface modes;
- public screenshot matrix for window sizes, scales, display modes, focus/hover/disabled states, and supported display servers.

### Exit criteria

- Core workflows can be completed without a mouse.
- State is never communicated by color alone.
- Every visible control has a usable label, focus state, and keyboard equivalent.
- Small windows use explicit overflow rather than silently dropping controls.

---

## Phase 7 — Interchange, print, and manufacturing assurance

**Goal:** make supported formats predictable, portable, and independently verifiable.

### Deliverables

- exact STEP support for solids, shells, surfaces, wires, colors/layers where supported, and fresh-process round trips;
- exact `.3dm` multi-face solid persistence strategy with no runtime compilation or download;
- richer DXF entities, blocks, text, styles, bulged polylines, and import/export options;
- optional IGES/DWG paths only after licensing, geometry, units, and round-trip behavior are supportable;
- complete print workflow: paper destinations, window selection, margins, positioning, line types, layouts, silhouettes, points, meshes, and text;
- verified app-local converter bundle or explicit feature unavailability;
- atomic failure with no partial output.

### Exit criteria

- Each supported format has redistributable fixtures and semantic round-trip tests.
- Exact formats preserve topology, analytic face types, units, bounds, and mass properties.
- Approximate outputs report representation and achieved tolerance.

---

## Phase 8 — Performance, reliability, and accessibility baseline

**Goal:** make large real projects responsive and failures recoverable.

### Deliverables

- public synthetic benchmarks for startup, open/import, viewport redraw, large selection, gumball interaction, save/export, booleans, and memory;
- bounded caches with deterministic invalidation;
- lazy exact-master rehydration and visible-object prioritization;
- cancelable progress for long operations;
- structured privacy-safe logging;
- crash recovery, interrupted save, read-only location, low disk, invalid file, and unsupported GPU tests;
- regression budgets based on repeatable public baselines.

### Exit criteria

- Interactive operations meet published latency budgets on representative public fixtures.
- Performance optimizations never change manufacturing geometry or silently relax tolerance.
- Recovery and autosave paths are release gates.

---

## Phase 9 — Linux release maturity

**Goal:** produce repeatable, supportable Linux releases from clean CI.

### Deliverables

- locked native compiler, linker, source, libraries, wheels, and packaging tools;
- x86_64 AppImage and portable archive, followed by arm64 when dependencies support it;
- X11/Wayland and integrated/discrete GPU validation;
- checksum, SBOM, provenance attestation, signatures, complete notices, and launch/file-open tests;
- preview, release-candidate, and stable channels;
- no auto-update until signing, rollback, channels, and privacy are designed.

### Exit criteria

- A clean build environment reproduces and verifies a publishable artifact.
- No host application, system Python, or undeclared system converter is required.

---

## Phase 10 — Cross-platform and native integration

**Goal:** deliver one cohesive application on Linux, Windows, and macOS, then remove add-on-style bootstrapping.

### Platform abstraction

Create stable services for resource discovery, user directories, file associations, URL/folder opening, clipboard, modifiers, theme/scale, crash logs, and optional integrations.

### Windows and macOS

- signed Windows installer and portable build;
- signed/notarized macOS application bundle;
- native file associations, paths, scaling, menus, shortcuts, trackpads, and permission handling;
- platform-specific GPU and input validation.

### Native migration

1. Freeze behavioral contracts in public tests.
2. Move identity, lifecycle, menus, key routing, and platform services into the native source tree.
3. Move custom drawing and accessibility semantics behind native interfaces.
4. Move frame-critical viewport/event paths.
5. Move command orchestration after behavior is fully covered.
6. Retain Python for tests, build tooling, and prototypes where useful.

### Exit criteria

- Shipping builds no longer depend on add-on-style startup.
- Supported platforms share the same document, command, exactness, and release contracts.

---

## Stable-release gates

A stable major release must pass:

- public provenance and asset audit;
- Python/native compilation and static checks;
- complete command and option classification;
- exact geometry representation, validity, airtightness, coherence, and fresh-process round trip;
- unit and mass-property verification;
- import/export semantic round trips;
- undo, recovery, and atomic-failure tests;
- accessibility and keyboard report;
- public performance report;
- supported-platform matrix;
- SBOM, checksums, signatures, provenance, and third-party notices.

## How to help

Start with issues labelled `good first issue` or `help wanted`. Keep one command or one UI behavior per pull request, include focused tests, and demonstrate that the new test fails under a deliberate mutation before restoring the implementation.
