"""Additive transcript ABI qualification, preserving the shared editor/popup gate.

Python is ctypes/test orchestration only. --probe LIB emits a combined identity;
it does not attest source provenance, build/lint completion or GUI acceptance.
"""
import ctypes as C
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_linux_native as packaging

spec = importlib.util.spec_from_file_location('_transcript_abi_adapter', ROOT / 'narwhal3d/native_transcript.py')
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
EXPORTS = tuple('narwhal_transcript_' + name for name in
                ('abi', 'capabilities', 'apply', 'read', 'release', 'close', 'stats'))


def probe(library):
    identity = packaging.probe_ui_core(Path(library))
    # Keep the existing strict editor/popup identity intact; add transcript proof.
    with patch.dict(os.environ, {'NARWHAL_UI_CORE_LIBRARY': str(library)}), patch.object(native._editor, '_loaded_library', None):
        lib = native.library()
    for name in EXPORTS:
        getattr(lib, name)
    assert lib.narwhal_transcript_abi() == 1
    assert lib.narwhal_transcript_capabilities() == 7
    assert (C.sizeof(native.Request), C.sizeof(native.Reply)) == (40, 56)
    owner = native.Owner(lib, 2, True)
    try:
        owner.command(1, data=native.encode_lines(['old', '> Box\r\n', 'ΑΣ']))
        assert owner.projection(4, 0, 2, 1) == ('> Box', 'ΑΣ')
        assert owner.projection(8) == ('Box',)
        assert owner.projection(5, data='ας'.encode('utf-32-le')) == (1,)
        assert owner.projection(7) == '> Box\nΑΣ\n'
    finally:
        owner.close()
    identity.update(transcript_abi=1, transcript_capabilities=7, request_bytes=40, reply_bytes=56)
    identity['exports'] = identity['exports'] + [name for name in EXPORTS if name not in identity['exports']]
    return identity


class AbiTests(unittest.TestCase):
    def test_all_additive_exports_and_existing_layouts(self):
        lib = native.library()
        identity = probe(lib._name)
        self.assertEqual((identity['editor_abi'], identity['popup_abi'], identity['transcript_abi']), (1, 1, 1))
        self.assertEqual(identity['state_bytes'], 88)
        self.assertEqual(set(identity['exports']), set(packaging.UI_CORE_EXPORTS) | set(EXPORTS))
        self.assertEqual(len(identity['exports']), 18)

    def test_main_additive_packaging_identity_is_not_duplicated(self):
        library = native.library()._name
        expected = probe(library)
        with patch.object(packaging, 'probe_ui_core', return_value=dict(expected)):
            self.assertEqual(probe(library), expected)

    def test_each_missing_export_is_refused_before_owner_creation(self):
        real = native.library()
        for missing in EXPORTS:
            class Incomplete:
                def __getattr__(self, name):
                    if name == missing: raise AttributeError(name)
                    return getattr(real, name)
            with self.subTest(missing=missing), patch.object(native._editor, 'library', return_value=Incomplete()):
                with self.assertRaisesRegex(RuntimeError, 'transcript ABI'): native.library()

    def test_wrong_version_or_capabilities_are_refused(self):
        real = native.library()
        for name, value in (('narwhal_transcript_abi', 2), ('narwhal_transcript_capabilities', 3)):
            class Wrong:
                def __getattr__(self, key):
                    return (lambda: value) if key == name else getattr(real, key)
            with patch.object(native._editor, 'library', return_value=Wrong()):
                with self.assertRaisesRegex(RuntimeError, 'transcript ABI'): native.library()


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--probe':
        print(json.dumps(probe(Path(sys.argv[2]).absolute()), sort_keys=True))
    else:
        unittest.main()
