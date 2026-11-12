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
EXT = ".narwhal"
INTERCHANGE_EXT = ".3dm"
NATIVE_PROJECT_FILTER = ("Narwhal 3D Projects", "*.narwhal")


def _app():
    from .state import app_state

    return app_state


def _load_guard():
    app = _app()
    undo = app.ctx.undo
    return dict(model=app.model._object_registry.native.handle, context=undo.native_identity, serial=int(undo.top_serial or 0),
                state=int(undo.state_id), generation=int(undo.generation) if undo.uncertain else None,
                path=str(app.doc_path) if app.doc_path is not None else None)


class _LoadHost:
    """Blender scheduling and opaque payload lifetimes; Rust decides every action."""
    def __init__(self, scheduler, changed, paused=None):
        from .native_document import LoadJobs
        self.native = LoadJobs()
        self.scheduler, self.changed = scheduler, changed
        self.host_paused = paused or (lambda:False)
        self.payloads = {}
        self.polling, self.closed = False, False

    def _dispose(self, job, notify=True):
        item = self.payloads.pop(job, None)
        if item is not None:
            if item['hydration'] is not None:
                item['hydration'].close()
            if item['prepared'] is not None:
                item['prepared'].cleanup()
            if notify and item['done'] is not None:
                item['done']()

    def request(self, path, apply, strict, done):
        if self.closed:
            raise RuntimeError('Native background loading has stopped; restart the application')
        reply = self.native.request('request', guard=_load_guard(), strict=strict)
        if reply['replaced'] is not None:
            self._dispose(reply['replaced'])
        job = reply['job']
        self.payloads[job] = dict(path=os.path.abspath(os.fspath(path)), apply=apply,
                                  done=done, prepared=None, hydration=None, candidate=None, error=None)
        if not self.polling:
            self.polling = True
            try:
                self.scheduler(self.poll)
            except BaseException:
                self.close()
                raise
        self.changed()
        return True, f"Loading {os.path.basename(os.fspath(path))}…"

    def _work(self, job, item):
        from . import narwhal_document
        try:
            item['prepared'] = narwhal_document.prepare_load(item['path'])
        except Exception as exc:
            item['error'] = exc
        try:
            reply = self.native.request('complete', job=job, success=item['error'] is None)
            if reply['action'] == 'discard' or self.closed:
                self._dispose(job, notify=False)
        except Exception as exc:
            item['error'] = exc
            if self.closed:
                self._dispose(job, notify=False)

    def poll(self):
        if self.closed:
            return None
        app = _app()
        from . import save_queue
        try:
            reply = self.native.request('tick', guard=_load_guard(),
                paused=bool(app.active_command is not None or app.ctx.undo.recording or save_queue.busy() or self.host_paused()))
            action, job = reply['action'], reply['job']
            if action == 'start':
                import threading
                item = self.payloads[job]
                try:
                    threading.Thread(target=self._work,args=(job,item),name='narwhal-load',daemon=True).start()
                except Exception as exc:
                    item['error'] = exc
                    self.native.request('complete',job=job,success=False)
            elif action == 'hydrate':
                from . import narwhal_document
                item = self.payloads[job]
                try:
                    if item['hydration'] is None:
                        item['hydration'] = narwhal_document.begin_load(item['prepared'])
                    candidate = item['hydration'].step()
                    if candidate is not None:
                        item['candidate'] = candidate
                        self.native.request('hydrated',job=job,success=True)
                except Exception as exc:
                    item['error'] = exc
                    self.native.request('hydrated',job=job,success=False)
            elif action in ('commit', 'error', 'cancel'):
                item = self.payloads[job]
                try:
                    if action == 'commit':
                        item['prepared'].check_source()
                        message = item['apply'](item['candidate'],item['path'])
                        if message:
                            app.log(message)
                    elif action == 'error':
                        app.log(f"Could not load {item['path']}: {item['error']}")
                    else:
                        app.log('Load cancelled: superseded, or the current document changed')
                except Exception as exc:
                    app.log(f"Could not load {item['path']}: {exc}")
                finally:
                    self._dispose(job)
                self.changed()
            elif action == 'idle':
                self.polling = False
                return None
        except Exception as exc:
            app.log(f'Background load failed: {exc}; no request was replayed')
            self.changed()
            try:
                self.close()
            except Exception:
                pass
            return None
        return .05

    def close(self):
        if self.closed:
            return
        self.closed = True
        reply = self.native.request('close')
        for job in (reply['replaced'],reply['job']):
            if job is not None:
                self._dispose(job)


