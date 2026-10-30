#!/usr/bin/env python3
"""Polygon input/memory and display-preview parity; no Blender window or VM.

Run with the bundled Python [--mutations]. Exercises real Command begin/feed,
document curves/UndoStack and command-aware preview dispatch against producers.
"""
import ast
import copy
from contextlib import ExitStack
import importlib
import io
import math
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
pkg = ModuleType('narwhal3d')
pkg.__path__ = [str(ROOT / 'narwhal3d')]
sys.modules['narwhal3d'] = pkg
commands = importlib.import_module('narwhal3d.commands')
bridge = importlib.import_module('narwhal3d.bridge')
cplane = importlib.import_module('narwhal3d.cplane')
coords = importlib.import_module('narwhal3d.coords')
model = importlib.import_module('narwhal3d.model')
preview = importlib.import_module('narwhal3d.preview')


class Context(commands.NarwhalContext):
    def __init__(self, plane=cplane.WORLD_TOP):
        super().__init__(model.NarwhalModel())
        self.plane = plane
        self._polygon_num_sides = 5  # Controlled initial state, not a factory-default claim.
        self.added = []

    def active_cplane(self):
        return self.plane

    def selected_objects(self):
        return []

    def add_geometry(self, geom, base_name, layer_index=None):
        self.note_change(base_name)
        name = self.next_name(base_name)
        oid, _ = self.model.add_object(geom, name, 0)
        self.added.append(geom)
        return oid, name


def constraint_ui(cmd, ctx):
    """Execute the actual UI constraint functions without importing Blender.

    Only window/redraw plumbing is stubbed. This is a pure UI-path test, not a
    claim that event delivery or draw callbacks ran (Main owns the GUI run).
    """
    import itertools
    names = {'_constraint_prompt', '_cmd_token', '_typed_constraint',
             'handle_constraint_line', 'constrain_pick', 'apply_ortho'}
    source = ROOT/'narwhal3d/ui.py'
    tree = ast.parse(source.read_text())
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == names
    state = SimpleNamespace(active_command=cmd, input_buffer='', history=[], log=lambda text: None)
    namespace = dict(commands=commands, pickconstraint=importlib.import_module('narwhal3d.pickconstraint'),
        ortho=importlib.import_module('narwhal3d.ortho'), app_state=state,
        _pick_tokens=itertools.count(), _ortho_on=False, _ortho_angle=90.,
        active_cplane=ctx.active_cplane, get_cplane=lambda area: ctx.plane,
        _snap_base=lambda running: running._last_point(), redraw_cmdbar=lambda: None,
        _reapply_constraint_preview=lambda running: None)
    namespace['_pick_constraint'] = namespace['pickconstraint'].PickConstraint()
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
    return namespace, state


