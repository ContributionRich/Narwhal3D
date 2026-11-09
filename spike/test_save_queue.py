"""Background Save / AutoSave (narwhal3d/save_queue.py): the UI keeps running while a save
is validated, and no work is ever lost.

MEASURED before this suite: a Save on a user's 33-object project froze the UI for
30-45 s. Here the validation worker is the REAL one, slowed by a fixed delay, and the
"UI" is a main-thread loop that pumps the save's scheduler and counts its own ticks:

  * the main thread keeps ticking while a save runs (no gap anywhere near the delay);
  * an edit made while saving is NOT in the file, and the document stays modified;
  * a worker refusal is repaired on the main thread and validated again;
  * one save at a time: requests made meanwhile queue, and the newest one wins;
  * AutoSave runs in the background and never marks the document saved;
  * Exit (`_exit_handler`, the real function body) waits for the save in flight
    instead of starting another, so the file is written and no recovery copy is needed.

Run in Blender: blender -b --factory-startup --python-exit-code 1 --python <this> [-- --mutations]
"""
import ast
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from narwhal3d import bridge, file_commands, filedialog, kernel  # noqa: E402
from narwhal3d import narwhal_document as doc, save_queue  # noqa: E402
from narwhal3d.kernel import occt_backend as ob  # noqa: E402
from narwhal3d.model import NarwhalModel  # noqa: E402
from narwhal3d.state import app_state  # noqa: E402

DELAY = 2.0
REAL_VALIDATE = doc._validate_in_worker
MUTATION = None          # (old, new) applied to `_exit_handler`'s source


def solid(maker):
    recipe = kernel.recipe_of(maker)
    master = ob.from_recipe(recipe)
    proxy = bridge._proxy_from_occt(master)
    kernel.register_master(proxy, master)
    kernel.record_recipe(proxy, recipe)
    return proxy


def draw_line(y):
    for text in ("Line", f"w0,{y},0", f"w10,{y},0"):
        app_state.feed_line(text)


def objects_in(path):
    return doc.load_model(path).object_count()


class Pump:
    """The main-thread loop: runs whatever the save scheduled and counts ticks."""

    def __init__(self):
        self.callbacks = []
        self.ticks = 0
        self.max_gap = 0.0

    def schedule(self, fn):
        self.callbacks.append(fn)

    def run(self, until, timeout=120.0):
        deadline = time.monotonic() + timeout
        last = time.monotonic()
        while not until():
            if time.monotonic() > deadline:
                raise AssertionError("the save never finished")
            for fn in list(self.callbacks):
                if fn() is None:
                    self.callbacks.remove(fn)
            now = time.monotonic()
            self.max_gap = max(self.max_gap, now - last)
            last = now
            self.ticks += 1
            time.sleep(0.01)


def slow_validate(calls):
    def validate(root, count, **kw):
        calls.append(threading.current_thread() is threading.main_thread())
        time.sleep(DELAY)
        return REAL_VALIDATE(root, count, **kw)
    return validate


class SaveQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="narwhal save queue ")
        self.env = patch.dict(os.environ, NARWHAL3D_DATA_DIR=os.path.join(self.tmp, "data"))
        self.env.start()
        filedialog.set_backend(None)
        self.pump = Pump()
        save_queue.install(self.pump.schedule)
        app_state.set_model(NarwhalModel())
        app_state.doc_path = None
        app_state.mark_saved()
        app_state.history.clear()

    def tearDown(self):
        save_queue.uninstall()
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def idle(self):
        return not save_queue.busy()

    def two_solids(self):
        model = NarwhalModel()
        app_state.set_model(model)
        a, _ = model.add_object(solid(bridge.make_cylinder((0, 0, 0), 1, 3)), "Keep", 0)
        b, _ = model.add_object(solid(bridge.make_cylinder((5, 0, 0), 1, 2)), "Refused", 0)
        app_state.mark_saved()
        draw_line(9)                      # a real, undoable edit: the document is modified
        return a, b

    # -- 1. the main thread is never blocked ----------------------------------------
    def test_ui_keeps_running_during_a_slow_save(self):
        self.two_solids()
        target = os.path.join(self.tmp, "part.narwhal")
        calls = []
        with patch.object(doc, "_validate_in_worker", side_effect=slow_validate(calls)):
            started = time.monotonic()
            ok, message = file_commands.write_document(target, background=True)
            request = time.monotonic() - started
            self.assertTrue(ok, message)
            self.assertTrue(message.startswith("Saving part.narwhal"), message)
            self.assertLess(request, DELAY / 2, "Save blocked the main thread")
            self.assertTrue(save_queue.busy())
            self.pump.run(self.idle)
        self.assertEqual(calls, [False], "validation must run off the main thread")
        self.assertGreater(self.pump.ticks, 50, "the main loop did not keep running")
        self.assertLess(self.pump.max_gap, DELAY / 2, f"gap {self.pump.max_gap:.2f}s")
        self.assertEqual(objects_in(target), 3)
        self.assertEqual(app_state.doc_path, target)
        self.assertEqual(app_state.model.path, target)
        self.assertFalse(app_state.doc_modified())
        self.assertEqual(app_state.history[-1], f"Saved {target}")
        print(f"MEASURED: request {request * 1000:.0f} ms, {self.pump.ticks} main-loop ticks "
              f"during the save, longest gap {self.pump.max_gap * 1000:.0f} ms")

    # -- 2. edits made while saving ------------------------------------------------
    def test_edit_during_save_is_not_in_the_file_and_stays_modified(self):
        self.two_solids()
        target = os.path.join(self.tmp, "edited.narwhal")
        with patch.object(doc, "_validate_in_worker", side_effect=slow_validate([])):
            file_commands.write_document(target, background=True)
            draw_line(20)                 # while the worker runs
            self.assertTrue(save_queue.busy(), "the save finished before the edit")
            self.pump.run(self.idle)
        self.assertEqual(objects_in(target), 3, "an edit made after staging leaked in")
        self.assertEqual(app_state.doc_path, target)
        self.assertTrue(app_state.doc_modified(), "unsaved edit reported as saved")
        self.assertIn("not in that file yet", app_state.history[-1])
        app_state.feed_line("Undo")       # back to exactly what was staged
        self.assertFalse(app_state.doc_modified(),
                         "undoing to the saved state must read unmodified")

    # -- 3. a refusal is repaired on the main thread, then validated again ---------
    def test_refusal_is_repaired_and_revalidated(self):
        _a, b = self.two_solids()
        target = os.path.join(self.tmp, "repaired.narwhal")
        validations = []
        repaired_on_main = []
        real_rebuild = doc._rebuild_proxy

        def validate(root, count, **kw):
            validations.append(1)
            time.sleep(0.3)
            if len(validations) == 1:
                error = doc.NarwhalDocumentError(
                    f"exact-master validation worker failed: master {b} "
                    "does not match its triangulated proxy")
                error.errors = [str(error)]
                raise error
            return REAL_VALIDATE(root, count, **kw)

        def rebuild(*args, **kw):
            repaired_on_main.append(threading.current_thread() is threading.main_thread())
            return real_rebuild(*args, **kw)

        with patch.object(doc, "_validate_in_worker", side_effect=validate), \
                patch.object(doc, "_rebuild_proxy", side_effect=rebuild):
            ok, _message = file_commands.write_document(target, background=True)
            self.assertTrue(ok)
            self.pump.run(self.idle)
        self.assertEqual(len(validations), 2, "the repaired document was not re-validated")
        self.assertEqual(repaired_on_main, [True])
        self.assertTrue(any("Refused: display copy rebuilt" in line
                            for line in app_state.history), list(app_state.history)[-4:])
        self.assertEqual(app_state.history[-1], f"Saved {target}")
        reopened = doc.load_model(target)
        self.assertEqual(len(reopened._native_master_bindings), 2)
        self.assertFalse(app_state.doc_modified())

    # -- 4. one save at a time; the newest queued request wins ---------------------
    def test_requests_during_a_save_queue_and_the_newest_wins(self):
        self.two_solids()
        first, dropped, newest = (os.path.join(self.tmp, f"{n}.narwhal")
                                  for n in ("first", "dropped", "newest"))
        calls = []
        with patch.object(doc, "_validate_in_worker", side_effect=slow_validate(calls)):
            file_commands.write_document(first, background=True)
            ok, message = file_commands.write_document(dropped, background=True)
            self.assertTrue(ok and message.startswith("Save queued"), message)
            file_commands.write_document(newest, background=True)
            self.assertEqual(save_queue.intended_path(), newest)
            self.pump.run(self.idle)
        self.assertEqual(len(calls), 2, "exactly two saves should have run")
        self.assertTrue(os.path.exists(first) and os.path.exists(newest))
        self.assertFalse(os.path.exists(dropped), "a superseded request was still saved")
        self.assertEqual(app_state.doc_path, newest)
        self.assertFalse(app_state.doc_modified())

    # -- 5. AutoSave: background, never "saved", never behind a running save -------
    def test_autosave_runs_in_the_background_and_keeps_the_document_modified(self):
        self.two_solids()
        with patch.object(doc, "_validate_in_worker", side_effect=slow_validate([])):
            started = time.monotonic()
            path = file_commands.write_recovery(background=True)
            self.assertLess(time.monotonic() - started, DELAY / 2)
            self.assertTrue(path and path.startswith(file_commands.recovery_dir()), path)
            self.assertIs(file_commands.write_recovery(background=True), False,
                          "a second autosave must wait for the running one")
            self.pump.run(self.idle)
        self.assertEqual(objects_in(path), 3)
        self.assertEqual(app_state.history[-1], f"Autosaved to {path}")
        self.assertIsNone(app_state.doc_path)
        self.assertTrue(app_state.doc_modified())

    # -- 6. Exit waits for the save in flight (the real exit handler body) ---------
    def exit_handler(self):
        source = (ROOT / "narwhal3d" / "ui.py").read_text()
        node = next(n for n in ast.parse(source).body
                    if isinstance(n, ast.FunctionDef) and n.name == "_exit_handler")
        code = ast.unparse(node)
        if MUTATION:
            self.assertEqual(code.count(MUTATION[0]), 1, MUTATION[0])
            code = code.replace(*MUTATION)
        g = dict(__name__="narwhal3d._exit_fixture", __package__="narwhal3d",
                 _ai_shutdown=lambda: None, _remember_preferences=lambda _s: None,
                 user_preference_snapshot=lambda: {}, _EXIT_SAVE_WAIT=60.0)
        exec(compile(code, "ui.py:_exit_handler", "exec"), g)
        return g["_exit_handler"]

    def test_exit_waits_for_the_save_in_flight(self):
        self.two_solids()
        target = os.path.join(self.tmp, "at exit.narwhal")
        handler = self.exit_handler()
        calls = []
        with patch.object(doc, "_validate_in_worker", side_effect=slow_validate(calls)):
            file_commands.write_document(target, background=True)
            self.assertTrue(save_queue.busy())
            handler()                                     # no pumping: exit is synchronous
        self.assertFalse(save_queue.busy())
        self.assertEqual(len(calls), 1, "exit started a second save")
        self.assertEqual(objects_in(target), 3)
        self.assertFalse(app_state.doc_modified())
        recovery = file_commands.recovery_dir()
        left = [n for n in os.listdir(recovery) if n.endswith(".narwhal")] \
            if os.path.isdir(recovery) else []
        self.assertEqual(left, [], "the finished save should have made a recovery copy moot")

    def test_exit_wait_is_bounded(self):
        self.two_solids()
        target = os.path.join(self.tmp, "bounded.narwhal")
        with patch.object(doc, "_validate_in_worker", side_effect=slow_validate([])):
            file_commands.write_document(target, background=True)
            started = time.monotonic()
            self.assertFalse(file_commands.finish_saves(0.2))
            self.assertLess(time.monotonic() - started, 1.0)
            self.assertTrue(app_state.doc_modified(), "an unfinished save is not a save")
            self.pump.run(self.idle)
        self.assertFalse(app_state.doc_modified())

    # -- 7. headless / scripted: the synchronous API is unchanged ------------------
    def test_without_install_save_is_synchronous(self):
        save_queue.uninstall()
        self.two_solids()
        target = os.path.join(self.tmp, "sync.narwhal")
        ok, message = file_commands.write_document(target, background=True)
        self.assertTrue(ok, message)
        self.assertEqual(message, f"Saved {target}")
        self.assertEqual(objects_in(target), 3)
        self.assertTrue(doc.save_model(app_state.model, os.path.join(self.tmp, "api.narwhal")))


