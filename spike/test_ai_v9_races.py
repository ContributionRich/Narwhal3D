#!/usr/bin/env python3
"""Synthetic trusted-publisher events through source-bound native review fixtures.

No producer invocation, geometry, actual human-input or manufacturing claim.
Python only supplies ctypes lifecycle events; Rust owns canonical dispositions.
"""
import unittest
from unittest.mock import patch
import test_ai_sustained_lifecycle as life
from test_ai_accepted_review_native import NativeBridge, discover_executable

ai = life.base.ai


class PublisherRaces(unittest.TestCase):
    def test_failure_and_unknown_reports_cannot_precede_consumption_or_corrupt_sibling(self):
        cells = 0
        with NativeBridge(discover_executable()) as bridge, patch.object(ai, '_load_workflow_library', return_value=bridge.library):
            for provider in life.PROVIDERS:
                for outcome, reasons in (('failed', ('before_publication', 'rolled_back')),
                                         ('unknown', ('receipt_missing', 'publisher_disconnected', 'timeout', 'rollback_unconfirmed', 'receipt_mismatch'))):
                    for reason in reasons:
                        with self.subTest(provider=provider, outcome=outcome, reason=reason), life.session(provider) as (_state, w):
                            turn = w.prepare(life.DOC, user_text='synthetic publisher race matrix')
                            reply = life.finish(w, turn, provider, calls=[
                                (name, 'narwhal_create_cylinder', dict(center=[offset, 0, 0], radius=2, height=3))
                                for name, offset in (('first', 1), ('sibling', 20))])
                            self.assertEqual(reply['status'], 'tool_calls')
                            owner = turn.data['token']
                            context = dict(append=life.box_context(), undo_top_serial=0, undo_next_serial=1)
                            first = w.stage_accepted_review(owner, 'first', life.DOC, context)['review']
                            event = dict(outcome=outcome, reason=reason)
                            with self.assertRaises(ai.WorkflowUnavailable):
                                w.record_review_publication(first['token'], event, life.DOC)
                            self.assertEqual(w.accepted_review_state(owner, 'first', life.DOC)['state'], 'awaiting_human')
                            w.decide_accepted_review(first['token'], first['action'], life.DOC, context, accepted=True)
                            w.consume_accepted_review(first['token'], life.DOC, context)
                            w.record_review_publication(first['token'], event, life.DOC)
                            canonical = w.accepted_review_result(owner, 'first', life.DOC)['result']
                            self.assertTrue(canonical['is_error'])
                            self.assertIs(canonical['output']['retry_allowed'], False)
                            self.assertEqual(canonical['output']['outcome'], outcome)
                            sibling = w.stage_accepted_review(owner, 'sibling', life.DOC, context)['review']
                            for callback in (
                                lambda: w.record_review_publication(first['token'], event, life.DOC),
                                lambda: w.decide_accepted_review(first['token'], first['action'], life.DOC, context, accepted=True),
                                lambda: w.consume_accepted_review(first['token'], life.DOC, context)):
                                try:
                                    callback()
                                except ai.WorkflowUnavailable:
                                    pass
                                self.assertEqual(w.accepted_review_state(owner, 'sibling', life.DOC)['state'], 'awaiting_human')
                                self.assertEqual(w.accepted_review_result(owner, 'first', life.DOC)['result'], canonical)
                            w.decide_accepted_review(sibling['token'], sibling['action'], life.DOC, context, accepted=False)
                            second = w.accepted_review_result(owner, 'sibling', life.DOC)['result']
                            self.assertEqual(second['output']['review_state'], 'denied')
                            w.record_results(owner, [canonical, second], life.DOC)
                            self.assertFalse(w.public_workflow_state()['cad_state']['current_geometry_verified'])
                            cells += 1
        self.assertEqual(cells, 28)
        print('PASS28 synthetic publisher disposition/sibling race cells')


if __name__ == '__main__':
    import sys
    if '--mutations' in sys.argv:
        sys.argv.remove('--mutations')
        original = ai.WorkflowSession.record_review_publication
        def hide_refusal(self, *args, **kwargs):
            try:
                return original(self, *args, **kwargs)
            except ai.WorkflowUnavailable:
                return {'status':'accepted'}
        with patch.object(ai.WorkflowSession, 'record_review_publication', hide_refusal):
            red = unittest.TestResult()
            PublisherRaces('test_failure_and_unknown_reports_cannot_precede_consumption_or_corrupt_sibling').run(red)
            assert len(red.failures) >= 28 and not red.errors, (red.failures, red.errors)
        print('KILLED adapter mutation concealing pre-consumption publisher refusal; restored')
    unittest.main(verbosity=2)
