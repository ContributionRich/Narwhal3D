"""Pure lifecycle tests: mocked children/timers + real Unix socketpairs/pipes.

No Blender window, builds, network, provider credentials or user documents.
Run: python3 -B spike/test_automation_host.py
Mutations load only these owned source variants in memory, never edit the tree.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
NONCE = "ab" * 16
VERSION = "0.4.6-preview.1"


def load(path, name, replacement=None):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    source = path.read_text()
    if replacement:
        old, new = replacement
        assert source.count(old) == 1, old
        source = source.replace(old, new)
    # In-memory mutation loading belongs only to tests, never the target/CLI.
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


class Timers:
    def __init__(self):
        self.callbacks = set()

    def is_registered(self, callback):
        return callback in self.callbacks

    def register(self, callback, **_kwargs):
        self.callbacks.add(callback)

    def unregister(self, callback):
        self.callbacks.remove(callback)


class HostTests(unittest.TestCase):
    mutation = None

    def setUp(self):
        self.package = "_host_test_" + uuid.uuid4().hex
        package = types.ModuleType(self.package)
        package.__path__ = [str(ROOT / "narwhal3d")]
        sys.modules[self.package] = package
        self.host = load(ROOT / "narwhal3d/automation_host.py", self.package + ".automation_host", self.mutation)
        self.app = types.SimpleNamespace(model=types.SimpleNamespace(file=object()))
        self.state = types.ModuleType(self.package + ".state")
        self.state.app_state = self.app
        sys.modules[self.state.__name__] = self.state
        package.state = self.state
        self.timers = Timers()
        self.bpy = types.SimpleNamespace(app=types.SimpleNamespace(timers=self.timers, version_string="5.2.2"))
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(patch.dict(sys.modules, {"bpy": self.bpy}))
        self.stack.enter_context(patch.dict(os.environ, {}, clear=True))
        self.endpoint, self.peer = socket.socketpair()
        self.peer.settimeout(0.2)
        self.life_host, self.life_cli = socket.socketpair()
        self.life_cli.settimeout(0.2)
        self.addCleanup(self.clean)

    def clean(self):
        self.host.close()
        self.endpoint.close()
        self.peer.close()
        self.life_host.close()
        self.life_cli.close()
        self.stack.close()
        for name in list(sys.modules):
            if name == self.package or name.startswith(self.package + "."):
                del sys.modules[name]

    def attach(self, **changes):
        env = {
            "NARWHAL_AUTOMATION_OPT_IN": self.host.OPT_IN,
            "NARWHAL_AUTOMATION_FD": str(self.endpoint.detach()),
            "NARWHAL_AUTOMATION_LIFETIME_FD": str(self.life_host.detach()),
            "NARWHAL_AUTOMATION_SESSION": NONCE,
            "NARWHAL_AUTOMATION_ROOT": str(ROOT),
            "NARWHAL3D_VERSION": VERSION, "NARWHAL3D_UI_READY": "1",
        }
        env.update(changes)
        os.environ.update(env)
        return self.host.attach_inherited(self.app, VERSION)

    def ready(self):
        host = self.attach()
        self.assertIn(host.pump, self.timers.callbacks)
        host.pump()
        self.assertEqual(self.peer.recv(256), self.host.READY_PREFIX + NONCE.encode() + b"\n")
        return host

    def test_worker_cannot_attach_pump_or_close(self):
        host = self.ready()
        failures = []

        def worker():
            for action in (lambda: self.host.attach_inherited(self.app, VERSION),
                           host.pump, self.host.close):
                try:
                    action()
                except RuntimeError as exc:
                    failures.append(str(exc))
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, ["main_thread_required"] * 3)
        self.assertFalse(host.closed)

    def test_timer_teardown_failure_still_closes_socket(self):
        host = self.ready()
        with patch.object(self.host, "unregister_timer", side_effect=RuntimeError("teardown")):
            self.host.close()
        self.assertTrue(host.closed)
        self.assertIsNone(host.pump())
        self.assertEqual(self.peer.recv(1), b"")

    def test_datagram_socket_refused(self):
        self.endpoint.close()
        self.peer.close()
        self.endpoint, self.peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        with self.assertRaisesRegex(ValueError, "invalid_automation_launch"):
            self.attach()
        self.assertFalse(self.timers.callbacks)

    def test_no_optin_no_timer_or_socket(self):
        with patch.object(self.host.socket, "socket", side_effect=AssertionError("no socket")):
            self.assertIsNone(self.host.attach_inherited(self.app, VERSION))
        self.assertFalse(self.timers.callbacks)

    def test_real_handoff_application_info_and_unregister(self):
        host = self.ready()
        self.assertNotIn("NARWHAL_AUTOMATION_SESSION", os.environ)
        self.assertFalse(os.get_inheritable(host._endpoint.fileno()))
        self.peer.sendall(json.dumps({"session": NONCE, "seq": 1,
                                     "tool": "narwhal.application_info", "arguments": {}}).encode() + b"\n")
        host.pump()
        reply = json.loads(self.peer.recv(4096))
        self.assertEqual(reply["result"]["application_version"], VERSION)
        self.assertEqual(reply["result"]["permissions"], {"read_only": True, "mutations": False})
        self.host.close()
        self.host.close()
        self.assertTrue(host.closed)
        self.assertFalse(self.timers.callbacks)
        self.assertEqual(self.peer.recv(1), b"")
        self.assertIsNone(self.host.attach_inherited(self.app, VERSION))

    def test_waits_for_normal_ui_readiness(self):
        host = self.attach(NARWHAL3D_UI_READY="0")
        self.assertIsNotNone(host.pump())
        self.peer.setblocking(False)
        with self.assertRaises(BlockingIOError):
            self.peer.recv(256)
        os.environ["NARWHAL3D_UI_READY"] = "1"
        host.pump()
        self.assertTrue(self.peer.recv(256).startswith(self.host.READY_PREFIX))

    def test_boot_deadline(self):
        host = self.attach(NARWHAL3D_UI_READY="0")
        with patch.object(self.host.time, "monotonic", return_value=host._deadline + 1):
            self.assertIsNone(host.pump())
        self.assertTrue(host.closed)

    def test_model_switch_refuses_queued_read_and_cannot_resume(self):
        host = self.ready()
        old = self.app.model
        self.peer.sendall(json.dumps({"session": NONCE, "seq": 1,
                                     "tool": "narwhal.application_info", "arguments": {}}).encode() + b"\n")
        self.app.model = types.SimpleNamespace(file=object())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(host.pump())
        self.assertTrue(host.closed)
        self.assertEqual(self.life_cli.recv(1), b"")
        self.assertIsNot(self.app.model, old)  # Revocation does not close/change the new document.
        self.app.model = old
        self.assertIsNone(host.pump())

    def test_file_identity_switch_revokes(self):
        host = self.ready()
        self.app.model.file = object()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(host.pump())
        self.assertTrue(host.closed)

    def test_state_singleton_switch_revokes(self):
        host = self.ready()
        self.state.app_state = types.SimpleNamespace(model=self.app.model)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(host.pump())
        self.assertTrue(host.closed)

    def test_eof_and_mutation_tool_close(self):
        host = self.ready()
        self.peer.sendall(json.dumps({"session": NONCE, "seq": 1,
                                     "tool": "narwhal.execute", "arguments": {}}).encode() + b"\n")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(host.pump())
        self.assertTrue(host.closed)

    def test_eof_before_ready(self):
        host = self.attach(NARWHAL3D_UI_READY="0")
        self.peer.close()
        self.assertIsNone(host.pump())
        self.assertTrue(host.closed)

    def test_bad_origin_closes_capability(self):
        with self.assertRaisesRegex(ValueError, "^invalid_automation_launch$"):
            self.attach(NARWHAL_AUTOMATION_ROOT="/not-the-source-root")
        self.assertEqual(self.peer.recv(1), b"")
        self.assertFalse(self.timers.callbacks)

    def test_not_the_real_singleton_is_refused(self):
        self.state.app_state = object()
        with self.assertRaisesRegex(ValueError, "invalid_automation_launch"):
            self.attach()
        self.assertEqual(self.peer.recv(1), b"")

    def test_wrong_version_or_nonce_refused(self):
        with self.assertRaisesRegex(ValueError, "invalid_automation_launch"):
            self.attach(NARWHAL3D_VERSION="0.1.0")
        self.assertEqual(self.peer.recv(1), b"")

    def test_nonce_refused(self):
        with self.assertRaisesRegex(ValueError, "invalid_automation_launch"):
            self.attach(NARWHAL_AUTOMATION_SESSION="private-payload")
        self.assertEqual(self.peer.recv(1), b"")


class Child:
    def __init__(self):
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("owned", timeout)
        return self.returncode


class CliTests(unittest.TestCase):
    mutation = None

    def setUp(self):
        self.name = "_cli_test_" + uuid.uuid4().hex
        self.cli = load(ROOT / "scripts/narwhal_mcp.py", self.name, self.mutation)
        self.addCleanup(lambda: sys.modules.pop(self.name, None))
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.runtime = self.cli.Runtime(self.root, self.root / "blender", self.root / "server",
                                       self.root / "launch/bootstrap.py", "linux-x86_64", VERSION)

    def test_fixed_resolver_and_manifest(self):
        for name in ("runtime/linux-x86_64/bin/narwhal-automation", "tools/blender-patched/blender", "launch/bootstrap.py"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
            path.chmod(0o700)
        (self.root / "packaging").mkdir()
        (self.root / "packaging/release-manifest.json").write_text(json.dumps({"identity": {"version": VERSION}}))
        with patch.object(self.cli, "runtime_key", return_value="linux-x86_64"):
            runtime = self.cli.resolve_runtime(self.root, {"PATH": "/untrusted", "NARWHAL_AUTOMATION_SERVER": "/evil"})
        self.assertEqual(runtime.server, self.root / "runtime/linux-x86_64/bin/narwhal-automation")
        self.assertEqual(runtime.version, VERSION)
        self.assertEqual(self.cli.product_version(self.root, {"NARWHAL3D_VERSION": "1.2.3"}), "1.2.3")

    def test_source_version_not_legacy_and_invalid_version_refused(self):
        self.assertEqual(self.cli.product_version(self.root, {}), "source-unversioned")
        with self.assertRaises(self.cli.LaunchError):
            self.cli.product_version(self.root, {"NARWHAL3D_VERSION": "private\npayload"})
        with self.assertRaises(self.cli.LaunchError):
            self.cli.resolve_runtime(self.root, {})

    def test_environment_no_secrets_or_injection(self):
        env = self.cli.application_environment(self.runtime, self.root, 7, NONCE, {
            "DISPLAY": ":7", "HOME": "/home/example", "OPENAI_API_KEY": "secret",
            "PYTHONPATH": "/evil", "LD_PRELOAD": "/evil", "PATH": "/evil",
            "NARWHAL3D_WELCOME_REQUEST": "/evil", "NARWHAL_GEOMETRY_CORE": "/evil"})
        for key in ("OPENAI_API_KEY", "PYTHONPATH", "LD_PRELOAD", "NARWHAL3D_WELCOME_REQUEST", "NARWHAL_GEOMETRY_CORE"):
            self.assertNotIn(key, env)
        self.assertEqual(env["DISPLAY"], ":7")
        self.assertEqual(env["NARWHAL3D_VERSION"], VERSION)
        self.assertEqual(env["NARWHAL_AUTOMATION_ROOT"], str(self.root))
        self.assertEqual(env["NARWHAL3D_SETTINGS_FILE"], str(self.root / "preferences.json"))

    def test_private_data_environment_is_stable(self):
        private = self.root / "private"
        expected = {"NARWHAL3D_DATA_DIR": str(private / "data/narwhal3d"),
                    "XDG_DATA_HOME": str(private / "data"),
                    "XDG_CACHE_HOME": str(private / "cache")}
        for inherited in ({}, {key: "/existing-user-data" for key in expected}):
            inherited["HOME"] = "/existing-user-home"
            env = self.cli.application_environment(self.runtime, private, 7, NONCE, inherited)
            self.assertEqual({key: env.get(key) for key in expected}, expected)
            self.assertEqual(env["HOME"], "/existing-user-home")
            self.assertNotIn("/existing-user-data", env.values())

    def test_resolver_never_reads_existing_user_data(self):
        app_root = self.root / "install"
        for name in ("runtime/linux-x86_64/bin/narwhal-automation",
                     "tools/blender-patched/blender", "launch/bootstrap.py"):
            path = app_root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
            path.chmod(0o700)
        manifest = app_root / "packaging/release-manifest.json"
        manifest.parent.mkdir()
        manifest.write_text(json.dumps({"identity": {"version": VERSION}}))
        user_home = self.root / "existing-user"
        data = user_home / ".local/share/narwhal3d"
        (data / "autosave").mkdir(parents=True)
        (data / "recent.json").write_text('["private-existing-document.narwhal"]')
        (data / "autosave/private.narwhal").write_bytes(b"private recovery canary")
        inherited = {"HOME": str(user_home), "NARWHAL3D_DATA_DIR": str(data),
                     "XDG_DATA_HOME": str(data.parent), "XDG_CACHE_HOME": str(user_home / ".cache")}
        opened = []
        real_open = io.open

        def only_manifest(path, *args, **kwargs):
            self.assertEqual(Path(path), manifest, "resolver must only read the package manifest")
            opened.append(Path(path))
            return real_open(path, *args, **kwargs)

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(self.cli, "runtime_key", return_value="linux-x86_64"))
            stack.enter_context(patch.dict(os.environ, inherited, clear=True))
            stack.enter_context(patch("builtins.open", side_effect=only_manifest))
            stack.enter_context(patch.object(io, "open", side_effect=only_manifest))
            stack.enter_context(patch.object(os, "open", side_effect=AssertionError("no raw file reads")))
            stack.enter_context(patch.object(os, "scandir", side_effect=AssertionError("no data enumeration")))
            runtime = self.cli.resolve_runtime(app_root)
        self.assertEqual(runtime.version, VERSION)
        self.assertEqual(opened, [manifest])

    def test_filtered_logs_never_echo_payloads(self):
        output = io.StringIO()
        log = self.cli.LogFilter(output)
        safe = "narwhal automation: initialization_failed"
        log.feed(b"API_KEY=private\n" + b"x" * 10000 + b"\n")
        log.feed((safe + " with private payload\n" + safe[:10]).encode())
        log.feed((safe[10:] + "\n" + safe + "\n").encode())
        self.assertEqual(output.getvalue(), safe + "\n")
        self.assertLessEqual(len(log.pending), 256)

    def test_windows_refuses_before_socket(self):
        with patch.object(self.cli.sys, "platform", "win32"), patch.object(self.cli.socket, "socketpair") as pair:
            with self.assertRaisesRegex(self.cli.LaunchError, "Windows"):
                self.cli.supervise(self.runtime, self.root, None, None, io.StringIO())
            pair.assert_not_called()

    def test_consent_required(self):
        with contextlib.redirect_stderr(io.StringIO()), patch.object(self.cli, "resolve_runtime") as resolve:
            with self.assertRaises(SystemExit):
                self.cli.main([])
            resolve.assert_not_called()
        self.assertIn("external model/provider", self.cli.CONSENT)
        self.assertIn("not the visible app", self.cli.CONSENT)
        self.assertNotIn("losing unsaved work", self.cli.CONSENT)

    def test_reap_owned_kill_fallback(self):
        child = Child()
        child.terminate = Mock()  # A child which ignores SIGTERM.
        other = Child()
        self.cli.reap_owned([child])
        self.assertTrue(child.killed)
        self.assertFalse(other.terminated)
        self.assertFalse(other.killed)

    def test_unreapable_child_does_not_skip_other_child(self):
        stuck, other = Child(), Child()
        stuck.wait = Mock(side_effect=subprocess.TimeoutExpired("owned", 0))
        other.wait = Mock(return_value=-15)
        with self.assertRaisesRegex(self.cli.LaunchError, "bounded kill"):
            self.cli.reap_owned([stuck, other])
        self.assertTrue(stuck.killed)
        self.assertTrue(other.terminated)
        self.assertEqual(other.wait.call_count, 2)

    def test_startup_queue_is_bounded(self):
        with patch.object(self.cli, "MAX_PENDING", 8):
            self.supervise_case("overflow")

    def supervise_case(self, mode):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        config = self.cli.PrivateConfig()
        self.addCleanup(lambda: self.cli.shutil.rmtree(config.path, ignore_errors=True))
        read_fd, write_fd = os.pipe()
        source = stack.enter_context(os.fdopen(read_fd, "rb", buffering=0))
        writer = stack.enter_context(os.fdopen(write_fd, "wb", buffering=0))
        output = stack.enter_context(tempfile.TemporaryFile())
        children = []
        received = bytearray()
        request = b'{"jsonrpc":"2.0","id":1,"method":"initialize"}\n'
        if mode == "eof":
            writer.close()
        else:
            writer.write(request)

        def popen(args, **kwargs):
            child = Child()
            child.args, child.kwargs = args, kwargs
            children.append(child)
            fd = kwargs["pass_fds"][0]
            endpoint = socket.socket(fileno=os.dup(fd))
            stack.callback(endpoint.close)
            log_read, log_write = os.pipe()
            log = stack.enter_context(os.fdopen(log_read, "rb", buffering=0))
            stack.callback(os.close, log_write)
            if len(children) == 1:
                self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
                self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
                self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
                self.assertTrue(kwargs["start_new_session"])
                self.assertEqual(kwargs["env"]["NARWHAL3D_DATA_DIR"], str(config.path / "data/narwhal3d"))
                self.assertEqual(kwargs["env"]["XDG_DATA_HOME"], str(config.path / "data"))
                self.assertEqual(kwargs["env"]["XDG_CACHE_HOME"], str(config.path / "cache"))
                (config.path / "data/narwhal3d").mkdir(parents=True)
                (config.path / "data/narwhal3d/recent.json").write_text("[]")
                life_fd = kwargs["pass_fds"][1]
                self.assertEqual(kwargs["env"]["NARWHAL_AUTOMATION_LIFETIME_FD"], str(life_fd))
                lifetime = socket.socket(fileno=os.dup(life_fd))
                children[0].lifetime = lifetime
                stack.callback(lifetime.close)
                self.assertEqual(args, [str(self.runtime.app), "--factory-startup", "--disable-autoexec", "--python", str(self.runtime.bootstrap)])
                nonce = kwargs["env"]["NARWHAL_AUTOMATION_SESSION"]
                self.assertEqual(len(nonce), 32)
                if mode not in {"timeout", "eof", "app-death", "cancel", "disconnect", "overflow"}:
                    data = self.cli.READY_PREFIX + nonce.encode() + b"\n"
                    if mode == "bad-ready":
                        data = b"bad private payload\n"
                    endpoint.sendall(data)
                if mode == "disconnect":
                    endpoint.close()
                if mode == "app-death":
                    child.returncode = 9
                if mode == "cancel":
                    os.kill(os.getpid(), signal.SIGTERM)
            else:
                if mode == "spawn-fail":
                    children.pop()
                    raise OSError("secret must not be logged")
                child.stderr = log
                if mode == "ready-eof":
                    writer.close()
                elif mode == "ready-cancel":
                    os.kill(os.getpid(), signal.SIGTERM)
                elif mode == "revoke":
                    children[0].lifetime.close()
                in_read, in_write = os.pipe()
                child.stdin = os.fdopen(in_write, "wb", buffering=0)
                stack.callback(os.close, in_read)
                os.set_blocking(in_read, False)
                os.write(kwargs["stdout"].fileno(), b'{"jsonrpc":"2.0","id":1,"result":{}}\n')
                self.assertEqual(set(kwargs["env"]), {"NARWHAL_AUTOMATION_SESSION", "NARWHAL_AUTOMATION_FD"})
                self.assertEqual(kwargs["env"]["NARWHAL_AUTOMATION_SESSION"], children[0].kwargs["env"]["NARWHAL_AUTOMATION_SESSION"])

                def server_poll():
                    if child.returncode is None:
                        try:
                            received.extend(os.read(in_read, 4096))
                        except BlockingIOError:
                            pass
                        if received == request and mode == "success":
                            child.returncode = 0
                    return child.returncode
                child.poll = server_poll
            return child

        with patch.object(self.cli.subprocess, "Popen", side_effect=popen):
            if mode in {"bad-ready", "disconnect", "timeout", "app-death", "overflow"}:
                with self.assertRaises(self.cli.LaunchError):
                    self.cli.supervise(self.runtime, config, source, output, io.StringIO(), 0.05)
            elif mode == "spawn-fail":
                with self.assertRaises(OSError):
                    self.cli.supervise(self.runtime, config, source, output, io.StringIO(), 0.05)
            else:
                result = self.cli.supervise(self.runtime, config, source, output, io.StringIO(), 0.2)
                self.assertEqual(result, 130 if mode in {"cancel", "ready-cancel"} else 0)
        config.__exit__()
        self.assertFalse(children[0].terminated)
        self.assertFalse(children[0].killed)
        self.assertEqual(config.path.exists(), mode != "app-death")
        if mode != "app-death":
            self.assertEqual((config.path / "data/narwhal3d/recent.json").read_text(), "[]")
        self.assertTrue(all(child.poll() is not None for child in children[1:]))
        self.assertTrue(os.get_blocking(source.fileno()))
        if mode == "success":
            self.assertEqual(len(children), 2)
            self.assertEqual(received, request)
            output.seek(0)
            self.assertEqual(output.read(), b'{"jsonrpc":"2.0","id":1,"result":{}}\n')
        elif mode in {"ready-eof", "ready-cancel", "revoke"}:
            self.assertEqual(len(children), 2)
            self.assertTrue(children[1].terminated)
        else:
            self.assertEqual(len(children), 1)

    def test_child_environment_ignores_data_overrides(self):
        inherited = {"NARWHAL3D_DATA_DIR": "/existing-data", "XDG_DATA_HOME": "/existing-xdg-data",
                     "XDG_CACHE_HOME": "/existing-cache"}
        with patch.dict(os.environ, inherited):
            self.supervise_case("ready-eof")

    def test_ready_eof_preserves_app_and_config(self):
        self.supervise_case("ready-eof")

    def test_ready_signal_preserves_app_and_config(self):
        self.supervise_case("ready-cancel")

    def test_revocation_reaps_only_rust(self):
        self.supervise_case("revoke")

    def test_unused_config_is_removed(self):
        with self.cli.PrivateConfig() as config:
            path = config.path
        self.assertFalse(path.exists())

    def test_ambiguous_spawn_preserves_config(self):
        with self.cli.PrivateConfig() as config:
            config.launch_attempted = True
            path = config.path
        self.addCleanup(lambda: self.cli.shutil.rmtree(path, ignore_errors=True))
        self.assertTrue(path.exists())

    def test_child_descriptors_stdio_ready_and_reap(self):
        self.supervise_case("success")

    def test_startup_failure_and_eof_paths(self):
        for mode in ("eof", "timeout", "bad-ready", "disconnect", "app-death", "cancel", "spawn-fail"):
            with self.subTest(mode=mode):
                self.supervise_case(mode)


def mutation_checks():
    variants = (
        (HostTests, "test_model_switch_refuses_queued_read_and_cannot_resume",
         ("self._app.model is not self._model", "False")),
        (HostTests, "test_waits_for_normal_ui_readiness",
         ('os.environ.get("NARWHAL3D_UI_READY") != "1"', "False")),
        (HostTests, "test_bad_origin_closes_capability",
         ('values["NARWHAL_AUTOMATION_ROOT"] != str(_ROOT)', "False")),
        (CliTests, "test_startup_queue_is_bounded",
         ("len(pending) > MAX_PENDING", "len(pending) > MAX_PENDING + 100")),
        (CliTests, "test_ready_eof_preserves_app_and_config",
         ("# Deliberately exclude the GUI", "children.append(app)\n        # Deliberately exclude the GUI")),
        (CliTests, "test_ready_eof_preserves_app_and_config",
         ("elif self.app.poll() is None:\n            return",
          "elif self.app.poll() is None:\n            shutil.rmtree(self.path)\n            return")),
        (CliTests, "test_private_data_environment_is_stable",
         ('"NARWHAL3D_DATA_DIR": str(private_dir / "data/narwhal3d")',
          '"NARWHAL3D_DATA_DIR": environ.get("NARWHAL3D_DATA_DIR", str(private_dir / "data/narwhal3d"))')),
        (CliTests, "test_private_data_environment_is_stable",
         ('"XDG_DATA_HOME": str(private_dir / "data")',
          '"XDG_DATA_HOME": environ.get("XDG_DATA_HOME", str(private_dir / "data"))')),
        (CliTests, "test_private_data_environment_is_stable",
         ('"XDG_CACHE_HOME": str(private_dir / "cache")',
          '"XDG_CACHE_HOME": environ.get("XDG_CACHE_HOME", str(private_dir / "cache"))')),
        (CliTests, "test_resolver_never_reads_existing_user_data",
         ('env = os.environ if env is None else env',
          'env = os.environ if env is None else env\n    Path(env["NARWHAL3D_DATA_DIR"]).joinpath("recent.json").read_text()')),
        (CliTests, "test_filtered_logs_never_echo_payloads",
         ("text in SAFE_LOGS and text not in self.seen", "text not in self.seen")),
        (CliTests, "test_environment_no_secrets_or_injection",
         ('"LC_ALL", "LC_CTYPE", "TZ"', '"LC_ALL", "LC_CTYPE", "TZ", "OPENAI_API_KEY"')),
    )
    for base, test, replacement in variants:
        mutated = type("Mutated" + base.__name__, (base,), {"mutation": replacement})
        output = io.StringIO()
        result = unittest.TextTestRunner(stream=output).run(unittest.TestSuite([mutated(test)]))
        if not result.failures or result.errors:
            raise AssertionError("mutation must fail the behavioral assertion: " + test + "\n" + output.getvalue())
        print("RED confirmed: " + test)


if __name__ == "__main__":
    suite = unittest.TestSuite((unittest.defaultTestLoader.loadTestsFromTestCase(HostTests),
                                unittest.defaultTestLoader.loadTestsFromTestCase(CliTests)))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    mutation_checks()
    print("Host/CLI mutation variants rejected; working sources unchanged.")
