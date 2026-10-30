#!/usr/bin/env python3
"""No hang/OOM mutations: a measurement-call watchdog bounds the broken loop."""
import importlib.util
import inspect
from pathlib import Path
import unittest
from unittest.mock import patch

path = Path(__file__).resolve().parents[2] / "narwhal3d/textfield.py"
spec = importlib.util.spec_from_file_location("wrap_progress_reference", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class WrapTests(unittest.TestCase):
    def test_no_progress_refuses_without_unbounded_spans(self):
        for width, invalid_metric in ((-1.0, False), (float("nan"), False), (0.0, True)):
            calls = []
            def measure(text):
                calls.append(text)
                if len(calls) > 12:
                    raise RuntimeError("unbounded wrap detected by test watchdog")
                return max(1, len(text)) if invalid_metric else len(text)
            with self.subTest(width=width), self.assertRaisesRegex(ValueError, "cannot make progress"):
                module.wrap_spans("abc", width, measure)
            self.assertLessEqual(len(calls), 12)

    def test_existing_zero_infinite_and_nonadditive_behavior(self):
        self.assertEqual(module.wrap_spans("abc", 0, len), [(0, 1), (1, 2), (2, 3), (3, 3)])
        self.assertEqual(module.wrap_spans("abc", float("inf"), len), [(0, 3)])
        self.assertEqual(module.wrap_spans("", -1, len), [(0, 0)])
        def kerning(text):
            return len(text) * 10 - 8 * text.count("AV")
        self.assertEqual(module.wrap_spans("AV AV", 13, kerning), [(0, 3), (3, 5)])

    def test_removed_guard_goes_red_without_hanging_host(self):
        source = inspect.getsource(module._wrap_paragraph)
        old = '        if start >= p1:\n            raise ValueError("text wrapping cannot make progress with this width/measurement")\n'
        self.assertEqual(source.count(old), 1)
        with patch.dict(module.__dict__):
            exec(source.replace(old, ""), module.__dict__)
            result = unittest.TestResult()
            WrapTests("test_no_progress_refuses_without_unbounded_spans").run(result)
            self.assertFalse(result.wasSuccessful())
        result = unittest.TestResult()
        WrapTests("test_no_progress_refuses_without_unbounded_spans").run(result)
        self.assertTrue(result.wasSuccessful())

if __name__ == "__main__":
    unittest.main()
