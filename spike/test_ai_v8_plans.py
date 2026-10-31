#!/usr/bin/env python3
"""Round07 cross-provider plan revisions through the real Rust workflow.

Deterministic local wire fixtures, synthetic host decisions/contexts, no HTTP or
geometry. Draft revisions are not document revisions or execution permission.
"""
from copy import deepcopy
import unittest
from unittest.mock import patch

import test_ai_plan as plans
import test_ai_sustained_lifecycle as life
from test_ai_accepted_review_native import NativeBridge, discover_executable

ai = life.base.ai


def fixtures():
    step, plan = plans.step, plans.plan
    enclosure = plan(step('body'), step('cutter', arguments=dict(origin=[2, 2, 1], size=[16, 6, 5])),
                     step('hollow', 'boolean_difference', {}, inputs=[dict(kind='step', id=k) for k in ('body', 'cutter')],
                          dependencies=['body', 'cutter']),
                     step('finish', 'unsupported', dict(need='Wall-thickness and hole-pattern verification are not performed'),
                          dependencies=['hollow']))
    enclosure['goal'] = 'Enclosure, with explicitly specified fixture dimensions'
    dolphin = plans.dolphin_plan()
    cover = deepcopy(dolphin)
    cover['goal'] = 'Lofted cover; closure and wall thickness are still unresolved'
    cover['steps'][-1]['action']['arguments']['need'] = 'Close and thicken the lofted cover with exact surfaces; executor unavailable'
    pipe = plan(step('rail', 'curve', dict(points=[[0, 0, 0], [8, 12, 0], [20, 15, 0]]), 'curve'),
                step('pipe', 'pipe', dict(radius=2, cap='Flat'), inputs=[dict(kind='step', id='rail')], dependencies=['rail']))
    pipe['goal'] = 'Bent pipe, not executed'
    return dict(enclosure=enclosure, dolphin=dolphin, cover=cover, pipe=pipe)


class PlanRevisions(unittest.TestCase):
    def test_revised_constraints_retire_real_accepted_reviews_across_providers(self):
        cells = 0
        with NativeBridge(discover_executable()) as bridge, patch.object(ai, '_load_workflow_library', return_value=bridge.library):
            for provider in life.PROVIDERS:
                for name, original in fixtures().items():
                    for change in ('dimension', 'units', 'questions'):
                        with self.subTest(provider=provider, case=name, change=change), life.session(provider) as (_state, w):
                            p = w.prepare(life.DOC, user_text='deterministic plan revision fixture')
                            reply = life.finish(w, p, provider, calls=[('one', 'narwhal_create_cylinder',
                                                                        dict(center=[1, 2, 3], radius=4, height=7))])
                            self.assertEqual(reply['status'], 'tool_calls')
                            current = dict(append=life.box_context(), undo_top_serial=0, undo_next_serial=1)
                            review = w.stage_accepted_review(p.data['token'], 'one', life.DOC, current)['review']
                            draft = w._exchange(dict(op='stage_plan_draft', binding=w._binding(), plan=original))
                            approved_plan = w._exchange(dict(op='review_plan_draft', binding=w._binding(),
                                                             ticket=draft['ticket'], accepted=True))
                            self.assertTrue(approved_plan['plan_reviewed'])
                            self.assertFalse(approved_plan['action_approval'])
                            self.assertIsNone(approved_plan['document_revision'])
                            with self.assertRaises(ai.WorkflowUnavailable):
                                w.consume_accepted_review(review['token'], life.DOC, current)
                            w.decide_accepted_review(review['token'], review['action'], life.DOC, current, accepted=True)
                            revised = deepcopy(original)
                            if change == 'units':
                                revised['units'] = 'Inches'
                            elif change == 'questions':
                                revised['questions'] = ['The new overall length conflicts with the old dimensions. Which should be retained?']
                            elif name == 'enclosure':
                                revised['steps'][0]['action']['arguments']['size'][0] = 30
                            else:
                                revised['steps'][0]['action']['arguments']['points'][1][1] += 2
                            result = w._exchange(dict(op='revise_plan_draft', binding=w._binding(),
                                                      ticket=draft['ticket'], plan=revised))
                            all_ids = {step['id'] for step in original['steps']}
                            independent = {'enclosure': {'cutter'}, 'dolphin': {'section1'}, 'cover': {'section1'}, 'pipe': set()}
                            expected = all_ids - independent[name] if change == 'dimension' else all_ids
                            self.assertEqual(set(result['invalidated_steps']), expected)
                            self.assertEqual(result['plan'], revised)
                            self.assertEqual(result['ticket']['revision'], 2)
                            self.assertEqual(result['revision_kind'], 'planning_draft_only')
                            self.assertIsNone(result['document_revision'])
                            self.assertFalse(result['action_approval'])
                            self.assertFalse(result['plan_reviewed'])
                            self.assertTrue(result['outstanding_actions_abandoned'])
                            with self.assertRaises(ai.WorkflowUnavailable):
                                w.consume_accepted_review(review['token'], life.DOC, current)
                            with self.assertRaisesRegex(ai.WorkflowUnavailable, 'stale_plan_revision'):
                                w._exchange(dict(op='review_plan_draft', binding=w._binding(), ticket=draft['ticket'], accepted=True))
                            state = w.public_workflow_state()
                            self.assertEqual(state['pending_tool_count'], 0)
                            self.assertFalse(state['cad_state']['current_geometry_verified'])
                            cells += 1
        self.assertEqual(cells, 48)
        print('PASS48 typed revision cells; no geometry or live LLM-quality claim')

    def test_revision_budget_is_bounded_and_invalid_revision_preserves_draft(self):
        with life.session('openai_responses') as (_state, w):
            original = plans.dolphin_plan()
            draft = w._exchange(dict(op='stage_plan_draft', binding=w._binding(), plan=original))
            invalid = deepcopy(original); invalid['steps'][0]['action']['arguments']['points'][0][0] = 1e9
            with self.assertRaisesRegex(ai.WorkflowUnavailable, 'invalid_plan_points'):
                w._exchange(dict(op='revise_plan_draft', binding=w._binding(), ticket=draft['ticket'], plan=invalid))
            # The same ticket is still usable: failed validation did not increment it.
            for revision in range(2, 9):
                revised = deepcopy(original); revised['questions'] = ['Confirm length revision ' + str(revision) + '?']
                draft = w._exchange(dict(op='revise_plan_draft', binding=w._binding(), ticket=draft['ticket'], plan=revised))
                self.assertEqual(draft['ticket']['revision'], revision)
                self.assertFalse(draft['action_approval'])
            with self.assertRaisesRegex(ai.WorkflowUnavailable, 'plan_revision_limit'):
                w._exchange(dict(op='revise_plan_draft', binding=w._binding(), ticket=draft['ticket'], plan=original))
            self.assertFalse(w.public_workflow_state()['cad_state']['current_geometry_verified'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
