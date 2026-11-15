"""STL file I/O over the required exact Rust triangle codec.

Callers supply explicit mesh triangles; this module never tessellates analytic
manufacturing geometry. Nonfinite file data is preserved, not certified as safe
mesh geometry. Missing/stale natives and native failures refuse explicitly.
"""
import importlib.util
import os
import sys


def _native_stl():
    if __package__:
        from . import native_stl
        return native_stl
    # Spike suites load this file directly, without importing the Blender addon.
    name = "narwhal3d_native_stl"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, os.path.join(os.path.dirname(__file__), "native_stl.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
    return sys.modules[name]


def write_stl(path, triangles, binary=True, name="Narwhal 3D") -> int:
    """Write binary or Latin-1 ASCII STL; return bytes written.

    Normals are recomputed, degenerate triangles retained, and file order kept.
    Encoding finishes before opening the destination, so a declined native call
    cannot truncate an existing file. There is no Python codec replay.
    """
    data = _native_stl().encode_stl(triangles, name, binary)
    if not isinstance(data, bytes):
        raise _native_stl().NativeSTLError("STL native encoder returned no file bytes")
    with open(path, "wb") as fh:
        fh.write(data)
    return len(data)


def read_stl(path) -> list:
    """Read triangles in file order; malformed files raise legacy ValueErrors."""
    return _native_stl().decode_stl(_read_data(path))


def _read_data(path):
    """Bounded file bytes, shared with the separate validated import codec."""
    native = _native_stl()
    with open(path, "rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        if size > native._MAX_BYTES:
            raise ValueError("STL input byte limit exceeded (512 MiB)")
        # A blanket 512-MiB read request reserves that much virtual memory even
        # for a tiny file. Bound reads without imposing that small-file cost.
        request = min(native._MAX_BYTES + 1, max(65536, size + 1))
        data = fh.read(request)
        if len(data) == request and request <= native._MAX_BYTES:
            # Covers streams/stat-size-zero files and growth since fstat.
            data += fh.read(native._MAX_BYTES + 1 - request)
    return data


def _read_ascii(text):
    return _native_stl().decode_stl(text.encode("latin-1"), mode=2)


def _read_binary(data):
    return _native_stl().decode_stl(data, mode=1)


def _facet_normal(a, b, c):
    return _native_stl().facet_normal(a, b, c)


def _f(x):
    return "%.9g" % (x,)


def _tok(line):
    parts = line.split(None, 1)
    return parts[0] if parts else ""
