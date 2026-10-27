"""Display-only analytic CPlane grid; no document geometry or snap changes.

MEASURED from grid.grid_segments: n=max(1,int(line_count)), half extent=n*spacing,
major interval=max(1,int(major_every)); only positive half-axes are colored.
OURS: one-pixel strokes, box-integrated coverage, density fade at 4..2 pixels
per period. Minor and major lattices fade independently, per plane direction.
The finite quad clips at the exact legacy bounds (including the outer line
centers); filtered endpoint coverage is inside those bounds, never an infinite
screen grid. Hardware clips the quad at the camera near/far planes.

Python is Blender integration and a test-only scalar reference, not a rendering
fallback. All per-fragment computation lives in GLSL (Rust cannot supply Blender
GPU derivatives). Importing this module needs no Blender/GPU context.

Call draw in POST_VIEW with world-space gpu.matrix state, after scene depth.
The caller retains the existing rendered-mode / pane visibility policy. Leave
face culling disabled, as for other two-sided viewport overlays. draw changes
only blend/depth-test/depth-mask and the model-view stack and restores those.
GPU failures raise to the caller: never silently substitute aliased lines.
"""
from __future__ import annotations

import math


VERTEX_SOURCE = """
void main()
{
    planeCoord = plane_co;
    gl_Position = gridMatrix * vec4(pos, 1.0);
}
"""

FRAGMENT_SOURCE = """
float intervalCoverage(float center, float footprint, float lo, float hi)
{
    float a = max(center - 0.5 * footprint, lo);
    float b = min(center + 0.5 * footprint, hi);
    return clamp((b - a) / footprint, 0.0, 1.0);
}

float densityFade(float footprint, float period)
{
    return 1.0 - smoothstep(0.25, 0.5, footprint / period);
}

/* Integral of a periodic pulse train. Clamp the integration domain to the
 * first/last REAL line, not to a rounded-up major grid extent. */
float pulseIntegral(float x, float period, float halfWidth)
{
    float cell = floor(x / period + 0.5);
    return cell * (2.0 * halfWidth)
         + clamp(x - cell * period + halfWidth, 0.0, 2.0 * halfWidth);
}

float latticeCoverage(float x, float footprint, float halfWidth,
                      float period, float lastLine)
{
    float lo = max(x - 0.5 * footprint, -lastLine - halfWidth);
    float hi = min(x + 0.5 * footprint, lastLine + halfWidth);
    float covered = max(0.0, pulseIntegral(hi, period, halfWidth)
                           - pulseIntegral(lo, period, halfWidth)) / footprint;
    /* Zero belongs to the split axes, even when no colored axes are shown. */
    float zero = intervalCoverage(x, footprint, -halfWidth, halfWidth);
    return clamp(covered - zero, 0.0, 1.0);
}

vec3 targetColor(vec3 c)
{
    if (!srgbTarget) { return c; }
    return mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)),
               step(vec3(0.04045), c));
}

vec3 blendInk(vec3 line)
{
    /* Convert BOTH colors first, never gamma-convert a ratio/difference. */
    vec3 bg = targetColor(backgroundColor);
    vec3 fg = targetColor(line);
    if (multiplyBlend) {
        return min(vec3(1.0), fg / max(bg, vec3(1e-20)));
    }
    return max(vec3(0.0), fg - bg);
}

void main()
{
    /* Derivatives precede discard and use the unwrapped, perspective-correct
     * coordinates: fract derivatives would spike at every line. L1 is a
     * conservative pixel box; L2 keeps stroke width independent of rotation. */
    vec2 q = planeCoord / gridParams.x;
    vec2 footprint = max(fwidth(q), vec2(1e-8));
    vec2 dx = dFdx(q), dy = dFdy(q);
    vec2 pixelSize = max(sqrt(dx * dx + dy * dy), vec2(1e-8));
    float n = gridParams.y;
    float every = gridParams.z;
    float lastMajor = floor(n / every) * every;
    vec2 halfWidth = min(0.5 * gridParams.w * pixelSize, vec2(0.25));
    vec2 majorHalf = min(0.5 * gridParams.w * pixelSize, vec2(0.25 * every));
    vec2 minor, majors;
    for (int i = 0; i < 2; ++i) {
        float allLines = latticeCoverage(q[i], footprint[i], halfWidth[i], 1.0, n);
        float excluded = latticeCoverage(q[i], footprint[i], halfWidth[i], every, lastMajor);
        minor[i] = max(0.0, allLines - excluded);
        /* Majors retain pixel width after minors have become too dense. */
        majors[i] = latticeCoverage(q[i], footprint[i], majorHalf[i], every, lastMajor);
    }
    minor *= vec2(densityFade(footprint.x, 1.0), densityFade(footprint.y, 1.0));
    majors *= vec2(densityFade(footprint.x, every), densityFade(footprint.y, every));
    /* Half-open split integrates to the exact origin..edge segment. The axes
     * are not periodic and must not disappear with the minor density fade. */
    vec2 axisHalf = 0.5 * gridParams.w * pixelSize;
    float uLine = intervalCoverage(q.x, footprint.x, -axisHalf.x, axisHalf.x);
    float vLine = intervalCoverage(q.y, footprint.y, -axisHalf.y, axisHalf.y);
    float posU = intervalCoverage(q.x, footprint.x, 0.0, n);
    float posV = intervalCoverage(q.y, footprint.y, 0.0, n);
    float negU = intervalCoverage(q.x, footprint.x, -n, 0.0);
    float negV = intervalCoverage(q.y, footprint.y, -n, 0.0);
    float minorCoverage = 1.0 - (1.0 - minor.x) * (1.0 - minor.y);
    float majorCoverage = 1.0 - (1.0 - majors.x) * (1.0 - majors.y);
    float xCoverage = vLine * posU;
    float yCoverage = uLine * posV;
    float originCoverage = 1.0 - (1.0 - vLine * (negU + (showAxes ? 0.0 : posU)))
                                * (1.0 - uLine * (negV + (showAxes ? 0.0 : posV)));
    majorCoverage = 1.0 - (1.0 - majorCoverage) * (1.0 - originCoverage);
    vec3 neutral = multiplyBlend ? vec3(1.0) : vec3(0.0);
    vec3 ink = neutral;
    if (showGrid) {
        ink = mix(ink, blendInk(minorColor), minorCoverage);
        ink = mix(ink, blendInk(majorColor), majorCoverage);
    }
    if (showAxes) {
        ink = mix(ink, blendInk(xAxisColor), xCoverage);
        ink = mix(ink, blendInk(yAxisColor), yCoverage);
    }
    if (any(greaterThan(abs(planeCoord), vec2(gridParams.x * n)))) { discard; }
    /* Coverage belongs in RGB: MULTIPLY/ADDITIVE have neutral 1/0, not alpha. */
    fragColor = vec4(ink, 1.0);
}
"""

