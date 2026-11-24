"""Actual candidate-owned GUI/WM_DELETE/keyboard qualification, never the desktop.

Python is unavoidable bpy integration. Rust owns isolated Xvfb/process lifetimes.
Only deterministic fixture models and private preference/recovery directories are used.
"""
import ctypes as C
import ctypes.util
import inspect
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'spike'), str(ROOT / 'launch')]
OUT = Path(os.environ['NARWHAL_GUI_CASE'])
MODE = os.environ['NARWHAL_GUI_MODE']
MUTATION = os.environ.get('NARWHAL_GUI_MUTATION', 'none')
state = dict(started=False, finished=False, canceled=False, pending=False,
             edited=False, esc_sent=False, closing=False, ticks=0, max_gap=0.0,
             answer='yes', cancel_dialog=MODE in ('cancel-dialog', 'updater-cancel'))
started = time.monotonic()
last = started
sq = fc = queue = app = ui = boot = None
TARGET = OUT / 'part.narwhal'
SECOND = OUT / 'queued.narwhal'
REQUEST = OUT / 'welcome.json'
RESULT = OUT / 'result.json'


def report(ok, exc=None):
    data = dict(ok=ok, mode=MODE, mutation=MUTATION, pid=os.getpid(),
                binary=bpy.app.binary_path, display=os.environ.get('DISPLAY'),
                background=bpy.app.background, state=state,
                elapsed=time.monotonic() - started,
                assertion_failure=isinstance(exc, AssertionError))
    if exc is not None:
        data.update(error=repr(exc), traceback=traceback.format_exc())
    RESULT.write_text(json.dumps(data, indent=2) + '\n')


def fail(exc):
    report(False, exc)
    if hasattr(bpy.context.window_manager, 'narwhal_defer_close'):
        bpy.context.window_manager.narwhal_defer_close = False
    bpy.context.preferences.view.use_save_prompt = False
    bpy.ops.wm.quit_blender()


class ClientData(C.Union):
    _fields_ = [('b', C.c_char * 20), ('s', C.c_short * 10), ('l', C.c_long * 5)]


class ClientMessage(C.Structure):
    _fields_ = [('type', C.c_int), ('serial', C.c_ulong), ('send_event', C.c_int),
                ('display', C.c_void_p), ('window', C.c_ulong), ('message_type', C.c_ulong),
                ('format', C.c_int), ('data', ClientData)]


class XEvent(C.Union):
    _fields_ = [('message', ClientMessage), ('pad', C.c_long * 24)]


