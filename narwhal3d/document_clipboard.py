"""Portable Narwhal3D document-fragment clipboard encoding.

The clipboard payload is public-neutral text: a recognizable versioned prefix followed by
base64(zlib(JSON)). Geometry, layers, materials, and groups use rhino3dm CommonObject
archive dictionaries. ObjectAttributes are the exception: this rhino3dm build decodes their
archive as an unusable base CommonObject, so the document fields the app supports are
encoded explicitly instead.

This module has no Blender, UI, model, or platform dependency. Commands provide clipboard
transport and apply decoded fragments to a document.
"""
from __future__ import annotations

import base64
import json
import zlib
from dataclasses import dataclass

import rhino3dm as r3


FORMAT = "narwhal3d-document-fragment"
VERSION = 1
PREFIX = "application/x-narwhal3d-document-fragment;version=1+zlib+base64,"
MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024

_ATTRIBUTE_FIELDS = (
    "Name",
    "LayerIndex",
    "Visible",
    "ObjectColor",
    "ColorSource",
    "Mode",
    "MaterialIndex",
    "MaterialSource",
)
_ENUMS = {
    "ObjectColorSource": r3.ObjectColorSource,
    "ObjectMode": r3.ObjectMode,
    "ObjectMaterialSource": r3.ObjectMaterialSource,
}
_process_text = None


class ClipboardFormatError(ValueError):
    """The clipboard does not contain a valid Narwhal3D document fragment."""


@dataclass
class DecodedObject:
    geometry: object
    attributes: r3.ObjectAttributes
    layer_key: str
    material_key: str | None
    group_keys: tuple[str, ...]


@dataclass
class DecodedLayer:
    layer: r3.Layer
    parent_key: str | None
    material_key: str | None


@dataclass
class DecodedFragment:
    objects: tuple[DecodedObject, ...]
    layers: dict[str, DecodedLayer]
    materials: dict[str, r3.Material]
    groups: dict[str, r3.Group]


def remember_process_text(text: str) -> None:
    """Remember a successful clipboard write for sessions without a system clipboard."""
    global _process_text
    _process_text = str(text)


def process_text() -> str | None:
    """Return the last successfully written in-process fragment, if any."""
    return _process_text


