#!/usr/bin/env python3
"""Source-pinned Linux ui_core development gate; never installs or deploys.

Python orchestrates Cargo, filesystem provenance and the existing isolated ctypes
packaging probe; editor/popup algorithms remain Rust-owned. Default: fmt, strict
Clippy, Rust tests, release build, editor ABI1/State88 + popup ABI1/all exports.
The adjacent ui-core-source.json uses the packaging format {inputs, sha256}.

--library verifies an existing binary AND its receipt against the current source;
it does not attest that Cargo/tests ran. --library --abi-only needs no source or
receipt and explicitly does NOT qualify provenance. Missing/malformed/stale source
receipts never silently fall back to ABI-only. Receipts are integrity evidence,
not signatures or portable-release/cross-platform acceptance. Focused host/GUI
suites are registered separately in run_tests.sh, not run while holding this lock.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

import build_linux_native as common

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = 'libnarwhal_ui_core.so'
DEFAULT_TARGET = Path('/var/tmp/nwug/target')
REQUIRED = ('Cargo.toml', 'Cargo.lock', 'LICENSE', 'narwhal_ui_core.h',
            'src/lib.rs', 'src/editor.rs', 'src/unicode16.rs',
            'src/popup.rs', 'src/popup_ffi.rs')


def source_inventory(root):
    crate = root / 'native/ui_core'
    for name in REQUIRED:
        if not (crate / name).is_file():
            raise ValueError(f'missing ui-core source input: {crate / name}; source NOT qualified')
    # The packaging inventory rejects file links; also refuse directory links,
    # which os.walk otherwise silently omits (or follows for its starting path).
    for path in (root / 'native', crate):
        if path.is_symlink():
            raise ValueError(f'unreviewed source symlink: {path}')
    for directory, dirs, _ in os.walk(crate):
        dirs[:] = sorted(n for n in dirs if n not in {'.git', 'target', '__pycache__'})
        for name in dirs:
            if (Path(directory) / name).is_symlink():
                raise ValueError(f'unreviewed source symlink: {Path(directory) / name}')
    return common.inventory(root, 'ui_core')


def regular(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'missing regular ui-core file: {path}')


def abi_probe(library):
    regular(library)
    return common.probe_ui_core(library)


def unchanged(root, library, receipt):
    regular(library)
    if source_inventory(root) != receipt['inputs']:
        raise ValueError('ui-core sources changed during qualification')
    if common.digest(library) != receipt['sha256']:
        raise ValueError('ui-core binary changed during qualification')


def verify(root, library):
    source_inventory(root)  # No empty/missing-source success, even with a forged receipt.
    receipt = common.verify_ui_core_source(library.parent, root, library.name)
    identity = abi_probe(library)
    unchanged(root, library, receipt)
    # Detect receipt replacement as well as source/binary drift during the probe.
    if common.verify_ui_core_source(library.parent, root, library.name) != receipt:
        raise ValueError('ui-core source receipt changed during qualification')
    return identity


@contextmanager
def build_slot():
    import fcntl  # ABI-only/receipt checks also work on non-POSIX hosts.
    with open('/tmp/narwhal3d-heavy-build.lock', 'a') as lock:
        deadline = time.monotonic() + 60
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('ui-core heavy-build slot busy; nothing launched')
                time.sleep(.25)
        available = int(next(row.split()[1] for row in Path('/proc/meminfo').read_text().splitlines()
                             if row.startswith('MemAvailable:')))
        if available < 5 * 1024**2:
            raise RuntimeError('need 4 GiB desktop reserve + 1 GiB compiler allowance')
        yield


def checked(command, root, env):
    print('==>', ' '.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), cwd=root, env=env, check=True, timeout=190)


def write_receipt(path, receipt):
    if path.is_symlink():
        raise ValueError(f'refusing symlink source receipt: {path}')
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        json.dump(receipt, output, indent=2, sort_keys=True)
        output.write('\n')
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build(root, target):
    if sys.platform != 'linux' or platform.machine() != 'x86_64':
        raise RuntimeError('host build requires Linux x86_64; use the platform release builder')
    root, target = root.resolve(), target.expanduser().resolve()
    if target == root or root in target.parents or target in root.parents:
        raise ValueError('ui-core target must be outside, and not contain, the source checkout')
    source_inventory(root)  # Refuse absent source before creating output/locking.
    with build_slot():
        before = source_inventory(root)
        target.mkdir(parents=True, exist_ok=True)
        library = target / 'release' / LIBRARY
        receipt_path = library.parent / common.UI_CORE_SOURCE
        if library.is_symlink() or receipt_path.is_symlink():
            raise ValueError('refusing symlink ui-core build output')
        # Invalidate old qualification before any tool can modify the library.
        receipt_path.unlink(missing_ok=True)
        with tempfile.TemporaryDirectory(prefix='tmp-', dir=target.parent) as temporary:
            env = dict(os.environ, CARGO_BUILD_JOBS='1', CARGO_INCREMENTAL='0',
                       CARGO_TARGET_DIR=str(target), TMPDIR=temporary)
            env.pop('CARGO_BUILD_TARGET', None)
            prefix = ['systemd-run', '--user', '--scope', '--quiet', '-p', 'MemoryMax=1G',
                      '-p', 'MemorySwapMax=0', 'timeout', '--kill-after=5s', '180s', 'cargo', '+1.95.0']
            manifest = ['--manifest-path', str(root / 'native/ui_core/Cargo.toml')]
            cargo = [*manifest, '--locked', '--offline', '--target-dir', str(target), '-j', '1']
            checked([*prefix, 'fmt', *manifest, '--check'], root, env)
            checked([*prefix, 'clippy', *cargo, '--all-targets', '--', '-D', 'warnings'], root, env)
            checked([*prefix, 'test', *cargo, '--release'], root, env)
            checked([*prefix, 'build', *cargo, '--release'], root, env)
        regular(library)
        receipt = {'inputs': before, 'sha256': common.digest(library)}
        identity = abi_probe(library)
        unchanged(root, library, receipt)
        write_receipt(receipt_path, receipt)
    return library, identity


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=ROOT)
    parser.add_argument('--target-dir', type=Path)
    parser.add_argument('--library', type=Path)
    parser.add_argument('--abi-only', action='store_true')
    args = parser.parse_args(argv)
    if args.abi_only and not args.library:
        parser.error('--abi-only requires --library')
    if args.library and args.target_dir:
        parser.error('--target-dir is only for source builds')
    root = args.source_root.expanduser().resolve()
    if args.library:
        # absolute(), not resolve(): do not conceal a symlink from validation.
        library = args.library.expanduser().absolute()
        if args.abi_only:
            before = common.digest(library)
            identity = abi_probe(library)
            if common.digest(library) != before:
                raise ValueError('ui-core binary changed during ABI probe')
            label = 'UI CORE ABI ONLY PASSED; source NOT qualified'
        else:
            identity = verify(root, library)
            label = 'UI CORE SOURCE RECEIPT + ABI VERIFIED; build/tests not rerun'
    else:
        library, identity = build(root, args.target_dir or DEFAULT_TARGET)
        label = 'UI CORE SOURCE GATE PASSED (host development only)'
    print(label, library, json.dumps(identity, sort_keys=True), flush=True)
    if not args.abi_only:
        print('Source manifest:', library.parent / common.UI_CORE_SOURCE, flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'UI CORE GATE FAILED: {error}', file=sys.stderr)
        raise SystemExit(2)
