#!/usr/bin/env python3
"""MAIN-RUST-SESSION-01: real model/Blender metadata integration, not ABI mocks.

Python justification: orchestrates rhino3dm/Blender, native lifetimes and file APIs.
No product edits, builds, full suite, VM, or geometry-kernel substitutions.

Run with the existing application-core selected, using host Python:
  NARWHAL_APPLICATION_CORE=/path/libnarwhal_application_core.so \
    python3 -B spike/test_document_session_integration.py --launch --mutations

The launcher runs model-only and actual registered-command phases sequentially.
Only Blender holds /tmp/narwhal3d_gui.lock (no native-builder lock). Each process
and its descendants have an external 2 GiB cgroup cap and zero additional swap;
launch requires another 4 GiB available. Sources are read-only in bubblewrap.
Reports remain in a unique /tmp directory; document fixtures are disposable.
O(1) assertions concern metadata handle size/call counts, NOT whole-model snapshots.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from contextlib import ExitStack
from types import ModuleType
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
EVIDENCE = {}
# Loaded only inside bounded workers; host Python need not have bpy/rhino3dm.
Model: Any = None
metadata: Any = None
native: Any = None
r3: Any = None
app: Any = None
commands: Any = None
filedialog: Any = None
file_commands: Any = None


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_path(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'cannot load test subject {path}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def initialize(mode: str) -> None:
    global Model, metadata, native, r3, app, commands, filedialog, file_commands
    sys.path.insert(0, str(ROOT))
    import rhino3dm
    r3 = rhino3dm
    if mode == 'model':
        # The real bpy-free module, without importing the UI package initializer.
        module = load_path('session_integration_model', ROOT / 'narwhal3d/model.py')
    else:
        import bpy
        from narwhal3d import model as module
        from narwhal3d import commands as cmd, filedialog as dialog, file_commands as files
        from narwhal3d.state import app_state
        commands, filedialog, file_commands, app = cmd, dialog, files, app_state
        EVIDENCE['blender_version'] = bpy.app.version_string
    Model = module.NarwhalModel
    metadata = module._metadata_api()
    native = metadata.native
    library = native.load_library()
    if library is None:
        raise RuntimeError('native integration requires an explicit, working application core')
    EVIDENCE['application_core'] = str(Path(os.environ['NARWHAL_APPLICATION_CORE']).resolve())


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='session-fixtures-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.m = Model()
        self.assertEqual(self.m.metadata_backend, 'rust')

    def session(self, model=None):
        return (model or self.m)._document_metadata.session

    def test_authority_real_file_identity_and_both_serialization_boundaries(self):
        file = self.m.file
        raw = metadata._raw(file)
        self.assertIs(type(file), r3.File3dm)
        self.assertIs(self.m.file, file)
        self.m.file.Strings['Project'] = 'native-only'
        self.assertEqual(list(raw), [], 'native projection must stay stale before serialization')
        self.assertEqual(self.session().get('Project')['value'], 'native-only')
        self.assertEqual(self.m.document_user_text('Project'), 'native-only')
        encoded = file.Encode()
        self.assertEqual(list(raw), [('Project', 'native-only')])
        self.assertEqual(list(r3.File3dm.Decode(encoded).Strings), [('Project', 'native-only')])
        file.Strings['Project'] = 'second-state'
        self.assertEqual(raw['Project'], 'native-only')
        path = self.root / 'boundary.3dm'
        self.assertTrue(file.Write(str(path), 8))
        self.assertEqual(r3.File3dm.Read(str(path)).Strings['Project'], 'second-state')
        self.assertEqual(raw['Project'], 'second-state')
        self.assertIs(self.m.file, file)
        self.assertEqual(self.m.metadata_revision()['session'], self.session().handle)

    def test_direct_strings_match_unowned_native_casing_nul_and_indexes(self):
        unowned = r3.File3dm()
        table, oracle = self.m.file.Strings, unowned.Strings
        operations = [('MixedCase', 'one'), ('mixedcase', 'two'),
                      ('.hidden', 'private'), ('Nul\0ignored', 'value\0ignored'),
                      ('', 'empty key'), ('Unicode-Ω', '雪😀')]
        for key, value in operations:
            oracle[key] = value
            table[key] = value
            with self.subTest(operation='write/read', key=key):
                self.assertEqual(list(table), list(oracle), (key, value))
                self.assertEqual(table[key], oracle[key])
        self.assertEqual(len(table), len(oracle))
        with self.subTest(operation='DocumentUserTextCount'):
            self.assertEqual(table.DocumentUserTextCount(), oracle.DocumentUserTextCount())
        for i in range(len(oracle)):
            self.assertEqual(table[i], oracle[i])
        self.assertEqual(table['absent'], oracle['absent'])
        for index in (-1, len(table)):
            with self.assertRaises(IndexError):
                table[index]
        for key in ('MIXEDCASE', 'Nul\0suffix', 'absent'):
            self.assertEqual(table.Delete(key), oracle.Delete(key))
            self.assertEqual(list(table), list(oracle))
        revision = self.m.metadata_revision()
        table['.hidden'] = 'private'
        table.Delete('missing')
        self.assertEqual(self.m.metadata_revision(), revision, 'no-op must not advance native state')
        EVIDENCE['native_string_oracle_rows'] = list(oracle)

    def test_native_document_user_text_count_semantics(self):
        oracle_file = r3.File3dm()
        oracle = oracle_file.Strings
        table = self.m.file.Strings
        measurements = EVIDENCE['native_document_user_text_count'] = []
        for key in ('plain', '.hidden', 'Section\\Entry', 'section/entry',
                    'a\\b\\c', '\\', '\\leading', 'trailing\\'):
            table[key] = 'value'
            oracle[key] = 'value'
            measurements.append({'key': key, 'rows': list(oracle),
                                 'native_count': oracle.DocumentUserTextCount(),
                                 'facade_count': table.DocumentUserTextCount()})
            with self.subTest(key=key):
                self.assertEqual(table.DocumentUserTextCount(), oracle.DocumentUserTextCount())

    def test_snapshot_is_immutable_native_handle_and_hot_reads_never_export_rows(self):
        # Populate BEFORE adoption, avoiding O(N^2) fixture construction via edits.
        file = r3.File3dm()
        for i in range(2048):
            file.Strings[f'key-{i:04d}'] = f'value-{i}'
        self.m.file = file
        session = self.session()
        counts = session.call_counts
        snapshots = [self.m.snapshot().strings for _ in range(24)]
        for snapshot in snapshots:
            self.assertIsInstance(snapshot, native.Snapshot)
            self.assertIs(snapshot._session, session)
            self.assertFalse(hasattr(snapshot, '__dict__'))
            self.assertIs(copy.deepcopy(snapshot), snapshot)
            with self.assertRaises(AttributeError):
                snapshot._state_id = 1
            with self.assertRaises(AttributeError):
                del snapshot._handle
        for i in range(64):
            self.assertEqual(self.m.document_user_text('key-1024'), 'value-1024')
            self.assertEqual(file.Strings['key-1024'], 'value-1024')
            self.assertEqual(file.Strings[1024], ('key-1024', 'value-1024'))
        self.assertEqual(snapshots[0], snapshots[-1])
        delta = {key: value - counts.get(key, 0) for key, value in session.call_counts.items()}
        self.assertEqual(delta.get('rows', 0), 0)
        self.assertEqual(delta.get('snapshot_rows', 0), 0)
        self.assertEqual(delta['snapshot'], 24)
        self.assertEqual(delta['get'], 128)
        self.assertEqual(delta['at'], 64)
        self.assertEqual(delta['equal'], 1)
        small = Model().snapshot().strings
        self.assertEqual(sys.getsizeof(small), sys.getsizeof(snapshots[0]))
        self.m.file.Strings['key-1024'] = 'later'
        self.assertNotEqual(self.m.snapshot().strings, snapshots[0])
        self.m._document_metadata.restore(snapshots[0])
        self.assertEqual(self.m.document_user_text('key-1024'), 'value-1024')
        EVIDENCE['hot_path'] = {'rows': 2048, 'abi_delta': delta,
                                'snapshot_python_bytes': sys.getsizeof(snapshots[0])}

    def test_model_snapshot_restores_metadata_and_geometry_together(self):
        self.m.file.Strings['phase'] = 'saved'
        before = self.m.snapshot()
        self.m.file.Strings['phase'] = 'edited'
        oid, _ = self.m.add_object(r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(3, 4, 0)), 'line')
        after = self.m.snapshot()
        self.assertEqual(len(after), 1)
        self.m.restore(before)
        self.assertEqual(self.m.document_user_text('phase'), 'saved')
        self.assertEqual(self.m.object_count(), 0)
        self.m.restore(after)
        self.assertEqual(self.m.document_user_text('phase'), 'edited')
        self.assertEqual(self.m.object_count(), 1)
        self.assertIsNotNone(self.m.file.Objects.FindId(oid))
        self.assertEqual(self.m.snapshot().strings, after.strings)

    def test_group_and_layer_roundtrips_preserve_session_and_old_snapshots(self):
        self.m.file.Strings['phase'] = 'before'
        before = self.m.snapshot()
        session = self.session()
        self.m.file.Strings['phase'] = 'after'
        for kind in ('groups', 'layers'):
            old_file, old_table = self.m.file, self.m.file.Strings
            if kind == 'groups':
                group = r3.Group()
                group.Name = 'fixture-group'
                self.m.file.Groups.Add(group)
                records = self.m.group_records()
                self.m._replace_group_table(records)
                self.assertEqual(self.m.group_records(), records)
            else:
                self.m.add_layer('fixture-layer')
                records = self.m.layer_records()
                self.m._replace_layer_table(records)
                self.assertEqual(self.m.layer_records(), records)
            self.assertIsNot(self.m.file, old_file)
            self.assertIs(self.session(), session, 'internal round trip must not adopt a new session')
            self.assertEqual(self.m.document_user_text('phase'), 'after')
            with self.assertRaisesRegex(metadata.MetadataError, 'retired'):
                old_table['phase'] = 'must not reach current document'
        self.m.restore(before)
        self.assertEqual(self.m.document_user_text('phase'), 'before')
        self.assertIs(self.session(), session)
        self.assertEqual(self.m.group_records(), before.groups)
        self.assertEqual(self.m.layer_records(), before.layers)

    def test_external_adoption_retires_views_and_rejects_cross_document_snapshots(self):
        self.m.file.Strings['old'] = 'document'
        old_file, old_table = self.m.file, self.m.file.Strings
        old_snapshot = self.m.snapshot()
        old_session = self.session()
        file = r3.File3dm()
        file.Strings['new'] = 'document'
        self.m.file = file
        self.assertIs(self.m.file, file)
        self.assertIsNot(self.session(), old_session)
        self.assertEqual(self.m.document_user_text(), [('new', 'document')])
        for action in (lambda: len(old_table), lambda: list(old_table),
                       lambda: old_table['old'], lambda: old_table.Delete('old'),
                       lambda: old_file.Strings):
            with self.assertRaisesRegex(metadata.MetadataError, 'retired'):
                action()
        with self.assertRaisesRegex(native.SessionError, 'snapshot_owner_mismatch'):
            self.m.restore(old_snapshot)
        self.assertEqual(self.m.document_user_text(), [('new', 'document')])
        self.m.file.Strings['still'] = 'usable'
        self.assertEqual(self.m.document_user_text('still'), 'usable')

    def _escaped_metadata_file(self):
        def temporary_owner():
            owner = Model()
            owner.file.Strings['Project'] = 'unsaved 雪 metadata'
            owner.file.Strings['.private'] = 'retained'
            return owner.file, owner._document_metadata

        file, binding = temporary_owner()
        self.assertIsNone(binding.model(), 'fixture must retain the file, not its original model')
        self.assertFalse(binding.retired)
        self.assertEqual(list(metadata._raw(file)), [], 'orphan fixture projection must still be stale')
        self.assertEqual(binding.table['Project'], 'unsaved 雪 metadata')
        return file, binding

    def test_orphan_file_adoption_materializes_metadata_into_a_new_session(self):
        file, orphan = self._escaped_metadata_file()
        old_table, old_session = orphan.table, orphan.session
        old_snapshot = old_session.snapshot()
        expected = list(old_table)
        previous_session = self.session()
        self.m.file = file
        self.assertIs(self.m.file, file)
        self.assertEqual(self.m.metadata_backend, 'rust')
        self.assertEqual(self.m.document_user_text(), expected, 'orphan metadata was lost during adoption')
        self.assertEqual(list(metadata._raw(file)), expected)
        self.assertIsNot(self.session(), old_session)
        self.assertNotEqual(self.session().handle, old_session.handle)
        self.assertIsNot(self.session(), previous_session)
        self.assertIs(metadata._hooks()['files'][file], self.m._document_metadata)
        self.assertTrue(orphan.retired)
        for action in (lambda: len(old_table), lambda: list(old_table),
                       lambda: old_table['Project'], lambda: old_table.Delete('Project'),
                       lambda: old_table.__setitem__('Project', 'must not reach new owner')):
            with self.assertRaisesRegex(metadata.MetadataError, 'retired'):
                action()
        with self.assertRaisesRegex(native.SessionError, 'snapshot_owner_mismatch'):
            self.m._document_metadata.restore(old_snapshot)
        live_foreign = Model()
        live_foreign.file.Strings['foreign'] = 'live owner'
        revision = self.m.metadata_revision()
        with self.assertRaisesRegex(metadata.MetadataError, 'already_owned'):
            self.m.file = live_foreign.file
        self.assertIs(self.m.file, file)
        self.assertEqual(self.m.metadata_revision(), revision)
        self.assertEqual(self.m.document_user_text(), expected)
        self.assertEqual(live_foreign.document_user_text('foreign'), 'live owner')
        self.m.file.Strings['Project'] = 'new owner edit'
        self.assertEqual(self.m.document_user_text('Project'), 'new owner edit')
        self.assertEqual(old_session.get('Project', exact=True)['value'], 'unsaved 雪 metadata')
        EVIDENCE['orphan_native_adoption'] = {
            'old_handle': old_session.handle, 'new_handle': self.session().handle,
            'stale_projection_preserved': True, 'live_owner_rejected': True}

    def test_failed_orphan_adoption_does_not_retire_either_binding(self):
        file, orphan = self._escaped_metadata_file()
        table = orphan.table
        expected = list(table)
        self.m.file.Strings['destination'] = 'still usable'
        original_file, original_binding = self.m.file, self.m._document_metadata
        revision = self.m.metadata_revision()
        with patch.object(native, 'Session', side_effect=native.SessionError('injected_orphan_create_failure')):
            with self.assertRaisesRegex(native.SessionError, 'injected_orphan_create_failure'):
                self.m.file = file
        self.assertFalse(orphan.retired, 'failed orphan adoption retired the escaped view')
        self.assertFalse(original_binding.retired)
        self.assertIs(self.m.file, original_file)
        self.assertIs(self.m._document_metadata, original_binding)
        self.assertIs(metadata._hooks()['files'][file], orphan)
        self.assertEqual(self.m.metadata_revision(), revision)
        self.assertEqual(self.m.document_user_text('destination'), 'still usable')
        self.assertEqual(list(table), expected)
        table['after-failure'] = 'also unsaved'
        self.assertEqual(metadata._raw(file)['after-failure'], '')
        self.m.file = file
        self.assertEqual(self.m.document_user_text('Project'), 'unsaved 雪 metadata')
        self.assertEqual(self.m.document_user_text('after-failure'), 'also unsaved')
        self.assertTrue(orphan.retired)
        self.assertTrue(original_binding.retired)
        self.assertIsNot(self.session(), orphan.session)

    def test_native_admission_precedes_orphan_and_previous_projection(self):
        file, orphan = self._escaped_metadata_file()
        self.m.file.Strings['destination'] = 'unprojected destination'
        previous = self.m._document_metadata
        old_file, old_table = self.m.file, self.m.file.Strings
        snapshot = self.m.snapshot()
        revision = self.m.metadata_revision()
        expected = list(orphan.table)
        raw_before = (tuple(metadata._raw(file)), tuple(metadata._raw(old_file)))
        self.assertEqual(raw_before, ((), ()), 'both projections must be stale to expose early writes')
        env = dict(os.environ)
        env.pop('NARWHAL_APPLICATION_CORE', None)
        with patch.dict(os.environ, env, clear=True), patch.object(native, '_ROOT', self.root):
            with self.assertRaisesRegex(native.SessionUnavailable, 'NARWHAL_APPLICATION_CORE'):
                self.m.file = file
        self.assertEqual((tuple(metadata._raw(file)), tuple(metadata._raw(old_file))), raw_before,
                         'native refusal projected metadata before admission')
        # Present but incompatible ABI, unloadable explicit binary, and native
        # create refusal must have the same host-side atomicity as absence.
        class Function:
            def __call__(self):
                return 0
        class OldCore:
            narwhal_session_abi = Function()
        failures = [
            patch.object(native.ctypes, 'CDLL', return_value=OldCore()),
            patch.object(native.ctypes, 'CDLL', side_effect=OSError('injected unloadable core')),
            patch.object(native, '_call', side_effect=native.SessionError('injected_create_failure')),
        ]
        for fault in failures:
            with fault:
                with self.assertRaises((native.SessionUnavailable, native.SessionError)):
                    self.m.file = file
            self.assertEqual((tuple(metadata._raw(file)), tuple(metadata._raw(old_file))), raw_before,
                             'native refusal projected metadata before admission')
        self.assertIs(self.m.file, old_file)
        self.assertIs(self.m._document_metadata, previous)
        self.assertIs(metadata._hooks()['files'][file], orphan)
        self.assertIs(metadata._hooks()['files'][old_file], previous)
        self.assertFalse(orphan.retired)
        self.assertFalse(previous.retired)
        self.assertEqual(self.m.metadata_revision(), revision)
        self.assertEqual(list(orphan.table), expected)
        self.assertEqual(old_table['destination'], 'unprojected destination')
        self.m.restore(snapshot)
        self.m.file = file
        self.assertEqual(self.m.document_user_text(), expected)
        self.assertTrue(orphan.retired)
        self.assertTrue(previous.retired)
        target = self.root / 'adopted-native.3dm'
        self.assertTrue(self.m.save(str(target)))
        self.assertEqual(list(r3.File3dm.Read(str(target)).Strings), expected)

    def test_cross_document_snapshot_rejection_precedes_any_document_mutation(self):
        self.m.file.Strings['keep'] = 'live document'
        self.m.add_layer('live-layer')
        old_file = self.m.file
        old_table = old_file.Strings
        old_layers = self.m.layer_records()
        alien = Model()
        alien.add_layer('alien-layer')
        foreign_snapshot = alien.snapshot()
        with self.assertRaisesRegex(native.SessionError, 'snapshot_owner_mismatch'):
            self.m.restore(foreign_snapshot)
        self.assertEqual(self.m.layer_records(), old_layers,
                         'rejected snapshot must not replace live layers')
        self.assertIs(self.m.file, old_file, 'rejected snapshot must not retire the live backing file')
        self.assertEqual(old_table['keep'], 'live document')
        old_table['keep'] = 'still usable'
        self.assertEqual(self.m.document_user_text('keep'), 'still usable')

    def test_failed_adoption_leaves_old_file_session_views_and_snapshot_usable(self):
        self.m.file.Strings['keep'] = 'safe'
        old_file, old_table, session = self.m.file, self.m.file.Strings, self.session()
        snapshot = self.m.snapshot()
        other = Model()
        with self.assertRaisesRegex(metadata.MetadataError, 'already_owned'):
            self.m.file = other.file
        with patch.object(native, 'Session', side_effect=native.SessionError('injected_create_failure')):
            with self.assertRaisesRegex(native.SessionError, 'injected_create_failure'):
                self.m.file = r3.File3dm()
        self.assertIs(self.m.file, old_file)
        self.assertIs(self.session(), session)
        self.assertEqual(old_table['keep'], 'safe')
        old_table['keep'] = 'changed'
        self.m.restore(snapshot)
        self.assertEqual(old_table['keep'], 'safe')
        self.assertTrue(self.m.save(str(self.root / 'still-usable.3dm')))

    def test_raw_escaped_native_writes_are_detected_and_not_discarded(self):
        self.m.file.Strings['owner'] = 'rust'
        raw = metadata._raw(self.m.file)
        raw['escaped'] = 'native'
        target = self.root / 'existing.3dm'
        target.write_bytes(b'destination sentinel')
        for serialize in (lambda: self.m.file.Encode(), lambda: self.m.save(str(target)),
                          lambda: setattr(self.m, 'file', r3.File3dm())):
            with self.assertRaisesRegex(metadata.MetadataError, 'unmediated_native_metadata_write'):
                serialize()
            self.assertEqual(raw['escaped'], 'native')
            self.assertEqual(self.m.document_user_text('owner'), 'rust')
            self.assertEqual(target.read_bytes(), b'destination sentinel')
        raw.Delete('escaped')
        self.assertTrue(self.m.save(str(target)))
        self.assertEqual(r3.File3dm.Read(str(target)).Strings['owner'], 'rust')

    def test_missing_defaults_and_explicit_missing_refuse_before_file_mutation(self):
        env = dict(os.environ)
        env.pop('NARWHAL_APPLICATION_CORE', None)
        incoming = r3.File3dm()
        incoming.Strings['old-file'] = 'before'
        target = self.root / 'old-file.3dm'
        self.assertTrue(incoming.Write(str(target), 8))
        disk_before = target.read_bytes()
        with patch.dict(os.environ, env, clear=True), patch.object(native, '_ROOT', self.root):
            for action in (Model, lambda: Model.load(str(target)),
                           lambda: setattr(self.m, 'file', incoming)):
                with self.assertRaisesRegex(native.SessionUnavailable, 'NARWHAL_APPLICATION_CORE'):
                    action()
            for path in (str(self.root / 'absent.so'), ''):
                with patch.dict(os.environ, {'NARWHAL_APPLICATION_CORE': path}):
                    with self.assertRaises(native.SessionUnavailable):
                        Model()
        self.assertEqual(list(incoming.Strings), [('old-file', 'before')])
        self.assertEqual(target.read_bytes(), disk_before)
        self.assertEqual(self.m.metadata_backend, 'rust')
        # Old on-disk File3dm strings are still adopted into Rust, not discarded.
        loaded = Model.load(str(target))
        self.assertEqual(loaded.metadata_backend, 'rust')
        self.assertEqual(loaded.document_user_text('old-file'), 'before')
        before = loaded.snapshot()
        self.assertIsInstance(before.strings, native.Snapshot)
        loaded.file.Strings['old-file'] = 'after'
        loaded.restore(before)
        self.assertEqual(loaded.document_user_text('old-file'), 'before')
        self.assertTrue(loaded.save(str(target)))
        self.assertEqual(list(r3.File3dm.Read(str(target)).Strings), [('old-file', 'before')])

    def test_runtime_prepare_refusal_precedes_callbacks_and_document_mutation(self):
        self.m.file.Strings['keep'] = 'safe'
        binding = self.m._document_metadata
        session = self.session()
        before = session.info()
        raw_before = tuple(metadata._raw(self.m.file))
        with patch.object(session, 'prepare', side_effect=native.SessionError('transport_error')), \
                patch.object(self.m, '_will_change') as capture, \
                patch.object(native, 'load_library', side_effect=AssertionError('runtime rediscovery')):
            from unittest.mock import Mock
            callback = Mock()
            with self.assertRaisesRegex(native.SessionError, 'transport_error'):
                self.m.set_document_user_text('keep', 'lost', callback)
            callback.assert_not_called()
            capture.assert_not_called()
        self.assertEqual(session.info(), before)
        self.assertEqual(tuple(metadata._raw(self.m.file)), raw_before)
        self.assertIs(self.m._document_metadata, binding)
        self.assertEqual(self.m.document_user_text('keep'), 'safe')

    def test_runtime_native_error_never_falls_back_and_recovery_is_same_session(self):
        self.m.file.Strings['keep'] = 'safe'
        session = self.session()
        real_request = session.request
        def fail_get(op, **fields):
            if op == 'get':
                raise native.SessionError('injected_read_failure')
            return real_request(op, **fields)
        with patch.object(session, 'request', side_effect=fail_get):
            with self.assertRaisesRegex(native.SessionError, 'injected_read_failure'):
                self.m.document_user_text('keep')
            with self.assertRaisesRegex(native.SessionError, 'injected_read_failure'):
                self.m.file.Strings['keep']
        with patch.object(session, 'commit', side_effect=native.SessionError('injected_commit_failure')):
            with self.assertRaisesRegex(native.SessionError, 'injected_commit_failure'):
                self.m.file.Strings['keep'] = 'lost'
        self.assertIs(self.session(), session)
        self.assertEqual(self.m.metadata_backend, 'rust')
        self.assertEqual(self.m.document_user_text('keep'), 'safe')
        self.m.file.Strings['keep'] = 'recovered'
        self.assertEqual(self.m.document_user_text('keep'), 'recovered')
        session.close()
        with self.assertRaisesRegex(native.SessionError, 'invalid_session'):
            self.m.file.Strings['keep'] = 'must not fall back'
        self.assertEqual(self.m.metadata_backend, 'rust')

    def _reentrant_model_callback(self, kind):
        for placement in ('model-hook', 'explicit-callback'):
            with self.subTest(placement=placement):
                m = Model()
                m.file.Strings['Project'] = 'before'
                file, binding = m.file, m._document_metadata
                session = binding.session
                revision = m.metadata_revision()
                candidate = r3.File3dm()
                candidate.Strings['candidate'] = 'untouched'
                calls = []

                def callback():
                    calls.append(placement)
                    m.before_change = None  # No recursive hook can mask a missing guard.
                    if kind == 'metadata':
                        m.file.Strings['inner'] = 'must not commit'
                    else:
                        m.file = candidate

                expected = 'metadata_reentrant_change' if kind == 'metadata' else 'metadata_reentrant_adoption'
                with patch.object(session, 'abort', wraps=session.abort) as abort:
                    with self.assertRaisesRegex(metadata.MetadataError, expected):
                        if placement == 'model-hook':
                            m.before_change = callback
                            m.file.Strings['outer'] = 'must not commit'
                        else:
                            m.set_document_user_text('outer', 'must not commit', before_change=callback)
                self.assertEqual(calls, [placement])
                self.assertIs(m.file, file)
                self.assertIs(m._document_metadata, binding)
                self.assertFalse(binding.editing)
                self.assertFalse(binding.retired)
                self.assertEqual(m.document_user_text(), [('Project', 'before')])
                self.assertEqual(m.metadata_revision(), revision)
                self.assertEqual(list(candidate.Strings), [('candidate', 'untouched')])
                abort.assert_called_once()
                with self.assertRaisesRegex(native.SessionError, 'invalid_ticket'):
                    session.commit(abort.call_args.args[0])
                m.file.Strings['recovered'] = 'usable'
                self.assertEqual(m.document_user_text('recovered'), 'usable')

    def test_model_callback_reentrant_metadata_is_atomic(self):
        self._reentrant_model_callback('metadata')

    def test_model_callback_reentrant_adoption_is_atomic(self):
        self._reentrant_model_callback('adoption')

    def test_model_save_load_metadata_only_and_mixed(self):
        for mixed in (False, True):
            m = Model()
            m.file.Strings['Project'] = '雪 project'
            m.file.Strings['.private'] = 'kept'
            if mixed:
                m.add_object(r3.LineCurve(r3.Point3d(1, 2, 3), r3.Point3d(4, 6, 3)), 'Guide')
            target = self.root / f'model-{mixed}.3dm'
            self.assertTrue(m.save(str(target)))
            loaded = Model.load(str(target))
            self.assertEqual(loaded.metadata_backend, 'rust')
            self.assertNotEqual(loaded.metadata_revision()['session'], m.metadata_revision()['session'])
            self.assertEqual(loaded.document_user_text(), m.document_user_text())
            self.assertEqual(loaded.object_count(), int(mixed))
            self.assertEqual(loaded.layer_records(), m.layer_records())
            self.assertEqual(loaded.unit_system(), m.unit_system())
            if mixed:
                self.assertEqual(str(loaded.file.Objects[0].Attributes.Id), str(m.file.Objects[0].Attributes.Id))
                self.assertEqual(loaded.file.Objects[0].Geometry.PointAtEnd, r3.Point3d(4, 6, 3))


class Dialogs:
    def __init__(self, *, save=(), open_=()):
        self.saves, self.opens, self.calls = list(save), list(open_), []

    def save(self, *args):
        self.calls.append('save')
        if not self.saves:
            raise AssertionError('unexpected save dialog')
        return filedialog.OK, str(self.saves.pop(0))

    def open(self, *args):
        self.calls.append('open')
        if not self.opens:
            raise AssertionError('unexpected open dialog')
        return filedialog.OK, str(self.opens.pop(0))

    def ask(self, *args):
        raise AssertionError('unexpected unsaved-changes dialog')


class CommandTests(ModelTests):
    # Deliberately select only methods declared here when building this suite.
    def setUp(self):
        super().setUp()
        app.set_model(self.m)
        app.doc_path = None
        app.mark_saved()
        app.history.clear()
        self.addCleanup(filedialog.set_backend, None)
        self.assertIsInstance(commands.COMMANDS['saveas'], file_commands.SaveAsCmd)
        self.assertIsInstance(commands.COMMANDS['open'], file_commands.OpenCmd)
        self.assertIsInstance(commands.COMMANDS['setdocumentusertext'], commands.SetDocumentUserTextCmd)
        self.assertIsInstance(commands.COMMANDS['getdocumentusertext'], commands.GetDocumentUserTextCmd)

    def feed(self, *lines):
        for line in lines:
            app.feed_line(line)
        self.assertIsNone(app.active_command, repr(app.history[-5:]))
        return '\n'.join(app.history[-5:])

    def set_text(self, key='Project', value='current'):
        self.feed('SetDocumentUserText', key, value)
        self.assertEqual(app.model.document_user_text(key), None if value == '\"\"' else value)

    def _history_state(self):
        stack = app.ctx.undo
        # Snapshot equality is insufficient: failed navigation must preserve the
        # exact Record and before/after handles, not just equivalent document rows.
        def records(items):
            return tuple((id(r), r.serial, r.name, id(r.before), id(r.after)) for r in items)
        return (records(stack._undo), records(stack._redo), stack.top_serial,
                stack.current_serial, stack.recording, stack.captured, stack._depth,
                app._saved_serial, app.doc_path, app.doc_modified())

    def _reentrant_command_hook(self, kind):
        self.set_text('Project', 'before')
        self.set_text('Future', 'redo must survive')
        self.feed('Undo')
        app.mark_saved()
        history = self._history_state()
        file, binding = self.m.file, self.m._document_metadata
        session = binding.session
        revision = self.m.metadata_revision()
        raw_before = list(metadata._raw(file))
        candidate = r3.File3dm()
        candidate.Strings['candidate'] = 'untouched'
        calls = []

        def hook():
            calls.append(kind)
            self.m.before_change = None
            if kind == 'metadata':
                self.m.file.Strings['inner'] = 'must not commit'
            else:
                self.m.file = candidate

        counts = session.call_counts
        self.m.before_change = hook
        with patch.object(session, 'abort', wraps=session.abort) as abort:
            message = self.feed('SetDocumentUserText', 'outer', 'must not commit')
        self.assertIs(self.m.file, file, 'reentrant callback changed backing file')
        self.assertEqual(self.m.document_user_text(), [('Project', 'before')],
                         'reentrant callback leaked metadata')
        self.assertEqual(self._history_state(), history, 'reentrant callback changed document history')
        expected = 'metadata_reentrant_change' if kind == 'metadata' else 'metadata_reentrant_adoption'
        self.assertIn(expected, message)
        self.assertEqual(calls, [kind])
        self.assertIs(self.m._document_metadata, binding)
        self.assertFalse(binding.editing)
        self.assertFalse(binding.retired)
        self.assertEqual(self.m.metadata_revision(), revision)
        self.assertEqual(list(metadata._raw(file)), raw_before)
        self.assertEqual(list(candidate.Strings), [('candidate', 'untouched')])
        delta = {key: value - counts.get(key, 0) for key, value in session.call_counts.items()}
        self.assertEqual(delta.get('prepare', 0), 1, 'inner edit must be rejected before prepare')
        self.assertEqual(delta.get('commit', 0), 0)
        self.assertEqual(delta.get('abort', 0), 1)
        abort.assert_called_once()
        with self.assertRaisesRegex(native.SessionError, 'invalid_ticket'):
            session.commit(abort.call_args.args[0])
        self.feed('Redo')
        self.assertEqual(self.m.document_user_text('Future'), 'redo must survive')
        self.set_text('Recovered', 'usable')
        EVIDENCE.setdefault('reentrant_hooks', {})[kind] = delta

    def test_registered_reentrant_metadata_hook_preserves_data_and_history(self):
        self._reentrant_command_hook('metadata')

    def test_registered_reentrant_adoption_hook_preserves_data_and_history(self):
        self._reentrant_command_hook('adoption')

    def test_case_variant_noop_keeps_redo_and_delete_uses_canonical_key(self):
        self.set_text('Project', 'v')
        self.set_text('Future', 'redo value')
        self.feed('Undo')
        app.mark_saved()
        history = self._history_state()
        revision = self.m.metadata_revision()
        session = self.session()
        counts = session.call_counts
        with patch.object(app.ctx, 'note_change', wraps=app.ctx.note_change) as capture:
            message = self.feed('SetDocumentUserText', 'project', 'v')
        self.assertEqual(capture.call_count, 0, 'ordinal no-op must not capture undo')
        self.assertIn('unchanged', message)
        self.assertEqual(self._history_state(), history, 'ordinal no-op must retain redo and saved marker')
        self.assertEqual(self.m.metadata_revision(), revision)
        self.assertEqual(session.call_counts['prepare'] - counts.get('prepare', 0), 1)
        self.assertEqual(session.call_counts.get('commit', 0), counts.get('commit', 0))
        self.assertEqual(self.m.document_user_text(), [('Project', 'v')])
        self.assertIn('Project = v', self.feed('GetDocumentUserText', 'Project'))
        self.assertIn('project not found', self.feed('GetDocumentUserText', 'project'))
        self.assertEqual(self._history_state(), history)
        self.feed('Redo')
        self.assertEqual(self.m.document_user_text('Future'), 'redo value')
        self.feed('Undo')
        with patch.object(app.ctx, 'note_change', wraps=app.ctx.note_change) as capture:
            self.feed('SetDocumentUserText', 'project', '\"\"')
        self.assertEqual(capture.call_count, 1)
        self.assertEqual(self.m.document_user_text(), [])
        self.assertFalse(app.ctx.undo.can_redo, 'a real deletion, unlike a no-op, invalidates redo')
        self.feed('Undo')
        self.assertEqual(self.m.document_user_text(), [('Project', 'v')])
        self.assertFalse(app.doc_modified())
        self.feed('Redo')
        self.assertEqual(self.m.document_user_text(), [])
        self.assertTrue(app.doc_modified())

    def _raw_fault_navigation(self, direction):
        self.set_text('Project', 'saved')
        saved_layers = self.m.layer_records()
        target = self.root / 'saved-marker.3dm'
        self.assertTrue(self.m.save(str(target)))
        saved_bytes = target.read_bytes()
        app.mark_saved(str(target))
        saved_serial = app._saved_serial
        session = self.session()
        app.ctx.begin_undo('metadata and layer edit')
        self.m.file.Strings['Project'] = 'edited'
        self.m.add_layer('edited-layer')
        self.assertTrue(app.ctx.end_undo())
        edited_layers = self.m.layer_records()
        if direction == 'Redo':
            self.feed('Undo')
            self.assertFalse(app.doc_modified())
        else:
            self.assertTrue(app.doc_modified())
        file, table = self.m.file, self.m.file.Strings
        layers, rows = self.m.layer_records(), self.m.document_user_text()
        history, revision = self._history_state(), self.m.metadata_revision()
        raw = metadata._raw(file)
        raw['escaped'] = 'must not be discarded'
        # Execute the real registered command/context. The exception is expected;
        # avoid the separate UI command-start error presentation path here.
        with self.assertRaisesRegex(metadata.MetadataError, 'unmediated_native_metadata_write'):
            commands.COMMANDS[direction.lower()].execute(app.ctx)
        try:
            actual_layers = self.m.layer_records()
        except IndexError as exc:
            self.fail(f'raw fault refusal changed layers: native layer table is unusable: {exc}')
        self.assertEqual(actual_layers, layers, 'raw fault refusal changed layers')
        self.assertIs(self.m.file, file, 'raw fault refusal replaced backing file')
        self.assertEqual(self._history_state(), history, 'failed navigation changed history before restore')
        self.assertEqual(self.m.metadata_revision(), revision)
        self.assertEqual(self.m.document_user_text(), rows)
        self.assertEqual(list(table), rows, 'live view must not be retired by a refused restore')
        self.assertEqual(raw['escaped'], 'must not be discarded')
        self.assertEqual(app._saved_serial, saved_serial)
        self.assertEqual(target.read_bytes(), saved_bytes)
        self.assertIs(self.session(), session)
        raw.Delete('escaped')
        self.feed(direction)
        expected_layers = saved_layers if direction == 'Undo' else edited_layers
        self.assertEqual(self.m.layer_records(), expected_layers)
        self.assertEqual(self.m.document_user_text('Project'), 'saved' if direction == 'Undo' else 'edited')
        self.assertEqual(app.doc_modified(), direction == 'Redo')
        self.assertIs(self.session(), session)
        # Both successful directions remain usable after the fault is cleared.
        self.feed('Redo' if direction == 'Undo' else 'Undo')
        self.assertEqual(self.m.document_user_text('Project'), 'edited' if direction == 'Undo' else 'saved')
        self.assertEqual(app.doc_modified(), direction == 'Undo')
        self.assertEqual(app._saved_serial, saved_serial)
        self.assertEqual(target.read_bytes(), saved_bytes)
        EVIDENCE.setdefault('raw_fault_navigation', {})[direction] = 'refused atomically; recovered'

    def test_raw_fault_layer_undo_preserves_document_history_and_saved_marker(self):
        self._raw_fault_navigation('Undo')

    def test_raw_fault_layer_redo_preserves_document_history_and_saved_marker(self):
        self._raw_fault_navigation('Redo')

    def test_registered_set_get_queries_noops_and_native_counts(self):
        self.set_text('Project', 'native')
        session = self.session()
        self.assertEqual(list(metadata._raw(self.m.file)), [])
        before = session.call_counts
        serial = app.ctx.undo.top_serial
        self.assertIn('Project = native', self.feed('GetDocumentUserText', 'Project'))
        self.assertIn('project not found', self.feed('GetDocumentUserText', 'project'))
        self.assertEqual(session.call_counts.get('rows', 0), before.get('rows', 0))
        self.assertEqual(session.call_counts['get'] - before.get('get', 0), 2)
        self.assertEqual(app.ctx.undo.top_serial, serial)
        revision = self.m.metadata_revision()
        self.set_text('Project', 'native')
        self.assertEqual(app.ctx.undo.top_serial, serial)
        self.assertEqual(self.m.metadata_revision(), revision)
        self.set_text('.hidden', 'visible-to-query')
        self.assertIn('.hidden = visible-to-query', self.feed('GetDocumentUserText', ''))
        self.set_text('Project', '\"\"')
        self.feed('Undo')
        self.assertEqual(self.m.document_user_text('Project'), 'native')
        EVIDENCE['registered_query_counts'] = session.call_counts

    def test_unified_actual_commands_mixed_undo_redo_and_saved_state(self):
        self.set_text('phase', 'saved')
        app.mark_saved()
        baseline = app.ctx.undo.top_serial
        session = self.session()
        stack = app.ctx.undo
        stack.begin('mixed metadata and geometry')
        self.m.file.Strings['phase'] = 'changed'
        self.feed('Line', 'w0,0,0', 'w3,4,0')
        self.assertTrue(stack.end())
        self.assertEqual(stack.undo_names(), ['mixed metadata and geometry', 'SetDocumentUserText'])
        self.assertEqual(self.m.object_count(), 1)
        self.assertTrue(app.doc_modified())
        self.feed('Undo')
        self.assertEqual(self.m.document_user_text('phase'), 'saved')
        self.assertEqual(self.m.object_count(), 0)
        self.assertEqual(stack.top_serial, baseline)
        self.assertFalse(app.doc_modified())
        self.feed('Redo')
        self.assertEqual(self.m.document_user_text('phase'), 'changed')
        self.assertEqual(self.m.object_count(), 1)
        self.assertTrue(app.doc_modified())
        app.mark_saved()
        self.feed('Undo')
        self.assertTrue(app.doc_modified())
        self.feed('Redo')
        self.assertFalse(app.doc_modified())
        self.assertIs(self.session(), session)

    def test_actual_saveas_open_whole_3dm_metadata_only_and_mixed(self):
        for mixed in (False, True):
            with self.subTest(mixed=mixed):
                m = Model()
                app.set_model(m)
                app.doc_path = None
                self.set_text('Project', f'whole-document-{mixed}')
                m.file.Strings['.private'] = 'retained'
                m.add_layer('Metadata fixture layer')
                m.set_absolute_tolerance(0.125)
                group = r3.Group()
                group.Name = 'empty-group-is-document-data'
                m.file.Groups.Add(group)
                if mixed:
                    self.feed('Line', 'w1,2,3', 'w4,6,3')
                expected_layers, expected_groups = m.layer_records(), m.group_records()
                target = self.root / f'whole-{mixed}.3dm'
                dialog = Dialogs(save=[target])
                filedialog.set_backend(dialog)
                self.feed('SaveAs')
                self.assertEqual(dialog.calls, ['save'])
                self.assertTrue(target.is_file(), repr(app.history[-5:]))
                raw = r3.File3dm.Read(str(target))
                self.assertEqual(list(raw.Strings), m.document_user_text())
                self.assertEqual(len(raw.Objects), int(mixed))
                self.assertEqual(len(raw.Layers), 2)
                self.assertEqual(len(raw.Groups), 1)
                self.assertIsNone(app.doc_path, '.3dm is still an interchange copy')
                # New disposable model proves Open actually adopts the file, not stale app state.
                app.set_model(Model())
                dialog = Dialogs(open_=[target])
                filedialog.set_backend(dialog)
                self.feed('Open')
                opened = app.model
                self.assertIsNot(opened, m)
                self.assertEqual(dialog.calls, ['open'])
                self.assertEqual(opened.metadata_backend, 'rust')
                self.assertEqual(opened.document_user_text(), m.document_user_text())
                self.assertEqual(opened.layer_records(), expected_layers)
                self.assertEqual(opened.group_records(), expected_groups)
                self.assertEqual(opened.absolute_tolerance(), 0.125)
                self.assertEqual(opened.object_count(), int(mixed))
                self.assertFalse(app.doc_modified())
                self.assertEqual(app.suggested_name, str(target.with_suffix('.narwhal')))
                if mixed:
                    self.assertEqual(opened.file.Objects[0].Geometry.PointAtEnd, r3.Point3d(4, 6, 3))

    def test_narwhal_persistence_existing_native_executable_and_actual_commands(self):
        from narwhal3d import native_document, narwhal_document
        executable = native_document.require()
        self.assertTrue(executable.is_file())
        EVIDENCE['document_core'] = {'path': str(executable), 'sha256': digest(executable)}
        for mixed in (False, True):
            with self.subTest(mixed=mixed):
                app.set_model(Model())
                app.doc_path = None
                self.set_text('Project', f'narwhal-{mixed}')
                if mixed:
                    self.feed('Line', 'w0,0,0', 'w3,4,0')
                original = app.model
                target = self.root / f'native-{mixed}.narwhal'
                dialog = Dialogs(save=[target])
                filedialog.set_backend(dialog)
                self.feed('SaveAs')
                self.assertTrue(target.is_file(), repr(app.history[-5:]))
                self.assertEqual(app.doc_path, str(target))
                self.assertFalse(app.doc_modified())
                members = native_document.inspect(target)
                self.assertEqual({row[2] for row in members}, {'document.3dm', 'manifest.json'})
                loaded = narwhal_document.load_model(target)
                self.assertEqual(loaded.document_user_text(), original.document_user_text())
                self.assertEqual(loaded.object_count(), int(mixed))
                self.assertEqual(loaded.metadata_backend, 'rust')
                app.set_model(Model())
                app.doc_path = None
                filedialog.set_backend(Dialogs(open_=[target]))
                self.feed('Open')
                self.assertEqual(app.model.document_user_text(), original.document_user_text())
                self.assertEqual(app.model.object_count(), int(mixed))
                self.assertEqual(app.doc_path, str(target))
                self.assertFalse(app.doc_modified())

    def test_failed_saveas_keeps_destination_active_model_and_dirty_state(self):
        self.set_text('Project', 'unsaved')
        target = self.root / 'existing.3dm'
        target.write_bytes(b'previous destination')
        serial = app.ctx.undo.top_serial
        def fail_after_partial(path, *args):
            Path(path).write_bytes(b'partial new bytes')
            return False
        filedialog.set_backend(Dialogs(save=[target]))
        with patch.object(self.m, 'save', side_effect=fail_after_partial):
            history = self.feed('SaveAs')
        self.assertIn('could not save', history)
        self.assertEqual(target.read_bytes(), b'previous destination')
        self.assertIs(app.model, self.m)
        self.assertIsNone(app.doc_path)
        self.assertEqual(app.ctx.undo.top_serial, serial)
        self.assertTrue(app.doc_modified())
        self.assertEqual(list(self.root.iterdir()), [target], 'temporary sibling must be removed')
        # The same failure guarantee with an actual escaped-native mutation, not a fake writer.
        metadata._raw(self.m.file)['escaped'] = 'unexpected'
        filedialog.set_backend(Dialogs(save=[target]))
        self.assertIn('unmediated_native_metadata_write', self.feed('SaveAs'))
        self.assertEqual(target.read_bytes(), b'previous destination')

    def test_native_failed_save_preserves_existing_destination(self):
        from narwhal3d import narwhal_document
        self.set_text('Project', 'native-destination')
        target = self.root / 'existing.narwhal'
        self.assertTrue(narwhal_document.save_model(self.m, target))
        saved_bytes = target.read_bytes()
        self.set_text('Project', 'unsaved replacement')
        metadata._raw(self.m.file)['escaped'] = 'unmediated'
        filedialog.set_backend(Dialogs(save=[target]))
        self.assertIn('unmediated_native_metadata_write', self.feed('SaveAs'))
        self.assertEqual(target.read_bytes(), saved_bytes)
        self.assertEqual(self.m.document_user_text('Project'), 'unsaved replacement')
        self.assertTrue(app.doc_modified())
        self.assertIsNone(app.doc_path)
        metadata._raw(self.m.file).Delete('escaped')
        self.assertTrue(narwhal_document.save_model(self.m, target))
        self.assertNotEqual(target.read_bytes(), saved_bytes)


def suite_for(cls):
    return unittest.TestSuite(cls(name) for name in sorted(cls.__dict__) if name.startswith('test_'))


def without_guard(owner, name, guard, replacement=''):
    """Compile one in-memory mutation; all other product code is retained."""
    function = getattr(owner, name)
    source = textwrap.dedent(inspect.getsource(function))
    if source.count(guard) != 1:
        raise AssertionError(f'mutation anchor changed: {name}')
    namespace = dict(function.__globals__)
    exec(compile(source.replace(guard, replacement, 1), f'<integration mutation {name}>', 'exec'), namespace)
    return patch.object(owner, name, namespace[name])


def mutation_checks(mode):
    """Re-break meaningful runtime behavior; never rewrite watched source files."""
    real_adopt = metadata.adopt

    def premature_orphan_retirement(model, file, previous=None, *, preserve=False):
        existing = metadata._hooks()['files'].get(file)
        if existing is not None and existing.model() is None:
            existing.retired = True
        return real_adopt(model, file, previous, preserve=preserve)

    def premature_projection(model, file, previous=None, *, preserve=False):
        existing = metadata._hooks()['files'].get(file)
        if existing is not None and existing.model() is None:
            existing.materialize()
        if previous is not None:
            previous.materialize()
        return real_adopt(model, file, previous, preserve=preserve)

    cases = [
        ('capture_before_native_prepare', without_guard(metadata.Binding, 'change',
         '    prepared = self.session.prepare(key, value)',
         '    self.model()._will_change()\n    prepared = self.session.prepare(key, value)'),
         ModelTests, 'test_runtime_prepare_refusal_precedes_callbacks_and_document_mutation',
         'Called 1 times'),
        ('native_admission_after_projection', patch.object(metadata, 'adopt', premature_projection),
         ModelTests, 'test_native_admission_precedes_orphan_and_previous_projection',
         'native refusal projected metadata before admission'),
        ('metadata_restore_removed', patch.object(metadata.Binding, 'restore', lambda *args: None),
         ModelTests, 'test_model_snapshot_restores_metadata_and_geometry_together', 'saved'),
        ('serialization_materialization_removed', patch.object(metadata.Binding, 'materialize', lambda *args: None),
         ModelTests, 'test_authority_real_file_identity_and_both_serialization_boundaries', 'native-only'),
        ('internal_session_preservation_removed',
         patch.object(Model, '_replace_backing_file', lambda self, file: setattr(self, 'file', file)),
         ModelTests, 'test_group_and_layer_roundtrips_preserve_session_and_old_snapshots', 'internal round trip'),
        ('orphan_materialization_removed', without_guard(metadata, 'adopt', '            existing.materialize()\n', '            pass\n'),
         ModelTests, 'test_orphan_file_adoption_materializes_metadata_into_a_new_session', 'unsaved'),
        ('orphan_retired_before_new_binding_ready', patch.object(metadata, 'adopt', premature_orphan_retirement),
         ModelTests, 'test_failed_orphan_adoption_does_not_retire_either_binding', 'failed orphan adoption retired'),
        ('orphan_authority_read_removed', without_guard(metadata, 'adopt',
         'existing.session.rows() if orphan else ', ''),
         ModelTests, 'test_orphan_file_adoption_materializes_metadata_into_a_new_session', 'orphan metadata was lost'),
    ]
    if mode == 'commands':
        stack_type = type(app.ctx.undo)
        real_undo, real_redo = stack_type.undo, stack_type.redo
        real_set = commands.SetDocumentUserTextCmd.execute

        def premature_undo(self, current, *, restore=None):
            record = real_undo(self, current)
            if record is not None and restore is not None:
                restore(record.before)
            return record

        def premature_redo(self, current, *, restore=None):
            record = real_redo(self, current)
            if record is not None and restore is not None:
                restore(record.after)
            return record

        def eager_metadata_capture(self, ctx):
            ctx.note_change(self.name)
            return real_set(self, ctx)

        cases = [
            ('registered_metadata_restore_removed', patch.object(metadata.Binding, 'restore', lambda *args: None),
             CommandTests, 'test_unified_actual_commands_mixed_undo_redo_and_saved_state', 'saved'),
            ('metadata_reentrance_guard_removed', without_guard(metadata.Binding, 'change',
             "    if self.editing:\n        raise MetadataError('metadata_reentrant_change')\n"),
             CommandTests, 'test_registered_reentrant_metadata_hook_preserves_data_and_history', 'reentrant callback'),
            ('adoption_reentrance_guard_removed', without_guard(metadata, 'adopt',
             "    if isinstance(previous, Binding) and previous.editing:\n        raise MetadataError('metadata_reentrant_adoption')\n"),
             CommandTests, 'test_registered_reentrant_adoption_hook_preserves_data_and_history', 'reentrant callback'),
            ('snapshot_projection_preflight_removed',
             patch.object(metadata.Binding, 'validate_snapshot',
                          lambda self, snapshot: self.session.validate_snapshot(snapshot)),
             CommandTests, 'test_raw_fault_layer_undo_preserves_document_history_and_saved_marker', 'raw fault refusal changed layers'),
            ('undo_history_moved_before_restore', patch.object(stack_type, 'undo', premature_undo),
             CommandTests, 'test_raw_fault_layer_undo_preserves_document_history_and_saved_marker', 'failed navigation changed history'),
            ('redo_history_moved_before_restore', patch.object(stack_type, 'redo', premature_redo),
             CommandTests, 'test_raw_fault_layer_redo_preserves_document_history_and_saved_marker', 'failed navigation changed history'),
            ('metadata_noop_captures_history', patch.object(commands.SetDocumentUserTextCmd, 'execute', eager_metadata_capture),
             CommandTests, 'test_case_variant_noop_keeps_redo_and_delete_uses_canonical_key', 'ordinal no-op must not capture undo'),
        ]
    records = []
    for name, mutation, cls, method, expected in cases:
        baseline = unittest.TextTestRunner(verbosity=1).run(cls(method))
        if not baseline.wasSuccessful():
            records.append({'name': name, 'killed': False, 'restored_green': False,
                            'reason': 'targeted baseline is not green; mutation not attempted'})
            continue
        output = io.StringIO()
        with mutation:
            result = unittest.TextTestRunner(stream=output, verbosity=2).run(cls(method))
        text = output.getvalue()
        # Infrastructure exceptions do not count as killing the intended mutation.
        killed = bool(result.failures) and not result.errors and expected in text
        records.append({'name': name, 'killed': killed, 'output': text})
        print('MUTATION', name, 'RED as intended' if killed else 'NOT KILLED', flush=True)
        restored = unittest.TextTestRunner(verbosity=1).run(cls(method))
        records[-1]['restored_green'] = restored.wasSuccessful()
    return records


def worker(mode, report, mutations):
    initialize(mode)
    group = Path('/sys/fs/cgroup') / Path('/proc/self/cgroup').read_text().strip().split('::', 1)[1].lstrip('/')
    limits = {name: (group / name).read_text().strip() for name in ('memory.max', 'memory.swap.max')}
    if limits != {'memory.max': str(2 * 1024**3), 'memory.swap.max': '0'}:
        raise RuntimeError(f'refusing unbounded test worker: {limits}')
    EVIDENCE['cgroup'] = dict(path=str(group), **limits)
    result = unittest.TextTestRunner(verbosity=2).run(suite_for(ModelTests if mode == 'model' else CommandTests))
    mutations_report = mutation_checks(mode) if mutations else []
    ok = result.wasSuccessful() and all(r['killed'] and r['restored_green'] for r in mutations_report)
    EVIDENCE['cgroup_usage'] = {name: (group / name).read_text().strip()
                                for name in ('memory.peak', 'memory.swap.current')}
    payload = {'ok': ok, 'phase': mode, 'tests': result.testsRun,
               'failures': result.failures, 'errors': result.errors,
               'evidence': EVIDENCE, 'mutations': mutations_report}
    # TestCase references in failure tuples are diagnostic strings, never pickled.
    report.write_text(json.dumps(payload, indent=2, default=str))
    print('SESSION_INTEGRATION_REPORT', report, 'PASS' if ok else 'FAIL', flush=True)
    return 0 if ok else 1


def launch(args):
    import fcntl
    if not os.environ.get('NARWHAL_APPLICATION_CORE'):
        raise RuntimeError('set NARWHAL_APPLICATION_CORE to the existing native library; never build here')
    out = Path(tempfile.mkdtemp(prefix='narwhal-session-integration-'))
    watched = list((ROOT / 'narwhal3d').rglob('*.py')) + list((ROOT / 'native/application_core').rglob('*.rs'))
    watched += [SELF, Path(os.environ['NARWHAL_APPLICATION_CORE']).resolve()]
    before = {str(p): digest(p) for p in watched}
    env = dict(os.environ)
    for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'TMPDIR'):
        folder = out / key.lower()
        folder.mkdir()
        env[key] = str(folder)
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONPYCACHEPREFIX=str(out / 'bytecode'),
               PYTHONPATH=str(ROOT / 'vendor/occt'), NARWHAL3D_OPEN_WORKERS='0',
               NARWHAL3D_SETTINGS_FILE=str(out / 'settings.json'),
               OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', LP_NUM_THREADS='1')
    runtime = ROOT / 'tools/blender-5.2.2-linux-x64'
    phases = ('model', 'commands') if args.phase == 'all' else (args.phase,)
    statuses = []
    for phase in phases:
        with ExitStack() as stack:
            if phase == 'commands':
                lock = stack.enter_context(open('/tmp/narwhal3d_gui.lock', 'a'))
                deadline = time.monotonic() + 90
                while True:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError('GUI lock busy; no Blender launched')
                        time.sleep(0.25)
            available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                                 if line.startswith('MemAvailable:')))
            if available < 6 * 1024**2:
                raise RuntimeError('need 6 GiB available: 2 GiB cap + 4 GiB desktop reserve')
            command = ['systemd-run', '--user', '--scope', '--quiet', '-p', 'MemoryMax=2G',
                       '-p', 'MemorySwapMax=0', '-p', 'CPUQuota=100%',
                       'timeout', '--signal=TERM', '--kill-after=5s', '240s',
                       'bwrap', '--ro-bind', '/', '/', '--dev-bind', '/dev', '/dev', '--proc', '/proc',
                       '--bind', str(out), str(out), '--unshare-net', '--die-with-parent']
            if phase == 'model':
                command += [str(runtime / '5.2/python/bin/python3.13'), '-B', str(SELF)]
            else:
                command += [str(runtime / 'blender'), '-b', '--factory-startup', '-t', '1',
                            '--python-exit-code', '1', '--python', str(SELF), '--']
            report = out / f'{phase}.json'
            command += ['--worker', phase, '--report', str(report)]
            if args.mutations:
                command += ['--mutations']
            (out / f'{phase}-launch.json').write_text(json.dumps({
                'argv': command, 'mem_available_kib': available}, indent=2))
            with (out / f'{phase}.log').open('w') as log:
                completed = subprocess.run(command, cwd=ROOT, env=env, stdout=log,
                                           stderr=subprocess.STDOUT, timeout=255, check=False)
            data = json.loads(report.read_text()) if report.exists() else {}
            statuses.append(completed.returncode == 0 and data.get('ok') is True)
            print(phase, 'exit', completed.returncode, 'tests', data.get('tests'), 'report', report, flush=True)
            print((out / f'{phase}.log').read_text()[-16000:], flush=True)
    after = {str(p): digest(p) for p in watched}
    unchanged = before == after
    (out / 'sources.json').write_text(json.dumps({'unchanged': unchanged, 'before': before, 'after': after}, indent=2))
    print('SOURCES_UNCHANGED', unchanged, 'REPORT_DIRECTORY', out, flush=True)
    return 0 if all(statuses) and unchanged else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--launch', action='store_true')
    parser.add_argument('--phase', choices=('all', 'model', 'commands'), default='all')
    parser.add_argument('--worker', choices=('model', 'commands'))
    parser.add_argument('--report', type=Path)
    parser.add_argument('--mutations', action='store_true')
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else sys.argv[1:]
    args = parser.parse_args(argv)
    if args.launch:
        raise SystemExit(launch(args))
    if not args.worker or not args.report:
        parser.error('use --launch (worker mode is reserved for the externally bounded launcher)')
    status = worker(args.worker, args.report, args.mutations)
    if args.worker == 'commands':
        # Blender otherwise treats SystemExit as a script exception, obscuring the result.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(status)
    raise SystemExit(status)
