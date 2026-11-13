"""RUST-ALIAS-17: host admission, bound, fault-injection and live-CRUD
qualification for the native alias grammar.

Runs with bundled CPython 3.13. The grammar differential (5364 cases: corner,
Unicode predicates, seeded random) was archived into the Rust golden test
``native/pure_core/tests/alias_golden.rs`` and the lossless fixture
``native/pure_core/tests/fixtures/alias_golden.txt`` — no Python reference
engine remains in this test. What is retained here is what the Rust test
cannot see: the HOST (``narwhal3d/native_alias.py``) admission, the 4096-codepoint
bound, the malformed-reply and fault-injection rejections, the live
``narwhal3d.alias_table`` CRUD surface, and a small set of
fixture-derived representative parity anchors (expected tuples captured from
the ORIGINAL oracle before it was retired).

The test never skips a missing real-native library. By default it loads the
packaged adjacent library (``narwhal3d/native/libnarwhal_pure_core.<so|dylib|dll>``)
or ``NARWHAL_PURE_CORE``; no scratch compiler is required in the public gate.
``--library`` qualifies an explicit integrated build; ``--candidate``
(optionally) compares the candidate module (``tmp/rust14-alias-candidate.py``)
against the same native expectations.
"""
import argparse
import ast
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('numparse', 'numparse_owned', 'python_text', 'calc_owned', 'alias_owned')


def load(name, path):
    # CPython 3.13 dataclasses resolve a class's module via sys.modules; the
    # loaded module must be registered before exec_module so @dataclass works.
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def scratch(directory, mutation=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'Cargo.toml').write_text(
        '[package]\nname="rust14-alias-qualification"\nversion="0.0.0"\nedition="2021"\n'
        '[lib]\nname="narwhal_pure_core"\ncrate-type=["cdylib","rlib"]\npath="lib.rs"\n')
    original = (ROOT / 'native/pure_core/src/lib.rs').read_text()
    start = original.index('use std::mem::{align_of, size_of};')
    helpers = original[start:original.index('\n#[cfg(test)]', start)]
    for name in MODULES:
        path = ROOT / 'native/pure_core/src' / (name + '.rs')
        if mutation is not None:
            content = path.read_text()
            if name == 'alias_owned':
                old, new = mutation
                assert content.count(old) == 1, old
                content = content.replace(old, new)
            path = directory / (name + '.rs')
            path.write_text(content)
        helpers += '\n#[path=%s]\npub mod %s;\n' % (json.dumps(str(path)), name)
    (directory / 'lib.rs').write_text(helpers)
    return directory / 'Cargo.toml'


