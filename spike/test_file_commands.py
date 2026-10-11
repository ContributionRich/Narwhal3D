"""Headless-Blender tests for Save, SaveAs, New, Open, and Exit workflows.

A scripted file-dialog backend records each interaction so tests can assert both the
resulting .3dm files and whether a dialog or command-line prompt was requested.
"""
import os
import shutil
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import rhino3dm as r3  # noqa: E402

from narwhal3d import filedialog  # noqa: E402
from narwhal3d.state import app_state  # noqa: E402
from narwhal3d.model import NarwhalModel  # noqa: E402

results = []


def check(ok, label, detail=""):
    results.append((bool(ok), label, detail))


class Fake:
    """A scripted user. Each list is consumed in order; a call with nothing scripted
    is a test failure (the command asked something the test did not expect)."""

    def __init__(self, save=(), open_=(), ask=()):
        self.save_answers = list(save)
        self.open_answers = list(open_)
        self.ask_answers = list(ask)
        self.calls = []

    def _next(self, kind, queue):
        self.calls.append(kind)
        if not queue:
            raise AssertionError(f"unexpected {kind} dialog")
        return queue.pop(0)

    def save(self, title, start, filters):
        self.calls.append(("save-start", start))
        return self._next("save", self.save_answers)

    def open(self, title, start, filters):
        return self._next("open", self.open_answers)

    def ask(self, title, text):
        self.calls.append(("ask-title", title))
        self.calls.append(("ask-text", text))
        return self._next("ask", self.ask_answers)


def kinds(fake):
    return [c for c in fake.calls if isinstance(c, str)]


def fresh():
    app_state.set_model(NarwhalModel())
    app_state.doc_path = None
    app_state.mark_saved()
    app_state.active_command = None


def draw_line(a="w0,0,0", b="w10,0,0"):
    app_state.feed_line("Line")
    app_state.feed_line(a)
    app_state.feed_line(b)


def last():
    return app_state.history[-1] if app_state.history else ""


def objects_in(path):
    f = r3.File3dm.Read(path)
    return None if f is None else len(f.Objects)


TMP = tempfile.mkdtemp(prefix="narwhal3d_f_d_")
OK, CANCEL, UNAV = filedialog.OK, filedialog.CANCEL, filedialog.UNAVAILABLE


