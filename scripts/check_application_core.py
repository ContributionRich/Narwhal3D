#!/usr/bin/env python3
"""Build/qualify the document-session core; optionally stage Linux development output.

Python only orchestrates tools, ABI tests and provenance. Real platform releases
use the portable Linux/MSVC/macOS builders, not this host build. No build on import.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT / 'native/application_core'
LIBRARY = 'libnarwhal_application_core.so'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_inventory():
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(CRATE.rglob('*'))
            if p.is_file() and 'target' not in p.relative_to(CRATE).parts}


def checked(command, *, env=None, timeout=180):
    print('==>', ' '.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), cwd=ROOT, env=env, check=True, timeout=timeout)


def build(target):
    if sys.platform != 'linux':
        raise RuntimeError('use the platform native builder on Windows/macOS')
    target = target.expanduser().resolve()
    if target == ROOT or ROOT in target.parents:
        raise ValueError('application-core target must be outside the source checkout')
    before = source_inventory()
    target.mkdir(parents=True, exist_ok=True)
    with open('/tmp/narwhal3d-heavy-build.lock', 'a') as lock:
        deadline = time.monotonic() + 120
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('application-core build slot unavailable')
                time.sleep(.25)
        available = int(next(row.split()[1] for row in Path('/proc/meminfo').read_text().splitlines()
                             if row.startswith('MemAvailable:')))
        if available < 5 * 1024**2:
            raise RuntimeError('need 4 GiB desktop reserve plus 1 GiB compiler allowance')
        env = dict(os.environ, CARGO_BUILD_JOBS='1', CARGO_INCREMENTAL='0')
        prefix = ['systemd-run', '--user', '--scope', '--quiet', '-p', 'MemoryMax=1024M',
                  '-p', 'MemorySwapMax=0', 'timeout', '--kill-after=5s', '180s']
        common = ['--manifest-path', str(CRATE / 'Cargo.toml')]
        checked(prefix + ['cargo', '+1.95.0', 'fmt', *common, '--check'], env=env)
        checked(prefix + ['cargo', '+1.95.0', 'clippy', *common, '--locked', '--offline',
                          '--all-targets', '--target-dir', str(target), '--', '-D', 'warnings'], env=env)
        checked(prefix + ['cargo', '+1.95.0', 'test', *common, '--release', '--locked', '--offline',
                          '--target-dir', str(target)], env=env)
        checked(prefix + ['cargo', '+1.95.0', 'build', *common, '--release', '--locked', '--offline',
                          '--target-dir', str(target)], env=env)
    library = target / 'release' / LIBRARY
    python = ROOT / 'tools/blender-patched/5.2/python/bin/python3.13'
    if not python.is_file():
        python = Path(sys.executable)
    env = dict(os.environ, NARWHAL_APPLICATION_CORE=str(library), PYTHONDONTWRITEBYTECODE='1')
    checked([python, '-B', ROOT / 'spike/test_document_session_abi.py', '--mutation'], env=env)
    if before != source_inventory():
        raise RuntimeError('application-core sources changed during qualification')
    return library, {'source_inputs': before, 'library_sha256': digest(library), 'abi': 1,
                     'qualification': 'host Linux development; not a portable release or other-OS acceptance'}


def stage(library, receipt):
    destination = ROOT / 'artifacts/application-core/linux-x86_64' / LIBRARY
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise RuntimeError('refusing symlink development artifact')
    if destination.exists() and digest(destination) != receipt['library_sha256']:
        backup = ROOT / 'artifacts/application-core/backups' / digest(destination) / LIBRARY
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            shutil.copy2(destination, backup)
        if digest(backup) != digest(destination):
            raise RuntimeError('development artifact backup mismatch')
    if receipt['source_inputs'] != source_inventory() or digest(library) != receipt['library_sha256']:
        raise RuntimeError('qualified inputs changed before development staging')
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
        temporary = Path(output.name)
    try:
        shutil.copy2(library, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    (destination.parent / 'build.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print('Staged development application core:', destination, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target-dir', type=Path, default=Path.home() / '.cache/narwhal3d-application-core/target')
    parser.add_argument('--stage-dev', action='store_true')
    args = parser.parse_args()
    library, receipt = build(args.target_dir)
    if args.stage_dev:
        stage(library, receipt)
    print('APPLICATION CORE GATE PASSED', library, receipt['library_sha256'])


if __name__ == '__main__':
    main()
