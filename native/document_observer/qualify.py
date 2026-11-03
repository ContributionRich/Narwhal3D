#!/usr/bin/env python3
"""Observer build/mutation qualification; --stage-dev installs a qualified local pair.

Python orchestrates the external compiler and host tests; no geometry algorithms.
The caller supplies the hash-verified rhino3dm sdist tree. Linux builds against PIC
openNURBS archives with g++; Windows builds against MSVC /MD openNURBS and zlib
libraries inside a vcvars64 environment. Both run the same mutations and RED/GREEN rules.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sys
from pathlib import Path
import subprocess
import tempfile
import tarfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).with_name('observer.cpp')
TEST = ROOT / 'spike/test_document_registry_observer.py'
SDIST_SHA256 = '8315ec0c177ad4c69e8cc4e6d331c584110e8e8cc9bad294f121f18b4b7ec81a'
WINDOWS = sys.platform == 'win32'
LINUX_LIBRARY = '_narwhal_document_observer.so'
WINDOWS_LIBRARY = '_narwhal_document_observer.pyd'
MUTATIONS = (
    ('omit-geometry', 'archive(output, *geometry, raw);', 'u32(output, 0);',
     'test_same_uuid_same_count_raw_replacement_detected'),
    ('omit-attributes', 'archive(output, *attributes, raw);', 'u32(output, 0);',
     'test_attributes_user_text_groups_and_plot_weight_roundtrip'),
    ('omit-name', 'blob(output, name.Array(), name_length);', 'u32(output, 0);',
     'test_unicode_name'),
    ('wrong-identity', 'append(output, id, 36);',
     'append(output, "00000000-0000-0000-0000-000000000001", 36);',
     'test_same_uuid_same_count_raw_replacement_detected'),
    ('lazy-solidity', 'if (auto* brep = dynamic_cast<ON_Brep*>(copy.get())) (void)brep->IsSolid();',
     '(void)copy;', 'test_lazy_open_brep_solidity_query_is_not_a_document_edit'),
    ('lazy-bounds', 'if (auto* brep = dynamic_cast<ON_Brep*>(copy.get())) (void)brep->BoundingBox();',
     '(void)copy;', 'test_lazy_brep_bounding_box_query_is_not_a_document_edit'),
    ('userdata-copy', 'target_attributes->MoveUserData(saved_userdata);', '(void)saved_userdata;',
     'test_complete_attribute_copy_is_detached'),
    ('copy-counter-witness', 'if (chunk.m_typecode == TCODE_OPENNURBS_CLASS_USERDATA_HEADER)', 'if (false)',
     'test_userdata_copy_counters_are_not_geometry_changes'),
    ('crc-witness', 'if (crc && chunk.m_end_offset >= crc)', 'if (false)',
     'test_userdata_copy_counters_are_not_geometry_changes'),
    ('name-limit', 'if (name_length > max_name)', 'if (false)',
     'test_oversized_name_refuses'),
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def execute(command, *, timeout, log):
    # Each compiler process is bounded; no shared production source is mutated.
    with log.open('w') as output:
        result = subprocess.run(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                                timeout=timeout, check=False)
    return result.returncode


def platform_tag():
    system = 'windows' if sys.platform == 'win32' else sys.platform
    machine = platform.machine().lower()
    return f"{system}-{'x86_64' if machine == 'amd64' else machine}"


def bounded(seconds, command):
    """Linux: memory-capped systemd scope plus timeout. Windows: execute()'s subprocess timeout bounds it."""
    if WINDOWS:
        return list(command)
    return ['systemd-run', '--user', '--scope', '--quiet', '-p', 'MemoryMax=1024M', '-p', 'MemorySwapMax=0',
            'timeout', '--kill-after=5s', f'{seconds}s', *command]


