"""Copy/Explode retain exact curves and restore screen-tolerance display after Undo.

Actual Blender/SDK projection and command integration requires Python. Sampling,
projection and distance queries use the existing Rust/SDK paths, not a new engine.
An optional --fixture-file/--curve-name pair exercises a private local document.
"""
import json
from pathlib import Path
import sys
import tempfile
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg
sys.path.insert(0, str(ROOT))
import bpy
import rhino3dm as r3
from mathutils import Euler, Matrix, Vector
from narwhal3d import bridge, commands, gumball, native_selection, ui
from narwhal3d.state import app_state
from narwhal3d.model import NarwhalModel

checks = 0


def check(ok, message):
    global checks
    checks += 1
    assert ok, message


def generated():
    curve = r3.PolyCurve()
    for i in range(4):
        x = 2.0 * i
        check(curve.Append(r3.Arc(r3.Point3d(x, 0, 0), r3.Point3d(x+1, 1, 0),
                                 r3.Point3d(x+2, 0, 0)).ToNurbsCurve()), "append exact large arc")
    check(curve.Append(r3.Arc(r3.Point3d(8, 0, 0), r3.Point3d(8.01, .01, 0),
                             r3.Point3d(8.02, 0, 0)).ToNurbsCurve()), "append exact small arc")
    return curve


def parts(curve):
    return list(curve.Explode()) if isinstance(curve, r3.PolyCurve) else [curve]


def signature(curve):
    records = []
    for part in parts(curve):
        nc = part.ToNurbsCurve()
        records.append((type(part).__name__, nc.Degree, nc.IsRational,
                        tuple((p.X, p.Y, p.Z, p.W) for p in nc.Points),
                        tuple(nc.Knots.ToList()), (part.Domain.T0, part.Domain.T1)))
    return records


def camera(curve):
    # The snapshot need not contain a camera. Choose an explicit perspective at
    # its smallest curved feature and record the matrix; do not claim recovery
    # of the reporting application's original view.
    curved = [part for part in parts(curve) if not part.IsLinear()]
    feature = min(curved, key=lambda p: p.GetBoundingBox().Min.DistanceTo(p.GetBoundingBox().Max))
    middle = feature.PointAt((feature.Domain.T0+feature.Domain.T1)/2)
    focus = Vector((middle.X, middle.Y, middle.Z))
    data = bpy.data.cameras.new("curve-display-proof")
    obj = bpy.data.objects.new("curve-display-proof", data)
    bpy.context.scene.collection.objects.link(obj)
    data.lens = 160
    rotation = Euler((.03, .07, .13)).to_quaternion()
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = rotation
    obj.location = focus + rotation @ Vector((0, 0, 5))
    bpy.context.view_layer.update()
    matrix = obj.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(), x=1234, y=633) @ obj.matrix_world.inverted()
    region = types.SimpleNamespace(width=1234, height=633)
    view = types.SimpleNamespace(perspective_matrix=matrix, view_location=focus,
                                 view_rotation=rotation, view_perspective="PERSP",
                                 view_matrix=obj.matrix_world.inverted(), is_perspective=True)
    ppu = ui._pixels_per_unit(region, view, focus)
    check(ppu is not None and ppu > 500, "actual perspective projection has close-view scale")
    print("PERSPECTIVE", json.dumps({"width":1234,"height":633,"matrix":[list(v) for v in matrix],"ppu":ppu}), flush=True)
    return matrix, ppu


def screen_error(name, curve, matrix):
    obj = bpy.data.objects[name]
    record = ui._curve_record(obj)
    check(record is not None, "actual curve-pass CPU record exists")
    mesh_points = record["lines"]
    pixels, visible = native_selection.project_display_points(mesh_points, matrix, 1234, 633)
    offsets = list(range(0, len(pixels)+1, 2))
    samples = []
    for part in parts(curve):
        d = part.Domain
        for i in range(65):
            p = part.PointAt(d.T0+(d.T1-d.T0)*i/64)
            samples.append((p.X, p.Y, p.Z))
    exact, shown = native_selection.project_display_points(samples, matrix, 1234, 633)
    maximum = 0.0
    count = 0
    for point, is_visible in zip(exact, shown):
        if not is_visible or not (0 <= point[0] <= 1234 and 0 <= point[1] <= 633):
            continue
        result = native_selection.nearest_screen_polyline(pixels, visible, offsets, point, 1e6)
        check(result is not None and 0 <= result[0] < len(offsets)-1, "Rust measures projected display chord distance")
        maximum = max(maximum, result[1])
        count += 1
    return maximum if count else None


