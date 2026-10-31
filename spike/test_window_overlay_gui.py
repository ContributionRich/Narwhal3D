"""OURS: real RNA/GPU pixel regression; Python is required for Blender integration.
Main runs SERIAL under its GUI lock (never run this alongside another GUI suite):
  NARWHAL3D_SETTINGS_FILE=/tmp/fresh-overlay-test/preferences.json \
  NARWHAL3D_DEFER_FIRST_PRESENT=1 timeout 150s tools/blender-patched/blender \
    --factory-startup --enable-event-simulate --python-exit-code 1 \
    --python spike/test_window_overlay_gui.py --python launch/bootstrap.py
Use Main's normal native-core environment; unset UI_READY/THEME/WELCOME_REQUEST.
This script MUST precede bootstrap to protect user preferences. A fresh temporary
settings path is REQUIRED. NARWHAL_OVERLAY_MUTATION=regions must fail PIXEL coverage
(with both themes exercised), not merely an API/registration assertion. Main must
also reject asynchronous draw errors in the log. No second window/lifecycle stress:
spike/test_window_overlay_patch.py owns that native CPython fixture coverage.
"""
import gc
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback
from unittest.mock import patch
import weakref

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts'
START = time.monotonic()
RESULT = {'status': 'running', 'checks': [], 'samples': {},
          'mutation': os.environ.get('NARWHAL_OVERLAY_MUTATION', '')}
LOCK = threading.Lock()


def check(ok, label, fatal=True):
    RESULT['checks'].append({'ok': bool(ok), 'label': label})
    if not ok and fatal:
        raise AssertionError(label)


def finish(code, error=None):
    if not LOCK.acquire(blocking=False):
        os._exit(1)
    RESULT.update(status='failed' if code else 'passed', error=error,
                  elapsed_s=time.monotonic() - START)
    try:
        OUT.mkdir(exist_ok=True)
        (OUT / 'ui_overlay_seams.json').write_text(json.dumps(RESULT, indent=2))
        print('WINDOW_OVERLAY_RESULT ' + json.dumps(RESULT), flush=True)
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)  # Blender swallows exceptions from timer callbacks.


def watchdog():
    time.sleep(140)
    finish(1, '140s watchdog expired')  # No RNA access from this thread.


def settle():
    for area in WIN.screen.areas:
        area.tag_redraw()
    yield .25
    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
    yield .15


def capture(name, points):
    path = OUT / (name + '.png')
    check(bpy.ops.screen.screenshot(filepath=str(path)) == {'FINISHED'}, name + ': capture')
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        image.colorspace_settings.name = 'Non-Color'  # PNG display bytes, NOT linearized sRGB.
        w, h = image.size
        check((w, h) == tuple(RESULT['viewport'][2:]), 'PNG matches full native GPU viewport')
        check(all(0 <= x < w and 0 <= y < h for x, y in points), 'samples inside screenshot')
        return [tuple(round(v * 255) for v in image.pixels[4*(y*w+x):4*(y*w+x)+3])
                for x, y in points]  # bpy pixels and window coordinates are both bottom-up.
    finally:
        bpy.data.images.remove(image)


def coverage(label, points, before, painted, after, expected, tolerance=0):
    wrong = [i for i, (got, want) in enumerate(zip(painted, expected))
             if any(abs(a-b) > tolerance for a, b in zip(got, want))]
    ghosts = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    RESULT['samples'][label] = {'points': points, 'before': before, 'painted': painted,
                               'after_dismiss': after, 'expected': expected,
                               'wrong': wrong, 'ghosts': ghosts}
    check(not wrong, label + ': PIXEL seam coverage', fatal=False)
    check(not ghosts, label + ': PIXEL no ghosts after dismissal', fatal=False)
    check(any(a != b for a, b in zip(before, painted)), label + ': visible overlay', fatal=False)


