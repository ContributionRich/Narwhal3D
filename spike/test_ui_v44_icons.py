#!/usr/bin/env python3
"""Bounded v4.4 icon checks, no Blender/imported CAD runtime or shared-file edits.

python3 -B spike/test_ui_v44_icons.py [--mutations] [--raster-check]
Source modules are compiled directly, bypassing stale bytecode even in mutations.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zlib

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = Path(os.environ['NARWHAL_UI_REFERENCE_DIR']) if os.environ.get('NARWHAL_UI_REFERENCE_DIR') else None
RASTER_CHECK = "--raster-check" in sys.argv
# Independently measured from the approved CSV and exact supplied SVG bytes.
MAPPING_SHA = "67a7a212cad30fbc0cdf3586627863edbe95681e3c03ef486a2c2ea8e01a76ae"
SVG_SHA = "711f724f77f100f2c67f68b9c41a981531617190f7b94224a95033ff1e10c812"
# Raster receipt: librsvg 2.62.4 / cairo 1.18.6, transparent 96px output.
PNG_SHA = "2ddf65531d06a5b6333146bfff25814262a33ccd256067f020c900531dd3a41c"


def load_modules(mutation=None):
    package = "_ui_v44_test"
    pkg = types.ModuleType(package)
    pkg.__path__ = [str(ROOT / "narwhal3d")]
    sys.modules[package] = pkg
    modules = {}
    for name in ("flyout_icon_data", "toolbar_data", "ui_icon_data", "toolbars"):
        path = ROOT / "narwhal3d" / (name + ".py")
        source = path.read_text()
        if mutation and name == mutation[0]:
            old, new = mutation[1:]
            if source.count(old) != 1:
                raise AssertionError(f"mutation anchor is not unique: {name}: {old}")
            source = source.replace(old, new, 1)
        module = types.ModuleType(f"{package}.{name}")
        module.__file__ = str(path)
        module.__package__ = package
        sys.modules[module.__name__] = module
        setattr(pkg, name, module)
        exec(compile(source, str(path), "exec"), module.__dict__)
        modules[name] = module
    return modules["toolbar_data"], modules["ui_icon_data"], modules["toolbars"]


def digest_assets(names):
    return hashlib.sha256(b"".join(
        name.encode() + b"\0" + (ROOT / "narwhal3d/icons" / name).read_bytes()
        for name in sorted(names))).hexdigest()


def visible_records(category):
    records = (data.SIDEBAR["buttons"] if category == "Standard" else
               next(t["buttons"] for t in data.TABS if t["name"] == category))
    return [r for r in records if not tb._button(r, set()).is_separator]


class IconTests(unittest.TestCase):
    def test_mapping_receipt(self):
        entries = sorted((category, index, *row)
                         for (category, index), row in icons.TOOL_ICONS.items())
        self.assertEqual(len(entries), 81)
        self.assertEqual(len({row[2] for row in icons.TOOL_ICONS.values()}), 77)
        digest = hashlib.sha256(json.dumps(entries, ensure_ascii=True,
                                          separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(digest, MAPPING_SHA)

    def test_reference_csv_and_exact_svg_copies(self):
        if REFERENCE is None or not REFERENCE.is_dir():
            self.skipTest("external design reference unavailable; pinned receipts still checked")
        with (REFERENCE / "ICON_MAPPING.csv").open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 81)
        for row in rows:
            key = row["category"], int(row["index"])
            expected_png = row["new_icon"].replace(".svg", ".png")
            self.assertIn(key, icons.TOOL_ICONS)
            self.assertEqual(icons.TOOL_ICONS[key],
                             (row["original_label"], row["original_icon"], expected_png))
            button = tb.context_buttons(key[0])[key[1]]
            self.assertEqual((button.text, button.icon), (row["command_label"], expected_png))
        source = REFERENCE / "app/assets/icons"
        self.assertEqual({p.name for p in source.glob("generated-*.svg")},
                         icons.SVG_NAMES - {"narwhal-ai-mark.svg"})
        for name in icons.SVG_NAMES:
            self.assertEqual((source / name).read_bytes(),
                             (ROOT / "narwhal3d/icons" / name).read_bytes(), name)

    def test_every_placement_and_only_those_placements(self):
        seen = set()
        for category in tb.tab_names():
            for index, (record, button) in enumerate(zip(
                    visible_records(category), tb.context_buttons(category))):
                key = category, index
                mapping = icons.TOOL_ICONS.get(key)
                if mapping:
                    seen.add(key)
                    self.assertEqual((record["text"], record["icon"]), mapping[:2], key)
                    self.assertEqual(button.icon, mapping[2], key)
                else:
                    self.assertEqual(button.icon, record.get("icon"), key)
                self.assertEqual(button.text, "Left view" if key == ("Set View", 13)
                                 else record.get("text") or "", key)
        self.assertEqual(seen, set(icons.TOOL_ICONS))

    def test_action_semantics_and_imported_records_unchanged(self):
        original = copy.deepcopy((data.TABS, data.SIDEBAR, data.FLYOUTS))
        commands = {r.get(k) for category in tb.tab_names()
                    for r in visible_records(category) for k in ("command", "right_command")}
        fields = set(tb.Button.__slots__) - {"text", "icon"}
        for registry in (set(), {"move", "copy", "group"}, commands - {None}):
            for category in tb.tab_names():
                records = visible_records(category)
                actual = tb.context_buttons(category, registry)
                self.assertEqual(len(actual), len(records))
                for record, button in zip(records, actual):
                    baseline = tb._button(record, registry)
                    for field in fields:
                        self.assertEqual(getattr(button, field), getattr(baseline, field),
                                         (category, record["text"], field))
                    self.assertEqual(button.hover_text(), baseline.hover_text())
                    self.assertEqual(tb.sidebar_tooltip_lines(button),
                                     tb.sidebar_tooltip_lines(baseline))
        self.assertEqual((data.TABS, data.SIDEBAR, data.FLYOUTS), original)
        # Standard mapping belongs ONLY to favorites, never its legacy top tab.
        for record, button in zip(data.TABS[0]["buttons"], tb.tab_rows("Standard", commands)):
            self.assertEqual((button.text, button.icon), (record.get("text") or "", record.get("icon")))
        self.assertEqual(tb.tab_rows("unknown", commands), [])

    def test_new_functional_fields_and_appended_actions_survive(self):
        record = dict(visible_records("Visibility")[0], command="recent-command",
                      right_command="alternate", right_tooltip="Recent alternate",
                      preset=("NewOption=Yes",), link="Recent tools", available=False)
        extra = dict(record, text="Recent appended tool", icon="narwhal-copy.png")
        registry = {"recent-command", "alternate"}
        actual = tb._presented_buttons("Visibility", [record, extra], registry)
        expected = tb._button(record, registry)
        self.assertEqual(actual[0].icon, "generated-hide.png")
        for field in set(tb.Button.__slots__) - {"icon"}:
            self.assertEqual(getattr(actual[0], field), getattr(expected, field), field)
        self.assertEqual((actual[1].text, actual[1].icon), (extra["text"], extra["icon"]))
        self.assertFalse(actual[0].enabled)
        self.assertTrue(actual[0].right_enabled)

    def test_slot_original_data_guards(self):
        for category, index in icons.TOOL_ICONS:
            self.assertLess(index, len(visible_records(category)))
            record = visible_records(category)[index]
            self.assertIsNotNone(icons.presentation(category, index, record))
            self.assertIsNone(icons.presentation("unknown", index, record))
            self.assertIsNone(icons.presentation(category, 999, record))
            for field, value in (("text", "New functional action"), ("icon", "new-art.png")):
                self.assertIsNone(icons.presentation(category, index, dict(record, **{field: value})))
        left = visible_records("Set View")[13]
        self.assertIsNone(icons.presentation("Set View", 13, dict(left, tooltip="Right view")))
        self.assertIsNone(icons.presentation("Set View", 13, dict(left, command="new-command")))
        # Inserted tools do not receive the previous row's artwork by position alone.
        shifted = [dict(visible_records("Visibility")[0], text="Inserted tool")]
        shifted += visible_records("Visibility")
        for record, button in zip(shifted, tb._presented_buttons("Visibility", shifted, set())):
            self.assertEqual(button.icon, record["icon"])

    def test_separators_do_not_shift_indices(self):
        records = visible_records("Visibility")
        spaced = [r for record in records for r in ({}, record)]
        actual = tb._presented_buttons("Visibility", spaced, set())
        self.assertEqual(len(actual), len(spaced))
        self.assertTrue(all(button.is_separator for button in actual[::2]))
        self.assertEqual([b.icon for b in actual[1::2]],
                         [b.icon for b in tb.context_buttons("Visibility")])

    def test_left_right_are_distinct_without_rerouting(self):
        rows = tb.context_buttons("Set View", {"setview"})
        self.assertEqual((rows[12].text, rows[13].text), ("Right view", "Left view"))
        self.assertEqual((rows[12].icon, rows[13].icon),
                         ("generated-right-view.png", "generated-left-view.png"))
        self.assertEqual((rows[13].left, rows[13].tooltip), ("setview", "Left view"))
        self.assertFalse(tb.context_buttons("Set View", set())[13].enabled)
        self.assertNotEqual((ROOT / "narwhal3d/icons/generated-right-view.png").read_bytes(),
                            (ROOT / "narwhal3d/icons/generated-left-view.png").read_bytes())

    def test_inspector_and_reply_hooks(self):
        self.assertEqual(icons.INSPECTOR_ICONS, {
            "Layers": "generated-panel-layers.png", "Properties": "generated-panel-properties.png",
            "Display": "generated-panel-display.png", "Bug Report": "generated-panel-bug-report.png",
            "AI Assistant": "generated-panel-ai-assistant.png"})
        self.assertEqual(icons.AI_REPLY_ICON, "narwhal-ai-mark.png")
        svg = ET.parse(ROOT / "narwhal3d/icons/narwhal-ai-mark.svg").getroot()
        self.assertEqual(svg.attrib["viewBox"], "0 0 24 24")
        self.assertEqual([node.tag for node in svg], ["{http://www.w3.org/2000/svg}path"])
        self.assertEqual(svg[0].attrib["fill"], "#3679C2")
        self.assertEqual(set(svg[0].attrib), {"fill", "d"})

    def test_asset_receipts_and_png_integrity(self):
        self.assertEqual(len(icons.SVG_NAMES), 83)
        self.assertEqual(len(icons.PNG_NAMES), 83)
        self.assertEqual(digest_assets(icons.SVG_NAMES), SVG_SHA)
        self.assertEqual(digest_assets(icons.PNG_NAMES), PNG_SHA)
        for name in icons.PNG_NAMES:
            blob = (ROOT / "narwhal3d/icons" / name).read_bytes()
            self.assertEqual(blob[:8], b"\x89PNG\r\n\x1a\n", name)
            self.assertEqual(struct.unpack(">IIBBBBB", blob[16:29]), (96, 96, 8, 6, 0, 0, 0), name)
            offset, compressed = 8, bytearray()
            while offset < len(blob):
                size = struct.unpack(">I", blob[offset:offset + 4])[0]
                chunk = blob[offset + 4:offset + 8 + size]
                crc = struct.unpack(">I", blob[offset + 8 + size:offset + 12 + size])[0]
                self.assertEqual(zlib.crc32(chunk), crc, name)
                if chunk[:4] == b"IDAT":
                    compressed.extend(chunk[4:])
                offset += 12 + size
            self.assertEqual(offset, len(blob))
            self.assertEqual(len(zlib.decompress(compressed)), 96 * (96 * 4 + 1), name)

    def test_png_resolution_and_legacy_compatibility(self):
        for name in icons.PNG_NAMES | tb.V4_ICON_NAMES | data.ICON_NAMES:
            self.assertIsNotNone(tb.icon_path(name), name)
        for name in (None, 42, [], "generated-unknown.png", "../generated-copy.png",
                     str(ROOT / "narwhal3d/icons/generated-copy.png"), "generated-copy.svg"):
            self.assertIsNone(tb.icon_path(name))
        with tempfile.TemporaryDirectory(prefix="narwhal-v44-icons-") as directory:
            folder = Path(directory)
            outside = folder / "outside.png"
            outside.write_bytes(b"test")
            inside = folder / "icons"
            inside.mkdir()
            (inside / "generated-copy.png").symlink_to(outside)
            with patch.object(tb, "ICON_DIR", str(inside)):
                self.assertIsNone(tb.icon_path("generated-copy.png"))
                self.assertIsNone(tb.icon_path("generated-hide.png"))

    def test_availability_checks_include_new_pngs(self):
        self.assertTrue(tb.icons_available())
        with patch.object(tb, "icon_path", side_effect=lambda name:
                          None if name == "generated-copy.png" else "/bundled/icon.png"):
            self.assertFalse(tb.icons_available())

    @unittest.skipUnless(RASTER_CHECK, "use --raster-check for offline SVG-to-PNG parity")
    def test_rasterized_pixels_match_sources(self):
        executable = shutil.which("rsvg-convert")
        if not executable:
            self.skipTest("rsvg-convert is not installed; never install for this test")
        for name in sorted(icons.SVG_NAMES):
            result = subprocess.run([executable, "--width", "96", "--height", "96",
                                     str(ROOT / "narwhal3d/icons" / name)],
                                    capture_output=True, check=True, timeout=10)
            # Exact bytes on the recorded renderer; other versions may encode differently.
            self.assertEqual(result.stdout,
                             (ROOT / "narwhal3d/icons" / (name[:-4] + ".png")).read_bytes(), name)


MUTATIONS = {
    "omit-placement": ("ui_icon_data", '("Standard", 20):', '("Standard", 999):'),
    "wrong-art": ("ui_icon_data", '"generated-ungroup.png"', '"generated-group.png"'),
    "missing-label-guard": ("ui_icon_data", 'record.get("text") != original_text', 'False'),
    "missing-icon-guard": ("ui_icon_data", 'record.get("icon") != original_icon', 'False'),
    "missing-left-tooltip-guard": ("ui_icon_data", 'record.get("tooltip") != "Left view"', 'False'),
    "missing-left-command-guard": ("ui_icon_data", 'record.get("command") != "setview"', 'False'),
    "left-still-right": ("ui_icon_data", 'text = "Left view"', 'text = "Right view"'),
    "no-sidebar-overlay": ("toolbars", '_presented_buttons("Standard", sidebar.get',
                           '_presented_buttons(None, sidebar.get'),
    "reroute-command": ("toolbars", 'button.text, button.icon = visual',
                        'button.text, button.icon = visual\n                button.left = "copy"'),
    "lose-preset": ("toolbars", 'button.text, button.icon = visual',
                    'button.text, button.icon = visual\n                button.preset = ()'),
    "enable-unavailable": ("toolbars", 'button.text, button.icon = visual',
                           'button.text, button.icon = visual\n                button.enabled = True'),
    "lose-right-click": ("toolbars", 'button.text, button.icon = visual',
                         'button.text, button.icon = visual\n                button.right = None'),
    "no-png-allowlist": ("toolbars", 'and icon not in ui_icon_data.PNG_NAMES', ''),
    "ignore-missing-new-assets": ("toolbars", 'toolbar_data.ICON_NAMES | ui_icon_data.PNG_NAMES',
                                 'toolbar_data.ICON_NAMES'),
}


def run_suite(stream):
    return unittest.TextTestRunner(stream=stream, verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(IconTests))


if __name__ == "__main__":
    data, icons, tb = load_modules()
    if not run_suite(sys.stdout).wasSuccessful():
        raise SystemExit(1)
    if "--mutations" in sys.argv:
        # Asset checks need not invoke 83 child processes for every logic mutation.
        raster_test = "test_rasterized_pixels_match_sources"
        with patch.object(IconTests, raster_test, lambda self: None):
            for name, mutation in MUTATIONS.items():
                data, icons, tb = load_modules(mutation)
                result = run_suite(io.StringIO())
                if result.errors or not result.failures:
                    raise AssertionError(f"{name}: expected assertion RED, not error/survival")
                print(f"mutation {name}: RED ({len(result.failures)} failing tests)")
        data, icons, tb = load_modules()
        if not run_suite(sys.stdout).wasSuccessful():
            raise SystemExit(1)
        print(f"{len(MUTATIONS)} isolated mutations RED -> restored GREEN; no source files changed")
