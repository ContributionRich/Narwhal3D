#!/usr/bin/env python3
"""OURS: trusted Unix stdio launch of ONE new, visible, read-only MCP app.

Python only orchestrates processes/FDs and the normal Python bootstrap. All MCP
framing, tools and security remain in the Rust server. No incoming bytes choose
an executable, evaluate code, open a document, or authorize a mutation.
"""
from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import re
import secrets
import selectors
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from typing import BinaryIO, TextIO

ROOT = Path(__file__).resolve().parents[1]
OPT_IN = "new-app-read-only-v1"
READY_PREFIX = b"narwhal-automation-ready-v1 "
MAX_PENDING = 65_536
CONSENT = (
    "Read-only consent: this launches a NEW visible Narwhal app. Its initial "
    "document names, selection and metadata may be forwarded by your MCP client "
    "to an external model/provider. No modeling commands are authorized. "
    "Disconnecting or revoking MCP closes read access, not the visible app. "
    "Local work remains open; close the app yourself when finished. Its private "
    "temporary configuration is retained while the app may still be running."
)
SAFE_LOGS = frozenset({
    "narwhal automation: document_changed_relaunch_session",
    "narwhal automation: read_session_closed_relaunch_session",
    "narwhal automation: initialization_timeout",
    "narwhal automation: initialization_failed",
})


class LaunchError(Exception):
    """Only launcher-authored static messages; never include exception contents."""


def unix_only():
    if os.name != "posix" or sys.platform.startswith(("win", "cygwin", "msys")):
        raise LaunchError("Unix inherited-FD transport required; Windows is not supported")


def runtime_key():
    unix_only()
    system = {"linux": "linux", "darwin": "macos"}.get(sys.platform)
    machine = platform.machine().lower()
    arch = {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    if system is None or arch not in {"x86_64", "arm64"}:
        raise LaunchError("unsupported application runtime platform")
    return f"{system}-{arch}"


def _executable(candidates: Iterable[Path]) -> Path:
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return path
    raise LaunchError("required bundled executable missing; ask Main to package the automation runtime")


@dataclass(frozen=True)
class Runtime:
    root: Path
    app: Path
    server: Path
    bootstrap: Path
    key: str
    version: str


def product_version(root: Path, env: Mapping[str, str]) -> str:
    # AppRun exports this version. Direct installed CLI launches use its manifest.
    version = env.get("NARWHAL3D_VERSION")
    if version is None:
        manifest = root / "packaging/release-manifest.json"
        if manifest.is_file():
            try:
                with manifest.open("rb") as stream:
                    data = stream.read(1_048_577)
                if len(data) > 1_048_576:
                    raise ValueError
                version = json.loads(data)["identity"]["version"]
            except (OSError, ValueError, KeyError, TypeError):
                raise LaunchError("invalid package version manifest") from None
        else:
            version = "source-unversioned"
    if type(version) is not str or not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,127}", version):
        raise LaunchError("invalid product version")
    return version


def resolve_runtime(root: Path = ROOT, env: Mapping[str, str] | None = None) -> Runtime:
    """Fixed installed/artifact locations only; no PATH or request override.

    Main's release builder owns staging the binary into one of these locations.
    /tmp build outputs are deliberately NOT a production fallback.
    """
    env = os.environ if env is None else env
    key = runtime_key()
    root = Path(root).resolve()
    server = _executable((root / "runtime" / key / "bin/narwhal-automation",
                          root / "artifacts/automation" / key / "narwhal-automation"))
    if key.startswith("macos-"):
        candidates = (root / "tools/blender-patched/Blender.app/Contents/MacOS/Blender",
                      root / "tools/Blender.app/Contents/MacOS/Blender")
    else:
        candidates = (root / "tools/blender-patched/blender",)
        if key == "linux-x86_64":
            candidates += (root / "tools/blender-5.2.2-linux-x64/blender",)
    app = _executable(candidates)
    bootstrap = root / "launch/bootstrap.py"
    if not bootstrap.is_file():
        raise LaunchError("normal application bootstrap missing")
    return Runtime(root, app, server, bootstrap, key, product_version(root, env))