class PolygonBehavior(unittest.TestCase):
    def begin(self, ctx):
        cmd = copy.deepcopy(commands.COMMANDS['polygon'])
        self.assertEqual(cmd.begin(ctx), (None, False))
        return cmd

    def set_count(self, cmd, ctx, line='8'):
        message, done = cmd.feed(line, ctx)
        self.assertFalse(done, message)
        self.assertEqual(cmd.opt.get('NumSides'), 8, message)
        self.assertEqual(cmd.step_index, 0)
        self.assertEqual(cmd.args, [])

    def commit(self, cmd, ctx, center='2,3', corner='4,3'):
        message, done = cmd.feed(center, ctx)
        self.assertFalse(done, message)
        message, done = cmd.feed(corner, ctx)
        self.assertTrue(done, 'Corner must finish without a Sides prompt: ' + str(message))
        self.assertEqual(len(ctx.added), 1)
        return ctx.added[-1]

    def test_count_at_center_is_draft_and_cancel_does_not_remember(self):
        ctx = Context(); cmd = self.begin(ctx)
        self.assertIn('NumSides=5', cmd.prompt)
        self.set_count(cmd, ctx)
        self.assertIn('NumSides=8', cmd.prompt)
        self.assertEqual(ctx._polygon_num_sides, 5)
        self.assertEqual(ctx.model.object_count(), 0)
        self.assertEqual(ctx.undo.undo_names(), [])
        self.assertTrue(cmd.feed('', ctx)[1])
        fresh = self.begin(ctx)
        self.assertEqual(fresh.opt['NumSides'], 5)
        self.assertEqual(ctx.undo.undo_names(), [])

    def test_commit_remembers_after_existing_transaction_and_uses_fresh_begin(self):
        ctx = Context(); before = copy.deepcopy(commands.COMMANDS['polygon'])
        cmd = self.begin(ctx); self.set_count(cmd, ctx)
        curve = self.commit(cmd, ctx)
        self.assertEqual(curve.ToNurbsCurve().Degree, 1)
        self.assertEqual(len(curve.ToNurbsCurve().Points), 9)
        self.assertTrue(curve.IsClosed)
        self.assertEqual(ctx.undo.undo_names(), ['Polygon'])
        self.assertEqual(ctx._polygon_num_sides, 8)
        before.begin(ctx)  # Prototype was copied BEFORE the previous count committed.
        self.assertEqual(before.opt['NumSides'], 8)

    def test_named_option_forms_have_the_same_draft_behavior(self):
        for lines in [('NumSides=8',), ('NumSides', '8'), ('NumS=8',)]:
            with self.subTest(lines=lines):
                ctx = Context(); cmd = self.begin(ctx)
                for line in lines:
                    self.assertFalse(cmd.feed(line, ctx)[1])
                self.assertEqual(cmd.opt['NumSides'], 8)
                self.assertEqual(cmd.step_index, 0)
                self.assertIsNone(cmd.pending_option)
                self.assertEqual(ctx._polygon_num_sides, 5)
                self.commit(cmd, ctx)
                self.assertEqual(ctx._polygon_num_sides, 8)

    def test_invalid_count_does_not_poison_draft_or_progress(self):
        for value in ('2', '0', '-3', '3.5', 'nan', 'inf', '10001'):
            for form in ('bare', 'inline', 'pending'):
                with self.subTest(value=value, form=form):
                    ctx = Context(); cmd = self.begin(ctx)
                    if form == 'pending':
                        cmd.feed('NumSides', ctx)
                    text = 'NumSides=' + value if form == 'inline' else value
                    msg, done = cmd.feed(text, ctx)
                    self.assertFalse(done, msg)
                    self.assertTrue(msg)
                    self.assertEqual(cmd.opt['NumSides'], 5)
                    self.assertEqual(ctx._polygon_num_sides, 5)
                    self.assertEqual(cmd.args, [])
                    self.assertEqual(cmd.step_index, 0)
                    self.assertEqual(ctx.model.object_count(), 0)
                    if form == 'pending':
                        self.assertIsNotNone(cmd.pending_option)
                        cmd.feed('8', ctx)
                        self.assertEqual(cmd.opt['NumSides'], 8)

    def test_corner_point_step_and_radius_are_not_reinterpreted_as_sides(self):
        ctx = Context(); cmd = self.begin(ctx)
        self.assertEqual(cmd.steps[1], ('Corner of polygon:', commands.POINT))
        # Bare radii belong to UI handle_constraint_line, not direct command feed.
        curve = self.commit(cmd, ctx, corner='10,3')
        nurbs = curve.ToNurbsCurve()
        self.assertEqual(len(nurbs.Points), 6)
        p = nurbs.Points[0]
        self.assertAlmostEqual(math.dist((p.X, p.Y, p.Z), (2, 3, 0)), 8)
        self.assertEqual(ctx._polygon_num_sides, 5)

    def test_ui_numeric_radius_waits_for_direction_and_ortho_xor_shift(self):
        for plane in (cplane.WORLD_TOP, cplane.WORLD_FRONT, cplane.WORLD_RIGHT):
            for mode in ('Inscribed', 'Circumscribed'):
                for submitted in (False, True):
                    with self.subTest(mode=mode, submitted=submitted, plane=plane):
                        ctx = Context(plane); cmd = self.begin(ctx)
                        if mode != 'Inscribed':
                            cmd.feed(mode, ctx)
                        cmd.feed('2,3', ctx)
                        ui, state = constraint_ui(cmd, ctx)
                        self.assertIsNotNone(ui['_constraint_prompt'](cmd), 'Polygon corner enables UI constraints')
                        if submitted:
                            self.assertTrue(ui['handle_constraint_line']('5'))
                        else:
                            state.input_buffer = '5'
                        self.assertEqual(cmd.args, [plane.to_world(2, 3)])
                        self.assertEqual(ctx.model.object_count(), 0)
                        self.assertEqual(ctx.undo.undo_names(), [])
                        self.assertEqual(ctx._polygon_num_sides, 5)
                        picks = []
                        for ortho_on, shift in ((False, False), (False, True), (False, False),
                                                (True, False), (True, True), (True, False)):
                            ui['_ortho_on'] = ortho_on
                            # Actual UI order: Ortho first, radius constraint second.
                            raw = plane.to_world(8, 5)
                            adjusted = ui['apply_ortho'](cmd, raw, None, shift)
                            point = ui['constrain_pick'](cmd, adjusted)
                            u, v, w = plane.from_world(point)
                            self.assertAlmostEqual(math.hypot(u-2, v-3), 5)
                            self.assertAlmostEqual(w, 0)
                            if ortho_on != shift:
                                self.assertAlmostEqual(v, 3)
                                self.assertAlmostEqual(u, 7)
                            else:
                                self.assertAlmostEqual((v-3)/(u-2), 1/3)
                            lines = preview.segments('Polygon', cmd.args, point, plane, cmd=cmd)
                            self.assertEqual(len(lines), 1)
                            contact = lines[0][0] if mode == 'Inscribed' else tuple(
                                (a+b)/2 for a, b in zip(lines[0][-2], lines[0][0]))
                            self.assertLess(math.dist(contact, point), 1e-8)
                            picks.append(point)
                        self.assertLess(math.dist(picks[0], picks[2]), 1e-8)
                        self.assertLess(math.dist(picks[0], picks[4]), 1e-8)
                        ui['_ortho_on'] = False
                        # Move again AFTER setting radius: bearing changes, radius does not.
                        final = ui['constrain_pick'](cmd, plane.to_world(4, 9))
                        self.assertGreater(math.dist(final, picks[0]), 1)
                        self.assertTrue(cmd.feed(coords.format_world(final), ctx)[1])
                        points = ctx.added[-1].ToNurbsCurve().Points
                        self.assertEqual(len(points), 6)
                        contact = (points[0].X, points[0].Y, points[0].Z)
                        if mode == 'Circumscribed':
                            last = points[len(points)-2]
                            contact = tuple((a+b)/2 for a, b in zip(contact, (last.X, last.Y, last.Z)))
                        self.assertLess(math.dist(contact, final), 1e-8)
                        self.assertEqual(ctx.undo.undo_names(), ['Polygon'])

    def test_gui_pick_oracle_matches_existing_ui_ortho_and_radius(self):
        # Validate the new GUI oracle without launching its module/window.
        source = ROOT/'spike/test_polygon_behavior_gui.py'
        names = {'world', 'expected_pick'}
        nodes = [node for node in ast.parse(source.read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
        self.assertEqual({node.name for node in nodes}, names)
        oracle = dict(math=math)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), oracle)
        for label, plane in zip(('Top', 'Front', 'Right'),
                                (cplane.WORLD_TOP, cplane.WORLD_FRONT, cplane.WORLD_RIGHT)):
            ctx = Context(plane); cmd = self.begin(ctx); cmd.feed('2,3', ctx)
            ui, state = constraint_ui(cmd, ctx)
            for fixed in (False, True):
                state.input_buffer = '5' if fixed else ''
                for persistent, shift in ((False, False), (False, True), (True, False), (True, True)):
                    ui['_ortho_on'] = persistent
                    for u, v in ((8, 5), (4, 8), (-4, 1)):
                        raw = plane.to_world(u, v)
                        actual = ui['constrain_pick'](cmd, ui['apply_ortho'](cmd, raw, None, shift))
                        expected = oracle['expected_pick'](raw, label, fixed, persistent, shift)
                        self.assertLess(math.dist(actual, expected), 1e-8)

    def test_geometry_failure_and_publication_exception_do_not_remember(self):
        for failure in ('zero_radius', 'builder', 'none_geometry', 'publication', 'undo_end'):
            with self.subTest(failure=failure):
                ctx = Context(); cmd = self.begin(ctx); self.set_count(cmd, ctx)
                cmd.feed('2,3', ctx)
                if failure == 'zero_radius':
                    msg, done = cmd.feed('2,3', ctx)
                    self.assertTrue(done); self.assertIn('positive', msg)
                else:
                    target, method, kwargs = {
                        'builder': (bridge, 'make_polygon', {'side_effect': ValueError('fixture build refusal')}),
                        'none_geometry': (bridge, 'make_polygon', {'return_value': None}),
                        'publication': (ctx, 'add_geometry', {'side_effect': RuntimeError('fixture publish refusal')}),
                        'undo_end': (ctx, 'end_undo', {'side_effect': RuntimeError('fixture commit refusal')}),
                    }[failure]
                    with patch.object(target, method, **kwargs):
                        try:
                            message, _ = cmd.feed('4,3', ctx)
                            self.assertNotIn('Created', message or '')
                        except (ValueError, RuntimeError):
                            pass
                self.assertEqual(ctx._polygon_num_sides, 5)
                ctx.undo.abandon()

    def test_off_origin_typed_and_clicked_points_in_three_cplanes(self):
        for plane in (cplane.WORLD_TOP, cplane.WORLD_FRONT, cplane.WORLD_RIGHT):
            for clicked in (False, True):
                with self.subTest(plane=plane, clicked=clicked):
                    ctx = Context(plane); cmd = self.begin(ctx); self.set_count(cmd, ctx)
                    center = plane.to_world(2, 3)
                    center_input = coords.format_world(center) if clicked else '2,3'
                    corner_input = coords.format_world(plane.to_world(4, 3)) if clicked else '4,3'
                    curve = self.commit(cmd, ctx, center_input, corner_input)
                    n = curve.ToNurbsCurve(); self.assertTrue(curve.IsClosed)
                    self.assertEqual(len(n.Points), 9)
                    for i, point in enumerate(n.Points):
                        expected = plane.to_world(2 + 2*math.cos(2*math.pi*i/8),
                                                  3 + 2*math.sin(2*math.pi*i/8))
                        self.assertLess(math.dist((point.X,point.Y,point.Z),expected), 1e-8)

    def test_count_can_be_changed_explicitly_after_center(self):
        ctx = Context(); cmd = self.begin(ctx)
        cmd.feed('2,3', ctx); cmd.feed('NumSides=8', ctx)
        self.assertEqual(cmd.args, [(2.,3.,0.)])
        self.assertTrue(cmd.feed('4,3', ctx)[1])
        self.assertEqual(len(ctx.added[0].ToNurbsCurve().Points), 9)

    def test_rotated_corner_and_midpoint_in_active_cplane(self):
        planes = (cplane.WORLD_TOP, cplane.WORLD_FRONT, cplane.WORLD_RIGHT,
                  cplane.CPlane((11, -7, 4), (1, 2, 2), (2, 1, -2)))
        for plane in planes:
            for mode in ('Inscribed', 'Circumscribed'):
                for du, dv in ((3, 4), (-4, 3), (-3, -4)):
                    with self.subTest(mode=mode, direction=(du, dv), plane=plane):
                        ctx = Context(plane); cmd = self.begin(ctx); self.set_count(cmd, ctx)
                        cmd.feed(mode, ctx)
                        self.assertEqual(cmd.steps[1][1], commands.POINT)
                        curve = self.commit(cmd, ctx, '2,3,1', f'{2+du},{3+dv},3')
                        points = [(p.X, p.Y, p.Z) for p in curve.ToNurbsCurve().Points]
                        angle = math.atan2(dv, du)
                        corner_radius = 5 if mode == 'Inscribed' else 5 / math.cos(math.pi/8)
                        phase = 0 if mode == 'Inscribed' else math.pi/8
                        for i, point in enumerate(points):
                            expected = plane.to_world(2+corner_radius*math.cos(angle+phase+i*math.pi/4),
                                                      3+corner_radius*math.sin(angle+phase+i*math.pi/4), 1)
                            self.assertLess(math.dist(point, expected), 1e-8)
                        contact = points[0] if mode == 'Inscribed' else tuple(
                            (a+b)/2 for a, b in zip(points[-2], points[0]))
                        self.assertLess(math.dist(contact, plane.to_world(2+du, 3+dv, 1)), 1e-8)

    def test_right_view_reported_live_radius5_bearing(self):
        # Owner/Main's rounded native What values, not a fresh VM probe here.
        ctx = Context(cplane.WORLD_RIGHT); cmd = self.begin(ctx)
        scale = 5 / math.hypot(4.866, 1.148)
        curve = self.commit(cmd, ctx, '0,0', f'{4.866*scale},{1.148*scale}')
        points = curve.ToNurbsCurve().Points
        for point, reported in zip(points, ((0, 4.866, 1.148), (0, .412, 4.983))):
            self.assertLess(math.dist((point.X, point.Y, point.Z), reported), .001)
        self.assertEqual(len(points), 6)


