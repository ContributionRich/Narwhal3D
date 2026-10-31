"""Native popup parity/ownership tests; no Blender or package initialization.

NARWHAL_UI_CORE_LIBRARY=... python3 -B spike/test_ui_core_popup.py
Reuses ALL unchanged test_shell_popup.PopupTests with the native constructor and
runtime factory; independently captured DATA checks state and metric call order.
"""
import ctypes as C
import gc
import importlib.util
import io
import math
import os
from pathlib import Path
from test_ui_core_golden import DATA
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('UI_CORE_POPUP_SOURCE', ROOT))
pkg = ModuleType('_popup_native_tests')
pkg.__path__ = [str(SOURCE / 'narwhal3d')]
sys.modules[pkg.__name__] = pkg


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    # Compile directly: mutation tests must never consult a stale bytecode cache.
    exec(compile(path.read_text(), str(path), 'exec'), mod.__dict__)
    return mod


nt = load(pkg.__name__ + '.native_textfield', SOURCE / 'narwhal3d/native_textfield.py')
native = load(pkg.__name__ + '.native_popup', SOURCE / 'narwhal3d/native_popup.py')
popup = load(pkg.__name__ + '.shell_popup', SOURCE / 'narwhal3d/shell_popup.py')
old = load('_popup_unchanged_suite', ROOT / 'spike/test_shell_popup.py')
old.popup = popup
PopupTests = old.PopupTests
event, point = old.event, old.row_point


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.models = []

    def tearDown(self):
        for model in self.models:
            model.dispose()
        popup._model = popup._window = None

    def make(self, rows=None, scale=.85, measure=None, callback=None):
        m = native.NativePopup((1600, 1000), scale, measure)
        self.models.append(m)
        return m.open(rows if rows is not None else [{'label': 'AV ffi', 'action': object()},
                      {'label': 'second', 'action': 1}], (352, 666), 'Choose object', callback or (lambda *a: None))

    def test_real_factory_native_and_small_traffic(self):
        rows = [{'label': str(i), 'action': i} for i in range(10000)]
        rows[-1]['children'] = [{'label': 'lazy child', 'action': 'child'}]
        m = popup.open(rows,
                       (120, 900), 'Traffic', lambda *a: None, window_size=(1600, 1000), scale=.85)
        self.models.append(m)
        self.assertIsInstance(m, native.NativePopup)
        handle, uploaded = m.handle, m.uploaded_rows
        p = m.panels[0]
        rects = p.row_rects
        downloaded = m.downloaded_rows
        x, y = old.center(p.row_rects[0])
        for _ in range(100):
            before = m.mutation_reply_bytes
            self.assertTrue(m.handle_event(event('MOUSEMOVE', 'NOTHING', (x, y))))
            self.assertEqual(m.mutation_reply_bytes - before, 48)
            self.assertEqual(m.panels[0].selected, 0)
            self.assertIs(m.panels[0].row_rects, rects)
            self.assertIs(m.panels[0].rows, p.rows)
        self.assertEqual((m.handle, m.uploaded_rows, m.downloaded_rows), (handle, uploaded, downloaded))
        self.assertEqual(uploaded, 10001 * 24)

    def test_fractional_gutters(self):
        m = self.make()
        p = m.panels[0]
        self.assertIsNone(p.up)
        self.assertIsNone(p.down)
        self.assertLess(p.max_scroll, 1e-6)
        for r in p.row_rects:
            visible = popup._intersection(r, p.body)
            self.assertIsNotNone(visible)
            for a, b in zip(r, visible):
                self.assertAlmostEqual(a, b)

    def compare_golden(self, model, expected):
        self.assertEqual(model.is_open, expected['is_open'])
        self.assertEqual(sorted(model._releases), expected['releases'])
        self.assertEqual(len(model.panels), len(expected['panels']))
        def plain(value):
            if hasattr(value, 'items'):
                return {k: plain(v) for k, v in value.items()}
            if isinstance(value, (tuple, list)):
                return [plain(v) for v in value]
            return value
        for p, q in zip(model.panels, expected['panels']):
            for name in ('title', 'parent_index', 'selected', 'rows'):
                self.assertEqual(plain(getattr(p, name)), q[name], name)
            for name in ('offset', 'max_scroll', 'width'):
                self.assertAlmostEqual(getattr(p, name), q[name], places=7, msg=name)
            for name in ('rect', 'body', 'up', 'down'):
                got = getattr(p, name)
                if q[name] is None:
                    self.assertIsNone(got)
                else:
                    self.assertEqual(len(got), len(q[name]))
                    for x, y in zip(got, q[name]):
                        self.assertAlmostEqual(x, y, places=7, msg=name)
            self.assertEqual(len(p.row_rects), len(q['row_rects']))
            for got, wanted in zip(p.row_rects, q['row_rects']):
                for x, y in zip(got, wanted):
                    self.assertAlmostEqual(x, y, places=7)

    def test_metrics_lazy_nonadditive_and_stateful(self):
        trace, log = DATA['popup_metrics'], []
        def measure(text, size):
            log.append([text, size])
            return {'AV': 201, 'ffi': 45, 'e\u0301': 17}.get(text, 0) + len(log)*.75
        m = native.NativePopup((1000, 800), .85, measure)
        self.models.append(m)
        m.open(trace['rows'], (790, 650), 'Title', lambda *a: None)
        self.compare_golden(m, trace['initial'])
        self.assertEqual(log, trace['calls'])
        self.assertNotIn(['e\u0301', 10.2], log)
        for step in trace['steps']:
            m.handle_event(event(step['key']))
            self.compare_golden(m, step['state'])
            self.assertEqual(log, step['calls'])
        self.assertEqual([x[0] for x in log].count('e\u0301'), 2)

    def test_golden_event_trace(self):
        trace, calls = DATA['popup'], []
        m = native.NativePopup((1000, 800), .85)
        self.models.append(m)
        self.assertEqual(len(trace['steps']), 900)
        for i, step in enumerate(trace['steps']):
            if 'open' in step:
                m.open(trace['rows'], step['open'], 'Root', lambda *args: calls.append(list(args)))
            if 'resize' in step:
                m.resize(step['resize'])
            key, value, xy = step['event']
            self.assertEqual(m.handle_event(event(key, value, xy)), step['consumed'], i)
            self.compare_golden(m, step['state'])
            self.assertEqual(calls, step['calls'])
            hit = m.hit_test(*xy)
            self.assertEqual(None if hit is None else list(hit), step['hit'])

    def test_failure_closes_without_callback_replay(self):
        log, calls = [], []
        def measure(text, size):
            log.append(text)
            if text == 'explode':
                raise LookupError('font failed')
            return 30
        m = self.make([{'label': 'parent', 'children': [{'label': 'explode', 'action': 1}]}],
                      measure=measure, callback=lambda *a: calls.append(a))
        h = m.handle
        m.handle_event(event('HOME'))
        with self.assertRaisesRegex(LookupError, 'font failed'):
            m.handle_event(event('RIGHT_ARROW'))
        self.assertFalse(m.is_open)
        self.assertEqual(m.panels, ())
        self.assertEqual(log, ['Choose object', 'parent', '', '', 'explode'])
        with self.assertRaisesRegex(RuntimeError, 'not replayed'):
            m.handle_event(event('RIGHT_ARROW'))
        self.assertEqual(log.count('explode'), 1)
        self.assertEqual(calls, [])
        self.assertEqual(m.lib.narwhal_popup_destroy(h), 2)

    def test_callback_closed_before_reopen_and_raise(self):
        calls = []
        m = self.make(callback=lambda *a: calls.append(a))
        handle = m.handle
        def reopen(*args):
            self.assertFalse(m.is_open)
            m.open([{'label': 'next', 'action': 'next'}], (0, 0), '', lambda *a: calls.append(a))
        m.callback = reopen
        m.handle_event(event('HOME')); m.handle_event(event('RET'))
        self.assertTrue(m.is_open)
        self.assertEqual(m.handle, handle)
        self.assertIn('RET', m._releases)
        m.handle_event(event('RET', 'RELEASE'))
        m.handle_event(event('HOME')); m.handle_event(event('RET'))
        self.assertEqual(calls, [('next', None)])

    def test_reentry_measurement_fails_without_deadlock(self):
        m = self.make()
        m.measure = lambda text, size: m.close()
        with self.assertRaisesRegex(RuntimeError, 'recursive'):
            m.open([], (0, 0), 'reenter', lambda *a: None)
        self.assertFalse(m.is_open)

    def test_disposal_during_measurement_does_not_leak_busy_owner(self):
        m = self.make()
        handle = m.handle
        m.measure = lambda text, size: m.dispose()
        try:
            m.open([], (0, 0), 'reenter', lambda *a: None)
        except BaseException as exc:
            self.assertIsInstance(exc, RuntimeError)
            self.assertIn('recursive popup disposal', str(exc))
        else:
            self.fail('recursive disposal did not fail')
        self.assertFalse(m.is_open)
        self.assertEqual(m.lib.narwhal_popup_destroy(handle), 2)

    def test_stale_handle_revision_and_gc(self):
        m = self.make()
        handle, revision = m.handle, m._state.revision
        m.handle_event(event('HOME'))
        p = native.PanelState()
        self.assertEqual(m.lib.narwhal_popup_panel(handle, revision, 0, C.byref(p), C.sizeof(p)), 2)
        m.dispose()
        self.assertEqual(m.lib.narwhal_popup_panel(handle, m._state.revision, 0, C.byref(p), C.sizeof(p)), 2)
        n = native.NativePopup((100, 100))
        h, lib = n.handle, n.lib
        self.assertGreater(h, handle)
        del n
        gc.collect()
        self.assertEqual(lib.narwhal_popup_destroy(h), 2)

    def test_retired_gui_mode_refuses_before_launch(self):
        driver = load('_popup_gui_policy', ROOT / 'spike/test_ui_core_popup_gui.py')
        with patch.object(sys, 'argv', ['test', '--legacy']), patch.object(driver.tempfile, 'mkdtemp') as scratch:
            with self.assertRaisesRegex(ValueError, '--legacy was retired'):
                driver.launch()
            scratch.assert_not_called()

    def test_factory_unavailable_and_bad_library(self):
        with patch.object(native, 'library', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'required native'):
                native.model((1000, 800), 1, None)
        with patch.object(nt, '_loaded_library', None), patch.dict(os.environ,
                NARWHAL_UI_CORE_LIBRARY='/var/tmp/nwp/no-such-library.so'):
            with self.assertRaisesRegex(RuntimeError, 'cannot load'):
                native.model((1000, 800), 1, None)
        with patch.object(nt, 'library', return_value=NS()):
            with self.assertRaisesRegex(RuntimeError, 'popup core'):
                native.library()

    def test_same_window_guard_transfer_and_different_window_discard(self):
        # UI registration is covered by the unchanged old suite and the real GUI.
        owner = NS(width=1000, height=800, screen=NS(areas=[]))
        other = NS(width=1000, height=800, screen=NS(areas=[]), other=True)
        pkg.ui, pkg.uiscale = NS(_text_width=lambda t, s: len(t)*s), NS(current=lambda: .85)
        with patch.object(popup, 'register'):
            a = popup.open([], (0, 0), '', lambda *a: None, context=NS(window=owner))
            self.models.append(a)
            a.handle_event(event('BUTTON7MOUSE'))
            b = popup.open([], (0, 0), '', lambda *a: None, context=NS(window=owner))
            self.models.append(b)
            self.assertFalse(a.is_open)
            b.close()
            self.assertTrue(popup.handle_event(event('BUTTON7MOUSE', 'RELEASE'), NS(window=owner)))
            b.open([], (0, 0), '', lambda *a: None)
            b.handle_event(event('PEN'))
            c = popup.open([], (0, 0), '', lambda *a: None, context=NS(window=other))
            self.models.append(c)
            c.close()
            self.assertFalse(popup.handle_event(event('PEN', 'RELEASE'), NS(window=owner)))
            self.assertFalse(popup.handle_event(event('PEN', 'RELEASE'), NS(window=other)))

    def test_bad_native_reply_cannot_consume_or_dispatch(self):
        calls = []
        m = self.make(callback=lambda *args: calls.append(args))
        original = m.lib.narwhal_popup_apply
        def corrupt(*args):
            rc = original(*args)
            C.cast(args[-2], C.POINTER(native.Reply)).contents.panels = 65
            return rc
        with patch.object(m.lib, 'narwhal_popup_apply', side_effect=corrupt):
            with self.assertRaisesRegex(RuntimeError, 'invalid popup reply'):
                m.handle_event(event('HOME'))
        self.assertFalse(m.is_open)
        self.assertEqual(m.panels, ())
        self.assertEqual(calls, [])
        with self.assertRaisesRegex(RuntimeError, 'not replayed'):
            m.handle_event(event('LEFTMOUSE'))

    def test_ffi_exception_after_mutation_is_terminal(self):
        m = self.make()
        handle = m.handle
        original = m.lib.narwhal_popup_apply
        def fail(*args):
            original(*args)
            raise OSError('injected transport failure after mutation')
        with patch.object(m.lib, 'narwhal_popup_apply', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'transport failure'):
                m.handle_event(event('HOME'))
        self.assertFalse(m.is_open)
        self.assertEqual(m.lib.narwhal_popup_destroy(handle), 2)
        with self.assertRaisesRegex(RuntimeError, 'not replayed'):
            m.handle_event(event('HOME'))

    def test_failed_new_open_preserves_previous_drawing_model(self):
        previous = popup.open([{'label': 'old', 'action': 1}], (100, 500), '',
                              lambda *a: None, window_size=(1000, 800))
        self.models.append(previous)
        def fail(text, size):
            raise LookupError('new font failure')
        pkg.ui, pkg.uiscale = NS(_text_width=fail), NS(current=lambda: 1)
        with self.assertRaisesRegex(LookupError, 'new font failure'):
            popup.open([], (100, 500), '', lambda *a: None, context=NS(window=None),
                       window_size=(1000, 800))
        self.assertIs(popup._model, previous)
        self.assertTrue(previous.is_open)
        self.assertTrue(previous.panels[0].row_rects)

    def test_dismissal_releases_opaque_payloads(self):
        import weakref
        class Payload:
            pass
        for method in ('close', 'dispose', 'activate'):
            action = Payload()
            ref = weakref.ref(action)
            m = self.make([{'label': 'opaque', 'action': action}])
            del action
            self.assertIsNotNone(ref())
            if method == 'activate':
                m.handle_event(event('HOME'))
                m.handle_event(event('RET'))
            else:
                getattr(m, method)()
            gc.collect()
            self.assertIsNone(ref(), method)
            self.assertFalse(m.is_open)

    def test_unregister_disposes_retained_owner(self):
        m = popup.open([], (0, 0), '', lambda *a: None, window_size=(1000, 800))
        self.models.append(m)
        handle = m.handle
        with patch.dict(sys.modules, {'bpy': NS(types=NS())}):
            popup.unregister()
        self.assertFalse(m.is_open)
        self.assertEqual(m.lib.narwhal_popup_destroy(handle), 2)

    def test_header_layout_and_prototypes(self):
        import shutil
        import subprocess
        compiler = shutil.which('cc')
        self.assertIsNotNone(compiler, 'ABI header check requires a C compiler')
        source = '''#include "narwhal_ui_core.h"
_Static_assert(sizeof(NarwhalEditorState) == 88, "editor ABI changed");
_Static_assert(sizeof(NarwhalPopupRow) == 24, "row layout");
_Static_assert(sizeof(NarwhalPopupRequest) == 64, "request layout");
_Static_assert(sizeof(NarwhalPopupReply) == 48, "reply layout");
_Static_assert(sizeof(NarwhalPopupPanel) == 192, "panel layout");
uint32_t probe(uint64_t h, NarwhalPopupRequest *r, NarwhalPopupRow *rows,
               NarwhalPopupMeasure m, NarwhalPopupReply *out) {
    return narwhal_popup_apply(h, r, sizeof(*r), 0, 0, rows, 1, m, out, sizeof(*out));
}
'''
        result = subprocess.run([compiler, '-std=c11', '-Werror', '-fsyntax-only',
            '-I', str(ROOT / 'native/ui_core'), '-x', 'c', '-'], input=source,
            text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_editor_primitive_abi_unchanged(self):
        lib = native.library()
        self.assertEqual(lib.narwhal_editor_abi(), 1)
        self.assertEqual(C.sizeof(nt.State), 88)
        self.assertEqual(C.sizeof(native.Reply), 48)
        e = nt.Editor(lib, 'AV 😀', False, None)
        try:
            e.apply(5, 1)
            e.apply(2, text='xy')
            self.assertEqual(e.text(), 'AxyV 😀')
        finally:
            e.close()


if __name__ == '__main__':
    assert native.library() is not None, 'native qualification requires an explicit built core'
    unittest.main(verbosity=2)
