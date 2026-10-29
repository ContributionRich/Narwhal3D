#!/usr/bin/env python3
"""Isolated proposal-gate mutations; no CAD executor, UI or installed-runtime edits."""
import argparse
import fcntl
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from test_automation_native import ROOT, CRATE, compile_crate, compile_command, checked

MUTATIONS = (
    ("wire_token_name", '#[serde(rename = "owner")]', '#[serde(rename = "request_token")]', "test_proposal_wire_token_name_is_compatible"),
    ("exact_approval", "if pending.proposal != proposal", "if false && pending.proposal != proposal", "test_exact_review_preconditions_replay_and_production_block"),
    ("approval_replay", "if pending.approved || pending.spent", "if false && (pending.approved || pending.spent)", "test_exact_review_preconditions_replay_and_production_block"),
    ("approval_required", "if !pending.approved", "if false && !pending.approved", "test_exact_review_preconditions_replay_and_production_block"),
    ("dispatch_preconditions", "if pending.proposal.expected.as_ref() != Some(current)", "if false && pending.proposal.expected.as_ref() != Some(current)", "test_exact_review_preconditions_replay_and_production_block"),
    ("production_disabled", '"status":"blocked","dispatch_enabled":false', '"status":"blocked","dispatch_enabled":true', "test_exact_review_preconditions_replay_and_production_block"),
    ("review_scope", "            request_token,\n            action:", "            request_token: crate::conversation::Token {conversation:0,generation:0,request:0},\n            action:", "test_reject_default_busy_locked_and_cross_session_review"),
    ("positive_radius", "self.radius <= 0.0", "false", "test_invalid_circle_and_extra_operation_refused"),
    ("proposal_call_replay", "if self.staged_proposal_calls.contains(&call_id)", "if false && self.staged_proposal_calls.contains(&call_id)", "test_rejected_call_cannot_be_resurrected_and_old_review_preserves_new_turn"),
)


def run(target, mutate):
    assert not target.is_relative_to(ROOT)
    with open("/tmp/narwhal3d-heavy-build.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        compile_crate(CRATE, target, "test")
        compile_crate(CRATE, target, "build")
        client = [sys.executable, "-B", str(ROOT / "spike/test_ai_cad.py"), "--library", str(target / "debug/libnarwhal_automation.so")]
        checked(client, timeout=30)
        if mutate:
            mutant_target = target / "cad-mutations"
            mutant_client = client[:-1] + [str(mutant_target / "debug/libnarwhal_automation.so")]
            variants = list(MUTATIONS) + [("one_use_consumption", "self.pending.as_mut().unwrap().spent = true;", "/* mutation: permit replay */", None)]
            for label, old, new, test in variants:
                with tempfile.TemporaryDirectory(prefix="narwhal-cad-mutation-") as tmp:
                    crate = Path(tmp)
                    shutil.copytree(CRATE / "src", crate / "src")
                    for name in ("Cargo.toml", "Cargo.lock"):
                        shutil.copy2(CRATE / name, crate / name)
                    source = crate / "src" / ("conversation.rs" if label == "proposal_call_replay" else "actions.rs")
                    text = source.read_text()
                    assert text.count(old) == 1, (label, "mutation anchor changed")
                    source.write_text(text.replace(old, new, 1))
                    compile_crate(crate, mutant_target, "build")
                    if test:
                        checked(mutant_client + ["--test", test], timeout=30, expect_failure=True)
                    else:
                        # Test-only consume returns typed data, not a CAD action.
                        result = subprocess.run(compile_command(crate, mutant_target, "test",
                            ("--lib", "actions::tests::missing_authority")),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=180)
                        assert result.returncode and "test result: FAILED" in result.stdout, "consumption mutation did not fail the intended Rust test"
                    print("RED confirmed:", label, flush=True)
            checked(client, timeout=30)
        print("CAD gate: GREEN; isolated mutations", len(MUTATIONS) + 1 if mutate else 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-dir", required=True, type=Path)
    parser.add_argument("--mutations", action="store_true")
    args = parser.parse_args()
    run(args.target_dir.resolve(), args.mutations)
