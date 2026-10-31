#!/usr/bin/env python3
"""Source-only UI-core packaging gates; no native build, VM or release operation.

Python exercises packaging orchestration with ctypes doubles and synthetic PE
bytes. Mutation checks run in memory, never modifying application/native sources.
"""
import ast
import copy
import ctypes
import inspect
import io
import json
from pathlib import Path
import re

import struct
import subprocess
import sys
import tempfile
import textwrap
import tomllib
import types
import unittest
from unittest.mock import patch

import build_linux_native as common
import build_macos_native as mac_build
import assemble_linux_release as linux
import verify_linux_release as linux_verify
import assemble_macos_release as mac
import assemble_windows_release as windows_assemble
import verify_windows_release as windows

ROOT = Path(__file__).resolve().parents[1]
SO, DYLIB, DLL = "libnarwhal_ui_core.so", "libnarwhal_ui_core.dylib", "narwhal_ui_core.dll"
EXPORTS = (
    "narwhal_editor_abi", "narwhal_editor_close", "narwhal_editor_edit", "narwhal_editor_read",
    "narwhal_popup_abi", "narwhal_popup_apply", "narwhal_popup_destroy",
    "narwhal_popup_guards", "narwhal_popup_hit", "narwhal_popup_panel", "narwhal_popup_rows",
    "narwhal_transcript_abi", "narwhal_transcript_apply", "narwhal_transcript_capabilities",
    "narwhal_transcript_close", "narwhal_transcript_read", "narwhal_transcript_release",
    "narwhal_transcript_stats",
)
IDENTITY = {"editor_abi": 1, "popup_abi": 1, "state_bytes": 88,
            "transcript_abi": 1, "transcript_capabilities": 7,
            "transcript_request_bytes": 40, "transcript_reply_bytes": 56, "exports": list(EXPORTS)}


def add_transcript_fixture(lib, transcript=1, capabilities=7, request_bytes=40, reply_bytes=56, transcript_fault=""):
    """Fixed binary smoke witness, not the application parser or transcript adapter."""
    payload = struct.pack("<8I", 2, 3, 65, 0, 0x1f40b, 2, 0xd800, 66)
    state = dict(owner=False, revision=0, lines=0, token=False)
    lib.transcript_calls = []
    lib.narwhal_transcript_abi = lambda: transcript
    lib.narwhal_transcript_capabilities = lambda: capabilities

    def apply(handle, request, request_size, data, length, output, output_size):
        if request_size != request_bytes or output_size != reply_bytes:
            return 0 if transcript_fault == "accept-sizes" else 1
        r, out = request._obj, output._obj
        lib.transcript_calls.append(r.op)
        if transcript_fault == "apply":
            return 1
        if r.op == 0:
            assert (handle, r.flags, r.revision, r.a, r.b, r.c, length) == (0, 2, 0, 4, 0, 0, 0)
            state.update(owner=True, revision=1, lines=0, token=False)
        else:
            assert state["owner"] and handle == 19 and r.revision == state["revision"] and r.flags == 0
            if r.op == 1:
                assert ctypes.string_at(data, length) == payload
                state.update(revision=2, lines=0 if transcript_fault == "append" else 2)
            else:
                assert (r.op, r.a, r.b, r.c, length) == (4, 0, 2, 1, 0)
                state["token"] = True
                out.token, out.count, out.bytes, out.kind = 23, 2, len(payload), 1
                if transcript_fault == "huge-bytes": out.bytes = 2**63
                if transcript_fault == "count": out.count = 0
        out.handle, out.revision, out.len = 19, state["revision"], state["lines"]
        if transcript_fault == "owner": out.handle = 0
        if transcript_fault == "revision": out.revision = 0
        return 0

    def read(handle, token, revision, output, capacity, needed):
        assert (handle, token, revision, capacity) == (19, 23, 2, len(payload)) and state["token"]
        needed._obj.value = len(payload) + (1 if transcript_fault == "needed" else 0)
        ctypes.memmove(output, payload, len(payload))
        if transcript_fault == "bytes": output[0] = 99
        return 1 if transcript_fault == "read" else 0

    def release(handle, token):
        assert (handle, token) == (19, 23) and state["token"]
        state["token"] = transcript_fault == "leak"
        return 1 if transcript_fault == "release" else 0

    def close(handle):
        assert handle == 19 and state["owner"]
        state.update(owner=False, lines=0, token=False)
        return 1 if transcript_fault == "close" else 0

    def stats(output, count):
        assert count == 4
        output[:] = (int(state["owner"]), int(state["token"]),
                     5 if state["lines"] else 0, len(payload) if state["token"] else 0)
        return 1 if transcript_fault == "stats" else 0

    lib.narwhal_transcript_apply = apply
    lib.narwhal_transcript_read = read
    lib.narwhal_transcript_release = release
    lib.narwhal_transcript_close = close
    lib.narwhal_transcript_stats = stats
    return lib


