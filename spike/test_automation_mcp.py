#!/usr/bin/env python3
"""External stdio MCP client; mock transport tests or real disposable Blender demo.

No provider APIs, user documents or UI registration. --live starts background
Blender with the real application state/model and an explicitly attached adapter.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import secrets
import select
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SERVER = None
SESSION = "0123456789abcdef" * 2
UUID = "12345678-1234-1234-1234-123456789abc"
TOOLS = {"narwhal.application_info", "narwhal.document_summary",
         "narwhal.selection_summary", "narwhal.object_summary", "narwhal.objects",
         "narwhal.layers", "narwhal.workspace", "narwhal.components", "narwhal.operations"}


class Client:
    def __init__(self, server, endpoint, session=SESSION, *, modern=False):
        self.modern = modern
        env = {"PATH": os.defpath, "NARWHAL_AUTOMATION_FD": str(endpoint.fileno()),
               "NARWHAL_AUTOMATION_SESSION": session}
        self.process = subprocess.Popen([str(server)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        pass_fds=(endpoint.fileno(),), env=env, bufsize=0)
        self.buffer = bytearray()

    def send(self, message):
        self.raw(json.dumps(message, separators=(",", ":")).encode() + b"\n")

    def raw(self, frame):
        self.process.stdin.write(frame)

    def request(self, rid, method, params=None):
        if self.modern:
            params = {**(params or {}), "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
            }}
        request = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            request["params"] = params
        self.send(request)

    def receive(self, timeout=8):
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
                raise AssertionError("MCP response timeout")
            chunk = os.read(self.process.stdout.fileno(), 196608)
            if not chunk:
                raise AssertionError("MCP stdout closed")
            self.buffer.extend(chunk)
            if len(self.buffer) > 393216:
                raise AssertionError("unbounded MCP output")
        line, _, rest = self.buffer.partition(b"\n")
        self.buffer = bytearray(rest)
        value = json.loads(line)
        assert value["jsonrpc"] == "2.0", "stdout must be protocol-only"
        return value

    def initialize(self, version="2025-11-25"):
        self.request("init", "initialize", {"protocolVersion": version,
                     "capabilities": {}, "clientInfo": {"name": "narwhal-external-test", "version": "1"}})
        reply = self.receive()
        assert reply["id"] == "init"
        assert reply["result"]["protocolVersion"] == "2025-11-25"
        assert set(reply["result"]["capabilities"]) == {"tools"}
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return reply

    def call(self, rid, tool="narwhal.document_summary", arguments=None):
        self.request(rid, "tools/call", {"name": tool, "arguments": arguments or {}})

    def close(self):
        if not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)
            raise AssertionError("server failed to stop after stdin EOF")
        diagnostics = self.process.stderr.read()
        self.process.stderr.close()
        self.process.stdout.close()
        assert b"private-canary" not in diagnostics
        assert SESSION.encode() not in diagnostics
        return diagnostics


@contextlib.contextmanager
def mock_client(initialize=True):
    app, server = socket.socketpair()
    app.settimeout(8)
    client = Client(SERVER, server)
    server.close()
    try:
        if initialize:
            client.initialize()
        yield client, app
    finally:
        app.close()
        client.close()


def bridge_request(app):
    data = bytearray()
    while not data.endswith(b"\n"):
        chunk = app.recv(4096)
        assert chunk, "bridge unexpectedly disconnected"
        data.extend(chunk)
        assert len(data) <= 65536
    return json.loads(data)


def bridge_reply(app, request, result=None, **changes):
    reply = {"session": request["session"], "seq": request["seq"],
             "result": result if result is not None else {"fixture": True}}
    reply.update(changes)
    app.sendall(json.dumps(reply).encode() + b"\n")


class ProtocolTests(unittest.TestCase):
    def test_requires_inherited_capability_and_safe_startup(self):
        for values in ({}, {"NARWHAL_AUTOMATION_FD": "999999",
                            "NARWHAL_AUTOMATION_SESSION": SESSION}):
            result = subprocess.run([str(SERVER)], input=b"", capture_output=True,
                                    env={"PATH": os.defpath, "OPENAI_API_KEY": "private-canary", **values},
                                    timeout=3)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, b"")
            self.assertNotIn(b"private-canary", result.stderr)
            self.assertNotIn(SESSION.encode(), result.stderr)

    def test_sigterm_closes_pending_read(self):
        with mock_client() as (client, app):
            client.call(1)
            bridge_request(app)
            client.process.terminate()
            client.process.wait(timeout=3)
            self.assertEqual(app.recv(1), b"")

    def test_handshake_tools_and_ids(self):
        with mock_client() as (client, app):
            client.request(7, "tools/list")
            reply = client.receive()
            self.assertEqual(reply["id"], 7)
            self.assertEqual({t["name"] for t in reply["result"]["tools"]}, TOOLS)
            for tool in reply["result"]["tools"]:
                self.assertTrue(tool["annotations"]["readOnlyHint"])
                self.assertFalse(tool["annotations"]["openWorldHint"])
                self.assertFalse(tool["inputSchema"]["additionalProperties"])
            client.call("read")
            request = bridge_request(app)
            self.assertEqual(request, {"session": SESSION, "seq": 1,
                                      "tool": "narwhal.document_summary", "arguments": {}})
            bridge_reply(app, request, {"object_count": 42})
            reply = client.receive()
            self.assertEqual(reply["id"], "read")
            self.assertEqual(reply["result"]["structuredContent"], {"object_count": 42})
            self.assertEqual(json.loads(reply["result"]["content"][0]["text"]), {"object_count": 42})

    def test_version_negotiation(self):
        with mock_client(False) as (client, _):
            client.initialize("1900-01-01")

    def test_initialization_gate(self):
        with mock_client(False) as (client, app):
            client.call(1)
            # Without a legacy handshake these are malformed modern requests;
            # 2026-07-28 requires -32602 for missing per-request metadata.
            self.assertEqual(client.receive()["error"]["code"], -32602)
            client.request(2, "server/discover")
            self.assertEqual(client.receive()["error"]["code"], -32602)
            client.initialize()
            client.request(3, "ping")
            self.assertIn("result", client.receive())
            self.assertFalse(select.select([app], [], [], 0)[0])

    def test_permission_boundary(self):
        with mock_client() as (client, app):
            for rid, name in enumerate(("narwhal.execute", "Delete", "eval", "shell"), 1):
                client.call(rid, name)
                self.assertEqual(client.receive()["error"]["code"], -32602)
            client.call(5, arguments={"execute": "private-canary"})
            self.assertTrue(client.receive()["result"]["isError"])
            client.call(6, "narwhal.object_summary", {"object_id": "../private-canary"})
            self.assertTrue(client.receive()["result"]["isError"])
            client.request(7, "tools/call", {"name": "narwhal.document_summary", "arguments": {}, "task": {}})
            self.assertEqual(client.receive()["error"]["code"], -32602)
            self.assertFalse(select.select([app], [], [], 0)[0], "refused calls must not reach application")

    def test_duplicate_request_id(self):
        with mock_client() as (client, app):
            client.request(1, "tools/list")
            self.assertIn("result", client.receive())
            client.request(1, "tools/list")
            self.assertEqual(client.receive()["error"]["code"], -32600)
            self.assertFalse(select.select([app], [], [], 0)[0])

    def test_malformed_and_duplicate_json(self):
        with mock_client() as (client, _):
            for frame in (b"{no-json}\n", b'[{"jsonrpc":"2.0","id":3,"method":"ping"}]\n',
                          b'{"jsonrpc":"2.0","id":1,"id":2,"method":"ping"}\n',
                          b'{"jsonrpc":"2.0","id":null,"method":"ping"}\n',
                          b'{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":2}}\n'):
                client.raw(frame)
                self.assertIn("error", client.receive())
            client.request(4, "ping")
            self.assertEqual(client.receive()["id"], 4)

    def test_oversized_frame_closes(self):
        with mock_client() as (client, _):
            client.raw(b"x" * 65536 + b"\n")
            self.assertEqual(client.receive()["error"]["code"], -32700)
            client.process.wait(timeout=3)

    def test_bridge_identity(self):
        for changes in ({"session": "0" * 32}, {"seq": 7}):
            with mock_client() as (client, app):
                client.call(1)
                request = bridge_request(app)
                bridge_reply(app, request, **changes)
                reply = client.receive()
                self.assertTrue(reply["result"]["isError"])
                self.assertNotIn("structuredContent", reply["result"])
                self.assertEqual(app.recv(1), b"")

    def test_bridge_size_and_safe_errors(self):
        with mock_client() as (client, app):
            client.call(1)
            request = bridge_request(app)
            app.sendall(json.dumps({"session": SESSION, "seq": request["seq"],
                                    "error": "private-canary"}).encode() + b"\n")
            reply = client.receive()
            self.assertTrue(reply["result"]["isError"])
            self.assertNotIn("private-canary", json.dumps(reply))
        with mock_client() as (client, app):
            client.call(1)
            bridge_request(app)
            app.sendall(b"x" * 65536 + b"\n")
            self.assertTrue(client.receive()["result"]["isError"])
            # Closing a stream with unread over-limit bytes may signal RST rather
            # than FIN; either must prove the capability channel is no longer usable.
            try:
                self.assertEqual(app.recv(1), b"")
            except ConnectionResetError:
                pass

    def test_busy_rate_limit_and_cancel(self):
        with mock_client() as (client, app):
            client.call(1)
            bridge_request(app)
            client.call(2)
            busy = client.receive()
            self.assertEqual(busy["id"], 2)
            self.assertEqual(busy["result"]["content"][0]["text"], "application_busy")
            client.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                         "params": {"requestId": 1, "reason": "private-canary"}})
            self.assertEqual(app.recv(1), b"", "cancel must close inherited channel")
            time.sleep(.03)
            client.call(3)
            # Cancellation responses may race; the SDK normally suppresses them.
            reply = client.receive()
            if reply.get("id") == 1:
                reply = client.receive()
            self.assertEqual(reply["id"], 3)
            self.assertTrue(reply["result"]["isError"])
        with mock_client() as (client, app):
            client.call(1)
            request = bridge_request(app)
            bridge_reply(app, request)
            client.receive()
            client.call(2)
            reply = client.receive()
            self.assertEqual(reply["result"]["content"][0]["text"], "rate_limited")

    def test_malformed_cancel_does_not_cancel(self):
        with mock_client() as (client, app):
            client.call(1)
            request = bridge_request(app)
            client.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                         "params": {"requestId": 1, "reason": 123}})
            client.request(2, "ping")
            self.assertEqual(client.receive()["id"], 2)
            self.assertFalse(select.select([app], [], [], .03)[0])
            bridge_reply(app, request)
            self.assertEqual(client.receive()["id"], 1)

    def test_error_reply_survives_concurrent_requests(self):
        with mock_client() as (client, app):
            client.call(1)
            request = bridge_request(app)
            client.raw(b"{not-json}\n")
            client.request(2, "ping")
            replies = [client.receive(), client.receive()]
            self.assertTrue(any(r.get("error", {}).get("code") == -32700 for r in replies))
            self.assertTrue(any(r.get("id") == 2 and "result" in r for r in replies))
            bridge_reply(app, request)
            self.assertEqual(client.receive()["id"], 1)

    def test_stalled_stdout_is_terminal(self):
        import fcntl
        with mock_client() as (client, app):
            fcntl.fcntl(client.process.stdout.fileno(), fcntl.F_SETPIPE_SZ, 4096)
            # Stay below request/error admission limits: only the writer's
            # deadline, not overload shutdown, may satisfy this regression.
            for rid in range(8):
                client.request(rid, "tools/list")
            # Do not consume output. The bounded writer's deadline must terminate
            # rather than append further frames after a partial response.
            client.process.wait(timeout=7)
            self.assertEqual(app.recv(1), b"")

    def test_timeout_disconnect_and_eof(self):
        with mock_client() as (client, app):
            client.call(1)
            bridge_request(app)
            reply = client.receive(timeout=7)
            self.assertEqual(reply["result"]["content"][0]["text"], "application_timeout_relaunch_session")
            self.assertEqual(app.recv(1), b"")
        with mock_client() as (client, app):
            client.call(1)
            bridge_request(app)
            app.shutdown(socket.SHUT_RDWR)
            self.assertTrue(client.receive()["result"]["isError"])
        with mock_client() as (client, app):
            client.call(1)
            bridge_request(app)
            client.process.stdin.close()
            client.process.wait(timeout=3)
            self.assertEqual(app.recv(1), b"")


def live_demo(server, blender, *, modern=False):
    """The test controller is outside Blender and owns MCP stdin/stdout."""
    with tempfile.TemporaryDirectory(prefix="narwhal-mcp-live-") as work:
        app_end, server_end = socket.socketpair()
        session = secrets.token_hex(16)
        env = os.environ.copy()
        env.update({"NARWHAL_AUTOMATION_FD": str(app_end.fileno()),
                    "NARWHAL_AUTOMATION_SESSION": session,
                    "NARWHAL_AUTOMATION_TEST_DIR": work,
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "BLENDER_USER_CONFIG": work, "BLENDER_USER_SCRIPTS": work,
                    "PYTHONPATH": str(ROOT / "vendor/occt")})
        # stdout/stderr stay private and are not MCP output or product logs.
        with tempfile.TemporaryFile() as log:
            app = subprocess.Popen([str(blender), "-b", "--factory-startup", "--threads", "2",
                                    "--python-exit-code", "1", "--python",
                                    str(ROOT / "spike/test_automation_live.py")],
                                   env=env, pass_fds=(app_end.fileno(),), stdout=log, stderr=log)
            app_end.close()
            client = None
            try:
                ready = Path(work) / "ready.json"
                deadline = time.monotonic() + 45
                while not ready.exists():
                    if app.poll() is not None or time.monotonic() >= deadline:
                        log.seek(0)
                        raise AssertionError("Blender bootstrap failed: " + log.read().decode(errors="replace")[-8000:])
                    time.sleep(.02)
                fixture = json.loads(ready.read_text())
                client = Client(server, server_end, session, modern=modern)
                server_end.close()
                if modern:
                    client.request("discover", "server/discover")
                    discovery = client.receive()["result"]
                    assert set(discovery["supportedVersions"]) == {"2025-11-25", "2026-07-28"}
                    assert discovery["resultType"] == "complete"
                else:
                    client.initialize()
                client.request(1, "tools/list")
                assert {t["name"] for t in client.receive()["result"]["tools"]} == TOOLS

                def query(rid, name, args=None):
                    time.sleep(.03)
                    client.call(rid, name, args)
                    reply = client.receive()
                    assert reply["id"] == rid and not reply["result"].get("isError"), reply
                    if modern:
                        assert reply["result"]["resultType"] == "complete"
                    return reply["result"]["structuredContent"]

                info = query(2, "narwhal.application_info")
                assert info["permissions"] == {"read_only": True, "mutations": False}
                assert info["blender_version"] == fixture["blender_version"]
                before = query(3, "narwhal.document_summary")
                assert before["object_count"] == 2 and before["revision"] is None
                selected = query(4, "narwhal.selection_summary")
                assert selected["scope"] == "whole_objects"
                assert [o["object_id"] for o in selected["objects"]] == [fixture["circle_id"]]
                circle = query(5, "narwhal.object_summary", {"object_id": fixture["circle_id"]})
                assert circle["name"] == fixture["name"] and circle["storage_type"] == "NurbsCurve"
                assert circle["representation_declared"] == "analytic"
                assert circle["integrity"] == "not_audited"
                client.call(6, "narwhal.execute", {"command": "Delete"})
                assert client.receive()["error"]["code"] == -32602
                after = query(7, "narwhal.document_summary")
                assert before == after, "refused mutation changed document"
                # A local test-only file triggers a real main-thread model change.
                # It is NOT an automation command or a production adapter feature.
                (Path(work) / "advance").write_text("fixture")
                deadline = time.monotonic() + 10
                while not (Path(work) / "advanced").exists():
                    assert time.monotonic() < deadline
                    time.sleep(.02)
                changed = query(8, "narwhal.document_summary")
                assert changed["object_count"] == 3, "MCP returned canned/stale document data"
                assert not query(9, "narwhal.selection_summary")["objects"]
                client.close()
                client = None
                app.wait(timeout=10)
                log.seek(0)
                output = log.read().decode(errors="replace")
                assert app.returncode == 0 and "AUTOMATION_LIVE_PASS" in output, output[-8000:]
                assert fixture["name"] not in output and session not in output
                print("LIVE PASS:", "modern" if modern else "legacy",
                      "external discovery + four tools; live 2→3 objects and selection change; refused mutation unchanged; main-thread and EOF checks")
            finally:
                server_end.close()
                if client is not None:
                    client.close()
                if app.poll() is None:
                    app.kill()
                    app.wait(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True, type=Path)
    parser.add_argument("--live", type=Path, metavar="BLENDER")
    parser.add_argument("--era", choices=("legacy", "modern"), default="legacy",
                        help="Protocol used by the disposable live demo")
    parser.add_argument("--test", help="One ProtocolTests method, used for isolated mutations")
    args = parser.parse_args()
    SERVER = args.server.resolve()
    if args.live:
        live_demo(SERVER, args.live.resolve(), modern=args.era == "modern")
    else:
        suite = (unittest.defaultTestLoader.loadTestsFromName(args.test, ProtocolTests)
                 if args.test else unittest.defaultTestLoader.loadTestsFromTestCase(ProtocolTests))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(not result.wasSuccessful())
