"""Built-in `.narwhal` project save/load with exact analytic OCCT masters."""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import hashlib
import importlib
import json
import math
import os
import shutil
from pathlib import Path
import sys
import tempfile
import uuid

import rhino3dm as r3

from . import converter_process, kernel, native_document
from .kernel import occt_backend
from .document_compat import schema1_volume

FORMAT = "Narwhal 3D Document"
SCHEMA = 1
EXTENSION = ".narwhal"
MASTER_ENCODING = "OCCT BinTools"
MASTER_DIGEST_KEY = "Narwhal3D:NativeMasterSHA256"
MAX_MANIFEST = 16 * 1024 * 1024
MAX_MASTER = 256 * 1024 * 1024
MAX_MASTERS = 10_000
MAX_EXACT_TOTAL = 2 * 1024 * 1024 * 1024
MAX_SHAPE_SUMMARIES = 256
_shape_summaries = OrderedDict()
_SHAPE_TYPES = {
    "TopoDS_Shape", "TopoDS_Compound", "TopoDS_CompSolid", "TopoDS_Solid",
    "TopoDS_Shell", "TopoDS_Face", "TopoDS_Wire", "TopoDS_Edge", "TopoDS_Vertex",
}


class NarwhalDocumentError(RuntimeError):
    """A native document could not be saved or loaded without losing exactness."""


def _object_uuid(file_object) -> str:
    try:
        return str(uuid.UUID(str(file_object.Attributes.Id)))
    except (ValueError, AttributeError) as exc:
        raise NarwhalDocumentError("document object has no stable UUID") from exc


def _format_contract() -> tuple[str, int]:
    ocp = importlib.import_module("OCP")
    bin_tools = importlib.import_module("OCP.BinTools")
    current = bin_tools.BinTools_FormatVersion.BinTools_FormatVersion_CURRENT
    from .document_compat import writer_bintools_format
    return str(ocp.__version__), writer_bintools_format(int(current.value))


def _shape_summary_contract():
    """Process-local native/runtime and summary-source identity, never archive metadata."""
    root = Path(__file__).resolve().parent
    source = hashlib.sha256()
    for name in ("narwhal_document.py", "document_compat.py", "kernel/occt_backend.py"):
        source.update((root / name).read_bytes())
    from . import document_admission
    return ("schema1-v1", _format_contract(),
            document_admission.runtime_identity(), source.digest())


def _shape_record(file_object, shape, member: str, digest: str, *,
                  reuse_save_summary: bool = False) -> dict:
    """Measure a master; Save alone may reuse its freshly serialized SHA witness.

    The opt-in requires the digest of this shape's just-produced BinTools bytes.
    Arbitrary callers (including archive/load paths) always measure independently.
    Python only coordinates the native measurements; no geometry is approximated.
    """
    wrapper_type = type(shape).__name__
    key = None
    if reuse_save_summary:
        key = (_shape_summary_contract(), digest, wrapper_type, int(shape.ShapeType().value))
        cached = _shape_summaries.get(key)
        if cached is not None:
            _shape_summaries.move_to_end(key)
            return dict(deepcopy(cached), object_id=_object_uuid(file_object),
                        member=member, sha256=digest)
    if wrapper_type not in _SHAPE_TYPES or not occt_backend.is_valid(shape):
        raise NarwhalDocumentError(f"unsupported or invalid exact OCCT shape {wrapper_type!r}")
    face_types = {str(key): int(value)
                  for key, value in sorted(occt_backend.face_types(shape).items())}
    lower, upper = occt_backend.tight_bbox(shape)
    bounds = [float(value) for point in (lower, upper) for value in point]
    if not all(math.isfinite(value) for value in bounds):
        raise NarwhalDocumentError("exact OCCT master has non-finite bounds")
    airtight = bool(occt_backend.is_airtight(shape))
    volume = float(schema1_volume(shape)) if airtight else None
    if volume is not None and not math.isfinite(volume):
        raise NarwhalDocumentError("exact OCCT master has non-finite volume")
    summary = {
        "wrapper_type": wrapper_type,
        "shape_enum": int(shape.ShapeType().value),
        "airtight": airtight,
        "face_types": face_types,
        "bounds": bounds,
        "volume": volume,
    }
    if key is not None:
        _shape_summaries[key] = deepcopy(summary)
        _shape_summaries.move_to_end(key)
        while len(_shape_summaries) > MAX_SHAPE_SUMMARIES:
            _shape_summaries.popitem(last=False)
    return dict(summary, object_id=_object_uuid(file_object), member=member, sha256=digest)


