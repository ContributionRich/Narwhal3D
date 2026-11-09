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
    bounds: object = None


def _polyline(points):
    # Application curve segments use rhino3dm Point3d, unlike tuple/BVH samples.
    return tuple((float(point.X), float(point.Y), float(point.Z))
                 if hasattr(point, 'X') else tuple(float(v) for v in point)
                 for point in points)


def _candidate(key, label, lines=(), triangles=(), points=()):
    from mathutils.bvhtree import BVHTree
    triangles = tuple(_polyline(t) for t in triangles)
    tree = None
    lines, points = tuple(_polyline(p) for p in lines), _polyline(points)
    vertices = [p for t in triangles for p in t]
    if triangles:
        tree = BVHTree.FromPolygons(vertices,
            [(i, i + 1, i + 2) for i in range(0, len(vertices), 3)], all_triangles=True)
    import numpy as np
    packed = np.asarray(vertices + [p for line in lines for p in line] + list(points))
    bounds = (packed.min(axis=0), packed.max(axis=0)) if packed.size else None
    return Candidate(key, label, lines, triangles, points, tree, bounds)


_cache = {}


def clear():
    _cache.clear()


def _mesh_display(ob):
    """Read Blender's existing display buffers; no geometry is constructed."""
    if getattr(ob, 'type', None) != 'MESH':
        return (), ()
    mesh = ob.data
    mesh.calc_loop_triangles()
    vertices = [tuple(ob.matrix_world @ v.co) for v in mesh.vertices]
    return ([tuple(vertices[i] for i in e.vertices) for e in mesh.edges],
            [tuple(vertices[i] for i in t.vertices) for t in mesh.loop_triangles])


def collect(ui, mode):
    """Prepare immutable display data once per document/visibility/mode signature."""
    import bpy
    import rhino3dm as r3
    blocked = ui._unpickable_names_cached()
    rows = ui._doc_rows()
    # The UI already observes display/depsgraph changes (including hide_set and
    # app-drawn curves). Reuse its revision rather than rescanning every Blender
    # object on every click. Minimal compatibility hosts without it still verify
    # visibility explicitly below.
    dg = getattr(ui, '_curve_dg', None)
    revision = ((ui._doc_key(), id(rows), len(rows), frozenset(blocked), mode,
                 dg['serial'], bpy.context.view_layer.as_pointer()) if dg is not None else None)
    if revision is not None and _cache.get('revision') == revision:
        return _cache['candidates']
    visible = tuple(row for row in rows
                    if (ob := bpy.data.objects.get(row[0])) is not None
                    and ui.bridge.display_visible(ob))
    signature = (ui._doc_key(), tuple((row[0], str(row[4].Id)) for row in visible),
                 frozenset(blocked), mode)
    if dg is None and _cache.get('rows') is rows and _cache.get('signature') == signature:
        _cache['revision'] = revision
        return _cache['candidates']
    parts_by_name = None
    out = []
    for name, _state, _layer, geom, _attrs in visible:
        if isinstance(geom, r3.Curve):
            if name in blocked:
                continue
            if mode == 'object':
                out.append(_candidate((name, 'Object', 0), name + ' — Curve',
                                      lines=(ui._curve_points(geom),)))
            elif mode == 'part':
                for index, points, _grips in ui.bridge.curve_segments(geom):
                    out.append(_candidate((name, 'Segment', index),
                        f'{name} — Segment {index + 1}', lines=(points,)))
            continue
        if isinstance(geom, r3.Point):
            if name in blocked:
                continue
            if mode == 'object':
                out.append(_candidate((name, 'Object', 0), name + ' — Point',
                                      points=((geom.Location.X, geom.Location.Y, geom.Location.Z),)))
            continue
        if parts_by_name is None:
            parts_by_name = ui._brep_parts_all()
        parts = parts_by_name.get(name) or {}
        if mode == 'object' or name in blocked:
            lines = [points for _index, points in parts.get('edges', ())]
            triangles = [t for _index, ts in parts.get('faces', ()) for t in ts]
            if not lines and not triangles:
                # Mesh/Picture display data are already Blender-native. They do
                # not imply analytic geometry or editable BRep subobjects.
                lines, triangles = _mesh_display(bpy.data.objects.get(name))
            if lines or triangles:
                kind = 'Occluder' if name in blocked else 'Object'
                out.append(_candidate((name, kind, 0), name + ' — ' + type(geom).__name__,
                                      lines, triangles))
        else:
            if not parts.get('faces'):
                # Pictures/meshes still obscure curve segments and BRep parts,
                # even when they expose no selectable exact subobjects.
                _lines, triangles = _mesh_display(bpy.data.objects.get(name))
                if triangles:
                    out.append(_candidate((name, 'Occluder', 0), '', triangles=triangles))
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
        # Keep separate hit witnesses for separate drawn segments. A hidden
        # nearest branch must not erase another visible branch in the aperture.
        spans = tuple((a, b) for points in spans for a, b in zip(points, points[1:]))
        polylines.extend(spans)
        indices.extend([index] * len(spans))
    # Blender's C BVH owns depth traversal, including locked opaque objects.
    # Cache construction is Blender/display orchestration, never document geometry.
    from mathutils.bvhtree import BVHTree
    import numpy as np
    vertices = [p for candidate in out for triangle in candidate.triangles for p in triangle]
    depth_tree = (BVHTree.FromPolygons(vertices,
        [(i, i + 1, i + 2) for i in range(0, len(vertices), 3)], all_triangles=True)
        if vertices else None)
    lower = np.asarray([c.bounds[0] for c in out], dtype=np.float64).reshape(-1, 3)
    upper = np.asarray([c.bounds[1] for c in out], dtype=np.float64).reshape(-1, 3)
    packed_segments = np.asarray(polylines, dtype=np.float64).reshape(-1, 2, 3)
    result = (tuple(out), tuple(polylines), tuple(indices))
    _cache.update(rows=rows, revision=revision, signature=signature, candidates=result, depth_tree=depth_tree,
                  lower=lower, upper=upper, surface_view=None, local_key=None,
                  segment_lower=packed_segments.min(axis=1), segment_upper=packed_segments.max(axis=1),
                  segment_extent=np.ones(len(polylines), dtype=np.uint8),
                  candidate_extent=np.ones(len(out), dtype=np.uint8))
    return result


