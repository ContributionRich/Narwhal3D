"""FilletEdge / ChamferEdge on a solid, with an explicit mesh fallback.

rhino3dm 8.35 has no `Brep.CreateFilletEdges`, `CreateFillet`, or `CreateChamfer`, and a
`BrepEdge` exposes no adjacent-face topology. The fallback therefore tessellates the
solid, bevels its sharp mesh edges with `bmesh.ops.bevel`, and rebuilds a brep through
`CreateFromMesh`.

This fallback is intentionally identified as a mesh fillet/chamfer: the rounded surface
is a `segments`-sided faceted arc rather than a NURBS blend, and the radius is only an
approximate bmesh bevel offset. The Open CASCADE path below is used whenever an analytic
construction is available.
"""
import math

try:
    import rhino3dm as r3
except ImportError:  # pragma: no cover
    r3 = None


def _bridge():
    try:
        from . import bridge
    except ImportError:  # supports direct file-path loading
        import os
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import bridge
    return bridge


def _dist(a, b):
    return math.dist(a, b)


def _edge_mid(e):
    a, b = e.verts
    return ((a.co.x + b.co.x) / 2.0, (a.co.y + b.co.y) / 2.0,
            (a.co.z + b.co.z) / 2.0)


def _sharp_edges(bm, min_angle_deg=20.0):
    """Manifold edges whose two faces meet at more than `min_angle_deg` -- the real
    edges of the solid, not the triangulation diagonals that lie flat within a face."""
    out = []
    for e in bm.edges:
        if len(e.link_faces) == 2:
            try:
                ang = math.degrees(e.calc_face_angle())
            except Exception:  # noqa: BLE001
                continue
            if ang > min_angle_deg:
                out.append(e)
    return out


def fillet_edges(brep, radius, edge_points=None, chamfer=False, segments=8):
    """Round (fillet) or flat-cut (chamfer) the sharp edges of a solid `brep`.

    `radius` is the bevel size. `edge_points` is a list of world points; the sharp
    edge nearest each is beveled (so one named edge can be rounded). ``None`` bevels
    EVERY sharp edge -- "round all edges of this solid". `chamfer=True` makes a single
    flat cut; otherwise a `segments`-faceted rounded arc.

    Returns a mesh-derived Brep. This is a MESH fillet (see the module docstring): the
    blend is faceted and the radius approximate -- NOT a NURBS fillet. Raises
    ValueError for a non-positive radius, when the solid has no sharp edge, or when the
    radius is too large for the shortest edge being beveled (which would collapse faces
    into garbage rather than round them).
    """
    import bmesh

    if radius <= 0:
        raise ValueError(f"fillet radius must be positive (got {radius})")
    bridge = _bridge()
    # ANALYTIC FIRST. OCCT rounds an edge with a real CYLINDER along it and a real
    # SPHERE at each corner -- MEASURED, a 4x4x4 box beveled by 0.5 is 26 faces
    # {PLANE:6, CYLINDER:12, SPHERE:8} and airtight, against 492 faces (64 planar, 428
    # warped quads) from the bmesh route below. The mesh route stays as an AUDIBLE fallback
    # for a solid with no recipe: the result is tagged MESH_DERIVED, `describe()` says
    # "faceted", and `bridge.last_occt_decline()` says why.
    #
    # Only the ALL-EDGES case goes analytic so far. Selecting individual edges by world
    # point needs an edge-picking map in OCCT that is not built yet, so `edge_points` still
    # takes the mesh route rather than silently beveling the wrong edge.
    # The analytic route handles named edges too. `edge_points` are resolved to edge
    # indices by `narwhal3d.edgepick`, rather than by a kernel picking API, so
    # the identification works on any edge representation and is testable with no kernel at
    # all. An AMBIGUOUS pick propagates as a ValueError: beveling the wrong edge of a part is
    # silent and expensive, so refusing beats guessing.
    analytic = bridge.bevel_edges_occt(brep, radius, chamfer=bool(chamfer),
                                       edge_points=edge_points)
    if analytic is not None:
        return analytic
    bm = bridge._welded_brep_mesh(brep)
    try:
        edges = _sharp_edges(bm)
        if not edges:
            raise ValueError("fillet_edges: the brep has no sharp edge to round")
        if edge_points is not None:
            edges = [min(edges, key=lambda e, p=p: _dist(_edge_mid(e), p))
                     for p in edge_points]
        min_len = min(e.calc_length() for e in edges)
        if radius >= 0.5 * min_len:
            raise ValueError(
                f"fillet radius {radius} too large for the shortest edge "
                f"({min_len:.4f}); it must be < {0.5 * min_len:.4f}")
        segs = 1 if chamfer else max(2, int(segments))
        bmesh.ops.bevel(bm, geom=edges, offset=float(radius), offset_type="OFFSET",
                        segments=segs, profile=0.5, affect="EDGES")
        bridge._weld(bm)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        mesh = bridge._bmesh_to_rhino_mesh(bm)
    finally:
        bm.free()
    out = r3.Brep.CreateFromMesh(mesh, False)
    if out is None or len(out.Faces) == 0:
        raise ValueError("fillet_edges: CreateFromMesh produced an empty brep")
    return out


def chamfer_edges(brep, distance, edge_points=None, segments=8):
    """Flat-cut the sharp edges of `brep` using `fillet_edges` with ``chamfer=True``.

    `distance` is the cut size. The same refusals as `fillet_edges` apply.
    """
    return fillet_edges(brep, distance, edge_points=edge_points, chamfer=True,
                        segments=segments)
