"""Native-wire value identity regression; Python exercises the OCP/document boundary.

Run in Blender with --python-exit-code 1. --private opts into the reported read-only
fixture; no private data is copied into this test or published.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'spike'))
import rhino3dm as r3
from narwhal3d import bridge, kernel, narwhal_document as doc, native_document, document_worker as worker
from narwhal3d.kernel import occt_backend as ob
from narwhal3d.model import NarwhalModel
from test_document_correspondence import stage, validate, cavity_pair, surface_pair


def encoded(shape):
    return bytes(ob.shape_to_bytes(shape, triangles=False,
                 format_version=ob.document_current_binary_format()))


def payloads(path, root):
    native_document.unpack(str(path), root)
    manifest = json.loads((root / 'manifest.json').read_text())
    return {row['object_id']: (root / row['member']).read_bytes()
            for row in manifest['masters']}


def cycles(model, count, expected_objects, expected_masters):
    original = {str(obj.Attributes.Id): encoded(kernel.master_of(obj.Geometry))
                for obj in model.file.Objects if kernel.master_of(obj.Geometry) is not None}
    assert len(original) == expected_masters
    with tempfile.TemporaryDirectory(prefix='native-identity-') as directory:
        root = Path(directory)
        target = root / 'roundtrip.narwhal'
        for cycle in range(count):
            start = time.perf_counter()
            doc.save_model(model, target)
            saved = time.perf_counter() - start
            assert payloads(target, root / f'payload-{cycle}') == original
            start = time.perf_counter()
            candidate = doc.load_model(target)
            opened = time.perf_counter() - start
            assert candidate.object_count() == expected_objects
            # Before registration, candidate bindings are still an inert transaction.
            if '--mutation-next-save' not in sys.argv:
                assert sorted(encoded(shape) for geometry, shape in
                              candidate._native_master_bindings) == sorted(original.values())
            doc.commit_masters(candidate)
            model = candidate
            if '--mutation-next-save' not in sys.argv:
                assert {str(obj.Attributes.Id): encoded(kernel.master_of(obj.Geometry))
                        for obj in model.file.Objects
                        if kernel.master_of(obj.Geometry) is not None} == original
            print(f'CYCLE {cycle+1} objects={expected_objects} masters={expected_masters} '
                  f'save={saved:.6f}s open={opened:.6f}s FULL_PAYLOADS_IDENTICAL', flush=True)
    return model


class IdentityTests(unittest.TestCase):
    def test_generated_nonaxis_native_constructor(self):
        # A deterministic search, not private coordinates: exercise unit-direction
        # normalization with ordinary rotated analytic cylinders.
        drifting = None
        for index in range(1, 30):
            geom = bridge.make_cylinder((0, 0, 0), 2, 4)
            transform = r3.Transform.Rotation(0.137 * index, r3.Vector3d(1, 2, 3), r3.Point3d(0, 0, 0))
            bridge.transform_geometry(geom, transform)
            recipe_shape = ob.from_recipe(kernel.recipe_of(geom))
            data = encoded(recipe_shape)
            if encoded(ob.shape_from_bytes(data, type(recipe_shape).__name__)) != data:
                drifting = geom, recipe_shape
                break
        self.assertIsNotNone(drifting, 'fixture must expose native direction drift')
        geom, shape = drifting
        recipe = kernel.recipe_of(geom)
        geom = bridge._triangulated_proxy(shape)
        geom.SetUserString(kernel.DOCUMENT_FORM_KEY, 'triangulated')
        kernel.record_recipe(geom, recipe)
        # Display meshing changes native edge flags even with triangles omitted.
        # Register the untouched constructor, not the mesher's scratch shape.
        kernel.register_master(geom, ob.from_recipe(recipe))
        model = NarwhalModel()
        model.add_object(geom, 'NonaxisCylinder', 0)
        model = cycles(model, 5, 1, 1)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'parent-proof.narwhal'
            doc.save_model(model, target)
            original = encoded(kernel.master_of(next(iter(model.file.Objects)).Geometry))
            decoded = encoded(ob.shape_from_bytes(original, type(shape).__name__))
            self.assertNotEqual(original, decoded)
            # The real worker still succeeds and supplies its usual receipts.
            # Only the parent's independent witness is wrong: it must retain
            # the decoded native master, never trust worker/digest claims alone.
            serialize = ob.shape_to_bytes
            for mismatch in ('bytes', 'format'):
                def wrong_witness(item, **kwargs):
                    if mismatch == 'format':
                        kwargs['format_version'] = 1
                        return serialize(item, **kwargs)
                    return b'not the validated native bytes'
                with patch.object(ob, 'shape_to_bytes', side_effect=wrong_witness):
                    candidate = doc.load_model(target)
                self.assertEqual(len(candidate._native_master_bindings), 1)
                self.assertEqual(encoded(candidate._native_master_bindings[0][1]), decoded)

    def test_open_replays_only_recipes_the_worker_proved_identical(self):
        # MEASURED: a 33-object project's Open replayed 13.7 s of construction history in
        # the app and discarded every result, because the worker had already shown those
        # replays differ from the stored masters. The worker now lists the identical ones
        # and the app replays only those; the masters it binds are unchanged either way.
        self.assertEqual(doc.RECIPE_IDENTITY_FILE, worker.RECIPE_IDENTITY_FILE)
        identical = bridge.make_cylinder((0, 0, 0), 2, 4)
        kernel.register_master(identical, ob.from_recipe(kernel.recipe_of(identical)))
        identical.SetUserString(kernel.DOCUMENT_FORM_KEY, 'exact')
        model = NarwhalModel()
        object_id, _ = model.add_object(identical, 'Identical', 0)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'hint.narwhal'
            self.assertTrue(doc.save_model(model, target))
            payload = payloads(target, Path(directory) / 'payload')
            hints = []
            real_hint = doc._recipe_identity_hint
            def observed(validated):
                value = real_hint(validated)
                hints.append(value)
                return value
            replays = []
            original = ob.from_recipe
            def counted(item, _depth=0):
                if _depth == 0:
                    replays.append(item.get('kind'))
                return original(item, _depth)
            with patch.object(doc, '_recipe_identity_hint', side_effect=observed), \
                    patch.object(ob, 'from_recipe', side_effect=counted):
                candidate = doc.load_model(target)
            self.assertEqual(hints, [frozenset({object_id})])
            self.assertEqual(replays, ['cylinder'])
            # Not proven identical: no replay at all, and the bound master is still the
            # worker-validated exact geometry.
            for hint in (frozenset(), None):
                replays.clear()
                with patch.object(doc, '_recipe_identity_hint', return_value=hint), \
                        patch.object(ob, 'from_recipe', side_effect=counted):
                    candidate = doc.load_model(target)
                self.assertEqual(replays, [] if hint is not None else ['cylinder'])
                (data,) = payload.values()
                expected = data if hint is None else encoded(
                    ob.shape_from_bytes(data, 'TopoDS_Solid'))
                self.assertEqual([encoded(shape) for _g, shape in candidate._native_master_bindings],
                                 [expected])

    def test_full_data_and_declared_format_not_digest(self):
        shape = ob.make_box(2, 3, 4)
        current = ob.document_current_binary_format()
        data = encoded(shape)
        self.assertTrue(worker._recipe_encoding_matches(data, shape, current))
        self.assertFalse(worker._recipe_encoding_matches(data[:-1] + bytes([data[-1] ^ 1]), shape, current))
        self.assertFalse(worker._recipe_encoding_matches(b'', shape, current))
        # Same shape/digest claim is not enough: exact format-specific bytes must
        # match. A historical format keeps the geometric fallback available.
        historical = bytes(ob.shape_to_bytes(shape, triangles=False, format_version=1))
        self.assertNotEqual(historical, data)
        self.assertTrue(worker._recipe_encoding_matches(historical, shape, 1))
        self.assertFalse(worker._recipe_encoding_matches(historical, shape, current))
        with patch.object(worker.hashlib, 'sha256', return_value=hashlib.sha256(data)):
            self.assertFalse(worker._recipe_encoding_matches(b'wrong bytes', shape, current))
        geometry, wrong, master = cavity_pair()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'fallback'
            stage(root, geometry, master)
            with patch.object(worker, '_recipe_encoding_matches', return_value=False), \
                    patch.object(worker, '_corresponds', wraps=worker._corresponds) as fallback:
                accepted, message, _ = validate(root)
            self.assertTrue(accepted, message)
            self.assertTrue(fallback.called)

    def test_adversarial_recipe_and_proxy_still_refused(self):
        for attack in ('recipe', 'closed-proxy', 'open-proxy'):
            if attack == 'open-proxy':
                geometry, wrong, shape = surface_pair()
            else:
                geometry, wrong, shape = cavity_pair()
            if attack == 'recipe':
                kernel.record_recipe(geometry, kernel.recipe_of(wrong))
            else:
                for key, value in geometry.GetUserStrings():
                    wrong.SetUserString(key, value)
                geometry = wrong
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'attack'
                stage(root, geometry, shape)
                accepted, message, _ = validate(root)
                self.assertFalse(accepted, (attack, message))
                # Failed admission cannot replace an existing good archive.
                old = Path(directory) / 'old.narwhal'
                old.write_bytes(b'previous archive untouched')
                with patch.object(doc, '_validate_in_worker', side_effect=doc.NarwhalDocumentError(message)):
                    model = NarwhalModel()
                    model.add_object(bridge.make_line((0,0,0), (1,0,0)), 'Line', 0)
                    with self.assertRaises(doc.NarwhalDocumentError):
                        doc.save_model(model, old)
                self.assertEqual(old.read_bytes(), b'previous archive untouched')

    def test_viewport_meshed_master_still_proves_identity(self):
        """The live app meshes masters for display; that cache state is not geometry."""
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        shape = ob.make_box(2, 3, 4)
        current = ob.document_current_binary_format()
        before = encoded(shape)
        BRepMesh_IncrementalMesh(shape, 0.01, False, 0.5, True)
        after = encoded(shape)
        self.assertNotEqual(before, after, 'fixture must expose mesher cache-state bytes')
        recipe = ob.make_box(2, 3, 4)
        self.assertTrue(worker._recipe_encoding_matches(after, recipe, current, 'TopoDS_Solid'))
        moved = ob.make_box(2, 3, 4.000001)
        self.assertFalse(worker._recipe_encoding_matches(after, moved, current, 'TopoDS_Solid'))
        self.assertFalse(worker._recipe_encoding_matches(b'not native bytes', recipe, current,
                                                         'TopoDS_Solid'))

    @unittest.skipUnless('--private' in sys.argv
                         and os.environ.get('NARWHAL_TEST_NATIVE_IDENTITY_PRIVATE_3DM'),
                         'private fixture explicitly opt-in')
    def test_private_five_cycles(self):
        # Expected counts: NARWHAL_TEST_NATIVE_IDENTITY_COUNTS=objects,masters
        source = Path(os.environ['NARWHAL_TEST_NATIVE_IDENTITY_PRIVATE_3DM'])
        objects, masters = (int(v) for v in os.environ.get(
            'NARWHAL_TEST_NATIVE_IDENTITY_COUNTS', '19,12').split(','))
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        try:
            cycles(NarwhalModel.load(str(source)), 5, objects, masters)
        finally:
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)


if __name__ == '__main__':
    suite = (unittest.defaultTestLoader.loadTestsFromName(
        sys.argv[sys.argv.index('--only') + 1], IdentityTests)
        if '--only' in sys.argv else
        unittest.defaultTestLoader.loadTestsFromTestCase(IdentityTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError('native encoding identity regression failed')
