"""Opaque Python payload/lifetime adapter for Rust history ABI1; no bpy.

Rust alone owns ordering, grouping, record fields and navigation transactions.
Python is necessary for host callbacks and actual opaque Python object references.
No geometry is copied/serialized, no library is built/staged, and runtime failures
never switch backend. See native/application_core/{HISTORY.md,README.md}.
"""
from __future__ import annotations

import ctypes
import importlib.util
import json
import os
import sys
import threading
import weakref
from collections.abc import Sequence
from pathlib import Path
from types import MappingProxyType

__all__ = ["HistoryError", "NativeRecord", "NativeUndoStack", "try_create"]

_MIN_CAPACITY = 4096
_MAX_OUTPUT = 8 * 1024 * 1024  # OURS: enough for all 100,000 IDs or one bounded name.
_MAX_INPUT = 1024 * 1024
_MAX_ATTEMPTS = 3
_BYTE_P = ctypes.POINTER(ctypes.c_ubyte)
_READ_OPS = frozenset(("info", "record", "records", "garbage", "names"))
_FATAL_CODES = frozenset(("panic", "registry_poisoned", "invalid_history"))
_CLEANUP_OPS = _READ_OPS | frozenset(("ack_garbage", "release_record",
                                   "retain_record", "drop_payload"))
_NULL_OPS = frozenset(("info", "close", "drop_payload", "abandon", "enable",
                       "retain_record", "release_record", "set_before", "set_after",
                       "set_name", "abort_nav", "clear", "ack_garbage"))
_HEADER_UINT = frozenset(("generation", "state_id", "current_serial", "top_serial",
                          "next_serial", "depth", "undo_count", "redo_count",
                          "gc_pending", "record_count", "payload_count"))
_HEADER_BOOL = frozenset(("enabled", "uncertain", "captured"))
_HEADER_IDS = frozenset(("open_record", "undo_record", "redo_record"))


class HistoryError(RuntimeError):
    """Native refusal or ABI failure; code is safe to report without payloads."""

    def __init__(self, code):
        self.code = str(code)
        super().__init__(self.code)


class HistoryProtocolError(HistoryError):
    """Transport/protocol failure: a mutation may already have happened."""


def _uint(value):
    return type(value) is int and 0 <= value < 1 << 64


def _id(value):
    return _uint(value) and value > 0


def _nullable_id(value):
    return value is None or _id(value)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate reply key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("non-JSON number")


def _shape(value, fields):
    return (type(value) is dict and value.keys() == fields.keys()
            and all(check(value[key]) for key, check in fields.items()))


def _names(value):
    if type(value) is not list or len(value) > 100000 or not all(isinstance(name, str) for name in value):
        return False
    try:
        return sum(len(name.encode('utf-8')) for name in value) <= 16 * 1024 * 1024
    except UnicodeError:
        return False


def _validate(op, reply):
    if type(reply) is not dict:
        raise HistoryProtocolError("invalid_reply")
    if reply.get("status") == "error":
        if set(reply) != {"status", "code"} or not isinstance(reply["code"], str) or not reply["code"]:
            raise HistoryProtocolError("invalid_reply")
        raise HistoryError(reply["code"])
    if set(reply) != {"status", "result", "info"} or reply["status"] != "ok":
        raise HistoryProtocolError("invalid_reply")
    info, result = reply["info"], reply["result"]
    if op == "close":
        valid_header = info is None
    else:
        valid_header = (type(info) is dict
                        and set(info) == _HEADER_UINT | _HEADER_BOOL | _HEADER_IDS
                        and all(_uint(info[k]) for k in _HEADER_UINT)
                        and all(type(info[k]) is bool for k in _HEADER_BOOL)
                        and all(_nullable_id(info[k]) for k in _HEADER_IDS)
                        and _id(info["state_id"]) and _id(info["next_serial"]))
    if op in _NULL_OPS:
        valid_result = result is None
    elif op in ("create", "register_payload", "commit_nav", "begin"):
        key = {"create": "history", "register_payload": "payload",
               "commit_nav": "record", "begin": "serial"}[op]
        valid_result = _shape(result, {key: _uint if op == "begin" else _id})
    elif op in ("capture", "end"):
        valid_result = _shape(result, {"captured" if op == "capture" else "committed":
                                      lambda v: type(v) is bool})
    elif op in ("records", "garbage"):
        key = "records" if op == "records" else "payloads"
        maximum = 100000 if op == "records" else 128
        valid_result = _shape(result, {key: lambda v: type(v) is list
                              and len(v) <= maximum and all(_id(i) for i in v)
                              and len(v) == len(set(v))})
    elif op == "names":
        valid_result = _shape(result, {"names": _names})
    elif op == "record":
        valid_result = _shape(result, {"serial": _id, "name": lambda v: isinstance(v, str),
                                       "before": _nullable_id, "after": _nullable_id})
    elif op == "prepare_nav":
        valid_result = result is None or _shape(result, {"ticket": _id, "record": _id,
                                                        "target": _nullable_id})
    else:
        valid_result = False
    if not valid_header or not valid_result:
        raise HistoryProtocolError("invalid_reply")
    return reply