def fake_library(editor=1, popup=1, missing=(), state_bytes=88, edit_status=0, read=0, close=0,
                 transcript=1, capabilities=7, request_bytes=40, reply_bytes=56, transcript_fault=""):
    lib = types.SimpleNamespace(**{name: lambda *args: 0 for name in EXPORTS})
    lib.narwhal_editor_abi = lambda: editor
    lib.narwhal_popup_abi = lambda: popup
    lib.edits = 0
    def edit(handle, op, value, flags, text, length, output, size):
        lib.edits += 1
        if size != state_bytes:
            return 1
        state = output._obj
        state.handle, state.revision, state.text_revision, state.length = 7, 1, 1, length
        return edit_status
    def read_text(handle, revision, start, output, count):
        output[0] = 65
        return read
    lib.narwhal_editor_edit = edit
    lib.narwhal_editor_read = read_text
    lib.narwhal_editor_close = lambda handle: close
    add_transcript_fixture(lib, transcript, capabilities, request_bytes, reply_bytes, transcript_fault)
    for name in missing:
        delattr(lib, name)
    return lib


def execute_probe(command, **kwargs):
    """Execute production probe text only, with CDLL patched by the caller."""
    index = command.index("-c")
    output = io.StringIO()
    with patch.object(sys, "argv", ["-c", *command[index + 2:]]), patch.object(sys, "stdout", output):
        try:
            exec(compile(command[index + 1], "ui-core-probe", "exec"), {})
        except Exception as error:
            raise subprocess.CalledProcessError(1, command, stderr=str(error)) from error
    return types.SimpleNamespace(stdout=output.getvalue())


def pe_fixture(editor=1, popup=1, missing=(), unknown=False, transcript=1, capabilities=7):
    """Synthetic mapped PE32+ with distinct, nonoverlapping export tables/code."""
    data = bytearray(0x2200)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3c, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", data, 0x84, 0x8664, 1, 0, 0, 0, 240, 0x2002)
    struct.pack_into("<H", data, 0x98, 0x20b)
    struct.pack_into("<I", data, 0x98 + 60, 0x200)
    struct.pack_into("<IIII", data, 0x98 + 112, 0x1000, 0x400, 0x1800, 40)
    struct.pack_into("<IIII", data, 0x188 + 8, 0x2000, 0x1000, 0x2000, 0x200)
    def at(rva):
        return rva - 0x1000 + 0x200
    symbols = [name for name in EXPORTS if name not in missing]
    struct.pack_into("<IIIII", data, at(0x1000) + 20, len(symbols), len(symbols), 0x10a0, 0x1040, 0x1100)
    for i, symbol in enumerate(symbols):
        name_rva, function_rva = 0x1180 + i * 0x30, 0x1600 + i * 0x10
        struct.pack_into("<I", data, at(0x1040) + 4 * i, name_rva)
        struct.pack_into("<I", data, at(0x10a0) + 4 * i, function_rva)
        struct.pack_into("<H", data, at(0x1100) + 2 * i, i)
        name = symbol.encode() + b"\0"
        data[at(name_rva):at(name_rva) + len(name)] = name
        value = {"narwhal_popup_abi": popup, "narwhal_transcript_abi": transcript,
                 "narwhal_transcript_capabilities": capabilities}.get(symbol, editor)
        data[at(function_rva):at(function_rva) + 6] = b"\xb8" + struct.pack("<I", value) + b"\xc3"
        if unknown and symbol == "narwhal_popup_abi":
            data[at(function_rva)] = 0x90
    struct.pack_into("<IIIII", data, at(0x1800), 0x1840, 0, 0, 0x1880, 0x1840)
    struct.pack_into("<QQ", data, at(0x1840), 0x18a0, 0)
    for rva, value in ((0x1880, b"KERNEL32.dll\0"), (0x18a2, b"Sleep\0")):
        data[at(rva):at(rva) + len(value)] = value
    return bytes(data)


