#!/usr/bin/env python3
"""Shared CAD reads and proposal gates; no executor/auto-approval demo or GUI.

The real-document case deliberately has no authoritative change-token/dispatcher.
It must stay unchanged. Synthetic preconditions only test validation state logic.
"""
import argparse
import copy
import importlib
import json
from pathlib import Path
import sys
import types
import unittest

import test_ai_workflow as workflow

ai = workflow.ai
http = workflow.http
LIBRARY = None


def preconditions():
    # Explicit synthetic fixture, not production authorization or document revision.
    return {"document_session": "synthetic-test-only", "change_token": 9, "selection": [],
            "layer_id": "00000000-0000-0000-0000-000000000001", "layer_locked": False,
            "layer_visible": True, "cplane": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
            "units": "Millimeters", "tolerance": 0.01, "command_idle": True}


def request_call(session, name, arguments):
    prepared = session.prepare("document-A", user_text="inspect and propose")
    data = workflow.payload("openai_compatible", "", [("call", name, arguments)])
    return session.finish(workflow.send(prepared, data), "document-A")


def stage(session, expected=None):
    result = request_call(session, "narwhal_propose_circle", {"center": [2, 3, 0], "radius": 4})
    assert result["status"] == "tool_calls"
    return session.propose_circle(result["token"], "call", "document-A", expected=expected)


