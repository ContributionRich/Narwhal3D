#!/usr/bin/env python3
"""Source-integrity and bounded-build controls for the document observer gate."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import os
from pathlib import Path
import ssl
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('observer_gate', ROOT / 'scripts/check_document_observer.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='observer-gate-', dir='/var/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / 'source.tar.gz'
        self.tree = self.root / 'source'
        self.path = self.tree / 'src/bindings/fixture.h'
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b'qualified source\n')
        with tarfile.open(self.archive, 'w:gz') as archive:
            info = tarfile.TarInfo('rhino3dm-8.35.0/src/bindings/fixture.h')
            info.size = self.path.stat().st_size
            archive.addfile(info, io.BytesIO(self.path.read_bytes()))
        self.pin = hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def test_matching_archive_and_extracted_source(self):
        with patch.object(gate, 'SDIST_SHA256', self.pin):
            gate.verify_sources(self.archive, self.tree)

    def test_wrong_archive_refuses(self):
        with patch.object(gate, 'SDIST_SHA256', '0' * 64):
            with self.assertRaisesRegex(RuntimeError, 'archive hash mismatch'):
                gate.verify_sources(self.archive, self.tree)

    def test_changed_extracted_source_refuses(self):
        self.path.write_bytes(b'unreviewed source\n')
        with patch.object(gate, 'SDIST_SHA256', self.pin):
            with self.assertRaisesRegex(RuntimeError, 'modified/missing'):
                gate.verify_sources(self.archive, self.tree)

    def test_compiler_has_memory_time_and_shared_lock_bounds(self):
        with patch.object(gate.subprocess, 'run') as run:
            gate.run(['compiler', 'input'], seconds=30)
        args, kwargs = run.call_args
        command = args[0]
        self.assertEqual(command[:4], ['flock', '-w', '120', '/tmp/narwhal3d-heavy-build.lock'])
        self.assertIn('MemoryMax=1024M', command)
        self.assertIn('MemorySwapMax=0', command)
        self.assertIn('30s', command)
        self.assertEqual(kwargs['env']['CARGO_BUILD_JOBS'], '1')
        self.assertEqual(kwargs['timeout'], 170)
        self.assertTrue(kwargs['check'])

    def test_disabled_integrity_guards_are_assertion_mutation_kills(self):
        import inspect
        original = gate.verify_sources
        source = inspect.getsource(original)
        for old, case in (
            ('if sha(archive) != SDIST_SHA256:', 'test_wrong_archive_refuses'),
            ('if not path.is_file() or sha(path) != expected:', 'test_changed_extracted_source_refuses'),
        ):
            self.assertEqual(source.count(old), 1)
            log = io.StringIO()
            try:
                exec(compile(source.replace(old, 'if False:'), '<observer-gate-mutant>', 'exec'), gate.__dict__)
                result = unittest.TextTestRunner(stream=log).run(unittest.TestSuite([GateTests(case)]))
            finally:
                gate.verify_sources = original
            self.assertEqual(result.errors, [], log.getvalue())
            self.assertEqual(len(result.failures), 1, log.getvalue())
            print('RED assertion:', case)


class TlsContextTests(unittest.TestCase):
    """The source download's trust store: never unverified, system CA bundle as fallback."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='observer-tls-', dir='/var/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.first = self.root / 'first.pem'
        self.second = self.root / 'second.pem'
        self.first.write_text('first\n')
        self.second.write_text('second\n')
        self.bundles = (str(self.root / 'missing.pem'), str(self.first), str(self.second))

    @staticmethod
    def paths(cafile=None, capath=None):
        # Like the bundled runtime: OpenSSL's compiled-in store does not exist.
        return ssl.DefaultVerifyPaths(cafile, capath, 'SSL_CERT_FILE', '/dev/null/cert.pem',
                                      'SSL_CERT_DIR', '/dev/null/certs')

    def context(self, paths, env=None):
        """Build the context with `paths` and `env`; return it and the CA files it loaded."""
        real = ssl.create_default_context()
        loaded = []
        real.load_verify_locations = lambda cafile=None, **_: loaded.append(cafile)
        environ = {k: v for k, v in os.environ.items() if k not in ('SSL_CERT_FILE', 'SSL_CERT_DIR')}
        environ.update(env or {})
        with patch.object(gate, 'SYSTEM_CA_BUNDLES', self.bundles), \
                patch.object(gate.ssl, 'create_default_context', return_value=real), \
                patch.object(gate.ssl, 'get_default_verify_paths', return_value=paths), \
                patch.dict(gate.os.environ, environ, clear=True):
            return gate._tls_context(), loaded

    def assertVerifying(self, context):
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_no_store_loads_the_first_existing_system_bundle(self):
        context, loaded = self.context(self.paths())
        self.assertEqual(loaded, [str(self.first)])
        self.assertVerifying(context)

    def test_explicit_cert_file_or_dir_is_kept(self):
        for env in ({'SSL_CERT_FILE': str(self.second)}, {'SSL_CERT_DIR': str(self.root)}):
            context, loaded = self.context(self.paths(), env)
            self.assertEqual(loaded, [], env)
            self.assertVerifying(context)

    def test_configured_default_store_is_kept(self):
        for paths in (self.paths(cafile=str(self.second)), self.paths(capath=str(self.root))):
            context, loaded = self.context(paths)
            self.assertEqual(loaded, [], paths)
            self.assertVerifying(context)

    def test_no_store_and_no_bundle_still_verifies(self):
        with patch.object(self, 'bundles', (str(self.root / 'missing.pem'),)):
            context, loaded = self.context(self.paths())
        self.assertEqual(loaded, [])
        self.assertVerifying(context)

    def test_download_uses_the_context(self):
        import inspect
        self.assertIn('context=_tls_context()', inspect.getsource(gate.main))


if __name__ == '__main__':
    unittest.main(verbosity=2)
