"""Pure overlap-menu tests: python3 -B spike/test_selection_menu.py [--mutations].

Compile current source, never package __init__ or bytecode. Exercise the real
shell PopupModel/public routing; no Blender, drawing, timer or UI integration.
"""
import importlib
import io
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / 'narwhal3d'
SOURCES = {name: (ROOT / (name + '.py')).read_text()
           for name in ('shell_popup', 'selection_menu')}
OVERRIDES = {}


def load(name, package):
    module = ModuleType(package.__name__ + '.' + name)
    module.__package__ = package.__name__
    sys.modules[module.__name__] = module
    setattr(package, name, module)
    exec(compile(OVERRIDES.get(name, SOURCES[name]), str(ROOT / (name + '.py')), 'exec'),
         module.__dict__)
    return module


def event(key='LEFTMOUSE', value='PRESS', point=(0, 0)):
    return NS(type=key, value=value, mouse_x=point[0], mouse_y=point[1],
              mouse_region_x=-9999, mouse_region_y=-9999)


class SelectionMenuTests(unittest.TestCase):
    native_backend = False

    def setUp(self):
        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)
        package = ModuleType('_selection_menu_tests')
        package.__path__ = [str(ROOT)]
        sys.modules[package.__name__] = package
        self.shell = load('shell_popup', package)
        self.menu = load('selection_menu', package)
        adapter = importlib.import_module(package.__name__ + '.native_popup')
        if self.native_backend:
            if adapter.library() is None:
                self.skipTest('native UI core unavailable; legacy controls still required')
        else:
            # These source mutants target the complete Python compatibility model.
            # Keep that control explicit even when the native core is installed.
            fallback = patch.object(adapter, 'library', return_value=None)
            fallback.start()
            self.addCleanup(fallback.stop)
        self.calls = []
        self.valid = True
        self.candidates = [NS(key=('Curve', 'Object', 0), label='Curve — Curve'),
                           NS(key=('Box', 'Face', 2), label='Box — Face 3'),
                           NS(key=('Box', 'Edge', 7), label='Box — Edge 8')]
        self.open()

    def open(self):
        return self.menu.open(self.candidates, (120, 650), self.calls.append,
                              lambda: self.valid, window_size=(1000, 800))

    def point(self, index):
        rect = self.shell._model.panels[0].row_rects[index]
        return ((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)

    def send(self, key='LEFTMOUSE', value='PRESS', index=0):
        return self.menu.handle_event(event(key, value, self.point(index)))

    def test_hover_highlights_candidate_without_selecting(self):
        self.assertIsNone(self.menu.highlighted())
        self.assertEqual([row['label'] for row in self.shell._model.panels[0].rows],
                         [c.label for c in self.candidates])
        for index in (0, 2, 1):
            self.assertTrue(self.send('MOUSEMOVE', 'NOTHING', index))
            self.assertIs(self.menu.highlighted(), self.candidates[index])
        self.assertEqual(self.calls, [])

    def test_keyboard_navigation_and_confirmation(self):
        for key, index in [('DOWN_ARROW', 0), ('DOWN_ARROW', 1), ('END', 2),
                           ('HOME', 0), ('UP_ARROW', 2)]:
            self.assertTrue(self.send(key))
            self.assertIs(self.menu.highlighted(), self.candidates[index])
            self.assertEqual(self.calls, [])
        self.assertTrue(self.send('RET'))
        self.assertEqual(self.calls, [self.candidates[2].key])
        self.assertIsNone(self.menu.current())
        self.assertFalse(self.shell.is_open())
        self.assertTrue(self.shell.handle_event(event('RET', 'RELEASE')))
        self.assertFalse(self.shell.handle_event(event('RET', 'RELEASE')))

    def test_callback_deferred_until_matching_release(self):
        # The opening viewport click's release cannot choose a popup row.
        self.assertTrue(self.send(value='RELEASE', index=1))
        self.assertEqual(self.calls, [])
        self.assertTrue(self.send(index=1))
        self.assertEqual(self.calls, [])
        self.assertTrue(self.send(value='RELEASE', index=2))
        self.assertEqual(self.calls, [])
        self.assertTrue(self.send(index=1))
        point = self.point(1)
        self.assertTrue(self.send(value='RELEASE', index=1))
        self.assertEqual(self.calls, [self.candidates[1].key])
        self.assertIsNone(self.menu.highlighted())
        self.menu.handle_event(event(value='RELEASE', point=point))
        self.assertEqual(self.calls, [self.candidates[1].key])

    def test_cancellation_is_noop_and_shell_owns_release_guard(self):
        for key in ('ESC', 'LEFTMOUSE', 'RIGHTMOUSE', 'MIDDLEMOUSE'):
            with self.subTest(key=key):
                self.open()
                self.assertTrue(self.menu.handle_event(event(key, point=(950, 30))))
                self.assertIsNone(self.menu.current())
                self.assertIsNone(self.menu.highlighted())
                release = event(key, 'RELEASE', (950, 30))
                self.assertFalse(self.menu.handle_event(release))
                self.assertTrue(self.shell.handle_event(release))
                self.assertFalse(self.shell.handle_event(release))
        self.assertEqual(self.calls, [])

    def test_invalidated_session_closes_without_callback(self):
        self.send('MOUSEMOVE', 'NOTHING', 1)
        self.valid = False
        self.assertIsNone(self.menu.highlighted())
        self.assertFalse(self.shell.is_open())
        self.assertFalse(self.menu.handle_event(event('RET')))
        self.assertEqual(self.calls, [])

    def test_stale_confirmation_rechecks_valid_callback(self):
        choose = self.shell._model.callback
        self.valid = False  # Changed after opening, before confirmation dispatch.
        choose(1, None)
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.menu.current())

    def test_replaced_shell_model_is_neither_highlighted_nor_closed(self):
        self.send('MOUSEMOVE', 'NOTHING', 1)
        other = self.shell.open([{'label': 'Other', 'action': 'other'}],
                                (120, 650), 'Other', lambda *a: None,
                                window_size=(1000, 800))
        self.assertIsNone(self.menu.current())
        self.assertIsNone(self.menu.highlighted())
        self.menu.close()
        self.assertIs(self.shell._model, other)
        self.assertTrue(other.is_open)
        self.assertEqual(self.calls, [])

    def test_close_does_not_dismiss_replacement_before_current_poll(self):
        other = self.shell.open([], (120, 650), 'Other', lambda *a: None,
                                window_size=(1000, 800))
        self.menu.close()
        self.assertTrue(other.is_open)
        self.assertEqual(self.calls, [])

    def test_too_few_candidates_never_open(self):
        for count in (0, 1):
            self.menu.close()
            self.assertFalse(self.menu.open(self.candidates[:count], (120, 650),
                             self.calls.append, lambda: True, window_size=(1000, 800)))
            self.assertIsNone(self.menu.current())
            self.assertFalse(self.shell.is_open())
        self.assertEqual(self.calls, [])

    def test_malformed_or_unavailable_confirmation_is_not_selection(self):
        for index, explanation in [(True, None), (-1, None), (3, None),
                                   ('1', None), (1, 'unavailable')]:
            self.open()
            self.shell._model.callback(index, explanation)
        self.assertEqual(self.calls, [])


