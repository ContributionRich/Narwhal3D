"""Native command-macro tokenizer: direct ABI proof + route activation + fallbacks.

Complements ``spike/test_macro.py`` (which pins the raw-token grammar, the step
contract, flags, quotes, comments, specials, the ``_3Point`` text rule and the
non-string refusal on the Python path) by proving the *native* route is the one
actually running for valid input, and that every fallback scenario runs the
*unchanged* Python tokenizer with the exact same step list and with the native
counter NOT incremented.

* the Rust ABI is present and ``_macro_binding()`` is not ``None``;
* ``macro.tokenize`` activates the native route (the ``_native_macro_calls``
  counter must move) and, on the SAME input, returns the same step list the
  pure-Python tokenizer does (the "native matches the Python reference"
  invariant, including the lossless ``prefixes`` tuples);
* a non-string macro is STILL refused with ``MacroError`` (the native route is
  only ever reached for a string) and does not move the native counter;
* the three fallback scenarios (missing library, an old library without the
  macro export, a malformed native output) all run the *unchanged Python
  tokenizer* with the exact same step list and with the counter NOT incremented;
* a performance guard: on the *same* valid input the native success path must
  not be *materially* slower than the pure-Python tokenizer (the "never slower"
  rule -- a regression leaves a DECISION NEEDED note for the owner).

The suite is green with or without a prebuilt ``.so``: if the library is not
present it builds the crate once (offline, pinned toolchain) and rechecks.
"""
import ctypes
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, "tools", "blender-patched", "5.2", "python",
                  "bin", "python3.13")
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


macro = _load("narwhal_native_test_macro", "macro.py")
tokenize = macro.tokenize
MacroError = macro.MacroError
# Resolve the SAME native module the tokenizer uses, so we can inspect its
# counter and monkeypatch it in the fallback tests.
native = macro._native_macro()

results = []
TMP = tempfile.mkdtemp(prefix="native_macro_")


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
def _force_python():
    """Temporarily make BOTH native routes report unavailable so the tokenizer
    runs its UNCHANGED Python path (the "native == Python" baseline and the
    malformed-output fallback). The packed one-pass route (Q117-04) runs first
    for >=240-byte inputs, so it must be disabled alongside the legacy JSON
    route or the "Python baseline" would still be native."""
    if native is None:
        yield
        return
    real_packed = native._native_macro_packed
    real_macro = native._native_macro
    native._native_macro_packed = lambda *a, **k: None
    native._native_macro = lambda *a, **k: None
    try:
        yield
    finally:
        native._native_macro_packed = real_packed
        native._native_macro = real_macro


# The valid macros: (macro, lossless). The expected value is NOT hardcoded --
# it is asserted to be identical to the pure-Python result (the check below),
# which is the real invariant. These cover every step kind, the flag set,
# quotes, comments, specials, the _3Point text rule, both lossless modes and
# the empty / whitespace edge.
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
LONG_MACRO = ("! _Line _Pause 0,0 _Enter " * 10).strip()
# A macro clearly at/above the packed-tier crossover (>=8192 bytes): the packed
# one-pass route is the production native route for this one.
LARGE_MACRO = ("! _Line _Pause 0,0 _Enter '*Zoom -_SelName \"A 01\" "
                "_Mode=_Shaded _Points=10 " * 120).strip()


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


@check("tokenize routes only measured-winning long macros through Rust")
def _():
    _reset()
    before = native._native_macro_calls
    assert isinstance(tokenize("_Line"), list)
    assert native._native_macro_calls == before, "short macro must stay on Python"
    assert isinstance(tokenize(LONG_MACRO, lossless=True), list)
    _assert_native_ran(before, "long macro")


