"""Read native CorelDRAW v15 curve, layer, and outline records.

The reader intentionally handles the manufacturing-vector subset directly so
Corel objects are not lost or split unpredictably by LibreOffice's SVG export.
Unsupported CDR generations/features raise :class:`CDRReadError`; the command
layer may then use its explicitly approximate converter fallback.
"""
from __future__ import annotations

import math
import re
import struct
import zipfile
import zlib
from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any, cast

UNITS_PER_MM = 10_000.0
MAX_RIFF_BYTES = 64 * 1024 * 1024
MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_RECORDS = 500_000
MAX_PATH_NODES = 2_000_000
MAX_OBJECTS = 100_000
MAX_PATHS = 500_000
MAX_LAYER_BYTES = 64 * 1024 * 1024
MAX_LAYER_NAME_BYTES = 1024 * 1024
MAX_DEPTH = 256


class CDRReadError(ValueError):
    pass


Point2 = tuple[float, float]
Bezier = tuple[Point2, Point2, Point2, Point2]
Outline = tuple[tuple[int, int, int], float, bool]


@dataclass
class Path:
    beziers: list[Bezier]
    closed: bool = False


@dataclass
class Object:
    paths: list[Path] = field(default_factory=list)
    layer: str = "Layer 1"
    rgb: tuple[int, int, int] = (0, 0, 0)
    width_mm: float = 0.0
    hairline: bool = False


@dataclass
class Drawing:
    width_mm: float
    height_mm: float
    objects: list[Object] = field(default_factory=list)
    version: int = 0


@dataclass
class _Node:
    tag: bytes
    kind: bytes = b""
    payload: bytes = b""
    children: list["_Node"] = field(default_factory=list)


def _u16(data: bytes, offset=0) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset=0) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _i32(data: bytes, offset=0) -> int:
    return struct.unpack_from("<i", data, offset)[0]


def _records(data: bytes, start: int, end: int,
             lengths: list[int] | None = None,
             state: list[int] | None = None, depth: int = 0) -> list[_Node]:
    if depth > MAX_DEPTH:
        raise CDRReadError("CDR RIFF nesting is too deep")
    if start > end or end > len(data):
        raise CDRReadError("invalid CDR record range")
    if state is None:
        state = [0]
    result = []
    pos = start
    while pos + 8 <= end:
        state[0] += 1
        if state[0] > MAX_RECORDS:
            raise CDRReadError("CDR has too many records")
        tag = data[pos:pos + 4]
        raw_length = _u32(data, pos + 4)
        length = lengths[raw_length] if lengths is not None and raw_length < len(lengths) \
            else raw_length
        stop = pos + 8 + length
        if stop > end or stop <= pos:
            raise CDRReadError("invalid CDR RIFF record length")
        payload = data[pos + 8:stop]
        node = _Node(tag=tag, payload=payload)
        if tag in (b"LIST", b"RIFF") and len(payload) >= 4:
            node.kind = payload[:4]
            if node.kind not in (b"cmpr", b"stlt"):
                node.children = _records(
                    data, pos + 12, stop, lengths, state, depth + 1)
                node.payload = b""
        result.append(node)
        pos = stop + (length & 1)
        if pos > end:
            raise CDRReadError("CDR record is missing its padding byte")
    if pos != end and (end - pos >= 8 or any(data[pos:end])):
        raise CDRReadError("trailing invalid CDR record bytes")
    return result


def _inflate(data: bytes, limit: int) -> bytes:
    try:
        decoder = zlib.decompressobj()
        result = decoder.decompress(data, limit + 1)
        if len(result) > limit or decoder.unconsumed_tail:
            raise CDRReadError("CDR compressed stream exceeds the limit")
        result += decoder.flush(limit + 1 - len(result))
    except zlib.error as exc:
        raise CDRReadError(f"invalid CDR compressed stream: {exc}") from exc
    if len(result) > limit or not decoder.eof or decoder.unused_data:
        raise CDRReadError("invalid or oversized CDR compressed stream")
    return result


