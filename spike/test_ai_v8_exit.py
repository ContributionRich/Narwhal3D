#!/usr/bin/env python3
"""Actual exit/shutdown function bodies with fake Blender services, not OS evidence.

Python is required for bpy exit_pre integration. Mutations run in private AST
namespaces; never modify shared production files or exercise another window.
"""
import ast
import contextlib
import io
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'narwhal3d/ui.py'
MUTATION = None


class ExitCleanup(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(SOURCE.read_text())
        names = {'_exit_handler', '_ai_shutdown', '_ai_invalidate'}
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        self.assertEqual({n.name for n in nodes}, names)
        source = ast.unparse(ast.Module(body=nodes, type_ignores=[]))
        if MUTATION:
            old, new = MUTATION
            self.assertEqual(source.count(old), 1)
            source = source.replace(old, new)
        self.events = []
        self.extra = {}
        self.workflow = SimpleNamespace(invalidate=lambda reason: self.events.append('invalidate'), close=self.close)
        self.extra['workflow'] = self.workflow
        self.state = {'state': SimpleNamespace(extra=self.extra)}
        for key in ('transport_output', 'transport_thread', 'read_grant', 'consent_scope', 'box_pending',
                    'action_pending', 'review_batch', 'review_view', 'review_drawn_pending', 'review_layout_key',
                    'model_output', 'model_thread', 'document_identity', 'document_context', 'workflow_scope'):
            self.state[key] = object()
        self.poll, self.models = object(), object()
        self.timers = {self.poll, self.models}
        self.package = ModuleType('exit_fixture')
        self.package.file_commands = SimpleNamespace(write_recovery=self.recover)
        self.g = dict(__name__='exit_fixture.ui', __package__='exit_fixture', _ai=self.state,
                      ai_assistant=SimpleNamespace(WorkflowSession=SimpleNamespace(_main_thread=lambda: None),
                                                   WorkflowUnavailable=RuntimeError),
                      bpy=SimpleNamespace(app=SimpleNamespace(timers=SimpleNamespace(
                          is_registered=self.timers.__contains__, unregister=self.timers.remove))),
                      _ai_poll_transport=self.poll, _ai_poll_model_discovery=self.models,
                      user_preference_snapshot=lambda: {'theme': 'dark'},
                      _remember_preferences=lambda snapshot: self.events.append(('preferences', snapshot)))
        exec(compile(source, str(SOURCE), 'exec'), self.g)

    def close(self):
        self.events.append('close')
        self.extra.pop('workflow', None)

    def recover(self):
        self.events.append('recovery')
        return '/disposable/recovery.narwhal'

    def exit(self):
        with patch.dict(sys.modules, exit_fixture=self.package), contextlib.redirect_stdout(io.StringIO()) as output:
            self.g['_exit_handler'](None)
        return output.getvalue()

    def test_generic_exit_retires_assistant_before_preferences_and_recovery(self):
        output = self.exit()
        self.assertEqual(self.events, ['invalidate', 'close', ('preferences', {'theme': 'dark'}), 'recovery'])
        self.assertEqual(self.state, {'state': self.state['state']})
        self.assertEqual(self.extra, {})
        self.assertEqual(self.timers, set())
        self.assertIn('unsaved changes kept in /disposable/recovery.narwhal', output)

    def test_cleanup_exception_cannot_suppress_preferences_or_recovery(self):
        def fail():
            self.events.append('cleanup_failed')
            raise ValueError('PRIVATE-PROVIDER-CANARY')
        self.g['_ai_shutdown'] = fail
        output = self.exit()
        self.assertEqual(self.events, ['cleanup_failed', ('preferences', {'theme': 'dark'}), 'recovery'])
        self.assertIn('ValueError', output)
        self.assertNotIn('PRIVATE-PROVIDER-CANARY', output)

    def test_exit_and_unregister_cleanup_are_idempotent_in_either_order(self):
        for exit_first in (True, False):
            with self.subTest(exit_first=exit_first):
                self.setUp()
                if exit_first:
                    self.exit()
                    self.g['_ai_shutdown']()
                else:
                    self.g['_ai_shutdown']()
                    self.exit()
                self.exit()
                self.assertEqual(self.events.count('close'), 1)
                self.assertEqual(self.events.count('recovery'), 2)
                self.assertEqual(self.extra, {})
                self.assertEqual(self.timers, set())
                self.assertEqual(set(self.state), {'state'})

    def test_native_invalidation_failure_still_disposes_and_detaches(self):
        def fail(_reason):
            raise RuntimeError('native unavailable')
        self.workflow.invalidate = fail
        self.exit()
        self.assertEqual(self.events, ['close', ('preferences', {'theme': 'dark'}), 'recovery'])
        self.assertEqual(self.extra, {})
        self.assertEqual(self.timers, set())
        self.assertEqual(set(self.state), {'state'})


def mutations():
    global MUTATION
    variants = [
        ('missing_exit_hook', '        _ai_shutdown()', '        pass',
         'test_generic_exit_retires_assistant_before_preferences_and_recovery'),
        ('suppressed_recovery', "    except Exception as exc:\n", "    except Exception as exc:\n        return\n",
         'test_cleanup_exception_cannot_suppress_preferences_or_recovery'),
        ('missing_timer_removal', '            bpy.app.timers.unregister(callback)', '            pass',
         'test_generic_exit_retires_assistant_before_preferences_and_recovery'),
    ]
    for label, old, new, test in variants:
        MUTATION = (old, new)
        try:
            result = unittest.TestResult()
            ExitCleanup(test).run(result)
            assert result.failures and not result.errors, (label, result.failures, result.errors)
            print('KILLED', label)
        finally:
            MUTATION = None
    return unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ExitCleanup)).wasSuccessful()


if __name__ == '__main__':
    if '--mutations' in sys.argv:
        raise SystemExit(not mutations())
    unittest.main(verbosity=2)
