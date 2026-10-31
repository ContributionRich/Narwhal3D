"""Pure popup regression tests: python3 spike/test_shell_popup.py [--mutations].

Compile source directly so mutation probes cannot pick up stale .pyc files.
No Blender, GPU, package __init__, or display server is needed.
"""
import io
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch

import os
SOURCE = Path(os.environ.get('UI_CORE_POPUP_SOURCE', Path(__file__).resolve().parents[1])) / 'narwhal3d' / 'shell_popup.py'


def load(source=None):
    name = '_shell_popup_test_package'
    package = sys.modules.get(name)
    if package is None:
        package = ModuleType(name)
        package.__path__ = [str(SOURCE.parent)]
        sys.modules[name] = package
        # Keep ctypes/module identities stable across per-test sys.modules stubs.
        importlib.import_module(name + '.native_popup')
    module = ModuleType(package.__name__ + '.shell_popup')
    module.__package__ = package.__name__
    sys.modules[module.__name__] = module
    exec(compile(SOURCE.read_text() if source is None else source, str(SOURCE), 'exec'),
         module.__dict__)
    return module


popup = load()


def event(key='LEFTMOUSE', value='PRESS', point=(0, 0)):
    return NS(type=key, value=value, mouse_x=point[0], mouse_y=point[1],
              mouse_region_x=-9999, mouse_region_y=-9999)


