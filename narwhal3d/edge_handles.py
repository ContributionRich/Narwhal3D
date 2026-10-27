"""Blender/UI orchestration for exact fillet-radius handles.

The command owns radii, section curves, validation and recipes. This module only
projects its cached records, captures pointer motion and edits scalar parameters.
No kernel work runs during drawing. Pixel-to-radius sensitivity is measured from
the projected radius direction, with a view-plane fallback for end-on handles.
"""
import math
import time

_boxes = {}
_drag = None
_numeric = None


def _ui():
    from . import ui
    return ui


def draw(region, rv3d, shader, batch_for_shader):
    ui = _ui()
    cmd = ui.app_state.active_command
    getter = getattr(cmd, 'handle_records', None)
    pane = (region.x, region.y, region.width, region.height)
    _boxes[pane] = []
    if not callable(getter):
        return
    records = getter()
    color = ui.design.rgb01(ui.design.VIEWPORT_TEXT)
    for record in records:
        anchor = ui._project_2d(region, rv3d, record.get('anchor', record['point']))
        point = ui._project_2d(region, rv3d, record.get('grip_point', record['point']))
        if anchor is None or point is None:
            continue
        x, y = map(float, anchor)
        dx, dy = float(point[0])-x, float(point[1])-y
        radius = float(record['size'])
        pixels = math.hypot(dx, dy)
        if pixels > 4 and record.get('endpoint') is not None:
            axis = dx/pixels, dy/pixels
            sensitivity = pixels / max(radius, 1e-12)
        else:
            from mathutils import Vector
            right = rv3d.view_matrix.inverted().col[0].xyz.normalized()
            offset = Vector(record.get('anchor', record['point'])) + right
            p = ui._project_2d(region, rv3d, offset)
            sensitivity = max(1e-6, math.hypot(p[0]-x, p[1]-y)) if p else 1.0
            axis = .845, .535
        # Keep a legible grab target even when the exact section is only a few pixels.
        distance = max(ui._fpx(34), pixels)
        hx, hy = x + axis[0]*distance, y + axis[1]*distance
        size = ui._fpx(5)
        ui._alpha_blend()
        shader.bind()
        ui._draw_glyph(shader, ui.glyphs.segment(x, y, hx, hy, ui._fpx(1)), color)
        ui._draw_glyph(shader, ui.glyphs.disc(hx, hy, size), ui.design.rgb01(ui.design.ACCENT))
        font = ui._fpx(13)
        label = f'{radius:.3g}'
        tx, ty = hx + ui._fpx(9), hy - font*.35
        ui._draw_text(tx, ty, label, font, color)
        hit_size = ui._fpx(9)
        _boxes[pane].append({
            'cmd': cmd, 'id': record.get('id'), 'radius': radius, 'axis': axis,
            'pixels_per_unit': sensitivity,
            'grip': (region.x+hx-hit_size, region.y+hy-hit_size,
                     region.x+hx+hit_size, region.y+hy+hit_size),
            'label': (region.x+tx, region.y+ty-ui._fpx(3),
                      region.x+tx+ui._text_width(label, font)+ui._fpx(5), region.y+ty+font),
        })


def boxes():
    ui = _ui()
    cmd = ui.app_state.active_command
    ids = {record.get('id') for record in getattr(cmd, 'handle_records', lambda: ())()}
    return [record for rows in _boxes.values() for record in rows
            if record['cmd'] is cmd and record['id'] in ids]


def _inside(x, y, box):
    return box[0] <= x < box[2] and box[1] <= y < box[3]


def _apply(value):
    ui = _ui()
    if _drag is None or ui.app_state.active_command is not _drag['cmd']:
        return
    message, done = _drag['cmd'].set_handle_radius(_drag['id'], value, ui.app_state.ctx)
    _drag['last'] = time.monotonic()
    if message:
        ui.app_state.log(message)
    ui._tag_redraw_viewports()
    ui.redraw_cmdbar()


def numeric_start():
    ui = _ui()
    if _numeric is None or ui.app_state.active_command is not _numeric['cmd']:
        return None
    return str(_numeric['radius'])


def numeric_apply(text):
    ui = _ui()
    if numeric_start() is None:
        return 'That fillet handle is no longer active'
    message, done = _numeric['cmd'].set_handle_radius(_numeric['id'], text, ui.app_state.ctx)
    ui._tag_redraw_viewports()
    ui.redraw_cmdbar()
    return message


def handle_event(event):
    global _drag, _numeric
    ui = _ui()
    if _drag is not None:
        if ui.app_state.active_command is not _drag['cmd']:
            _drag = None
            return False
        if event.type == 'ESC' and event.value == 'PRESS':
            _apply(_drag['radius'])
            _drag = None
            return True
        if event.type == 'MOUSEMOVE':
            dx, dy = event.mouse_x-_drag['start'][0], event.mouse_y-_drag['start'][1]
            delta = dx*_drag['axis'][0] + dy*_drag['axis'][1]
            value = max(1e-9, _drag['radius'] + delta/_drag['pixels_per_unit'])
            _drag['pending'] = value
            if time.monotonic()-_drag['last'] >= .05:
                _apply(value)
            return True
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            if _drag['pending'] is not None:
                _apply(_drag['pending'])
            _drag = None
            return True
    if event.type != 'LEFTMOUSE' or event.value != 'PRESS':
        return False
    if not callable(getattr(ui.app_state.active_command, 'set_handle_radius', None)):
        return False
    if not ui._point_in_view3d(event.mouse_x, event.mouse_y):
        return False
    for record in boxes():
        if _inside(event.mouse_x, event.mouse_y, record['grip']):
            _drag = dict(record, start=(event.mouse_x,event.mouse_y), last=0.0, pending=None)
            return True
        if _inside(event.mouse_x, event.mouse_y, record['label']):
            import bpy
            _numeric = record
            ui._defer_menu(lambda: bpy.ops.narwhal.edge_radius('INVOKE_DEFAULT'))
            return True
    return False
