"""Run optional converter tools through the bounded Rust process supervisor.

The supervisor owns process-group/job teardown and output draining. Source checkouts and
platforms without its verified executable retain a behavior-compatible Python fallback.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
from collections.abc import Mapping, Sequence

_MAX_MEMORY_MIB = 1024 * 1024
_MEMORY_WRAPPER = """\
import os
import resource
import sys

try:
    limit = int(sys.argv[1])
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    os.execvpe(sys.argv[2], sys.argv[2:], os.environ)
except BaseException as exc:
    os.write(2, f"could not apply converter memory limit: {exc}\\n".encode("utf-8", "replace"))
    raise SystemExit(126)
"""

_ALLOWED_ENV = (
    "PATH", "HOME", "USERPROFILE", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL",
    "LC_CTYPE", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "FONTCONFIG_PATH", "FONTCONFIG_FILE",
    "LD_LIBRARY_PATH",
)


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    error: str | None = None
    native: bool = False


def _environment(extra: Mapping[str, str] | None) -> dict[str, str]:
    env = {name: os.environ[name] for name in _ALLOWED_ENV if name in os.environ}
    if extra:
        env.update({str(key): str(value) for key, value in extra.items()})
    return env


def _supervisor() -> str | None:
    explicit = os.environ.get("NARWHAL_CONVERTER_SUPERVISOR")
    if explicit:
        return explicit if os.path.isfile(explicit) and os.access(explicit, os.X_OK) else None
    try:
        from .runtime_tools import arch_name, platform_name

        key = f"{platform_name()}-{arch_name()}"
    except Exception:  # noqa: BLE001
        return None
    root = Path(__file__).resolve().parents[1]
    candidates = (
        root / "runtime" / key / "bin" / ("narwhal-converter-supervisor.exe"
                                           if key.startswith("windows-")
                                           else "narwhal-converter-supervisor"),
        root / "artifacts" / "converter-supervisor" / key /
            ("narwhal-converter-supervisor.exe" if key.startswith("windows-")
             else "narwhal-converter-supervisor"),
    )
    return next((str(path) for path in candidates
                 if path.is_file() and os.access(path, os.X_OK)), None)


def _decode_tail(data: bytes, limit: int) -> str:
    text = data[-limit:].decode("utf-8", "replace")
    used = 0
    start = len(text)
    for index in range(len(text) - 1, -1, -1):
        width = len(text[index].encode("utf-8"))
        if used + width > limit:
            break
        used += width
        start = index
    return text[start:]


def _read_tail(stream, limit: int, output: list[bytes]) -> None:
    tail = bytearray()
    try:
        while True:
            block = stream.read(8192)
            if not block:
                break
            tail.extend(block)
            if len(tail) > limit:
                del tail[:-limit]
    finally:
        output.append(bytes(tail))


def _is_windows() -> bool:
    return os.name == "nt"


def _terminate_tree(process: subprocess.Popen[bytes]) -> None:
    if _is_windows():
        _ = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.kill()
    except OSError:
        pass


def _python_run(
    argv: Sequence[str],
    timeout: float,
    env: dict[str, str],
    limit: int,
    memory_mib: int | None,
) -> Result:
    command = list(map(str, argv))
    if memory_mib is not None:
        try:
            import resource
        except ImportError:
            return Result(-1, "", "", error="RLIMIT_AS is unavailable for Python fallback")
        if not hasattr(resource, "RLIMIT_AS") or not sys.executable:
            return Result(-1, "", "", error="RLIMIT_AS is unavailable for Python fallback")
        command = [
            sys.executable, "-c", _MEMORY_WRAPPER,
            str(memory_mib * 1024 * 1024), *command,
        ]
    try:
        if _is_windows():
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                start_new_session=True,
            )
    except OSError as exc:
        return Result(-1, "", "", error=f"could not start converter: {exc}")
    stdout, stderr = [], []
    stdout_pipe = process.stdout
    stderr_pipe = process.stderr
    assert stdout_pipe is not None and stderr_pipe is not None
    threads = [
        threading.Thread(target=_read_tail, args=(stdout_pipe, limit, stdout), daemon=True),
        threading.Thread(target=_read_tail, args=(stderr_pipe, limit, stderr), daemon=True),
    ]
    for worker in threads:
        worker.start()
    timed_out = False
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_tree(process)
        returncode = process.wait()
    for worker in threads:
        worker.join(0.05)
    if any(worker.is_alive() for worker in threads):
        _terminate_tree(process)
    for worker in threads:
        worker.join(2)
    out = stdout[0] if stdout else b""
    err = stderr[0] if stderr else b""
    return Result(
        returncode, _decode_tail(out, limit), _decode_tail(err, limit),
        timed_out=timed_out,
        error=f"timed out after {timeout:g} seconds" if timed_out else None,
    )


def run(
    argv: Sequence[str],
    *,
    timeout: float,
    extra_env: Mapping[str, str] | None = None,
    max_output: int = 8192,
    memory_mib: int | None = None,
) -> Result:
    """Run one converter with bounded output and an optional process memory ceiling."""
    if not argv:
        return Result(-1, "", "", error="converter command is empty")
    if max_output < 1 or max_output > 1024 * 1024:
        return Result(-1, "", "", error="max_output must be from 1 through 1048576")
    if memory_mib is not None and (
        isinstance(memory_mib, bool) or not isinstance(memory_mib, int)
        or memory_mib < 1 or memory_mib > _MAX_MEMORY_MIB
    ):
        return Result(-1, "", "", error="memory_mib must be from 1 through 1048576")
    env = _environment(extra_env)
    supervisor = _supervisor()
    if supervisor:
        command = [
            supervisor, "--timeout-ms", str(max(1, round(timeout * 1000))),
            "--max-output", str(max_output),
        ]
        if memory_mib is not None:
            command.extend(("--memory-mib", str(memory_mib)))
        for key, value in sorted(env.items()):
            command.extend(("--env", f"{key}={value}"))
        command.extend(("--", *map(str, argv)))
        try:
            done = subprocess.run(
                command, check=False, capture_output=True, text=True,
                errors="replace", timeout=timeout + 10,
            )
        except subprocess.TimeoutExpired:
            return Result(-1, "", "", timed_out=True,
                          error="native converter supervisor timed out", native=True)
        except OSError as exc:
            return Result(-1, "", "", error=f"native supervisor could not start: {exc}",
                          native=True)
        if done.returncode != 0:
            return Result(-1, "", done.stderr,
                          error=f"native supervisor failed with status {done.returncode}",
                          native=True)
        try:
            packet = json.loads(done.stdout)
        except json.JSONDecodeError as exc:
            return Result(-1, "", "", error=f"native supervisor returned invalid JSON: {exc}",
                          native=True)
        if not isinstance(packet, dict):
            return Result(-1, "", "", error="native supervisor returned a non-object report",
                          native=True)
        returncode = packet.get("returncode")
        stdout = packet.get("stdout")
        stderr = packet.get("stderr")
        timed_out = packet.get("timed_out")
        error = packet.get("error")
        if (not isinstance(returncode, int) or not isinstance(stdout, str)
                or not isinstance(stderr, str) or not isinstance(timed_out, bool)
                or (error is not None and not isinstance(error, str))):
            return Result(-1, "", "", error="native supervisor returned a malformed report",
                          native=True)
        return Result(returncode, stdout, stderr, timed_out, error, native=True)
    if memory_mib is not None and _is_windows():
        return Result(
            -1, "", "",
            error="memory limit requires the native converter supervisor on Windows",
        )
    return _python_run(argv, timeout, env, max_output, memory_mib)
