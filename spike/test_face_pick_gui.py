"""OURS: real-window face prompt clicks for Shell and MoveFace (Python needs bpy).

Public standalone harness, loaded BEFORE launch/bootstrap.py, never under -b:
  blender --factory-startup --enable-event-simulate --python-exit-code 1 \
    --python spike/test_face_pick_gui.py --python launch/bootstrap.py
Require a fresh absolute temporary NARWHAL3D_SETTINGS_FILE,
NARWHAL3D_DEFER_FIRST_PRESENT=1 and the normal native core/OCCT environment, as
spike/v4_live_gui.sh provides. Bound the process to 190s (180s watchdog).
Output: FACE_PICK_RESULT JSON marker + artifacts/face_pick/result.json.

The bug this guards: a face prompt is a POINT step, so a click used to be read as the
CPlane point under the cursor and then matched to the nearest face. Looking down on a
box resting on the CPlane that picked the BOTTOM face; from any other view the point was
off the solid ("Shell: the pick (x, y, 0.0) is not on the solid"). A face prompt now
takes the front face under the cursor (`ui._answer_face_prompt_with_click`).

Only fixture setup and command starts use APIs; every pick is a real
Window.event_simulate click. FACE_PICK_MUTATION=no-hook installs one process-local
negative control (the click falls back to the CPlane point) and MUST fail.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'face_pick'
START = time.monotonic()
RESULT = {'status': 'running', 'checks': [], 'mutation': os.environ.get('FACE_PICK_MUTATION', '')}
POSITION = (0, 0)
FINISH_LOCK = threading.Lock()


def check(ok, label):
    RESULT['checks'].append({'ok': bool(ok), 'label': label})
    print('FACE_PICK', 'PASS' if ok else 'FAIL', label, flush=True)
    if not ok:
        raise AssertionError(label)


def finish(code, error=None):
    if not FINISH_LOCK.acquire(blocking=False):
        os._exit(1)
    RESULT.update(status='failed' if code else 'passed', error=error,
                  elapsed_s=time.monotonic() - START)
    try:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / 'result.json').write_text(json.dumps(RESULT, indent=2, default=str))
        print('FACE_PICK_RESULT ' + json.dumps(RESULT, default=str), flush=True)
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)  # Timer exceptions alone do not fail Blender.


def window():
    return bpy.context.window_manager.windows[0]


def event(key, value):
    window().event_simulate(type=key, value=value, x=POSITION[0], y=POSITION[1])


def move(point):
    global POSITION
    POSITION = tuple(round(v) for v in point)
    event('MOUSEMOVE', 'NOTHING')
    yield .12


def tap(key, point=None):
    if point is not None:
        yield from move(point)
    event(key, 'PRESS')
    yield .06
    event(key, 'RELEASE')
    yield .3


def screen(label, world):
    """Window pixel of `world` in the pane named `label`, and the pane's (area, space, region)."""
    area = ui._pane_by_label(label)
    region, space = ui._window_region(area), area.spaces.active
    xy = ui._project_2d(region, space.region_3d, world)
    check(xy is not None, f'{label}: {world} projects into the pane')
    return (region.x + xy[0], region.y + xy[1]), (area, space, region)


def frame(label, location, distance):
    from mathutils import Vector
    area = ui._pane_by_label(label)
    rv3d = area.spaces.active.region_3d
    rv3d.view_location = Vector(location)
    rv3d.view_distance = distance
    rv3d.update()
    ui.set_active_pane(area)
    ui._tag_redraw_viewports()


def face_id(world):
    return bridge.faces_at_points(st.ctx.geom_by_name(BOX), [world])[0]


def add_box():
    global BOX
    _, BOX = st.ctx.add_geometry(bridge.make_box((0, 0, 0), (20, 20, 0), 10), 'FacePickBox')
    st.feed_line('SelNone')
    ui._tag_redraw_viewports()
    yield .5


def start(command, label, at):
    """Start `command` and answer its solid prompt with a real click on the box."""
    check(st.active_command is None, f'{command}: start only while idle')
    st.feed_line('SelNone')  # a preselected box would skip the solid prompt
    yield .1
    st.start_command(command)
    yield .25
    cmd = st.active_command
    point, _pane = screen(label, at)
    yield from tap('LEFTMOUSE', point)
    check(cmd.step_index == 1 and cmd.args and cmd.args[0] == BOX,
          f'{command}: a click on the box answers the solid prompt ({cmd.args})')
    return cmd


