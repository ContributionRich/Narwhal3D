# Narwhal 3D feature gaps

This is the living inventory behind [ROADMAP.md](../ROADMAP.md). Status terms:

- **Complete:** implemented for the stated scope.
- **Partial:** useful behavior exists, but important options or integrations remain.
- **Missing:** no production implementation.
- **Deferred:** intentionally scheduled after a dependency or architecture milestone.

A visible command or control must not imply behavior that does not exist.

## Command shell and automation

| Capability | Status | Remaining work |
|---|---|---|
| Prompt-driven command runtime | Partial | Complete nested/transparent command behavior, consistent option defaults, and full interruption/repeat semantics |
| Command autocomplete/history | Partial | Searchable command browser, persistent usage ranking, discoverable status and documentation |
| Aliases | Partial | Unify editor/import/export state with runtime resolution; persist atomically |
| Macros | Partial | Execute the existing plan model with pauses, cancellation, Enter variants, nesting, and deterministic recovery |
| Scripting/extension API | Deferred | Define only after native boundaries and versioning policy stabilize |

## Files and document services

| Capability | Status | Remaining work |
|---|---|---|
| New/Open/Save/Save As/Exit | Partial | Template chooser, save options, Save a Copy, richer format options |
| Autosave/recovery/recent files | Partial | Public recovery UI, storage controls, low-disk and interrupted-write tests |
| Templates | Partial | Connect catalog to New Model, units/properties transfer, configured locations |
| Document Notes | Missing integration | Persisted notes, lock state, print placement, undo, panel and command |
| Object/document user text | Partial | Editable panels, aggregate edits, import/export/match UI |
| Units and tolerance | Partial | Layout units, angle tolerance, custom units, precision/display settings |
| User settings | Partial | One versioned atomic store for UI, aliases, recent files, templates, and preferences |
| Blocks/managers | Missing | Data model, references, editing, manager UI, persistence, import/export |

## Layers, properties, groups, and selection

| Capability | Status | Remaining work |
|---|---|---|
| Basic layers | Partial | Sublayer hierarchy, richer properties, hierarchy persistence and context actions |
| Layer states | Partial | Save/reopen persistence, import, conflict policy, complete panel |
| Object Properties | Partial | Editing/apply actions, multi-selection writes, complete supported-field persistence |
| Groups | Partial | Complete nested/overlapping workflows and remaining convenience commands |
| Selection | Partial | Additional geometry/property filters, lasso/brush/fence/volume selection, richer subobject selection |
| Visibility/locking | Partial | Swap/isolate-lock families and per-detail/layer visibility workflows |

## Curve creation

Implemented default paths include points, lines, polylines, control-point and interpolated curves, arcs, circles, ellipses, rectangles, polygons, and blends.

Remaining work includes:

- alternate line, circle, arc, ellipse, rectangle, polygon, and conic construction modes;
- fitted/smoothed/fair curves;
- helix and spiral;
- curve tweening and symmetry;
- complete periodic/closed interpolation;
- rail-based revolve and planar curve-region operations.

## Curve editing

| Family | Status | Remaining work |
|---|---|---|
| Join/explode/close | Partial | Broader geometry types, selection-order fidelity, copy variants |
| Offset | Partial | Corner modes, self-intersection cleanup, caps, loose mode, through-point, output layer, subcurve |
| Extend | Partial | Boundary-object, start/end picking, smooth/curvature extension, surface/edge variants |
| Fillet/chamfer/blend | Partial | Arbitrary picked locations, complex topology, richer continuity and interactive handles |
| Split/trim/intersect | Partial | Surface/polysurface breadth, overlaps, loops, infinite cutters, exact intersection output |
| Rebuild/degree/knots | Partial | Surfaces, degree reduction, knot removal, symmetric/automatic modes, fairing |
| Projection/pull | Partial | Surface targets, view direction, ambiguity/deviation reporting |
| Divide | Partial | Exact arc-length/chord solving, remainder/split/group/subcurve options |
| Extraction | Partial | Complete chains, output layers, subobjects, preserved identity |

## Surface creation and editing

| Family | Status | Remaining work |
|---|---|---|
| Planar/edge surfaces | Partial | Wider boundary/topology cases and diagnostics |
| Loft | Partial | Multiple sections in UI, closed lofts, seams, alignment, style controls, recipes |
| Sweep | Partial | Multiple sections, closed sweeps, point sections, refit, maintain-height, miter controls |
| Revolve | Partial | Complete angle/output/input options, rail revolve, durable exact recipe |
| Extrusion | Partial | Remaining output, tangent, boundary, base-point, and solid options |
| Surface offset | Partial | Polysurfaces, both sides, corners, through-point, loose mode, certified fitted deviation |
| Surface intersection/trim | Partial | Kernel intersection curves, polysurface trims, periodic seams, tangency/overlap handling |
| Blend/fillet/match/connect | Missing | Shared continuity solver with measured G0/G1/G2 behavior |
| Network/patch | Missing | Tolerance-fitted construction with achieved-deviation reporting |
| Pull/untrim | Missing command breadth | Trim-aware pull and retained underlying-surface reconstruction |
| Surface control editing | Missing | Master-aware control-point, degree, knot, rebuild, and fairing operations |

## Solids and B-rep topology

