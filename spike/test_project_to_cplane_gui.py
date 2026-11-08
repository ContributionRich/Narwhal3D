"""OURS: #114 -- a real click on ProjectToCPlane's DeleteInput command-bar button.

Run by the scratch host, which owns the GUI lock, fresh settings and the time bound:

  python3 /var/tmp/workspace_dialogs_host/host.py --suite spike/test_project_to_cplane_gui.py

Every toggle is a real Window.event_simulate left click at the option's pixels on the
command bar, and Enter is a real RET key. APIs only set up the fresh document and start
the command. Writes result.json to $HARNESS_OUT (or a temp directory when run by hand).
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
OUT = Path(os.environ.get('HARNESS_OUT') or tempfile.mkdtemp(prefix='project-to-cplane-gui-'))
START = time.monotonic()
RESULT = {'status': 'running', 'checks': [], 'facts': {}, 'error': None}
FINISH_LOCK = threading.Lock()
WITNESS = 'a click on DeleteInput toggles it and stays on the question'
INPUT_NAME = None
NO_LABEL = 'Delete input objects? <No>:'
YES_LABEL = 'Delete input objects? <Yes>:'


def check(ok, label):
    RESULT['checks'].append({'ok': bool(ok), 'label': label})
    print('PROJECT_TO_CPLANE_GUI', 'PASS' if ok else 'FAIL', label, flush=True)
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
        print('PROJECT_TO_CPLANE_GUI_RESULT ' + json.dumps(RESULT, default=str), flush=True)
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)


def window():
    return bpy.context.window_manager.windows[0]


def send(kind, value, x=0, y=0):
    window().event_simulate(type=kind, value=value, x=round(x), y=round(y))


def wait_until(predicate, label, timeout=20.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f'timed out waiting for: {label}')
        ui.redraw_cmdbar()
        yield .15


def delete_command():
    cmd = ui.app_state.active_command
    return cmd if cmd is not None and cmd.name == 'ProjectToCPlane' else None


def option_point(name):
    area = ui._cmdbar_area()
    region = next(r for r in area.regions if r.type == 'WINDOW')
    x0, x1 = next((b[1], b[2]) for b in ui.get_option_boxes() if b[0] == name)
    return region.x + (x0 + x1) / 2, region.y + ui._option_line_y + cmdbar.LINE_H / 2


def click_option(name):
    x, y = option_point(name)
    send('LEFTMOUSE', 'PRESS', x, y)
    yield .15
    send('LEFTMOUSE', 'RELEASE', x, y)
    yield .4


def undo_count():
    return ui.app_state.ctx.undo.undo_names().count('ProjectToCPlane')


def run():
    deadline = time.monotonic() + 60
    while not (os.environ.get('NARWHAL3D_UI_READY') == '1' and not ui._viewports_in_progress
               and ui._viewport_count() == 4 and ui.is_input_active()):
        if time.monotonic() > deadline:
            raise RuntimeError('Bootstrap not ready within 60s')
        yield .15
    st = ui.app_state
    check(not list(st.model.file.Objects), 'refuse nonempty startup document')
    global INPUT_NAME
    _oid, INPUT_NAME = st.ctx.add_geometry(bridge.make_line((0, 0, 5), (3, 2, 7)), 'CurveA')
    st.ctx.select_by_names([INPUT_NAME])
    yield .3
    st.feed_line('ProjectToCPlane')
    yield from wait_until(lambda: delete_command() is not None
                          and any(b[0] == 'DeleteInput' for b in ui.get_option_boxes()),
                          'the delete question draws its DeleteInput button')
    cmd = delete_command()
    check(cmd.step_index == 1 and cmd.opt['DeleteInput'] is False and cmd.steps[1][0] == NO_LABEL,
          'the delete question starts at No with the DeleteInput button drawn')
    undo_before = undo_count()

    yield from click_option('DeleteInput')
    cmd = delete_command()
    check(cmd is not None and cmd.opt['DeleteInput'] is True and cmd.steps[1][0] == YES_LABEL,
          WITNESS)
    check(model_has(INPUT_NAME) and not model_has_prefix('Projection'),
          'the first click runs nothing: the input is kept and no projection exists')
    check(undo_count() == undo_before, 'a click records no undo step')

    yield from click_option('DeleteInput')
    cmd = delete_command()
    check(cmd is not None and cmd.opt['DeleteInput'] is False and cmd.steps[1][0] == NO_LABEL,
          'a second click toggles back to No, still on the question')

    yield from click_option('DeleteInput')
    cmd = delete_command()
    check(cmd is not None and cmd.opt['DeleteInput'] is True,
          'a third click is Yes again, still on the question')

    send('RET', 'PRESS')
    yield .1
    send('RET', 'RELEASE')
    yield from wait_until(lambda: delete_command() is None, 'Enter finishes the command')
    check(not model_has(INPUT_NAME), 'Enter runs with the clicked Yes: the input is deleted')
    check(model_has_prefix('Projection'), 'Enter runs with the clicked Yes: a projection exists')
    check(undo_count() == undo_before + 1, 'the whole run is exactly one undo step')
    RESULT['facts']['undo_after'] = ui.app_state.ctx.undo.undo_names()[-3:]


def model_has(name):
    return ui.app_state.model.object_id_by_name(name) is not None


def model_has_prefix(prefix):
    return any((o.Attributes.Name or '').startswith(prefix)
               for o in ui.app_state.model.file.Objects)


def tick():
    try:
        with bpy.context.temp_override(window=window()):
            return next(STEPS)
    except StopIteration:
        finish(0)
    except BaseException:
        finish(1, traceback.format_exc())


threading.Thread(target=lambda: (time.sleep(150), finish(1, '150s watchdog expired')),
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
    from narwhal3d import ui, bridge, cmdbar
    check(not bpy.app.background, 'real GUI required')
    STEPS = run()
    bpy.app.timers.register(tick, first_interval=.1)
except BaseException:
    finish(1, traceback.format_exc())
