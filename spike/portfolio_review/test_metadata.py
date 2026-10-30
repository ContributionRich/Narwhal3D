#!/usr/bin/env python3
"""Real in-process boundary parity, fallback tests and end-to-end timings."""
import argparse
import ast
import ctypes
import importlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import struct
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
p = argparse.ArgumentParser()
p.add_argument("--library", default=os.environ.get("NARWHAL_PURE_CORE"))
p.add_argument("--bench", action="store_true")
args = p.parse_args()
if not args.library:
    p.error("--library or NARWHAL_PURE_CORE is required; native tests may not silently skip")
os.environ["NARWHAL_PURE_CORE"] = str(Path(args.library).resolve())
package = types.ModuleType("metadata_review_app")
package.__path__ = [str(ROOT / "narwhal3d")]
sys.modules[package.__name__] = package
aci = importlib.import_module(package.__name__ + ".aci_colors")
widths = importlib.import_module(package.__name__ + ".printwidths")
native = importlib.import_module(package.__name__ + ".native_metadata")
# Retain the original Python calculation: remove ONLY the newly added optional
# dispatch from its AST. Expectations also include independent fixed results.
tree = ast.parse((ROOT / "narwhal3d/aci_colors.py").read_text())
function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "rgb_to_aci")
function.body = [n for n in function.body if not isinstance(n, ast.Try)
                 and not (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "exact" for t in n.targets))
                 and not (isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
                          and isinstance(n.test.left, ast.Name) and n.test.left.id == "exact")]
reference_ns = dict(aci.__dict__)
exec(compile(ast.Module(body=[function], type_ignores=[]), "original-aci-calculation", "exec"), reference_ns)
reference_rgb = reference_ns["rgb_to_aci"]


class MetadataTests(unittest.TestCase):
    def test_native_exports_are_really_used(self):
        before = native.status()["calls"]
        self.assertEqual(aci.rgb_to_aci((160, 160, 160)), (8, False))
        self.assertEqual(native.widths_from_text("2\n1\n2\nNaN"), widths._widths_from_text("2\n1\n2\nNaN"))
        after = native.status()["calls"]
        self.assertEqual(after["rgb"], before["rgb"] + 1)
        self.assertEqual(after["widths"], before["widths"] + 1)

    def test_colors_differential_and_ties(self):
        rng = random.Random(9081)
        values = [(255, 0, 0), (255, 127.5, 0), (0, 127.5, 255), (160, 160, 160)]
        values += [tuple(rng.uniform(0, 255) for _ in range(3)) for _ in range(5000)]
        for rgb in values:
            self.assertEqual(aci.rgb_to_aci(rgb), reference_rgb(rgb), rgb)

    def test_python_coercion_and_errors_stay_authoritative(self):
        class FloatSubclass(float):
            pass
        class ListSubclass(list):
            pass
        for rgb in (None, "red", [], [True, 2, 3], [float("nan"), 0, 0], [10**80, 0, 0], [1, 2, "3"]):
            with self.assertRaises(ValueError) as original:
                reference_rgb(rgb)
            with self.assertRaises(ValueError) as current:
                aci.rgb_to_aci(rgb)
            self.assertEqual(str(original.exception), str(current.exception))
        self.assertEqual(aci.rgb_to_aci(ListSubclass([FloatSubclass(255), 0, 0])), (1, True))
        self.assertEqual(aci.aci_to_rgb(10), (None, "ACI 10-255 not implemented yet"))

    def test_widths_differential(self):
        rng = random.Random(450)
        alphabet = "0123456789eE_+-. infaN;/'\"\n\r\t\x00\x1c\u00a0\u0661\u2028"
        cases = ["", "\ufeff0.5", ";comment\r\n0.5\r0.5\rNaN", "1_0\n١.٥\nInfinity", "2\n1\n2\n0\nbad"]
        cases += ["".join(rng.choice(alphabet) for _ in range(rng.randrange(250))) for _ in range(2000)]
        for text in cases:
            actual = native.widths_from_text(text)
            if actual is None and not text.isascii() and native.unicodedata.unidata_version != "15.1.0":
                continue
            self.assertEqual(actual, widths._widths_from_text(text), repr(text))
        self.assertIsNone(native.widths_from_text("\ud800"))

    def test_file_io_newlines_decoding_and_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "widths.txt"
            file.write_bytes(b";comment\r\n" * 30 + b"2\r1\r\n0\r\nbad")
            before = native.status()["calls"]["widths"]
            self.assertEqual(widths.read_widths(file), widths._widths_from_text(file.read_text()))
            self.assertEqual(native.status()["calls"]["widths"], before + 1)
            file.write_bytes(b"1\n2")
            self.assertEqual(widths.read_widths(file), ([1.0, 2.0], []))
            self.assertEqual(native.status()["calls"]["widths"], before + 1)
            file.write_bytes(b"\xff")
            with self.assertRaises(UnicodeDecodeError): widths.read_widths(file)
            with self.assertRaises(FileNotFoundError): widths.read_widths(file.with_name("absent"))

    def test_missing_or_malformed_native_routes_fall_back(self):
        with patch.object(native, "_bindings", return_value=(None, None, None)):
            self.assertEqual(aci.rgb_to_aci((160, 160, 160)), (8, False))
            self.assertIsNone(native.widths_from_text("1\n2"))
        with patch.object(native, "_bindings", return_value=(None, lambda *_: 9999, None)):
            self.assertEqual(aci.rgb_to_aci((160, 160, 160)), (8, False))
        for payload in (struct.pack("<I", 0xffffffff) + b"\x00" * 4,
                        struct.pack("<IdI", 1, float("nan"), 0),
                        struct.pack("<IddI", 2, 2.0, 1.0, 0),
                        struct.pack("<III", 0, 1, 1) + b"\xff"):
            def broken(data, length, output, capacity, needed):
                ctypes.memmove(output, payload, len(payload))
                ctypes.cast(needed, ctypes.POINTER(ctypes.c_size_t))[0] = len(payload)
                return 0
            with patch.object(native, "_bindings", return_value=(None, None, broken)):
                self.assertIsNone(native.widths_from_text("abc"))


def median_ns(function, loops):
    rounds = []
    for _ in range(5):
        start = time.perf_counter_ns()
        for _ in range(loops): function()
        rounds.append((time.perf_counter_ns() - start) / loops)
    return round(statistics.median(rounds))


result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(MetadataTests))
if not result.wasSuccessful(): raise SystemExit(1)
if args.bench:
    records = []
    for rgb in ((255, 0, 0), (160, 160, 160), (13.2, 111.7, 199.4)):
        records.append(dict(case="rgb-" + str(rgb), python_ns=median_ns(lambda: reference_rgb(rgb), 20000),
                            native_ns=median_ns(lambda: aci.rgb_to_aci(rgb), 20000)))
    with tempfile.TemporaryDirectory() as directory:
        file = Path(directory) / "widths.txt"
        for repetitions in (1, 150, 3000):
            text = "; standard widths\n0.13\n0.18\n0.25\n0.35\n0.5\n0.7\n1\n2\n" * repetitions
            file.write_text(text)
            def baseline():
                with file.open(encoding="utf-8") as f: return widths._widths_from_text(f.read())
            loops = max(3, 1500 // repetitions)
            records.append(dict(case="file-" + str(len(text)), python_ns=median_ns(baseline, loops),
                                native_ns=median_ns(lambda: widths.read_widths(file), loops)))
    print(json.dumps(records, indent=2))
    print("Native calls:", native.status()["calls"])
