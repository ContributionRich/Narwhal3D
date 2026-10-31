"""Python ownership/ctypes adapter for application-core ABI 1 (no bpy imports).

Rust owns the table, ordinal matching, transactions and immutable states. Python
is required only for library discovery, ABI marshalling and object lifetimes;
there is no Python table authority, build-on-import or runtime fallback here.
See native/application_core/README.md for the measured native wire contract.

``rows()`` materializes a fresh list of (key, value) tuples for legacy callers.
``request()``, ``get()`` and ``at()`` return validated native reply dictionaries.
Raw snapshot/ticket requests transfer cleanup responsibility to the caller;
prefer ``snapshot()`` for automatic lifetime management.
"""
from __future__ import annotations

import ctypes
import json
import os
import platform
import sys
import threading
import weakref
from pathlib import Path

__all__ = ["load_library", "Session", "Snapshot", "SessionUnavailable", "SessionError"]

_ROOT = Path(__file__).resolve().parents[1]
_ENV_PATH = "NARWHAL_APPLICATION_CORE"
_ABI_VERSION = 1
_MIN_CAPACITY = 4096
_MAX_OUTPUT_BYTES = 512 * 1024 * 1024
_MAX_INPUT_BYTES = 128 * 1024 * 1024
_MAX_ATTEMPTS = 3
_ByteP = ctypes.POINTER(ctypes.c_ubyte)
_READ_OPS = frozenset(("info", "rows", "get", "at", "snapshot_rows", "equal", "document_user_text_count"))
_OPS = _READ_OPS | frozenset(("create", "close", "snapshot", "release_snapshot",
                             "prepare", "commit", "abort", "restore"))


class SessionUnavailable(RuntimeError):
    """The native library is absent, unloadable or incompatible (before create)."""


class SessionError(RuntimeError):
    """Native operation or adapter protocol failure; ``code`` is always a string."""

    def __init__(self, code):
        self.code = str(code)
        super().__init__(self.code)


def _library_name():
    if sys.platform == "win32":
        return "narwhal_application_core.dll"
    if sys.platform == "darwin":
        return "libnarwhal_application_core.dylib"
    return "libnarwhal_application_core.so"


def _platform_tag():
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, sys.platform)
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    return f"{system}-{machine}"


def _configure(library):
    try:
        abi = library.narwhal_session_abi
        abi.argtypes = []
        abi.restype = ctypes.c_uint32
        if abi() != _ABI_VERSION:
            raise SessionUnavailable("application-core ABI must be 1")
        call = library.narwhal_session_json
        call.argtypes = [_ByteP, ctypes.c_size_t, _ByteP, ctypes.c_size_t]
        call.restype = ctypes.c_ssize_t
    except (AttributeError, OSError, TypeError, ctypes.ArgumentError) as exc:
        raise SessionUnavailable("application-core ABI exports unavailable") from exc
    return library


def load_library():
    """Load explicit env path, else adjacent native slot, else platform artifact.

    Only missing *default* candidates return None. An explicit path (even empty),
    or an installed but unloadable/incompatible library, raises SessionUnavailable.
    No development /tmp path, build invocation or fallback implementation is used.
    """
    explicit = os.environ.get(_ENV_PATH)
    if explicit is not None:
        if not explicit:
            raise SessionUnavailable("empty NARWHAL_APPLICATION_CORE path")
        candidates = (Path(explicit).expanduser(),)
    else:
        name = _library_name()
        candidates = (_ROOT / "narwhal3d" / "native" / name,
                      _ROOT / "artifacts" / "application-core" / _platform_tag() / name)
    for path in candidates:
        if explicit is None and not path.exists():
            continue
        try:
            return _configure(ctypes.CDLL(str(path.resolve())))
        except OSError as exc:
            raise SessionUnavailable(f"cannot load application-core: {path}") from exc
    return None


def _uint(value):
    return type(value) is int and 0 <= value <= (1 << 64) - 1


def _id(value):
    return _uint(value) and value > 0


def _row(value):
    return (isinstance(value, list) and len(value) == 2
            and all(isinstance(item, str) for item in value))


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate reply field")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("non-JSON number")


