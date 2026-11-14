"""RUST-BEARING-GOLDEN-18 host qualification; the grammar now lives in Rust.

The 28,190-case differential corpus was captured ONCE from the retired Python
reference into `native/pure_core/tests/fixtures/bearing_golden.txt` and is
replayed by `native/pure_core/tests/bearing_golden.rs` against the real
`bearing_v2` ABI. No Python regex parser remains in the public host path.

This file keeps ONLY the host-side contract that the ABI never sees, and a
small LIVE sample that verifies actual errors against the CAPTURED message
EXACTLY (the label is fixed presentation; the span is the substring the old
reference embedded, decoded from the fixture). It does not re-derive the
grammar, re-compute the corpus checksum, or build any scratch crate: the
heavy integrity work (hard count, provenance, FNV-1a64 fingerprint, every-row
replay) is enforced by the Rust golden test, and the native mutation cycle
lives in the ignored private runner `legacy/retired-rust18/`.

Host-side contract kept here:
  * object admission — non-strings refused with 'expected a string'
  * OURS safety      — >4096 code points and nonfinite numbers refused explicitly
  * live wrapper     — narwhal3d/bearing.py delegates to the required adapter
  * protocol         — missing/stale/invalid core, reply metadata, unaligned pointer
  * no Python reference remains in the public path (no `re`, reference retired)
  * fixture sample   — a live slice cross-checked for EXACT captured messages

Default: load the PACKAGED real core (never build a scratch crate). Override
with --library or NARWHAL_PURE_CORE to qualify a candidate build.

Bundled CPython 3.13 (Unicode 15.1.0):
    python3.13 -B spike/test_bearing_owned.py                # host checks, packaged core
    python3.13 -B spike/test_bearing_owned.py --library PATH # host checks, candidate
"""
import argparse
import ast
import ctypes
import importlib.util
import os
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'native/pure_core/tests/fixtures/bearing_golden.txt'
EXPECTED_COUNT = 28190
EXPECTED_PROVENANCE = 'spike/bearing_reference.py'

# Fixed host presentation for the grammar-error classes. The SPAN that follows
# the label is what is CAPTURED per row (the substring the old reference
# embedded in its error string); the label itself is stable presentation shared
# by the retired reference and the host adapter.
EXACT_LABELS = {
    'BEARING': 'not a bearing (need N/S + angle + E/W)',
    'SURVEYOR': 'not a surveyor coordinate (need dist<N/SangE/W)',
    'ANGLE': 'not an angle',
}
# QUADRANT's offending angle is deliberately NOT captured in the fixture (the
# old reference exposed only a lossy %g), so the sample pins the EXACT fixed
# label prefix and leaves only the (uncaptured) %g tail open.
QUADRANT_PREFIX = 'quadrant angle must be in [0, 90]: '


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_fixture():
    lines = FIXTURE.read_text(encoding='utf-8').split('\n')
    header = [l for l in lines if l.startswith('#')]
    data = [l for l in lines if l and not l.startswith('#')]

    def hfield(name):
        for l in header:
            if l.startswith('# ' + name + '='):
                return l.split('=', 1)[1]
        raise AssertionError('fixture missing `# %s=' % name)

    # Count + provenance are cheap host-side sanity; the fingerprint (FNV-1a64
    # checksum) and every-row replay are enforced by the Rust golden test, not
    # re-derived here.
    return {
        'count': int(hfield('count')),
        'provenance': hfield('provenance'),
        'data': data,
    }


def decode_hexdots(field):
    if field == '':
        return ''
    return ''.join(chr(int(x, 16)) for x in field.split('.'))


def bits_of(field):
    # The fixture stores the little-endian byte hex of the exact f64.
    return struct.unpack('<d', bytes.fromhex(field))[0]


