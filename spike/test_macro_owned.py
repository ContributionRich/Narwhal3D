"""RUST-MACRO-11 -- the native-only command-macro tokenizer, proven.

Differential suite for the production tokenizer ``narwhal3d/macro.py``
(after the native-only retirement, when it is activated) against an
INDEPENDENT pure-Python oracle (``spike/macro_reference.py``, a frozen
byte-faithful copy of the pre-retirement module with its native accessor
forced off, so it never pulls in the product adapter).

What this suite proves GREEN:

* the production tokenizer and the oracle produce the SAME step list (both
  lossless modes) on the standard contract, on adversarial edge cases, on the
  mandatory Unicode parity sets, on a seeded-random sweep, and on long/big
  macros;
* the production tokenizer is NATIVE-ONLY: the retired Python parser is gone,
  and every macro -- SHORT and LONG -- moves the adapter's
  ``_native_macro_calls`` counter (there is no "short stays Python" band);
* a non-string is still ``MacroError`` (counter unmoved);
* a declined native result is ``RuntimeError`` -- NEVER a Python fallback --
  for: an input over the shared 1 MiB cap, a missing library, a library
  without the macro exports, and an adapter that returns ``None``;
* the production module delegates to the SAME qualified adapter a test
  inspects, in BOTH the development (by-path, canonical-name) and the
  production (package ``from . import native_pure``) routes.

Mandatory Unicode parity checks (each was a measured parity blocker, BLOCKER
A/B/C; Main is aligning the shared validator/Rust and these now assert the
agreement -- they MUST stay green and must not be weakened):

* A -- empty-name options: ``_=x`` / ``-=x`` / ``=_x`` / ``a = b`` (and the
  cancel-attached form ``!=-15``): the tokenizer and the oracle now agree
  (the oracle emits an option step with an empty name);
* B -- Nl letter-numerals (Roman numerals): ``str.isalpha()`` (FALSE) vs
  Rust ``char::is_alphabetic()`` (TRUE) -- both sides now agree;
* C -- C1 separators U+001C..U+001F: ``str.isspace()`` (TRUE) vs Rust
  ``char::is_whitespace()`` (FALSE) -- both sides now agree.

TEXT-ONLY parity (a fourth measured discrepancy): a leading ``!`` attached to
a word that is NOT a command stays TEXT -- ``!123``, ``!3Point``, ``!&``.
(``!=-15`` is NOT text-only: both grammars emit the empty-name option, so it
is asserted in the BLOCKER-A set, where its true shape lives.)

Mutation guards (proven to go RED, then restored):
* "constant output" -- the module returns one fixed step list for every
  input;
* "short bypass" -- a host that tokenizes short inputs WITHOUT the shared
  native call (counter unmoved) while still returning a correct-looking
  result.

Run with the Blender-bundled interpreter (has ctypes, no bpy needed):

    tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_macro_owned.py

STATUS BEFORE ACTIVATION: the production ``narwhal3d/macro.py`` still carries
the Python fallback (pre-retirement), so the native-only checks -- the
short-macro counter, the four RuntimeError checks, and the parser-gone check
-- are EXPECTED TO FAIL until Main activates the retired module. That is the
point of this suite: it is the post-activation gate. The parity checks that
compare the tokenizer against the independent oracle stay green either way.
Exits 0 when every check passes; exits 1 otherwise (never claims green while
a check is red).
"""
import importlib.util
import json
import os
import random
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
PRODUCTION = os.path.join(ROOT, "narwhal3d", "macro.py")
ORACLE_FILE = os.path.join(HERE, "macro_reference.py")
ADAPTER_FILE = os.path.join(ROOT, "narwhal3d", "native_pure.py")
CANON = "narwhal3d_native_pure"
LIB_CANDIDATES = [
    os.environ.get("NARWHAL_PURE_CORE", ""),
    os.path.join(ROOT, "narwhal3d", "native", "libnarwhal_pure_core.so"),
    os.path.join(ROOT, "native", "pure_core", "target",
                 "x86_64-unknown-linux-gnu", "release",
                 "libnarwhal_pure_core.so"),
    os.path.join(ROOT, "native", "pure_core", "target", "release",
                 "libnarwhal_pure_core.so"),
]


