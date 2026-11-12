"""Background Save and AutoSave: the UI keeps running while a save is validated.

MEASURED on a user's 33-object project: a Save of new/changed solids froze the UI for
30-45 s ("stall 43.7 s" after SaveAs, "stall 35.7 s" after an autosave). Nearly all of
that is the exact-geometry validation worker (a subprocess) and the container pack (a
subprocess). Neither reads the live model, so they run on a background thread here,
while everything that touches the model stays on the main thread:

  1. STAGE (main thread, `narwhal_document._stage`): masters, document.3dm and the
     manifest are written to a private directory. Edits made after this point can never
     leak into the file.
  2. VALIDATE + PACK (background thread, `narwhal_document._validate_and_pack`). The
     thread only waits on subprocesses, which releases the GIL. `pack` writes the
     target atomically, so a target is never partial.
  3. FINISH (main thread, from `poll`): the document is marked saved AS OF THE STAGED
     STATE -- if it was edited meanwhile it stays modified. A refusal is repaired by
     `narwhal_document._recover_from_refusal` on the current model, exactly as
     `save_model` does, then re-staged and validated again in the background.

Rust application-core owns request order: one active save and one newest pending
user save. Busy AutoSave declines natively. Python retains only opaque host payloads,
Blender scheduling and exact-kernel repair callbacks. There is no Python queue policy.
The original Python coordinator is backed up outside application imports.
AutoSave never queues behind a user save (the caller retries later). EXIT stays
synchronous: `drain` finishes the in-flight save (bounded) on the calling thread.

Headless (no `install`) nothing here runs: `file_commands` saves synchronously.
No `bpy`: the UI passes a scheduler (`bpy.app.timers.register`), tests pass their own.
"""
from __future__ import annotations

import os
import threading
import time

USER = "save"
AUTOSAVE = "autosave"
#: seconds between main-thread checks of a running save
POLL_INTERVAL = 0.1
#: OURS: a save re-staged because objects it refused were deleted meanwhile -- bounded.
MAX_RESTAGES = 3

#: callable(fn): call `fn()` on the main thread until it returns None. None = disabled.
_scheduler = None
_polling = False
_job = None
_coordinator = None
_payloads = {}
#: optional UI hook (redraw the command line) after a save starts or finishes
on_change = None


def _app():
    from .state import app_state

    return app_state


def install(scheduler):
    """Enable background saving; `scheduler(fn)` must call `fn` on the main thread
    repeatedly (every returned interval) until it returns None."""
    global _scheduler, _polling, _coordinator
    if _coordinator is None:
        from .native_document import SaveJobs
        _coordinator = SaveJobs()
    _scheduler = scheduler
    _polling = False


def uninstall():
    """Disable background saving (tests). Any save in flight is finished first."""
    global _scheduler, _coordinator
    drain()
    if _coordinator is not None:
        _coordinator.request('close')
        _coordinator = None
    _scheduler = None


def enabled():
    return _scheduler is not None


def busy():
    return _coordinator is not None and _coordinator.request('info')['action']=='busy'


def intended_path():
    """Where the newest queued or running USER save will write, or None."""
    if _coordinator is None:
        return None
    job = _coordinator.request('intended')['job']
    return _payloads[job].target if job is not None else None


class _Job:
    def __init__(self, kind, target):
        self.kind = kind
        self.target = target
        self.model = None
        self.notes = []
        self.attempts = {}
        self.limits = {}
        self.restages = 0
        self.snapshot = None
        self.staged = None
        self.thread = None
        self.error = None
        self.defer_log = False
        self.lines = []


def _log(job, line):
    if job.defer_log:
        job.lines.append(line)
    else:
        _app().log(line)


def _changed():
    hook = on_change
    if hook is not None:
        try:
            hook()
        except Exception:  # noqa: BLE001 -- a redraw must never break a save
            pass


def _ensure_polling():
    global _polling
    if _polling or _scheduler is None:
        return
    _polling = True
    try:
        _scheduler(poll)
    except Exception:  # noqa: BLE001
        _polling = False
        raise


