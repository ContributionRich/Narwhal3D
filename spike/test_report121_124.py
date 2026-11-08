"""Focused owner-report regressions; no document writes or full application import.

Run with the bundled Python and PYTHONPATH=vendor/occt. Mutations operate on
in-memory source only, so concurrent agents' files and bytecode are untouched.
All mandatory workloads are generated exact geometry. Optional private files are
read only when NARWHAL_TEST_PRIVATE_REPORT_DIR explicitly names their directory.
"""
import ast
import importlib
import math
import os
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
if 'narwhal3d' not in sys.modules:
    pkg = types.ModuleType('narwhal3d')
    pkg.__path__ = [str(ROOT / 'narwhal3d')]
    sys.modules['narwhal3d'] = pkg
bridge = importlib.import_module('narwhal3d.bridge')
kernel = importlib.import_module('narwhal3d.kernel')
import rhino3dm as r3


def synthetic_profile(sides=25, radius=3.0, origin=(0, 0, -1.2), joined=False):
    """OURS: exact polynomial boundary, deliberately NOT a circle approximation.

    Each quadratic Bezier span bows outward by radius/100. Adjacent spans have
    G0 but not G1 continuity. One closed NURBS hides sides-1 interior creases;
    a PolyCurve exposes each source segment as a topological edge instead.
    """
    vertices = [(origin[0] + radius * math.cos(2 * math.pi * i / sides),
                 origin[1] + radius * math.sin(2 * math.pi * i / sides), origin[2])
                for i in range(sides)]
    poles = [vertices[0]]
    polycurve = r3.PolyCurve() if joined else None
    for i in range(sides):
        a, c = vertices[i], vertices[(i + 1) % sides]
        angle = 2 * math.pi * (i + .5) / sides
        middle = ((a[0] + c[0]) / 2 + radius / 100 * math.cos(angle),
                  (a[1] + c[1]) / 2 + radius / 100 * math.sin(angle), origin[2])
        poles.extend((middle, c))
        if joined:
            segment = r3.NurbsCurve(3, False, 3, 3)
            for index, point in enumerate((a, middle, c)):
                segment.Points[index] = r3.Point4d(*point, 1)
            for index, knot in enumerate((0, 0, 1, 1)):
                segment.Knots[index] = knot
            assert polycurve.Append(segment)
    if joined:
        assert polycurve.IsClosed and polycurve.SegmentCount == sides
        return polycurve
    curve = r3.NurbsCurve(3, False, 3, len(poles))
    for index, point in enumerate(poles):
        curve.Points[index] = r3.Point4d(*point, 1)
    for index, knot in enumerate(value for knot in range(sides + 1) for value in (knot, knot)):
        curve.Knots[index] = knot
    assert curve.IsClosed and not curve.IsPolyline() and not curve.IsCircle()
    return curve


def quadratic_profile_area(curve):
    """Independent exact Green-integral area of the synthetic polynomial spans."""
    def cross(a, b):
        return a.X * b.Y - a.Y * b.X
    if isinstance(curve, r3.PolyCurve):
        return sum(quadratic_profile_area(curve.SegmentCurve(i)) for i in range(curve.SegmentCount))
    points = list(curve.Points)
    return sum((cross(a, b) + cross(b, c)) / 3 + cross(a, c) / 6
               for a, b, c in (points[i:i + 3] for i in range(0, len(points) - 2, 2)))


def synthetic_legacy_prism(origin=(0, 0, -1.2)):
    """A NEW test object carrying an OLD unversioned, hollow-prism recipe.

    Expected legacy topology: 1 outer wall + 7 inner walls + 2 caps = 10 faces,
    24 edges, 16 vertices. Its outer wall hides 24 real G1 breaks. Versioning
    splits it into 34 exact faces, without changing its mathematical boundary.
    """
    outer = synthetic_profile(origin=origin)
    inner = synthetic_profile(7, 1.0, origin, joined=True)
    recipe = {'kind': 'prism', 'a': {'kind': 'planar_face',
              'loops': [[bridge.curve_recipe(outer)], [bridge.curve_recipe(inner)]]},
              'dir': [0, 0, -.5], 'both': False, 'solid': True}
    occt = bridge._occt()
    geom = bridge.occt_backed(occt.from_recipe(recipe), recipe)
    volume = (quadratic_profile_area(outer) - quadratic_profile_area(inner)) * .5
    return geom, inner, volume


