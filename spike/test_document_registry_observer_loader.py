#!/usr/bin/env python3
"""Candidate observer admission tests; no app startup or installed library changes."""
from __future__ import annotations

import argparse
import io
import types
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('nw_observer_loader', ROOT / 'narwhal3d/native_document_observer.py')
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)
LIBRARY = RECEIPT = None


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nw-observer-admission-', dir='/var/tmp')
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


def run_mutations():
    global loader
    original = loader
    source = Path(spec.origin).read_text()
    mutants = (
        ('host-byte-check', 'or _sha(host_path) != _HOST', 'or False', 'test_actual_host_bytes_must_match'),
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
            result = unittest.TextTestRunner(stream=log).run(unittest.TestSuite([LoaderTests(case)]))
        finally:
            loader = original
        if result.errors or not result.failures:
            raise AssertionError(f'expected assertion RED: {label}\n{log.getvalue()}')
        print('RED assertion:', label)
    return unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(LoaderTests))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', required=True, type=Path)
    parser.add_argument('--receipt', required=True, type=Path)
    parser.add_argument('--mutations', action='store_true')
    args = parser.parse_args()
    LIBRARY, RECEIPT = args.library.resolve(), args.receipt.resolve()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(LoaderTests))
    if result.wasSuccessful() and args.mutations:
        result = run_mutations()
    raise SystemExit(not result.wasSuccessful())
