#!/usr/bin/env python3
"""Focused runtime identity and in-memory mutation witnesses; no Blender UI/gates.

Run with Python 3; --mutations re-breaks only in-memory functions and restores them.
--runtime-probe uses real installed decoder/kernel libraries in the chosen interpreter.
"""
import contextlib
import inspect
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import mock_open, patch

ROOT = Path(__file__).resolve().parents[1]
# Avoid the application's UI __init__; these orchestration modules need no Blender.
package = types.ModuleType("narwhal3d")
package.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = package
from narwhal3d import document_admission as admission, native_geometry as geometry
from narwhal3d.kernel import occt_backend as backend


class Function:
    def __init__(self, result=0):
        self.result = result

    def __call__(self, *args):
        return self.result


def library(path):
    result = types.SimpleNamespace()
    for name in ("abi_version", "capabilities", "edit_session_import",
                 "edit_session_move_mode", "edit_session_release"):
        value = (geometry.ABI_VERSION if name == "abi_version" else
                 geometry.CAP_EDIT_SESSIONS if name == "capabilities" else 0)
        if Path(path).read_bytes() == b"invalid" and name == "abi_version":
            value = 0
        setattr(result, "narwhal_geometry_core_" + name, Function(value))
    return result


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "narwhal3d" / "native_geometry.py"
        self.source.parent.mkdir()
        self.source.write_bytes(b"resolver source")
        self.decoder = self.root / "_rhino3dm.so"
        self.ocp = self.root / "OCP.so"
        self.kernel = self.root / "libTKBRep.so"
        self.unrelated = self.root / "unrelated.so"
        for path in (self.decoder, self.ocp, self.kernel, self.unrelated):
            path.write_bytes(b"binary-original")
        self.modules = {
            "rhino3dm": types.SimpleNamespace(__version__="same-version"),
            "rhino3dm._rhino3dm": types.SimpleNamespace(__file__=str(self.decoder)),
            "OCP": types.SimpleNamespace(__version__="same-version"),
            "OCP.OCP": types.SimpleNamespace(__file__=str(self.ocp)),
        }
        self.modules["OCP"].OCP = self.modules["OCP.OCP"]
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(sys.modules, self.modules))
        self.stack.enter_context(patch.object(backend, "_ocp", self.modules["OCP"]))
        self.stack.enter_context(patch.dict(os.environ))
        os.environ.pop("NARWHAL_GEOMETRY_CORE", None)
        self.stack.enter_context(patch.object(geometry, "__file__", str(self.source)))
        self.stack.enter_context(patch.object(geometry, "_cdll_factory", side_effect=library))
        self.inventory = admission._loaded_library_paths
        self.stack.enter_context(patch.object(admission, "_loaded_library_paths",
                                             return_value={str(self.kernel), str(self.unrelated)}))
        self.stack.enter_context(patch.object(geometry, "_signature", None))
        self.stack.enter_context(patch.object(geometry, "_binding_cache", None))
        self.core = geometry._candidates()[0]
        self.core.parent.mkdir(parents=True)
        self.core.write_bytes(b"core-original")
        admission._binary_hashes.clear()
        admission._loaded_hashes.clear()

    def identity(self):
        return admission.runtime_identity()

    def rewrite(self, path):
        stamp = path.stat()
        path.write_bytes(b"X" * stamp.st_size)
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))

    def test_decoder_same_version_changes(self):
        before = self.identity()
        self.rewrite(self.decoder)
        self.assertNotEqual(before, self.identity())

    def test_ocp_extension_changes(self):
        self.assertEqual(backend.document_kernel_binding(), ("same-version", str(self.ocp)))
        before = self.identity()
        self.rewrite(self.ocp)
        self.assertNotEqual(before, self.identity())

    def test_linked_kernel_changes(self):
        before = self.identity()
        self.rewrite(self.kernel)
        self.assertNotEqual(before, self.identity())

    def test_default_binary_changes(self):
        before = self.identity()
        self.rewrite(self.core)
        self.assertNotEqual(before, self.identity())

    def test_identity_helper_source_changes(self):
        helper = self.root / "document_admission.py"
        helper.write_bytes(b"helper-source")
        with patch.object(admission, "__file__", str(helper)):
            before = self.identity()
            self.rewrite(helper)
            self.assertNotEqual(before, self.identity())

    def test_resolver_source_changes(self):
        before = self.identity()
        self.rewrite(self.source)
        self.assertNotEqual(before, self.identity())

    def test_override_and_actual_selection(self):
        before = self.identity()
        override = self.root / "override.so"
        override.write_bytes(b"override")
        os.environ["NARWHAL_GEOMETRY_CORE"] = str(override)
        after = self.identity()
        self.assertNotEqual(before, after)
        self.assertEqual(geometry._binding_cache.path, override)
        self.assertIn(str(override.resolve()), repr(after))
        self.rewrite(override)
        self.assertNotEqual(after, self.identity())

    def test_bad_first_candidate_uses_actual_abi_qualified_binding(self):
        self.core.write_bytes(b"invalid")
        second = geometry._candidates()[1]
        second.parent.mkdir(parents=True)
        second.write_bytes(b"core-second")
        result = self.identity()
        self.assertEqual(geometry._binding_cache.path, second)
        self.assertEqual(result[-2][0], str(second.resolve()))

    def test_new_default_after_cached_unavailability(self):
        self.core.unlink()
        before = self.identity()
        self.core.write_bytes(b"core-installed")
        self.assertNotEqual(before, self.identity())

    def test_irrelevant_environment_and_library_do_not_change_identity(self):
        before = self.identity()
        os.environ["NARWHAL_UNRELATED"] = "untrusted-claim"
        os.environ[admission.ENVIRONMENT] = "/not-a-runtime-identity"
        os.environ["NARWHAL_RUNTIME_IDENTITY"] = "forged"
        self.rewrite(self.unrelated)
        self.assertEqual(before, self.identity())

    def test_warm_does_not_read_binary_contents_again(self):
        before = self.identity()
        with patch.object(Path, "open", side_effect=AssertionError("binary reread")):
            self.assertEqual(before, self.identity())

    def test_atomic_same_size_same_mtime_replacement(self):
        before = admission._binary_identity(self.decoder)
        stamp = self.decoder.stat()
        replacement = self.root / "replacement"
        replacement.write_bytes(b"Y" * stamp.st_size)
        os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        replacement.replace(self.decoder)
        self.assertNotEqual(before, admission._binary_identity(self.decoder))

    def test_stat_guards_not_cross_process_identity(self):
        before = admission._binary_identity(self.decoder)
        data = self.decoder.read_bytes()
        self.decoder.unlink()
        self.decoder.write_bytes(data)
        admission._binary_hashes.clear()
        self.assertEqual(before, admission._binary_identity(self.decoder))

    def test_loaded_original_is_not_relabelled_after_replacement(self):
        before = self.identity()
        self.rewrite(self.decoder)
        stale_parent = self.identity()
        admission._loaded_hashes.clear()  # a fresh worker observes the new image
        fresh_worker = self.identity()
        self.assertNotEqual(before, stale_parent)
        self.assertNotEqual(stale_parent, fresh_worker)

    def test_missing_required_binary_fails_closed(self):
        self.decoder.unlink()
        with self.assertRaises(OSError):
            self.identity()

    def test_mutation_during_hash_fails_closed(self):
        original = admission.os.fstat
        calls = []
        def changing(fd):
            value = original(fd)
            calls.append(None)
            if len(calls) == 2:
                return types.SimpleNamespace(st_dev=value.st_dev, st_ino=value.st_ino,
                    st_size=value.st_size, st_mtime_ns=value.st_mtime_ns,
                    st_ctime_ns=value.st_ctime_ns + 1)
            return value
        with patch.object(admission.os, "fstat", side_effect=changing):
            with self.assertRaises(RuntimeError):
                admission._binary_identity(self.decoder)

    def test_windows_change_time_guards_cache(self):
        with patch.object(admission, "_change_time", return_value=10):
            admission._binary_identity(self.decoder)
        with patch.object(admission, "_change_time", return_value=11), patch.object(
                Path, "open", side_effect=RuntimeError("cache miss reads bytes")):
            with self.assertRaisesRegex(RuntimeError, "cache miss"):
                admission._binary_identity(self.decoder)

    def test_binary_hash_cache_is_bounded(self):
        with patch.object(admission, "MAX_BINARY_HASHES", 2):
            for path in (self.decoder, self.ocp, self.kernel):
                admission._binary_identity(path)
            self.assertEqual(len(admission._binary_hashes), 2)

    def test_linux_inventory_uses_loaded_paths_and_refuses_stale_images(self):
        # Exercise the real inventory, not the set stub used by the other tests.
        inventory = self.inventory
        value = self.kernel.stat()
        device = format(os.major(value.st_dev), "x") + ":" + format(os.minor(value.st_dev), "x")
        line = "1000-2000 r-xp 0000 " + device + " " + str(value.st_ino) + " " + str(self.kernel) + "\n"
        with patch.object(admission.sys, "platform", "linux"), patch(
                "builtins.open", mock_open(read_data=line)):
            self.assertEqual(inventory(), {str(self.kernel)})
        with patch.object(admission.sys, "platform", "linux"), patch(
                "builtins.open", mock_open(read_data=line.replace(device, "ffff:ffff"))):
            self.assertEqual(inventory(), {str(self.kernel)})
        for path in (self.kernel, self.decoder, self.ocp, self.core):
            deleted = "1000-2000 r-xp 0000 " + device + " " + str(value.st_ino) + " " + str(path) + " (deleted)\n"
            with patch.object(admission.sys, "platform", "linux"), patch(
                    "builtins.open", mock_open(read_data=deleted)):
                with self.assertRaises(RuntimeError):
                    inventory()
        with patch.object(admission.sys, "platform", "linux"), patch(
                "builtins.open", mock_open(read_data=line.replace(str(value.st_ino), "0"))):
            with self.assertRaises(RuntimeError):
                inventory()

    def test_contract_includes_runtime(self):
        native = types.SimpleNamespace(executable=lambda feature: None)
        with patch.object(package, "native_document", native, create=True):
            before = admission.contract()
            self.rewrite(self.decoder)
            self.assertNotEqual(before, admission.contract())


