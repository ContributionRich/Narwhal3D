"""`Import` opens the file browser at once, like `Open`; the typed name is the fallback.

The file dialog and `import_commands.import_into` are replaced: a scripted backend answers
the browser, and a recording importer stands in for the real read so no file is opened and
no external dialog tool is launched. The command-level checks drive `begin` / `feed` on a
fake document context; the state checks drive the real `app_state` (`feed_line`,
`start_command`) with the real undo stack.

Interpreter: `state.py` loads its native document observer on import, so run this under the
Blender-bundled Python (no bpy is loaded):

    tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_import_dialog.py
"""
import importlib
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg

import rhino3dm as r3  # noqa: E402

filedialog = importlib.import_module("narwhal3d.filedialog")
import_commands = importlib.import_module("narwhal3d.import_commands")
model_mod = importlib.import_module("narwhal3d.model")
app_state = importlib.import_module("narwhal3d.state").app_state

OK, CANCEL, UNAV = filedialog.OK, filedialog.CANCEL, filedialog.UNAVAILABLE
PART = os.path.join(tempfile.gettempdir(), "narwhal_import_dialog", "part.dxf")
OTHER = os.path.join(tempfile.gettempdir(), "narwhal_import_dialog", "other.obj")
REAL_IMPORT_INTO = import_commands.import_into
REAL_ENV = os.environ.get("NARWHAL3D_NO_DIALOGS")

checks = 0
failures = []


def check(cond, label, detail=""):
    global checks
    checks += 1
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        failures.append(f"{label} {detail}".rstrip())


class ScriptedDialog:
    """Answers the browser from a script and records each call. Once the script runs out
    it answers UNAVAILABLE, so an extra call shows up as a count mismatch, not a crash."""

    def __init__(self, answers=()):
        self.answers = list(answers)
        self.opened = []  # (title, start, filters)

    def open(self, title, start, filters):
        self.opened.append((title, start, filters))
        return self.answers.pop(0) if self.answers else (UNAV, None)

    def save(self, title, start, filters):
        raise AssertionError("Import never opens a save dialog")

    def ask(self, title, text):
        raise AssertionError("Import never asks a yes/no question")


class Importer:
    """Stands in for `import_into`: records the path and mutates like the real import --
    one `note_change`, then the objects. Each import adds two objects."""

    def __init__(self):
        self.calls = []

    def __call__(self, ctx, path):
        self.calls.append(path)
        ctx.note_change("Import")
        for i in range(2):
            ctx.model.add_object(r3.LineCurve(r3.Point3d(0, i, 0), r3.Point3d(1, i, 0)),
                                 f"Line{i}", 0)
        return True, f"Imported {os.path.basename(path)}"


class FakeCtx:
    """The hooks a command reaches the document through, recorded in order."""

    def __init__(self):
        self.model = model_mod.NarwhalModel()
        self.events = []

    def begin_undo(self, name):
        self.events.append(("begin_undo", name))

    def note_change(self, name=None):
        self.events.append(("note_change", name))

    def end_undo(self):
        self.events.append(("end_undo",))


def install_dialog(answers=()):
    dialog = ScriptedDialog(answers)
    filedialog.set_backend(dialog)
    return dialog


def install_importer():
    imp = Importer()
    import_commands.import_into = imp
    return imp


def fresh_app():
    app_state.set_model(model_mod.NarwhalModel())
    # Undo redraws the viewport through bpy; the history restore itself needs no bpy.
    app_state.ctx.rebuild_display = lambda: 0
    app_state.doc_path = None
    app_state.mark_saved()
    app_state.active_command = None


def last():
    return app_state.history[-1] if app_state.history else ""


