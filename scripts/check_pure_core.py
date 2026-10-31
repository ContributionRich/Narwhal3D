#!/usr/bin/env python3
"""Mandatory source gate for the ``native/pure_core`` crate.

This is the fast, source-level gate that keeps the pure-core crate honest in
the working tree. It runs, in order:

1. ``cargo fmt --check`` — the committed source is rustfmt-clean.
2. ``cargo clippy --all-targets -- -D warnings`` — every lint is an error.
3. ``cargo test --offline --locked`` — the crate's own tests pass.
4. one Linux release build — the ``cdylib`` compiles for the shipped target.
5. a direct ctypes ABI probe — the built library answers the stable C ABI
   (version + a real encode) without going through the Python adapter.
6. the *dedicated* pure-core Python suites — adapters, route activation,
   compatibility paths, native-only parser and packaging contracts.

The Python suites are an **explicit manifest**, never a ``test_native_*.py``
glob: a glob would also capture the selection-core and CDR suites, which are a
different crate and a different agent's write set.

The gate builds its own Linux release library and **does not require a
committed retained artifact**. The Windows GNU ``cargo check`` runs only when
the pinned target is installed (no dual-platform reproducibility yet).

A mutation of the source must fail this gate. The self-test in
``spike/test_pure_core_gate.py`` proves that on a **temporary copied crate** —
never the live tree — and proves a skipped clippy step would fail the self-test.
"""
from __future__ import annotations

import argparse
import array
import ctypes
import json
import os
import shutil
import re
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CRATE = "native/pure_core"
ABI_VERSION = 1
RUST_TOOLCHAIN = "1.95.0"
LINUX_TARGET = "x86_64-unknown-linux-gnu"
WINDOWS_TARGET = "x86_64-pc-windows-gnu"
LIB_FILENAME = "libnarwhal_pure_core.so"

# The dedicated pure-core Python test manifest. An explicit list, deliberately
# not a glob: ``test_native_*.py`` would also match the selection-core and CDR
# suites, which belong to another crate and another agent's write set.
PURE_CORE_PYTESTS = (
    "spike/test_stl_writer.py",
    "spike/test_native_stl.py",
    "spike/test_obj_writer.py",
    "spike/test_obj_reader.py",
    "spike/test_native_obj.py",
    "spike/test_dxf_writer.py",
    "spike/test_dxf_reader.py",
    "spike/test_native_dxf.py",
    "spike/test_numparse.py",
    "spike/test_numparse_owned.py",
    "spike/test_native_numparse.py",
    "spike/test_pure_core_packaging.py",
    "spike/test_calc.py",
    "spike/test_native_calc.py",
    "spike/test_aci_colors.py",

    "spike/portfolio_review/test_metadata.py",
    "spike/test_bearing.py",
    "spike/test_native_bearing.py",
    "spike/test_coords.py",
    "spike/test_native_coords.py",
    "spike/test_macro.py",
    "spike/test_native_macro.py",
    "spike/test_macro_plan.py",
    "spike/test_native_macro_plan.py",
    "spike/test_svgpath.py",
    "spike/test_native_svgpath.py",
    "spike/test_vector_export.py",
    "spike/test_native_vector_export.py",
    "spike/test_native_command_search.py",
    "spike/test_native_coil.py",
    "spike/test_native_coil_preview.py",
    "spike/test_native_interpolate.py",
    "spike/test_native_tween.py",
)


