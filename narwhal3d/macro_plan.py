"""Pure, validated execution plans for the reference CAD application command macros.

This module deliberately does not execute commands or touch Blender/UI state.  It
turns :func:`macro.tokenize`'s lossless steps into explicit action dictionaries.
The apostrophe is a safety boundary: the reference CAD application documents it as nestable only for
view/CPlane/object-snap style commands, never for geometry creation.  A
transparent command outside the measured allow-list is refused rather than
silently run as if it were safe.
"""
from __future__ import annotations

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


def plan_macro(macro):
    """Tokenize and validate a macro into explicit, non-executing actions."""
    try:
        tokens = tokenize(macro, lossless=True)
    except MacroError as exc:
        raise MacroPlanError(str(exc)) from exc
    return build_plan(tokens)


# Short public spelling for callers that already have a macro string.
plan = plan_macro
