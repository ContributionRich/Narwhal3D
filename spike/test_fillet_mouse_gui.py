"""OURS: focused real-window FilletEdge radius-input regression (Python needs bpy).

Public standalone harness, loaded BEFORE launch/bootstrap.py, never under -b:
  blender --factory-startup --enable-event-simulate --python-exit-code 1 \
    --python spike/test_fillet_mouse_gui.py --python launch/bootstrap.py
Require a fresh absolute temporary NARWHAL3D_SETTINGS_FILE, isolated HOME/XDG dirs,
NARWHAL3D_DEFER_FIRST_PRESENT=1, PYTHONDONTWRITEBYTECODE=1, and the normal native
core/OCCT environment. Unset inherited UI_READY/THEME/WELCOME_REQUEST. Serialize
under the integration harness's GUI lock; bound the process to 190s (180s watchdog).
No private research imports, personal paths, external CAD process, native builds or fixtures.
Output: FILLET_MOUSE_RESULT JSON marker + artifacts/fillet_mouse/*.json and PNGs.
Reject nonzero exit, missing/failed marker AND asynchronous tracebacks in the log.
Main owns run_tests.sh/public allowlist integration. The existing component GUI
suite separately protects native label-popup EXEC and component-selection contracts.

Only fixture setup/command starts use APIs. All tested edits, confirmations, Undo,
cancellation and options use real Window.event_simulate mouse/keyboard text events.
Draw observation forwards the original _draw_text, captures only the actual command
bar prompt baseline (not history/getters), and checks text fits the visible region.
Optional FILLET_MOUSE_MUTATION=prompt|begin|release installs ONE process-local
negative control. Never modify production files; rerun without it for restored GREEN.
"""
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'fillet_mouse'
START = time.monotonic()
RESULT = {'status': 'running', 'checks': [], 'states': [], 'events': [],
          'mutation': os.environ.get('FILLET_MOUSE_MUTATION', ''),
          'input': 'real Window.event_simulate; no numeric EXEC or radius setter calls'}
POSITION = (0, 0)
PAINT = []
FINISH_LOCK = threading.Lock()


def check(ok, label):
    RESULT['checks'].append({'ok': bool(ok), 'label': label})
    print('FILLET_MOUSE', 'PASS' if ok else 'FAIL', label, flush=True)
    if not ok:
        raise AssertionError(label)


def finish(code, error=None):
    if not FINISH_LOCK.acquire(blocking=False):
        os._exit(1)
    RESULT.update(status='failed' if code else 'passed', error=error,
                  elapsed_s=time.monotonic() - START)
    try:
        OUT.mkdir(exist_ok=True)
        (OUT / 'result.json').write_text(json.dumps(RESULT, indent=2, default=str))
        print('FILLET_MOUSE_RESULT ' + json.dumps(RESULT, default=str), flush=True)
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)  # Timer exceptions alone do not fail Blender.


def window():
    return bpy.context.window_manager.windows[0]


def event(key, value, text=''):
    RESULT['events'].append({'type': key, 'value': value, 'text': text,
                             'position': POSITION, 'time_s': time.monotonic() - START})
    window().event_simulate(type=key, value=value, x=POSITION[0], y=POSITION[1],
                            **({'unicode': text} if text else {}))


def move(point):
    global POSITION
    POSITION = tuple(round(v) for v in point)
    event('MOUSEMOVE', 'NOTHING')
    yield .12


def tap(key, point=None):
    if point is not None:
        yield from move(point)
    event(key, 'PRESS')
    yield .06
    event(key, 'RELEASE')
    yield .25


def type_text(text):
    check(not st.input_buffer, 'keyboard starts with empty actual input buffer')
    digits = ('ZERO', 'ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX', 'SEVEN', 'EIGHT', 'NINE')
    for char in text:
        key = digits[int(char)] if char.isdigit() else char.upper()
        event(key, 'PRESS', char)
        yield .025
        event(key, 'RELEASE')
        yield .025
    check(st.input_buffer == text, 'real keyboard populated buffer: ' + text)


