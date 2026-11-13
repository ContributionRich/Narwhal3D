"""RUST-ALIAS-14 differential qualification.

Runs with bundled CPython 3.13. Compares the native alias-grammar host
(``narwhal3d.native_alias.parse_macro``) against the independent reference
(``spike/alias_reference.py`` — pure Python, no native hook) on the live
``narwhal3d.alias_table`` module. The optional candidate
(``tmp/rust14-alias-candidate.py`` — whole module with the native grammar) is
compared when ``--candidate`` is given.

The test never skips a missing real-native library. By default it loads the
packaged adjacent library (``narwhal3d/native/libnarwhal_pure_core.<so|dylib|dll>``)
or ``NARWHAL_PURE_CORE``; no scratch compiler is required in the public gate.
``--library`` qualifies an explicit integrated build; ``--candidate`` enables the
optional candidate comparison; ``--mutations`` builds two external source copies
and requires each to fail the same Rust tests (prefix handling and option trimming).
"""
import argparse
import ast
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import random
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
        cls.reference = load('rust14_alias_reference', ROOT / 'spike/alias_reference.py')
        cls.live = load('rust14_alias_live', ROOT / 'narwhal3d/alias_table.py')
        cls.comparisons = 0

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

    def compare(self, macro):
        """Compare native, reference and live parse_macro on one input."""
        expected = self.reference.parse_macro(macro)
        actual = self.native.parse_macro(macro)
        live = self.live.parse_macro(macro)
        type(self).comparisons += 1
        self.assertEqual(actual, expected, (repr(macro), actual, expected))
        self.assertEqual(live, expected, (repr(macro), live, expected))
        return expected

    def test_grammar_corner_cases(self):
        cases = [
            # literal prefix corner cases
            "_", "__", "___", "____", "_____", "______",
            "'_", "'__", "'___", "'____", "'______",
            "!_", "!__", "!___", "!____", "!______",
            "_ x", "_  x", "_ _", "_ _ x", "_ _ _", "_ _ _ x",
            "'_ x", "'__ x", "!_ x", "!__ x",
            "' _", "'__ _", "!_ _", "!__ _",
            "_\tZoom", "'\t_Zoom", "'\t_ x", "'\nZoom All",
            "Zoom_", "_Zoom_", "_a_b", "Zoom _A", "Zoom _", "Zoom ___", "Zoom __",
            "a__", "__a", "___a", "a___", "a__ b", "'a__", "!a__",
            # defaults (22 macros)
            "'_Zoom", "'_Zoom _Extents", "'_Zoom _Target", "'_Zoom _All _Extents",
            "'_Zoom _Selected", "'_Zoom _All _Selected", "'_Snap", "'_Ortho",
            "'_Planar", "! _Move", "_Undo", "! _PointsOn", "! _PointsOff",
            "'_SelCrossing", "'_SelWindow", "'_CurvatureGraph",
            "'_CurvatureGraphOff", "! _DeleteSubCrv", "'_SelPolysrf",
            "! _OptionsPage _PlugIns", "! _OptionsPage _DisplayModes",
            # whitespace / control / newline
            "Zoom\n", "Zoom\n\n", "Zoom\n\n\n", "Zoom \n", "Zoom  \n",
            "Zoom A\n", "Zoom A B\n", "Zoom A\n\n", "Zoom Ext\nMore",
            "Zoom x\ny", "a\nb", "Zoom \n x", "Zoom\nx", "Zoom\n A", "Zoom\nA B",
            "Zoom\x0bA", "Zoom \r A", "Zoom\r\nA", "Zoom A\rB",
            "Zoom\xa0A", "Zoom\u2028A", "Zoom A\u2028B", "Zoom\x0c", "Zoom\u2029A",
            "Zoom A\u1680B", "Zoom A\u2000B", "Zoom A\u3000B", "Zoom\x85A",
            "Zoom \x01 A", "Zoom\t", "Zoom \t ", "Zoom A B",
            # Unicode word characters
            "Zöom", "½", "Ⅻ", "١٢٣", "Zoom½", "½Zoom", "ｚoom", "𝕌nicode",
            "Zoom²", "fooᢅ", "一", "Z©", "Zoom©", "Zo\u0308m",
            # invalid
            "", "   ", "\t", "\n", " _", "' ", "'  ", "_!", "__!", "!!", "''",
            "'!", "! ", "_!_", "Zoom A\n\n", "Zoom Ext\nMore", "Zoom x\ny",
            # option trimming
            "Zoom _A", "Zoom _B", "Zoom ___", "Zoom _A___B", "Zoom A_B",
            "Zoom  _A  _B  ", "a _", "Zoom _A _B", "Zoom _A\u2028_B",
        ]
        for c in cases:
            self.compare(c)

    def test_unicode_word_and_whitespace_predicates(self):
        # Word characters: alnum (alpha | numeric) + underscore.
        for cp in [0x2070, 0x2071, 0x2074, 0x24ea, 0x2160, 0xb2, 0xb3, 0xb9,
                   0xbc, 0x661, 0x110f, 0x136a, 0x301, 0x308, 0x900, 0xb7,
                   0x2027, 0x2038, 0x1100, 0x1101, 0x1102, 0x1103, 0x1104,
                   0x1105, 0x1106, 0x1107, 0x1108, 0x1109, 0x110a]:
            c = chr(cp)
            self.compare(c)
            self.compare('foo' + c)
            self.compare(c + 'foo')
        # Whitespace: is_space (includes \xa0, \u2028, \u2000, \u1680, \x85, etc.)
        for cp in [0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x20, 0x85, 0xa0, 0x1680,
                   0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006,
                   0x2007, 0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f,
                   0x205f, 0x3000, 0x1c, 0x1d, 0x1e, 0x1f]:
            c = chr(cp)
            self.compare('Zoom' + c + 'A')
            self.compare('Zoom ' + c + ' A')
            self.compare(c + 'Zoom')
            self.compare('Zoom' + c)
        # Non-word, non-space: control chars, surrogates, null.
        for cp in [0x00, 0x01, 0x7f, 0xad, 0x200b, 0xfeff, 0x2060, 0x180e,
                   0x200c, 0x200d]:
            c = chr(cp)
            self.compare('Zoom' + c + 'A')
            self.compare('Zoom ' + c + ' A')

    def test_seeded_random(self):
        rng = random.Random(14)
        # A small alphabet covering word, space, control, prefix and marker chars.
        alphabet = list("abcXYZ_zoom") + list(" \t\n\r\u2028\xa0\u1680\u2000") + \
                   list("'!_") + list("½Ⅻ²ᢅ一١𝕌©\u0308\x0b\x0c\x85\x01")
        for _ in range(5000):
            length = rng.randint(0, 12)
            macro = ''.join(rng.choice(alphabet) for _ in range(length))
            self.compare(macro)

    def test_default_table_and_crud_unchanged(self):
        # The 22 default aliases must resolve to identical (prefix, command, options).
        live_aliases = self.live.ALIAS_TABLE
        self.assertEqual(len(live_aliases), 22)
        for name, alias in live_aliases.items():
            expected = self.reference.parse_macro(alias.macro)
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
        # and its parse_macro / default table must match the native host and
        # the independent reference. This runs WITHOUT a candidate and never
        # reads the tmp candidate path by default.
        self._assert_native_only(self.live)
        for c in ["Zoom", "'_Zoom", "_ x", "Zoom\n\n", "Zoom A\n\n", "!!", ""]:
            self.assertEqual(self.live.parse_macro(c), self.reference.parse_macro(c), c)
            self.assertEqual(self.live.parse_macro(c), self.native.parse_macro(c), c)
        # The live default table must equal the native + reference derivation.
        for name, alias in self.live.ALIAS_TABLE.items():
            expected = self.reference.parse_macro(alias.macro)
            self.assertEqual((alias.prefix, alias.command, alias.options), expected, name)
            self.assertEqual((alias.prefix, alias.command, alias.options),
                             self.native.parse_macro(alias.macro), name)
        # When --candidate is given, the candidate must satisfy the SAME checks.
        if self.candidate:
            self._assert_native_only(self.candidate)
            for c in ["Zoom", "'_Zoom", "_ x", "Zoom\n\n", "Zoom A\n\n", "!!", ""]:
                self.assertEqual(self.candidate.parse_macro(c), self.reference.parse_macro(c), c)
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
        print('Grammar differential comparisons:', cls.comparisons)


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
