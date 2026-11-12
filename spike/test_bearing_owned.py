"""RUST-BEARING-12 real ctypes differential qualification; no skip/fallback.

Bundled CPython 3.13: python3.13 -B spike/test_bearing_owned.py --mutations
Builds only an external scratch crate using exact source copies. --library PATH
qualifies an integrated library. No live source, gate or shared lib.rs edits.
"""
import argparse
import ast
import ctypes
import hashlib
import importlib.util
import itertools
import json
import math
import os
from pathlib import Path
import random
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('numparse', 'numparse_owned', 'python_text', 'bearing_owned')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scratch(directory, mutation=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'Cargo.toml').write_text(
        '[package]\nname="rust12-bearing-qualification"\nversion="0.0.0"\nedition="2021"\n'
        '[lib]\nname="narwhal_pure_core"\ncrate-type=["cdylib","rlib"]\npath="lib.rs"\n')
    original = (ROOT / 'native/pure_core/src/lib.rs').read_text()
    start = original.index('use std::mem::{align_of, size_of};')
    helpers = original[start:original.index('\n#[cfg(test)]', start)]
    for name in MODULES:
        content = (ROOT / 'native/pure_core/src' / (name + '.rs')).read_text()
        if mutation and name == 'bearing_owned':
            old, new = mutation
            assert content.count(old) == 1
            content = content.replace(old, new)
        path = directory / (name + '.rs')
        path.write_text(content)
        helpers += '\n#[path=%s]\npub mod %s;\n' % (json.dumps(str(path)), name)
    (directory / 'lib.rs').write_text(helpers)
    return directory / 'Cargo.toml'


def cargo(manifest, command):
    return subprocess.run(['cargo', '+1.95.0', command, '--offline', '--manifest-path', str(manifest)],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, timeout=90)


