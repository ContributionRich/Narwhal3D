#!/usr/bin/env python3
"""Build and acceptance-test the bundled, supervised CDR v16-v18 reader."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT / "native" / "cdr_multiversion"
RUST_TESTS = subprocess.run(
    ["cargo", "+1.95.0", "test", "--locked", "--offline"],
    cwd=CRATE, text=True, capture_output=True)
BUILD = subprocess.run(
    ["cargo", "+1.95.0", "build", "--release", "--locked", "--offline"],
    cwd=CRATE, text=True, capture_output=True)
HELPER = CRATE / "target" / "release" / (
    "narwhal-cdr-multiversion.exe" if sys.platform.startswith("win")
    else "narwhal-cdr-multiversion")

pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg
embedded = importlib.import_module("narwhal3d.embedded_cdr")

checks = 0


def check(condition, label):
    global checks
    checks += 1
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        raise AssertionError(label)


check(RUST_TESTS.returncode == 0,
      "Rust helper unit tests generate/read v16-v18 and reject inner-RIFF truncation")
check(BUILD.returncode == 0 and HELPER.is_file(),
      "bundled multi-version helper builds from tracked vendored source")
protocol = subprocess.run([str(HELPER), "--version"], text=True, capture_output=True, timeout=30)
check(protocol.returncode == 0 and protocol.stdout.strip()
      == "narwhal-cdr-multiversion 1 tracedraw 821ba154 read 16-18 write 17",
      "bundled helper exposes the exact release capability protocol")
prior = os.environ.get("NARWHAL_CDR_MULTIVERSION")
os.environ["NARWHAL_CDR_MULTIVERSION"] = str(HELPER)
try:
    generator = ROOT / "runtime" / "linux-x86_64" / "bin" / "tracedraw-cli"
    if generator.is_file():
        with tempfile.TemporaryDirectory(prefix="narwhal_embedded_cdr_test_") as temporary:
            work = Path(temporary)
            svg = work / "source.svg"
            svg.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="80mm" '
                'viewBox="0 0 100 80"><path d="M10 20 C20 35 30 5 40 20" '
                'fill="none" stroke="#f00" stroke-width="0.25"/></svg>',
                encoding="utf-8")
            packages = {}
            for version in (15, 16, 17, 18, 19):
                target = work / f"v{version}.cdr"
                environment = os.environ.copy()
                environment["TRACEDRAW_CDR_VERSION"] = str(version)
                result = subprocess.run(
                    [str(generator), "convert", str(svg), str(target)],
                    env=environment, text=True, capture_output=True, timeout=120)
                check(result.returncode == 0 and target.is_file(),
                      f"fixture generator produced CDR v{version}")
                packages[version] = target

            values = [embedded.decode(str(packages[version])) for version in (16, 17, 18)]
            check([value["version"] for value in values] == [16, 17, 18]
                  and all(value["report"]["pages"] == 1
                          and value["report"]["shapes"] >= 1 for value in values),
                  "supervised helper decodes CDR v16/v17/v18 without an external runtime")
            for version in (15, 19):
                try:
                    embedded.decode(str(packages[version]))
                except embedded.EmbeddedCDRUnsupported:
                    refused = True
                else:
                    refused = False
                check(refused, f"helper classifies CDR v{version} as outside its embedded range")
            check(embedded.version_hint(str(packages[19])) == 19,
                  "bounded metadata hint identifies an external-fallback generation")

            written = work / "written-v17.cdr"
            strokes = [types.SimpleNamespace(
                beziers=(((10.0, 20.0), (20.0, 35.0), (30.0, 5.0), (40.0, 20.0)),),
                closed=False, rgb=(1.0, 0.0, 0.0), width_mm=0.25, layer="Cut")]
            embedded.write_v17(str(written), strokes, 100.0, 80.0, "Written", hairline=True)
            reopened = embedded.decode(str(written))
            shape = reopened["document"]["pages"][0]["layers"][0]["shapes"][0]
            check(reopened["version"] == 17 and reopened["report"]["shapes"] == 1
                  and shape["stroke"]["width"] == 0.0762
                  and shape["stroke"]["color"] == {"model": "rgb", "r": 1.0,
                                                     "g": 0.0, "b": 0.0},
                  "bundled Rust writer emits/reopens v17 layers, RGB, and Hairline")

            damaged = work / "damaged-v18.cdr"
            with zipfile.ZipFile(packages[18]) as source, zipfile.ZipFile(damaged, "w") as output:
                for info in source.infolist():
                    data = source.read(info.filename)
                    if info.filename == "content/root.dat":
                        data = data[:-1]
                    output.writestr(info, data)
            try:
                embedded.decode(str(damaged))
            except embedded.EmbeddedCDRError as exc:
                malformed = not isinstance(exc, embedded.EmbeddedCDRUnsupported)
            else:
                malformed = False
            check(malformed, "valid ZIP with truncated inner RIFF is classified malformed")
    else:
        print("  SKIP external fixture generator; unconditional Rust tests cover generated versions")

    saved = os.environ.pop("NARWHAL_CDR_MULTIVERSION", None)
    try:
        embedded._executable = lambda: None
        try:
            embedded.decode(__file__)
        except embedded.EmbeddedCDRUnavailable:
            unavailable = True
        else:
            unavailable = False
        check(unavailable, "source checkouts without the bundled helper fail explicitly")
    finally:
        if saved is not None:
            os.environ["NARWHAL_CDR_MULTIVERSION"] = saved
finally:
    if prior is None:
        os.environ.pop("NARWHAL_CDR_MULTIVERSION", None)
    else:
        os.environ["NARWHAL_CDR_MULTIVERSION"] = prior

print(f"\n{checks}/{checks} embedded CDR checks passed")
