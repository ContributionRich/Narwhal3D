"""Hardened acceptance for exact-only topology-local OCCT movement.

Run: PYTHONPATH=vendor/occt tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 \
         spike/test_local_brep_move.py
"""
import inspect
import json
import math
import os
import subprocess
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg

from narwhal3d.kernel import KernelError  # noqa: E402
from narwhal3d.kernel import occt_backend as ob  # noqa: E402
from OCP.BRep import BRep_Tool  # noqa: E402
from OCP.BRepAlgoAPI import BRepAlgoAPI_Check  # noqa: E402
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface  # noqa: E402
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex  # noqa: E402
from OCP.BRepClass import BRepClass_FaceClassifier  # noqa: E402
from OCP.BRepExtrema import BRepExtrema_DistShapeShape  # noqa: E402
from OCP.BRepGProp import BRepGProp  # noqa: E402
from OCP.BRepTools import BRepTools  # noqa: E402
from OCP.GeomAbs import GeomAbs_BSplineSurface  # noqa: E402
from OCP.GProp import GProp_GProps  # noqa: E402
from OCP.TopAbs import TopAbs_EDGE, TopAbs_IN, TopAbs_VERTEX  # noqa: E402
from OCP.TopExp import TopExp_Explorer  # noqa: E402
from OCP.TopoDS import TopoDS  # noqa: E402
from OCP.gp import gp_Pnt, gp_Pnt2d, gp_Vec  # noqa: E402

checks = 0
failures = []


def check(condition, label):
    global checks
    checks += 1
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


def distance(a, b):
    return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b)))


def add(point, vector):
    return tuple(float(point[i]) + float(vector[i]) for i in range(3))


def point(vertex):
    value = BRep_Tool.Pnt_s(vertex)
    return value.X(), value.Y(), value.Z()


def independent_tolerance(shape):
    low, high = ob.tight_bbox(shape)
    coordinate = max(1.0, *(abs(value) for value in low + high))
    return max(1e-9, 128.0 * math.ulp(coordinate))


def point_shape_distance(value, shape):
    vertex = BRepBuilderAPI_MakeVertex(gp_Pnt(*value)).Vertex()
    extrema = BRepExtrema_DistShapeShape(vertex, shape)
    extrema.Perform()
    return extrema.Value() if extrema.IsDone() else math.inf


def edge_samples(edge, count=25):
    adaptor = BRepAdaptor_Curve(edge)
    first, last = adaptor.FirstParameter(), adaptor.LastParameter()
    return [adaptor.Value(first + (last - first) * i / (count - 1)) for i in range(count)]


def face_samples(face):
    adaptor = BRepAdaptor_Surface(face)
    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    tolerance = 1e-9
    result = []
    for i in range(1, 10):
        for j in range(1, 10):
            u = u0 + (u1 - u0) * i / 10.0
            v = v0 + (v1 - v0) * j / 10.0
            classifier = BRepClass_FaceClassifier(face, gp_Pnt2d(u, v), tolerance)
            if classifier.State() == TopAbs_IN:
                result.append(adaptor.Value(u, v))
    explorer = TopExp_Explorer(face, TopAbs_EDGE)
    seen = []
    while explorer.More():
        edge = TopoDS.Edge(explorer.Current())
        if not BRep_Tool.Degenerated_s(edge) and not any(old.IsSame(edge) for old in seen):
            seen.append(edge)
            result.extend(edge_samples(edge, 13))
        explorer.Next()
    assert result, "face oracle found no exact support samples"
    return result


def part_measure(part, kind):
    props = GProp_GProps()
    if kind == "edge":
        BRepGProp.LinearProperties_s(part, props)
    else:
        BRepGProp.SurfaceProperties_s(part, props, 1e-11)
    return props.Mass()


