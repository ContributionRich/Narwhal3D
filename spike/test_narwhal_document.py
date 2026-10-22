#!/usr/bin/env python3
"""Exact built-in `.narwhal` document container acceptance checks."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

import rhino3dm as r3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from narwhal3d import bridge, coil, kernel, narwhal_document, native_document  # noqa: E402
from narwhal3d.kernel import occt_backend  # noqa: E402
from narwhal3d.model import NarwhalModel  # noqa: E402

checks = 0
failures = []


def check(condition, label, detail=""):
    global checks
    checks += 1
    ok = bool(condition)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f" ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(label)


with tempfile.TemporaryDirectory(prefix="narwhal-document-") as directory:
    root = Path(directory)
    target = root / "exact.narwhal"
    model = NarwhalModel()
    model.add_layer("Machining", (20, 80, 160))
    model.add_object(bridge.make_line((0, 0, 0), (3, 4, 0)), "Guide", 0)
    exact = bridge.boolean_brep(
        bridge.make_box((0, 0, 0), (10, 10, 0), 10),
        bridge.make_cylinder((10, 5, 0), 3, 10), "UNION")
    exact_id, _ = model.add_object(exact, "ExactSolid", 1)
    source_master = kernel.master_of(exact)
    source_types = occt_backend.face_types(source_master)
    source_volume = occt_backend.volume(source_master)

    worker_probe = root / "worker-probe"
    (worker_probe / "validated").mkdir(parents=True)
    worker_script = Path("C:/app/narwhal3d/document_worker.py")
    python_worker = narwhal_document._worker_argv(
        Path("C:/runtime/python.exe"), worker_script, worker_probe)
    blender_worker = narwhal_document._worker_argv(
        Path("C:/runtime/blender.exe"), worker_script, worker_probe)
    check(python_worker == (str(Path("C:/runtime/python.exe")), str(worker_script),
                            "--", str(worker_probe))
          and "--factory-startup" not in python_worker,
          "embedded Windows Python receives Python arguments, not Blender flags")
    check("--factory-startup" in blender_worker and "--python" in blender_worker,
          "Blender worker executable retains bounded background invocation flags")
    worker_calls = []
    real_converter_run = narwhal_document.converter_process.run
    narwhal_document.converter_process.run = lambda *args, **kwargs: (
        worker_calls.append((args, kwargs))
        or type("WorkerResult", (), {
            "returncode": 0,
            "timed_out": False,
            "error": None,
            "stderr": "",
            "stdout": 'NARWHAL-DOCUMENT-WORKER {"count": 0}\n',
        })()
    )
    try:
        validated = narwhal_document._validate_in_worker(worker_probe, 0)
    finally:
        narwhal_document.converter_process.run = real_converter_run
    check(validated == worker_probe / "validated"
          and worker_calls[0][1].get("memory_mib") == 4096,
          "native exact-master parsing requires a supervised 4096 MiB memory ceiling")

    old_writer = os.environ.get("NARWHAL3D_BREP_WRITER")
    os.environ["NARWHAL3D_BREP_WRITER"] = str(root / "missing-openNURBS-helper")
    try:
        check(narwhal_document.save_model(model, target) and target.is_file(),
              "native save succeeds without the optional openNURBS writer")
    finally:
        if old_writer is None:
            os.environ.pop("NARWHAL3D_BREP_WRITER", None)
        else:
            os.environ["NARWHAL3D_BREP_WRITER"] = old_writer

    exact_proxy = model.file.Objects.FindId(str(exact_id)).Geometry
    kernel.tag(exact_proxy, kernel.MESH_DERIVED)
    wrong_tag_rejected = False
    try:
        narwhal_document.save_model(model, root / "wrong-tag.narwhal")
    except narwhal_document.NarwhalDocumentError as exc:
        wrong_tag_rejected = "has an exact master but declares" in str(exc)
    finally:
        kernel.register_master(exact_proxy, source_master)
    check(wrong_tag_rejected,
          "native save refuses an exact master whose proxy declares another representation")

    class RefusingGeometry:
        def GetUserString(self, _key):
            return ""
        def SetUserString(self, _key, _value):
            return False
    class FakeAttributes:
        Id = uuid.uuid4()
        Name = "RefusingProxy"
    class FakeObject:
        Geometry = RefusingGeometry()
        Attributes = FakeAttributes()
    class FakeFile:
        Objects = (FakeObject(),)
        def Write(self, _path, _version):
            raise AssertionError("metadata write must not follow a refused digest binding")
    class FakeModel:
        file = FakeFile()
        def normalize_order(self):
            return 0
    real_representation = narwhal_document.kernel.representation_of
    real_master_of = narwhal_document.kernel.master_of
    real_recipe_of = narwhal_document.kernel.recipe_of
    narwhal_document.kernel.representation_of = lambda _geometry: kernel.ANALYTIC_MASTER
    narwhal_document.kernel.master_of = lambda _geometry: source_master
    narwhal_document.kernel.recipe_of = lambda _geometry: {"type": "test"}
    setter_rejected = False
    try:
        narwhal_document.save_model(FakeModel(), root / "setter-refusal.narwhal")
    except narwhal_document.NarwhalDocumentError as exc:
        setter_rejected = "refused its native digest binding" in str(exc)
    finally:
        narwhal_document.kernel.representation_of = real_representation
        narwhal_document.kernel.master_of = real_master_of
        narwhal_document.kernel.recipe_of = real_recipe_of
    check(setter_rejected,
          "native save refuses a proxy that cannot retain its exact-master digest")

    faceted = bridge._triangulated_proxy(source_master)
    kernel.tag(faceted, kernel.ANALYTIC)
    dishonest_model = NarwhalModel()
    dishonest_model.add_object(faceted, "DishonestSolid", 0)
    dishonest_save_rejected = False
    try:
        narwhal_document.save_model(dishonest_model, root / "dishonest-save.narwhal")
    except narwhal_document.NarwhalDocumentError as exc:
        dishonest_save_rejected = "tagged 'analytic' but measures as a tessellation" in str(exc)
    check(dishonest_save_rejected,
          "native save refuses faceted geometry falsely tagged analytic")

    inventory = native_document.inspect(target)
    names = [row[2] for row in inventory]
    master_name = f"masters/{str(exact_id).lower()}.brep"
    check(names == sorted(names) and names == ["document.3dm", "manifest.json", master_name],
          "Rust inventory contains deterministic metadata and one exact master", repr(names))

    extracted = root / "extracted"
    native_document.unpack(target, extracted)
    manifest = json.loads((extracted / "manifest.json").read_text(encoding="utf-8"))
    check(manifest["schema"] == 1 and manifest["format"] == "Narwhal 3D Document"
          and manifest["masters"][0]["object_id"] == str(exact_id).lower(),
          "manifest maps the exact master by stable document UUID")

    kernel.forget_masters()
    reopened = narwhal_document.load_model(target)
    reopened_objects = {str(item.Attributes.Id): item for item in reopened.file.Objects}
    reopened_geom = reopened_objects[str(exact_id)].Geometry
    check(kernel.master_of(reopened_geom) is None,
          "native load returns an inert candidate before explicit publication")
    narwhal_document.commit_masters(reopened)
    reopened_master = kernel.master_of(reopened_geom)
    check(len(reopened.file.Objects) == 2 and reopened.path == str(target),
          "explicit native publication restores objects and adopts the native path")
    check(reopened_master is not None
          and occt_backend.face_types(reopened_master) == source_types
          and occt_backend.is_airtight(reopened_master),
          "native load restores the exact airtight analytic master", repr(source_types))
    check(abs(occt_backend.volume(reopened_master) - source_volume)
          <= 1e-10 * max(1.0, abs(source_volume)),
          "native load preserves exact master volume")
    check(kernel.honesty_problem(reopened_geom) is None and reopened.last_save_warnings == [],
          "native round trip is honest and has no faceted-save warning")

    corrupt = root / "corrupt.narwhal"
    damaged = bytearray(target.read_bytes())
    damaged[-1] ^= 1
    corrupt.write_bytes(damaged)
    retained_master = kernel.master_of(reopened_geom)
    rejected_corrupt = False
    try:
        narwhal_document.load_model(corrupt)
    except narwhal_document.NarwhalDocumentError as exc:
        rejected_corrupt = "invalid native container" in str(exc)
    check(rejected_corrupt and kernel.master_of(reopened_geom) is retained_master,
          "checksum corruption is refused without changing the active master registry")

    missing_stage = root / "missing-stage"
    shutil.copytree(extracted, missing_stage)
    (missing_stage / master_name).unlink()
    missing = root / "missing.narwhal"
    native_document.pack(missing_stage, missing)
    rejected_missing = False
    try:
        narwhal_document.load_model(missing)
    except narwhal_document.NarwhalDocumentError as exc:
        rejected_missing = "members do not match" in str(exc)
    check(rejected_missing, "a manifest with a missing exact master is refused")

    topology_stage = root / "topology-stage"
    shutil.copytree(extracted, topology_stage)
    topology_manifest_path = topology_stage / "manifest.json"
    topology_manifest = json.loads(topology_manifest_path.read_text(encoding="utf-8"))
    topology_manifest["masters"][0]["face_types"] = {}
    topology_manifest_path.write_text(
        json.dumps(topology_manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8")
    topology = root / "topology.narwhal"
    native_document.pack(topology_stage, topology)
    rejected_topology = False
    try:
        narwhal_document.load_model(topology)
    except narwhal_document.NarwhalDocumentError as exc:
        rejected_topology = "validation worker failed" in str(exc)
    check(rejected_topology,
          "a checksum-valid manifest cannot invent different exact topology")

    dishonest_stage = root / "dishonest-stage"
    shutil.copytree(extracted, dishonest_stage)
    dishonest_doc_path = dishonest_stage / "document.3dm"
    dishonest_doc = r3.File3dm.Read(str(dishonest_doc_path))
    dishonest_attributes = r3.ObjectAttributes()
    dishonest_attributes.Name = "InjectedFacets"
    dishonest_doc.Objects.Add(faceted, dishonest_attributes)
    dishonest_doc.Write(str(dishonest_doc_path), 8)
    dishonest = root / "dishonest-open.narwhal"
    native_document.pack(dishonest_stage, dishonest)
    dishonest_open_rejected = False
    try:
        narwhal_document.load_model(dishonest)
    except narwhal_document.NarwhalDocumentError as exc:
        dishonest_open_rejected = "dishonest" in str(exc)
    check(dishonest_open_rejected,
          "native open refuses injected faceted geometry falsely tagged analytic")

    swapped_stage = root / "swapped-stage"
    shutil.copytree(extracted, swapped_stage)
    equal_volume_hole_radius = math.sqrt((1300.0 - source_volume) / (math.pi * 10.0))
    alternate = bridge.boolean_brep(
        bridge.make_box((0, 0, 0), (13, 10, 0), 10),
        bridge.make_cylinder((6.5, 5, -1), equal_volume_hole_radius, 12), "DIFFERENCE")
    alternate_master = kernel.master_of(alternate)
    check(abs(occt_backend.volume(alternate_master) - source_volume)
          <= 1e-10 * max(1.0, abs(source_volume)),
          "proxy-correlation attack fixture has the same bounds and volume as the master")
    alternate_bytes = bytes(occt_backend.shape_to_bytes(alternate_master, triangles=False))
    alternate_digest = hashlib.sha256(alternate_bytes).hexdigest()
    (swapped_stage / master_name).write_bytes(alternate_bytes)
    swapped_doc_path = swapped_stage / "document.3dm"
    swapped_doc = r3.File3dm.Read(str(swapped_doc_path))
    swapped_object = next(item for item in swapped_doc.Objects
                          if str(item.Attributes.Id) == str(exact_id))
    swapped_object.Geometry.SetUserString(
        narwhal_document.MASTER_DIGEST_KEY, alternate_digest)
    swapped_doc.Write(str(swapped_doc_path), 8)
    swapped_manifest_path = swapped_stage / "manifest.json"
    swapped_manifest = json.loads(swapped_manifest_path.read_text(encoding="utf-8"))
    swapped_manifest["masters"][0] = narwhal_document._shape_record(
        swapped_object, alternate_master, master_name, alternate_digest)
    swapped_manifest_path.write_text(
        json.dumps(swapped_manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8")
    swapped = root / "swapped.narwhal"
    native_document.pack(swapped_stage, swapped)
    rejected_swap = False
    try:
        narwhal_document.load_model(swapped)
    except narwhal_document.NarwhalDocumentError as exc:
        rejected_swap = "proxy" in str(exc) or "reconstruction recipe" in str(exc)
    check(rejected_swap and kernel.master_of(reopened_geom) is retained_master,
          "a same-bounds replacement master cannot contradict the proxy recipe")

    proxy_stage = root / "proxy-stage"
    shutil.copytree(extracted, proxy_stage)
    proxy_doc_path = proxy_stage / "document.3dm"
    proxy_doc = r3.File3dm.Read(str(proxy_doc_path))
    proxy_object = next(item for item in proxy_doc.Objects
                        if str(item.Attributes.Id) == str(exact_id))
    wrong_proxy = alternate.Duplicate()
    for key, value in proxy_object.Geometry.GetUserStrings() or ():
        wrong_proxy.SetUserString(key, value)
    proxy_doc.Objects.Delete(str(exact_id))
    proxy_doc.Objects.Add(wrong_proxy, proxy_object.Attributes)
    proxy_doc.Write(str(proxy_doc_path), 8)
    mismatched_proxy = root / "mismatched-proxy.narwhal"
    native_document.pack(proxy_stage, mismatched_proxy)
    proxy_rejected = False
    try:
        narwhal_document.load_model(mismatched_proxy)
    except narwhal_document.NarwhalDocumentError as exc:
        proxy_rejected = "proxy" in str(exc)
    check(proxy_rejected and kernel.master_of(reopened_geom) is retained_master,
          "a same-bounds display proxy cannot contradict its manufacturing master")

    rollback_candidate = narwhal_document.load_model(target)
    class FailingApp:
        def __init__(self):
            self.model = reopened
            self.ctx = object()
            self.active_command = None
            self.input_buffer = ""
            self.layers = object()
            self._saved_serial = 7
            self.doc_path = str(target)
        def set_model(self, candidate):
            self.model = candidate
            raise RuntimeError("injected publication failure")
        def mark_saved(self, _path):
            raise AssertionError("mark_saved must not follow failed set_model")
    failing_app = FailingApp()
    rollback_rejected = False
    try:
        narwhal_document.commit_model(failing_app, rollback_candidate, str(target))
    except narwhal_document.NarwhalDocumentError:
        rollback_rejected = True
    check(rollback_rejected and failing_app.model is reopened
          and kernel.master_of(reopened_geom) is retained_master,
          "failed app publication rolls back model state and the exact master registry")

    concurrent_target = root / "concurrent.narwhal"
    concurrent_temp = root / ".concurrent.narwhal.narwhal-document-core.file.999.tmp"
    concurrent_temp.write_bytes(b"another process owns this")
    real_core_run = native_document._run
    native_document._run = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        native_document.DocumentCoreError("injected supervisor termination"))
    retained_concurrent_temp = False
    try:
        native_document.pack(extracted, concurrent_target)
    except native_document.DocumentCoreError:
        retained_concurrent_temp = concurrent_temp.read_bytes() == b"another process owns this"
    finally:
        native_document._run = real_core_run
    check(retained_concurrent_temp,
          "failed native operations never wildcard-delete another process's temporary file")

    logical_order = [str(item.Attributes.Id) for item in reopened.file.Objects]
    before_delete = reopened.snapshot()
    reopened.file.Objects.Delete(logical_order[0])
    reopened.snapshot()
    reopened.restore(before_delete)
    physical_before = [str(item.Attributes.Id) for item in reopened.file.Objects]
    master_before_failed_save = kernel.master_of(reopened_geom)
    types_before_failed_save = occt_backend.face_types(master_before_failed_save)
    volume_before_failed_save = occt_backend.volume(master_before_failed_save)

    preserved = root / "preserved.narwhal"
    preserved.write_bytes(b"known-good")
    real_pack = native_document.pack
    pack_calls = []
    native_document.pack = lambda *args: pack_calls.append(args) or (_ for _ in ()).throw(
        native_document.DocumentCoreError("injected pack failure"))
    failed = False
    try:
        narwhal_document.save_model(reopened, preserved)
    except narwhal_document.NarwhalDocumentError:
        failed = True
    finally:
        native_document.pack = real_pack
    physical_after = [str(item.Attributes.Id) for item in reopened.file.Objects]
    exact_after_failed_save = reopened.file.Objects.FindId(str(exact_id)).Geometry
    master_after_failed_save = kernel.master_of(exact_after_failed_save)
    check(failed and len(pack_calls) == 1
          and preserved.read_bytes() == b"known-good"
          and physical_before != logical_order
          and list(reopened.snapshot().by_id) == logical_order
          and physical_after == logical_order
          and not reopened.order_pending()
          and master_after_failed_save is master_before_failed_save
          and occt_backend.face_types(master_after_failed_save) == types_before_failed_save
          and abs(occt_backend.volume(master_after_failed_save) - volume_before_failed_save)
              <= 1e-10 * max(1.0, abs(volume_before_failed_save)),
          "failed native pack preserves the target, logical order, and exact manufacturing master")

with tempfile.TemporaryDirectory(prefix="narwhal-coils-") as directory:
    root = Path(directory)
    source = NarwhalModel()
    helix, _ = coil.make_curve((0, 0, 0), (0, 0, 5), (1, 0, 0),
                               1.0, 1.0, 2.0, tolerance=0.001)
    spiral, _ = coil.make_curve((10, 0, 0), (10, 0, 5), (1, 0, 0),
                                1.0, 3.0, 2.0, tolerance=0.001)
    source.add_object(helix, "HelixAcceptance", 0)
    source.add_object(spiral, "SpiralAcceptance", 0)
    three_dm = root / "coils.3dm"
    native_path = root / "coils.narwhal"
    source.save(str(three_dm))
    reopened_3dm = r3.File3dm.Read(str(three_dm))
    narwhal_document.save_model(source, native_path)
    reopened_native = narwhal_document.load_model(native_path)
    curves_3dm = [item.Geometry for item in reopened_3dm.Objects]
    curves_native = [item.Geometry for item in reopened_native.file.Objects]
    check(len(curves_3dm) == 2 and len(curves_native) == 2
          and all(isinstance(curve, r3.NurbsCurve) and curve.Degree == 3
                  and not curve.IsPolyline()
                  for curve in curves_3dm + curves_native),
          "Helix and Spiral survive fresh .3dm and .narwhal round trips as smooth NURBS")

print(f"\n{checks - len(failures)}/{checks} native-document checks passed")
if failures:
    raise SystemExit("FAIL: " + ", ".join(failures))
