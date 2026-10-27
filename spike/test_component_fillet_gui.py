"""OURS: real Blender event/RNA/GPU integration (cannot live in Rust).
Run SERIAL under Main's GUI lock, with Main's geometry/pure/selection/platform core
and OCCT environment. Require a FRESH temporary NARWHAL3D_SETTINGS_FILE and:
  NARWHAL3D_DEFER_FIRST_PRESENT=1 PYTHONDONTWRITEBYTECODE=1
  timeout 190s tools/blender-patched/blender --factory-startup \
    --enable-event-simulate --python-exit-code 1 \
    --python spike/test_component_fillet_gui.py --python launch/bootstrap.py
Test MUST precede bootstrap; unset inherited UI_READY/THEME/WELCOME_REQUEST.
180s watchdog; artifacts/component_fillet.json and fillet_handles.png. Main must
also reject asynchronous draw errors in the log. No fake context, picks or events:
event_simulate queues real Blender events; helpers only locate screen targets.
Fixtures/command starts use real APIs. Numeric EXEC follows an actual label click;
it tests the native callback, NOT typing into Blender's text editor. Draw observers
forward to native code. Fresh-process save/reload and rational-input fidelity are
covered by backend suites, not repeated here. GUI execution/mutation RED runs are
Main's responsibility: regress component autoaccept, endpoint setter, cached-line
draw, and invalid-candidate clearing separately; each must fail, then restore.
"""
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts'
START = time.monotonic()
RESULT = {'status': 'running', 'checks': [], 'input': 'Blender event_simulate'}
POSITION = (0, 0)
FINISH_LOCK = threading.Lock()


def check(ok, label):
    RESULT['checks'].append({'ok': bool(ok), 'label': label})
    print(f"COMPONENT_FILLET {'PASS' if ok else 'FAIL'}: {label}", flush=True)
    if not ok:
        raise AssertionError(label)


def finish(code, error=None):
    if not FINISH_LOCK.acquire(blocking=False):
        os._exit(1)
    RESULT.update(status='failed' if code else 'passed', error=error,
                  elapsed_s=time.monotonic() - START)
    try:
        OUT.mkdir(exist_ok=True)
        (OUT / 'component_fillet.json').write_text(json.dumps(RESULT, indent=2))
        print('COMPONENT_FILLET_RESULT ' + json.dumps(RESULT), flush=True)
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)  # Blender swallows exceptions from timers.


def watchdog():
    time.sleep(180)
    finish(1, '180s watchdog expired')  # No RNA access on this thread.


def window():
    return bpy.context.window_manager.windows[0]


def center(box):
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def event(key, value, chord=False):
    window().event_simulate(type=key, value=value, x=POSITION[0], y=POSITION[1],
                            ctrl=chord, shift=chord)


def move(point):
    global POSITION
    POSITION = tuple(round(v) for v in point)
    event('MOUSEMOVE', 'NOTHING')
    yield .15


def tap(key, point=None, chord=False):
    if point is not None:
        yield from move(point)
    event(key, 'PRESS', chord)
    yield .08
    event(key, 'RELEASE', chord)
    yield .25


def wait_for(predicate, label, seconds=10):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError('Timed out: ' + label)
        yield .1
    check(True, label)


def document():
    return {obj.Attributes.Name: json.dumps(obj.Geometry.Encode(), sort_keys=True)
            for obj in st.model.file.Objects}


def same_geometry_after_undo(snapshot):
    """Full BRep geometry/user strings, excluding only archive copy bookkeeping.

    MEASURED: Undo's three copies change ON_UserStringList.m_userdata_copycount
    (3 -> 6) and its CRC, while all face/surface/edge/vertex archives are identical.
    Comparing only volume or recipes would miss real shape changes.
    """
    now = {o.Attributes.Name: o.Geometry for o in st.model.file.Objects}
    if set(now) != set(snapshot):
        return False
    def archive(geom):
        return json.dumps(geom.Encode(), sort_keys=True)
    def brep(geom):
        return (geom.IsValid, geom.IsSolid, tuple(geom.GetUserStrings()),
                tuple((face.OrientationIsReversed, archive(face.DuplicateFace(False))) for face in geom.Faces),
                tuple(archive(surface) for surface in geom.Surfaces),
                tuple(archive(edge) for edge in geom.Edges),
                tuple(archive(vertex) for vertex in geom.Vertices))
    for name, encoded in snapshot.items():
        original = r3.CommonObject.Decode(json.loads(encoded))
        current = now[name]
        if isinstance(original, r3.Brep):
            if not isinstance(current, r3.Brep) or brep(original) != brep(current):
                return False
        elif archive(current) != encoded:
            return False
    return True