def independent_part_matches(old, candidate, kind, vector, tolerance):
    if kind == "face":
        if BRepAdaptor_Surface(old).GetType() != BRepAdaptor_Surface(candidate).GetType():
            return False
        old_samples, candidate_samples = face_samples(old), face_samples(candidate)
    else:
        # Storage may change CIRCLE -> analytically recognized BSPLINE at a Coons boundary.
        # Geometry is independently checked in both directions below; semantic type is a
        # separate production assertion rather than the sole oracle.
        if ob._local_curve_type(old) != ob._local_curve_type(candidate):
            return False
        old_samples, candidate_samples = edge_samples(old), edge_samples(candidate)
    old_mass, new_mass = part_measure(old, kind), part_measure(candidate, kind)
    dimensional_noise = (8.0 * tolerance if kind == "edge" else
                         8.0 * tolerance * max(math.sqrt(abs(old_mass)), tolerance))
    mass_tolerance = max(2.5e-7 * max(tolerance, abs(old_mass)), dimensional_noise)
    if abs(new_mass - old_mass) > mass_tolerance:
        return False
    for sample in old_samples:
        moved = add((sample.X(), sample.Y(), sample.Z()), vector)
        if point_shape_distance(moved, candidate) > tolerance:
            return False
    inverse = tuple(-value for value in vector)
    for sample in candidate_samples:
        restored = add((sample.X(), sample.Y(), sample.Z()), inverse)
        if point_shape_distance(restored, old) > tolerance:
            return False
    return True


def selected_part(shape, kind, index):
    if kind == "face":
        return ob.unique_faces(shape)[index - 1]
    if kind == "edge":
        return ob._unique_edges(shape)[index - 1]
    return ob._unique_vertices(shape)[index - 1]


def independent_translated_matches(before, after, kind, index, vector):
    old = selected_part(before, kind, index)
    tolerance = independent_tolerance(before)
    if kind == "vertex":
        target = add(point(old), vector)
        return [i for i, vertex in enumerate(ob._unique_vertices(after), 1)
                if distance(point(vertex), target) <= tolerance]
    candidates = ob.unique_faces(after) if kind == "face" else ob._unique_edges(after)
    return [i for i, candidate in enumerate(candidates, 1)
            if independent_part_matches(old, candidate, kind, vector, tolerance)]


def edge_vertex_indices(edge, vertices):
    result = []
    explorer = TopExp_Explorer(edge, TopAbs_VERTEX)
    while explorer.More():
        vertex = TopoDS.Vertex(explorer.Current())
        found = next(i for i, candidate in enumerate(vertices) if candidate.IsSame(vertex))
        if found not in result:
            result.append(found)
        explorer.Next()
    return result


def face_edge_indices(face, edges):
    result = []
    explorer = TopExp_Explorer(face, TopAbs_EDGE)
    while explorer.More():
        edge = TopoDS.Edge(explorer.Current())
        found = next(i for i, candidate in enumerate(edges) if candidate.IsSame(edge))
        if found not in result:
            result.append(found)
        explorer.Next()
    return result


def moved_vertex_indices(shape, kind, index):
    faces = ob.unique_faces(shape)
    edges = ob._unique_edges(shape)
    vertices = ob._unique_vertices(shape)
    edge_vertices = [edge_vertex_indices(edge, vertices) for edge in edges]
    if kind == "face":
        selected_edges = face_edge_indices(faces[index - 1], edges)
        return {vertex for edge in selected_edges for vertex in edge_vertices[edge]}
    if kind == "edge":
        return set(edge_vertices[index - 1])
    return {index - 1}


def strict_vertex_containment(before, after, kind, index, vector):
    old_vertices = ob._unique_vertices(before)
    new_vertices = ob._unique_vertices(after)
    moved = moved_vertex_indices(before, kind, index)
    tolerance = independent_tolerance(before)
    if len(old_vertices) != len(new_vertices):
        return False
    unused = set(range(len(new_vertices)))
    for old_index, vertex in enumerate(old_vertices):
        expected = add(point(vertex), vector) if old_index in moved else point(vertex)
        matches = [new_index for new_index in unused
                   if distance(expected, point(new_vertices[new_index])) <= tolerance]
        if len(matches) != 1:
            return False
        unused.remove(matches[0])
    return not unused


