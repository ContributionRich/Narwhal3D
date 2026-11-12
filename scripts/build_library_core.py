#!/usr/bin/env python3
"""Build the native Library authority; --stage installs the platform helper."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', action='store_true')
    args = parser.parse_args()
    crate = ROOT / 'native/library_core/Cargo.toml'
    subprocess.run(['cargo', '+1.95.0', 'build', '--release', '--locked',
                    '--manifest-path', str(crate)], check=True, cwd=ROOT, timeout=600)
    name = 'narwhal-library-core.exe' if sys.platform == 'win32' else 'narwhal-library-core'
    source = crate.parent / 'target/release' / name
    if args.stage:
        platform = 'windows-x86_64' if sys.platform == 'win32' else 'linux-x86_64'
        dest = ROOT / 'runtime' / platform / 'bin' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        temporary = dest.with_suffix('.new')
        shutil.copy2(source, temporary)
        temporary.replace(dest)
        print(dest)
    else:
        print(source)


if __name__ == '__main__':
    main()
