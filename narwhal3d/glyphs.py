"""Small vector glyphs for panel chrome, as flat triangle lists. Pure (no bpy).

The Layers panel needs an eye, a padlock, a plus, a "more" ellipsis, a tick and a
magnifier. Blender's UI font has none of them reliably and the bundled icon set is
command artwork (blue, 24 px), so these are drawn as geometry. Each function returns
``[(x, y), ...]`` in groups of three -- one ``TRIS`` batch -- so the draw site binds a
colour once and the shapes stay crisp at any interface scale.

Python rather than Rust: a glyph is a few dozen vertices built once per panel repaint
and handed straight to Blender's `gpu` module, so the FFI round trip would cost more
than the arithmetic.
"""
from __future__ import annotations

import math


def disc(cx, cy, r, segments=16):
    """A filled circle."""
    out = []
    for k in range(segments):
        a0 = 2.0 * math.pi * k / segments
        a1 = 2.0 * math.pi * (k + 1) / segments
        out += [(cx, cy), (cx + r * math.cos(a0), cy + r * math.sin(a0)),
                (cx + r * math.cos(a1), cy + r * math.sin(a1))]
    return out


def segment(x0, y0, x1, y1, width):
    """A straight stroke `width` thick, as one quad (two triangles)."""
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length <= 0.0:
        return []
    nx, ny = -dy / length * width / 2.0, dx / length * width / 2.0
    a, b = (x0 + nx, y0 + ny), (x1 + nx, y1 + ny)
    c, d = (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)
    return [a, b, c, a, c, d]


def polyline(points, width, closed=False):
    """A stroked polyline with round joints (a disc at every vertex)."""
    pts = list(points)
    if closed and pts:
        pts.append(pts[0])
    out = []
    for p, q in zip(pts, pts[1:]):
        out += segment(p[0], p[1], q[0], q[1], width)
    for p in pts:
        out += disc(p[0], p[1], width / 2.0, segments=8)
    return out


def arc(cx, cy, r, a0, a1, width, segments=12):
    """A stroked circular arc from angle `a0` to `a1` (radians, counter-clockwise)."""
    pts = [(cx + r * math.cos(a0 + (a1 - a0) * k / segments),
            cy + r * math.sin(a0 + (a1 - a0) * k / segments)) for k in range(segments + 1)]
    return polyline(pts, width)


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y0), (x1, y1), (x0, y1)]


def eye(cx, cy, size, open_=True, width=None):
    """An eye `size` wide: an almond outline and a pupil. Closed = the outline struck
    through, so ON and OFF differ in shape, not only in colour."""
    w = size / 2.0
    h = size * 0.30
    width = max(1.0, size / 11.0) if width is None else width
    n = 10
    upper = [(cx - w + 2 * w * k / n, cy + h * math.sin(math.pi * k / n)) for k in range(n + 1)]
    lower = [(cx + w - 2 * w * k / n, cy - h * math.sin(math.pi * k / n)) for k in range(n + 1)]
    out = polyline(upper + lower[1:], width)
    if open_:
        out += disc(cx, cy, h * 0.62)
    else:
        out += segment(cx - w * 0.85, cy - h * 1.35, cx + w * 0.85, cy + h * 1.35, width)
    return out


def lock(cx, cy, size, locked=True, width=None):
    """A padlock `size` tall: a solid body and a shackle -- closed when locked, swung
    open (one leg free) when not."""
    width = max(1.0, size / 9.0) if width is None else width
    bw, bh = size * 0.72, size * 0.48
    bx0, by0 = cx - bw / 2.0, cy - size / 2.0
    out = rect(bx0, by0, bx0 + bw, by0 + bh)
    r = bw * 0.30
    top = by0 + bh
    if locked:
        sx = cx
        out += segment(sx - r, top, sx - r, top + size * 0.18, width)
        out += segment(sx + r, top, sx + r, top + size * 0.18, width)
        out += arc(sx, top + size * 0.18, r, 0.0, math.pi, width)
    else:
        sx = cx + r * 0.9
        out += segment(sx + r, top, sx + r, top + size * 0.18, width)
        out += arc(sx, top + size * 0.18, r, 0.0, math.pi, width)
        out += segment(sx - r, top + size * 0.18, sx - r, top + size * 0.10, width)
    return out


def plus(cx, cy, size, width=None):
    width = max(1.0, size / 7.0) if width is None else width
    h = size / 2.0
    return segment(cx - h, cy, cx + h, cy, width) + segment(cx, cy - h, cx, cy + h, width)


def ellipsis(cx, cy, size):
    """Three dots across `size` -- the "more" button."""
    r = size / 9.0
    step = size / 3.0
    return disc(cx - step, cy, r) + disc(cx, cy, r) + disc(cx + step, cy, r)


def tick(cx, cy, size, width=None):
    width = max(1.0, size / 7.0) if width is None else width
    h = size / 2.0
    return polyline([(cx - h, cy), (cx - h * 0.25, cy - h * 0.7), (cx + h, cy + h * 0.7)],
                    width)


def magnifier(cx, cy, size, width=None):
    """A search magnifier: a ring and a handle to the lower right."""
    width = max(1.0, size / 8.0) if width is None else width
    r = size * 0.32
    ox, oy = cx - size * 0.1, cy + size * 0.1
    out = arc(ox, oy, r, 0.0, 2.0 * math.pi, width, segments=20)
    d = r / math.sqrt(2.0)
    out += segment(ox + d, oy - d, cx + size / 2.0, cy - size / 2.0, width * 1.2)
    return out


def bounds(tris):
    """``(x0, y0, x1, y1)`` of a triangle list, or None when empty."""
    if not tris:
        return None
    xs = [p[0] for p in tris]
    ys = [p[1] for p in tris]
    return (min(xs), min(ys), max(xs), max(ys))
