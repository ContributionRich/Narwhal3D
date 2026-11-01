#!/usr/bin/env python3
"""Python adapter admission, pre-allocation refusal and iterable consumption (no library).

Native calls are replaced by ctypes-compatible fakes, so these tests prove what Python
does before and around the FFI call. Compiled-native behaviour is tested elsewhere.
"""
import gc
import importlib.util
import os
import re
import sys
import types
import unittest
import weakref
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "narwhal_search02_adapter_app"
RUST_SOURCE = os.path.join(ROOT, "native", "pure_core", "src", "command_search.rs")
MIB16 = 16 * 1024 * 1024


def _load_package():
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


native, cmdbar = _load_package()


class FakePrefix:
    """ctypes-shaped stand-in: records the call, reports zero matches."""

    def __init__(self):
        self.calls = 0

    def __call__(self, *args):
        self.calls += 1
        return 0


class FakeFuzzy(FakePrefix):
    pass


def _rust_constant(name):
    with open(RUST_SOURCE, encoding="utf-8") as handle:
        text = handle.read()
    match = re.search(name + r": usize = ([^;]+);", text)
    if match is None:
        raise AssertionError(f"{name} not found in command_search.rs")
    return eval(match.group(1).replace("_", ""), {})


class AdmissionMirrorTests(unittest.TestCase):
    def test_python_limits_equal_the_rust_source_values(self):
        self.assertEqual(native._MAX_QUERY_BYTES, _rust_constant("MAX_QUERY_BYTES"))
        self.assertEqual(native._MAX_NAMES, _rust_constant("MAX_NAMES"))
        self.assertEqual(native._MAX_CONCAT_BYTES, _rust_constant("MAX_CONCAT_BYTES"))


class PrefixAdmissionTests(unittest.TestCase):
    def setUp(self):
        native._reset_for_tests()
        self.fake = FakePrefix()

    def _run(self, prefix, names, **kwargs):
        with patch.object(native, "_prefix_binding", return_value=self.fake):
            return native.prefix_matches(prefix, names, **kwargs)

    def test_query_at_limit_reaches_native_and_one_more_byte_does_not(self):
        self.assertEqual(self._run("a" * native._MAX_QUERY_BYTES, ["b"], force=True), [])
        self.assertEqual(self.fake.calls, 1)
        self.assertIsNone(self._run("a" * (native._MAX_QUERY_BYTES + 1), ["b"], force=True))
        self.assertEqual(self.fake.calls, 1)

    def test_name_count_at_limit_reaches_native_and_one_more_entry_is_refused_before_packing(self):
        names = ["x"] * native._MAX_NAMES
        self.assertEqual(self._run("a", names, force=True), [])
        self.assertEqual(self.fake.calls, 1)
        with patch.object(native, "_pack", side_effect=AssertionError("packed before admission")):
            self.assertIsNone(self._run("a", ["x"] * (native._MAX_NAMES + 1), force=True))
        self.assertEqual(self.fake.calls, 1)

    def test_total_name_bytes_at_limit_reach_native_and_one_more_is_refused_before_packing(self):
        self.assertEqual(self._run("b", ["a" * MIB16], force=True), [])
        self.assertEqual(self.fake.calls, 1)
        with patch.object(native, "bytearray", create=True,
                          side_effect=AssertionError("allocated before admission")):
            self.assertIsNone(self._run("b", ["a" * (MIB16 + 1)], force=True))
        self.assertEqual(self.fake.calls, 1)

    def test_unsupported_domains_take_the_existing_python_route_without_native(self):
        self.assertIsNone(self._run("é", ["éclair"], force=True))
        self.assertIsNone(self._run("a", ["é"], force=True))
        self.assertEqual(self.fake.calls, 0)

    def test_missing_library_returns_before_packing(self):
        with patch.object(native, "_prefix_binding", return_value=None), \
                patch.object(native, "_pack", side_effect=AssertionError("packed without library")):
            self.assertIsNone(native.prefix_matches("a", ["a"], force=True))


