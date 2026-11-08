"""Read historical triangulated DISPLAY proxies without depending on viewport tuning.

Python only extracts openNURBS data and marshals the comparison to the supervised Rust
helper. The Rust codec compares the whole oriented piecewise-planar surface: vertex/face
ordering and coplanar diagonals are irrelevant; missing faces, changed nonplanar geometry,
reversed winding and duplicate faces are not. Its fixed float32 rounding allowance applies
only to the old render proxy, never to the exact OCCT master or manufacturing checks.

Keep these codec readers when adding a new file format. Do not replace historical
validation with the current viewport mesh, and never replace an exact master with a mesh.
"""
from __future__ import annotations

from pathlib import Path
import struct
import tempfile

from . import bridge, native_document
from .kernel import KernelError, occt_backend

MAX_TRIANGLES = 1_000_000


def proxy_triangles(proxy):
    """The exact planar triangles stored in a legacy mesh-Brep, with winding preserved.

    No sampling: check each degree-1 collapsed bilinear patch and its actual trim edges.
    A malformed/nontriangular proxy is refused rather than approximated for comparison.
    """
    data = bridge.brep_data(proxy)
    faces, edges = data["faces"], data["edges"]
    if not faces or len(faces) > MAX_TRIANGLES:
        raise ValueError("invalid or oversized triangulated document proxy")
    triangles = []
    for face in faces:
        surface = face["surface"]
        poles = surface["poles"]
        if (surface["degree_u"] != 1 or surface["degree_v"] != 1
                or len(poles) != 2 or any(len(row) != 2 for row in poles)
                or surface["weights"] != [[1.0, 1.0], [1.0, 1.0]]
                or len(face["loops"]) != 1 or not face["loops"][0]["outer"]):
            raise ValueError("document proxy contains a nontriangular surface")
        points = []
        for point in (poles[0][0], poles[1][0], poles[1][1], poles[0][1]):
            point = tuple(point)
            if point not in points:
                points.append(point)
        if len(points) != 3:
            raise ValueError("document proxy patch is not a triangle")
        boundary = []
        for index, _reversed in face["loops"][0]["trims"]:
            if index < 0:  # collapsed/singular edge of a triangular bilinear patch
                continue
            edge = edges[index]
            if edge["degree"] != 1 or len(edge["poles"]) != 2 or edge["weights"] != [1.0, 1.0]:
                raise ValueError("document proxy triangle has a nonlinear trim")
            boundary.append(frozenset(tuple(p) for p in edge["poles"]))
        expected = [frozenset((points[i], points[(i + 1) % 3])) for i in range(3)]
        if len(boundary) != 3 or set(boundary) != set(expected):
            raise ValueError("document proxy trim does not bound its triangle")
        if face["reversed"]:
            points.reverse()
        triangles.append(points)
    return triangles


def _write(path, triangles):
    if not 0 < len(triangles) <= MAX_TRIANGLES:
        raise ValueError("invalid display-proxy comparison size")
    with Path(path).open("wb") as stream:
        stream.write(struct.pack("<8sQ", b"NWPX0001", len(triangles)))
        for triangle in triangles:
            stream.write(struct.pack("<9d", *(v for point in triangle for v in point)))


def matches_master(proxy, shape):
    """Whether a historical display proxy matches a frozen schema-1 meshing codec.

    Exact master digest, topology, volume, recipe and bounds validation remains in the
    document worker. This compares DISPLAY surfaces, so numbering/planar diagonals and
    the legacy float32 render-vertex rounding cannot invalidate a saved project.
    """
    actual = proxy_triangles(proxy)
    with tempfile.TemporaryDirectory(prefix="narwhal-proxy-compare-") as directory:
        left, right = Path(directory) / "saved.bin", Path(directory) / "canonical.bin"
        _write(left, actual)
        for codec in (1, 2, 3, 4):
            try:
                vertices, triangles = occt_backend.document_proxy_tessellation(shape, codec)
            except KernelError as error:
                if str(error) == "tessellation produced no triangles":
                    continue  # the historical fallback codecs below handle this case
                raise
            _write(right, [[vertices[i] for i in tri] for tri in triangles])
            if native_document.compare_display_proxies(left, right):
                return True
    return False
