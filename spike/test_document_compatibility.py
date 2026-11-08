"""Permanent schema-1 compatibility: fixed historical bytes, old BinTools and round trips.

Run in Blender after building native/document_core. This fixture contains only synthetic
geometry, was saved by the historical writer, and must never be silently regenerated.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import zlib

import rhino3dm as r3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from narwhal3d import bridge, document_compat, document_proxy, document_worker, kernel  # noqa: E402
from narwhal3d import narwhal_document as document, native_document  # noqa: E402
from narwhal3d.kernel import occt_backend as occt  # noqa: E402
from OCP.BinTools import BinTools, BinTools_FormatVersion  # noqa: E402

FIXTURE_DIGEST = "51c1cac58a56a062d94aed96e2c64d148990911da0239509f19b0c28e1320234"
fixture = json.loads((ROOT / "spike/fixtures/document_schema1_historical.json").read_text())
compressed = bytes.fromhex(fixture["payload"])
expander = zlib.decompressobj()
raw = expander.decompress(compressed, 1024 * 1024 + 1)
assert expander.eof and not expander.unconsumed_tail and not expander.unused_data
assert len(raw) == fixture["size"] < 1024 * 1024
assert hashlib.sha256(raw).hexdigest() == FIXTURE_DIGEST == fixture["sha256"]
checks = 0


def check(condition, label):
    global checks
    assert condition, label
    checks += 1
    print("  PASS " + label, flush=True)


def summaries(model):
    document.commit_masters(model)
    result = {}
    for item in model.file.Objects:
        master = kernel.master_of(item.Geometry)
        assert master is not None
        result[str(item.Attributes.Id)] = (occt.face_types(master), occt.is_airtight(master),
                                          occt.volume(master), occt.tight_bbox(master))
    return result


with tempfile.TemporaryDirectory(prefix="narwhal-compat-") as temporary:
    root = Path(temporary)
    historical = root / "historical.narwhal"
    historical.write_bytes(raw)
    loaded = document.load_model(historical)
    initial = summaries(loaded)
    check(loaded.object_count() == 2, "fixed historical schema-1 file opens")
    check(all(value[1] for value in initial.values()), "both historical exact solids remain airtight")
    cylinder = next(o.Geometry for o in loaded.file.Objects
                    if o.Attributes.Name == "Historical moved cylinder")
    cylinder_master = kernel.master_of(cylinder)
    check(bridge.brep_data(cylinder) != bridge.brep_data(bridge._triangulated_proxy(cylinder_master)),
          "fixture exposes the old byte-equality rejection, not a trivial identical proxy")
    extrusion = next(o.Geometry for o in loaded.file.Objects
                     if o.Attributes.Name == "Historical analytic extrusion")
    extrusion_master = kernel.master_of(extrusion)
    check(abs(occt.volume(extrusion_master) - occt.volume(occt._nurbs_convert(extrusion_master))) > 0.1,
          "fixture exposes the extrusion/NURBS integration discrepancy")
    check(any(v[0] == {"EXTRUSION": 1, "PLANE": 2} for v in initial.values()),
          "extrusion retains its analytic side and two caps, not a replacement mesh")

    # Viewport quality is not a file-format decoder. Historical codecs are independent.
    original_display = occt._display_mesh
    try:
        def forbidden_display(*_args, **_kwargs):
            raise AssertionError("file validation called the live viewport mesher")
        occt._display_mesh = forbidden_display
        check(document_proxy.matches_master(cylinder, cylinder_master),
              "historical proxy validation does not call the live viewport mesher")
    finally:
        occt._display_mesh = original_display

    check(document_compat.writer_bintools_format(999) == 4,
          "a newer kernel must still write a tested historical wire encoding")
    original_volume = occt.volume
    try:
        occt.volume = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("file summaries called the live measurement API"))
        check(document_compat.schema1_volume(cylinder_master) > 0,
              "historical volume summaries are independent of live measurement changes")
    finally:
        occt.volume = original_volume

    rewritten = root / "rewritten.narwhal"
    document.save_model(loaded, rewritten)
    reopened = document.load_model(rewritten)
    again = summaries(reopened)
    check(initial.keys() == again.keys(), "save/reopen preserves every object UUID")
    check(all(a[0] == again[k][0] and a[1] == again[k][1]
              and abs(a[2] - again[k][2]) <= 1e-10 * max(1, abs(a[2]))
              and all(abs(x - y) < 1e-9 for pa, pb in zip(a[3], again[k][3])
                      for x, y in zip(pa, pb)) for k, a in initial.items()),
          "save/reopen preserves exact master types, closure, bounds and volume")
    check(hashlib.sha256(historical.read_bytes()).hexdigest() == FIXTURE_DIGEST,
          "opening and saving elsewhere never changes the historical original")

    saved_bytes = rewritten.read_bytes()
    # summaries(reopened) published that document's masters; republish a fresh source
    # before testing save admission, otherwise a missing-master refusal would mask it.
    loaded = document.load_model(historical)
    document.commit_masters(loaded)
    loaded.add_object(r3.Point(r3.Point3d(50, 50, 0)), "Unsaved change", 0)
    original_validate = document._validate_in_worker
    refused = False
    try:
        def refusal(*_args):
            raise document.NarwhalDocumentError("injected reader admission failure")
        document._validate_in_worker = refusal
        try:
            document.save_model(loaded, rewritten)
        except document.NarwhalDocumentError as error:
            refused = "injected reader admission failure" in str(error)
    finally:
        document._validate_in_worker = original_validate
    check(refused and rewritten.read_bytes() == saved_bytes,
          "a reader/serializer disagreement cannot replace a previous good save")

    fallback_shape = occt.make_cylinder(20, 5)
    vertices, triangles = occt.document_proxy_tessellation(fallback_shape, 3)
    vertices, triangles = bridge._weld_tessellation(vertices, triangles)
    mesh = r3.Mesh()
    for vertex in vertices:
        mesh.Vertices.Add(*vertex)
    for triangle in triangles:
        mesh.Faces.AddFace(*triangle)
    fallback_proxy = r3.Brep.CreateFromMesh(mesh, False)
    check(document_proxy.matches_master(fallback_proxy, fallback_shape),
          "historical coarse retry proxies retain their own frozen decoder")

    # A plane and its exact NURBS conversion can have different surface-class labels.
    box = occt.make_box(2, 3, 4)
    nurbs_box = occt._nurbs_convert(box)
    box_proxy = bridge._triangulated_proxy(box)
    check(occt.face_types(box) != occt.face_types(nurbs_box)
          and document_worker._compatible_recipe_types(
              box, nurbs_box, {"kind": "brep_data", "tolerance": 1e-6}, box_proxy, "triangulated"),
          "legacy planar/NURBS recognition differences require independent surface correlation")
    shifted = occt.transform(box, [1, 0, 0, 1, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])
    pair = document_worker._exclusive_volume_pair(box, shifted)
    check(all(abs(value - 12) < 1e-10 for value in pair),
          "shared intersections retain both exact nonzero difference volumes")
    check(document_worker._exclusive_volume_pair(box, box) == (0.0, 0.0),
          "identical exact shapes have empty differences without a Boolean rebuild")

    check(not document_worker._compatible_recipe_types(
        box, nurbs_box, {"kind": "brep_data", "tolerance": 1.0}, box_proxy, "triangulated"),
          "recognition migration cannot use an unbounded supplied tolerance")

    # Exercise REAL older binary encodings, not only altered version labels. Metadata
    # records and digest bindings are rebuilt exactly as an older producer would write.
    for binary_format in sorted(document_compat.SUPPORTED_BINTOOLS_FORMATS):
        stage = root / f"old-{binary_format}"
        native_document.unpack(historical, stage)
        manifest = json.loads((stage / "manifest.json").read_text())
        metadata_path = stage / "document.3dm"
        metadata = r3.File3dm.Read(str(metadata_path))
        objects = {str(o.Attributes.Id): o for o in metadata.Objects}
        for record in manifest["masters"]:
            path = stage / record["member"]
            shape = occt.shape_from_bytes(path.read_bytes(), record["wrapper_type"])
            stream = io.BytesIO()
            BinTools.Write_s(shape, stream, False, False, BinTools_FormatVersion(binary_format))
            data = stream.getvalue()
            path.write_bytes(data)
            record["sha256"] = hashlib.sha256(data).hexdigest()
            objects[record["object_id"]].Geometry.SetUserString(document.MASTER_DIGEST_KEY, record["sha256"])
        assert metadata.Write(str(metadata_path), 8)
        manifest["bintools_format"] = binary_format
        manifest["application_version"] = "99.99.99"
        manifest["occt_version"] = "7.8.1.0"  # producer metadata is not the wire version
        (stage / "manifest.json").write_text(json.dumps(manifest))
        archive = root / f"old-format-{binary_format}.narwhal"
        native_document.pack(stage, archive)
        old_model = document.load_model(archive)
        actual = summaries(old_model)
        check(actual.keys() == initial.keys() and all(
            v[0] == initial[k][0] and v[1] == initial[k][1]
            and abs(v[2] - initial[k][2]) < 1e-9 for k, v in actual.items()),
            f"BinTools {binary_format} decodes with exact geometry despite different producer version")

    # Capability-qualified discovery must not activate an older helper merely because
    # its unchanged container protocol is compatible with pack/unpack.
    from types import SimpleNamespace
    old_candidates, old_run = native_document._candidates, native_document.converter_process.run
    old_cache = native_document._probe_cache
    try:
        candidate = Path(sys.executable)
        native_document._candidates = lambda: (candidate,)
        native_document._probe_cache = {}
        native_document.converter_process.run = lambda argv, **kwargs: SimpleNamespace(
            returncode=0 if argv[-1] == "--version" else 2,
            timed_out=False, stdout=native_document.PROTOCOL if argv[-1] == "--version" else "")
        check(native_document.executable() == candidate
              and native_document.executable("display-proxy-equivalence-v1") is None,
              "an old pack/unpack helper cannot activate the new comparison hook")
    finally:
        native_document._candidates, native_document.converter_process.run = old_candidates, old_run
        native_document._probe_cache = old_cache

    # The assistant's historical additive box attached a master without a form tag.
    # Exercise the real publication/Undo path, then emulate those older saved bytes.
    from narwhal3d import ai_box, ui
    from narwhal3d.model import NarwhalModel
    app = ui.app_state
    app.set_model(NarwhalModel())
    approved = dict(status="approved_box", context=ai_box.context(app, "compat-document"),
                    arguments=dict(origin=[1, 2, 3], size=[4, 5, 6]))
    before_undo = len(app.ctx.undo.undo_names())
    receipt = ai_box.publish(app, approved, lambda: None)
    item = next(iter(app.model.file.Objects))
    check(item.Geometry.GetUserString(kernel.DOCUMENT_FORM_KEY) == "exact"
          and len(app.ctx.undo.undo_names()) == before_undo + 1,
          "approved box publishes an exact file form with one Undo record")
    ai_archive = root / "approved-box.narwhal"
    check(document.save_model(app.model, ai_archive), "approved box can be saved for exit recovery")
    ai_loaded = document.load_model(ai_archive)
    ai_summary = summaries(ai_loaded)
    check(ai_loaded.object_count() == 1 and all(v[0] == {"PLANE": 6} and v[1]
          and abs(v[2] - 120) < 1e-9 for v in ai_summary.values()),
          "approved box reopens as an airtight six-face analytic solid")
    legacy_stage = root / "legacy-box-stage"
    native_document.unpack(ai_archive, legacy_stage)
    legacy_meta = r3.File3dm.Read(str(legacy_stage / "document.3dm"))
    legacy_proxy = next(iter(legacy_meta.Objects)).Geometry
    legacy_proxy.SetUserString(kernel.DOCUMENT_FORM_KEY, "")
    assert legacy_meta.Write(str(legacy_stage / "document.3dm"), 8)
    legacy_archive = root / "legacy-box.narwhal"
    native_document.pack(legacy_stage, legacy_archive)
    legacy_model = document.load_model(legacy_archive)
    check(next(iter(legacy_model.file.Objects)).Geometry.GetUserString(kernel.DOCUMENT_FORM_KEY)
          == "exact" and summaries(legacy_model) == ai_summary,
          "historical missing form is recognized only after exact correlation and canonicalized")
    document.save_model(legacy_model, root / "legacy-box-resaved.narwhal")
    check(document.load_model(root / "legacy-box-resaved.narwhal").object_count() == 1,
          "legacy missing-form file saves and reopens without losing its master")
    legacy_proxy.SetUserString(kernel.DOCUMENT_FORM_KEY, "unknown-future-form")
    assert legacy_meta.Write(str(legacy_stage / "document.3dm"), 8)
    native_document.pack(legacy_stage, root / "unknown-form.narwhal")
    try:
        document.load_model(root / "unknown-form.narwhal")
    except document.NarwhalDocumentError as exc:
        check("no recognized document proxy form" in str(exc),
              "a nonempty unknown form is not treated as a missing historical marker")
    else:
        raise AssertionError("unknown proxy form admitted")
    legacy_proxy.SetUserString(kernel.DOCUMENT_FORM_KEY, "")
    legacy_proxy.Transform(r3.Transform.Translation(0.125, 0, 0))
    assert legacy_meta.Write(str(legacy_stage / "document.3dm"), 8)
    native_document.pack(legacy_stage, root / "legacy-box-mismatch.narwhal")
    try:
        document.load_model(root / "legacy-box-mismatch.narwhal")
    except document.NarwhalDocumentError as exc:
        check("proxy bounds" in str(exc), "missing form cannot bypass exact proxy correlation")
    else:
        raise AssertionError("mismatched legacy proxy admitted")

    unknown = dict(manifest, schema=999)
    check(not document_compat.supported_manifest(unknown, 4), "unknown document schema fails explicitly")
    unknown_summary = dict(manifest, summary_codec="unknown-v999")
    check(not document_compat.supported_manifest(unknown_summary, 4),
          "unknown summary semantics cannot reinterpret historical measurements")
    unknown = dict(manifest, bintools_format=999)
    check(not document_compat.supported_manifest(unknown, 4), "unknown binary encoding fails explicitly")

print(f"{checks}/{checks} historical document compatibility checks passed")