def _fallback_hits(lines, matrix, width, height, cursor):
    """Complete scalar compatibility path only when the existing Rust query is absent.

    Clipping and projective interpolation mirror selection_core/snap_clip.rs;
    production computation belongs to that already-qualified native query.
    """
    from math import hypot
    from mathutils import Vector
    for points in lines:
        best = None
        for a, b in zip(points, points[1:]):
            ca = matrix @ Vector((*a, 1.0))
            cb = matrix @ Vector((*b, 1.0))
            start, end = 0.0, 1.0
            for component in range(3):
                for sign in (-1, 1):
                    fa, fb = ca.w + sign * ca[component], cb.w + sign * cb[component]
                    if fa < 0 and fb < 0:
                        end = -1.0
                    elif fa < 0:
                        start = max(start, fa / (fa - fb))
                    elif fb < 0:
                        end = min(end, fa / (fa - fb))
            if start > end:
                continue
            p, q = ca.lerp(cb, start), ca.lerp(cb, end)
            if p.w <= 0 or q.w <= 0:
                continue
            px, py = (p.x / p.w + 1) * width / 2, (p.y / p.w + 1) * height / 2
            qx, qy = (q.x / q.w + 1) * width / 2, (q.y / q.w + 1) * height / 2
            dx, dy = qx - px, qy - py
            length = dx * dx + dy * dy
            t = (max(0.0, min(1.0, ((cursor[0] - px) * dx + (cursor[1] - py) * dy) / length))
                 if length else 0.0)
            distance = hypot(cursor[0] - px - t * dx, cursor[1] - py - t * dy)
            if best is None or distance < best[1]:
                local = t * p.w / ((1 - t) * q.w + t * p.w)
                world_t = start + (end - start) * local
                best = (tuple(av + (bv - av) * world_t for av, bv in zip(a, b)), distance)
        yield best


