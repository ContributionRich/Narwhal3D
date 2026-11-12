"""Read a ``printwidths.txt`` line-width file. Native parse, no bpy.

``read_widths(path) -> (list[float], warnings)`` returns valid line widths sorted
ascending and de-duplicated, plus one warning per invalid line identified by its
one-based line number.

The parse itself is the qualified Rust export ``narwhal_pure_core_printwidths``
(reached through ``native_metadata.widths_from_text``); this module owns only the
host file I/O.  There is no Python parser and no small-file bypass: if the native
export is unavailable or refuses the text, ``read_widths`` raises ``RuntimeError``
with the adapter's recorded reason instead of falling back.  Blank lines and lines
beginning with ``;`` or ``//`` after leading whitespace are ignored; every other
line must contain exactly one finite number greater than zero (the Rust port keeps
CPython's exact ``float``/``repr`` semantics, so warnings are byte-identical).
A missing file remains a caller error and raises ``FileNotFoundError``.
"""

import importlib.util
import sys
from pathlib import Path

__all__ = ["read_widths"]


def _native_metadata():
    """Return the ``native_metadata`` adapter.

    Package import first (``from . import native_metadata``); otherwise load it
    from this same directory by file path, the way ``numparse.py`` loads its
    native adapter, so the module also works when loaded directly from a path
    (spike tests) with no package context.
    """
    if __package__:
        from . import native_metadata
        return native_metadata
    name = "narwhal3d_native_metadata"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name("native_metadata.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return sys.modules[name]


def _native_widths(text):
    """Parse ``text`` with the native export; never fall back to Python.

    Raises ``RuntimeError`` naming the adapter's recorded route reason when the
    native export is unavailable, rejects the text, or reports an invalid result.
    """
    adapter = _native_metadata()
    result = adapter.widths_from_text(text)
    if result is None:
        route = adapter.status().get("routes", {}).get("widths", "unknown")
        raise RuntimeError(
            f"printwidths parse requires the native export "
            f"narwhal_pure_core_printwidths: {route}")
    return result


def _widths_from_text(text):
    """Compatibility symbol: the parse is now the native export only.

    Kept so existing callers (and the pre-Rust test suite) keep loading; it
    delegates entirely to ``_native_widths`` and performs no Python parsing.
    """
    return _native_widths(text)


def read_widths(path):
    """Read a ``printwidths.txt`` file.

    Returns ``(widths, warnings)`` where ``widths`` is a sorted, de-duplicated
    list of the valid line widths and ``warnings`` is a list of one string per
    bad line (each naming its 1-based line number).  A missing ``path`` raises
    ``FileNotFoundError``.  If the native parse is unavailable or refuses the
    text, raises ``RuntimeError`` with the adapter's recorded reason.
    """
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    return _native_widths(text)
