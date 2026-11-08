"""OURS: real-window edge prompt clicks for MoveEdge (Python needs bpy).

Public standalone harness, loaded BEFORE launch/bootstrap.py, never under -b:
  blender --factory-startup --enable-event-simulate --python-exit-code 1 \
    --python spike/test_edge_pick_gui.py --python launch/bootstrap.py
Require a fresh absolute temporary NARWHAL3D_SETTINGS_FILE,
NARWHAL3D_DEFER_FIRST_PRESENT=1 and the normal native core/OCCT environment, as
spike/v4_live_gui.sh provides. Bound the process to 190s (180s watchdog).
Output: EDGE_PICK_RESULT JSON marker + artifacts/edge_pick/result.json.

The bug this guards: MoveEdge's "Select an edge" is a POINT step, so a click used to be
read as the CPlane point under the cursor; away from the CPlane that point is on no edge
("MoveEdge: the pick (x, y, 0.0) is not on an edge of the solid"). MoveEdge now offers
`pick_edge`, so a click takes the edge under the cursor, at "Select edges" (solid and
edge at once) and at "Select an edge", while its later point prompts stay point picks.

Only fixture setup and command starts use APIs; every pick is a real
Window.event_simulate click. EDGE_PICK_MUTATION=no-pick-edge removes the capability
in this process (the click falls back to selection / the CPlane point) and MUST fail.
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
OUT = ROOT / 'artifacts' / 'edge_pick'
START = time.monotonic()
RESULT = {'status': 'running', 'checks': [], 'mutation': os.environ.get('EDGE_PICK_MUTATION', '')}
POSITION = (0, 0)
FINISH_LOCK = threading.Lock()


def check(ok, label):
    RESULT['checks'].append({'ok': bool(ok), 'label': label})
    print('EDGE_PICK', 'PASS' if ok else 'FAIL', label, flush=True)
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
        print('EDGE_PICK_RESULT ' + json.dumps(RESULT, default=str), flush=True)
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


def pane():
    area = ui._pane_by_label('Perspective')
    return area, area.spaces.active, ui._window_region(area)


def screen(world):
    _area, space, region = pane()
    xy = ui._project_2d(region, space.region_3d, world)
    check(xy is not None, f'{world} projects into the Perspective pane')
    return region.x + xy[0], region.y + xy[1]


def edge_target():
    """A point 30% along a top edge whose pixel picks exactly that edge of the box. Not
    the midpoint or an end: the default Mid/End snaps would put even a CPlane point
    there, hiding the bug."""
    area, space, region = pane()
    for world in ((6, 0, 10), (20, 6, 10), (14, 20, 10), (0, 14, 10)):
        x, y = screen(world)
        hit = ui.pick_brep_part(area, space, region, x - region.x, y - region.y)
        if hit is not None and hit[0] == BOX and hit[1] == 'Edge':
            return world, (x, y)
    check(False, 'a top edge of the box is pickable in Perspective')


def start(command):
    check(st.active_command is None, f'{command}: start only while idle')
    st.feed_line('SelNone')  # a preselected box would skip the first prompt
    yield .1
    st.start_command(command)
    yield .25
    return st.active_command


def expected_edge(world):
    return st.active_command._resolve(st.ctx.geom_by_name(BOX), world, st.ctx)


def edge_at_select_edges():
    world, point = edge_target()
    cmd = yield from start('MoveEdge')
    want = expected_edge(world)
    yield from tap('LEFTMOUSE', point)
    check(cmd.step_index == 2 and cmd.args[0] == BOX and cmd._sub_id == want,
          f'a click on edge {want} at "Select edges" takes the solid and that edge '
          f'(step {cmd.step_index}, {cmd.args}, edge {cmd._sub_id})')
    # The next prompt is a point prompt: a click on the same edge is a POINT now.
    yield from tap('LEFTMOUSE', point)
    check(cmd.step_index == 3 and cmd._sub_id == want and len(cmd.args) == 3,
          f'"Point to move from" stays a point pick on the edge (step {cmd.step_index})')
    yield from tap('ESC')
    check(st.active_command is None, 'Esc cancels MoveEdge')


def edge_after_solid():
    world, point = edge_target()
    cmd = yield from start('MoveEdge')
    want = expected_edge(world)
    yield from tap('LEFTMOUSE', screen((7, 7, 10)))
    check(cmd.step_index == 1 and cmd.args == [BOX],
          f'a click on the top face answers "Select edges" with the solid ({cmd.args})')
    yield from tap('LEFTMOUSE', point)
    check(cmd.step_index == 2 and cmd._sub_id == want and st.active_command is cmd,
          f'"Select an edge": the click takes edge {want}, never a CPlane point off the '
          f'solid (step {cmd.step_index}, edge {cmd._sub_id})')
    yield from tap('ESC')
    check(st.active_command is None and st.ctx.geom_by_name(BOX) is not None,
          'Esc cancels MoveEdge, box untouched')


def install_mutation():
    mode = RESULT['mutation']
    if mode == 'no-pick-edge':
        del type(ui.commands.COMMANDS['moveedge']).pick_edge
    elif mode:
        raise ValueError('Unknown process-local mutation: ' + mode)


def run():
    global st, BOX
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
    from mathutils import Vector
    area, space, _region = pane()
    space.region_3d.view_location = Vector((10, 10, 5))
    space.region_3d.view_distance = 70
    space.region_3d.update()
    ui.set_active_pane(area)
    _, BOX = st.ctx.add_geometry(bridge.make_box((0, 0, 0), (20, 20, 0), 10), 'EdgePickBox')
    ui._tag_redraw_viewports()
    yield .5
    for case in (edge_at_select_edges, edge_after_solid):
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
    from narwhal3d import ui, bridge
    from narwhal3d.kernel import occt_backend as ob
    check(not bpy.app.background, 'real GUI required')
    STEPS = run()
    bpy.app.timers.register(tick, first_interval=.1)
except BaseException:
    finish(1, traceback.format_exc())
