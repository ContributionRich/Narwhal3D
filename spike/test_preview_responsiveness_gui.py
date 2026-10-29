"""Integrated real-event preview gates, loaded BEFORE launch/bootstrap.py.

Public runner (system Python, no builds):
  python3 -B spike/test_preview_responsiveness_gui.py --run integration
  python3 -B spike/test_preview_responsiveness_gui.py --run gumball
  python3 -B spike/test_preview_responsiveness_gui.py --run fillet
  python3 -B spike/test_preview_responsiveness_gui.py --run integration --mutation hidden

Runner: shared GUI lock, fresh HOME/XDG/TMPDIR/settings, launcher-selected native
artifacts, MemoryMax=3G/MemorySwapMax=0, bounded process/watchdog. No private imports.
Child marker PREVIEW_RESPONSIVENESS_RESULT; existing suite markers retained.
Only fixtures/command starts use APIs. Tested motion, release, Esc, options and Undo
use Window.event_simulate. Observers forward real production calls. Pixels come
from the actual app framebuffer around its actual draw submissions, not an offscreen
reconstruction. Pixel-readback frames are excluded from latency summaries.
"""
from collections import Counter

import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
import threading
import time
import traceback


ROOT = Path(__file__).resolve().parents[1]


def public_run():
    import argparse
    import fcntl
    import subprocess
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', choices=('integration', 'gumball', 'fillet'), required=True)
    parser.add_argument('--mutation', choices=('old_compute', 'latest', 'hidden', 'missing_cap',
                                                'no_cancel', 'no_invalidation'), default='')
    parser.add_argument('--case', choices=('all', 'previews', 'circle', 'annulus', 'fillet', 'source_change', 'cancel'), default='all')
    args = parser.parse_args()
    scripts = {'integration': ('test_preview_responsiveness_gui.py', 'PREVIEW_RESPONSIVENESS_RESULT'),
               'gumball': ('test_gumball_extrude_scale_gui.py', 'GUMBALL_GUI_RESULT'),
               'fillet': ('test_fillet_mouse_gui.py', 'FILLET_MOUSE_RESULT')}
    script, marker = scripts[args.run]
    if args.mutation and args.run != 'integration':
        parser.error('mutations apply only to the new integration suite')
    base = ROOT / 'artifacts/tmp'
    base.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix='preview-' + args.run + '-', dir=base))
    print('PREVIEW_RUN_OUTPUT', output, flush=True)
    with open('/tmp/narwhal3d_gui.lock', 'a') as lock:
        print('Waiting for shared GUI lock (120 s bound)', flush=True)
        until = time.monotonic() + 120
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > until:
                    raise RuntimeError('GUI lock busy; no application launched')
                time.sleep(.25)
        with tempfile.TemporaryDirectory(prefix='isolated-', dir=output) as tmp:
            scratch = Path(tmp)
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(('NARWHAL', 'RHI' + 'NO', 'BLENDER_'))
                   and k not in ('PYTHONPATH', 'LD_LIBRARY_PATH')}
            probe_env = dict(env, NARWHAL3D_LAUNCHER_TEST='native-paths')
            native = subprocess.run([str(ROOT / 'narwhal3d.sh')], env=probe_env,
                                    cwd=ROOT, text=True, capture_output=True, check=True, timeout=10)
            for line in native.stdout.splitlines():
                key, value = line.split('=', 1)
                if value:
                    env[key] = value
            for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
                directory = scratch / key.lower()
                directory.mkdir()
                env[key] = str(directory)
            env.update(TMPDIR=str(scratch), PYTHONDONTWRITEBYTECODE='1',
                       PYTHONPATH=str(ROOT / 'vendor/occt'),
                       LD_LIBRARY_PATH=str(ROOT / 'tools/blender-patched/5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs'),
                       NARWHAL3D_SETTINGS_FILE=str(scratch / 'preferences.json'),
                       NARWHAL3D_DEFER_FIRST_PRESENT='1', NARWHAL3D_NO_DIALOGS='1',
                       OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2',
                       PREVIEW_RESP_OUTPUT=str(output), PREVIEW_RESP_MUTATION=args.mutation,
                       PREVIEW_RESP_CASE=args.case)
            command = ['systemd-run', '--user', '--scope', '--quiet',
                       '-p', 'MemoryMax=3G', '-p', 'MemorySwapMax=0',
                       'timeout', '--kill-after=3', '190s',
                       str(ROOT / 'tools/blender-patched/blender'), '--threads', '2',
                       '--factory-startup', '--window-geometry', '0', '0', '1600', '1000',
                       '--enable-event-simulate', '--python-exit-code', '1',
                       '--python', str(ROOT / 'spike' / script),
                       '--python', str(ROOT / 'launch/bootstrap.py')]
            (output / 'launch.json').write_text(json.dumps({'command': command, 'native_paths': native.stdout,
                                                           'mutation': args.mutation}, indent=2))
            log_path = output / 'stdout.log'
            with log_path.open('w') as log:
                result = subprocess.run(command, env=env, cwd=ROOT, stdout=log,
                                        stderr=subprocess.STDOUT, timeout=200)
            text = log_path.read_text(errors='replace')
            print(text, flush=True)
            rows = [line[len(marker) + 1:] for line in text.splitlines() if line.startswith(marker + ' ')]
            payload = json.loads(rows[-1]) if rows else None
            async_error = 'Traceback (most recent call last)' in text
            if result.returncode or not payload or payload.get('status') != 'passed' or async_error:
                print('PREVIEW_RUN_FAILED', result.returncode, 'marker=', bool(payload),
                      'traceback=', async_error, 'log=', log_path, flush=True)
                return 1
            print('PREVIEW_RUN_PASSED', args.run, 'log=', log_path, flush=True)
            return 0