class BearingOwned(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native = load('bearing_owned_native', ROOT / 'narwhal3d/native_bearing.py')
        cls.candidate = load('bearing_owned_candidate', ROOT / 'narwhal3d/bearing.py')
        cls.fixture = read_fixture()

    # ---- object admission (host-only; the ABI never sees non-strings) ----
    def test_object_admission(self):
        for text in [None, 0, [], {}, b'N30E']:
            self.assertEqual(self.native.bearing_angle(text), (None, 'expected a string'))
            self.assertEqual(self.native.parse_surveyor(text), (None, 'expected a string'))
        # The public wrapper refuses identically.
        self.assertEqual(self.candidate.bearing_angle(None), (None, 'expected a string'))
        self.assertEqual(self.candidate.parse_surveyor(b'N30E'), (None, 'expected a string'))

    # ---- OURS safety (no silent fallback) ----
    def test_ours_safety(self):
        for mode in (0, 1):
            fn = self.native.parse_surveyor if mode else self.native.bearing_angle
            self.assertEqual(fn('x' * 4097), (None, 'bearing input exceeds 4096 characters'))
            text = ('9' * 400 + '<N30E') if mode else ('N' + '9' * 400 + 'E')
            self.assertEqual(fn(text), (None, 'bearing number outside finite range'))
        # The long all-zero angle (a valid OK case in the fixture) still parses.
        self.assertEqual(self.native.bearing_angle('N' + '0' * 4093 + 'E'), (90.0, None))
        self.assertEqual(self.native.parse_surveyor('1<N' + '0' * 4091 + 'E'), ((1.0, 90.0), None))

    # ---- live wrapper (thin required-adapter delegate) ----
    def test_live_wrapper(self):
        self.assertEqual(self.candidate.bearing_angle('N30E'), (60.0, None))
        self.assertEqual(self.candidate.parse_surveyor('-2<S30W'), ((-2.0, 240.0), None))
        self.assertEqual(self.candidate.bearing_angle('N95E')[1], 'quadrant angle must be in [0, 90]: 95')

    # ---- fixture header sanity (cheap; the heavy integrity work is Rust's) ----
    def test_fixture_header(self):
        fx = self.fixture
        self.assertEqual(fx['count'], EXPECTED_COUNT)
        self.assertEqual(fx['provenance'], EXPECTED_PROVENANCE)
        self.assertEqual(len(fx['data']), fx['count'])

    # ---- small live sample: verifies actual errors against the CAPTURED message
    #      EXACTLY (label + captured span), not merely a suffix. This also proves
    #      the host frames UTF-32 correctly for a slice of the corpus. ----
    def test_fixture_sample_crosscheck(self):
        fx = self.fixture
        checked = 0
        cap = 300
        saw_ok = saw_grammar = saw_quadrant = False
        for line in fx['data']:
            if checked >= cap:
                break
            if checked >= 60 and saw_ok and saw_grammar and saw_quadrant:
                break
            inp_field, mode, cls, vb, db, sh, _grp = line.split('\t')
            mode = int(mode)
            if cls == 'NONSTR':
                continue
            text = decode_hexdots(inp_field)
            fn = self.native.parse_surveyor if mode else self.native.bearing_angle
            actual = fn(text)
            if cls == 'OK':
                saw_ok = True
                self.assertIsNone(actual[1], (text, actual))
                if mode == 0:
                    self.assertEqual(actual[0].hex(), bits_of(vb).hex(), (text, actual[0]))
                else:
                    # The host returns (distance, angle) for surveyor mode.
                    distance, value = actual[0]
                    self.assertEqual(value.hex(), bits_of(vb).hex(), (text, value))
                    self.assertEqual(distance.hex(), bits_of(db).hex(), (text, distance))
            elif cls == 'QUADRANT':
                saw_quadrant = True
                # Exact fixed label prefix; the offending angle (>90) tail is the
                # deliberately-uncaptured %g value, so only the label is pinned.
                self.assertTrue(actual[1].startswith(QUADRANT_PREFIX), (text, actual[1]))
            else:
                saw_grammar = True
                span = decode_hexdots(sh)
                expected = '%s: %r' % (EXACT_LABELS[cls], span)
                self.assertEqual(actual[1], expected, (text, actual[1], expected))
            checked += 1
        self.assertGreaterEqual(checked, 60, 'sample cross-check too small')
        self.assertTrue(saw_ok, 'no OK rows in sample')
        self.assertTrue(saw_grammar, 'no grammar-error rows in sample')
        self.assertTrue(saw_quadrant, 'no QUADRANT rows in sample')

    # ---- no Python regex reference remains in the public host path ----
    def test_no_python_regex_reference(self):
        for path in ['narwhal3d/native_bearing.py', 'narwhal3d/bearing.py']:
            tree = ast.parse((ROOT / path).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertNotIn('re', [n.name for n in node.names])
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, 're')
        # The retired independent reference is gone from the public tree.
        self.assertFalse((ROOT / 'spike/bearing_reference.py').exists())

    # ---- protocol (retained from RUST-BEARING-12) ----
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
        unaligned = ctypes.cast(ctypes.addressof(reply) + 1, ctypes.POINTER(ctypes.c_uint32))
        self.assertEqual(binding(unaligned, 1, 0, 151, ctypes.byref(reply), 32), 2)


def qualify(library):
    os.environ['NARWHAL_PURE_CORE'] = str(library)
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(BearingOwned))
    print('Fixture cases (grammar, replayed by Rust):', EXPECTED_COUNT)
    return result.wasSuccessful()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    packaged = ROOT / 'narwhal3d/native/libnarwhal_pure_core.so'
    parser.add_argument('--library', type=Path,
                        default=Path(os.environ.get('NARWHAL_PURE_CORE', str(packaged))))
    args = parser.parse_args()
    return 0 if qualify(args.library) else 1


if __name__ == '__main__':
    raise SystemExit(main())
