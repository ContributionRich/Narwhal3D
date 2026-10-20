#!/usr/bin/env python3
"""Focused source and behavior checks for the native welcome startup bridge."""
from __future__ import annotations

import ast
import builtins
import os
from pathlib import Path
import subprocess
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
saved = []
welcome_units = {"Millimeters", "Centimeters", "Meters", "Inches", "Feet"}
namespace = {
    "os": os,
    "sys": sys,
    "_WELCOME_UNITS": welcome_units,
    "_open_launch_file": lambda path: opened.append(path),
}
exec(compile(module, "launch/bootstrap.py", "exec"), namespace)
welcome_request = namespace["_welcome_request"]

package = types.ModuleType("narwhal3d")
package.__path__ = []
class FakeAppState:
    def __init__(self):
        self.model = object()
        self.cancel_new = False

    def feed_line(self, line):
        commands.append(line)
        if line == "New" and not self.cancel_new:
            self.model = object()

    def mark_saved(self):
        saved.append(True)


state_module = types.ModuleType("narwhal3d.state")
state_module.app_state = FakeAppState()
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
    real_os = namespace["os"]

    def vanish_during_claim(source, _destination):
        os.remove(source)
        raise FileNotFoundError(source)

    namespace["os"] = types.SimpleNamespace(
        environ=os.environ,
        path=os.path,
        replace=vanish_during_claim,
        remove=os.remove,
    )
    claim_state = {"tries": 0, "lines": None}
    check(welcome_request(claim_state) == 0.1,
          "a request removed during claim retries instead of unregistering the listener")
    namespace["os"] = real_os

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
    check(welcome_request(state) == 0.1
          and commands == ["New", "-Units", "Millimeters"] and saved == [True],
          "default New choice uses real scripted Units command and becomes the baseline")

    request.write_text("new\nInches\n", encoding="utf-8")
    check(welcome_request(state) == 0.1
          and commands[-3:] == ["New", "-Units", "Inches"] and saved == [True, True],
          "the persistent request listener accepts a second canonical unit choice")

    state_module.app_state.cancel_new = True
    existing_model = state_module.app_state.model
    before_cancel = list(commands)
    request.write_text("new\nFeet\n", encoding="utf-8")
    check(welcome_request(state) == 0.1
          and state_module.app_state.model is existing_model
          and commands == before_cancel + ["New"] and saved == [True, True],
          "cancelled New cannot change units or mark the existing document saved")
    state_module.app_state.cancel_new = False

    before_invalid = list(commands)
    request.write_text("new\nFurlongs\n", encoding="utf-8")
    invalid_state = {"tries": 0, "lines": None}
    check(welcome_request(invalid_state) == 0.1 and commands == before_invalid,
          "unknown welcome units are refused instead of silently substituted")

    request.write_text("open-dialog\n", encoding="utf-8")
    open_dialog_state = {"tries": 0, "lines": None}
    check(welcome_request(open_dialog_state) == 0.1 and commands[-1] == "Open",
          "Open delegates to the implemented Open command and keeps listening")

    request.write_text("open\n/models/Clock face.3dm\n", encoding="utf-8")
    open_state = {"tries": 0, "lines": None}
    check(welcome_request(open_state) == 0.1
          and opened == ["/models/Clock face.3dm"],
          "recent-file requests preserve spaced paths and keep the listener active")

    request.write_text("new\nFeet\n", encoding="utf-8")
    closed = request.with_suffix(".closed")
    closed.write_text("", encoding="utf-8")
    closing_state = {"tries": 0, "lines": None}
    check(welcome_request(closing_state) is None
          and commands[-3:] == ["New", "-Units", "Feet"]
          and saved == [True, True, True] and not closed.exists(),
          "queued action executes before its close marker stops the listener")

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
attention = rust.index("self.lifetime.note_attention(now, interaction_started)")
alpha = rust.index("let alpha_f = self.alpha(&ctx, now)")
check("input.pointer.hover_pos().is_some()" in rust
      and "ATTENTION_GRACE_SECONDS" in rust
      and "self.engaged |= interaction_started" in rust
      and attention < alpha,
      "first pointer/input interaction permanently owns the welcome and cancels auto-fade")
