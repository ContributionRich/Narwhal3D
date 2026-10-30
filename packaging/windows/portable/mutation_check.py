#!/usr/bin/env python3
"""Compile negative controls in disposable external copies, never product sources.

Python only orchestrates Rust tests and receipts. Run inside the admitted heavy
build scope/lock with --evidence-dir and --target-dir outside the source checkout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

# Each removed guard must produce a Rust assertion failure, not a compiler error.
MUTATIONS = (
    ("identity", "if &manifest.identity != expected {", "if false {"),
    ("schema", "if manifest.schema != 1 {", "if false {"),
    ("platform", 'if manifest.platform != "windows-x86_64" {', "if false {"),
    # Both checks reject '..'; remove both protections for this negative control.
    ("traversal", '|| component == ".."\n            || component.ends_with([\'.\', \' \'])',
     '|| (component != ".." && component.ends_with([\'.\', \' \']))'),
    ("device_alias", ".trim_end_matches(' ')", ".trim_end_matches('\\t')"),
    ("case_alias", "names.insert(file.path.to_ascii_lowercase())", "names.insert(file.path.clone())"),
    ("parent_collision", "if names.contains(&name[..end]) {", "if false {"),
    ("file_limit", "if file.size > limits.file_bytes {", "if false {"),
    ("expanded_limit", "if expanded > limits.expanded_bytes {", "if false {"),
    ("disk", "if free_bytes < required_free_bytes {", "if false {"),
    ("entrypoint", "file.path == required && file.size > 0", "file.path == required"),
    ("digest", "if !hex(&file.sha256, 64) {", "if false {"),
    ("overflow", ".checked_add(file.size)\n            .ok_or(Error::ArithmeticOverflow)?",
     ".wrapping_add(file.size)"),
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cargo", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--target-dir", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    evidence = args.evidence_dir.resolve()
    target = args.target_dir.resolve()
    # Never nest a scratch checkout/target inside either the crate or project.
    project = root.parents[2]
    for path in (evidence, target):
        if path == project or project in path.parents:
            raise SystemExit("mutation scratch/evidence must be outside the project")
    if not evidence.is_dir():
        raise SystemExit("create the explicit external evidence directory before running")
    original = (root / "src/lib.rs").read_bytes()
    receipt = {"source_sha256": hashlib.sha256(original).hexdigest(), "mutations": []}
    with tempfile.TemporaryDirectory(prefix="payload-mutations-", dir=evidence) as temporary:
        crate = Path(temporary) / "crate"
        (crate / "src").mkdir(parents=True)
        for name in ("Cargo.toml", "Cargo.lock", "build.rs"):
            shutil.copyfile(root / name, crate / name)
        originals = {p.name: p.read_bytes() for p in (root / "src").glob("*.rs")}
        for name, data in originals.items():
            (crate / "src" / name).write_bytes(data)
        source = crate / "src/lib.rs"
        command = [str(args.cargo), "test", "--manifest-path", str(crate / "Cargo.toml"),
                   "--target-dir", str(target), "--offline", "--locked", "--jobs", "1"]
        receipt["command"] = command
        for name, old, new in MUTATIONS:
            text = original.decode("utf-8")
            if text.count(old) != 1:
                raise SystemExit(f"mutation anchor is not unique: {name}")
            source.write_text(text.replace(old, new), encoding="utf-8")
            result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=60)
            (evidence / f"mutation-{name}.log").write_text(result.stdout, encoding="utf-8")
            if result.returncode != 101 or "test result: FAILED." not in result.stdout:
                raise SystemExit(f"mutation {name} did not yield an observed test failure; see log")
            receipt["mutations"].append({"name": name, "exit": result.returncode,
                                         "observed_test_failure": True})
            print(f"RED: {name}", flush=True)
        source.write_bytes(original)
        module_mutations = (
            ("member_digest", "payload.rs", "if hex(&digest(input, record.size)?) != record.sha256 {", "if false {"),
            ("pack_output_inside", "pack.rs", "if fs::canonicalize(parent)?.starts_with(fs::canonicalize(root)?) {", "if false {"),
            ("pack_signed_stub", "pack.rs", "if bytes[pe + 24 + 144..pe + 24 + 152].iter().any(|b| *b != 0) {", "if false {"),
            ("update_version", "update.rs", ".is_some_and(|(n, o)| n > o)", ".is_some_and(|(n, o)| n >= o)"),
            ("update_copy_overwrite", "update.rs", ".create_new(true)", ".create(true)"),
            ("archive_digest", "payload.rs", "if digest(payload.segment(), length)?[..] != footer[80..112] {", "if false {"),
            ("zip_members", "payload.rs", "let _ = payload.archive(limits)?;", "// mutation: bypass ZIP metadata admission"),
            ("cache_integrity", "payload.rs", "pub fn verify_tree(&self, root: &Path) -> io::Result<()> {", "pub fn verify_tree(&self, root: &Path) -> io::Result<()> { return Ok(());"),
            ("active_cleanup", "cache.rs", "if lock.try_lock_exclusive().is_err() {", "if false {"),
            ("cold_exclusive_queue", "cache.rs", "match lock.try_lock_exclusive() {", "match lock.lock_exclusive() {"),
            ("lock_links", "cache.rs", "fn lock(&self, key: &str) -> io::Result<File> {",
             'fn lock(&self, key: &str) -> io::Result<File> { return OpenOptions::new().read(true).write(true).create(true).truncate(false).open(self.root.join(format!("{key}.lock")));'),
        )
        for name, filename, old, new in module_mutations:
            text = originals[filename].decode("utf-8")
            if text.count(old) != (2 if name == "update_copy_overwrite" else 1):
                raise SystemExit(f"module mutation anchor not unique: {name}")
            mutated = crate / "src" / filename
            mutated.write_text(text.replace(old, new, 1), encoding="utf-8")
            result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=60)
            (evidence / f"mutation-{name}.log").write_text(result.stdout, encoding="utf-8")
            mutated.write_bytes(originals[filename])
            if result.returncode != 101 or "test result: FAILED." not in result.stdout:
                raise SystemExit(f"module mutation {name} did not fail its test; see log")
            receipt["mutations"].append({"name": name, "exit": result.returncode,
                                         "observed_test_failure": True})
            print(f"RED: {name}", flush=True)
        result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=60)
        (evidence / "restored-tests.log").write_text(result.stdout, encoding="utf-8")
        if result.returncode or "test result: ok. 27 passed; 0 failed;" not in result.stdout:
            raise SystemExit("restored source did not pass; see restored-tests.log")
        print("GREEN: restored 27 tests", flush=True)
    if (root / "src/lib.rs").read_bytes() != original:
        raise SystemExit("source changed during the proof; receipt is invalid")
    receipt["restored_tests"] = 27
    (evidence / "mutation-receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
