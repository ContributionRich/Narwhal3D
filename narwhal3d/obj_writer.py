"""OBJ writer. Pure Python, standard library only.

OBJ is a widely supported mesh format. When an ABI-compatible native encoder is
available its (byte-identical) output is used; on any native unavailability or
failure the unchanged Python writer below runs. Upstream geometry code tessellates exact
geometry into triangles or quads and passes named meshes to this module, which
only writes them.

``meshes`` is a sequence of ``(name, vertices, faces)`` where:

* ``vertices`` is a list of ``(x, y, z)`` in model units;
* ``faces`` is a list of index TUPLES, **0-based in Python** (OBJ is 1-based),
  with at least 3 vertices each.

Indices continue across meshes: the second mesh's first vertex is not index 1,
but ``1 + (number of vertices in the first mesh)``. Restarting face indices at
1 would silently reference the previous mesh's geometry.

Refusal (``ValueError``), never a silent write: a face with fewer than 3
vertices, or a face index outside ``0..len(vertices)-1``.
"""

import os


def _num(x):
    return "%.9g" % (x,)


def _native_obj():
    """Lazily resolve the native OBJ adapter, or ``None``.

    Returns the ``native_pure`` module when it can be imported (production,
    where this file is ``narwhal3d.obj_writer``) or loaded from this directory
    (spike tests load this module by file path, so ``narwhal3d`` is not an
    importable package there). ``None`` means "use the unchanged Python
    writer" -- the native library is absent, it predates the OBJ exports, or
    the adapter cannot be imported.
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


def write_obj(path, meshes) -> int:
    """Write *meshes* to *path* as an OBJ file. Returns the byte count.

    Layout per mesh: one ``o name`` line, then the ``v`` lines, then the ``f``
    lines. The file begins with the comment ``# Narwhal 3D``. Numbers
    are ``%.9g``. Vertex indices are 1-based and continue across meshes.

    The one-pass native encoder (Q117-01) is the production fast path when it
    is available and the input is at/above the measured crossover: it
    serializes the file exactly once into a Python-owned ``bytearray`` (which
    is written directly, no ``bytes`` copy) and is measured >=10% faster than
    the Python writer there. Below the crossover, or when the native route is
    unavailable or declines the input, the unchanged Python writer below runs
    (the complete fallback).
    """
    # Snapshot every one-shot level exactly once, up front. A one-shot
    # generator is consumed here (not by the native packing or the crossover
    # check), so neither the native route nor the Python fallback ever sees an
    # exhausted iterator; both operate on the same materialized list.
    materialized = []
    for name, vertices, faces in meshes:
        materialized.append((name, list(vertices),
                             [tuple(face) for face in faces]))
    # One-pass native route (Q117-01): active only when the one-pass export is
    # available and the materialized input clears the measured crossover.
    _native = _native_obj()
    if _native is not None and _native._obj_native_active(materialized):
        result = _native._native_obj_encode_into(materialized)
        if result is not None:
            out, written = result
            with open(path, "wb") as fh:
                fh.write(memoryview(out)[:written])
            return written
    out = ["# Narwhal 3D"]
    offset = 0  # global 0-based index of the current mesh's first vertex
    for name, vertices, faces in materialized:
        nverts = len(vertices)
        for face in faces:
            if len(face) < 3:
                raise ValueError(
                    "face %r has fewer than 3 vertices" % (face,))
            for idx in face:
                if idx < 0 or idx >= nverts:
                    raise ValueError(
                        "face index %d is out of range 0..%d" % (idx, nverts - 1))
        out.append("o %s" % (name,))
        for x, y, z in vertices:
            out.append("v %s %s %s" % (_num(x), _num(y), _num(z)))
        for face in faces:
            # 1-based, and shifted by the vertices of every earlier mesh.
            out.append("f " + " ".join(str(i + 1 + offset) for i in face))
        offset += nverts

    data = ("\n".join(out) + "\n").encode("latin-1")
    with open(path, "wb") as fh:
        fh.write(data)
    return len(data)