_load_host = None


def install_background_loading(scheduler, changed, paused=None):
    global _load_host
    if _load_host is None or _load_host.closed:
        _load_host = _LoadHost(scheduler,changed,paused)


def background_loading_enabled():
    # An installed-but-failed native path must refuse, not fall back to sync loading.
    return _load_host is not None


def background_load(path, apply, *, strict=True, done=None):
    if not background_loading_enabled():
        raise RuntimeError('Native background loading is not installed')
    return _load_host.request(path,apply,strict,done)


def stop_background_loads():
    if _load_host is not None:
        _load_host.close()


def _normalise(path):
    """A typed/picked path -> an absolute native document path, or a refusal."""
    p = os.path.abspath(os.path.expanduser((path or "").strip().strip('"')))
    if not (path or "").strip():
        return None, "no file name given"
    root, ext = os.path.splitext(p)
    if not ext:
        p += EXT                     # OURS: Save defaults to Narwhal's exact project format
    elif ext.lower() != EXT:
        return None, (f"{os.path.basename(p)!r}: saving as {ext} is not "
                      f"implemented yet -- use {EXT}, or Export to .3dm .step .ai .pdf .svg")
    return p, None


def _export_route(path):
    """Return `.3dm` for the one document interchange route accepted by Save As."""
    ext = os.path.splitext((path or "").strip().strip('"'))[1].lower()
    return ext if ext == INTERCHANGE_EXT else None


def write_document(path, background=False):
    """Write the model to `path` (atomically). Returns ``(ok, message)``.

    `.3dm` writes a whole-document interchange copy without replacing the active native
    project. Other formats belong to Export or Print and are refused here rather than
    silently discarding unsupported document content.

    ``background=True`` (Save / SaveAs in the app) hands a native save to `save_queue`
    when the app installed it: the message is then "Saving ..." and the result is logged
    when the save finishes. Otherwise -- and always for New/Open/Exit's "Save changes?"
    -- the save completes before this returns, after any background save has finished.
    """
    from . import save_queue
    app = _app()
    if _export_route(path):
        import tempfile
        target = os.path.abspath(os.path.expanduser(path.strip().strip('"')))
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix='.narwhal-interchange-', suffix='.3dm',
                                             dir=os.path.dirname(target))
            os.close(fd)
            if not app.model.save(temporary) or os.path.getsize(temporary) == 0:
                raise OSError('the document writer reported failure')
            os.replace(temporary, target)
        except Exception as exc:  # File/adapter boundary; the previous destination remains intact.
            return False, f"could not save {target}: {exc}"
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        return True, f"Exported {app.model.object_count()} object(s) to {target} -- the model's own file is unchanged"
    target, err = _normalise(path)
    if err:
        return False, err
    folder = os.path.dirname(target)
    if not os.path.isdir(folder):
        return False, f"the folder {folder!r} does not exist -- nothing was saved"
    if background and save_queue.enabled():
        return save_queue.request_save(target)
    save_queue.drain()               # never write alongside a background save
    try:
        from . import narwhal_document
        ok = narwhal_document.save_model(app.model, target)
        if not ok or not os.path.exists(target) or os.path.getsize(target) == 0:
            raise OSError("the native document writer reported failure")
    except Exception as e:  # noqa: BLE001
        return False, f"could not save {target}: {e}"
    app.model.path = target
    app.mark_saved(target)
    app.suggested_name = None
    recent_add(target)
    for note in getattr(app.model, "last_save_warnings", None) or ():
        app.log(f"Save note: {note}")
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
        from . import export_commands

        app = _app()
        start = (app.doc_path or getattr(app, "suggested_name", None)
                 or os.path.join(filedialog.start_dir(), "Untitled" + EXT))
        interchange = tuple(
            item for item in export_commands.EXPORT_FILTERS if item[1] == "*.3dm")
        filters = (NATIVE_PROJECT_FILTER, *interchange)
        return filedialog.ask_save_path(title, start, filters)

    def _settle_unsaved(self, verb):
        """Before New/Open: ask about unsaved changes. Returns None to go on, or the
        ``(message, done)`` that ends the command."""
        app = _app()
        from . import save_queue

        if save_queue.busy():
            # A save in progress may already hold these changes: finish it first.
            app.log("Finishing the save in progress\u2026")
            save_queue.drain()
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
        from . import save_queue

        app = _app()
        # A SaveAs still in progress decides where this document's file now is.
        current = save_queue.intended_path() or app.doc_path
        if current:
            return write_document(current, background=True)[1], True
        status, path = self._ask_save_path(self.name)
        if status == filedialog.OK:
            return write_document(path, background=True)[1], True
        if status == filedialog.CANCEL:
            return f"{self.name} cancelled", True
        return filedialog.unavailable_hint(), False

    def execute(self, ctx):
        return write_document(self.args[0], background=True)[1], True


