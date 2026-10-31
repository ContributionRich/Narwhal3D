#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cheap orchestration tests; no Rust compiler, real staging, or heavy lock.

Python is intentional: this exercises the Python build/qualification boundary.
Run: python3 -B -m unittest spike.test_application_core_gate -v
Mutants are compiled in memory, never written over the main agent's script.
"""
from __future__ import annotations

import argparse
import builtins
from contextlib import ExitStack
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_application_core.py"
SOURCE = SCRIPT.read_text(encoding="utf-8")
STEPS = ["fmt", "clippy", "test", "build", "abi"]
NEW_BINARY = b"synthetic qualified application core\x00\x01"
OLD_BINARY = b"previous working application core\x00\x02"


def load_gate(source=SOURCE):
    spec = importlib.util.spec_from_file_location("application_core_gate_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # Compile fresh text, avoiding stale path-loader bytecode during mutations.
    exec(compile(source, str(SCRIPT), "exec"), module.__dict__)
    return module


class Sandbox:
    """All writes are below one temporary directory, all tool calls are fake."""

    def __init__(self, source=SOURCE):
        self.source = source

    def __enter__(self):
        self.stack = ExitStack()
        try:
            base = Path(self.stack.enter_context(tempfile.TemporaryDirectory(
                prefix="application-core-gate-"))).resolve()
            self.root = base / "checkout"
            self.crate = self.root / "native/application_core"
            self.src = self.crate / "src/lib.rs"
            self.src.parent.mkdir(parents=True)
            self.src.write_text("pub fn fixture() {}\n")
            (self.crate / "Cargo.toml").write_text("synthetic manifest\n")
            (self.crate / "Cargo.lock").write_text("synthetic lockfile\n")
            self.target = base / "target"
            self.gate = load_gate(self.source)
            self.gate.ROOT = self.root
            self.gate.CRATE = self.crate
            self.library = self.target / "release" / self.gate.LIBRARY
            self.destination = (self.root / "artifacts/application-core/linux-x86_64"
                                / self.gate.LIBRARY)
            self.calls = []
            self.fail_at = None
            self.warning = False
            self.after_step = lambda step: None
            self.output = io.StringIO()
            self.stack.enter_context(mock.patch("sys.stdout", self.output))
            self.stack.enter_context(mock.patch.object(self.gate.sys, "platform", "linux"))
            self.run = self.stack.enter_context(mock.patch.object(
                self.gate.subprocess, "run", side_effect=self.fake_run))
            self.flock = self.stack.enter_context(mock.patch.object(self.gate.fcntl, "flock"))
            self.stack.enter_context(mock.patch.object(
                self.gate, "open", side_effect=self.fake_open, create=True))
            read_text = Path.read_text

            def read_memory(path, *args, **kwargs):
                if path == Path("/proc/meminfo"):
                    return "MemAvailable: 16777216 kB\n"
                return read_text(path, *args, **kwargs)

            self.stack.enter_context(mock.patch.object(Path, "read_text", read_memory))
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *exc):
        return self.stack.__exit__(*exc)

    def fake_open(self, path, *args, **kwargs):
        if str(path) == "/tmp/narwhal3d-heavy-build.lock":
            return io.StringIO()
        raise AssertionError(f"unexpected gate open outside fixture: {path}")

    def fake_run(self, command, **kwargs):
        command = list(map(str, command))
        if "cargo" in command:
            step = command[command.index("cargo") + 2]
        elif str(self.root / "spike/test_document_session_abi.py") in command:
            step = "abi"
        else:
            raise AssertionError(f"unexpected subprocess: {command}")
        self.calls.append((step, command, kwargs))
        strict_warning = (step == "clippy" and self.warning
                          and command[-3:] == ["--", "-D", "warnings"])
        status = 23 if step == self.fail_at or strict_warning else 0
        if status and kwargs.get("check"):
            raise subprocess.CalledProcessError(status, command)
        if step == "build" and not status:
            self.library.parent.mkdir(parents=True, exist_ok=True)
            self.library.write_bytes(NEW_BINARY)
        self.after_step(step)
        return subprocess.CompletedProcess(command, status)

    def main(self, stage=True, target=None):
        argv = [str(SCRIPT), "--target-dir", str(target or self.target)]
        if stage:
            argv.append("--stage-dev")
        with mock.patch.object(sys, "argv", argv):
            return self.gate.main()

    def prior_binary(self):
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        self.destination.write_bytes(OLD_BINARY)
        self.receipt_path = self.destination.parent / "build.json"
        self.receipt_path.write_text('{"previous": true}\n')

    def backup_path(self):
        return (self.root / "artifacts/application-core/backups"
                / hashlib.sha256(OLD_BINARY).hexdigest() / self.gate.LIBRARY)


class ApplicationCoreGateTests(unittest.TestCase):
    gate_source = SOURCE

    def setUp(self):
        self.box = Sandbox(self.gate_source).__enter__()
        self.addCleanup(self.box.__exit__, None, None, None)
        self.gate = self.box.gate

    def assert_prior_untouched(self):
        self.assertEqual(self.box.destination.read_bytes(), OLD_BINARY)
        self.assertEqual(self.box.receipt_path.read_text(), '{"previous": true}\n')

    def test_required_order_flags_environment_and_receipt(self):
        box = self.box
        expected_sources = self.gate.source_inventory()
        library, receipt = self.gate.build(box.target)
        self.assertEqual([step for step, _, _ in box.calls], STEPS)
        for step, command, kwargs in box.calls:
            with self.subTest(step=step):
                self.assertTrue(kwargs["check"])
                self.assertGreater(kwargs["timeout"], 0)
                self.assertEqual(kwargs["cwd"], box.root)
                if step == "abi":
                    self.assertEqual(command[-3:], ["-B", str(box.root / "spike/test_document_session_abi.py"), "--mutation"])
                    self.assertEqual(kwargs["env"]["NARWHAL_APPLICATION_CORE"], str(box.library))
                    self.assertEqual(kwargs["env"]["PYTHONDONTWRITEBYTECODE"], "1")
                    continue
                self.assertEqual(command[command.index("cargo") + 1], "+1.95.0")
                self.assertEqual(command[command.index("--manifest-path") + 1], str(box.crate / "Cargo.toml"))
                self.assertEqual(kwargs["env"]["CARGO_BUILD_JOBS"], "1")
                self.assertEqual(kwargs["env"]["CARGO_INCREMENTAL"], "0")
                if step == "fmt":
                    self.assertIn("--check", command)
                else:
                    self.assertIn("--locked", command)
                    self.assertIn("--offline", command)
                    self.assertEqual(command[command.index("--target-dir") + 1], str(box.target))
                if step == "clippy":
                    self.assertIn("--all-targets", command)
                    self.assertEqual(command[-3:], ["--", "-D", "warnings"])
                if step in ("test", "build"):
                    self.assertIn("--release", command)
        box.flock.assert_called_once()
        self.assertEqual(box.flock.call_args.args[1], self.gate.fcntl.LOCK_EX | self.gate.fcntl.LOCK_NB)
        self.assertEqual(library, box.library)
        self.assertEqual(receipt["source_inputs"], expected_sources)
        self.assertEqual(receipt["library_sha256"], hashlib.sha256(NEW_BINARY).hexdigest())
        self.assertEqual(receipt["abi"], 1)
        self.assertFalse(box.destination.exists())

    def test_main_only_stages_after_all_checks(self):
        def stage(library, receipt):
            self.assertEqual([step for step, _, _ in self.box.calls], STEPS)
            self.assertEqual(library, self.box.library)
            self.assertEqual(receipt["source_inputs"], self.gate.source_inventory())
        with mock.patch.object(self.gate, "stage", side_effect=stage) as staged:
            self.box.main()
        staged.assert_called_once()
        self.assertIn("APPLICATION CORE GATE PASSED", self.box.output.getvalue())

    def test_main_does_not_stage_without_flag(self):
        with mock.patch.object(self.gate, "stage") as staged:
            self.box.main(stage=False)
        staged.assert_not_called()
        self.assertEqual([step for step, _, _ in self.box.calls], STEPS)

    def test_each_failed_check_aborts_before_staging(self):
        self.box.prior_binary()
        for index, step in enumerate(STEPS):
            with self.subTest(step=step):
                self.box.calls.clear()
                self.box.output.seek(0)
                self.box.output.truncate()
                self.box.fail_at = step
                with mock.patch.object(self.gate, "stage") as staged:
                    with self.assertRaises(subprocess.CalledProcessError) as error:
                        self.box.main()
                self.assertEqual(error.exception.returncode, 23)
                self.assertEqual([name for name, _, _ in self.box.calls], STEPS[:index + 1])
                staged.assert_not_called()
                self.assert_prior_untouched()
                self.assertNotIn("APPLICATION CORE GATE PASSED", self.box.output.getvalue())

    def test_clippy_warning_is_fatal(self):
        self.box.warning = True
        with mock.patch.object(self.gate, "stage") as staged:
            with self.assertRaises(subprocess.CalledProcessError):
                self.box.main()
        self.assertEqual([step for step, _, _ in self.box.calls], ["fmt", "clippy"])
        staged.assert_not_called()

    def test_source_edit_during_qualification_aborts_before_stage(self):
        self.box.prior_binary()
        def edit_after_abi(step):
            if step == "abi":
                self.box.src.write_text("pub fn altered() {}\n")
        self.box.after_step = edit_after_abi
        with mock.patch.object(self.gate, "stage") as staged:
            with self.assertRaisesRegex(RuntimeError, "sources changed"):
                self.box.main()
        self.assertEqual([step for step, _, _ in self.box.calls], STEPS)
        staged.assert_not_called()
        self.assert_prior_untouched()

    def test_source_inventory_detects_edits_additions_and_deletions(self):
        before = self.gate.source_inventory()
        key = "native/application_core/src/lib.rs"
        self.assertEqual(before[key], hashlib.sha256(self.box.src.read_bytes()).hexdigest())
        self.box.src.write_text("pub fn altered() {}\n")
        self.assertNotEqual(before[key], self.gate.source_inventory()[key])
        extra = self.box.crate / "build.rs"
        extra.write_text("fn main() {}\n")
        self.assertIn("native/application_core/build.rs", self.gate.source_inventory())
        self.box.src.unlink()
        self.assertNotIn(key, self.gate.source_inventory())

    def test_in_project_targets_refused_before_tools_or_lock(self):
        alias = self.box.target.parent / "source-alias"
        alias.symlink_to(self.box.root, target_is_directory=True)
        for target in (self.box.root, self.box.root / "target", alias / "target"):
            with self.subTest(target=target):
                with self.assertRaisesRegex(ValueError, "outside the source checkout"):
                    self.gate.build(target)
        self.box.run.assert_not_called()
        self.box.flock.assert_not_called()
        self.assertFalse((self.box.root / "target").exists())

    def test_staging_refuses_changed_source(self):
        library, receipt = self.gate.build(self.box.target)
        self.box.prior_binary()
        self.box.src.write_text("pub fn altered() {}\n")
        with self.assertRaisesRegex(RuntimeError, "qualified inputs changed"):
            self.gate.stage(library, receipt)
        self.assert_prior_untouched()

    def test_staging_refuses_changed_binary(self):
        library, receipt = self.gate.build(self.box.target)
        self.box.prior_binary()
        library.write_bytes(b"unqualified replacement")
        with self.assertRaisesRegex(RuntimeError, "qualified inputs changed"):
            self.gate.stage(library, receipt)
        self.assert_prior_untouched()

    def test_staging_backs_up_previous_binary_and_atomically_replaces(self):
        library, receipt = self.gate.build(self.box.target)
        self.box.prior_binary()
        replace = self.gate.os.replace
        def observe_replace(source, destination):
            source, destination = Path(source), Path(destination)
            self.assertEqual(destination, self.box.destination)
            self.assertEqual(source.parent, destination.parent)
            self.assertNotEqual(source, library)
            self.assertEqual(source.read_bytes(), NEW_BINARY)
            self.assert_prior_untouched()
            self.assertEqual(self.box.backup_path().read_bytes(), OLD_BINARY)
            replace(source, destination)
        with mock.patch.object(self.gate.os, "replace", side_effect=observe_replace) as replaced:
            self.gate.stage(library, receipt)
        replaced.assert_called_once()
        self.assertEqual(self.box.destination.read_bytes(), NEW_BINARY)
        self.assertEqual(json.loads(self.box.receipt_path.read_text()), receipt)
        self.assertEqual(self.box.backup_path().read_bytes(), OLD_BINARY)
        self.assertEqual(set(self.box.destination.parent.iterdir()), {self.box.destination, self.box.receipt_path})

    def test_failed_atomic_replace_preserves_previous_and_cleans_temporary(self):
        library, receipt = self.gate.build(self.box.target)
        self.box.prior_binary()
        with mock.patch.object(self.gate.os, "replace", side_effect=OSError("replace denied")):
            with self.assertRaisesRegex(OSError, "replace denied"):
                self.gate.stage(library, receipt)
        self.assert_prior_untouched()
        self.assertEqual(self.box.backup_path().read_bytes(), OLD_BINARY)
        self.assertEqual(set(self.box.destination.parent.iterdir()), {self.box.destination, self.box.receipt_path})

    def test_staging_refuses_corrupted_existing_backup(self):
        library, receipt = self.gate.build(self.box.target)
        self.box.prior_binary()
        backup = self.box.backup_path()
        backup.parent.mkdir(parents=True)
        backup.write_bytes(b"wrong backup")
        with self.assertRaisesRegex(RuntimeError, "backup mismatch"):
            self.gate.stage(library, receipt)
        self.assert_prior_untouched()
        self.assertEqual(backup.read_bytes(), b"wrong backup")

    def test_staging_refuses_symlink_destination_even_if_dangling(self):
        library, receipt = self.gate.build(self.box.target)
        self.box.destination.parent.mkdir(parents=True)
        victim = self.box.target.parent / "unrelated-binary"
        for existing in (True, False):
            with self.subTest(existing=existing):
                if existing:
                    victim.write_bytes(OLD_BINARY)
                else:
                    victim.unlink()
                self.box.destination.symlink_to(victim)
                with self.assertRaisesRegex(RuntimeError, "symlink"):
                    self.gate.stage(library, receipt)
                self.assertTrue(self.box.destination.is_symlink())
                if existing:
                    self.assertEqual(victim.read_bytes(), OLD_BINARY)
                else:
                    self.assertFalse(victim.exists())
                self.assertFalse((self.box.destination.parent / "build.json").exists())
                self.box.destination.unlink()


class ImportAndExitTests(unittest.TestCase):
    def test_importspec_does_not_parse_args_build_stage_or_lock(self):
        with (mock.patch.object(argparse, "ArgumentParser", side_effect=AssertionError("parser on import")),
              mock.patch.object(subprocess, "run", side_effect=AssertionError("subprocess on import")),
              mock.patch.object(builtins, "open", side_effect=AssertionError("open on import")),
              mock.patch.object(sys, "argv", ["gate", "--unknown-import-argument"])):
            gate = load_gate()
        self.assertTrue(callable(gate.main))
        self.assertTrue(callable(gate.build))
        self.assertTrue(callable(gate.stage))

    def test_abi_failure_exits_nonzero_at_main_boundary(self):
        # Only a Python child is real; its tools and heavy lock remain patched.
        child = f"""
