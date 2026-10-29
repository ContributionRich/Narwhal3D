#!/usr/bin/env python3
"""Execute actual UI orchestration functions with real native policy and local HTTP.

Blender timer/dialog registration is stubbed; this is not a visible GUI receipt.
No installed binary changes, external providers, document uploads or CAD writes.
"""
import argparse
import ast
from contextlib import contextmanager
import importlib
import json
import os
from pathlib import Path
import queue
import threading
import time
import types
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import test_ai_workflow as base

ai, transport, ROOT = base.ai, base.http, base.ROOT
LIBRARY = None
SOURCE = ROOT / "narwhal3d/ui.py"
MUTATION = None
MUTATIONS = (
    ("config_snapshot", 'replace(st.provider)', 'st.provider', "test_in_place_configuration_change_revokes_grant"),
    ("consent_snapshot", 'replace(st.provider)', 'st.provider', "test_in_place_configuration_cannot_retarget_open_consent"),
    ("consent", 'if st.include_selection and _ai.get("read_grant") != scope:', 'if False:', "test_explicit_consent_and_selection_off"),
    ("consent_scope", 'getattr(self, "_scope", None) != scope', 'False', "test_old_consent_cannot_authorize_new_document"),
    ("document_identity", 'any(a is not b for a, b in zip(old, identity))', 'False', "test_idle_document_replacement_revokes_reads"),
    ("config_commit", '_ai_invalidate("configuration_changed")\n    st.provider', '# mutation\n    st.provider', "test_settings_commit_closes_native_immediately"),
    ("poll_timeout", 'workflow.poll_timeout()["status"] == "timeout"', 'False', "test_logical_timeout_without_http_return"),
    ("finish_gate", 'outcome = workflow.finish(receipt, scope[0])', 'st.add_message("assistant", "UNSAFE"); outcome = workflow.finish(receipt, scope[0])', "test_stale_receipt_cannot_publish_or_finish_new_request"),
    ("new_chat", '_ai_invalidate("new_chat")', '# mutation', "test_new_chat_revokes_consent_and_closes_session"),
    ("toggle", '_ai_invalidate("selection_toggle_changed")', '# mutation', "test_toggle_immediately_cancels_worker_and_revokes"),
    ("unregister", '_ai_invalidate("unregister")', '# mutation', "test_shutdown_closes_handle_and_detaches_timers"),
    ("read_loop", '_ai_read_results(workflow, outcome, scope[0])', '# mutation', "test_shared_read_loop_and_proposals_do_not_modify_document"),
    ("proposal_truth", '"Proposal only — not executed: "', '"Executed: "', "test_shared_read_loop_and_proposals_do_not_modify_document"),
    ("timer_cleanup", 'bpy.app.timers.unregister(callback)', 'pass', "test_shutdown_closes_handle_and_detaches_timers"),
    ("cleanup_after_error", 'for key in ("transport_output", "transport_thread", "read_grant", "consent_scope"):', 'for key in ():', "test_native_close_error_still_detaches_grants_and_queue"),
)


class Timers:
    def __init__(self):
        self.callbacks = set()
    def is_registered(self, callback):
        return callback in self.callbacks
    def register(self, callback, **_kwargs):
        self.callbacks.add(callback)
    def unregister(self, callback):
        self.callbacks.remove(callback)


@contextmanager
def provider(replies):
    received, errors = [], []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_POST(self):
            try:
                length = int(self.headers["Content-Length"])
                if length > 262144:
                    raise ValueError("request bound")
                received.append(json.loads(self.rfile.read(length)))
                data = json.dumps(replies[len(received)-1]).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as exc:
                errors.append(type(exc).__name__)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", received, errors
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


class UIWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.maxDiff = 1200
        self.st = ai.AssistantState(provider=ai.set_provider("openai_compatible", "fixture"))
        self.model = types.SimpleNamespace(file=object())
        self.app = types.SimpleNamespace(model=self.model)
        self.timers = Timers()
        self.dialogs = []
        bpy = types.SimpleNamespace(types=types.SimpleNamespace(Operator=object),
            app=types.SimpleNamespace(timers=self.timers, background=False),
            ops=types.SimpleNamespace(narwhal=types.SimpleNamespace(ai_read_consent=lambda *_: self.dialogs.append(True))))
        self.g = dict(__name__="narwhal3d.ui", __package__="narwhal3d", ai_assistant=ai,
                      ai_transport=transport, os=os, bpy=bpy, app_state=self.app,
                      _ai={"state": self.st}, _tag_redraw_bands=lambda: None,
                      _preferences_ready=False, ai_panel_open=lambda: True,
                      _remember_preferences=lambda _values: None)
        text = SOURCE.read_text()
        if MUTATION:
            _label, old, new, _test = MUTATION
            self.assertEqual(text.count(old), 1, "mutation anchor")
            text = text.replace(old, new, 1)
        names = {"_ai_invalidate", "_ai_scope", "NARWHAL_OT_ai_read_consent", "_ai_transport_worker",
                 "_ai_start_turn", "_ai_read_results", "_ai_poll_transport", "_ai_send", "_ai_shutdown",
                 "_ai_poll_model_discovery", "_apply_ai_settings", "ai_panel_click"}
        tree = ast.parse(text)
        nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
        self.assertEqual(len(nodes), len(names))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), self.g)
        self.original_init = ai.WorkflowSession.__init__
        def native(session, state, context, **kwargs):
            return self.original_init(session, state, context, library_path=LIBRARY, **kwargs)
        self.native_patch = patch.object(ai.WorkflowSession, "__init__", native)
        self.native_patch.start()
        self.addCleanup(self.native_patch.stop)
        self.addCleanup(self.close_native)
        self.addCleanup(self.g["_ai_shutdown"])

    def close_native(self):
        # Even a broken shutdown mutation must not exhaust the native handle table.
        workflow = self.st.extra.get("workflow")
        if workflow is not None:
            workflow.close()

    def session(self, reads=False):
        scope = self.g["_ai_scope"]()
        if reads:
            self.g["_ai"]["read_grant"] = scope
        return ai.WorkflowSession(self.st, scope[0], allow_reads=reads), scope[0]

    def click(self, kind, ident):
        self.g["_ai"].update(lay={}, region=(0, 0))
        with patch.object(ai, "panel_hit", return_value=(kind, ident)):
            self.g["ai_panel_click"](0, 0)

    def drive(self):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            self.g["_ai_poll_transport"]()
            if self.st.status != "sending":
                return
            time.sleep(.005)
        self.fail("bounded local workflow did not finish")

    def test_explicit_consent_and_selection_off(self):
        self.st.input_text = "hello"
        with patch.object(transport, "send_turn", side_effect=AssertionError("unexpected network")):
            self.assertIn("awaiting read consent", self.g["_ai_send"]())
        self.assertEqual(len(self.dialogs), 1)
        self.assertEqual(len(self.st.messages), 0)
        with provider([base.payload("openai_compatible", "local answer")]) as (endpoint, seen, errors):
            self.st.provider = ai.set_provider("openai_compatible", "fixture", endpoint)
            self.st.include_selection = False
            self.g["_ai_send"]()
            self.drive()
            self.assertEqual(self.st.status, "connected")
            self.assertEqual(len(seen), 1)
            self.assertFalse(seen[0].get("tools"))
            self.assertFalse(errors)
            self.assertIn("local answer", [m.text for m in self.st.messages])

    def test_consent_execute_requires_invoke_and_exact_scope(self):
        cls = self.g["NARWHAL_OT_ai_read_consent"]
        self.assertEqual(cls().execute(None), {"CANCELLED"})
        operator = cls()
        context = types.SimpleNamespace(window_manager=types.SimpleNamespace(invoke_props_dialog=lambda *_a, **_kw: {"RUNNING_MODAL"}))
        self.assertEqual(operator.invoke(context, None), {"RUNNING_MODAL"})
        labels = []
        operator.layout = types.SimpleNamespace(label=lambda **kw: labels.append(kw["text"]))
        operator.draw(context)
        self.assertTrue(any("unselected" in s for s in labels))
        self.assertTrue(any("8 model turns" in s for s in labels))
        with patch.dict(self.g, _ai_send=lambda: None):
            self.assertEqual(operator.execute(context), {"FINISHED"})
        self.assertEqual(self.g["_ai"]["read_grant"], self.g["_ai_scope"]())

    def test_old_consent_cannot_authorize_new_document(self):
        cls = self.g["NARWHAL_OT_ai_read_consent"]
        operator = cls()
        ctx = types.SimpleNamespace(window_manager=types.SimpleNamespace(invoke_props_dialog=lambda *_a, **_kw: None))
        operator.invoke(ctx, None)
        self.model.file = object()
        fresh = cls()
        fresh.invoke(ctx, None)
        self.assertEqual(operator.execute(ctx), {"CANCELLED"})
        self.assertNotIn("read_grant", self.g["_ai"])

    def test_idle_document_replacement_revokes_reads(self):
        session, _ = self.session(True)
        self.model.file = object()
        self.g["_ai_poll_transport"]()
        self.assertEqual(session._handle, 0)
        self.assertNotIn("read_grant", self.g["_ai"])

    def test_settings_commit_closes_native_immediately(self):
        session, context = self.session(True)
        ready = session.prepare(context, user_text="old")
        self.assertIsNone(self.g["_apply_ai_settings"](ai.set_provider("openai_compatible", "changed")))
        self.assertTrue(ready.cancel_event.is_set())
        self.assertEqual(session._handle, 0)
        self.assertNotIn("read_grant", self.g["_ai"])

    def test_failed_settings_save_preserves_live_workflow(self):
        session, _ = self.session(True)
        self.g.update(_preferences_ready=True, preferences=types.SimpleNamespace(update=lambda _v: (_ for _ in ()).throw(OSError())))
        self.assertIsNotNone(self.g["_apply_ai_settings"](ai.set_provider("openai_compatible", "changed")))
        self.assertNotEqual(session._handle, 0)
        self.assertEqual(self.st.provider.model, "fixture")

    def test_logical_timeout_without_http_return(self):
        session, context = self.session()
        ready = session.prepare(context, user_text="wait", timeout_ms=1)
        self.g["_ai"]["transport_output"] = queue.Queue(maxsize=1)
        time.sleep(.01)
        self.g["_ai_poll_transport"]()
        self.assertEqual(self.st.error, "timeout")
        self.assertTrue(ready.cancel_event.is_set())
        self.assertNotIn("transport_output", self.g["_ai"])

    def test_stale_receipt_cannot_publish_or_finish_new_request(self):
        session, context = self.session()
        old = session.prepare(context, user_text="old")
        receipt = base.send(old, base.payload("openai_compatible", "OLD ANSWER"))
        self.st.new_chat()
        fresh = session.prepare(context, user_text="fresh")
        output = queue.Queue(maxsize=1)
        output.put(receipt)
        self.g["_ai"]["transport_output"] = output
        self.g["_ai_poll_transport"]()
        self.assertEqual([m.text for m in self.st.messages], ["fresh"])
        self.assertEqual(session.finish(base.send(fresh, base.payload("openai_compatible", "FRESH")), context)["status"], "completed")

    def test_new_chat_revokes_consent_and_closes_session(self):
        session, context = self.session(True)
        ready = session.prepare(context, user_text="old")
        self.click("button", "new")
        self.assertEqual(session._handle, 0)
        self.assertTrue(ready.cancel_event.is_set())
        self.assertNotIn("read_grant", self.g["_ai"])
        self.assertFalse(self.st.messages)

    def test_toggle_immediately_cancels_worker_and_revokes(self):
        session, context = self.session(True)
        ready = session.prepare(context, user_text="old")
        self.click("context", "selection")
        self.assertFalse(self.st.include_selection)
        self.assertEqual(session._handle, 0)
        self.assertTrue(ready.cancel_event.is_set())
        self.assertNotIn("read_grant", self.g["_ai"])

    def test_shutdown_closes_handle_and_detaches_timers(self):
        session, context = self.session(True)
        ready = session.prepare(context, user_text="old")
        for name in ("_ai_poll_transport", "_ai_poll_model_discovery"):
            self.timers.register(self.g[name])
        self.g["_ai_shutdown"]()
        self.assertEqual(session._handle, 0)
        self.assertTrue(ready.cancel_event.is_set())
        self.assertFalse(self.timers.callbacks)
        self.assertFalse(self.g["_ai"].get("document_identity"))
        tree = ast.parse(SOURCE.read_text())
        shutdown = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "unregister")
        self.assertIn("_ai_shutdown()", ast.unparse(shutdown))
        classes = next(n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CLASSES" for t in n.targets))
        self.assertIn("NARWHAL_OT_ai_read_consent", ast.unparse(classes))

    def test_native_close_error_still_detaches_grants_and_queue(self):
        session, context = self.session(True)
        ready = session.prepare(context, user_text="old")
        self.g["_ai"]["transport_output"] = queue.Queue(maxsize=1)
        close = session.close
        def failed_close():
            close()
            raise ai.WorkflowUnavailable("test close failure")
        with patch.object(session, "close", failed_close):
            self.g["_ai_shutdown"]()
        self.assertEqual(session._handle, 0)
        self.assertTrue(ready.cancel_event.is_set())
        self.assertNotIn("read_grant", self.g["_ai"])
        self.assertNotIn("transport_output", self.g["_ai"])

    def test_shared_read_loop_and_proposals_do_not_modify_document(self):
        import rhino3dm as r3
        model = importlib.import_module("narwhal3d.model").NarwhalModel()
        oid, _ = model.add_object(r3.Circle(r3.Point3d(3, 4, 0), 2).ToNurbsCurve(), "Disposable circle")
        self.app.model = model
        self.app.active_command = None
        self.app.doc_title = lambda: "Disposable"
        self.app.doc_modified = lambda: False
        self.app.ctx = types.SimpleNamespace(selected_objects=lambda: [])
        before = model.file.Encode()
        replies = [base.payload("openai_compatible", "", [("read", "narwhal_objects", {"offset": 0, "limit": 1})]),
                   base.payload("openai_compatible", "", [("propose", "narwhal_propose_circle", {"center": [2, 3, 4], "radius": 7})]),
                   base.payload("openai_compatible", "Here is a proposal.")]
        with provider(replies) as (endpoint, seen, errors):
            self.st.provider = ai.set_provider("openai_compatible", "fixture", endpoint)
            self.session(True)
            self.st.input_text = "Inspect the disposable fixture and propose a circle"
            self.g["_ai_send"]()
            self.drive()
            self.assertEqual(self.st.status, "connected", self.st.error)
            self.assertEqual(len(seen), 3)
            result = json.loads(seen[1]["messages"][-1]["content"])
            self.assertEqual(result["output"]["objects"][0]["object_id"], oid)
            blocked = json.loads(seen[2]["messages"][-1]["content"])
            self.assertFalse(blocked["output"]["dispatch_enabled"])
            self.assertTrue(blocked["is_error"])
            self.assertTrue(any("Proposal only — not executed" in m.text for m in self.st.messages))
            self.assertFalse(errors)
        self.assertTrue(model.file.Encode() == before, "document changed")
        self.assertEqual(model.file.Objects[0].Geometry.Degree, 2)

    def test_read_loop_stops_at_native_turn_budget(self):
        replies = [base.payload("openai_compatible", "", [(f"read{i}", "narwhal_operations", {})]) for i in range(8)]
        with provider(replies) as (endpoint, seen, errors):
            self.st.provider = ai.set_provider("openai_compatible", "fixture", endpoint)
            self.session(True)
            self.st.input_text = "inspect operations"
            self.g["_ai_send"]()
            self.drive()
            self.assertEqual(len(seen), 8)
            self.assertFalse(errors)
            self.assertEqual(self.st.status, "error")
            self.assertNotIn("workflow", self.st.extra)
            self.assertNotIn("read_grant", self.g["_ai"])

    def test_unconsented_tool_reply_never_reads_document(self):
        reply = base.payload("openai_compatible", "", [("read", "narwhal_document_summary", {})])
        with provider([reply]) as (endpoint, seen, errors):
            self.st.provider = ai.set_provider("openai_compatible", "fixture", endpoint)
            self.st.include_selection = False
            self.st.input_text = "text only"
            with patch.dict(self.g, _ai_read_results=lambda *_: self.fail("unconsented read")):
                self.g["_ai_send"]()
                self.drive()
            self.assertEqual(len(seen), 1)
            self.assertFalse(errors)
            self.assertEqual(self.st.error, "tools_not_authorized")

    def test_old_worker_return_after_new_chat_is_detached(self):
        started, release = threading.Event(), threading.Event()
        workers = []
        def delayed(prepared, _environ):
            started.set()
            release.wait(3)
            return transport.TurnTransportResult(prepared.data["token"], raw=json.dumps(base.payload("openai_compatible", "late")))
        self.st.include_selection = False
        self.st.input_text = "old"
        try:
            with patch.object(transport, "send_turn", delayed):
                self.g["_ai_send"]()
                workers.append(self.g["_ai"]["transport_thread"])
                self.assertTrue(started.wait(1))
                self.click("button", "new")
                release.set()
                workers[0].join(2)
                self.g["_ai_poll_transport"]()
                self.assertFalse(self.st.messages)
                self.assertNotIn("transport_output", self.g["_ai"])
        finally:
            release.set()
            for worker in workers:
                worker.join(2)

    def test_in_place_configuration_change_revokes_grant(self):
        from dataclasses import FrozenInstanceError
        session, context = self.session(True)
        prepared = session.prepare(context, user_text="old")
        original = self.st.provider
        # Normal assignment is blocked, but frozen dataclasses still expose __dict__.
        with self.assertRaises(FrozenInstanceError):
            original.model = "changed"
        original.__dict__["model"] = "changed"
        self.assertIs(self.st.provider, original)
        self.g["_ai_scope"]()
        self.assertEqual(session._handle, 0)
        self.assertTrue(prepared.cancel_event.is_set())
        self.assertNotIn("read_grant", self.g["_ai"])

    def test_in_place_configuration_cannot_retarget_open_consent(self):
        operator = self.g["NARWHAL_OT_ai_read_consent"]()
        ctx = types.SimpleNamespace(window_manager=types.SimpleNamespace(invoke_props_dialog=lambda *_a, **_kw: None))
        operator.invoke(ctx, None)
        self.st.provider.__dict__["endpoint"] = "http://127.0.0.1:54321/v1"
        self.assertEqual(operator.execute(ctx), {"CANCELLED"})
        self.assertNotIn("read_grant", self.g["_ai"])

    def test_programmatic_configuration_change_revokes_before_receipt(self):
        session, context = self.session(True)
        prepared = session.prepare(context, user_text="old")
        output = queue.Queue(maxsize=1)
        output.put(base.send(prepared, base.payload("openai_compatible", "old answer")))
        self.g["_ai"]["transport_output"] = output
        self.st.provider = ai.set_provider("openai_compatible", "replacement")
        self.g["_ai_poll_transport"]()
        self.assertEqual(session._handle, 0)
        self.assertNotIn("old answer", [m.text for m in self.st.messages])
        self.assertNotIn("read_grant", self.g["_ai"])

    def test_ui_and_reader_are_main_thread_only(self):
        errors = []
        def run():
            try:
                self.g["_ai_scope"]()
            except ai.WorkflowUnavailable as exc:
                errors.append(str(exc))
        thread = threading.Thread(target=run)
        thread.start(); thread.join(2)
        self.assertEqual(errors, ["workflow_requires_main_thread"])

    def test_native_unavailable_never_falls_back_to_send_chat(self):
        self.st.include_selection = False
        self.st.input_text = "hello"
        with patch.object(ai.WorkflowSession, "__init__", side_effect=ai.WorkflowUnavailable("test missing library")), patch.object(transport, "send_chat", side_effect=AssertionError("legacy transport")):
            self.g["_ai_send"]()
        self.assertEqual(self.st.status, "error")
        self.assertFalse(self.st.messages)

    def test_resource_profile_is_capped_single_job_and_headroom_checked(self):
        import test_automation_native as driver
        for cap in (1024, 1536):
            with patch.dict(os.environ, NARWHAL_AI_BUILD_MIB=str(cap)), patch.object(Path, "read_text", return_value=f"MemAvailable: {(4096 + cap) * 1024} kB\n"):
                command = driver.compile_command(driver.CRATE, Path("/tmp/test-only"), "build")
                self.assertIn(f"MemoryMax={cap}M", command)
                self.assertIn("MemorySwapMax=0", command)
                self.assertEqual(command[command.index("-j")+1], "1")
                self.assertIn("170s", command)
            with patch.dict(os.environ, NARWHAL_AI_BUILD_MIB=str(cap)), patch.object(Path, "read_text", return_value=f"MemAvailable: {(4096 + cap) * 1024 - 1} kB\n"):
                with self.assertRaises(AssertionError):
                    driver.compile_command(driver.CRATE, Path("/tmp/test-only"), "build")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", required=True, type=Path)
    parser.add_argument("--mutations", action="store_true")
    args = parser.parse_args()
    LIBRARY = args.library.resolve()
    def run(test=None):
        suite = unittest.TestSuite([UIWorkflowTests(test)]) if test else unittest.defaultTestLoader.loadTestsFromTestCase(UIWorkflowTests)
        return unittest.TextTestRunner(verbosity=2).run(suite)
    if not run().wasSuccessful():
        raise SystemExit(1)
    if args.mutations:
        for mutation in MUTATIONS:
            MUTATION = mutation
            result = run(mutation[3])
            if result.errors or not result.failures:
                raise AssertionError("MUTATION did not fail its intended assertion: " + mutation[0])
            print("RED confirmed:", mutation[0], flush=True)
        MUTATION = None
        import inspect
        import test_automation_native as driver
        resource_mutations = (
            ("memory_cap", 'f"MemoryMax={cap}M"', '"MemoryMax=infinity"'),
            ("swap_cap", '"MemorySwapMax=0"', '"MemorySwapMax=infinity"'),
            ("one_job", '"-j", "1"', '"-j", "2"'),
            ("headroom", 'assert memory >= (4096 + cap) * 1024', 'assert True'),
        )
        source = inspect.getsource(driver.compile_command)
        for label, old, new in resource_mutations:
            assert source.count(old) == 1
            namespace = dict(driver.__dict__)
            exec(source.replace(old, new, 1), namespace)
            with patch.object(driver, "compile_command", namespace["compile_command"]):
                result = run("test_resource_profile_is_capped_single_job_and_headroom_checked")
                if result.errors or not result.failures:
                    raise AssertionError("resource mutation survived: " + label)
            print("RED confirmed: resource", label, flush=True)
        if not run().wasSuccessful():
            raise SystemExit(1)
    print("UI workflow GREEN; mutations:", len(MUTATIONS) + 4 if args.mutations else 0)