def application_environment(runtime: Runtime, private_dir: Path, fd: int, nonce: str,
                            environ: Mapping[str, str]) -> dict[str, str]:
    # No inherited provider tokens, PYTHONPATH, LD_PRELOAD, shell hooks, startup
    # document/welcome requests or executable overrides. Desktop display authority
    # is necessary for this private visible app, but never passed to Rust.
    keep = ("HOME", "USER", "LOGNAME", "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY",
            "XDG_RUNTIME_DIR", "LANG", "LC_ALL", "LC_CTYPE", "TZ")
    env = {key: environ[key] for key in keep if key in environ}
    bin_dir = runtime.root / "runtime" / runtime.key / "bin"
    env.update({
        "PATH": str(bin_dir) + os.pathsep + "/usr/bin:/bin",
        "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "BLENDER_USER_CONFIG": str(private_dir / "blender"),
        "XDG_CONFIG_HOME": str(private_dir / "config"),
        "XDG_DATA_HOME": str(private_dir / "data"),
        "XDG_CACHE_HOME": str(private_dir / "cache"),
        # Recent/recovery paths use this override, not XDG_DATA_HOME alone.
        "NARWHAL3D_DATA_DIR": str(private_dir / "data/narwhal3d"),
        "NARWHAL3D_SETTINGS_FILE": str(private_dir / "preferences.json"),
        "NARWHAL3D_LOG_DIR": str(private_dir / "logs"),
        "NARWHAL3D_VERSION": runtime.version,
        "NARWHAL3D_DEFER_FIRST_PRESENT": "1", "NARWHAL3D_STATUSBAR_HEIGHT": "37",
        "NARWHAL_AUTOMATION_OPT_IN": OPT_IN,
        "NARWHAL_AUTOMATION_ROOT": str(runtime.root),
        "NARWHAL_AUTOMATION_FD": str(fd), "NARWHAL_AUTOMATION_SESSION": nonce,
    })
    # Match the normal source launch's fixed native-core precedence without
    # invoking its desktop installer or welcome process (which would own the FD).
    target = {"linux-x86_64": "x86_64-unknown-linux-gnu",
              "linux-arm64": "aarch64-unknown-linux-gnu",
              "macos-x86_64": "x86_64-apple-darwin",
              "macos-arm64": "aarch64-apple-darwin"}[runtime.key]
    suffix = ".dylib" if runtime.key.startswith("macos-") else ".so"
    for core in ("pure", "selection", "cdr", "geometry", "platform"):
        name = f"libnarwhal_{core}_core{suffix}"
        for candidate in (runtime.root / "narwhal3d/native" / name, bin_dir / name,
                          runtime.root / "native" / f"{core}_core" / "target" / target / "release" / name,
                          runtime.root / "native" / f"{core}_core" / "target/release" / name):
            if candidate.is_file():
                env[f"NARWHAL_{core.upper()}_CORE"] = str(candidate)
                break
    document = bin_dir / "narwhal-document-core"
    if document.is_file():
        env["NARWHAL_DOCUMENT_CORE"] = str(document)
    if runtime.key.startswith("linux-"):
        libraries = runtime.app.parent / "5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs"
        if libraries.is_dir():
            env["LD_LIBRARY_PATH"] = str(libraries)
    return env


class LogFilter:
    """Bounded line assembly; emit ONLY exact static status lines, once each."""
    def __init__(self, output: TextIO):
        self.output = output
        self.pending = bytearray()
        self.dropping = False
        self.seen: set[str] = set()

    def feed(self, chunk: bytes):
        for byte in chunk:
            if byte == 10:
                if not self.dropping:
                    text = self.pending.decode("ascii", errors="replace")
                    if text in SAFE_LOGS and text not in self.seen:
                        self.seen.add(text)
                        print(text, file=self.output, flush=True)
                self.pending.clear()
                self.dropping = False
            elif not self.dropping:
                if len(self.pending) < 256:
                    self.pending.append(byte)
                else:
                    self.pending.clear()
                    self.dropping = True


