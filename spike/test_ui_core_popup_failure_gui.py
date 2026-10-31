"""Actual Blender typing after terminal popup failures (not an input-active flag).

NARWHAL_UI_CORE_LIBRARY=... TMPDIR=/var/tmp/nwp python3 -B \
    spike/test_ui_core_popup_failure_gui.py --launch
Add --mutant to restore the escaping event boundary in this process only; the
same typing assertion must go RED. Reuses the popup launcher's locks, reserve,
read-only source mounts and private HOME. Python is necessary for bpy events.
"""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'spike'))
if '--launch' in sys.argv:
    from test_ui_core_popup_gui import launch
    os.environ['POPUP_FAILURE_MUTATION'] = str(int('--mutant' in sys.argv))
    raise SystemExit(launch(__file__))

import json
import threading
import time
import traceback
import bpy

sys.path.insert(0, str(ROOT))
OUT = Path(os.environ['VISIBILITY_07_OUTPUT'])
RESULT = dict(status='running', checks=[], events=[], reports=[], mutation=os.environ.get('POPUP_FAILURE_MUTATION'))
START = time.monotonic()
POSITION = (0, 0)
FINISH = threading.Lock()


def check(ok, label):
    RESULT['checks'].append(dict(ok=bool(ok), label=label))
    print('POPUP_FAILURE', 'PASS' if ok else 'FAIL', label, flush=True)
    if not ok:
        raise AssertionError(label)