if '--run' in sys.argv:
    raise SystemExit(public_run())

START = time.perf_counter()
RESULT = {'status': 'running', 'checks': [], 'cases': [], 'mutation': os.environ.get('PREVIEW_RESP_MUTATION', ''),
          'case_filter': os.environ.get('PREVIEW_RESP_CASE', 'all')}
OUT = Path(os.environ['PREVIEW_RESP_OUTPUT'])
FINISH = threading.Lock()
POSITION = (0, 0)
SENT = []
DRAW_ROWS = []
HANDLING = []
PREP = []
SECTIONS = []
COUNTS = Counter()
ALL_COUNTS = Counter()
OBS_ERRORS = []
LIVE = None
LAST_EVENT = None
CAPTURE = None
PIXELS = {}


def check(ok, label):
    RESULT['checks'].append({'ok': bool(ok), 'label': label})
    print('PREVIEW_RESP', 'PASS' if ok else 'FAIL', label, flush=True)
    if not ok:
        raise AssertionError(label)


def finish(code, error=None):
    if not FINISH.acquire(blocking=False):
        os._exit(1)
    RESULT.update(status='failed' if code else 'passed', error=error,
                  elapsed_s=time.perf_counter() - START, observer_errors=OBS_ERRORS)
    if 'st' in globals():
        RESULT['last_state'] = {'input': st.input_buffer, 'history': st.history[-10:],
                                'undo': st.ctx.undo.undo_names(),
                                'objects': [(str(o.Attributes.Id), o.Attributes.Name) for o in st.model.file.Objects]}
    try:
        RESULT['memory'] = [line for line in Path('/proc/self/status').read_text().splitlines()
                            if line.startswith(('VmHWM:', 'VmRSS:', 'VmSwap:'))]
        (OUT / 'result.json').write_text(json.dumps(RESULT, indent=2, default=str))
        print('PREVIEW_RESPONSIVENESS_RESULT ' + json.dumps(RESULT, default=str), flush=True)
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)


def window():
    return bpy.context.window_manager.windows[0]


def event(kind, value, point=None, text='', **modifiers):
    global POSITION
    if point is not None:
        POSITION = tuple(round(v) for v in point)
    row = {'seq': len(SENT), 'kind': kind, 'value': value, 'xy': POSITION, 'sent': time.perf_counter()}
    SENT.append(row)
    window().event_simulate(type=kind, value=value, x=POSITION[0], y=POSITION[1],
                            **({'unicode': text} if text else {}), **modifiers)
    return row['seq']


def tap(kind, point=None):
    if point is not None:
        event('MOUSEMOVE', 'NOTHING', point)
        yield .1
    event(kind, 'PRESS')
    yield .05
    event(kind, 'RELEASE')
    yield .16


def type_line(text):
    digits = ('ZERO', 'ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX', 'SEVEN', 'EIGHT', 'NINE')
    check(not st.input_buffer, 'typing starts with empty command buffer')
    for char in text:
        key = digits[int(char)] if char.isdigit() else {'=': 'EQUAL', '.': 'PERIOD', '-': 'MINUS'}.get(char, char.upper())
        event(key, 'PRESS', text=char)
        yield .018
        event(key, 'RELEASE')
        yield .018
    check(st.input_buffer == text, 'real keyboard entered ' + text)
    yield from tap('RET')


def undo():
    event('LEFT_CTRL', 'PRESS', ctrl=True)
    yield .04
    event('Z', 'PRESS', ctrl=True)
    yield .04
    event('Z', 'RELEASE', ctrl=True)
    event('LEFT_CTRL', 'RELEASE')
    yield .35


def wait_for(predicate, label, seconds=5):
    until = time.perf_counter() + seconds
    while not predicate():
        if OBS_ERRORS:
            raise AssertionError(OBS_ERRORS)
        if time.perf_counter() > until:
            raise AssertionError('Timed out: ' + label)
        yield .025


def document():
    return {str(obj.Attributes.Id): (obj.Attributes.Name, json.dumps(obj.Geometry.Encode(), sort_keys=True))
            for obj in st.model.file.Objects}


def source_semantics(names):
    return {name: (bridge.curve_recipe(st.ctx.geom_by_name(name)) if isinstance(st.ctx.geom_by_name(name), r3.Curve)
                   else kernel.recipe_of(st.ctx.geom_by_name(name))) for name in names}


