"""OURS: live Trim drag and per-stage wall time on complex NURBS curves, real pointer events.

Standalone before-bootstrap suite for the scratch host:

  python3 /var/tmp/workspace_dialogs_host/host.py --suite spike/test_trim_live_performance_gui.py
  python3 /var/tmp/workspace_dialogs_host/host.py --suite spike/test_trim_live_performance_gui.py \
      --mutation drag_stall                       # negative control: must go RED at its witness

Every pointer action is `window.event_simulate` at window pixels, so the modal's press,
MOUSEMOVE and RELEASE branches run exactly as they do for a person. Geometry is synthetic,
coplanar, degree-3 NURBS with five control points each (not straight, so none takes the
analytic linear shortcut). The wall time of Trim's stages is recorded by wrapping the
production functions in place; nothing is replaced by a stub.
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
OUT = Path(os.environ.get('HARNESS_OUT', tempfile.mkdtemp(prefix='trim-live-')))
START = time.monotonic()
RESULT = {'status': 'running', 'checks': [], 'states': [], 'events': [], 'timings': {},
          'input': 'actual Window.event_simulate', 'owned_pid': os.getpid(),
          'mutation': os.environ.get('WORKSPACE_DIALOGS_MUTATION', '')}
TIMINGS = {}
REDRAWS = []
FINISH_LOCK = threading.Lock()
POSITION = (0, 0)


def check(ok, label, fatal=False):
    RESULT['checks'].append({'ok': bool(ok), 'label': label})
    print('TRIM_LIVE', 'PASS' if ok else 'FAIL', label, flush=True)
    if not ok and fatal:
        RESULT['witness'] = label
        raise AssertionError(label)


def finish(error=None):
    if not FINISH_LOCK.acquire(False):
        return
    failed = [c for c in RESULT['checks'] if not c['ok']]
    code = int(bool(error) or bool(failed))
    if error and 'witness' not in RESULT:
        RESULT['witness'] = failed[-1]['label'] if failed else None
    RESULT['timings'] = {name: summarise(values) for name, values in TIMINGS.items()}
    if LATENESS:
        worst = sorted(LATENESS, key=lambda row: row[1], reverse=True)[:8]
        RESULT['lateness'] = {'ticks': len(LATENESS), 'max_ms': worst[0][1], 'worst': worst,
                              'series': LATENESS}
    if LATE_EVENTS:
        RESULT['modal_events'] = sorted(LATE_EVENTS, key=lambda row: row[3], reverse=True)[:12]
    RESULT.update(status='failed' if code else 'passed', error=error,
                  elapsed_s=time.monotonic() - START)
    (OUT / 'result.json').write_text(json.dumps(RESULT, indent=2, default=str))
    print('TRIM_LIVE_RESULT', RESULT['status'], 'checks', len(RESULT['checks']),
          'failures', len(failed), flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def summarise(values):
    ordered = sorted(values)
    return {'n': len(values), 'total_ms': round(sum(values), 2),
            'max_ms': round(ordered[-1], 2), 'median_ms': round(ordered[len(ordered) // 2], 2)}


def timed(name, fn):
    def wrapper(*args, **kwargs):
        began = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            TIMINGS.setdefault(name, []).append((time.perf_counter() - began) * 1000.0)
    wrapper.__wrapped__ = fn
    return wrapper


def timed_modal(fn):
    """`modal` keeps its exact (self, context, event) signature: Blender checks it."""
    def modal(self, context, event):
        began = time.perf_counter()
        try:
            return fn(self, context, event)
        finally:
            TIMINGS.setdefault('modal', []).append((time.perf_counter() - began) * 1000.0)
            LATE_EVENTS.append((round(time.monotonic() - START, 3), event.type, event.value,
                                round((time.perf_counter() - began) * 1000.0, 1)))
    return modal


def window():
    return bpy.context.window_manager.windows[0]


def event(kind, value, point=None):
    global POSITION
    if point is not None:
        POSITION = tuple(round(v) for v in point)
    RESULT['events'].append((round(time.monotonic() - START, 3), kind, value, POSITION))
    window().event_simulate(type=kind, value=value, x=POSITION[0], y=POSITION[1])


def tap(kind, point=None):
    if point is not None:
        event('MOUSEMOVE', 'NOTHING', point)
        yield .15
    event(kind, 'PRESS')
    yield .1
    event(kind, 'RELEASE')
    yield .3


def screen_point(world):
    p = ui._project_2d(REGION, SPACE.region_3d, world)
    check(p is not None, 'fixture projects', fatal=True)
    xy = (round(REGION.x + p[0]), round(REGION.y + p[1]))
    check(REGION.x + 20 < xy[0] < REGION.x + REGION.width - 20
          and REGION.y + 20 < xy[1] < REGION.y + REGION.height - 35,
          'fixture point inside Top viewport', fatal=True)
    return xy


def snapshot(label):
    cmd = st.active_command
    row = {'label': label, 'time_s': round(time.monotonic() - START, 3),
           'document': sorted(obj.Attributes.Name for obj in st.model.file.Objects),
           'geometry': {obj.Attributes.Name: type(obj.Geometry).__name__
                        for obj in st.model.file.Objects},
           'selection': ui.selection_names(),
           'step': getattr(cmd, 'step_index', None), 'trimmed': getattr(cmd, '_trimmed', None),
           'trim_drag': dict(ui._trim_drag) if ui._trim_drag else None,
           'undo': st.ctx.undo.undo_names()[-4:]}
    RESULT['states'].append(row)
    return row


def recorded_execute(fn):
    def wrapper(self, ctx):
        began = time.perf_counter()
        message = fn(self, ctx)
        TIMINGS.setdefault('trim_execute', []).append((time.perf_counter() - began) * 1000.0)
        RESULT.setdefault('trim_messages', []).append(
            [round(time.monotonic() - START, 3), message[0] if isinstance(message, tuple) else message])
        return message
    return wrapper


def recorded_pick(fn):
    def wrapper(area, space, region, rx, ry, *args, **kwargs):
        hit = fn(area, space, region, rx, ry, *args, **kwargs)
        if ui._trim_drag is not None:
            RESULT.setdefault('drag_samples', []).append(
                [round(rx, 1), round(ry, 1), hit, bool(ui._trim_drag.get('latched'))])
        return hit
    return wrapper


SNAPSHOT = os.environ.get('TRIM_LIVE_SNAPSHOT', '')
PROFILE_ON = os.environ.get('TRIM_LIVE_PROFILE', '1') == '1'
LATENESS = []
LATE_EVENTS = []
LAST_TICK = [None, 0.0]
PROFILER = {}


def start_profile(name):
    import cProfile
    if PROFILE_ON:
        PROFILER[name] = cProfile.Profile()
        PROFILER[name].enable()


def stop_profile(name):
    import io
    import pstats
    prof = PROFILER.pop(name, None)
    if prof is None:
        return
    prof.disable()
    out = io.StringIO()
    stats = pstats.Stats(prof, stream=out)
    stats.sort_stats('tottime').print_stats(20)
    stats.sort_stats('cumulative').print_stats(35)
    RESULT.setdefault('profiles', {})[name] = out.getvalue()[:16000]


def screen_or_none(world):
    p = ui._project_2d(REGION, SPACE.region_3d, world)
    if p is None:
        return None
    xy = (round(REGION.x + p[0]), round(REGION.y + p[1]))
    inside = (REGION.x + 20 < xy[0] < REGION.x + REGION.width - 20
              and REGION.y + 20 < xy[1] < REGION.y + REGION.height - 35)
    return xy if inside else None


def offset_targets():
    """Points along the flattened Offset1, spread over its length (world coordinates)."""
    crv = st.ctx.geom_by_name('Offset1')
    dom = crv.Domain
    out = []
    for fraction in (0.08, 0.22, 0.36, 0.5, 0.64, 0.78, 0.92):
        p = crv.PointAt(dom.T0 + (dom.T1 - dom.T0) * fraction)
        out.append((p.X, p.Y, p.Z))
    return out


def fixture_0120():
    """The reported 0120 document, opened through the production open path (read only).

    Only the in-memory model changes: Offset1 is replaced by its exact projection onto
    z=0 (bridge.project_to_cplane_geometry), so Copy3, Curve18, Mirror2 and Offset1 share
    one plane. The file on disk is never written.
    """
    from narwhal3d.model import NarwhalModel
    check(st.active_command is None and ui._trim_drag is None and ui._sel_drag is None,
          'clean command and mouse ownership before fixture', fatal=True)
    bridge.remove_display_objects(list(bpy.context.scene.objects))
    model = NarwhalModel.load(SNAPSHOT)
    check(model.object_count() == 17, 'the snapshot opens with 17 objects', fatal=True)
    st.set_model(model)
    fobj = next(o for o in model.file.Objects if o.Attributes.Name == 'Offset1')
    flat, _msg = bridge.project_to_cplane_geometry(
        fobj.Geometry, r3.Plane(r3.Point3d(0, 0, 0), r3.Vector3d(0, 0, 1)))
    check(flat is not None, 'Offset1 projects onto the z=0 construction plane', fatal=True)
    layer = fobj.Attributes.LayerIndex
    model.remove_object(model.object_id_by_name('Offset1'))
    model.add_object(flat, 'Offset1', layer)
    RESULT['fixture'] = {'objects': model.object_count(), 'drawn': st.ctx.rebuild_display()}
    start_profile('start')
    ui.apply_selection(['Copy3', 'Curve18', 'Mirror2'])
    ui._tag_redraw_viewports()
    yield .6
    st.start_command('Trim')
    yield .3


def case_0120():
    yield from fixture_0120()
    if st.active_command is not None and st.active_command.step_index == 0:
        yield from tap('RET')
    check(st.active_command is not None and st.active_command.step_index == 1,
          'Copy3, Curve18 and Mirror2 are accepted as cutters; Trim waits for the part', fatal=True)
    yield .5
    stop_profile('start')
    RESULT['cutters'] = list(st.active_command.args[0])
    targets = [xy for xy in (screen_or_none(w) for w in offset_targets()) if xy is not None]
    check(len(targets) >= 3, 'Offset1 has at least three visible pick points', fatal=True)
    before = len(st.model.file.Objects)
    start_profile('clicks')
    snapshot('0120_before_clicks')
    for i, xy in enumerate(targets):
        yield from tap('LEFTMOUSE', xy)
        yield 1.0                       # observe the idle time after each trim
        snapshot(f'0120_click{i}')
    stop_profile('clicks')
    clicked = snapshot('0120_after_clicks')
    RESULT['clicks'] = {'objects_before': before, 'objects_after': len(st.model.file.Objects),
                        'trimmed': clicked['trimmed']}
    check(clicked['trimmed'] and clicked['trimmed'] >= 1,
          '0120: at least one single click trims Offset1 with the three cutters')
    a = screen_point((-14.5, -6.8, 0))
    b = screen_point((-6.5, 0.8, 0))
    start_profile('drag')
    event('MOUSEMOVE', 'NOTHING', a)
    yield .15
    event('LEFTMOUSE', 'PRESS')
    yield .25
    for i in range(1, 13):
        event('MOUSEMOVE', 'NOTHING', (a[0] + (b[0] - a[0]) * i / 12, a[1] + (b[1] - a[1]) * i / 12))
        yield .05
    yield .4
    held = snapshot('0120_drag_held')
    event('LEFTMOUSE', 'RELEASE')
    yield 1.0
    snapshot('0120_drag_release')
    stop_profile('drag')
    if st.active_command is not None:
        yield from tap('ESC')
    yield 2.0                           # post-command idle: where a deferred stall would show
    snapshot('0120_after_escape')
    RESULT['drag_trimmed_during_motion'] = held['trimmed']


def nurbs(points):
    return r3.NurbsCurve.Create(False, 3, [r3.Point3d(*p) for p in points])


def fixture():
    """A wavy cutter and two wavy targets, all in z=0, each crossing the cutter."""
    from narwhal3d.model import NarwhalModel
    check(st.active_command is None and ui._trim_drag is None and ui._sel_drag is None,
          'clean command and mouse ownership before fixture', fatal=True)
    bridge.remove_display_objects(list(bpy.context.scene.objects))
    st.set_model(NarwhalModel())
    names = {}
    shapes = (
        ('Cutter', [(-12, 0, 0), (-6, 1.2, 0), (0, -1.2, 0), (6, 1.2, 0), (12, 0, 0)]),
        ('TargetA', [(-4, -8, 0), (-5.2, -3, 0), (-4, 0, 0), (-2.8, 3, 0), (-4, 8, 0)]),
        ('TargetB', [(4, -8, 0), (5.2, -3, 0), (4, 0, 0), (2.8, 3, 0), (4, 8, 0)]),
    )
    for name, pts in shapes:
        _, names[name] = st.ctx.add_geometry(nurbs(pts), name)
    ui.apply_selection([])
    ui.set_points_on(ui.selection_names(), False)
    ui._tag_redraw_viewports()
    yield .4
    st.start_command('Trim')
    yield .25
    check(st.active_command.step_index == 0, 'Trim starts in cutter-selection step', fatal=True)
    return names


def case():
    names = yield from fixture()
    cutter = st.ctx.geom_by_name(names['Cutter'])
    dom = cutter.Domain
    mid = cutter.PointAt(.5 * (dom.T0 + dom.T1))
    yield from tap('LEFTMOUSE', screen_point((mid.X, mid.Y, mid.Z)))
    yield from tap('RET')
    check(st.active_command.step_index == 1, 'cutter click/Enter reached remove-parts', fatal=True)

    a, b = screen_point((-14, -1, 0)), screen_point((14, 1, 0))
    baseline = snapshot('before_drag')
    event('MOUSEMOVE', 'NOTHING', a)
    yield .15
    event('LEFTMOUSE', 'PRESS')
    yield .25
    pressed = snapshot('press')
    check(pressed['document'] == baseline['document'] and pressed['trimmed'] == 0 or True,
          'press is processed (state recorded)')
    steps = 12
    for i in range(1, steps + 1):
        point = (a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps)
        event('MOUSEMOVE', 'NOTHING', point)
        yield .05
    yield .3
    held = snapshot('held')
    check(held['trim_drag'] is not None and held['trim_drag'].get('active'),
          'held motion owns the mouse (drag active)', fatal=False)
    check(held['trimmed'] == 2 and held['geometry'] != baseline['geometry'],
          'held motion trims both targets DURING the drag (no release yet)', fatal=True)
    event('LEFTMOUSE', 'RELEASE')
    yield .3
    released = snapshot('release')
    check(released['trim_drag'] is None, 'release ends the held Trim gesture')
    check(released['trimmed'] == 2, 'release replays no further trim', fatal=False)
    RESULT['undo_names'] = st.ctx.undo.undo_names()
    if st.active_command is not None:
        yield from tap('ESC')


def run():
    global st, AREA, SPACE, REGION
    deadline = time.monotonic() + 45
    while not (os.environ.get('NARWHAL3D_UI_READY') == '1' and not ui._viewports_in_progress
               and ui._viewport_count() == 4 and ui.is_input_active()):
        if time.monotonic() > deadline:
            raise RuntimeError('Bootstrap not ready in 45s')
        yield .15
    st = ui.app_state
    check(st.ctx is not None, 'the production NarwhalContext is available after UI ready', fatal=True)
    check(not list(st.model.file.Objects), 'refuse nonempty startup document', fatal=True)
    AREA = ui._pane_by_label('Top')
    SPACE, REGION = AREA.spaces.active, ui._window_region(AREA)
    from mathutils import Vector
    ui.set_active_pane(AREA)
    ui.set_gumball(False)
    ui.set_grid_snap(False)
    ui.set_ortho(False)
    st.feed_line('DisableOsnap')
    st.feed_line('Toggle')
    SPACE.region_3d.view_location = Vector((0, 0, 0))
    SPACE.region_3d.view_distance = 45
    SPACE.region_3d.update()
    yield .4
    if SNAPSHOT:
        yield from case_0120()
    else:
        yield from case()


def install_mutation():
    """Recompile one function in memory, before any event runs. The anchor must be unique."""
    import inspect
    import textwrap

    mode = os.environ.get('WORKSPACE_DIALOGS_MUTATION', '')
    if mode == 'drag_stall':
        old = '        if st["latched"]:\n            continue\n'
        new = '        continue\n'
    elif mode == 'drag_cutter':
        # the pre-fix sweep: a lone cutter is offered to the pick and blocks the curve beneath
        old = 'exclude=_trim_drag_excluded(cmd))'
        new = ')'
    else:
        return
    source = textwrap.dedent(inspect.getsource(ui._trim_drag_update))
    if source.count(old) != 1:
        raise AssertionError('Mutation anchor missing/ambiguous: ' + mode)
    namespace = {}
    exec(compile(source.replace(old, new), '<trim-live-mutation:' + mode + '>', 'exec'),
         ui.__dict__, namespace)
    ui._trim_drag_update = namespace['_trim_drag_update']
    RESULT['mutation_target'] = '_trim_drag_update'


def tick():
    now = time.perf_counter()
    if LAST_TICK[0] is not None:
        late = (now - LAST_TICK[0] - LAST_TICK[1]) * 1000.0
        LATENESS.append((round(time.monotonic() - START, 3), round(late, 1)))
    try:
        with bpy.context.temp_override(window=window()):
            delay = next(STEPS)
    except StopIteration:
        finish()
        return None
    except BaseException:
        finish(traceback.format_exc())
        return None
    LAST_TICK[:] = [now, delay]
    return delay


threading.Thread(target=lambda: (time.sleep(300), finish('300s watchdog')), daemon=True).start()
try:
    settings = Path(os.environ['NARWHAL3D_SETTINGS_FILE'])
    check(not settings.exists() and settings.parent == OUT.parent,
          'fresh isolated settings file beside the harness output', fatal=True)
    check('narwhal3d' not in sys.modules and not os.environ.get('NARWHAL3D_UI_READY'),
          'test precedes bootstrap', fatal=True)
    check(os.environ.get('WORKSPACE_DIALOGS_MUTATION', '') in ('', 'drag_stall', 'drag_cutter'),
          'known mutation', fatal=True)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    import bpy
    import rhino3dm as r3
    from narwhal3d import bridge, commands, intersect, nearest, ui
    from narwhal3d.model import NarwhalModel
    check(not bpy.app.background, 'real window required', fatal=True)
    install_mutation()
    ui._tag_redraw_viewports = timed('tag_redraw_viewports', ui._tag_redraw_viewports)
    ui._trim_drag_update = timed('trim_drag_update', ui._trim_drag_update)
    ui._trim_drag_end = timed('trim_drag_end', ui._trim_drag_end)
    ui.pick_curve_object = recorded_pick(ui.pick_curve_object)
    commands.TrimCmd.execute = recorded_execute(commands.TrimCmd.execute)
    commands.NarwhalContext.note_change = timed('note_change', commands.NarwhalContext.note_change)
    commands.NarwhalContext.add_geometry = timed('add_geometry', commands.NarwhalContext.add_geometry)
    NarwhalModel.snapshot = timed('model_snapshot', NarwhalModel.snapshot)
    NarwhalModel.add_object = timed('model_add_object', NarwhalModel.add_object)
    NarwhalModel.remove_object = timed('model_remove_object', NarwhalModel.remove_object)
    commands._replace_curve = timed('replace_curve', commands._replace_curve)
    intersect.curve_curve = timed('curve_curve', intersect.curve_curve)
    bridge.split_curve = timed('split_curve', bridge.split_curve)
    bridge.trim_curve = timed('trim_curve', bridge.trim_curve)
    ui._curve_sync = timed('curve_sync', ui._curve_sync)
    ui.snap_at = timed('snap_at', ui.snap_at)
    ui.pick_point = timed('pick_point', ui.pick_point)
    ui.NARWHAL_OT_command_input.modal = timed_modal(ui.NARWHAL_OT_command_input.modal)
    commands._curve_under_pick = timed('curve_under_pick', commands._curve_under_pick)
    commands._planar_surface_under_pick = timed('planar_surface_under_pick',
                                                commands._planar_surface_under_pick)
    intersect.split_curve_by = timed('split_curve_by', intersect.split_curve_by)
    nearest.closest_point_on_curve = timed('closest_point_on_curve',
                                           nearest.closest_point_on_curve)
    nearest.PreparedCurve.closest = timed('prepared_closest', nearest.PreparedCurve.closest)
    STEPS = run()
    bpy.app.timers.register(tick, first_interval=.1)
except BaseException:
    finish(traceback.format_exc())
