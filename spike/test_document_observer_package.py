#!/usr/bin/env python3
"""Packaging admission tests; fixture bytes are not native runtime qualification."""
from __future__ import annotations

import ast
import inspect
import io
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = '/var/tmp' if Path('/var/tmp').is_dir() else None
spec = importlib.util.spec_from_file_location('stage_observer', ROOT / 'scripts/stage_document_observer.py')
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='observer-package-', dir=SCRATCH)
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

    def lifetime_proof(self):
        return dict(abi=1, platform='linux-x86_64', library_sha256=stage.digest(self.library),
                    host_sha256=stage._HOSTS['linux-x86_64'],
                    source_sha256=self.record['source_sha256'],
                    loader_sha256=self.record['integration']['loader_sha256'],
                    checks=dict(stage._LIFETIME_CHECKS))

    def test_matching_lifetime_proof_survives_compaction(self):
        self.record['master_lifetime'] = self.lifetime_proof()
        self.save()
        stage.stage(self.source, self.native, self.app, 'linux-x86_64')
        compact = json.loads((self.app / 'narwhal3d/native/document-observer/build.json').read_text())
        self.assertEqual(compact.get('master_lifetime'), self.record['master_lifetime'])

    def test_absent_proof_stays_conservative(self):
        stage.stage(self.source, self.native, self.app, 'linux-x86_64')
        compact = json.loads((self.app / 'narwhal3d/native/document-observer/build.json').read_text())
        self.assertNotIn('master_lifetime', compact)

    def test_tampered_or_partial_lifetime_proof_refuses(self):
        import copy
        proof = self.lifetime_proof()
        for key in proof:
            for removal in (True, False):
                with self.subTest(key=key, removal=removal):
                    changed = copy.deepcopy(proof)
                    if removal: del changed[key]
                    else: changed[key] = 'tampered'
                    self.record['master_lifetime'] = changed
                    self.save()
                    with self.assertRaisesRegex(ValueError, 'master lifetime qualification'):
                        stage.validate(self.source, self.native, 'linux-x86_64')
        for key in proof['checks']:
            changed = copy.deepcopy(proof)
            del changed['checks'][key]
            self.record['master_lifetime'] = changed
            self.save()
            with self.assertRaisesRegex(ValueError, 'master lifetime qualification'):
                stage.validate(self.source, self.native, 'linux-x86_64')
        self.assertFalse(self.app.exists())

    def test_windows_cannot_package_lifetime_proof(self):
        directory, core = self.windows_pair()
        receipt = directory / 'build.json'
        record = json.loads(receipt.read_text())
        record['master_lifetime'] = self.lifetime_proof()
        receipt.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, 'master lifetime qualification'):
            stage.validate(self.source, self.native, 'windows-x86_64',
                           observer_directory=directory, core_path=core)

    def test_lifetime_staging_mutations_are_red(self):
        original = stage.validate_lifetime
        stage.validate_lifetime = lambda *args: None
        try:
            result = unittest.TextTestRunner(stream=io.StringIO()).run(unittest.TestSuite([
                PackageTests('test_tampered_or_partial_lifetime_proof_refuses')]))
            self.assertFalse(result.errors)
            self.assertTrue(result.failures)
        finally:
            stage.validate_lifetime = original
        print('RED assertion: tampered lifetime proof guard removed')
        original_stage = stage.stage
        source = inspect.getsource(original_stage)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and ast.unparse(node.test) == "'master_lifetime' in record":
                node.body = [ast.Pass()]
        try:
            exec(compile(ast.fix_missing_locations(tree), '<dropped-lifetime-proof>', 'exec'), stage.__dict__)
            result = unittest.TextTestRunner(stream=io.StringIO()).run(unittest.TestSuite([
                PackageTests('test_matching_lifetime_proof_survives_compaction')]))
            self.assertEqual(result.errors, [])
            self.assertEqual(len(result.failures), 1)
        finally:
            stage.stage = original_stage
        print('RED: valid lifetime proof dropped during compaction')

    def test_changed_binary_refuses(self):
        self.library.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'qualified control'):
            stage.stage(self.source, self.native, self.app, 'linux-x86_64')
        self.assertFalse(self.app.exists())

    def test_unqualified_platform_refuses_before_output(self):
        for platform in ('freebsd-x86_64', 'macos-arm64'):
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

    def windows_pair(self):
        directory = self.root / 'windows-observer'
        directory.mkdir()
        library = directory / '_narwhal_document_observer.pyd'
        library.write_bytes(b'windows packaging fixture; NOT a native library')
        core = self.root / 'runtime/narwhal_application_core.dll'
        core.parent.mkdir(parents=True)
        core.write_bytes(b'windows core packaging fixture')
        record = dict(upstream_sdist_sha256=stage._SDIST, source_sha256=self.record['source_sha256'],
            host=dict(version='8.35.0', sha256=stage._HOSTS['windows-x86_64'], cache_tag='cpython-313',
                      soabi='cp313-win_amd64', extension='C:/private/path'),
            entries=[dict(label='control', status='GREEN', sha256=stage.digest(library), library='C:/private/build')],
            integration=dict(loader={'status': 'GREEN'}, registry={'status': 'GREEN'},
                             core_sha256=stage.digest(core), adapter_sha256=self.record['integration']['adapter_sha256'],
                             loader_sha256=self.record['integration']['loader_sha256']))
        (directory / 'build.json').write_text(json.dumps(record))
        return directory, core

    def test_hosts_and_names_cover_exactly_linux_and_windows(self):
        self.assertEqual(stage._HOSTS, {
            'linux-x86_64': 'd5fed4447838a0645bfc82cf6ccc684e1c6ca6d4568533e044adf9754de169d4',
            'windows-x86_64': '1b59225812361ffd89e48eb1caf64a9f505aa1613da2058479b0b766fa976ce7'})
        self.assertEqual(stage._LIBRARY, {'linux-x86_64': '_narwhal_document_observer.so',
                                          'windows-x86_64': '_narwhal_document_observer.pyd'})
        self.assertEqual(stage._CORE, {'linux-x86_64': 'libnarwhal_application_core.so',
                                       'windows-x86_64': 'narwhal_application_core.dll'})

    def test_linux_defaults_still_read_the_native_directory(self):
        library, _ = stage.validate(self.source, self.native, 'linux-x86_64')
        self.assertEqual(library, self.native / 'document-observer' / '_narwhal_document_observer.so')

    def test_windows_defaults_read_the_native_directory_and_core_name(self):
        directory, core = self.windows_pair()
        target = self.native / 'document-observer'
        shutil.copy2(directory / '_narwhal_document_observer.pyd', target / '_narwhal_document_observer.pyd')
        shutil.copy2(directory / 'build.json', target / 'build.json')
        shutil.copy2(core, self.native / 'narwhal_application_core.dll')
        library, _ = stage.validate(self.source, self.native, 'windows-x86_64')
        self.assertEqual(library, target / '_narwhal_document_observer.pyd')

    def test_windows_default_refuses_when_pyd_is_absent(self):
        with self.assertRaisesRegex(ValueError, 'missing regular document observer input: _narwhal_document_observer.pyd'):
            stage.validate(self.source, self.native, 'windows-x86_64')

    def test_windows_pair_stages_from_explicit_directories(self):
        directory, core = self.windows_pair()
        result = stage.stage(self.source, self.native, self.app, 'windows-x86_64',
                             observer_directory=directory, core_path=core)
        destination = self.app / 'narwhal3d/native/document-observer'
        self.assertEqual(result['sha256'], stage.digest(directory / '_narwhal_document_observer.pyd'))
        self.assertEqual((destination / '_narwhal_document_observer.pyd').read_bytes(),
                         (directory / '_narwhal_document_observer.pyd').read_bytes())
        self.assertFalse((destination / '_narwhal_document_observer.so').exists())
        self.assertNotIn('C:/private', (destination / 'build.json').read_text())

    def test_windows_receipt_with_linux_host_refuses(self):
        directory, core = self.windows_pair()
        record = json.loads((directory / 'build.json').read_text())
        record['host']['sha256'] = stage._HOSTS['linux-x86_64']
        (directory / 'build.json').write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, 'dependency/host pair'):
            stage.validate(self.source, self.native, 'windows-x86_64', observer_directory=directory, core_path=core)

    def test_windows_core_override_must_match_the_qualified_core(self):
        directory, core = self.windows_pair()
        core.write_bytes(b'different core')
        with self.assertRaisesRegex(ValueError, 'pair differs'):
            stage.validate(self.source, self.native, 'windows-x86_64', observer_directory=directory, core_path=core)


if __name__ == '__main__':
    unittest.main(verbosity=2)