def outline_precondition(theme, pane, points, before, vx, hy, pad):
    """Observe the real outline's contribution, not proximity to its unblended RGB.

    MEASURED: packaged Linux at scale .95 draws a 1.9px frame; light outline
    samples differ from ACCENT_TITLE by 7 despite correct overlay coverage.
    Suppress ONLY draw_viewport_label's active frame batch, leaving titles and
    all other drawing intact. Python is required to intercept the live GPU call.
    """
    draw_rects = ui._draw_rects
    calls = 0

    def without_outline(shader, rects, color):
        nonlocal calls
        region = bpy.context.region
        if region and bpy.context.area == pane:
            w, h = region.width, region.height
            bw = max(1.0, ui._fpx(design.VIEWPORT_BORDER_W))
            frame = [(0, 0, w, bw), (0, h-bw, w, h),
                     (0, 0, bw, h), (w-bw, 0, w, h)]
            if rects == frame and color == design.rgb01(design.ACCENT_TITLE):
                calls += 1
                return
        return draw_rects(shader, rects, color)

    with patch.object(ui, '_draw_rects', without_outline):
        yield from settle()
        suppressed = capture('ui_overlay_' + theme + '_outline_suppressed', points)
    yield from settle()
    restored = capture('ui_overlay_' + theme + '_outline_restored', points)
    observed = [i for i, (a, b) in enumerate(zip(suppressed, restored)) if a != b]
    # Separate arms exclude the intersection: one corner pixel cannot prove both.
    vertical = [i for i in observed if vx[0]-pad <= points[i][0] <= vx[1]+pad
                and points[i][1] < hy[0]-pad]
    horizontal = [i for i in observed if hy[0]-pad <= points[i][1] <= hy[1]+pad
                  and points[i][0] < vx[0]-pad]
    RESULT.setdefault('outline', {})[theme] = {
        'points': points, 'suppressed': suppressed, 'restored': restored,
        'draw_calls_suppressed': calls, 'observed': observed,
        'vertical': vertical, 'horizontal': horizontal,
    }
    check(ui._pane_key(pane) == ui.active_pane_key(), theme + ': fixture pane remains active')
    check(calls > 0, theme + ': actual active outline painter intercepted')
    check(before == restored, theme + ': outline restoration is pixel-exact')
    check(bool(observed), theme + ': observed active outline included in samples')
    check(bool(vertical) and bool(horizontal), theme + ': observed active outline beside both central seams')
    check(any(vx[0] <= x <= vx[1] and y < hy[0]-pad for x, y in points) and
          any(hy[0] <= y <= hy[1] and x < vx[0]-pad for x, y in points),
          theme + ': samples include both native central gaps, not just the outline')


def native_contract():
    import gpu
    from mathutils import Matrix, Vector
    records = []

    class Probe:
        def __call__(self):
            try:
                vp = tuple(gpu.state.viewport_get())
                projection = gpu.matrix.get_projection_matrix()
                lo = projection @ Vector((0, 0, 0, 1))
                hi = projection @ Vector((vp[2], vp[3], 0, 1))
                records.append((bpy.context.window == WIN, bpy.context.area is None,
                                bpy.context.region is None, vp,
                                gpu.state.blend_get(), gpu.state.depth_test_get(),
                                gpu.state.depth_mask_get(),
                                gpu.matrix.get_model_view_matrix() == Matrix.Identity(4),
                                max(abs(lo[0]+1), abs(lo[1]+1), abs(hi[0]-1), abs(hi[1]-1))))
            except BaseException:
                RESULT['callback_error'] = traceback.format_exc()

    callback = Probe()
    ref = weakref.ref(callback)
    token = bpy.types.Window.draw_handler_add(callback, (), 'POST_PIXEL')
    del callback
    yield from settle()
    check(records and 'callback_error' not in RESULT, 'native callback actually ran without errors')
    check(all(r[:3] == (True, True, True) and r[3][:2] == (0, 0)
              and min(r[3][2:]) > 0 and r[4:8] == ('ALPHA', 'NONE', False, True)
              and r[8] < 1e-5 for r in records), 'Window context, full viewport, matrices and GPU prestate')
    RESULT['viewport'] = records[-1][3]
    RESULT['callback_prestate'] = records[-1]
    bpy.types.Window.draw_handler_remove(token)
    bpy.types.Window.draw_handler_remove(token)
    check(ref() is None, 'idempotent remove releases callback')
    count = len(records)
    yield from settle()
    check(len(records) == count, 'removed callback stays silent')
    callback = Probe()
    ref = weakref.ref(callback)
    token = bpy.types.Window.draw_handler_add(callback, (), 'POST_PIXEL')
    del callback
    check(ref() is not None, 'live handle retains callback')
    del token
    gc.collect()
    yield from settle()
    check(ref() is None and len(records) == count, 'dropping handle unregisters and releases callback')


