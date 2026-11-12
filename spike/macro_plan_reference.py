"""Independent test-only planner oracle; not application code."""
import importlib.util
from pathlib import Path
_s = importlib.util.spec_from_file_location("planner_reference_tokens", Path(__file__).with_name("macro_reference.py"))
_m = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_m)
MacroError, tokenize = _m.MacroError, _m.tokenize

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
    try:
        return build_plan(tokenize(macro, lossless=True))
    except MacroError as error:
        raise MacroPlanError(str(error)) from error
