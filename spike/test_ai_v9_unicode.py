#!/usr/bin/env python3
"""Actual assistant-only Blender modal branch with injected event objects."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'narwhal3d/ui.py'
MUTANT = False


class UnicodeRouting(unittest.TestCase):
    def test_unicode_and_modifier_routing(self):
        tree = ast.parse(SOURCE.read_text())
        nodes = [node for node in ast.walk(tree) if isinstance(node, ast.If)
                 and ast.unparse(node.test).startswith("_ai['state'].focus and ai_panel_open()")]
        self.assertEqual(len(nodes), 1)
        code = ast.unparse(nodes[0])
        if MUTANT:
            code = code.replace('event.unicode', 'event.ascii')
        calls = []
        namespace = dict(_ai={'state': SimpleNamespace(focus=True, extra={})},
                         ai_panel_open=lambda: True,
                         ai_panel_key=lambda *args, **kwargs: calls.append((args, kwargs)))
        exec('def route(event, context):\n' + '\n'.join('    '+line for line in code.splitlines()), namespace)
        context = SimpleNamespace(window_manager=SimpleNamespace(clipboard='disposable'))
        for char in ('a', 'Ω', '🙂', '界'):
            event = SimpleNamespace(type='A', value='PRESS', unicode=char,
                                    ascii=char if char.isascii() else '', ctrl=False, alt=False, shift=False)
            self.assertEqual(namespace['route'](event, context), {'RUNNING_MODAL'})
            self.assertEqual(calls[-1][0], ('char', char))
        event.ctrl = True
        namespace['route'](event, context)
        self.assertEqual(calls[-1][0][0], 'select_all')
        event.type = 'V'
        namespace['route'](event, context)
        self.assertEqual(calls[-1][0], ('paste', 'disposable'))
        event.ctrl = False
        event.alt = True
        before = len(calls)
        self.assertIsNone(namespace['route'](event, context))
        self.assertEqual(len(calls), before)


if __name__ == '__main__':
    MUTANT = True
    try:
        result = unittest.TestResult()
        UnicodeRouting('test_unicode_and_modifier_routing').run(result)
        assert result.failures and not result.errors, (result.failures, result.errors)
        print('KILLED ASCII-only assistant routing mutant')
    finally:
        MUTANT = False
    unittest.main(verbosity=2)
