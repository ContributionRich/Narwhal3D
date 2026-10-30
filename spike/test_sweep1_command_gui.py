#!/usr/bin/env python3
"""Visible typed whole-curve Sweep1 acceptance, using actual bootstrap/window events.

python3 -B spike/test_sweep1_command_gui.py --launch [--mutations]
Only fixture/selection/camera setup uses APIs. All command text, confirmation,
Escape, Undo and Redo are real Window.event_simulate events. No product writes.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ('launch/bootstrap.py', 'narwhal3d/ui.py', 'narwhal3d/state.py',
           'narwhal3d/commands.py', 'narwhal3d/solid_commands.py', 'narwhal3d/bridge.py',
           'narwhal3d/kernel/__init__.py', 'narwhal3d/surfacing_backend.py',
           'spike/test_surfacing_preparation.py', 'spike/test_sweep1_command_integration.py',
           'spike/test_sweep1_command_gui.py',
           'native/geometry_core/target/release/libnarwhal_geometry_core.so')
EXPECTED_RED = 'typed Sweep1 publishes exactly one result'


def launch():
    import fcntl
    import subprocess

    out = Path(tempfile.mkdtemp(prefix='narwhal-sweep1-gui-'))
    env = {k: v for k, v in os.environ.items() if k in (
        'PATH', 'DISPLAY', 'XAUTHORITY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS', 'LANG',
        'NARWHAL3D_BREP_WRITER')}
    # Source-only exact-document test dependency; never stage it into a release.
    if env.get('NARWHAL3D_BREP_WRITER'):
        writer = Path(env['NARWHAL3D_BREP_WRITER']).resolve(strict=True)
        (out/'exact-writer-input.json').write_text(json.dumps({
            'path': str(writer), 'sha256': hashlib.sha256(writer.read_bytes()).hexdigest()}))
    paths = subprocess.check_output(['bash', str(ROOT/'narwhal3d.sh')], cwd=ROOT,
        env=dict(os.environ, NARWHAL3D_LAUNCHER_TEST='native-paths'), text=True, timeout=10)
    for row in paths.splitlines():
        key, value = row.split('=', 1)
        if value:
            env[key] = value
    locks = []
    print('SWEEP1_GUI_RECEIPT', out, flush=True)
    try:
        deadline = time.monotonic()+90
        while True:
            try:
                for name in ('/tmp/narwhal3d-heavy-build.lock', '/tmp/narwhal3d_gui.lock'):
                    lock = open(name, 'a'); locks.append(lock)
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                for lock in locks: lock.close()
                locks.clear()
                if time.monotonic() >= deadline:
                    raise TimeoutError('heavy/GUI lock busy; no window launched')
                time.sleep(.25)
        receipts = []
        modes = ('baseline', 'disabled-multisection', 'restored') if '--mutations' in sys.argv else ('baseline',)
        for mode in modes:
            case = out/mode; case.mkdir()
            child_env = dict(env)
            for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'TMPDIR'):
                folder = case/key.lower(); folder.mkdir(); child_env[key] = str(folder)
            child_env.update(SWEEP1_GUI_OUT=str(case), SWEEP1_GUI_MODE=mode,
                NARWHAL3D_SETTINGS_FILE=str(case/'preferences.json'), PYTHONDONTWRITEBYTECODE='1',
                PYTHONPYCACHEPREFIX=str(case/'bytecode'), PYTHONPATH=str(ROOT/'vendor/occt'),
                LD_LIBRARY_PATH=str(ROOT/'tools/blender-patched/5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs'),
                NARWHAL3D_NO_DIALOGS='1', NARWHAL3D_DEFER_FIRST_PRESENT='1',
                NARWHAL3D_STATUSBAR_HEIGHT='37', GHOST_BACKEND='X11',
                OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
            available = int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines()
                                 if l.startswith('MemAvailable:')))
            assert available >= 6*1024*1024, 'Need 4 GiB desktop reserve plus 2 GiB test cap'
            cmd = ['systemd-run', '--user', '--scope', '--quiet', '-p', 'MemoryMax=2048M',
                '-p', 'MemorySwapMax=0', '-p', 'CPUQuota=100%', 'timeout', '--signal=TERM',
                '--kill-after=5s', '180s', 'bwrap', '--ro-bind', '/', '/', '--dev-bind', '/dev', '/dev',
                '--proc', '/proc', '--bind', str(case), str(case), '--unshare-net', '--die-with-parent',
                str(ROOT/'tools/blender-patched/blender'), '--factory-startup', '-t', '1',
                '-p', '40', '40', '1440', '1000', '--enable-event-simulate', '--python-exit-code', '1',
                '--python', str(Path(__file__).resolve()), '--python', str(ROOT/'launch/bootstrap.py')]
            before = {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in SOURCES}
            (case/'launch.json').write_text(json.dumps(dict(command=cmd, native_paths=paths,
                mem_available_kib=available, source_sha256=before), indent=2))
            with (case/'run.log').open('w') as log:
                run = subprocess.run(cmd, cwd=ROOT, env=child_env, stdout=log,
                                     stderr=subprocess.STDOUT, timeout=190)
            text = (case/'run.log').read_text()
            print(text[-5000:], flush=True)
            after = {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in SOURCES}
            (case/'source_check.json').write_text(json.dumps(dict(before=before, after=after,
                                                                unchanged=before == after), indent=2))
            report = json.loads((case/'result.json').read_text()) if (case/'result.json').exists() else {}
            bad_log = any(s in text for s in ('Error in draw callback', 'Traceback (most recent call last)'))
            expected = (run.returncode == 1 and report.get('error') == EXPECTED_RED
                        if mode == 'disabled-multisection' else
                        run.returncode == 0 and report.get('status') == 'passed')
            receipts.append(dict(mode=mode, exit=run.returncode, status=report.get('status'),
                expected_result=expected, source_unchanged=before == after, bad_log=bad_log,
                checks=len(report.get('checks', [])), path=str(case)))
            (out/'receipt.json').write_text(json.dumps(receipts, indent=2))
            if not expected or before != after or bad_log:
                return 1
        print('SWEEP1_GUI_COMPLETE', 'GREEN -> RED -> GREEN' if len(modes) == 3 else 'GREEN', out, flush=True)
        return 0
    finally:
        for lock in locks: lock.close()


if '--launch' in sys.argv:
    raise SystemExit(launch())

import threading
import traceback
from collections import deque
import bpy

OUT = Path(os.environ['SWEEP1_GUI_OUT'])
MODE = os.environ['SWEEP1_GUI_MODE']
START = time.monotonic()
RESULT = dict(status='running', mode=MODE, checks=[], events=[], states=[], captures=[], owned_pid=os.getpid())
FINISH_LOCK = threading.Lock()
POSITION = (0, 0)
PAINT = deque(maxlen=8)


def check(ok, label):
    RESULT['checks'].append(dict(ok=bool(ok), label=label))
    print('SWEEP1_GUI', 'PASS' if ok else 'FAIL', label, flush=True)
    if not ok:
        raise AssertionError(label)


def finish(error=None):
    if not FINISH_LOCK.acquire(False):
        return
    RESULT.update(status='failed' if error else 'passed', error=error,
                  elapsed_s=time.monotonic()-START)
    try:
        cgroup = Path('/sys/fs/cgroup'+Path('/proc/self/cgroup').read_text().strip().split(':', 2)[2])
        RESULT['cgroup'] = {name: (cgroup/name).read_text().strip()
                            for name in ('memory.max', 'memory.swap.max', 'memory.peak')}
    except OSError:
        pass
    (OUT/'result.json').write_text(json.dumps(RESULT, indent=2))
    print('SWEEP1_GUI_RESULT', RESULT['status'], error or '', flush=True)
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(int(bool(error)))


def window():
    return bpy.context.window_manager.windows[0]


def event(key, value, text=''):
    RESULT['events'].append(dict(type=key, value=value, unicode=text, position=POSITION))
    window().event_simulate(type=key, value=value, x=POSITION[0], y=POSITION[1],
                           **({'unicode': text} if text else {}))


def tap(key, point=None):
    global POSITION
    if point is not None:
        POSITION = tuple(round(v) for v in point)
        event('MOUSEMOVE', 'NOTHING'); yield .12
    event(key, 'PRESS'); yield .06
    event(key, 'RELEASE'); yield .25


def wait(predicate, label, seconds=6):
    deadline = time.monotonic()+seconds
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError('timed out: '+label)
        yield .06
    check(True, label)


def line(text):
    check(not st.input_buffer, 'empty keyboard buffer before '+text)
    digits = ('ZERO', 'ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX', 'SEVEN', 'EIGHT', 'NINE')
    for char in text:
        key = digits[int(char)] if char.isdigit() else char.upper()
        event(key, 'PRESS', char); yield .04
        event(key, 'RELEASE'); yield .04
    check(st.input_buffer == text, 'window keyboard populated '+text)
    yield from tap('RET')


def observe_text(x, y, text, *args, **kwargs):
    result = ORIGINAL_DRAW(x, y, text, *args, **kwargs)
    frame = sys._getframe(1)
    if frame.f_code is ui.draw_command_bar.__code__ and y == frame.f_locals.get('prompt_y'):
        if x == frame.f_locals.get('pad_x'):
            PAINT.append([])
        if PAINT:
            PAINT[-1].append(str(text))
    return result


def painted(label, expected=None, shot=False):
    PAINT.clear()
    ui.redraw_cmdbar(); ui._tag_redraw_viewports()
    yield .4
    text = ''.join(PAINT[-1]) if PAINT else ''
    check(bool(text) and (expected is None or expected in text), 'visible command bar: '+label)
    RESULT['states'].append(dict(label=label, painted_prompt=text, input=st.input_buffer,
        command=getattr(st.active_command, 'name', None), undo=st.ctx.undo.undo_names(),
        redo=st.ctx.undo.redo_names(), object_count=st.model.object_count(), history=st.history[-6:]))
    if shot:
        path = OUT/(label+'.png')
        check(bpy.ops.screen.screenshot(filepath=str(path)) == {'FINISHED'}, label+' screenshot')
        RESULT['captures'].append(str(path))


def unchanged():
    return all(st.model.file.Objects.FindId(oid).Geometry.Encode() == encoded
               for oid, encoded in SOURCE_CONTENT.items())


def run():
    global st, SOURCE_CONTENT
    yield from wait(lambda: os.environ.get('NARWHAL3D_UI_READY') == '1'
                    and ui._preferences_ready and not ui._viewports_in_progress
                    and ui._viewport_count() == 4 and ui.is_input_active(), 'visible bootstrap ready', 50)
    st = ui.app_state
    check(not bpy.app.background and window().width > 800 and window().height > 500,
          'actual visible window, not background Blender')
    check(st.model.object_count() == 0 and st.active_command is None, 'empty disposable startup')
    # Load validated synthetic source construction only after the real package/bootstrap.
    sys.path.insert(0, str(ROOT/'spike'))
    from test_surfacing_preparation import sources
    from test_sweep1_command_integration import verify_document
    from narwhal3d import solid_commands, kernel, native_geometry as ng
    from mathutils import Vector
    case = sources()
    st.set_model(case[0]); st.ctx.rebuild_display(); st.ctx.undo.clear(); st.mark_saved()
    SOURCE_CONTENT = {oid: st.model.file.Objects.FindId(oid).Geometry.Encode() for oid in case[1]}
    RESULT['sources'] = dict(ids=list(case[1]), sha256=[hashlib.sha256(
        json.dumps(SOURCE_CONTENT[oid], sort_keys=True, separators=(',', ':')).encode()).hexdigest() for oid in case[1]])
    check(ng.prepare_freeform_available() and ng.sweep1_freeform_available(), 'real native preparation/build capabilities')
    for label in ('Top', 'Perspective', 'Front', 'Right'):
        area = ui._pane_by_label(label)
        area.spaces.active.region_3d.view_location = Vector((6, 6, .6))
        area.spaces.active.region_3d.view_distance = 28
        area.spaces.active.region_3d.update()
    ui.set_active_pane(ui._pane_by_label('Perspective'))
    ui.set_gumball(False)
    region = ui._window_region(ui._cmdbar_area())
    yield from tap('LEFTMOUSE', (region.x+region.width*.7, region.y+15))
    check(ui._console_focus, 'real click focuses command bar')

    # Selection is a setup fixture, not claimed as simulated chronological mouse picking.
    # Command/name entries, empty confirmation and cancellation remain window events.
    yield from line('Sweep1')
    yield from painted('rail_prompt', 'Select rail')
    yield from line('source0')
    for name in ('source3', 'source1', 'source2'):
        st.ctx.object_by_name(name).select_set(True)
    before_registry = kernel.master_registry_snapshot()
    yield from tap('RET')
    check(st.active_command is not None and 'order is ambiguous' in '\n'.join(st.history[-3:]),
          'unordered multi-selection explicitly refused by real Enter')
    check(st.model.object_count() == 4 and not st.ctx.undo.undo_names() and unchanged()
          and kernel.master_registry_snapshot() == before_registry, 'unordered refusal has no publication/Undo/source effects')
    yield from painted('unordered_refusal', 'cross-section', shot=True)
    yield from tap('ESC')
    check(st.active_command is None and not st.input_buffer, 'real Escape clears refused command')
    st.ctx.select_by_names([])

    if MODE == 'disabled-multisection':
        # In-memory mutation only. Registry deepcopy and real input handling stay intact.
        solid_commands.Sweep1Cmd.execute = solid_commands._SweepCmd.execute
        RESULT['mutation'] = 'disable only multi-section Sweep1 dispatch'
    yield from line('Sweep1')
    check(isinstance(st.active_command, solid_commands.Sweep1Cmd), 'typed Sweep1 starts real command')
    yield from line('source0')
    yield from painted('section_prompt', 'cross-section')
    cmd = st.active_command
    for name in ('source1', 'source2', 'source3'):
        yield from line(name)
        check(st.active_command is cmd and cmd._pending_multi[-1] == name,
              'typed section accepted in order: '+name)
    check(st.model.object_count() == 4 and not st.ctx.undo.undo_names() and unchanged(),
          'three accepted sections do not publish before final Enter')
    yield from painted('before_commit', 'cross-section', shot=True)
    yield from tap('RET')
    yield from wait(lambda: st.active_command is None, 'real command clears after final Enter')
    check(st.model.object_count() == 5, EXPECTED_RED)
    check(st.ctx.undo.undo_names() == ['Sweep1'] and not st.ctx.undo.recording,
          'exactly one completed Sweep1 Undo record')
    check(unchanged() and not st.input_buffer, 'four source curves unchanged and input cleared')
    geom = verify_document(st.model)
    check(bpy.data.objects.get('Sweep1') is not None and geom.IsValid and not geom.IsSolid
          and len(geom.Faces) == 1, 'exact open result exists in document and real display')
    check(tuple(kernel.recipe_of(geom)['source_provenance']['ids']) == case[1], 'result provenance retains ordered source UUIDs')
    yield from painted('result', shot=True)

    yield from line('Undo')
    check(st.model.object_count() == 4 and st.active_command is None and unchanged()
          and bpy.data.objects.get('Sweep1') is None and st.ctx.undo.redo_names() == ['Sweep1'],
          'keyboard Undo removes exactly the result')
    yield from painted('undo', shot=True)
    yield from line('Redo')
    check(st.model.object_count() == 5 and st.active_command is None and unchanged()
          and st.ctx.undo.undo_names() == ['Sweep1'], 'keyboard Redo restores exactly the result')
    verify_document(st.model)
    yield from painted('restored_result', shot=True)


def tick():
    try:
        with bpy.context.temp_override(window=window()):
            return next(STEPS)
    except StopIteration:
        finish()
    except BaseException as exc:
        RESULT['traceback'] = traceback.format_exc()
        finish(str(exc))


try:
    check(Path(os.environ['HOME']).is_relative_to(OUT)
          and not Path(os.environ['NARWHAL3D_SETTINGS_FILE']).exists(), 'fresh disposable HOME/preferences')
    check('narwhal3d' not in sys.modules and not os.environ.get('NARWHAL3D_UI_READY'), 'fixture precedes actual bootstrap')
    sys.path.insert(0, str(ROOT))
    from narwhal3d import ui
    ORIGINAL_DRAW = ui._draw_text
    ui._draw_text = observe_text  # Passive observer; always forwards real drawing.
    STEPS = run()
    threading.Thread(target=lambda: (time.sleep(170), finish('170s GUI watchdog')), daemon=True).start()
    bpy.app.timers.register(tick, first_interval=.1)
except BaseException as exc:
    RESULT['traceback'] = traceback.format_exc()
    finish(str(exc))
