"""Lightweight fake-based bridge tests; no Blender, UI, builds or source mutations.

Run: python3 -B spike/test_automation_bridge.py
All mutation variants are exec'd in isolated package namespaces in memory. Real
Blender integration, including native read latency, belongs to the parent harness.
"""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import socket
import sys
import threading
import types
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {name: (ROOT / "narwhal3d" / (name + ".py")).read_text()
           for name in ("automation_tools", "automation_bridge")}
SESSION = "a1" * 16
SECRET = "private-token-MUST-NOT-LEAK"
TOOL = "narwhal.document_summary"


def load_modules(sources):
    package_name = "_automation_test_" + uuid.uuid4().hex
    package = types.ModuleType(package_name)
    package.__path__ = [str(ROOT / "narwhal3d")]
    sys.modules[package_name] = package
    modules = {}
    for name, source in sources.items():
        module = types.ModuleType(package_name + "." + name)
        module.__package__ = package_name
        sys.modules[module.__name__] = module
        exec(compile(source, name + ".py", "exec"), module.__dict__)
        setattr(package, name, module)
        modules[name] = module
    # Real pure kernel tag/master readers, but no OCP imports or geometry work.
    spec = importlib.util.spec_from_file_location(package_name + ".kernel",
                                                 ROOT / "narwhal3d/kernel/__init__.py")
    kernel = importlib.util.module_from_spec(spec)
    sys.modules[kernel.__name__] = kernel
    spec.loader.exec_module(kernel)
    package.kernel = kernel
    return package_name, modules["automation_tools"], modules["automation_bridge"], kernel


def unload(package_name):
    for name in list(sys.modules):
        if name == package_name or name.startswith(package_name + "."):
            del sys.modules[name]


class Geometry:
    def __init__(self, tags=None):
        self.tags = dict(tags or {})

    def GetUserString(self, key):
        if key not in {"Narwhal3D:Representation", "Narwhal3D:KernelShape"}:
            raise AssertionError("must not read recipes or unrelated user strings")
        return self.tags.get(key, "")

    def __getattr__(self, _name):
        raise AssertionError("no geometry calculations or validity claims")


def row(index=1, name="Part", tags=None):
    return types.SimpleNamespace(
        Attributes=types.SimpleNamespace(Id=uuid.UUID(int=index), Name=name, LayerIndex=0),
        Geometry=Geometry(tags))


class Table:
    def __init__(self, rows, reported=None):
        self.rows = rows
        self.reported = len(rows) if reported is None else reported
        self.probes = 0
        self.iterations = 0

    def __len__(self):
        return self.reported

    def __iter__(self):
        self.iterations += 1
        for obj in self.rows:
            self.probes += 1
            yield obj


class Selection:
    def __init__(self, names):
        self.names = names
        self.probes = 0
        self.calls = 0

    def selected_objects(self):
        self.calls += 1
        for name in self.names:
            self.probes += 1
            yield types.SimpleNamespace(name=name)


def app_fixture(rows=None, names=()):
    rows = [row()] if rows is None else rows
    table = Table(rows)
    model = types.SimpleNamespace(
        file=types.SimpleNamespace(Objects=table, Layers=[object()]),
        current_layer_index=0, object_count=lambda: len(table),
        unit_system=lambda: "Millimeters", absolute_tolerance=lambda: 0.01,
        blender_name_for=lambda _oid: None)
    return types.SimpleNamespace(model=model, ctx=Selection(names),
                                 doc_title=lambda: "Untitled", doc_modified=lambda: False)


class Reader:
    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = {"ok": True} if result is None else result
        self.error = error

    def read(self, tool, arguments):
        self.calls.append((tool, arguments))
        if self.error:
            raise self.error
        return self.result


