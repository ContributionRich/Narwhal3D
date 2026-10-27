#!/usr/bin/env python3
"""Apply the opt-in 37-logical-pixel statusbar patch to Blender 5.2.2.

Python only orchestrates source patching; sizing remains in Blender's native C++.
``apply(source)`` takes the directory containing ``editors/`` (source/blender),
not the checkout root. Without an argument it uses the local development checkout.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "tools" / "blender-src" / "source" / "blender"
RELATIVE = "editors/screen/screen_edit.cc"

INCLUDE_OLD = "#include <cmath>\n#include <cstring>\n"
INCLUDE_NEW = "#include <cmath>\n#include <cstdlib>\n#include <cstring>\n"

HELPER_OLD = "static void screen_global_statusbar_area_refresh(wmWindow *win, bScreen *screen)\n"
HELPER_NEW = '''static short narwhal_statusbar_fixed_height()
{
  const char *defer = std::getenv("NARWHAL3D_DEFER_FIRST_PRESENT");
  const char *height = std::getenv("NARWHAL3D_STATUSBAR_HEIGHT");
  if (defer == nullptr || std::strcmp(defer, "1") != 0 || height == nullptr ||
      std::strcmp(height, "37") != 0)
  {
    return short(0.85f * screen_global_header_size());
  }

  /* Global bottom-area geometry adds one boundary pixel unit to its content height.
   * Convert the requested scaled content height back to Blender's logical short size. */
  const int desired_physical = int(std::round(37.0f * UI_SCALE_FAC));
  return short(std::round((desired_physical - U.pixelsize) / UI_SCALE_FAC));
}

''' + HELPER_OLD

SIZE_OLD = "  const short size_max = 0.85f * screen_global_header_size();\n"
SIZE_NEW = "  const short size_max = narwhal_statusbar_fixed_height();\n"

BOUNDS_OLD = "  if (area->global->cur_fixed_height != height_cur) {\n"
BOUNDS_NEW = '''  /* Statusbar bounds may change after creation when its requested size or DPI changes. */
  if (space_type == SPACE_STATUSBAR &&
      (area->global->size_min != height_min || area->global->size_max != height_max))
  {
    area->global->size_min = height_min;
    area->global->size_max = height_max;
    screen->do_refresh = true;
  }

''' + BOUNDS_OLD

EDITS = (
    (RELATIVE, INCLUDE_OLD, INCLUDE_NEW),
    (RELATIVE, HELPER_OLD, HELPER_NEW),
    (RELATIVE, SIZE_OLD, SIZE_NEW),
    (RELATIVE, BOUNDS_OLD, BOUNDS_NEW),
)


def apply(source: str | os.PathLike[str] | None = None) -> list[str]:
    """Validate all anchors before writing; return changed paths relative to source."""
    root = Path(source) if source is not None else Path(SRC)
    path = root / RELATIVE
    original = path.read_text(encoding="utf-8")
    text = original
    for _, old, new in EDITS:
        count = text.count(new)
        if count == 1 and text.count(old) == new.count(old):
            continue
        if count != 0 or text.count(old) != 1:
            raise RuntimeError(f"statusbar-height patch anchor mismatch: {RELATIVE}: {old.splitlines()[0]}")
        text = text.replace(old, new, 1)
    if text == original:
        return []
    path.write_text(text, encoding="utf-8")
    return [RELATIVE]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=None,
                        help="Blender source/blender directory (contains editors/)")
    args = parser.parse_args(argv)
    for path in apply(args.source):
        print(f"patched: {path}")


if __name__ == "__main__":
    main()
