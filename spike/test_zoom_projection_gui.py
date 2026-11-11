"""PR153: Zoom 1to1 qualified against actual Blender viewport matrices.

  python3 -B spike/test_zoom_projection_gui.py --launch
  python3 -B spike/test_zoom_projection_gui.py --launch --mutation lens_ignored

The mutation must exit RED. Two disposable windows (landscape/portrait), real
AppState command dispatch, lenses 35/50 and mm/inch. Expected 96 reference pixels
per model inch is ASSUMED, not a physical monitor calibration. No analytical
project_ortho_point helper participates in the measurement.

Python is necessary Blender/UI/test glue. Fresh HOME/settings per child, shared
/var/tmp/narwhal3d_gui.lock, 3G memory bound, 150s total child-run deadline, no
shared artifacts, no builds, no other-process termination or production layout edits.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'spike'))


def launch():
    import argparse
    import fcntl
    import subprocess
    import memory_bound
    import runtime_paths

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--launch', action='store_true')
    parser.add_argument('--mutation', choices=('lens_ignored',), default='')
    parser.add_argument('--aspect', choices=('both', 'landscape', 'portrait'), default='both')
    args = parser.parse_args()
    out = Path(tempfile.mkdtemp(prefix='zoom-projection-gui-'))
    print('ZOOM_PROJECTION_OUTPUT', out, flush=True)
    env = os.environ.copy()
    for key in ('NARWHAL3D_UI_READY', 'NARWHAL3D_THEME', 'NARWHAL3D_WELCOME_REQUEST',
                'NARWHAL3D_PRESENT_WATCHDOG'):
        env.pop(key, None)
    paths = subprocess.check_output(
        ['bash', str(ROOT / 'narwhal3d.sh')], cwd=ROOT,
        env=dict(env, NARWHAL3D_LAUNCHER_TEST='native-paths'), text=True, timeout=10)
    for row in paths.splitlines():
        key, value = row.split('=', 1)
        if value:
            env[key] = value
    env.update(PYTHONDONTWRITEBYTECODE='1', NARWHAL3D_NO_DIALOGS='1',
               NARWHAL3D_DEFER_FIRST_PRESENT='1', NARWHAL3D_STATUSBAR_HEIGHT='37',
               PYTHONPATH=str(ROOT / 'vendor/occt'), ZOOM_PROJECTION_MUTATION=args.mutation)
    failed = False
    bounded_prefix = memory_bound.prefix('3G')
    with open('/var/tmp/narwhal3d_gui.lock', 'a') as lock:
        wait_until = time.monotonic() + 60
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > wait_until:
                    raise TimeoutError('GUI lock busy for 60s; no window launched')
                time.sleep(.25)
        deadline = time.monotonic() + 150
        for aspect, width, height in (('landscape', 1500, 850), ('portrait', 900, 1000)):
            if args.aspect not in ('both', aspect):
                continue
            child = out / aspect
            child.mkdir()
            child_env = dict(env, ZOOM_PROJECTION_OUTPUT=str(child), ZOOM_PROJECTION_ASPECT=aspect)
            for key, subdir in (('HOME', 'home'), ('XDG_CONFIG_HOME', 'config'),
                                ('XDG_CACHE_HOME', 'cache'), ('XDG_DATA_HOME', 'data'),
                                ('XDG_STATE_HOME', 'state')):
                folder = child / subdir
                folder.mkdir()
                child_env[key] = str(folder)
            child_env['NARWHAL3D_SETTINGS_FILE'] = str(child / 'preferences.json')
            remaining = int(deadline - time.monotonic())
            if remaining < 5:
                raise TimeoutError('150s disposable GUI deadline exhausted')
            cmd = [*bounded_prefix, 'timeout', '--signal=TERM', '--kill-after=3s', f'{remaining}s',
                   str(runtime_paths.blender(ROOT)), '--factory-startup',
                   '--window-geometry', '0', '0', str(width), str(height),
                   '--python-exit-code', '1', '--python', str(Path(__file__).resolve()),
                   '--python', str(ROOT / 'launch/bootstrap.py')]
            with (child / 'run.log').open('w') as log:
                result = subprocess.run(cmd, cwd=ROOT, env=child_env, stdout=log,
                                        stderr=subprocess.STDOUT, timeout=remaining + 5)
            text = (child / 'run.log').read_text()
            print(text, flush=True)
            report = child / 'result.json'
            if not report.exists():
                raise RuntimeError(f'{aspect}: no result, child exit {result.returncode}; see {child}')
            data = json.loads(report.read_text())
            failed |= bool(result.returncode or data['status'] != 'passed')
            print('ZOOM_PROJECTION_COMPLETED', aspect, data['status'], child, flush=True)
    return int(failed)


if '--launch' in sys.argv:
    raise SystemExit(launch())

import math
import threading
import traceback

START = time.monotonic()
OUT = Path(os.environ['ZOOM_PROJECTION_OUTPUT'])
RESULT = {'status': 'running', 'checks': [], 'cases': [], 'pid': os.getpid(),
          'aspect': os.environ['ZOOM_PROJECTION_ASPECT'],
          'mutation': os.environ.get('ZOOM_PROJECTION_MUTATION', ''),
          'measurement': 'actual RegionView3D.perspective_matrix -> screen pixels',
          'density': 'ASSUMED 96 DPI reference pixels; not monitor-calibrated'}
FINISH_LOCK = threading.Lock()


def check(ok, label, fatal=True):
    RESULT['checks'].append({'ok': bool(ok), 'label': label})
    print('ZOOM_PROJECTION', 'PASS' if ok else 'FAIL', label, flush=True)
    if not ok and fatal:
        raise AssertionError(label)


def finish(error=None):
    if not FINISH_LOCK.acquire(False):
        return
    code = int(bool(error) or any(not row['ok'] for row in RESULT['checks']))
    RESULT.update(status='failed' if code else 'passed', error=error,
                  elapsed_s=time.monotonic() - START)
    (OUT / 'result.json').write_text(json.dumps(RESULT, indent=2))
    print('ZOOM_PROJECTION_RESULT', RESULT['status'], 'checks', len(RESULT['checks']), flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)  # Only this disposable Blender; timer exceptions alone don't fail it.


def snapshot(st):
    encoded = st.model.file.Encode()
    document = hashlib.sha256(json.dumps(encoded, sort_keys=True).encode()).hexdigest()
    undo = st.ctx.undo
    return {'document': document, 'undo': list(undo.undo_names()), 'redo': list(undo.redo_names()),
            'generation': undo.generation, 'state_id': undo.state_id,
            'current_serial': undo.current_serial, 'next_serial': undo.next_serial,
            'recording': undo.recording, 'captured': undo.captured}


def projected(region, rv, point):
    # Read the real Blender matrix. No production zoom/pixsize/projection helper.
    clip = rv.perspective_matrix @ Vector((*point, 1.0))
    check(all(math.isfinite(v) for v in clip) and abs(clip.w) > 1e-12, 'finite clip coordinates')
    return ((clip.x / clip.w + 1) * region.width / 2,
            (clip.y / clip.w + 1) * region.height / 2)


def run():
    deadline = time.monotonic() + 45
    while not (os.environ.get('NARWHAL3D_UI_READY') == '1' and not ui._viewports_in_progress
               and ui._viewport_count() == 4 and ui.is_input_active()):
        if time.monotonic() > deadline:
            raise RuntimeError('Bootstrap not ready in 45s')
        yield .15
    st = ui.app_state
    check(not list(st.model.file.Objects), 'fresh empty document before fixtures')
    # Real command-created geometry and a surviving Redo branch make a spurious
    # Undo push/clear detectable. Fixture setup is outside every measured baseline.
    for y in (0, 5):
        for line in ('Line', f'0,{y},0', f'10,{y},0', ''):
            st.feed_line(line)
    st.feed_line('Undo')
    check(st.active_command is None and bool(list(st.model.file.Objects)), 'real line fixture remains')
    check(st.ctx.undo.can_undo and st.ctx.undo.can_redo, 'nonempty real Undo and Redo branches seeded')
    area = ui._pane_by_label('Top')
    check(area is not None, 'real Top viewport available')
    ui.set_active_pane(area)
    space, region = area.spaces.active, ui._window_region(area)
    check(region is not None and min(region.width, region.height) > 100, 'usable actual viewport dimensions')
    check((region.width > region.height) == (RESULT['aspect'] == 'landscape'),
          f"actual {RESULT['aspect']} region {region.width}x{region.height}")
    rv = space.region_3d
    rv.view_perspective = 'ORTHO'
    rv.view_rotation = Quaternion((1, 0, 0, 0))
    rv.view_location = Vector((0, 0, 0))
    space.clip_start, space.clip_end = .01, 1000000
    for unit, span in (('Millimeters', 25.4), ('Inches', 1.0)):
        st.model.set_unit_system(unit, scale=False)
        for lens in (35., 50.):
            space.lens = lens
            rv.view_distance = 17
            area.tag_redraw()
            yield .25
            before = snapshot(st)
            check(before == snapshot(st), 'document/Undo snapshot is deterministic')
            with bpy.context.temp_override(window=window(), area=area, region=region):
                st.feed_line('Zoom 1to1')
            check(st.active_command is None, 'real Zoom 1to1 command completes')
            check('assumed 96 DPI' in st.history[-1] and 'not monitor-calibrated' in st.history[-1],
                  'command truthfully reports assumed, not calibrated density')
            # Let the event loop consume the command's redraw request and rebuild
            # Blender's matrices. Do not manufacture a projection or call rv.update().
            yield .35
            p = projected(region, rv, (0, 0, 0))
            x = projected(region, rv, (span, 0, 0))
            y = projected(region, rv, (0, span, 0))
            dx = math.dist(p, x)
            dy = math.dist(p, y)
            row = {'unit': unit, 'lens': space.lens, 'region': [region.width, region.height],
                   'distance': rv.view_distance, 'pixels_x': dx, 'pixels_y': dy,
                   'matrix': [list(r) for r in rv.perspective_matrix], 'before': before,
                   'after': snapshot(st)}
            RESULT['cases'].append(row)
            check(abs(space.lens - lens) < 1e-6 and not rv.is_perspective, 'lens and parallel mode preserved')
            check(abs(dx - 96) < .002 and abs(dy - 96) < .002,
                  f'{unit} lens={lens:g}: actual projected inch X={dx:.6f}, Y={dy:.6f} reference pixels', fatal=False)
            check(row['after'] == before, 'complete encoded document and Undo/Redo identities unchanged')
    rv.view_perspective = 'PERSP'
    area.tag_redraw()
    yield .2
    before, distance = snapshot(st), rv.view_distance
    st.feed_line('Zoom 1to1')
    check('active view is perspective' in st.history[-1], 'real perspective command refuses by name')
    check(rv.view_distance == distance and snapshot(st) == before, 'perspective refusal changes no view distance/document/Undo')


def window():
    return bpy.context.window_manager.windows[0]


def tick():
    try:
        with bpy.context.temp_override(window=window()):
            return next(STEPS)
    except StopIteration:
        finish()
    except BaseException:
        finish(traceback.format_exc())


threading.Thread(target=lambda: (time.sleep(70), finish('70s child watchdog')), daemon=True).start()
try:
    check(OUT.is_relative_to(Path(tempfile.gettempdir())), 'unique temporary report directory')
    settings = Path(os.environ['NARWHAL3D_SETTINGS_FILE'])
    check(settings.parent == OUT and not settings.exists(), 'fresh disposable settings')
    check('narwhal3d' not in sys.modules and not os.environ.get('NARWHAL3D_UI_READY'), 'test precedes bootstrap')
    sys.path.insert(0, str(ROOT))
    import bpy
    from mathutils import Quaternion, Vector
    from narwhal3d import ui, views
    check(not bpy.app.background, 'actual GUI Blender required')
    bpy.context.preferences.filepaths.use_auto_save_temporary_files = False
    if RESULT['mutation'] == 'lens_ignored':
        original = views.zoom_1to1_distance

        def lens_ignored(*args, **kwargs):
            kwargs['lens_mm'] = views.DEFAULT_SENSOR_WIDTH_MM  # Old formula omits lens/sensor correction.
            return original(*args, **kwargs)

        views.zoom_1to1_distance = lens_ignored
    RESULT['source_sha256'] = {n: hashlib.sha256((ROOT / 'narwhal3d' / n).read_bytes()).hexdigest()
                               for n in ('views.py', 'commands.py')}
    STEPS = run()
    bpy.app.timers.register(tick, first_interval=.1)
except BaseException:
    finish(traceback.format_exc())