class PolygonPreview(unittest.TestCase):
    begin = PolygonBehavior.begin
    set_count = PolygonBehavior.set_count
    PLANES = (cplane.WORLD_TOP, cplane.WORLD_FRONT, cplane.WORLD_RIGHT,
              cplane.CPlane((11, -7, 4), (1, 2, 2), (2, 1, -2)))

    def outline(self, cmd, ctx, cursor):
        lines = preview.segments('Polygon', cmd.args, cursor, ctx.plane, cmd=cmd)
        self.assertEqual(len(lines), 1, 'one live Polygon outline')
        self.assertEqual(len(lines[0]), cmd.opt['NumSides'] + 1, 'draft count, not a circle')
        self.assertLess(math.dist(lines[0][0], lines[0][-1]), 1e-8)
        return lines[0]

    def matches(self, points, curve):
        nurbs = curve.ToNurbsCurve()
        self.assertTrue(curve.IsValid and curve.IsClosed)
        self.assertEqual(nurbs.Degree, 1)
        self.assertEqual(len(points), len(nurbs.Points))
        for actual, expected in zip(points, nurbs.Points):
            self.assertLess(math.dist(actual, (expected.X, expected.Y, expected.Z)), 1e-8)

    def test_live_count_before_radius_changes_and_cancel(self):
        ctx = Context(); cmd = self.begin(ctx); self.set_count(cmd, ctx)
        self.assertEqual(preview.segments('Polygon', [], (4, 3, 0), ctx.plane, cmd=cmd), [])
        cmd.feed('2,3', ctx)
        self.outline(cmd, ctx, (4, 3, 0))
        self.assertFalse(cmd.feed('NumSides=7', ctx)[1])
        self.outline(cmd, ctx, (4, 3, 0))
        self.assertEqual(ctx.model.object_count(), 0)
        self.assertEqual(ctx.undo.undo_names(), [])
        self.assertEqual(ctx._polygon_num_sides, 5)
        self.assertTrue(cmd.feed('', ctx)[1])
        fresh = self.begin(ctx); fresh.feed('2,3', ctx)
        self.assertEqual(fresh.opt['NumSides'], 5)
        self.outline(fresh, ctx, (4, 3, 0))

    def test_center_mode_preview_matches_producer_in_all_frames(self):
        for plane in self.PLANES:
            for mode in ('Inscribed', 'Circumscribed'):
                for count in (3, 5, 8, 11):
                    for clicked in (False, True):
                        with self.subTest(mode=mode, count=count, clicked=clicked, plane=plane):
                            ctx = Context(plane); cmd = self.begin(ctx)
                            cmd.feed('NumSides='+str(count), ctx); cmd.feed(mode, ctx)
                            cmd.feed('2,3,1', ctx)
                            # Elevated input is projected into the center's plane;
                            # its in-plane bearing reaches a corner or edge midpoint.
                            cursor = plane.to_world(5, 7, 3)
                            before = self.outline(cmd, ctx, cursor)
                            contact = before[0] if mode == 'Inscribed' else tuple(
                                (a+b)/2 for a, b in zip(before[-2], before[0]))
                            self.assertLess(math.dist(contact, plane.to_world(5, 7, 1)), 1e-8)
                            rotated = self.outline(cmd, ctx, plane.to_world(-2, 6, 1))
                            self.assertGreater(math.dist(before[0], rotated[0]), 1)
                            settled = preview.segments('Polygon', [cmd.args[0], cursor],
                                                       plane.to_world(99, 99), plane, cmd=cmd)
                            self.assertEqual(settled, [before])
                            text = coords.format_world(cursor) if clicked else '5,7,3'
                            self.assertTrue(cmd.feed(text, ctx)[1])
                            self.matches(before, ctx.added[-1])

    def test_edge_flip_preview_matches_producer_in_all_frames(self):
        for plane in self.PLANES:
            for flip in (False, True):
                for count in (3, 5, 8, 11):
                    with self.subTest(flip=flip, count=count, plane=plane):
                        ctx = Context(plane); cmd = self.begin(ctx)
                        cmd.feed('NumSides='+str(count), ctx); cmd.feed('Edge', ctx)
                        cmd.feed('2,3,1', ctx)
                        if flip:
                            cmd.feed('Flip=Yes', ctx)
                        cursor = plane.to_world(-1, 7, 3)
                        before = self.outline(cmd, ctx, cursor)
                        self.assertLess(math.dist(before[0], plane.to_world(2, 3, 1)), 1e-8)
                        self.assertLess(math.dist(before[1], plane.to_world(-1, 7, 1)), 1e-8)
                        settled = preview.segments('Polygon', [cmd.args[0], cursor],
                                                   plane.to_world(99, 99), plane, cmd=cmd)
                        self.assertEqual(settled, [before])
                        self.assertTrue(cmd.feed(coords.format_world(cursor), ctx)[1])
                        self.matches(before, ctx.added[-1])

    def test_live_mode_and_flip_changes_are_not_cached(self):
        ctx = Context(); cmd = self.begin(ctx); self.set_count(cmd, ctx)
        cmd.feed('2,3', ctx)
        inscribed = self.outline(cmd, ctx, (5, 7, 0))
        cmd.feed('Circumscribed', ctx)
        circumscribed = self.outline(cmd, ctx, (5, 7, 0))
        self.assertNotEqual(inscribed, circumscribed)
        cmd.feed('Inscribed', ctx)
        self.assertEqual(self.outline(cmd, ctx, (5, 7, 0)), inscribed)
        cmd.feed('', ctx)
        cmd = self.begin(ctx); cmd.feed('Edge', ctx); cmd.feed('2,3', ctx)
        left = self.outline(cmd, ctx, (5, 7, 0))
        cmd.feed('Flip=Yes', ctx)
        self.assertNotEqual(left, self.outline(cmd, ctx, (5, 7, 0)))
        cmd.feed('Flip=No', ctx)
        self.assertEqual(left, self.outline(cmd, ctx, (5, 7, 0)))
        cmd.feed('NumSides=8', ctx)
        self.outline(cmd, ctx, (5, 7, 0))

    def test_legacy_helper_and_dispatch_without_command(self):
        plane = cplane.WORLD_FRONT; center = (2, 0, 3); cursor = (4, 0, 3)
        for args, size in (([center], 65), ([center, 2.], 65), ([center, 2., 8], 9)):
            direct = preview.preview_polygon(args, cursor, plane)
            self.assertEqual(len(direct), 1)
            self.assertEqual(len(direct[0]), size)
            self.assertEqual(preview.segments('Polygon', args, cursor, plane), direct)
        curve = bridge.make_polygon(center, 2, 8, plane=plane)
        self.matches(preview.preview_polygon([center, 2., 8], cursor, plane)[0], curve)

    def test_no_geometry_build_or_state_changes_during_repeated_draws(self):
        ctx = Context(); cmd = self.begin(ctx); self.set_count(cmd, ctx)
        cmd.feed('2,3', ctx)
        before = copy.deepcopy((cmd.args, cmd.opt, cmd.step_index, ctx._polygon_num_sides))
        with ExitStack() as stack:
            for name in dir(bridge):
                if name.startswith('make_'):
                    stack.enter_context(patch.object(bridge, name,
                        side_effect=AssertionError('preview called geometry producer')))
            for _ in range(20):
                self.outline(cmd, ctx, (5, 7, 0))
            cmd._edge = True
            for _ in range(20):
                self.outline(cmd, ctx, (5, 7, 0))
        self.assertEqual((cmd.args, cmd.opt, cmd.step_index, ctx._polygon_num_sides), before)
        self.assertEqual(ctx.model.object_count(), 0)
        self.assertEqual(ctx.undo.undo_names(), [])

    def test_degenerate_and_allocation_bounds(self):
        ctx = Context(); cmd = self.begin(ctx); cmd.feed('2,3', ctx)
        self.assertEqual(preview.segments('Polygon', cmd.args, (2, 3, 0), ctx.plane, cmd=cmd), [])
        cmd._edge = True
        self.assertEqual(preview.segments('Polygon', cmd.args, (2, 3, 7), ctx.plane, cmd=cmd), [])
        for count in (2, 3.5, float('inf'), float('nan'), 10001):
            cmd.opt['NumSides'] = count
            with patch.object(preview, 'polygon_polyline') as helper:
                self.assertEqual(preview.segments('Polygon', cmd.args, (5, 7, 0), ctx.plane, cmd=cmd), [])
                helper.assert_not_called()
        cmd.opt['NumSides'] = 10000
        self.outline(cmd, ctx, (5, 7, 0))