class FuzzyAdmissionTests(unittest.TestCase):
    def setUp(self):
        native._reset_for_tests()
        self.fake = FakeFuzzy()

    def _run(self, typed, names, usage=None, **kwargs):
        with patch.object(native, "_fuzzy_binding_for_library", return_value=self.fake):
            return native.fuzzy_matches(typed, names, usage, **kwargs)

    def test_fuzzy_query_limit_and_name_limit_match_the_prefix_contract(self):
        self.assertEqual(self._run("a" * native._MAX_QUERY_BYTES, ["b"], force=True), ([], []))
        self.assertEqual(self.fake.calls, 1)
        with patch.object(native, "_pack", side_effect=AssertionError("packed before admission")):
            self.assertIsNone(self._run("a" * (native._MAX_QUERY_BYTES + 1), ["b"], force=True))
            self.assertIsNone(self._run("a", ["x"] * (native._MAX_NAMES + 1), force=True))
        self.assertEqual(self.fake.calls, 1)

    def test_unsupported_usage_extremes_take_the_python_route(self):
        self.assertIsNone(self._run("ab", ["xab"], {"xab": 2 ** 63}, force=True))
        self.assertEqual(self.fake.calls, 0)


def _prefix_reply(code=0, indices=(), used=0, raise_with=None):
    def fake(*args):
        if raise_with is not None:
            raise raise_with
        for slot, value in enumerate(indices):
            args[6][slot] = value
        args[8]._obj.value = used
        return code
    return fake


def _fuzzy_reply(code=0, indices=(), exact=0, total=0, raise_with=None):
    def fake(*args):
        if raise_with is not None:
            raise raise_with
        for slot, value in enumerate(indices):
            args[7][slot] = value
        args[9]._obj.value = exact
        args[10]._obj.value = total
        return code
    return fake


class ReplyRefusalTests(unittest.TestCase):
    NAMES = ["Line", "Line", "Limit"]

    def setUp(self):
        native._reset_for_tests()

    def _prefix(self, reply):
        with patch.object(native, "_prefix_binding", return_value=reply):
            return native.prefix_matches("Li", self.NAMES, force=True)

    def _fuzzy(self, reply, names=None):
        with patch.object(native, "_fuzzy_binding_for_library", return_value=reply):
            return native.fuzzy_matches("ab", names or ["xab", "abc"], None, force=True)

    def test_prefix_control_accepts_a_valid_reply(self):
        before = native._native_prefix_calls
        self.assertEqual(self._prefix(_prefix_reply(indices=(2, 0, 1), used=3)),
                         ["Limit", "Line", "Line"])
        self.assertEqual(native._native_prefix_calls, before + 1)

    def test_prefix_refusals_return_none_without_counting(self):
        refusals = {
            "nonzero status -1": _prefix_reply(code=-1, indices=(0,), used=1),
            "nonzero status -3": _prefix_reply(code=-3, used=0),
            "runtime error from the call": _prefix_reply(raise_with=RuntimeError("boom")),
            "os error from the call": _prefix_reply(raise_with=OSError("boom")),
            "count larger than the table": _prefix_reply(indices=(0, 1, 2), used=4),
            "duplicate index": _prefix_reply(indices=(0, 0), used=2),
            "index past the table": _prefix_reply(indices=(7,), used=1),
            "maximal unsigned index": _prefix_reply(indices=(2 ** 64 - 1,), used=1),
            "valid prefix then duplicate": _prefix_reply(indices=(1, 1), used=2),
        }
        for label, reply in refusals.items():
            with self.subTest(refusal=label):
                before = native._native_prefix_calls
                self.assertIsNone(self._prefix(reply))
                self.assertEqual(native._native_prefix_calls, before)

    def test_fuzzy_control_splits_exact_and_inexact_groups(self):
        before = native._native_fuzzy_calls
        self.assertEqual(self._fuzzy(_fuzzy_reply(indices=(1, 0), exact=1, total=2)),
                         (["abc"], ["xab"]))
        self.assertEqual(native._native_fuzzy_calls, before + 1)

    def test_fuzzy_refusals_return_none_without_counting(self):
        refusals = {
            "nonzero status": _fuzzy_reply(code=-2, indices=(1, 0), exact=1, total=2),
            "runtime error from the call": _fuzzy_reply(raise_with=RuntimeError("boom")),
            "exact count above total": _fuzzy_reply(indices=(1, 0), exact=2, total=1),
            "total above the table": _fuzzy_reply(indices=(1, 0), exact=0, total=3),
            "duplicate across the groups": _fuzzy_reply(indices=(0, 0), exact=1, total=2),
            "index past the table in the inexact group": _fuzzy_reply(indices=(1, 9), exact=1, total=2),
        }
        for label, reply in refusals.items():
            with self.subTest(refusal=label):
                before = native._native_fuzzy_calls
                self.assertIsNone(self._fuzzy(reply))
                self.assertEqual(native._native_fuzzy_calls, before)