def panel(label, center=(0, 0, 2.5), distance=25):
    area = ui._pane_by_label(label)
    region, space = ui._window_region(area), area.spaces.active
    ui.set_active_pane(area)
    space.region_3d.view_location = Vector(center)
    space.region_3d.view_distance = distance
    space.region_3d.update()
    ui._tag_redraw_viewports()
    return area, region, space


def matched_event(x, y):
    return next((row for row in reversed(SENT) if row['kind'] == 'MOUSEMOVE'
                 and row['xy'] == (round(x), round(y))), None)


def image():
    x, y, width, height = gpu.state.viewport_get()
    buf = gpu.state.active_framebuffer_get().read_color(x, y, width, height, 4, 0, 'FLOAT')
    buf.dimensions = width * height * 4
    return np.array(buf, dtype=np.float32).reshape(height, width, 4)


def current_values(kind):
    if kind == 'gumball':
        drag = ui._gumball_drag or {}
        guide = drag.get('ext_preview')
        return (getattr(guide, 'distance', None), drag.get('total'))
    drag = ui.edge_handles._drag
    if not drag:
        return None, None
    _owner, edge, endpoint = drag['id']
    return drag['cmd']._edge_radii[edge][endpoint or 0], drag['pending']


def observe_draw(original, region, call, kind):
    before = None
    current, pending = current_values(kind)
    latest = current == pending
    capture = CAPTURE
    key = region.as_pointer()
    if (capture and capture['kind'] == kind and key == capture['region'] and key not in PIXELS
            and (latest or RESULT['mutation'] in ('latest', 'hidden'))):
        before = image()
    started = time.perf_counter()
    value = call()
    ended = time.perf_counter()
    if LIVE == kind and LAST_EVENT:
        DRAW_ROWS.append({'kind': kind, 'seq': LAST_EVENT['seq'], 'region': key,
                          'event_to_draw_ms': (ended - LAST_EVENT['sent']) * 1000,
                          'draw_submit_ms': (ended - started) * 1000, 'pixel_frame': bool(capture),
                          'current_latest': latest, 'display_value': current, 'pending_value': pending})
    if before is not None:
        PIXELS[key] = (before, image())
    return value


def install_observers():
    def final_pane_pixels():
        region = bpy.context.region
        if (CAPTURE and CAPTURE['kind'] == 'pane' and region is not None
                and CAPTURE['region'] == region.as_pointer() and region.as_pointer() not in PIXELS):
            try:
                PIXELS[region.as_pointer()] = image()
            except Exception:
                OBS_ERRORS.append(traceback.format_exc())
    # Read after the real app's POST_PIXEL callbacks. This observes the composed
    # viewport even when cancellation correctly stops calling Session.draw entirely.
    bpy.types.SpaceView3D.draw_handler_add(final_pane_pixels, (), 'WINDOW', 'POST_PIXEL')
    if RESULT['mutation'] == 'no_cancel':
        ui._cancel_gumball_extrude = lambda: None
    elif RESULT['mutation'] == 'no_invalidation':
        source = inspect.getsource(ui.invalidate_snap_cache)
        old = 'if drag is not None and drag.get("extrude") is not None:'
        if source.count(old) != 1:
            raise RuntimeError('invalidation mutation target changed')
        # Remove only the extrusion invalidation hook, leaving all ordinary cache
        # invalidation intact. Compile in the actual module globals, never on disk.
        exec(compile(source.replace(old, 'if False:'), '<no-invalidation mutation>', 'exec'), ui.__dict__)
    original_update = ui._gumball_update
    def update(x, y, *args, **kwargs):
        global LAST_EVENT
        if LIVE == 'gumball':
            LAST_EVENT = matched_event(x, y)
        start = time.perf_counter()
        result = original_update(x, y, *args, **kwargs)
        if LIVE == 'gumball' and LAST_EVENT:
            if RESULT['mutation'] == 'old_compute':
                ui._extrude_compute(ui._gumball_drag, force=True)
            HANDLING.append({'kind': LIVE, 'seq': LAST_EVENT['seq'], 'ms': (time.perf_counter() - start) * 1000})
        return result
    ui._gumball_update = update
    original_event = ui.edge_handles.handle_event
    def handle_event(ev):
        global LAST_EVENT
        if LIVE == 'fillet' and ev.type == 'MOUSEMOVE':
            LAST_EVENT = matched_event(ev.mouse_x, ev.mouse_y)
            start = time.perf_counter()
            result = original_event(ev)
            if LAST_EVENT:
                HANDLING.append({'kind': LIVE, 'seq': LAST_EVENT['seq'], 'ms': (time.perf_counter() - start) * 1000})
            return result
        return original_event(ev)
    ui.edge_handles.handle_event = handle_event
    original_prepare = ui._prepare_extrude_preview
    def prepare(state):
        start = time.perf_counter()
        value = original_prepare(state)
        PREP.append((time.perf_counter() - start) * 1000)
        return value
    ui._prepare_extrude_preview = prepare
    original_draw = ui.extrude_preview.Session.draw
    def draw(session, region, rv3d, color):
        try:
            call = (lambda: None) if RESULT['mutation'] == 'hidden' else lambda: original_draw(session, region, rv3d, color)
            return observe_draw(original_draw, region, call, 'gumball')
        except Exception:
            OBS_ERRORS.append(traceback.format_exc())
            raise
    ui.extrude_preview.Session.draw = draw
    original_session_update = ui.extrude_preview.Session.update
    def session_update(session, axis, distance):
        return original_session_update(session, axis, distance * .5 if RESULT['mutation'] == 'latest' else distance)
    ui.extrude_preview.Session.update = session_update
    original_batch = ui._line_batch
    def line_batch(shader, aa, coords):
        batch = original_batch(shader, aa, coords)
        if sys._getframe(1).f_code is not ui.draw_command_preview.__code__:
            return batch
        class ObservedBatch:
            def draw(self, draw_shader):
                try:
                    return observe_draw(original_batch, bpy.context.region, lambda: batch.draw(draw_shader), 'fillet')
                except Exception:
                    OBS_ERRORS.append(traceback.format_exc())
                    raise
        return ObservedBatch()
    ui._line_batch = line_batch
    for owner, names, prefix in (
        (ui, ('_extrude_compute',), 'ui'),
        (bridge, ('gumball_extrude', '_proxy_from_occt', '_triangulated_proxy'), 'bridge'),
        (occt, ('from_recipe', 'subobject_topology', 'tessellate', 'tessellate_with_edges',
                'brep_description', '_nurbs_convert', 'bevel_edges'), 'occt'),
        (solid._EdgeBevelCmd, ('_refresh_candidate', '_refresh_sections'), 'fillet')):
        for name in names:
            original = getattr(owner, name)
            def counted(*args, _original=original, _key=prefix + '.' + name, **kwargs):
                ALL_COUNTS[_key] += 1
                if LIVE:
                    COUNTS[(LIVE, _key)] += 1
                if _key == 'bridge.gumball_extrude' and RESULT['mutation'] == 'missing_cap':
                    kwargs['solid'] = False
                started = time.perf_counter()
                value = _original(*args, **kwargs)
                if LIVE == 'fillet' and _key == 'fillet._refresh_sections':
                    SECTIONS.append({'wall_ms': (time.perf_counter() - started) * 1000,
                                     'event_to_section_ms': (time.perf_counter() - LAST_EVENT['sent']) * 1000
                                     if LAST_EVENT else None})
                return value
            setattr(owner, name, counted)


