"""STL writer (binary + ASCII). Pure Python, standard library only.

STL is consumed by 3-D printers and slicers. This module does not
tessellate exact geometry -- callers provide triangles, and this module writes
them in the two STL dialects readers still use.

``triangles`` is a sequence of ``((x,y,z),(x,y,z),(x,y,z))`` in model units,
wound counter-clockwise seen from outside.

A facet's normal is recomputed here as the normalised
``(v1-v0) x (v2-v0)``. A degenerate triangle (zero area) is NOT dropped -- it
is written with a zero normal. Silent dropping would produce a file with
missing geometry.
"""

import struct
import os


def _facet_normal(a, b, c):
    """Normalised ``(b-a) x (c-a)``; ``((0,0,0),(0,0,0),(0,0,0))`` for zero area."""
    ux = b[0] - a[0]; uy = b[1] - a[1]; uz = b[2] - a[2]
    vx = c[0] - a[0]; vy = c[1] - a[1]; vz = c[2] - a[2]
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    m = (nx * nx + ny * ny + nz * nz) ** 0.5
    if m == 0.0:
        return (0.0, 0.0, 0.0)
    return (nx / m, ny / m, nz / m)


def _f(x):
    return "%.9g" % (x,)


def _native_stl():
    """Lazily resolve the native STL adapter, or ``None``.

    Returns the ``native_pure`` module when it can be imported (production,
    where this file is ``narwhal3d.stl_writer``) or loaded from this directory
    (spike tests load this module by file path, so ``narwhal3d`` is not an
    importable package there). ``None`` means "use the unchanged Python
    writer" -- the native library is absent, its ABI is wrong, or the adapter
    cannot be imported.
    """
    try:
        from . import native_pure as _mod  # production package context
        return _mod
    except ImportError:
        pass
    try:
        import importlib.util
        import sys
        _canon = "narwhal3d_native_pure"
        if _canon in sys.modules:
            return sys.modules[_canon]
        _path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "native_pure.py")
        if not os.path.isfile(_path):
            return None
        _spec = importlib.util.spec_from_file_location(_canon, _path)
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules[_canon] = _mod
        _spec.loader.exec_module(_mod)
        return _mod
    except Exception:  # noqa: BLE001 -- any failure degrades to the fallback
        return None


def write_stl(path, triangles, binary=True, name="Narwhal 3D") -> int:
    """Write *triangles* to *path* as STL (binary by default). Returns bytes.

    *binary*: 80-byte header (the *name*, space-padded, and never beginning
    with the word ``solid`` so readers do not mistake it for ASCII), a
    little-endian uint32 triangle count, then 50 bytes per facet -- the
    normal, the three vertices (12 little-endian float32), and a uint16 of 0.

    ASCII: ``solid name`` / ``facet normal`` / ``outer loop`` / three
    ``vertex`` lines / ``endloop`` / ``endfacet`` / ``endsolid name``, numbers
    as ``%.9g``.

    When an ABI-compatible native encoder is available its (byte-identical)
    output is used; on any native unavailability or failure the unchanged
    Python writer below runs.
    """
    # Materialize once before trying native: if a one-shot iterator is consumed
    # by a declining native call, the complete Python fallback must still see
    # the same triangles.
    tris = list(triangles)
    _native = _native_stl()
    if _native is not None:
        _data = _native.encode_stl(tris, name, binary)
        if _data is not None:
            with open(path, "wb") as fh:
                fh.write(_data)
            return len(_data)
    if binary:
        # The header is exactly 80 bytes and must NOT read as the ASCII
        # keyword `solid` (some readers then take the binary file for ASCII).
        if str(name).strip().lower().startswith("solid"):
            name = "Narwhal 3D"
        hdr = (str(name)[:80] + " " * 80).encode("latin-1")[:80]
        parts = [hdr, struct.pack("<I", len(tris))]
        for a, b, c in tris:
            nx, ny, nz = _facet_normal(a, b, c)
            parts.append(struct.pack(
                "<12fH",
                nx, ny, nz,
                a[0], a[1], a[2],
                b[0], b[1], b[2],
                c[0], c[1], c[2],
                0,
            ))
        data = b"".join(parts)
    else:
        out = ["solid %s" % (name or "Narwhal 3D")]
        for a, b, c in tris:
            nx, ny, nz = _facet_normal(a, b, c)
            out.append("  facet normal %s %s %s" % (_f(nx), _f(ny), _f(nz)))
            out.append("  outer loop")
            out.append("    vertex %s %s %s" % (_f(a[0]), _f(a[1]), _f(a[2])))
            out.append("    vertex %s %s %s" % (_f(b[0]), _f(b[1]), _f(b[2])))
            out.append("    vertex %s %s %s" % (_f(c[0]), _f(c[1]), _f(c[2])))
            out.append("  endloop")
            out.append("  endfacet")
        out.append("endsolid %s" % (name or "Narwhal 3D"))
        data = ("\n".join(out) + "\n").encode("latin-1")

    with open(path, "wb") as fh:
        fh.write(data)
    return len(data)


