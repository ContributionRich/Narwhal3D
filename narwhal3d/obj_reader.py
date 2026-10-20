"""Read Wavefront OBJ (for Import). **No `bpy`, no rhino3dm.**

``read_obj`` is the companion reader for ``obj_writer``: a file written by one
reads back into the same 0-based ``(name, vertices, faces)`` meshes.

``read_obj(path) -> list[(name, vertices, faces)]``

* ``vertices`` is a list of ``(x, y, z)`` floats (a 4th ``w`` coordinate is
  read and ignored);
* ``faces`` is a list of index tuples, **0-based per mesh**, kept at whatever
  polygon degree the file had (a 5-sided face stays a 5-tuple -- we do not
  triangulate on import);
* a new mesh begins at each ``o`` (and ``g``) name; a file with no ``o``/``g``
  is one mesh named after the file (``model.obj`` -> ``model``).

Handled, per the spec: ``v x y z [w]``; ``f`` in the ``i``, ``i/t``, ``i//n``
and ``i/t/n`` forms (only the vertex index -- the first part before any ``/`` --
is used; texture / normal indices are dropped); NEGATIVE (relative) indices,
where ``-1`` is the last vertex defined so far; comments (``#``) and blank
lines; and ``\\`` line continuations (a line ending in a backslash joins the
next physical line).

Ignored (and **counted**, see :func:`ignored_kinds`), so a caller can see what
was skipped rather than have it vanish: ``vt``, ``vn``, ``vp``, ``s``,
``mtllib``, ``usemtl`` -- and any other non-geometry command. Counting them
all means nothing is silently dropped.

A face whose index does not resolve to a real vertex in the current mesh
raises ``ValueError`` **naming the line number** -- never a silent bad index.
"""
from __future__ import annotations

import os


def _native_obj():
    """Lazily resolve the native OBJ adapter, or ``None``.

    Returns the ``native_pure`` module when it can be imported (production,
    where this file is ``narwhal3d.obj_reader``) or loaded from this directory
    (spike tests load this module by file path, so ``narwhal3d`` is not an
    importable package there). ``None`` means "use the unchanged Python
    reader" -- the native library is absent, it predates the OBJ decode
    exports, or the adapter cannot be imported. (Identical to the accessor in
    ``obj_writer``; the two share the same cached ``native_pure`` module.)
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


# Diagnostic state from the most recent read_obj call, keyed by command name.
# Mirrors the bridge.last_occt_decline() convention: a pure reader still needs
# to say WHAT it skipped.
_last_ignored = {}


def ignored_kinds():
    """A copy of the ignored-command counts from the last :func:`read_obj` call."""
    return dict(_last_ignored)


def read_obj(path):
    """Read *path* as OBJ -> ``list[(name, vertices, faces)]`` (0-based faces).

    See the module docstring for the full contract. Raises ``ValueError``
    (naming the line number) on a face index that does not resolve to a real
    vertex, or a ``v`` with fewer than three coordinates.

    When an ABI-compatible native decoder is available its (struct-identical)
    output is used; on any native unavailability or rejection the unchanged
    Python reader below runs.
    """
    file_base = os.path.basename(path)
    file_base = os.path.splitext(file_base)[0] or file_base

    # Reset the ignored-kind diagnostic at read entry so a clean second read
    # retains no stale kinds from a prior (dirtier) read -- the measured defect
    # (``_last_ignored`` was module-global and only ever accumulated).
    _last_ignored.clear()

    _native = _native_obj()
    if _native is not None:
        with open(path, "rb") as fh:
            raw_bytes = fh.read()
        decoded = _native.decode_obj(raw_bytes, file_base)
        if decoded is not None:
            meshes, ignored = decoded
            _last_ignored.update(ignored)
            return meshes
        # decode_obj returned None (unavailable / rejected / malformed): run the
        # unchanged Python reader below.

    with open(path, "r", encoding="latin-1") as fh:
        raw = fh.readlines()

    meshes = []
    verts = []
    faces = []
    current_name = None
    mesh_voffset = 0          # global vertex count at the start of this mesh
    global_vcount = 0

    def flush():
        nonlocal verts, faces, mesh_voffset
        if verts or faces:
            meshes.append((current_name or file_base,
                           [tuple(v) for v in verts],
                           [tuple(f) for f in faces]))
        verts = []
        faces = []
        mesh_voffset = global_vcount

    # join `\` continuations; record the line number where each logical line began
    logical = []
    buf = None
    buf_start = None
    for idx, rawline in enumerate(raw, start=1):
        line = rawline.rstrip("\r\n")
        if buf is None:
            buf = line
            buf_start = idx
        else:
            buf = buf + " " + line.strip()
        if buf.endswith("\\"):
            buf = buf[:-1].rstrip()
            continue
        logical.append((buf_start, buf))
        buf = None
        buf_start = None
    if buf is not None:
        logical.append((buf_start, buf))

    for ln, text in logical:
        parts = text.split()
        if not parts or parts[0].startswith("#"):
            continue
        cmd = parts[0].lower()

        if cmd == "v":
            if len(parts) < 4:
                raise ValueError(f"line {ln}: 'v' needs x y z")
            verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
            global_vcount += 1

        elif cmd in ("o", "g"):
            flush()
            current_name = parts[1] if len(parts) > 1 else None

        elif cmd == "f":
            if len(parts) < 4:
                raise ValueError(f"line {ln}: a face needs at least 3 vertices")
            idxs = []
            for token in parts[1:]:
                first = token.split("/")[0]          # i, or i/t, i//n, i/t/n
                try:
                    val = int(first)
                except ValueError:
                    raise ValueError(
                        f"line {ln}: bad face index {token!r}") from None
                # resolve to a 1-based global vertex index
                if val > 0:
                    g = val
                else:                                # negative = relative to now
                    g = global_vcount + val + 1      # -1 -> the last vertex
                local = g - 1 - mesh_voffset
                if local < 0 or local >= len(verts):
                    raise ValueError(
                        f"line {ln}: face index {token!r} references a missing "
                        f"vertex (resolved to {g}, mesh has {len(verts)} vertices)")
                idxs.append(local)
            faces.append(tuple(idxs))

        elif cmd in ("vt", "vn", "vp", "s", "mtllib", "usemtl"):
            _last_ignored[cmd] = _last_ignored.get(cmd, 0) + 1

        else:
            # not a geometry command -- count it so nothing is silently dropped
            _last_ignored[cmd] = _last_ignored.get(cmd, 0) + 1

    flush()
    return meshes
