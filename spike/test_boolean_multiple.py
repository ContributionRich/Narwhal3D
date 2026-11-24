"""Actual Blender/OCCT multi-result publication. Python is unavoidable host API glue.

Run with blender -b --factory-startup --python spike/test_boolean_multiple.py.
No private owner geometry is embedded in this suite.
"""
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg
sys.path.insert(0, str(ROOT))
import bpy
import rhino3dm as r3
from narwhal3d import bridge, commands, model, kernel
from narwhal3d.kernel import occt_backend as occt


def objects(ctx):
    return {o.Attributes.Name: o for o in ctx.model.file.Objects}


def exact(geom):
    master = kernel.master_of(geom) or bridge._occt_operand(geom)
    assert master is not None and occt.is_airtight(master)
    assert occt.volume(master) > 0
    return master


def shape_signature(shape):
    return (round(occt.volume(shape), 8), occt.face_count(shape),
            tuple(round(x, 7) for corner in occt.tight_bbox(shape) for x in corner),
            tuple(sorted(occt.face_types(shape).items())))


def signatures(ctx):
    return {n: shape_signature(exact(o.Geometry))
            for n, o in objects(ctx).items() if n.startswith("Boolean")}


def context(curved=False):
    bpy.data.batch_remove(list(bpy.data.objects))
    ctx = commands.NarwhalContext(model.NarwhalModel())
    target = (bridge.make_sphere((0, 0, 0), 3) if curved else
              bridge.make_box((-3, -2, -2), (3, 2, -2), 4))
    cutter = bridge.make_box((-1, -5, -5), (1, 5, -5), 10)
    if not curved:
        bridge.transform_geometry(target, r3.Transform.Translation(0, 0, -2))
    bridge.transform_geometry(cutter, r3.Transform.Translation(0, 0, -5))
    layer = ctx.model.add_layer("Manufacturing target", (150, 80, 30))
    ctx.add_geometry(target, "Target", layer)
    ctx.add_geometry(cutter, "Cutter", 0)
    target_attrs = objects(ctx)["Target1"].Attributes
    group = r3.Group()
    group.Name = "source group"
    ctx.model.file.Groups.Add(group)
    group_index = ctx.model.file.Groups[0].Index
    target_attrs.AddToGroup(group_index)
    target_attrs.SetUserString("manufacturing", "source attributes")
    ctx.undo.clear()
    return ctx


def run(ctx, options=None, preselect=True, targets=("Target1",), cutters=("Cutter1",)):
    cmd = commands.BooleanDifferenceCmd()
    cmd.opt.update(options or {})
    ctx.select_by_names(list(targets) if preselect else [])
    message, done = cmd.begin(ctx)
    assert not done, (message, done)
    if not preselect:
        for name in targets:
            message, done = cmd.feed(name, ctx)
        message, done = cmd.feed("", ctx)
        assert not done
    # Exercise the actual Blender second-set selection, not only typed names.
    ctx.select_by_names(list(cutters))
    message, done = cmd.feed("", ctx)
    assert done, message
    return message


def reload_check(path, expected):
    ctx = commands.NarwhalContext(model.NarwhalModel.load(path))
    assert json.loads(json.dumps(signatures(ctx))) == expected, (signatures(ctx), expected)
    for name, obj in objects(ctx).items():
        if not name.startswith("Boolean"):
            continue
        master = exact(obj.Geometry)
        step = str(Path(path).with_name(name + ".step"))
        occt.export_step(master, step)
        restored_shapes = occt.import_step(step)["solids"]
        assert len(restored_shapes) == 1
        restored = restored_shapes[0]
        assert occt.is_airtight(restored)
        assert occt.face_count(restored) == occt.face_count(master)
        assert occt.face_types(restored) == occt.face_types(master)
        for a, b in zip(occt.tight_bbox(restored), occt.tight_bbox(master)):
            assert all(math.isclose(x, y, abs_tol=1e-7) for x, y in zip(a, b))
        assert math.isclose(occt.volume(restored), occt.volume(master), rel_tol=1e-9)
    print("fresh-process component save/reload and STEP: GREEN")