def at(ui, area, space, region, x, y, mode='object'):
    """OURS (owner policy): aperture intersection AND visibility at that hit.

    Visibility elsewhere on an object is not evidence at the click. Wireframe
    has no opaque face depth; shaded picks, including curves/points, cannot see
    through opaque display faces. Document geometry remains untouched.
    """
    from mathutils import Vector
    from . import native_selection
    rv = getattr(space, 'region_3d', None)
    if (rv is None or region is None
            or not (0 <= x < region.width and 0 <= y < region.height)):
        return []
    candidates, lines, indices = collect(ui, mode)
    tolerance = ui.SUBOBJECT_PICK_PX if mode != 'object' else ui.CURVE_PICK_PX
    # Existing Rust box queries reject remote candidates before Python unpacks
    # hit rows or invokes BVHs. Bounds are rejection-only, never acceptance.
    view_key = (tuple(tuple(row) for row in rv.perspective_matrix), region.width, region.height)
    if _cache['surface_view'] != view_key:
        _cache['surface_rects'] = native_selection.screen_rects_from_aabbs(
            _cache['lower'], _cache['upper'], rv.perspective_matrix, region.width, region.height)
        _cache['segment_rects'] = native_selection.screen_rects_from_aabbs(
            _cache['segment_lower'], _cache['segment_upper'], rv.perspective_matrix,
            region.width, region.height)
        _cache['surface_view'] = view_key
    rects = _cache['surface_rects']
    reached = (native_selection.reached_screen_rect_indices(
        rects, _cache['candidate_extent'], (x, y), tolerance, margin=0.0)
        if rects is not None else None)
    if reached is None:  # Compatibility only; production iteration is over local survivors.
        reached = tuple(range(len(candidates)))
    segment_rects = _cache['segment_rects']
    local = (native_selection.reached_screen_rect_indices(
        segment_rects, _cache['segment_extent'], (x, y), tolerance, margin=0.0)
        if segment_rects is not None else None)
    if local is None:
        local = tuple(range(len(lines)))
    if _cache['local_key'] != local:
        _cache.update(local_key=local, local_lines=tuple(lines[i] for i in local),
                      local_indices=tuple(indices[i] for i in local))
    lines, indices = _cache['local_lines'], _cache['local_indices']
    measured = native_selection.nearest_projected_polylines(
        lines, rv.perspective_matrix, region.width, region.height, (x, y),
        with_distances=True, clipped=True)
    if measured is None:
        measured = _fallback_hits(lines, rv.perspective_matrix, region.width, region.height, (x, y))
    depth_tree = _cache['depth_tree'] if space.shading.type != 'WIREFRAME' else None
    distances = {}
    inverse = rv.perspective_matrix.inverted()

    def ray_at(xy):
        # Mathutils owns unprojection. Start at the near clip plane, not behind
        # clipped foreground geometry; the far plane bounds traversal too.
        nx, ny = xy[0] * 2 / region.width - 1, xy[1] * 2 / region.height - 1
        a = inverse @ Vector((nx, ny, -1, 1))
        b = inverse @ Vector((nx, ny, 1, 1))
        origin, end = a.xyz / a.w, b.xyz / b.w
        delta = end - origin
        return origin, delta.normalized(), delta.length

    def record(index, point, distance):
        projected = ui._project_with_depth(region, rv, point)
        if (projected is None or not (0 <= projected[0] < region.width
                                     and 0 <= projected[1] < region.height)):
            return
        # Raycast at the actual tolerance hit, not the cursor centre. A tiny
        # foreground face may cover one but not the other.
        depth = ui._view_depth(rv, point)
        if depth_tree is not None:
            xy = projected[:2]
            origin, direction, limit = ray_at(xy)
            front = depth_tree.ray_cast(origin, direction, limit)[0]
            if front is not None:
                front_depth = ui._view_depth(rv, front)
                if depth > front_depth + 1e-5 * max(1.0, abs(front_depth)):
                    return
        old = distances.get(index)
        if old is None or (distance, depth) < old:
            distances[index] = (distance, depth)

    for index, hit in zip(indices, measured):
        if hit is not None and hit[1] <= tolerance:
            record(index, hit[0], hit[1])

    # Only a real triangle intersection can establish a face-interior hit.
    origin, direction, limit = ray_at((x, y))
    for index in reached:
        if candidates[index].tree is None or candidates[index].key[1] == 'Occluder':
            continue
        point = candidates[index].tree.ray_cast(origin, direction, limit)[0]
        if point is not None:
            record(index, point, 0.0)
    priority = {'Vertex': 0, 'Segment': 1, 'Edge': 2, 'Face': 3, 'Object': 4}
    hits = [(index, distance, depth) for index, (distance, depth) in distances.items()]
    hits.sort(key=lambda row: (priority[candidates[row[0]].key[1]], row[1], row[2], row[0]))
    return [candidates[index] for index, _distance, _depth in hits]