# Additive parser ABI: core ABI 1 and all its existing exports remain unchanged.
PURE_CORE_PARSE_EXPORTS = (
    "narwhal_pure_core_parse_abi", "narwhal_pure_core_parse_v2",
)
PURE_CORE_PARSE_IDENTITY = {
    "abi": 1, "parse_abi": 2, "reply_bytes": 24,
    "parser_exports": list(PURE_CORE_PARSE_EXPORTS),
}
# Packaging orchestration only: no Python grammar/fallback. Run the real C ABI
# in a bounded child so an incompatible library cannot crash the verifier host.
PURE_CORE_PARSE_PROBE = r'''
import ctypes as c, json, sys
lib = c.CDLL(sys.argv[1])
for symbol, expected in (("narwhal_pure_core_abi_version", 1),
                         ("narwhal_pure_core_parse_abi", 2)):
    fn = getattr(lib, symbol)
    fn.argtypes = []
    fn.restype = c.c_uint32
    if fn() != expected:
        raise RuntimeError(symbol + " ABI mismatch")
class Reply(c.Structure):
    _fields_ = [("code", c.c_int32), ("reserved", c.c_uint32),
                ("value", c.c_double), ("start", c.c_uint32), ("end", c.c_uint32)]
if c.sizeof(Reply) != 24:
    raise RuntimeError("pure-core parser Reply must be 24 bytes")
parse = lib.narwhal_pure_core_parse_v2
parse.argtypes = [c.POINTER(c.c_uint32), c.c_size_t, c.POINTER(c.c_uint32),
                  c.c_size_t, c.c_uint32, c.c_uint32, c.POINTER(Reply), c.c_size_t]
parse.restype = c.c_uint32
# Independent ABI smoke witnesses, not a second implementation of the parser.
for text, model, length, code, value in (("1/2", "", 0, 0, 0.5),
                                        ("2cm", "Millimeters", 1, 0, 20.0),
                                        ("bad", "", 0, -2, 0.0)):
    points = (c.c_uint32 * len(text))(*map(ord, text))
    units = (c.c_uint32 * len(model))(*map(ord, model))
    reply = Reply(99, 99, 99.0, 99, 99)
    status = parse(points, len(text), units, len(model), length, 160,
                   c.byref(reply), c.sizeof(reply))
    if status != 0 or reply.code != code or reply.reserved != 0 or reply.value != value:
        raise RuntimeError("pure-core parser semantic probe failed: " + repr(text))
print(json.dumps({"abi": 1, "parse_abi": 2, "reply_bytes": c.sizeof(Reply),
                  "parser_exports": ["narwhal_pure_core_parse_abi", "narwhal_pure_core_parse_v2"]}, sort_keys=True))
'''


def probe_pure_core_parser(path: Path, python: Path | str = sys.executable, *, env=None) -> dict:
    """Require the additive parser ABI and real number/length/rejection behavior."""
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"missing regular pure-core parser library: {path}")
    try:
        completed = subprocess.run(
            [str(python), "-I", "-B", "-c", PURE_CORE_PARSE_PROBE, str(path.resolve())],
            env=env, text=True, capture_output=True, check=True, timeout=30)
        identity = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError(f"cannot verify pure-core parser ABI/semantics: {getattr(error, 'stderr', '') or error}") from error
    if identity != PURE_CORE_PARSE_IDENTITY:
        raise ValueError(f"pure-core parser ABI/export evidence differs: {identity!r}")
    return identity


class GateError(RuntimeError):
    """A mandatory pure-core source check failed."""


def _run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> tuple[int, str]:
    """Run *cmd* and return ``(returncode, combined_output)`` without raising."""
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
    except OSError as exc:
        return 127, f"cannot run {cmd[0]!r}: {exc}"
    return proc.returncode, proc.stdout


def _cargo(*args: str, crate: Path) -> list[str]:
    return ["cargo", f"+{RUST_TOOLCHAIN}", *args,
            "--manifest-path", str(crate / "Cargo.toml")]


def _bundled_python() -> str:
    """The CPython the app ships (matches ``run_tests.sh``); fall back to the
    interpreter running the gate. The pure-core suites need only the stdlib."""
    candidate = ROOT / "tools" / "blender-patched" / "5.2" / "python" / "bin" / "python3.13"
    return str(candidate) if candidate.is_file() else sys.executable


def _release_lib(crate: Path) -> Path:
    return (crate / "target" / LINUX_TARGET / "release" / LIB_FILENAME)


# ---------------------------------------------------------------------------
# Individual steps. Each returns a short status dict for the report.
# ---------------------------------------------------------------------------

def step_fmt(crate: Path) -> dict[str, object]:
    rc, out = _run(_cargo("fmt", "--check", crate=crate), ROOT)
    if rc != 0:
        raise GateError(f"cargo fmt --check failed:\n{out.strip()}")
    return {"status": "ok", "command": "cargo fmt --check"}


