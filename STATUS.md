# Narwhal 3D project status

Narwhal 3D is an early public MVP. This page records what currently works and what remains incomplete so visible UI does not imply unsupported behavior.

## Command compatibility

Narwhal 3D is **not yet at Rhino command parity**. It has a substantial working command set, but many commands, advanced options, selection routes, dialogs, and viewport tools remain missing or incomplete. A registered command name means that command is runnable; it does not guarantee every advanced option or interaction path is complete.

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

All currently shipped Narwhal toolbar and panel icons are **original generated placeholders**. They replaced non-redistributable development references before the project was published. They are intentionally functional rather than final artwork.

A future icon set must be original or have clearly documented GPL-compatible redistribution terms. Contributions should include source artwork, licence information, and readable light/dark-theme behavior.

## Geometry and interchange limitations

- Viewport tessellation is display-only and must not become document or manufacturing geometry.
- Exact solid operations use Open CASCADE and must preserve analytic surfaces and airtightness.
- STEP is the recommended exact solid interchange format.
- The initial public build does not bundle the optional openNURBS exact-Brep writer. Some Open CASCADE multi-face solids therefore cannot be stored as exact `.3dm` Breps; the application warns rather than claiming a faceted save is exact.
- PDF and Adobe Illustrator import can use an optional converter pack that is not bundled in the initial release.
- Native CorelDRAW import/export is not part of the public build.

## Platform and architecture roadmap

The current supported release target is Linux x86_64 through the AppImage bundle. Other platforms are planned but not currently release targets.

During the 0.x phase, commands, interaction behavior, document orchestration, and much of the UI are implemented in Python for rapid correction and testing. Exact geometry already runs through compiled Open CASCADE code, and the bundled Blender runtime carries native Narwhal patches.

After feature and interaction behavior stabilize, the production application layer is intended to move into the Blender source tree and compile as one native Narwhal 3D application. Python may remain for tests, build tooling, and prototypes, but the released application should not depend on a separately installed add-on.
