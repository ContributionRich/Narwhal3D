# Narwhal 3D project status

Narwhal 3D is an extremely early public prerelease. This page records what currently works and what remains incomplete so visible UI does not imply unsupported behavior. Package versions remain on the `0.4.x` line until the project owner explicitly lifts that cap.

## Command compatibility

Narwhal 3D is **not yet at full command parity with established NURBS CAD workflows**. It has a substantial working command set, but many commands, advanced options, selection routes, dialogs, and viewport tools remain missing or incomplete. A registered command name means that command is runnable; it does not guarantee every advanced option or interaction path is complete.

The authored menu hierarchy exposes all 169 commands currently registered by Narwhal, plus Recent Files and 362 individually named unavailable commands with `(Not implemented)` labels. File includes recent/recovery, template, and document groups; Help includes learning/reference and support groups. The desktop toolbar workspace contains 582 top-strip slots across fifteen tabs plus the 37-cell left tool palette and its 28 linked flyouts. Of the 525 non-separator top slots, 118 currently invoke registered commands; every unavailable action remains visible, identifies itself on hover, and explains the gap when clicked.

The issue tracker is the live source for bounded contribution work. Current one-command starter tasks include:

- [ClearUndo](https://github.com/ContributionRich/Narwhal3D/issues/4)
- [HideSwap](https://github.com/ContributionRich/Narwhal3D/issues/1)
- [LockSwap](https://github.com/ContributionRich/Narwhal3D/issues/2)
- [JoinCopy](https://github.com/ContributionRich/Narwhal3D/issues/3)

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
- STEP is the recommended exact solid interchange format.
- The initial public build does not bundle the optional openNURBS exact-Brep writer. Some Open CASCADE multi-face solids therefore cannot be stored as exact `.3dm` Breps; the application warns rather than claiming a faceted save is exact.
- PDF and Adobe Illustrator import can use an optional converter pack that is not bundled in the 0.4.0 AppImage.
- CorelDRAW `.cdr` Import, Open, Export, Print, and Print Setup are implemented through an optional local TraceDraw CLI. Structured import retains pages, layers, transforms, colours, and disconnected paths; output defaults to genuine CDR v17 for CorelDRAW 2015/X7 and remains readable by 2016/X8. TraceDraw is not bundled in 0.4.0, and unsupported CDR content or a missing CLI is reported explicitly.

## Platform and architecture roadmap

The current supported release target is Linux x86_64 through the AppImage bundle. Windows and macOS are planned but are not current release targets. A Windows executable is not published yet because it requires a native MSVC/Windows SDK build, Windows resource branding and launcher work, locked Windows dependencies, a platform-specific verifier, and real Windows GUI/GPU validation; a renamed or cross-wrapped Linux binary would not be a valid substitute. The privacy-conscious [Windows build collector](docs/windows-build.md) creates a reviewable Desktop ZIP with the system, GPU, toolchain, and dependency-reachability facts needed to prepare that build.

During the 0.x phase, commands, interaction behavior, document orchestration, and much of the UI are implemented in Python for rapid correction and testing. Exact geometry already runs through compiled Open CASCADE code, and the bundled Blender runtime carries native Narwhal patches.

After feature and interaction behavior stabilize, the production application layer is intended to move into the Blender source tree and compile as one native Narwhal 3D application. Python may remain for tests, build tooling, and prototypes, but the released application should not depend on a separately installed add-on.