#: Worker refusals that mean an object's construction RECIPE can no longer be proven to
#: be its master. The master is the geometry, so Save re-bases the recipe on it exactly.
_RECIPE_REFUSALS = ("recipe correspondence cannot be established",
                    "differs geometrically from its reconstruction recipe",
                    "differs from its reconstruction recipe", "volume differs from its recipe",
                    "recipe cannot be rebuilt", "has no reconstruction recipe")
#: Refusals that mean the DISPLAY proxy no longer matches the master (it only draws).
_PROXY_REFUSALS = ("does not match its triangulated proxy", "does not match its exact proxy",
                   "differs geometrically from its exact proxy",
                   "has no recognized document proxy form", "proxy cannot be validated",
                   "does not match proxy bounds")


def _refused_master(message):
    import re
    match = re.search(r"master ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}) ",
                      str(message))
    if match is None:
        return None, None
    text = str(message)
    if any(reason in text for reason in _PROXY_REFUSALS):
        return match.group(1), "proxy"
    if any(reason in text for reason in _RECIPE_REFUSALS):
        return match.group(1), "recipe"
    return match.group(1), None


def _rebase_recipe(geometry, master):
    """Make the exact master itself the recipe (`native_master`). Never approximates."""
    if not kernel.record_recipe(geometry, occt_backend.native_master_recipe(master)):
        raise NarwhalDocumentError("the exact master could not be recorded as its recipe")


def _rebuild_proxy(model, file_object, master):
    """A fresh display proxy derived from the master, keeping the object's identity and
    every user string. The proxy only draws; the master stays the geometry."""
    from . import bridge
    old = file_object.Geometry
    proxy = bridge._proxy_from_occt(master)
    form = proxy.GetUserString(kernel.DOCUMENT_FORM_KEY)
    for key, value in old.GetUserStrings():
        if key not in (kernel.DOCUMENT_FORM_KEY, kernel.MASTER_KEY):
            proxy.SetUserString(key, value)
    proxy.SetUserString(kernel.DOCUMENT_FORM_KEY, form)
    # Bind first, then hand it to the table -- the same order every producer uses.
    kernel.register_master(proxy, master)
    object_id = str(file_object.Attributes.Id)
    if not model.replace_geometry(object_id, proxy):
        raise NarwhalDocumentError("the display proxy could not be replaced")
    stored = model.file.Objects.FindId(object_id)
    if stored is None or kernel.master_of(stored.Geometry) is None:
        raise NarwhalDocumentError("the exact master could not be re-bound to its display copy")


def _replace_with(model, file_object, geometry):
    object_id = str(file_object.Attributes.Id)
    if not model.replace_geometry(object_id, geometry):
        raise NarwhalDocumentError("the document object could not be updated for saving")
    return model.file.Objects.FindId(object_id)


def _demote(model, file_object, why):
    """LAST RESORT for one object: keep its visible geometry, honestly marked as NOT
    exact (`kernel.drop_master` tags it mesh-derived), so the rest of the document still
    saves. Never claims exactness it no longer has."""
    geometry = file_object.Geometry.Duplicate()
    kernel.drop_master(geometry)
    _replace_with(model, file_object, geometry)
    name = file_object.Attributes.Name or file_object.Attributes.Id
    if kernel.representation_of(geometry) == kernel.ANALYTIC:
        # The document object is the exact openNURBS polysurface; only the separate
        # kernel solid (and its editable construction history) is set aside.
        return (f"{name}: saved as its exact NURBS polysurface; its kernel solid could not "
                f"be validated ({why}), so its construction history was not kept.")
    return (f"{name}: saved as display geometry only -- its exact solid could not be "
            f"validated ({why}). Undo and save again, or rebuild this object, to keep it exact.")


def _prepare_for_save(model, notes):
    """Repair declaration problems that would otherwise refuse the whole Save."""
    for file_object in list(model.file.Objects):
        geometry = file_object.Geometry
        problem = kernel.honesty_problem(geometry)
        master = kernel.master_of(geometry)
        declared = kernel.representation_of(geometry)
        mismatch = master is not None and declared != kernel.ANALYTIC_MASTER
        if problem is None and not mismatch:
            continue
        name = file_object.Attributes.Name or str(file_object.Attributes.Id)
        fixed = geometry.Duplicate()
        if master is None:
            recipe = kernel.recipe_of(geometry)
            try:
                master = occt_backend.from_recipe(recipe) if recipe else None
            except Exception:  # noqa: BLE001 -- an unrebuildable recipe cannot restore it
                master = None
        if master is not None:
            kernel.register_master(fixed, master)
            stored = _replace_with(model, file_object, fixed)
            if stored is not None and kernel.honesty_problem(stored.Geometry) is None:
                notes.append(f"{name}: exact solid re-attached before saving")
                continue
            file_object = stored or file_object
        notes.append(_demote(model, file_object, problem or "declaration mismatch"))