def unchanged_faces_preserved(before, after, kind, index):
    faces = ob.unique_faces(before)
    edges = ob._unique_edges(before)
    vertices = ob._unique_vertices(before)
    edge_vertices = [edge_vertex_indices(edge, vertices) for edge in edges]
    face_edges = [face_edge_indices(face, edges) for face in faces]
    moved = moved_vertex_indices(before, kind, index)
    changed_edges = {edge for edge, endpoints in enumerate(edge_vertices)
                     if any(vertex in moved for vertex in endpoints)}
    changed_faces = {face for face, boundary in enumerate(face_edges)
                     if any(edge in changed_edges for edge in boundary)}
    candidates = ob.unique_faces(after)
    tolerance = max(independent_tolerance(before), ob._local_tolerances(before)["build"])
    for face_index, face in enumerate(faces):
        if face_index in changed_faces:
            continue
        matches = sum(independent_part_matches(
            face, candidate, "face", (0.0, 0.0, 0.0), tolerance)
            for candidate in candidates)
        if matches != 1:
            return False
    return True


def exact_face_subset(shape):
    allowed = {"PLANE", "CYLINDER", "SPHERE", "BSPLINE"}
    if not set(ob.face_types(shape)) <= allowed:
        return False
    for face in ob.unique_faces(shape):
        adaptor = BRepAdaptor_Surface(face)
        if adaptor.GetType() == GeomAbs_BSplineSurface:
            surface = adaptor.BSpline()
            if surface.UDegree() > 8 or surface.VDegree() > 8:
                return False
    return True


def topology_counts(shape):
    return len(ob.unique_faces(shape)), len(ob._unique_edges(shape)), len(ob._unique_vertices(shape))


def composite_quality_candidates(shape, kind, index, vector):
    faces = ob.unique_faces(shape)
    edges = ob._unique_edges(shape)
    vertices = ob._unique_vertices(shape)
    edge_vertices = [ob._local_edge_vertices(edge, vertices) for edge in edges]
    face_edges = [ob._local_face_edges(face, edges) for face in faces]
    if kind == "face":
        selected_edges = set(face_edges[index - 1])
        moved = {vertex for edge in selected_edges for vertex in edge_vertices[edge]}
    elif kind == "edge":
        moved = set(edge_vertices[index - 1])
    else:
        moved = {index - 1}
    changed_edges = {edge for edge, endpoints in enumerate(edge_vertices)
                     if any(vertex in moved for vertex in endpoints)}
    replacements = {
        edge: ob._local_rebuild_edge(
            edges[edge], edge_vertices[edge], vertices, moved, vector,
            ob._local_tolerances(shape)["build"])[0]
        for edge in changed_edges
    }
    changed_faces = {face for face, boundary in enumerate(face_edges)
                     if any(edge in changed_edges for edge in boundary)}
    tolerances = ob._local_tolerances(shape)
    found = []
    for face_index in changed_faces:
        boundary = ob._local_boundary_edges(
            faces[face_index], edges, replacements, vertices, moved, vector,
            tolerances["build"])
        if len(boundary) == 5 and ob._local_planar_face(boundary) is None:
            found.extend(ob._local_composite_coons_candidates(
                faces[face_index], boundary, tolerances))
    return found, tolerances


def accepted_composite_quality(shape, kind, index, vector):
    candidates, tolerances = composite_quality_candidates(shape, kind, index, vector)
    accepted = [candidate for candidate in candidates
                if candidate["boundary_error"] <= tolerances["compare"]
                and candidate["normal_jump"] <= ob.LOCAL_COONS_NORMAL_JUMP]
    return accepted, tolerances


