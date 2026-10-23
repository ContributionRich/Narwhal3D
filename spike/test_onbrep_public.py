"""Public-release gates for the optional exact B-rep helper adapter."""
import importlib.util
import os
import stat
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_PATH = os.path.join(ROOT, "narwhal3d", "kernel", "onbrep.py")
SOURCE_PATH = os.path.join(ROOT, "native", "brep_writer", "main.cpp")
README_PATH = os.path.join(ROOT, "native", "brep_writer", "README.md")
BUILD_PATH = os.path.join(ROOT, "scripts", "build_brep_writer_source.sh")
VERIFY_PATH = os.path.join(ROOT, "scripts", "verify_brep_writer.py")
PINNED_OPENNURBS = "eb92af3ba1806b0a34a99aba0d3bda83e3d46083"

spec = importlib.util.spec_from_file_location("onbrep_public_test", MODULE_PATH)
onbrep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(onbrep)

checks = 0
failures = []


def check(condition, label):
    global checks
    checks += 1
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


def executable(path, version):
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(f"#!/bin/sh\nprintf '%s\\n' {version!r}\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


def reset():
    onbrep._probed.clear()
    onbrep._last_error[0] = None


saved_env = {
    "NARWHAL3D_BREP_WRITER": os.environ.get("NARWHAL3D_BREP_WRITER"),
}
saved_shipped = onbrep.SHIPPED
saved_run = onbrep.subprocess.run

try:
    with tempfile.TemporaryDirectory(prefix="onbrep_public_") as tmp:
        missing = os.path.join(tmp, "missing-brep-writer")
        wrong = os.path.join(tmp, "wrong-protocol")
        explicit = os.path.join(tmp, "explicit-writer")
        shipped = os.path.join(tmp, "shipped-writer")

        executable(wrong, "brep_writer ONBREP 2")
        executable(explicit, onbrep.PROTOCOL + " opennurbs 2425133316")
        executable(shipped, onbrep.PROTOCOL)


        print("-- missing helper --")
        os.environ.pop("NARWHAL3D_BREP_WRITER", None)
        onbrep.SHIPPED = missing
        reset()
        check(onbrep.helper_path() is None and not onbrep.available(),
              "a missing helper is unavailable")
        check(not onbrep.ensure(), "ensure fails closed instead of installing anything")
        reason = onbrep.last_error() or ""
        check("NARWHAL3D_BREP_WRITER" in reason and missing in reason
              and "not included in this release" in reason,
              f"the unavailable reason gives both supported remedies ({reason!r})")

        print("\n-- protocol and candidate order --")
        os.environ["NARWHAL3D_BREP_WRITER"] = wrong
        reset()
        check(onbrep.helper_path() is None, "an explicit helper with the wrong protocol is refused")
        check(not onbrep.ensure() and onbrep.PROTOCOL in (onbrep.last_error() or ""),
              "a refused explicit helper reports the required protocol")

        os.environ["NARWHAL3D_BREP_WRITER"] = explicit
        reset()
        check(onbrep.helper_path() == explicit and onbrep.available(),
              "a protocol-compatible explicit helper is accepted")

        onbrep.SHIPPED = shipped
        reset()
        check(onbrep.helper_path() == explicit,
              "the explicit helper has priority over a compatible packaged helper")
        os.environ.pop("NARWHAL3D_BREP_WRITER")
        reset()
        check(onbrep.helper_path() == shipped,
              "the packaged platform helper is accepted when no override is set")

        print("\n-- published bounded source build --")
        check(all(os.path.isfile(path) for path in (
            SOURCE_PATH, README_PATH, BUILD_PATH, VERIFY_PATH)),
              "public source contains the helper, boundary README, and build recipe")
        with open(SOURCE_PATH, encoding="utf-8") as stream:
            helper_source = stream.read()
        with open(BUILD_PATH, encoding="utf-8") as stream:
            build_source = stream.read()
        with open(README_PATH, encoding="utf-8") as stream:
            boundary_readme = stream.read()
        check(all(token in helper_source for token in (
            "brep_writer ONBREP 1", "kMaxVertices = 100000",
            "kMaxTotalTrims = 100000;", "kMaxArchiveBytes",
            "std::isfinite", "valid_index", "reader.finish")),
              "helper parsing is protocol-pinned, bounded, finite, indexed, and complete")
        check(all(token in helper_source for token in (
            "current_end.DistanceTo(next_start) > tolerance",
            "current->SetEndPoint(next_start)",
            "false, false, false, false, false, false, false,\n                                  true")),
              "sub-tolerance trim closure recomputes parameter-space bounding boxes")
        check(PINNED_OPENNURBS in build_source
              and "artifacts/brep-writer" in build_source
              and "manylinux_2_28_x86_64@sha256:" in build_source
              and "--network none" in build_source and "--read-only" in build_source
              and "narwhal3d/kernel/bin" not in build_source,
              "developer build pins openNURBS and cannot populate packaged runtime paths")
        check("does **not** determine or resolve" in boundary_readme
              and "does not add the binary to a release" in boundary_readme,
              "source boundary refuses to claim linked-binary redistribution clearance")
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as stream:
            root_readme = stream.read()
        with open(os.path.join(ROOT, "STATUS.md"), encoding="utf-8") as stream:
            status_text = stream.read()
        check("still do **not** bundle" in root_readme
              and "Published binaries do not bundle" in status_text,
              "public product documentation keeps packaged exact-save limitations explicit")
        self_check = subprocess.run(
            [BUILD_PATH, "--self-check"], capture_output=True, text=True, timeout=20)
        verifier_check = subprocess.run(
            [sys.executable, VERIFY_PATH, "--self-check"],
            capture_output=True, text=True, timeout=20)
        check(self_check.returncode == 0 and PINNED_OPENNURBS in self_check.stdout
              and verifier_check.returncode == 0
              and "self-check: OK" in verifier_check.stdout,
              f"source/verifier self-checks pass without network/compiler "
              f"({self_check.stdout!r}; {verifier_check.stdout!r})")

        print("\n-- no implicit build fallback --")
        onbrep.SHIPPED = missing
        reset()
        check(onbrep.candidates() == [("shipped", missing)]
              and onbrep.helper_path() is None,
              "only the public packaged candidate is probed without an override")

        def subprocess_forbidden(*_args, **_kwargs):
            raise AssertionError("build() invoked a subprocess")

        onbrep.subprocess.run = subprocess_forbidden
        ok, message = onbrep.build()
        check(not ok and "not included in this release" in message,
              "build() is a fail-closed compatibility entry point")

        print("\n-- public source audit --")
        with open(MODULE_PATH, encoding="utf-8") as stream:
            source = stream.read()
        forbidden_build_calls = (
            "subprocess.run([\"make\"",
            "subprocess.run([\"g++\"",
        )
        found = [marker for marker in forbidden_build_calls if marker in source]
        check(not found, f"the runtime adapter does not invoke local compilers ({found})")
finally:
    onbrep.SHIPPED = saved_shipped
    onbrep.subprocess.run = saved_run
    for name, value in saved_env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    for failure in failures:
        print("FAIL:", failure)
    sys.exit(1)
print("PUBLIC BREP ADAPTER PASSED")
