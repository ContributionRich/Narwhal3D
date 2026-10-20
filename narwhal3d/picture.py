"""`Picture` -- an image on an exact planar surface. Pure: no bpy, no rhino3dm.

MEASURED (commands/picture.htm): "draws a rectangular planar surface with the specified
image assigned to its material ... The defining rectangle retains the same aspect
(length to width) ratio as the image." / "1to1 ... The plane size will be the print size
of the bitmap image calculated from the resolution (dots-per-inch) and the pixel
dimension." / "A Picture material will be created ... The image file name becomes the
material name." / "When a Picture plane is split and shrunk, a new material will be added
for each new Picture plane. The goal is to make the texture display consistent."

What lives here:

* the IMAGE HEADER reader -- PNG (IHDR size, pHYs density) and JPEG (SOFn size, JFIF APP0
  density). Other formats are refused BY NAME; nothing is decoded, only headers read.
* the FRAME -- where the image sits in space: ``origin`` (the image's lower-left corner),
  ``u`` (the vector spanning its full WIDTH) and ``v`` (its full HEIGHT). It is stored on
  the geometry as a JSON user string, so the texture stays registered to the ORIGINAL
  rectangle after any trim or split (a piece is not re-normalised to 0..1), and it moves
  with the geometry (`bridge.transform_geometry` maps origin, u and v).
* the aspect-locked SIZING of the rectangle from the picked points.
"""
from __future__ import annotations

import json
import os
import struct

#: the user-string key the frame is stored under (geometry user strings survive save,
#: reload and Duplicate)
KEY = "Narwhal3D:Picture"

#: The density assumed when an image declares none. The help gives no default;
#: 72 dpi is the conventional screen density. `ImageInfo.dpi_declared` says whether the
#: file stated one, and the command reports it when it had to assume.
DEFAULT_DPI = 72.0

SUPPORTED = ("PNG", "JPEG")

#: the file-dialog filter (`filedialog.ask_open_path(..., filters)` shape)
FILTERS = (("Images (PNG, JPEG)", "*.png *.jpg *.jpeg"),)


class ImageInfo:
    __slots__ = ("format", "width", "height", "dpi_x", "dpi_y", "dpi_declared")

    def __init__(self, fmt, width, height, dpi_x=None, dpi_y=None):
        self.format = fmt
        self.width = int(width)
        self.height = int(height)
        self.dpi_declared = bool(dpi_x and dpi_y)
        self.dpi_x = float(dpi_x) if dpi_x else DEFAULT_DPI
        self.dpi_y = float(dpi_y) if dpi_y else DEFAULT_DPI

    @property
    def aspect(self):
        """width / height."""
        return self.width / self.height

    def print_size_inches(self):
        """``(width, height)`` in inches -- `1to1`'s pixel dimension / dpi."""
        return self.width / self.dpi_x, self.height / self.dpi_y

    def __repr__(self):  # pragma: no cover - debugging aid
        return (f"ImageInfo({self.format} {self.width}x{self.height} "
                f"{self.dpi_x:g}x{self.dpi_y:g}dpi)")


def _sniff(head):
    """The format named by the magic bytes, or None."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if head.startswith(b"\xff\xd8"):
        return "JPEG"
    for magic, name in ((b"GIF8", "GIF"), (b"BM", "BMP"), (b"II*\x00", "TIFF"),
                        (b"MM\x00*", "TIFF"), (b"RIFF", "WEBP")):
        if head.startswith(magic):
            return name
    return None


def read_image_info(path):
    """`ImageInfo` for the image at `path`. Raises ValueError, naming the problem, for an
    unreadable file or a format other than PNG / JPEG."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(1 << 20)
    except OSError as e:
        raise ValueError(f"cannot read {path!r}: {e.strerror or e}") from e
    fmt = _sniff(data[:16])
    if fmt == "PNG":
        return _png(data, path)
    if fmt == "JPEG":
        return _jpeg(data, path)
    name = fmt or (os.path.splitext(path)[1].lstrip(".").upper() or "this")
    raise ValueError(f"{name} images are not supported yet -- Picture reads "
                     f"{' and '.join(SUPPORTED)}")


def _png(data, path):
    if len(data) < 33 or data[12:16] != b"IHDR":
        raise ValueError(f"{path!r} is not a valid PNG (no IHDR)")
    width, height = struct.unpack(">II", data[16:24])
    dpi = (None, None)
    pos = 8
    while pos + 8 <= len(data):
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        if ctype == b"pHYs" and length >= 9:
            ppx, ppy, unit = struct.unpack(">IIB", data[pos + 8:pos + 17])
            if unit == 1:                         # pixels per METRE
                dpi = (ppx * 0.0254, ppy * 0.0254)
            break
        if ctype in (b"IDAT", b"IEND"):
            break
        pos += 12 + length
    return ImageInfo("PNG", width, height, *dpi)


