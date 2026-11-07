#!/usr/bin/env python3
"""spike/memory_bound.py: which memory bound a gate child gets, and that it really applies.

Run: python3 spike/test_memory_bound.py   (no build, no display; works with or without
a systemd user session)

Covers the selection logic (forced modes, a working user scope, no systemd-run, a
systemd-run whose user scope fails, and neither bound available, which must refuse
rather than run unbounded), the exact argv of both modes, the one-line notice, and real
children: under the fallback a child sees RLIMIT_AS = 4 x MemoryMax and the right
working directory. Also runs scripts/setup_dev_native.sh's preamble in a copied tree with
stubbed tools: without a user session it names the RLIMIT_AS fallback and carries on, with
one it names the scope, and with no bound at all it stops.
"""
import io
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_bound  # noqa: E402

HELPER = str(Path(memory_bound.__file__).resolve())
PROBE = ("import os, resource; print(resource.getrlimit(resource.RLIMIT_AS)[0], os.getcwd())")


def fresh():
    """Forget the per-process decision and notice, and any forced mode."""
    memory_bound._mode = None
    memory_bound._announced = False


class Selection(unittest.TestCase):
    def setUp(self):
        fresh()
        self.env = patch.dict(os.environ)
        self.env.start()
        os.environ.pop(memory_bound.MODE_ENV, None)
        self.addCleanup(self.env.stop)
        self.addCleanup(fresh)

    def probe(self, returncode):
        return patch.object(memory_bound, "_probe_run",
                            return_value=subprocess.CompletedProcess([], returncode))

    def test_working_user_scope_is_preferred(self):
        with patch.object(memory_bound.shutil, "which", return_value="/usr/bin/systemd-run"), self.probe(0):
            self.assertEqual(memory_bound.mode(), "systemd")

    def test_no_systemd_run_falls_back_to_rlimit(self):
        with patch.object(memory_bound.shutil, "which", return_value=None):
            self.assertEqual(memory_bound.mode(), "rlimit")

    def test_systemd_run_without_a_user_session_falls_back_to_rlimit(self):
        with patch.object(memory_bound.shutil, "which", return_value="/usr/bin/systemd-run"), self.probe(1):
            self.assertEqual(memory_bound.mode(), "rlimit")

    def test_a_hanging_probe_falls_back_to_rlimit(self):
        with patch.object(memory_bound.shutil, "which", return_value="/usr/bin/systemd-run"), \
                patch.object(memory_bound, "_probe_run", side_effect=subprocess.TimeoutExpired("x", 30)):
            self.assertEqual(memory_bound.mode(), "rlimit")

    def test_no_bound_at_all_refuses_instead_of_running_unbounded(self):
        with patch.object(memory_bound.shutil, "which", return_value=None), \
                patch.object(memory_bound, "_rlimit_available", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "no memory bound available"):
                memory_bound.mode()
            with self.assertRaises(RuntimeError):
                memory_bound.prefix("1G")

    def test_forced_modes_and_a_bad_value(self):
        with patch.object(memory_bound.shutil, "which", side_effect=AssertionError("not probed")):
            for forced in memory_bound.MODES:
                os.environ[memory_bound.MODE_ENV] = forced
                self.assertEqual(memory_bound.mode(), forced)
            os.environ[memory_bound.MODE_ENV] = "none"
            with self.assertRaisesRegex(RuntimeError, "must be one of"):
                memory_bound.mode()

    def test_the_decision_is_made_once_per_process(self):
        with patch.object(memory_bound.shutil, "which", return_value=None) as which:
            memory_bound.mode()
            memory_bound.mode()
        self.assertEqual(which.call_count, 1)