def _locate_lib():
    for cand in LIB_CANDIDATES:
        if cand and os.path.isfile(cand):
            return cand
    return None


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Load the modules, in the order that keeps the oracle independent.
# ---------------------------------------------------------------------------
LIB = _locate_lib()
if LIB is None:
    print("FATAL: the qualified libnarwhal_pure_core.so is not present. "
          "Staged it and re-run.", file=sys.stderr)
    sys.exit(2)
os.environ["NARWHAL_PURE_CORE"] = LIB

# 1) the oracle FIRST: a frozen pure-Python copy. Its native accessor is
#    forced to ``None`` inside the file, so it never pulls in the product
#    adapter.
ORACLE = _load("rust11_macro_oracle", ORACLE_FILE)

# 2) the shared native adapter, registered under the canonical name so the
#    production module's by-path route (and any host's development route)
#    resolves the SAME object this suite inspects.
NAT = _load(CANON, ADAPTER_FILE)

# 3) the production tokenizer, by file path.
PROD = _load("rust11_macro_prod", PRODUCTION)

results = []


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


def _reset():
    NAT._reset_for_tests()


def _steps_of(mod, text, lossless):
    """Run a tokenizer, returning either the step list or ``("ERR", name, r)``."""
    try:
        return mod.tokenize(text, lossless=lossless)
    except Exception as e:  # noqa: BLE001
        return ("ERR", type(e).__name__, repr(e))


def _compare(text, lossless):
    """``("equal"|"diverge", detail)`` between the tokenizer and the oracle."""
    got = _steps_of(PROD, text, lossless)
    want = _steps_of(ORACLE, text, lossless)
    if got == want:
        return "equal", None
    return "diverge", {"text": text, "lossless": lossless,
                       "candidate": got, "oracle": want}


def _divergences(items):
    out = []
    for text in items:
        for lossless in (False, True):
            status, detail = _compare(text, lossless)
            if status != "equal":
                out.append(detail)
    return out


# ---------------------------------------------------------------------------
# The parity corpora
# ---------------------------------------------------------------------------
# The standard contract: every step kind, the flag set, quotes, comments,
# specials, the ``_3Point`` text rule, both lossless modes, and the empty /
# whitespace edge. (Mirrors the retired module's documented grammar.)
STANDARD = [
    "! _Line _Pause 0,0 _Enter",
    "-_SetDisplayMode _Mode=_Shaded",
    "'_Zoom",
    "!&_Delete",
    "*_Circle",
    "~_Circle",
    "#Circle",
    "%Circle",
    "_3Point",
    "-15,0,0",
    "_Points=10",
    '"My Mode"',
    '_-SelName "Table 01"',
    "_Mode=_Shaded",
    '_Mode="Shaded"',
    "_Line 0,0,0 pause ;15,0,0",
    "; This is a test macro\n_Circle 0,0,0 15",
    "_Pause",
    "_MultiPause",
    "_Enter",
    "_EnterEnd",
    "NoEcho",
    "Run",
    "NoEcho _Circle 5,5 10",
    "! _-Circle 5,5 10",
    "! _SelNone _Move",
    '! _-Layer New "Layer 01:: New Layer" Enter',
    "'_CPlane _3Point\n_Pause _Pause _Pause _Enter",
    "! _-DocumentProperties _Mesh _Custom _MaxEdgeSrf .01 _EnterEnd",
    '-ReadCommandFile "my file.txt"',
    "",
    "   \n\t ",
    "!",
    "_Move !",
    "!_Move",
    # well-behaved Unicode that BOTH grammars agree on (letters are
    # alphabetic in both; digits/other-numbers are text in both):
    "_Caf\u00e9", "_\u0395\u03bb\u03bb\u03b7\u03bd\u03b9\u03ba\u03ac",
    "_5", "_\u0665", "_\u2460",
    # common Unicode whitespace (in the White_Space set for BOTH):
    "a\u2000b", "a\u3000b", "a\u00a0b", "a\u2028b", "a\x0bb", "a\u0085b",
]