def stats(kind, since):
    handled = [r['ms'] for r in HANDLING if r['kind'] == kind and r['seq'] >= since]
    first_draw = {}
    for row in DRAW_ROWS:
        if row['kind'] == kind and row['seq'] >= since and not row['pixel_frame'] and row['current_latest']:
            key = (row['seq'], row['region'])
            first_draw.setdefault(key, row['event_to_draw_ms'])
    def summarize(values):
        if not values:
            return {'n': 0}
        return {'n': len(values), 'median_ms': statistics.median(values),
                'p95_ms': sorted(values)[math.ceil(.95 * len(values)) - 1], 'max_ms': max(values)}
    return {'handler_first_ms': handled[0] if handled else None, 'handler_warm': summarize(handled[1:]),
            'event_to_first_draw_per_region': summarize(list(first_draw.values())),
            'current_drawn_event_count': len({seq for seq, _region in first_draw}),
            'note': 'event injection to CURRENT latest-value draw-return, NOT photon; pixel probe frames excluded'}


def world_pixel(region, rv3d, point):
    p = ui._project_2d(region, rv3d, point)
    check(p is not None, 'pixel witness projects into the actual pane')
    return tuple(round(v) for v in p)


def witness(before, after, xy, label, changed=True, orange=True):
    x, y = xy
    check(3 <= x < after.shape[1] - 3 and 3 <= y < after.shape[0] - 3, label + ' inside framebuffer')
    patch_after = after[y-2:y+3, x-2:x+3, :3]
    colored = ((patch_after[..., 0] - patch_after[..., 2] > .08) if orange
               else np.ones(patch_after.shape[:2], dtype=bool))
    if changed:
        colored &= np.max(np.abs(patch_after - before[y-2:y+3, x-2:x+3, :3]), axis=2) > .002
    check(bool(colored.any()), 'VISIBLE ' + label)


def capture(region, kind):
    global CAPTURE
    PIXELS.clear()
    CAPTURE = {'region': region.as_pointer(), 'kind': kind}
    ui._tag_redraw_viewports()
    yield from wait_for(lambda: region.as_pointer() in PIXELS, 'actual app framebuffer capture')
    CAPTURE = None
    return PIXELS[region.as_pointer()]


