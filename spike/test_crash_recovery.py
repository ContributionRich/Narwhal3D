"""Crash recovery: after an UNCLEAN exit the next start offers the newest autosave.

`file_commands.begin_session` writes a per-process marker in the recovery folder at
startup; the real `_exit_handler` (extracted from ui.py) removes it on a clean exit. A
marker whose process is gone means the session crashed. A "next start" is simulated by
handing any marker left behind to a process that has really exited, then starting again.

  * crash -> prompt -> Open loads the newest recovery copy of THAT session, Untitled and
    modified, so Save asks where to keep it (never silently back into the recovery folder);
  * clean exit / normal restart -> no prompt (any dialog would fail the scripted user);
  * a live session's marker (another window) and an older recovery copy -> no prompt;
  * a reused pid (same number, different process) is still a crash;
  * no dialogs (NARWHAL3D_NO_DIALOGS=1) -> logged on the command line instead.

Run in Blender: blender -b --factory-startup --python-exit-code 1 --python <this> [-- --mutations]
"""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from narwhal3d import file_commands, filedialog, narwhal_document as doc  # noqa: E402
from narwhal3d.model import NarwhalModel  # noqa: E402
from narwhal3d.state import app_state  # noqa: E402

MUTATION = None          # (old, new) applied to `_exit_handler`'s source


class Scripted:
    """A user who answers exactly what the test scripted; anything else fails."""

    def __init__(self, ask=(), save=()):
        self.ask_answers, self.save_answers = list(ask), list(save)
        self.asked, self.save_starts = [], []

    def ask(self, title, text):
        self.asked.append(text)
        if not self.ask_answers:
            raise AssertionError(f"unexpected question: {text}")
        return self.ask_answers.pop(0)

    def save(self, title, start, filters):
        self.save_starts.append(start)
        if not self.save_answers:
            raise AssertionError("unexpected save dialog")
        return self.save_answers.pop(0)

    def open(self, title, start, filters):
        raise AssertionError("unexpected open dialog")


def dead_pid():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def exit_handler():
    source = (ROOT / "narwhal3d" / "ui.py").read_text()
    node = next(n for n in ast.parse(source).body
                if isinstance(n, ast.FunctionDef) and n.name == "_exit_handler")
    code = ast.unparse(node)
    if MUTATION:
        assert code.count(MUTATION[0]) == 1, MUTATION[0]
        code = code.replace(*MUTATION)
    g = dict(__name__="narwhal3d._exit_fixture", __package__="narwhal3d",
             _ai_shutdown=lambda: None, _remember_preferences=lambda _s: None,
             user_preference_snapshot=lambda: {}, _EXIT_SAVE_WAIT=60.0)
    exec(compile(code, "ui.py:_exit_handler", "exec"), g)
    return g["_exit_handler"]


class CrashRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="narwhal crash ")   # a space, as on Windows
        self.env = patch.dict(os.environ, NARWHAL3D_DATA_DIR=os.path.join(self.tmp, "data"))
        self.env.start()
        os.environ.pop("NARWHAL3D_NO_DIALOGS", None)
        self.user = Scripted()
        filedialog.set_backend(self.user)
        self.fresh()

    def tearDown(self):
        filedialog.set_backend(None)
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fresh(self):
        app_state.set_model(NarwhalModel())
        app_state.doc_path = None
        app_state.mark_saved()
        app_state.active_command = None
        app_state.history.clear()

    def draw(self):
        for text in ("Line", "w0,0,0", "w10,0,0"):
            app_state.feed_line(text)

    def markers(self):
        folder = file_commands.recovery_dir()
        return sorted(n for n in os.listdir(folder) if n.startswith("session-"))

    def next_start(self):
        """The process ends: whatever marker it left now belongs to a dead process."""
        for name in self.markers():
            path = os.path.join(file_commands.recovery_dir(), name)
            with open(path) as handle:
                info = json.load(handle)
            pid = dead_pid()
            info.update(pid=pid, proc_start=None)
            os.remove(path)
            with open(os.path.join(file_commands.recovery_dir(), f"session-{pid}.json"),
                      "w") as handle:
                json.dump(info, handle)
        self.fresh()
        return file_commands.begin_session()

    def session_with_unsaved_work(self, doc_path):
        self.assertEqual(file_commands.begin_session(), [])
        self.draw()
        app_state.doc_path = doc_path
        recovery = file_commands.write_recovery()            # what AutoSave writes
        self.assertTrue(recovery and os.path.exists(recovery), recovery)
        return recovery

    # -- crash -> prompt -> Open ----------------------------------------------------
    def test_crash_prompts_and_opens_the_newest_recovery_copy(self):
        original = os.path.join(self.tmp, "my part.narwhal")
        recovery = self.session_with_unsaved_work(original)
        crashed = self.next_start()                            # no exit handler ran
        self.assertEqual(len(crashed), 1)
        self.user.ask_answers = [(filedialog.OK, "yes")]
        self.assertEqual(file_commands.offer_crash_recovery(crashed), "opened")
        self.assertEqual(len(self.user.asked), 1)
        self.assertIn("closed unexpectedly", self.user.asked[0])
        self.assertIn("recovered copy of my part (autosaved ", self.user.asked[0])
        self.assertEqual(app_state.model.object_count(), 1, list(app_state.history)[-3:])
        self.assertIn("recovered copy", app_state.history[-1])
        # a recovery copy must not silently become the document's file
        self.assertIsNone(app_state.doc_path)
        self.assertTrue(app_state.doc_modified(), "recovered work must read unsaved")
        before = os.path.getmtime(recovery)
        target = os.path.join(self.tmp, "kept.narwhal")
        self.user.save_answers = [(filedialog.OK, target)]
        app_state.feed_line("Save")
        self.assertEqual(len(self.user.save_starts), 1, "Save must ask where to keep it")
        self.assertEqual(os.path.basename(self.user.save_starts[0]), "my part.narwhal")
        self.assertEqual(app_state.doc_path, target)
        self.assertEqual(doc.load_model(target).object_count(), 1)
        self.assertEqual(os.path.getmtime(recovery), before)
        # each crash is offered once: its marker is gone, only this live session's remains
        self.assertEqual(file_commands.begin_session(), [])
        self.assertEqual(self.markers(), [f"session-{os.getpid()}.json"])

    def test_declining_keeps_the_document_and_the_copy(self):
        recovery = self.session_with_unsaved_work(None)
        crashed = self.next_start()
        self.user.ask_answers = [(filedialog.OK, "no")]
        self.assertEqual(file_commands.offer_crash_recovery(crashed), "declined")
        self.assertIn("recovered copy of Untitled", self.user.asked[0])
        self.assertEqual(app_state.model.object_count(), 0)
        self.assertTrue(os.path.exists(recovery))

    def test_no_dialogs_logs_instead(self):
        recovery = self.session_with_unsaved_work(None)
        crashed = self.next_start()
        with patch.dict(os.environ, NARWHAL3D_NO_DIALOGS="1"):
            self.assertEqual(file_commands.offer_crash_recovery(crashed), "logged")
        self.assertEqual(self.user.asked, [])
        self.assertIn("closed unexpectedly", app_state.history[-1])
        self.assertIn(recovery, app_state.history[-1])

    # -- clean exits never prompt ---------------------------------------------------
    def test_clean_exit_then_restart_shows_no_prompt(self):
        self.session_with_unsaved_work(None)
        self.assertEqual(len(self.markers()), 1)
        exit_handler()()                                       # the real exit_pre body
        self.assertEqual(self.markers(), [], "a clean exit must remove its marker")
        crashed = self.next_start()
        self.assertEqual(crashed, [])
        self.assertIsNone(file_commands.offer_crash_recovery(crashed))
        self.assertEqual(self.user.asked, [])
        # the existing command-line notice still reports the copy
        self.assertIn("earlier session", file_commands.recovery_notice() or "")

    def test_normal_restart_without_work_shows_no_prompt(self):
        self.assertEqual(file_commands.begin_session(), [])
        self.assertEqual(file_commands.begin_session(), [], "this live session is no crash")
        exit_handler()()
        self.assertEqual(self.next_start(), [])
        self.assertEqual(self.user.asked, [])

    def test_a_live_session_is_not_a_crash(self):
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            folder = file_commands.recovery_dir()
            os.makedirs(folder, exist_ok=True)
            marker = os.path.join(folder, f"session-{sleeper.pid}.json")
            with open(marker, "w") as handle:
                json.dump({"pid": sleeper.pid, "started": time.time() - 5,
                           "proc_start": file_commands._process_start(sleeper.pid)}, handle)
            self.assertEqual(file_commands.begin_session(), [])
            self.assertTrue(os.path.exists(marker), "another window's marker was touched")
        finally:
            sleeper.kill()
            sleeper.wait()

    def test_a_reused_pid_is_still_a_crash(self):
        folder = file_commands.recovery_dir()
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, f"session-{os.getpid()}.json"), "w") as handle:
            json.dump({"pid": os.getpid(), "started": time.time() - 50, "proc_start": -1},
                      handle)
        self.assertEqual(len(file_commands.begin_session()), 1)

    def test_an_older_recovery_copy_is_not_offered_for_a_new_crash(self):
        recovery = self.session_with_unsaved_work(None)
        old = time.time() - 3600
        os.utime(recovery, (old, old))
        crashed = self.next_start()
        self.assertEqual(len(crashed), 1)
        self.assertIsNone(file_commands.offer_crash_recovery(crashed))
        self.assertEqual(self.user.asked, [])


