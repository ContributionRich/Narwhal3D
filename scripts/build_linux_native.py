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

from check_pure_core import probe_pure_core_curve_hits, probe_pure_core_curve_record_hits, probe_pure_core_line_samples, probe_pure_core_nearest_batch, probe_pure_core_nurbs_record, probe_pure_core_offset_fit, probe_pure_core_surface_offset, probe_pure_core_surface_grid, probe_pure_core_surface_display, probe_pure_core_parser, probe_pure_core_polycurve_samples, probe_pure_core_polyline_nearest, probe_pure_core_polyline_samples, probe_pure_core_retirement, probe_pure_core_remove_join_cv, probe_pure_core_dxf_approx, probe_pure_core_planar_area

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
    "library_core": ("narwhal-library-core",),
    "application_core": ("libnarwhal_application_core.so",),
    "ui_core": ("libnarwhal_ui_core.so",),
    "converter_supervisor": ("narwhal-converter-supervisor",),
    "automation_core": ("libnarwhal_automation.so", "narwhal-automation"),
    "picture_trace": ("narwhal-picture-trace",),
    "picture_pdf": ("narwhal-picture-pdf",),
}


GEOMETRY_CORE_EXPORTS = (
    "narwhal_geometry_core_abi_version",
    "narwhal_geometry_core_capabilities",
    "narwhal_geometry_core_cap_exact_v1",
    "narwhal_geometry_core_verify_cap_v1",
    "narwhal_geometry_core_nurbs_convert_seam_safe_v1",
    "narwhal_geometry_core_edit_session_import",
    "narwhal_geometry_core_edit_session_move",
    "narwhal_geometry_core_edit_session_move_mode",
    "narwhal_geometry_core_edit_session_release",
    "narwhal_geometry_core_inspect_brep",
    "narwhal_geometry_core_join_exact_v1",
    "narwhal_geometry_core_join_mapped_v1",
    "narwhal_geometry_core_make_box_brep",
    "narwhal_geometry_core_move_component_brep",
    "narwhal_geometry_core_prepare_freeform_v1",
    "narwhal_geometry_core_sweep1_freeform_approx_v1",
    "narwhal_geometry_core_sweep1_parallel_v1",
    "narwhal_geometry_core_verify_join_v1",
)
GEOMETRY_CORE_REQUIRED_CAPABILITIES = (1 << 11) - 1
GEOMETRY_CORE_SOURCE = "geometry-core-source.json"
GEOMETRY_CORE_REQUIRED_SOURCE = (
    '.gitignore',
    'Cargo.lock',
    'Cargo.toml',
    'README.md',
    'build.rs',
    'cpp/cap_adapter.inc',
    'cpp/exact_budget.inc',
    'cpp/exact_conversion.inc',
    'cpp/freeform_adapter.inc',
    'cpp/join_adapter.inc',
    'cpp/occt_adapter.cpp',
    'cpp/preparation_adapter.inc',
    'cpp/sweep_adapter.inc',
    'include/narwhal_geometry_core.h',
    'src/cap.rs',
    'src/exact_conversion.rs',
    'src/freeform.rs',
    'src/freeform_math.rs',
    'src/height_field.rs',
    'src/join.rs',
    'src/lib.rs',
    'src/preparation.rs',
    'src/surfacing.rs',
)

