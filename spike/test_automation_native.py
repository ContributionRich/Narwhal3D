#!/usr/bin/env python3
"""Focused Rust build/test + optional isolated source mutations, under shared lock.

Builds only this lane. Requires Linux/systemd user scopes; fails rather than
silently building without a memory cap. No worktrees, runtime symlinks or full suite.
"""
from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT / "native/automation_core"
MUTATIONS = (
    ("read_tool_allowlist", "catalog.rs", "if !READS.iter().any", "if false && !READS.iter().any", "test_permission_boundary"),
    ("request_id_replay", "policy.rs", "if self.seen.contains(&id)", "if false && self.seen.contains(&id)", "test_duplicate_request_id"),
    ("duplicate_json_keys", "json.rs", "if values.contains_key(&key)", "if false && values.contains_key(&key)", "test_malformed_and_duplicate_json"),
    ("public_frame_bound", "protocol.rs", "const MAX_INPUT: usize = 65_535;", "const MAX_INPUT: usize = 131_071;", "test_oversized_frame_closes"),
    ("bridge_session_identity", "main.rs", "reply.session != self.session", "false", "test_bridge_identity"),
    ("bridge_response_sequence", "main.rs", "reply.seq != self.seq", "false", "test_bridge_identity"),
    ("prompt_eof_cancel", "protocol.rs", "reader_shutdown.cancel();", "/* mutation: omit EOF cancellation */", "test_timeout_disconnect_and_eof"),
    ("failed_stdout_terminal", "protocol.rs", "if failed", "if false && failed", "test_stalled_stdout_is_terminal"),
)


MODERN_MUTATIONS = (
    ("modern_discovery", "policy.rs", "vec![ProtocolVersion::V_2025_11_25, ProtocolVersion::V_2026_07_28]", "vec![ProtocolVersion::V_2025_11_25]", "test_discovery_selects_modern_without_initialize"),
    ("optional_identity_type", "policy.rs", "meta.contains_key(INFO) && meta.client_info().is_none()", "false", "test_optional_client_info_absent_valid_and_malformed"),
    ("metadata_downgrade", "policy.rs", "let inline = meta.is_some_and", "let inline = false && meta.is_some_and", "test_required_metadata_types_and_legacy_fallback_boundary"),
    ("modern_completed_id_reuse", "policy.rs", "if era == Era::Legacy {\n            self.seen.insert", "if true {\n            self.seen.insert", "test_completed_modern_id_reuse_keeps_private_sequence_monotonic"),
    ("inflight_id_guard", "policy.rs", "if self.active.contains_key(&id)", "if false && self.active.contains_key(&id)", "test_overlapping_id_correlation_and_cancelled_id_quarantine"),
    ("queued_cancel_suppression", "protocol.rs", ".map(|active| active.cancelled.clone())", ".map(|_active| CancellationToken::new())", "test_cancel_suppresses_response_queued_behind_stdout"),
    ("discovery_idle_lifetime", "protocol.rs", "self.first_frame.cancel();", "/* mutation: discovery still has startup deadline */", "test_discovery_delay_does_not_timeout_first_read"),
    ("notification_metadata", "policy.rs", '["requestId", "reason", "_meta"]', '["requestId", "reason"]', "test_cancel_accepts_notification_metadata"),
    ("bootstrap_cancel", "protocol.rs", "!self.gate.lock().unwrap().sdk_running()", "false", "test_bootstrap_discovery_cancel_does_not_kill_transport"),
    ("extension_identifier", "policy.rs", "if extensions.keys().any", "if false && extensions.keys().any", "test_extensions_validate_without_mirroring_or_requiring_capabilities"),
)


def checked(command, timeout=180, expect_failure=False):
    result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, timeout=timeout)
    if expect_failure:
        if result.returncode == 0:
            raise AssertionError("MUTATION SURVIVED: " + " ".join(command))
        # Require a test assertion/error, not startup/CLI failure.
        assert "FAILED (" in result.stdout, result.stdout[-6000:]
        return
    if result.returncode:
        raise AssertionError(result.stdout[-8000:])
    print(result.stdout[-1200:], end="", flush=True)