def frame(point, distance):
    from mathutils import Vector
    ui.set_active_pane(AREA)
    SPACE.region_3d.view_location = Vector(point)
    SPACE.region_3d.view_distance = distance
    SPACE.region_3d.update()
    ui._tag_redraw_viewports()


def target(key, points):
    points = [(p.X, p.Y, p.Z) if hasattr(p, 'X') else tuple(p) for p in points]
    # Use interior samples, not endpoints shared by several selectable parts.
    for fraction in (.5, .37, .63):
        offset = fraction * (len(points) - 1)
        i = min(int(offset), len(points) - 2)
        world = tuple(points[i][j] + (offset-i)*(points[i+1][j]-points[i][j]) for j in range(3))
        p = ui._project_2d(REGION, SPACE.region_3d, world)
        if p is None:
            continue
        x, y = round(p[0]), round(p[1])
        print('PICK_PROBE', key, world, (x,y), (REGION.width,REGION.height),
              ui.pick_subobject(AREA, SPACE, REGION, x, y), flush=True)
        if 55 < x < REGION.width-55 and 55 < y < REGION.height-55:
            if ui.pick_subobject(AREA, SPACE, REGION, x, y) == key:
                return REGION.x+x, REGION.y+y
    raise AssertionError(f'No unobstructed projected interior target: {key}')


def segment(owner, index):
    key = (owner, 'Segment', index)
    points = next(points for k, points, _ in ui._subobject_candidates((owner,)) if k == key)
    return target(key, points)


def handle(cmd, endpoint):
    rows = [r for r in ui.edge_handles.boxes() if r['cmd'] is cmd and r['id'][2] == endpoint
            and REGION.x < center(r['grip'])[0] < REGION.x+REGION.width
            and REGION.y < center(r['grip'])[1] < REGION.y+REGION.height]
    check(len(rows) == 1, f'one drawn Top handle for endpoint {endpoint}')
    return rows[0]


def drag(cmd, endpoint, amount):
    row = handle(cmd, endpoint)
    before = dict(cmd._edge_radii)
    yield from move(center(row['grip']))
    start = POSITION
    end = tuple(round(start[i] + row['axis'][i]*row['pixels_per_unit']*amount) for i in range(2))
    event('LEFTMOUSE', 'PRESS')
    yield .12
    yield from move(end)
    event('LEFTMOUSE', 'RELEASE')
    yield .25
    delta = sum((end[i]-start[i])*row['axis'][i] for i in range(2))/row['pixels_per_unit']
    pair = cmd._edge_radii[row['id'][1]]
    expected = list(before[row['id'][1]])
    for i in ((0, 1) if endpoint is None else (endpoint,)):
        expected[i] += delta
    check(delta > 0 and all(abs(a-b) < 1e-7 for a, b in zip(pair, expected)),
          f'real drag applies measured delta {delta:g}, only to intended radii')


def numeric(cmd, endpoint, value=None):
    calls, draw = [], ui.NARWHAL_OT_edge_radius.draw
    def observe(operator, context):
        calls.append(operator.value)
        return draw(operator, context)
    before = dict(cmd._edge_radii)
    row = handle(cmd, endpoint)
    with patch.object(ui.NARWHAL_OT_edge_radius, 'draw', observe):
        yield from tap('LEFTMOUSE', center(row['label']))
        yield from wait_for(lambda: bool(calls), 'actual radius label opens native dialog')
        check(ui.edge_handles._numeric['id'] == row['id'], 'label captures the intended stable handle id')
        if value is not None:
            check(bpy.ops.narwhal.edge_radius(value=str(value)) == {'FINISHED'}, 'native numeric execute callback')
        yield from tap('ESC')
        calls.clear()
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        check(not calls, 'Escape closes native numeric dialog')
    check(st.active_command is cmd, 'dialog Escape does not cancel FilletEdge')
    if value is None:
        check(cmd._edge_radii == before, 'numeric dialog cancellation preserves radii')


