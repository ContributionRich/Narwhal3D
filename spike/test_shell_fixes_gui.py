"""Sequential GUI test: --factory-startup --enable-event-simulate --python launch/bootstrap.py
--python spike/test_shell_fixes_gui.py; timeout 200s externally, watchdog 180s internally.
Python is necessary for RNA/events/timers/GPU; main must reject asynchronous draw errors in the log."""
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback
import bpy
OUT = Path(__file__).resolve().parents[1] / 'artifacts'
RESULT = {'status': 'running', 'checks': []}
START = time.monotonic()
POSITION = (0, 0)

def check(ok, label):
    RESULT['checks'].append({'ok': bool(ok), 'label': label})
    print(f"SHELL_FIXES {'PASS' if ok else 'FAIL'}: {label}", flush=True)
    if not ok:
        raise AssertionError(label)

def finish(code, error=None):
    RESULT.update(status='failed' if code else 'passed', error=error, elapsed=time.monotonic() - START)
    try:
        OUT.mkdir(exist_ok=True)
        (OUT / 'shell_fixes.json').write_text(json.dumps(RESULT, indent=2), encoding='utf-8')
        print('SHELL_FIXES_RESULT ' + json.dumps(RESULT), flush=True)
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)  # Timer exceptions/SystemExit do not reliably fail Blender.

def watchdog():
    time.sleep(180)
    finish(1, '180s watchdog expired')  # No RNA access from this thread.

def window():
    return bpy.context.window_manager.windows[0]

def center(box):
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)

def event(key, value, text='', ctrl=False):
    window().event_simulate(type=key, value=value, x=POSITION[0], y=POSITION[1],
                            ctrl=ctrl, **({'unicode': text} if text else {}))

def move(point):
    global POSITION
    POSITION = tuple(map(int, point))
    event('MOUSEMOVE', 'NOTHING')
    yield .15

def tap(key, text='', point=None, ctrl=False):
    if point is not None:
        yield from move(point)
    event(key, 'PRESS', text, ctrl)
    yield .08
    event(key, 'RELEASE', ctrl=ctrl)
    yield .2

def wait_for(predicate, label, timeout=12):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError('Timed out: ' + label)
        yield .1
    check(True, label)
    yield .2

