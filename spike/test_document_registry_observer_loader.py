#!/usr/bin/env python3
"""Candidate observer admission tests; no app startup or installed library changes."""
from __future__ import annotations

import argparse
import io
import sys
import types
import importlib.util
import json
from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = '/var/tmp' if Path('/var/tmp').is_dir() else None
LINUX_HOST = 'd5fed4447838a0645bfc82cf6ccc684e1c6ca6d4568533e044adf9754de169d4'
WINDOWS_HOST = '1b59225812361ffd89e48eb1caf64a9f505aa1613da2058479b0b766fa976ce7'
spec = importlib.util.spec_from_file_location('nw_observer_loader', ROOT / 'narwhal3d/native_document_observer.py')
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)
LIBRARY = RECEIPT = None


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nw-observer-admission-', dir=SCRATCH)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'receipt.json'
        self.evidence = json.loads(RECEIPT.read_text())

    def refuse(self, evidence, message):
        self.path.write_text(json.dumps(evidence))
        with patch.object(loader.importlib.util, 'module_from_spec') as loading:
            with self.assertRaisesRegex(loader.ObservationUnavailable, message):
                loader.load_candidate(LIBRARY, self.path)
            loading.assert_not_called()

    def test_matching_pair_loads_and_reads(self):
        observer = loader.load_candidate(LIBRARY, RECEIPT)
        file = loader.r3.File3dm()
        self.assertEqual(observer.observe(file), b'NWOBS01\0\0\0\0\0')
        file.Objects.AddPoint(loader.r3.Point3d(1, 0, 0))
        before = observer.observe(file)
        file.Objects[0].Attributes.Name = 'changed'
        self.assertNotEqual(before, observer.observe(file))

    def test_wrong_host_hash_refuses_before_loading(self):
        self.evidence['host']['sha256'] = '0' * 64
        self.refuse(self.evidence, 'binary/ABI pair')

    def test_actual_host_bytes_must_match(self):
        real = loader._sha
        with patch.object(loader, '_sha', side_effect=lambda path: '0' * 64 if str(path) == loader.host.__file__ else real(path)):
            self.refuse(self.evidence, 'binary/ABI pair')

    def test_wrong_abi_refuses(self):
        self.evidence['host']['soabi'] = 'wrong'
        self.refuse(self.evidence, 'binary/ABI pair')

    def test_wrong_source_distribution_refuses(self):
        self.evidence['upstream_sdist_sha256'] = '0' * 64
        self.refuse(self.evidence, 'binary/ABI pair')

    def test_mutant_cannot_be_admitted_as_a_control(self):
        self.evidence['entries'] = [dict(label='mutant', status='GREEN', sha256=loader._sha(LIBRARY))]
        self.refuse(self.evidence, 'qualified control')

    def test_wrong_candidate_hash_refuses(self):
        self.evidence['entries'] = [dict(label='control', status='GREEN', sha256='0' * 64)]
        self.refuse(self.evidence, 'qualified control')

    def test_duplicate_receipt_field_refuses(self):
        self.path.write_text('{"host":{},"host":{}}')
        with self.assertRaisesRegex(loader.ObservationUnavailable, 'duplicate'):
            loader.load_candidate(LIBRARY, self.path)

    def test_proxy_and_malformed_output_refuse(self):
        observer = loader.load_candidate(LIBRARY, RECEIPT)
        with patch.object(observer, '_observe', return_value=b'NWOBS01\0\0\0\0\0'):
            with self.assertRaisesRegex(TypeError, 'exact rhino3dm'):
                observer.observe(object())
        for invalid in (b'', b'WRONG0000000', 'NWOBS01\0\0\0\0\0'):
            with patch.object(observer, '_observe', return_value=invalid):
                with self.assertRaisesRegex(loader.ObservationUnavailable, 'malformed'):
                    observer.observe(loader.r3.File3dm())


