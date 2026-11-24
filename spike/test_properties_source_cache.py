"""Actual Blender/SDK Properties: source-certified rows, never global Undo geometry.

Python orchestrates actual host objects and existing exact SDK operations; there is
no new numeric geometry engine or approximation in this regression.
"""
from pathlib import Path
import math
import sys
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType('narwhal3d')
pkg.__path__ = [str(ROOT / 'narwhal3d')]
sys.modules['narwhal3d'] = pkg
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

def main():
    bpy.data.batch_remove(list(bpy.data.objects))
    app_state.set_model(NarwhalModel())
    ctx = app_state.ctx
    geometry = bridge.make_cylinder((0,0,0), 1, 2)
    profile = bridge.curve_recipe(bridge.make_circle((0,0,0), 1))
    kernel.record_recipe(geometry, {'kind':'prism', 'a':{'kind':'planar_face', 'loops':[[profile]]},
                                    'dir':[0,0,2], 'solid':True})
    _, a = ctx.add_geometry(geometry, 'Prism', 0)
    master_geom = bridge.make_cylinder((10,0,0), 1, 3)
    kernel.register_master(master_geom, bridge._occt_operand(master_geom))
    _, b = ctx.add_geometry(master_geom, 'Master', 0)
    ctx.undo.clear()
    def select(names):
        ctx.select_by_names(names)
        check(set(ui.selection_names()) == set(names), 'actual host selection matches request')
    select([a])
    calls = []
    make_prism = occt.make_prism
    def counted(*args, **kwargs):
        calls.append(True)
        return make_prism(*args, **kwargs)
    with patch.object(occt, 'make_prism', counted):
        rows = dict(ui.properties_rows())
        check(len(calls) == 1, f'cold Properties rebuilds prism ONCE, not twice (calls={len(calls)})')
        check(rows['Faces'] == '3' and rows['Airtight'] == 'yes' and rows['Face types'] == 'PLANE 2, CYLINDER 1',
              'Properties describes three exact analytic airtight faces')
        calls.clear()
        for _ in range(3):
            check(dict(ui.properties_rows())['Faces'] == '3' and not calls, 'same-selection redraw does not replay')
        select([])
        check(ui.properties_rows() == [('', 'No object selected')], 'deselection is reflected')
        select([a])
        ui.properties_rows()
        check(not calls, 'deselect/reselect reuses certified geometry')
        ctx.begin_undo('unrelated curve')
        ctx.add_geometry(bridge.make_line((30,0,0), (31,1,0)), 'Line', 0)
        ctx.end_undo()
        for action in (lambda: None, ctx.undo_last, ctx.redo_last):
            action(); select([a]); ui.properties_rows()
            check(not calls, 'unrelated edit/Undo/Redo never replays selected solid')
        # All selected objects use their own memo rather than a selection-wide stamp.
        select([a,b]); ui.properties_rows(); select([a]); ui.properties_rows()
        check(not calls, 'multi-selection and selection changes retain per-object geometry')
        ui.properties_details()
        check(len(calls) == 1, 'first explicit measurement reconstructs once')
        volume = float(dict(ui.properties_rows())['Volume'])
        check(math.isclose(volume, 2*math.pi, rel_tol=1e-5), 'Details volume is exact analytic cylinder volume')
        calls.clear()
        ui.properties_details(); ui.properties_rows()
        check(not calls, 'repeated Details reuses measured exact source')
        ctx.begin_undo('another unrelated edit')
        ctx.add_geometry(bridge.make_line((40,0,0), (41,1,0)), 'OtherLine', 0)
        ctx.end_undo()
        select([a]); ui.properties_rows(); ui.properties_details()
        check(not calls, 'unrelated edit does not erase or replay exact measurements')
        # Metadata is live independently of geometry and Undo serial.
        layer = r3.Layer(); layer.Name = 'PropertiesLayer'; layer.Color = (12,34,56,255)
        layer_index = ctx.model.file.Layers.Add(layer)
        attrs = obj(a).Attributes
        attrs.LayerIndex = layer_index
        attrs.ColorSource = r3.ObjectColorSource.ColorFromLayer
        rows = dict(ui.properties_rows())
        check(rows['Layer'] == 'PropertiesLayer' and not calls, 'object layer edit is live without topology replay')
        ctx.model.file.Layers[layer_index].Name = 'RenamedLayer'
        rows = dict(ui.properties_rows())
        check(rows['Layer'] == 'RenamedLayer' and not calls, 'layer rename is live before Undo closure')
        # Edit the authoritative recipe before the Undo serial changes.
        ctx.begin_undo('source change'); ctx.note_change()
        recipe = kernel.recipe_of(obj(a).Geometry); recipe['dir'] = [0,0,5]
        kernel.record_recipe(obj(a).Geometry, recipe)
        ui.properties_rows()
        check(len(calls) == 1, 'in-place recipe edit invalidates immediately')
        check('Volume' not in dict(ui.properties_rows()), 'old Details measurements are not shown for edited source')
        calls.clear(); ui.properties_details()
        check(len(calls) == 1 and math.isclose(float(dict(ui.properties_rows())['Volume']), 5*math.pi, rel_tol=1e-5),
              'new Details measures changed exact source')
        ctx.end_undo()
        for action in (ctx.undo_last, ctx.redo_last):
            calls.clear(); action(); select([a]); ui.properties_rows()
            check(len(calls) == 1, f'source Undo/Redo invalidates the correct object: calls={len(calls)}, source={dict(ui.properties_rows()).get("Source")}, recipe={kernel.recipe_of(obj(a).Geometry)}')
        # Supported transform changes authoritative recipe, not merely proxy display.
        calls.clear()
        geom = obj(a).Geometry
        moved = bridge.transform_geometry(geom, r3.Transform.Translation(7,0,0))
        if moved is not geom:
            ctx.model.replace_geometry(obj(a).Attributes.Id, moved)
        ui.properties_rows()
        check(len(calls) == 1, 'supported in-place transform invalidates source rows')
        ui.properties_details()
        check(dict(ui.properties_rows())['Bounding box'] == dict(bridge.object_details(obj(a).Geometry))['Bounding box'] and dict(ui.properties_rows())['Bounding box'].startswith('(6,'), 'Details follows translated analytic source')
        # Native master key stays unchanged while geometry changes in place.
        select([b]); ui.properties_rows(); ui.properties_details()
        from OCP.gp import gp_Trsf, gp_Vec
        from OCP.TopLoc import TopLoc_Location
        geom = obj(b).Geometry; key = kernel.master_key_of(geom)
        transform = gp_Trsf(); transform.SetTranslation(gp_Vec(0,4,0))
        kernel.master_of(geom).Move(TopLoc_Location(transform))
        check(kernel.master_key_of(geom) == key, 'native mutation deliberately keeps binding key')
        check('Volume' not in dict(ui.properties_rows()), 'same-key master mutation invalidates old measurements')
        ui.properties_details()
        expected = dict(bridge.object_details(obj(b).Geometry))
        check(dict(ui.properties_rows())['Bounding box'] == expected['Bounding box'], 'master Details matches fresh native truth')
        # Representation wording must not hide behind unchanged topology.
        select([a]); ui.properties_rows()
        kernel.tag(obj(a).Geometry, kernel.MESH_DERIVED)
        check('faceted' in dict(ui.properties_rows())['Representation'], 'representation metadata stays honest')
        kernel.tag(obj(a).Geometry, kernel.ANALYTIC)
        ui.properties_rows()
        # No fingerprint or failed extraction may expose earlier cached success.
        calls.clear()
        with patch.object(bridge, 'object_details_fingerprint', return_value=None):
            ui.properties_rows(); ui.properties_rows()
        check(len(calls) == 2, 'unavailable fingerprint never becomes permission to reuse')
        recipe = kernel.recipe_of(obj(a).Geometry); recipe['dir'] = [0,0,9]
        kernel.record_recipe(obj(a).Geometry, recipe)
        with patch.object(bridge, 'object_details', side_effect=RuntimeError('injected inspection failure')):
            check('injected inspection failure' in dict(ui.properties_rows())['Details'], 'failure reports unavailable, not old faces')
        check(dict(ui.properties_rows())['Faces'] == '3', 'source inspection recovers after failure')
        original = bridge.object_details
        changed = []
        def during(geom, *args, **kwargs):
            rows = original(geom, *args, **kwargs)
            if not changed:
                changed.append(True)
                value = kernel.recipe_of(geom); value['dir'] = [0,0,11]; kernel.record_recipe(geom,value)
            return rows
        recipe = kernel.recipe_of(obj(a).Geometry); recipe['dir'] = [0,0,10]
        kernel.record_recipe(obj(a).Geometry, recipe)
        calls.clear()
        with patch.object(bridge, 'object_details', during):
            ui.properties_rows()
        check(len(calls) == 2, 'callback source change triggers bounded exact reinspection')
        def unstable(geom, *args, **kwargs):
            rows = original(geom, *args, **kwargs)
            value = kernel.recipe_of(geom); value['dir'][2] += 1
            kernel.record_recipe(geom, value)
            return rows
        value = kernel.recipe_of(obj(a).Geometry); value['dir'] = [0,0,12]
        kernel.record_recipe(obj(a).Geometry, value)
        calls.clear()
        with patch.object(bridge, 'object_details', unstable):
            failed = dict(ui.properties_rows())
        check(len(calls) == 2 and 'geometry changed' in failed['Details'] and 'Faces' not in failed,
              'persistently changing inspection is bounded and never publishes stale topology')
        check(dict(ui.properties_rows())['Faces'] == '3', 'stable inspection recovers after bounded refusal')
        # Measurement failure is visible, without throwing away unchanged topology.
        inspector = bridge.object_details
        def measurement_failure(geom, measure=True):
            if measure:
                raise RuntimeError('injected measurement failure')
            return inspector(geom, measure=False)
        with patch.object(bridge, 'object_details', measurement_failure):
            ui.properties_details()
        calls.clear()
        check('injected measurement failure' in dict(ui.properties_rows())['Details'] and not calls,
              'failed Details remains visible without replaying certified cheap geometry')
        ui.properties_details()
        check(math.isclose(float(dict(ui.properties_rows())['Volume']), kernel.recipe_of(obj(a).Geometry)['dir'][2]*math.pi, rel_tol=1e-5),
              'measurement recovery still computes exact volume')
        # Backing-file replacement with same UUID/name cannot retain document rows.
        replacement = r3.File3dm.Decode(ctx.model.file.Encode())
        ctx.model.file = replacement
        recipe = kernel.recipe_of(obj(a).Geometry); recipe['dir'] = [0,0,13]
        kernel.record_recipe(obj(a).Geometry, recipe)
        calls.clear(); ui.properties_rows()
        check(len(calls) == 1, 'document replacement re-evaluates even matching object identity')
        ui.properties_details()
        check(math.isclose(float(dict(ui.properties_rows())['Volume']), 13*math.pi, rel_tol=1e-5),
              'replacement document Details uses current exact geometry')
        identity = str(obj(a).Attributes.Id)
        ctx.model.remove_object(identity); select([]); ui.properties_rows()
        check(identity not in ui._props_geometry_cache['entries'], 'deleted source is pruned')
        # Also exercise a real SDK Extrusion, whose Path/Profile rows are document-side.
        extrusion = r3.Extrusion.Create(bridge.make_circle((0,0,0), 1), 2, True)
        kernel.record_recipe(extrusion, {'kind':'prism', 'a':{'kind':'planar_face', 'loops':[[profile]]},
                                          'dir':[0,0,2], 'solid':True})
        kernel.tag(extrusion, kernel.ANALYTIC)
        _, e = ctx.add_geometry(extrusion, 'SDKExtrusion', 0)
        select([e]); calls.clear()
        rows = dict(ui.properties_rows())
        check(rows['Type'] == 'extrusion' and rows['Faces'] == '3' and len(calls) == 1,
              'real SDK extrusion retains exact topology and extrusion-specific rows')
        path = rows['Path']; calls.clear(); ui.properties_rows()
        check(not calls, 'real extrusion warm redraw does not replay')
        geom = obj(e).Geometry
        moved = bridge.transform_geometry(geom, r3.Transform.Translation(3,4,0))
        if moved is not geom:
            ctx.model.replace_geometry(obj(e).Attributes.Id, moved)
        rows = dict(ui.properties_rows())
        check(rows['Path'] != path and len(calls) == 1, 'extrusion path rows follow the supported transform')
        ui.properties_details()
        check(math.isclose(float(dict(ui.properties_rows())['Volume']), 2*math.pi, rel_tol=1e-5),
              'transformed SDK extrusion measurements stay exact')
    print(f'PROPERTIES SOURCE CACHE PASSED ({checks} checks)', flush=True)

if __name__ == '__main__':
    main()
