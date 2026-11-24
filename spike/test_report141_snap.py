"""Report 141: exact Near survives coarse chords on detailed imported curves.

Actual Blender/SDK/viewport integration requires Python; all curve search remains
in the existing Rust core. Public inputs are generated, never owner geometry.
Run with Blender -b --factory-startup --python this_file.
"""
import math
import os
import sys
import bpy
import rhino3dm as r3
from mathutils import Matrix

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from narwhal3d import bridge, commands, gumball, osnap, ui
from narwhal3d.model import NarwhalModel
from narwhal3d.state import app_state

class Region:
    width, height, x, y = 1200, 800, 0, 0

class View:
    perspective_matrix = Matrix(((0.1, 0, 0, 0), (0, 0.15, 0, 0),
                                 (0, 0, -0.001, 0), (0, 0, 0, 1)))

class Space:
    region_3d = View()

checks = 0

def verify(ok, label):
    global checks
    checks += 1
    assert ok, label


def geometry():
    return next(o.Geometry for o in app_state.model.file.Objects
                if o.Attributes.Name == name)


def sweep(label):
    g = geometry()
    for i in range(1, 40):
        p = g.PointAt(g.Domain.T0 + (g.Domain.T1 - g.Domain.T0) * i / 40)
        q = (p.X, p.Y, p.Z)
        snap = ui.snap_at(None, Space(), Region(), q)
        verify(snap is not None and math.dist(snap.point, q) * 60 < 10,
               f'{label}: exact curve cursor {i}: {snap!r} at {q}')


try:
    app_state.set_model(NarwhalModel())
    app_state.active_command = None
    ctx = app_state.ctx
    # Many short cubic spans, like imported artwork, undersampled by 64 chords.
    points = [r3.Point3d(-8 + i / 24, 2 * math.sin(i * 0.7), 0)
              for i in range(337)]
    curve = r3.NurbsCurve.Create(False, 3, points)
    ctx.begin_undo('detailed curve')
    ctx.note_change('detailed curve')
    _id, name = ctx.add_geometry(curve, 'Curve')
    ctx.end_undo()
    for ob in bpy.data.objects:
        ob.select_set(ob.name == name)
    ui._snap_settings.enabled = {osnap.NEAR}
    ui._snap_settings.disabled = False
    ui._snap_settings.snap_to_locked = False
    sweep('original')
    for modes in ({osnap.NEAR}, {osnap.END, osnap.NEAR, osnap.POINT, osnap.MID, osnap.CEN}):
        ui._snap_settings.enabled = modes
        sweep('enabled modes')
    ui._snap_settings.enabled = {osnap.NEAR}
    commands.apply_transform(ctx, bridge.translation_xform((0, 3, 0)))
    sweep('Move')
    ob = bpy.data.objects[name]
    ctx.begin_undo('Gumball Move')
    ui._gumball_drag = {'kind': gumball.MOVE, 'mode': gumball.MOVE, 'axis': 0,
                        'total': 1.0, 'defer': {'start': {name: ob.matrix_world.copy()},
                        'acc': Matrix.Translation((1, 0, 0))}}
    ui._gumball_end()
    sweep('Gumball')
    ctx.undo_last()
    sweep('Undo')
    ctx.redo_last()
    sweep('Redo')
    for ob in bpy.data.objects:
        ob.select_set(ob.name == name)
    app_state.feed_line('Lock')
    verify(next(o.Attributes.Mode for o in app_state.model.file.Objects
                if o.Attributes.Name == name) == r3.ObjectMode.Locked,
           'Lock updates document mode')
    app_state.feed_line('Unlock')
    ui._snap_settings.snap_to_locked = False
    sweep('Unlock')
    print(f'REPORT141 PASSED ({checks} checks)')
except Exception:
    import traceback
    traceback.print_exc()
    print(f'REPORT141 FAILED after {checks} checks')
    sys.exit(1)
