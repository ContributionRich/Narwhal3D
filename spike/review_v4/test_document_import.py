#!/usr/bin/env python3
"""Linear metadata import preserves errors and custom Python method hooks."""
import ast
import importlib.util
import inspect
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

path = Path(__file__).resolve().parents[2] / "narwhal3d/document_user_text.py"
spec = importlib.util.spec_from_file_location("review_document_text", path)
module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)

class ImportTests(unittest.TestCase):
    def test_import_rows_and_first_error_order(self):
        text = 'Key,Value,Type\na,first,Plain text\nb,second,Plain text\n'
        table = module.Table.import_text(text)
        self.assertEqual([r.key for r in table.rows()], ['a', 'b'])
        with self.assertRaisesRegex(ValueError, "row 4: duplicate.*'a'"):
            module.Table.import_text(text + 'a,duplicate,Plain text\n')
        with self.assertRaisesRegex(ValueError, "row 4: unknown Document User Text type"):
            module.Table.import_text(text + 'a,duplicate,wrong\n')

    def test_subclass_and_overridden_add_are_not_bypassed(self):
        called = []
        class Custom(module.Table):
            def add(self, row):
                called.append(row.key)
                return super().add(row)
        text = 'Key,Value,Type\na,x,Plain text\nb,y,Plain text\n'
        self.assertIsInstance(Custom.import_text(text), Custom)
        self.assertEqual(called, ['a', 'b'])
        called.clear(); original = module.Table.add
        def hook(self, row):
            called.append(row.key)
            return original(self, row)
        with patch.object(module.Table, 'add', hook): module.Table.import_text(text)
        self.assertEqual(called, ['a', 'b'])

    def test_custom_constructor_and_get_hooks_are_preserved(self):
        text = 'Key,Value,Type\na,x,Plain text\nb,y,Plain text\n'
        original = module.Table.__init__
        def initialize(table, **kwargs):
            original(table, [module.Row('a', 'existing')], **kwargs)
        with patch.object(module.Table, '__init__', initialize):
            with self.assertRaisesRegex(ValueError, 'row 2: duplicate'):
                module.Table.import_text(text)
        calls = []
        get = module.Table.get
        def lookup(table, key):
            calls.append(key)
            return get(table, key)
        with patch.object(module.Table, 'get', lookup):
            table = module.Table.import_text(text)
        self.assertEqual(calls, ['a', 'b'])
        self.assertEqual(table.export_text(), text)

    def test_duplicate_guard_mutation_fails(self):
        tree = ast.parse(path.read_text())
        method = next(n for c in tree.body if isinstance(c, ast.ClassDef) and c.name == 'Table'
                      for n in c.body if isinstance(n, ast.FunctionDef) and n.name == 'import_text')
        source = ast.unparse(method).replace('if row.key in seen:', 'if False:')
        ns = dict(module.__dict__); exec(source, ns)
        with patch.object(module.Table, 'import_text', ns['import_text']):
            result = unittest.TestResult(); ImportTests('test_import_rows_and_first_error_order').run(result)
            self.assertFalse(result.wasSuccessful())

if __name__ == '__main__': unittest.main()
