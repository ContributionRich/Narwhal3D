#!/usr/bin/env python3
"""V4 additive-operation compatibility against unchanged reviewed V3 library."""
import argparse
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch
import test_ai_sustained_lifecycle as base

CURRENT=None
REVIEWED=None


class Compatibility(unittest.TestCase):
    def test_v4_operation_unavailable_is_explicit_without_losing_old_request(self):
        for library,code in ((REVIEWED,'invalid_request'),(CURRENT,'accepted_review_call_required')):
            with self.subTest(library=str(library)):
                state=base.base.ai.AssistantState(provider=base.base.ai.set_provider('openai_compatible','fixture',credential_env='TEST_KEY'))
                with base.base.ai.WorkflowSession(state,base.DOC,allow_reads=True,library_path=library) as w:
                    p=w.prepare(base.DOC,user_text='ordinary chat remains usable')
                    with self.assertRaisesRegex(base.base.ai.WorkflowUnavailable,code):
                        w._exchange({'op':'accepted_review_state','owner':p.data['token'],'binding':w._binding(),'call_id':'not-an-accepted-call'})
                    self.assertEqual(base.finish(w,p,'openai_compatible')['status'],'completed')
                    catalog=w.operation_catalog()
                    self.assertEqual([row['operation'] for row in catalog['operations']['operations'] if row['execution_available']],['box'])
                    self.assertFalse(any(tool['name'].startswith('narwhal_create_') and tool['name']!='narwhal_create_box' for tool in catalog['tools']))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--current-library',type=Path,required=True);p.add_argument('--reviewed-library',type=Path,required=True)
    a=p.parse_args();CURRENT=a.current_library.resolve();REVIEWED=a.reviewed_library.resolve()
    before=hashlib.sha256(REVIEWED.read_bytes()).hexdigest()
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Compatibility))
    if result.wasSuccessful():
        original=base.base.ai.WorkflowSession._exchange
        def silent(self,data):
            try:return original(self,data)
            except base.base.ai.WorkflowUnavailable:return {'status':'ready'}
        with patch.object(base.base.ai.WorkflowSession,'_exchange',silent):
            red=unittest.TestResult();Compatibility('test_v4_operation_unavailable_is_explicit_without_losing_old_request').run(red)
            assert len(red.failures)==2 and not red.errors,'unavailable-as-success mutation survived'
        print('KILLED unavailable_as_success')
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Compatibility))
    assert hashlib.sha256(REVIEWED.read_bytes()).hexdigest()==before,'reviewed library changed'
    print('Reviewed library unchanged SHA256:',before)
    raise SystemExit(not result.wasSuccessful())
