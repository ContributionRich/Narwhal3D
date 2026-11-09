"""Owner-policy click-local regressions; display fixtures, never geometry rewrites.

PYTHONDONTWRITEBYTECODE=1 tools/blender-patched/blender -b --factory-startup \
  --python-exit-code 1 --python spike/test_pick_click_local.py -- --mutations
python3 -B spike/test_selection_menu_gui.py --launch --scenario click-local

Python is test/Blender orchestration. Existing packaged Rust performs clipping,
projection, aperture distance and broad rejection; Blender's C BVH performs depth.
"""
import io
import statistics
import sys
import time
import unittest
from unittest.mock import patch

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_pick_candidates as base
from mathutils import Matrix


class ClickLocalTests(base.PickCandidateTests):
    def test_sparse_large_bounds_are_not_geometry(self):
        points = [base.r3.Point3d(*p) for p in
                  ((-.8, -.8, 0), (-.8, .8, 0), (.8, .8, 0), (.8, -.8, 0))]
        curve = self.ui.add('large_sparse_bounds', base.r3.PolylineCurve(points))
        self.ui._curve_points = lambda geom: tuple((p.X, p.Y, p.Z) for p in points)
        self.assertEqual(self.keys(), [], 'box encloses click, actual drawn U does not')
        self.assertEqual(self.keys(xy=(80, 300)), [(curve.name, 'Object', 0)])

    def test_sparse_mesh_bounds_do_not_fill_empty_center(self):
        mesh = base.bpy.data.meshes.new('PickTest_sparse_mesh')
        self.ui.meshes.append(mesh)
        vertices = ((-.9, -.8, 0), (-.7, -.8, 0), (-.7, .8, 0), (-.9, .8, 0),
                    (.7, -.8, 0), (.9, -.8, 0), (.9, .8, 0), (.7, .8, 0))
        mesh.from_pydata(vertices, [], ((0, 1, 2, 3), (4, 5, 6, 7)))
        mesh.update()
        obj = self.ui.add('sparse_mesh', base.r3.Mesh(), mesh)
        self.space.shading.type = 'SOLID'
        self.assertEqual(self.keys(), [], 'large mesh bounds are not a face at the cursor')
        self.assertEqual(self.keys(xy=(80, 300)), [(obj.name, 'Object', 0)])

    def test_rear_visible_far_left_is_not_visible_at_center(self):
        rear = self.ui.box('wide_rear', low=(-.9, -.4, -1.4), high=(.4, .4, -1))
        front = self.ui.box('front')
        self.space.shading.type = 'SOLID'
        self.assertEqual(self.keys(xy=(100, 300)), [(rear.name, 'Object', 0)],
                         'nonvacuous: rear really is visible far left')
        self.assertEqual(self.keys(), [(front.name, 'Object', 0)],
                         'visibility far left cannot qualify a covered center hit')
        self.space.shading.type = 'WIREFRAME'
        self.assertEqual({k[0] for k in self.keys()}, {front.name, rear.name})

    def test_actual_offset_hit_occluded_while_center_ray_is_clear(self):
        self.ui.box('thin_front', low=(.008, -.4, -.4), high=(.035, .4, 0))
        hidden = self.ui.add('offset_hidden', base.r3.Point(base.r3.Point3d(.015, 0, -1)))
        visible = self.ui.add('offset_visible', base.r3.Point(base.r3.Point3d(-.015, 0, -1)))
        curve = self.ui.add('offset_curve', base.r3.LineCurve(
            base.r3.Point3d(.015, -.2, -1), base.r3.Point3d(.015, .2, -1)))
        self.space.shading.type = 'SOLID'
        keys = self.keys()
        self.assertIn((visible.name, 'Object', 0), keys)
        self.assertNotIn((hidden.name, 'Object', 0), keys)
        self.assertNotIn((curve.name, 'Object', 0), keys)
        self.assertNotIn((curve.name, 'Segment', 0), self.keys('part'))

    def test_offset_hit_visible_while_center_ray_is_occluded(self):
        self.ui.box('thin_front', low=(-.01, -.4, -.4), high=(.01, .4, 0))
        point = self.ui.add('offset_visible', base.r3.Point(base.r3.Point3d(.015, 0, -1)))
        self.space.shading.type = 'SOLID'
        self.assertIn((point.name, 'Object', 0), self.keys(),
                      'center depth must not suppress visible geometry six pixels away')

    def test_visible_branch_survives_occluded_nearest_branch(self):
        points = ((.005, -.02, -1), (.005, .02, -1),
                  (-.015, .02, -1), (-.015, -.02, -1))
        curve = self.ui.add('two_branches', base.r3.PolylineCurve(
            [base.r3.Point3d(*p) for p in points]))
        self.ui._curve_points = lambda geom: points
        self.ui.box('thin_front', low=(.001, -.4, -.4), high=(.01, .4, 0))
        self.space.shading.type = 'SOLID'
        self.assertIn((curve.name, 'Object', 0), self.keys())
        self.assertIn((curve.name, 'Segment', 0), self.keys('part'))

    def test_clipped_front_does_not_occlude_visible_hit(self):
        curve = self.ui.line('visible')
        self.ui.box('outside_near', low=(-.4, -.4, 16), high=(.4, .4, 17))
        self.space.shading.type = 'SOLID'
        self.assertEqual(self.keys(), [(curve.name, 'Object', 0)])

    def test_locked_front_is_still_opaque(self):
        front = self.ui.box('locked_front')
        rear = self.ui.line('hidden_curve', z=-1)
        front.hide_select = True
        self.space.shading.type = 'SOLID'
        self.assertEqual(self.keys(), [])
        self.space.shading.type = 'WIREFRAME'
        self.assertEqual(self.keys(), [(rear.name, 'Object', 0)])

    def test_picture_display_mesh_obeys_local_depth_in_both_roles(self):
        mesh = base.bpy.data.meshes.new('PickTest_picture')
        self.ui.meshes.append(mesh)
        mesh.from_pydata(((-.3, -.3, -1), (.3, -.3, -1), (.3, .3, -1), (-.3, .3, -1)),
                         [], ((0, 1, 2, 3),))
        mesh.update()
        picture = self.ui.add('picture', base.r3.Mesh(), mesh)
        point = self.ui.add('behind_picture', base.r3.Point(base.r3.Point3d(0, 0, -2)))
        self.space.shading.type = 'SOLID'
        self.assertEqual(self.keys(), [(picture.name, 'Object', 0)])
        front = self.ui.box('front')
        self.assertEqual(self.keys(), [(front.name, 'Object', 0)])
        self.assertNotIn((point.name, 'Object', 0), self.keys())

    def test_off_viewport_and_clipped_depth_cannot_qualify(self):
        self.ui.line('center')
        for xy in ((-1, 300), (800, 300), (400, -1), (400, 600)):
            with patch.object(base.native, 'nearest_projected_polylines') as query:
                self.assertEqual(base.picker.at(self.ui, None, self.space, self.region, *xy), [])
                query.assert_not_called()
        # The original synthetic orthographic projection clips z outside [-5, 15].
        clipped = self.ui.line('beyond_far', z=-20)
        self.assertNotIn((clipped.name, 'Object', 0), self.keys())

    def test_perspective_hit_has_correct_world_depth(self):
        self.rv.is_perspective = True
        self.rv.view_perspective = 'PERSP'
        projection = Matrix(((1, 0, 0, 0), (0, 1, 0, 0),
                             (0, 0, -1.002002, -.2002002), (0, 0, -1, 0)))
        self.rv.perspective_matrix = projection @ self.rv.view_matrix
        curve = self.ui.add('sloped_visible', base.r3.LineCurve(
            base.r3.Point3d(-1, 0, 4), base.r3.Point3d(2, 0, 1)))
        # At screen center the true line point is z=3. Screen-linear interpolation
        # instead gives z=2, incorrectly behind this occluder at z=2.5.
        self.ui.box('behind_true_hit', low=(-.2, -.2, 2), high=(.2, .2, 2.5))
        self.space.shading.type = 'SOLID'
        self.assertIn((curve.name, 'Object', 0), self.keys())

    def test_display_revision_invalidates_visibility_without_doc_change(self):
        curve = self.ui.line('revision')
        self.ui._curve_dg = {'serial': 1}
        self.assertEqual(self.keys(), [(curve.name, 'Object', 0)])
        curve.hide_viewport = True
        self.ui._curve_dg['serial'] += 1
        self.assertEqual(self.keys(), [])
        curve.hide_viewport = False
        self.ui._curve_dg['serial'] += 1
        self.assertEqual(self.keys(), [(curve.name, 'Object', 0)])
        curve.hide_select = True
        self.assertEqual(self.keys(), [])

    def test_large_scene_only_local_rows_reach_native_distance(self):
        local = self.ui.line('local')
        for i in range(5000):
            self.ui.line('remote' + str(i), y=.5 + i * .00001)
        self.ui._curve_dg = {'serial': 1}  # Production display revision, stable during idle picks.
        real = base.native.nearest_projected_polylines
        counts = []
        def query(lines, *args, **kwargs):
            counts.append(len(lines))
            self.assertTrue(kwargs.get('clipped'))
            return real(lines, *args, **kwargs)
        elapsed = []
        with patch.object(base.native, 'nearest_projected_polylines', side_effect=query):
            for _ in range(8):
                start = time.perf_counter()
                self.assertEqual([c.key for c in base.picker.at(
                    self.ui, None, self.space, self.region, 400, 300)], [(local.name, 'Object', 0)])
                elapsed.append(time.perf_counter() - start)
        self.assertEqual(counts, [1] * 8, 'Rust culls 5000 remote curves before Python hit decoding')
        print('CLICK_LOCAL_PERF', {'objects': 5001, 'cold_s': elapsed[0],
              'warm_median_s': statistics.median(elapsed[1:]), 'distance_rows': counts}, flush=True)


