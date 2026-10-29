"""OURS display-only straight-extrusion guides; never document geometry.

prepare([(geometry, None | ('face', one_based_id)), ...]) -> Session.
Session.update(axis, signed_distance) stores only the latest scalar/vector state.
Session.draw(region, rv3d, rgba) draws cached GPU lines in POST_VIEW or POST_PIXEL
using rv3d.perspective_matrix (world -> clip); no per-point Python projection.

Whole profiles retain their near/far outlines and sparse endpoint/corner generators.
A closed smooth profile has one generator at its authored parameter seam. Face guides
are provisional prisms, NOT predictions of the final boolean. Main must run the exact
backend on release, and discard this session on cancel/source or selection change.
Python orchestrates existing kernel sampling once and packs GPU buffers; all dynamic
vertex displacement, projection and AA math executes in the existing GPU runtime.
"""
from __future__ import annotations

import math

from . import bridge

MAX_JOBS = 256
MAX_CURVES = 4096
MAX_POINTS = 100_000
_gpu = None


class PreviewError(ValueError):
    """Named preparation/render refusal, never a silently missing preview."""


def _xyz(point):
    return (point.X, point.Y, point.Z)


def _leaves(curve):
    import rhino3dm as r3
    stack = [curve]
    seen = 0
    while stack:
        item = stack.pop()
        seen += 1
        if seen > MAX_CURVES:
            raise PreviewError('too many profile segments for a bounded extrusion preview')
        if isinstance(item, r3.PolyCurve):
            count = item.SegmentCount
            if count < 1 or count + len(stack) > MAX_CURVES:
                raise PreviewError('empty or oversized PolyCurve')
            stack.extend(item.SegmentCurve(i) for i in reversed(range(count)))
        else:
            yield item


def prepare(jobs):
    """Snapshot existing curve/face boundaries once; no extrusion, mesh or registration.

    jobs is an iterable of (rhino3dm geometry, None | ('face', id)). Face IDs are
    OCCT's existing 1-based unique-face IDs, never triangulated proxy face indices.
    Whole polysurfaces, non-face parts and absent exact operands refuse by job number.
    Missing source recipes/masters are not reconstructed from proxy triangles.

    Returns a CPU-prepared session; optionally call session.prepare_gpu() in a GPU
    context on press. Otherwise the first draw uploads its immutable buffers once.
    No kernel handle or source object is retained in the returned session.
    """
    import rhino3dm as r3
    polylines, connectors, loops = [], {}, []
    provisional = False
    total = 0

    def add_curve(curve):
        nonlocal total
        for leaf in _leaves(curve):
            if not leaf.IsValid:
                raise PreviewError('invalid profile curve')
            if len(polylines) >= MAX_CURVES:
                raise PreviewError('too many boundary curves')
            vertices = bridge._polyline_vertices(leaf)
            points = tuple(bridge._curve_display_points(leaf, bridge.display_tolerance(leaf)))
            if len(points) < 2:
                raise PreviewError('profile has no drawable boundary')
            total += len(points)
            if total > MAX_POINTS:
                raise PreviewError('extrusion preview exceeds the 100000-point display budget')
            polylines.append(points)
            # Real polyline corners and authored segment endpoints only. Sampling a
            # smooth curve more finely must never create a forest of generators.
            anchors = vertices if vertices is not None else (
                [_xyz(leaf.PointAtStart)] if leaf.IsClosed else
                [_xyz(leaf.PointAtStart), _xyz(leaf.PointAtEnd)])
            for anchor in anchors:
                connectors.setdefault(tuple(anchor), None)

    for index, job in enumerate(jobs):
        if index >= MAX_JOBS:
            raise PreviewError('extrusion preview exceeds the 256-source budget')
        try:
            geom, part = job
            if isinstance(geom, r3.Curve):
                if part is not None:
                    raise PreviewError('curve subparts must be detached curves, not face jobs')
                first = len(polylines)
                add_curve(geom)
                loops.append((first, len(polylines)))
                continue
            if not isinstance(geom, (r3.Brep, r3.Extrusion, r3.Surface)):
                raise PreviewError(f'{type(geom).__name__} is not an extrusion profile or face')
            occt = bridge._occt()
            if occt is None:
                raise PreviewError('face preview needs the OCCT kernel')
            shape = bridge._occt_operand(geom)
            if shape is None:
                raise PreviewError('face has no exact master or rebuildable recipe')
            faces = occt.unique_faces(shape)
            if part is None:
                if len(faces) != 1:
                    raise PreviewError('whole polysurface extrusion needs one selected face')
                face = faces[0]
            else:
                if (not isinstance(part, tuple) or len(part) != 2 or part[0] != 'face'
                        or type(part[1]) is not int or not 1 <= part[1] <= len(faces)):
                    raise PreviewError('expected a valid 1-based face id')
                face = faces[part[1] - 1]
                if occt.face_plane(face) is None:
                    raise PreviewError('Gumball push/pull of a curved face is not implemented yet')
            provisional = True
            boundaries = occt.face_loops(face)
            if not boundaries or any(not loop for loop in boundaries):
                raise PreviewError('exact face has no supported boundary loops')
            for loop in boundaries:
                first = len(polylines)
                for recipe in loop:
                    add_curve(bridge.curve_from_recipe(recipe))
                loops.append((first, len(polylines)))
        except (ValueError, RuntimeError, TypeError) as exc:
            raise PreviewError(f'extrusion preview job {index + 1}: {exc}') from exc
    return Session(tuple(polylines), tuple(connectors), tuple(loops), provisional)