# Public contract for offline interface checks and the caller's real GPU probe.
UNIFORMS = (
    ('MAT4', 'gridMatrix'),
    ('VEC4', 'gridParams'),  # spacing, clamped count, clamped major interval, px width
    ('VEC3', 'backgroundColor'), ('VEC3', 'minorColor'), ('VEC3', 'majorColor'),
    ('VEC3', 'xAxisColor'), ('VEC3', 'yAxisColor'),
    ('BOOL', 'showGrid'), ('BOOL', 'showAxes'), ('BOOL', 'multiplyBlend'),
    ('BOOL', 'srgbTarget'),  # Blender sets this on shader bind; do not override.
)
TRIANGLES = ((0, 1, 2), (0, 2, 3))
_shader = None
_batches = {}
CACHE_LIMIT = 16


def grid_parameters(settings, line_width=1.0):
    """Return shader params, or None for hidden/invalid spacing; match grid.py.

    Non-finite / GPU-unrepresentable dimensions are refused rather than sent to
    the GPU. Callers supply grid.GridSettings (spacing, not minor_spacing).
    """
    if not settings.show_grid and not settings.show_axes:
        return None
    step = float(settings.spacing)
    if not math.isfinite(step) or step <= 0.0:
        return None
    n = max(1, int(settings.line_count))
    every = max(1, int(settings.major_every))
    width = float(line_width)
    if not math.isfinite(width) or width <= 0.0:
        raise ValueError('line_width must be finite and positive')
    extent = step * n
    if not 1e-30 <= step <= extent <= 1e30 or every > 1e30:
        raise ValueError('grid dimensions exceed the supported GPU float range')
    return step, float(n), float(every), width


def quad_data(plane, extent):
    """Four world vertices and local UVs, only O(1) Blender batch preparation."""
    uv = ((-extent, -extent), (extent, -extent),
          (extent, extent), (-extent, extent))
    return {'pos': tuple(plane.to_world(u, v) for u, v in uv), 'plane_co': uv}


def create_shader_info(gpu):
    """Build Blender's interface, without compiling it (also used by stubs)."""
    interface = gpu.types.GPUStageInterfaceInfo('narwhal_grid_interface')
    interface.smooth('VEC2', 'planeCoord')
    info = gpu.types.GPUShaderCreateInfo()
    for kind, name in UNIFORMS:
        info.push_constant(kind, name)
    info.vertex_in(0, 'VEC3', 'pos')
    info.vertex_in(1, 'VEC2', 'plane_co')
    info.vertex_out(interface)
    info.fragment_out(0, 'VEC4', 'fragColor')
    info.vertex_source(VERTEX_SOURCE)
    info.fragment_source(FRAGMENT_SOURCE)
    return info