class SaveAsCmd(SaveCmd):
    name = "SaveAs"
    description = "Save the model under a new name; the model becomes that file."

    def begin(self, ctx):
        status, path = self._ask_save_path("Save As")
        if status == filedialog.OK:
            return write_document(path, background=True)[1], True
        if status == filedialog.CANCEL:
            return f"{self.name} cancelled", True
        return filedialog.unavailable_hint(), False


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
    description = "Open a native .narwhal project or import an interchange file as a new model."
    steps = [("File name to open:", TEXT)]

    def begin(self, ctx):
        stop = self._settle_unsaved("Open")
        if stop is not None:
            return stop
        app = _app()
        from . import import_commands

        filt = (("Supported files", " ".join(
                    ["*" + EXT] + [f"*{e}" for e in import_commands.FORMATS])),
                ("Narwhal 3D Projects", "*" + EXT),
                ("3DM Interchange", "*.3dm"))
        status, path = filedialog.ask_open_path(
            self.name, filedialog.start_dir(app.doc_path), filt)
        if status == filedialog.OK:
            return self._open(path)
        if status == filedialog.CANCEL:
            return f"{self.name} cancelled", True
        return filedialog.unavailable_hint(), False

    def execute(self, ctx):
        stop = self._settle_unsaved("Open")
        if stop is not None:
            return stop
        return self._open(self.args[0])

    def _open(self, path):
        p = os.path.abspath(os.path.expanduser((path or "").strip().strip('"')))
        root, ext = os.path.splitext(p)
        if not ext:
            p += EXT
            ext = EXT
        app = _app()
        if ext.lower() != EXT:
            # An interchange file opens as a NEW Untitled model
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
        if background_loading_enabled():
            try:
                return background_load(p, self._publish)[1], True
            except Exception as exc:
                return f"Could not start native load: {exc}", True
        try:
            from . import narwhal_document
            model = narwhal_document.load_model(p)
            return self._publish(model,p), True
        except (FileNotFoundError, narwhal_document.NarwhalDocumentError) as e:
            return f"Could not open {p}: {e}", True

    @staticmethod
    def _publish(model, p):
        from . import narwhal_document
        app = _app()
        narwhal_document.commit_model(app, model, p)
        if is_recovery_copy(p):
            # OURS: a recovery copy is not the user's file. Saving back into the recovery
            # folder would hide the work where pruning deletes it, so the document opens
            # Untitled and modified: Save asks where to keep it (named after the original).
            app.doc_path = None
            model.path = None
            app.suggested_name = os.path.join(filedialog.start_dir(), recovered_name(p) + EXT)
            app._saved_history_state = _NEVER_SAVED  # see AppState.doc_modified
            drawn = _rebuild_display()
            return (f"Opened the recovered copy {p} ({model.object_count()} objects, "
                    f"{drawn} drawn) -- Save asks where to keep it")
        drawn = _rebuild_display()
        recent_add(p)
        return f"Opened {p} ({model.object_count()} objects, {drawn} drawn)"


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


