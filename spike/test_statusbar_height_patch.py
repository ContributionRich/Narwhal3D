#!/usr/bin/env python3
"""Fixture-only patch and native sizing tests; never reads or patches tools/.

Requires a C++17 compiler named c++. Only a small temporary fixture is compiled;
no Blender build, runtime, GUI, or installed provenance is accessed.
"""
from __future__ import annotations

import importlib.util
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "blender_patches" / "apply_statusbar_height.py"
spec = importlib.util.spec_from_file_location("statusbar_height_patch", PATCH)
assert spec is not None and spec.loader is not None
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)

# Independent Blender 5.2.2 sizing excerpts with minimal stand-ins for window geometry.
# Keep the upstream anchors here, rather than assembling fixtures from patch.EDITS.
FIXTURE = '''#include <cmath>
#include <cstring>
#include <iostream>
#include <vector>

float ui_scale = 1.0f;
#define UI_SCALE_FAC ui_scale
struct { int pixelsize = 1; } U;
enum eSpace_Type { SPACE_TOPBAR, SPACE_STATUSBAR };
enum GlobalAreaAlign { GLOBAL_AREA_ALIGN_TOP, GLOBAL_AREA_ALIGN_BOTTOM };
constexpr int SCREENFULL = 1, SCREEN_COLLAPSE_STATUSBAR = 2;
struct rcti { int xmin = 0, xmax = 1000, ymin = 0, ymax = 800; };
struct ScrGlobalAreaData {
  short size_max = 0, size_min = 0, cur_fixed_height = 0;
  GlobalAreaAlign align = GLOBAL_AREA_ALIGN_TOP;
};
struct ScrArea {
  eSpace_Type spacetype;
  ScrGlobalAreaData *global = nullptr;
  rcti rect;
};
struct ScrAreaMap { std::vector<ScrArea> areabase; };
struct wmWindow {
  ScrAreaMap global_areas;
  ~wmWindow() { for (auto &area : global_areas.areabase) delete area.global; }
};
struct bScreen { int state = 0, flag = 0; bool do_refresh = false; };
template<typename T> T *MEM_new(const char *) { return new T; }
void *WM_window_get_active_scene(wmWindow *) { return nullptr; }
void screen_area_spacelink_add(void *, ScrArea *, eSpace_Type) {}
void WM_window_rect_calc(wmWindow *, rcti *rect) { *rect = rcti{}; }
int ED_area_headersize() { return int(26.0f * UI_SCALE_FAC); }
void screen_area_set_geometry_rect(ScrArea *area, const rcti *rect) { area->rect = *rect; }
ScrArea *screen_area_create_with_geometry(ScrAreaMap *map, const rcti *rect, eSpace_Type type)
{
  map->areabase.push_back({type, nullptr, *rect});
  return &map->areabase.back();
}

static void screen_global_area_refresh(wmWindow *win,
                                       bScreen *screen,
                                       const eSpace_Type space_type,
                                       GlobalAreaAlign align,
                                       const rcti *rect,
                                       const short height_cur,
                                       const short height_min,
                                       const short height_max)
{
  /* Full-screens shouldn't have global areas. Don't touch them. */
  if (screen->state == SCREENFULL) {
    return;
  }

  ScrArea *area = nullptr;
  for (ScrArea &area_iter : win->global_areas.areabase) {
    if (area_iter.spacetype == space_type) {
      area = &area_iter;
      break;
    }
  }

  if (area) {
    screen_area_set_geometry_rect(area, rect);
  }
  else {
    area = screen_area_create_with_geometry(&win->global_areas, rect, space_type);
    screen_area_spacelink_add(WM_window_get_active_scene(win), area, space_type);

    /* Data specific to global areas. */
    area->global = MEM_new<ScrGlobalAreaData>(__func__);
    area->global->size_max = height_max;
    area->global->size_min = height_min;
    area->global->align = align;
  }

  if (area->global->cur_fixed_height != height_cur) {
    /* Refresh layout if size changes. */
    area->global->cur_fixed_height = height_cur;
    screen->do_refresh = true;
  }
}

static int screen_global_header_size()
{
  return int(ceilf(ED_area_headersize() / UI_SCALE_FAC));
}

static void screen_global_topbar_area_refresh(wmWindow *win, bScreen *screen)
{
  const short size = screen_global_header_size();
  rcti rect;

  /* Use content rect to account for CSD, converted to inclusive bounds for area geometry. */
  WM_window_rect_calc(win, &rect);
  rect.xmax -= 1;
  rect.ymin = (rect.ymax - 1) - size;
  rect.ymax -= 1;

  screen_global_area_refresh(
      win, screen, SPACE_TOPBAR, GLOBAL_AREA_ALIGN_TOP, &rect, size, size, size);
}

static void screen_global_statusbar_area_refresh(wmWindow *win, bScreen *screen)
{
  const short size_min = 1;
  const short size_max = 0.85f * screen_global_header_size();
  const short size = (screen->flag & SCREEN_COLLAPSE_STATUSBAR) ? size_min : size_max;
  rcti rect;

  /* Use content rect to account for CSD, converted to inclusive bounds for area geometry. */
  WM_window_rect_calc(win, &rect);
  rect.xmax -= 1;
  rect.ymax = rect.ymin + size_max;

  screen_global_area_refresh(
      win, screen, SPACE_STATUSBAR, GLOBAL_AREA_ALIGN_BOTTOM, &rect, size, size_min, size_max);
}
'''

