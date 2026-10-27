"""Real GPU display regression; bootstrap must precede this script, never use -b.

Run SERIAL inside the harness's GUI lock (main owns harness integration):
  PYTHONDONTWRITEBYTECODE=1 timeout 180s tools/blender-patched/blender \
    --factory-startup --enable-event-simulate --python-exit-code 1 \
    --python launch/bootstrap.py --python spike/test_render_quality_gui.py

Mutation proof, same command with one environment variable prepended:
  NARWHAL_RENDER_QUALITY_MUTATION=raw-grid
  NARWHAL_RENDER_QUALITY_MUTATION=flat-lines
  NARWHAL_RENDER_QUALITY_MUTATION=flat-preview
Each must exit 1 with failing PIXEL checks, then the normal run must exit 0.
flat-preview changes only the actual preview callback's submission path; completed
line AA stays enabled. Expected normal count: 19 grid + 19 line + 9 preview = 47.
Mutations are process-local patches restored in finally; no production file edits.

Artifacts: render_quality_verdict.txt, render_quality.json, render_quality_grid.png.
PNG rows: dense pitches in ascending order; columns: raw, filtered, raw temporal
standard deviation, filtered temporal standard deviation (std images scaled x4).
The JSON records every threshold's inputs, GPU identity and 32 extra synchronized
render/readback timings. Timings are diagnostics, not portable performance gates.

OURS: quality tolerances, not CAD behavior claims. GPU readback is the oracle, NOT
source inspection or grid_render's scalar reference. Python is necessary for
Blender timers/GPU bindings; no document geometry is created or changed. This
covers the grid renderer, completed-line dispatch and actual live-preview callback,
not text/icons. Preview inputs are temporary; no fake GPU calls or raster oracle.
"""
import json
import math
import os
from pathlib import Path
import struct
import sys
import threading
import time
import traceback
from types import SimpleNamespace
from unittest.mock import patch
import zlib

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'artifacts'
W = H = 128
START = time.monotonic()
MODE = os.environ.get('NARWHAL_RENDER_QUALITY_MUTATION', '')
RESULT = {'status': 'running', 'mutation': MODE, 'checks': [], 'measurements': {}}
FINISH_LOCK = threading.Lock()
PHASES = tuple((i / 8.0, ((3 * i) % 8) / 8.0) for i in range(8))
WHITE, BLACK = (255, 255, 255), (0, 0, 0)


def check(ok, label, **measurements):
    RESULT['checks'].append(dict(ok=bool(ok), label=label, **measurements))
    print(f"RENDER_QUALITY {'PASS' if ok else 'FAIL'} {label} "
          + json.dumps(measurements), flush=True)


def finish(code, error=None):
    if not FINISH_LOCK.acquire(blocking=False):
        os._exit(1)
    try:
        RESULT.update(status='failed' if code else 'passed', error=error,
                      elapsed_s=time.monotonic() - START)
        OUT.mkdir(exist_ok=True)
        (OUT / 'render_quality.json').write_text(json.dumps(RESULT, indent=2), encoding='utf-8')
        (OUT / 'render_quality_verdict.txt').write_text('FAILED' if code else 'PASSED',
                                                      encoding='utf-8')
        print('RENDER_QUALITY_RESULT ' + json.dumps(RESULT), flush=True)
    finally:
        # Blender swallows timer exceptions and quit_blender can exit 0 on failures.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)


def watchdog():
    # No Blender/RNA/GPU access off the main thread; also bounds a stuck GPU call.
    time.sleep(150)
    finish(1, '150s process watchdog expired')


