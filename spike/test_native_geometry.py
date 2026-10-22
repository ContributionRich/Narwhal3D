#!/usr/bin/env python3
"""Focused boundary tests for the ABI-4 native exact-geometry adapter."""
from __future__ import annotations

import ast
import ctypes
import importlib.util
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

NATIVE_SPEC = importlib.util.spec_from_file_location(
    "narwhal_native_geometry_test", ROOT / "narwhal3d" / "native_geometry.py")
assert NATIVE_SPEC is not None and NATIVE_SPEC.loader is not None
native = importlib.util.module_from_spec(NATIVE_SPEC)
NATIVE_SPEC.loader.exec_module(native)


class Fn:
    def __init__(self, call):
        self.call = call
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.call(*args)


class FakeLibrary:
    def __init__(self, abi=4):
        self.imports = []
        self.moves = []
        self.releases = []
        self.next_handle = 1
        self.narwhal_geometry_core_abi_version = Fn(lambda: abi)
        self.narwhal_geometry_core_capabilities = Fn(lambda: 1 << 5)
        self.narwhal_geometry_core_edit_session_import = Fn(self._import)
        self.narwhal_geometry_core_edit_session_move_mode = Fn(self._move)
        self.narwhal_geometry_core_edit_session_release = Fn(self._release)

    def _import(self, source, length, output):
        data = ctypes.string_at(source, length)
        self.imports.append(data)
        if data == b"malformed":
            return -5
        handle = self.next_handle
        self.next_handle += 1
        ctypes.cast(output, ctypes.POINTER(ctypes.c_uint64))[0] = handle
        return 0

    def _move(self, handle, mode, kind, index, dx, dy, dz, output, capacity, certificate):
        row = (int(handle), int(mode), int(kind), int(index), float(dx), float(dy), float(dz))
        self.moves.append(row)
        words = ctypes.cast(certificate, ctypes.POINTER(ctypes.c_uint32))
        words[0], words[1], words[2], words[3] = 248, 4, 0, 0
        if float(dx) == 99.0:
            words[2], words[3] = ctypes.c_uint32(-7).value, 8
            return -7
        payload = ("move:%d:%d:%g,%g,%g" %
                   (int(mode), int(kind), float(dx), float(dy), float(dz))).encode()
        if not output or not capacity:
            return len(payload)
        ctypes.memmove(output, payload, len(payload))
        return len(payload)

    def _release(self, handle):
        self.releases.append(int(handle))
        return 0


class SourceIdentity:
    pass


class NativeGeometryBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.old_env = os.environ.get("NARWHAL_GEOMETRY_CORE")
        os.environ["NARWHAL_GEOMETRY_CORE"] = str(Path(__file__).resolve())
        self.fake = FakeLibrary()
        native._reset_for_tests(lambda _path: self.fake)

    def tearDown(self):
        native._reset_for_tests()
        if self.old_env is None:
            os.environ.pop("NARWHAL_GEOMETRY_CORE", None)
        else:
            os.environ["NARWHAL_GEOMETRY_CORE"] = self.old_env

    def test_abi_session_reuse_modes_refusal_and_malformed_input(self):
        source_identity = SourceIdentity()
        supplied = []

        def supplier():
            supplied.append(True)
            return b"source"

        live = native.move_component(source_identity, supplier, 1, 1, (1, 0, 0), live=True)
        commit = native.move_component(source_identity, supplier, 1, 1, (2, 0, 0), live=False)
        self.assertTrue(live.ok and commit.ok)
        self.assertEqual((len(supplied), self.fake.imports), (1, [b"source"]))
        self.assertEqual([row[1] for row in self.fake.moves], [1, 1, 2, 2])
        self.assertEqual([row[4] for row in self.fake.moves], [1.0, 1.0, 2.0, 2.0])
        refused = native.move_component(source_identity, supplier, 1, 1, (99, 0, 0), live=True)
        self.assertTrue(refused.refused)
        self.assertEqual((refused.status, refused.refusal_reason), (-7, 8))
        malformed = native.move_component(
            SourceIdentity(), lambda: b"malformed", 1, 1, (1, 0, 0))
        self.assertEqual((malformed.kind, malformed.status), ("error", -5))

    def test_eviction_replacement_reset_and_unavailable_signal(self):
        source_identities = [SourceIdentity() for _unused in range(9)]
        for source_identity in source_identities:
            self.assertTrue(native.move_component(
                source_identity, lambda: b"source", 1, 1, (1, 0, 0)).ok)
        self.assertEqual(self.fake.releases, [1])
        native.reset_sessions()
        self.assertEqual(self.fake.releases, list(range(1, 10)))

        first = SourceIdentity()
        self.assertTrue(native.move_component(first, lambda: b"source", 1, 1, (1, 0, 0)).ok)
        os.environ["NARWHAL_GEOMETRY_CORE"] = str(ROOT / "narwhal3d" / "native_geometry.py")
        self.assertTrue(native.move_component(
            SourceIdentity(), lambda: b"source", 1, 1, (1, 0, 0)).ok)
        self.assertIn(10, self.fake.releases)

        native._reset_for_tests(lambda _path: (_ for _ in ()).throw(OSError("missing dependency")))
        unavailable = native.move_component(
            SourceIdentity(), lambda: b"unused", 1, 1, (1, 0, 0))
        self.assertTrue(unavailable.unavailable)
        self.assertIn("could not load", unavailable.detail)

    def test_wrong_abi_and_import_surface_fail_closed(self):
        native._reset_for_tests(lambda _path: FakeLibrary(abi=3))
        self.assertFalse(native.available())
        self.assertIn("expected 4", native.backend_status())

        tree = ast.parse((ROOT / "narwhal3d" / "native_geometry.py").read_text())
        imported = {node.names[0].name.split(".")[0] for node in ast.walk(tree)
                    if isinstance(node, ast.Import)}
        imported.update(node.module.split(".")[0] for node in ast.walk(tree)
                        if (isinstance(node, ast.ImportFrom) and node.module
                            and node.module != "__future__"))
        self.assertLessEqual(imported,
                             {"collections", "ctypes", "os", "pathlib", "sys", "threading"})
        source = (ROOT / "narwhal3d" / "native_geometry.py").read_text()
        self.assertNotIn("OCP", source)
        self.assertNotIn("narwhal3d.kernel", source)
        bridge_source = (ROOT / "narwhal3d" / "bridge.py").read_text()
        ui_source = (ROOT / "narwhal3d" / "ui.py").read_text()
        self.assertIn("native.move_component(", bridge_source)
        self.assertIn("kind.lower(), sub_id, vec, live=True", ui_source)

    def test_real_library_moves_occt_box_bytes(self):
        configured = self.old_env
        if not configured or not Path(configured).is_file():
            self.skipTest("NARWHAL_GEOMETRY_CORE does not name a built release library")
        try:
            package_spec = importlib.util.spec_from_file_location(
                "narwhal_kernel_test", ROOT / "narwhal3d" / "kernel" / "__init__.py",
                submodule_search_locations=[str(ROOT / "narwhal3d" / "kernel")])
            assert package_spec is not None and package_spec.loader is not None
            package = importlib.util.module_from_spec(package_spec)
            sys.modules[package_spec.name] = package
            package_spec.loader.exec_module(package)
            backend_spec = importlib.util.spec_from_file_location(
                "narwhal_kernel_test.occt_backend",
                ROOT / "narwhal3d" / "kernel" / "occt_backend.py")
            assert backend_spec is not None and backend_spec.loader is not None
            occt_backend = importlib.util.module_from_spec(backend_spec)
            backend_spec.loader.exec_module(occt_backend)
            source = occt_backend.shape_to_bytes(
                occt_backend.make_box(4, 5, 6), triangles=False)
        except Exception as exc:
            self.skipTest(f"OCCT Python backend unavailable: {exc}")
        os.environ["NARWHAL_GEOMETRY_CORE"] = configured
        native._reset_for_tests()
        result = native.move_component(
            SourceIdentity(), lambda: source, 1, 1, (1, 0, 0), live=False)
        self.assertTrue(result.ok, (result.kind, result.status, result.refusal_reason, result.detail))
        self.assertTrue(result.data and result.data != source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
