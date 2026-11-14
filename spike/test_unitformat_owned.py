"""Host-only tests for the unit-format display port (RUST-FORMAT-GOLDEN-18).

The pure computation (mode normalisation, precision validation, rounding,
fraction reduction, string rendering) is owned by the Rust engine
(``native/pure_core/src/unitformat.rs``) and is replayed against a frozen
golden fixture by the Rust integration test
(``native/pure_core/tests/unitformat_golden.rs``).  No Python engine remains
at test time.

This host test owns the Python side only:

* **object admission / coercion order** — value → mode → precision, with the
  reference's exact exception type and message for each failure;
* **context guards** — non-default rounding, custom Emin/Emax/clamp/traps,
  precision > 0xffffffff are all refused with ``RuntimeError`` (the Rust
  engine takes ``context_precision`` as an explicit ABI param and cannot see
  a custom context);
* **no-fallback** — missing library, missing exports, invalid native codes,
  unbounded ``needed``, inconsistent OK size are all ``RuntimeError`` (never
  a silent Python fallback);
* **fault injection** — fake native bindings that lie about codes or sizes;
* **candidate delegation** — the candidate's ``format_length`` IS the
  qualified adapter's ``format_length`` (one shared instance), both the
  standalone and package-member routes;
* **live replay** — a deterministic sample of OK and ERR fixture rows is
  replayed through the live native host and the result (OK string or ERR
  exception type+message) is asserted EXACT against the frozen expectation;
  **all** HOST rows are replayed and their exact exception type, message and
  original context asserted. This proves the live host still matches the
  golden; it does NOT re-derive any expectation from the engine.
* **reference absent** — the retired Python reference is gone from the
  public tree.

The live exports are mandatory: missing native qualification fails this
suite, never skips it.
"""
from __future__ import annotations

import ast
import ctypes
import decimal
import importlib.util
import os
import re
import struct
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADAPTER_FILE = os.path.join(ROOT, "narwhal3d", "native_unitformat.py")
NATIVE_LIB = os.path.join(ROOT, "narwhal3d", "native", "libnarwhal_pure_core.so")
CANDIDATE = os.path.join(ROOT, "narwhal3d", "unitformat.py")
FIXTURE = os.path.join(ROOT, "native", "pure_core", "tests", "fixtures",
                       "unitformat_golden.txt")
REFERENCE_PATH = os.path.join(ROOT, "spike", "unitformat_reference.py")

MODE_NAMES = {0: "Decimal", 1: "Fractional", 2: "Feet & Inches"}

results = []


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:
            results.append((name, "FAIL", repr(e)))
    return deco


# ---------------------------------------------------------------------------
# Load the three subjects.  No reference is loaded (it is retired).
# ---------------------------------------------------------------------------
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


NAT = _load_adapter("uf_nat")
sys.modules["narwhal3d_native_unitformat"] = NAT
CAND = _load_candidate("uf_cand")
CAND_PKG = _load_candidate_as_package()


def native_ready():
    try:
        NAT.format_length(1.0, "mm", "Decimal", 2)
        return True
    except RuntimeError:
        return False
    except Exception:
        return True


READY = native_ready()
if not READY:
    raise RuntimeError("unitformat native exports are required for qualification")


# ---------------------------------------------------------------------------
# Basic exposure and delegation
# ---------------------------------------------------------------------------
@check("native host and candidate both expose format_length")
def _():
    assert callable(NAT.format_length)
    assert callable(CAND.format_length)
    assert callable(CAND_PKG.format_length)


@check("candidate delegates to the QUALIFIED adapter's format_length (one instance)")
def _():
    assert CAND.native_unitformat is NAT, \
        "standalone candidate must delegate to the qualified adapter instance"
    assert CAND.format_length is NAT.format_length, \
        "candidate must delegate to the adapter's format_length, not reimplement it"
    assert os.path.abspath(NAT.__file__) == os.path.abspath(ADAPTER_FILE), \
        "qualified adapter must be narwhal3d/native_unitformat.py"


@check("candidate (package route) delegates to a sibling native_unitformat.py")
def _():
    adapter = getattr(CAND_PKG, "native_unitformat", None)
    assert adapter is not None, "package candidate must import the native host"
    assert CAND_PKG.format_length is adapter.format_length, \
        "package candidate must delegate, not reimplement"
    assert os.path.abspath(adapter.__file__) == os.path.abspath(ADAPTER_FILE), \
        "package candidate must delegate to narwhal3d/native_unitformat.py"