def baseline_evidence():
    """Run the original committed implementation in memory, without reverting files."""
    import subprocess
    source = subprocess.check_output(
        ['git', '--no-pager', 'show', 'HEAD:narwhal3d/pick_candidates.py'],
        cwd=Path(__file__).resolve().parents[1], text=True)
    original = base.picker
    try:
        base.picker = base.load('pick_candidates', source)
        for test, should_pass in (
            ('test_sparse_large_bounds_are_not_geometry', True),
            ('test_sparse_mesh_bounds_do_not_fill_empty_center', True),
            ('test_rear_visible_far_left_is_not_visible_at_center', False),
        ):
            result = unittest.TextTestRunner(verbosity=2).run(ClickLocalTests(test))
            if result.wasSuccessful() != should_pass or result.errors:
                raise AssertionError('Unexpected original-source result: ' + test)
            print('ORIGINAL_SOURCE', test, 'GREEN' if should_pass else 'RED', flush=True)
    finally:
        base.picker = original
        base.PACKAGE.pick_candidates = original
        sys.modules[original.__name__] = original


def mutations():
    original = base.picker
    source = base.SOURCES['pick_candidates']
    probes = (
        ("depth_tree = _cache['depth_tree'] if space.shading.type != 'WIREFRAME' else None",
         'depth_tree = None', 'test_rear_visible_far_left_is_not_visible_at_center'),
        ('xy = projected[:2]', 'xy = (x, y)',
         'test_actual_offset_hit_occluded_while_center_ray_is_clear'),
        ('xy = projected[:2]', 'xy = (x, y)',
         'test_offset_hit_visible_while_center_ray_is_occluded'),
        ('with_distances=True, clipped=True)', 'with_distances=True, clipped=False)',
         'test_perspective_hit_has_correct_world_depth'),
        ("and _cache.get('signature') == signature", 'and True',
         'test_display_revision_invalidates_visibility_without_doc_change'),
        ('origin, end = a.xyz / a.w, b.xyz / b.w',
         'origin, end = 2 * a.xyz / a.w - b.xyz / b.w, b.xyz / b.w',
         'test_clipped_front_does_not_occlude_visible_hit'),
        ('if local is None:', 'if True:',
         'test_large_scene_only_local_rows_reach_native_distance'),
        ('point = candidates[index].tree.ray_cast(origin, direction, limit)[0]',
         'point = (0, 0, 0)', 'test_sparse_mesh_bounds_do_not_fill_empty_center'),
        ('for index, hit in zip(indices, measured):',
         'for index, hit in dict(reversed(tuple(zip(indices, measured)))).items():',
         'test_visible_branch_survives_occluded_nearest_branch'),
        ("        if candidates[index].tree is None or candidates[index].key[1] == 'Occluder':\n            continue",
         "        if candidates[index].tree is None:\n            record(index, (0, 0, 0), 0.0)\n            continue\n        if candidates[index].key[1] == 'Occluder':\n            continue",
         'test_sparse_large_bounds_are_not_geometry'),
    )
    try:
        for old, new, test in probes:
            assert source.count(old) == 1, old
            base.picker = base.load('pick_candidates', source.replace(old, new, 1))
            stream = io.StringIO()
            result = unittest.TextTestRunner(stream=stream).run(ClickLocalTests(test))
            if result.wasSuccessful() or result.errors:
                raise AssertionError('Expected assertion RED: ' + test + '\n' + stream.getvalue())
            print('RED verified:', test, flush=True)
            base.picker = original
    finally:
        base.picker = original
        base.PACKAGE.pick_candidates = original
        sys.modules[original.__name__] = original


