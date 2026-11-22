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
                          native_vector_export, native_dxf, native_tween, native_tessellate,
                          native_bspline, native_preview, native_dxf_reader, native_nearest, options)

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

    def earclip():
        square = [(0., 0.), (1., 0.), (1., 1.), (0., 1.)]
        partial = [(0., 0.), (2., 2.), (4., 0.), (4., 2.), (2., 0.), (0., 2.)]
        if (native_tessellate.ear_clip(square) != [(0, 1, 2), (0, 2, 3)]
                or native_tessellate.ear_clip(partial) != [(5, 0, 1), (1, 2, 3)]
                or native_tessellate.signed_area(square) != 1.):
            raise RuntimeError('ear clip returned invalid ordered/partial startup data')

    def control_preview():
        result = native_bspline.control_point_curve_polyline(
            [(0., 0., 0.), (2., 4., 0.), (4., 0., 0.)], max_degree=2, per_span=2)
        if result != [(0., 0., 0.), (2., 2., 0.), (4., 0., 0.)]:
            raise RuntimeError('control preview returned invalid startup samples')

    def preview():
        import struct
        plane = types.SimpleNamespace(xaxis=(1., 0., 0.), yaxis=(0., 1., 0.), zaxis=(0., 0., 1.),
                                      from_world=lambda point: point, to_world=lambda *point: tuple(point))
        rectangle = native_preview.rectangle(plane, (0., -0., 1.), (3., 4., 9.))
        expected = [(0., -0., 9.), (3., -0., 9.), (3., 4., 9.), (0., 4., 9.), (0., -0., 9.)]
        if (len(rectangle) != 5 or struct.pack('<15d', *(v for point in rectangle for v in point))
                != struct.pack('<15d', *(v for point in expected for v in point))):
            raise RuntimeError('primitive preview lost startup depth/order/signed zero')
        ring = native_preview.ring((1., 2., 3.), plane.xaxis, plane.yaxis, 2., 4)
        if len(ring) != 5 or ring[0] != (3., 2., 3.):
            raise RuntimeError('primitive preview returned an invalid startup frame')

    def dxf_reader():
        # Exercise the required decoder without filesystem I/O during startup.
        data = b'0\nSECTION\n2\nENTITIES\n0\nLINE\n8\nCaf\xe9\n10\n0\n20\n0\n30\n0\n11\n1\n21\n2\n31\n3\n0\nENDSEC\n0\nEOF\n'
        expected = {'entities': [{'type': 'LINE', 'layer': 'Caf\xe9', 'color': 7,
                                  'p0': (0., 0., 0.), 'p1': (1., 2., 3.)}],
                    'layers': {}, 'layer_true_colors': {}, 'insunits': None, 'skipped': {}}
        result = native_dxf_reader.decode_dxf(data)
        if (result != expected or list(result) != list(expected)
                or list(result['entities'][0]) != list(expected['entities'][0])):
            raise RuntimeError('DXF reader returned invalid startup types/Latin1/order')
        try:
            native_dxf_reader.decode_dxf(b'0\nSECTION\n2')
        except native_dxf_reader.FileFormatError as error:
            if str(error) != 'truncated DXF: group code with no value':
                raise RuntimeError('DXF reader returned an invalid startup diagnostic') from error
        else:
            raise RuntimeError('DXF reader accepted truncated startup input')

    def nearest():
        # Scalar: a tiny caller-owned NURBS, searched through the required native
        # scalar export (narwhal_pure_core_nearest_on_nurbs).
        scalar = native_nearest.PackedCurve(2, [0., 1.],
                                            [0., 0., 0., 1., 1., 0., 0., 1.],
                                            False, False)
        if native_nearest.search(scalar, (.25, 2., 0.)) != (0.25, (0.25, 0., 0.), 2., False):
            raise RuntimeError('nearest scalar search returned an invalid startup result')
        # Callback: a host-SDK stub (PointAt / DerivativeAt) retained as the ORIGINAL
        # object and searched through the required native callback export
        # (narwhal_pure_core_nearest_on_callbacks_v1). Python only supplies the SDK
        # callbacks; all numerical searching stays in Rust. The stub exercises the
        # host-callback protocol; it is not a geometry producer.
        host = types.SimpleNamespace(
            Domain=types.SimpleNamespace(T0=0., T1=1.),
            Knots=None, SpanCount=1, IsClosed=False,
            PointAt=lambda t: (t, 0., 0.),
            DerivativeAt=lambda t, n: [(t, 0., 0.), (1., 0., 0.), (0., 0., 0.)])
        packed = native_nearest.pack(host)
        if not isinstance(packed, native_nearest.CallbackCurve):
            raise RuntimeError('nearest callback probe did not retain the original SDK object')
        if native_nearest.search(packed, (.25, 2., 0.)) != (0.25, (0.25, 0., 0.), 2., False):
            raise RuntimeError('nearest callback search returned an invalid startup result')

    def option_framework():
        descriptors = (options.Option('2Point', options.MODE),
                       options.Option('3Point', options.MODE))
        result = options.match('o', descriptors)
        if result.status != options.BRANCH or result.option is not descriptors[1]:
            raise RuntimeError('option resolver lost activation-key priority or identity')
        if options.prompt_segments('Point:', descriptors) != [
                ('Point ( ', None), ('2Point', '2Point'), ('  ', None),
                ('3Point', '3Point'), (' ):', None)]:
            raise RuntimeError('option prompt lost native text or clickable labels')

    for owner, probe in (('interpolation', interpolation), ('coil', coil),
                         ('catenary', catenary), ('STL', stl), ('OBJ', obj),
                         ('vector', vector), ('DXF', dxf), ('Tween', tween),
                         ('earclip', earclip), ('control preview', control_preview),
                         ('preview', preview), ('DXF reader', dxf_reader),
                         ('nearest', nearest), ('options', option_framework)):
        try:
            probe()
        except Exception as error:
            raise RuntimeError(f'required {owner} native startup support failed: {error}') from error


