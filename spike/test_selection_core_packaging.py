#!/usr/bin/env python3
"""Build, reproduce, verify, and mutation-test Rust selection-core artifacts."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / "tools/blender-patched/5.2/python/bin/python3.13"
BUILD = ROOT / "scripts/build_selection_core.py"
VERIFY = ROOT / "scripts/verify_selection_core.py"

spec = importlib.util.spec_from_file_location("verify_selection_core", VERIFY)
assert spec is not None and spec.loader is not None
verifier = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = verifier
spec.loader.exec_module(verifier)

checks = 0
failures: list[str] = []


def check(condition, label, detail=""):
    global checks
    checks += 1
    ok = bool(condition)
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f" ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def command(*args):
    return subprocess.run(
        [str(PYTHON), *map(str, args)], cwd=ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", check=False,
    )


self_build = command(BUILD, "--self-check")
self_verify = command(VERIFY, "--self-check")
check(self_build.returncode == 0, "builder self-check passes", self_build.stdout)
check(self_verify.returncode == 0, "verifier self-check passes", self_verify.stdout)

with tempfile.TemporaryDirectory(prefix="selection-core-acceptance-") as temporary:
    foreign = Path(temporary) / "foreign-output"
    foreign.mkdir()
    sentinel = foreign / "keep.txt"
    sentinel.write_text("not builder-owned\n", encoding="utf-8")
    refused = command(BUILD, "--output-dir", foreign)
    check(refused.returncode == 1 and sentinel.read_text(encoding="utf-8") == "not builder-owned\n",
          "builder refuses to replace an unowned existing directory", refused.stdout)

    output = Path(temporary) / "artifacts"
    built = command(
        BUILD, "--output-dir", output, "--source-date-epoch", "0", "--reproducible")
    check(built.returncode == 0, "two clean Linux/Windows builds are reproducible", built.stdout)
    if built.returncode != 0:
        print(f"\nFAIL: artifact build stopped acceptance cleanly\n{built.stdout}")
        raise SystemExit(1)
    checked = command(VERIFY, output)
    check(checked.returncode == 0, "independent artifact-directory verification passes", checked.stdout)
    manifest = json.loads((output / verifier.MANIFEST_NAME).read_text(encoding="utf-8"))
    check(manifest["rust_toolchain"] == "1.95.0" and manifest["abi"] == 1,
          "manifest pins Rust toolchain and ABI", repr(manifest))
    records = {item["platform"]: item for item in manifest["artifacts"]}
    check(set(records) == {"linux-x86_64", "windows-x86_64"},
          "manifest contains exactly both x86-64 platforms", repr(records))
    check(records["linux-x86_64"]["max_glibc"] <= "2.35"
          and records["linux-x86_64"]["abi_executed"] is True,
          "Linux artifact is executable here and within the glibc ceiling",
          repr(records["linux-x86_64"]))
    check(records["windows-x86_64"]["coff_timestamp"] == 0
          and records["windows-x86_64"]["dependencies"] == list(verifier.WINDOWS_IMPORTS),
          "Windows artifact has deterministic timestamp and measured system imports",
          repr(records["windows-x86_64"]))
    check(all(tuple(item["exports"]) == verifier.EXPECTED_EXPORTS for item in records.values()),
          "both artifacts export exactly the ABI-1 symbol set")

    # Actual verifier mutation: corrupt a copied ELF byte and require hash/metadata failure.
    damaged = Path(temporary) / "damaged"
    shutil.copytree(output, damaged)
    linux = damaged / "linux-x86_64/libnarwhal_selection_core.so"
    original = linux.read_bytes()
    mutated = bytearray(original)
    mutated[-1] ^= 0x01
    linux.write_bytes(mutated)
    mutation_failed = False
    try:
        verifier.verify_directory(damaged)
    except verifier.VerificationError:
        mutation_failed = True
    check(mutation_failed, "binary-byte mutation makes verification fail")
    linux.write_bytes(original)
    check(verifier.verify_directory(damaged)["abi"] == 1,
          "restored artifact directory verifies again")

if failures:
    print(f"\nFAIL: {len(failures)}/{checks} checks failed: {failures}")
    raise SystemExit(1)
print(f"\n{checks}/{checks} selection-core packaging checks passed")
