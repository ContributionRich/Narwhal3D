"""Actual shared-editor GUI acceptance; Python is required for bpy events/timers.

Run with bundled Python 3.13: spike/test_ui_core_review_gui.py --launch
Requires NARWHAL_UI_CORE_LIBRARY. Reuses the isolated workflow GUI fixture pattern:
one owned process, both locks, 2 GiB/no swap, 4 GiB desktop reserve, read-only source,
disposable HOME/settings, no network. No report submission or AI request is made.
Optional --mutations injects no-op insert/move only in this disposable process.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def launch():
    import fcntl
    import subprocess
    out = Path(tempfile.mkdtemp(prefix="ui-core-review-gui-"))
    library = Path(os.environ["NARWHAL_UI_CORE_LIBRARY"]).resolve(strict=True)
    env = {k: v for k, v in os.environ.items() if k in (
        "PATH", "DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "LANG")}
    paths = subprocess.check_output(["bash", str(ROOT / "narwhal3d.sh")], cwd=ROOT,
        env=dict(os.environ, NARWHAL3D_LAUNCHER_TEST="native-paths"), text=True, timeout=10)
    for row in paths.splitlines():
        key, value = row.split("=", 1)
        if value:
            env[key] = value
    for key in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "TMPDIR"):
        folder = out / key.lower()
        folder.mkdir()
        env[key] = str(folder)
    env.update(UI_CORE_REVIEW_OUT=str(out), UI_CORE_REVIEW_MUTATIONS=str("--mutations" in sys.argv),
        NARWHAL_UI_CORE_LIBRARY=str(library), NARWHAL3D_SETTINGS_FILE=str(out / "preferences.json"),
        PYTHONDONTWRITEBYTECODE="1", NARWHAL3D_NO_DIALOGS="1", NARWHAL3D_DEFER_FIRST_PRESENT="1",
        NARWHAL3D_STATUSBAR_HEIGHT="37", PYTHONPATH=str(ROOT / "vendor/occt"),
        GHOST_BACKEND="X11", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    sources = [ROOT / p for p in ("narwhal3d/textfield.py", "narwhal3d/native_textfield.py",
        "narwhal3d/ui.py", "narwhal3d/bugreport.py", "narwhal3d/ai_assistant.py",
        "narwhal3d/shell_input.py", "artifacts/application-core/linux-x86_64/libnarwhal_application_core.so")]
    sources.append(library)
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    cmd = ["systemd-run", "--user", "--scope", "--quiet", "-p", "MemoryMax=2048M",
        "-p", "MemorySwapMax=0", "-p", "CPUQuota=100%", "timeout", "--signal=TERM", "--kill-after=5s", "180s",
        "bwrap", "--ro-bind", "/", "/", "--dev-bind", "/dev", "/dev", "--proc", "/proc",
        "--bind", str(out), str(out), "--unshare-net", "--die-with-parent",
        str(ROOT / "tools/blender-patched/blender"), "--factory-startup", "-t", "1",
        "-p", "40", "40", "1440", "1000", "--enable-event-simulate", "--python-exit-code", "1",
        "--python", str(Path(__file__).resolve()), "--python", str(ROOT / "launch/bootstrap.py")]
    print("UI_CORE_REVIEW_EVIDENCE", out, flush=True)
    locks = []
    try:
        deadline = time.monotonic() + 90
        while True:
            try:
                for name in ("/tmp/narwhal3d-heavy-build.lock", "/tmp/narwhal3d_gui.lock"):
                    lock = open(name, "a")
                    locks.append(lock)
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                for lock in locks:
                    lock.close()
                locks.clear()
                if time.monotonic() >= deadline:
                    raise TimeoutError("GUI/heavy locks busy; nothing launched")
                time.sleep(.25)
        available = int(next(row.split()[1] for row in Path("/proc/meminfo").read_text().splitlines()
                             if row.startswith("MemAvailable:")))
        assert available >= 6 * 1024 * 1024, "Need 4 GiB reserve + 2 GiB GUI"
        (out / "launch.json").write_text(json.dumps(dict(command=cmd, source_sha256=before,
            mem_available_kib=available, native_paths=paths), indent=2))
        with (out / "run.log").open("w") as log:
            result = subprocess.run(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=190)
        text = (out / "run.log").read_text()
        print(text[-10000:], flush=True)
        after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
        (out / "source_check.json").write_text(json.dumps(dict(before=before, after=after,
            unchanged=before == after), indent=2))
        report = json.loads((out / "result.json").read_text()) if (out / "result.json").exists() else {}
        bad_log = any(s in text for s in ("Traceback (most recent call last)", "Error in draw callback"))
        return result.returncode or int(report.get("status") != "passed" or before != after or bad_log)
    finally:
        for lock in locks:
            lock.close()


if "--launch" in sys.argv:
    raise SystemExit(launch())

import threading
import traceback
import unicodedata
import bpy

OUT = Path(os.environ["UI_CORE_REVIEW_OUT"])
START = time.monotonic()
RESULT = dict(status="running", checks=[], mutations=[], events=[], captures=[], pid=os.getpid(),
              python=sys.version, unicode=unicodedata.unidata_version)
FINISH_LOCK = threading.Lock()
POSITION = (0, 0)


def check(ok, label):
    RESULT["checks"].append(dict(ok=bool(ok), label=label))
    print("UI_CORE_REVIEW", "PASS" if ok else "FAIL", label, flush=True)
    if not ok:
        raise AssertionError(label)


def finish(error=None):
    if not FINISH_LOCK.acquire(False):
        return
    RESULT.update(status="failed" if error else "passed", error=error, elapsed_s=time.monotonic()-START)
    try:
        group = Path("/sys/fs/cgroup" + Path("/proc/self/cgroup").read_text().strip().split(":", 2)[2])
        RESULT["cgroup"] = {key: (group/key).read_text().strip()
                            for key in ("memory.max", "memory.swap.max", "memory.peak")}
    except OSError:
        pass
    (OUT / "result.json").write_text(json.dumps(RESULT, indent=2))
    print("UI_CORE_REVIEW_RESULT", RESULT["status"], error or "", flush=True)
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(int(bool(error)))


def window():
    return bpy.context.window_manager.windows[0]


def event(kind, value="PRESS", point=None, **kwargs):
    global POSITION
    if point is not None:
        POSITION = tuple(round(v) for v in point)
    RESULT["events"].append((kind, value, POSITION, kwargs))
    window().event_simulate(type=kind, value=value, x=POSITION[0], y=POSITION[1], **kwargs)


def tap(kind, point=None, **kwargs):
    if point is not None:
        event("MOUSEMOVE", "NOTHING", point)
        yield .07
    event(kind, "PRESS", **kwargs)
    yield .07
    event(kind, "RELEASE", **{k: v for k, v in kwargs.items() if k != "unicode"})
    yield .18


def center(rect):
    return ((rect[0]+rect[2])/2, (rect[1]+rect[3])/2)


def wait(predicate, label, seconds=8):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("timed out: " + label)
        yield .05
    check(True, label)


def capture(name):
    path = OUT / (name + ".png")
    check(bpy.ops.screen.screenshot(filepath=str(path)) == {"FINISHED"}, name + " screenshot")
    RESULT["captures"].append(str(path))


def exercise(field, mirror, label, multiline):
    n = field._native
    check(n is not None, label + " native field")
    handle = n.state.handle
    for char in "abc":
        yield from tap(char.upper(), unicode=char)
    check(field.text == mirror() == "abc" and field.caret == 3, label + " actual typing")
    traffic = n.uploaded, n.downloaded
    yield from tap("LEFT_ARROW", shift=True)
    check(field.selection() == (2, 3), label + " shift caret selection")
    check((n.uploaded, n.downloaded) == traffic, label + " movement zero text traffic")
    yield from tap("C", ctrl=True)
    check(bpy.context.window_manager.clipboard == "c", label + " actual clipboard copy")
    yield from tap("X", ctrl=True)
    check(field.text == mirror() == "ab", label + " actual cut")
    bpy.context.window_manager.clipboard = "😀e\u0301\r\nZ"
    yield from tap("V", ctrl=True)
    expected = "ab😀e\u0301" + ("\n" if multiline else " ") + "Z"
    check(field.text == mirror() == expected, label + " normalized Unicode clipboard paste")
    yield from tap("HOME")
    check(field.caret == (6 if multiline else 0), label + " logical Home")
    yield from tap("END")
    check(field.caret == 7, label + " logical End")
    yield from tap("BACK_SPACE")
    check(field.text == mirror() == expected[:-1], label + " backspace")
    check(field._native is n and n.state.handle == handle, label + " owner persists across real events")
    if os.environ["UI_CORE_REVIEW_MUTATIONS"] == "True":
        for name, key, kwargs in (("insert", "Q", {"unicode": "q"}), ("move", "LEFT_ARROW", {})):
            original = getattr(type(field), name)
            before = field.text, field.caret
            setattr(type(field), name, lambda *_a, **_kw: None)
            try:
                yield from tap(key, **kwargs)
                # The positive GUI assertion must reject a suppressed real edit/move.
                changed = (field.text, field.caret) != before
                try:
                    assert changed, label + ": " + name + " did not change state"
                except AssertionError as exc:
                    RESULT["mutations"].append(dict(label=label, method=name, red=str(exc)))
                else:
                    raise AssertionError("GUI mutant survived: " + name)
            finally:
                setattr(type(field), name, original)
            yield from tap(key, **kwargs)
            check((field.text, field.caret) != before, label + " restored " + name + " GREEN")
    capture(label)


def run():
    from narwhal3d import ui, shell_input, bugreport, ai_assistant, native_document_session
    yield from wait(lambda: os.environ.get("NARWHAL3D_UI_READY") == "1" and ui._preferences_ready
                    and not ui._viewports_in_progress and ui.is_input_active()
                    and len(ui._right_tab_boxes) == 5, "normal visible bootstrap", 50)
    check(not bpy.app.background and ui.app_state.model.object_count() == 0, "visible disposable empty document")
    lib = native_document_session.load_library()
    digest = hashlib.sha256(Path(lib._name).read_bytes()).hexdigest()
    check(digest == "b156d9c049aa735e8b5c7c3b66e68fe87a3533cb0ac1eab73369bb02473cea21",
          "actual resolver loads Main staged application/history b156")
    check(ui.app_state.model.metadata_revision()["available"]
          and ui.app_state.ctx.undo.backend == "rust"
          and Path(ui.app_state.ctx.undo._backend._library._name).resolve() == Path(lib._name).resolve(),
          "live document metadata and unified history use staged Rust core")
    before_doc = ui.app_state.model.file.Encode()
    before_undo = ui.app_state.ctx.undo.top_serial
    r = ui._window_region(ui._cmdbar_area())
    yield from tap("LEFTMOUSE", (r.x+r.width*.7, r.y+15))
    check(ui._console_focus, "actual console click focus")
    yield from exercise(shell_input.sync(ui.app_state.input_buffer), lambda: ui.app_state.input_buffer, "console", False)
    yield from tap("A", ctrl=True)
    yield from tap("BACK_SPACE")
    check(ui.app_state.input_buffer == "", "console cleared without submitting")
    for label, tab, panel, module, state, textkey in (
        ("report", 3, ui._bug, bugreport, ui.bug_report_state(), "text"),
        ("chat", 4, ui._ai, ai_assistant, ui.ai_assistant_state(), "input_text")):
        yield from tap("LEFTMOUSE", center(ui._right_tab_boxes[tab]))
        yield from wait(lambda: panel.get("view") is not None, label + " drawn input")
        box = panel["lay"]["box" if label == "report" else "input"]
        rx, ry = panel["region"]
        x, y = center(box)
        yield from tap("LEFTMOUSE", (rx+x, ry+y))
        check(state.focus, label + " actual click focus")
        field = module.field_of(state)
        yield from exercise(field, lambda: getattr(state, textkey), label, True)
        # Use the actual drawn view and BLF widths to place/drag at known indices.
        yield from tap("A", ctrl=True)
        bpy.context.window_manager.clipboard = "abcdef"
        yield from tap("V", ctrl=True)
        view = panel["view"]
        measure = ui._bug_measure(11.0 * ui._ui_scale())
        def point(index):
            return (rx+view["x0"]+measure("abcdef"[:index]), ry+view["top"]-view["line_h"]*.5)
        yield from tap("LEFTMOUSE", point(2))
        check(field.caret == 2 and field.selection() is None, label + " actual click positions caret")
        event("LEFTMOUSE", "PRESS", point(2)); yield .1
        event("MOUSEMOVE", "NOTHING", point(5)); yield .15
        event("LEFTMOUSE", "RELEASE"); yield .2
        check(field.selection() == (2, 5), label + " actual drag selects cde")
        capture(label + "_selection")
        yield from tap("RET")
        check(field.text == getattr(state, textkey) == "ab\nf", label + " Enter edits, does not submit")
        check(ui.app_state.input_buffer == "", label + " keys do not leak to console")
        yield from tap("ESC")
        check(not state.focus, label + " Escape blurs")
    fields = (shell_input.sync(ui.app_state.input_buffer),
              bugreport.field_of(ui.bug_report_state()), ai_assistant.field_of(ui.ai_assistant_state()))
    def states():
        return [(f.text, f.caret, f.anchor, f._native.state.handle) for f in fields]
    saved = states()
    dimensions = (window().width, window().height)
    # Existing ui_resize_gui_check uses this real window-manager resize route.
    check(bpy.ops.wm.window_fullscreen_toggle() == {"FINISHED"}, "real fullscreen resize requested")
    yield from wait(lambda: (window().width, window().height) != dimensions, "actual window dimensions changed")
    yield 2.0
    yield from wait(lambda: not ui._viewports_in_progress, "resize reflow settled")
    check(states() == saved, "resize preserves all editor text/caret/anchor/handles")
    capture("resized_chat")
    check(bpy.ops.wm.window_fullscreen_toggle() == {"FINISHED"}, "window size restored")
    yield from wait(lambda: (window().width, window().height) == dimensions, "original window dimensions restored")
    yield 2.0
    yield from wait(lambda: not ui._viewports_in_progress, "restored reflow settled")
    check(states() == saved, "restored window preserves all editor states")
    panel = ui._ai
    rx, ry = panel["region"]
    x, y = center(panel["lay"]["input"])
    yield from tap("LEFTMOUSE", (rx+x, ry+y))
    yield from tap("END")
    yield from tap("Q", unicode="q")
    check(fields[2].text == ui.ai_assistant_state().input_text == "ab\nfq",
          "real chat typing works after resize")
    check(ui.app_state.model.file.Encode() == before_doc and ui.app_state.ctx.undo.top_serial == before_undo,
          "typing/report/chat leaves document and unified history unchanged")
    finish()


def tick():
    try:
        with bpy.context.temp_override(window=window()):
            return next(STEPS)
    except StopIteration:
        finish()
    except BaseException:
        finish(traceback.format_exc())


def watchdog():
    time.sleep(170)
    finish("170s GUI watchdog")


sys.path.insert(0, str(ROOT))
STEPS = run()
threading.Thread(target=watchdog, daemon=True).start()
bpy.app.timers.register(tick, first_interval=.1)
