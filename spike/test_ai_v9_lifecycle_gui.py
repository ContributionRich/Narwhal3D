"""AI08 Blender/event orchestration; no production policy or geometry changes.

The cfg(test) provider bridge is inherited from v8, not a normal catalog enable.
Late decision callbacks are explicitly injected; Send/New Chat/WM_DELETE are events.
"""


def sustained_eviction(ui, f, frames):
    import os
    from pathlib import Path
    from narwhal3d import ai_settings_popup as popup
    samples = []
    for index in range(30):
        yield from f.send('ordinary', consent=index == 0)
        state = ui.ai_assistant_state()
        old = state.extra['workflow']
        handle = old._handle
        if index % 3 == 0:
            yield from f.tap('LEFTMOUSE', f.button('settings'))
            yield from f.wait(popup.is_open, 'stress settings open')
            yield from f.tap('ESC')
            yield from f.wait(lambda: not popup.is_open(), 'stress settings dismissed')
        if index % 2 == 0:
            yield from f.tap('ESC')
            yield from f.wait(lambda: state.status == 'connected', 'stress canonical reject settled')
        for row in range(101):
            state.add_message('assistant', 'display stress Ω '+str(row))
        f.check(len(state.messages) == 100 and state.extra.get('messages_omitted'), 'stress bounded display and omission flag')
        yield from f.fresh()
        f.check(not state.extra.get('messages_omitted') and not state.messages, 'New Chat resets omission state')
        f.check(old._handle == 0 and f.NATIVE.rpc(handle, {'op':'state'}).get('code') == 'unknown_session',
                'stress old native owner disposed')
        yield from f.wait(lambda: not ui.bpy.app.timers.is_registered(ui._ai_poll_transport), 'stress polling timer removed')
        f.check(ui._ai.get('transport_output') is None and ui._ai.get('transport_thread') is None,
                'stress queue and worker references detached')
        status = Path('/proc/'+str(os.getpid())+'/status').read_text()
        samples.append(int(next(line.split()[1] for line in status.splitlines() if line.startswith('VmRSS:'))))
    f.check(max(samples[10:])-min(samples[10:]) <= 65536, 'thirty-cycle warmed RSS span at most64MiB')
    f.check(ui.app_state.model.object_count() == 0, 'stress no geometry publication')
    f.REPORT['v9_stress'] = dict(cycles=30, process_restarts=0, warmup_cycles=10,
        plateau_allowance_kib=65536, rss_kib=samples, injected='101 display-only rows per cycle')
    yield from frames['capture']('thirty-cycle-final')


def long_settings_error(ui, f, frames):
    from narwhal3d import ai_settings_popup as popup
    from test_ai_v8_x11 import OwnedWindow
    yield from f.send('ordinary', consent=True)
    pending = ui._ai['action_pending']
    original = ui.ai_assistant_state().provider
    yield from f.tap('LEFTMOUSE', f.button('settings'))
    yield from f.wait(popup.is_open, 'long-error settings popup open')
    model = popup._model
    # Deliberately injected backend failure, not a real provider/service error.
    model.on_save = lambda _config: 'Disposable settings failure Ω. ' * 100
    window = ui.bpy.context.window_manager.windows[0]
    owned = OwnedWindow(window.width, window.height)
    try:
        owned.resize(800, 520)
        yield from f.wait(lambda: abs(window.width-800) <= 2, 'actual narrow settings resize')
        yield .4
        yield from f.tap('LEFTMOUSE', f.center(model.controls['save']))
        f.check(popup.is_open() and model.error_field == 'save', 'failed Save keeps narrow popup open')
        f.check(len(model.error) == 2000, 'existing settings error ceiling retained')
        f.check(ui.ai_assistant_state().provider == original and ui._ai.get('action_pending') is pending,
                'injected failed Save neither changes provider nor authorizes pending review')
        yield from frames['capture']('narrow-long-settings-error')
        yield from f.tap('WHEELDOWNMOUSE', f.center(model.body))
        yield from f.tap('TAB')
        f.check(ui.app_state.model.object_count() == 0, 'settings scroll and Tab cannot publish geometry')
        yield from f.tap('ESC')
        yield from f.wait(lambda: not popup.is_open(), 'Escape dismisses narrow failed Save')
        owned.resize(1440, 1000)
        yield from f.wait(lambda: abs(window.width-1440) <= 2, 'restored after narrow settings')
        yield .3
        yield from f.tap('RET')
        f.check(ui._ai.get('action_pending') is pending, 'settings focus cannot authorize restored review')
    finally:
        owned.dispose()
    f.REPORT['settings_error_injection'] = 'on_save returns disposable repeated text; actual Save/resize/wheel/Tab/Escape'


