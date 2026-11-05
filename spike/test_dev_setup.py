#!/usr/bin/env python3
"""Fresh-clone development setup: runtime selection, header lookup and pinned inputs.

A clean clone has only the bootstrapped official runtime until scripts/setup_dev_native.sh adds the
patched runtime, CPython headers, the document observer and the other native pieces. These checks
keep that path working without downloads: they exercise the real selection code on synthetic trees.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spike"))
sys.path.insert(0, str(ROOT / "scripts"))
import runtime_paths  # noqa: E402

checks = 0
failures = []


def check(condition, label):
    global checks
    checks += 1
    print(("  ok   " if condition else "  FAIL ") + label)
    if not condition:
        failures.append(label)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def runtime(root, name):
    directory = root / "tools" / name
    (directory / "5.2/python/bin").mkdir(parents=True)
    for path in (directory / "blender", directory / "5.2/python/bin/python3.13"):
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
    return directory


saved = os.environ.pop("NARWHAL_RUNTIME_ROOT", None)
try:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        official = runtime(root, "blender-5.2.2-linux-x64")
        check(runtime_paths.runtime_root(root) == official,
              "a fresh clone (official runtime only) selects the official runtime")
        patched = runtime(root, "blender-patched")
        check(runtime_paths.runtime_root(root) == patched and runtime_paths.is_patched(root),
              "the patched runtime is preferred once it exists")
        os.environ["NARWHAL_RUNTIME_ROOT"] = str(official)
        check(runtime_paths.runtime_root(root) == official and not runtime_paths.is_patched(root),
              "NARWHAL_RUNTIME_ROOT exported by run_tests.sh wins")
        del os.environ["NARWHAL_RUNTIME_ROOT"]

        # run_tests.sh: execute its real runtime-selection block against the synthetic tree.
        text = (ROOT / "run_tests.sh").read_text(encoding="utf-8")
        block = text[text.index("# Prefer the patched runtime"):text.index('echo "runtime: $RUNTIME_ROOT"')]
        script = f'ROOT={json.dumps(str(root))}\n{block}\necho "$RUNTIME_ROOT"\n'
        chosen = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        check(chosen.returncode == 0 and chosen.stdout.strip() == str(patched),
              "run_tests.sh selects the patched runtime when present")
        (patched / "blender").unlink()
        chosen = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        check(chosen.returncode == 0 and chosen.stdout.strip() == str(official),
              "run_tests.sh falls back to the bootstrapped runtime in a fresh clone")
        for path in (official / "blender",):
            path.unlink()
        chosen = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        check(chosen.returncode == 2 and "setup_dev_native.sh" in chosen.stderr,
              "run_tests.sh names both runtimes and the setup scripts when none exists")
finally:
    if saved is not None:
        os.environ["NARWHAL_RUNTIME_ROOT"] = saved

# check_document_observer.py: header lookup prefers the headers setup_dev_native.sh made.
observer = load("check_document_observer_under_test", ROOT / "scripts/check_document_observer.py")
saved_include = os.environ.pop("NARWHAL_PYTHON_INCLUDE", None)
try:
    with tempfile.TemporaryDirectory() as temp:
        fake = Path(temp)
        observer.ROOT = fake
        made = fake / "tools/cpython-3.13-include/python3.13"
        made.mkdir(parents=True)
        check(observer.default_python_include() == made,
              "without headers the default still names the setup location (clear error later)")
        blender_src = fake / "tools/blender-src/lib/linux_x64/python/include/python3.13"
        blender_src.mkdir(parents=True)
        (blender_src / "Python.h").write_text("")
        check(observer.default_python_include() == blender_src,
              "an existing Blender-source header tree is used when setup headers are absent")
        (made / "Python.h").write_text("")
        check(observer.default_python_include() == made,
              "headers made by setup_dev_native.sh are preferred")
        os.environ["NARWHAL_PYTHON_INCLUDE"] = str(fake / "explicit")
        check(observer.default_python_include() == fake / "explicit",
              "NARWHAL_PYTHON_INCLUDE overrides every default")
finally:
    os.environ.pop("NARWHAL_PYTHON_INCLUDE", None)
    if saved_include is not None:
        os.environ["NARWHAL_PYTHON_INCLUDE"] = saved_include

# The development lock is pinned, and the fetcher's self-check refuses a changed pin.
fetcher = load("fetch_locked_under_test", ROOT / "scripts/fetch_locked.py")
fetcher.self_check()
check(True, "fetch_locked self-check accepts the committed development lock")
with tempfile.TemporaryDirectory() as temp:
    changed = json.loads(fetcher.DEV_LOCK.read_text(encoding="utf-8"))
    changed["artifacts"][0]["sha256"] = "0" * 64
    altered = Path(temp) / "dev.lock.json"
    altered.write_text(json.dumps(changed), encoding="utf-8")
    fetcher.DEV_LOCK = altered
    try:
        fetcher.self_check()
    except fetcher.PackagingError:
        refused = True
    else:
        refused = False
    check(refused, "fetch_locked self-check refuses a changed development pin")

# check_application_core.py: the default Cargo target follows XDG_CACHE_HOME (fresh clones, sandboxes).
application = load("check_application_core_under_test", ROOT / "scripts/check_application_core.py")
saved_cache = os.environ.pop("XDG_CACHE_HOME", None)
try:
    os.environ["XDG_CACHE_HOME"] = "/nonexistent/xdg-cache"
    check(application.default_target_dir() == Path("/nonexistent/xdg-cache/narwhal3d-application-core/target"),
          "the application-core default target honours XDG_CACHE_HOME")
    del os.environ["XDG_CACHE_HOME"]
    check(application.default_target_dir() == Path.home() / ".cache/narwhal3d-application-core/target",
          "without XDG_CACHE_HOME the application-core default stays under ~/.cache")
finally:
    os.environ.pop("XDG_CACHE_HOME", None)
    if saved_cache is not None:
        os.environ["XDG_CACHE_HOME"] = saved_cache

setup = (ROOT / "scripts/setup_dev_native.sh").read_text(encoding="utf-8")
lock = json.loads((ROOT / "packaging/linux-x86_64-dev.lock.json").read_text(encoding="utf-8"))
ids = {item["id"] for item in lock["artifacts"]}
check(lock.get("purpose") == "development-native-inputs-only"
      and ids == {"cpython-source", "rhino3dm-sdist", "narwhal-appimage-runtime"},
      "the development lock holds exactly the three development inputs")
check(all(name in setup for name in ids), "setup_dev_native.sh fetches every development input")
check("OCCT_REVISION=b8f597c677811d1f9f4d8a97f5ae2825c0353a42" in setup
      and 'if [[ ! -x "$PATCHED/blender" ]]' in setup,
      "setup pins the OCCT revision and never replaces an existing patched runtime")
self_check = subprocess.run([str(ROOT / "scripts/setup_dev_native.sh"), "--self-check"],
                            capture_output=True, text=True)
check(self_check.returncode == 0 and "self-check: OK" in self_check.stdout,
      "setup_dev_native.sh --self-check passes without downloads")
launcher = (ROOT / "narwhal3d.sh").read_text(encoding="utf-8")
check('dev_brep_writer="$ROOT/artifacts/brep-writer/' in launcher
      and 'if [[ -z "${NARWHAL3D_BREP_WRITER:-}" && -x "$dev_brep_writer" ]]' in launcher,
      "the launcher uses the development exact-save helper only when nothing else is configured")

# GUI suites strip NARWHAL* variables and rebuild them from the launcher's native-paths report, so
# the exact-save helper must be in that report or their documents fall back to triangulated proxies.
paths_env = {k: v for k, v in os.environ.items() if not k.startswith("NARWHAL")}
paths_env.update(NARWHAL3D_LAUNCHER_TEST="native-paths", NARWHAL3D_BREP_WRITER="/custom/brep_writer")
reported = subprocess.run([str(ROOT / "narwhal3d.sh")], cwd=ROOT, env=paths_env,
                          capture_output=True, text=True, timeout=30)
check(reported.returncode == 0
      and "NARWHAL3D_BREP_WRITER=/custom/brep_writer" in reported.stdout.splitlines(),
      "the launcher's native-paths report carries the exact-save helper to GUI suites")

print(f"\n{checks - len(failures)}/{checks} development setup checks passed")
raise SystemExit(bool(failures))
