"""Persistent editor qualification. Requires NARWHAL_UI_CORE_LIBRARY, no bpy.

Run python3 -B spike/test_ui_core_editor.py. The legacy class is the retained,
unchanged implementation, not a second invocation of the native algorithms.
"""
import copy
import ctypes as C
import gc
import importlib.util
from pathlib import Path
import random
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
        return "error", type(exc), str(exc)


class EditorTests(unittest.TestCase):
    def pair(self, *args, **kwargs):
        a, b = tf.TextField(*args, **kwargs), tf._LegacyTextField(*args, **kwargs)
        self.assertIsNotNone(a._native)
        self.assertEqual(state(a), state(b))
        return a, b

    def test_differential_transitions(self):
        rng = random.Random(601)
        samples = ["", "abc", "\r\na\r\tb\x00", "😀𐐀e\u0301\ud800\udfff", "a\nb c"]
        for multiline in (False, True):
            for limit in (None, -3, 0, 1, 10, 100):
                a, b = self.pair("abc", multiline, limit)
                for _ in range(500):
                    choices = [("insert", (rng.choice(samples),)), ("set_text", (rng.choice(samples),)),
                               ("set_caret", (rng.randint(-10, 20), rng.choice([True, False]))),
                               ("move", (rng.randint(-10, 10), rng.choice([True, False]))),
                               ("home", (rng.choice([True, False]),)), ("end", (rng.choice([True, False]),)),
                               ("backspace", ()), ("delete", ()), ("cut", ()),
                               ("select_all", ()), ("_delete_selection", ())]
                    method, args = rng.choice(choices)
                    self.assertEqual(outcome(a, method, args), outcome(b, method, args), (method, args))
                    self.assertEqual(state(a), state(b), (method, args))
                    self.assertIsNotNone(a._native)

    def test_assignments_and_atomic_handover(self):
        cases = [("text", "a\x00\r\ud800z"), ("caret", -1), ("caret", 99),
                 ("anchor", 99), ("anchor", -1), ("anchor", "1"),
                 ("multiline", 1), ("multiline", "yes"), ("max_chars", 2.5),
                 ("max_chars", True), ("max_chars", 2**90), ("text", None),
                 ("text", ["a"]), ("text", b"abc"), ("text", "x")]
        for key, value in cases:
            for method, args in [("insert", ("x",)), ("delete", ()), ("backspace", ()),
                                 ("set_text", ("abcdef",)), ("home", (True,)), ("end", ())]:
                a, b = self.pair("abc")
                setattr(a, key, value)
                setattr(b, key, value)
                self.assertEqual(state(a), state(b))
                self.assertEqual(outcome(a, method, args), outcome(b, method, args), (key, value, method))
                self.assertEqual(state(a), state(b))
        a, b = self.pair("abc")
        n = a._native
        a.caret = b.caret = 1
        a.anchor = b.anchor = 2
        a.text = b.text = "x\x00\r\ud800"
        a.max_chars = b.max_chars = 2
        a.multiline = b.multiline = True
        self.assertIs(a._native, n)
        self.assertEqual(state(a), state(b))
        self.assertNotIn("text", a.__dict__)
        self.assertNotIn("caret", a.__dict__)

    def test_error_order_and_coercions(self):
        for method, args in [("set_caret", ("bad", True)), ("set_caret", ("bad", False)),
                             ("move", (1.5,)), ("move", ("1",)), ("move", (2**100,)),
                             ("insert", (None,)), ("insert", (5,)), ("set_text", (b"x",))]:
            a, b = self.pair("abcd")
            a.set_caret(1, True)
            b.set_caret(1, True)
            self.assertEqual(outcome(a, method, args), outcome(b, method, args))
            self.assertEqual(state(a), state(b))
        for cls in (tf.TextField, tf._LegacyTextField):
            f = cls("abc")
            class Coercion:
                def __str__(self):
                    f.set_caret(0)
                    return "X"
            f.insert(Coercion())
            self.assertEqual(state(f)[:3], ("Xabc", 1, None))

    def test_projection_and_no_upload_on_movement(self):
        f, _ = self.pair("a" * 100000)
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
        f, _ = self.pair("a" * 100000)
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
        f, _ = self.pair("abc")
        n, out = f._native, adapter.State()
        data = (C.c_uint32 * 1)(88)
        for size in (0, C.sizeof(out)-1, C.sizeof(out)+1):
            rc = n.lib.narwhal_editor_edit(n.state.handle, 2, 0, 0, data, 1, C.byref(out), size)
            self.assertEqual(rc, 1)
        for start, count in ((4, 0), (2, 2), (2**63, 0)):
            self.assertEqual(n.lib.narwhal_editor_read(n.state.handle, n.state.text_revision, start, data, count), 3)
        self.assertEqual(f.text, "abc")

    def test_lifetime_and_copy(self):
        f, _ = self.pair("abc")
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
        f, _ = self.pair("closed")
        f._native.close()
        for action in (lambda: f.text, lambda: f.insert("x"), lambda: copy.copy(f)):
            with self.assertRaises(RuntimeError):
                action()

    def test_full_unicode_classifier(self):
        # Chunked exhaustive comparison includes supplementary planes and surrogates.
        for start in range(0, 0x110000, 8192):
            text = "".join(map(chr, range(start, min(start + 8192, 0x110000))))
            a, b = self.pair(text, True)
            self.assertEqual(a.text, b.text, (unicodedata.unidata_version, start))

    def test_hostile_projection_never_replays(self):
        for corruption in ("length", "caret", "anchor", "handle", "revision", "text_revision", "flags", "change_start", "change_end", "inserted"):
            f, _ = self.pair("abc")
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
        f, _ = self.pair("abc")
        class Failure:
            def narwhal_editor_edit(self, *args):
                return 37
        f._native.lib = Failure()
        with self.assertRaises(RuntimeError):
            f.insert("x")

    def test_clone_length_bound_before_any_read(self):
        f, _ = self.pair("abcd")
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
            f = tf.TextField("a\r\nb")
            self.assertIsNone(f._native)
            self.assertEqual(f.text, "a b")
        with patch.object(adapter.unicodedata, "unidata_version", "99.0.0"):
            f = tf.TextField("abc")
            self.assertIsNone(f._native)
            self.assertEqual(f._backend_reason, "unsupported Unicode version")


if __name__ == "__main__":
    unittest.main()