def _validate_reply(op, reply):
    if not isinstance(reply, dict):
        raise SessionError("invalid_reply")
    if reply.get("status") == "error":
        if (set(reply) != {"status", "code"} or not isinstance(reply["code"], str)
                or not reply["code"]):
            raise SessionError("invalid_reply")
        raise SessionError(reply["code"])
    if reply.get("status") != "ok":
        raise SessionError("invalid_reply")
    fields = {key: value for key, value in reply.items() if key != "status"}
    keys = set(fields)
    if op in ("create", "info", "commit", "restore"):
        expected = {"generation", "state_id"}
        if op in ("create", "info"):
            expected.add("count")
        if op == "create":
            expected.add("session")
        valid = (keys == expected and all(_uint(v) for v in fields.values())
                 and _id(fields.get("state_id")))
        if op == "create":
            valid = valid and _id(fields["session"]) and fields["generation"] == 0
    elif op == "document_user_text_count":
        valid = keys == {"count"} and _uint(fields["count"])
    elif op in ("close", "release_snapshot", "abort"):
        valid = not fields
    elif op in ("rows", "snapshot_rows"):
        valid = (keys == {"rows"} and isinstance(fields["rows"], list)
                 and all(_row(row) for row in fields["rows"]))
    elif op == "get":
        valid = keys == {"found", "key", "value"} and type(fields["found"]) is bool
        if valid:
            valid = (all(isinstance(fields[k], str) for k in ("key", "value"))
                     if fields["found"] else fields["key"] is None and fields["value"] is None)
    elif op == "at":
        valid = keys == {"found", "row"} and type(fields["found"]) is bool
        if valid:
            valid = _row(fields["row"]) if fields["found"] else fields["row"] is None
    elif op == "snapshot":
        valid = keys == {"snapshot", "state_id"} and all(_id(v) for v in fields.values())
    elif op == "equal":
        valid = keys == {"equal"} and type(fields["equal"]) is bool
    elif op == "prepare":
        valid = keys == {"changed", "ticket"} and type(fields["changed"]) is bool
        if valid:
            valid = _id(fields["ticket"]) if fields["changed"] else fields["ticket"] is None
    else:
        valid = False
    if not valid:
        raise SessionError("invalid_reply")
    return reply


def _valid_capacity(capacity):
    return (type(capacity) is int
            and _MIN_CAPACITY <= capacity <= min(_MAX_OUTPUT_BYTES, sys.maxsize))


def _allocate_output(capacity):
    return (ctypes.c_ubyte * capacity)()


