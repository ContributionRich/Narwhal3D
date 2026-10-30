#!/usr/bin/env python3
"""Native icon generation/application fixtures; no runtime build or GUI.

Python orchestrates the existing Pillow/native resource pipeline. These tests
cannot certify an installed window or replace platform acceptance.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
from io import BytesIO, StringIO
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


native = load("native_window_branding", "blender_patches/apply_narwhal3d_branding.py")
make = load("native_window_generator", "launch/make_icon.py")
STOCK_EDITS = tuple(native.EDITS)


def stock(root):
    files = {}
    for relative, old, _, count in STOCK_EDITS:
        files[relative] = files.get(relative, "") + (old + "\n") * count
    files[native.ICON_DEST] = "<svg/>"
    for relative in native.WINDOW_ICON_DESTS.values():
        files[relative] = "stock window icon"
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def raster_identity(data):
    # PNG compression differs between zlib and zlib-ng. Compare every decoded
    # frame, not codec output; bundle SHA256 checks still bind the stored bytes.
    with Image.open(BytesIO(data)) as image:
        format_name = image.format
        if format_name == "ICO":
            frames = [(size, image.ico.getimage(size).convert("RGBA"))
                      for size in sorted(image.ico.sizes())]
            return format_name, [(size, frame.size, hashlib.sha256(frame.tobytes()).hexdigest())
                                 for size, frame in frames]
        if format_name == "ICNS":
            sizes = sorted(image.info["sizes"])
        else:
            image.verify()
            sizes = [None]
    frames = []
    for size in sizes:
        with Image.open(BytesIO(data)) as image:
            frame = (image.icns.getimage(size) if size is not None else image).convert("RGBA")
            frames.append((size, frame.size, hashlib.sha256(frame.tobytes()).hexdigest()))
    return format_name, frames


def asset_identity(relative, data):
    if relative.suffix in (".png", ".ico"):
        return raster_identity(data)
    if relative.suffix != ".json":
        return data
    bundle = json.loads(data)
    if set(bundle) != {"schema", "icons"}:
        raise ValueError("unexpected native icon bundle fields")
    identities = {}
    for name, record in bundle["icons"].items():
        if set(record) != {"sha256", "base64"}:
            raise ValueError("unexpected native resource fields")
        decoded = base64.b64decode(record["base64"], validate=True)
        if hashlib.sha256(decoded).hexdigest() != record["sha256"]:
            raise ValueError("native resource digest mismatch")
        identities[name] = decoded if name == "x11" else raster_identity(decoded)
    return bundle["schema"], identities


class WindowIcons(unittest.TestCase):
    def test_generated_x11_argb_dimensions_and_orientation(self):
        image = Image.new("RGBA", (128, 128), (255, 0, 0, 128))
        image.paste((0, 255, 0, 128), (64, 0, 128, 64))
        image.paste((0, 0, 255, 128), (0, 64, 64, 128))
        image.paste((255, 255, 0, 128), (64, 64, 128, 128))
        bundle = json.loads(make.window_icon_bundle(image, b"\x00\x00\x01\x00"))
        header = base64.b64decode(bundle["icons"]["x11"]["base64"]).decode("ascii")
        words = [int(value, 16) for value in re.findall(r"0x([0-9a-f]{8})UL", header)]
        offset = 0
        for size in (32, 48):
            self.assertEqual(words[offset:offset + 2], [size, size])
            pixels = words[offset + 2:offset + 2 + size * size]
            self.assertEqual(len(pixels), size * size)
            self.assertEqual([pixels[0], pixels[size - 1], pixels[-size], pixels[-1]],
                             [0x80ff0000, 0x8000ff00, 0x800000ff, 0x80ffff00])
            offset += 2 + size * size
        self.assertEqual(offset, len(words))

    def test_native_replacements(self):
        with tempfile.TemporaryDirectory(prefix="window-icons-") as directory:
            root = Path(directory)
            stock(root)
            native.apply(str(root))
            # Independent destinations: changing a mapping must not move the goalposts.
            header = (root / "intern/ghost/intern/GHOST_IconX11.hh").read_text()
            self.assertIn("BLENDER_ICONS_WM_X11", header)
            self.assertIn("0x00000020UL, 0x00000020UL", header)
            self.assertEqual((root / "release/windows/icons/winblender.ico").read_bytes(),
                             (ROOT / "packaging/windows/narwhal3d.ico").read_bytes())
            data = (root / "release/darwin/Blender.app/Contents/Resources/blender_icon_legacy.icns").read_bytes()
            self.assertEqual(data[:4], b"icns")
            with Image.open(BytesIO(data)) as decoded, Image.open(ROOT / "artwork/Logo/narwhal.png") as source:
                expected = source.convert("RGBA").resize(decoded.size, Image.Resampling.LANCZOS)
                self.assertTrue(decoded.convert("RGBA").tobytes() == expected.tobytes(),
                                "ICNS pixels differ from canonical artwork at the decoded size")
            plist = (root / "release/darwin/Blender.app/Contents/Info.plist").read_text()
            self.assertNotIn("CFBundleIconName", plist)
            self.assertNotIn("blender_liquid_glass", plist)
            self.assertEqual(native.apply(str(root)), [])

    def test_bundle_and_generated_assets_match_canonical_artwork(self):
        for relative, data in make.assets().items():
            self.assertEqual(asset_identity(relative, (ROOT / relative).read_bytes()),
                             asset_identity(relative, data), str(relative))
        self.assertEqual(set(native.window_icons()), {
            "intern/ghost/intern/GHOST_IconX11.hh",
            "release/windows/icons/winblender.ico",
            "release/darwin/Blender.app/Contents/Resources/blender_icon_legacy.icns",
        })

    def test_pixel_changes_fail_even_with_valid_png_and_bundle_hashes(self):
        image = Image.new("RGBA", (16, 16), (12, 34, 56, 255))
        before = BytesIO()
        image.save(before, format="PNG", compress_level=0)
        alternate = BytesIO()
        image.save(alternate, format="PNG", compress_level=9)
        self.assertNotEqual(before.getvalue(), alternate.getvalue())
        self.assertEqual(raster_identity(before.getvalue()), raster_identity(alternate.getvalue()))
        image.putpixel((15, 15), (12, 34, 57, 255))
        after = BytesIO()
        image.save(after, format="PNG")
        self.assertNotEqual(raster_identity(before.getvalue()), raster_identity(after.getvalue()))
        original = json.loads((ROOT / "blender_patches/narwhal3d_window_icons.json").read_text())
        changed = json.loads(json.dumps(original))
        encoded = after.getvalue()
        changed["icons"]["macos"] = {"base64": base64.b64encode(encoded).decode(),
                                     "sha256": hashlib.sha256(encoded).hexdigest()}
        relative = Path("bundle.json")
        self.assertNotEqual(asset_identity(relative, json.dumps(original).encode()),
                            asset_identity(relative, json.dumps(changed).encode()))

    def test_corrupt_or_incomplete_bundle_fails_before_source_changes(self):
        original = json.loads((ROOT / "blender_patches/narwhal3d_window_icons.json").read_text())
        for mode in ("digest", "missing", "schema"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(prefix="bad-window-icons-") as directory:
                root = Path(directory)
                stock(root)
                icon = root / "narwhal3d_app_icon.svg"
                icon.write_bytes((ROOT / "blender_patches/narwhal3d_app_icon.svg").read_bytes())
                bundle = json.loads(json.dumps(original))
                if mode == "digest":
                    bundle["icons"]["x11"]["sha256"] = "0" * 64
                elif mode == "missing":
                    del bundle["icons"]["windows"]
                else:
                    bundle["schema"] = 2
                (root / native.WINDOW_ICON_BUNDLE).write_text(json.dumps(bundle))
                tracked = root / "source/blender/windowmanager/intern/wm_window.cc"
                before = tracked.read_bytes()
                with self.assertRaises(RuntimeError):
                    native.apply(str(root), str(icon))
                self.assertEqual(tracked.read_bytes(), before)


def mutations():
    original = native.window_icons
    for name, omitted in native.WINDOW_ICON_DESTS.items():
        def missing(icon_source=native.ICON_SOURCE, omit=omitted):
            return {path: data for path, data in original(icon_source).items() if path != omit}
        with patch.object(native, "window_icons", missing):
            result = unittest.TextTestRunner(stream=StringIO()).run(
                unittest.TestSuite([WindowIcons("test_native_replacements")]))
        if len(result.failures) != 1 or result.errors:
            raise AssertionError(f"missing {name} copy did not cause the expected assertion failure")
        print(f"RED: skipped {name} native icon copy")
    with patch.object(native, "EDITS", tuple(edit for edit in STOCK_EDITS
                                            if not edit[0].endswith("Info.plist"))):
        result = unittest.TextTestRunner(stream=StringIO()).run(
            unittest.TestSuite([WindowIcons("test_native_replacements")]))
    if len(result.failures) != 1 or result.errors:
        raise AssertionError("upstream Mac asset-catalog override was not rejected")
    print("RED: retained upstream Mac icon override")
    generator = make.window_icon_bundle

    def wrong_alpha(*args):
        bundle = json.loads(generator(*args))
        record = bundle["icons"]["x11"]
        data = base64.b64decode(record["base64"]).replace(b"0x80ff0000UL", b"0x00ff0000UL")
        record.update(base64=base64.b64encode(data).decode(), sha256=hashlib.sha256(data).hexdigest())
        return json.dumps(bundle).encode()

    with patch.object(make, "window_icon_bundle", wrong_alpha):
        result = unittest.TextTestRunner(stream=StringIO()).run(unittest.TestSuite([
            WindowIcons("test_generated_x11_argb_dimensions_and_orientation")]))
    if len(result.failures) != 1 or result.errors:
        raise AssertionError("wrong X11 alpha packing did not fail its pixel assertion")
    print("RED: wrong X11 alpha packing")
    with patch.dict(globals(), {"raster_identity": lambda data: "unchecked pixels"}):
        result = unittest.TextTestRunner(stream=StringIO()).run(unittest.TestSuite([
            WindowIcons("test_pixel_changes_fail_even_with_valid_png_and_bundle_hashes")]))
    if len(result.failures) != 1 or result.errors:
        raise AssertionError("ignored icon pixels did not fail the canonical-artwork check")
    print("RED: ignored decoded icon pixels")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(WindowIcons)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    mutations()
    result = unittest.TextTestRunner(verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(WindowIcons))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("NATIVE WINDOW ICONS: 6 mutations RED; restored GREEN (no runtime/GUI certification)")