def independent_boundary_error(face, boundary):
    source_wire = ob._local_make_wire(boundary)
    result_wire = BRepTools.OuterWire_s(face)
    worst = 0.0
    for edge in boundary:
        for sample in edge_samples(edge, 33):
            worst = max(worst, point_shape_distance(
                (sample.X(), sample.Y(), sample.Z()), result_wire))
    explorer = TopExp_Explorer(result_wire, TopAbs_EDGE)
    while explorer.More():
        edge = TopoDS.Edge(explorer.Current())
        for sample in edge_samples(edge, 33):
            worst = max(worst, point_shape_distance(
                (sample.X(), sample.Y(), sample.Z()), source_wire))
        explorer.Next()
    return worst


def independent_normal_jump(surface):
    u0, u1, v0, v1 = surface.Bounds()
    jumps = []
    for axis in (0, 1):
        degree = surface.UDegree() if axis == 0 else surface.VDegree()
        count = surface.NbUKnots() if axis == 0 else surface.NbVKnots()
        knot = surface.UKnot if axis == 0 else surface.VKnot
        multiplicity = surface.UMultiplicity if axis == 0 else surface.VMultiplicity
        for index in range(2, count):
            if multiplicity(index) < degree:
                continue
            value = knot(index)
            delta = min(value - knot(index - 1), knot(index + 1) - value) * 1e-6
            worst = 0.0
            for sample in range(1, 10):
                cross = (v0 + (v1 - v0) * sample / 10.0 if axis == 0 else
                         u0 + (u1 - u0) * sample / 10.0)
                normals = []
                for side in (-1.0, 1.0):
                    u, v = ((value + side * delta, cross) if axis == 0 else
                            (cross, value + side * delta))
                    p, du, dv = gp_Pnt(), gp_Vec(), gp_Vec()
                    surface.D1(u, v, p, du, dv)
                    normal = du.Crossed(dv)
                    normal.Normalize()
                    normals.append(normal)
                cosine = max(-1.0, min(1.0, normals[0].Dot(normals[1])))
                worst = max(worst, math.acos(cosine))
            jumps.append(worst)
    return max(jumps) if jumps else math.inf


def composite_face_records(shape):
    records = []
    edges = ob._unique_edges(shape)
    for face in ob.unique_faces(shape):
        adaptor = BRepAdaptor_Surface(face)
        if adaptor.GetType() != GeomAbs_BSplineSurface:
            continue
        surface = adaptor.BSpline()
        if (surface.UDegree(), surface.VDegree(), surface.NbUPoles(),
                surface.NbVPoles()) == (2, 1, 5, 2):
            boundary = face_edge_indices(face, edges)
            records.append((face, surface, boundary))
    return records


def parent_recipe(scale=1.0, transform=None):
    recipe = {
        "kind": "fillet", "size": scale, "edges": [13],
        "a": {
            "kind": "fillet", "size": scale, "edges": [4],
            "a": {"kind": "box", "dx": 20.0 * scale, "dy": 10.0 * scale,
                  "dz": 33.8393 * scale},
        },
    }
    if transform is not None:
        recipe["xform"] = transform
    return recipe


def move_recipe(parent, shape, kind, index, vector, index_hint=None):
    return {
        "kind": "local_move", "a": parent, "component": kind,
        "index": index if index_hint is None else index_hint,
        "vector": list(vector),
        "target": ob.local_subobject_signature(shape, kind, index),
    }


def translated_sphere(center, radius):
    matrix = [1.0, 0.0, 0.0, center[0],
              0.0, 1.0, 0.0, center[1],
              0.0, 0.0, 1.0, center[2],
              0.0, 0.0, 0.0, 1.0]
    return ob.from_recipe({"kind": "sphere", "radius": radius, "xform": matrix})


