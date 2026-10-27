"""OURS: application preference persistence, with no UI/runtime imports.

Python is intentional here: this is small JSON/disk orchestration for UI state,
not geometry or a performance-sensitive computation. Import through the running
app package (whose __init__ starts the UI); headless clients can use a lightweight
package loader, as spike/test_preferences.py does.

Public API:
* settings_path() resolves NARWHAL3D_SETTINGS_FILE first, then the OS config path.
* get(key) returns a defensive copy of a validated cached value; unknown -> KeyError.
* load() refreshes from disk and returns a defensive snapshot; malformed -> defaults.
* reset_cache() invalidates memory only. A path change also invalidates the cache.
* update(mapping) validates before writing, fresh-merges ONLY supplied keys, and
  returns the new validated snapshot. Unknown keys -> KeyError, invalid values ->
  ValueError, I/O errors propagate; a failed write never changes the cache.

Nested ai_config/print_settings dictionaries replace that preference, not patch it;
unknown nested fields are dropped, including raw credentials. Other top-level owners'
fields are preserved verbatim. Read/merge/replace is not an interprocess transaction:
sequential stale clients merge safely, but simultaneous writers need caller locking.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math
import re

from . import ai_assistant, toolbar_data, user_settings
from .user_settings import settings_path

# MEASURED app defaults: design.DEFAULT_THEME, UI toggles and DialogState. Keep
# these independent of design/UI to avoid a startup import cycle.
DEFAULTS = {
    "theme": "light",
    "show_unavailable": True,
    "toolbar": "Standard",
    "inspector": "Layers",
    "grid_snap": False,
    "ortho": False,
    "gumball": True,
    "gumball_alignment": "CPlane",
    "viewport_tabs": True,
    "osnap_modes": ["End", "Point", "Mid", "Cen", "Quad"],
    "osnap_disabled": False,
    "osnap_project": False,
    "ai_config": {},
    "ai_include_selection": True,
    "print_settings": {},
}
_ENUMS = {
    "theme": ("light", "dark"),
    "toolbar": tuple(tab["name"] for tab in toolbar_data.TABS),
    "inspector": ("Layers", "Properties", "Display", "Bug Report", "AI Assistant"),
    # Object alignment is declared but not implemented by gumball.ALIGNMENTS.
    "gumball_alignment": ("CPlane", "World", "View"),
}
_OSNAP_MODES = ("Cen", "End", "Int", "Knot", "Mid", "Near", "Perp", "Point", "Quad", "Tan", "Vertex")
_AI_FIELDS = {
    "provider_id": str, "model": str, "endpoint": str, "credential_env": str,
    "allow_insecure_remote": bool,
}
# MEASURED printdialog.DialogState / choices(); no printdialog import because
# print2d imports rhino3dm. Transient views, dropdowns, focus and messages stay out.
_PRINT_ENUMS = {
    "destination": (0, 1, 2, 3),
    "cdr_version": ("15", "17"),
    "size": ("Letter", "Legal", "Tabloid", "A4", "A3", "A2", "A1", "A0", "Custom"),
    "orientation": ("Landscape", "Portrait"),
    "color": ("PrintColor", "DisplayColor", "BlackWhite"),
    "area": ("Viewport", "Extents"),
    "scale": ("Fit", "1:1", "1:2", "1:5", "1:10", "1:20", "1:50", "1:100",
              "2:1", "5:1", "10:1"),
    "width": ("Hairline", "0.13", "0.18", "0.25", "0.30", "0.35", "0.50", "0.60",
              "0.70", "0.80", "0.90", "1.00", "1.20", "1.40", "2.00"),
}
_PRINT_FIELDS = frozenset(_PRINT_ENUMS) | {"view", "selected_only", "custom_w", "custom_h"}
_cache = None
_cache_path = None


def _text(value, limit):
    return (type(value) is str and len(value) <= limit
            and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value))


def _ai_config(value):
    clean = {key: val for key, val in value.items() if key in _AI_FIELDS}
    if any(type(val) is not _AI_FIELDS[key] for key, val in clean.items()):
        raise ValueError("invalid AI preference type")
    if "credential_env" in clean and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", clean["credential_env"]):
        raise ValueError("credential_env must name an environment variable, not a key")
    if "endpoint" in clean and (not clean["endpoint"] or not _text(clean["endpoint"], 2048)):
        raise ValueError("invalid AI endpoint")
    if "model" in clean and not _text(clean["model"], 200):
        raise ValueError("invalid AI model")
    config = ai_assistant.set_provider(**{"provider_id": "openai_compatible", **clean})
    if "model" in clean:
        clean["model"] = config.model
    if "endpoint" in clean:
        clean["endpoint"] = ai_assistant.validate_endpoint(config.endpoint, config.allow_insecure_remote)
    return clean


def _print_settings(value):
    clean = {key: val for key, val in value.items() if key in _PRINT_FIELDS}
    for key, val in clean.items():
        if key in _PRINT_ENUMS:
            choices = _PRINT_ENUMS[key]
            valid = type(val) is type(choices[0]) and val in choices
        elif key in ("custom_w", "custom_h"):
            valid = type(val) in (int, float) and 0 < val <= 5000 and math.isfinite(val)
        elif key == "selected_only":
            valid = type(val) is bool
        else:
            # View names are document-dependent, not a fixed global enum.
            valid = _text(val, 256)
        if not valid:
            raise ValueError(f"invalid print preference: {key}")
    return clean


def _validate(key, value):
    default = DEFAULTS[key]
    if type(value) is not type(default):
        raise ValueError(f"invalid preference type: {key}")
    if key in _ENUMS and value not in _ENUMS[key]:
        raise ValueError(f"invalid preference choice: {key}")
    if key == "osnap_modes":
        if any(type(mode) is not str or mode not in _OSNAP_MODES for mode in value):
            raise ValueError("invalid osnap mode")
        return list(dict.fromkeys(value))
    if key == "ai_config":
        return _ai_config(value)
    if key == "print_settings":
        return _print_settings(value)
    return deepcopy(value)


def _snapshot(data):
    values = {}
    for key, default in DEFAULTS.items():
        try:
            values[key] = _validate(key, data.get(key, default))
        except (ValueError, TypeError):
            values[key] = deepcopy(default)
    return values


def reset_cache():
    """Forget cached reads without changing the file or the environment."""
    global _cache, _cache_path
    _cache = _cache_path = None


def load():
    """Refresh validated preferences from the current path; return a copy."""
    global _cache, _cache_path
    path = settings_path().absolute()
    values = _snapshot(user_settings._read_json(path))
    _cache, _cache_path = values, path
    return deepcopy(values)


def get(key):
    """Return a validated current value, or its default for damaged disk data."""
    if key not in DEFAULTS:
        raise KeyError(key)
    if _cache is None or _cache_path != settings_path().absolute():
        load()
    return deepcopy(_cache[key])


def update(mapping):
    """Validate, fresh-merge and atomically save only the supplied known keys."""
    global _cache, _cache_path
    if not isinstance(mapping, Mapping):
        raise TypeError("preferences.update expects a mapping")
    for key in mapping:
        if key not in DEFAULTS:
            raise KeyError(key)
    changes = {key: _validate(key, value) for key, value in mapping.items()}
    if not changes:
        return load()
    path = settings_path().absolute()
    data = user_settings._read_json(path, for_write=True)
    data.update(changes)
    values = _snapshot(data)
    user_settings._atomic_write(path, data)
    _cache, _cache_path = values, path
    return deepcopy(values)