def check_geometry_consumers():
    """Qualify exact SDK Join and recipe replay on disposable startup geometry."""
    import math
    from narwhal3d import bridge, kernel, native_geometry

    with native_geometry._lock:
        binding = native_geometry._load_locked()
        if binding is None or not hasattr(binding.library, "narwhal_geometry_core_nurbs_convert_seam_safe_v1"):
            raise RuntimeError("native seam-safe exact NURBS conversion unavailable")
    if not native_geometry.join_exact_available():
        raise RuntimeError("exact Join unavailable: geometry capability bit9 and mapped/verify exports required")
    backend = bridge._occt()
    if backend is None:
        raise RuntimeError("exact Join SDK unavailable")
    recipe = {"kind": "cylinder", "radius": 2.0, "height": 3.0}
    source = backend.from_recipe(recipe)
    operands = [("startup_join_" + str(index), bridge.occt_backed(face,
                 {"kind": "face", "index": index, "a": recipe}))
                for index, face in enumerate(backend.unique_faces(source), 1)]
    groups = bridge.join_breps(operands, 0.02)
    if len(groups) != 1 or groups[0][0] != [name for name, _ in operands] or not groups[0][2]:
        raise RuntimeError("exact Join lost source membership or closure")
    geometry = groups[0][1]
    if (len(geometry.Faces) != 3 or not geometry.IsSolid
            or geometry.GetUserString(kernel.DOCUMENT_FORM_KEY) != "exact"):
        raise RuntimeError("exact Join did not publish three exact SDK faces")
    tree = kernel.recipe_of(geometry)
    if tree.get("kind") != "join_exact" or len(tree.get("operands", [])) != 3:
        raise RuntimeError("exact Join lost its operand recipe tree")
    for shape in (kernel.master_of(geometry), backend.from_recipe(tree)):
        if (not backend.is_airtight(shape)
                or backend.face_types(shape) != {"PLANE": 2, "CYLINDER": 1}
                or not math.isclose(backend.volume(shape), 12 * math.pi, rel_tol=1e-10)):
            raise RuntimeError("exact Join SDK/recipe semantics differ")
    import copy
    changed = copy.deepcopy(tree)
    changed["result"] = backend.native_master_recipe(backend.make_box(1, 1, 1))
    try:
        backend.from_recipe(changed)
    except kernel.KernelError:
        pass
    else:
        raise RuntimeError("exact Join verifier accepted changed frozen geometry")
    return {"suite": "exact Join startup", "exact_faces": 3, "closed": True,
            "operand_count": 3, "recipe_verified": True}


