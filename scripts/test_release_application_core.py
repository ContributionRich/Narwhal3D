#!/usr/bin/env python3
"""Host-only metadata/history-core packaging gates; no native build or OS acceptance.

Python tests existing packaging orchestration. Binary parsing uses a synthetic PE,
not a product DLL. Mutants run in memory so other agents never see broken files.
"""
import ast
import copy
import ctypes
import inspect
import io
import json
import linecache
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

import assemble_linux_release as linux
import verify_linux_release as linux_verify
import build_linux_native as linux_build
import build_macos_native as mac_build
import assemble_macos_release as mac
import assemble_windows_release as windows_assemble
import verify_windows_release as windows

ROOT = Path(__file__).resolve().parents[1]
SO = "libnarwhal_application_core.so"
DYLIB = "libnarwhal_application_core.dylib"
DLL = "narwhal_application_core.dll"
LICENSE = "application_core-OPENNURBS.txt"


EXPORTS = ("narwhal_history_abi", "narwhal_history_json",
           "narwhal_session_abi", "narwhal_session_json")


def fake_library(abi=1, history_abi=1, missing=()):
    library = types.SimpleNamespace(narwhal_session_abi=lambda: abi,
                                    narwhal_session_json=lambda: None,
                                    narwhal_history_abi=lambda: history_abi,
                                    narwhal_history_json=lambda: None)
    for symbol in missing:
        delattr(library, symbol)
    return library


def execute_probe(command, **kwargs):
    """Run only a production embedded Python probe; never invoke a native binary."""
    index = command.index("-c")
    output = io.StringIO()
    with patch.object(sys, "argv", ["-c", *command[index + 2:]]), patch.object(sys, "stdout", output):
        try:
            exec(compile(command[index + 1], "packaging-ctypes-probe", "exec"), {})
        except AttributeError as exc:
            raise subprocess.CalledProcessError(1, command, stderr=str(exc)) from exc
    return types.SimpleNamespace(stdout=output.getvalue())


def pe_fixture(abi=1, history_abi=1, missing=()):
    """Small mapped x86-64 DLL with real export RVAs, ordinals and imports."""
    data = bytearray(0x1200)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3c, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", data, 0x84, 0x8664, 1, 0, 0, 0, 240, 0x2002)
    struct.pack_into("<H", data, 0x98, 0x20b)
    struct.pack_into("<I", data, 0x98 + 60, 0x200)
    struct.pack_into("<IIII", data, 0x98 + 112, 0x1000, 0x100, 0x1100, 40)
    struct.pack_into("<IIII", data, 0x188 + 8, 0x1000, 0x1000, 0x1000, 0x200)
    def at(rva):
        return rva - 0x1000 + 0x200
    symbols = [name for name in EXPORTS if name not in missing]
    struct.pack_into("<IIIII", data, at(0x1000) + 20, len(symbols), len(symbols), 0x1050, 0x1040, 0x1060)
    for index, symbol in enumerate(symbols):
        name_rva, function_rva = 0x1080 + 0x20 * index, 0x1200 + 0x20 * index
        struct.pack_into("<I", data, at(0x1040) + 4 * index, name_rva)
        struct.pack_into("<I", data, at(0x1050) + 4 * index, function_rva)
        struct.pack_into("<H", data, at(0x1060) + 2 * index, index)
        value = symbol.encode() + b"\0"
        data[at(name_rva):at(name_rva) + len(value)] = value
        result = history_abi if symbol == "narwhal_history_abi" else abi
        data[at(function_rva):at(function_rva) + 6] = b"\xb8" + struct.pack("<I", result) + b"\xc3"
    for rva, value in ((0x1180, b"KERNEL32.dll\0"), (0x11a2, b"Sleep\0")):
        data[at(rva):at(rva) + len(value)] = value
    struct.pack_into("<IIIII", data, at(0x1100), 0x1140, 0, 0, 0x1180, 0x1140)
    struct.pack_into("<QQ", data, at(0x1140), 0x11a0, 0)

    return bytes(data)


