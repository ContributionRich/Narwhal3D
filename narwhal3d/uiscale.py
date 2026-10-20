"""One interface scale for the whole application. Pure (no bpy).

Blender and application-drawn UI use different raw scaling paths:
  * Blender scales ITS widgets by `preferences.system.ui_scale`, which is the user's
    `view.ui_scale` times the display DPI over 72 -- 1.0 x 90/72 = 1.25 at start;
  * it updates only on the next redraw after `view.ui_scale` changes;
  * `blf` text and our POST_PIXEL drawing are raw pixels -- nothing we draw followed it.
So Blender's widgets and our chrome were drawn at DIFFERENT scales, and ours at 1.0
whatever the screen.

The fix is one factor, `S`:
  * `auto_scale` picks it from the window size, with a maximized 1920x1080 desktop
    window (1920x1048 below the desktop panel) as scale 1.0; the result is snapped
    to 0.05 and clamped;
  * `blender_view_scale` is the `view.ui_scale` that makes Blender's own effective scale
    come out at exactly S, so its menus and headers match ours;
  * `apply` rescales every registered pixel CONSTANT from its base value (the value at
    import) -- so code that reads `rightpanel.ROW_H` at draw time is scaled for free, and
    pure tests that never call `apply` see the measured base values unchanged.
"""
from __future__ import annotations

#: A maximized window on a 1920x1080 desktop is scale 1.0.
REFERENCE = (1920.0, 1048.0)
MIN_SCALE = 0.6
MAX_SCALE = 2.5
STEP = 0.05


def auto_scale(window_w, window_h):
    """The interface scale for a window of this size: 1.0 at 1080p, larger above."""
    try:
        w, h = float(window_w), float(window_h)
    except (TypeError, ValueError):
        return 1.0
    if w <= 0 or h <= 0:
        return 1.0
    s = min(w / REFERENCE[0], h / REFERENCE[1])
    s = round(s / STEP) * STEP
    return round(max(MIN_SCALE, min(MAX_SCALE, s)), 4)


def blender_view_scale(target, effective_now, view_now):
    """`view.ui_scale` that makes Blender's effective scale equal `target`.

    Blender: effective = view x (DPI / 72). The DPI factor is read from the two current
    values (effective / view), so no DPI has to be known. Clamped to Blender's own range.
    """
    try:
        dpi_factor = float(effective_now) / float(view_now)
    except (TypeError, ValueError, ZeroDivisionError):
        dpi_factor = 1.0
    if dpi_factor <= 0:
        dpi_factor = 1.0
    return round(max(0.25, min(4.0, float(target) / dpi_factor)), 4)


#: module -> names of its pixel constants; filled by `register`
_REGISTRY = {}
#: (module, name) -> base value, captured the first time `apply` sees it
_BASE = {}
_applied = [1.0]


def register(module, names):
    """Declare `names` on `module` as pixel sizes that follow the interface scale."""
    _REGISTRY.setdefault(module, [])
    for n in names:
        if n not in _REGISTRY[module]:
            _REGISTRY[module].append(n)


def base(module, name):
    """A constant's value at scale 1 (its value at import)."""
    return _BASE.get((module, name), getattr(module, name))


def apply(scale):
    """Set every registered constant to base x `scale`. Integers stay integers (rounded,
    never below 1), floats stay floats. Returns the number of constants changed."""
    s = float(scale)
    changed = 0
    for module, names in _REGISTRY.items():
        for n in names:
            key = (module, n)
            if key not in _BASE:
                _BASE[key] = getattr(module, n)
            b = _BASE[key]
            v = max(1, int(round(b * s))) if isinstance(b, int) else float(b) * s
            if getattr(module, n) != v:
                setattr(module, n, v)
                changed += 1
    _applied[0] = s
    return changed


def current():
    """The scale last applied (1.0 until `apply` runs)."""
    return _applied[0]


def px(value, scale=None):
    """`value` pixels at scale 1 -> pixels now."""
    return float(value) * (current() if scale is None else float(scale))