def camera(pitch, phase=(0.0, 0.0), perspective=False):
    """DERIVED: world XY at z=0 projects at pitch pixels/unit in either camera."""
    from mathutils import Matrix, Vector

    distance = 100.0
    location = Vector((phase[0] / pitch, phase[1] / pitch, 0.0))
    view = Matrix.Translation((-location.x, -location.y, -distance))
    if perspective:
        near, far = 0.1, 1000.0
        f = 2.0 * pitch * distance / W
        proj = Matrix(((f, 0, 0, 0), (0, f, 0, 0),
                       (0, 0, (far + near) / (near - far), 2 * far * near / (near - far)),
                       (0, 0, -1, 0)))
    else:
        proj = Matrix(((2 * pitch / W, 0, 0, 0), (0, 2 * pitch / H, 0, 0),
                       (0, 0, -0.001, 0), (0, 0, 0, 1)))
    rv = SimpleNamespace(view_matrix=view, view_location=location,
                         view_distance=distance, is_perspective=perspective,
                         perspective_matrix=proj @ view, window_matrix=proj)
    return view, proj, rv


def pixel_camera():
    from mathutils import Matrix

    return Matrix.Identity(4), Matrix(((2 / W, 0, 0, -1), (0, 2 / H, 0, -1),
                                      (0, 0, -0.001, 0), (0, 0, 0, 1)))


class Target:
    """Single-sample float target: no MSAA/TAA/color-management hiding aliasing."""

    def __init__(self):
        import gpu
        self.offscreen = gpu.types.GPUOffScreen(W, H, format='RGBA32F')

    def render(self, draw, view, proj):
        import gpu
        import numpy as np

        old = dict(blend=gpu.state.blend_get(), depth=gpu.state.depth_test_get(),
                   mask=gpu.state.depth_mask_get(), width=gpu.state.line_width_get(),
                   viewport=gpu.state.viewport_get(), scissor=gpu.state.scissor_get())
        try:
            with self.offscreen.bind():
                gpu.state.viewport_set(0, 0, W, H)
                # There is no scissor-enable getter. Do NOT toggle it: a full-target
                # rectangle works whether scissoring was enabled or disabled.
                gpu.state.scissor_set(0, 0, W, H)
                gpu.state.depth_mask_set(True)
                fb = gpu.state.active_framebuffer_get()
                fb.clear(color=(1, 1, 1, 1), depth=1.0)
                gpu.state.depth_mask_set(False)
                gpu.state.depth_test_set('NONE')
                gpu.state.blend_set('ALPHA')
                gpu.state.line_width_set(1.0)
                with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
                    gpu.matrix.load_matrix(view)
                    gpu.matrix.load_projection_matrix(proj)
                    draw()
                buf = fb.read_color(0, 0, W, H, 4, 0, 'FLOAT')
                buf.dimensions = W * H * 4
                rgba = np.array(buf, dtype=np.float32).reshape(H, W, 4)
        finally:
            gpu.state.blend_set(old['blend'])
            gpu.state.depth_test_set(old['depth'])
            gpu.state.depth_mask_set(old['mask'])
            gpu.state.line_width_set(old['width'])
            gpu.state.viewport_set(*old['viewport'])
            gpu.state.scissor_set(*old['scissor'])
        if not np.isfinite(rgba).all():
            raise AssertionError('non-finite GPU framebuffer pixels')
        ink = 1.0 - rgba[..., 0]
        if ink.min() < -1e-4 or ink.max() > 1.0001:
            raise AssertionError(f'coverage outside [0,1]: {ink.min()}, {ink.max()}')
        return ink

    def free(self):
        self.offscreen.free()


