"""Apply Narwhal 3D's minimal native identity patch to the bundled runtime source.

Only presentation surfaces are changed: window titles, Linux application identity,
normal console banners, quit text, and the Wayland window icon. File formats, version
numbers, internal APIs, and runtime behavior remain untouched.

Run from the project root: ``python3 blender_patches/apply_narwhal3d_branding.py``.
The patch is idempotent and asserts every stock source anchor before changing it.
"""
from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "tools", "blender-src")
ICON_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "narwhal3d_app_icon.svg")
ICON_DEST = os.path.join("release", "freedesktop", "icons", "scalable", "apps",
                         "blender.svg")

# (relative source path, stock text, Narwhal text, expected count)
EDITS = (
    ("source/blender/windowmanager/intern/wm_window.cc",
     '    return "Blender";', '    return "Narwhal 3D";', 1),
    ("source/blender/windowmanager/intern/wm_window.cc",
     '  win_title.append(fmt::format(" - Blender {}", BKE_blender_version_string()));',
     '  win_title.append(" - Narwhal 3D");', 1),
    ("intern/ghost/intern/GHOST_SystemX11.hh",
     '#  define GHOST_X11_RES_NAME "Blender"  /* res_name */\n'
     '#  define GHOST_X11_RES_CLASS "Blender" /* res_class */',
     '#  define GHOST_X11_RES_NAME "narwhal3d"  /* res_name */\n'
     '#  define GHOST_X11_RES_CLASS "Narwhal 3D" /* res_class */', 1),
    ("intern/ghost/intern/GHOST_SystemWayland.cc",
     '#else\n    "blender"\n#endif', '#else\n    "narwhal3d"\n#endif', 1),
    ("source/creator/creator_args.cc",
     'printf("Blender %s\\n", BKE_blender_version_string());',
     'printf("Narwhal 3D %s\\n", BKE_blender_version_string());', 3),
    ("source/creator/creator_args.cc",
     'printf("Blender %s (hash %s built %s %s)\\n",',
     'printf("Narwhal 3D %s (hash %s built %s %s)\\n",', 1),
    ("source/creator/creator_args.cc",
     'PRINT("Blender %s\\n", BKE_blender_version_string());',
     'PRINT("Narwhal 3D %s\\n", BKE_blender_version_string());', 1),
    ("source/creator/creator_args.cc",
     'PRINT("Usage: blender [args ...] [file] [args ...]\\n");',
     'PRINT("Usage: narwhal3d [args ...] [file] [args ...]\\n");', 1),
    ("source/blender/windowmanager/intern/wm_init_exit.cc",
     '    printf("\\nBlender quit\\n");',
     '    printf("\\nNarwhal 3D quit\\n");', 1),
)


def apply(source: str = SOURCE, icon_source: str = ICON_SOURCE) -> list[str]:
    """Apply every branding edit under *source* and return changed relative paths."""
    changed: list[str] = []
    by_path: dict[str, list[tuple[str, str, int]]] = {}
    for rel, stock, branded, count in EDITS:
        by_path.setdefault(rel, []).append((stock, branded, count))

    for rel, replacements in by_path.items():
        path = os.path.join(source, rel)
        text = open(path, encoding="utf-8").read()
        original = text
        for stock, branded, count in replacements:
            stock_count = text.count(stock)
            branded_count = text.count(branded)
            if branded_count == count and stock_count == 0:
                continue
            if branded_count != 0 or stock_count != count:
                raise RuntimeError(
                    f"{rel}: expected {count} stock or Narwhal anchor(s), found "
                    f"stock={stock_count}, Narwhal={branded_count}")
            text = text.replace(stock, branded)
        if text != original:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
            changed.append(rel)

    icon_dest = os.path.join(source, ICON_DEST)
    branded_icon = open(icon_source, "rb").read()
    current_icon = open(icon_dest, "rb").read()
    if current_icon != branded_icon:
        shutil.copyfile(icon_source, icon_dest)
        changed.append(ICON_DEST)
    return changed


def main() -> None:
    try:
        changed = apply()
    except (OSError, RuntimeError) as exc:
        sys.exit(f"NARWHAL 3D BRANDING PATCH FAILED: {exc}")
    if changed:
        for rel in changed:
            print(f"patched: {rel}")
    else:
        print("Narwhal 3D native branding already applied")


if __name__ == "__main__":
    main()
