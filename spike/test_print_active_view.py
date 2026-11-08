"""Print starts from the active pane, not a previous dialog's remembered view.

Background Blender: real page generation, isolated preferences and scripted panes.
A selected curve in a small Top pane must not inherit an unsupported Perspective view.
"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from narwhal3d import print2d, printdialog, ui  # noqa: E402
from narwhal3d.model import NarwhalModel  # noqa: E402

panes = [SimpleNamespace(name="Top"), SimpleNamespace(name="Perspective")]
active = [panes[0]]
views = {
    "Top": print2d.View((1, 0, 0), (0, 1, 0), (0, 0, 0), 100, 75),
    "Perspective": print2d.View((1, 0, 0), (0, 1, 0), (0, 0, 0), 100, 75,
                                perspective=True),
}
settings = dict(view="Perspective", area="Extents", scale="Fit", selected_only=True,
                size="Letter", orientation="Landscape", width="0.13")
app = ui.app_state
app.set_model(NarwhalModel())
app.model.add_object(r3.Circle(r3.Point3d(0, 0, 0), 10).ToNurbsCurve(), "Circle", 0)
app.model.add_object(r3.Circle(r3.Point3d(200, 0, 0), 30).ToNurbsCurve(), "Other", 0)
original = [item.Geometry.Encode() for item in app.model.file.Objects]
undo = list(app.ctx.undo.undo_names())

with patch.object(ui, "_view3d_areas", return_value=panes), \
        patch.object(ui, "active_pane_area", side_effect=lambda: active[0]), \
        patch.object(ui, "pane_label", side_effect=lambda a: a.name), \
        patch.object(ui, "_pane_key", side_effect=lambda a: a.name), \
        patch.object(ui, "print_view_spec", side_effect=lambda a: views[a.name]), \
        patch.object(ui, "selected_display_names", return_value=["Circle"]), \
        patch.object(ui, "_tag_redraw_viewports"), \
        patch.object(ui, "_remember_preferences"), \
        patch.object(ui.preferences, "get", return_value=settings):
    try:
        assert ui.open_print_dialog()
        st = ui.print_dialog_state()
        assert st.view == "Top", "remembered Perspective must not override active Top"
        page = ui._print_dialog["page"]
        assert page is not None and len(page.strokes) == 1, st.message
        assert st.area == "Extents" and st.scale == "Fit" and st.selected_only
        assert st.size == "Letter" and st.orientation == "Landscape" and st.width == "0.13"
        sheet = printdialog.page_rect(printdialog.Rect(0, 0, 175, 136),
                                      page.width_mm, page.height_mm)
        points = [p for pts, _rgb, _width in printdialog.preview_lines(page, sheet) for p in pts]
        assert points and max(p[0] for p in points) - min(p[0] for p in points) > 100
        print("PASS: active Top produces a visible selected-curve preview and retains paper/scale settings")

        st.selected_only = False
        ui._print_dialog_apply("toggled:selected_only")
        assert len(ui._print_dialog["page"].strokes) == 2
        st.selected_only = True
        ui._print_dialog_apply("toggled:selected_only")
        assert len(ui._print_dialog["page"].strokes) == 1
        print("PASS: toggling Only selected objects immediately rebuilds the actual preview page")

        # View selection inside the current dialog remains explicit and effective.
        st.view = "Perspective"
        ui._print_dialog_refresh()
        assert ui._print_dialog["page"] is None and "PERSPECTIVE" in st.message
        st.view = "Top"
        ui._print_dialog_refresh()
        assert ui._print_dialog["page"] is not None
        ui.close_print_dialog()
        active[0] = panes[1]
        settings["view"] = "Top"
        assert ui.open_print_dialog() and ui.print_dialog_state().view == "Perspective"
        assert ui._print_dialog["page"] is None
        print("PASS: explicit dialog view changes work; an active Perspective pane is not silently replaced")
        assert [item.Geometry.Encode() for item in app.model.file.Objects] == original
        assert list(app.ctx.undo.undo_names()) == undo
        print("PASS: preview does not change geometry or Undo")
    finally:
        ui.close_print_dialog()