def _decompress(node: _Node, record_state: list[int],
                expanded_state: list[int]) -> list[_Node]:
    payload = node.payload
    if len(payload) < 30 or payload[:4] != b"cmpr":
        raise CDRReadError("invalid compressed CDR list")
    compressed_span = _u32(payload, 4)
    expected = _u32(payload, 8)
    compressed_size = compressed_span - 8
    zstart = 28
    if compressed_size < 0 or zstart + compressed_span > len(payload):
        raise CDRReadError("invalid CDR compression lengths")
    remaining = MAX_EXPANDED_BYTES - expanded_state[0]
    records = _inflate(payload[zstart:zstart + compressed_size], remaining)
    remaining -= len(records)
    length_data = _inflate(
        payload[zstart + compressed_span:], min(32 * 1024 * 1024, remaining))
    expanded_state[0] += len(records) + len(length_data)
    if expanded_state[0] > MAX_EXPANDED_BYTES:
        raise CDRReadError("CDR expanded data exceeds the limit")
    if expected and len(records) != expected:
        raise CDRReadError("CDR compressed stream has the wrong expanded size")
    if len(length_data) % 4:
        raise CDRReadError("invalid CDR record-length table")
    lengths = [item[0] for item in struct.iter_unpack("<I", length_data)]
    return _records(records, 0, len(records), lengths, record_state)


def _all(nodes: list[_Node], tag: bytes) -> Generator[_Node, None, None]:
    for node in nodes:
        if node.tag == tag:
            yield node
        yield from _all(node.children, tag)


def _args(payload: bytes) -> tuple[int, dict[int, bytes]]:
    if len(payload) < 20:
        raise CDRReadError("short CDR loda record")
    total, count, offsets_at, types_at, kind = struct.unpack_from("<IIIII", payload)
    if total < 20 or total > len(payload) or count > 4096:
        raise CDRReadError("invalid CDR loda header")
    table_bytes = count * 4
    offsets_end = offsets_at + table_bytes
    types_end = types_at + table_bytes
    if (offsets_at < 20 or types_at < 20 or offsets_end > total or types_end > total
            or (offsets_at < types_end and types_at < offsets_end)):
        raise CDRReadError("invalid CDR loda argument table")
    arguments_at = max(offsets_end, types_end)
    offsets = list(struct.unpack_from("<" + "I" * count, payload, offsets_at))
    stored_types = list(struct.unpack_from("<" + "I" * count, payload, types_at))
    types = list(reversed(stored_types))
    result = {}
    for index, (offset, arg_type) in enumerate(zip(offsets, types)):
        stop = offsets[index + 1] if index + 1 < count else total
        if offset < arguments_at or offset > stop or stop > total:
            raise CDRReadError("invalid CDR loda argument offset")
        if arg_type == 0x03E8 and stop - offset > MAX_LAYER_NAME_BYTES:
            raise CDRReadError("CDR layer name exceeds the limit")
        if arg_type in result:
            raise CDRReadError("duplicate CDR loda argument type")
        result[arg_type] = payload[offset:stop]
    return kind, result


def _layer_name(layer: _Node, fallback: str) -> str:
    for loda in _all(layer.children, b"loda"):
        kind, args = _args(loda.payload)
        raw = args.get(0x03E8)
        if kind == 0 and raw:
            if len(raw) > MAX_LAYER_NAME_BYTES or len(raw) % 2:
                raise CDRReadError("invalid or oversized CDR layer name")
            try:
                name = raw.decode("utf-16le").rstrip("\0")
            except UnicodeDecodeError as exc:
                raise CDRReadError("invalid UTF-16 CDR layer name") from exc
            if name:
                return name
    return fallback


def _rgb(data: bytes, offset: int) -> tuple[int, int, int]:
    if offset + 12 > len(data):
        return 0, 0, 0
    model, _palette = struct.unpack_from("<HH", data, offset)
    value = _u32(data, offset + 8)
    if model == 5:  # RGB, value 0x00RRGGBB
        return (value >> 16) & 255, (value >> 8) & 255, value & 255
    if model in (2, 3):  # CMYK100/255, packed C M Y K
        c, m, y, k = (value & 255, (value >> 8) & 255,
                      (value >> 16) & 255, (value >> 24) & 255)
        scale = 100.0 if model == 2 else 255.0
        values = [round(255 * (1 - min(scale, channel + k) / scale))
                  for channel in (c, m, y)]
        return values[0], values[1], values[2]
    return 0, 0, 0


def _outlines(nodes: list[_Node]) -> dict[int, Outline]:
    result = {}
    for node in _all(nodes, b"outl"):
        data = node.payload
        if len(data) < 20:
            continue
        outline_id = _u32(data)
        pos, skip, field_id = 4, 0, 0
        for _ in range(64):
            pos += skip
            if pos + 8 > len(data):
                break
            field_id, skip = struct.unpack_from("<II", data, pos)
            pos += 8
            if field_id == 1:
                break
        if field_id != 1 or pos + 76 > len(data):
            continue
        line_type = _u16(data, pos)
        # Bit 0 means no outline; 0x20 only means scale-with-object.
        if line_type & 0x01:
            continue
        width = _i32(data, pos + 6) / UNITS_PER_MM
        color = _rgb(data, pos + 64)
        # Corel's UI calls exactly 762 units (0.003 inch) Hairline.
        result[outline_id] = color, width, abs(width - 0.0762) < 1e-9
    return result