def raw_grid(plane, settings, region, rv3d, see_through=False, *,
             background=WHITE, minor=BLACK, major=BLACK, xaxis=BLACK, yaxis=BLACK,
             blend='MULTIPLY', line_width=1.0, depth_bias=0.0, metrics=None):
    """The negative control: legacy finite grid_segments -> builtin raw LINES.

    Fixtures use white background, neutral white or black strokes, and no axes.
    Endpoints avoid sRGB ambiguity without forcing Blender's srgbTarget uniform.
    """
    import gpu
    from gpu_extras.batch import batch_for_shader
    from narwhal3d import grid as _grid

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    old_blend = gpu.state.blend_get()
    old_width = gpu.state.line_width_get()
    vertices = 0
    try:
        gpu.state.blend_set(blend)
        gpu.state.line_width_set(line_width)
        for kind, segments in _grid.grid_segments(plane, settings).items():
            pts = [point for segment in segments for point in segment]
            if not pts:
                continue
            shader.bind()
            color = dict(minor=minor, major=major, xaxis=xaxis, yaxis=yaxis)[kind]
            shader.uniform_float('color', tuple(c / 255 for c in color) + (1.0,))
            batch_for_shader(shader, 'LINES', {'pos': pts}).draw(shader)
            vertices += len(pts)
    finally:
        gpu.state.blend_set(old_blend)
        gpu.state.line_width_set(old_width)
    if metrics is not None:
        metrics.update(vertices=vertices, triangles=0, submitted=bool(vertices))
    return bool(vertices)


def draw_line(ui, points):
    shader, aa = ui._line_shader()
    batch = ui._line_batch(shader, aa, points)
    ui._line_begin(shader, aa)
    ui._line_width(shader, aa, 1.0)
    shader.uniform_float('color', (0.0, 0.0, 0.0, 1.0))
    batch.draw(shader)


def flat_line(points):
    import gpu
    from gpu_extras.batch import batch_for_shader

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    shader.bind()
    shader.uniform_float('color', (0.0, 0.0, 0.0, 1.0))
    batch_for_shader(shader, 'LINES', {'pos': points}).draw(shader)


def flat_preview_callback(ui):
    """Compile the old raw submission into ONLY this callback, never its helpers.

    Source AST is used solely to inject the negative control, not as the oracle.
    Keep all context guards, input resolution, color, GPU state and exception paths.
    In particular, do not patch _line_shader/_line_batch or disable SMOOTH_LINES:
    the completed reference must still render real AA while this callback is raw.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(ui.draw_command_preview)))

    class RawPreview(ast.NodeTransformer):
        shaders = 0
        batches = 0

        def visit_Call(self, node):
            self.generic_visit(node)
            if isinstance(node.func, ast.Name) and node.func.id == '_line_shader':
                self.shaders += 1
                return ast.copy_location(ast.parse('(_uniform_color_shader(), False)',
                                                   mode='eval').body, node)
            if isinstance(node.func, ast.Name) and node.func.id == '_line_batch':
                self.batches += 1
                return ast.copy_location(ast.Call(
                    func=ast.Name(id='batch_for_shader', ctx=ast.Load()),
                    args=[node.args[0], ast.Constant(value='LINES'),
                          ast.Dict(keys=[ast.Constant(value='pos')], values=[node.args[2]])],
                    keywords=[]), node)
            return node

    mutation = RawPreview()
    tree = mutation.visit(tree)
    if (mutation.shaders, mutation.batches) != (1, 2):
        raise AssertionError('flat-preview expects one shader dispatch and two preview batches')
    namespace = {}
    exec(compile(ast.fix_missing_locations(tree), '<flat-preview mutation>', 'exec'),
         ui.__dict__, namespace)
    return namespace['draw_command_preview']


def draw_completed_preview_reference(ui, polyline, color):
    """Use completed curves' renderer dispatch, without inserting a document object."""
    coords = [point for pair in zip(polyline, polyline[1:]) for point in pair]
    shader, aa = ui._line_shader()
    batch = ui._line_batch(shader, aa, coords)
    ui._line_begin(shader, aa)
    ui._line_width(shader, aa, 1.0)
    shader.uniform_float('color', color)
    batch.draw(shader)


