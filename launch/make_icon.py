"""Generate application branding from the supplied artwork/Logo sources.

Pillow is a build-time dependency only; its native resampler creates PNG/ICO sizes.
The supplied PNG/SVG stay untouched. Do not replace them with procedural artwork.

Run: /usr/bin/python3 launch/make_icon.py
Check without writing: /usr/bin/python3 launch/make_icon.py --check
"""
from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SIZE = 256
ICON_FILENAME = "narwhal3d.png"
SOURCE_PNG = Path("artwork/Logo/narwhal.png")
SOURCE_SVG = Path("artwork/Logo/narwhal_vector.svg")
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


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
