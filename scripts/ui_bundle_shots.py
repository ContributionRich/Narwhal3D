"""Capture the live Narwhal 3D shell for the UI bundle: screenshots + measured layout.

Run in a real window, after the bootstrap, with simulated events enabled:

    NARWHAL_BUNDLE_SHOTS=/path/to/dir \\
    tools/blender-patched/blender --enable-event-simulate --factory-startup \\
        --python launch/bootstrap.py --python scripts/ui_bundle_shots.py

Writes numbered PNGs and ``layout.json`` (every area, hit box and metric the shell
actually drew) into ``$NARWHAL_BUNDLE_SHOTS``, prints ``UI BUNDLE SHOTS DONE`` and quits.
The theme follows ``NARWHAL3D_THEME`` like the app itself.
"""
import json
import os
import sys

import bpy

OUT = os.environ.get("NARWHAL_BUNDLE_SHOTS") or os.path.join(os.getcwd(), "ui_bundle_shots")
os.makedirs(OUT, exist_ok=True)
STATE = {"t": 0, "step": 0, "shots": []}


def _win():
    return bpy.context.window_manager.windows[0]


def _ev(kind, value="PRESS", x=0, y=0, **kw):
    _win().event_simulate(type=kind, value=value, x=int(x), y=int(y), **kw)


def _click(x, y):
    _ev("MOUSEMOVE", "NOTHING", x, y)
    _ev("LEFTMOUSE", "PRESS", x, y)
    _ev("LEFTMOUSE", "RELEASE", x, y)


def _esc():
    _ev("ESC", "PRESS")
    _ev("ESC", "RELEASE")


def _shot(name, caption):
    path = os.path.join(OUT, name)
    with bpy.context.temp_override(window=_win()):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
        bpy.ops.screen.screenshot(filepath=path)
    STATE["shots"].append({"file": name, "caption": caption})


def _rect(a):
    return [a.x, a.y, a.width, a.height]


def _layout():
    """Everything the shell drew, in WINDOW pixels (origin bottom-left, y up)."""
    from narwhal3d import ui, uiscale
    win = _win()
    areas = []
    for a in win.screen.areas:
        kind = a.type
        if a.type == "INFO":
            side = ui._band_side(a)
            kind = {"left": "LEFT_DOCK", "right": "RIGHT_DOCK"}.get(side, "COMMAND_AREA")
        elif a.type == "VIEW_3D":
            kind = f"VIEWPORT:{ui.pane_label(a)}"
        areas.append({"role": kind, "rect_xywh": _rect(a)})
    bar = ui._cmdbar_area()
    bar_region = next((r for r in bar.regions if r.type == "WINDOW"), None) if bar else None
    cats = []
    if bar_region is not None:
        top, h = ui._toolbar_tab_row_top, ui._toolbar_tab_row_h
        for name, x0, x1 in ui._toolbar_tab_boxes:
            cats.append({"name": name, "box_x0y0x1y1": [bar_region.x + x0, bar_region.y + top - h,
                                                        bar_region.x + x1, bar_region.y + top]})
    return {
        "window_px": [win.width, win.height],
        "interface_scale": uiscale.current(),
        "note": "window pixels, origin bottom-left, y grows UP (Blender convention)",
        "areas": areas,
        "category_buttons": cats,
        "left_header_box": list(ui._left_header_box or ()),
        "left_tool_cells": [list(c[1:]) for c in ui._sidebar_cells],
        "osnap_rows": [{"label": b[0], "box": list(b[1:])} for b in ui.get_osnap_panel_boxes()],
        "right_tab_boxes": [list(b) for b in ui._right_tab_boxes],
        "layer_search_box": list(ui._layer_search_box or ()),
        "layer_rows": [list(r) for r in ui._layer_rows],
        "statusbar_panes": [p.id for p in ui.sbar.panes()],
    }


