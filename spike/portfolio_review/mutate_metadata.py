#!/usr/bin/env python3
"""Compile deliberate bridge failures in an external scratch crate, not live sources."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess

p = argparse.ArgumentParser()
p.add_argument("--work", type=Path, required=True)
a = p.parse_args()
root = Path(__file__).resolve().parents[2]
work = a.work.resolve()
if work == root or root in work.parents:
    raise SystemExit("mutation output must be outside the project")
work.mkdir(parents=True, exist_ok=True)
crate = work / "crate"
shutil.copytree(root / "native/pure_core", crate, ignore=shutil.ignore_patterns("target"))
path = crate / "src/metadata_ffi.rs"
original = path.read_text()
mutations = [
    ("exact_flag", original.replace("(u32::from(exact) << 8)", "0")),
    ("short_status", original.replace("return 1;", "return -1;")),
]
text, count = re.subn(r'let Ok\(text\) = std::str::from_utf8\(bytes\) else\s*\{\s*return -1;\s*\};',
                      'let text = std::str::from_utf8(bytes).unwrap_or("1");', original)
assert count == 1
mutations.append(("utf8", text))
env = dict(os.environ, CARGO_TARGET_DIR=str(work / "target"), CARGO_BUILD_JOBS="2")
command = ["cargo", "test", "--release", "--locked", "--offline", "--manifest-path", str(crate / "Cargo.toml"), "metadata_ffi"]
try:
    for name, text in mutations:
        assert text != original, name
        path.write_text(text)
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=120)
        log = result.stdout + result.stderr
        (work / (name + ".log")).write_text(log)
        assert result.returncode == 101 and "test result: FAILED" in log, (name, log[-3000:])
        print("EXPECTED RED:", name, flush=True)
finally:
    path.write_text(original)
result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=120)
(work / "restored.log").write_text(result.stdout + result.stderr)
assert result.returncode == 0, result.stderr[-3000:]
print("Restored metadata bridge GREEN", flush=True)
