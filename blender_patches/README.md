# Narwhal 3D native runtime patches

Narwhal 3D uses a patched Blender 5.2.2 rendering runtime when
`tools/blender-patched/blender` exists and falls back to the stock development bundle
otherwise. The primary launcher is `narwhal3d.sh`; test harnesses select the same runtime.


## `apply_narwhal3d_branding.py` — native Narwhal 3D identity

The idempotent branding patch changes only presentation surfaces: main/temporary window
titles, normal console/version and quit banners, X11 class, Wayland app ID, and Wayland
window icon. It deliberately does not alter file formats, version numbers, Python module
names, or behavior. `spike/branding_test.py` applies it to a miniature source tree,
asserts every configured anchor plus idempotence. Set
`NARWHAL3D_VALIDATE_NATIVE_RUNTIME=1` after rebuilding to validate the ignored installed
runtime too. Run the patch before the two behavioral patches:

```bash
python3 blender_patches/apply_narwhal3d_branding.py
```


## `app_drawn.patch` — keep app-drawn objects out of the dependency graph

**Why.** The application draws CAD curves itself, while Blender retains their objects for
selection. Keeping those objects in Blender's dependency graph adds evaluation and rebuild
work even though Blender does not draw them. An excluded collection avoids that work but
removes the view-layer base required for selection.

**What the patch does.** It adds a collection flag, `Collection.narwhal_app_drawn`
(`COLLECTION_APP_DRAWN`). Objects in such a collection:

* keep their view-layer base, so selection, the active object and hide flags work as before;
* get `BASE_APP_DRAWN`, which `DepsgraphBuilder::need_pull_base_into_graph` refuses.

An ordinary scene never sets the flag, so Blender behaves as stock everywhere else.

**What the app does in return.**

* `bridge.curve_collection()` sets the flag when the build has it (`bridge.app_drawn_ok()`).
* Objects outside the graph raise no `depsgraph_update_post` event when their mesh or
  matrix is written. Every application writer therefore calls
  `bridge.note_display_changed(...)`, and the curve pass listens.
* Their evaluated `bound_box` reads zeros; `bridge.local_box_corners(ob)` reads the mesh.
* Zoom's `bridge.blender_sees_curves()` clears the flag for its duration, because the
  framing operators read evaluated bounds.

Test: `spike/test_app_drawn.py`, run in `run_tests.sh` on the patched binary.

## `cad_boundary_wires.patch` — CAD solids show Brep boundaries, never render triangles

Narwhal 3D shades analytic solids with viewport tessellation but draws only meaningful Brep
boundaries. Blender's wire overlay can also expose manifold tessellation edges when adjacent
triangles turn, producing fragments that are not part of the exact object's topology.

The patch adds `Mesh.narwhal_boundary_wires`. On a flagged mesh, the edge-factor extractor
hides every manifold edge; split/non-manifold boundaries remain. The application flags Breps,
Extrusions and Surfaces, whose exact Brep edges are already split by `bridge._tessellation_bmesh`.
Real mesh objects stay unflagged and may show their actual polygon topology.

Apply with `python3 blender_patches/apply_cad_boundary_wires.py`. The headless wiring gate is
in `spike/test_complex_import.py`; `spike/cad_wire_gui_check.py` verifies real pixels in both
Shaded and Wireframe and proves actual mesh objects retain their topology.

## `apply_deferred_first_present.py` — first visible frame is Narwhal

Blender normally presents its factory layout before processing `--python`, which makes
Outliner/Properties/timeline chrome flash before Narwhal's asynchronous four-view layout
finishes. The launcher sets `NARWHAL3D_DEFER_FIRST_PRESENT=1`; this patch suppresses the
initial clear/swap and, on the validated OpenGL backend, subsequent buffer presentation
until the viewport state machine sets `NARWHAL3D_UI_READY=1`. Back-buffer drawing, reflow,
timers and events continue. A nine-second Python watchdog requests a diagnostic presentation
without claiming layout readiness, while a native ten-second fallback prevents an invisible
process even if bootstrap fails. Vulkan and other stateful backends
present normally because suppressing a release after image acquisition would violate their
acquire/release lifecycle. Stock Blender ignores both variables and retains the development
fallback.

## Rebuilding

Use `scripts/build_blender_linux.sh` with the exact source commit and build inputs pinned
in `packaging/linux-x86_64.lock.json`. The build script applies all required patches, including pre-close approval,
validates their anchors, compiles the runtime, and writes the native-runtime provenance
marker required by release assembly. Downloaded source, build trees, installed runtimes,
and wheel caches remain ignored build artifacts.

See `packaging/README.md` for required arguments, dependency installation, resource
requirements, and release-verification steps.

## `apply_preclose.rs` — opt-in cancellable application close

Rust tooling applies a small C++/RNA patch before exit scheduling. The default is
unchanged Blender behavior. `WindowManager.narwhal_defer_close` opts into a
process-local mailbox; native last-main-window close, native QuitRequest, and the
interactive quit operator then coalesce into `narwhal_close_requested` instead of
scheduling `WM_exit`. Neither property is serialized into a .blend.

The application installs its main-thread coordinator timer before opting in.
Save, repair/revalidation, Cancel, latest-edit protection and updater completion
all use the existing save coordinator. Only completed protection permits the app
to disable the guard briefly and request native quit. No host handles are handed
to a detached process, and no window is hidden during saving. Unregister removes
the timer/guard. Older runtimes retain synchronous exit_pre recovery and log that
native close deferral is unavailable.

`scripts/build_blender_linux.sh` compiles/applies/checks this Rust patch tool and
includes its hash in native-runtime provenance. Release assembly/verification
require the new input. The tool can also run independently:

```sh
rustc --edition 2024 blender_patches/apply_preclose.rs -o /tmp/apply_preclose
/tmp/apply_preclose /absolute/blender-source/source/blender
/tmp/apply_preclose /absolute/blender-source/source/blender --check
```

Tests: `rustc --test` on the patch tool, `spike/test_preclose.rs` against a patched
source tree (exact C++ function assertions and deliberate native-guard mutation),
`spike/test_responsive_exit.py` for actual Blender save/callback integration, and
`spike/test_native_runtime_provenance.py` for build/record/package checks.
Source-level tests do not replace the required owned-candidate GUI qualification.
Do not publish or attest a runtime before its executable is rebuilt and that
qualification passes.