def x11():
    # Connect ONLY to the isolated display supplied by the Rust supervisor.
    lib = C.CDLL(ctypes.util.find_library('X11'))
    lib.XOpenDisplay.argtypes, lib.XOpenDisplay.restype = [C.c_char_p], C.c_void_p
    lib.XDefaultRootWindow.argtypes, lib.XDefaultRootWindow.restype = [C.c_void_p], C.c_ulong
    lib.XInternAtom.argtypes, lib.XInternAtom.restype = [C.c_void_p, C.c_char_p, C.c_int], C.c_ulong
    lib.XQueryTree.argtypes = [C.c_void_p, C.c_ulong, C.POINTER(C.c_ulong), C.POINTER(C.c_ulong),
                              C.POINTER(C.POINTER(C.c_ulong)), C.POINTER(C.c_uint)]
    lib.XGetWindowProperty.argtypes = [C.c_void_p, C.c_ulong, C.c_ulong, C.c_long, C.c_long,
        C.c_int, C.c_ulong, C.POINTER(C.c_ulong), C.POINTER(C.c_int), C.POINTER(C.c_ulong),
        C.POINTER(C.c_ulong), C.POINTER(C.POINTER(C.c_ubyte))]
    lib.XSendEvent.argtypes = [C.c_void_p, C.c_ulong, C.c_int, C.c_long, C.POINTER(XEvent)]
    lib.XFree.argtypes = [C.c_void_p]
    lib.XFlush.argtypes = [C.c_void_p]
    lib.XCloseDisplay.argtypes = [C.c_void_p]
    lib.XSetInputFocus.argtypes = [C.c_void_p, C.c_ulong, C.c_int, C.c_ulong]
    lib.XKeysymToKeycode.argtypes, lib.XKeysymToKeycode.restype = [C.c_void_p, C.c_ulong], C.c_ubyte
    display = lib.XOpenDisplay(None)
    assert display, 'isolated X display unavailable'
    root = lib.XDefaultRootWindow(display)
    root_ret, parent = C.c_ulong(), C.c_ulong()
    children, count = C.POINTER(C.c_ulong)(), C.c_uint()
    assert lib.XQueryTree(display, root, C.byref(root_ret), C.byref(parent), C.byref(children), C.byref(count))
    atom = lib.XInternAtom(display, b'_NET_WM_PID', False)
    found = []
    for i in range(count.value):
        actual, fmt, items, after, data = C.c_ulong(), C.c_int(), C.c_ulong(), C.c_ulong(), C.POINTER(C.c_ubyte)()
        lib.XGetWindowProperty(display, children[i], atom, 0, 1, False, 6,
                              C.byref(actual), C.byref(fmt), C.byref(items), C.byref(after), C.byref(data))
        if data:
            if fmt.value == 32 and items.value == 1 and C.cast(data, C.POINTER(C.c_ulong))[0] == os.getpid():
                found.append(children[i])
            lib.XFree(data)
    lib.XFree(children)
    assert len(found) == 1, f'expected exactly one window belonging to this test pid, got {found}'
    return lib, display, found[0]


def close_window():
    lib, display, window = x11()
    event = XEvent()
    event.message.type, event.message.send_event = 33, True
    event.message.display, event.message.window = display, window
    event.message.message_type = lib.XInternAtom(display, b'WM_PROTOCOLS', False)
    event.message.format = 32
    event.message.data.l[0] = lib.XInternAtom(display, b'WM_DELETE_WINDOW', False)
    assert lib.XSendEvent(display, window, False, 0, C.byref(event))
    lib.XFlush(display)
    lib.XCloseDisplay(display)


def escape():
    lib, display, window = x11()
    xt = C.CDLL(ctypes.util.find_library('Xtst'))
    xt.XTestFakeKeyEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]
    lib.XSetInputFocus(display, window, 2, 0)
    keycode = lib.XKeysymToKeycode(display, 0xff1b)
    assert xt.XTestFakeKeyEvent(display, keycode, True, 0)
    assert xt.XTestFakeKeyEvent(display, keycode, False, 0)
    lib.XFlush(display)
    lib.XCloseDisplay(display)


def ask(*_args):
    if state['cancel_dialog']:
        state['cancel_dialog'] = False
        state['dialog_canceled'] = True
        return fc.filedialog.CANCEL, None
    return fc.filedialog.OK, state['answer']


def on_exit(*_args):
    if RESULT.exists():
        return  # An earlier timer failure must never be overwritten by cleanup.
    try:
        assert state['started'] and state['finished'], 'native exit bypassed pending approval/Cancel checks'
        if MODE != 'default-off':
            assert not queue.busy(), 'native exit interrupted user Save'
            assert state['ticks'] > 30 and state['max_gap'] < 1.0, state
            if MODE in ('save', 'updater-save', 'pending-save', 'updater-escape'):
                assert TARGET.is_file(), 'native close bypassed the requested user Save'
                assert sq.objects_in(TARGET) == 3
                assert not app.doc_modified()
                assert state['pending'], 'never observed background Save'
                if MODE == 'pending-save':
                    assert sq.objects_in(SECOND) == 3
            else:
                assert state['canceled'], 'Cancel was not observed before cleanup'
                assert app.doc_modified(), 'recovery falsely marked fixture saved'
                recovery = queue.recovery_for_current_state()
                assert recovery and sq.objects_in(recovery) == (4 if MODE == 'latest-edit' else 3)
                if MODE == 'latest-edit':
                    assert sq.objects_in(TARGET) == 3
            if MODE in ('pending-save', 'updater-escape'):
                assert state['canceled'], 'Escape did not cancel Exit'
            if MODE.startswith('updater-'):
                approved = REQUEST.with_suffix('.restart-approved').exists()
                declined = REQUEST.with_suffix('.restart-declined').exists()
                assert approved == (MODE == 'updater-save')
                assert declined == (MODE != 'updater-save')
            assert not os.path.exists(fc._session_marker()), 'successful protected exit left session marker'
        report(True)
    except Exception as exc:
        report(False, exc)


