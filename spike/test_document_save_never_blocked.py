"""Save is never blocked by one object, and never silently loses work.

Layers under test (narwhal3d.narwhal_document.save_model):
  * a declaration problem (tagged exact, master missing) is repaired from the recipe;
  * an object whose exact solid the validator refuses for a reason no repair addresses is
    kept as display geometry, honestly marked NOT exact, while every other object saves
    exactly and the reason is reported;
  * a validator that runs out of time/memory is retried with larger limits;
  * if the archive still cannot be produced, an interchange copy is written beside it.

Run in Blender: blender -b --factory-startup --python-exit-code 1 --python <this> [-- --mutations]
"""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from narwhal3d import bridge, kernel, narwhal_document as doc
from narwhal3d.kernel import occt_backend as ob
from narwhal3d.model import NarwhalModel


def solid(recipe_source):
    recipe = kernel.recipe_of(recipe_source)
    master = ob.from_recipe(recipe)
    proxy = bridge._proxy_from_occt(master)
    kernel.register_master(proxy, master)
    kernel.record_recipe(proxy, recipe)
    return proxy


def two_solids():
    model = NarwhalModel()
    a, _ = model.add_object(solid(bridge.make_cylinder((0, 0, 0), 1, 3)), 'Keep', 0)
    b, _ = model.add_object(solid(bridge.make_cylinder((5, 0, 0), 1, 2)), 'Broken', 0)
    return model, a, b


class SaveNeverBlockedTests(unittest.TestCase):
    def test_missing_master_is_reattached_from_recipe(self):
        model, a, _b = two_solids()
        item = model.file.Objects.FindId(a)
        orphan = item.Geometry.Duplicate()
        orphan.SetUserString(kernel.MASTER_KEY, 'm-does-not-exist')
        model.replace_geometry(a, orphan)
        self.assertIsNotNone(kernel.honesty_problem(model.file.Objects.FindId(a).Geometry))
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'reattach.narwhal'
            self.assertTrue(doc.save_model(model, target))
            self.assertTrue(any('re-attached' in n for n in model.last_save_warnings))
            reopened = doc.load_model(target)
            self.assertEqual(len(reopened._native_master_bindings), 2)

    def test_unrepairable_object_is_kept_honestly_and_the_rest_saves(self):
        model, a, b = two_solids()
        real = doc._validate_in_worker
        refused = {'n': 0}

        def validate(root, count, **kw):
            # The validator refuses object B's exact solid while it is still exact.
            if kernel.master_of(model.file.Objects.FindId(b).Geometry) is not None:
                refused['n'] += 1
                raise doc.NarwhalDocumentError(
                    f'exact-master validation worker failed: master {b} is invalid')
            return real(root, count, **kw)

        with tempfile.TemporaryDirectory() as folder, \
                patch.object(doc, '_validate_in_worker', side_effect=validate):
            target = Path(folder) / 'partial.narwhal'
            self.assertTrue(doc.save_model(model, target))
            self.assertGreaterEqual(refused['n'], 1)
            note = ' '.join(model.last_save_warnings)
            broken = model.file.Objects.FindId(b).Geometry
            self.assertIsNone(kernel.master_of(broken))
            # Honest either way: an exact NURBS document object stays ANALYTIC (and says
            # its kernel solid was set aside); a triangulated one becomes MESH_DERIVED.
            expected = {kernel.ANALYTIC: 'Broken: saved as its exact NURBS polysurface',
                        kernel.MESH_DERIVED: 'Broken: saved as display geometry only'}
            self.assertIn(kernel.representation_of(broken), expected)
            self.assertIn(expected[kernel.representation_of(broken)], note)
            self.assertIsNone(kernel.honesty_problem(broken))
            reopened = doc.load_model(target)
            self.assertEqual(reopened.object_count(), 2)
            self.assertEqual(len(reopened._native_master_bindings), 1)  # Keep stays exact

    def test_resource_failure_retries_with_larger_limits(self):
        model, _a, _b = two_solids()
        real = doc._validate_in_worker
        seen = []

        def validate(root, count, **kw):
            seen.append((kw.get('timeout'), kw.get('memory_mib')))
            if len(seen) == 1:
                raise doc.NarwhalDocumentError('exact-master validation worker failed: timed out')
            return real(root, count, **kw)

        with tempfile.TemporaryDirectory() as folder, \
                patch.object(doc, '_validate_in_worker', side_effect=validate):
            self.assertTrue(doc.save_model(model, Path(folder) / 'retry.narwhal'))
        self.assertEqual(seen, [(600, 4096), (1800, 8192)])

    def test_unwritable_archive_leaves_an_emergency_copy(self):
        model, _a, _b = two_solids()
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(doc, '_validate_in_worker', side_effect=doc.NarwhalDocumentError(
                    'exact-master validation worker failed: disk refused')):
            target = Path(folder) / 'lost.narwhal'
            with self.assertRaises(doc.NarwhalDocumentError) as caught:
                doc.save_model(model, target)
            emergency = Path(folder) / 'lost (emergency copy).3dm'
            self.assertTrue(emergency.exists() and emergency.stat().st_size > 0)
            self.assertIn(str(emergency), str(caught.exception))
            self.assertFalse(target.exists())


if __name__ == '__main__':
    args = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(SaveNeverBlockedTests))
    failed = not result.wasSuccessful()
    if '--mutations' in args:
        for label, target, value in (('no demotion', '_demote', None),
                                     ('no emergency copy', '_emergency_copy', None)):
            replacement = (lambda *a, **k: (_ for _ in ()).throw(doc.NarwhalDocumentError('x'))) \
                if label == 'no demotion' else (lambda *a, **k: None)
            with patch.object(doc, target, side_effect=replacement):
                red = unittest.TextTestRunner(verbosity=0).run(unittest.TestSuite([
                    SaveNeverBlockedTests('test_unrepairable_object_is_kept_honestly_and_the_rest_saves'
                                          if label == 'no demotion' else
                                          'test_unwritable_archive_leaves_an_emergency_copy')]))
            print(('RED verified: ' if not red.wasSuccessful() else 'FAIL: mutation survived: ') + label)
            failed = failed or red.wasSuccessful()
    if failed:
        raise RuntimeError('save-never-blocked regression failed')
