#!/usr/bin/env python3
"""Finite same-process native review lifecycle/retention qualification.

No Blender, network or geometry. Providers are deterministic wire fixtures; review
contexts and clock advances are injected policy inputs, not document evidence.
Python only drives the existing bounded source-bound native process and reads OS
resource counters. Installed libraries and production catalogs are not changed.
"""
import hashlib
import json
from pathlib import Path
import tempfile
import time
import sys
import unittest
from unittest.mock import patch

import test_ai_sustained_lifecycle as life
from test_ai_accepted_review_native import NativeBridge, discover_executable, _source_hash

ai = life.base.ai


def rss_kib(bridge):
    """Read only our timeout child's native executable; no process control."""
    parent = bridge.process.pid
    children = Path(f'/proc/{parent}/task/{parent}/children').read_text().split()
    if len(children) != 1:
        raise AssertionError('expected exactly one owned native child')
    proc = Path('/proc') / children[0]
    if (proc / 'exe').resolve() != bridge.executable.resolve():
        raise AssertionError('unexpected child executable; refusing resource attribution')
    for line in (proc / 'status').read_text().splitlines():
        if line.startswith('VmRSS:'):
            return int(line.split()[1])
    raise AssertionError('native RSS unavailable')


class Lifetime(unittest.TestCase):
    report = {}

    def test_96_disposals_plateau_in_one_native_process(self):
        executable = discover_executable()
        before = _source_hash()
        samples = []
        start = time.monotonic()
        with NativeBridge(executable) as bridge, patch.object(ai, '_load_workflow_library', return_value=bridge.library):
            bridge.executable = executable
            last_handle = None
            for index in range(96):
                provider = life.PROVIDERS[index % len(life.PROVIDERS)]
                mode = ('reject', 'cancel', 'expire')[index % 3]
                with life.session(provider) as (_state, w):
                    handle = w._handle
                    self.assertNotEqual(handle, last_handle, 'closed handles must never alias a new owner')
                    last_handle = handle
                    p = w.prepare(life.DOC, user_text='finite local lifecycle fixture')
                    out = life.finish(w, p, provider, text='', calls=[
                        ('review', 'narwhal_create_cylinder', dict(center=[1, 2, 3], radius=4, height=7))])
                    self.assertEqual(out['status'], 'tool_calls')
                    current = dict(append=life.box_context(), undo_top_serial=0, undo_next_serial=1)
                    review = w.stage_accepted_review(p.data['token'], 'review', life.DOC, current)['review']
                    self.assertFalse(review['enabled'])
                    self.assertEqual(w.accepted_review_state(p.data['token'], 'review', life.DOC)['retained_reviews'], 1)
                    if mode == 'cancel':
                        w.invalidate()
                        late = life.finish(w, p, provider, text='forged late success')
                        self.assertEqual(late['status'], 'stale')
                        fresh = w.prepare(life.DOC, user_text='fresh only')
                        self.assertNotIn('forged late success', json.dumps(fresh.data['body']))
                        self.assertEqual(life.finish(w, fresh, provider, text='fixture done')['status'], 'completed')
                    else:
                        if mode == 'expire':
                            bridge.rpc(handle, dict(op='fixture_advance_review_ms', delta_ms=300001))
                        else:
                            w.decide_accepted_review(review['token'], review['action'], life.DOC, current, accepted=False)
                        result = w.accepted_review_result(p.data['token'], 'review', life.DOC)['result']
                        self.assertEqual(result['output']['review_state'], 'expired' if mode == 'expire' else 'denied')
                        self.assertFalse(result['output']['created'])
                        self.assertEqual(result['output']['provenance']['source'], 'native_review_resolution')
                        w.record_results(p.data['token'], [result], life.DOC)
                        with self.assertRaises(ai.WorkflowUnavailable):
                            w.record_results(p.data['token'], [result], life.DOC)
                        continuation = w.prepare(life.DOC, continuation=True)
                        self.assertIn('native_review_resolution', json.dumps(continuation.data['body']))
                        self.assertEqual(life.finish(w, continuation, provider, text='fixture done')['status'], 'completed')
                    state = w.public_workflow_state()
                    self.assertEqual(state['pending_tool_count'], 0)
                    self.assertFalse(state['request_pending'])
                    self.assertLessEqual(state['history_items'], 8)
                    self.assertFalse(state['cad_state']['current_geometry_verified'])
                self.assertEqual(w._handle, 0)
                self.assertEqual(bridge.rpc(handle, {'op': 'state'}).get('code'), 'unknown_session')
                samples.append(rss_kib(bridge))
            # Predeclared allocator allowance: after 32 warm-up cycles, last64
            # samples must remain within4MiB; absolute native resident cap64MiB.
            self.assertLessEqual(max(samples[32:]) - min(samples[32:]), 4096)
            self.assertLessEqual(max(samples), 65536)
            self.report.update(cycles=96, providers=list(life.PROVIDERS), modes=['reject', 'cancel', 'expire'],
                native_pid=int(Path(f'/proc/{bridge.process.pid}/task/{bridge.process.pid}/children').read_text().strip()),
                rss_kib=samples, plateau_allowance_kib=4096, absolute_cap_kib=65536,
                native_messages=bridge.messages, process_restarts_during_cycles=0)
        self.assertIsNotNone(bridge.process.returncode)
        self.assertEqual(before, _source_hash(), 'native inputs changed during qualification')
        self.report.update(seconds=round(time.monotonic()-start, 3), native_source_sha256=before,
            executable=str(executable), executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
            process_reaped=True, actual_gui=False, geometry_verified=False, clock_injected=True)


if __name__ == '__main__':
    out = Path(tempfile.mkdtemp(prefix='ar7-life-'))
    if '--mutations' in sys.argv:
        original = ai.WorkflowSession._exchange
        def omit_native_close(self, request):
            if request.get('op') == 'close':
                return {'status': 'closed'}
            return original(self, request)
        with patch.object(ai.WorkflowSession, '_exchange', omit_native_close):
            mutant = unittest.TestResult()
            Lifetime('test_96_disposals_plateau_in_one_native_process').run(mutant)
            if len(mutant.failures) != 1 or mutant.errors:
                raise AssertionError(('close mutation did not assertion-fail', mutant.failures, mutant.errors))
        print('KILLED binding mutation: local handle zero without native disposal', flush=True)
        Lifetime.report = {'mutation': 'omitted_native_close_assertion_killed'}
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Lifetime))
    Lifetime.report['passed'] = result.wasSuccessful()
    (out/'result.json').write_text(json.dumps(Lifetime.report, indent=2))
    print('LIFETIME07_EVIDENCE', out, flush=True)
    raise SystemExit(not result.wasSuccessful())