def _json_value(value):
    enum_name = type(value).__name__
    if enum_name in _ENUMS and hasattr(value, "value"):
        return {"enum": enum_name, "value": int(value.value)}
    if isinstance(value, (tuple, list)):
        return list(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise ClipboardFormatError(
        f"unsupported object attribute value {type(value).__name__}"
    )


def _attribute_record(attributes) -> dict:
    fields = {}
    for name in _ATTRIBUTE_FIELDS:
        try:
            fields[name] = _json_value(getattr(attributes, name))
        except ClipboardFormatError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ClipboardFormatError(f"could not read object attribute {name}: {exc}") from exc
    try:
        user_strings = [[str(key), str(value)]
                        for key, value in (attributes.GetUserStrings2() or [])]
    except Exception as exc:  # noqa: BLE001
        raise ClipboardFormatError(f"could not read object user text: {exc}") from exc
    return {"fields": fields, "user_strings": user_strings}


def _decode_attributes(record) -> r3.ObjectAttributes:
    if not isinstance(record, dict) or not isinstance(record.get("fields"), dict):
        raise ClipboardFormatError("malformed object attributes")
    attributes = r3.ObjectAttributes()
    fields = record["fields"]
    for name in _ATTRIBUTE_FIELDS:
        if name not in fields:
            raise ClipboardFormatError(f"object attributes omit {name}")
        value = fields[name]
        if isinstance(value, dict) and "enum" in value:
            enum_type = _ENUMS.get(value.get("enum"))
            if enum_type is None:
                raise ClipboardFormatError(f"unsupported attribute enum {value.get('enum')!r}")
            try:
                value = enum_type(int(value["value"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise ClipboardFormatError(f"invalid {name} enum value") from exc
        elif name == "ObjectColor":
            if not isinstance(value, list) or len(value) not in (3, 4):
                raise ClipboardFormatError("invalid ObjectColor")
            try:
                value = tuple(int(component) for component in value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ClipboardFormatError("invalid ObjectColor") from exc
        try:
            setattr(attributes, name, value)
        except Exception as exc:  # noqa: BLE001
            raise ClipboardFormatError(f"could not restore object attribute {name}: {exc}") from exc
    rows = record.get("user_strings", [])
    if not isinstance(rows, list):
        raise ClipboardFormatError("malformed object user text")
    for row in rows:
        if not isinstance(row, list) or len(row) != 2:
            raise ClipboardFormatError("malformed object user text row")
        if not attributes.SetUserString(str(row[0]), str(row[1])):
            raise ClipboardFormatError(f"could not restore object user text {row[0]!r}")
    return attributes


def _common_record(value, expected_type, label: str) -> dict:
    try:
        record = value.Encode()
        # This also proves every nested value is JSON-safe before the clipboard can change.
        record = json.loads(json.dumps(record, separators=(",", ":")))
        decoded = r3.CommonObject.Decode(record)
    except Exception as exc:  # noqa: BLE001
        raise ClipboardFormatError(f"could not encode {label}: {exc}") from exc
    if not isinstance(decoded, expected_type):
        raise ClipboardFormatError(
            f"could not encode {label}: decoded as {type(decoded).__name__}"
        )
    return record


def _decode_common(record, expected_type, label: str):
    if not isinstance(record, dict):
        raise ClipboardFormatError(f"malformed {label} archive")
    try:
        value = r3.CommonObject.Decode(record)
    except Exception as exc:  # noqa: BLE001
        raise ClipboardFormatError(f"could not decode {label}: {exc}") from exc
    if not isinstance(value, expected_type):
        raise ClipboardFormatError(
            f"could not decode {label}: got {type(value).__name__}"
        )
    return value


def encode_fragment(file, objects) -> str:
    """Encode selected File3dm objects atomically as one portable text fragment.

    Every selected object and every referenced table entry is encoded and test-decoded before
    text is returned. A caller can therefore leave the previous clipboard untouched whenever
    this function raises.
    """
    selected = list(objects or ())
    if not selected:
        raise ClipboardFormatError("no document objects are selected")

    layers = {}
    materials = {}
    groups = {}

    def capture_material(index: int) -> str | None:
        if not 0 <= index < len(file.Materials):
            return None
        key = f"material:{index}"
        if key not in materials:
            materials[key] = _common_record(file.Materials[index], r3.Material, key)
        return key

    def capture_layer(index: int) -> str:
        if not 0 <= index < len(file.Layers):
            raise ClipboardFormatError(f"object refers to missing layer index {index}")
        key = f"layer:{index}"
        if key in layers:
            return key
        layer = file.Layers[index]
        parent_key = None
        try:
            parent_id = str(layer.ParentLayerId)
        except Exception:  # noqa: BLE001
            parent_id = "00000000-0000-0000-0000-000000000000"
        if parent_id != "00000000-0000-0000-0000-000000000000":
            for parent_index in range(len(file.Layers)):
                if str(file.Layers[parent_index].Id) == parent_id:
                    parent_key = capture_layer(parent_index)
                    break
        try:
            material_key = capture_material(int(layer.RenderMaterialIndex))
        except Exception:  # noqa: BLE001
            material_key = None
        layers[key] = {
            "archive": _common_record(layer, r3.Layer, key),
            "parent": parent_key,
            "material": material_key,
        }
        return key

    def capture_group(index: int) -> str:
        if not 0 <= index < len(file.Groups):
            raise ClipboardFormatError(f"object refers to missing group index {index}")
        key = f"group:{index}"
        if key not in groups:
            groups[key] = _common_record(file.Groups.FindIndex(index), r3.Group, key)
        return key

    rows = []
    seen_ids = set()
    for number, fobj in enumerate(selected):
        try:
            attributes = fobj.Attributes
            geometry = fobj.Geometry
            source_id = str(attributes.Id)
        except Exception as exc:  # noqa: BLE001
            raise ClipboardFormatError(f"selected object {number + 1} is unreadable: {exc}") from exc
        if source_id in seen_ids:
            continue
        seen_ids.add(source_id)
        layer_key = capture_layer(int(attributes.LayerIndex))
        material_key = None
        try:
            if attributes.MaterialSource == r3.ObjectMaterialSource.MaterialFromObject:
                material_key = capture_material(int(attributes.MaterialIndex))
        except Exception as exc:  # noqa: BLE001
            raise ClipboardFormatError(f"could not read material for object {source_id}: {exc}") from exc
        try:
            group_keys = [capture_group(int(index))
                          for index in (attributes.GetGroupList2() or [])]
        except ClipboardFormatError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ClipboardFormatError(f"could not read groups for object {source_id}: {exc}") from exc
        rows.append({
            "source_id": source_id,
            "geometry": _common_record(geometry, r3.GeometryBase, f"object {source_id} geometry"),
            "attributes": _attribute_record(attributes),
            "layer": layer_key,
            "material": material_key,
            "groups": group_keys,
        })

    document = {
        "format": FORMAT,
        "version": VERSION,
        "objects": rows,
        "layers": layers,
        "materials": materials,
        "groups": groups,
    }
    try:
        raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
        payload = base64.b64encode(zlib.compress(raw, 9)).decode("ascii")
    except Exception as exc:  # noqa: BLE001
        raise ClipboardFormatError(f"could not build clipboard fragment: {exc}") from exc
    return PREFIX + payload


def _unpack(text: str) -> dict:
    if not isinstance(text, str):
        raise ClipboardFormatError("clipboard content is not text")
    if not text.startswith(PREFIX):
        raise ClipboardFormatError(
            "clipboard does not contain a Narwhal3D document fragment; text and images are not implemented"
        )
    encoded = text[len(PREFIX):]
    try:
        compressed = base64.b64decode(encoded.encode("ascii"), validate=True)
        inflater = zlib.decompressobj()
        raw = inflater.decompress(compressed, MAX_UNCOMPRESSED_BYTES + 1)
        if len(raw) > MAX_UNCOMPRESSED_BYTES or inflater.unconsumed_tail:
            raise ClipboardFormatError("clipboard document fragment is too large")
        raw += inflater.flush()
        if len(raw) > MAX_UNCOMPRESSED_BYTES:
            raise ClipboardFormatError("clipboard document fragment is too large")
        if not inflater.eof or inflater.unused_data:
            raise ClipboardFormatError("malformed compressed clipboard document fragment")
        value = json.loads(raw.decode("utf-8"))
    except ClipboardFormatError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ClipboardFormatError(f"malformed Narwhal3D document fragment: {exc}") from exc
    if not isinstance(value, dict) or value.get("format") != FORMAT:
        raise ClipboardFormatError("clipboard document fragment has the wrong format")
    if value.get("version") != VERSION:
        raise ClipboardFormatError(
            f"clipboard document fragment version {value.get('version')!r} is not supported"
        )
    return value


def decode_fragment(text: str) -> DecodedFragment:
    """Strictly decode and validate a fragment without mutating a document."""
    document = _unpack(text)
    raw_layers = document.get("layers")
    raw_materials = document.get("materials")
    raw_groups = document.get("groups")
    raw_objects = document.get("objects")
    if not all(isinstance(value, dict)
               for value in (raw_layers, raw_materials, raw_groups)):
        raise ClipboardFormatError("malformed clipboard document tables")
    if not isinstance(raw_objects, list) or not raw_objects:
        raise ClipboardFormatError("clipboard document fragment contains no objects")

    materials = {
        str(key): _decode_common(record, r3.Material, str(key))
        for key, record in raw_materials.items()
    }
    groups = {
        str(key): _decode_common(record, r3.Group, str(key))
        for key, record in raw_groups.items()
    }
    layers = {}
    for key, record in raw_layers.items():
        if not isinstance(record, dict):
            raise ClipboardFormatError(f"malformed layer {key!r}")
        parent_key = record.get("parent")
        material_key = record.get("material")
        if parent_key is not None and str(parent_key) not in raw_layers:
            raise ClipboardFormatError(f"layer {key!r} refers to a missing parent")
        if material_key is not None and str(material_key) not in materials:
            raise ClipboardFormatError(f"layer {key!r} refers to a missing material")
        layers[str(key)] = DecodedLayer(
            _decode_common(record.get("archive"), r3.Layer, str(key)),
            str(parent_key) if parent_key is not None else None,
            str(material_key) if material_key is not None else None,
        )

    visiting = set()
    visited = set()

    def validate_layer_parent(key):
        if key in visited:
            return
        if key in visiting:
            raise ClipboardFormatError("clipboard layers contain a parent cycle")
        visiting.add(key)
        parent_key = layers[key].parent_key
        if parent_key is not None:
            validate_layer_parent(parent_key)
        visiting.remove(key)
        visited.add(key)

    for key in layers:
        validate_layer_parent(key)

    objects = []
    for number, record in enumerate(raw_objects):
        if not isinstance(record, dict):
            raise ClipboardFormatError(f"malformed object {number + 1}")
        layer_key = str(record.get("layer"))
        if layer_key not in layers:
            raise ClipboardFormatError(f"object {number + 1} refers to a missing layer")
        material_key = record.get("material")
        if material_key is not None and str(material_key) not in materials:
            raise ClipboardFormatError(f"object {number + 1} refers to a missing material")
        group_keys = record.get("groups", [])
        if not isinstance(group_keys, list) or any(str(key) not in groups for key in group_keys):
            raise ClipboardFormatError(f"object {number + 1} refers to a missing group")
        objects.append(DecodedObject(
            _decode_common(record.get("geometry"), r3.GeometryBase,
                           f"object {number + 1} geometry"),
            _decode_attributes(record.get("attributes")),
            layer_key,
            str(material_key) if material_key is not None else None,
            tuple(str(key) for key in group_keys),
        ))
    return DecodedFragment(tuple(objects), layers, materials, groups)