def run():
    global WIN
    deadline = time.monotonic() + 60
    while not (os.environ.get('NARWHAL3D_UI_READY') == '1' and not ui._viewports_in_progress
               and ui._viewport_count() == 4 and ui.is_input_active() and ui._sidebar_cells):
        check(time.monotonic() < deadline, 'bootstrap ready within 60s')
        yield .2
    WIN = bpy.context.window
    check(len(ui.app_state.model.file.Objects) == 0, 'empty factory-startup document')
    shell_popup.register()  # Popup handlers are normally installed lazily on first open.
    check(ui._window_chrome_handle is not None and
          [space for space, _ in shell_popup._handlers] == [bpy.types.Window], 'real window overlay registrations')
    yield from native_contract()
    if RESULT['mutation'] == 'regions':
        shell_popup.unregister()
        with patch.object(bpy.types, 'Window', None):
            shell_popup.register()  # Execute the real production fallback, not a fake painter.
        bpy.types.Window.draw_handler_remove(ui._window_chrome_handle)
        ui._window_chrome_handle = None
        for space in (bpy.types.SpaceInfo, bpy.types.SpaceView3D):
            for draw in (ui.draw_sidebar_tooltip_overlay, ui.draw_sidebar_flyout_overlay):
                MUTATION_HANDLES.append((space, space.draw_handler_add(draw, (), 'WINDOW', 'POST_PIXEL')))
    original_theme = design.THEME
    for theme in ('light', 'dark'):
        ui.set_shell_theme(theme)
        check(design.THEME == theme, 'actual ' + theme + ' theme')
        yield from settle()
        lt, lb, rt, rb = ui._canonical_panes()
        ui.set_active_pane(lb)
        WIN.event_simulate(type='MOUSEMOVE', value='NOTHING',
                           x=int(lb.x + lb.width / 4), y=int(lb.y + lb.height / 4))
        yield from settle()
        # Area gaps, not window midpoints: docks and arbitrary UI scales shift these.
        vx = (max(a.x+a.width for a in (lt, lb)), min(a.x for a in (rt, rb)))
        hy = (max(a.y+a.height for a in (lb, rb)), min(a.y for a in (lt, rt)))
        check(vx[0] <= vx[1] and hy[0] <= hy[1], 'real central vertical/horizontal area gaps')
        cx, cy = sum(vx)//2, sum(hy)//2
        s = ui.uiscale.current()
        pad = max(4, math.ceil(4*s))
        points = sorted({(x, cy+dy) for x in range(vx[0]-pad, vx[1]+pad+1) for dy in (-25, 0, 25)} |
                        {(cx+dx, y) for y in range(hy[0]-pad, hy[1]+pad+1) for dx in (-25, 0, 25)})

        def open_popup():
            rows = [{'label': '', 'action': 'fixture'} for _ in range(12)]
            title = 'Overlay seam regression across both viewport boundaries'
            model = shell_popup.open(rows, (cx, cy), title, lambda *_: None)
            panel = model.panels[0]
            width = panel.rect[2] - panel.rect[0]
            height = panel.rect[3] - panel.rect[1]
            anchor = (cx-width/2, cy+height/2+shell_popup.ANCHOR_GAP*s)
            model = shell_popup.open(rows, anchor, title, lambda *_: None)
            panel = model.panels[0]
            check(all(panel.rect[0]+pad < x < panel.rect[2]-pad and
                      panel.rect[1]+pad < y < panel.body[3]-pad for x, y in points),
                  'popup blank body spans both seams and active outline')
            return panel.rect

        ui.sidebar_hover_update(-1, -1)
        before = capture('ui_overlay_' + theme + '_before', points)
        yield from outline_precondition(theme, lb, points, before, vx, hy, pad)
        box = open_popup()
        yield from settle()
        painted = capture('ui_overlay_seams' if theme == 'light' else 'ui_overlay_dark_popup', points)
        shell_popup.close()
        yield from settle()
        after = capture('ui_overlay_' + theme + '_after_dismiss', points)

        RESULT[theme] = {'scale': s, 'popup_box': box, 'vertical_gap': vx, 'horizontal_gap': hy}
        coverage(theme + '.popup', points, before, painted, after, [tuple(design.MENU_BG)]*len(points))

        # Use real hit-testing and pointer+14 layout; find a card spanning the dock boundary.
        seam = min(lt.x, lb.x)
        candidate = None
        for _, x0, y0, x1, y1 in ui._sidebar_cells:
            ui.sidebar_hover_update(-1, -1)
            pointer = (int(x0+2*s), int((y0+y1)/2))
            ui.sidebar_hover_update(*pointer)
            layout = ui.sidebar_tooltip_layout()
            if layout and layout['box'][0]+pad < seam-pad and layout['box'][2]-pad > seam+pad:
                candidate = (pointer, layout['box'])
                break
        check(candidate is not None, 'real sidebar tooltip crosses left dock/viewport separator')
        pointer, box = candidate
        # Top padding lies above all glyphs; include native gap and outline pixels.
        y = math.floor(box[3]-max(2, 2*s))
        points = [(x, y) for x in range(seam-pad, seam+pad+1)]
        ui.sidebar_hover_update(-1, -1)
        yield from settle()
        before = capture('ui_overlay_' + theme + '_tooltip_before', points)
        ui.sidebar_hover_redraw_update(*pointer)
        yield from settle()
        painted = capture('ui_overlay_' + theme + '_tooltip', points)
        ui.sidebar_hover_redraw_update(-1, -1)
        yield from settle()
        after = capture('ui_overlay_' + theme + '_tooltip_dismiss', points)
        ink = ui.SIDEBAR_TOOLTIP_BG
        expected = [tuple(round(255*ink[c]*ink[3] + rgb[c]*(1-ink[3])) for c in range(3)) for rgb in before]
        RESULT[theme]['tooltip_box'] = box
        coverage(theme + '.tooltip', points, before, painted, after, expected, tolerance=1)
    ui.set_shell_theme(original_theme)  # Writes only to the required isolated settings file.
    check(all(row['ok'] for row in RESULT['checks']), 'all pixel coverage checks passed')


