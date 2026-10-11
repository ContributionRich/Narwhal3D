"""Narwhal document commands for Save, SaveAs, New, Open, Recent, and recovery.

A named document saves back to its current path; an untitled document asks for one.
SaveAs adopts the new path, while export to another format remains a separate command.
Writes use a temporary sibling and atomic replacement so a failed write cannot replace a
good file with a partial one. New documents use Narwhal's internal defaults. Unsupported
save options are reported rather than silently ignored.

These classes replace the minimal registry entries from :mod:`commands` when imported by
the application state, so every invocation route shares one file workflow.
"""
from __future__ import annotations

import os

from . import commands
from . import filedialog

TEXT = commands.TEXT
EXT = ".3dm"


def _app():
    from .state import app_state

    return app_state


def _normalise(path):
    """A typed/picked path -> an absolute .3dm path, or ``(None, reason)``."""
    p = os.path.abspath(os.path.expanduser((path or "").strip().strip('"')))
    if not (path or "").strip():
        return None, "no file name given"
    root, ext = os.path.splitext(p)
    if not ext:
        p += EXT                     # the reference CAD application adds the extension for you
    elif ext.lower() != EXT:
        return None, (f"{os.path.basename(p)!r}: saving as {ext} is not "
                      f"implemented yet -- .3dm, or export to .step .ai .pdf .svg")
    return p, None


def _export_route(path):
    """The export extension of `path` when Save should EXPORT instead, else None."""
    from . import export_commands

    ext = os.path.splitext((path or "").strip().strip('"'))[1].lower()
    return ext if ext in export_commands.FORMATS and ext != EXT else None


def write_document(path):
    """Write the model to `path` (atomically). Returns ``(ok, message)``.

    A path with another format's extension (.step, .ai, .pdf, .svg) EXPORTS the
    whole model instead -- MEASURED (commands/save.htm): "To save as a different file
    format, type the file name with the desired extension. See: Export." -- and, as
    Export says, "it does not become your active the reference CAD application model": the document keeps
    its file and its modified state.
    """
    app = _app()
    if _export_route(path):
        from . import export_commands

        ok, msg = export_commands.export_objects(
            app.model, list(app.model.file.Objects), path)
        return ok, (msg + " -- the model's own file is unchanged") if ok else msg
    target, err = _normalise(path)
    if err:
        return False, err
    folder = os.path.dirname(target)
    if not os.path.isdir(folder):
        return False, f"the folder {folder!r} does not exist -- nothing was saved"
    tmp = f"{target}.saving-{os.getpid()}.tmp"
    try:
        ok = app.model.save(tmp)
        if not ok or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
            raise OSError("the 3dm writer reported failure")
        os.replace(tmp, target)
    except Exception as e:  # noqa: BLE001
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False, f"could not save {target}: {e}"
    app.mark_saved(target)
    app.suggested_name = None
    recent_add(target)
    return True, f"Saved {target}"


def _rebuild_display():
    """Draw the newly opened / new document -- then settle the Python heap
    (`_settle_heap`). Every New and Open ends here."""
    app = _app()
    try:
        drawn = app.ctx.rebuild_display()
    except Exception:  # noqa: BLE001 -- headless suites have no viewport
        drawn = 0
    _settle_heap()
    return drawn


def _settle_heap():
    """Collect what the previous document left behind, then FREEZE what is alive now.

    Large models can leave hundreds of thousands of objects tracked by Python's cyclic
    garbage collector, making a full collection take tens of milliseconds
    -- it landed in frames of a Gumball drag and of an undo, at random ("gc gen2").
    Almost all of those objects live as long as the document does. `gc.freeze()` moves
    them out of every later collection, so a full one only looks at objects created
    since. The previous document's frozen objects are UNfrozen and collected first, so
    an Open never leaves the last document's cycles stranded in the frozen set.
    Garbage cycles among the frozen objects themselves wait for the next New / Open.
    """
    import gc

    try:
        gc.unfreeze()
        gc.collect()
        gc.freeze()
    except Exception:  # noqa: BLE001
        pass


