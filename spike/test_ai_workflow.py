#!/usr/bin/env python3
"""Actual Rust-backed conversation/HTTP-adapter tests; deterministic local fixtures."""
import argparse
from contextlib import contextmanager
import importlib
import json
from pathlib import Path
import sys
import threading
import time
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg
ai = importlib.import_module("narwhal3d.ai_assistant")
http = importlib.import_module("narwhal3d.ai_transport")
LIBRARY = None


def payload(provider, text="answer", calls=(), reason=None):
    if provider == "anthropic":
        blocks = ([{"type": "text", "text": text}] if text else [])
        blocks += [{"type": "tool_use", "id": ident, "name": name, "input": args}
                   for ident, name, args in calls]
        return {"role": "assistant", "content": blocks,
                "stop_reason": reason or ("tool_use" if calls else "end_turn"),
                "usage": {"output_tokens": 5}}
    message = {"role": "assistant", "content": text or None}
    if calls:
        message["tool_calls"] = [{"id": ident, "type": "function", "function": {
            "name": name, "arguments": json.dumps(args)}} for ident, name, args in calls]
    return {"choices": [{"message": message, "finish_reason": reason or ("tool_calls" if calls else "stop")}],
            "usage": {"completion_tokens": 5}}


class Response:
    def __init__(self, raw):
        self.raw, self.closed = raw, False
    def read(self, limit):
        return self.raw[:limit]
    def close(self):
        self.closed = True


def send(prepared, data):
    response = Response(json.dumps(data).encode())
    receipt = http.send_turn(prepared, {"TEST_KEY": "private-canary"},
                             opener=lambda _request, timeout: response)
    assert response.closed
    return receipt


@contextmanager
def workflow(provider="openai_compatible", allow=True):
    state = ai.AssistantState(provider=ai.set_provider(provider, "fixture-model", credential_env="TEST_KEY"))
    with ai.WorkflowSession(state, "document-A", allow_reads=allow, library_path=LIBRARY) as session:
        yield state, session


