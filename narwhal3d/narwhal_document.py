"""Built-in `.narwhal` project save/load with exact analytic OCCT masters."""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import uuid

import rhino3dm as r3

from . import converter_process, kernel, native_document
from .kernel import occt_backend

FORMAT = "Narwhal 3D Document"
SCHEMA = 1
EXTENSION = ".narwhal"
MASTER_ENCODING = "OCCT BinTools"
MASTER_DIGEST_KEY = "Narwhal3D:NativeMasterSHA256"
MAX_MANIFEST = 16 * 1024 * 1024
MAX_MASTER = 256 * 1024 * 1024
MAX_MASTERS = 10_000
MAX_EXACT_TOTAL = 2 * 1024 * 1024 * 1024
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
    return str(ocp.__version__), int(current.value)


def _shape_record(file_object, shape, member: str, digest: str) -> dict:
    wrapper_type = type(shape).__name__
    if wrapper_type not in _SHAPE_TYPES or not occt_backend.is_valid(shape):
        raise NarwhalDocumentError(f"unsupported or invalid exact OCCT shape {wrapper_type!r}")
    face_types = {str(key): int(value)
                  for key, value in sorted(occt_backend.face_types(shape).items())}
    lower, upper = occt_backend.tight_bbox(shape)
    bounds = [float(value) for point in (lower, upper) for value in point]
    if not all(math.isfinite(value) for value in bounds):
        raise NarwhalDocumentError("exact OCCT master has non-finite bounds")
    airtight = bool(occt_backend.is_airtight(shape))
    volume = float(occt_backend.volume(shape)) if airtight else None
    if volume is not None and not math.isfinite(volume):
        raise NarwhalDocumentError("exact OCCT master has non-finite volume")
    return {
        "object_id": _object_uuid(file_object),
        "member": member,
        "sha256": digest,
        "wrapper_type": wrapper_type,
        "shape_enum": int(shape.ShapeType().value),
        "airtight": airtight,
        "face_types": face_types,
        "bounds": bounds,
        "volume": volume,
    }


def save_model(model, target: str | os.PathLike) -> bool:
    """Atomically save ``model`` as a self-contained exact `.narwhal` container."""
    target = os.path.abspath(os.fspath(target))
    folder = os.path.dirname(target)
    if not os.path.isdir(folder):
        raise NarwhalDocumentError(f"the folder {folder!r} does not exist")
    if not occt_backend.available():
        raise NarwhalDocumentError("the built-in OCCT kernel is unavailable")
    occt_version, bintools_format = _format_contract()
    model.normalize_order()  # established file-order normalization; logical state is unchanged
    with tempfile.TemporaryDirectory(prefix="narwhal-save-") as directory:
        root = Path(directory)
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
                    raise NarwhalDocumentError(
                        f"{file_object.Attributes.Name or '(unnamed)'} has an exact master "
                        "but no reconstruction recipe")
                try:
                    data = bytes(occt_backend.shape_to_bytes(master, triangles=False))
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
                records.append(_shape_record(file_object, master, member, digest))
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
        try:
            native_document.pack(root, target)
        except native_document.DocumentCoreError as exc:
            raise NarwhalDocumentError(f"native container writer failed: {exc}") from exc
    model.last_save_warnings = []
    return True


def _read_manifest(path: Path) -> dict:
    try:
        if path.stat().st_size > MAX_MANIFEST:
            raise NarwhalDocumentError("native document manifest exceeds 16 MiB")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NarwhalDocumentError(f"invalid native document manifest: {exc}") from exc
    occt_version, bintools_format = _format_contract()
    if (not isinstance(value, dict) or value.get("format") != FORMAT
            or value.get("schema") != SCHEMA
            or value.get("document_member") != "document.3dm"
            or value.get("master_encoding") != MASTER_ENCODING
            or value.get("occt_version") != occt_version
            or value.get("bintools_format") != bintools_format
            or not isinstance(value.get("masters"), list)):
        raise NarwhalDocumentError("unsupported native document or OCCT/BinTools version")
    if len(value["masters"]) > MAX_MASTERS:
        raise NarwhalDocumentError("native document has more than 10,000 exact masters")
    return value


