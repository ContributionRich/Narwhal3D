#!/usr/bin/env python3
"""Acceptance for Trim's held-button drag-over curve gesture."""
from __future__ import annotations

from pathlib import Path
import sys
import types

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from narwhal3d import commands, ui  # noqa: E402

checks = 0
failures = []


def check(condition, label, detail=""):
    global checks
    checks += 1
    ok = bool(condition)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f" ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(label)


real_area_lookup = ui._area_by_pane_key
real_pane_key = ui._pane_key
real_curve_pick = ui.pick_curve_object
real_pick_location = ui.object_pick_location
real_feed = ui.app_state.feed_line
real_history = ui.app_state.history
real_command = ui.app_state.active_command
real_update_cursor = ui._update_pick_cursor
real_tag = ui._tag_redraw_viewports
real_trim_drag = ui._trim_drag
patched = {}


def patch(name, value):
    if name not in patched:
        patched[name] = getattr(ui, name)
    setattr(ui, name, value)


area = object()
space = types.SimpleNamespace(region_3d=object())
region = types.SimpleNamespace(x=100, y=50, width=800, height=600)
feeds = []
try:
    ui._area_by_pane_key = lambda _key: (area, space, region)
    ui._pane_key = lambda _area: "trim-pane"

    def curve_at(_area, _space, _region, rx, _ry):
        if 7.0 <= rx <= 13.0:
            return "CurveA"
        if 27.0 <= rx <= 33.0:
            return "CurveB"
        return None

    ui.pick_curve_object = curve_at
    ui.object_pick_location = (
        lambda _area, _space, _region, rx, _ry, _name: (float(rx), 0.0, 0.0))
    ui.app_state.history = []
    ui.app_state.feed_line = lambda text: feeds.append(text)
    trim = commands.TrimCmd()
    trim.step_index = 1
    ui.app_state.active_command = trim

    check(ui._trim_drag_command(trim), "only Trim's remove-parts step enables drag-over")
    trim.step_index = 0
    check(not ui._trim_drag_command(trim), "Trim's cutter-selection step does not enable drag-over")
    trim.step_index = 1
    check(not ui._trim_drag_command(commands.LineCmd()),
          "ordinary point commands do not inherit Trim drag behavior")

    ui._trim_drag_begin(area, space, region, 0.0, 0.0, latched=False)
    changed = ui._trim_drag_update(region.x + 40.0, region.y)
    check(changed and len(feeds) == 2,
          "one held movement trims two separated crossed curve parts", repr(feeds))
    check(float(feeds[0].split(",")[0].removeprefix("w")) < 15.0
          and float(feeds[1].split(",")[0].removeprefix("w")) > 25.0,
          "dense stroke samples preserve crossing order", repr(feeds))
    check(ui._trim_drag["trimmed"] == 2 and ui._trim_drag["active"],
          "drag state records only successful crossing entries")
    check(ui._trim_drag_end(region.x + 40.0, region.y) == 2 and ui._trim_drag is None,
          "release ends the gesture without replaying its final crossing")

    feeds.clear()
    ui._trim_drag_begin(area, space, region, 9.0, 0.0, latched=True)
    ui._trim_drag_update(region.x + 12.0, region.y)
    check(feeds == [], "the curve already handled by the initial click remains latched")
    ui._trim_drag_update(region.x + 20.0, region.y)
    ui._trim_drag_update(region.x + 30.0, region.y)
    check(len(feeds) == 1 and float(feeds[0].split(",")[0].removeprefix("w")) > 25.0,
          "empty space rearms the gesture for the next crossed part", repr(feeds))
    ui._trim_drag_end()

    feeds.clear()
    ui._trim_drag_begin(area, space, region, 5.0, 5.0, latched=False)
    check(ui._trim_drag_end(region.x + 5.0, region.y + 5.0) == 0 and feeds == [],
          "a simple click release adds no second Trim pick")

    # Drive the complete production press branch, not just the helpers. All chrome
    # hit tests are neutral so the viewport point route receives the event.
    # Title interception also runs during commands; the fake area has no Blender spaces.
    for name, value in (
            ("pane_title_arrow_at", lambda *_args: None),
            ("pane_title_at", lambda *_args: None),
            ("_click_command_bar", lambda *_args: None),
            ("osnap_panel_at", lambda *_args: None),
            ("sidebar_action_at", lambda *_args: None),
            ("right_tab_at", lambda *_args: None),
            ("bug_panel_click", lambda *_args, **_kwargs: None),
            ("display_panel_hit", lambda *_args: None),
            ("properties_details_button_at", lambda *_args: False),
            ("layer_new_button_at", lambda *_args: False),
            ("layer_row_at", lambda *_args: None),
            ("layer_rename_editing", lambda: False),
            ("_point_in_view3d", lambda *_args: True),
            ("_view3d_under_mouse", lambda *_args: (area, space, region)),
            ("set_active_pane", lambda *_args: False),
            ("pick_point", lambda *_args, **_kwargs: (10.0, 0.0, 0.0)),
            ("snap_at", lambda *_args, **_kwargs: None),
            ("apply_ortho", lambda _cmd, point, *_args: point),
            ("pick_consumes_typed", lambda *_args: False),
            ("constrain_pick", lambda _cmd, point: point),
            ("clear_active_snap", lambda: False)):
        patch(name, value)
    for name in ("_sidebar_flyout_state", "_print_dialog", "_gumball_drag",
                 "_gumball_relocate", "_cp_drag", "_sel_drag", "_pending_menu"):
        patch(name, None)
    ui._update_pick_cursor = lambda *_args: None
    redraws = []
    ui._tag_redraw_viewports = lambda: redraws.append("tag")
    operator = types.SimpleNamespace(
        _redraw=lambda: redraws.append("redraw"), _clear_transient=lambda: None)
    context = types.SimpleNamespace(window=None)
    press = types.SimpleNamespace(type="LEFTMOUSE", value="PRESS",
                                  mouse_x=region.x + 10.0, mouse_y=region.y,
                                  shift=False, ctrl=False, alt=False, ascii="")
    release = types.SimpleNamespace(type="LEFTMOUSE", value="RELEASE",
                                    mouse_x=region.x + 10.0, mouse_y=region.y,
                                    shift=False, ctrl=False, alt=False, ascii="")

    feeds.clear()
    ui.app_state.active_command = trim
    pressed = ui.NARWHAL_OT_command_input.modal(operator, context, press)
    released = ui.NARWHAL_OT_command_input.modal(operator, context, release)
    check(pressed == {"RUNNING_MODAL"} and released == {"RUNNING_MODAL"}
          and len(feeds) == 1 and ui._trim_drag is None,
          "the production point press arms Trim and its click release is not replayed")

    feeds.clear()
    ui.pick_curve_object = lambda *_args: None  # surface/empty: no curve latch
    ui.app_state.active_command = trim
    ui.NARWHAL_OT_command_input.modal(operator, context, press)
    ui.NARWHAL_OT_command_input.modal(operator, context, release)
    check(len(feeds) == 1 and ui._trim_drag is None,
          "a surface-style Trim click keeps the original single-pick behavior")
    ui.pick_curve_object = curve_at

    feeds.clear()
    line = commands.LineCmd()
    ui.app_state.active_command = line
    ui.NARWHAL_OT_command_input.modal(operator, context, press)
    check(ui._trim_drag is None,
          "an ordinary production point press never arms Trim drag-over")

    trim.step_index = 0
    ui.app_state.active_command = trim
    ui.NARWHAL_OT_command_input.modal(operator, context, press)
    check(ui._trim_drag is None,
          "Trim's production cutter-selection press never arms drag-over")
    ui._sel_drag = None
    trim.step_index = 1

    feeds.clear()
    ui.app_state.active_command = trim
    ui.NARWHAL_OT_command_input.modal(operator, context, press)
    cancelled = []
    real_cancel = ui.app_state.cancel
    ui.app_state.cancel = lambda: (cancelled.append(True), setattr(ui.app_state, "active_command", None))
    escape = types.SimpleNamespace(type="ESC", value="PRESS", mouse_x=0, mouse_y=0,
                                   shift=False, ctrl=False, alt=False, ascii="")
    ui.NARWHAL_OT_command_input.modal(operator, context, escape)
    ui.app_state.cancel = real_cancel
    check(cancelled and ui._trim_drag is None,
          "the production Escape route clears Trim drag ownership")

    ui.app_state.active_command = trim
    ui._trim_drag_begin(area, space, region, 10.0, 0.0, latched=True)
    ui.app_state.active_command = None
    ui._trim_drag_update(region.x + 20.0, region.y)
    check(ui._trim_drag is None,
          "a command changed before movement releases Trim drag over latched/empty space")

    ui.app_state.active_command = trim
    ui._trim_drag_begin(area, space, region, 0.0, 0.0, latched=False)
    ui.app_state.feed_line = lambda text: (
        feeds.append(text), setattr(ui.app_state, "active_command", None))
    ui._trim_drag_update(region.x + 12.0, region.y)
    check(ui._trim_drag is None,
          "a command ending during a swept pick releases mouse ownership immediately")
    ui.app_state.feed_line = lambda text: feeds.append(text)
    ui.app_state.active_command = trim

    feeds.clear()
    ui._trim_drag_begin(area, space, region, 0.0, 0.0, latched=False)
    redraws = []
    ui._tag_redraw_viewports = lambda: redraws.append("tag")
    move = types.SimpleNamespace(type="MOUSEMOVE", value="NOTHING",
                                 mouse_x=region.x + 40.0, mouse_y=region.y,
                                 shift=False, ctrl=False, alt=False, ascii="")
    release = types.SimpleNamespace(type="LEFTMOUSE", value="RELEASE",
                                    mouse_x=region.x + 40.0, mouse_y=region.y,
                                    shift=False, ctrl=False, alt=False, ascii="")
    moved = ui.NARWHAL_OT_command_input.modal(operator, context, move)
    released = ui.NARWHAL_OT_command_input.modal(operator, context, release)
    check(moved == {"RUNNING_MODAL"} and released == {"RUNNING_MODAL"}
          and len(feeds) == 2 and ui._trim_drag is None,
          "the production modal routes held movement and release through Trim drag-over")
    check(redraws, "modal Trim drag-over repaints after document changes")
finally:
    ui._trim_drag = real_trim_drag
    ui._area_by_pane_key = real_area_lookup
    ui._pane_key = real_pane_key
    ui.pick_curve_object = real_curve_pick
    ui.object_pick_location = real_pick_location
    ui.app_state.feed_line = real_feed
    ui.app_state.history = real_history
    ui.app_state.active_command = real_command
    ui._update_pick_cursor = real_update_cursor
    ui._tag_redraw_viewports = real_tag
    for name, value in patched.items():
        setattr(ui, name, value)

if failures:
    raise SystemExit(f"FAIL: {len(failures)}/{checks}: {failures}")
print(f"\n{checks}/{checks} Trim drag-over checks passed")
