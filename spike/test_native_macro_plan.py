"""Native macro execution planner: direct ABI proof + route activation + fallbacks.

Complements ``spike/test_macro_plan.py`` (which pins the action contract, the
nestable allow-list, the transparent-command refusal, the Pause/Enter spelling
rules and the hand-built-token refusals on the Python path) by proving the
*native* route is the one actually running for valid input, and that every
fallback scenario runs the *unchanged* Python planner with the exact same
action list and with the native counter NOT incremented.

* the Rust ABI is present and ``_macro_plan_binding()`` is not ``None``;
* ``macro_plan.plan_macro`` activates the native route (the
  ``_native_macro_plan_calls`` counter must move) and, on the SAME input,
  returns the same action list the pure-Python planner does (the "native
  matches the Python reference" invariant, including the ``prefixes`` tuples);
* a transparent command outside the measured nestable allow-list (``'_Box``)
  is STILL refused with ``NonNestableCommandError`` naming the command, and
  the native counter is NOT incremented (the refusal is a rejection, not a
  success -- the mutation target);
* the three fallback scenarios (missing library, an old library without the
  planner export, a malformed native output) all run the *unchanged Python
  planner* with the exact same action list and with the counter NOT
  incremented;
* a performance guard: on the *same* valid (accepted) input the native
  success path must not be *materially* slower than the pure-Python planner
  (the "never slower" rule -- a regression leaves a DECISION NEEDED note).

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


macro = _load("narwhal_native_test_macro_for_plan", "macro.py")
# Expose the dependency under the fallback name used by file-path loading.
sys.modules["macro"] = macro
plan_mod = _load("narwhal_native_test_macro_plan", "macro_plan.py")
plan_macro = plan_mod.plan_macro
MacroPlanError = plan_mod.MacroPlanError
NonNestableCommandError = plan_mod.NonNestableCommandError
# Resolve the SAME native module the planner uses, so we can inspect its
# counter and monkeypatch it in the fallback tests.
native = plan_mod._native_macro_plan()

results = []
TMP = tempfile.mkdtemp(prefix="native_macro_plan_")


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
    assert native._native_macro_plan_calls > before, \
        (label, "native route did not run (%d -> %d)" %
         (before, native._native_macro_plan_calls))


@contextmanager
def _force_python():
    """Temporarily make BOTH native routes report unavailable so the planner
    runs its UNCHANGED Python path (tokenizer AND planner pure Python -- the
    "native == Python" baseline and the malformed-output fallback)."""
    if native is None:
        yield
        return
    real_plan = native._native_macro_plan
    real_tok = native._native_macro
    native._native_macro_plan = lambda *a, **k: None
    native._native_macro = lambda *a, **k: None
    try:
        yield
    finally:
        native._native_macro_plan = real_plan
        native._native_macro = real_tok


# The valid (accepted) macros: the planner's full action surface -- cancel,
# transparent nestable commands, extra flags, specials, options, text.
# Rejected macros are deliberately NOT in this set: a rejection + Python
# fallback is double work and would pollute the performance comparison.
VALID_MACROS = [
    "! '-_Zoom",
    "'_Zoom '_CPlane",
    "'_SelWindow",
    "NoEcho '_Pan 5",
    "!&_Delete",
    "*_RotateView",
    "~_Tilt 10",
    "#ShowP",
    "%HideP",
    "_-_SetDisplayMode _Mode=_Shaded",
    '_-_SetDisplayMode _Mode=_Shaded "My Mode"',
    "_Pause _MultiPause _Enter _EnterEnd",
    "'_CPlane _3Point\n_Pause _Pause _Pause _Enter",
    "; comment line\n'_Ortho\n_Zoom 100",
    "_Move !",
    "! _-SelName \"Table 01\" Enter",
    "",
]
LONG_MACRO = ("! _Line _Pause 0,0 _Enter " * 8).strip()


@check("native library is built, discovered, and exposes the planner ABI")
def _():
    assert _LIB is not None, "pure_core .so not present and build failed"
    assert native is not None, "adapter did not resolve the native module"
    binding = native._macro_plan_binding()
    assert binding is not None and len(binding) == 2 and \
        callable(binding[0]) and callable(binding[1]), \
        "the planner binding must resolve (size, encode)"
    # The counter is a real attribute (the tripwire target).
    assert hasattr(native, "_native_macro_plan_calls")


@check("plan_macro routes only measured-winning long macros through Rust")
def _():
    _reset()
    before = native._native_macro_plan_calls
    assert isinstance(plan_macro("_Line"), list)
    assert native._native_macro_plan_calls == before, "short plan must stay on Python"
    assert isinstance(plan_macro(LONG_MACRO), list)
    _assert_native_ran(before, "long macro plan")


@check("native equals the pure-Python planner on the SAME valid input")
def _():
    _reset()
    for text in VALID_MACROS:
        got_native = plan_macro(text)
        with _force_python():
            got_py = plan_macro(text)
        assert got_py is not None, (text, got_py)
        # Byte-for-byte the Python contract: the action dicts (with their
        # ``prefixes`` tuples) must be exactly equal.
        assert got_native == got_py, \
            (text, got_native, "expected", got_py)


@check("native plan prefixes are TUPLES (the Python contract)")
def _():
    _reset()
    before = native._native_macro_plan_calls
    for action in plan_macro(LONG_MACRO):
        if "prefixes" in action:
            assert isinstance(action["prefixes"], tuple), action
    _assert_native_ran(before, "macro_plan prefixes")


@check("the exact action structure holds on the native route")
def _():
    _reset()
    got = plan_macro("! '-_Zoom")
    assert got == [
        {"action": "cancel", "prefixes": ("!",)},
        {"action": "command", "name": "Zoom",
         "prefixes": ("'", "-", "_"), "dash": True,
         "underscore": True, "transparent": True},
    ], got
    got = plan_macro("_-_SetDisplayMode _Mode=_Shaded \"My Mode\"")
    assert got == [
        {"action": "command", "name": "SetDisplayMode",
         "prefixes": ("_", "-", "_"), "dash": True,
         "underscore": True, "transparent": False},
        {"action": "option", "name": "Mode", "value": "Shaded",
         "prefixes": ("_",)},
        {"action": "text", "value": "My Mode"},
    ], got
    got = plan_macro("!&_Delete")
    assert got == [
        {"action": "cancel", "prefixes": ("!",)},
        {"action": "command", "name": "Delete",
         "prefixes": ("!", "&", "_"), "dash": False, "underscore": True,
         "transparent": False, "history_lock_off": True},
    ], got


@check("a transparent geometry command is still refused (mutation target)")
def _():
    # The safety boundary: ``'_Box`` (transparent, not in the measured
    # NESTABLE_COMMANDS allow-list) must be refused with the exact
    # NonNestableCommandError naming the command, and the refusal must NOT
    # count as a native success. If the Rust ``is_nestable`` check were
    # loosened (or dropped), ``plan_macro("'_Box")`` would return an action
    # list and this check goes RED.
    _reset()
    before = native._native_macro_plan_calls
    assert native._native_macro_plan("'_Box") is None
    for bad in ("'_Box", "'_Box _Pause", "'_Circle 5,5 10"):
        try:
            plan_macro(bad)
            raise AssertionError("transparent command must be refused: %r"
                                 % bad)
        except NonNestableCommandError as exc:
            assert "Box" in str(exc) or "Circle" in str(exc), str(exc)
    assert native._native_macro_plan_calls == before, \
        ("refusals must not touch the native counter",
         before, native._native_macro_plan_calls)


@check("a non-string macro is still refused (counter unmoved)")
def _():
    _reset()
    before = native._native_macro_plan_calls
    for bad in (None, 5, ["_Move"]):
        try:
            plan_macro(bad)
            raise AssertionError("expected MacroPlanError for %r" % (bad,))
        except MacroPlanError:
            pass
    # The native route is only ever reached for a string, so none of these may
    # have counted as a native SUCCESS.
    assert native._native_macro_plan_calls == before, \
        ("non-string rejections must not touch the native counter",
         before, native._native_macro_plan_calls)


@check("fallback: a missing library uses the unchanged Python planner")
def _():
    _reset()
    real_env = os.environ.get("NARWHAL_PURE_CORE")
    os.environ["NARWHAL_PURE_CORE"] = os.path.join(TMP, "no_such_lib.so")
    try:
        assert native._macro_plan_binding() is None, \
            "a missing library must make the binding None"
        before = native._native_macro_plan_calls
        # A valid macro still works via the Python path (value unchanged).
        assert plan_macro("! '-_Zoom") == [
            {"action": "cancel", "prefixes": ("!",)},
            {"action": "command", "name": "Zoom",
             "prefixes": ("'", "-", "_"), "dash": True,
             "underscore": True, "transparent": True},
        ]
        # The refusal still happens (the Python planner raised it).
        try:
            plan_macro("'_Box")
            raise AssertionError("expected NonNestableCommandError")
        except NonNestableCommandError:
            pass
        # The native counter must NOT have moved (the Python planner ran).
        assert native._native_macro_plan_calls == before, \
            ("missing-lib fallback must not touch the native counter",
             before, native._native_macro_plan_calls)
    finally:
        if real_env is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = real_env
        _reset()


@check("fallback: an old library without the planner exports uses Python")
def _():
    # A real file that is not a valid shared library models "old symbol":
    # CDLL fails (OSError) so the binding is None and the Python planner runs
    # with the same action list and an unchanged counter.
    old = os.path.join(TMP, "empty.so")
    open(old, "wb").close()
    _reset()
    real_env = os.environ.get("NARWHAL_PURE_CORE")
    os.environ["NARWHAL_PURE_CORE"] = old
    try:
        assert native._macro_plan_binding() is None, \
            "a library without the planner export must make the binding None"
        before = native._native_macro_plan_calls
        assert plan_macro("!&_Delete") == [
            {"action": "cancel", "prefixes": ("!",)},
            {"action": "command", "name": "Delete",
             "prefixes": ("!", "&", "_"), "dash": False, "underscore": True,
             "transparent": False, "history_lock_off": True},
        ]
        try:
            plan_macro("'_Box")
            raise AssertionError("expected NonNestableCommandError")
        except NonNestableCommandError:
            pass
        assert native._native_macro_plan_calls == before, \
            ("old-symbol fallback must not touch the native counter",
             before, native._native_macro_plan_calls)
    finally:
        if real_env is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = real_env
        _reset()


@check("fallback: a malformed native output (None) uses the Python planner")
def _():
    _reset()
    assert native is not None and native._macro_plan_binding() is not None
    real_plan = native._native_macro_plan
    native._native_macro_plan = lambda *a, **k: None  # simulate a failed call
    try:
        before = native._native_macro_plan_calls
        # The Python planner still produces the correct action list.
        assert plan_macro("! '-_Zoom") == [
            {"action": "cancel", "prefixes": ("!",)},
            {"action": "command", "name": "Zoom",
             "prefixes": ("'", "-", "_"), "dash": True,
             "underscore": True, "transparent": True},
        ]
        try:
            plan_macro("'_Box")
            raise AssertionError("expected NonNestableCommandError")
        except NonNestableCommandError:
            pass
        # The native counter must NOT have incremented (the fallback ran).
        assert native._native_macro_plan_calls == before, \
            ("malformed-output fallback must not touch the native counter",
             before, native._native_macro_plan_calls)
    finally:
        native._native_macro_plan = real_plan
        _reset()


@check("malformed successful planner JSON falls back without counting")
def _():
    _reset()
    payload = (b'[{"action":"command","name":"Zoom","prefixes":["_"],'
               b'"dash":0,"underscore":true,"transparent":false}]')
    real_binding = native._macro_plan_binding

    def size_fn(_text, _length):
        return len(payload)

    def encode_fn(_text, _length, out, out_len):
        assert out_len == len(payload)
        ctypes.memmove(out, payload, len(payload))
        return 0

    native._macro_plan_binding = lambda: (size_fn, encode_fn)
    try:
        before = native._native_macro_plan_calls
        assert native._native_macro_plan("_Zoom") is None
        assert native._native_macro_plan_calls == before
        assert plan_macro("_Zoom") == [
            {"action": "command", "name": "Zoom", "prefixes": ("_",),
             "dash": False, "underscore": True, "transparent": False},
        ]
    finally:
        native._macro_plan_binding = real_binding
        _reset()


@check("packed planner ABI is present and its counter moves only on success")
def _():
    assert native is not None and hasattr(native, "_native_macro_plan_packed"), \
        "the adapter must expose the packed planner entry point"
    assert native._macro_plan_packed_binding() is not None, \
        "the packed planner export must resolve in this library"
    _reset()
    before = native._native_macro_plan_calls
    # A valid macro packs and moves the counter exactly once.
    r = native._native_macro_plan_packed("! '-_Zoom")
    assert r is not None and r[6] == 2, r
    assert native._native_macro_plan_calls == before + 1, \
        ("packed success must move the counter", before,
         native._native_macro_plan_calls)
    # A transparent non-nestable command is refused and does NOT count.
    assert native._native_macro_plan_packed("'_Box") is None
    assert native._native_macro_plan_calls == before + 1, \
        "a packed refusal must not touch the native counter"


@check("packed planner equals the pure-Python planner (byte-for-byte)")
def _():
    # Directly compare the packed native route against the pure-Python
    # reference (tokenize lossless + build_plan), including a >=192B macro so
    # the comparison is non-trivial. This is the named equivalence invariant.
    _reset()
    cases = list(VALID_MACROS) + [LONG_MACRO]
    for text in cases:
        packed = native._native_macro_plan_packed(text)
        if packed is None:
            raise AssertionError("packed route unexpectedly refused %r" % text)
        got_packed = plan_mod._actions_from_packed(packed)
        got_py = plan_mod.build_plan(plan_mod.tokenize(text, lossless=True))
        assert got_packed == got_py, (text, got_packed, "expected", got_py)


@check("packed planner refuses transparent geometry (mutation target)")
def _():
    # The same safety boundary as the JSON route: a transparent command outside
    # the measured allow-list is refused (None) and does not count as a native
    # success. If the Rust is_nestable check were loosened (allow transparent
    # Box), the packed route would return an action list and this goes RED.
    _reset()
    before = native._native_macro_plan_calls
    assert native._native_macro_plan_packed("'_Box") is None
    assert native._native_macro_plan_packed("'_Circle 5,5 10") is None
    assert native._native_macro_plan_calls == before, \
        ("packed refusals must not touch the native counter", before,
         native._native_macro_plan_calls)


@check("a missing packed export falls back to the active JSON route")
def _():
    # Model "old library without the packed export": with the (inactive in
    # production) packed threshold lowered, a None-returning packed entry point
    # must let the active JSON route serve the macro, and the JSON route must
    # actually have run (counter moved).
    _reset()
    saved = plan_mod.NATIVE_MACRO_PLAN_PACKED_MIN_BYTES
    plan_mod.NATIVE_MACRO_PLAN_PACKED_MIN_BYTES = 8  # force the packed attempt
    real_pp = native._native_macro_plan_packed
    native._native_macro_plan_packed = lambda *a, **k: None  # simulate missing
    try:
        before = native._native_macro_plan_calls
        got = plan_macro(LONG_MACRO)
        with _force_python():
            got_py = plan_macro(LONG_MACRO)
        assert got == got_py, (got, "expected", got_py)
        assert native._native_macro_plan_calls > before, \
            "the JSON route must have run when the packed export is missing"
    finally:
        native._native_macro_plan_packed = real_pp
        plan_mod.NATIVE_MACRO_PLAN_PACKED_MIN_BYTES = saved
        _reset()


@check("production posture: packed tier is proven-but-inactive above the ceiling")
def _():
    # Q117-05 DECISION (NEVER-slower order): the packed route is at parity with
    # Python (not a >=10% win), so production keeps the 192B JSON floor and a
    # measured ceiling, and the packed threshold sits above the ceiling so the
    # hook never routes to it. This pins that posture.
    assert plan_mod.NATIVE_MACRO_PLAN_MIN_BYTES == 192
    assert plan_mod.NATIVE_MACRO_PLAN_MAX_BYTES >= 4096
    assert plan_mod.NATIVE_MACRO_PLAN_PACKED_MIN_BYTES > \
        plan_mod.NATIVE_MACRO_PLAN_MAX_BYTES


@check("routed native planner workload is faster than the Python planner")
def _():
    # The "never slower" rule: on the *same* valid (accepted) input, the
    # native success path must not be materially slower than the unchanged
    # Python planner. We measure the same public function twice -- once with
    # the native route active, once forced onto the pure-Python path
    # (``_force_python``) -- so the comparison isolates plan cost, not grammar.
    #
    # A material regression (native > 2x Python) is a DECISION NEEDED for the
    # owner, not a silent slowdown: the assertion fails and the measured
    # numbers are printed so the owner can decide whether to change the ABI.
    #
    # The comparison is HONEST: only on inputs the native route *accepts*
    # (its success path), never on rejections (a rejection + Python fallback
    # is double work and would falsely look like a native slowdown).
    #
    # The task's measured baseline is 20,000 tokenize+plan calls in 704.0 ms
    # (the pure-Python path); N below matches that scale so the numbers are
    # directly comparable.
    if native is None or native._macro_plan_binding() is None:
        print("  [perf] SKIPPED -- native planner binding not available")
        return
    perf_inputs = [LONG_MACRO]
    N = 5000

    def _best(fn):
        best = float("inf")
        for _ in range(3):
            t0 = time.perf_counter()
            for _ in range(N):
                fn()
            best = min(best, time.perf_counter() - t0)
        return best

    i = [0]

    def _cycle():
        i[0] += 1
        plan_macro(perf_inputs[i[0] % len(perf_inputs)])

    _reset()
    t_native = _best(_cycle)
    with _force_python():
        _reset()
        t_py = _best(_cycle)
    r = t_native / max(t_py, 1e-9)
    print("  [perf] macro_plan.plan_macro native=%.5fs python=%.5fs "
          "ratio=%.2fx (%d calls)" % (t_native, t_py, r, N))
    assert t_native < t_py, \
        ("routed native planner workload is slower than Python: native %.5fs "
         "vs python %.5fs (ratio %.2fx)" % (t_native, t_py, r))


failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
