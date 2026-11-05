#!/usr/bin/env python3
"""scripts/fast_checks.sh runs only what works after bootstrap alone.

Run:
  tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_fast_checks.py

The fast checks are for a clone that has run scripts/bootstrap_dev_linux.sh but
not scripts/setup_dev_native.sh, so they must not need the patched runtime or
the document observer / application core. Pinned:

  * the script uses the official bootstrapped runtime, never the patched one,
    and exports no application-core or observer library;
  * every listed suite exists, is not a Blender-hosted suite in run_tests.sh,
    builds no document (NarwhalModel / NarwhalContext start the native document
    session the observer belongs to), reads no application-core variable and
    opens no patched-runtime path;
  * the script fails when a step fails.
"""
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "fast_checks.sh")

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


def bash_array(text, name):
    match = re.search(rf"^{name}=\(\n(.*?)^\)", text, re.M | re.S)
    if match is None:
        return None
    return [line.strip() for line in match.group(1).splitlines()
            if line.strip() and not line.strip().startswith("#")]


with open(SCRIPT, encoding="utf-8") as fh:
    script = fh.read()
with open(os.path.join(ROOT, "run_tests.sh"), encoding="utf-8") as fh:
    run_tests = fh.read()

suites = bash_array(script, "FAST_SUITES")
blender_suites = set(bash_array(run_tests, "BLENDER_SUITES") or [])

print("the script itself")
check(os.access(SCRIPT, os.X_OK), "scripts/fast_checks.sh is executable")
check(suites, f"FAST_SUITES is a non-empty list ({len(suites or [])} suites)")
PATCHED_DIR = "blender-" + "patched"
check(PATCHED_DIR not in script, "it never names the patched runtime")
check('RUNTIME_ROOT="$ROOT/tools/blender-5.2.2-linux-x64"' in script,
      "it uses the bootstrapped official runtime")
for var in ("NARWHAL_APPLICATION_CORE", "NARWHAL_HISTORY_CORE", "document-observer"):
    check(var not in script, f"it does not reference {var}")
check("export NARWHAL_PURE_CORE=" in script and "--release --locked" in script,
      "it builds pure-core with --release --locked and exports NARWHAL_PURE_CORE")
check('export PATH="${CARGO_HOME:-$HOME/.cargo}/bin:$PATH"' in script,
      "it puts rustup's cargo on PATH itself")
check("spike/test_fast_checks.py" in (suites or []), "it runs this guard")

# Building a document starts the native document session the observer feeds.
NEEDS_DOCUMENT = re.compile(r"\bNarwhal(Model|Context)\s*\(")
# Reading the application-core/history variables, or a path into the patched runtime
# or the staged observer. Policy suites that only quote these names as data pass.
PATCHED = re.compile(
    r"environ(?:\.get)?\s*[\[(]\s*['\"]NARWHAL_(?:APPLICATION|HISTORY)_CORE"
    r"|['\"/]" + PATCHED_DIR + r"['\"/]"
    r"|artifacts/document-" + "observer")

print()
print("every listed suite runs without the observer or the patched runtime")
for suite in suites or []:
    path = os.path.join(ROOT, suite)
    exists = os.path.isfile(path)
    check(exists, f"{suite} exists")
    if not exists:
        continue
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    check(suite not in blender_suites, f"{suite} is not a Blender-hosted suite")
    check(not NEEDS_DOCUMENT.search(source), f"{suite} builds no NarwhalModel/NarwhalContext")
    check(not PATCHED.search(source), f"{suite} names no patched runtime or observer")

print()
print("a failing step makes the script exit non-zero")
with tempfile.TemporaryDirectory() as tmp:
    # Re-run only the step() helper and its summary on one failing and one
    # passing command; the real script's steps need the full checkout.
    start = script.index("failed=()")
    stop = script.index('step "pure-core release build"')
    tail = script[script.index("printf '\\n'\nif ((${#failed[@]}))"):]
    probe = os.path.join(tmp, "probe.sh")
    for label, command, want in (("failing", "false", 1), ("passing", "true", 0)):
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("set -uo pipefail\n" + script[start:stop]
                     + f'step "{label}" {command}\n' + tail)
        out = subprocess.run(["bash", probe], capture_output=True, text=True)
        check(out.returncode == want,
              f"a {label} step exits {want} (got {out.returncode})")

print()
print("=" * 60)
if failures:
    print(f"{len(failures)} of {checks} CHECKS FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print(f"FAST CHECKS GUARD PASSED ({checks} checks)")