def _call(library, request):
    try:
        encoded = json.dumps(request, ensure_ascii=False, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise HistoryError("invalid_request") from exc
    if len(encoded) > _MAX_INPUT:
        raise HistoryError("request_too_large")
    source = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
    capacity = _MIN_CAPACITY
    op = request["op"]
    # The total native name budget is 16 MiB; JSON escaping can expand it sixfold.
    maximum = 128 * 1024 * 1024 if op == 'names' else _MAX_OUTPUT
    for _ in range(_MAX_ATTEMPTS):
        if type(capacity) is not int or not 4096 <= capacity <= maximum:
            raise HistoryProtocolError("invalid_capacity")
        output = (ctypes.c_ubyte * capacity)()
        try:
            size = library.narwhal_history_json(source, len(encoded), output, capacity)
        except (OSError, TypeError, ctypes.ArgumentError) as exc:
            raise HistoryProtocolError("transport_error") from exc
        if type(size) is not int or size <= 0:
            raise HistoryProtocolError("invalid_reply_size")
        if size > maximum:
            raise HistoryProtocolError("reply_too_large")
        if size > capacity:
            if op not in _READ_OPS:
                raise HistoryProtocolError("mutation_reply_too_large")
            capacity = size
            continue
        try:
            # Never dereference a claimed size until it fits our actual allocation.
            reply = json.loads(ctypes.string_at(output, size).decode("utf-8"),
                               object_pairs_hook=_object, parse_constant=_reject_constant)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise HistoryProtocolError("invalid_reply") from exc
        return _validate(op, reply)
    raise HistoryProtocolError("reply_retry_limit")


def _session_module():
    """Use the canonical module identity without importing the bpy package init."""
    name = "narwhal3d.native_document_session"
    module = sys.modules.get(name)
    if module is None:
        path = Path(__file__).resolve().with_name("native_document_session.py")
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise HistoryError("session_loader_unavailable")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return module


def _configure(library, *, optional=False):
    abi = getattr(library, "narwhal_history_abi", None)
    call = getattr(library, "narwhal_history_json", None)
    if abi is None and call is None and optional:
        return None  # A valid old application core has neither history export.
    if abi is None or call is None:
        raise HistoryError("history_exports_unavailable")
    try:
        abi.argtypes = []
        abi.restype = ctypes.c_uint32
        if abi() != 1:
            raise HistoryError("history_abi_must_be_1")
        call.argtypes = [_BYTE_P, ctypes.c_size_t, _BYTE_P, ctypes.c_size_t]
        call.restype = ctypes.c_ssize_t
    except (AttributeError, OSError, TypeError, ctypes.ArgumentError) as exc:
        raise HistoryError("history_exports_invalid") from exc
    return library


def try_create(limit=100):
    """Return None only for an absent default core or an old core without history.

    NARWHAL_HISTORY_CORE overrides discovery, including when empty (an error).
    Otherwise use native_document_session.load_library's existing discovery policy.
    Present malformed/incompatible libraries and every runtime error are explicit.
    """
    explicit = os.environ.get("NARWHAL_HISTORY_CORE")
    if explicit is not None:
        if not explicit:
            raise HistoryError("empty_NARWHAL_HISTORY_CORE")
        try:
            library = ctypes.CDLL(str(Path(explicit).expanduser().resolve()))
        except OSError as exc:
            raise HistoryError("cannot_load_history_core") from exc
        library = _configure(library)
    else:
        library = _session_module().load_library()
        if library is None:
            return None
        library = _configure(library, optional=True)
        if library is None:
            return None
    return NativeUndoStack(limit, library=library)


def _cleanup_stack(library, handle):
    try:
        _call(library, {"op": "close", "history": handle})
    except Exception:
        pass  # Only GC finalization is best effort; explicit close reports errors.


def _cleanup_record(stack_ref, record):
    stack = stack_ref()
    if stack is not None:
        try:
            with stack._lock:
                if not stack._closed and not stack._broken:
                    stack._request("release_record", record=record)
                    stack._drain()
        except Exception:
            # A failed lease release must not let later edits run on unknown state.
            stack._broken = True


class NativeRecord:
    """Live leased native record, including after pruning; serial is read-only."""

    # __weakref__ is initialized by Python's object machinery, not by __init__.
    __slots__ = ("__weakref__", "_finalizer", "_id", "_stack")  # pyright: ignore[reportUninitializedInstanceVariable]

    def __init__(self, stack, record):
        self._stack, self._id = stack, record
        stack._request("retain_record", record=record)
        # A strong finalizer argument would root stack -> cache -> record forever.
        self._finalizer = weakref.finalize(self, _cleanup_record, weakref.ref(stack), record)

    def _read(self):
        return self._stack._request("record", record=self._id)["result"]

    @property
    def serial(self):
        return self._read()["serial"]

    @property
    def name(self):
        return self._read()["name"]

    @name.setter
    def name(self, value):
        with self._stack._lock:
            self._stack._request("set_name", record=self._id, name=value)
            self._stack._drain()

    def _payload(self, field):
        with self._stack._lock:
            return self._stack._payload(self._read()[field])

    @property
    def before(self):
        return self._payload("before")

    @before.setter
    def before(self, value):
        with self._stack._lock:
            self._stack._attach("set_before", value, record=self._id)

    @property
    def after(self):
        return self._payload("after")

    @after.setter
    def after(self, value):
        with self._stack._lock:
            self._stack._attach("set_after", value, record=self._id)

    def __repr__(self):
        return f"NativeRecord(#{self.serial}, {self.name!r})"


class _RecordSequence(Sequence[NativeRecord]):
    """Read-only projection; membership/order are fetched from Rust, never cached."""

    def __init__(self, stack, side):
        self._stack, self._side = stack, side

    def __len__(self):
        return self._stack._info()[self._side + "_count"]

    def __iter__(self):
        with self._stack._lock:
            records = [self._stack._view(i) for i in self._stack._record_ids(self._side)]
        return iter(records)

    def __reversed__(self):
        with self._stack._lock:
            ids = self._stack._record_ids(self._side)
            records = [self._stack._view(i) for i in reversed(ids)]
        return iter(records)

    def __getitem__(self, index):
        with self._stack._lock:
            ids = self._stack._record_ids(self._side)[index]
            if isinstance(index, slice):
                return [self._stack._view(i) for i in ids]
            return self._stack._view(ids)

    def __eq__(self, other):
        if isinstance(other, (list, _RecordSequence)):
            return list(self) == list(other)
        return NotImplemented


class NativeUndoStack:
    """UndoStack-compatible facade with Rust-owned state identity and generation."""

    def __init__(self, limit=100, *, library):
        self._library = _configure(library)
        self.__limit = max(1, int(limit))
        self._lock = threading.RLock()
        self._closed = self._broken = self._draining = False
        self._arena = {}  # payload ID -> actual opaque object, exactly one strong ref
        self._identity = {}  # id(object) -> payload ID (arena prevents ID reuse)
        self._cache = {}  # STRONG while native timeline owns the record
        self._views = weakref.WeakValueDictionary()
        reply = _call(self._library, {"op": "create", "limit": self.__limit})
        self._handle = reply["result"]["history"]
        self._header = MappingProxyType(dict(reply["info"]))
        self._finalizer = weakref.finalize(self, _cleanup_stack, self._library, self._handle)

    @property
    def backend(self):
        return "rust"

    def _request(self, op, **fields):
        with self._lock:
            if self._closed:
                raise HistoryError("history_closed")
            if self._broken and op != "close":
                raise HistoryProtocolError("history_protocol_failed")
            if self._draining and op not in _CLEANUP_OPS:
                raise HistoryError("history_cleanup_busy")
            try:
                reply = _call(self._library, {**fields, "op": op, "history": self._handle})
            except HistoryError as exc:
                if isinstance(exc, HistoryProtocolError) or exc.code in _FATAL_CODES:
                    self._broken = True
                raise
            except BaseException:
                # Decoding/allocation can fail after Rust has already committed.
                self._broken = True
                raise
            try:
                # Publish only validated headers, including lifetime/read replies.
                # Copy so the returned reply cannot mutate this immutable snapshot.
                self._header = (None if reply["info"] is None
                                else MappingProxyType(dict(reply["info"])))
                return reply
            except BaseException:
                # Even a valid wire reply is unusable if its header cannot publish.
                self._broken = True
                raise

    def _info(self):
        """Last native header, without FFI. All mutations use this facade's lock.

        Private _request("info") remains an explicit diagnostic refresh; ordinary
        callers never need one because every successful reply replaces the header.
        Previously returned snapshots stay immutable and do not change in place.
        """
        with self._lock:
            if self._closed:
                raise HistoryError("history_closed")
            if self._broken:
                raise HistoryProtocolError("history_protocol_failed")
            return self._header

    def _record_ids(self, side):
        return self._request("records", side=side)["result"]["records"]

    def _view(self, record: int) -> NativeRecord:
        view = self._views.get(record)
        if view is None:
            view = NativeRecord(self, record)
            self._views[record] = view
        self._cache[record] = view
        return view

    def _prune_cache(self):
        if not self._cache:
            return
        # Only native membership decides which strong wrapper references to drop.
        owned = set(self._record_ids("undo")) | set(self._record_ids("redo"))
        opened = self._info()["open_record"]
        if opened is not None:
            owned.add(opened)
        for record in tuple(self._cache):
            if record not in owned:
                del self._cache[record]

    def _payload(self, payload):
        if payload is None:
            return None
        try:
            return self._arena[payload]
        except KeyError as exc:
            self._broken = True
            raise HistoryProtocolError("unknown_payload") from exc

    def _register(self, value):
        if value is None:
            return None, False
        payload = self._identity.get(id(value))
        if payload is not None:
            return payload, False
        payload = self._request("register_payload")["result"]["payload"]
        try:
            self._arena[payload] = value
            self._identity[id(value)] = payload
            return payload, True
        except BaseException:
            # A dict insertion may fail before OR after inserting. The argument
            # pins value throughout rollback; remove both indexes independently.
            was_draining = self._draining
            self._draining = True
            rollback_failed = False
            try:
                try:
                    self._identity.pop(id(value), None)
                except BaseException:
                    rollback_failed = True
                try:
                    self._arena.pop(payload, None)
                except BaseException:
                    rollback_failed = True
                try:
                    self._request("drop_payload", payload=payload)
                except BaseException:
                    rollback_failed = True
                if rollback_failed:
                    self._broken = True
            finally:
                self._draining = was_draining
            raise  # Preserve the original allocation/insertion error.

    def _forget(self, payload):
        value = self._arena[payload]
        del self._identity[id(value)]
        del self._arena[payload]

    def _drop(self, payload):
        was_draining = self._draining
        self._draining = True
        try:
            self._request("drop_payload", payload=payload)
            self._forget(payload)
        except BaseException:
            self._broken = True
            raise
        finally:
            self._draining = was_draining

    def _drain(self):
        if self._draining or self._broken or self._closed:
            return
        if not self._info()["gc_pending"]:
            return
        retired = []
        self._draining = True
        try:
            while self._info()["gc_pending"]:
                ids = self._request("garbage", limit=128)["result"]["payloads"]
                if not ids:
                    break
                if any(i not in self._arena for i in ids):
                    self._broken = True
                    raise HistoryProtocolError("unknown_garbage")
                # Pin the entire batch BEFORE ack. No destructor may observe an
                # acknowledged ID still registered in either Python index.
                retired = [self._arena[payload] for payload in ids]
                self._request("ack_garbage", payloads=ids)
                for payload in ids:
                    self._forget(payload)
                retired.clear()  # Reentrant mutations/close remain blocked here.
        except BaseException:
            self._broken = True
            raise
        finally:
            retired.clear()
            self._draining = False

    def _attach(self, op, value, **fields):
        payload, fresh = self._register(value)
        field = "current" if op == "prepare_nav" else "payload"
        try:
            result = self._request(op, **fields, **{field: payload})["result"]
        except BaseException:
            if fresh and not self._broken:
                try:
                    self._drop(payload)
                except Exception:
                    self._broken = True
            raise
        refused = (op == "capture" and not result["captured"]) or (op == "prepare_nav" and result is None)
        if fresh and refused:
            self._drop(payload)
        self._drain()
        return result

    @property
    def _undo(self):
        return _RecordSequence(self, "undo")

    @property
    def _redo(self):
        return _RecordSequence(self, "redo")

    @property
    def _open(self):
        with self._lock:
            record = self._info()["open_record"]
            return None if record is None else self._view(record)

    @property
    def _limit(self):
        return self.__limit

    @property
    def _depth(self):
        return self._info()["depth"]

    @property
    def _next_serial(self):
        return self.next_serial

    @property
    def enabled(self):
        return self._info()["enabled"]

    @enabled.setter
    def enabled(self, value):
        with self._lock:
            self._request("enable", value=bool(value))
            self._drain()

    @property
    def recording(self):
        return self._info()["open_record"] is not None

    @property
    def captured(self):
        return self._info()["captured"]

    @property
    def current_serial(self):
        return self._info()["current_serial"]

    @property
    def top_serial(self):
        return self._info()["top_serial"]

    @property
    def next_serial(self):
        return self._info()["next_serial"]

    @property
    def state_id(self):
        return self._info()["state_id"]

    @property
    def generation(self):
        return self._info()["generation"]

    @property
    def uncertain(self):
        """Conservative dirty-state guard, also True when broken or closed.

        Closing invalidates state certification rather than claiming a clean
        document. These safety/UI flags remain readable after close; identity,
        serial and other state reads still raise, as do all mutations.
        """
        with self._lock:
            if self._closed or self._broken:
                return True
            return self._info()["uncertain"]

    @property
    def can_undo(self):
        """UI eligibility; False for uncertain, broken or closed histories."""
        with self._lock:
            return not self.uncertain and bool(self._info()["undo_count"])

    @property
    def can_redo(self):
        """UI eligibility; False for uncertain, broken or closed histories."""
        with self._lock:
            return not self.uncertain and bool(self._info()["redo_count"])

    def _name(self, side):
        with self._lock:
            record = self._info()[side + "_record"]
            return None if record is None else self._view(record).name

    @property
    def undo_name(self):
        return self._name("undo")

    @property
    def redo_name(self):
        return self._name("redo")

    def undo_names(self):
        return self._request('names', side='undo')['result']['names']

    def redo_names(self):
        return self._request('names', side='redo')['result']['names']

    def begin(self, name):
        with self._lock:
            result = self._request("begin", name=str(name or "Change"))["result"]["serial"]
            self._drain()
            return result

    def capture(self, snapshot):
        with self._lock:
            header = self._info()
            if not header['uncertain'] and header['captured']:
                return False
            result = self._attach("capture", snapshot)
            assert result is not None  # _validate already checked the capture shape.
            return result["captured"]

    def end(self):
        with self._lock:
            old_open = self._info()["open_record"]
            reply = self._request("end")
            committed = reply["result"]["committed"]
            if committed:
                self._prune_cache()
            elif reply["info"]["open_record"] is None:
                self._cache.pop(old_open, None)
            self._drain()
            return committed

    def abandon(self):
        with self._lock:
            old_open = self._info()["open_record"]
            self._request("abandon")
            self._cache.pop(old_open, None)
            self._drain()

    def _navigate(self, direction, current, restore, failure_state):
        with self._lock:
            nav = self._attach("prepare_nav", current, direction=direction)
            if nav is None:
                return None
            try:
                record = self._view(nav["record"])
                target = self._payload(nav["target"])
                if restore is not None:
                    restore(target)
                self._request("commit_nav", ticket=nav["ticket"])
            except BaseException as exc:
                if self._broken:
                    # Commit may have succeeded despite an unreadable reply. Do
                    # not classify it as host rollback or attempt a known abort.
                    raise
                unknown = False
                if failure_state is not None:
                    try:
                        unknown = failure_state(exc) == "unknown"
                    except BaseException:
                        unknown = True  # A failed certification cannot establish rollback.
                try:
                    self._request("abort_nav", ticket=nav["ticket"], unknown=unknown)
                    self._drain()
                except Exception:
                    self._broken = True
                raise  # Preserve the host's original exception, including its identity.
            self._drain()
            return record

    def undo(self, current, *, restore=None, failure_state=None):
        return self._navigate("undo", current, restore, failure_state)

    def redo(self, current, *, restore=None, failure_state=None):
        return self._navigate("redo", current, restore, failure_state)

    def clear(self):
        with self._lock:
            self._request("clear")
            self._cache.clear()
            self._drain()

    def close(self):
        with self._lock:
            if not self._closed:
                self._request("close")
                self._closed = True
                self._finalizer.detach()
                self._cache.clear()
                self._views.clear()
                self._identity.clear()
                self._arena.clear()

    def __repr__(self):
        info = self._info()
        return (f"NativeUndoStack(undo={info['undo_count']}, redo={info['redo_count']}, "
                f"recording={info['open_record'] is not None})")