class ApplicationPackaging(unittest.TestCase):
    def test_mandatory_build_and_package_contracts(self):
        self.assertEqual(linux_build.COMPONENTS["application_core"], (SO,))
        self.assertEqual(mac_build.COMPONENTS["application_core"], (DYLIB,))
        for module in (linux, linux_verify):
            self.assertIn(SO, module.NATIVE_COMPONENTS)
            self.assertIn(SO, module.PACKAGED_ACCELERATORS)
            self.assertEqual(module.NATIVE_LIBRARY_ABIS[SO], ("narwhal_session_abi", 1))
        for module in (linux, linux_verify, windows):
            self.assertEqual(module.APPLICATION_CORE_HISTORY_ABI, 1)
            self.assertEqual(module.APPLICATION_CORE_EXPORTS, EXPORTS)
        self.assertEqual(linux.NATIVE_COMPONENTS, linux_verify.NATIVE_COMPONENTS)
        self.assertIn("usr/lib/narwhal3d/runtime/linux-x86_64/bin/" + SO, linux_verify.REQUIRED_PATHS)
        self.assertIn("usr/lib/narwhal3d/narwhal3d/native/" + SO, linux_verify.REQUIRED_PATHS)
        self.assertEqual(windows.NATIVE_COMPONENT_PATHS[DLL], "app/runtime/windows-x86_64/bin/" + DLL)
        self.assertIn("narwhal3d/native/" + DLL, windows.GENERATED_PACKAGE_FILES)
        self.assertIn(windows.APPLICATION_CORE_ADJACENT, windows.REQUIRED)

    def test_source_inventory_and_locked_licenses(self):
        inputs = linux_build.inventory(ROOT, "application_core")
        for name in ("Cargo.toml", "Cargo.lock", "OPENNURBS_LICENSE", "generate_ordinal.rs", "src/ordinal_map.rs"):
            relative = "native/application_core/" + name
            self.assertEqual(inputs[relative], linux_build.digest(ROOT / relative))
        self.assertFalse(any("/target/" in name for name in inputs))
        # Use a small native tree: don't traverse other agents' compiler outputs.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in inputs:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT / relative).read_bytes())
            (root / "launch").mkdir()
            (root / "launch/narwhal3d.png").write_bytes(b"icon")
            self.assertEqual({k: v for k, v in mac_build.source_inputs(root).items()
                              if k.startswith("native/application_core/")}, inputs)
        notices = (ROOT / "licenses/Native-Rust-Dependencies.txt").read_text()
        lock = tomllib.loads((ROOT / "native/application_core/Cargo.lock").read_text())
        for crate in lock["package"]:
            if "source" in crate:
                self.assertIn(crate["name"] + " " + crate["version"] + " —", notices)
        table = (ROOT / "native/application_core/src/ordinal_map.rs").read_text()
        self.assertEqual(len(re.findall(r"^    \(0x", table, re.M)), 205)
        self.assertIn("Native-Rust-Dependencies.txt", linux.REQUIRED_LICENSE_FILES)
        for filename in (LICENSE, "Native-Rust-Dependencies.txt"):
            self.assertIn("usr/lib/narwhal3d/licenses/" + filename, linux_verify.REQUIRED_NOTICE_PATHS)
            self.assertIn("app/licenses/" + filename, windows.REQUIRED)

    def test_linux_missing_copy_manifest_and_wrong_abi_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / linux_verify.APP_ROOT_RELATIVE
            records = {"required_glibc": "2.28"}
            for name in linux_verify.NATIVE_COMPONENTS:
                path = app / linux_verify.NATIVE_COMPONENT_RELATIVE / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(name.encode())
                records[name] = {"size": path.stat().st_size, "sha256": linux_verify.sha256_file(path)}
                if name in linux_verify.PACKAGED_ACCELERATORS:
                    adjacent = app / "narwhal3d/native" / name
                    adjacent.parent.mkdir(parents=True, exist_ok=True)
                    adjacent.write_bytes(path.read_bytes())
            for name, (_, abi) in linux_verify.NATIVE_LIBRARY_ABIS.items():
                records[name]["abi"] = abi
            records["libnarwhal_pure_core.so"].update(linux_verify.PURE_CORE_PARSE_IDENTITY)
            records[SO].update(history_abi=1, exports=list(EXPORTS))
            records["narwhal-welcome"]["launch_contract"] = 1
            linux_verify.verify_native_manifest(root, records)
            for relative in (linux_verify.NATIVE_COMPONENT_RELATIVE / SO, Path("narwhal3d/native") / SO):
                path = app / relative
                data = path.read_bytes()
                path.unlink()
                with self.assertRaises(linux_verify.VerificationError):
                    linux_verify.verify_native_manifest(root, records)
                path.write_bytes(data)
            for change in ("missing", "abi"):
                damaged = copy.deepcopy(records)
                if change == "missing":
                    del damaged[SO]
                else:
                    damaged[SO]["abi"] = 2
                with self.assertRaises(linux_verify.VerificationError):
                    linux_verify.verify_native_manifest(root, damaged)
            for key, value in (("history_abi", 2), ("exports", list(EXPORTS)[1:])):
                for omit in (True, False):
                    damaged = copy.deepcopy(records)
                    if omit:
                        del damaged[SO][key]
                    else:
                        damaged[SO][key] = value
                    with self.assertRaises(linux_verify.VerificationError):
                        linux_verify.verify_native_manifest(root, damaged)
            linux_verify.verify_native_manifest(root, records)

    def test_linux_abi_probe_rejects_actual_wrong_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for result in ("1\n", "2\n"):
                with patch.object(linux.subprocess, "run", return_value=types.SimpleNamespace(stdout=result)) as run:
                    if result == "1\n":
                        self.assertEqual(linux.probe_native_library_abi(root / SO, "narwhal_session_abi", 1, root), 1)
                    else:
                        with self.assertRaises(linux.ReleaseError):
                            linux.probe_native_library_abi(root / SO, "narwhal_session_abi", 1, root)
                    self.assertIn("narwhal_session_abi", run.call_args.args[0])

    def test_linux_history_probe_and_packaged_loader(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for options in ({}, {"abi": 2}, {"history_abi": 2},
                            {"missing": ("narwhal_history_abi", "narwhal_history_json")},
                            *({"missing": (symbol,)} for symbol in EXPORTS)):
                library = fake_library(**options)
                def load(path):
                    if Path(path).name == SO:
                        return library
                    symbol, abi = linux.NATIVE_LIBRARY_ABIS[Path(path).name]
                    return types.SimpleNamespace(**{symbol: lambda: abi})
                with self.subTest(options=options), patch.object(ctypes, "CDLL", side_effect=load) as loaded, \
                     patch.object(linux_verify, "probe_pure_core_parser", return_value=linux_verify.PURE_CORE_PARSE_IDENTITY), \
                     patch.object(linux.subprocess, "run", side_effect=execute_probe):
                    # Run the real assembler's ABI/receipt block, without other native probes.
                    tree = ast.parse(textwrap.dedent(inspect.getsource(linux.validate_native_components)))
                    body = tree.body[0].body
                    start = next(i for i, node in enumerate(body) if isinstance(node, ast.For)
                                 and "NATIVE_LIBRARY_ABIS" in ast.unparse(node.iter))
                    end = next(i for i in range(start + 1, len(body)) if isinstance(body[i], ast.Try))
                    code = compile(ast.Module(body=body[start:end], type_ignores=[]), "linux-abi-staging", "exec")
                    namespace = dict(vars(linux), directory=root, occt_library_dir=root,
                                     records={name: {} for name in linux.NATIVE_COMPONENTS})
                    if options:
                        with self.assertRaises(linux.ReleaseError):
                            exec(code, namespace)
                        with self.assertRaises(linux_verify.VerificationError):
                            linux_verify.verify_native_library_abis(root, Path("python"), {})
                    else:
                        exec(code, namespace)
                        self.assertEqual(namespace["records"][SO],
                                         {"abi": 1, "history_abi": 1, "exports": list(EXPORTS)})
                        loaded.reset_mock()
                        linux_verify.verify_native_library_abis(root, Path("python"), {})
                        paths = [call.args[0] for call in loaded.call_args_list if Path(call.args[0]).name == SO]
                        self.assertEqual(paths, [str(root / "narwhal3d/native" / SO)] * 2)
                        self.assertEqual(library.narwhal_history_abi.argtypes, [])
                        self.assertIs(library.narwhal_history_abi.restype, ctypes.c_uint32)

    def test_linux_packaged_history_receipt_must_match_executed_probe(self):
        expected = {name: abi for name, (_, abi) in linux_verify.NATIVE_LIBRARY_ABIS.items()}
        for history in (None, 2, 1):
            result = dict(expected)
            if history is not None:
                result["application_core_history"] = history
            with patch.object(linux_verify, "probe_pure_core_parser", return_value=linux_verify.PURE_CORE_PARSE_IDENTITY), \
                    patch.object(linux_verify.subprocess, "run", return_value=types.SimpleNamespace(stdout=json.dumps(result))):
                if history == 1:
                    linux_verify.verify_native_library_abis(Path("app"), Path("python"), {})
                else:
                    with self.assertRaises(linux_verify.VerificationError):
                        linux_verify.verify_native_library_abis(Path("app"), Path("python"), {})

    def test_mac_executed_probe_requires_metadata_and_history(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, app = self.mac_fixture(Path(directory))
            for options in ({}, {"abi": 2}, {"history_abi": 2},
                            {"missing": ("narwhal_history_abi", "narwhal_history_json")},
                            *({"missing": (symbol,)} for symbol in EXPORTS)):
                with self.subTest(options=options), patch.object(ctypes, "CDLL", return_value=fake_library(**options)), \
                     patch.object(mac, "run", side_effect=lambda *args, **kw: execute_probe(args).stdout):
                    if options:
                        with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                            mac.verify_application_core(app, Path("python"))
                    else:
                        self.assertEqual(mac.verify_application_core(app, Path("python")), 1)

    def test_mac_receipt_keeps_metadata_and_adds_history_evidence(self):
        tree = ast.parse(textwrap.dedent(inspect.getsource(mac.main)))
        receipt = next(node.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == "receipt" for t in node.targets))
        fields = {key.value: value for key, value in zip(receipt.keys, receipt.values)}
        self.assertEqual(ast.unparse(fields["application_core_abi"]), "application_core_abi")
        self.assertEqual(ast.literal_eval(fields["application_core_history_abi"]), 1)
        self.assertEqual(ast.literal_eval(fields["application_core_exports"]), list(EXPORTS))

    def mac_fixture(self, root):
        source, app = root / "source", root / "app"
        native = app / "runtime/macos-arm64/bin"
        native.mkdir(parents=True)
        (native / DYLIB).write_bytes(b"fixture dylib")
        (source / "native/application_core").mkdir(parents=True)
        (source / "native/application_core/OPENNURBS_LICENSE").write_bytes(b"maintained license")
        (app / "licenses").mkdir()
        (app / "licenses/Native-Rust-Dependencies.txt").write_bytes(b"dependency notices")
        mac.stage_application_core(source, native, app)
        return source, native, app

    def test_mac_stage_and_verify_both_slots_license_and_abi(self):
        with tempfile.TemporaryDirectory() as directory:
            source, native, app = self.mac_fixture(Path(directory))
            adjacent = app / "narwhal3d/native" / DYLIB
            self.assertEqual(adjacent.read_bytes(), (native / DYLIB).read_bytes())
            self.assertEqual((app / "licenses" / LICENSE).read_bytes(), b"maintained license")
            with patch.object(mac, "run", return_value="1\n") as run:
                self.assertEqual(mac.verify_application_core(app, Path("python")), 1)
                self.assertEqual(run.call_args.args[-1], str(adjacent))
                for path in (adjacent, native / DYLIB, app / "licenses" / LICENSE,
                             app / "licenses/Native-Rust-Dependencies.txt"):
                    data = path.read_bytes()
                    path.unlink()
                    with self.assertRaises(ValueError):
                        mac.verify_application_core(app, Path("python"))
                    path.write_bytes(data)
                adjacent.write_bytes(b"different dylib")
                with self.assertRaises(ValueError):
                    mac.verify_application_core(app, Path("python"))
                mac.stage_application_core(source, native, app)
            with patch.object(mac, "run", return_value="2\n"):
                with self.assertRaises(ValueError):
                    mac.verify_application_core(app, Path("python"))
            (native / DYLIB).unlink()
            with self.assertRaises(ValueError):
                mac.stage_application_core(source, native, app)

    def test_mac_receipt_omission_is_not_reusable(self):
        records = {name: {} for names in mac_build.COMPONENTS.values() for name in names}
        mac_build.require_components(records, {})
        del records[DYLIB]
        with self.assertRaises(ValueError):
            mac_build.require_components(records, {})

    def test_windows_pe_wrong_abi_and_missing_export(self):
        with tempfile.TemporaryDirectory() as directory:
            dll = Path(directory) / DLL
            dll.write_bytes(pe_fixture())
            self.assertEqual(windows.verify_application_core_identity(dll)["exports"],
                             EXPORTS)
            for data in (pe_fixture(2), pe_fixture(history_abi=2),
                         pe_fixture(missing=("narwhal_history_abi", "narwhal_history_json")),
                         *(pe_fixture(missing=(symbol,)) for symbol in EXPORTS),
                         pe_fixture().replace(b"\xb8\x01\0\0\0\xc3", b"\x90" * 6)):
                dll.write_bytes(data)
                with self.assertRaises(windows.VerificationError):
                    windows.verify_application_core_identity(dll)
            dll.write_bytes(pe_fixture())
            windows.verify_application_core_identity(dll)

    def test_windows_required_paths_copies_and_source_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "windows-release-manifest.json").write_text("{}")
            paths = [{"path": path} for path in windows.REQUIRED]
            windows.validate_paths(root, paths)
            for relative in (windows.NATIVE_COMPONENT_PATHS[DLL], windows.APPLICATION_CORE_ADJACENT,
                             windows.APPLICATION_CORE_SOURCE, "app/licenses/" + LICENSE):
                with self.assertRaises(windows.VerificationError):
                    windows.validate_paths(root, [row for row in paths if row["path"] != relative])
            for relative in (windows.NATIVE_COMPONENT_PATHS[DLL], windows.APPLICATION_CORE_ADJACENT):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(pe_fixture())
            windows.verify_application_core_copies(root)
            adjacent = root / windows.APPLICATION_CORE_ADJACENT
            adjacent.write_bytes(pe_fixture(2))
            with self.assertRaises(windows.VerificationError):
                windows.verify_application_core_copies(root)
            adjacent.unlink()
            with self.assertRaises(windows.VerificationError):
                windows.verify_application_core_copies(root)
            source_path = root / windows.APPLICATION_CORE_SOURCE
            receipt = {"inputs": linux_build.inventory(ROOT, "application_core"),
                       "sha256": windows.sha256(root / windows.NATIVE_COMPONENT_PATHS[DLL]),
                       "abi": 1, "history_abi": 1}
            source_path.write_text(json.dumps(receipt))
            windows.verify_application_core_source(root, ROOT)
            metadata_only = dict(receipt)
            del metadata_only["history_abi"]
            source_path.write_text(json.dumps(metadata_only))
            with self.assertRaises(windows.VerificationError):
                windows.verify_application_core_source(root, ROOT)
            for key, value in (("inputs", {}), ("sha256", "0" * 64), ("abi", 2), ("history_abi", 2)):
                source_path.write_text(json.dumps(dict(receipt, **{key: value})))
                with self.assertRaises(windows.VerificationError):
                    windows.verify_application_core_source(root, ROOT)
            source_path.unlink()
            with self.assertRaises(windows.VerificationError):
                windows.verify_application_core_source(root, ROOT)

    def test_windows_native_receipt_requires_both_services(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / windows.NATIVE_COMPONENT_PATHS[DLL]
            adjacent = root / windows.APPLICATION_CORE_ADJACENT
            for path in (runtime, adjacent):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(pe_fixture())
            source = root / windows.APPLICATION_CORE_SOURCE
            source.write_text(json.dumps({"inputs": linux_build.inventory(ROOT, "application_core"),
                                          "sha256": windows.sha256(runtime), "abi": 1, "history_abi": 1}))
            with patch.object(windows, "NATIVE_COMPONENT_PATHS", {DLL: windows.NATIVE_COMPONENT_PATHS[DLL]}):
                record = windows_assemble.native_records(root, ROOT)[DLL]
            self.assertEqual(record, {"size": runtime.stat().st_size, "sha256": windows.sha256(runtime),
                                      "abi": 1, "history_abi": 1, "exports": list(EXPORTS),
                                      "machine": "pe32plus-x86-64"})
            # Execute the actual verifier's application-core manifest branch only.
            tree = ast.parse(textwrap.dedent(inspect.getsource(windows.verify_directory)))
            branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                          and ast.unparse(node.test) == "filename == 'narwhal_application_core.dll'")
            code = compile(ast.Module(body=branch.body, type_ignores=[]), "windows-app-manifest", "exec")
            exec(code, dict(vars(windows), path=runtime, record=record))
            for key, value in (("abi", 2), ("history_abi", 2), ("exports", list(EXPORTS)[1:])):
                for omit in (True, False):
                    damaged = dict(record)
                    if omit:
                        del damaged[key]
                    else:
                        damaged[key] = value
                    with self.assertRaises(windows.VerificationError):
                        exec(code, dict(vars(windows), path=runtime, record=damaged))

    def test_windows_source_replacement_preserves_adjacent_and_copies_license(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, package = root / "source", root / "package"
            for name in windows_assemble.SOURCE_DIRECTORIES:
                (source / name).mkdir(parents=True)
            (source / "narwhal3d/__init__.py").write_text("# source")
            for name in windows_assemble.SOURCE_FILES:
                (source / name).write_text("source")
            (source / "native/application_core").mkdir(parents=True)
            (source / "native/application_core/OPENNURBS_LICENSE").write_bytes(b"maintained license")
            adjacent = package / windows.APPLICATION_CORE_ADJACENT
            adjacent.parent.mkdir(parents=True)
            adjacent.write_bytes(pe_fixture())
            windows_assemble.replace_public_source(package, source)
            self.assertEqual(adjacent.read_bytes(), pe_fixture())
            self.assertEqual((package / "app" / windows.APPLICATION_CORE_LICENSE).read_bytes(), b"maintained license")

    def test_assembly_calls_are_mandatory(self):
        mac_main = inspect.getsource(mac.main)
        self.assertIn("stage_application_core(source, native_bin, app)", mac_main)
        self.assertIn("verify_native_library_abis(app_root, python, environment)",
                      inspect.getsource(linux_verify.verify_executables_and_imports))
        self.assertIn("application_core_abi = verify_application_core(app, python)", mac_main)
        self.assertIn('verify_application_core(mounted_app / "Contents/Resources/app",', mac_main)
        self.assertIn("verify_application_core_copies(root)", inspect.getsource(windows.verify_directory))
        self.assertIn("verify_application_core_identity(path)", inspect.getsource(windows.verify_directory))
        self.assertIn("verify_application_core_source(root,", inspect.getsource(windows.verify_directory))
        self.assertIn("verify_application_core_source(package,", inspect.getsource(windows_assemble.native_records))

    def test_linux_real_staging_block_copies_both_slots_and_license(self):
        # Execute only the assembler's file-copy block, never assemble a package.
        tree = ast.parse(textwrap.dedent(inspect.getsource(linux.assemble)))
        block = next(node for node in ast.walk(tree) if isinstance(node, ast.Try)
                     and any(ast.unparse(child).startswith("shutil.copytree(licenses,")
                             for child in node.body))
        start = next(i for i, node in enumerate(block.body)
                     if ast.unparse(node).startswith("shutil.copytree(licenses,"))
        end = next(i for i, node in enumerate(block.body)
                   if ast.unparse(node).startswith("validate_appdir_paths("))
        code = compile(ast.Module(body=block.body[start:end], type_ignores=[]), "linux-staging-block", "exec")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "app"
            app.mkdir()
            source = root / "source"
            licenses = source / "licenses"
            licenses.mkdir(parents=True)
            (licenses / "Native-Rust-Dependencies.txt").write_text("notices")
            (source / "native/application_core").mkdir(parents=True)
            (source / "native/application_core/OPENNURBS_LICENSE").write_bytes(b"maintained license")
            runtime = root / "runtime"
            runtime.mkdir()
            native = root / "native"
            native.mkdir()
            for name in linux.NATIVE_COMPONENTS:
                (native / name).write_bytes(name.encode())
            namespace = dict(vars(linux), licenses=licenses, source_root=source,
                             app_root=app, runtime_dir=runtime, native_components_dir=native)
            exec(code, namespace)
            self.assertEqual((app / linux.NATIVE_COMPONENT_RELATIVE / SO).read_bytes(), SO.encode())
            self.assertEqual((app / "narwhal3d/native" / SO).read_bytes(), SO.encode())
            self.assertEqual((app / "licenses" / LICENSE).read_bytes(), b"maintained license")

    def test_windows_license_is_compared_to_maintained_source_not_exempted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "app" / windows.APPLICATION_CORE_LICENSE
            path.parent.mkdir(parents=True)
            path.write_bytes(b"maintained license")
            with patch.object(windows, "SOURCE_FILES", ()), \
                 patch.object(windows.subprocess, "run", return_value=types.SimpleNamespace(stdout=b"maintained license")) as run:
                windows.verify_public_source(root, "a" * 40)
                self.assertEqual(run.call_args.args[0][-1], "a" * 40 + ":native/application_core/OPENNURBS_LICENSE")
                path.write_bytes(b"wrong license")
                with self.assertRaises(windows.VerificationError):
                    windows.verify_public_source(root, "a" * 40)

    def test_windows_embedded_source_driver_binds_actual_staged_bytes(self):
        text = (ROOT / "scripts/build_windows_preview.ps1").read_text()
        start = text.index("@'\n", text.index('$ApplicationInputsDriver =')) + 3
        end = text.index("'@ | Set-Content -LiteralPath $ApplicationInputsDriver", start)
        code = compile(text[start:end], "application-core-inputs", "exec")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            crate = source / "native/application_core"
            crate.mkdir(parents=True)
            (crate / "Cargo.toml").write_text("source fixture")
            snapshot = root / "before.json"
            dll = root / DLL
            dll.write_bytes(pe_fixture())
            def execute(mode):
                with patch.object(sys, "argv", ["probe", str(source), mode, str(snapshot), str(dll)]), \
                     patch.object(sys, "path", list(sys.path)):
                    exec(code, {})
            execute("before")
            execute("after")
            receipt = json.loads(dll.with_name("application-core-source.json").read_text())
            self.assertEqual(receipt, {"inputs": linux_build.inventory(source, "application_core"),
                                       "sha256": windows.sha256(dll), "abi": 1, "history_abi": 1})
            (crate / "Cargo.toml").write_text("changed source fixture")
            with self.assertRaises(SystemExit):
                execute("after")
            execute("before")
            for data in (pe_fixture(2), pe_fixture(history_abi=2),
                         pe_fixture(missing=("narwhal_history_abi", "narwhal_history_json")),
                         pe_fixture(missing=("narwhal_history_json",))):
                dll.write_bytes(data)
                with self.assertRaises(windows.VerificationError):
                    execute("after")

    def test_windows_build_stage_and_executed_probe_contract(self):
        text = (ROOT / "scripts/build_windows_preview.ps1").read_text()
        self.assertIn('"welcome", "application_core"', text)
        self.assertIn('@("application_core", "narwhal_application_core.dll")', text)
        self.assertIn('Copy-Item -LiteralPath $ApplicationCoreTarget -Destination (Join-Path $AdjacentNativeDirectory "narwhal_application_core.dll")', text)
        self.assertIn('native\\application_core\\OPENNURBS_LICENSE', text)
        self.assertIn("'before', $ApplicationInputsPath", text)
        self.assertIn("'after', $ApplicationInputsPath, $ApplicationCoreTarget", text)
        # Execute just the actual embedded ctypes session probe, not Blender/UI code.
        start = text.index('application = ctypes.CDLL(')
        end = text.index('platform = ctypes.WinDLL', start)
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory)
            native = app / "narwhal3d/native"
            native.mkdir(parents=True)
            runtime = app / "runtime/windows-x86_64/bin"
            runtime.mkdir(parents=True)
            (native / DLL).write_bytes(b"fixture")
            (runtime / DLL).write_bytes(b"fixture")
            for options in ({}, {"abi": 2}, {"history_abi": 2},
                            {"missing": ("narwhal_history_abi", "narwhal_history_json")},
                            *({"missing": (symbol,)} for symbol in EXPORTS)):
                library = fake_library(**options)
                with self.subTest(options=options), patch.object(ctypes, "CDLL", return_value=library):
                    env = dict(ctypes=ctypes, native=native, app=app)
                    if not options:
                        exec(compile(text[start:end], "packaged-session-probe", "exec"), env)
                        self.assertIs(library.narwhal_history_abi.restype, ctypes.c_uint32)
                    else:
                        with self.assertRaises((SystemExit, AttributeError)):
                            exec(compile(text[start:end], "packaged-session-probe", "exec"), env)


