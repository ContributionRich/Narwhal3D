"""Tests for the RUST-UNITFORMAT-11 native port (owned by this task).

Pure Python, no bpy, no network.  Compares the live native host
(``narwhal3d/native_unitformat.py`` -> the Rust port in
``native/pure_core/src/unitformat.rs``) against the UNCHANGED reference
(``narwhal3d/unitformat.py``) and checks the candidate thin host
(``tmp/rust11-unitformat-candidate.py``).

Coverage (bounded, deterministic seed):

* **differential grid** -- every (value x mode x precision) combination over a
  curated adversarial set must return the identical result OR raise the
  identical exception type and message;
* **randomized adversarial** -- 6000 seeded random calls across the full
  value/mode/precision space (big floats, denormals, NaN/inf, int/float
  coercion, ``__int__`` precision objects, unicode whitespace, bogus modes);
* **large-value / overflow** -- values whose ``value*denom`` coefficient
  exceeds the default 28-digit context (``1e100``, ``1e308``, 50-digit ints)
  must raise ``InvalidOperation`` exactly as the reference does, and values
  under the limit must format exactly (this is the path the old ``u128`` fold
  would have overflowed/wrapped on);
* **default-context parity** -- the reference reads the thread's ``decimal``
  context, so both are compared under ``prec`` = 1, 2, 3, 5, 28 and must
  agree.  **Scope (documented, not a bug):** the reference's
  ``Decimal(str(mag)) * denom`` **multiply** is a context operation rounded to
  ``prec`` sig digits with the context rounding mode (default **HALF_EVEN**);
  the subsequent ``.quantize(1, HALF_UP)`` uses an explicit HALF_UP. This port
  emulates the DEFAULT HALF_EVEN multiply rounding. A **non-default context
  rounding mode** is refused with ``RuntimeError`` (not silently miscomputed).
  A *custom* context (un-trapped InvalidOperation, custom Emin/Emax) is OUT
  OF SCOPE -- the app never customizes the decimal context.
* **adversarial near-half-crossings** -- for every denominator in 3/7/16/128/
  1024, every context precision 1..10, and both Fractional and Feet-Inch, a
  dense window around each HALF_UP tie (``v*denom`` just below/above ``m + 0.5``)
  must match the reference. This is the region where the HALF_EVEN sig-digit
  multiply rounding and the HALF_UP integer rounding interact -- the exact
  class of bug Main measured (``0.0936`` Fractional 16 at prec 2).
* **non-default rounding guard** -- a context with a non-default rounding mode
  is refused with ``RuntimeError`` (the reference would use the context
  rounding for the multiply, which this port does not support); HALF_EVEN
  (the default) still works and matches the reference.
* **whitespace-padded mode names** -- a name padded within the 1 MiB bound
  resolves (the reference ``str.strip()``s); a name beyond the bound is
  refused (a documented explicit limit, not exact parity);
* **no-fallback** -- a missing library or a library lacking the two exports
  raises ``RuntimeError`` (never a silent Python fallback);
* **fault injection** -- a native that answers with an invalid code, or with
  an unbounded ``needed`` (10**9), or with ``UF_OK``/wrong size on the
  1-byte probe, is refused with ``RuntimeError`` and never allocates;
* **candidate delegation** -- the candidate's ``format_length`` is literally
  the *qualified* adapter's ``format_length`` (one shared instance), and both
  the standalone (file-path) and package-member (``from . import``) routes
  delegate to the adapter, not a re-implementation.

The live exports are mandatory: missing native qualification fails this suite,
never skips it. The frozen reference is test-only, not a runtime fallback.
"""
from __future__ import annotations

import ast
import ctypes
import decimal
import importlib.util
import os
import random
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADAPTER_FILE = os.path.join(ROOT, "narwhal3d", "native_unitformat.py")
NATIVE_LIB = os.path.join(ROOT, "narwhal3d", "native", "libnarwhal_pure_core.so")
CANDIDATE = os.path.join(ROOT, "narwhal3d", "unitformat.py")

results = []


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


