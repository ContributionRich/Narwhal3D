"""Resource-limited validator/canonicalizer for extracted `.narwhal` exact masters."""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from narwhal3d import bridge, kernel  # noqa: E402
from narwhal3d.kernel import occt_backend  # noqa: E402
import rhino3dm as r3  # noqa: E402

OCP = importlib.import_module("OCP")
BinTools_FormatVersion = importlib.import_module("OCP.BinTools").BinTools_FormatVersion

FORMAT = "Narwhal 3D Document"
SCHEMA = 1
ENCODING = "OCCT BinTools"
MAX_MANIFEST = 16 * 1024 * 1024
MAX_MASTER = 256 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024
MAX_MASTERS = 10_000
MEMORY_LIMIT = 4 * 1024 * 1024 * 1024


def fail(message):
    print("NARWHAL-DOCUMENT-WORKER " + json.dumps({"error": str(message)}))
    raise SystemExit(1)


def _exclusive_volume(shape_a, shape_b):
    """Exact volume of ``shape_a - shape_b`` without rejecting an empty result."""
    cut = importlib.import_module("OCP.BRepAlgoAPI").BRepAlgoAPI_Cut

    builder = cut(shape_a, shape_b)
    builder.Build()
    if not builder.IsDone():
        raise RuntimeError("exact proxy correlation Boolean failed")
    result = builder.Shape()
    return 0.0 if result.IsNull() else abs(float(occt_backend.volume(result)))