class _FileCommand(commands.Command):
    records_undo = False             # writing a file does not change the document
    variable_points = False

    def _ask_save_path(self, title):
        app = _app()
        start = (app.doc_path or getattr(app, "suggested_name", None)
                 or os.path.join(filedialog.start_dir(), "Untitled.3dm"))
        return filedialog.ask_save_path(title, start)

    def _settle_unsaved(self, verb):
        """Before New/Open: ask about unsaved changes. Returns None to go on, or the
        ``(message, done)`` that ends the command."""
        app = _app()
        if not app.doc_modified():
            return None
        status, answer = filedialog.ask_yes_no_cancel(
            "Narwhal 3D", f"Save changes to {app.doc_title()}?")
        if status == filedialog.UNAVAILABLE:
            # no way to ask -> never discard work silently
            return (f"{verb} cancelled: the model has unsaved changes and no dialog "
                    f"is available to ask about them. Save first, then {verb}."), True
        if status == filedialog.CANCEL:
            return f"{verb} cancelled", True
        if answer == "yes":
            path = app.doc_path
            if not path:
                st, path = self._ask_save_path("Save")
                if st != filedialog.OK:
                    return f"{verb} cancelled", True
            ok, msg = write_document(path)
            app.log(msg)
            if not ok:
                return f"{verb} cancelled", True
        return None


class SaveCmd(_FileCommand):
    name = "Save"
    description = "Save the model to its file (asks for a name the first time)."
    steps = [("File name to save:", TEXT)]

    def begin(self, ctx):
        app = _app()
        if app.doc_path:
            return write_document(app.doc_path)[1], True
        status, path = self._ask_save_path(self.name)
        if status == filedialog.OK:
            return write_document(path)[1], True
        if status == filedialog.CANCEL:
            return f"{self.name} cancelled", True
        return ("No file dialog is available (kdialog / zenity) -- type the file "
                "name."), False

    def execute(self, ctx):
        return write_document(self.args[0])[1], True


class SaveAsCmd(SaveCmd):
    name = "SaveAs"
    description = "Save the model under a new name; the model becomes that file."

    def begin(self, ctx):
        status, path = self._ask_save_path("Save As")
        if status == filedialog.OK:
            return write_document(path)[1], True
        if status == filedialog.CANCEL:
            return f"{self.name} cancelled", True
        return ("No file dialog is available (kdialog / zenity) -- type the file "
                "name."), False


class NewCmd(_FileCommand):
    name = "New"
    description = "Start a new, empty model."
    steps = []

    def begin(self, ctx):
        stop = self._settle_unsaved("New")
        if stop is not None:
            return stop
        from .model import NarwhalModel

        app = _app()
        app.set_model(NarwhalModel())
        app.doc_path = None
        app.mark_saved()
        _rebuild_display()
        return ("New model (Untitled). Templates are not implemented yet -- this is "
                "the reference CAD application's \"No template\": the internal defaults."), True

    def execute(self, ctx):          # pragma: no cover - begin() always finishes
        return self.begin(ctx)


class OpenCmd(_FileCommand):
    name = "Open"
    description = "Open a .3dm file (replaces the current model)."
    steps = [("File name to open:", TEXT)]

    def begin(self, ctx):
        stop = self._settle_unsaved("Open")
        if stop is not None:
            return stop
        app = _app()
        from . import import_commands

        filt = (("Supported files", " ".join(
                    ["*.3dm"] + [f"*{e}" for e in import_commands.FORMATS])),
                ("the reference CAD application 3D Models", "*.3dm"))
        status, path = filedialog.ask_open_path(
            self.name, filedialog.start_dir(app.doc_path), filt)
        if status == filedialog.OK:
            return self._open(path)
        if status == filedialog.CANCEL:
            return f"{self.name} cancelled", True
        return ("No file dialog is available (kdialog / zenity) -- type the file "
                "name."), False

    def execute(self, ctx):
        stop = self._settle_unsaved("Open")
        if stop is not None:
            return stop
        return self._open(self.args[0])

    def _open(self, path):
        from .state import NarwhalModel

        p = os.path.abspath(os.path.expanduser((path or "").strip().strip('"')))
        root, ext = os.path.splitext(p)
        if not ext:
            p += EXT
            ext = EXT
        app = _app()
        if ext.lower() != EXT:
            # A STEP / DXF / SVG / OBJ / STL file opens as a NEW Untitled model
            # (insert.htm#Open: "When the reference CAD application opens a non-3dm file ... When the model is
            # saved for the first time, this model name is entered as the file name").
            # The format-specific reader performs the import.
            from . import import_commands

            if ext.lower() not in import_commands.FORMATS:
                return (f"Opening {ext} files is not implemented yet -- .3dm, "
                        + ", ".join(import_commands.FORMATS)), True
            if not os.path.exists(p):
                return f"Could not open {p}: no such file", True
            model, msg = import_commands.open_foreign(p)
            if model is None:
                return msg, True
            app.set_model(model)
            app.doc_path = None
            app.mark_saved()
            app.suggested_name = root + EXT
            _rebuild_display()
            recent_add(p)
            return msg, True
        try:
            model = NarwhalModel.load(p)
        except FileNotFoundError as e:
            return f"Could not open {p}: {e}", True
        app.set_model(model)
        app.mark_saved(p)
        drawn = _rebuild_display()
        recent_add(p)
        return f"Opened {p} ({model.object_count()} objects, {drawn} drawn)", True


