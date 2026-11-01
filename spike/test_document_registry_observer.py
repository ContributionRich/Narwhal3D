#!/usr/bin/env python3
"""Real host tests for the candidate-only read-only openNURBS observer.

Python is necessary to exercise rhino3dm aliases and inspect native archive output.
This decoder is a test oracle, not a production registry or fallback implementation.
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path
import struct
import unittest
import uuid

import rhino3dm as r3

observer = None
rust_check = None


def decode(data):
    assert type(data) is bytes and data[:8] in (b'NWOBS01\0', b'NWRAW01\0')
    count, = struct.unpack_from('<I', data, 8)
    offset = 12
    rows = []
    for _ in range(count):
        oid = data[offset:offset + 36].decode('ascii')
        assert str(uuid.UUID(oid)) == oid
        offset += 36
        fields = []
        for _ in range(3):
            length, = struct.unpack_from('<I', data, offset)
            offset += 4
            field = data[offset:offset + length]
            assert len(field) == length
            offset += length
            fields.append(field)
        rows.append((oid, fields[0].decode('utf-8'), fields[1], fields[2]))
    assert offset == len(data)
    return rows


def unarchive(data, reference):
    encoded = reference.Encode()
    encoded['archive3dm'] = 80
    encoded['data'] = base64.b64encode(data).decode('ascii')
    result = r3.CommonObject.Decode(encoded)
    assert result is not None, 'observer archive cannot be read by installed rhino3dm'
    return result


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.file = r3.File3dm()
        layer = r3.Layer()
        layer.Name = 'Default'
        self.file.Layers.Add(layer)

    def add(self, geom=None, name='same'):
        attrs = r3.ObjectAttributes()
        attrs.Name = name
        return str(self.file.Objects.Add(geom or r3.Point(r3.Point3d(1, 0, 0)), attrs))

    def test_index_does_not_serialize_geometry_and_preserves_names(self):
        oid = self.add(r3.Sphere(r3.Point3d(0, 0, 0), 2).ToBrep(), name='海')
        data = observer.index(self.file)
        self.assertEqual(data[:12], b'NWIDX01\0\1\0\0\0')
        self.assertEqual(data[12:48].decode('ascii'), oid)
        length, = struct.unpack_from('<I', data, 48)
        self.assertEqual(data[52:].decode('utf-8'), '海')
        self.assertEqual(len(data), 52 + length)
        self.file.Objects.FindId(oid).Geometry.Transform(r3.Transform.Translation(4, 0, 0))
        self.assertEqual(observer.index(self.file), data)

    def test_complete_attribute_copy_is_detached(self):
        oid = self.add(name='all fields')
        original = self.file.Objects.FindId(oid).Attributes
        original.PlotWeight = 0.75
        original.AddToGroup(9)
        original.SetUserString('.hidden', 'value')
        original.Visible = False
        before = original.Encode()
        holder = r3.File3dm()
        cloned = observer.copy_attributes(self.file, oid, holder)
        self.assertIsInstance(cloned, r3.ObjectAttributes)
        self.assertEqual(cloned.Encode(), before)
        self.assertEqual(list(cloned.GetGroupList2()), [9])
        self.assertEqual(cloned.PlotWeight, 0.75)
        cloned.Name = 'detached'
        cloned.SetUserString('.hidden', 'detached value')
        self.assertEqual(original.Encode(), before)
        self.assertEqual(original.Name, 'all fields')

    def test_lazy_open_brep_solidity_query_is_not_a_document_edit(self):
        surface = r3.PlaneSurface(r3.Plane.WorldXY(), r3.Interval(0, 10), r3.Interval(0, 8))
        oid = self.add(r3.Brep.CreateFromSurface(surface))
        before = observer.observe(self.file)
        self.assertFalse(self.file.Objects.FindId(oid).Geometry.IsSolid)
        self.assertEqual(before, observer.observe(self.file), 'lazy solidity cache is not geometry state')

    def test_lazy_brep_bounding_box_query_is_not_a_document_edit(self):
        plane = r3.Plane(r3.Point3d(2, 3, 0), r3.Vector3d(1, 0, 0), r3.Vector3d(0, 1, 0))
        oid = self.add(r3.Brep.CreateFromSurface(r3.PlaneSurface(plane, r3.Interval(0, 10), r3.Interval(0, 8))))
        before = observer.observe(self.file)
        box = self.file.Objects.FindId(oid).Geometry.GetBoundingBox()
        self.assertEqual((box.Min.X, box.Min.Y, box.Max.X, box.Max.Y), (2, 3, 12, 11))
        self.assertEqual(before, observer.observe(self.file), 'lazy bounding-box cache is not geometry state')

    def test_userdata_copy_counters_are_not_geometry_changes(self):
        oid = self.add()
        obj = self.file.Objects.FindId(oid)
        obj.Geometry.SetUserString('geometry', 'retained')
        obj.Attributes.SetUserString('attribute', 'retained')
        before = observer.observe(self.file)
        raw_before = observer.raw_observe(self.file)
        geometry = obj.Geometry.Duplicate()
        holder = r3.File3dm()
        attributes = observer.copy_attributes(self.file, oid, holder)
        self.file.Objects.Delete(oid)
        self.file.Objects.Add(geometry, attributes)
        self.assertNotEqual(raw_before, observer.raw_observe(self.file))
        self.assertEqual(before, observer.observe(self.file))
        self.assertEqual(self.file.Objects.FindId(oid).Geometry.GetUserString('geometry'), 'retained')
        self.assertEqual(self.file.Objects.FindId(oid).Attributes.GetUserString('attribute'), 'retained')

    def test_empty_and_repeat_are_stable(self):
        self.assertEqual(decode(observer.observe(self.file)), [])
        self.add()
        self.assertEqual(observer.observe(self.file), observer.observe(self.file))

    def test_raw_name_setter_detected(self):
        oid = self.add()
        before = observer.observe(self.file)
        r3.ObjectAttributes.Name.fset(self.file.Objects.FindId(oid).Attributes, 'changed')
        after = observer.observe(self.file)
        self.assertNotEqual(before, after)
        self.assertEqual(decode(after)[0][1], 'changed')

    def test_same_uuid_same_count_raw_replacement_detected(self):
        oid = self.add()
        before = observer.observe(self.file)
        attrs = self.file.Objects.FindId(oid).Attributes
        self.file.Objects.Delete(oid)
        self.assertEqual(str(self.file.Objects.AddPoint(r3.Point3d(99, 0, 0), attrs)), oid)
        after = observer.observe(self.file)
        self.assertEqual(len(self.file.Objects), 1)
        self.assertNotEqual(before, after)
        row = decode(after)[0]
        self.assertEqual(row[0], oid)
        raw = decode(observer.raw_observe(self.file))[0]
        self.assertEqual(unarchive(raw[2], self.file.Objects.FindId(oid).Geometry).Location.X, 99)

    def test_order_and_deleted_objects(self):
        first, second = self.add(), self.add()
        attrs = self.file.Objects.FindId(first).Attributes
        self.file.Objects.Delete(first)
        self.file.Objects.AddPoint(r3.Point3d(2, 0, 0), attrs)
        self.assertEqual([row[0] for row in decode(observer.observe(self.file))], [second, first])
        self.file.Objects.Delete(second)
        self.assertEqual([row[0] for row in decode(observer.observe(self.file))], [first])

    def test_in_place_geometry_detected(self):
        oid = self.add()
        before = observer.observe(self.file)
        self.file.Objects.FindId(oid).Geometry.Transform(r3.Transform.Translation(4, 0, 0))
        self.assertNotEqual(before, observer.observe(self.file))

    def test_unicode_name(self):
        self.add(name='\u0130\u03a3\U0001f40b\u6d77')
        self.assertEqual(decode(observer.observe(self.file))[0][1], '\u0130\u03a3\U0001f40b\u6d77')

    def test_geometry_user_text_roundtrip(self):
        oid = self.add()
        geometry = self.file.Objects.FindId(oid).Geometry
        before = observer.observe(self.file)
        geometry.SetUserString('recipe-witness', 'exact tree, not mesh')
        after = observer.observe(self.file)
        self.assertNotEqual(before, after)
        restored = unarchive(decode(observer.raw_observe(self.file))[0][2], geometry)
        self.assertEqual(restored.GetUserString('recipe-witness'), 'exact tree, not mesh')

    def test_attributes_user_text_groups_and_plot_weight_roundtrip(self):
        oid = self.add()
        attrs = self.file.Objects.FindId(oid).Attributes
        before = observer.observe(self.file)
        attrs.SetUserString('.hidden', 'preserved')
        user_text = observer.observe(self.file)
        self.assertNotEqual(before, user_text)
        attrs.AddToGroup(7)
        grouped = observer.observe(self.file)
        self.assertNotEqual(user_text, grouped)
        attrs.PlotWeight = 0.75
        after = observer.observe(self.file)
        self.assertNotEqual(grouped, after)
        restored = unarchive(decode(observer.raw_observe(self.file))[0][3], attrs)
        self.assertEqual(restored.GetUserString('.hidden'), 'preserved')
        # rhino3dm decodes attributes as CommonObject, not ObjectAttributes.
        # Normalize BOTH independent archive routes through that same decoder.
        expected = r3.CommonObject.Decode(attrs.Encode())
        self.assertEqual(restored.Encode(), expected.Encode())

    def test_curved_geometry_keeps_exact_archive_and_faces(self):
        shapes = [r3.Sphere(r3.Point3d(0, 0, 0), 2).ToBrep(),
                  r3.Cylinder(r3.Circle(r3.Point3d(0, 0, 0), 2), 5).ToBrep(True, True),
                  r3.Circle(r3.Point3d(0, 0, 0), 2).ToNurbsCurve()]
        for shape in shapes:
            self.add(shape)
        rows = decode(observer.raw_observe(self.file))
        for row, obj in zip(rows, self.file.Objects):
            geometry = obj.Geometry
            restored = unarchive(row[2], geometry)
            self.assertEqual(type(restored), type(geometry))
            if isinstance(geometry, r3.Brep):
                self.assertEqual(len(restored.Faces), len(geometry.Faces))
                self.assertEqual(restored.IsSolid, geometry.IsSolid)
            else:
                self.assertEqual(restored.Degree, 2)
                self.assertTrue(restored.IsRational)
                self.assertEqual(len(restored.Points), 9)

    def test_observation_does_not_change_host_archives(self):
        self.add(r3.Sphere(r3.Point3d(0, 0, 0), 2).ToBrep())
        obj = self.file.Objects[0]
        obj.Attributes.SetUserString('unchanged', 'yes')
        before = (obj.Geometry.Encode(), obj.Attributes.Encode())
        for _ in range(10):
            observer.observe(self.file)
        self.assertEqual(before, (obj.Geometry.Encode(), obj.Attributes.Encode()))

    def test_actual_native_batch_passes_rust_decoder(self):
        if rust_check is None:
            self.skipTest('explicit Rust observation_check executable required')
        first = self.add(name='海🐋')
        second = self.add(r3.Sphere(r3.Point3d(0, 0, 0), 2).ToBrep(), name='sphere')
        data = observer.observe(self.file)
        with tempfile.TemporaryDirectory(prefix='nw-observation-', dir='/var/tmp') as directory:
            path = Path(directory) / 'batch.bin'
            path.write_bytes(data)
            result = subprocess.run([str(rust_check), str(path)], capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            reply = json.loads(result.stdout)
            self.assertEqual(reply['count'], 2)
            self.assertEqual([row['id'] for row in reply['rows']], [first, second])
            self.assertEqual([row['name'] for row in reply['rows']], ['海🐋', 'sphere'])
            for rust_row, native_row in zip(reply['rows'], decode(data)):
                self.assertEqual(rust_row['geometry_bytes'], len(native_row[2]))
                self.assertEqual(rust_row['attribute_bytes'], len(native_row[3]))
            path.write_bytes(data[:-1])
            refused = subprocess.run([str(rust_check), str(path)], capture_output=True, timeout=15)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn(b'observation_truncated', refused.stderr)

    def test_wrong_type_refuses(self):
        for obj in (None, 7, r3.Point(r3.Point3d(0, 0, 0)), self.file.Objects):
            with self.assertRaises((TypeError, AttributeError, ValueError)):
                observer.observe(obj)

    def test_oversized_name_refuses(self):
        self.add(name='x' * 4097)
        with self.assertRaisesRegex(RuntimeError, 'name limit'):
            observer.observe(self.file)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', required=True, type=Path)
    parser.add_argument('--case', choices=unittest.defaultTestLoader.getTestCaseNames(ObserverTests))
    parser.add_argument('--rust-check', type=Path)
    args = parser.parse_args()
    rust_check = args.rust_check.resolve() if args.rust_check else None
    spec = importlib.util.spec_from_file_location('_narwhal_document_observer', args.library.resolve())
    observer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(observer)
    suite = (unittest.TestSuite([ObserverTests(args.case)]) if args.case else
             unittest.defaultTestLoader.loadTestsFromTestCase(ObserverTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
