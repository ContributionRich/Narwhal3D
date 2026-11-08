"""Disposable real-window hover/typed/click regression for owner report 121.

Use /var/tmp/workspace_dialogs_host/host.py --suite spike/test_report121_124_gui.py.
The host enforces isolated preferences, GUI lock, finite lifetime and 2 GiB cap.
"""
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ['HARNESS_OUT'])
RESULT = {'status': 'running', 'checks': [], 'facts': {}}
START = time.monotonic()
LOCK = threading.Lock()
WITNESS = ({'plane': 'End hover works when the CPlane has no intersection',
            'zoom': 'Near retains the visible crease interval across the near plane'}
           .get(os.environ.get('WORKSPACE_DIALOGS_MUTATION'),
                'positive typed 7 follows negative real mouse hover'))


def check(ok, label):
    RESULT['checks'].append({'ok': bool(ok), 'label': label})
    print('WORKSPACE_DIALOGS_REPORT', bool(ok), label, flush=True)
    if not ok:
        raise AssertionError(label)


def finish(code, error=None):
    if not LOCK.acquire(False):
        os._exit(1)
    RESULT.update(status='failed' if code else 'passed', error=error,
                  witness=WITNESS, elapsed_s=time.monotonic() - START)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'result.json').write_text(json.dumps(RESULT, indent=2, default=str))
    print('WORKSPACE_DIALOGS_RESULT', json.dumps(RESULT, default=str), flush=True)
    os._exit(code)


def send(kind, value='PRESS', x=0, y=0, text=''):
    win.event_simulate(type=kind, value=value, x=round(x), y=round(y),
                       **({'unicode': text} if text else {}))


def wait(predicate, label, seconds=30):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError('timeout: ' + label)
        yield .15


def move_to(point):
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    from mathutils import Vector
    q = location_3d_to_region_2d(region, space.region_3d, Vector(point))
    check(q is not None and 0 < q.x < region.width and 0 < q.y < region.height,
          'hover witness lies inside target viewport')
    xy = (region.x + q.x, region.y + q.y)
    send('MOUSEMOVE', 'NOTHING', *xy)
    yield .4
    return xy


