"""Independent baseline layout/callback equivalence, NOT native layout acceptance."""
import hashlib
import importlib.util
import os
from pathlib import Path

import types
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ.get("UI_CORE_SOURCE_DIR", PROJECT))
spec = importlib.util.spec_from_file_location("ui_layout_subject", ROOT / "narwhal3d/textfield.py")
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)
# Exact repository source at 168212d4b508f0b641448377706564345ccd144e:
# narwhal3d/textfield.py (GPL-3.0-or-later). Keep the fixture byte-for-byte;
# the independent oracle must work in source archives without Git/history.
REFERENCE_PATH = PROJECT / "spike/fixtures/textfield_168212d4.py"
REFERENCE_SHA256 = "3d75866e25e881d36477c58b2f3ba4c39da316025fb397871485becc2b45879c"


def load_reference():
    source = REFERENCE_PATH.read_bytes()
    if hashlib.sha256(source).hexdigest() != REFERENCE_SHA256:
        raise ValueError("reviewed textfield baseline SHA-256 mismatch")
    reference = types.ModuleType("reviewed_textfield")
    exec(compile(source, str(REFERENCE_PATH), "exec"), reference.__dict__)
    return reference


reference = load_reference()


class Measure:
    def __init__(self, fail=None, stateful=False):
        self.calls = []
        self.fail = fail
        self.stateful = stateful

    def __call__(self, text):
        self.calls.append(text)
        if len(self.calls) == self.fail:
            raise RuntimeError("font callback failure")
        return 10 * len(text) - 9 * text.count("AV") + (len(self.calls) % 3 if self.stateful else 0)


def outcome(call):
    try:
        return "ok", call()
    except Exception as e:
        return "err", type(e), str(e)


class LayoutTests(unittest.TestCase):
    def test_baseline_digest_rejects_tampering_before_execution(self):
        source = REFERENCE_PATH.read_bytes()
        for changed in (source + b"\n# changed\n", source[:-1],
                        source.replace(b"best_d", b"BEST_d"),
                        b"raise AssertionError('unverified baseline executed')\n"):
            with self.subTest(sha256=hashlib.sha256(changed).hexdigest()):
                self.assertNotEqual(source, changed)
                with patch.object(Path, "read_bytes", return_value=changed):
                    with self.assertRaisesRegex(ValueError, "baseline SHA-256 mismatch"):
                        load_reference()

    def test_baseline_callbacks_and_geometry(self):
        for text in ("", "AV AV\n\nAVVV", "i😀e\u0301 AV  ", "abcdef"):
            for width in (0, -1, 11, 45, float("nan")):
                for stateful in (False, True):
                    for fail in (None, 1, 3):
                        a, b = Measure(fail, stateful), Measure(fail, stateful)
                        got = outcome(lambda: subject.wrap_spans(text, width, a))
                        expected = outcome(lambda: reference.wrap_spans(text, width, b))
                        self.assertEqual(got, expected)
                        self.assertEqual(a.calls, b.calls)
                        if got[0] != "ok":
                            continue
                        spans = got[1]
                        for name, args in (("caret_xy", (spans, text, len(text)//2, 18)),
                                           ("index_at", (spans, text, 5.5, 0)),
                                           ("selection_rects", (spans, text, (0, len(text))))):
                            a, b = Measure(fail, stateful), Measure(fail, stateful)
                            def invoke(module, measure):
                                if name == "caret_xy":
                                    return module.caret_xy(*args[:3], measure, args[3])
                                return getattr(module, name)(*args, measure)
                            self.assertEqual(outcome(lambda: invoke(subject, a)), outcome(lambda: invoke(reference, b)))
                            self.assertEqual(a.calls, b.calls)


if __name__ == "__main__":
    unittest.main()