def _own_records(nodes: list[_Node], tag: bytes) -> Generator[_Node, None, None]:
    for node in nodes:
        if node.tag == tag:
            yield node
        if node.tag == b"LIST" and node.kind in (b"obj ", b"grp "):
            continue
        yield from _own_records(node.children, tag)


def _transform(node: _Node) -> tuple[float, float, float, float, float, float]:
    """Read this object/group's first v15 affine `trfd`, excluding nested owners."""
    for record in _own_records(node.children, b"trfd"):
        data = record.payload
        if len(data) < 20:
            continue
        count, offsets_at = _u32(data, 4), _u32(data, 8)
        if not count or offsets_at + count * 4 > len(data):
            continue
        offset = _u32(data, offsets_at)
        # v13+ argument: 8-byte optional header, type 0x08, six reserved
        # bytes, then [a,b,tx,d,e,ty] as doubles. Translations are CDR
        # coordinate values and therefore divide by 10,000 for millimetres.
        if offset + 64 > len(data) or _u16(data, offset + 8) != 0x08:
            continue
        a, b, tx, d, e, ty = struct.unpack_from("<6d", data, offset + 16)
        if not all(math.isfinite(value) for value in (a, b, tx, d, e, ty)):
            raise CDRReadError("CDR transform contains a non-finite value")
        return a, b, tx / UNITS_PER_MM, d, e, ty / UNITS_PER_MM
    return 1.0, 0.0, 0.0, 0.0, 1.0, 0.0


def _compose(parent, child):
    a, b, tx, d, e, ty = parent
    f, g, ux, h, i, uy = child
    return (a * f + b * h, a * g + b * i, a * ux + b * uy + tx,
            d * f + e * h, d * g + e * i, d * ux + e * uy + ty)


def _path_data(data: bytes, page_w: float, page_h: float,
               transform: tuple[float, float, float, float, float, float],
               node_state: list[int]) -> list[Path]:
    if len(data) < 4:
        return []
    count = _u16(data)
    need = 4 + count * 8 + count
    if count < 1 or need > len(data):
        raise CDRReadError("invalid CDR path node table")
    node_state[0] += count
    if node_state[0] > MAX_PATH_NODES:
        raise CDRReadError("CDR has too many path nodes")
    points = []
    at = 4
    a, b, tx, d, e, ty = transform
    for _ in range(count):
        raw_x, raw_y = struct.unpack_from("<ii", data, at)
        x, y = raw_x / UNITS_PER_MM, raw_y / UNITS_PER_MM
        points.append((a * x + b * y + tx + page_w / 2,
                       d * x + e * y + ty + page_h / 2))
        at += 8
    types = data[at:at + count]
    paths, beziers, controls = [], [], []
    start = current = None
    closed = False

    def finish():
        nonlocal beziers, controls, start, current, closed
        if beziers:
            paths.append(Path(beziers, closed))
        beziers, controls, start, current, closed = [], [], None, None, False

    for point, node_type in zip(points, types):
        command = node_type & 0xC0
        closes = bool(node_type & 0x08)
        if command == 0x00:
            # Compatibility rule verified against CorelDRAW v15 output. A close
            # bit on a MOVE is a disconnected-subpath separator; Corel does not
            # draw a straight chord back to the prior start. Treating it as SVG
            # `Z` produced 21-34 mm diagonal defects on Curve534/538/542/548/
            # 554/559/563/567/569 and every translated clone. Endpoint close
            # flags below remain real closures (including the five circles).
            finish()
            start = current = point
            closed = False
        elif command == 0x40 and current is not None:  # line endpoint
            p0 = current
            c1 = (p0[0] + (point[0] - p0[0]) / 3,
                  p0[1] + (point[1] - p0[1]) / 3)
            c2 = (p0[0] + 2 * (point[0] - p0[0]) / 3,
                  p0[1] + 2 * (point[1] - p0[1]) / 3)
            beziers.append((p0, c1, c2, point))
            current, controls, closed = point, [], closes
        elif command == 0xC0:  # cubic control point
            controls.append(point)
        elif command == 0x80 and current is not None:  # cubic endpoint
            if len(controls) >= 2:
                beziers.append((current, controls[-2], controls[-1], point))
            else:
                p0 = current
                beziers.append((p0, p0, point, point))
            current, controls, closed = point, [], closes
        if closes and current is not None and start is not None and current != start:
            p0, point = current, start
            c1 = (p0[0] + (point[0] - p0[0]) / 3,
                  p0[1] + (point[1] - p0[1]) / 3)
            c2 = (p0[0] + 2 * (point[0] - p0[0]) / 3,
                  p0[1] + 2 * (point[1] - p0[1]) / 3)
            beziers.append((p0, c1, c2, point))
            current = point
    finish()
    return paths


