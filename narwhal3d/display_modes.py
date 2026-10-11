"""Display-mode model for SetDisplayMode.

MEASURED (options/view_displaymode_options.htm, SetDisplayMode):
  "Command-line options: Viewport Active / All, Mode (a display mode name)."
  "Shortcuts: Ctrl (CMD) + Alt + G Ghosted, R Rendered, S Shaded, W Wireframe, X XRay."

**Wireframe, Shaded, and Rendered** are implemented; Ghosted and XRay are listed
but not built (``implemented=False``).  Raytraced, Technical, Artistic, Pen,
Monochrome and Arctic have no shortcut in the measured list and are out of
scope for this model.

``blender_shading`` maps to the Blender viewport shading type
(WIREFRAME / SOLID / MATERIAL).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Mode:
    name: str
    shortcut_letter: str
    implemented: bool
    blender_shading: str | None


MODES: tuple[Mode, ...] = (
    Mode("Wireframe", "W", True, "WIREFRAME"),
    Mode("Shaded", "S", True, "SOLID"),
    Mode("Rendered", "R", True, "MATERIAL"),
    Mode("Ghosted", "G", False, None),
    Mode("XRay", "X", False, None),
)


def mode_by_name(text):
    """Find a mode by name.

    Case-insensitive, unique-prefix match.  Leading ``_`` / ``-`` characters
    are stripped (Rhino macro syntax: ``-_SetDisplayMode Mode=_Shaded``).

    Returns ``(Mode, None)`` on success, ``(None, error_message)`` on failure.
    """
    if not isinstance(text, str):
        return None, f"mode name must be a string, got {type(text).__name__}"
    cleaned = text.strip().lstrip("_-")
    if not cleaned:
        return None, "empty mode name"
    key = cleaned.lower()
    for m in MODES:
        if m.name.lower() == key:
            return m, None
    matches = [m for m in MODES if m.name.lower().startswith(key)]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        names = ", ".join(m.name for m in matches)
        return None, f"ambiguous prefix {key!r} matches {names}"
    return None, f"unknown display mode {cleaned!r}"


def mode_by_shortcut(letter):
    """Find a mode by its shortcut letter (case-insensitive).

    Returns the ``Mode`` or ``None`` if the letter is not in the measured list.
    """
    if not isinstance(letter, str) or len(letter) != 1:
        return None
    key = letter.lower()
    for m in MODES:
        if m.shortcut_letter.lower() == key:
            return m
    return None


def apply(current, pane_key, mode_name, scope="Active"):
    """Apply a display mode to the active pane (``scope="Active"``) or to
    every pane in ``current`` (``scope="All"``).

    ``current`` is a dict ``{pane_key: mode_name_string}`` and is never
    mutated.  Returns ``(new_dict, None)`` on success, ``(current, error)``
    on failure.  An unbuilt mode is FOUND but refused by name ("not
    implemented yet"), and an unknown scope is refused rather than guessed.
    """
    if not isinstance(current, dict):
        return current, "current must be a dict of pane_key -> mode name"
    scope_key = scope.strip().lower() if isinstance(scope, str) else None
    if scope_key not in ("active", "all"):
        return current, f"unknown scope {scope!r} -- use 'Active' or 'All'"
    mode, err = mode_by_name(mode_name)
    if err is not None:
        return current, err
    if not mode.implemented:
        return current, f"the {mode.name} display mode is not implemented yet"
    new = dict(current)
    if scope_key == "all":
        for k in new:
            new[k] = mode.name
    else:
        new[pane_key] = mode.name
    return new, None