| Family | Status | Remaining work |
|---|---|---|
| Analytic primitives | Partial | Remaining construction modes and editable parameters |
| Booleans | Partial | Multi-result transactions, Boolean split, merge-face behavior, history/edit workflows |
| Shell/cap | Partial | Existing-feature editing, broader B-rep adoption, non-planar filling as fitted surface |
| Edge fillet/chamfer | Partial | Variable/asymmetric values, chains, setback/corner controls, exact preview and editing |
| Move face/edge | Partial | More analytic/topological cases, multiple compatible faces, boundary constraints |
| Push/pull | Partial | Inner loops, holes, curved faces, robust topology remapping |
| Bend/twist/taper/flow | Missing | Exact or explicitly tolerance-fitted geometry; never viewport-vertex deformation |
| Repair/validation | Missing UI/commands | Manifold, orientation, self-intersection, tolerance, feature-size diagnostics |

## Mesh domain

Current mesh support focuses on import, display, and export. Missing:

- explicit exact-geometry-to-mesh conversion controls;
- mesh primitives;
- validity/manifold inspection;
- weld/unweld, normals, hole repair, orientation repair;
- split and booleans;
- triangulate, reduce, remesh, smoothing, and quad workflows;
- print-preparation checks;
- analytic recognition only with measured deviation and user confirmation.

Mesh tools must remain separate from exact manufacturing geometry.

## SubD domain

Status: **Missing/deferred**.

Required before activation:

- document representation and persistence contract;
- primitives and conversion;
- selection, creases, editing, subdivision display;
- undo/properties/export behavior;
- measured conversion to exact B-rep where supportable.

## Drafting and layouts

Status: **Mostly missing**.

- text and multiline text;
- dimensions and annotation styles;
- leaders and tables;
- hatches and patterns;
- model/paper layouts and detail views;
- title blocks, print scales, page setup, and layout plotting.

## Rendering and display

| Capability | Status | Remaining work |
|---|---|---|
| Wireframe/Shaded/Rendered viewport modes | Partial | Additional settings, saved presets, consistent reset behavior |
| Ghosted/X-Ray | Missing | Rendering model, selection/picking behavior, tests |
| Materials/textures/lights/environments | Missing | Document types, editors, persistence, import/export |
| Render output | Missing | Settings, camera/output workflow, color management and file output |
| View capture | Missing | Clipboard/file actions and predictable resolution/framing |

## Viewports and navigation

- zoom window/factor/target and complete navigation history;
- named views and camera controls;
- one/two/three/four/six viewport arrangements;
- split, merge, close, float, synchronize, and active-viewport management;
- layout/detail viewports;
- responsive toolbar overflow and persistent workspace layout.

Viewport layout/state-machine work is deferred until it has dedicated integration tests.

## UI and accessibility

- searchable command browser and contextual help;
- editable Notes, Layer States, aliases, units, templates, blocks, and Properties;
- toolbar customization and persisted favorites;
- scrollable/resizable/floating panels;
- narrow-window overflow instead of dropped controls;
- complete keyboard focus order and keyboard alternatives;
- accessible names/roles/state for GPU-drawn controls;
- high contrast, scalable text, reduced motion, and color-vision testing;
- public screenshot matrix for scales, window sizes, display modes, and focus states.

## Performance and reliability

- public synthetic benchmarks for startup, open/import, redraw, large selection, manipulation, save/export, booleans, and memory;
- cancelable long operations with progress;
- bounded caches and deterministic invalidation;
- lazy exact-master rehydration;
- crash recovery, low disk, read-only location, invalid/truncated file, and unsupported GPU tests;
- explicit performance budgets after repeatable baselines exist.

## Import, export, and print

| Area | Status | Remaining work |
|---|---|---|
| `.3dm` | Partial | Guaranteed exact multi-face solid writer and broader metadata policy |
| STEP | Partial | Curves, wires, shells, surfaces, assemblies, colors/layers, broader round trips |
| DXF | Partial | Blocks, text, bulges, styles, richer entities, surfaces/solids, options |
| SVG/PDF/AI | Partial | Page selection, fill/text policy, portable converter availability |
| OBJ/STL | Partial | Unit confirmation/sidecars, explicit deviation reporting, richer mesh options |
| IGES/DWG and other formats | Missing/deferred | Licensing, exactness, units, topology, and round-trip contract first |
| Print | Partial | Physical printers, window selection, margins, positioning, line types, layouts, text/mesh/point output |

## Platform and packaging

Current release target: Linux x86_64 AppImage.

Missing:

- fully locked native CI build graph;
- portable archive and Linux arm64;
- X11/Wayland and broader GPU matrix;
- SBOM, signatures, provenance attestations, and release channels;
- Windows signed installer/portable build;
- macOS signed/notarized universal application;
- shared platform services for paths, file associations, browser/folder opening, clipboard, themes, scaling, and diagnostics;
- eventual native integration removing add-on-style startup.

## Stable-release definition

A stable major release requires:

- complete public capability classification;
- exact geometry representation, validity, airtightness, coherence, tolerance, and fresh-process round trips;
- import/export semantic tests;
- undo, recovery, and atomic-failure tests;
- accessibility and keyboard report;
- public performance report;
- supported-platform matrix;
- clean source/asset provenance;
- checksums, SBOM, signatures, build provenance, and complete notices.
