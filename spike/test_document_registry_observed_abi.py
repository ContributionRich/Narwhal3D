#!/usr/bin/env python3
"""Actual openNURBS observations through the additive Rust registry C ABI.

Component gate only; this does not activate NarwhalModel constructors or replace Undo.
"""
from __future__ import annotations

import argparse
import ctypes
import importlib.util
import io
import types
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import rhino3dm as r3

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


adapter = load('nw_registry_observed_adapter', ROOT / 'narwhal3d/native_object_registry.py')
observer_loader = load('nw_registry_observer_loader', ROOT / 'narwhal3d/native_document_observer.py')
CORE = OBSERVER = None


class ObservedRegistryTests(unittest.TestCase):
    def setUp(self):
        self.file = r3.File3dm()
        attributes = r3.ObjectAttributes()
        attributes.Name = 'same'
        self.oid = str(self.file.Objects.AddPoint(r3.Point3d(1, 0, 0), attributes))
        self.owner = adapter.Registry(library=CORE)
        self.addCleanup(self.owner.close)

    def row(self, payload=1):
        obj = self.file.Objects.FindId(self.oid)
        return dict(id=self.oid, name=obj.Attributes.Name, display='Display', payload=payload)

    def observe(self):
        return OBSERVER.observe(self.file)

    def adopt(self):
        data = self.observe()
        ticket = self.owner.prepare_observed([self.row()], data)
        ticket.begin_apply()
        return ticket.commit_observed(data)

    def test_unmediated_same_uuid_change_invalidates_until_explicit_rebuild(self):
        self.adopt()
        attrs = self.file.Objects.FindId(self.oid).Attributes
        self.file.Objects.Delete(self.oid)
        self.assertEqual(str(self.file.Objects.AddPoint(r3.Point3d(99, 0, 0), attrs)), self.oid)
        data = self.observe()
        result = self.owner.check_observation(data)
        self.assertFalse(result['matches'])
        self.assertFalse(self.owner.info()['authoritative'])
        with self.assertRaisesRegex(adapter.RegistryError, 'authority_invalid'):
            self.owner.snapshot()
        with self.assertRaisesRegex(adapter.RegistryError, 'changed_object_reused_payload'):
            self.owner.prepare_observed([self.row()], data, rebuild=True)
        ticket = self.owner.prepare_observed([self.row(2)], data, rebuild=True)
        ticket.begin_apply()
        result = ticket.commit_observed(data)
        self.assertTrue(result['authoritative'])
        self.assertEqual(self.owner.get(self.oid)['payload'], 2)

    def test_failed_host_confirmation_consumes_ticket_and_invalidates(self):
        self.adopt()
        before = self.observe()
        ticket = self.owner.prepare_observed([self.row()], before)
        ticket.begin_apply()
        self.file.Objects.FindId(self.oid).Attributes.Name = 'unexpected'
        with self.assertRaisesRegex(adapter.RegistryError, 'observation_commit_mismatch'):
            ticket.commit_observed(self.observe())
        self.assertFalse(self.owner.info()['authoritative'])
        self.assertFalse(self.owner.info()['pending'])
        with self.assertRaisesRegex(adapter.RegistryError, 'invalid_ticket'):
            ticket.commit_observed(before)

    def test_snapshots_retain_witnesses_and_restore_requires_confirmation(self):
        self.adopt()
        saved = self.owner.snapshot()
        self.addCleanup(saved.close)
        original = self.file.Objects.FindId(self.oid).Geometry.Duplicate()
        self.file.Objects.FindId(self.oid).Geometry.Transform(r3.Transform.Translation(4, 0, 0))
        ticket = self.owner.prepare_observed([self.row(2)], self.observe())
        ticket.begin_apply()
        ticket.commit_observed(self.observe())
        restore = self.owner.prepare_restore(saved)
        restore.begin_apply()
        with self.assertRaisesRegex(adapter.RegistryError, 'observation_confirmation_required'):
            restore.commit()
        attrs = self.file.Objects.FindId(self.oid).Attributes
        self.file.Objects.Delete(self.oid)
        self.file.Objects.Add(original, attrs)
        restore.commit_observed(self.observe())
        self.assertEqual(self.owner.get(self.oid)['payload'], 1)
        self.assertTrue(self.owner.check_observation(self.observe())['matches'])

    def test_noop_and_false_names_do_not_advance_revision(self):
        first = self.adopt()
        ticket = self.owner.prepare_observed([self.row()], self.observe())
        ticket.begin_apply()
        self.assertEqual(ticket.commit_observed(self.observe())['revision'], first['revision'])
        row = self.row()
        row['name'] = 'not in host'
        with self.assertRaisesRegex(adapter.RegistryError, 'observation_name_mismatch'):
            self.owner.prepare_observed([row], self.observe())
        self.assertFalse(self.owner.info()['pending'])
        self.assertEqual(self.owner.info()['revision'], first['revision'])

    def test_unobserved_updates_and_cross_owner_restore_refuse(self):
        self.adopt()
        with self.assertRaisesRegex(adapter.RegistryError, 'observation_required'):
            self.owner.prepare_edits([])
        snapshot = self.owner.snapshot()
        self.addCleanup(snapshot.close)
        other = adapter.Registry(library=CORE)
        self.addCleanup(other.close)
        with self.assertRaisesRegex(adapter.RegistryError, 'invalid_snapshot'):
            other.prepare_restore(snapshot)

    def test_truncated_input_does_not_prepare_partial_state(self):
        data = self.observe()
        with self.assertRaisesRegex(adapter.RegistryError, 'observation_truncated'):
            self.owner.prepare_observed([self.row()], data[:-1])
        self.assertEqual(self.owner.info()['count'], 0)
        self.assertFalse(self.owner.info()['pending'])
        self.adopt()

    def test_payload_keys_are_reserved_natively_without_a_revision_change(self):
        keys = self.owner.reserve_payloads(3)
        self.assertEqual(len(keys), 3)
        self.assertEqual(self.owner.info()['revision'], 0)
        later = self.owner.reserve_payloads(2)
        self.assertEqual(later.start, keys.stop)
        data = self.observe()
        ticket = self.owner.prepare_observed([self.row(keys.start)], data)
        ticket.begin_apply()
        ticket.commit_observed(data)
        self.assertEqual(self.owner.get(self.oid)['payload'], keys.start)
        for count in (0, 100_001):
            with self.assertRaisesRegex(adapter.RegistryError, 'payload_reservation_limit'):
                self.owner.reserve_payloads(count)

    def test_reservation_reply_count_must_match_request(self):
        original = adapter._call
        def wrong(function, request):
            if request['op'] == 'reserve_payloads':
                return dict(first=100, count=request['count'] + 1)
            return original(function, request)
        with patch.object(adapter, '_call', wrong):
            with self.assertRaisesRegex(adapter.RegistryError, 'invalid_reply'):
                self.owner.reserve_payloads(2)
        self.assertFalse(self.owner.info()['authoritative'])
        self.owner.invalidate()

    def test_wrong_observation_abi_refuses_before_native_mutation(self):
        with patch.object(CORE, 'narwhal_object_observation_abi', lambda: 99):
            with self.assertRaisesRegex(adapter.RegistryUnavailable, 'ABI1'):
                self.owner.prepare_observed([self.row()], self.observe())
        self.assertFalse(self.owner.info()['pending'])
        self.assertEqual(self.owner.info()['count'], 0)

    def test_malformed_check_reply_is_not_authority(self):
        self.adopt()
        def malformed(*args):
            data = b'{"status":"ok","revision":true,"matches":true}'
            ctypes.memmove(args[4], data, len(data))
            return len(data)
        with patch.object(CORE, 'narwhal_object_registry_observe', malformed):
            with self.assertRaisesRegex(adapter.RegistryError, 'invalid_reply'):
                self.owner.check_observation(self.observe())
        self.assertFalse(self.owner.info()['authoritative'])
        self.owner.invalidate()

    def test_lost_commit_reply_never_retries_and_quarantines(self):
        self.adopt()
        ticket = self.owner.prepare_observed([self.row()], self.observe())
        ticket.begin_apply()
        actual = CORE.narwhal_object_registry_observe
        calls = []

        def lost(*args):
            calls.append(1)
            actual(*args)
            return 0

        with patch.object(CORE, 'narwhal_object_registry_observe', lost):
            with self.assertRaisesRegex(adapter.RegistryError, 'invalid_reply'):
                ticket.commit_observed(self.observe())
        self.assertEqual(calls, [1])
        self.assertFalse(self.owner.info()['authoritative'])
        with self.assertRaisesRegex(adapter.RegistryError, 'outcome_unknown'):
            self.owner.check_observation(self.observe())
        self.owner.invalidate()


