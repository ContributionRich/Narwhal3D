"""Independent reference for RUST-ALIAS-14 (the live Python grammar, no native hook).

This is an independent copy of the ``parse_macro`` grammar from the live
``narwhal3d/alias_table.py``. It uses the Python regex + ``str.split`` +
``str.lstrip`` and NEVER imports or calls the native core, the candidate, or
``native_alias``. It exists only so the differential test has an oracle that is
independent of the native path. The grammar and option derivation are kept
verbatim from the live module.
"""
from __future__ import annotations

import re

_MACRO_RE = re.compile(
    r"^(?P<prefix>['!_])?\s*_?(?P<cmd>\w+)(?:\s+(?P<rest>.*))?$"
)


def parse_macro(macro: str):
    """Parse a macro into ``(prefix, command, options)``.

    Return ``(None, None, None)`` when the value does not follow the supported
    form. Falsy input (``None``, ``0``, ``False``, ``[]``, ``''``) returns the
    invalid tuple (``macro or ''``); a truthy non-string raises ``TypeError``
    (``re.match`` admission). This mirrors the live module exactly.
    """
    match = _MACRO_RE.match(macro or "")
    if not match:
        return None, None, None
    prefix = match.group("prefix")
    command = match.group("cmd")
    rest = match.group("rest")
    options = tuple(token.lstrip("_").strip() for token in (rest or "").split())
    return prefix, command, options
