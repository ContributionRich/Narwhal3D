"""Pure console tests: python3 -B spike/test_shell_input.py [--mutations].

Compile fresh source (including TextField) without package __init__, Blender,
GUI or bytecode caches. Mutations run only in memory, never in the shared tree.
"""
import io
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1] / "narwhal3d"
SOURCE = ROOT / "shell_input.py"
SOURCE_TEXT = SOURCE.read_text()


def load():
    package = ModuleType("_shell_input_tests")
    package.__path__ = []
    field = ModuleType(package.__name__ + ".textfield")
    field.__file__ = str(ROOT / 'textfield.py')
    shell = ModuleType(package.__name__ + ".shell_input")
    shell.__package__ = package.__name__
    modules = {module.__name__: module for module in (package, field, shell)}
    with patch.dict(sys.modules, modules):
        exec(compile((ROOT / "textfield.py").read_text(),
                     str(ROOT / "textfield.py"), "exec"), field.__dict__)
        exec(compile(SOURCE_TEXT, str(SOURCE), "exec"), shell.__dict__)
    return shell


def event(key="A", **kw):
    values = dict(type=key, value="PRESS", unicode="", ascii="",
                  ctrl=False, shift=False, alt=False, oskey=False)
    values.update(kw)
    return NS(**values)