class Shapes(unittest.TestCase):
    def setUp(self):
        fresh()
        self.addCleanup(fresh)

    def test_systemd_scope_is_the_established_command(self):
        with patch.dict(os.environ, {memory_bound.MODE_ENV: "systemd"}), patch("sys.stderr", io.StringIO()):
            self.assertEqual(memory_bound.prefix("1024M"), [
                "systemd-run", "--user", "--scope", "--quiet",
                "-p", "MemoryMax=1024M", "-p", "MemorySwapMax=0"])
            self.assertEqual(memory_bound.prefix("2G", properties=("CPUQuota=100%",), quiet=False), [
                "systemd-run", "--user", "--scope",
                "-p", "MemoryMax=2G", "-p", "MemorySwapMax=0", "-p", "CPUQuota=100%"])
            self.assertEqual(memory_bound.prefix("768M", scope=False, properties=("TasksMax=16",)), [
                "systemd-run", "--user", "--quiet", "--wait", "--pipe",
                "-p", "MemoryMax=768M", "-p", "MemorySwapMax=0", "-p", "TasksMax=16"])

    def test_rlimit_prefix_is_the_launcher_and_keeps_only_the_working_directory(self):
        with patch.dict(os.environ, {memory_bound.MODE_ENV: "rlimit"}), patch("sys.stderr", io.StringIO()):
            self.assertEqual(
                memory_bound.prefix("2G", properties=("CPUQuota=100%", "WorkingDirectory=/w")),
                [sys.executable, "-I", HELPER, "--rlimit", "--max", "2G", "--cwd", "/w", "--"])

    def test_one_notice_per_process_naming_the_bound(self):
        for forced, words in (("systemd", ("systemd user scope", "MemoryMax=1G")),
                              ("rlimit", ("no systemd user session", "RLIMIT_AS=4096M", "not resident"))):
            fresh()
            err = io.StringIO()
            with patch.dict(os.environ, {memory_bound.MODE_ENV: forced}), patch("sys.stderr", err):
                memory_bound.prefix("1G")
                memory_bound.prefix("2G")
            lines = err.getvalue().splitlines()
            self.assertEqual(len(lines), 1, lines)
            for word in words:
                self.assertIn(word, lines[0])

    def test_sizes(self):
        self.assertEqual(memory_bound.size_bytes("1024M"), 1024 ** 3)
        self.assertEqual(memory_bound.size_bytes("3G"), 3 * 1024 ** 3)
        self.assertEqual(memory_bound.size_bytes("512k"), 512 * 1024)
        with self.assertRaises(ValueError):
            memory_bound.size_bytes("lots")
        with self.assertRaises(ValueError):
            memory_bound.prefix("1.5G")