def refine(names, matrix, ppu, label):
    for name in names:
        ui._curve_record(bpy.data.objects[name])  # warm the same CPU cache used by drawing
    with patch.object(ui, "navigating", return_value=False), patch.object(ui, "_live_region_pointers", return_value=None), patch.object(ui, "_visible_curve_names", return_value=set(names)):
        ui._pane_ppu.clear()
        ui._retess_ppu = ppu
        ui._retess_job.update(ppu=None, doc=None, queue=[])
        for _ in range(100):
            if ui._retess_tick() is None:
                break
        else:
            raise AssertionError("bounded retessellation slices did not finish")
    errors = [screen_error(name, app_state.ctx.geom_by_name(name), matrix) for name in names]
    errors = [value for value in errors if value is not None]
    check(bool(errors), label+": has visible curve samples")
    vertices = sum(len(bpy.data.objects[name].data.vertices) for name in names)
    check(vertices < 20000, label+": display tessellation remains bounded")
    print(label, "maximum pixel chord error", max(errors), "vertices", vertices, flush=True)
    check(max(errors) <= .6, label+": screen chord error exceeds subpixel tolerance")
    for name in names:
        geom = app_state.ctx.geom_by_name(name)
        if not geom.IsLinear() and not geom.IsPolyline():
            # Exact polylines keep their original vertices and need no tolerance tag.
            tolerance = bpy.data.objects[name].data.get(bridge.CURVE_DISPLAY_TOL_KEY)
            check(tolerance is not None and tolerance * ppu <= .500001,
                  label+": display records its requested screen chord tolerance")
    check(not ui._retess_queue(ppu), label+": unchanged zoom does not queue repeated work")