def linux_compile_command(candidate_source, library, *, python_include, source_tree, archives):
    return [
        'flock', '-w', '120', '/tmp/narwhal3d-heavy-build.lock', 'systemd-run', '--user', '--scope', '--quiet',
        '-p', 'MemoryMax=1024M', '-p', 'MemorySwapMax=0', 'timeout', '--kill-after=5s', '120s',
        'g++', '-std=c++17', '-O2', '-fPIC', '-shared', '-fvisibility=hidden',
        '-Wl,--exclude-libs,ALL', '-I' + str(python_include.resolve()),
        '-I' + str((source_tree / 'src/bindings').resolve()),
        '-I' + str((source_tree / 'src/lib/pybind11/include').resolve()),
        str(candidate_source.resolve()), '-Wl,--start-group',
        *(str(path.resolve()) for path in archives), '-Wl,--end-group', '-lpthread',
        '-o', str(library.resolve())]


def windows_compile_command(candidate_source, library, *, vcvars, python_include, source_tree,
                            opennurbs_lib_dir, python_lib):
    """One cmd.exe line: vcvars64 environment, then an MSVC /MD build of one variant.

    A string, not an argv list: cmd.exe must see the && operator, which list quoting would escape.
    Paths are used as given; the caller resolves them.
    """
    flags = ['/nologo', '/LD', '/MD', '/O2', '/EHsc', '/std:c++17', '/bigobj', '/DNOMINMAX', '/DUNICODE', '/D_UNICODE']
    includes = [python_include, source_tree / 'src/bindings', source_tree / 'src/lib/pybind11/include',
                source_tree / 'src/lib/opennurbs']
    libraries = [opennurbs_lib_dir / 'opennurbsStatic.lib', opennurbs_lib_dir / 'zlib.lib', python_lib]
    system_libraries = ['shlwapi.lib', 'usp10.lib', 'shell32.lib', 'ole32.lib', 'rpcrt4.lib',
                        'advapi32.lib', 'user32.lib', 'gdi32.lib']
    command = ['cmd /d /c call', f'"{vcvars}"', '&&', 'cl', *flags,
               *(f'"/I{path}"' for path in includes),
               f'"{candidate_source}"', f'"/Fo{library.with_suffix(".obj")}"',
               '/link', f'"/OUT:{library}"', *(f'"{path}"' for path in libraries), *system_libraries]
    return ' '.join(command)