def preview_mutations():
    import inspect
    probes = [
        ('old-circle-third-argument', preview.preview_polygon,
         "    center = args[0]\n", "    cmd = None\n    center = args[0]\n",
         'test_live_count_before_radius_changes_and_cancel'),
        ('stale-preview-count', preview.preview_polygon,
         "getattr(cmd, 'opt', {}).get('NumSides')", '5',
         'test_live_count_before_radius_changes_and_cancel'),
        ('inscribed-only', preview.preview_polygon,
         "inscribed=getattr(cmd, '_inscribed', True)", 'inscribed=True',
         'test_center_mode_preview_matches_producer_in_all_frames'),
        ('circumscribed-phase', preview.polygon_polyline,
         'start_angle += 0.0 if inscribed else math.pi / sides', 'start_angle += 0.0',
         'test_center_mode_preview_matches_producer_in_all_frames'),
        ('edge-is-radius', preview.preview_polygon,
         "if getattr(cmd, '_edge', False):", 'if False:',
         'test_edge_flip_preview_matches_producer_in_all_frames'),
        ('ignore-flip', preview.preview_polygon,
         "direction = -1.0 if cmd.opt.get('Flip') else 1.0", 'direction = 1.0',
         'test_edge_flip_preview_matches_producer_in_all_frames'),
        ('drop-edge-elevation', preview.preview_polygon,
         'plane.to_world(cu, cv, aw)', 'plane.to_world(cu, cv, 0)',
         'test_edge_flip_preview_matches_producer_in_all_frames'),
        ('world-xy-preview', preview.polygon_polyline,
         'u, v = plane.xaxis, plane.yaxis', 'u, v = (1, 0, 0), (0, 1, 0)',
         'test_center_mode_preview_matches_producer_in_all_frames'),
        ('frozen-preview-bearing', preview.preview_polygon,
         'start_angle = math.atan2(dv, du)', 'start_angle = 0.0',
         'test_center_mode_preview_matches_producer_in_all_frames'),
        ('unbounded-draw-count', preview.preview_polygon,
         'count <= 10_000', 'count <= 20_000',
         'test_degenerate_and_allocation_bounds'),
    ]
    for label, fn, old, new, test in probes:
        source = inspect.getsource(fn)
        assert source.count(old) == 1, ('mutation anchor changed', label)
        # Use the module globals so helper mutations reach the real dispatcher.
        namespace = {}
        exec(compile(source.replace(old, new, 1), preview.__file__, 'exec'), preview.__dict__, namespace)
        mutated = namespace[fn.__name__]
        with patch.object(preview, fn.__name__, mutated), ExitStack() as stack:
            if fn.__name__ == 'preview_polygon':
                stack.enter_context(patch.dict(preview.PREVIEWS_WITH_CMD, polygon=mutated))
            result = unittest.TextTestRunner(stream=io.StringIO()).run(PolygonPreview(test))
        assert result.failures and not result.errors, ('expected assertion RED', label)
        print('RED verified:', label)
    with patch.dict(preview.PREVIEWS_WITH_CMD, {}, clear=True), \
            patch.dict(preview.PREVIEWS, polygon=preview.preview_polygon):
        result = unittest.TextTestRunner(stream=io.StringIO()).run(
            PolygonPreview('test_live_count_before_radius_changes_and_cancel'))
    assert result.failures and not result.errors, 'expected dispatcher assertion RED'
    print('RED verified: missing-command-dispatch')


