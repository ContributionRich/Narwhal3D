#!/usr/bin/env python3
"""Standalone, no-build application-core binding tests (256 MiB address-space cap).

NARWHAL_APPLICATION_CORE=/path/to/libnarwhal_application_core.so \
    python3 -B spike/test_document_session_abi.py
Add --mutation to run in-memory adapter mutants against these same tests.
Without an installed/explicit library, only the actual-core tests are skipped.
On Linux the CLI re-execs with LC_ALL=C and PYTHONUTF8=1 before applying its
memory cap: this avoids this host's 223 MiB virtual mapping of locale-archive.
Fake callbacks copy only into real allocations; hostile sizes never reach an
unchecked memory read, including when exercising deliberately broken adapters.
"""
from __future__ import annotations

import copy
import ctypes
import gc
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock
import weakref

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "narwhal3d" / "native_document_session.py"
spec = importlib.util.spec_from_file_location("document_session_abi_under_test", MODULE)
binding = importlib.util.module_from_spec(spec)
# Deliberately do not register in sys.modules: the model also loads by filepath.
spec.loader.exec_module(binding)


class Function:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


class FakeLibrary:
    """No arbitrary C pointers, retained buffer views, or large allocations."""

    def __init__(self, handler=None, version=1):
        self.handler = handler
        self.calls = []
        self.narwhal_session_abi = Function(lambda: version)
        self.narwhal_session_json = Function(self.call)

    def call(self, source, length, output, capacity):
        assert length <= ctypes.sizeof(source)
        assert capacity <= ctypes.sizeof(output)
        request = json.loads(ctypes.string_at(source, length))
        self.calls.append((request, capacity))
        reply = self.handler(request, capacity) if self.handler else None
        if reply is None:
            if request["op"] == "create":
                reply = {"status": "ok", "session": 1, "generation": 0,
                         "state_id": 2, "count": 0}
            elif request["op"] == "info":
                reply = {"status": "ok", "generation": 0, "state_id": 2, "count": 0}
            elif request["op"] == "rows":
                reply = {"status": "ok", "rows": []}
            else:
                reply = {"status": "ok"}
        if type(reply) in (int, float, bool):
            return reply
        data = reply if isinstance(reply, bytes) else json.dumps(reply).encode()
        if len(data) <= capacity:
            ctypes.memmove(output, data, len(data))
        return len(data)


