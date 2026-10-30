#!/usr/bin/env python3
"""Native proof orchestration only; bounded workers and their containment are Rust."""
from pathlib import Path
import subprocess
import tempfile

source = Path(__file__).with_name("probe.rs").read_text()
sealed = '"(version 1)(allow default)(deny process-fork)(deny process-exec)"'
mutations = (
    ("exec", sealed, '"(version 1)(allow default)(deny process-fork)"', "sealed_denies_exec"),
    ("fork", sealed, '"(version 1)(allow default)(deny process-exec)"', "sealed_denies_fork"),
    ("memory", "let code = unsafe { set(&mut attr, 0x000c, 0, limit, limit) };",
     "let code = 0;", "sealed_over_limit"),
)
with tempfile.TemporaryDirectory(prefix="narwhal-mac-proof-") as directory:
    root = Path(directory)
    for name, old, new, case in mutations:
        assert source.count(old) == 1, f"ambiguous mutation: {name}"
        path = root / f"{name}.rs"
        binary = root / name
        path.write_text(source.replace(old, new))
        subprocess.run(["rustc", "--edition=2021", "-O", str(path), "-o", str(binary)],
                       check=True, timeout=60)
        result = subprocess.run([str(binary), "--sealed-suite"], text=True,
                                capture_output=True, timeout=35)
        assert result.returncode == 101, (name, result.returncode, result.stdout, result.stderr)
        assert any(line.startswith(case + ":") and "passed=false" in line
                   for line in result.stdout.splitlines()), (name, result.stdout)
        print(f"EXPECTED RED: {name} guard removed; {case} failed", flush=True)
print("All three removed guards failed their intended native assertions.")
