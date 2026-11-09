"""Opt-in local usage monitor for finding broken code paths and slowdowns.

OURS: the Bug Report panel's "Monitor usage" checkbox is OFF by default. While it is on,
this module appends compact JSON Lines records to a local file: session start/stop,
each command and its duration, command errors, Python tracebacks/diagnostic lines from
the session log, and UI stalls (gaps in the interface heartbeat). Nothing is uploaded.
Geometry, picture pixels, document contents and typed report text are never recorded;
command transcript lines are truncated.

This is UI/orchestration glue: it receives events from Blender's timer and the command
dispatcher and appends small records. It performs no geometry or high-cardinality
computation, so it has no performance-sensitive core to place in Rust.
"""
from __future__ import annotations

from collections import deque
import datetime
import json
import os
import threading
import time
import uuid

#: A heartbeat gap this long (seconds) is a stall the user would notice.
STALL_SECONDS = 0.35
#: Commands at least this slow are listed as slow in summaries.
SLOW_COMMAND_SECONDS = 1.0
#: Stop writing (and say so once) at this file size; a long workday is far below it.
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TEXT = 600
MAX_DIAGNOSTICS = 500
_DIAGNOSTIC_MARKERS = ("Traceback (most recent call last)", "Error:", "Exception",
                       " failed", "FAILED", "WARNING")


def _clip(value, limit=MAX_TEXT):
    text = str(value if value is not None else "")
    return text if len(text) <= limit else text[:limit] + "..."