def link_inputs(args):
    """Return (archives, needed): the link inputs recorded as pic_inputs, and every file needed before compiling."""
    if WINDOWS:
        archives = [args.opennurbs_lib_dir / 'opennurbsStatic.lib', args.opennurbs_lib_dir / 'zlib.lib',
                    args.python_lib]
    else:
        archives = [args.pic_build / item for item in (
            'libopennurbsStatic.a', 'android_uuid/libandroid_uuid.a',
            'freetype263/libopennurbs_public_freetype.a', 'zlib/libzlib.a')]
    needed = archives + [args.python_include / 'Python.h', args.source_tree / 'src/bindings/bindings.h']
    if WINDOWS:
        needed.append(args.vcvars)
    return archives, needed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-tree', required=True, type=Path)
    parser.add_argument('--sdist', required=True, type=Path)
    parser.add_argument('--pic-build', type=Path, help='Linux: PIC openNURBS build directory')
    parser.add_argument('--vcvars', type=Path, help='Windows: vcvars64.bat')
    parser.add_argument('--opennurbs-lib-dir', type=Path,
                        help='Windows: directory with opennurbsStatic.lib and zlib.lib built /MD')
    parser.add_argument('--python-lib', type=Path, help='Windows: python313.lib')
    parser.add_argument('--python', required=True, type=Path)
    parser.add_argument('--rust-check', required=True, type=Path)
    parser.add_argument('--python-include', required=True, type=Path)
    parser.add_argument('--output-root', required=True, type=Path)
    parser.add_argument('--mutations', action='store_true')
    parser.add_argument('--core', required=True, type=Path)
    parser.add_argument('--stage-dev', action='store_true')
    args = parser.parse_args()
    if WINDOWS:
        if args.pic_build is not None or any(item is None for item in (
                args.vcvars, args.opennurbs_lib_dir, args.python_lib)):
            parser.error('Windows qualification requires --vcvars, --opennurbs-lib-dir and --python-lib, not --pic-build')
    elif args.pic_build is None:
        parser.error('Linux qualification requires --pic-build')
    if digest(args.sdist) != SDIST_SHA256:
        raise RuntimeError('rhino3dm source distribution hash mismatch')
    # Compare all upstream files we compile/include, not just the version string.
    inputs = {}
    with tarfile.open(args.sdist) as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            relative = Path(member.name).relative_to('rhino3dm-8.35.0')
            if not any(relative.is_relative_to(prefix) for prefix in (
                    'src/bindings', 'src/lib/opennurbs', 'src/lib/pybind11')):
                continue
            path = args.source_tree / relative
            expected = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
            if not path.is_file() or digest(path) != expected:
                raise RuntimeError(f'upstream source mismatch: {relative}')
            inputs[str(relative)] = expected
    if not inputs:
        raise RuntimeError('empty upstream source inventory')
    args.output_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='qualify-', dir=args.output_root))
    host_probe = subprocess.run(
        [str(args.python.resolve()), '-B', '-c',
         'import json,sys,sysconfig,rhino3dm,rhino3dm._rhino3dm as n; '
         'print(json.dumps(dict(version=rhino3dm.__version__,extension=n.__file__, '
         'cache_tag=sys.implementation.cache_tag,soabi=sysconfig.get_config_var("SOABI"))))'],
        capture_output=True, text=True, check=True, timeout=15)
    host = json.loads(host_probe.stdout)
    if host['version'] != '8.35.0':
        raise RuntimeError('candidate requires rhino3dm 8.35.0')
    host['sha256'] = digest(Path(host['extension']))
    frozen_paths = [SOURCE, TEST, args.core,
                    ROOT / 'narwhal3d/native_object_registry.py',
                    ROOT / 'narwhal3d/native_document_observer.py',
                    ROOT / 'spike/test_document_registry_observed_abi.py',
                    ROOT / 'spike/test_document_registry_observer_loader.py']
    frozen = {str(p): digest(p) for p in frozen_paths}
    source = SOURCE.read_text()
    archives, needed = link_inputs(args)
    for item in needed:
        if not item.is_file():
            raise RuntimeError(f'missing build input: {item}')
    entries = []
    variants = [('control', source, None)]
    if args.mutations:
        for label, old, new, case in MUTATIONS:
            if source.count(old) != 1:
                raise RuntimeError(f'nonunique mutation anchor: {label}')
            variants.append((label, source.replace(old, new), case))
        variants.append(('restored', source, None))
    if not WINDOWS and Path('/proc/meminfo').exists():
        available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                             if line.startswith('MemAvailable:')))
        if available < 5 * 1024**2:
            raise RuntimeError('need 4 GiB desktop reserve plus compiler allowance')
    for label, text, case in variants:
        directory = work / label
        directory.mkdir()
        candidate_source = directory / 'observer.cpp'
        candidate_source.write_text(text)
        library = directory / (WINDOWS_LIBRARY if WINDOWS else LINUX_LIBRARY)
        if WINDOWS:
            compile_command = windows_compile_command(
                candidate_source.resolve(), library.resolve(), vcvars=args.vcvars.resolve(),
                python_include=args.python_include.resolve(), source_tree=args.source_tree.resolve(),
                opennurbs_lib_dir=args.opennurbs_lib_dir.resolve(), python_lib=args.python_lib.resolve())
        else:
            compile_command = linux_compile_command(
                candidate_source, library, python_include=args.python_include,
                source_tree=args.source_tree, archives=archives)
        if execute(compile_command, timeout=260, log=directory / 'build.log'):
            raise RuntimeError(f'build failed, not a mutation kill: {directory}')
        test_command = bounded(30, [str(args.python.resolve()), '-B', str(TEST), '--library', str(library.resolve()),
                                    '--rust-check', str(args.rust_check.resolve())])
        if case:
            test_command += ['--case', case]
        code = execute(test_command, timeout=40, log=directory / 'test.log')
        log = (directory / 'test.log').read_text()
        if case:
            if code != 1 or 'FAIL:' not in log or 'ERROR:' in log or 'AssertionError' not in log:
                raise RuntimeError(f'expected assertion RED, not crash/infrastructure error: {directory}')
            status = 'assertion RED'
        else:
            if code != 0 or '\nOK\n' not in log:
                raise RuntimeError(f'control failed: {directory}')
            status = 'GREEN'
        entries.append(dict(label=label, status=status, library=str(library), sha256=digest(library)))
        print(label, status, flush=True)
    if SOURCE.read_text() != source:
        raise RuntimeError('production observer source changed during qualification')
    receipt = dict(scope='observer component qualification; not live model/registry integration',
                   upstream_sdist_sha256=SDIST_SHA256, upstream_inputs=inputs, host=host,
                   source_sha256=digest(SOURCE), test_sha256=digest(TEST), entries=entries,
                   pic_inputs={str(p): digest(p) for p in archives},
                   source_tree=str(args.source_tree), rust_check_sha256=digest(args.rust_check), pid=os.getpid())
    path = work / 'receipt.json'
    path.write_text(json.dumps(receipt, indent=2) + '\n')
    library = Path(entries[-1]['library'])
    checks = (
        ('loader', ROOT / 'spike/test_document_registry_observer_loader.py',
         ['--library', str(library), '--receipt', str(path), '--mutations']),
        ('registry', ROOT / 'spike/test_document_registry_observed_abi.py',
         ['--core', str(args.core.resolve()), '--observer', str(library), '--receipt', str(path), '--mutations']),
    )
    receipt['integration'] = {}
    for label, test, options in checks:
        command = bounded(45, [str(args.python.resolve()), '-B', str(test), *options])
        if execute(command, timeout=55, log=work / f'{label}.log'):
            raise RuntimeError(f'{label} qualification failed: {work}')
        receipt['integration'][label] = dict(status='GREEN', test_sha256=digest(test))
    if any(digest(p) != frozen[str(p)] for p in frozen_paths):
        raise RuntimeError('qualified input changed during observer integration checks')
    receipt['qualified_inputs'] = frozen
    receipt['integration']['core_sha256'] = digest(args.core)
    receipt['integration']['adapter_sha256'] = digest(ROOT / 'narwhal3d/native_object_registry.py')
    receipt['integration']['loader_sha256'] = digest(ROOT / 'narwhal3d/native_document_observer.py')
    path.write_text(json.dumps(receipt, indent=2) + '\n')
    if args.stage_dev:
        tag = platform_tag()
        if tag not in ('linux-x86_64', 'windows-x86_64'):
            raise RuntimeError('only Linux x86_64 and Windows x86_64 pairs have runtime qualification')
        if digest(SOURCE) != receipt['source_sha256'] or digest(TEST) != receipt['test_sha256']:
            raise RuntimeError('observer inputs changed before staging')
        destination = ROOT / 'artifacts/document-observer' / tag
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.stage-', dir=destination.parent))
        shutil.copy2(library, staging / library.name)
        shutil.copy2(path, staging / 'build.json')
        if digest(staging / library.name) != entries[-1]['sha256']:
            raise RuntimeError('staged observer hash mismatch')
        backup = None
        if destination.exists():
            backup = Path(tempfile.mkdtemp(prefix='preserved-', dir=destination.parent))
            backup.rmdir()
            destination.rename(backup)
        try:
            staging.rename(destination)
        except BaseException:
            if backup is not None and not destination.exists():
                backup.rename(destination)
            raise
        print('STAGED_OBSERVER', destination)
    print('OBSERVER_RECEIPT', path)


if __name__ == '__main__':
    main()
