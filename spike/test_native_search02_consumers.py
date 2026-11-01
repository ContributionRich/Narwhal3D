#!/usr/bin/env python3
"""Real cmdbar consumers over the real native library (explicit library, never skipped).

Correctness is proven by differential comparison: the native route through the real
``cmdbar`` functions must equal the pure Python reference, obtained by disabling the native
route with a patch. Sentinel routes alone are not accepted as proof.

    python3 -B spike/test_native_search02_consumers.py --library PATH
"""
import argparse
import importlib.util
import os
from pathlib import Path
import random
import sys
import types
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "narwhal_search02_consumer_app"
ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

ARGS = None
native = None
cmdbar = None


def _load(library):
    os.environ["NARWHAL_PURE_CORE"] = str(library)
    package = types.ModuleType(PACKAGE)
    package.__path__ = [os.path.join(ROOT, "narwhal3d")]
    sys.modules[PACKAGE] = package
    loaded = {}
    for name in ("native_command_search", "cmdbar"):
        spec = importlib.util.spec_from_file_location(
            PACKAGE + "." + name, os.path.join(ROOT, "narwhal3d", name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[module.__name__] = module
        setattr(package, name, module)
        spec.loader.exec_module(module)
        loaded[name] = module
    return loaded["native_command_search"], loaded["cmdbar"]


def high_cardinality_table(seed, size=300):
    rng = random.Random(seed)
    names = []
    for index in range(size):
        length = rng.randrange(1, 13)
        names.append("".join(rng.choice(ALPHABET) for _ in range(length)) + f"{index % 7}")
    names += names[:20]  # duplicate names are distinct entries
    return names


def native_calls():
    return native._native_prefix_calls, native._native_fuzzy_calls


class NativeRouteTests(unittest.TestCase):
    def setUp(self):
        native._reset_for_tests()
        self.table = high_cardinality_table(9081)

    def _pure(self, function, *args):
        with patch.object(native, "prefix_matches", return_value=None), \
                patch.object(native, "fuzzy_matches", return_value=None):
            return function(*args)

    def test_explicit_library_is_the_one_in_use(self):
        native.prefix_matches("Cmd", self.table, force=True)
        self.assertEqual(native._prefix_binding() is not None, True)
        self.assertEqual(native._signature, str(Path(ARGS.library).resolve()))

    def test_high_cardinality_prefix_routes_natively_and_matches_the_pure_reference(self):
        for query in ("a", "A", "ab", "Zz", "0", "a1", "zzzzzzzz"):
            with self.subTest(query=query):
                before = native_calls()[0]
                got = cmdbar.match_commands(query, self.table)
                self.assertEqual(native_calls()[0], before + 1, "native route not taken")
                self.assertEqual(got, self._pure(cmdbar.match_commands, query, self.table))

    def test_high_cardinality_fuzzy_routes_natively_and_matches_the_pure_reference(self):
        usage = {name: (index * 37) % 11 for index, name in enumerate(self.table[:50])}
        for query in ("ab", "a1", "zq", "xyz"):
            for mapping in (None, usage):
                with self.subTest(query=query, usage=mapping is not None):
                    before = native_calls()[1]
                    got = cmdbar.fuzzy_matches(query, self.table, mapping)
                    self.assertEqual(native_calls()[1], before + 1, "native route not taken")
                    self.assertEqual(got, self._pure(cmdbar.fuzzy_matches, query, self.table, mapping))

    def test_small_tables_stay_on_the_pure_route(self):
        before = native_calls()
        self.assertEqual(cmdbar.match_commands("Li", ["line", "Line", "Limit"]),
                         ["Limit", "Line", "line"])
        self.assertEqual(cmdbar.fuzzy_matches("ab", ["xab", "abc"]), (["abc"], ["xab"]))
        self.assertEqual(native_calls(), before)

    def test_empty_query_returns_empty_without_native_calls(self):
        before = native_calls()
        self.assertEqual(cmdbar.match_commands("", self.table), [])
        self.assertEqual(cmdbar.fuzzy_matches("", self.table), ([], []))
        self.assertEqual(native_calls(), before)

    def test_duplicate_names_are_retained_through_the_native_route(self):
        table = ["Line"] * 130 + ["Limit"]
        got = cmdbar.match_commands("Li", table)
        self.assertEqual(got, self._pure(cmdbar.match_commands, "Li", table))
        self.assertEqual(got.count("Line"), 130)
        self.assertEqual(got[-1], "Line")

    def test_unicode_query_or_names_use_the_python_route(self):
        before = native_calls()
        table = self.table + ["\u00e9clair"]
        self.assertEqual(cmdbar.match_commands("\u00e9", table),
                         self._pure(cmdbar.match_commands, "\u00e9", table))
        self.assertEqual(cmdbar.fuzzy_matches("ab", table + ["\u00e9ab"]),
                         self._pure(cmdbar.fuzzy_matches, "ab", table + ["\u00e9ab"], None))
        self.assertEqual(native_calls(), before)

    def test_usage_outside_i64_uses_the_python_route(self):
        before = native_calls()
        usage = {self.table[0]: 2 ** 63}
        self.assertEqual(cmdbar.fuzzy_matches("ab", self.table, usage),
                         self._pure(cmdbar.fuzzy_matches, "ab", self.table, usage))
        self.assertEqual(native_calls()[1], before[1])


class CompletionConsumerTests(unittest.TestCase):
    """completion_lists/autocomplete over real routes; hand-derived small-table expectations."""

    def setUp(self):
        native._reset_for_tests()

    def test_divider_only_when_both_groups_exist(self):
        self.assertEqual(cmdbar.completion_lists("ab", ["xab", "abc"]), ["abc", None, "xab"])
        self.assertEqual(cmdbar.completion_lists("Li", ["line", "Line", "Limit"]),
                         ["Limit", "Line", "line"])
        self.assertEqual(cmdbar.completion_lists("pon", ["Point", "xpon"]), ["Point", "xpon"])
        self.assertNotIn(None, cmdbar.completion_lists("ab", ["abc", "abd"]))
        self.assertEqual(cmdbar.completion_lists("po", ["Point", "xpo"]), ["Point", None, "xpo"])

    def test_pon_does_not_fuzzy_complete_to_point(self):
        self.assertIsNone(cmdbar.autocomplete("pon", ["Point"]))
        self.assertIsNone(cmdbar.autocomplete("pon", ["Point", "xpon"]))
        self.assertEqual(cmdbar.autocomplete("po", ["Point", "xpo"]), "Point")

    def test_exact_prefix_order_is_the_head_of_match_commands(self):
        names = ["line", "Line", "Limit"]
        self.assertEqual(cmdbar.autocomplete("Li", names), "Limit")
        self.assertEqual(cmdbar.completion_lists("Li", names), cmdbar.match_commands("Li", names))

    def test_high_cardinality_completion_matches_the_pure_reference(self):
        table = high_cardinality_table(20260)
        for query in ("a", "ab", "Cmd", "zq"):
            with self.subTest(query=query):
                got = cmdbar.completion_lists(query, table)
                with patch.object(native, "prefix_matches", return_value=None), \
                        patch.object(native, "fuzzy_matches", return_value=None):
                    expected = cmdbar.completion_lists(query, table)
                self.assertEqual(got, expected)
                self.assertEqual(cmdbar.autocomplete(query, table),
                                 cmdbar.match_commands(query, table)[0]
                                 if cmdbar.match_commands(query, table) else None)


def main():
    global ARGS, native, cmdbar
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", default=os.environ.get("NARWHAL_PURE_CORE"))
    ARGS = parser.parse_args()
    if not ARGS.library:
        parser.error("--library or NARWHAL_PURE_CORE is required; native tests may not silently skip")
    library = Path(ARGS.library).resolve()
    if not library.is_file():
        parser.error(f"native library not found: {library}")
    ARGS.library = str(library)
    native, cmdbar = _load(library)
    suite = unittest.TestSuite([
        unittest.defaultTestLoader.loadTestsFromTestCase(NativeRouteTests),
        unittest.defaultTestLoader.loadTestsFromTestCase(CompletionConsumerTests),
    ])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
