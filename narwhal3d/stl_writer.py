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


def write_stl(path, triangles, binary=True, name="Narwhal 3D") -> int:
    """Write *triangles* to *path* as STL (binary by default). Returns bytes.

    *binary*: 80-byte header (the *name*, space-padded, and never beginning
    with the word ``solid`` so readers do not mistake it for ASCII), a
    little-endian uint32 triangle count, then 50 bytes per facet -- the
    normal, the three vertices (12 little-endian float32), and a uint16 of 0.

    ASCII: ``solid name`` / ``facet normal`` / ``outer loop`` / three
    ``vertex`` lines / ``endloop`` / ``endfacet`` / ``endsolid name``, numbers
    as ``%.9g``.
    """
    tris = list(triangles)
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
    order. Used by the tests to round-trip a file we just wrote.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:80].lstrip().startswith(b"solid"):
        return _read_ascii(data.decode("latin-1"))
    return _read_binary(data)


def _read_ascii(text):
    verts = []
    for line in text.splitlines():
        t = line.strip()
        if t.startswith("vertex"):
            nums = t.split()[1:]
            if len(nums) >= 3:
                verts.append((float(nums[0]), float(nums[1]), float(nums[2])))
    return [tuple(verts[i:i + 3]) for i in range(0, len(verts), 3)]


def _read_binary(data):
    count = struct.unpack_from("<I", data, 80)[0]
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