def _emergency_copy(model, target):
    """Write the whole document as an interchange copy beside `target` (recipes kept)."""
    base, _ext = os.path.splitext(os.path.abspath(os.fspath(target)))
    path = base + " (emergency copy).3dm"
    try:
        return path if model.save(path) and os.path.getsize(path) > 0 else None
    except Exception:  # noqa: BLE001
        return None


#: Worker failures that are about resources, not the document.
_RESOURCE_FAILURES = ("timed out", "timeout", "memory", "Killed", "signal", "MemoryError")


def save_model(model, target: str | os.PathLike) -> bool:
    """Atomically save ``model`` as a self-contained exact `.narwhal` container.

    Saving must never be blocked by one object. When the validator cannot prove an
    object's stored construction RECIPE or display PROXY matches its exact master, that
    object is repaired from the master (recipe re-based on the master's exact bytes, or
    proxy re-derived) and the whole archive is validated again. If an object's exact solid
    still cannot be validated, it is kept as its visible geometry, honestly marked as not
    exact, and reported -- the rest of the document saves exactly. A worker that runs out
    of time/memory is retried with larger limits. If the archive still cannot be written,
    an interchange copy is written beside it so no work is lost, and the error says where.
    """
    notes = []
    _prepare_for_save(model, notes)
    attempts = {}
    limits = {}
    while True:
        try:
            ok = _save_once(model, target, **limits)
        except NarwhalDocumentError as exc:
            limits = _recover_from_refusal(model, target, exc, notes, attempts, limits)
            continue
        model.last_save_warnings = notes
        return ok


def _recover_from_refusal(model, target, exc, notes, attempts, limits):
    """One step of Save's repair loop, on the CURRENT model (it mutates it, so it runs on
    the main thread). Returns the worker limits for the next attempt, or raises: `exc`
    itself, or an error naming the emergency interchange copy written beside `target`.
    Shared by `save_model` and the background save (`save_queue`)."""
    text = str(exc)
    # MEASURED: one worker run per refused object made a 4-object Gumball move
    # on a 44-object file take 180 s to save. The worker now reports every
    # refusal of a run, so all of them are repaired before ONE re-validation.
    refused = []
    for message in getattr(exc, "errors", None) or [text]:
        object_id, kind = _refused_master(message)
        file_object = model.file.Objects.FindId(object_id) if object_id else None
        if file_object is None:
            refused = None
            break
        refused.append((object_id, kind, file_object, message))
    if not refused:
        if not limits and any(word in text for word in _RESOURCE_FAILURES):
            notes.append("validation needed more time/memory; retried with larger limits")
            return {"timeout": 1800, "memory_mib": 8192}
        emergency = _emergency_copy(model, target)
        if emergency:
            raise NarwhalDocumentError(
                f"{text}. Your work was saved as an interchange copy: {emergency}") from exc
        raise exc
    for object_id, kind, file_object, message in refused:
        # A proxy rebuild replaces the table object; never repair a stale one.
        file_object = model.file.Objects.FindId(object_id) or file_object
        name = file_object.Attributes.Name or object_id
        master = kernel.master_of(file_object.Geometry)
        tried = attempts.setdefault(object_id, set())
        if master is not None and kind == "recipe" and "recipe" not in tried:
            _rebase_recipe(file_object.Geometry, master)
            notes.append(f"{name}: construction history re-based on its exact geometry "
                         f"(the old recipe could not be verified)")
        elif master is not None and kind == "proxy" and "proxy" not in tried:
            _rebuild_proxy(model, file_object, master)
            notes.append(f"{name}: display copy rebuilt from its exact geometry")
        elif "demote" not in tried:
            kind = "demote"
            notes.append(_demote(model, file_object, message.split(": ", 1)[-1][:200]))
        else:
            raise exc
        tried.add(kind)
    return limits


def _save_once(model, target: str | os.PathLike, *, timeout=600, memory_mib=4096) -> bool:
    staged = _stage(model, target)
    try:
        _validate_and_pack(staged, timeout=timeout, memory_mib=memory_mib)
    finally:
        staged.cleanup()
    return True


class StagedSave:
    """Every byte of one save, written to a private directory by `_stage`.

    Nothing in it refers back to the live model, so edits made after staging can never
    leak into the file, and `_validate_and_pack` may run it on another thread.
    """

    def __init__(self, directory, target, count):
        self._directory = directory
        self.root = Path(directory.name)
        self.target = target
        self.count = count

    def cleanup(self):
        try:
            self._directory.cleanup()
        except OSError:
            pass


