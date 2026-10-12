#!/usr/bin/env python3
"""Build 24 px runtime icons from contributor-owned Narwhal 3D artwork.

Every manifested toolbar slot first receives a deterministic project placeholder. When
``toolbar_data.ICON_SOURCES`` names a contributed 96 px PNG, ImageMagick replaces that
placeholder with a high-quality downscale. The mapping is by visible toolbar symbol—not by
command—so repeated command variants can retain distinct artwork.
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


def _toolbar_data():
    path = ROOT / "narwhal3d" / "toolbar_data.py"
    spec = importlib.util.spec_from_file_location("narwhal_toolbar_data", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_matches():
    """Return ``(matched, missing)`` runtime filenames from the authored manifest."""
    data = _toolbar_data()
    matched = {
        runtime: source
        for runtime, source in data.ICON_SOURCES.items()
        if source and (SOURCE / source).is_file()
    }
    missing = sorted(set(data.ICON_NAMES) - set(matched))
    return dict(sorted(matched.items())), missing


def exact_matches():
    """Compatibility alias retained for focused tests and release tooling."""
    return source_matches()


def _write_placeholders():
    path = ROOT / "narwhal3d" / "icon_generator.py"
    spec = importlib.util.spec_from_file_location("narwhal_icon_generator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    wanted = set(module.icon_specs())
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for path in OUTPUT.glob("*.png"):
        if path.name not in wanted:
            path.unlink()
    module.write_icons(str(OUTPUT))


def converter():
    command = shutil.which("magick") or shutil.which("convert")
    if command is None:
        raise RuntimeError("ImageMagick 'magick' or 'convert' is required")
    return command


def install():
    matched, missing = source_matches()
    command = converter()
    _write_placeholders()
    with tempfile.TemporaryDirectory(prefix="narwhal3d-icons-") as temporary:
        temporary = Path(temporary)
        for runtime, source_name in matched.items():
            source = SOURCE / source_name
            target = temporary / runtime
            args = [command]
            if Path(command).name == "magick":
                args.append("convert")
            args.extend([
                str(source), "-filter", "Lanczos", "-resize", "24x24!",
                "-strip", str(target),
            ])
            subprocess.run(args, check=True)
            shutil.move(str(target), OUTPUT / runtime)
    return sorted(matched), missing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="report mappings without writing")
    args = parser.parse_args()
    matched, missing = source_matches() if args.list else install()
    names = sorted(matched)
    print(f"installed/exact: {len(names)}")
    print("\n".join(f"  {name}" for name in names))
    print(f"generated placeholders: {len(missing)}")
    print("\n".join(f"  {name}" for name in missing))


if __name__ == "__main__":
    main()