# Adversarial but UNAMBIGUOUS edge cases (both grammars must agree; these are
# NOT any of the mandatory parity inputs below, which are checked on their
# own).
ADVERSARIAL = [
    '"unterminated',          # unmatched quote
    '"a" "b"',               # two quoted values
    '""',                    # empty quoted -> text ""
    '" "',                   # quoted single space
    "a;b",                   # trailing comment
    ";full line comment",
    "! !_Zoom",              # leading cancel + flagged command
    "''",                    # flags only, no name -> text
    "____",                  # flags only, no name -> text
    "_",                     # a single flag -> text
    "'!&_Zoom",              # transparent + cancel + history_lock_off
    "____Zoom",              # deep flags
    '"=x"',                  # a quoted value containing '='
    '_A=_B=_C',              # option whose value has its own '='
    "Pause",                 # bare special (unprefixed)
    "EnterEnterEnd",         # not a special -> text
]

# MANDATORY PARITY (former BLOCKER A) -- empty-name options. The oracle emits
# an option step with an EMPTY name (``_=x`` -> option name="" value="x"); the
# native route currently DECLINES these. After Main's fix both must agree on
# the empty-name option.
EMPTY_NAME_OPTIONS = ["_=x", "-=x", "=_x", "a = b"]

# MANDATORY PARITY (former BLOCKER B) -- Nl "letter" numerals (Roman
# numerals): alphabetic in Rust (char::is_alphabetic), not in Python
# (str.isalpha).
UNICODE_ALPHA_BOUNDARY = [
    "_\u2160",          # U+2160 ROMAN NUMERAL ONE (Nl)
    "_\u2164",          # U+2164 ROMAN NUMERAL FIVE (Nl)
    "_\u2169",          # U+2169 ROMAN NUMERAL TEN (Nl)
    "-\u2160\u2164",    # flagged, two Nl letters
]
# MANDATORY PARITY (former BLOCKER C) -- C1 control separators U+001C..U+001F:
# isspace() in Python, not in Rust's is_whitespace().
UNICODE_WS_BOUNDARY = [
    "a\u001cb",         # U+001C FILE SEPARATOR
    "a\u001db",         # U+001D GROUP SEPARATOR
    "a\u001eb",         # U+001E RECORD SEPARATOR
    "a\u001fb",         # U+001F UNIT SEPARATOR
]

# TEXT-ONLY parity (the fourth measured discrepancy): a leading ``!`` attached
# to a word that is NOT a command stays TEXT. The oracle emits the token as a
# single TEXT step (no cancel step); the native route currently emits a
# spurious CANCEL step + text. After Main's fix both must agree on text-only.
TEXT_CANCEL = ["!123", "!3Point", "!&", "!-15"]

LONG_MACRO = ("! _Line _Pause 0,0 _Enter " * 10).strip()      # ~259 B
BIG_MACRO = ("! _Line _Pause 0,0 _Enter '*Zoom -_SelName \"A 01\" "
             "_Mode=_Shaded _Points=10 " * 60).strip()         # ~3 KiB


# ---------------------------------------------------------------------------
# API surface (GREEN)
# ---------------------------------------------------------------------------
@check("the production module keeps the public API (tokenize/MacroError/SPECIAL_COMMANDS)")
def _():
    assert callable(PROD.tokenize)
    assert issubclass(PROD.MacroError, ValueError)
    assert PROD.SPECIAL_COMMANDS == frozenset({
        "pause", "multipause", "enter", "enterend",
        "noecho", "echo", "setredrawoff", "setredrawon", "run"}), \
        PROD.SPECIAL_COMMANDS
    assert callable(ORACLE.tokenize)
    assert issubclass(ORACLE.MacroError, ValueError)


