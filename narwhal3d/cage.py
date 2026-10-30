"""CageEdit orchestration: bind captive objects to a control cage and re-deform them.

The deformation itself is Rust (``native/pure_core/src/cage.rs`` via ``native_cage``).
This module only reads and writes CONTROL POINTS, so a captive NURBS curve or surface
stays an exact NURBS of the same degree, knots and weights -- nothing is sampled.

The cage is a document polyline whose vertices are the lattice points, visited in a
serpentine order so each lattice point is exactly one vertex. Dragging a vertex with the
normal control-point tools edits the cage; ``refresh`` then rebuilds every captive from
the REST copy stored on the cage, so repeated edits never accumulate error.
"""
from __future__ import annotations

import json

import rhino3dm as r3

from . import native_cage

CAGE_KEY = "Narwhal3D:Cage"


class CageError(ValueError):
    """A cage operation was refused, with a message fit to show the user."""


# ---------------------------------------------------------------------------
# lattice <-> polyline vertex order
# ---------------------------------------------------------------------------

def serpentine(counts):
    """Lattice indices (i fastest) in the order the cage polyline visits them."""
    nx, ny, nz = counts
    order = []
    for k in range(nz):
        rows = range(ny) if k % 2 == 0 else range(ny - 1, -1, -1)
        for step, j in enumerate(rows):
            forward = (step + k * ny) % 2 == 0
            cols = range(nx) if forward else range(nx - 1, -1, -1)
            order.extend(i + nx * (j + ny * k) for i in cols)
    return order


# ---------------------------------------------------------------------------
# exact control-point access per captive type
# ---------------------------------------------------------------------------

def _nurbs_curve_points(curve):
    pts = curve.Points
    out, weights = [], []
    for index in range(len(pts)):
        cp = pts[index]
        w = getattr(cp, "W", 1.0) or 1.0
        out.append((cp.X / w, cp.Y / w, cp.Z / w))
        weights.append(w)
    return out, weights


def _surface_points(surf):
    nu, nv = surf.Points.CountU, surf.Points.CountV
    out, weights = [], []
    for i in range(nu):
        for j in range(nv):
            cp = surf.Points[i, j]
            w = getattr(cp, "W", 1.0) or 1.0
            out.append((cp.X / w, cp.Y / w, cp.Z / w))
            weights.append(w)
    return out, weights


def _untrimmed_surface(brep):
    """The NURBS form of a one-face brep whose face is its whole surface, else None."""
    # `IsSurface`: "a single untrimmed surface" -- a trimmed face's control points do
    # not describe its boundary, so moving them alone would not move the trim.
    if not isinstance(brep, r3.Brep) or len(brep.Faces) != 1 or not brep.IsSurface:
        return None
    return brep.Faces[0].UnderlyingSurface().ToNurbsSurface()


def captive_points(geom):
    """The exact control points of `geom` that a cage moves, or a CageError."""
    if isinstance(geom, r3.Point):
        p = geom.Location
        return [(p.X, p.Y, p.Z)]
    if isinstance(geom, r3.Curve):
        return _nurbs_curve_points(geom.ToNurbsCurve())[0]
    if isinstance(geom, r3.Mesh):
        return [(p.X, p.Y, p.Z) for p in geom.Vertices.ToPoint3dArray()]
    surf = _untrimmed_surface(geom)
    if surf is not None:
        return _surface_points(surf)[0]
    raise CageError(_unsupported(geom))


def _unsupported(geom):
    if isinstance(geom, r3.Brep):
        return ("deforming a trimmed surface, polysurface or solid with a cage is not "
                "implemented yet (curves, points, meshes and untrimmed surfaces are)")
    return f"a {type(geom).__name__} cannot be a cage captive yet"


