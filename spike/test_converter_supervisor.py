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
from typing import cast
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
supervisor_source = (crate / "src" / "main.rs").read_text(encoding="utf-8")
check("JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_JOB_MEMORY" in supervisor_source
      and "information.JobMemoryLimit = process_memory_limit" in supervisor_source,
      "Windows containment caps both each process and the aggregate Job Object")
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

        memory_bytes = 128 * 1024 * 1024
        memory_probe = work / "memory_probe.py"
        memory_probe.write_text(
            "import resource\n"
            "import sys\n"
            "expected = int(sys.argv[1])\n"
            "soft, _ = resource.getrlimit(resource.RLIMIT_AS)\n"
            "print(soft)\n"
            "if soft != expected:\n"
            "    raise SystemExit(9)\n"
            "try:\n"
            "    bytearray(expected)\n"
            "except MemoryError:\n"
            "    print('limited')\n"
            "else:\n"
            "    print('allocation unexpectedly succeeded')\n"
            "    raise SystemExit(10)\n",
            encoding="utf-8",
        )
        result = converter_process.run(
            [sys.executable, str(memory_probe), str(memory_bytes)],
            timeout=2, memory_mib=128,
        )
        check(result.native and result.returncode == 0
              and result.stdout.splitlines() == [str(memory_bytes), "limited"],
              "native forwarding applies RLIMIT_AS before converter execution")

        invalid_memory = subprocess.run(
            [str(supervisor), "--timeout-ms", "1000", "--memory-mib", "0",
             "--", str(success)],
            text=True, capture_output=True,
        )
        check(invalid_memory.returncode == 2
              and "--memory-mib must be from 1 through" in invalid_memory.stderr,
              "native supervisor rejects a zero memory limit")

        invalid_memory_effect = work / "invalid-memory-ran"
        invalid_memory_converter = script(
            work / "invalid-memory.sh", f"touch '{invalid_memory_effect}'\n",
        )
        invalid_results = [
            converter_process.run(
                [str(invalid_memory_converter)], timeout=2, memory_mib=value,
            )
            for value in (
                0, -1, 1024 * 1024 + 1, True, cast(int, cast(object, "128")),
            )
        ]
        check(all(result.returncode == -1 and result.error and not result.native
                  for result in invalid_results)
              and not invalid_memory_effect.exists(),
              "Python API rejects non-positive, oversized, and non-integer limits before launch")

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
        result = converter_process.run(
            [sys.executable, str(memory_probe), str(memory_bytes)],
            timeout=2, memory_mib=128,
        )
        check(not result.native and result.returncode == 0
              and result.stdout.splitlines() == [str(memory_bytes), "limited"],
              "Unix Python fallback applies RLIMIT_AS before converter execution")
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

        windows_effect = work / "windows-fallback-ran"
        windows_converter = script(
            work / "windows-fallback.sh", f"touch '{windows_effect}'\n",
        )
        actual_is_windows = converter_process._is_windows
        actual_python_run = converter_process._python_run
        fallback_calls = []
        try:
            converter_process._is_windows = lambda: True
            converter_process._python_run = lambda *args, **kwargs: fallback_calls.append(args)
            result = converter_process.run(
                [str(windows_converter)], timeout=2, memory_mib=128,
            )
        finally:
            converter_process._python_run = actual_python_run
            converter_process._is_windows = actual_is_windows
        check(result.returncode == -1 and result.error
              and "native converter supervisor" in result.error
              and not fallback_calls and not windows_effect.exists(),
              "Windows memory request fails closed when the native supervisor is unavailable")

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