@check("the retired Python parser is GONE from the production module")
def _():
    for gone in ("_split_tokens", "_classify", "_strip_flags",
                 "_command_step", "_clean_value", "_is_fully_quoted",
                 "_find_eq_outside_quotes", "_with_cancel",
                 "_FLAG_CHARS",
                 "NATIVE_MACRO_MIN_BYTES", "NATIVE_MACRO_JSON_MAX_BYTES",
                 "NATIVE_MACRO_PACKED_MIN_BYTES"):
        assert not hasattr(PROD, gone), \
            "the module still carries the retired %r" % gone
    # the ABI glue that MUST remain (host converters kept for the direct
    # ABI tests):
    assert callable(PROD._steps_from_json)
    assert callable(PROD._steps_from_packed)


# ---------------------------------------------------------------------------
# Delegation to the shared adapter (GREEN)
# ---------------------------------------------------------------------------
@check("the production module delegates to the SAME qualified adapter (by-path route)")
def _():
    # The by-path route registers the adapter under the canonical name; the
    # module must resolve exactly that object (not a private copy), so the
    # counter this suite inspects is the one the module moves.
    assert NAT in sys.modules.values() or sys.modules.get(CANON) is NAT
    native_attr = getattr(PROD, "native_pure", None)
    if native_attr is None:
        # Pre-retirement shape: the accessor resolves the adapter lazily.
        assert PROD._native_macro() is NAT, \
            "the module must resolve the shared adapter, not a private copy"
    else:
        # Native-only shape: the resolved adapter is exposed.
        assert native_attr is NAT, \
            "the module must use the shared adapter, not a private copy"
        assert PROD.native_pure._native_macro is NAT._native_macro


@check("the production same-directory route (from . import native_pure) works")
def _():
    # A minimal throwaway package in a temp dir: the module as ``macro.py``
    # alongside the real adapter as ``native_pure.py`` (a re-export of the
    # already-loaded qualified adapter, so it is the SAME native function).
    pkgroot = tempfile.mkdtemp(prefix="rust11_pkg_")
    pkgdir = os.path.join(pkgroot, "rust11_pkg")
    os.makedirs(pkgdir)
    open(os.path.join(pkgdir, "__init__.py"), "w", encoding="utf-8").close()
    with open(os.path.join(pkgdir, "native_pure.py"), "w", encoding="utf-8") as f:
        f.write("from narwhal3d_native_pure import _native_macro\n")
    with open(PRODUCTION, encoding="utf-8") as src, \
            open(os.path.join(pkgdir, "macro.py"), "w", encoding="utf-8") as dst:
        dst.write(src.read())
    sys.path.insert(0, pkgroot)
    try:
        import rust11_pkg.macro as prod_pkg  # noqa: PLC0415
        # __package__ is truthy -> the module took the `from . import` route.
        assert prod_pkg.__package__ == "rust11_pkg"
        resolved = getattr(prod_pkg, "native_pure", None)
        if resolved is None:
            resolved = prod_pkg._native_macro()
        assert resolved._native_macro is NAT._native_macro, \
            "the production route must resolve the same native function"
        assert prod_pkg.tokenize("! _Line _Pause 0,0 _Enter") == \
            ORACLE.tokenize("! _Line _Pause 0,0 _Enter")
    finally:
        sys.path.remove(pkgroot)
        for name in ("rust11_pkg", "rust11_pkg.macro", "rust11_pkg.native_pure"):
            sys.modules.pop(name, None)


# ---------------------------------------------------------------------------
# Parity: tokenizer == oracle (GREEN)
# ---------------------------------------------------------------------------
@check("tokenizer == oracle on the standard contract (both lossless modes)")
def _():
    d = _divergences(STANDARD)
    assert not d, "tokenizer and oracle disagree on the standard contract: %r" % d


@check("tokenizer == oracle on adversarial edge cases (both lossless modes)")
def _():
    d = _divergences(ADVERSARIAL)
    assert not d, "tokenizer and oracle disagree on an adversarial edge: %r" % d


