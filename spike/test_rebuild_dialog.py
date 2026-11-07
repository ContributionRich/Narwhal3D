"""Curve Rebuild dialog: deferred selection, Preview/Cancel without mutation, one Undo on OK.

Blender integration only: fitting remains the existing rebuild implementation. Run through
scripts/run_suite.sh, which supplies the qualified native document/history libraries.
"""
import os
import sys
from types import SimpleNamespace

import bpy
import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from narwhal3d import commands, model, state, ui  # noqa: E402


class Context(commands.NarwhalContext):
    def __init__(self, doc):
        super().__init__(doc)
        self.selection = ["Input"]

    def selected_objects(self):
        return [SimpleNamespace(name=name) for name in self.selection]

    def object_by_name(self, name):
        return None

    def select_by_names(self, names):
        self.selection = list(names)

    def add_geometry(self, geometry, base_name, layer_index=None):
        self.note_change("Rebuild")
        name = base_name + "_rebuilt"
        oid = self.model.add_object(geometry, name, 0 if layer_index is None else layer_index)
        return oid, name

    def _restore_document(self, snapshot):
        self.model.restore(snapshot)


def curve_names(doc):
    return [obj.Attributes.Name for obj in doc.file.Objects]


original_state = ui.app_state
old_tag, old_redraw = ui._tag_redraw_viewports, ui.redraw_cmdbar
ui._tag_redraw_viewports = lambda: None
ui.redraw_cmdbar = lambda: None
registered = []
try:
    app = state.AppState()
    app.ctx = Context(app.model)
    app.model.add_object(r3.NurbsCurve.Create(False, 3, [r3.Point3d(i, i % 2, 0)
                                                       for i in range(6)]), "Input", 0)
    ui.app_state = app
    before = app.ctx.geom_by_name("Input").Encode()
    cmd = commands.RebuildCmd()
    cmd.args = [["Input"]]
    # Same deferred command hook used after graphical selection; no fitting or mutation yet.
    captured = []
    cmd._dialog_opener = lambda command, ctx: (captured.append(command) or None, True)
    assert cmd._recorded(app.ctx, lambda: cmd.execute(app.ctx)) == (None, True)
    assert captured == [cmd] and app.ctx.geom_by_name("Input").Encode() == before
    assert len(app.ctx.undo.undo_names()) == 0, "opening the dialog adds no Undo entry"

    def request(command):
        ui._rebuild_window = dict(command=command, key=ui._doc_key(), preview=(),
                                 counts=[command._own_counts(app.ctx.geom_by_name("Input"))],
                                 closed=False, message="")

    for cls in (ui.NARWHAL_OT_rebuild_dialog, ui.NARWHAL_OT_rebuild_preview):
        bpy.utils.register_class(cls)
        registered.append(cls)
    request(cmd)
    operator = bpy.ops.narwhal.rebuild_dialog.get_rna_type()
    assert "point_count" in operator.properties and "degree" in operator.properties
    # Exercise the actual operator callbacks with a UI-like draft, without opening a window
    # in background Blender (invoke_props_dialog requires a graphical session).
    draft = SimpleNamespace(point_count=4, degree=3, delete_input=True,
                            current_layer=False, preserve_tangents=False,
                            report=lambda level, text: None)
    ui._rebuild_window["operator"] = draft
    preview_op = SimpleNamespace(report=lambda level, text: None)
    result = ui.NARWHAL_OT_rebuild_preview.execute(preview_op, None)
    assert result == {"FINISHED"} and ui._rebuild_window["preview"]
    assert "Maximum deviation:" in ui._rebuild_window["message"]
    assert app.ctx.geom_by_name("Input").Encode() == before and len(app.ctx.undo.undo_names()) == 0
    ui.NARWHAL_OT_rebuild_dialog.cancel(draft, None)
    assert ui._rebuild_window is None and app.ctx.geom_by_name("Input").Encode() == before
    assert len(app.ctx.undo.undo_names()) == 0, "Cancel leaves document and Undo unchanged"

    request(cmd)
    assert ui.NARWHAL_OT_rebuild_dialog.execute(draft, None) == {"FINISHED"}
    assert len(app.ctx.undo.undo_names()) == 1 and "Input" not in curve_names(app.model)
    assert app.ctx.undo_last() == "Rebuild" and app.ctx.geom_by_name("Input").Encode() == before
    assert app.ctx.redo_last() == "Rebuild" and "Input" not in curve_names(app.model)

    # Source changes while the window is open must fail closed, not rebuild stale inputs.
    cmd2 = commands.RebuildCmd()
    cmd2.args = [[curve_names(app.model)[0]]]
    ui._rebuild_window = dict(command=cmd2, key=ui._doc_key(), preview=())
    app.model.add_object(r3.Point(r3.Point3d(20, 0, 0)), "Other", 0)
    assert ui.NARWHAL_OT_rebuild_dialog.execute(draft, None) == {"CANCELLED"}
    assert len(app.ctx.undo.undo_names()) == 1

    draft.point_count, draft.degree = 3, 3
    ui._rebuild_counts_changed(draft, None, "PointCount")
    assert (draft.point_count, draft.degree) == (3, 2)
    draft.degree = 5
    ui._rebuild_counts_changed(draft, None, "Degree")
    assert (draft.point_count, draft.degree) == (6, 5)
    print("PASS: Rebuild dialog selection, Preview, Cancel, OK, Undo/Redo, stale-source guard and count coupling")
finally:
    ui._rebuild_window = None
    ui.app_state = original_state
    ui._tag_redraw_viewports, ui.redraw_cmdbar = old_tag, old_redraw
    for cls in reversed(registered):
        bpy.utils.unregister_class(cls)
