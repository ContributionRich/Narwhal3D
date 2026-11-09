"""Save must not fail when a recipe or display copy drifted from the exact master.

The master is the geometry. When the worker cannot prove a stored construction recipe or
display proxy equal to it, Save re-bases that object's recipe on the master's exact bytes
(`native_master`) or re-derives the proxy, then validates again. Open is unchanged: a
crafted archive whose recipe/proxy disagrees with its master is still refused.

Run in Blender: blender -b --factory-startup --python-exit-code 1 --python <this> [-- --mutations]
"""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'spike'))
import rhino3dm as r3
from narwhal3d import bridge, kernel, narwhal_document as doc
from narwhal3d.kernel import occt_backend as ob
from narwhal3d.model import NarwhalModel
from test_document_correspondence import stage, validate, cavity_pair


def master_summary(shape):
    return (round(ob.volume(shape), 12), dict(ob.face_types(shape)),
            tuple(round(v, 9) for p in ob.tight_bbox(shape) for v in p))


def reopened_master(path, object_id):
    model = doc.load_model(path)
    doc.commit_masters(model)
    item = model.file.Objects.FindId(object_id)
    return model, kernel.master_of(item.Geometry)


class SaveRepairTests(unittest.TestCase):
    def save_and_check(self, model, object_id, expected_note):
        live = kernel.master_of(model.file.Objects.FindId(object_id).Geometry)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'repaired.narwhal'
            self.assertTrue(doc.save_model(model, target))
            self.assertTrue(any(expected_note in note for note in model.last_save_warnings),
                            model.last_save_warnings)
            _reopened, shape = reopened_master(target, object_id)
            # Same exact geometry. (Bytes may differ only by OCCT's renormalized unit
            # directions / signed zeros on read, which is why identity uses other witnesses.)
            self.assertEqual(master_summary(shape), master_summary(live))
            # A second save is clean: nothing left to repair.
            self.assertTrue(doc.save_model(model, target))
            self.assertEqual(model.last_save_warnings, [])

    def test_unverifiable_recipe_is_rebased_on_the_exact_master(self):
        geometry, wrong, _master = cavity_pair()
        model = NarwhalModel()
        object_id, _attrs = model.add_object(geometry, 'Cavity', 0)
        # The stored history now describes a DIFFERENT solid (reflected cavity). Save
        # must keep what the user sees -- the master -- not refuse, and not the recipe.
        live = model.file.Objects.FindId(object_id).Geometry
        kernel.record_recipe(live, kernel.recipe_of(wrong))
        self.save_and_check(model, object_id, 'construction history re-based')
        recipe = kernel.recipe_of(model.file.Objects.FindId(object_id).Geometry)
        self.assertEqual(recipe['kind'], 'native_master')
        self.assertEqual(ob.native_master_bytes(recipe),
                         bytes(ob.shape_to_bytes(kernel.master_of(live), triangles=False,
                                                 format_version=ob.document_current_binary_format())))

    def test_moved_brep_data_solid_saves(self):
        # The reported case: a recipe-from-faces solid, moved with the Gumball.
        shape = ob.make_box(3, 4, 2)
        proxy = bridge._proxy_from_occt(shape)
        kernel.register_master(proxy, shape)
        kernel.record_recipe(proxy, {'kind': 'brep_data', 'data': bridge.brep_data(proxy),
                                     'tolerance': 1e-6})
        model = NarwhalModel()
        object_id, _ = model.add_object(proxy, 'Faces', 0)
        item = model.file.Objects.FindId(object_id)
        moved = bridge.transform_geometry(item.Geometry.Duplicate(), r3.Transform.Rotation(
            0.7, r3.Vector3d(1, 2, 3), r3.Point3d(1, 1, 1)))
        model.replace_geometry(object_id, moved)
        live = kernel.master_of(model.file.Objects.FindId(object_id).Geometry)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'moved.narwhal'
            self.assertTrue(doc.save_model(model, target))
            _reopened, shape = reopened_master(target, object_id)
            self.assertEqual(master_summary(shape), master_summary(live))

    def test_stale_display_proxy_is_rebuilt(self):
        recipe = kernel.recipe_of(bridge.make_cylinder((0, 0, 0), 1.5, 4))
        master = ob.from_recipe(recipe)
        geometry = bridge._proxy_from_occt(master)
        kernel.register_master(geometry, master)
        kernel.record_recipe(geometry, recipe)
        model = NarwhalModel()
        object_id, _ = model.add_object(geometry, 'Cylinder', 0)
        item = model.file.Objects.FindId(object_id)
        self.assertIs(kernel.master_of(item.Geometry), master)
        stale = bridge._triangulated_proxy(ob.make_box(1, 1, 1))
        for key, value in item.Geometry.GetUserStrings():
            stale.SetUserString(key, value)
        stale.SetUserString(kernel.DOCUMENT_FORM_KEY, 'triangulated')
        kernel.register_master(stale, master)
        model.replace_geometry(object_id, stale)
        self.assertIsNotNone(kernel.master_of(model.file.Objects.FindId(object_id).Geometry))
        self.save_and_check(model, object_id, 'display copy rebuilt')

    def test_every_refusal_is_repaired_in_one_round(self):
        # MEASURED: one worker run per refused object made the first Save after moving
        # 4 solids in a 44-object project take 180 s. Two drifted objects -- one with a
        # wrong recipe, one with a stale proxy AND a wrong recipe -- must cost exactly
        # one refused run plus one accepted run.
        model = NarwhalModel()
        geometry, wrong, _master = cavity_pair()
        first, _ = model.add_object(geometry, 'Cavity', 0)
        kernel.record_recipe(model.file.Objects.FindId(first).Geometry, kernel.recipe_of(wrong))
        recipe = kernel.recipe_of(bridge.make_cylinder((0, 0, 0), 1.5, 4))
        master = ob.from_recipe(recipe)
        cylinder = bridge._proxy_from_occt(master)
        kernel.register_master(cylinder, master)
        kernel.record_recipe(cylinder, kernel.recipe_of(bridge.make_cylinder((0, 0, 0), 2.5, 4)))
        second, _ = model.add_object(cylinder, 'Cylinder', 0)
        stale = bridge._triangulated_proxy(ob.make_box(1, 1, 1))
        for key, value in model.file.Objects.FindId(second).Geometry.GetUserStrings():
            stale.SetUserString(key, value)
        stale.SetUserString(kernel.DOCUMENT_FORM_KEY, 'triangulated')
        kernel.register_master(stale, master)
        model.replace_geometry(second, stale)
        runs = []
        original = doc._validate_in_worker
        def counted(*args, **kwargs):
            runs.append(kwargs.get('collect_errors'))
            return original(*args, **kwargs)
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(doc, '_validate_in_worker', counted):
            target = Path(folder) / 'both.narwhal'
            self.assertTrue(doc.save_model(model, target))
            notes = model.last_save_warnings
            self.assertEqual(len(runs), 2, notes)
            self.assertTrue(all(runs), runs)
            self.assertTrue(any('Cylinder: display copy rebuilt' in n for n in notes), notes)
            self.assertTrue(any('Cylinder: construction history re-based' in n for n in notes), notes)
            self.assertTrue(any('Cavity: construction history re-based' in n for n in notes), notes)
            _reopened, shape = reopened_master(target, second)
            self.assertEqual(master_summary(shape), master_summary(master))

    def test_open_still_refuses_a_crafted_mismatched_recipe(self):
        geometry, wrong, master = cavity_pair()
        kernel.record_recipe(geometry, kernel.recipe_of(wrong))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'attack'
            stage(root, geometry, master)
            accepted, message, _ = validate(root)
            self.assertFalse(accepted, message)

    def test_native_master_recipe_must_hold_the_exact_bytes(self):
        from narwhal3d import document_worker as worker
        shape = ob.make_box(2, 3, 4)
        recipe = ob.native_master_recipe(shape)
        data = bytes(ob.shape_to_bytes(shape, triangles=False,
                                       format_version=ob.document_current_binary_format()))
        self.assertTrue(worker._native_master_recipe_matches(recipe, data))
        self.assertFalse(worker._native_master_recipe_matches(recipe, data[:-1] + bytes([data[-1] ^ 1])))
        self.assertFalse(worker._native_master_recipe_matches(dict(recipe, xform=[1] * 16), data))
        self.assertFalse(worker._native_master_recipe_matches(dict(recipe, data='!!'), data))
        self.assertEqual(master_summary(ob.from_recipe(recipe)), master_summary(shape))


