"""The app's SESSION LOG: everything printed this session, kept in a file. Pure.

The launcher may run without a terminal, so printed diagnostics and tracebacks need a
persistent session log that a bug report can optionally attach.

`install` tees Python's stdout and stderr into ``session.log`` -- which is where our
``print`` lines and every Python traceback (operators and draw handlers report through
``sys.stderr``) go -- and keeps the previous session as ``session.prev.log``. The Bug
Report panel attaches its tail when asked (`tail`).
"""
from __future__ import annotations

import os
import sys

LOG_NAME = "session.log"
PREV_NAME = "session.prev.log"
#: OURS: how much of the log a report carries -- enough for a traceback and its lead-up
TAIL_LINES = 80


def log_dir():
    """$NARWHAL3D_LOG_DIR, a per-run temp folder under a no-dialog test harness,
    or ~/.local/share/narwhal3d/logs."""
    if os.environ.get("NARWHAL3D_LOG_DIR"):
        return os.environ["NARWHAL3D_LOG_DIR"]
    if os.environ.get("NARWHAL3D_NO_DIALOGS"):
        import tempfile
        return os.path.join(tempfile.gettempdir(), f"narwhal3d_test_logs_{os.getpid()}")
    return os.path.join(os.path.expanduser("~"), ".local", "share", "narwhal3d", "logs")


def log_path():
    return os.path.join(log_dir(), LOG_NAME)


class _Tee:
    """A text stream that writes to the original stream AND the log file."""

    def __init__(self, stream, fh):
        self._stream, self._fh = stream, fh

    def write(self, s):
        try:
            self._fh.write(s)
            self._fh.flush()
        except Exception:  # noqa: BLE001  (a full disk must not take the app down)
            pass
        return self._stream.write(s) if self._stream is not None else len(s)

    def flush(self):
        try:
            self._fh.flush()
        except Exception:  # noqa: BLE001
            pass
        if self._stream is not None:
            self._stream.flush()

    def __getattr__(self, name):            # isatty, fileno, encoding, ...
        return getattr(self._stream, name)


_installed = None


def install(path=None):
    """Start logging this session. Idempotent; returns the log path, or None on failure."""
    global _installed
    if _installed is not None:
        return _installed
    path = path or log_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            os.replace(path, os.path.join(os.path.dirname(path), PREV_NAME))
        fh = open(path, "w", encoding="utf-8", errors="replace")
    except OSError:
        return None
    sys.stdout = _Tee(sys.stdout, fh)
    sys.stderr = _Tee(sys.stderr, fh)
    _installed = path
    return path


def available(path=None):
    """True when there is a non-empty log to attach."""
    path = path or _installed or log_path()
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def tail(path=None, lines=TAIL_LINES):
    """The last `lines` lines of the log, or [] when there is none."""
    path = path or _installed or log_path()
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()[-lines:]
    except OSError:
        return []
