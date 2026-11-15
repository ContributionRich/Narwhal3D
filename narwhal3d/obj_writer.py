"""Required Rust OBJ writer; Python owns iterable snapshots and file I/O.

Only o/v/f mesh records are supported. Exact geometry is tessellated upstream
for explicit mesh export, never replaced here. Invalid meshes are refused
before opening the destination. Nonfinite vertices and noninteger face indices
were unsafe legacy output and are now intentionally rejected.
"""
import importlib.util
import os
import sys


def _native_obj():
    if __package__:
        from . import native_obj
        return native_obj
    key = "narwhal3d_native_obj"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            key, os.path.join(os.path.dirname(__file__), "native_obj.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[key] = mod
    return sys.modules[key]


def write_obj(path, meshes) -> int:
    """Write Latin-1 OBJ with %.9g coordinates and cumulative 1-based indices.

    Returns bytes written. Native failures raise RuntimeError without replay;
    malformed meshes raise ValueError. UnicodeEncodeError and file OSErrors
    propagate unchanged. All encoding completes before opening *path*.
    """
    native = _native_obj()
    out, written = native.encode_obj(meshes)
    with open(path, "wb") as fh:
        fh.write(memoryview(out)[:written])
    return written
