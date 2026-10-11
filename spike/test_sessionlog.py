"""Session-log tests for output capture, traceback capture, and log rotation.

Tests redirect storage to a temporary directory and never touch user data.
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "sessionlog", os.path.join(ROOT, "narwhal3d", "sessionlog.py"))
sl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sl)

results = []


def check(ok, label, detail=""):
    results.append((bool(ok), label, detail))


tmp = tempfile.mkdtemp()
real_out, real_err = sys.stdout, sys.stderr
try:
    env0 = dict(os.environ)
    os.environ.pop("NARWHAL3D_LOG_DIR", None)
    os.environ["NARWHAL3D_NO_DIALOGS"] = "1"
    check(not sl.log_dir().startswith(os.path.expanduser("~/.local")),
          "under the test harnesses the log goes to a temporary folder",
          sl.log_dir())
    os.environ.pop("NARWHAL3D_NO_DIALOGS")
    check(sl.log_dir().endswith(os.path.join(".local", "share", "narwhal3d", "logs")),
          "in the real app it is ~/.local/share/narwhal3d/logs", sl.log_dir())
    preferred = os.path.join(tmp, "narwhal3d-logs")
    os.environ["NARWHAL3D_LOG_DIR"] = preferred
    check(sl.log_dir() == preferred,
          "$NARWHAL3D_LOG_DIR is the log-directory override")
    os.environ.clear()
    os.environ.update(env0)

    path = os.path.join(tmp, sl.LOG_NAME)
    with open(path, "w") as fh:
        fh.write("last session\n")
    check(sl.available(path), "a non-empty log 'exists'")
    # the tee also writes to the stream it wraps: point that at memory, so the
    # deliberate traceback below does not reach run_tests.sh, which (rightly) fails any
    # suite that prints one
    import io
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    got = sl.install(path)
    print("[narwhal3d] hello from the app")
    try:
        raise ValueError("boom")
    except ValueError:
        traceback.print_exc()
    sys.stdout.flush()
    sys.stdout, sys.stderr = real_out, real_err
    body = open(path, encoding="utf-8").read()
    check(got == path and "[narwhal3d] hello from the app" in body,
          "print() lands in the session log", body[:200])
    check("Traceback (most recent call last)" in body and "ValueError: boom" in body,
          "and so does a traceback (stderr)", body[-200:])
    prev = os.path.join(tmp, sl.PREV_NAME)
    check(os.path.exists(prev) and open(prev).read() == "last session\n",
          "the previous session is kept as session.prev.log, not lost")
    check(sl.install(path) == path, "installing twice is harmless")
    t = sl.tail(path, lines=2)
    check(len(t) == 2 and t[-1] == "ValueError: boom", "tail() gives the last lines", t)
    empty = os.path.join(tmp, "empty.log")
    open(empty, "w").close()
    check(not sl.available(empty) and not sl.available(os.path.join(tmp, "nope.log")),
          "an empty or missing log does not 'exist' -- the checkbox stays off")
    check(sl.tail(os.path.join(tmp, "nope.log")) == [], "and its tail is empty")
except Exception:  # noqa: BLE001
    sys.stdout, sys.stderr = real_out, real_err
    results.append((False, "the suite ran without raising", traceback.format_exc()))
finally:
    sys.stdout, sys.stderr = real_out, real_err
    shutil.rmtree(tmp, ignore_errors=True)

failed = [r for r in results if not r[0]]
for ok, label, detail in results:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok else ""))
print(f"\n{len(results) - len(failed)}/{len(results)} session-log checks passed")
sys.exit(1 if failed else 0)