def cached_draw(cmd):
    lines, curves, candidate = cmd.preview_lines(), cmd.preview_section_curves(), cmd._candidate
    expected = [tuple(p for pair in zip(line, line[1:]) for p in pair) for line in lines]
    submitted, rebuilt = [], []
    batch, cursor = ui._line_batch, ui._preview_cursor
    def observe(shader, aa, coords):
        if tuple(map(tuple, coords)) in expected:
            submitted.append(ui.pane_label(bpy.context.area))
        return batch(shader, aa, coords)
    def rebuild(*_args, **_kwargs):
        rebuilt.append(True)
        raise AssertionError('draw must not rebuild kernel geometry')
    try:
        ui._preview_cursor = None  # Deliberate cursor-absence fixture, not a simulated pick.
        with patch.object(ui, '_line_batch', observe), patch.object(cmd, '_refresh_sections', rebuild), \
                patch.object(cmd, '_refresh_candidate', rebuild):
            ui._tag_redraw_viewports()
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
    finally:
        ui._preview_cursor = cursor
    check(set(submitted) == {'Top', 'Front', 'Right', 'Perspective'} and not rebuilt,
          'real draw submits cached section lines in all panes without cursor or rebuild')
    check(cmd.preview_lines() is lines and cmd.preview_section_curves() is curves and cmd._candidate is candidate,
          'draw getters retain cached geometry identities')


def smooth_master(geom, anchors, radii):
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_BSplineSurface
    shape = kernel.master_of(geom)
    check(shape is not None and ob.is_valid(shape) and ob.is_airtight(shape), 'valid airtight analytic fillet master')
    check(ob.face_types(shape) == {'PLANE': 6, 'BSPLINE': 1}, 'variable fillet is one curved face, not facets')
    surface = next(BRepAdaptor_Surface(f) for f in ob.unique_faces(shape)
                   if BRepAdaptor_Surface(f).GetType() == GeomAbs_BSplineSurface)
    check(surface.UDegree() >= 2 and surface.VDegree() >= 2, 'variable blend smooth in both surface directions')
    vertices = [BRep_Tool.Pnt_s(v).Coord() for v in ob._unique_vertices(shape)]
    axis = [(anchors[1][i]-anchors[0][i])/math.dist(*anchors) for i in range(3)]
    for anchor, radius in zip(anchors, radii):
        cap = [p for p in vertices if abs(sum((p[i]-anchor[i])*axis[i] for i in range(3))) < 1e-6]
        check(bool(cap) and abs(min(math.dist(p, anchor) for p in cap)-radius) < 1e-6,
              'master endpoint contact measures requested radius')
    volume = ob.volume(shape)
    check(0 < volume < 1000, 'fillet removes material from the 10-unit box')
    return volume