import importlib.util
spec = importlib.util.spec_from_file_location('isolated_gate_tests', {str(Path(__file__).resolve())!r})
tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tests)
with tests.Sandbox() as box:
    box.fail_at = 'abi'
    box.main()
"""
        result = subprocess.run([sys.executable, "-B", "-c", child],
                                capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CalledProcessError", result.stderr)
        self.assertIn("test_document_session_abi.py", result.stderr)
        self.assertIn("23", result.stderr)
        self.assertNotIn("APPLICATION CORE GATE PASSED", result.stdout)


class MutationTests(unittest.TestCase):
    def assert_mutant_is_red(self, old, new, test_name):
        self.assertEqual(SOURCE.count(old), 1, "mutation anchor changed; update the mutant")
        mutant = SOURCE.replace(old, new, 1)
        case_type = type("MutatedGateTests", (ApplicationCoreGateTests,), {"gate_source": mutant})
        result = unittest.TestResult()
        case_type(test_name).run(result)
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(result.errors, [], "mutation must fail a behavior assertion, not crash")
        self.assertEqual(len(result.failures), 1, "mutant survived its regression test")

    def test_dropping_strict_clippy_goes_red(self):
        self.assert_mutant_is_red("'--', '-D', 'warnings'", "'--'",
                                  "test_clippy_warning_is_fatal")

    def test_dropping_qualification_source_guard_goes_red(self):
        self.assert_mutant_is_red("if before != source_inventory():", "if False:",
                                  "test_source_edit_during_qualification_aborts_before_stage")

    def test_dropping_staging_source_guard_goes_red(self):
        self.assert_mutant_is_red("receipt['source_inputs'] != source_inventory() or ", "",
                                  "test_staging_refuses_changed_source")

    def test_dropping_staging_binary_hash_goes_red(self):
        self.assert_mutant_is_red(" or digest(library) != receipt['library_sha256']", "",
                                  "test_staging_refuses_changed_binary")


if __name__ == "__main__":
    unittest.main()