def main():
    bpy.data.batch_remove(list(bpy.data.objects))
    ui._attach_curve_depsgraph_handler()
    if "--fixture-file" in sys.argv:
        path = sys.argv[sys.argv.index("--fixture-file")+1]
        name = sys.argv[sys.argv.index("--curve-name")+1]
        source = r3.File3dm.Read(path)
        curve = next(o.Geometry.Duplicate() for o in source.Objects if o.Attributes.Name == name)
        print("PRIVATE", type(curve).__name__, "segments", len(parts(curve)), "views", len(source.Views), flush=True)
    else:
        curve = generated()
    original = signature(curve)
    app_state.set_model(NarwhalModel())
    ctx = app_state.ctx
    _, name = ctx.add_geometry(curve, "Source", 0)
    ctx.undo.clear()
    ui.forget_curve_densities()
    matrix, ppu = camera(curve)
    print("INITIAL maximum pixel chord error", screen_error(name, curve, matrix), "vertices", len(bpy.data.objects[name].data.vertices), flush=True)
    refine([name], matrix, ppu, "original at zoom")
    ctx.select_by_names([name])
    command = commands.CopyCmd()
    command.args = [[name], (0,0,0), (0,0,0)]
    message, _ = command._recorded(ctx, lambda: command.execute(ctx))
    check("Created 1 copy" in message, message)
    copy_name = next(o.Attributes.Name for o in ctx.model.file.Objects if o.Attributes.Name != name)
    check(signature(ctx.geom_by_name(copy_name)) == original and signature(ctx.geom_by_name(name)) == original,
          "Copy preserves exact degree, controls, rational weights, knots and domains")
    deferred = []
    ui._retess_queue(ppu, visible={name}, deferred=deferred)
    check(copy_name in deferred, "coarse off-screen copy is deferred until visible")
    refine([copy_name], matrix, ppu, "Copy")
    check(ctx.undo_last() == "Copy", "Copy is one Undo step")
    check(ctx.redo_last() == "Copy", "Copy Redo")
    refine([copy_name], matrix, ppu, "Copy Redo")
    ctx.select_by_names([copy_name])
    command = commands.ExplodeCmd()
    message, _ = command._recorded(ctx, lambda: command.execute(ctx))
    check(message.startswith("Exploded 1 object"), message)
    exploded = [o for o in ctx.model.file.Objects if o.Attributes.Name != name]
    check(len(exploded) == len(original), "Explode retains every original exact segment")
    check([signature(o.Geometry)[0] for o in exploded] == original,
          "Explode preserves exact source segment degrees, controls, weights, knots and domains")
    refine([o.Attributes.Name for o in exploded], matrix, ppu, "Explode")
    for obj in exploded[:3]:
        ctx.select_by_names([obj.Attributes.Name])
        delete = commands.DeleteCmd()
        message, _ = delete._recorded(ctx, lambda: delete.execute(ctx))
        check(message.startswith("Deleted 1"), message)
    for _ in range(3):
        check(ctx.undo_last() == "Delete", "restore each of three separate Deletes")
    refine([o.Attributes.Name for o in exploded], matrix, ppu, "three Delete Undos")
    check(ctx.undo_last() == "Explode", "Undo Explode restores the joined curve")
    check(signature(ctx.geom_by_name(copy_name)) == original, "Undo Explode preserves authoritative NURBS")
    refine([copy_name], matrix, ppu, "Undo Explode")
    # A new mesh may have the same count: density belongs to the actual datablock,
    # not a reused name/vertex count after Undo or display reconstruction.
    obj = bpy.data.objects[copy_name]
    old = obj.data
    obj.data = old.copy()
    check(ui._recorded_density(copy_name) is None, "same-count replacement mesh invalidates density affinity")
    refine([copy_name], matrix, ppu, "same-count replacement")
    # In-place display rebuild also resets the requested chord tolerance, even
    # when the Blender object and mesh UUIDs remain unchanged.
    check(bridge.rebuild_object_mesh(copy_name, ctx.geom_by_name(copy_name)), "in-place display rebuild")
    refine([copy_name], matrix, ppu, "in-place rebuild")
    ctx.select_by_names([copy_name])
    commands.apply_transform(ctx, bridge.translation_xform((.02,0,0)))
    refine([copy_name], matrix, ppu, "Move")
    check(ctx.undo_last() is not None, "Undo moved copy")
    refine([copy_name], matrix, ppu, "Undo Move")
    ctx.select_by_names([copy_name])
    obj = bpy.data.objects[copy_name]
    ctx.begin_undo("Gumball Move")
    ui._gumball_drag = {"kind": gumball.MOVE, "mode": gumball.MOVE, "axis": 0,
                        "total": .02, "defer": {"start": {copy_name: obj.matrix_world.copy()},
                        "acc": Matrix.Translation((.02,0,0))}}
    ui._gumball_end()
    refine([copy_name], matrix, ppu, "Gumball release")
    check(ctx.undo_last() == "Gumball Move", "Undo actual Gumball release")
    refine([copy_name], matrix, ppu, "Undo Gumball")
    check(signature(ctx.geom_by_name(copy_name)) == original, "final document curve remains exact")
    ctx.select_by_names([name])
    names_before = {o.Attributes.Name for o in ctx.model.file.Objects}
    command = commands.CopyCmd()
    command.args = [[name], (0,0,0), (.02,0,0)]
    message, _ = command._recorded(ctx, lambda: command.execute(ctx))
    check("Created 1 copy" in message, message)
    translated_name = next(o.Attributes.Name for o in ctx.model.file.Objects
                           if o.Attributes.Name not in names_before)
    expected = bridge.transform_geometry(curve.Duplicate(), bridge.translation_xform((.02,0,0)))
    check(signature(ctx.geom_by_name(translated_name)) == signature(expected),
          "nonzero Copy retains exact translated NURBS definition")
    refine([translated_name], matrix, ppu, "translated Copy")
    check(ctx.undo_last() == "Copy" and ctx.redo_last() == "Copy", "translated Copy one Undo/Redo")
    refine([translated_name], matrix, ppu, "translated Copy Redo")
    check(ctx.geom_by_name(copy_name).GetUserString(bridge.CURVE_DISPLAY_TOL_KEY) in (None, ""),
          "display tolerance never becomes authoritative geometry metadata")
    with tempfile.TemporaryDirectory(prefix="curve-display-exact-roundtrip-") as directory:
        path = str(Path(directory) / "curves.3dm")
        check(ctx.model.save(path), "curve document save")
        reopened = NarwhalModel.load(path)
        saved = next(o.Geometry for o in reopened.file.Objects if o.Attributes.Name == copy_name)
        check(signature(saved) == original, "save/reload retains exact copied/exploded NURBS")
    print(f"CURVE DISPLAY COPY/EXPLODE PASSED ({checks} checks)", flush=True)


if __name__ == "__main__":
    main()
