#!/usr/bin/env python3
"""MAIN-RUST-HISTORY-02: public facade and actual Blender command integration.

Python is required for the host payload, rhino3dm and Blender callback boundary;
no history policy or geometry algorithm is reimplemented here.

  python3 -B spike/test_rust_history_integration.py --launch --mutations

Uses the owner-supplied /tmp history core (override NARWHAL_APPLICATION_CORE).
The launcher selects it for BOTH metadata and history, runs pure-host then real
background Blender, and requires 6 GiB available (2 GiB cap + 4 GiB reserve).
Only Blender takes the GUI lock. No builds, staging, VM, full suite, product writes
or other agents' source watches. Workers have a read-only project, private settings,
2 GiB/no-swap cgroup, one CPU and a 240s timeout. Receipts go to a unique /tmp dir.
--phase host|blender and --test METHOD allow bounded, concrete bug reproduction.
Mutations compile individual methods in memory; assertions, not incidental errors,
must kill each mutation, followed by a restored GREEN control.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import inspect
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import weakref
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
CORE = '/tmp/narwhal3d-history-core/target/release/libnarwhal_application_core.so'
# Deliberately not rglob(narwhal3d): text/UI/AI agents are writing concurrently.
WATCHED = ('undo.py', 'native_history.py', 'native_document_session.py',
           'document_metadata.py', 'model.py', 'commands.py', 'state.py',
           'narwhal_document.py', 'file_commands.py', 'import_commands.py')
EVIDENCE = {}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def initialize(phase):
    global undo, nh, Model, metadata, r3, app, commands, state, files, dialogs, nd, bridge
    sys.path.insert(0, str(ROOT))
    import rhino3dm as r3
    if phase == 'host':
        undo = load('history_integration_undo', ROOT / 'narwhal3d/undo.py')
        Model = load('history_integration_model', ROOT / 'narwhal3d/model.py').NarwhalModel
    else:
        import bpy
        from narwhal3d import undo, commands, state, file_commands as files
        from narwhal3d import filedialog as dialogs, narwhal_document as nd, bridge
        from narwhal3d.model import NarwhalModel as Model
        app = state.app_state
        EVIDENCE['blender'] = bpy.app.version_string
    probe = undo.UndoStack()
    assert probe.backend == 'rust', 'public UndoStack silently selected a non-Rust backend'
    nh = sys.modules['narwhal3d.native_history']
    probe.close()
    metadata = Model()._document_metadata.__class__.__module__
    metadata = sys.modules[metadata]


def history(stack):
    def rows(records):
        return tuple((id(r), r.serial, r.name, id(r.before), id(r.after)) for r in records)
    return (rows(stack._undo), rows(stack._redo), stack.state_id, stack.current_serial,
            stack.recording, stack.captured, stack._depth)


def geometry(model):
    # BREP Encode contains runtime/archive bookkeeping that changes on Duplicate.
    # Compare exact surface/edge NURBS and trim topology, never a display mesh.
    def shape(geom):
        if isinstance(geom, r3.Brep):
            return bridge.brep_data(geom)
        return geom.Encode()
    return {str(o.Attributes.Id): (o.Attributes.Name, shape(o.Geometry)) for o in model.file.Objects}


class Payload:
    def __deepcopy__(self, memo):
        raise AssertionError('history copied an opaque payload')


class HostTests(unittest.TestCase):
    def stack(self, limit=100):
        stack = undo.UndoStack(limit)
        self.assertEqual(stack.backend, 'rust')
        self.addCleanup(stack.close)
        return stack

    def commit(self, stack, value, name='edit'):
        stack.begin(name)
        self.assertTrue(stack.capture(value))
        self.assertTrue(stack.end())

    def test_public_facade_lazy_nested_disabled_and_noop(self):
        s = self.stack()
        start = s.state_id
        self.assertFalse(s.capture(Payload()))
        serial = s.begin('outer')
        record = s._open
        self.assertIsNone(record.before)
        self.assertFalse(s.captured)
        self.assertEqual(s.begin('inner'), serial)
        self.assertIs(s._open, record)
        s.enabled = False
        self.assertEqual(s.begin('disabled'), undo.NO_RECORD)
        self.assertEqual(s._depth, 2)
        self.assertFalse(s.end())
        self.assertFalse(s.end())
        self.assertEqual(s.state_id, start)
        self.assertFalse(s.recording)
        s.enabled = True
        payload = Payload()
        self.commit(s, payload)
        moved = s.undo(Payload())
        before = history(s)
        s.begin('cancelled')
        self.assertFalse(s.end())
        self.assertEqual(history(s), before, 'no-op invalidated redo')
        s.begin('abandoned')
        s.capture(Payload())
        s.abandon()
        self.assertEqual(history(s), before)
        self.assertIs(s.redo(Payload()), moved)

    def test_record_identity_and_native_rollback_setters(self):
        s = self.stack()
        s.begin('Sweep1')
        record = s._open
        first, replacement = Payload(), Payload()
        record.before = first
        self.assertTrue(s.captured, 'Record.before did not delegate into native capture')
        record.before = None
        self.assertFalse(s.captured, 'rollback did not clear native capture')
        record.before = replacement
        record.name = 'External change during Sweep1'
        self.assertIs(s._open, record)
        self.assertTrue(s.end(), 'Record.before did not delegate into native commit')
        self.assertEqual(s.undo_names(), ['External change during Sweep1'])
        identities = history(s)
        gc.collect()
        self.assertEqual(history(s), identities, 'record wrappers must remain stable')
        self.assertIs(s._undo[-1], record)
        seen = []
        self.assertIs(s.undo(first, restore=seen.append), record)
        self.assertEqual(seen, [replacement], 'native navigation ignored Record.before setter')
        self.assertIs(s._redo[-1], record)
        self.assertIs(s.redo(replacement), record)
        self.assertIs(record.before, replacement)
        self.assertIs(record.after, first)

    def test_record_identity_without_escaped_strong_references(self):
        s = self.stack()
        self.commit(s, Payload())
        ref = weakref.ref(s._undo[-1])
        gc.collect()
        self.assertIsNotNone(ref(), 'timeline lost its stable Record wrapper')
        self.assertIs(s._undo[-1], ref())
        s.undo(Payload())
        gc.collect()
        self.assertIs(s._redo[-1], ref())
        s.begin('open')
        opened = weakref.ref(s._open)
        gc.collect()
        self.assertIsNotNone(opened(), 'open record lost its stable wrapper')
        self.assertIs(s._open, opened())
        s.abandon()
        gc.collect()
        self.assertIsNone(opened())

    def test_payload_gc_pruning_redo_branch_and_close(self):
        s = self.stack(1)
        first = Payload()
        first_ref = weakref.ref(first)
        self.commit(s, first)
        leased = s._undo[-1]
        del first
        self.commit(s, Payload(), 'prune')
        gc.collect()
        self.assertIsNotNone(first_ref(), 'escaped Record must retain pruned payload')
        self.assertIs(leased.before, first_ref())
        del leased
        gc.collect()
        self.assertIsNone(first_ref(), 'pruned payload leaked after final Record lease')
        after = Payload()
        after_ref = weakref.ref(after)
        s.undo(after)
        del after
        self.commit(s, Payload(), 'branch')
        gc.collect()
        self.assertIsNone(after_ref(), 'invalidated redo payload leaked')
        pending = Payload()
        pending_ref = weakref.ref(pending)
        s.begin('abandon')
        s.capture(pending)
        del pending
        s.abandon()
        gc.collect()
        self.assertIsNone(pending_ref(), 'abandoned payload leaked')
        s.clear()
        gc.collect()
        self.assertEqual(len(s._arena), 0)
        last = Payload()
        last_ref = weakref.ref(last)
        self.commit(s, last)
        del last
        s.close()
        gc.collect()
        self.assertIsNone(last_ref(), 'closed history retained payload')

    def test_real_snapshot_metadata_gc_and_source_uuid(self):
        m = Model()
        self.assertEqual(m.metadata_backend, 'rust')
        source, _ = m.add_object(r3.LineCurve(r3.Point3d(1, 2, 3), r3.Point3d(7, 8, 9)), 'source')
        m.file.Strings['phase'] = 'before'
        expected = geometry(m)
        snap = m.snapshot()
        ref = weakref.ref(snap)
        s = self.stack(1)
        self.commit(s, snap)
        del snap
        m.file.Strings['phase'] = 'after'
        m.remove_object(source)
        current = m.snapshot()  # drop the model's copy-on-write base reference
        s.undo(current, restore=m.restore)
        self.assertEqual(geometry(m), expected, 'restored source UUID/geometry changed')
        self.assertEqual(m.document_user_text('phase'), 'before')
        s.redo(m.snapshot(), restore=m.restore)
        self.assertEqual(m.object_count(), 0)
        self.assertEqual(m.document_user_text('phase'), 'after')
        s.clear()
        m.snapshot()
        del current
        gc.collect()
        self.assertIsNone(ref(), 'real geometry/metadata snapshot leaked after history clear')

    def test_missing_default_legacy_state_identity_and_explicit_failure(self):
        with patch.object(undo, '_native_backend', return_value=None):
            s = undo.UndoStack()
        self.assertEqual(s.backend, 'legacy')
        start = s.state_id
        self.commit(s, Payload())
        saved = s.state_id
        s.clear()
        self.assertEqual(s.state_id, saved)
        self.assertNotEqual(start, saved)
        self.commit(s, Payload())
        edited = s.state_id
        s.undo(Payload())
        self.assertEqual(s.state_id, saved)
        s.redo(Payload())
        self.assertEqual(s.state_id, edited)
        with patch.dict(os.environ, {'NARWHAL_HISTORY_CORE': '/does/not/exist/history.so'}):
            with self.assertRaisesRegex(nh.HistoryError, 'cannot_load_history_core'):
                undo.UndoStack()


class Dialogs:
    def __init__(self, *, save=None, open_=None):
        self.save_path, self.open_path = save, open_

    def save(self, *args):
        if self.save_path is None:
            raise AssertionError('unexpected save dialog')
        return dialogs.OK, str(self.save_path)

    def open(self, *args):
        if self.open_path is None:
            raise AssertionError('unexpected open dialog')
        return dialogs.OK, str(self.open_path)

    def ask(self, *args):
        raise AssertionError('unexpected unsaved-changes dialog')


class BlenderTests(unittest.TestCase):
    def setUp(self):
        import bpy
        bridge.remove_display_objects(list(bpy.data.objects))
        for mesh in list(bpy.data.meshes):
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        self.tmp = tempfile.TemporaryDirectory(prefix='history-fixtures-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.m = Model()
        app.set_model(self.m)
        app.doc_path = None
        app.history.clear()
        app.mark_saved()
        self.ctx, self.s = app.ctx, app.ctx.undo
        self.assertEqual(self.s.backend, 'rust')
        self.assertEqual(self.m.metadata_backend, 'rust')
        self.addCleanup(dialogs.set_backend, None)
        self.addCleanup(self.s.close)

    def feed(self, *lines):
        for line in lines:
            app.feed_line(line)
        self.assertIsNone(app.active_command, repr(app.history[-6:]))
        return '\n'.join(app.history[-6:])

    def text(self, value):
        self.feed('SetDocumentUserText', 'phase', value)
        self.assertEqual(self.m.document_user_text('phase'), value)

    def save(self, name='saved.narwhal'):
        target = self.root / name
        dialogs.set_backend(Dialogs(save=target))
        self.feed('SaveAs')
        self.assertTrue(target.is_file(), repr(app.history[-6:]))
        self.assertEqual(app.doc_path, str(target))
        self.assertFalse(app.doc_modified())
        return target

    def test_actual_lazy_nested_disabled_noop_recording(self):
        with patch.object(self.m, 'snapshot', wraps=self.m.snapshot) as capture:
            serial = self.ctx.begin_undo('outer')
            self.assertEqual(self.ctx.begin_undo('nested'), serial)
            self.assertFalse(self.ctx.end_undo())
            self.assertFalse(self.ctx.end_undo())
            self.assertEqual(capture.call_count, 0, 'begin/end eagerly captured geometry')
            self.s.enabled = False
            self.assertFalse(self.ctx.note_change('disabled'))
            self.assertEqual(self.ctx.begin_undo('disabled'), 0)
            self.assertFalse(self.ctx.end_undo())
            self.assertEqual(capture.call_count, 0)
            self.s.enabled = True
        self.text('saved')
        self.text('edited')
        self.feed('Undo')
        before = history(self.s)
        self.text('saved')
        self.assertEqual(history(self.s), before, 'metadata no-op captured or invalidated redo')
        self.ctx.begin_undo('outer')
        self.text('nested metadata')
        self.assertTrue(self.s.recording)
        self.assertEqual(self.s.undo_names(), ['SetDocumentUserText'])
        self.assertTrue(self.ctx.end_undo())
        self.assertEqual(self.s.undo_names(), ['outer', 'SetDocumentUserText'])
        self.assertFalse(self.s.can_redo)

    def test_open_capture_is_dirty_before_commit_and_unchanged_abandon_is_clean(self):
        self.text('saved')
        app.mark_saved()
        saved_id = self.s.state_id
        original = history(self.s)
        for finish in (self.ctx.end_undo, self.s.abandon):
            self.ctx.begin_undo('empty record')
            self.assertFalse(self.s.captured)
            self.assertFalse(app.doc_modified(), 'uncaptured open record appeared dirty')
            finish()
            self.assertFalse(app.doc_modified(), 'empty record settlement appeared dirty')
            self.assertEqual(history(self.s), original)
        self.ctx.begin_undo('in-progress edit')
        self.assertFalse(app.doc_modified())
        self.assertIs(self.ctx.note_change(), True)
        self.assertTrue(self.s.captured)
        self.assertEqual(self.s.state_id, saved_id)
        self.assertEqual(self.m.document_user_text('phase'), 'saved')
        self.assertTrue(app.doc_modified(), 'captured open record falsely appeared saved before end')
        self.text('edited')
        self.assertTrue(self.s.recording)
        self.assertEqual(self.s.state_id, saved_id)
        self.assertTrue(app.doc_modified())
        self.assertTrue(self.ctx.end_undo())
        self.assertFalse(self.s.captured)
        self.assertNotEqual(self.s.state_id, saved_id)
        self.assertTrue(app.doc_modified(), 'committed mutation appeared saved')
        self.assertEqual(self.ctx.undo_last(), 'in-progress edit')
        self.assertEqual(self.m.document_user_text('phase'), 'saved')
        self.assertEqual(self.s.state_id, saved_id)
        self.assertFalse(app.doc_modified())
        restored = history(self.s)
        self.ctx.begin_undo('capture without mutation')
        self.assertFalse(app.doc_modified())
        self.assertIs(self.ctx.note_change(), True)
        self.assertTrue(app.doc_modified())
        self.s.abandon()
        self.assertFalse(self.s.recording)
        self.assertFalse(self.s.captured)
        self.assertEqual(self.m.document_user_text('phase'), 'saved')
        self.assertEqual(history(self.s), restored, 'unchanged abandon altered saved history or Redo')
        self.assertFalse(app.doc_modified(), 'unchanged capture abandonment remained dirty')

    def test_repeated_note_change_skips_snapshot_and_restores_first_capture(self):
        self.text('before')
        self.feed('Line', 'w1,2,3', 'w4,6,3')
        self.s.clear()
        app.mark_saved()
        original = geometry(self.m)
        saved_id = self.s.state_id
        with patch.object(self.m, 'snapshot', wraps=self.m.snapshot) as snapshot:
            self.ctx.begin_undo('capture once')
            self.assertEqual(snapshot.call_count, 0)
            self.assertIs(self.ctx.note_change(), True)
            snapshot.assert_called_once_with()
            first = self.s._open.before
            self.assertIsNotNone(first)
            self.assertTrue(self.s.captured)
            for _ in range(3):
                self.assertIs(self.ctx.note_change('already captured'), False)
                self.assertEqual(snapshot.call_count, 1,
                                 'repeated note_change took another model snapshot')
                self.assertIs(self.s._open.before, first)
            self.text('edited')
            self.feed('Line', 'w10,20,30', 'w40,60,30')
            self.assertEqual(self.m.object_count(), 2)
            self.assertIs(self.ctx.note_change('after actual edits'), False)
            self.assertEqual(snapshot.call_count, 1, 'mutation hooks recaptured the open record')
            self.assertTrue(self.ctx.end_undo())
            self.assertEqual(snapshot.call_count, 1)
            self.assertIs(self.s._undo[-1].before, first)
        edited = geometry(self.m)
        self.assertEqual(self.s.undo_names(), ['capture once'])
        with patch.object(self.m, 'restore', wraps=self.m.restore) as restore:
            self.assertEqual(self.ctx.undo_last(), 'capture once')
            self.assertEqual(restore.call_count, 1)
            self.assertIs(restore.call_args.args[0], first, 'Undo did not restore the first captured snapshot')
        self.assertEqual(geometry(self.m), original)
        self.assertEqual(self.m.document_user_text('phase'), 'before')
        self.assertEqual(self.s.state_id, saved_id)
        self.assertFalse(app.doc_modified())
        self.assertEqual(self.ctx.redo_last(), 'capture once')
        self.assertEqual(geometry(self.m), edited)
        self.assertEqual(self.m.document_user_text('phase'), 'edited')

    def test_box_metadata_single_history_undo_redo_saved_uuid(self):
        self.text('saved')
        target = self.save()
        saved_bytes = target.read_bytes()
        saved_id = app._saved_history_state
        self.ctx.begin_undo('box plus metadata')
        self.text('changed')
        self.feed('Box', 'w0,0,0', 'w2,3,0', '4')
        self.assertTrue(self.ctx.end_undo())
        self.assertEqual(self.m.object_count(), 1, repr(app.history[-6:]))
        obj = self.m.file.Objects[0]
        self.assertEqual(len(obj.Geometry.Faces), 6)
        self.assertTrue(obj.Geometry.IsSolid)
        result = geometry(self.m)
        record = self.s._undo[-1]
        self.assertEqual(self.s.undo_names(), ['box plus metadata', 'SetDocumentUserText'])
        self.assertTrue(app.doc_modified())
        self.feed('Undo')
        self.assertEqual(self.m.object_count(), 0)
        self.assertEqual(self.m.document_user_text('phase'), 'saved')
        self.assertEqual(self.s.redo_names(), ['box plus metadata'], 'navigation did not commit redo membership')
        self.assertIs(self.s._redo[-1], record)
        self.assertEqual(self.s.state_id, saved_id, 'Undo did not commit saved state identity')
        self.assertFalse(app.doc_modified())
        self.feed('Redo')
        self.assertEqual(geometry(self.m), result, 'Box UUID/exact geometry changed on Redo')
        self.assertEqual(self.m.document_user_text('phase'), 'changed')
        self.assertIs(self.s._undo[-1], record)
        self.assertTrue(app.doc_modified())
        self.assertEqual(target.read_bytes(), saved_bytes)

    def test_clear_and_pruning_preserve_saved_content_identity(self):
        self.s.close()
        self.ctx.undo = self.s = undo.UndoStack(1)
        self.addCleanup(self.s.close)
        app.mark_saved()
        self.text('saved')
        self.save()
        saved = self.s.state_id
        self.s.clear()
        self.assertEqual(self.s.top_serial, 0)
        self.assertEqual(self.s.state_id, saved, 'clear reset saved content identity')
        self.assertFalse(app.doc_modified(), 'clear turned saved content dirty')
        self.text('edit')
        edited = self.s.state_id
        self.assertTrue(app.doc_modified())
        self.feed('Undo')
        self.assertEqual(self.s.state_id, saved)
        self.assertFalse(app.doc_modified(), 'Undo to cleared saved history became dirty')
        self.feed('Redo')
        self.assertEqual(self.s.state_id, edited)
        self.s.clear()
        self.assertTrue(app.doc_modified(), 'clear falsely marked dirty content saved')
        self.assertEqual(self.s.state_id, edited)
        self.text('pruned edit')
        self.text('latest')
        self.assertEqual(len(self.s._undo), 1)
        self.feed('Undo')
        self.assertEqual(self.m.document_user_text('phase'), 'pruned edit')
        self.assertTrue(app.doc_modified(), 'pruned stack position was mistaken for saved content')
        self.assertEqual(app._saved_history_state, saved)

    def preflight(self, direction):
        self.text('saved')
        self.save()
        self.ctx.begin_undo('metadata and layers')
        self.m.file.Strings['phase'] = 'edited'
        self.m.add_layer('edited layer')
        self.assertTrue(self.ctx.end_undo())
        if direction == 'redo':
            self.ctx.undo_last()
        before = history(self.s)
        saved = (app._saved_history_state, app._saved_serial, app.doc_path, app.doc_modified())
        model_before = (self.m.file, self.m.layer_records(), geometry(self.m), self.m.document_user_text())
        raw = metadata._raw(self.m.file)
        raw['escaped'] = 'unmediated'
        try:
            with patch.object(self.ctx, '_restore_document', wraps=self.ctx._restore_document) as restore:
                with self.assertRaisesRegex(metadata.MetadataError, 'unmediated_native_metadata_write'):
                    getattr(self.ctx, direction + '_last')()
                self.assertEqual(restore.call_count, 0, 'preflight entered mutating restore')
            self.assertEqual(history(self.s), before, 'preflight advanced history')
            self.assertFalse(self.s.uncertain, 'pure preflight rejection poisoned retryable history')
            self.assertEqual((app._saved_history_state, app._saved_serial, app.doc_path, app.doc_modified()), saved)
            self.assertIs(self.m.file, model_before[0])
            self.assertEqual(self.m.layer_records(), model_before[1])
            self.assertEqual(geometry(self.m), model_before[2])
            self.assertEqual(self.m.document_user_text(), model_before[3])
            self.assertEqual(raw['escaped'], 'unmediated')
        finally:
            raw.Delete('escaped')
        self.assertEqual(getattr(self.ctx, direction + '_last')(), 'metadata and layers')
        self.assertEqual(self.m.document_user_text('phase'), 'saved' if direction == 'undo' else 'edited')
        self.assertEqual(app.doc_modified(), direction == 'redo')

    def test_failed_metadata_projection_undo_preflight_retryable(self):
        self.preflight('undo')

    def test_failed_metadata_projection_redo_preflight_retryable(self):
        self.preflight('redo')

    def failed_restore(self, display=False):
        self.text('before')
        self.text('saved')
        self.save()
        before = history(self.s)
        saved = app._saved_history_state
        error = RuntimeError('injected display failure' if display else 'injected partial model restore')
        if display:
            fault = patch.object(bridge, 'refresh_display_objects', side_effect=error)
        else:
            def partial(snapshot):
                self.m._document_metadata.restore(snapshot.strings)
                raise error
            fault = patch.object(self.m, 'restore', partial)
        with fault:
            with self.assertRaises(RuntimeError) as caught:
                self.ctx.undo_last()
        self.assertIs(caught.exception, error)
        self.assertEqual(self.m.document_user_text('phase'), 'before', 'fault must follow actual mutation')
        self.assertEqual(history(self.s), before, 'failed restore advanced stack/state')
        self.assertEqual(app._saved_history_state, saved)
        self.assertTrue(self.s.uncertain, 'post-restore exception did not poison history')
        self.assertTrue(app.doc_modified(), 'partial restore falsely appears saved')
        for action in (lambda: self.ctx.begin_undo('blocked'), self.ctx.undo_last, self.ctx.redo_last):
            with self.assertRaisesRegex(nh.HistoryError, 'history_uncertain'):
                action()
        self.s.clear()
        self.assertFalse(self.s.uncertain)
        self.assertNotEqual(self.s.state_id, saved)
        self.assertTrue(app.doc_modified())
        self.text('recovered')
        self.feed('Undo')
        self.assertEqual(self.m.document_user_text('phase'), 'before')
        app.set_model(Model())
        self.assertEqual(app.ctx.undo.backend, 'rust')
        self.assertFalse(app.ctx.undo.uncertain)
        self.assertFalse(app.doc_modified())
        app.ctx.undo.close()

    def test_partial_model_restore_marks_uncertain_dirty_and_blocks(self):
        self.failed_restore()

    def test_display_exception_marks_uncertain_dirty_and_blocks(self):
        self.failed_restore(display=True)

    def unknown_restore_fixture(self):
        self.text('before')
        self.text('saved')
        target = self.save()
        before = history(self.s)
        error = RuntimeError('partial restore before acknowledgement')
        def partial(snapshot):
            self.m._document_metadata.restore(snapshot.strings)
            raise error
        with patch.object(self.m, 'restore', partial):
            with self.assertRaises(RuntimeError) as caught:
                self.ctx.undo_last()
        self.assertIs(caught.exception, error)
        self.assertEqual(self.m.document_user_text('phase'), 'before')
        self.assertEqual(history(self.s), before)
        self.assertIsNone(app._saved_uncertain_generation)
        self.assertTrue(self.s.uncertain)
        self.assertTrue(app.doc_modified(), 'unknown contents were acknowledged without a save')
        return target

    def assert_acknowledged_but_blocked(self):
        self.assertTrue(self.s.uncertain, 'acknowledgement unlocked uncertain history')
        self.assertFalse(self.s.can_undo, 'uncertain history advertised Undo')
        self.assertFalse(self.s.can_redo, 'uncertain history advertised Redo')
        self.assertEqual(app._saved_uncertain_generation, self.s.generation,
                         'save did not acknowledge the uncertainty generation')
        self.assertFalse(app.doc_modified(), 'explicit acknowledgement left current contents dirty')
        before = history(self.s)
        generation = self.s.generation
        for action in (lambda: self.ctx.begin_undo('blocked'), self.ctx.undo_last, self.ctx.redo_last):
            with self.assertRaisesRegex(nh.HistoryError, 'history_uncertain'):
                action()
        self.assertEqual(history(self.s), before)
        self.assertEqual(self.s.generation, generation, 'refused navigation invalidated the acknowledgement')
        self.assertFalse(app.doc_modified())

    def assert_clear_requires_fresh_acknowledgement(self):
        acknowledged = app._saved_uncertain_generation
        previous = self.s.state_id
        self.s.clear()
        self.assertFalse(self.s.uncertain)
        self.assertNotEqual(self.s.state_id, previous, 'clear reused an uncertain content identity')
        self.assertTrue(app.doc_modified(), 'clear reused the uncertain saved acknowledgement')
        self.assertEqual(app._saved_uncertain_generation, acknowledged)
        app.mark_saved()
        self.assertIsNone(app._saved_uncertain_generation)
        self.assertFalse(app.doc_modified())
        self.text('after clear')
        self.assertTrue(app.doc_modified())
        self.feed('Undo')
        self.assertEqual(self.m.document_user_text('phase'), 'before')
        self.assertFalse(app.doc_modified())

    def test_mark_saved_acknowledges_unknown_contents_without_unlocking_history(self):
        self.unknown_restore_fixture()
        before = history(self.s)
        generation = self.s.generation
        app.mark_saved()
        self.assertEqual(history(self.s), before, 'mark_saved changed the history timeline')
        self.assertEqual(self.s.generation, generation)
        self.assert_acknowledged_but_blocked()
        self.assert_clear_requires_fresh_acknowledgement()

    def test_successful_save_acknowledges_unknown_contents_without_unlocking_history(self):
        previous = self.unknown_restore_fixture()
        original_bytes = previous.read_bytes()
        before = history(self.s)
        target = self.save('acknowledged.narwhal')
        loaded = nd.load_model(target)
        self.assertEqual(loaded.document_user_text('phase'), 'before', 'save wrote pre-failure contents')
        self.assertEqual(previous.read_bytes(), original_bytes)
        self.assertEqual(history(self.s), before, 'file save changed uncertain history')
        self.assert_acknowledged_but_blocked()
        self.assert_clear_requires_fresh_acknowledgement()

    def test_settled_exit_acknowledges_unknown_contents_without_unlocking_history(self):
        self.unknown_restore_fixture()
        # Exercise the registered Exit, but replace only the OS quit callback.
        observed = []
        def quitting():
            observed.append((app.doc_modified(), self.s.uncertain, app._saved_uncertain_generation))
        with patch.object(dialogs, 'ask_yes_no_cancel', return_value=(dialogs.OK, 'no')) as ask:
            with patch.object(files, 'quitter', quitting):
                message, done = commands.COMMANDS['exit'].begin(self.ctx)
        self.assertTrue(done)
        self.assertEqual(message, 'Exiting')
        ask.assert_called_once()
        self.assertEqual(observed, [(False, True, self.s.generation)],
                         'settled Exit did not acknowledge before invoking quit')
        self.assert_acknowledged_but_blocked()

    def acknowledged_replacement_failure(self, direction):
        source = self.unknown_restore_fixture()
        app.mark_saved()
        self.assert_acknowledged_but_blocked()
        fields = ('model', 'ctx', 'layers', '_saved_serial', '_saved_history_state',
                  '_saved_uncertain_generation', 'doc_path')
        old = {name: getattr(app, name) for name in fields}
        before = history(self.s)
        rows = self.m.document_user_text()
        candidates = []
        def fail_after_reset(*args):
            self.assertIsNone(app._saved_uncertain_generation,
                              'failure must happen AFTER the candidate resets the acknowledgement')
            candidates.append(app.ctx)
            raise RuntimeError('late acknowledged ' + direction + ' publication failure')
        if direction == 'Open':
            real = app.mark_saved
            def fail_after_saved(path=None):
                real(path)
                fail_after_reset()
            dialogs.set_backend(Dialogs(open_=source))
            with patch.object(app, 'mark_saved', fail_after_saved):
                message, done = commands.COMMANDS['open'].begin(self.ctx)
            self.assertTrue(done)
            self.assertIn('late acknowledged Open publication failure', message)
        else:
            # set_model retires the automation host only after every candidate
            # field is published. A late host-close error exercises full rollback,
            # unlike a layer failure that precedes the acknowledgement reset.
            name = state.__package__ + '.automation_host'
            with patch.dict(sys.modules, {name: SimpleNamespace(close=fail_after_reset)}):
                with self.assertRaisesRegex(RuntimeError, 'late acknowledged New publication failure'):
                    commands.COMMANDS['new'].begin(self.ctx)
        for candidate in candidates:
            self.addCleanup(candidate.undo.close)
        self.assertEqual(len(candidates), 1)
        for field, value in old.items():
            self.assertEqual(getattr(app, field), value, direction + ' rollback lost ' + field)
        self.assertIs(app.model, self.m)
        self.assertIs(app.ctx, self.ctx)
        self.assertIs(app.layers, old['layers'])
        self.assertEqual(history(self.s), before)
        self.assertEqual(self.m.document_user_text(), rows)
        self.assert_acknowledged_but_blocked()

    def test_failed_open_restores_saved_uncertain_generation(self):
        self.acknowledged_replacement_failure('Open')

    def test_failed_new_restores_saved_uncertain_generation(self):
        self.acknowledged_replacement_failure('New')

    def test_broken_and_closed_history_are_dirty_despite_saved_acknowledgement(self):
        self.unknown_restore_fixture()
        app.mark_saved()
        self.assert_acknowledged_but_blocked()
        acknowledgement = app._saved_uncertain_generation
        backend = self.s._backend
        with patch.object(nh, '_call', side_effect=nh.HistoryProtocolError('injected invalid reply')):
            with self.assertRaisesRegex(nh.HistoryProtocolError, 'injected invalid reply'):
                backend._request('info')
        for closed in (False, True):
            with self.subTest(closed=closed):
                if closed:
                    self.s.close()
                self.assertTrue(self.s.uncertain)
                self.assertFalse(self.s.can_undo)
                self.assertFalse(self.s.can_redo)
                self.assertTrue(app.doc_modified(), 'broken transport certified acknowledged contents as saved')
                self.assertEqual(app._saved_uncertain_generation, acknowledgement)
                for action in (lambda: self.ctx.begin_undo('blocked'), self.ctx.undo_last, self.ctx.redo_last):
                    with self.assertRaisesRegex(RuntimeError, 'history_closed' if closed else 'history_protocol_failed'):
                        action()

    def test_restore_callback_reentrance_begin_clear_undo_redo_refused(self):
        self.text('before')
        self.text('after')
        actual = self.m.restore
        seen = []
        def callback(snapshot):
            for name, action in (
                    ('begin', lambda: self.ctx.begin_undo('reentrant')), ('clear', self.s.clear),
                    ('undo', self.ctx.undo_last), ('redo', self.ctx.redo_last)):
                with self.assertRaisesRegex(nh.HistoryError, 'navigation_busy', msg='reentrant ' + name):
                    action()
                seen.append(name)
            return actual(snapshot)
        with patch.object(self.m, 'restore', callback):
            self.assertEqual(self.ctx.undo_last(), 'SetDocumentUserText')
        self.assertEqual(seen, ['begin', 'clear', 'undo', 'redo'])
        self.assertFalse(self.s.uncertain)
        self.assertEqual(self.m.document_user_text('phase'), 'before')
        self.assertEqual(len(self.s._undo), 1)
        self.assertEqual(len(self.s._redo), 1)
        self.feed('Redo')
        self.assertEqual(self.m.document_user_text('phase'), 'after')

    def test_internal_file_roundtrip_keeps_native_history_and_metadata_handles(self):
        self.text('saved')
        self.feed('Line', 'w1,2,3', 'w4,6,3')
        self.save()
        self.text('edited')
        before = history(self.s)
        session = self.m._document_metadata.session
        sources = geometry(self.m)
        old_file = self.m.file
        self.m._round_trip()
        self.assertIsNot(self.m.file, old_file)
        self.assertIs(app.ctx, self.ctx)
        self.assertIs(self.m._document_metadata.session, session)
        self.assertEqual(history(self.s), before)
        self.assertEqual(geometry(self.m), sources)
        self.feed('Undo')
        self.assertEqual(self.m.document_user_text('phase'), 'saved')
        self.assertFalse(app.doc_modified())
        self.feed('Redo')
        self.assertEqual(self.m.document_user_text('phase'), 'edited')
        self.assertEqual(geometry(self.m), sources)

    def replacement_fixture(self):
        self.text('saved')
        source = self.save('candidate.narwhal')
        self.text('redo candidate')
        self.feed('Undo')
        # Make content identity and compatibility serial DISTINCT before rollback.
        self.ctx.begin_undo('no-op creates a serial gap')
        self.ctx.end_undo()
        self.text('saved after gap')
        self.save('old.narwhal')
        self.text('redo after gap')
        self.feed('Undo')
        self.assertNotEqual(app._saved_serial, app._saved_history_state)
        return source

    def test_native_open_commit_failure_restores_old_context_saved_state_history(self):
        source = self.replacement_fixture()
        old = {name: getattr(app, name) for name in (
            'model', 'ctx', 'layers', '_saved_serial', '_saved_history_state',
            '_saved_uncertain_generation', 'doc_path')}
        before = history(self.s)
        rows = self.m.document_user_text()
        real = app.mark_saved
        def fail_after_saved(path=None):
            real(path)
            raise RuntimeError('late native commit failure')
        dialogs.set_backend(Dialogs(open_=source))
        with patch.object(app, 'mark_saved', fail_after_saved):
            message, done = commands.COMMANDS['open'].begin(self.ctx)
        self.assertTrue(done)
        self.assertIn('late native commit failure', message)
        for field, value in old.items():
            self.assertEqual(getattr(app, field), value, 'rollback lost ' + field)
        self.assertIs(app.ctx, self.ctx)
        self.assertEqual(history(self.s), before, 'failed Open closed/replaced old history')
        self.assertEqual(self.m.document_user_text(), rows)
        self.assertFalse(app.doc_modified(), 'rollback lost saved-state identity')
        self.feed('Redo')
        self.assertEqual(self.m.document_user_text('phase'), 'redo after gap')
        self.feed('Undo')
        self.assertFalse(app.doc_modified())

    def test_new_commit_failure_restores_old_context_saved_state_history(self):
        self.replacement_fixture()
        before = history(self.s)
        saved = (app._saved_serial, app._saved_history_state, app._saved_uncertain_generation, app.doc_path)
        real = app._rebuild_layers
        def fail_after_layers():
            real()
            raise RuntimeError('late New layer publication failure')
        with patch.object(app, '_rebuild_layers', fail_after_layers):
            with self.assertRaisesRegex(RuntimeError, 'late New layer publication failure'):
                commands.COMMANDS['new'].begin(self.ctx)
        self.assertIs(app.ctx, self.ctx, 'failed New replaced old context instead of rolling back')
        self.assertIs(app.model, self.m)
        self.assertEqual((app._saved_serial, app._saved_history_state, app._saved_uncertain_generation, app.doc_path), saved)
        self.assertEqual(history(self.s), before)
        self.assertFalse(app.doc_modified())
        self.feed('Redo')
        self.assertEqual(self.m.document_user_text('phase'), 'redo after gap')

    def test_successful_new_open_fresh_history_old_context_not_eagerly_closed(self):
        source = self.replacement_fixture()
        previous = history(self.s)
        self.feed('New')
        self.assertEqual(app.model.object_count(), 0)
        self.assertEqual(app.ctx.undo.backend, 'rust')
        self.assertEqual(app.ctx.undo.undo_names(), [])
        self.assertFalse(app.doc_modified())
        self.assertEqual(history(self.s), previous, 'set_model eagerly closed an escaped old context')
        dialogs.set_backend(Dialogs(open_=source))
        self.feed('Open')
        self.assertEqual(app.model.document_user_text('phase'), 'saved')
        self.assertEqual(app.ctx.undo.backend, 'rust')
        self.assertEqual(app.ctx.undo.undo_names(), [], 'process-local history was serialized')
        self.assertFalse(app.doc_modified())
        self.assertEqual(history(self.s), previous)
        app.ctx.undo.close()

    def sweep_fixture(self):
        # Exact rational arc and three rational profiles; same public source-input
        # contract as the existing Sweep1 integration fixture, without importing its
        # tests or executing any other agent's harness.
        rail = bridge.curve_from_recipe(dict(ctype='nurbs', degree=2,
            poles=[[10, 0, 0], [10, 10, 0], [0, 10, 0]],
            weights=[1, math.sqrt(.5), 1], knots=[-2, -2, 7, 7]))
        curves = [rail]
        for fraction, radius in zip((0, .27, 1), (1, 1.4, .8)):
            p = rail.PointAt(rail.Domain.T0 + (rail.Domain.T1 - rail.Domain.T0) * fraction)
            a = [p.X, p.Y, p.Z]
            x, y = [p.X / 10, p.Y / 10, 0], [0, 0, 1]
            curves.append(bridge.curve_from_recipe(dict(ctype='nurbs', degree=2,
                poles=[a, [a[k] + radius*x[k] for k in range(3)],
                       [a[k] + radius*(x[k]+y[k]) for k in range(3)]],
                weights=[1, math.sqrt(.5), 1], knots=[-9, -9, -3, -3])))
        for i, curve in enumerate(curves):
            self.m.add_object(curve, 'source' + str(i))
        self.ctx.rebuild_display()
        self.s.clear()
        app.mark_saved()
        return geometry(self.m)

    def enter_sweep(self):
        for line in ('Sweep1', 'source0', 'source1', 'source2', 'source3'):
            app.feed_line(line)
        self.assertEqual(app.active_command.name, 'Sweep1')

    def test_actual_sweep_publication_source_uuid_and_undo_redo(self):
        before = self.sweep_fixture()
        self.enter_sweep()
        message = self.feed('')
        self.assertIn('Created Sweep1', message)
        self.assertEqual(self.m.object_count(), 5)
        after = geometry(self.m)
        self.assertEqual({k: after[k] for k in before}, before, 'Sweep modified source geometry/UUIDs')
        self.assertEqual(self.s.undo_names(), ['Sweep1'])
        self.feed('Undo')
        self.assertEqual(geometry(self.m), before)
        self.assertFalse(app.doc_modified())
        self.feed('Redo')
        self.assertEqual(geometry(self.m), after, 'Sweep source/result UUIDs changed on Redo')
        self.assertTrue(app.doc_modified())

    def test_sweep_display_failure_external_edit_uses_record_before_name_setters(self):
        before = self.sweep_fixture()
        self.enter_sweep()
        real = self.ctx._make_display_object
        def external_then_fail(*args, **kwargs):
            real(*args, **kwargs)
            self.m.file.Strings['external'] = 'surviving edit'
            raise RuntimeError('injected Sweep display failure')
        with patch.object(self.ctx, '_make_display_object', external_then_fail):
            self.assertIn('failed', self.feed(''))
        self.assertEqual(geometry(self.m), before)
        self.assertEqual(self.m.document_user_text('external'), 'surviving edit')
        self.assertEqual(self.s.undo_names(), ['External change during Sweep1'],
                         'rollback setters failed to publish surviving external edit')
        self.assertTrue(app.doc_modified())
        self.feed('Undo')
        self.assertIsNone(self.m.document_user_text('external'))
        self.assertEqual(geometry(self.m), before)
        self.assertFalse(app.doc_modified())
        self.feed('Redo')
        self.assertEqual(self.m.document_user_text('external'), 'surviving edit')
        self.assertEqual(geometry(self.m), before)


def method_mutation(owner, name, old, new):
    function = getattr(owner, name)
    source = textwrap.dedent(inspect.getsource(function))
    if source.count(old) != 1:
        raise AssertionError('mutation anchor changed: ' + name)
    scope = dict(function.__globals__)
    exec(compile(source.replace(old, new, 1), '<history mutation ' + name + '>', 'exec'), scope)
    return patch.object(owner, name, scope[name])


def mutation_checks(phase, selected_test=None):
    cases = []
    if phase == 'host':
        record = nh.NativeRecord
        cases.append(('record_before_not_delegated',
            patch.object(record, 'before', property(record.before.fget, lambda self, value: None)),
            HostTests, 'test_record_identity_and_native_rollback_setters', 'Record.before did not delegate'))
        cases.append(('record_strong_cache_removed', method_mutation(nh.NativeUndoStack, '_view',
            '    self._cache[record] = view\n', ''),
            HostTests, 'test_record_identity_without_escaped_strong_references', 'timeline lost its stable Record wrapper'))
        cases.append(('payload_gc_drain_removed', patch.object(nh.NativeUndoStack, '_drain', lambda self: None),
            HostTests, 'test_payload_gc_pruning_redo_branch_and_close', 'pruned payload leaked'))
    else:
        cases.extend([
            ('open_captured_dirty_predicate_removed', method_mutation(state.AppState, 'doc_modified',
                'self.ctx.undo.captured\n            or ', ''),
             BlenderTests, 'test_open_capture_is_dirty_before_commit_and_unchanged_abandon_is_clean',
             'captured open record falsely appeared saved before end'),
            ('repeated_note_change_snapshot_guard_removed', method_mutation(commands.NarwhalContext, 'note_change',
                '    if self.undo.recording and self.undo.captured:\n        return False\n', ''),
             BlenderTests, 'test_repeated_note_change_skips_snapshot_and_restores_first_capture',
             'repeated note_change took another model snapshot'),
            ('navigation_commit_skipped', method_mutation(nh.NativeUndoStack, '_navigate',
                'self._request("commit_nav", ticket=nav["ticket"])',
                'self._request("abort_nav", ticket=nav["ticket"], unknown=False)'),
             BlenderTests, 'test_box_metadata_single_history_undo_redo_saved_uuid', 'navigation did not commit redo membership'),
            ('dirty_compares_compat_serial', patch.object(state.AppState, 'doc_modified',
                lambda self: self.ctx.undo.uncertain or self.ctx.undo.top_serial != self._saved_serial),
             BlenderTests, 'test_clear_and_pruning_preserve_saved_content_identity', 'clear turned saved content dirty'),
            ('post_restore_classified_unchanged', method_mutation(commands.NarwhalContext, '_navigate_history',
                "'unknown' if started else 'unchanged'", "'unchanged'"),
             BlenderTests, 'test_partial_model_restore_marks_uncertain_dirty_and_blocks', 'did not poison history'),
            ('preflight_classified_unknown', method_mutation(commands.NarwhalContext, '_navigate_history',
                "'unknown' if started else 'unchanged'", "'unknown'"),
             BlenderTests, 'test_failed_metadata_projection_undo_preflight_retryable', 'poisoned retryable history'),
            ('pure_admission_skipped', method_mutation(commands.NarwhalContext, '_navigate_history',
                '        self.model.validate_restore(snapshot)\n', ''),
             BlenderTests, 'test_failed_metadata_projection_undo_preflight_retryable', 'preflight entered mutating restore'),
            ('native_commit_saved_identity_not_restored', method_mutation(nd, 'commit_model',
                '"_saved_history_state", ', ''),
             BlenderTests, 'test_native_open_commit_failure_restores_old_context_saved_state_history', 'rollback lost _saved_history_state'),
            ('saved_uncertainty_acknowledgement_removed', method_mutation(state.AppState, 'mark_saved',
                'self._saved_uncertain_generation = self.ctx.undo.generation if self.ctx.undo.uncertain else None',
                'self._saved_uncertain_generation = None'),
             BlenderTests, 'test_mark_saved_acknowledges_unknown_contents_without_unlocking_history',
             'save did not acknowledge the uncertainty generation'),
            ('new_saved_acknowledgement_not_restored', method_mutation(state.AppState, 'set_model',
                ", '_saved_uncertain_generation')", ')'),
             BlenderTests, 'test_failed_new_restores_saved_uncertain_generation',
             'New rollback lost _saved_uncertain_generation'),
            ('open_saved_acknowledgement_not_restored', method_mutation(nd, 'commit_model',
                '"_saved_uncertain_generation", ', ''),
             BlenderTests, 'test_failed_open_restores_saved_uncertain_generation',
             'Open rollback lost _saved_uncertain_generation'),
            ('new_publication_rollback_removed', method_mutation(state.AppState, 'set_model',
                '        for name, value in previous.items():\n            setattr(self, name, value)\n', ''),
             BlenderTests, 'test_new_commit_failure_restores_old_context_saved_state_history',
             'failed New replaced old context'),
            ('broken_transport_falsely_clean', method_mutation(state.AppState, 'doc_modified',
                'return True  # Broken transport cannot certify a saved state.', 'return False'),
             BlenderTests, 'test_broken_and_closed_history_are_dirty_despite_saved_acknowledgement',
             'broken transport certified acknowledged contents as saved'),
            ('sweep_record_name_setter_ignored', patch.object(nh.NativeRecord, 'name',
                property(nh.NativeRecord.name.fget, lambda self, value: None)),
             BlenderTests, 'test_sweep_display_failure_external_edit_uses_record_before_name_setters', 'rollback setters failed'),
        ])
    receipts = []
    for name, mutation, cls, test, expected in cases:
        if selected_test is not None and test != selected_test:
            continue
        baseline = unittest.TextTestRunner(verbosity=0).run(cls(test))
        if not baseline.wasSuccessful():
            receipts.append(dict(name=name, killed=False, restored_green=False, reason='baseline failed'))
            continue
        output = io.StringIO()
        with mutation:
            result = unittest.TextTestRunner(stream=output, verbosity=2).run(cls(test))
        text = output.getvalue()
        killed = bool(result.failures) and not result.errors and expected in text
        restored = unittest.TextTestRunner(verbosity=0).run(cls(test))
        receipts.append(dict(name=name, killed=killed, restored_green=restored.wasSuccessful(), output=text))
        print('MUTATION', name, 'RED' if killed else 'NOT KILLED', 'restored', restored.wasSuccessful(), flush=True)
    return receipts


def cgroup():
    path = Path('/sys/fs/cgroup') / Path('/proc/self/cgroup').read_text().strip().split('::', 1)[1].lstrip('/')
    limits = {key: (path / key).read_text().strip() for key in ('memory.max', 'memory.swap.max')}
    if limits != {'memory.max': str(2 * 1024**3), 'memory.swap.max': '0'}:
        raise RuntimeError('refusing unbounded worker: ' + repr(limits))
    return path, limits


def worker(args):
    group, limits = cgroup()
    initialize(args.worker)
    cls = HostTests if args.worker == 'host' else BlenderTests
    suite = (unittest.TestSuite([cls(args.test)]) if args.test else unittest.defaultTestLoader.loadTestsFromTestCase(cls))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    mutants = mutation_checks(args.worker, args.test) if args.mutations else []
    ok = result.wasSuccessful() and all(m['killed'] and m['restored_green'] for m in mutants)
    report = dict(ok=ok, phase=args.worker, tests=result.testsRun, failures=result.failures,
                  errors=result.errors, mutations=mutants, evidence=EVIDENCE,
                  cgroup=dict(path=str(group), **limits,
                              peak=(group / 'memory.peak').read_text().strip()))
    args.report.write_text(json.dumps(report, indent=2, default=str))
    print('HISTORY_INTEGRATION', args.report, 'PASS' if ok else 'FAIL', flush=True)
    return int(not ok)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def launch(args):
    import fcntl
    out = Path(tempfile.mkdtemp(prefix='narwhal-history-integration-'))
    core = Path(os.environ.get('NARWHAL_APPLICATION_CORE', CORE)).expanduser().resolve(strict=True)
    brep = Path(os.environ.get('NARWHAL3D_BREP_WRITER',
        ROOT / 'artifacts/brep-writer/linux-x86_64/brep_writer-linux-x86_64')).resolve(strict=True)
    watched = [ROOT / 'narwhal3d' / name for name in WATCHED]
    watched += list((ROOT / 'native/application_core/src').rglob('*.rs')) + [SELF, core]
    before = {str(p): digest(p) for p in watched}
    env = dict(os.environ)
    for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'TMPDIR'):
        folder = out / key.lower()
        folder.mkdir()
        env[key] = str(folder)
    env.update(NARWHAL_APPLICATION_CORE=str(core), NARWHAL_HISTORY_CORE=str(core),
               NARWHAL_GEOMETRY_CORE=str((ROOT / 'native/geometry_core/target/release/libnarwhal_geometry_core.so').resolve(strict=True)),
               NARWHAL3D_BREP_WRITER=str(brep), PYTHONDONTWRITEBYTECODE='1',
               PYTHONPYCACHEPREFIX=str(out / 'bytecode'), PYTHONPATH=str(ROOT / 'vendor/occt'),
               NARWHAL3D_OPEN_WORKERS='0', NARWHAL3D_SETTINGS_FILE=str(out / 'settings.json'),
               OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', LP_NUM_THREADS='1')
    runtime = ROOT / 'tools/blender-patched'
    env['LD_LIBRARY_PATH'] = str(runtime / '5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs')
    phases = ('host', 'blender') if args.phase == 'all' else (args.phase,)
    statuses = []
    print('HISTORY_RECEIPTS', out, flush=True)
    for phase in phases:
        with ExitStack() as stack:
            if phase == 'blender':
                lock = stack.enter_context(open('/tmp/narwhal3d_gui.lock', 'a'))
                deadline = time.monotonic() + 90
                while True:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError('GUI lock busy; no Blender launched')
                        time.sleep(.25)
            available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                                 if line.startswith('MemAvailable:')))
            if available < 6 * 1024**2:
                raise RuntimeError('need 6 GiB available: 2 GiB cap + 4 GiB desktop reserve')
            command = ['systemd-run', '--user', '--scope', '--quiet', '-p', 'MemoryMax=2G',
                       '-p', 'MemorySwapMax=0', '-p', 'CPUQuota=100%',
                       'timeout', '--signal=TERM', '--kill-after=5s', '240s',
                       'bwrap', '--ro-bind', '/', '/', '--dev-bind', '/dev', '/dev', '--proc', '/proc',
                       '--bind', str(out), str(out), '--unshare-net', '--die-with-parent']
            if phase == 'host':
                command += [str(runtime / '5.2/python/bin/python3.13'), '-B', str(SELF)]
            else:
                command += [str(runtime / 'blender'), '-b', '--factory-startup', '-t', '1',
                            '--python-exit-code', '1', '--python', str(SELF), '--']
            report = out / (phase + '.json')
            command += ['--worker', phase, '--report', str(report)]
            if args.test:
                command += ['--test', args.test]
            if args.mutations:
                command.append('--mutations')
            (out / (phase + '-launch.json')).write_text(json.dumps(dict(
                argv=command, mem_available_kib=available,
                native={k: env[k] for k in ('NARWHAL_APPLICATION_CORE', 'NARWHAL_HISTORY_CORE', 'NARWHAL_GEOMETRY_CORE', 'NARWHAL3D_BREP_WRITER')}), indent=2))
            log = out / (phase + '.log')
            try:
                with log.open('w') as stream:
                    completed = subprocess.run(command, cwd=ROOT, env=env, stdout=stream,
                                               stderr=subprocess.STDOUT, timeout=255, check=False)
                data = json.loads(report.read_text()) if report.exists() else {}
                statuses.append(completed.returncode == 0 and data.get('ok') is True)
                print(phase, 'exit', completed.returncode, 'tests', data.get('tests'), flush=True)
            except subprocess.TimeoutExpired:
                statuses.append(False)
                print(phase, 'TIMEOUT (255s launcher bound)', flush=True)
            print(log.read_text()[-18000:], flush=True)
    after = {str(p): digest(p) for p in watched}
    changed = [p for p in before if before[p] != after[p]]
    (out / 'sources.json').write_text(json.dumps(dict(before=before, after=after, changed=changed), indent=2))
    print('MAIN_SOURCES_UNCHANGED', not changed, 'CHANGED', changed, 'REPORT_DIRECTORY', out, flush=True)
    return int(not all(statuses) or bool(changed))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--launch', action='store_true')
    parser.add_argument('--phase', choices=('all', 'host', 'blender'), default='all')
    parser.add_argument('--worker', choices=('host', 'blender'))
    parser.add_argument('--report', type=Path)
    parser.add_argument('--mutations', action='store_true')
    parser.add_argument('--test', help='Run one test method and, with --mutations, only its mutations')
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else sys.argv[1:]
    args = parser.parse_args(argv)
    if args.launch:
        raise SystemExit(launch(args))
    if not args.worker or not args.report:
        parser.error('use --launch; workers require a bounded launcher and --report')
    raise SystemExit(worker(args))
