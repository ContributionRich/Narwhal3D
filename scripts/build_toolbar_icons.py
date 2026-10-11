#!/usr/bin/env python3
"""Build active 24px toolbar icons from the licensed source artwork.

The importer maps exact source stems plus a small reviewed alias table to active command
keys. It never installs icons for commands that are not currently exposed or guesses
ambiguous mappings. ImageMagick performs the high-quality RGBA downscale.
"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "artwork" / "icons"
OUTPUT = ROOT / "narwhal3d" / "icons"
ALIASES = {
    "grid": "grid_on",
    "pointsoff": "pointsoff_rt",
    "rectangle": "rectangle_corner",
    "redo": "redo_rt",
    "rotate3d": "rotate3d_rt",
}


def _toolbar_data():
    path = ROOT / "narwhal3d" / "toolbar_data.py"
    spec = importlib.util.spec_from_file_location("narwhal_toolbar_data", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def active_commands():
    data = _toolbar_data()
    return {
        button["command"]
        for tab in data.TABS
        for button in tab["buttons"]
        if button.get("command")
    }


def exact_matches():
    available = {path.stem for path in SOURCE.glob("*.png") if path.is_file()}
    active = active_commands()
    matched = {
        name for name in active
        if name in available or ALIASES.get(name) in available
    }
    return sorted(matched), sorted(active - matched)


def converter():
    command = shutil.which("magick") or shutil.which("convert")
    if command is None:
        raise RuntimeError("ImageMagick 'magick' or 'convert' is required")
    return command


def install():
    matched, missing = exact_matches()
    command = converter()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="narwhal3d-icons-") as temporary:
        temporary = Path(temporary)
        for name in matched:
            source = SOURCE / f"{ALIASES.get(name, name)}.png"
            target = temporary / f"narwhal-{name}.png"
            args = [command]
            if Path(command).name == "magick":
                args.append("convert")
            args.extend([
                str(source), "-filter", "Lanczos", "-resize", "24x24!",
                "-strip", str(target),
            ])
            subprocess.run(args, check=True)
            shutil.move(str(target), OUTPUT / target.name)
    return matched, missing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="report exact matches without writing")
    args = parser.parse_args()
    if args.list:
        matched, missing = exact_matches()
    else:
        matched, missing = install()
    print(f"installed/exact: {len(matched)}")
    print("\n".join(f"  {name}" for name in matched))
    print(f"missing active: {len(missing)}")
    print("\n".join(f"  {name}" for name in missing))


if __name__ == "__main__":
    main()
