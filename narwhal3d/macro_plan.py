"""Pure, validated execution plans for the reference CAD application command macros.

This module deliberately does not execute commands or touch Blender/UI state.  It
turns :func:`macro.tokenize`'s lossless steps into explicit action dictionaries.
The apostrophe is a safety boundary: the reference CAD application documents it as nestable only for
view/CPlane/object-snap style commands, never for geometry creation.  A
transparent command outside the measured allow-list is refused rather than
silently run as if it were safe.
"""
from __future__ import annotations

import json

try:
    from .macro import MacroError, tokenize
except ImportError:  # loaded directly by the headless file-path test harness
    from macro import MacroError, tokenize

__all__ = [
    "MacroPlanError", "NonNestableCommandError", "NESTABLE_COMMANDS",
    "plan", "plan_macro", "build_plan",
]


class MacroPlanError(ValueError):
    """A macro token stream cannot be represented as a safe execution plan."""


class NonNestableCommandError(MacroPlanError):
    """A transparent command is not one the reference CAD application documents as nestable."""


# MEASURED from information/cad_scripting, "Naming conventions in the reference CAD application":
# view and construction-plane manipulation and object snaps are nestable;
# geometry creation is not.  Keep this explicit and conservative: unknown
# commands must not accidentally become transparent geometry commands.
NESTABLE_COMMANDS = frozenset({
    "cplane", "namedcplane", "namedview", "namedviews", "ortho", "planar",
    "snap", "osnap", "zoom", "rotateview", "pan", "tilt", "walkabout",
    "selcrossing", "selwindow", "selpolysrf", "curvaturegraph",
    "curvaturegraphoff", "hidep", "showp",
})


def _prefixes(step):
    """Return the exact prefix sequence, including prefixes with no flag field."""
    prefixes = step.get("prefixes")
    if prefixes is None:
        # This fallback also makes build_plan useful with hand-built legacy
        # tokenizer steps, while normal planner input is lossless.
        prefixes = tuple(p for p, key in (
            ("!", "cancel"), ("'", "transparent"), ("_", "underscore"),
            ("-", "dash"), ("&", "history_lock_off"), ("*", "repeat"),
            ("~", "suppress_options"), ("#", "history_on"),
            ("%", "history_off"),
        ) if (key == "cancel" and False) or step.get(key, False))
    return tuple(prefixes)


def _command_action(step):
    name = step.get("name")
    if not isinstance(name, str) or not name or not name[0].isalpha():
        raise MacroPlanError("command action has an invalid name")
    prefixes = _prefixes(step)
    if step.get("transparent") and name.casefold() not in NESTABLE_COMMANDS:
        raise NonNestableCommandError(
            "transparent command %r is not a measured nestable command" % name)
    action = {
        "action": "command",
        "name": name,
        "prefixes": prefixes,
        "dash": bool(step.get("dash", False)),
        "underscore": bool(step.get("underscore", False)),
        "transparent": bool(step.get("transparent", False)),
    }
    # Preserve every documented flag, not merely the three contract booleans.
    for key in ("history_lock_off", "repeat", "suppress_options",
                "history_on", "history_off"):
        if key in step:
            action[key] = bool(step[key])
    return action


def build_plan(tokens):
    """Validate lossless tokenizer steps and return explicit action dicts.

    The returned list is detached from the input.  It is data only: no command
    lookup, document mutation, execution, or UI wiring occurs here.
    """
    if not isinstance(tokens, (list, tuple)):
        raise MacroPlanError("tokens must be a list or tuple")
    actions = []
    for step in tokens:
        if not isinstance(step, dict) or not isinstance(step.get("kind"), str):
            raise MacroPlanError("each token must be a dict with a kind")
        kind = step["kind"]
        if kind == "cancel":
            actions.append({"action": "cancel", "prefixes": ("!",)})
        elif kind == "command":
            if "prefixes" not in step:
                raise MacroPlanError("command tokens must retain their prefixes")
            if "!" in step["prefixes"]:
                # ! is both a prefix and an explicit cancel action in the reference CAD application.
                # The tokenizer has already emitted this cancel; do not lose it.
                pass
            actions.append(_command_action(step))
        elif kind == "pause":
            special = step.get("special", "Pause")
            if special.casefold() not in {"pause", "multipause"}:
                raise MacroPlanError("invalid pause spelling")
            actions.append({"action": "pause", "special": special,
                            "prefixes": tuple(step.get("prefixes", ()))})
        elif kind == "enter":
            special = step.get("special", "Enter")
            if special.casefold() not in {"enter", "enterend"}:
                raise MacroPlanError("invalid enter spelling")
            actions.append({"action": "enter", "special": special,
                            "prefixes": tuple(step.get("prefixes", ()))})
        elif kind == "text":
            if not isinstance(step.get("value"), str):
                raise MacroPlanError("text action has a non-string value")
            actions.append({"action": "text", "value": step["value"]})
        elif kind == "option":
            if not isinstance(step.get("name"), str) or not step["name"]:
                raise MacroPlanError("option action has an invalid name")
            if not isinstance(step.get("value"), str):
                raise MacroPlanError("option action has a non-string value")
            actions.append({"action": "option", "name": step["name"],
                            "value": step["value"],
                            "prefixes": tuple(step.get("prefixes", ()))})
        else:
            raise MacroPlanError("unsupported token kind %r" % kind)
    return actions


