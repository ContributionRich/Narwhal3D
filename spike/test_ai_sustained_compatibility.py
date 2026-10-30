#!/usr/bin/env python3
"""Old ABI1 library compatibility; no builds, installed binary writes or provider I/O."""
import argparse
from pathlib import Path
from unittest.mock import patch
import unittest
import test_ai_sustained_lifecycle as lifecycle

OLD_LIBRARY=None


class CompatibilityTests(unittest.TestCase):
    def test_old_native_refuses_new_operations_without_breaking_old_chat_or_box(self):
        ai=lifecycle.base.ai
        state=ai.AssistantState(provider=ai.set_provider('openai_responses','fixture',credential_env='TEST_KEY'))
        with ai.WorkflowSession(state,lifecycle.DOC,allow_reads=True,library_path=OLD_LIBRARY) as w:
            names={t['name'] for t in w.operation_catalog()['tools']}
            self.assertIn('narwhal_create_box',names)
            self.assertNotIn('narwhal_plan',names,'fixture must really predate the new operations')
            for method in (lambda:w.public_workflow_state(),lambda:w.validate_plan({}),
                           lambda:w.stage_primitive_prototype({},lifecycle.DOC,{},{})):
                with self.assertRaisesRegex(ai.WorkflowUnavailable,'invalid_request'):
                    method()
            p=w.prepare(lifecycle.DOC,user_text='ordinary chat still works')
            self.assertEqual(lifecycle.finish(w,p,'openai_responses')['status'],'completed')
            p=w.prepare(lifecycle.DOC,user_text='box proposal')
            out=lifecycle.finish(w,p,'openai_responses',calls=[('box','narwhal_create_box',dict(origin=[0,0,0],size=[2,2,2]))])
            proposal=w.propose_box(out['token'],'box',lifecycle.DOC,lifecycle.box_context())['proposal']
            self.assertEqual(w.decide_box(proposal,lifecycle.DOC,lifecycle.box_context(),accepted=True)['status'],'approved_box')

    def test_missing_native_export_never_falls_back_to_python_policy(self):
        ai=lifecycle.base.ai
        import ctypes
        class MissingExports:
            def __getattr__(self,_name): raise AttributeError('missing export')
        state=ai.AssistantState(provider=ai.set_provider('openai_responses','fixture'))
        with patch.object(ctypes,'CDLL',return_value=MissingExports()), \
             patch.object(lifecycle.base.http,'send_chat',side_effect=AssertionError('legacy bypass')):
            with self.assertRaisesRegex(ai.WorkflowUnavailable,'native_workflow_unavailable'):
                ai.WorkflowSession(state,lifecycle.DOC,library_path=OLD_LIBRARY)
        self.assertNotIn('workflow',state.extra)
        self.assertEqual(state.messages,[])


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--old-library',type=Path,required=True)
    args=p.parse_args();OLD_LIBRARY=args.old_library.resolve()
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CompatibilityTests))
    if result.wasSuccessful():
        original=lifecycle.base.ai.WorkflowSession._exchange
        def silent_unavailable(self,data):
            try: return original(self,data)
            except lifecycle.base.ai.WorkflowUnavailable: return {'status':'ready'}
        with patch.object(lifecycle.base.ai.WorkflowSession,'_exchange',silent_unavailable):
            mutant=unittest.TestResult()
            CompatibilityTests('test_old_native_refuses_new_operations_without_breaking_old_chat_or_box').run(mutant)
            assert len(mutant.failures)==1 and not mutant.errors,'unavailable-state mutation survived'
        print('RED confirmed: old-native errors must not become silent success')
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CompatibilityTests))
    raise SystemExit(not result.wasSuccessful())