def invalid_settings(ui, f, frames):
    from narwhal3d import ai_settings_popup as popup
    yield from f.send('ordinary', consent=True)
    pending = ui._ai['action_pending']
    old = ui.ai_assistant_state().extra['workflow']
    original = ui.ai_assistant_state().provider
    yield from f.tap('LEFTMOUSE', f.button('settings'))
    yield from f.wait(popup.is_open, 'actual settings opened with pending review')
    model = popup._model
    def control(key):
        for _ in range(35):
            rect, clip = model.controls[key], model.control_clip(key)
            if rect[1] >= clip[1] and rect[3] <= clip[3]:
                break
            yield from f.tap('WHEELDOWNMOUSE' if rect[1] < clip[1] else 'WHEELUPMOUSE', f.center(model.body))
        rect, clip = model.controls[key], model.control_clip(key)
        f.check(rect[1] >= clip[1] and rect[3] <= clip[3], key + ' fully visible')
        yield from f.tap('LEFTMOUSE', f.center(rect))
    yield from control('endpoint')
    yield from f.tap('A', ctrl=True)
    yield from f.tap('BACK_SPACE')
    yield from control('save')
    f.check(popup.is_open() and bool(model.error) and model.error_field == 'endpoint',
            'actual Save rejects empty endpoint with field feedback')
    f.check(old._handle != 0 and ui._ai.get('action_pending') is pending,
            'invalid draft Save cannot revoke or approve pending review')
    f.check(ui.ai_assistant_state().provider == original, 'invalid draft does not replace provider')
    yield from frames['capture']('settings-invalid-endpoint')
    yield from f.tap('ESC')
    yield from f.wait(lambda: not popup.is_open(), 'actual Escape dismisses invalid draft')
    f.check(ui.ai_assistant_state().provider == original, 'dismissed draft leaves provider unchanged')
    f.REPORT['settings_new_cell'] = 'empty endpoint actual Save; pending review preserved; no preference changes'


def editor_after_replies(ui, f, frames):
    f.PLAN_PROMPTS = (*f.PLAN_PROMPTS, 'continuation_probe')
    for index in range(6):
        yield from f.send('continuation_probe', consent=index == 0)
        yield from f.wait(lambda: ui.ai_assistant_state().status == 'connected', 'reply before editor stress ' + str(index))
    state = ui.ai_assistant_state()
    rx, ry = ui._ai['region']
    x, y = f.center(ui._ai['lay']['input'])
    yield from f.tap('LEFTMOUSE', (rx+x, ry+y))
    f.check(state.focus, 'actual click focuses existing native TextField')
    yield from f.tap('A', ctrl=True)
    for char in 'draft Ω text':
        f.event('A', 'PRESS', unicode=char)
        yield .02
        f.event('A', 'RELEASE')
    yield .2
    field = ui.ai_assistant.field_of(state)
    f.REPORT['editor_observed_text'] = field.text
    f.check(field.text == 'draft Ω text', 'actual Unicode editor events after six replies: ' + repr(field.text))
    yield from f.tap('END')
    yield from f.tap('LEFT_ARROW', shift=True)
    f.check(field.anchor is not None and abs(field.anchor-field.caret) == 1, 'actual shift-left selection uses native caret')
    yield from f.tap('BACK_SPACE')
    f.check(field.text == 'draft Ω tex', 'actual Backspace deletes selection only')
    yield from frames['capture']('editor-after-six-replies')
    yield from f.tap('ESC')
    f.check(ui.app_state.model.object_count() == 0, 'editor keys never publish geometry')
    f.REPORT['editor_actual'] = ['six local replies', 'focus', 'Unicode input', 'End', 'Shift+Left', 'Backspace', 'Escape']
    f.REPORT['editor_missing'] = ['clipboard isolation', 'long-session resize/scroll matrix']