class UsageMonitor:
    """One process's monitor. Disabled until `enable` is called."""

    def __init__(self, clock=time.perf_counter, wall=time.time):
        self._clock, self._wall = clock, wall
        self._lock = threading.Lock()
        self.path = None
        self._fh = None
        self._written = 0
        self._full = False
        self._last_beat = None
        self._diagnostics = 0
        self._partial = ""
        self._in_traceback = []
        self.counts = {"commands": 0, "errors": 0, "slow_commands": 0, "stalls": 0,
                       "diagnostics": 0}
        self.notable = deque(maxlen=40)

    @property
    def enabled(self):
        return self._fh is not None

    def enable(self, folder, **session):
        """Start a new session file under `folder`; returns its path. Idempotent."""
        with self._lock:
            if self._fh is not None:
                return self.path
            os.makedirs(folder, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            # Re-enabling within the same second must start a new session file.
            path = os.path.join(folder, f"usage-{stamp}-{os.getpid()}-{uuid.uuid4().hex[:8]}.jsonl")
            self._fh = open(path, "x", encoding="utf-8")
            self.path, self._written, self._full = path, 0, False
            self._last_beat, self._diagnostics = None, 0
            self._partial, self._in_traceback = "", []
            for key in self.counts:
                self.counts[key] = 0
            self.notable.clear()
        self.record("session_start", **session)
        return path

    def disable(self, reason="disabled"):
        if self._fh is None:
            return
        self.record("session_stop", reason=reason, counts=dict(self.counts))
        with self._lock:
            try:
                self._fh.close()
            finally:
                self._fh = None
                self._last_beat = None

    def record(self, kind, **fields):
        """Append one event. A full disk or file must never interrupt modeling."""
        if self._fh is None:
            return False
        event = {"t": round(self._wall(), 3), "kind": kind}
        event.update(fields)
        try:
            line = json.dumps(event, separators=(",", ":"), default=str) + "\n"
        except (TypeError, ValueError):
            line = json.dumps({"t": event["t"], "kind": kind, "unserializable": True}) + "\n"
        with self._lock:
            if self._fh is None or self._full:
                return False
            size = len(line.encode("utf-8"))
            if self._written + size > MAX_FILE_BYTES:
                self._full = True
                line = json.dumps({"t": event["t"], "kind": "limit_reached",
                                   "bytes": self._written}) + "\n"
            try:
                self._fh.write(line)
                self._fh.flush()
                self._written += len(line.encode("utf-8"))
            except OSError:
                return False
        return True

    def command(self, name, seconds, *, ok=True, message=None, phase="run"):
        """Record one command dispatch (start, typed input, click or Enter)."""
        if not self.enabled:
            return
        seconds = float(seconds)
        self.counts["commands"] += 1
        if not ok:
            self.counts["errors"] += 1
        slow = seconds >= SLOW_COMMAND_SECONDS
        if slow:
            self.counts["slow_commands"] += 1
        self.record("command", name=_clip(name, 80), phase=phase, seconds=round(seconds, 4),
                    ok=bool(ok), message=_clip(message, 300))
        if slow or not ok:
            self.notable.append(f"{'ERROR' if not ok else 'slow'} {name} "
                                f"{seconds:.2f}s: {_clip(message, 120)}")

    def heartbeat(self, context=None):
        """Called on an interface timer. Returns the stall in seconds, or None."""
        if not self.enabled:
            return None
        now = self._clock()
        previous, self._last_beat = self._last_beat, now
        if previous is None:
            return None
        gap = now - previous
        if gap < STALL_SECONDS:
            return None
        self.counts["stalls"] += 1
        fields = dict(context() if callable(context) else (context or {}))
        self.record("stall", seconds=round(gap, 4), **fields)
        self.notable.append(f"stall {gap:.2f}s during {fields.get('active_command') or 'idle'}"
                            f" (last: {_clip(fields.get('last_line'), 80)})")
        return gap

    def observe_output(self, text):
        """Session-log text: keep tracebacks whole and other diagnostic lines bounded."""
        if not self.enabled or not text:
            return
        self._partial += str(text)
        lines = self._partial.split("\n")
        self._partial = lines.pop()[-4000:]
        for line in lines:
            if self._in_traceback:
                self._in_traceback.append(line)
                # A traceback ends with its unindented exception line.
                if line and not line.startswith((" ", "\t", "Traceback")):
                    self._diagnostic("traceback", "\n".join(self._in_traceback[-40:]))
                    self._in_traceback = []
                continue
            if line.startswith("Traceback (most recent call last)"):
                self._in_traceback = [line]
            elif any(marker in line for marker in _DIAGNOSTIC_MARKERS):
                self._diagnostic("diagnostic", line)

    def _diagnostic(self, kind, text):
        if self._diagnostics >= MAX_DIAGNOSTICS:
            return
        self._diagnostics += 1
        self.counts["diagnostics"] += 1
        self.record(kind, text=_clip(text, 4000))
        first = text.strip().splitlines()[-1] if text.strip() else kind
        self.notable.append(f"{kind}: {_clip(first, 120)}")

    def summary(self, limit=8):
        """Short lines for a bug report: counts plus the most recent notable events."""
        if not self.path:
            return []
        c = self.counts
        head = (f"{c['commands']} command(s), {c['errors']} error(s), "
                f"{c['slow_commands']} slow command(s), {c['stalls']} stall(s), "
                f"{c['diagnostics']} diagnostic(s)")
        return [f"Log: {self.path}", head] + list(self.notable)[-limit:]


monitor = UsageMonitor()


def _dispatch_label(app, text):
    active = getattr(app.active_command, "name", None)
    if active:
        return active, "input"
    return ((text or "").strip().split(None, 1) or ["(empty)"])[0], "start"


def instrument(app, target=None):
    """Time `app`'s outermost command dispatches while monitoring.

    Installs reversible INSTANCE wrappers for `feed_line` and `start_command`; the
    AppState class and every caller stay unchanged. Nested calls (feed_line starting a
    command) are recorded once. Returns True when newly installed.
    """
    target = target or monitor
    if getattr(app, "_usage_monitor_wrapped", None) is not None:
        return False
    originals = {"feed_line": app.feed_line, "start_command": app.start_command}
    depth = [0]

    def timed(label, phase, run):
        if not target.enabled or depth[0]:
            return run()
        before = len(app.history)
        started = time.perf_counter()
        depth[0] += 1
        try:
            result = run()
        except BaseException as exc:
            target.command(label, time.perf_counter() - started, ok=False,
                           message=f"{type(exc).__name__}: {exc}", phase=phase)
            raise
        finally:
            depth[0] -= 1
        history = list(app.history)
        # The transcript is capped; then the newest lines are the evidence.
        lines = history[before:] if len(history) > before else history[-3:]
        failed = any(str(line).startswith(("Error:", "Bad input:"))
                     or "Traceback" in str(line) for line in lines)
        target.command(label, time.perf_counter() - started, ok=not failed,
                       message=" | ".join(str(line) for line in lines[-3:]), phase=phase)
        return result

    def feed_line(text):
        label, phase = _dispatch_label(app, text)
        return timed(label, phase, lambda: originals["feed_line"](text))

    def start_command(name, rest=None):
        return timed(name, "start", lambda: originals["start_command"](name, rest))

    app.feed_line, app.start_command = feed_line, start_command
    app._usage_monitor_wrapped = originals
    return True


def uninstrument(app):
    """Remove `instrument`'s wrappers, restoring the class methods exactly."""
    originals = getattr(app, "_usage_monitor_wrapped", None)
    if originals is None:
        return False
    for name in originals:
        try:
            delattr(app, name)
        except AttributeError:
            pass
    del app._usage_monitor_wrapped
    return True
