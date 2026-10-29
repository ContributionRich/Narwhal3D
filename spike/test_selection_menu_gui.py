"""OURS: real overlap-picker events and forwarded GPU highlight submissions.

  python3 -B spike/test_selection_menu_gui.py --launch
  python3 -B spike/test_selection_menu_gui.py --launch --mutation no_open
  python3 -B spike/test_selection_menu_gui.py --launch --mutation no_highlight

Mutations must exit RED, never invert their result into success. Main runs these.
Launcher follows test_trim_box_gui.py: fresh HOME/settings, shared GUI lock,
3G/no swap, 150s timeout, unique /tmp reports; no builds or shared artifacts.
Python is intentional Blender event/UI/GPU observation, not new picking math.
Only fixtures, initial selection and command start use APIs. All tested picks,
row hovers, confirmations and cancellations are real Window.event_simulate.
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
    import argparse
    import fcntl
    import subprocess

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--launch', action='store_true')
    parser.add_argument('--mutation', choices=('no_open', 'no_highlight'), default='')
    args = parser.parse_args()
    out = Path(tempfile.mkdtemp(prefix='selection-menu-gui-'))
    print('SELECTION_MENU_OUTPUT', out, flush=True)
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
    for key, subdir in (('HOME', 'home'), ('XDG_CONFIG_HOME', 'config'),
                        ('XDG_CACHE_HOME', 'cache'), ('XDG_DATA_HOME', 'data')):
        folder = out / subdir
        folder.mkdir()
        env[key] = str(folder)
    env.update(SELECTION_MENU_OUTPUT=str(out), SELECTION_MENU_MUTATION=args.mutation,
               PYTHONDONTWRITEBYTECODE='1', NARWHAL3D_SETTINGS_FILE=str(out / 'preferences.json'),
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
        text = (out / 'run.log').read_text()
        print(text, flush=True)
        report = out / 'result.json'
        if not report.exists():
            raise RuntimeError('GUI exited without result: ' + str(result.returncode))
        data = json.loads(report.read_text())
        bad_draw = 'point marker draw failed' in text or 'Traceback (most recent call last)' in text
        print('SELECTION_MENU_COMPLETED', out, 'exit', result.returncode, flush=True)
        return result.returncode or int(data['status'] != 'passed' or bad_draw)


if '--launch' in sys.argv:
    raise SystemExit(launch())

import threading
import traceback

START = time.monotonic()
OUT = Path(os.environ['SELECTION_MENU_OUTPUT'])
RESULT = {'status': 'running', 'checks': [], 'states': [], 'events': [], 'gpu': [],
          'input': 'actual Window.event_simulate', 'owned_pid': os.getpid(),
          'mutation': os.environ.get('SELECTION_MENU_MUTATION', '')}
POSITION = (0, 0)
PAINT = []
FINISH_LOCK = threading.Lock()


def check(ok, label):
    RESULT['checks'].append({'ok': bool(ok), 'label': label})
    print('SELECTION_MENU', 'PASS' if ok else 'FAIL', label, flush=True)
    if not ok:
        raise AssertionError(label)


def finish(error=None):
    if not FINISH_LOCK.acquire(False):
        return
    code = int(bool(error) or any(not row['ok'] for row in RESULT['checks']))
    RESULT.update(status='failed' if code else 'passed', error=error,
                  elapsed_s=time.monotonic() - START)
    try:
        (OUT / 'result.json').write_text(json.dumps(RESULT, indent=2, default=str))
        print('SELECTION_MENU_RESULT', RESULT['status'], 'checks', len(RESULT['checks']), flush=True)
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)  # A timer exception alone does not fail Blender.


def window():
    return bpy.context.window_manager.windows[0]


def event(kind, value, point=None, *, shift=False, ctrl=False):
    global POSITION
    if point is not None:
        POSITION = tuple(round(v) for v in point)
    RESULT['events'].append((kind, value, POSITION, shift, ctrl))
    window().event_simulate(type=kind, value=value, x=POSITION[0], y=POSITION[1],
                            shift=shift, ctrl=ctrl)


def tap(kind, point=None, **modifiers):
    if point is not None:
        event('MOUSEMOVE', 'NOTHING', point, **modifiers)
        yield .15
    event(kind, 'PRESS', **modifiers)
    yield .1
    event(kind, 'RELEASE', **modifiers)
    yield .3


def snapshot(label):
    document = {obj.Attributes.Name: (str(obj.Attributes.Id), hashlib.sha256(
        json.dumps(obj.Geometry.Encode(), sort_keys=True).encode()).hexdigest())
        for obj in st.model.file.Objects}
    cmd = st.active_command
    row = {'label': label, 'document': document, 'undo': list(st.ctx.undo.undo_names()),
           'selection': sorted(ui.selection_names()), 'parts': list(ui.selected_subobjects()),
           'command': getattr(cmd, 'name', None), 'step': getattr(cmd, 'step_index', None)}
    RESULT['states'].append(row)
    return row


def unchanged(before, label, selection=True):
    after = snapshot(label)
    fields = ('document', 'undo', 'command', 'step')
    if selection:
        fields += ('selection', 'parts')
    check(all(after[k] == before[k] for k in fields), label + ': unchanged ' + ', '.join(fields))


def screen_point(world):
    p = ui._project_2d(REGION, SPACE.region_3d, world)
    check(p is not None, 'fixture projects')
    xy = (round(REGION.x + p[0]), round(REGION.y + p[1]))
    check(REGION.x + 20 < xy[0] < REGION.x + REGION.width - 20
          and REGION.y + 20 < xy[1] < REGION.y + REGION.height - 35,
          'fixture point inside Top viewport')
    return xy


class ShaderObserver:
    def __init__(self, shader):
        self.shader, self.color = shader, None

    def __getattr__(self, name):
        return getattr(self.shader, name)

    def uniform_float(self, name, value):
        if name == 'color':
            self.color = tuple(value)
        return self.shader.uniform_float(name, value)


class BatchObserver:
    def __init__(self, batch, kind, content, key):
        self.batch, self.kind, self.key = batch, kind, key
        self.vertices = [tuple(p) for p in content.get('pos', ())]

    def draw(self, shader):
        result = self.batch.draw(shader.shader)  # Observe only AFTER forwarding to the real GPU.
        if len(PAINT) < 128:
            PAINT.append({'key': self.key, 'kind': self.kind, 'vertices': self.vertices,
                          'color': shader.color, 'pane': ui._pane_key(bpy.context.area)})
        return result


def observed_hover(region, rv3d, shader, batch_for_shader):
    choice = selection_menu.highlighted()
    if choice is None:
        return ORIGINAL_HOVER(region, rv3d, shader, batch_for_shader)
    wrapped = ShaderObserver(shader)
    def batch(observer, kind, content, **kwargs):
        real = batch_for_shader(observer.shader, kind, content, **kwargs)
        return BatchObserver(real, kind, content, choice.key)
    return ORIGINAL_HOVER(region, rv3d, wrapped, batch)


def row_point(key):
    session = selection_menu.current()
    check(session is not None, 'overlap popup is open')
    index = next((i for i, c in enumerate(session['candidates']) if c.key == key), None)
    check(index is not None, 'popup contains ' + repr(key))
    panel = session['menu'].panels[0]
    rect = shell_popup._intersection(panel.row_rects[index], panel.body)
    check(rect is not None, 'requested row is visible')
    return ((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)


def hover(key, label):
    before = snapshot(label + '_before_hover')
    PAINT.clear()
    event('MOUSEMOVE', 'NOTHING', row_point(key))
    yield .45  # Observe event-driven repaint, not a direct call to a drawing function.
    choice = selection_menu.highlighted()
    expected = ('object', key[0]) if key[1] == 'Object' else ('part', key)
    check(ui.hover_pick() == expected and choice is not None and choice.key == key,
          label + ': actual row hover updates ui.hover_pick')
    kind = 'TRIS' if choice.triangles else 'LINES'
    spans = choice.triangles if choice.triangles else choice.lines
    vertices = []
    for span in spans:
        projected = [ui._project_2d(REGION, SPACE.region_3d, p) for p in span]
        check(all(p is not None for p in projected), label + ': highlight fixture projects')
        if kind == 'TRIS':
            vertices.extend(tuple(p) for p in projected)
        else:
            vertices.extend(tuple(p) for a, b in zip(projected, projected[1:]) for p in (a, b))
    color = ui.HOVER_PICK_COLOR[:3] + ((.35,) if kind == 'TRIS' else (1.0,))
    matched = [r for r in PAINT if r['key'] == key and r['kind'] == kind
               and r['pane'] == ui._pane_key(AREA) and len(r['vertices']) == len(vertices)
               and np.allclose(r['vertices'], vertices, atol=.01, rtol=0)
               and np.allclose(r['color'], color, atol=1e-6, rtol=0)]
    RESULT['gpu'].append({'label': label, 'submissions': len(PAINT), 'match': matched[:1]})
    check(bool(vertices) and bool(matched), label + ': real forwarded GPU highlight vertices/color')
    unchanged(before, label + '_after_hover')


def open_at(point, label, **modifiers):
    before = snapshot(label + '_before_open')
    yield from tap('LEFTMOUSE', point, **modifiers)
    session = selection_menu.current()
    check(session is not None and len(session['candidates']) >= 2,
          label + ': actual viewport click opens ambiguity menu')
    RESULT['states'].append({'label': label + '_candidates',
                             'keys': [c.key for c in session['candidates']]})
    unchanged(before, label + '_opened')
    return session


def accept(key):
    yield from tap('LEFTMOUSE', row_point(key))
    check(selection_menu.current() is None and not shell_popup.is_open(), 'confirmation closes popup')


def cancel(point, outside, label):
    before = snapshot(label + '_before')
    yield from open_at(point, label)
    if outside:
        check(shell_popup._model.hit_test(*EMPTY_POINT) is None, 'cancel click really outside menu')
        yield from tap('LEFTMOUSE', EMPTY_POINT)
    else:
        yield from tap('ESC')
    check(selection_menu.current() is None and not shell_popup.is_open(), label + ': menu dismissed')
    unchanged(before, label + '_after_release')


def run():
    global st, AREA, SPACE, REGION, EMPTY_POINT
    deadline = time.monotonic() + 45
    while not (os.environ.get('NARWHAL3D_UI_READY') == '1' and not ui._viewports_in_progress
               and ui._viewport_count() == 4 and ui.is_input_active()):
        if time.monotonic() > deadline:
            raise RuntimeError('Bootstrap not ready in 45s')
        yield .15
    st = ui.app_state
    check(not list(st.model.file.Objects), 'refuse nonempty startup document')
    AREA = ui._pane_by_label('Top')
    check(AREA is not None, 'Top pane exists')
    SPACE, REGION = AREA.spaces.active, ui._window_region(AREA)
    ui.set_active_pane(AREA)
    ui.set_gumball(False)
    ui.set_grid_snap(False)
    ui.set_ortho(False)
    st.feed_line('DisableOsnap')
    SPACE.shading.type = 'WIREFRAME'
    SPACE.region_3d.view_location = Vector((0, 0, 0))
    SPACE.region_3d.view_distance = 45
    SPACE.region_3d.update()
    yield .4
    # Four screen pixels separate the two real document lines at this viewport size.
    p0, p1 = (ui._project_2d(REGION, SPACE.region_3d, p) for p in ((0, 0, 0), (0, 1, 0)))
    check(p0 is not None and p1 is not None and abs(p1[1] - p0[1]) > .1, 'Top scale is usable')
    dy = 4.0 / abs(p1[1] - p0[1])
    _, first = st.ctx.add_geometry(bridge.make_line((-12, 3, 0), (-4, 3, 0)), 'OverlapA')
    _, second = st.ctx.add_geometry(bridge.make_line((-13, 3 + dy, 0), (-3, 3 + dy, 0)), 'OverlapB')
    _, box = st.ctx.add_geometry(bridge.make_box((4, -4, 0), (12, 4, 0), 4), 'OverlapBox')
    ui.apply_selection([first])
    ui.set_points_on([first], False)
    ui._tag_redraw_viewports()
    yield .4
    line_point, edge_point = screen_point((-8, 3, 0)), screen_point((8, -4, 4))
    EMPTY_POINT = screen_point((0, 12, 0))
    a, b = (first, 'Object', 0), (second, 'Object', 0)
    fixture = snapshot('fixture')

    session = yield from open_at(line_point, 'shift', shift=True)
    check([c.key for c in session['candidates']] == [a, b], 'nearest first, distinct second line offered')
    yield from hover(a, 'first_line')
    yield from hover(b, 'second_line')
    yield from accept(b)  # Modifiers released: opener's Shift intent must be retained.
    check(set(ui.selection_names()) == {first, second}, 'Shift adds chosen second line, preserving initial selection')
    unchanged(fixture, 'shift_selected', selection=False)

    yield from open_at(line_point, 'ctrl', ctrl=True)
    yield from accept(b)
    check(ui.selection_names() == [first], 'Ctrl removes only chosen second line')
    unchanged(fixture, 'ctrl_removed', selection=False)
    yield from cancel(line_point, False, 'escape')
    yield from cancel(line_point, True, 'outside')

    session = yield from open_at(edge_point, 'parts', ctrl=True, shift=True)
    edges = [c for c in session['candidates'] if c.key[:2] == (box, 'Edge')]
    faces = [c for c in session['candidates'] if c.key[:2] == (box, 'Face')]
    check(bool(edges) and len(faces) >= 2, 'CtrlShift offers edge and adjacent faces')
    edge = max(edges, key=lambda c: max(p[2] for span in c.lines for p in span)).key
    face = max(faces, key=lambda c: min(p[2] for tri in c.triangles for p in tri)).key
    yield from hover(face, 'box_face')
    yield from hover(edge, 'box_edge')
    yield from accept(edge)
    check(ui.selected_subobjects() == [edge] and ui.selection_names() == [first],
          'CtrlShift chooses only the requested edge, not faces or whole box')
    unchanged(fixture, 'edge_on', selection=False)
    yield from cancel(line_point, False, 'escape_preserves_edge')
    yield from open_at(edge_point, 'parts_toggle_off', ctrl=True, shift=True)
    yield from accept(edge)
    check(not ui.selected_subobjects() and ui.selection_names() == [first], 'second CtrlShift choice toggles same edge off')
    unchanged(fixture, 'edge_off', selection=False)

    yield from tap('LEFTMOUSE', EMPTY_POINT)
    check(not ui.selection_names() and not ui.selected_subobjects(), 'actual empty click clears preselection')
    st.start_command('Length')
    yield .25
    cmd = st.active_command
    check(cmd is not None and cmd.steps[cmd.step_index][1] == commands.OBJECTS,
          'existing Length command waits at single OBJECTS prompt')
    yield from open_at(line_point, 'command')
    check(st.active_command is cmd and cmd.step_index == 0 and not cmd.args,
          'opening menu does not answer the command')
    yield from accept(b)
    check(st.active_command is None and cmd.args == [second], 'row choice answers single OBJECTS with second line')
    check(any('Length = 10' in row and second in row for row in st.history[-4:]),
          'Length reports chosen second line, not nearest first line')
    check(snapshot('final')['document'] == fixture['document'], 'all document geometry/identity hashes unchanged')


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
          'fresh isolated temporary settings')
    check('narwhal3d' not in sys.modules and not os.environ.get('NARWHAL3D_UI_READY'), 'test precedes bootstrap')
    check(RESULT['mutation'] in ('', 'no_open', 'no_highlight'), 'known process-local mutation')
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    import bpy
    import numpy as np
    from mathutils import Vector
    from narwhal3d import ui, bridge, commands, selection_menu, shell_popup
    check(not bpy.app.background, 'real window required')
    ORIGINAL_HOVER = ui.draw_hover_pick
    ui.draw_hover_pick = observed_hover
    if RESULT['mutation'] == 'no_open':
        selection_menu.open = lambda *args, **kwargs: False
    elif RESULT['mutation'] == 'no_highlight':
        ui.draw_hover_pick = lambda *args, **kwargs: None
    RESULT['source_sha256'] = {name: hashlib.sha256((ROOT / 'narwhal3d' / name).read_bytes()).hexdigest()
                               for name in ('ui.py', 'selection_menu.py', 'pick_candidates.py')}
    STEPS = run()
    bpy.app.timers.register(tick, first_interval=.1)
except BaseException:
    finish(traceback.format_exc())