def mutation_checks():
    def mutated_function(module, name, old, new):
        source = textwrap.dedent(inspect.getsource(getattr(module, name)))
        if source.count(old) != 1:
            raise AssertionError(f"ambiguous mutation {name}: {old}")
        namespace = dict(vars(module))
        source = source.replace(old, new)
        filename = f"<packaging-mutant-{module.__name__}-{name}-{len(linecache.cache)}>"
        linecache.cache[filename] = (len(source), None, source.splitlines(keepends=True), filename)
        exec(compile(source, filename, "exec"), namespace)
        # Keep live module globals so dependency mocks apply to mutant and control.
        function = types.FunctionType(namespace[name].__code__, vars(module), name,
                                      namespace[name].__defaults__)
        function.__kwdefaults__ = namespace[name].__kwdefaults__
        return patch.object(module, name, function)
    def mutated_windows_driver(old, new):
        path = ROOT / "scripts/build_windows_preview.ps1"
        source = path.read_text()
        if source.count(old) != 1:
            raise AssertionError(f"ambiguous Windows driver mutation: {old}")
        original = Path.read_text
        return patch.object(Path, "read_text", lambda self, *args, **kwargs:
                            source.replace(old, new) if self == path else original(self, *args, **kwargs))

    mutants = [
        ("Linux component omitted", patch.object(linux, "NATIVE_COMPONENTS", tuple(n for n in linux.NATIVE_COMPONENTS if n != SO)), "test_mandatory_build_and_package_contracts"),
        ("Linux wrong ABI accepted", mutated_function(linux, "probe_native_library_abi", "if actual != expected:", "if False:"), "test_linux_abi_probe_rejects_actual_wrong_result"),
        ("Linux adjacent omitted", patch.object(linux_verify, "PACKAGED_ACCELERATORS", tuple(n for n in linux_verify.PACKAGED_ACCELERATORS if n != SO)), "test_linux_missing_copy_manifest_and_wrong_abi_rejected"),
        ("Linux staging copy omitted", patch.object(linux, "PACKAGED_ACCELERATORS", tuple(n for n in linux.PACKAGED_ACCELERATORS if n != SO)), "test_linux_real_staging_block_copies_both_slots_and_license"),
        ("Mac stage omitted", patch.object(mac, "stage_application_core", lambda *args: None), "test_mac_stage_and_verify_both_slots_license_and_abi"),
        ("Mac wrong ABI accepted", mutated_function(mac, "verify_application_core", 'if actual != "1":', "if False:"), "test_mac_stage_and_verify_both_slots_license_and_abi"),
        ("Windows wrong ABI accepted", mutated_function(windows, "inspect_pe_dll", 'if data[offset:offset + 6] != b"\\xb8" + struct.pack("<I", expected) + b"\\xc3":', "if False:"), "test_windows_pe_wrong_abi_and_missing_export"),
        ("Windows adjacent unchecked", patch.object(windows, "verify_application_core_copies", lambda *args: None), "test_windows_required_paths_copies_and_source_receipt"),
        ("Linux build omitted", patch.object(linux_build, "COMPONENTS", {k: v for k, v in linux_build.COMPONENTS.items() if k != "application_core"}), "test_mandatory_build_and_package_contracts"),
        ("Mac build omitted", patch.object(mac_build, "COMPONENTS", {k: v for k, v in mac_build.COMPONENTS.items() if k != "application_core"}), "test_mandatory_build_and_package_contracts"),
        ("Linux license optional", patch.object(linux_verify, "REQUIRED_NOTICE_PATHS", linux_verify.REQUIRED_NOTICE_PATHS - {"usr/lib/narwhal3d/licenses/" + LICENSE}), "test_source_inventory_and_locked_licenses"),
        ("Windows license source exempted", patch.object(windows, "GENERATED_PACKAGE_FILES", windows.GENERATED_PACKAGE_FILES | {windows.APPLICATION_CORE_LICENSE}), "test_windows_license_is_compared_to_maintained_source_not_exempted"),
        ("Windows source unchecked", patch.object(windows, "verify_application_core_source", lambda *args: None), "test_windows_required_paths_copies_and_source_receipt"),
        ("Linux assembly history probe omitted", mutated_function(linux, "validate_native_components",
            'application["history_abi"] = probe_native_library_abi(\n        directory / "libnarwhal_application_core.so", "narwhal_history_abi",\n        APPLICATION_CORE_HISTORY_ABI, occt_library_dir, APPLICATION_CORE_EXPORTS)',
            'application["history_abi"] = APPLICATION_CORE_HISTORY_ABI'), "test_linux_history_probe_and_packaged_loader"),
        ("Linux assembly JSON exports unchecked", mutated_function(linux, "probe_native_library_abi",
            '"[getattr(lib, name) for name in sys.argv[3:]]; "', '""'), "test_linux_history_probe_and_packaged_loader"),
        ("Linux packaged history probe omitted", mutated_function(linux_verify, "verify_native_library_abis",
            '"narwhal_history_abi", APPLICATION_CORE_EXPORTS))',
            '"narwhal_session_abi", APPLICATION_CORE_EXPORTS))'), "test_linux_history_probe_and_packaged_loader"),
        ("Linux packaged JSON exports unchecked", mutated_function(linux_verify, "verify_native_library_abis",
            '[getattr(lib, export) for export in exports]', '[]'), "test_linux_history_probe_and_packaged_loader"),
        ("Linux wrong/missing history probe result accepted", mutated_function(linux_verify, "verify_native_library_abis",
            'if actual_abis != expected_abis:', 'if False:'), "test_linux_packaged_history_receipt_must_match_executed_probe"),
        ("Linux missing history manifest evidence accepted", mutated_function(linux_verify, "verify_native_manifest",
            'if (application.get("history_abi") != APPLICATION_CORE_HISTORY_ABI\n            or application.get("exports") != list(APPLICATION_CORE_EXPORTS)):',
            'if False:'), "test_linux_missing_copy_manifest_and_wrong_abi_rejected"),
        ("Mac history ABI probe omitted", mutated_function(mac, "verify_application_core",
            'for symbol in ("narwhal_session_abi", "narwhal_history_abi"):',
            'for symbol in ("narwhal_session_abi",):'), "test_mac_executed_probe_requires_metadata_and_history"),
        ("Mac wrong history ABI accepted", mutated_function(mac, "verify_application_core",
            'if actual != "1":', 'if symbol == "narwhal_session_abi" and actual != "1":'), "test_mac_executed_probe_requires_metadata_and_history"),
        ("Mac history JSON export unchecked", mutated_function(mac, "verify_application_core",
            'lib.narwhal_history_json; ', ''), "test_mac_executed_probe_requires_metadata_and_history"),
        ("Mac history receipt omitted", mutated_function(mac, "main",
            '"application_core_history_abi": 1,', ''), "test_mac_receipt_keeps_metadata_and_adds_history_evidence"),
        ("Windows history constant proof omitted", mutated_function(windows, "verify_application_core_identity",
            '"narwhal_history_abi": APPLICATION_CORE_HISTORY_ABI,', ''), "test_windows_pe_wrong_abi_and_missing_export"),
        ("Windows missing history JSON export accepted", mutated_function(windows, "verify_application_core_identity",
            'if identity["exports"] != APPLICATION_CORE_EXPORTS:', 'if False:'), "test_windows_pe_wrong_abi_and_missing_export"),
        ("Windows history manifest ABI unchecked", mutated_function(windows, "verify_directory",
            'or record.get("history_abi") != APPLICATION_CORE_HISTORY_ABI', ''), "test_windows_native_receipt_requires_both_services"),
        ("Windows history manifest receipt omitted", mutated_function(windows_assemble, "native_records",
            '"history_abi": verifier.APPLICATION_CORE_HISTORY_ABI,', ''), "test_windows_native_receipt_requires_both_services"),
        ("Windows history source receipt omitted", mutated_windows_driver('"history_abi": 1', '"old_history_abi": 1'),
            "test_windows_embedded_source_driver_binds_actual_staged_bytes"),
        ("Windows executed history ABI wrong accepted", mutated_windows_driver('if application.narwhal_history_abi() != 1:', 'if False:'),
            "test_windows_build_stage_and_executed_probe_contract"),
        ("Windows executed history JSON unchecked", mutated_windows_driver('getattr(application, "narwhal_history_json")', 'pass'),
            "test_windows_build_stage_and_executed_probe_contract"),
    ]
    for label, mutant, test in mutants:
        with mutant:
            result = unittest.TextTestRunner(stream=io.StringIO()).run(ApplicationPackaging(test))
        if result.wasSuccessful():
            raise AssertionError("mutation survived: " + label)
        restored = unittest.TextTestRunner(stream=io.StringIO()).run(ApplicationPackaging(test))
        if not restored.wasSuccessful():
            raise AssertionError("restored test failed: " + label)
        print("RED -> GREEN:", label)
    print(f"{len(mutants)} packaging mutations killed; no source files changed")


if __name__ == "__main__":
    if sys.argv[1:] == ["--mutation-check"]:
        mutation_checks()
    else:
        unittest.main()
