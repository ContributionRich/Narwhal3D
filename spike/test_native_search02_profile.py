#!/usr/bin/env python3
"""Measured workloads for command search (report script, not a pass/fail gate).

Times the real ``cmdbar`` functions on the native route and on the pure Python reference
(native route disabled by patch), records native call counts and packed bytes, and checks
every native result against the reference while measuring. Table generation is excluded
from timing; cold series use a fresh table per call, warm series reuse one table.

    python3 -B spike/test_native_search02_profile.py --library PATH --report OUT.json [--label NAME]
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import random
import statistics
import sys
import time
import types
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "narwhal_search02_profile_app"
SIZES = (32, 127, 128, 129, 1024, 10000)
WORDS = ("Line", "Circle", "Curve", "Box", "Cylinder", "Dimension", "Extrude", "Fillet",
         "Move", "Copy", "Rotate", "Scale", "Trim", "Join", "Point", "Polyline")


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


def make_table(seed, size):
    rng = random.Random(seed)
    names = []
    for index in range(size):
        word = WORDS[rng.randrange(len(WORDS))]
        suffix = "".join(rng.choice("abcdeflmnoprstuvxyz") for _ in range(rng.randrange(0, 6)))
        names.append(f"{word}{suffix}{index}")
    return names


def typed_prefixes(name, limit=6):
    return [name[:length] for length in range(1, min(limit, len(name)) + 1)]


def typed_fuzzy(name):
    letters = name[::2][:5]
    return [letters[:length] for length in range(2, len(letters) + 1)] or ["ab"]


def percentile(samples, fraction):
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))]


def time_series(calls):
    samples = []
    results = []
    for call in calls:
        start = time.perf_counter_ns()
        results.append(call())
        samples.append(time.perf_counter_ns() - start)
    summary = {"calls": len(samples), "p50_ns": percentile(samples, 0.50),
               "p95_ns": percentile(samples, 0.95), "mean_ns": round(statistics.fmean(samples))}
    return summary, results


def run(native, cmdbar, label, size, warm):
    base = make_table(size * 7919 + 17, size)
    queries = typed_prefixes(base[size // 2]) + ["zzqx"]
    fuzzy_queries = typed_fuzzy(base[size // 3])
    repeat = max(3, min(40, 20000 // size))
    sequence = queries * repeat
    if warm:
        tables = [base] * len(sequence)
    else:
        tables = [make_table(size * 7919 + 17 + index + 1, size) for index in range(len(sequence))]

    native._reset_for_tests()
    before = native._native_prefix_calls
    packed_bytes = []

    def prefix_call(names, query):
        def call():
            result = cmdbar.match_commands(query, names)
            packed = native._pack_cache["packed"]
            if packed is not None:
                packed_bytes.append(len(packed[1]) + 8 * len(packed[2]))
            return result
        return call

    native_summary, native_results = time_series(
        [prefix_call(names, query) for names, query in zip(tables, sequence)])
    native_calls = native._native_prefix_calls - before

    with patch.object(native, "prefix_matches", return_value=None):
        pure_summary, pure_results = time_series(
            [lambda n=names, q=query: cmdbar.match_commands(q, n)
             for names, query in zip(tables, sequence)])
    mismatches = sum(1 for got, want in zip(native_results, pure_results) if got != want)

    fuzzy_tables = [base] * (len(fuzzy_queries) * repeat)
    fuzzy_sequence = fuzzy_queries * repeat
    fuzzy_before = native._native_fuzzy_calls
    fuzzy_native, _ = time_series([lambda q=query, t=table: cmdbar.completion_lists(q, t)
                                   for table, query in zip(fuzzy_tables, fuzzy_sequence)])
    fuzzy_calls = native._native_fuzzy_calls - fuzzy_before
    with patch.object(native, "prefix_matches", return_value=None), \
            patch.object(native, "fuzzy_matches", return_value=None):
        fuzzy_pure, _ = time_series([lambda q=query, t=table: cmdbar.completion_lists(q, t)
                                     for table, query in zip(fuzzy_tables, fuzzy_sequence)])
    return {
        "label": label, "size": size, "cache": "warm" if warm else "cold", "repeat": repeat,
        "prefix_native": native_summary, "prefix_pure": pure_summary,
        "prefix_native_calls": native_calls, "prefix_calls": len(sequence),
        "prefix_mismatches": mismatches,
        "packed_bytes_mean": round(statistics.fmean(packed_bytes)) if packed_bytes else 0,
        "fuzzy_native": fuzzy_native, "fuzzy_pure": fuzzy_pure, "fuzzy_native_calls": fuzzy_calls,
        "prefix_p50_ratio": round(native_summary["p50_ns"] / pure_summary["p50_ns"], 3),
        "fuzzy_p50_ratio": round(fuzzy_native["p50_ns"] / fuzzy_pure["p50_ns"], 3),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--label", default="candidate")
    args = parser.parse_args()
    library = Path(args.library).resolve()
    if not library.is_file():
        parser.error(f"native library not found: {library}")
    native, cmdbar = _load(library)
    records = []
    for size in SIZES:
        for warm in (False, True):
            record = run(native, cmdbar, args.label, size, warm)
            records.append(record)
            print(f"{args.label} size={size:6d} {record['cache']:4s} n={record['prefix_calls']:4d} "
                  f"prefix p50 native={record['prefix_native']['p50_ns']:9d}ns "
                  f"pure={record['prefix_pure']['p50_ns']:9d}ns ratio={record['prefix_p50_ratio']} "
                  f"p95 native={record['prefix_native']['p95_ns']:9d}ns "
                  f"mismatches={record['prefix_mismatches']} fuzzy ratio={record['fuzzy_p50_ratio']} "
                  f"packed_bytes={record['packed_bytes_mean']}", flush=True)
    with open(args.report, "w", encoding="utf-8") as handle:
        json.dump({"library": str(library), "records": records}, handle, indent=2)
    print(f"wrote {args.report}")


if __name__ == "__main__":
    main()
