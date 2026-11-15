"""Required Rust ASCII DXF writer; analytic entity dictionaries are not tessellated.

LINE/CIRCLE/ARC/POLYLINE/SPLINE remain their explicit representations. Python
owns file I/O and host iterable marshalling only. Missing/stale native libraries
and unsafe data refuse before opening a destination; there is no Python codec.

Coordinates stay in the supplied units: None retains the legacy millimetre
header without MEASUREMENT, explicit names declare their unit, and unknown names
are unitless. ACI/RGB colors, first-seen layer order and cubic control chains
retain their legacy representation. Nonfinite coordinates, nonpositive radii,
and NUL/newline-bearing layer names raise ValueError before file mutation.
"""
import importlib.util
from pathlib import Path
import sys

# Public compatibility metadata; Rust owns actual unit/header selection.
INSUNITS = {"Inches": 1, "Feet": 2, "Miles": 3, "Millimeters": 4, "Centimeters": 5,
            "Meters": 6, "Kilometers": 7, "Microinches": 8, "Mils": 9, "Yards": 10,
            "Angstroms": 11, "Nanometers": 12, "Microns": 13, "Decimeters": 14,
            "Dekameters": 15, "Hectometers": 16, "Gigameters": 17}


def _native_dxf():
    if __package__:
        from . import native_dxf
        return native_dxf
    key = "narwhal3d_native_dxf"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name("native_dxf.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[key] = module
    return sys.modules[key]


def unit_header(units):
    """Return (INSUNITS, MEASUREMENT-or-None), preserving explicit/default units."""
    return _native_dxf().unit_header(units)


def build_dxf_bytes(entities, layers=None, units=None) -> bytes:
    """Build complete Latin-1 DXF bytes with the required Rust serializer."""
    out, written = _native_dxf().encode_dxf(entities, layers, units)
    return bytes(memoryview(out)[:written])


def write_dxf(path, entities, layers=None, units=None) -> int:
    """Write complete DXF, returning bytes; encoding errors preserve the target."""
    out, written = _native_dxf().encode_dxf(entities, layers, units)
    with open(path, "wb") as stream:
        stream.write(memoryview(out)[:written])
    return written
