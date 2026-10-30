"""Cage command limits and nonzero-thickness identity; bundled Python, no GUI.

Requires a real Rust pure core (absence fails, never a successful skip).
--mutations compiles modified modules in memory, never edits production sources.
"""
import importlib
import io
import math
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

import rhino3dm as r3

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType('narwhal3d')
pkg.__path__ = [str(ROOT / 'narwhal3d')]
sys.modules['narwhal3d'] = pkg
commands = importlib.import_module('narwhal3d.commands')
model = importlib.import_module('narwhal3d.model')
cage = importlib.import_module('narwhal3d.cage')
surface = importlib.import_module('narwhal3d.surface_commands')
native = importlib.import_module('narwhal3d.native_cage')


class Context(commands.NarwhalContext):
    def __init__(self):
        super().__init__(model.NarwhalModel())
        self.added = []
        for name, p in [('a', (0, 0, 0)), ('b', (1, 1, 5e-10))]:
            self.model.add_object(r3.Point(r3.Point3d(*p)), name, 0)

    def selected_objects(self):
        return [types.SimpleNamespace(name=n) for n in ('a', 'b')]

    def geom_by_name(self, name):
        return next(o.Geometry for o in self.model.file.Objects if o.Attributes.Name == name)

    def add_geometry(self, geom, base_name, layer_index=None):
        self.note_change(base_name)
        oid, name = self.model.add_object(geom, base_name, 0)
        self.added.append(geom)
        return oid, base_name


class BoundaryTests(unittest.TestCase):
    def test_loaded_native_core_rejects_non_identity_shapes(self):
        axes = ((1, 0, 0), (0, 1, 0), (0, 0, 1))
        for degrees, counts in (((0, 1, 1), (2, 2, 2)), ((0, 1, 1), (1, 2, 2))):
            self.assertIsNone(native.rest_points((0, 0, 0), axes, degrees, counts))
        for length in (1e200, 1e-200):
            self.assertIsNone(native.rest_points((0, 0, 0),
                ((length, 0, 0), axes[1], axes[2]), (1, 1, 1), (2, 2, 2)))

    def test_invalid_command_values_refused_before_builder_and_without_undo(self):
        for key in ('PointCountX', 'DegreeX'):
            for value in (float('nan'), float('inf'), -1, 1.5, 1000, None, 'bad'):
                with self.subTest(key=key, value=value):
                    ctx = Context(); cmd = surface.CageEditCmd(); cmd.opt[key] = value
                    with patch.object(cage, 'make_cage', side_effect=AssertionError('builder reached')):
                        msg, done = cmd.begin(ctx)
                    self.assertTrue(done)
                    self.assertIn('CageEdit:', msg)
                    self.assertEqual(ctx.added, [])
                    self.assertEqual(ctx.undo.undo_names(), [])
                    self.assertEqual(ctx.model.object_count(), 2)

    def test_publication_describes_limited_mode_and_one_undo(self):
        ctx = Context(); cmd = surface.CageEditCmd()
        msg, done = cmd.begin(ctx)
        self.assertTrue(done)
        self.assertEqual(len(ctx.added), 1)
        self.assertEqual(ctx.undo.undo_names(), ['CageEdit'])
        self.assertIn('structure-preserving only', msg)
        self.assertIn('Global/Local influence and refitting are not implemented yet', msg)
        self.assertIn('Limited', cmd.description)

    def test_near_flat_rest_refresh_preserves_all_coordinates(self):
        # Both thin and exactly flat directions, with each possible world normal.
        # A world-XY-only or tolerance-collapsing implementation cannot pass.
        for axis in range(3):
            for thickness in (0.0, 5e-10):
                with self.subTest(axis=axis, thickness=thickness):
                    top = [1.0] * 3; top[axis] = thickness
                    geoms = [r3.Point(r3.Point3d(0, 0, 0)), r3.Point(r3.Point3d(*top))]
                    m = model.NarwhalModel()
                    captives = [(m.add_object(g, str(i), 0)[0], g) for i, g in enumerate(geoms)]
                    control = cage.make_cage(captives)
                    binding = cage.binding_of(control)
                    self.assertEqual(binding['counts'][axis], 1 if thickness == 0 else 4)
                    out = cage.deformed_captives(control)
                    for (_, result), (_, rest) in zip(out, captives):
                        for coordinate in ('X', 'Y', 'Z'):
                            self.assertTrue(math.isclose(getattr(result.Location, coordinate),
                                getattr(rest.Location, coordinate), rel_tol=1e-12, abs_tol=1e-20))
                    self.assertEqual(cage.refresh(m, control), 2)


def suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(BoundaryTests)


def mutated_module(path, old, new):
    source = (ROOT / path).read_text()
    assert source.count(old) == 1, 'mutation anchor must be unique'
    mod = types.ModuleType('narwhal3d._boundary_mutation')
    mod.__package__ = 'narwhal3d'
    with patch.dict(commands.COMMANDS, commands.COMMANDS.copy()):
        exec(compile(source.replace(old, new, 1), str(ROOT / path), 'exec'), mod.__dict__)
    return mod


if __name__ == '__main__':
    if not native.available():
        raise SystemExit('FAIL: real Rust pure core required; set NARWHAL_PURE_CORE')
    if not unittest.TextTestRunner(verbosity=1).run(suite()).wasSuccessful():
        raise SystemExit(1)
    if '--mutations' in sys.argv:
        cases = [
            ('finite command values', 'surface', 'narwhal3d/surface_commands.py',
             'not math.isfinite(v) or not v.is_integer()', 'False'),
            ('bounded command counts', 'surface', 'narwhal3d/surface_commands.py',
             'n < 2 or n > 64', 'False'),
            ('bounded command degrees', 'surface', 'narwhal3d/surface_commands.py',
             'd < 1 or d > 63', 'False'),
            ('honest completion', 'surface', 'narwhal3d/surface_commands.py',
             '{self.LIMITATION}', ''),
            ('nonzero thickness', 'cage', 'narwhal3d/cage.py',
             'if size[d] == 0.0:', 'if size[d] <= 1e-9 * extent:'),
        ]
        for label, target, path, old, new in cases:
            replacement = mutated_module(path, old, new)
            original = globals()[target]
            try:
                globals()[target] = replacement
                result = unittest.TextTestRunner(stream=io.StringIO()).run(suite())
                if result.wasSuccessful():
                    raise AssertionError('SURVIVED: ' + label)
                print('RED:', label)
            finally:
                globals()[target] = original
        if not unittest.TextTestRunner(verbosity=1).run(suite()).wasSuccessful():
            raise SystemExit(1)
        print('CAGE BOUNDARY: 5 mutations RED -> restored GREEN')