def clear_cache():
    """Release cached resources on unregister/context replacement; no GPU import."""
    global _shader
    _batches.clear()
    _shader = None


def _rgb01(color):
    rgb = tuple(float(c) / 255.0 for c in color)
    if len(rgb) != 3 or not all(math.isfinite(c) and 0.0 <= c <= 1.0 for c in rgb):
        raise ValueError('grid colors must be sRGB byte triples in 0..255')
    return rgb


def draw(plane, settings, region, rv3d, see_through=False, *,
         background=None, minor=None, major=None, xaxis=None, yaxis=None,
         blend=None, line_width=1.0, depth_bias=1e-4, metrics=None) -> bool:
    """Draw a finite filtered grid; True means one batch submitted, not visible pixels.

    Color keywords are sRGB byte RGB triples, defaulting to CURRENT design tokens
    on every call. blend is MULTIPLY or ADDITIVE; None chooses from minor/bg.
    depth_bias is the legacy away-from-camera offset times max(view_distance,1e-3).
    region is the current WINDOW region; derivatives use actual framebuffer pixels
    without CPU viewport-size projection. rv3d supplies view_matrix, view_location,
    view_distance and is_perspective. Call with the normal POST_VIEW gpu.matrix.

    False = hidden/invalid spacing/missing or zero-sized region or rv3d. Errors
    (including unavailable GPU) propagate; the caller should report them. Optional
    metrics dict receives submitted, reason, extent, vertices, triangles, cache_hit,
    blend and depth_test. No readback, timing query, or fragment-count claim.
    """
    global _shader
    if metrics is not None:
        metrics.clear()
        metrics.update(submitted=False, reason='not drawable', vertices=0, triangles=0)
    params = grid_parameters(settings, line_width)
    if params is None or region is None or rv3d is None:
        return False
    if region.width <= 0 or region.height <= 0:
        return False
    if not math.isfinite(depth_bias) or depth_bias < 0:
        raise ValueError('depth_bias must be finite and nonnegative')
    if any(c is None for c in (background, minor, major, xaxis, yaxis)):
        from . import design
        defaults = (design.VIEWPORT_BG, design.GRID_MINOR, design.GRID_MAJOR,
                    design.AXIS_X, design.AXIS_Y)
        colors = tuple(d if c is None else c for c, d in
                       zip((background, minor, major, xaxis, yaxis), defaults))
    else:
        colors = (background, minor, major, xaxis, yaxis)
    colors = tuple(_rgb01(c) for c in colors)
    if blend is None:
        blend = 'MULTIPLY' if all(m <= b for m, b in zip(colors[1], colors[0])) else 'ADDITIVE'
    if blend not in ('MULTIPLY', 'ADDITIVE'):
        raise ValueError('grid blend must be MULTIPLY or ADDITIVE')
    import gpu
    from gpu_extras.batch import batch_for_shader

    if _shader is None:
        _shader = gpu.shader.create_from_info(create_shader_info(gpu))
    extent = params[0] * params[1]
    key = (tuple(plane.origin), tuple(plane.xaxis), tuple(plane.yaxis), extent)
    batch = _batches.get(key)
    hit = batch is not None
    if not hit:
        batch = batch_for_shader(_shader, 'TRIS', quad_data(plane, extent), indices=TRIANGLES)
        if len(_batches) >= CACHE_LIMIT:
            _batches.clear()
        _batches[key] = batch
    inv = rv3d.view_matrix.inverted()
    if rv3d.is_perspective:
        away = (rv3d.view_location - inv.translation).normalized()
    else:
        away = -inv.col[2].xyz.normalized()
    nudge = away * (depth_bias * max(rv3d.view_distance, 1e-3))
    old_blend = gpu.state.blend_get()
    old_depth = gpu.state.depth_test_get()
    old_mask = gpu.state.depth_mask_get()
    depth = 'ALWAYS' if see_through else 'LESS_EQUAL'
    gpu.matrix.push()
    try:
        gpu.matrix.translate(tuple(nudge))
        gpu.state.depth_test_set(depth)
        gpu.state.depth_mask_set(False)
        gpu.state.blend_set(blend)
        _shader.bind()
        _shader.uniform_float('gridMatrix', gpu.matrix.get_projection_matrix()
                              @ gpu.matrix.get_model_view_matrix())
        _shader.uniform_float('gridParams', params)
        for name, color in zip(('backgroundColor', 'minorColor', 'majorColor',
                                'xAxisColor', 'yAxisColor'), colors):
            _shader.uniform_float(name, color)
        _shader.uniform_bool('showGrid', bool(settings.show_grid))
        _shader.uniform_bool('showAxes', bool(settings.show_axes))
        _shader.uniform_bool('multiplyBlend', blend == 'MULTIPLY')
        batch.draw(_shader)
    finally:
        gpu.matrix.pop()
        gpu.state.blend_set(old_blend)
        gpu.state.depth_test_set(old_depth)
        gpu.state.depth_mask_set(old_mask)
    if metrics is not None:
        metrics.update(submitted=True, reason='drawn', extent=extent, vertices=4,
                       triangles=2, cache_hit=hit, blend=blend, depth_test=depth)
    return True


