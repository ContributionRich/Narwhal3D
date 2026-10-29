"""OURS: actual Trim held-box feedback research/regression; Python requires bpy.

Host: python3 -B spike/test_trim_box_gui.py --launch --baseline
Uses one disposable factory GUI for all cases, /tmp/narwhal3d_gui.lock,
MemoryMax=3G, MemorySwapMax=0, 150s timeout, isolated preferences/HOME.
No production edits, builds, reference-application/VM input, or other-process termination.

The expected overlay is owner-requested, NOT a claim of measured reference-application Trim
semantics. Existing motion-time Trim semantics must remain unchanged. A failed
visual check does not stop later cases. Actual GPU submissions are forwarded and
observed, not substituted; saved screen pixels independently check all four edges.
Optional TRIM_BOX_MUTATION=hide|style|fallback|empty_redraw|escape_reset installs
one process-local negative control. --baseline explicitly clears that override.
Reports/screenshots are kept in the printed unique /tmp directory, not shared artifacts.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def launch():
    import fcntl
    import subprocess

    out = Path(tempfile.mkdtemp(prefix='trim-box-gui-'))
    print('TRIM_BOX_OUTPUT', out, flush=True)
    env = os.environ.copy()
    if '--baseline' in sys.argv:
        env.pop('TRIM_BOX_MUTATION', None)
    for key in ('NARWHAL3D_UI_READY', 'NARWHAL3D_THEME', 'NARWHAL3D_WELCOME_REQUEST',
                'NARWHAL3D_PRESENT_WATCHDOG'):
        env.pop(key, None)
    # Query normal launcher resolution without launching/building anything.
    paths = subprocess.check_output(
        ['bash', str(ROOT / 'narwhal3d.sh')], cwd=ROOT,
        env=dict(env, NARWHAL3D_LAUNCHER_TEST='native-paths'), text=True, timeout=10)
    for row in paths.splitlines():
        key, value = row.split('=', 1)
        if value:
            env[key] = value
    for key, subdir in (('HOME', 'home'), ('XDG_CONFIG_HOME', 'config'),
                        ('XDG_CACHE_HOME', 'cache'), ('XDG_DATA_HOME', 'data')):
        folder = out / subdir
        folder.mkdir()
        env[key] = str(folder)
    env.update(TRIM_BOX_OUTPUT=str(out), PYTHONDONTWRITEBYTECODE='1',
               NARWHAL3D_SETTINGS_FILE=str(out / 'preferences.json'),
               NARWHAL3D_NO_DIALOGS='1', NARWHAL3D_DEFER_FIRST_PRESENT='1',
               NARWHAL3D_STATUSBAR_HEIGHT='37', PYTHONPATH=str(ROOT / 'vendor/occt'))
    with open('/tmp/narwhal3d_gui.lock', 'a') as lock:
        deadline = time.monotonic() + 60
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise TimeoutError('Shared GUI lock busy for 60s; no GUI launched')
                time.sleep(.25)
        cmd = ['systemd-run', '--user', '--scope', '--quiet', '-p', 'MemoryMax=3G',
               '-p', 'MemorySwapMax=0', 'timeout', '--signal=TERM', '--kill-after=5s', '150s',
               str(ROOT / 'tools/blender-patched/blender'), '--factory-startup',
               '--enable-event-simulate', '--python-exit-code', '1',
               '--python', str(Path(__file__).resolve()),
               '--python', str(ROOT / 'launch/bootstrap.py')]
        with (out / 'run.log').open('w') as log:
            result = subprocess.run(cmd, cwd=ROOT, env=env, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=165)
        print((out / 'run.log').read_text(), flush=True)
        report = out / 'result.json'
        if not report.exists():
            raise RuntimeError('GUI exited without a result: ' + str(result.returncode))
        data = json.loads(report.read_text())
        log_text = (out / 'run.log').read_text()
        bad_draw = 'point marker draw failed' in log_text or 'Traceback (most recent call last)' in log_text
        print('TRIM_BOX_COMPLETED', out, 'exit', result.returncode, flush=True)
        return result.returncode or int(data['status'] != 'passed' or bad_draw)


if '--launch' in sys.argv:
    raise SystemExit(launch())

import threading
import traceback

START = time.monotonic()
OUT = Path(os.environ['TRIM_BOX_OUTPUT'])
RESULT = {'status': 'running', 'checks': [], 'states': [], 'events': [],
          'input': 'actual Window.event_simulate', 'owned_pid': os.getpid(),
          'mutation': os.environ.get('TRIM_BOX_MUTATION', '')}
POSITION = (0, 0)
PAINT = []
REDRAWS = []
FINISH_LOCK = threading.Lock()


def check(ok, label, fatal=False):
    RESULT['checks'].append({'ok': bool(ok), 'label': label})
    print('TRIM_BOX', 'PASS' if ok else 'FAIL', label, flush=True)
    if fatal and not ok:
        raise AssertionError(label)


def finish(error=None):
    if not FINISH_LOCK.acquire(False):
        return
    code = int(bool(error) or any(not row['ok'] for row in RESULT['checks']))
    RESULT.update(status='failed' if code else 'passed', error=error,
                  elapsed_s=time.monotonic() - START)
    (OUT / 'result.json').write_text(json.dumps(RESULT, indent=2, default=str))
    print('TRIM_BOX_RESULT', RESULT['status'], 'checks', len(RESULT['checks']),
          'failures', sum(not r['ok'] for r in RESULT['checks']), flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)  # An exception in a Blender timer alone does not fail the process.


def window():
    return bpy.context.window_manager.windows[0]


def event(kind, value, point=None):
    global POSITION
    if point is not None:
        POSITION = tuple(round(v) for v in point)
    RESULT['events'].append((time.monotonic() - START, kind, value, POSITION))
    window().event_simulate(type=kind, value=value, x=POSITION[0], y=POSITION[1])


def tap(kind, point=None):
    if point is not None:
        event('MOUSEMOVE', 'NOTHING', point)
        yield .15
    event(kind, 'PRESS')
    yield .1
    event(kind, 'RELEASE')
    yield .3


class ShaderObserver:
    def __init__(self, shader):
        self.shader = shader
        self.color = None

    def __getattr__(self, name):
        return getattr(self.shader, name)

    def uniform_float(self, name, value):
        if name == 'color':
            self.color = tuple(value)
        return self.shader.uniform_float(name, value)


def observed_shader():
    shader = ORIGINAL_SHADER()
    if shader is not None and sys._getframe(1).f_code is ui.draw_point_markers.__code__:
        return ShaderObserver(shader)
    return shader


class BatchObserver:
    def __init__(self, batch, kind, content):
        self.batch, self.kind = batch, kind
        self.vertices = [tuple(p) for p in content.get('pos', ())]

    def __getattr__(self, name):
        return getattr(self.batch, name)

    def draw(self, shader):
        frame = sys._getframe(1)
        is_rect = (frame.f_code is ui.draw_point_markers.__code__
                   and frame.f_locals.get('drag') is not None
                   and frame.f_locals.get('corners') is not None
                   and isinstance(shader, ShaderObserver))
        mode = os.environ.get('TRIM_BOX_MUTATION', '')
        if is_rect and mode == 'hide':
            return None
        if is_rect and mode == 'style':
            shader.uniform_float('color', (1.0, 0.0, 1.0, 1.0))
        result = self.batch.draw(shader.shader if isinstance(shader, ShaderObserver) else shader)
        if is_rect:
            PAINT.append({'kind': self.kind, 'vertices': self.vertices,
                          'color': shader.color, 'line_width': gpu.state.line_width_get(),
                          'blend': gpu.state.blend_get(), 'pane': ui._pane_key(bpy.context.area)})
        return result


def observed_batch(shader, kind, content, **kwargs):
    real = shader.shader if isinstance(shader, ShaderObserver) else shader
    return BatchObserver(ORIGINAL_BATCH(real, kind, content, **kwargs), kind, content)


def document():
    return {obj.Attributes.Name: hashlib.sha256(json.dumps(obj.Geometry.Encode(), sort_keys=True).encode()).hexdigest()
            for obj in st.model.file.Objects}


def snapshot(label):
    cmd = st.active_command
    row = {'label': label, 'time_s': time.monotonic() - START,
           'document': document(), 'selection': ui.selection_names(),
           'step': getattr(cmd, 'step_index', None), 'trimmed': getattr(cmd, '_trimmed', None),
           'trim_drag': dict(ui._trim_drag) if ui._trim_drag else None,
           'sel_drag': dict(ui._sel_drag) if ui._sel_drag else None,
           'undo': st.ctx.undo.undo_names(), 'history': st.history[-3:]}
    RESULT['states'].append(row)
    return row


def screen_point(world):
    p = ui._project_2d(REGION, SPACE.region_3d, world)
    check(p is not None, 'fixture projects', fatal=True)
    xy = (round(REGION.x + p[0]), round(REGION.y + p[1]))
    check(REGION.x + 20 < xy[0] < REGION.x + REGION.width - 20
          and REGION.y + 20 < xy[1] < REGION.y + REGION.height - 35,
          'fixture point inside Top viewport', fatal=True)
    return xy


def screenshot_edges(label, a, b, color):
    path = OUT / (label + '.png')
    check(bpy.ops.screen.screenshot(filepath=str(path)) == {'FINISHED'}, label + ': real screenshot saved', fatal=True)
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        image.colorspace_settings.name = 'Non-Color'
        width, height = image.size
        pixels = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
        pixels = pixels.reshape(height, width, 4)
        x0, x1 = sorted((a[0], b[0]))
        y0, y1 = sorted((a[1], b[1]))
        edges = []
        # Samples avoid corners and use a 5px normal band to allow raster alignment.
        for vertical, fixed, lo, hi in ((True, x0, y0, y1), (True, x1, y0, y1),
                                         (False, y0, x0, x1), (False, y1, x0, x1)):
            hits = []
            for varying in np.linspace(lo + 5, hi - 5, 35).astype(int):
                coords = [(fixed + d, varying) if vertical else (varying, fixed + d) for d in range(-2, 3)]
                hits.append(any(np.max(np.abs(pixels[y, x, :3] - color[:3])) < .10 for x, y in coords))
            edges.append(sum(hits) / len(hits))
        return edges
    finally:
        bpy.data.images.remove(image)


def visual(label, a, b, direction, visible):
    # Observe natural event redraws first, then request a fresh frame for cleanup.
    if not visible:
        PAINT.clear()
        ui._tag_redraw_viewports()
        yield .3
    paint = list(PAINT)
    color = ui.WINDOW_RECT_COLOR if direction == 'ltr' else ui.CROSSING_RECT_COLOR
    fractions = screenshot_edges(label, a, b, color)
    RESULT['states'].append({'label': label, 'gpu': paint, 'edge_pixel_fractions': fractions,
                             'event_redraws': list(REDRAWS)})
    if not visible:
        check(not paint, label + ': no rectangle GPU draw after cleanup')
        check(not all(f > .65 for f in fractions), label + ': screen rectangle removed')
        return
    x0, x1 = sorted((a[0] - REGION.x, b[0] - REGION.x))
    y0, y1 = sorted((a[1] - REGION.y, b[1] - REGION.y))
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    lines = [p for i in range(4) for p in (corners[i], corners[(i + 1) % 4])]
    tris = [corners[i] for i in (0, 1, 2, 0, 2, 3)]
    def submitted(kind, vertices, rgba):
        return any(r['kind'] == kind and r['vertices'] == vertices and r['pane'] == ui._pane_key(AREA)
                   and np.allclose(r['color'], rgba) and r['blend'] == 'ALPHA'
                   and (kind != 'LINES' or abs(r['line_width'] - ui.SELECTION_RECT_STROKE_PX) < .01)
                   for r in paint)
    # Blender can incidentally repaint on cursor motion even without our redraw tag.
    # Keep the visual oracle AND require this gesture to schedule its own repaint.
    scheduled = not label.endswith('_empty_motion') or any(tuple(r['position']) == b for r in REDRAWS)
    check(submitted('LINES', lines, color) and scheduled,
          label + ': exact four-edge GPU stroke with correct directional style and event-owned redraw')
    check(submitted('TRIS', tris, (*color[:3], ui.SELECTION_RECT_FILL_ALPHA)), label + ': actual translucent GPU fill')
    check(all(f > .65 for f in fractions), label + ': all four visible screen edges in correct color')


def fixture():
    global FIXTURE_NAMES
    from narwhal3d.model import NarwhalModel
    check(st.active_command is None and ui._trim_drag is None and ui._sel_drag is None,
          'clean command and mouse ownership before fixture', fatal=True)
    bridge.remove_display_objects(list(bpy.context.scene.objects))
    st.set_model(NarwhalModel())
    FIXTURE_NAMES = {}
    for name, a, b in (('Cutter', (-12, 0, 0), (12, 0, 0)),
                        ('TargetA', (-4, -8, 0), (-4, 8, 0)),
                        ('TargetB', (4, -8, 0), (4, 8, 0))):
        _, FIXTURE_NAMES[name] = st.ctx.add_geometry(bridge.make_line(a, b), name)
    ui.apply_selection([])
    ui.set_points_on(ui.selection_names(), False)
    ui._tag_redraw_viewports()
    yield .4
    st.start_command('Trim')
    yield .25
    check(st.active_command.step_index == 0, 'Trim starts in cutter-selection step', fatal=True)


def case(stage, direction, ending):
    label = '_'.join((stage, direction, ending))
    yield from fixture()
    if stage == 'remove':
        yield from tap('LEFTMOUSE', screen_point((10, 0, 0)))
        yield from tap('RET')
        check(st.active_command.step_index == 1, label + ': actual cutter click/Enter reached remove-parts', fatal=True)
        a, b = screen_point((-9, 2, 0)), screen_point((9, 6, 0))
    else:
        a, b = screen_point((-14, -1, 0)), screen_point((14, 1, 0))
    if direction == 'rtl':
        a, b = b, a
    baseline = snapshot(label + '_before')
    event('MOUSEMOVE', 'NOTHING', a)
    yield .15
    event('LEFTMOUSE', 'PRESS')
    yield .25
    pressed = snapshot(label + '_press')
    check(pressed['document'] == baseline['document'], label + ': empty press does not mutate geometry')
    event('MOUSEMOVE', 'NOTHING', ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
    yield .3
    middle = snapshot(label + '_mid')
    PAINT.clear()
    event('MOUSEMOVE', 'NOTHING', b)
    yield .45
    held = snapshot(label + '_held')
    if stage == 'remove':
        check(middle['trimmed'] == 1 and held['trimmed'] == 2,
              label + ': real document trims first then second target DURING held motion')
        check(middle['document'] != baseline['document'] and held['document'] != middle['document'],
              label + ': model archives changed at each held movement')
    else:
        check(held['document'] == baseline['document'] and not held['selection'],
              label + ': cutter drag defers selection and never edits geometry')
    yield from visual(label + '_held', a, b, direction, True)
    if stage == 'remove':
        # Empty-space feedback must follow the cursor even when no Trim pick succeeds.
        b = (b[0], b[1] + 18)
        PAINT.clear()
        REDRAWS.clear()
        event('MOUSEMOVE', 'NOTHING', b)
        yield .45
        empty = snapshot(label + '_empty_motion')
        check(empty['document'] == held['document'] and empty['trimmed'] == 2,
              label + ': further empty-space motion leaves geometry unchanged')
        yield from visual(label + '_empty_motion', a, b, direction, True)
    if ending == 'escape':
        yield from tap('ESC')  # Still holding left button: inspect BEFORE release.
        snap = snapshot(label + '_escape')
        check(snap['trim_drag'] is None and snap['sel_drag'] is None,
              label + ': Escape immediately clears both drag owners')
        yield from visual(label + '_escape', a, b, direction, False)
    event('LEFTMOUSE', 'RELEASE')
    yield .3
    released = snapshot(label + '_release')
    check(released['document'] == held['document'], label + ': release does not replay Trim')
    if stage == 'cutters' and ending == 'release':
        expected = ({FIXTURE_NAMES['Cutter']} if direction == 'ltr'
                    else set(FIXTURE_NAMES.values()))
        check(set(released['selection']) == expected, label + ': directional cutter selection on release')
    if stage == 'cutters' and ending == 'escape':
        check(not released['selection'], label + ': release after Escape must not select cutters')
    yield from visual(label + '_release', a, b, direction, False)
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
    check(not list(st.model.file.Objects), 'refuse nonempty startup document', fatal=True)
    AREA = ui._pane_by_label('Top')
    SPACE, REGION = AREA.spaces.active, ui._window_region(AREA)
    from mathutils import Vector
    ui.set_active_pane(AREA)
    ui.set_gumball(False)
    ui.set_grid_snap(False)
    ui.set_ortho(False)
    st.feed_line('DisableOsnap')
    SPACE.region_3d.view_location = Vector((0, 0, 0))
    SPACE.region_3d.view_distance = 45
    SPACE.region_3d.update()
    yield .4
    for stage in ('cutters', 'remove'):
        for direction in ('ltr', 'rtl'):
            for ending in ('release', 'escape'):
                yield from case(stage, direction, ending)


def observed_redraw():
    result = ORIGINAL_REDRAW()
    frame = sys._getframe(1)
    if frame.f_code is ui.NARWHAL_OT_command_input.modal.__code__:
        evt = frame.f_locals.get('event')
        drag = ui._trim_drag
        if evt is not None and evt.type == 'MOUSEMOVE' and drag is not None:
            _, _, region = ui._area_by_pane_key(drag['pane'])
            if region is not None and drag['last'] == (evt.mouse_x - region.x, evt.mouse_y - region.y):
                REDRAWS.append({'position': (evt.mouse_x, evt.mouse_y),
                                'time_s': time.monotonic() - START})
    return result


def install_mutation():
    """Recompile exactly one current function in memory, before bpy registration.

    Assert the anchor is unique rather than silently running an ineffective mutant.
    Disk source and other processes are never modified.
    """
    import inspect
    import textwrap

    mode = os.environ.get('TRIM_BOX_MUTATION', '')
    if mode not in ('fallback', 'empty_redraw', 'escape_reset'):
        return
    if mode == 'fallback':
        owner, name = ui, 'draw_point_markers'
        old = 'drag = selection_drag_rect() or _trim_drag_rect()'
        new = 'drag = selection_drag_rect()'
    else:
        owner, name = ui.NARWHAL_OT_command_input, 'modal'
        if mode == 'empty_redraw':
            old = ('            _trim_drag_update(event.mouse_x, event.mouse_y)\n'
                   '            _tag_redraw_viewports()\n'
                   '            self._redraw()')
            new = ('            if _trim_drag_update(event.mouse_x, event.mouse_y):\n'
                   '                _tag_redraw_viewports()\n'
                   '                self._redraw()')
        else:
            old = ('    if event.type == "ESC" and event.value == "PRESS":\n'
                   '        globals()["_sel_drag"] = None\n'
                   '        if _trim_drag is not None:')
            new = ('    if event.type == "ESC" and event.value == "PRESS":\n'
                   '        if _trim_drag is not None:')
    source = textwrap.dedent(inspect.getsource(getattr(owner, name)))
    if source.count(old) != 1:
        raise AssertionError('Mutation anchor missing/ambiguous: ' + mode)
    namespace = {}
    exec(compile(source.replace(old, new), '<trim-box-mutation:' + mode + '>', 'exec'),
         ui.__dict__, namespace)
    setattr(owner, name, namespace[name])
    RESULT['mutation_target'] = name


def tick():
    try:
        with bpy.context.temp_override(window=window()):
            return next(STEPS)
    except StopIteration:
        finish()
    except BaseException:
        finish(traceback.format_exc())


threading.Thread(target=lambda: (time.sleep(140), finish('140s watchdog')), daemon=True).start()
try:
    settings = Path(os.environ['NARWHAL3D_SETTINGS_FILE'])
    check(settings.parent == OUT and not settings.exists() and OUT.is_relative_to(Path(tempfile.gettempdir())),
          'fresh isolated temporary settings', fatal=True)
    check('narwhal3d' not in sys.modules and not os.environ.get('NARWHAL3D_UI_READY'),
          'test precedes bootstrap', fatal=True)
    check(os.environ.get('TRIM_BOX_MUTATION', '') in
          ('', 'hide', 'style', 'fallback', 'empty_redraw', 'escape_reset'),
          'known mutation', fatal=True)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    import bpy
    import gpu
    import gpu_extras.batch
    import numpy as np
    from narwhal3d import ui, bridge
    check(not bpy.app.background, 'real window required', fatal=True)
    install_mutation()
    ORIGINAL_REDRAW = ui._tag_redraw_viewports
    ui._tag_redraw_viewports = observed_redraw
    ORIGINAL_SHADER = ui._uniform_color_shader
    ORIGINAL_BATCH = gpu_extras.batch.batch_for_shader
    ui._uniform_color_shader = observed_shader
    gpu_extras.batch.batch_for_shader = observed_batch
    RESULT['source_sha256'] = {name: hashlib.sha256((ROOT / 'narwhal3d' / name).read_bytes()).hexdigest()
                               for name in ('ui.py', 'commands.py')}
    STEPS = run()
    bpy.app.timers.register(tick, first_interval=.1)
except BaseException:
    finish(traceback.format_exc())