class ShellInputTests(unittest.TestCase):
    def setUp(self):
        self.shell = load()
        self.text = "abcd"
        self.field = self.shell.sync(self.text)
        self.clipboard = "keep"

    def press(self, key="A", **kw):
        result = self.shell.handle(event(key, **kw), self.text, self.clipboard)
        self.assertIsNotNone(result)
        self.assertIs(result["consumed"], True)
        self.text = result["text"]
        if "clipboard" in result:
            self.clipboard = result["clipboard"]
        self.assertIs(self.shell.sync(self.text), self.field)
        return result

    def state(self):
        return self.field.text, self.field.caret, self.field.selection()

    def test_initial_and_unchanged_sync(self):
        self.assertEqual(self.state(), ("abcd", 4, None))
        self.assertFalse(self.field.multiline)
        self.field.set_caret(1, select=True)
        for _ in range(3):
            self.assertIs(self.shell.sync("abcd"), self.field)
            self.assertEqual(self.state(), ("abcd", 1, (1, 4)))

    def test_external_replacement_resets_once_even_same_length(self):
        self.field.select_all()
        self.shell.sync("wxyz")
        self.assertEqual(self.state(), ("wxyz", 4, None))
        self.field.set_caret(2, select=True)
        self.shell.sync("wxyz")
        self.assertEqual(self.state(), ("wxyz", 2, (2, 4)))
        self.shell.sync("")
        self.assertEqual(self.state(), ("", 0, None))

    def test_normalized_external_text_matches_once(self):
        raw = "a\r\nb\tc\x00"
        self.shell.sync(raw)
        self.assertEqual(self.field.text, "a b c")
        self.field.set_caret(1, select=True)
        self.shell.sync(raw)
        self.assertEqual(self.state(), ("a b c", 1, (1, 5)))

    def test_middle_insert_echo_preserves_caret(self):
        self.press("LEFT_ARROW")
        self.press("X", unicode="X")
        self.assertEqual(self.state(), ("abcXd", 4, None))
        self.press("Y", unicode="Y")
        self.assertEqual(self.state(), ("abcXYd", 5, None))

    def test_handle_syncs_external_text_before_edit(self):
        self.field.select_all()
        result = self.shell.handle(event("LEFT_ARROW"), "other", "")
        self.assertEqual(result, {"text": "other", "consumed": True})
        self.assertEqual(self.state(), ("other", 4, None))
        self.shell.sync("other")
        self.assertEqual(self.field.caret, 4)

    def test_shift_arrows_extend_reverse_and_collapse(self):
        self.press("LEFT_ARROW", shift=True)
        self.press("LEFT_ARROW", shift=True)
        self.assertEqual(self.state(), ("abcd", 2, (2, 4)))
        self.press("RIGHT_ARROW", shift=True)
        self.assertEqual(self.state(), ("abcd", 3, (3, 4)))
        self.press("LEFT_ARROW")
        self.assertEqual(self.state(), ("abcd", 3, None))
        self.field.set_caret(1)
        self.press("RIGHT_ARROW", shift=True)
        self.press("RIGHT_ARROW")
        self.assertEqual(self.state(), ("abcd", 2, None))

    def test_home_end_and_shift(self):
        self.field.set_caret(2)
        self.press("HOME", shift=True)
        self.assertEqual(self.state(), ("abcd", 0, (0, 2)))
        self.press("END", shift=True)
        self.assertEqual(self.state(), ("abcd", 4, (2, 4)))
        self.press("HOME")
        self.assertEqual(self.state(), ("abcd", 0, None))
        self.press("END")
        self.assertEqual(self.state(), ("abcd", 4, None))

    def test_backspace_and_delete_at_caret(self):
        self.field.set_caret(2)
        self.press("BACK_SPACE")
        self.assertEqual(self.state(), ("acd", 1, None))
        self.press("DEL")
        self.assertEqual(self.state(), ("ad", 1, None))

    def test_selection_replaced_by_typing_or_deleted(self):
        for key, chars, expected in [("X", "x", "axd"),
                                     ("BACK_SPACE", "", "ad"), ("DEL", "", "ad")]:
            with self.subTest(key=key):
                self.setUp()
                self.field.set_caret(1)
                self.field.set_caret(3, select=True)
                self.press(key, unicode=chars)
                self.assertEqual(self.state(), (expected, 2 if chars else 1, None))

    def test_boundaries_are_consumed_noops(self):
        self.press("HOME")
        self.press("LEFT_ARROW")
        self.press("BACK_SPACE")
        self.assertEqual(self.state(), ("abcd", 0, None))
        self.press("END")
        self.press("RIGHT_ARROW")
        self.press("DEL")
        self.assertEqual(self.state(), ("abcd", 4, None))
        self.text = ""
        for key in ("BACK_SPACE", "DEL", "LEFT_ARROW", "RIGHT_ARROW"):
            self.press(key)
            self.assertEqual(self.state(), ("", 0, None))

    def test_select_all_copy_and_cut(self):
        self.press("A", ctrl=True, unicode="a")
        self.assertEqual(self.state(), ("abcd", 4, (0, 4)))
        self.press("C", ctrl=True, unicode="c")
        self.assertEqual(self.clipboard, "abcd")
        self.assertEqual(self.state(), ("abcd", 4, (0, 4)))
        self.field.set_caret(1)
        self.field.set_caret(3, select=True)
        self.press("X", ctrl=True, unicode="x")
        self.assertEqual(self.clipboard, "bc")
        self.assertEqual(self.state(), ("ad", 1, None))

    def test_empty_copy_cut_preserve_clipboard(self):
        for key in ("C", "X"):
            result = self.press(key, ctrl=True)
            self.assertNotIn("clipboard", result)
            self.assertEqual(self.clipboard, "keep")
            self.assertEqual(self.state(), ("abcd", 4, None))

    def test_paste_normalization_and_selection(self):
        self.field.set_caret(1)
        self.field.set_caret(3, select=True)
        self.clipboard = "é\r\n猫\rZ\nQ\t!\x00\x1b"
        result = self.press("V", ctrl=True, unicode="v")
        self.assertNotIn("clipboard", result)
        self.assertEqual(self.state(), ("aé 猫 Z Q !d", 10, None))

    def test_empty_paste_preserves_selection(self):
        self.field.select_all()
        for self.clipboard in ("", "\x00\x1b"):
            self.press("V", ctrl=True)
            self.assertEqual(self.state(), ("abcd", 4, (0, 4)))

    def test_unicode_ascii_fallback_and_space(self):
        self.press("E", unicode="é猫", ascii="e")
        self.press("SPACE", unicode=" ")
        self.press("ONE", ascii="!")
        result = self.shell.handle(NS(type="B", value="PRESS", ascii="b"), self.text, "")
        self.assertEqual(result, {"text": "abcdé猫 !b", "consumed": True})

    def test_reserved_keys_never_consumed_even_with_printable_payload(self):
        for key in ("RET", "NUMPAD_ENTER", "TAB", "ESC", "UP_ARROW", "DOWN_ARROW"):
            for ctrl in (False, True):
                with self.subTest(key=key, ctrl=ctrl):
                    self.assertIsNone(self.shell.handle(
                        event(key, unicode="x", ctrl=ctrl), self.text, ""))
        self.assertEqual(self.state(), ("abcd", 4, None))

    def test_other_shortcuts_never_consumed(self):
        for key in ("S", "Z", "Y", "LEFT_ARROW", "RIGHT_ARROW", "HOME", "END",
                    "BACK_SPACE", "DEL"):
            self.assertIsNone(self.shell.handle(event(key, ctrl=True, unicode="s"),
                                                self.text, ""))
        for modifier in ("alt", "oskey"):
            for key in ("A", "C", "X", "V", "LEFT_ARROW", "BACK_SPACE"):
                self.assertIsNone(self.shell.handle(event(key, unicode="x", **{modifier: True}),
                                                    self.text, ""))
        self.assertEqual(self.state(), ("abcd", 4, None))

    def test_release_unknown_controls_and_repeat(self):
        for ev in (event("BACK_SPACE", value="RELEASE"),
                   event("A", value="RELEASE", unicode="a"),
                   event("MOUSEMOVE", value="NOTHING"), event("F1"),
                   event(unicode="\x01"), event(unicode="\n"), event(unicode="\t")):
            self.assertIsNone(self.shell.handle(ev, self.text, ""))
        self.assertEqual(self.state(), ("abcd", 4, None))
        self.press("A", unicode="a", is_repeat=True)
        self.assertEqual(self.state(), ("abcda", 5, None))


