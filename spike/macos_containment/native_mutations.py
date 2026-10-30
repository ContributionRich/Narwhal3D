#!/usr/bin/env python3
"""Compile broken native policies in scratch copies; exercise the real supervisor."""
from pathlib import Path
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

p = argparse.ArgumentParser()
p.add_argument("--target", required=True, type=Path)
p.add_argument("--fixture", required=True, type=Path)
a = p.parse_args()
root = Path(__file__).resolve().parents[2]
crate = root / "native/converter_supervisor"
source = (crate / "src/lib.rs").read_text()
profile = '"(version 1)(allow default)(deny process-fork)(deny process-exec)"'
cases = (("memory", "set(&mut attr, 0x000c, 0, limit as i32, limit as i32)", "0"),
         ("fork", profile, '"(version 1)(allow default)(deny process-exec)"'),
         ("exec", profile, '"(version 1)(allow default)(deny process-fork)"'))
env = dict(os.environ, CARGO_TARGET_DIR=str(a.target.resolve()), CARGO_BUILD_JOBS="2")
def build(manifest):
    subprocess.run(["cargo", "+1.95.0", "build", "--release", "--locked", "--manifest-path", str(manifest)], env=env, check=True, timeout=120)
def check(mutation=None):
    command = [sys.executable, str(Path(__file__).with_name("test_supervisor.py")),
               "--bin-dir", str(a.target / "release"), "--fixture", str(a.fixture)]
    if mutation:
        command += ["--mutation", mutation]
    subprocess.run(command, check=True, timeout=60)
with tempfile.TemporaryDirectory(prefix="nw-native-mutations-") as temporary:
    copy = Path(temporary) / "crate"
    shutil.copytree(crate, copy, ignore=shutil.ignore_patterns("target", "__pycache__"))
    for name, old, new in cases:
        assert source.count(old) == 1, name
        (copy / "src/lib.rs").write_text(source.replace(old, new))
        build(copy / "Cargo.toml")
        check(name)
build(crate / "Cargo.toml")
check()
print("Restored native policy GREEN after three observed guard failures.")