# Packaging contract from native/ui_core/narwhal_ui_core.h. Python only loads the
# C ABI and checks receipts; all editor and popup behavior remains in Rust.
UI_CORE_EXPORTS = (
    "narwhal_editor_abi", "narwhal_editor_close", "narwhal_editor_edit", "narwhal_editor_read",
    "narwhal_popup_abi", "narwhal_popup_apply", "narwhal_popup_destroy",
    "narwhal_popup_guards", "narwhal_popup_hit", "narwhal_popup_panel", "narwhal_popup_rows",
    "narwhal_transcript_abi", "narwhal_transcript_apply", "narwhal_transcript_capabilities",
    "narwhal_transcript_close", "narwhal_transcript_read", "narwhal_transcript_release",
    "narwhal_transcript_stats",
    "narwhal_ui_content_abi", "narwhal_ui_content_read",
)
UI_CORE_IDENTITY = {"content_abi": 1, "editor_abi": 1, "popup_abi": 1, "state_bytes": 88,
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
    'src/content.rs',
    'src/content_data/content.json',
    'tests/content13.rs',
    'tests/content13_host.rs',
    'tests/content13_mutations.rs',
    'tests/data/content13_golden.json',
    'tests/data/content13_provenance.json',
    "tests/host_ui31_integrity.rs",
    "tests/data/host_ui31.json",
    "tests/data/host_ui31.provenance.json",
    "tests/editor27_golden.rs",
    "tests/unicode28_golden.rs",
    "tests/popup28_native.rs",
    "tests/support/popup28_fixture.rs",
    "tests/data/popup28_golden.bin",
    "tests/data/popup28_golden.provenance.json",

    "tests/data/unicode28_golden.txt",
    "tests/data/unicode28_golden.provenance.json",

    "tests/support/editor27_fixture.rs",
    "tests/support/golden27_sha256.rs",
    "tests/data/editor27_golden.bin",
    "tests/data/editor27_golden.provenance.json",
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
lib.narwhal_ui_content_abi.argtypes = []
lib.narwhal_ui_content_abi.restype = c.c_uint32
result["content_abi"] = int(lib.narwhal_ui_content_abi())
if result["content_abi"] != 1:
    raise RuntimeError("ui-core content ABI mismatch")
lib.narwhal_ui_content_read.argtypes = [c.c_uint32, c.c_void_p, c.c_size_t, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t)]
lib.narwhal_ui_content_read.restype = c.c_uint32
content_input = b'{"rows":{"t":"tuple","v":[]}}'
content_expected = b'{"t":"tuple","v":[]}'
source = c.create_string_buffer(content_input)
needed = c.c_size_t()
if lib.narwhal_ui_content_read(4, source, len(content_input), None, 0, c.byref(needed)) != 0 or needed.value != len(content_expected):
    raise RuntimeError("ui-core content sizing differs")
output = c.create_string_buffer(len(content_expected))
if lib.narwhal_ui_content_read(4, source, len(content_input), output, len(output), c.byref(needed)) != 0 or needed.value != len(content_expected) or output.raw != content_expected:
    raise RuntimeError("ui-core content projection differs")
if lib.narwhal_ui_content_read(99, source, len(content_input), None, 0, c.byref(needed)) == 0:
    raise RuntimeError("ui-core content accepted an unknown operation")
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


def geometry_source_inventory(root):
    root = Path(root)
    for name in GEOMETRY_CORE_REQUIRED_SOURCE:
        path = root / "native/geometry_core" / name
        if not path.is_file() or path.is_symlink():
            raise ValueError("missing geometry-core source: " + name)
    return inventory(root, "geometry_core")


def verify_geometry_core_source(directory, root, library_name="libnarwhal_geometry_core.so"):
    receipt, binary = Path(directory) / GEOMETRY_CORE_SOURCE, Path(directory) / library_name
    if any(not path.is_file() or path.is_symlink() for path in (receipt, binary)):
        raise ValueError("missing regular geometry-core source receipt/library")
    record = json.loads(receipt.read_text())
    if record.get("inputs") != geometry_source_inventory(root) or record.get("sha256") != digest(binary):
        raise ValueError("geometry-core source/binary receipt differs")
    probe = record.get("geometry_core", {})
    if (probe.get("abi") != 4
            or probe.get("capabilities", 0) & GEOMETRY_CORE_REQUIRED_CAPABILITIES != GEOMETRY_CORE_REQUIRED_CAPABILITIES
            or probe.get("exports") != list(GEOMETRY_CORE_EXPORTS)
            or probe.get("join_semantics") != {"suite": "exact Join startup", "exact_faces": 3,
                "closed": True, "operand_count": 3, "recipe_verified": True}):
        raise ValueError("geometry-core source receipt lacks exact Join qualification")
    if probe.get("cap_semantics") != {"suite": "exact Cap startup", "exact_faces": 3,
            "closed": True, "recipe_verified": True, "supervised": True, "shifted_cone": True}:
        raise ValueError("geometry-core source receipt lacks supervised exact Cap qualification")
    return record


