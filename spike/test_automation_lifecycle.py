#!/usr/bin/env python3
"""Combined output-pressure/error/cancellation regressions; no GUI or provider."""
import argparse
import array
from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
import select
import socket
import sys
import termios
import time
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_automation_mcp as base


@contextmanager
def blocked(modern):
    app, child = socket.socketpair()
    app.settimeout(3)
    client = base.Client(base.SERVER, child, modern=modern)
    child.close()
    try:
        if not modern:
            client.initialize()
        fcntl.fcntl(client.process.stdout.fileno(), fcntl.F_SETPIPE_SZ, 4096)
        client.call("fill")
        request = base.bridge_request(app)
        base.bridge_reply(app, request, {"fixture_padding": "x" * 16000})
        deadline = time.monotonic() + 2
        while True:
            unread = array.array("i", [0])
            fcntl.ioctl(client.process.stdout.fileno(), termios.FIONREAD, unread, True)
            if unread[0] == 4096:
                break
            if time.monotonic() >= deadline:
                raise AssertionError("fixture did not fill the stdout pipe")
            time.sleep(.005)
        time.sleep(.03)
        client.call("pending")
        request = base.bridge_request(app)
        assert request["seq"] == 2
        yield client, app
    finally:
        app.close()
        client.close()


class LifecycleTests(unittest.TestCase):
    def test_error_under_pressure_does_not_delay_cancellation_or_lose_error(self):
        for modern in (False, True):
            with self.subTest(modern=modern), blocked(modern) as (client, app):
                client.raw(b"{malformed-json}\n")
                client.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                             "params": {"requestId": "pending"}})
                self.assertTrue(select.select([app], [], [], 1)[0],
                                "reader waited for stdout before cancellation")
                self.assertEqual(app.recv(1), b"")
                # Resume output, proving the accepted parse error was queued,
                # not silently discarded to make cancellation responsive.
                seen = []
                for _ in range(2):
                    reply = client.receive()
                    seen.append((reply.get("id"), reply.get("error", {}).get("code")))
                self.assertIn(("fill", None), seen)
                self.assertIn((None, -32700), seen)
                self.assertFalse(client.buffer, "cancelled response leaked")

    def test_rejection_under_pressure_does_not_delay_eof(self):
        for modern in (False, True):
            with self.subTest(modern=modern), blocked(modern) as (client, app):
                client.request("rejected", "not-a-method")
                client.process.stdin.close()
                client.process.wait(timeout=1)
                self.assertTrue(select.select([app], [], [], .2)[0])
                self.assertEqual(app.recv(1), b"")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True, type=Path)
    parser.add_argument("--test", choices=unittest.defaultTestLoader.getTestCaseNames(LifecycleTests))
    args = parser.parse_args()
    base.SERVER = args.server.resolve()
    suite = unittest.TestSuite([LifecycleTests(args.test)]) if args.test else unittest.defaultTestLoader.loadTestsFromTestCase(LifecycleTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
