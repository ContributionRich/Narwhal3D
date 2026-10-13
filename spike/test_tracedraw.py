#!/usr/bin/env python3
"""Pure tests for the local TraceDraw CDR conversion boundary."""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg

from narwhal3d import tracedraw  # noqa: E402

checks = 0


def check(condition, label):
    global checks
    checks += 1
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        raise AssertionError(label)


FAKE = r'''#!/usr/bin/env python3
import json
import os
import pathlib
import sys

command = sys.argv[1]
path = pathlib.Path(sys.argv[2])
if command == "info":
    data = path.read_bytes()
    if data.startswith(b"CDR:"):
        print("Page 1: 100.0 x 80.0 mm, 1 layer(s), 2 object(s)")
        print("version: CDR version " + data.decode().split(":", 1)[1])
    else:
        print("Page 1: 100.0 x 80.0 mm, 1 layer(s), 2 object(s)")
        print("Page 2: 60.0 x 40.0 mm, 1 layer(s), 1 object(s)")
        print("version: CDR version 17")
        print("warning: spline object not modelled")
    raise SystemExit(0)
if command == "convert":
    target = pathlib.Path(sys.argv[3])
    if target.suffix.lower() == ".tdraw":
        target.write_text(json.dumps({"pages": [
            {"name": "Page 1", "size": {"width": 100.0, "height": 80.0}, "layers": []},
            {"name": "Page 2", "size": {"width": 60.0, "height": 40.0}, "layers": []}
        ]}))
    elif target.suffix.lower() == ".cdr":
        target.write_bytes(("CDR:" + os.environ.get("TRACEDRAW_CDR_VERSION", "12")).encode())
    else:
        raise SystemExit(2)
    raise SystemExit(0)
raise SystemExit(2)
'''


with tempfile.TemporaryDirectory(prefix="narwhal_tracedraw_test_") as work:
    work = Path(work)
    runtime = work / "runtime"
    binary = runtime / "linux-x86_64" / "bin" / "tracedraw-cli"
    binary.parent.mkdir(parents=True)
    binary.write_text(FAKE, encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    source = work / "drawing.cdr"
    source.write_bytes(b"input")
    prior_runtime = os.environ.get("NARWHAL3D_RUNTIME_DIR")
    prior_strict = os.environ.get("NARWHAL3D_STRICT_RUNTIME")
    os.environ["NARWHAL3D_RUNTIME_DIR"] = str(runtime)
    os.environ["NARWHAL3D_STRICT_RUNTIME"] = "1"
    try:
        check(tracedraw.executable() == str(binary),
              "tracedraw-cli resolves through the strict bundled-runtime boundary")
        structured, notes = tracedraw.cdr_to_tdraw(str(source), str(work))
        drawing = __import__("json").loads(Path(structured).read_text())
        check(Path(structured).is_file() and len(drawing["pages"]) == 2,
              "CDR input becomes one structured local drawing with every page")
        check(any("spline object" in note for note in notes)
              and any("all 2" in note for note in notes),
              "TraceDraw warnings and multi-page placement are reported")
        svg = work / "out.svg"
        svg.write_text("<svg/>", encoding="utf-8")
        output = work / "out.cdr"
        size = tracedraw.svg_to_cdr(str(svg), str(output), 17)
        check(size == len(b"CDR:17") and output.read_bytes() == b"CDR:17",
              "SVG output becomes a verified genuine CDR v17 file")
        check(not list(work.glob("*.tmp.cdr")), "atomic CDR output leaves no temporary file")
        try:
            tracedraw.svg_to_cdr(str(svg), str(output), 23)
        except tracedraw.TraceDrawError as exc:
            bad_version = str(exc)
        else:
            bad_version = ""
        check("7 through 22" in bad_version, "unsupported CDR output versions are refused")
    finally:
        if prior_runtime is None:
            os.environ.pop("NARWHAL3D_RUNTIME_DIR", None)
        else:
            os.environ["NARWHAL3D_RUNTIME_DIR"] = prior_runtime
        if prior_strict is None:
            os.environ.pop("NARWHAL3D_STRICT_RUNTIME", None)
        else:
            os.environ["NARWHAL3D_STRICT_RUNTIME"] = prior_strict

print(f"\n{checks}/{checks} TraceDraw checks passed")
