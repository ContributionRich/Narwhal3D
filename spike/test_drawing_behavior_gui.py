#!/usr/bin/env python3
"""Visible Line constraint / default few-control-point Curve regressions.

python3 -B spike/test_drawing_behavior_gui.py --launch [--mutations]

Only camera/preferences setup uses APIs. Commands, coordinates, submitted/pending
numbers, LMB and final Enter use real Window.event_simulate events after bootstrap.
Python is required for Blender's window/UI inspection; geometry is the real document
curve, not the display mesh. No product writes, native builds, VM or owner fixtures.
See docs/measured/DRAWING_BEHAVIOR_REGRESSIONS.md for evidence boundaries.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ('launch/bootstrap.py', 'narwhal3d.sh', 'narwhal3d/ui.py',
           'narwhal3d/state.py', 'narwhal3d/commands.py', 'narwhal3d/bridge.py',
           'narwhal3d/model.py', 'narwhal3d/cplane.py', 'narwhal3d/coords.py',
           'narwhal3d/pickconstraint.py', 'spike/test_drawing_behavior_gui.py')
EXPECTED_RED = {
    'no-constrained-click': 'Top submitted: document line length is 4',
    'no-pending-constraint': 'Top pending: document line length is 4',
    'linear-curve': 'Top Curve3: degree is 2',
    'early-curve-finish': 'Top Curve2: point 2 waits for explicit Enter',
}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def launch():
    import fcntl
    import subprocess

    out = Path(tempfile.mkdtemp(prefix='narwhal-drawing-gui-'))
    print('DRAWING_GUI_RECEIPT', out, flush=True)
    env = {k: v for k, v in os.environ.items() if k in (
        'PATH', 'DISPLAY', 'XAUTHORITY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS', 'LANG')}
    # Launcher test mode selects installed libraries only; it exits before desktop
    # registration/startup. Clear inherited native overrides for reproducibility.
    paths = subprocess.check_output(['bash', str(ROOT/'narwhal3d.sh')], cwd=ROOT,
        env=dict(env, NARWHAL3D_LAUNCHER_TEST='native-paths'), text=True, timeout=10)
    native = dict(row.split('=', 1) for row in paths.splitlines() if row)
    env.update({key: value for key, value in native.items() if value})
    runtime = ROOT/'tools/blender-patched/blender'
    if not os.access(runtime, os.X_OK):
        runtime = ROOT/'tools/blender-5.2.2-linux-x64/blender'
    if not os.access(runtime, os.X_OK):
        raise RuntimeError('No installed launcher-selected runtime; do not build in this lane')
    occt_libs = runtime.parent/'5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs'
    if occt_libs.is_dir():
        env['LD_LIBRARY_PATH'] = str(occt_libs)
    watched = [ROOT/p for p in SOURCES] + [Path(v) for v in native.values() if v]
    initial = {str(p): digest(p) for p in watched}
    locks = []
    try:
        deadline = time.monotonic()+90
        while True:
            try:
                for name in ('/tmp/narwhal3d-heavy-build.lock', '/tmp/narwhal3d_gui.lock'):
                    lock = open(name, 'a')
                    locks.append(lock)
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                for lock in locks:
                    lock.close()
                locks.clear()
                if time.monotonic() >= deadline:
                    raise TimeoutError('GUI/heavy lock busy; no window launched')
                time.sleep(.25)
        modes = ['baseline']
        if '--mutations' in sys.argv:
            for mutation in EXPECTED_RED:
                modes.extend((mutation, 'restored-'+mutation))
        receipts = []
        for mode in modes:
            before = {str(p): digest(p) for p in watched}
            if before != initial:
                raise RuntimeError('Source/native changed concurrently; ask Main for a frozen run')
            available = int(next(row.split()[1] for row in Path('/proc/meminfo').read_text().splitlines()
                                 if row.startswith('MemAvailable:')))
            if available < 6*1024*1024:
                raise RuntimeError('Need >=6 GiB available (2 GiB cap + 4 GiB desktop reserve)')
            case = out/mode
            case.mkdir()
            child_env = dict(env)
            for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'TMPDIR'):
                folder = case/key.lower()
                folder.mkdir()
                child_env[key] = str(folder)
            child_env.update(DRAWING_GUI_OUT=str(case), DRAWING_GUI_MODE=mode,
                NARWHAL3D_SETTINGS_FILE=str(case/'preferences.json'), PYTHONDONTWRITEBYTECODE='1',
                PYTHONPYCACHEPREFIX=str(case/'bytecode'), PYTHONPATH=str(ROOT/'vendor/occt'),
                NARWHAL3D_NO_DIALOGS='1', NARWHAL3D_DEFER_FIRST_PRESENT='1',
                NARWHAL3D_STATUSBAR_HEIGHT='37', GHOST_BACKEND='X11',
                OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
            cmd = ['systemd-run', '--user', '--scope', '--quiet', '-p', 'MemoryMax=2048M',
                '-p', 'MemorySwapMax=0', '-p', 'CPUQuota=100%', 'timeout', '--signal=TERM',
                '--kill-after=5s', '180s', 'bwrap', '--ro-bind', '/', '/', '--dev-bind', '/dev', '/dev',
                '--proc', '/proc', '--bind', str(case), str(case), '--unshare-net', '--die-with-parent',
                str(runtime), '--factory-startup', '-t', '1', '-p', '40', '40', '1440', '1000',
                '--enable-event-simulate', '--python-exit-code', '1',
                '--python', str(Path(__file__).resolve()), '--python', str(ROOT/'launch/bootstrap.py')]
            (case/'launch.json').write_text(json.dumps(dict(command=cmd, native_paths=native,
                mem_available_kib=available, source_sha256=before), indent=2))
            with (case/'run.log').open('w') as log:
                run = subprocess.run(cmd, cwd=ROOT, env=child_env, stdout=log,
                                     stderr=subprocess.STDOUT, timeout=190)
            after = {str(p): digest(p) for p in watched}
            (case/'source_check.json').write_text(json.dumps(dict(before=before, after=after,
                                                                unchanged=before == after), indent=2))
            text = (case/'run.log').read_text()
            report = json.loads((case/'result.json').read_text()) if (case/'result.json').exists() else {}
            bad_log = any(s in text for s in ('Error in draw callback', 'Traceback (most recent call last)'))
            expected = (run.returncode == 1 and report.get('error') == EXPECTED_RED[mode]
                        if mode in EXPECTED_RED else run.returncode == 0 and report.get('status') == 'passed')
            receipts.append(dict(mode=mode, exit=run.returncode, status=report.get('status'),
                error=report.get('error'), expected_result=expected, source_unchanged=before == after,
                bad_log=bad_log, checks=len(report.get('checks', [])), path=str(case)))
            (out/'receipt.json').write_text(json.dumps(receipts, indent=2))
            print('DRAWING_GUI', mode, 'expected' if expected else 'UNEXPECTED',
                  report.get('error') or report.get('status'), 'checks', len(report.get('checks', [])), flush=True)
            if not expected or before != after or bad_log:
                # Full private log stays in the receipt; never emit huge dictionaries.
                print('DRAWING_GUI_DIAGNOSTIC', text[-2500:], flush=True)
                return 1
        print('DRAWING_GUI_COMPLETE', 'GREEN / 4 RED->GREEN mutations' if len(modes) > 1 else 'GREEN', out, flush=True)
        return 0
    finally:
        for lock in locks:
            lock.close()


if '--launch' in sys.argv:
    raise SystemExit(launch())
if 'DRAWING_GUI_OUT' not in os.environ:
    raise SystemExit('Use python3 -B spike/test_drawing_behavior_gui.py --launch [--mutations]')

import threading
import traceback
from collections import deque
import bpy
import rhino3dm as r3

OUT = Path(os.environ['DRAWING_GUI_OUT'])
MODE = os.environ['DRAWING_GUI_MODE']
START = time.monotonic()
RESULT = dict(status='running', mode=MODE, checks=[], events=[], states=[], geometry=[], captures=[], owned_pid=os.getpid())
FINISH_LOCK = threading.Lock()
POSITION = (0, 0)
PAINT = deque(maxlen=8)


def check(ok, label):
    RESULT['checks'].append(dict(ok=bool(ok), label=label))
    if not ok:
        raise AssertionError(label)


def cgroup_limits():
    group = Path('/sys/fs/cgroup'+Path('/proc/self/cgroup').read_text().strip().split(':', 2)[2])
    return {name: (group/name).read_text().strip()
            for name in ('memory.max', 'memory.swap.max', 'memory.peak')}


def finish(error=None):
    if not FINISH_LOCK.acquire(False):
        return
    RESULT.update(status='failed' if error else 'passed', error=error, elapsed_s=time.monotonic()-START)
    try:
        RESULT['cgroup'] = cgroup_limits()
    except OSError:
        pass
    (OUT/'result.json').write_text(json.dumps(RESULT, indent=2))
    print('DRAWING_GUI_RESULT', RESULT['status'], error or '', 'checks', len(RESULT['checks']), flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(int(bool(error)))


def window():
    return bpy.context.window_manager.windows[0]


def event(key, value, text=''):
    RESULT['events'].append(dict(type=key, value=value, unicode=text, position=POSITION))
    window().event_simulate(type=key, value=value, x=POSITION[0], y=POSITION[1],
                           **({'unicode': text} if text else {}))


def move(point):
    global POSITION
    POSITION = tuple(round(v) for v in point)
    event('MOUSEMOVE', 'NOTHING')
    yield .18


def tap(key, point=None):
    if point is not None:
        yield from move(point)
    event(key, 'PRESS')
    yield .06
    event(key, 'RELEASE')
    yield .22


def wait(predicate, label, seconds=6):
    deadline = time.monotonic()+seconds
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError('timed out: '+label)
        yield .06
    check(True, label)


def type_text(text, submit=True):
    check(not st.input_buffer, 'empty keyboard buffer before '+text)
    digits = ('ZERO', 'ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX', 'SEVEN', 'EIGHT', 'NINE')
    for char in text:
        key = digits[int(char)] if char.isdigit() else {',': 'COMMA', '-': 'MINUS', '.': 'PERIOD'}.get(char, char.upper())
        event(key, 'PRESS', char)
        yield .025
        event(key, 'RELEASE')
        yield .025
    check(st.input_buffer == text, 'window keyboard populated '+text)
    if submit:
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
    ui.redraw_cmdbar()
    ui._tag_redraw_viewports()
    yield .35
    text = ''.join(PAINT[-1]) if PAINT else ''
    check(bool(text) and (expected is None or expected in text), 'visible command bar: '+label)
    RESULT['states'].append(dict(label=label, painted_prompt=text, input=st.input_buffer,
        command=getattr(st.active_command, 'name', None), object_count=st.model.object_count()))
    if shot:
        path = OUT/(label.replace(' ', '_').replace(':', '')+'.png')
        check(bpy.ops.screen.screenshot(filepath=str(path)) == {'FINISHED'}, label+' screenshot')
        RESULT['captures'].append(str(path))


def xyz(point):
    return point.X, point.Y, point.Z


def world(label, u, v):
    # Independent oracle for the two STANDARD planes, not active_cplane.to_world.
    return (u, v, 0.) if label == 'Top' else (u, 0., v)


def pixel(area, point):
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    from mathutils import Vector
    region = ui._window_region(area)
    q = location_3d_to_region_2d(region, area.spaces.active.region_3d, Vector(point))
    check(q is not None and 35 < q.x < region.width-35 and 35 < q.y < region.height-55,
          'synthetic point safely inside viewport')
    return region.x+q.x, region.y+q.y


def unproject(area, position, label):
    from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
    region = ui._window_region(area)
    rv3d = area.spaces.active.region_3d
    xy = (position[0]-region.x, position[1]-region.y)
    origin = region_2d_to_origin_3d(region, rv3d, xy)
    direction = region_2d_to_vector_3d(region, rv3d, xy)
    normal_axis = 2 if label == 'Top' else 1
    check(abs(direction[normal_axis]) > .9, label+' orthographic pick ray')
    return tuple(origin[i]-direction[i]*origin[normal_axis]/direction[normal_axis] for i in range(3))


def one_object(label, before):
    objects = [obj for obj in st.model.file.Objects if str(obj.Attributes.Id) not in before]
    check(len(objects) == 1 and st.model.object_count() == len(before)+1,
          label+': exactly one document object published')
    obj = objects[0]
    check(bpy.data.objects.get(obj.Attributes.Name) is not None, label+': real display object exists')
    return obj.Geometry


def object_ids():
    return {str(obj.Attributes.Id) for obj in st.model.file.Objects}


def undo_result(label):
    yield from type_text('Undo')
    check(st.model.object_count() == 0 and st.active_command is None and not st.input_buffer,
          label+': real keyboard Undo restores empty fixture')


def line_case(area, plane, submitted):
    label = plane+(' submitted' if submitted else ' pending')
    before = object_ids()
    yield from type_text('Line')
    cmd = st.active_command
    check(isinstance(cmd, commands.LineCmd), label+': real Line command started')
    yield from type_text('1,1')
    start = world(plane, 1., 1.)
    check(st.active_command is cmd and len(cmd.args) == 1 and math.dist(cmd.args[0], start) < 1e-12,
          label+': non-origin start in active CPlane')
    # Arm away from the eventual direction, so using a stale hover also fails.
    yield from move(pixel(area, world(plane, 3., 7.)))
    yield from type_text('4', submit=submitted)
    check(st.active_command is cmd and len(cmd.args) == 1 and st.model.object_count() == len(before),
          label+': scalar retains pending command and publishes no line')
    check(st.input_buffer == ('' if submitted else '4'), label+': submitted/pending buffer state')
    yield from painted(label+'_pending', 'Point 2', shot=True)
    yield from move(pixel(area, world(plane, 9., 6.)))
    raw = unproject(area, POSITION, plane)
    raw_length = math.dist(raw, start)
    check(abs(raw_length-4.) > 3. and abs(raw[0]-start[0]) > 2.
          and abs(raw[1 if plane == 'Top' else 2]-1.) > 2., label+': cursor off-radius AND off-axis')
    expected_end = tuple(start[i]+4.*(raw[i]-start[i])/raw_length for i in range(3))
    yield from tap('LEFTMOUSE')
    geom = one_object(label, before)
    check(isinstance(geom, r3.NurbsCurve) and geom.IsValid and geom.Degree == 1
          and len(geom.Points) == 2, label+': real valid degree-1 two-control line')
    a, b = xyz(geom.PointAtStart), xyz(geom.PointAtEnd)
    RESULT['geometry'].append(dict(case=label, start=a, end=b, length=math.dist(a, b),
        raw_cursor=raw, raw_distance=raw_length, expected_end=expected_end))
    check(abs(math.dist(a, b)-4.) < 1e-9, label+': document line length is 4')
    check(math.dist(a, start) < 1e-12 and math.dist(b, expected_end) < 2e-5,
          label+': start retained and endpoint follows new mouse direction in CPlane')
    check(abs(b[2 if plane == 'Top' else 1]) < 1e-9, label+': endpoint remains on requested plane')
    check(st.active_command is None and not st.input_buffer, label+': LMB commits and consumes numeric buffer')
    yield from painted(label+'_result', shot=True)
    yield from undo_result(label)


def curve_case(plane, count):
    label = f'{plane} Curve{count}'
    points = ((1, 1), (3, 5), (6, 2), (9, 6))[:count]
    expected = [world(plane, *point) for point in points]
    before = object_ids()
    yield from type_text('Curve')
    cmd = st.active_command
    check(isinstance(cmd, commands.CurveCmd), label+': real Curve command started')
    for index, (u, v) in enumerate(points, 1):
        yield from type_text(f'{u},{v}')
        check(st.active_command is cmd and len(cmd.args) == index
              and st.model.object_count() == len(before) and not st.input_buffer,
              label+f': point {index} waits for explicit Enter')
    yield from painted(label+'_pending', f'Point {count+1}', shot=count == 4)
    yield from tap('RET')
    geom = one_object(label, before)
    source_type = type(geom).__name__
    check(isinstance(geom, r3.Curve) and geom.IsValid and not geom.IsClosed,
          label+': valid open document curve, not a render mesh')
    # openNURBS may return a LineCurve for two controls; its exact NURBS
    # conversion preserves the line. Do not require one storage subclass.
    geom = geom.ToNurbsCurve()
    check(geom is not None and geom.IsValid, label+': exact NURBS representation')
    check(geom.Degree == count-1, label+f': degree is {count-1}')
    controls = [xyz(geom.Points[i]) for i in range(len(geom.Points))]
    check(len(controls) == count and all(math.dist(a, b) < 1e-12 for a, b in zip(controls, expected)),
          label+': exact ordered control points in requested CPlane')
    check(not geom.IsRational and all(abs(geom.Points[i].W-1.) < 1e-12 for i in range(count)),
          label+': unit control weights')
    # DERIVED: these clamped degree n-1 / n-control curves are single-span Beziers.
    # The Bernstein midpoint distinguishes control-point construction from an
    # interpolation-through-inputs implementation without asserting interpolation.
    midpoint = tuple(sum(math.comb(count-1, j)*expected[j][i] for j in range(count))/2**(count-1)
                     for i in range(3))
    actual_midpoint = xyz(geom.PointAt((geom.Domain.T0+geom.Domain.T1)/2.))
    check(math.dist(actual_midpoint, midpoint) < 1e-10, label+': control-polygon Bernstein midpoint')
    check(math.dist(xyz(geom.PointAtStart), expected[0]) < 1e-12
          and math.dist(xyz(geom.PointAtEnd), expected[-1]) < 1e-12, label+': exact end controls')
    check(st.active_command is None and not st.input_buffer, label+': empty Enter commits curve')
    RESULT['geometry'].append(dict(case=label, source_type=source_type,
        degree=geom.Degree, controls=controls, knots=list(geom.Knots), midpoint=actual_midpoint))
    yield from painted(label+'_result', shot=count == 4)
    yield from undo_result(label)


def install_mutation():
    if MODE == 'no-constrained-click':
        ui.constrain_pick = lambda cmd, point: point
    elif MODE == 'no-pending-constraint':
        ui._typed_constraint = lambda cmd: None
    elif MODE == 'linear-curve':
        original = bridge.make_polyline
        bridge.make_polyline = lambda points, degree=3, closed=False: original(points, degree=1, closed=closed)
    elif MODE == 'early-curve-finish':
        original = commands.CurveCmd.on_point
        commands.CurveCmd.on_point = lambda self, point, ctx: (
            self.execute(ctx) if len(self.args) == 2 else original(self, point, ctx))
    if MODE in EXPECTED_RED:
        RESULT['mutation'] = dict(name=MODE, expected_failure=EXPECTED_RED[MODE], in_memory_only=True)


def run():
    global st
    yield from wait(lambda: os.environ.get('NARWHAL3D_UI_READY') == '1'
                    and ui._preferences_ready and not ui._viewports_in_progress
                    and ui._viewport_count() == 4 and ui.is_input_active(), 'visible bootstrap ready', 50)
    st = ui.app_state
    check(not bpy.app.background and window().width > 800 and window().height > 500,
          'actual visible app window')
    check(st.model.object_count() == 0 and st.active_command is None, 'empty disposable startup')
    ui.set_gumball(False)
    ui.set_grid_snap(False)
    ui.set_ortho(False)
    region = ui._window_region(ui._cmdbar_area())
    yield from tap('LEFTMOUSE', (region.x+region.width*.7, region.y+15))
    check(ui._console_focus, 'actual click focuses command bar')
    yield from type_text('DisableOsnap')
    install_mutation()
    from mathutils import Vector
    for plane in ('Top', 'Front'):
        area = ui._pane_by_label(plane)
        check(area is not None, plane+' pane exists')
        rv3d = area.spaces.active.region_3d
        rv3d.view_location = Vector(world(plane, 5., 4.))
        rv3d.view_distance = 22
        rv3d.update()
        yield .25
        yield from tap('LEFTMOUSE', pixel(area, world(plane, 5., 4.)))
        check(ui.active_pane_area() == area, 'actual viewport click activates '+plane)
        for submitted in (True, False):
            yield from line_case(area, plane, submitted)
        for count in (2, 3, 4):
            yield from curve_case(plane, count)


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
    limits = cgroup_limits()
    check(limits['memory.max'] != 'max' and int(limits['memory.max']) <= 2*1024**3
          and limits['memory.swap.max'] == '0', 'enforced <=2 GiB / no-swap cgroup')
    check(Path(os.environ['HOME']).is_relative_to(OUT)
          and not Path(os.environ['NARWHAL3D_SETTINGS_FILE']).exists(), 'fresh disposable HOME/preferences')
    check('narwhal3d' not in sys.modules and not os.environ.get('NARWHAL3D_UI_READY'),
          'test precedes actual bootstrap')
    sys.path.insert(0, str(ROOT))
    from narwhal3d import ui, commands, bridge
    ORIGINAL_DRAW = ui._draw_text
    ui._draw_text = observe_text  # Passive draw observer forwards every real draw.
    STEPS = run()
    threading.Thread(target=lambda: (time.sleep(170), finish('170s GUI watchdog')), daemon=True).start()
    bpy.app.timers.register(tick, first_interval=.1)
except BaseException as exc:
    RESULT['traceback'] = traceback.format_exc()
    finish(str(exc))