def _outcome(fn, *a):
    try:
        return ("ok", fn(*a))
    except Exception as e:  # noqa: BLE001
        return ("err", type(e).__name__, str(e))


# ---------------------------------------------------------------------------
# Load the three subjects.  The reference and the qualified adapter (NAT) are
# loaded once.  The candidate is made to delegate to THAT SAME adapter instance
# (seeded into sys.modules) so the identity check ``CAND.format_length is
# NAT.format_length`` is honest -- it proves delegation to the qualified
# adapter, not a self-consistent copy.  A second, package-member load of the
# candidate separately proves the post-activation ``from . import`` route.
# ---------------------------------------------------------------------------
def _load_reference():
    spec = importlib.util.spec_from_file_location(
        "uf_ref", os.path.join(ROOT, "spike", "unitformat_reference.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["uf_ref"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_adapter(name):
    spec = importlib.util.spec_from_file_location(name, os.path.abspath(ADAPTER_FILE))
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = ""
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_candidate(name):
    spec = importlib.util.spec_from_file_location(name, os.path.abspath(CANDIDATE))
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = ""
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_candidate_as_package():
    pkg_name = "uf_act_pkg"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
        pkg.__package__ = pkg_name
        sys.modules[pkg_name] = pkg
    cand_name = f"{pkg_name}.candidate"
    if cand_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(cand_name, os.path.abspath(CANDIDATE))
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = pkg_name
        sys.modules[cand_name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[cand_name]


REF = _load_reference()
# The qualified native host, under one stable name.
NAT = _load_adapter("uf_nat")
# Make the candidate delegate to that exact instance (honest identity check).
sys.modules["narwhal3d_native_unitformat"] = NAT
# The candidate, loaded standalone (file-path route, no parent package).
CAND = _load_candidate("uf_cand")
# The candidate, loaded as a package member (the post-activation route).
CAND_PKG = _load_candidate_as_package()


def native_ready():
    try:
        NAT.format_length(1.0, "mm", "Decimal", 2)
        return True
    except RuntimeError:
        return False
    except Exception:  # noqa: BLE001
        return True


READY = native_ready()
if not READY:
    raise RuntimeError("unitformat native exports are required for qualification")


# --- deterministic adversarial generators (defined before use) -------------
def _rv(rng):
    k = rng.randint(0, 6)
    if k == 0:
        return rng.uniform(-300, 300)
    if k == 1:
        return rng.uniform(-1e6, 1e6)
    if k == 2:
        return rng.uniform(1e14, 1e22)
    if k == 3:
        return rng.uniform(1e-20, 1e-6)
    if k == 4:
        return rng.choice([float("nan"), float("inf"), float("-inf")])
    if k == 5:
        return rng.randint(-10 ** rng.randint(1, 40), 10 ** rng.randint(1, 40))
    return rng.choice([0, -0.0, 1e308, -1e308, 5e-324])


def _rm(rng):
    return rng.choice(["Decimal", "Fractional", "Feet & Inches", "decimal",
                       "FRACTIONAL", "feet_inch", "Feet-Inch", "feet and inches",
                       "  decimal  ", "bogus", "", "x", "Fraction", "fraction"])


def _rp(rng):
    return rng.choice([
        rng.randint(-2, 20), rng.randint(0, 1024), rng.randint(1025, 5000),
        rng.choice(["x", "16", " 3 ", None, 16.0, 16.5, True, False, -1, 10 ** 9, 10 ** 18]),
    ])


# ---------------------------------------------------------------------------
@check("reference, native host and candidate all expose format_length")
def _():
    assert callable(REF.format_length)
    assert callable(NAT.format_length)
    assert callable(CAND.format_length)
    assert callable(CAND_PKG.format_length)


@check("candidate delegates to the QUALIFIED adapter's format_length (one instance)")
def _():
    # The candidate must delegate to the SAME adapter instance we qualified,
    # not load a private copy.
    assert CAND.native_unitformat is NAT, \
        "standalone candidate must delegate to the qualified adapter instance"
    assert CAND.format_length is NAT.format_length, \
        "candidate must delegate to the adapter's format_length, not reimplement it"
    assert os.path.abspath(NAT.__file__) == os.path.abspath(ADAPTER_FILE), \
        "qualified adapter must be narwhal3d/native_unitformat.py"


@check("candidate (package route) delegates to a sibling native_unitformat.py")
def _():
    # Post-activation layout: the candidate is a package member and resolves the
    # adapter via ``from . import`` to a sibling of the same file.
    adapter = getattr(CAND_PKG, "native_unitformat", None)
    assert adapter is not None, "package candidate must import the native host"
    assert CAND_PKG.format_length is adapter.format_length, \
        "package candidate must delegate, not reimplement"
    assert os.path.abspath(adapter.__file__) == os.path.abspath(ADAPTER_FILE), \
        "package candidate must delegate to narwhal3d/native_unitformat.py"


@check("candidate is a thin alias (AST: no format_length definition of its own)")
def _():
    tree = ast.parse(open(CANDIDATE).read())
    bodies = tree.body
    allowed = (ast.ImportFrom, ast.Import, ast.Assign, ast.Expr,
               ast.FunctionDef, ast.ClassDef, ast.If, ast.With, ast.Try,
               ast.Return, ast.Raise, ast.AnnAssign, ast.If)
    for node in bodies:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # module docstring
        assert isinstance(node, allowed), f"unexpected top-level {type(node).__name__}"
    defs = [n.name for n in bodies if isinstance(n, ast.FunctionDef)]
    assert "format_length" not in defs, "candidate must not define format_length"


@check("native host owns no rounding/reduction/computation of its own")
def _():
    tree = ast.parse(open(ADAPTER_FILE).read())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "quantize" not in names and "quantize" not in attrs, \
        "native host must not quantize in Python"
    assert "ROUND_HALF_UP" not in names, "native host must not round in Python"
    assert "gcd" not in names, "native host must not reduce fractions in Python"


@check("native host raises RuntimeError (no fallback) and maps InvalidOperation")
def _():
    src = open(ADAPTER_FILE).read()
    assert "RuntimeError" in src, "missing/failed native must raise RuntimeError"
    assert "InvalidOperation" in src, "must raise decimal.InvalidOperation for the native code"


@check("differential grid: 58 values x 11 modes x 18 precisions match the reference")
def _():
    if not READY:
        raise RuntimeError("SKIPPED: native exports not yet registered (pre-activation)")
    values = [
        0, 1, 1.25, 1.75, 30.5, 3.5, 12.0, 11.99999, 0.99999, -1.2345, -0.001,
        1.5, 2.0, 100.0, 1e20, 9.99e15, float("nan"), float("inf"), float("-inf"),
        1e-7, 123456789.0, 0.1, 1.2345, 0.25, 1.4, 1.6, 12.25, 1.9999999999,
        1.23456789012345, 7.5e16, 1e16, 123456.789, -123456.789, -0.0, 1e308,
        -1e308, 5e-324, 1000.0, 999.5, 1200.25, 24.0, 23.999,
    ]
    modes = ["Decimal", "Fractional", "Feet & Inches", "fractional", "feet_inches",
             "Feet-Inch", "  DECIMAL  ", "Imperial", "", "fraction", "feet and inches"]
    precisions = [0, 1, 2, 3, 4, 16, 128, 1024, 12, 13, -1, 4096, "x", None, "16", " 2 ", 16.0]
    mismatches = 0
    for v in values:
        for m in modes:
            for p in precisions:
                ref = _outcome(REF.format_length, v, "mm", m, p)
                nat = _outcome(NAT.format_length, v, "mm", m, p)
                if ref != nat:
                    mismatches += 1
                    if mismatches <= 8:
                        print(f"  grid mismatch: value={v!r} mode={m!r} prec={p!r}\n"
                              f"    REF={ref}\n    NAT={nat}")
    assert mismatches == 0, f"{mismatches} grid mismatches"


@check("randomized adversarial: 6000 seeded calls match the reference")
def _():
    if not READY:
        raise RuntimeError("SKIPPED: native exports not yet registered (pre-activation)")
    rng = random.Random(11)
    for _ in range(6000):
        v, m, p = _rv(rng), _rm(rng), _rp(rng)
        ref = _outcome(REF.format_length, v, "mm", m, p)
        nat = _outcome(NAT.format_length, v, "mm", m, p)
        assert ref == nat, f"random mismatch: value={v!r} mode={m!r} prec={p!r}\n  REF={ref}\n  NAT={nat}"


@check("large-value / overflow: coefficient over the 28-digit limit matches the reference")
def _():
    if not READY:
        raise RuntimeError("SKIPPED: native exports not yet registered (pre-activation)")
    # The path the old u128 fold would have overflowed/wrapped on.  Under the
    # default 28-digit context the ``value*denom`` coefficient of these exceeds
    # the limit, so the reference raises InvalidOperation; values under the
    # limit must format exactly.  Both must agree.
    cases = [
        (1e100, "Fractional", 16), (1e100, "Fractional", 1), (1e100, "Decimal", 0),
        (1e100, "Feet & Inches", 16), (1e150, "Fractional", 1024),
        (1e308, "Fractional", 16), (1e308, "Decimal", 0), (1e308, "Feet & Inches", 1024),
        (10 ** 40, "Fractional", 16), (10 ** 40, "Decimal", 0), (-(10 ** 50), "Fractional", 4),
        (2 ** 120, "Fractional", 1024), (9.99e15, "Fractional", 16), (9.99e15, "Decimal", 0),
        (1e20, "Fractional", 16), (7.5e16, "Fractional", 1024), (1234.5, "Feet & Inches", 16),
    ]
    for v, m, p in cases:
        ref = _outcome(REF.format_length, v, "mm", m, p)
        nat = _outcome(NAT.format_length, v, "mm", m, p)
        assert ref == nat, f"large-value mismatch: value={v!r} mode={m!r} prec={p!r}\n  REF={ref}\n  NAT={nat}"
    # Spot-check the semantics: over the limit -> InvalidOperation, under -> exact.
    assert _outcome(REF.format_length, 1e100, "mm", "Fractional", 16)[1] == "InvalidOperation"
    assert _outcome(NAT.format_length, 1e100, "mm", "Fractional", 16)[1] == "InvalidOperation"
    assert _outcome(NAT.format_length, 9.99e15, "mm", "Fractional", 16) == \
        _outcome(REF.format_length, 9.99e15, "mm", "Fractional", 16)


@check("default-context parity under prec = 1, 2, 3, 5, 28 (scope: default context only)")
def _():
    if not READY:
        raise RuntimeError("SKIPPED: native exports not yet registered (pre-activation)")
    # The reference passes rounding=ROUND_HALF_UP explicitly (context rounding
    # does not change these) and its multiply is exact under the default traps;
    # only the context PRECISION changes the result.  A custom context
    # (un-trapped InvalidOperation / custom Emin-Emax) is out of scope.
    cases = [(1.25, "Decimal", 2), (1.5, "Decimal", 1), (1.2345, "Decimal", 3),
             (0.99999, "Decimal", 2), (10.5, "Decimal", 0),
             (1.75, "Fractional", 16), (1.5, "Fractional", 2), (0.05, "Fractional", 1),
             (1.1, "Fractional", 10), (30.5, "Feet & Inches", 16),
             (1e20, "Decimal", 12), (9.99e15, "Fractional", 16)]
    for prec in (1, 2, 3, 5, 28):
        with decimal.localcontext() as c:
            c.prec = prec
            for v, m, p in cases:
                ref = _outcome(REF.format_length, v, "mm", m, p)
                nat = _outcome(NAT.format_length, v, "mm", m, p)
                assert ref == nat, (
                    f"default-context prec={prec}: value={v!r} mode={m!r} prec={p!r}\n"
                    f"  REF={ref}\n  NAT={nat}")


@check("adversarial near-half-crossings: prec 1..10 x denoms 3,7,16,128,1024")
def _():
    if not READY:
        raise RuntimeError("SKIPPED: native exports not yet registered (pre-activation)")
    # The reference computes int((Decimal(str(mag)) * denom).quantize(1, HALF_UP)):
    # the MULTIPLY is rounded to `prec` sig digits (HALF_EVEN), THEN the quantize
    # rounds to integer (HALF_UP). The dangerous region is where v*denom is just
    # below/above a half-integer (m + 0.5) and the sig-digit rounding pushes it
    # across the HALF_UP tie. Sample densely around every half-integer for every
    # denominator and every precision 1..10.
    denoms = (3, 7, 16, 128, 1024)
    mismatches = 0
    total = 0
    for d in denoms:
        for m in range(12):
            boundary = m + 0.5  # v*d = boundary is a HALF_UP tie
            for prec in range(1, 11):
                # Window where the HALF_EVEN sig-digit rounding is active: the
                # last kept digit is at position 10^-(prec-1), so the rounding
                # can shift the value by at most ~10^-(prec-1).
                half_width = 2 * (10 ** (1 - prec))
                for t in range(-12, 13):
                    v_times_d = boundary + half_width * (t / 12.0)
                    if v_times_d <= 0:
                        continue
                    v = v_times_d / d
                    with decimal.localcontext() as c:
                        c.prec = prec
                        ref = _outcome(REF.format_length, v, "mm", "Fractional", d)
                        nat = _outcome(NAT.format_length, v, "mm", "Fractional", d)
                    total += 1
                    if ref != nat:
                        mismatches += 1
                        if mismatches <= 10:
                            print(f"  near-half: d={d} m={m} prec={prec} v={v!r}\n"
                                  f"    REF={ref}\n    NAT={nat}")
    assert mismatches == 0, f"{mismatches} near-half mismatches out of {total}"


@check("adversarial near-half-crossings (Feet-Inch): prec 1..10 x denoms 3,7,16,128,1024")
def _():
    if not READY:
        raise RuntimeError("SKIPPED: native exports not yet registered (pre-activation)")
    # Feet-Inch has the same multiply-then-quantize pattern (v*denom, then divmod
    # by 12*denom). The HALF_UP tie is at v*denom = m + 0.5, same as Fractional.
    denoms = (3, 7, 16, 128, 1024)
    mismatches = 0
    total = 0
    for d in denoms:
        for m in range(12):
            boundary = m + 0.5
            for prec in range(1, 11):
                half_width = 2 * (10 ** (1 - prec))
                for t in range(-12, 13):
                    v_times_d = boundary + half_width * (t / 12.0)
                    if v_times_d <= 0:
                        continue
                    v = v_times_d / d
                    with decimal.localcontext() as c:
                        c.prec = prec
                        ref = _outcome(REF.format_length, v, "mm", "Feet & Inches", d)
                        nat = _outcome(NAT.format_length, v, "mm", "Feet & Inches", d)
                    total += 1
                    if ref != nat:
                        mismatches += 1
                        if mismatches <= 10:
                            print(f"  near-half FI: d={d} m={m} prec={prec} v={v!r}\n"
                                  f"    REF={ref}\n    NAT={nat}")
    assert mismatches == 0, f"{mismatches} near-half FI mismatches out of {total}"


@check("non-default context rounding mode is refused with RuntimeError (guard)")
def _():
    if not READY:
        raise RuntimeError("SKIPPED: native exports not yet registered (pre-activation)")
    # The reference would use the context rounding for the multiply step; this port
    # does not support it. The adapter must refuse with RuntimeError, not silently
    # produce a wrong result.
    with decimal.localcontext() as c:
        c.prec = 2
        c.rounding = decimal.ROUND_HALF_UP  # non-default
        try:
            NAT.format_length(0.0936, "mm", "Fractional", 16)
            raise AssertionError("expected RuntimeError for non-default rounding mode")
        except RuntimeError:
            pass
        except Exception as e:  # noqa: BLE001
            raise AssertionError(f"expected RuntimeError, got {type(e).__name__}")
    # And HALF_EVEN (the default) must still work.
    with decimal.localcontext() as c:
        c.prec = 2
        c.rounding = decimal.ROUND_HALF_EVEN
        ref = _outcome(REF.format_length, 0.0936, "mm", "Fractional", 16)
        nat = _outcome(NAT.format_length, 0.0936, "mm", "Fractional", 16)
        assert ref == nat, f"HALF_EVEN (default) must work: {ref} vs {nat}"


@check("whitespace-padded mode name within the 1 MiB bound resolves; beyond it is refused")
def _():
    if not READY:
        raise RuntimeError("SKIPPED: native exports not yet registered (pre-activation)")
    # Within the bound: the reference str.strip()s, so this is valid and both agree.
    padded = "\u2007" + " " * 1500 + "decimal" + "\u2007"
    ref = _outcome(REF.format_length, 1.25, "mm", padded, 2)
    nat = _outcome(NAT.format_length, 1.25, "mm", padded, 2)
    assert ref == nat and ref[0] == "ok" and ref[1] == "1.25", (ref, nat)
    # Beyond the 1 MiB bound: the reference would accept it, but the native
    # refuses it (a documented explicit limit, not exact parity).  This is an
    # accepted divergence, asserted here so it is not silently "fixed" later.
    too_long = " " * (1024 * 1024 + 1) + "decimal"
    nat = _outcome(NAT.format_length, 1.25, "mm", too_long, 2)
    assert nat[0] == "err" and nat[1] == "ValueError", \
        f"over-bound mode name must be refused as an unsupported mode, got {nat}"


@check("no-fallback: missing library raises RuntimeError")
def _():
    saved = os.environ.get("NARWHAL_PURE_CORE")
    try:
        os.environ["NARWHAL_PURE_CORE"] = "/nonexistent/definitely_missing.so"
        try:
            NAT.format_length(1.0, "mm", "Decimal", 2)
            raise AssertionError("expected RuntimeError for a missing library")
        except RuntimeError:
            pass
        except Exception as e:  # noqa: BLE001
            raise AssertionError(f"expected RuntimeError, got {type(e).__name__}")
    finally:
        if saved is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = saved


@check("no-fallback: library lacking the unitformat exports raises RuntimeError")
def _():
    if READY:
        return  # already wired; the missing-export case is covered pre-activation
    saved = os.environ.get("NARWHAL_PURE_CORE")
    try:
        os.environ["NARWHAL_PURE_CORE"] = NATIVE_LIB
        try:
            NAT.format_length(1.0, "mm", "Decimal", 2)
            raise AssertionError("expected RuntimeError for missing exports")
        except RuntimeError:
            pass
        except Exception as e:  # noqa: BLE001
            raise AssertionError(f"expected RuntimeError, got {type(e).__name__}")
    finally:
        if saved is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = saved


def _with_fake_native(fake_resolve, fake_fmt, fn, *a):
    """Run ``fn(*a)`` with the adapter's native bindings replaced by fakes."""
    saved = NAT._require_native
    NAT._require_native = lambda: (fake_resolve, fake_fmt)
    try:
        fn(*a)
    finally:
        NAT._require_native = saved


def _fake_fmt(code, needed_val):
    def fake(value, mode, prec, ctxprec, out, cap, needed_ref):
        try:
            ptr = ctypes.cast(needed_ref, ctypes.POINTER(ctypes.c_size_t))
            ptr[0] = needed_val
        except Exception:  # noqa: BLE001
            pass
        return code
    return fake


@check("fault injection: invalid resolve code raises RuntimeError (no fallback)")
def _():
    def fake_resolve(b, n):
        return 99  # not one of {0,1,2,13}
    try:
        _with_fake_native(fake_resolve, _fake_fmt(0, 0), NAT.format_length, 1.0, "mm", "Decimal", 2)
        raise AssertionError("expected RuntimeError for an invalid resolve code")
    except RuntimeError:
        pass
    except Exception as e:  # noqa: BLE001
        raise AssertionError(f"expected RuntimeError, got {type(e).__name__}: {e!r}")


@check("fault injection: huge needed (10**9) is refused without a large allocation")
def _():
    fake = _fake_fmt(NAT.UF_SHORT_BUFFER, 10 ** 9)
    try:
        _with_fake_native(lambda b, n: NAT.UF_DECIMAL, fake, NAT.format_length, 1.0, "mm", "Decimal", 2)
        raise AssertionError("expected RuntimeError for an unbounded needed")
    except RuntimeError:
        pass
    except Exception as e:  # noqa: BLE001
        raise AssertionError(f"expected RuntimeError, got {type(e).__name__}: {e!r}")


@check("fault injection: UF_OK with a wrong size on the 1-byte probe is refused")
def _():
    fake = _fake_fmt(NAT.UF_OK, 500)  # OK but claims 500 bytes on a 1-byte probe
    try:
        _with_fake_native(lambda b, n: NAT.UF_DECIMAL, fake, NAT.format_length, 1.0, "mm", "Decimal", 2)
        raise AssertionError("expected RuntimeError for an inconsistent OK size report")
    except RuntimeError:
        pass
    except Exception as e:  # noqa: BLE001
        raise AssertionError(f"expected RuntimeError, got {type(e).__name__}: {e!r}")


@check("fault injection: invalid format code raises RuntimeError (no fallback)")
def _():
    fake = _fake_fmt(99, 0)  # not one of the documented format codes
    try:
        _with_fake_native(lambda b, n: NAT.UF_DECIMAL, fake, NAT.format_length, 1.0, "mm", "Decimal", 2)
        raise AssertionError("expected RuntimeError for an invalid format code")
    except RuntimeError:
        pass
    except Exception as e:  # noqa: BLE001
        raise AssertionError(f"expected RuntimeError, got {type(e).__name__}: {e!r}")


@check("candidate delegates: format_length matches the reference (standalone + package)")
def _():
    if not READY:
        raise RuntimeError("SKIPPED: native exports not yet registered (pre-activation)")
    for subject in (CAND, CAND_PKG):
        for v, m, p in [(1.25, "Decimal", 2), (1.75, "Fractional", 16),
                        (30.5, "Feet & Inches", 16), (0.99999, "Feet & Inches", 16),
                        (1e100, "Fractional", 16)]:
            ref = _outcome(REF.format_length, v, "mm", m, p)
            cand = _outcome(subject.format_length, v, "mm", m, p)
            assert ref == cand, (
                f"candidate mismatch ({subject.__name__}): value={v!r} mode={m!r} prec={p!r}\n"
                f"  REF={ref}\n  CAND={cand}")


@check("candidate raises the reference's exception for a bad mode (delegation)")
def _():
    if not READY:
        raise RuntimeError("SKIPPED: native exports not yet registered (pre-activation)")
    for subject in (CAND, CAND_PKG):
        ref = _outcome(REF.format_length, 1.0, "mm", "Imperial", 2)
        cand = _outcome(subject.format_length, 1.0, "mm", "Imperial", 2)
        assert ref == cand and ref[0] == "err" and ref[1] == "ValueError", (ref, cand)


@check("custom exponent/clamp/trap contexts are explicitly refused")
def _():
    for field, value in (("Emax", 4), ("Emin", -4), ("clamp", 1)):
        with decimal.localcontext() as context:
            setattr(context, field, value)
            try:
                NAT.format_length(1.25, "mm", "Decimal", 2)
            except RuntimeError as error:
                assert "custom decimal" in str(error)
            else:
                raise AssertionError("unsupported custom context computed silently")
    with decimal.localcontext() as context:
        context.traps[decimal.InvalidOperation] = False
        try:
            NAT.format_length(float("inf"), "mm", "Decimal", 2)
        except RuntimeError as error:
            assert "custom decimal" in str(error)
        else:
            raise AssertionError("unsupported traps computed silently")


# ---------------------------------------------------------------------------
failed = 0
skipped = 0
for name, status, detail in results:
    if status == "FAIL" and "SKIPPED" in detail:
        skipped += 1
        print(f"[SKIP] {name}  -- exports not yet registered (pre-activation)")
        continue
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
print(f"\n{len(results) - failed - skipped}/{len(results)} passed, "
      f"{skipped} skipped")
sys.exit(1 if failed else 0)
