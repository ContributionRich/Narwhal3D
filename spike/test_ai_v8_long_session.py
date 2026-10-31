#!/usr/bin/env python3
"""Bounded native display projection, distinct from canonical provider replay.

No paid HTTP, GUI, document data or library staging. The source-bound fixture is
built explicitly. Python tests only the existing binding/UI message integration.
"""
import json
import inspect
import textwrap
import sys
import unittest
from unittest.mock import patch
import test_ai_sustained_lifecycle as life
from test_ai_accepted_review_native import NativeBridge, discover_executable, TARGET, _file_hash

ai = life.base.ai


class LongReply(unittest.TestCase):
    def setUp(self):
        self.bridge = NativeBridge(discover_executable()).__enter__()
        self.addCleanup(self.bridge.close)
        self.loader = patch.object(ai, '_load_workflow_library', return_value=self.bridge.library)
        self.loader.start(); self.addCleanup(self.loader.stop)

    def test_unicode_display_is_bounded_and_canonical_replay_is_not_shortened(self):
        text = 'x'*15999 + '🙂🙂TAIL-CANARY'
        for provider in life.PROVIDERS:
            with self.subTest(provider=provider), life.session(provider) as (state, w):
                p = w.prepare(life.DOC, user_text='bounded long reply fixture')
                out = life.finish(w, p, provider, text=text, opaque='opaque-private-replay')
                self.assertEqual(out['status'], 'completed')
                self.assertTrue('presentation' in out, 'native display projection missing')
                view = out['presentation']
                self.assertTrue(view['truncated'])
                self.assertEqual(view['scope'], 'display_only')
                self.assertEqual(view['text'], text[:16000])
                self.assertEqual(out['response']['text'], text)
                self.assertIn('Display shortened', view['notice'])
                messages = [(m.role, m.text) for m in state.messages]
                self.assertIn(('assistant', text[:16000]), messages)
                self.assertIn(('status', view['notice']), messages)
                self.assertNotIn('opaque-private-replay', str(messages))
                next_turn = w.prepare(life.DOC, user_text='continue')
                self.assertIn('TAIL-CANARY', json.dumps(next_turn.data['body']))
                if provider == 'openai_responses':
                    replay = next_turn.data['body']['input']
                    self.assertIn(life.reasoning('opaque-private-replay'), replay)
                    self.assertIn(life.message(text), replay)
                self.assertFalse(w.public_workflow_state()['cad_state']['current_geometry_verified'])

    def test_exact_limit_has_no_false_truncation_notice(self):
        for length in (15999, 16000):
            with self.subTest(length=length), life.session('openai_responses') as (state, w):
                p = w.prepare(life.DOC, user_text='at limit')
                out = life.finish(w, p, 'openai_responses', text='x'*length)
                self.assertTrue('presentation' in out, 'native display projection missing')
                self.assertFalse(out['presentation']['truncated'])
                self.assertIsNone(out['presentation']['notice'])
                self.assertFalse(any('Display shortened' in m.text for m in state.messages))

    def test_legacy_completion_without_additive_projection_remains_supported(self):
        # Simulate an older response envelope, not a legacy-library qualification.
        original = ai.WorkflowSession._exchange
        def legacy(self, request):
            result = original(self, request)
            result.pop('presentation', None)
            return result
        with patch.object(ai.WorkflowSession, '_exchange', legacy), life.session('openai_compatible') as (state, w):
            p = w.prepare(life.DOC, user_text='legacy optional envelope')
            out = life.finish(w, p, 'openai_compatible', text='legacy reply')
            self.assertEqual(out['status'], 'completed')
            self.assertEqual(state.messages[-1].text, 'legacy reply')


class NormalNativeLongReply(LongReply):
    """Same contract through the real CDLL, without the cfg(test) bridge/catalog."""
    def setUp(self):
        discover_executable()  # Also validates current-source and normal-library hashes.
        library = TARGET.resolve() / 'debug/libnarwhal_automation.so'
        native = ai._load_workflow_library(library)
        self.loader = patch.object(ai, '_load_workflow_library', return_value=native)
        self.loader.start(); self.addCleanup(self.loader.stop)


if __name__ == '__main__':
    normal_only = '--normal-native' in sys.argv
    if normal_only:
        sys.argv.remove('--normal-native')
    discover_executable()
    print('NORMAL_NATIVE_SHA256', _file_hash(TARGET.resolve() / 'debug/libnarwhal_automation.so'), flush=True)
    if '--mutations' in sys.argv:
        source = textwrap.dedent(inspect.getsource(ai.WorkflowSession.finish))
        old = 'self._state.add_message("status", view["notice"])'
        assert source.count(old) == 1
        namespace = dict(ai.__dict__)
        exec(compile(source.replace(old, 'pass'), '<private-display-notice-mutation>', 'exec'), namespace)
        with patch.object(ai.WorkflowSession, 'finish', namespace['finish']):
            result = unittest.TestResult()
            NormalNativeLongReply('test_unicode_display_is_bounded_and_canonical_replay_is_not_shortened').run(result)
            assert result.failures and not result.errors, (result.failures, result.errors)
        print('KILLED host projection-notice mutation; original binding restored', flush=True)
        original_prepare = ai.WorkflowSession.prepare
        def drop_opaque(self, *args, **kwargs):
            turn = original_prepare(self, *args, **kwargs)
            body = turn.data['body']
            if 'input' in body:
                body['input'] = [item for item in body['input'] if item.get('type') != 'reasoning']
            return turn
        with patch.object(ai.WorkflowSession, 'prepare', drop_opaque):
            result = unittest.TestResult()
            NormalNativeLongReply('test_unicode_display_is_bounded_and_canonical_replay_is_not_shortened').run(result)
            assert result.failures and not result.errors, (result.failures, result.errors)
        print('KILLED opaque replay omission mutation; original binding restored', flush=True)
        cases = (NormalNativeLongReply,) if normal_only else (LongReply, NormalNativeLongReply)
        suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(case)
                                   for case in cases)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(not result.wasSuccessful())
    unittest.main(defaultTest='NormalNativeLongReply' if normal_only else None, verbosity=2)
