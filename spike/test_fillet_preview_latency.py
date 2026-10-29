"""Headless real-OCCT Fillet drag scheduling/acceptance tests; no GUI or builds.

Bundled Python -B + PYTHONPATH=vendor/occt + OCCT LD_LIBRARY_PATH.
--mutations re-breaks production functions in memory, then restores them.
--timings reports source-call costs on a 10 mm box, not GUI frame latency.
Python only tests UI orchestration of existing native kernel operations.
"""
import ast
from contextlib import ExitStack
import inspect
import io
import json
import math
import statistics
import sys
import textwrap
import time
import types
import unittest
from unittest.mock import patch

from test_fillet_handle_input import cache, fixture
from narwhal3d import bridge, edge_handles as handles, kernel, solid_commands as solid
from narwhal3d.kernel import occt_backend as occt


class TimerQueue:
    """Deterministic main-thread timers; tests may retain an already-queued callback."""
    def __init__(self):
        self.pending = []
        self.intervals = []

    def register(self, callback, *, first_interval):
        assert callback not in self.pending
        self.pending.append(callback)
        self.intervals.append(first_interval)

    def is_registered(self, callback):
        return callback in self.pending

    def unregister(self, callback):
        self.pending.remove(callback)

    def tick(self):
        assert len(self.pending) == 1, self.pending
        callback = self.pending.pop(0)
        assert callback() is None, "one-shot callback must not become a busy timer"