def _gui_mutate(h):
    from narwhal3d import pick_candidates
    import inspect
    mutation = h.RESULT['mutation']
    if mutation in ('no_depth', 'center_depth', 'distant_tol'):
        source = Path(pick_candidates.__file__).read_text()
        old, new = {
            'no_depth': ("depth_tree = _cache['depth_tree'] if space.shading.type != 'WIREFRAME' else None",
                         'depth_tree = None'),
            'center_depth': ('xy = projected[:2]', 'xy = (x, y)'),
            'distant_tol': ("tolerance = ui.SUBOBJECT_PICK_PX if mode != 'object' else ui.CURVE_PICK_PX",
                            'tolerance = max(region.width, region.height)'),
        }[mutation]
        h.check(source.count(old) == 1, 'unique process-local mutation anchor')
        exec(compile(source.replace(old, new, 1), pick_candidates.__file__, 'exec'), pick_candidates.__dict__)
    if mutation == 'legacy_singleton':
        source = inspect.getsource(h.ui._open_selection_choices)
        old = '        return True'
        h.check(source.count(old) == 1, 'unique caller mutation anchor')
        exec(compile(source.replace(old, '        return False', 1),
                     h.ui.__file__, 'exec'), h.ui.__dict__)
    if mutation in ('legacy_direct', 'legacy_singleton'):
        source = inspect.getsource(h.ui.narwhal_click_select)
        old = "_open_selection_choices(area, space, region, rx, ry, 'object',\n                            shift=shift, ctrl=ctrl)"
        new = "_select_object_hit(pick_point_object(area, space, region, rx, ry),\n                       shift=shift, ctrl=ctrl, area=area)"
        h.check(source.count(old) == 1, 'unique direct-click mutation anchor')
        exec(compile(source.replace(old, new, 1), h.ui.__file__, 'exec'), h.ui.__dict__)


