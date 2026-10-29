"""Actual AI dock/button -> Main router -> Window/GPU popup integration.

OURS: Blender UI test orchestration, not a compute implementation.
Host: python3 -B spike/test_ai_settings_popup_gui.py --launch [--narrow | --mutations]
One fresh GUI per invocation, shared lock, source read-only bwrap, no IP network,
3GiB/zero swap, 180s hard bound. No user app termination or production patching.
Only transport discovery is mocked; all events, painting and persistence are real.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'artifacts/ai_settings_integration'


def launch():
    import fcntl
    import subprocess

    mode = ('paint-regression' if '--paint-regression' in sys.argv else
            'narrow' if '--narrow' in sys.argv else 'mutations' if '--mutations' in sys.argv else 'baseline')
    out = EVIDENCE / (time.strftime('%Y%m%d-%H%M%S') + '-' + mode)
    out.mkdir(parents=True, exist_ok=False)
    scratch = Path(tempfile.mkdtemp(prefix='narwhal-ai-settings-gui-'))
    env = os.environ.copy()
    for key in ('NARWHAL3D_UI_READY', 'NARWHAL3D_THEME', 'NARWHAL3D_WELCOME_REQUEST',
                'NARWHAL3D_PRESENT_WATCHDOG', 'WAYLAND_DISPLAY'):
        env.pop(key, None)
    paths = subprocess.check_output(['bash', str(ROOT / 'narwhal3d.sh')], cwd=ROOT,
        env=dict(env, NARWHAL3D_LAUNCHER_TEST='native-paths'), text=True, timeout=10)
    for row in paths.splitlines():
        key, value = row.split('=', 1)
        if value:
            env[key] = value
    for key, dirname in (('HOME', 'home'), ('XDG_CONFIG_HOME', 'config'),
                         ('XDG_CACHE_HOME', 'cache'), ('XDG_DATA_HOME', 'data'), ('TMPDIR', 'tmp')):
        folder = scratch / dirname
        folder.mkdir()
        env[key] = str(folder)
    env.update(AI_SETTINGS_GUI_OUT=str(out), AI_SETTINGS_GUI_MODE=mode,
               AI_SETTINGS_GUI_SCRATCH=str(scratch), PYTHONDONTWRITEBYTECODE='1',
               NARWHAL3D_SETTINGS_FILE=str(scratch / 'preferences.json'),
               NARWHAL3D_NO_DIALOGS='1', NARWHAL3D_DEFER_FIRST_PRESENT='1',
               NARWHAL3D_STATUSBAR_HEIGHT='37', PYTHONPATH=str(ROOT / 'vendor/occt'),
               GHOST_BACKEND='X11', OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='1')
    source_paths = ('narwhal3d/ui.py', 'launch/bootstrap.py', 'narwhal3d/ai_settings_popup.py',
                    'narwhal3d/ai_assistant.py', 'narwhal3d/ai_transport.py')
    hashes = {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in source_paths}
    sandbox = ['bwrap', '--ro-bind', '/', '/', '--dev-bind', '/dev', '/dev', '--proc', '/proc',
               '--bind', str(scratch), str(scratch), '--bind', str(out), str(out),
               '--unshare-net', '--die-with-parent']
    scope = ['systemd-run', '--user', '--scope', '--quiet', '-p', 'MemoryMax=3G', '-p', 'MemorySwapMax=0']
    width, height = (900, 640) if mode == 'narrow' else (1440, 1000)
    cmd = scope + ['timeout', '--signal=TERM', '--kill-after=5s', '180s'] + sandbox + [
        str(ROOT/'tools/blender-patched/blender'), '--factory-startup', '-t', '2',
        '-p', '40', '40', str(width), str(height), '--enable-event-simulate', '--python-exit-code', '1',
        '--python', str(Path(__file__).resolve()), '--python', str(ROOT/'launch/bootstrap.py')]
    (out/'launch.json').write_text(json.dumps({'command': cmd, 'scratch': str(scratch),
        'source_sha256_before': hashes, 'native_paths': paths, 'mode': mode}, indent=2))
    print('AI_SETTINGS_GUI_EVIDENCE', out, flush=True)
    with open('/tmp/narwhal3d_gui.lock', 'a') as lock:
        deadline = time.monotonic()+60
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise TimeoutError('GUI lock busy for 60s; no process launched')
                time.sleep(.25)
        with (out/'run.log').open('w') as log:
            result = subprocess.run(cmd, cwd=ROOT, env=env, stdout=log,
                stderr=subprocess.STDOUT, timeout=190, check=False)
        log = (out/'run.log').read_text()
        print(log, flush=True)
        report = out/'result.json'
        data = json.loads(report.read_text()) if report.exists() else {'status': 'missing'}
        after = {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in source_paths}
        (out/'source_check.json').write_text(json.dumps({'before': hashes, 'after': after,
            'unchanged': hashes == after}, indent=2))
        bad_log = 'Traceback (most recent call last)' in log or 'Error in draw callback' in log
        if mode == 'baseline' and (out/'saved_config.json').exists():
            fresh = subprocess.run(scope + sandbox + [sys.executable, '-B', str(Path(__file__).resolve()),
                '--read-preferences'], cwd=ROOT, env=env, capture_output=True, text=True,
                timeout=30, check=False)
            (out/'fresh_preferences.log').write_text(fresh.stdout+fresh.stderr)
            print(fresh.stdout, flush=True)
            if fresh.returncode:
                return fresh.returncode
        print('AI_SETTINGS_GUI_COMPLETE', mode, data.get('status'), 'exit', result.returncode, flush=True)
        return result.returncode or int(data.get('status') != 'passed' or bad_log or hashes != after)


if '--launch' in sys.argv:
    raise SystemExit(launch())

if '--read-preferences' in sys.argv:
    import importlib
    import types
    package = types.ModuleType('narwhal3d')
    package.__path__ = [str(ROOT/'narwhal3d')]
    sys.modules['narwhal3d'] = package
    preferences = importlib.import_module('narwhal3d.preferences')
    expected = json.loads((Path(os.environ['AI_SETTINGS_GUI_OUT'])/'saved_config.json').read_text())
    actual = preferences.load()['ai_config']
    assert actual == expected, (actual, expected)
    print('AI_SETTINGS_FRESH_PROCESS_PREFS_PASS', json.dumps(actual))
    raise SystemExit(0)

import inspect
import textwrap
import threading
import traceback
from unittest.mock import patch

START = time.monotonic()
OUT = Path(os.environ['AI_SETTINGS_GUI_OUT'])
SCRATCH = Path(os.environ['AI_SETTINGS_GUI_SCRATCH'])
MODE = os.environ['AI_SETTINGS_GUI_MODE']
RESULT = {'status': 'running', 'mode': MODE, 'checks': [], 'captures': {}, 'events': [],
          'mutations': [], 'worker_calls': [], 'owned_pid': os.getpid()}
FINISH_LOCK = threading.Lock()
POSITION = (0, 0)
DRAW = []
TEXT = []
SAVES = []
BUTTON_CLICKS = []
TRANSPORT = {'mode': 'models', 'gate': None}
SECRET = 'not-a-real-key-ai-settings-fixture-only'
SHORT_ERROR = 'Cannot connect. Check the endpoint and retry.'


def check(ok, label, *, fatal=True):
    RESULT['checks'].append({'ok': bool(ok), 'label': label})
    print('AI_SETTINGS_GUI', 'PASS' if ok else 'FAIL', label, flush=True)
    if not ok and fatal:
        raise AssertionError(label)


def finish(error=None):
    if not FINISH_LOCK.acquire(False):
        return
    code = int(bool(error) or any(not c['ok'] for c in RESULT['checks']))
    RESULT.update(status='failed' if code else 'passed', error=error, elapsed_s=time.monotonic()-START)
    (OUT/'result.json').write_text(json.dumps(RESULT, indent=2, default=str))
    print('AI_SETTINGS_GUI_RESULT', json.dumps({'status': RESULT['status'], 'checks': len(RESULT['checks']),
        'error': error, 'output': str(OUT)}), flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def window():
    return bpy.context.window_manager.windows[0]


def event(kind, value='PRESS', point=None, **kwargs):
    global POSITION
    if point is not None:
        POSITION = tuple(round(v) for v in point)
    RESULT['events'].append((kind, value, POSITION, kwargs))
    window().event_simulate(type=kind, value=value, x=POSITION[0], y=POSITION[1], **kwargs)


def tap(kind, point=None, **kwargs):
    if point is not None:
        event('MOUSEMOVE', 'NOTHING', point)
        yield .06
    event(kind, 'PRESS', **kwargs)
    yield .06
    event(kind, 'RELEASE', **kwargs)
    yield .16


def settle():
    for area in window().screen.areas:
        area.tag_redraw()
    yield .18
    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
    yield .12


def center(r):
    return ((r[0]+r[2])/2, (r[1]+r[3])/2)


def model():
    check(popup.is_open(), 'popup open for interaction')
    return popup._model


def settings_button():
    rx, ry = ui._ai['region']
    r = next(rect for name, rect in ui._ai['lay']['buttons'] if name == 'settings')
    return (rx+r[0], ry+r[1], rx+r[2], ry+r[3])


def open_button(wait=True):
    before = len(BUTTON_CLICKS)
    yield from tap('LEFTMOUSE', center(settings_button()))
    check(popup.is_open() and len(BUTTON_CLICKS) == before+1,
          'actual existing dock settings button invoked popup')
    if wait:
        deadline = time.monotonic()+4
        while model().busy and time.monotonic() < deadline:
            yield .05
        check(not model().busy, 'real Main worker/poll delivered discovery')
    yield from settle()


def control(key):
    m = model()
    for _ in range(35):
        r = m.controls[key]
        clip = m.control_clip(key)
        if r[1] >= clip[1] and r[3] <= clip[3]:
            break
        yield from tap('WHEELDOWNMOUSE' if r[1] < clip[1] else 'WHEELUPMOUSE', center(m.body))
    r = m.controls[key]
    check(r[1] >= m.control_clip(key)[1] and r[3] <= m.control_clip(key)[3], key+': scrolled into view')
    yield from tap('LEFTMOUSE', center(r))


def edit(key, text):
    yield from control(key)
    yield from tap('A', ctrl=True)
    bpy.context.window_manager.clipboard = text
    yield from tap('V', ctrl=True)
    check(model().fields[key].text == text, key+': real clipboard/selection editing')


def snapshot():
    return {'provider': vars(ui._ai['state'].provider).copy(),
            'persisted': preferences.get('ai_config'),
            'geometry': [(str(o.Attributes.Id), hashlib.sha256(json.dumps(o.Geometry.Encode(), sort_keys=True).encode()).hexdigest())
                         for o in ui.app_state.model.file.Objects],
            'selection': list(ui.selection_names()), 'command': ui.app_state.active_command,
            'input': ui.app_state.input_buffer}


def capture(name):
    import numpy as np
    path = OUT/(name+'.png')
    check(bpy.ops.screen.screenshot(filepath=str(path)) == {'FINISHED'}, name+': screenshot')
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        image.colorspace_settings.name = 'Non-Color'
        w, h = image.size
        floats = np.empty(w*h*4, dtype=np.float32)
        image.pixels.foreach_get(floats)
        pixels = np.rint(floats.reshape(h, w, 4)*255).astype(np.uint8)
    finally:
        bpy.data.images.remove(image)
    RESULT['captures'][name] = {'size': [w, h], 'popup': list(popup._model.rect) if popup.is_open() else None,
        'gpu_batches': list(DRAW), 'text': list(TEXT), 'settings_button': settings_button()}
    DRAW.clear()
    TEXT.clear()
    return pixels


def outside_pixels(before, after, rect, label):
    import numpy as np
    check(before.shape == after.shape, label+': same screenshot dimensions')
    mask = np.ones(before.shape[:2], dtype=bool)
    a,b,c,d = map(int, rect)
    mask[max(0,b-1):d+1, max(0,a-1):c+1] = False
    # The bottom status strip contains a live clock; the existing button may hover.
    mask[:round(42*ui._ui_scale()), :] = False
    a,b,c,d = map(int, settings_button())
    mask[max(0,b-2):d+2, max(0,a-2):c+2] = False
    changed = (np.max(np.abs(before[:,:,:3].astype(int)-after[:,:,:3].astype(int)), axis=2) > 1) & mask
    ys, xs = np.where(changed)
    RESULT.setdefault('outside_pixels', {})[label] = {'checked': int(mask.sum()), 'changed': int(changed.sum()),
        'change_bounds': [int(xs.min()),int(ys.min()),int(xs.max()),int(ys.max())] if len(xs) else None}
    check(not changed.any(), label+': no backdrop or global shell pixel changes outside popup')


def anchor_check():
    m = model()
    button = settings_button()
    check(abs(m.rect[2]-(button[0]-round(8*m.scale))) <= 1,
          'popup beside existing right-dock button, not centered')
    check(m.rect[2]-m.rect[0] == round(520*m.scale), '520 logical pixel width')
    check(0 <= m.rect[0] < m.rect[2] <= window().width and
          0 <= m.rect[1] < m.rect[3] <= window().height, 'popup inside available window')


def paint_check(name):
    m = model()
    records = RESULT['captures'][name]['gpu_batches']
    check(bool(records), name+': observed forwarded real GPU batch submissions')
    check(all(r['window'] and r['region_none'] for r in records), name+': real Window POST_PIXEL context')
    check(all(r['scissor'][0] >= m.rect[0] and r['scissor'][1] >= m.rect[1]
              and r['scissor'][0]+r['scissor'][2] <= m.rect[2]
              and r['scissor'][1]+r['scissor'][3] <= m.rect[3] for r in records),
          name+': every actual popup submission clipped inside popup')


def visible_field(name, key):
    import numpy as np
    DRAW.clear()
    TEXT.clear()
    yield from settle()
    pixels = capture(name)
    m = model()
    r = m.controls[key]
    left = round(r[0]+11*m.scale)
    calls = [t for t in RESULT['captures'][name]['text'] if t['text'] == m.fields[key].text]
    color = np.array(list(bytes.fromhex(popup.PALETTES[design.THEME]['text'][1:])))
    interior = pixels[round(r[1]+5*m.scale):round(r[3]-5*m.scale), left:round(r[2]-11*m.scale), :3]
    ink = int(np.count_nonzero(np.max(np.abs(interior.astype(int)-color), axis=2) < 35))
    aligned = bool(calls) and all(t['x'] == left for t in calls)
    RESULT.setdefault('field_visibility', {})[name] = {'expected_left': left, 'draw_calls': calls,
        'ink_pixels': ink, 'aligned': aligned, 'field_text': m.fields[key].text,
        'horizontal_offset': m.field_offset(key)}
    return aligned and ink > 15


def discover(config, environ):
    mode, gate = TRANSPORT['mode'], TRANSPORT['gate']
    RESULT['worker_calls'].append({'thread': threading.current_thread().name, 'mode': mode,
                                  'config': vars(config), 'not_main_thread': threading.current_thread() != threading.main_thread()})
    if gate is not None:
        gate.wait(8)
    if mode == 'short_error':
        raise transport.TransportError(SHORT_ERROR)
    if mode == 'error':
        raise transport.TransportError('Offline fixture: endpoint discovery is unavailable. '*18)
    return [transport.ModelInfo('fixture-real-model-a'), transport.ModelInfo('fixture-real-model-b')]


def popup_stack():
    frame = sys._getframe(1)
    for _ in range(10):
        if frame is None:
            break
        if frame.f_globals.get('__name__') == popup.__name__:
            return True
        frame = frame.f_back
    return False


class ObservedBatch:
    def __init__(self, batch, kind, count):
        self.batch, self.kind, self.count = batch, kind, count

    def draw(self, shader):
        import gpu
        if len(DRAW) < 250:
            DRAW.append({'kind': self.kind, 'vertices': self.count,
                         'scissor': list(gpu.state.scissor_get()), 'window': bpy.context.window == window(),
                         'region_none': bpy.context.region is None})
        return self.batch.draw(shader)

    def __getattr__(self, name):
        return getattr(self.batch, name)


def observed_batch(shader, kind, content, **kwargs):
    batch = ORIGINAL_BATCH(shader, kind, content, **kwargs)
    return ObservedBatch(batch, kind, len(content.get('pos', ()))) if popup_stack() else batch


def observed_text(*args, **kwargs):
    if popup_stack() and len(TEXT) < 180:
        TEXT.append({'x': args[0], 'y': args[1], 'text': args[2]})
    return ORIGINAL_TEXT(*args, **kwargs)


def observed_apply(config):
    SAVES.append(vars(config).copy())
    return ORIGINAL_APPLY(config)


def observed_click(*args, **kwargs):
    result = ORIGINAL_CLICK(*args, **kwargs)
    if result == 'button:settings':
        BUTTON_CLICKS.append(args)
    return result


def themes():
    for theme in ('light', 'dark'):
        ui.set_shell_theme(theme)
        yield from settle()
        event('MOUSEMOVE', 'NOTHING', center(settings_button()))
        yield from settle()
        before = capture(theme+'_before')
        yield from open_button()
        anchor_check()
        after = capture(theme+'_popup')
        paint_check(theme+'_popup')
        outside_pixels(before, after, model().rect, theme)
        TRANSPORT['mode'] = 'short_error'
        m = popup._model
        yield from tap('LEFTMOUSE', center(m.controls['refresh']))
        deadline = time.monotonic()+4
        while m.busy and time.monotonic() < deadline:
            yield .05
        check(m.status_error and m.status == 'Model discovery failed: '+SHORT_ERROR
              and 0 < len([i for i in m.items if i.key == 'status']) <= 3,
              theme+': actual worker short error stays compact')
        DRAW.clear()
        TEXT.clear()
        yield from settle()
        error_pixels = capture(theme+'_short_error')
        outside_pixels(before, error_pixels, m.rect, theme+'_short_error')
        TRANSPORT['mode'] = 'models'
        rect = model().rect
        yield from control('close')
        yield from settle()
        dismissed = capture(theme+'_dismissed')
        outside_pixels(before, dismissed, rect, theme+'_dismissed')


def cancelled_edits():
    for dismissal in ('cancel', 'close', 'ESC', 'outside'):
        before = snapshot()
        count = len(SAVES)
        yield from open_button()
        yield from edit('model', 'discard-'+dismissal)
        if dismissal in ('cancel', 'close'):
            yield from control(dismissal)
        elif dismissal == 'ESC':
            yield from tap('ESC')
        else:
            # A real viewport press/release behind the popup must not pick/drag.
            pane = ui._pane_by_label('Top')
            r = ui._window_region(pane)
            yield from tap('LEFTMOUSE', (r.x+20, r.y+20))
        check(not popup.is_open() and popup._handler is None, dismissal+': overlay handler removed')
        check(snapshot() == before and len(SAVES) == count, dismissal+': no persistent/provider/geometry/input change')


def fields_and_save():
    yield from open_button()
    yield from control('provider')
    check(model().choices() == ai.PROVIDERS and len(model().choices()) == 3,
          'actual provider dropdown has exactly the backend three incl Anthropic')
    check('Ollama' not in str(model().choices()), 'no prototype Ollama provider')
    yield from settle()
    capture('provider_dropdown')
    index = [p for p,_ in ai.PROVIDERS].index('anthropic')
    rect = next(r for i,r in model().menu_rows if i == index)
    yield from tap('LEFTMOUSE', center(rect))
    check(model().provider_id == 'anthropic' and model().fields['credential_env'].text == 'ANTHROPIC_API_KEY',
          'provider selection applies actual Anthropic defaults')
    endpoint = 'https://example.invalid/' + 'long-endpoint/'*15
    yield from edit('endpoint', endpoint)
    yield from tap('HOME')
    check(model().fields['endpoint'].caret == 0, 'Home moves endpoint caret')
    yield from tap('END', shift=True)
    yield from tap('C', ctrl=True)
    check(bpy.context.window_manager.clipboard == endpoint, 'real Ctrl-C copies full endpoint selection')
    yield from tap('TAB')
    yield from tap('TAB', shift=True)
    check(model().focus == 'endpoint', 'Tab/Shift-Tab returns to endpoint')
    yield from edit('model', 'modèle-猫-🙂-'+'long-id-'*12)
    event('A', 'PRESS', unicode='é')
    yield .06
    event('A', 'RELEASE')
    yield .1
    check(model().fields['model'].text.endswith('é'), 'actual Unicode key event inserts text')
    yield from edit('credential_env', 'NARWHAL_UI_TEST_'+'KEY_'*30)
    yield from edit('credential_env', 'bad credential name')
    count = len(SAVES)
    yield from control('save')
    check(popup.is_open() and model().error_field == 'credential_env' and len(SAVES) == count,
          'invalid credential name stays open without persistence')
    yield from edit('credential_env', 'NARWHAL_UI_TEST_KEY')
    yield from tap('TAB')
    check(model().focus == 'insecure' and not model().allow_insecure_remote, 'security opt-in defaults unchecked')
    check(not any(i.key == 'warning' for i in model().items), 'no warning while unchecked')
    yield from tap('SPACE')
    check(model().allow_insecure_remote and any(i.key == 'warning' for i in model().items), 'warning appears only after opt-in')
    yield from settle()
    capture('security_warning')
    yield from tap('SPACE')
    before, count = snapshot(), len(SAVES)
    yield from edit('endpoint', 'ftp://invalid.example')
    yield from control('save')
    check(popup.is_open() and model().error_field == 'endpoint', 'invalid Save stays open and marks endpoint')
    check(snapshot() == before and len(SAVES) == count, 'invalid Save does not persist or mutate live state')
    yield from settle()
    capture('invalid_endpoint')
    yield from edit('endpoint', endpoint)
    TRANSPORT['mode'] = 'error'
    yield from control('refresh')
    deadline = time.monotonic()+4
    while model().busy and time.monotonic() < deadline:
        yield .05
    check(model().status_error and len(model().status) > 400, 'real worker failure delivered long status')
    yield from settle()
    capture('discovery_error_capped_stress')
    status_lines = [i for i in model().items if i.key == 'status']
    check(len(status_lines) == 3 and status_lines[-1].text.endswith('…'),
          'stress status uses three visible lines with ellipsis; full status remains in model')
    yield from edit('model', 'manual-modèle-猫-🙂')
    visible = yield from visible_field('manual_after_error', 'model')
    check(visible, 'short replacement model actually draws visible text at field origin')
    count = len(SAVES)
    yield from control('save')
    check(not popup.is_open() and len(SAVES) == count+1, 'valid Save persists exactly once')
    saved = preferences.get('ai_config')
    check(set(saved) == {'provider_id','model','endpoint','credential_env','allow_insecure_remote'}, 'only allowed public config fields persisted')
    check(saved['model'] == 'manual-modèle-猫-🙂' and saved['provider_id'] == 'anthropic', 'saved values match edited draft')
    check(SECRET not in Path(os.environ['NARWHAL3D_SETTINGS_FILE']).read_text(), 'no raw credential in settings file')
    live = {k: getattr(ui._ai['state'].provider, k) for k in saved}
    RESULT['saved_vs_live'] = {'saved': saved, 'live': live}
    # Retain the persisted/live identity regression after Main's normalization fix.
    check(live == saved, 'live provider matches persisted config', fatal=False)
    (OUT/'saved_config.json').write_text(json.dumps(saved, indent=2, ensure_ascii=False))
    TRANSPORT['mode'] = 'models'


def stale_generation():
    gate = threading.Event()
    TRANSPORT['gate'] = gate
    yield from open_button(wait=False)
    first = ui._ai['settings_generation']
    yield from tap('ESC')
    yield from open_button(wait=False)
    check(ui._ai['settings_generation'] > first, 'reopen increments settings generation')
    replacement = model()
    status = replacement.status
    gate.set()
    deadline = time.monotonic()+4
    while ui._ai.get('model_output') is not None and time.monotonic() < deadline:
        yield .05
    check(not replacement.models and replacement.status == status, 'late old-generation result ignored after same-config reopen')
    TRANSPORT['gate'] = None
    yield from control('refresh')
    deadline = time.monotonic()+4
    while model().busy and time.monotonic() < deadline:
        yield .05
    check(model().models == ('fixture-real-model-a','fixture-real-model-b'), 'new generation refresh gets worker models')
    yield from control('available')
    yield from tap('DOWN_ARROW')
    yield from tap('RET')
    check(model().fields['model'].text == 'fixture-real-model-b', 'actual available-model dropdown edits manual model field')
    yield from control('cancel')


def negative_controls():
    # Each mutation is process-local, exercises the real button/router, is detected,
    # then restored and checked green. No source file/worktree mutation.
    yield from open_button()
    original = ui.NARWHAL_OT_command_input.modal
    source = textwrap.dedent(inspect.getsource(original))
    anchor = 'if ai_settings_popup.handle_event(event, context):'
    check(source.count(anchor) == 1, 'router mutation anchor measured')
    namespace = {}
    exec(compile(source.replace(anchor, 'if False:', 1), '<AI-popup-router-mutation>', 'exec'), ui.__dict__, namespace)
    with patch.object(ui.NARWHAL_OT_command_input, 'modal', namespace['modal']):
        yield from tap('LEFTMOUSE', center(model().controls['endpoint']))
        red = model().focus != 'endpoint'
    check(red, 'RED expected: omitted Main popup router rejected by actual field click')
    RESULT['mutations'].append('router: RED')
    yield from control('endpoint')
    check(model().focus == 'endpoint', 'restored router GREEN')
    yield from control('cancel')

    before = snapshot()
    original = popup.PopupModel.activate
    def cancel_writes(self, key):
        return self.save() if key == 'cancel' else original(self, key)
    yield from open_button()
    yield from edit('model', 'mutation-must-not-save')
    with patch.object(popup.PopupModel, 'activate', cancel_writes):
        yield from control('cancel')
    red = snapshot()['persisted'] != before['persisted']
    check(red, 'RED expected: Cancel writes caught by persisted-state oracle')
    RESULT['mutations'].append('cancel: RED')
    preferences.update({'ai_config': before['persisted']})
    ui._ai['state'].provider = ai.ProviderConfig(**before['provider'])
    yield from open_button()
    yield from edit('model', 'restored-cancel')
    yield from control('cancel')
    check(snapshot()['persisted'] == before['persisted'], 'restored Cancel GREEN')

    yield from open_button()
    yield from edit('endpoint', 'ftp://invalid.example')
    with patch.object(popup.PopupModel, 'validated_config', lambda self: self.original):
        yield from control('save')
    check(not popup.is_open(), 'RED expected: bypassed invalid-field validation closes popup')
    RESULT['mutations'].append('invalid-save: RED')
    yield from open_button()
    yield from edit('endpoint', 'ftp://invalid.example')
    yield from control('save')
    check(popup.is_open() and model().error_field == 'endpoint', 'restored invalid Save GREEN')
    yield from control('cancel')

    with patch.object(ui, '_ai_settings_anchor', lambda context: (20, context.window.height-20)):
        yield from open_button()
    check(abs(model().rect[2]-(settings_button()[0]-round(8*model().scale))) > 20,
          'RED expected: wrong sidebar anchor rejected by measured button position')
    RESULT['mutations'].append('anchor: RED')
    yield from settle()
    capture('mutation_wrong_anchor')
    yield from control('cancel')
    yield from open_button()
    anchor_check()
    check(True, 'restored sidebar anchor GREEN')
    yield from control('cancel')

    yield from open_button()
    yield from edit('model', 'W'*180)
    source = textwrap.dedent(inspect.getsource(popup.PopupModel.field_offset))
    anchor = 'min(self._offsets[key], caret, maximum)'
    check(source.count(anchor) == 1, 'horizontal-scroll mutation anchor measured')
    namespace = {}
    exec(compile(source.replace(anchor, 'min(self._offsets[key], caret)', 1),
                 '<AI-popup-scroll-mutation>', 'exec'), popup.__dict__, namespace)
    with patch.object(popup.PopupModel, 'field_offset', namespace['field_offset']):
        yield from edit('model', 'visible-model')
        visible = yield from visible_field('mutation_hidden_short_text', 'model')
        check(not visible, 'RED expected: stale horizontal offset hides real painted text')
    RESULT['mutations'].append('short-text-clipping: RED')
    visible = yield from visible_field('restored_visible_short_text', 'model')
    check(visible, 'restored short text GPU rendering GREEN')
    yield from control('cancel')


def run():
    deadline = time.monotonic()+45
    while not (os.environ.get('NARWHAL3D_UI_READY') == '1' and ui._preferences_ready and
               not ui._viewports_in_progress and ui.is_input_active() and len(ui._right_tab_boxes) == 5):
        if time.monotonic() > deadline:
            raise RuntimeError('Bootstrap not ready within 45s')
        yield .1
    check(not list(ui.app_state.model.file.Objects), 'owned fresh document before fixture')
    # Existing exact Line command supplies nonempty geometry for unchanged-state checks.
    for line in ('Line', '0,0,0', '10,0,0'):
        ui.app_state.feed_line(line)
    check(len(list(ui.app_state.model.file.Objects)) == 1, 'one real document line guards geometry invariance')
    if ui.app_state.active_command is not None:
        ui.app_state.cancel()
    ui.apply_selection([])
    yield from settle()
    RESULT['window'] = [window().width, window().height]
    RESULT['scale'] = ui._ui_scale()
    yield from tap('LEFTMOUSE', center(ui._right_tab_boxes[4]))
    check(ui.ai_panel_open(), 'actual existing AI tab click selects assistant')
    yield from settle()
    if MODE == 'paint-regression':
        yield from open_button()
        yield from edit('model', 'W'*180)
        yield from edit('model', 'visible-model')
        visible = yield from visible_field('short_replacement', 'model')
        check(visible, 'short replacement model actually draws visible text at field origin')
        yield from control('cancel')
    elif MODE == 'mutations':
        yield from negative_controls()
    else:
        yield from themes()
        if MODE == 'baseline':
            yield from cancelled_edits()
            yield from fields_and_save()
            yield from stale_generation()
        else:
            yield from open_button()
            yield from edit('credential_env', 'NARROW_UI_KEY')
            check(model().scroll > 0, 'narrow body scrolls to focused credential field')
            yield from tap('TAB', shift=True)
            check(model().focus == 'model', 'narrow Shift-Tab auto-reveals Model ID')
            yield from settle()
            capture('narrow_scrolled')
            yield from tap('ESC')
    check(all(c['not_main_thread'] for c in RESULT['worker_calls']), 'mock discovery ran only in Main callback workers')
    check(not popup.is_open() and popup._handler is None, 'no overlay handler after final dismissal')
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
    time.sleep(170)
    finish('170s fixture watchdog')


try:
    settings = Path(os.environ['NARWHAL3D_SETTINGS_FILE']).resolve()
    check(settings.is_relative_to(SCRATCH) and not settings.exists(), 'fresh owned temporary preferences')
    check(Path(os.environ['HOME']).is_relative_to(SCRATCH), 'isolated HOME')
    os.environ['NARWHAL_UI_TEST_KEY'] = SECRET
    import bpy
    import gpu_extras.batch as batches
    sys.path.insert(0, str(ROOT))
    from narwhal3d import ui, design, preferences, ai_assistant as ai, ai_transport as transport
    from narwhal3d import ai_settings_popup as popup
    check(not bpy.app.background and not ui._preferences_ready, 'real GUI fixture installed before bootstrap')
    check(hasattr(bpy.types.Window, 'draw_handler_add'), 'native Window overlay available')
    ORIGINAL_BATCH, ORIGINAL_TEXT = batches.batch_for_shader, ui._draw_text
    ORIGINAL_APPLY, ORIGINAL_CLICK = ui._apply_ai_settings, ui.ai_panel_click
    batches.batch_for_shader, ui._draw_text = observed_batch, observed_text
    ui._apply_ai_settings, ui.ai_panel_click = observed_apply, observed_click
    transport.discover_models = discover
    STEPS = run()
    threading.Thread(target=watchdog, daemon=True).start()
    bpy.app.timers.register(tick, first_interval=.1)
except BaseException:
    finish(traceback.format_exc())
