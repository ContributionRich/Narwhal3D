#!/usr/bin/env python3
"""Measured Degree2/3 control-curve workflow, real geometry and preview parity."""
import importlib
import inspect
import math
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg
commands = importlib.import_module("narwhal3d.commands")
cplane = importlib.import_module("narwhal3d.cplane")
preview = importlib.import_module("narwhal3d.preview")

class Context:
    def __init__(self, plane): self.plane, self.geometry = plane, []
    def active_cplane(self): return self.plane
    def selected_objects(self): return []
    def add_geometry(self, geometry, name, *args):
        self.geometry.append(geometry)
        return name, name

class DegreeTests(unittest.TestCase):
    def draw(self, degree, plane=cplane.WORLD_TOP):
        ctx = Context(plane)
        command = commands.CurveCmd()
        command.begin(ctx)
        self.assertFalse(command.feed("Degree", ctx)[1])
        self.assertFalse(command.feed(str(degree), ctx)[1])
        for value in ("0,0,0", "10,4,0", "10,8,0", "20,4,0"):
            self.assertFalse(command.feed(value, ctx)[1])
        self.assertTrue(command.feed("", ctx)[1])
        self.assertEqual(len(ctx.geometry), 1)
        return command, ctx, ctx.geometry[0]

    def test_both_degrees_and_preview_in_three_planes(self):
        for plane in (cplane.WORLD_TOP, cplane.WORLD_FRONT, cplane.WORLD_RIGHT):
            for degree in (2, 3):
                command, ctx, curve = self.draw(degree, plane)
                self.assertEqual(curve.Degree, degree)
                self.assertEqual(len(curve.Points), 4)
                self.assertFalse(curve.IsRational)
                self.assertFalse(curve.IsPolyline())
                samples = preview.segments("curve", command.args[:-1], command.args[-1], plane, cmd=command)[0]
                domain = curve.Domain
                for i, point in enumerate(samples):
                    expected = curve.PointAt(domain.T0 + (domain.T1-domain.T0)*i/(len(samples)-1))
                    self.assertLess(math.dist(point, (expected.X, expected.Y, expected.Z)), 1e-8)

    def test_success_memory_and_conservative_cancel_failure(self):
        command, ctx, curve = self.draw(2)
        second = commands.CurveCmd(); second.begin(ctx)
        self.assertEqual(second.opt["Degree"], "2")
        second.feed("Degree=3", ctx)  # no publication, so no new remembered value
        third = commands.CurveCmd(); third.begin(ctx)
        self.assertEqual(third.opt["Degree"], "2")
        self.assertFalse(third.feed("Degree=99", ctx)[1])
        self.assertEqual(third.opt["Degree"], "2")
        with patch.object(ctx, "add_geometry", side_effect=RuntimeError("publication failed")):
            for value in ("0,0,0", "1,1,0"): third.feed(value, ctx)
            with self.assertRaises(RuntimeError): third.feed("", ctx)
        self.assertEqual(ctx._curve_degree, "2")

    def test_few_points_reduce_degree_and_builder_cannot_facet(self):
        ctx = Context(cplane.WORLD_TOP); command = commands.CurveCmd(); command.begin(ctx)
        command.feed("Degree=2", ctx)
        command.feed("0,0,0", ctx); command.feed("1,1,0", ctx); command.feed("", ctx)
        self.assertEqual(ctx.geometry[0].Degree, 1)
        original = commands.bridge.make_polyline
        with patch.object(commands.bridge, "make_polyline", side_effect=lambda points, **kw: original(points, degree=1)):
            ctx = Context(cplane.WORLD_TOP); command = commands.CurveCmd(); command.begin(ctx)
            for value in ("0,0,0", "10,4,0", "10,8,0", "20,4,0"): command.feed(value, ctx)
            message, done = command.feed("", ctx)
            self.assertTrue(done)
            self.assertIn("could not be constructed", message)
            self.assertFalse(ctx.geometry)

    def test_preview_and_memory_mutations_fail(self):
        original = preview.control_point_curve_polyline
        with patch.object(preview, "control_point_curve_polyline", side_effect=lambda control, **kw: original(control, max_degree=3)):
            result = unittest.TestResult(); DegreeTests("test_both_degrees_and_preview_in_three_planes").run(result)
            self.assertFalse(result.wasSuccessful())
        with patch.object(commands.CurveCmd, "_recorded", commands.Command._recorded):
            result = unittest.TestResult(); DegreeTests("test_success_memory_and_conservative_cancel_failure").run(result)
            self.assertFalse(result.wasSuccessful())

if __name__ == "__main__": unittest.main()
