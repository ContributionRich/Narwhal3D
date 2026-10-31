#!/usr/bin/env python3
"""Assistant review presentation controls; no geometry, policy or frozen editor edits."""
import importlib
import unittest
from unittest.mock import patch
import test_ai_workflow as base
review=importlib.import_module('narwhal3d.ai_action_review')


def descriptor():
    return {'action':{'operation':'cylinder','arguments':{'center':[1.23456789012345,2,3],'radius':4,'height':7}},
            'expected':{'append':{'units':'Millimeters'}}}


class Presentation(unittest.TestCase):
    def test_waiting_review_is_not_ready_to_send(self):
        state=base.ai.AssistantState();state.status='awaiting_review'
        self.assertEqual(base.ai.connection_label(state),'Awaiting your review')
    def test_exact_fields_and_unavailable_authority(self):
        text='\n'.join(review.lines(descriptor(),300001))
        for value in ('1.23456789012345','Radius: 4','Height (+world Z): 7','Millimeters','unavailable','OFF in normal catalogs','301 s'):
            self.assertIn(value,text)
    def test_no_hidden_approval_when_fields_do_not_fit(self):
        small=review.layout(descriptor(),1000,(0,0,100,80),len)
        self.assertFalse(small['fits']);self.assertIsNone(small['approve']);self.assertIsNone(small['reject'])
        large=review.layout(descriptor(),1000,(0,0,250,700),len)
        self.assertTrue(large['fits']);self.assertIsNotNone(large['approve'])
    def test_unknown_is_not_no_object_or_success(self):
        text=review.outcome_text({'is_error':True,'output':{'created':None,'may_have_published':True,'outcome':'unknown'}})
        self.assertIn('UNKNOWN',text);self.assertIn('may exist',text);self.assertIn('no automatic retry',text)
    def test_plan_is_not_execution(self):
        text=review.plan_text({'status':'plan_preflight','steps':[{'id':'shell','operation':'unsupported','blockers':['executor_not_integrated'],'unsupported_need':'Exact closure missing'}], 'questions':['Wall thickness?']})
        self.assertIn('PLAN ONLY',text);self.assertIn('Exact closure missing',text);self.assertIn('not been checked',text)
        self.assertIsNone(review.plan_text('created according to model'))
    def test_presentation_mutations(self):
        variants=[(review,'outcome_text',lambda _r:'Nothing created.','test_unknown_is_not_no_object_or_success'),
                  (review,'lines',lambda *_a:['missing native fields'],'test_exact_fields_and_unavailable_authority'),
                  (review,'layout',lambda *_a,**_k:{'fits':True,'approve':(0,0,1,1),'reject':(0,0,1,1)},'test_no_hidden_approval_when_fields_do_not_fit'),
                  (review,'plan_text',lambda _r:'Created geometry.','test_plan_is_not_execution'),
                  (base.ai,'connection_label',lambda _s:'Ready to send','test_waiting_review_is_not_ready_to_send')]
        for module,name,mutant,case in variants:
            original=getattr(module,name)
            with patch.object(module,name,mutant):
                result=unittest.TestResult();Presentation(case).run(result)
                self.assertEqual(len(result.failures),1,name);self.assertEqual(result.errors,[],name)
            self.assertIs(getattr(module,name),original)
        print('KILLED 5 presentation mutations; original functions restored')


if __name__=='__main__':unittest.main(verbosity=2)