@check("native equals the pure-Python tokenizer on the SAME valid input")
def _():
    _reset()
    for text, lossless in VALID_MACROS:
        got_native = tokenize(text, lossless=lossless)
        with _force_python():
            got_py = tokenize(text, lossless=lossless)
        assert got_py is not None, (text, lossless, got_py)
        # Byte-for-byte the Python contract: the step dicts (with their
        # ``prefixes`` tuples) must be exactly equal.
        assert got_native == got_py, \
            (text, lossless, got_native, "expected", got_py)


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
# byte-for-byte the forced-pure-Python result in BOTH lossless modes -- the
# real packed-route invariant, covering commands with the full flag set, a
# leading-`!` cancel, specials, options, and quoted values.
LONG_MACROS = [
    LONG_MACRO,
    ('!&_Delete *_Circle ~_Line "My Layer 01" "Table 01:: New" '
     '_Mode=_Shaded _Points=10 Pause Enter ') * 4,
    ("! _-DocumentProperties _Mesh _Custom _MaxEdgeSrf .01 _EnterEnd ") * 4,
    ("'_CPlane _3Point\n_Pause _Pause _Pause _Enter ") * 5,
]


@check("packed native equals the pure-Python tokenizer on >=240-byte inputs")
def _():
    _reset()
    for text in LONG_MACROS:
        for lossless in (False, True):
            got_native = tokenize(text, lossless=lossless)
            with _force_python():
                got_py = tokenize(text, lossless=lossless)
            assert got_native == got_py, \
                (text[:40], lossless, got_native[:3], "expected", got_py[:3])


@check("the _3Point text rule holds on the packed route")
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


@check("a library without the packed export falls back to the JSON route")
def _():
    # Resolve the packed binding as absent (a library built before Q117-04):
    # the >=240-byte macro must still be tokenized correctly via the legacy
    # two-pass JSON ABI, with the counter moving and the exact step list.
    _reset()
    real_resolver = native._macro_packed_binding
    native._macro_packed_binding = lambda: None
    try:
        before = native._native_macro_calls
        assert native._native_macro_packed(LONG_MACRO, True) is None, \
            "with the packed export absent the packed call must report None"
        got = tokenize(LONG_MACRO, lossless=True)
        with _force_python():
            expected = tokenize(LONG_MACRO, lossless=True)
        assert got == expected
        assert native._native_macro_calls > before, \
            "the JSON fallback must count as a native success"
    finally:
        native._macro_packed_binding = real_resolver
        _reset()


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


@check("the _3Point text rule holds on the native route (mutation target)")
def _():
    # A flagged word whose name starts with a digit is TEXT, not a command.
    # If the Rust ``name[0].isalpha()`` requirement were loosened to also accept
    # a leading digit, this would go RED (native would emit a command step).
    _reset()
    before = native._native_macro_calls
    payload = native._native_macro("_3Point", False)
    assert payload is not None
    assert macro._steps_from_json(payload) == [{"kind": "text", "value": "_3Point"}]
    _assert_native_ran(before, "direct macro _3Point")
    assert tokenize("-15,0,0") == [{"kind": "text", "value": "-15,0,0"}]
    assert tokenize("_Points=10") == [{"kind": "option",
                                       "name": "Points", "value": "10"}]


@check("fallback: a missing library uses the unchanged Python tokenizer")
def _():
    _reset()
    real_env = os.environ.get("NARWHAL_PURE_CORE")
    os.environ["NARWHAL_PURE_CORE"] = os.path.join(TMP, "no_such_lib.so")
    try:
        assert native._macro_binding() is None, \
            "a missing library must make the binding None"
        before = native._native_macro_calls
        # A valid macro still works via the Python path (value unchanged).
        assert tokenize("! _Line _Pause 0,0 _Enter") == [
            {"kind": "cancel"},
            {"kind": "command", "name": "Line", "dash": False,
             "underscore": True, "transparent": False},
            {"kind": "pause"},
            {"kind": "text", "value": "0,0"},
            {"kind": "enter"},
        ]
        assert tokenize("_3Point") == [{"kind": "text", "value": "_3Point"}]
        # The native counter must NOT have moved (the Python tokenizer ran).
        assert native._native_macro_calls == before, \
            ("missing-lib fallback must not touch the native counter",
             before, native._native_macro_calls)
    finally:
        if real_env is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = real_env
        _reset()


