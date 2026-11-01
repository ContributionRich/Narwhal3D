#!/usr/bin/env python3
"""Live NarwhalModel registry consumers, immutable payloads and failure admission."""
from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from test_document_registry_baseline import model, r3


class ModelRegistryTests(unittest.TestCase):
    def setUp(self):
        self.m = model.NarwhalModel()
        self.addCleanup(self.m._object_registry.close)

    def add(self, name='same'):
        return self.m.add_object(r3.Point(r3.Point3d(1, 0, 0)), name)[0]

    def test_current_index_and_dirty_journal_are_native(self):
        oid = self.add()
        self.assertNotIsInstance(self.m._blender_names, dict)
        self.assertNotIn('_dirty', self.m.__dict__)
        self.assertIn(oid, self.m._dirty)
        self.m._dirty.clear()
        self.assertIn(oid, self.m._dirty, 'diagnostic copy must not become writable authority')
        binding = self.m._object_registry
        with patch.object(binding.native, 'find_name', wraps=binding.native.find_name) as lookup:
            self.assertEqual(self.m.object_id_by_name('SAME'), oid)
            lookup.assert_called_once_with('SAME')
        self.m.snapshot()
        self.assertEqual(self.m._dirty, {})
        self.assertTrue(self.m.object_registry_revision()['authoritative'])

    def test_count_and_display_hot_reads_do_not_scan_or_serialize_document(self):
        oid = self.add()
        self.m.register_blender_object(oid, 'Display')
        self.m.snapshot()
        observer = self.m._object_registry.observer
        with patch.object(observer, '_observe', side_effect=AssertionError('full geometry scan')):
            with patch.object(observer, '_index', side_effect=AssertionError('full index scan')):
                for _ in range(20):
                    self.assertEqual(self.m.object_count(), 1)
                    self.assertEqual(self.m.blender_name_for(oid), 'Display')
        self.assertIsNone(self.m.blender_name_for('not a UUID'))

    def test_unicode_lookup_keeps_host_lowercase_semantics(self):
        names = ['ΟΣ', 'Straße', 'İ', '海🐋', 'Ångström']
        ids = [self.add(name) for name in names]
        for query in names + ['ος', 'οσ', 'STRASSE', 'i\u0307', 'ångström', 'missing']:
            expected = next((oid for oid, name in zip(ids, names) if name.lower() == query.lower()), None)
            self.assertEqual(self.m.object_id_by_name(query), expected, query)

    def test_full_attributes_survive_snapshot_restore(self):
        group = r3.Group()
        group.Name = 'Fixture'
        index = len(self.m.file.Groups)
        self.m.file.Groups.Add(group)
        oid = self.add()
        attrs = self.m.file.Objects.FindId(oid).Attributes
        attrs.AddToGroup(index)
        attrs.PlotWeight = 0.875
        attrs.PlotWeightSource = r3.ObjectPlotWeightSource.PlotWeightFromObject
        attrs.CastsShadows = False
        attrs.SetUserString('.hidden', 'exact value')
        before = self.m.snapshot()
        attrs.PlotWeight = 4.0
        attrs.CastsShadows = True
        attrs.SetUserString('.hidden', 'changed')
        self.m.snapshot()
        self.assertEqual(before.by_id[oid]['_attributes'].PlotWeight, 0.875,
                         'snapshot attributes must not alias live attributes')
        self.m.restore(before)
        restored = self.m.file.Objects.FindId(oid).Attributes
        self.assertEqual(restored.PlotWeight, 0.875)
        self.assertFalse(restored.CastsShadows)
        self.assertEqual(restored.GetUserString('.hidden'), 'exact value')
        self.assertEqual(list(restored.GetGroupList2()), [index])
        self.assertEqual(before.by_id[oid]['_attributes'].PlotWeight, 0.875)

    def test_noop_snapshot_reuses_native_payload_and_revision(self):
        oid = self.add()
        first = self.m.snapshot()
        revision = self.m.object_registry_revision()['revision']
        self.m.file.Objects.FindId(oid).Attributes.Name = 'same'
        second = self.m.snapshot()
        self.assertIs(first.by_id[oid], second.by_id[oid])
        self.assertEqual(self.m.object_registry_revision()['revision'], revision)

    def test_payload_copy_failure_does_not_publish_native_snapshot(self):
        self.add()
        self.m.snapshot()
        self.add('next')
        native = self.m._object_registry.native
        with patch.object(self.m, '_entry', side_effect=RuntimeError('copy failed')):
            with self.assertRaisesRegex(RuntimeError, 'copy failed'):
                self.m.snapshot()
        self.assertFalse(native.info()['pending'])
        self.assertEqual(len(self.m.snapshot()), 2)

    def test_file_adoption_failure_preserves_old_owner_and_document(self):
        oid = self.add()
        before = self.m.snapshot()
        old_file = self.m.file
        old_owner = self.m._object_registry.native
        with patch.object(model._metadata_api(), 'adopt', side_effect=RuntimeError('adoption failed')):
            with self.assertRaisesRegex(RuntimeError, 'adoption failed'):
                self.m.file = r3.File3dm()
        self.assertIs(self.m.file, old_file)
        self.assertIs(self.m._object_registry.native, old_owner)
        self.assertEqual(self.m.object_count(), 1)
        self.m.restore(before)
        self.assertEqual(self.m.file.Objects.FindId(oid).Geometry.Location.X, 1)

    def test_private_backing_swap_observes_the_new_file(self):
        oid = self.add()
        before = self.m.snapshot()
        old_file = self.m.file
        self.m._round_trip()
        self.assertIsNot(self.m.file, old_file)
        self.m.file.Objects.FindId(oid).Attributes.Name = 'new backing'
        after = self.m.snapshot()
        self.assertEqual(after.by_id[oid]['attrs']['Name'], 'new backing')
        self.assertEqual(before.by_id[oid]['attrs']['Name'], 'same')

    def test_missing_observer_is_native_required_before_model_activation(self):
        api = model._registry_api()
        with patch.object(api.observer_api, 'load_observer', side_effect=RuntimeError('native required')):
            with self.assertRaisesRegex(RuntimeError, 'native required'):
                model.NarwhalModel()

    def test_save_and_load_keep_full_attributes_and_identity(self):
        oid = self.add('saved')
        attrs = self.m.file.Objects.FindId(oid).Attributes
        attrs.PlotWeight = 0.75
        attrs.SetUserString('fixture', 'preserve')
        with tempfile.TemporaryDirectory(prefix='nw-registry-model-', dir='/var/tmp') as directory:
            path = Path(directory) / 'fixture.3dm'
            self.assertTrue(self.m.save(str(path)))
            restored = model.NarwhalModel.load(str(path))
            self.addCleanup(restored._object_registry.close)
            self.assertEqual(restored.object_count(), 1)
            obj = restored.file.Objects.FindId(oid)
            self.assertIsNotNone(obj)
            self.assertEqual(obj.Attributes.PlotWeight, 0.75)
            self.assertEqual(obj.Attributes.GetUserString('fixture'), 'preserve')
            self.assertEqual(len(restored.snapshot()), 1)