class SafeCase(unittest.TestCase):
    def setUp(self):
        allocate = binding._allocate_output
        string_at = ctypes.string_at
        self.allocations = []

        def bounded_allocate(capacity):
            self.allocations.append(capacity)
            # Independent of production caps: a mutant cannot allocate 128/256 MiB.
            self.assertLessEqual(capacity, 1024 * 1024, "unsafe test allocation")
            self.assertGreaterEqual(capacity, 4096, "undersized ABI buffer")
            return allocate(capacity)

        def bounded_read(buffer, size):
            self.assertGreater(size, 0, "nonpositive buffer read")
            self.assertLessEqual(size, ctypes.sizeof(buffer), "out-of-bounds read")
            return string_at(buffer, size)

        self.enterContext(mock.patch.object(binding, "_allocate_output", bounded_allocate))
        self.enterContext(mock.patch.object(ctypes, "string_at", bounded_read))

    def error(self, code, callback, *args, **kwargs):
        with self.assertRaises(binding.SessionError) as caught:
            callback(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertIsInstance(caught.exception.code, str)

    def fake(self, handler=None):
        library = FakeLibrary(handler)
        session = binding.Session(library=library)
        self.addCleanup(session._finalizer)
        return session, library


class DiscoveryTests(unittest.TestCase):
    def test_standalone_import_never_loads_or_builds(self):
        other = importlib.util.module_from_spec(spec)
        with mock.patch.object(ctypes, "CDLL", side_effect=AssertionError("import loaded native")):
            spec.loader.exec_module(other)
        self.assertNotIn(spec.name, sys.modules)
        self.assertEqual(other.__all__, ["load_library", "Session", "Snapshot",
                                        "SessionUnavailable", "SessionError"])

    def test_default_discovery_order_and_missing(self):
        name = binding._library_name()
        packaged = binding._ROOT / "narwhal3d" / "native" / name
        artifact = binding._ROOT / "artifacts" / "application-core" / binding._platform_tag() / name
        with mock.patch.dict(os.environ, {}, clear=True):
            for present, expected in (([packaged, artifact], packaged), ([artifact], artifact), ([], None)):
                with self.subTest(present=present), \
                        mock.patch.object(Path, "exists", lambda path: path in present), \
                        mock.patch.object(ctypes, "CDLL", return_value=FakeLibrary()) as load:
                    result = binding.load_library()
                    if expected is None:
                        self.assertIsNone(result)
                        load.assert_not_called()
                        with self.assertRaises(binding.SessionUnavailable):
                            binding.Session()
                    else:
                        self.assertIs(result, load.return_value)
                        load.assert_called_once_with(str(expected.resolve()))

    def test_explicit_is_authoritative_including_empty_bad_and_old(self):
        for explicit in ("", "/missing/application-core.so"):
            with self.subTest(explicit=explicit), \
                    mock.patch.dict(os.environ, {binding._ENV_PATH: explicit}), \
                    mock.patch.object(ctypes, "CDLL", side_effect=OSError("missing")) as load:
                with self.assertRaises(binding.SessionUnavailable):
                    binding.load_library()
                if not explicit:
                    load.assert_not_called()
                else:
                    load.assert_called_once_with(explicit)
        with mock.patch.dict(os.environ, {binding._ENV_PATH: "/explicit/core.so"}), \
                mock.patch.object(ctypes, "CDLL", return_value=FakeLibrary(version=2)) as load:
            with self.assertRaises(binding.SessionUnavailable):
                binding.load_library()
            load.assert_called_once_with("/explicit/core.so")
        for library in (object(), FakeLibrary(version=0)):
            with self.subTest(library=library), self.assertRaises(binding.SessionUnavailable):
                binding.Session(library=library)

    def test_bad_packaged_library_does_not_silently_select_another(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(Path, "exists", return_value=True), \
                mock.patch.object(ctypes, "CDLL", side_effect=OSError("bad binary")) as load:
            with self.assertRaises(binding.SessionUnavailable):
                binding.load_library()
            self.assertEqual(load.call_count, 1)

    def test_platform_names_and_abi_signature(self):
        for system, machine, tag, name in (
            ("linux", "x86_64", "linux-x86_64", "libnarwhal_application_core.so"),
            ("linux", "aarch64", "linux-arm64", "libnarwhal_application_core.so"),
            ("win32", "AMD64", "windows-x86_64", "narwhal_application_core.dll"),
            ("darwin", "arm64", "macos-arm64", "libnarwhal_application_core.dylib"),
            ("darwin", "x86_64", "macos-x86_64", "libnarwhal_application_core.dylib"),
        ):
            with self.subTest(system=system, machine=machine), \
                    mock.patch.object(sys, "platform", system), \
                    mock.patch.object(binding.platform, "machine", return_value=machine):
                self.assertEqual(binding._library_name(), name)
                self.assertEqual(binding._platform_tag(), tag)
        library = binding._configure(FakeLibrary())
        self.assertIs(library.narwhal_session_json.restype, ctypes.c_ssize_t)
        self.assertEqual(library.narwhal_session_json.argtypes,
                         [ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                          ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t])


class TransportTests(SafeCase):
    def test_capacity_policy_without_large_allocations(self):
        for size in (4096, 128 * 1024**2, 256 * 1024**2, 512 * 1024**2):
            self.assertTrue(binding._valid_capacity(size))
        for size in (-1, 0, 4095, 512 * 1024**2 + 1, 1 << 63, True, 4096.0):
            self.assertFalse(binding._valid_capacity(size))

    def test_invalid_reply_sizes_never_read_memory(self):
        for size in (-1, -100, 0, True, 3.5):
            with self.subTest(size=size):
                session, library = self.fake(lambda req, cap: size if req["op"] == "rows" else None)
                self.error("invalid_reply_size", session.rows)
                self.assertEqual(sum(req["op"] == "rows" for req, _ in library.calls), 1)

    def test_response_ceiling_precedes_allocation_or_read(self):
        session, library = self.fake(lambda req, cap: 512 * 1024**2 + 1
                                     if req["op"] == "rows" else None)
        self.error("reply_too_large", session.rows)
        self.assertEqual(self.allocations, [4096, 4096])
        self.assertEqual(len(library.calls), 2)

    def test_read_resize_and_retry_limit(self):
        row = ["large", "value" * 1600]
        payload = {"status": "ok", "rows": [row]}
        session, library = self.fake(lambda req, cap: payload if req["op"] == "rows" else None)
        self.assertEqual(session.rows(), [tuple(row)])
        self.assertEqual(session.call_counts["rows"], 2)
        self.assertEqual(library.calls[-1][1], len(json.dumps(payload).encode()))
        session, library = self.fake(lambda req, cap: cap + 1 if req["op"] == "rows" else None)
        self.error("reply_retry_limit", session.rows)
        self.assertEqual(session.call_counts["rows"], 3)
        self.assertEqual([cap for req, cap in library.calls if req["op"] == "rows"],
                         [4096, 4097, 4098])

    def test_mutations_are_never_replayed(self):
        for op in ("create", "snapshot", "prepare", "commit", "restore", "abort",
                   "close", "release_snapshot"):
            with self.subTest(op=op):
                library = FakeLibrary(lambda req, cap: cap + 1 if req["op"] == op else None)
                if op == "create":
                    self.error("mutation_reply_too_large", binding.Session, library=library)
                else:
                    session = binding.Session(library=library)
                    self.addCleanup(session._finalizer)
                    self.error("mutation_reply_too_large", session.request, op)
                self.assertEqual(sum(req["op"] == op for req, _ in library.calls), 1)
                self.assertTrue(all(cap == 4096 for _, cap in library.calls))

    def test_malformed_json_status_and_schema(self):
        bad = [b"", b"{", b"\xff", b"{}\x00", b"[]", b"null", b"42",
               b'{"status":"ok","status":"ok","rows":[]}',
               b'{"status":"ok","rows":NaN}',
               {}, {"status": True}, {"status": "unknown"}, {"status": "error"},
               {"status": "error", "code": 12}, {"status": "error", "code": ""},
               {"status": "error", "code": "panic", "extra": "not allowed"},
               {"status": "ok"}, {"status": "ok", "rows": None},
               {"status": "ok", "rows": [["key", 1]]},
               {"status": "ok", "rows": [["key", "value", "extra"]]},
               {"status": "ok", "rows": [], "extra": True}]
        for reply in bad:
            with self.subTest(reply=reply):
                session, _ = self.fake(lambda req, cap: reply if req["op"] == "rows" else None)
                self.error("invalid_reply_size" if reply == b"" else "invalid_reply", session.rows)

    def test_all_success_schemas_are_checked(self):
        cases = {
            "create": {"session": True, "generation": 0, "state_id": 1, "count": 0},
            "info": {"generation": -1, "state_id": 1, "count": 0},
            "commit": {"generation": 1, "state_id": 0},
            "restore": {"generation": 2**64, "state_id": 1},
            "snapshot": {"snapshot": 0, "state_id": 1},
            "prepare": {"changed": False, "ticket": 1},
            "get": {"found": False, "key": "wrong", "value": None},
            "at": {"found": True, "row": None},
            "equal": {"equal": 1},
            "abort": {"extra": True},
            "close": {"extra": True},
            "release_snapshot": {"extra": True},
        }
        for op, fields in cases.items():
            with self.subTest(op=op):
                library = FakeLibrary(lambda req, cap: {"status": "ok", **fields}
                                      if req["op"] == op else None)
                if op == "create":
                    self.error("invalid_reply", binding.Session, library=library)
                else:
                    session = binding.Session(library=library)
                    self.addCleanup(session._finalizer)
                    self.error("invalid_reply", session.request, op)

    def test_native_error_propagates_without_fallback_or_data_retention(self):
        session, library = self.fake(lambda req, cap: {"status": "error", "code": "generation_conflict"}
                                     if req["op"] == "prepare" else None)
        with mock.patch.object(binding, "load_library", side_effect=AssertionError("fallback")):
            self.error("generation_conflict", session.prepare, "private key", "private value", generation=0)
        self.assertEqual(session.backend, "rust")
        self.assertEqual(session.call_counts, {"create": 1, "prepare": 1})
        counts = session.call_counts
        counts.clear()
        self.assertEqual(session.call_counts["prepare"], 1)
        session.request("info", session=999)
        self.assertEqual(library.calls[-1][0]["session"], session.handle)

    def test_input_limits_checked_before_ffi(self):
        session, library = self.fake()
        before = len(library.calls)
        with mock.patch.object(binding, "_MAX_INPUT_BYTES", 128):
            self.error("request_too_large", session.get, "x" * 129)
        for value in (object(), float("nan"), "\ud800"):
            self.error("invalid_request", session.get, value)
        self.assertEqual(len(library.calls), before)

    def test_ctypes_failure_is_an_explicit_session_error(self):
        session, library = self.fake()
        with mock.patch.object(library, "narwhal_session_json", side_effect=ctypes.ArgumentError("ABI call")) as call:
            self.error("transport_error", session.info)
            call.assert_called_once()

    def test_explicit_close_errors_are_not_swallowed(self):
        session, _ = self.fake(lambda req, cap: {"status": "error", "code": "registry_poisoned"}
                               if req["op"] == "close" else None)
        self.error("registry_poisoned", session.close)
        self.assertTrue(session._finalizer.alive)
        session._finalizer()  # Best-effort cleanup, and only cleanup, suppresses it.
        self.assertFalse(session._finalizer.alive)


class NativeTests(SafeCase):
    @classmethod
    def setUpClass(cls):
        cls.library = binding.load_library()
        if cls.library is None:
            raise unittest.SkipTest("application-core not installed; set NARWHAL_APPLICATION_CORE")

    def session(self, rows=()):
        session = binding.Session(rows, library=self.library)
        self.addCleanup(session.close)
        return session

    def write(self, session, key, value):
        prepared = session.prepare(key, value)
        self.assertTrue(prepared["changed"])
        return session.commit(prepared["ticket"])

    def test_read_write_replay_unicode_and_noops(self):
        session = self.session([("Name", "first"), ("NAME", "last"), ("other", "two"),
                                ("drop", "yes"), ("DROP", ""), ("", "ignored"),
                                ("Σ", "Greek"), ("emoji😀", "é\ntext")])
        self.assertEqual(session.rows(), [("Name", "last"), ("other", "two"),
                                          ("Σ", "Greek"), ("emoji😀", "é\ntext")])
        self.assertEqual(session.get("name"), {"status": "ok", "found": True,
                                              "key": "Name", "value": "last"})
        self.assertFalse(session.get("name", exact=True)["found"])
        self.assertTrue(session.get("Name", exact=True)["found"])
        self.assertEqual(session.get("σ")["value"], "Greek")
        self.assertEqual(session.at(0)["row"], ["Name", "last"])
        self.assertEqual(session.at(2**64 - 1), {"status": "ok", "found": False, "row": None})
        self.assertEqual(session.get("missing"), {"status": "ok", "found": False,
                                                 "key": None, "value": None})
        before = session.info()
        for key, value in (("name", "last"), ("missing", None), ("missing", ""), ("", "ignored")):
            self.assertEqual(session.prepare(key, value), {"status": "ok", "changed": False, "ticket": None})
            self.assertEqual(session.info(), before)
        self.write(session, "NAME", "new")
        self.assertEqual(session.at(0)["row"], ["Name", "new"])
        self.write(session, "name", None)
        self.write(session, "NAME", "reinserted")
        self.assertEqual(session.rows()[-1], ("NAME", "reinserted"))
        self.assertEqual(session.info()["generation"], 3)

    def test_staging_abort_conflicts_and_ticket_owner(self):
        session, other = self.session(), self.session()
        one = session.prepare("a", "one", generation=0)["ticket"]
        two = session.prepare("b", "two", generation=0)["ticket"]
        self.assertEqual(session.rows(), [])
        self.error("ticket_owner_mismatch", other.commit, one)
        self.error("ticket_owner_mismatch", other.abort, one)
        session.commit(one)
        self.error("invalid_ticket", session.commit, one)
        self.error("generation_conflict", session.commit, two)
        self.error("invalid_ticket", session.commit, two)
        self.error("generation_conflict", session.prepare, "a", "one", generation=0)
        ticket = session.prepare("b", "two")["ticket"]
        session.abort(ticket)
        self.error("invalid_ticket", session.abort, ticket)
        self.assertEqual(session.rows(), [("a", "one")])

    def test_snapshot_restore_immutable_equality_and_no_python_table(self):
        session = self.session([("a", "one")])
        first = session.snapshot()
        twin = session.snapshot()
        self.assertEqual(first, twin)
        self.assertEqual(session.call_counts.get("snapshot_rows", 0), 0)
        self.assertEqual(session.call_counts.get("rows", 0), 0)
        self.assertEqual(session.call_counts["equal"], 1)
        self.assertEqual(first, [("a", "one")])
        self.assertEqual([["a", "one"]], first)
        self.assertEqual(list(first), [("a", "one")])
        self.assertIs(copy.copy(first), first)
        self.assertIs(copy.deepcopy(first), first)
        with self.assertRaises(TypeError):
            hash(first)
        for name in ("state_id", "handle", "_handle", "_session", "_state_id", "new"):
            with self.assertRaises(AttributeError):
                setattr(first, name, None)
        with self.assertRaises(AttributeError):
            del first._handle
        self.assertFalse(hasattr(first, "__dict__"))
        self.write(session, "a", "two")
        changed = session.snapshot()
        self.assertNotEqual(first, changed)
        first.rows().clear()
        self.assertEqual(first.rows(), [("a", "one")])
        staged = session.prepare("b", "three")["ticket"]
        before = session.info()["generation"]
        restored = session.restore(first)
        self.assertEqual(restored["state_id"], first.state_id)
        self.assertEqual(restored["generation"], before + 1)
        self.error("generation_conflict", session.commit, staged)
        self.assertEqual(session.restore(first)["generation"], before + 2)
        self.write(session, "a", "different")
        self.write(session, "a", "one")
        equal_contents = session.snapshot()
        self.assertNotEqual(first.state_id, equal_contents.state_id)
        self.assertEqual(first, equal_contents)

    def test_cross_session_snapshot_comparison_and_identity_validation(self):
        left, right = self.session([("a", "one")]), self.session([("a", "one")])
        a, b = left.snapshot(), right.snapshot()
        self.assertEqual(a, b)
        self.assertNotEqual(a, object())
        before = right.call_counts
        self.error("snapshot_owner_mismatch", right.restore, a)
        self.assertEqual(right.call_counts, before)
        self.error("snapshot_owner_mismatch", right.request, "restore", snapshot=a.handle)
        self.error("snapshot_owner_mismatch", right.request, "release_snapshot", snapshot=a.handle)
        self.assertEqual(a.rows(), [("a", "one")])
        self.write(right, "b", "two")
        self.assertNotEqual(a, right.snapshot())

    def test_snapshot_finalizer_releases_once_and_retains_session(self):
        session = binding.Session([("a", "one")], library=self.library)
        handle = session.handle
        snapshot = session.snapshot()
        snap_handle = snapshot.handle
        snapshot_ref = weakref.ref(snapshot)
        session_ref = weakref.ref(session)
        finalizer = snapshot._finalizer
        del session
        gc.collect()
        self.assertIsNotNone(session_ref())
        self.assertEqual(snapshot.rows(), [("a", "one")])
        del snapshot
        gc.collect()
        self.assertIsNone(snapshot_ref())
        self.assertFalse(finalizer.alive)
        finalizer()  # No second release or retained session.
        gc.collect()
        self.assertIsNone(session_ref())
        observer = self.session()
        self.error("invalid_session", binding._call, self.library, {"op": "info", "session": handle})
        self.error("invalid_snapshot", observer.request, "snapshot_rows", snapshot=snap_handle)

    def test_gc_releases_snapshot_once_while_session_is_live(self):
        session = self.session([("a", "one")])
        snapshot = session.snapshot()
        handle = snapshot.handle
        finalizer = snapshot._finalizer
        reference = weakref.ref(snapshot)
        del snapshot
        gc.collect()
        self.assertIsNone(reference())
        self.assertFalse(finalizer.alive)
        self.assertEqual(session.call_counts.get("release_snapshot", 0), 1)
        finalizer()
        self.assertEqual(session.call_counts["release_snapshot"], 1)
        self.error("invalid_snapshot", session.request, "snapshot_rows", snapshot=handle)
        self.assertEqual(session.rows(), [("a", "one")])

    def test_released_snapshot_and_closed_session_are_stale(self):
        session = self.session([("a", "one")])
        snapshot = session.snapshot()
        handle = snapshot.handle
        finalizer = snapshot._finalizer
        finalizer()
        self.assertEqual(session.call_counts["release_snapshot"], 1)
        finalizer()
        self.assertEqual(session.call_counts["release_snapshot"], 1)
        self.error("invalid_snapshot", session.request, "snapshot_rows", snapshot=handle)
        ticket = session.prepare("a", "two")["ticket"]
        retained = session.snapshot()
        session.close()
        calls = session.call_counts
        session.close()
        retained._finalizer()
        self.assertEqual(session.call_counts, calls)
        self.assertFalse(session._finalizer.alive)
        self.error("invalid_session", retained.rows)
        self.error("invalid_session", session.info)
        other = self.session()
        self.error("invalid_ticket", other.commit, ticket)
        self.error("invalid_snapshot", other.request, "snapshot_rows", snapshot=retained.handle)
        self.assertGreater(other.handle, session.handle)

    def test_large_reads_and_strict_native_request_errors(self):
        value = "é" * 5000
        session = self.session([("large", value)])
        self.assertEqual(session.get("large")["value"], value)
        self.assertEqual(session.at(0)["row"], ["large", value])
        self.assertEqual(session.rows(), [("large", value)])
        snapshot = session.snapshot()
        self.assertEqual(snapshot.rows(), [("large", value)])
        for op in ("get", "at", "rows", "snapshot_rows"):
            self.assertEqual(session.call_counts[op], 2)
        self.error("invalid_request", session.at, -1)
        self.error("invalid_request", session.at, True)
        self.error("invalid_request", session.get, "large", exact=1)
        self.error("invalid_request", session.request, "prepare", generation=0, key="large")
        self.error("invalid_request", session.request, "info", extra=True)
        self.error("nul_not_supported", session.prepare, "a\0b", "c")
        self.error("invalid_request", session.request, "not_an_operation")


def mutation_checks():
    """Re-break bounded transport/lifetime behavior only in isolated module memory."""
    original = binding
    source = MODULE.read_text()
    mutations = [
        ("explicit path authority", "if explicit is not None:\n        if not explicit:",
         "if explicit:\n        if not explicit:",
         DiscoveryTests, "test_explicit_is_authoritative_including_empty_bad_and_old"),
        ("reply size sign", "type(size) is not int or size <= 0", "type(size) is not int or size < 0",
         TransportTests, "test_invalid_reply_sizes_never_read_memory"),
        ("output cap", "_MAX_OUTPUT_BYTES = 512 * 1024 * 1024", "_MAX_OUTPUT_BYTES = 1024 * 1024 * 1024",
         TransportTests, "test_response_ceiling_precedes_allocation_or_read"),
        ("capacity minimum", "_MIN_CAPACITY <= capacity", "0 <= capacity",
         TransportTests, "test_capacity_policy_without_large_allocations"),
        ("read bounds", "if size > capacity:", "if False:",
         TransportTests, "test_read_resize_and_retry_limit"),
        ("mutation replay", "if not isinstance(op, str) or op not in _READ_OPS:", "if False:",
         TransportTests, "test_mutations_are_never_replayed"),
        ("retry bound", "_MAX_ATTEMPTS = 3", "_MAX_ATTEMPTS = 4",
         TransportTests, "test_read_resize_and_retry_limit"),
        ("schema validation", "if not valid:", "if False:",
         TransportTests, "test_malformed_json_status_and_schema"),
        ("input bound", "if len(encoded) > _MAX_INPUT_BYTES:", "if False:",
         TransportTests, "test_input_limits_checked_before_ffi"),
        ("session identity", '{**fields, "op": op, "session": self._handle}',
         '{"op": op, "session": self._handle, **fields}',
         TransportTests, "test_native_error_propagates_without_fallback_or_data_retention"),
    ]
    if original.load_library() is not None:
        mutations.extend([
            ("snapshot cleanup", 'session.request("release_snapshot", snapshot=handle)', "pass",
             NativeTests, "test_gc_releases_snapshot_once_while_session_is_live"),
            ("native snapshot equality", "if self._session is other._session:", "if False:",
             NativeTests, "test_snapshot_restore_immutable_equality_and_no_python_table"),
            ("restore owner identity", "if not isinstance(snapshot, Snapshot) or snapshot._session is not self:",
             "if not isinstance(snapshot, Snapshot):",
             NativeTests, "test_cross_session_snapshot_comparison_and_identity_validation"),
        ])
    else:
        print("SKIP: native snapshot mutations (application-core unavailable)")
    for label, old, new, test_class, test_name in mutations:
        if source.count(old) != 1:
            raise AssertionError(f"mutation anchor is not unique: {label}")
        mutant = types.ModuleType("document_session_mutant")
        mutant.__file__ = str(MODULE)
        exec(compile(source.replace(old, new), str(MODULE), "exec"), mutant.__dict__)
        with mock.patch.dict(globals(), binding=mutant):
            stream = io.StringIO()
            result = unittest.TextTestRunner(stream=stream).run(unittest.TestSuite([test_class(test_name)]))
        if result.wasSuccessful():
            raise AssertionError(f"SURVIVED: {label}")
        print(f"MUTATION RED: {label} ({len(result.failures)} failures, {len(result.errors)} errors)")
    assert binding is original
    print(f"Mutation check: {len(mutations)}/{len(mutations)} killed; source unchanged")


if __name__ == "__main__":
    if sys.platform.startswith("linux"):
        if os.environ.get("LC_ALL") != "C" or os.environ.get("PYTHONUTF8") != "1":
            environment = dict(os.environ, LC_ALL="C", PYTHONUTF8="1")
            os.execve(sys.executable, [sys.executable, "-B", *sys.argv], environment)
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        cap = 256 * 1024 * 1024
        limits = [value for value in (soft, hard, cap) if value != resource.RLIM_INFINITY]
        resource.setrlimit(resource.RLIMIT_AS, (min(limits), hard))
    if "--mutation" in sys.argv:
        sys.argv.remove("--mutation")
        mutation_checks()
    unittest.main(verbosity=2)