def preview_checks(target):
    import numpy as np
    from contextlib import ExitStack
    from narwhal3d import cplane, ui

    window = bpy.context.window
    area = next((a for a in window.screen.areas if a.type == 'VIEW_3D'), None)
    region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None
    if bpy.app.background or region is None:
        raise AssertionError('preview requires a real VIEW_3D WINDOW region in a live GUI')
    callback = flat_preview_callback(ui) if MODE == 'flat-preview' else ui.draw_command_preview
    plane = cplane.CPlane()
    view, proj = pixel_camera()
    yy, xx = np.mgrid[:H, :W] + .5
    theta = math.radians(23)
    dx, dy = 48 * math.cos(theta), 48 * math.sin(theta)
    line = [(64.23 - dx, 63.87 - dy, 0), (64.23 + dx, 63.87 + dy, 0)]
    bent = [(18.23, 25.87, 0), (59.23, 89.87, 0), (108.23, 46.87, 0)]
    for name, points, exact in (('Line', line, False), ('Polyline', bent, False),
                                ('ExactPolyline', bent, True)):
        # Freeze input, NOT the drawing path. Ordinary fixtures call the real
        # preview.segments with picked points/cursor. The exact fixture freezes
        # only ghost_polylines' geometry-to-display resolution: no OCCT/document
        # object is needed to exercise its distinct GPU submission branch.
        color = (.92, .92, .92, 1.0) if exact else (0.0, 0.0, 0.0, 1.0)
        contrast = 1.0 - color[0]
        samples = []
        for px, py in PHASES:
            poly = [(x + px, y + py, z) for x, y, z in points]
            cmd = SimpleNamespace(name=name, args=poly[:-1])
            token = object()
            if exact:
                cmd.preview_geometry = lambda: token

            def draw_preview():
                # No patch/override survives this synchronous draw, even if it
                # raises. Never yield to Blender's timers with fake state active.
                with ExitStack() as stack:
                    stack.enter_context(bpy.context.temp_override(window=window, area=area,
                                                                  region=region))
                    stack.enter_context(patch.object(ui.app_state, 'active_command', cmd))
                    stack.enter_context(patch.object(ui, '_preview_cursor', None if exact else poly[-1]))
                    stack.enter_context(patch.object(ui, '_preview_cplane', return_value=plane))
                    stack.enter_context(patch.object(ui, 'PREVIEW_COLOR', color))
                    if exact:
                        def resolve(geometry):
                            if geometry is not token:
                                raise AssertionError('exact preview getter was bypassed')
                            return [poly]
                        stack.enter_context(patch.object(ui, 'ghost_polylines', side_effect=resolve))
                    stack.enter_context(patch.object(ui, 'draw_command_preview', callback))
                    ui.draw_command_preview()

            completed = target.render(lambda: draw_completed_preview_reference(ui, poly, color),
                                      view, proj) / contrast
            image = target.render(draw_preview, view, proj) / contrast
            length = sum(math.dist(a, b) for a, b in zip(poly, poly[1:]))
            distance = np.full((H, W), np.inf)
            for (ax, ay, _), (bx, by, _) in zip(poly, poly[1:]):
                t = np.clip(((xx - ax) * (bx - ax) + (yy - ay) * (by - ay))
                            / ((bx - ax)**2 + (by - ay)**2), 0, 1)
                distance = np.minimum(distance, np.hypot(xx - ax - t * (bx - ax),
                                                        yy - ay - t * (by - ay)))
            samples.append(dict(phase=(px, py), max_error=float(np.abs(image - completed).max()),
                                preview_partial=int(((image > .05) & (image < .95)).sum()),
                                completed_partial=int(((completed > .05) & (completed < .95)).sum()),
                                preview_width=float(image.sum()) / length,
                                completed_width=float(completed.sum()) / length,
                                distant_ink=float(np.abs(image[distance > 1.6]).sum())))
            yield .01
        RESULT['measurements'][f'preview_{name}'] = samples
        # OURS: float readback tolerance (including low-contrast exact previews),
        # not a visual approximation. A raw callback cannot match the AA reference.
        check(all(s['max_error'] <= 2e-5 for s in samples),
              f'preview {name}: actual callback pixels equal completed-line dispatch in every phase',
              max_error=max(s['max_error'] for s in samples), tolerance=2e-5)
        check(all(s['preview_partial'] > 30 and s['completed_partial'] > 30 for s in samples),
              f'preview {name}: callback and completed reference have positive partial GPU coverage',
              preview_min=min(s['preview_partial'] for s in samples),
              completed_min=min(s['completed_partial'] for s in samples))
        check(ui.CURVE_WIRE_WIDTH == 1.0 and all(
                  .85 <= s['preview_width'] <= 1.15 and .85 <= s['completed_width'] <= 1.15
                  and s['preview_width'] <= s['completed_width'] + 2e-5
                  and s['distant_ink'] < .01 for s in samples),
              f'preview {name}: width one, no thicker than completed, no wide fringe',
              preview_widths=[s['preview_width'] for s in samples],
              completed_widths=[s['completed_width'] for s in samples],
              distant_ink=max(s['distant_ink'] for s in samples))