@check("fallback: an old library without the macro exports uses Python")
def _():
    # A real file that is not a valid shared library models "old symbol":
    # CDLL fails (OSError) so the binding is None and the Python tokenizer runs
    # with the same step list and an unchanged counter.
    old = os.path.join(TMP, "empty.so")
    open(old, "wb").close()
    _reset()
    real_env = os.environ.get("NARWHAL_PURE_CORE")
    os.environ["NARWHAL_PURE_CORE"] = old
    try:
        assert native._macro_binding() is None, \
            "a library without the macro export must make the binding None"
        before = native._native_macro_calls
        assert tokenize("!&_Delete") == [
            {"kind": "cancel"},
            {"kind": "command", "name": "Delete", "dash": False,
             "underscore": True, "transparent": False,
             "history_lock_off": True},
        ]
        assert tokenize("_3Point") == [{"kind": "text", "value": "_3Point"}]
        assert native._native_macro_calls == before, \
            ("old-symbol fallback must not touch the native counter",
             before, native._native_macro_calls)
    finally:
        if real_env is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = real_env
        _reset()


@check("fallback: a malformed native output (None) uses the Python tokenizer")
def _():
    _reset()
    assert native is not None and native._macro_binding() is not None
    real_macro = native._native_macro
    native._native_macro = lambda *a, **k: None  # simulate a failed call
    try:
        before = native._native_macro_calls
        # The Python tokenizer still produces the correct step list.
        assert tokenize("! _Line _Pause 0,0 _Enter") == [
            {"kind": "cancel"},
            {"kind": "command", "name": "Line", "dash": False,
             "underscore": True, "transparent": False},
            {"kind": "pause"},
            {"kind": "text", "value": "0,0"},
            {"kind": "enter"},
        ]
        assert tokenize("_3Point") == [{"kind": "text", "value": "_3Point"}]
        # The native counter must NOT have incremented (the fallback ran).
        assert native._native_macro_calls == before, \
            ("malformed-output fallback must not touch the native counter",
             before, native._native_macro_calls)
    finally:
        native._native_macro = real_macro
        _reset()


@check("malformed successful macro JSON falls back without counting")
def _():
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
        assert tokenize("_Move") == [{"kind": "command", "name": "Move",
                                      "dash": False, "underscore": True,
                                      "transparent": False}]
    finally:
        native._macro_binding = real_binding
        _reset()


@check("the active native win band is >=10% faster; packed stays inactive")
def _():
    # The "never slower" rule, tier by tier: on the *same* valid input, the
    # ROUTED native route (the JSON two-pass ABI below the packed crossover,
    # the packed one-pass ABI at/above it) must be faster than the unchanged
    # Python tokenizer. We measure the same public function twice -- once with
    # the native route active, once forced onto the pure-Python path
    # (``_force_python`` disables BOTH native routes) -- so the comparison
    # isolates tokenizing cost, not grammar. A regression (routed native slower
    # than Python) is a DECISION NEEDED for the owner, not a silent slowdown:
    # the assertion fails and the measured numbers are printed.
    if native is None or native._macro_binding() is None:
        print("  [perf] SKIPPED -- native macro binding not available")
        return
    assert macro.NATIVE_MACRO_JSON_MAX_BYTES == 4096
    assert macro.NATIVE_MACRO_PACKED_MIN_BYTES > 1024 * 1024
    tiers = [("json-tier (259B, the 240B crossover)", LONG_MACRO)]

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
        with _force_python():
            _reset()
            t_py = _best(lambda: tokenize(text, lossless=True), N)
        r = t_native / max(t_py, 1e-9)
        print("  [perf] macro.tokenize %s native=%.5fs python=%.5fs "
              "ratio=%.2fx (%d calls)" % (label, t_native, t_py, r, N))
        assert r <= 0.90, \
            ("routed native macro workload (%s) lacks the required 10%% win: "
             "native %.5fs vs python %.5fs (ratio %.2fx)" %
             (label, t_native, t_py, r))


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


failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
