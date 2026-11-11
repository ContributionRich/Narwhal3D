"""Optional bounded Rust pick solver; Python owns prompts and CPlane integration.

Library selection is process-cached: set NARWHAL_PURE_CORE before first use, or
call reload_binding() explicitly at an idle qualification boundary. Mouse events
never inspect the environment or construct ctypes arguments. Missing support
selects compatibility before execution; native failures are never replayed.
"""
from __future__ import annotations

import ctypes
from functools import partial
import math
import os
from pathlib import Path
import sys
import threading

_D = ctypes.POINTER(ctypes.c_double)


class Event(ctypes.Structure):
    _fields_ = [("byte_size", ctypes.c_uint32), ("operation", ctypes.c_uint32),
                ("input", ctypes.c_double * 32), ("output", ctypes.c_double * 4)]


_binding = None
_loaded = False
_lock = threading.Lock()
_local = threading.local()
UNAVAILABLE = object()
_UNSET = object()


def binding():
    global _loaded, _binding
    if _loaded:
        return _binding
    with _lock:
        if _loaded:
            return _binding
        explicit = os.environ.get("NARWHAL_PURE_CORE")
        name = ("narwhal_pure_core.dll" if sys.platform == "win32" else
                "libnarwhal_pure_core.dylib" if sys.platform == "darwin" else
                "libnarwhal_pure_core.so")
        path = (Path(explicit).expanduser() if explicit else
                Path(__file__).resolve().parent / "native" / name)
        fn = None
        try:
            lib = ctypes.CDLL(str(path))
            abi = lib.narwhal_pure_core_abi_version
            abi.argtypes, abi.restype = [], ctypes.c_uint32
            capability = lib.narwhal_pure_core_pickconstraint_v1_capability
            capability.argtypes, capability.restype = [], ctypes.c_uint32
            if abi() == 1 and capability() == 1:
                fn = lib.narwhal_pure_core_pickconstraint_v1
                fn.argtypes = [ctypes.c_uint32, _D, ctypes.c_size_t, _D, ctypes.c_size_t]
                fn.restype = ctypes.c_int32
                event = lib.narwhal_pure_core_pickconstraint_event_v1
                event.argtypes, event.restype = [ctypes.POINTER(Event)], ctypes.c_int32
                event_cap = lib.narwhal_pure_core_pickconstraint_event_v1_capability
                event_cap.argtypes, event_cap.restype = [], ctypes.c_uint32
                if event_cap() != ctypes.sizeof(Event):
                    fn = None
                else:
                    fn.event = event
        except (OSError, AttributeError):
            fn = None
        _binding, _loaded = fn, True
        return fn


def reload_binding():
    """Requalify after explicitly changing library selection; not an event hook."""
    global _loaded
    with _lock:
        _loaded = False
    return binding()


def _frame(plane):
    # Snapshot mutable host frame vectors too; retaining a list in a signature
    # would compare that list with itself after an in-place change.
    return (tuple(plane.origin), tuple(plane.xaxis), tuple(plane.yaxis), tuple(plane.zaxis))


def _failure(op, detail):
    raise RuntimeError(f"native pickconstraint v1 operation {op} failed ({detail}); no Python replay")


class Solver:
    def __init__(self, fn):
        self.fn = fn
        self.packet = Event()
        self.packet.byte_size = ctypes.sizeof(Event)
        self.inputs, self.output = self.packet.input, self.packet.output
        event = getattr(fn, "event", None)
        self.call = partial(event, ctypes.pointer(self.packet)) if event is not None else None
        # The checked legacy entry remains useful for injected error witnesses.
        if self.call is None:
            inp, out = ctypes.cast(self.inputs, _D), ctypes.cast(self.output, _D)
            self.calls = tuple(partial(fn, ctypes.c_uint32(op), inp, ctypes.c_size_t(32),
                                       out, ctypes.c_size_t(4)) for op in range(5))
        self.signature = None
        self.fixed = _UNSET

    def plane(self, frame):
        a = self.inputs
        for offset, values in zip((6, 9, 12, 15), frame):
            a[offset], a[offset+1], a[offset+2] = values

    def run(self, op, cursor=None):
        a = self.inputs
        if cursor is not None:
            a[3], a[4], a[5] = cursor
        if self.call is not None:
            self.packet.operation = op
            code = self.call()
        else:
            code = self.calls[op]()
        if code == 1 and op in (1, 2, 3):
            return None
        if code != 0:
            _failure(op, f"status {code}")
        if op == 3:
            r = self.output[3]
            if not math.isfinite(r) or r < 1e-12:
                _failure(op, "invalid radius output")
            return r
        x, y, z = self.output[0], self.output[1], self.output[2]
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            _failure(op, "non-finite output")
        return (x, y, z)


def constraint(owner, op, cursor=None):
    fn = binding()
    if fn is None:
        return UNAVAILABLE
    # Direction/distance-only operations do not use a CPlane. In particular,
    # do not derive its normal on every distance-only mouse move.
    try:
        frame = _frame(owner.plane) if owner.angle is not None or owner.lock == "circle" else None
    except AttributeError:
        return UNAVAILABLE
    solver = owner._native_solver
    if solver is None or solver.fn is not fn:
        solver = owner._native_solver = Solver(fn)
    signature = (owner.base, frame, owner.distance, owner.incremental, owner.angle,
                 owner.lock, owner.lock_dir, owner.lock_radius)
    if signature != solver.signature:
        solver.signature = None
        solver.fixed = _UNSET
        a = solver.inputs
        a[0], a[1], a[2] = owner.base
        # Clear the previous event before FIXED validation (a prior failed
        # non-finite cursor must not poison a subsequently changed prompt).
        a[3], a[4], a[5] = 0.0, 0.0, 0.0
        solver.plane(frame if frame is not None else ((0.0, 0.0, 0.0),) * 4)
        a[18], a[19], a[20] = owner.lock_dir or (0.0, 0.0, 0.0)
        a[23] = owner.distance if owner.distance is not None else 0.0
        a[24] = owner.distance is not None
        a[25] = owner.angle if owner.angle is not None else 0.0
        a[26], a[27] = owner.angle is not None, owner.incremental
        a[28] = {None: 0, "direction": 1, "circle": 2}[owner.lock]
        a[29] = owner.lock_radius if owner.lock_radius is not None else 0.0
        if owner.distance is not None and not owner.incremental and (owner.angle is not None or owner.lock == "direction"):
            solver.fixed = solver.run(1)
            if solver.fixed is None:
                _failure(1, "status 1: missing fixed result")
        solver.signature = signature
    if op in (0, 1) and solver.fixed is not _UNSET:
        # Match the native finite-input contract even when the numerical
        # result is already determined and no FFI crossing is necessary.
        if cursor is not None and not (math.isfinite(cursor[0]) and math.isfinite(cursor[1]) and math.isfinite(cursor[2])):
            _failure(op, "status -2")
        return solver.fixed
    return solver.run(op, cursor)


def ortho(base, point, plane, angle):
    fn = binding()
    if fn is None:
        return UNAVAILABLE
    try:
        frame = _frame(plane)
    except AttributeError:
        return UNAVAILABLE
    solver = getattr(_local, "ortho", None)
    if solver is None or solver.fn is not fn:
        solver = _local.ortho = Solver(fn)
    signature = (tuple(base), frame, angle)
    if signature != solver.signature:
        solver.signature = None
        solver.inputs[0], solver.inputs[1], solver.inputs[2] = base
        solver.plane(frame)
        solver.inputs[30] = angle
        solver.signature = signature
    return solver.run(4, point)
