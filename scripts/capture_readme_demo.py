#!/usr/bin/env python3
"""Capture the README command-workflow frames from a real Narwhal 3D window.

Run through the bundled runtime after `launch/bootstrap.py`. The script creates two
exact circles, drives the production Loft command, and records each visible command
stage as a PNG. `scripts/build_readme_demo.sh` converts those frames into the GIF used
by the README.
"""
from __future__ import annotations

import os

import bpy

from narwhal3d import commands, ui
from narwhal3d.state import app_state

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRAME_DIR = os.path.join(ROOT, "artifacts", "readme-demo")
POLL_SECONDS = 0.25
MAX_POLLS = 120


def _layout_ready():
    screen = getattr(bpy.context, "screen", None)
    return (
        screen is not None
        and len([area for area in screen.areas if area.type == "VIEW_3D"]) == 4
        and not getattr(ui, "_viewports_in_progress", False)
        and ui.left_band_area() is not None
        and ui.right_band_area() is not None
    )


def _redraw():
    screen = getattr(bpy.context, "screen", None)
    if screen is not None:
        for area in screen.areas:
            area.tag_redraw()
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=3)


def _capture(number, name):
    _redraw()
    path = os.path.join(FRAME_DIR, f"{number:02d}-{name}.png")
    result = bpy.ops.screen.screenshot(filepath=path)
    if "FINISHED" not in result or not os.path.isfile(path):
        raise RuntimeError(f"could not capture {path}")
    print(f"README DEMO FRAME {path}")


def _frame_all():
    zoom = commands.ZoomCmd()
    zoom._all = True
    zoom._zoom(selected=False)


def _build_curves():
    for line in (
        "Circle", "w0,0,0", "w12,0,0",
        "Circle", "w0,0,24", "w7,0,24",
    ):
        app_state.feed_line(line)
    _frame_all()
    _capture(0, "two-curves")


def _type_loft():
    app_state.input_buffer = "Loft"
    ui.redraw_cmdbar()
    _capture(1, "type-loft")


def _start_loft():
    app_state.feed_line(app_state.input_buffer)
    _capture(2, "loft-first-prompt")


def _pick_first_curve():
    app_state.ctx.select_by_names(["Circle1"])
    app_state.feed_line("Circle1")
    _capture(3, "first-curve-picked")


def _pick_second_curve():
    app_state.ctx.select_by_names(["Circle1", "Circle2"])
    app_state.feed_line("Circle2")
    _frame_all()
    _capture(4, "loft-created")


def _hold_result():
    _capture(5, "loft-result-hold")
    print("README DEMO CAPTURE DONE")
    bpy.ops.wm.quit_blender()


def start():
    os.makedirs(FRAME_DIR, exist_ok=True)
    actions = (
        _build_curves,
        _type_loft,
        _start_loft,
        _pick_first_curve,
        _pick_second_curve,
        _hold_result,
    )
    state = {"polls": 0, "action": 0}

    def tick():
        if not _layout_ready():
            state["polls"] += 1
            if state["polls"] >= MAX_POLLS:
                raise RuntimeError("Narwhal 3D layout did not become ready")
            return POLL_SECONDS
        action = actions[state["action"]]
        state["action"] += 1
        action()
        return 1.0 if state["action"] < len(actions) else None

    bpy.app.timers.register(tick, first_interval=POLL_SECONDS)


if __name__ == "__main__":
    start()