def _check_direct_click(h, point, expected, label):
    h.ui.apply_selection([])
    h.ui.narwhal_click_select(h.AREA, h.SPACE, h.REGION,
                            point[0] - h.REGION.x, point[1] - h.REGION.y)
    h.check(h.selection_menu.current() is None and h.ui.selection_names() == [expected],
            label + ': direct ordinary-click API uses the same visible candidate filter')


def gui_run(h):
    """Top and actual oblique Perspective panes, selected independently for mutations."""
    view = h.os.environ.get('SELECTION_MENU_PICK_VIEW', 'both')
    if view in ('Top', 'both'):
        yield from _gui_top(h)
    if view in ('Perspective', 'both'):
        yield from _gui_perspective(h)


def _gui_top(h):
    """Actual Top modal clicks, using the isolated/locked GUI launcher."""
    from mathutils import Vector
    from narwhal3d import pick_candidates
    deadline = time.monotonic() + 45
    while not (h.os.environ.get('NARWHAL3D_UI_READY') == '1' and not h.ui._viewports_in_progress
               and h.ui._viewport_count() == 4 and h.ui.is_input_active()):
        if time.monotonic() > deadline:
            raise RuntimeError('Bootstrap not ready in 45s')
        yield .15
    h.st = h.ui.app_state
    h.check(not list(h.st.model.file.Objects), 'isolated empty document')
    h.AREA = h.ui._pane_by_label('Top')
    h.SPACE, h.REGION = h.AREA.spaces.active, h.ui._window_region(h.AREA)
    h.ui.set_active_pane(h.AREA)
    h.ui.set_gumball(False)
    h.ui.set_grid_snap(False)
    h.ui.set_ortho(False)
    h.st.feed_line('DisableOsnap')
    h.st.feed_line('Toggle')
    h.SPACE.shading.type = 'SOLID'
    h.SPACE.region_3d.view_location = Vector((0, 0, 0))
    h.SPACE.region_3d.view_distance = 45
    h.SPACE.region_3d.update()
    yield .4
    _, rear = h.st.ctx.add_geometry(h.bridge.make_box((-14, -4, -5), (4, 4, -5), 2), 'WideRear')
    _, front = h.st.ctx.add_geometry(h.bridge.make_box((-4, -4, 0), (4, 4, 0), 4), 'OpaqueFront')
    _, hidden = h.st.ctx.add_geometry(base.r3.Point(base.r3.Point3d(0, 0, -8)), 'CoveredPoint')
    sparse_geom = base.r3.PolylineCurve([base.r3.Point3d(*p) for p in
        ((-16, -10, 0), (-16, 10, 0), (16, 10, 0), (16, -10, 0))])
    _, sparse = h.st.ctx.add_geometry(sparse_geom, 'SparseLargeBounds')
    p0 = h.ui._project_2d(h.REGION, h.SPACE.region_3d, (0, 0, 0))
    p1 = h.ui._project_2d(h.REGION, h.SPACE.region_3d, (1, 0, 0))
    ppu = abs(p1[0] - p0[0])
    delta = 4 / ppu
    # Click x=8: centre ray misses the tiny face. Four pixels to its right,
    # the point's actual tolerance hit is covered; left-side point is visible.
    _, thin = h.st.ctx.add_geometry(h.bridge.make_box(
        (8 + delta * .6, -13, 0), (8 + delta * 1.4, -11, 0), 1), 'OffsetOccluder')
    _, covered = h.st.ctx.add_geometry(base.r3.Point(base.r3.Point3d(8 + delta, -12, -2)), 'OffsetCovered')
    _, exposed = h.st.ctx.add_geometry(base.r3.Point(base.r3.Point3d(8 - delta, -12, -2)), 'OffsetExposed')
    h.ui.apply_selection([])
    h.ui._tag_redraw_viewports()
    yield .5
    fixture = h.snapshot('local_fixture')
    _gui_mutate(h)

    center = h.screen_point((0, 0, 4))
    yield from h.tap('LEFTMOUSE', center)
    h.check(h.selection_menu.current() is None, 'covered center is not ambiguous')
    h.check(h.ui.selection_names() == [front], 'center selects front, not rear visible far left or covered point')
    h.unchanged(fixture, 'center_geometry_preserved', selection=False)
    _check_direct_click(h, center, front, 'Top')
    yield from h.tap('LEFTMOUSE', h.screen_point((-10, 0, -3)))
    h.check(h.selection_menu.current() is None and h.ui.selection_names() == [rear],
            'far-left real click selects the genuinely exposed rear')

    offset = h.screen_point((8, -12, 0))
    session = yield from h.open_at(offset, 'offset_local')
    names = {c.key[0] for c in session['candidates']}
    h.check(exposed in names and thin in names and covered not in names,
            'actual offset hit depth, not center ray: visible point and face only')
    yield from h.accept((exposed, 'Object', 0))
    h.check(h.ui.selection_names() == [exposed], 'offset row commits only visible point')

    h.SPACE.shading.type = 'WIREFRAME'
    h.ui._tag_redraw_viewports()
    yield .3
    session = yield from h.open_at(center, 'wireframe')
    names = {c.key[0] for c in session['candidates']}
    h.check({front, rear, hidden} <= names and sparse not in names,
            'wireframe has no opaque-face occlusion but still rejects sparse enclosing bounds')
    yield from h.tap('ESC')
    h.SPACE.shading.type = 'SOLID'
    h.ui._tag_redraw_viewports()
    yield .3
    yield from h.tap('LEFTMOUSE', center, ctrl=True, shift=True)
    session = h.selection_menu.current()
    if session is not None:
        h.check(all(c.key[0] == front for c in session['candidates']), 'subobject menu has no covered rear parts')
        yield from h.tap('ESC')
    else:
        h.check(all(k[0] == front for k in h.ui.selected_subobjects()), 'single front subobject, no covered rear')
    h.check(h.snapshot('local_final')['document'] == fixture['document'],
            'all real clicks preserve exact document geometry and identities')