HARNESS = '''
int main(int argc, char **argv)
{
  if (argc != 4) return 2;
  ui_scale = std::strtof(argv[1], nullptr);
  U.pixelsize = std::atoi(argv[2]);
  const bool collapsed = std::atoi(argv[3]);
  wmWindow win;
  bScreen screen;
  screen.flag = collapsed ? SCREEN_COLLAPSE_STATUSBAR : 0;
  screen_global_statusbar_area_refresh(&win, &screen);
  auto &area = win.global_areas.areabase.front();
  auto &g = *area.global;
  std::cout << g.size_max << ' ' << g.size_min << ' ' << g.cur_fixed_height << ' '
            << screen.do_refresh << ' ' << area.rect.ymax << ' ' << g.align << ' ';
  screen.do_refresh = false;
  screen_global_statusbar_area_refresh(&win, &screen);
  std::cout << screen.do_refresh << ' ';

  // A pre-existing area must acquire new bounds, even while collapsed.
  g.size_max = 7;
  g.size_min = 2;
  screen_global_statusbar_area_refresh(&win, &screen);
  std::cout << g.size_max << ' ' << g.size_min << ' ' << screen.do_refresh << ' '
            << g.cur_fixed_height << ' ';
  screen.flag ^= SCREEN_COLLAPSE_STATUSBAR;
  screen_global_statusbar_area_refresh(&win, &screen);
  std::cout << g.cur_fixed_height << ' ';

  wmWindow top;
  bScreen top_screen;
  screen_global_topbar_area_refresh(&top, &top_screen);
  auto &top_global = *top.global_areas.areabase.front().global;
  std::cout << top_global.cur_fixed_height << ' ';
  top_global.size_max = 99;
  top_screen.do_refresh = false;
  screen_global_topbar_area_refresh(&top, &top_screen);
  std::cout << top_global.size_max << ' ' << top_screen.do_refresh << ' ';

  wmWindow full;
  bScreen full_screen;
  full_screen.state = SCREENFULL;
  screen_global_statusbar_area_refresh(&full, &full_screen);
  std::cout << full.global_areas.areabase.size() << ' '
            << screen_global_header_size() << '\\n';
}
'''