def run():
    global AREA, REGION, SPACE, st
    yield from wait_for(lambda: os.environ.get('NARWHAL3D_UI_READY') == '1'
                       and not ui._viewports_in_progress and ui._viewport_count() == 4
                       and ui.is_input_active(), 'bootstrap ready with four panes and input', 45)
    st = ui.app_state
    check(not document(), 'refuse nonempty startup document')
    check(ob.available(), 'OCCT available; no skipped geometry checks')
    AREA = ui._pane_by_label('Top')
    REGION, SPACE = ui._window_region(AREA), AREA.spaces.active
    ui.set_gumball(False)
    frame((5, 1, 0), 24)
    rail, profile = r3.PolyCurve(), r3.PolyCurve()
    def line(a, b):
        return r3.LineCurve(r3.Point3d(*a), r3.Point3d(*b))
    for part in (line((0, 0, 0), (10, 0, 0)),
                 r3.Arc(r3.Point3d(10, 0, 0), r3.Point3d(12, 2, 0), r3.Point3d(10, 4, 0)).ToNurbsCurve(),
                 line((10, 4, 0), (0, 4, 0))):
        check(rail.Append(part), 'joined rail fixture append')
    check(profile.Append(line((0, 0, 0), (0, 2, 0))) and profile.Append(r3.Arc(
        r3.Point3d(0, 2, 0), r3.Point3d(-1, 3, 0), r3.Point3d(-2, 2, 0)).ToNurbsCurve()), 'branched profile fixture')
    _, owner = st.ctx.add_geometry(rail, 'Rail')
    _, section = st.ctx.add_geometry(profile, 'Section')
    sources = document()
    st.feed_line('SelNone')
    yield .4
    st.feed_line('Sweep1')
    cmd = st.active_command
    yield from tap('LEFTMOUSE', segment(owner, 0), True)
    check(cmd.args == [('segment', owner, 0)] and cmd.step_index == 1, 'CtrlShift rail autoaccepts exact segment')
    yield from tap('LEFTMOUSE', segment(section, 0), True)
    check(st.active_command is cmd and cmd.step_index == 1 and len(cmd.args) == 1
          and ui.ordered_selected_subobjects() == [(section, 'Segment', 0)], 'multi section stays pending until Enter')
    yield from tap('RET')
    check(st.active_command is None and cmd.args[-1] == [('segment', section, 0)], 'Enter accepts section reference, not parent')
    added = set(document())-set(sources)
    check(len(added) == 1 and all(document()[n] == v for n, v in sources.items()), 'Sweep creates one surface, leaves parents unchanged')
    result = st.ctx.geom_by_name(added.pop())
    shape = kernel.master_of(result)
    check(isinstance(result, r3.Brep) and shape is not None and not kernel.honesty_problem(result), 'Sweep retains exact surface master')
    bounds = ob.tight_bbox(shape)
    check(all(abs(a-b) < 1e-5 for a, b in zip((*bounds[0], *bounds[1]), (0, 0, 0, 10, 2, 0))),
          'Sweep shape spans only chosen segments; curved parent fallback would differ')
    st.feed_line('Undo')
    st.feed_line('SelNone')
    st.feed_line('Sweep2')
    cmd = st.active_command
    for step, index in enumerate((2, 0), 1):
        yield from tap('LEFTMOUSE', segment(owner, index), True)
        check(cmd.step_index == step and cmd.args == [('segment', owner, i) for i in (2, 0)[:step]],
              'Sweep2 accepts distinct same-owner rails sequentially in pick order')
    for index in (1, 0):
        yield from tap('LEFTMOUSE', segment(section, index), True)
    check(st.active_command is cmd and cmd.step_index == 2 and len(cmd.args) == 2
          and ui.ordered_selected_subobjects() == [(section, 'Segment', i) for i in (1, 0)],
          'multi-selection retains reverse-index user pick order without autoaccept')
    yield from tap('LEFTMOUSE', segment(section, 0), True)
    check(cmd.step_index == 2 and ui.ordered_selected_subobjects() == [(section, 'Segment', 1)],
          'CtrlShift deselection does not advance the pending prompt')
    yield from tap('ESC')
    check(st.active_command is None and document() == sources, 'Sweep2 confirmation-only cancellation preserves sources')
    _, box = st.ctx.add_geometry(bridge.make_box((30, 0, 0), (40, 10, 0), 10), 'Box')
    frame((35, 5, 0), 25)
    st.feed_line('SelNone')
    yield .4
    parts = ui._brep_parts_all()[box]
    edge, points = next((i, p) for i, p in parts['edges'] if p and all(abs(v[2]-10) < 1e-7 for v in p))
    click = target((box, 'Edge', edge), points)
    before, history = document(), st.ctx.undo.undo_names()
    st.feed_line('FilletEdge')
    cmd = st.active_command
    yield from tap('LEFTMOUSE', click, True)
    check(cmd._edges == [edge] and not ui.selected_display_names() and cmd.phase == cmd.SELECT_EDGES
          and len(cmd.handle_records()) == 1 and cmd.preview_lines() and not cmd.opt['Preview'],
          'CtrlShift adds box edge, one selection handle and PreviewNo section arcs')
    check(all(c.ToNurbsCurve().IsRational and c.Degree == 2 for c in cmd.preview_section_curves()), 'section previews are exact rational arcs')
    yield from drag(cmd, None, .2)
    check(document() == before and st.ctx.undo.undo_names() == history, 'scalar drag leaves original document intact')
    yield from tap('RET')
    check(st.active_command is cmd and cmd.phase == cmd.EDIT_HANDLES and len(cmd.handle_records()) == 2,
          'first Enter exposes two independent endpoint handles')
    yield from drag(cmd, 0, .25)
    untouched = cmd._edge_radii[edge][0]
    yield from numeric(cmd, 1, .65)
    check(cmd._edge_radii[edge] == (untouched, .65), 'numeric callback changes only other endpoint')
    yield from numeric(cmd, 1)
    cached_draw(cmd)
    check(document() == before and st.ctx.undo.undo_names() == history, 'all handle editing leaves document/history untouched')
    anchors, radii = cmd._edge_endpoints[edge], cmd._edge_radii[edge]
    volume = smooth_master(cmd._candidate, anchors, radii)
    OUT.mkdir(exist_ok=True)
    check(bpy.ops.screen.screenshot(filepath=str(OUT / 'fillet_handles.png')) == {'FINISHED'}, 'endpoint screenshot saved')
    yield from tap('RET')
    changed = set(document())-set(before)
    check(st.active_command is None and len(changed) == 1 and len(st.ctx.undo.undo_names()) == len(history)+1,
          'second Enter commits exactly one undo step')
    committed = smooth_master(st.ctx.geom_by_name(changed.pop()), anchors, radii)
    check(abs(committed-volume) < 1e-8, 'committed master matches candidate volume')
    st.feed_line('Undo')
    check(same_geometry_after_undo(before) and st.ctx.undo.undo_names() == history,
          'Undo restores names, complete trimmed geometry, user strings and history')
    before = document()  # Preserve the restored archive's new copy bookkeeping too.
    for invalid in (True, False):
        st.feed_line('SelNone')
        st.feed_line('FilletEdge')
        cmd = st.active_command
        yield from tap('LEFTMOUSE', click, True)
        yield from tap('RET')
        check(cmd._candidate is not None, 'fresh valid candidate before rejection/cancellation')
        if invalid:
            yield from numeric(cmd, 1, 10000)
            check(cmd._candidate is None and cmd._candidate_error, 'invalid native radius callback clears stale candidate')
        yield from tap('RET' if invalid else 'ESC')
        check(st.active_command is None and document() == before and st.ctx.undo.undo_names() == history,
              'invalid Enter cannot commit stale geometry' if invalid else 'Escape cancels without geometry/history changes')
    RESULT['radii'], RESULT['volume'] = radii, committed


