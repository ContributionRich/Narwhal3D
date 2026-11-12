# Library

The **Library** tab sits between **Bug Report** and **AI Assistant**. It is a
user-managed collection, separate from the current document. It starts empty;
there are no demo objects or simulated placement actions.

## Add and organize assets

1. Select geometry in the model.
2. Open Library and click **+ → Add selected object…**.
3. Enter a name and choose a category, then save. The asset is an independent
   exact `.narwhal` snapshot with a real square thumbnail.

Use **+ → Import asset file…** to reuse a native project, `.3dm`, or another format
supported by the existing importer. Imported mesh formats remain explicit meshes;
analytic solids and curves are not converted into thumbnail/display triangles.
A missing or unsupported resource is reported, never silently discarded.

The category **+** creates categories. The header **⋯** menu renames/deletes the
current user category. Deleting a category moves its entries to **Uncategorized**;
it does not delete their geometry. Uncategorized is protected. Search matches
asset names, categories and tags; matching entries are sorted by name.

Right-click a tile, or use its **⋯**, to insert, rename, move or remove it.
Removing an entry does not affect copies already placed in a document. Immutable
source bytes are currently retained internally for safe open readers and recovery;
automatic reclamation of unreferenced files is not yet implemented.

## Insert copies

- Click a tile to insert at the active construction-plane origin.
- Drag a tile onto a modeling viewport to place it at the picked/snapped point on
  that viewport's construction plane. The thumbnail follows the pointer.
- Escape cancels a drag without changing the document.

The insertion base is the source geometry's world-XY bounding-box **bottom centre**.
Source units are converted to document units before placement. Each insertion is
an independent copy; multi-object assets are grouped. Insertion is one ordinary
Undo/Redo operation, not a second history. Finish an active modeling command before
inserting. Capturing/organizing Library entries does not add document Undo records.

## Share libraries

The header **⋯** menu imports and exports `.narwhallib` packages. Export all
categories or only the current category. Import can merge, preserving current
entries, or replace the catalogue after explicit confirmation. Invalid packages
leave the current catalogue unchanged. Packages carry the geometry and previews,
not links to the source computer's asset files. CAD object/project metadata can
be retained in the captured asset; review it before publishing a shared library.

## Storage and developer setup

Library data lives under the user's application data directory:
- Linux: `$XDG_DATA_HOME/Narwhal3D/library`, normally `~/.local/share/Narwhal3D/library`.
- Windows: `%LOCALAPPDATA%/Narwhal3D/library`.

`NARWHAL_LIBRARY_ROOT` overrides the location for tests or a managed installation.
`NARWHAL_LIBRARY_CORE` selects a specific native helper. Source checkouts build it
with `python3 scripts/build_library_core.py --stage`; no Python storage fallback is
used when that helper is unavailable.

The native store checks schema versions, checksums and package boundaries. An operating-system lock
serializes writes and releases automatically after a hard crash. Busy operations
produce an actionable error rather than racing another writer. Back up/export a library before moving or replacing
its storage. The first version bounds source files to 256 MiB, thumbnails to 32 MiB,
and packages/expanded imports to 1 GiB. Preview dimensions are limited to
1024×1024 pixels. Large package transfers run off the UI thread.

## Current fidelity boundaries

Exact curves/analytic solids, ordinary materials, groups, flat layer state and
embedded planar pictures are covered by regression tests. Nested/nonstandard layer
state, group user text, custom linetypes and unsupported texture mappings currently
refuse explicitly when the existing unified Undo snapshots cannot replay them
faithfully. Point-only isolated thumbnails are also explicitly unsupported. These
limits do not prevent saving the source document; the Library operation fails
without altering it.

The panel is native application UI, not the supplied HTML concept embedded into
the product. Rust owns catalogue, validation, search/sort, packaging and grid layout;
Python is Blender/UI and exact-geometry host integration.

### Preview refresh and safe removal

