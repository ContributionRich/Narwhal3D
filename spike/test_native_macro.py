"""Native command-macro tokenizer: direct ABI proof + native-only policy.

Complements ``spike/test_macro.py`` (which pins the raw-token grammar, the
step contract, flags, quotes, comments, specials, the ``_3Point`` text rule
and the non-string refusal on the Python path) and
``spike/test_macro_owned.py`` (the differential gate for the native-only
retirement) by proving, on the production module:

* the Rust ABI is present, and the direct JSON and packed ABIs resolve and
  convert through the module's host converters (``_steps_from_json`` /
  ``_steps_from_packed``) to the exact Python step contract;
* the tokenizer is NATIVE-ONLY: every length -- SHORT included -- moves the
  adapter's ``_native_macro_calls`` counter (there is no "short stays Python"
  band any more), and on the SAME input the native route returns the same
  step list the independent pure-Python reference produces (the "native ==
  Python reference" invariant, including the lossless ``prefixes`` tuples);
* a non-string macro is STILL refused with ``MacroError`` (the native route
  is only ever reached for a string) and does not move the counter;
* a DECLINED native result -- a missing library, a library without the macro
  exports, or an adapter that returns ``None`` (refused / malformed) -- is
  raised as ``RuntimeError`` with the counter UNMOVED: there is NO Python
  replay (the retired tokenizer must not be reachable);
* a performance REPORT: the native route is benchmarked against the
  independent pure-Python reference on the same valid input (reported, not
  asserted -- the owner deliberately consolidates on the native route rather
  than claiming every tiny call is faster, so there is no routing guard).

The pure-Python reference for the parity and performance checks is
``spike/macro_reference.py`` -- a frozen, independent copy of the
pre-retirement tokenizer with its native accessor forced off -- NOT a product
fallback. ``_force_oracle`` temporarily patches ``macro.tokenize`` to that
reference so a check can compare the native route against it without the
reference ever being a production code path.

STATUS BEFORE ACTIVATION: the production ``narwhal3d/macro.py`` still carries
the Python fallback (pre-retirement), so the native-only checks -- the short
counter and the four declined-native ``RuntimeError`` checks -- are EXPECTED
TO FAIL until Main activates the retired module. That is the point of this
suite: it is the post-activation gate. The direct-ABI, parity and
non-string checks stay green either way.
"""
import ctypes
import importlib.util
import os
import subprocess
import sys
import time
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtime_paths
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = str(runtime_paths.python(ROOT))
LIB_CANDIDATES = [
    os.environ.get("NARWHAL_PURE_CORE", ""),
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


def _ensure_lib():
    """Return the ``.so`` path, building the crate once if it is not present."""
    lib = _locate_lib()
    if lib is not None:
        return lib
    manifest = os.path.join(ROOT, "native", "pure_core", "Cargo.toml")
    if not os.path.isfile(manifest):
        return None
    subprocess.run(
        ["cargo", "+1.95.0", "build", "--release", "--locked", "--offline",
         "--manifest-path", manifest, "--target", "x86_64-unknown-linux-gnu"],
        cwd=ROOT, check=True,
    )
    return _locate_lib()


# Make the native library discoverable before the adapter is first imported.
_LIB = _ensure_lib()
if _LIB is not None:
    os.environ["NARWHAL_PURE_CORE"] = _LIB


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, "narwhal3d", filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# The independent pure-Python reference (the frozen pre-retirement tokenizer,
# native accessor forced off) -- the parity and performance baseline.
def _load_oracle():
    spec = importlib.util.spec_from_file_location(
        "native_test_macro_oracle",
        os.path.join(ROOT, "spike", "macro_reference.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["native_test_macro_oracle"] = mod
    spec.loader.exec_module(mod)
    return mod


ORACLE = _load_oracle()

macro = _load("narwhal_native_test_macro", "macro.py")
tokenize = macro.tokenize
MacroError = macro.MacroError
# Resolve the SAME native module the tokenizer uses, so we can inspect its
# counter and monkeypatch it in the declined-native tests.
native = macro._native_macro()

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
    """Forget discovery + counter state so a check can re-point the binding."""
    if native is not None:
        native._reset_for_tests()


def _assert_native_ran(before, label):
    """Tripwire: the counter must have moved (the native route actually ran)."""
    assert native is not None, "adapter did not resolve the native module"
    assert native._native_macro_calls > before, \
        (label, "native route did not run (%d -> %d)" %
         (before, native._native_macro_calls))


@contextmanager
def _force_oracle():
    """Temporarily patch ``macro.tokenize`` to the INDEPENDENT pure-Python
    reference so a parity/performance check can compare against it. The
    reference is a test-only baseline (a frozen copy with its native accessor
    forced off), NOT a product fallback: the production module's own code path
    is never re-entered through this."""
    real = macro.tokenize
    macro.tokenize = ORACLE.tokenize
    try:
        yield
    finally:
        macro.tokenize = real


# The valid macros: (macro, lossless). The expected value is NOT hardcoded --
# it is asserted to be identical to the independent pure-Python reference
# (the checks below), which is the real invariant. These cover every step
# kind, the flag set, quotes, comments, specials, the _3Point text rule,
# both lossless modes and the empty / whitespace edge.
VALID_MACROS = [
    ("! _Line _Pause 0,0 _Enter", False),
    ("! _Line _Pause 0,0 _Enter", True),
    ("-_SetDisplayMode _Mode=_Shaded", False),
    ("-_SetDisplayMode _Mode=_Shaded", True),
    ("'_Zoom", False),
    ("!&_Delete", False),
    ("!&_Delete", True),
    ("*_Circle", False),
    ("~_Circle", False),
    ("#Circle", False),
    ("%Circle", False),
    ("_3Point", False),
    ("-15,0,0", False),
    ("_Points=10", False),
    ("_Points=10", True),
    ('"My Mode"', False),
    ('_-SelName "Table 01"', False),
    ("_-SelName Table 01", False),
    ("_Mode=_Shaded", False),
    ('_Mode="Shaded"', False),
    ("_Line 0,0,0 pause ;15,0,0", False),
    ("; This is a test macro\n_Circle 0,0,0 15", False),
    ("_Pause", False),
    ("_Pause", True),
    ("_MultiPause", True),
    ("_Enter", False),
    ("_EnterEnd", True),
    ("NoEcho", False),
    ("Run", False),
    ("NoEcho _Circle 5,5 10", False),
    ("! _-Circle 5,5 10", False),
    ("! _SelNone _Move", False),
    ("! _-Layer New \"Layer 01:: New Layer\" Enter", False),
    ("'_CPlane _3Point\n_Pause _Pause _Pause _Enter", False),
    ("'_CPlane _3Point\n_Pause _Pause _Pause _Enter", True),
    ("! _-DocumentProperties _Mesh _Custom _MaxEdgeSrf .01 _EnterEnd", False),
    ('-ReadCommandFile "my file.txt"', False),
    ("", False),
    ("   \n\t ", False),
    ("!", False),
    ("_Move !", False),
    ("!_Move", False),
]
# A SHORT macro: the native-only policy routes it through the native ABI too
# (no "short stays Python" band any more).
SHORT_MACRO = "_Line"
LONG_MACRO = ("! _Line _Pause 0,0 _Enter " * 10).strip()
# A macro clearly at/above the old packed-tier crossover: the production
# native route for this one (JSON or packed, either way it counts as native).
LARGE_MACRO = ("! _Line _Pause 0,0 _Enter '*Zoom -_SelName \"A 01\" "
               "_Mode=_Shaded _Points=10 " * 120).strip()

# A leading ``!`` attached to a word that is NOT a command stays TEXT (the
# fourth measured parity case) -- asserted against the independent reference.
# The oracle emits the token as a single TEXT step (no cancel step); the
# native route currently emits a spurious CANCEL step + text.
TEXT_CANCEL = ["!123", "!3Point", "!&", "!-15"]


@check("native library is built, discovered, and exposes the macro ABI")
def _():
    assert _LIB is not None, "pure_core .so not present and build failed"
    assert native is not None, "adapter did not resolve the native module"
    binding = native._macro_binding()
    assert binding is not None and len(binding) == 2 and \
        callable(binding[0]) and callable(binding[1]), \
        "the macro binding must resolve (size, encode)"
    # The counter is a real attribute (the tripwire target).
    assert hasattr(native, "_native_macro_calls")


@check("the packed one-pass macro ABI (Q117-04) is present and resolvable")
def _():
    _reset()
    packed = native._macro_packed_binding()
    assert packed is not None, "the packed macro export must resolve"
    assert callable(packed), "the packed handle must be callable"
    assert native._macro_packed_native_active(LONG_MACRO) is True
    assert native._macro_packed_native_active(5) is False, \
        "a non-string is never a packed-route candidate"


@check("a SHORT macro is native (the counter moves) -- no 'short stays Python' band")
def _():
    _reset()
    before = native._native_macro_calls
    assert isinstance(tokenize(SHORT_MACRO), list)
    assert native._native_macro_calls == before + 1, \
        ("short macro must be native: counter %d -> %d" %
         (before, native._native_macro_calls))


@check("a LONG macro is native (the counter moves)")
def _():
    _reset()
    before = native._native_macro_calls
    assert isinstance(tokenize(LONG_MACRO, lossless=True), list)
    _assert_native_ran(before, "long macro")


@check("native equals the pure-Python reference on the SAME valid input")
def _():
    _reset()
    for text, lossless in VALID_MACROS:
        got_native = tokenize(text, lossless=lossless)
        got_ref = ORACLE.tokenize(text, lossless=lossless)
        assert got_ref is not None, (text, lossless, got_ref)
        # Byte-for-byte the Python contract: the step dicts (with their
        # ``prefixes`` tuples) must be exactly equal.
        assert got_native == got_ref, \
            (text, lossless, got_native, "expected", got_ref)


@check("lossless native prefixes are TUPLES (the Python contract)")
def _():
    _reset()
    before = native._native_macro_calls
    got = tokenize(LONG_MACRO, lossless=True)
    for step in got:
        if "prefixes" in step:
            assert isinstance(step["prefixes"], tuple), step
    _assert_native_ran(before, "macro lossless")


# >=240-byte macros (the packed route fires for these). Each is asserted to be
# byte-for-byte the independent pure-Python reference result in BOTH lossless
# modes -- the real long-macro invariant, covering commands with the full flag
# set, a leading-`!` cancel, specials, options, and quoted values.
LONG_MACROS = [
    LONG_MACRO,
    ('!&_Delete *_Circle ~_Line "My Layer 01" "Table 01:: New" '
     '_Mode=_Shaded _Points=10 Pause Enter ') * 4,
    ("! _-DocumentProperties _Mesh _Custom _MaxEdgeSrf .01 _EnterEnd ") * 4,
    ("'_CPlane _3Point\n_Pause _Pause _Pause _Enter ") * 5,
]


@check("long native equals the pure-Python reference on >=240-byte inputs")
def _():
    _reset()
    for text in LONG_MACROS:
        for lossless in (False, True):
            got_native = tokenize(text, lossless=lossless)
            got_ref = ORACLE.tokenize(text, lossless=lossless)
            assert got_native == got_ref, \
                (text[:40], lossless, got_native[:3], "expected", got_ref[:3])


@check("the _3Point text rule holds on the packed route (host converter)")
def _():
    # A flagged word whose name starts with a digit is TEXT, not a command.
    # If the Rust `name[0].isalpha()` requirement were loosened to also accept
    # a leading digit, this goes RED (packed would emit a command step).
    _reset()
    packed = native._native_macro_packed("_3Point", False)
    assert packed is not None, "the packed call must succeed for _3Point"
    steps = macro._steps_from_packed(packed, False)
    assert steps == [{"kind": "text", "value": "_3Point"}], steps
    packed_lossless = native._native_macro_packed("_3Point", True)
    assert macro._steps_from_packed(packed_lossless, True) == \
        [{"kind": "text", "value": "_3Point"}]


@check("the _3Point text rule holds on the native route (mutation target)")
def _():
    # A flagged word whose name starts with a digit is TEXT, not a command.
    # If the Rust ``name[0].isalpha()`` requirement were loosened to also
    # accept a leading digit, this would go RED (native would emit a command
    # step).
    _reset()
    before = native._native_macro_calls
    payload = native._native_macro("_3Point", False)
    assert payload is not None
    assert macro._steps_from_json(payload) == \
        [{"kind": "text", "value": "_3Point"}]
    _assert_native_ran(before, "direct macro _3Point")
    assert tokenize("-15,0,0") == [{"kind": "text", "value": "-15,0,0"}]
    assert tokenize("_Points=10") == [{"kind": "option",
                                       "name": "Points", "value": "10"}]


@check("a non-string macro is still refused with MacroError (counter unmoved)")
def _():
    _reset()
    before = native._native_macro_calls
    for bad in (None, 5, ["_Move"], b"_Move"):
        try:
            tokenize(bad)
            raise AssertionError("expected MacroError for %r" % (bad,))
        except MacroError:
            pass
    # The native route is only ever reached for a string, so none of these may
    # have counted as a native SUCCESS.
    assert native._native_macro_calls == before, \
        ("non-string rejections must not touch the native counter",
         before, native._native_macro_calls)


@check("a leading-! non-command stays TEXT, no spurious cancel (native == reference)")
def _():
    _reset()
    for text in TEXT_CANCEL:
        got_native = tokenize(text)
        got_ref = ORACLE.tokenize(text)
        assert got_native == got_ref, \
            (text, got_native, "expected", got_ref)
        # the oracle shape: ONE text step, the token verbatim, NO cancel step
        assert got_native == [{"kind": "text", "value": text}], \
            (text, got_native)


@check("declined native (missing library) -> RuntimeError, counter unmoved")
def _():
    _reset()
    real_env = os.environ.get("NARWHAL_PURE_CORE")
    os.environ["NARWHAL_PURE_CORE"] = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "no_such_lib.so")
    try:
        assert native._macro_binding() is None, \
            "a missing library must make the binding None"
        before = native._native_macro_calls
        try:
            tokenize("! _Line _Pause 0,0 _Enter")
            raise AssertionError(
                "expected RuntimeError (no Python replay) for a missing library")
        except RuntimeError:
            pass
        assert native._native_macro_calls == before, \
            ("missing-lib refusal must not touch the native counter",
             before, native._native_macro_calls)
    finally:
        if real_env is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = real_env
        _reset()


@check("declined native (stale library) -> RuntimeError, counter unmoved")
def _():
    # A real file that is not a valid shared library models "old symbol":
    # CDLL fails (OSError) so the binding is None and the module must raise
    # RuntimeError -- the retired Python tokenizer must NOT be reachable.
    import tempfile
    stale = os.path.join(tempfile.gettempdir(), "native_macro_stale.so")
    with open(stale, "w", encoding="utf-8") as f:
        f.write("not a shared object\n")
    _reset()
    real_env = os.environ.get("NARWHAL_PURE_CORE")
    os.environ["NARWHAL_PURE_CORE"] = stale
    try:
        assert native._macro_binding() is None, \
            "a library without the macro export must make the binding None"
        before = native._native_macro_calls
        try:
            tokenize("!&_Delete")
            raise AssertionError(
                "expected RuntimeError (no Python replay) for a stale library")
        except RuntimeError:
            pass
        assert native._native_macro_calls == before, \
            ("stale-lib refusal must not touch the native counter",
             before, native._native_macro_calls)
    finally:
        if real_env is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = real_env
        _reset()


@check("declined native (adapter returns None) -> RuntimeError, counter unmoved")
def _():
    _reset()
    assert native is not None and native._macro_binding() is not None
    real_macro = native._native_macro
    native._native_macro = lambda *a, **k: None  # simulate a failed/refused call
    try:
        before = native._native_macro_calls
        try:
            tokenize("! _Line _Pause 0,0 _Enter")
            raise AssertionError(
                "expected RuntimeError (no Python replay) when the "
                "adapter returns None")
        except RuntimeError:
            pass
        assert native._native_macro_calls == before, \
            ("a refused native result must not touch the native counter",
             before, native._native_macro_calls)
    finally:
        native._native_macro = real_macro
        _reset()


@check("malformed successful macro JSON -> RuntimeError (no Python replay)")
def _():
    # A binding that "succeeds" (rc==0) but emits a payload the strict
    # validator refuses: the adapter reports ``None``, and the native-only
    # module must raise RuntimeError -- not re-run the retired tokenizer.
    _reset()
    payload = (b'[{"kind":"command","name":"Move","dash":0,'
               b'"underscore":true,"transparent":false}]')
    real_binding = native._macro_binding

    def size_fn(_text, _length, _flag):
        return len(payload)

    def encode_fn(_text, _length, _flag, out, out_len):
        assert out_len == len(payload)
        ctypes.memmove(out, payload, len(payload))
        return 0

    native._macro_binding = lambda: (size_fn, encode_fn)
    try:
        before = native._native_macro_calls
        assert native._native_macro("_Move", False) is None
        assert native._native_macro_calls == before
        try:
            tokenize("_Move")
            raise AssertionError(
                "expected RuntimeError (no Python replay) for a malformed "
                "native output")
        except RuntimeError:
            pass
        assert native._native_macro_calls == before, \
            ("a malformed native output must not count as a native success",
             before, native._native_macro_calls)
    finally:
        native._macro_binding = real_binding
        _reset()


@check("packed macro validator rejects invalid UTF-8, gaps, and trailing bytes")
def _():
    assert native._valid_macro_packed(1, 1, [4], [0], [1], [0], [0],
                                      bytearray(b"x")) is True
    assert native._valid_macro_packed(1, 1, [4], [0], [1], [0], [0],
                                      bytearray(b"\xff")) is False
    # offset=1, len=1 leaves byte zero unclaimed.
    gap = (1 << 32) | 1
    assert native._valid_macro_packed(1, 2, [4], [0], [gap], [0], [0],
                                      bytearray(b"xy")) is False


@check("performance REPORT: native vs the pure-Python reference (not asserted)")
def _():
    # The owner deliberately consolidates on the native route rather than
    # claiming every tiny call is faster, so this is a REPORT, not a routing
    # guard: measure the same valid input on the native route and on the
    # independent pure-Python reference, and print the numbers. A slow native
    # route is surfaced (DECISION NEEDED), never silently accepted as a win.
    if native is None or native._macro_binding() is None:
        print("  [perf] SKIPPED -- native macro binding not available")
        return
    tiers = [("short (5B)", SHORT_MACRO),
             ("json-tier (259B, the old 240B crossover)", LONG_MACRO)]

    def _best(fn, N):
        best = float("inf")
        for _ in range(3):
            t0 = time.perf_counter()
            for _ in range(N):
                fn()
            best = min(best, time.perf_counter() - t0)
        return best

    for label, text in tiers:
        N = max(2000, 200000 // max(1, len(text.encode())))
        _reset()
        t_native = _best(lambda: tokenize(text, lossless=True), N)
        with _force_oracle():
            _reset()
            t_py = _best(lambda: ORACLE.tokenize(text, lossless=True), N)
        r = t_native / max(t_py, 1e-9)
        print("  [perf] macro.tokenize %s native=%.5fs python=%.5fs "
              "ratio=%.2fx (%d calls)" % (label, t_native, t_py, r, N))
        if r > 1.0:
            print("  [perf] NOTE: native route is slower than the Python "
                  "reference here (ratio %.2fx) -- DECISION NEEDED for the "
                  "owner (reported, not asserted)" % r)


failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
print(f"\n{len(results) - failed}/{len(results)} passed")
if failed:
    print("NOTE: before the retirement activates, the native-only checks "
          "(short counter, the four declined-native RuntimeError checks) are "
          "EXPECTED red; the direct-ABI, parity and non-string checks stay "
          "green either way.")
sys.exit(1 if failed else 0)