def run():
    global win, region, space, area
    yield from wait(lambda: os.environ.get('NARWHAL3D_UI_READY') == '1'
                    and not ui._viewports_in_progress and ui.is_input_active(), 'bootstrap', 60)
    st = ui.app_state
    win = bpy.context.window_manager.windows[0]
    check(not list(st.model.file.Objects), 'disposable document starts empty')
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D'
                and ui.pane_label(a) == 'Front')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    space = area.spaces.active
    space.region_3d.view_distance = 35
    space.region_3d.view_location = (0, 0, 0)
    ui._snap_settings.enabled.clear()
    _id, name = st.ctx.add_geometry(bridge.make_circle((0, 0, 0), 2), 'Profile')
    yield .4
    # Real keyboard events exercise acceptance, not just a helper's arithmetic.
    for hover, negative, expected in ((-7, False, -7), (7, True, -7), (7, False, 7)):
        st.ctx.select_by_names([name])
        st.feed_line('ExtrudeCrv')
        yield from wait(lambda: st.active_command is not None
                        and st.active_command.step_index == 1, 'distance step')
        cmd = st.active_command
        check(cmd.opt['Solid'] is True, 'owner Solid=Yes default reaches live command')
        yield from move_to((2, 0, hover))
        if negative:
            send('MINUS', text='-'); yield .1
            send('MINUS', 'RELEASE'); yield .1
        send('SEVEN', text='7'); yield .15
        send('SEVEN', 'RELEASE'); yield .4
        check(st.input_buffer.strip() == ('-7' if negative else '7'), 'real keys reach input buffer')
        check(abs(ui._preview_cursor[2] - expected) < 1e-6,
              WITNESS if hover < 0 else 'typed preview preserves requested sign')
        before = len(st.ctx.undo.undo_names())
        send('RET'); yield .15
        send('RET', 'RELEASE'); yield .5
        yield from wait(lambda: st.active_command is None, 'extrusion accepted')
        made = [o.Geometry for o in st.model.file.Objects if o.Attributes.Name.startswith('Extrusion')][-1]
        bb = made.GetBoundingBox()
        check(abs((bb.Min.Z if expected < 0 else bb.Max.Z) - expected) < 1e-6,
              'accepted geometry matches visible typed preview')
        check(made.IsSolid, 'typed extrusion remains capped solid')
        check(len(st.ctx.undo.undo_names()) == before + 1, 'extrusion adds exactly one Undo entry')
    st.ctx.select_by_names([name])
    st.feed_line('ExtrudeCrv')
    yield .3
    xy = yield from move_to((2, 0, -5))
    send('LEFTMOUSE', 'PRESS', *xy); yield .15
    send('LEFTMOUSE', 'RELEASE', *xy); yield .5
    yield from wait(lambda: st.active_command is None, 'distance click accepted')
    made = [o.Geometry for o in st.model.file.Objects if o.Attributes.Name.startswith('Extrusion')][-1]
    check(abs(made.GetBoundingBox().Min.Z + 5) < .1, 'real negative hover click extrudes down')

    # Mandatory public adversary: one creased outer wall and seven inner walls.
    # It reproduces the legacy topology, not the reported private artwork.
    import importlib.util
    import math
    from narwhal3d import kernel
    spec = importlib.util.spec_from_file_location('public_snap_fixtures', ROOT / 'spike/test_report121_124.py')
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    geom, _joined, _expected_volume = fixtures.synthetic_legacy_prism()
    check(bridge.rehydrate_master(geom), 'synthetic legacy prism analytic master restored')
    master = kernel.master_of(geom)
    occt = bridge._occt()
    check(len(occt.unique_faces(master)) == 10 and len(occt._unique_edges(master)) == 24
          and len(occt._unique_vertices(master)) == 16,
          'synthetic legacy fixture has 10 faces, 24 edges and 16 vertices')
    corners = [occt.vertex_at(master, i + 1) for i in range(len(occt._unique_vertices(master)))]
    corner = min(corners, key=lambda p: (p[0], -p[2]))
    _id, solid_name = st.ctx.add_geometry(geom, 'SyntheticLegacyPrism')

    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D'
                and ui.pane_label(a) == 'Perspective')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    space = area.spaces.active
    from mathutils import Quaternion
    space.region_3d.view_rotation = Quaternion((1, 0, 0, 0))
    space.region_3d.view_perspective = 'PERSP'
    space.region_3d.view_location = corner
    space.region_3d.view_distance = .5
    ui._snap_settings.enabled = {'End'}
    ui._snap_settings.disabled = False
    ui._snap_settings.project = False
    yield .5
    for command in ('Move', 'Polyline'):
        st.ctx.select_by_names([solid_name] if command == 'Move' else [])
        st.feed_line(command)
        yield .4
        xy = yield from move_to(corner)
        raw = ui.pick_point(area, space, region, xy[0] - region.x, xy[1] - region.y)
        check(raw is None, 'real below-plane camera cannot intersect its CPlane forward')
        check(ui.snap_at(area, space, region, raw, suspend=True,
                         mouse_region=(xy[0] - region.x, xy[1] - region.y)) is None,
              'Alt suspension does not turn the screen anchor into a pick')
        snap = ui.active_snap()
        check(snap is not None and snap.mode == 'End' and snap.target.name == solid_name,
              'End hover works when the CPlane has no intersection')
        check(min(math.dist(snap.point, p) for p in corners) < 1e-7,
              'hover returns an actual OCCT corner, not a facet vertex')
        send('LEFTMOUSE', 'PRESS', *xy); yield .15
        send('LEFTMOUSE', 'RELEASE', *xy); yield .4
        point = st.active_command._last_point()
        check(point is not None and min(math.dist(point, p) for p in corners) < 1e-7,
              command + ' click accepts the exact displayed End snap')
        send('ESC'); yield .1
        send('ESC', 'RELEASE'); yield .3

    # A real interior G1 crease, deliberately absent from the legacy edge map.
    # No retopology: this remains a snap-only iso of the ten-face synthetic master.
    crease_curves = [curve for curve in bridge._analytic_snap_curves(geom)
                     if curve.GetUserString('Narwhal3D:SnapFeature') == 'crease-isocurve']
    check(len(crease_curves) == 24, 'all 24 exact internal creases reach live snap data')
    curve = crease_curves[0]

    a = (curve.PointAtStart.X, curve.PointAtStart.Y, curve.PointAtStart.Z)
    b = (curve.PointAtEnd.X, curve.PointAtEnd.Y, curve.PointAtEnd.Z)
    from mathutils import Vector
    for mode, fraction, distance in (('Near', .501, .002), ('Mid', .5, .002), ('End', 0, .002)):
        target = tuple(a[i] + (b[i] - a[i]) * fraction for i in range(3))
        # For Near the camera lies within the solid's AABB and the sampled edge
        # straddles its eye/near plane. Its original endpoints are far offscreen.
        direction = Vector((0, 1, -1)) if mode == 'Near' else Vector((0, 1, 0))
        space.region_3d.view_rotation = direction.to_track_quat('-Z', 'Y')
        space.region_3d.view_location = target
        space.region_3d.view_distance = distance
        space.clip_start = .000001
        ui._snap_settings.enabled = {'End', 'Mid', 'Near'}
        st.feed_line('Polyline')
        yield .4
        xy = yield from move_to(target)
        from bpy_extras.view3d_utils import location_3d_to_region_2d
        if mode == 'Near':
            clip = space.region_3d.perspective_matrix @ Vector((*target, 1))
            check(clip.w > 0 and -clip.w <= clip.z <= clip.w,
                  'zoom witness is inside the physical near/far clip interval')
            from bpy_extras.view3d_utils import region_2d_to_origin_3d
            eye = region_2d_to_origin_3d(region, space.region_3d,
                                        (xy[0] - region.x, xy[1] - region.y))
            bb = geom.GetBoundingBox()
            check(all((bb.Min.X, bb.Min.Y, bb.Min.Z)[i] < eye[i] <
                      (bb.Max.X, bb.Max.Y, bb.Max.Z)[i] for i in range(3)),
                  'close-zoom camera is inside the synthetic solid bounding box')
            projected = [location_3d_to_region_2d(region, space.region_3d, Vector(p)) for p in (a, b)]
            check(all(q is None or q.x < 0 or q.x > region.width or q.y < 0 or q.y > region.height
                      for q in projected), 'both original crease endpoints lie outside the viewport')
            check(ui.pick_point(area, space, region, xy[0]-region.x, xy[1]-region.y) is None,
                  'close-zoom mouse ray cannot reach the CPlane')
        snap = ui.active_snap()
        if mode == 'Near':
            import numpy as np
            targets = ui._snap_targets()
            coarse = ui.native_selection.nearest_projected_polylines(
                tuple(t.points for t in targets), np.asarray(space.region_3d.perspective_matrix),
                region.width, region.height, (xy[0]-region.x, xy[1]-region.y), clipped=True,
                with_distances=True)
            RESULT['facts']['zoom'] = {'snap': repr(snap), 'target': target, 'mouse': xy,
                'native_status': ui.native_selection._status, 'coarse': coarse,
                'matrix': [list(row) for row in space.region_3d.perspective_matrix],
                'active_modes': ui._snap_settings.active(), 'target_count': len(targets)}
        check(snap is not None and snap.mode == mode,
              'Near retains the visible crease interval across the near plane' if mode == 'Near'
              else mode + ' remains available at extreme close zoom')
        check(snap.target.curve.GetUserString('Narwhal3D:SnapFeature') == 'crease-isocurve',
              mode + ' targets an exact marked legacy crease, not a triangle edge')
        check(math.dist(snap.point, target) < 1e-5, mode + ' returns the actual crease point at close zoom')
        send('LEFTMOUSE', 'PRESS', *xy); yield .15
        send('LEFTMOUSE', 'RELEASE', *xy); yield .3
        check(math.dist(st.active_command._last_point(), snap.point) < 1e-7,
              mode + ' close-zoom click matches the hover')
        send('ESC'); yield .1
        send('ESC', 'RELEASE'); yield .3