def eviction_visible(ui, f, frames):
    f.PLAN_PROMPTS = (*f.PLAN_PROMPTS, 'continuation_probe')
    yield from f.send('continuation_probe', consent=True)
    yield from f.wait(lambda: ui.ai_assistant_state().status == 'connected', 'native reply before UI eviction')
    state = ui.ai_assistant_state()
    for index in range(101):
        state.add_message('assistant', 'UNVERIFIED display-only 🙂 row ' + str(index))
    ui._tag_redraw_bands()
    yield .4
    pixels = yield from frames['capture']('eviction-visible-notice')
    rect = ui._ai['lay']['transcript']
    rx, ry = ui._ai['region']
    sc = ui._ui_scale()
    visible, _offset = ui.ai_presentation.visible_items(ui._ai['chat_layout'], rect, ui._ai.get('chat_offset', 0))
    observations = []
    color = ui.design.rgb01(ui.design.TEXT_MUTED)
    for item, bubble, _top in visible:
        if item['message'].text != ui.ai_assistant.OMITTED_MESSAGES_NOTICE:
            continue
        for index, line in enumerate(item['lines']):
            baseline = ry + bubble[3] - item['padding'] - (index+1)*item['line_h'] + 3*sc
            x = rx + bubble[0] + item['padding']
            box = (x-1, baseline-3*sc, x+ui._text_width(line.rstrip(), 11*sc)+1, baseline+12*sc)
            count = int(frames['np'].count_nonzero(frames['near_color'](frames['roi'](pixels, box), color, .18)))
            f.check(count > max(2, len(line.strip())//2), 'eviction notice row has composed ink')
            f.check(box[1] >= ry+rect[1] and box[2] <= rx+rect[2], 'eviction notice row not clipped')
            observations.append({'text': line, 'ink_pixels': count, 'rect': box})
    f.check(bool(observations), 'eviction notice is visibly painted')
    f.check(len(state.messages) == 100, 'original host list bound unchanged')
    yield from f.send('continuation_probe')
    yield from f.wait(lambda: state.status == 'connected', 'real Send remains responsive after display eviction')
    f.check('UNVERIFIED display-only' not in str(f.REPORT['requests'][-1]), 'display injections never enter provider replay')
    f.REPORT['eviction_notice'] = observations
    f.REPORT['injected_display_rows'] = 101


def unsaved_no_dialog(ui, f):
    # Existing own-window fixture deliberately has NO_DIALOGS=1. Qualify safe
    # refusal here, never relabel this as actual native-dialog interaction.
    from narwhal3d import file_commands
    ui.app_state.start_command('-Units', 'in')
    f.check(ui.app_state.doc_modified(), 'unit change makes disposable document unsaved')
    yield from f.send('ordinary', consent=True)
    model = ui.app_state.model
    pending = ui._ai['action_pending']
    old = ui.ai_assistant_state().extra['workflow']
    ui.app_state.start_command('New')
    yield .3
    f.check(ui.app_state.model is model, 'unavailable discard dialog cannot replace unsaved model')
    f.check(ui._ai.get('action_pending') is pending and old._handle != 0,
            'refused New preserves review and native owner')
    f.check(ui.app_state.doc_modified(), 'refused New preserves unsaved changes')
    ok, _message = file_commands.write_document(str(f.OUT / 'saved-transition.narwhal'))
    f.check(ok, 'disposable document saved before accepted New')
    ui.app_state.start_command('New')
    yield from f.wait(lambda: ui.app_state.model is not model and old._handle == 0,
                      'accepted supported New after Save disposes old owner')
    f.REPORT['transition_gap'] = 'actual discard/cancel dialogs and scene switching not qualified; fixture disables dialogs'


def resized_focus(ui, f, capture):
    from test_ai_v8_x11 import OwnedWindow
    yield from f.send('long', consent=True)
    pending = ui._ai['action_pending']
    yield from f.tap('TAB')
    yield from f.tap('TAB')
    f.check(pending.get('keyboard_focus') == 'approve', 'approval focus painted before resize')
    window = ui.bpy.context.window_manager.windows[0]
    owned = OwnedWindow(window.width, window.height)
    try:
        owned.resize(800, 520)
        yield from f.wait(lambda: abs(window.width-800) <= 2, 'actual narrow resize')
        yield .4
        yield from capture('focused-review-narrow')
        f.check(not ui._ai['review_view']['fits'], 'long review refuses narrow layout')
        yield from f.tap('RET')
        f.check(ui._ai.get('action_pending') is pending and ui.app_state.model.object_count() == 0,
                'Return after shrink cannot activate formerly painted Approve')
        owned.resize(1440, 1000)
        yield from f.wait(lambda: abs(window.width-1440) <= 2, 'actual restored resize')
        yield .4
        yield from f.tap('RET')
        f.check(ui._ai.get('action_pending') is pending, 'restored size does not restore approval focus')
        yield from f.tap('ESC')
        yield from f.wait(lambda: ui.ai_assistant_state().status == 'connected', 'resized review rejects normally')
    finally:
        owned.dispose()


def open_during_request(ui, f, release):
    from narwhal3d import file_commands
    path = f.OUT / 'open while request pending.narwhal'
    ok, message = file_commands.write_document(str(path))
    f.check(ok, 'saved disposable Open target: ' + message)
    yield from f.send('ordinary', consent=True)
    yield from f.tap('ESC')
    yield from f.wait(lambda: ui.ai_assistant_state().status == 'connected', 'review rejected before blocked request')
    state = ui.ai_assistant_state()
    state.input_text = 'blocked_request'
    ui.ai_assistant.field_of(state).set_text('blocked_request')
    yield from f.tap('LEFTMOUSE', f.button('send'))
    yield from f.wait(lambda: f.REPORT.get('provider_request_waiting'), 'local HTTP blocked before Open')
    old_model = ui.app_state.model
    old = state.extra['workflow']
    handle, cancel = old._handle, old._cancel_event
    worker = ui._ai['transport_thread']
    ui.app_state.start_command('Open', '"' + str(path) + '"')
    yield from f.wait(lambda: ui.app_state.model is not old_model, 'supported Open replaced document during HTTP')
    yield from f.wait(lambda: old._handle == 0, 'Open disposes in-flight native owner')
    f.check(cancel.is_set(), 'Open logically cancels old request')
    f.check(f.NATIVE.rpc(handle, {'op':'state'}).get('code') == 'unknown_session', 'old Open request native owner absent')
    f.check(ui._ai.get('transport_output') is None and ui._ai.get('transport_thread') is None,
            'Open detaches old request queue and worker')
    yield from f.send('ordinary')
    replacement = ui._ai['action_pending']
    release.set()
    yield from f.wait(lambda: not worker.is_alive(), 'old HTTP physically completes after new-document review')
    f.check(ui._ai.get('action_pending') is replacement, 'late old-document reply cannot replace new review')
    f.check(not any('Late fixture reply' in row.text for row in state.messages), 'old-document prose never enters transcript')
    f.check(ui.app_state.model.object_count() == 0, 'late reply publishes no new-document geometry')
    f.REPORT['open_request_route'] = 'supported Open command; real local HTTP; no manual model setter or cancellation callback'
    f.REPORT['remote_execution_undone'] = False


def document_open(ui, f):
    from pathlib import Path
    from narwhal3d import file_commands
    path = Path(f.OUT) / 'disposable open.narwhal'
    ok, message = file_commands.write_document(str(path))
    f.check(ok, 'disposable empty document saved: ' + message)
    yield from f.send('ordinary', consent=True)
    old_model = ui.app_state.model
    pending = ui._ai['action_pending']
    old = ui.ai_assistant_state().extra['workflow']
    handle = old._handle
    ui.app_state.start_command('Open', '"' + str(path) + '"')
    yield from f.wait(lambda: ui.app_state.model is not old_model, 'supported Open command replaced document')
    yield from f.wait(lambda: old._handle == 0 and ui._ai.get('action_pending') is None,
                      'Open disposes pending review owner')
    f.check(f.NATIVE.rpc(handle, {'op': 'state'}).get('code') == 'unknown_session', 'Open old native owner absent')
    before = f.NATIVE.messages
    f.check(not ui._ai_decide_action_review(True, pending) and f.NATIVE.messages == before,
            'injected old approval after Open sends no native traffic')
    f.check(ui.app_state.model.object_count() == 0, 'Open receives no stale geometry')
    f.REPORT['document_route'] = 'actual Open command dispatcher; not keyboard/file-dialog evidence'


def units_change(ui, f, capture):
    yield from f.send('ordinary', consent=True)
    pending = ui._ai['action_pending']
    ui.app_state.start_command('-Units', 'in')
    yield from f.wait(lambda: ui.app_state.model.unit_system() == 'Inches', 'supported -Units command changed units')
    # The decision adapter must reject the earlier painted dimensions in old units.
    ui._ai_decide_action_review(True, pending)
    result = pending['batch']['results'][pending['index']]
    f.check(result is not None and result['is_error'], 'injected old-unit decision has canonical refusal')
    f.check(ui.app_state.model.object_count() == 0, 'old-unit decision publishes nothing')
    f.REPORT['old_units_result'] = result
    yield from f.fresh()
    yield from f.send('ordinary')
    view = ui._ai['review_view']
    f.check(any('Inches' in line for line in view['lines']), 'subsequent native review paints current Inches')
    yield from capture('units-inches-review')
    yield from f.tap('ESC')
    yield from f.wait(lambda: ui.ai_assistant_state().status == 'connected', 'actual Escape rejects new-unit review')
    f.REPORT['units_route'] = 'actual -Units command dispatcher; old approval callback injected'


def replaced_review(ui, f):
    yield from f.send('ordinary')
    pending = ui._ai['action_pending']
    session = ui.ai_assistant_state().extra['workflow']
    handle = session._handle
    yield from f.fresh()
    f.check(session._handle == 0, 'replacement disposes first review owner')
    f.check(f.NATIVE.rpc(handle, {'op': 'state'}).get('code') == 'unknown_session',
            'replacement removes first native session')
    yield from f.send('ordinary')
    current = ui._ai['action_pending']
    count = f.NATIVE.messages
    f.check(ui._ai_decide_action_review(True, pending) is False,
            'injected replaced-review approval refuses')
    f.check(f.NATIVE.messages == count and ui._ai['action_pending'] is current,
            'injected old callback cannot consume replacement review')
    f.REPORT['adverse_close'] = {'case': 'replaced_review', 'injected': ['old approval callback'],
                                'old_handle': handle, 'old_owner_disposed': True}


def replaced_request(ui, f, release):
    state = ui.ai_assistant_state()
    state.input_text = 'blocked_request'
    ui.ai_assistant.field_of(state).set_text('blocked_request')
    yield from f.tap('LEFTMOUSE', f.button('send'))
    yield from f.wait(lambda: f.REPORT.get('provider_request_waiting'), 'first local HTTP blocked')
    old = state.extra['workflow']
    handle = old._handle
    worker = ui._ai['transport_thread']
    cancel = old._cancel_event
    yield from f.fresh()
    f.check(old._handle == 0 and cancel.is_set(), 'New Chat disposes and logically cancels old request')
    f.check(f.NATIVE.rpc(handle, {'op': 'state'}).get('code') == 'unknown_session',
            'old request native handle absent')
    yield from f.send('ordinary')
    current = ui._ai['action_pending']
    release.set()
    yield from f.wait(lambda: not worker.is_alive(), 'old physical HTTP finishes after replacement review')
    f.check(ui._ai['action_pending'] is current, 'late old HTTP preserves replacement review')
    f.check(not any('Late fixture reply' in m.text for m in ui.ai_assistant_state().messages),
            'late old HTTP cannot publish prose to replacement chat')
    yield from f.fresh()
    release.clear()
    f.REPORT['provider_request_waiting'] = False
    state = ui.ai_assistant_state()
    state.input_text = 'blocked_request'
    ui.ai_assistant.field_of(state).set_text('blocked_request')
    yield from f.tap('LEFTMOUSE', f.button('send'))
    yield from f.wait(lambda: f.REPORT.get('provider_request_waiting'), 'replacement HTTP blocked before actual close')
    f.REPORT['adverse_close'] = {'case': 'replaced_request', 'injected': [],
                                'old_handle': handle, 'old_worker_finished': True,
                                'remote_execution_undone': False}
