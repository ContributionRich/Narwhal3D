"""A foreign-window X protocol error must not terminate the application.

Run (needs an X server / XWayland DISPLAY; skips cleanly without one):
  tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_x11_errors.py [--mutations]
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CHILD = r'''
import ctypes, importlib, os, sys, types
pkg = types.ModuleType("narwhal3d"); pkg.__path__ = [os.path.join(sys.argv[1], "narwhal3d")]
sys.modules["narwhal3d"] = pkg
x11 = importlib.import_module("narwhal3d.x11_errors")
if sys.argv[2] == "install":
    assert x11.install(), "handler not installed"
lib = ctypes.CDLL("libX11.so.6")
lib.XOpenDisplay.restype = ctypes.c_void_p
lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
lib.XInternAtom.restype = ctypes.c_ulong
lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
lib.XChangeProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
                                ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
lib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
display = lib.XOpenDisplay(None)
assert display, "no display"
atom = lib.XInternAtom(display, b"NARWHAL_TEST", 0)
# The crash: X_ChangeProperty on a window that no longer exists -> BadWindow.
lib.XChangeProperty(display, 0x7fffffe, atom, atom, 8, 0, b"x", 1)
lib.XSync(display, 0)
print("SURVIVED", len(x11.recent), x11.recent[-1][1:3] if x11.recent else None, flush=True)
'''


def child(mode):
    return subprocess.run([sys.executable, "-c", CHILD, ROOT, mode], capture_output=True,
                          text=True, timeout=60)


def main():
    if not os.environ.get("DISPLAY"):
        print("SKIP: no X display for the X error regression")
        return 0
    failures = []
    handled = child("install")
    ok = handled.returncode == 0 and "SURVIVED 1 (3, 18)" in handled.stdout
    print(("PASS" if ok else "FAIL") + ": a BadWindow on X_ChangeProperty is logged and survived",
          handled.stdout.strip(), handled.stderr.strip()[-200:])
    if not ok:
        failures.append("handled")
    if "--mutations" in sys.argv:
        default = child("default")
        red = default.returncode != 0 and "SURVIVED" not in default.stdout
        print(("RED verified" if red else "FAIL: mutation survived")
              + ": without the handler Xlib's default handler exits the process",
              f"exit {default.returncode}")
        if not red:
            failures.append("mutation")
    print("FAILED" if failures else "OK: X error handler")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
