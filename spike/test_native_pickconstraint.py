#!/usr/bin/env python3
"""Focused v1 ABI, public behavior, differential and complete-adapter benchmarks.

Set NARWHAL_PURE_CORE to a source-built library. --benchmark compares the retained
compatibility path with binding disabled outside timed loops. Optionally pass
--oracle-dir DIR containing pickconstraint_oracle.py and ortho_oracle.py to compare
unchanged archived implementations instead. No private files are required.
"""
import ctypes
import importlib.util
import math
import os
from pathlib import Path
import random
import statistics
import sys
import timeit
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def load(name, path):
    spec = importlib.util.spec_from_file_location("narwhal3d." + name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


from narwhal3d import native_pickconstraint as native, pickconstraint as pc, ortho, cplane


class NativePickTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if native.binding() is None:
            raise RuntimeError("source-built pure-core with pickconstraint v1 required")

    def close(self, a, b):
        if a is None or b is None:
            self.assertIs(a, b)
        else:
            for x, y in zip(a, b):
                self.assertTrue(math.isclose(x, y, rel_tol=2e-12, abs_tol=2e-10), (a, b))

    def test_public_contract(self):
        p = pc.PickConstraint()
        plane = cplane.WORLD_FRONT
        p.set_angle("a", (1, 2, 3), plane, 90)
        self.close(p.set_distance("a", (1, 2, 3), plane, 4), (1, 2, 7))
        self.close(p.apply((-80, 50, -9)), (1, 2, 7))
        self.assertFalse(p.active_for("b"))
        self.assertIsNone(p.base)
        self.assertIsNone(p.tab("b", (0, 0, 0), plane, (0, 0, 0)))
        self.assertEqual(p.tab("b", (0, 0, 0), plane, (3, 0, 4)), "direction")
        self.close(p.apply((8, 0, 1)), (3.36, 0, 4.48))
        self.assertEqual(p.tab("b", (0, 0, 0), plane, (6, 0, 8)), "circle")
        self.close(p.apply((3, 9, 4)), (6, 0, 8))
        self.assertIsNone(p.tab("b", (0, 0, 0), plane, (3, 0, 4)))
        p.set_distance("c", (0, 0, 0), plane, -2)
        self.assertEqual(p.apply((5, 0, 0)), (4, 0, 0))
        self.assertEqual(p.apply((7, 0, 0)), (8, 0, 0))
        self.assertEqual(native.constraint(p, 0, (5, 0, 0)), (4, 0, 0))
        self.assertEqual(native.constraint(p, 0, (7, 0, 0)), (8, 0, 0))
        self.assertEqual(ortho.constrain((2, 3, 5), (3, 10, 99), cplane.WORLD_TOP), (2, 10, 5))
        for x, y in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            self.assertEqual(ortho.constrain((0, 0, 0), (x, y, 0), cplane.WORLD_TOP), (x, 0, 0))
        p.reset()
        p.tab("circle", (0, 0, 0), cplane.WORLD_TOP, (3, 0, 0))
        p.tab("circle", (0, 0, 0), cplane.WORLD_TOP, (3, 0, 0))
        p.set_distance("circle", (0, 0, 0), cplane.WORLD_TOP, 2)
        self.assertEqual(p.apply((0, 0, 8)), (0, 0, 8))
        p.reset()
        p.set_distance("z", (1, 2, 3), plane, 2)
        self.assertEqual(p.apply((1, 2, 3)), (1, 2, 3))
        self.assertIsNone(p.apply(None))
        self.assertEqual(pc.parse("0"), None)
        self.assertEqual(pc.parse("<90"), ("angle", 90))
        self.assertEqual(pc.parse("-2"), ("distance", -2))

    def test_differential(self):
        rng = random.Random(74219)
        planes = [cplane.WORLD_TOP, cplane.WORLD_FRONT,
                  cplane.CPlane((5, -7, 9), (1, 2, 3), (-2, 1, 0))]
        for i in range(1200):
            plane = planes[i % 3]
            base = tuple(rng.uniform(-100, 100) for _ in range(3))
            cursor = tuple(rng.uniform(-150, 150) for _ in range(3))
            p = pc.PickConstraint()
            q = pc.PickConstraint()
            actions = []
            if i % 4:
                actions.append(("set_angle", rng.uniform(-720, 720)))
            if i % 5:
                actions.append(("set_distance", rng.choice([-1, 1]) * rng.uniform(.01, 30)))
            if i % 2:
                actions.reverse()
            for method, value in actions:
                actual = getattr(p, method)("key", base, plane, value)
                with patch.object(native, "binding", return_value=None):
                    expected = getattr(q, method)("key", base, plane, value)
                self.close(actual, expected)
            for _ in range(i % 4):
                actual = p.tab("key", base, plane, cursor)
                with patch.object(native, "binding", return_value=None):
                    expected = q.tab("key", base, plane, cursor)
                self.assertEqual(actual, expected)
                self.close(p.lock_dir, q.lock_dir)
                if p.lock_radius is not None:
                    self.assertAlmostEqual(p.lock_radius, q.lock_radius, places=10)
            expected = self.python_apply(q, cursor)
            self.close(p.apply(cursor), expected)
            if p.base is not None:
                self.close(native.constraint(p, 0, cursor), expected)
            step = rng.choice([15, 30, 45, 90, 125, .01])
            actual = ortho.constrain(base, cursor, plane, step)
            with patch.object(native, "binding", return_value=None):
                expected = ortho.constrain(base, cursor, plane, step)
            self.close(actual, expected)

    @staticmethod
    def python_apply(p, cursor):
        with patch.object(native, "binding", return_value=None):
            return p.apply(cursor)

    def test_ffi_no_partial_output(self):
        fn = native.binding()
        a = (ctypes.c_double * 32)()
        out = (ctypes.c_double * 4)(*([12345] * 4))
        cases = [(99, a, 32, out, 4), (0, None, 32, out, 4),
                 (0, a, 31, out, 4), (0, a, 32, out, 3),
                 (0, a, 32, None, 4)]
        for args in cases:
            self.assertLess(fn(*args), 0)
            self.assertEqual(list(out), [12345] * 4)
        for value in [math.nan, math.inf, -math.inf]:
            a[3] = value
            self.assertLess(fn(0, a, 32, out, 4), 0)
            self.assertEqual(list(out), [12345] * 4)
        a[3] = 0
        self.assertEqual(fn(2, a, 32, out, 4), 1)
        self.assertEqual(list(out), [12345] * 4)
        a[24] = 5
        self.assertLess(fn(0, a, 32, out, 4), 0)
        self.assertEqual(list(out), [12345] * 4)
        a[24] = 1
        a[23] = 1e308
        a[28] = 1
        a[18] = 1e308
        self.assertLess(fn(1, a, 32, out, 4), 0)
        self.assertEqual(list(out), [12345] * 4)

    def test_ffi_alias_alignment_and_event_packet(self):
        fn = native.binding()
        a = (ctypes.c_double * 32)(*range(32))
        out = (ctypes.c_double * 4)(*([12345] * 4))
        before = bytes(a)
        self.assertEqual(fn(0, a, 32, a, 4), -1)
        self.assertEqual(bytes(a), before)
        overlap = ctypes.cast(ctypes.byref(a, 24), native._D)
        self.assertEqual(fn(0, a, 32, overlap, 4), -1)
        self.assertEqual(bytes(a), before)
        raw = ctypes.create_string_buffer(264)
        bad = ctypes.cast(ctypes.addressof(raw) + 1, native._D)
        self.assertEqual(fn(0, bad, 32, out, 4), -1)
        self.assertEqual(fn(0, a, 32, bad, 4), -1)
        overflow = ctypes.cast(ctypes.c_void_p((1 << (8 * ctypes.sizeof(ctypes.c_void_p))) - 8), native._D)
        self.assertEqual(fn(0, overflow, 32, out, 4), -1)
        self.assertEqual(list(out), [12345] * 4)
        event = native.Event()
        event.output[:] = [12345] * 4
        self.assertEqual(fn.event(None), -1)
        # Bad header size is refused without reading the full input payload.
        self.assertEqual(fn.event(ctypes.byref(event)), -1)
        event.byte_size = ctypes.sizeof(event)
        event.operation = 99
        self.assertEqual(fn.event(ctypes.byref(event)), -1)
        event.operation = 2
        self.assertEqual(fn.event(ctypes.byref(event)), 1)
        for value in (math.nan, math.inf, -math.inf):
            event.input[3] = value
            self.assertEqual(fn.event(ctypes.byref(event)), -2)
            self.assertEqual(list(event.output), [12345] * 4)
        event.input[3] = 1
        self.assertEqual(fn.event(ctypes.byref(event)), 0)
        self.assertEqual(list(event.output), [1, 0, 0, 0])
        misaligned = ctypes.cast(ctypes.addressof(raw) + 1, ctypes.POINTER(native.Event))
        self.assertEqual(fn.event(misaligned), -1)

    def test_cache_updates_and_protocol_plane(self):
        plane = cplane.CPlane()
        p = pc.PickConstraint()
        p.set_angle("a", (0, 0, 0), plane, 0)
        self.close(p.apply((4, 5, 6)), (4, 0, 0))
        plane.xaxis, plane.yaxis = (0, 0, 1), (1, 0, 0)
        self.close(p.apply((4, 5, 6)), (0, 0, 6))
        p.set_distance("a", (0, 0, 0), plane, 9)
        self.close(p.apply((4, 5, 6)), (0, 0, 9))
        p.set_distance("b", (1, 2, 3), plane, 2)
        self.assertIsNone(p.angle)
        self.close(p.apply((1, 2, 8)), (1, 2, 5))
        # A protocol-only host plane must retain the complete Python path.
        protocol = types.SimpleNamespace(from_world=plane.from_world,
                                         to_world=plane.to_world,
                                         vector_to_world=plane.vector_to_world)
        self.close(ortho.constrain((0, 0, 0), (2, 5, 8), protocol), (0, 0, 8))
        p.set_angle("c", (0, 0, 0), protocol, 0)
        self.close(p.apply((2, 5, 8)), (0, 0, 8))

    def test_mutable_frame_normal_and_vectors(self):
        class Frame:
            from_world = cplane.CPlane.from_world
            to_world = cplane.CPlane.to_world
            vector_to_world = cplane.CPlane.vector_to_world
        f = Frame()
        f.origin, f.xaxis, f.yaxis, f.zaxis = [0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., 1.]
        p = pc.PickConstraint()
        p.tab("k", (0, 0, 0), f, (3, 0, 0))
        p.tab("k", (0, 0, 0), f, (3, 0, 0))
        p.set_distance("k", (0, 0, 0), f, 2)
        self.close(p.apply((1, 2, 4)), (2/math.sqrt(5), 4/math.sqrt(5), 0))
        # Only the independent normal changes, in place. A signature retaining
        # vector lists, or omitting Z, cannot detect this.
        f.zaxis[:] = [0., 1., 0.]
        self.close(p.apply((1, 2, 4)), (2/math.sqrt(17), 0, 8/math.sqrt(17)))
        # Ortho uses Z both for base height and reconstruction, independently
        # of X/Y; snapshot it and the mutable base too.
        f.zaxis[:] = [0., 0., 1.]
        base = [2., 3., 5.]
        self.assertEqual(ortho.constrain(base, (4, 11, 99), f), (2, 11, 5))
        f.zaxis[:] = [0., 0., 2.]
        self.assertEqual(ortho.constrain(base, (4, 11, 99), f), (2, 11, 20))
        base[2] = 7.
        self.assertEqual(ortho.constrain(base, (4, 11, 99), f), (2, 11, 28))
        f.origin[2] = 2.
        self.assertEqual(ortho.constrain(base, (4, 11, 99), f), (2, 11, 22))

    def test_fixed_cache_validates_and_invalidates(self):
        p = pc.PickConstraint()
        p.set_angle("k", (0, 0, 0), cplane.WORLD_TOP, 30)
        p.set_distance("k", (0, 0, 0), cplane.WORLD_TOP, 4)
        before = p.apply((1, 2, 3))
        solver = p._native_solver
        with patch.object(solver, "call", side_effect=AssertionError("cached result should not cross FFI")):
            self.assertEqual(p.apply((-90, 2, 3)), before)
            with self.assertRaisesRegex(RuntimeError, "status -2.*no Python replay"):
                p.apply((math.nan, 2, 3))
        p.distance = 8
        self.close(p.apply((1, 2, 3)), tuple(2*x for x in before))
        p.angle = 90
        self.close(p.apply((1, 2, 3)), (0, 8, 0))
        p.base = (1, 2, 3)
        self.close(p.apply((1, 2, 3)), (1, 10, 3))
        p.reset()
        self.assertIsNone(p._native_solver)

    def test_failure_never_replays(self):
        p = pc.PickConstraint()
        p.set_distance("a", (0, 0, 0), cplane.WORLD_TOP, -3)
        p.set_angle("a", (0, 0, 0), cplane.WORLD_TOP, 31)
        for code in (-7, 1):
            with patch.object(native, "binding", return_value=lambda *args: code):
                with self.assertRaisesRegex(RuntimeError, f"status {code}.*no Python replay"):
                    p.apply((4, 5, 6))

    def test_routing_and_cached_library_selection(self):
        p = pc.PickConstraint()
        p.set_distance("k", (0, 0, 0), cplane.WORLD_TOP, 3)
        with patch.object(native, "constraint", side_effect=AssertionError("simple pick must not cross FFI")):
            self.close(p.apply((3, 4, 0)), (1.8, 2.4, 0))
        p.set_angle("k", (0, 0, 0), cplane.WORLD_TOP, 30)
        fn = native.binding()
        class NoEnvironment:
            def get(self, *args):
                raise AssertionError("mouse events must not query the environment")
        with patch.object(native.os, "environ", NoEnvironment()):
            self.assertIs(native.binding(), fn)
            self.close(p.apply((3, 4, 0)), (3 * math.cos(math.pi/6), 1.5, 0))
            self.assertEqual(ortho.constrain((0, 0, 0), (1, 9, 8), cplane.WORLD_TOP), (0, 9, 0))

    def test_nonfinite_native_output_is_not_replayed(self):
        def invalid(op, a, size, out, capacity):
            out[0], out[1], out[2] = math.nan, 0, 0
            return 0
        p = pc.PickConstraint()
        p.set_distance("k", (0, 0, 0), cplane.WORLD_TOP, -3)
        p.set_angle("k", (0, 0, 0), cplane.WORLD_TOP, 30)
        with patch.object(native, "binding", return_value=invalid):
            with self.assertRaisesRegex(RuntimeError, "non-finite output.*no Python replay"):
                p.apply((1, 2, 3))

    def test_missing_library_and_symbols(self):
        real = os.environ.get("NARWHAL_PURE_CORE")
        with patch.dict(os.environ, {"NARWHAL_PURE_CORE": str(ROOT / "tmp/no-pick-library")}):
            self.assertIsNone(native.reload_binding())
            self.assertEqual(ortho.constrain((0, 0, 0), (1, 9, 8), cplane.WORLD_TOP), (0, 9, 0))
        for abi, capability in [(2, 1), (1, 2), (1, None), (1, 1)]:
            class Fn:
                def __init__(self, result): self.result = result
                def __call__(self): return self.result
            lib = types.SimpleNamespace(narwhal_pure_core_abi_version=Fn(abi))
            if capability is not None:
                lib.narwhal_pure_core_pickconstraint_v1_capability = Fn(capability)
            with patch.dict(os.environ, {"NARWHAL_PURE_CORE": "fake-pick-" + str((abi, capability))}), patch.object(ctypes, "CDLL", return_value=lib):
                self.assertIsNone(native.reload_binding())
        for variant in ("missing-event", "missing-event-capability", "wrong-layout"):
            lib = types.SimpleNamespace(
                narwhal_pure_core_abi_version=Fn(1),
                narwhal_pure_core_pickconstraint_v1_capability=Fn(1),
                narwhal_pure_core_pickconstraint_v1=Fn(0),
                narwhal_pure_core_pickconstraint_event_v1=Fn(0),
                narwhal_pure_core_pickconstraint_event_v1_capability=Fn(ctypes.sizeof(native.Event)))
            if variant == "missing-event":
                del lib.narwhal_pure_core_pickconstraint_event_v1
            elif variant == "missing-event-capability":
                del lib.narwhal_pure_core_pickconstraint_event_v1_capability
            else:
                lib.narwhal_pure_core_pickconstraint_event_v1_capability = Fn(8)
            with patch.object(ctypes, "CDLL", return_value=lib):
                self.assertIsNone(native.reload_binding(), variant)
        self.assertIsNotNone(native.reload_binding(), real)
        admitted = ctypes.CDLL(real) if real else None
        if admitted is not None:
            with patch.dict(os.environ):
                os.environ.pop("NARWHAL_PURE_CORE", None)
                with patch.object(ctypes, "CDLL", return_value=admitted) as cdll:
                    self.assertIsNotNone(native.reload_binding())
                    name = native.binding().__name__
                    self.assertEqual(name, "narwhal_pure_core_pickconstraint_v1")
                    self.assertEqual(Path(cdll.call_args.args[0]).parent, ROOT / "narwhal3d/native")
            self.assertIsNotNone(native.reload_binding())


def benchmark(oracle_dir=None):
    if oracle_dir is None:
        ref, ref_ortho = pc, ortho
        print("Reference: public Python compatibility, binding disabled outside timed loops")
    else:
        oracle_dir = Path(oracle_dir)
        ref = load("pickconstraint_oracle", oracle_dir / "pickconstraint_oracle.py")
        ref_ortho = load("ortho_oracle", oracle_dir / "ortho_oracle.py")
        print(f"Reference: unchanged oracle at {oracle_dir}")
    assert native.binding() is not None
    plane = cplane.CPlane((5, -7, 9), (1, 2, 3), (-2, 1, 0))
    for case in ["distance", "increment", "angle", "circle", "direction", "fixed",
                 "direction_fixed", "angle_increment", "direction_increment", "circle_distance", "ortho"]:
        p, q = pc.PickConstraint(), ref.PickConstraint()
        for obj in (p, q):
            if case in ("distance", "increment", "fixed", "direction_fixed", "angle_increment", "direction_increment", "circle_distance"):
                obj.set_distance("k", (2, 3, 5), plane, -3 if "increment" in case else 3)
            if case in ("angle", "fixed", "angle_increment"):
                obj.set_angle("k", (2, 3, 5), plane, 31)
            if case in ("direction", "direction_fixed", "direction_increment", "circle", "circle_distance"):
                obj.tab("k", (2, 3, 5), plane, (8, 3, 4))
                if "circle" in case:
                    obj.tab("k", (2, 3, 5), plane, (8, 3, 4))
        # A moving cursor, public adapter calls, tuple results and warm buffers.
        cursors = [(i * .01, 8 + i * .03, 4 - i * .02) for i in range(1000)]
        if case == "ortho":
            a = lambda: [ortho.constrain((2, 3, 5), c, plane) for c in cursors]
            b = lambda: [ref_ortho.constrain((2, 3, 5), c, plane) for c in cursors]
        else:
            a = lambda: [p.apply(c) for c in cursors]
            b = lambda: [q.apply(c) for c in cursors]
        a()
        # Alternate order to reduce temperature/frequency/order bias. Disable
        # native for the whole reference timing, never via a per-event Mock.
        native_times, python_times = [], []
        for sample in range(7):
            for is_native in ((True, False) if sample % 2 else (False, True)):
                if is_native:
                    native_times.append(timeit.timeit(a, number=30))
                elif oracle_dir is None:
                    with patch.object(native, "binding", lambda: None):
                        python_times.append(timeit.timeit(b, number=30))
                else:
                    python_times.append(timeit.timeit(b, number=30))
        nt = statistics.median(native_times) / 30000 * 1e6
        pt = statistics.median(python_times) / 30000 * 1e6
        route = "native" if case in ("fixed", "angle_increment", "circle_distance", "ortho") else "Python"
        print(f"{case:20s} routed/{route:6s} {nt:.3f} us/event reference {pt:.3f} us/event ratio {nt/pt:.3f}")


if __name__ == "__main__":
    if "--benchmark" in sys.argv:
        import argparse
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--benchmark", action="store_true")
        parser.add_argument("--oracle-dir", type=Path)
        args = parser.parse_args()
        benchmark(args.oracle_dir)
    else:
        unittest.main()
