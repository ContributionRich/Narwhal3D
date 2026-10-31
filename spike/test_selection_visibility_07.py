"""OURS MAIN-REGRESSIONS-07: first-popup visibility in a fresh actual Blender GUI.

  python3 -B spike/test_selection_visibility_07.py --launch --first vertex
  python3 -B spike/test_selection_visibility_07.py --launch --first edge
  python3 -B spike/test_selection_visibility_07.py --launch --first face
  python3 -B spike/test_selection_visibility_07.py --launch --first object
  python3 -B spike/test_selection_visibility_07.py --launch --mutation no_register
  python3 -B spike/test_selection_visibility_07.py --launch --mutation no_highlight
  python3 -B spike/test_selection_visibility_07.py --launch --diagnostic-scroll
  python3 -B spike/test_selection_visibility_07.py --layout-only
  python3 -B spike/test_selection_visibility_07.py --layout-only --mutation no_scroll_tolerance
  python3 -B spike/test_selection_visibility_07.py --launch --first object --mutation no_scroll_tolerance

Default mode is strict about a target row being visible. --diagnostic-scroll uses
real keyboard navigation to reveal a clipped row, records that workaround, and
certifies ONLY downstream interactions, not the two-row layout regression.

Negative controls must exit RED. They modify only this Blender process. No builds,
owner documents, test-helper launchers or nested GUI locks. Python is necessary
Blender event/RNA/GPU test orchestration, not a new picking/geometry implementation.
Actual Window.event_simulate drives every tested pick/hover/accept/cancel. Window
POST_PIXEL readback witnesses composed framebuffer pixels (not physical monitor
presentation). PNGs and JSON retain baseline, popup and hover evidence. Each --first
invocation starts a new process: no caption/bug menu primes popup registration.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def mutate_scroll_tolerance(module=None):
    """The Python layout mutation is retired, not a successful negative control."""
    raise RuntimeError(
        'retired mutation mode no_scroll_tolerance: popup layout is native; '
        'use the native popup mutation fixture and a qualified mutated UI library')


def launch():
    import argparse
    import fcntl
    import subprocess

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--launch', action='store_true')
    parser.add_argument('--first', choices=('vertex', 'edge', 'face', 'object'), default='vertex')
    parser.add_argument('--mutation', choices=('no_register', 'no_highlight', 'no_scroll_tolerance'), default='')
    parser.add_argument('--diagnostic-scroll', action='store_true',
                        help='Reveal clipped rows by keyboard; does NOT certify initial row layout')
    args = parser.parse_args()
    if args.mutation == 'no_scroll_tolerance':
        mutate_scroll_tolerance()
    # Disk-backed, deliberately short enough for Blender's Unix socket names.
    scratch = ROOT / 'tmp/s07'
    scratch.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix='', dir=scratch))
    print('SELECTION_VISIBILITY_07_OUTPUT', out, flush=True)
    with open('/tmp/narwhal3d_gui.lock', 'a') as lock:
        deadline = time.monotonic() + 60
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise TimeoutError('GUI lock busy for 60s; no Blender launched')
                time.sleep(.25)
        mem = {line.split(':')[0]: int(line.split()[1])
               for line in Path('/proc/meminfo').read_text().splitlines()}
        if mem['MemAvailable'] < 6 * 1024 * 1024:
            raise RuntimeError('Need 6 GiB available: 2 GiB GUI cap + 4 GiB reserve')
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(('NARWHAL', 'RHI' + 'NO', 'BLENDER_'))
               and k not in ('PYTHONPATH', 'LD_LIBRARY_PATH')}
        paths = subprocess.check_output([str(ROOT / 'narwhal3d.sh')], cwd=ROOT,
            env=dict(env, NARWHAL3D_LAUNCHER_TEST='native-paths'), text=True, timeout=10)
        for row in paths.splitlines():
            key, value = row.split('=', 1)
            if value:
                env[key] = value
        # Retain the harness-qualified UI core across the clean launch environment.
        if 'NARWHAL_UI_CORE_LIBRARY' in os.environ:
            env['NARWHAL_UI_CORE_LIBRARY'] = os.environ['NARWHAL_UI_CORE_LIBRARY']
        for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
            folder = out / key.lower()
            folder.mkdir()
            env[key] = str(folder)
        env.update(TMPDIR=str(out), PYTHONDONTWRITEBYTECODE='1',
                   PYTHONPATH=str(ROOT / 'vendor/occt'),
                   LD_LIBRARY_PATH=str(ROOT / 'tools/blender-patched/5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs'),
                   NARWHAL3D_SETTINGS_FILE=str(out / 'preferences.json'),
                   NARWHAL3D_DEFER_FIRST_PRESENT='1', NARWHAL3D_NO_DIALOGS='1',
                   OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2',
                   VISIBILITY_07_OUTPUT=str(out), VISIBILITY_07_FIRST=args.first,
                   VISIBILITY_07_MUTATION=args.mutation,
                   VISIBILITY_07_DIAGNOSTIC_SCROLL=str(int(args.diagnostic_scroll)))
        cmd = ['systemd-run', '--user', '--scope', '--quiet',
               '-p', 'MemoryMax=2G', '-p', 'MemorySwapMax=0',
               'timeout', '--signal=TERM', '--kill-after=5s', '140s',
               str(ROOT / 'tools/blender-patched/blender'), '--threads', '2',
               '--factory-startup', '--window-geometry', '0', '0', '1600', '1000',
               '--enable-event-simulate', '--python-exit-code', '1',
               '--python', str(Path(__file__).resolve()),
               '--python', str(ROOT / 'launch/bootstrap.py')]
        (out / 'launch.json').write_text(json.dumps({'command': cmd, 'native_paths': paths,
            'mem_available_kib': mem['MemAvailable'], 'args': vars(args)}, indent=2))
        with (out / 'run.log').open('w') as log:
            result = subprocess.run(cmd, cwd=ROOT, env=env, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=155)
        text = (out / 'run.log').read_text(errors='replace')
        print(text, flush=True)
        report = out / 'result.json'
        if not report.exists():
            raise RuntimeError('Blender exited without result: ' + str(result.returncode))
        data = json.loads(report.read_text())
        print('SELECTION_VISIBILITY_07_COMPLETED', out, 'exit', result.returncode, flush=True)
        return result.returncode or int(data['status'] != 'passed' or
                                       'Traceback (most recent call last)' in text)


def layout_only():
    """Focused native-layout reproduction of the measured 0.85-scale defect."""
    import argparse
    import importlib.util
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--layout-only', action='store_true')
    parser.add_argument('--mutation', choices=('no_scroll_tolerance',), default='')
    args = parser.parse_args()
    if args.mutation:
        mutate_scroll_tolerance()
    # Load the pure adapter without importing the Blender application package.
    import types
    package = types.ModuleType('visibility_07_native')
    package.__path__ = [str(ROOT / 'narwhal3d')]
    sys.modules[package.__name__] = package
    spec = importlib.util.spec_from_file_location('visibility_07_native.native_popup', ROOT / 'narwhal3d/native_popup.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    model = module.NativePopup((1600, 1000), .85)
    def intersection(a, b):
        rect = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
        return rect if rect[0] < rect[2] and rect[1] < rect[3] else None
    model.open([{'label': 'OverlapA', 'action': 0}, {'label': 'OverlapB', 'action': 1}],
               (352, 666), 'Choose object', lambda *args: None)
    panel = model.panels[0]
    intersections = [intersection(rect, panel.body) for rect in panel.row_rects]
    print(json.dumps({'scale': model.scale, 'max_scroll': panel.max_scroll,
        'body': panel.body, 'rows': panel.row_rects, 'visible': intersections}, indent=2))
    assert panel.max_scroll <= 1e-6 and panel.up is None and panel.down is None, (
        'two-row chooser at 0.85 fits: floating-point residue must not enable scroll gutters')
    assert all(rect is not None for rect in intersections), 'both object rows must initially be visible'
    assert all(abs(actual - expected) <= 1e-6
               for visible, row in zip(intersections, panel.row_rects)
               for actual, expected in zip(visible, row)), 'both rows must be fully visible, not clipped'
    model.dispose()
    print('VISIBILITY_07_LAYOUT PASS')


if '--layout-only' in sys.argv:
    layout_only()
    raise SystemExit(0)
if '--launch' in sys.argv:
    raise SystemExit(launch())

import struct
import threading
import traceback
import zlib

START = time.monotonic()
OUT = Path(os.environ['VISIBILITY_07_OUTPUT'])
RESULT = {'status': 'running', 'checks': [], 'events': [], 'states': [], 'pixels': [],
          'releases': [], 'opens': [], 'layout_defects': [],
          'diagnostic_scroll': os.environ.get('VISIBILITY_07_DIAGNOSTIC_SCROLL') == '1',
          'first': os.environ['VISIBILITY_07_FIRST'],
          'mutation': os.environ.get('VISIBILITY_07_MUTATION', ''), 'pid': os.getpid()}
FINISH = threading.Lock()
POSITION = (0, 0)


def check(ok, label):
    RESULT['checks'].append({'ok': bool(ok), 'label': label})
    print('VISIBILITY_07', 'PASS' if ok else 'FAIL', label, flush=True)
    if not ok:
        raise AssertionError(label)


def finish(error=None):
    if not FINISH.acquire(False):
        return
    code = int(bool(error) or any(not row['ok'] for row in RESULT['checks']))
    RESULT.update(status='failed' if code else 'passed', error=error,
                  elapsed_s=time.monotonic() - START)
    (OUT / 'result.json').write_text(json.dumps(RESULT, indent=2, default=str))
    print('VISIBILITY_07_RESULT', RESULT['status'], 'checks', len(RESULT['checks']), flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def window():
    return bpy.context.window_manager.windows[0]


def event(kind, value, point=None, **modifiers):
    global POSITION
    if point is not None:
        POSITION = tuple(round(v) for v in point)
    RESULT['events'].append((kind, value, POSITION, modifiers))
    window().event_simulate(type=kind, value=value, x=POSITION[0], y=POSITION[1], **modifiers)


def tap(kind, point=None, **modifiers):
    if point is not None:
        event('MOUSEMOVE', 'NOTHING', point, **modifiers)
        yield .15
    event(kind, 'PRESS', **modifiers)
    yield .15
    event(kind, 'RELEASE', **modifiers)
    yield .3


def snapshot(label):
    row = {'label': label, 'document': {obj.Attributes.Name: (str(obj.Attributes.Id),
        hashlib.sha256(json.dumps(obj.Geometry.Encode(), sort_keys=True).encode()).hexdigest())
        for obj in st.model.file.Objects}, 'undo': list(st.ctx.undo.undo_names()),
        'objects': sorted(ui.selection_names()), 'parts': list(ui.selected_subobjects()),
        'points': sorted(ui._selected_cps), 'command': getattr(st.active_command, 'name', None)}
    RESULT['states'].append(row)
    return row


def unchanged(before, label, selection=True):
    after = snapshot(label)
    fields = ('document', 'undo', 'command') + (('objects', 'parts', 'points') if selection else ())
    check(all(before[k] == after[k] for k in fields), label + ': unchanged ' + ', '.join(fields))


def screen_point(world):
    p = ui._project_2d(REGION, SPACE.region_3d, world)
    check(p is not None, 'fixture projects')
    xy = (round(REGION.x + p[0]), round(REGION.y + p[1]))
    check(REGION.x + 20 < xy[0] < REGION.x + REGION.width - 20
          and REGION.y + 20 < xy[1] < REGION.y + REGION.height - 35, 'fixture inside Top')
    return xy


def png(path, pixels):
    """Dependency-free RGB screenshot; readback's origin is bottom-left."""
    rgb = np.rint(np.clip(pixels, 0, 1) * 255).astype(np.uint8)[::-1]
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    raw = b''.join(b'\0' + row.tobytes() for row in rgb)
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B',
        rgb.shape[1], rgb.shape[0], 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def capture(label):
    # Install AFTER popup.open has registered its draw handler; never call draw ourselves.
    got = []
    def readback():
        try:
            x, y, w, h = gpu.state.viewport_get()
            if (w, h) != (window().width, window().height):
                return
            buf = gpu.state.active_framebuffer_get().read_color(x, y, w, h, 3, 0, 'FLOAT')
            buf.dimensions = w * h * 3
            got.append(np.array(buf, dtype=np.float32).reshape(h, w, 3))
        except BaseException:
            got.append(traceback.format_exc())
    token = bpy.types.Window.draw_handler_add(readback, (), 'POST_PIXEL')
    try:
        deadline = time.monotonic() + 4
        for area in window().screen.areas:
            area.tag_redraw()
        while not got:
            if time.monotonic() > deadline:
                raise RuntimeError('No composed framebuffer: ' + label)
            yield .1
    finally:
        bpy.types.Window.draw_handler_remove(token)
    check(not isinstance(got[-1], str), label + ': real framebuffer captured')
    pixels = got[-1]
    png(OUT / (label + '.png'), pixels)
    return pixels


