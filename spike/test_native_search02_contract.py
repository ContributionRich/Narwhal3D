#!/usr/bin/env python3
"""Independent command-search contract: hand-derived fixtures vs the pure reference.

Expected outputs come from ``spike/fixtures/native_search02.json`` (hand derivations),
never from Rust. Tables stay below the native gate, so this exercises the pure Python
reference that native results must match. Needs no native library.
"""
import importlib.util
import json
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "spike", "fixtures", "native_search02.json")
PACKAGE = "narwhal_search02_contract_app"


def _load_cmdbar():
    package = types.ModuleType(PACKAGE)
    package.__path__ = [os.path.join(ROOT, "narwhal3d")]
    sys.modules[PACKAGE] = package
    spec = importlib.util.spec_from_file_location(
        PACKAGE + ".cmdbar", os.path.join(ROOT, "narwhal3d", "cmdbar.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[module.__name__] = module
    spec.loader.exec_module(module)
    setattr(package, "cmdbar", module)
    return module


cmdbar = _load_cmdbar()
with open(FIXTURES, encoding="utf-8") as handle:
    FIXTURE_DATA = json.load(handle)


class PrefixContractTests(unittest.TestCase):
    def test_every_prefix_fixture_matches_its_hand_derivation(self):
        self.assertGreaterEqual(len(FIXTURE_DATA["prefix"]), 9)
        for case in FIXTURE_DATA["prefix"]:
            with self.subTest(case=case["id"]):
                got = cmdbar.match_commands(case["query"], tuple(case["names"]))
                self.assertEqual(got, case["expected"], case["derivation"])

    def test_prefix_fixtures_stay_below_the_native_gate(self):
        for case in FIXTURE_DATA["prefix"]:
            self.assertLess(len(case["names"]), 128, case["id"])


class FuzzyContractTests(unittest.TestCase):
    def test_every_fuzzy_fixture_matches_its_hand_derivation(self):
        self.assertGreaterEqual(len(FIXTURE_DATA["fuzzy"]), 22)
        for case in FIXTURE_DATA["fuzzy"]:
            with self.subTest(case=case["id"]):
                exact, inexact = cmdbar.fuzzy_matches(
                    case["query"], tuple(case["names"]), case["usage"])
                self.assertEqual({"exact": exact, "inexact": inexact}, case["expected"],
                                 case["derivation"])

    def test_adversarial_fixtures_isolate_every_ordering_key(self):
        isolated = {case.get("isolates") for case in FIXTURE_DATA["fuzzy"]}
        for key in ("start", "span", "usage", "usage-negative", "length",
                    "alphabetical", "index", "group"):
            self.assertIn(key, isolated)

    def test_fuzzy_inexact_group_never_repeats_an_exact_prefix_name(self):
        for case in FIXTURE_DATA["fuzzy"]:
            exact, inexact = cmdbar.fuzzy_matches(
                case["query"], tuple(case["names"]), case["usage"])
            self.assertEqual(set(exact) & set(inexact), set(), case["id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