def deformed(rest_geom, mapper):
    """A deformed copy of `rest_geom`: every control point passed through `mapper`
    (list of xyz -> list of xyz), weights, degrees and knots unchanged."""
    if isinstance(rest_geom, r3.Point):
        (x, y, z), = mapper(captive_points(rest_geom))
        return r3.Point(r3.Point3d(x, y, z))
    if isinstance(rest_geom, r3.Curve):
        curve = rest_geom.ToNurbsCurve()
        pts, weights = _nurbs_curve_points(curve)
        for index, ((x, y, z), w) in enumerate(zip(mapper(pts), weights)):
            curve.Points[index] = r3.Point4d(x * w, y * w, z * w, w)
        return curve
    if isinstance(rest_geom, r3.Mesh):
        mesh = rest_geom.Duplicate()
        for index, (x, y, z) in enumerate(mapper(captive_points(rest_geom))):
            mesh.Vertices.SetPoint3dAt(index, r3.Point3d(x, y, z))
        mesh.Normals.ComputeNormals() if hasattr(mesh, "Normals") else None
        return mesh
    surf = _untrimmed_surface(rest_geom)
    if surf is None:
        raise CageError(_unsupported(rest_geom))
    pts, weights = _surface_points(surf)
    moved = mapper(pts)
    nv = surf.Points.CountV
    for index, ((x, y, z), w) in enumerate(zip(moved, weights)):
        surf.Points[index // nv, index % nv] = r3.Point4d(x * w, y * w, z * w, w)
    brep = r3.Brep.CreateFromSurface(surf)
    if brep is None:
        raise CageError("the deformed surface could not be rebuilt")
    from . import bridge
    bridge.record_exact_recipe(brep)
    return brep


# ---------------------------------------------------------------------------
# cage creation and refresh
# ---------------------------------------------------------------------------

def bounding_frame(geoms, counts, degrees):
    """(origin, axes, degrees, counts) of the world-axis box around the captives'
    CONTROL POINTS. OURS: the reference sizes the cage from the object bounding box;
    a control point outside the cage would not be moved, so the box must hold every
    control point (for a circle the two boxes are the same). A flat direction (zero
    thickness) gets one cage point and degree 0."""
    points = [p for g in geoms for p in captive_points(g)]
    if not points:
        raise CageError("select the objects to edit")
    box = [[min(p[d] for p in points) for d in range(3)],
           [max(p[d] for p in points) for d in range(3)]]
    size = [box[1][d] - box[0][d] for d in range(3)]
    extent = max(size)
    if extent <= 0.0:
        raise CageError("the captives have no extent to cage")
    axes, out_counts, out_degrees = [], [], []
    for d in range(3):
        axis = [0.0, 0.0, 0.0]
        # Only an exactly flat axis can collapse without moving the rest geometry.
        # Small but nonzero thickness is still manufacturing geometry, not tolerance noise.
        if size[d] == 0.0:
            out_counts.append(1)
            out_degrees.append(0)
        else:
            axis[d] = size[d]
            n = int(counts[d])
            if n < 2:
                raise CageError("PointCount must be at least 2 in every direction")
            out_counts.append(n)
            out_degrees.append(max(1, min(int(degrees[d]), n - 1)))
        axes.append(axis)
    return tuple(box[0]), axes, out_degrees, out_counts


def make_cage(captives, counts=(4, 4, 4), degrees=(3, 3, 3)):
    """A cage polyline around `captives` [(object id, geometry)], with the binding
    stored on it. Raises CageError by name."""
    if not native_cage.available():
        raise CageError("CageEdit needs the Rust pure core, which is not available")
    for _oid, geom in captives:
        captive_points(geom)            # refuse unsupported captives before building
    origin, axes, deg, cnt = bounding_frame([g for _oid, g in captives], counts, degrees)
    lattice = native_cage.rest_points(origin, axes, deg, cnt)
    if lattice is None:
        raise CageError("the cage frame is not valid")
    order = serpentine(cnt)
    pts = [r3.Point3d(*lattice[i]) for i in order]
    if len(pts) == 1:
        raise CageError("the captives are a single point; there is nothing to cage")
    cage = r3.PolylineCurve(pts)
    binding = {"origin": list(origin), "axes": axes, "degrees": deg, "counts": cnt,
               "captives": [{"id": str(oid), "rest": geom.Encode()} for oid, geom in captives]}
    cage.SetUserString(CAGE_KEY, json.dumps(binding))
    return cage


def binding_of(geom):
    text = geom.GetUserString(CAGE_KEY) if geom is not None else None
    return json.loads(text) if text else None


def lattice_of(cage_geom, binding):
    """Lattice points (index order) read back from the cage polyline's vertices."""
    from . import bridge
    vertices = bridge.control_points(cage_geom)
    order = serpentine(binding["counts"])
    if len(vertices) != len(order):
        raise CageError("the cage has lost points; it cannot drive its captives")
    lattice = [None] * len(order)
    for vertex, index in zip(vertices, order):
        lattice[index] = vertex
    return lattice


def deformed_captives(cage_geom):
    """[(captive id, new geometry)] for the cage's current shape."""
    binding = binding_of(cage_geom)
    if binding is None:
        return []
    lattice = lattice_of(cage_geom, binding)
    frame = (binding["origin"], binding["axes"], binding["degrees"], binding["counts"])

    def mapper(points):
        out = native_cage.deform(*frame, lattice, points)
        if out is None:
            raise CageError("CageEdit needs the Rust pure core, which is not available")
        return out

    out = []
    for captive in binding["captives"]:
        rest = r3.CommonObject.Decode(captive["rest"])
        out.append((captive["id"], deformed(rest, mapper)))
    return out


def refresh(model, cage_geom):
    """Rebuild every captive of `cage_geom` in `model`. Returns how many changed."""
    changed = 0
    for oid, geom in deformed_captives(cage_geom):
        if model.replace_geometry(oid, geom):
            changed += 1
    return changed
