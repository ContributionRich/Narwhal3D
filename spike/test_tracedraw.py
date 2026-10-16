#!/usr/bin/env python3
"""Pure tests for the local TraceDraw CDR conversion boundary."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
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
    if path.name == "fail.svg":
        target.write_bytes(b"partial")
        print("deliberate converter failure", file=sys.stderr)
        raise SystemExit(7)
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
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    archive = binary.parent / "trace.tar.gz"
    archive.write_bytes(b"fake archive")
    archive_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (runtime / "manifest.json").write_text(json.dumps({
        "schema": 1,
        "tools": {"tracedraw-cli": {
            "executable": "tracedraw-cli", "executable_sha256": digest,
            "revision": "test", "sha256": archive_digest, "version": "test",
        }},
    }), encoding="utf-8")
    inventory = binary.parent.parent / "inventory.json"
    inventory.write_text(json.dumps({
        "schema": 1,
        "platform": "linux-x86_64",
        "tools": {"tracedraw-cli": {
            "artifact": archive.name,
            "executable": "tracedraw-cli",
            "executable_sha256": digest,
            "revision": "test",
            "sha256": archive_digest,
            "version": "test",
            "verified": True,
        }},
    }), encoding="utf-8")
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
        failure_svg = work / "fail.svg"
        failure_svg.write_text("<svg/>", encoding="utf-8")
        protected = work / "protected.cdr"
        protected.write_bytes(b"previous-good-file")
        try:
            tracedraw.svg_to_cdr(str(failure_svg), str(protected), 17)
        except tracedraw.TraceDrawError:
            pass
        else:
            raise AssertionError("deliberate TraceDraw failure was accepted")
        check(protected.read_bytes() == b"previous-good-file"
              and not list(work.glob("*.tmp.cdr")),
              "converter failure preserves the prior CDR and removes partial output")
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

real_bin = ROOT / "runtime" / "linux-x86_64" / "bin" / "tracedraw-cli"
real_manifest = json.loads((ROOT / "runtime" / "manifest.json").read_text(encoding="utf-8"))
real_details = real_manifest["tools"]["tracedraw-cli"]
real_archive = real_bin.parent / real_details["archive_name"]
if real_bin.is_file() and real_archive.is_file():
    check(hashlib.sha256(real_archive.read_bytes()).hexdigest() == real_details["sha256"]
          and hashlib.sha256(real_bin.read_bytes()).hexdigest()
              == real_details["executable_sha256"],
          "real TraceDraw archive and extracted CLI match immutable source hashes")
    with tarfile.open(real_archive, "r:gz") as bundle:
        check(sorted(member.name for member in bundle.getmembers())
              == ["tracedraw", "tracedraw-cli"],
              "pinned TraceDraw archive has exactly the measured two members")
    with tempfile.TemporaryDirectory(prefix="narwhal_tracedraw_real_") as work:
        work = Path(work)
        svg = work / "input.svg"
        svg.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="80mm" '
            'viewBox="0 0 100 80"><path d="M10 10 C20 0 30 20 40 10 Z" '
            'fill="none" stroke="#f00" stroke-width="0.25"/></svg>',
            encoding="utf-8",
        )
        for version in (15, 16, 17):
            output = work / f"v{version}.cdr"
            env = os.environ.copy()
            env["TRACEDRAW_CDR_VERSION"] = str(version)
            converted = subprocess.run(
                [str(real_bin), "convert", str(svg), str(output)], env=env,
                text=True, capture_output=True, timeout=120,
            )
            info = subprocess.run(
                [str(real_bin), "info", str(output)], text=True,
                capture_output=True, timeout=120,
            )
            check(converted.returncode == 0 and info.returncode == 0
                  and f"version: CDR version {version}" in info.stdout,
                  f"real TraceDraw writes and reopens genuine CDR v{version}")
        structured = work / "roundtrip.tdraw"
        decoded = subprocess.run(
            [str(real_bin), "convert", str(work / "v17.cdr"), str(structured)],
            text=True, capture_output=True, timeout=120,
        )
        document = json.loads(structured.read_text(encoding="utf-8"))
        check(decoded.returncode == 0 and len(document.get("pages", [])) == 1
              and len(document["pages"][0].get("layers", [])) == 1,
              "real TraceDraw structured import retains the page and layer")
    versions = subprocess.run(
        ["readelf", "--version-info", str(real_bin)], text=True,
        capture_output=True, timeout=30,
    ).stdout
    glibc = re.findall(r"GLIBC_(\d+)\.(\d+)", versions)
    check(bool(glibc) and max((int(a), int(b)) for a, b in glibc) <= (2, 35),
          "real TraceDraw CLI stays within the GLIBC 2.35 runtime ceiling")
else:
    print("  SKIP real TraceDraw certification: staged runtime is absent")

print(f"\n{checks}/{checks} TraceDraw checks passed")
