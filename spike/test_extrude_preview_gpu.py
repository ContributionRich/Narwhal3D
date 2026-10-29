"""Focused real-GPU offscreen preview gates; run in an OWN factory Blender window.

Acquire /tmp/narwhal3d_gui.lock externally, then run existing Blender --factory-startup
--threads 2 --python spike/test_extrude_preview_gpu.py under a bounded timeout.
No app bootstrap, user input, screenshots/files, native builds or document commits.
Output/exit code are the verdict. Tests current AA shader source without loading UI.
"""
from contextlib import ExitStack
import os
from pathlib import Path
import sys
import time
import traceback
import types
from unittest.mock import patch

import bpy
import gpu
import numpy as np
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_extrude_preview import bridge, examples, immutable_sources, preview, shader_ui

W = H = 256
REGION = types.SimpleNamespace(width=W, height=H)
RV3D = types.SimpleNamespace(perspective_matrix=Matrix(
    ((2 / W, 0, 0, -1), (0, 2 / H, 0, -1), (0, 0, -.001, 0), (0, 0, 0, 1))))


def render(session):
    offscreen = gpu.types.GPUOffScreen(W, H)
    try:
        with offscreen.bind():
            fb = gpu.state.active_framebuffer_get()
            fb.clear(color=(0, 0, 0, 0), depth=0.0)
            # Deliberately wrong pixel-view matrices: the helper MUST use RV3D's
            # explicit world projection and overlay above an already occupied depth.
            with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
                gpu.matrix.load_matrix(Matrix.Identity(4))
                gpu.matrix.load_projection_matrix(Matrix.Identity(4))
                state = gpu.state.blend_get(), gpu.state.depth_test_get(), gpu.state.depth_mask_get()
                session.draw(REGION, RV3D, (1.0, .5, 0.0, 1.0))
                assert state == (gpu.state.blend_get(), gpu.state.depth_test_get(), gpu.state.depth_mask_get())
            buf = fb.read_color(0, 0, W, H, 4, 0, 'FLOAT')
            buf.dimensions = W * H * 4
            return np.array(buf).reshape(H, W, 4)
    finally:
        offscreen.free()


def ink(image, x, y):
    return float(image[max(0, y - 1):y + 2, max(0, x - 1):x + 2, 3].max())


def rectangle_witness(image, *, far_y=96, left=32, right=128):
    assert ink(image, 80, 64) > .15, 'missing near outline'
    assert ink(image, 80, far_y) > .15, 'missing far outline / wrong latest distance'
    middle = (64 + far_y) // 2
    assert ink(image, left, middle) > .15, 'missing left connector'
    assert ink(image, right, middle) > .15, 'missing right connector'
    assert ink(image, 80, middle) < .01, 'extrusion guide filled its interior'