def _stage(model, target: str | os.PathLike) -> StagedSave:
    """The main-thread half of a save: serialize every master, document.3dm and the
    manifest into a private directory. May rebase a recipe-less master's recipe (as
    Save always has); never validates (the slow part)."""
    target = os.path.abspath(os.fspath(target))
    folder = os.path.dirname(target)
    if not os.path.isdir(folder):
        raise NarwhalDocumentError(f"the folder {folder!r} does not exist")
    if not occt_backend.available():
        raise NarwhalDocumentError("the built-in OCCT kernel is unavailable")
    occt_version, bintools_format = _format_contract()
    model.normalize_order()  # established file-order normalization; logical state is unchanged
    directory = tempfile.TemporaryDirectory(prefix="narwhal-save-")
    try:
        root = Path(directory.name)
        (root / "masters").mkdir()
        records = []
        exact_total = 0
        object_ids = []
        digest_restore = []
        try:
            for file_object in model.file.Objects:
                object_id = _object_uuid(file_object)
                object_ids.append(object_id)
                geometry = file_object.Geometry
                problem = kernel.honesty_problem(geometry)
                if problem is not None:
                    raise NarwhalDocumentError(
                        f"{file_object.Attributes.Name or '(unnamed)'} cannot be saved: {problem}")
                declared = kernel.representation_of(geometry)
                master = kernel.master_of(geometry)
                if master is not None and declared != kernel.ANALYTIC_MASTER:
                    raise NarwhalDocumentError(
                        f"{file_object.Attributes.Name or '(unnamed)'} has an exact master "
                        f"but declares {declared!r}")
                if declared == kernel.ANALYTIC_MASTER and master is None:
                    raise NarwhalDocumentError(
                        f"{file_object.Attributes.Name or '(unnamed)'} claims an exact master "
                        "but none is registered")
                if master is None:
                    continue
                if kernel.recipe_of(geometry) is None:
                    # The master is the geometry; it becomes its own exact recipe.
                    _rebase_recipe(geometry, master)
                try:
                    data = bytes(occt_backend.shape_to_bytes(
                        master, triangles=False, format_version=bintools_format))
                except Exception as exc:  # noqa: BLE001
                    raise NarwhalDocumentError(
                        f"could not serialize exact master for "
                        f"{file_object.Attributes.Name or object_id}: {exc}") from exc
                if not data or len(data) > MAX_MASTER:
                    raise NarwhalDocumentError(
                        f"exact master {object_id} has unsupported size {len(data)}")
                exact_total += len(data)
                if exact_total > MAX_EXACT_TOTAL:
                    raise NarwhalDocumentError("exact master payloads exceed 2 GiB")
                digest = hashlib.sha256(data).hexdigest()
                member = f"masters/{object_id}.brep"
                (root / member).write_bytes(data)
                records.append(_shape_record(
                    file_object, master, member, digest, reuse_save_summary=True))
                geometry = file_object.Geometry
                getter = getattr(geometry, "GetUserString", None)
                setter = getattr(geometry, "SetUserString", None)
                if not callable(setter):
                    raise NarwhalDocumentError("exact proxy cannot store its native digest")
                old = getter(MASTER_DIGEST_KEY) if callable(getter) else None
                if setter(MASTER_DIGEST_KEY, digest) is False:
                    raise NarwhalDocumentError("exact proxy refused its native digest binding")
                digest_restore.append((geometry, old))
                if len(records) > MAX_MASTERS:
                    raise NarwhalDocumentError("document has more than 10,000 exact masters")
            if len(object_ids) != len(set(object_ids)):
                raise NarwhalDocumentError("document contains duplicate object UUIDs")
            document_path = root / "document.3dm"
            if not model.file.Write(str(document_path), 8):
                raise NarwhalDocumentError("the metadata document writer failed")
        finally:
            for geometry, old in digest_restore:
                try:
                    geometry.SetUserString(MASTER_DIGEST_KEY, old or "")
                except Exception:  # noqa: BLE001
                    pass
        manifest = {
            "format": FORMAT,
            "schema": SCHEMA,
            "summary_codec": "schema1-v1",
            "document_member": "document.3dm",
            "master_encoding": MASTER_ENCODING,
            "occt_version": occt_version,
            "bintools_format": bintools_format,
            "masters": records,
        }
        manifest_data = (json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(manifest_data) > MAX_MANIFEST:
            raise NarwhalDocumentError("native document manifest exceeds 16 MiB")
        (root / "manifest.json").write_bytes(manifest_data)
    except BaseException:
        directory.cleanup()
        raise
    return StagedSave(directory, target, len(records))


def _validate_and_pack(staged: StagedSave, *, timeout=600, memory_mib=4096) -> None:
    """The slow half of a save. Reads only the staged directory -- never the model -- so
    the background save runs it off the main thread (the worker wait releases the GIL)."""
    # Admit the staged geometry with the SAME reader checks before replacing any
    # existing project. A serialization/reader disagreement cannot destroy a good save.
    validated = _validate_in_worker(staged.root, staged.count, reuse_proofs=True,
                                    timeout=timeout, memory_mib=memory_mib,
                                    collect_errors=True)
    shutil.rmtree(validated)  # worker output is inside the private staging dir
    try:
        native_document.pack(staged.root, staged.target)
    except native_document.DocumentCoreError as exc:
        raise NarwhalDocumentError(f"native container writer failed: {exc}") from exc


def _read_manifest(path: Path) -> dict:
    try:
        if path.stat().st_size > MAX_MANIFEST:
            raise NarwhalDocumentError("native document manifest exceeds 16 MiB")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NarwhalDocumentError(f"invalid native document manifest: {exc}") from exc
    from .document_compat import supported_manifest
    _producer, bintools_format = _format_contract()
    if not supported_manifest(value, bintools_format):
        raise NarwhalDocumentError("unsupported native document schema or binary encoding")
    if len(value["masters"]) > MAX_MASTERS:
        raise NarwhalDocumentError("native document has more than 10,000 exact masters")
    return value


def _worker_argv(executable: Path, worker: Path, root: Path) -> tuple[str, ...]:
    if "blender" in executable.name.lower():
        return (str(executable), "-b", "--factory-startup", "--python-exit-code", "1",
                "--python", str(worker), "--", str(root))
    return (str(executable), "-P", str(worker), "--", str(root))


def _worker_command(root: Path) -> tuple[str, ...]:
    worker = Path(__file__).resolve().with_name("document_worker.py")
    executable = Path(sys.executable).resolve()
    # The validator needs OCCT/openNURBS, not a Blender UI/process. Stay within
    # this runtime's bundled Python; never select an arbitrary PATH interpreter.
    prefix = Path(sys.prefix).resolve()
    if sys.platform != 'darwin' and prefix.name.lower() == 'python':
        names = ('bin/python.exe', 'python.exe') if sys.platform == 'win32' else (f'bin/python{sys.version_info.major}.{sys.version_info.minor}',)
        bundled = next((prefix / name for name in names if (prefix / name).is_file()), None)
        if bundled is not None:
            return _worker_argv(bundled, worker, root)
    if "blender" not in executable.name.lower():
        project = Path(__file__).resolve().parents[1]
        candidates = (
            project.parent / "runtime" / "blender.exe",
            project.parent / "runtime" / "Blender.app" / "Contents" / "MacOS" / "Blender",
            project / "tools" / "blender-patched" / "blender",
            project / "tools" / "blender-5.2.2-linux-x64" / "blender",
        )
        executable = next((path for path in candidates if path.is_file()), executable)
    return _worker_argv(executable, worker, root)


def _validate_in_worker(root: Path, count: int, *, reuse_proofs=False, bounds_scope=None,
                        timeout=600, memory_mib=4096, collect_errors=False, report_out=None) -> Path:
    # Preserve the trusted launcher's kernel selection for recipe replay, not any
    # document-supplied path or the rest of the parent's (possibly secret) environment.
    # The document core too: MEASURED, without it the worker resolved a different
    # default binary, so its admission identity never matched the parent's and every
    # worker-measured bounds fact was discarded.
    extra_env = {name: os.environ[name]
                 for name in ("NARWHAL_GEOMETRY_CORE", "NARWHAL_DOCUMENT_CORE")
                 if os.environ.get(name)}
    if collect_errors:
        extra_env["NARWHAL_DOCUMENT_COLLECT_ERRORS"] = "1"
    from . import document_admission
    extra_env[document_admission.WORKER_IMAGES_ENV] = document_admission.worker_runtime_images()
    with document_admission.hints(reuse_proofs) as hint_env:
        extra_env.update(hint_env)
        result = converter_process.run(
            _worker_command(root), timeout=timeout, max_output=65536, memory_mib=memory_mib,
            extra_env=extra_env)
    marker = next((line for line in reversed(result.stdout.splitlines())
                   if line.startswith("NARWHAL-DOCUMENT-WORKER ")), None)
    report = None
    if marker is not None:
        try:
            report = json.loads(marker.split(" ", 1)[1])
        except json.JSONDecodeError as exc:
            raise NarwhalDocumentError("exact-master worker returned malformed output") from exc
        if isinstance(report, dict) and report.get("error"):
            error = NarwhalDocumentError(
                f"exact-master validation worker failed: {report['error']}")
            errors = report.get("errors")
            error.errors = ([str(item) for item in errors]
                            if isinstance(errors, list) else [str(report["error"])])
            raise error
    if result.returncode != 0 or result.timed_out or result.error or marker is None:
        detail = result.error or result.stderr[-2000:] or result.stdout[-2000:]
        raise NarwhalDocumentError(f"exact-master validation worker failed: {detail}")
    if not isinstance(report, dict) or report.get("count") != count:
        raise NarwhalDocumentError(f"exact-master worker rejected the document: {report}")
    output = root / "validated"
    if not output.is_dir() or output.is_symlink():
        raise NarwhalDocumentError("exact-master worker produced no safe output directory")
    document_admission.remember(output)
    if report_out is not None:
        report_out.update(report)
    if bounds_scope is not None:
        occt_backend.document_accept_bounds(bounds_scope, output, report.get("bounds_facts"))
    return output


class PreparedLoad:
    """Private, fully validated files; no live model or Blender handles cross threads."""
    def __init__(self, source):
        self.source = os.path.abspath(os.fspath(source))
        self.directory = tempfile.TemporaryDirectory(prefix="narwhal-open-")
        self.payload = Path(self.directory.name) / "payload"
        self.report = {}
        try:
            before = os.stat(self.source)
            self.stamp = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            if not occt_backend.available():
                raise NarwhalDocumentError("the built-in OCCT kernel is unavailable")
            try:
                native_document.unpack(self.source, self.payload)
            except native_document.DocumentCoreError as exc:
                raise NarwhalDocumentError(f"invalid native container: {exc}") from exc
            self.manifest = _read_manifest(self.payload / "manifest.json")
            self.records = self.manifest["masters"]
            self.record_by_id = {}
            expected_members = {"manifest.json", "document.3dm"}
            for record in self.records:
                if not isinstance(record, dict):
                    raise NarwhalDocumentError("native master record is not an object")
                try:
                    object_id = str(uuid.UUID(str(record["object_id"])))
                except (KeyError, ValueError) as exc:
                    raise NarwhalDocumentError("native master record has an invalid object UUID") from exc
                member, digest = record.get("member"), record.get("sha256")
                if (member != f"masters/{object_id}.brep" or object_id in self.record_by_id
                        or not isinstance(digest, str) or len(digest) != 64):
                    raise NarwhalDocumentError("native master mapping is duplicate or noncanonical")
                expected_members.add(member)
                self.record_by_id[object_id] = record
            actual_members = {path.relative_to(self.payload).as_posix()
                              for path in self.payload.rglob("*") if path.is_file()}
            if actual_members != expected_members:
                raise NarwhalDocumentError("native container members do not match its manifest")
            self.validated = _validate_in_worker(self.payload, len(self.records), report_out=self.report)
            # Hash/qualify the parent's loaded runtime while file work is already
            # off-thread. Main still rechecks identities before accepting facts.
            from . import document_admission
            document_admission.contract()
            after = os.stat(self.source)
            if self.stamp != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise NarwhalDocumentError("source file changed while loading; reopen it")
        except BaseException:
            self.cleanup()
            raise

    def check_source(self):
        current = os.stat(self.source)
        if self.stamp != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns):
            raise NarwhalDocumentError("source file changed before load publication; reopen it")

    def cleanup(self):
        self.directory.cleanup()