class InstrumentedSocket:
    """Real socketpair input, controllable short/blocking writes and probe budgets."""
    def __init__(self, sock):
        self.sock = sock
        self.family = sock.family
        self.recv_bytes = []
        self.send_bytes = []
        self.block_send = False
        self.send_limit = None
        self.zero_send = False

    def __getattr__(self, name):
        return getattr(self.sock, name)

    def recv(self, size, flags=0):
        self.recv_bytes.append(size)
        return self.sock.recv(size, flags)

    def send(self, data):
        self.send_bytes.append(len(data))
        if self.zero_send:
            return 0
        if self.block_send:
            raise BlockingIOError()
        if self.send_limit is not None:
            data = data[:self.send_limit]
        return self.sock.send(data)


def worker_call(call):
    results = []

    def run():
        try:
            call()
        except Exception as exc:
            results.append(exc)
    thread = threading.Thread(target=run)
    thread.start()
    thread.join(timeout=2)
    if thread.is_alive():
        raise AssertionError("thread did not finish")
    return results


class Controls(unittest.TestCase):
    tools = None
    bridge_module = None
    kernel = None

    def pair(self, reader=None):
        left, right = socket.socketpair()
        self.addCleanup(right.close)
        self.addCleanup(left.close)
        sock = InstrumentedSocket(left)
        bridge = self.bridge_module.MainThreadBridge(sock, reader or Reader(), SESSION)
        self.addCleanup(bridge.close)
        right.setblocking(False)
        return bridge, right, sock

    def request(self, seq=1, tool=TOOL, arguments=None, **overrides):
        request = {"session": SESSION, "seq": seq, "tool": tool,
                   "arguments": {} if arguments is None else arguments}
        request.update(overrides)
        return json.dumps(request, separators=(",", ":")).encode() + b"\n"

    def receive(self, peer):
        data = bytearray()
        while True:
            try:
                chunk = peer.recv(65536)
            except BlockingIOError:
                break
            if not chunk:
                break
            data.extend(chunk)
        return bytes(data)

    def reader(self, app=None):
        return self.tools.ApplicationReader(app or app_fixture(), "0.4.6-test")

    def test_main_thread_for_all_public_lifecycle_calls(self):
        bridge, _peer, _sock = self.pair()
        reader = self.reader()
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        for call in (bridge.pump, bridge.close,
                     lambda: self.bridge_module.MainThreadBridge(left, Reader(), SESSION),
                     lambda: self.tools.ApplicationReader(app_fixture(), "version"),
                     lambda: reader.read(TOOL, {}),
                     lambda: self.bridge_module.register_timer(bridge),
                     lambda: self.bridge_module.unregister_timer(bridge)):
            with self.subTest(call=call):
                errors = worker_call(call)
                self.assertEqual(len(errors), 1)
                self.assertEqual(str(errors[0]), "main_thread_required")
        self.assertFalse(bridge.closed)

    def test_constructor_and_idle_close(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        for value in ("", "A" * 32, "x" * 32, SESSION + "\n", None):
            with self.assertRaises(ValueError):
                self.bridge_module.MainThreadBridge(left, Reader(), value)
        tcp = socket.socket()
        self.addCleanup(tcp.close)
        with self.assertRaises(ValueError):
            self.bridge_module.MainThreadBridge(tcp, Reader(), SESSION)
        disconnected = socket.socket(socket.AF_UNIX)
        self.addCleanup(disconnected.close)
        with self.assertRaises(OSError):
            self.bridge_module.MainThreadBridge(disconnected, Reader(), SESSION)
        bridge, _peer, sock = self.pair()
        self.assertFalse(sock.getblocking())
        self.assertEqual(bridge.pump(), .02)
        self.assertFalse(bridge.closed)
        bridge.close()
        bridge.close()
        self.assertIsNone(bridge.pump())
        self.assertEqual(sock.fileno(), -1)

    def test_valid_partial_and_one_request_per_tick(self):
        reader = Reader()
        bridge, peer, _sock = self.pair(reader)
        wire = self.request()
        peer.sendall(wire[:20])
        bridge.pump()
        self.assertEqual(reader.calls, [])
        peer.sendall(wire[20:] + self.request(seq=2))
        self.assertEqual(bridge.pump(), .02)
        self.assertEqual(len(reader.calls), 1)
        reply = json.loads(self.receive(peer))
        self.assertEqual(reply, {"session": SESSION, "seq": 1, "result": {"ok": True}})
        bridge.pump()
        self.assertEqual(len(reader.calls), 2)
        self.assertEqual(json.loads(self.receive(peer))["seq"], 2)
        peer.sendall(self.request(seq=9))  # monotonic, not necessarily consecutive
        bridge.pump()
        self.assertEqual(json.loads(self.receive(peer))["seq"], 9)

    def test_auth_and_strict_envelopes_fail_closed(self):
        valid = json.loads(self.request())
        invalid = [b"\n", b"{}\n", b"[]\n", b"null\n", b"\xff\n",
                   b"{\"session\":NaN}\n", b"[" * 2000 + b"\n"]
        for key in valid:
            missing = dict(valid)
            del missing[key]
            invalid.append(json.dumps(missing).encode() + b"\n")
        for changes in ({"session": "b2" * 16}, {"session": "A" * 32},
                        {"session": 2}, {"session": "é" * 32},
                        {"seq": 0}, {"seq": -1}, {"seq": True}, {"seq": 1.0},
                        {"seq": "1"}, {"tool": "narwhal.delete"}, {"tool": []},
                        {"arguments": []}, {"extra": SECRET}):
            invalid.append(self.request(**changes))
        invalid.append(self.request().replace(b'"seq":1', b'"seq":1,"seq":2'))
        invalid.append(self.request().replace(b'"arguments":{}',
                                             b'"arguments":{"x":1,"x":2}'))
        for wire in invalid:
            with self.subTest(wire=wire[:60]):
                reader = Reader()
                bridge, peer, _sock = self.pair(reader)
                peer.sendall(wire)
                for _ in range(4):
                    bridge.pump()
                    if bridge.closed:
                        break
                self.assertTrue(bridge.closed)
                self.assertEqual(reader.calls, [])
                self.assertEqual(self.receive(peer), b"")

    def test_compare_digest_is_used(self):
        bridge, peer, _sock = self.pair()
        peer.sendall(self.request())
        original = self.bridge_module.hmac.compare_digest
        with patch.object(self.bridge_module.hmac, "compare_digest", wraps=original) as compare:
            bridge.pump()
        compare.assert_called_once_with(SESSION, SESSION)

    def test_replay_and_backward_seq_close(self):
        for seq in (1, 2):
            reader = Reader()
            bridge, peer, _sock = self.pair(reader)
            peer.sendall(self.request(seq=2))
            bridge.pump()
            self.receive(peer)
            peer.sendall(self.request(seq=seq))
            bridge.pump()
            self.assertTrue(bridge.closed)
            self.assertEqual(len(reader.calls), 1)
            self.assertEqual(self.receive(peer), b"")

    def test_argument_errors_do_not_dispatch_and_consume_sequence(self):
        bad = [(TOOL, {"secret": SECRET}), ("narwhal.object_summary", {}),
               ("narwhal.object_summary", {"object_id": SECRET}),
               ("narwhal.object_summary", {"object_id": str(uuid.UUID(int=1)), "x": 1})]
        for tool, args in bad:
            reader = Reader()
            bridge, peer, _sock = self.pair(reader)
            peer.sendall(self.request(tool=tool, arguments=args))
            bridge.pump()
            self.assertEqual(json.loads(self.receive(peer))["error"], "invalid_arguments")
            self.assertEqual(reader.calls, [])
            self.assertFalse(bridge.closed)
            peer.sendall(self.request())
            bridge.pump()
            self.assertTrue(bridge.closed)

    def test_eof_discards_queued_work(self):
        reader = Reader()
        bridge, peer, _sock = self.pair(reader)
        peer.sendall(self.request() + self.request(seq=2))
        bridge.pump()
        self.assertEqual(len(reader.calls), 1)
        peer.close()
        self.assertIsNone(bridge.pump())
        self.assertTrue(bridge.closed)
        self.assertEqual(len(reader.calls), 1)
        reader = Reader()
        bridge, peer, _sock = self.pair(reader)
        peer.sendall(self.request())
        peer.shutdown(socket.SHUT_WR)
        bridge.pump()
        self.assertTrue(bridge.closed)
        self.assertEqual(reader.calls, [])

    def test_recv_and_send_budgets_backpressure_and_short_writes(self):
        reader = Reader({"name": "x" * 9000})
        bridge, peer, sock = self.pair(reader)
        sock.block_send = True
        peer.sendall(self.request() + self.request(seq=2))
        bridge.pump()
        pending = bytes(bridge._output)
        bridge.pump()
        self.assertEqual(len(reader.calls), 1)
        self.assertEqual(bytes(bridge._output), pending)
        sock.block_send = False
        sock.send_limit = 333
        received = bytearray()
        for _ in range(100):
            sock.recv_bytes.clear()
            sock.send_bytes.clear()
            bridge.pump()
            self.assertLessEqual(sum(sock.recv_bytes), 4096)
            self.assertLessEqual(sum(sock.send_bytes), 4096)
            self.assertLessEqual(len(sock.send_bytes), 1)
            received.extend(self.receive(peer))
            if received.count(b"\n") == 2:
                break
        replies = [json.loads(line) for line in received.splitlines()]
        self.assertEqual([reply["seq"] for reply in replies], [1, 2])
        self.assertEqual(replies[0]["result"], reader.result)
        self.assertFalse(bridge.closed)

    def test_frame_boundaries_and_input_memory(self):
        limit = 65536
        for size, accepted in ((limit, True), (limit + 1, False)):
            bridge, peer, sock = self.pair()
            base = self.request()
            wire = base[:-1] + b" " * (size - len(base)) + b"\n"
            peer.sendall(wire)
            for _ in range(30):
                sock.recv_bytes.clear()
                bridge.pump()
                self.assertLessEqual(sum(sock.recv_bytes), 4096)
                self.assertLessEqual(len(bridge._input), limit)
                if bridge.closed or bridge._last_seq:
                    break
            self.assertEqual(not bridge.closed, accepted)
            if accepted:
                self.assertEqual(json.loads(self.receive(peer))["seq"], 1)
        reader = Reader()
        bridge, peer, sock = self.pair(reader)
        sock.block_send = True
        peer.sendall(self.request() * 700)
        for _ in range(25):
            bridge.pump()
            self.assertLessEqual(len(bridge._input), limit)
            self.assertLessEqual(len(bridge._output), limit)
        self.assertEqual(len(reader.calls), 1)

    def test_result_boundaries_and_safe_failures(self):
        limit = 65536
        base = {"session": SESSION, "seq": 1, "result": {"x": ""}}
        empty = len(json.dumps(base, separators=(",", ":")).encode()) + 1
        for size in (limit - empty, limit - empty + 1):
            bridge, peer, _sock = self.pair(Reader({"x": "y" * size}))
            peer.sendall(self.request())
            result = bytearray()
            for _ in range(20):
                bridge.pump()
                result.extend(self.receive(peer))
                if b"\n" in result or bridge.closed:
                    break
            self.assertLessEqual(len(result), limit)
            if size == limit - empty:
                self.assertEqual(len(result), limit)
                self.assertIn("result", json.loads(result))
            else:
                self.assertTrue(bridge.closed)
                self.assertEqual(result, b"")
        for error in (RuntimeError(SECRET), self.tools.ReadError(SECRET)):
            bridge, peer, _sock = self.pair(Reader(error=error))
            peer.sendall(self.request())
            capture = io.StringIO()
            with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
                bridge.pump()
            wire = self.receive(peer)
            self.assertEqual(capture.getvalue(), "")
            self.assertNotIn(SECRET.encode(), wire)
            self.assertEqual(json.loads(wire)["error"], "read_failed")
        for result in ([], {"value": float("nan")}, {"value": object()}):
            bridge, peer, _sock = self.pair(Reader(result))
            peer.sendall(self.request())
            bridge.pump()
            self.assertEqual(json.loads(self.receive(peer))["error"], "read_failed")

    def test_synchronous_read_deadline_discards_late_reply(self):
        reader = Reader()
        bridge, peer, _sock = self.pair(reader)
        peer.sendall(self.request())
        with patch.object(self.bridge_module.time, "monotonic", side_effect=[10, 15]):
            bridge.pump()
        self.assertEqual(len(reader.calls), 1)
        self.assertTrue(bridge.closed)
        self.assertEqual(self.receive(peer), b"")

    def test_pending_deadline_and_zero_send(self):
        bridge, peer, _sock = self.pair()
        with patch.object(self.bridge_module.time, "monotonic", return_value=10):
            peer.sendall(b"{")
            bridge.pump()
        with patch.object(self.bridge_module.time, "monotonic", return_value=15):
            self.assertIsNone(bridge.pump())
        self.assertTrue(bridge.closed)
        bridge, peer, sock = self.pair()
        sock.block_send = True
        with patch.object(self.bridge_module.time, "monotonic", return_value=10):
            peer.sendall(self.request())
            bridge.pump()
        with patch.object(self.bridge_module.time, "monotonic", return_value=15):
            bridge.pump()
        self.assertTrue(bridge.closed)
        bridge, peer, sock = self.pair()
        sock.zero_send = True
        peer.sendall(self.request())
        self.assertIsNone(bridge.pump())
        self.assertTrue(bridge.closed)

    def test_reader_application_and_document(self):
        app = app_fixture()
        reader = self.reader(app)
        with patch.dict(sys.modules, {"bpy": types.SimpleNamespace(
                app=types.SimpleNamespace(version_string="5.2.2 actual"))}):
            result = reader.read("narwhal.application_info", {})
        self.assertEqual(result, {"application_version": "0.4.6-test",
                                 "blender_version": "5.2.2 actual",
                                 "permissions": {"read_only": True, "mutations": False}})
        document = reader.read(TOOL, {})
        self.assertEqual(document, {"object_count": 1, "layer_count": 1,
                                   "unit_system": "Millimeters", "tolerance": .01,
                                   "current_layer_index": 0, "title": "Untitled",
                                   "modified": False, "revision": None})
        self.assertEqual(app.model.file.Objects.iterations, 0)
        app.doc_title = lambda: "T" * 300
        self.assertEqual(len(reader.read(TOOL, {})["title"]), 256)
        with patch.dict(sys.modules, {"bpy": None}):
            with self.assertRaisesRegex(self.tools.ReadError, "application_unavailable"):
                reader.read("narwhal.application_info", {})
        for version in (None, "", "x" * 257):
            with self.assertRaises(ValueError):
                self.tools.ApplicationReader(app, version)

    def test_reader_validation_and_safe_errors(self):
        reader = self.reader()
        for tool in ("narwhal.delete", "eval", "exec", "narwhal.run_command", [], None):
            with self.assertRaisesRegex(self.tools.ReadError, "tool_not_allowed"):
                reader.read(tool, {})
        for tool in self.tools.TOOLS:
            with self.assertRaisesRegex(self.tools.ReadError, "invalid_arguments"):
                reader.read(tool, {"secret": SECRET})
        for value in (None, 1, "{00000000-0000-0000-0000-000000000001}",
                      "00000000000000000000000000000001", str(uuid.UUID(int=1)) + "\n"):
            with self.assertRaisesRegex(self.tools.ReadError, "invalid_arguments"):
                reader.read("narwhal.object_summary", {"object_id": value})
        app = app_fixture()
        def fail():
            raise RuntimeError(SECRET)
        app.doc_title = fail
        with self.assertRaisesRegex(self.tools.ReadError, "^read_failed$"):
            self.reader(app).read(TOOL, {})
        app.model.absolute_tolerance = lambda: float("inf")
        with self.assertRaisesRegex(self.tools.ReadError, "^read_failed$"):
            self.reader(app).read(TOOL, {})

    def test_selection_mapping_scope_and_bounded_probes(self):
        rows = [row(i + 1, "part" + str(i)) for i in range(70)]
        app = app_fixture(rows, [r.Attributes.Name for r in rows])
        result = self.reader(app).read("narwhal.selection_summary", {})
        self.assertEqual(result["scope"], "whole_objects")
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["objects"]), 64)
        self.assertEqual(app.ctx.probes, 65)
        self.assertEqual(result["objects"][0], {"object_id": str(uuid.UUID(int=1)),
                                              "name": "part0", "layer_index": 0})
        long_name = "N" * 300
        app = app_fixture([row(name=long_name)], ["display-alias"])
        app.model.blender_name_for = lambda _oid: "display-alias"
        result = self.reader(app).read("narwhal.selection_summary", {})
        self.assertEqual(result["objects"][0]["name"], "N" * 256)
        self.assertFalse(result["truncated"])
        for rows, names in (([], []), ([row()], ["Part"] * 3)):
            app = app_fixture(rows, names)
            result = self.reader(app).read("narwhal.selection_summary", {})
            self.assertFalse(result["truncated"])
            self.assertEqual(len(result["objects"]), bool(names))
        for rows in ([row()], [row(1), row(2)]):
            app = app_fixture(rows, ["unknown"] if len(rows) == 1 else ["Part"])
            with self.assertRaisesRegex(self.tools.ReadError, "selection_unavailable"):
                self.reader(app).read("narwhal.selection_summary", {})

    def test_combined_unicode_selection_result_budget(self):
        for first_codepoint in (0x1F300, 0x4E00):
            with self.subTest(first_codepoint=first_codepoint):
                names = [chr(first_codepoint + i) * 256 for i in range(64)]
                rows = [row(i + 1, name) for i, name in enumerate(names)]
                for obj in rows:
                    obj.Attributes.LayerIndex = 2147483647
                expected = [{"object_id": str(obj.Attributes.Id), "name": obj.Attributes.Name,
                             "layer_index": obj.Attributes.LayerIndex} for obj in rows]
                full = {"scope": "whole_objects", "objects": expected, "truncated": False}
                encode = lambda value: json.dumps(
                    value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")
                # Neither the object count nor character cap alone exposes this bug.
                self.assertGreater(len(encode(full)), 65536)
                app = app_fixture(rows, names)
                result = self.reader(app).read("narwhal.selection_summary", {})
                self.assertLessEqual(len(encode(result)), 48 * 1024)
                self.assertTrue(result["truncated"])
                count = len(result["objects"])
                self.assertGreater(count, 0)
                self.assertLess(count, 64)
                self.assertEqual(result["objects"], expected[:count])
                next_result = dict(result, objects=expected[:count + 1])
                self.assertGreater(len(encode(next_result)), 48 * 1024)
                self.assertEqual(app.ctx.probes, 64)
                self.assertEqual([obj.Attributes.Name for obj in rows], names)

    def test_combined_unicode_selection_bridge_roundtrip(self):
        for first_codepoint in (0x1F300, 0x4E00):
            with self.subTest(first_codepoint=first_codepoint):
                names = [chr(first_codepoint + i) * 256 for i in range(64)]
                app = app_fixture([row(i + 1, name) for i, name in enumerate(names)], names)
                bridge, peer, _sock = self.pair(self.reader(app))
                peer.sendall(self.request(tool="narwhal.selection_summary"))
                wire = bytearray()
                for _ in range(20):
                    self.assertEqual(bridge.pump(), .02)
                    self.assertFalse(bridge.closed)
                    wire.extend(self.receive(peer))
                    if b"\n" in wire:
                        break
                self.assertTrue(wire.endswith(b"\n"))
                self.assertLessEqual(len(wire), 65536)
                reply = json.loads(wire)
                self.assertEqual(set(reply), {"session", "seq", "result"})
                self.assertEqual((reply["session"], reply["seq"]), (SESSION, 1))
                result = reply["result"]
                self.assertLessEqual(len(json.dumps(result, ensure_ascii=True,
                                                     separators=(",", ":")).encode("ascii")),
                                     48 * 1024)
                self.assertTrue(result["truncated"])
                self.assertGreater(len(result["objects"]), 0)
                self.assertLess(len(result["objects"]), 64)
                self.assertEqual([obj["name"] for obj in result["objects"]],
                                 names[:len(result["objects"])])
                peer.sendall(self.request(seq=2))
                bridge.pump()
                followup = json.loads(self.receive(peer))
                self.assertEqual(followup["seq"], 2)
                self.assertEqual(followup["result"]["object_count"], 64)
                self.assertFalse(bridge.closed)

    def test_scan_limit_precedes_iteration_and_selection(self):
        app = app_fixture()
        app.model.file.Objects.reported = 10001
        reader = self.reader(app)
        for tool, args in (("narwhal.selection_summary", {}),
                           ("narwhal.object_summary", {"object_id": str(uuid.UUID(int=1))})):
            with self.assertRaisesRegex(self.tools.ReadError, "scan_limit"):
                reader.read(tool, args)
        self.assertEqual(app.model.file.Objects.iterations, 0)
        self.assertEqual(app.ctx.calls, 0)
        app.model.file.Objects.reported = 10000
        result = reader.read("narwhal.object_summary", {"object_id": str(uuid.UUID(int=1))})
        self.assertEqual(result["object_id"], str(uuid.UUID(int=1)))
        with self.assertRaisesRegex(self.tools.ReadError, "object_not_found"):
            reader.read("narwhal.object_summary", {"object_id": str(uuid.UUID(int=2))})

    def test_object_honesty_no_geometry_work_or_document_mutation(self):
        for declared, present in (("analytic-master", False), ("analytic-master", True),
                                  ("analytic", False), ("mesh-derived", False),
                                  ("", False)):
            geom_row = row(name="N" * 300, tags={"Narwhal3D:Representation": declared,
                                               "Narwhal3D:KernelShape": "test-master"})
            self.kernel._MASTERS.clear()
            if present:
                self.kernel._MASTERS["test-master"] = object()
            app = app_fixture([geom_row], [geom_row.Attributes.Name])
            before = (copy.deepcopy(vars(geom_row.Attributes)), dict(geom_row.Geometry.tags),
                      dict(self.kernel._MASTERS), dict(vars(app.model)), dict(vars(app)))
            reader = self.reader(app)
            result = reader.read("narwhal.object_summary", {"object_id": str(geom_row.Attributes.Id)})
            reader.read(TOOL, {})
            reader.read("narwhal.selection_summary", {})
            self.assertEqual(result["name"], "N" * 256)
            self.assertEqual(result["storage_type"], "Geometry")
            self.assertEqual(result["representation_declared"], declared or "unknown")
            self.assertEqual(result["master_present"], present)
            missing = declared == "analytic-master" and not present
            self.assertEqual(result["integrity"], "missing_master" if missing else "not_audited")
            self.assertEqual(result["geometry_source"], None if missing else
                             "occt_master" if present else "document_geometry")
            self.assertEqual(set(result), {"object_id", "name", "layer_index", "storage_type",
                                          "representation_declared", "master_present",
                                          "geometry_source", "integrity"})
            after = (vars(geom_row.Attributes), geom_row.Geometry.tags,
                     self.kernel._MASTERS, vars(app.model), vars(app))
            self.assertEqual(before, after)

    def test_timer_is_explicit_idempotent_and_does_not_own_close(self):
        callbacks = set()
        calls = []
        def register(callback, first_interval):
            callbacks.add(callback)
            calls.append(first_interval)
        timers = types.SimpleNamespace(is_registered=lambda cb: cb in callbacks,
                                       register=register, unregister=callbacks.remove)
        with patch.dict(sys.modules, {"bpy": types.SimpleNamespace(app=types.SimpleNamespace(timers=timers))}):
            bridge, _peer, _sock = self.pair()
            self.assertEqual(calls, [])
            self.bridge_module.register_timer(bridge)
            self.bridge_module.register_timer(bridge)
            self.assertEqual(calls, [.02])
            self.assertEqual(callbacks, {bridge.pump})
            self.bridge_module.unregister_timer(bridge)
            self.bridge_module.unregister_timer(bridge)
            self.assertFalse(bridge.closed)
            self.assertEqual(callbacks, set())
            bridge.close()
            with self.assertRaises(ValueError):
                self.bridge_module.register_timer(bridge)


MUTATIONS = (
    ("authentication", "automation_bridge", "if not hmac.compare_digest(session, self._session):", "if False:"),
    ("replay", "automation_bridge", "seq <= self._last_seq", "seq <= 0"),
    ("strict fields", "automation_bridge", "set(request) != _FIELDS", "not _FIELDS.issubset(request)"),
    ("boolean sequence", "automation_bridge", "type(seq) is not int", "not isinstance(seq, int)"),
    ("duplicate JSON keys", "automation_bridge", "if key in result:", "if False:"),
    ("receive budget", "automation_bridge", "RECV_BUDGET = 4096", "RECV_BUDGET = 8192"),
    ("send budget", "automation_bridge", "SEND_BUDGET = 4096", "SEND_BUDGET = 8192"),
    ("frame bound", "automation_bridge", "MAX_FRAME = 65_536", "MAX_FRAME = 131_072"),
    ("response bound", "automation_bridge", "len(output) + len(chunk) + 1 > MAX_FRAME", "False"),
    ("oversized response closes", "automation_bridge", "if code == \"result_too_large\":", "if False:"),
    ("post-read deadline", "automation_bridge", "time.monotonic() - self._pending_since >= _PENDING_TIMEOUT", "False"),
    ("one in-flight reply", "automation_bridge", "if not self._output and b\"\\n\" in self._input:", "if b\"\\n\" in self._input:"),
    ("EOF before queued input", "automation_bridge", "return self._socket.recv(1, sockets.MSG_PEEK) == b\"\"", "return False"),
    ("deadline", "automation_bridge", "_PENDING_TIMEOUT = 5.0", "_PENDING_TIMEOUT = 50.0"),
    ("secret-safe errors", "automation_bridge", "code = code if type(code) is str and code in SAFE_ERRORS else \"read_failed\"", "code = str(exc)"),
    ("main-thread guard", "automation_tools", "threading.current_thread() is not threading.main_thread()", "False"),
    ("scan limit", "automation_tools", "if len(objects) > MAX_SCAN:", "if False:"),
    ("selection cap", "automation_tools", "MAX_SELECTION = 64", "MAX_SELECTION = 128"),
    ("combined selection byte budget", "automation_tools", "if encoded_size + entry_size > MAX_SELECTION_BYTES:", "if False:"),
    ("argument allowlist", "automation_tools", "elif arguments:", "elif False:"),
    ("name bound", "automation_tools", "return value[:MAX_NAME]", "return value"),
    ("missing master on open surface", "automation_tools", "missing = declared == kernel.ANALYTIC_MASTER and not master_present", "missing = False"),
    ("unverified analytic claim", "automation_tools", "\"missing_master\" if missing else \"not_audited\"", "\"missing_master\" if missing else \"analytic_validated\""),
)


def run_suite(sources, verbosity=0):
    package, tools, bridge, kernel = load_modules(sources)
    cls = type("IsolatedControls", (Controls,), {
        "tools": tools, "bridge_module": bridge, "kernel": kernel})
    output = io.StringIO()
    try:
        result = unittest.TextTestRunner(stream=output, verbosity=verbosity).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(cls))
        return result, output.getvalue()
    finally:
        unload(package)


def main():
    result, text = run_suite(SOURCES, verbosity=2)
    print(text, end="")
    if not result.wasSuccessful():
        return 1
    for label, module, old, new in MUTATIONS:
        if SOURCES[module].count(old) != 1:
            print("FAIL: mutation anchor", label)
            return 1
        sources = dict(SOURCES)
        sources[module] = sources[module].replace(old, new, 1)
        mutant, _text = run_suite(sources)
        if mutant.wasSuccessful():
            print("FAIL: surviving mutation", label)
            return 1
        print("RED confirmed:", label)
    restored, text = run_suite(SOURCES)
    if not restored.wasSuccessful():
        print(text)
        return 1
    print(f"PASS: {result.testsRun} controls; {len(MUTATIONS)} in-memory mutations RED; original GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