def update_request():
    os.environ['NARWHAL3D_WELCOME_REQUEST'] = str(REQUEST)
    REQUEST.with_suffix('.restart').touch()
    if MUTATION == 'premature-update-approval':
        request = fc.request_exit
        def premature(**kwargs):
            kwargs['on_ready']()
            return request(**kwargs)
        fc.request_exit = premature
    boot._update_exit_request()  # Actual bridge module, no fixture implementation.
    assert not REQUEST.with_suffix('.restart-approved').exists(), 'updater approved before Save completion'


def begin():
    global last
    state['started'] = True
    try:
        import gpu
        state['gpu_backend'] = gpu.platform.backend_type_get()
        state['gpu_renderer'] = gpu.platform.renderer_get()
        bpy.ops.screen.screenshot(filepath=str(OUT / 'window.png'))
    except Exception as exc:
        state['screenshot_note'] = repr(exc)
    last = time.monotonic()
    if MODE == 'default-off':
        assert not bpy.context.window_manager.narwhal_defer_close
        state['finished'] = True
        close_window()
        return
    if MUTATION == 'drop-latest-guard':
        assert fc is sys.modules['narwhal3d.file_commands'], 'mutation targeted a stale module'
        source = inspect.getsource(fc._exit_tick)
        old = 'if _exit_guard is not None and save_queue._snapshot(app) != _exit_guard:'
        assert source.count(old) == 1
        fc._qualification_exit_tick_calls = 0
        mutated = source.replace(old, 'if False:').replace(
            '    app = _app()',
            '    global _qualification_exit_tick_calls\n    _qualification_exit_tick_calls += 1\n    app = _app()', 1)
        exec(compile(mutated, '<private GUI mutation>', 'exec'), fc.__dict__)

    if MUTATION == 'bypass-native':
        bpy.context.window_manager.narwhal_defer_close = False
    if MODE in ('save', 'updater-save'):
        state['finished'] = True
    if MODE == 'pending-save':
        assert fc.write_document(str(TARGET), background=True)[0]
        assert fc.write_document(str(SECOND), background=True)[0]
    if MODE.startswith('updater-'):
        update_request()
    else:
        close_window()