# Test-only mathematical oracle. draw never calls these per-sample routines.
def interval_coverage(center, footprint, lo, hi):
    footprint = max(abs(footprint), 1e-8)
    a = max(center - 0.5 * footprint, lo)
    b = min(center + 0.5 * footprint, hi)
    return max(0.0, min(1.0, (b - a) / footprint))


def density_fade(footprint, period):
    t = max(0.0, min(1.0, (abs(footprint) / period - 0.25) / 0.25))
    return 1.0 - t * t * (3.0 - 2.0 * t)


def lattice_coverage(x, footprint, half_width, period, last_line):
    """Box-integrated finite periodic strokes, excluding the zero stroke."""
    footprint = max(abs(footprint), 1e-8)
    def integral(value):
        cell = math.floor(value / period + 0.5)
        return cell * (2.0 * half_width) + max(
            0.0, min(2.0 * half_width, value - cell * period + half_width))
    lo = max(x - 0.5 * footprint, -last_line - half_width)
    hi = min(x + 0.5 * footprint, last_line + half_width)
    covered = max(0.0, integral(hi) - integral(lo)) / footprint
    zero = interval_coverage(x, footprint, -half_width, half_width)
    return max(0.0, min(1.0, covered - zero))


def reference_coverage(uv, duv_dx, duv_dy, settings, line_width=1.0):
    """CPU reference: minor, major, xaxis, yaxis coverage in world plane units.

    Derivatives are analytic screen-space UV derivatives, NOT camera distance.
    The production shader obtains them with fwidth/dFdx/dFdy.
    """
    params = grid_parameters(settings, line_width)
    if params is None:
        return (0.0,) * 4
    step, n, every, width = params
    if any(abs(c) > step * n for c in uv):
        return (0.0,) * 4
    q = tuple(c / step for c in uv)
    footprint = tuple(max((abs(a) + abs(b)) / step, 1e-8)
                      for a, b in zip(duv_dx, duv_dy))
    pixel = tuple(max(math.hypot(a, b) / step, 1e-8)
                  for a, b in zip(duv_dx, duv_dy))
    minor, major = [], []
    for x, f, p in zip(q, footprint, pixel):
        half = min(0.5 * width * p, 0.25)
        all_lines = lattice_coverage(x, f, half, 1.0, n)
        last = math.floor(n / every) * every
        excluded = lattice_coverage(x, f, half, every, last)
        major_half = min(0.5 * width * p, 0.25 * every)
        majors = lattice_coverage(x, f, major_half, every, last)
        minor.append(max(0.0, all_lines - excluded) * density_fade(f, 1.0))
        major.append(majors * density_fade(f, every))
    u_line, v_line = (interval_coverage(x, f, -0.5 * width * p, 0.5 * width * p)
                      for x, f, p in zip(q, footprint, pixel))
    pos_u, pos_v = (interval_coverage(x, f, 0.0, n) for x, f in zip(q, footprint))
    neg_u, neg_v = (interval_coverage(x, f, -n, 0.0) for x, f in zip(q, footprint))
    origin = 1.0 - (1.0 - v_line * (neg_u + (0.0 if settings.show_axes else pos_u))) * (
        1.0 - u_line * (neg_v + (0.0 if settings.show_axes else pos_v)))
    return ((1.0 - (1.0 - minor[0]) * (1.0 - minor[1])) if settings.show_grid else 0.0,
            (1.0 - (1.0 - major[0]) * (1.0 - major[1]) * (1.0 - origin))
            if settings.show_grid else 0.0,
            v_line * pos_u if settings.show_axes else 0.0,
            u_line * pos_v if settings.show_axes else 0.0)


def reference_ink(line, background, coverage, blend, srgb_target=False):
    """RGB shader output reference; byte inputs, RGB coverage, opaque alpha."""
    def target(c):
        if not srgb_target:
            return c
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    fg = tuple(target(c) for c in _rgb01(line))
    bg = tuple(target(c) for c in _rgb01(background))
    if blend == 'MULTIPLY':
        return tuple(1.0 + coverage * (min(1.0, c / max(b, 1e-20)) - 1.0)
                     for c, b in zip(fg, bg))
    if blend == 'ADDITIVE':
        return tuple(coverage * max(0.0, c - b) for c, b in zip(fg, bg))
    raise ValueError('grid blend must be MULTIPLY or ADDITIVE')
