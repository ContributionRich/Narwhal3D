"""Apply Narwhal 3D's minimal native identity patch to the bundled runtime source.

Only presentation surfaces are changed: window titles, Linux application identity,
normal console banners, quit text, and native X11/Wayland/Windows/macOS icons. File formats, version
numbers, internal APIs, and runtime behavior remain untouched.

Run from the project root: ``python3 blender_patches/apply_narwhal3d_branding.py``.
The patch is idempotent and asserts every stock source anchor before changing it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "tools", "blender-src")
ICON_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "narwhal3d_app_icon.svg")
ICON_DEST = os.path.join("release", "freedesktop", "icons", "scalable", "apps",
                         "blender.svg")

WINDOW_ICON_BUNDLE = "narwhal3d_window_icons.json"
WINDOW_ICON_DESTS = {
    "x11": "intern/ghost/intern/GHOST_IconX11.hh",
    "windows": "release/windows/icons/winblender.ico",
    "macos": "release/darwin/Blender.app/Contents/Resources/blender_icon_legacy.icns",
}


def window_icons(icon_source: str = ICON_SOURCE) -> dict[str, bytes]:
    """Decode only fixed native destinations from the provenance-bound bundle."""
    path = os.path.join(os.path.dirname(icon_source), WINDOW_ICON_BUNDLE)
    if os.path.islink(path) or os.path.getsize(path) > 4 * 1024 * 1024:
        raise RuntimeError("native window icon bundle is symlinked or oversized")
    with open(path, encoding="ascii") as handle:
        bundle = json.load(handle)
    if bundle.get("schema") != 1 or set(bundle.get("icons", {})) != set(WINDOW_ICON_DESTS):
        raise RuntimeError("native window icon bundle must contain X11, Windows and macOS")
    result = {}
    for name, destination in WINDOW_ICON_DESTS.items():
        record = bundle["icons"][name]
        data = base64.b64decode(record["base64"], validate=True)
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise RuntimeError(f"native {name} icon digest mismatch")
        if name == "x11" and b"static const unsigned long BLENDER_ICONS_WM_X11[]" not in data:
            raise RuntimeError("native X11 icon has no property array")
        if name == "windows" and not data.startswith(b"\x00\x00\x01\x00"):
            raise RuntimeError("native Windows icon is not ICO")
        if name == "macos" and (len(data) < 8 or data[:4] != b"icns"
                                or struct.unpack(">I", data[4:8])[0] != len(data)):
            raise RuntimeError("native macOS icon is not ICNS")
        result[destination] = data
    return result


# (relative source path, stock text, Narwhal text, expected count)
EDITS = (
    ("release/darwin/Blender.app/Contents/Info.plist",
     '\t<!-- Modern Liquid Glass icon, the app bundle Asset.car file needs to be regenerated in Xcode on modification -->\n'
     '\t<key>CFBundleIconName</key>\n\t<string>blender_liquid_glass</string>',
     '\t<!-- Narwhal uses its bundled ICNS, not the upstream Asset.car icon. -->', 1),
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
    native_icons = window_icons(icon_source)
    # Refuse an incomplete checkout before applying any source edits.
    for relative in native_icons:
        path = os.path.join(source, relative)
        if not os.path.isfile(path) or os.path.islink(path):
            raise RuntimeError(f"missing regular native icon destination: {relative}")
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
    for relative, data in native_icons.items():
        path = os.path.join(source, relative)
        with open(path, "rb") as handle:
            current = handle.read()
        if current != data:
            with open(path, "wb") as handle:
                handle.write(data)
            changed.append(relative)
    return changed


def main() -> None:
    try:
        changed = apply()
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        sys.exit(f"NARWHAL 3D BRANDING PATCH FAILED: {exc}")
    if changed:
        for rel in changed:
            print(f"patched: {rel}")
    else:
        print("Narwhal 3D native branding already applied")


if __name__ == "__main__":
    main()
