#!/usr/bin/env python3
"""NEXT-RELEASE native lifecycle regressions; inspect outgoing payloads, no HTTP/GUI."""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import time
import unittest

import test_ai_workflow as base
from test_ai_responses import response, message, reasoning, function

LIBRARY = None
DOC = '00000000000000000000000000000001'
PROVIDERS = ('openai', 'openai_compatible', 'anthropic', 'openai_responses')


@contextmanager
def session(provider):
    state = base.ai.AssistantState(provider=base.ai.set_provider(provider,'fixture',credential_env='TEST_KEY'))
    with base.ai.WorkflowSession(state,DOC,allow_reads=True,library_path=LIBRARY) as workflow:
        yield state,workflow


def finish(workflow, prepared, provider, text='prior-answer-canary', calls=(), opaque='opaque-replay-canary'):
    if provider == 'openai_responses':
        items=[reasoning(opaque),message(text)]
        items.extend(function(cid,'item-'+cid,name,json.dumps(args)) for cid,name,args in calls)
        data=response(*items)
    else:
        data=base.payload(provider,text,calls)
    return workflow.finish(base.http.TurnTransportResult(prepared.data['token'],raw=json.dumps(data)),DOC)


def box_context():
    return dict(document_session=DOC,layer_id='00000000-0000-0000-0000-000000000002',
                units='Millimeters',tolerance=.01,layer_available=True,command_idle=True,undo_ready=True)


def terminate(workflow, prepared, mode):
    if mode in ('poll','complete_expiry'):
        time.sleep(.01)
    if mode=='cancel':
        return workflow.invalidate()
    if mode=='poll':
        return workflow.poll_timeout()
    return workflow.finish(base.http.TurnTransportResult(prepared.data['token'],
        raw=None, failure={'transport_cancel':'cancelled','transport_timeout':'timeout'}.get(mode)),DOC)


