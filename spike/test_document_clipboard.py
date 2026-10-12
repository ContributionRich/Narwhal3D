"""Focused exact document clipboard tests: CopyToClipboard, Cut, and Paste."""
import base64
import importlib.util
import json
import math
import os
import sys
import types
import uuid
import zlib

import rhino3dm as r3


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "narwhal3d")
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [ADDON]
pkg.__package__ = "narwhal3d"
sys.modules["narwhal3d"] = pkg


def _load(name):
    spec = importlib.util.spec_from_file_location(
        f"narwhal3d.{name}", os.path.join(ADDON, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"narwhal3d.{name}"] = module
    spec.loader.exec_module(module)
    return module


_load("cplane")
bridge = _load("bridge")
model_module = _load("model")
_load("options")
document_clipboard = _load("document_clipboard")
commands = _load("commands")
picture = _load("picture")
from narwhal3d import kernel  # noqa: E402
from narwhal3d.kernel import occt_backend as ob  # noqa: E402


checks = 0
failures = []


def check(condition, label):
    global checks
    checks += 1
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


def object_named(model, name):
    return next((obj for obj in model.file.Objects
                 if (obj.Attributes.Name or "") == name), None)


def layer_index(model, name):
    return next((index for index in range(len(model.file.Layers))
                 if (model.file.Layers[index].Name or "") == name), None)


def group_rows(model):
    return [(index, model.file.Groups.FindIndex(index).Name)
            for index in range(len(model.file.Groups))]


def repack(document):
    raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return document_clipboard.PREFIX + base64.b64encode(zlib.compress(raw, 9)).decode("ascii")


def undo_once(context):
    record = context.undo.undo(context.model.snapshot())
    if record is not None:
        context.model.restore(record.before)
    return record


def redo_once(context):
    record = context.undo.redo(context.model.snapshot())
    if record is not None:
        context.model.restore(record.after)
    return record


class Display:
    def __init__(self, name):
        self.name = name


class Clipboard:
    def __init__(self, text=None, fail_write=False):
        self.text = text
        self.fail_write = fail_write
        self.writes = 0

    def set(self, text):
        self.writes += 1
        if self.fail_write:
            raise RuntimeError("simulated clipboard failure")
        self.text = text

    def get(self):
        return self.text


class Context:
    """Headless command context over a real model and real undo stack."""

    def __init__(self, model=None, clipboard=None, selected=()):
        self.model = model or model_module.NarwhalModel()
        self.clipboard = clipboard or Clipboard()
        self.selected = list(selected)
        self.selected_after = []
        self.rebuilds = 0
        self.undo = commands.undo.UndoStack()

    def selected_objects(self):
        return [Display(name) for name in self.selected]

    def set_clipboard_text(self, text):
        return self.clipboard.set(text)

    def get_clipboard_text(self):
        return self.clipboard.get()

    def rebuild_display(self):
        self.rebuilds += 1
        return len(self.model.file.Objects)

    def select_by_names(self, names, already_expanded=False):
        self.selected_after = list(names)
        self.selected = list(names)
        return list(names)

    def begin_undo(self, name):
        return self.undo.begin(name)

    def end_undo(self):
        return self.undo.end()

    def note_change(self, name=None):
        if not self.undo.enabled:
            return False
        implicit = not self.undo.recording
        if implicit:
            self.undo.begin(name or "Change")
        captured = self.undo.capture(self.model.snapshot())
        if implicit:
            self.undo.end()
        return captured


def add_layer(model, name, color, visible=True, locked=False):
    layer = r3.Layer()
    layer.Name = name
    layer.Color = (*color, 255)
    layer.Visible = visible
    layer.Locked = locked
    return model.file.Layers.Add(layer)


def add_object(model, geometry, name, layer, *, visible=True,
               color=(0, 0, 0, 255), mode=r3.ObjectMode.Normal,
               user_text=None, material=None):
    attributes = r3.ObjectAttributes()
    attributes.Name = name
    attributes.LayerIndex = layer
    attributes.Visible = visible
    attributes.ObjectColor = color
    attributes.ColorSource = r3.ObjectColorSource.ColorFromObject
    attributes.Mode = mode
    if user_text:
        for key, value in user_text.items():
            attributes.SetUserString(key, value)
    if material is not None:
        attributes.MaterialIndex = material
        attributes.MaterialSource = r3.ObjectMaterialSource.MaterialFromObject
    model.file.Objects.Add(geometry, attributes)
    return object_named(model, name)


def source_document():
    source = model_module.NarwhalModel()
    existing = add_layer(source, "Existing", (20, 30, 40))
    missing = add_layer(source, "Fabrication", (70, 80, 90), visible=False, locked=True)

    point = r3.Point(r3.Point3d(12.5, -3.25, 47.75))
    point.SetUserString("geometry-key", "geometry-value")
    first = add_object(
        source, point, "Part", existing, visible=False,
        color=(11, 22, 33, 255), mode=r3.ObjectMode.Hidden,
        user_text={"object-key": "object-value"})

    material = r3.Material()
    material.Name = "Picture material"
    texture = r3.Texture()
    texture.FileName = "/portable/assets/reference-image.png"
    material.SetBitmapTexture(texture)
    material_index = source.file.Materials.Add(material)
    frame = picture.make_frame(
        "/portable/assets/reference-image.png",
        (4.0, 5.0, 6.0), (8.0, 0.0, 0.0), (0.0, 0.0, 3.0))
    second = add_object(
        source, bridge.make_picture(frame), "Reference", missing,
        color=(101, 102, 103, 255), user_text={"kind": "picture"},
        material=material_index)

    outsider = add_object(
        source, r3.Point(r3.Point3d(-100, -100, -100)), "Outside", existing)
    group = r3.Group()
    group.Name = "Assembly"
    source.file.Groups.Add(group)
    for item in (first, second, outsider):
        item.Attributes.AddToGroup(0)
    return source


print("-- registration and strict public-neutral format --")
for command_name in ("copytoclipboard", "cut", "paste"):
    check(command_name in commands.COMMANDS,
          f"{command_name} is registered")
check(document_clipboard.PREFIX.startswith("application/x-narwhal3d-document-fragment"),
      "fragment has a unique recognizable MIME-like text prefix")
empty_context = Context(selected=())
empty_copy = commands.CopyToClipboardCmd()
empty_message, empty_done = empty_copy.begin(empty_context)
check(empty_message is None and empty_done is False
      and empty_copy.prompt == "Select objects to copy:"
      and empty_context.clipboard.writes == 0,
      "empty selection honestly prompts and does not claim a copy")


print("\n-- Copy retains sources and captures exact selected document state --")
source = source_document()
clip = Clipboard("old clipboard")
copy_context = Context(source, clip, ("Part", "Reference"))
copy_message, copy_done = commands.CopyToClipboardCmd().execute(copy_context)
check(copy_done and copy_message.startswith("Copied 2 object"), copy_message)
check(len(source.file.Objects) == 3 and object_named(source, "Part") is not None,
      "CopyToClipboard retains every source object")
check(clip.writes == 1 and clip.text.startswith(document_clipboard.PREFIX),
      "CopyToClipboard performs one portable text write")
decoded = document_clipboard.decode_fragment(clip.text)
check(len(decoded.objects) == 2, "the fragment contains exactly the selected subset")
check(decoded.objects[0].geometry.GetUserString("geometry-key") == "geometry-value",
      "geometry user strings survive CommonObject encode/decode")

old_text = clip.text
original_encode = document_clipboard.encode_fragment
document_clipboard.encode_fragment = lambda *_args: (_ for _ in ()).throw(
    document_clipboard.ClipboardFormatError("unsupported selected object"))
try:
    failed_message, _ = commands.CopyToClipboardCmd().execute(copy_context)
finally:
    document_clipboard.encode_fragment = original_encode
check("unsupported selected object" in failed_message and clip.text == old_text,
      "failed capture is atomic and does not replace the clipboard")


print("\n-- same-document Paste mints fresh IDs/names/groups without touching sources --")
source_ids = {str(obj.Attributes.Id) for obj in source.file.Objects}
paste_same = Context(source, clip)
paste_command = commands.PasteCmd()
same_message, same_done = paste_command.begin(paste_same)
check(same_done and same_message.startswith("Pasted 2 object"), same_message)
check(paste_same.undo.can_undo and paste_same.undo.undo_name == "Paste",
      "Paste is one undoable document operation")
check(len(source.file.Objects) == 5, "same-document Paste adds two and retains three sources")
check(len(set(paste_command.pasted_names)) == 2
      and not {name.casefold() for name in paste_command.pasted_names}.intersection(
          {"part", "reference"}),
      "same-document Paste gives every object a unique fresh display name")
pasted_same = [object_named(source, name) for name in paste_command.pasted_names]
check(all(str(item.Attributes.Id) not in source_ids for item in pasted_same),
      "pasted objects have fresh object IDs")
check(all(item.Attributes.GetGroupList2() == [1] for item in pasted_same),
      "pasted subset members attach only to one fresh group")
check(object_named(source, "Outside").Attributes.GetGroupList2() == [0]
      and all(0 not in item.Attributes.GetGroupList2() for item in pasted_same),
      "pasted objects do not attach to the original source group")
check(group_rows(source) == [(0, "Assembly"), (1, "Assembly Copy")],
      "same-document Paste creates a fresh uniquely named group-table row")
check(paste_same.selected_after == list(paste_command.pasted_names),
      "Paste selects and returns the pasted object names")

same_record = undo_once(paste_same)
check(same_record is not None and same_record.name == "Paste"
      and len(source.file.Objects) == 3
      and group_rows(source) == [(0, "Assembly")],
      "Undo Paste restores the source objects and original group table")
redo_record = redo_once(paste_same)
check(redo_record is not None and len(source.file.Objects) == 5
      and group_rows(source) == [(0, "Assembly"), (1, "Assembly Copy")],
      "Redo Paste restores the independent objects and group")


print("\n-- fresh-document Paste restores coordinates, attributes, layers, and Picture material --")
target = model_module.NarwhalModel()
existing_target = add_layer(target, "Existing", (200, 201, 202), visible=True, locked=False)
fresh_context = Context(target, clip)
fresh_command = commands.PasteCmd()
fresh_message, _ = fresh_command.begin(fresh_context)
check(fresh_message.startswith("Pasted 2 object"), fresh_message)
check(len(target.file.Objects) == 2, "fresh document receives both objects")
check(sum(1 for index in range(len(target.file.Layers))
          if target.file.Layers[index].Name == "Existing") == 1,
      "an existing destination layer is reused by name")
part = object_named(target, "Part")
reference = object_named(target, "Reference")
point = part.Geometry.Location
check((point.X, point.Y, point.Z) == (12.5, -3.25, 47.75),
      "geometry keeps exact world X/Y/Z coordinates")
check(int(part.Attributes.LayerIndex) == existing_target,
      "object on an existing layer remaps to that destination layer")
check(part.Attributes.Visible is False
      and tuple(part.Attributes.ObjectColor) == (11, 22, 33, 255)
      and part.Attributes.ColorSource == r3.ObjectColorSource.ColorFromObject
      and part.Attributes.Mode == r3.ObjectMode.Hidden
      and part.Attributes.GetUserString("object-key") == "object-value",
      "visible/color/source/mode/object user text survive")
check(part.Geometry.GetUserString("geometry-key") == "geometry-value",
      "pasted geometry user text survives")
fabrication = layer_index(target, "Fabrication")
check(fabrication is not None, "a missing source layer is created")
created_layer = target.file.Layers[fabrication]
check(tuple(created_layer.Color) == (70, 80, 90, 255)
      and created_layer.Visible is False and created_layer.Locked is True,
      "the created layer retains its available fields")
check(int(reference.Attributes.LayerIndex) == fabrication,
      "the Picture remaps to its recreated source layer")
check(reference.Attributes.MaterialSource == r3.ObjectMaterialSource.MaterialFromObject,
      "object-assigned material source survives")
material = target.file.Materials[int(reference.Attributes.MaterialIndex)]
bitmap = material.GetBitmapTexture()
check(material.Name == "Picture material"
      and bitmap is not None
      and bitmap.FileName == "/portable/assets/reference-image.png",
      "object material and bitmap image path are cloned and remapped")
frame = bridge.picture_frame(reference.Geometry)
check(frame is not None and frame["image"] == "/portable/assets/reference-image.png",
      "Picture frame and image path survive geometry encoding")
check(reference.Attributes.GetGroupList2() == part.Attributes.GetGroupList2() == [0]
      and group_rows(target) == [(0, "Assembly")],
      "selected subset relationships are recreated in one fresh destination group")


print("\n-- repeated Paste creates independent objects, materials, and groups --")
first_ids = {str(obj.Attributes.Id) for obj in target.file.Objects}
first_group = tuple(part.Attributes.GetGroupList2())
first_material = int(reference.Attributes.MaterialIndex)
repeat = commands.PasteCmd()
repeat_message, _ = repeat.begin(fresh_context)
repeated = [object_named(target, name) for name in repeat.pasted_names]
check(repeat_message.startswith("Pasted 2 object") and len(target.file.Objects) == 4,
      "a second Paste independently adds the selection again")
check(all(str(obj.Attributes.Id) not in first_ids for obj in repeated)
      and len({str(obj.Attributes.Id) for obj in target.file.Objects}) == 4,
      "repeated Paste uses independent fresh object IDs")
check(all(tuple(obj.Attributes.GetGroupList2()) != first_group for obj in repeated)
      and group_rows(target) == [(0, "Assembly"), (1, "Assembly Copy")],
      "repeated Paste creates a distinct fresh group")
repeated_picture = next(obj for obj in repeated if bridge.picture_frame(obj.Geometry))
check(int(repeated_picture.Attributes.MaterialIndex) != first_material,
      "repeated Paste clones an independent object material")
check(len({obj.Attributes.Name.casefold() for obj in target.file.Objects}) == 4,
      "repeated Paste keeps all application display names unique")


print("\n-- Cut deletes only after complete capture and clipboard write --")
cut_model = model_module.NarwhalModel()
cut_object = add_object(cut_model, r3.Point(r3.Point3d(2, 3, 9)), "CutMe", 0)
cut_clip = Clipboard()
cut_context = Context(cut_model, cut_clip, ("CutMe",))
cut_message, cut_done = commands.CutCmd().begin(cut_context)
check(cut_done and cut_message.startswith("Cut 1 object"), cut_message)
check(len(cut_model.file.Objects) == 0 and cut_clip.text.startswith(document_clipboard.PREFIX),
      "successful Cut writes first and then deletes the selected source")
check(cut_context.undo.can_undo and cut_context.undo.undo_name == "Cut",
      "Cut deletion is one undoable document operation")
cut_record = undo_once(cut_context)
check(cut_record is not None and object_named(cut_model, "CutMe") is not None,
      "Undo Cut restores the exact deleted source object")
redo_once(cut_context)
check(len(cut_model.file.Objects) == 0,
      "Redo Cut removes the source again")

failed_model = model_module.NarwhalModel()
add_object(failed_model, r3.Point(r3.Point3d(7, 8, 9)), "KeepMe", 0)
failed_clip = Clipboard("untouched", fail_write=True)
failed_context = Context(failed_model, failed_clip, ("KeepMe",))
failed_cut_message, _ = commands.CutCmd().begin(failed_context)
check("no objects were deleted" in failed_cut_message
      and len(failed_model.file.Objects) == 1
      and failed_clip.text == "untouched",
      "Cut never deletes or replaces fallback state when clipboard write fails")
check(not failed_context.undo.can_undo,
      "failed Cut creates no empty undo record")


print("\n-- foreign and malformed clipboard text is refused without mutation --")
refusal_target = model_module.NarwhalModel()
foreign = Context(refusal_target, Clipboard("ordinary clipboard text"))
foreign_message, _ = commands.PasteCmd().begin(foreign)
check("text and images are not implemented" in foreign_message
      and len(refusal_target.file.Objects) == 0,
      "foreign text is explicitly refused and does not mutate the document")
malformed = Context(refusal_target, Clipboard(document_clipboard.PREFIX + "not-base64"))
malformed_message, _ = commands.PasteCmd().begin(malformed)
check("malformed Narwhal3D document fragment" in malformed_message
      and len(refusal_target.file.Objects) == 0,
      "malformed fragment is explicitly refused and does not mutate the document")
check("text and images are not implemented" in foreign_message,
      "refusal does not pretend image/text clipboard paste is implemented")
cyclic_document = document_clipboard._unpack(clip.text)
cyclic_key = next(iter(cyclic_document["layers"]))
cyclic_document["layers"][cyclic_key]["parent"] = cyclic_key
cyclic = Context(refusal_target, Clipboard(repack(cyclic_document)))
cyclic_message, _ = commands.PasteCmd().begin(cyclic)
check("parent cycle" in cyclic_message
      and len(refusal_target.file.Objects) == 0
      and not cyclic.undo.can_undo,
      "cyclic layer metadata is refused before document mutation")


print("\n-- a mid-apply failure rolls back every document table --")
rollback_target = model_module.NarwhalModel()
rollback_context = Context(rollback_target, clip)
initial_counts = (len(rollback_target.file.Objects), len(rollback_target.file.Layers),
                  len(rollback_target.file.Materials), len(rollback_target.file.Groups))
real_object_add = r3.File3dmObjectTable.Add


def fail_reference(table, geometry, attributes):
    if attributes.Name == "Reference":
        return uuid.UUID(int=0)
    return real_object_add(table, geometry, attributes)


r3.File3dmObjectTable.Add = fail_reference
try:
    rollback_message, _ = commands.PasteCmd().begin(rollback_context)
finally:
    r3.File3dmObjectTable.Add = real_object_add
rollback_counts = (len(rollback_target.file.Objects), len(rollback_target.file.Layers),
                   len(rollback_target.file.Materials), len(rollback_target.file.Groups))
check("could not add object" in rollback_message
      and rollback_counts == initial_counts
      and not rollback_context.undo.can_undo,
      "failed Paste restores objects, layers, materials, groups, and undo state")


print("\n-- analytic primitive and master+recipe solid survive exact clipboard round-trip --")
if not ob.available():
    check(False, f"OCCT is required for clipboard exactness ({ob.unavailable_reason()})")
else:
    exact_source = model_module.NarwhalModel()
    cylinder = bridge.make_cylinder((20, 30, 7), 2.0, 5.0)
    solid = bridge.boolean_brep(
        bridge.make_box((0, 0, 0), (4, 4, 0), 4),
        bridge.make_cylinder((4, 2, 0), 1.0, 4.0), "UNION")
    check(kernel.recipe_of(cylinder) is not None,
          "fixture primitive starts with a construction recipe")
    check(kernel.master_of(solid) is not None and kernel.recipe_of(solid) is not None,
          "fixture boolean starts with a master and recipe tree")
    add_object(exact_source, cylinder, "AnalyticCylinder", 0)
    add_object(exact_source, solid, "MasterSolid", 0)
    exact_clip = Clipboard()
    exact_copy = Context(exact_source, exact_clip, ("AnalyticCylinder", "MasterSolid"))
    commands.CopyToClipboardCmd().execute(exact_copy)
    exact_target = model_module.NarwhalModel()
    exact_paste = Context(exact_target, exact_clip)
    exact_command = commands.PasteCmd()
    exact_message, _ = exact_command.begin(exact_paste)
    check(exact_message.startswith("Pasted 2 object"), exact_message)
    pasted_cylinder = object_named(exact_target, "AnalyticCylinder").Geometry
    pasted_solid = object_named(exact_target, "MasterSolid").Geometry
    cylinder_recipe = kernel.recipe_of(pasted_cylinder)
    cylinder_shape = ob.from_recipe(cylinder_recipe) if cylinder_recipe else None
    check(cylinder_recipe is not None and cylinder_shape is not None
          and ob.is_airtight(cylinder_shape)
          and abs(ob.volume(cylinder_shape) - math.pi * 2.0 ** 2 * 5.0) < 1e-9,
          "analytic primitive recipe remains exact, airtight, and at world placement")
    pasted_master = kernel.master_of(pasted_solid)
    check(kernel.recipe_of(pasted_solid) is not None
          and pasted_master is not None and ob.is_airtight(pasted_master),
          "master/proxy solid gets a fresh exact airtight master from its recipe")
    check(kernel.master_key_of(pasted_solid) != kernel.master_key_of(solid),
          "pasted master/proxy solid never shares the source process master key")
    drilled = bridge.boolean_brep(
        pasted_solid, bridge.make_cylinder((2, 2, -1), 0.5, 6), "DIFFERENCE")
    check(kernel.master_of(drilled) is not None
          and kernel.recipe_of(drilled) is not None
          and ob.is_airtight(kernel.master_of(drilled)),
          "pasted master+recipe solid remains boolean-ready without proxy tessellation")


print()
if failures:
    print(f"{checks - len(failures)}/{checks} checks passed")
    for failure in failures:
        print("FAIL:", failure)
    sys.exit(1)
print(f"all {checks} checks passed")
