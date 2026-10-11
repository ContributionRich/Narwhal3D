"""Generate Narwhal 3D's temporary 24x24 toolbar icons with the standard library.

The artwork is deliberately simple and deterministic: each category has its own
background color and each action gets a unique hash-derived tile pattern. These
icons are project-owned placeholders, not a final visual language.
"""
from __future__ import annotations

import binascii
import hashlib
import importlib.util
import os
import struct
from typing import Any
import zlib

WIDTH = 24
HEIGHT = 24

CATEGORY_COLORS: dict[str, tuple[int, int, int, int]] = {
    "File": (46, 108, 166, 255),
    "Draw": (30, 145, 112, 255),
    "Edit": (185, 93, 44, 255),
    "Transform": (125, 82, 181, 255),
    "Solid": (185, 58, 81, 255),
    "View": (66, 119, 70, 255),
    "Panel": (70, 96, 125, 255),
}


def _toolbar_data() -> Any:
    """Load toolbar data both as a package module and as a directly run script."""
    try:
        from . import toolbar_data
        return toolbar_data
    except ImportError:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "toolbar_data.py")
        spec = importlib.util.spec_from_file_location("narwhal_toolbar_data", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load toolbar data from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


def icon_specs() -> dict[str, str]:
    """Return ``{filename: category}`` for every toolbar icon."""
    specs: dict[str, str] = {}
    data = _toolbar_data()
    for tab in data.TABS:
        category = tab["name"]
        for button in tab["buttons"]:
            specs[button["icon"]] = category
    for filename in data.PANEL_ICON_NAMES:
        specs[filename] = "Panel"
    return specs


def _chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return (struct.pack(">I", len(payload)) + body
            + struct.pack(">I", binascii.crc32(body) & 0xFFFFFFFF))


def icon_bytes(filename: str, category: str) -> bytes:
    """Return one deterministic RGBA PNG as bytes."""
    if category not in CATEGORY_COLORS:
        raise ValueError(f"unknown icon category: {category!r}")
    if not filename or filename != os.path.basename(filename):
        raise ValueError(f"unsafe icon filename: {filename!r}")

    base = CATEGORY_COLORS[category]
    digest = hashlib.sha256(("narwhal3d-icon:" + filename).encode("utf-8")).digest()
    light = (240, 248, 250, 255)
    dark = (22, 31, 38, 255)
    edge = (max(0, base[0] - 35), max(0, base[1] - 35),
            max(0, base[2] - 35), 255)

    pixels = [[base for _x in range(WIDTH)] for _y in range(HEIGHT)]
    for y in range(HEIGHT):
        for x in range(WIDTH):
            if x in (0, WIDTH - 1) or y in (0, HEIGHT - 1):
                pixels[y][x] = edge

    # Thirty-six project-specific tiles form a compact, unique action mark.
    bits = int.from_bytes(digest[:5], "big") | (1 << 35) | 1
    for row in range(6):
        for col in range(6):
            color = light if bits & (1 << (row * 6 + col)) else dark
            x0 = 3 + col * 3
            y0 = 3 + row * 3
            for y in range(y0, y0 + 2):
                for x in range(x0, x0 + 2):
                    pixels[y][x] = color

    raw = b"".join(
        b"\x00" + b"".join(bytes(pixel) for pixel in row)
        for row in pixels
    )
    signature = b"\x89PNG\r\n\x1a\n"
    header = struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 6, 0, 0, 0)
    return signature + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b"")


def write_icons(output_dir: str | None = None) -> list[str]:
    """Write every generated icon and return the paths in filename order."""
    output_dir = output_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
    os.makedirs(output_dir, exist_ok=True)
    paths: list[str] = []
    for filename, category in sorted(icon_specs().items()):
        path = os.path.join(output_dir, filename)
        with open(path, "wb") as stream:
            stream.write(icon_bytes(filename, category))
        paths.append(path)
    return paths


if __name__ == "__main__":
    made = write_icons()
    print(f"generated {len(made)} Narwhal 3D toolbar icons")
