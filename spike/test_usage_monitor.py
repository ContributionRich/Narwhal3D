"""Opt-in local usage monitor: records, stalls, tracebacks, bounds and privacy.

Run:
  tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_usage_monitor.py [--mutations]
"""
import importlib
import json
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg = types.ModuleType("narwhal3d")
_pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = _pkg
um = importlib.import_module("narwhal3d.usage_monitor")
br = importlib.import_module("narwhal3d.bugreport")
sessionlog = importlib.import_module("narwhal3d.sessionlog")


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def events(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def run():
    failures = []

    def check(ok, label):
        print(("PASS: " if ok else "FAIL: ") + label)
        if not ok:
            failures.append(label)

    check(not br.ReportState().monitor_usage, "the Monitor usage checkbox is OFF by default")
    lay = br.layout((0, 0, 220, 640))
    rect = dict(lay["checks"])["usage"]
    st = br.ReportState()
    did = br.click(st, lay, rect[0] + 5, (rect[1] + rect[3]) / 2)
    check(did == "check:usage" and st.monitor_usage, "clicking the checkbox turns monitoring on")
    br.click(st, lay, rect[0] + 5, (rect[1] + rect[3]) / 2)
    check(not st.monitor_usage, "and clicking again turns it off")
    rows = sorted(r for _cid, r in lay["checks"])
    check(all(a[3] <= b[1] + 1e-9 for a, b in zip(rows, rows[1:])), "four checkboxes do not overlap")

    clock = Clock()
    monitor = um.UsageMonitor(clock=clock, wall=lambda: 1.5e9)
    check(monitor.heartbeat() is None and not monitor.record("command"),
          "a disabled monitor records nothing")
    with tempfile.TemporaryDirectory() as folder:
        path = monitor.enable(folder, build="test")
        check(os.path.dirname(path) == folder and path.endswith(".jsonl"),
              "enabling creates one local JSON Lines session file")
        monitor.command("Line", 0.02, message="Created Line1")
        monitor.command("SaveAs", 15.5, message="Saved project")
        monitor.command("Explode", 0.01, ok=False, message="Error: boom")
        check(monitor.heartbeat(lambda: {"active_command": None}) is None,
              "the first heartbeat only starts timing")
        clock.now += 0.1
        check(monitor.heartbeat() is None, "a normal 0.1 s heartbeat is not a stall")
        clock.now += 2.0
        gap = monitor.heartbeat(lambda: {"active_command": "Trim", "last_line": "Trimmed 1"})
        check(gap is not None and abs(gap - 2.0) < 1e-9, "a 2 s heartbeat gap is recorded as a stall")
        sessionlog.listeners[:] = [monitor.observe_output]
        monitor.observe_output("Traceback (most recent call last):\n  File \"x.py\", line 1\n")
        monitor.observe_output("ValueError: bad thing\n[narwhal3d] draw failed: x\nordinary line\n")
        sessionlog.listeners.clear()
        monitor.disable()
        rows = events(path)
        kinds = [row["kind"] for row in rows]
        check(kinds[0] == "session_start" and kinds[-1] == "session_stop",
              "a session file is bracketed by start and stop records")
        commands = [row for row in rows if row["kind"] == "command"]
        check([row["name"] for row in commands] == ["Line", "SaveAs", "Explode"]
              and commands[2]["ok"] is False, "each command is recorded with its outcome")
        stall = next((row for row in rows if row["kind"] == "stall"), {})
        check(stall.get("active_command") == "Trim" and stall.get("seconds") == 2.0,
              "a stall carries the command context")
        trace = next((row for row in rows if row["kind"] == "traceback"), {"text": ""})
        check(trace["text"].startswith("Traceback") and trace["text"].endswith("ValueError: bad thing"),
              "a whole traceback is captured as one record")
        check(any(row["kind"] == "diagnostic" and "draw failed" in row["text"] for row in rows)
              and not any("ordinary line" in json.dumps(row) for row in rows),
              "diagnostic lines are kept and ordinary output is not")
        summary = monitor.summary()
        check(summary[0] == f"Log: {path}" and "1 error(s)" in summary[1]
              and "1 slow command(s)" in summary[1] and "1 stall(s)" in summary[1],
              "the report summary counts errors, slow commands and stalls")
        check(not any("Created Line1" in line for line in summary),
              "the summary lists notable events, not every command")
        monitor.command("After", 0.1)
        check(len(events(path)) == len(rows), "after disabling, nothing more is written")

        small = um.UsageMonitor()
        old = um.MAX_FILE_BYTES
        um.MAX_FILE_BYTES = 600
        try:
            limited = small.enable(folder)
            for index in range(50):
                small.command(f"C{index}", 0.0, message="x" * 50)
            small.disable()
        finally:
            um.MAX_FILE_BYTES = old
        rows = events(limited)
        check(rows[-1]["kind"] == "limit_reached" and os.path.getsize(limited) < 800,
              "the session file stops at its size limit and says so")

        class App:
            """The AppState dispatch surface, without native transcript dependencies."""
            def __init__(self):
                self.history, self.active_command = [], None

            def start_command(self, name, rest=None):
                self.history.append(f"{name}: started")
                if name == "Broken":
                    self.history.append("Error: RuntimeError('boom')")
                return None

            def feed_line(self, text):
                return self.start_command(text.split()[0])  # nested dispatch

        app = App()
        timed = um.UsageMonitor()
        dispatched = timed.enable(folder)
        check(um.instrument(app, timed) and not um.instrument(app, timed),
              "instrumenting is idempotent")
        app.feed_line("Circle 0,0,0")
        app.start_command("Broken")
        check(um.uninstrument(app) and "feed_line" not in vars(app)
              and app.feed_line.__func__ is App.feed_line, "uninstrumenting restores the class methods")
        app.feed_line("Line")
        timed.disable()
        rows = [row for row in events(dispatched) if row["kind"] == "command"]
        check([(row["name"], row["phase"], row["ok"]) for row in rows]
              == [("Circle", "start", True), ("Broken", "start", False)],
              "each outermost dispatch is timed once, failures marked, none after removal")
    return failures


if __name__ == "__main__":
    failures = run()
    if "--mutations" in sys.argv:
        original = um.UsageMonitor.heartbeat
        um.UsageMonitor.heartbeat = lambda self, context=None: None
        try:
            red = run()
        finally:
            um.UsageMonitor.heartbeat = original
        print("RED verified: stall detection removed" if red else "FAIL: stall mutation survived")
        failures += [] if red else ["stall mutation survived"]
        original_click = br.click
        br.click = lambda state, *a, **k: original_click(state, *a, **k) if not state.__setattr__(
            "monitor_usage", False) else None
        try:
            red = run()
        finally:
            br.click = original_click
        print("RED verified: checkbox toggle removed" if red else "FAIL: toggle mutation survived")
        failures += [] if red else ["toggle mutation survived"]
    print(f"{'FAILED' if failures else 'OK'}: usage monitor")
    sys.exit(1 if failures else 0)
