#!/usr/bin/env python3
"""Focused source and behavior checks for the native welcome startup bridge."""
from __future__ import annotations

import ast
import builtins
import os
from pathlib import Path
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[1]
failures = []
passes = 0


def check(condition, message):
    global passes
    if condition:
        passes += 1
    else:
        failures.append(message)
        print(f"FAIL: {message}")


source = (ROOT / "launch" / "bootstrap.py").read_text(encoding="utf-8")
tree = ast.parse(source)
function = next(
    node for node in tree.body
    if isinstance(node, ast.FunctionDef) and node.name == "_welcome_request"
)
module = ast.Module(body=[function], type_ignores=[])
ast.fix_missing_locations(module)
opened = []
commands = []
namespace = {
    "os": os,
    "sys": sys,
    "_open_launch_file": lambda path: opened.append(path),
}
exec(compile(module, "launch/bootstrap.py", "exec"), namespace)
welcome_request = namespace["_welcome_request"]

package = types.ModuleType("narwhal3d")
package.__path__ = []
state_module = types.ModuleType("narwhal3d.state")
state_module.app_state = types.SimpleNamespace(feed_line=commands.append)
sys.modules.setdefault("narwhal3d", package)
sys.modules["narwhal3d.state"] = state_module

with tempfile.TemporaryDirectory(prefix="narwhal-welcome-test-") as temporary:
    request = Path(temporary) / "request"
    os.environ["NARWHAL3D_WELCOME_REQUEST"] = str(request)
    os.environ.pop("NARWHAL3D_UI_READY", None)

    request.write_text("open-dialog\n", encoding="utf-8")
    replacement_published = [False]

    def publish_while_reading(path, *args, **kwargs):
        if str(path).endswith(".reading") and not replacement_published[0]:
            temporary = request.with_suffix(".tmp")
            temporary.write_text("new\n", encoding="utf-8")
            os.replace(temporary, request)
            replacement_published[0] = True
        return builtins.open(path, *args, **kwargs)

    namespace["open"] = publish_while_reading
    race_state = {"tries": 0, "lines": None}
    check(welcome_request(race_state) == 0.1
          and race_state["lines"] == ["open-dialog"] and request.exists(),
          "a newer request published during an old read survives at the shared path")
    check(welcome_request(race_state) == 0.1 and race_state["lines"] == ["new"],
          "the surviving concurrent publication replaces the pending action next tick")
    namespace.pop("open")

    request.write_text("open-dialog\n", encoding="utf-8")
    state = {"tries": 0, "lines": None}
    check(welcome_request(state) == 0.1,
          "Open request waits while the application UI is not ready")
    check(not request.exists() and state["lines"] == ["open-dialog"],
          "atomic request is retained in timer state after its file is consumed")
    request.write_text("new\n", encoding="utf-8")
    check(welcome_request(state) == 0.1 and state["lines"] == ["new"],
          "a later pre-readiness choice replaces the earlier pending action")
    os.environ["NARWHAL3D_UI_READY"] = "1"
    check(welcome_request(state) is None and commands == ["New"],
          "the user's final pending choice reaches the existing command state machine")

    request.write_text("open-dialog\n", encoding="utf-8")
    open_dialog_state = {"tries": 0, "lines": None}
    check(welcome_request(open_dialog_state) is None and commands == ["New", "Open"],
          "Open delegates to the implemented Open command after readiness")

    request.write_text("open\n/models/Clock face.3dm\n", encoding="utf-8")
    open_state = {"tries": 0, "lines": None}
    check(welcome_request(open_state) is None
          and opened == ["/models/Clock face.3dm"],
          "recent-file requests preserve spaced paths and use the normal open path")

os.environ.pop("NARWHAL3D_WELCOME_REQUEST", None)
os.environ.pop("NARWHAL3D_UI_READY", None)

launcher = (ROOT / "narwhal3d.sh").read_text(encoding="utf-8")
check('exec "$WELCOME" --runtime "$BLENDER" --bootstrap "$BOOTSTRAP" -- "$@"' in launcher,
      "launcher delegates child supervision and argument forwarding to the native welcome")
check('runtime/${runtime_platform}-${runtime_arch}/bin/narwhal-welcome' in launcher,
      "launcher supports a packaged platform-specific welcome binary")

rust = (ROOT / "native" / "welcome" / "src" / "main.rs").read_text(encoding="utf-8")
stacking = (ROOT / "native" / "welcome" / "src" / "native_stacking.rs").read_text(
    encoding="utf-8"
)
check('line == READY_MARKER' in rust and 'line.contains(READY_MARKER)' not in rust,
      "fade readiness uses the exact native marker, not a substring")
check('WM_TRANSIENT_FOR' in stacking and 'restack_window' in stacking,
      "Linux welcome establishes native transient ownership and EWMH restacking")
check('remove_var("WAYLAND_DISPLAY")' in rust
      and 'set_var("WINIT_UNIX_BACKEND"' not in rust,
      "winit 0.30 is forced to X11 through its supported display selection")
check("with_transparent(true)" in rust and "ui.set_opacity(alpha_f)" in rust,
      "welcome applies compositor alpha to the complete surface during its final fade")
check("centered: true" in rust,
      "welcome explicitly requests centered desktop placement")
check("first_paint: Option<Instant>" in rust
      and "painted.elapsed() >= Duration::from_millis(350)" in rust,
      "runtime launch and minimum visibility are measured from the first paint")
check("impl Drop for WelcomeApp" in rust and "child.try_wait()" in rust
      and "std::process::exit(code)" in rust,
      "native supervisor cancels abandoned children and propagates runtime failures")
check('ui.add_enabled(\n                false,' in rust
      and rust.count("(Not implemented)") >= 3,
      "template choices remain visibly disabled rather than faking functionality")

print(f"\n{passes}/{passes + len(failures)} welcome checks passed")
if failures:
    raise SystemExit(1)