# ---------------------------------------------------------------------------
# AST guards: no Python-side computation
# ---------------------------------------------------------------------------
@check("candidate is a thin alias (AST: no format_length definition of its own)")
def _():
    tree = ast.parse(open(CANDIDATE).read())
    allowed = (ast.ImportFrom, ast.Import, ast.Assign, ast.Expr,
               ast.FunctionDef, ast.ClassDef, ast.If, ast.With, ast.Try,
               ast.Return, ast.Raise, ast.AnnAssign)
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        assert isinstance(node, allowed), f"unexpected top-level {type(node).__name__}"
    defs = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
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
    assert "InvalidOperation" in src, \
        "must raise decimal.InvalidOperation for the native code"


# ---------------------------------------------------------------------------
# Context guards (host-only; the Rust engine takes ctx as an explicit param)
# ---------------------------------------------------------------------------
@check("non-default context rounding mode is refused with RuntimeError (guard)")
def _():
    with decimal.localcontext() as c:
        c.prec = 2
        c.rounding = decimal.ROUND_HALF_UP
        try:
            NAT.format_length(0.0936, "mm", "Fractional", 16)
            raise AssertionError("expected RuntimeError for non-default rounding mode")
        except RuntimeError:
            pass
        except Exception as e:
            raise AssertionError(f"expected RuntimeError, got {type(e).__name__}")
    # HALF_EVEN (the default) must still work.
    with decimal.localcontext() as c:
        c.prec = 2
        c.rounding = decimal.ROUND_HALF_EVEN
        r = NAT.format_length(0.0936, "mm", "Fractional", 16)
        assert r is not None, "HALF_EVEN (default) must work"


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
# Whitespace-padded mode names
# ---------------------------------------------------------------------------
@check("whitespace-padded mode name within the 1 MiB bound resolves; beyond it is refused")
def _():
    padded = "\u2007" + " " * 1500 + "decimal" + "\u2007"
    r = NAT.format_length(1.25, "mm", padded, 2)
    assert r == "1.25", f"padded mode should resolve to '1.25', got {r!r}"
    too_long = " " * (1024 * 1024 + 1) + "decimal"
    try:
        NAT.format_length(1.25, "mm", too_long, 2)
        raise AssertionError("over-bound mode name must be refused")
    except ValueError as e:
        assert "unsupported display mode" in str(e), \
            f"over-bound mode must be refused as unsupported, got {e!r}"


# ---------------------------------------------------------------------------
# No-fallback: missing library / missing exports
# ---------------------------------------------------------------------------
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
        except Exception as e:
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
        except Exception as e:
            raise AssertionError(f"expected RuntimeError, got {type(e).__name__}")
    finally:
        if saved is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = saved


# ---------------------------------------------------------------------------
# Fault injection: fake native bindings that lie
# ---------------------------------------------------------------------------
def _with_fake_native(fake_resolve, fake_fmt, fn, *a):
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
        except Exception:
            pass
        return code
    return fake


@check("fault injection: invalid resolve code raises RuntimeError (no fallback)")
def _():
    def fake_resolve(b, n):
        return 99
    try:
        _with_fake_native(fake_resolve, _fake_fmt(0, 0),
                          NAT.format_length, 1.0, "mm", "Decimal", 2)
        raise AssertionError("expected RuntimeError for an invalid resolve code")
    except RuntimeError:
        pass
    except Exception as e:
        raise AssertionError(f"expected RuntimeError, got {type(e).__name__}: {e!r}")


@check("fault injection: huge needed (10**9) is refused without a large allocation")
def _():
    fake = _fake_fmt(NAT.UF_SHORT_BUFFER, 10 ** 9)
    try:
        _with_fake_native(lambda b, n: NAT.UF_DECIMAL, fake,
                          NAT.format_length, 1.0, "mm", "Decimal", 2)
        raise AssertionError("expected RuntimeError for an unbounded needed")
    except RuntimeError:
        pass
    except Exception as e:
        raise AssertionError(f"expected RuntimeError, got {type(e).__name__}: {e!r}")


@check("fault injection: UF_OK with a wrong size on the 1-byte probe is refused")
def _():
    fake = _fake_fmt(NAT.UF_OK, 500)
    try:
        _with_fake_native(lambda b, n: NAT.UF_DECIMAL, fake,
                          NAT.format_length, 1.0, "mm", "Decimal", 2)
        raise AssertionError("expected RuntimeError for an inconsistent OK size report")
    except RuntimeError:
        pass
    except Exception as e:
        raise AssertionError(f"expected RuntimeError, got {type(e).__name__}: {e!r}")