def _native_recovery_candidates(folder):
    """Native recovery files as ``(path, mtime)`` rows, newest sorting left to callers."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    found = []
    for name in names:
        if not name.lower().endswith(EXT):
            continue
        path = os.path.join(folder, name)
        try:
            if os.path.isfile(path):
                found.append((path, os.path.getmtime(path)))
        except OSError:
            continue
    return found


def _prune_native_recovery(folder, keep=10):
    rows = sorted(_native_recovery_candidates(folder), key=lambda row: row[1], reverse=True)
    for path, _mtime in rows[max(0, int(keep)):]:
        try:
            os.remove(path)
        except OSError:
            pass


def write_recovery(background=False):
    """Write the model to a recovery copy IF it has unsaved changes. Returns the path,
    or None (nothing to protect, or the write failed -- never raises at exit).

    ``background=True`` (AutoSave) starts the copy in `save_queue` when the app
    installed it and returns its path at once (the result is logged when it finishes),
    or False when a save is already running (try again later). Exit always writes
    synchronously."""
    from . import autosave

    app = _app()
    try:
        if not app.doc_modified():
            return None
        folder = recovery_dir()
        os.makedirs(folder, exist_ok=True)
        path = os.path.splitext(autosave.autosave_path(folder, app.doc_path))[0] + EXT
        if background:
            from . import save_queue

            if save_queue.enabled():
                return path if save_queue.request_autosave(path) else False
        from . import narwhal_document
        if not narwhal_document.save_model(app.model, path):
            return None
        autosave.prune(folder, 10)          # retain legacy .3dm recovery compatibility
        _prune_native_recovery(folder, 10)  # OURS: keep the newest ten native projects
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
            found.extend(_native_recovery_candidates(folder))
        except Exception:  # noqa: BLE001
            continue
    if not found:
        return None
    found.sort(key=lambda pm: pm[1], reverse=True)
    newest = found[0][0]
    return (f"Unsaved work from an earlier session was kept: {newest} "
            f"({len(found)} recovery file(s)) -- Open it to continue")


def finish_saves(timeout=None):
    """Exit: finish any background save synchronously (bounded by `timeout` seconds).
    True when none is left running."""
    from . import save_queue

    return save_queue.drain(timeout)


#: never equal to a history state id: a recovered document is unsaved
_NEVER_SAVED = object()


def is_recovery_copy(path):
    """True for a file inside the recovery folder."""
    try:
        return (os.path.dirname(os.path.realpath(path))
                == os.path.realpath(recovery_dir()))
    except (OSError, ValueError):
        return False


def recovered_name(path):
    """The document name a recovery copy was written for (see `autosave.autosave_path`):
    ``part_autosave.narwhal`` -> ``part``; ``Untitled_autosave_<stamp>`` -> ``Untitled``."""
    base = os.path.splitext(os.path.basename(path))[0]
    if base.startswith("Untitled_autosave_"):
        return "Untitled"
    if base.endswith("_autosave") and len(base) > len("_autosave"):
        return base[:-len("_autosave")]
    return base


# ---------------------------------------------------------------------------
# Crash detection: a session marker per running app, removed by a clean exit
# ---------------------------------------------------------------------------
#
# OURS: `begin_session` writes `session-<pid>.json` (pid, start time, the OS's own
# process start stamp) in the recovery folder; the exit handler's `end_session` removes
# it. A marker whose process is gone -- or whose pid now belongs to a different process
# -- is a session that ended without a clean exit. Several apps may run at once, so
# each has its own marker and a LIVE session's marker is never touched.

_SESSION_PREFIX = "session-"


def _session_marker(pid=None):
    return os.path.join(recovery_dir(), f"{_SESSION_PREFIX}{pid or os.getpid()}.json")


def _process_start(pid):
    """The kernel's start stamp for `pid` (Linux /proc), or None where unavailable."""
    try:
        with open(f"/proc/{int(pid)}/stat", "rb") as handle:
            data = handle.read()
        return int(data.rsplit(b")", 1)[1].split()[19])   # field 22: starttime
    except (OSError, ValueError, IndexError):
        return None