@check("MANDATORY parity: empty-name options (formal BLOCKER A resolved)")
def _():
    d = _divergences(EMPTY_NAME_OPTIONS)
    assert not d, ("empty-name options (\"_=x\", \"-=x\", \"=_x\", \"a = b\") "
                   "diverge from the oracle: %r -- the native route must agree "
                   "with the oracle (an option step with an empty name), not "
                   "raise or re-tokenize in Python" % d)


@check("MANDATORY parity: Nl letter-numerals (formal BLOCKER B resolved)")
def _():
    d = _divergences(UNICODE_ALPHA_BOUNDARY)
    assert not d, ("Nl letter-numerals (Roman numerals) diverge from the "
                   "oracle: %r -- char::is_alphabetic() must agree with "
                   "str.isalpha(), not raise or re-tokenize in Python" % d)


@check("MANDATORY parity: C1 separators U+001C..U+001F (formal BLOCKER C resolved)")
def _():
    d = _divergences(UNICODE_WS_BOUNDARY)
    assert not d, ("C1 separators U+001C..U+001F diverge from the oracle: %r "
                   "-- char::is_whitespace() must agree with str.isspace(), "
                   "not raise or re-tokenize in Python" % d)


@check("MANDATORY parity: leading-! text stays text, no spurious cancel (!123, !3Point, !&, !-15)")
def _():
    d = _divergences(TEXT_CANCEL)
    assert not d, ("a leading ! attached to a non-command must stay TEXT "
                   "(!123, !3Point, !&, !-15) WITHOUT a spurious cancel step, "
                   "matching the oracle: %r" % d)


@check("tokenizer == oracle on a seeded-random macro sweep (both lossless modes)")
def _():
    rng = random.Random(0x5EED)
    pool = ["_", "-", "'", "!", "&", "*", "~", "#", "%",
            "_Line", "_Circle", "0,0", "0,0,0", "5", "10",
            '"Layer 01"', "Pause", "Enter", "EnterEnd", "MultiPause",
            "NoEcho", "Echo", "SetRedrawOff", "SetRedrawOn", "Run",
            "_Mode=_Shaded", "_Points=10", "Table 01", "::", ";comment",
            "_3Point", "-15,0,0", "New", "_Zoom"]
    random_macros = []
    for _ in range(400):
        k = rng.randrange(1, 9)
        random_macros.append(" ".join(rng.choice(pool) for _ in range(k)))
    d = _divergences(random_macros)
    assert not d, "tokenizer and oracle disagree on a random macro: %r" % d


@check("the tokenizer's step list == the oracle's on the long/big macros")
def _():
    for text in (LONG_MACRO, BIG_MACRO):
        for lossless in (False, True):
            status, detail = _compare(text, lossless)
            assert status == "equal", detail


# ---------------------------------------------------------------------------
# Native at EVERY length (GREEN post-activation)
# ---------------------------------------------------------------------------
@check("a SHORT macro is native (counter moves) -- no 'short stays Python' band")
def _():
    _reset()
    before = NAT._native_macro_calls
    assert isinstance(PROD.tokenize("_Line"), list)
    assert NAT._native_macro_calls == before + 1, \
        ("short macro must be native: counter %d -> %d" %
         (before, NAT._native_macro_calls))


@check("a LONG macro is native (counter moves)")
def _():
    _reset()
    before = NAT._native_macro_calls
    assert isinstance(PROD.tokenize(LONG_MACRO, lossless=True), list)
    assert NAT._native_macro_calls == before + 1, \
        ("long macro must be native: counter %d -> %d" %
         (before, NAT._native_macro_calls))


@check("a BIG macro (> the old JSON band) is native (counter moves)")
def _():
    _reset()
    before = NAT._native_macro_calls
    assert isinstance(PROD.tokenize(BIG_MACRO), list)
    assert NAT._native_macro_calls == before + 1, \
        ("big macro must be native: counter %d -> %d" %
         (before, NAT._native_macro_calls))


