#!/usr/bin/env python3
"""Actual Blender publisher + test-only native accepted calls; no live tool activation.

Run via Blender --background --python this-file -- --native-executable PATH.
The native fixture parses real provider replies, but human decisions are synthetic
explicit test events, not visible-dialog acceptance. Never use owner documents.
"""
import argparse
from contextlib import contextmanager
from copy import deepcopy
import importlib
import inspect
import math
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_ai_primitives as base
from test_ai_accepted_review_native import NativeBridge

life = base.lifecycle
ai = life.base.ai
EXECUTABLE = None
LEGACY_LIBRARY = None


class PublisherTests(unittest.TestCase):
    @contextmanager
    def scene(self, operation='sphere', provider='openai_responses'):
        import bpy
        from narwhal3d import model, commands, ai_primitives, ai_workspace
        bpy.data.batch_remove(list(bpy.data.objects))
        m = model.NarwhalModel(); ctx = commands.NarwhalContext(m)
        app = types.SimpleNamespace(model=m, ctx=ctx, active_command=None)
        with NativeBridge(EXECUTABLE) as native, base.ui_fixture(app), \
             patch.object(ai_workspace.WorkspaceSnapshots, 'ready', lambda _self: True), \
             patch.object(ai, '_load_workflow_library', return_value=native.library), \
             life.session(provider) as (_state, w):
            p = w.prepare(life.DOC, user_text='local disabled publisher integration')
            args = base.arguments(operation)
            completed = life.finish(w, p, provider, calls=[('p1', 'narwhal_create_' + operation, args['arguments'])])
            self.assertEqual(completed['status'], 'tool_calls')
            review = w.stage_accepted_review(p.data['token'], 'p1', life.DOC,
                                            ai_primitives.context(app, life.DOC))['review']
            self.assertFalse(review['enabled'])
            yield app, w, p.data['token'], review

    def approve(self, app, w, review):
        from narwhal3d import ai_primitives
        return w.decide_accepted_review(review['token'], review['action'], life.DOC,
                                       ai_primitives.context(app, life.DOC), accepted=True)

    def test_exact_primitives_one_use_undo_and_canonical_continuation(self):
        from narwhal3d import ai_primitives, kernel
        from narwhal3d.kernel import occt_backend as occt
        for operation in ('sphere', 'cylinder', 'cone'):
            with self.subTest(operation=operation), self.scene(operation) as (app, w, owner, review):
                self.assertEqual(app.model.object_count(), 0)
                with self.assertRaises(ai.WorkflowUnavailable):
                    ai_primitives.publish_accepted_review(app, w, review['token'], life.DOC, lambda: None)
                self.assertEqual(app.model.object_count(), 0)
                self.approve(app, w, review)
                result = ai_primitives.publish_accepted_review(app, w, review['token'], life.DOC, lambda: None)
                self.assertTrue(result['output']['created'])
                self.assertFalse(result['output']['current_geometry_verified'])
                self.assertEqual(app.model.object_count(), 1)
                obj = list(app.model.file.Objects)[0]
                shape = kernel.master_of(obj.Geometry)
                self.assertIsNotNone(shape)
                expected_faces = {'sphere': {'SPHERE': 1}, 'cylinder': {'CYLINDER': 1, 'PLANE': 2},
                                  'cone': {'CONE': 1, 'PLANE': 1}}[operation]
                self.assertEqual(occt.face_types(shape), expected_faces)
                self.assertTrue(occt.is_airtight(shape))
                volume = 4 / 3 * math.pi * 4**3 if operation == 'sphere' else math.pi * 4**2 * 7
                if operation == 'cone': volume /= 3
                self.assertAlmostEqual(occt.volume(shape), volume, places=7)
                self.assertEqual(kernel.recipe_of(obj.Geometry)['kind'], operation)
                with self.assertRaises(ai.WorkflowUnavailable):
                    ai_primitives.publish_accepted_review(app, w, review['token'], life.DOC, lambda: None)
                self.assertEqual(app.model.object_count(), 1)
                self.assertEqual(w.accepted_review_result(owner, 'p1', life.DOC)['result'], result)
                w.record_results(owner, [result], life.DOC)
                next_turn = w.prepare(life.DOC, continuation=True)
                self.assertTrue(next_turn.data['body'])
                self.assertEqual(app.ctx.undo.current_serial, 0)
                self.assertTrue(app.ctx.undo.can_undo)
                self.assertEqual(app.ctx.undo.undo_names(), ['AI ' + operation.title() + ' prototype'])
                app.ctx.undo_last(); self.assertEqual(app.model.object_count(), 0)
                self.assertFalse(app.ctx.undo.can_undo)
                app.ctx.redo_last(); self.assertEqual(app.model.object_count(), 1)
                restored = list(app.model.file.Objects)[0]
                self.assertEqual(occt.face_types(kernel.master_of(restored.Geometry)), expected_faces)

    def test_deny_cancel_changed_pins_and_foreign_action_never_publish(self):
        from narwhal3d import ai_primitives
        for mode in ('deny', 'cancel', 'pins', 'action', 'document'):
            with self.subTest(mode=mode), self.scene() as (app, w, owner, review):
                current = ai_primitives.context(app, life.DOC)
                self.assertEqual(w.accepted_review_state(owner, 'p1', life.DOC)['state'], 'awaiting_human')
                if mode == 'deny':
                    w.decide_accepted_review(review['token'], review['action'], life.DOC, current)
                elif mode == 'cancel':
                    self.approve(app, w, review); w.invalidate()
                elif mode == 'pins':
                    self.approve(app, w, review)
                    app.active_command = types.SimpleNamespace(name='Busy')
                elif mode == 'document':
                    self.approve(app, w, review)
                else:
                    action = deepcopy(review['action']); action['arguments']['radius'] = 9
                    with self.assertRaises(ai.WorkflowUnavailable):
                        w.decide_accepted_review(review['token'], action, life.DOC, current, accepted=True)
                with self.assertRaises(ai.WorkflowUnavailable):
                    ai_primitives.publish_accepted_review(app, w, review['token'],
                        'different-document' if mode == 'document' else life.DOC, lambda: None)
                self.assertEqual(app.model.object_count(), 0)

    def test_before_publication_failure_is_native_canonical_failure(self):
        from narwhal3d import ai_primitives, bridge
        with self.scene() as (app, w, owner, review):
            self.approve(app, w, review)
            with patch.object(bridge, 'make_sphere', side_effect=RuntimeError('private diagnostic')):
                result = ai_primitives.publish_accepted_review(app, w, review['token'], life.DOC, lambda: None)
            self.assertFalse(result['output']['created'])
            self.assertEqual(result['output']['reason'], 'before_publication')
            self.assertTrue(result['is_error']); self.assertEqual(app.model.object_count(), 0)
            w.record_results(owner, [result], life.DOC)
            w.prepare(life.DOC, continuation=True)

    def test_publication_error_preserves_external_edit_and_reports_unknown(self):
        import bpy
        from narwhal3d import ai_primitives, bridge
        with self.scene() as (app, w, owner, review):
            self.approve(app, w, review)
            retained = []
            def display_failure(*args, **kwargs):
                oid, _ = app.model.add_object(bridge.make_sphere([1, 2, 3], 2), 'External')
                retained.append(oid)
                raise RuntimeError('injected display failure')
            with patch.object(app.ctx, '_make_display_object', display_failure):
                result = ai_primitives.publish_accepted_review(app, w, review['token'], life.DOC, lambda: None)
            self.assertIsNone(result['output']['created'])
            self.assertEqual(result['output']['reason'], 'rollback_unconfirmed')
            self.assertFalse(result['output']['retry_allowed'])
            self.assertEqual({str(o.Attributes.Id) for o in app.model.file.Objects}, set(retained))
            self.assertEqual(len(bpy.data.objects), 0)
            self.assertEqual(app.ctx.undo.current_serial, 0)
            w.record_results(owner, [result], life.DOC)

    def test_lost_callback_does_not_republish_or_fabricate_success(self):
        from narwhal3d import ai_primitives
        with self.scene() as (app, w, owner, review):
            self.approve(app, w, review)
            with patch.object(w, 'record_review_publication', side_effect=ai.WorkflowUnavailable('disconnected')):
                with self.assertRaises(ai.WorkflowUnavailable):
                    ai_primitives.publish_accepted_review(app, w, review['token'], life.DOC, lambda: None)
            self.assertEqual(app.model.object_count(), 1)
            with self.assertRaises(ai.WorkflowUnavailable):
                ai_primitives.publish_accepted_review(app, w, review['token'], life.DOC, lambda: None)
            self.assertEqual(app.model.object_count(), 1)

    def test_old_library_reports_explicit_unavailability(self):
        if LEGACY_LIBRARY is None:
            self.skipTest('Retained legacy fixture not supplied: use --legacy-library or NARWHAL_AI_LEGACY_LIBRARY')
        old = LEGACY_LIBRARY
        loader = ai._load_workflow_library
        with patch.object(ai, '_load_workflow_library', lambda path=None: loader(old)), \
             life.session('openai_responses') as (_state, w):
            p = w.prepare(life.DOC, user_text='old library remains useful')
            self.assertEqual(life.finish(w, p, 'openai_responses')['status'], 'completed')
            with self.assertRaisesRegex(ai.WorkflowUnavailable, 'accepted_review_unavailable'):
                w.accepted_review_state(p.data['token'], 'missing', life.DOC)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-executable', type=Path, required=True)
    parser.add_argument('--legacy-library', type=Path,
                        default=os.environ.get('NARWHAL_AI_LEGACY_LIBRARY'),
                        help='Explicit retained pre-review library; absence skips only legacy compatibility')
    parser.add_argument('--test')
    parser.add_argument('--mutations', action='store_true')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else None)
    EXECUTABLE = args.native_executable.resolve()
    LEGACY_LIBRARY = args.legacy_library.expanduser().resolve() if args.legacy_library is not None else None
    suite = (unittest.TestSuite([PublisherTests(args.test)]) if args.test
             else unittest.defaultTestLoader.loadTestsFromTestCase(PublisherTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful(): raise SystemExit(1)
    if args.mutations:
        import io
        from narwhal3d import ai_primitives
        variants = [
            ('old pending API', 'publish_accepted_review', 'workflow.consume_accepted_review(',
             'workflow.take_primitive_prototype(', 'test_exact_primitives_one_use_undo_and_canonical_continuation'),
            ('missing publisher event', 'publish_accepted_review',
             'workflow.record_review_publication(token, outcome, document_session)', 'None',
             'test_exact_primitives_one_use_undo_and_canonical_continuation'),
            ('false rollback certainty', 'publish_accepted_review',
             "{'outcome': 'unknown', 'reason': 'rollback_unconfirmed'}",
             "{'outcome': 'failed', 'reason': 'rolled_back'}",
             'test_publication_error_preserves_external_edit_and_reports_unknown'),
            ('wrong geometry', '_publish_consumed', "bridge.make_sphere(args['center'], args['radius'])",
             "bridge.make_sphere(args['center'], args['radius'] * 2)",
             'test_exact_primitives_one_use_undo_and_canonical_continuation'),
        ]
        for label, name, old, new, test in variants:
            source = inspect.getsource(getattr(ai_primitives, name))
            assert source.count(old) == 1, (label, 'mutation anchor changed')
            namespace = dict(ai_primitives.__dict__)
            exec(source.replace(old, new), namespace)
            with patch.object(ai_primitives, name, namespace[name]):
                observed = unittest.TextTestRunner(stream=io.StringIO()).run(unittest.TestSuite([PublisherTests(test)]))
            assert not observed.wasSuccessful(), 'mutation survived: ' + label
            print('RED confirmed:', label, flush=True)
        restored = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(PublisherTests))
        if not restored.wasSuccessful(): raise SystemExit(1)