class _SyncThread:
    """Mutation: the 'background' thread runs on the caller's thread."""

    def __init__(self, target, args=(), **_kw):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)

    def is_alive(self):
        return False

    def join(self, _timeout=None):
        return None


def mutations():
    global MUTATION
    plans = [
        ("worker runs on the main thread", "test_ui_keeps_running_during_a_slow_save",
         lambda: patch.object(save_queue.threading, "Thread", _SyncThread), None),
        ("edits during a save are marked saved",
         "test_edit_during_save_is_not_in_the_file_and_stays_modified",
         lambda: patch.object(save_queue, "_mark_saved_as_staged",
                              side_effect=lambda app, job: app.mark_saved(job.target) or True),
         None),
        ("refusal is not repaired", "test_refusal_is_repaired_and_revalidated",
         lambda: patch.object(doc, "_recover_from_refusal",
                              side_effect=lambda m, t, exc, *a: (_ for _ in ()).throw(exc)),
         None),
        ("no queue: every request starts at once",
         "test_requests_during_a_save_queue_and_the_newest_wins",
         lambda: patch.object(save_queue, "busy", side_effect=lambda: False), None),
        ("autosave marks the document saved",
         "test_autosave_runs_in_the_background_and_keeps_the_document_modified",
         lambda: patch.object(save_queue, "USER", save_queue.AUTOSAVE), None),
        ("exit does not wait for the save", "test_exit_waits_for_the_save_in_flight",
         None, ("file_commands.finish_saves(_EXIT_SAVE_WAIT)", "True")),
    ]
    survived = []
    for label, test, patcher, source_mutation in plans:
        MUTATION = source_mutation
        try:
            if patcher is not None:
                with patcher():
                    result = unittest.TextTestRunner(verbosity=0).run(
                        unittest.TestSuite([SaveQueueTests(test)]))
            else:
                result = unittest.TextTestRunner(verbosity=0).run(
                    unittest.TestSuite([SaveQueueTests(test)]))
        finally:
            MUTATION = None
        red = not result.wasSuccessful()
        print(("RED verified: " if red else "FAIL: mutation survived: ") + label)
        if not red:
            survived.append(label)
    return not survived


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(SaveQueueTests))
    failed = not result.wasSuccessful()
    if "--mutations" in args:
        failed = not mutations() or failed
    if failed:
        raise SystemExit(1)
    print("SAVE QUEUE PASSED")