def prepare_load(source):
    """Read/unpack/validate frozen files; safe for a file-only background worker."""
    return PreparedLoad(source)


class LoadHydration:
    """Step main-thread host construction without leaking its bounds context."""
    def __init__(self, prepared):
        import contextvars
        self.context = contextvars.copy_context()
        self.iterator = _load_steps(prepared)
        self.finished = False

    def step(self):
        if self.finished:
            raise NarwhalDocumentError('load hydration already finished')
        try:
            self.context.run(next, self.iterator)
        except StopIteration as result:
            self.finished = True
            return result.value
        return None

    def close(self):
        if not self.finished:
            self.context.run(self.iterator.close)
        self.finished = True


def begin_load(prepared):
    import threading
    if threading.current_thread() is not threading.main_thread():
        raise NarwhalDocumentError('native document hydration requires the main thread')
    return LoadHydration(prepared)


def finish_load(prepared):
    """Synchronous host construction; UI callers step the same checks between frames."""
    hydration = begin_load(prepared)
    try:
        while True:
            model = hydration.step()
            if model is not None:
                return model
    finally:
        hydration.close()


def _load_steps(prepared):
    """Construct an inert host candidate on main; caller still explicitly commits it."""
    import threading
    if threading.current_thread() is not threading.main_thread():
        raise NarwhalDocumentError("native document hydration requires the main thread")
    prepared.check_source()
    with occt_backend.document_bounds_context() as bounds_scope:
        occt_backend.document_accept_bounds(bounds_scope, prepared.validated, prepared.report.get("bounds_facts"))
        model = yield from _hydrate_load(prepared)
    prepared.check_source()
    return model


