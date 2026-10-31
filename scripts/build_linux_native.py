#!/usr/bin/env python3
"""Build source-bound native helpers in the pinned portable Linux container.

Build orchestration only. Preserves prior compiler targets, uses no network, and
writes receipts after checking that all source inputs stayed unchanged.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from check_pure_core import probe_pure_core_parser

IMAGE = "quay.io/pypa/manylinux_2_28_x86_64@sha256:1e7b8460ad8fb3fb0fbe1cd818845159ac896b4fd17452d56e892c291f3b97a8"
COMPONENTS = {
    "welcome": ("narwhal-welcome",),
    "pure_core": ("libnarwhal_pure_core.so",),
    "geometry_core": ("libnarwhal_geometry_core.so",),
    "platform_core": ("libnarwhal_platform_core.so",),
    "selection_core": ("libnarwhal_selection_core.so",),
    "cdr_core": ("libnarwhal_cdr_core.so",),
    "cdr_multiversion": ("narwhal-cdr-multiversion",),
    "document_core": ("narwhal-document-core",),
    "application_core": ("libnarwhal_application_core.so",),
    "ui_core": ("libnarwhal_ui_core.so",),
    "converter_supervisor": ("narwhal-converter-supervisor",),
    "automation_core": ("libnarwhal_automation.so", "narwhal-automation"),
}


# Packaging contract from native/ui_core/narwhal_ui_core.h. Python only loads the
# C ABI and checks receipts; all editor and popup behavior remains in Rust.
UI_CORE_EXPORTS = (
    "narwhal_editor_abi", "narwhal_editor_close", "narwhal_editor_edit", "narwhal_editor_read",
    "narwhal_popup_abi", "narwhal_popup_apply", "narwhal_popup_destroy",
    "narwhal_popup_guards", "narwhal_popup_hit", "narwhal_popup_panel", "narwhal_popup_rows",
    "narwhal_transcript_abi", "narwhal_transcript_apply", "narwhal_transcript_capabilities",
    "narwhal_transcript_close", "narwhal_transcript_read", "narwhal_transcript_release",
    "narwhal_transcript_stats",
)
UI_CORE_IDENTITY = {"editor_abi": 1, "popup_abi": 1, "state_bytes": 88,
                    "transcript_abi": 1, "transcript_capabilities": 7,
                    "transcript_request_bytes": 40, "transcript_reply_bytes": 56,
                    "exports": list(UI_CORE_EXPORTS)}
UI_CORE_SOURCE = "ui-core-source.json"
UI_CORE_REQUIRED_SOURCE = (
    "Cargo.toml", "Cargo.lock", "LICENSE", "README.md", "POPUP.md", "TRANSCRIPT.md",
    "narwhal_ui_core.h", "gen_unicode.py", "transcript_unicode.py",
    "src/lib.rs", "src/editor.rs", "src/unicode16.rs", "src/allocation_tests.rs",
    "src/layout_algorithm.rs", "src/layout_contract.rs", "src/popup.rs", "src/popup_ffi.rs",
    "src/popup_tests.rs", "src/transcript.rs", "src/transcript_ffi.rs", "src/transcript_tests.rs",
)
UI_CORE_PROBE = r'''
import ctypes as c, json, struct, sys
lib = c.CDLL(sys.argv[1])
exports = json.loads(sys.argv[2])
for name in exports:
    getattr(lib, name)
result = {"exports": exports}
for name in ("editor", "popup", "transcript"):
    fn = getattr(lib, "narwhal_" + name + "_abi")
    fn.argtypes = []
    fn.restype = c.c_uint32
    result[name + "_abi"] = int(fn())
if result["editor_abi"] != 1 or result["popup_abi"] != 1 or result["transcript_abi"] != 1:
    raise RuntimeError("ui-core editor/popup/transcript ABI mismatch")
lib.narwhal_transcript_capabilities.argtypes = []
lib.narwhal_transcript_capabilities.restype = c.c_uint64
result["transcript_capabilities"] = int(lib.narwhal_transcript_capabilities())
if result["transcript_capabilities"] != 7:
    raise RuntimeError("ui-core transcript capabilities mismatch")
class TranscriptRequest(c.Structure):
    _fields_ = [("op", c.c_uint32), ("flags", c.c_uint32), ("revision", c.c_uint64),
                ("a", c.c_int64), ("b", c.c_int64), ("c", c.c_int64)]
class TranscriptReply(c.Structure):
    _fields_ = [(name, c.c_uint64) for name in ("handle", "revision", "len", "token", "count", "bytes", "kind")]
result["transcript_request_bytes"] = c.sizeof(TranscriptRequest)
result["transcript_reply_bytes"] = c.sizeof(TranscriptReply)
if result["transcript_request_bytes"] != 40 or result["transcript_reply_bytes"] != 56:
    raise RuntimeError("ui-core transcript Request40/Reply56 layout mismatch")
lib.narwhal_transcript_apply.argtypes = [c.c_uint64, c.POINTER(TranscriptRequest), c.c_size_t,
    c.POINTER(c.c_ubyte), c.c_size_t, c.POINTER(TranscriptReply), c.c_size_t]
lib.narwhal_transcript_apply.restype = c.c_uint32
lib.narwhal_transcript_read.argtypes = [c.c_uint64, c.c_uint64, c.c_uint64,
    c.POINTER(c.c_ubyte), c.c_size_t, c.POINTER(c.c_size_t)]
lib.narwhal_transcript_read.restype = c.c_uint32
lib.narwhal_transcript_release.argtypes = [c.c_uint64, c.c_uint64]
lib.narwhal_transcript_release.restype = c.c_uint32
lib.narwhal_transcript_close.argtypes = [c.c_uint64]
lib.narwhal_transcript_close.restype = c.c_uint32
lib.narwhal_transcript_stats.argtypes = [c.POINTER(c.c_uint64), c.c_size_t]
lib.narwhal_transcript_stats.restype = c.c_uint32
class State(c.Structure):
    _fields_ = [(name, c.c_int64 if name in ("anchor", "max_chars") else c.c_uint64)
                for name in ("handle", "revision", "text_revision", "length", "caret",
                             "anchor", "max_chars", "flags", "change_start", "change_end", "inserted")]
result["state_bytes"] = c.sizeof(State)
if result["state_bytes"] != 88:
    raise RuntimeError("ui-core editor State must be 88 bytes")
lib.narwhal_editor_edit.argtypes = [c.c_uint64, c.c_uint32, c.c_int64, c.c_uint32,
                                   c.POINTER(c.c_uint32), c.c_size_t, c.POINTER(State), c.c_size_t]
lib.narwhal_editor_edit.restype = c.c_uint32
lib.narwhal_editor_read.argtypes = [c.c_uint64, c.c_uint64, c.c_size_t, c.POINTER(c.c_uint32), c.c_size_t]
lib.narwhal_editor_read.restype = c.c_uint32
lib.narwhal_editor_close.argtypes = [c.c_uint64]
lib.narwhal_editor_close.restype = c.c_uint32
state, text = State(), (c.c_uint32 * 1)(65)
if lib.narwhal_editor_edit(0, 0, 0, 0, text, 1, c.byref(state), c.sizeof(state)) != 0:
    raise RuntimeError("ui-core rejected editor State88 create")
if not state.handle:
    raise RuntimeError("ui-core returned no editor handle")
try:
    output = (c.c_uint32 * 1)()
    if (state.length != 1 or not state.revision or not state.text_revision
            or lib.narwhal_editor_read(state.handle, state.text_revision, 0, output, 1) != 0
            or output[0] != 65):
        raise RuntimeError("ui-core editor State88/read contract differs")
finally:
    if lib.narwhal_editor_close(state.handle) != 0:
        raise RuntimeError("ui-core editor close failed")
# Fixed binary witness from TRANSCRIPT.md: two raw rows, preserving NUL,
# a supplementary code point and a lone surrogate. No product adapter imports.
payload = struct.pack("<8I", 2, 3, 65, 0, 0x1f40b, 2, 0xd800, 66)
def transcript_stats(expected):
    stats = (c.c_uint64 * 4)()
    if lib.narwhal_transcript_stats(stats, 4) != 0 or tuple(stats) != expected:
        raise RuntimeError("ui-core transcript ownership/snapshot stats differ")
def transcript_call(handle, request, data=b""):
    output = TranscriptReply()
    input = (c.c_ubyte * len(data)).from_buffer_copy(data) if data else None
    if lib.narwhal_transcript_apply(handle, c.byref(request), c.sizeof(request),
            input, len(data), c.byref(output), c.sizeof(output)) != 0:
        raise RuntimeError("ui-core transcript apply failed: " + str(request.op))
    return output
transcript_stats((0, 0, 0, 0))
request, reply = TranscriptRequest(0, 2, 0, 4, 0, 0), TranscriptReply()
for request_size, reply_size in ((39, 56), (40, 55)):
    if lib.narwhal_transcript_apply(0, c.byref(request), request_size, None, 0,
            c.byref(reply), reply_size) != 1:
        raise RuntimeError("ui-core transcript accepted a wrong struct size")
transcript_stats((0, 0, 0, 0))
created = transcript_call(0, request)
if not created.handle:
    raise RuntimeError("ui-core transcript returned no owner")
owner = created.handle
try:
    if (created.revision, created.len, created.token, created.count, created.bytes, created.kind) != (1, 0, 0, 0, 0, 0):
        raise RuntimeError("ui-core transcript create reply differs")
    appended = transcript_call(owner, TranscriptRequest(1, 0, created.revision, 0, 0, 0), payload)
    if (appended.handle, appended.revision, appended.len, appended.token, appended.count, appended.bytes, appended.kind) != (owner, 2, 2, 0, 0, 0, 0):
        raise RuntimeError("ui-core transcript append reply differs")
    snapshot = transcript_call(owner, TranscriptRequest(4, 0, appended.revision, 0, 2, 1))
    if not snapshot.token:
        raise RuntimeError("ui-core transcript returned no snapshot")
    try:
        if (snapshot.handle, snapshot.revision, snapshot.len, snapshot.count, snapshot.bytes, snapshot.kind) != (owner, 2, 2, 2, len(payload), 1):
            raise RuntimeError("ui-core transcript projection reply differs")
        transcript_stats((1, 1, 5, len(payload)))
        # Allocate from the known witness, never an untrusted native byte count.
        output, needed = (c.c_ubyte * len(payload))(), c.c_size_t()
        if (lib.narwhal_transcript_read(owner, snapshot.token, snapshot.revision,
                output, len(output), c.byref(needed)) != 0
                or needed.value != len(payload) or bytes(output) != payload):
            raise RuntimeError("ui-core transcript projection bytes differ")
    finally:
        if lib.narwhal_transcript_release(owner, snapshot.token) != 0:
            raise RuntimeError("ui-core transcript snapshot release failed")
    transcript_stats((1, 0, 5, 0))
finally:
    if lib.narwhal_transcript_close(owner) != 0:
        raise RuntimeError("ui-core transcript owner close failed")
transcript_stats((0, 0, 0, 0))
print(json.dumps(result, sort_keys=True))
'''


def probe_ui_core(path: Path, python: Path | str = sys.executable, *, env=None) -> dict:
    """Execute the actual library in an isolated child, not just inspect symbol strings."""
    try:
        completed = subprocess.run(
            [str(python), "-I", "-B", "-c", UI_CORE_PROBE, str(path), json.dumps(list(UI_CORE_EXPORTS))],
            env=env, text=True, capture_output=True, check=True, timeout=30)
        identity = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError(f"cannot verify ui-core ABI/State88/transcript Request40/Reply56: {getattr(error, 'stderr', '') or error}") from error
    if identity != UI_CORE_IDENTITY:
        raise ValueError(f"ui-core ABI/State88/transcript/export evidence differs: {identity!r}")
    return identity


def verify_ui_core_source(directory: Path, source: Path, library: str) -> dict:
    """Bind the staged binary to the full crate (including header, tables and license)."""
    try:
        receipt = directory / UI_CORE_SOURCE
        binary = directory / library
        if any(not path.is_file() or path.is_symlink() for path in (receipt, binary)):
            raise ValueError("missing regular ui-core source receipt/library")
        actual = json.loads(receipt.read_text(encoding="utf-8"))
        crate = source / "native/ui_core"
        for name in UI_CORE_REQUIRED_SOURCE:
            if not (crate / name).is_file():
                raise ValueError(f"missing ui-core source input: {name}")
        if (source / "native").is_symlink() or crate.is_symlink():
            raise ValueError("symlinked ui-core source directory")
        for directory, dirs, _ in os.walk(crate):
            dirs[:] = [name for name in dirs if name not in {".git", "target", "__pycache__"}]
            if any((Path(directory) / name).is_symlink() for name in dirs):
                raise ValueError("symlinked ui-core source directory")
        expected = {"inputs": inventory(source, "ui_core"), "sha256": digest(binary)}
        if actual != expected:
            raise ValueError("ui-core source/binary receipt differs")
    except (OSError, ValueError) as error:
        raise ValueError(f"cannot bind ui-core source: {error}") from error
    return actual


def digest(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def inventory(root: Path, crate: str) -> dict[str, str]:
    roots = [root / "native" / crate]
    if crate == "welcome":
        roots.append(root / "launch/narwhal3d.png")
    if crate == "cdr_multiversion":
        roots.append(root / "native/vendor/tracedraw")
    result = {}
    for base in roots:
        if base.is_file():
            result[base.relative_to(root).as_posix()] = digest(base)
            continue
        if not base.is_dir():
            raise ValueError(f"missing input: {base}")
        for directory, dirs, files in os.walk(base):
            dirs[:] = sorted(n for n in dirs if n not in {".git", "target", "__pycache__"})
            for name in files:
                path = Path(directory) / name
                if path.is_symlink():
                    raise ValueError(f"unreviewed source symlink: {path}")
                result[path.relative_to(root).as_posix()] = digest(path)
    return dict(sorted(result.items()))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("source-root", "output", "seed-target", "occt-source", "occt-libs"):
        p.add_argument("--" + name, required=True, type=Path)
    p.add_argument("--crate", choices=COMPONENTS, required=True)
    p.add_argument("--jobs", type=int, default=2)
    p.add_argument("--test", action="store_true")
    args = p.parse_args()
    root, out = args.source_root.resolve(), args.output.resolve()
    if not 1 <= args.jobs <= 4:
        p.error("jobs must be 1..4")
    if out == root or root in out.parents:
        p.error("output must be outside source")
    for path in (root, args.seed_target, args.occt_source, args.occt_libs):
        if not path.is_dir():
            p.error(f"missing input directory: {path}")
    out.mkdir(parents=True, exist_ok=True)
    target = out / (args.crate + "-target")
    if not target.exists():
        subprocess.run(["cp", "-a", "--reflink=auto", str(args.seed_target), str(target)], check=True)
    before = inventory(root, args.crate)
    home = Path.home()
    mode = "test" if args.test else "build"
    command = ["podman", "run", "--rm", "--name", f"narwhal048-native-{args.crate}",
               "--timeout=1800", "--pull=never", "--network=none", "--read-only",
               f"--cpus={args.jobs}", "--memory=4g", "--memory-swap=4g", "--pids-limit=512",
               "--security-opt", "label=disable", "--security-opt", "no-new-privileges",
               "--tmpfs", "/tmp:rw,size=512m", "--tmpfs", "/cargohome:rw,size=32m"]
    for source, destination, access in (
        (root, "/proj", "ro"), (target, "/target", "rw"),
        (home / ".rustup", "/rustup", "ro"),
        (home / ".cargo/registry", "/cargohome/registry", "ro"),
        (home / ".cargo/git", "/cargohome/git", "ro"),
        (args.occt_source.resolve(), "/occt-source", "ro"),
        (args.occt_libs.resolve(), "/occt-libs", "ro"),
    ):
        command += ["-v", f"{source}:{destination}:{access}"]
    for value in (
        "PATH=/rustup/toolchains/1.95.0-x86_64-unknown-linux-gnu/bin:/opt/rh/gcc-toolset-14/root/usr/bin:/usr/local/bin:/usr/bin:/bin",
        "CARGO_HOME=/cargohome", "RUSTUP_HOME=/rustup", "CARGO_TARGET_DIR=/target",
        "CARGO_NET_OFFLINE=true", "CARGO_INCREMENTAL=0", "HOME=/tmp/home",
        "NARWHAL_OCCT_SOURCE_DIR=/occt-source", "NARWHAL_OCCT_LIB_DIR=/occt-libs",
        "LD_LIBRARY_PATH=/occt-libs", "LC_ALL=C", "TZ=UTC",
    ):
        command += ["-e", value]
    command += ["-w", f"/proj/native/{args.crate}", IMAGE, "cargo", mode,
                "--release", "--frozen", "--offline", "--jobs", str(args.jobs)]
    log = out / f"{args.crate}-{mode}.log"
    with log.open("w") as stream:
        completed = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=1830)
    after = inventory(root, args.crate)
    receipt = {"command": command, "inputs": before, "source_unchanged": before == after,
               "returncode": completed.returncode, "log_sha256": digest(log)}
    (out / f"{args.crate}-{mode}-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if completed.returncode or before != after:
        raise SystemExit(f"{args.crate}: failed or source changed; inspect {log}")
    if not args.test:
        if args.crate == "pure_core":
            receipt["pure_core_parser"] = probe_pure_core_parser(target / "release/libnarwhal_pure_core.so")
            (out / f"{args.crate}-{mode}-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        (out / "bin").mkdir(exist_ok=True)
        for name in COMPONENTS[args.crate]:
            source, destination = target / "release" / name, out / "bin" / name
            if destination.exists() and digest(destination) != digest(source):
                raise SystemExit(f"refusing to overwrite previously staged {destination}")
            shutil.copy2(source, destination)
        if args.crate == "ui_core":
            (out / "bin" / UI_CORE_SOURCE).write_text(json.dumps(
                {"inputs": before, "sha256": digest(out / "bin/libnarwhal_ui_core.so")},
                sort_keys=True) + "\n")
    print(f"{args.crate} {mode}: passed; receipt {out / (args.crate + '-' + mode + '-receipt.json')}")


if __name__ == "__main__":
    main()