class SpyPrefix:
    """Records the packed bytes, lengths and usage the native side would receive."""

    def __init__(self):
        self.seen = []

    def __call__(self, *args):
        self.seen.append((bytes(args[2]), list(args[4])))
        return 0


class SpyFuzzy:
    def __init__(self):
        self.seen = []

    def __call__(self, *args):
        self.seen.append((bytes(args[2]), list(args[4]), list(args[5])))
        return 0


class CacheCorrectnessTests(unittest.TestCase):
    def setUp(self):
        native._reset_for_tests()

    def _prefix(self, spy, prefix, names):
        with patch.object(native, "_prefix_binding", return_value=spy):
            return native.prefix_matches(prefix, names, force=True)

    def _fuzzy(self, spy, typed, names, usage=None):
        with patch.object(native, "_fuzzy_binding_for_library", return_value=spy):
            return native.fuzzy_matches(typed, names, usage, force=True)

    def test_in_place_mutation_is_repacked_by_content(self):
        spy = SpyPrefix()
        names = ["Line", "Limit"]
        self._prefix(spy, "L", names)
        names[0] = "Zed"
        self._prefix(spy, "L", names)
        self.assertEqual(spy.seen[0], (b"LineLimit", [4, 5]))
        self.assertEqual(spy.seen[1], (b"ZedLimit", [3, 5]))

    def test_reorder_is_repacked_in_the_new_order(self):
        spy = SpyPrefix()
        self._prefix(spy, "L", ["Line", "Limit"])
        self._prefix(spy, "L", ["Limit", "Line"])
        self.assertEqual(spy.seen[1], (b"LimitLine", [5, 4]))

    def test_same_content_replacement_reuses_one_packed_table(self):
        spy = SpyPrefix()
        first = ["Line", "Limit"]
        self._prefix(spy, "L", first)
        packed = native._pack_cache["packed"]
        self._prefix(spy, "L", list(first))
        self.assertIs(native._pack_cache["packed"], packed)
        self.assertEqual(spy.seen[0], spy.seen[1])

    def test_only_the_latest_table_is_retained(self):
        spy = SpyPrefix()
        self._prefix(spy, "L", ["Line"] * 3)
        self._prefix(spy, "L", ["Limit"])
        self.assertEqual(native._pack_cache["names"], ("Limit",))

    def test_usage_is_never_reused_from_a_previous_call(self):
        spy = SpyFuzzy()
        names = ["xab", "yab"]
        self._fuzzy(spy, "ab", names, {"yab": 1})
        self._fuzzy(spy, "ab", names, {"xab": 7})
        self.assertEqual(spy.seen[0][2], [0, 1])
        self.assertEqual(spy.seen[1][2], [7, 0])

    def test_unicode_after_ascii_is_refused_and_leaves_the_ascii_cache_usable(self):
        spy = SpyPrefix()
        self._prefix(spy, "L", ["Line"])
        self.assertIsNone(self._prefix(spy, "L", ["Lïne"]))
        self.assertEqual(len(spy.seen), 1)
        self._prefix(spy, "L", ["Line", "Limit"])
        self.assertEqual(spy.seen[1], (b"LineLimit", [4, 5]))

    def test_ascii_after_an_over_limit_refusal_packs_correctly(self):
        spy = SpyPrefix()
        self.assertIsNone(self._prefix(spy, "L", ["Lx"] * (native._MAX_NAMES + 1)))
        self._prefix(spy, "L", ["Lab", "Lc"])
        self.assertEqual(spy.seen, [(b"LabLc", [3, 2])])

    def test_duplicate_names_keep_their_original_order_in_the_packed_bytes(self):
        spy = SpyPrefix()
        self._prefix(spy, "b", ["b", "a", "b"])
        self.assertEqual(spy.seen, [(b"bab", [1, 1, 1])])


