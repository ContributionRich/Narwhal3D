"""Pure, validated execution plans for the reference CAD application command macros.

This module deliberately does not execute commands or touch Blender/UI state.
It turns :func:`macro.tokenize`'s lossless steps into explicit action
dictionaries. The apostrophe is a safety boundary: the reference CAD
application documents it as nestable only for view/CPlane/object-snap style
commands, never for geometry creation. A transparent command outside the
measured allow-list is refused rather than silently run as if it were safe.

Native-only string planning: the ``plan_macro`` string path is
native-only at every length. The checked planner export owns tokenization,
validation, and the first semantic error in token order; the host maps the
stable refusal codes to the unchanged public errors. There is no Python
tokenizer/planner replay on the string path. ``build_plan`` remains the
public hand-built-token API (a different contract that is not yet
native-equivalent) and is preserved unchanged.
"""
from __future__ import annotations

import json

import importlib.util
import sys
from pathlib import Path

__all__ = [
    "MacroPlanError", "NonNestableCommandError", "NESTABLE_COMMANDS",
    "plan", "plan_macro", "build_plan",
]

# The native planner refuses input above 1 MiB (Rust
# ``MACRO_MAX_INPUT_BYTES``); the guard here gives the fast-path class.
_INPUT_MAX_BYTES = 1 << 20


class MacroPlanError(ValueError):
    """A macro token stream cannot be represented as a safe execution plan."""


class NonNestableCommandError(MacroPlanError):
    """A transparent command is not one the reference CAD application documents as nestable."""


# MEASURED from information/cad_scripting, "Naming conventions in the reference CAD application":
# view and construction-plane manipulation and object snaps are nestable;
# geometry creation is not.  Keep this explicit and conservative: unknown
# commands must not accidentally become transparent geometry commands.
# (Kept for API compatibility; the native planner owns the allow-list now.)
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

    The returned list is detached from the input. It is data only: no command
    lookup, document mutation, execution, or UI wiring occurs here.

    This is the public hand-built-token API. It is preserved unchanged; the
    ``plan_macro`` string path is native-only and does not replay it.
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


def _native_macro_plan_adapter():
    """Resolve the checked planner adapter (``native_macro_plan``).

    Package context imports the sibling module; the file-path test harness
    (which loads this candidate by path from ``tmp/``) falls back to the
    package directory. Unlike the legacy route there is no ``None``: a
    missing adapter is an explicit ``RuntimeError`` at the call site.
    """
    if __package__:
        from . import native_macro_plan
        return native_macro_plan
    name = "narwhal3d_native_macro_plan"
    if name in sys.modules:
        return sys.modules[name]
    here = Path(__file__).resolve()
    candidates = (
        here.with_name("native_macro_plan.py"),
        here.parent.parent / "narwhal3d" / "native_macro_plan.py",
    )
    for path in candidates:
        if not path.is_file():
            continue
        spec = importlib.util.spec_from_file_location(name, str(path))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
        return module
    raise RuntimeError(
        "native macro planner adapter is unavailable; no Python fallback")


def plan_macro(macro):
    """Tokenize and validate a macro into explicit, non-executing actions.

    RUST-PLAN-12: the string path is native-only at every length. The
    checked planner export (``narwhal_pure_core_macro_plan_checked``) owns
    tokenization, validation, and the FIRST semantic error in token order;
    the host maps the stable refusal codes:

    * ``-4`` -> ``NonNestableCommandError`` (the exact command name);
    * ``-5`` -> ``MacroPlanError('option action has an invalid name')``.

    A missing, stale, or refusing native library is an explicit
    ``RuntimeError``; there is no Python tokenizer/planner replay on this
    path.
    """
    if not isinstance(macro, str):
        raise MacroPlanError("a macro is a string; got %s" % type(macro).__name__)
    adapter = _native_macro_plan_adapter()
    try:
        actions = adapter.plan_macro(macro)
    except adapter.NativeMacroPlanError as exc:
        if exc.code == -4:
            raise NonNestableCommandError(
                "transparent command %r is not a measured nestable command"
                % exc.name) from exc
        raise MacroPlanError("option action has an invalid name") from exc
    for action in actions:
        if isinstance(action, dict) and isinstance(action.get("prefixes"), list):
            action["prefixes"] = tuple(action["prefixes"])
    return actions


# Short public spelling for callers that already have a macro string.
plan = plan_macro