class Session:
    """Immutable prepared outlines plus latest axis/distance and one cached GPU batch.

    polylines, connectors and loop_ranges are read-only tuples for diagnostics/tests.
    They contain SOURCE positions only; no transformed Python geometry is generated.
    bool(session) reports nonempty prepared data, including at distance zero.
    """
    def __init__(self, polylines, connectors, loop_ranges, provisional):
        import numpy as np
        self.polylines = polylines
        self.connectors = connectors
        self.loop_ranges = loop_ranges
        self.provisional = provisional
        self.axis = (0.0, 0.0, 1.0)
        self.distance = 0.0
        self._batch = None
        # Pack once; the factor at each segment endpoint selects the near/far position.
        pairs = [(a, b) for line in polylines for a, b in zip(line, line[1:])]
        near = np.asarray(pairs, dtype=np.float32).reshape(-1, 2, 3)
        anchors = np.asarray(connectors, dtype=np.float32).reshape(-1, 3)
        generators = np.repeat(anchors[:, None, :], 2, axis=1)
        self._segments = np.concatenate((near, near, generators))
        self._factors = np.concatenate((np.zeros((len(near), 2), dtype=np.float32),
                                        np.ones((len(near), 2), dtype=np.float32),
                                        np.tile(np.array([0, 1], dtype=np.float32),
                                                (len(anchors), 1))))
        if not np.isfinite(self._segments).all():
            raise PreviewError('source boundary is outside finite GPU display coordinates')
        self._segments.flags.writeable = False
        self._factors.flags.writeable = False

    def __bool__(self):
        return bool(len(self._segments))

    def update(self, axis, distance):
        """Latest-value replacement, O(1); axis may be non-unit, distance is signed.

        Invalid input resets distance to zero before raising, never leaving an old
        nonzero extrusion displayed as the rejected new input. No geometry APIs run.
        """
        self.distance = 0.0
        try:
            if len(axis) != 3:
                raise ValueError('axis must have three components')
            direction = tuple(float(value) for value in axis)
            value = float(distance)
            if (len(direction) != 3 or not all(math.isfinite(v) for v in direction)
                    or not math.isfinite(value) or not 1e-30 < math.hypot(*direction) < 1e30
                    or max(map(abs, direction)) > 1e30 or abs(value) > 1e30):
                raise ValueError('nonfinite, zero or out-of-range axis/distance')
        except (TypeError, ValueError, OverflowError) as exc:
            raise PreviewError(f'extrusion preview: invalid axis/distance ({exc})') from exc
        self.axis, self.distance = direction, value

    def prepare_gpu(self):
        """Upload once in Blender's GPU context; optional prewarm on press, no kernel."""
        if self._batch is not None or not self:
            return
        import numpy as np
        from gpu_extras.batch import batch_for_shader
        from . import ui
        shader = _shader()
        pos, other, side = ui._aa_line_arrays(self._segments.reshape(-1, 3))
        end = np.array([0, 0, 1, 0, 1, 1])
        self._batch = batch_for_shader(shader, 'TRIS', {
            'pos': pos, 'other': other, 'side': side,
            'factor': self._factors[:, end].reshape(-1),
            'other_factor': self._factors[:, 1 - end].reshape(-1)})

    def draw(self, region, rv3d, color):
        """Orange-style overlay in either draw pass; restore blend/depth state afterward.

        World-to-clip is explicit, so POST_PIXEL does NOT require Python projection.
        Main controls lifetime/visibility and calls this even with no active command.
        Drawing performs only uniform/state changes and one cached batch submission.
        """
        if not self:
            return
        import gpu
        self.prepare_gpu()
        shader = _shader()
        blend, depth, mask = gpu.state.blend_get(), gpu.state.depth_test_get(), gpu.state.depth_mask_get()
        try:
            gpu.state.blend_set('ALPHA')
            gpu.state.depth_test_set('NONE')
            gpu.state.depth_mask_set(False)
            shader.bind()
            shader.uniform_float('viewProjection', rv3d.perspective_matrix)
            shader.uniform_float('viewport', (float(max(region.width, 1)), float(max(region.height, 1))))
            shader.uniform_float('halfw', .5)
            shader.uniform_float('color', color)
            shader.uniform_float('extrusion', (*self.axis, self.distance))
            self._batch.draw(shader)
        finally:
            gpu.state.depth_mask_set(mask)
            gpu.state.depth_test_set(depth)
            gpu.state.blend_set(blend)