class DragTests(unittest.TestCase):
    def setUp(self):
        self.cmd, self.ctx, self.handle = fixture()
        self.original = self.ctx.model.file.Encode()
        self.timers = TimerQueue()
        self.logs, self.redraws = [], []
        self.state = types.SimpleNamespace(active_command=self.cmd, ctx=self.ctx,
                                           input_buffer='', history=[], feed_line=self.feed,
                                           log=self.logs.append)
        self.ui = types.SimpleNamespace(
            app_state=self.state, _fpx=lambda value: value,
            _point_in_view3d=lambda x, y: True,
            _tag_redraw_viewports=lambda: self.redraws.append('view'),
            redraw_cmdbar=lambda: self.redraws.append('bar'))
        self.patches = ExitStack()
        self.patches.enter_context(patch.dict(sys.modules, {
            'bpy': types.SimpleNamespace(app=types.SimpleNamespace(timers=self.timers))}))
        self.patches.enter_context(patch.object(handles, '_ui', return_value=self.ui))
        self.patches.enter_context(patch.object(handles, 'boxes', side_effect=self.boxes))
        self.patches.enter_context(patch.object(handles, '_drag', None))
        self.patches.enter_context(patch.object(handles, '_preview_timer', None))
        self.patches.enter_context(patch.object(handles, '_numeric', None))
        self.addCleanup(self.patches.close)
        self.addCleanup(handles._drop_drag)

    def boxes(self):
        return [dict(cmd=self.cmd, id=self.handle, radius=self.cmd._edge_radii[1][0],
                     axis=(1, 0), pixels_per_unit=100, grip=(90, 90, 110, 110),
                     label=(120, 90, 150, 110))]

    def feed(self, line):
        message, done = self.state.active_command.feed(line, self.state.ctx)
        if message:
            self.logs.append(message)
        return message, done

    def event(self, kind, value='PRESS', x=100, y=100):
        return handles.handle_event(types.SimpleNamespace(
            type=kind, value=value, mouse_x=x, mouse_y=y))

    def start(self):
        self.assertTrue(self.event('LEFTMOUSE'))
        self.assertTrue(self.cmd.handle_input_active())

    def move(self, x):
        self.assertTrue(self.event('MOUSEMOVE', 'NOTHING', x=x))

    def unchanged(self):
        self.assertEqual(self.ctx.model.file.Encode(), self.original)
        self.assertFalse(self.ctx.undo.can_undo)
        self.assertEqual(self.ctx.added, [])

    def assert_live(self, radius):
        self.assertAlmostEqual(self.cmd._edge_radii[1][0], radius)
        self.assertEqual(self.cmd._edge_radii[1][1], .5)
        self.assertTrue(self.cmd.preview_lines())
        self.assertTrue(all(len(line) >= 2 for line in self.cmd.preview_lines()))
        self.assertIsNone(self.cmd._candidate)
        self.assertIsNone(self.cmd._candidate_key)
        self.assertIsNone(self.cmd.preview_geometry())
        record = next(row for row in self.cmd.handle_records() if row['id'] == self.handle)
        self.assertAlmostEqual(record['radius'], radius)
        self.assertEqual(record['grip_point'], tuple(self.cmd._sections[1][0]['grip_point']))
        self.assertAlmostEqual(self.cmd._sections[1][0]['curve']['radius'], radius)
        self.assertIn('pending', self.logs[-1])
        self.unchanged()

    def test_motion_is_kernel_free_timer_builds_sections_only_for_both_preview_modes(self):
        self.start()
        with patch.object(self.cmd, '_refresh_candidate', wraps=self.cmd._refresh_candidate) as full, \
             patch.object(self.cmd, '_refresh_sections', wraps=self.cmd._refresh_sections) as sections, \
             patch.object(bridge, '_proxy_from_occt', wraps=bridge._proxy_from_occt) as proxy:
            for preview, x in ((False, 120), (True, 130)):
                self.cmd.opt['Preview'] = preview
                previous = sections.call_count
                self.move(x)
                self.assertEqual(sections.call_count, previous, 'kernel on mouse motion')
                self.assertEqual(len(self.timers.pending), 1)
                self.assertLessEqual(self.timers.intervals[-1], 1 / 60)
                self.timers.tick()
                self.assertEqual(sections.call_count, previous + 1)
                self.assert_live(.5 + (x - 100) / 100)
                self.assertEqual(full.call_count, 0, 'full candidate built during motion')
                self.assertEqual(proxy.call_count, 0, 'proxy built during motion')
        self.assertTrue(self.redraws)

    def test_burst_coalesces_and_trailing_value_runs_without_more_mouse_events(self):
        self.start()
        with patch.object(self.cmd, '_refresh_sections', wraps=self.cmd._refresh_sections) as sections:
            for x in (110, 120, 115, 140):
                self.move(x)
            self.assertEqual(len(self.timers.pending), 1)
            self.assertEqual(sections.call_count, 0)
            self.timers.tick()
            self.assertEqual(sections.call_count, 1)
            self.assert_live(.9)
            self.move(125)  # Latest, not maximum: reversing the drag must work too.
            self.timers.tick()
            self.assert_live(.75)
            self.assertFalse(self.timers.pending)
            self.move(125)
            self.timers.tick()
            self.assertEqual(sections.call_count, 2, 'identical live value was simulated again')

    def test_release_flushes_latest_once_and_command_commit_reuses_exact_candidate(self):
        self.start()
        with patch.object(self.cmd, '_refresh_candidate', wraps=self.cmd._refresh_candidate) as full:
            self.move(115)
            self.timers.tick()
            self.move(145)
            queued = self.timers.pending[0]
            self.assertTrue(self.event('LEFTMOUSE', 'RELEASE'))
            self.assertEqual(full.call_count, 1)
            self.assertFalse(self.timers.pending)
            self.assertIsNone(handles._drag)
            self.assertFalse(self.cmd.handle_input_active())
            self.assertAlmostEqual(self.cmd._edge_radii[1][0], .95)
            self.assertEqual(self.cmd._candidate_key, self.cmd._state_key())
            candidate = self.cmd._candidate
            self.assertIsNotNone(candidate)
            self.assertEqual(kernel.recipe_of(candidate)['edge_radii'], [[.95, .5]])
            master = kernel.master_of(candidate)
            self.assertTrue(occt.is_valid(master) and occt.is_airtight(master))
            self.unchanged()
            queued()
            self.event('LEFTMOUSE', 'RELEASE')
            self.assertEqual(full.call_count, 1)
            message, done = self.cmd.feed('', self.ctx)
            self.assertTrue(done, message)
            self.assertEqual(full.call_count, 1, 'commit rebuilt a current exact candidate')
            self.assertEqual(self.ctx.added, [candidate])
            self.assertEqual(self.ctx.undo.undo_names(), ['FilletEdge'])
            record = self.ctx.undo.undo(self.ctx.model.snapshot())
            self.ctx.model.restore(record.before)
            self.assertEqual(self.ctx.names(), ['Box'])
            self.assertEqual(kernel.recipe_of(self.ctx.geom_by_name('Box'))['kind'], 'box')

    def test_release_before_first_timer_still_builds_only_final_value(self):
        self.start()
        self.move(155)
        callback = self.timers.pending[0]
        with patch.object(self.cmd, '_refresh_candidate', wraps=self.cmd._refresh_candidate) as full:
            self.event('LEFTMOUSE', 'RELEASE')
            callback()
            self.assertEqual(full.call_count, 1)
        self.assertAlmostEqual(self.cmd._edge_radii[1][0], 1.05)
        self.assertEqual(self.cmd._candidate_key, self.cmd._state_key())
        self.unchanged()

    def test_escape_cancels_before_restoration_and_restores_cache_identity(self):
        for failed in (False, True):
            if failed:
                self.cmd.set_handle_radius(self.handle, float('nan'), self.ctx)
            original = cache(self.cmd)
            self.start()
            self.move(120)
            self.timers.tick()
            self.move(135)
            queued = self.timers.pending[0]
            cancel = self.cmd.cancel_handle_input
            def checked_cancel(ctx):
                self.assertFalse(self.timers.pending, 'cancel must unregister before restore')
                self.assertIsNone(handles._preview_timer)
                return cancel(ctx)
            with patch.object(self.cmd, 'cancel_handle_input', side_effect=checked_cancel):
                self.event('ESC')
            queued()
            self.assertEqual(self.cmd._edge_radii[1], (.5, .5))
            self.assertTrue(all(a is b for a, b in zip(cache(self.cmd), original)))
            self.assertIsNone(handles._drag)
            self.unchanged()

    def test_old_callback_cannot_clear_or_consume_new_drag_timer(self):
        self.start()
        self.move(120)
        old = self.timers.pending[0]
        self.event('ESC')
        self.start()  # Same command, new subedit and drag generation.
        self.move(150)
        new = self.timers.pending[0]
        old()
        self.assertIs(handles._preview_timer, new)
        self.assertEqual(self.cmd._edge_radii[1], (.5, .5))
        self.timers.tick()
        self.assert_live(1.0)

    def test_timer_rejects_changed_active_command(self):
        self.start()
        self.move(120)
        self.state.active_command = solid.FilletEdgeCmd()
        with patch.object(self.cmd, 'set_handle_radius', wraps=self.cmd.set_handle_radius) as setter:
            self.timers.tick()
            self.assertEqual(setter.call_count, 0)
        self.assertEqual(self.cmd._edge_radii[1], (.5, .5))
        self.unchanged()

    def test_timer_rejects_replaced_subedit_on_same_command(self):
        self.start()
        self.move(120)
        self.cmd.cancel_handle_input(self.ctx)
        self.assertTrue(self.cmd.begin_handle_input(self.handle, self.ctx))
        self.timers.tick()
        self.assertEqual(self.cmd._edge_radii[1], (.5, .5))
        self.assertTrue(self.cmd.handle_input_active())
        self.unchanged()

    def test_enter_during_drag_uses_latest_or_explicit_typed_value_once(self):
        for text, expected in (('', .9), ('0.75', .75)):
            self.start()
            self.move(140)
            queued = self.timers.pending[0]
            self.state.input_buffer = text
            with patch.object(self.cmd, '_refresh_candidate', wraps=self.cmd._refresh_candidate) as full:
                self.event('RET')
                queued()
                self.assertEqual(full.call_count, 1)
            self.assertFalse(self.cmd.handle_input_active())
            self.assertAlmostEqual(self.cmd._edge_radii[1][0], expected)
            self.assertEqual(self.cmd._candidate_key, self.cmd._state_key())
            self.unchanged()
            self.cmd.set_handle_radius(self.handle, .5, self.ctx)

    def test_section_failure_reports_no_stale_lines_or_candidate(self):
        self.start()
        with patch.object(bridge, 'bevel_edge_sections_occt', side_effect=RuntimeError('section refused')), \
             patch.object(self.cmd, '_refresh_candidate', wraps=self.cmd._refresh_candidate) as full:
            self.move(120)
            self.timers.tick()
            self.assertEqual(full.call_count, 0)
        self.assertEqual(self.cmd.preview_lines(), ())
        self.assertEqual(self.cmd.preview_section_curves(), ())
        self.assertIsNone(self.cmd._candidate)
        self.assertIn('section refused', self.logs[-1])
        self.assertIn('pending', self.logs[-1])
        self.assertAlmostEqual(self.cmd.handle_records()[0]['radius'], .7)
        self.event('LEFTMOUSE', 'RELEASE')
        self.assertIsNotNone(self.cmd._candidate)
        self.unchanged()

    def test_failed_final_validation_cannot_accept_previous_success(self):
        self.start()
        self.move(120)
        self.timers.tick()
        with patch.object(bridge, 'bevel_edges_occt', side_effect=RuntimeError('final refused')):
            self.event('LEFTMOUSE', 'RELEASE')
        self.assertIsNone(self.cmd._candidate)
        self.assertEqual(self.cmd._candidate_key, self.cmd._state_key())
        self.assertIn('final refused', self.logs[-1])
        with patch.object(self.cmd, '_refresh_candidate', wraps=self.cmd._refresh_candidate) as full:
            message, done = self.cmd.feed('', self.ctx)
            self.assertTrue(done)
            self.assertIn('Nothing was changed', message)
            self.assertEqual(full.call_count, 0)
        self.unchanged()

    def test_preview_only_finish_then_command_accept_rebuilds_current_parameters(self):
        self.start()
        self.move(130)
        self.timers.tick()
        self.cmd.finish_handle_input()
        with patch.object(self.cmd, '_refresh_candidate', wraps=self.cmd._refresh_candidate) as full:
            message, done = self.cmd.feed('', self.ctx)
            self.assertTrue(done, message)
            self.assertEqual(full.call_count, 1)
        self.assertEqual(kernel.recipe_of(self.ctx.added[0])['edge_radii'], [[.8, .5]])

    def test_click_jitter_numeric_label_and_default_setter_remain_exact(self):
        self.start()
        self.move(101)
        self.assertFalse(self.timers.pending)
        self.event('LEFTMOUSE', 'RELEASE')
        self.assertTrue(self.cmd.handle_input_active())
        self.assertIsNone(handles._drag)
        self.state.input_buffer = '0.8'
        self.event('RET')
        self.assertIsNotNone(self.cmd._candidate)
        handles._numeric = dict(cmd=self.cmd, id=self.handle, radius=.8)
        with patch.object(self.cmd, '_refresh_candidate', wraps=self.cmd._refresh_candidate) as full:
            handles.numeric_apply('0.9')
            self.cmd.set_handle_radius(self.handle, .7, self.ctx)
            self.assertEqual(full.call_count, 2)
        self.assertIsNotNone(self.cmd._candidate)
        self.unchanged()

    def test_chamfer_keeps_exact_preview_because_it_has_no_section_wires(self):
        self.cmd, self.ctx, self.handle = fixture(chamfer=True)
        self.state.active_command, self.state.ctx = self.cmd, self.ctx
        self.original = self.ctx.model.file.Encode()
        self.cmd.opt['Preview'] = True
        self.start()
        self.move(120)
        self.timers.tick()
        self.assertIsNotNone(self.cmd.preview_geometry())
        self.assertEqual(self.cmd._edge_radii[1], (.7, .7))
        self.unchanged()

    def test_invalid_preview_input_clears_and_cannot_bypass_validation(self):
        for radius in (float('nan'), float('inf'), 0, -1, 'bad'):
            message, done = self.cmd.set_handle_radius(self.handle, radius, self.ctx, preview_only=True)
            self.assertFalse(done)
            self.assertIn('finite and positive', message)
            self.assertIsNone(self.cmd._candidate)
            self.assertEqual(self.cmd.preview_lines(), ())
        self.unchanged()