def cargo(manifest, command, expect=0):
    result = subprocess.run(['cargo', '+1.95.0', command, '--offline', '--manifest-path', str(manifest)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=180)
    if result.returncode != expect:
        raise AssertionError(result.stdout)
    return result


class AliasGrammarOwned(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native = load('rust14_native_alias', ROOT / 'narwhal3d/native_alias.py')
        cls.live = load('rust14_alias_live', ROOT / 'narwhal3d/alias_table.py')

    def _reject_allocation(self, record):
        """Test-owned allocation trap (production keeps only host glue).

        Records any ``_array_type`` call and raises, so a bound/length
        rejection that precedes buffer allocation is observable as an
        empty record rather than a silent allocation.
        """
        def trap(length):
            record.append(length)
            raise AssertionError('buffer allocation attempted for %d codepoints' % length)
        return trap

    def fault_prefix_wrong_codepoint(self, *args):
        """Fault: claim a (0,1) prefix span for input that does NOT start in ['!_]"""
        reply = ctypes.cast(args[3], ctypes.POINTER(self.native.AliasReply)).contents
        reply.code = 0
        reply.prefix_start = 0
        reply.prefix_end = 1
        reply.command_start = 1
        reply.command_end = 5
        return 0

    def test_representative_fixture_parity(self):
        # Representative anchors of the archived 5364-case differential. Each
        # expected tuple was captured from the ORIGINAL oracle
        # (spike/alias_reference.py, retired into legacy/retired-rust17/)
        # before the native grammar qualified against it; the Rust golden test
        # (native/pure_core/tests/alias_golden.rs) replays the full capture
        # against the real ABI. Here the host and the live module must agree
        # with those captured expectations.
        cases = [
            ("'Zoom", ("'", 'Zoom', ())),
            ("'_Zoom", ("'", 'Zoom', ())),
            ("'_Zoom _Extents", ("'", 'Zoom', ('Extents',))),
            ("Zoom A B", (None, 'Zoom', ('A', 'B'))),
            ("Zoom A\n", (None, 'Zoom', ('A',))),
            ("Zoom A\n\n", (None, None, None)),
            ("!!", (None, None, None)),
            ("", (None, None, None)),
            ("   ", (None, None, None)),
            ("Zoom _A", (None, 'Zoom', ('A',))),
            ("Zoom _A___B", (None, 'Zoom', ('A___B',))),
            ("Zöom", (None, 'Zöom', ())),
            ("_ x", ('_', 'x', ())),
            ("'_SelWindow", ("'", 'SelWindow', ())),
            ("Zoom\n", (None, 'Zoom', ())),
        ]
        for macro, expected in cases:
            self.assertEqual(self.native.parse_macro(macro), expected, macro)
            self.assertEqual(self.live.parse_macro(macro), expected, macro)

    def test_default_table_and_crud_unchanged(self):
        # The 22 default aliases must resolve to identical (prefix, command, options).
        live_aliases = self.live.ALIAS_TABLE
        self.assertEqual(len(live_aliases), 22)
        for name, alias in live_aliases.items():
            expected = self.native.parse_macro(alias.macro)
            self.assertEqual((alias.prefix, alias.command, alias.options), expected,
                             (name, alias.macro))
        # CRUD operations must behave identically (state logic unchanged).
        table = self.live.AliasTable()
        # lookup / resolve_alias
        self.assertIsNotNone(table.lookup("Z"))
        self.assertIsNotNone(table.lookup("z"))
        self.assertIsNone(table.lookup("nope"))
        self.assertIsNone(table.lookup(""))
        self.assertIsNone(table.lookup(None))
        # create
        alias = table.create("MyTest", "'_Zoom _Extents")
        self.assertEqual(alias.name, "MyTest")
        self.assertEqual(alias.prefix, "'")
        self.assertEqual(alias.command, "Zoom")
        self.assertEqual(alias.options, ("Extents",))
        self.assertIsNotNone(table.lookup("mytest"))
        # collision
        with self.assertRaises(ValueError):
            table.create("MyTest", "'_Zoom")
        # delete
        self.assertTrue(table.delete("mytest"))
        self.assertIsNone(table.lookup("mytest"))
        self.assertFalse(table.delete("mytest"))
        # restore_defaults
        table.create("Temp", "_Undo")
        table.restore_defaults()
        self.assertIsNone(table.lookup("temp"))
        self.assertIsNotNone(table.lookup("U"))
        # export_text
        text = table.export_text()
        self.assertIn("Z='_Zoom", text)
        self.assertIn("U=_Undo", text)
        # import_text
        report = table.import_text("MyNew='_Zoom _Selected\n")
        self.assertTrue(report.ok)
        self.assertEqual(report.imported, ("MyNew",))
        self.assertIsNotNone(table.lookup("MyNew"))
        # collision on import
        report2 = table.import_text("Z='_Zoom\n")
        self.assertFalse(report2.ok)
        self.assertIn("collision", " ".join(report2.collisions))
        # malformed name
        report3 = table.import_text("bad name='_Zoom\n")
        self.assertFalse(report3.ok)

    def test_native_call_count_and_admission(self):
        # Every truthy-string grammar input drives exactly one native operation
        # (OK or INVALID reply). Falsy admission and the TypeError path do not
        # cross the ABI.
        before = self.native._native_alias_calls
        cases = ["Zoom", "Zoom A", "'_Zoom", "invalid", "   ", " _", "!!"]
        for c in cases:
            self.native.parse_macro(c)
        self.assertEqual(self.native._native_alias_calls - before, len(cases))
        # Falsy admission: (None, None, None) for None, 0, False, [], '' and NO
        # native call (the admission is the host's, matching the reference
        # ``macro or ''`` which never reaches the grammar for falsy input).
        before = self.native._native_alias_calls
        for falsy in [None, 0, False, [], '']:
            self.assertEqual(self.native.parse_macro(falsy), (None, None, None))
        self.assertEqual(self.native._native_alias_calls, before)
        # Truthy non-string: TypeError (matching re.match admission), no call.
        for non_str in (b'Zoom', 42, ['Zoom']):
            with self.assertRaises(TypeError):
                self.native.parse_macro(non_str)
        self.assertEqual(self.native._native_alias_calls, before)

    def test_bound_rejection(self):
        # 4096 codepoints is the bound; 4097 raises RuntimeError (OURS limit).
        ok = 'a' * 4096
        self.assertIsNotNone(self.native.parse_macro(ok)[1])
        too_long = 'a' * 4097
        with self.assertRaisesRegex(RuntimeError, '4096'):
            self.native.parse_macro(too_long)

    def test_bound_no_allocation_and_reply_admission(self):
        # 4097 codepoints must be rejected BEFORE any buffer allocation or
        # native binding (mock _array_type would record any attempt).
        record = []
        with patch.object(self.native, '_array_type', self._reject_allocation(record)):
            with self.assertRaisesRegex(RuntimeError, '4096'):
                self.native.parse_macro('a' * 4097)
        self.assertEqual(record, [])
        # Counter guard: malformed replies must not count as native operations.
        before = self.native._native_alias_calls
        # Prefix start must be 0.
        def fault_prefix_start(*args):
            reply = ctypes.cast(args[3], ctypes.POINTER(self.native.AliasReply)).contents
            reply.code = 0
            reply.prefix_start = 5
            reply.prefix_end = 6
            reply.command_start = 6
            reply.command_end = 10
            return 0
        with patch.object(self.native, '_required_binding', return_value=fault_prefix_start):
            with self.assertRaisesRegex(RuntimeError, 'prefix start'):
                self.native.parse_macro('Zoom')
        self.assertEqual(self.native._native_alias_calls, before)
        # Prefix end must be 1 when present.
        def fault_prefix_end(*args):
            reply = ctypes.cast(args[3], ctypes.POINTER(self.native.AliasReply)).contents
            reply.code = 0
            reply.prefix_end = 2
            reply.command_start = 2
            reply.command_end = 6
            return 0
        with patch.object(self.native, '_required_binding', return_value=fault_prefix_end):
            with self.assertRaisesRegex(RuntimeError, 'prefix end'):
                self.native.parse_macro("'Zoom")
        self.assertEqual(self.native._native_alias_calls, before)
        # Command must start at or after the prefix end.
        def fault_cmd_before_prefix(*args):
            reply = ctypes.cast(args[3], ctypes.POINTER(self.native.AliasReply)).contents
            reply.code = 0
            reply.prefix_start = 0
            reply.prefix_end = 1
            reply.command_start = 0
            reply.command_end = 5
            return 0
        with patch.object(self.native, '_required_binding', return_value=fault_cmd_before_prefix):
            with self.assertRaisesRegex(RuntimeError, 'command span'):
                self.native.parse_macro("'Zoom")
        self.assertEqual(self.native._native_alias_calls, before)
        # Option span reversed.
        def fault_option_reversed(*args):
            reply = ctypes.cast(args[3], ctypes.POINTER(self.native.AliasReply)).contents
            reply.code = 0
            reply.prefix_start = 0
            reply.prefix_end = 0
            reply.command_start = 0
            reply.command_end = 4
            reply.option_count = 1
            opts = ctypes.cast(args[5], ctypes.POINTER(ctypes.c_uint32))
            opts[0] = 6
            opts[1] = 5
            return 0
        with patch.object(self.native, '_required_binding', return_value=fault_option_reversed):
            with self.assertRaisesRegex(RuntimeError, 'option span 0'):
                self.native.parse_macro('Zoom A')
        self.assertEqual(self.native._native_alias_calls, before)
        # Option span out of bounds (end > n).
        def fault_option_out_of_bounds(*args):
            reply = ctypes.cast(args[3], ctypes.POINTER(self.native.AliasReply)).contents
            reply.code = 0
            reply.prefix_start = 0
            reply.prefix_end = 0
            reply.command_start = 0
            reply.command_end = 4
            reply.option_count = 1
            opts = ctypes.cast(args[5], ctypes.POINTER(ctypes.c_uint32))
            opts[0] = 5
            opts[1] = 99
            return 0
        with patch.object(self.native, '_required_binding', return_value=fault_option_out_of_bounds):
            with self.assertRaisesRegex(RuntimeError, 'option span 0'):
                self.native.parse_macro('Zoom A')
        self.assertEqual(self.native._native_alias_calls, before)
        # Option spans overlapping (second starts before first ends).
        def fault_option_overlap(*args):
            reply = ctypes.cast(args[3], ctypes.POINTER(self.native.AliasReply)).contents
            reply.code = 0
            reply.prefix_start = 0
            reply.prefix_end = 0
            reply.command_start = 0
            reply.command_end = 4
            reply.option_count = 2
            opts = ctypes.cast(args[5], ctypes.POINTER(ctypes.c_uint32))
            opts[0] = 5
            opts[1] = 7
            opts[2] = 6
            opts[3] = 8
            return 0
        with patch.object(self.native, '_required_binding', return_value=fault_option_overlap):
            with self.assertRaisesRegex(RuntimeError, 'option span 1'):
                self.native.parse_macro('Zoom A B C')
        self.assertEqual(self.native._native_alias_calls, before)
        # Option spans before the command end.
        def fault_option_before_command(*args):
            reply = ctypes.cast(args[3], ctypes.POINTER(self.native.AliasReply)).contents
            reply.code = 0
            reply.prefix_start = 0
            reply.prefix_end = 0
            reply.command_start = 0
            reply.command_end = 4
            reply.option_count = 1
            opts = ctypes.cast(args[5], ctypes.POINTER(ctypes.c_uint32))
            opts[0] = 2
            opts[1] = 3
            return 0
        with patch.object(self.native, '_required_binding', return_value=fault_option_before_command):
            with self.assertRaisesRegex(RuntimeError, 'option span 0'):
                self.native.parse_macro('Zoom A')
        self.assertEqual(self.native._native_alias_calls, before)
        # Invalid reply with nonzero metadata must be rejected.
        def fault_invalid_with_metadata(*args):
            reply = ctypes.cast(args[3], ctypes.POINTER(self.native.AliasReply)).contents
            reply.code = 1
            reply.command_start = 0
            reply.command_end = 5
            return 0
        with patch.object(self.native, '_required_binding', return_value=fault_invalid_with_metadata):
            with self.assertRaisesRegex(RuntimeError, 'invalid code with command_end'):
                self.native.parse_macro('Zoom')
        self.assertEqual(self.native._native_alias_calls, before)
        # Option count beyond the allocated span buffer must be rejected.
        def fault_option_count_overflow(*args):
            reply = ctypes.cast(args[3], ctypes.POINTER(self.native.AliasReply)).contents
            reply.code = 0
            reply.prefix_start = 0
            reply.prefix_end = 0
            reply.command_start = 0
            reply.command_end = 4
            reply.option_count = 999
            return 0
        with patch.object(self.native, '_required_binding', return_value=fault_option_count_overflow):
            with self.assertRaisesRegex(RuntimeError, 'option count'):
                self.native.parse_macro('Zoom A')
        self.assertEqual(self.native._native_alias_calls, before)

        # Prefix span (0, 1) is illegal unless the input's first codepoint is in ['!_].
        with patch.object(self.native, '_required_binding', return_value=self.fault_prefix_wrong_codepoint):
            with self.assertRaisesRegex(RuntimeError, 'prefix not first codepoint'):
                self.native.parse_macro('Zoom')
        self.assertEqual(self.native._native_alias_calls, before)

    def test_absent_stale_and_fault_injection_never_replay(self):
        with patch.dict(os.environ, {'NARWHAL_PURE_CORE': '/nonexistent/rust14-alias.so'}):
            with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                self.native.parse_macro('Zoom')
        # Stale: wrong alias identity.
        class StaleLib:
            narwhal_pure_core_abi_version = staticmethod(lambda: 1)
            narwhal_pure_core_alias_identity = staticmethod(lambda: 99)
        with patch.dict(os.environ, {'NARWHAL_PURE_CORE': '/stale/rust14-alias.so'}):
            with patch.object(self.native.ctypes, 'CDLL', return_value=StaleLib()):
                with self.assertRaisesRegex(RuntimeError, 'Stale'):
                    self.native.parse_macro('Zoom')
        # Fault: invalid reply code.
        def fault_rc(*args):
            return 99
        with patch.object(self.native, '_required_binding', return_value=fault_rc):
            with self.assertRaisesRegex(RuntimeError, 'Invalid Rust alias reply'):
                self.native.parse_macro('Zoom')
        # Fault: reserved nonzero.
        real = self.native._required_binding()
        def fault_reserved(*args):
            reply = ctypes.cast(args[3], ctypes.POINTER(self.native.AliasReply)).contents
            reply.reserved = 1
            reply.code = 0
            return 0
        with patch.object(self.native, '_required_binding', return_value=fault_reserved):
            with self.assertRaisesRegex(RuntimeError, 'Invalid Rust alias reply'):
                self.native.parse_macro('Zoom')

    def test_candidate_uses_native_not_regex(self):
        # The live alias_table module is the always-on subject: it must be
        # native-only (no ``re`` import, no ``_MACRO_RE`` module-level name)
        # and its parse_macro / default table must match the native host.
        # This runs WITHOUT a candidate and never reads the tmp candidate
        # path by default.
        self._assert_native_only(self.live)
        for c in ["Zoom", "'_Zoom", "_ x", "Zoom\n\n", "Zoom A\n\n", "!!", ""]:
            self.assertEqual(self.live.parse_macro(c), self.native.parse_macro(c), c)
        # The live default table must equal the native derivation.
        for name, alias in self.live.ALIAS_TABLE.items():
            self.assertEqual((alias.prefix, alias.command, alias.options),
                             self.native.parse_macro(alias.macro), name)
        # When --candidate is given, the candidate must satisfy the SAME checks.
        if self.candidate:
            self._assert_native_only(self.candidate)
            for c in ["Zoom", "'_Zoom", "_ x", "Zoom\n\n", "Zoom A\n\n", "!!", ""]:
                self.assertEqual(self.candidate.parse_macro(c), self.native.parse_macro(c), c)
            # The candidate's default table must match the live table.
            self.assertEqual(len(self.candidate.ALIAS_TABLE), len(self.live.ALIAS_TABLE))
            for name in self.live.ALIAS_TABLE:
                self.assertEqual(self.candidate.ALIAS_TABLE[name].prefix, self.live.ALIAS_TABLE[name].prefix, name)
                self.assertEqual(self.candidate.ALIAS_TABLE[name].command, self.live.ALIAS_TABLE[name].command, name)
                self.assertEqual(self.candidate.ALIAS_TABLE[name].options, self.live.ALIAS_TABLE[name].options, name)

    def _assert_native_only(self, module):
        """Assert ``module`` is native-only: no ``re`` import, no ``_MACRO_RE`` name.

        Reads the module's own source file (``module.__file__``), so the
        default path (the live module) never touches the tmp candidate. The
        docstring may legitimately mention the retired names; the AST check
        targets real Python names, not prose.
        """
        source = Path(module.__file__).resolve().read_text()
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
        self.assertNotIn('re', imported, module.__file__)
        def names(target):
            return {sub.id for sub in ast.walk(target) if isinstance(sub, ast.Name)}

        assigned = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                assigned.update(*[names(t) for t in node.targets])
            elif isinstance(node, ast.AnnAssign) and node.target is not None:
                assigned.update(names(node.target))
        self.assertNotIn('_MACRO_RE', assigned, module.__file__)

    @classmethod
    def tearDownClass(cls):
        pass


def _default_library():
    """Default library: NARWHAL_PURE_CORE env, else the packaged adjacent
    ``narwhal3d/native/libnarwhal_pure_core.<so|dylib|dll>``. The public
    gate must not require a scratch compiler; MAIN-RUST-14 stages the
    matching artifact into that location. ``--library`` overrides either.
    """
    explicit = os.environ.get('NARWHAL_PURE_CORE')
    if explicit:
        return Path(explicit).expanduser()
    suffix = ('narwhal_pure_core.dll' if sys.platform == 'win32' else
              'libnarwhal_pure_core.dylib' if sys.platform == 'darwin' else
              'libnarwhal_pure_core.so')
    return ROOT / 'narwhal3d' / 'native' / suffix


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--library', type=Path, default=None,
                        help='explicit real-native library (overrides the packaged adjacent default)')
    parser.add_argument('--candidate', action='store_true',
                        help='also compare the candidate module (tmp/rust14-alias-candidate.py)')
    parser.add_argument('--mutations', action='store_true')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='rust14-alias-qualification-') as temp:
        if args.library:
            library = Path(args.library).resolve()
            if not library.is_file():
                raise RuntimeError('Explicit real-native qualification library missing')
        else:
            library = _default_library()
            if not library.is_file():
                raise RuntimeError(
                    'Required integrated alias library missing; stage the artifact into '
                    'narwhal3d/native/ or set NARWHAL_PURE_CORE / pass --library')
        os.environ['NARWHAL_PURE_CORE'] = str(library)
        # Provide the candidate if requested.
        if args.candidate:
            candidate_path = ROOT / 'tmp/rust14-alias-candidate.py'
            if candidate_path.is_file():
                AliasGrammarOwned.candidate = load('rust14_alias_candidate', candidate_path)
            else:
                AliasGrammarOwned.candidate = None
        else:
            AliasGrammarOwned.candidate = None
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(AliasGrammarOwned)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful():
            return 1
        if args.mutations:
            mutations = [
                ('prefix handling',
                 "if first == '\\'' as u32 || first == '!' as u32 || first == '_' as u32 {",
                 'if false {'),
                ('option trimming',
                 'while s < tok_end && cps[s] == 0x5f {',
                 'while s < tok_end && cps[s] == 0x5e {'),
            ]
            for index, (label, old, new) in enumerate(mutations):
                manifest = scratch(Path(temp) / ('mutant' + str(index)), (old, new))
                red = subprocess.run(['cargo', '+1.95.0', 'test', '--offline', '--manifest-path', str(manifest)],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=180)
                if red.returncode != 101 or 'test result: FAILED' not in red.stdout:
                    raise AssertionError('Mutation did not fail tests: ' + label + '\n' + red.stdout)
                print('MUTATION RED confirmed:', label)
            print(cargo(scratch(Path(temp) / 'restored'), 'test').stdout)
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
