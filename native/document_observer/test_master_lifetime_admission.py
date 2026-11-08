"""Fail-closed lifetime admission; no native loading or constructor mutation."""
import copy
import importlib.util
from pathlib import Path
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('master_lifetime_loader_test', ROOT / 'narwhal3d/native_document_observer.py')
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)


class LifetimeAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.checks = {
            'control': 'GREEN', 'history': 'GREEN', 'observer': 'GREEN',
            'loader': 'GREEN', 'registry': 'GREEN', 'unload': 'GREEN',
            'capability': 'GREEN', 'mutation-retention': 'RED',
            'mutation-premature': 'RED', 'mutation-copy': 'RED', 'mutation-unload': 'RED',
        }
        self.evidence = {'source_sha256': 'source', 'master_lifetime': {
            'abi': 1, 'platform': 'linux-x86_64', 'library_sha256': 'binary',
            'host_sha256': loader._HOSTS['linux-x86_64'], 'source_sha256': 'source',
            'loader_sha256': 'loader', 'checks': self.checks}}
        self.hashes = patch.object(loader, '_sha', side_effect=lambda path: 'loader' if str(path).endswith('native_document_observer.py') else 'source')
        self.hashes.start()
        self.addCleanup(self.hashes.stop)

    def allowed(self, evidence=None, platform='linux-x86_64'):
        return loader._lifetime_allowed(self.evidence if evidence is None else evidence, 'binary', platform)

    def test_complete_matching_proof_admits(self):
        self.assertTrue(self.allowed())

    def test_old_observer_receipt_is_conservative(self):
        self.assertFalse(self.allowed({}))

    def test_every_proof_field_is_required(self):
        for field in self.evidence['master_lifetime']:
            with self.subTest(field=field):
                evidence = copy.deepcopy(self.evidence)
                del evidence['master_lifetime'][field]
                self.assertFalse(self.allowed(evidence))

    def test_every_regression_and_mutation_witness_is_required(self):
        for field in self.checks:
            evidence = copy.deepcopy(self.evidence)
            del evidence['master_lifetime']['checks'][field]
            self.assertFalse(self.allowed(evidence), field)

    def test_wrong_hashes_abi_and_platform_refuse(self):
        for field in ('abi', 'platform', 'library_sha256', 'host_sha256', 'source_sha256', 'loader_sha256'):
            evidence = copy.deepcopy(self.evidence)
            evidence['master_lifetime'][field] = 'wrong'
            self.assertFalse(self.allowed(evidence), field)

    def test_windows_never_activates_even_with_a_linux_proof(self):
        self.assertFalse(self.allowed(platform='windows-x86_64'))
        self.evidence['master_lifetime']['platform'] = 'windows-x86_64'
        self.evidence['master_lifetime']['host_sha256'] = loader._HOSTS['windows-x86_64']
        self.assertFalse(self.allowed(platform='windows-x86_64'))

    def test_missing_source_refuses_conservatively(self):
        with patch.object(loader, '_sha', side_effect=OSError('unavailable')):
            self.assertFalse(self.allowed())

    def module(self):
        names = ('observe', 'index', 'copy_attributes', 'lease_bind', 'lease_key',
                 'lease_unbind', 'lease_hold', 'lease_hold_key', 'lease_drain', 'file_owner', 'lease_stats')
        module = types.SimpleNamespace(**{name: lambda *args: None for name in names})
        module.master_lifetime_abi = lambda: 1
        return module

    def test_no_proof_never_exposes_cleanup(self):
        self.assertIsNone(loader.Observer(self.module()).master_lifetime())

    def test_missing_entry_point_disables_whole_capability(self):
        module = self.module()
        del module.lease_key
        with patch.object(loader, '_platform_tag', return_value='linux-x86_64'):
            self.assertIsNone(loader.Observer(module, lifetime=True).master_lifetime())

    def test_wrong_native_abi_disables_whole_capability(self):
        module = self.module()
        module.master_lifetime_abi = lambda: 0
        with patch.object(loader, '_platform_tag', return_value='linux-x86_64'):
            self.assertIsNone(loader.Observer(module, lifetime=True).master_lifetime())

    def test_windows_cannot_bypass_gate_via_observer_constructor(self):
        with patch.object(loader, '_platform_tag', return_value='windows-x86_64'):
            self.assertIsNone(loader.Observer(self.module(), lifetime=True).master_lifetime())


if __name__ == '__main__':
    unittest.main()
