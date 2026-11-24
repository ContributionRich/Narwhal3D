"""Native Import merge regression. Python is actual Blender/SDK orchestration only.

The archive is written/read by the existing Rust native document boundary. No owner
geometry or alternate archive parser is used. Run inside Blender with --python-exit-code 1.
"""
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg
sys.path.insert(0, str(ROOT))
import bpy
import rhino3dm as r3
from narwhal3d import bridge, commands, import_commands as imports, kernel, model
from narwhal3d import filedialog, narwhal_document, picture
from narwhal3d.kernel import occt_backend as occt

checks = 0


def check(condition, label):
    global checks
    checks += 1
    assert condition, label


def entries(document):
    return {o.Attributes.Name: o for o in document.file.Objects}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def signature(geom):
    master = kernel.master_of(geom)
    check(master is not None and occt.is_airtight(master), "imported master remains airtight")
    return (occt.volume(master), occt.tight_bbox(master), occt.face_types(master))


def source_fixture(directory):
    source = model.NarwhalModel()
    source.set_unit_system("Inches")
    source.file.Strings["project"] = "source-only metadata"
    layer = source.add_layer("Imported machining", (180, 70, 20))
    group = r3.Group()
    group.Name = "Assembly"
    source.file.Groups.Add(group)
    source.add_object(bridge.make_line((1, 2, 3), (2, 2, 3)), "ImportedCurve", layer)
    solid = bridge.make_cylinder((7, -4, 3), 1, 2)
    kernel.register_master(solid, bridge._occt_operand(solid))
    source.add_object(solid, "ImportedSolid", layer)
    image_path = str(directory / "pixel.png")
    image = bpy.data.images.new("native-import-pixel", 1, 1)
    image.filepath_raw, image.file_format = image_path, "PNG"
    image.save()
    frame = picture.make_frame(image_path, (0, 0, 0), (2, 0, 0), (0, 1, 0))
    oid, _ = source.add_object(bridge.make_picture(frame), "ImportedPicture", layer)
    material = source.add_picture_material(image_path)
    source.set_object_material(oid, material)
    for obj in source.file.Objects:
        obj.Attributes.AddToGroup(0)
        obj.Attributes.SetUserString("manufacturing", "preserved")
        obj.Attributes.PlotWeight = 0.125
        obj.Attributes.PlotWeightSource = r3.ObjectPlotWeightSource.PlotWeightFromObject
    path = directory / "source.NARWHAL"
    check(narwhal_document.save_model(source, path), "real native source archive saved")
    return path