if __name__ == '__main__':
    args = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    failed = False
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(SaveRepairTests))
    failed = not result.wasSuccessful()
    if '--mutations' in args:
        with patch.object(doc, '_refused_master', return_value=(None, None)):
            red = unittest.TextTestRunner(verbosity=0).run(unittest.TestSuite([
                SaveRepairTests('test_unverifiable_recipe_is_rebased_on_the_exact_master'),
                SaveRepairTests('test_stale_display_proxy_is_rebuilt')]))
        if red.wasSuccessful():
            print('FAIL: repair-disabled mutation survived')
            failed = True
        else:
            print('RED verified: without repair, Save refuses these documents')
        unmutated = doc._validate_in_worker
        def first_refusal_only(*args, **kwargs):
            return unmutated(*args, **dict(kwargs, collect_errors=False))
        with patch.object(doc, '_validate_in_worker', first_refusal_only):
            red = unittest.TextTestRunner(verbosity=0).run(unittest.TestSuite([
                SaveRepairTests('test_every_refusal_is_repaired_in_one_round')]))
        if red.wasSuccessful():
            print('FAIL: one-refusal-per-run mutation survived')
            failed = True
        else:
            print('RED verified: reporting one refusal per run costs extra worker runs')
    if failed:
        raise RuntimeError('save repair regression failed')