def monitor():
    global last
    try:
        now = time.monotonic()
        assert now - started < 120, 'owned GUI qualification timed out'
        if not state['started']:
            if MODE == 'default-off':
                begin()
            elif (ui._exit_installed and not ui._viewports_in_progress
                  and len([a for a in bpy.context.screen.areas if a.type == 'VIEW_3D']) >= 4):
                if 'warm_until' not in state:
                    sq.SaveQueueTests.two_solids(None)
                    app.doc_path = str(TARGET)
                    state['warm_until'] = now + 1.5
                    # Install the result observer AFTER the real recovery handler.
                    bpy.app.handlers.exit_pre.append(on_exit)
                elif now >= state['warm_until']:
                    assert bpy.context.window_manager.narwhal_defer_close
                    begin()
            return .02
        state['ticks'] += 1
        state['mutated_tick_calls'] = getattr(fc, '_qualification_exit_tick_calls', None)
        state['max_gap'] = max(state['max_gap'], now - last)
        last = now
        if MODE == 'default-off':
            return .02
        if queue.busy():
            state['pending'] = True
            if MODE.startswith('updater-') and not state['closing']:
                assert not REQUEST.with_suffix('.restart-approved').exists(), 'updater approved while Save was running'
        if MODE == 'latest-edit' and fc._exit_guard is not None and queue.busy() and not state['edited']:
            sq.draw_line(12)
            state['edited'] = True
        if MODE in ('pending-save', 'updater-escape'):
            if fc._exit_polling and queue.busy() and not state['esc_sent']:
                if MODE == 'pending-save' or fc._exit_guard is not None:
                    escape()
                    state['esc_sent'] = True
            if state['esc_sent'] and not fc._exit_polling and queue.busy():
                state['canceled'] = True
            if state['canceled'] and not queue.busy() and not state['closing']:
                assert not app.doc_modified()
                state['finished'] = state['closing'] = True
                close_window()
        elif MODE in ('cancel-dialog', 'updater-cancel', 'latest-edit'):
            canceled = state.get('dialog_canceled') or (state['edited'] and state.get('cancel_calls', 0) > 0 and not fc._exit_polling)
            if canceled and not fc._exit_polling and not queue.busy() and not state['closing']:
                assert app.doc_modified() and os.path.exists(fc._session_marker())
                assert bpy.context.window_manager.narwhal_defer_close
                state['canceled'] = True
                state['answer'] = 'no'  # Cleanup also qualifies latest-state validated recovery.
                state['finished'] = state['closing'] = True
                close_window()
        return .02
    except Exception as exc:
        fail(exc)
        return None


try:
    manifest = json.loads(Path(os.environ['NARWHAL_GUI_INPUT_MANIFEST']).read_text())
    state['application_hashes'] = {}
    for relative, digest in manifest['application_sources'].items():
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        assert actual == digest, f'application source changed before qualification: {relative}'
        state['application_hashes'][relative] = actual
    state['host_binary_sha256'] = hashlib.sha256(Path(bpy.app.binary_path).read_bytes()).hexdigest()
    state['application_core_sha256'] = hashlib.sha256(Path(os.environ['NARWHAL_APPLICATION_CORE']).read_bytes()).hexdigest()
    assert state['host_binary_sha256'] == manifest['host_binary_sha256']
    assert state['application_core_sha256'] == manifest['application_core_sha256']
    assert not bpy.app.background, 'this qualification requires an actual owned GUI'
    assert os.environ.get('NARWHAL_GUI_ISOLATED') == '1'
    assert hasattr(bpy.context.window_manager, 'narwhal_defer_close'), 'candidate lacks native preclose RNA'
    assert not bpy.context.window_manager.narwhal_defer_close, 'native guard must default OFF'
    bpy.context.preferences.view.use_save_prompt = False
    if MODE == 'default-off':
        bpy.app.handlers.exit_pre.append(on_exit)
    else:
        import test_save_queue as sq
        from narwhal3d import file_commands as fc, save_queue as queue, ui
        from narwhal3d.state import app_state as app
        import bootstrap as boot
        fc.filedialog.ask_yes_no_cancel = ask
        original_cancel = fc.cancel_exit
        def observed_cancel(*args, **kwargs):
            state['cancel_calls'] = state.get('cancel_calls', 0) + 1
            return original_cancel(*args, **kwargs)
        fc.cancel_exit = observed_cancel
        real_validate = sq.doc._validate_in_worker
        def slow_validate(*args, **kwargs):
            time.sleep(2.0)
            return real_validate(*args, **kwargs)
        sq.doc._validate_in_worker = slow_validate
        boot.main()
    bpy.app.timers.register(monitor, first_interval=.25, persistent=True)
except Exception as exc:
    fail(exc)