def mutations():
    binding = model._registry_api().Binding
    original_entry = model.NarwhalModel._entry
    original_swap = model.NarwhalModel._replace_backing_file

    def mutable_attributes(owner, obj, names):
        entry = original_entry(owner, obj, names)
        entry['_attributes'] = obj.Attributes
        return entry

    def stale_backing(owner, file):
        old = owner._object_registry.file
        original_swap(owner, file)
        owner._object_registry.file = old

    variants = (
        ('hot-full-scan', binding, 'count', lambda self: self.sync()['count'],
         'test_count_and_display_hot_reads_do_not_scan_or_serialize_document'),
        ('missing-native-mark', binding, 'mark', lambda *a, **k: None,
         'test_current_index_and_dirty_journal_are_native'),
        ('mutable-snapshot-attributes', model.NarwhalModel, '_entry', mutable_attributes,
         'test_full_attributes_survive_snapshot_restore'),
        ('stale-backing-file', model.NarwhalModel, '_replace_backing_file', stale_backing,
         'test_private_backing_swap_observes_the_new_file'),
    )
    for label, owner, field, replacement, case in variants:
        log = io.StringIO()
        with patch.object(owner, field, replacement):
            result = unittest.TextTestRunner(stream=log).run(unittest.TestSuite([ModelRegistryTests(case)]))
        if result.errors or not result.failures:
            raise AssertionError(f'expected assertion RED: {label}\n{log.getvalue()}')
        print('RED assertion:', label)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ModelRegistryTests))
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == '__main__':
    if '--mutations' in sys.argv:
        mutations()
    else:
        unittest.main(verbosity=2)
