#!/usr/bin/env python3
"""Exercise the actual Mac supervisor/policy binaries in a disposable package layout."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

p = argparse.ArgumentParser()
p.add_argument("--bin-dir", type=Path, required=True)
p.add_argument("--fixture", type=Path, required=True)
p.add_argument("--mutation", choices=("memory", "fork", "exec"))
args = p.parse_args()
records = []
with tempfile.TemporaryDirectory(prefix="narwhal-worker-") as directory:
    root = Path(directory)
    app = root / "app"
    bin_dir = app / "runtime/macos-arm64/bin"
    runtime = root / "runtime/Blender.app/Contents/MacOS/Blender"
    script = app / "narwhal3d/document_worker.py"
    bin_dir.mkdir(parents=True)
    runtime.parent.mkdir(parents=True)
    script.parent.mkdir(parents=True)
    script.write_text("# integration fixture, not production worker\n")
    supervisor = bin_dir / "narwhal-converter-supervisor"
    policy = bin_dir / "libnarwhal_worker_policy.dylib"
    for name in (supervisor.name, policy.name):
        shutil.copy2(args.bin_dir / name, bin_dir / name)
    shutil.copy2(args.fixture, runtime)
    def invoke(mode, limit=64, extra=(), program=None, worker=None):
        command = [str(supervisor), "--timeout-ms", "200" if mode == "sleep" else "5000",
                   "--max-output", "4096", "--memory-mib", str(limit),
                   "--env", f"NARWHAL_POLICY_FIXTURE={policy}",
                   "--env", "NARWHAL_MAC_DOCUMENT_LIMIT_MIB=99999", "--", str(program or runtime),
                   "-b", "--factory-startup", "--python-exit-code", "1", "--python",
                   str(worker or script), "--", mode, *extra]
        result = subprocess.run(command, capture_output=True, text=True, timeout=12)
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        records.append(dict(mode=mode, limit=limit, report=report))
        return report
    normal = invoke("normal")
    assert normal["returncode"] == 0 and normal["error"] is None and "contained-64" in normal["stdout"], normal
    large_limit = invoke("over", 768)
    assert large_limit["returncode"] == 0 and "allocation-survived" in large_limit["stdout"], large_limit
    checks = {}
    over = invoke("over")
    checks["memory"] = over["returncode"] != 0 and "allocation-survived" not in over["stdout"] and not over["timed_out"]
    for mode in ("fork", "exec"):
        report = invoke(mode)
        checks[mode] = report["returncode"] == 0 and mode + "-denied" in report["stdout"]
    if args.mutation:
        assert not checks[args.mutation], ("mutation survived", checks, records)
        assert all(value for name, value in checks.items() if name != args.mutation), checks
        print("EXPECTED RED:", args.mutation, "actual supervisor containment assertion failed")
    else:
        assert all(checks.values()), (checks, records)
        timed = invoke("sleep")
        assert timed["timed_out"] and timed["returncode"] != 0, timed
        for report in (invoke("normal", extra=("--python-expr", "untrusted")),
                       invoke("normal", program=supervisor), invoke("normal", worker=runtime)):
            assert report["returncode"] == -1 and report["error"], report
        print("PASS: actual supervisor final-image cap, sealing, timeout, environment override and target/argument refusals")
print(json.dumps(records))
