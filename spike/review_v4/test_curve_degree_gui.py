#!/usr/bin/env python3
"""V3 focused visible Curve Degree2/3 and AI evidence presentation regression.

Run: python3 -B spike/review_v4/test_curve_degree_gui.py --launch
Owns its GUI/heavy locks: do NOT wrap this driver in another flock.
Python is needed for real Blender events/draw observation. No product edits,
installed binary staging, native builds, remote network or owner documents.
AI coverage injects a completion outcome at the workflow boundary; it does NOT
qualify the native V3 library, HTTP transport, approval lifecycle or Send button.
Preview observations are draw-handler inputs, not pixel/antialiasing comparisons.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / 'spike/test_drawing_behavior_gui.py'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def launch():
    import fcntl
    import subprocess

    out = Path(tempfile.mkdtemp(prefix='narwhal-curve-degree-v4-gui-'))
    print('CURVE_DEGREE_GUI_EVIDENCE', out, flush=True)
    env = {k: v for k, v in os.environ.items() if k in (
        'PATH', 'DISPLAY', 'XAUTHORITY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS', 'LANG')}
    paths = subprocess.check_output(['bash', str(ROOT / 'narwhal3d.sh')], cwd=ROOT,
        env=dict(env, NARWHAL3D_LAUNCHER_TEST='native-paths'), text=True, timeout=10)
    native = dict(row.split('=', 1) for row in paths.splitlines() if row)
    env.update({k: v for k, v in native.items() if v})
    runtime = ROOT / 'tools/blender-patched/blender'
    if not os.access(runtime, os.X_OK):
        runtime = ROOT / 'tools/blender-5.2.2-linux-x64/blender'
    if not os.access(runtime, os.X_OK):
        raise RuntimeError('No installed Blender runtime')
    libs = runtime.parent / '5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs'
    if libs.is_dir():
        env['LD_LIBRARY_PATH'] = str(libs)
    watched = [Path(__file__).resolve(), HELPER, ROOT / 'launch/bootstrap.py', ROOT / 'narwhal3d.sh']
    watched += [ROOT / 'narwhal3d' / name for name in (
        'ui.py', 'state.py', 'commands.py', 'bridge.py', 'model.py', 'preview.py',
        'ai_evidence.py', 'ai_assistant.py', 'ai_presentation.py')]
    watched += [Path(value) for value in native.values() if value]
    locks = []
    try:
        deadline = time.monotonic() + 90
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
                    raise TimeoutError('GUI/heavy locks busy; no process launched')
                time.sleep(.25)
        available = int(next(row.split()[1] for row in Path('/proc/meminfo').read_text().splitlines()
                             if row.startswith('MemAvailable:')))
        if available < 6 * 1024 * 1024:
            raise RuntimeError('Need 6 GiB available: 2 GiB GUI cap + 4 GiB desktop reserve')
        before = {str(p): digest(p) for p in watched}
        for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'TMPDIR'):
            folder = out / key.lower()
            folder.mkdir()
            env[key] = str(folder)
        env.update(DRAWING_GUI_OUT=str(out), DRAWING_GUI_MODE='curve-degree-v4',
            NARWHAL3D_SETTINGS_FILE=str(out / 'preferences.json'), PYTHONDONTWRITEBYTECODE='1',
            PYTHONPYCACHEPREFIX=str(out / 'bytecode'), PYTHONPATH=str(ROOT / 'vendor/occt'),
            NARWHAL3D_NO_DIALOGS='1', NARWHAL3D_DEFER_FIRST_PRESENT='1',
            NARWHAL3D_STATUSBAR_HEIGHT='37', GHOST_BACKEND='X11',
            OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
        cmd = ['systemd-run', '--user', '--scope', '--quiet', '-p', 'MemoryMax=2048M',
            '-p', 'MemorySwapMax=0', '-p', 'CPUQuota=100%', 'timeout', '--signal=TERM',
            '--kill-after=5s', '180s', 'bwrap', '--ro-bind', '/', '/', '--dev-bind', '/dev', '/dev',
            '--proc', '/proc', '--bind', str(out), str(out), '--unshare-net', '--die-with-parent',
            str(runtime), '--factory-startup', '-t', '1', '-p', '40', '40', '1440', '1000',
            '--enable-event-simulate', '--python-exit-code', '1',
            '--python', str(Path(__file__).resolve()), '--python', str(ROOT / 'launch/bootstrap.py')]
        (out / 'launch.json').write_text(json.dumps(dict(command=cmd, native_paths=native,
            source_sha256=before, mem_available_kib=available), indent=2))
        with (out / 'run.log').open('w') as log:
            run = subprocess.run(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=190)
        after = {str(p): digest(p) for p in watched}
        (out / 'source_check.json').write_text(json.dumps(dict(before=before, after=after,
                                                              unchanged=before == after), indent=2))
        text = (out / 'run.log').read_text()
        report = json.loads((out / 'result.json').read_text()) if (out / 'result.json').exists() else {}
        bad_log = any(s in text for s in ('Error in draw callback', 'Traceback (most recent call last)',
                                          'preview draw failed:'))
        print('CURVE_DEGREE_GUI', report.get('status', 'missing'), 'exit', run.returncode,
              'checks', len(report.get('checks', [])), 'sources_unchanged', before == after, flush=True)
        if run.returncode or report.get('status') != 'passed' or bad_log or before != after:
            print(text[-5000:], flush=True)
            return 1
        print('CURVE_DEGREE_GUI_COMPLETE', out, flush=True)
        return 0
    finally:
        for lock in locks:
            lock.close()


if '--launch' in sys.argv:
    raise SystemExit(launch())
if 'DRAWING_GUI_OUT' not in os.environ:
    raise SystemExit(__doc__)

# Reuse the existing actual event driver, not its launcher (which owns locks).
# Its timer hasn't run yet; replace its steps before returning to Blender.
import runpy
import queue
import types
import bpy

helper = runpy.run_path(str(HELPER))
h = helper['run'].__globals__
ui, commands = h['ui'], h['commands']
from narwhal3d import preview, ai_evidence

check, tap, type_text = h['check'], h['tap'], h['type_text']
RESULT = h['RESULT']
RESULT['boundaries'] = __doc__
RESULT['mutations'] = []
PREVIEWS = {}
PAINTED_AI = []
real_segments = preview.segments
real_draw = ui._draw_text


def observe_segments(name, args, cursor, plane, resolve=None, cmd=None):
    result = real_segments(name, args, cursor, plane, resolve=resolve, cmd=cmd)
    if (sys._getframe(1).f_code is ui.draw_command_preview.__code__
            and isinstance(cmd, commands.CurveCmd) and len(args) == 3 and result):
        PREVIEWS[bpy.context.area.as_pointer()] = dict(
            samples=[tuple(p) for p in result[0]], cursor=tuple(cursor), degree=cmd.opt['Degree'])
    return result


def observe_draw(x, y, text, *args, **kwargs):
    result = real_draw(x, y, text, *args, **kwargs)
    frame = sys._getframe(1)
    if frame.f_code is ui._draw_ai_transcript.__code__:
        PAINTED_AI.append((frame.f_locals.get('role'), str(text)))
    return result


preview.segments = observe_segments


def matches(samples, curve):
    domain = curve.Domain
    return max(math.dist(p, h['xyz'](curve.PointAt(domain.T0 +
               (domain.T1 - domain.T0) * i / (len(samples) - 1))))
               for i, p in enumerate(samples)) < 1e-8


def redraw_preview():
    PREVIEWS.clear()
    ui._tag_redraw_viewports()
    yield .5
    areas = {a.as_pointer() for a in bpy.context.screen.areas if a.type == 'VIEW_3D'}
    check(len(areas) == 4 and set(PREVIEWS) == areas, 'preview observed in all four real draw handlers')


def curve_case(plane, degree):
    st = ui.app_state
    label = f'{plane} Degree{degree}'
    before = h['object_ids']()
    undo = st.ctx.undo.top_serial
    yield from type_text('Curve')
    cmd = st.active_command
    check(isinstance(cmd, commands.CurveCmd), label + ' real command started')
    yield from type_text('Degree')
    yield from type_text(str(degree))
    check(cmd.opt['Degree'] == str(degree), label + ' option set by real keyboard')
    for point in ('1,1', '3,5', '6,2'):
        yield from type_text(point)
    area = ui._pane_by_label(plane)
    yield from h['move'](h['pixel'](area, h['world'](plane, 9, 6)))
    yield from redraw_preview()
    baseline = dict(PREVIEWS)
    cursor = baseline[area.as_pointer()]['cursor']
    check(all(row['cursor'] == cursor for row in baseline.values()), label + ' shared world cursor')
    check(st.model.object_count() == len(before), label + ' preview publishes nothing')
    shot = h['OUT'] / (label.replace(' ', '_') + '_preview.png')
    check(bpy.ops.screen.screenshot(filepath=str(shot)) == {'FINISHED'}, label + ' preview screenshot')
    RESULT['captures'].append(str(shot))

    # Fault injection is strictly in memory; the real draw handler must expose it.
    broken = None
    if plane == 'Top' and degree == 2:
        original = preview.control_point_curve_polyline
        preview.control_point_curve_polyline = lambda controls, **kw: original(controls, max_degree=3)
        try:
            yield from redraw_preview()
            broken = dict(PREVIEWS)
        finally:
            preview.control_point_curve_polyline = original
        yield from redraw_preview()
        baseline = dict(PREVIEWS)

    yield from tap('LEFTMOUSE')
    check(st.active_command is cmd and len(cmd.args) == 4 and st.model.object_count() == len(before),
          label + ' fourth point still waits for Enter')
    check(math.dist(cmd.args[-1], cursor) < 1e-10, label + ' clicked point equals preview cursor')
    yield from tap('RET')
    curve = h['one_object'](label, before).ToNurbsCurve()
    check(curve.IsValid and curve.Degree == degree and len(curve.Points) == 4
          and not curve.IsRational and not curve.IsPolyline(), label + ' exact four-control NURBS')
    check(all(matches(row['samples'], curve) for row in baseline.values()),
          label + ' all four viewport previews match published exact geometry')
    if broken is not None:
        check(all(not matches(row['samples'], curve) for row in broken.values()),
              'forced cubic Degree2 preview mutation RED; restored preview GREEN')
        RESULT['mutations'].append('forced-cubic-preview RED -> restored GREEN')
    encoding = curve.Encode()
    published_ids = h['object_ids']()
    serial = st.ctx.undo.top_serial
    check(serial != undo and st.active_command is None, label + ' publication has Undo record')
    yield from type_text('Undo')
    check(h['object_ids']() == before and st.ctx.undo.top_serial == undo, label + ' real keyboard Undo')
    yield from type_text('Redo')
    restored = h['one_object'](label + ' Redo', before).ToNurbsCurve()
    check(h['object_ids']() == published_ids and restored.Encode() == encoding,
          label + ' real keyboard Redo restores identical exact curve and UUID')
    RESULT['geometry'].append(dict(case=label, degree=curve.Degree, controls=[h['xyz'](p) for p in curve.Points],
        preview_panes=len(baseline), preview_samples=len(next(iter(baseline.values()))['samples'])))
    yield from h['painted'](label + '_result', shot=True)
    yield from type_text('Undo')
    check(h['object_ids']() == before, label + ' fixture cleaned by keyboard Undo')


def evidence_case():
    ui._draw_text = observe_draw
    st = ui.ai_assistant_state()
    box = ui._right_tab_boxes[4]
    yield from tap('LEFTMOUSE', ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2))
    check(ui.ai_panel_open(), 'actual AI tab click opens visible transcript')
    before = ui.app_state.model.file.Encode(), ui.app_state.ctx.undo.top_serial
    ui._ai_scope()  # Establish the actual document/config identity before injection.
    check('workflow' not in st.extra, 'no real workflow overwritten by presentation fixture')
    state = dict(known_action_results=0, approved_creation_receipts=0,
                 provider_text_is_geometry_evidence=False, current_geometry_verified=False)
    workflow = types.SimpleNamespace(poll_timeout=lambda: {'status': 'idle'},
        finish=lambda *_: {'status': 'completed', 'cad_state': state})
    original = ai_evidence.status_text
    try:
        for mode in ('baseline', 'dropped-evidence', 'restored', 'approved-receipt'):
            st.messages.clear()
            st.add_message('assistant', 'Provider prose claims a cube exists.')
            state['known_action_results'] = state['approved_creation_receipts'] = int(mode == 'approved-receipt')
            expected = original(state)
            ai_evidence.status_text = (lambda _: None) if mode == 'dropped-evidence' else original
            st.extra['workflow'] = workflow
            output = queue.Queue()
            output.put(object())
            ui._ai['transport_output'] = output
            ui._ai_poll_transport()  # Actual hook, synthetic completion boundary, no HTTP/native claim.
            PAINTED_AI.clear()
            ui._tag_redraw_bands()
            yield .6
            # BLF wrapping preserves trailing spaces at visual line boundaries.
            status = ' '.join(' '.join(text for role, text in PAINTED_AI if role == 'status').split())
            present = any(m.role == 'status' and m.text == expected for m in st.messages)
            painted = expected in status
            RESULT.setdefault('evidence', []).append(dict(mode=mode, present=present, painted=painted,
                expected=expected, draws=PAINTED_AI[:80], messages=[(m.role, m.text) for m in st.messages]))
            if mode == 'dropped-evidence':
                check(not present and not painted, 'dropped AI evidence hook mutation RED')
            else:
                check(present and painted, mode + ' separate CAD evidence status actually painted')
                check(any(role == 'assistant' and 'Provider prose' in text for role, text in PAINTED_AI),
                      mode + ' provider prose remains a separate assistant bubble')
                path = h['OUT'] / ('ai_evidence_' + mode + '.png')
                check(bpy.ops.screen.screenshot(filepath=str(path)) == {'FINISHED'}, mode + ' AI screenshot')
                RESULT['captures'].append(str(path))
        RESULT['mutations'].append('dropped-evidence RED -> restored GREEN')
    finally:
        ai_evidence.status_text = original
        ui._draw_text = real_draw
        st.extra.pop('workflow', None)
        ui._ai.pop('transport_output', None)
    check((ui.app_state.model.file.Encode(), ui.app_state.ctx.undo.top_serial) == before,
          'AI presentation fixtures did not change document or Undo')


def run():
    yield from h['wait'](lambda: os.environ.get('NARWHAL3D_UI_READY') == '1'
        and ui._preferences_ready and not ui._viewports_in_progress
        and ui._viewport_count() == 4 and ui.is_input_active(), 'visible bootstrap ready', 50)
    h['st'] = st = ui.app_state
    check(not bpy.app.background and h['window']().width > 800 and st.model.object_count() == 0,
          'actual visible disposable empty app')
    ui.set_gumball(False)
    ui.set_grid_snap(False)
    ui.set_ortho(False)
    region = ui._window_region(ui._cmdbar_area())
    yield from tap('LEFTMOUSE', (region.x + region.width * .7, region.y + 15))
    yield from type_text('DisableOsnap')
    from mathutils import Vector
    for plane in ('Top', 'Front'):
        area = ui._pane_by_label(plane)
        rv3d = area.spaces.active.region_3d
        rv3d.view_location = Vector(h['world'](plane, 5, 4))
        rv3d.view_distance = 22
        rv3d.update()
        yield .25
        yield from tap('LEFTMOUSE', h['pixel'](area, h['world'](plane, 5, 4)))
        check(ui.active_pane_area() == area, 'actual viewport click activates ' + plane)
        for degree in (2, 3):
            yield from curve_case(plane, degree)
    yield from evidence_case()


h['STEPS'] = run()
