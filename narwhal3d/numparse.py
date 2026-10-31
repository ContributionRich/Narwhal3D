"""Native-only numbers, fractions and lengths with units.

Rust owns grammar, Unicode decimal handling, conversion and error classification.
Python provides type admission, module loading and repr formatting through the ABI.
Returns (value, None) or (None, error); a broken installation raises RuntimeError.
OURS safety limits: 4096 code points per input and finite values only.
"""
from pathlib import Path
import importlib.util
import sys

__all__ = ['parse_number', 'parse_length']


def _native_pure():
    if __package__:
        from . import native_pure
        return native_pure
    name = 'narwhal3d_native_pure'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name('native_pure.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return sys.modules[name]


def parse_number(text):
    """Parse a signed decimal/scientific, fraction or mixed number without units."""
    if not isinstance(text, str):
        return None, 'expected a string'
    return _native_pure().parse_owned(text)


def parse_length(text, model_unit='Millimeters'):
    """Parse a length and convert units; bare numbers remain in model units."""
    if not isinstance(text, str):
        return None, 'expected a string'
    if not isinstance(model_unit, str):
        return None, 'expected a model unit string'
    return _native_pure().parse_owned(text, model_unit)