class NativeSelectionMenuTests(SelectionMenuTests):
    native_backend = True


def mutations():
    probes = [
        ('selection_menu', "st['candidates'][index]", "st['candidates'][0]",
         'test_hover_highlights_candidate_without_selecting'),
        ('selection_menu', 'and valid() and', 'and True and',
         'test_stale_confirmation_rechecks_valid_callback'),
        ('selection_menu', "shell_popup._model is not st['menu']", 'False',
         'test_replaced_shell_model_is_neither_highlighted_nor_closed'),
        ('selection_menu', "and shell_popup._model is _session['menu']", '',
         'test_close_does_not_dismiss_replacement_before_current_poll'),
        ('shell_popup', 'if pressed == (key, hit) and hit is not None:',
         'if hit is not None:', 'test_callback_deferred_until_matching_release'),
        ('shell_popup', 'if not self.is_open:\n            return guarded',
         'if not self.is_open:\n            return False',
         'test_cancellation_is_noop_and_shell_owns_release_guard'),
    ]
    try:
        for module, old, new, test in probes:
            assert SOURCES[module].count(old) == 1, ('mutation anchor changed', old)
            OVERRIDES[module] = SOURCES[module].replace(old, new, 1)
            output = io.StringIO()
            result = unittest.TextTestRunner(stream=output).run(SelectionMenuTests(test))
            if result.wasSuccessful() or result.errors:
                raise AssertionError(f'{test}: expected assertion failure\n{output.getvalue()}')
            print('RED verified:', test)
            OVERRIDES.clear()
    finally:
        OVERRIDES.clear()


if __name__ == '__main__':
    if '--mutations' in sys.argv:
        mutations()
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls)
                               for cls in (SelectionMenuTests, NativeSelectionMenuTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
