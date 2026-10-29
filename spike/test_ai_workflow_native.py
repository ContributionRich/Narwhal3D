#!/usr/bin/env python3
"""Bounded native workflow validation/mutations; sources and binaries stay isolated."""
import argparse
import fcntl
from pathlib import Path
import shutil
import sys
import tempfile

from test_automation_native import ROOT, CRATE, compile_crate, checked

MUTATIONS = (
    ("conversation_correlation", "conversation.rs", "if pending.token != token", "if false && pending.token != token", "test_new_chat_cannot_receive_old_answer_or_call"),
    ("binding", "conversation.rs", "if binding != self.binding {\n            self.cancel();", "if false && binding != self.binding {\n            self.cancel();", "test_changed_config_or_document_invalidates_old_response"),
    ("deadline", "conversation.rs", "if Instant::now() >= pending.deadline", "if false && Instant::now() >= pending.deadline", "test_cancel_timeout_and_replay_have_explicit_outcomes"),
    ("result_correlation", "conversation.rs", "r.call_id != c.id", "false", "test_tool_only_multiple_calls_and_result_round_trip"),
    ("read_consent", "conversation.rs", 'return Err("tools_not_authorized");', '/* mutation: omit read consent */', "test_no_read_consent_or_selection_toggle_means_no_tools"),
    ("output_tokens", "provider.rs", "if n > cap", "if false && n > cap", "test_unknown_json_arguments_and_output_tokens_are_refused"),
    ("truncation", "provider.rs", '\"length\" => return Err("truncated_response"),', '\"length\" => false,', "test_budgets_truncation_malformed_and_unsupported_calls"),
    ("turns", "conversation.rs", "if self.turns >= MAX_TURNS", "if false && self.turns >= MAX_TURNS", "test_turn_action_and_total_token_budgets"),
    ("tool_call_replay", "conversation.rs", "if self.used_call_ids.contains(&call.id)", "if false && self.used_call_ids.contains(&call.id)", "test_completed_tool_call_id_cannot_be_replayed"),
    ("old_callback_binding", "conversation.rs", "        call_id: &str,\n    ) -> Result<Value, &'static str> {", "        call_id: &str,\n    ) -> Result<Value, &'static str> {\n        if binding != self.binding { self.rebind(binding.clone())?; }", "test_old_tool_callbacks_do_not_reset_new_pending_context"),
    ("logical_timeout", "conversation.rs", ".is_some_and(|pending| Instant::now() >= pending.deadline)", ".is_some_and(|_pending| false)", "test_poll_expires_logical_request_without_waiting_for_http"),
)


def run(target, mutate):
    assert not target.is_relative_to(ROOT)
    with open("/tmp/narwhal3d-heavy-build.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        compile_crate(CRATE, target, "test")
        compile_crate(CRATE, target, "build")
        client = [sys.executable, "-B", str(ROOT / "spike/test_ai_workflow.py"), "--library", str(target / "debug/libnarwhal_automation.so")]
        checked(client, timeout=30)
        if mutate:
            mutant_target = target / "workflow-mutations"
            mutant_client = client[:-1] + [str(mutant_target / "debug/libnarwhal_automation.so")]
            for label, file, old, new, test in MUTATIONS:
                with tempfile.TemporaryDirectory(prefix="narwhal-workflow-mutation-") as tmp:
                    crate = Path(tmp)
                    shutil.copytree(CRATE / "src", crate / "src")
                    for name in ("Cargo.toml", "Cargo.lock"):
                        shutil.copy2(CRATE / name, crate / name)
                    source = crate / "src" / file
                    text = source.read_text()
                    assert text.count(old) == 1, (label, "mutation anchor changed")
                    source.write_text(text.replace(old, new, 1))
                    compile_crate(crate, mutant_target, "build")
                    checked(mutant_client + ["--test", test], timeout=30, expect_failure=True)
                    print("RED confirmed:", label, flush=True)
            for label, change, test in (
                ("new_chat_hook", "t.ai.WorkflowSession.invalidate=lambda self,reason='cancelled': {'status':'cancelled'}", "test_new_chat_cannot_receive_old_answer_or_call"),
                ("main_thread_hook", "t.ai.WorkflowSession._main_thread=staticmethod(lambda: None)", "test_prose_is_not_a_tool_and_main_thread_is_enforced"),
            ):
                code = (f"import sys,unittest; from pathlib import Path; sys.path.insert(0,{str(ROOT / 'spike')!r}); "
                        f"import test_ai_workflow as t; t.LIBRARY=Path({str(target / 'debug/libnarwhal_automation.so')!r}); "
                        f"{change}; r=unittest.TextTestRunner().run(unittest.TestSuite([t.WorkflowTests({test!r})])); sys.exit(not r.wasSuccessful())")
                checked([sys.executable, "-B", "-c", code], timeout=30, expect_failure=True)
                print("RED confirmed:", label, flush=True)
            for method, old, new, test in (
                ("_exchange", "ensure_ascii=False", "ensure_ascii=True", "test_unicode_envelope_and_oversized_completion_retire_request"),
                ("finish", 'completion.update(raw=None, failure="response_too_large")', 'raise', "test_unicode_envelope_and_oversized_completion_retire_request"),
                ("prepare", "self._allow_reads = False", "self._allow_reads = True", "test_document_change_does_not_transfer_read_grant"),
            ):
                code = (f"import sys,unittest,inspect,textwrap; from pathlib import Path; sys.path.insert(0,{str(ROOT / 'spike')!r}); "
                        f"import test_ai_workflow as t; t.LIBRARY=Path({str(target / 'debug/libnarwhal_automation.so')!r}); "
                        f"s=textwrap.dedent(inspect.getsource(getattr(t.ai.WorkflowSession,{method!r}))); assert s.count({old!r})==1; "
                        f"g=dict(t.ai.__dict__); exec(s.replace({old!r},{new!r},1),g); setattr(t.ai.WorkflowSession,{method!r},g[{method!r}]); "
                        f"r=unittest.TextTestRunner().run(unittest.TestSuite([t.WorkflowTests({test!r})])); sys.exit(not r.wasSuccessful())")
                checked([sys.executable, "-B", "-c", code], timeout=30, expect_failure=True)
                print("RED confirmed: FFI", method, flush=True)
            checked(client, timeout=30)
        print("Workflow native: GREEN; isolated mutations", len(MUTATIONS) + 5 if mutate else 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-dir", required=True, type=Path)
    parser.add_argument("--mutations", action="store_true")
    args = parser.parse_args()
    run(args.target_dir.resolve(), args.mutations)
