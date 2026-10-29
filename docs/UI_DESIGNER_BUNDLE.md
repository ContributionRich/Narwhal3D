# Narwhal 3D — editable interface preview

Extract the entire ZIP and open **index.html**. No server, installation, account,
API key or network connection is required. The welcome window appears first;
close it to see the workspace. Press **F1** for the interface navigator.

Direct views: `index.html?screen=workspace`, `?screen=guide`, `?screen=ai-settings`,
`?screen=print`, `?screen=open`, `?screen=color`. Add `&theme=dark` or `&scale=1`.
Default auto scale reproduces the compact supplied screenshot at its browser width.
The navigator lets you change theme/scale, inspect every included surface and add
an explicitly synthetic selection scene. No extra preview banner/control bar takes
space away from the application replica.

## What is editable and interactive

- All 15 authored menu groups and their nested submenus, shortcuts and unavailable
  states, resolved from actual command payloads rather than guessed label words.
- All 15 tool categories, palettes, flyouts and two-button tooltips.
- Command console, real command names/step labels/options, autocomplete and Escape.
  Advancing prompts is a UI demonstration, not geometry execution.
- Four viewport titles, arrow-only menus, double-click/corner maximize/restore,
  viewport tabs, display-only grids and a synthetic selection/Gumball sample.
- Layers, Properties, Display, Bug Report, and AI Assistant; resizable right dock.
- Side-anchored AI Settings: provider, endpoint, model, variable-name credential
  settings, discovery/error text, draft cancellation, local preview Save.
- Native welcome recreation: New/Recent/Open/About, unit choices, News/Tips/logo,
  loading/ready progress, empty/synthetic recent states, keyboard/focus behavior.
- Print Setup, overlap chooser/hover feedback, units, custom tolerance and the
  source-sized Fillet Radius dialog.
- Full source-backed Gumball, viewport/object/control-point context menus and
  viewport-tab/add-tab menus.
- Browser equivalents for OS-owned Open/Save/Color/save-confirmation dialogs.
- View preferences reproduce the real Theme/Show Unavailable menu. The app does
  not currently define a general Preferences window; none is invented here.

## Fidelity — what “1:1” can and cannot mean

The workspace is reconstructed using the actual logical dimensions, semantic
colors, labels and project artwork. The package includes the **same Inter font
file used by Blender** and the **Ubuntu-Light font used by egui's welcome window**,
with their licenses. `playground/references/workspace-light.png` is an inspected,
blank real-application UI-report capture for comparison, not the interactive UI.

This is **not a claim of exhaustive pixel equivalence**. Browser and BLF/egui text
rasterization differ. The grid is a display-only approximation, not Blender's GPU
shader/projection. OS-native file and color pickers vary by desktop and cannot have
one universal exact layout; their HTML layouts are explicitly approximate. Additional
command-specific operator dialogs not listed above remain source-only. F1 exposes
the dialog-by-dialog provenance and limitations. Welcome automatic fading is paused
so a designer can inspect it. Do not count these limits as implemented product parity.

The HTML contains real DOM controls, not a screenshot with invisible hotspots.
You can edit layout, typography, colors and interactions directly in the files below.

## Files to edit

| File | Purpose |
|---|---|
| `index.html` | Workspace structure and script/style loading |
| `playground/style.css` | Shell, menus, tool palettes, right dock, settings and navigator |
| `playground/app.js` | Browser-only state, menus, panels, viewports and interaction routing |
| `playground/welcome.js`, `welcome.css` | Welcome window and its four tabs |
| `playground/dialogs.js`, `dialogs.css` | Dialogs, draft handling, dropdowns and picker |
| `playground/fonts/` | Unmodified Inter/Ubuntu fonts, license texts and provenance |
| `data/ui_data.json`, `ui_data.js` | Actual exported menus, categories, command metadata, themes and layouts |
| `assets/` | Application icons, branding and original artwork |
| `source/` | Current Python/Rust UI and supporting source snapshot for integration |
| `data/source_symbols.json` | Exact source function/class locations |
| `data/coverage.json` | Included regions, editable/source-only distinctions and limitations |
| `MANIFEST.json` | Input/output hashes and sizes; working-tree snapshot, not a commit |

F1's **Export design state** downloads only browser design choices/tokens as JSON.
It does not save application preferences. Theme edits in `ui_data.json` must also
be reflected in `ui_data.js`, which loads directly from disk without fetch.

## Safe simulation boundary

No CAD operation runs; no actual document, file list, recent-file list, bug report,
provider discovery or chat response is accessed. “Send”, “Open”, “Save”, “Print”
and update controls demonstrate UI state only. File/color/print results are local
callbacks. AI settings contain an environment-variable NAME, never an API key.
Network connections are blocked by Content-Security-Policy. There is no telemetry.

User documents, credentials, private preferences, VM captures, report attachments,
vendor/runtime binaries and historical design packs are excluded. Source files named
preferences.py or bugreport.py are implementation code, not private data. This
source snapshot alone cannot build the CAD application.

## Delivering changes to the designer

Return the modified HTML/CSS/JS, any new licensed assets, and a short CHANGES.md
mapping changes to real source modules. Include light/dark screenshots and narrow/
wide dock states. Keep the original manifest as the baseline; do not call it a
manifest of edited files. Do not replace product Python/Rust with HTML.

## Repository maintainer commands

`python3 -B scripts/build_ui_bundle.py` builds and validates
`dist/Narwhal3D-UI-Designer.zip` atomically. Source/asset hashes are rechecked after
export. `--out` selects another output directory for isolated browser testing.
`python3 -B spike/test_ui_bundle.py` runs bounded inventory/archive/safety tests.
The browser regression driver is `scripts/ui_bundle/test_preview.cjs` (see its header).

Builder: 3 GiB/no-swap/330-second service bound. Pure bundle tests: 768 MiB/60s.
Use bounded browser runs separately; never launch full product builds just to test
HTML. Missing-entry assertions report filenames, never binary/source dictionary
contents. Existing product geometry and other agents' work must remain untouched.