Right-click an asset (or use its three-dot menu) and choose **Refresh preview**
to regenerate an older thumbnail without re-importing geometry. The source asset
and document Undo history are unchanged; Rust publishes a validated new immutable
thumbnail and catalogue checksum atomically. New thumbnails use transparent
backgrounds and camera-space fitting; flat axis-aligned curve drawings are shown
face-on with stronger strokes instead of being foreshortened into an isometric view.

**Remove from Library** opens a dedicated confirmation editor with Cancel and a
red **Remove asset** button. Existing model copies are unaffected. Category deletion
uses the same destructive confirmation treatment and moves its assets to Uncategorized.

Solid thumbnails use neutral-grey studio shading with the app's `studio.sl` light,
without heavy shadows/cavity darkening. Wire-only thumbnails retain stronger flat-lit
strokes. The Library uses the normal arrow cursor for buttons, tiles and drag
placement, with a text cursor only over editable fields. The drag ghost and hover
highlights provide interaction feedback without replacing the cursor with a hand.

Thumbnail drawing uses Blender's scene-linear-to-display image shader, not the
generic UI IMAGE shader. This preserves the saved PNG's brightness in the actual
Library dock and drag preview. Changing this drawing path fixes already-stored
thumbnails without touching their bytes; Refresh preview is only needed to
regenerate thumbnails created with older framing or lighting. The default real
Library GUI acceptance captures a box and compares opaque rendered face colours
against the saved PNG, not just the existence of visible pixels.

### Capture responsiveness and insertion cache

Adding the current selection snapshots exact geometry, stages native-save bytes
and renders its isolated thumbnail on Blender's main thread. Exact validation,
archive packing and Rust catalogue publication then run against frozen files on
a background worker. The entry is published only after validation succeeds; a
failed operation keeps the source document and library unchanged. The Library
reports validation progress while the UI remains responsive. This does not
remove the validation wait or make thumbnail rendering asynchronous.

After successful capture, the private validated model seeds a transient cache,
avoiding an immediate save/reopen/revalidate cycle on first insertion. Insertion
still clones/transforms independent exact geometry and uses the existing one-step
Undo transaction. At most two assets with stored files no larger than4MiB are
cached; file path/mtime/size changes invalidate cache lookup. The cache clears on
panel shutdown and never becomes a document or catalogue authority. An uncached
asset first opened from disk still undergoes normal exact validation.

Focused Linux measurement (one selected box in a201-object document, isolated
Blender host): first insertion1.887s before,0.086s after. Warm main-thread capture
preparation0.091s; background file validation/publication1.840s. These are local
measurements, not promises for large assemblies or end-to-end interactive scene
redraw. Geometry fidelity and validation were not weakened to achieve them.

### Tile presentation and hover

Library cards use the approved rounded card/thumb surfaces, subtle grid and
hairline border. Hover eases through a one-pixel lift, soft blue-tinted shadow
and accent border; the insertion hint fades/slides in over the thumbnail. Leaving
the tile reverses the transition. Rust supplies cached easing frames and tooltip
placement; Blender timers only schedule host redraws. No native subprocess runs
per animation frame, and the timer stops once the hover/tooltip state settles.

Pause over a tile for about0.55seconds to see its full name, category and insertion
hint. The tooltip follows pointer proximity, stays within the Library panel, and
dismisses on pointer leave, dragging, menus or editors. The arrow cursor remains
unchanged. Presentation changes apply to existing thumbnails without regenerating
their geometry or preview files.

### Cold asset loading

Clicking or dropping an uncached native asset now reads and validates its files
in the background through the Rust-owned load queue. Candidate construction is
stepped on the host thread before the insertion's single Undo transaction.
Changing documents while it loads cancels placement; ordinary edits in the same
document are retained. The bounded cache still makes newly captured/reused assets
fast. See [native file I/O](FILE_IO.md) for scope and remaining synchronous work.
