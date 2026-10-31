"""Owned-window document and repeated assistant lifecycle event probes.

Python is Blender event/OS-observation glue only. No direct model/document setter,
revision invention, producer edits, or application shutdown/layout changes.
"""
import os
from pathlib import Path


def document_new(ui, f):
    yield from f.send('ordinary', consent=True)
    old_model = ui.app_state.model
    pending = ui._ai['action_pending']
    session = ui.ai_assistant_state().extra['workflow']
    f.check(not ui.app_state.doc_modified(), 'File New fixture is unmodified; no discard dialog bypass')
    area = ui.active_pane_area()
    yield from f.tap('LEFTMOUSE', (area.x + area.width*.4, area.y + area.height*.4))
    f.event('LEFT_CTRL'); yield .05
    yield from f.tap('N', ctrl=True)
    f.event('LEFT_CTRL', 'RELEASE')
    yield from f.wait(lambda: ui.app_state.model is not old_model, 'actual Ctrl+N replaced the document model')
    yield from f.wait(lambda: session._handle == 0 and ui._ai.get('action_pending') is None,
                      'document transition retired native owner and pending review')
    count = f.NATIVE.messages
    f.check(ui._ai_decide_action_review(True, pending) is False and f.NATIVE.messages == count,
            'injected old callback after actual New produces no native traffic')
    f.check(ui.app_state.model.object_count() == 0, 'new document receives no stale publication')
    f.REPORT['actual_document_transitions'] = ['Ctrl+N: unmodified document']
    f.REPORT['unqualified_document_transitions'] = ['Open', 'units', 'scene', 'unsaved discard dialog']


def stress(ui, f):
    samples = []
    for index in range(24):
        mode = ('reject', 'cancel', 'new')[index % 3]
        yield from f.send('ordinary', consent=index == 0)
        pending = ui._ai['action_pending']
        session = ui.ai_assistant_state().extra['workflow']
        handle = session._handle
        if mode == 'reject':
            yield from f.tap('LEFTMOUSE', f.review_button('reject'))
            yield from f.wait(lambda: ui.ai_assistant_state().status == 'connected', 'stress canonical rejection continued')
            yield from f.fresh()
        elif mode == 'cancel':
            yield from f.tap('LEFTMOUSE', f.button('send'))
        else:
            yield from f.fresh()
        yield from f.wait(lambda: session._handle == 0, 'stress native session disposed ' + str(index))
        f.check(f.NATIVE.rpc(handle, {'op': 'state'}).get('code') == 'unknown_session', 'stress native handle actually absent')
        f.check(ui._ai.get('action_pending') is None and ui._ai.get('review_batch') is None,
                'stress no pending review/batch')
        yield from f.wait(lambda: not ui.bpy.app.timers.is_registered(ui._ai_poll_transport),
                          'stress polling timer removed ' + str(index))
        f.check(ui._ai.get('transport_output') is None and ui._ai.get('transport_thread') is None,
                'stress no attached transport queue/worker')
        before = f.NATIVE.messages
        f.check(ui._ai_decide_action_review(True, pending) is False and f.NATIVE.messages == before,
                'injected stale stress callback has zero native traffic')
        f.check(ui.app_state.model.object_count() == 0, 'stress never approves geometry')
        status = Path(f'/proc/{os.getpid()}/status').read_text()
        samples.append(int(next(line.split()[1] for line in status.splitlines() if line.startswith('VmRSS:'))))
    f.REPORT['gui_stress'] = dict(cycles=24, process_restarts=0, rss_kib=samples,
                                 warmup_cycles=8, plateau_allowance_kib=65536,
                                 actual_events=['Send', 'Reject', 'Cancel', 'New Chat'],
                                 injected=['stale pending callback'])
    f.check(max(samples[8:])-min(samples[8:]) <= 65536, 'same-GUI-process RSS plateau within declared64MiB after8 warmup cycles')