def compile_command(crate, target, action, extra=()):
    cap = int(os.environ.get("NARWHAL_AI_BUILD_MIB", "1536"))
    assert cap in (1024, 1536), "Supported compiler caps: 1024 or 1536 MiB"
    memory = int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines()
                      if line.startswith("MemAvailable:")))
    assert memory >= (4096 + cap) * 1024, f"Insufficient headroom: reserve 4 GiB desktop + {cap} MiB compiler cap"
    # timeout lives inside the scope so killing a driver cannot orphan a compiler.
    return ["systemd-run", "--user", "--scope", "-p", f"MemoryMax={cap}M", "-p", "MemorySwapMax=0",
            "timeout", "--signal=TERM", "--kill-after=5s", "170s",
            "env", "CARGO_TARGET_DIR=" + str(target), "cargo", action, "--locked", "--offline",
            "--manifest-path", str(crate / "Cargo.toml"), "-j", "1", *extra]


def compile_crate(crate, target, action):
    checked(compile_command(crate, target, action))


def run(target, mutations, mutation_filter=None):
    assert not target.is_relative_to(ROOT), "Build output must be outside project"
    with open("/tmp/narwhal3d-heavy-build.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        compile_crate(CRATE, target, "test")
        compile_crate(CRATE, target, "build")
        binary = target / "debug/narwhal-automation"
        client = [sys.executable, "-B", str(ROOT / "spike/test_automation_mcp.py"), "--server", str(binary)]
        modern_client = [sys.executable, "-B", str(ROOT / "spike/test_automation_modern.py"), "--server", str(binary)]
        lifecycle_client = [sys.executable, "-B", str(ROOT / "spike/test_automation_lifecycle.py"), "--server", str(binary)]
        checked(client, timeout=45)
        checked(modern_client, timeout=60)
        checked(lifecycle_client, timeout=30)
        mutation_count = 0
        if mutations:
            # Cargo may consider the baseline fresh without replacing a mutant
            # binary sharing the same target path. Never share that output path.
            mutation_target = target / "mutation-artifacts"
            mutation_binary = str(mutation_target / "debug/narwhal-automation")
            variants = [(mutation, client[:-1] + [mutation_binary]) for mutation in MUTATIONS]
            variants += [(mutation, modern_client[:-1] + [mutation_binary]) for mutation in MODERN_MUTATIONS]
            variants += [(("combined_" + suffix, "protocol.rs", "drop(wait);", "let _ = wait.await;", test), lifecycle_client[:-1] + [mutation_binary])
                         for suffix, test in (("cancel", "test_error_under_pressure_does_not_delay_cancellation_or_lose_error"),
                                              ("eof", "test_rejection_under_pressure_does_not_delay_eof"))]
            if mutation_filter:
                variants = [(m, c) for m, c in variants if mutation_filter in m[0]]
                assert variants, "no mutation matched"
            mutation_count = len(variants)
            try:
                for (label, file, old, new, test), mutation_client in variants:
                    # Copy source only; never edit the live tree during mutation tests.
                    with tempfile.TemporaryDirectory(prefix="narwhal-mcp-mutation-") as tmp:
                        crate = Path(tmp)
                        shutil.copytree(CRATE / "src", crate / "src")
                        for name in ("Cargo.toml", "Cargo.lock"):
                            shutil.copy2(CRATE / name, crate / name)
                        source = crate / "src" / file
                        text = source.read_text()
                        assert text.count(old) == 1, (label, "mutation anchor changed")
                        source.write_text(text.replace(old, new, 1))
                        compile_crate(crate, mutation_target, "build")
                        checked(mutation_client + ["--test", test], timeout=30, expect_failure=True)
                        print("RED confirmed:", label, flush=True)
            finally:
                compile_crate(CRATE, target, "build")
            checked(client, timeout=45)
            checked(modern_client, timeout=60)
            checked(lifecycle_client, timeout=30)
        print("PASS: Rust/native protocol controls; isolated mutations=" + str(mutation_count))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-dir", required=True, type=Path)
    parser.add_argument("--mutations", action="store_true")
    parser.add_argument("--mutation-filter")
    args = parser.parse_args()
    run(args.target_dir.resolve(), args.mutations, args.mutation_filter)