def mutations():
    cases = (
        (solid, solid._EdgeBevelCmd, 'set_handle_radius', 'if preview_only:', 'if False:',
         'test_motion_is_kernel_free_timer_builds_sections_only_for_both_preview_modes'),
        (handles, handles, '_schedule_preview', 'if _preview_timer is not tick:', 'if False:',
         'test_old_callback_cannot_clear_or_consume_new_drag_timer'),
        (handles, handles, '_drag_is_current', "_ui().app_state.active_command is not drag['cmd']", 'False',
         'test_timer_rejects_changed_active_command'),
        (handles, handles, '_drag_is_current', "drag['cmd']._handle_input is drag['input_session']", 'True',
         'test_timer_rejects_replaced_subedit_on_same_command'),
        (handles, handles, '_drop_drag', '_cancel_preview_timer()', 'pass',
         'test_escape_cancels_before_restoration_and_restores_cache_identity'),
        (handles, handles, 'handle_event', "if _drag['pending'] is not None:", 'if False:',
         'test_release_flushes_latest_once_and_command_commit_reuses_exact_candidate'),
        (handles, handles, '_schedule_preview', "_apply(value, preview_only=not drag['cmd'].chamfer)", 'pass',
         'test_burst_coalesces_and_trailing_value_runs_without_more_mouse_events'),
        (handles, handles, '_schedule_preview', "preview_only=not drag['cmd'].chamfer", 'preview_only=True',
         'test_chamfer_keeps_exact_preview_because_it_has_no_section_wires'),
    )
    for module, owner, name, old, new, test in cases:
        code = textwrap.dedent(inspect.getsource(getattr(owner, name)))
        assert code.count(old) == 1, (name, 'mutation target drifted')
        tree = ast.parse(code.replace(old, new))
        tree.body[0].name = '_latency_mutant'
        # Use actual module globals so global timer writes exercise the real state.
        exec(compile(tree, f'<mutation {name}>', 'exec'), module.__dict__)
        mutant = module.__dict__.pop('_latency_mutant')
        with patch.object(owner, name, mutant):
            result = unittest.TestResult()
            DragTests(test).run(result)
        assert result.failures and not result.errors, (name, result.errors, 'did not go RED')
        print(f'MUTATION RED {name}: {result.failures[0][1].splitlines()[-1]}')
        restored = unittest.TextTestRunner(stream=io.StringIO()).run(DragTests(test))
        assert restored.wasSuccessful(), (name, 'restore failed', restored.errors, restored.failures)
    print(f'{len(cases)} mutations RED; restored tests GREEN')