def run():
    # -- 0. Save on an untitled model prompts instead of writing implicitly ------
    fresh()
    draw_line()
    fake = Fake(save=[(CANCEL, None)])
    filedialog.set_backend(fake)
    app_state.feed_line("Save")

    check(kinds(fake) == ["save"] and "cancelled" in last(),
          "an Untitled model's Save ASKS for a name; cancelling saves nothing",
          f"{kinds(fake)} {last()!r}")
    check(app_state.doc_path is None and app_state.doc_modified(),
          "and the model stays Untitled and modified", f"{app_state.doc_path}")

    # -- 1. first Save: dialog -> file written, model is now that file ----------
    p1 = os.path.join(TMP, "part")              # no extension typed
    fake = Fake(save=[(OK, p1)])
    filedialog.set_backend(fake)
    app_state.feed_line("Save")
    want = p1 + ".3dm"
    check(os.path.exists(want) and objects_in(want) == 1,
          "Save writes the model, adding .3dm as the reference CAD application does", f"{last()!r}")
    check(app_state.doc_path == want and not app_state.doc_modified(),
          "the model is now that file, and unmodified", f"{app_state.doc_path}")
    check(not [f for f in os.listdir(TMP) if f.endswith(".tmp")],
          "no temporary file is left behind", f"{os.listdir(TMP)}")

    # -- 2. change -> modified; Save again writes back WITHOUT asking -----------
    draw_line("w0,5,0", "w10,5,0")
    check(app_state.doc_modified(), "drawing something marks the model modified")
    fake = Fake()                                # any dialog would fail the test
    filedialog.set_backend(fake)
    app_state.feed_line("Save")
    check(kinds(fake) == [] and objects_in(want) == 2 and not app_state.doc_modified(),
          "Save on a model that has a file writes straight back -- no dialog",
          f"{kinds(fake)} objects={objects_in(want)} {last()!r}")

    # -- 3. undo after saving -> modified; redo back -> unmodified ---------------
    app_state.feed_line("Undo")
    check(app_state.doc_modified(), "Undo after a save makes the model differ from "
          "its file (modified)")
    app_state.feed_line("Redo")
    check(not app_state.doc_modified(), "Redo back to the saved state reads "
          "unmodified again")

    # -- 4. SaveAs: always asks; the model BECOMES the new file -----------------
    p2 = os.path.join(TMP, "copy.3dm")
    fake = Fake(save=[(OK, p2)])
    filedialog.set_backend(fake)
    app_state.feed_line("SaveAs")
    starts = [c[1] for c in fake.calls if isinstance(c, tuple) and c[0] == "save-start"]
    check(kinds(fake) == ["save"] and objects_in(p2) == 2 and app_state.doc_path == p2,
          "SaveAs always asks, writes, and the model becomes the new file",
          f"{kinds(fake)} {app_state.doc_path}")
    check(starts and starts[0] == want,
          "the Save As dialog opens on the current file", f"{starts}")

    # -- 5. a format nobody writes: refused BY NAME, nothing written -----------
    # Use a format that is still unsupported; STEP is handled by Export.
    p3 = os.path.join(TMP, "part.dwg")
    fake = Fake(save=[(OK, p3)])
    filedialog.set_backend(fake)
    app_state.feed_line("SaveAs")
    check(not os.path.exists(p3) and "not implemented" in last()
          and app_state.doc_path == p2,
          "saving as .dwg is refused by name and nothing changes", f"{last()!r}")

    # -- 5b. Save As with an EXPORT extension exports the whole model; the
    # document keeps its own file ("it does not become your active the reference CAD application model")
    from narwhal3d import print2d, print_commands
    print_commands.view_provider = lambda: print2d.View(
        (1, 0, 0), (0, 1, 0), (0, 0, 0), 100.0, 80.0)
    p3b = os.path.join(TMP, "drawing.ai")
    fake = Fake(save=[(OK, p3b)])
    filedialog.set_backend(fake)
    app_state.feed_line("SaveAs")
    print_commands.view_provider = None
    check(os.path.exists(p3b) and app_state.doc_path == p2
          and "unchanged" in last(),
          "Save As .ai exports the model (2-D, 1:1) and the document keeps its file",
          f"{last()!r}")

    # -- 6. a folder that does not exist: refused, the document keeps its file ---
    fake = Fake(save=[(OK, os.path.join(TMP, "nope", "x.3dm"))])
    filedialog.set_backend(fake)
    app_state.feed_line("SaveAs")
    check("does not exist" in last() and app_state.doc_path == p2,
          "an impossible path is reported; the model keeps its file", f"{last()!r}")

    # -- 7. no dialog tool: Save PROMPTS on the command line --------------------
    fresh()
    draw_line()
    filedialog.set_backend(type("None_", (), {
        "save": lambda *a: (UNAV, None), "open": lambda *a: (UNAV, None),
        "ask": lambda *a: (UNAV, None)})())
    app_state.feed_line("Save")
    prompting = app_state.active_command is not None
    p4 = os.path.join(TMP, "typed.3dm")
    app_state.feed_line(p4)
    check(prompting and objects_in(p4) == 1 and app_state.doc_path == p4,
          "with no dialog available Save asks for the name on the command line",
          f"prompting={prompting} {last()!r}")

    # -- 7b. no dialog + unsaved changes: New REFUSES rather than discard --------
    draw_line("w0,9,0", "w3,9,0")
    app_state.feed_line("New")
    check(len(app_state.model.file.Objects) == 2 and "unsaved" in last(),
          "New with unsaved changes and no way to ask does NOT discard the model",
          f"{last()!r}")

    # -- 8. New with unsaved changes: Cancel keeps everything -------------------
    fake = Fake(ask=[(CANCEL, None)])
    filedialog.set_backend(fake)
    app_state.feed_line("New")
    asked = [c[1] for c in fake.calls if isinstance(c, tuple) and c[0] == "ask-text"]
    titles = [c[1] for c in fake.calls if isinstance(c, tuple) and c[0] == "ask-title"]
    check(len(app_state.model.file.Objects) == 2 and "cancelled" in last()
          and asked and "typed.3dm" in asked[0] and titles == ["Narwhal 3D"],
          "New asks under the Narwhal 3D title; Cancel keeps the model",
          f"titles={titles} asked={asked} {last()!r}")

    # -- 9. New, answer No: empty Untitled model, nothing written ----------------
    before = objects_in(p4)
    fake = Fake(ask=[(OK, "no")])
    filedialog.set_backend(fake)
    app_state.feed_line("New")
    check(len(app_state.model.file.Objects) == 0 and app_state.doc_path is None
          and objects_in(p4) == before and not app_state.doc_modified(),
          "New, No: an empty Untitled model; the file on disk is untouched",
          f"objs={len(app_state.model.file.Objects)} {app_state.doc_path}")

    # -- 10. New, answer Yes on an Untitled model: asks for a name, saves, new ---
    draw_line()
    p5 = os.path.join(TMP, "before_new.3dm")
    fake = Fake(ask=[(OK, "yes")], save=[(OK, p5)])
    filedialog.set_backend(fake)
    app_state.feed_line("New")
    check(kinds(fake) == ["ask", "save"] and objects_in(p5) == 1
          and len(app_state.model.file.Objects) == 0,
          "New, Yes: saves (asking for a name), THEN starts the new model",
          f"{kinds(fake)} {last()!r}")

    # -- 11. Open: loads the file; the model is that file, unmodified ------------
    fake = Fake(open_=[(OK, p2)])
    filedialog.set_backend(fake)
    import gc
    gc_calls = []
    real_gc = {n: getattr(gc, n) for n in ("unfreeze", "collect", "freeze")}
    for n_, f_ in real_gc.items():
        setattr(gc, n_, (lambda n__, f__: lambda *a, **k: (gc_calls.append(n__),
                                                          f__(*a, **k))[1])(n_, f_))
    try:
        app_state.feed_line("Open")
    finally:
        for n_, f_ in real_gc.items():
            setattr(gc, n_, f_)
    check(len(app_state.model.file.Objects) == 2 and app_state.doc_path == p2
          and not app_state.doc_modified(),
          "Open loads the file; it is the current file and unmodified",
          f"{last()!r}")
    # Large imported models can leave hundreds of thousands of long-lived Python objects,
    # making later full garbage collections land in drag and undo frames. Open freezes them after
    # collecting what the previous document left (unfreeze first, or it stays stranded).
    check(gc_calls[-3:] == ["unfreeze", "collect", "freeze"] and gc.get_freeze_count() > 0,
          "Open settles the heap: the last document's objects unfrozen and collected, "
          "the new one's frozen out of later collections", f"{gc_calls}")

    # -- 12. Open with unsaved changes asks first; Cancel leaves everything -----
    draw_line("w0,20,0", "w5,20,0")
    fake = Fake(ask=[(CANCEL, None)])
    filedialog.set_backend(fake)
    app_state.feed_line("Open")
    check(kinds(fake) == ["ask"] and len(app_state.model.file.Objects) == 3,
          "Open with unsaved changes asks first; Cancel opens nothing",
          f"{kinds(fake)}")

    # -- 12b. headless (`blender -b`): NO real dialog may open ------------------
    # MEASURED: the e2e suite's Open/Save reached the real kdialog from a background
    # run; it exited at once as a cancel, so Open silently did nothing. With no
    # backend forced, a background process must report no dialog -> the command
    # line prompt, which is what scripted input expects.
    filedialog.set_backend(None)
    check(filedialog.headless() and filedialog.backend() is None,
          "under blender -b there is no dialog backend (never a real dialog)",
          f"headless={filedialog.headless()} backend={filedialog.backend()}")
    fresh()
    draw_line()
    app_state.feed_line("Save")
    p6 = os.path.join(TMP, "headless.3dm")
    app_state.feed_line(p6)
    check(objects_in(p6) == 1, "and Save then takes the typed name", f"{last()!r}")

    # -- 14. Exit is tied to the file ----------------------------------------
    from narwhal3d import file_commands

    # Public data and recovery paths use only the Narwhal 3D namespace.
    saved_paths = {name: os.environ.get(name) for name in (
        "HOME", "NARWHAL3D_DATA_DIR")}
    try:
        home = os.path.join(TMP, "identity-home")
        os.environ["HOME"] = home
        os.environ.pop("NARWHAL3D_DATA_DIR", None)
        new_data = os.path.join(home, ".local", "share", "narwhal3d")
        recovery = os.path.join(new_data, "autosave")
        check(file_commands._data_dir() == new_data
              and file_commands.recovery_dir() == recovery,
              "default data and recovery paths use ~/.local/share/narwhal3d",
              f"data={file_commands._data_dir()} recovery={file_commands.recovery_dir()}")
        check(file_commands._recovery_dirs() == [recovery],
              "recovery scans only the canonical Narwhal 3D directory",
              f"directories={file_commands._recovery_dirs()}")

        override = os.path.join(TMP, "narwhal3d-data")
        os.environ["NARWHAL3D_DATA_DIR"] = override
        check(file_commands._data_dir() == override
              and file_commands._recovery_dirs() == [os.path.join(override, "autosave")],
              "$NARWHAL3D_DATA_DIR is the only data-directory override")
    finally:
        for name, value in saved_paths.items():
            os.environ.pop(name, None)
            if value is not None:
                os.environ[name] = value

    quits = []
    file_commands.quitter = lambda: quits.append(1)
    os.environ["NARWHAL3D_DATA_DIR"] = os.path.join(TMP, "data")
    try:
        fresh()
        draw_line()
        fake = Fake(ask=[(CANCEL, None)])
        filedialog.set_backend(fake)
        app_state.feed_line("Exit")
        check(kinds(fake) == ["ask"] and not quits and app_state.doc_modified(),
              "Exit with unsaved changes ASKS; Cancel keeps the program open",
              f"{kinds(fake)} quits={quits}")
        p7 = os.path.join(TMP, "exit_yes.3dm")
        fake = Fake(ask=[(OK, "yes")], save=[(OK, p7)])
        filedialog.set_backend(fake)
        app_state.feed_line("Exit")
        check(objects_in(p7) == 1 and quits == [1],
              "Exit, Yes: saves (asking for a name when Untitled), THEN quits",
              f"{kinds(fake)} quits={quits}")
        draw_line("w0,3,0", "w4,3,0")
        filedialog.set_backend(Fake(ask=[(OK, "no")]))
        app_state.feed_line("Exit")
        check(quits == [1, 1] and file_commands.write_recovery() is None,
              "Exit, No: quits, and the exit handler then writes NO recovery copy "
              "(the user chose to discard)", f"quits={quits}")
        fresh()
        filedialog.set_backend(Fake())                 # nothing may be asked
        app_state.feed_line("Exit")
        check(quits == [1, 1, 1], "Exit with nothing unsaved quits without asking")

        # the window's close button: no question is possible -> a recovery copy
        draw_line()
        rp = file_commands.write_recovery()
        check(rp is not None and objects_in(rp) == 1
              and rp.startswith(file_commands.recovery_dir()),
              "an exit with unsaved changes writes a RECOVERY copy (window close "
              "button: nothing can be asked)", f"{rp}")
        note = file_commands.recovery_notice() or ""
        check(rp and os.path.basename(rp) in note,
              "and the next start announces it on the command line", f"{note!r}")
        app_state.mark_saved()
        check(file_commands.write_recovery() is None,
              "a model with no unsaved changes writes no recovery copy")
    finally:
        file_commands.quitter = None
        os.environ.pop("NARWHAL3D_DATA_DIR", None)

    # -- 15. Open of a non-.3dm file -----------------------------------------
    os.environ["NARWHAL3D_DATA_DIR"] = os.path.join(TMP, "data2")
    try:
        from narwhal3d import dxf_writer
        dxf = os.path.join(TMP, "plate.dxf")
        dxf_writer.write_dxf(dxf, [{"type": "CIRCLE", "center": (0, 0, 0),
                                    "radius": 5.0, "layer": "0", "color": 7}])
        fresh()
        filedialog.set_backend(Fake(open_=[(OK, dxf)]))
        app_state.feed_line("Open")
        objs = app_state.model.file.Objects
        circ = objs[0].Geometry.TryGetCircle() if len(objs) else None
        check(len(objs) == 1 and circ is not None and abs(circ.Radius - 5.0) < 1e-9
              and app_state.doc_path is None and not app_state.doc_modified(),
              "Open of a .dxf opens it as a NEW Untitled model, exact circle",
              f"{len(objs)} {last()!r}")
        p8 = os.path.join(TMP, "plate.3dm")
        fake = Fake(save=[(OK, p8)])
        filedialog.set_backend(fake)
        app_state.feed_line("Save")
        starts = [c[1] for c in fake.calls if isinstance(c, tuple) and c[0] == "save-start"]
        check(starts == [p8] and objects_in(p8) == 1,
              "its first Save suggests the file's own name as .3dm (the reference CAD application's rule)",
              f"{starts}")
        from narwhal3d import file_commands as _fc
        rec = _fc.recent_items()
        check(rec[:2] == [p8, dxf], "File > Recent lists them newest first", f"{rec}")
        filedialog.set_backend(Fake(open_=[(OK, os.path.join(TMP, "x.dwg"))]))
        app_state.feed_line("Open")
        check("not implemented" in last(), "an unread format is refused by name",
              f"{last()!r}")
    finally:
        os.environ.pop("NARWHAL3D_DATA_DIR", None)

    # -- 13. the menus see the commands ----------------------------------------
    from narwhal3d import commands
    for key in ("save", "saveas", "new", "open", "exit"):
        check(commands.COMMANDS.get(key).__class__.__module__.endswith("file_commands"),
              f"the registry's {key!r} uses file_commands",
              f"{type(commands.COMMANDS.get(key))}")


try:
    run()
except Exception:  # noqa: BLE001
    results.append((False, "suite raised", traceback.format_exc()))
finally:
    filedialog.set_backend(None)
    shutil.rmtree(TMP, ignore_errors=True)

failed = [r for r in results if not r[0]]
for ok, label, detail in results:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok else ""))
print(f"\n{len(results) - len(failed)}/{len(results)} file-workflow checks passed")
if failed:
    raise SystemExit(1)