def _call(library, request, counts=None):
    """Own all ABI memory for this call; never return a pointer or buffer view."""
    try:
        encoded = json.dumps(request, ensure_ascii=False, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise SessionError("invalid_request") from exc
    if len(encoded) > _MAX_INPUT_BYTES:
        raise SessionError("request_too_large")
    source = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
    op = request["op"]
    capacity = _MIN_CAPACITY
    for _ in range(_MAX_ATTEMPTS):
        if not _valid_capacity(capacity):
            raise SessionError("reply_too_large")
        output = _allocate_output(capacity)
        if counts is not None:
            counter = op if isinstance(op, str) and op in _OPS else "unknown"
            counts[counter] = counts.get(counter, 0) + 1
        try:
            size = library.narwhal_session_json(source, len(encoded), output, capacity)
        except (OSError, ctypes.ArgumentError) as exc:
            raise SessionError("transport_error") from exc
        if type(size) is not int or size <= 0:
            raise SessionError("invalid_reply_size")
        if size > _MAX_OUTPUT_BYTES or size > sys.maxsize:
            raise SessionError("reply_too_large")
        if size > capacity:
            if not isinstance(op, str) or op not in _READ_OPS:
                # A mutation may already have executed. Replaying it is unsafe.
                raise SessionError("mutation_reply_too_large")
            capacity = size
            continue
        try:
            reply = json.loads(ctypes.string_at(output, size).decode("utf-8"),
                               object_pairs_hook=_object, parse_constant=_reject_constant)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise SessionError("invalid_reply") from exc
        return _validate_reply(op, reply)
    raise SessionError("reply_retry_limit")


def _cleanup_session(library, handle):
    try:
        _call(library, {"op": "close", "session": handle})
    except Exception:
        # Only best-effort finalization suppresses failures. close() never does.
        pass


def _cleanup_snapshot(session, handle):
    try:
        with session._lock:
            if not session._closed:
                session.request("release_snapshot", snapshot=handle)
    except Exception:
        pass


class Session:
    """One native document metadata session. Explicit close is idempotent.

    Native failures raise SessionError, never switch backend. ``call_counts`` is
    a copy of bounded per-operation ABI call counters, containing no document data.
    """

    def __init__(self, rows=(), library=None):
        self._library = load_library() if library is None else _configure(library)
        if self._library is None:
            raise SessionUnavailable("application-core library not installed")
        self._lock = threading.RLock()
        self._call_counts = {}
        self._closed = False
        reply = _call(self._library, {"op": "create", "rows": list(rows)}, self._call_counts)
        self._handle = reply["session"]
        self._finalizer = weakref.finalize(self, _cleanup_session, self._library, self._handle)

    @property
    def backend(self):
        return "rust"

    @property
    def handle(self):
        return self._handle

    @property
    def call_counts(self):
        with self._lock:
            return dict(self._call_counts)

    def request(self, op, **fields):
        """Send one native operation, always using this session's own identity."""
        with self._lock:
            if self._closed:
                raise SessionError("invalid_session")
            reply = _call(self._library, {**fields, "op": op, "session": self._handle},
                          self._call_counts)
            if op == "close":
                self._closed = True
                self._finalizer.detach()
            return reply

    def info(self):
        return self.request("info")

    def rows(self):
        return [tuple(row) for row in self.request("rows")["rows"]]

    def get(self, key, exact=False):
        return self.request("get", key=key, exact=exact)

    def at(self, index):
        return self.request("at", index=index)

    def snapshot(self):
        with self._lock:
            reply = self.request("snapshot")
            return Snapshot(self, reply["snapshot"], reply["state_id"])

    def prepare(self, key, value, generation=None):
        with self._lock:
            if generation is None:
                generation = self.info()["generation"]
            return self.request("prepare", generation=generation, key=key, value=value)

    def commit(self, ticket):
        return self.request("commit", ticket=ticket)

    def abort(self, ticket):
        return self.request("abort", ticket=ticket)

    def validate_snapshot(self, snapshot):
        if not isinstance(snapshot, Snapshot) or snapshot._session is not self:
            raise SessionError("snapshot_owner_mismatch")
        self.request("equal", left=snapshot.handle, right=snapshot.handle)

    def restore(self, snapshot):
        self.validate_snapshot(snapshot)
        return self.request("restore", snapshot=snapshot.handle)

    def close(self):
        with self._lock:
            if not self._closed:
                self.request("close")


class Snapshot:
    """Immutable native reference, created by Session.snapshot(), not a table copy.

    Keeps the Session alive; explicit Session.close() invalidates its handles.
    Same-session equality uses native state equality, cross-session/list equality
    materializes ordered rows only for that comparison (never caches them).
    """

    # Slots are initialized through object.__setattr__; weakref owns __weakref__.
    __slots__ = ("_session", "_handle", "_state_id", "_finalizer", "__weakref__")  # pyright: ignore[reportUninitializedInstanceVariable]
    # Defining __eq__ deliberately leaves snapshots unhashable.

    def __init__(self, session, handle, state_id):
        object.__setattr__(self, "_session", session)
        object.__setattr__(self, "_handle", handle)
        object.__setattr__(self, "_state_id", state_id)
        object.__setattr__(self, "_finalizer",
                           weakref.finalize(self, _cleanup_snapshot, session, handle))

    def __setattr__(self, name, value):
        raise AttributeError("Snapshot is immutable")

    def __delattr__(self, name):
        raise AttributeError("Snapshot is immutable")

    @property
    def handle(self):
        return self._handle

    @property
    def state_id(self):
        return self._state_id

    def rows(self):
        return [tuple(row) for row in self._session.request(
            "snapshot_rows", snapshot=self._handle)["rows"]]

    def __iter__(self):
        return iter(self.rows())

    def __eq__(self, other):
        if isinstance(other, Snapshot):
            if self._session is other._session:
                return self._session.request("equal", left=self._handle,
                                             right=other._handle)["equal"]
            return self.rows() == other.rows()
        if isinstance(other, list):
            # Accept both legacy tuple rows and JSON list rows, without caching.
            return self.rows() == [tuple(row) if isinstance(row, (tuple, list)) else row
                                   for row in other]
        return NotImplemented

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self
