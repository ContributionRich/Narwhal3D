#!/usr/bin/env python3
"""Main09 real host-boundary regression tests, including raw-alias refusal/rebuild.

Uses actual rhino3dm descriptors and model snapshot/restore. Python is necessary
for exercising the host integration; no alternative registry engine is supplied.
Run with bundled Python, -B. No Blender/GUI, installed swaps or archive imports.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType('narwhal3d')
pkg.__path__ = [str(ROOT / 'narwhal3d')]
sys.modules['narwhal3d'] = pkg

# Retain a pre-instrumentation descriptor to demonstrate the explicit bypass case.
import rhino3dm as r3
RAW_NAME = r3.ObjectAttributes.Name
RAW_OBJECTS = r3.File3dm.Objects
RAW_ADD_POINT = r3.File3dmObjectTable.AddPoint
RAW_DELETE = r3.File3dmObjectTable.Delete
spec = importlib.util.spec_from_file_location('narwhal3d.model', ROOT / 'narwhal3d/model.py')
model = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = model
spec.loader.exec_module(model)


class HostBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.m = model.NarwhalModel()

    def add(self, x=1, name='same', owner=None):
        owner = owner or self.m
        return owner.add_object(r3.Point(r3.Point3d(x, 0, 0)), name)[0]

    def ids(self):
        return [str(o.Attributes.Id) for o in self.m.file.Objects]

    def test_empty_and_same_names_keep_distinct_identity(self):
        self.assertEqual(self.m.object_count(), 0)
        first, second = self.add(), self.add(2)
        self.assertNotEqual(first, second)
        self.assertEqual(self.ids(), [first, second])
        self.assertEqual(self.m.object_id_by_name('SAME'), first)

    def test_host_duplicate_uuid_remints_instead_of_refusing(self):
        first = self.add()
        attributes = r3.ObjectAttributes()
        attributes.Id = uuid.UUID(first)
        second = str(self.m.file.Objects.AddPoint(r3.Point3d(2, 0, 0), attributes))
        self.assertNotEqual(second, first)
        self.assertNotEqual(second, str(uuid.UUID(int=0)))
        self.assertEqual(self.ids(), [first, second])

    def test_table_wrapper_identity_is_not_document_identity(self):
        self.assertIs(self.m.file, self.m.file)
        self.assertIsNot(self.m.file.Objects, self.m.file.Objects)

    def test_replacement_preserves_id_attributes_and_moves_table_order(self):
        first, second = self.add(), self.add(2, 'second')
        self.m.file.Objects.FindId(first).Attributes.SetUserString('fixture', 'keep')
        self.assertTrue(self.m.replace_geometry(first, r3.Point(r3.Point3d(9, 0, 0))))
        self.assertEqual(self.ids(), [second, first])
        obj = self.m.file.Objects.FindId(first)
        self.assertEqual(obj.Attributes.GetUserString('fixture'), 'keep')
        self.assertEqual(obj.Attributes.Name, 'same')
        self.assertEqual(obj.Geometry.Location.X, 9)
        self.assertFalse(self.m.replace_geometry(str(uuid.UUID(int=0)), obj.Geometry))

    def test_missing_delete_is_host_noop(self):
        first = self.add()
        self.assertIsNone(self.m.file.Objects.Delete(str(uuid.UUID(int=0))))
        self.assertEqual(self.ids(), [first])
        self.assertFalse(self.m.remove_object(str(uuid.UUID(int=0))))

    def test_direct_attribute_and_geometry_mutations_are_snapshot_visible(self):
        oid = self.add()
        before = self.m.snapshot()
        obj = self.m.file.Objects.FindId(oid)
        obj.Attributes.Name = 'renamed'
        # Point geometry has no analytic master; this probes the rhino3dm watcher,
        # not the bridge transform contract for solids.
        self.assertTrue(obj.Geometry.Transform(r3.Transform.Translation(3, 0, 0)))
        after = self.m.snapshot()
        self.assertEqual(after.by_id[oid]['attrs']['Name'], 'renamed')
        self.assertEqual(after.by_id[oid]['geometry'].Location.X, 4)
        self.assertEqual(before.by_id[oid]['geometry'].Location.X, 1)
        self.assertIsNot(after.by_id[oid], before.by_id[oid])

    def test_direct_add_delete_and_restore_logical_order(self):
        first, second = self.add(), self.add(2)
        self.m.register_blender_object(first, 'Display1')
        before = self.m.snapshot()
        self.m.file.Objects.Delete(first)
        third = str(self.m.file.Objects.AddPoint(r3.Point3d(3, 0, 0)))
        self.assertEqual(list(self.m.snapshot().by_id), [second, third])
        self.m.restore(before)
        self.assertEqual(list(self.m.snapshot().by_id), [first, second])
        self.assertEqual(self.m.blender_name_for(first), 'Display1')
        self.m.normalize_order()
        self.assertEqual(self.ids(), [first, second])

    def test_layer_and_settings_roundtrip_share_existing_snapshot(self):
        self.add()
        before = self.m.snapshot()
        self.m.file.Layers[0].Name = 'changed layer'
        self.m.file.Settings.ModelAbsoluteTolerance = 0.75
        self.m.restore(before)
        self.assertEqual(self.m.file.Layers[0].Name, 'Default')
        self.assertEqual(self.m.absolute_tolerance(), 0.01)
        self.assertEqual(self.m.object_count(), 1)

    def test_cross_owner_restore_refuses_before_mutation(self):
        self.add()
        snapshot = self.m.snapshot()
        other = model.NarwhalModel()
        oid = self.add(7, owner=other)
        with self.assertRaises(Exception):
            other.restore(snapshot)
        self.assertEqual(other.object_count(), 1)
        self.assertEqual(other.file.Objects.FindId(oid).Geometry.Location.X, 7)

    def test_retained_raw_table_same_count_replacement_is_untracked(self):
        oid = self.add()
        before = self.m.snapshot()
        table = RAW_OBJECTS.fget(self.m.file)
        attrs = r3.ObjectAttributes()
        attrs.Id = uuid.UUID(oid)
        attrs.Name = 'same'
        RAW_DELETE(table, oid)
        self.assertEqual(str(RAW_ADD_POINT(table, r3.Point3d(99, 0, 0), attrs)), oid)
        self.assertEqual(self.m.object_count(), 1)
        self.assertEqual(self.m.file.Objects.FindId(oid).Geometry.Location.X, 99)
        self.assertEqual(self.m._dirty, {})
        with self.assertRaisesRegex(RuntimeError, 'unmediated_object_payload_write'):
            self.m.snapshot()
        self.assertFalse(self.m.object_registry_revision()['authoritative'])
        self.assertEqual(before.by_id[oid]['geometry'].Location.X, 1)
        after = self.m.rebuild_object_registry()
        self.assertEqual(after.by_id[oid]['geometry'].Location.X, 99)
        self.assertIsNot(after.by_id[oid], before.by_id[oid])
        self.m.restore(before)
        self.assertEqual(self.m.file.Objects.FindId(oid).Geometry.Location.X, 1)

    def test_unreported_order_only_change_refuses_and_restore_normalizes(self):
        first, second = self.add(), self.add(2)
        before = self.m.snapshot()
        attrs = self.m.file.Objects.FindId(first).Attributes
        RAW_DELETE(self.m.file.Objects, first)
        RAW_ADD_POINT(self.m.file.Objects, r3.Point3d(1, 0, 0), attrs)
        with self.assertRaisesRegex(RuntimeError, 'unmediated_object_index_write'):
            self.m.snapshot()
        rebuilt = self.m.rebuild_object_registry()
        self.assertEqual(list(rebuilt.by_id), [second, first])
        self.m.restore(before)
        self.assertEqual(list(self.m.snapshot().by_id), [first, second])
        self.assertTrue(self.m.order_pending())
        self.m.normalize_order()
        self.assertEqual(self.ids(), [first, second])

    def test_preinstrumentation_setter_is_a_real_untracked_bypass(self):
        oid = self.add()
        before = self.m.snapshot()
        obj = self.m.file.Objects.FindId(oid)
        RAW_NAME.fset(obj.Attributes, 'bypassed')
        self.assertEqual(obj.Attributes.Name, 'bypassed')
        with self.assertRaisesRegex(RuntimeError, 'unmediated_object_index_write'):
            self.m.snapshot()
        self.assertFalse(self.m.object_registry_revision()['authoritative'])
        self.assertEqual(before.by_id[oid]['attrs']['Name'], 'same')
        after = self.m.rebuild_object_registry()
        self.assertEqual(after.by_id[oid]['attrs']['Name'], 'bypassed')
        self.assertIsNot(after.by_id[oid], before.by_id[oid])
        self.m.restore(before)
        self.assertEqual(self.m.file.Objects.FindId(oid).Attributes.Name, 'same')


if __name__ == '__main__':
    unittest.main(verbosity=2)
