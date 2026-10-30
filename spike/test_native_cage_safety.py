"""Bounded FFI safety tests; no Blender, rhino3dm, or native library is loaded.

Run with python3 -B spike/test_native_cage_safety.py [--mutations].
--rust-mutations additionally compiles just cage.rs plus the real slice helpers
from lib.rs, using stdin and a temporary binary in tmp/. Run that mode under an
external 512 MiB / 90s limit. Production sources are never mutated on disk.
"""

import io
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "narwhal3d/native_cage.py").read_text()
ORIGIN = (0.0, 0.0, 0.0)
AXES = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
FRAME = (ORIGIN, AXES, (1, 1, 1), (2, 2, 2))
LATTICE = [(0.0, 0.0, 0.0)] * 8


def load_adapter(source):
    # Compile source directly: no stale pyc, package initialization, or native load.
    package = types.ModuleType("_cage_safety")
    package.native_surface = types.SimpleNamespace(_lib=lambda: None)
    module = types.ModuleType("_cage_safety.native_cage")
    module.__package__ = "_cage_safety"
    with patch.dict(sys.modules, {"_cage_safety": package}):
        exec(compile(source, str(ROOT / "narwhal3d/native_cage.py"), "exec"), module.__dict__)
    return module


def forbidden(*args, **kwargs):
    raise AssertionError("unsafe boundary reached (sentinel, no allocation/native call)")


class SizedOnly:
    def __init__(self, count):
        self.count = count

    def __len__(self):
        return self.count

    def __iter__(self):
        return forbidden()


class LengthMismatch(list):
    def __len__(self):
        return super().__len__() + 1