def probe_geometry_core_join(library, root=None, python=None, *, env=None):
    """Check all existing exports/capabilities and, with a root, actual SDK semantics."""
    code = 'import ctypes,json,os,sys,types,runpy\nfrom pathlib import Path\npath,root,exports,required=sys.argv[1:]\nlib=ctypes.CDLL(path)\nfor name in json.loads(exports): getattr(lib,name)\nlib.narwhal_geometry_core_abi_version.restype=ctypes.c_uint32\nlib.narwhal_geometry_core_capabilities.restype=ctypes.c_uint64\nabi=int(lib.narwhal_geometry_core_abi_version()); caps=int(lib.narwhal_geometry_core_capabilities())\nassert abi==4 and caps & int(required)==int(required),(abi,caps)\nresult=dict(abi=abi,capabilities=caps,exports=json.loads(exports))\nif root:\n    os.environ["NARWHAL_GEOMETRY_CORE"]=path\n    package=types.ModuleType("narwhal3d"); package.__path__=[str(Path(root)/"narwhal3d")]\n    sys.modules["narwhal3d"]=package\n    namespace=runpy.run_path(str(Path(root)/"launch/check_native.py"),run_name="geometry_join_admission")\n    result["join_semantics"]=namespace["check_geometry_consumers"]()\n    result["cap_semantics"]=namespace["check_cap_consumers"]()\nprint(json.dumps(result,sort_keys=True))\n'
    try:
        completed = subprocess.run(
            [str(python or sys.executable), "-I", "-c", code, str(Path(library).resolve()),
             str(Path(root).resolve()) if root else "", json.dumps(GEOMETRY_CORE_EXPORTS),
             str(GEOMETRY_CORE_REQUIRED_CAPABILITIES)],
            env=env, text=True, capture_output=True, timeout=60, check=True)
        result = json.loads(completed.stdout.strip())
        if (result.get("abi") != 4 or result.get("capabilities", 0) & GEOMETRY_CORE_REQUIRED_CAPABILITIES
                != GEOMETRY_CORE_REQUIRED_CAPABILITIES
                or result.get("exports") != list(GEOMETRY_CORE_EXPORTS)):
            raise ValueError("geometry ABI/export/capability admission differs")
        if root and result.get("join_semantics") != {
                "suite": "exact Join startup", "exact_faces": 3, "closed": True,
                "operand_count": 3, "recipe_verified": True}:
            raise ValueError("exact Join SDK admission differs")
        if root and result.get("cap_semantics") != {"suite": "exact Cap startup",
                "exact_faces": 3, "closed": True, "recipe_verified": True, "supervised": True, "shifted_cone": True}:
            raise ValueError("exact Cap SDK admission differs")
        return result
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError("exact Join/Cap geometry admission failed: " + str(error)) from error


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
    before = geometry_source_inventory(root) if args.crate == "geometry_core" else inventory(root, args.crate)
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
    after = geometry_source_inventory(root) if args.crate == "geometry_core" else inventory(root, args.crate)
    receipt = {"command": command, "inputs": before, "source_unchanged": before == after,
               "returncode": completed.returncode, "log_sha256": digest(log)}
    (out / f"{args.crate}-{mode}-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if completed.returncode or before != after:
        raise SystemExit(f"{args.crate}: failed or source changed; inspect {log}")
    if not args.test:
        if args.crate == "geometry_core":
            interpreter = args.occt_libs.resolve().parents[3] / "bin/python3.13"
            environment = dict(os.environ, LD_LIBRARY_PATH=str(args.occt_libs.resolve()),
                               PYTHONDONTWRITEBYTECODE="1")
            receipt["geometry_core"] = probe_geometry_core_join(
                target / "release/libnarwhal_geometry_core.so", root, interpreter, env=environment)
            (out / f"{args.crate}-{mode}-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        if args.crate == "pure_core":
            receipt["pure_core_parser"] = probe_pure_core_parser(target / "release/libnarwhal_pure_core.so")
            receipt["pure_core_retirement"] = probe_pure_core_retirement(target / "release/libnarwhal_pure_core.so")
            receipt["pure_core_nearest_batch"] = probe_pure_core_nearest_batch(target / "release/libnarwhal_pure_core.so")
            receipt["pure_core_curve_hits"] = probe_pure_core_curve_hits(target / "release/libnarwhal_pure_core.so")
            receipt["pure_core_curve_record_hits"] = probe_pure_core_curve_record_hits(target / "release/libnarwhal_pure_core.so")
            receipt["pure_core_polyline_nearest"] = probe_pure_core_polyline_nearest(target / "release/libnarwhal_pure_core.so")
            receipt["pure_core_nurbs_record"] = probe_pure_core_nurbs_record(target / "release/libnarwhal_pure_core.so")
            receipt["pure_core_line_samples"] = probe_pure_core_line_samples(target / "release/libnarwhal_pure_core.so")
            receipt["pure_core_polyline_samples"] = probe_pure_core_polyline_samples(target / "release/libnarwhal_pure_core.so")
            receipt["pure_core_polycurve_samples"] = probe_pure_core_polycurve_samples(target / "release/libnarwhal_pure_core.so")
            receipt["pure_core_remove_join_cv"] = probe_pure_core_remove_join_cv(target / "release/libnarwhal_pure_core.so")
            receipt["pure_core_offset_fit"] = probe_pure_core_offset_fit(target / "release/libnarwhal_pure_core.so")
            receipt["pure_core_dxf_approx"] = probe_pure_core_dxf_approx(target / "release/libnarwhal_pure_core.so")
            receipt["pure_core_surface_offset"] = probe_pure_core_surface_offset(target / "release/libnarwhal_pure_core.so")
            receipt["pure_core_planar_area"] = probe_pure_core_planar_area(target / "release/libnarwhal_pure_core.so")
            receipt["pure_core_surface_grid"] = probe_pure_core_surface_grid(target / "release/libnarwhal_pure_core.so")
            receipt["pure_core_surface_display"] = probe_pure_core_surface_display(target / "release/libnarwhal_pure_core.so")
            (out / f"{args.crate}-{mode}-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        (out / "bin").mkdir(exist_ok=True)
        for name in COMPONENTS[args.crate]:
            source, destination = target / "release" / name, out / "bin" / name
            if destination.exists() and digest(destination) != digest(source):
                raise SystemExit(f"refusing to overwrite previously staged {destination}")
            shutil.copy2(source, destination)
        if args.crate == "geometry_core":
            (out / "bin" / GEOMETRY_CORE_SOURCE).write_text(json.dumps(
                {"inputs": before, "sha256": digest(out / "bin/libnarwhal_geometry_core.so"),
                 "geometry_core": receipt["geometry_core"]}, sort_keys=True) + "\n")
        if args.crate == "ui_core":
            (out / "bin" / UI_CORE_SOURCE).write_text(json.dumps(
                {"inputs": before, "sha256": digest(out / "bin/libnarwhal_ui_core.so")},
                sort_keys=True) + "\n")
    print(f"{args.crate} {mode}: passed; receipt {out / (args.crate + '-' + mode + '-receipt.json')}")


if __name__ == "__main__":
    main()