def _object(node: _Node, layer: str, outlines: dict[int, Outline], page_w: float,
            page_h: float, node_state: list[int],
            inherited=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0)) -> Object:
    for loda in _all(node.children, b"loda"):
        kind, args = _args(loda.payload)
        if kind != 3 or 0x1E not in args:
            continue
        outline_id = _u32(args[0x0A]) if len(args.get(0x0A, b"")) >= 4 else 0
        fill_id = _u32(args[0x14]) if len(args.get(0x14, b"")) >= 4 else 0
        color, width, hairline = outlines.get(outline_id, ((0, 0, 0), 0.0, False))
        # A fill-only Corel path still needs a visible boundary curve in CAD.
        if outline_id not in outlines and fill_id:
            color = (0, 0, 0)
        if not math.isfinite(width) or width < 0:
            raise CDRReadError("invalid CDR outline width")
        paths = _path_data(args[0x1E], page_w, page_h,
                           _compose(inherited, _transform(node)), node_state)
        if paths:
            return Object(paths, layer, color, width, hairline)
    raise CDRReadError("the CDR contains an unsupported non-curve object")


def _mcfg_dimensions(nodes: list[_Node]) -> tuple[float, float]:
    record = next(_all(nodes, b"mcfg"), None)
    if record is None or len(record.payload) < 20:
        raise CDRReadError("CDR RIFF has no measurable mcfg page dimensions")
    width = _i32(record.payload, 12) / UNITS_PER_MM
    height = _i32(record.payload, 16) / UNITS_PER_MM
    if not (math.isfinite(width) and math.isfinite(height)
            and 0 < width <= 10_000 and 0 < height <= 10_000):
        raise CDRReadError("CDR mcfg page dimensions are invalid")
    return width, height


def _collect_objects(nodes, layer, outlines, page_w, page_h, inherited,
                     drawing, node_state, totals, depth=0):
    if depth > MAX_DEPTH:
        raise CDRReadError("CDR group nesting is too deep")
    for node in nodes:
        if node.tag != b"LIST":
            continue
        if node.kind == b"obj ":
            if len(drawing.objects) >= MAX_OBJECTS:
                raise CDRReadError("CDR has too many objects")
            totals[0] += len(layer.encode("utf-8"))
            if totals[0] > MAX_LAYER_BYTES:
                raise CDRReadError("CDR reconstructed layer data exceeds the limit")
            obj = _object(node, layer, outlines, page_w, page_h,
                          node_state, inherited)
            totals[1] += len(obj.paths)
            if totals[1] > MAX_PATHS:
                raise CDRReadError("CDR reconstructed data exceeds the limit")
            drawing.objects.append(obj)
        elif node.kind == b"grp ":
            _collect_objects(
                node.children, layer, outlines, page_w, page_h,
                _compose(inherited, _transform(node)), drawing, node_state,
                totals, depth + 1)


