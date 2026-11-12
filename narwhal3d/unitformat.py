"""Native-only length display formatting.

Rust owns mode normalization, rounding, fraction reduction and rendering.
Python provides host coercion and exception types; no Decimal arithmetic or
Python formatter is replayed if the native library is absent or stale.
"""
from pathlib import Path
import importlib.util
import sys

__all__ = ["format_length"]


def _adapter():
    if __package__:
        from . import native_unitformat
        return native_unitformat
    name = "narwhal3d_native_unitformat"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("native_unitformat.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return sys.modules[name]


native_unitformat = _adapter()
format_length = native_unitformat.format_length