def run_tests(stream=None):
    return unittest.TextTestRunner(stream=stream, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(RuntimeTests))


def mutations():
    cases = [
        ("omit ctime/inode", "_stamp", "value.st_dev, value.st_ino, value.st_size,\n            value.st_mtime_ns, value.st_ctime_ns", "value.st_size, value.st_mtime_ns"),
        ("omit Windows change time", "_binary_identity", "_stamp(before), change_time)", "_stamp(before), None)"),
        ("omit decoder bytes", "runtime_identity", "_loaded_identity(decoder.__file__)", "None"),
        ("omit OCP bytes", "runtime_identity", "_loaded_identity(binding_path)", "None"),
        ("omit linked kernel bytes", "runtime_identity", "binding_path), libraries,", "binding_path), (),"),
        ("omit default geometry bytes", "runtime_identity", "geometry, tuple(candidates)", "None, ()"),
        ("omit identity helper source", "runtime_identity", "_binary_identity(__file__)", "None"),
        ("omit resolver source", "runtime_identity", "_binary_identity(native_geometry.__file__)", "None"),
        ("omit runtime from contract", "contract", "repr(runtime_identity())", "repr(None)"),
        ("relabel stale loaded image", "_loaded_identity", "original, current[1]", "current[1], current[1]"),
        ("reject differing maps device", "_loaded_library_paths", "if current.st_ino != int(fields[4]):", "if current.st_ino != int(fields[4]) or current.st_dev != os.makedev(*(int(x, 16) for x in fields[3].split(\":\"))):"),
        ("trust deleted Linux images", "_loaded_library_paths", "if _runtime_image(path):", "if False:"),
        ("reread large binaries", "_binary_identity", "if cached is not None:", "if False:"),
    ]
    for label, name, old, new in cases:
        original = getattr(admission, name)
        source = inspect.getsource(original)
        assert old in source, label
        exec(compile(source.replace(old, new), "<identity-mutant>", "exec"), admission.__dict__)
        try:
            output = io.StringIO()
            result = run_tests(output)
            if result.wasSuccessful():
                raise AssertionError("SURVIVED: " + label)
            print("MUTATION RED: " + label + " (" + str(len(result.failures) + len(result.errors)) + " failures/errors)", flush=True)
        finally:
            setattr(admission, name, original)
    assert run_tests().wasSuccessful(), "restored code must be GREEN"
    print("RESTORED GREEN", flush=True)


if __name__ == "__main__":
    if "--runtime-probe" in sys.argv:
        print(json.dumps(admission.runtime_identity(), separators=(",", ":")))
    elif "--mutations" in sys.argv:
        assert run_tests().wasSuccessful(), "baseline must be GREEN"
        mutations()
    else:
        sys.exit(not run_tests().wasSuccessful())