def _read_cdr_python(riff: bytes, metadata_bytes: bytes) -> Drawing:
    """Complete Python decoder retained as the compatibility fallback."""
    try:
        metadata = metadata_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CDRReadError("CDR metadata is not UTF-8") from exc
    version = _u16(riff, 20) if riff[12:16] == b"vrsn" else 0
    match_w = re.search(r"<PageWidth>\s*(\d+)\s*</PageWidth>", metadata)
    match_h = re.search(r"<PageHeight>\s*(\d+)\s*</PageHeight>", metadata)
    page_w = int(match_w.group(1)) / UNITS_PER_MM if match_w else None
    page_h = int(match_h.group(1)) / UNITS_PER_MM if match_h else None
    if ((page_w is not None and page_w <= 0)
            or (page_h is not None and page_h <= 0)):
        raise CDRReadError("CDR page dimensions must be positive")

    record_state = [0]
    expanded_state = [0]
    outer = _records(riff, 12, len(riff), state=record_state)
    nodes = []
    for node in outer:
        if node.tag == b"LIST" and node.kind == b"cmpr":
            expanded = _decompress(node, record_state, expanded_state)
            if expanded:
                nodes.extend(expanded)
        elif node.tag != b"vrsn":
            nodes.append(node)
    if page_w is None or page_h is None:
        page_w, page_h = _mcfg_dimensions(nodes)
    outline_styles = _outlines(nodes)
    drawing = Drawing(page_w, page_h, version=version)
    pages = [node for node in nodes if node.tag == b"LIST" and node.kind == b"page"]
    # Corel packages commonly put a master page before the drawing page. Choose
    # the one that actually owns curve objects rather than counting masters.
    drawing_pages = [page for page in pages
                     if any(node.kind == b"obj " for node in _all(page.children, b"LIST"))]
    if len(drawing_pages) != 1:
        raise CDRReadError(
            f"native CDR reader needs one drawing page, found {len(drawing_pages)}")
    layers = [node for node in _all(drawing_pages[0].children, b"LIST")
              if node.kind == b"layr"]
    node_state = [0]
    totals = [0, 0]  # layer-name bytes, paths
    for layer_index, layer_node in enumerate(layers, start=1):
        name = _layer_name(layer_node, f"Layer {layer_index}")
        totals[0] += len(name.encode("utf-8"))
        if totals[0] > MAX_LAYER_BYTES:
            raise CDRReadError("CDR reconstructed layer data exceeds the limit")
        _collect_objects(
            layer_node.children, name, outline_styles, page_w, page_h,
            (1.0, 0.0, 0.0, 0.0, 1.0, 0.0), drawing, node_state, totals)
    if not drawing.objects:
        raise CDRReadError("the CDR contains no supported curve objects")
    return drawing


def _drawing_from_native(value: dict[str, Any]) -> Drawing:
    objects: list[Object] = []
    for raw_object in cast(list[dict[str, Any]], value["objects"]):
        paths: list[Path] = []
        for raw_path in cast(list[dict[str, Any]], raw_object["paths"]):
            beziers: list[Bezier] = []
            for raw_bezier in cast(list[list[list[float]]], raw_path["beziers"]):
                points = [(float(point[0]), float(point[1])) for point in raw_bezier]
                beziers.append((points[0], points[1], points[2], points[3]))
            paths.append(Path(beziers, bool(raw_path["closed"])))
        raw_rgb = cast(list[int], raw_object["rgb"])
        objects.append(Object(
            paths=paths,
            layer=str(raw_object["layer"]),
            rgb=(raw_rgb[0], raw_rgb[1], raw_rgb[2]),
            width_mm=float(raw_object["width_mm"]),
            hairline=bool(raw_object["hairline"]),
        ))
    return Drawing(
        width_mm=float(value["width_mm"]),
        height_mm=float(value["height_mm"]),
        objects=objects,
        version=int(value["version"]),
    )


def read_cdr(path: str) -> Drawing:
    """Read v15 curves exactly, using Rust first and a complete Python fallback."""
    metadata_info = None
    try:
        with zipfile.ZipFile(path) as archive:
            riff_info = archive.getinfo("content/riffData.cdr")
            try:
                metadata_info = archive.getinfo("metadata/metadata.xml")
            except KeyError:
                pass
            if (riff_info.file_size > MAX_RIFF_BYTES
                    or metadata_info is not None
                    and metadata_info.file_size > MAX_METADATA_BYTES):
                raise CDRReadError("CDR package member exceeds the size limit")
            riff = archive.read(riff_info)
            metadata = archive.read(metadata_info) if metadata_info is not None else b""
            if len(riff) > MAX_RIFF_BYTES or len(metadata) > MAX_METADATA_BYTES:
                raise CDRReadError("CDR package member exceeds the size limit")
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise CDRReadError(f"not a supported packaged CDR file: {exc}") from exc
    if (len(riff) < 22 or riff[:4] != b"RIFF" or riff[8:12] != b"CDRF"
            or _u32(riff, 4) != len(riff) - 8):
        raise CDRReadError("not a valid Corel CDRF RIFF stream")
    version = _u16(riff, 20) if riff[12:16] == b"vrsn" else 0
    if version != 1500:
        raise CDRReadError(f"native CDR reader currently supports version 15, not {version}")

    try:
        from . import native_cdr
        native = native_cdr.read_v15(riff, metadata)
    except Exception:  # noqa: BLE001 -- optional native acceleration must fail closed
        native = None
    if native is not None:
        return _drawing_from_native(native)
    return _read_cdr_python(riff, metadata)
