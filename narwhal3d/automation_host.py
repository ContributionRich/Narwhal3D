"""OURS: opt-in lifecycle glue for the normal application's read-only MCP host.

Python is needed for Blender's main-thread timers and AppState identity. Rust
continues to own MCP parsing, admission, cancellation and the read allowlist.
Importing this module has no lifecycle effects. Main owns calling attach/close.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import socket
import sys
import time

from .automation_bridge import MainThreadBridge, POLL_INTERVAL, register_timer, unregister_timer
from .automation_tools import ApplicationReader, require_main_thread

OPT_IN = "new-app-read-only-v1"
READY_PREFIX = b"narwhal-automation-ready-v1 "
REVOKED = "narwhal automation: document_changed_relaunch_session"
CLOSED = "narwhal automation: read_session_closed_relaunch_session"
_ROOT = Path(__file__).resolve().parents[1]
_session = None


class _Host:
    def __init__(self, endpoint, app_state, version, nonce, lifetime):
        self._lifetime = lifetime
        self._app = app_state
        self._model = app_state.model
        self._file = self._model.file
        self._bridge = MainThreadBridge(endpoint, ApplicationReader(app_state, version), nonce)
        self._endpoint = endpoint
        self._ready = bytearray(READY_PREFIX + nonce.encode("ascii") + b"\n")
        self._deadline = time.monotonic() + 120.0
        # Register this wrapper, not the unguarded bridge pump.
        self._timer_callback = self.pump

    @property
    def closed(self):
        return self._bridge.closed

    def _close_transport(self):
        self._bridge.close()
        self._lifetime.close()  # EOF only: revoke MCP, never request app shutdown.

    def close(self):
        require_main_thread()
        self._close_transport()
        try:
            unregister_timer(self)
        except Exception:
            # Blender teardown may already have removed timers. Any surviving
            # callback sees a closed bridge and cannot perform another read.
            pass

    def pump(self):
        require_main_thread()
        if self.closed:
            return None
        try:
            from . import state
            if (state.app_state is not self._app or self._app.model is not self._model
                    or self._model.file is not self._file):
                self._close_transport()
                print(REVOKED, file=sys.stderr, flush=True)
                return None
            if self._ready:
                if time.monotonic() >= self._deadline:
                    self._close_transport()
                    return None
                # Registration starts asynchronous layout. Do not announce a usable
                # host until the normal UI has actually signalled readiness.
                if os.environ.get("NARWHAL3D_UI_READY") != "1":
                    if self._bridge._eof():
                        self._close_transport()
                        return None
                    return POLL_INTERVAL
                try:
                    sent = self._endpoint.send(self._ready)
                except (BlockingIOError, InterruptedError):
                    return POLL_INTERVAL
                if not sent:
                    self._close_transport()
                    return None
                del self._ready[:sent]
                return POLL_INTERVAL
            result = self._bridge.pump()
            if result is None:
                self._close_transport()
                print(CLOSED, file=sys.stderr, flush=True)
            return result
        except Exception:
            self._close_transport()
            return None


def attach_inherited(app_state, version):
    """Main-thread hook AFTER normal add-on registration; returns host or None.

    Consumes the environment capability once. No opt-in means a no-op. An invalid
    opt-in fails closed with a static ValueError, never an exception payload.
    Main must pass the real state singleton and package version, not bl_info.
    No listener, process creation, arbitrary code or model mutation occurs here.
    """
    global _session
    require_main_thread()
    if os.environ.get("NARWHAL_AUTOMATION_OPT_IN") != OPT_IN:
        return None
    values = {key: os.environ.pop(key, "") for key in (
        "NARWHAL_AUTOMATION_OPT_IN", "NARWHAL_AUTOMATION_FD",
        "NARWHAL_AUTOMATION_SESSION", "NARWHAL_AUTOMATION_ROOT",
        "NARWHAL_AUTOMATION_LIFETIME_FD")}
    endpoint = lifetime = None
    try:
        if os.name != "posix" or sys.platform.startswith(("win", "cygwin", "msys")):
            raise ValueError
        fd_text = values["NARWHAL_AUTOMATION_FD"]
        life_text = values["NARWHAL_AUTOMATION_LIFETIME_FD"]
        if any(not re.fullmatch(r"[0-9]{1,10}", text) or int(text) < 3
               for text in (fd_text, life_text)) or int(fd_text) == int(life_text):
            raise ValueError
        # Both are launcher-owned connected capabilities; never wrap stdin/out/err.
        endpoint = socket.socket(fileno=int(fd_text))
        lifetime = socket.socket(fileno=int(life_text))
        for channel in (endpoint, lifetime):
            os.set_inheritable(channel.fileno(), False)
            if (channel.family != socket.AF_UNIX
                    or channel.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM
                    or channel.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)):
                raise ValueError
            channel.getpeername()
        if values["NARWHAL_AUTOMATION_ROOT"] != str(_ROOT):
            raise ValueError
        if not re.fullmatch(r"[0-9a-f]{32}", values["NARWHAL_AUTOMATION_SESSION"]):
            raise ValueError
        if (type(version) is not str or not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,127}", version)
                or version != os.environ.get("NARWHAL3D_VERSION")):
            raise ValueError
        from . import state
        if state.app_state is not app_state or app_state.model is None or app_state.model.file is None:
            raise ValueError
        if _session is not None:
            raise ValueError
        _session = _Host(endpoint, app_state, version,
                         values["NARWHAL_AUTOMATION_SESSION"], lifetime)
        register_timer(_session)
        return _session
    except Exception:
        if _session is not None:
            _session.close()
        if endpoint is not None:
            endpoint.close()
        if lifetime is not None:
            lifetime.close()
        raise ValueError("invalid_automation_launch") from None


def close():
    """Main calls BEFORE unregister/reload/document replacement; idempotent.

    Consent is intentionally not renewable inside this app. A fresh CLI launch
    is required even if an old document is subsequently restored.
    """
    require_main_thread()
    if _session is not None:
        was_open = not _session.closed
        _session.close()
        if was_open:
            print(CLOSED, file=sys.stderr, flush=True)