def _caption_arrow():
    """Window position of the Perspective title's drop-down arrow, via the app's own hit test."""
    from narwhal3d import ui
    for area in _win().screen.areas:
        if area.type != "VIEW_3D" or ui.pane_label(area) != "Perspective":
            continue
        region = next(r for r in area.regions if r.type == "WINDOW")
        top = region.y + region.height
        for dy in range(2, 40):
            for x in range(region.x, region.x + 260):
                if ui.pane_title_arrow_at(x, top - dy):
                    return x + 4, top - dy - 4
    return None


def _step():
    from narwhal3d import ui, layer_commands
    from narwhal3d.state import app_state
    STATE["t"] += 1
    if os.environ.get("NARWHAL3D_UI_READY") != "1" and STATE["t"] < 80:
        return 0.25
    s = STATE["step"]
    STATE["step"] += 1
    try:
        if s == 0:
            app_state.log("Narwhal 3D ready. Type a command below, or click a tool.")
            ui.set_toolbar_tab("Standard")
            ui.redraw_cmdbar()
            return 0.5
        if s == 1:
            _shot("01_workspace.png", "The whole shell at startup (Standard category).")
            with open(os.path.join(OUT, "layout.json"), "w") as f:
                json.dump(_layout(), f, indent=1)
            ui.set_toolbar_tab("Transform")
            return 0.4
        if s == 2:
            _shot("02_category_transform.png",
                  "Clicking a category swaps the left panel's tools in place (Transform).")
            ui.set_toolbar_tab("Standard")
            b = ui._left_header_box
            _click((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
            return 0.6
        if s == 3:
            _shot("03_category_menu.png", "The left panel header is a category drop-down.")
            _esc()
            return 0.3
        if s == 4:
            _click(15, _win().height - 9)
            return 0.6
        if s == 5:
            _shot("04_menu_file.png", "Menu bar: File. Greyed rows are not implemented yet.")
            _esc()
            return 0.3
        if s == 6:
            pos = _caption_arrow()
            if pos:
                _click(*pos)
            return 0.6
        if s == 7:
            _shot("05_viewport_menu.png", "A viewport title's drop-down (views, display modes).")
            _esc()
            return 0.3
        if s == 8:
            for rgb in ((220, 60, 60), (40, 170, 70), (30, 110, 230), (240, 200, 40)):
                layer_commands.new_layer()
                ui.set_layer_color(list(app_state.layers.iter_all())[-1], rgb)
            layers = list(app_state.layers.iter_all())
            app_state.layers.set_visible(layers[2], False)
            app_state.layers.set_locked(layers[3], True)
            ui.set_right_tab(0)
            return 0.5
        if s == 9:
            _shot("06_layers.png", "Right inspector, Layers: search, +, ..., eye, lock, row menu.")
            for i, name in ((1, "07_properties.png"), (2, "08_display.png"),
                            (3, "09_bug_report.png"), (4, "10_ai_assistant.png")):
                ui.set_right_tab(i)
                _shot(name, f"Right inspector, {ui.rightpanel.TABS[i].name} tab.")
            ui.set_right_tab(0)
            cell = ui._sidebar_cells[4]
            x, y = (cell[1] + cell[3]) / 2, (cell[2] + cell[4]) / 2
            _ev("MOUSEMOVE", "NOTHING", x, y)
            ui.sidebar_hover_redraw_update(x, y)
            return 0.5
        if s == 10:
            _shot("11_tool_tooltip.png", "Hovering a left-panel tool shows its command name.")
            app_state.feed_line("Circle")
            ui.redraw_cmdbar()
            return 0.5
        if s == 11:
            _shot("12_command_prompt.png",
                  "A running command: the console shows its prompt and clickable options.")
            app_state.feed_line("")
            app_state.active_command = None
            ui.redraw_cmdbar()
            with open(os.path.join(OUT, "shots.json"), "w") as f:
                json.dump(STATE["shots"], f, indent=1)
            print("UI BUNDLE SHOTS DONE", flush=True)
            sys.stdout.flush()
            bpy.ops.wm.quit_blender()
            return None
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"[ui-bundle] step {s} failed: {exc!r}", flush=True)
        if STATE["t"] > 400:
            bpy.ops.wm.quit_blender()
            return None
    return 0.3


bpy.app.timers.register(_step, first_interval=1.0)
