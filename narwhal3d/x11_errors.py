"""Keep a recoverable X11 protocol error from terminating the application.

MEASURED (crash 2026-11-08 18:37:23, core dump + journal): an asynchronous ``BadWindow``
on ``X_ChangeProperty`` for a foreign window (resource 0x200e47, not the application
window) arrived as a cancelled Save As file dialog closed. It reached Xlib's DEFAULT
error handler, which calls ``exit()``; exit handlers then aborted (``terminate called
after throwing std::runtime_error``) and unsaved work was lost.

Such errors concern another client's window, not our state. Installing a handler that
logs and continues is what toolkits do. Python is only the ctypes bridge to the
platform's error-handler hook; no geometry or application logic lives here.
"""
from __future__ import annotations

import ctypes
import os
import sys
import time

#: Recent errors, newest last: (monotonic time, error code, request code, resource id).
recent = []
_state = {"handler": None, "library": None, "count": 0}


class _XErrorEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("display", ctypes.c_void_p),
                ("resourceid", ctypes.c_ulong), ("serial", ctypes.c_ulong),
                ("error_code", ctypes.c_ubyte), ("request_code", ctypes.c_ubyte),
                ("minor_code", ctypes.c_ubyte)]


_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(_XErrorEvent))


def _on_error(_display, event):
    try:
        e = event.contents
        _state["count"] += 1
        recent.append((time.monotonic(), int(e.error_code), int(e.request_code),
                       int(e.resourceid)))
        del recent[:-50]
        if _state["count"] <= 100:
            print(f"[narwhal3d] X error ignored: code {e.error_code} request "
                  f"{e.request_code}.{e.minor_code} resource {e.resourceid:#x} "
                  f"serial {e.serial}", file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001 -- an error handler must never raise into Xlib
        pass
    return 0


def install(library_name="libX11.so.6"):
    """Install the non-fatal handler once. Returns True when it is installed."""
    if _state["handler"] is not None:
        return True
    if not sys.platform.startswith("linux") or not os.environ.get("DISPLAY"):
        return False
    try:
        library = ctypes.CDLL(library_name)
        library.XSetErrorHandler.restype = ctypes.c_void_p
        library.XSetErrorHandler.argtypes = [_HANDLER]
    except (OSError, AttributeError):
        return False
    handler = _HANDLER(_on_error)
    library.XSetErrorHandler(handler)
    _state.update(handler=handler, library=library)  # the callback must stay alive
    return True


def installed():
    return _state["handler"] is not None