def public_documents():
    """Mandatory generated workloads, plus explicitly requested private input."""
    for label, origin in (('synthetic-origin', (0, 0, -1.2)),
                          ('synthetic-translated', (17, -11, 4)),
                          ('synthetic-negative', (-8, 3, -6))):
        document = r3.File3dm()
        geom, joined, _volume = synthetic_legacy_prism(origin)
        attributes = r3.ObjectAttributes()
        attributes.Name = 'SyntheticLegacyPrism'
        document.Objects.AddBrep(geom, attributes)
        attributes = r3.ObjectAttributes()
        attributes.Name = 'SyntheticJoinedProfile'
        document.Objects.AddCurve(joined, attributes)
        yield label, document
    private = os.environ.get('NARWHAL_TEST_PRIVATE_REPORT_DIR')
    if private:
        # Explicit opt-in only. No artwork discovery, fallback, or copying.
        directory = Path(private).expanduser().resolve(strict=True)
        for number in ('0121', '0122', '0124'):
            filename = directory / (number + '.3dm')
            document = r3.File3dm.Read(str(filename))
            assert document is not None, filename
            yield 'optional-private-' + number, document


def extrusion_class(mutation=''):
    tree = ast.parse((ROOT / 'narwhal3d/commands.py').read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ExtrudeCmd')
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in
               ('update_extrusion_hover', 'extrusion_typed_distance', '_parse_distance')]
    mini = ast.Module(body=methods, type_ignores=[])
    text = ast.unparse(mini)
    if mutation == 'sign':
        text = text.replace('value * getattr(self, \'_extrusion_hover_sign\', 1)', 'value')
    ns = {'parse_number': float}
    exec(compile(text, '<owned-extrusion-methods>', 'exec'), ns)
    return type('ExtrudeProbe', (), {n.name: ns[n.name] for n in methods}), cls


def test_extrusion(mutation=''):
    cls, node = extrusion_class(mutation)
    options = next(n.value for n in node.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'options' for t in n.targets))
    solid = next(n for n in options.elts if n.args[0].value == 'Solid')
    assert next(k.value.value for k in solid.keywords if k.arg == 'default') is True
    cmd = cls()
    assert cmd._parse_distance('7', None) == 7
    axis = ((2, 3, 5), (0, 1, 0))
    cmd.update_extrusion_hover((2, -4, 5), axis)
    assert cmd._parse_distance('7', None) == -7, 'positive 7 follows negative hover'
    assert cmd._parse_distance('-7', None) == -7, 'explicit negative remains negative'
    cmd.update_extrusion_hover(axis[0], axis)
    assert cmd._parse_distance('7', None) == -7, 'zero hover preserves side'
    cmd.update_extrusion_hover((2, 10, 5), axis)
    assert cmd._parse_distance('7', None) == 7
    assert cmd._parse_distance('-7', None) == -7
    assert cmd._parse_distance('0', None) == 0