def main():
    for curved in (False, True):
        for preselect in (False, True):
            ctx = context(curved)
            before = {n: str(o.Attributes.Id) for n, o in objects(ctx).items()}
            message = run(ctx, preselect=preselect)
            assert message.startswith("Created "), message
            results = [o for n, o in objects(ctx).items() if n.startswith("Boolean")]
            assert len(results) == 2, message
            expected_volume = 32 if not curved else 28 * math.pi / 3
            for obj in results:
                master = exact(obj.Geometry)
                assert math.isclose(occt.volume(master), expected_volume, rel_tol=1e-9)
                assert occt.face_count(master) in ((2, 3) if curved else (6,))
                assert obj.Attributes.GetUserString("manufacturing") == "source attributes"
                assert obj.Attributes.GetGroupList2() == [0]
                recipe = kernel.recipe_of(obj.Geometry)
                assert recipe["kind"] == "native_master"
                assert shape_signature(occt.from_recipe(recipe)) == shape_signature(master), "unstable component recipe binding"
                assert obj.Attributes.LayerIndex == 1
                if curved:
                    assert occt.face_types(master) in ({"SPHERE": 1, "PLANE": 1}, {"SPHERE": 2, "PLANE": 1})
            assert set(objects(ctx)) == {"Cutter1", "Boolean1", "Boolean2"}
            after = signatures(ctx)
            assert ctx.undo_last() == "BooleanDifference"
            assert {n: str(o.Attributes.Id) for n, o in objects(ctx).items()} == before
            assert ctx.redo_last() == "BooleanDifference"
            assert signatures(ctx) == after
            assert all(o.Attributes.LayerIndex == 1 and o.Attributes.GetGroupList2() == [0]
                       for n, o in objects(ctx).items() if n.startswith("Boolean"))
            assert ctx.undo_last() == "BooleanDifference" and ctx.undo_last() is None
            print("two exact components, prompts, attributes, one Undo/Redo:", curved, preselect, "GREEN")

    for options, names in (({"DeleteInput": False}, {"Target1", "Cutter1"}),
                           ({"DeleteCutters": True}, set())):
        ctx = context()
        assert run(ctx, options).startswith("Created ")
        assert set(objects(ctx)) == names | {"Boolean1", "Boolean2"}

    ctx = context(True)
    # The single-result API MUST NOT return a compound or lose a component.
    try:
        bridge.boolean_brep(objects(ctx)["Target1"].Geometry,
                            objects(ctx)["Cutter1"].Geometry, "DIFFERENCE")
        raise AssertionError("legacy single-result API accepted multiple solids")
    except ValueError as exc:
        assert "separate solids" in str(exc)

    for edit_source in (False, True):
        ctx = context()
        if not edit_source:
            ctx.begin_undo("Existing operation")
            ctx.add_geometry(bridge.make_sphere((20, 0, 0), 1), "Existing", 0)
            ctx.end_undo()
            assert ctx.undo_last() == "Existing operation"
            assert ctx.undo.redo_names() == ["Existing operation"]
        original = ctx._make_display_object
        before_ids = {str(o.Attributes.Id) for o in ctx.model.file.Objects}
        before_displays = {o.as_pointer() for o in bpy.data.objects}
        calls = 0
        def failing(*args, **kwargs):
            nonlocal calls
            calls += 1
            display = original(*args, **kwargs)
            if calls == 2:
                if edit_source:
                    objects(ctx)["Target1"].Attributes.SetUserString("callback", "retained")
                raise RuntimeError("injected second display failure")
            return display
        ctx._make_display_object = failing
        message = run(ctx)
        assert message.startswith("Boolean failed:"), message
        assert {str(o.Attributes.Id) for o in ctx.model.file.Objects} == before_ids
        assert {o.as_pointer() for o in bpy.data.objects} == before_displays
        if edit_source:
            assert objects(ctx)["Target1"].Attributes.GetUserString("callback") == "retained"
            assert ctx.undo_last() is not None
        else:
            assert ctx.undo_last() is None
            assert ctx.undo.redo_names() == ["Existing operation"]
            assert ctx.redo_last() == "Existing operation"
        print("second-add rollback, original displays/history, callback preservation:", edit_source, "GREEN")

    for failure in ("after-return", "source-edit"):
        ctx = context()
        original = ctx.add_geometry
        before_ids = {str(o.Attributes.Id) for o in ctx.model.file.Objects}
        before_displays = {o.as_pointer() for o in bpy.data.objects}
        calls = 0
        def interrupted(*args, **kwargs):
            nonlocal calls
            result = original(*args, **kwargs)
            calls += 1
            if calls == 2:
                if failure == "source-edit":
                    objects(ctx)["Target1"].Attributes.SetUserString("callback", "retained")
                else:
                    raise RuntimeError("injected add wrapper failure after publication")
            return result
        ctx.add_geometry = interrupted
        message = run(ctx, {"DeleteCutters": True})
        assert message.startswith("Boolean failed:"), message
        assert {str(o.Attributes.Id) for o in ctx.model.file.Objects} == before_ids
        assert {o.as_pointer() for o in bpy.data.objects} == before_displays
        if failure == "source-edit":
            assert objects(ctx)["Target1"].Attributes.GetUserString("callback") == "retained"
        else:
            assert ctx.undo_last() is None
        print("post-add failure/source mutation leaves originals and no partial result:", failure, "GREEN")

    # Disjoint intersection and full subtraction keep the historical named refusal,
    # whereas a disjoint difference preserves its unchanged target as one result.
    for operation, shift, should_succeed in (("INTERSECT", 50, False),
                                              ("DIFFERENCE", 0, False),
                                              ("DIFFERENCE", 50, True)):
        ctx = context()
        if shift == 0:
            target = objects(ctx)["Target1"].Geometry
            cutter = target.Duplicate()
        else:
            cutter = objects(ctx)["Cutter1"].Geometry.Duplicate()
            bridge.transform_geometry(cutter, r3.Transform.Translation(shift, 0, 0))
        ctx.model.replace_geometry(ctx.model.object_id_by_name("Cutter1"), cutter)
        before = {n: o.Geometry.Encode() for n, o in objects(ctx).items()}
        command = commands.BooleanIntersectionCmd() if operation == "INTERSECT" else commands.BooleanDifferenceCmd()
        command.args = [["Target1"], ["Cutter1"]]
        message, _ = command._recorded(ctx, lambda: command.execute(ctx))
        if should_succeed:
            assert message.startswith("Created ") and len(signatures(ctx)) == 1
        else:
            assert "produced no solid" in message, message
            assert {n: o.Geometry.Encode() for n, o in objects(ctx).items()} == before
            assert ctx.undo_last() is None

    # Union retains disjoint outputs, then can rejoin ALL intermediate pieces.
    for reconnect in (False, True):
        ctx = context()
        other = objects(ctx)["Target1"].Geometry.Duplicate()
        bridge.transform_geometry(other, r3.Transform.Translation(10, 0, 0))
        ctx.model.replace_geometry(ctx.model.object_id_by_name("Cutter1"), other)
        command = commands.BooleanUnionCmd()
        command.args = [["Target1", "Cutter1"]]
        if reconnect:
            connector = bridge.make_box((0, -2, 0), (10, 2, 0), 4)
            bridge.transform_geometry(connector, r3.Transform.Translation(0, 0, -2))
            ctx.add_geometry(connector, "Connector", 0)
            command.args[0].append("Connector1")
        message, _ = command._recorded(ctx, lambda: command.execute(ctx))
        assert message.startswith("Created ") and len(signatures(ctx)) == (1 if reconnect else 2)
        if reconnect:
            assert math.isclose(occt.volume(exact(objects(ctx)["Boolean1"].Geometry)), 256, rel_tol=1e-10)

    # One connected U-shaped target intersected below its connecting bar gives
    # two components. This exercises the actual intersection command, not a compound proxy.
    ctx = context()
    left = bridge.make_box((-3, -2, 0), (-1, 2, 0), 4)
    right = bridge.make_box((1, -2, 0), (3, 2, 0), 4)
    bar = bridge.make_box((-3, 1, 0), (3, 3, 0), 4)
    target = bridge.boolean_brep(bridge.boolean_brep(left, bar, "UNION"), right, "UNION")
    ctx.model.replace_geometry(ctx.model.object_id_by_name("Target1"), target)
    ctx.model.replace_geometry(ctx.model.object_id_by_name("Cutter1"),
                               bridge.make_box((-5, -3, 0), (5, 0, 0), 4))
    command = commands.BooleanIntersectionCmd()
    command.args = [["Target1"], ["Cutter1"]]
    message, _ = command._recorded(ctx, lambda: command.execute(ctx))
    assert message.startswith("Created ") and len(signatures(ctx)) == 2
    assert all(math.isclose(occt.volume(exact(o.Geometry)), 16, rel_tol=1e-10)
               for n,o in objects(ctx).items() if n.startswith("Boolean"))

    ctx = context(True)
    assert run(ctx).startswith("Created ")
    with tempfile.TemporaryDirectory(prefix="narwhal-boolean-components-") as directory:
        path = str(Path(directory) / "components.3dm")
        assert ctx.model.save(path)
        expected = signatures(ctx)
        subprocess.run([bpy.app.binary_path, "-b", "--factory-startup", "--python-exit-code", "1", "--python", __file__,
                        "--", "--reload", path, json.dumps(expected)], check=True, timeout=90)
    print("multi-result actual-host suite: GREEN")


if __name__ == "__main__":
    if "--reload" in sys.argv:
        i = sys.argv.index("--reload")
        reload_check(sys.argv[i + 1], json.loads(sys.argv[i + 2]))
    else:
        main()