def validate_result(before, after, kind, index, vector):
    verdicts = {
        "airtight": ob.is_airtight(after),
        "topology": topology_counts(after) == topology_counts(before),
        "selected": len(independent_translated_matches(
            before, after, kind, index, vector)) == 1,
        "containment": strict_vertex_containment(before, after, kind, index, vector),
        "unchanged": unchanged_faces_preserved(before, after, kind, index),
        "face_subset": exact_face_subset(after),
        "positive_volume": ob.volume(after) > 0.0,
        "self_intersection": BRepAlgoAPI_Check(after).IsValid(),
    }
    if not all(verdicts.values()):
        print(f"    oracle detail {kind}{index} {vector}: {verdicts}")
    return all(verdicts.values())


def fresh_mode(path):
    with open(path, "r", encoding="utf-8") as stream:
        payload = json.load(stream)
    for item in payload:
        before = ob.from_recipe(item["parent"])
        after = ob.from_recipe(item["move"])
        kind, index, vector = item["component"], item["index"], tuple(item["vector"])
        assert validate_result(before, after, kind, index, vector)
        if (kind, index) in (("edge", 20), ("vertex", 11)):
            quality, tolerances = accepted_composite_quality(before, kind, index, vector)
            assert len(quality) == 1
            assert independent_boundary_error(
                quality[0]["face"], quality[0]["source_boundary"]) <= tolerances["compare"]
            assert independent_normal_jump(
                BRepAdaptor_Surface(quality[0]["face"]).BSpline()) <= ob.LOCAL_COONS_NORMAL_JUMP
            records = composite_face_records(after)
            assert len(records) == 1 and len(records[0][2]) == 5
        cutter = translated_sphere((10.0, 5.0, 16.0), 0.2)
        cut = ob.boolean(after, cutter, "DIFFERENCE")
        assert ob.is_airtight(cut) and ob.volume(cut) < ob.volume(after)
        assert "SPHERE" in ob.face_types(cut)
    print("FRESH-LOCAL-MOVE-OK")


if not ob.available():
    print(f"skip: local B-rep movement needs OCCT ({ob.unavailable_reason()})")
    raise SystemExit(0)
if len(sys.argv) == 3 and sys.argv[1] == "--fresh":
    fresh_mode(sys.argv[2])
    raise SystemExit(0)

source = inspect.getsource(ob.move_subobject_local)
for name, value in vars(ob).items():
    if name.startswith("_local_") and callable(value):
        source += "\n" + inspect.getsource(value)
for forbidden in ("BRepOffsetAPI_MakeFilling", "BRepFill_Filling", "BRepMesh_",
                  "Triangulation_s", "tessellate(", "MESH_DERIVED"):
    check(forbidden not in source, f"local implementation forbids {forbidden}")
check("GeomFill_BSplineCurves" in source and "GeomFill_StretchStyle" in source
      and "GeomConvert_CompCurveToBSplineCurve" in source,
      "composite-natural GeomFill_BSplineCurves is the allowed boundary-exact Coons primitive")

parent = parent_recipe()
base = ob.from_recipe(parent)
check(ob.is_airtight(base), "twice-filleted owner solid is valid and airtight")
check(ob.face_types(base) == {"PLANE": 6, "CYLINDER": 3, "SPHERE": 1},
      f"owner solid retains exact analytic faces ({ob.face_types(base)})")
check(ob.local_continuity_report(base) == {"C0": 12, "G1": 9},
      f"owner continuity baseline is explicit ({ob.local_continuity_report(base)})")

