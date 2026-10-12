#!/usr/bin/env python3
"""Drive the real Narwhal UI while the README demo is screen-recorded.

The companion shell script records the application window with the system pointer. This
script creates two exact circles, moves the real cursor through the expanded interface,
types Loft through Blender's event queue, clicks both curves, and leaves the resulting
surface visible before closing.
"""
from __future__ import annotations

import os
from pathlib import Path

import bpy
from bpy_extras import view3d_utils
from mathutils import Vector

from narwhal3d import commands, ui
from narwhal3d.state import app_state

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "readme-demo"
READY = ARTIFACTS / "ready"
RECORDING = ARTIFACTS / "recording"
CAPTURE = ARTIFACTS / "capture"
DONE = ARTIFACTS / "done"
POLL_SECONDS = 0.1
MAX_POLLS = 300


def _layout_ready():
    screen = getattr(bpy.context, "screen", None)
    return (
        screen is not None
        and len([area for area in screen.areas if area.type == "VIEW_3D"]) == 4
        and not getattr(ui, "_viewports_in_progress", False)
        and ui.left_band_area() is not None
        and ui.right_band_area() is not None
        and len(getattr(ui, "_sidebar_cells", ())) == 37
    )


def _redraw():
    screen = getattr(bpy.context, "screen", None)
    if screen is not None:
        for area in screen.areas:
            area.tag_redraw()
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)


def _frame_all():
    zoom = commands.ZoomCmd()
    zoom._all = True
    zoom._zoom(selected=False)


def _window():
    return bpy.context.window_manager.windows[0]


def _move(point):
    x, y = (int(point[0]), int(point[1]))
    win = _window()
    win.cursor_warp(x, y)
    win.event_simulate(type="MOUSEMOVE", value="NOTHING", x=x, y=y)


def _click(point):
    x, y = (int(point[0]), int(point[1]))
    win = _window()
    win.event_simulate(type="LEFTMOUSE", value="PRESS", x=x, y=y)
    win.event_simulate(type="LEFTMOUSE", value="RELEASE", x=x, y=y)


def _key(character):
    win = _window()
    key = character.upper()
    win.event_simulate(type=key, value="PRESS", unicode=character)
    win.event_simulate(type=key, value="RELEASE")


def _enter():
    win = _window()
    win.event_simulate(type="RET", value="PRESS")
    win.event_simulate(type="RET", value="RELEASE")


def _info_region():
    area = next(area for area in bpy.context.screen.areas if area.type == "INFO")
    region = next(region for region in area.regions if region.type == "WINDOW")
    return region


def _toolbar_tab_point(name):
    region = _info_region()
    box = next(box for box in ui._toolbar_tab_boxes if box[0] == name)
    return (region.x + (box[1] + box[2]) / 2,
            region.y + ui._toolbar_tab_row_top - 9)


def _world_point(world):
    area = next(area for area in bpy.context.screen.areas
                if area.type == "VIEW_3D" and ui.pane_label(area) == "Perspective")
    region = next(region for region in area.regions
                  if region.type == "WINDOW" and region.alignment == "NONE")
    space = next(space for space in area.spaces if space.type == "VIEW_3D")
    local = view3d_utils.location_3d_to_region_2d(region, space.region_3d, Vector(world))
    if local is None:
        raise RuntimeError(f"could not project demo point {world!r}")
    return (region.x + local.x, region.y + local.y)


def _line(start, end, frames=12):
    return [
        lambda t=i / frames: _move((start[0] + (end[0] - start[0]) * t,
                                    start[1] + (end[1] - start[1]) * t))
        for i in range(1, frames + 1)
    ]


def _pause(frames):
    return [lambda: None for _index in range(frames)]


def _prepare():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    for marker in (READY, RECORDING, CAPTURE, DONE):
        marker.unlink(missing_ok=True)
    for line in (
        "Circle", "w0,0,0", "w12,0,0",
        "Circle", "w0,0,24", "w7,0,24",
    ):
        app_state.feed_line(line)
    _frame_all()
    _redraw()
    start = (50.0, float(bpy.context.window.height - 120))
    _move(start)
    READY.write_text("ready\n", encoding="utf-8")
    return start


def _ensure_command_started():
    if app_state.active_command is None:
        app_state.input_buffer = "Loft"
        app_state.feed_line("Loft")
        ui.redraw_cmdbar()


def _ensure_curve(name, expected_step):
    command = app_state.active_command
    if command is None or command.name != "Loft":
        return
    if command.step_index == expected_step:
        app_state.ctx.select_by_names(["Circle1"] if expected_step == 0
                                      else ["Circle1", "Circle2"])
        app_state.feed_line(name)
        ui.redraw_cmdbar()


def _finish():
    _redraw()
    DONE.write_text("done\n", encoding="utf-8")
    print("README DEMO CAPTURE DONE")
    bpy.ops.wm.quit_blender()


def _actions(start):
    tab = _toolbar_tab_point("Curve Tools")
    command_line = (360.0, float(_info_region().y + 24))
    first = _world_point((12.0, 0.0, 0.0))
    second = _world_point((7.0, 0.0, 24.0))
    away = (first[0] + 180.0, first[1] - 120.0)

    actions = []
    actions += _pause(8)
    actions += _line(start, tab, 14)
    actions += [lambda: _click(tab), lambda: ui.set_toolbar_tab("Curve Tools")]
    actions += _pause(8)
    actions += _line(tab, command_line, 12)
    actions += [lambda ch=ch: _key(ch) for ch in "Loft"]
    actions += _pause(4)
    actions += [_enter, _ensure_command_started]
    actions += _pause(8)
    actions += _line(command_line, first, 18)
    actions += [lambda: _click(first)]
    actions += _pause(4)
    actions += [lambda: _ensure_curve("Circle1", 0)]
    actions += _pause(8)
    actions += _line(first, second, 18)
    actions += [lambda: _click(second)]
    actions += _pause(4)
    actions += [lambda: _ensure_curve("Circle2", 1)]
    actions += _pause(24)
    actions += _line(second, away, 12)
    actions += _pause(18)
    actions += [_finish]
    return actions


def start():
    state = {"polls": 0, "actions": None, "index": 0,
             "capture_started": False, "start": None}

    def tick():
        if state["actions"] is None:
            if not _layout_ready():
                state["polls"] += 1
                if state["polls"] >= MAX_POLLS:
                    raise RuntimeError("Narwhal 3D layout did not become ready")
                return POLL_SECONDS
            start_point = _prepare()
            state["start"] = start_point
            state["actions"] = _actions(start_point)
            return POLL_SECONDS
        if not RECORDING.exists():
            return POLL_SECONDS
        if not state["capture_started"]:
            _move(state["start"])
            _redraw()
            CAPTURE.write_text("capture\n", encoding="utf-8")
            state["capture_started"] = True
            return 0.5
        action = state["actions"][state["index"]]
        state["index"] += 1
        action()
        return 0.12 if state["index"] < len(state["actions"]) else None

    bpy.app.timers.register(tick, first_interval=POLL_SECONDS)


if __name__ == "__main__":
    start()