def main():
    bpy.data.batch_remove(list(bpy.data.objects))
    with tempfile.TemporaryDirectory(prefix="native-import-host-") as temporary:
        directory = Path(temporary)
        source_path = source_fixture(directory)
        source_hash = digest(source_path)
        dest = model.NarwhalModel()
        dest.path = str(directory / "active.narwhal")
        dest.file.Strings["project"] = "active metadata"
        group = r3.Group()
        group.Name = "Assembly"
        dest.file.Groups.Add(group)
        ctx = commands.NarwhalContext(dest)
        ctx.add_geometry(bridge.make_cylinder((0, 0, 0), 3, 4), "Existing", 0)
        existing = entries(dest)["Existing1"]
        existing_id = str(existing.Attributes.Id)
        existing_master = bridge._occt_operand(existing.Geometry)
        existing_bounds = occt.tight_bbox(existing_master)
        ctx.undo.clear()
        before_ids = {str(o.Attributes.Id) for o in dest.file.Objects}
        before_displays = {o.as_pointer() for o in bpy.data.objects}
        before_path = dest.path
        with patch.object(filedialog, "ask_open_path", return_value=(filedialog.CANCEL, None)):
            message, done = imports.ImportCmd().begin(ctx)
        check(done and "cancelled" in message.lower(), "browser cancellation is explicit")
        check(ctx.undo.undo_names() == [] and dest.path == before_path
              and {o.as_pointer() for o in bpy.data.objects} == before_displays,
              "cancel creates no Undo, document or display mutation")
        broken = directory / "broken.narwhal"
        broken.write_bytes(b"not a native archive")
        with patch.object(filedialog, "ask_open_path", return_value=(filedialog.OK, str(broken))):
            message, done = imports.ImportCmd().begin(ctx)
        check(done and message.startswith("Import:"), "malformed native archive refused by reader")
        check(ctx.undo.undo_names() == [] and {str(o.Attributes.Id) for o in dest.file.Objects} == before_ids,
              "malformed archive fails before publication and Undo capture")

        blocked = model.NarwhalModel()
        definition_index = blocked.file.InstanceDefinitions.Add(
            "Native block", "", "", "", r3.Point3d(0, 0, 0),
            (bridge.make_line((0, 0, 0), (1, 0, 0)),), (r3.ObjectAttributes(),))
        definition = blocked.file.InstanceDefinitions[definition_index]
        blocked.add_object(r3.InstanceReference(definition.Id, r3.Transform.Identity()), "BlockedInstance", 0)
        blocked_path = directory / "block.narwhal"
        check(narwhal_document.save_model(blocked, blocked_path), "write genuine native block fixture")
        with patch.object(filedialog, "ask_open_path", return_value=(filedialog.OK, str(blocked_path))):
            message, done = imports.ImportCmd().begin(ctx)
        check(done and "block definition/instance mapping is not supported" in message,
              "unsupported native block resources are refused by name, never silently omitted")
        check(ctx.undo.undo_names() == [] and {str(o.Attributes.Id) for o in dest.file.Objects} == before_ids,
              "unsupported resources fail before publishing any imported object")

        with patch.object(filedialog, "ask_open_path", return_value=(filedialog.OK, str(source_path))):
            message, done = imports.ImportCmd().begin(ctx)
        check(done and message.startswith("Imported 3 object(s)"), message)
        check(ctx.model is dest and dest.path == before_path and dest.unit_system() == "Millimeters",
              "Import merges without replacing active identity, path or units")
        check(dest.file.Strings["project"] == "active metadata", "source document strings do not overwrite active metadata")
        check(ctx.undo.undo_names() == ["Import"], "whole native import is one Import Undo record")
        got = entries(dest)
        check(set(got) == {"Existing1", "ImportedCurve", "ImportedSolid", "ImportedPicture"}, "all source objects merged")
        check(str(got["Existing1"].Attributes.Id) == existing_id
              and occt.tight_bbox(bridge._occt_operand(got["Existing1"].Geometry)) == existing_bounds,
              "existing object identity and exact master are preserved")
        for name in ("ImportedCurve", "ImportedSolid", "ImportedPicture"):
            attrs = got[name].Attributes
            check(dest.file.Layers[attrs.LayerIndex].Name == "Imported machining", "source layer mapped by name")
            check(attrs.GetGroupList2() == [1], "source group receives independent destination membership")
            check(attrs.GetUserString("manufacturing") == "preserved" and attrs.PlotWeight == 0.125,
                  "full source object attributes are preserved")
        start = got["ImportedCurve"].Geometry.PointAtStart
        check(math.dist((start.X, start.Y, start.Z), (25.4, 50.8, 76.2)) < 1e-8, "curve coordinates scaled from inches")
        volume, bounds, types = signature(got["ImportedSolid"].Geometry)
        check(math.isclose(volume, 2 * math.pi * 25.4**3, rel_tol=1e-10)
              and math.isclose(bounds[0][0], 6 * 25.4, abs_tol=1e-7)
              and types == {"CYLINDER": 1, "PLANE": 2}, "translated solid's exact master scales with its proxy")
        imported_frame = bridge.picture_frame(got["ImportedPicture"].Geometry)
        check(Path(imported_frame["image"]).is_file() and math.isclose(imported_frame["u"][0], 50.8),
              "picture asset survives outside native-load temporary with scaled frame")
        check(digest(source_path) == source_hash, "native source archive remains unchanged")
        original_native = narwhal_document.load_model(source_path)
        source_ids = {str(o.Attributes.Id) for o in original_native.file.Objects}
        after_ids = {str(o.Attributes.Id) for o in dest.file.Objects}
        check(not source_ids.intersection(after_ids - before_ids), "imported objects receive fresh independent UUIDs")
        (directory / "pixel.png").unlink()
        check(Path(imported_frame["image"]).is_file(), "imported picture survives removal of its source image")
        material = dest.file.Materials[got["ImportedPicture"].Attributes.MaterialIndex]
        check(material.GetBitmapTexture().FileName == imported_frame["image"], "picture material follows its preserved asset")
        check(ctx.undo_last() == "Import" and {str(o.Attributes.Id) for o in dest.file.Objects} == before_ids,
              "one Undo restores the original active objects")
        check(len(dest.file.Groups) == 1 and len(dest.file.Layers) == 1, "Undo restores group/layer tables")
        check(ctx.redo_last() == "Import" and {str(o.Attributes.Id) for o in dest.file.Objects} == after_ids,
              "one Redo restores exact imported identities")
        check(signature(entries(dest)["ImportedSolid"].Geometry) == (volume, bounds, types), "Redo retains exact imported geometry")
        saved = directory / "merged.narwhal"
        check(narwhal_document.save_model(dest, saved), "merged active document saves as a real native project")
        reopened = narwhal_document.load_model(saved)
        narwhal_document.commit_masters(reopened)
        check(set(entries(reopened)) == set(entries(dest)), "native save/reload retains all merged objects")
        check(signature(entries(reopened)["ImportedSolid"].Geometry) == (volume, bounds, types), "native save/reload retains exact scaled master")
        check(entries(reopened)["ImportedSolid"].Attributes.GetGroupList2() == [1], "native save/reload retains mapped groups")
        opened, message = imports.open_foreign(str(source_path))
        check(opened is not None and opened.unit_system() == "Inches"
              and opened.file.Strings["project"] == "source-only metadata",
              "whole-document Open fallback adopts source metadata and units instead of merging")
        check(digest(source_path) == source_hash, "Open fallback also leaves native source unchanged")
    print(f"NATIVE IMPORT PASSED ({checks} checks)")


if __name__ == "__main__":
    main()
