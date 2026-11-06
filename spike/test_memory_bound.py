#!/usr/bin/env python3
"""Tests for scripts/memory_bound.py: memory-bounding detection and prefix selection.

Exercises:
1. Parsing of memory limits (bytes, suffixes M/G/K, case insensitivity, validation);
2. Selection order: systemd-run -> prlimit -> ulimit -> refusal to run unbounded;
3. Output tokens for each bounding mechanism;
4. Announcement line reporting which bound is in effect;
5. CLI probe and error handling.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import memory_bound


class ParseMemoryBytesTests(unittest.TestCase):
    def test_parses_megabytes_and_gigabytes_exact(self):
        self.assertEqual(memory_bound.parse_memory_bytes("1024M"), 1024 * 1024 * 1024)
        self.assertEqual(memory_bound.parse_memory_bytes("1G"), 1024 * 1024 * 1024)
        self.assertEqual(memory_bound.parse_memory_bytes("2G"), 2 * 1024 * 1024 * 1024)
        self.assertEqual(memory_bound.parse_memory_bytes("2048M"), 2048 * 1024 * 1024)
        self.assertEqual(memory_bound.parse_memory_bytes("1536M"), 1536 * 1024 * 1024)

    def test_parses_kilobytes_and_bytes(self):
        self.assertEqual(memory_bound.parse_memory_bytes("512K"), 512 * 1024)
        self.assertEqual(memory_bound.parse_memory_bytes("1048576"), 1048576)
        self.assertEqual(memory_bound.parse_memory_bytes(1048576), 1048576)

    def test_case_insensitive_and_whitespace(self):
        self.assertEqual(memory_bound.parse_memory_bytes("  1g  "), 1024 * 1024 * 1024)
        self.assertEqual(memory_bound.parse_memory_bytes("1024mb"), 1024 * 1024 * 1024)
        self.assertEqual(memory_bound.parse_memory_bytes("1gib"), 1024 * 1024 * 1024)

    def test_refuses_invalid_specifications(self):
        for bad in ("0", "0M", "-10M", "abc", "1024XYZ", ""):
            with self.assertRaises(ValueError):
                memory_bound.parse_memory_bytes(bad)


class BoundingSelectionTests(unittest.TestCase):
    def setUp(self):
        self.saved_env = os.environ.pop("NARWHAL_MEMORY_BOUND_METHOD", None)

    def tearDown(self):
        if self.saved_env is not None:
            os.environ["NARWHAL_MEMORY_BOUND_METHOD"] = self.saved_env
        else:
            os.environ.pop("NARWHAL_MEMORY_BOUND_METHOD", None)

    def test_prefers_systemd_run_when_available(self):
        method, desc = memory_bound.detect_bounding_method(
            force_refresh=True,
            systemd_prober=lambda: True,
            prlimit_prober=lambda: True,
            ulimit_prober=lambda: True,
        )
        self.assertEqual(method, "systemd-run")
        self.assertIn("systemd", desc)

    def test_falls_back_to_prlimit_when_systemd_fails(self):
        method, desc = memory_bound.detect_bounding_method(
            force_refresh=True,
            systemd_prober=lambda: False,
            prlimit_prober=lambda: True,
            ulimit_prober=lambda: True,
        )
        self.assertEqual(method, "prlimit")
        self.assertIn("prlimit", desc)

    def test_falls_back_to_ulimit_when_prlimit_fails(self):
        method, desc = memory_bound.detect_bounding_method(
            force_refresh=True,
            systemd_prober=lambda: False,
            prlimit_prober=lambda: False,
            ulimit_prober=lambda: True,
        )
        self.assertEqual(method, "ulimit")
        self.assertIn("ulimit", desc)

    def test_refuses_to_run_unbounded_when_all_fail(self):
        with self.assertRaises(RuntimeError) as ctx:
            memory_bound.detect_bounding_method(
                force_refresh=True,
                systemd_prober=lambda: False,
                prlimit_prober=lambda: False,
                ulimit_prober=lambda: False,
            )
        self.assertIn("No memory bounding mechanism available", str(ctx.exception))
        self.assertIn("Refusing to run unbounded silently", str(ctx.exception))

    def test_environment_override_forces_method(self):
        os.environ["NARWHAL_MEMORY_BOUND_METHOD"] = "prlimit"
        method, _ = memory_bound.detect_bounding_method(
            force_refresh=True,
            systemd_prober=lambda: True,
        )
        self.assertEqual(method, "prlimit")

    def test_environment_override_none_refuses_unbounded(self):
        os.environ["NARWHAL_MEMORY_BOUND_METHOD"] = "none"
        with self.assertRaises(RuntimeError) as ctx:
            memory_bound.detect_bounding_method(force_refresh=True)
        self.assertIn("Refusing to run unbounded silently", str(ctx.exception))


class BoundedPrefixTests(unittest.TestCase):
    def setUp(self):
        self.saved_env = os.environ.pop("NARWHAL_MEMORY_BOUND_METHOD", None)

    def tearDown(self):
        if self.saved_env is not None:
            os.environ["NARWHAL_MEMORY_BOUND_METHOD"] = self.saved_env
        else:
            os.environ.pop("NARWHAL_MEMORY_BOUND_METHOD", None)

    def test_systemd_run_tokens_and_announcement(self):
        messages = []
        tokens = memory_bound.bounded_prefix(
            "1024M",
            quiet=True,
            swap_max=0,
            announce=True,
            print_fn=messages.append,
            force_refresh=True,
            systemd_prober=lambda: True,
        )
        self.assertEqual(
            tokens,
            ["systemd-run", "--user", "--scope", "--quiet", "-p", "MemoryMax=1024M", "-p", "MemorySwapMax=0"],
        )
        self.assertEqual(len(messages), 1)
        self.assertIn("memory bound: systemd-run (MemoryMax=1024M, MemorySwapMax=0)", messages[0])

    def test_prlimit_tokens_and_announcement(self):
        messages = []
        tokens = memory_bound.bounded_prefix(
            "1G",
            announce=True,
            print_fn=messages.append,
            force_refresh=True,
            systemd_prober=lambda: False,
            prlimit_prober=lambda: True,
        )
        self.assertEqual(tokens, ["prlimit", "--as=1073741824", "--"])
        self.assertEqual(len(messages), 1)
        self.assertIn("memory bound: prlimit (--as=1073741824)", messages[0])

    def test_ulimit_tokens_and_announcement(self):
        messages = []
        tokens = memory_bound.bounded_prefix(
            "2048M",
            announce=True,
            print_fn=messages.append,
            force_refresh=True,
            systemd_prober=lambda: False,
            prlimit_prober=lambda: False,
            ulimit_prober=lambda: True,
        )
        self.assertEqual(tokens, ["sh", "-c", 'ulimit -v 2097152 && exec "$@"', "_"])
        self.assertEqual(len(messages), 1)
        self.assertIn("memory bound: ulimit (-v 2097152)", messages[0])


class CliTests(unittest.TestCase):
    def test_cli_probe_success(self):
        os.environ["NARWHAL_MEMORY_BOUND_METHOD"] = "prlimit"
        try:
            rc = memory_bound.main(["--probe"])
            self.assertEqual(rc, 0)
        finally:
            os.environ.pop("NARWHAL_MEMORY_BOUND_METHOD", None)

    def test_cli_probe_failure_returns_code_2(self):
        os.environ["NARWHAL_MEMORY_BOUND_METHOD"] = "none"
        try:
            rc = memory_bound.main(["--probe"])
            self.assertEqual(rc, 2)
        finally:
            os.environ.pop("NARWHAL_MEMORY_BOUND_METHOD", None)

    def test_cli_without_command_returns_code_2(self):
        rc = memory_bound.main([])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
