"""Display-only overlap candidates; exact document geometry is never changed.

Python adapts Blender/document APIs and labels. Existing Rust batch projection /
polyline distance does the numerical hit testing. Blender's native BVH supplies
face-interior ray hits (including holes in the display triangulation). No kernel
construction occurs on popup hover/draw. The existing scalar picker helpers are
used only as a complete compatibility fallback without the optional Rust core.
"""
from dataclasses import dataclass


@dataclass
class Candidate:
    key: tuple
    label: str
    lines: tuple = ()
    triangles: tuple = ()
    points: tuple = ()
    tree: object = None


def _polyline(points):
    # Application curve segments use rhino3dm Point3d, unlike tuple/BVH samples.
    return tuple((float(point.X), float(point.Y), float(point.Z))
                 if hasattr(point, 'X') else tuple(float(v) for v in point)
                 for point in points)


def _candidate(key, label, lines=(), triangles=(), points=()):
    from mathutils.bvhtree import BVHTree
    triangles = tuple(_polyline(t) for t in triangles)
    tree = None
    if triangles:
        vertices = [p for t in triangles for p in t]
        tree = BVHTree.FromPolygons(vertices,
            [(i, i + 1, i + 2) for i in range(0, len(vertices), 3)], all_triangles=True)
    return Candidate(key, label, tuple(_polyline(p) for p in lines), triangles,
                     _polyline(points), tree)


_cache = {}


def clear():
    _cache.clear()


def collect(ui, mode):
    """Prepare immutable display data once per document/visibility/mode signature."""
    import bpy
    import rhino3dm as r3
    blocked = ui._unpickable_names_cached()
    rows = ui._doc_rows()
    visible = tuple(row for row in rows if row[0] not in blocked
                    and (ob := bpy.data.objects.get(row[0])) is not None
                    and ui.bridge.display_visible(ob))
    signature = (ui._doc_key(), tuple((row[0], str(row[4].Id)) for row in visible), mode)
    if _cache.get('rows') is rows and _cache.get('signature') == signature:
        return _cache['candidates']
    parts_by_name = None
    out = []
    for name, _state, _layer, geom, _attrs in visible:
        if isinstance(geom, r3.Curve):
            if mode == 'object':
                out.append(_candidate((name, 'Object', 0), name + ' — Curve',
                                      lines=(ui._curve_points(geom),)))
            elif mode == 'part':
                for index, points, _grips in ui.bridge.curve_segments(geom):
                    out.append(_candidate((name, 'Segment', index),
                        f'{name} — Segment {index + 1}', lines=(points,)))
            continue
        if isinstance(geom, r3.Point):
            if mode == 'object':
                out.append(_candidate((name, 'Object', 0), name + ' — Point',
                                      points=((geom.Location.X, geom.Location.Y, geom.Location.Z),)))
            continue
        if parts_by_name is None:
            parts_by_name = ui._brep_parts_all()
        parts = parts_by_name.get(name) or {}
        if mode == 'object':
            lines = [points for _index, points in parts.get('edges', ())]
            triangles = [t for _index, ts in parts.get('faces', ()) for t in ts]
            if not lines and not triangles:
                # Mesh/Picture display data are already Blender-native. They do
                # not imply analytic geometry or editable BRep subobjects.
                ob = bpy.data.objects.get(name)
                mesh = getattr(ob, 'data', None)
                if getattr(ob, 'type', None) == 'MESH':
                    mesh.calc_loop_triangles()
                    vertices = [tuple(ob.matrix_world @ v.co) for v in mesh.vertices]
                    triangles = [tuple(vertices[i] for i in t.vertices) for t in mesh.loop_triangles]
                    lines = [tuple(vertices[i] for i in e.vertices) for e in mesh.edges]
            if lines or triangles:
                out.append(_candidate((name, 'Object', 0), name + ' — ' + type(geom).__name__,
                                      lines, triangles))
        else:
            if mode == 'edge':
                # Edge commands still obey shaded occlusion, but must not offer
                # faces as selectable rows. Keep their display BVH only.
                triangles = [t for _index, ts in parts.get('faces', ()) for t in ts]
                if triangles:
                    out.append(_candidate((name, 'Occluder', 0), '', triangles=triangles))
            for index, points in parts.get('edges', ()):
                out.append(_candidate((name, 'Edge', index), f'{name} — Edge {index + 1}', lines=(points,)))
            if mode == 'part':
                for index, ts in parts.get('faces', ()):
                    out.append(_candidate((name, 'Face', index), f'{name} — Face {index + 1}', triangles=ts))
                for index, point in parts.get('vertices', ()):
                    out.append(_candidate((name, 'Vertex', index), f'{name} — Vertex {index + 1}', points=(point,)))
    # Stable identities let the native adapter reuse its packed input buffers.
    polylines, indices = [], []
    for index, candidate in enumerate(out):
        if candidate.key[1] == 'Occluder':
            continue
        spans = list(candidate.lines)
        spans.extend((p, p) for p in candidate.points)
        # Face boundaries within the aperture remain selectable beside an edge.
        spans.extend(t + (t[0],) for t in candidate.triangles)
        polylines.extend(spans)
        indices.extend([index] * len(spans))
    result = (tuple(out), tuple(polylines), tuple(indices))
    _cache.update(rows=rows, signature=signature, candidates=result)
    return result