def constraint_mutations():
    original = constraint_ui
    for label, symbol, broken in (
            ('ui-no-submitted-radius', 'handle_constraint_line', lambda line: False),
            ('ui-no-pending-radius', '_typed_constraint', lambda cmd: None),
            ('ui-no-ortho-shift', 'apply_ortho', lambda cmd, point, area, shift: point)):
        def faulty(cmd, ctx):
            namespace, state = original(cmd, ctx)
            namespace[symbol] = broken
            return namespace, state
        with patch.dict(globals(), constraint_ui=faulty):
            result = unittest.TextTestRunner(stream=io.StringIO()).run(
                PolygonBehavior('test_ui_numeric_radius_waits_for_direction_and_ortho_xor_shift'))
        assert result.failures and not result.errors, ('expected UI-path assertion RED', label)
        restored = unittest.TextTestRunner(stream=io.StringIO()).run(
            PolygonBehavior('test_ui_numeric_radius_waits_for_direction_and_ortho_xor_shift'))
        assert restored.wasSuccessful(), ('UI path did not restore', label)
        print('RED -> restored GREEN verified:', label)


def mutations():
    import inspect
    source = inspect.getsource(commands.PolygonCmd)
    probes = [
        ('premature-memory', 'self._num_sides = count\n        self.opt',
         'self._num_sides = count\n        ctx._polygon_num_sides = count\n        self.opt',
         'test_count_at_center_is_draft_and_cancel_does_not_remember'),
        ('missing-memory', 'ctx._polygon_num_sides = self._published_num_sides', 'pass',
         'test_commit_remembers_after_existing_transaction_and_uses_fresh_begin'),
        ('late-sides-prompt', 'steps = [("Center of inscribed polygon:", POINT), ("Corner of polygon:", POINT)]',
         'steps = [("Center of inscribed polygon:", POINT), ("Corner of polygon:", POINT), ("Sides:", NUMBER)]',
         'test_commit_remembers_after_existing_transaction_and_uses_fresh_begin'),
        ('wrong-count', '\n        sides = self._num_sides\n', '\n        sides = 3\n',
         'test_commit_remembers_after_existing_transaction_and_uses_fresh_begin'),
        ('stale-prototype', "getattr(ctx, '_polygon_num_sides', 5)", '5',
         'test_commit_remembers_after_existing_transaction_and_uses_fresh_begin'),
        ('before-undo-end', 'self._published_num_sides = sides',
         'self._published_num_sides = sides\n        ctx._polygon_num_sides = sides',
         'test_geometry_failure_and_publication_exception_do_not_remember'),
        ('fractional-count', 'not count.is_integer()', 'False',
         'test_invalid_count_does_not_poison_draft_or_progress'),
        ('scalar-completion-step', '("Corner of polygon:", POINT)', '("Corner of polygon:", DISTANCE)',
         'test_ui_numeric_radius_waits_for_direction_and_ortho_xor_shift'),
        ('fixed-commit-frame', 'plane=oriented)', 'plane=plane)',
         'test_rotated_corner_and_midpoint_in_active_cplane'),
        ('spatial-not-planar-radius', 'radius = math.hypot(du, dv)', 'radius = math.dist(center, corner)',
         'test_rotated_corner_and_midpoint_in_active_cplane'),
    ]
    for label, old, new, test in probes:
        assert source.count(old) == 1, ('mutation anchor changed', label)
        namespace = dict(commands.__dict__)
        exec(compile(source.replace(old, new, 1), commands.__file__, 'exec'), namespace)
        mutated = namespace['PolygonCmd']
        with patch.object(commands, 'PolygonCmd', mutated), \
                patch.dict(commands.COMMANDS, {'polygon': mutated()}):
            result = unittest.TextTestRunner(stream=io.StringIO()).run(PolygonBehavior(test))
        assert result.failures and not result.errors, ('expected assertion RED', label)
        print('RED verified:', label)


if __name__ == '__main__':
    if '--mutations' in sys.argv:
        mutations()
        preview_mutations()
        constraint_mutations()
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls)
                               for cls in (PolygonBehavior, PolygonPreview))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