def main():
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if len(args) != 1:
        fail("usage: document_worker.py -- <extracted-root>")
    root = Path(args[0]).resolve()
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT, MEMORY_LIMIT))
    except (ImportError, ValueError, OSError):
        pass
    manifest_path = root / "manifest.json"
    try:
        if manifest_path.stat().st_size > MAX_MANIFEST:
            fail("manifest exceeds 16 MiB")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        fail(f"invalid manifest: {exc}")
    current_format = int(BinTools_FormatVersion.BinTools_FormatVersion_CURRENT.value)
    if (not isinstance(manifest, dict) or manifest.get("format") != FORMAT
            or manifest.get("schema") != SCHEMA
            or manifest.get("master_encoding") != ENCODING
            or manifest.get("occt_version") != str(OCP.__version__)
            or manifest.get("bintools_format") != current_format
            or not isinstance(manifest.get("masters"), list)
            or len(manifest["masters"]) > MAX_MASTERS):
        fail("unsupported manifest or OCCT/BinTools version")
    output = root / "validated"
    if output.exists():
        fail("validated output already exists")
    output.mkdir()
    document_path = root / "document.3dm"
    try:
        if document_path.stat().st_size > MAX_MASTER * 2:
            fail("metadata document exceeds 512 MiB")
        document = r3.File3dm.Read(str(document_path))
    except Exception as exc:  # noqa: BLE001
        fail(f"metadata document cannot be decoded: {exc}")
    if document is None:
        fail("metadata document cannot be decoded")
    document_objects = {}
    for item in document.Objects:
        try:
            object_id = str(uuid.UUID(str(item.Attributes.Id)))
        except Exception:  # noqa: BLE001
            fail("metadata document contains an invalid object UUID")
        if object_id in document_objects:
            fail("metadata document contains duplicate object UUIDs")
        document_objects[object_id] = item
    tolerance = float(document.Settings.ModelAbsoluteTolerance)
    if not math.isfinite(tolerance) or tolerance <= 0.0:
        fail("metadata document has invalid model tolerance")
    expected_exact = set()
    for object_id, item in document_objects.items():
        declared = kernel.representation_of(item.Geometry)
        if declared == kernel.ANALYTIC_MASTER:
            expected_exact.add(object_id)
            continue
        problem = kernel.honesty_problem(item.Geometry)
        if problem is not None:
            fail(f"metadata object {object_id} is dishonest: {problem}")
    manifest_ids = {str(record.get("object_id")) for record in manifest["masters"]
                    if isinstance(record, dict)}
    if expected_exact != manifest_ids:
        fail("metadata and manifest exact-master coverage differs")
    if not document.Write(str(output / "document.3dm"), 8):
        fail("metadata document cannot be canonicalized")
    total = 0
    canonical_total = 0
    seen = set()
    for record in manifest["masters"]:
        try:
            object_id = str(uuid.UUID(str(record["object_id"])))
        except Exception:  # noqa: BLE001
            fail("invalid master UUID")
        if object_id in seen or record.get("member") != f"masters/{object_id}.brep":
            fail("duplicate or noncanonical master mapping")
        seen.add(object_id)
        path = root / record["member"]
        try:
            size = path.stat().st_size
        except OSError as exc:
            fail(f"missing master {object_id}: {exc}")
        if size <= 0 or size > MAX_MASTER:
            fail(f"master {object_id} has unsupported size {size}")
        total += size
        if total > MAX_TOTAL:
            fail("master payloads exceed 2 GiB")
        try:
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != record.get("sha256"):
                fail(f"master {object_id} digest binding changed")
            shape = occt_backend.shape_from_bytes(data, record.get("wrapper_type"))
        except Exception as exc:  # noqa: BLE001
            fail(f"master {object_id} cannot be decoded: {exc}")
        if not occt_backend.is_valid(shape):
            fail(f"master {object_id} is invalid")
        shape_enum = int(shape.ShapeType().value)
        face_types = {str(key): int(value)
                      for key, value in sorted(occt_backend.face_types(shape).items())}
        lower, upper = occt_backend.tight_bbox(shape)
        bounds = [float(value) for point in (lower, upper) for value in point]
        if not all(math.isfinite(value) for value in bounds):
            fail(f"master {object_id} has non-finite bounds")
        airtight = bool(occt_backend.is_airtight(shape))
        volume = float(occt_backend.volume(shape)) if airtight else None
        if volume is not None and not math.isfinite(volume):
            fail(f"master {object_id} has non-finite volume")
        expected_bounds = record.get("bounds")
        if (record.get("shape_enum") != shape_enum
                or record.get("face_types") != face_types
                or record.get("airtight") != airtight
                or not isinstance(expected_bounds, list) or len(expected_bounds) != 6
                or any(not isinstance(value, (int, float))
                       or not math.isfinite(float(value)) for value in expected_bounds)
                or any(abs(actual - float(expected))
                       > 1.0e-9 * max(1.0, abs(float(expected)))
                       for actual, expected in zip(bounds, expected_bounds))):
            fail(f"master {object_id} topology summary changed")
        expected_volume = record.get("volume")
        if airtight and (not isinstance(expected_volume, (int, float))
                          or not math.isfinite(float(expected_volume))
                          or abs(volume - float(expected_volume))
                          > 1.0e-10 * max(1.0, abs(float(expected_volume)))):
            fail(f"master {object_id} volume changed")
        metadata_object = document_objects.get(object_id)
        if metadata_object is None:
            fail(f"master {object_id} has no metadata object")
        proxy = metadata_object.Geometry
        getter = getattr(proxy, "GetUserString", None)
        document_form = getter(kernel.DOCUMENT_FORM_KEY) if callable(getter) else None
        try:
            if document_form == "triangulated":
                expected_proxy = bridge._triangulated_proxy(shape)
                if bridge.brep_data(proxy) != bridge.brep_data(expected_proxy):
                    fail(f"master {object_id} does not match its triangulated proxy")
            elif document_form == "exact":
                proxy_shape = occt_backend.shape_from_brep_data(
                    bridge.brep_data(proxy), sew_tolerance=tolerance)
                if (not occt_backend.is_valid(proxy_shape)
                        or bool(occt_backend.is_airtight(proxy_shape)) != airtight):
                    fail(f"master {object_id} does not match its exact proxy")
                proxy_lower, proxy_upper = occt_backend.tight_bbox(proxy_shape)
                proxy_shape_bounds = [
                    float(value) for point in (proxy_lower, proxy_upper) for value in point]
                if any(abs(a - b) > max(1.0e-7, tolerance * 10.0)
                       for a, b in zip(proxy_shape_bounds, bounds)):
                    fail(f"master {object_id} does not match its exact proxy bounds")
                if airtight:
                    proxy_volume = float(occt_backend.volume(proxy_shape))
                    if (not math.isfinite(proxy_volume)
                            or abs(proxy_volume - volume)
                            > max(1.0e-9, tolerance ** 3) * max(1.0, abs(volume))):
                        fail(f"master {object_id} does not match its exact proxy volume")
                    exclusive = max(
                        _exclusive_volume(shape, proxy_shape),
                        _exclusive_volume(proxy_shape, shape),
                    )
                    exclusive_tolerance = max(
                        abs(volume) * 1.0e-9,
                        tolerance ** 3 * 1000.0,
                    )
                    if not math.isfinite(exclusive) or exclusive > exclusive_tolerance:
                        fail(f"master {object_id} differs geometrically from its exact proxy")
            else:
                fail(f"master {object_id} has no recognized document proxy form")
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            fail(f"master {object_id} proxy cannot be validated: {exc}")
        recipe = kernel.recipe_of(proxy)
        if recipe is None:
            fail(f"master {object_id} has no reconstruction recipe")
        try:
            recipe_shape = occt_backend.from_recipe(recipe)
        except Exception as exc:  # noqa: BLE001
            fail(f"master {object_id} recipe cannot be rebuilt: {exc}")
        recipe_types = {str(key): int(value)
                        for key, value in sorted(occt_backend.face_types(recipe_shape).items())}
        recipe_airtight = bool(occt_backend.is_airtight(recipe_shape))
        recipe_lower, recipe_upper = occt_backend.tight_bbox(recipe_shape)
        recipe_bounds = [float(value) for point in (recipe_lower, recipe_upper) for value in point]
        if (not occt_backend.is_valid(recipe_shape) or recipe_types != face_types
                or recipe_airtight != airtight
                or any(abs(a - b) > 1.0e-9 * max(1.0, abs(b))
                       for a, b in zip(recipe_bounds, bounds))):
            fail(f"master {object_id} differs from its reconstruction recipe")
        if airtight:
            recipe_volume = float(occt_backend.volume(recipe_shape))
            if (not math.isfinite(recipe_volume)
                    or abs(recipe_volume - volume) > 1.0e-10 * max(1.0, abs(volume))):
                fail(f"master {object_id} volume differs from its recipe")
        proxy_box = proxy.GetBoundingBox()
        proxy_bounds = [proxy_box.Min.X, proxy_box.Min.Y, proxy_box.Min.Z,
                        proxy_box.Max.X, proxy_box.Max.Y, proxy_box.Max.Z]
        if any(abs(float(a) - float(b)) > max(1.0e-7, tolerance * 10.0)
               for a, b in zip(proxy_bounds, bounds)):
            fail(f"master {object_id} does not match proxy bounds")
        try:
            canonical = bytes(occt_backend.shape_to_bytes(shape, triangles=False))
        except Exception as exc:  # noqa: BLE001
            fail(f"master {object_id} cannot be canonicalized: {exc}")
        if not canonical or len(canonical) > MAX_MASTER:
            fail(f"canonical master {object_id} has unsupported size")
        canonical_total += len(canonical)
        if canonical_total > MAX_TOTAL:
            fail("canonical master payloads exceed 2 GiB")
        try:
            reread = occt_backend.shape_from_bytes(canonical, record.get("wrapper_type"))
        except Exception as exc:  # noqa: BLE001
            fail(f"canonical master {object_id} cannot be reread: {exc}")
        if (not occt_backend.is_valid(reread)
                or int(reread.ShapeType().value) != shape_enum
                or {str(key): int(value)
                    for key, value in sorted(occt_backend.face_types(reread).items())}
                    != face_types
                or bool(occt_backend.is_airtight(reread)) != airtight):
            fail(f"canonical master {object_id} changed during reread")
        if airtight:
            reread_volume = float(occt_backend.volume(reread))
            if (not math.isfinite(reread_volume)
                    or abs(reread_volume - volume)
                    > 1.0e-10 * max(1.0, abs(volume))):
                fail(f"canonical master {object_id} volume changed during reread")
        (output / f"{object_id}.brep").write_bytes(canonical)
    print("NARWHAL-DOCUMENT-WORKER " + json.dumps({"count": len(seen)}, sort_keys=True))


if __name__ == "__main__":
    main()