def roi(pixels, rect, inset=0):
    x0, y0, x1, y1 = (round(v) for v in rect)
    return pixels[y0 + inset:y1 - inset, x0 + inset:x1 - inset]


def near_color(pixels, color, tolerance=.035):
    return np.max(np.abs(pixels - np.asarray(color)[:3]), axis=-1) < tolerance


def visible(before, after, label):
    panel = selection_menu.current()['menu'].panels[0]
    old, new = (roi(p, panel.rect, 5) for p in (before, after))
    changed = float(np.mean(np.max(np.abs(new - old), axis=-1) > .04))
    background = float(np.mean(near_color(new, design.rgb01(design.MENU_BG))))
    # Title/labels must have ink as well as the large opaque panel background.
    ink = int(np.count_nonzero(near_color(new, design.rgb01(design.TEXT), .08)))
    RESULT['pixels'].append({'label': label, 'rect': panel.rect, 'changed_fraction': changed,
                             'menu_background_fraction': background, 'text_pixels': ink,
                             'handlers': len(shell_popup._handlers)})
    check(changed > .65 and background > .45 and ink > 20,
          label + ': popup VISIBLE in framebuffer (panel/background/text, not modal state)')


def row_point(key):
    session = selection_menu.current()
    check(session is not None, 'chooser open')
    index = next((i for i, c in enumerate(session['candidates']) if c.key == key), None)
    check(index is not None, 'chooser contains ' + repr(key))
    panel = session['menu'].panels[0]
    rect = shell_popup._intersection(panel.row_rects[index], panel.body)
    check(rect is not None, 'row in visible menu body')
    return ((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)


def open_at(point, label, **modifiers):
    before = snapshot(label + '_before')
    closed = yield from capture(label + '_closed')
    yield from tap('LEFTMOUSE', point, **modifiers)
    session = selection_menu.current()
    check(session is not None and len(session['candidates']) >= 2, label + ': actual click opens modal')
    RESULT['states'].append({'label': label + '_candidates', 'keys': [c.key for c in session['candidates']]})
    opened = yield from capture(label + '_open')
    visible(closed, opened, label)
    unchanged(before, label + '_opened')
    return session, opened


def hover(key, baseline, label):
    before = snapshot(label + '_before_hover')
    session = selection_menu.current()
    index = next(i for i, c in enumerate(session['candidates']) if c.key == key)
    panel = session['menu'].panels[0]
    if shell_popup._intersection(panel.row_rects[index], panel.body) is None:
        RESULT['layout_defects'].append({'label': label, 'scale': session['menu'].scale,
            'row': index, 'body': panel.body, 'rects': panel.row_rects,
            'max_scroll': panel.max_scroll, 'diagnostic_scroll': RESULT['diagnostic_scroll']})
        if RESULT['diagnostic_scroll']:
            yield from tap('HOME')
            for _ in range(index):
                yield from tap('DOWN_ARROW')
    event('MOUSEMOVE', 'NOTHING', row_point(key))
    yield .25
    choice = selection_menu.highlighted()
    check(choice is not None and choice.key == key, label + ': actual hover chooses candidate')
    after = yield from capture(label + '_hover')
    panel = selection_menu.current()['menu'].panels[0]
    rect = shell_popup._intersection(panel.row_rects[panel.selected], panel.body)
    strip = (rect[0] + 6, rect[1] + 3, rect[0] + 18, rect[3] - 3)
    accent = float(np.mean(near_color(roi(after, strip), design.rgb01(design.ACCENT))))
    check(accent > .8, label + ': hovered row accent visible in framebuffer')
    # Restrict the differential to this candidate's projected extent, excluding the
    # entire popup. The row's accent alone cannot satisfy geometry hover visibility.
    worlds = list(choice.points) + [p for span in choice.lines + choice.triangles for p in span]
    projected = [ui._project_2d(REGION, SPACE.region_3d, p) for p in worlds]
    check(bool(projected) and all(p is not None for p in projected), label + ': candidate projects')
    xy = np.asarray(projected) + (REGION.x, REGION.y)
    lo, hi = np.floor(xy.min(axis=0) - 5).astype(int), np.ceil(xy.max(axis=0) + 5).astype(int)
    mask = np.zeros(after.shape[:2], dtype=bool)
    mask[max(REGION.y, lo[1]):min(REGION.y + REGION.height, hi[1]),
         max(REGION.x, lo[0]):min(REGION.x + REGION.width, hi[0])] = True
    x0, y0, x1, y1 = (round(v) for v in panel.rect)
    mask[max(0, y0 - 3):y1 + 3, max(0, x0 - 3):x1 + 3] = False
    delta = np.max(np.abs(after - baseline), axis=-1)
    changed = int(np.count_nonzero(mask & (delta > .06)))
    RESULT['pixels'].append({'label': label, 'row_accent_fraction': accent,
                             'candidate_changed_pixels_outside_popup': changed, 'key': key})
    check(changed >= 8, label + ': geometry hover changes framebuffer outside popup')
    unchanged(before, label + '_after_hover')


def shielded_since(start, key, label):
    rows = RESULT['releases'][start:]
    check(any(r['type'] == key and r['consumed'] for r in rows), label + ': matching release consumed')
    check(not shell_popup._model._releases and ui._sel_drag is None,
          label + ': release guard and selection drag cleared')


def accept(key, keyboard=False):
    start = len(RESULT['releases'])
    if keyboard:
        index = next(i for i, c in enumerate(selection_menu.current()['candidates']) if c.key == key)
        yield from tap('HOME')
        for _ in range(index):
            yield from tap('DOWN_ARROW')
        check(selection_menu.highlighted().key == key, 'keyboard navigates requested row')
        yield from tap('RET')
    else:
        yield from tap('LEFTMOUSE', row_point(key))
    check(selection_menu.current() is None and not shell_popup.is_open(), 'accept closes menu')
    shielded_since(start, 'RET' if keyboard else 'LEFTMOUSE', 'accept')


def cancel(point, outside, label, **modifiers):
    before = snapshot(label + '_before_cancel')
    yield from open_at(point, label, **modifiers)
    start = len(RESULT['releases'])
    if outside:
        check(shell_popup._model.hit_test(*EMPTY) is None, 'outside target outside popup')
        yield from tap('LEFTMOUSE', EMPTY)
    else:
        yield from tap('ESC')
    check(selection_menu.current() is None and not shell_popup.is_open(), label + ': dismissed')
    shielded_since(start, 'LEFTMOUSE' if outside else 'ESC', label)
    unchanged(before, label + '_after_matching_release')


def run():
    global st, AREA, SPACE, REGION, EMPTY
    deadline = time.monotonic() + 45
    while not (os.environ.get('NARWHAL3D_UI_READY') == '1' and not ui._viewports_in_progress
               and ui._viewport_count() == 4 and ui.is_input_active()):
        if time.monotonic() > deadline:
            raise RuntimeError('Bootstrap not ready in 45s')
        yield .15
    st = ui.app_state
    check(not list(st.model.file.Objects), 'fresh empty startup document')
    check(not RESULT['opens'] and not shell_popup._handlers and shell_popup._model is None,
          'no earlier popup or manual registration since startup')
    AREA = ui._pane_by_label('Top')
    check(AREA is not None, 'Top viewport exists')
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
    p0, p1 = (ui._project_2d(REGION, SPACE.region_3d, p) for p in ((0, 0, 0), (0, 1, 0)))
    dy = 4 / abs(p1[1] - p0[1])
    _, first = st.ctx.add_geometry(bridge.make_line((-12, 3, 0), (-4, 3, 0)), 'OverlapA')
    _, second = st.ctx.add_geometry(bridge.make_line((-13, 3 + dy, 0), (-3, 3 + dy, 0)), 'OverlapB')
    _, box = st.ctx.add_geometry(bridge.make_box((4, -4, 0), (12, 4, 0), 4), 'OverlapBox')
    _, point = st.ctx.add_geometry(bridge.make_point((-8, -8, 0)), 'LaterPoint')
    ui.apply_selection([first])
    ui.set_points_on([first], False)
    ui._tag_redraw_viewports()
    yield .4
    positions = {key: screen_point(world) for key, world in {
        'object': (-8, 3, 0), 'vertex': (12, -4, 4), 'edge': (8, -4, 4),
        'face': (8, 0, 4), 'point': (-8, -8, 0)}.items()}
    EMPTY = screen_point((0, 12, 0))
    fixture = snapshot('fixture')
    first_case = RESULT['first']
    order = [first_case] + [key for key in ('vertex', 'edge', 'face', 'object') if key != first_case]
    for number, kind in enumerate(order):
        modifiers = {'shift': True, 'ctrl': kind != 'object'}
        session, baseline = yield from open_at(positions[kind], kind, **modifiers)
        if number == 0:
            check(len(RESULT['opens']) == 1, 'tested chooser is FIRST popup after startup')
        candidates = session['candidates']
        parts_before = set(ui.selected_subobjects())
        objects_before = set(ui.selection_names())
        kinds = {c.key[1] for c in candidates}
        expected = {'vertex': {'Vertex', 'Edge', 'Face'}, 'edge': {'Edge', 'Face'},
                    'face': {'Face'}, 'object': {'Object'}}[kind]
        check(expected <= kinds, kind + ': real overlapping candidate types offered')
        requested = {'vertex': 'Vertex', 'edge': 'Edge', 'face': 'Face', 'object': 'Object'}[kind]
        def representative(candidate_type):
            choices = [c for c in candidates if c.key[1] == candidate_type]
            if candidate_type == 'Face':
                # Side faces are exactly edge-on in Top: their fill has no pixels.
                # Choose a real offered cap, not a degenerate visibility fixture.
                def area(candidate):
                    total = 0.0
                    for triangle in candidate.triangles:
                        a, b, c = [ui._project_2d(REGION, SPACE.region_3d, p) for p in triangle]
                        total += abs((b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0]))
                    return total
                face = max(choices, key=area)
                check(area(face) > 100, 'offered face has nondegenerate projected area')
                return face
            return choices[-1] if candidate_type == 'Object' else choices[0]
        chosen = representative(requested)
        # The vertex ambiguity must visibly preview all three competing kinds.
        previews = [representative(t) for t in sorted(expected)]
        for candidate in previews:
            yield from hover(candidate.key, baseline, kind + '_' + candidate.key[1])
        yield from accept(chosen.key, keyboard=kind in {'vertex', 'face'})
        if kind == 'object':
            check(set(ui.selection_names()) == {first, second}, 'Shift opener adds chosen object after modifiers released')
        else:
            check(set(ui.selected_subobjects()) == parts_before.symmetric_difference({chosen.key})
                  and set(ui.selection_names()) == objects_before,
                  kind + ': only exact requested component toggles, whole-object selection preserved')
        unchanged(fixture, kind + '_accepted', selection=False)

    yield from cancel(positions['object'], False, 'escape', shift=True)
    yield from cancel(positions['vertex'], True, 'outside', shift=True, ctrl=True)
    yield from tap('LEFTMOUSE', positions['point'])
    check(not shell_popup.is_open() and ui.selection_names() == [point],
          'subsequent actual point-object click not stuck')
    session, baseline = yield from open_at(positions['edge'], 'subsequent_edge', shift=True, ctrl=True)
    edge = next(c.key for c in session['candidates'] if c.key[1] == 'Edge')
    old = set(ui.selected_subobjects())
    yield from hover(edge, baseline, 'subsequent_edge')
    yield from accept(edge)
    check(set(ui.selected_subobjects()) == old.symmetric_difference({edge}),
          'subsequent actual edge choice toggles exactly once, not stuck')
    unchanged(fixture, 'final', selection=False)