def timings():
    for count in (1, 2):
        cmd, ctx, handle = fixture(edit=False)
        if count == 2:
            ends = bridge.bevel_edge_endpoints_occt(ctx.geom_by_name('Box'), range(1, 13))
            other = next(i for i, (a, b) in ends.items() if a[:2] == [10., 10.] == b[:2])
            point = tuple((a + b) / 2 for a, b in zip(*ends[other]))
            cmd.pick_edge('Box', other, point, 'plain', ctx)
        cmd.feed('', ctx)
        handle = ('Box', 1, 0)
        before = ctx.model.file.Encode()
        result = {}
        for preview_only in (False, True):
            values = []
            for i in range(31):
                start = time.perf_counter_ns()
                cmd.set_handle_radius(handle, .6 + .002 * i, ctx, preview_only=preview_only)
                values.append((time.perf_counter_ns() - start) / 1e6)
                assert cmd.preview_lines()
                assert (cmd._candidate is None) == preview_only
            result['sections_only' if preview_only else 'exact_default'] = {
                'first_ms': values[0], 'warm_n': 30,
                'warm_median_ms': statistics.median(values[1:]),
                'warm_p95_ms': sorted(values[1:])[math.ceil(.95 * 30) - 1],
                'warm_max_ms': max(values[1:])}
        assert ctx.model.file.Encode() == before
        print(json.dumps({'selected_edges': count, 'source_call_timings': result}))


if __name__ == '__main__':
    if '--mutations' in sys.argv:
        mutations()
    elif '--timings' in sys.argv:
        timings()
    else:
        unittest.main(verbosity=2)
