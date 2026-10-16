#!/usr/bin/env python3
"""Behavior tests for the Rust converter process supervisor and Python fallback."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import types

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg
from narwhal3d import converter_process  # noqa: E402

checks = 0


def check(condition, label):
    global checks
    checks += 1
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        raise AssertionError(label)


def script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\nset -eu\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


crate = ROOT / "native" / "converter_supervisor"
built = subprocess.run(
    ["cargo", "+1.95.0", "build", "--release", "--locked", "--offline"],
    cwd=crate, text=True, capture_output=True,
)
supervisor = crate / "target" / "release" / "narwhal-converter-supervisor"
check(built.returncode == 0 and supervisor.is_file() and os.access(supervisor, os.X_OK),
      "optimized Rust supervisor builds from tracked source for direct acceptance")
prior = os.environ.get("NARWHAL_CONVERTER_SUPERVISOR")
os.environ["NARWHAL_CONVERTER_SUPERVISOR"] = str(supervisor)
try:
    with tempfile.TemporaryDirectory(prefix="narwhal_converter_test_") as temporary:
        work = Path(temporary)
        success = script(work / "success.sh", "printf 'hello'; printf 'warning: note\\n' >&2\n")
        result = converter_process.run([str(success)], timeout=2)
        check(result.native and result.returncode == 0 and result.stdout == "hello"
              and "warning: note" in result.stderr,
              "native supervisor returns converter status and both streams")

        noisy = script(
            work / "noisy.sh",
            "i=0; while [ $i -lt 300 ]; do printf x; i=$((i + 1)); done; printf 'THE-END'\n",
        )
        result = converter_process.run([str(noisy)], timeout=2, max_output=32)
        check(result.native and len(result.stdout.encode()) <= 32
              and result.stdout.endswith("THE-END"),
              "native capture keeps only the bounded output tail")

        environment = script(
            work / "environment.sh",
            "printf '%s|%s' \"${NARWHAL_TEST_ALLOWED:-}\" \"${NARWHAL_TEST_SECRET:-missing}\"\n",
        )
        os.environ["NARWHAL_TEST_SECRET"] = "must-not-leak"
        result = converter_process.run(
            [str(environment)], timeout=2,
            extra_env={"NARWHAL_TEST_ALLOWED": "yes"},
        )
        check(result.stdout == "yes|missing",
              "converter receives explicit additions but not unrelated application secrets")
        os.environ.pop("NARWHAL_TEST_SECRET", None)

        failure = script(work / "failure.sh", "printf 'specific failure' >&2; exit 7\n")
        result = converter_process.run([str(failure)], timeout=2)
        check(result.native and result.returncode == 7 and result.stderr == "specific failure",
              "nonzero converter exits retain actionable diagnostics")

        invalid = script(work / "invalid.sh", "printf '\\377ok'\n")
        result = converter_process.run([str(invalid)], timeout=2)
        check(result.native and result.returncode == 0 and result.stdout.endswith("ok")
              and "\ufffd" in result.stdout and len(result.stdout.encode()) <= 8192,
              "invalid converter bytes are decoded with replacement inside the byte limit")

        pidfile = work / "grandchild.pid"
        hanging = script(
            work / "hanging.sh",
            f"sleep 30 &\nprintf '%s %s' $$ $! > '{pidfile}'\nwait\n",
        )
        result = converter_process.run([str(hanging)], timeout=0.2)
        check(result.native and result.timed_out,
              "native supervisor reports its own timeout")
        process_ids = [int(value) for value in pidfile.read_text().split()]
        gone = set()
        for _ in range(40):
            for process_id in process_ids:
                try:
                    os.kill(process_id, 0)
                except ProcessLookupError:
                    gone.add(process_id)
            if len(gone) == len(process_ids):
                break
            time.sleep(0.05)
        check(len(gone) == len(process_ids),
              "timeout reaps both converter and spawned grandchild")

        os.environ["NARWHAL_CONVERTER_SUPERVISOR"] = str(work / "missing")
        result = converter_process.run([str(success)], timeout=2)
        check(not result.native and result.returncode == 0 and result.stdout == "hello",
              "missing native supervisor uses the behavior-compatible Python fallback")
        result = converter_process.run([str(invalid)], timeout=2, max_output=4)
        check(not result.native and result.stdout.endswith("ok")
              and len(result.stdout.encode()) <= 4,
              "Python fallback bounds invalid UTF-8 output in linear space")
        fallback_pidfile = work / "fallback.pid"
        fallback_hanging = script(
            work / "fallback-hanging.sh",
            f"sleep 30 &\nprintf '%s %s' $$ $! > '{fallback_pidfile}'\nwait\n",
        )
        result = converter_process.run([str(fallback_hanging)], timeout=0.2)
        fallback_ids = [int(value) for value in fallback_pidfile.read_text().split()]
        fallback_gone = set()
        for _ in range(40):
            for process_id in fallback_ids:
                try:
                    os.kill(process_id, 0)
                except ProcessLookupError:
                    fallback_gone.add(process_id)
            if len(fallback_gone) == len(fallback_ids):
                break
            time.sleep(0.05)
        check(result.timed_out and len(fallback_gone) == len(fallback_ids),
              "Python fallback bounds timeout and reaps the complete process tree")

        malformed = script(work / "malformed-supervisor.sh", "printf 'not json'\n")
        side_effect = work / "runs"
        converter = script(work / "side-effect.sh", f"printf x >> '{side_effect}'\n")
        os.environ["NARWHAL_CONVERTER_SUPERVISOR"] = str(malformed)
        result = converter_process.run([str(converter)], timeout=2)
        check(result.native and result.error and not side_effect.exists(),
              "malformed native protocol never reruns a side-effecting converter")

        empty = converter_process.run([], timeout=2)
        check(empty.error == "converter command is empty" and empty.returncode == -1,
              "empty converter commands are refused by name")
finally:
    os.environ.pop("NARWHAL_TEST_SECRET", None)
    if prior is None:
        os.environ.pop("NARWHAL_CONVERTER_SUPERVISOR", None)
    else:
        os.environ["NARWHAL_CONVERTER_SUPERVISOR"] = prior

print(f"\n{checks}/{checks} converter supervisor checks passed")
