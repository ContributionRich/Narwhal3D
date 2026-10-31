"""Persistent editor qualification. Requires NARWHAL_UI_CORE_LIBRARY, no bpy.

Run python3 -B spike/test_ui_core_editor.py. Golden DATA was captured from the
original before retirement; no executable Python editor is used as an oracle.
"""
import copy
import ctypes as C
import gc
import importlib.util
from pathlib import Path
import hashlib
from test_ui_core_golden import DATA
import os
import sys
import unicodedata
import unittest
from unittest.mock import patch
import weakref

ROOT = Path(os.environ.get("UI_CORE_SOURCE_DIR", Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location("ui_core_subject", ROOT / "narwhal3d/textfield.py")
tf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tf)
adapter = sys.modules["_narwhal_native_textfield"]


def state(f):
    return f.text, f.caret, f.anchor, f.multiline, f.max_chars


def outcome(f, method, args):
    try:
        return "ok", getattr(f, method)(*args)
    except Exception as exc:
        return "error", type(exc).__name__, str(exc)


class EditorTests(unittest.TestCase):
    def field(self, *args, **kwargs):
        f = tf.TextField(*args, **kwargs)
        self.assertIsNotNone(f._native)
        self.assertIsInstance(f.text, str)  # Prime the same read projection as the old pair fixture.
        return f

    def test_golden_transitions(self):
        for trace in DATA['editor']:
            f = self.field(*trace['args'])
            self.assertEqual(list(state(f)), trace['initial'])
            for method, args, expected, after in trace['steps']:
                self.assertEqual(list(outcome(f, method, args)), expected, (method, args))
                self.assertEqual(list(state(f)), after, (method, args))
                self.assertIsNotNone(f._native)

    def test_raw_assignments_remain_native(self):
        f = self.field('abc')
        owner = f._native
        f.caret, f.anchor = 1, 2
        f.text = 'x\x00\r\ud800'
        f.max_chars, f.multiline = 2, True
        self.assertEqual(state(f), ('x\x00\r\ud800', 1, 2, True, 2))
        self.assertIs(f._native, owner)
        self.assertNotIn('text', f.__dict__)
        self.assertNotIn('caret', f.__dict__)
        f.anchor = None
        self.assertIsNone(f.selection())
        f.caret = True
        f.max_chars = True
        f.multiline = 0
        self.assertEqual(state(f)[1:], (1, None, False, 1))

    def test_invalid_assignments_rejected_atomically(self):
        for key, value, error in [('caret', -1, ValueError), ('caret', 99, ValueError),
                ('anchor', 99, ValueError), ('anchor', -1, ValueError),
                ('anchor', '1', TypeError), ('max_chars', 2.5, TypeError),
                ('max_chars', 2**90, OverflowError), ('text', None, TypeError),
                ('text', ['a'], TypeError), ('text', b'abc', TypeError), ('text', 'x', ValueError)]:
            f = self.field('abc')
            owner, before, calls = f._native, state(f), f._native.calls
            with self.assertRaises(error, msg=(key, value)):
                setattr(f, key, value)
            self.assertEqual(state(f), before)
            self.assertIs(f._native, owner)
            self.assertEqual(owner.calls, calls)
            f.insert('X')
            self.assertEqual(f.text, 'abcX')

    def test_error_order_and_coercions(self):
        for select in (True, False):
            f = self.field('abcd')
            f.set_caret(1, True)
            before, calls = state(f), f._native.calls
            with self.assertRaises(ValueError):
                f.set_caret('bad', select)
            self.assertEqual(state(f), before)  # OURS: no partial anchor mutation.
            self.assertEqual(f._native.calls, calls)
        f = self.field(b'ab', multiline=1, max_chars=True)
        self.assertEqual(state(f), ('b', 1, None, True, 1))
        f.max_chars = None
        f.set_text(None)
        self.assertEqual(f.text, 'None')
        f.set_caret('1', 'yes')
        self.assertEqual(f.selection(), (1, 4))
        f.insert(5)
        self.assertEqual(f.text, 'N5')
        f.set_text('abcd')
        f.move(-1.5)
        self.assertEqual(f.caret, 2)
        f.move(2**100)
        self.assertEqual(f.caret, 4)
        f.move(-2**100)
        self.assertEqual(f.caret, 0)
        f.set_caret(2, True)
        f.move(-.5)
        self.assertEqual((f.caret, f.anchor), (0, None))
        class Coercion:
            def __str__(self):
                f.set_caret(0)
                return 'X'
        f.insert(Coercion())
        self.assertEqual(state(f)[:3], ('Xabcd', 1, None))

    def test_subclasses_and_copy_preserve_public_hooks(self):
        class Field(tf.TextField):
            def set_text(self, text):
                self.label = 'subclass'
                super().set_text(str(text).upper())
            def insert(self, text):
                return super().insert(str(text).upper())
        f = Field('abc')
        self.assertEqual(f.text, 'ABC')
        f.insert('x')
        for g in (copy.copy(f), copy.deepcopy(f)):
            self.assertIs(type(g), Field)
            self.assertEqual((g.text, g.label), ('ABCX', 'subclass'))
            self.assertNotEqual(g._native.state.handle, f._native.state.handle)
            g.insert('y')
            self.assertEqual(g.text, 'ABCXY')
        self.assertEqual(f.text, 'ABCX')

    def test_projection_and_no_upload_on_movement(self):
        f = self.field("a" * 100000)
        n = f._native
        text = f.text
        revision, copied, calls = n.state.text_revision, (n.uploaded, n.downloaded), n.calls
        for _ in range(100):
            f.move(-1)
            self.assertIs(f.text, text)
        self.assertEqual(n.calls - calls, 100)
        self.assertEqual((n.uploaded, n.downloaded), copied)
        self.assertEqual(n.state.text_revision, revision)
        f.insert("Z")
        self.assertEqual(n.uploaded - copied[0], 4)
        self.assertNotEqual(f.text, text)
        self.assertGreater(n.state.text_revision, revision)

    def test_lazy_delta_projection(self):
        f = self.field("a" * 100000)
        n = f._native
        before = n.downloaded
        f.insert("Z")
        self.assertEqual(f.text, "a" * 100000 + "Z")
        f.backspace()
        self.assertEqual(f.text, "a" * 100000)
        self.assertEqual(n.downloaded - before, 4)
        # Missing an intermediate projection deliberately falls back to a full READ,
        # never a full-state upload or replayed edit.
        f.insert("1")
        f.insert("2")
        self.assertEqual(f.text, "a" * 100000 + "12")
        f.insert("3")
        g = copy.copy(f)
        f.move(-1)
        self.assertEqual(f.text, g.text)
        self.assertEqual(f.text, "a" * 100000 + "123")
        f.set_caret(1)
        f.set_caret(5, True)
        f.insert("😀")
        self.assertEqual(f.text, "a😀" + "a" * 99995 + "123")

    def test_native_read_and_output_bounds(self):
        f = self.field("abc")
        n, out = f._native, adapter.State()
        data = (C.c_uint32 * 1)(88)
        for size in (0, C.sizeof(out)-1, C.sizeof(out)+1):
            rc = n.lib.narwhal_editor_edit(n.state.handle, 2, 0, 0, data, 1, C.byref(out), size)
            self.assertEqual(rc, 1)
        for start, count in ((4, 0), (2, 2), (2**63, 0)):
            self.assertEqual(n.lib.narwhal_editor_read(n.state.handle, n.state.text_revision, start, data, count), 3)
        self.assertEqual(f.text, "abc")

    def test_lifetime_and_copy(self):
        f = self.field("abc")
        f.set_caret(1, True)
        for clone in (copy.copy(f), copy.deepcopy(f)):
            self.assertEqual(state(clone), state(f))
            self.assertNotEqual(clone._native.state.handle, f._native.state.handle)
            clone.insert("X")
            self.assertEqual(f.text, "abc")
        n = f._native
        lib, handle = n.lib, n.state.handle
        ref = weakref.ref(n)
        del f, n
        gc.collect()
        self.assertIsNone(ref())
        self.assertEqual(lib.narwhal_editor_close(handle), 2)
        f = self.field("closed")
        f._native.close()
        for action in (lambda: f.text, lambda: f.insert("x"), lambda: copy.copy(f)):
            with self.assertRaises(RuntimeError):
                action()

    def test_full_unicode_classifier(self):
        # Chunked exhaustive comparison includes supplementary planes and surrogates.
        for start in range(0, 0x110000, 8192):
            text = "".join(map(chr, range(start, min(start + 8192, 0x110000))))
            f = self.field(text, True)
            digest = hashlib.sha256(f.text.encode('utf-32-le', 'surrogatepass')).hexdigest()
            self.assertEqual(digest, DATA['unicode'][unicodedata.unidata_version][start//8192])

    def test_hostile_projection_never_replays(self):
        for corruption in ("length", "caret", "anchor", "handle", "revision", "text_revision", "flags", "change_start", "change_end", "inserted"):
            f = self.field("abc")
            n = f._native
            real = n.lib
            class Hostile:
                def narwhal_editor_edit(self, handle, op, value, flags, data, length, out, size):
                    rc = real.narwhal_editor_edit(handle, op, value, flags, data, length, out, size)
                    setattr(out._obj, corruption, (1 << 40) if corruption not in ("revision", "text_revision") else 0)
                    return rc
            n.lib = Hostile()
            with self.assertRaises(RuntimeError):
                f.insert("X")
            self.assertIs(f._native, n)
            calls = n.calls
            with self.assertRaises(RuntimeError):
                f.insert("Y")
            self.assertEqual(n.calls, calls)
            self.assertNotIn("text", f.__dict__)
        f = self.field("abc")
        class Failure:
            def narwhal_editor_edit(self, *args):
                return 37
        f._native.lib = Failure()
        with self.assertRaises(RuntimeError):
            f.insert("x")

    def test_transport_exceptions_are_terminal_without_replay(self):
        for operation in ('edit', 'read'):
            f = self.field('abc')
            n, real = f._native, f._native.lib
            class Transport:
                def narwhal_editor_edit(self, *args):
                    real.narwhal_editor_edit(*args)
                    raise OSError('after native mutation')
                def narwhal_editor_read(self, *args):
                    raise OSError('during native read')
            if operation == 'read':
                f.insert('X')
            n.lib = Transport()
            with self.assertRaises(OSError):
                f.insert('X') if operation == 'edit' else f.text
            self.assertTrue(n.failed)
            self.assertIs(f._native, n)
            calls = n.calls
            for action in (lambda: f.insert('Y'), lambda: f.text, lambda: copy.copy(f)):
                with self.assertRaises(RuntimeError):
                    action()
            self.assertEqual(n.calls, calls)

    def test_input_bound_rejection_preserves_owner(self):
        f = self.field('abc')
        before, owner, calls = state(f), f._native, f._native.calls
        with patch.object(adapter, 'LIMIT', 3):
            for action in (lambda: f.set_text('abcd'), lambda: f.insert('abcd'),
                           lambda: setattr(f, 'text', 'abcd')):
                with self.assertRaises(OverflowError):
                    action()
                self.assertEqual(state(f), before)
                self.assertIs(f._native, owner)
                self.assertEqual(owner.calls, calls)
        self.assertFalse(owner.failed)

    def test_clone_length_bound_before_any_read(self):
        f = self.field("abcd")
        with patch.object(adapter, "LIMIT", 3):
            with self.assertRaises(RuntimeError):
                copy.copy(f)

    def test_loader_missing_broken_and_version(self):
        with patch.object(adapter, "_loaded_library", None), patch.dict(adapter.os.environ, {"NARWHAL_UI_CORE_LIBRARY": "/missing/ui-core.so"}):
            with self.assertRaises(RuntimeError):
                tf.TextField("preserve")
        class Fn:
            def __call__(self): return 99
        class Stale:
            narwhal_editor_abi = Fn()
        with patch.object(adapter, "_loaded_library", None), patch.object(C, "CDLL", return_value=Stale()), patch.dict(adapter.os.environ, {"NARWHAL_UI_CORE_LIBRARY": "stale"}):
            with self.assertRaises(RuntimeError):
                tf.TextField("preserve")
        with patch.object(adapter, "library", return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'required native'):
                tf.TextField('a\r\nb')
        with patch.object(adapter, '_loaded_library', None), patch.object(Path, 'is_file', return_value=False), patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'required native'):
                adapter.library()
            with self.assertRaisesRegex(RuntimeError, 'required native'):
                tf.TextField('preserve')
        with patch.object(adapter.unicodedata, "unidata_version", "99.0.0"):
            with self.assertRaisesRegex(RuntimeError, 'unsupported Unicode'):
                tf.TextField('abc')


if __name__ == "__main__":
    unittest.main()