def _native_macro_plan():
    """Lazily resolve the native pure-core adapter, or ``None``.

    Identical to ``macro._native_macro()``: returns the ``native_pure``
    module when it can be imported (production, where this file is
    ``narwhal3d.macro_plan``) or loaded from this directory (spike tests load
    this module by file path, so ``narwhal3d`` is not an importable package
    there). ``None`` means "use the unchanged Python planner" -- the native
    library is absent, it predates the planner export, or the adapter cannot
    be imported.
    """
    try:
        from . import native_pure as _mod  # production package context
        return _mod
    except ImportError:
        pass
    try:
        import importlib.util as _ilu
        import os as _os
        import sys as _sys
        _canon = "narwhal3d_native_pure"
        if _canon in _sys.modules:
            return _sys.modules[_canon]
        _path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                              "native_pure.py")
        if not _os.path.isfile(_path):
            return None
        _spec = _ilu.spec_from_file_location(_canon, _path)
        _mod = _ilu.module_from_spec(_spec)
        _sys.modules[_canon] = _mod
        _spec.loader.exec_module(_mod)
        return _mod
    except Exception:  # noqa: BLE001 -- any failure degrades to the fallback
        return None


def _actions_from_json(payload):
    """Convert the native JSON action list into the Python action contract.

    ``json.loads`` returns the ``prefixes`` as a *list*; the Python reference
    emits a *tuple*, so each action's ``prefixes`` is converted back to a
    tuple so the result is byte-for-byte the Python output. Everything else
    (action kinds, names, values, booleans, flag keys) round-trips exactly
    through JSON.
    """
    actions = json.loads(payload)
    for action in actions:
        if isinstance(action, dict) and isinstance(action.get("prefixes"), list):
            action["prefixes"] = tuple(action["prefixes"])
    return actions


_PACKED_CANCEL = 0
_PACKED_COMMAND = 1
_PACKED_PAUSE = 2
_PACKED_ENTER = 3
_PACKED_TEXT = 4
_PACKED_OPTION = 5

_PACKED_DASH = 1 << 0
_PACKED_UNDERSCORE = 1 << 1
_PACKED_TRANSPARENT = 1 << 2
_PACKED_HISTORY_LOCK_OFF = 1 << 3
_PACKED_REPEAT = 1 << 4
_PACKED_SUPPRESS_OPTIONS = 1 << 5
_PACKED_HISTORY_ON = 1 << 6
_PACKED_HISTORY_OFF = 1 << 7


def _actions_from_packed(packed):
    """Convert a native packed action list into the Python action contract.

    ``packed`` is the ``(kinds, flags, s0, s1, s2, strings, n_actions,
    n_bytes)`` tuple from ``native_pure._native_macro_plan_packed``: per-action
    ``kind`` / flag bitmasks, three ``(offset << 32) | length`` fields per
    action, and the shared ``strings`` byte buffer they all index into. No
    JSON is involved.

    The layout mirrors the Q117-04 packed tokenizer: cancel -> s0="!";
    command -> s0=name, s1=prefixes; pause / enter -> s0=special, s1=prefixes;
    text -> s0=value; option -> s0=name, s1=value, s2=prefixes. The flag bits
    on a command are the same ``CMD_*`` bits the tokenizer uses, so the
    ``dash`` / ``underscore`` / ``transparent`` booleans and the five optional
    flag keys (present only when set) are rebuilt exactly as the Python
    reference emits them, and every ``prefixes`` is a tuple -- the result is
    byte-for-byte the Python planner output.
    """
    kinds, flags, s0, s1, s2, strings, n_actions, _n_bytes = packed
    str_buf = bytes(strings)

    def field(arr, i):
        pair = arr[i]
        if pair == 0:
            return ""
        off = pair >> 32
        ln = pair & 0xFFFFFFFF
        return str_buf[off:off + ln].decode("utf-8")

    actions = []
    for i in range(n_actions):
        kind = kinds[i]
        f = flags[i]
        if kind == _PACKED_CANCEL:
            actions.append({"action": "cancel", "prefixes": ("!",)})
        elif kind == _PACKED_COMMAND:
            action = {"action": "command", "name": field(s0, i),
                      "prefixes": tuple(field(s1, i)),
                      "dash": bool(f & _PACKED_DASH),
                      "underscore": bool(f & _PACKED_UNDERSCORE),
                      "transparent": bool(f & _PACKED_TRANSPARENT)}
            if f & _PACKED_HISTORY_LOCK_OFF:
                action["history_lock_off"] = True
            if f & _PACKED_REPEAT:
                action["repeat"] = True
            if f & _PACKED_SUPPRESS_OPTIONS:
                action["suppress_options"] = True
            if f & _PACKED_HISTORY_ON:
                action["history_on"] = True
            if f & _PACKED_HISTORY_OFF:
                action["history_off"] = True
            actions.append(action)
        elif kind == _PACKED_PAUSE:
            actions.append({"action": "pause", "special": field(s0, i),
                            "prefixes": tuple(field(s1, i))})
        elif kind == _PACKED_ENTER:
            actions.append({"action": "enter", "special": field(s0, i),
                            "prefixes": tuple(field(s1, i))})
        elif kind == _PACKED_TEXT:
            actions.append({"action": "text", "value": field(s0, i)})
        else:  # _PACKED_OPTION
            actions.append({"action": "option", "name": field(s0, i),
                            "value": field(s1, i),
                            "prefixes": tuple(field(s2, i))})
    return actions