def _snapshot(app):
    """The app's saved-state fields as `AppState.mark_saved` would record them now."""
    try:
        undo = app.ctx.undo
        return (app.ctx, undo.top_serial, undo.state_id,
                undo.generation if undo.uncertain else None)
    except Exception:  # noqa: BLE001 -- unreadable history never certifies a save
        return None


def _mark_saved_as_staged(app, job):
    """Record the file as saved -- as of the STAGED state. Unchanged since staging: the
    ordinary `mark_saved`. Edited meanwhile: the saved markers point at the staged state
    (as `mark_saved` would have recorded them then), so the document stays modified,
    and undoing back to the staged state reads unmodified again."""
    if job.snapshot is not None and _snapshot(app) == job.snapshot:
        app.mark_saved(job.target)
        return True
    app.doc_path = job.target
    if job.snapshot is not None and job.snapshot[0] is app.ctx:
        # The same three fields `AppState.mark_saved` writes, captured at stage time.
        (_ctx, app._saved_serial, app._saved_history_state,
         app._saved_uncertain_generation) = job.snapshot
    return False


def request_save(target, kind=USER):
    """Save the current model to `target` (already a normalised absolute path) in the
    background. Returns ``(ok, message)`` for the command line; ok is False only when
    the save failed before it could go to the background. A save already running makes
    this the queued request (replacing any older queued one)."""
    if _coordinator is None:
        raise RuntimeError('Native background-save coordination is not installed')
    decision = _coordinator.request('enqueue',autosave=kind==AUTOSAVE)
    if decision['action']=='declined':
        return False, None
    job = _Job(kind, target)
    job.native_id = decision['job']
    _payloads[job.native_id] = job
    if decision['replaced'] is not None:
        _payloads.pop(decision['replaced'])
    name = os.path.basename(target)
    if decision['action']=='queued':
        _ensure_polling()
        return True, f"Save queued: {name} is saved when the save in progress finishes"
    job.defer_log = True
    _start(job)
    job.defer_log = False
    lines = job.lines
    job.lines = []
    if _job is not job and lines:
        # finished (refused) before it could go to the background
        for line in lines[:-1]:
            _app().log(line)
        return False, lines[-1]
    for line in lines:
        _app().log(line)
    return True, (f"Saving {name}\u2026" if kind == USER else None)


def request_autosave(target):
    """Start a background recovery copy. False when a save is already running (try
    again later); True once attempted (a failure is logged, as a sync autosave's is)."""
    ok, message = request_save(target, AUTOSAVE)
    return ok or message is not None


def _start(job):
    global _job
    from . import narwhal_document

    app = _app()
    _job = job
    job.model = app.model
    try:
        narwhal_document._prepare_for_save(job.model, job.notes)
    except Exception as exc:  # noqa: BLE001
        _fail(job, exc)
        return
    _stage_and_launch(job)
    _changed()


def _stage_and_launch(job):
    from . import narwhal_document

    while True:
        try:
            job.staged = narwhal_document._stage(job.model, job.target)
            break
        except narwhal_document.NarwhalDocumentError as exc:
            try:
                job.limits = narwhal_document._recover_from_refusal(
                    job.model, job.target, exc, job.notes, job.attempts, job.limits)
            except Exception as final:  # noqa: BLE001
                _fail(job, final)
                return
        except Exception as exc:  # noqa: BLE001
            _fail(job, exc)
            return
    job.snapshot = _snapshot(_app()) if _app().model is job.model else None
    job.error = None
    job.thread = threading.Thread(target=_run, args=(job,), name="narwhal-save", daemon=True)
    job.thread.start()
    _ensure_polling()


def _run(job):
    """Background thread: touches only the staged directory."""
    from . import narwhal_document

    try:
        narwhal_document._validate_and_pack(job.staged, **job.limits)
    except BaseException as exc:  # noqa: BLE001 -- reported on the main thread
        job.error = exc


def _missing_refused(job, exc):
    """True when the worker refused objects that were deleted after staging."""
    from . import narwhal_document

    for message in getattr(exc, "errors", None) or [str(exc)]:
        object_id, _kind = narwhal_document._refused_master(message)
        if object_id and job.model.file.Objects.FindId(object_id) is None:
            return True
    return False


