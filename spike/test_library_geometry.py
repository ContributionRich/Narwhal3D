#!/usr/bin/env python3
"""Focused Library host tests. Run with bundled Python -B or Blender -b.

Blender run additionally qualifies actual isolated thumbnail rendering; Python
runs all geometry/history tests, substituting only the unavailable render host.
LIBRARY_GEOMETRY_MUTATION=materials|transform deliberately breaks each boundary.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import types
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Avoid initializing the application's UI when exercising its host APIs.
pkg = types.ModuleType('narwhal3d')
pkg.__path__ = [str(ROOT / 'narwhal3d')]
sys.modules['narwhal3d'] = pkg
import rhino3dm as r3
from narwhal3d import bridge, commands, kernel, library_geometry as lib, narwhal_document
from narwhal3d.kernel import occt_backend as occt
from narwhal3d.model import NarwhalModel
try:
    import bpy
except ImportError:
    bpy = None


class Store:
    """No catalogue substitute: just the contract's synchronous byte-copy boundary."""
    def __init__(self, root):
        self.root = root
        self.count = 0

    def add_asset(self, name, category_id, source_path, thumbnail_path):
        self.count += 1
        directory = self.root / str(self.count)
        directory.mkdir()
        source, thumbnail = directory / 'asset.narwhal', directory / 'thumbnail.png'
        shutil.copyfile(source_path, source)
        shutil.copyfile(thumbnail_path, thumbnail)
        return dict(id=str(self.count), name=name, category_id=category_id,
                    source_file=str(source), thumbnail=str(thumbnail))


class Context(commands.NarwhalContext):
    """Real document and native Undo; only display publication is host-independent."""
    def __init__(self, model=None):
        super().__init__(model or NarwhalModel())
        self.selection = []
        self.display_calls = 0

    def selected_objects(self):
        return [types.SimpleNamespace(name=name) for name in self.selection]

    def select_by_names(self, names, **kwargs):
        self.selection = list(names)

    def rebuild_display(self):
        self.display_calls += 1

    def _make_file_object_display(self, item, layers):
        self.display_calls += 1
        return object()

    def _restore_document(self, snapshot):
        self.model.restore(snapshot)
        self.rebuild_display()
        return True


def host_properties(value):
    """Public host values, not archival cache serials/CRC (Encode changes on read)."""
    out = {}
    for key in dir(value):
        if key.startswith('_'):
            continue
        try:
            field = getattr(value, key)
        except Exception:
            continue
        if isinstance(field, (str, int, float, bool, tuple, uuid.UUID)):
            out[key] = field
        elif type(field).__module__.startswith('rhino3dm') and not callable(field):
            if hasattr(field, 'value'):
                out[key] = str(field)
    if hasattr(value, 'GetUserStrings2'):
        out['user_strings'] = sorted(value.GetUserStrings2() or [])
    return out


def geometry_signature(geometry):
    box = geometry.GetBoundingBox()
    out = dict(type=type(geometry).__name__,
               bounds=(box.Min.X, box.Min.Y, box.Min.Z, box.Max.X, box.Max.Y, box.Max.Z),
               strings=sorted(geometry.GetUserStrings2() or []))
    if isinstance(geometry, r3.Curve):
        curve = geometry.ToNurbsCurve()
        out['nurbs'] = (curve.Degree, curve.IsRational, list(curve.Knots),
                       [(p.X,p.Y,p.Z,p.W) for p in curve.Points])
    if isinstance(geometry, r3.Brep):
        out['faces'] = [f.IsPlanar() for f in geometry.Faces]
    return out


def signature(model):
    return dict(objects=[(str(o.Attributes.Id), geometry_signature(o.Geometry),
                          host_properties(o.Attributes), list(o.Attributes.GetGroupList2() or []))
                         for o in model.file.Objects],
                layers=[host_properties(o) for o in model.file.Layers],
                groups=model.group_records(),
                materials=[host_properties(o) for o in model.file.Materials],
                settings=(model.unit_system(), model.absolute_tolerance()),
                strings=list(model.file.Strings))


class LibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.started = time.perf_counter()

    @classmethod
    def tearDownClass(cls):
        print('LIBRARY host workload seconds:', round(time.perf_counter() - cls.started, 3))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='library-geometry-test-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = Store(self.root)
        if bpy is None:
            # Geometry suite remains runnable in bundled Python without pretending
            # that a placeholder qualifies the real Blender thumbnail test below.
            self.render_patch = patch.object(lib, '_thumbnail',
                side_effect=lambda model, path: Path(path).write_bytes(b'host-render-unavailable'))
            self.render_patch.start()
            self.addCleanup(self.render_patch.stop)

    def fixture(self):
        ctx = Context()
        model = ctx.model
        layer = model.add_layer('Asset layer', (30, 90, 170))
        group = r3.Group(); group.Name = 'Source group'
        model.file.Groups.Add(group)
        geometries = [bridge.make_box((0, 0, 0), (25.4, 25.4, 0), 25.4),
                      bridge.make_cylinder((35, 10, 0), 5, 25.4),
                      bridge.make_circle((0, 0, 30), 6)]
        material = r3.Material(); material.Name = 'Shared name'; material.DiffuseColor = (190, 30, 80, 255)
        material.SetUserString('asset-material', 'source')
        mi = model.file.Materials.Add(material)
        model.file.Layers[layer].RenderMaterialIndex = mi
        for geometry, name in zip(geometries, ('Box', 'Cylinder', 'Circle')):
            oid, _ = model.add_object(geometry, name, layer)
            model.file.Objects.FindId(oid).Attributes.AddToGroup(0)
            model.file.Objects.FindId(oid).Attributes.SetUserString('.hidden', 'preserved')
            model.set_object_material(oid, mi)
        model.add_object(bridge.make_line((999, 0, 0), (999, 1, 0)), 'Excluded')
        ctx.selection = ['Box', 'Cylinder', 'Circle']
        return ctx

    def asset(self):
        ctx = self.fixture()
        return ctx, lib.capture_selection(ctx, self.store, 'Exact assembly', 'uncategorized')

    def test_material_snapshot_restore_ids_indices_and_layer_refs(self):
        ctx = self.fixture(); m = ctx.model
        before = m.snapshot(); expected = signature(m)
        first_id = m.file.Materials[0].Id
        m.file.Materials[0].Name = 'Changed definition'
        m.file.Materials[0].DiffuseColor = (1, 2, 3, 255)
        extra = r3.Material(); extra.Name = 'Added'; m.file.Materials.Add(extra)
        m.file.Layers[1].RenderMaterialIndex = 1
        m.snapshot()
        m.restore(before)
        self.assertEqual(signature(m), expected)
        self.assertEqual(m.file.Materials[0].Id, first_id)
        self.assertEqual(m.file.Layers[1].RenderMaterialIndex, 0)

    def test_newly_saved_asset_first_insert_reuses_validated_private_model(self):
        source, asset = self.asset()
        source_state = signature(source.model)
        source_bytes = Path(asset['source_file']).read_bytes()
        ctx = Context()
        with patch.object(lib, '_read', side_effect=AssertionError('unnecessary second validation')):
            names = lib.insert_asset(ctx, asset)
        self.assertEqual(len(names), 3)
        self.assertEqual(Path(asset['source_file']).read_bytes(), source_bytes)
        self.assertEqual(len(ctx.undo.undo_names()), 1)
        self.assertEqual(signature(source.model), source_state)

    @unittest.skipIf(bpy is None, 'requires real Blender thumbnail renderer')
    def test_prepared_capture_worker_uses_frozen_files_then_admits_on_main(self):
        import threading
        ctx = self.fixture()
        expected_count = len(ctx.selection)
        pending = lib.prepare_capture(ctx, self.store, 'Frozen', 'uncategorized')
        ctx.model.add_object(bridge.make_circle((500,500,0),2), 'Later edit')
        ctx.selection = ['Later edit']
        result, errors = [], []
        def finish():
            try: result.append(pending.finish())
            except BaseException as exc: errors.append(exc)
        worker = threading.Thread(target=finish, daemon=True)
        worker.start();worker.join(30)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        pending.admit(result[0])
        target = Context()
        with patch.object(lib, '_read', side_effect=AssertionError('unnecessary second validation')):
            names = lib.insert_asset(target, result[0])
        self.assertEqual(len(names), expected_count)
        self.assertEqual(ctx.selection, ['Later edit'])
        self.assertFalse(Path(pending.directory.name).exists())

    @unittest.skipIf(bpy is None, 'requires real Blender thumbnail renderer')
    def test_prepared_capture_validation_failure_never_publishes(self):
        ctx = self.fixture()
        expected = signature(ctx.model)
        pending = lib.prepare_capture(ctx, self.store, 'Refused', 'uncategorized')
        with patch.object(lib.narwhal_document, '_validate_and_pack', side_effect=RuntimeError('injected validator refusal')):
            with self.assertRaisesRegex(RuntimeError, 'injected validator refusal'):
                pending.finish()
        self.assertEqual(self.store.count, 0)
        self.assertEqual(signature(ctx.model), expected)
        self.assertEqual(ctx.undo.undo_names(), [])
        self.assertFalse(Path(pending.directory.name).exists())

    def test_material_compaction_failure_keeps_live_file_unchanged(self):
        ctx = self.fixture(); m = ctx.model
        m.snapshot()
        records = m.material_records(); expected = signature(m); backing = m.file
        with patch.object(r3.File3dm, 'Write', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'compact'):
                m._replace_material_table(records)
        self.assertIs(m.file, backing)
        self.assertEqual(signature(m), expected)
        self.assertTrue(m.object_registry_revision()['authoritative'])

    def test_material_undo_redo_one_record(self):
        ctx = Context(); initial = signature(ctx.model)
        ctx.begin_undo('Material definitions'); ctx.note_change()
        material = r3.Material(); material.Name = 'Added material'
        ctx.model.file.Materials.Add(material)
        oid, _ = ctx.model.add_object(bridge.make_circle((0, 0, 0), 3), 'Curve')
        ctx.model.set_object_material(oid, 0)
        ctx.end_undo(); after = signature(ctx.model)
        self.assertEqual(ctx.undo_last(), 'Material definitions')
        self.assertEqual(signature(ctx.model), initial)
        self.assertEqual(ctx.redo_last(), 'Material definitions')
        self.assertEqual(signature(ctx.model), after)

    def test_material_only_edit_does_not_reuse_stale_definitions(self):
        m = NarwhalModel(); material = r3.Material(); material.Name = 'Before'
        m.file.Materials.Add(material)
        first = m.snapshot()
        m.file.Materials[0].Name = 'After'
        second = m.snapshot()
        self.assertNotEqual(first.materials, second.materials)
        m.restore(first); self.assertEqual(m.file.Materials[0].Name, 'Before')
        m.restore(second); self.assertEqual(m.file.Materials[0].Name, 'After')

    def test_capture_subset_immutable_and_reload_independent(self):
        ctx = self.fixture(); before = signature(ctx.model)
        old_selection = list(ctx.selection)
        old_history = ctx.undo.undo_names()
        asset = lib.capture_selection(ctx, self.store, 'Exact', 'uncategorized')
        self.assertEqual(signature(ctx.model), before)
        self.assertEqual(ctx.selection, old_selection)
        self.assertEqual(ctx.undo.undo_names(), old_history)
        self.assertTrue(Path(asset['source_file']).is_file())
        candidate = lib._read(Path(asset['source_file']))
        self.assertEqual(candidate.object_count(), 3)
        items = {o.Attributes.Name: o for o in candidate.file.Objects}
        self.assertNotIn('Excluded', items)
        self.assertEqual(len(items['Box'].Geometry.Faces), 6)
        cylinder = items['Cylinder'].Geometry
        self.assertEqual(len(cylinder.Faces), 3)
        self.assertEqual(sum(f.IsPlanar() for f in cylinder.Faces), 2)
        circle = items['Circle'].Geometry.ToNurbsCurve()
        self.assertTrue(circle.IsRational)
        self.assertEqual(circle.Degree, 2)
        self.assertEqual(len(circle.Points), 9)
        # The store's file outlives all capture temporary directories and the source model.
        del candidate, ctx
        reopened = lib._read(Path(asset['source_file']))
        self.assertEqual(reopened.object_count(), 3)

    def test_two_insertions_groups_material_collisions_units_and_single_undo(self):
        source, asset = self.asset()
        source_sig = signature(source.model)
        bytes_before = Path(asset['source_file']).read_bytes()
        ctx = Context(); ctx.model.file.Settings.ModelUnitSystem = r3.UnitSystem.Inches
        existing = r3.Material(); existing.Name = 'Shared name'; existing.SetUserString('asset-material', 'destination')
        ctx.model.file.Materials.Add(existing)
        start = signature(ctx.model)
        one = lib.insert_asset(ctx, asset, point=(10, 20, 30))
        first = signature(ctx.model)
        self.assertEqual(len(one), 3)
        self.assertEqual(len(ctx.undo.undo_names()), 1)
        self.assertEqual(ctx.model.file.Materials[0].GetUserString('asset-material'), 'destination')
        self.assertEqual(ctx.model.file.Materials[1].GetUserString('asset-material'), 'source')
        objects = list(ctx.model.file.Objects)
        box = objects[0].Geometry.GetBoundingBox()
        self.assertAlmostEqual(box.Max.X - box.Min.X, 1, places=10)
        self.assertAlmostEqual(box.Min.Z, 30, places=10)
        self.assertTrue(set(objects[0].Attributes.GetGroupList2()) & set(objects[1].Attributes.GetGroupList2()))
        self.assertEqual(objects[0].Attributes.GetUserString('.hidden'), 'preserved')
        self.assertEqual(objects[0].Attributes.MaterialIndex, 1)
        self.assertNotEqual(ctx.model.file.Materials[1].Id, source.model.file.Materials[0].Id)
        self.assertNotEqual(ctx.model.file.Materials[1].Id, ctx.model.file.Materials[0].Id)
        self.assertEqual(ctx.undo_last(), 'Insert library asset')
        self.assertEqual(signature(ctx.model), start)
        self.assertIsNone(ctx.undo_last())
        self.assertEqual(ctx.redo_last(), 'Insert library asset')
        self.assertEqual(signature(ctx.model), first)
        two = lib.insert_asset(ctx, asset, point=(40, 50, 60))
        self.assertTrue(set(one).isdisjoint(two))
        objects = list(ctx.model.file.Objects)
        self.assertTrue(set(objects[0].Attributes.GetGroupList2()).isdisjoint(objects[3].Attributes.GetGroupList2()))
        self.assertEqual(Path(asset['source_file']).read_bytes(), bytes_before)
        self.assertEqual(signature(source.model), source_sig)
        self.assertEqual(len(ctx.undo.undo_names()), 2)

    def test_failure_after_first_object_restores_all_tables_and_redo(self):
        _, asset = self.asset(); ctx = Context()
        initial = signature(ctx.model)
        calls = 0
        def fail(item, layers):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('injected display publication failure')
            return object()
        with patch.object(ctx, '_make_file_object_display', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'injected'):
                lib.insert_asset(ctx, asset)
        self.assertEqual(signature(ctx.model), initial)
        self.assertEqual(ctx.undo.undo_names(), [])
        self.assertFalse(ctx.undo.recording)
        self.assertTrue(ctx.model.object_registry_revision()['authoritative'])

    def test_failed_insertion_into_existing_document_keeps_selection_and_redo(self):
        _, asset = self.asset()
        ctx = self.fixture()
        ctx.counters = {'Box': 12}
        ctx.begin_undo('Prior curve'); ctx.note_change()
        ctx.model.add_object(bridge.make_circle((0,0,0), 1), 'Prior curve')
        ctx.end_undo(); ctx.undo_last()
        expected = signature(ctx.model)
        selection = list(ctx.selection)
        history, redo = ctx.undo.undo_names(), ctx.undo.redo_names()
        with patch.object(ctx, '_make_file_object_display', side_effect=RuntimeError('injected')):
            with self.assertRaisesRegex(RuntimeError, 'injected'):
                lib.insert_asset(ctx, asset)
        self.assertEqual(signature(ctx.model), expected)
        self.assertEqual(ctx.selection, selection)
        self.assertEqual(ctx.counters, {'Box': 12})
        self.assertEqual(ctx.undo.undo_names(), history)
        self.assertEqual(ctx.undo.redo_names(), redo)
        self.assertEqual(ctx.redo_last(), 'Prior curve')

    def test_nested_layers_refused_before_document_publication(self):
        ctx = self.fixture()
        child = ctx.model.add_layer('Child layer', (10,20,30))
        ctx.model.file.Layers[child].ParentLayerId = ctx.model.file.Layers[1].Id
        asset = lib.capture_selection(ctx, self.store, 'Layer hierarchy', 'uncategorized')
        target = Context(); before = signature(target.model)
        with self.assertRaisesRegex(lib.LibraryGeometryError, 'nested layer'):
            lib.insert_asset(target, asset)
        self.assertEqual(signature(target.model), before)
        self.assertEqual(target.undo.undo_names(), [])

    def test_master_transform_follows_copy_without_aliasing(self):
        ctx = Context()
        geometry = bridge.boolean_brep(bridge.make_box((0, 0, 0), (10, 10, 0), 10),
                    bridge.make_cylinder((10, 5, 0), 3, 10), 'UNION')
        ctx.model.add_object(geometry, 'Master')
        ctx.selection = ['Master']
        master = kernel.master_of(geometry)
        original = occt.tight_bbox(master)
        asset = lib.capture_selection(ctx, self.store, 'Master', 'uncategorized')
        target = Context()
        names = lib.insert_asset(target, asset, point=(100, 50, 20))
        copied = target.geom_by_name(names[0])
        shape = kernel.master_of(copied)
        self.assertIsNotNone(shape)
        self.assertEqual(occt.face_types(shape), occt.face_types(master))
        low, high = occt.tight_bbox(shape)
        self.assertAlmostEqual(low[2], 20, places=7)
        self.assertAlmostEqual((low[0] + high[0]) / 2, 100, places=7)
        self.assertEqual(occt.tight_bbox(master), original)
        recipe_shape = occt.from_recipe(kernel.recipe_of(copied))
        self.assertEqual(occt.face_types(recipe_shape), occt.face_types(shape))
        for actual, expected in zip(occt.tight_bbox(recipe_shape)[0], low):
            self.assertAlmostEqual(actual, expected, places=7)

    def test_import_3dm_and_existing_svg_reader(self):
        ctx = self.fixture(); path = self.root / 'source.3dm'
        self.assertTrue(ctx.model.save(str(path)))
        before = signature(ctx.model)
        asset = lib.import_asset(ctx, self.store, str(path), 'Imported', 'uncategorized')
        self.assertEqual(lib._read(Path(asset['source_file'])).object_count(), 4)
        svg = self.root / 'circle.svg'
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="20mm" height="20mm" viewBox="0 0 20 20"><circle cx="10" cy="10" r="5"/></svg>')
        asset = lib.import_asset(ctx, self.store, str(svg), 'Drawing', 'uncategorized')
        candidate = lib._read(Path(asset['source_file']))
        self.assertEqual(candidate.object_count(), 1)
        self.assertTrue(next(iter(candidate.file.Objects)).Geometry.ToNurbsCurve().IsRational)
        self.assertEqual(signature(ctx.model), before)

    def test_import_native_step_and_explicit_mesh_formats(self):
        ctx, native_asset = self.asset()
        asset = lib.import_asset(ctx, self.store, native_asset['source_file'], 'Native import', 'uncategorized')
        self.assertEqual(lib._read(Path(asset['source_file'])).object_count(), 3)
        step = self.root / 'cylinder.step'
        shape = bridge._occt_operand(bridge.make_cylinder((0,0,0), 3, 8))
        self.assertTrue(occt.export_step(shape, str(step)))
        asset = lib.import_asset(ctx, self.store, str(step), 'STEP import', 'uncategorized')
        candidate = lib._read(Path(asset['source_file']))
        geometry = next(iter(candidate.file.Objects)).Geometry
        self.assertEqual(occt.face_types(kernel.master_of(geometry)), {'CYLINDER': 1, 'PLANE': 2})
        obj = self.root / 'triangle.obj'
        obj.write_text('v 0 0 0\nv 10 0 0\nv 0 10 0\nf 1 2 3\n')
        asset = lib.import_asset(ctx, self.store, str(obj), 'Explicit mesh', 'uncategorized')
        geometry = next(iter(lib._read(Path(asset['source_file'])).file.Objects)).Geometry
        self.assertIsInstance(geometry, r3.Mesh)

    def test_empty_selection_invalid_source_and_busy_are_noops(self):
        ctx = Context(); before = signature(ctx.model)
        with self.assertRaisesRegex(lib.LibraryGeometryError, 'Select geometry'):
            lib.capture_selection(ctx, self.store, 'Empty', 'uncategorized')
        with self.assertRaises(lib.LibraryGeometryError):
            lib.insert_asset(ctx, dict(source_file='relative.narwhal', thumbnail='relative.png'))
        ctx.begin_undo('Other command')
        with self.assertRaisesRegex(lib.LibraryGeometryError, 'idle'):
            lib.insert_asset(ctx, {})
        self.assertTrue(ctx.undo.recording)
        ctx.undo.abandon()
        self.assertEqual(signature(ctx.model), before)

    def test_picture_bytes_survive_source_removal_and_target_reload(self):
        from narwhal3d import picture
        # Real 1x1 PNG, not the thumbnail's imagery.
        import struct, zlib
        def chunk(kind, data):
            return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
        image = self.root / 'source.png'
        image.write_bytes(b'\x89PNG\r\n\x1a\n' +
            chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(b'\0\xff\x40\x20')) + chunk(b'IEND', b''))
        ctx = Context()
        geometry = bridge.make_picture(picture.make_frame(str(image), (0,0,0), (10,0,0), (0,10,0)))
        oid, _ = ctx.model.add_object(geometry, 'Picture')
        mi = ctx.model.add_picture_material(str(image)); ctx.model.set_object_material(oid, mi)
        ctx.selection = ['Picture']
        asset = lib.capture_selection(ctx, self.store, 'Picture', 'uncategorized')
        # Only delete a self-created temporary resource, never a repository file.
        image.unlink()
        target = Context(); names = lib.insert_asset(target, asset)
        Path(asset['source_file']).unlink()  # self-created temporary store only
        geometry = target.geom_by_name(names[0])
        frame = bridge.picture_frame(geometry)
        self.assertTrue(Path(frame['image']).is_file())
        self.assertTrue(geometry.GetUserString(lib._PICTURE))
        path = self.root / 'target.narwhal'
        self.assertTrue(narwhal_document.save_model(target.model, path))
        reopened = lib._read(path)
        self.assertTrue(next(iter(reopened.file.Objects)).Geometry.GetUserString(lib._PICTURE))

    def test_cplane_orientation_and_name_collision(self):
        source, asset = self.asset()
        from narwhal3d.cplane import CPlane
        ctx = Context()
        ctx.model.add_object(bridge.make_line((0,0,0), (1,0,0)), 'Box1')
        plane = CPlane(origin=(70,80,90), xaxis=(1,0,0), yaxis=(0,0,1))
        with patch.object(ctx, 'active_cplane', return_value=plane):
            names = lib.insert_asset(ctx, asset)
        self.assertNotIn('Box1', names)
        # Plane's local Z is world -Y, so the solid extends behind its base.
        box = ctx.geom_by_name(names[0]).GetBoundingBox()
        self.assertAlmostEqual(box.Max.Y, 80, places=7)
        self.assertAlmostEqual(box.Min.Y, 80 - 25.4, places=7)
        self.assertEqual(len({o.Attributes.Name.lower() for o in ctx.model.file.Objects}), 4)

    def test_unsupported_picture_resource_refused_without_live_mutation(self):
        ctx = self.fixture(); before = signature(ctx.model)
        material = ctx.model.file.Materials[0]
        material.SetBumpTexture(str(self.root / 'not-preservable.png'))
        with self.assertRaisesRegex(lib.LibraryGeometryError, 'GetBumpTexture'):
            lib.capture_selection(ctx, self.store, 'Bad resource', 'uncategorized')
        self.assertEqual(ctx.model.file.Materials[0].GetBumpTexture().FileName,
                         str(self.root / 'not-preservable.png'))
        self.assertEqual(ctx.undo.undo_names(), [])
        self.assertEqual(self.store.count, 0)

    @unittest.skipIf(bpy is None, 'requires actual Blender display publication')
    def test_real_context_insert_and_undo_redo(self):
        _, asset = self.asset()
        ctx = commands.NarwhalContext(NarwhalModel())
        names = lib.insert_asset(ctx, asset, point=(100,20,10))
        for name in names:
            self.assertIsNotNone(bpy.data.objects.get(name))
        self.assertEqual(ctx.undo_last(), 'Insert library asset')
        self.assertEqual(ctx.model.object_count(), 0)
        self.assertEqual(len(ctx.model.file.Materials), 0)
        self.assertEqual(ctx.redo_last(), 'Insert library asset')
        for name in names:
            self.assertIsNotNone(bpy.data.objects.get(name))
        self.assertEqual(ctx.model.object_count(), 3)
        self.assertEqual(len(ctx.model.file.Materials), 1)

    @unittest.skipIf(bpy is None, 'requires real Blender rendering host')
    @unittest.skipIf(bpy is None, 'requires real Blender thumbnail renderer')
    def test_box_thumbnail_has_readable_neutral_shaded_faces(self):
        ctx = Context()
        _id, name = ctx.add_geometry(bridge.make_box((0,0,0),(10,10,0),10), 'Shaded box')
        ctx.select_by_names([name])
        asset = lib.capture_selection(ctx, self.store, 'Shaded box', 'uncategorized')
        image = bpy.data.images.load(asset['thumbnail'], check_existing=False)
        try:
            pixels = list(image.pixels)
            opaque = [pixels[i:i+3] for i in range(0,len(pixels),4) if pixels[i+3]>.95]
            self.assertGreater(len(opaque), 15000, 'box must fill the thumbnail')
            luminance = sorted(sum(rgb)/3 for rgb in opaque)
            self.assertGreater(luminance[len(luminance)//10], .12, 'shadowed faces must not become a black void')
            self.assertGreater(luminance[9*len(luminance)//10]-luminance[len(luminance)//10], .10,
                               'studio shading must distinguish box faces')
            self.assertLess(sum(max(rgb)-min(rgb) for rgb in opaque)/len(opaque), .08,
                            'solid previews use neutral shaded-view colours, not dark blue')
            self.assertEqual(pixels[3], 0)
        finally:
            bpy.data.images.remove(image)

    @unittest.skipIf(bpy is None, 'requires real Blender thumbnail renderer')
    def test_flat_curve_thumbnail_is_face_on_framed_and_high_contrast(self):
        ctx = Context()
        _id, name = ctx.add_geometry(bridge.make_circle((0, 0, 0), 5), 'Flat drawing')
        ctx.select_by_names([name])
        asset = lib.capture_selection(ctx, self.store, 'Flat drawing', 'uncategorized')
        image = bpy.data.images.load(asset['thumbnail'], check_existing=False)
        try:
            pixels = list(image.pixels)
            visible = [(i//4%256, i//4//256) for i in range(0,len(pixels),4)
                       if pixels[i+3]>.8 and max(pixels[i:i+3])<.7]
            self.assertGreater(len(visible), 600, 'curve strokes must remain legible at tile size')
            width = max(x for x,y in visible)-min(x for x,y in visible)
            height = max(y for x,y in visible)-min(y for x,y in visible)
            self.assertGreater(width, 180, 'fit drawing instead of a loose 3D diagonal')
            self.assertLess(abs(width-height), 4, 'face-on circle must not become a tilted ellipse')
            self.assertEqual(pixels[3], 0, 'thumbnail background must be transparent')
        finally:
            bpy.data.images.remove(image)

    def test_real_thumbnail_isolated_scene_selection_and_visible_pixels(self):
        ctx = self.fixture()
        scene = bpy.context.scene
        objects = set(bpy.data.objects.keys())
        scenes = set(bpy.data.scenes.keys())
        asset = lib.capture_selection(ctx, self.store, 'Thumbnail', 'uncategorized')
        self.assertIs(bpy.context.scene, scene)
        self.assertEqual(set(bpy.data.objects.keys()), objects)
        self.assertEqual(set(bpy.data.scenes.keys()), scenes)
        image = bpy.data.images.load(asset['thumbnail'], check_existing=False)
        try:
            self.assertEqual(tuple(image.size), (256, 256))
            pixels = list(image.pixels)
            colours = {tuple(round(c, 3) for c in pixels[i:i+3]) for i in range(0, len(pixels), 4)}
            self.assertGreater(len(colours), 20, 'actual isolated geometry must be visible')
        finally:
            bpy.data.images.remove(image)


if __name__ == '__main__':
    mutation = os.environ.get('LIBRARY_GEOMETRY_MUTATION')
    if mutation == 'materials':
        original = NarwhalModel._snapshot_document
        def broken_materials(self, by_id, names):
            snapshot = original(self, by_id, names)
            snapshot.materials = None
            snapshot.material_layers = None
            return snapshot
        NarwhalModel._snapshot_document = broken_materials
    elif mutation == 'transform':
        def proxy_only(geometry, transform):
            geometry.Transform(transform)
            return geometry
        lib.bridge.transform_geometry = proxy_only
    args = sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else sys.argv[1:]
    if bpy is not None and '--' not in sys.argv:
        args = []
    result = unittest.main(argv=[__file__, *args], exit=False, verbosity=2)
    if not result.result.wasSuccessful():
        raise SystemExit(1)