def tick():
    try:
        with bpy.context.temp_override(window=window()):
            return next(STEPS)
    except StopIteration:
        finish(0)
    except BaseException:
        finish(1, traceback.format_exc())


threading.Thread(target=watchdog, daemon=True).start()
try:
    override = os.environ.get('NARWHAL3D_SETTINGS_FILE', '')
    check(bool(override), 'require temporary NARWHAL3D_SETTINGS_FILE before bootstrap')
    settings = Path(override).expanduser()
    check(settings.is_absolute() and settings.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
          and not settings.exists() and not settings.is_symlink(), 'refuse existing/non-temporary/symlink settings')
    check('narwhal3d' not in sys.modules and not os.environ.get('NARWHAL3D_UI_READY'), 'test must precede bootstrap')
    check(os.environ.get('NARWHAL3D_DEFER_FIRST_PRESENT') == '1' and not os.environ.get('NARWHAL3D_THEME')
          and not os.environ.get('NARWHAL3D_WELCOME_REQUEST'), 'require isolated deferred launch environment')
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    import bpy
    import rhino3dm as r3
    from narwhal3d import ui, bridge, kernel
    from narwhal3d.kernel import occt_backend as ob
    check(not bpy.app.background, 'real GUI required')
    STEPS = run()
    bpy.app.timers.register(tick, first_interval=.1)
except BaseException:
    finish(1, traceback.format_exc())
