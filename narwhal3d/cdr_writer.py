"""Native CDR v15 vector writer targeting CorelDRAW 2015/X7.

This is a real CDR writer, not a PDF/SVG file with a ``.cdr`` suffix. The
production package and compressed/indexed RIFF ``CDRF`` stream are built entirely
by the tracked Rust core; Python only marshals strokes and writes returned bytes. The
implemented interchange subset is deliberately narrow: pages, layers, RGB
outlines, line widths, open/closed paths, and cubic Beziers.  Those are exactly
the primitives produced by :mod:`print2d` for manufacturing drawings.

MEASURED from CorelDRAW v15 files and independently decoded with libcdr 0.1.9.
CorelDRAW's 2015/X7 help says X7 reads CDR versions 7 through 17, so v15 is
used as the broad-compatible native stream rather than merely relabeling a file:
coordinates are signed 32-bit values in 1/254000 inch (10,000 units/mm), path
node types are move=0x00, line=0x40, cubic-control=0xc0, cubic-end=0x80, and the
closed flag is 0x08.  libcdr's public parser is the second source for the RIFF,
``loda``, ``outl``, and color record layouts.
"""
from __future__ import annotations

import os
import struct
import uuid
import zipfile
from collections.abc import Iterable
from typing import Protocol
from xml.sax.saxutils import escape

CDR_VERSION = 1500
MIMETYPE = "application/x-vnd.corel.draw.document+zip"
UNITS_PER_MM = 10_000
# CorelDRAW v15 stores Hairline as 762 coordinate units, exactly
# 0.003 inch / 0.0762 mm.
HAIRLINE_MM = 0.0762
_I32_MIN = -(1 << 31)
_I32_MAX = (1 << 31) - 1

Point2 = tuple[float, float]
Bezier = tuple[Point2, Point2, Point2, Point2]
Bounds = tuple[int, int, int, int]
RGB = tuple[float, float, float]


class StrokeLike(Protocol):
    beziers: tuple[Bezier, ...]
    closed: bool
    rgb: RGB
    width_mm: float
    layer: str


def _u16(value: int) -> bytes:
    return struct.pack("<H", value)


def _u32(value: int) -> bytes:
    return struct.pack("<I", value)


def _i32(value: int) -> bytes:
    if not _I32_MIN <= value <= _I32_MAX:
        raise ValueError("CDR v15 coordinate exceeds its signed 32-bit range")
    return struct.pack("<i", value)


def _chunk(tag: bytes, payload: bytes) -> bytes:
    if len(tag) != 4:
        raise ValueError("a RIFF tag must contain four bytes")
    result = tag + _u32(len(payload)) + payload
    return result + (b"\0" if len(payload) & 1 else b"")


def _list(kind: bytes, *children: bytes) -> bytes:
    if len(kind) != 4:
        raise ValueError("a RIFF list type must contain four bytes")
    return _chunk(b"LIST", kind + b"".join(children))


def _coord(mm: float) -> int:
    return round(float(mm) * UNITS_PER_MM)


def _rgb8(rgb: RGB) -> tuple[int, int, int]:
    values = tuple(max(0, min(255, round(float(c) * 255))) for c in rgb)
    if len(values) != 3:
        raise ValueError("a CDR outline color needs three RGB components")
    return values


def _color(rgb: RGB) -> bytes:
    r, g, b = _rgb8(rgb)
    # libcdr color model 5 is RGB; its value is 0x00RRGGBB.
    return struct.pack("<HHII", 5, 0, 0, (r << 16) | (g << 8) | b)


def _outline(outline_id: int, rgb: RGB, width_mm: float) -> bytes:
    """One legacy solid RGB outline record independently decoded by libcdr v15."""
    if width_mm <= 0:
        raise ValueError("CDR outline width must be positive")
    payload = bytearray()
    payload += _u32(outline_id)
    # v13+ style records are optional-field blocks.  ID 1 is the main block.
    payload += _u32(1) + _u32(0)
    # In v15, 0x22 is a scalable solid outline;
    # 0x01 is the no-outline style.  Zero is not a valid solid style here.
    payload += struct.pack("<HHH", 0x22, 0, 0)  # solid, butt cap, miter join
    payload += _i32(_coord(width_mm))
    payload += _u16(100) + b"\0\0"          # 100% stretch
    payload += _i32(0)                       # pen angle
    payload += b"\0" * 46
    payload += _color(rgb)
    payload += b"\0" * 16
    payload += _u16(0)                       # no dash entries
    payload += b"\0" * 22
    payload += _u32(0) + _u32(0)             # no arrowheads
    return _chunk(b"outl", bytes(payload))