def run_mutations():
    global adapter
    original = adapter
    path = ROOT / 'narwhal3d/native_object_registry.py'
    source = path.read_text()
    mutants = (
        ('observation-abi', 'if abi() != 1:', 'if False:', 'test_wrong_observation_abi_refuses_before_native_mutation', 2),
        ('observed-reply-validation', "return _validate({'check': 'check_observation', 'index': 'info', 'capture': 'prepare'}.get(op, op), reply)",
         "return reply if op == 'check' else _validate(op, reply)", 'test_malformed_check_reply_is_not_authority', 1),
        ('observed-reply-quarantine', "if error.code in ('invalid_reply', 'panic', 'registry_poisoned'):",
         'if False:', 'test_lost_commit_reply_never_retries_and_quarantines', 1),
        ('observed-mutation-replay', 'size = function(input_header, len(header), input_data, len(observation), output, _MIN_OUTPUT)',
         'size = function(input_header, len(header), input_data, len(observation), output, _MIN_OUTPUT)\n                if size <= 0:\n                    size = function(input_header, len(header), input_data, len(observation), output, _MIN_OUTPUT)',
         'test_lost_commit_reply_never_retries_and_quarantines', 1),
        ('reservation-correlation', "if reply['count'] != count:", 'if False:',
         'test_reservation_reply_count_must_match_request', 1),
    )
    for label, old, new, case, expected in mutants:
        if source.count(old) != expected:
            raise AssertionError(f'stale mutation anchor: {label}')
        candidate = types.ModuleType('nw_registry_observed_mutant')
        candidate.__file__ = str(path)
        exec(compile(source.replace(old, new), str(path), 'exec'), candidate.__dict__)
        adapter = candidate
        log = io.StringIO()
        try:
            result = unittest.TextTestRunner(stream=log).run(unittest.TestSuite([ObservedRegistryTests(case)]))
        finally:
            adapter = original
        if result.errors or not result.failures:
            raise AssertionError(f'expected assertion RED: {label}\n{log.getvalue()}')
        print('RED assertion:', label)
    return unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ObservedRegistryTests))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', required=True, type=Path)
    parser.add_argument('--observer', required=True, type=Path)
    parser.add_argument('--receipt', required=True, type=Path)
    parser.add_argument('--mutations', action='store_true')
    args = parser.parse_args()
    CORE = ctypes.CDLL(str(args.core.resolve()))
    OBSERVER = observer_loader.load_candidate(args.observer, args.receipt)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ObservedRegistryTests))
    if result.wasSuccessful() and args.mutations:
        result = run_mutations()
    raise SystemExit(not result.wasSuccessful())