def command_level():
    print("command level: begin / feed on a fake document context")
    app_state.doc_path = None

    # accepted browser: one dialog, one import, one undo record around the mutation
    dialog = install_dialog([(OK, PART)])
    imp = install_importer()
    cmd, ctx = import_commands.ImportCmd(), FakeCtx()
    msg, done = cmd.begin(ctx)
    check(len(dialog.opened) == 1 and dialog.opened[0][0] == "Import",
          "begin opens the browser exactly once, titled Import", repr(dialog.opened))
    check(dialog.opened and dialog.opened[0][2] == import_commands.IMPORT_FILTERS,
          "the browser uses the Import filters")
    check(dialog.opened and dialog.opened[0][1] == os.path.expanduser("~"),
          "the browser starts in the home folder when nothing is open")
    check(imp.calls == [PART] and done,
          "an accepted file is imported once and the command ends", f"{imp.calls} {done}")
    check(ctx.events == [("begin_undo", "Import"), ("note_change", "Import"),
                         ("end_undo",)],
          "the import is one undo record, opened before and closed after the mutation",
          repr(ctx.events))
    check(ctx.model.object_count() == 2, "the accepted file's objects are in the model")

    # cancelled: nothing is imported, no undo record is opened, the model is untouched
    dialog = install_dialog([(CANCEL, None)])
    imp = install_importer()
    cmd, ctx = import_commands.ImportCmd(), FakeCtx()
    msg, done = cmd.begin(ctx)
    check(msg == "Import cancelled" and done, "cancelling ends the command", repr(msg))
    check(imp.calls == [] and ctx.events == [] and ctx.model.object_count() == 0,
          "a cancelled browser changes nothing", f"{imp.calls} {ctx.events}")

    # no dialog tool: the typed-name prompt stays, and a typed name imports without a 2nd dialog
    dialog = install_dialog([(UNAV, None)])
    imp = install_importer()
    cmd, ctx = import_commands.ImportCmd(), FakeCtx()
    msg, done = cmd.begin(ctx)
    check(not done and "type the file name" in msg,
          "with no dialog tool the command stays open and asks for a typed name", repr(msg))
    check(len(dialog.opened) == 1 and imp.calls == [] and ctx.events == [],
          "the unavailable browser was tried once and imported nothing")
    msg, done = cmd.feed(PART, ctx)
    check(imp.calls == [PART] and done, "a typed name imports that file and ends",
          f"{imp.calls} {done}")
    check(len(dialog.opened) == 1,
          "an explicit typed path does not reopen the browser", f"{len(dialog.opened)} calls")
    check(ctx.events == [("begin_undo", "Import"), ("note_change", "Import"),
                         ("end_undo",)],
          "the typed import is also one undo record", repr(ctx.events))

    # blank Enter at the typed prompt cancels, as Open does; it does not reopen the browser
    dialog = install_dialog([(UNAV, None)])
    imp = install_importer()
    cmd, ctx = import_commands.ImportCmd(), FakeCtx()
    cmd.begin(ctx)
    msg, done = cmd.feed("", ctx)
    check(msg == "Import cancelled" and done and imp.calls == [] and len(dialog.opened) == 1,
          "blank Enter cancels the typed prompt without a second browser attempt",
          f"{msg!r} {done} {len(dialog.opened)}")

    # headless (NARWHAL3D_NO_DIALOGS): no dialog tool exists, so the prompt is the answer
    os.environ["NARWHAL3D_NO_DIALOGS"] = "1"
    filedialog.set_backend(None)
    imp = install_importer()
    cmd, ctx = import_commands.ImportCmd(), FakeCtx()
    msg, done = cmd.begin(ctx)
    check(not done and imp.calls == [] and ctx.events == [],
          "headless Import opens no dialog and imports nothing before a typed name",
          f"{msg!r} {done}")
    msg, done = cmd.feed(OTHER, ctx)
    check(imp.calls == [OTHER] and done, "headless Import imports the typed name",
          f"{imp.calls}")
    if REAL_ENV is None:
        os.environ.pop("NARWHAL3D_NO_DIALOGS", None)
    else:
        os.environ["NARWHAL3D_NO_DIALOGS"] = REAL_ENV


def state_level():
    print("state level: feed_line / start_command on the real app_state")

    # accepted browser: one import, and ONE Undo removes everything the import added
    fresh_app()
    dialog = install_dialog([(OK, PART)])
    imp = install_importer()
    app_state.feed_line("Import")
    check(len(dialog.opened) == 1 and imp.calls == [PART] and app_state.active_command is None,
          "Import from the command line opens the browser once and imports the choice",
          f"{len(dialog.opened)} {imp.calls} active={app_state.active_command}")
    check(app_state.model.object_count() == 2, "the import adds its two objects")
    app_state.feed_line("Undo")
    check(app_state.model.object_count() == 0,
          "one Undo removes the whole import, not one object at a time")
    app_state.feed_line("Redo")
    check(app_state.model.object_count() == 2, "Redo restores the whole import")

    # cancelled: nothing changes and the model stays unmodified
    fresh_app()
    dialog = install_dialog([(CANCEL, None)])
    imp = install_importer()
    app_state.feed_line("Import")
    check(imp.calls == [] and app_state.active_command is None
          and app_state.model.object_count() == 0 and not app_state.doc_modified()
          and last() == "Import cancelled",
          "cancelling the browser from the command line leaves the model untouched",
          f"{last()!r}")

    # no dialog tool: the command stays at its prompt until a file name arrives
    fresh_app()
    dialog = install_dialog([(UNAV, None)])
    imp = install_importer()
    app_state.feed_line("Import")
    check(app_state.active_command is not None and imp.calls == []
          and "type the file name" in last(),
          "with no dialog tool Import stays at its typed-name prompt", f"{last()!r}")
    app_state.feed_line(PART)
    check(imp.calls == [PART] and app_state.active_command is None
          and len(dialog.opened) == 1 and app_state.model.object_count() == 2,
          "the typed name imports once and the browser is not reopened",
          f"{imp.calls} dialogs={len(dialog.opened)}")

    # inline typed file names: the state's inline route bypasses begin() entirely
    fresh_app()
    dialog = install_dialog([])
    imp = install_importer()
    app_state.start_command("Import", rest=PART)
    check(imp.calls == [PART] and not dialog.opened and app_state.active_command is None,
          "start_command('Import', rest=path) imports the path with no browser",
          f"{imp.calls} dialogs={len(dialog.opened)}")
    check(app_state.model.object_count() == 2 and app_state.active_command is None,
          "the inline import adds its objects once")
    fresh_app()
    dialog = install_dialog([])
    imp = install_importer()
    app_state.feed_line("Import " + OTHER)
    check(imp.calls == [OTHER] and not dialog.opened and app_state.active_command is None,
          "Import <path> on the command line imports the path with no browser",
          f"{imp.calls}")


def main():
    try:
        command_level()
        state_level()
    finally:
        import_commands.import_into = REAL_IMPORT_INTO
        filedialog.set_backend(None)
        if REAL_ENV is None:
            os.environ.pop("NARWHAL3D_NO_DIALOGS", None)
        else:
            os.environ["NARWHAL3D_NO_DIALOGS"] = REAL_ENV
    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        for failure in failures:
            print("FAIL:", failure)
        sys.exit(1)
    print("IMPORT DIALOG PASSED")


if __name__ == "__main__":
    main()