def tick():
    try:
        with bpy.context.temp_override(window=window()):
            return next(STEPS)
    except StopIteration:
        finish()
    except BaseException:
        finish(traceback.format_exc())


threading.Thread(target=lambda: (time.sleep(130), finish('130s watchdog')), daemon=True).start()
try:
    check('narwhal3d' not in sys.modules and not os.environ.get('NARWHAL3D_UI_READY'), 'test precedes bootstrap')
    check(Path(os.environ['NARWHAL3D_SETTINGS_FILE']).parent == OUT
          and not Path(os.environ['NARWHAL3D_SETTINGS_FILE']).exists(), 'fresh isolated settings')
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    import bpy
    import gpu
    import numpy as np
    from mathutils import Vector
    from narwhal3d import ui, bridge, design, selection_menu, shell_popup
    check(not bpy.app.background and hasattr(bpy.types.Window, 'draw_handler_add'), 'actual GUI window framebuffer API')
    original_open, original_event = shell_popup.open, shell_popup.handle_event
    def observe_open(*args, **kwargs):
        RESULT['opens'].append({'title': args[2], 'handlers_before': len(shell_popup._handlers)})
        return original_open(*args, **kwargs)
    def observe_event(event, context=None):
        consumed = original_event(event, context)
        if event.value == 'RELEASE':
            RESULT['releases'].append({'type': event.type, 'consumed': bool(consumed),
                                      'open_after': shell_popup.is_open()})
        return consumed
    shell_popup.open, shell_popup.handle_event = observe_open, observe_event
    if RESULT['mutation'] == 'no_register':
        shell_popup.register = lambda: None
    elif RESULT['mutation'] == 'no_highlight':
        ui.draw_hover_pick = lambda *args, **kwargs: None
    elif RESULT['mutation'] == 'no_scroll_tolerance':
        RESULT['mutated_layout_sha256'] = mutate_scroll_tolerance(shell_popup)
    RESULT['source_sha256'] = {name: hashlib.sha256((ROOT / 'narwhal3d' / name).read_bytes()).hexdigest()
                               for name in ('ui.py', 'shell_popup.py', 'selection_menu.py', 'pick_candidates.py')}
    STEPS = run()
    bpy.app.timers.register(tick, first_interval=.1)
except BaseException:
    finish(traceback.format_exc())
