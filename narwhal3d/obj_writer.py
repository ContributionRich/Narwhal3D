"""OBJ writer. Pure Python, standard library only.

OBJ is a widely supported mesh format. Upstream geometry code tessellates exact
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


def _num(x):
    return "%.9g" % (x,)


def write_obj(path, meshes) -> int:
    """Write *meshes* to *path* as an OBJ file. Returns the byte count.

    Layout per mesh: one ``o name`` line, then the ``v`` lines, then the ``f``
    lines. The file begins with the comment ``# Narwhal 3D``. Numbers
    are ``%.9g``. Vertex indices are 1-based and continue across meshes.
    """
    out = ["# Narwhal 3D"]
    offset = 0  # global 0-based index of the current mesh's first vertex
    for name, vertices, faces in meshes:
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