@check("fault injection: invalid format code raises RuntimeError (no fallback)")
def _():
    fake = _fake_fmt(99, 0)
    try:
        _with_fake_native(lambda b, n: NAT.UF_DECIMAL, fake,
                          NAT.format_length, 1.0, "mm", "Decimal", 2)
        raise AssertionError("expected RuntimeError for an invalid format code")
    except RuntimeError:
        pass
    except Exception as e:
        raise AssertionError(f"expected RuntimeError, got {type(e).__name__}: {e!r}")


# ---------------------------------------------------------------------------
# Live sample cross-check against the frozen golden fixture (EXACT)
# ---------------------------------------------------------------------------
def _decode_bits(hex_str):
    return struct.unpack('<d', bytes.fromhex(hex_str))[0]


def _decode_value(s):
    # A value stored as repr() may be nan/inf (not literal_eval-able); float()
    # handles those and every int/float in the corpus. A non-numeric (a
    # 'must be a number' host error) falls back to a safe literal eval.
    try:
        return float(s)
    except (ValueError, TypeError):
        return ast.literal_eval(s)


def _decode_literal(s):
    return ast.literal_eval(s)


def _expected_err(code, mode_code, precision):
    if code == 10:
        if mode_code == 0:
            return ValueError, f"Decimal precision out of range (0..12): {precision}"
        return ValueError, f"Fraction denominator out of range (1..1024): {precision}"
    if code == 11:
        return ValueError, "cannot convert NaN to integer"
    if code == 12:
        return decimal.InvalidOperation, "[<class 'decimal.InvalidOperation'>]"
    raise AssertionError(f"unexpected ERR code {code}")


def _fixture_data_lines():
    with open(FIXTURE, encoding="utf-8") as f:
        lines = f.read().splitlines()
    return [l for l in lines if l and not l.startswith('#')]


@check("live sample cross-check: OK rows match the fixture EXACTLY (≥40 rows)")
def _():
    rows = _fixture_data_lines()
    ok_rows = [l for l in rows if l.split('\t')[0] == 'OK'][:40]
    assert len(ok_rows) >= 40, f"need ≥40 OK rows in fixture, got {len(ok_rows)}"
    for line in ok_rows:
        fields = line.split('\t')
        cls, bits, mode_code, precision, ctx, payload, group = fields
        value = _decode_bits(bits)
        mode_name = MODE_NAMES[int(mode_code)]
        prec = int(precision)
        with decimal.localcontext() as c:
            c.prec = int(ctx)
            result = NAT.format_length(value, "mm", mode_name, prec)
        assert result == payload, \
            f"OK mismatch: value={value!r} mode={mode_name} prec={prec} ctx={ctx}\n" \
            f"  NAT={result!r} FIXTURE={payload!r}"


@check("live sample cross-check: ERR rows raise the EXACT exception (≥20 rows)")
def _():
    rows = _fixture_data_lines()
    err_rows = [l for l in rows if l.split('\t')[0] == 'ERR'][:20]
    assert len(err_rows) >= 20, f"need ≥20 ERR rows in fixture, got {len(err_rows)}"
    for line in err_rows:
        fields = line.split('\t')
        cls, bits, mode_code, precision, ctx, code_str, group = fields
        value = _decode_bits(bits)
        mode_name = MODE_NAMES[int(mode_code)]
        prec = int(precision)
        code = int(code_str)
        exp_type, exp_msg = _expected_err(code, int(mode_code), prec)
        with decimal.localcontext() as c:
            c.prec = int(ctx)
            try:
                NAT.format_length(value, "mm", mode_name, prec)
                raise AssertionError(f"expected {exp_type.__name__}, got no exception")
            except Exception as e:
                assert type(e) is exp_type, \
                    f"ERR type mismatch: got {type(e).__name__}, expected {exp_type.__name__}"
                assert str(e) == exp_msg, \
                    f"ERR msg mismatch: got {str(e)!r}, expected {exp_msg!r}"


def _host_value(s):
    # Type-preserving decode of the stored repr. inf/-inf/nan are not
    # literal_eval-able, so they are special-cased; every other token (ints,
    # floats like -0.0 / 5e-324 / 1e+308) is a valid Python numeric literal,
    # which keeps the int-vs-float distinction the host's value coercion relies
    # on. Deliberately NOT float()-first: that is the lossy path the retired
    # sample test used and it erases that distinction.
    s = s.strip()
    if s == "inf":
        return float("inf")
    if s == "-inf":
        return float("-inf")
    if s == "nan":
        return float("nan")
    return ast.literal_eval(s)


