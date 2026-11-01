#!/usr/bin/env python3
"""Build/qualify the required Linux document observer from hash-pinned sources.

No Blender rebuild, bundled wheel replacement, release/tag change, or Python fallback.
Compiler caches stay on disk outside the checkout; all compiler runs are bounded.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SDIST_SHA256 = '8315ec0c177ad4c69e8cc4e6d331c584110e8e8cc9bad294f121f18b4b7ec81a'
SDIST_URL = ('https://files.pythonhosted.org/packages/f6/0a/'
             'b60de8b663fb898b7a9bc7f8a3b93c5fa32733defccbafb70d0b0bad8e4b/rhino3dm-8.35.0.tar.gz')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_sources(archive, tree):
    if sha(archive) != SDIST_SHA256:
        raise RuntimeError('rhino3dm source archive hash mismatch; refusing build')
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            if not member.isfile():
                continue
            relative = Path(member.name).relative_to('rhino3dm-8.35.0')
            if not any(relative.is_relative_to(prefix) for prefix in (
                    'src/bindings', 'src/lib/opennurbs', 'src/lib/pybind11')):
                continue
            path = tree / relative
            expected = hashlib.sha256(source.extractfile(member).read()).hexdigest()
            if not path.is_file() or sha(path) != expected:
                raise RuntimeError(f'modified/missing observer dependency: {relative}')


def run(command, seconds=180, heavy=True):
    prefix = ['systemd-run', '--user', '--scope', '--quiet', '-p', 'MemoryMax=1024M',
              '-p', 'MemorySwapMax=0', 'timeout', '--kill-after=5s', f'{seconds}s']
    if heavy:
        prefix = ['flock', '-w', '120', '/tmp/narwhal3d-heavy-build.lock'] + prefix
    env = dict(os.environ, CARGO_BUILD_JOBS='1', CMAKE_BUILD_PARALLEL_LEVEL='1')
    print('==>', ' '.join(map(str, command)), flush=True)
    subprocess.run([*prefix, *map(str, command)], cwd=ROOT, env=env, check=True, timeout=seconds + 140)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir', type=Path,
                        default=Path(os.environ.get('NARWHAL_OBSERVER_WORK', '/var/tmp/nw-document-observer')))
    parser.add_argument('--python', type=Path, default=ROOT / 'tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13')
    parser.add_argument('--python-include', type=Path,
                        default=Path(os.environ.get('NARWHAL_PYTHON_INCLUDE',
                            str(ROOT / 'tools/blender-src/lib/linux_x64/python/include/python3.13'))))
    parser.add_argument('--stage-dev', action='store_true')
    parser.add_argument('--mutations', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'linux' or platform.machine().lower() != 'x86_64':
        raise RuntimeError('document observer runtime qualification currently covers Linux x86_64 only')
    available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                         if line.startswith('MemAvailable:')))
    if available < 5 * 1024**2:
        raise RuntimeError('need 4 GiB desktop reserve plus a 1 GiB compiler allowance')
    if not args.python.is_file() or not (args.python_include / 'Python.h').is_file():
        raise RuntimeError('matching bundled Python and CPython3.13 SDK headers required; use --python/--python-include')
    work = args.work_dir.resolve()
    if work.is_relative_to(ROOT):
        raise RuntimeError('observer dependency/cache directory must be outside the project')
    work.mkdir(parents=True, exist_ok=True)
    archive = work / 'rhino3dm-8.35.0.tar.gz'
    if not archive.is_file():
        download = archive.with_suffix('.download')
        deadline = time.monotonic() + 120
        with urllib.request.urlopen(SDIST_URL, timeout=20) as response, download.open('wb') as output:
            total = 0
            while block := response.read(1024 * 1024):
                if time.monotonic() > deadline:
                    raise TimeoutError('source download deadline exceeded')
                total += len(block)
                if total > 80 * 1024 * 1024:
                    raise RuntimeError('source archive exceeds pinned download bound')
                output.write(block)
        if sha(download) != SDIST_SHA256:
            raise RuntimeError('downloaded source hash mismatch')
        download.replace(archive)
    if sha(archive) != SDIST_SHA256:
        raise RuntimeError('cached source archive hash mismatch')
    tree = work / 'rhino3dm-8.35.0'
    if not tree.exists():
        with tarfile.open(archive) as source:
            source.extractall(work, filter='data')
    verify_sources(archive, tree)
    build = work / 'on-observe-pic'
    run(['cmake', '-S', tree / 'src/lib/opennurbs', '-B', build,
         '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_POSITION_INDEPENDENT_CODE=ON',
         '-DCMAKE_CXX_FLAGS_RELEASE=-O2', '-DCMAKE_C_FLAGS_RELEASE=-O2', '-DCMAKE_POLICY_VERSION_MINIMUM=3.5'])
    run(['cmake', '--build', build, '--target', 'opennurbsStatic', '--parallel', '1'], seconds=900)
    target = Path(os.environ.get('NARWHAL_OBSERVER_PROBE_TARGET', str(work / 'target'))).resolve()
    run(['cargo', '+1.95.0', 'build', '--manifest-path', ROOT / 'native/application_core/Cargo.toml',
         '--locked', '--offline', '--target-dir', target, '--example', 'observation_check'])
    command = [sys.executable, '-B', ROOT / 'native/document_observer/qualify.py',
               '--sdist', archive, '--source-tree', tree, '--pic-build', build,
               '--python', args.python.resolve(), '--python-include', args.python_include.resolve(),
               '--rust-check', target / 'debug/examples/observation_check',
               '--core', ROOT / 'artifacts/application-core/linux-x86_64/libnarwhal_application_core.so',
               '--output-root', work / 'observer-qualification']
    if args.stage_dev:
        command.append('--stage-dev')
    if args.mutations:
        command.append('--mutations')
    # qualify.py locks each compiler itself; never hold the heavy lock around it.
    subprocess.run(list(map(str, command)), cwd=ROOT, check=True, timeout=1800)
    print('DOCUMENT_OBSERVER_GATE_PASSED', flush=True)


if __name__ == '__main__':
    main()
