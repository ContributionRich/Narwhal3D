"""Runtime popup failure boundary: real native owner, no bpy required.

NARWHAL_UI_CORE_LIBRARY=... python3 -B spike/test_ui_core_popup_failure.py
Source compiled directly, including for disposable-source mutation controls.
"""
import io
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

import test_ui_core_popup as fixture

popup, native, event, point = fixture.popup, fixture.native, fixture.event, fixture.point


class RuntimeFailureTests(unittest.TestCase):
    def setUp(self):
        self.models = []
        self.messages = []
        self.window = NS(width=1000, height=800, screen=NS(areas=[]))
        self.context = NS(window=self.window)
        fixture.pkg.ui = NS(_text_width=lambda t, s: len(t) * s,
                            app_state=NS(log=self.messages.append))
        fixture.pkg.uiscale = NS(current=lambda: 1)
        self.registration = patch.object(popup, 'register')
        self.registration.start()
        popup._model = popup._window = None
        if hasattr(popup, '_failure_releases'):
            popup._failure_releases.clear()

    def tearDown(self):
        for model in self.models:
            model.dispose()
        popup._model = popup._window = None
        if hasattr(popup, '_failure_releases'):
            popup._failure_releases.clear()
        self.registration.stop()

    def open(self, callback=None, rows=None, context=None):
        m = popup.open(rows if rows is not None else [
            {'label': 'parent', 'children': [{'label': 'bad metric', 'action': 1}]}],
            (100, 600), 'Failure', callback or (lambda *a: None),
            context=context or self.context)
        self.models.append(m)
        return m

    def send(self, key, value='PRESS', pos=(0, 0), context=None):
        try:
            return popup.handle_event(event(key, value, pos), context or self.context)
        except Exception as exc:
            self.fail('exception escaped command modal: ' + repr(exc))

    def break_metric(self, m):
        calls = []
        def measure(text, size):
            calls.append(text)
            if text == 'bad metric':
                raise LookupError('injected lazy metric failure')
            return 30
        m.measure = measure
        return calls

    def assert_detached(self, m):
        self.assertIsNone(popup._model)
        self.assertFalse(m.is_open)
        self.assertEqual(m.lib.narwhal_popup_destroy(m.handle), 2)
        self.assertEqual(len(self.messages), 1)
        self.assertIn('not replayed', self.messages[0])

    def test_lazy_keyboard_failure_reports_detaches_and_shields_pending_keys(self):
        m = self.open()
        calls = self.break_metric(m)
        self.assertTrue(self.send('HOME'))  # Deliberately leave its release pending.
        self.assertTrue(self.send('RIGHT_ARROW'))
        self.assert_detached(m)
        self.assertIn('injected lazy metric failure', self.messages[0])
        self.assertTrue(self.send('HOME', 'RELEASE'))
        self.assertTrue(self.send('RIGHT_ARROW', 'RELEASE'))
        self.assertFalse(self.send('RIGHT_ARROW', 'RELEASE'))
        self.assertFalse(self.send('B'))
        self.assertEqual(calls.count('bad metric'), 1)

    def test_lazy_mouse_failure_shields_press_and_prior_mouse_key(self):
        m = self.open()
        pos = point(m)
        self.assertTrue(self.send('BUTTON7MOUSE', pos=pos))
        self.break_metric(m)
        self.assertTrue(self.send('LEFTMOUSE', pos=pos))
        self.assert_detached(m)
        for key in ('BUTTON7MOUSE', 'LEFTMOUSE'):
            self.assertTrue(self.send(key, 'RELEASE', pos))
            self.assertFalse(self.send(key, 'RELEASE', pos))

    def test_hover_failure_does_not_invent_guard(self):
        m = self.open()
        self.break_metric(m)
        self.assertTrue(self.send('MOUSEMOVE', 'NOTHING', point(m)))
        self.assert_detached(m)
        self.assertFalse(self.send('LEFTMOUSE', 'RELEASE'))

    def test_callback_replacement_then_raise_is_preserved_exactly_once(self):
        calls = []
        def callback(*args):
            calls.append(args)
            self.open(rows=[{'label': 'replacement', 'action': 2}])
            raise LookupError('callback after replacement')
        m = self.open(callback, [{'label': 'activate', 'action': 1}])
        self.send('HOME')
        self.assertTrue(self.send('RET'))
        replacement = self.models[-1]
        self.assertIs(popup._model, replacement)
        self.assertTrue(replacement.is_open)
        self.assertFalse(m.is_open)
        self.assertEqual(calls, [(1, None)])
        self.assertEqual(len(self.messages), 1)
        popup.close()
        self.assertTrue(self.send('HOME', 'RELEASE'))
        self.assertTrue(self.send('RET', 'RELEASE'))
        self.assertFalse(self.send('B'))

    def test_callback_raise_without_replacement_keeps_release_shield(self):
        def callback(*args):
            raise LookupError('callback failed')
        m = self.open(callback, [{'label': 'activate', 'action': 1}])
        self.send('HOME')
        self.assertTrue(self.send('RET'))
        self.assert_detached(m)
        self.assertTrue(self.send('RET', 'RELEASE'))
        self.assertFalse(self.send('B'))

    def test_resize_failure_is_inside_event_boundary(self):
        m = self.open()
        self.send('HOME')
        with patch.object(m, 'resize', side_effect=OSError('resize failed')):
            self.assertTrue(self.send('LEFTMOUSE'))
        self.assert_detached(m)
        self.assertTrue(self.send('HOME', 'RELEASE'))
        self.assertTrue(self.send('LEFTMOUSE', 'RELEASE'))

    def test_draw_failure_preserves_native_guards_before_disposal(self):
        m = self.open(rows=[{'label': 'leaf', 'action': 1}])
        # Include direct native input: the shell must read Rust's guards, not
        # maintain a second normal-operation keyboard/mouse state machine.
        m.handle_event(event('HOME'))
        m.handle_event(event('LEFTMOUSE', 'PRESS', point(m)))
        self.context.region = NS(type='WINDOW', width=1000, height=800)
        with patch.dict(sys.modules, {'blf': NS(), 'gpu': NS(), 'bpy': NS(context=self.context)}), \
                patch.object(m, 'resize', side_effect=OSError('draw resize failed')):
            try:
                popup._draw_current()
            except Exception as exc:
                self.fail('exception escaped draw handler: ' + repr(exc))
        self.assert_detached(m)
        self.assertTrue(self.send('HOME', 'RELEASE'))
        self.assertTrue(self.send('LEFTMOUSE', 'RELEASE'))
        self.assertFalse(self.send('B'))

    def test_window_draw_failure_and_callback_replacement(self):
        m = self.open()
        m.handle_event(event('HOME'))
        with patch.dict(sys.modules, {'bpy': NS(context=self.context)}), \
                patch.object(popup, 'draw', side_effect=OSError('window paint failed')):
            try:
                popup._draw_window()
            except Exception as exc:
                self.fail('exception escaped window draw handler: ' + repr(exc))
        self.assert_detached(m)
        self.assertTrue(self.send('HOME', 'RELEASE'))
        self.messages.clear()
        m = self.open()
        def draw(*args):
            self.open(rows=[{'label': 'replacement', 'action': 1}])
            raise OSError('paint callback replaced popup')
        with patch.dict(sys.modules, {'bpy': NS(context=self.context)}), \
                patch.object(popup, 'draw', side_effect=draw):
            popup._draw_window()
        self.assertIs(popup._model, self.models[-1])
        self.assertTrue(popup.is_open())
        self.assertFalse(m.is_open)
        self.assertEqual(len(self.messages), 1)

    def test_close_failure_keeps_guards(self):
        m = self.open()
        m.handle_event(event('HOME'))
        with patch.object(m, 'close', side_effect=OSError('dismiss failed')):
            popup.close()
        self.assert_detached(m)
        self.assertTrue(self.send('HOME', 'RELEASE'))
        self.assertFalse(self.send('B'))

    def test_failed_release_is_not_guarded_a_second_time(self):
        m = self.open(rows=[{'label': 'leaf', 'action': 1}])
        pos = point(m)
        self.send('LEFTMOUSE', pos=pos)
        with patch.object(m, 'resize', side_effect=OSError('release resize failed')):
            self.assertTrue(self.send('LEFTMOUSE', 'RELEASE', pos))
        self.assert_detached(m)
        self.assertFalse(self.send('LEFTMOUSE', 'RELEASE', pos))

    def test_native_failure_after_mutation_cannot_reach_picker_or_replay(self):
        m = self.open()
        self.send('HOME')
        original = m.lib.narwhal_popup_apply
        calls = []
        def fail(*args):
            calls.append(1)
            original(*args)
            raise OSError('transport failed after mutation')
        with patch.object(m.lib, 'narwhal_popup_apply', side_effect=fail):
            self.assertTrue(self.send('A'))
        self.assert_detached(m)
        self.assertEqual(len(calls), 1)
        self.assertTrue(self.send('HOME', 'RELEASE'))
        self.assertTrue(self.send('A', 'RELEASE'))
        self.assertFalse(self.send('B'))

    def test_failed_guards_survive_same_window_reopen_not_other_window(self):
        m = self.open()
        self.break_metric(m)
        self.send('HOME')
        self.send('RIGHT_ARROW')
        self.open(rows=[])
        popup.close()
        other = NS(window=NS(width=1000, height=800, screen=NS(areas=[]), other=True))
        self.assertFalse(self.send('HOME', 'RELEASE', context=other))
        self.assertTrue(self.send('HOME', 'RELEASE'))
        self.open(rows=[], context=other)
        popup.close()
        self.assertFalse(self.send('RIGHT_ARROW', 'RELEASE', context=other))

    def test_report_failure_cannot_disable_input(self):
        m = self.open()
        self.break_metric(m)
        fixture.pkg.ui.app_state.log = lambda *_: (_ for _ in ()).throw(OSError('log failed'))
        with patch('sys.stderr', new_callable=io.StringIO) as stderr:
            self.send('HOME')
            self.assertTrue(self.send('RIGHT_ARROW'))
        self.assertIn('injected lazy metric failure', stderr.getvalue())
        self.assertIsNone(popup._model)
        self.assertFalse(self.send('B'))


class TreeBoundsTests(unittest.TestCase):
    def test_bound_checked_before_copy_and_consumption_is_bounded(self):
        for nested in (False, True):
            copied, pulled = [], []
            class Row:
                def keys(self):
                    copied.append(1)
                    return ['label']
                def __getitem__(self, key):
                    return 'leaf'
            def rows():
                for i in range(native.MAX_ROWS + 2):
                    pulled.append(i)
                    if i == native.MAX_ROWS + 1:
                        raise AssertionError('unbounded iterable consumption')
                    yield Row()
            source = [{'label': 'parent', 'children': rows()}] if nested else rows()
            with self.assertRaisesRegex(ValueError, '65536'):
                native._tree(source)
            budget = native.MAX_ROWS - int(nested)
            self.assertEqual(len(copied), budget)
            self.assertEqual(len(pulled), budget + 1)


if __name__ == '__main__':
    unittest.main()
