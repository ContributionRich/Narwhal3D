"""AutoCAD Color Index (ACI) <-> RGB, for DXF import/export.

Only the NINE STANDARD colours are built -- these are the ones every DXF
reader agrees on:

    1  red        (255,  0,   0)
    2  yellow     (255, 255,  0)
    3  green      (0,   255,  0)
    4  cyan       (0,   255, 255)
    5  blue       (0,   0,   255)
    6  magenta    (255, 0,   255)
    7  white/black (255,255, 255)  # drawn black on a light background
    8  dark grey  (128, 128, 128)
    9  light grey (192, 192, 192)

Indices 10-255 are NOT built: the published tables we could find disagree on
them and we have no authoritative copy on disk -- so ``aci_to_rgb(i)`` for
10-255 returns ``(None, "ACI 10-255 not implemented yet")`` rather than a
guessed colour.  0 (ByBlock) and 256 (ByLayer) are not concrete colours and
are refused by name.

``rgb_to_aci(rgb)`` always returns one of 1-9 (never 0 or 256): an exact
match gives that index with ``exact=True``; anything else gives the NEAREST
of 1-9 by Euclidean RGB distance with ``exact=False``, so the caller can say
the colour was approximated.
"""
from __future__ import annotations

# The published tables agree on these nine standard ACI colours; the
# white/black dual rendering of index 7 is stored as white.
_STANDARD = {
    1: (255, 0, 0),
    2: (255, 255, 0),
    3: (0, 255, 0),
    4: (0, 255, 255),
    5: (0, 0, 255),
    6: (255, 0, 255),
    7: (255, 255, 255),
    8: (128, 128, 128),
    9: (192, 192, 192),
}

# Exact nine-color lookup is cheaper than an FFI call (measured); only the
# nearest-color search crosses into Rust.
_EXACT_STANDARD = {rgb: index for index, rgb in _STANDARD.items()}


def aci_to_rgb(i):
    """Return ``(rgb, error)`` for an ACI index.

    * 1-9   -> ``( (r, g, b), None )`` (the standard colour).
    * 10-255-> ``( None, "ACI 10-255 not implemented yet" )``.
    * 0     -> ``( None, "ACI 0 is ByBlock, not a concrete colour" )``.
    * 256   -> ``( None, "ACI 256 is ByLayer, not a concrete colour" )``.
    * other -> ``( None, error )`` (out of range or not an integer).
    """
    if isinstance(i, bool) or not isinstance(i, int):
        return None, f"ACI index must be an integer, got {type(i).__name__}"
    if 1 <= i <= 9:
        return _STANDARD[i], None
    if i == 0:
        return None, "ACI 0 is ByBlock, not a concrete colour"
    if i == 256:
        return None, "ACI 256 is ByLayer, not a concrete colour"
    if 10 <= i <= 255:
        return None, "ACI 10-255 not implemented yet"
    return None, f"ACI index out of range (0-256): {i}"


def _validate_rgb(rgb) -> tuple[float, float, float]:
    if isinstance(rgb, str) or not isinstance(rgb, (tuple, list)):
        raise ValueError(f"rgb must be a 3-tuple, got {type(rgb).__name__}")
    if len(rgb) != 3:
        raise ValueError(f"rgb must have exactly 3 components, got {len(rgb)}")
    out = []
    for c in rgb:
        if isinstance(c, bool) or not isinstance(c, (int, float)):
            raise ValueError(f"rgb components must be numbers, got {type(c).__name__}")
        if not 0 <= c <= 255:
            raise ValueError(f"rgb component out of range (0-255): {c}")
        out.append(float(c))
    return tuple(out)  # type: ignore[return-value]


def rgb_to_aci(rgb):
    """Return ``(index, exact)`` for an RGB colour.

    An exact match to one of the nine standard colours gives that index with
    ``exact=True``; anything else gives the NEAREST of 1-9 (Euclidean RGB)
    with ``exact=False``.  Never returns 0 or 256.  Raises ``ValueError`` on
    a structurally invalid ``rgb`` (wrong arity, non-numeric, out of range).
    """
    r, g, b = _validate_rgb(rgb)
    exact = _EXACT_STANDARD.get((r, g, b))
    if exact is not None:
        return exact, True
    try:
        from . import native_metadata
    except ImportError:  # direct-file use retains the complete reference implementation
        pass
    else:
        result = native_metadata.rgb_to_aci(r, g, b)
        if result is not None:
            return result
    for idx in range(1, 10):
        if (r, g, b) == tuple(float(v) for v in _STANDARD[idx]):
            return idx, True
    best_idx, best_d = 1, float("inf")
    for idx in range(1, 10):
        tr, tg, tb = (float(v) for v in _STANDARD[idx])
        d = (r - tr) ** 2 + (g - tg) ** 2 + (b - tb) ** 2
        if d < best_d:
            best_d, best_idx = d, idx
    return best_idx, False
