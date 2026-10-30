#!/usr/bin/env python3
"""Native-backed Responses contracts; no builds, GUI, external HTTP or paid calls.

Run only after the parent builds the native library, using --library and optionally
--test. Python owns only fixture/orchestration code; parsing and policy exercise
Rust through the existing WorkflowSession ABI. Fixtures use literal official wire
shapes, not the adapter's serializer. Main/UI lifetime tests remain separate.
"""
import argparse
from contextlib import contextmanager
from dataclasses import replace
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import json
from pathlib import Path
import queue
import sys
import threading
import time
import unittest
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

sys.dont_write_bytecode = True
import test_ai_workflow as workflow

ai, transport = workflow.ai, workflow.http
PROVIDER = "openai_responses"
DOC = "document-A"
KEY = "responses-fixture-key-not-a-real-credential"


def message(text="A literal answer.", ident="msg_1", phase="final_answer"):
    return {"id": ident, "type": "message", "role": "assistant",
            "status": "completed", "phase": phase,
            "content": [{"type": "output_text", "text": text, "annotations": []}]}


def function(call_id="call_1", item_id="fc_item", name="narwhal_document_summary",
             arguments="{}"):
    return {"type": "function_call", "id": item_id, "call_id": call_id,
            "name": name, "arguments": arguments, "status": "completed"}


def reasoning(opaque="opaque-test-state", ident="rs_1"):
    return {"id": ident, "type": "reasoning", "summary": [],
            "encrypted_content": opaque}


def response(*items):
    return {"id": "resp_1", "object": "response", "status": "completed",
            "output": list(items), "usage": {"output_tokens": 5}}


def digest(value):
    # Hash comparisons preserve exact JSON values without dumping opaque state.
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


@contextmanager
def local_http(replies, *, hold=False, status=200):
    """Bounded real HTTP; no proxies/redirects or non-loopback socket targets.

    Authentication is recorded as a boolean, never as a header dump. Events hold
    headers until a lifecycle transition has happened, without timing races.
    """
    seen, errors = [], []
    arrived, release = threading.Event(), threading.Event()
    if not hold:
        release.set()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            try:
                length = int(self.headers["Content-Length"])
                if not 0 < length <= 262144:
                    raise ValueError("request bound")
                body = json.loads(self.rfile.read(length))
                index = len(seen)
                seen.append({"path": self.path, "body": body,
                             "bearer_ok": self.headers.get("Authorization") == "Bearer " + KEY,
                             "content_type": self.headers.get("Content-Type")})
                arrived.set()
                if not release.wait(5):
                    raise TimeoutError("fixture release")
                raw = json.dumps(replies[index], ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                # A logically cancelled client may close after headers arrive.
                pass
            except Exception as exc:
                errors.append(type(exc).__name__)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/v1"
    opener = build_opener(ProxyHandler({}), NoRedirect())

    def send(prepared):
        def open_local(request, timeout):
            parsed = urlsplit(request.full_url)
            if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                    or parsed.port != server.server_port or parsed.username is not None):
                raise ValueError("fixture forbids non-local HTTP")
            return opener.open(request, timeout=min(timeout, 5))
        return transport.send_turn(prepared, {"TEST_KEY": KEY}, opener=open_local)

    try:
        yield endpoint, seen, errors, arrived, release, send
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(2)


