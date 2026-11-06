#!/usr/bin/env python3
"""scripts/run_suite.sh gives one suite the environment ./run_tests.sh gives it.

Run:
  tools/blender-patched/5.2/python/bin/python3.13 spike/test_run_suite.py

Pinned:
  * every variable run_tests.sh exports before its suite loops is exported by
    run_suite.sh too, so a suite cannot pass in the gate and fail alone (or back);
  * a suite listed in run_tests.sh's BLENDER_SUITES runs in headless Blender, any
    other in the runtime's Python -- checked with the script's own lookup;
  * a missing suite and a missing argument are refused with exit 2.
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "run_suite.sh")

checks = 0
failures = []


def check(cond, label):
    global checks
    checks += 1
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}")
        failures.append(label)


with open(SCRIPT, encoding="utf-8") as fh:
    script = fh.read()
with open(os.path.join(ROOT, "run_tests.sh"), encoding="utf-8") as fh:
    run_tests = fh.read()

# The suite environment: what run_tests.sh exports before its first suite loop.
head = run_tests[:run_tests.index('for suite in "${PURE_SUITES[@]}"')]
gate_vars = set(re.findall(r"^\s*export (\w+)=", head, re.M))
script_vars = set(re.findall(r"^\s*export (\w+)=", script, re.M))
script_vars |= set(re.findall(r'"(NARWHAL_\w+)=native/', script))   # the CDR loop

print("the environment matches run_tests.sh")
check(gate_vars, f"run_tests.sh exports {sorted(gate_vars)}")
check(gate_vars <= script_vars,
      f"run_suite.sh exports every one of them (missing {sorted(gate_vars - script_vars)})")
check(os.access(SCRIPT, os.X_OK), "scripts/run_suite.sh is executable")

print()
print("Blender suites are recognised the way run_tests.sh lists them")
block = re.search(r"^BLENDER_SUITES=\(\n(.*?)^\)", run_tests, re.M | re.S).group(1)
blender_suites = [ln.strip() for ln in block.splitlines() if ln.strip()]
pure_block = re.search(r"^PURE_SUITES=\(\n(.*?)^\)", run_tests, re.M | re.S).group(1)
pure_suites = [ln.strip() for ln in pure_block.splitlines() if ln.strip()]
lookup = script[script.index("in_blender_suites() {"):script.index("if in_blender_suites")]


def is_blender(suite):
    out = subprocess.run(["bash", "-c", lookup + f'in_blender_suites "{suite}"'],
                         cwd=ROOT, capture_output=True, text=True)
    return out.returncode == 0


check(blender_suites and all(is_blender(s) for s in blender_suites[:3] + blender_suites[-2:]),
      f"BLENDER_SUITES entries run in Blender ({blender_suites[:3]} ... {blender_suites[-2:]})")
check(pure_suites and not any(is_blender(s) for s in pure_suites[:3] + pure_suites[-2:]),
      "PURE_SUITES entries run in the runtime's Python")
check(not is_blender("spike/test_interpolate"), "a prefix of a Blender suite does not match")

print()
print("bad arguments are refused")
for args, label in (([], "no suite"), (["spike/no_such_suite.py"], "a missing suite")):
    out = subprocess.run(["bash", SCRIPT, *args], cwd=ROOT, capture_output=True, text=True)
    check(out.returncode == 2 and out.stderr.strip(),
          f"{label}: exit 2 with a message ({out.returncode}, {out.stderr.strip()!r})")

print()
print("=" * 60)
if failures:
    print(f"{len(failures)} of {checks} CHECKS FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print(f"RUN SUITE GUARD PASSED ({checks} checks)")
