"""Actual projected Gumball handle press/drag/release smoke (Blender RNA integration).

Run serially under /tmp/narwhal3d_gui.lock, MemoryMax=4G, MemorySwapMax=0,
with launcher-selected native cores/OCCT and fresh disposable settings:
  PYTHONDONTWRITEBYTECODE=1 NARWHAL3D_DEFER_FIRST_PRESENT=1
  NARWHAL3D_SETTINGS_FILE=/tmp/<fresh-directory>/preferences.json
  timeout 140s tools/blender-patched/blender --factory-startup \
    --enable-event-simulate --python-exit-code 1 \
    --python spike/test_gumball_extrude_scale_gui.py --python launch/bootstrap.py
Unset inherited NARWHAL3D_UI_READY, NARWHAL3D_THEME and NARWHAL3D_WELCOME_REQUEST.
Test precedes bootstrap. Output is stdout only; require GUMBALL_GUI_RESULT passed,
exit 0 and no asynchronous tracebacks. No production monkeypatches or direct
_gumball_begin/update/end calls: events go through the real modal operator.
"""
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
START = time.monotonic()
CHECKS = []
FINISH = threading.Lock()
POSITION = (0, 0)


def check(ok, label):
    CHECKS.append((bool(ok), label))
    print("GUMBALL_GUI", "PASS" if ok else "FAIL", label, flush=True)
    if not ok:
        raise AssertionError(label)


