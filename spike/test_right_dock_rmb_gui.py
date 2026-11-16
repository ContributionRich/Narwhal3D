"""OURS: real right clicks on the right dock (bug reports #115 and #116).

Run by the scratch host, which owns the GUI lock, fresh settings and the time bound:

  python3 /var/tmp/workspace_dialogs_host/host.py --suite spike/test_right_dock_rmb_gui.py

A right click over EMPTY right-dock space must be claimed by the app, so Blender's
native context menu never opens there. A right click on a layer row must still open
the layer context menu (`ui._layer_menu_target`). Every click is a real
Window.event_simulate at window pixels; APIs only prepare the fresh document.
Writes result.json to $HARNESS_OUT (or a temp directory when run by hand).
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback
import types

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get('HARNESS_OUT') or os.environ.get('VISIBILITY_07_OUTPUT')
           or tempfile.mkdtemp(prefix='right-dock-rmb-'))
if __name__ == "__main__" and "--launch" in sys.argv:
    from test_ui_core_popup_gui import launch
    raise SystemExit(launch(Path(__file__).resolve()))

START = time.monotonic()
RESULT = {'status': 'running', 'checks': [], 'facts': {}, 'error': None}
FINISH_LOCK = threading.Lock()
WITNESS = 'right click on empty right-dock space is claimed by the app'


def check(ok, label):
    RESULT['checks'].append({'ok': bool(ok), 'label': label})
    print('RIGHT_DOCK_RMB', 'PASS' if ok else 'FAIL', label, flush=True)
    if not ok:
        raise AssertionError(label)


def finish(code, error=None):
    if not FINISH_LOCK.acquire(blocking=False):
        os._exit(1)
    RESULT.update(status='failed' if code else 'passed', error=error,
                  elapsed_s=round(time.monotonic() - START, 2), witness=WITNESS)
    try:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / 'result.json').write_text(json.dumps(RESULT, indent=2, default=str))
        print('RIGHT_DOCK_RMB_RESULT ' + json.dumps(RESULT, default=str), flush=True)
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)


def window():
    return bpy.context.window_manager.windows[0]


def send(kind, value, x, y):
    window().event_simulate(type=kind, value=value, x=round(x), y=round(y))


def click_right(x, y):
    window().cursor_warp(round(x), round(y))
    send("MOUSEMOVE", "NOTHING", x, y)
    yield .15
    send('RIGHTMOUSE', 'PRESS', x, y)
    yield .2
    send('RIGHTMOUSE', 'RELEASE', x, y)
    yield .3


def close_menu():
    send('ESC', 'PRESS', 0, 0)
    yield .1
    send('ESC', 'RELEASE', 0, 0)
    yield .3


def empty_point(region):
    """A window point inside the right band that is on no layer row and no tab."""
    x = region.x + region.width / 2
    for step in range(1, 40):
        y = region.y + region.height - step * 8
        if y < region.y + 2:
            break
        if ui.layer_row_at(x, y) is None and ui.right_tab_at(x, y) is None:
            return x, y
    return None


_native_info_menu_draws = 0
_native_menu_names = []


def record_native_info_menu(_menu, _context):
    global _native_info_menu_draws
    _native_info_menu_draws += 1
    _native_menu_names.append(type(_menu).__name__)


def empty_shell_point(area, top=False):
    region = next(r for r in area.regions if r.type == "WINDOW")
    x = region.x + region.width - 6
    ys = ([region.y + region.height - 6] if top else
          [region.y + 6 + step * 11 for step in range(max(1, region.height // 11 - 1))])
    for y in ys:
        if (ui.sidebar_action_at(x, y) is None and ui.layer_row_at(x, y) is None
                and ui.right_tab_at(x, y) is None):
            return x, y
    return None


def run():
    deadline = time.monotonic() + 60
    while not (os.environ.get('NARWHAL3D_UI_READY') == '1' and not ui._viewports_in_progress
               and ui._viewport_count() == 4 and ui.is_input_active()):
        if time.monotonic() > deadline:
            raise RuntimeError('Bootstrap not ready within 60s')
        yield .15
    check(not list(ui.app_state.model.file.Objects), 'refuse nonempty startup document')
    band = ui.right_band_area()
    check(band is not None, 'right dock band is carved')
    region = next(r for r in band.regions if r.type == 'WINDOW')
    yield .5
    check(bool(ui._layer_row_objs), 'Layers list has rows to right-click')
    # Keep a native Info context binding present even in isolated factory settings.
    # The application must claim its bands, not rely on a missing host keymap.
    info_keys = bpy.context.window_manager.keyconfigs.addon.keymaps.new(
        name="Info", space_type="INFO", region_type="WINDOW")
    native_context = info_keys.keymap_items.new("wm.call_menu", "RIGHTMOUSE", "PRESS")
    native_context.properties.name = "INFO_MT_context_menu"
    RESULT["facts"]["native_context_binding"] = native_context.active
    RESULT["facts"]["info_keymaps"] = [
        {"name": km.name, "space": km.space_type, "region": km.region_type,
         "right": [(item.idname, item.value, item.active,
                    getattr(item.properties, "name", "")) for item in km.keymap_items
                   if item.type == "RIGHTMOUSE"]}
        for km in bpy.context.window_manager.keyconfigs.default.keymaps
        if "Info" in km.name]



    point = empty_point(region)
    check(point is not None, 'the right dock has empty space below its layer rows')
    before = getattr(ui, '_right_dock_rmb_claims', None)
    yield from click_right(*point)
    after = getattr(ui, '_right_dock_rmb_claims', None)
    RESULT['facts']['empty_dock_rmb'] = {'point': list(point), 'before': before, 'after': after}
    yield from close_menu()
    check(before is not None and after is not None and after - before >= 2,
          WITNESS)

    shell_expectations = []
    for name, area, top in (("top toolbar", ui._cmdbar_area(), True),
                            ("command bar", ui._cmdbar_area(), False),
                            ("left bar", ui.left_band_area(), False)):
        check(area is not None, name + " is present")
        point = empty_shell_point(area, top)
        check(point is not None, name + " has a blank hit-testable point")
        before = getattr(ui, "_shell_band_rmb_claims", 0)
        native_before = _native_info_menu_draws
        yield from click_right(*point)
        after = getattr(ui, "_shell_band_rmb_claims", 0)
        RESULT["facts"][name] = {"point": list(point), "before": before, "after": after,
                                 "native_menu_draws": _native_info_menu_draws - native_before}
        yield from close_menu()
        shell_expectations.append((name, _native_info_menu_draws == native_before,
                                   after - before >= 2))
        for phase in ("PRESS", "RELEASE"):
            event = types.SimpleNamespace(type="RIGHTMOUSE", value=phase,
                mouse_x=point[0], mouse_y=point[1], ascii="", ctrl=False,
                shift=False, alt=False, oskey=False)
            operator = types.SimpleNamespace(_redraw=lambda: None)
            reply = ui.NARWHAL_OT_command_input.modal(operator, bpy.context, event)
            check(reply == {"RUNNING_MODAL"}, name + " modal consumes " + phase)

    RESULT["facts"]["native_menu_names"] = list(_native_menu_names)
    RESULT["facts"]["native_context_active_after"] = native_context.active
    for name, no_menu, claimed in shell_expectations:
        check(no_menu, name + " does not open the native Info Delete context menu")
        check(claimed, name + " claims right-click press and release")

    shell_before_view = getattr(ui, "_shell_band_rmb_claims", 0)
    view = next(a for a in ui._view3d_areas())
    vregion = next(r for r in view.regions if r.type == 'WINDOW')
    before_view = ui._right_dock_rmb_claims
    yield from click_right(vregion.x + vregion.width / 2, vregion.y + vregion.height / 2)
    yield from close_menu()
    check(ui._right_dock_rmb_claims == before_view,
          'right click in a viewport is not claimed by the right dock')

    check(getattr(ui, "_shell_band_rmb_claims", 0) == shell_before_view,
          "viewport right click is not claimed by any shell band")

    row = ui._layer_rows[0]
    centre = ((row[0] + row[2]) / 2, (row[1] + row[3]) / 2)
    check(ui.layer_row_at(*centre) is not None, 'first layer row is hit-testable at its centre')
    ui._layer_menu_target = None
    yield from click_right(*centre)
    check(ui._layer_menu_target == ui._layer_row_objs[0].name,
          'right click on a layer row still opens the layer context menu')
    yield from close_menu()


def tick():
    try:
        with bpy.context.temp_override(window=window()):
            return next(STEPS)
    except StopIteration:
        finish(0)
    except BaseException:
        finish(1, traceback.format_exc())


threading.Thread(target=lambda: (time.sleep(120), finish(1, '120s watchdog expired')),
                 daemon=True).start()
try:
    override = os.environ.get('NARWHAL3D_SETTINGS_FILE', '')
    check(bool(override), 'require disposable preferences before bootstrap')
    settings = Path(override).expanduser()
    check(settings.is_absolute() and settings.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
          and not settings.is_symlink(), 'refuse non-temporary/symlink settings')
    check('narwhal3d' not in sys.modules and not os.environ.get('NARWHAL3D_UI_READY'),
          'test must precede bootstrap')
    check(os.environ.get('NARWHAL3D_DEFER_FIRST_PRESENT') == '1', 'require deferred launch environment')
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    import bpy
    from narwhal3d import ui
    for menu_name in dir(bpy.types):
        if "context" in menu_name.lower() or menu_name == "INFO_MT_area":
            menu_type = getattr(bpy.types, menu_name)
            if isinstance(menu_type, type) and issubclass(menu_type, bpy.types.Menu):
                menu_type.append(record_native_info_menu)
    check(not bpy.app.background, 'real GUI required')
    STEPS = run()
    bpy.app.timers.register(tick, first_interval=.1)
except BaseException:
    finish(1, traceback.format_exc())