def mutations():
    global MUTATION
    plans = [
        ("clean exit never removes the marker", "test_clean_exit_then_restart_shows_no_prompt",
         ("file_commands.end_session()", "None"), None),
        ("crash detection ignores dead sessions",
         "test_crash_prompts_and_opens_the_newest_recovery_copy",
         None, lambda: patch.object(file_commands, "_session_alive", return_value=True)),
        ("a recovery copy becomes the saved path",
         "test_crash_prompts_and_opens_the_newest_recovery_copy",
         None, lambda: patch.object(file_commands, "is_recovery_copy", return_value=False)),
        ("live sessions treated as crashed", "test_a_live_session_is_not_a_crash",
         None, lambda: patch.object(file_commands, "_session_alive", return_value=False)),
        ("pid reuse ignored", "test_a_reused_pid_is_still_a_crash",
         None, lambda: patch.object(file_commands, "_process_start", return_value=None)),
        ("older copies offered (session start ignored)",
         "test_an_older_recovery_copy_is_not_offered_for_a_new_crash",
         None, lambda: patch.object(
             file_commands, "crash_recovery_candidate",
             side_effect=lambda crashed, real=file_commands.crash_recovery_candidate:
                 real([dict(info, started=0.0) for info in crashed]))),
    ]
    survived = []
    for label, test, source_mutation, patcher in plans:
        MUTATION = source_mutation
        try:
            suite = unittest.TestSuite([CrashRecoveryTests(test)])
            if patcher is not None:
                with patcher():
                    result = unittest.TextTestRunner(verbosity=0).run(suite)
            else:
                result = unittest.TextTestRunner(verbosity=0).run(suite)
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
        unittest.defaultTestLoader.loadTestsFromTestCase(CrashRecoveryTests))
    failed = not result.wasSuccessful()
    if "--mutations" in args:
        failed = not mutations() or failed
    if failed:
        raise SystemExit(1)
    print("CRASH RECOVERY PASSED")
