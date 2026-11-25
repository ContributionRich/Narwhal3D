"""Actual host save/Exit integration; Python is required for Blender/OCCT callbacks.
Policy tests live in Rust. Never closes a real window: the final quit is a recorder.
Run using the same Blender environment as test_save_queue.py.
"""
import ast
import json
import inspect
import threading
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_save_queue as sq

fc, queue, doc, app = sq.file_commands, sq.save_queue, sq.doc, sq.app_state
NATIVE_UI_MUTATION = None


class ResponsiveExit(unittest.TestCase):
    setUp_base = sq.SaveQueueTests.setUp
    two_solids = sq.SaveQueueTests.two_solids
    idle = sq.SaveQueueTests.idle

    def setUp(self):
        self.setUp_base()
        self.quits = []
        self.host_quit = lambda: self.quits.append(True)
        self.patches = [patch.object(fc, '_exit_scheduler', self.pump.schedule),
                        patch.object(fc, 'quitter', self.host_quit),
                        patch.object(fc.filedialog, 'ask_yes_no_cancel', return_value=(fc.filedialog.OK, 'yes'))]
        for p in self.patches:
            p.start()
        self.two_solids()
        self.target = os.path.join(self.tmp, 'exit.narwhal')
        app.doc_path = self.target
        fc.begin_session()

    def tearDown(self):
        if fc._exit_polling:
            fc.cancel_exit()
        queue.drain(120)
        for p in reversed(self.patches):
            p.stop()
        sq.SaveQueueTests.tearDown(self)

    def start(self):
        start = time.monotonic()
        fc.ExitCmd().begin(app.ctx)
        self.assertLess(time.monotonic() - start, .5, 'Exit blocked before returning to event loop')

    def finish(self):
        self.pump.run(lambda: not fc._exit_polling, timeout=120)

    def test_save_exit_pumps_real_validation_and_skips_duplicate_exit_recovery(self):
        with patch.object(doc, '_validate_in_worker', side_effect=sq.slow_validate([])):
            self.start()
            self.finish()
        self.assertEqual(self.quits, [True])
        self.assertEqual(sq.objects_in(self.target), 3)
        self.assertFalse(app.doc_modified())
        self.assertGreater(self.pump.ticks, 50)
        self.assertLess(self.pump.max_gap, 1.0)
        print('EXIT_PROFILE', json.dumps(dict(ticks=self.pump.ticks, max_gap=self.pump.max_gap)))

    def test_wait_for_running_and_queued_user_save_before_asking_latest_edits(self):
        second = os.path.join(self.tmp, 'second.narwhal')
        with patch.object(doc, '_validate_in_worker', side_effect=sq.slow_validate([])):
            fc.write_document(self.target, background=True)
            fc.write_document(second, background=True)
            self.start()
            fc._exit_tick()
            self.assertEqual(self.quits, [])
            self.finish()
        self.assertEqual(self.quits, [True])
        self.assertTrue(os.path.isfile(self.target) and os.path.isfile(second))
        self.assertEqual(app.doc_path, second)

    def test_explicit_no_has_no_save_recovery_measurement_or_drain(self):
        handler = sq.SaveQueueTests.exit_handler(self)
        with patch.object(fc.filedialog, 'ask_yes_no_cancel', return_value=(fc.filedialog.OK, 'no')), \
                patch.object(doc, '_stage', side_effect=AssertionError('No reached staging')), \
                patch.object(doc, '_shape_record', side_effect=AssertionError('No measured geometry')), \
                patch.object(fc, 'write_recovery', side_effect=AssertionError('No wrote recovery')), \
                patch.object(fc, 'finish_saves', side_effect=AssertionError('No drained Save')), \
                patch.object(queue, 'drain', side_effect=AssertionError('No joined worker')):
            self.start()
            self.assertFalse(fc._exit_polling, 'No should commit in the same event turn')
            handler()
        self.assertEqual(self.quits, [True])
        self.assertTrue(app.doc_modified(), 'discard must not pretend the document was saved')
        self.assertFalse(os.path.exists(self.target))
        self.assertFalse(os.path.exists(fc._session_marker()))
        self.assertIsNone(queue.recovery_for_current_state())


    def test_clean_exit_does_not_prompt_stage_recover_or_drain(self):
        app.mark_saved()
        handler = sq.SaveQueueTests.exit_handler(self)
        with patch.object(fc.filedialog, 'ask_yes_no_cancel', side_effect=AssertionError('clean Exit prompted')), \
                patch.object(doc, '_stage', side_effect=AssertionError('clean Exit staged')), \
                patch.object(doc, '_shape_record', side_effect=AssertionError('clean Exit measured')), \
                patch.object(fc, 'write_recovery', side_effect=AssertionError('clean Exit recovered')), \
                patch.object(fc, 'finish_saves', side_effect=AssertionError('clean Exit drained')):
            self.start()
            handler()
        self.assertEqual(self.quits, [True])
        self.assertFalse(os.path.exists(fc._session_marker()))

    def test_pending_save_deadline_cancels_only_exit_responsively(self):
        with patch.object(doc, '_validate_in_worker', side_effect=sq.slow_validate([])):
            fc.write_document(self.target, background=True)
            self.start()
            fc._exit_wait_started = time.monotonic() - 30.1
            started = time.monotonic()
            fc._exit_tick()
            self.assertLess(time.monotonic() - started, .2)
            self.assertFalse(fc._exit_polling)
            self.assertTrue(queue.busy(), 'Exit deadline must not interrupt user Save')
            self.assertEqual(self.quits, [])
            self.pump.run(self.idle)
        self.assertEqual(sq.objects_in(self.target), 3)
        self.assertTrue(os.path.exists(fc._session_marker()))

    def test_updater_no_approves_discard_without_saving_or_recovery(self):
        with patch.object(fc.filedialog, 'ask_yes_no_cancel', return_value=(fc.filedialog.OK, 'no')), \
                patch.object(doc, '_stage', side_effect=AssertionError('updater No staged')), \
                patch.object(fc, 'write_recovery', side_effect=AssertionError('updater No recovered')):
            request = self.updater()
        self.assertTrue(request.with_suffix('.restart-approved').exists())
        self.assertFalse(request.with_suffix('.restart-declined').exists())
        self.assertTrue(app.doc_modified())
        self.assertEqual(self.quits, [True])

    def native_ui(self):
        from types import SimpleNamespace
        tree = ast.parse((sq.ROOT / 'narwhal3d/ui.py').read_text())
        names = {'_preclose_tick', '_quit_after_approval', '_uninstall_preclose', '_autosave_tick'}
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        wm = SimpleNamespace(narwhal_defer_close=True, narwhal_close_requested=True)
        timers = set()
        bpy = SimpleNamespace(context=SimpleNamespace(window_manager=wm),
            ops=SimpleNamespace(wm=SimpleNamespace(quit_blender=self.host_quit)),
            app=SimpleNamespace(timers=SimpleNamespace(is_registered=timers.__contains__, unregister=timers.remove)))
        ns = dict(__package__='narwhal3d', bpy=bpy, app_state=app, os=os,
                  _autosave_last=[__import__('datetime').datetime.now()], redraw_cmdbar=lambda: None)
        source = ast.unparse(ast.Module(body=nodes, type_ignores=[]))
        if NATIVE_UI_MUTATION:
            old, new = NATIVE_UI_MUTATION
            self.assertEqual(source.count(old), 1)
            source = source.replace(old, new)
        exec(compile(source, '<actual preclose host>', 'exec'), ns)
        return ns, wm, timers

    def test_autosave_cannot_write_after_intentional_discard(self):
        from narwhal3d import autosave
        ns, _, _ = self.native_ui()
        fc._exit_commit = (app.model, queue._snapshot(app))
        with patch.object(autosave, 'due', return_value=True), \
                patch.object(fc, 'write_recovery', return_value=False) as recover:
            self.assertEqual(ns['_autosave_tick'](), 60.0)
            recover.assert_not_called()
            fc._exit_commit = None
            ns['_autosave_tick']()
            recover.assert_called_once_with(background=True)

    def test_native_mailbox_routes_to_coordinator_and_cancel_retains_guard(self):
        ns, wm, _ = self.native_ui()
        ns['_preclose_tick']()
        self.assertFalse(wm.narwhal_close_requested)
        self.assertTrue(fc._exit_polling)
        self.assertEqual(self.quits, [])
        fc.cancel_exit()
        self.assertTrue(wm.narwhal_defer_close)
        self.assertEqual(self.quits, [])

    def test_native_approval_bypasses_guard_only_during_native_quit(self):
        ns, wm, _ = self.native_ui()
        calls = []
        def quit_native():
            calls.append(wm.narwhal_defer_close)
            raise RuntimeError('deliberate native quit failure')
        ns['bpy'].ops.wm.quit_blender = quit_native
        fc._exit_commit = (app.model, queue._snapshot(app))
        with self.assertRaises(RuntimeError):
            ns['_quit_after_approval']()
        self.assertEqual(calls, [False])
        self.assertTrue(wm.narwhal_defer_close)

    def test_unregister_cancels_pending_exit_and_removes_native_guard(self):
        ns, wm, timers = self.native_ui()
        ns['_preclose_tick']()
        timers.add(ns['_preclose_tick'])
        ns['_uninstall_preclose']()
        self.assertFalse(fc._exit_polling)
        self.assertFalse(wm.narwhal_defer_close)
        self.assertEqual(timers, set())
        self.assertFalse(ns['_exit_installed'])

    def updater(self):
        tree = ast.parse((sq.ROOT / 'launch/bootstrap.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == '_update_exit_request')
        ns = dict(os=os)
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<actual updater bridge>', 'exec'), ns)
        request = Path(self.tmp) / 'welcome.json'
        request.with_suffix('.restart').touch()
        with patch.dict(os.environ, NARWHAL3D_WELCOME_REQUEST=str(request)):
            start = time.monotonic()
            ns['_update_exit_request']()
            self.assertLess(time.monotonic() - start, .5)
        return request

    def test_updater_waits_for_durable_save_before_approval(self):
        with patch.object(doc, '_validate_in_worker', side_effect=sq.slow_validate([])):
            request = self.updater()
            self.assertTrue(fc._exit_polling)
            self.assertFalse(request.with_suffix('.restart-approved').exists())
            self.assertFalse(request.with_suffix('.restart-declined').exists())
            self.finish()
        self.assertTrue(request.with_suffix('.restart-approved').exists())
        self.assertFalse(request.with_suffix('.restart-declined').exists())
        self.assertEqual(self.quits, [True])
        self.assertIs(fc.quitter, self.host_quit)
        self.assertFalse(app.doc_modified())
        self.assertEqual(sq.objects_in(self.target), 3)
        self.assertGreater(self.pump.ticks, 50)

    def test_updater_cancel_and_save_failure_decline_without_approval(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                request = Path(self.tmp) / 'welcome.json'
                for suffix in ('.restart-approved', '.restart-declined'):
                    request.with_suffix(suffix).unlink(missing_ok=True)
                with patch.object(fc.filedialog, 'ask_yes_no_cancel', return_value=(
                        fc.filedialog.OK if failure else fc.filedialog.CANCEL, 'yes')):
                    with patch.object(doc, '_validate_and_pack', side_effect=OSError('failed save')):
                        self.updater()
                        self.finish()
                self.assertTrue(request.with_suffix('.restart-declined').exists())
                self.assertFalse(request.with_suffix('.restart-approved').exists())
                self.assertEqual(self.quits, [])
                self.assertTrue(app.doc_modified())

    def test_updater_late_edit_or_escape_declines_without_interrupting_save(self):
        for edit in (True, False):
            with self.subTest(edit=edit):
                request = Path(self.tmp) / 'welcome.json'
                request.with_suffix('.restart-declined').unlink(missing_ok=True)
                with patch.object(doc, '_validate_in_worker', side_effect=sq.slow_validate([])):
                    self.updater()
                    fc._exit_tick()
                    if edit:
                        sq.draw_line(18)
                        self.finish()
                    else:
                        fc.cancel_exit()
                    self.pump.run(self.idle)
                self.assertTrue(request.with_suffix('.restart-declined').exists())
                self.assertFalse(request.with_suffix('.restart-approved').exists())
                self.assertEqual(self.quits, [])
                # Escape cancels Exit, not the Save. A completed unchanged Save
                # must correctly become clean; only the late-edit case stays dirty.
                self.assertEqual(app.doc_modified(), edit)

    def test_updater_quit_failure_retracts_approval_and_declines(self):
        with patch.object(fc, 'quitter', side_effect=RuntimeError('host refused quit')):
            request = self.updater()
            self.finish()
        self.assertTrue(request.with_suffix('.restart-declined').exists())
        self.assertFalse(request.with_suffix('.restart-approved').exists())
        self.assertEqual(self.quits, [])

    def test_edits_during_earlier_user_save_are_saved_before_exit(self):
        with patch.object(doc, '_validate_in_worker', side_effect=sq.slow_validate([])):
            fc.write_document(self.target, background=True)
            sq.draw_line(15)
            self.start()
            self.finish()
        self.assertEqual(self.quits, [True])
        self.assertEqual(sq.objects_in(self.target), 4)
        self.assertFalse(app.doc_modified())

    def test_existing_save_finishes_before_no_without_an_additional_write(self):
        with patch.object(doc, '_stage', wraps=doc._stage) as stage, \
                patch.object(doc, '_validate_in_worker', side_effect=sq.slow_validate([])), \
                patch.object(fc.filedialog, 'ask_yes_no_cancel', return_value=(fc.filedialog.OK, 'no')), \
                patch.object(fc, 'write_recovery', side_effect=AssertionError('No wrote recovery')):
            fc.write_document(self.target, background=True)
            sq.draw_line(18)
            self.start()
            self.assertEqual(self.quits, [])
            self.finish()
        self.assertEqual(stage.call_count, 1, 'Exit must not start a second Save/recovery')
        self.assertEqual(sq.objects_in(self.target), 3)
        self.assertEqual(app.model.object_count(), 4)
        self.assertTrue(app.doc_modified())
        self.assertEqual(self.quits, [True])


    def test_stale_discard_never_authorizes_later_edits_or_forced_exit(self):
        with patch.object(fc.filedialog, 'ask_yes_no_cancel', return_value=(fc.filedialog.OK, 'no')):
            self.start()
        self.assertTrue(fc.intentional_exit_ready())
        sq.draw_line(18)
        self.assertFalse(fc.intentional_exit_ready())
        handler = sq.SaveQueueTests.exit_handler(self)
        with patch.object(fc, 'write_recovery', return_value=None) as recovery:
            handler()
        recovery.assert_called_once()
        self.assertTrue(os.path.exists(fc._session_marker()))
        self.assertTrue(app.doc_modified())


    def test_escape_cancels_exit_not_user_save(self):
        with patch.object(doc, '_validate_in_worker', side_effect=sq.slow_validate([])):
            fc.write_document(self.target, background=True)
            self.start()
            fc._exit_tick()
            # Execute the real modal's early ESC branch without constructing a UI.
            tree = ast.parse((sq.ROOT / 'narwhal3d/ui.py').read_text())
            node = next(n for cls in tree.body if isinstance(cls, ast.ClassDef)
                        for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'modal'
                        and 'cancel_exit' in ast.unparse(n))
            ns = dict(__package__='narwhal3d', app_state=app)
            exec(compile(ast.Module(body=[node], type_ignores=[]), '<actual modal ESC>', 'exec'), ns)
            from types import SimpleNamespace
            redraw = []
            result = ns['modal'](SimpleNamespace(_redraw=lambda: redraw.append(True)), None,
                                  SimpleNamespace(type='ESC', value='PRESS'))
            self.assertEqual(result, {'RUNNING_MODAL'})
            self.assertEqual(redraw, [True])
            self.finish()
            self.pump.run(self.idle)
        self.assertEqual(self.quits, [])
        self.assertEqual(sq.objects_in(self.target), 3)

    def test_edit_after_approval_cancels_and_never_claims_latest_saved(self):
        with patch.object(doc, '_validate_in_worker', side_effect=sq.slow_validate([])):
            self.start()
            fc._exit_tick()  # approve and stage user Save
            sq.draw_line(12)
            self.finish()
            self.pump.run(self.idle)
        self.assertEqual(self.quits, [])
        self.assertTrue(app.doc_modified())
        self.assertEqual(sq.objects_in(self.target), 3)
        self.assertEqual(app.model.object_count(), 4)
        self.assertTrue(os.path.isfile(fc._session_marker()))

    def test_discard_approval_does_not_authorize_a_new_edit(self):
        # Isolate the post-dialog guard: native policy cannot inspect host history.
        self.start_policy = queue.shutdown_request('begin_shutdown')
        queue.shutdown_request('approve_shutdown', discard=True)
        fc._exit_polling = True
        fc._exit_guard = queue._snapshot(app)
        fc._exit_ready_callback = lambda: self.quits.append(True)
        sq.draw_line(12)
        fc._exit_tick()
        self.assertEqual(self.quits, [])
        self.assertTrue(app.doc_modified())
        self.assertFalse(fc.intentional_exit_ready())

    def test_failure_prevents_quit_and_clean_session_even_if_recovery_also_fails(self):
        with patch.object(doc, '_validate_and_pack', side_effect=OSError('deliberate refusal')):
            self.start()
            self.finish()
        self.assertEqual(self.quits, [])
        self.assertTrue(app.doc_modified())
        fc.end_session_if_protected(None)
        self.assertTrue(os.path.isfile(fc._session_marker()))

    def test_cancel_dialog_and_save_as_cancel_never_quit_or_save(self):
        for answer in ('cancel', 'yes'):
            with self.subTest(answer=answer):
                with patch.object(fc.filedialog, 'ask_yes_no_cancel', return_value=(
                        fc.filedialog.CANCEL if answer == 'cancel' else fc.filedialog.OK, answer)):
                    with patch.object(fc.ExitCmd, '_ask_save_path', return_value=(fc.filedialog.CANCEL, None)):
                        app.doc_path = None
                        self.start()
                        self.finish()
                self.assertFalse(queue.busy())
                self.assertEqual(self.quits, [])
                self.assertTrue(app.doc_modified())

    def test_prior_autosave_is_not_exit_approval(self):
        fc.write_recovery(background=True)
        self.pump.run(self.idle)
        with patch.object(fc.filedialog, 'ask_yes_no_cancel', return_value=(fc.filedialog.CANCEL, None)) as ask:
            self.start()
            self.finish()
        ask.assert_called_once()
        self.assertEqual(self.quits, [])

    def test_exit_refusal_repairs_on_main_thread_and_revalidates_before_quit(self):
        obj = list(app.model.file.Objects)[1]
        object_id = str(obj.Attributes.Id)
        validations, repairs = [], []
        rebuild = doc._rebuild_proxy

        def validate(root, count, **kw):
            validations.append(True)
            if len(validations) == 1:
                exc = doc.NarwhalDocumentError(
                    f"exact-master validation worker failed: master {object_id} "
                    "does not match its triangulated proxy")
                exc.errors = [str(exc)]
                raise exc
            self.assertEqual(self.quits, [], 'quit before revalidation')
            return sq.REAL_VALIDATE(root, count, **kw)

        def repair(*args, **kw):
            repairs.append(threading.current_thread() is threading.main_thread())
            return rebuild(*args, **kw)

        with patch.object(doc, '_validate_in_worker', side_effect=validate), \
                patch.object(doc, '_rebuild_proxy', side_effect=repair):
            self.start()
            self.finish()
        self.assertEqual(len(validations), 2)
        self.assertEqual(repairs, [True])
        self.assertEqual(self.quits, [True])
        self.assertEqual(sq.objects_in(self.target), 3)

    def test_timed_out_exit_pre_never_clears_session_marker(self):
        handler = sq.SaveQueueTests.exit_handler(self)
        with patch.object(fc, 'finish_saves', return_value=False), \
                patch.object(fc, 'write_recovery', return_value='/test/complete.narwhal'):
            handler()
        self.assertTrue(os.path.isfile(fc._session_marker()))

    def test_native_exit_pre_fallback_is_still_blocking_but_bounded_probe(self):
        handler = sq.SaveQueueTests.exit_handler(self)
        with patch.object(doc, '_validate_in_worker', side_effect=sq.slow_validate([])):
            fc.write_document(self.target, background=True)
            started = time.monotonic()
            handler()
            elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, sq.DELAY)
        self.assertFalse(queue.busy())
        print('NATIVE_EXIT_PRE_BLOCKER', json.dumps(dict(blocked_seconds=elapsed)))

    def test_exit_pre_failed_recovery_retains_crash_marker(self):
        handler = sq.SaveQueueTests.exit_handler(self)
        with patch.object(doc, 'save_model', return_value=False):
            handler()
        self.assertTrue(os.path.isfile(fc._session_marker()))
        self.assertTrue(app.doc_modified())

    def test_clean_marker_removed_only_after_successful_protection_and_idle(self):
        self.assertTrue(os.path.isfile(fc._session_marker()))
        self.start()
        self.finish()
        fc.end_session_if_protected(None)
        self.assertFalse(os.path.exists(fc._session_marker()))


def mutations():
    global NATIVE_UI_MUTATION
    # Private in-process host mutation, never writes shared production files or pyc.
    plans = [
        ('main-thread drain returns', '_exit_tick',
         '    app = _app()', '    app = _app()\n    save_queue.drain()',
         'test_save_exit_pumps_real_validation_and_skips_duplicate_exit_recovery'),
        ('latest-edit guard removed', '_exit_tick',
         'if _exit_guard is not None and save_queue._snapshot(app) != _exit_guard:',
         'if False:', 'test_discard_approval_does_not_authorize_a_new_edit'),
        ('discard performs recovery again', '_exit_tick',
         "elif action == 'commit':", "elif action == 'commit':\n            if app.doc_modified():\n                write_recovery()",
         'test_explicit_no_has_no_save_recovery_measurement_or_drain'),
        ('discard masquerades as saved', '_exit_tick',
         "save_queue.shutdown_request('approve_shutdown', discard=answer == 'no')",
         "save_queue.shutdown_request('approve_shutdown', discard=answer == 'no')\n            if answer == 'no':\n                app.mark_saved()",
         'test_explicit_no_has_no_save_recovery_measurement_or_drain'),
        ('failed recovery falsely clean', 'end_session_if_protected',
         "if not save_queue.busy() and (path or not _app().doc_modified() or intentional_exit_ready()):",
         'if True:', 'test_exit_pre_failed_recovery_retains_crash_marker'),
    ]
    for label, name, old, new, test in plans:
        original = getattr(fc, name)
        source = inspect.getsource(original)
        assert source.count(old) == 1, label
        namespace = dict(fc.__dict__)
        exec(compile(source.replace(old, new), '<shutdown mutation>', 'exec'), namespace)
        # Keep globals bound to the live host module; only the body is mutated.
        from types import FunctionType
        mutated = FunctionType(namespace[name].__code__, fc.__dict__)
        with patch.object(fc, name, mutated):
            result = unittest.TextTestRunner(verbosity=2).run(
                unittest.TestSuite([ResponsiveExit(test)]))
        if not result.failures or result.errors:
            raise AssertionError(f'mutation did not produce an assertion RED: {label}')
        print('MUTATION RED:', label)
    for label, old, new, test in [
        ('native approval does not bypass mailbox',
         'if guarded:\n        wm.narwhal_defer_close = False', 'if guarded:\n        pass',
         'test_native_approval_bypasses_guard_only_during_native_quit'),
        ('autosave ignores intentional discard',
         'if file_commands.intentional_exit_ready():', 'if False:',
         'test_autosave_cannot_write_after_intentional_discard'),
        ('native mailbox request dropped',
         'app_state.log(file_commands.request_exit())', 'app_state.log("dropped")',
         'test_native_mailbox_routes_to_coordinator_and_cancel_retains_guard'),
    ]:
        NATIVE_UI_MUTATION = old, new
        try:
            result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([ResponsiveExit(test)]))
        finally:
            NATIVE_UI_MUTATION = None
        if not result.failures or result.errors:
            raise AssertionError(f'native host mutation did not produce assertion RED: {label}')
        print('MUTATION RED:', label)
    original_request = fc.request_exit
    def prematurely_approve(**kwargs):
        if kwargs.get('on_ready') is not None:
            kwargs['on_ready']()
        return original_request(**kwargs)
    with patch.object(fc, 'request_exit', side_effect=prematurely_approve):
        result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([
            ResponsiveExit('test_updater_waits_for_durable_save_before_approval')]))
    if not result.failures or result.errors:
        raise AssertionError('premature update approval did not produce an assertion RED')
    print('MUTATION RED: updater approval before Save completion')
    def refuse(model, target, exc, *args):
        raise exc
    with patch.object(doc, '_recover_from_refusal', side_effect=refuse):
        result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([
            ResponsiveExit('test_exit_refusal_repairs_on_main_thread_and_revalidates_before_quit')]))
    if not result.failures or result.errors:
        raise AssertionError('repair bypass did not produce an assertion RED')
    print('MUTATION RED: refusal repair bypassed')
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(ResponsiveExit))
    if not result.wasSuccessful():
        raise AssertionError('restored host tests did not go GREEN')
    print('RESTORED GREEN: all responsive-exit host tests')


if __name__ == '__main__':
    if '--mutations' in sys.argv:
        mutations()
    else:
        unittest.main(argv=[sys.argv[0]])