class ResponsesTests(unittest.TestCase):
    maxDiff = 400

    def finish(self, session, data, **kwargs):
        prepared = session.prepare(DOC, user_text="Inspect without modifying CAD.", **kwargs)
        return session.finish(workflow.send(prepared, data), DOC)

    def rejected(self, session, state, outcome, code=None):
        self.assertEqual(outcome["status"], "failed")
        if code is not None:
            self.assertEqual(outcome["code"], code)
        self.assertFalse(outcome.get("response", {}).get("tool_calls"))
        self.assertFalse(any(m.role == "assistant" for m in state.messages))
        with self.assertRaises(ai.WorkflowUnavailable):
            session.record_results(outcome["token"], [
                {"call_id": "call_1", "output": {}, "is_error": False}], DOC)

    def assert_hidden(self, needle, text):
        self.assertFalse(needle in text, "private fixture data leaked (payload omitted)")

    def assert_no_tools(self, body):
        tools = body.get("tools", [])
        self.assertTrue(isinstance(tools, list), "tools must be absent or a list")
        self.assertEqual(len(tools), 0, "request must grant no tools")

    def assert_private_body(self, body):
        self.assertIs(body["store"], False)
        self.assertIs(body["background"], False)
        self.assertIs(body["stream"], False)
        self.assertEqual(body["truncation"], "disabled")
        self.assertIn("reasoning.encrypted_content", body["include"])
        keys = tuple(body)
        self.assertNotIn("messages", keys)
        self.assertNotIn("max_tokens", keys)
        self.assertNotIn("previous_response_id", keys)
        self.assertNotIn("conversation", keys)

    def test_literal_text_is_not_a_tool_and_replay_is_private(self):
        prose = '```json\n{"command":"Delete"}\n```'
        data = response(message(prose))
        with workflow.workflow(PROVIDER) as (state, session):
            outcome = self.finish(session, data)
            self.assertEqual(outcome["status"], "completed")
            self.assertEqual(outcome["response"]["text"], prose)
            self.assertEqual(outcome["response"]["tool_calls"], [])
            self.assertEqual(outcome["response"]["output_tokens"], 5)
            self.assertNotIn("replay_items", tuple(outcome["response"]))
            self.assertEqual(state.messages[-1].text, prose)
            following = session.prepare(DOC, user_text="Continue.")
            self.assertEqual(digest(following.data["body"]["input"][1]),
                             digest(data["output"][0]))

    def test_output_nulls_are_omitted_only_where_input_schema_requires(self):
        call = function()
        call.update(id=None, status=None, namespace=None)
        call["async"] = None
        thought = reasoning()
        thought.update(content=None, status=None)
        with workflow.workflow(PROVIDER) as (_state, session):
            outcome = self.finish(session, response(thought, message(phase="commentary"), call))
            session.record_results(outcome["token"], [{"call_id":"call_1","output":{},"is_error":False}], DOC)
            body = session.prepare(DOC, continuation=True).data["body"]
            replay = body["input"]
            r = next(item for item in replay if item.get("type") == "reasoning")
            f = next(item for item in replay if item.get("type") == "function_call")
            self.assertTrue(all(key not in f for key in ("id", "status", "namespace", "async")))
            self.assertTrue(all(key not in r for key in ("content", "status")))
            self.assertEqual(r["encrypted_content"], "opaque-test-state")
            self.assertEqual(next(item for item in replay if item.get("type") == "message")["phase"], "commentary")

    def test_reply_reserves_space_for_correlated_tool_results(self):
        with workflow.workflow(PROVIDER) as (_state, session):
            items = [message("x", f"msg_{n}") for n in range(58)] + [function()]
            result = self.finish(session, response(*items))
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["code"], "context_budget")

    def test_tool_only_uses_call_id_not_replay_item_id(self):
        with workflow.workflow(PROVIDER) as (state, session):
            outcome = self.finish(session, response(function()))
            self.assertEqual(outcome["status"], "tool_calls")
            self.assertEqual(outcome["response"]["text"], "")
            call = outcome["response"]["tool_calls"][0]
            self.assertEqual(call["id"], "call_1")
            self.assertEqual(call["name"], "narwhal_document_summary")
            self.assertEqual(call["arguments"], {})
            self.assertFalse(any(m.role == "assistant" for m in state.messages))
            with self.assertRaises(ai.WorkflowUnavailable):
                session.prepare(DOC, continuation=True)
            session.record_results(outcome["token"], [
                {"call_id": "call_1", "output": {"units": "Millimeters"}, "is_error": False}], DOC)
            following = session.prepare(DOC, continuation=True)
            result = following.data["body"]["input"][-1]
            self.assertEqual(result["type"], "function_call_output")
            self.assertEqual(result["call_id"], "call_1")
            self.assertIsInstance(result["output"], str)
            self.assertEqual(json.loads(result["output"]),
                             {"is_error": False, "output": {"units": "Millimeters"}})

    def test_real_http_mixed_replay_keeps_order_phase_and_encrypted_state(self):
        raw_reasoning = reasoning()
        raw_reasoning["summary"] = [{"type": "summary_text", "text": "private-summary-canary"}]
        first = response(raw_reasoning, message("Inspecting. ", phase="commentary"),
                         function(arguments="{  }"),
                         message("Need selection.", "msg_2"),
                         function("call_2", "fc_second", "narwhal_selection_summary"))
        with local_http([first, response(message("Finished.", "msg_3"))]) as wire:
            endpoint, seen, errors, _arrived, _release, send = wire
            with workflow.workflow(PROVIDER) as (state, session):
                state.provider = replace(state.provider, endpoint=endpoint)
                prepared = session.prepare(DOC, user_text="Inspect.", max_tokens=128)
                receipt = send(prepared)
                self.assertIsNone(receipt.failure)
                outcome = session.finish(receipt, DOC)
                self.assertEqual(outcome["status"], "tool_calls")
                self.assertEqual(outcome["response"]["text"], "Inspecting. Need selection.")
                self.assertEqual([c["id"] for c in outcome["response"]["tool_calls"]],
                                 ["call_1", "call_2"])
                self.assertNotIn("replay_items", tuple(outcome["response"]))
                for hidden in ("opaque-test-state", "private-summary-canary"):
                    self.assert_hidden(hidden, json.dumps(outcome))
                    self.assert_hidden(hidden, repr(state))
                session.record_results(outcome["token"], [
                    {"call_id": "call_1", "output": {"units": "Millimeters"}, "is_error": False},
                    {"call_id": "call_2", "output": {"code": "selection_unavailable"}, "is_error": True}], DOC)
                following = session.prepare(DOC, continuation=True, max_tokens=128)
                self.assertEqual(session.finish(send(following), DOC)["status"], "completed")
                self.assertEqual(state.messages[-1].text, "Finished.")
                self.assert_hidden(KEY, repr(prepared) + repr(following) + repr(receipt) + repr(state))
            self.assertEqual(len(seen), 2)
            for request in seen:
                self.assertEqual(request["path"], "/v1/responses")
                self.assertTrue(request["bearer_ok"], "environment credential was not used")
                self.assertEqual(request["content_type"], "application/json")
                self.assert_private_body(request["body"])
                self.assertEqual(request["body"]["max_output_tokens"], 128)
            self.assertEqual(seen[0]["body"]["input"], [{"role": "user", "content": "Inspect."}])
            replay = seen[1]["body"]["input"]
            self.assertEqual(len(replay), 8)
            self.assertEqual(digest(replay[1:6]), digest(first["output"]))
            self.assertEqual([item["type"] for item in replay[-2:]],
                             ["function_call_output", "function_call_output"])
            self.assertEqual([item["call_id"] for item in replay[-2:]], ["call_1", "call_2"])
            self.assertEqual(json.loads(replay[-1]["output"]),
                             {"is_error": True, "output": {"code": "selection_unavailable"}})
            self.assertEqual(errors, [])

    def test_explicit_provider_and_model_select_route_not_substrings(self):
        popup = importlib.import_module("narwhal3d.ai_settings_popup")
        saved = []
        draft = popup.PopupModel(ai.set_provider("openai", "gpt-fixture"),
                                 anchor=(600, 500), window_size=(900, 700), scale=1,
                                 on_save=saved.append,
                                 on_refresh=lambda _config: self.fail("unexpected model discovery"))
        draft.activate("provider")
        self.assertIn(("openai_responses", "OpenAI Responses"), draft.choices())
        draft.choose_provider(PROVIDER)
        self.assertTrue(draft.save())
        self.assertEqual(saved[0].provider_id, PROVIDER)
        self.assertEqual(saved[0].model, "gpt-fixture")
        self.assertEqual(saved[0].endpoint, "https://api.openai.com/v1")
        self.assertEqual(saved[0].credential_env, "OPENAI_API_KEY")
        for provider, model, path in (
                (PROVIDER, "ordinary-model-without-api-hints", "/v1/responses"),
                ("openai", "responses-gpt-5-reasoning", "/v1/chat/completions"),
                ("openai_compatible", "responses-o3", "/v1/chat/completions")):
            with self.subTest(provider=provider), local_http([
                    response(message()) if provider == PROVIDER else workflow.payload(provider)]) as wire:
                endpoint, seen, errors, _arrived, _release, send = wire
                with workflow.workflow(provider) as (state, session):
                    state.provider = ai.set_provider(provider, model, endpoint, credential_env="TEST_KEY")
                    prepared = session.prepare(DOC, user_text="Explain.")
                    self.assertEqual(prepared.data["body"]["model"], model)
                    self.assertEqual(session.finish(send(prepared), DOC)["status"], "completed")
                self.assertEqual(seen[0]["path"], path)
                self.assertEqual(errors, [])

    def test_malformed_double_encoded_and_duplicate_key_arguments_are_atomic(self):
        for label, arguments in (
                ("malformed", "{"), ("double-json", '"{}"'), ("array", "[]"),
                ("null", "null"), ("duplicate-key", '{"offset":0,"offset":1,"limit":1}'),
                ("extra-key", '{"execute":true}')):
            with self.subTest(case=label), workflow.workflow(PROVIDER) as (state, session):
                name = "narwhal_objects" if label == "duplicate-key" else "narwhal_document_summary"
                data = response(function(), function("call_2", "fc_2", name, arguments))
                self.rejected(session, state, self.finish(session, data))
        with workflow.workflow(PROVIDER) as (state, session):
            prepared = session.prepare(DOC, user_text="Ask.")
            raw = json.dumps(response(function())).replace('"status": "completed"',
                    '"status": "failed", "status": "completed"', 1)
            receipt = transport.TurnTransportResult(prepared.data["token"], raw=raw)
            self.rejected(session, state, session.finish(receipt, DOC), "invalid_json")

    def test_duplicate_item_and_call_ids_and_completed_call_replay_are_refused(self):
        for label, items in (
                ("item", [function(), function("call_2")]),
                ("call", [function(), function(item_id="fc_2")]),
                ("cross-kind-item", [message(ident="fc_item"), function()])):
            with self.subTest(case=label), workflow.workflow(PROVIDER) as (state, session):
                self.rejected(session, state, self.finish(session, response(*items)))
        with workflow.workflow(PROVIDER) as (state, session):
            first = self.finish(session, response(function()))
            self.assertEqual(first["status"], "tool_calls")
            session.record_results(first["token"], [
                {"call_id": "call_1", "output": {}, "is_error": False}], DOC)
            second = session.prepare(DOC, continuation=True)
            replay = response(function(item_id="fc_new"))
            self.rejected(session, state, session.finish(workflow.send(second, replay), DOC),
                          "replayed_tool_call")

    def test_unknown_item_id_reversed_and_duplicate_results_do_not_consume_calls(self):
        with workflow.workflow(PROVIDER) as (_state, session):
            first = self.finish(session, response(function(), function("call_2", "fc_2")))
            self.assertEqual(first["status"], "tool_calls")
            good = [{"call_id": "call_1", "output": {}, "is_error": False},
                    {"call_id": "call_2", "output": {}, "is_error": False}]
            for label, results in (
                    ("unknown", [dict(good[0], call_id="unknown"), good[1]]),
                    ("item-not-call", [dict(good[0], call_id="fc_item"), good[1]]),
                    ("reversed", list(reversed(good))), ("duplicate", [good[0], good[0]]),
                    ("missing", good[:1])):
                with self.subTest(case=label), self.assertRaises(ai.WorkflowUnavailable):
                    session.record_results(first["token"], results, DOC)
            session.record_results(first["token"], good, DOC)
            following = session.prepare(DOC, continuation=True)
            self.assertEqual([item["call_id"] for item in following.data["body"]["input"][-2:]],
                             ["call_1", "call_2"])

    def test_refusal_nonfinal_and_provider_errors_accept_no_calls_or_text(self):
        cases = []
        for status in ("incomplete", "failed", "cancelled", "queued", "in_progress"):
            data = response(function(), message("Must not publish."))
            data["status"] = status
            cases.append((status, data))
        refusal = message()
        refusal["content"] = [{"type": "refusal", "refusal": "private-refusal-canary"}]
        cases.append(("refusal", response(function(), refusal)))
        data = response(function())
        data["error"] = {"code": "server_error", "message": "private-provider-canary"}
        cases.append(("provider-error", data))
        data = response(function())
        data["incomplete_details"] = {"reason": "max_output_tokens"}
        cases.append(("incomplete-details", data))
        for item in (message(), function()):
            item["status"] = "in_progress"
            cases.append(("nonfinal-" + item["type"], response(function("other", "other_item"), item)))
        for label, data in cases:
            with self.subTest(case=label), workflow.workflow(PROVIDER) as (state, session):
                outcome = self.finish(session, data)
                self.rejected(session, state, outcome)
                self.assert_hidden("private-", repr(outcome) + repr(state.error))

    def test_usage_zero_missing_null_bool_and_output_token_cap(self):
        for label, usage, expected in (("zero", {"output_tokens": 0}, 0),
                                       ("missing", None, None), ("null", None, None),
                                       ("positive", {"output_tokens": 9}, 9)):
            with self.subTest(case=label), workflow.workflow(PROVIDER) as (_state, session):
                data = response(message())
                data["usage"] = usage
                if label == "missing":
                    del data["usage"]
                outcome = self.finish(session, data, max_tokens=10)
                self.assertEqual(outcome["status"], "completed")
                self.assertEqual(outcome["response"]["output_tokens"], expected)
        for label, usage, code in (("bool", {"output_tokens": True}, "invalid_usage"),
                                  ("field-null", {"output_tokens": None}, "invalid_usage"),
                                  ("negative", {"output_tokens": -1}, "invalid_usage"),
                                  ("float", {"output_tokens": 1.5}, "invalid_usage"),
                                  ("over-cap", {"output_tokens": 11}, "output_token_limit")):
            with self.subTest(case=label), workflow.workflow(PROVIDER) as (state, session):
                data = response(function())
                data["usage"] = usage
                self.rejected(session, state, self.finish(session, data, max_tokens=10), code)

    def test_hosted_multimedia_and_unreplayable_reasoning_are_refused(self):
        unsupported = [
            {"id": "ws_1", "type": "web_search_call", "status": "completed",
             "action": {"type": "search", "query": "CAD"}},
            {"id": "fs_1", "type": "file_search_call", "status": "completed",
             "queries": ["CAD"], "results": None},
            {"id": "ci_1", "type": "code_interpreter_call", "status": "completed",
             "container_id": "container_1", "code": "print(1)", "outputs": []},
            {"id": "ig_1", "type": "image_generation_call", "status": "completed", "result": "AA=="},
            {"id": "mc_1", "type": "mcp_call", "name": "execute", "server_label": "remote",
             "arguments": "{}", "output": "done", "error": None},
        ]
        for kind in ("output_audio", "output_image"):
            item = message()
            item["content"] = [{"type": kind, "data": "AA=="}]
            unsupported.append(item)
        for opaque in (None, ""):
            unsupported.append(reasoning(opaque))
        item = reasoning()
        del item["encrypted_content"]
        unsupported.append(item)
        for index, item in enumerate(unsupported):
            with self.subTest(case=index), workflow.workflow(PROVIDER) as (state, session):
                self.rejected(session, state, self.finish(session, response(function(), item)))
        for key, value in (("store", True), ("background", True), ("truncation", "auto"),
                           ("previous_response_id", "resp_old"), ("conversation", {"id": "conv_1"})):
            with self.subTest(field=key), workflow.workflow(PROVIDER) as (state, session):
                data = response(function())
                data[key] = value
                self.rejected(session, state, self.finish(session, data))

    def test_flat_read_catalog_consent_and_circle_remain_proposal_only(self):
        expected = {"narwhal_application_info", "narwhal_document_summary", "narwhal_selection_summary",
                    "narwhal_object_summary", "narwhal_objects", "narwhal_layers", "narwhal_workspace",
                    "narwhal_components", "narwhal_operations", "narwhal_propose_circle", "narwhal_create_box"}
        with workflow.workflow(PROVIDER) as (_state, session):
            prepared = session.prepare(DOC, user_text="Discover operations.")
            tools = prepared.data["body"]["tools"]
            self.assertEqual({tool["name"] for tool in tools}, expected)
            for tool in tools:
                self.assertEqual(tool["type"], "function")
                self.assertIs(tool["strict"], False)
                self.assertEqual(tool["parameters"]["type"], "object")
                self.assertNotIn("function", tuple(tool))
            outcome = session.finish(workflow.send(prepared, response(function(
                name="narwhal_operations"))), DOC)
            self.assertEqual(outcome["status"], "tool_calls")
            read = session.read_tool_result(outcome["token"], "call_1", None, DOC)
            self.assertFalse(read["is_error"])
            circle = next(op for op in read["output"]["operations"] if op["command"] == "Circle")
            self.assertTrue(circle["proposal_available"])
            self.assertFalse(circle["execution_available"])
            session.record_results(outcome["token"], [read], DOC)
            prepared = session.prepare(DOC, continuation=True)
            data = response(function("proposal", "fc_proposal", "narwhal_propose_circle",
                                     '{"center":[2,3,4],"radius":7}'))
            outcome = session.finish(workflow.send(prepared, data), DOC)
            self.assertEqual(outcome["status"], "tool_calls")
            with self.assertRaisesRegex(ai.WorkflowUnavailable, "proposal_requires_review"):
                session.read_tool_result(outcome["token"], "proposal", None, DOC)
            proposal = session.propose_circle(outcome["token"], "proposal", DOC)
            self.assertFalse(proposal["dispatch_enabled"])
            self.assertIsNone(proposal["proposal"]["expected"])
            with self.assertRaisesRegex(ai.WorkflowUnavailable, "authoritative_preconditions_required"):
                session.review_proposal(proposal["proposal"], DOC, accepted=True)
            with self.assertRaises(ai.WorkflowUnavailable):
                session._exchange({"op": "execute", "command": "Circle"})
        for allow, selection in ((False, True), (True, False)):
            with self.subTest(allow=allow, selection=selection), workflow.workflow(PROVIDER, allow=allow) as (state, session):
                state.include_selection = selection
                prepared = session.prepare(DOC, user_text="Inspect.")
                self.assert_no_tools(prepared.data["body"])
                self.rejected(session, state, session.finish(workflow.send(prepared, response(function())), DOC),
                              "tools_not_authorized")
        with workflow.workflow(PROVIDER) as (state, session):
            self.rejected(session, state, self.finish(session, response(function(name="shell"))),
                          "unsupported_tool")

    def test_four_calls_per_reply_and_eight_total_actions(self):
        with workflow.workflow(PROVIDER) as (state, session):
            data = response(*(function(f"call_{i}", f"fc_{i}") for i in range(5)))
            self.rejected(session, state, self.finish(session, data), "tool_call_limit")
        with workflow.workflow(PROVIDER) as (state, session):
            for turn in range(2):
                calls = [function(f"call_{turn}_{i}", f"fc_{turn}_{i}") for i in range(4)]
                outcome = self.finish(session, response(*calls))
                self.assertEqual(outcome["status"], "tool_calls")
                self.assertEqual(len(outcome["response"]["tool_calls"]), 4)
                session.record_results(outcome["token"], [
                    {"call_id": f"call_{turn}_{i}", "output": {}, "is_error": False}
                    for i in range(4)], DOC)
            self.rejected(session, state, self.finish(session, response(function("ninth", "fc_ninth"))),
                          "action_budget")

    def test_turn_and_total_token_budgets_and_4096_request_cap(self):
        with workflow.workflow(PROVIDER) as (_state, session):
            with self.assertRaises(ai.WorkflowUnavailable):
                session.prepare(DOC, user_text="Ask.", max_tokens=4097)
            for _ in range(8):
                data = response(message())
                del data["usage"]
                self.assertEqual(self.finish(session, data)["status"], "completed")
            with self.assertRaisesRegex(ai.WorkflowUnavailable, "turn_budget"):
                session.prepare(DOC, user_text="Ninth.")
        with workflow.workflow(PROVIDER) as (_state, session):
            for turn in range(5):
                data = response(message())
                data["usage"]["output_tokens"] = 4096
                outcome = self.finish(session, data, max_tokens=4096)
                self.assertEqual(outcome["status"], "completed" if turn < 4 else "failed")
            self.assertEqual(outcome["code"], "token_budget")

    def test_opaque_replay_counts_toward_individual_and_total_output_bytes(self):
        with workflow.workflow(PROVIDER) as (state, session):
            # UTF-8 exceeds 32 KiB although character count and visible text do not.
            data = response(reasoning("é" * 17000), function())
            self.rejected(session, state, self.finish(session, data), "normalized_output_limit")
        with workflow.workflow(PROVIDER) as (_state, session):
            for turn in range(3):
                data = response(reasoning("x" * 24000, f"rs_{turn}"), message("ok", f"msg_{turn}"))
                del data["usage"]
                outcome = self.finish(session, data)
                self.assertEqual(outcome["status"], "completed" if turn < 2 else "failed")
            self.assertEqual(outcome["code"], "output_budget")

    def test_raw_replay_and_user_history_share_64k_context_budget(self):
        with workflow.workflow(PROVIDER) as (_state, session):
            for turn in range(3):
                prepared = session.prepare(DOC, user_text="u" * 7000)
                data = response(reasoning("x" * 21000, f"rs_{turn}"), message("ok", f"msg_{turn}"))
                data["usage"] = None
                outcome = session.finish(workflow.send(prepared, data), DOC)
                self.assertEqual(outcome["status"], "completed" if turn < 2 else "failed")
            # Replay alone is below 64 KiB; user history pushes only context over.
            self.assertEqual(outcome["code"], "context_budget")

    def test_cancellation_and_late_http_are_logical_not_physical(self):
        with local_http([response(function(), message("Late."))], hold=True) as wire:
            endpoint, seen, errors, arrived, release, send = wire
            with workflow.workflow(PROVIDER) as (state, session):
                state.provider = replace(state.provider, endpoint=endpoint)
                prepared = session.prepare(DOC, user_text="Old.")
                receipts = queue.Queue()
                worker = threading.Thread(target=lambda: receipts.put(send(prepared)), daemon=True)
                worker.start()
                try:
                    self.assertTrue(arrived.wait(3), "loopback request did not arrive")
                    cancelled = session.invalidate()
                    self.assertFalse(cancelled["physical_http_cancelled"])
                    self.assertTrue(prepared.cancel_event.is_set())
                    self.assertTrue(worker.is_alive(), "HTTP was not held by fixture")
                finally:
                    release.set()
                    worker.join(6)
                self.assertFalse(worker.is_alive(), "transport did not finish")
                receipt = receipts.get(timeout=1)
                self.assertEqual(receipt.failure, "cancelled")
                self.assertFalse(receipt.physical_http_cancelled)
                self.assertEqual(session.finish(receipt, DOC)["status"], "stale")
                self.assertFalse(any(m.role == "assistant" for m in state.messages))
                cancelled_receipt = transport.send_turn(prepared, {},
                    opener=lambda *_a, **_k: self.fail("cancelled request opened HTTP"))
                self.assertEqual(cancelled_receipt.failure, "cancelled")
            self.assertEqual(len(seen), 1)
            self.assertEqual(errors, [])
        with workflow.workflow(PROVIDER) as (_state, session):
            prepared = session.prepare(DOC, user_text="Timeout.", timeout_ms=1)
            time.sleep(.01)
            self.assertEqual(session.poll_timeout()["status"], "timeout")
            receipt = transport.TurnTransportResult(prepared.data["token"],
                                                    raw=json.dumps(response(function())))
            self.assertEqual(session.finish(receipt, DOC)["status"], "stale")

    def test_new_chat_provider_model_endpoint_and_document_detach_old_receipts(self):
        for change in ("new_chat", "provider", "model", "endpoint", "document"):
            with self.subTest(change=change), workflow.workflow(PROVIDER) as (state, session):
                old = session.prepare(DOC, user_text="Old.")
                receipt = workflow.send(old, response(reasoning(), function(), message("Late.")))
                document = "document-B" if change == "document" else DOC
                if change == "new_chat":
                    state.new_chat()
                    self.assertFalse(state.messages)
                elif change == "provider":
                    state.provider = ai.set_provider("openai", "fixture-model", credential_env="TEST_KEY")
                elif change == "model":
                    state.provider = replace(state.provider, model="different-model")
                elif change == "endpoint":
                    state.provider = replace(state.provider, endpoint="http://127.0.0.1:9999/v1")
                self.assertEqual(session.finish(receipt, document)["status"], "stale")
                self.assertNotIn("Late.", [m.text for m in state.messages])
                fresh = session.prepare(document, user_text="Fresh.")
                body = fresh.data["body"]
                self.assert_hidden("opaque-test-state", json.dumps(body))
                self.assert_hidden("fc_item", json.dumps(body))
                if change == "document":
                    self.assert_no_tools(body)
                    self.assertFalse(fresh.data["binding"]["allow_reads"])
                self.assertEqual(session.finish(receipt, document)["status"], "stale")
                self.assertEqual(state.status, "sending")
                data = (workflow.payload("openai", "Fresh answer.") if change == "provider"
                        else response(message("Fresh answer.")))
                self.assertEqual(session.finish(workflow.send(fresh, data), document)["status"], "completed")
                self.assertEqual(state.messages[-1].text, "Fresh answer.")

    def test_credentials_and_provider_error_bodies_never_enter_repr_or_errors(self):
        data = {"error": {"code": "invalid_api_key", "message": "rejected " + KEY}}
        with local_http([data], status=401) as wire:
            endpoint, seen, errors, _arrived, _release, send = wire
            with workflow.workflow(PROVIDER) as (state, session):
                state.provider = replace(state.provider, endpoint=endpoint)
                prepared = session.prepare(DOC, user_text="Ask.")
                receipt = send(prepared)
                self.assertEqual(receipt.failure, "http_error")
                self.assertEqual(receipt.http_status, 401)
                self.assertIsNone(receipt.raw)
                outcome = session.finish(receipt, DOC)
                self.assertEqual(outcome["status"], "transport_failed")
                self.assert_hidden(KEY, repr(prepared) + repr(receipt) + repr(state) + repr(outcome))
                self.assertFalse(any(m.role == "assistant" for m in state.messages))
            self.assertTrue(seen[0]["bearer_ok"])
            self.assertEqual(errors, [])
        with workflow.workflow(PROVIDER) as (state, session):
            outcome = self.finish(session, dict(response(function()), error=data["error"]))
            self.rejected(session, state, outcome)
            self.assert_hidden(KEY, repr(state) + repr(outcome))
            prepared = session.prepare(DOC, user_text="Missing key.")
            opened = []
            def forbidden_open(*_args, **_kwargs):
                opened.append(True)
                raise RuntimeError("fixture forbids this request")
            receipt = transport.send_turn(prepared, {}, opener=forbidden_open)
            self.assertFalse(opened, "missing credential reached HTTP opener")
            self.assertIsNotNone(receipt.failure)
            self.assert_hidden(KEY, repr(receipt) + repr(prepared))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", required=True, type=Path)
    parser.add_argument("--test", choices=unittest.defaultTestLoader.getTestCaseNames(ResponsesTests))
    args = parser.parse_args()
    workflow.LIBRARY = args.library.resolve()
    suite = (unittest.TestSuite([ResponsesTests(args.test)]) if args.test else
             unittest.defaultTestLoader.loadTestsFromTestCase(ResponsesTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
