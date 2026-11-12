"""Native-only surveyor bearings; pending Main integrated qualification.

OURS: inputs over 4096 code points and nonfinite numbers are refused explicitly.
Python only loads the required adapter; it never parses or replays errors.
"""
import importlib.util
from pathlib import Path
import sys

__all__ = ['bearing_angle', 'parse_surveyor']


def _adapter():
    try:
        from . import native_bearing
        return native_bearing
    except ImportError:
        name = 'narwhal3d_native_bearing'
        if name not in sys.modules:
            # File-path loading for qualification; production lives beside adapter.
            here = Path(__file__).resolve().parent
            path = here / 'native_bearing.py'
            if not path.is_file():
                path = here.parent / 'narwhal3d' / 'native_bearing.py'
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            sys.modules[name] = module
        return sys.modules[name]


def bearing_angle(text):
    return _adapter().bearing_angle(text)


def parse_surveyor(text):
    return _adapter().parse_surveyor(text)