class CadTests(unittest.TestCase):
    def test_catalog_discloses_real_execution_blocker(self):
        with workflow.workflow() as (_state, session):
            catalog = session.operation_catalog()
            self.assertEqual(len(catalog["tools"]), 13)
            self.assertEqual([tool['name'] for tool in catalog['tools'][-2:]], ['narwhal_plan','narwhal_verify'])
            operation = catalog["operations"]["operations"][0]
            self.assertEqual(operation["command"], "Circle")
            self.assertFalse(operation["execution_available"])
            self.assertTrue(operation["proposal_available"])
            self.assertGreater(len(operation["blockers"]), 0)
            box = catalog['operations']['operations'][1]
            self.assertEqual(box['command'], 'Box')
            self.assertTrue(box['requires_explicit_ui_approval'])
            self.assertIn('in_app_assistant_only', box['scope'])

    def test_proposal_wire_token_name_is_compatible(self):
        with workflow.workflow() as (_state, session):
            proposal = stage(session)["proposal"]
            self.assertEqual(set(proposal), {"id", "owner", "action", "expected"})
            self.assertEqual(set(proposal["owner"]), {"conversation", "generation", "request"})
            self.assertNotIn("request_token", proposal)
            renamed = copy.deepcopy(proposal)
            renamed["request_token"] = renamed.pop("owner")
            with self.assertRaisesRegex(ai.WorkflowUnavailable, "invalid_request"):
                session.review_proposal(renamed, "document-A", accepted=False)
            extra = copy.deepcopy(proposal)
            extra["request_token"] = extra["owner"]
            with self.assertRaisesRegex(ai.WorkflowUnavailable, "invalid_request"):
                session.review_proposal(extra, "document-A", accepted=False)
            # JSON serialization and deserialization retain the existing wire key.
            round_trip = json.loads(json.dumps(proposal))
            self.assertEqual(session.review_proposal(round_trip, "document-A", accepted=False)["status"], "rejected")

    def test_unavailable_authority_cannot_be_approved_or_dispatched(self):
        with workflow.workflow() as (_state, session):
            result = stage(session)
            self.assertFalse(result["dispatch_enabled"])
            self.assertIsNone(result["proposal"]["expected"])
            with self.assertRaisesRegex(ai.WorkflowUnavailable, "authoritative_preconditions_required"):
                session.review_proposal(result["proposal"], "document-A", accepted=True)
            with self.assertRaises(ai.WorkflowUnavailable):
                session.check_proposal(result["proposal"]["id"], "document-A")

    def test_exact_review_preconditions_replay_and_production_block(self):
        with workflow.workflow() as (state, session):
            p = stage(session, preconditions())["proposal"]
            changed = copy.deepcopy(p)
            changed["action"]["arguments"]["radius"] = 8
            with self.assertRaisesRegex(ai.WorkflowUnavailable, "approval_mismatch"):
                session.review_proposal(changed, "document-A", current=preconditions(), accepted=True)
            with self.assertRaisesRegex(ai.WorkflowUnavailable, "approval_required"):
                session.check_proposal(p["id"], "document-A", current=preconditions())
            reviewed = session.review_proposal(p, "document-A", current=preconditions(), accepted=True)
            self.assertFalse(reviewed["dispatch_enabled"])
            with self.assertRaisesRegex(ai.WorkflowUnavailable, "approval_replay"):
                session.review_proposal(p, "document-A", current=preconditions(), accepted=True)
            for key, value in (("change_token", 10), ("document_session", "replacement"),
                               ("selection", [{"object_id": "00000000-0000-0000-0000-000000000002", "content_token": "different"}]),
                               ("units", "Inches"), ("tolerance", 0.1)):
                changed = preconditions(); changed[key] = value
                with self.assertRaisesRegex(ai.WorkflowUnavailable, "stale_preconditions"):
                    session.check_proposal(p["id"], "document-A", current=changed)
            blocked = session.check_proposal(p["id"], "document-A", current=preconditions())
            self.assertEqual(blocked["status"], "blocked")
            self.assertFalse(blocked["dispatch_enabled"])
            state.new_chat()
            with self.assertRaisesRegex(ai.WorkflowUnavailable, "no_proposal"):
                session.check_proposal(p["id"], "document-A", current=preconditions())

        with workflow.workflow() as (_state, session):
            expected = preconditions()
            expected["selection"] = [{"object_id": "00000000-0000-0000-0000-000000000002", "content_token": "shape-before"}]
            p = stage(session, expected)["proposal"]
            session.review_proposal(p, "document-A", current=expected, accepted=True)
            changed = copy.deepcopy(expected)
            changed["selection"][0]["content_token"] = "same-id-new-geometry"
            with self.assertRaisesRegex(ai.WorkflowUnavailable, "stale_preconditions"):
                session.check_proposal(p["id"], "document-A", current=changed)

    def test_reject_default_busy_locked_and_cross_session_review(self):
        with workflow.workflow() as (_state, session):
            p = stage(session, preconditions())["proposal"]
            self.assertEqual(session.review_proposal(p, "document-A")["status"], "rejected")
        for key, value, code in (("command_idle", False, "command_busy"), ("layer_locked", True, "layer_unavailable")):
            with workflow.workflow() as (_state, session):
                expected = preconditions(); expected[key] = value
                with self.assertRaisesRegex(ai.WorkflowUnavailable, code):
                    stage(session, expected)
        with workflow.workflow() as (_a, first), workflow.workflow() as (_b, second):
            a = stage(first, preconditions())["proposal"]
            stage(second, preconditions())
            with self.assertRaisesRegex(ai.WorkflowUnavailable, "approval_mismatch"):
                second.review_proposal(a, "document-A", current=preconditions(), accepted=True)

    def test_rejected_call_cannot_be_resurrected_and_old_review_preserves_new_turn(self):
        with workflow.workflow() as (_state, session):
            outcome = request_call(session, "narwhal_propose_circle", {"center": [0, 0, 0], "radius": 2})
            p = session.propose_circle(outcome["token"], "call", "document-A", expected=preconditions())["proposal"]
            session.review_proposal(p, "document-A", accepted=False)
            with self.assertRaisesRegex(ai.WorkflowUnavailable, "proposal_call_replay"):
                session.propose_circle(outcome["token"], "call", "document-A", expected=preconditions())
        with workflow.workflow() as (_state, session):
            p = stage(session, preconditions())["proposal"]
            fresh = session.prepare("document-B", user_text="new context")
            with self.assertRaises(ai.WorkflowUnavailable):
                session.review_proposal(p, "document-A", current=preconditions(), accepted=True)
            result = session.finish(workflow.send(fresh, workflow.payload("openai_compatible", "fresh")), "document-B")
            self.assertEqual(result["status"], "completed")

    def test_invalid_circle_and_extra_operation_refused(self):
        for args in ({"center": [0, 0, 0], "radius": 0}, {"center": [0, 0], "radius": 1},
                     {"center": [0, 0, 0], "radius": 1, "execute": True}):
            with workflow.workflow() as (_state, session):
                result = request_call(session, "narwhal_propose_circle", args)
                self.assertEqual(result["status"], "failed")
        with workflow.workflow() as (_state, session):
            with self.assertRaisesRegex(ai.WorkflowUnavailable, "invalid_request"):
                session._exchange({"op": "execute", "command": "Circle"})

    def test_real_document_inspection_proposal_and_refusal_leave_geometry_unchanged(self):
        import rhino3dm as r3
        model_module = importlib.import_module("narwhal3d.model")
        tools = importlib.import_module("narwhal3d.automation_tools")
        model = model_module.NarwhalModel()
        geometry = r3.Circle(r3.Point3d(2, 3, 0), 4).ToNurbsCurve()
        oid, _ = model.add_object(geometry, "Existing exact circle")
        app = types.SimpleNamespace(model=model, active_command=None,
                                    doc_title=lambda: "Disposable", doc_modified=lambda: False,
                                    ctx=types.SimpleNamespace(selected_objects=lambda: []))
        reader = tools.ApplicationReader(app, "disposable-test")
        before = model.file.Encode()
        with workflow.workflow() as (_state, session):
            outcome = request_call(session, "narwhal_objects", {"offset": 0, "limit": 1})
            result = session.read_tool_result(outcome["token"], "call", reader, "document-A")
            self.assertFalse(result["is_error"])
            self.assertEqual(result["output"]["objects"][0]["object_id"], oid)
            self.assertFalse(result["output"]["provenance"]["geometry_measured"])
            session.record_results(outcome["token"], [result], "document-A")
            # A fresh call ID is required; use a separate request fixture explicitly.
            p = session.prepare("document-A", user_text="propose a profile")
            data = workflow.payload("openai_compatible", "", [("proposal", "narwhal_propose_circle", {"center": [8, 2, 0], "radius": 3})])
            outcome = session.finish(workflow.send(p, data), "document-A")
            with self.assertRaisesRegex(ai.WorkflowUnavailable, "proposal_requires_review"):
                session.read_tool_result(outcome["token"], "proposal", reader, "document-A")
            proposal = session.propose_circle(outcome["token"], "proposal", "document-A")
            with self.assertRaisesRegex(ai.WorkflowUnavailable, "authoritative_preconditions_required"):
                session.review_proposal(proposal["proposal"], "document-A", accepted=True)
            self.assertEqual(model.file.Encode(), before)
            actual = model.file.Objects[0].Geometry
            self.assertEqual(actual.Degree, 2)
            self.assertTrue(actual.IsRational)
            self.assertTrue(actual.IsClosed)
            self.assertEqual(len(actual.Points), 9)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", required=True, type=Path)
    parser.add_argument("--test", choices=unittest.defaultTestLoader.getTestCaseNames(CadTests))
    args = parser.parse_args()
    workflow.LIBRARY = args.library.resolve()
    suite = unittest.TestSuite([CadTests(args.test)]) if args.test else unittest.defaultTestLoader.loadTestsFromTestCase(CadTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