def begin_extrude(area, region, space):
    frame, handles = ui._gumball_handles(area, space, region)
    hit = next(h for h in handles if h.kind == gumball.EXTRUDE_HANDLE and h.axis == 2)
    a, b = hit.hot[0]
    p = ui._project_2d(region, space.region_3d, tuple((a[i] + b[i]) / 2 for i in range(3)))
    point = (region.x + p[0], region.y + p[1])
    check(ui._gumball_hit(area, space, region, *point).key == hit.key, 'actual extrusion-dot hit target')
    event('MOUSEMOVE', 'NOTHING', point)
    yield .1
    event('LEFTMOUSE', 'PRESS')
    yield from wait_for(lambda: ui._gumball_drag is not None, 'real dot press begins drag')
    return ui._gumball_drag


def extrude_point(drag, amount):
    return tuple(drag['mouse0'][i] + (drag['axis_2d'][1][i] - drag['axis_2d'][0][i])
                 * amount / drag['axis_len'] for i in range(2))


def extrusion_case(annulus=False):
    global LIVE, LAST_EVENT
    names = [st.ctx.add_geometry(bridge.make_circle((0, 0, 0), 5), 'PreviewCircle')[1]]
    if annulus:
        names.append(st.ctx.add_geometry(bridge.make_circle((0, 0, 0), 2), 'PreviewHole')[1])
    st.ctx.select_by_names(names)
    ui.set_gumball(True)
    ui.set_gumball_alignment('World')
    area, region, space = panel('Front')
    yield .6
    before, semantics, history = document(), source_semantics(names), st.ctx.undo.undo_names()
    check(ui._extrude_gpu_attempted and ui.extrude_preview._gpu is not None, 'idle UI prewarmed extrusion shader')
    drag = yield from begin_extrude(area, region, space)
    guide = drag['ext_preview']
    polylines = guide.polylines
    yield from wait_for(lambda: guide._batch is not None or RESULT['mutation'] == 'hidden', 'actual first guide draw/upload')
    batch = guide._batch
    before_calls = COUNTS.copy()
    start_seq = len(SENT)
    LIVE, LAST_EVENT = 'gumball', None
    for i in range(30):
        event('MOUSEMOVE', 'NOTHING', extrude_point(drag, 1 + i * .12))
        yield .035
    final_seq = event('MOUSEMOVE', 'NOTHING', extrude_point(drag, 5.1))
    yield from wait_for(lambda: any(r['seq'] == final_seq and r['region'] == region.as_pointer()
                                   and (r['current_latest'] or RESULT['mutation'] in ('latest', 'hidden'))
                                   for r in DRAW_ROWS), 'trailing event reaches draw without another mouse event')
    pixels = yield from capture(region, 'gumball')
    hot_calls = {key[1]: n - before_calls[key] for key, n in COUNTS.items()
                 if key[0] == LIVE and n != before_calls[key]}
    check(not hot_calls, 'held extrusion has ZERO kernel/exact/proxy/mesher calls: ' + str(hot_calls))
    value = drag['total']
    source = st.ctx.geom_by_name(names[0])
    dom = source.Domain
    p = source.PointAt(dom.T0 + .37 * (dom.T1 - dom.T0))
    near = (p.X, p.Y, p.Z)
    delta = np.asarray(drag['axis_dir']) * value
    far = tuple(np.asarray(near) + delta)
    seam = source.PointAtStart
    middle = tuple(np.asarray((seam.X, seam.Y, seam.Z)) + delta * .5)
    for label, point, changed in (('near outline', near, False), ('latest far outline', far, True),
                                   ('sparse connector', middle, True)):
        witness(*pixels, world_pixel(region, space.region_3d, point), label, changed)
    check(guide.distance == value and guide.polylines is polylines and guide._batch is batch,
          'latest distance, prepared outlines and GPU batch reused')
    rendered = {r['region'] for r in DRAW_ROWS if r['kind'] == LIVE and r['seq'] >= start_seq}
    check(len(rendered) == 4, 'actual GPU guide draw submitted in all four viewports')
    check(not drag['ext_cands'] and not drag['ext_ghost'] and drag['ext_d'] is None,
          'no live exact candidates or old per-point ghost')
    check(document() == before and st.ctx.undo.undo_names() == history, 'held drag preserves document and Undo')
    timing = stats(LIVE, start_seq)
    LIVE = None
    shot = OUT / ('annulus-held.png' if annulus else 'circle-held.png')
    check(bpy.ops.screen.screenshot(filepath=str(shot)) == {'FINISHED'}, 'actual held preview screenshot saved')
    builds = ALL_COUNTS['ui._extrude_compute']
    event('LEFTMOUSE', 'RELEASE')
    yield from wait_for(lambda: ui._gumball_drag is None and not st.ctx.undo.recording, 'real release completes exact transaction')
    yield .3
    made = [obj for obj in st.model.file.Objects if str(obj.Attributes.Id) not in before]
    check(len(made) == 1, 'release creates one exact extrusion/region')
    check(ALL_COUNTS['ui._extrude_compute'] == builds + 1, 'release invokes exact computation exactly once')
    geom = made[0].Geometry
    master = kernel.master_of(geom)
    recipe = kernel.recipe_of(geom)
    expected = {'CYLINDER': 2, 'PLANE': 2} if annulus else {'CYLINDER': 1, 'PLANE': 2}
    check(master is not None and occt.is_valid(master) and occt.is_airtight(master)
          and occt.face_types(master) == expected, 'exact release is valid, AIRTIGHT and has both caps')
    volume = math.pi * (25 - (4 if annulus else 0)) * abs(value)
    check(abs(occt.volume(master) - volume) < 1e-6, 'exact release volume follows FINAL distance and holes')
    replay = occt.from_recipe(recipe)
    check(occt.is_airtight(replay) and abs(occt.volume(replay) - volume) < 1e-6, 'release recipe rebuilds coherent exact geometry')
    check(len(st.ctx.undo.undo_names()) == len(history) + 1, 'one release produces one Undo')
    yield from undo()
    check(set(document()) == set(before) and source_semantics(names) == semantics,
          'real Ctrl+Z restores original profiles')
    RESULT['cases'].append({'name': 'annulus' if annulus else 'circle', 'timings': timing,
                            'final_distance': value, 'source_prepare_ms': PREP[-1], 'live_calls': hot_calls})
    return names, area, region, space


