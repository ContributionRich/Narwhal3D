"""Actual AppState + unchanged consumer function bodies (headless, NOT GUI acceptance)."""
import ast
import gc
import hashlib
import importlib
import os
from pathlib import Path

import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType('narwhal3d')
pkg.__path__ = [str(ROOT / 'narwhal3d')]
sys.modules['narwhal3d'] = pkg
state = importlib.import_module('narwhal3d.state')
cmdbar = importlib.import_module('narwhal3d.cmdbar')
native = importlib.import_module('narwhal3d.native_transcript')


def function(path, name, namespace):
    tree = ast.parse(path.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name]


class ConsumerTests(unittest.TestCase):
    def setUp(self):
        self.st = state.AppState()
        self.addCleanup(self.st.history.close)
        self.assertIsNotNone(self.st.history._native)

    def test_real_state_initialization_log_and_new_model(self):
        st = self.st
        owner, handle = st.history, st.history._native.reply.handle
        for i in range(205): st.history.append(f'> {i}\n')
        self.assertEqual(len(st.history), 205) # direct append is NOT capped
        st.log('log')
        self.assertEqual(st.history[:], [f'> {i}\n' for i in range(6, 205)] + ['log'])
        st.set_model(state.NarwhalModel())
        self.assertIs(st.history, owner)
        self.assertEqual(st.history._native.reply.handle, handle)
        self.assertEqual(st.history[-1], 'log')
        other = state.AppState()
        self.addCleanup(other.history.close)
        self.assertEqual(len(other.history), 0)
        self.assertNotEqual(other.history._native.reply.handle, handle)

    def test_state_lease_preserves_all_other_ast(self):
        # Golden normalized AST digest measured from 50267dd6 before retirement.
        # Retains whole-file lease coverage without requiring Git/retired source.
        tree = ast.parse((ROOT / 'narwhal3d/state.py').read_text())
        def normalized(tree):
            tree.body = [n for n in tree.body if not (isinstance(n, ast.ImportFrom) and n.module == 'command_transcript')]
            cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AppState')
            init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
            def history_assignment(n):
                targets = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, ast.AnnAssign) else []
                return any(isinstance(t, ast.Attribute) and t.attr == 'history' for t in targets)
            init.body = [n for n in init.body if not history_assignment(n)]
            return ast.dump(tree, include_attributes=False)
        self.assertEqual(hashlib.sha256(normalized(tree).encode()).hexdigest(),
                         '9a1ed58cf733ca026b8f94b5def179ec363f221963499244f66b890d05c096c0')

    def test_visible_completion_and_recent_bug_context(self):
        st = self.st
        st.history.append_many([f'line{i}' for i in range(220)])
        self.assertEqual(cmdbar.visible_history(st.history, 3), ['line217', 'line218', 'line219'])
        self.assertEqual(cmdbar.visible_history(st.history, 0), [])
        ns = dict(cmdbar=cmdbar, _completion_names_for=lambda st: ['Line', 'Loft'],
                  _completion_is_divider=lambda name: False)
        completion = function(ROOT / 'narwhal3d/ui.py', '_completion_rows_for', ns)
        self.assertEqual(completion(st, 120), cmdbar.completion_rows_for_bar(['Line', 'Loft'], 120, 220))
        ns = dict(app_state=st, selected_display_names=lambda: [], active_pane_area=lambda: None,
                  _view3d_areas=lambda: [], _ROOT=str(ROOT), bpy=types.SimpleNamespace(context=types.SimpleNamespace(window=None)))
        context = function(ROOT / 'narwhal3d/ui.py', '_bug_context', ns)
        with patch('subprocess.run', return_value=types.SimpleNamespace(stdout='test-build')):
            rows, _ = context()
        self.assertEqual(dict(rows)['Recent command line (oldest first)'], [f'line{i}' for i in range(205, 220)])

    def test_unchanged_recall_body_and_direct_ui_edge_append(self):
        st = self.st
        st.history.append_many(['> First', 'output', '> Last', 'tail'])
        tree = ast.parse((ROOT / 'narwhal3d/ui.py').read_text())
        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and isinstance(node.test, ast.BoolOp) and 'UP_ARROW' in ast.unparse(node.test):
                if any(isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'prev' for t in n.targets) for n in node.body):
                    target = node
                    break
        self.assertIsNotNone(target)
        start = next(i for i, n in enumerate(target.body) if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'prev')
        body = target.body[start:start+2]
        exec(compile(ast.Module(body=body, type_ignores=[]), 'ui:unchanged-recall-body', 'exec'),
             dict(st=st, self=types.SimpleNamespace(_redraw=lambda: None), _set_completion_index=lambda i: None))
        self.assertEqual(st.input_buffer, 'Last')
        self.assertEqual(st.history.last_command(), st.input_buffer)
        ns = dict(coords=types.SimpleNamespace(format_world=lambda p: '1,2,3'))
        ui_append = function(ROOT / 'narwhal3d/ui.py', '_feed_constrained_point', ns)
        fed = []
        ui_append(types.SimpleNamespace(history=st.history, feed_line=fed.append), (1, 2, 3))
        self.assertEqual(st.history[-1], '> 1,2,3')
        edge_st = types.SimpleNamespace(history=st.history, input_buffer='4.5', feed_line=fed.append,
            active_command=types.SimpleNamespace(handle_input_active=lambda: True))
        ns = dict(_drag=None, _numeric=None, _ui=lambda: types.SimpleNamespace(app_state=edge_st), _drop_drag=lambda: None)
        event = function(ROOT / 'narwhal3d/edge_handles.py', 'handle_event', ns)
        self.assertTrue(event(types.SimpleNamespace(type='RET', value='PRESS')))
        self.assertEqual(st.history[-1], '> 4.5')
        self.assertEqual(fed, ['1,2,3', '4.5'])

    def test_appstate_collection_releases_owner(self):
        import weakref
        transient = state.AppState()
        handle = transient.history._native.reply.handle
        ref = weakref.ref(transient.history._native)
        del transient
        gc.collect()
        self.assertIsNone(ref())
        self.assertEqual(native.library().narwhal_transcript_close(handle), 2)


if __name__ == '__main__':
    unittest.main()