def save_grid_png(rows):
    """Encode measured grayscale pixels directly; no scene/image datablock changes."""
    import numpy as np

    image = np.concatenate([np.concatenate(row, axis=1) for row in rows], axis=0)
    gray = np.rint(255 * (1 - np.clip(image, 0, 1))).astype(np.uint8)[::-1]
    height, width = gray.shape

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack(
            '>I', zlib.crc32(kind + data) & 0xffffffff)

    data = b''.join(b'\0' + row.tobytes() for row in gray)
    png = (b'\x89PNG\r\n\x1a\n'
           + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(data)) + chunk(b'IEND', b''))
    (OUT / 'render_quality_grid.png').write_bytes(png)


def run_checks():
    import gpu
    import numpy as np
    from narwhal3d import cplane, grid, grid_render, ui

    RESULT['gpu'] = dict(vendor=gpu.platform.vendor_get(), renderer=gpu.platform.renderer_get(),
                         version=gpu.platform.version_get(), target='RGBA32F, single sample')
    plane = cplane.CPlane()
    settings = grid.GridSettings(spacing=1.0, line_count=100, major_every=5, show_axes=False)
    region = SimpleNamespace(width=W, height=H)
    target = Target()
    rows = []
    metrics = {}

    def render_grid(renderer, pitch, phase=(0, 0), *, minor=BLACK, major=BLACK,
                    cfg=settings, perspective=False, report=None):
        view, proj, rv = camera(pitch, phase, perspective)

        def draw():
            submitted = renderer(plane, cfg, region, rv, background=WHITE,
                                 minor=minor, major=major, xaxis=BLACK, yaxis=BLACK,
                                 blend='MULTIPLY', depth_bias=0.0, metrics=report)
            if not submitted:
                raise AssertionError('visible grid fixture submitted no draw')
        return target.render(draw, view, proj)

    try:
        yy, xx = np.mgrid[:H, :W] + 0.5
        for pitch in (0.30, 0.57, 0.73, 0.89):
            # Neutral majors isolate the vanished subpixel minor lattice. Unlike
            # a blank-render test, independent coarse/major fixtures MUST draw ink.
            raw = np.stack([render_grid(raw_grid, pitch, p, major=WHITE) for p in PHASES])
            filtered = np.stack([render_grid(grid_render.draw, pitch, p, major=WHITE,
                                            report=metrics) for p in PHASES])
            limit = min(48, 100 * pitch - 4)
            roi = (abs(xx - W / 2) < limit) & (abs(yy - H / 2) < limit)
            r, f = raw[:, roi], filtered[:, roi]
            raw_var, filtered_var = float(r.var(axis=0).mean()), float(f.var(axis=0).mean())
            raw_mean, filtered_mean = float(r.mean()), float(f.mean())
            mean_drift = float(np.ptp(f.mean(axis=1)))
            check(raw_mean > 0.1, f'{pitch}: raw dense-grid control actually inks pixels',
                  raw_mean=raw_mean)
            # At .30 every raster row/column may be covered; do not pretend that
            # saturated control measures temporal aliasing. The other pitches must.
            if pitch > 0.5:
                check(raw_var > 0.001, f'{pitch}: raw control has measurable temporal aliasing',
                      raw_variance=raw_var)
                check(filtered_var <= 0.20 * raw_var + 1e-7,
                      f'{pitch}: filtered temporal variance <= 20% of raw',
                      filtered_variance=filtered_var, raw_variance=raw_var)
            check(filtered_mean < 0.002 and mean_drift < 0.001,
                  f'{pitch}: subpixel minors suppressed with stable coverage',
                  filtered_mean=filtered_mean, mean_drift=mean_drift)
            rows.append([raw[0], filtered[0], raw.std(axis=0) * 4, filtered.std(axis=0) * 4])
            yield 0.01
        save_grid_png(rows)

        # Majors remain a visible one-pixel lattice at 5*.89 = 4.45 px. Compare
        # coverage with the DERIVED union fraction 2/d - 1/d^2, not CPU GLSL emulation.
        majors = np.stack([render_grid(grid_render.draw, .89, p, minor=WHITE) for p in PHASES])
        roi = ((xx > 12) & (xx < 116) & (yy > 12) & (yy < 116)
               & (abs(xx - 64) > 3) & (abs(yy - 64) > 3))
        means = majors[:, roi].mean(axis=1)
        expected = 2 / 4.45 - 1 / 4.45 ** 2
        check(abs(float(means.mean()) - expected) < 0.04 and float(np.ptp(means)) < 0.025,
              'visible majors retain approximately one-pixel integral coverage while panning',
              expected=expected, frame_means=means.tolist())
        check(int(((majors > .05) & (majors < .95)).sum()) > 100,
              'visible majors have GPU partial-coverage edges')

        perspective = render_grid(grid_render.draw, .89, PHASES[0], minor=WHITE, perspective=True)
        check(float(np.abs(perspective - majors[0]).max()) < .003,
              'perspective top-plane projection agrees with matched orthographic scale',
              max_error=float(np.abs(perspective - majors[0]).max()))
        finite_cfg = grid.GridSettings(spacing=1, line_count=6, major_every=5, show_axes=False)
        finite = render_grid(grid_render.draw, 8, cfg=finite_cfg)
        outside = (abs(xx - 64) > 49) | (abs(yy - 64) > 49)
        check(float(finite.sum()) > 500 and float(np.abs(finite[outside]).max()) < 1e-5,
              'finite world-top quad draws coarse lines but no ink beyond its +/-48px bounds',
              ink=float(finite.sum()), outside_max=float(np.abs(finite[outside]).max()))
        raw_metrics = {}
        render_grid(raw_grid, .89, report=raw_metrics)
        check(metrics.get('vertices') == 4 and metrics.get('triangles') == 2
              and raw_metrics.get('vertices') == 808,
              'submitted grid draw: four quad vertices versus 808 raw line vertices',
              filtered=metrics.copy(), raw=raw_metrics)
        yield 0.01

        view, proj = pixel_camera()
        widths = []
        for angle in (0, 7, 23, 45, 77, 90):
            theta = math.radians(angle)
            dx, dy = 48 * math.cos(theta), 48 * math.sin(theta)
            points = [(64.23 - dx, 63.87 - dy, 0), (64.23 + dx, 63.87 + dy, 0)]
            aa = target.render(lambda: draw_line(ui, points), view, proj)
            flat = target.render(lambda: flat_line(points), view, proj)
            partial = int(((aa > .05) & (aa < .95)).sum())
            flat_partial = int(((flat > .05) & (flat < .95)).sum())
            width = float(aa.sum()) / 96.0
            widths.append(width)
            # Distance to the segment, including ends: a wider/streaked shader
            # cannot pass just by making all its pixels proportionally fainter.
            ax, ay, _ = points[0]
            bx, by, _ = points[1]
            t = np.clip(((xx - ax) * (bx - ax) + (yy - ay) * (by - ay)) / 96**2, 0, 1)
            distance = np.hypot(xx - (ax + t * (bx - ax)), yy - (ay + t * (by - ay)))
            check(flat_partial == 0 and float(flat.sum()) > 50,
                  f'{angle}deg: raw LINES are visible and have zero partial pixels',
                  partial=flat_partial, ink=float(flat.sum()))
            check(partial > 30, f'{angle}deg: app line shader has partial GPU coverage',
                  partial=partial)
            check(.85 <= width <= 1.15 and float(aa[distance > 1.6].sum()) < .01,
                  f'{angle}deg: thin line integral approximately equals length; no wide fringe',
                  equivalent_width=width, distant_ink=float(aa[distance > 1.6].sum()))
            yield 0.01
        check(max(widths) - min(widths) < .10, 'one-pixel line coverage is angle-independent',
              equivalent_widths=widths)

        yield from preview_checks(target)

        # Warmed draws; readback synchronizes completion. Includes Python/batch/
        # binding/readback overhead, deliberately NOT advertised as GPU-only time.
        timings = {'filtered_ms': [], 'raw_ms': []}
        for i in range(32):
            key, renderer = (('filtered_ms', grid_render.draw) if i % 2 == 0
                             else ('raw_ms', raw_grid))
            start = time.perf_counter()
            render_grid(renderer, .89, PHASES[(i // 2) % 8])
            timings[key].append((time.perf_counter() - start) * 1000)
            if i % 8 == 7:
                yield .01
        RESULT['measurements']['warm_render_readback'] = timings
        RESULT['measurements']['median_ms'] = {k: float(np.median(v)) for k, v in timings.items()}
    finally:
        target.free()


def run():
    from narwhal3d import grid_render, ui

    deadline = time.monotonic() + 60
    while not (os.environ.get('NARWHAL3D_UI_READY') == '1'
               and not ui._viewports_in_progress and ui._viewport_count() == 4
               and ui.is_input_active() and bool(ui._toolbar_tab_boxes)):
        if time.monotonic() > deadline:
            raise AssertionError('60s timeout waiting for screenshot-ready GUI/layout/input')
        yield .1
    # Readiness includes drawn toolbar boxes; allow another event-loop/redraw turn.
    yield .25
    if MODE == 'raw-grid':
        mutation = patch.object(grid_render, 'draw', raw_grid)
    elif MODE == 'flat-lines':
        mutation = patch.object(ui, 'SMOOTH_LINES', False)
    else:
        from contextlib import nullcontext
        mutation = nullcontext()
    with mutation:
        yield from run_checks()


RUNNER = None


def tick():
    try:
        if time.monotonic() - START > 120:
            raise AssertionError('120s timer deadline exceeded')
        return next(RUNNER)
    except StopIteration:
        failed = not RESULT['checks'] or any(not c['ok'] for c in RESULT['checks'])
        finish(1 if failed else 0)
    except BaseException:
        error = traceback.format_exc()
        print(error, file=sys.stderr, flush=True)
        try:
            RUNNER.close()
        finally:
            finish(1, error)
    return None


if __name__ == '__main__':
    OUT.mkdir(exist_ok=True)
    (OUT / 'render_quality_verdict.txt').write_text('RUNNING', encoding='utf-8')
    (OUT / 'render_quality.json').write_text(json.dumps(RESULT), encoding='utf-8')
    if bpy.app.background or '--enable-event-simulate' not in sys.argv:
        finish(1, 'real Blender GUI with bootstrap and --enable-event-simulate required')
    if MODE not in ('', 'raw-grid', 'flat-lines', 'flat-preview'):
        finish(1, f'unknown mutation mode: {MODE!r}')
    threading.Thread(target=watchdog, daemon=True).start()
    RUNNER = run()
    bpy.app.timers.register(tick, first_interval=.25)