def far_witness_point(name, drag):
    curve = st.ctx.geom_by_name(name)
    dom = curve.Domain
    p = curve.PointAt(dom.T0 + .37 * (dom.T1 - dom.T0))
    return tuple(np.array((p.X, p.Y, p.Z)) + np.array(drag['axis_dir']) * drag['total'])


def absent_witness(after, xy, label):
    x, y = xy
    check(3 <= x < after.shape[1] - 3 and 3 <= y < after.shape[0] - 3, label + ' inside framebuffer')
    pixels = after[y-2:y+3, x-2:x+3, :3]
    check(not bool((pixels[..., 0] - pixels[..., 2] > .08).any()), 'VISIBLE ' + label)


def cancel_case(names, area, region, space):
    global LIVE, LAST_EVENT
    ui.set_gumball(True)
    st.ctx.select_by_names(names)
    yield .25
    before, history = document(), st.ctx.undo.undo_names()
    drag = yield from begin_extrude(area, region, space)
    LIVE, LAST_EVENT = 'gumball', None
    event('MOUSEMOVE', 'NOTHING', extrude_point(drag, 3))
    yield .15
    LIVE = None
    held = yield from capture(region, 'pane')
    xy = world_pixel(region, space.region_3d, far_witness_point(names[0], drag))
    witness(held, held, xy, 'held ghost before Esc', changed=False)
    builds = ALL_COUNTS['ui._extrude_compute']
    yield from tap('ESC')
    cleared = yield from capture(region, 'pane')
    absent_witness(cleared, xy, 'canceled ghost removed before release')
    np.savez_compressed(OUT / 'cancel_pixels.npz', held=held, cleared=cleared)
    check(ui._gumball_drag is None and not st.ctx.undo.recording, 'Esc closes drag and empty undo before release')
    event('LEFTMOUSE', 'RELEASE')
    yield .15
    check(ALL_COUNTS['ui._extrude_compute'] == builds, 'Esc and subsequent release invoke no exact builder')
    check(ui._gumball_drag is None and document() == before and st.ctx.undo.undo_names() == history,
          'real Esc restores source/Undo and leaves no held extrusion')


def source_change_case(names, area, region, space):
    ui.set_gumball(True)
    st.ctx.select_by_names(names)
    yield .25
    drag = yield from begin_extrude(area, region, space)
    event('MOUSEMOVE', 'NOTHING', extrude_point(drag, 4))
    yield .15
    held = yield from capture(region, 'pane')
    xy = world_pixel(region, space.region_3d, far_witness_point(names[0], drag))
    witness(held, held, xy, 'held ghost before source change', changed=False)
    builds = ALL_COUNTS['ui._extrude_compute']
    # Intentional external edit of the disposable source, same object ID. The real
    # document-change invalidator must stop the held drag using its previous source.
    obj_id = st.model.object_id_by_name(names[0])
    original_key = ui._doc_key()
    original_doc = document()
    st.model.replace_geometry(obj_id, bridge.make_circle((.25, 0, 0), 5))
    ui.invalidate_snap_cache()
    changed = document()
    RESULT['source_change_stamp'] = {'before': original_key, 'after': ui._doc_key(),
                                     'geometry_changed': changed != original_doc}
    event('MOUSEMOVE', 'NOTHING', extrude_point(drag, 4))
    yield .15
    cleared = yield from capture(region, 'pane')
    absent_witness(cleared, xy, 'invalidated ghost removed before release')
    np.savez_compressed(OUT / 'invalidation_pixels.npz', held=held, cleared=cleared)
    check(drag['ext_preview'] is None and 'source document changed' in drag['ext_why'],
          'source revision invalidates live guide')
    event('LEFTMOUSE', 'RELEASE')
    yield .25
    check(ALL_COUNTS['ui._extrude_compute'] == builds, 'invalidated release invokes no exact builder')
    check(ui._gumball_drag is None and document() == changed, 'stale-source release creates no geometry and preserves external edit')


