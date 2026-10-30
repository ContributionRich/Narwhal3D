#!/usr/bin/env python3
"""Native CAD evidence is visible as status, separate from provider prose."""
import ast
import importlib.util
from pathlib import Path
import queue
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("evidence", ROOT / "narwhal3d/ai_evidence.py")
evidence = importlib.util.module_from_spec(spec); spec.loader.exec_module(evidence)

class EvidenceTests(unittest.TestCase):
    def state(self, count=0):
        return dict(known_action_results=count, approved_creation_receipts=count,
                    provider_text_is_geometry_evidence=False, current_geometry_verified=False)

    def test_trusted_receipts_are_not_current_geometry_certification(self):
        self.assertIn("no approved geometry", evidence.status_text(self.state()))
        text = evidence.status_text(self.state(1))
        self.assertIn("1 approved creation receipt", text)
        self.assertIn("not been independently reverified", text)
        self.assertIsNone(evidence.status_text(None))
        for bad in ([], {}, dict(self.state(), current_geometry_verified=True),
                    dict(self.state(), approved_creation_receipts=True),
                    dict(self.state(), known_action_results=999)):
            self.assertEqual(evidence.status_text(bad), "CAD evidence unavailable.")

    def test_actual_completion_hook_adds_a_separate_status(self):
        tree = ast.parse((ROOT / "narwhal3d/ui.py").read_text())
        tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_ai_poll_transport"]
        messages = []
        state = types.SimpleNamespace(extra={}, error="", add_message=lambda role, text: messages.append((role, text)))
        workflow = types.SimpleNamespace(poll_timeout=lambda: {"status": "idle"}, finish=lambda *_: {"status": "completed", "cad_state": self.state()})
        state.extra["workflow"] = workflow
        output = queue.Queue(); output.put(object())
        package = types.ModuleType("evidence_test_package"); package.ai_evidence = evidence
        ns = dict(__package__=package.__name__, _ai={"state": state, "transport_output": output},
                  _ai_scope=lambda: ("doc",), _tag_redraw_bands=lambda: None,
                  _ai_invalidate=lambda *_: self.fail("unexpected invalidation"))
        with patch.dict(sys.modules, {package.__name__: package}):
            exec(compile(tree, "actual-ui-completion", "exec"), ns)
            ns["_ai_poll_transport"]()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0][0], "status")
        self.assertIn("no approved geometry", messages[0][1])

    def test_dropped_evidence_cannot_pass_the_ui_test(self):
        with patch.object(evidence, "status_text", return_value=None):
            result = unittest.TestResult(); EvidenceTests("test_actual_completion_hook_adds_a_separate_status").run(result)
            self.assertFalse(result.wasSuccessful())

if __name__ == "__main__": unittest.main()
