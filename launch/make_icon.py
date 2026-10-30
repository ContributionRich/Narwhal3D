"""Generate application branding from the supplied artwork/Logo sources.

Pillow is a build-time dependency only; its native resampler creates PNG/ICO sizes.
The supplied PNG/SVG stay untouched. Do not replace them with procedural artwork.

Run: /usr/bin/python3 launch/make_icon.py
Check without writing: /usr/bin/python3 launch/make_icon.py --check
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from io import BytesIO
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SIZE = 256
ICON_FILENAME = "narwhal3d.png"
SOURCE_PNG = Path("artwork/Logo/narwhal.png")
SOURCE_SVG = Path("artwork/Logo/narwhal_vector.svg")
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def window_icon_bundle(image, ico: bytes) -> bytes:
    """Freeze native resources once; platform builders need no imaging packages.

    Pillow performs native resampling/encoding in this existing build-time asset
    pipeline. No image conversion or Python pixel iteration runs in the app.
    """
    from PIL import Image

    words = []
    for size in (32, 48):
        words.extend((size, size))
        resized = image.resize((size, size), Image.Resampling.LANCZOS)
        pixels = (resized.get_flattened_data() if hasattr(resized, "get_flattened_data")
                  else resized.getdata())
        words.extend((a << 24) | (r << 16) | (g << 8) | b
                     for r, g, b, a in pixels)
    header = (
        "/* Generated from Narwhal 3D artwork; GPL-3.0-or-later. */\n"
        "#pragma once\nstatic const unsigned long BLENDER_ICONS_WM_X11[] = {\n"
        + "".join("  " + ", ".join(f"0x{word:08x}UL" for word in words[i:i + 8])
                  + ",\n" for i in range(0, len(words), 8))
        + "};\n"
    ).encode("ascii")
    icns = BytesIO()
    image.save(icns, format="ICNS", append_images=[
        image.resize((size, size), Image.Resampling.LANCZOS)
        for size in (32, 64, 128, 256, 512, 1024)
    ])
    icons = {name: {"base64": base64.b64encode(data).decode("ascii"),
                    "sha256": hashlib.sha256(data).hexdigest()}
             for name, data in (("x11", header), ("windows", ico), ("macos", icns.getvalue()))}
    return (json.dumps({"schema": 1, "icons": icons}, sort_keys=True, indent=2) + "\n").encode("ascii")


def assets(root: Path = ROOT) -> dict[Path, bytes]:
    """Build the raster variants and retain the original scalable artwork verbatim."""
    from PIL import Image

    svg = (root / SOURCE_SVG).read_bytes()
    if ET.fromstring(svg).tag != "{http://www.w3.org/2000/svg}svg":
        raise ValueError("canonical vector logo must be an SVG")
    with Image.open(root / SOURCE_PNG) as source:
        if source.width != source.height or source.width < SIZE:
            raise ValueError("canonical raster logo must be square and at least 256px")
        image = source.convert("RGBA")
    icon = image.resize((SIZE, SIZE), Image.Resampling.LANCZOS)
    png = BytesIO()
    icon.save(png, format="PNG", optimize=True)
    ico = BytesIO()
    icon.save(ico, format="ICO", sizes=[(size, size) for size in ICO_SIZES])
    return {
        Path("launch") / ICON_FILENAME: png.getvalue(),
        Path("packaging/windows/narwhal3d.ico"): ico.getvalue(),
        Path("blender_patches/narwhal3d_app_icon.svg"): svg,
        Path("blender_patches/narwhal3d_window_icons.json"): window_icon_bundle(image, ico.getvalue()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if any generated asset is stale")
    args = parser.parse_args()
    stale = []
    for relative, data in assets().items():
        path = ROOT / relative
        if args.check:
            if not path.is_file() or path.read_bytes() != data:
                stale.append(str(relative))
        else:
            path.write_bytes(data)
            print(f"wrote {relative} ({len(data)} bytes)")
    if stale:
        raise SystemExit("Stale branding assets: " + ", ".join(stale))
    if args.check:
        print("Branding assets match artwork/Logo.")


if __name__ == "__main__":
    main()