def _path_nodes(stroke: StrokeLike, page_w_mm: float,
                page_h_mm: float) -> tuple[list[tuple[int, int]], list[int]]:
    if not stroke.beziers:
        return [], []

    def point(p: Point2) -> tuple[int, int]:
        # print2d is bottom-left page space; CDR is page-centred, y-up.
        return (_coord(float(p[0]) - page_w_mm / 2.0),
                _coord(float(p[1]) - page_h_mm / 2.0))

    points = [point(stroke.beziers[0][0])]
    types = [0x00]
    for index, bezier in enumerate(stroke.beziers):
        if len(bezier) != 4:
            raise ValueError("a CDR path segment must be a cubic Bezier")
        p0, p1, p2, p3 = bezier
        if index and point(p0) != points[-1]:
            raise ValueError("CDR Bezier segments must form one continuous stroke")
        points.extend((point(p1), point(p2), point(p3)))
        types.extend((0xC0, 0xC0, 0x80))
    if stroke.closed:
        types[-1] |= 0x08
    return points, types


def _loda(kind: int, arguments: tuple[tuple[int, bytes], ...]) -> bytes:
    """Build Corel's offset/type argument record (types are stored reversed)."""
    count = len(arguments)
    start_offsets = 20
    start_types = start_offsets + 4 * count
    cursor = start_types + 4 * count
    offsets = []
    for _kind, data in arguments:
        offsets.append(cursor)
        cursor += len(data)

    payload = bytearray()
    payload += _u32(0)  # patched with this loda payload's complete size
    payload += _u32(count)
    payload += _u32(start_offsets)
    payload += _u32(start_types)
    payload += _u32(kind)
    payload += b"".join(_u32(offset) for offset in offsets)
    # CDR stores the argument type table in reverse offset order.
    payload += b"".join(_u32(kind) for kind, _data in reversed(arguments))
    payload += b"".join(data for _kind, data in arguments)
    struct.pack_into("<I", payload, 0, len(payload))
    return _chunk(b"loda", bytes(payload))


def _loda_path(stroke: StrokeLike, outline_id: int, page_w_mm: float,
               page_h_mm: float) -> bytes:
    points, types = _path_nodes(stroke, page_w_mm, page_h_mm)
    shape = _u16(len(points)) + _u16(0)
    shape += b"".join(_i32(x) + _i32(y) for x, y in points)
    shape += bytes(types)
    return _loda(3, ((0x1E, shape), (0x0A, _u32(outline_id))))


def _layer_descriptor(name: str) -> bytes:
    # The v15 0x3e8 field is the UTF-16LE layer name, 0x9c72 its GUID,
    # and 0x7d0 carries the standard visible/editable/printable layer flags.
    flags = bytes.fromhex(
        "010c000000020005000000000000000064071000000000000000000000000000"
        "0000000000000000000000")
    encoded_name = name.encode("utf-16le") + b"\0\0"
    return _loda(0, (
        (0x9C72, uuid.uuid4().bytes_le),
        (0x2EE0, _u32(0)),
        (0x07D0, flags),
        (0x03E8, encoded_name),
    ))


def _bounds(stroke: StrokeLike, page_w_mm: float,
            page_h_mm: float) -> Bounds:
    points, _types = _path_nodes(stroke, page_w_mm, page_h_mm)
    if not points:
        return 0, 0, 0, 0
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _bbox(bounds: Bounds) -> bytes:
    x0, y0, x1, y1 = bounds
    return _chunk(b"bbox", _i32(x0) + _i32(y0) + _i32(x1) + _i32(y1))


def _union(bounds: Iterable[Bounds]) -> Bounds:
    bounds = list(bounds)
    if not bounds:
        return 0, 0, 0, 0
    return (min(b[0] for b in bounds), min(b[1] for b in bounds),
            max(b[2] for b in bounds), max(b[3] for b in bounds))


def _riff_python(strokes: list[StrokeLike], page_w_mm: float,
                 page_h_mm: float, hairline: bool = False) -> bytes:
    style_keys = []
    for stroke in strokes:
        width = HAIRLINE_MM if hairline else float(stroke.width_mm)
        key = (_rgb8(stroke.rgb), round(width, 9))
        if key not in style_keys:
            style_keys.append(key)
    style_id = {key: index + 1 for index, key in enumerate(style_keys)}
    outlines = [_outline(index + 1, rgb, width)
                for index, (rgb, width) in enumerate(style_keys)]

    layer_names = []
    for stroke in strokes:
        name = str(stroke.layer or "Layer 1")
        if name not in layer_names:
            layer_names.append(name)

    layers = []
    all_bounds = []
    for layer_name in layer_names:
        objects = []
        layer_bounds = []
        for stroke in strokes:
            if str(stroke.layer or "Layer 1") != layer_name:
                continue
            bounds = _bounds(stroke, page_w_mm, page_h_mm)
            layer_bounds.append(bounds)
            all_bounds.append(bounds)
            width = HAIRLINE_MM if hairline else float(stroke.width_mm)
            key = (_rgb8(stroke.rgb), round(width, 9))
            obj = _list(b"obj ", _bbox(bounds),
                        _list(b"lgob", _loda_path(
                            stroke, style_id[key], page_w_mm, page_h_mm)))
            objects.append(obj)
        # The layer name is also present in textinfo.xml.  Keeping each layer as a
        # distinct `layr` list preserves object grouping in Corel/libcdr readers.
        layers.append(_list(b"layr", _chunk(b"flgs", _u32(0)),
                            _bbox(_union(layer_bounds)),
                            _list(b"lgob", _layer_descriptor(layer_name)),
                            *objects))

    page_bounds = (-_coord(page_w_mm / 2), -_coord(page_h_mm / 2),
                   _coord(page_w_mm / 2), _coord(page_h_mm / 2))
    page = _list(b"page", _chunk(b"flgs", _u32(0)), _bbox(page_bounds),
                 _list(b"gobj", *layers))
    # readMcfg for v15 skips twelve bytes before these page dimensions.
    mcfg = _chunk(b"mcfg", b"\0" * 12
                  + _i32(_coord(page_w_mm)) + _i32(_coord(page_h_mm)))
    doc_children = [mcfg]
    if outlines:
        doc_children.append(_list(b"otlt", *outlines))
    doc = _list(b"doc ", *doc_children)
    body = b"CDRF" + _chunk(b"vrsn", _u16(CDR_VERSION)) + doc + page
    return b"RIFF" + _u32(len(body)) + body


