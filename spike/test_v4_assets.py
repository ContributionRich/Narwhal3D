"""Exact v7 UI design contract; legacy filename retained for harness compatibility.

MEASURED from the v4 light and v7 dark palettes and extracted final CSS, plus the
owner-authorized 0.4.7 readability overrides (OURS): light rail ink/title fills and
dark accent foregrounds only. Fixtures are pinned, not loaded from ignored tmp/.
CSS overrides win over embedded JSON. These are fidelity tests, NOT accessibility
waivers: test_design.py independently retains the normal-text contrast gate.

SVGs remain byte-for-byte approved copies. PNGs were exported by rsvg-convert 2.62.4
(cairo 1.18.6), --width=96 --height=96, with no background/recolouring. All five
were measured as RGBA with alpha extrema 0..1. Pinned full-content hashes protect
that transparency and the exact artwork, not just filenames or PNG signatures.
If intentionally re-exporting with another renderer, review pixels before
updating hashes; do not regenerate expectations as part of the test itself.
"""
import hashlib

import os
from pathlib import Path
import runpy
import struct
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "narwhal3d" / "design.py"

# Requested override: v4 light palette with grey viewports, alongside v7 dark.
LIGHT = {
    "APP_BG": "D6DEE8", "BAR_BG": "334151", "STATUS_BG": "354252",
    "RIBBON_BG": "455364", "RIBBON_HOVER": "586A80", "BAR_BORDER": "66778C",
    "BAR_CHIP": "45566B", "CONTROL_BG": "F2F5FA", "COMMAND_FRAME": "CFD9E5",
    "PANEL_BG": "F2F5FA", "PANEL_HEADER_BG": "E5ECF5", "RAIL_BG": "E8EEF6",
    "LIST_BG": "F8FAFD", "CHIP_BG": "E5EBF4", "FIELD_BG": "EFF3F9",
    "MENU_BG": "FFFFFF", "TITLE_CHIP_BG": "68788A", "VIEWPORT_BORDER": "9AA7B6",
    "BORDER": "D0D9E5", "BORDER_STRONG": "99AABD", "ACCENT": "266CE2",
    "ACCENT_DEEP": "266CE2", "ACCENT_TITLE": "2573E5", "ACCENT_SOFT": "D9E7FF",
    "RAIL_ACTIVE_TEXT": "2362CE", "TOOL_HOVER": "DDEBFF", "TOOL_ICON": "2365BF",
    "TEXT": "253343", "TEXT_STRONG": "202B3A", "TEXT_MUTED": "526275",
    "TEXT_DISABLED": "A3AEBB", "BAR_TEXT": "EDF3FC", "BAR_TEXT_STRONG": "FFFFFF",
    "BAR_TEXT_MUTED": "B8C4D2", "CHIP_TEXT": "FFFFFF", "ACTIVE_TITLE_TEXT": "FFFFFF",
    "VIEWPORT_BG": "AAB5C1",
    "VIEWPORT_TEXT": "202B3A", "GRID_MINOR": "9DA8B4", "GRID_MAJOR": "8F9BA8",
    "AXIS_X": "DB5963", "AXIS_Y": "68A580", "WIRE": "000000", "POINT": "0D0D0D",
    "PREVIEW": "1A1A1A", "CONTROL_POINT": "1A1A8C", "CONTROL_POLYGON": "383840",
    "SUBOBJECT_PREVIEW": "383D45",
    # Final CSS overrides, with their own semantic surfaces/text.
    "CONSOLE_BG": "F7F9FC", "CONSOLE_TEXT": "344456", "CONSOLE_INPUT": "16253A",
    "PROMPT": "253950", "PROMPT_OPTION": "253950", "CONSOLE_BORDER": "B5C3D3",
    "CONSOLE_FRAME_BORDER": "A9B8CB", "CONSOLE_FOCUS": "3279DF",
    "PANEL_OUTLINE": "BFCBD9", "TOOL_CELL_HOVER": "D7E7FF", "OSNAP_BG": "F4F7FB",
    "LAYER_HOVER": "EEF4FC",
}
# v7 charcoal JSON replaces the obsolete v4 dark hash; keep every token explicit.
DARK = {
    "APP_BG": "2B2B2B", "BAR_BG": "3A3A3A", "STATUS_BG": "3A3A3A",
    "RIBBON_BG": "4A4A4A", "RIBBON_HOVER": "5A5A5A", "BAR_BORDER": "5F5F5F",
    "BAR_CHIP": "4A4A4A", "CONTROL_BG": "2F2F2F", "COMMAND_FRAME": "242424",
    "PANEL_BG": "333333", "PANEL_HEADER_BG": "3B3B3B", "RAIL_BG": "313131",
    "LIST_BG": "2F2F2F", "CHIP_BG": "444444", "FIELD_BG": "2B2B2B",
    "MENU_BG": "3A3A3A", "TITLE_CHIP_BG": "5E5E5E", "VIEWPORT_BORDER": "6C6C6C",
    "BORDER": "4B4B4B", "BORDER_STRONG": "5E5E5E", "ACCENT": "4EA1FF",
    "ACCENT_DEEP": "4EA1FF", "ACCENT_TITLE": "6BB3FF", "ACCENT_SOFT": "203248",
    "RAIL_ACTIVE_TEXT": "4EA1FF", "TOOL_HOVER": "454545", "TOOL_ICON": "125BF0",
    "TEXT": "D0D0D0", "TEXT_STRONG": "F0F0F0", "TEXT_MUTED": "A3A3A3",
    "TEXT_DISABLED": "707070", "BAR_TEXT": "D8D8D8", "BAR_TEXT_STRONG": "15253A",
    "BAR_TEXT_MUTED": "A2A8B3", "CHIP_TEXT": "FFFFFF", "ACTIVE_TITLE_TEXT": "15253A",
    "VIEWPORT_BG": "2C2C2C",
    "VIEWPORT_TEXT": "D7DEE7", "GRID_MINOR": "3A3A3A", "GRID_MAJOR": "515151",
    "AXIS_X": "C83A3A", "AXIS_Y": "3AB84A", "WIRE": "C9D1DB", "POINT": "E6EAF0",
    "PREVIEW": "FFFFFF", "CONTROL_POINT": "73A6FF", "CONTROL_POLYGON": "9EADC2",
    "SUBOBJECT_PREVIEW": "C7D1E0",
    # body[data-theme="dark"] wins over JSON; .prompt .opt keeps BAR_TEXT_STRONG.
    "CONSOLE_BG": "262626", "CONSOLE_TEXT": "D9D9D9", "CONSOLE_INPUT": "F0F0F0",
    "PROMPT": "E6E6E6", "PROMPT_OPTION": "E6E6E6", "CONSOLE_BORDER": "515151",
    "CONSOLE_FRAME_BORDER": "5E5E5E", "CONSOLE_FOCUS": "4EA1FF",
    "PANEL_OUTLINE": "4B4B4B", "TOOL_CELL_HOVER": "454545", "OSNAP_BG": "303030",
    "LAYER_HOVER": "3A3A3A",
}
METRICS = {
    "MENUBAR_H": 26, "CATEGORY_H": 78, "CATEGORY_BUTTON_W": 80,
    "COMMAND_BOX_H": 65, "COMMAND_MARGIN": 5, "GAP": 5, "VIEWPORT_GAP": 4,
    "RADIUS": 6, "PANEL_RADIUS": 4, "VIEWPORT_RADIUS": 5, "VIEWPORT_BORDER_W": 2,
    "CHIP_RADIUS": 4, "LEFT_PANEL_W": 126, "TOOL_CELL": 35,
    "RIGHT_RAIL_W": 72, "RIGHT_PANEL_W": 208, "TITLE_CHIP_INSET": 6,
    "TITLE_CHIP_PAD_X": 10, "TITLE_CHIP_PAD_Y": 6, "STATUS_H": 37,
    "STATUS_CHIP_PAD": 8, "CONSOLE_H": 75, "CONSOLE_INPUT_FONT": 14,
    "PROMPT_FONT": 13, "CONSOLE_HISTORY_WEIGHT": 500, "PROMPT_WEIGHT": 650,
    "CONSOLE_FOCUS_W": 2, "TOOL_GAP": 3, "CATEGORY_GAP": 3,
    "CATEGORY_PAD_X": 7, "CATEGORY_PAD_Y": 5, "CATEGORY_RADIUS": 6,
    "RAIL_ACTIVE_WEIGHT": 650, "RAIL_ICON_RADIUS": 8, "RAIL_ICON_SIZE": 20,
    "VIEWPORT_CONTROL_SIZE": 20, "VIEWPORT_CONTROL_ICON_SIZE": 13,
    "FLYOUT_CELL": 32, "FLYOUT_ICON": 22, "COORDINATE_SLOT_W": 242,
}
# stem: (approved source SHA256, reviewed transparent raster SHA256)
ASSETS = {
    "v4-panel-properties": (
        "b8dfd1c1839bb2fd78cdc95016bef8afb76510a3a98c83bd2d7a86702f534049",
        "e085afabd361a1fb699ea03131f7a1d74a2fdfb4d09e551af5e499f1e90c8d55"),
    "v4-panel-display": (
        "3d1db5f42972038a6c45a8ca1130a7400d0fb6ee9647cbde9dbf09f3a0b8c100",
        "4f9bfeb6079acc9d16612b847029f6fd13f33d6bf1da24cd1271374389cd98b1"),
    "v4-panel-ai-assistant": (
        "eec24ea76d0c762099885f2b039ab75b631e0883c44bb3416f0ed263e871542d",
        "92731d320f9a6b44d776021c4fa555f0ba920545c239837507415953d27c0979"),
    "v4-viewport-maximize": (
        "a847b0ccf1d6902906b3004364157b9a6af1c530e008184aa7eabfbff9c8f474",
        "4cf72b1b722c731404598cf69eeaef0284ff53337e82aff6a592ec63efeb5692"),
    "v4-viewport-restore": (
        "a3402e83d4e6eb05a63ae0a404e6e7cbad8f7056f38514b62829ea8082ebb388",
        "f61207d7f346c1b67ef0e1a3775ddfb3699744c2c4789b75bfeaac91e579815b"),
}


