# Narwhal 3D project status

Narwhal 3D is an extremely early public prerelease. This page records what currently works and what remains incomplete so visible UI does not imply unsupported behavior. Package versions remain on the `0.4.x` line until the project owner explicitly lifts that cap.

## Command compatibility

Narwhal 3D is **not yet at full command parity with established NURBS CAD workflows**. It has a substantial working command set, but many commands, advanced options, selection routes, dialogs, and viewport tools remain missing or incomplete. A registered command name means that command is runnable; it does not guarantee every advanced option or interaction path is complete.

The authored menu hierarchy exposes all 184 commands currently registered by Narwhal, plus Recent Files and 361 individually named unavailable commands with `(Not implemented)` labels. File includes recent/recovery, template, and document groups; Help includes learning/reference and support groups. The desktop toolbar workspace contains 582 top-strip slots across fifteen tabs plus the 37-cell left tool palette and its 28 linked flyouts. Of the 525 non-separator top slots, 119 currently invoke registered commands; every unavailable action remains visible, identifies itself on hover, and explains the gap when clicked.

The issue tracker is the live source for bounded contribution work. Current one-command starter tasks include:

- [ClearUndo](https://github.com/ContributionRich/Narwhal3D/issues/4)
- [HideSwap](https://github.com/ContributionRich/Narwhal3D/issues/1)
- [LockSwap](https://github.com/ContributionRich/Narwhal3D/issues/2)
- [JoinCopy](https://github.com/ContributionRich/Narwhal3D/issues/3)

Axis-based `Helix`, variable-radius `Spiral`, exact `Conic`, `Parabola`, `Hyperbola`, polyline/point-to-curve conversion, compatible `TweenCurves`, tolerance-qualified `Catenary`, C1 `HandleCurve`, and surface-control-polygon extraction now create real curve geometry with live previews where point-driven; planar `Flat` Spiral is supported. Sweep1 and Sweep2 rail prompts accept exact Ctrl+Shift-selected surface/solid edges as well as standalone curve objects. `AroundCurve` remains explicitly unavailable until its moving-frame convention is measured.

Major missing or incomplete areas include advanced curve and surface construction, deformation tools, some solid-editing operations, advanced viewport arrangements, document/options panels, rendering features, and many less-common command options. Examples that are not currently implemented include `Bend`, `Twist`, `Taper`, `Flow`, `BlendSrf`, `FilletSrf`, `NetworkSrf`, `Patch`, `MatchSrf`, `Untrim`, and `BooleanSplit`.

This status stays intentionally high-level. The public issue tracker contains detailed, bounded work that contributors can act on.

Unsupported behavior must be reported explicitly. Narwhal should never silently run a different command or substitute faceted manufacturing geometry.

## Viewport display modes

The following modes are implemented:

- **Wireframe**
- **Shaded**
- **Rendered**

They can be selected from:

- the **Display** tab in the right dock;
- the viewport-title menu opened from a viewport title;
- the `SetDisplayMode` command, with Active or All viewport scope;
- `Ctrl+Alt+W`, `Ctrl+Alt+S`, and `Ctrl+Alt+R`.

`Ghosted` and `X-Ray` are recognized but not implemented yet. Rendered mode currently uses the bundled viewport renderer and Narwhal's soft ground-shadow pass; it is not a complete photorealistic rendering pipeline.

## Icons and visual design

The expanded workspace manifests 561 distinct runtime symbols. Owner-generated GPL-3.0-or-later source artwork supplies 555 of them, including every left-palette cell and all four right-panel tabs. Six specialized actions still use distinct generated placeholders: Parametric tools, three visibility clusters, Analyze curve deviation, and Update all linked blocks. The icon set remains work in progress rather than final visual design.

Artwork published with Narwhal must be original or have clearly documented GPL-compatible redistribution terms. Contributions should include source artwork, licence information, and readable light/dark-theme behavior.

## Geometry and interchange limitations

- Viewport tessellation is display-only and must not become document or manufacturing geometry.
- Exact solid operations use Open CASCADE and must preserve analytic surfaces and airtightness.
- Box and extrusion faces, edges, and vertices can be moved through the Rust/OCCT local-edit core, including curved extrusion walls and vertices moved out of six-edge caps. Such a cap, including one already reshaped by earlier edits, becomes one smooth exact bicubic NURBS height field over its original plane: Rust fits it, and OCCT builds a face whose edge tolerances are measured to bound the true gap, at most 1e-6 of the part size times four. Exact composite NURBS with a topology-aware fast path and exhaustive fallback remain the fallback. This also covers repeated edits after incident faces become exact NURBS, and further edits after a selective analytic fillet. A boundary-exact direct edit may introduce a measured NURBS crease when all topology, mapping, validity, self-intersection, and airtightness certificates pass. Standalone one-face Sweep1 surfaces support point, boundary-curve, and whole-surface movement while remaining valid exact open BReps. Every part of every catalogued solid moves in any direction: boxes, pyramids, extrusions of any profile, booleans, faces with holes, fillets, cylinders, cones, tubes, spheres and tori. Walls that close through a seam become exact ruled surfaces between their rims, and cone tips and sphere poles stretch the surface smoothly. A test with 1,944 moves enforces this. The one known refusal is a sideways pole stretch of a B-spline ellipsoid, which OCCT's self-intersection check cannot certify. Open surfaces and polysurfaces move the same way: planar surfaces with or without holes, lofts, edge surfaces, Sweep1, Sweep2, open pipes, open extrusions, open cones and partial revolves. Each edit is sewn back and must keep its boundary, and a curved wall between two rims keeps its curvature when one rim moves. Revolve produces an exact OCCT shape with a replayable recipe.
- Solid-part Gumball motion keeps the shaded source and draws a complete prospective boundary wireframe for face/edge/vertex edits; only real analytic BRep boundaries are displayed after commit, and release performs one fully validated exact native solve. Free Perspective movement tracks on a camera-facing plane and Grid Snap rounds all three local CPlane coordinates in Rust instead of flattening depth. Non-uniform scaling of an edited NURBS solid retains exact transformed NURBS and avoids OCCT's unsafe optional face-recognition healer.
- `.narwhal` is the built-in active project format. Its Rust-validated v1 container stores document metadata plus checksum-protected exact OCCT BReps for every analytic master; Save/Open does not depend on the optional openNURBS writer.
- STEP remains the recommended exact solid interchange format for other CAD/CAM systems.
- Public source includes Narwhal's bounded ONBREP helper and an exact-revision developer build recipe. The helper closes only sub-tolerance UV trim round-off and refuses larger gaps, preserving exact 3-D curves and surfaces. Published binaries do not bundle the linked openNURBS helper while the official “no restrictions” documentation conflicts with the repository's “All Rights Reserved” license text. Some Open CASCADE multi-face solids therefore cannot yet be stored as exact `.3dm` Breps by packaged builds; the application warns rather than claiming a faceted save is exact.
- PDF and Adobe Illustrator import can use an optional converter pack that is not bundled in the 0.4.7 AppImage or Windows portable ZIP.
- CorelDRAW `.cdr` v15 Import/Open and Export/Print use Narwhal's built-in checked codec. Version 16, 17, and 18 Import/Open uses pinned TraceDraw Rust crates statically compiled into a bundled Narwhal helper and isolated by the native process supervisor. These paths require no separately installed executable or service. Curves, source layers, per-object colours, ordinary plot widths, and exact 0.003-inch Hairline survive the supported path; output defaults to genuine v15 for CorelDRAW 2015/X7 compatibility, with v17 available as an explicit Export/Print option. Other generations and explicitly unsupported structures may use an optional local CLI fallback with a visible note; malformed files are refused without silently switching parsers.

## Platform and architecture roadmap

**Release: 0.4.7.** Both shipping assets use public source revision `94534c954dc77f1f1d22f2be6064bedcc8cd1f5b`, source epoch `1793144099`; the release tag retains that asset-build revision, not this documentation follow-up. See [release checksums and sizes](README.md#release-source-and-integrity).

The Linux 0.4.7 AppImage has been built and its inventory verified. Final Linux source release gates, including the full suite, and AppDir/extracted-AppImage verification passed. Actual shipping-archive acceptance on NVIDIA/OpenGL passed Gumball mouse extrusion then scale (24/24), background Gumball checks (47/47), all 28 meshed-master transform/round-trip combinations, render quality (47/47), preferences write/restore (183/193 checks), window overlays (105/105), component/fillet interaction (74/74), and shell interaction (73/73). Clean-copy and overlay negative controls went RED and restored production paths passed. Package inventory and archive digest were unchanged after acceptance. These Linux results do not certify Windows.

The supported prerelease target is Linux x86_64 through `Narwhal_3D-0.4.7-x86_64.AppImage`. All 290 bundled ELFs were measured to require at most glibc 2.28, below the enforced Ubuntu 22.04/glibc 2.35 ceiling. The portable runtime and welcome include the current native UI patches; the separate source-development executables still require GLIBC 2.43 and must not ship. The native welcome window appears while the bundled runtime initializes. Pristine Ubuntu-desktop and optional GPU-backend acceptance remain open; the measured ELF ceiling alone does not certify either. The AppImage is unsigned.

The Windows asset name for this release is `Narwhal_3D-0.4.7-Windows-x86_64.zip`, an unsigned, portable early preview. It contains a native MSVC-built runtime, locked Windows `rhino3dm`/OpenCASCADE dependencies, native Explorer file dialogs, Rust Grid Snap and exact geometry cores, and a `Narwhal3D.exe` launcher with original Narwhal icon/version resources. Normal startup routes through the bundled native welcome window while deterministic headless/version checks remain direct. The welcome checks for newer compatible GitHub releases only after its first paint and installs only after an explicit user action and matching GitHub SHA-256 digest. The 0.4.7 engineering build completed using a verified, unchanged native runtime; directory and ZIP verifiers passed. Actual extracted-final-ZIP WinBoat acceptance passed background Gumball checks (47/47), all 28 meshed-master transform/round-trip combinations, Gumball mouse press/drag/release (24/24), preferences write/restore (184/194 checks), window overlays (112/112), and all six representation gates, including fresh-process round-trip. GUI acceptance used temporary Mesa llvmpipe software rendering, not a simulated UI; it is not physical-GPU certification. The temporary driver was removed and the exact 7,705-file package inventory restored and hash-checked; the shipping ZIP never contained that driver. It is not yet a supported Windows release: physical-GPU acceptance, complete internal PE resource replacement, installer/file associations, and optional signing remain open. macOS is also not a current release target. An [experimental macOS 14+ source-build guide](docs/macos-build.md) is available for Apple Silicon and Intel contributors; exact CPython 3.13 geometry wheels exist for both architectures, but no Mac build has yet passed the real-window, exact-geometry, project round-trip, packaging, signing, or notarization gates. The privacy-conscious [Windows build collector and engineering guide](docs/windows-build.md) records the Windows platform evidence and remaining gates.

During the 0.x phase, commands, interaction behavior, document orchestration, and much of the UI remain in Python for rapid correction and testing. Exact geometry runs through compiled Open CASCADE code. The dependency-free Rust pure core now accelerates measured-winning STL encode, OBJ/DXF decode, length/calculator/coordinate parsing, and sufficiently long macro workloads; any native half measured slower than Python stays disabled in production. The bundled Blender runtime also carries native Narwhal patches.

After feature and interaction behavior stabilize, the production application layer is intended to move into the Blender source tree and compile as one native Narwhal 3D application. Python may remain for tests, build tooling, and prototypes, but the released application should not depend on a separately installed add-on.