def tick():
    try:
        return next(STEPS)
    except StopIteration:
        finish(0)
    except BaseException:
        finish(1, traceback.format_exc())


try:
    check(os.environ.get('NARWHAL3D_SETTINGS_FILE', '').startswith('/var/tmp/'), 'isolated settings required')
    check('narwhal3d' not in sys.modules, 'suite precedes bootstrap')
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    import bpy
    from narwhal3d import ui, bridge
    check(not bpy.app.background, 'real GUI required')
    if os.environ.get('WORKSPACE_DIALOGS_MUTATION') == 'sign':
        from narwhal3d import commands
        commands.ExtrudeCmd.extrusion_typed_distance = lambda self, value: value
    if os.environ.get('WORKSPACE_DIALOGS_MUTATION') == 'plane':
        real_snap_at = ui.snap_at
        ui.snap_at = lambda a, s, r, p, **kw: (None if p is None else real_snap_at(a, s, r, p, **kw))
    if os.environ.get('WORKSPACE_DIALOGS_MUTATION') == 'zoom':
        real_nearest = ui.native_selection.nearest_projected_polylines
        def old_nearest(*args, **kw):
            kw['clipped'] = False
            return real_nearest(*args, **kw)
        ui.native_selection.nearest_projected_polylines = old_nearest
    STEPS = run()
    bpy.app.timers.register(tick, first_interval=.1)
    threading.Thread(target=lambda: (time.sleep(150), finish(1, 'watchdog')), daemon=True).start()
except BaseException:
    finish(1, traceback.format_exc())
