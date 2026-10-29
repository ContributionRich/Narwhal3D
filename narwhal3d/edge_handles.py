"""Blender/UI orchestration for exact fillet-radius handles.

The command owns radii, section curves, validation and recipes. This module only
projects its cached records, captures pointer motion and edits scalar parameters.
No kernel work runs during drawing. Pixel-to-radius sensitivity is measured from
the projected radius direction, with a view-plane fallback for end-on handles.
"""
import math

_boxes = {}
_drag = None
_numeric = None
_preview_timer = None


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


def _drag_is_current(drag):
    if drag is None or _drag is not drag or _ui().app_state.active_command is not drag['cmd']:
        return False
    return (not drag.get('command_input') or
            drag['cmd'].handle_input_active() and
            drag['cmd']._handle_input is drag['input_session'])


def _cancel_preview_timer():
    global _preview_timer
    callback, _preview_timer = _preview_timer, None
    if callback is not None:
        import bpy
        if bpy.app.timers.is_registered(callback):
            bpy.app.timers.unregister(callback)


def _drop_drag():
    global _drag
    _cancel_preview_timer()
    _drag = None


def _schedule_preview():
    """One latest-value slot, consumed on the next main-thread timer tick, even at rest.

    Fillet motion only schedules the existing OCCT section simulation. No worker
    handles, new geometry math, or kernel calls in the mouse-motion handler.
    """
    global _preview_timer
    if _preview_timer is not None:
        return
    drag = _drag

    def tick():
        global _preview_timer
        # A canceled callback may already have been queued. It must neither consume
        # a new drag's value nor clear the new drag's timer, even on the same command.
        if _preview_timer is not tick:
            return None
        _preview_timer = None
        if not _drag_is_current(drag):
            return None
        value = drag['pending']
        if value is not None and value != drag.get('preview_value'):
            # Chamfer has no section wires: retain its existing exact preview rather
            # than blanking Preview=Yes while sharing this pointer/timer plumbing.
            _apply(value, preview_only=not drag['cmd'].chamfer)
            drag['preview_value'] = value
        return None

    import bpy
    _preview_timer = tick
    bpy.app.timers.register(tick, first_interval=0.0)


def _apply(value, *, preview_only=False):
    ui = _ui()
    if not _drag_is_current(_drag):
        return
    message, done = _drag['cmd'].set_handle_radius(
        _drag['id'], value, ui.app_state.ctx, preview_only=preview_only)
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
    st = ui.app_state
    cmd = st.active_command
    input_active = callable(getattr(cmd, 'handle_input_active', None)) and cmd.handle_input_active()
    if _drag is not None and not _drag_is_current(_drag):
        _drop_drag()
    # Radius input owns its cancellation and confirmation, not the outer command.
    if input_active and event.value == 'PRESS':
        if event.type == 'ESC':
            _drop_drag()
            cmd.cancel_handle_input(st.ctx)
            st.input_buffer = ''
            ui._tag_redraw_viewports()
            ui.redraw_cmdbar()
            return True
        if event.type in ('RET', 'NUMPAD_ENTER', 'SPACE'):
            line, st.input_buffer = st.input_buffer, ''
            if line.strip():
                st.history.append('> ' + line)
            elif _drag is not None and _drag['pending'] is not None:
                # Enter during a held drag accepts the last pointer value, not the
                # original click default or the last value a timer happened to consume.
                line = str(_drag['pending'])
            _drop_drag()
            st.feed_line(line)
            return True
    if _drag is not None:
        if event.type == 'ESC' and event.value == 'PRESS':
            _cancel_preview_timer()
            _apply(_drag['radius'])
            _drop_drag()
            return True
        if event.type == 'MOUSEMOVE':
            dx, dy = event.mouse_x-_drag['start'][0], event.mouse_y-_drag['start'][1]
            # OURS: a three-logical-pixel threshold separates a click from hand jitter.
            if not _drag['moved'] and math.hypot(dx, dy) < ui._fpx(3):
                return True
            _drag['moved'] = True
            delta = dx*_drag['axis'][0] + dy*_drag['axis'][1]
            value = max(1e-9, _drag['radius'] + delta/_drag['pixels_per_unit'])
            _drag['pending'] = value
            _schedule_preview()
            return True
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            _cancel_preview_timer()
            if _drag['pending'] is not None:
                _apply(_drag['pending'])
            if _drag['moved'] and _drag.get('command_input'):
                cmd.finish_handle_input()
            # A click leaves the radius prompt active; a drag only ends the adjustment.
            _drop_drag()
            return True
    if event.type != 'LEFTMOUSE' or event.value != 'PRESS':
        return False
    if not callable(getattr(cmd, 'set_handle_radius', None)):
        return False
    if not ui._point_in_view3d(event.mouse_x, event.mouse_y):
        return False
    for record in boxes():
        if _inside(event.mouse_x, event.mouse_y, record['grip']):
            _drop_drag()
            begin = getattr(cmd, 'begin_handle_input', None)
            if input_active:
                cmd.cancel_handle_input(st.ctx)
            if callable(begin) and not begin(record['id'], st.ctx):
                return True
            st.input_buffer = ''
            _drag = dict(record, start=(event.mouse_x,event.mouse_y),
                         pending=None, moved=False, command_input=callable(begin),
                         input_session=getattr(cmd, '_handle_input', None))
            return True
        if _inside(event.mouse_x, event.mouse_y, record['label']):
            import bpy
            _drop_drag()
            if input_active:
                cmd.cancel_handle_input(st.ctx)
            _numeric = record
            ui._defer_menu(lambda: bpy.ops.narwhal.edge_radius('INVOKE_DEFAULT'))
            return True
    return False
