#!/usr/bin/env python3
"""Source-fixture acceptance for Narwhal's deferred first presentation patch."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "blender_patches" / "apply_deferred_first_present.py"
spec = importlib.util.spec_from_file_location("narwhal_deferred_present_patch", PATCH)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

checks = 0
failures = []


def check(condition, label):
    global checks
    checks += 1
    ok = bool(condition)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        failures.append(label)


with tempfile.TemporaryDirectory(prefix="narwhal-deferred-present-") as temporary:
    source = Path(temporary)
    window = source / "windowmanager" / "intern" / "wm_window.cc"
    window.parent.mkdir(parents=True)
    window.write_text("prefix\n" + module.WINDOW_OLD + module.RELEASE_OLD + "suffix\n",
                      encoding="utf-8")
    changed = module.apply(source)
    check(changed == ["windowmanager/intern/wm_window.cc",
                      "windowmanager/intern/wm_window.cc"],
          "patch changes the exact window and draw anchors")
    check(module.apply(source) == [], "patch is idempotent")
    window_text = window.read_text(encoding="utf-8")

    check("NARWHAL3D_DEFER_FIRST_PRESENT" in window_text
          and "if (!narwhal_waiting)" in window_text,
          "native window creation suppresses the initial clear/swap only when requested")
    release_function = window_text[
        window_text.index("static bool narwhal_defer_first_present"):
        window_text.index("void wm_window_swap_buffer_release")]
    check('if (narwhal_defer_first_present(ghost_window))' in window_text
          and "first presentation released (%s)" in window_text,
          "back-buffer draws continue while presentation waits with traced release")
    check("if (ghost_window->getDrawingContextType() != "
          "GHOST_kDrawingContextTypeOpenGL) {\n    return false;\n  }" in release_function,
          "stateful backends return without withholding their acquired buffer")
    check('const char *watchdog = std::getenv("NARWHAL3D_PRESENT_WATCHDOG");\n'
          "  if (watchdog != nullptr && watchdog[0] == '1') {\n"
          "    return false;\n  }" in release_function,
          "Python watchdog requests presentation without claiming UI readiness")
    check("return std::chrono::steady_clock::now() - wait_started < "
          "std::chrono::seconds(10);" in release_function,
          "native elapsed watchdog withholds only during its ten-second window")

launcher = (ROOT / "narwhal3d.sh").read_text(encoding="utf-8")
ui = (ROOT / "narwhal3d" / "ui.py").read_text(encoding="utf-8")
bootstrap = (ROOT / "launch" / "bootstrap.py").read_text(encoding="utf-8")
builder = (ROOT / "scripts" / "build_blender_linux.sh").read_text(encoding="utf-8")
check("export NARWHAL3D_DEFER_FIRST_PRESENT=1" in launcher
      and "unset NARWHAL3D_UI_READY" in launcher
      and "unset NARWHAL3D_PRESENT_WATCHDOG" in launcher,
      "launcher arms a fresh first-presentation gate for every process")
ready_assignment = 'os.environ["NARWHAL3D_UI_READY"] = "1"'
ready_guard = ui[ui.rindex('if (state["complete"]', 0, ui.index(ready_assignment)):
                 ui.index(ready_assignment) + len(ready_assignment)]
completion_predicate = '''state["complete"] = (len(panes) == 4
                                     and state["top_ready"]
                                     and state["left_ready"]
                                     and state["right_ready"])'''
check(ready_assignment in ui
      and "layout presentation ready" in ready_guard
      and completion_predicate in ui
      and ('if (state["complete"]\n'
           '                    and os.environ.get("NARWHAL3D_DEFER_FIRST_PRESENT") == "1"):'
           in ready_guard),
      "only four panes plus all three application bands release the native gate")
watchdog_function = bootstrap[bootstrap.index("def _presentation_watchdog"):
                              bootstrap.index("def _session_only_preferences")]
check('os.environ["NARWHAL3D_PRESENT_WATCHDOG"] = "1"' in watchdog_function
      and 'os.environ["NARWHAL3D_UI_READY"] = "1"' not in watchdog_function
      and "first_interval=9.0" in bootstrap
      and bootstrap.index("bpy.app.timers.register(_presentation_watchdog")
      < bootstrap.index("_session_only_preferences()", bootstrap.index("def main")),
      "bootstrap requests watchdog presentation before setup without claiming readiness")
marker_builder = builder[builder.index('  "$INSTALL_DIR/.narwhal3d-native-runtime.json"'):]
marker_invocation = marker_builder[:marker_builder.index("<<'PY'")]
marker_args = re.findall(r'^\s+"\$([A-Z_]+)"', marker_invocation, re.MULTILINE)
check(marker_args[-5:] == ["BRANDING_PATCH", "APP_DRAWN_PATCH", "BOUNDARY_PATCH",
                           "DEFERRED_PRESENT_PATCH", "ICON_SOURCE"]
      and '("blender_patches/apply_deferred_first_present.py", Path(sys.argv[9]))'
      in marker_builder
      and '("blender_patches/narwhal3d_app_icon.svg", Path(sys.argv[10]))'
      in marker_builder,
      "runtime provenance arguments and path-to-index mappings are exact")

public_spec = importlib.util.spec_from_file_location(
    "narwhal_public_branch", ROOT / "scripts" / "create_public_branch.py")
assert public_spec is not None and public_spec.loader is not None
public_module = importlib.util.module_from_spec(public_spec)
public_spec.loader.exec_module(public_module)
check("blender_patches/apply_deferred_first_present.py" in public_module.PATCH_FILES,
      "public source allowlist includes the native behavioral patch")

if failures:
    raise SystemExit(f"FAIL: {len(failures)}/{checks}: {failures}")
print(f"\n{checks}/{checks} deferred-first-presentation checks passed")
