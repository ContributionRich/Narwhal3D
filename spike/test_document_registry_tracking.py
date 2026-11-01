#!/usr/bin/env python3
"""Owner-routing prerequisites for the registry; actual rhino3dm host glue.

This does not certify retained raw-callable coverage or full-document authority.
"""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import sys
import types
import unittest
import uuid
from unittest.mock import patch

import rhino3dm as r3

ROOT = Path(__file__).resolve().parents[1]
RAW_ATTRIBUTES = r3.File3dmObject.Attributes
pkg = types.ModuleType('narwhal3d')
pkg.__path__ = [str(ROOT / 'narwhal3d')]
sys.modules['narwhal3d'] = pkg
spec = importlib.util.spec_from_file_location('narwhal3d.model', ROOT / 'narwhal3d/model.py')
model = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = model
spec.loader.exec_module(model)


class TrackingOwnerTests(unittest.TestCase):
    def setUp(self):
        self.left = model.NarwhalModel()
        self.right = model.NarwhalModel()
        self.oid = str(uuid.uuid4())
        for owner in (self.left, self.right):
            attrs = r3.ObjectAttributes()
            attrs.Id = uuid.UUID(self.oid)
            attrs.Name = 'original'
            owner.file.Objects.AddPoint(r3.Point3d(1, 0, 0), attrs)
            owner.snapshot()

    def assert_only_left_changed(self):
        self.assertIn(self.oid, self.left._dirty)
        self.assertEqual(self.right._dirty, {})
        self.assertEqual(self.right.file.Objects.FindId(self.oid).Attributes.Name, 'original')

    def test_find_id_attribute_owner(self):
        self.left.file.Objects.FindId(self.oid).Attributes.Name = 'left'
        self.assert_only_left_changed()
        self.assertEqual(self.left.snapshot().by_id[self.oid]['attrs']['Name'], 'left')

    def test_index_geometry_owner(self):
        self.left.file.Objects[0].Geometry.Transform(r3.Transform.Translation(2, 0, 0))
        self.assert_only_left_changed()
        self.assertEqual(self.left.snapshot().by_id[self.oid]['geometry'].Location.X, 3)
        self.assertEqual(self.right.snapshot().by_id[self.oid]['geometry'].Location.X, 1)

    def test_iteration_attribute_owner(self):
        for obj in self.left.file.Objects:
            obj.Attributes.SetUserString('owner', 'left')
        self.assert_only_left_changed()

    def test_delete_owner(self):
        self.left.file.Objects.Delete(self.oid)
        self.assert_only_left_changed()
        self.assertEqual(self.left.snapshot().by_id, {})

    def test_add_owner(self):
        self.left.file.Objects.Delete(self.oid)
        self.left.snapshot()
        attrs = r3.ObjectAttributes()
        attrs.Id = uuid.UUID(self.oid)
        self.left.file.Objects.AddPoint(r3.Point3d(2, 0, 0), attrs)
        self.assert_only_left_changed()

    def test_detached_attributes_do_not_impersonate_document(self):
        attrs = r3.ObjectAttributes()
        attrs.Id = uuid.UUID(self.oid)
        attrs.Name = 'detached'
        attrs.SetUserString('detached', 'value')
        self.assertEqual(self.left._dirty, {})
        self.assertEqual(self.right._dirty, {})

    def test_unknown_attribute_view_still_marks_conservatively(self):
        obj = self.left.file.Objects.FindId(self.oid)
        attrs = RAW_ATTRIBUTES.fget(obj)
        attrs.Name = 'raw view'
        self.assertEqual(self.left.snapshot().by_id[self.oid]['attrs']['Name'], 'raw view')

    def test_retained_table_keeps_original_file_owner(self):
        table = self.left.file.Objects
        self.left.file = r3.File3dm()
        self.left.snapshot()
        table.Delete(self.oid)
        self.assertEqual(self.left._dirty, {})
        self.assertEqual(self.right._dirty, {})

    def test_restore_batch_does_not_dirty_other_document(self):
        snap = self.left.snapshot()
        self.left.remove_object(self.oid)
        self.right.snapshot()
        self.left.restore(snap)
        self.assertEqual(self.right._dirty, {})
        self.assertEqual(self.left.object_count(), 1)


def run_mutations():
    class MissingOwner(dict):
        def get(self, key, default=None):
            return None

    class UnknownDetached:
        def add(self, obj):
            pass

        def __contains__(self, obj):
            return False

    original_register = model._register
    original_batch = model._mark_moved_batch
    original_touched = model._touched
    mutants = (
        ('broadcast-by-uuid', '_watching', lambda file: list(model._WATCHERS),
         'test_find_id_attribute_owner'),
        ('lost-table-owner', '_TABLE_FILES', MissingOwner(), 'test_delete_owner'),
        ('lost-object-owner', '_OBJECT_FILES', MissingOwner(), 'test_index_geometry_owner'),
        ('untracked-attributes', '_register',
         lambda obj, root: None if isinstance(obj, r3.ObjectAttributes) else original_register(obj, root),
         'test_find_id_attribute_owner'),
        ('detached-id-impersonation', '_DETACHED_ATTRIBUTES', UnknownDetached(),
         'test_detached_attributes_do_not_impersonate_document'),
        ('unknown-view-ignored', '_touched',
         lambda obj, cls: None if model._root_of(obj) is None else original_touched(obj, cls),
         'test_unknown_attribute_view_still_marks_conservatively'),
        ('broadcast-restore-batch', '_mark_moved_batch',
         lambda oids, model=None: original_batch(oids),
         'test_restore_batch_does_not_dirty_other_document'),
    )
    for label, field, value, case in mutants:
        log = io.StringIO()
        with patch.object(model, field, value):
            result = unittest.TextTestRunner(stream=log).run(
                unittest.TestSuite([TrackingOwnerTests(case)]))
        if result.errors or not result.failures:
            raise AssertionError(f'{label}: expected assertion RED, not infrastructure failure\n{log.getvalue()}')
        print(f'RED assertion: {label}')
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(TrackingOwnerTests))
    if not result.wasSuccessful():
        raise AssertionError('restored owner-routing controls failed')


if __name__ == '__main__':
    if '--mutations' in sys.argv:
        run_mutations()
    else:
        unittest.main(verbosity=2)