class AdapterSafety(unittest.TestCase):
    source = SOURCE

    def setUp(self):
        self.mod = load_adapter(self.source)
        self.mod._bind = lambda: (forbidden, forbidden)

    def reject_frame(self, frame):
        self.assertIsNone(self.mod.rest_points(*frame))
        self.assertIsNone(self.mod.deform(*frame, LATTICE, [(0, 0, 0)]))
        self.assertIsNone(self.mod.deform(*frame, LATTICE, []))

    def test_counts_rejected_before_any_array_allocation(self):
        # Even a missing guard cannot allocate: the module-local array is a sentinel.
        self.mod.array = types.SimpleNamespace(array=forbidden)
        for counts in [(1000,) * 3, (64, 65, 64), (0, 2, 2), (-1, 2, 2),
                       (2**64, 2, 2), (2.5, 2, 2), (2.0, 2, 2), ("2", 2, 2),
                       (float("inf"), 2, 2), (float("nan"), 2, 2)]:
            with self.subTest(counts=counts):
                self.reject_frame((*FRAME[:3], counts))

    def test_shape_lengths_and_types(self):
        for index in (1, 2, 3):
            for value in (None, (), FRAME[index][:2], (*FRAME[index], FRAME[index][0]),
                          {"x": 1, "y": 2, "z": 3}, LengthMismatch(FRAME[index][:2])):
                with self.subTest(index=index, value=value):
                    frame = list(FRAME)
                    frame[index] = value
                    self.reject_frame(frame)
        for degrees in [(-1, 1, 1), (2, 1, 1), (2**64, 1, 1), (0.5, 1, 1)]:
            self.reject_frame((ORIGIN, AXES, degrees, FRAME[3]))

    def test_frame_xyz_lengths_conversion_and_finiteness(self):
        for xyz in [None, (), (1, 2), (1, 2, 3, 4), "123", (object(), 0, 0),
                    {"x": 1, "y": 2, "z": 3}, LengthMismatch([1, 2]),
                    (10**400, 0, 0), (float("nan"), 0, 0), (float("inf"), 0, 0)]:
            with self.subTest(xyz=xyz):
                self.reject_frame((xyz, *FRAME[1:]))
                for axis in range(3):
                    axes = list(AXES)
                    axes[axis] = xyz
                    self.reject_frame((ORIGIN, axes, *FRAME[2:]))

    def test_malformed_point_and_lattice_entries_never_call_native(self):
        for xyz in [None, (), (1, 2), (1, 2, 3, 4), "123", (object(), 0, 0),
                    {"x": 1, "y": 2, "z": 3}, LengthMismatch([1, 2]),
                    (10**400, 0, 0), (float("nan"), 0, 0), (float("inf"), 0, 0)]:
            with self.subTest(xyz=xyz):
                self.assertIsNone(self.mod.deform(*FRAME, LATTICE, [xyz]))
                lattice = [xyz, *LATTICE[1:]]
                self.assertIsNone(self.mod.deform(*FRAME, lattice, [(0, 0, 0)]))
                self.assertIsNone(self.mod.deform(*FRAME, lattice, []))

    def test_collection_lengths_checked_before_iteration(self):
        for lattice in [None, [], LATTICE[:-1], LATTICE + [(0, 0, 0)], SizedOnly(9)]:
            self.assertIsNone(self.mod.deform(*FRAME, lattice, [(0, 0, 0)]))
        self.assertIsNone(self.mod.deform(*FRAME, LATTICE, None))
        self.assertIsNone(self.mod.deform(*FRAME, LATTICE, SizedOnly(10_000_001)))

    def test_flat_buffer_lengths_match_declared_counts(self):
        self.assertIsNone(self.mod.deform(*FRAME, LengthMismatch(LATTICE[:-1]), [(0, 0, 0)]))
        self.assertIsNone(self.mod.deform(*FRAME, LATTICE, LengthMismatch([(0, 0, 0)])))

    def test_frame_layout_and_inclusive_count_limit(self):
        frame = self.mod._frame((11, 12, 13), AXES, (63, 1, 0), (64, 2, 1))
        self.assertIsNotNone(frame)
        self.assertEqual(list(frame[1]), [11, 12, 13, 1, 0, 0, 0, 1, 0, 0, 0, 1])
        self.assertEqual(list(frame[3]), [63, 1, 0, 64, 2, 1])

    def test_valid_buffers_and_output_layout(self):
        calls = []

        def rest(f, s, out, capacity):
            self.assertEqual(len(f), 12)
            self.assertEqual(list(s), [1, 1, 1, 2, 2, 2])
            self.assertEqual(capacity, 24)
            self.assertEqual(len(out), capacity)
            for i in range(capacity):
                out[i] = i + 0.25
            calls.append("rest")
            return 8

        def deform(f, s, lattice, points, count, out):
            self.assertEqual(len(lattice), 24)
            self.assertEqual(list(lattice), [0.0] * 24)
            self.assertEqual(len(points), 3 * count)
            self.assertEqual(len(out), len(points))
            for i, value in enumerate(points):
                out[i] = value + 1
            calls.append("deform")
            return count

        self.mod._bind = lambda: (rest, deform)
        self.assertEqual(self.mod.rest_points(*FRAME),
                         [tuple(3 * i + c + 0.25 for c in range(3)) for i in range(8)])
        self.assertEqual(self.mod.deform(*FRAME, LATTICE, [(1, 2, 3), (4, 5, 6)]),
                         [(2, 3, 4), (5, 6, 7)])
        self.assertEqual(self.mod.deform(*FRAME, LATTICE, []), [])
        self.assertEqual(calls, ["rest", "deform"])

    def test_unavailable_and_native_rejections(self):
        self.mod._bind = lambda: None
        self.assertIsNone(self.mod.rest_points(*FRAME))
        self.assertIsNone(self.mod.deform(*FRAME, LATTICE, [(0, 0, 0)]))
        for result in (-1, -2):
            self.mod._bind = lambda: (lambda *args: result, lambda *args: result)
            self.assertIsNone(self.mod.rest_points(*FRAME))
            self.assertIsNone(self.mod.deform(*FRAME, LATTICE, [(0, 0, 0)]))
        self.mod._bind = lambda: (lambda *args: 7, forbidden)
        self.assertIsNone(self.mod.rest_points(*FRAME))


