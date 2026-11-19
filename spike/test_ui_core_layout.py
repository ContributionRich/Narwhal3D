"""Independent captured layout/callback DATA, NOT native layout acceptance."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from ui_golden_data import DATA, GOLDEN_PATH, load_data

ROOT = Path(os.environ.get("UI_CORE_SOURCE_DIR", Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location("ui_layout_subject", ROOT / "narwhal3d/textfield.py")
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


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
    import json
    try:
        return ["ok", json.loads(json.dumps(call()))]
    except Exception as e:
        return ["error", type(e).__name__, str(e)]


class LayoutTests(unittest.TestCase):
    def test_golden_digest_rejects_tampering_before_execution(self):
        source = GOLDEN_PATH.read_bytes()
        for changed in (source + b'\n# changed\n', source[:-1],
                        source.replace(b'"source_commit"', b'"source_commix"', 1),
                        b"raise AssertionError('unverified data executed')\n"):
            with patch.object(Path, 'read_bytes', return_value=changed):
                with self.assertRaisesRegex(ValueError, 'DATA SHA-256 mismatch'):
                    load_data()

    def test_golden_callbacks_and_geometry(self):
        self.assertEqual(len(DATA['layout']), 120)
        for trace in DATA['layout']:
            m = Measure(trace['fail'], trace['stateful'])
            got = outcome(lambda: subject.wrap_spans(trace['text'], float(trace['width']), m))
            self.assertEqual(got, trace['result'])
            self.assertEqual(m.calls, trace['calls'])
            for name, args, expected, calls in trace['helpers']:
                m = Measure(trace['fail'], trace['stateful'])
                def invoke():
                    if name == 'caret_xy':
                        return subject.caret_xy(*args[:3], m, args[3])
                    return getattr(subject, name)(*args, m)
                self.assertEqual(outcome(invoke), expected)
                self.assertEqual(m.calls, calls)


if __name__ == "__main__":
    unittest.main()