def fixture(root: Path, text: str = FIXTURE) -> Path:
    path = root / patch.RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def compile_fixture(root: Path, text: str, name: str) -> Path:
    source = root / f"{name}.cc"
    binary = root / name
    source.write_text(text + HARNESS, encoding="utf-8")
    result = subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                             str(source), "-o", str(binary)],
                            capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise AssertionError(f"fixture compilation failed:\n{result.stdout}{result.stderr}")
    return binary


class StatusbarHeightPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="narwhal-statusbar-height-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        path = fixture(cls.root)
        patch.apply(cls.root)
        cls.patched = path.read_text(encoding="utf-8")
        cls.binary = compile_fixture(cls.root, cls.patched, "statusbar")
        cls.stock_binary = compile_fixture(cls.root, FIXTURE.replace(
            "#include <cstring>", "#include <cstring>\n#include <cstdlib>"), "stock")

    def run_native(self, binary, defer="1", height="37", scale=1.0, pixels=1,
                   collapsed=False, ready=None, watchdog=None):
        env = os.environ.copy()
        for key, value in (("NARWHAL3D_DEFER_FIRST_PRESENT", defer),
                           ("NARWHAL3D_STATUSBAR_HEIGHT", height),
                           ("NARWHAL3D_UI_READY", ready),
                           ("NARWHAL3D_PRESENT_WATCHDOG", watchdog)):
            env.pop(key, None)
            if value is not None:
                env[key] = value
        result = subprocess.run([str(binary), str(scale), str(pixels), str(int(collapsed))],
                                env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        values = list(map(int, result.stdout.split()))
        self.assertEqual(len(values), 17, result.stdout)
        return values

    def assert_behavior(self, binary, **kwargs):
        values = self.run_native(binary, **kwargs)
        scale = kwargs.get("scale", 1.0)
        pixels = kwargs.get("pixels", 1)
        collapsed = kwargs.get("collapsed", False)
        active = kwargs.get("defer", "1") == "1" and kwargs.get("height", "37") == "37"
        if active:
            desired = math.floor(37 * scale + 0.5)
            maximum = math.floor((desired - pixels) / scale + 0.5)
            physical = math.floor(values[0] * scale + 0.5) + pixels
            self.assertLessEqual(abs(physical - desired), 1, (kwargs, physical, desired))
        else:
            # Get stock conversion from the independently compiled unpatched fixture.
            maximum = self.run_native(self.stock_binary, **kwargs)[0]
        current = 1 if collapsed else maximum
        self.assertEqual(values[:12], [maximum, 1, current, 1, maximum, 1, 0,
                                       maximum, 1, 1, current, maximum if collapsed else 1])
        self.assertEqual(values[12:], [values[16], 99, 0, 0, values[16]])
        return values

    def test_apply_idempotence_and_only_target_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = fixture(root)
            neighbor = path.with_name("area.cc")
            neighbor.write_text("unchanged", encoding="utf-8")
            self.assertEqual(patch.apply(root), [patch.RELATIVE])
            once = path.read_bytes()
            stamp = path.stat().st_mtime_ns
            self.assertEqual(patch.apply(root), [])
            self.assertEqual(path.read_bytes(), once)
            self.assertEqual(path.stat().st_mtime_ns, stamp)
            self.assertEqual(neighbor.read_text(encoding="utf-8"), "unchanged")
            begin = FIXTURE.index("static void screen_global_topbar_area_refresh")
            end = FIXTURE.index("static void screen_global_statusbar_area_refresh")
            self.assertIn(FIXTURE[begin:end], once.decode())

    def test_all_anchor_failures_leave_fixture_untouched(self):
        for _, old, new in patch.EDITS:
            for text in (FIXTURE.replace(old, "", 1), FIXTURE + old,
                         self.patched + new):
                with self.subTest(anchor=old.splitlines()[0]), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    path = fixture(root, text)
                    with self.assertRaisesRegex(RuntimeError, "anchor mismatch"):
                        patch.apply(root)
                    self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_partial_application_resumes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = fixture(root, FIXTURE.replace(patch.INCLUDE_OLD, patch.INCLUDE_NEW))
            self.assertEqual(patch.apply(root), [patch.RELATIVE])
            self.assertEqual(path.read_text(encoding="utf-8"), self.patched)

    def test_cli_and_src_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture(root)
            command = [sys.executable, "-B", str(PATCH), "--source", str(root)]
            first = subprocess.run(command, capture_output=True, text=True, timeout=5)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(first.stdout, f"patched: {patch.RELATIVE}\n")
            second = subprocess.run(command, capture_output=True, text=True, timeout=5)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(second.stdout, "")
            previous = patch.SRC
            try:
                patch.SRC = root
                self.assertEqual(patch.apply(), [])
            finally:
                patch.SRC = previous

    def test_exact_environment_gate(self):
        for defer in (None, "", "0", "1", "10", "1extra", "true", " 1"):
            for height in (None, "", "36", "37", "38", "037", "37px", "37.0", "37 "):
                with self.subTest(defer=defer, height=height):
                    self.assert_behavior(self.binary, defer=defer, height=height)

    def test_scaled_height_and_collapsed_refresh(self):
        # Binary-exact scales make the independent rounding oracle unambiguous.
        for scale in (0.5, 0.625, 0.75, 0.875, 1.0, 1.125, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0):
            for pixels in (1, 2):
                for collapsed in (False, True):
                    with self.subTest(scale=scale, pixels=pixels, collapsed=collapsed):
                        self.assert_behavior(self.binary, scale=scale, pixels=pixels,
                                             collapsed=collapsed)
                        self.assert_behavior(self.binary, defer=None, scale=scale,
                                             pixels=pixels, collapsed=collapsed)
        self.assertEqual(self.assert_behavior(self.binary)[0], 36)

    def test_presentation_release_does_not_change_height(self):
        for ready in (None, "0", "1"):
            for watchdog in (None, "0", "1"):
                with self.subTest(ready=ready, watchdog=watchdog):
                    self.assert_behavior(self.binary, ready=ready, watchdog=watchdog)

    def test_mutations_are_rejected_by_behavior_checks(self):
        mutations = (
            ("missing app gate", 'std::strcmp(defer, "1") != 0', 'false', {"defer": "0"}),
            ("missing height gate", 'std::strcmp(height, "37") != 0', 'false', {"height": "38"}),
            ("missing boundary correction", 'desired_physical - U.pixelsize',
             'desired_physical', {}),
            ("truncate normalized height", 'std::round((desired_physical - U.pixelsize) / UI_SCALE_FAC)',
             '((desired_physical - U.pixelsize) / UI_SCALE_FAC)', {"scale": 1.5}),
            ("stale bounds", 'area->global->size_max = height_max;',
             'area->global->size_max = 7;', {"collapsed": True}),
            ("topbar bounds changed", 'space_type == SPACE_STATUSBAR &&', 'true &&', {}),
            ("collapsed minimum changed", 'const short size_min = 1;',
             'const short size_min = 2;', {"collapsed": True}),
            ("stock fallback changed", 'return short(0.85f * screen_global_header_size());',
             'return 36;', {"defer": None}),
        )
        for index, (label, old, new, kwargs) in enumerate(mutations):
            with self.subTest(mutation=label):
                # Mutate only the refresh assignment, not the creation assignment.
                text = self.patched
                if label == "stale bounds":
                    before, separator, after = text.rpartition(old)
                    self.assertTrue(separator)
                    text = before + new + after
                else:
                    self.assertEqual(text.count(old), 1)
                    text = text.replace(old, new, 1)
                binary = compile_fixture(self.root, text, f"mutation_{index}")
                with self.assertRaises(AssertionError):
                    self.assert_behavior(binary, **kwargs)
                print(f"  RED confirmed: {label}", flush=True)
                self.assert_behavior(self.binary, **kwargs)
                print(f"  GREEN restored: {label}", flush=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
