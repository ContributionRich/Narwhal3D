"""GUI-only v4 regression; bootstrap MUST precede this script (no registration here).
Run sequentially, not alongside another heavy GUI suite:
 NARWHAL3D_DEFER_FIRST_PRESENT=1 timeout 200s tools/blender-5.2.2-linux-x64/blender \
 --factory-startup --enable-event-simulate --python launch/bootstrap.py \
 --python spike/test_v4_live.py
Python is required for Blender's timers/RNA/events. No geometry fixtures or mocks.
Main also checks the process draw log: asynchronous draw exceptions are not hidden here.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts'
RESULT = {'status': 'running', 'checks': [], 'dimensions': None}
START = time.monotonic()
POSITION = (0, 0)


def check(condition, label):
    RESULT['checks'].append({'ok': bool(condition), 'label': label})
    print(f"V4 {'PASS' if condition else 'FAIL'}: {label}", flush=True)
    if not condition:
        raise AssertionError(label)


def finish(code, error=None):
    RESULT.update(status='failed' if code else 'passed', error=error,
                  assert_count=len(RESULT['checks']), elapsed=time.monotonic() - START)
    try:
        OUT.mkdir(exist_ok=True)
        (OUT / 'v4_live.json').write_text(json.dumps(RESULT, indent=2), encoding='utf-8')
        print('V4_LIVE_RESULT ' + json.dumps(RESULT), flush=True)
    finally:
        # Blender swallows timer exceptions; SystemExit alone cannot fail the process.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)


def watchdog():
    # No RNA on this thread; also bounds a blocked native operation/main event loop.
    time.sleep(180)
    finish(1, '180s process watchdog expired')


if bpy.app.background:
    print('V4_LIVE SKIP: real Blender GUI and --enable-event-simulate required', flush=True)
else:
    threading.Thread(target=watchdog, daemon=True).start()


def window():
    return bpy.context.window_manager.windows[0]


def center(box):
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def tap(key, text='', point=None, ctrl=False):
    global POSITION
    if point is not None:
        POSITION = tuple(map(int, point))
        window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=POSITION[0], y=POSITION[1])
        yield 0.08
    args = dict(type=key, x=POSITION[0], y=POSITION[1], ctrl=ctrl)
    window().event_simulate(value='PRESS', **args, **({'unicode': text} if text else {}))
    yield 0.08
    window().event_simulate(value='RELEASE', **args)
    yield 0.12


def click(box):
    yield from tap('LEFTMOUSE', point=center(box))


def double_click(box, first_click):
    """Real press/release pairs; tap() twice exceeds the 0.5s title deadline."""
    global POSITION
    POSITION = tuple(map(int, center(box)))
    args = dict(type='LEFTMOUSE', x=POSITION[0], y=POSITION[1])
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=POSITION[0], y=POSITION[1])
    yield 0.03
    started = time.monotonic()
    for index in range(2):
        if index:
            first_click()
            check(time.monotonic() - started < .5, 'second title press inside double-click deadline')
        window().event_simulate(value='PRESS', **args)
        yield 0.03
        window().event_simulate(value='RELEASE', **args)
        yield 0.03


def type_text(text):
    for char in text:
        yield from tap(char.upper() if char.isascii() and char.isalpha() else 'A', char)


def wait_for(predicate, label, timeout=12):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('Timed out: ' + label)
        yield 0.1
    check(True, label)
    yield 0.15


def run():
    from narwhal3d import ui, views, shell_popup, shell_input, layer_commands, cplane, design
    sys.path.insert(0, str(ROOT / 'spike'))
    from test_theme_bindings import assert_palette, filtered_caption_rows
    st = ui.app_state
    yield from wait_for(lambda: os.environ.get('NARWHAL3D_UI_READY') == '1'
                       and not ui._viewports_in_progress and ui._viewport_count() == 4
                       and ui.is_input_active() and bool(ui._toolbar_tab_boxes)
                       and ui._tool_scroll_layout is not None,
                       'ready: four panes, drawn controls and input modal', 45)
    RESULT['dimensions'] = [window().width, window().height]
    check(window().width >= 800 and window().height >= 600, 'usable GUI dimensions')
    # The global status area is not exposed in screen.areas. Its upper boundary
    # is the minimum y of the modeled/dock areas; includes native border quantization.
    floor = min(a.y for a in window().screen.areas)
    check(abs(floor - round(37 * ui.uiscale.current())) <= 2,
          f'native status strip follows 37 logical pixels (height={floor})')
    check(len(st.model.file.Objects) == 0, 'isolated empty document (refuse user geometry)')
    st.cancel()

    def console_box():
        r = ui._window_region(ui._cmdbar_area())
        return (r.x + r.width * .65, r.y + 8, r.x + r.width * .8, r.y + 22)

    def command_state():
        cmd = st.active_command
        return (id(cmd), repr(cmd.args), cmd.step_index, st.input_buffer,
                len(st.model.file.Objects))

    def pane_box(label, corner=False, title=False):
        a = ui._pane_by_label(label)
        r = ui._window_region(a)
        rect = (views.corner_button_rect(r.width, r.height, ui._fpx(views.CORNER_BUTTON),
                                        ui._fpx(views.CORNER_INSET)) if corner else
                ui._title_hit_rect(a, r) if title else views.title_arrow_rect(ui._title_hit_rect(a, r)))
        return tuple(v + (r.x if i % 2 == 0 else r.y) for i, v in enumerate(rect))

    def camera(a):
        state = ui._pane_view_state(a)
        # RNA normalizes float32 quaternions on assignment; one-ULP round-off is
        # not a changed camera (distance/location/CPlane still compared exactly).
        state['rotation'] = tuple(round(v, 6) for v in state['rotation'])
        plane = state.pop('cplane')
        state['cplane'] = (plane.origin, plane.xaxis, plane.yaxis)
        return state

    check(ui._show_unavailable is True, 'Show Unavailable defaults to showing all tools')
    identity = lambda buttons: [(b.left, b.icon) for b in buttons]
    all_buttons = ui.tbar.context_buttons(ui.toolbar_tab(), set(ui.COMMANDS))
    all_caption = ui._caption_popup_rows()
    check(all_caption != filtered_caption_rows(all_caption), 'caption fixture includes unavailable rows')
    osnaps = [row[0] for row in ui.get_osnap_panel_boxes()]
    status_ids = ui.sbar.pane_ids()
    check(any(not b.enabled for b in all_buttons) and bool(osnaps), 'non-vacuous unavailable/Osnap fixtures')
    for show in (True, False, True):
        if ui._show_unavailable != show:
            check(bpy.ops.narwhal.show_unavailable() == {'FINISHED'}, 'real Show Unavailable operator')
            yield 0.2
            check(ui.sidebar_flyout_layout() is None and not shell_popup.is_open(), 'visibility toggle closes stale popups')
        check(ui._show_unavailable == show and identity(ui._sidebar_buttons) ==
              identity([b for b in all_buttons if show or b.enabled]), f'show={show}: palette filters only left enabled')
        check(ui._caption_popup_rows() == (all_caption if show else filtered_caption_rows(all_caption)),
              f'show={show}: caption rows retain enabled actions and submenus')
        check([row[0] for row in ui.get_osnap_panel_boxes()] == osnaps and ui.sbar.pane_ids() == status_ids
              and ui.statusbar_installed(), f'show={show}: drawn Osnap rows and status model retained')
        cell = next(c for c in ui._sidebar_cells if ui._sidebar_buttons[c[0]].link == 'Point')
        parent = ui._sidebar_buttons[cell[0]]
        rows = ui.tbar.sidebar_flyout_rows(parent, set(ui.COMMANDS))
        check(any(not b.enabled for b in rows), 'Point flyout includes unavailable fixture')
        yield from click(ui.tbar.sidebar_flyout_box(cell))
        flyout = ui.sidebar_flyout_layout()
        check(flyout is not None and identity(flyout['buttons']) ==
              identity([b for b in rows if not b.is_separator and (show or b.enabled)]), f'show={show}: live flyout filtering')
        scale = ui.uiscale.current()
        check(all(abs(getattr(ui.tbar, key) - value * scale) < 1e-6 for key, value in
                  (('FLYOUT_CELL', 32), ('FLYOUT_ICON', 22), ('FLYOUT_GAP', 2))), 'flyout metrics follow UI scale')
        cells = flyout['cells']
        check(all(abs(c[3] - c[1] - 32 * scale) < 1e-6 and abs(c[4] - c[2] - 32 * scale) < 1e-6 for c in cells)
              and abs(cells[1][1] - cells[0][3] - 2 * scale) < 1e-6, 'flyout cells and inter-cell gap are compact')
        point = next(c for c in cells if flyout['buttons'][c[0]].left == 'point')
        yield from click(point[1:])
        check(st.active_command is not None and st.active_command.name == 'Point' and not st.active_command.args
              and len(st.model.file.Objects) == 0, 'linked Point flyout starts command without leaking a point')
        st.cancel()
        ui.sidebar_flyout_close()

    names = [name for name, x0, x1 in ui._toolbar_tab_boxes if x1 - x0 > ui._fpx(60)][:3]
    check(len(names) == 3, 'three full category targets are drawn')
    for part in ('icon', 'label'):
        for name in names:
            r = ui._window_region(ui._cmdbar_area())
            _, x0, x1 = next(b for b in ui._toolbar_tab_boxes if b[0] == name)
            top = ui._toolbar_tab_row_top
            y = top - ui._fpx(15 + ui.tbar.CATEGORY_ICON / 2) if part == 'icon' else top - ui._toolbar_tab_row_h + ui._fpx(18)
            yield from tap('LEFTMOUSE', point=(r.x + (x0 + x1) / 2, r.y + y))
            check(ui.toolbar_tab() == name, f'{name}: {part} click selects category')
            check([(b.left, b.icon) for b in ui._sidebar_buttons] ==
                  [(b.left, b.icon) for b in ui.tbar.context_buttons(name, set(ui.COMMANDS))]
                  and bool(ui._sidebar_cells), f'{name}: real left toolbar refreshed')
    layout = ui._tool_scroll_layout
    before = ui._tool_scroll.get(ui.toolbar_tab(), 0)
    yield from tap('WHEELDOWNMOUSE', point=center(layout['viewport']))
    check(ui._tool_scroll[ui.toolbar_tab()] == min(layout['max_offset'], before + ui._fpx(38) * 3),
          'tool wheel uses full-window viewport and clamps offset')

    for name in ('Line', 'Circle'):
        yield from click(console_box())
        yield from type_text(name)
        yield from tap('TAB' if name == 'Line' else 'RET')
        check(st.active_command is not None and st.active_command.name == name,
              f'{name} starts through the real command field (Tab/Enter acceptance)')
        saved = command_state()
        yield from click(pane_box('Top'))
        check(shell_popup.is_open(), f'{name}: title arrow opens shell popup')
        a = ui._pane_by_label('Top')
        r = ui._window_region(a)
        rect = views.title_menu_rect(ui._title_hit_rect(a, r))
        model = shell_popup._model
        check(model.anchor == (r.x + rect[0], r.y + rect[1]) and model.panels[0].title == 'Top',
              f'{name}: popup model anchored to title, not cursor')
        check(command_state() == saved, f'{name}: opening menu preserves args/step/buffer')
        outside = (r.x + r.width - 35, r.y + 45)
        check(model.hit_test(*outside) is None, f'{name}: dismiss target outside popup')
        yield from tap('LEFTMOUSE', point=outside)
        check(not shell_popup.is_open() and command_state() == saved,
              f'{name}: outside press AND release consumed without geometry input')
        yield from tap('LEFTMOUSE', point=outside)
        check(len(st.active_command.args) == 1 and st.active_command.step_index == 1
              and len(st.model.file.Objects) == 0, f'{name}: next ordinary click accepts first point')
        st.cancel()
        yield 0.15

    # Distinct fixtures expose reset-to-default and copying the survivor's state.
    for i, a in enumerate(ui._canonical_panes()):
        ui._view3d_space(a).region_3d.view_distance = 63.25 + i * 11
        plane = ui.get_cplane(a)
        check(ui.set_cplane(a, cplane.CPlane((i + 1, 2, 3), plane.xaxis, plane.yaxis)),
              f'{ui.pane_label(a)}: distinctive CPlane fixture')
        ui.set_display_mode(a, 'Wireframe' if ui.pane_label(a) == 'Front' else 'Shaded')
    before = {ui.pane_label(a): camera(a) for a in ui._canonical_panes()}
    yield from click(pane_box('Right', True))
    yield from wait_for(lambda: not ui._maximize_busy and ui._viewport_count() == 1,
                       'corner click finishes maximize')
    check(ui.is_maximized() and camera(ui.active_pane_area()) == before['Right'], 'Right state survives maximize')
    for name in ('Top', 'Front'):
        _, x0, x1 = next(b for b in ui.get_viewport_tab_boxes() if b[0] == name)
        yield from tap('LEFTMOUSE', point=((x0 + x1) / 2, ui._tab_strip[2] + views.TAB_H / 2))
        yield from wait_for(lambda: not ui._maximize_busy and ui.pane_label(ui.active_pane_area()) == name,
                           f'real {name} tab switches maximized pane')
        got_camera = camera(ui.active_pane_area())
        if got_camera != before[name]:
            print('CAMERA DIFF', name, got_camera, before[name], flush=True)
        check(ui._viewport_count() == 1 and got_camera == before[name],
              f'{name}: camera, mode and CPlane preserved')
    yield from click(pane_box('Front', True))
    yield from wait_for(lambda: not ui._maximize_busy and ui._viewport_count() == 4,
                       'restore corner returns four panes')
    check(not ui.is_maximized() and {ui.pane_label(a): camera(a) for a in ui._canonical_panes()} == before,
          'all four restored cameras, display modes and custom CPlanes match')

    # An active zero-point command makes swallowed/forwarded releases observable.
    yield from click(console_box())
    yield from type_text('Line')
    yield from tap('RET')
    check(st.active_command.name == 'Line' and not st.active_command.args and st.active_command.step_index == 0,
          'title double-click fixture: Line awaits first point')
    saved = command_state()
    for name in ('Top', 'Front', 'Perspective', 'Right'):
        for count in (1, 4):
            a = ui._pane_by_label(name)
            r = ui._window_region(a)
            rect = views.title_menu_rect(ui._title_hit_rect(a, r))
            anchor = (r.x + rect[0], r.y + rect[1])
            def first_click():
                check(not shell_popup.is_open() and ui.active_pane_area() == a,
                      f'{name}: title text only activates; menu belongs to arrow')
                check(command_state() == saved, f'{name}: first title click cannot feed a point')
            yield from double_click(pane_box(name, title=True), first_click)
            yield from wait_for(lambda: not ui._maximize_busy and ui._viewport_count() == count,
                               f'{name}: title double-click {"maximizes" if count == 1 else "restores"}')
            check(ui.is_maximized() == (count == 1) and not shell_popup.is_open()
                  and command_state() == saved, f'{name}: toggle closes popup, preserves command and zero geometry')
            check({ui.pane_label(a): camera(a) for a in ui._canonical_panes()} ==
                  ({name: before[name]} if count == 1 else before), f'{name}: title toggle preserves all surviving/restored cameras')
    st.cancel()

    index = next(i for i, tab in enumerate(ui.rightpanel.TABS) if tab.name == 'Layers')
    yield from click(ui._right_tab_boxes[index])
    for i in range(64):
        ok, message = layer_commands.new_layer(f'V4-row-{i:03d}')
        check(ok, message)
    ui._tag_redraw_bands()
    yield 0.25
    yield from click(ui._layer_search_box)
    check(ui.layer_search_focused(), 'Layers search gets focus from click')
    yield from type_text('V4-row-063')
    check(ui.layer_search_text() == 'V4-row-063' and st.input_buffer == '', 'typing edits filter, not command')
    check([layer.name for layer in ui._layer_row_objs] == ['V4-row-063'], 'filter displays the actual matching row')
    yield from tap('RET')  # retain the layer filter while giving the command field input
    yield from click(console_box())
    yield from type_text('abcéd')
    shell_state = lambda: (st.input_buffer, shell_input._field.caret, ui.toolbar_tab(), ui.right_tab_index(),
                           ui.layer_search_text(), ui.layer_search_focused(), ui._console_focus)
    saved_shell = shell_state()
    for theme in ('light', 'dark', 'light'):
        check(bpy.ops.narwhal.shell_theme(theme=theme) == {'FINISHED'}, f'{theme}: actual theme operator')
        yield 0.2
        assert_palette(ui, views, design)
        check(design.THEME == theme, f'{theme}: all semantic command/view aliases refreshed')
        check(shell_state() == saved_shell and {ui.pane_label(a): camera(a) for a in ui._canonical_panes()} == before,
              f'{theme}: input/category/panel/search/cameras/modes/custom CPlanes preserved')
        check(all(all(abs(x - y) < 1e-6 for x, y in zip(ui._view3d_space(a).shading.background_color,
                  design.linear(design.VIEWPORT_BG)[:3])) for a in ui._canonical_panes()), f'{theme}: real viewport backgrounds updated')
    st.cancel()
    yield from click(ui._layer_search_box)
    yield from tap('ESC')
    check(ui.layer_search_text() == '' and not ui.layer_search_focused(), 'Esc clears filter and releases focus')
    check(ui._layer_scroll_layout['max_offset'] > 0, 'more than fifty rows overflow Layers viewport')
    for _ in range(80):
        if ui._layer_scroll >= ui._layer_scroll_layout['max_offset']:
            break
        yield from tap('WHEELDOWNMOUSE', point=center(ui._layer_scroll_layout['viewport']))
    check(ui._layer_scroll == ui._layer_scroll_layout['max_offset'], 'wheel reaches final layer')
    row = next(i for i, layer in enumerate(ui._layer_row_objs) if layer.name == 'V4-row-063')
    box = ui._layer_rows[row]
    point = (box[0] + 12, (box[1] + box[3]) / 2)
    check(ui.layer_row_at(*point) == (row, 0), 'last visible hit maps to the indexed Name column')
    yield from tap('LEFTMOUSE', point=point)
    node = st.layers.current
    info = next(layer for layer in st.model.layers() if layer['name'] == 'V4-row-063')
    check(node.name == 'V4-row-063' and st.model.current_layer_index == info['index'],
          'last-row click activates correct document layer, not visible-row index')

    target_unit = 'Inches' if st.model.unit_system() != 'Inches' else 'Millimeters'
    check(bpy.ops.narwhal.status_units(unit=target_unit) == {'FINISHED'}, 'status Units operator executes')
    if st.active_command is not None:
        check(st.active_command.name.lstrip('-').lower() == 'units', 'only Units scale question remains')
        yield from click(console_box())
        yield from type_text('No')
        yield from tap('RET')
    check(st.active_command is None and st.model.unit_system() == target_unit, 'Units changes actual model settings')
    with tempfile.TemporaryDirectory(prefix='v4-units-') as tmp:
        path = str(Path(tmp) / 'units.3dm')
        check(st.model.save(path), 'empty document saves after Units')
        loaded = type(st.model).load(path)
        check(loaded.unit_system() == target_unit and len(loaded.file.Objects) == 0,
              'saved/reloaded model retains Units without geometry')

    model = st.model

    def document_state():
        snap = model.snapshot()
        return (list(snap), snap.settings, snap.strings, snap.layers, snap.groups,
                snap.names, model.current_layer_index)

    document = document_state()
    clipboard = bpy.context.window_manager.clipboard
    yield from click(console_box())
    check(ui._console_focus, 'real command-field click owns clipboard focus')
    yield from type_text('abcd')
    yield from tap('LEFT_ARROW')
    yield from tap('A', 'é')
    check(st.input_buffer == 'abcéd' and shell_input._field.caret == 4, 'LEFT_ARROW then unicode inserts at caret')
    yield from tap('A', ctrl=True)
    yield from tap('C', ctrl=True)
    check(bpy.context.window_manager.clipboard == 'abcéd', 'Ctrl+C copies field selection')
    yield from tap('X', ctrl=True)
    check(st.input_buffer == '', 'Ctrl+X cuts field, not document')
    yield from tap('V', ctrl=True)
    check(st.input_buffer == 'abcéd', 'Ctrl+V pastes into field')
    check(st.model is model and document_state() == document, 'field clipboard leaves complete document unchanged')
    bpy.context.window_manager.clipboard = clipboard
    yield 0.3
    OUT.mkdir(exist_ok=True)
    screenshot = str(OUT / 'v4_live.png')
    check(bpy.ops.screen.screenshot(filepath=screenshot) == {'FINISHED'}, 'final GUI screenshot captured')
    image = bpy.data.images.load(screenshot, check_existing=False)
    try:
        image.colorspace_settings.name = 'Non-Color'
        w, h = image.size
        check([w, h] == RESULT['dimensions'], 'screenshot matches GUI dimensions')
        r = ui._window_region(ui._cmdbar_area())
        pixels = image.pixels[:]
        counts = {}
        for token in ('CONSOLE_BG', 'PROMPT', 'CONSOLE_INPUT'):
            rgb = [v / 255 for v in getattr(design, token)]
            counts[token] = sum(all(abs(pixels[(y * w + x) * 4 + c] - rgb[c]) < .025 for c in range(3))
                                for y in range(r.y + 8, int(r.y + r.height - ui._toolbar_tab_row_h - 8))
                                for x in range(r.x + 10, min(r.x + r.width - 10, r.x + 650)))
            check(counts[token] > (100 if token == 'CONSOLE_BG' else 3), f'console renders {token} palette pixels')
        RESULT['console_pixel_counts'] = counts
    finally:
        bpy.data.images.remove(image)
    check(len(RESULT['checks']) >= 100 and len(st.model.file.Objects) == 0, 'complete assertion count; no geometry created')


DRIVER = run()


def tick():
    try:
        return next(DRIVER)
    except StopIteration:
        finish(0)
    except BaseException:
        error = traceback.format_exc()
        print(error, file=sys.stderr, flush=True)
        try:
            OUT.mkdir(exist_ok=True)
            bpy.ops.screen.screenshot(filepath=str(OUT / 'v4_live.png'))
        except Exception:
            print('Failure screenshot unavailable:\n' + traceback.format_exc(), file=sys.stderr, flush=True)
        finish(1, error)


if not bpy.app.background:
    bpy.app.timers.register(tick, first_interval=0.1)