def line(text, accept='RET'):
    yield from type_text(text)
    yield from tap(accept)


def center(rect):
    return (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2


def document():
    return {obj.Attributes.Name: json.dumps(obj.Geometry.Encode(), sort_keys=True)
            for obj in st.model.file.Objects}


def semantic_document():
    # Undo copies alter archive UserStringList copy-count/CRC, NOT trimmed geometry.
    # Preserve every face/surface/edge/vertex archive and both kinds of user strings.
    def archive(geom):
        return json.dumps(geom.Encode(), sort_keys=True)
    rows = []
    for obj in st.model.file.Objects:
        geom, attr = obj.Geometry, obj.Attributes
        rows.append((attr.Name, str(attr.Id), attr.LayerIndex, tuple(attr.GetUserStrings()),
                     geom.IsValid, geom.IsSolid, tuple(geom.GetUserStrings()),
                     tuple((f.OrientationIsReversed, archive(f.DuplicateFace(False))) for f in geom.Faces),
                     tuple(archive(s) for s in geom.Surfaces),
                     tuple(archive(e) for e in geom.Edges),
                     tuple(archive(v) for v in geom.Vertices)))
    return rows


def unchanged(label):
    check(document() == BEFORE and st.ctx.undo.undo_names() == HISTORY, label)


def cache(cmd):
    return tuple(getattr(cmd, name) for name in cmd._handle_cache_fields)


def observe_text(x, y, text, *args, **kwargs):
    result = ORIGINAL_DRAW(x, y, text, *args, **kwargs)
    frame = sys._getframe(1)
    if frame.f_code is ui.draw_command_bar.__code__ and y == frame.f_locals.get('prompt_y'):
        if x == frame.f_locals.get('pad_x'):
            PAINT.append([])
        if PAINT:
            size = args[0] if args else kwargs.get('size', 13.0)
            PAINT[-1].append({'text': text, 'x': x, 'y': y,
                              'right': x + ui._text_width(text, size),
                              'width': bpy.context.region.width,
                              'height': bpy.context.region.height})
    return result


def painted(label, expected, forbidden=(), shot=False):
    PAINT.clear()
    ui.redraw_cmdbar()
    yield .4
    rows = PAINT[-1] if PAINT else []
    text = ''.join(row['text'] for row in rows)
    cmd = st.active_command
    RESULT['states'].append({'label': label, 'paint': rows, 'rendered_prompt': text,
                             'command': getattr(cmd, 'name', None),
                             'phase': getattr(cmd, 'phase', None),
                             'input_active': bool(cmd and cmd.handle_input_active()),
                             'radii': dict(getattr(cmd, '_edge_radii', {})),
                             'candidate': getattr(cmd, '_candidate', None) is not None,
                             'candidate_error': getattr(cmd, '_candidate_error', None),
                             'input_buffer': st.input_buffer, 'undo': st.ctx.undo.undo_names(),
                             'history': st.history[-4:]})
    if shot:
        check(bpy.ops.screen.screenshot(filepath=str(OUT / (label + '.png'))) == {'FINISHED'},
              label + ': visible PNG saved')
    check(bool(rows) and expected in text and not any(s in text for s in forbidden),
          label + ': actual painted prompt: ' + expected)
    check(all(0 <= r['x'] < r['right'] <= r['width'] and 0 <= r['y'] < r['height']
              for r in rows if r['text']), label + ': painted prompt fits visible region')


def handle(cmd, endpoint=0):
    rows = [row for row in ui.edge_handles.boxes()
            if row['cmd'] is cmd and row['id'][2] == endpoint
            and REGION.x + 20 < center(row['grip'])[0] < REGION.x + REGION.width - 20
            and REGION.y + 20 < center(row['grip'])[1] < REGION.y + REGION.height - 20]
    check(len(rows) == 1, 'one actual drawn Top grip for endpoint ' + str(endpoint))
    return rows[0]


def edge_targets():
    choices = []
    for index, points in ui._brep_parts_all()[BOX]['edges']:
        if not points or not all(abs(p[2] - 10) < 1e-7 for p in points):
            continue
        world = tuple((points[0][k] + points[-1][k]) / 2 for k in range(3))
        screen = ui._project_2d(REGION, SPACE.region_3d, world)
        if screen is None:
            continue
        x, y = map(round, screen)
        if (55 < x < REGION.width - 55 and 55 < y < REGION.height - 55
                and ui.pick_brep_part(AREA, SPACE, REGION, x, y) == (BOX, 'Edge', index)):
            choices.append((index, (REGION.x + x, REGION.y + y), world))
    check(len(choices) >= 2, 'two unambiguous projected top-edge targets')
    a = choices[0]
    b = max(choices[1:], key=lambda row: math.dist(row[2], a[2]))
    RESULT['edge_targets'] = [a, b]
    return a, b


def start_single():
    check(st.active_command is None, 'start fixture command only while idle')
    st.start_command('FilletEdge')
    yield .25
    cmd = st.active_command
    yield from tap('LEFTMOUSE', A[1])
    check(cmd._edges == [A[0]] and cmd.phase == cmd.SELECT_EDGES,
          'single plain LMB selects exactly one edge without autoaccept')
    yield from tap('RIGHTMOUSE', EMPTY)
    check(st.active_command is cmd and cmd.phase == cmd.EDIT_HANDLES and cmd._candidate is not None,
          'first actual RMB enters handle editing with valid cached candidate')
    unchanged('first RMB leaves original document and undo intact')
    return cmd


def radius_click(cmd, endpoint=0):
    row = handle(cmd, endpoint)
    yield from tap('LEFTMOUSE', center(row['grip']))
    check(st.active_command is cmd and cmd.handle_input_active() and ui.edge_handles._drag is None,
          'grip click-release retains numeric subedit, not pointer capture')
    return row


def held_drag(cmd, amount=.8):
    row = handle(cmd)
    before = cmd._edge_radii[A[0]]
    yield from move(center(row['grip']))
    start = POSITION
    event('LEFTMOUSE', 'PRESS')
    yield .15
    travel = max(row['pixels_per_unit'] * amount, ui._fpx(4) * 5)
    previous = before[0]
    for i in range(1, 6):
        yield from move(tuple(start[k] + row['axis'][k] * travel * i / 5 for k in range(2)))
        pair = cmd._edge_radii[A[0]]
        RESULT.setdefault('held_moves', []).append({'position': POSITION, 'pair': pair, 'step': i})
        check(pair[0] > previous and pair[1] == before[1],
              'held MOUSEMOVE ' + str(i) + ': live radius changes, opposite endpoint unchanged')
        previous = pair[0]
    delta = sum((POSITION[k] - start[k]) * row['axis'][k] for k in range(2)) / row['pixels_per_unit']
    check(math.dist(start, POSITION) >= ui._fpx(3)
          and abs(cmd._edge_radii[A[0]][0] - before[0] - delta) < 1e-7,
          'drag exceeds three logical pixels and matches measured projected displacement')
    check(cmd.handle_input_active() and ui.edge_handles._drag is not None,
          'held drag retains radius subedit and pointer capture')


def single_case():
    cmd = yield from start_single()
    yield from painted('first_rmb', 'Select fillet handle to edit.', ('NextRadius',), True)
    yield from radius_click(cmd)
    yield from painted('grip_click', 'Select new fillet radius <1.000>', ('NextRadius',), True)
    yield from line('3')
    check(cmd._edge_radii[A[0]] == (3., 1.) and not cmd.handle_input_active()
          and st.active_command is cmd and cmd._candidate is not None,
          '3 + RET changes only endpoint zero and does not commit outer command')
    unchanged('typed radius leaves document/undo untouched')
    yield from held_drag(cmd)
    yield from painted('drag_held', 'Select new fillet radius <3.000>', ('NextRadius',), True)
    dragged = cmd._edge_radii[A[0]]
    event('LEFTMOUSE', 'RELEASE')
    yield .3
    check(st.active_command is cmd and not cmd.handle_input_active() and ui.edge_handles._drag is None
          and cmd._edge_radii[A[0]] == dragged,
          'drag release ends adjustment only, preserving live radius and outer command')
    unchanged('drag release cannot commit document or undo')
    yield from painted('drag_released', 'Select fillet handle to edit.', ('NextRadius', 'Select new'))

    row = handle(cmd)
    yield from move(center(row['grip']))
    start = POSITION
    event('LEFTMOUSE', 'PRESS')
    yield .12
    yield from move((start[0] + 1, start[1]))
    event('LEFTMOUSE', 'RELEASE')
    yield .25
    check(cmd.handle_input_active() and cmd._edge_radii[A[0]] == dragged,
          'one-pixel click jitter preserves radius and stays numeric')
    yield from line('2', 'NUMPAD_ENTER')
    check(cmd._edge_radii[A[0]] == (2., 1.) and st.active_command is cmd
          and not cmd.handle_input_active(), 'NUMPAD_ENTER consumes actual radius buffer, not outer commit')
    yield from radius_click(cmd)
    yield from line('3', 'SPACE')
    check(cmd._edge_radii[A[0]] == (3., 1.) and st.active_command is cmd
          and not cmd.handle_input_active(), 'SPACE consumes actual radius buffer, not outer commit')
    yield from radius_click(cmd)
    check(not st.input_buffer, 'empty default radius buffer before RMB')
    yield from tap('RIGHTMOUSE', EMPTY)
    check(st.active_command is cmd and not cmd.handle_input_active() and cmd._edge_radii[A[0]] == (3., 1.),
          'empty RMB accepts radius default and closes only subedit')
    unchanged('keyboard accepts and empty RMB never commit outer command')
    yield from tap('RIGHTMOUSE', EMPTY)
    check(st.active_command is None and len(list(st.model.file.Objects)) == 1
          and len(st.ctx.undo.undo_names()) == len(HISTORY) + 1, 'next RMB commits once with exactly one undo record')
    geom = next(iter(st.model.file.Objects)).Geometry
    shape = kernel.master_of(geom)
    check(shape is not None and ob.is_valid(shape) and ob.is_airtight(shape)
          and ob.face_count(shape) == 7 and ob.face_types(shape) == {'PLANE': 6, 'BSPLINE': 1}
          and not kernel.honesty_problem(geom), 'committed variable fillet is seven exact faces, one BSPLINE, airtight')
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_BSplineSurface
    surface = next(BRepAdaptor_Surface(f) for f in ob.unique_faces(shape)
                   if BRepAdaptor_Surface(f).GetType() == GeomAbs_BSplineSurface)
    check(surface.UDegree() >= 2 and surface.VDegree() >= 2, 'variable blend is smooth in both directions')
    RESULT['final_geometry'] = {'faces': ob.face_count(shape), 'types': ob.face_types(shape),
                                'airtight': ob.is_airtight(shape), 'radii': cmd._edge_radii[A[0]],
                                'volume': ob.volume(shape)}
    yield from painted('committed', 'Command:', shot=True)
    yield from line('Undo')
    check(semantic_document() == ORIGINAL and st.ctx.undo.undo_names() == HISTORY,
          'one keyboard Undo restores full trimmed box, identity, user data and undo stack')


def escape_case():
    cmd = yield from start_single()
    original_pair, original_cache = cmd._edge_radii[A[0]], cache(cmd)
    yield from held_drag(cmd)
    check(cmd._candidate is not original_cache[0], 'Escape test has genuinely changed candidate cache')
    yield from tap('ESC')  # LMB is still held here.
    check(st.active_command is cmd and not cmd.handle_input_active() and ui.edge_handles._drag is None
          and cmd._edge_radii[A[0]] == original_pair
          and all(a is b for a, b in zip(cache(cmd), original_cache)),
          'Escape while held restores original radius and every cache identity, not outer command')
    event('LEFTMOUSE', 'RELEASE')
    yield .25
    unchanged('release after Escape cannot apply pending dragged radius')
    yield from painted('held_escape', 'Select fillet handle to edit.', ('Select new',), True)
    yield from tap('ESC')
    check(st.active_command is None and semantic_document() == ORIGINAL,
          'second Escape cancels outer command and preserves original document')
    unchanged('outer Escape creates no undo record')


def invalid_case():
    cmd = yield from start_single()
    for invalid in ('oops', '0', '100'):
        yield from radius_click(cmd)
        yield from line(invalid)
        check(st.active_command is cmd and cmd.handle_input_active() and cmd._candidate is None
              and bool(cmd._candidate_error), invalid + ': invalid input clears stale candidate and remains correctable')
        unchanged(invalid + ': rejection preserves original document/undo')
        yield from painted('invalid_' + invalid, 'Select new fillet radius', shot=True)
        yield from type_text(invalid)
        yield from tap('RIGHTMOUSE', EMPTY)
        check(st.active_command is cmd and cmd.handle_input_active() and cmd._candidate is None,
              invalid + ': RMB with invalid buffer cannot commit stale candidate')
        unchanged(invalid + ': RMB rejection leaves document unchanged')
        yield from line('2')
        check(st.active_command is cmd and not cmd.handle_input_active() and cmd._candidate is not None
              and cmd._edge_radii[A[0]] == (2., 1.), invalid + ': keyboard correction rebuilds valid endpoint-only candidate')
    yield from tap('ESC')
    unchanged('cancel corrected invalid-input case without committing')


def two_edge_case():
    st.start_command('FilletEdge')
    yield .25
    cmd = st.active_command
    yield from tap('LEFTMOUSE', A[1])
    yield from line('NextRadius')
    yield from painted('next_radius', 'Fillet radius <1>', ('ChainEdges',))
    yield from line('2')
    yield from tap('LEFTMOUSE', B[1])
    check(cmd._edges == [A[0], B[0]] and cmd._edge_radii == {A[0]: (1., 1.), B[0]: (2., 2.)}
          and cmd.phase == cmd.SELECT_EDGES, 'two plain clicks accumulate; NextRadius affects only new edge')
    yield from painted('two_edges', 'Select edges to fillet.', shot=True)
    yield from tap('RIGHTMOUSE', EMPTY)
    check(st.active_command is cmd and cmd.phase == cmd.EDIT_HANDLES, 'two-edge first RMB only enters edit phase')
    unchanged('two-edge selection and NextRadius leave document intact')
    yield from tap('ESC')
    unchanged('two-edge Escape leaves original fixture and undo intact')


def install_mutation():
    mode = RESULT['mutation']
    if mode == 'prompt':
        source = inspect.getsource(ui._prompt_segments_for)
        old = '    provider = getattr(cmd, "prompt_segments", None)\n    if callable(provider):\n        return provider()\n'
        if source.count(old) != 1:
            raise RuntimeError('Prompt mutation target changed')
        exec(compile(source.replace(old, ''), '<filletmouse-prompt-mutation>', 'exec'), ui.__dict__)
    elif mode == 'begin':
        from narwhal3d.solid_commands import _EdgeBevelCmd
        _EdgeBevelCmd.begin_handle_input = lambda self, handle_id, ctx: False
    elif mode == 'release':
        original = ui.edge_handles.handle_event
        def premature_commit(ev):
            drag = ui.edge_handles._drag
            commit = (drag is not None and drag['moved'] and ev.type == 'LEFTMOUSE' and ev.value == 'RELEASE')
            result = original(ev)
            if commit:
                ui.app_state.feed_line('')
            return result
        ui.edge_handles.handle_event = premature_commit
    elif mode:
        raise ValueError('Unknown process-local mutation: ' + mode)


def run():
    global st, AREA, REGION, SPACE, BOX, A, B, EMPTY, BEFORE, ORIGINAL, HISTORY
    deadline = time.monotonic() + 45
    while not (os.environ.get('NARWHAL3D_UI_READY') == '1' and not ui._viewports_in_progress
               and ui._viewport_count() == 4 and ui.is_input_active()):
        if time.monotonic() > deadline:
            raise RuntimeError('Bootstrap not ready within 45s')
        yield .15
    st = ui.app_state
    check(not list(st.model.file.Objects), 'refuse nonempty startup document')
    check(ob.available(), 'OCCT available; no skipped geometry assertions')
    check(ui.design.theme_name() == 'light', 'one isolated light theme')
    AREA = ui._pane_by_label('Top')
    REGION, SPACE = ui._window_region(AREA), AREA.spaces.active
    from mathutils import Vector
    ui.set_active_pane(AREA)
    ui.set_gumball(False)
    SPACE.region_3d.view_location = Vector((10, 10, 0))
    SPACE.region_3d.view_distance = 48
    SPACE.region_3d.update()
    box = bridge.make_box((0, 0, 0), (20, 20, 0), 10)
    box.SetUserString('fillet_mouse_fixture', 'preserve original user data')
    _, BOX = st.ctx.add_geometry(box, 'FilletMouseBox')
    st.feed_line('SelNone')
    ui._tag_redraw_viewports()
    yield .7
    A, B = edge_targets()
    EMPTY = (REGION.x + REGION.width * .5, REGION.y + REGION.height * .35)
    BEFORE, ORIGINAL, HISTORY = document(), semantic_document(), st.ctx.undo.undo_names()
    check(len(box.Faces) == 6 and box.IsSolid and box.IsValid, 'exact six-face 20x20x10 source box')
    install_mutation()
    for case in (single_case, escape_case, invalid_case, two_edge_case):
        RESULT['case'] = case.__name__
        yield from case()
        BEFORE = document()  # Undo changes only archive copy bookkeeping.
    check(st.active_command is None and semantic_document() == ORIGINAL, 'all cases leave original disposable document idle')


def tick():
    try:
        with bpy.context.temp_override(window=window()):
            return next(STEPS)
    except StopIteration:
        finish(0)
    except BaseException:
        finish(1, traceback.format_exc())


threading.Thread(target=lambda: (time.sleep(180), finish(1, '180s watchdog expired')), daemon=True).start()
try:
    override = os.environ.get('NARWHAL3D_SETTINGS_FILE', '')
    check(bool(override), 'require disposable preferences before bootstrap')
    settings = Path(override).expanduser()
    check(settings.is_absolute() and settings.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
          and not settings.exists() and not settings.is_symlink(), 'refuse existing/non-temporary/symlink settings')
    check('narwhal3d' not in sys.modules and not os.environ.get('NARWHAL3D_UI_READY'), 'test must precede bootstrap')
    check(os.environ.get('NARWHAL3D_DEFER_FIRST_PRESENT') == '1' and not os.environ.get('NARWHAL3D_THEME')
          and not os.environ.get('NARWHAL3D_WELCOME_REQUEST'), 'require isolated deferred launch environment')
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    import bpy
    from narwhal3d import ui, bridge, kernel
    from narwhal3d.kernel import occt_backend as ob
    check(not bpy.app.background, 'real GUI required')
    OUT.mkdir(exist_ok=True)
    ORIGINAL_DRAW = ui._draw_text
    ui._draw_text = observe_text
    RESULT['owned_pid'] = os.getpid()
    RESULT['source_sha256'] = {name: hashlib.sha256((ROOT / 'narwhal3d' / name).read_bytes()).hexdigest()
                               for name in ('ui.py', 'edge_handles.py', 'solid_commands.py')}
    STEPS = run()
    bpy.app.timers.register(tick, first_interval=.1)
except BaseException:
    finish(1, traceback.format_exc())
