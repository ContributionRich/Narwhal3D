"""Read a ``printwidths.txt`` line-width file. Pure Python, no bpy.

``read_widths(path) -> (list[float], warnings)`` returns valid line widths sorted
ascending and de-duplicated, plus one warning per invalid line identified by its
one-based line number.

Blank lines and lines beginning with ``;`` or ``//`` after leading whitespace are
ignored. Every other line must contain exactly one finite number greater than zero;
invalid lines are skipped and reported. A missing file remains a caller error and
raises ``FileNotFoundError``.
"""

import math

__all__ = ["read_widths"]


def _widths_from_text(text):
    """Parse ``printwidths.txt`` content into ``(sorted widths, warnings)``.

    See the module docstring for the exact rules.  Splitting on the raw text
    (rather than iterating file lines) keeps this testable without I/O.
    """
    widths = []
    warnings = []
    for n, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue                          # blank line: neither value nor warning
        if stripped.startswith(";") or stripped.startswith("//"):
            continue                          # supported comment syntax
        try:
            value = float(stripped)
        except ValueError:
            warnings.append(f"line {n}: {stripped!r} is not a number")
            continue
        if not (math.isfinite(value) and value > 0):
            warnings.append(
                f"line {n}: {value!r} is not a finite number larger than 0")
            continue
        widths.append(value)
    return sorted(set(widths)), warnings


def read_widths(path):
    """Read a ``printwidths.txt`` file.

    Returns ``(widths, warnings)`` where ``widths`` is a sorted, de-duplicated
    list of the valid line widths and ``warnings`` is a list of one string per
    bad line (each naming its 1-based line number).  A missing ``path`` raises
    ``FileNotFoundError``.
    """
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    return _widths_from_text(text)