success_axes = {
    ("face", 2): {0, 1, 2},
    ("edge", 20): {0, 1, 2},
    ("edge", 5): {0, 1, 2},
    ("vertex", 1): {0, 1, 2},
    ("vertex", 11): {0, 1, 2},
}
results = {}
for (kind, index), axes in success_axes.items():
    signature = ob.local_subobject_signature(base, kind, index)
    check(json.loads(json.dumps(signature)) == signature,
          f"{kind}{index} target signature is JSON-safe")
    for axis in range(3):
        for sign in (1.0, -1.0):
            vector = tuple(sign * 0.5 if coordinate == axis else 0.0
                           for coordinate in range(3))
            key = kind, index, vector
            try:
                result = ob.move_subobject_local(base, kind, index, vector, signature)
            except KernelError as exc:
                results[key] = "refuse"
                expected_refusal = axis not in axes and "local move refused" in str(exc)
                check(expected_refusal,
                      f"{kind}{index} {vector} explicitly refuses outside safe matrix: {exc}")
                continue
            results[key] = "success"
            check(axis in axes, f"{kind}{index} {vector} succeeds only in safe matrix")
            check(validate_result(base, result, kind, index, vector),
                  f"{kind}{index} {vector} passes independent exact/containment/B-rep oracle")
            if (kind, index) in (("edge", 20), ("vertex", 11)) and axis in (0, 2):
                quality, quality_tolerances = accepted_composite_quality(
                    base, kind, index, vector)
                records = composite_face_records(result)
                check(len(quality) == 1 and quality[0]["degrees"] == (2, 1)
                      and quality[0]["poles"] == (5, 2)
                      and independent_boundary_error(
                          quality[0]["face"], quality[0]["source_boundary"])
                      <= quality_tolerances["compare"]
                      and independent_normal_jump(
                          BRepAdaptor_Surface(quality[0]["face"]).BSpline())
                      <= ob.LOCAL_COONS_NORMAL_JUMP
                      and len(records) == 1 and len(records[0][2]) == 5,
                      f"{kind}{index} {vector} composite is boundary-exact, smooth, 2x1/5x2, "
                      "and adds no seam")

for sign in (1.0, -1.0):
    vector = (0.0, sign * 0.5, 0.0)
    candidates, tolerances = composite_quality_candidates(base, "face", 2, vector)
    boundary_exact = [candidate for candidate in candidates
                      if independent_boundary_error(
                          candidate["face"], candidate["source_boundary"])
                      <= tolerances["compare"]]
    best_jump = min(independent_normal_jump(
        BRepAdaptor_Surface(candidate["face"]).BSpline()) for candidate in boundary_exact)
    check(boundary_exact and best_jump > ob.LOCAL_COONS_NORMAL_JUMP
          and best_jump > math.radians(0.7)
          and results[("face", 2, vector)] == "success",
          f"face2 {vector} accepts boundary-exact candidates with measured crease "
          f"({math.degrees(best_jump):.6f} degrees)")

