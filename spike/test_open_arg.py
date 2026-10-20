"""Verify that `launch/open_arg.py` finds a document in the runtime argument list.

`narwhal3d.sh` forwards its arguments after the bundled runtime's ``--`` separator, and
the desktop entry passes the clicked file as ``%f``. Pure; no bundled runtime required.

Run: python3 spike/test_open_arg.py
"""
import importlib.util
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("open_arg", os.path.join(ROOT, "launch",
                                                                       "open_arg.py"))
oa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oa)

checks = 0
failures = []


def check(cond, label):
    global checks
    checks += 1
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        failures.append(label)


TMP = tempfile.mkdtemp(prefix="narwhal3d-open-arg-")
SPACED = os.path.join(TMP, "sample model.3dm")
open(SPACED, "w").write("3D Geometry File Format ")
BLENDER = ["blender", "--factory-startup", "--python", "/x/launch/bootstrap.py"]

check(oa.file_to_open(BLENDER + ["--", SPACED]) == SPACED,
      "a path WITH SPACES after `--` is the file to open")
check(oa.file_to_open(BLENDER + [SPACED]) is None,
      "no `--`: nothing is ours (Blender's own args are never taken for a file)")
check(oa.file_to_open(BLENDER + ["--"]) is None, "`--` with nothing after it: None")
check(oa.file_to_open(BLENDER + ["--", "--install", SPACED]) == SPACED,
      "an option after `--` is skipped, the file after it is still found")
check(oa.file_to_open(BLENDER + ["--", os.path.join(TMP, "missing.3dm")]) is None,
      "a MISSING file is ignored (an empty session, not a failure)")
check(oa.file_to_open(BLENDER + ["--", TMP]) is None, "a directory is not a file")
rel = os.path.relpath(SPACED)
check(oa.file_to_open(BLENDER + ["--", rel]) == SPACED,
      "a RELATIVE path comes back absolute")
check(oa.file_to_open(["blender", "--python", SPACED, "--", "nope"]) is None,
      "a file named BEFORE `--` (Blender's --python script) is not taken")
check(oa.open_line(SPACED) == f'Open "{SPACED}"',
      "the command line quotes the path, as the File menu's Open does")

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    for f_ in failures:
        print("FAIL:", f_)
    sys.exit(1)
print("OPEN-AT-LAUNCH ARG PASSED")
