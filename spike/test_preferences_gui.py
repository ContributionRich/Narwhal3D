"""OURS: real Blender/UI integration; Python is required for RNA/timers/GPU.
Run SERIAL under Main's GUI lock, on a real display, with a fresh temporary path:
  NARWHAL3D_SETTINGS_FILE=/tmp/narwhal-pref-test/preferences.json \
  NARWHAL_PREF_TEST_PHASE=write NARWHAL3D_DEFER_FIRST_PRESENT=1 \
  timeout 190s tools/blender-patched/blender --factory-startup \
    --enable-event-simulate --python-exit-code 1 \
    --python spike/test_preferences_gui.py --python launch/bootstrap.py
Then repeat with PHASE=read and the SAME settings path. Test script MUST precede
bootstrap: refuse unsafe storage before importing the application. Unset inherited
NARWHAL3D_UI_READY, NARWHAL3D_THEME and NARWHAL3D_WELCOME_REQUEST. Main's existing
launcher helper must supply NARWHAL_PURE_CORE, NARWHAL_SELECTION_CORE,
NARWHAL_PLATFORM_CORE (and its normal geometry/document/CDR core paths).
Negative control: NARWHAL_PREF_TEST_MUTATION=drop-write, fresh write phase: MUST
exit 1 on disk.theme. Popup negative controls: popup-no-draw, popup-wrong-theme,
popup-escape-save (in-memory only); each must fail its corresponding popup check.
Old-style source must fail, never skip missing APIs. Then run unmutated write/read. Templates remain pending Core_root scope, not simulated.
Main must also reject asynchronous draw errors in the runtime log. These checks
observe real draw submissions, not a screenshot pixel/contrast oracle.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback
from contextlib import ExitStack
from copy import deepcopy
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts'
START = time.monotonic()
PHASE = os.environ.get('NARWHAL_PREF_TEST_PHASE')
RESULT = {'phase': PHASE, 'status': 'running', 'checks': [], 'draws': {},
          'mutation': os.environ.get('NARWHAL_PREF_TEST_MUTATION', '')}
LOCK = threading.Lock()
EXPECTED = dict(theme='dark', show_unavailable=False, toolbar='Transform',
                inspector='AI Assistant', grid_snap=True, ortho=True, gumball=False,
                gumball_alignment='World', viewport_tabs=False, osnap_disabled=False,
                osnap_modes=['Cen', 'End', 'Mid', 'Near', 'Point', 'Quad'],
                osnap_project=True)
AI = dict(provider_id='openai_compatible', model='test-model', endpoint='http://localhost:11434',
          credential_env='LOCAL_AI_KEY', allow_insecure_remote=False)
PRINT = dict(size='A4', orientation='Portrait', scale='1:2', view='Top')
SECRET = 'preferences-gui-fixture-secret-NOT-A-REAL-KEY'


def finish(code, error=None):
    if not LOCK.acquire(blocking=False):
        os._exit(1)
    RESULT.update(status='failed' if code else 'passed', error=error,
                  elapsed_s=time.monotonic() - START)
    try:
        OUT.mkdir(exist_ok=True)
        (OUT / f'preferences_{PHASE}.json').write_text(json.dumps(RESULT, indent=2))
        print('PREFERENCES_GUI_RESULT ' + json.dumps(RESULT), flush=True)
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)  # Blender swallows timer exceptions; quit can return zero.


def check(ok, label):
    RESULT['checks'].append({'ok': bool(ok), 'label': label})
    if not ok:
        raise AssertionError(label)


def equal(actual, expected, label):
    check(actual == expected, f'{label}: expected {expected!r}, got {actual!r}')


def watchdog():
    time.sleep(180)
    finish(1, '180s watchdog expired')  # Never touch RNA/GPU from this thread.


def disk():
    text = SETTINGS.read_text() if SETTINGS.exists() else '{}'
    check(SECRET not in text, 'credential secret VALUE never persisted')
    # Unchanged defaults may be omitted on disk; changed values must still be
    # physically present (e.g. dark theme makes the drop-write mutation fail).
    return {**preferences.DEFAULTS, **json.loads(text)}


def subset(actual, expected, label):
    for key, value in expected.items():
        got = actual.get(key)
        if key == 'osnap_modes' and isinstance(got, list):
            got = sorted(got)
        equal(got, value, label + '.' + key)


def document():
    return (ui.app_state.model.unit_system(), ui.app_state.model.absolute_tolerance())


def restored():
    check(ui._preferences_ready, 'preferences ready before presentation')
    subset(ui.user_preference_snapshot(), EXPECTED, 'restored')
    subset(vars(ui._ai['state'].provider), AI, 'restored.ai')
    check(ui._ai['state'].provider.credential_configured, 'fake credential fixture recognized')
    subset(preferences.get('print_settings'), PRINT, 'restored.print')
    equal(document(), ('Inches', .037), 'document units/tolerance untouched')


def observe_restore():
    try:
        check(os.environ.get('NARWHAL3D_UI_READY') != '1', 'restore precedes UI_READY frame')
        before = SETTINGS.read_bytes() if SETTINGS.exists() else None
        equal(before, INITIAL_BYTES, 'bootstrap did not write defaults before restore')
        original_restore()
        equal(document(), ('Inches', .037), 'restore preserves nondefault document settings')
        if PHASE == 'read':
            restored()
            equal(SETTINGS.read_bytes(), before, 'restore did not overwrite defaults on disk')
        RESULT['before_ready_observed'] = True
    except BaseException:
        finish(1, traceback.format_exc())


def redraw():
    ui._tag_redraw_bands()
    ui._tag_redraw_viewports()
    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)


def inspector(name):
    ui.set_right_tab(next(i for i, tab in enumerate(ui.rightpanel.TABS) if tab.name == name))
    redraw()


def rect_tuple(rect):
    return (rect.x, rect.y, rect.x + rect.w, rect.y + rect.h)


def print_choice(key, value):
    region = ui._print_dialog_region()
    lay = ui._print_dialog_layout(region)
    rect = next(v for rid, _, v in lay.rows if rid == key)
    click = lambda r: ui.print_dialog_click(region.x + r.center[0], region.y + r.center[1])
    equal(click(rect), 'opened:' + key, 'Print dropdown opens')
    lay = ui._print_dialog_layout(region)
    index = ui.print_dialog_state().choices(key).index(value)
    equal(click(dict(lay.dropdown)[index]), 'chose:' + key, 'Print choice adapter')
    equal(getattr(ui.print_dialog_state(), key), value, 'Print.' + key)


def draw_checks(name):
    records = {key: {'rect': [], 'text': []} for key in
               ('_draw_bug_panel', '_draw_ai_panel', 'draw_print_dialog')}
    def capture(kind, original):
        def wrapped(*args, **kwargs):
            frame = sys._getframe(1)
            for _ in range(4):
                if frame is None:
                    break
                if frame.f_code.co_name in records:
                    value = (list(args[1:5]), tuple(args[6] if kind == 'round' else args[5])) if kind in ('rect', 'round') else (
                        str(args[2]), tuple(args[4] if len(args) > 4 else kwargs.get('color', (0, 0, 0, 1))))
                    records[frame.f_code.co_name]['rect' if kind == 'round' else kind].append(value)
                    break
                frame = frame.f_back
            return original(*args, **kwargs)  # Real GPU/BLF call, not fake drawing.
        return wrapped
    def fill(key, rect, token):
        colors = [color for bounds, color in records[key]['rect'] if tuple(bounds) == tuple(rect)]
        check(bool(colors) and all(color == token for color in colors), name + ' ' + key + ' field/surface')
    ui.set_shell_theme(name)
    with patch.object(ui, '_draw_rect', capture('rect', ui._draw_rect)), \
         patch.object(ui, '_draw_round_rect', capture('round', ui._draw_round_rect)), \
         patch.object(ui, '_draw_text', capture('text', ui._draw_text)):
        inspector('Bug Report')
        fill('_draw_bug_panel', ui._bug['lay']['box'], design.rgb01(design.FIELD_BG))
        inspector('AI Assistant')
        fill('_draw_ai_panel', ui._ai['lay']['input'], design.rgb01(design.FIELD_BG))
        check(ui.open_print_dialog(), 'Print opens for real draw')
        redraw()
        lay = ui._print_dialog_layout(ui._print_dialog_region())
        fill('draw_print_dialog', rect_tuple(lay.panel), design.rgb01(design.PANEL_BG))
        for rid, _, rect in lay.rows:
            if rid in PRINT:
                fill('draw_print_dialog', rect_tuple(rect), design.rgb01(design.FIELD_BG))
        page = ui._print_dialog['page']
        check(page is not None, 'real physical Print page exists: ' + ui.print_dialog_state().message)
        sheet = ui.printdialog.page_rect(lay.preview, page.width_mm, page.height_mm)
        fill('draw_print_dialog', rect_tuple(sheet), (1, 1, 1, 1))
        for key, record in records.items():
            colors = [color for _, color in record['text']]
            check(design.rgb01(design.TEXT) in colors and design.rgb01(design.TEXT_MUTED) in colors,
                  name + ' ' + key + ' actual TEXT/TEXT_MUTED')
        if name == 'dark':
            check(design.rgb01(design.FIELD_BG) != (1, 1, 1, 1), 'dark fields are not white')
        OUT.mkdir(exist_ok=True)
        bpy.ops.screen.screenshot(filepath=str(OUT / f'preferences_{name}.png'))
        ui.close_print_dialog()
    RESULT['draws'][name] = {key: {k: len(v) for k, v in rec.items()} for key, rec in records.items()}


def ai_popup_checks(name, win):
    popup = ui.ai_settings_popup
    before_bytes = SETTINGS.read_bytes()
    before_preferences = deepcopy(ui.user_preference_snapshot())
    before_provider = deepcopy(vars(ui._ai['state'].provider))
    generation = ui._ai.get('settings_generation', 0)
    previous_model = popup._model
    calls, boxes, texts = [], [], []
    drawing = False
    original_draw = popup.draw_model
    original_box, original_text = ui._draw_round_rect, ui._draw_text

    def draw(model, theme='light'):
        nonlocal drawing
        drawing = True
        try:
            original_draw(model, theme)
            calls.append((model, theme))
        finally:
            drawing = False

    def box(*args, **kwargs):
        if drawing:
            boxes.append((tuple(args[1:5]), tuple(args[6])))
        return original_box(*args, **kwargs)

    def text(*args, **kwargs):
        if drawing:
            texts.append((str(args[2]), tuple(args[4])))
        return original_text(*args, **kwargs)

    def palette(token):
        return tuple(v / 255 for v in bytes.fromhex(popup.PALETTES[name][token][1:])) + (1,)

    with ExitStack() as stack:
        discovery = stack.enter_context(patch.object(ui, '_ai_start_model_discovery', return_value=False))
        save = stack.enter_context(patch.object(ui, '_apply_ai_settings', wraps=ui._apply_ai_settings))
        stack.enter_context(patch.object(popup, 'draw_model', draw))
        stack.enter_context(patch.object(ui, '_draw_round_rect', box))
        stack.enter_context(patch.object(ui, '_draw_text', text))
        if RESULT['mutation'] == 'popup-no-draw':
            stack.enter_context(patch.object(popup, 'draw_model', lambda *_: None))
        elif RESULT['mutation'] == 'popup-wrong-theme':
            stack.enter_context(patch.object(popup, 'draw_model', lambda model, theme: draw(
                model, 'light' if theme == 'dark' else 'dark')))
        # INVOKE now hands ownership to the Window overlay/input router, not a
        # native invoke_props_dialog; operator.draw is no longer its renderer.
        equal(bpy.ops.narwhal.ai_settings('INVOKE_DEFAULT'), {'FINISHED'}, name + ' actual AI settings invoke')
        model = popup._model
        check(popup.is_open() and isinstance(model, popup.PopupModel), name + ' custom settings popup open')
        check(model is not previous_model, name + ' fresh popup draft')
        check(popup._handler is not None and popup._window == win, name + ' Window draw handler registered')
        equal(ui._ai['settings_generation'], generation + 1, name + ' new settings generation')
        equal(model.original, ui._ai['state'].provider, name + ' draft starts from live provider')
        equal(model.provider_id, AI['provider_id'], name + ' draft provider')
        equal({key: field.text for key, field in model.fields.items()},
              {key: AI[key] for key in model.fields}, name + ' restored draft fields')
        equal(model.allow_insecure_remote, AI['allow_insecure_remote'], name + ' draft transport policy')
        equal(model.anchor, ui._ai_settings_anchor(bpy.context), name + ' popup anchored to settings button')
        equal(model.window_size, (win.width, win.height), name + ' popup window dimensions')
        equal(discovery.call_count, 1, name + ' discovery routed once without network')
        equal(vars(discovery.call_args.args[0]), before_provider, name + ' discovery uses restored configuration')
        yield .4
        redraw()
        check(bool(calls) and all(m is model for m, _ in calls), name + ' actual Window popup draw submissions')
        check(all(theme == name for _, theme in calls), name + ' popup draw uses current theme')
        equal(design.THEME, name, name + ' popup leaves shell theme unchanged')
        colors = [color for bounds, color in boxes if bounds == tuple(model.rect)]
        check(bool(colors) and all(color == palette('bg') for color in colors), name + ' actual popup background')
        # Scroll each field into view: absence of an offscreen draw must not be
        # mistaken for correct input styling on a small application window.
        for key, field in model.fields.items():
            model.ensure_visible(key)
            boxes.clear()
            texts.clear()
            redraw()
            colors = [color for bounds, color in boxes if bounds == tuple(model.controls[key])]
            check(bool(colors) and all(color == palette('input') for color in colors),
                  name + ' actual popup input ' + key)
            check((field.text, palette('text')) in texts, name + ' actual popup field text ' + key)
        check(any(color == palette('muted') for _, color in texts), name + ' actual popup muted text')
        widget = bpy.context.preferences.themes[0].user_interface.wcol_menu_back
        expected = design.blender_theme()['wcol_menu_back']['text']
        check(all(abs(a - b) < 1e-5 for a, b in zip(widget.text, expected)), name + ' native menu text normal')
        # A changed draft makes an accidental save-on-Escape observable, unlike
        # cancelling an untouched copy of the already-persisted configuration.
        model.fields['model'].set_text('discard-this-unsaved-model')
        model._changed()
        check(model.fields['model'].text != before_provider['model'], name + ' unsaved draft differs')
        equal(SETTINGS.read_bytes(), before_bytes, name + ' opening/drawing/editing leaves settings bytes unchanged')
        equal(vars(ui._ai['state'].provider), before_provider, name + ' draft is not live configuration')
        if RESULT['mutation'] == 'popup-escape-save':
            original_close = model.close
            def save_on_close():
                model.on_save(model.validated_config())
                original_close()
            stack.enter_context(patch.object(model, 'close', save_on_close))
        win.event_simulate(type='ESC', value='PRESS')
        yield .1
        win.event_simulate(type='ESC', value='RELEASE')
        yield .3
        check(not popup.is_open() and not model.is_open, name + ' Escape closes custom settings popup')
        check(popup._handler is None, name + ' Escape unregisters Window draw handler')
        check('ESC' not in model._releases, name + ' Escape release consumed')
        draw_count = len(calls)
        calls.clear()
        redraw()
        check(not calls, name + ' closed popup no longer draws')
        equal(SETTINGS.read_bytes(), before_bytes, name + ' Escape preserves settings bytes')
        equal(vars(ui._ai['state'].provider), before_provider, name + ' Escape preserves live provider')
        equal(ui.user_preference_snapshot(), before_preferences, name + ' Escape preserves user preferences')
        equal(save.call_count, 0, name + ' Escape never applies draft')
        equal(ui._ai['settings_generation'], generation + 1, name + ' drawing/Escape do not change generation')
        RESULT['draws'][name]['ai_settings_popup'] = {'draws': draw_count}


def run():
    while os.environ.get('NARWHAL3D_UI_READY') != '1' or ui._viewports_in_progress:
        yield .1
    check(RESULT.get('before_ready_observed'), 'observed restore before first ready frame')
    win = bpy.context.window_manager.windows[0]
    if PHASE == 'write':
        ui.set_shell_theme('dark')
        subset(disk(), {'theme': 'dark'}, 'disk')  # First negative-control mismatch.
        ui.set_show_unavailable(False)
        ui.set_toolbar_tab('Transform')
        inspector('AI Assistant')
        ui.set_grid_snap(True)
        ui.set_ortho(True)
        ui.set_gumball(False)
        ui.set_gumball_alignment('World')
        ui.set_viewport_tabs_visible(False)
        ui.osnap_panel_click('Near')
        ui.osnap_panel_click('Project')
        rect = ui._ai['lay']['context']
        check(rect[1] == rect[3] and ui.ai_assistant_state().include_selection,
              'AI selection context is automatic, with no checkbox or opt-out to persist')
        equal(bpy.ops.narwhal.ai_settings('EXEC_DEFAULT', **AI), {'FINISHED'}, 'AI execute without network')
        check(ui.open_print_dialog(), 'Print opens')
        for key, value in PRINT.items():
            print_choice(key, value)
        ui.close_print_dialog()
    restored()
    check(ui.ai_assistant_state().include_selection, 'selection context is automatic after restore')
    subset(disk(), EXPECTED, 'disk')
    equal(disk()['ai_config'], AI, 'disk AI whitelist only')
    subset(disk()['print_settings'], PRINT, 'disk Print')
    before_draws = SETTINGS.read_bytes()
    yield .3
    equal(SETTINGS.read_bytes(), before_draws, 'idle ready frame does not overwrite settings')
    check(ui.open_print_dialog(), 'Print reopens')
    subset(vars(ui.print_dialog_state()), PRINT, 'reopened Print')
    ui.close_print_dialog()
    # An empty document correctly has no printable page. Use one exact Line so
    # a missing/incorrect physical-paper preview cannot pass this colour test.
    ui.app_state.feed_line('Line')
    ui.app_state.feed_line('w0,0,0')
    ui.app_state.feed_line('w10,0,0')
    check(len(ui.app_state.model.file.Objects) == 1, 'real printable Line fixture')
    for name in ('dark', 'light'):
        draw_checks(name)
        yield from ai_popup_checks(name, win)
    ui.set_shell_theme('dark')
    inspector('AI Assistant')
    restored()
    subset(disk(), EXPECTED, 'final disk')
    check(not any(k in disk() for k in ('units', 'tolerance', 'ModelUnitSystem', 'ModelAbsoluteTolerance')),
          'document settings never become user preferences')
    finish(0)


def tick():
    try:
        check(time.monotonic() - START < 180, 'timer within 180s budget')
        with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
            return next(STEPS)
    except BaseException:
        finish(1, traceback.format_exc())


threading.Thread(target=watchdog, daemon=True).start()
try:
    check(PHASE in ('write', 'read'), 'NARWHAL_PREF_TEST_PHASE must be write/read')
    override = os.environ.get('NARWHAL3D_SETTINGS_FILE', '')
    check(bool(override), 'REFUSE missing NARWHAL3D_SETTINGS_FILE (never use main user storage)')
    SETTINGS = Path(override).expanduser().resolve()
    check(SETTINGS.is_relative_to(Path(tempfile.gettempdir()).resolve()) and not Path(override).is_symlink(),
          'REFUSE non-temporary or symlink settings override')
    check(SETTINGS.exists() == (PHASE == 'read'), 'write needs fresh path; read needs previous write file')
    INITIAL_BYTES = SETTINGS.read_bytes() if SETTINGS.exists() else None
    check(os.environ.get('NARWHAL3D_DEFER_FIRST_PRESENT') == '1', 'require deferred ready signal')
    check(not os.environ.get('NARWHAL3D_THEME'), 'refuse theme environment override hiding persistence')
    os.environ['LOCAL_AI_KEY'] = SECRET
    import bpy
    sys.path.insert(0, str(ROOT))
    from narwhal3d import ui, design, preferences
    check(not bpy.app.background and not ui._preferences_ready, 'GUI test installed before restoration')
    equal(preferences.settings_path().resolve(), SETTINGS, 'settings API uses only override')
    check(len(ui.app_state.model.file.Objects) == 0, 'refuse nonempty document')
    ui.app_state.model.set_unit_system('Inches')
    ui.app_state.model.set_absolute_tolerance(.037)
    original_restore = ui._restore_user_preferences
    ui._restore_user_preferences = observe_restore
    check(RESULT['mutation'] in ('', 'drop-write', 'popup-no-draw', 'popup-wrong-theme',
                                'popup-escape-save'), 'known mutation mode')
    if RESULT['mutation'] == 'drop-write':
        ui._remember_preferences = lambda values: None
    STEPS = run()
    bpy.app.timers.register(tick, first_interval=.1)
except BaseException:
    finish(1, traceback.format_exc())