def center(rect):
    return ((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)


def row_point(model, index=0, depth=0):
    panel = model.panels[depth]
    box = popup._intersection(panel.row_rects[index], panel.body)
    assert box is not None, 'test must click a visible row'
    return center(box)


class PopupTests(unittest.TestCase):
    def setUp(self):
        # Teardown checks bpy.types.Window even when no draw handlers were installed.
        bpy_stub = patch.dict(sys.modules, {'bpy': NS(types=NS())})
        bpy_stub.start()
        self.addCleanup(bpy_stub.stop)
        self.calls = []
        self.rows = [{'label': 'Wireframe', 'action': 'Wireframe', 'checked': True},
                     {'separator': True},
                     {'label': 'Unavailable', 'enabled': False, 'action': 'NeverRun'},
                     {'label': 'Top', 'action': 'ACTION_TOP'}]
        self.model = popup._make_model((1000, 800)).open(
            self.rows, (120, 650), 'Perspective', lambda *args: self.calls.append(args))

    def click(self, index=0, depth=0):
        point = row_point(self.model, index, depth)
        self.assertTrue(self.model.handle_event(event(point=point)))
        self.assertTrue(self.model.handle_event(event(value='RELEASE', point=point)))

    def tree(self, anchor=(120, 650)):
        rows = [{'label': 'Set View', 'children': [
            {'label': 'Nested', 'children': [{'label': 'Top', 'action': 'top'}]},
            {'label': 'Bottom', 'action': 'bottom'}]},
            {'label': 'Other', 'action': 'other'}]
        self.model.open(rows, anchor, 'Perspective', lambda *a: self.calls.append(a))

    def test_anchor_and_region_translation(self):
        box = self.model.panels[0].rect
        self.assertEqual(box[0], 120)
        self.assertEqual(box[3], 647)
        self.assertEqual(popup.region_rect(box, (100, 200)),
                         (20, box[1] - 200, box[2] - 100, 447))
        self.assertEqual(self.model.hit_test(*row_point(self.model)), (0, 0))
        self.assertEqual(box, self.model.panels[0].rect)

    def test_metrics_measurement_and_scale(self):
        measured = []
        def measure(text, size):
            measured.append((text, size))
            return len(text) * 15
        model = popup._make_model((1800, 1200), scale=2, measure=measure)
        model.open([{'label': 'Long label' * 4, 'shortcut': 'Ctrl+Shift+F', 'action': 1}],
                   (150, 1050), '', lambda *a: None)
        panel = model.panels[0]
        self.assertEqual(panel.rect[3], 1044)
        self.assertGreater(panel.width, 400)
        self.assertIn(('Ctrl+Shift+F', 24), measured)
        self.assertEqual(panel.row_rects[0][3] - panel.row_rects[0][1], 44)
        self.assertEqual(popup.FONT_SIZE, 12)
        self.assertEqual(popup.ROW_HEIGHT, 22)

    def test_clamp_and_resize_including_tiny_windows(self):
        for size in [(1000, 800), (160, 120), (10, 10), (1, 1)]:
            for anchor in [(-100, -500), (5000, 6000)]:
                self.model.resize(size)
                self.model.open(self.rows, anchor, 'Title', lambda *a: None)
                x0, y0, x1, y1 = self.model.panels[0].rect
                self.assertTrue(0 <= x0 < x1 <= size[0])
                self.assertTrue(0 <= y0 < y1 <= size[1])
                self.assertLessEqual(y1 - y0, size[1] * .8 + 1e-8)
        self.model.resize((1000, 800))
        self.model.open(self.rows, (120, 650), '', lambda *a: None)
        self.assertEqual(self.model.panels[0].rect[3], 647)

    def test_rows_copied_but_opaque_actions_preserved(self):
        action = object()
        rows = [{'label': 'Parent', 'children': [{'label': 'Child', 'action': action}]}]
        self.model.open(rows, (120, 650), '', lambda *a: self.calls.append(a))
        rows[0]['children'][0]['label'] = 'Changed'
        child = self.model.panels[0].rows[0]['children'][0]
        self.assertEqual(child['label'], 'Child')
        self.assertIs(child['action'], action)
        self.click()
        self.click(0, 1)
        self.assertIs(self.calls[0][0], action)

    def test_release_alone_does_not_execute(self):
        self.assertTrue(self.model.handle_event(event(value='RELEASE', point=row_point(self.model))))
        self.assertEqual(self.calls, [])
        self.assertTrue(self.model.is_open)
        self.click()
        self.assertEqual(self.calls, [('Wireframe', None)])
        self.assertFalse(self.model.is_open)

    def test_press_release_must_match_row(self):
        self.model.handle_event(event(point=row_point(self.model)))
        self.model.handle_event(event(value='RELEASE', point=row_point(self.model, 3)))
        self.assertEqual(self.calls, [])
        self.assertTrue(self.model.is_open)

    def test_separator_checked_and_unavailable_visible(self):
        self.assertEqual(len(self.model.panels[0].rows), 4)
        self.assertTrue(self.model.panels[0].rows[0]['checked'])
        self.click(1)
        self.assertEqual(self.calls, [])
        self.assertTrue(self.model.is_open)
        self.click(2)
        self.assertEqual(self.calls, [(None, 'Unavailable: not implemented yet')])

    def test_missing_action_and_explicit_explanation(self):
        for row, explanation in [({'label': 'Missing'}, 'Missing: not implemented yet'),
                                 ({'label': 'Pending', 'enabled': False, 'action': 1,
                                   'explanation': 'Requires a named view'}, 'Requires a named view')]:
            self.model.open([row], (120, 650), '', lambda *a: self.calls.append(a))
            self.click()
            self.assertEqual(self.calls[-1], (None, explanation))

    def test_every_mouse_button_outside_press_and_release_guard(self):
        for key in ['LEFTMOUSE', 'MIDDLEMOUSE', 'RIGHTMOUSE', 'BUTTON4MOUSE',
                    'BUTTON5MOUSE', 'BUTTON6MOUSE', 'BUTTON7MOUSE', 'PEN', 'ERASER']:
            with self.subTest(key=key):
                self.model.open(self.rows, (120, 650), '', lambda *a: self.calls.append(a))
                self.assertTrue(self.model.handle_event(event(key, point=(950, 50))))
                self.assertFalse(self.model.is_open)
                self.assertTrue(self.model.handle_event(event(key, 'RELEASE', (950, 50))))
                self.assertFalse(self.model.handle_event(event(key, point=(950, 50))))
        self.assertEqual(self.calls, [])

    def test_inside_buttons_never_reach_picker(self):
        for key in ['RIGHTMOUSE', 'MIDDLEMOUSE', 'BUTTON4MOUSE', 'PEN', 'ERASER']:
            point = row_point(self.model)
            self.assertTrue(self.model.handle_event(event(key, point=point)))
            self.assertTrue(self.model.handle_event(event(key, 'RELEASE', point)))
            self.assertTrue(self.model.is_open)
        self.assertEqual(self.calls, [])

    def test_hover_click_submenu_adjacency_and_left_flip(self):
        for anchor, flip in [((120, 650), False), ((790, 650), True)]:
            self.tree(anchor)
            self.model.handle_event(event('MOUSEMOVE', 'NOTHING', row_point(self.model)))
            self.assertEqual(len(self.model.panels), 2)
            parent, child = self.model.panels
            self.assertEqual(child.rect[2] if flip else child.rect[0],
                             parent.rect[0] if flip else parent.rect[2])
            self.assertLessEqual(abs(child.rect[3] - parent.row_rects[0][3]), 4)
            self.click(0, 1)
            self.assertEqual(len(self.model.panels), 3)
            self.click(0, 2)
            self.assertEqual(self.calls[-1], ('top', None))
        self.tree()
        self.click()
        self.assertEqual(len(self.model.panels), 2)
        self.model.handle_event(event('MOUSEMOVE', 'NOTHING', row_point(self.model, 1)))
        self.assertEqual(len(self.model.panels), 1)

    def test_disabled_parent_still_browsable(self):
        self.model.open([{'label': 'Views', 'enabled': False,
                          'children': [{'label': 'Top', 'action': 'top'}]}],
                        (120, 650), '', lambda *a: self.calls.append(a))
        self.click()
        self.assertEqual(len(self.model.panels), 2)
        self.assertEqual(self.calls, [])
        self.click(0, 1)
        self.assertEqual(self.calls, [('top', None)])

    def test_scroll_height_limit_and_last_row_accessible(self):
        rows = [{'label': str(i), 'action': i} for i in range(100)]
        self.model.open(rows, (120, 650), '', lambda *a: self.calls.append(a))
        panel = self.model.panels[0]
        self.assertEqual(panel.rect[3] - panel.rect[1], 640)
        self.assertGreater(panel.max_scroll, 0)
        self.assertEqual(self.model.hit_test(*center(panel.down)), (0, 'down'))
        self.model.handle_event(event(point=center(panel.down)))
        self.model.handle_event(event(value='RELEASE', point=center(panel.down)))
        self.assertEqual(panel.offset, 22)
        for _ in range(100):
            self.model.handle_event(event('WHEELDOWNMOUSE', point=center(panel.body)))
        self.assertEqual(panel.offset, panel.max_scroll)
        self.assertEqual(panel.row_rects[-1][1], panel.body[1])
        self.click(99)
        self.assertEqual(self.calls, [(99, None)])

    def test_keyboard_skips_separator_but_explains_unavailable(self):
        for key, selected in [('DOWN_ARROW', 0), ('DOWN_ARROW', 2), ('UP_ARROW', 0),
                              ('END', 3), ('HOME', 0), ('UP_ARROW', 3)]:
            self.assertTrue(self.model.handle_event(event(key)))
            self.assertEqual(self.model.panels[0].selected, selected)
            self.assertTrue(self.model.handle_event(event(key, 'RELEASE')))
        self.model.handle_event(event('UP_ARROW'))
        self.model.handle_event(event('RET'))
        self.assertEqual(self.calls, [(None, 'Unavailable: not implemented yet')])
        self.assertTrue(self.model.handle_event(event('RET', 'RELEASE')))

    def test_keyboard_submenu_navigation(self):
        self.tree()
        for key in ['HOME', 'RIGHT_ARROW', 'RIGHT_ARROW']:
            self.model.handle_event(event(key))
        self.assertEqual(len(self.model.panels), 3)
        self.assertEqual(self.model.panels[-1].selected, 0)
        self.model.handle_event(event('LEFT_ARROW'))
        self.assertEqual(len(self.model.panels), 2)
        self.model.handle_event(event('END'))
        self.model.handle_event(event('NUMPAD_ENTER'))
        self.assertEqual(self.calls, [('bottom', None)])

    def test_keyboard_scroll_selection_visible(self):
        self.model.open([{'label': str(i), 'action': i} for i in range(100)],
                        (120, 650), '', lambda *a: None)
        panel = self.model.panels[0]
        self.model.handle_event(event('END'))
        self.assertEqual(panel.selected, 99)
        self.assertEqual(panel.row_rects[-1][1], panel.body[1])
        self.model.handle_event(event('HOME'))
        self.assertEqual(panel.offset, 0)
        self.model.handle_event(event('PAGE_DOWN'))
        self.assertGreater(panel.offset, 0)
        self.model.handle_event(event('PAGE_UP'))
        self.assertEqual(panel.offset, 0)

    def test_esc_and_closed_release_shield(self):
        self.model.handle_event(event('LEFTMOUSE', point=row_point(self.model)))
        self.assertTrue(self.model.handle_event(event('ESC')))
        self.assertFalse(self.model.is_open)
        self.assertTrue(self.model.handle_event(event('ESC', 'RELEASE')))
        self.assertTrue(self.model.handle_event(event('LEFTMOUSE', 'RELEASE')))
        self.assertFalse(self.model.handle_event(event('A')))
        self.assertEqual(self.calls, [])

    def test_keyboard_modal_and_housekeeping_passthrough(self):
        self.assertTrue(self.model.handle_event(event('X')))
        self.assertTrue(self.model.handle_event(event('X', 'RELEASE')))
        self.assertFalse(self.model.handle_event(event('TIMER', 'NOTHING')))
        self.assertTrue(self.model.handle_event(event('TRACKPADPAN', 'NOTHING')))
        self.assertFalse(self.model.handle_event(event('WINDOW_DEACTIVATE', 'NOTHING')))
        self.assertFalse(self.model.is_open)

    def test_callback_can_reopen_or_raise(self):
        def reopen(*args):
            self.assertFalse(self.model.is_open)
            self.model.open([{'label': 'Next', 'action': 'next'}], (120, 650), '',
                            lambda *a: self.calls.append(a))
        self.model.callback = reopen
        self.click()
        self.assertTrue(self.model.is_open)
        self.click()
        self.assertEqual(self.calls, [('next', None)])
        def fail(*args):
            raise RuntimeError('callback failed')
        self.model.open(self.rows, (120, 650), '', fail)
        with self.assertRaisesRegex(RuntimeError, 'callback failed'):
            self.click()
        self.assertFalse(self.model.is_open)

    def test_public_api_preserves_release_on_reopen(self):
        try:
            popup.open(self.rows, (120, 650), '', lambda *a: None, window_size=(1000, 800))
            self.assertTrue(popup.handle_event(event('RIGHTMOUSE')))
            self.assertFalse(popup.is_open())
            popup.open(self.rows, (120, 650), '', lambda *a: None, window_size=(1000, 800))
            popup.close()
            self.assertTrue(popup.handle_event(event('RIGHTMOUSE', 'RELEASE')))
            self.assertFalse(popup.handle_event(event('RIGHTMOUSE')))
        finally:
            popup.unregister()

    def test_owner_window_isolation(self):
        owner = NS(width=1000, height=800, screen=NS(areas=[]))
        other = NS(width=900, height=700)
        try:
            popup._model, popup._window = self.model, owner
            self.assertFalse(popup.handle_event(event('ESC'), NS(window=other)))
            self.assertTrue(popup.is_open())
            self.assertTrue(popup.handle_event(event('ESC'), NS(window=owner)))
            self.assertFalse(popup.is_open())
        finally:
            popup.unregister()

    def test_registration_is_idempotent_and_cleans_partial_failure(self):
        added, removed = [], []
        class View:
            @staticmethod
            def draw_handler_add(*args):
                added.append(('view', args))
                return 'v'
            @staticmethod
            def draw_handler_remove(*args):
                removed.append(('view', args))
        class Info:
            @staticmethod
            def draw_handler_add(*args):
                added.append(('info', args))
                return 'i'
            @staticmethod
            def draw_handler_remove(*args):
                removed.append(('info', args))
        bpy = NS(types=NS(SpaceView3D=View, SpaceInfo=Info))
        with patch.dict(sys.modules, {'bpy': bpy}):
            popup.register()
            popup.register()
            self.assertEqual(len(added), 2)
            for name, args in added:
                self.assertEqual(args[1:], ((), 'WINDOW', 'POST_PIXEL'))
            popup.unregister()
            popup.unregister()
            self.assertEqual(removed, [('view', ('v', 'WINDOW')), ('info', ('i', 'WINDOW'))])
            with patch.object(Info, 'draw_handler_add', side_effect=RuntimeError('register')):
                with self.assertRaisesRegex(RuntimeError, 'register'):
                    popup.register()
            self.assertEqual(popup._handlers, [])
            self.assertIsNone(popup._model)
            self.assertEqual(removed[-1], ('view', ('v', 'WINDOW')))

    def test_draw_adapter_translates_clips_and_restores_state(self):
        rounded, texts, clips, blends, clipping = [], [], [], [], []
        design = NS(CHIP_RADIUS=4, rgb01=lambda value: value)
        for name in ['MENU_BG', 'BORDER_STRONG', 'BORDER', 'TEXT_MUTED',
                     'ACCENT', 'TEXT_DISABLED', 'BAR_TEXT_STRONG', 'TEXT']:
            setattr(design, name, name)
        shader = NS(bind=lambda: None)
        ui = NS(_DEFAULT_FONT_ID=0, _uniform_color_shader=lambda: shader,
                _draw_round_rect=lambda *a, **kw: rounded.append((a, kw)),
                _draw_rect=lambda *a: None,
                _draw_text=lambda *a: texts.append(a),
                _text_width=lambda text, size: len(text) * size * .6)
        package = ModuleType('_popup_draw_stubs')
        package.ui, package.design = ui, design
        blf = NS(CLIPPING=1, enable=lambda *a: clipping.append(True),
                 disable=lambda *a: clipping.append(False),
                 clipping=lambda *a: clips.append(a))
        gpu = NS(state=NS(blend_get=lambda: 'NONE', blend_set=blends.append))
        owner = NS(width=1000, height=800, screen=NS(areas=[]))
        region = NS(type='WINDOW', x=200, y=500, width=200, height=200)
        context = NS(window=owner, region=region)
        self.model.panels[0].selected = 0
        box = self.model.panels[0].rect
        try:
            popup._model, popup._window = self.model, owner
            with patch.object(popup, '__package__', package.__name__), patch.dict(
                    sys.modules, {package.__name__: package, 'blf': blf, 'gpu': gpu}):
                popup.draw(context)
                args, kwargs = rounded[0]
                self.assertEqual(args[1:5], (box[0] - 200, box[1] - 500,
                                             box[2] - 200, box[3] - 500))
                self.assertEqual(args[5:], (4, 'MENU_BG'))
                self.assertEqual(kwargs, {'border': 'BORDER_STRONG', 'border_width': 1})
                wire = next(t for t in texts if t[2] == 'Wireframe')
                self.assertEqual(wire[0], 120 + 28 - 200)
                self.assertEqual(wire[3:], (12, 'BAR_TEXT_STRONG'))
                unavailable = next(t for t in texts if t[2] == 'Unavailable')
                self.assertEqual(unavailable[4], 'TEXT_DISABLED')
                self.assertIn('✓', [t[2] for t in texts])
                for font, x0, y0, x1, y1 in clips:
                    self.assertTrue(0 <= x0 < x1 <= region.width)
                    self.assertTrue(0 <= y0 < y1 <= region.height)
                self.assertEqual(blends, ['ALPHA', 'NONE'])
                self.assertEqual(clipping.count(True), clipping.count(False))
                self.assertFalse(clipping[-1])
                self.assertEqual(self.model.panels[0].rect, box)
                with patch.object(ui, '_draw_text', side_effect=RuntimeError('paint failed')):
                    with self.assertRaisesRegex(RuntimeError, 'paint failed'):
                        popup.draw(context)
                self.assertEqual(blends[-1], 'NONE')
                self.assertFalse(clipping[-1])
                rounded.clear()
                popup.draw(NS(window=owner, region=NS(type='HEADER')))
                popup.draw(NS(window=NS(width=900), region=region))
                self.assertEqual(rounded, [])
        finally:
            popup.unregister()

    def test_invalid_inputs(self):
        for size in [(0, 800), (1000, -1), (float('inf'), 500)]:
            with self.assertRaises(ValueError):
                popup._make_model(size)
        for scale in [0, -1, float('nan')]:
            with self.assertRaises(ValueError):
                popup._make_model((1000, 800), scale)
        with self.assertRaises(TypeError):
            self.model.open([], (0, 0), '', None)
        with self.assertRaises(ValueError):
            self.model.open([], (float('nan'), 0), '', lambda *a: None)


def mutations():
    """Run real behavioral regressions against broken in-memory source, not mocks."""
    global popup
    original = popup
    source = SOURCE.read_text()
    probes = [
        # Core event/layout mutants moved to test_ui_core_popup_mutations.py.
        # This harness only mutates the remaining Python draw integration.
        ('region translation', 'return rect[0] - x, rect[1] - y, rect[2] - x, rect[3] - y',
         'return rect', 'test_anchor_and_region_translation'),

        ('draw coordinate translation', 'x - origin[0], y - origin[1], str(label)',
         'x, y, str(label)', 'test_draw_adapter_translates_clips_and_restores_state'),
        ('GPU state restoration', 'gpu.state.blend_set(previous)',
         "gpu.state.blend_set('ALPHA')", 'test_draw_adapter_translates_clips_and_restores_state'),
    ]
    try:
        for name, old, new, test in probes:
            assert source.count(old) == 1, f'non-unique mutation: {name}'
            popup = load(source.replace(old, new, 1))
            output = io.StringIO()
            result = unittest.TextTestRunner(stream=output).run(PopupTests(test))
            if result.wasSuccessful() or result.errors:
                raise AssertionError(f'{name}: expected assertion failure\n{output.getvalue()}')
            print(f'RED verified: {name} ({len(result.failures)} assertion failure(s))')
    finally:
        popup = original
        sys.modules[original.__name__] = original
    result = unittest.TextTestRunner(verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(PopupTests))
    return result.wasSuccessful()


if __name__ == '__main__':
    if '--mutations' in sys.argv:
        raise SystemExit(0 if mutations() else 1)
    unittest.main(verbosity=2)
