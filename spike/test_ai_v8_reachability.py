#!/usr/bin/env python3
"""Real assistant functions with injected events/drawing; NOT actual GUI evidence.

Python is Blender event/font integration. Mutants are compiled in private namespaces,
never written over a loaded shared module. Native approval remains separately tested.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch
import test_ai_v7_presentation as presentation

SOURCE = Path(__file__).resolve().parents[1] / 'narwhal3d/ui.py'
MUTATION = None


class Reachability(unittest.TestCase):
    def setUp(self):
        source = SOURCE.read_text()
        if MUTATION:
            old, new = MUTATION
            self.assertEqual(source.count(old), 1, 'unique mutation anchor')
            source = source.replace(old, new, 1)
        names = {'_ai_review_event', '_ai_transcript_event', '_ai_draw_action_review'}
        nodes = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name in names]
        self.calls = []
        self.rectangles = []
        self.pending = {'review': presentation.descriptor(), 'remaining_ms': 300000, 'deciding': False}
        self.state = SimpleNamespace(focus=False)
        self.ai = {'state': self.state, 'action_pending': self.pending,
                   'lay': {'transcript': (0, 0, 300, 700)}, 'region': (0, 0)}
        self.g = dict(__name__='narwhal3d.ui', __package__='narwhal3d', _ai=self.ai,
                      ai_panel_open=lambda: True, _tag_redraw_bands=lambda: None,
                      _ai_decide_action_review=lambda accepted, pending: self.calls.append((accepted, pending)),
                      _inside_box=lambda x, y, b: b[0] <= x < b[2] and b[1] <= y < b[3],
                      _ui_scale=lambda: 1, _bug_measure=lambda size: len,
                      design=SimpleNamespace(FIELD_BG='background', ACCENT='focus', TEXT='text',
                                             CHIP_BG='button', BORDER_STRONG='border', rgb01=lambda c: c),
                      _draw_round_rect=lambda *args: self.rectangles.append(args),
                      _draw_text=lambda *args: None, _draw_centered_panel_text=lambda *args: None)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), self.g)
        self.paint()

    def paint(self, rect=None):
        gpu = SimpleNamespace(state=SimpleNamespace(scissor_get=lambda: (0, 0, 300, 700),
                                                    scissor_set=lambda *args: None))
        with patch.dict(sys.modules, gpu=gpu):
            self.g['_ai_draw_action_review'](SimpleNamespace(bind=lambda: None),
                                             rect or self.ai['lay']['transcript'], 1)

    def event(self, kind, value='PRESS', **kwargs):
        event = SimpleNamespace(type=kind, value=value, mouse_x=30, mouse_y=300,
                                shift=False, ctrl=False, alt=False, oskey=False, is_repeat=False)
        event.__dict__.update(kwargs)
        return self.g['_ai_transcript_event'](event)

    def focus_approve(self):
        self.assertTrue(self.event('TAB'))
        self.assertEqual(self.pending.get('keyboard_focus'), 'reject')
        self.paint()
        self.assertTrue(self.event('TAB'))
        self.assertEqual(self.pending.get('keyboard_focus'), 'approve')
        self.paint()

    def test_tab_return_reaches_painted_reject_then_approve(self):
        self.focus_approve()
        self.assertTrue(self.event('RET'))
        self.assertEqual(self.calls, [(True, self.pending)])
        self.assertIsNone(self.pending.get('keyboard_focus'))

    def test_focus_must_be_painted_before_return(self):
        self.event('TAB'); self.paint()
        self.event('TAB'); self.event('RET')
        self.assertEqual(self.calls, [], 'unpainted Approve must not act')
        self.paint(); self.event('RET')
        self.assertEqual(self.calls, [(True, self.pending)])

    def test_repeated_return_and_new_sibling_need_fresh_tab(self):
        self.focus_approve()
        self.event('RET', is_repeat=True)
        self.event('RET', value='REPEAT')
        self.assertEqual(self.calls, [])
        self.event('RET'); self.event('RET')
        self.assertEqual(self.calls, [(True, self.pending)])
        self.ai['action_pending'] = dict(review=presentation.descriptor(), remaining_ms=300000, deciding=False)
        self.paint(); self.event('RET')
        self.assertEqual(len(self.calls), 1)

    def test_stale_painted_review_cannot_receive_keyboard_focus(self):
        self.focus_approve()
        self.ai['action_pending'] = dict(self.pending)
        self.event('RET')
        self.assertEqual(self.calls, [])

    def test_resize_refuses_hidden_approval_and_clears_focus(self):
        self.focus_approve()
        self.paint((0, 0, 100, 80))
        self.event('RET'); self.event('TAB'); self.event('RET')
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.pending.get('keyboard_focus'))
        self.assertIsNone(self.ai['review_view']['approve'])
        self.paint(); self.event('RET')
        self.assertEqual(self.calls, [])
        self.event('ESC')
        self.assertEqual(self.calls, [(False, self.pending)])

    def test_pointer_blur_and_input_focus_do_not_approve(self):
        self.focus_approve()
        self.event('LEFTMOUSE', mouse_x=400)
        self.event('RET')
        self.assertEqual(self.calls, [])
        self.focus_approve(); self.state.focus = True
        self.event('RET')
        self.assertEqual(self.calls, [])

    def test_focus_has_visible_border_and_shift_tab_is_reversible(self):
        self.event('TAB'); self.paint()
        buttons = [args for args in self.rectangles if args[-2] == 'button']
        self.assertEqual(buttons[-1][-1], 'focus', 'Reject focus must actually be painted')
        self.event('TAB', shift=True); self.paint()
        self.assertEqual(self.pending.get('keyboard_focus'), 'approve')
        self.event('TAB', shift=True); self.paint()
        self.assertEqual(self.pending.get('keyboard_focus'), 'reject')
        self.event('RET')
        self.assertEqual(self.calls, [(False, self.pending)])

    def test_pending_review_wheel_does_not_scroll_hidden_chat(self):
        self.ai['chat_offset'] = 123
        self.assertTrue(self.event('WHEELUPMOUSE'))
        self.assertEqual(self.ai['chat_offset'], 123)
        self.assertEqual(self.calls, [])

    def test_no_review_or_closed_panel_keeps_keyboard_route_unclaimed(self):
        self.ai.pop('action_pending')
        self.assertFalse(self.event('TAB'))
        self.ai['action_pending'] = self.pending
        self.g['ai_panel_open'] = lambda: False
        self.assertFalse(self.event('ESC'))
        self.assertEqual(self.calls, [])


def mutations():
    global MUTATION
    variants = [
        ('painted_owner', "drawn is pending and view.get('fits')", "True and view.get('fits')", 'test_stale_painted_review_cannot_receive_keyboard_focus'),
        ('painted_focus', "view.get('drawn_focus') == focus", 'True', 'test_focus_must_be_painted_before_return'),
        ('repeat', "event.value != 'PRESS' or getattr(event, 'is_repeat', False)", 'False', 'test_repeated_return_and_new_sibling_need_fresh_tab'),
        ('one_gesture', "pending.pop('keyboard_focus', None)  # One fresh focus gesture per decision.", 'pass', 'test_repeated_return_and_new_sibling_need_fresh_tab'),
        ('focus_border', "design.ACCENT if focused else design.BORDER_STRONG", 'design.BORDER_STRONG', 'test_focus_has_visible_border_and_shift_tab_is_reversible'),
    ]
    for name, old, new, case in variants:
        MUTATION = (old, new)
        try:
            result = unittest.TestResult(); Reachability(case).run(result)
            if len(result.failures) != 1 or result.errors:
                raise AssertionError((name, result.failures, result.errors))
            print('KILLED', name, flush=True)
        finally:
            MUTATION = None
    restored = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Reachability))
    return restored.wasSuccessful()


if __name__ == '__main__':
    if '--mutations' in sys.argv:
        raise SystemExit(not mutations())
    unittest.main(verbosity=2)
