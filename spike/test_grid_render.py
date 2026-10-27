#!/usr/bin/env python3
"""No GPU/context/dependencies: mathematical and strict GPU-interface grid tests.

Run: python3 -B spike/test_grid_render.py
     python3 -B spike/test_grid_render.py --mutations

The optional mutation run compiles broken copies IN MEMORY, observes failures,
then reruns the restored module. It never edits source or leaves stale bytecode.
The stub validates the Blender call contract, NOT real GLSL compilation/pixels.
"""
import importlib.util
import io
import math
from pathlib import Path
import random
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = '_grid_render_tests'
pkg = types.ModuleType(PACKAGE)
pkg.__path__ = [str(ROOT / 'narwhal3d')]
sys.modules[PACKAGE] = pkg


def load(name, source=None):
    path = ROOT / 'narwhal3d' / (name + '.py')
    spec = importlib.util.spec_from_file_location(PACKAGE + '.' + name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    # Source execution deliberately bypasses both reading and writing .pyc files.
    exec(compile(path.read_text() if source is None else source, str(path), 'exec'),
         module.__dict__)
    return module


grid = load('grid')
cplane = load('cplane')
render = load('grid_render')


def overlap(center, footprint, lo, hi):
    """Independent interval intersection (not the periodic antiderivative)."""
    return max(0, min(center + footprint / 2, hi)
               - max(center - footprint / 2, lo)) / footprint


def enumerated_lattice(x, footprint, half, period, last):
    count = round(last / period)
    return sum(overlap(x, footprint, i * period - half, i * period + half)
               for i in range(-count, count + 1) if i)


class CoverageTests(unittest.TestCase):
    def test_finite_integral_against_independent_stripe_sum(self):
        rng = random.Random(431)
        for period in (1, 3, 10):
            for count in (0, 1, 7, 20):
                last = count * period
                for _ in range(80):
                    x = rng.uniform(-last - period * 2, last + period * 2)
                    footprint = rng.uniform(.01, period * 7)
                    half = rng.uniform(.001, period * .25)
                    with self.subTest(period=period, count=count, x=x):
                        self.assertAlmostEqual(
                            render.lattice_coverage(x, footprint, half, period, last),
                            enumerated_lattice(x, footprint, half, period, last), places=10)

    def test_subpixel_filter_is_continuous_and_conserves_stroke_area(self):
        values = [render.lattice_coverage(1 + i * .01, .2, .1, 1, 3)
                  for i in range(-20, 21)]
        self.assertAlmostEqual(values[20], 1)
        self.assertAlmostEqual(values[25], .75)
        self.assertAlmostEqual(values[30], .5)
        self.assertLessEqual(max(abs(a - b) for a, b in zip(values, values[1:])), .050001)
        self.assertAlmostEqual(sum(values) * .01, .2)

        for a, b in zip(values, reversed(values)):
            self.assertAlmostEqual(a, b)

    def test_density_endpoints_monotonic_and_smooth(self):
        for period in (1, 5, 10):
            values = [render.density_fade(period * i / 1000, period) for i in range(701)]
            self.assertEqual(values[0], 1)
            self.assertEqual(values[250], 1)
            self.assertAlmostEqual(values[375], .5)
            self.assertEqual(values[500], 0)
            self.assertEqual(values[700], 0)
            self.assertTrue(all(a >= b for a, b in zip(values, values[1:])))
            self.assertLess(abs(values[251] - values[250]), .00005)
            self.assertLess(abs(values[500] - values[499]), .00005)

    def test_normal_zoom_keeps_minor_and_major_lines(self):
        settings = grid.GridSettings(line_count=23, major_every=5)
        for pixels in (8, 22, 26, 100):
            derivative = 1 / pixels
            for coord, kind in ((1, 0), (5, 1), (-2, 0), (-10, 1)):
                cov = render.reference_coverage((coord, 2.4), (derivative, 0),
                                                (0, derivative), settings)
                self.assertAlmostEqual(cov[kind], 1)
                self.assertAlmostEqual(cov[1 - kind], 0)
                self.assertEqual(cov[2:], (0, 0))

    def test_major_pixel_width_survives_minor_fade(self):
        settings = grid.GridSettings(major_every=10)
        for footprint in (.5, 1, 2, 2.5):
            cov = render.reference_coverage((10, 14.4), (footprint, 0),
                                            (0, .05), settings)
            self.assertEqual(cov[0], 0)
            self.assertAlmostEqual(cov[1], 1)

    def test_nyquist_phase_stability_and_anisotropic_fade(self):
        settings = grid.GridSettings(line_count=1000, major_every=10)
        for footprint in (5, 10, 100, 10000):
            for phase in (0, .1, .23, .5, .9):
                cov = render.reference_coverage((30 + phase, 30.4), (footprint, 0),
                                                (0, footprint), settings)
                self.assertEqual(cov[0], 0)
                # Nonperiodic axes intentionally survive, so test far from axes
                # while periodic coverage is checked directly for huge footprints.
                if footprint <= 10:
                    self.assertEqual(cov[1], 0)
                self.assertEqual(render.density_fade(footprint, 10), 0)
                self.assertTrue(all(math.isfinite(c) and 0 <= c <= 1 for c in cov))
        cov = render.reference_coverage((31, 1), (5, 0), (0, .05), settings)
        self.assertAlmostEqual(cov[0], 1)  # resolved horizontal lines remain
        cov = render.reference_coverage((31, 1.4), (5, 0), (0, .05), settings)
        self.assertEqual(cov[0], 0)  # unresolved vertical minors disappear

    def test_l2_width_l1_filter_at_rotated_pixel(self):
        d = .05 / math.sqrt(2)
        cov = render.reference_coverage((1, 2.4), (d, d), (-d, d), grid.GridSettings())
        self.assertAlmostEqual(cov[0], 1 / math.sqrt(2))

    def test_exact_legacy_extent_clamping_and_classification(self):
        for count in (-3, 0, 1, 7, 23):
            for every in (-2, 0, 1, 5, 50):
                settings = grid.GridSettings(spacing=2.5, line_count=count, major_every=every)
                params = render.grid_parameters(settings)
                extent = params[0] * params[1]
                segments = grid.grid_segments(cplane.WORLD_TOP, settings)
                points = [p for lines in segments.values() for segment in lines for p in segment]
                self.assertEqual(extent, max(p[0] for p in points))
                self.assertEqual(-extent, min(p[1] for p in points))
                n, m = max(1, count), max(1, every)
                for i in range(-n, n + 1):
                    if not i:
                        continue
                    cov = render.reference_coverage((i * 2.5, .37 * 2.5),
                                                    (.025, 0), (0, .025), settings)
                    self.assertAlmostEqual(cov[int(i % m == 0)], 1)
                    self.assertAlmostEqual(cov[int(i % m != 0)], 0)
                self.assertEqual(render.reference_coverage((extent + .001, 0),
                                 (.025, 0), (0, .025), settings), (0,) * 4)
        self.assertEqual(render.lattice_coverage(25, .1, .05, 5, 20), 0)

    def test_axis_switches_and_exact_finite_endpoints(self):
        for show_grid in (False, True):
            for show_axes in (False, True):
                settings = grid.GridSettings(line_count=7, show_grid=show_grid, show_axes=show_axes)
                for uv, index, positive in (((2.4, 0), 2, True), ((-2.4, 0), 2, False),
                                            ((0, 2.4), 3, True), ((0, -2.4), 3, False)):
                    cov = render.reference_coverage(uv, (.05, 0), (0, .05), settings)
                    self.assertAlmostEqual(cov[index], float(show_axes and positive))
                    self.assertAlmostEqual(cov[1], float(show_grid and not (show_axes and positive)))
                end = render.reference_coverage((7, 0), (.05, 0), (0, .05), settings)
                self.assertAlmostEqual(end[2], .5 if show_axes else 0)
                beyond = render.reference_coverage((7.001, 0), (.05, 0), (0, .05), settings)
                self.assertEqual(beyond, (0,) * 4)

    def test_arbitrary_plane_quad_preserves_world_frame(self):
        planes = (cplane.WORLD_TOP, cplane.WORLD_FRONT, cplane.WORLD_RIGHT,
                  cplane.CPlane((17, -11, 23), (1, 2, 3), (2, -1, 0)))
        for plane in planes:
            data = render.quad_data(plane, 17.5)
            self.assertEqual(data['plane_co'], ((-17.5, -17.5), (17.5, -17.5),
                                               (17.5, 17.5), (-17.5, 17.5)))
            for pos, uv in zip(data['pos'], data['plane_co']):
                local = plane.from_world(pos)
                for a, b in zip(local, (*uv, 0)):
                    self.assertAlmostEqual(a, b)
            self.assertEqual(render.TRIANGLES, ((0, 1, 2), (0, 2, 3)))

    def test_hidden_invalid_settings(self):
        self.assertIsNone(render.grid_parameters(grid.GridSettings(show_grid=False, show_axes=False)))
        for step in (0, -1, float('nan'), float('inf')):
            self.assertIsNone(render.grid_parameters(grid.GridSettings(spacing=step)))
        for width in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                render.grid_parameters(grid.GridSettings(), width)

    def test_blend_neutral_and_reconstructed_target_in_both_color_spaces(self):
        cases = (((193, 204, 218), (244, 247, 251), 'MULTIPLY'),
                 ((81, 81, 81), (44, 44, 44), 'ADDITIVE'))
        def linear(byte):
            s = byte / 255
            return s / 12.92 if s <= .04045 else math.pow((s + .055) / 1.055, 2.4)
        for fg, bg, mode in cases:
            self.assertEqual(render.reference_ink(fg, bg, 0, mode),
                             (1, 1, 1) if mode == 'MULTIPLY' else (0, 0, 0))
            for srgb in (False, True):
                convert = linear if srgb else lambda byte: byte / 255
                for coverage in (0, .1, .5, 1):
                    ink = render.reference_ink(fg, bg, coverage, mode, srgb)
                    for out, b, f in zip(ink, bg, fg):
                        reconstructed = convert(b) * out if mode == 'MULTIPLY' else convert(b) + out
                        self.assertAlmostEqual(reconstructed, convert(b) * (1 - coverage)
                                               + convert(f) * coverage)


class Vector(tuple):
    def __new__(cls, values):
        return super().__new__(cls, values)

    def __sub__(self, other):
        return Vector(a - b for a, b in zip(self, other))

    def __neg__(self):
        return Vector(-a for a in self)

    def __mul__(self, scalar):
        return Vector(a * scalar for a in self)

    def normalized(self):
        length = math.sqrt(sum(a * a for a in self))
        return self * (1 / length)


class Matrix:
    def __init__(self, translation=(0, 0, 0)):
        self.translation = Vector(translation)
        self.col = [None, None, types.SimpleNamespace(xyz=Vector((0, 0, 1)))]

    def inverted(self):
        return Matrix((0, 0, 10))

    def __matmul__(self, other):
        return ('projection @ model-view', tuple(other.translation))


class ShaderInfo:
    def __init__(self):
        self.uniforms, self.inputs, self.outputs = [], [], []

    def push_constant(self, kind, name):
        self.uniforms.append((kind, name))

    def vertex_in(self, slot, kind, name):
        self.inputs.append((slot, kind, name))

    def vertex_out(self, interface):
        self.interface = interface

    def fragment_out(self, slot, kind, name):
        self.outputs.append((slot, kind, name))

    def vertex_source(self, source):
        self.vertex = source

    def fragment_source(self, source):
        self.fragment = source


class Interface:
    def __init__(self, name):
        self.name, self.varyings = name, []

    def smooth(self, kind, name):
        self.varyings.append((kind, name))


class FakeGPU:
    """Strict draw/interface fake; does not purport to compile GLSL."""
    def __init__(self):
        self.types = types.SimpleNamespace(GPUStageInterfaceInfo=Interface, GPUShaderCreateInfo=ShaderInfo)
        self.shader = types.SimpleNamespace(create_from_info=self.compile_interface)
        self.state = types.SimpleNamespace()
        self.current = {'blend': 'ALPHA', 'depth_test': 'GREATER', 'depth_mask': True}
        for name in self.current:
            setattr(self.state, name + '_get', lambda name=name: self.current[name])
            setattr(self.state, name + '_set', lambda value, name=name: self.current.__setitem__(name, value))
        self.stack, self.translation = [], (0, 0, 0)
        self.matrix = types.SimpleNamespace(push=self.push, pop=self.pop, translate=self.translate,
                    get_projection_matrix=Matrix,
                    get_model_view_matrix=lambda: Matrix(self.translation))
        self.compiles, self.batches, self.draws = 0, [], []
        self.values, self.fail_draw = {}, False

    def push(self):
        self.stack.append(self.translation)

    def pop(self):
        self.translation = self.stack.pop()

    def translate(self, value):
        self.translation = value

    def compile_interface(self, info):
        self.compiles += 1
        self.info = info
        assert len(set(name for _, name in info.uniforms)) == len(info.uniforms)
        assert info.inputs == [(0, 'VEC3', 'pos'), (1, 'VEC2', 'plane_co')]
        assert info.interface.varyings == [('VEC2', 'planeCoord')]
        assert info.outputs == [(0, 'VEC4', 'fragColor')]
        self.kinds = dict((name, kind) for kind, name in info.uniforms)
        return types.SimpleNamespace(bind=lambda: None,
                    uniform_float=lambda name, value: self.uniform(name, value, False),
                    uniform_bool=lambda name, value: self.uniform(name, value, True))

    def uniform(self, name, value, boolean):
        assert name in self.kinds, name
        assert (self.kinds[name] == 'BOOL') == boolean, name
        self.values[name] = value

    def batch(self, shader, primitive, data, *, indices):
        assert primitive == 'TRIS'
        assert set(data) == {'pos', 'plane_co'}
        assert len(data['pos']) == len(data['plane_co']) == 4
        assert len(indices) == 2
        self.batches.append((data, indices))
        return types.SimpleNamespace(draw=self.submit)

    def submit(self, shader):
        self.draws.append((dict(self.current), dict(self.values)))
        if self.fail_draw:
            raise RuntimeError('intentional stub draw failure')


class DrawTests(unittest.TestCase):
    def setUp(self):
        render.clear_cache()
        self.gpu = FakeGPU()
        self.patch = patch.dict(sys.modules, {'gpu': self.gpu,
                    'gpu_extras': types.ModuleType('gpu_extras'),
                    'gpu_extras.batch': types.SimpleNamespace(batch_for_shader=self.gpu.batch)})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(render.clear_cache)
        self.settings = grid.GridSettings(line_count=23, major_every=5)
        self.region = types.SimpleNamespace(width=800, height=600)
        self.view = types.SimpleNamespace(view_matrix=Matrix(), view_location=Vector((0, 0, 0)),
                                         view_distance=100, is_perspective=True)
        self.colors = dict(background=(244, 247, 251), minor=(217, 224, 232),
                           major=(193, 204, 218), xaxis=(219, 89, 99), yaxis=(104, 165, 128))

    def draw(self, **kwargs):
        return render.draw(cplane.WORLD_FRONT, self.settings, self.region, self.view,
                           **dict(self.colors, **kwargs))

    def test_shader_interface_and_filter_support(self):
        self.assertTrue(self.draw())
        info = self.gpu.info
        expected = {'gridMatrix', 'gridParams', 'backgroundColor', 'minorColor', 'majorColor',
                    'xAxisColor', 'yAxisColor', 'showGrid', 'showAxes', 'multiplyBlend', 'srgbTarget'}
        self.assertEqual(set(self.gpu.kinds), expected)
        self.assertEqual(set(self.gpu.values), expected - {'srgbTarget'})
        self.assertIn('gridMatrix * vec4(pos, 1.0)', info.vertex)
        # Structural shader guards supplement (never replace) the mathematical tests.
        for token in ('fwidth(q)', 'dFdx(q)', 'dFdy(q)', 'smoothstep(0.25, 0.5,',
                      'pulseIntegral(hi', 'pulseIntegral(lo', '0.25 * every',
                      'targetColor(backgroundColor)', 'targetColor(line)'):
            self.assertIn(token, info.fragment)
        self.assertLess(info.fragment.index('fwidth(q)'), info.fragment.index('discard;'))

    def test_single_quad_depth_nudge_uniforms_and_state_restored(self):
        original = dict(self.gpu.current)
        for perspective in (True, False):
            self.view.is_perspective = perspective
            for see_through in (False, True):
                metrics = {}
                self.assertTrue(self.draw(see_through=see_through, metrics=metrics))
                state, uniforms = self.gpu.draws[-1]
                self.assertEqual(state, dict(blend='MULTIPLY', depth_mask=False,
                                 depth_test='ALWAYS' if see_through else 'LESS_EQUAL'))
                self.assertEqual(uniforms['gridMatrix'], ('projection @ model-view', (0, 0, -.01)))
                self.assertEqual(uniforms['gridParams'], (1, 23, 5, 1))
                self.assertTrue(uniforms['showGrid'] and uniforms['showAxes'])
                self.assertEqual(metrics['vertices'], 4)
                self.assertEqual(metrics['triangles'], 2)
                self.assertEqual(metrics['extent'], 23)
                self.assertTrue(metrics['submitted'])
                self.assertEqual(self.gpu.current, original)
                self.assertEqual(self.gpu.stack, [])
                self.assertEqual(self.gpu.translation, (0, 0, 0))
        self.assertEqual(len(self.gpu.batches), 1)
        self.assertEqual(len(self.gpu.draws), 4)

    def test_oblique_camera_nudge_and_distance_floor(self):
        self.view.view_location = Vector((3, 4, 10))
        self.view.view_distance = 0
        self.draw()
        translation = self.gpu.values['gridMatrix'][1]
        for a, b in zip(translation, (6e-8, 8e-8, 0)):
            self.assertAlmostEqual(a, b, places=15)

    def test_failure_restores_gpu_state_and_does_not_claim_submission(self):
        self.gpu.fail_draw = True
        original = dict(self.gpu.current)
        metrics = {}
        with self.assertRaisesRegex(RuntimeError, 'intentional'):
            self.draw(metrics=metrics)
        self.assertEqual(self.gpu.current, original)
        self.assertEqual(self.gpu.stack, [])
        self.assertEqual(self.gpu.translation, (0, 0, 0))
        self.assertFalse(metrics['submitted'])

    def test_current_theme_uniforms_and_color_independent_cache(self):
        theme = types.SimpleNamespace(VIEWPORT_BG=(244, 247, 251), GRID_MINOR=(217, 224, 232),
                GRID_MAJOR=(193, 204, 218), AXIS_X=(219, 89, 99), AXIS_Y=(104, 165, 128))
        with patch.object(pkg, 'design', theme, create=True):
            def themed():
                metrics = {}
                render.draw(cplane.WORLD_FRONT, self.settings, self.region, self.view, metrics=metrics)
                return metrics
            self.assertFalse(themed()['cache_hit'])
            self.assertEqual(self.gpu.values['minorColor'], tuple(c / 255 for c in theme.GRID_MINOR))
            theme.VIEWPORT_BG, theme.GRID_MINOR, theme.GRID_MAJOR = (44,) * 3, (58,) * 3, (81,) * 3
            theme.AXIS_X, theme.AXIS_Y = (200, 58, 58), (58, 184, 74)
            self.assertTrue(themed()['cache_hit'])
            self.assertFalse(self.gpu.values['multiplyBlend'])
            self.assertEqual(self.gpu.draws[-1][0]['blend'], 'ADDITIVE')
            for uniform, color in (('backgroundColor', theme.VIEWPORT_BG), ('minorColor', theme.GRID_MINOR),
                                   ('majorColor', theme.GRID_MAJOR), ('xAxisColor', theme.AXIS_X),
                                   ('yAxisColor', theme.AXIS_Y)):
                self.assertEqual(self.gpu.values[uniform], tuple(c / 255 for c in color))
        self.assertEqual(self.gpu.compiles, 1)
        self.assertEqual(len(self.gpu.batches), 1)
        self.settings.major_every = 3
        self.settings.show_grid = False
        self.draw()
        self.assertEqual(len(self.gpu.batches), 1)
        self.assertEqual(self.gpu.values['gridParams'][2], 3)
        self.assertFalse(self.gpu.values['showGrid'])
        self.settings.spacing = 2
        self.draw()
        self.assertEqual(len(self.gpu.batches), 2)

    def test_frame_cache_bounded_and_clearable(self):
        for i in range(render.CACHE_LIMIT + 3):
            plane = cplane.CPlane((i, 0, 0))
            render.draw(plane, self.settings, self.region, self.view, **self.colors)
            self.assertLessEqual(len(render._batches), render.CACHE_LIMIT)
        self.assertEqual(len(self.gpu.batches), render.CACHE_LIMIT + 3)
        render.clear_cache()
        self.draw()
        self.assertEqual(self.gpu.compiles, 2)

    def test_no_gpu_import_or_draw_for_hidden_or_missing_view(self):
        with patch.dict(sys.modules, {'gpu': None}):
            self.settings.show_axes = self.settings.show_grid = False
            metrics = {}
            self.assertFalse(self.draw(metrics=metrics))
            self.assertEqual(metrics['vertices'], 0)
            self.settings.show_grid = True
            self.region.width = 0
            self.assertFalse(self.draw())
            self.region.width = 800
            self.assertFalse(render.draw(cplane.WORLD_TOP, self.settings, None, self.view))
            self.assertFalse(render.draw(cplane.WORLD_TOP, self.settings, self.region, None))
            self.assertIsNotNone(load('grid_render'))
        self.assertEqual(self.gpu.compiles, 0)


def suite():
    return unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls)
                              for cls in (CoverageTests, DrawTests))


