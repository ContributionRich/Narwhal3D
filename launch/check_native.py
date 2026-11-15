"""Check required native application owners before launching the graphical runtime.

Python only orchestrates the application's real loaders/ABI calls. No bpy, document,
settings or user-file access (only temporary probe objects). A source/library
mismatch must not launch bare Blender.
"""
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]


def check_pure_consumers():
    """Use runtime consumers on tiny caller-owned data; no developer-script dependency.

    Python is required here for ctypes/host integration. All planning, interpolation
    and encoding stay in the existing Rust-backed adapters; nothing is replayed.
    """
    from narwhal3d import (native_interpolate, native_coil, native_stl, native_obj,
                          native_vector_export, native_dxf, native_tween)

    def interpolation():
        points = [(0., 0., 0.), (1., 0., 0.)]
        if native_interpolate.helper(1, [0., 0., 0., 1., 0., 0.], rows=2,
                                     style=0, capacity=2) != [0., 1.]:
            raise RuntimeError('helper returned invalid startup parameters')
        controls, knots, degree = native_interpolate.interpolate(points, 1, 'Uniform', required=True)
        if controls != points or knots != [0., 0., 1., 1.] or degree != 1:
            raise RuntimeError('full interpolation returned invalid startup controls')
        controls, knots, degree = native_interpolate.interpolate_periodic(
            [(0., 0., 0.), (1., 0., 0.), (1., 1., 0.), (0., 1., 0.)], 'Uniform', required=True)
        if degree != 3 or len(controls) != 7 or len(set(controls)) < 3 or len(set(knots)) < 2:
            raise RuntimeError('periodic interpolation returned an invalid startup curve')
        controls, knots, degree = native_interpolate.skin_control_net(
            [[(0., 0., 0.)], [(0., 1., 0.)]], 1, 'Uniform', required=True)
        if controls != [[(0., 0., 0.), (0., 1., 0.)]] or knots != [0., 0., 1., 1.] or degree != 1:
            raise RuntimeError('skinning returned invalid startup controls')

    def coil():
        poles, _knots, _spans, _deviation = native_coil.plan(
            (0., 0., 0.), (0., 0., 1.), (1., 0., 0.), 1., 1., 1., False, .001)
        if poles[:3] != (1., 0., 0.) or poles[-1] != 1.:
            raise RuntimeError('coil returned an invalid startup frame')

    def catenary():
        poles, _knots, _spans, _deviation = native_coil.catenary_plan(
            (1., 2., 3.), (11., 2., 3.), (6., 2., 2.), (6., 2., 1.), .001)
        if poles[:3] != (1., 2., 3.) or poles[-3:] != (11., 2., 3.):
            raise RuntimeError('catenary returned an invalid startup frame')

    def stl():
        triangle = ((0., 0., 0.), (1., 0., 0.), (0., 1., 0.))
        data = native_stl.encode_stl([triangle])
        if len(data) != 134 or native_stl.decode_stl(data) != [triangle]:
            raise RuntimeError('STL file codec returned invalid startup data')
        vertices, indices, nv, nf = native_stl.decode_stl_mesh(data)
        if ((nv, nf) != (3, 1) or tuple(vertices[:9]) != (0., 0., 0., 1., 0., 0., 0., 1., 0.)
                or tuple(indices[:3]) != (0, 1, 2)
                or native_stl.facet_normal(*triangle) != (0., 0., 1.)):
            raise RuntimeError('STL mesh codec returned invalid startup data')

    def obj():
        data, written = native_obj.encode_obj([
            ('startup', [(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)], [(0, 1, 2)])])
        expected = b'# Narwhal 3D\no startup\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n'
        if written != len(expected) or bytes(data[:written]) != expected:
            raise RuntimeError('OBJ encoder returned invalid startup bytes')

    def vector():
        stroke = types.SimpleNamespace(
            beziers=[((1., 2.), (3., 4.), (5., 6.), (7., 8.))],
            closed=False, rgb=(1., 0., 0.), width_mm=.5, layer='Startup')
        for options in ({'pdf': True, 'title': 'startup'}, {}, {'unit': ('in', 25.4, 4)}):
            # The runtime adapter checks owned length/copy/framing and frees handles.
            if not native_vector_export.encode([stroke], 100., 80., **options):
                raise RuntimeError('vector encoder returned empty startup bytes')

    def dxf():
        if native_dxf.unit_header('Inches') != (1, 0):
            raise RuntimeError('DXF units returned an invalid startup header')
        data, written = native_dxf.encode_dxf([
            {'type': 'LINE', 'p0': (0., 0., 0.), 'p1': (1., 2., 3.), 'layer': 'Startup'}])
        if b'0\nLINE\n' not in data[:written] or b'Startup' not in data[:written]:
            raise RuntimeError('DXF encoder lost the startup entity')

    def tween():
        if native_tween.require_support() is not True:
            raise RuntimeError('Tween required support was not admitted')

    for owner, probe in (('interpolation', interpolation), ('coil', coil),
                         ('catenary', catenary), ('STL', stl), ('OBJ', obj),
                         ('vector', vector), ('DXF', dxf), ('Tween', tween)):
        try:
            probe()
        except Exception as error:
            raise RuntimeError(f'required {owner} native startup support failed: {error}') from error


def check():
    package = types.ModuleType('narwhal3d')
    package.__path__ = [str(ROOT / 'narwhal3d')]
    sys.modules['narwhal3d'] = package
    from narwhal3d.native_textfield import TextField
    from narwhal3d.native_popup import NativePopup
    from narwhal3d.command_transcript import Transcript
    from narwhal3d.native_document_session import Session
    from narwhal3d.native_document_observer import load_observer
    from narwhal3d.native_object_registry import Registry
    import rhino3dm
    from narwhal3d.undo import UndoStack
    from narwhal3d.numparse import parse_length

    field = TextField('startup')
    try:
        if field.text != 'startup':
            raise RuntimeError('native editor did not preserve startup text')
    finally:
        field._native.close()
    popup = NativePopup((320, 240))
    popup.dispose()
    transcript = Transcript()
    try:
        transcript.append('startup')
        if transcript[:] != ['startup']:
            raise RuntimeError('native transcript did not preserve startup text')
    finally:
        transcript.close()
    observer = load_observer()
    registry = Registry()
    try:
        registry.track()
        probe = rhino3dm.File3dm()
        registry.sync_index(observer.index(probe))
        ticket = registry.capture(observer.observe(probe))
        ticket.begin_apply()
        ticket.commit_observed(observer.observe(probe))
        if registry.order_pending() or registry.normalization_plan():
            raise RuntimeError('empty registry has an invalid order plan')
        snapshot = registry.snapshot()
        restore = registry.prepare_restore(snapshot)
        if registry.restore_changes(restore.handle) != {'removed': {}, 'added': []}:
            raise RuntimeError('empty registry has an invalid restore plan')
        restore.abort()
        snapshot.close()
    finally:
        registry.close()
    session = Session()
    session.close()
    history = UndoStack()
    history.close()
    if parse_length('27cm', 'Meters') != (0.27, None):
        raise RuntimeError('native numeric parser returned an invalid startup result')
    check_pure_consumers()
    print('[narwhal3d] required native startup checks passed', flush=True)


if __name__ == '__main__':
    try:
        check()
    except Exception as error:
        print('[narwhal3d] Cannot start: ' + str(error) +
              '. Install/build matching native components; no fallback UI was launched.',
              file=sys.stderr, flush=True)
        raise SystemExit(2)