representatives = {
    ("face", 2): (0.5, 0.0, 0.0),
    ("edge", 20): (0.5, 0.0, 0.0),
    ("edge", 5): (0.5, 0.0, 0.0),
    ("vertex", 1): (0.5, 0.0, 0.0),
    ("vertex", 11): (0.5, 0.0, 0.0),
}
continuity_after = {
    ("face", 2): {"C0": 18, "C1": 1, "G1": 2},
    ("edge", 20): {"C0": 13, "C1": 1, "G1": 7},
    ("edge", 5): {"C0": 19, "G1": 2},
    ("vertex", 1): {"C0": 17, "G1": 4},
    ("vertex", 11): {"C0": 13, "C1": 1, "G1": 7},
}
fresh_payload = []
for (kind, index), vector in representatives.items():
    recipe = move_recipe(parent, base, kind, index, vector)
    direct = ob.move_subobject_local(base, kind, index, vector, recipe["target"])
    rebuilt = ob.from_recipe(recipe)
    check(validate_result(base, rebuilt, kind, index, vector),
          f"{kind}{index} local_move recipe resolves signature and rebuilds deterministically")
    check(abs(ob.volume(rebuilt) - ob.volume(direct)) <= 1e-8,
          f"{kind}{index} recipe/direct volumes agree")
    check(ob.local_continuity_report(direct) == continuity_after[(kind, index)],
          f"{kind}{index} continuity loss is explicit, not claimed G1 preservation")
    if (kind, index) in (("edge", 20), ("vertex", 11)):
        quality, tolerances = accepted_composite_quality(base, kind, index, vector)
        check(len(quality) == 1,
              f"{kind}{index} has one unambiguous smooth composite-natural-Coons candidate")
        candidate = quality[0]
        check(candidate["degrees"] == (2, 1) and candidate["poles"] == (5, 2),
              f"{kind}{index} composite is degree 2x1 with 5x2 poles")
        check(independent_boundary_error(candidate["face"], candidate["source_boundary"])
              <= tolerances["compare"],
              f"{kind}{index} composite boundary correspondence is exact bidirectionally")
        check(independent_normal_jump(BRepAdaptor_Surface(candidate["face"]).BSpline())
              <= ob.LOCAL_COONS_NORMAL_JUMP,
              f"{kind}{index} full-knot normal jump is numerical zero")
        records = composite_face_records(direct)
        check(len(records) == 1 and len(records[0][2]) == 5,
              f"{kind}{index} final solid has one split five-edge composite face, no extra seam")
    parts = {"face": ob.unique_faces(base), "edge": ob._unique_edges(base),
             "vertex": ob._unique_vertices(base)}[kind]
    wrong_hint = index % len(parts) + 1
    rerouted = ob.from_recipe(move_recipe(parent, base, kind, index, vector, wrong_hint))
    check(validate_result(base, rerouted, kind, index, vector),
          f"{kind}{index} target signature overrides stale index hint {wrong_hint}")
    fresh_payload.append({"parent": parent, "move": recipe, "component": kind,
                          "index": index, "vector": list(vector)})

bad_target = ob.local_subobject_signature(base, "face", 2)
bad_target["type"] = "PLANE"
try:
    ob.move_subobject_local(base, "face", 2, (0.5, 0.0, 0.0), bad_target)
except KernelError as exc:
    check("resolved to 0" in str(exc), f"tampered target signature refuses explicitly: {exc}")
else:
    check(False, "tampered target signature must refuse")

# The sphere corner has one unchanged zero-length pole boundary. Edge 5 can move because
# that boundary contributes no curve and collapses to an equivalent singular 3-boundary patch.
face6_signature = ob.local_subobject_signature(base, "face", 6)
check(face6_signature["adjacency"]["degenerated"] == 1,
      "owner sphere patch records one unchanged zero-length pole boundary")
edge5_result = ob.move_subobject_local(
    base, "edge", 5, (0.5, 0.0, 0.0), ob.local_subobject_signature(base, "edge", 5))
check(ob.is_airtight(edge5_result) and topology_counts(edge5_result) == topology_counts(base),
      "unchanged zero-length boundary collapses to equivalent singular Coons patch")
try:
    ob.move_subobject_local(base, "vertex", 8, (0.5, 0.0, 0.0))
except KernelError as exc:
    check("changed degenerate" in str(exc), f"changed degenerate edge refuses: {exc}")
else:
    check(False, "changed degenerate edge must refuse")

cylinder = ob.make_cylinder(2.0, 5.0)
cap_index = next(i for i, face in enumerate(ob.unique_faces(cylinder), 1)
                 if ob.face_plane(face) is not None)
try:
    ob.move_subobject_local(cylinder, "face", cap_index, (0.0, 0.0, 0.5))
except KernelError as exc:
    check("periodic same-face seam" in str(exc) or "changed degenerate" in str(exc),
          f"affected unsupported periodic/seam structure refuses explicitly: {exc}")
else:
    check(False, "affected unsupported periodic seam must refuse")