def _complete(job):
    """Main thread: the background half has finished."""
    from . import narwhal_document

    job.thread = None
    if job.staged is not None:
        job.staged.cleanup()
        job.staged = None
    exc = job.error
    if exc is None:
        try:
            if not os.path.exists(job.target) or os.path.getsize(job.target) == 0:
                exc = OSError("the native document writer reported failure")
        except OSError as error:
            exc = error
    if exc is None:
        _succeed(job)
        return
    if not isinstance(exc, narwhal_document.NarwhalDocumentError):
        _fail(job, exc)
        return
    if job.model is not _app().model:
        _fail(job, narwhal_document.NarwhalDocumentError(
            f"{exc} (the document was closed before it could be repaired)"))
        return
    if _missing_refused(job, exc) and job.restages < MAX_RESTAGES:
        # Edited while it was being validated: what was refused no longer exists.
        job.restages += 1
        _stage_and_launch(job)
        return
    try:
        job.limits = narwhal_document._recover_from_refusal(
            job.model, job.target, exc, job.notes, job.attempts, job.limits)
    except Exception as final:  # noqa: BLE001
        _fail(job, final)
        return
    _stage_and_launch(job)


def _succeed(job):
    from . import file_commands

    app = _app()
    if job.kind == USER:
        unchanged = True
        if app.model is job.model:
            job.model.path = job.target
            unchanged = _mark_saved_as_staged(app, job)
            app.suggested_name = None
        file_commands.recent_add(job.target)
        job.model.last_save_warnings = job.notes
        for note in job.notes:
            _log(job, f"Save note: {note}")
        _log(job, f"Saved {job.target}")
        if not unchanged:
            _log(job, "Changes made while saving are not in that file yet -- Save again "
                      "to keep them.")
    else:
        folder = os.path.dirname(job.target)
        try:
            from . import autosave
            autosave.prune(folder, 10)
            file_commands._prune_native_recovery(folder, 10)
        except Exception:  # noqa: BLE001
            pass
        _log(job, f"Autosaved to {job.target}")
    _end(job)


def _fail(job, exc):
    if job.staged is not None:
        job.staged.cleanup()
        job.staged = None
    if job.kind == USER:
        _log(job, f"could not save {job.target}: {exc}")
    else:
        _log(job, f"Autosave failed: {exc}")
        print(f"[narwhal3d] autosave failed: {exc!r}")
    _end(job)


def _end(job):
    global _job
    if _job is job:
        _coordinator.request('finish',job=job.native_id)
        _payloads.pop(job.native_id)
        _job = None
    _changed()


def _start_pending():
    if _coordinator is None:
        return
    decision = _coordinator.request('next')
    if decision['action']=='start':
        job = _payloads[decision['job']]
        if job.kind == USER:
            _app().log(f"Saving {os.path.basename(job.target)}\u2026")
        _start(job)


def poll():
    """Main thread, from the scheduler: finish a completed save, start a queued one."""
    global _polling
    try:
        job = _job
        if job is not None and job.thread is not None and not job.thread.is_alive():
            _complete(job)
        if _job is None:
            _start_pending()
    except Exception as exc:  # noqa: BLE001 -- the poller must never die with a save open
        print(f"[narwhal3d] background save failed: {exc!r}")
        if _job is not None and (_job.thread is None or not _job.thread.is_alive()):
            _fail(_job, exc)
    if not busy():
        _polling = False
        return None
    return POLL_INTERVAL


def drain(timeout=None):
    """Finish every save on THIS (the main) thread: the running one, any repair and
    re-validation it needs, then a queued USER save. Rust never queues an AutoSave behind busy work. Returns False if `timeout` seconds pass first --
    the running save is then left to finish on its own; nothing is half-written."""
    deadline = None if timeout is None else time.monotonic() + timeout
    while busy():
        job = _job
        if job is None:
            _start_pending()
            continue
        if job.thread is None:          # cannot happen; never spin on it
            _end(job)
            continue
        remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
        job.thread.join(remaining)
        if job.thread.is_alive():
            return False
        _complete(job)
    return True
