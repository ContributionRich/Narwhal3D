"""TESTS: the startup layout state machine at every interface scale.

The desktop launch goes through the native welcome window, which forces the runtime onto
X11. There the window is in physical pixels (MEASURED 3360x1761 on a HiDPI desktop, so
interface scale 1.7), where a native Wayland launch of the same desktop opens at
1920x1006 (scale 0.95). Step 5 of `_build_viewports_gui` accepted the carved command-bar
strip only below a literal 200 px, and the 153 px bar at scale 1.7 is 260 px. So the bar
stayed a viewport, the dock bands were never found, and the 2x2 split cascaded into
seven viewports -- the "viewplanes everywhere" startup.

Two modes:

  headless (`-b`): checks `_top_strip_settled` against `_command_bar_px` across every
      scale `uiscale.auto_scale` can return. Deterministic on any machine.
        tools/blender-patched/blender -b --factory-startup --python-exit-code 1 \
            --python spike/test_startup_layout.py

  window: run after launch/bootstrap.py by spike/startup_layout_gui.sh, which launches the
      way the welcome window does. Waits for the layout and asserts exactly four
      viewports, the command bar and both dock bands, and the readiness signal.
"""
import os
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

checks = 0
failures = []


def check(cond, label):
    global checks
    checks += 1
    print(f"  {'ok  ' if cond else 'FAIL'} {label}", flush=True)
    if not cond:
        failures.append(label)


def _finish():
    print(f"{checks - len(failures)}/{checks} checks passed", flush=True)
    if failures:
        print("FAILED:", *failures, sep="\n  ", flush=True)
    return 1 if failures else 0


def headless():
    from narwhal3d import design, ui, uiscale

    print("-- the command-bar strip is recognised at every interface scale --")
    base = design.CATEGORY_H + design.COMMAND_BOX_H + 2 * design.COMMAND_MARGIN
    check(ui._command_bar_px(1.0) == base,
          f"the bar is {base} px at scale 1.0 (category strip + console + margins)")
    check(ui._command_bar_px(1.7) == round(base * 1.7),
          "and it follows the interface scale (MEASURED 1.7 on the welcome's X11 window)")

    scales = []
    s = uiscale.MIN_SCALE
    while s <= uiscale.MAX_SCALE + 1e-9:
        scales.append(round(s, 2))
        s += uiscale.STEP
    carved_ok = [sc for sc in scales
                 if ui._top_strip_settled(ui._command_bar_px(sc), ui._command_bar_px(sc))]
    check(carved_ok == scales,
          "a strip the height of the bar is accepted at EVERY scale "
          f"{scales[0]}..{scales[-1]} (rejected at: "
          f"{sorted(set(scales) - set(carved_ok)) or 'none'})")
    check(ui._top_strip_settled(256, ui._command_bar_px(1.7)),
          "the MEASURED 256 px strip at scale 1.7 is accepted (the bug: it was not)")

    # Before the split reflows, the shortest top viewport is the whole unsplit one. At
    # scale s the window is at least 1048*s tall (uiscale.auto_scale), and the menu bar
    # and status bar take well under 60*s of that.
    unsplit_early = [sc for sc in scales
                     if ui._top_strip_settled(round((1048 - 60) * sc),
                                              ui._command_bar_px(sc))]
    check(not unsplit_early,
          "the unsplit viewport is NOT mistaken for the strip at any scale "
          f"(mistaken at: {unsplit_early or 'none'})")
    return _finish()


_gui = {"t": 0.0}
GUI_TIMEOUT = 45.0
POLL = 0.5


def _gui_tick():
    from narwhal3d import ui, uiscale

    _gui["t"] += POLL
    ready = os.environ.get("NARWHAL3D_UI_READY") == "1"
    watchdog = os.environ.get("NARWHAL3D_PRESENT_WATCHDOG") == "1"
    busy = getattr(ui, "_viewports_in_progress", False)
    if not (ready or watchdog) or busy:
        if _gui["t"] < GUI_TIMEOUT:
            return POLL
    win = bpy.context.window_manager.windows[0]
    areas = list(win.screen.areas)
    v3d = [a for a in areas if a.type == "VIEW_3D"]
    info = [a for a in areas if a.type == "INFO"]
    scale = uiscale.current()
    print(f"-- startup layout in a {win.width}x{win.height} window at scale {scale:g} "
          f"(WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY')!r}) --", flush=True)
    for a in areas:
        print(f"     {a.type:16s} x={a.x} y={a.y} w={a.width} h={a.height}", flush=True)
    check(ready and not watchdog,
          "the layout signalled readiness itself (not released by the watchdog)")
    check(len(v3d) == 4, f"exactly four viewports (found {len(v3d)})")
    check(ui._cmdbar_area() is not None, "the top strip became the command bar")
    check(ui.left_band_present() and ui.right_band_present(),
          "both dock bands were carved and converted")
    check(len(info) == 3, f"three INFO areas: command bar + two bands (found {len(info)})")
    if ui._command_bar_px(scale) < 200:
        print(f"  NOTE: at scale {scale:g} the bar is {ui._command_bar_px(scale)} px, under "
              "the old literal 200 px limit, so this window cannot expose that defect; "
              "the headless mode of this suite still does.", flush=True)
    code = _finish()
    print("STARTUP LAYOUT DONE", flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


if bpy.app.background:
    sys.exit(headless())
else:
    bpy.app.timers.register(_gui_tick, first_interval=POLL)
