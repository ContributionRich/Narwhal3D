"""Native-only formula host; native_calc owns the required ABI."""
import importlib.util
from pathlib import Path
import sys

__all__ = ['evaluate']


def _adapter():
    if __package__:
        from . import native_calc
        return native_calc
    name = 'narwhal3d_native_calc'
    if name not in sys.modules:
        parent = Path(__file__).resolve().parent
        path = parent / 'native_calc.py'
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return sys.modules[name]


def evaluate(text, model_unit='Millimeters'):
    return _adapter().evaluate(text, model_unit)