def read_stl(path) -> list:
    """Read the triangles back out of an STL file (binary or ASCII).

    Returns ``[(a, b, c), ...]`` with each point a 3-tuple of floats, in file
    order. A truncated or malformed file is rejected with ``ValueError`` (a
    truncated binary header, a facet-count/length mismatch, or an incomplete
    ASCII facet) -- it is never silently read as a shorter file.

    The native decoder remains directly tested but is not used here: its Python result
    reshaping measured slower than the established ``struct.unpack_from`` reader.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:80].lstrip().startswith(b"solid"):
        return _read_ascii(data.decode("latin-1"))
    return _read_binary(data)


def _tok(line):
    """The first whitespace-separated token of a line (``''`` if blank)."""
    parts = line.split(None, 1)
    return parts[0] if parts else ""


def _read_ascii(text):
    """Read an ASCII STL body, validating every facet has exactly three
    vertices and a closing ``endfacet``. A dangling facet (no ``endfacet``
    before ``endsolid`` or EOF), a facet with too few / too many vertices, or
    a ``vertex`` outside a facet is rejected with ``ValueError``.
    """
    verts = []
    facets = []
    nv = 0
    in_facet = False
    for line in text.splitlines():
        t = line.strip()
        if not t:
            continue
        tok = _tok(t)
        if tok == "facet":
            if in_facet:
                raise ValueError("STL ASCII: facet not closed by endfacet")
            in_facet = True
            nv = 0
        elif tok == "vertex":
            if not in_facet:
                raise ValueError("STL ASCII: vertex outside a facet")
            if nv == 3:
                raise ValueError("STL ASCII: facet has more than 3 vertices")
            nums = t.split()[1:]
            if len(nums) < 3:
                raise ValueError("STL ASCII: vertex has fewer than 3 components")
            verts.append((float(nums[0]), float(nums[1]), float(nums[2])))
            nv += 1
        elif tok == "endfacet":
            if not in_facet:
                raise ValueError("STL ASCII: endfacet without an open facet")
            if nv != 3:
                raise ValueError(
                    "STL ASCII: facet has %d vertices, need exactly 3" % nv)
            facets.append(tuple(verts[len(verts) - 3:]))
            nv = 0
            in_facet = False
    if in_facet:
        raise ValueError("STL ASCII: facet not closed by endfacet")
    return facets


def _read_binary(data):
    if len(data) < 84:
        raise ValueError(
            "STL binary truncated: %d bytes, need at least 84 (header + count)"
            % len(data))
    count = struct.unpack_from("<I", data, 80)[0]
    expected = 84 + 50 * count
    if len(data) != expected:
        raise ValueError(
            "STL binary truncated: %d bytes but count=%d requires %d"
            % (len(data), count, expected))
    out = []
    off = 84
    for _ in range(count):
        vals = struct.unpack_from("<12fH", data, off)
        out.append((
            (vals[3], vals[4], vals[5]),
            (vals[6], vals[7], vals[8]),
            (vals[9], vals[10], vals[11]),
        ))
        off += 50
    return out