# ---------------------------------------------------------------------------
# EXIT, tied to the file -- and a recovery copy when prompting is impossible
# ---------------------------------------------------------------------------
#
# MEASURED: Blender's own quit prompt is about the .BLEND scene (its "Save" writes a
# .blend, not the model), and the window's close button cannot be intercepted from
# Python. So: Ctrl+Q and File > Exit run `Exit`, which asks "Save changes to <file>?"
# exactly as New/Open do; Blender's .blend prompt is switched off (it asks the wrong
# question); and on ANY exit -- including the window's close button, where nothing can
# be asked -- a model with unsaved changes is written to a RECOVERY copy
# (`write_recovery`, from the exit_pre handler), which the next start reports.

#: set by the app: actually quit. None headless (the command then says so).
quitter = None


# ---------------------------------------------------------------------------
# File > Recent
# ---------------------------------------------------------------------------

def _data_dir():
    return (os.environ.get("NARWHAL3D_DATA_DIR")
            or os.path.join(os.path.expanduser("~"), ".local", "share", "narwhal3d"))


def _recent():
    from . import recent_files

    r = recent_files.RecentFiles(os.path.join(_data_dir(), "recent.json"))
    r.load()
    return r


def recent_add(path):
    try:
        r = _recent()
        r.add(path)
        r.save()
    except Exception:  # noqa: BLE001 -- a recent list must never break a save
        pass


def recent_items():
    try:
        return list(_recent().items())
    except Exception:  # noqa: BLE001
        return []


def recovery_dir():
    """Where recovery copies go: $NARWHAL3D_DATA_DIR or
    ~/.local/share/narwhal3d/autosave."""
    return os.path.join(_data_dir(), "autosave")


def _recovery_dirs():
    """The canonical recovery location to inspect."""
    return [recovery_dir()]


def write_recovery():
    """Write the model to a recovery copy IF it has unsaved changes. Returns the path,
    or None (nothing to protect, or the write failed -- never raises at exit)."""
    from . import autosave

    app = _app()
    try:
        if not app.doc_modified() or not len(app.model.file.Objects):
            return None
        folder = recovery_dir()
        os.makedirs(folder, exist_ok=True)
        path = autosave.autosave_path(folder, app.doc_path)
        tmp = f"{path}.saving-{os.getpid()}.tmp"
        if not app.model.save(tmp):
            return None
        os.replace(tmp, path)
        autosave.prune(folder, 10)          # OURS: keep the newest ten
        return path
    except Exception:  # noqa: BLE001
        return None


def recovery_notice():
    """One command-line line about recovery copies left by an earlier session, or None."""
    from . import autosave

    found = []
    for folder in _recovery_dirs():
        try:
            found.extend(autosave.recovery_candidates(folder))
        except Exception:  # noqa: BLE001
            continue
    if not found:
        return None
    found.sort(key=lambda pm: pm[1], reverse=True)
    newest = found[0][0]
    return (f"Unsaved work from an earlier session was kept: {newest} "
            f"({len(found)} recovery file(s)) -- Open it to continue")


class ExitCmd(_FileCommand):
    name = "Exit"
    description = "Close the program (asks to save changes first)."
    steps = []

    def begin(self, ctx):
        stop = self._settle_unsaved("Exit")
        if stop is not None:
            return stop
        if quitter is None:
            return "Exit: nothing to close (headless)", True
        _app().mark_saved()            # settled: the exit handler must not re-save
        quitter()
        return "Exiting", True

    def execute(self, ctx):          # pragma: no cover - begin() always finishes
        return self.begin(ctx)


#: the commands this module provides, keyed as the registry keys them
PROVIDED = (SaveCmd, SaveAsCmd, NewCmd, OpenCmd, ExitCmd)
for _cls in PROVIDED:
    _inst = _cls()
    commands.COMMANDS[_inst.name.lower()] = _inst