def step_clippy(crate: Path) -> dict[str, object]:
    # -D warnings is forwarded to the compiler via ``--``.
    rc, out = _run(
        _cargo("clippy", "--all-targets", "--locked", "--offline", crate=crate)
        + ["--", "-D", "warnings"],
        ROOT,
    )
    if rc != 0:
        raise GateError(f"cargo clippy -D warnings failed:\n{out.strip()}")
    # The self-test asserts this ran (a skipped clippy would not reach here).
    return {"status": "ok", "ran": True, "command": "cargo clippy -D warnings"}


def step_tests(crate: Path) -> dict[str, object]:
    rc, out = _run(_cargo("test", "--offline", "--locked", crate=crate), ROOT)
    if rc != 0:
        raise GateError(f"cargo test failed:\n{out.strip()}")
    match = re.search(r"test result: ok\.\s+(\d+) passed", out)
    if match is None:
        raise GateError("cargo test ran but reported no passing tests")
    return {"status": "ok", "passed": int(match.group(1)), "command": "cargo test"}


def step_release_build(crate: Path) -> dict[str, object]:
    rc, out = _run(
        _cargo("build", "--release", "--locked", "--offline",
               "--target", LINUX_TARGET, crate=crate),
        ROOT,
    )
    if rc != 0:
        raise GateError(f"cargo build --release (Linux) failed:\n{out.strip()}")
    lib = _release_lib(crate)
    if not lib.is_file():
        raise GateError(f"release build produced no library at {lib}")
    return {"status": "ok", "artifact": str(lib), "command": "cargo build --release"}


def step_direct_abi(lib_path: Path) -> dict[str, object]:
    """Load the built library and exercise both stable and additive C ABIs."""
    try:
        parser_identity = probe_pure_core_parser(lib_path)
    except ValueError as exc:
        raise GateError(str(exc)) from exc
    try:
        lib = ctypes.CDLL(str(lib_path))
        version = lib.narwhal_pure_core_abi_version
        version.argtypes = []
        version.restype = ctypes.c_uint32
        if int(version()) != ABI_VERSION:
            raise GateError(f"ABI version is {version()}, expected {ABI_VERSION}")

        size = lib.narwhal_pure_core_stl_size
        size.argtypes = [
            ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t, ctypes.c_ubyte,
        ]
        size.restype = ctypes.c_int64
        enc = lib.narwhal_pure_core_stl_encode
        enc.argtypes = [
            ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t, ctypes.c_ubyte,
            ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
        ]
        enc.restype = ctypes.c_int32
    except (AttributeError, OSError) as exc:
        raise GateError(f"cannot bind the pure-core ABI: {exc}") from exc

    # One binary STL facet: 84-byte header + 50-byte record.
    flat = array.array("d", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0])
    coords = (ctypes.c_double * 9).from_buffer(flat)
    name = b"Narwhal 3D"
    name_arr = (ctypes.c_ubyte * len(name))(*name)
    nbytes = int(size(coords, 1, name_arr, len(name), 1))
    if nbytes != 84 + 50:
        raise GateError(f"direct ABI size query returned {nbytes}, expected 134")
    buf = (ctypes.c_ubyte * nbytes)()
    if int(enc(coords, 1, name_arr, len(name), 1, buf, nbytes)) != 0:
        raise GateError("direct ABI encode returned a non-zero status")
    body = bytes(buf)
    if struct.unpack_from("<I", body, 80)[0] != 1:
        raise GateError("direct ABI encode: triangle count is not 1")
    if body[:80].strip().decode("latin-1") != "Narwhal 3D":
        raise GateError("direct ABI encode: header name is wrong")

    # A NaN coordinate must be a domain rejection, not a silent encode.
    bad = array.array("d", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, float("nan"), 1.0, 0.0])
    bad_ptr = (ctypes.c_double * 9).from_buffer(bad)
    if int(size(bad_ptr, 1, name_arr, len(name), 1)) >= 0:
        raise GateError("direct ABI size query accepted a NaN coordinate")
    return {"status": "ok", "abi_version": ABI_VERSION, "parser": parser_identity,
                "command": "ctypes ABI probe"}