def tick():
    try:
        with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
            return next(STEPS)
    except StopIteration:
        finish(0)
    except BaseException:
        finish(1, traceback.format_exc())


threading.Thread(target=watchdog, daemon=True).start()
try:
    override = os.environ.get('NARWHAL3D_SETTINGS_FILE', '')
    check(bool(override), 'REFUSE missing temporary NARWHAL3D_SETTINGS_FILE')
    settings = Path(override).expanduser().resolve()
    check(settings.is_relative_to(Path(tempfile.gettempdir()).resolve()) and
          not Path(override).is_symlink() and not settings.exists(), 'REFUSE non-temporary/existing settings path')
    check(os.environ.get('NARWHAL3D_DEFER_FIRST_PRESENT') == '1', 'require deferred presentation')
    check(not any(os.environ.get(k) for k in ('NARWHAL3D_UI_READY', 'NARWHAL3D_THEME',
                                             'NARWHAL3D_WELCOME_REQUEST')), 'clean bootstrap environment')
    check(RESULT['mutation'] in ('', 'regions'), 'known mutation mode')
    check('--enable-event-simulate' in sys.argv, 'require simulated GUI events')
    import bpy
    sys.path.insert(0, str(ROOT))
    from narwhal3d import ui, design, shell_popup, preferences
    check(not bpy.app.background and not ui._preferences_ready, 'GUI test precedes bootstrap')
    check(preferences.settings_path().resolve() == settings, 'settings API honors isolated override')
    check(hasattr(bpy.types.Window, 'draw_handler_add'), 'native Window API installed')
    OUT.mkdir(exist_ok=True)
    MUTATION_HANDLES = []
    STEPS = run()
    bpy.app.timers.register(tick, first_interval=.1)
except BaseException:
    finish(1, traceback.format_exc())