def suite(source=SOURCE):
    case = type("SafetyCase", (AdapterSafety,), {"source": source})
    return unittest.defaultTestLoader.loadTestsFromTestCase(case)


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise AssertionError(f"mutation anchor is not unique: {old!r}")
    return source.replace(old, new, 1)


def adapter_mutations():
    mutations = [
        ("count allocation bound", "not 1 <= n <= _MAX_COUNT", "not 1 <= n"),
        ("exact frame dimensions", "len(axes) != 3 or len(degrees) != 3 or len(counts) != 3", "False"),
        ("exact xyz dimensions", "len(point) != 3", "len(point) < 3"),
        ("finite xyz", "not all(math.isfinite(c) for c in xyz)", "False"),
        ("lattice count", "len(lattice) != total or count > _MAX_POINTS", "count > _MAX_POINTS"),
        ("point count bound", "len(lattice) != total or count > _MAX_POINTS", "len(lattice) != total"),
        ("flat buffer size", "len(cage) != 3 * total or len(pts) != 3 * count", "False"),
        ("conversion refusal", "except _INPUT_ERRORS:", "except IndexError:"),
    ]
    for name, old, new in mutations:
        # Both conversion sites need to obey the same invalid=>None contract.
        changed = SOURCE.replace(old, new) if name == "conversion refusal" else replace_once(SOURCE, old, new)
        result = unittest.TextTestRunner(stream=io.StringIO()).run(suite(changed))
        if result.wasSuccessful():
            raise AssertionError(f"SURVIVED: {name}")
        print(f"RED: {name} ({len(result.failures)} failures, {len(result.errors)} errors)")
    if not unittest.TextTestRunner(verbosity=1).run(suite()).wasSuccessful():
        raise AssertionError("restored adapter failed")
    print(f"ADAPTER MUTATIONS: {len(mutations)} RED; restored GREEN")


def rust_mutations():
    source = (ROOT / "native/pure_core/src/cage.rs").read_text()
    lib = (ROOT / "native/pure_core/src/lib.rs").read_text()
    # Reuse the actual ABI helpers, not a permissive stand-in. No full crate build.
    helpers = lib[lib.index("use std::mem::{"):lib.index("/// Return the stable ABI version")]
    cases = [
        ("baseline", source, True),
        ("positive degree", replace_once(source, "length2 == 0.0 || degrees[d] == 0", "length2 == 0.0"), False),
        ("finite squared length", replace_once(source, "!length2.is_finite()", "false"), False),
        ("underflowed nonzero axis", replace_once(source, "length2 == 0.0 || degrees[d] == 0", "degrees[d] == 0"), False),
        ("restored", source, True),
    ]
    for name, text, expected in cases:
        with tempfile.NamedTemporaryFile(prefix="cage-safety-", dir=ROOT / "tmp", delete=False) as binary:
            binary_path = Path(binary.name)
        try:
            wrapper = helpers + "\nmod cage {\n" + text + "\n}\n"
            built = subprocess.run(
                ["rustc", "--edition=2021", "--test", "-C", "codegen-units=1", "-A", "dead_code",
                 "-o", str(binary_path), "-"], input=wrapper, text=True, capture_output=True, timeout=30)
            if built.returncode:
                raise AssertionError(f"Rust compile failed ({name}): {built.stderr}")
            tested = subprocess.run([str(binary_path), "cage::tests", "--test-threads=1"],
                                    text=True, capture_output=True, timeout=10)
            if (tested.returncode == 0) != expected or tested.returncode not in (0, 101):
                raise AssertionError(f"unexpected Rust result ({name}): {tested.stdout}\n{tested.stderr}")
            print(f"RUST {name}: {'GREEN' if expected else 'RED'}")
            print(tested.stdout.strip())
        finally:
            binary_path.unlink(missing_ok=True)


if __name__ == "__main__":
    if not unittest.TextTestRunner(verbosity=2).run(suite()).wasSuccessful():
        sys.exit(1)
    if "--mutations" in sys.argv:
        adapter_mutations()
    if "--rust-mutations" in sys.argv:
        rust_mutations()
