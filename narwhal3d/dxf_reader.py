"""Read DXF file data into ordered analytic entity dictionaries via required Rust.

Rust returns the original explicit analytic entity data without tessellating,
clamping, validating it as kernel geometry, or silently losing skipped records.
Numeric/file errors retain their original classes; required-native admission and
protocol errors are distinct. No Python DXF/numeric parser or fallback exists.
"""
import importlib.util
import os
from pathlib import Path
import sys


class FileFormatError(ValueError):
    """The file is not a DXF this reader can represent exactly."""


def _native_dxf():
    if __package__:
        from . import native_dxf_reader
        return native_dxf_reader
    key = "narwhal3d_native_dxf_reader24"
    if key not in sys.modules:
        path = Path(__file__).resolve().with_name("native_dxf_reader.py")
        spec = importlib.util.spec_from_file_location(key, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[key] = module
    return sys.modules[key]


def read_dxf(path):
    """Read a DXF into ordered entity/layer/unit/skipped dicts via required Rust."""
    native = _native_dxf()
    with open(path, "rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        if size > native._MAX_INPUT:
            raise native.NativeDXFReaderError("DXF reader input byte limit exceeded")
        request = min(native._MAX_INPUT + 1, max(65536, size + 1))
        data = stream.read(request)
        if len(data) == request and request <= native._MAX_INPUT:
            data += stream.read(native._MAX_INPUT + 1 - request)
    return native.decode_dxf(data, format_error=FileFormatError)
