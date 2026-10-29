#!/usr/bin/env python3
"""Dual-era MCP acceptance tests against an already-built Rust server, no GUI.

Python is only the external process/socketpair test orchestrator; production
protocol handling belongs to Rust. Reuses test_automation_mcp's mock capability
channel and legacy client, without loading or changing its ProtocolTests.

Run this script for modern/dual-era coverage and test_automation_mcp.py for the
legacy-only suite. --test accepts exactly one ModernTests method for mutations.
No build, provider, real application, user document, or filesystem tool is used.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import select
import sys
import time
import unittest

# Also support unittest importing this file from outside the spike directory.
# Do not create sibling bytecode while importing the shared fixture.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_automation_mcp as base


MODERN = "2026-07-28"
LEGACY = "2025-11-25"
VERSIONS = {LEGACY, MODERN}
VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
CAPABILITIES_KEY = "io.modelcontextprotocol/clientCapabilities"
CLIENT_INFO_KEY = "io.modelcontextprotocol/clientInfo"
SERVER_INFO_KEY = "io.modelcontextprotocol/serverInfo"
_DEFAULT = object()


def modern_meta(*, version=MODERN, capabilities=_DEFAULT, client_info=_DEFAULT):
    meta = {VERSION_KEY: version,
            CAPABILITIES_KEY: {} if capabilities is _DEFAULT else capabilities}
    if client_info is not _DEFAULT:
        meta[CLIENT_INFO_KEY] = client_info
    return meta


class ModernClient:
    """Explicit modern requests over the base client; no implicit initialize.

    Use .wire for deliberately legacy or malformed input. Metadata None omits
    _meta, while the default supplies the two required keys, not clientInfo.
    """

    def __init__(self, wire):
        self.wire = wire

    def raw(self, frame, timeout=3):
        # Unlike a blocking write, this also bounds a failed/backpressured test's
        # input path. Write every byte even when the pipe accepts a short write.
        fd = self.wire.process.stdin.fileno()
        blocking = os.get_blocking(fd)
        os.set_blocking(fd, False)
        deadline = time.monotonic() + timeout
        try:
            remaining = memoryview(frame)
            while remaining:
                wait = deadline - time.monotonic()
                if wait <= 0 or not select.select([], [fd], [], wait)[1]:
                    raise AssertionError("MCP input write timeout")
                try:
                    count = os.write(fd, remaining)
                except BlockingIOError:
                    continue
                if count <= 0:
                    raise AssertionError("MCP input write made no progress")
                remaining = remaining[count:]
        finally:
            os.set_blocking(fd, blocking)

    def send(self, message):
        self.raw(json.dumps(message, separators=(",", ":")).encode() + b"\n")

    def request(self, rid, method="tools/list", params=None, *, meta=_DEFAULT):
        params = dict(params or {})
        if meta is not None:
            params["_meta"] = modern_meta() if meta is _DEFAULT else meta
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})

    def call(self, rid, tool="narwhal.document_summary", arguments=None, *, meta=_DEFAULT):
        self.request(rid, "tools/call", {"name": tool, "arguments": arguments or {}}, meta=meta)

    def receive(self, timeout=8):
        return self.wire.receive(timeout)

    def cancel(self, rid):
        self.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                   "params": {"requestId": rid, "reason": "test cancellation"}})

    def discover_and_select(self, preferred=MODERN):
        """Select from successful discovery, optionally taking the legacy path.

        This exercises real dual-era selection, not a simulated error fallback.
        Discovery errors fail closed; they are not permission to downgrade.
        """
        self.request("discover", "server/discover")
        reply = self.receive()
        assert reply.get("id") == "discover" and "result" in reply, reply
        supported = reply["result"]["supportedVersions"]
        selected = preferred if preferred in supported else LEGACY
        assert selected in supported, reply
        if selected == LEGACY:
            self.wire.initialize()
        return selected, reply


@contextmanager
def mock_modern(*, legacy=False):
    with base.mock_client(False) as (wire, app):
        if legacy:
            wire.initialize()
        yield ModernClient(wire), app


class ModernTests(unittest.TestCase):
    def assert_reply(self, client, rid):
        reply = client.receive()
        self.assertEqual(reply.get("id"), rid, reply)
        self.assertIs(type(reply.get("id")), type(rid), reply)
        self.assertNotEqual("result" in reply, "error" in reply, reply)
        return reply

    def assert_error(self, client, rid, code):
        reply = self.assert_reply(client, rid)
        self.assertEqual(reply.get("error", {}).get("code"), code, reply)
        return reply["error"]

    def assert_server_info(self, result):
        info = result.get("_meta", {}).get(SERVER_INFO_KEY)
        self.assertIsInstance(info, dict, result)
        self.assertEqual(info.get("name"), "narwhal-read-only", info)
        self.assertIsInstance(info.get("version"), str, info)
        self.assertTrue(info["version"], info)
        self.assertNotIn("serverInfo", result)

    def assert_discovery(self, reply):
        result = reply["result"]
        self.assertCountEqual(result["supportedVersions"], VERSIONS)
        self.assertEqual(result["capabilities"], {"tools": {}})
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(result["ttlMs"], 0)
        self.assertEqual(result["cacheScope"], "private")
        self.assert_server_info(result)

    def assert_list(self, client, rid):
        reply = self.assert_reply(client, rid)
        result = reply["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(result["ttlMs"], 0)
        self.assertEqual(result["cacheScope"], "private")
        self.assertEqual({tool["name"] for tool in result["tools"]}, base.TOOLS)
        self.assertEqual(len(result["tools"]), len(base.TOOLS))
        for tool in result["tools"]:
            self.assertTrue(tool["annotations"]["readOnlyHint"])
            self.assertFalse(tool["annotations"]["openWorldHint"])
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
        return result

    def assert_no_app_read(self, app):
        self.assertFalse(select.select([app], [], [], .04)[0],
                         "unexpected application request or bridge closure")

    def assert_quiet(self, client):
        self.assertFalse(client.wire.buffer, "unexpected buffered MCP output")
        self.assertIsNone(client.wire.process.poll(), "server unexpectedly exited")
        self.assertFalse(select.select([client.wire.process.stdout], [], [], .08)[0],
                         "unsolicited, cancelled, or duplicate response")

    def assert_bridge_closed(self, app):
        self.assertTrue(select.select([app], [], [], 3)[0], "bridge remained open")
        try:
            self.assertEqual(app.recv(1), b"")
        except ConnectionResetError:
            pass

    def complete_read(self, client, app, rid, *, seq=1, data=None,
                      tool="narwhal.document_summary", arguments=None):
        request = base.bridge_request(app)
        self.assertEqual(request, {"session": base.SESSION, "seq": seq,
                                   "tool": tool, "arguments": arguments or {}})
        data = {"object_count": 42} if data is None else data
        base.bridge_reply(app, request, data)
        result = self.assert_reply(client, rid)["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertFalse(result.get("isError", False))
        self.assertEqual(result["structuredContent"], data)
        self.assertEqual(json.loads(result["content"][0]["text"]), data)
        # Product acceptance of the modern serverInfo SHOULD, on tool results.
        self.assert_server_info(result)
        return request

    def test_discovery_selects_modern_without_initialize(self):
        with mock_modern() as (client, app):
            selected, reply = client.discover_and_select()
            self.assertEqual(selected, MODERN)
            self.assert_discovery(reply)
            client.request("list")
            self.assert_list(client, "list")
            self.assert_no_app_read(app)
            self.assert_quiet(client)

    def test_direct_list_and_read_without_initialize(self):
        with mock_modern() as (client, app):
            client.request(1)
            self.assert_list(client, 1)
            self.assert_no_app_read(app)
            seq = 0
            for tool in sorted(base.TOOLS):
                if tool == "narwhal.operations":
                    client.call("operations", tool)
                    data = self.assert_reply(client, "operations")["result"]["structuredContent"]
                    self.assertFalse(data["operations"][0]["execution_available"])
                    self.assert_no_app_read(app)
                    continue
                seq += 1
                arguments = ({"object_id": base.UUID} if tool == "narwhal.object_summary" else
                             {"offset": 0, "limit": 2} if tool in ("narwhal.objects", "narwhal.layers") else {})
                time.sleep(.03)  # The private read channel intentionally rate limits.
                client.call(f"read-{seq}", tool, arguments)
                self.complete_read(client, app, f"read-{seq}", seq=seq,
                                   tool=tool, arguments=arguments, data={"fixture_tool": tool})

    def test_discovery_delay_does_not_timeout_first_read(self):
        with mock_modern() as (client, app):
            client.request("discover", "server/discover")
            self.assert_discovery(self.assert_reply(client, "discover"))
            time.sleep(5.3)  # Must exceed the old five-second serve() timeout.
            self.assertIsNone(client.wire.process.poll(), "discovery kept the legacy init deadline")
            client.call("delayed-read")
            self.complete_read(client, app, "delayed-read")

    def test_optional_client_info_absent_valid_and_malformed(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy), mock_modern(legacy=legacy) as (client, app):
                for rid, meta in (("absent", modern_meta()),
                                  ("valid", modern_meta(client_info={"name": "test", "version": "1"}))):
                    client.request(rid, meta=meta)
                    self.assert_list(client, rid)
                for i, info in enumerate((None, 7, "bad", {}, {"name": "test", "version": 3})):
                    client.request(f"bad-info-{i}", meta=modern_meta(client_info=info))
                    self.assert_error(client, f"bad-info-{i}", -32602)
                self.assert_no_app_read(app)

    def test_required_metadata_types_and_legacy_fallback_boundary(self):
        malformed = [
            {CAPABILITIES_KEY: {}}, {VERSION_KEY: MODERN},
            *[modern_meta(version=value) for value in (None, 3, True, {})],
            *[modern_meta(capabilities=value) for value in (None, 3, "bad", [])],
            *[modern_meta(capabilities={key: None}) for key in
              ("extensions", "roots", "sampling", "elicitation", "experimental")],
            modern_meta(capabilities={"sampling": {"tools": None, "context": None}}),
            modern_meta(capabilities={"elicitation": {"form": None, "url": None}}),
            modern_meta(capabilities={"roots": {"listChanged": None}}),
            modern_meta(capabilities={"elicitation": {"form": {"schemaValidation": None}}}),
        ]
        for mode in ("bootstrap", "modern", "legacy"):
            with self.subTest(mode=mode), mock_modern(legacy=mode == "legacy") as (client, app):
                if mode == "modern":
                    client.request("open")
                    self.assert_list(client, "open")
                cases = malformed + ([None, {}] if mode == "modern" else [])
                for i, meta in enumerate(cases):
                    client.call(f"bad-meta-{i}", meta=meta)
                    self.assert_error(client, f"bad-meta-{i}", -32602)
                if mode == "legacy":
                    # A genuinely metadata-free request is still legacy, not an
                    # incomplete modern request. Do not outlaw the supported era.
                    client.wire.request("legacy-still-valid", "tools/list")
                    result = self.assert_reply(client, "legacy-still-valid")["result"]
                    self.assertNotIn("resultType", result)
                client.request("valid-after-errors")
                self.assert_list(client, "valid-after-errors")
                self.assert_no_app_read(app)

    def test_unknown_version_reports_supported_and_allows_retry(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy), mock_modern(legacy=legacy) as (client, app):
                for i, method in enumerate(("server/discover", "tools/list")):
                    client.request(i, method, meta=modern_meta(version="2099-01-01"))
                    error = self.assert_error(client, i, -32022)
                    self.assertEqual(error["data"]["requested"], "2099-01-01")
                    self.assertCountEqual(error["data"]["supported"], VERSIONS)
                client.request("retry")
                self.assert_list(client, "retry")
                self.assert_no_app_read(app)

    def test_extensions_validate_without_mirroring_or_requiring_capabilities(self):
        with mock_modern() as (client, app):
            # Official basic/_meta permits an empty name, not an empty prefix label.
            meta = modern_meta(capabilities={"extensions": {
                "com.example/extra": {}, "com.example/": {},
                "io.modelcontextprotocol/ui": {"mimeTypes": ["text/html;profile=mcp-app"]},
                "io.modelcontextprotocol/tasks": {},
            }})
            client.request("extensions", "server/discover", meta=meta)
            self.assert_discovery(self.assert_reply(client, "extensions"))
            client.call("core-fallback", meta=meta)
            self.complete_read(client, app, "core-fallback")
            bad_extensions = [{key: {}} for key in ("", "no-slash", "/extra", "com..example/extra")]
            bad_extensions += [{"com.example/extra": value} for value in (None, 4, [], "bad")]
            for i, extensions in enumerate(bad_extensions):
                client.call(f"bad-extension-{i}",
                            meta=modern_meta(capabilities={"extensions": extensions}))
                self.assert_error(client, f"bad-extension-{i}", -32602)
            self.assert_no_app_read(app)
            # No MissingRequiredClientCapability: these core reads need none.
            client.call("empty-capabilities")
            self.complete_read(client, app, "empty-capabilities", seq=2)

    def test_task_mrtr_and_authority_extras_never_reach_application(self):
        with mock_modern() as (client, app):
            client.request("open")
            self.assert_list(client, "open")
            for extra, value in (("task", {}), ("requestState", "private-canary"),
                                 ("inputResponses", [])):
                client.request(extra, "tools/call", {"name": "narwhal.document_summary",
                                                     "arguments": {}, extra: value})
                self.assert_error(client, extra, -32602)
            for i, method in enumerate(("tasks/get", "subscriptions/listen", "resources/read",
                                        "sampling/createMessage")):
                client.request(f"method-{i}", method)
                self.assert_error(client, f"method-{i}", -32601)
            for i, tool in enumerate(("narwhal.execute", "Delete", "eval", "shell")):
                client.call(f"tool-{i}", tool)
                self.assert_error(client, f"tool-{i}", -32602)
            for i, arguments in enumerate(({"path": "/private-canary"}, {"fd": app.fileno()},
                                            {"session": "0" * 32}, {"execute": "Delete"},
                                            {"protocolVersion": LEGACY})):
                client.call(f"arguments-{i}", arguments=arguments)
                reply = self.assert_reply(client, f"arguments-{i}")
                if "error" in reply:
                    self.assertEqual(reply["error"]["code"], -32602)
                else:
                    self.assertTrue(reply["result"]["isError"])
                    self.assertNotIn("structuredContent", reply["result"])
            self.assert_no_app_read(app)
            client.call("safe-after-refusals")
            self.complete_read(client, app, "safe-after-refusals", seq=1)

    def test_completed_modern_id_reuse_keeps_private_sequence_monotonic(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy), mock_modern(legacy=legacy) as (client, app):
                for seq in range(1, 4):
                    time.sleep(.03)
                    client.call("reusable")
                    self.complete_read(client, app, "reusable", seq=seq, data={"sequence": seq})
                    client.request("reusable")
                    self.assert_list(client, "reusable")
                self.assert_quiet(client)

    def test_overlapping_id_correlation_and_cancelled_id_quarantine(self):
        with mock_modern() as (client, app):
            client.call("active")
            request = base.bridge_request(app)
            client.request("active")
            self.assert_error(client, "active", -32600)
            client.request("barrier")
            self.assert_list(client, "barrier")
            self.assert_no_app_read(app)
            base.bridge_reply(app, request, {"original": True})
            result = self.assert_reply(client, "active")["result"]
            self.assertEqual(result["structuredContent"], {"original": True})
            self.assertEqual(request["seq"], 1)
            self.assert_quiet(client)
        # A separate process keeps the successful-completion and cancellation
        # branches independent: both must survive an overlapping-ID rejection.
        with mock_modern() as (client, app):
            client.call("cancel-me")
            base.bridge_request(app)
            client.request("cancel-me")
            self.assert_error(client, "cancel-me", -32600)
            client.cancel("cancel-me")
            self.assert_bridge_closed(app)
            client.request("after-cancel")
            self.assert_list(client, "after-cancel")  # Must not receive a cancellation result first.
            client.request("cancel-me")
            self.assert_error(client, "cancel-me", -32600)
            self.assert_quiet(client)

    def test_cancel_accepts_notification_metadata(self):
        with mock_modern() as (client, app):
            client.call("cancel-with-meta")
            base.bridge_request(app)
            client.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                         "params": {"requestId": "cancel-with-meta", "_meta": {}}})
            self.assert_bridge_closed(app)
            client.request("after-meta-cancel")
            self.assert_list(client, "after-meta-cancel")
            self.assert_quiet(client)

    def test_bootstrap_discovery_cancel_does_not_kill_transport(self):
        with mock_modern() as (client, app):
            request = {"jsonrpc": "2.0", "id": "cancel-discovery", "method": "server/discover",
                       "params": {"_meta": modern_meta()}}
            cancel = {"jsonrpc": "2.0", "method": "notifications/cancelled",
                      "params": {"requestId": "cancel-discovery"}}
            client.raw(json.dumps(request).encode() + b"\n" + json.dumps(cancel).encode() + b"\n")
            client.request("next-discovery", "server/discover")
            reply = client.receive()
            if reply.get("id") == "cancel-discovery":
                # Completion may precede cancellation; the next discovery must
                # still work, rather than reaching SDK ExpectedInitializeRequest.
                reply = client.receive()
            self.assertEqual(reply["id"], "next-discovery")
            self.assert_discovery(reply)
            self.assert_no_app_read(app)

    def test_cancel_suppresses_response_queued_behind_stdout(self):
        import fcntl
        with mock_modern() as (client, app):
            client.request("open")
            self.assert_list(client, "open")
            fcntl.fcntl(client.wire.process.stdout.fileno(), fcntl.F_SETPIPE_SZ, 4096)
            client.call("queued-read")
            request = base.bridge_request(app)
            blockers = {f"block-{i}" for i in range(8)}
            for rid in sorted(blockers):
                client.request(rid)
            # Eight full catalog results exceed the 4 KiB pipe. Leave it unread
            # while the application completes, so its response queues behind them.
            self.assertTrue(select.select([client.wire.process.stdout], [], [], 2)[0])
            time.sleep(.15)
            base.bridge_reply(app, request, {"must_not_escape": True})
            time.sleep(.15)
            client.cancel("queued-read")
            client.request("cancel-barrier")
            time.sleep(.15)
            expected = blockers | {"cancel-barrier"}
            for _ in range(len(expected)):
                reply = client.receive(timeout=3)
                self.assertIn(reply.get("id"), expected, reply)
                expected.remove(reply["id"])
                self.assertIn("result", reply)
                self.assertEqual(reply["result"]["resultType"], "complete")
            self.assertFalse(expected)
            client.request("queued-read")
            self.assert_error(client, "queued-read", -32600)
            self.assert_quiet(client)

    def test_numeric_and_string_ids_have_independent_cancellation(self):
        with mock_modern() as (client, app):
            client.call(7)
            request = base.bridge_request(app)
            client.request("7")
            self.assert_list(client, "7")
            client.cancel("7")  # Completed string ID must not cancel numeric 7.
            client.request("barrier")
            self.assert_list(client, "barrier")
            self.assert_no_app_read(app)
            base.bridge_reply(app, request, {"numeric": True})
            self.assertEqual(self.assert_reply(client, 7)["result"]["structuredContent"],
                             {"numeric": True})
            time.sleep(.03)
            client.call("7")
            self.complete_read(client, app, "7", seq=2, data={"string": True})

    def test_legacy_origin_ids_cannot_be_reused_by_modern_requests(self):
        with mock_modern(legacy=True) as (client, app):
            client.wire.request(7, "tools/list")
            self.assertNotIn("resultType", self.assert_reply(client, 7)["result"])
            for rid in (7, "init"):
                client.request(rid)
                self.assert_error(client, rid, -32600)
            client.call("modern-after-legacy")
            self.complete_read(client, app, "modern-after-legacy", seq=1)
            client.request("modern-after-legacy")
            self.assert_list(client, "modern-after-legacy")

    def test_discovery_then_legacy_allowed_but_operational_then_init_rejected(self):
        with mock_modern() as (client, app):
            selected, reply = client.discover_and_select(preferred=LEGACY)
            self.assertEqual(selected, LEGACY)
            self.assert_discovery(reply)
            client.wire.request("legacy-list", "tools/list")
            result = self.assert_reply(client, "legacy-list")["result"]
            self.assertNotIn("resultType", result)
            self.assertNotIn("ttlMs", result)
            self.assertEqual({tool["name"] for tool in result["tools"]}, base.TOOLS)
            self.assert_no_app_read(app)
        with mock_modern() as (client, app):
            client.request("open")
            self.assert_list(client, "open")
            client.wire.request("late-init", "initialize", {
                "protocolVersion": LEGACY, "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"}})
            self.assert_error(client, "late-init", -32600)
            client.request("modern-ping", "ping")
            self.assert_error(client, "modern-ping", -32601)
            client.request("still-modern")
            self.assert_list(client, "still-modern")
            self.assert_no_app_read(app)

    def test_invalid_notifications_and_client_responses_do_not_affect_read(self):
        with mock_modern() as (client, app):
            client.call("active")
            request = base.bridge_request(app)
            for notification in (
                {"method": "notifications/cancelled", "params": {"requestId": "active", "reason": 3}},
                {"method": "notifications/cancelled", "params": {"requestId": None}},
                {"method": "notifications/initialized"},
                {"method": "notifications/not-implemented", "params": {}},
            ):
                client.send({"jsonrpc": "2.0", **notification})
            client.request("notification-barrier")
            self.assert_list(client, "notification-barrier")
            self.assert_no_app_read(app)
            for i, payload in enumerate(({"result": {"execute": "Delete"}},
                                          {"error": {"code": "bad"}},
                                          {"result": {}, "error": {"code": -32603}})):
                client.send({"jsonrpc": "2.0", "id": "active", **payload})
                barrier = f"response-barrier-{i}"
                client.request(barrier)
                reply = client.receive()
                if reply.get("id") != barrier:
                    # A malformed client response may be ignored or explicitly
                    # refused, but it cannot complete the outstanding tool call.
                    self.assertIn(reply.get("error", {}).get("code"), (-32600, -32602), reply)
                    self.assertNotIn("result", reply)
                    reply = client.receive()
                self.assertEqual(reply.get("id"), barrier, reply)
                self.assertIn("result", reply)
                self.assert_no_app_read(app)
            base.bridge_reply(app, request, {"original_read": True})
            self.assertEqual(self.assert_reply(client, "active")["result"]["structuredContent"],
                             {"original_read": True})
            self.assert_quiet(client)

    def test_malformed_duplicate_json_and_null_id_omit_unknown_id(self):
        with mock_modern() as (client, app):
            client.request("open")
            self.assert_list(client, "open")
            frames = (
                (b"{not-json}\n", -32700),
                (b'{"jsonrpc":"2.0","id":1,"id":2,"method":"tools/list"}\n', -32700),
                (b'{"jsonrpc":"2.0","id":3,"method":"tools/call","params":'
                 b'{"name":"narwhal.document_summary","arguments":{"x":1,"x":2}}}\n', -32700),
                (b'{"jsonrpc":"2.0","id":null,"method":"tools/list"}\n', -32600),
                (b'[{"jsonrpc":"2.0","id":4,"method":"tools/list"}]\n', -32600),
            )
            for frame, code in frames:
                client.raw(frame)
                reply = client.receive()
                self.assertEqual(reply.get("error", {}).get("code"), code, reply)
                self.assertNotIn("id", reply, "modern errors omit an unknown/unreadable request ID")
                self.assertNotIn("result", reply)
            client.request("recovered")
            self.assert_list(client, "recovered")
            self.assert_no_app_read(app)

    def test_oversized_frame_and_eof_close_bounded_transport(self):
        with mock_modern() as (client, app):
            client.request("open")
            self.assert_list(client, "open")
            try:
                client.raw(b"x" * 65536 + b"\n")
            except BrokenPipeError:
                # The reader may reject and close before accepting the final LF.
                pass
            reply = client.receive()
            self.assertEqual(reply["error"]["code"], -32700)
            self.assertNotIn("id", reply)
            client.wire.process.wait(timeout=3)
            self.assert_bridge_closed(app)
        with mock_modern() as (client, app):
            client.call("pending-eof")
            base.bridge_request(app)
            client.wire.process.stdin.close()
            client.wire.process.wait(timeout=3)
            self.assert_bridge_closed(app)

    def test_stdout_backpressure_is_terminal_and_protocol_bounded(self):
        import fcntl
        with mock_modern() as (client, app):
            client.request("open")
            self.assert_list(client, "open")
            fcntl.fcntl(client.wire.process.stdout.fileno(), fcntl.F_SETPIPE_SZ, 4096)
            for rid in range(32):
                client.request(rid)
            client.wire.process.wait(timeout=7)
            self.assert_bridge_closed(app)
            output = client.wire.process.stdout.read()
            self.assertLessEqual(len(output), 4096)
            self.assertTrue(output, "test did not create stdout pressure")
            # A terminal partial write may leave one incomplete suffix. Every
            # complete frame before it must still be bounded protocol, not logs.
            for line in output.split(b"\n")[:-1]:
                reply = json.loads(line)
                self.assertEqual(reply["jsonrpc"], "2.0")
                self.assertNotEqual("result" in reply, "error" in reply)
            self.assertNotIn(base.SESSION.encode(), output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True, type=Path, help="Already-built automation server")
    methods = unittest.defaultTestLoader.getTestCaseNames(ModernTests)
    parser.add_argument("--test", choices=methods, help="One ModernTests method for an isolated mutation")
    args = parser.parse_args()
    base.SERVER = args.server.resolve()
    if not base.SERVER.is_file() or not os.access(base.SERVER, os.X_OK):
        parser.error("--server must name an existing executable; this script never builds it")
    suite = (unittest.TestSuite([ModernTests(args.test)]) if args.test else
             unittest.defaultTestLoader.loadTestsFromTestCase(ModernTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