class Name(str):
    """str subclass so tests can weak-reference individual names (str itself cannot)."""


class PackEquivalenceTests(unittest.TestCase):
    def setUp(self):
        native._reset_for_tests()

    def test_accepted_tables_pack_to_the_same_bytes_and_lengths_as_per_name_encoding(self):
        for names in ([], [""], ["", "a", ""], ["Line", "line"], ["x\x00y"], [Name("Sub")],
                      ["~" * 3, "!"]):
            with self.subTest(names=names):
                packed = native._pack(tuple(names))
                self.assertEqual(bytes(packed[1]), b"".join(n.encode("ascii") for n in names))
                self.assertEqual(list(packed[2]), [len(n) for n in names])

    def test_refused_tables_return_none(self):
        for names in ([b"bytes"], ["ok", 3], ["é"], [None], ["a", "éclair"]):
            with self.subTest(names=names):
                self.assertIsNone(native._pack(tuple(names)))


class RetentionTests(unittest.TestCase):
    def setUp(self):
        native._reset_for_tests()

    def _prefix(self, spy, prefix, names):
        with patch.object(native, "_prefix_binding", return_value=spy):
            return native.prefix_matches(prefix, names, force=True)

    def test_only_the_latest_table_survives_many_distinct_tables(self):
        spy = SpyPrefix()
        refs = []
        for round_index in range(50):
            names = [Name(f"Cmd{round_index:03d}x{index:03d}") for index in range(200)]
            refs.append(weakref.ref(names[0]))
            self._prefix(spy, "C", names)
            del names
        gc.collect()
        self.assertIsNotNone(refs[-1]())
        self.assertEqual([index for index, ref in enumerate(refs[:-1]) if ref() is not None], [])

    def test_refused_over_limit_table_is_not_retained_and_keeps_the_cache(self):
        spy = SpyPrefix()
        small = [Name("Cab")]
        self._prefix(spy, "C", small)
        packed = native._pack_cache["packed"]
        names = [Name("Lx") for _ in range(native._MAX_NAMES + 1)]
        ref = weakref.ref(names[0])
        self.assertIsNone(self._prefix(spy, "L", names))
        del names
        gc.collect()
        self.assertIsNone(ref())
        self.assertIs(native._pack_cache["packed"], packed)
        self.assertEqual(len(spy.seen), 1)


class IterableConsumptionTests(unittest.TestCase):
    def setUp(self):
        native._reset_for_tests()

    def test_generator_is_consumed_exactly_once_on_the_pure_route(self):
        table = ["Line", "line", "LinearDimension", "Limit"]
        seen = []

        def generator():
            for name in table:
                seen.append(name)
                yield name

        self.assertEqual(cmdbar.match_commands("Li", generator()),
                         ["Limit", "Line", "LinearDimension", "line"])
        self.assertEqual(seen, table)

    def test_generator_is_consumed_exactly_once_on_the_native_route(self):
        table = [f"Command{index:03d}" for index in range(200)]
        seen = []

        def generator():
            for name in table:
                seen.append(name)
                yield name

        fake = FakePrefix()
        with patch.object(native, "_prefix_binding", return_value=fake):
            self.assertEqual(cmdbar.match_commands("zz", generator()), [])
        self.assertEqual(fake.calls, 1)
        self.assertEqual(seen, table)

    def test_fuzzy_generator_is_consumed_exactly_once_on_the_native_route(self):
        table = [f"Command{index:03d}" for index in range(200)]
        seen = []

        def generator():
            for name in table:
                seen.append(name)
                yield name

        fake = FakeFuzzy()
        with patch.object(native, "_fuzzy_binding_for_library", return_value=fake):
            self.assertEqual(cmdbar.fuzzy_matches("zz", generator()), ([], []))
        self.assertEqual(fake.calls, 1)
        self.assertEqual(seen, table)


if __name__ == "__main__":
    unittest.main(verbosity=2)
