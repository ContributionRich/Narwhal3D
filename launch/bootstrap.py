"""Bootstrap the bundled runtime into the standalone Narwhal 3D application.

Run by ``narwhal3d.sh`` via the bundled runtime's Python-script option. Registers
the add-on straight from the source tree, removes host-specific UI chrome, and
leaves the user in the Narwhal 3D modeling session.
"""
import os
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _presentation_watchdog():
    """Reveal a diagnostic-capable window if layout startup never signals readiness."""
    if (os.environ.get("NARWHAL3D_DEFER_FIRST_PRESENT") == "1"
            and os.environ.get("NARWHAL3D_UI_READY") != "1"):
        print("[narwhal3d] presentation watchdog releasing incomplete UI",
              file=sys.stderr, flush=True)
        os.environ["NARWHAL3D_PRESENT_WATCHDOG"] = "1"
        screen = getattr(bpy.context, "screen", None)
        for area in getattr(screen, "areas", ()):
            area.tag_redraw()
    return None


_WELCOME_STATE = {"tries": 0, "lines": None}


def _welcome_request(state=None):
    """Consume one atomic request from the native welcome window.

    The welcome paints before Blender's layout is ready, so requests that need application
    state stay in this timer closure after their atomic file has been removed. This bridge must
    remain Python because only Blender's process can feed its UI command state machine.
    """
    if state is None:
        state = _WELCOME_STATE
    path = os.environ.get("NARWHAL3D_WELCOME_REQUEST")
    if not path:
        return None
    # A later click replaces an earlier pending choice until the UI is ready. The request
    # file is atomic, so reading it first on every timer tick makes the user's final choice win.
    if os.path.isfile(path):
        claimed = path + ".reading"
        try:
            # Claim by rename before opening. A newer atomic publication can then occupy the
            # original pathname and survive for the next tick instead of being unlinked here.
            os.replace(path, claimed)
            with open(claimed, encoding="utf-8") as stream:
                state["lines"] = stream.read().splitlines()
            state["tries"] = 0
        except OSError as exc:
            print(f"[narwhal3d] welcome request failed: {exc!r}",
                  file=sys.stderr, flush=True)
            return None
        finally:
            try:
                os.remove(claimed)
            except OSError:
                pass
    elif state["lines"] is None:
        state["tries"] += 1
        return 0.1 if state["tries"] < 600 else None
    lines = state["lines"] or []
    action = lines[0] if lines else ""
    if action == "open" and len(lines) > 1:
        result = _open_launch_file(lines[1])
        if result is None:
            state["lines"] = None
        return result
    if action in {"open-dialog", "new"}:
        if os.environ.get("NARWHAL3D_UI_READY") != "1":
            return 0.1
        from narwhal3d.state import app_state
        app_state.feed_line("Open" if action == "open-dialog" else "New")
    state["lines"] = None
    return None


def _session_only_preferences():
    """Keep our preference tweaks in-session.

    `use_preferences_save` defaults to True, which would write our theme and view
    changes into the user's real Blender config on exit. This app must not modify
    a Blender install the user may also use directly.
    """
    try:
        bpy.context.preferences.use_preferences_save = False
    except Exception as exc:  # noqa: BLE001
        print(f"[narwhal3d] could not pin preferences to session-only: {exc!r}")


def _suppress_splash():
    """No Blender splash screen — this is supposed to be its own app.

    There is no 'close splash' operator (only `wm.splash` / `wm.splash_about` to
    open it), so the only lever is the preference, set before the first draw.
    """
    try:
        bpy.context.preferences.view.show_splash = False
    except Exception as exc:  # noqa: BLE001
        print(f"[narwhal3d] could not suppress splash: {exc!r}")


def _clear_default_scene():
    """Remove Blender's default cube/light/camera so we open on an empty model."""
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)


def _configure_units():
    """the reference CAD application defaults: millimeters, no unit scaling surprises."""
    try:
        scene = bpy.context.scene
        scene.unit_settings.system = "METRIC"
        scene.unit_settings.length_unit = "MILLIMETERS"
    except Exception as exc:  # noqa: BLE001
        print(f"[narwhal3d] unit setup skipped: {exc!r}")


def _hide_blender_chrome():
    """Reduce Blender-isms: no overlays we don't want, CAD-friendly navigation."""
    for area in bpy.context.screen.areas:
        if area.type != "VIEW_3D":
            continue
        for space in area.spaces:
            if space.type != "VIEW_3D":
                continue
            # the reference CAD application shows a construction plane grid, not Blender's floor+axes combo
            overlay = space.overlay
            overlay.show_cursor = False
            overlay.show_text = False
            overlay.show_stats = False
            # CAD users expect no navigation-gizmo clutter.
            # (Do NOT set view_perspective here: setup_viewports() assigns each
            # pane its own mode -- the Perspective pane must stay PERSP.)
            space.show_gizmo_navigate = False


def _open_launch_file(path, tries=[0]):
    """Open the desktop-supplied file once the application UI is ready.

    `Open` replaces the model and rebuilds the display, so it must run after the add-on's
    viewport state machine has finished laying out the four panes. Otherwise the panes
    are built over a document that was opened too early. Polls every 0.25 s until it is
    done, then gives up waiting after ~30 s, or at once with no window (`-b`), and opens
    anyway rather than silently not opening.
    """
    tries[0] += 1
    try:
        from narwhal3d import ui

        screen = getattr(bpy.context, "screen", None)
        panes = [a for a in screen.areas if a.type == "VIEW_3D"] if screen else []
        ready = len(panes) >= 4 and not getattr(ui, "_viewports_in_progress", False)
    except Exception:  # noqa: BLE001
        ready = False
        screen = None
    if not ready and screen is not None and tries[0] < 120:
        return 0.25
    from open_arg import open_line

    from narwhal3d.state import app_state

    print(f"[narwhal3d] opening {path}")
    try:
        app_state.feed_line(open_line(path))
    except Exception as exc:  # noqa: BLE001
        print(f"[narwhal3d] could not open {path}: {exc!r}")
    return None


def main():
    # Register before any optional setup: if later bootstrap code fails, the event loop
    # still reveals a diagnostic-capable window instead of leaving an invisible process.
    if os.environ.get("NARWHAL3D_DEFER_FIRST_PRESENT") == "1":
        bpy.app.timers.register(_presentation_watchdog, first_interval=9.0)
    if os.environ.get("NARWHAL3D_WELCOME_REQUEST"):
        bpy.app.timers.register(_welcome_request, first_interval=0.1)
    _session_only_preferences()
    _suppress_splash()
    _clear_default_scene()
    _configure_units()

    import narwhal3d

    # Registration applies the CAD workspace theme and builds the command bar via a timer.
    narwhal3d.register()

    # Chrome tweaks must run after the add-on has reshaped the layout.
    def _late():
        try:
            _hide_blender_chrome()
        except Exception as exc:  # noqa: BLE001
            print(f"[narwhal3d] chrome setup skipped: {exc!r}")
        return None

    bpy.app.timers.register(_late, first_interval=0.3)

    # Handle a file passed by the desktop (`narwhal3d.sh %f`) or command line.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from open_arg import file_to_open

    path = file_to_open(sys.argv)
    if path:
        bpy.app.timers.register(lambda: _open_launch_file(path), first_interval=0.5)
    print("[narwhal3d] Narwhal 3D session ready")


main()