def shell_from_above():
    frame('Top', (10, 10, 0), 60)
    yield .4
    top, bottom = face_id((10, 10, 10)), face_id((10, 10, 0))
    cmd = yield from start('Shell', 'Top', (5, 5, 10))
    point, _pane = screen('Top', (10, 10, 10))
    yield from tap('LEFTMOUSE', point)
    check(cmd._faces == [top] and not cmd._picks,
          f'Top view: the click takes the TOP face {top}, not the bottom {bottom} under it '
          f'on the CPlane ({cmd._faces}, typed {cmd._picks})')
    # Two pixels inside the top face's edge is still the face, not that edge.
    edge, _pane = screen('Top', (19.9, 10, 10))
    yield from tap('LEFTMOUSE', (edge[0], edge[1]))
    check(cmd._faces == [top], f'a click just inside the edge is still face {top} ({cmd._faces})')
    yield from tap('RET')
    out = st.ctx.geom_by_name(next(o.Attributes.Name for o in st.model.file.Objects))
    rec = kernel.recipe_of(out) or {}
    check(st.active_command is None and rec.get('kind') == 'shell'
          and rec.get('remove') == [top], f'Enter shells with the top face removed ({rec.get("remove")})')
    st.feed_line('Undo')
    yield .4
    check(st.ctx.geom_by_name(BOX) is not None, 'Undo restores the box')


def side_face_in_perspective():
    frame('Perspective', (10, 10, 5), 70)
    yield .4
    sides = [(10, 0, 5), (20, 10, 5), (10, 20, 5), (0, 10, 5)]
    chosen = None
    for world in sides:
        point, (area, space, region) = screen('Perspective', world)
        hit = ui.pick_brep_part(area, space, region, point[0] - region.x, point[1] - region.y,
                                faces_only=True)
        if hit == (BOX, 'Face', face_id(world)):
            chosen = (world, point, hit[2])
            break
    check(chosen is not None, 'a side face of the box is visible in Perspective')
    world, point, side = chosen
    cmd = yield from start('Shell', 'Perspective', (10, 10, 10))
    yield from tap('LEFTMOUSE', point)
    check(cmd._faces == [side] and not cmd._picks and st.active_command is cmd,
          f'Perspective: the click takes side face {side} at {world}, never a CPlane point '
          f'off the solid ({cmd._faces}, typed {cmd._picks})')
    yield from tap('ESC')
    check(st.active_command is None and st.ctx.geom_by_name(BOX) is not None,
          'Esc cancels Shell, box untouched')


def moveface_in_perspective():
    top = face_id((10, 10, 10))
    cmd = yield from start('MoveFace', 'Perspective', (10, 10, 10))
    point, _pane = screen('Perspective', (12, 12, 10))
    yield from tap('LEFTMOUSE', point)
    check(cmd._sub_id == top and cmd.step_index == 2,
          f'MoveFace: the click takes the top face {top} ({cmd._sub_id}, step {cmd.step_index})')
    yield from tap('ESC')
    check(st.active_command is None, 'Esc cancels MoveFace')


def install_mutation():
    mode = RESULT['mutation']
    if mode == 'no-hook':
        ui._answer_face_prompt_with_click = lambda *args, **kwargs: False
    elif mode:
        raise ValueError('Unknown process-local mutation: ' + mode)


def run():
    global st
    deadline = time.monotonic() + 45
    while not (os.environ.get('NARWHAL3D_UI_READY') == '1' and not ui._viewports_in_progress
               and ui._viewport_count() == 4 and ui.is_input_active()):
        if time.monotonic() > deadline:
            raise RuntimeError('Bootstrap not ready within 45s')
        yield .15
    st = ui.app_state
    check(not list(st.model.file.Objects), 'refuse nonempty startup document')
    check(ob.available(), 'OCCT available; no skipped geometry assertions')
    ui.set_gumball(False)
    install_mutation()
    yield from add_box()
    for case in (shell_from_above, side_face_in_perspective, moveface_in_perspective):
        RESULT['case'] = case.__name__
        yield from case()


def tick():
    try:
        with bpy.context.temp_override(window=window()):
            return next(STEPS)
    except StopIteration:
        finish(0)
    except BaseException:
        finish(1, traceback.format_exc())


threading.Thread(target=lambda: (time.sleep(180), finish(1, '180s watchdog expired')), daemon=True).start()
try:
    override = os.environ.get('NARWHAL3D_SETTINGS_FILE', '')
    check(bool(override), 'require disposable preferences before bootstrap')
    settings = Path(override).expanduser()
    check(settings.is_absolute() and settings.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
          and not settings.is_symlink(), 'refuse non-temporary/symlink settings')
    check('narwhal3d' not in sys.modules and not os.environ.get('NARWHAL3D_UI_READY'), 'test must precede bootstrap')
    check(os.environ.get('NARWHAL3D_DEFER_FIRST_PRESENT') == '1', 'require deferred launch environment')
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    import bpy
    from narwhal3d import ui, bridge, kernel
    from narwhal3d.kernel import occt_backend as ob
    check(not bpy.app.background, 'real GUI required')
    STEPS = run()
    bpy.app.timers.register(tick, first_interval=.1)
except BaseException:
    finish(1, traceback.format_exc())
