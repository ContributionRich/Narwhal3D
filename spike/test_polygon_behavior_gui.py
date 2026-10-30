#!/usr/bin/env python3
"""Real-window Polygon corner/constraints/rotation proof; Main must run this revision.

python3 -B spike/test_polygon_behavior_gui.py --launch [--plane Right] [--mutations]

Blender integration requires Python. Inputs use Window.event_simulate, not feed;
passive observers forward real draw calls. Safety/launcher pattern follows
spike/test_drawing_behavior_gui.py, with the approved 1536 MiB profile.
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
           'narwhal3d/preview.py', 'narwhal3d/ortho.py', 'narwhal3d/pickconstraint.py',
           'spike/test_polygon_behavior_gui.py')
EXPECTED_RED = {
    'old-preview-dispatch': 'Top draft8: actual draw has 8 sides in all four panes',
    'premature-memory': 'Top: draft count does not update committed memory',
    'fixed-preview-bearing': 'Top Inscribed free first: cursor contact matches expected direction',
    'fixed-commit-frame': 'Top: picked result equals last drawn preview',
    'no-submitted-radius': 'Top Inscribed submitted first: cursor contact matches expected direction',
    'no-pending-radius': 'Top Inscribed pending first: cursor contact matches expected direction',
    'ignore-shift': 'Top Inscribed free shift: cursor contact matches expected direction',
}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def launch():
    import fcntl
    import subprocess

    out = Path(tempfile.mkdtemp(prefix='narwhal-polygon-gui-'))
    print('POLYGON_GUI_RECEIPT', out, flush=True)
    env = {k: v for k, v in os.environ.items() if k in (
        'PATH', 'DISPLAY', 'XAUTHORITY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS', 'LANG')}
    paths = subprocess.check_output(['bash', str(ROOT/'narwhal3d.sh')], cwd=ROOT,
        env=dict(env, NARWHAL3D_LAUNCHER_TEST='native-paths'), text=True, timeout=10)
    native = dict(row.split('=', 1) for row in paths.splitlines() if row)
    env.update({key: value for key, value in native.items() if value})
    runtime = ROOT/'tools/blender-patched/blender'
    if not os.access(runtime, os.X_OK):
        runtime = ROOT/'tools/blender-5.2.2-linux-x64/blender'
    if not os.access(runtime, os.X_OK):
        raise RuntimeError('Installed runtime unavailable; no rebuild in this lane')
    libs = runtime.parent/'5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs'
    if libs.is_dir():
        env['LD_LIBRARY_PATH'] = str(libs)
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
                    raise TimeoutError('Heavy/GUI locks busy; no window launched')
                time.sleep(.25)
        # One plane per bounded child: added interaction coverage must not turn
        # into a longer-lived Blender process. Main can also select one plane.
        planes = ['Top', 'Front', 'Right']
        if '--plane' in sys.argv:
            planes = [sys.argv[sys.argv.index('--plane')+1]]
            if planes[0] not in ('Top', 'Front', 'Right'):
                raise ValueError('--plane must be Top, Front or Right')
        modes = [('baseline', plane) for plane in planes]
        if '--mutations' in sys.argv:
            for mode in EXPECTED_RED:
                modes.extend(((mode, 'Top'), ('restored-'+mode, 'Top')))
        receipts = []
        for mode, plane_label in modes:
            before = {str(p): digest(p) for p in watched}
            if before != initial:
                raise RuntimeError('Watched source/native changed; need a frozen run')
            available = int(next(row.split()[1] for row in Path('/proc/meminfo').read_text().splitlines()
                                 if row.startswith('MemAvailable:')))
            if available < 5632*1024:
                raise RuntimeError('Need >=5.5 GiB available: 1536 MiB cap + 4 GiB desktop reserve')
            case = out/(mode+'-'+plane_label)
            case.mkdir()
            child_env = dict(env)
            for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'TMPDIR'):
                folder = case/key.lower()
                folder.mkdir()
                child_env[key] = str(folder)
            child_env.update(POLYGON_GUI_OUT=str(case), POLYGON_GUI_MODE=mode,
                POLYGON_GUI_PLANE=plane_label,
                NARWHAL3D_SETTINGS_FILE=str(case/'preferences.json'), PYTHONDONTWRITEBYTECODE='1',
                PYTHONPYCACHEPREFIX=str(case/'bytecode'), PYTHONPATH=str(ROOT/'vendor/occt'),
                NARWHAL3D_NO_DIALOGS='1', NARWHAL3D_DEFER_FIRST_PRESENT='1',
                NARWHAL3D_STATUSBAR_HEIGHT='37', GHOST_BACKEND='X11',
                OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
            cmd = ['systemd-run', '--user', '--scope', '--quiet', '-p', 'MemoryMax=1536M',
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
            text = (case/'run.log').read_text()
            report = json.loads((case/'result.json').read_text()) if (case/'result.json').exists() else {}
            bad_log = any(s in text for s in ('Error in draw callback', 'Traceback (most recent call last)'))
            expected = (run.returncode == 1 and report.get('error') == EXPECTED_RED[mode]
                        if mode in EXPECTED_RED else run.returncode == 0 and report.get('status') == 'passed')
            receipts.append(dict(mode=mode, plane=plane_label, exit=run.returncode, status=report.get('status'),
                error=report.get('error'), expected_result=expected, source_unchanged=before == after,
                bad_log=bad_log, checks=len(report.get('checks', [])), path=str(case),
                cgroup=report.get('cgroup')))
            (case/'source_check.json').write_text(json.dumps(dict(before=before, after=after,
                                                                unchanged=before == after), indent=2))
            (out/'receipt.json').write_text(json.dumps(receipts, indent=2))
            print('POLYGON_GUI', plane_label, mode, 'expected' if expected else 'UNEXPECTED',
                  report.get('error') or report.get('status'), 'checks', len(report.get('checks', [])), flush=True)
            if not expected or before != after or bad_log:
                print('See bounded private log:', case/'run.log', flush=True)
                return 1
        print('POLYGON_GUI_COMPLETE', 'GREEN', 'mutation pairs',
              len(EXPECTED_RED) if '--mutations' in sys.argv else 0, out, flush=True)
        return 0
    finally:
        for lock in locks:
            lock.close()


if '--launch' in sys.argv:
    raise SystemExit(launch())
if 'POLYGON_GUI_OUT' not in os.environ:
    raise SystemExit('Use python3 -B spike/test_polygon_behavior_gui.py --launch [--mutations]')

import threading
import traceback
from collections import deque
import bpy

OUT = Path(os.environ['POLYGON_GUI_OUT'])
MODE = os.environ['POLYGON_GUI_MODE']
START = time.monotonic()
RESULT = dict(status='running', mode=MODE, checks=[], events=[], states=[], captures=[], owned_pid=os.getpid())
FINISH_LOCK = threading.Lock()
POSITION = (0, 0)
SHIFT_HELD = False
PAINT = deque(maxlen=8)
WIRES = {}


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
    print('POLYGON_GUI_RESULT', RESULT['status'], error or '', 'checks', len(RESULT['checks']), flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(int(bool(error)))


def window():
    return bpy.context.window_manager.windows[0]


def event(key, value, text=''):
    global SHIFT_HELD
    if key == 'LEFT_SHIFT':
        SHIFT_HELD = value == 'PRESS'
    RESULT['events'].append(dict(type=key, value=value, unicode=text, position=POSITION, shift=SHIFT_HELD))
    window().event_simulate(type=key, value=value, x=POSITION[0], y=POSITION[1], shift=SHIFT_HELD,
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
        key = digits[int(char)] if char.isdigit() else {',': 'COMMA', '=': 'EQUAL'}.get(char, char.upper())
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


def observe_segments(name, args, cursor, plane, **kwargs):
    result = ORIGINAL_SEGMENTS(name, args, cursor, plane, **kwargs)
    if name == 'Polygon' and sys._getframe(1).f_code is ui.draw_command_preview.__code__:
        WIRES[bpy.context.area.as_pointer()] = dict(points=result, cursor=cursor,
            has_cmd=kwargs.get('cmd') is ui.app_state.active_command)
    return result


def painted(label, count=None, shot=False):
    PAINT.clear(); WIRES.clear()
    ui.redraw_cmdbar(); ui._tag_redraw_viewports()
    yield .4
    text = ''.join(PAINT[-1]) if PAINT else ''
    check(bool(text) and (count is None or 'NumSides='+str(count) in text), 'visible command bar: '+label)
    if count is not None:
        check(len(WIRES) == 4 and all(w['has_cmd'] and len(w['points']) == 1
              and len(w['points'][0]) == count+1 for w in WIRES.values()),
              label+f': actual draw has {count} sides in all four panes')
        points = next(iter(WIRES.values()))['points'][0]
        check(all(w['points'] == [points] for w in WIRES.values()), label+': same world outline in every pane')
        check(math.dist(points[0], points[-1]) < 1e-9, label+': closed outline')
    RESULT['states'].append(dict(label=label, painted_prompt=text, input=st.input_buffer,
        command=getattr(st.active_command, 'name', None), object_count=st.model.object_count(),
        wires=list(WIRES.values())))
    if shot:
        path = OUT/(label.replace(' ', '_')+'.png')
        check(bpy.ops.screen.screenshot(filepath=str(path)) == {'FINISHED'}, label+' screenshot')
        RESULT['captures'].append(str(path))


def world(label, u, v):
    return {'Top': (u, v, 0.), 'Front': (u, 0., v), 'Right': (0., u, v)}[label]


def pixel(area, point):
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    from mathutils import Vector
    region = ui._window_region(area)
    q = location_3d_to_region_2d(region, area.spaces.active.region_3d, Vector(point))
    check(q is not None and 35 < q.x < region.width-35 and 35 < q.y < region.height-55,
          'synthetic point safely inside viewport')
    return region.x+q.x, region.y+q.y


def polygon_case(area, plane):
    yield from type_text('Polygon')
    cmd = st.active_command
    check(isinstance(cmd, commands.PolygonCmd) and cmd.opt['NumSides'] == 5, plane+': controlled initial count5')
    yield from type_text('8')
    check(cmd.opt['NumSides'] == 8 and cmd.args == [] and cmd.step_index == 0, plane+': bare count is a draft')
    check(st.ctx._polygon_num_sides == 5, plane+': draft count does not update committed memory')
    yield from type_text('2,3')
    check(cmd.args == [world(plane, 2, 3)], plane+': center in active CPlane')
    yield from move(pixel(area, world(plane, 5, 7)))
    yield from painted(plane+' draft8', 8, shot=True)
    check(st.model.object_count() == 0 and st.ctx.undo.undo_names() == [], plane+': no draft publication/Undo')
    yield from type_text('NumSides=7')
    yield from painted(plane+' changed7', 7)
    yield from tap('ESC')
    check(st.active_command is None and st.model.object_count() == 0
          and st.ctx._polygon_num_sides == 5, plane+': Escape discards draft')
    yield from type_text('Polygon')
    check(st.active_command.opt['NumSides'] == 5, plane+': fresh begin ignores cancelled count')
    yield from type_text('NumSides'); yield from type_text('8')
    yield from type_text('2,3')
    yield from move(pixel(area, world(plane, 5, 7)))
    yield from painted(plane+' commit8', 8)
    if plane != 'Top':
        running = st.active_command
        yield from type_text('2')
        check(st.active_command is running and len(running.args) == 1 and st.model.object_count() == 0,
              plane+': submitted radius2 waits for direction/click')
        yield from move(pixel(area, world(plane, 4, 8)))
        yield from painted(plane+' fixed2', 8)
    outline = next(iter(WIRES.values()))['points'][0]
    yield from tap('LEFTMOUSE')
    check(st.active_command is None and not st.input_buffer, plane+': corner click commits without Sides prompt')
    check(st.model.object_count() == 1, plane+': exactly one document object')
    obj = list(st.model.file.Objects)[0]
    geom = obj.Geometry.ToNurbsCurve()
    check(geom.IsValid and geom.IsClosed and geom.Degree == 1 and len(geom.Points) == 9,
          plane+': exact closed eight-side document curve')
    check(bpy.data.objects.get(obj.Attributes.Name) is not None, plane+': actual display object published')
    controls = [(p.X, p.Y, p.Z) for p in geom.Points]
    check(all(math.dist(a, b) < 1e-8 for a, b in zip(outline, controls)), plane+': picked result equals last drawn preview')
    if plane != 'Top':
        check(abs(math.dist(controls[0], world(plane, 2, 3))-2) < 1e-8,
              plane+': submitted radius2 retained until click')
    check(st.ctx._polygon_num_sides == 8 and st.ctx.undo.undo_names() == ['Polygon'], plane+': finalization remembers count, one Undo')
    yield from painted(plane+' result', shot=True)
    yield from type_text('Polygon')
    check(st.active_command.opt['NumSides'] == 8, plane+': next command starts with committed8')
    yield from type_text('7'); yield from tap('ESC')
    check(st.ctx._polygon_num_sides == 8, plane+': cancelled next draft keeps committed8')
    yield from type_text('Undo')
    check(st.model.object_count() == 0, plane+': real Undo removes committed polygon')


def raw_pick(area, plane):
    """Independent standard-plane ray intersection, before Ortho/radius constraints."""
    from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
    region = ui._window_region(area)
    xy = (POSITION[0]-region.x, POSITION[1]-region.y)
    rv3d = area.spaces.active.region_3d
    origin = region_2d_to_origin_3d(region, rv3d, xy)
    direction = region_2d_to_vector_3d(region, rv3d, xy)
    axis = {'Top': 2, 'Front': 1, 'Right': 0}[plane]
    check(abs(direction[axis]) > .9, plane+': standard orthographic pick ray')
    return tuple(origin[i]-direction[i]*origin[axis]/direction[axis] for i in range(3))


def expected_pick(raw, plane, fixed, ortho_on, shift):
    u_axis, v_axis = {'Top': (0, 1), 'Front': (0, 2), 'Right': (1, 2)}[plane]
    du, dv = raw[u_axis]-2, raw[v_axis]-3
    radius = math.hypot(du, dv)
    angle = math.atan2(dv, du)
    if ortho_on != shift:
        angle = round(angle/(math.pi/2))*(math.pi/2)
        radius = du*math.cos(angle) + dv*math.sin(angle)
    radius = 5 if fixed else radius
    return world(plane, 2+radius*math.cos(angle), 3+radius*math.sin(angle))


def contact(points, mode):
    return points[0] if mode == 'Inscribed' else tuple((a+b)/2 for a, b in zip(points[-2], points[0]))


def check_outline(label, mode, expected):
    points = next(iter(WIRES.values()))['points'][0]
    check(math.dist(contact(points, mode), expected) < 2e-5,
          label+': cursor contact matches expected direction')
    return points


def direction_case(area, plane, mode, radius_kind):
    label = plane+' '+mode+' '+radius_kind
    fixed = radius_kind != 'free'
    ui.set_ortho(False)  # Persistent preference fixture; Shift below is real input.
    yield from type_text('Polygon'); yield from type_text('NumSides=5')
    yield from type_text(mode); yield from type_text('2,3')
    cmd = st.active_command
    check(cmd.steps[cmd.step_index][1] == commands.POINT, label+': actual corner POINT stage')
    yield from move(pixel(area, world(plane, 5, 7)))
    if fixed:
        yield from type_text('5', submit=radius_kind == 'submitted')
        check(st.active_command is cmd and len(cmd.args) == 1 and st.model.object_count() == 0,
              label+': numeric radius waits for moving/click')
        check(st.input_buffer == ('5' if radius_kind == 'pending' else ''), label+': numeric buffer state')
    yield from move(pixel(area, world(plane, 8, 5)))
    raw = raw_pick(area, plane)
    check(abs(math.dist(raw, world(plane, 2, 3))-5) > 1, label+': raw cursor is not radius5')
    yield from painted(label+' first', 5)
    first = check_outline(label+' first', mode, expected_pick(raw, plane, fixed, False, False))
    # Modifier-only updates must rotate immediately, without a mouse move.
    event('LEFT_SHIFT', 'PRESS'); yield .2
    yield from painted(label+' shift', 5)
    check_outline(label+' shift', mode, expected_pick(raw, plane, fixed, False, True))
    event('LEFT_SHIFT', 'RELEASE'); yield .2
    yield from painted(label+' released', 5)
    released = check_outline(label+' released', mode, expected_pick(raw, plane, fixed, False, False))
    check(all(math.dist(a, b) < 1e-8 for a, b in zip(first, released)), label+': release restores free bearing')
    ui.set_ortho(True)
    yield from move(POSITION)
    yield from painted(label+' persistent', 5)
    check_outline(label+' persistent', mode, expected_pick(raw, plane, fixed, True, False))
    event('LEFT_SHIFT', 'PRESS'); yield .2
    yield from painted(label+' xor', 5)
    check_outline(label+' xor', mode, expected_pick(raw, plane, fixed, True, True))
    event('LEFT_SHIFT', 'RELEASE'); yield .2
    yield from painted(label+' xor released', 5)
    check_outline(label+' xor released', mode, expected_pick(raw, plane, fixed, True, False))
    # Commit while Shift disables persistent Ortho, after another free rotation.
    event('LEFT_SHIFT', 'PRESS'); yield .2
    yield from move(pixel(area, world(plane, 4, 8)))
    expected = expected_pick(raw_pick(area, plane), plane, fixed, True, True)
    yield from painted(label+' rotated', 5, shot=radius_kind == 'submitted')
    final = check_outline(label+' rotated', mode, expected)
    check(math.dist(contact(first, mode), contact(final, mode)) > 1, label+': two mouse moves rotate outline')
    check(st.model.object_count() == 0, label+': all previews unpublished')
    yield from tap('LEFTMOUSE')
    event('LEFT_SHIFT', 'RELEASE'); yield .2
    check(st.active_command is None and not st.input_buffer and st.model.object_count() == 1,
          label+': corner LMB commits and consumes radius buffer')
    obj = list(st.model.file.Objects)[0]
    geom = obj.Geometry.ToNurbsCurve()
    check(geom.IsValid and geom.IsClosed and geom.Degree == 1 and len(geom.Points) == 6,
          label+': closed five-side document curve')
    controls = [(p.X, p.Y, p.Z) for p in geom.Points]
    check(all(math.dist(a, b) < 1e-8 for a, b in zip(final, controls)), label+': commit equals last drawn rotated preview')
    check(math.dist(contact(controls, mode), expected) < 2e-5, label+': committed corner/midpoint follows constrained pick')
    if fixed:
        check(abs(math.dist(contact(controls, mode), world(plane, 2, 3))-5) < 1e-8, label+': committed fixed radius5')
    check(st.ctx._polygon_num_sides == 5 and st.ctx.undo.undo_names() == ['Polygon'], label+': count memory and one Undo')
    ui.set_ortho(False)
    yield from type_text('Undo')
    check(st.model.object_count() == 0, label+': Undo restores empty fixture')


def install_mutation(direction=False):
    directional = MODE in ('fixed-preview-bearing', 'no-submitted-radius', 'no-pending-radius', 'ignore-shift')
    if directional != direction:
        return
    if MODE == 'old-preview-dispatch':
        preview.PREVIEWS_WITH_CMD.pop('polygon')
        preview.PREVIEWS['polygon'] = preview.preview_polygon
    elif MODE == 'premature-memory':
        original = commands.PolygonCmd.on_option_set
        def premature(self, option, value, ctx):
            result = original(self, option, value, ctx)
            ctx._polygon_num_sides = self._num_sides
            return result
        commands.PolygonCmd.on_option_set = premature
    elif MODE == 'fixed-preview-bearing':
        import inspect
        source = inspect.getsource(preview.preview_polygon)
        old = 'start_angle = math.atan2(dv, du)'
        assert source.count(old) == 1
        namespace = {}
        exec(compile(source.replace(old, 'start_angle = 0.0'), preview.__file__, 'exec'), preview.__dict__, namespace)
        preview.PREVIEWS_WITH_CMD['polygon'] = namespace['preview_polygon']
    elif MODE == 'fixed-commit-frame':
        import inspect
        import textwrap
        source = textwrap.dedent(inspect.getsource(commands.PolygonCmd.execute))
        assert source.count('plane=oriented)') == 1
        namespace = {}
        exec(compile(source.replace('plane=oriented)', 'plane=plane)'), commands.__file__, 'exec'), commands.__dict__, namespace)
        commands.PolygonCmd.execute = namespace['execute']
    elif MODE == 'no-submitted-radius':
        original = ui.constrain_pick
        ui.constrain_pick = lambda cmd, point: original(cmd, point) if st.input_buffer else point
    elif MODE == 'no-pending-radius':
        ui._typed_constraint = lambda cmd: None
    elif MODE == 'ignore-shift':
        original = ui.apply_ortho
        ui.apply_ortho = lambda cmd, point, area, shift: original(cmd, point, area, False)
    if MODE in EXPECTED_RED:
        RESULT['mutation'] = dict(name=MODE, expected_failure=EXPECTED_RED[MODE], in_memory_only=True)


def run():
    global st
    yield from wait(lambda: os.environ.get('NARWHAL3D_UI_READY') == '1'
                    and ui._preferences_ready and not ui._viewports_in_progress
                    and ui._viewport_count() == 4 and ui.is_input_active(), 'visible bootstrap ready', 50)
    st = ui.app_state
    check(not bpy.app.background and window().width > 800 and window().height > 500, 'actual visible app window')
    check(st.model.object_count() == 0 and st.active_command is None, 'empty disposable startup')
    ui.set_gumball(False); ui.set_grid_snap(False); ui.set_ortho(False)
    region = ui._window_region(ui._cmdbar_area())
    yield from tap('LEFTMOUSE', (region.x+region.width*.7, region.y+15))
    check(ui._console_focus, 'actual click focuses command bar')
    yield from type_text('DisableOsnap')
    install_mutation()
    from mathutils import Vector
    for plane in (os.environ['POLYGON_GUI_PLANE'],):
        # Controlled fixture memory, not a reference factory-default claim.
        st.ctx._polygon_num_sides = 5
        area = ui._pane_by_label(plane)
        check(area is not None, plane+' pane exists')
        rv3d = area.spaces.active.region_3d
        rv3d.view_location = Vector(world(plane, 5., 4.))
        rv3d.view_distance = 22
        rv3d.update()
        yield .25
        yield from tap('LEFTMOUSE', pixel(area, world(plane, 5., 4.)))
        check(ui.active_pane_area() == area, 'actual viewport click activates '+plane)
        yield from polygon_case(area, plane)
        install_mutation(direction=True)
        for mode in ('Inscribed', 'Circumscribed'):
            for radius_kind in ('free', 'submitted', 'pending'):
                yield from direction_case(area, plane, mode, radius_kind)


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
    check(limits['memory.max'] != 'max' and int(limits['memory.max']) <= 1536*1024**2
          and limits['memory.swap.max'] == '0', 'enforced <=1536 MiB / no-swap cgroup')
    check(Path(os.environ['HOME']).is_relative_to(OUT)
          and not Path(os.environ['NARWHAL3D_SETTINGS_FILE']).exists(), 'fresh disposable HOME/preferences')
    check('narwhal3d' not in sys.modules and not os.environ.get('NARWHAL3D_UI_READY'), 'test precedes actual bootstrap')
    sys.path.insert(0, str(ROOT))
    from narwhal3d import ui, commands, preview
    ORIGINAL_DRAW = ui._draw_text
    ORIGINAL_SEGMENTS = preview.segments
    ui._draw_text = observe_text
    preview.segments = observe_segments
    STEPS = run()
    threading.Thread(target=lambda: (time.sleep(170), finish('170s GUI watchdog')), daemon=True).start()
    bpy.app.timers.register(tick, first_interval=.1)
except BaseException as exc:
    RESULT['traceback'] = traceback.format_exc()
    finish(str(exc))
