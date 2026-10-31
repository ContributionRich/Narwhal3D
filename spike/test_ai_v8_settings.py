"""Round07 actual settings/consent event scenarios, used only by owned GUI fixture.

No policy implementation: Python supplies Blender events and inspects existing
popup controls. Synthetic initial provider configuration uses loopback only; all
changes qualified here go through the real settings Save/Forget controls.
"""


def scenarios(ui, f, frames, release_response):
    from narwhal3d import ai_settings_popup as popup

    def open_settings():
        yield from f.tap('LEFTMOUSE', f.button('settings'))
        yield from f.wait(popup.is_open, 'actual settings popup opened')

    def control(key):
        model = popup._model
        for _ in range(35):
            rect, clip = model.controls[key], model.control_clip(key)
            if rect[1] >= clip[1] and rect[3] <= clip[3]:
                break
            yield from f.tap('WHEELDOWNMOUSE' if rect[1] < clip[1] else 'WHEELUPMOUSE', f.center(model.body))
        rect, clip = model.controls[key], model.control_clip(key)
        f.check(rect[1] >= clip[1] and rect[3] <= clip[3], 'actual ' + key + ' control fully visible')
        yield from f.tap('LEFTMOUSE', f.center(rect))

    def edit(key, text):
        yield from control(key)
        yield from f.tap('A', ctrl=True)
        # Real own-window text events, without reading/changing the desktop clipboard.
        for char in text:
            f.event('A', 'PRESS', unicode=char)
            yield .01
            f.event('A', 'RELEASE')
        yield .1
        f.check(popup._model.fields[key].text == text, key + ' changed by actual text events')

    def saved(old, label):
        yield from control('save')
        yield from f.wait(lambda: not popup.is_open(), label + ': actual Save closed popup')
        f.check(old._handle == 0 and ui._ai.get('action_pending') is None,
                label + ': saved settings retire native owner and visible review')

    for key in ('model', 'endpoint', 'revoke'):
        yield from f.send('ordinary', consent=key == 'model')
        old = ui.ai_assistant_state().extra['workflow']
        before = ui.app_state.model.object_count()
        yield from open_settings()
        if key == 'revoke':
            yield from control('revoke')
            f.check(ui.preferences.get('ai_metadata_permission') == {}, 'actual Forget revokes remembered recipient permission')
            f.check(old._handle == 0 and ui._ai.get('action_pending') is None, 'actual Forget retires pending native review')
            yield from frames['capture']('settings-forgot-permission')
            yield from f.tap('ESC')
            yield from f.wait(lambda: not popup.is_open(), 'settings dismissal after Forget')
        else:
            value = ('review07-changed' if key == 'model' else
                     'http://127.0.0.1:' + str(f.SERVER.server_port) + '/alternate')
            yield from edit(key, value)
            f.check(old._handle != 0 and ui._ai.get('action_pending') is not None,
                    key + ': unsaved draft is not scope change or approval')
            yield from frames['capture']('settings-draft-' + key)
            yield from saved(old, key)
            f.check(getattr(ui.ai_assistant_state().provider, key) == value, key + ': current recipient uses saved value')
        f.check(ui.app_state.model.object_count() == before, key + ': settings never publish geometry')
        yield from f.fresh()

    # Regrant through the actual consent dialog; reject review, then send real HTTP
    # using that same current recipient grant. Do not inject a consent setter.
    yield from f.send('ordinary')
    yield from f.tap('ESC')
    yield from f.wait(lambda: ui.ai_assistant_state().status == 'connected', 'fresh consent review rejected')
    state = ui.ai_assistant_state()
    state.input_text = 'blocked_request'; ui.ai_assistant.field_of(state).set_text('blocked_request')
    yield from f.tap('LEFTMOUSE', f.button('send'))
    yield from f.wait(lambda: f.REPORT.get('provider_request_waiting'), 'real local HTTP pending before settings edit')
    old = state.extra['workflow']; worker = ui._ai['transport_thread']
    yield from open_settings()
    yield from edit('model', 'review07-late-detached')
    yield from saved(old, 'pending HTTP')
    release_response.set()
    yield from f.wait(lambda: not worker.is_alive(), 'detached physical worker completed after settings save')
    f.check(ui._ai.get('transport_output') is None and 'workflow' not in state.extra,
            'late HTTP completion did not reattach old native owner/queue')
    f.check(not any('Late fixture reply' in m.text for m in state.messages),
            'late provider prose not added to new recipient transcript')

    yield from f.send('ordinary')
    old = ui.ai_assistant_state().extra['workflow']
    yield from open_settings()
    yield from control('provider')
    model = popup._model
    index = [key for key, _label in model.choices()].index('anthropic')
    rect = next(rect for i, rect in model.menu_rows if i == index)
    yield from f.tap('LEFTMOUSE', f.center(rect))
    f.check(model.provider_id == 'anthropic', 'actual provider dropdown selected Anthropic')
    yield from saved(old, 'provider')
    f.check(ui.ai_assistant_state().provider.provider_id == 'anthropic', 'actual provider Save applied')
    f.check(ui.app_state.model.object_count() == 0, 'settings/consent scenarios create no objects')
    f.REPORT['settings_actual_controls'] = ['model', 'endpoint', 'revoke', 'late_http_model', 'provider']