def _worker_argv(executable: Path, worker: Path, root: Path) -> tuple[str, ...]:
    if "blender" in executable.name.lower():
        return (str(executable), "-b", "--factory-startup", "--python-exit-code", "1",
                "--python", str(worker), "--", str(root))
    return (str(executable), str(worker), "--", str(root))


def _worker_command(root: Path) -> tuple[str, ...]:
    worker = Path(__file__).resolve().with_name("document_worker.py")
    executable = Path(sys.executable).resolve()
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


def _validate_in_worker(root: Path, count: int) -> Path:
    # Preserve the trusted launcher's kernel selection for recipe replay, not any
    # document-supplied path or the rest of the parent's (possibly secret) environment.
    selected_core = os.environ.get("NARWHAL_GEOMETRY_CORE")
    extra_env = {"NARWHAL_GEOMETRY_CORE": selected_core} if selected_core else {}
    result = converter_process.run(
        _worker_command(root), timeout=600, max_output=65536, memory_mib=4096,
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
            raise NarwhalDocumentError(
                f"exact-master validation worker failed: {report['error']}")
    if result.returncode != 0 or result.timed_out or result.error or marker is None:
        detail = result.error or result.stderr[-2000:] or result.stdout[-2000:]
        raise NarwhalDocumentError(f"exact-master validation worker failed: {detail}")
    if not isinstance(report, dict) or report.get("count") != count:
        raise NarwhalDocumentError(f"exact-master worker rejected the document: {report}")
    output = root / "validated"
    if not output.is_dir() or output.is_symlink():
        raise NarwhalDocumentError("exact-master worker produced no safe output directory")
    return output


def load_model(source: str | os.PathLike):
    """Load a validated `.narwhal` project and atomically install every exact master."""
    source = os.path.abspath(os.fspath(source))
    if not os.path.isfile(source):
        raise FileNotFoundError(source)
    if not occt_backend.available():
        raise NarwhalDocumentError("the built-in OCCT kernel is unavailable")
    with tempfile.TemporaryDirectory(prefix="narwhal-open-") as directory:
        payload = Path(directory) / "payload"
        try:
            native_document.unpack(source, payload)
        except native_document.DocumentCoreError as exc:
            raise NarwhalDocumentError(f"invalid native container: {exc}") from exc
        manifest = _read_manifest(payload / "manifest.json")
        records = manifest["masters"]
        record_by_id = {}
        expected_members = {"manifest.json", "document.3dm"}
        for record in records:
            if not isinstance(record, dict):
                raise NarwhalDocumentError("native master record is not an object")
            try:
                object_id = str(uuid.UUID(str(record["object_id"])))
            except (KeyError, ValueError) as exc:
                raise NarwhalDocumentError("native master record has an invalid object UUID") from exc
            member = record.get("member")
            digest = record.get("sha256")
            if (member != f"masters/{object_id}.brep" or object_id in record_by_id
                    or not isinstance(digest, str) or len(digest) != 64):
                raise NarwhalDocumentError("native master mapping is duplicate or noncanonical")
            expected_members.add(member)
            record_by_id[object_id] = record
        actual_members = {
            path.relative_to(payload).as_posix() for path in payload.rglob("*") if path.is_file()
        }
        if actual_members != expected_members:
            raise NarwhalDocumentError("native container members do not match its manifest")

        validated = _validate_in_worker(payload, len(records))
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
            volume = float(occt_backend.volume(shape)) if airtight else None
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
            bindings.append((file_object.Geometry, shape))
        model._native_master_bindings = tuple(bindings)
        model.rehydrated = (len(bindings), 0)
        model.last_save_warnings = []
        return model


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
            "_saved_serial", "doc_path") if hasattr(app, name)
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