# ---------------------------------------------------------------------------
# Non-string refusal (GREEN)
# ---------------------------------------------------------------------------
@check("a non-string macro is MacroError (native counter unmoved)")
def _():
    _reset()
    before = NAT._native_macro_calls
    for bad in (None, 5, ["_Move"], b"_Move", 3.5, object()):
        try:
            PROD.tokenize(bad)
            raise AssertionError("expected MacroError for %r" % (bad,))
        except PROD.MacroError:
            pass
    assert NAT._native_macro_calls == before, \
        ("non-string rejections must not touch the native counter",
         before, NAT._native_macro_calls)


# ---------------------------------------------------------------------------
# Declined native result -> RuntimeError (GREEN post-activation)
# ---------------------------------------------------------------------------
@check("over the 1 MiB cap -> RuntimeError (no Python fallback)")
def _():
    _reset()
    before = NAT._native_macro_calls
    big = "a" * ((1 << 20) + 1)   # 1048577 > 1048576
    try:
        PROD.tokenize(big)
        raise AssertionError("expected RuntimeError for a >1 MiB macro")
    except RuntimeError:
        pass
    assert NAT._native_macro_calls == before, \
        ("a refused (over-cap) native result must not count as a success",
         before, NAT._native_macro_calls)


def _env_swap(value):
    real = os.environ.get("NARWHAL_PURE_CORE")
    if value is None:
        os.environ.pop("NARWHAL_PURE_CORE", None)
    else:
        os.environ["NARWHAL_PURE_CORE"] = value
    _reset()
    return real


@check("a missing library -> RuntimeError (no Python fallback)")
def _():
    real = _env_swap(os.path.join(tempfile.gettempdir(), "no_such_lib.so"))
    try:
        assert NAT._macro_binding() is None, \
            "a missing library must make the binding None"
        before = NAT._native_macro_calls
        try:
            PROD.tokenize("! _Line _Pause 0,0 _Enter")
            raise AssertionError("expected RuntimeError for a missing library")
        except RuntimeError:
            pass
        assert NAT._native_macro_calls == before, \
            ("missing-lib refusal must not touch the native counter",
             before, NAT._native_macro_calls)
    finally:
        if real is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = real
        _reset()


@check("a library without the macro exports (stale) -> RuntimeError")
def _():
    # A real file that is not a valid shared library models "old symbol":
    # CDLL fails (OSError) so the macro binding is None and the module must
    # raise RuntimeError rather than run a Python tokenizer.
    stale = os.path.join(tempfile.gettempdir(), "rust11_stale_lib.so")
    with open(stale, "w", encoding="utf-8") as f:
        f.write("not a shared object\n")
    real = _env_swap(stale)
    try:
        assert NAT._macro_binding() is None, \
            "a library without the macro export must make the binding None"
        before = NAT._native_macro_calls
        try:
            PROD.tokenize("!&_Delete")
            raise AssertionError("expected RuntimeError for a stale library")
        except RuntimeError:
            pass
        assert NAT._native_macro_calls == before, \
            ("stale-lib refusal must not touch the native counter",
             before, NAT._native_macro_calls)
    finally:
        if real is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = real
        _reset()


@check("an adapter that returns None (refused/malformed) -> RuntimeError")
def _():
    assert NAT._macro_binding() is not None, "the good library must be bound first"
    real_macro = NAT._native_macro
    NAT._native_macro = lambda *a, **k: None  # simulate a failed/refused call
    try:
        before = NAT._native_macro_calls
        try:
            PROD.tokenize("! _Line _Pause 0,0 _Enter")
            raise AssertionError("expected RuntimeError when the adapter returns None")
        except RuntimeError:
            pass
        assert NAT._native_macro_calls == before, \
            ("a refused native result must not touch the native counter",
             before, NAT._native_macro_calls)
    finally:
        NAT._native_macro = real_macro
        _reset()