def load_design(theme="light"):
    # run_path compiles the source, avoiding stale path-import .pyc during mutations.
    with patch.dict(os.environ, {"NARWHAL3D_THEME": theme}):
        return runpy.run_path(str(DESIGN))


class V7DesignTests(unittest.TestCase):
    def test_exact_light_and_exports(self):
        design = load_design()
        self.assertEqual(design["THEME"], "light")
        self.assertEqual(set(design["THEMES"]["light"]), set(LIGHT))
        for name, hex_rgb in LIGHT.items():
            with self.subTest(token=name):
                expected = tuple(bytes.fromhex(hex_rgb))
                self.assertEqual(design["THEMES"]["light"][name], expected)
                self.assertEqual(design[name], expected)

    def test_metrics(self):
        design = load_design()
        for name, expected in METRICS.items():
            with self.subTest(metric=name):
                self.assertEqual(design[name], expected)
        self.assertEqual(design["CONSOLE_H"],
                         design["COMMAND_BOX_H"] + 2 * design["COMMAND_MARGIN"])
        self.assertEqual(design["RIGHT_RAIL_W"] + design["RIGHT_PANEL_W"], 280)

    def test_exact_dark_and_exports(self):
        design = load_design("dark")
        self.assertEqual(design["THEME"], "dark")
        self.assertEqual(set(design["THEMES"]["dark"]), set(DARK))
        self.assertEqual(set(DARK), set(LIGHT))
        for name, hex_rgb in DARK.items():
            with self.subTest(token=name):
                expected = tuple(bytes.fromhex(hex_rgb))
                self.assertEqual(design["THEMES"]["dark"][name], expected)
                self.assertEqual(design[name], expected)