class RealChildren(unittest.TestCase):
    """The launcher really applies the bound (no mocks)."""

    def run_child(self, *helper_args, env=None):
        result = subprocess.run([sys.executable, HELPER, *helper_args, "--", sys.executable, "-c", PROBE],
                                capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        limit, cwd = result.stdout.split()
        return int(limit), cwd, result.stderr

    def test_forced_rlimit_child_sees_four_times_memory_max(self):
        env = dict(os.environ, **{memory_bound.MODE_ENV: "rlimit"})
        limit, _cwd, err = self.run_child("--max", "512M", env=env)
        self.assertEqual(limit, 4 * 512 * 1024 ** 2)
        self.assertIn("RLIMIT_AS=2048M", err)

    def test_working_directory_is_honoured(self):
        env = dict(os.environ, **{memory_bound.MODE_ENV: "rlimit"})
        _limit, cwd, _err = self.run_child("--max", "512M", "--cwd", "/", env=env)
        self.assertEqual(cwd, "/")

    def test_the_cap_stops_a_runaway_child(self):
        env = dict(os.environ, **{memory_bound.MODE_ENV: "rlimit"})
        result = subprocess.run([sys.executable, HELPER, "--max", "64M", "--", sys.executable, "-c",
                                 "bytearray(1024 * 1024 * 1024)"],
                                capture_output=True, text=True, timeout=60, env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MemoryError", result.stderr)

    def verify_in_child(self, *prefix_args, env):
        code = ("import sys; sys.path.insert(0, %r); import memory_bound\n"
                "try: print(memory_bound.verify_bound('512M')['bound'])\n"
                "except RuntimeError as e: print('REFUSED', e)" % str(Path(HELPER).parent))
        result = subprocess.run([*prefix_args, sys.executable, "-c", code],
                                capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_a_worker_can_prove_it_is_bounded_under_the_fallback(self):
        env = dict(os.environ, **{memory_bound.MODE_ENV: "rlimit"})
        self.assertEqual(self.verify_in_child(sys.executable, HELPER, "--max", "512M", "--", env=env), "rlimit")

    def test_an_unbounded_worker_is_refused(self):
        env = {k: v for k, v in os.environ.items() if k != memory_bound.MODE_ENV}
        self.assertTrue(self.verify_in_child(env=env).startswith("REFUSED"))
        # A looser address-space limit than the bound allows is not the bound either.
        loose = str(8 * 512 * 1024 ** 2)
        self.assertTrue(self.verify_in_child("prlimit", f"--as={loose}", "--", env=env).startswith("REFUSED")
                        if shutil.which("prlimit") else True)

    @unittest.skipUnless(memory_bound._user_scope_works(), "no systemd user session here")
    def test_a_worker_can_prove_it_is_bounded_under_a_user_scope(self):
        env = dict(os.environ, **{memory_bound.MODE_ENV: "systemd"})
        self.assertEqual(self.verify_in_child(sys.executable, HELPER, "--max", "512M", "--", env=env), "systemd")

    def test_without_a_user_session_auto_mode_uses_rlimit(self):
        # What a container looks like to systemd-run: no session bus, no runtime dir.
        env = {k: v for k, v in os.environ.items()
               if k not in (memory_bound.MODE_ENV, "DBUS_SESSION_BUS_ADDRESS")}
        env["XDG_RUNTIME_DIR"] = "/nonexistent-narwhal-runtime"
        limit, _cwd, err = self.run_child("--max", "512M", env=env)
        self.assertEqual(limit, 4 * 512 * 1024 ** 2)
        self.assertIn("no systemd user session", err)


# Runs scripts/setup_dev_native.sh's real preamble in a copied tree. The runtime Python is a
# shim that runs memory_bound.py for real and fails the CPython version check that follows,
# so the script stops right after the memory-bound step and never builds or downloads.
RUNTIME_SHIM = f"""#!/bin/sh
[ "$1" = -c ] && exit 1
exec "{sys.executable}" "$@"
"""
# systemd-run as a container has it (STUB_SYSTEMD=fail: no user session) or as a desktop
# has it (runs the command after its options).
SYSTEMD_RUN_STUB = """#!/bin/sh
[ "$STUB_SYSTEMD" = fail ] && { echo "Failed to create bus connection" >&2; exit 1; }
while [ $# -gt 0 ]; do case $1 in -p) shift 2;; -*) shift;; *) break;; esac; done
exec "$@"
"""


class SetupPreamble(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name in ("scripts/setup_dev_native.sh", "spike/memory_bound.py"):
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / name, self.root / name)
        python = self.root / "tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13"
        stubs = self.root / "bin"
        python.parent.mkdir(parents=True)
        stubs.mkdir()
        scripts = {python: RUNTIME_SHIM, stubs / "systemd-run": SYSTEMD_RUN_STUB}
        for tool in ("python3", "git", "curl", "cargo", "cmake", "make", "cc", "c++"):
            scripts[stubs / tool] = "#!/bin/sh\nexit 0\n"
        for path, text in scripts.items():
            path.write_text(text)
            path.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items() if k != memory_bound.MODE_ENV}
        self.env.update(PATH=f"{stubs}:{os.environ['PATH']}", XDG_CACHE_HOME=str(self.root / "cache"))

    def setup(self, **env):
        return subprocess.run([str(self.root / "scripts/setup_dev_native.sh")], env=dict(self.env, **env),
                              capture_output=True, text=True, timeout=60)

    def test_without_a_user_session_setup_continues_under_rlimit(self):
        done = self.setup(STUB_SYSTEMD="fail")
        self.assertIn("memory bound: no systemd user session; RLIMIT_AS=4096M", done.stderr)
        self.assertIn("not CPython 3.13.13", done.stderr, "setup went past the memory-bound step")
        self.assertNotIn("does not work here", done.stderr)

    def test_with_a_user_session_setup_names_the_scope(self):
        done = self.setup()
        self.assertIn("memory bound: systemd user scope, MemoryMax=1024M", done.stderr)
        self.assertIn("not CPython 3.13.13", done.stderr, "setup went past the memory-bound step")

    def test_setup_stops_when_no_bound_can_be_chosen(self):
        done = self.setup(**{memory_bound.MODE_ENV: "none"})
        self.assertEqual(done.returncode, 2)
        self.assertIn("no memory bound is available for the native gates", done.stderr)
        self.assertNotIn("not CPython 3.13.13", done.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