def finish(code, error=None):
    if not FINISH.acquire(blocking=False):
        os._exit(1)
    print("GUMBALL_GUI_RESULT " + json.dumps({
        "status": "passed" if code == 0 else "failed", "checks": CHECKS,
        "elapsed_s": time.monotonic() - START, "error": error}), flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def watchdog():
    time.sleep(120)
    finish(1, "120s watchdog expired")


def window():
    return bpy.context.window_manager.windows[0]


def event(kind, value, point=None):
    global POSITION
    if point is not None:
        POSITION = tuple(round(v) for v in point)
    window().event_simulate(type=kind, value=value, x=POSITION[0], y=POSITION[1])


def wait_for(predicate, label, seconds=45):
    end = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > end:
            raise AssertionError("Timed out: " + label)
        yield .1
    check(True, label)


def pane(label):
    area = ui._pane_by_label(label)
    region, space = ui._window_region(area), area.spaces.active
    ui.set_active_pane(area)
    space.region_3d.view_location = Vector((0, 0, 2.5))
    space.region_3d.view_distance = 22
    space.region_3d.update()
    ui._tag_redraw_viewports()
    return area, region, space


def drag_handle(area, region, space, kind, axis, amount):
    frame, handles = ui._gumball_handles(area, space, region)
    matches = [h for h in handles if h.kind == kind and h.axis == axis]
    check(bool(matches), "requested handle is visible in the chosen pane")
    handle = matches[0]
    a, b = handle.hot[0]
    middle = tuple((a[i] + b[i]) / 2 for i in range(3))
    projected = ui._project_2d(region, space.region_3d, middle)
    point = (region.x + projected[0], region.y + projected[1])
    check(ui._gumball_hit(area, space, region, *point).key == handle.key,
          "projected target resolves to requested handle")
    event('MOUSEMOVE', 'NOTHING', point)
    yield .15
    event('LEFTMOUSE', 'PRESS')
    yield .2
    st = ui._gumball_drag
    check(st is not None and st['kind'] == kind and st['axis'] == axis,
          "real mouse press begins the requested Gumball drag")
    travel = amount if kind == gumball.EXTRUDE_HANDLE else st['scale_at'] * (amount - 1)
    delta = tuple((st['axis_2d'][1][i] - st['axis_2d'][0][i])
                  * travel / st['axis_len'] for i in range(2))
    event('MOUSEMOVE', 'NOTHING', tuple(st['mouse0'][i] + delta[i] for i in range(2)))
    yield .3
    captured = dict(st)
    check(abs(captured['total'] - amount) < .06,
          "real mouse motion produces requested distance/ratio within pixel rounding")
    event('LEFTMOUSE', 'RELEASE')
    yield .4
    check(ui._gumball_drag is None and not ui.app_state.ctx.undo.recording,
          "real release commits and closes the drag transaction")
    return captured


def run():
    yield from wait_for(lambda: os.environ.get('NARWHAL3D_UI_READY') == '1'
                       and not ui._viewports_in_progress and ui._viewport_count() == 4
                       and ui.is_input_active(), "four panes and modal input ready")
    ctx = ui.app_state.ctx
    check(ctx.model.object_count() == 0, "refuse nonempty startup document")
    _, profile = ctx.add_geometry(bridge.make_circle((0, 0, 0), 3), 'Circle')
    ctx.select_by_names([profile])
    profile_before = ctx.geom_by_name(profile).Encode()
    ui.set_gumball(True)
    check(ui.set_gumball_alignment('World') is not None,
          "world alignment exposes extrusion Z in Front and scale X in Top")
    area, region, space = pane('Front')
    yield .3
    extrude = yield from drag_handle(area, region, space, gumball.EXTRUDE_HANDLE, 2, 5)
    names = [f.Attributes.Name for f in ctx.model.file.Objects if f.Attributes.Name != profile]
    check(len(names) == 1 and ui.selection_names() == [profile],
          "dot extrusion creates one solid and intentionally retains profile selection")
    result = names[0]
    before = bridge._occt_operand(ctx.geom_by_name(result))
    check(occt.is_airtight(before), "displayed extrusion has a valid airtight master")
    before_volume = occt.volume(before)
    before_bounds = occt.tight_bbox(before)
    ctx.select_by_names([result])
    ctx.undo.clear()
    area, region, space = pane('Top')
    yield .3
    bpy.context.view_layer.update()
    ob = bpy.data.objects[result]
    display_before = [tuple(ob.matrix_world @ v.co) for v in ob.data.vertices]
    scale = yield from drag_handle(area, region, space, gumball.SCALE, 0, 2)
    geom = ctx.geom_by_name(result)
    ratio, origin = scale['total'], scale['origin']
    expected_bounds = [tuple(origin[i] + (p[i] - origin[i]) * (ratio if i == 0 else 1)
                             for i in range(3)) for p in before_bounds]
    for shape in (kernel.master_of(geom), occt.from_recipe(kernel.recipe_of(geom))):
        check(shape is not None and occt.is_airtight(shape) and occt.face_count(shape) == 3
              and abs(occt.volume(shape) - before_volume * ratio) < 1e-6
              and all(abs(a - b) < 1e-6 for p, q in zip(occt.tight_bbox(shape), expected_bounds)
                      for a, b in zip(p, q)), "mouse-scaled master/recipe has exact bounds and volume")
    bpy.context.view_layer.update()
    ob = bpy.data.objects[result]
    displayed = [tuple(ob.matrix_world @ v.co) for v in ob.data.vertices]
    expected_display = [(origin[0] + (x - origin[0]) * ratio, y, z)
                        for x, y, z in display_before]
    check(len(displayed) == len(expected_display)
          and all(abs(a-b) < 2e-5 for p, q in zip(displayed, expected_display) for a, b in zip(p, q)),
          "render mesh follows exact scale once")
    check(ctx.geom_by_name(profile).Encode() == profile_before,
          "source circle is unchanged by both gestures")
    check(len(ctx.undo.undo_names()) == 1 and ui.selection_names() == [result],
          "one scale gesture creates one undo record and keeps result selected")
    ctx.undo_last()
    restored = bridge._occt_operand(ctx.geom_by_name(result))
    check(occt.is_airtight(restored) and abs(occt.volume(restored) - before_volume) < 1e-6,
          "one Undo restores exact unscaled solid")


def tick():
    try:
        with bpy.context.temp_override(window=window()):
            return next(STEPS)
    except StopIteration:
        finish(0)
    except BaseException:
        finish(1, traceback.format_exc())


threading.Thread(target=watchdog, daemon=True).start()
try:
    settings = Path(os.environ['NARWHAL3D_SETTINGS_FILE'])
    check(settings.is_absolute() and settings.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
          and not settings.exists() and not settings.is_symlink(), "fresh disposable settings required")
    check('narwhal3d' not in sys.modules and not os.environ.get('NARWHAL3D_UI_READY')
          and not os.environ.get('NARWHAL3D_THEME') and not os.environ.get('NARWHAL3D_WELCOME_REQUEST')
          and os.environ.get('NARWHAL3D_DEFER_FIRST_PRESENT') == '1', "isolated pre-bootstrap environment")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    import bpy
    from mathutils import Vector
    from narwhal3d import bridge, kernel, ui, gumball
    from narwhal3d.kernel import occt_backend as occt
    check(not bpy.app.background, "real GUI required")
    STEPS = run()
    bpy.app.timers.register(tick, first_interval=.1)
except BaseException:
    finish(1, traceback.format_exc())