def finish(error=None):
    if not FINISH.acquire(False):
        return
    RESULT.update(status='failed' if error else 'passed', error=error, elapsed_s=time.monotonic()-START)
    (OUT / 'result.json').write_text(json.dumps(RESULT, indent=2))
    print('POPUP_FAILURE_RESULT', RESULT['status'], error or '', flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(int(bool(error)))


def window():
    return bpy.context.window_manager.windows[0]


def event(key, value='PRESS', pos=None, **kwargs):
    global POSITION
    if pos is not None:
        POSITION = tuple(round(v) for v in pos)
    window().event_simulate(type=key, value=value, x=POSITION[0], y=POSITION[1], **kwargs)


def tap(key, pos=None, **kwargs):
    if pos is not None:
        event('MOUSEMOVE', 'NOTHING', pos)
        yield .08
    event(key, 'PRESS', **kwargs)
    yield .08
    event(key, 'RELEASE', **{k: v for k, v in kwargs.items() if k != 'unicode'})
    yield .18


def wait(predicate, label, seconds=8):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('timed out: ' + label)
        yield .05
    check(True, label)


def run():
    from narwhal3d import ui, shell_popup as popup, native_popup
    yield from wait(lambda: os.environ.get('NARWHAL3D_UI_READY') == '1'
                    and not ui._viewports_in_progress and ui.is_input_active(), 'bootstrap ready', 50)
    real_event = popup.handle_event
    real_report = popup._report_failure
    def report(exc):
        RESULT['reports'].append(str(exc))
        real_report(exc)
    popup._report_failure = report
    if os.environ.get('POPUP_FAILURE_MUTATION') == '1':
        def escaped(e, context=None):
            if popup._model is None:
                return False
            popup._model.resize((context.window.width, context.window.height))
            return popup._model.handle_event(e)
        real_event = escaped
    def observe(e, context=None):
        consumed = real_event(e, context)
        RESULT['events'].append((e.type, e.value, consumed))
        return consumed
    popup.handle_event = observe
    region = ui._window_region(ui._cmdbar_area())
    command_point = (region.x + region.width * .7, region.y + 15)
    yield from tap('LEFTMOUSE', command_point)
    yield from tap('A', unicode='a')
    check(ui.app_state.input_buffer == 'a', 'control: actual typing before failure')
    yield from tap('A', ctrl=True)
    yield from tap('BACK_SPACE')

    model = popup.open([{'label': 'parent', 'children': [{'label': 'badmetric', 'action': 1}]}],
                       (350, 650), 'Failure probe', lambda *a: None, context=bpy.context)
    check(isinstance(model, native_popup.NativePopup), 'real persistent native owner')
    measure = model.measure
    calls = []
    def fail_metric(text, size):
        calls.append(text)
        if text == 'badmetric':
            raise LookupError('injected runtime lazy metric failure')
        return measure(text, size)
    model.measure = fail_metric
    yield from tap('HOME')
    yield from tap('RIGHT_ARROW')
    check(model.failed and not model.is_open, 'native owner disposed after metric failure')
    yield from tap('LEFTMOUSE', command_point)
    yield from tap('B', unicode='b')
    # First assertion after typing is the witness killed by the escaping mutant.
    check(ui.app_state.input_buffer == 'b', 'actual Blender typing recovers after metric failure')
    check(popup._model is None, 'failed identity detached')
    check(calls.count('badmetric') == 1, 'failed measurement never replayed')
    check(('RIGHT_ARROW', 'RELEASE', True) in RESULT['events'], 'failing key release shielded')
    check(len(RESULT['reports']) == 1, 'metric failure reported exactly once')

    model = popup.open([{'label': 'leaf', 'action': 1}], (350, 650), 'Draw failure',
                       lambda *a: None, context=bpy.context)
    box = model.panels[0].row_rects[0]
    pos = ((box[0]+box[2])/2, (box[1]+box[3])/2)
    event('MOUSEMOVE', 'NOTHING', pos)
    yield .08
    event('LEFTMOUSE', pos=pos)
    yield .08
    read_panel = model.lib.narwhal_popup_panel
    def fail_panel(*args):
        model.lib.narwhal_popup_panel = read_panel
        return 2
    model._panels_revision = 0
    model.lib.narwhal_popup_panel = fail_panel
    for area in window().screen.areas:
        area.tag_redraw()
    yield from wait(lambda: model.failed, 'actual draw projection failure disposes native owner')
    check(popup._model is None, 'draw failure detached')
    before = len(RESULT['events'])
    event('LEFTMOUSE', 'RELEASE')
    yield .18
    check(('LEFTMOUSE', 'RELEASE', True) in RESULT['events'][before:], 'draw failure mouse release shielded')
    yield from tap('LEFTMOUSE', command_point)
    yield from tap('C', unicode='c')
    check(ui.app_state.input_buffer == 'bc', 'actual Blender typing recovers after draw failure')

    replacements, callbacks = [], []
    def callback(*args):
        callbacks.append(args)
        replacements.append(popup.open([{'label': 'replacement', 'action': 2}], (350, 650),
                           'Replacement', lambda *a: None, context=bpy.context))
        raise LookupError('injected callback failure after replacement')
    popup.open([{'label': 'activate', 'action': 1}], (350, 650), 'Callback failure',
               callback, context=bpy.context)
    yield from tap('HOME')
    yield from tap('RET')
    check(len(replacements) == 1 and popup._model is replacements[0] and popup.is_open(),
          'callback-created replacement survives callback exception')
    check(callbacks == [(1, None)], 'callback dispatched exactly once')
    yield from tap('ESC')
    yield from tap('LEFTMOUSE', command_point)
    yield from tap('D', unicode='d')
    check(ui.app_state.input_buffer == 'bcd', 'actual Blender typing recovers after callback failure')
    check(len(RESULT['reports']) == 3, 'three failures reported once each')
    finish()


def tick():
    try:
        with bpy.context.temp_override(window=window()):
            return next(STEPS)
    except StopIteration:
        finish()
    except BaseException:
        finish(traceback.format_exc())


def watchdog():
    time.sleep(130)
    finish('130s GUI watchdog')


STEPS = run()
threading.Thread(target=watchdog, daemon=True).start()
bpy.app.timers.register(tick, first_interval=.1)
