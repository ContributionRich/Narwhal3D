#!/usr/bin/env python3
"""Packaging admission tests; fixture bytes are not native runtime qualification."""
from __future__ import annotations

import ast
import inspect
import io
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('stage_observer', ROOT / 'scripts/stage_document_observer.py')
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='observer-package-', dir='/var/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.native, self.app = [self.root / name for name in ('source', 'native', 'app')]
        fields = {}
        for key, name in (
            ('source_sha256', 'native/document_observer/observer.cpp'),
            ('adapter_sha256', 'narwhal3d/native_object_registry.py'),
            ('loader_sha256', 'narwhal3d/native_document_observer.py'),
        ):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
            fields[key] = stage.digest(path)
        directory = self.native / 'document-observer'
        directory.mkdir(parents=True)
        self.library = directory / '_narwhal_document_observer.so'
        self.library.write_bytes(b'packaging fixture; NOT a native library')
        core = self.native / 'libnarwhal_application_core.so'
        core.write_bytes(b'core packaging fixture')
        self.receipt = directory / 'build.json'
        self.record = dict(upstream_sdist_sha256=stage._SDIST, source_sha256=fields['source_sha256'],
            host=dict(version='8.35.0',sha256=stage._HOSTS['linux-x86_64'],cache_tag='cpython-313',soabi='cpython-313-x86_64-linux-gnu',extension='/private/path'),
            entries=[dict(label='control',status='GREEN',sha256=stage.digest(self.library),library='/private/build/path')],
            integration=dict(loader={'status':'GREEN'},registry={'status':'GREEN'},core_sha256=stage.digest(core),
                             adapter_sha256=fields['adapter_sha256'],loader_sha256=fields['loader_sha256']))
        self.save()

    def save(self):
        self.receipt.write_text(json.dumps(self.record))

    def test_pair_is_copied_without_private_build_paths(self):
        stage.stage(self.source, self.native, self.app, 'linux-x86_64')
        destination = self.app / 'narwhal3d/native/document-observer'
        self.assertEqual((destination / self.library.name).read_bytes(), self.library.read_bytes())
        self.assertNotIn('/private/', (destination / 'build.json').read_text())
        with self.assertRaisesRegex(ValueError, 'already exists'):
            stage.stage(self.source, self.native, self.app, 'linux-x86_64')

    def test_changed_binary_refuses(self):
        self.library.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'qualified control'):
            stage.stage(self.source, self.native, self.app, 'linux-x86_64')
        self.assertFalse(self.app.exists())

    def test_unqualified_platform_refuses_before_output(self):
        for platform in ('windows-x86_64', 'macos-arm64'):
            with self.assertRaisesRegex(ValueError, 'not runtime-qualified'):
                stage.stage(self.source, self.native, self.app, platform)
        self.assertFalse(self.app.exists())

    def test_missing_integration_or_changed_source_refuses(self):
        self.record['integration']['loader']['status'] = 'unmeasured'
        self.save()
        with self.assertRaisesRegex(ValueError, 'integration qualification'):
            stage.validate(self.source, self.native, 'linux-x86_64')
        self.record['integration']['loader']['status'] = 'GREEN'
        self.save()
        (self.source / 'narwhal3d/native_object_registry.py').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'source mismatch'):
            stage.validate(self.source, self.native, 'linux-x86_64')

    def test_disabled_package_guards_are_assertion_mutation_kills(self):
        original = stage.validate
        source = inspect.getsource(original)
        for message, case in (
            ('observer binary does not match a qualified control', 'test_changed_binary_refuses'),
            ('observer integration qualification missing', 'test_missing_integration_or_changed_source_refuses'),
        ):
            tree = ast.parse(source)
            replaced = 0
            for node in ast.walk(tree):
                if isinstance(node, ast.If) and any(
                    isinstance(item, ast.Constant) and item.value == message
                    for statement in node.body for item in ast.walk(statement)
                ):
                    node.body = [ast.Pass()]
                    replaced += 1
            self.assertEqual(replaced, 1)
            log = io.StringIO()
            try:
                exec(compile(ast.fix_missing_locations(tree), '<observer-package-mutant>', 'exec'), stage.__dict__)
                result = unittest.TextTestRunner(stream=log).run(unittest.TestSuite([PackageTests(case)]))
            finally:
                stage.validate = original
            self.assertEqual(result.errors, [], log.getvalue())
            self.assertEqual(len(result.failures), 1, log.getvalue())
            print('RED assertion:', case)

    def test_changed_core_pair_refuses(self):
        (self.native / 'libnarwhal_application_core.so').write_bytes(b'different core')
        with self.assertRaisesRegex(ValueError, 'pair differs'):
            stage.validate(self.source, self.native, 'linux-x86_64')


if __name__ == '__main__':
    unittest.main(verbosity=2)
