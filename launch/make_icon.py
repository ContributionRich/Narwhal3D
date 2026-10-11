"""Generate the Narwhal 3D app icon using only the Python standard library.

Writes ``launch/narwhal3d.png``: a 256x256 ocean-blue rounded square with an
original, project-owned white narwhal and gold tusk. The PNG is committed so the
desktop entry works without a build step.

Run: python3 launch/make_icon.py
"""
import os
import struct
import zlib

SIZE = 256
ICON_FILENAME = "narwhal3d.png"
OCEAN = (24, 93, 133, 255)
WHALE = (235, 246, 248, 255)
TUSK = (244, 194, 79, 255)
INK = (20, 49, 66, 255)


def _png(path, pixels):
    """Write rows of RGBA byte quads as a PNG."""
    def chunk(tag, data):
        content = tag + data
        return (struct.pack(">I", len(data)) + content
                + struct.pack(">I", zlib.crc32(content) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)
    raw = b"".join(b"\x00" + row for row in pixels)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    with open(path, "wb") as output:
        output.write(png)


def main():
    pixels = [[(0, 0, 0, 0)] * SIZE for _ in range(SIZE)]

    def put(x, y, color):
        if 0 <= x < SIZE and 0 <= y < SIZE:
            pixels[y][x] = color

    def rounded_square(cx, cy, half, radius, color):
        x0, y0, x1, y1 = cx - half, cy - half, cx + half, cy + half
        for y in range(y0, y1):
            for x in range(x0, x1):
                qx = max(x0 + radius - x, 0, x - (x1 - radius))
                qy = max(y0 + radius - y, 0, y - (y1 - radius))
                if qx * qx + qy * qy <= radius * radius:
                    put(x, y, color)

    def ellipse(cx, cy, rx, ry, color):
        for y in range(cy - ry, cy + ry + 1):
            for x in range(cx - rx, cx + rx + 1):
                if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1:
                    put(x, y, color)

    def triangle(a, b, c, color):
        min_x, max_x = max(0, min(a[0], b[0], c[0])), min(SIZE - 1, max(a[0], b[0], c[0]))
        min_y, max_y = max(0, min(a[1], b[1], c[1])), min(SIZE - 1, max(a[1], b[1], c[1]))
        def edge(p, q, r):
            return (r[0] - p[0]) * (q[1] - p[1]) - (r[1] - p[1]) * (q[0] - p[0])
        for y in range(min_y, max_y + 1):
            for x in range(min_x, max_x + 1):
                p = (x, y)
                e1, e2, e3 = edge(a, b, p), edge(b, c, p), edge(c, a, p)
                if not ((e1 < 0 or e2 < 0 or e3 < 0) and (e1 > 0 or e2 > 0 or e3 > 0)):
                    put(x, y, color)

    rounded_square(SIZE // 2, SIZE // 2, 118, 48, OCEAN)
    # Tail behind the body, then a compact whale silhouette facing upper-right.
    triangle((58, 164), (24, 132), (63, 139), WHALE)
    triangle((58, 164), (34, 199), (76, 171), WHALE)
    ellipse(121, 145, 70, 47, WHALE)
    ellipse(166, 119, 39, 39, WHALE)
    triangle((174, 91), (225, 28), (188, 103), TUSK)
    triangle((119, 174), (153, 204), (159, 166), WHALE)
    ellipse(176, 116, 5, 5, INK)
    ellipse(191, 137, 5, 3, OCEAN)

    rows = [bytes(channel for pixel in row for channel in pixel) for row in pixels]
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ICON_FILENAME)
    _png(output_path, rows)
    print(f"wrote {output_path} ({os.path.getsize(output_path)} bytes)")


if __name__ == "__main__":
    main()