def put(path, data=b"fixture"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def source_receipt(directory, library, source=ROOT):
    result = {"inputs": common.inventory(source, "ui_core"), "sha256": common.digest(directory / library)}
    put(directory / common.UI_CORE_SOURCE, json.dumps(result).encode())
    return result


def windows_driver():
    text = (ROOT / "scripts/build_windows_preview.ps1").read_text()
    return next(block for block in re.findall(r"@'\n(.*?)\n'@", text, re.S)
                if 'inputs = inventory(source, "ui_core")' in block)


class UiPackaging(unittest.TestCase):
    def test_header_and_mandatory_component_sets(self):
        header = (ROOT / "native/ui_core/narwhal_ui_core.h").read_text()
        self.assertEqual(tuple(sorted(re.findall(r"uint(?:32|64)_t (narwhal_\w+)\(", header))), EXPORTS)
        self.assertEqual(common.UI_CORE_EXPORTS, EXPORTS)
        self.assertEqual(common.UI_CORE_IDENTITY, IDENTITY)
        self.assertEqual(common.COMPONENTS["ui_core"], (SO,))
        self.assertEqual(mac_build.COMPONENTS["ui_core"], (DYLIB,))
        for module in (linux, linux_verify):
            self.assertIn(SO, module.NATIVE_COMPONENTS)
            self.assertIn(SO, module.PACKAGED_ACCELERATORS)
        for relative in ("runtime/linux-x86_64/bin/" + SO, "narwhal3d/native/" + SO):
            self.assertIn("usr/lib/narwhal3d/" + relative, linux_verify.REQUIRED_PATHS)
        self.assertEqual(windows.NATIVE_COMPONENT_PATHS[DLL], "app/runtime/windows-x86_64/bin/" + DLL)
        self.assertIn("narwhal3d/native/" + DLL, windows.GENERATED_PACKAGE_FILES)
        for path in (windows.NATIVE_COMPONENT_PATHS[DLL], windows.UI_CORE_ADJACENT, windows.UI_CORE_SOURCE_PATH):
            self.assertIn(path, windows.REQUIRED)

    def test_full_source_provenance_and_existing_licenses(self):
        inputs = common.inventory(ROOT, "ui_core")
        for name in ("Cargo.toml", "Cargo.lock", "LICENSE", "narwhal_ui_core.h", "gen_unicode.py",
                     "src/editor.rs", "src/unicode16.rs", "src/popup.rs", "src/popup_ffi.rs",
                     "TRANSCRIPT.md", "transcript_unicode.py", "src/transcript.rs",
                     "src/transcript_ffi.rs", "src/transcript_tests.rs"):
            self.assertEqual(inputs["native/ui_core/" + name], common.digest(ROOT / "native/ui_core" / name))
        self.assertFalse(any("/target/" in name or "__pycache__" in name for name in inputs))
        lock = tomllib.loads((ROOT / "native/ui_core/Cargo.lock").read_text())
        self.assertEqual([p["name"] for p in lock["package"]], ["narwhal_ui_core"])
        self.assertEqual((ROOT / "native/ui_core/LICENSE").read_bytes(),
                         (ROOT / "licenses/GPL-3.0-or-later.txt").read_bytes())
        self.assertIn("UNICODE LICENSE V3", (ROOT / "licenses/Native-Rust-Dependencies.txt").read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in inputs:
                put(root / name, (ROOT / name).read_bytes())
            put(root / "launch/narwhal3d.png")
            self.assertEqual({k: v for k, v in mac_build.source_inputs(root).items()
                              if k.startswith("native/ui_core/")}, inputs)
            put(root / "native/ui_core/src/popup.rs", b"changed")
            self.assertNotEqual(common.inventory(root, "ui_core"), inputs)

    def test_executed_probe_rejects_old_editor_wrong_abis_and_every_missing_export(self):
        cases = [{}, {"editor": 2}, {"popup": 2}, {"transcript": 2}, {"capabilities": 3},
                 {"request_bytes": 48}, {"reply_bytes": 64},
                 {"state_bytes": 96}, {"edit_status": 1}, {"read": 1}, {"close": 1},
                 {"missing": tuple(n for n in EXPORTS if n.startswith("narwhal_popup"))}]
        cases += [{"missing": (name,)} for name in EXPORTS]
        cases += [{"transcript_fault": fault} for fault in (
            "apply", "accept-sizes", "append", "huge-bytes", "count", "owner", "revision",
            "needed", "bytes", "read", "release", "close", "stats", "leak")]
        for options in cases:
            library = fake_library(**options)
            with self.subTest(options=options), patch.object(ctypes, "CDLL", return_value=library), \
                 patch.object(common.subprocess, "run", side_effect=execute_probe) as run:
                if options:
                    with self.assertRaises(ValueError):
                        common.probe_ui_core(Path(SO))
                else:
                    self.assertEqual(common.probe_ui_core(Path(SO)), IDENTITY)
                    self.assertIn("-I", run.call_args.args[0])
                    self.assertIn("-B", run.call_args.args[0])
                    self.assertEqual(run.call_args.kwargs["timeout"], 30)
                if any(key in options for key in ("editor", "popup", "transcript", "capabilities", "missing")):
                    self.assertEqual(library.edits, 0, "incompatible library must fail before any editor mutation")

    def test_probe_receipt_must_match_executed_identity(self):
        for key, value in (("editor_abi", 2), ("popup_abi", 2), ("state_bytes", 96), ("exports", []),
                           ("transcript_abi", 2), ("transcript_capabilities", 3),
                           ("transcript_request_bytes", 48), ("transcript_reply_bytes", 64)):
            for omit in (True, False):
                result = copy.deepcopy(IDENTITY)
                if omit:
                    del result[key]
                else:
                    result[key] = value
                with patch.object(common.subprocess, "run", return_value=types.SimpleNamespace(stdout=json.dumps(result))):
                    with self.assertRaises(ValueError):
                        common.probe_ui_core(Path(SO))

    def test_source_receipt_rejects_stale_missing_linked_and_changed_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            put(root / SO)
            receipt = source_receipt(root, SO)
            self.assertEqual(common.verify_ui_core_source(root, ROOT, SO), receipt)
            for key, value in (("inputs", {}), ("sha256", "0" * 64)):
                put(root / common.UI_CORE_SOURCE, json.dumps(dict(receipt, **{key: value})).encode())
                with self.assertRaises(ValueError):
                    common.verify_ui_core_source(root, ROOT, SO)
            source_receipt(root, SO)
            put(root / SO, b"changed library")
            with self.assertRaises(ValueError):
                common.verify_ui_core_source(root, ROOT, SO)
            for name in (SO, common.UI_CORE_SOURCE):
                put(root / SO)
                source_receipt(root, SO)
                original = (root / name).read_bytes()
                (root / name).unlink()
                with self.assertRaises(ValueError):
                    common.verify_ui_core_source(root, ROOT, SO)
                put(root / "linked", original)
                (root / name).symlink_to(root / "linked")
                with self.assertRaises(ValueError):
                    common.verify_ui_core_source(root, ROOT, SO)
                (root / name).unlink()

    def test_required_sources_cannot_be_omitted_from_rehashed_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, binary = root / "source", root / "binary"
            inputs = common.inventory(ROOT, "ui_core")
            for name in inputs:
                put(source / name, (ROOT / name).read_bytes())
            put(binary / SO)
            receipt = source_receipt(binary, SO, source)
            self.assertEqual(len(common.UI_CORE_REQUIRED_SOURCE), 21)
            for name in common.UI_CORE_REQUIRED_SOURCE:
                path = source / "native/ui_core" / name
                data = path.read_bytes()
                path.unlink()
                damaged = copy.deepcopy(receipt)
                del damaged["inputs"]["native/ui_core/" + name]
                put(binary / common.UI_CORE_SOURCE, json.dumps(damaged).encode())
                with self.subTest(source=name), self.assertRaisesRegex(ValueError, "missing ui-core source"):
                    common.verify_ui_core_source(binary, source, SO)
                path.write_bytes(data)
            put(binary / common.UI_CORE_SOURCE, json.dumps(receipt).encode())
            self.assertEqual(common.verify_ui_core_source(binary, source, SO), receipt)
            # Receipts must not silently omit an added directory through os.walk.
            (source / "native/ui_core/linked").symlink_to(source / "native/ui_core/src", target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlinked ui-core source"):
                common.verify_ui_core_source(binary, source, SO)

    def test_linux_manifest_copies_and_ui_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / linux_verify.APP_ROOT_RELATIVE
            native = app / linux_verify.NATIVE_COMPONENT_RELATIVE
            records = {"required_glibc": "2.28"}
            for name in linux_verify.NATIVE_COMPONENTS:
                path = put(native / name, name.encode())
                records[name] = {"size": path.stat().st_size, "sha256": common.digest(path)}
                if name in linux_verify.PACKAGED_ACCELERATORS:
                    put(app / "narwhal3d/native" / name, path.read_bytes())
            for name, (_, abi) in linux_verify.NATIVE_LIBRARY_ABIS.items():
                records[name]["abi"] = abi
            records["libnarwhal_application_core.so"].update(
                history_abi=1, exports=list(linux_verify.APPLICATION_CORE_EXPORTS))
            records["libnarwhal_pure_core.so"].update(linux_verify.PURE_CORE_PARSE_IDENTITY)
            records["narwhal-welcome"]["launch_contract"] = 1
            records[SO].update(IDENTITY)
            records[SO]["source_inputs"] = common.inventory(ROOT, "ui_core")
            linux_verify.verify_native_manifest(root, records)
            linux_verify.verify_ui_core_manifest(records)
            damaged = dict(records)
            del damaged[SO]
            with self.assertRaises(linux_verify.VerificationError):
                linux_verify.verify_native_manifest(root, damaged)
            for relative in (linux_verify.NATIVE_COMPONENT_RELATIVE / SO, Path("narwhal3d/native") / SO):
                path = app / relative
                data = path.read_bytes()
                for mode in ("missing", "different", "linked"):
                    path.unlink(missing_ok=True)
                    if mode == "different":
                        path.write_bytes(b"wrong")
                    if mode == "linked":
                        path.symlink_to(put(app / "link-target", data))
                    with self.assertRaises(linux_verify.VerificationError):
                        linux_verify.verify_native_manifest(root, records)
                    path.unlink(missing_ok=True)
                    path.write_bytes(data)
            for key in (*IDENTITY, "source_inputs"):
                damaged = copy.deepcopy(records)
                del damaged[SO][key]
                with self.assertRaises(linux_verify.VerificationError):
                    linux_verify.verify_ui_core_manifest(damaged)

    def test_linux_assembly_source_and_actual_ui_probe_gate(self):
        tree = ast.parse(textwrap.dedent(inspect.getsource(linux.assemble)))
        block = next(node for node in ast.walk(tree) if isinstance(node, ast.Try)
                     and any(ast.unparse(child).startswith("ui_source =") for child in node.body))
        code = compile(ast.Module(body=[block], type_ignores=[]), "linux-ui-gate", "exec")
        with tempfile.TemporaryDirectory() as directory:
            native = Path(directory)
            put(native / SO)
            receipt = source_receipt(native, SO)
            for options in ({}, {"popup": 2}, {"missing": ("narwhal_popup_apply",)}):
                records = {SO: {}}
                namespace = dict(vars(linux), source_root=ROOT, native_components_dir=native, native_records=records)
                with patch.object(ctypes, "CDLL", return_value=fake_library(**options)), \
                     patch.object(common.subprocess, "run", side_effect=execute_probe):
                    if options:
                        with self.assertRaises(linux.ReleaseError):
                            exec(code, namespace)
                    else:
                        exec(code, namespace)
                        self.assertEqual(records[SO], dict(IDENTITY, source_inputs=receipt["inputs"]))
            (native / common.UI_CORE_SOURCE).unlink()
            with self.assertRaises(linux.ReleaseError):
                exec(code, namespace)

    def test_linux_build_stages_source_bound_receipt_without_building(self):
        tree = ast.parse(textwrap.dedent(inspect.getsource(common.main)))
        block = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                     and ast.unparse(node.test) == "not args.test")
        code = compile(ast.Module(body=block.body, type_ignores=[]), "linux-ui-build-staging", "exec")
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            target = out / "target"
            put(target / "release" / SO)
            before = common.inventory(ROOT, "ui_core")
            exec(code, dict(vars(common), args=types.SimpleNamespace(crate="ui_core"),
                            out=out, target=target, before=before))
            self.assertEqual(common.verify_ui_core_source(out / "bin", ROOT, SO)["inputs"], before)

    def test_linux_actual_copy_block(self):
        tree = ast.parse(textwrap.dedent(inspect.getsource(linux.assemble)))
        block = next(n for n in ast.walk(tree) if isinstance(n, ast.Try)
                     and any(ast.unparse(child).startswith("native_target =") for child in n.body))
        start = next(i for i, n in enumerate(block.body) if ast.unparse(n).startswith("native_target ="))
        end = next(i for i, n in enumerate(block.body) if ast.unparse(n).startswith("validate_appdir_paths("))
        code = compile(ast.Module(body=block.body[start:end], type_ignores=[]), "linux-ui-copy", "exec")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, app = root / "native", root / "app"
            for name in linux.NATIVE_COMPONENTS:
                put(source / name, name.encode())

            exec(code, dict(vars(linux), app_root=app, native_components_dir=source))
            for slot in (linux.NATIVE_COMPONENT_RELATIVE, Path("narwhal3d/native")):
                self.assertEqual((app / slot / SO).read_bytes(), SO.encode())


    def test_macos_both_slots_probe_and_component_omission(self):
        receipts = {name: {} for names in mac_build.COMPONENTS.values() for name in names}
        mac_build.require_components(receipts, {})
        del receipts[DYLIB]
        with self.assertRaises(ValueError):
            mac_build.require_components(receipts, {})
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory)
            native = app / "runtime/macos-arm64/bin"
            put(native / DYLIB)
            for name in ("GPL-3.0-or-later.txt", "Native-Rust-Dependencies.txt"):
                put(app / "licenses" / name)
            mac.stage_ui_core(native, app)
            adjacent = app / "narwhal3d/native" / DYLIB
            with patch.object(ctypes, "CDLL", return_value=fake_library()) as load, \
                 patch.object(common.subprocess, "run", side_effect=execute_probe):
                self.assertEqual(mac.verify_ui_core(app, Path("bundled-python")), IDENTITY)
                self.assertEqual(load.call_args.args, (str(adjacent),))
            with patch.object(ctypes, "CDLL", return_value=fake_library(popup=2)), \
                 patch.object(common.subprocess, "run", side_effect=execute_probe):
                with self.assertRaises(ValueError):
                    mac.verify_ui_core(app, Path("bundled-python"))
            for path in (native / DYLIB, adjacent, app / "licenses/GPL-3.0-or-later.txt"):
                data = path.read_bytes()
                path.unlink()
                with self.assertRaises(ValueError):
                    mac.verify_ui_core(app, Path("bundled-python"))
                path.write_bytes(data)
            adjacent.write_bytes(b"stale")
            with patch.object(mac, "probe_ui_core", return_value=IDENTITY) as probe:
                with self.assertRaises(ValueError):
                    mac.verify_ui_core(app, Path("bundled-python"))
                probe.assert_not_called()
            (native / DYLIB).unlink()
            with self.assertRaises(ValueError):
                mac.stage_ui_core(native, app)

    def test_macos_reuse_requires_source_hashes_and_actual_ui_qualification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "source", root / "output"
            put(source / "launch/narwhal3d.png")
            put(source / "native/ui_core/src/popup.rs")
            components = {}
            for names in mac_build.COMPONENTS.values():
                for name in names:
                    path = put(output / "bin" / name)
                    components[name] = {"architecture": "arm64", "sha256": common.digest(path)}
            occt = put(output / "bin/occt/fixture.dylib")
            receipt = {"source_inputs": mac_build.source_inputs(source), "components": components,
                       "unavailable_components": mac_build.UNAVAILABLE_COMPONENTS,
                       "occt_source": mac_build.OCCT, "occt_wheel_sha256": mac_build.WHEEL_SHA,
                       "pure_core_parser": linux_verify.PURE_CORE_PARSE_IDENTITY,
                       "occt_libraries": {occt.name: common.digest(occt)}, "ui_core": IDENTITY}
            path = output / "native-build.json"
            with patch.object(mac_build, "probe_pure_core_parser", return_value=linux_verify.PURE_CORE_PARSE_IDENTITY), \
                 patch.object(ctypes, "CDLL", return_value=fake_library()), \
                 patch.object(common.subprocess, "run", side_effect=execute_probe):
                put(path, json.dumps(receipt).encode())
                self.assertTrue(mac_build.reuse_output(source, output))
                for key in ("ui_core", "source_inputs"):
                    damaged = dict(receipt)
                    del damaged[key]
                    put(path, json.dumps(damaged).encode())
                    self.assertFalse(mac_build.reuse_output(source, output))
                put(path, json.dumps(receipt).encode())
            with patch.object(ctypes, "CDLL", return_value=fake_library(popup=2)), \
                 patch.object(common.subprocess, "run", side_effect=execute_probe):
                self.assertFalse(mac_build.reuse_output(source, output))

    def test_windows_static_abi_old_editor_missing_exports_and_unknown_codegen(self):
        cases = [{}, {"editor": 2}, {"popup": 2}, {"transcript": 2}, {"capabilities": 3}, {"unknown": True},
                 {"missing": tuple(n for n in EXPORTS if n.startswith("narwhal_popup"))}]
        cases += [{"missing": (name,)} for name in EXPORTS]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / DLL
            for options in cases:
                with self.subTest(options=options):
                    put(path, pe_fixture(**options))
                    if options:
                        with self.assertRaises(windows.VerificationError):
                            windows.verify_ui_core_identity(path)
                    else:
                        self.assertEqual(windows.verify_ui_core_identity(path)["exports"], EXPORTS)

    def test_windows_copies_source_record_and_manifest_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = put(root / windows.NATIVE_COMPONENT_PATHS[DLL], pe_fixture())
            adjacent = put(root / windows.UI_CORE_ADJACENT, runtime.read_bytes())
            source_receipt(runtime.parent, DLL)
            identity = windows.ui_core_record(root, ROOT)
            self.assertEqual(identity, dict(IDENTITY, machine="pe32plus-x86-64"))
            with patch.object(windows, "NATIVE_COMPONENT_PATHS", {DLL: windows.NATIVE_COMPONENT_PATHS[DLL]}), \
                 patch.object(windows, "verify_application_core_copies"), \
                 patch.object(windows, "verify_application_core_source"):
                assembled = windows_assemble.native_records(root, ROOT)[DLL]
            self.assertEqual(assembled, dict(identity, size=runtime.stat().st_size, sha256=common.digest(runtime)))
            tree = ast.parse(textwrap.dedent(inspect.getsource(windows.verify_directory)))
            branch = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                          and ast.unparse(n.test) == "filename == 'narwhal_ui_core.dll'")
            code = compile(ast.Module(body=branch.body, type_ignores=[]), "windows-ui-record", "exec")
            exec(code, dict(vars(windows), root=root, record=identity))
            for key in identity:
                damaged = dict(identity)
                del damaged[key]
                with self.assertRaises(windows.VerificationError):
                    exec(code, dict(vars(windows), root=root, record=damaged))
            adjacent.write_bytes(b"different")
            with self.assertRaises(windows.VerificationError):
                windows.ui_core_record(root, ROOT)
            adjacent.unlink()
            with self.assertRaises(windows.VerificationError):
                windows.ui_core_record(root, ROOT)
            adjacent.symlink_to(runtime)
            with self.assertRaises(windows.VerificationError):
                windows.ui_core_record(root, ROOT)

    def test_windows_source_replacement_preserves_ui_library(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, package = root / "source", root / "package"
            for name in windows_assemble.SOURCE_DIRECTORIES:
                (source / name).mkdir(parents=True)
            put(source / "narwhal3d/__init__.py")
            for name in windows_assemble.SOURCE_FILES:
                put(source / name)
            put(source / "native/application_core/OPENNURBS_LICENSE")
            adjacent = put(package / windows.UI_CORE_ADJACENT, pe_fixture())
            windows_assemble.replace_public_source(package, source)
            self.assertEqual(adjacent.read_bytes(), pe_fixture())

    def test_windows_embedded_driver_before_after_and_packaged_probe(self):
        code = compile(windows_driver(), "windows-ui-source-driver", "exec")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            for name in common.inventory(ROOT, "ui_core"):
                put(source / name, (ROOT / name).read_bytes())
            snapshot = root / "before.json"
            runtime = put(root / "app/runtime/windows-x86_64/bin" / DLL, pe_fixture())
            adjacent = put(root / "app/narwhal3d/native" / DLL, runtime.read_bytes())
            def execute(mode):
                with patch.object(sys, "argv", ["driver", str(source), mode, str(snapshot), str(runtime)]):
                    exec(code, {})
            with patch.object(ctypes, "CDLL", return_value=fake_library()) as load, \
                 patch.object(common.subprocess, "run", side_effect=execute_probe):
                execute("before")
                execute("after")
                common.verify_ui_core_source(runtime.parent, source, DLL)
                execute("verify")
                self.assertEqual(load.call_args.args, (str(adjacent),))
                put(source / "native/ui_core/src/popup.rs", b"source changed")
                with self.assertRaises(SystemExit):
                    execute("after")
                with self.assertRaises(ValueError):
                    execute("verify")
            with patch.object(ctypes, "CDLL", return_value=fake_library(popup=2)), \
                 patch.object(common.subprocess, "run", side_effect=execute_probe):
                execute("before")
                with self.assertRaises(ValueError):
                    execute("after")

    def test_mandatory_orchestrator_calls_and_windows_build_stage(self):
        sources = {
            linux.assemble: ("verify_ui_core_source(native_components_dir, source_root,", "probe_ui_core(native_components_dir /"),
            linux_verify.verify_appdir: ("verify_ui_core_manifest(native_components)",),
            linux_verify.verify_executables_and_imports: ("probe_ui_core(app_root /",),
            mac.main: ("stage_ui_core(native_bin, app)", "ui_core = verify_ui_core(app, python)",
                       "verify_ui_core(mounted_app /", 'native_receipt.get("ui_core") != UI_CORE_IDENTITY', '"ui_core": ui_core'),
            mac_build.main: ('probe_ui_core(bin_dir / "libnarwhal_ui_core.dylib")', '"ui_core": ui_core'),
            mac_build.reuse_output: ('receipt.get("ui_core") != probe_ui_core(',),
            common.main: ('if args.crate == "ui_core":', 'out / "bin" / UI_CORE_SOURCE', '"inputs": before'),
            windows_assemble.native_records: ('elif name == "narwhal_ui_core.dll":', "verifier.ui_core_record("),
        }
        for function, markers in sources.items():
            source = inspect.getsource(function)
            for marker in markers:
                self.assertIn(marker, source)
        text = (ROOT / "scripts/build_windows_preview.ps1").read_text()
        self.assertRegex(text, r'foreach \(\$Crate in @\([^\n]*"ui_core"\)\)')
        for marker in ('@("ui_core", "narwhal_ui_core.dll")',
                       "'before', $UiInputsPath", "'after', $UiInputsPath, $UiCoreTarget",
                       'Copy-Item -LiteralPath $UiCoreTarget -Destination (Join-Path $AdjacentNativeDirectory "narwhal_ui_core.dll")',
                       "Run-Native $RuntimePythonPath @('-I', '-B', $UiInputsDriver",
                       "'verify', $UiInputsPath, $UiCoreTarget"):
            self.assertIn(marker, text)


def mutation_checks():
    """Re-break real packaging functions/contracts in memory and demand RED."""
    def changed(function, old, new):
        source = textwrap.dedent(inspect.getsource(function))
        if old not in source:
            raise AssertionError(f"mutation anchor absent: {function.__name__}: {old}")
        namespace = dict(function.__globals__)
        exec(compile(source.replace(old, new, 1), "ui-packaging-mutant", "exec"), namespace)
        compiled = namespace[function.__name__]
        mutant = types.FunctionType(compiled.__code__, function.__globals__, function.__name__, compiled.__defaults__)
        mutant.__kwdefaults__ = compiled.__kwdefaults__
        return mutant
    mutants = [
        (common, "UI_CORE_EXPORTS", EXPORTS[:4], "test_header_and_mandatory_component_sets"),
        (common, "COMPONENTS", {k: v for k, v in common.COMPONENTS.items() if k != "ui_core"}, "test_header_and_mandatory_component_sets"),
        (mac_build, "COMPONENTS", {k: v for k, v in mac_build.COMPONENTS.items() if k != "ui_core"}, "test_header_and_mandatory_component_sets"),
        (linux, "PACKAGED_ACCELERATORS", tuple(n for n in linux.PACKAGED_ACCELERATORS if n != SO), "test_linux_actual_copy_block"),
        (linux_verify, "PACKAGED_ACCELERATORS", tuple(n for n in linux_verify.PACKAGED_ACCELERATORS if n != SO), "test_linux_manifest_copies_and_ui_identity"),
        (windows, "GENERATED_PACKAGE_FILES", windows.GENERATED_PACKAGE_FILES - {"narwhal3d/native/" + DLL}, "test_windows_source_replacement_preserves_ui_library"),
        (common, "probe_ui_core", changed(common.probe_ui_core, "identity != UI_CORE_IDENTITY", "False"), "test_probe_receipt_must_match_executed_identity"),
        (common, "verify_ui_core_source", changed(common.verify_ui_core_source, "actual != expected", "False"), "test_source_receipt_rejects_stale_missing_linked_and_changed_binary"),
        (common, "verify_ui_core_source", changed(common.verify_ui_core_source, " or path.is_symlink()", ""), "test_source_receipt_rejects_stale_missing_linked_and_changed_binary"),
        (windows, "verify_ui_core_identity", changed(windows.verify_ui_core_identity, '"narwhal_popup_abi": 1', '"narwhal_popup_abi": 2'), "test_windows_static_abi_old_editor_missing_exports_and_unknown_codegen"),
        (windows, "verify_ui_core_identity", changed(windows.verify_ui_core_identity, 'identity["exports"] != UI_CORE_EXPORTS', "False"), "test_windows_static_abi_old_editor_missing_exports_and_unknown_codegen"),
        (windows, "verify_ui_core_copies", changed(windows.verify_ui_core_copies, "sha256(runtime) != sha256(adjacent)", "False"), "test_windows_copies_source_record_and_manifest_branch"),
        (mac, "stage_ui_core", lambda *args: None, "test_macos_both_slots_probe_and_component_omission"),
        (mac, "verify_ui_core", changed(mac.verify_ui_core, "sha(library) != sha(adjacent)", "False"), "test_macos_both_slots_probe_and_component_omission"),
        (windows_assemble, "native_records", changed(windows_assemble.native_records,
            'record.update(verifier.ui_core_record(\n                package, source_root or Path(__file__).resolve().parents[1]))', 'pass'),
            "test_windows_copies_source_record_and_manifest_branch"),
        (mac_build, "reuse_output", changed(mac_build.reuse_output,
            'receipt.get("ui_core") != probe_ui_core(output / "bin/libnarwhal_ui_core.dylib")', "False"),
            "test_macos_reuse_requires_source_hashes_and_actual_ui_qualification"),
        (linux_verify, "verify_ui_core_manifest", changed(linux_verify.verify_ui_core_manifest,
            'record.get("source_inputs") != expected', "False"), "test_linux_manifest_copies_and_ui_identity"),
        (linux_verify, "verify_ui_core_manifest", changed(linux_verify.verify_ui_core_manifest,
            "any(record.get(key) != value for key, value in UI_CORE_IDENTITY.items())", "False"), "test_linux_manifest_copies_and_ui_identity"),
    ]
    for old, new in (("getattr(lib, name)", "pass"),
                     ('result["editor_abi"] != 1 or result["popup_abi"] != 1', "False"),
                     ("c.sizeof(state)) != 0", "c.sizeof(state)) == 999"),
                     ("output, 1) != 0", "output, 1) == 999"),
                     ("lib.narwhal_editor_close(state.handle) != 0", "lib.narwhal_editor_close(state.handle) == 999")):
        if old not in common.UI_CORE_PROBE:
            raise AssertionError("missing probe mutation anchor")
        mutants.append((common, "UI_CORE_PROBE", common.UI_CORE_PROBE.replace(old, new, 1),
                        "test_executed_probe_rejects_old_editor_wrong_abis_and_every_missing_export"))
    for old, new in (
        (' or result["transcript_abi"] != 1', ''),
        ('result["transcript_capabilities"] != 7', 'False'),
        ('or needed.value != len(payload) or bytes(output) != payload', 'or False'),
        ('lib.narwhal_transcript_release(owner, snapshot.token) != 0',
         'lib.narwhal_transcript_release(owner, snapshot.token) == 999'),
        ('lib.narwhal_transcript_close(owner) != 0', 'lib.narwhal_transcript_close(owner) == 999'),
        ('lib.narwhal_transcript_stats(stats, 4) != 0 or tuple(stats) != expected', 'False'),
        ('(snapshot.handle, snapshot.revision, snapshot.len, snapshot.count, snapshot.bytes, snapshot.kind) != (owner, 2, 2, 2, len(payload), 1)', 'False'),
        ('reply_size) != 1', 'reply_size) == 999'),
    ):
        if old not in common.UI_CORE_PROBE:
            raise AssertionError("missing transcript probe mutation anchor: " + old)
        mutants.append((common, "UI_CORE_PROBE", common.UI_CORE_PROBE.replace(old, new, 1),
                        "test_executed_probe_rejects_old_editor_wrong_abis_and_every_missing_export"))
    mutants.append((common, "UI_CORE_REQUIRED_SOURCE", tuple(n for n in common.UI_CORE_REQUIRED_SOURCE if n != "TRANSCRIPT.md"),
                    "test_required_sources_cannot_be_omitted_from_rehashed_receipts"))
    for symbol in ('narwhal_transcript_abi', 'narwhal_transcript_capabilities'):
        mutants.append((windows, "verify_ui_core_identity", changed(windows.verify_ui_core_identity,
                        '"' + symbol + '": ' + ('1' if symbol.endswith('_abi') else '7') + ',', ''),
                        "test_windows_static_abi_old_editor_missing_exports_and_unknown_codegen"))
    driver = windows_driver()
    for old, new in (("if json.loads(snapshot.read_text()) != inputs:", "if False:"),
                     ("probe_ui_core(library)", "pass"),
                     ('"sha256": digest(library)', '"sha256": "0" * 64')):
        if driver.count(old) != 1:
            raise AssertionError("ambiguous Windows driver mutation")
        mutant_driver = driver.replace(old, new, 1)
        mutants.append((sys.modules[__name__], "windows_driver", lambda text=mutant_driver: text,
                        "test_windows_embedded_driver_before_after_and_packaged_probe"))
    for module, name, mutant, test in mutants:
        with patch.object(module, name, mutant):
            result = unittest.TextTestRunner(stream=io.StringIO()).run(UiPackaging(test))
        if result.wasSuccessful():
            raise AssertionError(f"SURVIVED: {module.__name__}.{name}: {test}")
        restored = unittest.TextTestRunner(stream=io.StringIO()).run(UiPackaging(test))
        if not restored.wasSuccessful():
            raise AssertionError(f"restored control failed: {test}")
        print(f"mutation RED / restored GREEN: {module.__name__}.{name}: {test}")
    print(f"{len(mutants)}/{len(mutants)} UI packaging mutations rejected; production objects restored")


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(UiPackaging))
    if not result.wasSuccessful():
        raise SystemExit(1)
    mutation_checks()
    result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(UiPackaging))
    raise SystemExit(0 if result.wasSuccessful() else 1)