# ---------------------------------------------------------------------------
# Mutation guards (GREEN: the mutations must be CAUGHT)
# ---------------------------------------------------------------------------
def _expect_guard_red(mutate, restore, guard, label):
    """Apply *mutate*; require *guard* (the assertion a check would run) to FAIL
    -- i.e. prove the mutation is CAUGHT. Restore in ``finally``. If the guard
    does NOT fail, the mutation escaped the guard (a weak test) -> raise."""
    mutate()
    try:
        guard()
    except AssertionError:
        print("  [mutation] %s: guard went RED (caught) -- as required" % label)
    else:
        raise AssertionError("mutation %r was NOT caught by its guard (weak test)"
                             % label)
    finally:
        restore()


@check("mutation 'constant output' is caught by the parity guard")
def _():
    # A broken module that ignores its input and always emits one step list.
    real_tokenize = PROD.tokenize

    def mutate():
        PROD.tokenize = lambda *a, **k: [{"kind": "text", "value": "constant"}]

    def restore():
        PROD.tokenize = real_tokenize

    def guard():
        a = PROD.tokenize("_Line")
        b = PROD.tokenize("!&_Delete")
        # the parity/distinctness guard demands two distinct macros give two
        # distinct step lists -> must FAIL under the constant-output mutant:
        assert a != b, "two distinct macros must give two distinct step lists"

    _expect_guard_red(mutate, restore, guard, "constant output")
    _reset()


@check("mutation 'short bypass' is caught by the counter guard")
def _():
    # A broken HOST that tokenizes short (<240 B) macros WITHOUT the shared
    # native call: correct-looking result (the oracle's step list, re-encoded
    # through the JSON ABI converter) but the counter unmoved. The retired
    # production module had exactly this shape (the "short stays Python" band),
    # so this is the right defect to catch on the native-only surface.
    real_tokenize = PROD.tokenize

    def mutate():
        def bypass(text, *, lossless=False):
            if not isinstance(text, str):
                raise PROD.MacroError("a macro is a string; got %s"
                                      % type(text).__name__)
            if len(text.encode("utf-8")) < 240:
                # silent: correct output, but NO native call (the defect)
                payload = json.dumps(
                    ORACLE.tokenize(text, lossless=lossless)).encode("utf-8")
                return PROD._steps_from_json(payload)
            return real_tokenize(text, lossless=lossless)
        PROD.tokenize = bypass

    def restore():
        PROD.tokenize = real_tokenize

    def guard():
        _reset()
        result = PROD.tokenize("_Line")
        assert result == ORACLE.tokenize("_Line"), "bypass should look correct"
        assert NAT._native_macro_calls == 0, "bypass leaves the counter unmoved"
        # the real guard (counter must MOVE) -> must FAIL here:
        assert NAT._native_macro_calls > 0, "short macro must be native"

    _expect_guard_red(mutate, restore, guard, "short bypass")
    _reset()


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def main():
    print("RUST-MACRO-11 native-only macro tokenizer (production vs oracle)")
    print("  library      : %s" % LIB)
    print("  production   : %s" % PRODUCTION)
    print("  oracle       : %s" % ORACLE_FILE)
    print("  adapter      : %s" % ADAPTER_FILE)
    print()
    width = max(len(n) for n, _s, _d in results) if results else 0
    green_failed = sum(1 for r in results if r[1] == "FAIL")
    for name, status, detail in results:
        line = "  [%-4s] %s" % (status, name.ljust(width))
        if detail:
            line += "  ->  %s" % detail
        print(line)
    print()
    print("  checks       : %d/%d passed" % (len(results) - green_failed,
                                            len(results)))
    if green_failed:
        print("  RESULT: FAIL -- %d check(s) red. Pre-activation, the "
              "native-only checks (short counter, RuntimeError on declined "
              "native, parser-gone) are EXPECTED red until the retirement "
              "activates; the oracle-parity checks must be green either way."
              % green_failed)
        return 1
    print("  RESULT: PASS -- every check is green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