class LifecycleTests(unittest.TestCase):
    def test_01_terminal_paths_discard_same_replay_and_abandoned_turn(self):
        for provider in PROVIDERS:
            for mode in ('cancel','poll','complete_expiry','transport_cancel','transport_timeout'):
                with self.subTest(provider=provider,mode=mode), session(provider) as (_state,w):
                    p=w.prepare(DOC,user_text='prior-user-canary')
                    self.assertEqual(finish(w,p,provider)['status'],'completed')
                    abandoned=w.prepare(DOC,user_text='abandoned-user-canary',
                                        timeout_ms=1 if mode in ('poll','complete_expiry') else 30000)
                    self.assertTrue('prior-answer-canary' in json.dumps(abandoned.data['body']))
                    result=terminate(w,abandoned,mode)
                    self.assertIn(result['status'],('reset','cancelled','timeout'))
                    fresh=w.prepare(DOC,user_text='fresh-only')
                    wire=json.dumps(fresh.data['body'])
                    self.assertTrue(all(marker not in wire for marker in
                        ('prior-answer-canary','prior-user-canary','abandoned-user-canary','opaque-replay-canary')),
                        'abandoned transcript/opaque replay escaped into NEXT request')
                    self.assertGreater(fresh.data['token']['generation'],abandoned.data['token']['generation'])
                    late=w.finish(base.http.TurnTransportResult(abandoned.data['token'],failure='cancelled'),DOC)
                    self.assertEqual(late['status'],'stale')
                    self.assertEqual(finish(w,fresh,provider,'fresh-answer')['status'],'completed')

    def test_02_terminal_paths_revoke_box_but_do_not_reset_budgets_or_call_ids(self):
        for mode in ('cancel','poll','complete_expiry','transport_cancel','transport_timeout'):
            with self.subTest(mode=mode), session('openai_responses') as (_state,w):
                p=w.prepare(DOC,user_text='stage only')
                calls=[('box-call','narwhal_create_box',{'origin':[0,0,0],'size':[10,10,10]})]
                out=finish(w,p,'openai_responses',calls=calls)
                proposal=w.propose_box(out['token'],'box-call',DOC,box_context())['proposal']
                w.record_results(out['token'],[{'call_id':'box-call','output':{'created':False},'is_error':False}],DOC)
                p=w.prepare(DOC,user_text='abandon',timeout_ms=1 if mode in ('poll','complete_expiry') else 30000)
                terminate(w,p,mode)
                with self.assertRaises(base.ai.WorkflowUnavailable):
                    w.decide_box(proposal,DOC,box_context(),accepted=True)
                fresh=w.prepare(DOC,user_text='fresh')
                repeated=finish(w,fresh,'openai_responses',calls=calls)
                self.assertEqual(repeated.get('code'),'replayed_tool_call')
                for _ in range(5):
                    p=w.prepare(DOC,user_text='bounded')
                    self.assertEqual(finish(w,p,'openai_responses','ok')['status'],'completed')
                with self.assertRaisesRegex(base.ai.WorkflowUnavailable,'turn_budget'):
                    w.prepare(DOC,user_text='ninth')

    def test_03_near_capacity_result_terminates_instead_of_leaving_calls_pending(self):
        with session('openai_responses') as (_state,w):
            for index in range(2):
                p=w.prepare(DOC,user_text='u'*7000)
                self.assertEqual(finish(w,p,'openai_responses','short',opaque='q'*21000)['status'],'completed')
            p=w.prepare(DOC,user_text='read')
            out=finish(w,p,'openai_responses','',calls=[('result','narwhal_document_summary',{})],opaque='small')
            self.assertEqual(out['status'],'tool_calls')
            bounded=[{'call_id':'result','output':{'text':'r'*9000},'is_error':False}]
            self.assertLess(len(json.dumps(bounded).encode()),16384)
            with self.assertRaisesRegex(base.ai.WorkflowUnavailable,'context_budget'):
                w.record_results(out['token'],bounded,DOC)
            fresh=w.prepare(DOC,user_text='fresh-only')
            self.assertEqual(fresh.data['body']['input'],[{'role':'user','content':'fresh-only'}])
            self.assertGreater(fresh.data['token']['generation'],out['token']['generation'])
            with self.assertRaises(base.ai.WorkflowUnavailable):
                w.record_results(out['token'],bounded,DOC)

    def test_04_budgets_and_invalid_provider_output_end_whole_exchange(self):
        for provider in PROVIDERS:
            for failure in ('result_size','provider_json','output_size'):
                with self.subTest(provider=provider,failure=failure), session(provider) as (_state,w):
                    p=w.prepare(DOC,user_text='old-user-canary')
                    finish(w,p,provider)
                    p=w.prepare(DOC,user_text='abandoned-user-canary')
                    if failure=='result_size':
                        out=finish(w,p,provider,calls=[('large','narwhal_document_summary',{})])
                        with self.assertRaisesRegex(base.ai.WorkflowUnavailable,'tool_result_budget'):
                            w.record_results(out['token'],[{'call_id':'large','output':'x'*17000,'is_error':False}],DOC)
                    elif failure=='provider_json':
                        out=w.finish(base.http.TurnTransportResult(p.data['token'],raw='{invalid'),DOC)
                        self.assertEqual(out['status'],'failed')
                    else:
                        out=finish(w,p,provider,text='x'*33000)
                        self.assertEqual(out['status'],'failed')
                    fresh=w.prepare(DOC,user_text='fresh-only')
                    wire=json.dumps(fresh.data['body'])
                    self.assertTrue(all(x not in wire for x in ('old-user-canary','abandoned-user-canary','opaque-replay-canary','prior-answer-canary')),
                                    'failed exchange replay leaked into next turn')
                    self.assertGreater(fresh.data['token']['generation'],p.data['token']['generation'])

    def test_05_result_id_errors_are_precise_retryable_and_do_not_consume_new_requests(self):
        for provider in PROVIDERS:
            with self.subTest(provider=provider), session(provider) as (_state,w):
                p=w.prepare(DOC,user_text='read')
                out=finish(w,p,provider,calls=[('one','narwhal_document_summary',{}),('two','narwhal_selection_summary',{})])
                rows=[{'call_id':name,'output':{},'is_error':False} for name in ('one','two')]
                for bad in (rows[:1],list(reversed(rows)),[rows[0],rows[0]],
                            [dict(rows[0],call_id='foreign'),rows[1]]):
                    with self.assertRaisesRegex(base.ai.WorkflowUnavailable,'tool_result_correlation'):
                        w.record_results(out['token'],bad,DOC)
                with self.assertRaisesRegex(base.ai.WorkflowUnavailable,'stale'):
                    w.record_results(dict(out['token'],request=999),rows,DOC)
                w.record_results(out['token'],rows,DOC)
                fresh=w.prepare(DOC,continuation=True)
                self.assertEqual(w.finish(base.http.TurnTransportResult(p.data['token'],failure='cancelled'),DOC)['status'],'stale')
                self.assertEqual(finish(w,fresh,provider,'ok')['status'],'completed')

    def test_05_old_box_callback_cannot_reset_new_context(self):
        with session('openai_responses') as (_state,w):
            p=w.prepare(DOC,user_text='box')
            out=finish(w,p,'openai_responses',calls=[('box','narwhal_create_box',{'origin':[0,0,0],'size':[2,2,2]})])
            proposal=w.propose_box(out['token'],'box',DOC,box_context())['proposal']
            new_doc='00000000000000000000000000000003'
            fresh=w.prepare(new_doc,user_text='new unrelated question')
            with self.assertRaises(base.ai.WorkflowUnavailable):
                w.decide_box(proposal,DOC,box_context(),accepted=True)
            receipt=base.http.TurnTransportResult(fresh.data['token'],raw=json.dumps(response(message('fresh'))))
            self.assertEqual(w.finish(receipt,new_doc)['status'],'completed')

    def test_06_transport_malformed_bytes_and_late_shutdown_never_publish(self):
        for provider in PROVIDERS:
            for raw,code in ((b'\xff','invalid_utf8'),(b'x'*524289,'response_too_large')):
                with self.subTest(provider=provider,code=code), session(provider) as (_state,w):
                    p=w.prepare(DOC,user_text='abandoned-user-canary')
                    response_fixture=base.Response(raw)
                    receipt=base.http.send_turn(p,{'TEST_KEY':'fixture-key'},opener=lambda *_a,**_k:response_fixture)
                    self.assertEqual(receipt.failure,code)
                    self.assertIsNone(receipt.raw)
                    self.assertTrue(response_fixture.closed)
                    self.assertEqual(w.finish(receipt,DOC)['status'],'transport_failed')
                    fresh=w.prepare(DOC,user_text='fresh-only')
                    self.assertTrue('abandoned-user-canary' not in json.dumps(fresh.data['body']))
                    w.close()
                    self.assertEqual(w.finish(receipt,DOC)['status'],'stale')

    def test_07_public_diagnostics_are_bounded_precise_and_not_connection_state(self):
        with session('openai_responses') as (_state,w):
            p=w.prepare(DOC,user_text='private-prompt-canary')
            self.assertTrue('private-prompt-canary' not in json.dumps(w.public_workflow_state()))
            out=w.finish(base.http.TurnTransportResult(p.data['token'],failure='invalid_utf8'),DOC)
            self.assertEqual(out['code'],'invalid_utf8')
            self.assertEqual(out['diagnostic']['category'],'transport')
            self.assertFalse(out['diagnostic']['automatic_retry'])
            state=w.public_workflow_state()
            self.assertEqual(state['connection_state'],'stateless_http')
            self.assertEqual(state['history_items'],0)
            self.assertEqual(state['turns_spent'],1)
            self.assertTrue('private-prompt-canary' not in json.dumps(state))
            self.assertLess(len(json.dumps(state)),2048)
            p=w.prepare(DOC,user_text='x')
            out=w.finish(base.http.TurnTransportResult(p.data['token'],raw='not-json'),DOC)
            self.assertEqual(out['diagnostic']['category'],'provider_response')
            p=w.prepare(DOC,user_text='x')
            out=finish(w,p,'openai_responses',calls=[('x','not_implemented',{})])
            self.assertEqual(out['diagnostic']['category'],'unsupported')
            p=w.prepare(DOC,user_text='x')
            out=w.finish(base.http.TurnTransportResult(p.data['token'],failure='secret-do-not-echo'),DOC)
            self.assertTrue('secret-do-not-echo' not in json.dumps(out))
            with self.assertRaises(base.ai.WorkflowUnavailable) as failure:
                w.prepare(DOC,user_text='x'*8001)
            self.assertEqual(failure.exception.diagnostic['category'],'local_rejection')

    def test_10_prepared_recipient_cannot_follow_later_config_mutation(self):
        with session('openai_responses') as (state,w):
            prepared=w.prepare(DOC,user_text='private recipient-bound context')
            original=prepared.data['binding']['config']['endpoint']
            state.provider.__dict__['endpoint']='https://other.invalid/v1'
            sent=[]
            receipt=base.http.send_turn(prepared,{'TEST_KEY':'fixture-only'},opener=lambda request,timeout: sent.append(request.full_url) or base.Response(b'{}'))
            self.assertTrue(not sent or sent==[original+'/responses'],'prepared data escaped to another recipient')
        with session('openai_responses') as (_state,w):
            prepared=w.prepare(DOC,user_text='private')
            prepared.config.__dict__['endpoint']='https://other.invalid/v1'
            sent=[]
            receipt=base.http.send_turn(prepared,{'TEST_KEY':'fixture-only'},opener=lambda request,timeout: sent.append(request.full_url) or base.Response(b'{}'))
            self.assertEqual(sent,[],'changed prepared route must be refused before HTTP')
            self.assertEqual(receipt.failure,'request_scope_changed')
            outcome=w.finish(receipt,DOC)
            self.assertEqual(outcome['code'],'request_scope_changed')
            self.assertEqual(outcome['diagnostic']['category'],'stale_context')

    def test_12_catalog_matches_executor_and_instruction_boundary(self):
        for provider in PROVIDERS:
            with self.subTest(provider=provider), session(provider) as (_state,w):
                catalog=w.operation_catalog()
                operations=catalog['operations']['operations']
                self.assertEqual([r['operation'] for r in operations if r['execution_available']],['box'])
                self.assertTrue(next(r for r in operations if r['operation']=='box')['requires_explicit_ui_approval'])
                prepared=w.prepare(DOC,user_text='what can you do?')
                wire=json.dumps(prepared.data['body'])
                self.assertTrue('Production modeling dispatch is currently unavailable' not in wire, 'instructions contradict approved Box executor')
                self.assertIn('separately approved Box/Cube',wire)
                planned=catalog['operations']['planning_operations']
                self.assertTrue(all(not row['execution_available'] for row in planned))
                self.assertEqual({r['operation'] for r in planned},{'curve','loft','pipe','boolean_difference'})
                self.assertEqual(next(r for r in planned if r['operation']=='loft')['unsupported_options'],['Closed=Yes','Point'])
                self.assertEqual(len(catalog['operations']['read_tools']),9)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--library',type=Path,required=True)
    p.add_argument('--test',choices=unittest.defaultTestLoader.getTestCaseNames(LifecycleTests))
    a=p.parse_args();LIBRARY=a.library.resolve()
    suite=unittest.TestSuite([LifecycleTests(a.test)]) if a.test else unittest.defaultTestLoader.loadTestsFromTestCase(LifecycleTests)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