def run():
    from narwhal3d import ui, views, shell_popup, design
    st = ui.app_state
    yield from wait_for(lambda: os.environ.get('NARWHAL3D_UI_READY') == '1' and not ui._viewports_in_progress
                       and ui._viewport_count() == 4 and ui.is_input_active() and ui._tool_scroll_layout is not None,
                       'bootstrap ready: four panes, input and drawn tools', 45)
    RESULT['dimensions'] = [window().width, window().height, ui.uiscale.current()]
    check(len(st.model.file.Objects) == 0, 'refuse nonempty startup document')
    st.cancel()
    check((ui._tool_scroll_indicator is not None) == (ui._tool_scroll_layout['max_offset'] > 0), 'indicator only for overflow')
    def title(arrow=False):
        a = ui._pane_by_label('Perspective')
        r = ui._window_region(a)
        rect = ui._title_hit_rect(a, r)
        if arrow:
            rect = views.title_arrow_rect(rect)
        return (r.x + center(rect)[0], r.y + center(rect)[1])
    st.feed_line('Point')
    check(st.active_command.name == 'Point', 'actual Point command started')
    st.feed_line('w0,0,0')
    check(len(st.model.file.Objects) == 1 and st.active_command is None, 'one completed Point fixture')
    obj = bpy.data.objects[st.model.file.Objects[0].Attributes.Name]
    obj.select_set(True)
    r = ui._window_region(ui._cmdbar_area())
    yield from tap('LEFTMOUSE', point=(r.x + r.width * .7, r.y + 15))
    check(ui._console_focus, 'console click owns focus')
    for char in 'abc':
        yield from tap(char.upper(), char)
    yield from tap('HOME')
    yield from tap('DEL')
    check(st.input_buffer == 'bc' and len(st.model.file.Objects) == 1 and obj.select_get(), 'Home/Delete edits abc to bc, preserves Point')
    yield from tap('A', ctrl=True)
    yield from tap('BACK_SPACE')
    yield from tap('DEL')
    check(st.input_buffer == '' and ui._console_focus and len(st.model.file.Objects) == 1, 'empty focused Delete preserves geometry')
    yield from tap('LEFTMOUSE', point=title())
    check(not ui._console_focus and not shell_popup.is_open() and obj.select_get()
          and ui.pane_label(ui.active_pane_area()) == 'Perspective', 'title text only activates and blurs')
    yield from tap('DEL')
    check(len(st.model.file.Objects) == 0, 'unfocused empty-buffer Delete reaches real 3D View keymap')
    yield from tap('LEFTMOUSE', point=title(True))
    yield from wait_for(shell_popup.is_open, 'title arrow opens popup')
    yield from tap('ESC')
    check(not shell_popup.is_open(), 'Escape dismisses arrow popup')
    for count in (1, 4):
        yield .6  # Isolate each double-click from earlier title presses.
        yield from move(title())
        started = time.monotonic()
        for _ in range(2):
            check(time.monotonic() - started < .5, 'title presses inside double-click deadline')
            event('LEFTMOUSE', 'PRESS')
            yield .03
            event('LEFTMOUSE', 'RELEASE')
            yield .03
        yield from wait_for(lambda: not ui._maximize_busy and ui._viewport_count() == count,
                           f'title double-click returns {count} panes')
        check(not shell_popup.is_open() and ui.is_maximized() == (count == 1), 'maximize/restore without popup')
    old, unit = st.model.absolute_tolerance(), st.model.unit_system()
    check(old != .125 and bpy.ops.narwhal.status_tolerance(value=.125) == {'FINISHED'}, 'real tolerance operator')
    check(st.model.absolute_tolerance() == .125 and st.model.unit_system() == unit
          and st.active_command is None, 'exact binary tolerance, same units, Units command completed')
    st.start_command('Undo')
    check(st.model.absolute_tolerance() == old and st.model.unit_system() == unit
          and len(st.model.file.Objects) == 0, 'Undo reverses tolerance only, not preceding Delete')
    check(bpy.ops.narwhal.status_tolerance(decimal_value='0.001') == {'FINISHED'}
          and st.model.absolute_tolerance() == .001, 'menu decimals bypass float32 rounding')
    st.start_command('Undo')
    check(st.model.absolute_tolerance() == old, 'decimal preset undo restores tolerance')
    for cls in (ui.NARWHAL_MT_status_tolerance, ui.NARWHAL_MT_status_units, ui.NARWHAL_MT_shell_theme):
        check(issubclass(cls, bpy.types.Menu) and getattr(bpy.types, cls.__name__, None) is cls, cls.__name__ + ' registered native Menu')
        drawn, original = [], cls.draw
        def draw(menu, context):
            original(menu, context)
            drawn.append(True)
        cls.draw = draw
        try:
            bpy.ops.wm.call_menu(name=cls.bl_idname)
            yield from wait_for(lambda: bool(drawn), cls.__name__ + ' actually draws')
            yield from tap('ESC')
        finally:
            cls.draw = original
    a = ui.active_pane_area()
    with bpy.context.temp_override(window=window(), area=a, region=ui._window_region(a)):
        check(bpy.ops.narwhal.status_tolerance('INVOKE_DEFAULT') == {'RUNNING_MODAL'}, 'Custom opens numeric dialog')
    yield .3
    yield from tap('ESC')
    check(st.model.absolute_tolerance() == old, 'cancel Custom leaves tolerance intact')
    ui.set_active_pane(ui._pane_by_label('Perspective'))
    index = next(i for i, tab in enumerate(ui.rightpanel.TABS) if tab.name == 'Display')
    yield from tap('LEFTMOUSE', point=center(ui._right_tab_boxes[index]))
    check(ui.right_tab_index() == index, 'Display tab selected by click')
    for theme in ('dark', 'light'):
        ui.set_shell_theme(theme)
        check(design.THEME == theme, 'actual ' + theme + ' palette')
        yield .2
        for narrow in (False, True):
            records, text, panel = [], ui._draw_text, ui._draw_display_panel
            def capture(x, y, label, size=13., color=(0, 0, 0, 1), font_id=ui._DEFAULT_FONT_ID):
                records.append((label, size, tuple(color)))
                return text(x, y, label, size, color, font_id)
            def paint(shader, region, box):
                if narrow:  # Real GPU draw with a bounded body, not a layout/window mutation.
                    x0, y0, x1, y1 = box['body']
                    box = dict(box, body=(x0, y0, min(x1, x0 + ui._fpx(130)), y1))
                ui._draw_text = capture
                try:
                    return panel(shader, region, box)
                finally:
                    ui._draw_text = text
            ui._draw_display_panel = paint
            try:
                ui._tag_redraw_bands()
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
            finally:
                ui._draw_display_panel = panel
                ui._draw_text = text
            size = 12 * ui.uiscale.current()
            for label, token in (('Apply to', 'TEXT_MUTED'), ('Display mode', 'TEXT_MUTED'),
                                 ('Active', 'TEXT_STRONG'), ('All', 'TEXT')):
                check((label, size, design.rgb01(getattr(design, token))) in records, f'{theme}: {label} font/ink drawn')
            name, mode, _ = next(row for row in ui.display_panel_rows() if row[2])
            box = next(row[2:] for row in ui._display_panel_boxes['viewports'] if row[1] == name)
            width = box[2] - box[0] - 2 * ui._fpx(7)
            full = f'{name} — {mode}'
            clipped = ui._clip_text(full, width, size)
            check((clipped, size, design.rgb01(design.TEXT_STRONG)) in records
                  and ui._text_width(clipped, size) <= width and (not narrow or clipped != full),
                  f'{theme}: active viewport strong text fits; narrow fixture really clips')
    yield from scroll(ui, st)
    st.cancel()
    check(len(st.model.file.Objects) == 0, 'finished with zero geometry')
    ui._tag_redraw_bands()
    yield .3
    OUT.mkdir(exist_ok=True)
    check(bpy.ops.screen.screenshot(filepath=str(OUT / 'shell_fixes.png')) == {'FINISHED'}, 'Display screenshot saved')

