"""Actual startup/keyboard/recall/framebuffer transcript acceptance in an owned GUI.

--launch reuses the qualified private-profile/bwrap/resource-lock launcher unchanged.
No source UI edits, installed artifact swap, geometry producers or guest input.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import runpy
import struct
import sys
import threading
import time
import traceback
import zlib

ROOT = Path(__file__).resolve().parents[1]
if '--launch' in sys.argv:
    launch = runpy.run_path(str(ROOT/'spike/test_ui_core_popup_gui.py'))['launch']
    raise SystemExit(launch(__file__))

OUT = Path(os.environ['VISIBILITY_07_OUTPUT'])
RESULT = dict(status='running', checks=[], events=[], drawings=[], ownership=[], captures=[])
START = time.monotonic()
FINISHED = threading.Lock()
POSITION = (0, 0)


def check(value, label):
    RESULT['checks'].append(dict(ok=bool(value), label=label))
    print('TRANSCRIPT_GUI', 'PASS' if value else 'FAIL', label, flush=True)
    if not value: raise AssertionError(label)


def finish(error=None):
    if not FINISHED.acquire(False): return
    before = RESULT.get('owned_source_sha256', {})
    after = {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in before}
    RESULT['owned_source_unchanged'] = before == after
    if before != after:
        error = 'Owned source changed during GUI acceptance: ' + str(error)
    RESULT.update(status='failed' if error else 'passed', error=error, elapsed_s=time.monotonic()-START)
    (OUT/'result.json').write_text(json.dumps(RESULT, indent=2, default=str))
    print('TRANSCRIPT_GUI_RESULT', RESULT['status'], flush=True)
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(int(bool(error)))


def window(): return bpy.context.window_manager.windows[0]


def event(kind, value='PRESS', point=None, **kwargs):
    global POSITION
    if point is not None: POSITION = tuple(round(x) for x in point)
    RESULT['events'].append((kind, value, POSITION, kwargs))
    window().event_simulate(type=kind, value=value, x=POSITION[0], y=POSITION[1], **kwargs)


def tap(kind, point=None, **kwargs):
    if point is not None:
        event('MOUSEMOVE', 'NOTHING', point); yield .1
    event(kind, 'PRESS', **kwargs); yield .08
    event(kind, 'RELEASE', **{k:v for k,v in kwargs.items() if k != 'unicode'}); yield .2


def type_command(text):
    # Escape is not a promise to erase recalled input. Reset via actual editor
    # events before the next independent command; never assign the input mirror.
    if ui.app_state.input_buffer:
        yield from tap('A', ctrl=True)
        yield from tap('BACK_SPACE')
        check(ui.app_state.input_buffer == '', 'real input reset before independent command')
    for ch in text:
        yield from tap(ch.upper(), unicode=ch)
    check(ui.app_state.input_buffer == text, 'real typed buffer '+text)
    yield from tap('RET')
    yield .35


def run():
    deadline = time.monotonic()+50
    while not (os.environ.get('NARWHAL3D_UI_READY') == '1' and ui.is_input_active()
               and not ui._viewports_in_progress and ui._cmdbar_area() is not None):
        if time.monotonic() > deadline: raise RuntimeError('normal bootstrap readiness deadline')
        yield .15
    st = ui.app_state
    check(not bpy.app.background and st.model.object_count() == 0, 'normal visible startup with disposable empty document')
    RESULT['owned_source_sha256'] = {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in (
        'narwhal3d/state.py', 'narwhal3d/command_history.py', 'narwhal3d/command_transcript.py',
        'narwhal3d/native_transcript.py', 'native/ui_core/src/transcript.rs',
        'native/ui_core/src/transcript_ffi.rs', 'native/ui_core/src/lib.rs')}
    n = st.history._native
    check(n is not None, 'normal AppState initialized native transcript (not injected)')
    handle = n.reply.handle
    initial = st.history[:]
    RESULT['initial_history'] = initial
    RESULT['library_sha256'] = hashlib.sha256(Path(os.environ['NARWHAL_UI_CORE_LIBRARY']).read_bytes()).hexdigest()
    def retained(label):
        check(st.history._native is n and n.reply.handle == handle, label+' retains persistent owner')
        RESULT['ownership'].append(dict(label=label, handle=handle, revision=n.reply.revision,
                                         lines=len(st.history), calls=n.calls, uploaded=n.uploaded, downloaded=n.downloaded))
    area = ui._cmdbar_area(); region = ui._window_region(area)
    check(region is not None, 'real command-bar region')
    point = (region.x+min(150, region.width//3), region.y+10)
    yield from tap('LEFTMOUSE', point)
    yield from type_command('SelNone')
    check('> SelNone' in st.history[:], 'real UI Enter append reaches Rust')
    check(st.history.last_command() == 'SelNone', 'native recall query agrees with real command transcript')
    retained('first command')
    yield from type_command('SelAll')
    lines = st.history[:]
    check(lines.index('> SelNone') < lines.index('> SelAll'), 'actual commands preserve transcript ordering')
    retained('second command')
    # Empty buffer means this is history recall rather than completion navigation.
    check(st.input_buffer == '', 'buffer cleared by normal Enter flow')
    yield from tap('UP_ARROW')
    check(st.input_buffer == 'SelAll', 'actual Up event consumes unchanged iteration-based recall')
    yield from tap('ESC')
    retained('recall and escape')
    context, _ = ui._bug_context()
    check(dict(context)['Recent command line (oldest first)'] == st.history[-15:], 'real bug-context consumer reads native last15')
    # Observe the existing painter; do not draw the transcript ourselves.
    last = st.history[-1]
    DRAWINGS.clear()
    ui.redraw_cmdbar()
    pixels = yield from capture('transcript-command-bar')
    rows = [r for r in DRAWINGS if r['text'] == last]
    RESULT['drawings'] = rows
    check(bool(rows), 'unchanged command-bar painter consumed current native tail')
    row = rows[-1]
    x0, y0 = round(region.x+row['x']), round(region.y+row['y'])
    w = max(1, round(ui._text_width(last, row['size'])))
    crop = pixels[max(0,y0-3):min(pixels.shape[0],y0+round(row['size'])+6), max(0,x0):min(pixels.shape[1],x0+w+3)]
    color = np.asarray(ui.CMD_HISTORY[:3])
    ink = int(np.count_nonzero(np.max(np.abs(crop-color),axis=-1) < .12))
    RESULT['framebuffer'] = dict(text=last, x=x0, y=y0, width=w, ink_pixels=ink)
    check(ink > 5, 'composed framebuffer contains transcript glyph pixels, not only model state')
    retained('repaint')
    traffic = n.uploaded
    for _ in range(4):
        ui.redraw_cmdbar(); yield .2
    check(n.uploaded == traffic, 'real repaints upload zero transcript bytes')
    retained('repeated paints')
    # Negative control changes only our leased facade in this process, never UI code.
    original = type(st.history).append
    type(st.history).append = lambda *a: None
    try:
        yield from type_command('SelNone')
        try:
            assert st.history.last_command() == 'SelNone', 'append suppression prevented the expected command record'
        except AssertionError as exc:
            RESULT['mutation'] = str(exc)
        else:
            raise AssertionError('GUI append-suppression mutant survived')
    finally:
        type(st.history).append = original
    yield from type_command('SelNone')
    check(st.history.last_command() == 'SelNone', 'restored actual append event GREEN')
    retained('restored event')


def tick():
    try:
        with bpy.context.temp_override(window=window()): return next(STEPS)
    except StopIteration: finish()
    except BaseException: finish(traceback.format_exc())


threading.Thread(target=lambda: (time.sleep(130), finish('130s watchdog')), daemon=True).start()
try:
    check('narwhal3d' not in sys.modules and not os.environ.get('NARWHAL3D_UI_READY'), 'test installed before normal bootstrap')
    check(Path(os.environ['NARWHAL3D_SETTINGS_FILE']).parent == OUT, 'private disposable settings profile')
    sys.path.insert(0, str(ROOT))
    import bpy
    import gpu
    import numpy as np
    from narwhal3d import ui
    DRAWINGS = []
    original_draw = ui._draw_text
    def observed_draw(x, y, text, size, *args, **kwargs):
        if bpy.context.area == ui._cmdbar_area():
            DRAWINGS.append(dict(x=float(x), y=float(y), text=str(text), size=float(size)))
            if len(DRAWINGS) > 2000: del DRAWINGS[:1000]
        return original_draw(x, y, text, size, *args, **kwargs)
    ui._draw_text = observed_draw
    # Reuse qualified host framebuffer/PNG helpers verbatim, not their scene setup.
    tree = ast.parse((ROOT/'spike/test_selection_visibility_07.py').read_text())
    helpers = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ('png', 'capture')]
    exec(compile(ast.Module(body=helpers,type_ignores=[]), 'qualified-framebuffer-helpers', 'exec'), globals())
    STEPS = run()
    bpy.app.timers.register(tick, first_interval=.1)
except BaseException:
    finish(traceback.format_exc())