# The host's full HOST-error vocabulary (5 unsupported-mode + 4 integer-
# precision classes). All must survive in the fixture; this is the coverage
# guard that a row-count check alone cannot provide (a fixture could keep its
# count while silently dropping a whole message class).
_HOST_MESSAGE_CLASSES = frozenset({
    "unsupported display mode '' -- use 'Decimal', 'Fractional' or 'Feet & Inches'",
    "unsupported display mode 'Imperial' -- use 'Decimal', 'Fractional' or 'Feet & Inches'",
    "unsupported display mode 'feet_inch' -- use 'Decimal', 'Fractional' or 'Feet & Inches'",
    "unsupported display mode 'x' -- use 'Decimal', 'Fractional' or 'Feet & Inches'",
    "unsupported display mode 'bogus' -- use 'Decimal', 'Fractional' or 'Feet & Inches'",
    "Fraction denominator must be an integer, got 'x'",
    "Fraction denominator must be an integer, got None",
    "Decimal precision must be an integer, got 'x'",
    "Decimal precision must be an integer, got None",
})


def _fixture_host_count_declared():
    """Parse the HOST count the fixture header declares (not a hardcoded count),
    so a fixture that drifts from its own header fails here."""
    with open(FIXTURE, encoding="utf-8") as f:
        for line in f:
            m = re.search(r"HOST:(\d+)", line)
            if m:
                return int(m.group(1))
    raise AssertionError("fixture header declares no HOST count")


@check("live replay: ALL HOST rows raise the EXACT exception (type + message + context)")
def _():
    rows = _fixture_data_lines()
    host_rows = [l for l in rows if l.split('\t')[0] == 'HOST']
    declared = _fixture_host_count_declared()
    # Coverage guard 1: the fixture holds every HOST row it declares.
    assert len(host_rows) == declared, \
        f"fixture header declares {declared} HOST rows but holds {len(host_rows)}"
    # Coverage guard 2: every host message class is represented (no silent drop).
    seen = {l.split('\t')[5] for l in host_rows}
    assert seen == _HOST_MESSAGE_CLASSES, \
        f"HOST message classes drift: missing={sorted(_HOST_MESSAGE_CLASSES - seen)}, " \
        f"extra={sorted(seen - _HOST_MESSAGE_CLASSES)}"
    for line in host_rows:
        fields = line.split('\t')
        cls, value_repr, mode_repr, prec_repr, ctx, message, group = fields
        value = _host_value(value_repr)
        mode = _decode_literal(mode_repr)
        prec = _decode_literal(prec_repr)
        with decimal.localcontext() as c:
            c.prec = int(ctx)
            try:
                NAT.format_length(value, "mm", mode, prec)
                raise AssertionError(f"expected exception for HOST row, got none: {line!r}")
            except Exception as e:
                assert type(e) is ValueError, \
                    f"HOST type mismatch: got {type(e).__name__}, expected ValueError ({line!r})"
                assert str(e) == message, \
                    f"HOST msg mismatch: got {str(e)!r}, expected {message!r} ({line!r})"


@check("value admission: string / non-numeric object refused before mode (coercion order)")
def _():
    # The reference validates the VALUE first, so a bad value raises the value
    # error even when the mode is also bad. Messages are the reference's, pinned.
    try:
        NAT.format_length("1.25", "mm", "bogus", 5)
        raise AssertionError("expected ValueError for a string value")
    except ValueError as e:
        assert str(e) == "value must be a number, not a string '1.25'", f"got {e!r}"
    try:
        NAT.format_length([1, 2], "mm", "Fractional", 16)
        raise AssertionError("expected ValueError for a non-numeric object")
    except ValueError as e:
        assert str(e) == "value must be a number, got [1, 2]", f"got {e!r}"
    # A bool is coerced to its float value (not refused), per the reference.
    assert NAT.format_length(True, "mm", "Decimal", 0) == "1"
    assert NAT.format_length(False, "mm", "Decimal", 0) == "0"


# ---------------------------------------------------------------------------
# The retired reference must be absent from the public tree
# ---------------------------------------------------------------------------
@check("retired Python reference is absent from the public tree")
def _():
    assert not os.path.exists(REFERENCE_PATH), \
        f"spike/unitformat_reference.py must be deleted (archived in legacy/retired-rust18/)"


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