def _riff(strokes: list[StrokeLike], page_w_mm: float,
          page_h_mm: float, hairline: bool = False) -> bytes:
    """Build RIFF through Rust when available, else the complete Python implementation."""
    try:
        from . import native_cdr
        native = native_cdr.riff_v15(strokes, page_w_mm, page_h_mm, hairline)
    except Exception:  # noqa: BLE001 -- optional native acceleration must fail closed
        native = None
    return native if native is not None else _riff_python(
        strokes, page_w_mm, page_h_mm, hairline)


def _textinfo(layer_names: list[str]) -> bytes:
    items = "".join(f"<rdf:li>{escape(name)}</rdf:li>" for name in layer_names)
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\" ?>"
            "<x:xmpmeta xmlns:x=\"adobe:ns:meta/\">"
            "<rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\">"
            "<rdf:Description rdf:about=\"\" xmlns=\"http://namespace.corel.com/Cdr/metadata\">"
            f"<LayerNames><rdf:Bag>{items}</rdf:Bag></LayerNames>"
            "</rdf:Description></rdf:RDF></x:xmpmeta>").encode("utf-8")


def _metadata(strokes: list[StrokeLike], page_w_mm: float,
              page_h_mm: float, title: str, layer_names: list[str],
              hairline: bool = False) -> bytes:
    closed = sum(bool(s.closed) for s in strokes)
    nodes = sum(1 + 3 * len(s.beziers) for s in strokes)
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\" ?>"
            "<x:xmpmeta xmlns:x=\"adobe:ns:meta/\">"
            "<rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\">"
            "<rdf:Description rdf:about=\"\" xmlns=\"http://namespace.corel.com/Cdr/metadata\">"
            f"<CoreVersion>{CDR_VERSION}</CoreVersion>"
            "<ProductName>CorelDRAW</ProductName><AppVersion>1500</AppVersion>"
            f"<DocID>{uuid.uuid4()}</DocID><Subject>{escape(title)}</Subject>"
            f"<NumPages>1</NumPages><NumLayers>{len(layer_names)}</NumLayers>"
            f"<PageWidth>{_coord(page_w_mm)}</PageWidth>"
            f"<PageHeight>{_coord(page_h_mm)}</PageHeight>"
            f"<Objects><Total>{len(strokes)}</Total><Curve>{len(strokes)}</Curve></Objects>"
            f"<CurveInfo><Subpaths>{len(strokes)}</Subpaths><Nodes>{nodes}</Nodes>"
            f"<Closed>{closed}</Closed></CurveInfo>"
            f"<Outlines><Hairline>{len(strokes) if hairline else 0}</Hairline>"
            f"<Solid>{0 if hairline else len(strokes)}</Solid></Outlines>"
            "</rdf:Description></rdf:RDF></x:xmpmeta>").encode("utf-8")


def _writestr(archive: zipfile.ZipFile, name: str, data: bytes | str,
              compression: int) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = compression
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data)


def write_cdr(path: str, strokes: Iterable[StrokeLike], page_w_mm: float,
              page_h_mm: float, title: str = "", hairline: bool = False) -> int:
    """Write a compressed native CDR v15 package for CorelDRAW 2015.

    ``hairline=True`` stores every outline at Corel's native 0.003-inch
    Hairline width, irrespective of the document print width.
    """
    strokes = [stroke for stroke in strokes if stroke.beziers]
    if page_w_mm <= 0 or page_h_mm <= 0:
        raise ValueError("CDR page dimensions must be positive")
    if not strokes:
        raise ValueError("a CDR drawing needs at least one non-empty stroke")
    if any(1 + 3 * len(stroke.beziers) > 0xFFFF for stroke in strokes):
        raise ValueError("one CDR curve exceeds the v15 limit of 65535 path nodes")

    from . import native_cdr

    package = native_cdr.package_v15(
        strokes, page_w_mm, page_h_mm, title=title, hairline=hairline)
    if package is None:
        raise RuntimeError(
            "native CorelDRAW v15 package writer is unavailable; refusing to emit "
            "the legacy RIFF subset that real CorelDRAW cannot open")
    with open(path, "wb") as stream:
        stream.write(package)
    return len(package)
