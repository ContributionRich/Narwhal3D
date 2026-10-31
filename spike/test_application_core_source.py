#!/usr/bin/env python3
"""Public source closure and narrow legal-attribution scan controls."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import audit_public_tree as audit
import create_public_branch as public


class SourceTests(unittest.TestCase):
    def test_native_crate_and_host_closure(self):
        manifest = set(public.public_files())
        for path in (ROOT / 'native/application_core').rglob('*'):
            if path.is_file() and 'target' not in path.parts:
                self.assertIn(path.relative_to(ROOT).as_posix(), manifest)
        for name in ('document_metadata', 'native_document_session'):
            self.assertIn('narwhal3d/' + name + '.py', manifest)
        for path in ('scripts/check_application_core.py', 'scripts/test_release_application_core.py',
                     'spike/test_document_session_abi.py', 'spike/test_document_session_integration.py'):
            self.assertIn(path, manifest)

    def test_exact_legal_attribution_not_blanket_file_exemption(self):
        paths = ('native/application_core/src/ordinal_map.rs',
                 'native/application_core/generate_ordinal.rs',
                 'native/application_core/OPENNURBS_LICENSE')
        for path in paths:
            data = (ROOT / path).read_bytes()
            self.assertIsNone(audit.LEGACY_TEXT_PATTERN.search(audit.legacy_scan_bytes(path, data)))
            private = b'\nRhi' + b'no private implementation notes\n'
            self.assertIsNotNone(audit.LEGACY_TEXT_PATTERN.search(audit.legacy_scan_bytes(path, data + private)))
            self.assertIsNotNone(audit.LEGACY_TEXT_PATTERN.search(audit.legacy_scan_bytes('notes.txt', data)))
            self.assertIn(b'registered trademark', data.lower())

    def test_other_private_markers_still_rejected_in_licensed_source(self):
        path = 'native/application_core/src/ordinal_map.rs'
        data = (ROOT / path).read_bytes()
        for extra in (b'// Owner' + b': private\n', b'// gh' + b'p_secret\n'):
            self.assertTrue(audit.audit_entries({path: ('100644', data + extra)}, require_manifest=False))

    def test_attribution_filter_mutations_go_red(self):
        for replacement in (lambda path, blob: blob.lower(), lambda path, blob: b''):
            with patch.object(audit, 'legacy_scan_bytes', replacement):
                result = unittest.TestResult()
                SourceTests('test_exact_legal_attribution_not_blanket_file_exemption').run(result)
            self.assertFalse(result.wasSuccessful(), 'attribution mutation survived')


if __name__ == '__main__': unittest.main()