def _gui_perspective(h):
    """Real oblique Perspective with an opaque Picture and view-aligned fixtures."""
    import bpy_extras.view3d_utils as v3u
    from mathutils import Quaternion, Vector
    deadline = time.monotonic() + 45
    while not (h.os.environ.get('NARWHAL3D_UI_READY') == '1' and not h.ui._viewports_in_progress
               and h.ui._viewport_count() == 4 and h.ui.is_input_active()):
        if time.monotonic() > deadline:
            raise RuntimeError('Bootstrap not ready in 45s')
        yield .15
    h.st = h.ui.app_state
    h.AREA = h.ui._pane_by_label('Perspective')
    h.check(h.AREA is not None, 'Perspective pane exists')
    h.SPACE, h.REGION = h.AREA.spaces.active, h.ui._window_region(h.AREA)
    h.ui.set_active_pane(h.AREA)
    h.ui.set_gumball(False)
    h.ui.set_grid_snap(False)
    h.ui.set_ortho(False)
    h.ui.clear_subobject_selection()
    h.ui.apply_selection([])
    h.SPACE.shading.type = 'SOLID'
    rv = h.SPACE.region_3d
    rv.view_perspective = 'PERSP'
    rv.view_location = Vector((100, 100, 100))
    rv.view_distance = 45
    rv.view_rotation = Quaternion((0, 0, 1), .6) @ Quaternion((1, 0, 0), .9)
    rv.update()
    yield .4
    h.check(rv.is_perspective and rv.view_perspective == 'PERSP', 'actual Perspective projection, not orthographic')
    basis = [rv.view_rotation @ Vector(axis) for axis in ((1, 0, 0), (0, 1, 0), (0, 0, 1))]
    h.check(max(abs(v) for v in basis[2]) < .999, 'actual camera is oblique to world axes')
    anchor = rv.view_location.copy()

    def world(x, y, z):
        return anchor + basis[0] * x + basis[1] * y + basis[2] * z

    # Fixture transforms use the existing analytic bridge; no picking code or
    # document geometry implementation is substituted or edited.
    transform = base.r3.Transform(1.0)
    for row in range(3):
        for col in range(3):
            setattr(transform, f'M{row}{col}', basis[col][row])
        setattr(transform, f'M{row}3', anchor[row])
    rear_geom = h.bridge.make_box((-14, -4, -5), (4, 4, -5), 2)
    rear_geom = h.bridge.transform_geometry(rear_geom, transform)
    _, rear = h.st.ctx.add_geometry(rear_geom, 'PerspectiveWideRear')
    image = base.bpy.data.images.new('ClickLocalOpaque', width=16, height=16, alpha=False)
    image.pixels = (.2, .65, .9, 1.0) * (16 * 16)
    image.filepath_raw, image.file_format = str(h.OUT / 'opaque-picture.png'), 'PNG'
    image.save()
    frame = dict(origin=tuple(world(-4, -4, 4)), u=tuple(basis[0] * 8),
                 v=tuple(basis[1] * 8), image=image.filepath_raw)
    _, front = h.st.ctx.add_geometry(h.bridge.make_picture(frame), 'PerspectiveOpaquePicture')
    _, hidden = h.st.ctx.add_geometry(base.r3.Point(base.r3.Point3d(*world(0, 0, -8))), 'PerspectiveCoveredPoint')
    _, hidden_curve = h.st.ctx.add_geometry(h.bridge.make_line(
        tuple(world(-2, 0, -7)), tuple(world(2, 0, -7))), 'PerspectiveCoveredCurve')
    _, sparse = h.st.ctx.add_geometry(base.r3.PolylineCurve([base.r3.Point3d(*world(*p)) for p in
        ((-16, -10, 0), (-16, 10, 0), (16, 10, 0), (16, -10, 0))]), 'PerspectiveSparseBounds')
    local = h.ui._project_2d(h.REGION, rv, world(9, -8, 0))
    h.check(local is not None, 'Perspective aperture fixture projects')

    def local_world(dx, dy):
        return tuple(v3u.region_2d_to_location_3d(
            h.REGION, rv, (local[0] + dx, local[1] + dy), world(9, -8, 0)))

    _, curve = h.st.ctx.add_geometry(h.bridge.make_line(local_world(-10, 3), local_world(10, 3)),
                                   'PerspectiveNearCurve')
    _, point = h.st.ctx.add_geometry(base.r3.Point(base.r3.Point3d(*local_world(-3, 0))), 'PerspectiveNearPoint')
    _, distant_curve = h.st.ctx.add_geometry(h.bridge.make_line(local_world(-10, 18), local_world(10, 18)),
                                           'PerspectiveDistantCurve')
    _, distant_point = h.st.ctx.add_geometry(base.r3.Point(base.r3.Point3d(*local_world(20, 0))), 'PerspectiveDistantPoint')
    h.ui._tag_redraw_viewports()
    yield .5
    ob = base.bpy.data.objects.get(front)
    h.check(ob is not None and ob.get('narwhal_picture') and all(image.pixels[i] == 1 for i in range(3, len(image.pixels), 4)),
            'front is an actual Picture display with fully opaque image pixels')
    fixture = h.snapshot('perspective_fixture')
    _gui_mutate(h)
    center = h.screen_point(world(0, 0, 4))
    yield from h.tap('LEFTMOUSE', center)
    h.check(h.selection_menu.current() is None and h.ui.selection_names() == [front],
            'Perspective actual center click selects only opaque Picture, not rear/covered curve/point/sparse bounds')
    _check_direct_click(h, center, front, 'Perspective')
    far_left = h.screen_point(world(-10, 0, -3))
    yield from h.tap('LEFTMOUSE', far_left)
    from narwhal3d import pick_candidates
    xy = (round(far_left[0]) - h.REGION.x, round(far_left[1]) - h.REGION.y)
    candidates, _, _ = pick_candidates.collect(h.ui, 'object')
    ray_origin = v3u.region_2d_to_origin_3d(h.REGION, rv, xy)
    ray_direction = v3u.region_2d_to_vector_3d(h.REGION, rv, xy)
    h.RESULT['perspective_farleft'] = dict(selection=h.ui.selection_names(),
        candidates=[c.key for c in pick_candidates.at(h.ui, h.AREA, h.SPACE, h.REGION, *xy)],
        rear_ray=[tuple(c.tree.ray_cast(ray_origin, ray_direction)[0] or ())
                  for c in candidates if c.key[0] == rear and c.tree is not None])
    print('PERSPECTIVE_FARLEFT', h.RESULT['perspective_farleft'], flush=True)
    h.check(h.selection_menu.current() is None and h.ui.selection_names() == [rear],
            'Perspective actual far-left click selects exposed rear of same object')
    local_click = (h.REGION.x + local[0], h.REGION.y + local[1])
    session = yield from h.open_at(local_click, 'perspective_aperture')
    names = {c.key[0] for c in session['candidates']}
    h.check(names == {curve, point} and not names.intersection({distant_curve, distant_point, hidden, hidden_curve, sparse}),
            'Perspective actual aperture contains near curve/point only, not distant or covered geometry')
    yield from h.accept((point, 'Object', 0))
    h.check(h.ui.selection_names() == [point], 'Perspective choice selects only requested visible point')
    yield from h.tap('LEFTMOUSE', local_click, shift=True)
    h.check(h.selection_menu.current() is not None, 'Perspective Shift preserves genuine ambiguity')
    yield from h.accept((curve, 'Object', 0))
    h.check(set(h.ui.selection_names()) == {point, curve}, 'Perspective Shift adds chosen visible curve')
    yield from h.tap('LEFTMOUSE', center, ctrl=True, shift=True)
    session = h.selection_menu.current()
    if session is not None:
        h.check(all(c.key[0] == front for c in session['candidates']), 'Perspective subobject candidates belong only to front Picture')
        yield from h.tap('ESC')
    else:
        h.check(bool(h.ui.selected_subobjects()) and all(k[0] == front for k in h.ui.selected_subobjects()),
                'Perspective single visible front subobject, no covered curve/rear')
    h.check(h.snapshot('perspective_final')['document'] == fixture['document'],
            'Perspective real clicks preserve all exact fixture geometry and identities')


if __name__ == '__main__':
    if not base.native.available():
        raise RuntimeError(base.native.backend_status())
    if '--baseline' in sys.argv:
        baseline_evidence()
    if '--mutations' in sys.argv:
        mutations()
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(ClickLocalTests))
    if not result.wasSuccessful():
        raise AssertionError('click-local regressions failed')