# The production native route is the legacy two-pass JSON ABI in a measured
# win-band, and nothing else: production Rust activates only when the complete
# route is at least 10% faster.
NATIVE_MACRO_PLAN_MIN_BYTES = 192

# Q117-05 MEASURED (best-of-N, full public path incl. packing + FFI + strict
# validation + conversion, native vs the unchanged Python planner; the JSON
# route regresses sharply above its win-band, and the packed route is only at
# parity with Python -- neither is a >=10% win there):
#
#   size    json/py   packed/py   packed/json   shape
#   207B    0.79      1.04        1.32          dense   <- JSON wins
#   415B    0.74      0.95        1.29          dense   <- JSON wins
#   831B    0.79      1.00        1.26          dense   <- JSON wins
#   3327B   0.80      1.01        1.20          dense   <- JSON wins
#   4991B   0.84      ~1.0        ~1.2          dense   <- JSON still wins
#   6135B   2.22      1.01        0.46          dense   <- JSON CLIFF (regresses
#   6655B   2.36      1.00        0.44          dense        to ~2.3x); packed only
#   5759B   0.86      --          --          low-density <- JSON still wins
#
# The JSON route is a clean >=10% win up to ~5KB, then C json.loads of a large
# nested structure loses to Python (the same cliff Q117-04 saw at the tokenizer
# level). The packed route is at PARITY with Python at every measured size -- it
# is not a >=10% win, so it is NOT activated in production (the Q117-03 posture:
# added, proven byte-for-byte, ready to activate). Its threshold sits above the
# JSON ceiling so the production hook never routes to it; a test can still lower
# it to exercise the proven packed path.
NATIVE_MACRO_PLAN_MAX_BYTES = 4096
# Proven-but-inactive: above the ceiling AND above any realistic macro, so the
# production hook stays honest (Python above the ceiling; JSON in the win-band).
NATIVE_MACRO_PLAN_PACKED_MIN_BYTES = 2_000_000


def plan_macro(macro):
    """Tokenize and validate a macro into explicit, non-executing actions.

    The measured Q117-05 win band is 192..4096 UTF-8 bytes, where the legacy
    JSON native route is at least 10% faster. The packed route is proven
    equivalent but remains inactive because it measured only at parity; above
    the win band and below 192 bytes the unchanged Python planner runs.
    Rejections retain the unchanged Python errors.
    """
    nbytes = len(macro.encode("utf-8")) if isinstance(macro, str) else None
    if nbytes is not None and NATIVE_MACRO_PLAN_MIN_BYTES <= nbytes <= NATIVE_MACRO_PLAN_MAX_BYTES:
        _nm = _native_macro_plan()
        if _nm is not None:
            # Q117-05: the packed one-pass route (no JSON round-trip, no
            # re-tokenization) is the production native route at or above the
            # measured packed crossover. A successful packed result is the
            # exact Python action contract.
            if nbytes >= NATIVE_MACRO_PLAN_PACKED_MIN_BYTES \
                    and _nm._macro_plan_packed_native_active(macro):
                packed = _nm._native_macro_plan_packed(macro)
                if packed is not None:
                    return _actions_from_packed(packed)
            # The packed tier is proven but at parity (not a >=10% win), so in
            # production its threshold sits above the ceiling and this branch is
            # never taken -- the JSON route below is the only active native path.
            # The packed code stays fully wired so a test (or a future measured
            # >=10% win) can activate it by lowering the packed threshold.
            payload = _nm._native_macro_plan(macro)
            if payload is not None:
                return _actions_from_json(payload)
    try:
        tokens = tokenize(macro, lossless=True)
    except MacroError as exc:
        raise MacroPlanError(str(exc)) from exc
    return build_plan(tokens)


# Short public spelling for callers that already have a macro string.
plan = plan_macro