def reap_owned(children: Sequence[subprocess.Popen[bytes]]):
    """Signal only Popen handles created here; never process names/groups."""
    alive = []
    for child in children:
        if child.poll() is None:
            try:
                child.terminate()
            except ProcessLookupError:
                pass
            alive.append(child)
    deadline = time.monotonic() + 2.0
    for child in alive:
        try:
            child.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                child.kill()
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 2.0
    unreaped = False
    for child in children:
        try:
            child.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            unreaped = True
    if unreaped:
        # Still reap the other child if one is in OS-level uninterruptible sleep.
        raise LaunchError("owned child did not exit after bounded kill")


class PrivateConfig:
    """Retain configuration if the app might live; never kill it for cleanup.

    No daemon/PID-based janitor: once detached we cannot safely prove the app has
    exited. Retained directories require manual cleanup after closing the app.
    """
    def __init__(self):
        self.path = Path(tempfile.mkdtemp(prefix="narwhal-mcp-"))
        self.app = None
        self.launch_attempted = False

    def __enter__(self):
        return self

    def __exit__(self, *_exception):
        if self.app is None:
            if self.launch_attempted:
                return  # An interrupted/failed spawn may be ambiguous.
        elif self.app.poll() is None:
            return
        else:
            self.app.wait(timeout=0)  # Already exited, never signal it.
        shutil.rmtree(self.path)


