"""Actual host: exact BRep picker cache follows per-object values, not global Undo.

Python only orchestrates Blender/SDK objects. Existing native geometry creation,
serialization, validation and topology remain authoritative; no alternate engine.
"""
from pathlib import Path
import math
import time
import sys
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg
sys.path.insert(0, str(ROOT))
import bpy
import rhino3dm as r3
from narwhal3d import bridge, kernel, ui
from narwhal3d.model import NarwhalModel
from narwhal3d.state import app_state
from narwhal3d.kernel import occt_backend as occt

checks = 0


def check(ok, message):
    global checks
    checks += 1
    assert ok, message


def obj(name):
    return app_state.model.file.Objects.FindId(app_state.model.object_id_by_name(name))


def fresh_parts(name):
    return bridge.subobject_parts(obj(name).Geometry)


def vertices(parts):
    return tuple((index, tuple(round(float(v), 9) for v in point))
                 for index, point in parts["vertices"])


def main():
    bpy.data.batch_remove(list(bpy.data.objects))
    app_state.set_model(NarwhalModel())
    ctx = app_state.ctx
    _, a = ctx.add_geometry(bridge.make_box((0,0,0), (4,3,0), 2), "Recipe", 0)
    master_geom = bridge.make_cylinder((10,0,0), 1, 3)
    kernel.register_master(master_geom, bridge._occt_operand(master_geom))
    _, b = ctx.add_geometry(master_geom, "Master", 0)
    ctx.undo.clear()
    check(kernel.master_of(obj(a).Geometry) is None, "real producer is recipe-only")
    check(kernel.recipe_of(obj(a).Geometry) is not None, "recipe-only source is analytic/rebuildable")
    ui._brep_parts_cache.update(serial=object(), parts={})
    calls = []
    original = occt.from_recipe
    def counted(recipe, *args, **kwargs):
        calls.append(recipe["kind"])
        return original(recipe, *args, **kwargs)
    with patch.object(occt, "from_recipe", counted):
        parts = ui._brep_parts_all()
        check(set(parts) == {a,b}, "both exact solids expose pickable parts")
        check(calls == ["box"], "cold picker rebuilds recipe once")
        calls.clear()
        check(vertices(ui._brep_parts_all()[a]) == vertices(parts[a]) and not calls,
              "warm picker reuses exact parts without recipe evaluation")
        ctx.begin_undo("unrelated curve")
        ctx.add_geometry(bridge.make_line((20,0,0), (21,1,0)), "Line", 0)
        ctx.end_undo()
        calls.clear()
        ui._brep_parts_all()
        check(not calls, "unrelated edit must not rebuild any unchanged recipe-only solid")
        for direction in (ctx.undo_last, ctx.redo_last):
            direction()
            calls.clear()
            ui._brep_parts_all()
            check(not calls, "unrelated Undo/Redo does not rebuild exact solids")
        # In-place recipe edits must invalidate even before an Undo serial changes.
        ctx.begin_undo("edit source recipe")
        ctx.note_change()
        geometry = obj(a).Geometry
        recipe = kernel.recipe_of(geometry)
        recipe["dz"] = 5
        kernel.record_recipe(geometry, recipe)
        calls.clear()
        changed = ui._brep_parts_all()[a]
        check(calls == ["box"], "actual recipe edit invalidates this solid exactly once")
        check(max(p[2] for _,p in changed["vertices"]) == 5, "parts follow edited recipe, not old proxy")
        check(vertices(changed) == vertices(fresh_parts(a)), "edited recipe matches fresh exact topology")
        ctx.end_undo()
        for direction in (ctx.undo_last, ctx.redo_last):
            direction()
            calls.clear()
            changed = ui._brep_parts_all()[a]
            check(calls == ["box"] and vertices(changed) == vertices(fresh_parts(a)),
                  "source Undo/Redo invalidates and matches fresh topology")
        # Restore a coherent source, then transform it in place through the supported API.
        ctx.begin_undo("replace solid")
        ctx.note_change()
        ctx.model.replace_geometry(obj(a).Attributes.Id, bridge.make_box((0,0,0),(4,3,0),2))
        ctx.end_undo()
        ui._brep_parts_all()
        ctx.begin_undo("move source in place")
        ctx.note_change()
        geometry = obj(a).Geometry
        moved = bridge.transform_geometry(geometry, r3.Transform.Translation(7,0,0))
        if moved is not geometry:
            ctx.model.replace_geometry(obj(a).Attributes.Id, moved)
        calls.clear()
        moved_parts = ui._brep_parts_all()[a]
        check(calls == ["box"] and min(p[0] for _,p in moved_parts["vertices"]) == 7,
              "in-place transform is detected before record closure")
        ctx.end_undo()
        check(vertices(moved_parts) == vertices(fresh_parts(a)), "transformed recipe parts match fresh source")
        # A registered OCCT shape can be mutated without a new master key.
        source = obj(b).Geometry
        master = kernel.master_of(source)
        key = kernel.master_key_of(source)
        ui._brep_parts_all()
        from OCP.gp import gp_Trsf, gp_Vec
        from OCP.TopLoc import TopLoc_Location
        transform = gp_Trsf()
        transform.SetTranslation(gp_Vec(0,4,0))
        master.Move(TopLoc_Location(transform))  # test-only native mutation, not a document producer
        check(kernel.master_key_of(obj(b).Geometry) == key, "master key deliberately stays unchanged")
        moved_master_parts = ui._brep_parts_all()[b]
        low_y = min(p[1] for _,p in moved_master_parts["vertices"])
        check(abs(low_y - 4) < 1e-9,
              f"same-key in-place master mutation never reuses stale topology (y={low_y})")
        check(vertices(moved_master_parts) == vertices(fresh_parts(b)), "master mutation matches native truth")
        # Visibility is re-evaluated independently of cache reuse.
        bpy.data.objects[a].hide_set(True)
        calls.clear()
        check(a not in ui._brep_parts_all() and not calls, "hidden object is not picked or rebuilt")
        bpy.data.objects[a].hide_set(False)
        calls.clear()
        check(a in ui._brep_parts_all() and not calls, "unchanged visible source reuses its parts after reveal")
        # Replacing an object under the same name cannot inherit its identity.
        ctx.begin_undo("replace identity")
        ctx.note_change()
        old_id = str(obj(a).Attributes.Id)
        geometry = obj(a).Geometry.Duplicate()
        ctx.model.remove_object(old_id)
        new_id,_ = ctx.model.add_object(geometry, a, 0)
        calls.clear()
        replaced = ui._brep_parts_all()[a]
        check(str(new_id) != old_id and calls == ["box"], "same-name new UUID invalidates exact source binding")
        check(vertices(replaced) == vertices(fresh_parts(a)), "replacement follows fresh exact source")
        ctx.end_undo()
        # Unknown identity is a cache miss, never permission to serve a stale entry.
        with patch.object(bridge, "subobject_source_fingerprint", return_value=None):
            calls.clear()
            ui._brep_parts_all()
            ui._brep_parts_all()
            check(calls == ["box", "box"], "unavailable fingerprint always re-evaluates exact source")
        # Callback/native reentrancy cannot publish parts for an earlier value.
        extractor = bridge.subobject_parts
        changed_during = []
        def change_during(geometry):
            result = extractor(geometry)
            if kernel.master_of(geometry) is None and not changed_during:
                changed_during.append(True)
                value = kernel.recipe_of(geometry)
                value["dz"] = 8
                kernel.record_recipe(geometry, value)
            return result
        ctx.begin_undo("source changes during extraction")
        ctx.note_change()
        value = kernel.recipe_of(obj(a).Geometry)
        value["dz"] = 6
        kernel.record_recipe(obj(a).Geometry, value)
        calls.clear()
        with patch.object(bridge, "subobject_parts", change_during):
            stable = ui._brep_parts_all()[a]
        check(calls == ["box", "box"] and max(p[2] for _,p in stable["vertices"]) == 8,
              "source change during extraction retries from its new exact value")
        ctx.end_undo()
        def never_stable(geometry):
            result = extractor(geometry)
            if kernel.master_of(geometry) is None:
                value = kernel.recipe_of(geometry)
                value["dz"] += 1
                kernel.record_recipe(geometry, value)
            return result
        ctx.begin_undo("unstable source")
        ctx.note_change()
        value = kernel.recipe_of(obj(a).Geometry)
        value["dz"] += 1
        kernel.record_recipe(obj(a).Geometry, value)
        calls.clear()
        with patch.object(bridge, "subobject_parts", never_stable):
            unstable = ui._brep_parts_all()
        check(a not in unstable and calls == ["box", "box"],
              "persistently unstable source is omitted after bounded retries, never stale")
        check(vertices(ui._brep_parts_all()[a]) == vertices(fresh_parts(a)), "stable source recovers after failed certification")
        ctx.end_undo()
        # Extraction failure cannot return the old successful entry.
        ctx.begin_undo("extraction failure")
        ctx.note_change()
        value = kernel.recipe_of(obj(a).Geometry)
        value["dz"] += 1
        kernel.record_recipe(obj(a).Geometry, value)
        with patch.object(bridge, "subobject_parts", side_effect=RuntimeError("injected extraction failure")):
            check(a not in ui._brep_parts_all(), "failed extraction never returns old cached geometry")
        check(vertices(ui._brep_parts_all()[a]) == vertices(fresh_parts(a)), "extraction recovery computes fresh exact parts")
        ctx.end_undo()
        # A failed source does not poison a successful cache entry for later calls.
        ui._brep_parts_all()
        ctx.begin_undo("invalid recipe")
        ctx.note_change()
        kernel.record_recipe(obj(a).Geometry, {"kind":"not-a-producer"})
        calls.clear()
        invalid = ui._brep_parts_all()
        check("not-a-producer" in calls, "invalid recipe is not hidden by a cached successful result")
        ctx.end_undo()
        ctx.undo_last()
        check(vertices(ui._brep_parts_all()[a]) == vertices(fresh_parts(a)), "Undo failed recipe recovers exact picking")
    # A different backing document cannot reuse stale same-name/UUID geometry.
    replacement = r3.File3dm.Decode(ctx.model.file.Encode())
    ctx.model.file = replacement
    geometry = obj(a).Geometry
    recipe = kernel.recipe_of(geometry)
    recipe["dz"] = 19
    kernel.record_recipe(geometry, recipe)
    check(max(p[2] for _,p in ui._brep_parts_all()[a]["vertices"]) == 19,
          "backing-file replacement with same UUID follows current source values")
    # Removing a source prunes the memo; it never grows into a historical store.
    ctx.model.remove_object(obj(a).Attributes.Id)
    check(a not in ui._brep_parts_all() and a not in ui._brep_parts_cache["parts"],
          "deleted source is pruned from the cache")
    # Exercise the profiled make_prism validation path as well as cheap primitives.
    # This is the same exact cylindrical solid with its valid equivalent recipe tree.
    cylinder = bridge.make_cylinder((0,0,0), 1, 2)
    profile = bridge.curve_recipe(bridge.make_circle((0,0,0), 1))
    kernel.record_recipe(cylinder, {"kind":"prism", "a":{"kind":"planar_face", "loops":[[profile]]},
                                   "dir":[0,0,2], "solid":True})
    _, prism = ctx.add_geometry(cylinder, "Prism", 0)
    ctx.undo.clear()
    check(kernel.master_of(obj(prism).Geometry) is None, "prism source remains recipe-only")
    prism_calls = []
    make_prism = occt.make_prism
    def counted_prism(*args, **kwargs):
        prism_calls.append(True)
        return make_prism(*args, **kwargs)
    with patch.object(occt, "make_prism", counted_prism):
        start = time.perf_counter()
        prism_parts = ui._brep_parts_all()[prism]
        cold_ms = (time.perf_counter()-start)*1000
        check(len(prism_calls) == 1 and len(prism_parts["faces"]) == 3,
              f"cold prism validating SDK operation: calls={len(prism_calls)}, faces={len(prism_parts['faces'])}")
        prism_calls.clear()
        ctx.begin_undo("unrelated to prism")
        ctx.add_geometry(bridge.make_line((30,0,0), (31,0,0)), "Line", 0)
        ctx.end_undo()
        start = time.perf_counter()
        reused = ui._brep_parts_all()[prism]
        reuse_ms = (time.perf_counter()-start)*1000
        check(not prism_calls and vertices(reused) == vertices(prism_parts),
              "unrelated edit never replays validating make_prism")
        for direction in (ctx.undo_last, ctx.redo_last):
            direction()
            ui._brep_parts_all()
            check(not prism_calls, "unrelated Undo/Redo never replays make_prism")
        print(f"prism cold {cold_ms:.3f} ms / unchanged-source reuse {reuse_ms:.3f} ms; make_prism reuse calls 0", flush=True)
    shape = bridge._occt_operand(obj(prism).Geometry)
    check(occt.is_airtight(shape) and math.isclose(occt.volume(shape), 2*math.pi, rel_tol=1e-10),
          "picker caching never substitutes faceted manufacturing geometry")
    print(f"BREP PICKER SOURCE CACHE PASSED ({checks} checks)", flush=True)


if __name__ == "__main__":
    main()