class BearingOwned(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native = load('bearing_owned_native', ROOT / 'narwhal3d/native_bearing.py')
        cls.reference = load('bearing_owned_reference', ROOT / 'spike/bearing_reference.py')
        cls.candidate = load('bearing_owned_candidate', ROOT / 'narwhal3d/bearing.py')
        cls.comparisons = 0

    def compare(self, text, mode=0):
        name = 'parse_surveyor' if mode else 'bearing_angle'
        expected = getattr(self.reference, name)(text)
        actual = getattr(self.native, name)(text)
        type(self).comparisons += 1
        self.assertEqual(actual[1], expected[1], (ascii(text), mode, actual, expected))
        self.assertEqual(actual[0], expected[0], (ascii(text), mode, actual, expected))
        if expected[0] is not None:
            av = actual[0] if mode else (actual[0],)
            ev = expected[0] if mode else (expected[0],)
            for a, e in zip(av, ev):
                self.assertEqual(a.hex(), e.hex(), (ascii(text), a, e))

    def test_grid(self):
        angles = ['0', '30', '90', '90.00000000000001', '91', '0.000000000001',
                  '30d22\'54.43"', '30d22\'', '30d22\'"', '0d999\'999"',
                  '1d2.5\'3.5', '1D2\'', '1d', '1d2', '1d2\'3.', '.5', '1.',
                  '+1', '-1', '1e1', 'nan', 'inf', '', 'bad', 'E', 'W',
                  '٣٠', '３０.５', '𝟛𝟘d٢٢\'٥٤.٤٣"', '1\n2', '\ud800']
        for ns, ew, angle, ws in itertools.product(
                ['N', 'n', 'S', 's', 'ſ', 'x'], ['E', 'e', 'W', 'w', 'x'],
                angles, ['', ' ', '\t', '\u0085', '\u001c', '\u2003', '\n']):
            text = ws + ns + ws + angle + ws + ew + ws
            self.compare(text)
            self.compare('-٢.٥ <' + ws + text, 1)

    def test_capture_and_anchor_adversaries(self):
        tails = ['', ' ', '\n', '\n\n', '\r\n', '\n ', ' \n', '\t\n',
                 'N30E', 'Nbad E', 'N  E', 'N\nE', 'N \n E',
                 'N30E\n', 'N30E\n\n', 'N30E\n ', 'N30E\r\n',
                 'N  bad  E', 'N30 E', 'N30\nE']
        for prefix, tail in itertools.product(
                ['1<', ' 1 < ', '1<\n', '1<\n\n', '-1.5<', '+٣.٥<',
                 '.5<', '1.<', '1e2<', '1 <\u001c', '1<\n '], tails):
            self.compare(prefix + tail, 1)
        for tail in tails:
            self.compare(tail)
        for text in [None, 0, [], {}, b'N30E']:
            self.compare(text)
            self.compare(text, 1)

    def test_deterministic_adversarial(self):
        rng = random.Random(12012)
        alphabet = "NSnsſEWew01239dD.'\"+-< \t\n\r\x1c\u0085\u2003٣𝟙\ud800"
        for _ in range(12000):
            text = ''.join(rng.choice(alphabet) for _ in range(rng.randrange(0, 35)))
            self.compare(text, rng.randrange(2))
        for _ in range(1500):
            angle = str(rng.randrange(0, 92)) + 'd' + str(rng.randrange(0, 200)) + "'" + str(rng.random()*100)
            self.compare(rng.choice(['N', 'S', 'ſ']) + angle + rng.choice(['E', 'W']))

    def test_unicode_decimal_and_whitespace(self):
        # Every host-admitted Nd block, not just hand-picked Arabic/ASCII cases.
        import unicodedata
        for cp in range(0x110000):
            c = chr(cp)
            if unicodedata.category(c) == 'Nd':
                self.compare('N' + c + 'E')
                self.compare(c + '<ſ' + c + 'W', 1)
            if c.isspace():
                self.compare(c + 'N' + c + '30' + c + 'E' + c)
                self.compare(c + '1' + c + '<' + c + 'N30E' + c, 1)

    def test_safety_archive_candidate(self):
        for mode in (0, 1):
            fn = self.native.parse_surveyor if mode else self.native.bearing_angle
            self.assertEqual(fn('x'*4097), (None, 'bearing input exceeds 4096 characters'))
            text = ('9'*400 + '<N30E') if mode else ('N' + '9'*400 + 'E')
            self.assertEqual(fn(text), (None, 'bearing number outside finite range'))
        self.compare('N' + '0'*4093 + 'E')
        self.compare('1<N' + '0'*4091 + 'E', 1)
        self.assertEqual(hashlib.sha256((ROOT / 'spike/bearing_reference.py').read_bytes()).hexdigest(), 'd010960c6052050f58ed4e496f67dab2b2373c0f52dd8243d8ab996e80fb96b3')
        self.assertEqual(self.candidate.bearing_angle('N30E'), (60.0, None))
        self.assertEqual(self.candidate.parse_surveyor('-2<S30W'), ((-2.0, 240.0), None))
        source = (ROOT / 'narwhal3d/native_bearing.py').read_text()
        for path in ['narwhal3d/native_bearing.py', 'narwhal3d/bearing.py']:
            tree = ast.parse((ROOT / path).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertNotIn('re', [n.name for n in node.names])
        self.assertNotIn('_native_bearing', (ROOT / 'spike/bearing_reference.py').read_text())

    def test_required_and_protocol(self):
        with patch.dict(os.environ, NARWHAL_PURE_CORE='/no/such/bearing-library.so'):
            with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                self.native.bearing_angle('N30E')
        class Function:
            def __init__(self, value): self.value = value
            def __call__(self): return self.value
        class Stale:
            narwhal_pure_core_abi_version = Function(1)
            narwhal_pure_core_bearing_abi2 = Function(1)
        with patch.dict(os.environ, NARWHAL_PURE_CORE='/stale/bearing.so'):
            with patch.object(self.native.ctypes, 'CDLL', return_value=Stale()):
                with self.assertRaisesRegex(RuntimeError, 'Stale'):
                    self.native.bearing_angle('N30E')
        def invalid(*args): return 2
        with patch.object(self.native, '_required_binding', return_value=invalid):
            with self.assertRaisesRegex(RuntimeError, 'Invalid'):
                self.native.bearing_angle('N30E')
        binding = self.native._required_binding()
        R = self.native.BearingReply
        self.assertEqual(ctypes.sizeof(R), 32)
        reply = R(code=99)
        self.assertEqual(binding(None, 1, 0, 151, ctypes.byref(reply), 32), 2)
        self.assertEqual(reply.code, 0)
        self.assertEqual(binding(None, 0, 2, 151, ctypes.byref(reply), 32), 2)
        self.assertEqual(binding(None, 0, 0, 160, ctypes.byref(reply), 32), 2)
        self.assertEqual(binding(None, 0, 0, 151, None, 32), 1)
        self.assertEqual(binding(None, 0, 0, 151, ctypes.byref(reply), 31), 1)
        self.assertEqual(binding(None, 4097, 0, 151, ctypes.byref(reply), 32), 0)
        self.assertEqual(reply.code, 5)
        unaligned = ctypes.cast(ctypes.addressof(reply)+1, ctypes.POINTER(ctypes.c_uint32))
        self.assertEqual(binding(unaligned, 1, 0, 151, ctypes.byref(reply), 32), 2)


def qualify(library):
    os.environ['NARWHAL_PURE_CORE'] = str(library)
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(BearingOwned))
    print('Exact differential comparisons:', BearingOwned.comparisons)
    return result.wasSuccessful()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, default=os.environ.get('NARWHAL_PURE_CORE'))
    parser.add_argument('--mutations', action='store_true')
    args = parser.parse_args()
    if args.library and not args.mutations:
        return 0 if qualify(Path(args.library)) else 1
    with tempfile.TemporaryDirectory(prefix='rust12-bearing-') as tmp:
        tmp = Path(tmp)
        manifest = scratch(tmp / 'green')
        for command in ('test', 'build'):
            result = cargo(manifest, command)
            print(result.stdout)
            if result.returncode: return 1
        library = args.library or manifest.parent / 'target/debug/libnarwhal_pure_core.so'
        if not qualify(library): return 1
        if args.mutations:
            for label, mutation in [('quadrant-sign', ('north == east', 'north != east')),
                                    ('angle-classification', ('error(ANGLE, a, b)', 'error(BEARING, a, b)'))]:
                mutant = scratch(tmp / label, mutation)
                result = cargo(mutant, 'test')
                if result.returncode == 0 or 'quadrant_and_classification ... FAILED' not in result.stdout:
                    raise AssertionError('Mutation did not fail target test:\n' + result.stdout)
                print('RED mutation:', label, '(quadrant_and_classification FAILED)')
            # Re-run exact unmodified source after temporary-copy mutations.
            result = cargo(manifest, 'test')
            if result.returncode: raise AssertionError(result.stdout)
            if not qualify(library): return 1
            print('RESTORED GREEN (repository sources never mutated)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
