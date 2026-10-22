"""Packed Rust command-prefix search parity, routing and performance."""
import importlib.util
import os
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "native", "pure_core", "target",
                   "x86_64-unknown-linux-gnu", "release",
                   "libnarwhal_pure_core.so")
if not os.path.isfile(LIB):
    raise SystemExit("pure-core library is not built")
os.environ["NARWHAL_PURE_CORE"] = LIB
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg

def load(name):
    spec = importlib.util.spec_from_file_location(
        "narwhal3d." + name, os.path.join(ROOT, "narwhal3d", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["narwhal3d." + name] = mod
    setattr(pkg, name, mod)
    spec.loader.exec_module(mod)
    return mod

native = load("native_command_search")
cmdbar = load("cmdbar")
names = tuple(["Line", "line", "LinearDimension", "Limit", "Cylinder"]
              + ["Command%04d" % i for i in range(1019)])
checks = []

def check(condition, label):
    checks.append((bool(condition), label))

native._reset_for_tests()
result = native.prefix_matches("Li", names, force=True)
check(result == ["Limit", "Line", "LinearDimension", "line"],
      "direct native prefix ordering matches the Python contract")
before = native._native_prefix_calls
routed = cmdbar.match_commands("Li", names)
check(native._native_prefix_calls == before + 1
      and routed == ["Limit", "Line", "LinearDimension", "line"],
      "high-cardinality command matching routes through Rust with exact ordering")
real = native.prefix_matches
native.prefix_matches = lambda *_args, **_kwargs: None
try:
    reference = cmdbar.match_commands("Li", names)
finally:
    native.prefix_matches = real
check(routed == reference, "native and Python prefix lists are identical")
check(native.prefix_matches("é", names, force=True) is None,
      "non-ASCII input retains the Python fallback")

fuzzy_names = tuple(["abacus", "zAB", "zABCDE", "cab", "zaebcd", "zaxxe"]
                    + ["Command%04d" % i for i in range(1018)])
exact, inexact = native.fuzzy_matches("ab", fuzzy_names, {"zABCDE": 5}, force=True)
check(exact == ["abacus"] and inexact[:3] == ["zABCDE", "cab", "zAB"],
      "direct native fuzzy ranking preserves prefix, usage, length and alphabetic order")
span_exact, span_inexact = native.fuzzy_matches("ae", fuzzy_names, force=True)
check(span_exact == [] and span_inexact[:2] == ["zaebcd", "zaxxe"],
      "native fuzzy closeness ranks the tighter span before shorter loose matches")
before = native._native_fuzzy_calls
rows = cmdbar.completion_lists("Li", names)
check(native._native_fuzzy_calls == before + 1 and rows.count(None) == 1
      and rows[:4] == ["Limit", "Line", "LinearDimension", "line"],
      "completion rows use one native fuzzy call and retain the group divider")


def best(fn):
    elapsed = float("inf")
    for _ in range(5):
        start = time.perf_counter()
        for _ in range(3000):
            fn()
        elapsed = min(elapsed, time.perf_counter() - start)
    return elapsed

native_time = best(lambda: cmdbar.match_commands("Command09", names))
native.prefix_matches = lambda *_args, **_kwargs: None
try:
    python_time = best(lambda: cmdbar.match_commands("Command09", names))
finally:
    native.prefix_matches = real
ratio = native_time / python_time
print("  [perf] prefix native=%.5fs python=%.5fs ratio=%.3f" %
      (native_time, python_time, ratio))
check(ratio <= 0.90, "native prefix route has the required 10% complete-call win")

real_fuzzy = native.fuzzy_matches
native_time = best(lambda: cmdbar.completion_lists("C09", names))
native.fuzzy_matches = lambda *_args, **_kwargs: None
native.prefix_matches = lambda *_args, **_kwargs: None
try:
    python_time = best(lambda: cmdbar.completion_lists("C09", names))
finally:
    native.fuzzy_matches = real_fuzzy
    native.prefix_matches = real
ratio = native_time / python_time
print("  [perf] fuzzy native=%.5fs python=%.5fs ratio=%.3f" %
      (native_time, python_time, ratio))
check(ratio <= 0.90, "native fuzzy route has the required 10% complete-call win")
for passed, label in checks:
    print("[%s] %s" % ("PASS" if passed else "FAIL", label))
failed = sum(not passed for passed, _ in checks)
print("\n%d/%d passed" % (len(checks) - failed, len(checks)))
raise SystemExit(1 if failed else 0)