def scroll(ui, st):
    check(ui._show_unavailable and ui.set_toolbar_tab('Mesh Tools') == 'Mesh Tools', 'real Mesh Tools, unavailable shown')
    yield .3
    check(len(ui._sidebar_buttons) == 50 and ui._tool_scroll_layout['max_offset'] > 0, '50 actual tools overflow normal window')
    def thumb():
        lay, bar = ui._tool_scroll_layout, ui._tool_scroll_indicator
        check(bar is not None and bar['travel'] > 0, 'overflow has usable indicator')
        track, box = bar['track'], bar['thumb']
        height = track[3] - track[1]
        expected = min(height, max(ui._fpx(24), height * (lay['viewport'][3] - lay['viewport'][1]) / lay['content_extent']))
        check(abs(box[3] - box[1] - expected) < 1e-6 and abs(bar['travel'] - (height - expected)) < 1e-6
              and abs(box[3] - (track[3] - bar['travel'] * lay['offset'] / lay['max_offset'])) < 1e-6, 'thumb ratio/travel/current position')
        return lay, bar
    lay, bar = thumb()
    check(lay['offset'] == 0 and bar['thumb'][3] == bar['track'][3], 'thumb starts at top')
    yield from move(center(lay['viewport']))
    check(ui._tool_scroll_hover, 'real pointer move shows hover')
    yield from tap('WHEELDOWNMOUSE')
    check(ui._tool_scroll['Mesh Tools'] == min(lay['max_offset'], ui._fpx(38) * 3), 'actual wheel advances offset')
    # One wheel notch can legitimately reach the end on a short overflow. Put
    # the drag fixture halfway down so a no-op drag cannot pass at an endpoint.
    ui._tool_scroll['Mesh Tools'] = lay['max_offset'] / 2
    ui._tag_redraw_bands()
    yield .2
    lay, bar = thumb()
    yield from move(center(bar['thumb']))
    event('LEFTMOUSE', 'PRESS')
    yield .15
    check(ui._tool_scroll_drag is not None and 0 < ui._tool_scroll['Mesh Tools'] < lay['max_offset'], 'thumb drag starts with room to move')
    before = ui._tool_scroll['Mesh Tools']
    yield from move((POSITION[0], POSITION[1] - bar['travel'] - 2))
    event('LEFTMOUSE', 'RELEASE')
    yield .2
    lay, bar = thumb()
    check(lay['offset'] > before and lay['offset'] == lay['max_offset'] and ui._tool_scroll_drag is None
          and abs(bar['thumb'][1] - bar['track'][1]) < 1e-6, 'drag reaches bottom; release clears state')
    yield from tap('LEFTMOUSE', point=(center(bar['track'])[0], bar['track'][3] - 2))
    check(ui._tool_scroll['Mesh Tools'] < lay['offset'], 'track click pages upward')
    yield from move((window().width / 2, window().height / 2))
    check(not ui._tool_scroll_hover and st.active_command is None, 'leave clears hover; scrollbar never starts command')

def tick():
    try:
        return next(DRIVER)
    except StopIteration:
        finish(0)
    except BaseException:
        finish(1, traceback.format_exc())

if bpy.app.background:
    finish(1, 'Real GUI and --enable-event-simulate required')
threading.Thread(target=watchdog, daemon=True).start()
DRIVER = run()
bpy.app.timers.register(tick, first_interval=.1)
