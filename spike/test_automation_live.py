"""Disposable Blender-only bootstrap for test_automation_mcp.py --live.

No product UI registration. Creates a real AppState/NarwhalModel, real NURBS
geometry and actual Blender selection. The external controller owns MCP pipes.
"""
from __future__ import annotations

import ast
import importlib
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time
import types

import bpy
import rhino3dm as r3

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
pkg.__package__ = "narwhal3d"
sys.modules["narwhal3d"] = pkg
state = importlib.import_module("narwhal3d.state")
kernel = importlib.import_module("narwhal3d.kernel")
tools = importlib.import_module("narwhal3d.automation_tools")
bridge = importlib.import_module("narwhal3d.automation_bridge")

# Read the existing declared product version without executing the UI initializer.
init = ast.parse((ROOT / "narwhal3d/__init__.py").read_text())
info = next(ast.literal_eval(node.value) for node in init.body
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "bl_info"
                                                    for t in node.targets))
version = ".".join(map(str, info["version"]))
app = state.app_state
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob, do_unlink=True)
name = "Disposable-" + secrets.token_hex(8)
circle = r3.Circle(r3.Point3d(3, 4, 5), 7).ToNurbsCurve()
assert circle.Degree == 2 and circle.IsRational
kernel.tag(circle, kernel.ANALYTIC)
circle_id, _ = app.model.add_object(circle, name)
point_id, _ = app.model.add_object(r3.Point(r3.Point3d(9, 8, 7)), "Point")
ob = bpy.data.objects.new("AutomationDisplay", None)
bpy.context.collection.objects.link(ob)
bpy.context.view_layer.update()
ob.select_set(True)
bpy.context.view_layer.objects.active = ob
app.model.register_blender_object(circle_id, ob.name)


def fingerprint():
    return (app.model.file.Encode(), app.ctx.undo.top_serial,
            tuple(o.name for o in app.ctx.selected_objects()), app.active_command)


reader = tools.ApplicationReader(app, version)
thread_checks = []


def wrong_thread():
    try:
        reader.read("narwhal.document_summary", {})
    except RuntimeError as exc:
        thread_checks.append(str(exc))


worker = threading.Thread(target=wrong_thread)
worker.start()
worker.join(timeout=3)
assert thread_checks == ["main_thread_required"]


class CheckedReader:
    calls = 0

    def read(self, tool, arguments):
        assert threading.current_thread() is threading.main_thread()
        before = fingerprint()
        value = reader.read(tool, arguments)
        assert before == fingerprint(), "metadata read mutated document/undo/selection"
        self.calls += 1
        return value


checked = CheckedReader()
session = os.environ.pop("NARWHAL_AUTOMATION_SESSION")
fd = int(os.environ.pop("NARWHAL_AUTOMATION_FD"))
channel = bridge.MainThreadBridge(socket.socket(fileno=fd), checked, session)
work = Path(os.environ["NARWHAL_AUTOMATION_TEST_DIR"])
ready = {"circle_id": circle_id, "name": name, "blender_version": bpy.app.version_string}
(work / "ready.tmp").write_text(json.dumps(ready))
(work / "ready.tmp").replace(work / "ready.json")
advanced = False
deadline = time.monotonic() + 60
while not channel.closed and time.monotonic() < deadline:
    if not advanced and (work / "advance").exists():
        # Test-only local controller action. Not reachable through the bridge.
        app.model.add_object(r3.Point(r3.Point3d(1, 2, 3)), "FixtureChange")
        ob.select_set(False)
        advanced = True
        (work / "advanced").write_text("fixture")
    channel.pump()
    time.sleep(.005)
assert channel.closed, "bridge did not observe external MCP disconnect"
assert advanced and checked.calls == 7, checked.calls
assert app.model.object_count() == 3
print("AUTOMATION_LIVE_PASS")