def warm_gpu():
    """Compile the shared shader in an idle GPU callback BEFORE the first drag.

    First-use driver compilation can exceed a frame even for tiny geometry. Main
    may call this once when Gumball becomes available; no source/kernel is needed.
    """
    _shader()


def _shader():
    global _gpu
    if _gpu is not None:
        return _gpu
    import gpu
    from . import ui
    # Reuse the application's clipped 1-pixel AA shader, adding only display-space
    # extrusion offsets on GPU. No fork of its AA/color/projection implementation.
    # Scale before normalization so a valid large/small uniform axis cannot overflow
    # or underflow float32's dot product. CPU update only validates/copies uniforms.
    vertex = '''
vec3 preview_offset()
{
  vec3 a = extrusion.xyz;
  a /= max(max(abs(a.x), abs(a.y)), abs(a.z));
  return normalize(a) * extrusion.w;
}
''' + ui._AA_LINE_VERT_SRC
    for old, new in (
        ('vec4(pos, 1.0)', 'vec4(pos + preview_offset() * factor, 1.0)'),
        ('vec4(other, 1.0)', 'vec4(other + preview_offset() * other_factor, 1.0)'),
    ):
        if vertex.count(old) != 1:
            raise PreviewError('application AA shader interface changed; extrusion preview needs rebinding')
        vertex = vertex.replace(old, new)
    vertex = vertex.replace('ModelViewProjectionMatrix', 'viewProjection')
    interface = gpu.types.GPUStageInterfaceInfo('narwhal_extrude_preview')
    interface.no_perspective('FLOAT', 'v_d')
    info = gpu.types.GPUShaderCreateInfo()
    for kind, name in (('MAT4', 'viewProjection'), ('VEC4', 'color'), ('VEC4', 'extrusion'),
                       ('VEC2', 'viewport'), ('FLOAT', 'halfw'), ('BOOL', 'srgbTarget')):
        info.push_constant(kind, name)
    for slot, kind, name in ((0, 'VEC3', 'pos'), (1, 'VEC3', 'other'), (2, 'FLOAT', 'side'),
                             (3, 'FLOAT', 'factor'), (4, 'FLOAT', 'other_factor')):
        info.vertex_in(slot, kind, name)
    info.vertex_out(interface)
    info.fragment_out(0, 'VEC4', 'fragColor')
    info.vertex_source(vertex)
    info.fragment_source(ui._AA_LINE_FRAG_SRC)
    try:
        _gpu = gpu.shader.create_from_info(info)
    except Exception as exc:
        raise PreviewError(f'extrusion preview GPU shader unavailable: {exc}') from exc
    return _gpu