def load_model(source: str | os.PathLike):
    """Synchronous/headless entry point, with the same staged admission as the UI."""
    prepared = prepare_load(source)
    try:
        return finish_load(prepared)
    finally:
        prepared.cleanup()


def _hydrate_load(prepared):
    source, validated, record_by_id = prepared.source, prepared.validated, prepared.record_by_id
    document_path = validated / "document.3dm"
    raw = r3.File3dm.Read(str(document_path))
    if raw is None:
        raise NarwhalDocumentError("native metadata document is unreadable")
    raw_ids = [_object_uuid(item) for item in raw.Objects]
    if len(raw_ids) != len(set(raw_ids)):
        raise NarwhalDocumentError("native metadata contains duplicate object UUIDs")
    expected_exact = set()
    for item in raw.Objects:
        if kernel.representation_of(item.Geometry) != kernel.ANALYTIC_MASTER:
            continue
        object_id = _object_uuid(item)
        expected_exact.add(object_id)
        getter = getattr(item.Geometry, "GetUserString", None)
        digest = getter(MASTER_DIGEST_KEY) if callable(getter) else None
        record = record_by_id.get(object_id)
        if record is None or digest != record.get("sha256"):
            raise NarwhalDocumentError("native proxy/master digest binding is missing or changed")
    if expected_exact != set(record_by_id):
        raise NarwhalDocumentError("native document is missing or inventing exact master payloads")

    from .model import NarwhalModel
    model = NarwhalModel()
    model.file = raw
    model.path = source
    model.import_renamed = model.assign_app_names()
    model.rehydrated = (0, 0)
    model.adopted = (0, [])
    objects = {_object_uuid(item): item for item in model.file.Objects}
    bindings = []
    replay_ids = _recipe_identity_hint(validated)
    for object_id, record in record_by_id.items():
        path = validated / f"{object_id}.brep"
        try:
            data = path.read_bytes()
            shape = occt_backend.shape_from_bytes(data, record.get("wrapper_type"))
        except Exception as exc:  # noqa: BLE001
            raise NarwhalDocumentError(f"validated exact master {object_id} is unreadable: {exc}") from exc
        if (not occt_backend.is_valid(shape)
                or int(shape.ShapeType().value) != record.get("shape_enum")):
            raise NarwhalDocumentError(f"validated exact master {object_id} changed")
        face_types = {str(key): int(value)
                      for key, value in sorted(occt_backend.face_types(shape).items())}
        airtight = bool(occt_backend.is_airtight(shape))
        volume = float(schema1_volume(shape)) if airtight else None
        if (face_types != record.get("face_types")
                or airtight != record.get("airtight")
                or (volume is not None and not math.isfinite(volume))):
            raise NarwhalDocumentError(f"validated exact master {object_id} topology changed")
        if airtight:
            expected_volume = record.get("volume")
            if (not isinstance(expected_volume, (int, float))
                    or not math.isfinite(float(expected_volume))
                    or abs(volume - float(expected_volume))
                    > 1.0e-10 * max(1.0, abs(float(expected_volume)))):
                raise NarwhalDocumentError(f"validated exact master {object_id} volume changed")
        lower, upper = occt_backend.tight_bbox(shape)
        actual_bounds = [float(value) for point in (lower, upper) for value in point]
        expected_bounds = record.get("bounds")
        if (not isinstance(expected_bounds, list) or len(expected_bounds) != 6
                or any(not isinstance(value, (int, float))
                       or not math.isfinite(float(value)) for value in expected_bounds)
                or any(abs(actual - float(expected))
                       > 1.0e-9 * max(1.0, abs(float(expected)))
                       for actual, expected in zip(actual_bounds, expected_bounds))):
            raise NarwhalDocumentError(f"validated exact master {object_id} bounds changed")
        file_object = objects.get(object_id)
        if file_object is None:
            raise NarwhalDocumentError(f"exact master {object_id} has no document object")
        document_tolerance = float(model.file.Settings.ModelAbsoluteTolerance)
        if not math.isfinite(document_tolerance) or document_tolerance <= 0.0:
            raise NarwhalDocumentError("native metadata has invalid model tolerance")
        proxy = file_object.Geometry
        if proxy.GetUserString(kernel.DOCUMENT_FORM_KEY) == "exact":
            from . import bridge

            # Independently reconstruct the stored proxy, never _occt_operand
            # (which resolves back to the master/recipe being checked).
            try:
                proxy_shape = occt_backend.shape_from_brep_data(
                    bridge.brep_data(proxy), sew_tolerance=document_tolerance)
                if (not occt_backend.is_valid(proxy_shape)
                        or bool(occt_backend.is_airtight(proxy_shape)) != airtight):
                    raise ValueError("proxy validity/closure differs")
                proxy_bounds = [float(value) for point in
                                occt_backend.tight_bbox(proxy_shape) for value in point]
            except Exception as exc:  # noqa: BLE001
                raise NarwhalDocumentError(
                    f"exact master {object_id} document proxy cannot be measured: {exc}") from exc
        else:
            proxy_box = proxy.GetBoundingBox()
            proxy_bounds = [proxy_box.Min.X, proxy_box.Min.Y, proxy_box.Min.Z,
                            proxy_box.Max.X, proxy_box.Max.Y, proxy_box.Max.Z]
        tolerance = max(1.0e-7, document_tolerance * 10.0)
        if (not all(math.isfinite(float(value)) for value in proxy_bounds)
                or any(abs(float(a) - float(b)) > tolerance
                       for a, b in zip(proxy_bounds, actual_bounds))):
            raise NarwhalDocumentError(
                f"exact master {object_id} does not match its document proxy bounds")
        # Independently prove full native value identity; neither the worker's
        # receipt nor an archive digest authorizes replacing the decoded shape.
        # Retaining the constructor avoids another gp_Dir normalization on
        # every Open/Save, without redefining any persisted geometry.
        recipe = kernel.recipe_of(proxy)
        if recipe is not None and (replay_ids is None or object_id in replay_ids):
            try:
                recipe_shape = occt_backend.from_recipe(recipe)
                recipe_data = bytes(occt_backend.shape_to_bytes(
                    recipe_shape, triangles=False,
                    format_version=occt_backend.document_current_binary_format()))
                if data == recipe_data:
                    shape = recipe_shape
            except Exception as exc:  # noqa: BLE001
                raise NarwhalDocumentError(
                    f"exact master {object_id} recipe identity cannot be checked: {exc}") from exc
        bindings.append((file_object.Geometry, shape))
        yield None  # UI may process events between independent exact masters.
    model._native_master_bindings = tuple(bindings)
    model.rehydrated = (len(bindings), 0)
    model.last_save_warnings = []
    return model


