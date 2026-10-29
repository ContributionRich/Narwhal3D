#!/usr/bin/env python3
"""Actual AppState -> Sweep1Cmd -> NarwhalContext, in disposable real Blender.

Host: python3 -B spike/test_sweep1_command_integration.py --launch [--mutations | --regressions]
No native build, full suite, visible UI takeover or production-source mutations.
Heavy/GUI locks, 2 GiB/no swap, one thread, 600s scope, read-only project.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / 'tools/blender-patched/5.2/python/bin/python3.13'
SOURCES = ('narwhal3d/bridge.py', 'narwhal3d/solid_commands.py', 'narwhal3d/commands.py',
           'narwhal3d/kernel/__init__.py', 'narwhal3d/surfacing_backend.py',
           'native/geometry_core/target/release/libnarwhal_geometry_core.so',
           'spike/test_sweep1_command_integration.py')


def launch():
    import fcntl
    out = Path(tempfile.mkdtemp(prefix='narwhal-sweep1-command-'))
    env = dict(os.environ)
    paths = subprocess.check_output(['bash', str(ROOT/'narwhal3d.sh')], cwd=ROOT,
        env=dict(env, NARWHAL3D_LAUNCHER_TEST='native-paths'), text=True, timeout=10)
    for row in paths.splitlines():
        key, value = row.split('=', 1)
        if value:
            env[key] = value
    for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'TMPDIR'):
        folder = out/key.lower(); folder.mkdir(); env[key] = str(folder)
    env.update(SWEEP1_COMMAND_OUT=str(out), PYTHONDONTWRITEBYTECODE='1',
        PYTHONPYCACHEPREFIX=str(out/'bytecode'), PYTHONPATH=str(ROOT/'vendor/occt'),
        LD_LIBRARY_PATH=str(ROOT/'tools/blender-patched/5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs'),
        OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', NARWHAL3D_NO_DIALOGS='1',
        NARWHAL3D_SETTINGS_FILE=str(out/'preferences.json'))
    mode = '--regressions' if '--regressions' in sys.argv else '--inside'
    if mode == '--regressions':
        child = [str(PYTHON), '-B', str(Path(__file__).resolve()), mode]
    else:
        child = [str(ROOT/'tools/blender-patched/blender'), '-b', '--factory-startup', '-t', '1',
                 '--python-exit-code', '1', '--python', str(Path(__file__).resolve()), '--', mode]
        if '--mutations' in sys.argv:
            child.append('--mutations')
    cmd = ['systemd-run', '--user', '--scope', '--quiet', '-p', 'MemoryMax=2048M',
        '-p', 'MemorySwapMax=0', '-p', 'CPUQuota=100%', 'timeout', '--signal=TERM',
        '--kill-after=5s', '600s', 'bwrap', '--ro-bind', '/', '/', '--dev-bind', '/dev', '/dev',
        '--proc', '/proc', '--bind', str(out), str(out), '--unshare-net', '--die-with-parent'] + child
    locks = []
    print('SWEEP1_COMMAND_RECEIPT', out, flush=True)
    try:
        deadline = time.monotonic()+90
        while True:
            try:
                for name in ('/tmp/narwhal3d-heavy-build.lock', '/tmp/narwhal3d_gui.lock'):
                    lock = open(name, 'a'); locks.append(lock)
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                for lock in locks: lock.close()
                locks.clear()
                if time.monotonic() >= deadline:
                    raise TimeoutError('heavy/GUI lock busy; nothing launched')
                time.sleep(.25)
        available = int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines()
                             if l.startswith('MemAvailable:')))
        assert available >= 6*1024*1024, 'Need 4 GiB desktop reserve + 2 GiB cap'
        hashes = {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in SOURCES}
        (out/'launch.json').write_text(json.dumps(dict(command=cmd, mem_available_kib=available,
            source_sha256=hashes, native_paths=paths), indent=2))
        with (out/'run.log').open('w') as log:
            run = subprocess.run(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=610)
        print((out/'run.log').read_text()[-10000:], flush=True)
        after = {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in SOURCES}
        (out/'source_check.json').write_text(json.dumps(dict(before=hashes, after=after,
                                                            unchanged=hashes == after), indent=2))
        return run.returncode or int(hashes != after or not (out/'passed.json').exists())
    finally:
        for lock in locks: lock.close()


if '--launch' in sys.argv:
    raise SystemExit(launch())

if '--regressions' in sys.argv:
    rows = []
    for suite, args in (
        ('test_surfacing_preparation.py', []), ('test_surfacing_freeform.py', []),
        ('test_surfacing_parallel.py', []), ('test_surfacing_integration.py', ['--require-native']),
        ('test_surfacing_step_roundtrip.py', []), ('test_solid_commands.py', []),
        ('test_sweep.py', []), ('test_sweep_loft_occt.py', []), ('test_extrude_options.py', []),
        ('test_undo.py', [])):
        start = time.monotonic()
        # Match run_tests.sh: Sweep includes Blender display gates and is not
        # scheduled under the plain interpreter in the production harness.
        argv = ([str(ROOT/'tools/blender-patched/blender'), '-b', '--factory-startup', '-t', '1',
                 '--python-exit-code', '1', '--python', str(ROOT/'spike'/suite)]
                if suite == 'test_sweep.py' else [str(PYTHON), '-B', str(ROOT/'spike'/suite), *args])
        run = subprocess.run(argv, timeout=180)
        rows.append(dict(suite=suite, exit=run.returncode, seconds=time.monotonic()-start))
        (Path(os.environ['SWEEP1_COMMAND_OUT'])/'regressions.json').write_text(json.dumps(rows, indent=2))
        if run.returncode:
            raise SystemExit(run.returncode)
    (Path(os.environ['SWEEP1_COMMAND_OUT'])/'passed.json').write_text(json.dumps(rows, indent=2))
    raise SystemExit(0)

sys.path.insert(0, str(ROOT/'spike'))
from test_surfacing_preparation import sources, prepare, assert_physical, assert_sections
from test_surfacing_freeform import bridge, kernel, ob, sb, ng, r3, surface, assert_interior_equal, fixture
from narwhal3d import state, commands, solid_commands, selection_state
from narwhal3d.model import NarwhalModel
from unittest.mock import patch
import unittest


def verify_document(model):
    objects = list(model.file.Objects)
    geom = next(o.Geometry for o in objects if o.Attributes.Name == 'Sweep1')
    recipe = kernel.recipe_of(geom)
    provenance = recipe['source_provenance']
    ids = tuple(provenance['ids'])
    def resolve():
        return ids, [model.file.Objects.FindId(oid).Geometry for oid in ids]
    curves = resolve()[1]
    prepared = sb.prepare_freeform(curves[0], curves[1:], source_ids=ids,
        reverse=recipe['reverse'], tolerance=recipe['tolerance'])
    assert list(prepared.source_hashes) == provenance['geometry_sha256']
    assert list(map(list, prepared.station_points)) == provenance['station_points']
    expected = sb.build_prepared_freeform(prepared, resolve_sources=resolve)
    assert geom.IsValid and len(geom.Faces) == 1 and not geom.IsSolid
    assert geom.GetUserString(kernel.DOCUMENT_FORM_KEY) == 'exact'
    assert kernel.honesty_problem(geom) is None
    surface(kernel.master_of(geom))
    for actual in (kernel.master_of(geom), ob.from_recipe(recipe),
                   ob.shape_from_brep_data(bridge.brep_data(geom))):
        assert_interior_equal(actual, expected.shape, ob)
    assert_sections(expected, prepared, curves)
    return geom


if '--read' in sys.argv:
    path = sys.argv[-1]
    if path.endswith('.narwhal'):
        from narwhal3d import narwhal_document as nd
        model = nd.load_model(path); nd.commit_masters(model)
    else:
        model = NarwhalModel.load(path)
    verify_document(model)
    print('PASS fresh actual-command document', Path(path).name)
    raise SystemExit(0)

import bpy


class CommandIntegrationTests(unittest.TestCase):
    def setup_case(self, kind='arc', **kw):
        bridge.remove_display_objects(list(bpy.data.objects))
        for mesh in list(bpy.data.meshes):
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        self.case = sources(kind, **kw)
        state.app_state.set_model(self.case[0])
        self.state = state.app_state
        self.state.doc_path = None
        self.ctx = self.state.ctx
        self.ctx.rebuild_display()
        self.state.history.clear()
        self.ctx.undo.clear()
        self.display_sentinel()
        self.unchanged_displays = self.display_fingerprint()
        self.before = [c.Encode() for c in self.case[2]()[1]]
        return self.case

    def enter_sources(self, names=('source1', 'source2', 'source3')):
        self.unchanged_displays = self.display_fingerprint()
        self.state.feed_line('Sweep1')
        self.cmd = self.state.active_command
        self.assertIsInstance(self.cmd, solid_commands.Sweep1Cmd)
        self.state.feed_line('source0')
        for name in names:
            self.state.feed_line(name)

    def finish(self):
        self.state.feed_line('')
        return '\n'.join(self.state.history[-3:])

    def assert_no_result(self, registry=None, sources_unchanged=True, history_names=()):
        self.assertEqual(self.ctx.model.object_count(), 4)
        self.assertFalse(any(o.name.startswith('Sweep') for o in bpy.data.objects))
        self.assertFalse(any(m.name == 'Sweep1' for m in bpy.data.meshes))
        self.assertEqual(self.ctx.undo.undo_names(), list(history_names))
        self.assertNotIn('Sweep1', self.ctx.undo.undo_names())
        self.assertFalse(self.ctx.undo.recording)
        self.assertEqual(self.display_fingerprint(), self.unchanged_displays)
        if sources_unchanged:
            self.assertEqual([c.Encode() for c in self.case[2]()[1]], self.before)
        if registry is not None:
            self.assertEqual(kernel.master_registry_snapshot(), registry)

    def document_fingerprint(self):
        model = self.ctx.model
        return (tuple(sorted((str(o.Attributes.Id), json.dumps(o.Geometry.Encode(), sort_keys=True),
                              json.dumps(o.Attributes.Encode(), sort_keys=True)) for o in model.file.Objects)),
                tuple(model.file.Strings), model.unit_system(), model.absolute_tolerance())

    def saved_history_fixture(self):
        # Real reversible document edits, not Record('x') placeholders.
        for value in ('saved', 'redo candidate'):
            self.ctx.begin_undo('Prior '+value)
            self.ctx.note_change()
            self.ctx.model.file.Strings['rollback-fixture'] = value
            self.ctx.end_undo()
        self.assertEqual(self.ctx.undo_last(), 'Prior redo candidate')
        self.assertEqual(self.ctx.model.file.Strings['rollback-fixture'], 'saved')
        self.state.mark_saved(str(Path(os.environ['SWEEP1_COMMAND_OUT'])/'marked-saved.narwhal'))
        self.assertFalse(self.state.doc_modified())
        self.assertTrue(self.ctx.undo.can_redo)
        return self.document_fingerprint(), self.ctx.undo.top_serial

    def display_fingerprint(self):
        return ({o.name: o.as_pointer() for o in bpy.data.objects},
                {m.name: m.as_pointer() for m in bpy.data.meshes})

    def display_sentinel(self):
        mesh = bpy.data.meshes.new('unrelated sentinel mesh')
        obj = bpy.data.objects.new('unrelated sentinel', mesh)
        bpy.context.scene.collection.objects.link(obj)
        return obj, mesh

    def test_external_edit_preserves_saved_dirty_and_real_history(self):
        for phase in ('acceptance', 'build', 'display'):
            with self.subTest(phase=phase):
                self.setup_case()
                saved, serial = self.saved_history_fixture()
                self.display_sentinel()
                displays = self.display_fingerprint()
                self.enter_sources()
                registry = kernel.master_registry_snapshot()
                external_curve = self.case[2]()[1][2].Duplicate(); external_curve.Reverse()
                expected_sources = list(self.before)
                expected_sources[2] = external_curve.Encode()
                target, name = ((self.cmd, '_clear_selection') if phase == 'acceptance' else
                                (sb, 'sweep1_freeform_approx') if phase == 'build' else
                                (self.ctx, '_make_display_object'))
                real = getattr(target, name)
                def external_edit(*args, **kw):
                    result = real(*args, **kw)
                    self.ctx.model.replace_geometry(self.case[1][2], external_curve)
                    return result
                with patch.object(target, name, external_edit):
                    self.assertIn('stale', self.finish())
                changed = self.document_fingerprint()
                self.assertNotEqual(changed, saved)
                self.assertEqual([c.Encode() for c in self.case[2]()[1]], expected_sources)
                self.assertEqual(self.ctx.model.object_count(), 4)
                self.assertEqual(kernel.master_registry_snapshot(), registry)
                self.assertEqual(self.display_fingerprint(), displays)
                self.assertNotIn('Sweep1', self.ctx.undo.undo_names())
                self.assertTrue(self.state.doc_modified(), 'surviving edit must not appear saved')
                self.assertNotEqual(self.ctx.undo.top_serial, serial)
                self.assertFalse(self.ctx.undo.can_redo, 'old redo would overwrite the surviving edit')
                label = self.ctx.undo.undo_name
                self.assertIn('External change', label)
                self.assertEqual(self.ctx.undo_last(), label)
                self.assertEqual(self.document_fingerprint(), saved)
                self.assertFalse(self.state.doc_modified())
                self.assertEqual(self.ctx.undo.redo_names(), [label])
                self.assertEqual(self.ctx.redo_last(), label)
                self.assertEqual(self.document_fingerprint(), changed)
                self.assertEqual([c.Encode() for c in self.case[2]()[1]], expected_sources)
                self.assertTrue(self.state.doc_modified())
                self.assertEqual(self.ctx.model.object_count(), 4)

    def test_same_name_replacement_display_failure_before_return(self):
        self.setup_case(); self.saved_history_fixture(); self.enter_sources()
        old_model, old_stack = self.ctx.model, self.ctx.undo
        history = (list(old_stack._undo), list(old_stack._redo))
        registry = kernel.master_registry_snapshot()
        replacement = NarwhalModel()
        replacement.add_object(r3.Point(r3.Point3d(9, 8, 7)), 'Sweep1')
        replacement.add_object(r3.Point(r3.Point3d(6, 5, 4)), 'replacement sentinel')
        real = self.ctx._make_display_object
        displays = []
        def switch_then_raise(*args, **kw):
            real(*args, **kw)
            bridge.remove_display_objects(list(bpy.data.objects))
            state.app_state.set_model(replacement)
            state.app_state.ctx.rebuild_display()
            displays.append(self.display_fingerprint())
            raise RuntimeError('replacement display rebuilt before original function returned')
        with patch.object(self.ctx, '_make_display_object', switch_then_raise):
            self.assertIn('failed', self.finish())
        self.assertIs(self.state.model, replacement)
        self.assertEqual(replacement.object_count(), 2)
        self.assertEqual(old_model.object_count(), 4)
        self.assertEqual(self.display_fingerprint(), displays[0])
        self.assertEqual((old_stack._undo, old_stack._redo), history)
        self.assertEqual(kernel.master_registry_snapshot(), registry)

    def test_owned_autonamed_mesh_failure_preserves_existing_orphan(self):
        self.setup_case(); self.saved_history_fixture(); self.display_sentinel()
        orphan = bpy.data.meshes.new('Sweep1')
        original_pointer = orphan.as_pointer()
        displays = self.display_fingerprint()
        history = (list(self.ctx.undo._undo), list(self.ctx.undo._redo))
        self.enter_sources()
        registry = kernel.master_registry_snapshot()
        real = bridge.geometry_to_bmesh
        allocated = []
        class FailingBMesh:
            def __init__(self, bm): self.bm = bm
            @property
            def faces(self): return self.bm.faces
            def free(self): self.bm.free()
            def to_mesh(self, mesh):
                allocated.append(mesh.name)
                raise RuntimeError('mesh conversion failed after allocation, before object')
        with patch.object(bridge, 'geometry_to_bmesh', lambda *a, **kw: FailingBMesh(real(*a, **kw))):
            self.assertIn('failed', self.finish())
        self.assertEqual(allocated, ['Sweep1.001'])
        self.assertEqual(bpy.data.meshes.get('Sweep1').as_pointer(), original_pointer)
        self.assertEqual(self.display_fingerprint(), displays)
        self.assertEqual((self.ctx.undo._undo, self.ctx.undo._redo), history)
        self.assertFalse(self.state.doc_modified())
        self.assertEqual(self.ctx.model.object_count(), 4)
        self.assertEqual(kernel.master_registry_snapshot(), registry)

    def test_callback_assigned_unrelated_mesh_is_not_owned(self):
        self.setup_case(); self.saved_history_fixture()
        unrelated = bpy.data.meshes.new('external orphan')
        displays = self.display_fingerprint()
        registry = kernel.master_registry_snapshot()
        history = (list(self.ctx.undo._undo), list(self.ctx.undo._redo))
        self.enter_sources()
        real = self.ctx._make_display_object
        def replace_data_then_fail(*args, **kw):
            obj = real(*args, **kw)
            obj.data = unrelated
            raise RuntimeError('unrelated data assigned before failed display return')
        with patch.object(self.ctx, '_make_display_object', replace_data_then_fail):
            self.assertIn('failed', self.finish())
        self.assertEqual(self.display_fingerprint(), displays)
        self.assertEqual((self.ctx.undo._undo, self.ctx.undo._redo), history)
        self.assertFalse(self.state.doc_modified())
        self.assertEqual(self.ctx.model.object_count(), 4)
        self.assertEqual(kernel.master_registry_snapshot(), registry)

    def test_actual_arc_command_interior_undo_redo_fresh_documents(self):
        self.setup_case(); self.enter_sources()
        registry = kernel.master_registry_snapshot()
        with patch.object(self.ctx, 'add_geometry', wraps=self.ctx.add_geometry) as add:
            self.assertIn('Created Sweep1', self.finish())
            self.assertEqual(add.call_count, 1)
        self.assertEqual(self.ctx.model.object_count(), 5)
        self.assertEqual(self.ctx.undo.undo_names(), ['Sweep1'])
        self.assertEqual(len(kernel.master_registry_snapshot()), len(registry)+1)
        self.assertEqual([c.Encode() for c in self.case[2]()[1]], self.before)
        geom = verify_document(self.ctx.model)
        prepared = prepare(self.case)
        assert_physical(prepared, self.case[4])
        self.assertEqual(tuple(kernel.recipe_of(geom)['source_provenance']['ids']), self.case[1])
        self.assertEqual(self.ctx.undo_last(), 'Sweep1')
        self.assertEqual(self.ctx.model.object_count(), 4)
        self.assertIsNone(bpy.data.objects.get('Sweep1'))
        self.assertEqual(self.ctx.redo_last(), 'Sweep1')
        verify_document(self.ctx.model)
        self.assertIsNotNone(bpy.data.objects.get('Sweep1'))
        from narwhal3d import narwhal_document as nd
        out = Path(os.environ['SWEEP1_COMMAND_OUT'])
        for suffix in ('.narwhal', '.3dm'):
            path = out/('command'+suffix)
            self.assertTrue(nd.save_model(self.ctx.model, str(path)) if suffix == '.narwhal'
                            else self.ctx.model.save(str(path)))
            run = subprocess.run([str(PYTHON), '-B', __file__, '--read', str(path)],
                                 text=True, capture_output=True, timeout=120)
            self.assertEqual(run.returncode, 0, run.stderr[-3500:])
            print(run.stdout[-1000:])

    def test_nonplanar_and_reversed_rail(self):
        for kind, reverse in [('nonplanar', False), ('arc', True)]:
            self.setup_case(kind, reverse_rail=reverse); self.enter_sources()
            self.assertIn('Created Sweep1', self.finish())
            verify_document(self.ctx.model)
        self.setup_case(reverse_middle=True); self.enter_sources()
        self.assertIn('physical station is not on the rail', self.finish())
        self.assert_no_result()

    def test_order_is_not_pick_chronology(self):
        self.setup_case(); self.state.feed_line('Sweep1'); self.state.feed_line('source0')
        picked = ['source3', 'source1', 'source2']
        for name in picked:
            self.ctx.object_by_name(name).select_set(True)
        actual = [o.name for o in self.ctx.selected_objects()]
        self.assertNotEqual(actual, picked)
        self.assertIn('order is ambiguous', self.finish())
        self.assertIsNotNone(self.state.active_command)
        print('MEASURED selected-object order', actual, 'not click order', picked)
        self.ctx.select_by_names([])
        for name in ['source3', 'source2', 'source1']:
            self.state.feed_line(name)
        self.assertIn('endpoints in order', self.finish())
        self.assert_no_result()

    def test_unsupported_and_precision(self):
        for mode in ('Roadlike', 'five', 'tight', 'components', 'ambiguous', 'history-disabled'):
            with self.subTest(mode=mode):
                self.setup_case()
                if mode == 'history-disabled':
                    self.ctx.undo.enabled = False
                if mode == 'tight':
                    self.ctx.model.file.Settings.ModelAbsoluteTolerance = 1e-10
                if mode == 'ambiguous':
                    self.ctx.model.add_object(self.case[2]()[1][1], 'SOURCE1', 0)
                self.enter_sources()
                if mode == 'Roadlike': self.state.feed_line('Roadlike')
                if mode == 'five':
                    self.state.feed_line('extra4'); self.state.feed_line('extra5')
                if mode == 'components':
                    self.cmd._pending_multi[1] = ('source2', 'edge', 0)
                message = self.finish()
                self.assertNotIn('Created', message)
                self.assertEqual(self.ctx.model.object_count(), 5 if mode == 'ambiguous' else 4)
                self.assertEqual(self.ctx.undo.undo_names(), [])
        self.setup_case()
        self.ctx.model.file.Settings.ModelAbsoluteTolerance = 2e-9
        self.enter_sources()
        with patch.object(bridge, 'sweep1_freeform_from_sources', side_effect=kernel.KernelError('tolerance probe')) as build:
            self.finish()
        self.assertEqual(build.call_args.kwargs['tolerance'], 2e-9)

    def test_identity_capture_and_same_id_content(self):
        for change in ('same-id', 'new-id', 'rename'):
            self.setup_case(); self.enter_sources()
            model, ids, resolve = self.case[:3]
            if change == 'same-id':
                replacement = resolve()[1][2].Duplicate(); replacement.Reverse()
                model.replace_geometry(ids[2], replacement)
            elif change == 'new-id':
                replacement = resolve()[1][2].Duplicate()
                model.remove_object(ids[2]); model.add_object(replacement, 'source2')
            else:
                model.file.Objects.FindId(ids[2]).Attributes.Name = 'renamed'
            message = self.finish()
            if change == 'rename':
                self.assertIn('Created', message)  # UUID, NOT a fresh name search.
            else:
                self.assertIn('stale Freeform', message)
                self.assertEqual(model.object_count(), 4)
                self.assertEqual(self.ctx.undo.undo_names(), [])

    def test_live_guards_after_exact_conversion(self):
        for change in ('cancel', 'units', 'tolerance', 'path', 'file', 'context', 'command', 'source'):
            with self.subTest(change=change):
                self.setup_case(); self.enter_sources()
                registry = kernel.master_registry_snapshot()
                writer = bridge._onbrep(); real = writer.exact_brep
                def changed(*args):
                    geom = real(*args)
                    if change == 'cancel': self.state.cancel()
                    elif change == 'units': self.ctx.model.file.Settings.ModelUnitSystem = r3.UnitSystem.Inches
                    elif change == 'tolerance': self.ctx.model.file.Settings.ModelAbsoluteTolerance = .02
                    elif change == 'path': self.state.doc_path = '/external.narwhal'
                    elif change == 'file': self.ctx.model.file = r3.File3dm()
                    elif change == 'context': self.state.set_model(NarwhalModel())
                    elif change == 'command': self.state.active_command = solid_commands.Sweep1Cmd()
                    else:
                        curve = self.case[2]()[1][2].Duplicate(); curve.Reverse()
                        self.ctx.model.replace_geometry(self.case[1][2], curve)
                    return geom
                with patch.object(writer, 'exact_brep', changed):
                    self.assertNotIn('Created', self.finish())
                self.assertEqual(kernel.master_registry_snapshot(), registry)
                self.assertNotIn('Sweep1', self.ctx.undo.undo_names())
                if change in ('source', 'units', 'tolerance'):
                    self.assertEqual(self.ctx.undo.undo_names(), ['External change during Sweep1'])
                    self.assertTrue(self.state.doc_modified())
                else:
                    self.assertEqual(self.ctx.undo.undo_names(), [])
                self.assertFalse(any(o.name.startswith('Sweep') for o in bpy.data.objects))
                if change == 'command':
                    self.assertIsInstance(self.state.active_command, solid_commands.Sweep1Cmd)
                    self.assertIsNot(self.state.active_command, self.cmd)

    def test_source_change_during_native_build(self):
        self.setup_case(); self.enter_sources()
        registry = kernel.master_registry_snapshot()
        real = sb.sweep1_freeform_approx
        def changed(*args, **kw):
            result = real(*args, **kw)
            curve = self.case[2]()[1][2].Duplicate(); curve.Reverse()
            self.ctx.model.replace_geometry(self.case[1][2], curve)
            return result
        with patch.object(sb, 'sweep1_freeform_approx', changed):
            self.assertIn('stale', self.finish())
        self.assert_no_result(registry, sources_unchanged=False,
                              history_names=['External change during Sweep1'])
        self.assertTrue(self.state.doc_modified())
        self.assertNotEqual(self.case[2]()[1][2].Encode(), self.before[2])

    def test_bridge_last_source_check_and_explicit_reversal(self):
        case = sources(reverse_rail=True, reverse_middle=True)
        curves = case[2]()[1]
        geom = bridge.sweep1_freeform_from_sources(curves[0], curves[1:], source_ids=case[1],
            resolve_sources=case[2], reverse=case[5])
        expected = sb.build_prepared_freeform(prepare(case), resolve_sources=case[2])
        assert_interior_equal(kernel.master_of(geom), expected.shape, ob)
        registry = kernel.master_registry_snapshot()
        writer = bridge._onbrep(); real = writer.exact_brep
        def changed(*args):
            result = real(*args)
            curve = case[2]()[1][2].Duplicate(); curve.Reverse()
            case[0].replace_geometry(case[1][2], curve)
            return result
        with patch.object(writer, 'exact_brep', changed):
            with self.assertRaisesRegex(kernel.KernelError, 'source geometry changed'):
                bridge.sweep1_freeform_from_sources(curves[0], curves[1:], source_ids=case[1],
                    resolve_sources=case[2], reverse=case[5])
        self.assertEqual(kernel.master_registry_snapshot(), registry)

    def test_missing_capability_and_writer_failure(self):
        for target, name, value in ((ng, 'prepare_freeform_available', False),
                                    (ng, 'sweep1_freeform_available', False),
                                    (bridge._onbrep(), 'exact_brep', None),
                                    (kernel, 'record_recipe', False)):
            self.setup_case(); self.enter_sources(); registry = kernel.master_registry_snapshot()
            with patch.object(target, name, return_value=value):
                self.assertIn('failed', self.finish())
            self.assert_no_result(registry)

    def test_registration_and_partial_publication_rollback(self):
        for phase in ('register', 'insert', 'mesh', 'display', 'display-none', 'bookkeeping', 'postcheck'):
            with self.subTest(phase=phase):
                self.setup_case()
                saved, serial = self.saved_history_fixture()
                self.enter_sources()
                stack = self.ctx.undo
                old_records = (list(stack._undo), list(stack._redo))
                registry = kernel.master_registry_snapshot()
                created = selection_state.last_created(self.ctx.model)
                counters = dict(self.ctx.counters)
                if phase == 'register': target, name = kernel, 'register_master'
                elif phase == 'insert': target, name = self.ctx.model, 'add_object'
                elif phase == 'mesh': target, name = bridge, 'geometry_to_bmesh'
                elif phase == 'display': target, name = bridge, 'bmesh_to_object'
                elif phase == 'display-none': target, name = self.ctx, '_make_display_object'
                elif phase == 'bookkeeping': target, name = selection_state, 'record_created'
                else: target, name = self.ctx.model, 'register_blender_object'
                real = getattr(target, name)
                def fail(*args, **kw):
                    if phase == 'mesh':
                        bm = real(*args, **kw)
                        class FailingBMesh:
                            faces = bm.faces
                            def to_mesh(self, mesh): raise RuntimeError('injected mesh')
                            def free(self): bm.free()
                        return FailingBMesh()
                    real(*args, **kw)
                    if phase == 'display-none':
                        return None
                    if phase == 'postcheck':
                        self.state.cancel()
                        return
                    raise RuntimeError('injected '+phase)
                with patch.object(target, name, fail):
                    self.assertIn('failed', self.finish())
                self.assertEqual((stack._undo, stack._redo), old_records)
                self.assertEqual(selection_state.last_created(self.ctx.model), created)
                self.assert_no_result(registry, history_names=['Prior saved'])
                self.assertEqual(self.ctx.counters, counters)
                self.assertEqual(self.document_fingerprint(), saved)
                self.assertEqual(stack.top_serial, serial)
                self.assertFalse(self.state.doc_modified())
                self.assertEqual(self.ctx.redo_last(), 'Prior redo candidate')
                self.assertEqual(self.ctx.model.file.Strings['rollback-fixture'], 'redo candidate')
                self.assertEqual(self.ctx.undo_last(), 'Prior redo candidate')
                self.assertEqual(self.document_fingerprint(), saved)
                self.assertFalse(self.state.doc_modified())

    def test_external_replacement_is_not_rolled_back(self):
        self.setup_case(); self.enter_sources()
        old_model = self.ctx.model
        new_model = NarwhalModel()
        external = bridge.make_box((0, 0, 0), (2, 3, 0), 4)
        external_shape = ob.make_box(2, 3, 4)
        real = self.ctx.model.register_blender_object
        def replace(*args):
            real(*args)
            bridge.remove_display_objects(list(bpy.data.objects))
            new_model.add_object(external, 'external')
            state.app_state.set_model(new_model)
            kernel.replace_document_masters([(external, external_shape)])
            state.app_state.ctx.rebuild_display()
        with patch.object(old_model, 'register_blender_object', replace):
            self.assertNotIn('Created', self.finish())
        self.assertIs(self.state.model, new_model)
        self.assertEqual(new_model.object_count(), 1)
        self.assertIsNotNone(bpy.data.objects.get('external'))
        self.assertEqual(old_model.object_count(), 4)
        self.assertIs(kernel.master_of(external), external_shape)
        self.assertEqual(len(kernel.master_registry_snapshot()), 1)
        self.assertEqual(self.ctx.undo.undo_names(), [])

    def test_before_insert_file_switch_preserves_replacement(self):
        self.setup_case(); self.enter_sources()
        model = self.ctx.model
        original = model.file
        replacement = NarwhalModel().file
        replacement.Objects.AddPoint(r3.Point3d(1, 2, 3))
        registry = kernel.master_registry_snapshot()
        def switch():
            model.file = replacement
        model.before_change = switch
        self.assertIn('failed', self.finish())
        self.assertIs(model.file, replacement)
        self.assertEqual(len(original.Objects), 4)
        self.assertEqual(len(replacement.Objects), 1)
        self.assertIs(model.before_change, switch)
        self.assertEqual(self.ctx.undo.undo_names(), [])
        self.assertEqual(kernel.master_registry_snapshot(), registry)
        self.assertIsNone(bpy.data.objects.get('Sweep1'))

    def test_scoped_registry_compare_remove(self):
        a, b = object(), object()
        key = kernel.register_master(r3.Point(r3.Point3d(0, 0, 0)), a)
        self.assertFalse(kernel.discard_master_binding(key, b))
        self.assertIs(kernel.master_registry_snapshot()[key], a)
        self.assertTrue(kernel.discard_master_binding(key, a))
        self.assertNotIn(key, kernel.master_registry_snapshot())

    def test_command_derived_step(self):
        self.setup_case('nonplanar')
        rail, sections, _ = fixture()
        for oid, recipe in zip(self.case[1], [rail]+sections):
            self.ctx.model.replace_geometry(oid, bridge.curve_from_recipe(recipe))
        self.enter_sources(); self.assertIn('Created Sweep1', self.finish())
        geom = verify_document(self.ctx.model)
        from test_surfacing_step_roundtrip import derived_prism, verify_roundtrip
        prism = derived_prism(kernel.master_of(geom))
        reports = []
        out = Path(os.environ['SWEEP1_COMMAND_OUT'])
        for unit in ('MM', 'INCH'):
            path = out/f'command-prism-{unit}.step'
            self.assertGreater(ob.export_step(prism, str(path), unit=unit), 0)
            imported = ob.import_step(str(path), unit='MM')
            self.assertEqual(len(imported['solids']), 1)
            self.assertFalse(any(imported[k] for k in ('shells', 'faces', 'edges')))
            reports.append(verify_roundtrip(prism, imported['solids'][0], imported['file_units'], unit))
        (out/'step.json').write_text(json.dumps(reports, indent=2))
        surface(kernel.master_of(geom))


def run_tests():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CommandIntegrationTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError('Sweep1 command integration failed')
    return result.testsRun


def mutations():
    """In-memory source corruptions only; every guard is seen RED, then restored."""
    import inspect
    import io
    import textwrap

    rows = []
    def changed_function(fn, old, new):
        source = textwrap.dedent(inspect.getsource(fn))
        assert old in source, 'mutation anchor missing'
        namespace = {}
        exec(compile(source.replace(old, new), '<Sweep1 mutation>', 'exec'), fn.__globals__, namespace)
        return namespace[fn.__name__]

    cls = solid_commands.Sweep1Cmd
    cases = [
        ('late-content', cls, '_resolve_accepted_sources',
         'if bridge._freeform_source_hash(curve) != fingerprint:', 'if False:',
         'test_identity_capture_and_same_id_content'),
        ('ambiguous-owner', cls, '_capture_source', 'if len(owners) != 1:', 'if not owners:',
         'test_unsupported_and_precision'),
        ('live-command', cls, '_resolve_accepted_sources', 'if (app_state is not state',
         'if False and (app_state is not state', 'test_live_guards_after_exact_conversion'),
        ('last-bridge-source', bridge, 'sweep1_freeform_from_sources',
         'surfacing_backend.validate_prepared_sources(prepared, *resolve_sources())', 'pass',
         'test_bridge_last_source_check_and_explicit_reversal'),
        ('document-rollback', commands.NarwhalContext, '_guarded_geometry_add',
         'file.Objects.Delete(oid)', 'pass', 'test_registration_and_partial_publication_rollback'),
        ('before-insert-identity', commands.NarwhalContext, '_guarded_geometry_add',
         'model.before_change = before_insert', 'pass',
         'test_before_insert_file_switch_preserves_replacement'),
        ('display-rollback', commands.NarwhalContext, '_remove_publication_allocations',
         'bpy.data.batch_remove(objects)', 'pass',
         'test_registration_and_partial_publication_rollback'),
        ('source-undo', commands.NarwhalContext, '_settle_failed_publication',
         'record.before = baseline', 'record.before = previous_before',
         'test_external_edit_preserves_saved_dirty_and_real_history'),
        ('external-history-label', commands.NarwhalContext, '_settle_failed_publication',
         'record.name = "External change during Sweep1"', 'pass',
         'test_external_edit_preserves_saved_dirty_and_real_history'),
        ('mesh-allocation-ownership', bridge, 'bmesh_to_object',
         'allocations["meshes"].append(me)', 'pass',
         'test_owned_autonamed_mesh_failure_preserves_existing_orphan'),
        ('object-allocation-ownership', bridge, 'bmesh_to_object',
         'allocations["objects"].append(ob)', 'pass',
         'test_registration_and_partial_publication_rollback'),
        ('name-inferred-ownership', commands.NarwhalContext, '_remove_publication_allocations',
         'objects = []', 'objects = list(filter(None, [bpy.data.objects.get("Sweep1")]))',
         'test_same_name_replacement_display_failure_before_return'),
        ('unrelated-assigned-mesh', commands.NarwhalContext, '_remove_publication_allocations',
         'bpy.data.batch_remove(objects)', 'bridge.remove_display_objects(objects)',
         'test_callback_assigned_unrelated_mesh_is_not_owned'),
        ('registry-cleanup', kernel, 'discard_master_binding', 'del _MASTERS[key]', 'pass',
         'test_scoped_registry_compare_remove'),
    ]
    for label, target, name, old, new, test in cases:
        fn = changed_function(getattr(target, name), old, new)
        stream = io.StringIO()
        with patch.object(target, name, fn):
            result = unittest.TextTestRunner(stream=stream).run(CommandIntegrationTests(test))
        assert not result.wasSuccessful(), f'mutation {label} stayed GREEN'
        # No unrelated import/runtime error may masquerade as the intended RED.
        assert result.failures and not result.errors, stream.getvalue()[-4000:]
        rows.append(dict(mutation=label, red=True, failures=len(result.failures)))
        print('MUTATION RED', label, flush=True)
    # Dispatch and publication corruption exercise the actual acceptance oracle too.
    with patch.object(cls, 'execute', solid_commands._SweepCmd.execute):
        result = unittest.TextTestRunner(stream=io.StringIO()).run(
            CommandIntegrationTests('test_actual_arc_command_interior_undo_redo_fresh_documents'))
    assert result.failures and not result.errors
    rows.append(dict(mutation='multi-dispatch', red=True))
    writer = bridge._onbrep(); real = writer.exact_brep
    def corrupt(*args):
        geom = real(*args)
        geom.Transform(r3.Transform.Translation(.01, 0, 0))
        return geom
    with patch.object(writer, 'exact_brep', corrupt):
        result = unittest.TextTestRunner(stream=io.StringIO()).run(
            CommandIntegrationTests('test_actual_arc_command_interior_undo_redo_fresh_documents'))
    assert result.failures and not result.errors
    rows.append(dict(mutation='exact-publication-coordinates', red=True))
    print('MUTATION RED multi-dispatch / exact-publication-coordinates', flush=True)
    (Path(os.environ['SWEEP1_COMMAND_OUT'])/'mutations.json').write_text(json.dumps(rows, indent=2))
    return rows


if __name__ == '__main__':
    count = run_tests()
    rows = mutations() if '--mutations' in sys.argv else []
    if rows:
        count = run_tests()
        print('RESTORED GREEN', count, flush=True)
    library = ng._load_locked().library
    abi = int(library.narwhal_geometry_core_abi_version())
    capabilities = int(library.narwhal_geometry_core_capabilities())
    assert abi == 4 and capabilities & 384 == 384
    (Path(os.environ['SWEEP1_COMMAND_OUT'])/'passed.json').write_text(json.dumps(
        dict(tests=count, mutations=rows, native_abi=abi, native_capabilities=capabilities), indent=2))
