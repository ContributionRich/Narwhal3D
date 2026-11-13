#!/usr/bin/env python3
"""Focused ownership, loader, schema and bounded-allocation checks."""
import ctypes
import importlib.util
import math
import os
from pathlib import Path
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("native_rebuild_owned", ROOT / "narwhal3d/native_rebuild.py")
native = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = native
spec.loader.exec_module(native)
LIB = os.environ.get("NARWHAL_PURE_CORE", str(
    ROOT / "narwhal3d/native" / native._library_name()))

class Function:
    def __init__(self, result): self.result = result
    def __call__(self, *args): return self.result

class FunctionRaises:
    def __init__(self, error): self.error = error
    def __call__(self, *args): raise self.error

class Library:
    def __init__(self, version=1, fit=True):
        self.narwhal_pure_core_abi_version = Function(version)
        if fit: self.narwhal_pure_core_rebuild_fit = Function(0)

class Tests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"NARWHAL_PURE_CORE": LIB})
        self.env.start()
        self.addCleanup(setattr, native, "_binding", native._binding)
        native._binding = None
        self.args = ([(0.,0.,0.), (1.,1.,0.), (2.,0.,0.)], [0.,.5,1.], 3, 2, False, ((0.,0.,0.), (2.,0.,0.)))
    def tearDown(self): self.env.stop()
    def test_loader(self):
        for lib in (Library(2), Library(fit=False), object()):
            with patch.object(native.ctypes, "CDLL", return_value=lib):
                with self.assertRaisesRegex(native.NativeRebuildError, "stale"): native.fit(*self.args)
        with patch.object(native.ctypes, "CDLL", side_effect=OSError("missing")):
            with self.assertRaisesRegex(native.NativeRebuildError, "unavailable"): native.fit(*self.args)
        lib = Library()
        with patch.object(native.ctypes, "CDLL", return_value=lib) as loader:
            native._fit_binding(); binding = native._binding
            native._fit_binding()
            self.assertEqual(loader.call_count, 1)
            self.assertIs(binding.library, lib)
            with self.assertRaises(Exception): binding.signature = "changed"
    def test_signature_switch_and_warm_owner(self):
        owners = [Library() for _ in range(4)]
        records = []
        with patch.object(native.ctypes, "CDLL", side_effect=owners) as loader:
            for explicit, signature in ((None, ("packaged", None)), ("", ("env", "")),
                                        ("<packaged>", ("env", "<packaged>")), (LIB, ("env", LIB))):
                if explicit is None:
                    os.environ.pop(native._ENV, None)
                else:
                    os.environ[native._ENV] = explicit
                record = native._fit_binding()
                records.append(record)
                self.assertEqual(record.signature, signature)
                with patch.object(native, "Path", side_effect=AssertionError("warm Path")), \
                     patch.object(native, "_lock", None):
                    self.assertIs(native._fit_binding(), record)
            self.assertEqual(loader.call_count, 4)
            self.assertEqual(loader.call_args_list[1].args, ("",))
            self.assertEqual(loader.call_args_list[2].args, ("<packaged>",))
        for record, owner in zip(records, owners):
            self.assertIs(record.library, owner)
            self.assertIs(record.function, owner.narwhal_pure_core_rebuild_fit)
        self.assertIsNot(records[0], native._binding)
    def test_native_call_errors_are_runtime(self):
        for error in (ValueError("native"), OSError("native"), ctypes.ArgumentError("native")):
            with patch.object(native, "_fit_binding", return_value=SimpleNamespace(function=FunctionRaises(error))):
                with self.assertRaisesRegex(native.NativeRebuildError, "runtime") as caught:
                    native.fit(*self.args)
                self.assertIs(caught.exception.__cause__, error)
    def test_output(self):
        def fake(kind):
            def fn(*args):
                ctrl, knots, count = args[-3:]
                ctypes.cast(count, ctypes.POINTER(ctypes.c_size_t))[0] = 3 if kind != "count" else 4
                for i, v in enumerate((0.,0.,1.,1.)): knots[i] = v
                if kind == "nan": ctrl[0] = math.nan
                if kind == "knots": knots[1] = .5
                if kind == "status": return -2
                if kind == "exception": raise RuntimeError("boom")
                return 0
            return fn
        for kind in ("count", "nan", "knots", "status", "exception"):
            with self.subTest(kind=kind), patch.object(native, "_fit_binding", return_value=SimpleNamespace(function=fake(kind))):
                with self.assertRaisesRegex(native.NativeRebuildError, "count/capacity" if kind == "count" else "malformed|runtime"): native.fit(*self.args)
    def test_input_and_bounds(self):
        for index, value in ((0, [(0,0)]), (1, [0.]), (1, [0.,math.inf,1.]), (2, 2.5), (2, True), (2, -1), (2, math.inf), (2, 4097), (3, 0), (3, 4), (3, math.nan), (5, ())):
            args = list(self.args); args[index] = value
            with self.subTest(index=index, value=value), self.assertRaises(ValueError): native.fit(*args)
        self.assertEqual(native.fit(self.args[0], self.args[1], 1366, 3, True, ()), "too large")
        self.assertEqual(native.fit(self.args[0], [0.]*3, 8, 3, True, ()), "singular")
        self.assertEqual(native.fit(*self.args),
                         ([(0., 0., 0.), (1., 2., 0.), (2., 0., 0.)], [0., 0., 1., 1.]))
    def test_no_production_oracle(self):
        source = (ROOT / "narwhal3d/rebuild.py").read_text()
        for name in ("_basis", "_solve", "_least_squares", "_fit_open", "_fit_closed", "rebuild_reference"):
            self.assertNotIn(name, source)

if __name__ == "__main__":
    benchmark = "--benchmark" in sys.argv
    if benchmark:
        sys.argv.remove("--benchmark")
    suite = unittest.main(exit=False)
    if not suite.result.wasSuccessful(): raise SystemExit(1)
    if benchmark:
        native._binding = None  # benchmark must load the genuine selected library
        args = ([(0.,0.,0.), (1.,1.,0.), (2.,0.,0.)], [0.,.5,1.], 3, 2, False, ((0.,0.,0.), (2.,0.,0.)))
        native.fit(*args)
        start = time.perf_counter()
        for _ in range(10000): native.fit(*args)
        print(f"warm 3-point fit including Python validation/buffers: {(time.perf_counter()-start)*1e6/10000:.2f} us/call (10000 calls)")