def fillet_case():
    global LIVE, LAST_EVENT
    ui.set_gumball(False)
    area, region, space = panel('Top', (10, 10, 0), 42)
    _, name = st.ctx.add_geometry(bridge.make_box((0, 0, 0), (20, 20, 0), 10), 'PreviewBox')
    st.ctx.select_by_names([])
    ui._tag_redraw_viewports()
    yield .6
    choices = []
    for index, points in ui._brep_parts_all()[name]['edges']:
        if points and all(abs(p[2] - 10) < 1e-7 for p in points):
            world = tuple((points[0][k] + points[-1][k]) / 2 for k in range(3))
            local = ui._project_2d(region, space.region_3d, world)
            if local is not None and ui.pick_brep_part(area, space, region, *local) == (name, 'Edge', index):
                choices.append((index, (region.x + local[0], region.y + local[1]), world))
    check(len(choices) >= 2, 'two real top-edge pick targets')
    a = choices[0]
    b = max(choices[1:], key=lambda row: math.dist(a[2], row[2]))
    before, history = document(), st.ctx.undo.undo_names()
    st.start_command('FilletEdge')
    yield .15
    yield from type_line('NextRadius=0.5')
    yield from type_line('Preview=Yes')
    yield from tap('LEFTMOUSE', a[1])
    yield from tap('LEFTMOUSE', b[1])
    yield from tap('RIGHTMOUSE', (region.x + region.width * .75, region.y + region.height * .25))
    cmd = st.active_command
    check(cmd is not None and cmd.phase == cmd.EDIT_HANDLES and len(cmd._edge_radii) == 2,
          'two-edge Fillet handle phase reached by real picks')
    rows = [row for row in ui.edge_handles.boxes() if row['cmd'] is cmd and row['id'] == (name, a[0], 0)
            and region.x < (row['grip'][0] + row['grip'][2]) / 2 < region.x + region.width
            and region.y < (row['grip'][1] + row['grip'][3]) / 2 < region.y + region.height]
    check(len(rows) == 1, 'actual drawn Fillet grip in Top')
    row = rows[0]
    point = ((row['grip'][0] + row['grip'][2]) / 2, (row['grip'][1] + row['grip'][3]) / 2)
    event('MOUSEMOVE', 'NOTHING', point)
    yield .1
    event('LEFTMOUSE', 'PRESS')
    yield .1
    check(cmd.handle_input_active() and ui.edge_handles._drag is not None, 'real grip press begins held radius edit')
    original_cache = tuple(getattr(cmd, field) for field in cmd._handle_cache_fields)
    drag = ui.edge_handles._drag
    before_calls = COUNTS.copy()
    LIVE, LAST_EVENT = 'fillet', None
    start_seq = len(SENT)
    for i in range(24):
        # Pixel-sized travel avoids repeatedly asking for subpixel-identical radii
        # (and avoids treating three-pixel click jitter as preview latency).
        travel = 5 + i
        for fraction in (.7, .85, 1):
            event('MOUSEMOVE', 'NOTHING', tuple(drag['start'][j] + row['axis'][j] * travel * fraction
                                              for j in range(2)))
        yield .035
    last = event('MOUSEMOVE', 'NOTHING', tuple(drag['start'][j] + row['axis'][j] * 35 for j in range(2)))
    yield from wait_for(lambda: any(r['kind'] == LIVE and r['seq'] == last and r['current_latest'] for r in DRAW_ROWS), 'trailing Fillet sections actually drawn')
    pixels = yield from capture(region, 'fillet')
    actual = cmd._edge_radii[a[0]][0]
    expected = .5 + sum((POSITION[j] - drag['start'][j]) * row['axis'][j] for j in range(2)) / row['pixels_per_unit']
    check(abs(actual - expected) < 1e-8, 'latest trailing pointer radius consumed before release')
    check(cmd.opt['Preview'] and cmd.preview_geometry() is None and cmd.preview_lines(), 'Preview=Yes retains CURRENT section wires, not stale full candidate')
    current = cmd.preview_lines()[0]
    np.savez_compressed(OUT / 'fillet_pixels.npz', before=pixels[0], after=pixels[1])
    RESULT['fillet_pixel_witness'] = {'color': ui.PREVIEW_COLOR,
                                     'world': current[len(current) // 2]}
    # Fillet section strokes use design.PREVIEW (dark grey in light theme), NOT
    # Gumball's orange. The before/after delta isolates the actual section draw.
    witness(*pixels, world_pixel(region, space.region_3d, current[len(current) // 2]),
            'current Fillet arc', orange=False)
    check(bpy.data.objects[name].visible_get() and document() == before,
          'source box remains visible and document unmodified during Preview=Yes')
    for key in ('fillet._refresh_candidate', 'bridge._proxy_from_occt', 'occt.bevel_edges'):
        check(COUNTS[(LIVE, key)] == before_calls[(LIVE, key)], 'held Fillet has zero ' + key)
    sections = COUNTS[(LIVE, 'fillet._refresh_sections')] - before_calls[(LIVE, 'fillet._refresh_sections')]
    sent_count = sum(r['kind'] == 'MOUSEMOVE' and r['seq'] >= start_seq for r in SENT)
    check(0 < sections < sent_count, 'real burst coalesces section work while preserving latest value')
    timing = stats(LIVE, start_seq)
    LIVE = None
    check(bpy.ops.screen.screenshot(filepath=str(OUT / 'fillet-held.png')) == {'FINISHED'},
          'actual held Fillet screenshot saved')
    builds = ALL_COUNTS['fillet._refresh_candidate']
    event('LEFTMOUSE', 'RELEASE')
    yield .25
    check(ALL_COUNTS['fillet._refresh_candidate'] == builds + 1, 'Fillet release builds exact candidate exactly once')
    check(cmd._candidate is not None and cmd._candidate_key == cmd._state_key() and not cmd.handle_input_active(),
          'Fillet release validates latest candidate without committing command')
    check(document() == before and st.ctx.undo.undo_names() == history, 'Fillet release preserves original and Undo')
    master = kernel.master_of(cmd._candidate)
    check(occt.is_valid(master) and occt.is_airtight(master), 'released variable Fillet candidate is exact and airtight')
    yield from tap('RET')
    check(ALL_COUNTS['fillet._refresh_candidate'] == builds + 1, 'Fillet command acceptance reuses validated final candidate')
    check(st.active_command is None and len(st.ctx.undo.undo_names()) == len(history) + 1, 'later real Enter commits one Fillet Undo')
    yield from undo()
    check(set(document()) == set(before) and st.ctx.geom_by_name(name).IsSolid,
          'real Undo restores original solid identities')
    RESULT['cases'].append({'name': 'two_edge_fillet', 'timings': timing, 'sections': sections,
                            'sent_motion_events': sent_count, 'final_radius': actual,
                                                        'section_timings': list(SECTIONS)})


def run():
    global st
    yield from wait_for(lambda: os.environ.get('NARWHAL3D_UI_READY') == '1' and not ui._viewports_in_progress
                       and ui._viewport_count() == 4 and ui.is_input_active(), 'four panes and real modal input ready', 45)
    st = ui.app_state
    check(st.model.object_count() == 0, 'fresh app document required')
    ui.set_grid_snap(False)
    install_observers()
    case = RESULT['case_filter']
    if case in ('all', 'previews', 'circle', 'source_change', 'cancel'):
        names, area, region, space = yield from extrusion_case()
    if case in ('all', 'previews', 'annulus'):
        yield from extrusion_case(annulus=True)
    if case in ('all', 'previews', 'fillet'):
        yield from fillet_case()
    if case in ('all', 'source_change'):
        yield from source_change_case(names, area, region, space)
    if case in ('all', 'cancel'):
        yield from cancel_case(names, area, region, space)
    check(not OBS_ERRORS and ui._gumball_drag is None and not st.ctx.undo.recording, 'no observer/held-input/transaction debt')


def tick():
    try:
        with bpy.context.temp_override(window=window()):
            return next(STEPS)
    except StopIteration:
        finish(0)
    except BaseException:
        finish(1, traceback.format_exc())


threading.Thread(target=lambda: (time.sleep(180), finish(1, '180s watchdog')), daemon=True).start()
try:
    settings = Path(os.environ['NARWHAL3D_SETTINGS_FILE'])
    check(settings.is_absolute() and settings.is_relative_to(Path(tempfile.gettempdir()))
          and not settings.exists() and not settings.is_symlink(), 'fresh disposable settings before bootstrap')
    check('narwhal3d' not in sys.modules and not os.environ.get('NARWHAL3D_UI_READY')
          and not os.environ.get('NARWHAL3D_THEME') and not os.environ.get('NARWHAL3D_WELCOME_REQUEST')
          and os.environ.get('NARWHAL3D_DEFER_FIRST_PRESENT') == '1', 'isolated pre-bootstrap flags')
    cgroup = Path('/sys/fs/cgroup') / Path('/proc/self/cgroup').read_text().strip().split('::', 1)[1].lstrip('/')
    check((cgroup / 'memory.max').read_text().strip() == str(3 * 1024**3)
          and (cgroup / 'memory.swap.max').read_text().strip() == '0', '3 GiB / zero-swap cgroup enforced')
    RESULT['source_sha256'] = {p: hashlib.sha256((ROOT / 'narwhal3d' / p).read_bytes()).hexdigest()
                               for p in ('ui.py', 'edge_handles.py', 'solid_commands.py', 'extrude_preview.py')}
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    import bpy
    import gpu
    import numpy as np
    import rhino3dm as r3
    from mathutils import Vector
    from narwhal3d import ui, bridge, kernel, gumball, solid_commands as solid
    from narwhal3d.kernel import occt_backend as occt
    check(not bpy.app.background and occt.available(), 'real window and exact kernel required')
    STEPS = run()
    bpy.app.timers.register(tick, first_interval=.1)
except BaseException:
    finish(1, traceback.format_exc())