for scale in (1e-3, 1.0, 1e3):
    scaled_parent = parent_recipe(scale)
    scaled = ob.from_recipe(scaled_parent)
    tolerances = ob._local_tolerances(scaled)
    check(0.0 < tolerances["compare"] < tolerances["build"] <= tolerances["sew"],
          f"scale {scale:g} derives bounded comparison/build/sew tolerances {tolerances}")
    for (kind, index), unit_vector in representatives.items():
        vector = tuple(value * scale for value in unit_vector)
        signature = ob.local_subobject_signature(scaled, kind, index)
        moved = ob.move_subobject_local(scaled, kind, index, vector, signature)
        check(validate_result(scaled, moved, kind, index, vector),
              f"scale {scale:g} {kind}{index} preserves exact local-move contract")
        if (kind, index) in (("edge", 20), ("vertex", 11)):
            quality, quality_tolerances = accepted_composite_quality(
                scaled, kind, index, vector)
            check(len(quality) == 1 and quality[0]["degrees"] == (2, 1)
                  and quality[0]["poles"] == (5, 2)
                  and independent_boundary_error(
                      quality[0]["face"], quality[0]["source_boundary"])
                  <= quality_tolerances["compare"]
                  and independent_normal_jump(
                      BRepAdaptor_Surface(quality[0]["face"]).BSpline())
                  <= ob.LOCAL_COONS_NORMAL_JUMP,
                  f"scale {scale:g} {kind}{index} composite quality remains bounded")

angle = math.radians(31.0)
c, s = math.cos(angle), math.sin(angle)
transform = [c, -s, 0.0, 7.0,
             s, c, 0.0, -4.0,
             0.0, 0.0, 1.0, 2.0,
             0.0, 0.0, 0.0, 1.0]
rotated_parent = parent_recipe(transform=transform)
rotated = ob.from_recipe(rotated_parent)
for (kind, index), local_vector in representatives.items():
    vector = (c * local_vector[0] - s * local_vector[1],
              s * local_vector[0] + c * local_vector[1], local_vector[2])
    moved = ob.move_subobject_local(
        rotated, kind, index, vector, ob.local_subobject_signature(rotated, kind, index))
    check(validate_result(rotated, moved, kind, index, vector),
          f"rotated/transformed parent {kind}{index} preserves exact local-move contract")
    if (kind, index) in (("edge", 20), ("vertex", 11)):
        quality, quality_tolerances = accepted_composite_quality(
            rotated, kind, index, vector)
        check(len(quality) == 1 and quality[0]["degrees"] == (2, 1)
              and quality[0]["poles"] == (5, 2)
              and independent_boundary_error(
                  quality[0]["face"], quality[0]["source_boundary"])
              <= quality_tolerances["compare"]
              and independent_normal_jump(
                  BRepAdaptor_Surface(quality[0]["face"]).BSpline())
              <= ob.LOCAL_COONS_NORMAL_JUMP,
              f"rotated/transformed {kind}{index} composite quality remains bounded")

with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as stream:
    json.dump(fresh_payload, stream)
    fresh_path = stream.name
try:
    process = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "--fresh", fresh_path],
        cwd=ROOT, text=True, capture_output=True, timeout=120,
        env={**os.environ, "PYTHONPATH": os.path.join(ROOT, "vendor", "occt")},
        check=False,
    )
finally:
    os.unlink(fresh_path)
check(process.returncode == 0 and "FRESH-LOCAL-MOVE-OK" in process.stdout,
      f"fresh-process face/edge/vertex recipes and downstream booleans pass: "
      f"{process.stdout.strip()} {process.stderr.strip()}")

print("\nSUCCESS/REFUSAL MATRIX")
for (kind, index), axes in success_axes.items():
    row = []
    for axis, label in enumerate("XYZ"):
        states = [results[(kind, index, tuple(sign * 0.5 if i == axis else 0.0
                                              for i in range(3)))]
                  for sign in (1.0, -1.0)]
        row.append(f"{label}: {states[0]}/{states[1]}")
    print(f"  {kind}{index}: " + ", ".join(row))

print(f"\n{checks - len(failures)}/{checks} hardened local B-rep move checks passed")
if failures:
    raise SystemExit(1)