def test_master(mutation=''):
    circle = r3.NurbsCurve.CreateFromCircle(r3.Circle(3))
    solid = bridge.extrude_curve(circle, 4, direction=(0, 0, 1), solid=True)
    master = kernel.master_of(solid)
    assert master is not None
    osnap = importlib.import_module('narwhal3d.osnap')
    cplane = importlib.import_module('narwhal3d.cplane')
    circular = next(t for t in bridge.geometry_snap_targets(solid)
                    if t.center is not None and t.closed)
    assert math.dist(circular.center[:2], (0, 0)) < 1e-7
    assert all(abs(math.hypot(p[0], p[1]) - 3) < 1e-7
               for p in osnap._target_quads(circular, cplane.WORLD_TOP))
    near = osnap._nearest_on_target(circular, (2, 2, circular.center[2]))
    assert abs(math.hypot(near[0], near[1]) - 3) < 1e-7
    assert abs(near[2] - circular.center[2]) < 1e-7
    fn = bridge.geometry_snap_targets
    if mutation == 'proxy':
        source = (ROOT / 'narwhal3d/bridge.py').read_text()
        tree = ast.parse(source)
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == 'geometry_snap_targets')
        text = ast.unparse(node).replace('if _kernel().master_of(geom) is not None:', 'if False:')
        ns = dict(vars(bridge))
        exec(text, ns)
        fn = ns['geometry_snap_targets']
    # A boolean's display proxy has hundreds of edges, unlike a primitive's
    # exact Brep; only this input discriminates the proxy-edge regression.
    solid = bridge.boolean_brep(bridge.make_box((-2, -2, 0), (2, 2, 0), 2),
                                bridge.make_cylinder((0, 0, 0), 3, 4), 'UNION')
    # Force a historical display-only proxy independent of the currently installed
    # exact Brep writer. Its deliberately unrelated triangle must NEVER be snapped.
    master = kernel.master_of(solid)
    mesh = r3.Mesh()
    for p in ((90, 90, 90), (91, 90, 90), (90, 91, 90)):
        mesh.Vertices.Add(*p)
    mesh.Faces.AddFace(0, 1, 2)
    proxy = r3.Brep.CreateFromMesh(mesh, False)
    kernel.register_master(proxy, master)
    solid = proxy
    targets = fn(solid, 'Cylinder')
    exact, _ = bridge.extract_wireframe(solid)
    assert len(targets) == len(exact), 'master edge count, not proxy triangles'
    sources = bridge.object_snap_sources(solid)
    lazy = [t for source, edge, extent in sources
            for t in bridge.snap_source_targets(source, edge)]
    assert len(lazy) == len(exact), 'live lazy index uses master edges too'
    assert all(max(abs(v) for p in t.vertices for v in p) < 10 for t in lazy)
    assert any(t.center is not None for t in targets), 'analytic circular edge center exists'
    for target in targets:
        assert target.curve is not None
        for p in target.vertices:
            assert all(math.isfinite(v) for v in p)
    extrusion = r3.Extrusion.Create(circle, 4, True)
    assert fn(extrusion), 'native extrusion has boundary snap targets'


def test_event_mouse():
    osnap = importlib.import_module('narwhal3d.osnap')
    target = osnap.SnapTarget.point((1, 2, 3), name='screen witness')
    project = lambda p: (p[0], p[1])
    # The horizon's CPlane hit is far away/ill-conditioned; the event itself is
    # beside the visible target. Reprojecting the hit must not lose the snap.
    cursor = (1e12, 1e12, 0)
    assert osnap.best_on_screen([target], cursor, ('Point',), project, .1) is None
    result = osnap.best_on_screen([target], cursor, ('Point',), project, .1, mouse=(1, 2))
    assert result is not None and result.point == (1, 2, 3)


def test_reports():
    for number, doc in public_documents():
        count = 0
        for obj in doc.Objects:
            geom = obj.Geometry
            recipe = kernel.recipe_of(geom)
            if recipe is not None and kernel.master_key_of(geom):
                assert bridge.rehydrate_master(geom), (number, obj.Attributes.Name)
                if kernel.master_of(geom) is not None:
                    targets = bridge.geometry_snap_targets(geom, obj.Attributes.Name)
                    exact, source = bridge.extract_wireframe(geom)
                    crease_count = len(bridge._occt().extrusion_crease_curves(kernel.master_of(geom)))
                    assert source == 'master' and len(targets) == len(exact) + crease_count
                    assert sum(t.curve.GetUserString('Narwhal3D:SnapFeature') == 'crease-isocurve'
                               for t in targets) == crease_count
                    lazy = [t for src, edge, extent in bridge.object_snap_sources(geom)
                            for t in bridge.snap_source_targets(src, edge)]
                    assert len(lazy) == len(targets)
                    occt = bridge._occt()
                    master = kernel.master_of(geom)
                    for i in range(len(occt._unique_vertices(master))):
                        corner = occt.vertex_at(master, i + 1)
                        assert min(math.dist(corner, p) for t in lazy for p in t.vertices) < 1e-6
                    count += 1
        print(number, 'master-backed objects checked:', count)
        assert count


if __name__ == '__main__':
    mutation = os.environ.get('REPORT_MUTATION', '')
    if mutation == 'sign':
        test_extrusion(mutation)
    elif mutation == 'proxy':
        test_master(mutation)
    else:
        test_extrusion()
        test_master()
        test_event_mouse()
        test_reports()
    print('report121_124 PASS', mutation or 'restored')
