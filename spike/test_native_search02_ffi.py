#!/usr/bin/env python3
"""Compiled C ABI contract for both command-search exports (explicit library, never skipped).

Calls the library directly through ctypes. Fixture expectations come from the hand-derived
``spike/fixtures/native_search02.json``, not from Rust. Sentinel buffers are always valid
allocations; over-limit descriptors are refused before any access.

    python3 -B spike/test_native_search02_ffi.py --library PATH
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "spike", "fixtures", "native_search02.json")
ERR_NULL, ERR_INPUT, ERR_SIZE = -1, -2, -3
MAX_QUERY_BYTES, MAX_NAMES, MAX_CONCAT_BYTES = 4096, 65536, 16 * 1024 * 1024
SENTINEL = (1 << 64) - 1

LIB = None
ARGS = None


def _bind(library):
    lib = ctypes.CDLL(str(library))
    version = lib.narwhal_pure_core_abi_version
    version.argtypes, version.restype = [], ctypes.c_uint32
    if version() != 1:
        raise SystemExit(f"ABI mismatch: expected 1, got {version()}")
    u8p, i64p, u64p = ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(ctypes.c_int64), ctypes.POINTER(ctypes.c_uint64)
    prefix = lib.narwhal_pure_core_command_prefix
    prefix.argtypes = [u8p, ctypes.c_size_t, u8p, ctypes.c_size_t, i64p, ctypes.c_size_t,
                       u64p, ctypes.c_size_t, u64p]
    prefix.restype = ctypes.c_int32
    fuzzy = lib.narwhal_pure_core_command_fuzzy
    fuzzy.argtypes = [u8p, ctypes.c_size_t, u8p, ctypes.c_size_t, i64p, i64p, ctypes.c_size_t,
                      u64p, ctypes.c_size_t, u64p, u64p]
    fuzzy.restype = ctypes.c_int32
    return lib


def _bytes_buffer(data):
    return (ctypes.c_ubyte * len(data)).from_buffer_copy(data)


def _lengths(names):
    return (ctypes.c_int64 * len(names))(*(len(name) for name in names))


def _output(capacity):
    return (ctypes.c_uint64 * capacity)(*([SENTINEL] * capacity))


def call_prefix(query, names, capacity=None):
    concat = "".join(names).encode("ascii")
    out = _output(len(names) if capacity is None else capacity)
    count = ctypes.c_uint64(7)
    code = LIB.narwhal_pure_core_command_prefix(
        _bytes_buffer(query), len(query), _bytes_buffer(concat), len(concat), _lengths(names),
        len(names), out, len(out), ctypes.byref(count))
    return code, count.value, list(out)


def call_fuzzy(query, names, usage=None, capacity=None):
    concat = "".join(names).encode("ascii")
    values = [0] * len(names) if usage is None else usage
    out = _output(len(names) if capacity is None else capacity)
    exact, total = ctypes.c_uint64(7), ctypes.c_uint64(7)
    code = LIB.narwhal_pure_core_command_fuzzy(
        _bytes_buffer(query), len(query), _bytes_buffer(concat), len(concat), _lengths(names),
        (ctypes.c_int64 * len(values))(*values), len(names), out, len(out),
        ctypes.byref(exact), ctypes.byref(total))
    return code, exact.value, total.value, list(out)


def _names_from(indices, names):
    return [names[index] for index in indices]


with open(FIXTURES, encoding="utf-8") as handle:
    FIXTURE_DATA = json.load(handle)


class CompiledFixtureTests(unittest.TestCase):
    def test_abi_and_both_exports_are_present(self):
        self.assertIsNotNone(LIB.narwhal_pure_core_command_prefix)
        self.assertIsNotNone(LIB.narwhal_pure_core_command_fuzzy)

    def test_every_prefix_fixture_through_the_compiled_export(self):
        for case in FIXTURE_DATA["prefix"]:
            with self.subTest(case=case["id"]):
                code, used, out = call_prefix(case["query"].encode("ascii"), case["names"])
                self.assertEqual(code, 0)
                self.assertEqual(_names_from(out[:used], case["names"]), case["expected"])

    def test_every_fuzzy_fixture_through_the_compiled_export(self):
        for case in FIXTURE_DATA["fuzzy"]:
            with self.subTest(case=case["id"]):
                usage = None
                if case["usage"] is not None:
                    usage = [case["usage"].get(name, 0) for name in case["names"]]
                code, exact, total, out = call_fuzzy(case["query"].encode("ascii"), case["names"], usage)
                self.assertEqual(code, 0)
                names = case["names"]
                self.assertEqual(_names_from(out[:exact], names), case["expected"]["exact"], case["derivation"])
                self.assertEqual(_names_from(out[exact:total], names), case["expected"]["inexact"], case["derivation"])


class CompiledContractTests(unittest.TestCase):
    def test_admission_limits_at_and_one_past_each_bound(self):
        code, used, out = call_prefix(b"a" * MAX_QUERY_BYTES, ["b"])
        self.assertEqual((code, used), (0, 0))
        code, used, out = call_prefix(b"a" * (MAX_QUERY_BYTES + 1), ["b"])
        self.assertEqual((code, used, out), (ERR_SIZE, 0, [SENTINEL]))
        code, used, _ = call_prefix(b"a", [""] * MAX_NAMES)
        self.assertEqual((code, used), (0, 0))
        code, used, out = call_prefix(b"a", [""] * (MAX_NAMES + 1), capacity=1)
        self.assertEqual((code, used, out), (ERR_SIZE, 0, [SENTINEL]))
        code, used, _ = call_prefix(b"b", ["a" * MAX_CONCAT_BYTES])
        self.assertEqual((code, used), (0, 0))

    def test_concatenation_limit_is_checked_before_the_descriptor_is_read(self):
        concat = ctypes.create_string_buffer(MAX_CONCAT_BYTES + 1)
        lens = (ctypes.c_int64 * 1)(0)
        out = _output(1)
        count = ctypes.c_uint64(9)
        code = LIB.narwhal_pure_core_command_prefix(
            _bytes_buffer(b"b"), 1, ctypes.cast(concat, ctypes.POINTER(ctypes.c_ubyte)),
            MAX_CONCAT_BYTES + 1, lens, 1, out, 1, ctypes.byref(count))
        self.assertEqual((code, count.value, list(out)), (ERR_SIZE, 0, [SENTINEL]))

    def test_zero_query_is_a_no_op_even_for_an_oversize_table(self):
        names = [""] * (MAX_NAMES + 1)
        code, used, _ = call_prefix(b"", names, capacity=1)
        self.assertEqual((code, used), (0, 0))
        code, exact, total, _ = call_fuzzy(b"", names, capacity=1)
        self.assertEqual((code, exact, total), (0, 0, 0))

    def test_malformed_spans_are_refused_with_counts_cleared(self):
        cases = [
            ("negative length", "Line", [4, -1]),
            ("negative magnitude that fits", "Li", [-2]),
            ("length past the concatenation", "Li", [4]),
            ("lengths short of the concatenation", "Line", [2]),
        ]
        for label, concat_text, lens_values in cases:
            concat = _bytes_buffer(concat_text.encode("ascii"))
            lens = (ctypes.c_int64 * len(lens_values))(*lens_values)
            out = _output(4)
            count = ctypes.c_uint64(9)
            code = LIB.narwhal_pure_core_command_prefix(
                _bytes_buffer(b"L"), 1, concat, len(concat_text), lens, len(lens_values),
                out, 4, ctypes.byref(count))
            with self.subTest(case=label):
                self.assertEqual((code, count.value, list(out)), (ERR_INPUT, 0, [SENTINEL] * 4))

    def test_output_contract_exact_short_guard_and_null_counts(self):
        names = ["Line", "Line", "Limit"]
        code, used, out = call_prefix(b"Li", names)
        self.assertEqual((code, used, out), (0, 3, [2, 0, 1]))
        code, used, out = call_prefix(b"Li", names, capacity=2)
        self.assertEqual((code, used, out[:2]), (ERR_SIZE, 0, [SENTINEL, SENTINEL]))
        code, used, out = call_prefix(b"Li", names, capacity=5)
        self.assertEqual(out, [2, 0, 1, SENTINEL, SENTINEL])
        out = _output(3)
        code = LIB.narwhal_pure_core_command_prefix(
            _bytes_buffer(b"Li"), 2, _bytes_buffer("".join(names).encode()), 13,
            _lengths(names), 3, out, 3, None)
        self.assertEqual(code, ERR_NULL)
        self.assertEqual(list(out), [SENTINEL] * 3)

    def test_nullable_zero_output_is_accepted_when_nothing_matches(self):
        count = ctypes.c_uint64(9)
        code = LIB.narwhal_pure_core_command_prefix(
            _bytes_buffer(b"zz"), 2, _bytes_buffer(b"Line"), 4, _lengths(["Line"]), 1,
            None, 0, ctypes.byref(count))
        self.assertEqual((code, count.value), (0, 0))

    def test_signed_usage_extremes_and_ties_through_the_export(self):
        names = ["xb"] * 5
        code, exact, total, out = call_fuzzy(b"b", names, [-2**63, 0, 2**63 - 1, -2**63, 0])
        self.assertEqual((code, exact, total), (0, 0, 5))
        self.assertEqual(out[:5], [2, 1, 4, 0, 3])

    def test_fuzzy_short_capacity_clears_both_counts_and_writes_nothing(self):
        code, exact, total, out = call_fuzzy(b"ab", ["xab", "abc"], capacity=1)
        self.assertEqual((code, exact, total), (ERR_SIZE, 0, 0))
        self.assertEqual(out, [SENTINEL])


DIFFERENTIAL_SEED = 20260
DIFFERENTIAL_PREFIX_CASES = 3000
DIFFERENTIAL_FUZZY_CASES = 3000


def oracle_prefix(query, names):
    """Independent oracle written from the ordering rules, not from Rust or cmdbar."""
    low = query.lower()
    ranked = []
    for index, name in enumerate(names):
        if name[:len(query)].lower() != low:
            continue
        if name.lower() == low:
            rank = 0
        elif name.startswith(query):
            rank = 1
        else:
            rank = 2
        ranked.append((rank, name.lower(), index))
    return [index for _rank, _low, index in sorted(ranked)]


def oracle_fuzzy(query, names, usage):
    low = query.lower()
    exact = oracle_prefix(query, names)
    exact_set = set(exact)
    scored = []
    for index, name in enumerate(names):
        if index in exact_set:
            continue
        lowered = name.lower()
        position, first, last = 0, None, None
        for wanted in low:
            found = lowered.find(wanted, position)
            if found < 0:
                break
            first = found if first is None else first
            last = found
            position = found + 1
        else:
            if first is not None:
                scored.append((first, last - first + 1, -usage[index], len(name), lowered, index))
    return exact, [entry[-1] for entry in sorted(scored)]


def generated_names(rng, count):
    alphabet = "abcABC019"
    pool = []
    for _ in range(max(1, count // 3)):
        length = rng.choice((0, 1, 2, 3, 4, 6, 9, 12, 40, 200))
        pool.append("".join(rng.choice(alphabet) for _ in range(length)))
    return [rng.choice(pool) for _ in range(count)]


class CompiledDifferentialTests(unittest.TestCase):
    def test_seeded_prefix_corpus_matches_the_independent_oracle(self):
        import random
        rng = random.Random(DIFFERENTIAL_SEED)
        mismatches = []
        sizes = (0, 1, 2, 31, 32, 127, 128, 129, 256, 1024)
        for case in range(DIFFERENTIAL_PREFIX_CASES):
            names = generated_names(rng, sizes[case % len(sizes)])
            query = "".join(rng.choice("abcABC019") for _ in range(rng.choice((0, 1, 2, 3, 5))))
            if not query:
                continue
            code, used, out = call_prefix(query.encode("ascii"), names)
            expected = oracle_prefix(query, names)
            if code != 0 or out[:used] != expected:
                mismatches.append((case, query, names[:8], code, out[:used], expected))
        print(f"\n[differential] prefix seed={DIFFERENTIAL_SEED} cases={DIFFERENTIAL_PREFIX_CASES} mismatches={len(mismatches)}")
        self.assertEqual(mismatches[:3], [])

    def test_seeded_fuzzy_corpus_matches_the_independent_oracle(self):
        import random
        rng = random.Random(DIFFERENTIAL_SEED + 1)
        extremes = (-2 ** 63, -2 ** 63 + 1, -1, 0, 1, 2 ** 63 - 2, 2 ** 63 - 1)
        mismatches = []
        sizes = (0, 1, 2, 31, 32, 127, 128, 129, 256, 1024)
        for case in range(DIFFERENTIAL_FUZZY_CASES):
            names = generated_names(rng, sizes[case % len(sizes)])
            usage = [rng.choice(extremes) if rng.random() < 0.5 else rng.randint(-50, 50) for _ in names]
            query = "".join(rng.choice("abcABC019") for _ in range(rng.choice((0, 1, 2, 3, 5))))
            if not query:
                continue
            code, exact, total, out = call_fuzzy(query.encode("ascii"), names, usage)
            expected_exact, expected_inexact = oracle_fuzzy(query, names, usage)
            if code != 0 or out[:exact] != expected_exact or out[exact:total] != expected_inexact:
                mismatches.append((case, query, names[:8], code, exact, total, expected_exact, expected_inexact))
        print(f"\n[differential] fuzzy seed={DIFFERENTIAL_SEED + 1} cases={DIFFERENTIAL_FUZZY_CASES} mismatches={len(mismatches)}")
        self.assertEqual(mismatches[:3], [])


def main():
    global LIB, ARGS
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", default=os.environ.get("NARWHAL_PURE_CORE"))
    ARGS = parser.parse_args()
    if not ARGS.library:
        parser.error("--library or NARWHAL_PURE_CORE is required; native tests may not silently skip")
    library = Path(ARGS.library).resolve()
    if not library.is_file():
        parser.error(f"native library not found: {library}")
    LIB = _bind(library)
    suite = unittest.TestSuite([
        unittest.defaultTestLoader.loadTestsFromTestCase(CompiledFixtureTests),
        unittest.defaultTestLoader.loadTestsFromTestCase(CompiledContractTests),
        unittest.defaultTestLoader.loadTestsFromTestCase(CompiledDifferentialTests),
    ])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