class PlatformAdmissionTests(unittest.TestCase):
    """Platform tags, host-hash selection and discovery with synthetic receipts; no native pair is loaded."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nw-observer-platform-', dir=SCRATCH)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = self.root / 'candidate' / '_narwhal_document_observer.pyd'
        self.library.parent.mkdir()
        self.library.write_bytes(b'synthetic candidate bytes; never loaded')
        self.receipt = self.root / 'build.json'
        self.enterContext(patch.dict(sys.modules))
        sys.modules.pop(loader._MODULE, None)
        self.enterContext(patch.object(loader, '_loaded', None))

    @contextmanager
    def simulated(self, system, machine):
        with patch.object(loader.sys, 'platform', system), patch.object(loader.platform, 'machine', return_value=machine):
            yield

    def evidence(self, host):
        return dict(upstream_sdist_sha256=loader._SDIST,
                    host=dict(version=loader.r3.__version__, sha256=host, cache_tag=sys.implementation.cache_tag,
                              soabi=loader.sysconfig.get_config_var('SOABI')),
                    entries=[dict(label='control', status='GREEN', sha256=loader._sha(self.library))])

    def refuse(self, evidence, message):
        self.receipt.write_text(json.dumps(evidence))
        with patch.object(loader.importlib.util, 'module_from_spec') as loading:
            with self.assertRaisesRegex(loader.ObservationUnavailable, message):
                loader.load_candidate(self.library, self.receipt)
            loading.assert_not_called()

    def discovered(self, root, system, machine):
        offered = []
        with self.simulated(system, machine), \
                patch.object(loader, '__file__', str(root / 'narwhal3d/native_document_observer.py'), create=True), \
                patch.object(loader, 'load_candidate', side_effect=lambda library, receipt: offered.append((library, receipt))):
            try:
                loader.load_observer()
            except loader.ObservationUnavailable:
                pass
        return offered

    def test_hosts_and_library_suffixes_are_per_platform(self):
        self.assertEqual(loader._HOSTS, {'linux-x86_64': LINUX_HOST, 'windows-x86_64': WINDOWS_HOST})
        self.assertEqual(loader._LIBRARY_SUFFIX, {'linux-x86_64': '.so', 'windows-x86_64': '.pyd'})

    def test_platform_tag_normalizes_windows_and_linux(self):
        with self.simulated('win32', 'AMD64'):
            self.assertEqual(loader._platform_tag(), 'windows-x86_64')
        with self.simulated('win32', 'ARM64'):
            self.assertEqual(loader._platform_tag(), 'windows-arm64')
        with self.simulated('linux', 'x86_64'):
            self.assertEqual(loader._platform_tag(), 'linux-x86_64')

    def test_unknown_platform_refuses_before_loading(self):
        for system, machine in (('freebsd14', 'x86_64'), ('win32', 'ARM64')):
            with self.simulated(system, machine):
                self.refuse(self.evidence(WINDOWS_HOST), 'not qualified for this platform')

    def test_windows_receipt_with_linux_host_refuses(self):
        with self.simulated('win32', 'AMD64'):
            self.refuse(self.evidence(LINUX_HOST), 'binary/ABI pair')

    def test_windows_receipt_must_match_the_actual_host_bytes(self):
        with self.simulated('win32', 'AMD64'):
            self.refuse(self.evidence(WINDOWS_HOST), 'binary/ABI pair')

    def test_windows_admission_uses_the_windows_host_hash(self):
        with self.simulated('win32', 'AMD64'):
            evidence = self.evidence(WINDOWS_HOST)
            self.receipt.write_text(json.dumps(evidence))
            real = loader._sha
            self.enterContext(patch.object(loader, '_sha', side_effect=lambda path: (
                WINDOWS_HOST if str(path) == loader.host.__file__ else real(path))))
            self.enterContext(patch.object(loader.importlib.util, 'spec_from_file_location', return_value=None))
            with self.assertRaisesRegex(loader.ObservationUnavailable, 'observer extension cannot be loaded'):
                loader.load_candidate(self.library, self.receipt)

    def test_windows_discovery_offers_the_pyd_in_the_tag_directory(self):
        root = self.root / 'checkout'
        directory = root / 'artifacts/document-observer/windows-x86_64'
        directory.mkdir(parents=True)
        (directory / '_narwhal_document_observer.pyd').write_bytes(b'x')
        (directory / 'build.json').write_text('{}')
        self.assertEqual(self.discovered(root, 'win32', 'AMD64'),
                         [(directory / '_narwhal_document_observer.pyd', directory / 'build.json')])

    def test_linux_discovery_still_offers_the_so_only(self):
        root = self.root / 'checkout'
        directory = root / 'artifacts/document-observer/linux-x86_64'
        directory.mkdir(parents=True)
        (directory / '_narwhal_document_observer.so').write_bytes(b'x')
        (directory / '_narwhal_document_observer.pyd').write_bytes(b'x')
        (directory / 'build.json').write_text('{}')
        self.assertEqual(self.discovered(root, 'linux', 'x86_64'),
                         [(directory / '_narwhal_document_observer.so', directory / 'build.json')])


def all_tests():
    return unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(case)
                              for case in (LoaderTests, PlatformAdmissionTests))


def case_instance(name):
    owner = PlatformAdmissionTests if hasattr(PlatformAdmissionTests, name) else LoaderTests
    return owner(name)


def run_mutations():
    global loader
    original = loader
    source = Path(spec.origin).read_text()
    mutants = (
        ('host-byte-check', 'or _sha(host_path) != _HOSTS[tag]', 'or False', 'test_actual_host_bytes_must_match'),
        ('platform-refusal', 'if tag not in _HOSTS:', 'if False:', 'test_unknown_platform_refuses_before_loading'),
        ('windows-host-selection', "runtime['sha256'] != _HOSTS[tag]", "runtime['sha256'] != _HOSTS['linux-x86_64']",
         'test_windows_admission_uses_the_windows_host_hash'),
        ('platform-tag', "'x86_64' if machine == 'amd64' else machine", 'machine',
         'test_platform_tag_normalizes_windows_and_linux'),
        ('windows-suffix', "'windows-x86_64': '.pyd'}", "'windows-x86_64': '.so'}",
         'test_windows_discovery_offers_the_pyd_in_the_tag_directory'),
        ('abi-check', "or runtime['soabi'] != sysconfig.get_config_var('SOABI')", 'or False', 'test_wrong_abi_refuses'),
        ('candidate-hash', "and entry['sha256'] == expected", 'and True', 'test_wrong_candidate_hash_refuses'),
        ('control-label', "entry['label'] in ('control', 'restored')", 'True', 'test_mutant_cannot_be_admitted_as_a_control'),
        ('proxy-type', 'if type(file) is not r3.File3dm:', 'if False:', 'test_proxy_and_malformed_output_refuse'),
        ('duplicate-field', 'if key in value:', 'if False:', 'test_duplicate_receipt_field_refuses'),
    )
    for label, old, new, case in mutants:
        expected = 2 if label == 'proxy-type' else 1
        if source.count(old) != expected:
            raise AssertionError(f'nonunique mutation: {label}')
        candidate = types.ModuleType('nw_observer_loader_mutant')
        exec(compile(source.replace(old, new), str(spec.origin), 'exec'), candidate.__dict__)
        # Reuse the already admitted native pair; only loader logic is mutated.
        candidate._loaded = original._loaded
        loader = candidate
        log = io.StringIO()
        try:
            result = unittest.TextTestRunner(stream=log).run(unittest.TestSuite([case_instance(case)]))
        finally:
            loader = original
        if result.errors or not result.failures:
            raise AssertionError(f'expected assertion RED: {label}\n{log.getvalue()}')
        print('RED assertion:', label)
    return unittest.TextTestRunner(verbosity=2).run(all_tests())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', required=True, type=Path)
    parser.add_argument('--receipt', required=True, type=Path)
    parser.add_argument('--mutations', action='store_true')
    args = parser.parse_args()
    LIBRARY, RECEIPT = args.library.resolve(), args.receipt.resolve()
    result = unittest.TextTestRunner(verbosity=2).run(all_tests())
    if result.wasSuccessful() and args.mutations:
        result = run_mutations()
    raise SystemExit(not result.wasSuccessful())
