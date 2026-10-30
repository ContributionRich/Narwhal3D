#!/usr/bin/env python3
"""Normal visible CLI acceptance; synthetic documents only, Linux, no build/provider.

python3 -B spike/test_automation_startup.py --era both
Acquires GUI/heavy locks, requires 6 GiB MemAvailable, and runs in a verified
2 GiB/zero-swap systemd scope. The external client executes the real CLI via a
process-local Popen instrumentation wrapper. Only that wrapper appends fixture
scripts to its owned app argv. No public launch flag or MCP execution tool exists.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import json
import os
from pathlib import Path
import runpy
import secrets
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
CLI = ROOT / "scripts/narwhal_mcp.py"
BOOT = ROOT / "launch/bootstrap.py"
TOOLS = {"narwhal.application_info", "narwhal.document_summary",
         "narwhal.selection_summary", "narwhal.object_summary", "narwhal.objects",
         "narwhal.layers", "narwhal.workspace", "narwhal.components", "narwhal.operations"}
LEGACY = "2025-11-25"
MODERN = "2026-07-28"
VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
CAPABILITIES_KEY = "io.modelcontextprotocol/clientCapabilities"


def modern_metadata():
    return {VERSION_KEY: MODERN, CAPABILITIES_KEY: {},
            "io.modelcontextprotocol/clientInfo": {"name": "normal-startup-acceptance", "version": "1"}}


def validate_catalog(tools):
    assert len(tools) == 9 and {tool["name"] for tool in tools} == TOOLS, "exact nine-read catalog required"
    for tool in tools:
        assert tool["annotations"]["readOnlyHint"] is True, "read-only tool annotation required"
        assert tool["inputSchema"]["additionalProperties"] is False, "exact read arguments required"


def catalog_controls(tools):
    validate_catalog(tools)
    variants = (tools[:4], tools + [tools[0]],
                tools[:-1] + [dict(tools[-1], name="narwhal.execute")])
    for invalid in variants:
        try:
            validate_catalog(invalid)
        except AssertionError:
            pass
        else:
            raise AssertionError("catalog negative control survived")
    validate_catalog(tools)


def validate_modern_result(result, *, cache_policy=False):
    assert result["resultType"] == "complete"
    if cache_policy:
        assert result["ttlMs"] == 0 and result["cacheScope"] == "private"
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "narwhal-read-only"


def record(path, value):
    tmp = path.with_suffix(".writing")
    tmp.write_text(json.dumps(value, sort_keys=True))
    tmp.replace(path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def available():
    return int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines()
                    if line.startswith("MemAvailable:"))) * 1024


def fixture(phase, case, variant):
    """Only called by private fixed scripts in the disposable app, never MCP."""
    import bpy
    sys.path.insert(0, str(ROOT))
    if phase == "before":
        if variant == "missing-attach":
            source = BOOT.read_text()
            needle = '    automation_host.attach_inherited(app_state, os.environ.get("NARWHAL3D_VERSION", ""))'
            assert source.count(needle) == 1
            exec(compile(source.replace(needle, "    pass  # TEST mutation: missing attachment"),
                         str(BOOT), "exec"), {"__file__": str(BOOT), "__name__": "__main__"})
        else:
            # Disable explicit/timer revocation AND the reader's new identity
            # defense, in this process only. Keep the stale-access assertion strong.
            from narwhal3d import automation_host as host
            source = Path(host.__file__).read_text()
            for needle in ("state.app_state is not self._app", "self._app.model is not self._model",
                           "self._model.file is not self._file"):
                assert source.count(needle) == 1
                source = source.replace(needle, "False")
            source += "\n# TEST mutation: disable explicit document-switch revocation.\ndef close():\n    pass\n"
            exec(compile(source, host.__file__, "exec"), host.__dict__)
            from narwhal3d import ai_workspace
            ai_workspace.WorkspaceSnapshots.current = lambda self: True
        return

    from narwhal3d import automation_host as host, state, model, ui, automation_tools
    app = state.app_state
    names = set()
    deadline = time.monotonic() + 45
    ready = False
    reads = []

    def fingerprint():
        return (id(app.model), app.model.file.Encode(), app.ctx.undo.top_serial,
                tuple(ob.name for ob in app.ctx.selected_objects()), app.active_command)

    def safe_quit():
        assert app.doc_path is None, "fixture must not close a user document"
        actual = {obj.Attributes.Name for obj in app.model.file.Objects}
        assert actual <= names, "unexpected user work: preserve the application"
        record(case / "quitting.json", {"pid": os.getpid(), "main_thread": True,
                                       "objects": app.model.object_count()})
        bpy.ops.wm.quit_blender()

    def tick():
        nonlocal ready
        try:
            assert threading.current_thread() is threading.main_thread()
            request = case / "quit.json"
            if request.exists() or time.monotonic() >= deadline:
                if request.exists():
                    assert json.loads(request.read_text()) == {"pid": os.getpid()}
                safe_quit()
                return None
            if not ready:
                if os.environ.get("NARWHAL3D_UI_READY") != "1":
                    return .05
                assert not bpy.app.background
                assert app.model.object_count() == 0 and app.doc_path is None
                assert len([a for a in bpy.context.screen.areas if a.type == "VIEW_3D"]) >= 4
                session = host._session
                if variant != "missing-attach":
                    assert session is not None and session._app is app
                    assert bpy.app.timers.is_registered(session._timer_callback)
                    original = session._bridge._reader.read

                    def checked(tool, arguments):
                        assert threading.current_thread() is threading.main_thread()
                        before = fingerprint()
                        result = original(tool, arguments)
                        assert fingerprint() == before, "metadata read changed document/undo/selection"
                        reads.append(tool)
                        record(case / "reads.json", {"tools": reads, "main_thread": True,
                                                   "read_only_fingerprints": True})
                        return result
                    session._bridge._reader.read = checked
                modules = (state, model, ui, host, automation_tools, sys.modules["narwhal3d"])
                paths = {m.__name__: str(Path(m.__file__).resolve()) for m in modules}
                assert all(Path(p).is_relative_to(ROOT / "narwhal3d") for p in paths.values())
                record(case / "ready.json", {"pid": os.getpid(), "root": str(host._ROOT),
                       "module_paths": paths, "initial_objects": 0, "normal_ui": True,
                       "registered_host_timer": session is not None, "main_thread": True,
                       "version": os.environ["NARWHAL3D_VERSION"],
                       "private_data": os.environ["NARWHAL3D_DATA_DIR"]})
                ready = True
            for action in ("seed", "advance", "replace", "inspect"):
                request = case / (action + ".json")
                if not request.exists():
                    continue
                assert json.loads(request.read_text()) == {"pid": os.getpid()}
                request.unlink()
                if action in {"seed", "advance"}:
                    import rhino3dm as r3
                    oid, name = app.ctx.add_geometry(r3.Point(r3.Point3d(1, 2, 3)),
                                                     "MCPFixture" + secrets.token_hex(8))
                    names.add(name)
                    for ob in app.ctx.selected_objects():
                        ob.select_set(False)
                    if action == "seed":
                        bpy.data.objects[name].select_set(True)
                    result = {"object_id": oid, "name": name}
                elif action == "replace":
                    app.set_model(model.NarwhalModel())
                    result = {"replaced": True}
                else:
                    result = {"host_closed": host._session.closed if host._session else None}
                result.update(pid=os.getpid(), objects=app.model.object_count())
                record(case / (action + "-done.json"), result)
            return .05
        except Exception as exc:
            record(case / "fixture-error.json", {"error_type": type(exc).__name__})
            return .05  # Keep the fixed safe-quit path available; preserve unexpected user work.

    bpy.app.timers.register(tick, first_interval=.05)


def cli_wrapper(case, variant):
    """Run the actual CLI entrypoint; instrument only its own Popen calls."""
    real_popen = subprocess.Popen
    for phase in ("before", "after"):
        (case / (phase + ".py")).write_text(
            "import runpy\nrunpy.run_path(" + repr(str(HERE)) + ", init_globals=" +
            repr({"_PHASE": phase, "_CASE": str(case), "_VARIANT": variant}) + ")\n")

    def spawn(argv, **kwargs):
        args = list(argv)
        if "--factory-startup" in args:
            assert args[1:] == ["--factory-startup", "--disable-autoexec", "--python", str(BOOT)]
            normal_args = list(args)
            if variant == "missing-attach":
                args[-1] = str(case / "before.py")
            elif variant == "disabled-revocation":
                args[1:1] = ["--python", str(case / "before.py")]
            args += ["--python", str(case / "after.py")]
            child = real_popen(args, **kwargs)
            env = kwargs["env"]
            record(case / "app.json", {"pid": child.pid, "normal_argv": normal_args,
                   "config": str(Path(env["NARWHAL3D_SETTINGS_FILE"]).parent),
                   "data": env["NARWHAL3D_DATA_DIR"], "cache": env["XDG_CACHE_HOME"],
                   "version": env["NARWHAL3D_VERSION"], "devnull_output":
                   kwargs["stdout"] == subprocess.DEVNULL and kwargs["stderr"] == subprocess.DEVNULL})
        else:
            child = real_popen(args, **kwargs)
            record(case / "rust.json", {"pid": child.pid, "binary": args[0],
                                       "sha256": digest(Path(args[0]))})
        return child

    subprocess.Popen = spawn
    sys.argv = [str(CLI), "--read-only", "--startup-timeout",
                "12" if variant == "missing-attach" else "25"]
    runpy.run_path(str(CLI), run_name="__main__")


class Client:
    def __init__(self, case, variant, era):
        self.era = era
        self.log = (case / "cli.stderr").open("wb")
        self.process = subprocess.Popen([sys.executable, "-B", str(HERE), "--cli", str(case), variant],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log)
        self.buffer = bytearray()
        self.rid = 0

    def send(self, method, params=None, notification=False):
        message = {"jsonrpc": "2.0", "method": method}
        if not notification:
            self.rid += 1
            message["id"] = self.rid
        if self.era == MODERN and not notification:
            params = dict(params or {})
            params.setdefault("_meta", modern_metadata())
        if params is not None:
            message["params"] = params
        self.process.stdin.write(json.dumps(message).encode() + b"\n")
        self.process.stdin.flush()
        return self.rid

    def receive(self, rid, timeout=8):
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            assert remaining > 0 and select.select([self.process.stdout], [], [], remaining)[0], "MCP response timeout"
            chunk = os.read(self.process.stdout.fileno(), 65536)
            assert chunk, "MCP stdout closed before reply"
            self.buffer.extend(chunk)
            assert len(self.buffer) <= 196608
        line, _, rest = self.buffer.partition(b"\n")
        self.buffer = bytearray(rest)
        reply = json.loads(line)
        assert reply["jsonrpc"] == "2.0" and reply["id"] == rid, "non-protocol stdout"
        return reply

    def request(self, method, params=None, timeout=8):
        return self.receive(self.send(method, params), timeout)

    def call(self, tool, arguments=None):
        time.sleep(.04)  # Respect M1's documented rate limit, not a hidden retry.
        reply = self.request("tools/call", {"name": tool, "arguments": arguments or {}})
        assert not reply.get("error") and not reply["result"].get("isError"), "application read_error"
        result = reply["result"]
        if self.era == MODERN:
            validate_modern_result(result)
        assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
        return result["structuredContent"]

    def stop(self):
        if not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()  # Only our CLI; it must preserve the app.
            self.process.wait(timeout=5)
        self.process.stdout.close()
        self.log.close()


def wait_file(case, name, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assert not (case / "fixture-error.json").exists(), "fixture main-thread assertion failed"
        path = case / name
        if path.exists():
            return json.loads(path.read_text())
        time.sleep(.05)
    raise AssertionError("fixture deadline: " + name)


def action(case, name, pid):
    record(case / (name + ".json"), {"pid": pid})
    return wait_file(case, name + "-done.json")


def assert_revoked(client):
    try:
        assert client.process.wait(timeout=3) == 0
    except subprocess.TimeoutExpired:
        raise AssertionError("stale consent survived document replacement") from None


def run_case(output, variant, era):
    case = output / (era + "-" + variant)
    case.mkdir(mode=0o700)
    client = Client(case, variant, era)
    app = None
    pidfd = rustfd = None
    result = {"case": variant, "protocol": era}
    started = time.monotonic()
    try:
        app = wait_file(case, "app.json", 8)
        pidfd = os.pidfd_open(app["pid"])
        assert app["devnull_output"]
        if era == MODERN:
            init_id = client.send("server/discover")
        else:
            init_id = client.send("initialize", {"protocolVersion": LEGACY, "capabilities": {},
                                  "clientInfo": {"name": "normal-startup-acceptance", "version": "1"}})
        if variant == "missing-attach":
            ready = wait_file(case, "ready.json", 20)
            assert not ready["registered_host_timer"]
            try:
                client.receive(init_id, 15)
            except AssertionError as exc:
                assert str(exc) == "MCP stdout closed before reply"
            else:
                raise AssertionError("missing-attach mutation did not fail protocol startup")
            assert client.process.wait(timeout=2) == 1
            assert not (case / "rust.json").exists()
            assert b"readiness timed out" in (case / "cli.stderr").read_bytes()
            result["mutation_red"] = "missing bootstrap attachment: readiness timeout"
        else:
            initialized = client.receive(init_id, 30)
            if era == MODERN:
                discovery = initialized["result"]
                validate_modern_result(discovery, cache_policy=True)
                assert set(discovery["supportedVersions"]) == {LEGACY, MODERN}
                assert discovery["capabilities"] == {"tools": {}}
                result["discovery_without_initialize"] = True
            else:
                assert initialized["result"]["protocolVersion"] == LEGACY
                client.send("notifications/initialized", notification=True)
            rust = wait_file(case, "rust.json")
            rustfd = os.pidfd_open(rust["pid"])
            ready = wait_file(case, "ready.json")
            assert ready["registered_host_timer"] and ready["root"] == str(ROOT)
            assert ready["private_data"] == str(Path(app["config"]) / "data/narwhal3d")
            listed = client.request("tools/list")["result"]
            if era == MODERN:
                validate_modern_result(listed, cache_policy=True)
            discovered = listed["tools"]
            catalog_controls(discovered)
            result["catalog_controls"] = "three invalid catalogs RED; exact nine-read catalog GREEN"
            info = client.call("narwhal.application_info")
            assert info["application_version"] == app["version"] == ready["version"]
            assert info["permissions"] == {"read_only": True, "mutations": False}
            assert client.call("narwhal.document_summary")["object_count"] == 0
            assert client.call("narwhal.selection_summary")["objects"] == []
            seed = action(case, "seed", app["pid"])
            assert seed["objects"] == 1
            assert client.call("narwhal.document_summary")["object_count"] == 1
            selected = client.call("narwhal.selection_summary")["objects"]
            assert len(selected) == 1 and selected[0]["object_id"] == seed["object_id"]
            obj = client.call("narwhal.object_summary", {"object_id": seed["object_id"]})
            assert obj["name"] == seed["name"] and obj["object_id"] == seed["object_id"]
            objects = client.call("narwhal.objects", {"offset": 0, "limit": 1})
            assert len(objects["objects"]) == 1 and objects["objects"][0]["object_id"] == seed["object_id"]
            assert objects["total_count"] == 1 and objects["next_offset"] is None
            layers = client.call("narwhal.layers", {"offset": 0, "limit": 1})
            assert len(layers["layers"]) == 1 and layers["total_count"] >= 1
            workspace = client.call("narwhal.workspace")
            assert workspace["active_command"]["busy"] is False
            assert workspace["active_command"]["phase"]["value"] == "idle"
            assert workspace["cplane"]["available"] is True
            assert set(workspace["cplane"]["value"]) == {"origin", "xaxis", "yaxis", "zaxis"}
            assert all(len(v) == 3 for v in workspace["cplane"]["value"].values())
            assert workspace["view"]["available"] is True
            assert workspace["view"]["value"]["projection"] in {"PERSP", "ORTHO", "CAMERA"}
            components = client.call("narwhal.components")
            assert components["available"] is True and components["components"] == []
            assert components["truncated"] is False
            operations = client.call("narwhal.operations")["operations"]
            assert len(operations) == 1 and operations[0]["command"] == "Circle"
            assert operations[0]["execution_available"] is False
            if era == MODERN:
                before_reads = wait_file(case, "reads.json")["tools"]
                for missing in (VERSION_KEY, CAPABILITIES_KEY):
                    metadata = modern_metadata()
                    del metadata[missing]
                    rejected = client.request("tools/call", {"name": "narwhal.document_summary",
                                              "arguments": {}, "_meta": metadata})
                    assert rejected.get("error", {}).get("code") == -32602, "missing per-request metadata accepted"
                assert wait_file(case, "reads.json")["tools"] == before_reads, "invalid metadata reached application"
                assert client.call("narwhal.document_summary")["object_count"] == 1
                result["metadata_controls"] = "missing version/capabilities refused before read; valid request still works"
            refusal = client.request("tools/call", {"name": "narwhal.execute", "arguments": {"command": "Delete"}})
            assert "error" in refusal
            assert client.call("narwhal.document_summary")["object_count"] == 1
            assert action(case, "advance", app["pid"])["objects"] == 2
            assert client.call("narwhal.document_summary")["object_count"] == 2
            assert client.call("narwhal.selection_summary")["objects"] == []
            if variant in {"revoke", "disabled-revocation"}:
                assert action(case, "replace", app["pid"])["objects"] == 0
                if variant == "disabled-revocation":
                    # This MUST violate the positive revocation assertion.
                    try:
                        assert_revoked(client)
                    except AssertionError as exc:
                        assert str(exc) == "stale consent survived document replacement"
                    else:
                        raise AssertionError("disabled-revocation mutation escaped the test")
                    assert client.call("narwhal.document_summary")["object_count"] == 0
                    result["mutation_red"] = "disabled revocation: stale consent still reads replacement"
                    client.stop()
                else:
                    assert_revoked(client)
                    assert action(case, "inspect", app["pid"])["host_closed"]
                    result["revocation"] = "Rust/CLI stopped; application stayed alive"
            else:
                client.stop()
                assert action(case, "inspect", app["pid"])["host_closed"]
                result["eof"] = "Rust/CLI stopped; application stayed alive"
            result.update(tools=9, counts=[0, 1, 2],
                          version=app["version"], main_thread_read_checks=len(wait_file(case, "reads.json")["tools"]))
        if rustfd is not None:
            assert select.select([rustfd], [], [], 1)[0], "Rust survived MCP shutdown"
            result["rust_exited"] = True
        assert not select.select([pidfd], [], [], .2)[0], "disconnect killed the visible app"
        assert Path(app["config"]).is_dir(), "live app configuration was removed"
        result["alive_after_disconnect"] = True
        result["config_retained"] = True
    finally:
        client.stop()
        if rustfd is not None:
            os.close(rustfd)
        if app is not None and pidfd is not None:
            # Fixed PID, fixed action, synthetic-only main-thread safety check.
            # Never signal another GUI or use a process-name/group kill.
            record(case / "quit.json", {"pid": app["pid"]})
            exited = bool(select.select([pidfd], [], [], 10)[0])
            result["fixture_quit"] = exited and (case / "quitting.json").exists()
            os.close(pidfd)
            if exited:
                assert Path(app["config"]).is_dir(), "config disappeared before owned app ended"
                shutil.rmtree(app["config"])
            else:
                result["preserved_app_pid"] = app["pid"]
                raise AssertionError("owned app did not quit safely; preserved for Main")
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        record(case / "result.json", result)
    assert result["fixture_quit"], "main-thread graceful fixture quit not proven"
    record(case / "result.json", result)
    print("PASS " + era + " " + variant + ": " + json.dumps(result, sort_keys=True), flush=True)
    return result


def capped_worker(output, era_choice):
    cgroup = Path("/sys/fs/cgroup") / Path("/proc/self/cgroup").read_text().strip().split(":", 2)[2].lstrip("/")
    assert (cgroup / "memory.max").read_text().strip() == "2147483648"
    assert (cgroup / "memory.swap.max").read_text().strip() == "0"
    inputs = [CLI, BOOT, ROOT / "narwhal3d/automation_host.py", ROOT / "narwhal3d/state.py",
              ROOT / "narwhal3d/__init__.py", HERE,
              ROOT / "artifacts/automation/linux-x86_64/narwhal-automation",
              ROOT / "artifacts/automation/linux-x86_64/libnarwhal_automation.so"]
    before = {str(p.relative_to(ROOT)): digest(p) for p in inputs}
    record(output / "provenance.json", {"sha256": before, "memory_max": 2147483648, "swap_max": 0})
    results = []

    def timed_out(_signum, _frame):
        raise TimeoutError("integration budget exhausted; close only the synthetic owned app")

    signal.signal(signal.SIGALRM, timed_out)
    signal.alarm(135)  # Leave cleanup headroom within the hard in-scope timeout.
    eras = (LEGACY, MODERN) if era_choice == "both" else (LEGACY if era_choice == "legacy" else MODERN,)
    try:
        for era in eras:
            for variant in ("eof", "revoke", "missing-attach", "disabled-revocation"):
                until = time.monotonic() + 15
                while available() < 6 * 1024**3:
                    assert time.monotonic() < until, "headroom below 6 GiB; no further GUI launched"
                    time.sleep(1)
                results.append(run_case(output, variant, era))
    finally:
        assert before == {str(p.relative_to(ROOT)): digest(p) for p in inputs}, "production inputs changed during test"
        record(output / "summary.json", {"completed": results, "production_inputs_unchanged": True,
                                         "memory_peak": int((cgroup / "memory.peak").read_text())})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", nargs=2, metavar=("CASE", "VARIANT"))
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--era", choices=("legacy", "modern", "both"), default="both")
    args = parser.parse_args()
    if args.cli:
        cli_wrapper(Path(args.cli[0]), args.cli[1])
        return
    if args.worker:
        capped_worker(args.worker, args.era)
        return
    assert sys.platform == "linux", "this acceptance driver requires Linux pidfds/cgroups"
    import fcntl
    with contextlib.ExitStack() as stack:
        until = time.monotonic() + 20
        for path in ("/tmp/narwhal3d-heavy-build.lock", "/tmp/narwhal3d_gui.lock"):
            lock = stack.enter_context(open(path, "a"))
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= until:
                        raise SystemExit("BLOCKED: shared GUI/heavy lock busy; no GUI launched")
                    time.sleep(1)
        while available() < 6 * 1024**3:
            if time.monotonic() >= until:
                raise SystemExit("BLOCKED: require 4 GiB reserve + 2 GiB cap; no GUI launched")
            time.sleep(1)
        output = Path(tempfile.mkdtemp(prefix="narwhal-startup-"))
        print("Evidence: " + str(output), flush=True)
        print("MemAvailable bytes: " + str(available()), flush=True)
        command = ["systemd-run", "--user", "--scope", "--quiet", "-p", "MemoryMax=2G",
                   "-p", "MemorySwapMax=0", "-p", "CPUQuota=200%",
                   "timeout", "--signal=TERM", "--kill-after=5s", "160s", sys.executable,
                   "-B", str(HERE), "--worker", str(output), "--era", args.era]
        result = subprocess.run(command, timeout=175)
        raise SystemExit(result.returncode)


if "_PHASE" in globals():
    fixture(globals()["_PHASE"], Path(globals()["_CASE"]), globals()["_VARIANT"])
elif __name__ == "__main__":
    main()