def check_cap_consumers():
    """Require supervised exact Cap, nested replay and tamper rejection."""
    import copy
    import math
    from narwhal3d import bridge, kernel, native_geometry
    if not native_geometry.cap_exact_available():
        raise RuntimeError("exact Cap unavailable: geometry capability bit10 and cap/verify exports required")
    backend = bridge._occt()
    recipe = {"kind": "cylinder", "radius": 2.0, "height": 3.0}
    wall = next((index, face) for index, face in enumerate(
        backend.unique_faces(backend.from_recipe(recipe)), 1)
        if backend.face_types(face) == {"CYLINDER": 1})
    source = bridge.occt_backed(wall[1], {"kind": "face", "index": wall[0], "a": recipe})
    capped = bridge.cap_planar_holes_occt(source, tolerance=0.01)
    if capped is None:
        raise RuntimeError("exact Cap startup refused: " + str(bridge.last_occt_decline()))
    tree = kernel.recipe_of(capped)
    if (tree.get("kind") != "cap" or tree.get("version") != 1
            or tree.get("tolerance") != .01 or len(capped.Faces) != 3 or not capped.IsSolid):
        raise RuntimeError("exact Cap document/recipe contract differs")
    for shape in (kernel.master_of(capped), backend.from_recipe(tree)):
        if (not backend.is_airtight(shape)
                or backend.face_types(shape) != {"CYLINDER": 1, "PLANE": 2}
                or not math.isclose(backend.volume(shape), 12 * math.pi, rel_tol=1e-10)):
            raise RuntimeError("exact Cap SDK/recipe semantics differ")
    changed = copy.deepcopy(tree)
    changed["result"] = backend.native_master_recipe(backend.make_box(1, 1, 1))
    try:
        backend.from_recipe(changed)
    except kernel.KernelError:
        pass
    else:
        raise RuntimeError("exact Cap verifier accepted changed frozen geometry")
    # Shifted elementary seams must survive exact document conversion too: the
    # cylinder alone cannot expose OCCT's periodic rational-U segmentation bug.
    loft = bridge.loft_brep([bridge.make_circle((20, 0, 0), 5.0),
                             bridge.make_circle((20, 0, 6), 3.0)], style="Straight")
    cone = bridge.cap_planar_holes_occt(loft, tolerance=.01)
    if cone is None:
        raise RuntimeError("shifted conical Cap conversion refused: " + str(bridge.last_occt_decline()))
    for shape in (kernel.master_of(cone), backend.from_recipe(kernel.recipe_of(cone))):
        if (not cone.IsValid or len(cone.Faces) != 3 or not backend.is_airtight(shape)
                or backend.face_types(shape) != {"CONE": 1, "PLANE": 2}
                or not math.isclose(backend.volume(shape), 98 * math.pi, rel_tol=1e-10)):
            raise RuntimeError("shifted conical Cap document/recipe geometry differs")
    return {"suite": "exact Cap startup", "exact_faces": 3, "closed": True,
            "recipe_verified": True, "supervised": True, "shifted_cone": True}


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
    check_geometry_consumers()
    check_cap_consumers()
    print('[narwhal3d] required native startup checks passed', flush=True)


if __name__ == '__main__':
    try:
        check()
    except Exception as error:
        print('[narwhal3d] Cannot start: ' + str(error) +
              '. Install/build matching native components; no fallback UI was launched.',
              file=sys.stderr, flush=True)
        raise SystemExit(2)