def mutations():
    cases = [
        ("reset unchanged sync", "if text != _last_text:", "if True:"),
        ("forget returned-text acknowledgement", "_last_text = field.text", "pass"),
        ("ignore external replacement", "_field.set_text(text)", "pass"),
        ("drop shift selection", 'shift = getattr(event, "shift", False)', "shift = False"),
        ("reverse left", "field.move(-1, select=shift)", "field.move(1, select=shift)"),
        ("delete acts as backspace", "field.delete()", "field.backspace()"),
        ("cut only copies", 'field.cut() if key == "X" else selected', "selected"),
        ("paste types shortcut", "field.insert(clipboard)", 'field.insert("v")'),
        ("no ASCII fallback", 'or getattr(event, "ascii", "")', 'or ""'),
        ("consume Enter", '"RET", "NUMPAD_ENTER",', '"NUMPAD_ENTER",'),
        ("edit releases", 'if getattr(event, "value", None) != "PRESS":', "if False:"),
        ("leak Ctrl shortcuts", 'if getattr(event, "ctrl", False):', "if False:"),
    ]
    for label, old, new in cases:
        assert SOURCE_TEXT.count(old) == 1, (label, "mutation target changed")
        with patch.dict(globals(), SOURCE_TEXT=SOURCE_TEXT.replace(old, new)):
            output = io.StringIO()
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(ShellInputTests)
            result = unittest.TextTestRunner(stream=output).run(suite)
        if result.wasSuccessful() or result.errors:
            raise AssertionError(label + ": mutation escaped or crashed\n" + output.getvalue())
        print("RED:", label, "--", len(result.failures), "assertion failure(s)")
    print("All", len(cases), "mutations detected; on-disk source unchanged.")


if __name__ == "__main__":
    if "--mutations" in sys.argv:
        mutations()
    else:
        unittest.main()