class WorkflowTests(unittest.TestCase):
    def test_text_preserves_provider_apis_and_secret_boundaries(self):
        for provider in ("openai", "openai_compatible", "anthropic"):
            with self.subTest(provider=provider), workflow(provider) as (state, session):
                prepared = session.prepare("document-A", user_text="inspect")
                receipt = send(prepared, payload(provider))
                result = session.finish(receipt, "document-A")
                self.assertEqual(result["status"], "completed")
                self.assertEqual(state.messages[-1].text, "answer")
                self.assertNotIn("private-canary", repr(receipt) + repr(prepared) + repr(state))
                self.assertFalse(receipt.physical_http_cancelled)

    def test_tool_only_multiple_calls_and_result_round_trip(self):
        for provider in ("openai_compatible", "anthropic"):
            with self.subTest(provider=provider), workflow(provider) as (state, session):
                prepared = session.prepare("document-A", user_text="inspect")
                calls = [("one", "narwhal_document_summary", {}), ("two", "narwhal_selection_summary", {})]
                outcome = session.finish(send(prepared, payload(provider, "", calls)), "document-A")
                self.assertEqual(outcome["status"], "tool_calls")
                self.assertEqual(len(outcome["response"]["tool_calls"]), 2)
                with self.assertRaises(ai.WorkflowUnavailable):
                    session.prepare("document-A", continuation=True)
                results = [{"call_id": "one", "output": {"units": "Millimeters"}, "is_error": False},
                           {"call_id": "two", "output": {"code": "selection_unavailable"}, "is_error": True}]
                with self.assertRaises(ai.WorkflowUnavailable):
                    session.record_results(outcome["token"], list(reversed(results)), "document-A")
                session.record_results(outcome["token"], results, "document-A")
                prepared2 = session.prepare("document-A", continuation=True)
                messages = prepared2.data["body"]["messages"]
                if provider == "anthropic":
                    blocks = messages[-1]["content"]
                    self.assertEqual([b["tool_use_id"] for b in blocks], ["one", "two"])
                    self.assertTrue(blocks[1]["is_error"])
                else:
                    self.assertEqual([m["tool_call_id"] for m in messages[-2:]], ["one", "two"])
                    self.assertTrue(json.loads(messages[-1]["content"])["is_error"])
                self.assertEqual(session.finish(send(prepared2, payload(provider)), "document-A")["status"], "completed")

    def test_new_chat_cannot_receive_old_answer_or_call(self):
        for calls in ([], [("one", "narwhal_document_summary", {})]):
            with self.subTest(tool_only=bool(calls)), workflow() as (state, session):
                old = session.prepare("document-A", user_text="old chat")
                receipt = send(old, payload("openai_compatible", "late", calls))
                state.new_chat()
                self.assertEqual(session.finish(receipt, "document-A")["status"], "stale")
                self.assertFalse(state.messages)
                fresh = session.prepare("document-A", user_text="new chat")
                self.assertEqual(session.finish(receipt, "document-A")["status"], "stale")
                self.assertEqual([m.text for m in state.messages], ["new chat"])
                self.assertEqual(state.status, "sending")
                self.assertEqual(session.finish(send(fresh, payload("openai_compatible", "fresh")), "document-A")["status"], "completed")
                self.assertEqual(state.messages[-1].text, "fresh")

    def test_changed_config_or_document_invalidates_old_response(self):
        for kind in ("provider", "model", "endpoint", "document"):
            with self.subTest(kind=kind), workflow() as (state, session):
                old = session.prepare("document-A", user_text="old")
                receipt = send(old, payload("openai_compatible", "late"))
                doc = "document-B" if kind == "document" else "document-A"
                if kind == "model": state.provider = ai.set_provider("openai_compatible", "changed", credential_env="TEST_KEY")
                if kind == "provider": state.provider = ai.set_provider("openai", "fixture-model", credential_env="TEST_KEY")
                if kind == "endpoint": state.provider = ai.set_provider("openai_compatible", "fixture-model", endpoint="http://localhost:9999/v1", credential_env="TEST_KEY")
                self.assertEqual(session.finish(receipt, doc)["status"], "stale")
                self.assertNotIn("late", [m.text for m in state.messages])

    def test_cancel_timeout_and_replay_have_explicit_outcomes(self):
        with workflow() as (state, session):
            old = session.prepare("document-A", user_text="old")
            cancelled = session.invalidate()
            self.assertFalse(cancelled["physical_http_cancelled"])
            receipt = http.send_turn(old, {}, opener=lambda *a, **k: self.fail("cancelled request sent"))
            self.assertEqual(receipt.failure, "cancelled")
            self.assertEqual(session.finish(receipt, "document-A")["status"], "stale")
            prepared = session.prepare("document-A", user_text="timeout", timeout_ms=1)
            # Isolate the native expiry gate from HTTP preflight timing.
            receipt = http.TurnTransportResult(prepared.data["token"], raw=json.dumps(payload("openai_compatible")))
            time.sleep(.01)
            self.assertEqual(session.finish(receipt, "document-A")["status"], "timeout")
            self.assertEqual(session.finish(receipt, "document-A")["status"], "stale")

    def test_no_read_consent_or_selection_toggle_means_no_tools(self):
        for allow in (False, True):
            with self.subTest(allow=allow), workflow(allow=allow) as (state, session):
                state.include_selection = False
                prepared = session.prepare("document-A", user_text="ask", context={"private": "selection-canary"})
                self.assertNotIn("tools", prepared.data["body"])
                self.assertNotIn("selection-canary", json.dumps(prepared.data["body"]))
                calls = [("one", "narwhal_document_summary", {})]
                result = session.finish(send(prepared, payload("openai_compatible", "", calls)), "document-A")
                self.assertEqual(result["code"], "tools_not_authorized")

    def test_budgets_truncation_malformed_and_unsupported_calls(self):
        with workflow() as (_state, session):
            for kwargs in ({"context": "x" * 32769}, {"max_tokens": 4097}, {"user_text": "x" * 8001}):
                with self.assertRaises(ai.WorkflowUnavailable):
                    session.prepare("document-A", **({"user_text": "ask"} | kwargs))
        for data, code in ((payload("openai_compatible", "partial", reason="length"), "truncated_response"),
                           (payload("anthropic", "partial", reason="max_tokens"), "truncated_response"),
                           (payload("openai_compatible", "", [("x", "shell", {})]), "unsupported_tool"),
                           (payload("openai_compatible", "", [("x", "narwhal_document_summary", {"execute": True})]), "invalid_tool_arguments")):
            provider = "anthropic" if "stop_reason" in data else "openai_compatible"
            with self.subTest(code=code), workflow(provider) as (_state, session):
                p = session.prepare("document-A", user_text="ask")
                result = session.finish(send(p, data), "document-A")
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["code"], code)

    def test_unknown_json_arguments_and_output_tokens_are_refused(self):
        with workflow() as (_state, session):
            p = session.prepare("document-A", user_text="ask", max_tokens=10)
            data = payload("openai_compatible")
            data["usage"]["completion_tokens"] = 11
            self.assertEqual(session.finish(send(p, data), "document-A")["code"], "output_token_limit")
        with workflow() as (_state, session):
            p = session.prepare("document-A", user_text="ask")
            data = payload("openai_compatible", "", [("x", "narwhal_object_summary", {})])
            data["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = '{"object_id":"a","object_id":"b"}'
            self.assertEqual(session.finish(send(p, data), "document-A")["status"], "failed")

    def test_prose_is_not_a_tool_and_main_thread_is_enforced(self):
        with workflow() as (state, session):
            p = session.prepare("document-A", user_text="ask")
            prose = '```json\n{"command":"Delete"}\n```'
            receipt = send(p, payload("openai_compatible", prose))
            seen = []
            def wrong_thread():
                try: session.finish(receipt, "document-A")
                except ai.WorkflowUnavailable: seen.append("refused")
            worker = threading.Thread(target=wrong_thread)
            worker.start(); worker.join(timeout=1)
            self.assertEqual(seen, ["refused"])
            result = session.finish(receipt, "document-A")
            self.assertFalse(result["response"]["tool_calls"])
            self.assertEqual(state.messages[-1].text, prose)

    def test_turn_action_and_total_token_budgets(self):
        with workflow() as (_state, session):
            for _ in range(8):
                p = session.prepare("document-A", user_text="ask")
                self.assertEqual(session.finish(send(p, payload("openai_compatible")), "document-A")["status"], "completed")
            with self.assertRaisesRegex(ai.WorkflowUnavailable, "turn_budget"):
                session.prepare("document-A", user_text="ninth")
        with workflow() as (_state, session):
            for turn in range(5):
                p = session.prepare("document-A", user_text="ask")
                data = payload("openai_compatible")
                data["usage"]["completion_tokens"] = 4096
                result = session.finish(send(p, data), "document-A")
                self.assertEqual(result["status"], "completed" if turn < 4 else "failed")
            self.assertEqual(result["code"], "token_budget")
        with workflow() as (_state, session):
            for turn in range(5):
                p = session.prepare("document-A", user_text="ask")
                calls = [(f"{turn}-{i}", "narwhal_document_summary", {}) for i in range(2)]
                outcome = session.finish(send(p, payload("openai_compatible", "", calls)), "document-A")
                if turn == 4:
                    self.assertEqual(outcome["code"], "action_budget")
                else:
                    session.record_results(outcome["token"], [{"call_id": c[0], "output": {}, "is_error": False} for c in calls], "document-A")

    def test_completed_tool_call_id_cannot_be_replayed(self):
        with workflow() as (_state, session):
            calls = [("repeat", "narwhal_document_summary", {})]
            p = session.prepare("document-A", user_text="ask")
            outcome = session.finish(send(p, payload("openai_compatible", "", calls)), "document-A")
            session.record_results(outcome["token"], [{"call_id": "repeat", "output": {}, "is_error": False}], "document-A")
            p = session.prepare("document-A", continuation=True)
            self.assertEqual(session.finish(send(p, payload("openai_compatible", "", calls)), "document-A")["code"], "replayed_tool_call")

    def test_old_tool_callbacks_do_not_reset_new_pending_context(self):
        for callback in ("results", "resolve"):
            with self.subTest(callback=callback), workflow() as (_state, session):
                old = session.prepare("document-A", user_text="old")
                outcome = session.finish(send(old, payload("openai_compatible", "", [("old-call", "narwhal_document_summary", {})])), "document-A")
                fresh = session.prepare("document-B", user_text="new")
                with self.assertRaises(ai.WorkflowUnavailable):
                    if callback == "results":
                        session.record_results(outcome["token"], [{"call_id": "old-call", "output": {}, "is_error": False}], "document-A")
                    else:
                        session.read_tool_result(outcome["token"], "old-call", None, "document-A")
                result = session.finish(send(fresh, payload("openai_compatible", "fresh")), "document-B")
                self.assertEqual(result["status"], "completed")

    def test_unicode_envelope_and_oversized_completion_retire_request(self):
        with workflow() as (_state, session):
            p = session.prepare("document-A", user_text="ask")
            data = payload("openai_compatible")
            data["opaque_metadata"] = "é" * 180000
            receipt = http.TurnTransportResult(p.data["token"], raw=json.dumps(data, ensure_ascii=False))
            self.assertEqual(session.finish(receipt, "document-A")["status"], "completed")
            p = session.prepare("document-A", user_text="ask")
            oversized = http.TurnTransportResult(p.data["token"], raw="x" * 1048576)
            self.assertNotEqual(session.finish(oversized, "document-A")["status"], "completed")
            self.assertEqual(session.prepare("document-A", user_text="retry").data["status"], "ready")

    def test_poll_expires_logical_request_without_waiting_for_http(self):
        with workflow() as (state, session):
            prepared = session.prepare("document-A", user_text="ask", timeout_ms=1)
            time.sleep(.01)
            outcome = session.poll_timeout()
            self.assertEqual(outcome["status"], "timeout")
            self.assertFalse(outcome["physical_http_cancelled"])
            self.assertTrue(prepared.cancel_event.is_set())
            self.assertEqual(state.error, "timeout")
            receipt = http.TurnTransportResult(prepared.data["token"], raw=json.dumps(payload("openai_compatible")))
            self.assertEqual(session.finish(receipt, "document-A")["status"], "stale")

    def test_document_change_does_not_transfer_read_grant(self):
        with workflow() as (_state, session):
            prepared = session.prepare("document-B", user_text="new document")
            self.assertFalse(prepared.data["binding"]["allow_reads"])
            self.assertNotIn("tools", prepared.data["body"])
            receipt = http.TurnTransportResult(prepared.data["token"], raw=json.dumps(payload("openai_compatible")))
            with self.assertRaisesRegex(ai.WorkflowUnavailable, "document_context_identity_required"):
                session.finish(receipt, None)

    def test_missing_backend_is_explicit_not_text_fallback(self):
        with workflow() as (_state, session):
            with self.assertRaisesRegex(ai.WorkflowUnavailable, "document_context_identity_required"):
                session.prepare(None, user_text="inspect")
        state = ai.AssistantState(provider=ai.set_provider("openai_compatible", "fixture"))
        with self.assertRaises(ai.WorkflowUnavailable):
            ai.WorkflowSession(state, "document-A", library_path="/nonexistent/narwhal-workflow.so")
        self.assertEqual(ai.build_request(state)["model"], "fixture")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", required=True, type=Path)
    parser.add_argument("--test", choices=unittest.defaultTestLoader.getTestCaseNames(WorkflowTests))
    args = parser.parse_args()
    LIBRARY = args.library.resolve()
    suite = unittest.TestSuite([WorkflowTests(args.test)]) if args.test else unittest.defaultTestLoader.loadTestsFromTestCase(WorkflowTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