def mutation_proofs():
    global render
    good = render
    source = (ROOT / 'narwhal3d/grid_render.py').read_text()
    mutants = (
        ('wrong finite extent', 'n = max(1, int(settings.line_count))',
         'n = max(1, int(settings.line_count)) + 1'),
        ('unfiltered point samples', 'return max(0.0, min(1.0, covered - zero))',
         'return float(covered - zero > 0.5)'),
        ('no density fade', 'return 1.0 - t * t * (3.0 - 2.0 * t)', 'return 1.0'),
        ('major inherits minor width cap', 'major_half = min(0.5 * width * p, 0.25 * every)',
         'major_half = min(0.5 * width * p, 0.25)'),
        ('reversed positive axes', 'interval_coverage(x, f, 0.0, n) for x, f in zip(q, footprint)',
         'interval_coverage(x, f, -n, 0.0) for x, f in zip(q, footprint)'),
        ('wrong multiply neutral', 'return tuple(1.0 + coverage *', 'return tuple(0.0 + coverage *'),
        ('missing linear conversion', 'if not srgb_target:', 'if True:'),
        ('leaked depth mask', 'gpu.state.depth_mask_set(old_mask)', 'gpu.state.depth_mask_set(False)'),
        ('fixed shader footprint', 'fwidth(q)', 'vec2(0.05)'),
    )
    failures = []
    try:
        for label, old, new in mutants:
            if source.count(old) != 1:
                raise AssertionError('mutation anchor is not unique: ' + label)
            render = load('grid_render', source.replace(old, new))
            stream = io.StringIO()
            result = unittest.TextTestRunner(stream=stream).run(suite())
            detected = bool(result.failures) and not result.errors
            print(('RED observed: ' if detected else 'MUTATION NOT VALIDATED: ') + label)
            if not detected:
                print(stream.getvalue())
                failures.append(label)
    finally:
        render = good
        sys.modules[PACKAGE + '.grid_render'] = good
    if failures:
        raise AssertionError(failures)
    print(f'{len(mutants)}/{len(mutants)} mutations detected; original source untouched.')


if __name__ == '__main__':
    if '--mutations' in sys.argv:
        mutation_proofs()
    result = unittest.TextTestRunner(verbosity=2).run(suite())
    sys.exit(not result.wasSuccessful())
