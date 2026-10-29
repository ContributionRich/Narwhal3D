"""Private inherited AF_UNIX stream channel; no listener, workers or auto-start.

This is the minimal Blender-side framing/validation boundary for the Rust owner,
not a second public protocol server. Frames include their terminating newline in
MAX_FRAME. Invalid envelopes/authentication/replays close silently; valid requests
with refused arguments or failed reads receive static error codes. No contents are
logged. All lifecycle calls must run on Python's main thread.
"""
from __future__ import annotations

import hmac
import json
import re
import socket as sockets
import time

from .automation_tools import ReadError, SAFE_ERRORS, TOOLS, require_main_thread, validate_arguments

MAX_FRAME = 65_536
RECV_BUDGET = 4096
SEND_BUDGET = 4096
POLL_INTERVAL = 0.02
_PENDING_TIMEOUT = 5.0
_SESSION = re.compile(r"[0-9a-f]{32}\Z")
_FIELDS = frozenset({"session", "seq", "tool", "arguments"})


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("invalid_envelope")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("invalid_envelope")


def _encode(reply):
    # Bound retained serialized output, including the newline. ApplicationReader
    # returns bounded metadata; iterencode also avoids accumulating arbitrary replies.
    output = bytearray()
    encoder = json.JSONEncoder(ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    for part in encoder.iterencode(reply):
        chunk = part.encode("ascii")
        if len(output) + len(chunk) + 1 > MAX_FRAME:
            raise ReadError("result_too_large")
        output.extend(chunk)
    output.extend(b"\n")
    return output


class MainThreadBridge:
    def __init__(self, socket, reader, session: str):
        require_main_thread()
        if type(session) is not str or not _SESSION.fullmatch(session):
            raise ValueError("invalid_session")
        if (socket.family != sockets.AF_UNIX
                or socket.getsockopt(sockets.SOL_SOCKET, sockets.SO_TYPE) != sockets.SOCK_STREAM):
            raise ValueError("invalid_socket")
        socket.getpeername()  # A connected inherited endpoint, never a listener.
        socket.setblocking(False)
        self._socket = socket
        self._reader = reader
        self._session = session
        self._last_seq = 0
        self._input = bytearray()
        self._output = bytearray()
        self._pending_since = None
        self._closed = False
        self._timer_callback = self.pump

    @property
    def closed(self):
        return self._closed

    def close(self):
        require_main_thread()
        if not self._closed:
            self._closed = True
            self._input.clear()
            self._output.clear()
            self._pending_since = None
            self._socket.close()

    def _eof(self):
        # A bounded peek notices cancellation before dispatch when EOF is visible.
        # A FIN behind more unread bytes cannot be detected by peeking; the Rust
        # owner must discard late replies and relaunch the session after cancellation.
        try:
            return self._socket.recv(1, sockets.MSG_PEEK) == b""
        except (BlockingIOError, InterruptedError):
            return False

    def pump(self):
        """One request at most; <=4096 received/probed bytes and one bounded send.

        Pending input/output expires after five seconds; idle sessions do not. This
        cannot preempt a synchronous native read, so the Rust deadline remains the
        authority for timeout/cancellation. A closed session is never reusable.
        """
        require_main_thread()
        if self._closed:
            return None
        try:
            now = time.monotonic()
            if (self._pending_since is not None
                    and now - self._pending_since >= _PENDING_TIMEOUT):
                self.close()
                return None
            if self._eof():
                self.close()
                return None
            # Reserve two one-byte probes within the receive budget. Also bound
            # pipelined input as a whole rather than keeping an unbounded frame queue.
            capacity = min(RECV_BUDGET - 2, MAX_FRAME - len(self._input))
            if capacity:
                try:
                    chunk = self._socket.recv(capacity)
                except (BlockingIOError, InterruptedError):
                    chunk = None
                if chunk == b"":
                    self.close()
                    return None
                if chunk:
                    self._input.extend(chunk)
                    if self._pending_since is None:
                        self._pending_since = now
            if self._eof():
                self.close()
                return None
            if len(self._input) >= MAX_FRAME and b"\n" not in self._input:
                self.close()
                return None
            # Never dispatch another request while any previous reply is pending.
            if not self._output and b"\n" in self._input:
                end = self._input.index(b"\n") + 1
                frame = bytes(self._input[:end - 1])
                del self._input[:end]
                if not self._dispatch(frame):
                    self.close()
                    return None
            if (self._pending_since is not None
                    and time.monotonic() - self._pending_since >= _PENDING_TIMEOUT):
                self.close()
                return None
            if self._output:
                try:
                    sent = self._socket.send(self._output[:SEND_BUDGET])
                except (BlockingIOError, InterruptedError):
                    sent = None
                if sent == 0:
                    self.close()
                    return None
                if sent:
                    del self._output[:sent]
            if not self._input and not self._output:
                self._pending_since = None
            return POLL_INTERVAL
        except Exception:
            # Transport/framing failures expose neither native errors nor payloads.
            self.close()
            return None

    def _dispatch(self, frame):
        try:
            request = json.loads(frame.decode("utf-8"), object_pairs_hook=_pairs,
                                 parse_constant=_constant)
            if type(request) is not dict or set(request) != _FIELDS:
                return False
            session, seq = request["session"], request["seq"]
            if type(session) is not str or not _SESSION.fullmatch(session):
                return False
            if not hmac.compare_digest(session, self._session):
                return False
            if type(seq) is not int or seq <= self._last_seq:
                return False
            tool, arguments = request["tool"], request["arguments"]
            if type(tool) is not str or tool not in TOOLS or type(arguments) is not dict:
                return False
        except Exception:
            return False
        self._last_seq = seq
        reply = {"session": self._session, "seq": seq}
        try:
            validate_arguments(tool, arguments)
            result = self._reader.read(tool, arguments)
            if type(result) is not dict:
                raise ReadError("read_failed")
            reply["result"] = result
            self._output = _encode(reply)
        except Exception as exc:
            code = exc.args[0] if isinstance(exc, ReadError) and exc.args else None
            code = code if type(code) is str and code in SAFE_ERRORS else "read_failed"
            if code == "result_too_large":
                return False
            self._output = _encode({"session": self._session, "seq": seq, "error": code})
        return True


def register_timer(bridge):
    """Explicit caller opt-in; never called by importing or constructing a bridge."""
    require_main_thread()
    if bridge.closed:
        raise ValueError("bridge_closed")
    import bpy

    if not bpy.app.timers.is_registered(bridge._timer_callback):
        bpy.app.timers.register(bridge._timer_callback, first_interval=POLL_INTERVAL)


def unregister_timer(bridge):
    """Remove the optional timer; socket ownership remains with bridge.close()."""
    require_main_thread()
    import bpy

    if bpy.app.timers.is_registered(bridge._timer_callback):
        bpy.app.timers.unregister(bridge._timer_callback)