def at(ui, area, space, region, x, y, mode='object'):
    """Return all aperture hits, not only the nearest; UI order is application-defined.

    Component depth occlusion retains the established shaded-mode rule. Whole
    object ambiguity deliberately offers overlapping objects, including those
    behind other objects; hidden/locked objects are never offered.
    """
    import bpy_extras.view3d_utils as v3u
    from . import native_selection, subobject
    rv = getattr(space, 'region_3d', None)
    if rv is None or region is None:
        return []
    candidates, lines, indices = collect(ui, mode)
    tolerance = ui.SUBOBJECT_PICK_PX if mode != 'object' else ui.CURVE_PICK_PX
    measured = native_selection.nearest_projected_polylines(
        lines, rv.perspective_matrix, region.width, region.height, (x, y), with_distances=True)
    distances = {}
    if measured is not None:
        for index, hit in zip(indices, measured):
            if hit is not None and hit[1] <= tolerance:
                point, distance = hit
                depth = ui._view_depth(rv, point)
                old = distances.get(index)
                if old is None or (distance, depth) < old:
                    distances[index] = (distance, depth)
    else:
        for index, points in zip(indices, lines):
            projected = [ui._project_with_depth(region, rv, p) for p in points]
            # Do not connect spans across a clipped point.
            for a, b in zip(projected, projected[1:]):
                if a is None or b is None:
                    continue
                hit = subobject._closest_on_polyline((x, y), (a, b))
                if hit is not None and hit[0] <= tolerance:
                    old = distances.get(index)
                    if old is None or hit < old:
                        distances[index] = hit
    origin = v3u.region_2d_to_origin_3d(region, rv, (x, y))
    direction = v3u.region_2d_to_vector_3d(region, rv, (x, y))
    front = None
    for index, candidate in enumerate(candidates):
        if candidate.tree is None:
            continue
        point, _normal, _face, _distance = candidate.tree.ray_cast(origin, direction)
        if point is not None:
            depth = ui._view_depth(rv, point)
            if candidate.key[1] != 'Occluder':
                distances[index] = (0.0, depth)
            front = depth if front is None else min(front, depth)
    occlude = mode != 'object' and space.shading.type != 'WIREFRAME'
    priority = {'Vertex': 0, 'Segment': 1, 'Edge': 2, 'Face': 3, 'Object': 4}
    hits = [(index, distance, depth) for index, (distance, depth) in distances.items()
            if not occlude or front is None or candidates[index].key[1] == 'Segment'
            or depth <= front + 1e-3 * max(1.0, abs(front))]
    hits.sort(key=lambda row: (priority[candidates[row[0]].key[1]], row[1], row[2], row[0]))
    return [candidates[index] for index, _distance, _depth in hits]