#: Written by the worker (document_worker.RECIPE_IDENTITY_FILE; a test holds them equal).
RECIPE_IDENTITY_FILE = "recipe-identity.json"


def _recipe_identity_hint(validated: Path):
    """Object ids the worker found byte-identical to their recipe replay, or None.

    Only an optimization hint: an id listed here is still replayed and compared byte for
    byte below; an id left out keeps the worker-validated decoded master, which is the
    exact geometry either way. A missing or malformed hint replays everything.
    """
    path = validated / RECIPE_IDENTITY_FILE
    try:
        if path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return frozenset(value)


def commit_masters(model) -> int:
    """Explicitly install an inert candidate's prepared masters."""
    bindings = getattr(model, "_native_master_bindings", None)
    if bindings is None:
        raise NarwhalDocumentError("model has no prepared native master transaction")
    try:
        installed = kernel.replace_document_masters(bindings)
    except Exception as exc:  # noqa: BLE001
        raise NarwhalDocumentError(f"could not commit exact master registry: {exc}") from exc
    model._native_master_bindings = None
    model.rehydrated = (installed, 0)
    return installed


def commit_model(app, model, source: str) -> None:
    """Publish model/context/layers/master registry together, rolling back on failure."""
    old_registry = kernel.master_registry_snapshot()
    old_state = {
        name: getattr(app, name) for name in (
            "model", "ctx", "active_command", "input_buffer", "layers",
            "_saved_serial", "_saved_history_state", "_saved_uncertain_generation", "doc_path") if hasattr(app, name)
    }
    try:
        app.set_model(model)
        commit_masters(model)
        model.path = source
        app.mark_saved(source)
    except Exception as exc:  # noqa: BLE001
        kernel.restore_master_registry(old_registry)
        for name, value in old_state.items():
            setattr(app, name, value)
        raise NarwhalDocumentError(f"could not publish native document: {exc}") from exc
    try:
        from . import bridge
        bridge.note_document_styles(model.file)
    except Exception:  # noqa: BLE001 -- styles never invalidate committed geometry
        pass