def _pid_alive(pid):
    if pid <= 0:
        return False
    if os.name == "nt":
        # os.kill on Windows TERMINATES the process -- query it instead.
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5                # access denied: it exists
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) \
                and code.value == 259                           # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _session_alive(info):
    pid = int(info.get("pid", 0))
    if not _pid_alive(pid):
        return False
    recorded = info.get("proc_start")
    current = _process_start(pid)
    # A reused pid has a different start stamp: that session is gone.
    return recorded is None or current is None or recorded == current


def begin_session():
    """Record this session; return the markers (dicts) of earlier sessions that ended
    WITHOUT a clean exit. Their markers are removed, so each crash is reported once.
    Never raises."""
    import json
    import time

    crashed = []
    try:
        folder = recovery_dir()
        os.makedirs(folder, exist_ok=True)
        own = os.path.abspath(_session_marker())
        for name in os.listdir(folder):
            if not (name.startswith(_SESSION_PREFIX) and name.endswith(".json")):
                continue
            path = os.path.join(folder, name)
            try:
                with open(path, encoding="utf-8") as handle:
                    info = json.load(handle)
                info = {"pid": int(info["pid"]), "started": float(info["started"]),
                        "proc_start": info.get("proc_start")}
            except (OSError, ValueError, KeyError, TypeError):
                continue        # unreadable: never guess a crash from it
            if _session_alive(info):
                continue        # a live app (this one, or another window)
            crashed.append(info)
            if os.path.abspath(path) != own:
                try:
                    os.remove(path)
                except OSError:
                    pass
        marker = {"pid": os.getpid(), "started": time.time(),
                  "proc_start": _process_start(os.getpid())}
        temporary = own + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(marker, handle)
        os.replace(temporary, own)
    except Exception:  # noqa: BLE001 -- crash detection must never stop the app starting
        pass
    return crashed


def end_session():
    """The clean-exit half: remove this session's marker. Never raises."""
    try:
        os.remove(_session_marker())
    except OSError:
        pass


def crash_recovery_candidate(crashed):
    """The newest recovery copy written during a crashed session, as ``(path, mtime)``,
    or None. OURS: copies older than every crashed session belong to an earlier one and
    are left to the command-line notice."""
    from . import autosave

    if not crashed:
        return None
    since = min(info["started"] for info in crashed) - 1.0
    found = []
    for folder in _recovery_dirs():
        try:
            found.extend(autosave.recovery_candidates(folder))
            found.extend(_native_recovery_candidates(folder))
        except Exception:  # noqa: BLE001
            continue
    found = [row for row in found if row[1] >= since]
    if not found:
        return None
    return max(found, key=lambda row: row[1])


def offer_crash_recovery(crashed):
    """After an unclean exit: ask to open the newest recovery copy of that session.

    Returns "opened", "declined", "logged" (no dialog possible) or None (nothing to
    offer). Opening goes through the Open command, like File > Open."""
    import datetime

    found = crash_recovery_candidate(crashed)
    if found is None:
        return None
    path, mtime = found
    app = _app()
    name = recovered_name(path)
    when = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
    question = (f"Narwhal 3D closed unexpectedly. Open the recovered copy of {name} "
                f"(autosaved {when})?")
    if os.environ.get("NARWHAL3D_NO_DIALOGS"):
        app.log(f"{question} -- it is {path}; Open it to continue")
        return "logged"
    status, answer = filedialog.ask_yes_no_cancel("Narwhal 3D", question)
    if status == filedialog.UNAVAILABLE:     # headless: nobody to ask
        app.log(f"{question} -- it is {path}; Open it to continue")
        return "logged"
    if status == filedialog.OK and answer == "yes":
        app.start_command("Open", path)
        return "opened"
    return "declined"


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
