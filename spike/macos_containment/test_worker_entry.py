#!/usr/bin/env python3
"""Test only the Python/native entrypoint seam, without importing Blender geometry."""
import ast
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

SOURCE = (Path(__file__).resolve().parents[2] / "narwhal3d/document_worker.py").read_text()
MUTATION = None


def load():
    text = SOURCE
    if MUTATION:
        old, new = MUTATION
        assert text.count(old) == 1
        text = text.replace(old, new)
    tree = ast.parse(text)
    tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in ("main", "_seal_macos_worker")]
    def fail(message):
        raise RuntimeError(message)
    namespace = dict(ROOT=Path("/trusted/app"), sys=types.SimpleNamespace(platform="darwin", argv=[]), fail=fail)
    exec(compile(tree, "document_worker.py", "exec"), namespace)
    return namespace


class EntryTests(unittest.TestCase):
    def test_packaged_mac_worker_selects_blender_not_embedded_python(self):
        import tempfile
        path = Path(__file__).resolve().parents[2] / "narwhal3d/narwhal_document.py"
        tree = ast.parse(path.read_text())
        tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("_worker_command", "_worker_argv")]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime/Blender.app/Contents/MacOS/Blender"
            runtime.parent.mkdir(parents=True)
            runtime.write_bytes(b"fixture")
            ns = dict(Path=Path, __file__=str(root / "app/narwhal3d/narwhal_document.py"),
                      sys=types.SimpleNamespace(executable=str(root / "runtime/Blender.app/Contents/Resources/5.2/python/bin/python3.13")))
            exec(compile(tree, "narwhal_document.py", "exec"), ns)
            command = ns["_worker_command"](root / "document")
            self.assertEqual(command[0], str(runtime.resolve()))
            self.assertIn("--factory-startup", command)

    def test_missing_policy_refuses(self):
        namespace = load()
        fake = types.SimpleNamespace(CDLL=lambda _: (_ for _ in ()).throw(OSError("missing")))
        with patch.dict(sys.modules, ctypes=fake), self.assertRaisesRegex(RuntimeError, "containment failed"):
            namespace["_seal_macos_worker"]()

    def test_nonzero_policy_refuses(self):
        namespace = load()
        function = lambda: -1
        fake = types.SimpleNamespace(CDLL=lambda _: types.SimpleNamespace(narwhal_seal_document_worker=function), c_int=int)
        with patch.dict(sys.modules, ctypes=fake), self.assertRaisesRegex(RuntimeError, "contract was not established"):
            namespace["_seal_macos_worker"]()

    def test_success_loads_package_policy(self):
        namespace = load()
        calls = []
        function = lambda: calls.append("sealed") or 0
        def library(path):
            calls.append(path)
            return types.SimpleNamespace(narwhal_seal_document_worker=function)
        with patch.dict(sys.modules, ctypes=types.SimpleNamespace(CDLL=library, c_int=int)):
            namespace["_seal_macos_worker"]()
        self.assertEqual(calls, ["/trusted/app/runtime/macos-arm64/bin/libnarwhal_worker_policy.dylib", "sealed"])

    def test_non_mac_does_not_load_policy(self):
        namespace = load()
        namespace["sys"].platform = "linux"
        namespace["_seal_macos_worker"]()

    def test_main_seals_before_parsing_arguments_or_document(self):
        namespace = load()
        def stop():
            raise RuntimeError("sealed before parsing")
        namespace["_seal_macos_worker"] = stop
        with self.assertRaisesRegex(RuntimeError, "sealed before parsing"):
            namespace["main"]()


def run():
    return unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(EntryTests)).wasSuccessful()


if __name__ == "__main__":
    if not run():
        raise SystemExit(1)
    if "--mutations" in sys.argv:
        for MUTATION in (("    _seal_macos_worker()\n", "    # removed entry seal\n"),
                         ("if seal() != 0:", "if False:"),
                         ('if sys.platform != "darwin":', "if True:")):
            if run():
                raise SystemExit("entrypoint mutation survived")
            print("EXPECTED RED:", MUTATION[0].strip())
        MUTATION = None
        if not run():
            raise SystemExit(1)