def run():
    try:
        ui = shader_ui()
        print('GPU backend:', gpu.platform.backend_type_get(), gpu.platform.renderer_get(), flush=True)
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {'narwhal3d.ui': ui}))
            stack.enter_context(patch.object(sys.modules['narwhal3d'], 'ui', ui, create=True))
            started = time.perf_counter()
            preview.warm_gpu()
            print('GPU idle shader prewarm ms:', (time.perf_counter() - started) * 1000, flush=True)
            source = bridge.make_line((32, 64, 0), (128, 64, 0))
            source_before = source.Encode()
            session = preview.prepare([(source, None)])
            started = time.perf_counter()
            session.prepare_gpu()
            print('GPU prepared-session upload ms:', (time.perf_counter() - started) * 1000, flush=True)
            batch = session._batch
            for owner, names in ((bridge, ('gumball_extrude', '_occt_operand', '_proxy_from_occt',
                                          '_curve_display_points', 'curve_from_recipe', 'subobject_parts')),):
                for name in names:
                    stack.enter_context(patch.object(owner, name, side_effect=AssertionError(name)))
            session.update((0, 2, 0), 15)
            session.update((0, 2, 0), 32)
            image = render(session)
            rectangle_witness(image)
            assert session._batch is batch, 'batch rebuilt'
            alpha = image[:, 80, 3]
            assert 0 < alpha.sum() <= 3.3, 'not thin one-pixel outlines'
            assert np.any((image[..., 3] > .05) & (image[..., 3] < .95)), 'AA fringe missing'
            assert ink(image, 80, 79) < .01, 'old distance still visible'
            print('GPU PASS near/far/connectors, latest value, AA/thin lines, overlay depth, state restoration', flush=True)
            for magnitude in (2e-25, 2e25):
                session.update((0, magnitude, 0), 32)
                rectangle_witness(render(session))
            session.update((0, 1, 0), -24)
            rectangle_witness(render(session), far_y=40)
            session.update((3, 4, 0), 40)
            image = render(session)
            assert ink(image, 100, 96) > .15, 'rotated far outline missing'
            assert ink(image, 44, 80) > .15 and ink(image, 140, 80) > .15, 'wrong rotated axis'
            assert ink(image, 32, 80) < .01, 'old unrotated connector retained'
            session.update((0, 1, 0), 0)
            image = render(session)
            assert ink(image, 80, 64) > .15 and ink(image, 80, 96) < .01
            assert source.Encode() == source_before
            print('GPU PASS signed/nonunit/rotated axes, zero distance and unchanged source', flush=True)

        # Full source cases: no proxy/recipe/master/triangulation changes in preparation/draw.
        with patch.dict(sys.modules, {'narwhal3d.ui': ui}), \
             patch.object(sys.modules['narwhal3d'], 'ui', ui, create=True):
            for name, jobs in examples().items():
                before = immutable_sources(jobs)
                session = preview.prepare(jobs)
                session.update((0, 1, 0), 20)
                image = render(session)
                assert image[..., 3].max() > .15, name
                assert immutable_sources(jobs) == before, name
                print('GPU PASS source fixture', name, flush=True)

            # Separate loops/holes must ink BOTH near and far inner boundaries.
            outer = bridge.make_circle((64, 150, 0), 25)
            inner = bridge.make_circle((64, 150, 0), 10)
            annulus = bridge.make_planar_srf([outer, inner])[0]
            session = preview.prepare([(annulus, None)])
            session.update((1, 0, 0), 100)
            image = render(session)
            for x, y in ((64, 175), (64, 160), (164, 175), (164, 160)):
                assert ink(image, x, y) > .15, ('lost annular outline', x, y)
            print('GPU PASS exact annulus near/far outer AND inner boundaries', flush=True)

            # Re-break the GPU-visible inputs, not just a bool or a call-count spy.
            for mutation in ('far', 'connectors', 'axis', 'latest'):
                session = preview.prepare([(source, None)])
                session.update((0, 1, 0), 32)
                if mutation in ('far', 'connectors'):
                    factors = session._factors.copy()
                    if mutation == 'far':
                        factors[1] = 0
                    else:
                        factors[2:] = 0
                    session._factors = factors
                elif mutation == 'axis':
                    session.axis = (1, 0, 0)
                else:
                    session.distance = 15
                try:
                    rectangle_witness(render(session))
                except AssertionError as exc:
                    print('GPU MUTATION RED', mutation, str(exc), flush=True)
                else:
                    raise AssertionError('visible mutation was not detected: ' + mutation)
                restored = preview.prepare([(source, None)])
                restored.update((0, 1, 0), 32)
                rectangle_witness(render(restored))
            print('GPU 4 visible mutations RED, restored GREEN', flush=True)
        memory = [line for line in Path('/proc/self/status').read_text().splitlines()
                  if line.startswith(('VmHWM:', 'VmRSS:'))]
        assert all(int(line.split()[1]) < 3 * 1024**2 for line in memory), memory
        print('GPU memory (<3 GiB resident):', memory, flush=True)
        print('EXTRUDE PREVIEW GPU PASSED', flush=True)
        sys.stdout.flush()
        os._exit(0)
    except BaseException:
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)


bpy.app.timers.register(run, first_interval=.5)