close_start = rust.index('on_hover_text("Close welcome window")')
close_route = rust[close_start:close_start + 500]
hide_surface_start = rust.index("fn hide_surface")
hide_start = rust.index("fn hide_welcome")
hide_surface_route = rust[hide_surface_start:hide_start]
hide_route = rust[hide_start:hide_start + 200]
check("self.lifetime.dismiss()" in close_route
      and "self.hide_welcome(ui.ctx())" in close_route
      and "self.hide_surface(ctx)" in hide_route
      and "ViewportCommand::Visible(false)" in hide_surface_route
      and "cancel_child" not in close_route
      and "ViewportCommand::Close" not in close_route,
      "top-right close control immediately hides only the welcome surface")
top_tabs_start = rust.index("fn top_tabs")
top_tabs_end = rust.index("fn recent_grid", top_tabs_start)
top_tabs = rust[top_tabs_start:top_tabs_end]
check("welcome-drag" not in rust
      and 'self.send_request("open-dialog"' not in top_tabs
      and all(unit in rust for unit in welcome_units)
      and 'self.submit_and_hide("new", Some(unit), ui.ctx())' in rust
      and 'Button::new("Choose a model…")' in rust
      and 'ui.heading("Narwhal 3D")' in rust,
      "New, Open and About controls have unobstructed authored actions")
check("progress_state(self.ready" in rust
      and "(1.0, false)" in rust,
      "ready progress is complete and no longer animated")
submit_start = rust.index("fn submit_and_hide")
submit_end = rust.index("fn poll_submission", submit_start)
submit_route = rust[submit_start:submit_end]
poll_end = rust.index("fn mark_closed", submit_end)
poll_route = rust[submit_end:poll_end]
prepare_pos = submit_route.find("PendingRequest::prepare")
hide_pos = submit_route.find("self.hide_surface(ctx)")
publish_pos = poll_route.find("pending.publish")
closed_pos = poll_route.find("self.mark_closed()")
check(0 <= prepare_pos < hide_pos
      and "self.pending_request = Some(pending)" in submit_route
      and "self.hidden = false" in poll_route
      and "ViewportCommand::Visible(true)" in poll_route
      and 0 <= publish_pos < closed_pos
      and "cancel_child" not in submit_route + poll_route
      and "ViewportCommand::Close" not in submit_route + poll_route
      and 'self.submit_and_hide("new", Some(unit), ui.ctx())' in rust
      and 'self.submit_and_hide("open-dialog", None, ui.ctx())' in rust
      and '"open",\n                                Some(path.to_string_lossy().as_ref())' in rust,
      "New, Open and Recent hide before delayed publication and recover publication failure")

blender = ROOT / "tools" / "blender-patched" / "blender"
probe = (
    "import sys; "
    f"sys.path.insert(0, {str(ROOT)!r}); "
    "from narwhal3d.model import NarwhalModel; "
    "from narwhal3d.state import app_state; "
    "app_state.set_model(NarwhalModel()); app_state.mark_saved(); "
    "called=[]; app_state.dialog_openers['units']=lambda: called.append(True) or True; "
    "app_state.feed_line('New'); app_state.feed_line('-Units'); app_state.feed_line('Inches'); "
    "app_state.mark_saved(); "
    "assert not called; assert app_state.model.unit_system() == 'Inches'; "
    "assert app_state.active_command is None; assert not app_state.doc_modified(); "
    "print('SCRIPTED_UNITS_OK')"
)
result = subprocess.run(
    [str(blender), "-b", "--factory-startup", "--python-exit-code", "1",
     "--python-expr", probe],
    text=True,
    capture_output=True,
)
probe_ok = result.returncode == 0 and "SCRIPTED_UNITS_OK" in result.stdout
check(probe_ok,
      "production dispatcher bypasses the Units dialog and creates a clean Inches document")
if not probe_ok:
    print(result.stdout)
    print(result.stderr)

print(f"\n{passes}/{passes + len(failures)} welcome checks passed")
if failures:
    raise SystemExit(1)
