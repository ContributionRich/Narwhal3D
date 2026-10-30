#!/usr/bin/env python3
"""Native-backed box approval contract; no geometry, GUI, network or builds."""
import argparse
import unittest
from dataclasses import replace

import test_ai_workflow as base

LIBRARY = None
DOCUMENT = '00000000000000000000000000000001'


class BoxApprovalTests(unittest.TestCase):
    def setUp(self):
        self.state = base.ai.AssistantState(provider=base.ai.set_provider('openai_compatible', 'fixture'))
        self.session = base.ai.WorkflowSession(self.state, DOCUMENT, library_path=LIBRARY, allow_reads=True)
        self.addCleanup(self.session.close)
        self.context = dict(document_session=DOCUMENT,
            layer_id='00000000-0000-0000-0000-000000000002', units='Millimeters',
            tolerance=.01, layer_available=True, command_idle=True, undo_ready=True)

    def stage(self, arguments=None):
        ready = self.session.prepare(DOCUMENT, user_text='Make a cube')
        reply = base.payload('openai_compatible', '', [('cube', 'narwhal_create_box',
            arguments or {'origin':[1,2,3], 'size':[10,10,10]})])
        result = self.session.finish(base.send(ready, reply), DOCUMENT)
        self.assertEqual(result['status'], 'tool_calls')
        token = ready.data['token']
        proposal = self.session.propose_box(token, 'cube', DOCUMENT, self.context)['proposal']
        return token, proposal

    def test_approved_box_is_typed_and_one_use(self):
        token, proposal = self.stage()
        with self.assertRaises(base.ai.WorkflowUnavailable):
            self.session.read_tool_result(token, 'cube', None, DOCUMENT)
        approved = self.session.decide_box(proposal, DOCUMENT, self.context, accepted=True)
        self.assertEqual(approved['status'], 'approved_box')
        self.assertEqual(approved['arguments'], {'origin':[1,2,3], 'size':[10,10,10]})
        with self.assertRaises(base.ai.WorkflowUnavailable):
            self.session.decide_box(proposal, DOCUMENT, self.context, accepted=True)
        with self.assertRaises(base.ai.WorkflowUnavailable):
            self.session.propose_box(token, 'cube', DOCUMENT, self.context)

    def test_cancel_never_approves(self):
        _, proposal = self.stage()
        result = self.session.decide_box(proposal, DOCUMENT, self.context, accepted=False)
        self.assertEqual(result['status'], 'rejected')
        with self.assertRaises(base.ai.WorkflowUnavailable):
            self.session.decide_box(proposal, DOCUMENT, self.context, accepted=True)

    def test_provider_and_generation_changes_invalidate_approval(self):
        _, proposal = self.stage()
        self.state.provider = replace(self.state.provider, model='other')
        with self.assertRaises(base.ai.WorkflowUnavailable):
            self.session.decide_box(proposal, DOCUMENT, self.context, accepted=True)

    def test_document_units_and_layer_state_are_pinned(self):
        _, proposal = self.stage()
        altered = dict(self.context, units='Inches')
        with self.assertRaises(base.ai.WorkflowUnavailable):
            self.session.decide_box(proposal, DOCUMENT, altered, accepted=True)

    def test_forged_dimensions_and_missing_approval_are_rejected(self):
        _, proposal = self.stage()
        forged = dict(proposal, arguments={'origin':[1,2,3], 'size':[100,100,100]})
        with self.assertRaises(base.ai.WorkflowUnavailable):
            self.session.decide_box(forged, DOCUMENT, self.context, accepted=True)
        self.session.invalidate('new_chat')
        with self.assertRaises(base.ai.WorkflowUnavailable):
            self.session.decide_box(proposal, DOCUMENT, self.context, accepted=True)

    def test_catalog_bounds_and_append_scope(self):
        ready = self.session.prepare(DOCUMENT, user_text='Describe available operations')
        tool = next(t for t in ready.data['body']['tools'] if t['function']['name']=='narwhal_create_box')
        self.assertFalse(tool['function']['parameters']['additionalProperties'])
        self.assertIn('never modifies selected objects', tool['function']['description'])
        operation = next(op for op in self.session.operation_catalog()['operations']['operations'] if op['operation']=='box')
        self.assertTrue(operation['requires_explicit_ui_approval'])
        self.assertIn('in_app_assistant_only', operation['scope'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', required=True)
    args = parser.parse_args()
    LIBRARY = args.library
    unittest.main(argv=[__file__], verbosity=2)