def step_adapter(crate: Path, lib_path: Path) -> dict[str, object]:
    """Run the dedicated pure-core Python suites against the built library."""
    python = _bundled_python()
    env = {**os.environ, "NARWHAL_PURE_CORE": str(lib_path),
           "PYTHONDONTWRITEBYTECODE": "1"}
    for suite in PURE_CORE_PYTESTS:
        suite_path = ROOT / suite
        if not suite_path.is_file():
            raise GateError(f"missing pure-core suite {suite}")
        rc, out = _run([python, suite], ROOT, env)
        if rc != 0:
            raise GateError(f"pure-core suite {suite} failed:\n{out.strip()}")
    return {"status": "ok", "suites": list(PURE_CORE_PYTESTS), "python": python}


def _windows_target_installed() -> bool | None:
    """True/False when rustup can tell us; None when rustup is unavailable."""
    if not shutil.which("rustup"):
        return None
    rc, out = _run(
        ["rustup", "target", "list", "--toolchain", RUST_TOOLCHAIN, "--installed"],
        ROOT,
    )
    if rc != 0:
        return None
    return WINDOWS_TARGET in out.split()


def step_windows_check(crate: Path) -> dict[str, object]:
    installed = _windows_target_installed()
    if installed is False:
        return {"status": "skipped", "reason": "Windows GNU target not installed"}
    rc, out = _run(
        _cargo("check", "--locked", "--offline",
               "--target", WINDOWS_TARGET, crate=crate),
        ROOT,
    )
    if rc != 0:
        # If rustup could not report the target (installed is None) and the
        # failure is an "install the target" diagnostic, treat it as skipped.
        if installed is None and (
            "target not installed" in out or "component not installed" in out
        ):
            return {"status": "skipped", "reason": "Windows GNU target not installed"}
        raise GateError(f"cargo check (Windows GNU) failed:\n{out.strip()}")
    return {"status": "ok", "command": "cargo check --target x86_64-pc-windows-gnu"}


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run_gate(crate_rel: str) -> dict[str, object]:
    crate = (ROOT / crate_rel).resolve()
    if not (crate / "Cargo.toml").is_file():
        raise GateError(f"not a pure-core crate (no Cargo.toml): {crate}")
    if not shutil.which("cargo"):
        raise GateError("cargo is not on PATH; the pure-core source gate cannot run")

    steps: dict[str, object] = {}
    steps["fmt"] = step_fmt(crate)
    clippy = step_clippy(crate)
    # A skipped or absent clippy step must fail the gate: it is the only step
    # that turns lints into errors, so a silent skip would let a regression
    # through. The self-test exercises this by stubbing the step to "skipped".
    if clippy.get("status") != "ok" or clippy.get("ran") is not True:
        raise GateError("clippy did not run with -D warnings; a skipped clippy "
                        "must fail the gate")
    steps["clippy"] = clippy
    steps["tests"] = step_tests(crate)
    steps["release_build"] = step_release_build(crate)
    lib = _release_lib(crate)
    steps["direct_abi"] = step_direct_abi(lib)
    steps["adapter"] = step_adapter(crate, lib)
    steps["windows_check"] = step_windows_check(crate)

    report = {
        "gate": "pure-core-source",
        "crate": str(crate),
        "abi_version": ABI_VERSION,
        "rust_toolchain": RUST_TOOLCHAIN,
        "python_manifest": list(PURE_CORE_PYTESTS),
        "steps": steps,
        "ok": True,
    }
    return report


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crate", default=DEFAULT_CRATE,
                        help="path to the pure-core crate (default: %(default)s)")
    parser.add_argument("--json", action="store_true",
                        help="emit a machine-readable JSON report")
    args = parser.parse_args(argv)

    try:
        report = run_gate(args.crate)
    except GateError as exc:
        if args.json:
            print(json.dumps({"gate": "pure-core-source", "ok": False,
                              "error": str(exc)}, indent=2))
        else:
            print(f"pure-core source gate FAILED:\n{exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"pure-core source gate: OK (crate {report['crate']})")
        for name in ("fmt", "clippy", "tests", "release_build",
                     "direct_abi", "adapter", "windows_check"):
            step = report["steps"][name]
            detail = step.get("passed", step.get("suites", step.get("reason", "")))
            print(f"  [{name}] {step['status']}"
                  + (f"  -- {detail}" if detail else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