def _jpeg(data, path):
    pos, dpi, size = 2, (None, None), None
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        length = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        seg = data[pos + 4:pos + 2 + length]
        if marker == 0xE0 and seg[:5] == b"JFIF\x00" and len(seg) >= 12:
            units, dx, dy = seg[7], *struct.unpack(">HH", seg[8:12])
            if units == 1:
                dpi = (dx, dy)
            elif units == 2:                      # dots per CENTIMETRE
                dpi = (dx * 2.54, dy * 2.54)
        elif 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", seg[1:5])
            size = (width, height)
            break
        elif marker == 0xDA:                      # start of scan: no SOF before it
            break
        pos += 2 + length
    if size is None:
        raise ValueError(f"{path!r} is not a valid JPEG (no frame header)")
    return ImageInfo("JPEG", size[0], size[1], *dpi)


# ---------------------------------------------------------------------------
# the frame
# ---------------------------------------------------------------------------

def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def make_frame(image, origin, u, v):
    """The frame dict: the image path and where its rectangle lies."""
    return {"image": str(image), "origin": [float(c) for c in origin],
            "u": [float(c) for c in u], "v": [float(c) for c in v]}


def frame_to_json(frame):
    return json.dumps(frame, sort_keys=True)


def frame_from_json(text):
    """The frame dict, or None for an absent / unreadable string."""
    if not text:
        return None
    try:
        f = json.loads(text)
        if all(k in f for k in ("image", "origin", "u", "v")):
            return f
    except (TypeError, ValueError):
        pass
    return None


def corners(frame):
    """The four corners, counterclockwise from the origin (lower-left)."""
    o, u, v = (tuple(frame[k]) for k in ("origin", "u", "v"))
    return [o, _add(o, u), _add(_add(o, u), v), _add(o, v)]


def uv_of(frame, p):
    """The texture coordinate of world point `p`: (0,0) at the origin, (1,1) at the far
    corner, solved against u and v (exact even if a shear made them non-orthogonal)."""
    o, u, v = (tuple(frame[k]) for k in ("origin", "u", "v"))
    d = _sub(p, o)
    uu, vv, uv = _dot(u, u), _dot(v, v), _dot(u, v)
    det = uu * vv - uv * uv
    if abs(det) <= 1e-300:
        return 0.0, 0.0
    du, dv = _dot(d, u), _dot(d, v)
    return (du * vv - dv * uv) / det, (dv * uu - du * uv) / det


def transform_frame(frame, rows):
    """`frame` moved by the 4x4 row-major matrix `rows`: the origin as a point, u and v
    as vectors (the linear part only), so a scale or rotation stays registered."""
    def point(p):
        return tuple(rows[r][0] * p[0] + rows[r][1] * p[1] + rows[r][2] * p[2] + rows[r][3]
                     for r in range(3))

    def vector(p):
        return tuple(rows[r][0] * p[0] + rows[r][1] * p[1] + rows[r][2] * p[2]
                     for r in range(3))

    out = dict(frame)
    out["origin"] = list(point(frame["origin"]))
    out["u"] = list(vector(frame["u"]))
    out["v"] = list(vector(frame["v"]))
    return out


# ---------------------------------------------------------------------------
# sizing: the rectangle keeps the image's aspect
# ---------------------------------------------------------------------------

def fit_size(aspect, du, dv):
    """``(width, height, sign_u, sign_v)`` of the aspect-locked rectangle that reaches a
    corner picked at plane offsets (du, dv) from the first corner.

    OURS (the help says only that the aspect is kept): the rectangle is scaled until it
    reaches the pick in the direction that needs MORE -- so the picked corner always lies
    on the rectangle's boundary, and a pick straight along x (dv = 0) gives width |du|.
    The signs say which quadrant the rectangle grows into (+ when an offset is 0).
    """
    width = max(abs(du), abs(dv) * aspect)
    return width, width / aspect, (-1.0 if du < 0 else 1.0), (-1.0 if dv < 0 else 1.0)


def print_size(info, model_unit, convert):
    """`1to1`: ``(width, height)`` in `model_unit`, via `convert(value, from, to)`."""
    wi, hi = info.print_size_inches()
    return convert(wi, "Inches", model_unit), convert(hi, "Inches", model_unit)


def material_name(image_path):
    """MEASURED: "The image file name becomes the material name."."""
    return os.path.basename(str(image_path))