def supervise(runtime: Runtime, config: PrivateConfig, input_stream: BinaryIO,
              output_stream: BinaryIO, errors: TextIO, startup_timeout: float = 60.0):
    """Byte relay + process lifecycle only, not a Python implementation of MCP.

    Rust inherits client stdout directly. App stdout/stderr are DEVNULL, safe
    after this launcher exits. At most 64 KiB of input is queued during boot.
    The close-only lifetime socket observes revocation without retaining a copy
    of Rust's bridge endpoint (which would hide cancellation/EOF from the host).
    """
    unix_only()  # Must precede socketpair and ALL child launches.
    children = []
    streams = []
    app_end = server_end = None
    life_app = life_parent = None
    old_signals = {}
    cancelled = False
    input_fd = input_stream.fileno()
    was_blocking = os.get_blocking(input_fd)

    def cancel(_signum, _frame):
        nonlocal cancelled
        cancelled = True

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            old_signals[signum] = signal.signal(signum, cancel)
        os.set_blocking(input_fd, False)
        nonce = secrets.token_hex(16)
        app_end, server_end = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        server_end.setblocking(False)
        life_parent, life_app = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        life_parent.setblocking(False)
        env = application_environment(runtime, config.path, app_end.fileno(), nonce, os.environ)
        env["NARWHAL_AUTOMATION_LIFETIME_FD"] = str(life_app.fileno())
        config.launch_attempted = True
        app = config.app = subprocess.Popen(
            [str(runtime.app), "--factory-startup", "--disable-autoexec", "--python", str(runtime.bootstrap)],
            cwd=runtime.root, env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            pass_fds=(app_end.fileno(), life_app.fileno()), close_fds=True, start_new_session=True,
        )
        # Deliberately exclude the GUI from the terminate/kill list, even before
        # READY: a presentation watchdog or delayed prelude can make that ambiguous.
        life_app.close()
        life_app = None
        app_end.close()
        app_end = None
        expected = READY_PREFIX + nonce.encode("ascii") + b"\n"
        ready = bytearray()
        pending = bytearray()
        server = None
        server_input_fd = -1
        deadline = time.monotonic() + startup_timeout
        filters = {}
        with selectors.SelectSelector() as poll:
            poll.register(input_fd, selectors.EVENT_READ, "input")
            poll.register(server_end, selectors.EVENT_READ, "ready")
            poll.register(life_parent, selectors.EVENT_READ, "lifetime")
            writing = False
            while True:
                if cancelled:
                    return 130
                if app.poll() is not None:
                    raise LaunchError("private application exited; read session closed")
                if server is not None and server.poll() is not None:
                    return server.returncode
                if server is None and time.monotonic() >= deadline:
                    raise LaunchError("application readiness timed out; check Main's bootstrap hook and UI readiness")
                for key, _events in poll.select(0.05):
                    if key.data == "input":
                        try:
                            data = os.read(input_fd, min(4096, MAX_PENDING + 1 - len(pending)))
                        except BlockingIOError:
                            continue
                        if not data:
                            return 0  # EOF also works during boot and a pending read.
                        pending.extend(data)
                        if len(pending) > MAX_PENDING:
                            raise LaunchError("client input exceeded bounded startup/relay queue")
                    elif key.data == "ready":
                        assert server_end is not None
                        chunk = server_end.recv(len(expected) + 1 - len(ready))
                        if not chunk:
                            raise LaunchError("application host disconnected before readiness")
                        ready.extend(chunk)
                        if not expected.startswith(ready):
                            raise LaunchError("invalid application readiness handshake")
                        if bytes(ready) == expected:
                            poll.unregister(server_end)
                            server = subprocess.Popen(
                                [str(runtime.server)], cwd=runtime.root,
                                env={"NARWHAL_AUTOMATION_FD": str(server_end.fileno()),
                                     "NARWHAL_AUTOMATION_SESSION": nonce},
                                stdin=subprocess.PIPE, stdout=output_stream, stderr=subprocess.PIPE,
                                pass_fds=(server_end.fileno(),), close_fds=True, start_new_session=True,
                            )
                            children.append(server)
                            assert server.stdin is not None and server.stderr is not None
                            server_input_fd = server.stdin.fileno()
                            streams.extend((server.stdin, server.stderr))
                            os.set_blocking(server.stdin.fileno(), False)
                            os.set_blocking(server.stderr.fileno(), False)
                            filters[server.stderr.fileno()] = LogFilter(errors)
                            poll.register(server.stderr, selectors.EVENT_READ, "log")
                            server_end.close()
                            server_end = None
                    elif key.data == "lifetime":
                        if life_parent.recv(1):
                            raise LaunchError("invalid host lifetime signal")
                        print("narwhal automation: read_session_closed_relaunch_session",
                              file=errors, flush=True)
                        return 0
                    elif key.data == "log":
                        chunk = os.read(key.fd, 4096)
                        if not chunk:
                            poll.unregister(key.fileobj)
                        else:
                            filters[key.fd].feed(chunk)
                    elif key.data == "write":
                        try:
                            sent = os.write(key.fd, pending[:4096])
                            del pending[:sent]
                        except BlockingIOError:
                            pass
                if server is not None:
                    if pending and not writing:
                        poll.register(server_input_fd, selectors.EVENT_WRITE, "write")
                        writing = True
                    elif not pending and writing:
                        poll.unregister(server_input_fd)
                        writing = False
    finally:
        if life_app is not None:
            life_app.close()
        if life_parent is not None:
            life_parent.close()
        if app_end is not None:
            app_end.close()
        if server_end is not None:
            server_end.close()
        for stream in streams:
            stream.close()
        try:
            reap_owned(children)
        finally:
            os.set_blocking(input_fd, was_blocking)
            for signum, handler in old_signals.items():
                signal.signal(signum, handler)


def _timeout(value: str) -> float:
    try:
        result = float(value)
        if 1 <= result <= 120:
            return result
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("startup timeout must be 1..120 seconds")


def main(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description=CONSENT)
    parser.add_argument("--read-only", action="store_true", required=True,
                        help="explicit consent to the read-only disclosure and private app lifetime above")
    parser.add_argument("--startup-timeout", type=_timeout, default=60.0,
                        help="app readiness deadline, 1..120 seconds (default: 60)")
    args = parser.parse_args(argv)
    try:
        unix_only()
        runtime = resolve_runtime()
        print(CONSENT, file=sys.stderr, flush=True)
        with PrivateConfig() as config:
            return supervise(runtime, config, sys.stdin.buffer, sys.stdout.buffer,
                             sys.stderr, args.startup_timeout)
    except LaunchError as exc:
        print("narwhal automation: " + str(exc), file=sys.stderr, flush=True)
        return 1
    except (OSError, subprocess.SubprocessError, ValueError):
        print("narwhal automation: launch/transport failed (details suppressed)", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
