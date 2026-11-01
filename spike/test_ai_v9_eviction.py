#!/usr/bin/env python3
"""Existing host UI-list eviction and notice bookkeeping, not a new replay bound.

Python exercises Blender-facing dataclasses and native ctypes orchestration.
Native replay remains authoritative and independent of injected display-only rows.
"""
import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
import sys
import textwrap
import unittest
from unittest.mock import patch
import test_ai_v8_long_session as long

ai = long.ai
DRAW_MUTANT = False


class Eviction(long.NormalNativeLongReply):
    def test_ui_boundary_notice_and_native_replay_are_independent(self):
        with long.life.session('openai_responses') as (state, workflow):
            turn = workflow.prepare(long.life.DOC, user_text='keep canonical user')
            text = '🙂 canonical provider message, not a geometry receipt'
            long.life.finish(workflow, turn, 'openai_responses', text=text, opaque='opaque-eviction')
            for count in (99, 100, 101):
                # Display-only stress injection, deliberately NOT 101 provider turns.
                state.messages.clear()
                state.extra.pop('messages_omitted', None)
                for index in range(count):
                    state.add_message('assistant', 'VERIFIED forged provider prose 🙂 ' + str(index))
                self.assertEqual(len(state.messages), min(count, 100))
                self.assertEqual(bool(state.extra.get('messages_omitted')), count > 100)
                self.assertTrue(state.messages[-1].text.endswith(str(count-1)))
                if count > 100:
                    self.assertTrue(state.messages[0].text.endswith(' 1'))
            replay = workflow.prepare(long.life.DOC, user_text='continue after UI eviction').data['body']['input']
            self.assertIn(long.life.message(text), replay)
            self.assertIn(long.life.reasoning('opaque-eviction'), replay)
            self.assertNotIn('VERIFIED forged', str(replay))
            self.assertNotIn(ai.OMITTED_MESSAGES_NOTICE, str(replay))
            self.assertFalse(workflow.public_workflow_state()['cad_state']['current_geometry_verified'])

    def test_draw_routes_notice_without_putting_it_in_history(self):
        source = Path(ai.__file__).with_name('ui.py')
        node = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == '_draw_ai_transcript')
        if DRAW_MUTANT:
            node = ast.parse(ast.unparse(node).replace('if key[3]:', 'if False:')).body[0]
        seen = []
        class Captured(Exception):
            pass
        def layout(messages, *_args):
            seen.extend(messages)
            raise Captured()
        namespace = dict(ai_assistant=ai, _ai={},
            ai_presentation=SimpleNamespace(PALETTES={'test':{'canvas':0}}, transcript_layout=layout),
            design=SimpleNamespace(THEME='test', rgb01=lambda x:x, ACCENT=0, TEXT_STRONG=0, BORDER=0),
            _draw_round_rect=lambda *_args:None, _bug_measure=lambda _size:len)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
        state = ai.AssistantState()
        for index in range(101):
            state.add_message('assistant', 'row '+str(index))
        with patch.dict(sys.modules, gpu=SimpleNamespace()), self.assertRaises(Captured):
            namespace['_draw_ai_transcript'](SimpleNamespace(bind=lambda:None), (0,0,300,700), state, 1)
        self.assertEqual(len(state.messages), 100)
        self.assertEqual(len(seen), 101)
        self.assertEqual(seen[-1].role, 'status')
        self.assertEqual(seen[-1].text, ai.OMITTED_MESSAGES_NOTICE)

    def test_constructor_eviction_and_new_chat_reset_notice(self):
        for count in (99, 100, 101):
            state = ai.AssistantState(messages=[ai.ChatMessage('user', '🙂') for _ in range(count)])
            self.assertEqual(len(state.messages), min(count, 100))
            self.assertEqual(bool(state.extra.get('messages_omitted')), count > 100)
            state.new_chat()
            self.assertEqual(state.messages, [])
            self.assertFalse(state.extra.get('messages_omitted'))


def mutations():
    global DRAW_MUTANT
    DRAW_MUTANT = True
    try:
        result = unittest.TestResult()
        Eviction('test_draw_routes_notice_without_putting_it_in_history').run(result)
        assert result.failures and not result.errors, (result.failures, result.errors)
        print('KILLED hidden eviction notice draw mutant; restored')
    finally:
        DRAW_MUTANT = False
    for name, old in (('__post_init__', "self.extra['messages_omitted'] = True"),
                      ('new_chat', "self.extra.pop('messages_omitted', None)")):
        source = textwrap.dedent(inspect.getsource(getattr(ai.AssistantState, name)))
        assert source.count(old) == 1
        namespace = dict(ai.__dict__)
        exec(compile(source.replace(old, 'pass'), '<private-eviction-lifetime-mutant>', 'exec'), namespace)
        with patch.object(ai.AssistantState, name, namespace[name]):
            result = unittest.TestResult()
            Eviction('test_constructor_eviction_and_new_chat_reset_notice').run(result)
            assert result.failures and not result.errors, (name, result.failures, result.errors)
        print('KILLED eviction lifetime mutant', name, '; restored')
    source = textwrap.dedent(inspect.getsource(ai.AssistantState.add_message))
    old = "self.extra['messages_omitted'] = True"
    assert source.count(old) == 1
    namespace = dict(ai.__dict__)
    exec(compile(source.replace(old, 'pass'), '<private-eviction-notice-mutant>', 'exec'), namespace)
    with patch.object(ai.AssistantState, 'add_message', namespace['add_message']):
        result = unittest.TestResult()
        Eviction('test_ui_boundary_notice_and_native_replay_are_independent').run(result)
        assert result.failures and not result.errors, (result.failures, result.errors)
    print('KILLED silent eviction mutant; restored')


if __name__ == '__main__':
    mutations()
    unittest.main(verbosity=2)