class V4AssetTests(unittest.TestCase):
    def test_exact_svg_artwork(self):
        for stem, (svg_hash, _) in ASSETS.items():
            with self.subTest(asset=stem):
                data = (ROOT / "artwork" / "icons" / (stem + ".svg")).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), svg_hash)
                svg = ET.fromstring(data)
                self.assertEqual(svg.tag, "{http://www.w3.org/2000/svg}svg")
                self.assertEqual(svg.attrib["viewBox"], "0 0 24 24")
                self.assertEqual(svg.attrib["fill"], "none")

    def test_exact_transparent_png_exports(self):
        for stem, (_, png_hash) in ASSETS.items():
            with self.subTest(asset=stem):
                data = (ROOT / "narwhal3d" / "icons" / (stem + ".png")).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), png_hash)
                self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
                self.assertEqual(data[12:16], b"IHDR")
                # Width, height, 8-bit RGBA, standard compression/filter, no interlace.
                self.assertEqual(struct.unpack(">IIBBBBB", data[16:29]),
                                 (96, 96, 8, 6, 0, 0, 0))

    def test_no_shared_placeholders(self):
        for folder, suffix in ((ROOT / "artwork" / "icons", ".svg"),
                               (ROOT / "narwhal3d" / "icons", ".png")):
            with self.subTest(suffix=suffix):
                self.assertEqual(len({(folder / (stem + suffix)).read_bytes()
                                      for stem in ASSETS}), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
