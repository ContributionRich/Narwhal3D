#!/usr/bin/env python3
"""Qualify and atomically activate the additive clipping ABI in this checkout.

No release/package publication. Only the four named selection libraries may be
replaced. Every replaced file is backed up; failures roll back our own bytes only.
Builds are isolated, locked/offline, source-accounted, and repeated for both OSes.
"""
from __future__ import annotations
import argparse
import ast
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

CRATE = Path(__file__).resolve().parent
ROOT = CRATE.parents[1]
SYMBOL = 'narwhal_nearest_projected_polylines_clipped_f64'
sys.path.insert(0, str(ROOT))
from scripts import verify_selection_core as verify
from scripts.build_selection_core import write_staging

# The normal verifier must recognize this ABI; do not silently patch its
# export contract in memory. Public manifests must pass the standalone verifier.
assert SYMBOL in verify.EXPECTED_EXPORTS, "selection verifier lacks the clipping ABI"
PUBLIC_SOURCE_PATHS = ("native/selection_core/qualify_snap_activation.py",
                       "native/selection_core/src/snap_clip.rs")
TARGETS = {
    'linux-x86_64': ('x86_64-unknown-linux-gnu', 'libnarwhal_selection_core.so'),
    'windows-x86_64': ('x86_64-pc-windows-gnu', 'narwhal_selection_core.dll'),
}

PROBE = r'''
import ctypes as c, importlib, json, os, pathlib, sys, types
root = pathlib.Path(sys.argv[1])
expected = pathlib.Path(sys.argv[2])
os.environ.pop('NARWHAL_SELECTION_CORE', None)
sys.dont_write_bytecode = True
pkg = types.ModuleType('narwhal3d')
pkg.__path__ = [str(root / 'narwhal3d')]
sys.modules['narwhal3d'] = pkg
adapter = importlib.import_module('narwhal3d.native_selection')
binding = adapter._binding()
assert binding is not None, adapter.backend_status()
lib, *_, path = binding
assert pathlib.Path(path).resolve() == expected.resolve(), (path, expected)
assert pathlib.Path(adapter._PACKAGED_LIBRARY).resolve() == expected.resolve()
assert lib.narwhal_selection_core_abi_version() == 1
f = lib.narwhal_nearest_projected_polylines_clipped_f64
D=c.POINTER(c.c_double); U=c.POINTER(c.c_uint64); B=c.POINTER(c.c_ubyte)
f.argtypes=[D,D,c.c_size_t,U,c.c_size_t,c.c_double,c.c_double,D,U,D,D,B]
f.restype=c.c_int
m=(c.c_double*16)(1,0,0,0,0,1,0,0,0,0,1,-2,0,0,1,0)
offsets=(c.c_uint64*2)(0,2); mouse=(c.c_double*2)(400,300)
for points,want in [([0,-1,-1,0,1,3],.5),([-1,0,1,2,0,4],1/3),([-100,0,2,100,0,2],.5)]:
    index=c.c_uint64();t=c.c_double();distance=c.c_double();valid=c.c_ubyte()
    code=f(m,(c.c_double*6)(*points),2,offsets,1,800,600,mouse,c.byref(index),c.byref(t),c.byref(distance),c.byref(valid))
    assert code==0 and index.value==0 and valid.value==1 and abs(t.value-want)<1e-12 and distance.value<1e-10
print(json.dumps({'platform':sys.platform,'python':sys.version,'loaded_path':str(path),'symbol':f.__name__,'checks_passed':3}))
'''


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def wine_path(path):
    return 'Z:' + str(Path(path).resolve()).replace('/', '\\')


def snapshot(path):
    if path.is_symlink():
        raise RuntimeError(f'refuse symlink activation path: {path}')
    if not path.exists():
        return None
    stat = path.stat()
    return {'sha256': sha(path), 'size': stat.st_size, 'inode': stat.st_ino,
            'device': stat.st_dev, 'mode': stat.st_mode & 0o777}


def atomic_copy(source, destination):
    if destination.parent.is_symlink() or destination.is_symlink():
        raise RuntimeError(f'refuse symlink destination: {destination}')
    fd, name = tempfile.mkstemp(prefix='.selection-activate-', dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream, open(source, 'rb') as src:
            shutil.copyfileobj(src, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, Path(source).stat().st_mode & 0o777)
        if sha(temporary) != sha(source):
            raise RuntimeError('atomic copy hash mismatch')
        os.replace(temporary, destination)
        directory = os.open(destination.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def publication_inventory():
    """Read the harness-owned source list without importing or editing it."""
    manifest = ROOT / 'scripts/create_public_branch.py'
    tree = ast.parse(manifest.read_text())
    native = next(ast.literal_eval(node.value) for node in tree.body
                  if isinstance(node, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == 'NATIVE_FILES' for t in node.targets))
    return {'manifest': str(manifest.relative_to(ROOT)), 'manifest_sha256': sha(manifest),
            'required_paths': list(PUBLIC_SOURCE_PATHS),
            'missing': [p for p in PUBLIC_SOURCE_PATHS if p not in native]}


def require_publication_inventory():
    inventory = publication_inventory()
    if inventory['missing']:
        raise RuntimeError('selection source manifest is incomplete: ' + ', '.join(inventory['missing']))
    return inventory


def reusable_builds(receipt_path, inputs):
    """Reuse binaries only after exact native-build inventory + artifact checks.

    GUI/qualifier changes require fresh runtime qualification, not recompilation
    of byte-identical Rust inputs. The new receipt accounts for those separately.
    """
    receipt_path = receipt_path.resolve(strict=True)
    original = json.loads(receipt_path.read_text())
    assert original['status'] == 'passed'
    assert original['source_files'] == inputs, 'reuse rejected: native build inventory differs'
    assert original['source_aggregate_sha256'] == verify.source_aggregate(inputs)
    assert original['exact_expected_exports'] == list(verify.EXPECTED_EXPORTS)
    origin = receipt_path.parent
    for entry in original['commands']:
        assert entry['returncode'] == 0
        assert sha(origin / entry['log']) == entry['log_sha256'], 'reuse qualification log changed'
    assert sha(ROOT / 'scripts/build_selection_core.py') == original['additional_inputs']['scripts/build_selection_core.py']
    assert sha(ROOT / 'narwhal3d/native_selection.py') == original['additional_inputs']['narwhal3d/native_selection.py']
    binaries = []
    for number in (1, 2):
        stage = origin / f'qualified-{number}'
        manifest = verify.verify_directory(stage)
        assert manifest['source_files'] == inputs
        measured = {r['platform']: r['sha256'] for r in manifest['artifacts']}
        assert measured == original['reproducible_hashes']
        for platform, (target, _name) in TARGETS.items():
            command = next(e for e in original['commands'] if e['label'] == f'build-{number}-{platform}')
            assert all(flag in command['command'] for flag in ('+1.95.0', '--release', '--locked', '--offline', target))
        binaries.append({platform: stage / platform / name for platform, (_target, name) in TARGETS.items()})
    test_command = next(e['command'] for e in original['commands'] if e['label'] == 'linux-rust-tests')
    tests = Path(test_command[test_command.index('--target-dir') + 1])
    proof = {'receipt': str(receipt_path), 'receipt_sha256': sha(receipt_path),
             'native_source_files_match': True, 'native_source_aggregate_sha256': verify.source_aggregate(inputs),
             'build_script_and_adapter_match': True, 'exact_export_contract_match': True,
             'both_original_clean_build_manifests_reverified': True,
             'fresh_public_runtime_qualification_required': True}
    return binaries, tests, proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--activate', action='store_true', required=True)
    parser.add_argument('--reuse-qualified', type=Path, help='passed source-bound receipt; exact native inventory required')
    parser.add_argument('--windows-python', type=Path, help='Windows CPython executable for Wine adapter probes')
    parser.add_argument('--gui-host', type=Path, default=Path('/var/tmp/workspace_dialogs_host/host.py'))
    args = parser.parse_args()
    output = Path(tempfile.mkdtemp(prefix='snap-activation-', dir=CRATE / 'target'))
    receipt = {'schema': 1, 'status': 'running', 'started_unix': time.time(),
               'root': str(ROOT), 'symbol': SYMBOL, 'commands': [], 'installed': [],
               'scope': 'this checkout selection libraries/manifests only; no public commit or release'}
    path = output / 'receipt.json'
    print('SELECTION_ACTIVATION_OUTPUT', output, flush=True)

    def save():
        temporary = output / 'receipt.json.pending'
        temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
        os.replace(temporary, path)

    def run(label, command, *, cwd=ROOT, env=None, timeout=120):
        log = output / (str(len(receipt['commands'])).zfill(2) + '-' + label + '.log')
        started = time.time()
        result = subprocess.run([str(c) for c in command], cwd=cwd, env=env,
                                capture_output=True, text=True, errors='replace', timeout=timeout)
        log.write_text(result.stdout + result.stderr)
        receipt['commands'].append({'label': label, 'command': [str(c) for c in command],
                                    'cwd': str(cwd), 'returncode': result.returncode,
                                    'log': str(log.relative_to(output)), 'log_sha256': sha(log),
                                    'elapsed_s': time.time() - started})
        save()
        print('SELECTION_ACTIVATION', label, 'PASS' if result.returncode == 0 else 'FAIL', flush=True)
        if result.returncode:
            print(log.read_text(), flush=True)
            raise RuntimeError(f'{label} failed with {result.returncode}')
        return result.stdout

    env = dict(os.environ, CARGO_INCREMENTAL='0', LC_ALL='C', TZ='UTC', SOURCE_DATE_EPOCH='0',
               PYTHONDONTWRITEBYTECODE='1', WINEDEBUG='-all', WINEPREFIX=str(output / 'wineprefix'))
    env.pop('NARWHAL_SELECTION_CORE', None)
    inputs = verify.source_inventory()
    assert {'src/lib.rs', 'src/snap_clip.rs'} <= {r['path'] for r in inputs}, 'clipping Rust module missing from source manifest'
    extra_paths = [Path(__file__), ROOT / 'narwhal3d/native_selection.py',
                   ROOT / 'scripts/verify_selection_core.py', ROOT / 'scripts/build_selection_core.py',
                   ROOT / 'spike/snap_clip_abi_witness.c', ROOT / 'spike/test_report121_124_gui.py',
                   ROOT / 'spike/test_report121_124.py', ROOT / 'spike/test_extrusion_creases.py',
                   ROOT / 'spike/runtime_paths.py', ROOT / 'narwhal3d.sh']
    extras = {str(p.relative_to(ROOT)): sha(p) for p in extra_paths}
    receipt.update(source_files=inputs, source_aggregate_sha256=verify.source_aggregate(inputs),
                   additional_inputs=extras, exact_expected_exports=list(verify.EXPECTED_EXPORTS))
    receipt['gui_host'] = {'path': str(args.gui_host), 'sha256': sha(args.gui_host)}
    receipt['source_publication'] = require_publication_inventory()
    installed = []
    lock = open(CRATE / 'target/selection-activation.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        destinations = [ROOT / 'narwhal3d/native' / name for _, name in TARGETS.values()]
        destinations += [CRATE / 'target' / target / 'release' / name for target, name in TARGETS.values()]
        bundle = ROOT / 'artifacts/selection-core'
        destinations += [bundle / platform / name for platform, (_target, name) in TARGETS.items()]
        destinations.append(bundle / verify.MANIFEST_NAME)
        for destination in destinations:
            destination.parent.mkdir(parents=True, exist_ok=True)
        before = {str(p): snapshot(p) for p in destinations}
        receipt['before'] = before
        save()
        manifests = []
        reused = None
        if args.reuse_qualified:
            reused, reused_tests, proof = reusable_builds(args.reuse_qualified, inputs)
            receipt['reused_native_builds'] = proof
            save()
        for number in (1, 2):
            build_dir = output / f'build-{number}'
            binaries = dict(reused[number - 1]) if reused else {}
            if not reused:
                for platform, (target, name) in TARGETS.items():
                    run(f'build-{number}-{platform}',
                        ['cargo', '+1.95.0', 'build', '--release', '--locked', '--offline',
                         '--target', target, '--target-dir', build_dir], cwd=CRATE, env=env)
                    binaries[platform] = build_dir / target / 'release' / name
            stage = output / f'qualified-{number}'
            stage.mkdir()
            manifests.append(write_staging(stage, binaries, 0))
        first = {r['platform']: r['sha256'] for r in manifests[0]['artifacts']}
        second = {r['platform']: r['sha256'] for r in manifests[1]['artifacts']}
        assert first == second, 'independent clean builds differ'
        assert verify.source_inventory() == inputs, 'Rust source changed during qualification'
        receipt['reproducible_hashes'] = second
        if reused:
            linux_tests = [p for p in (reused_tests / 'debug/deps').glob('narwhal_selection_core-*')
                           if p.is_file() and p.suffix == '' and os.access(p, os.X_OK)]
            assert len(linux_tests) == 1
            receipt['reused_test_executables'] = {str(p): sha(p) for p in linux_tests}
            run('linux-rust-tests', [linux_tests[0], '--quiet'], env=env)
            tests = reused_tests
        else:
            run('linux-rust-tests', ['cargo', '+1.95.0', 'test', '--locked', '--offline',
                                    '--target-dir', output / 'tests'], cwd=CRATE, env=env)
            run('windows-rust-build', ['cargo', '+1.95.0', 'test', '--locked', '--offline',
                                       '--target', 'x86_64-pc-windows-gnu', '--target-dir', output / 'tests',
                                       '--no-run'], cwd=CRATE, env=env)
            tests = output / 'tests'
        windows_tests = list((tests / 'x86_64-pc-windows-gnu/debug/deps').glob('narwhal_selection_core-*.exe'))
        assert len(windows_tests) == 1
        receipt.setdefault('reused_test_executables', {}).update({str(p): sha(p) for p in windows_tests})
        run('windows-rust-tests-wine', ['wine', windows_tests[0], '--quiet'], env=env, timeout=90)
        witness = output / 'snap_clip_abi_witness.exe'
        run('windows-dll-witness-build', ['x86_64-w64-mingw32-gcc', '-O2',
                                         ROOT / 'spike/snap_clip_abi_witness.c', '-o', witness], env=env)
        qualified = output / 'qualified-2'
        run('qualified-windows-dll', ['wine', witness, wine_path(qualified / 'windows-x86_64/narwhal_selection_core.dll')], env=env)
        # Independent Linux ABI witness before installing any file.
        linux_probe = output / 'linux_abi_probe.py'
        linux_probe.write_text(PROBE.replace("os.environ.pop('NARWHAL_SELECTION_CORE', None)",
            "os.environ['NARWHAL_SELECTION_CORE'] = str(expected)").replace(
            "assert pathlib.Path(adapter._PACKAGED_LIBRARY).resolve() == expected.resolve()", ''))
        run('qualified-linux-so', [sys.executable, '-I', '-B', linux_probe, ROOT,
                                  qualified / 'linux-x86_64/libnarwhal_selection_core.so'], env=env)
        assert verify.source_inventory() == inputs and all(sha(ROOT / p) == h for p, h in extras.items()), 'qualification input changed'
        backups = output / 'backups'
        backups.mkdir()
        # Back up every existing destination BEFORE changing the first one.
        for i, destination in enumerate(destinations):
            assert snapshot(destination) == before[str(destination)], f'activation path changed: {destination}'
            previous = before[str(destination)]
            if previous:
                backup = backups / (str(i) + '-' + destination.name)
                shutil.copy2(destination, backup)
                assert sha(backup) == previous['sha256']
        for i, destination in enumerate(destinations):
            platform = 'windows-x86_64' if destination.suffix == '.dll' else 'linux-x86_64'
            source = (qualified / verify.MANIFEST_NAME if destination.name == verify.MANIFEST_NAME
                      else qualified / platform / destination.name)
            assert snapshot(destination) == before[str(destination)], f'activation race: {destination}'
            atomic_copy(source, destination)
            item = {'path': str(destination.relative_to(ROOT)), 'sha256': sha(destination),
                    'previous': before[str(destination)],
                    'backup': str((backups / (str(i) + '-' + destination.name)).relative_to(output))
                              if before[str(destination)] else None}
            installed.append(item)
            receipt['installed'] = installed
            save()
        normal = run('ordinary-linux-launch-paths', [ROOT / 'narwhal3d.sh'],
                     env=dict(env, NARWHAL3D_LAUNCHER_TEST='native-paths'))
        selected = dict(line.split('=', 1) for line in normal.splitlines())['NARWHAL_SELECTION_CORE']
        assert Path(selected) == destinations[0]
        receipt['ordinary_linux_selected_path'] = selected
        probe = output / 'ordinary_adapter_probe.py'
        probe.write_text(PROBE)
        run('ordinary-linux-adapter', [sys.executable, '-I', '-B', probe, ROOT, destinations[0]], env=env)
        windows_python = args.windows_python or ROOT / 'windows build/blender-build/bin/5.2/python/bin/python.exe'
        run('ordinary-windows-adapter-wine', ['wine', windows_python, '-I', '-B', wine_path(probe),
                                            wine_path(ROOT), wine_path(destinations[1])], env=env, timeout=90)
        run('ordinary-windows-dll', ['wine', witness, wine_path(destinations[1])], env=env)
        verify.verify_directory(bundle)
        host = args.gui_host
        # Configure the existing host functions for this checkout; do not edit
        # its file or accidentally launch its hard-coded PERSONAL source root.
        loader = ('from pathlib import Path; import sys; host=Path(sys.argv.pop(1)); '
                  'root=Path(sys.argv.pop(1)); sys.path.insert(0,str(root/"spike")); import runtime_paths; '
                  'body,tail=host.read_text().rsplit("raise SystemExit(main())",1); assert not tail.strip(); '
                  'ns={"__name__":"checkout_host","__file__":str(host)}; '
                  'exec(compile(body,str(host),"exec"),ns); ns["ROOT"]=root; raise SystemExit(ns["main"]())')
        for label, extra in [('ordinary-gui-green', []), ('ordinary-gui-zoom-red', ['--mutation', 'zoom']),
                             ('ordinary-gui-restored-green', [])]:
            text = run(label, [sys.executable, '-B', '-c', loader, host, ROOT, '--suite', 'spike/test_report121_124_gui.py', *extra], env=env, timeout=400)
            directory = Path(re.search(r'^WD_OUTPUT (.+)$', text, re.MULTILINE).group(1))
            launch = json.loads((directory / 'launch.json').read_text())
            assert str(ROOT / 'spike/test_report121_124_gui.py') in launch['command']
            assert str(ROOT / 'launch/bootstrap.py') in launch['command']
            paths = dict(line.split('=', 1) for line in launch['native_paths'].splitlines())
            assert Path(paths['NARWHAL_SELECTION_CORE']) == destinations[0]
            result = json.loads((directory / 'out/result.json').read_text())
            if not extra:
                assert result['status'] == 'passed' and len(result['checks']) >= 61
                assert result['facts']['zoom']['native_status'].endswith(str(destinations[0]))
            receipt.setdefault('gui_results', []).append({'label': label, 'directory': str(directory),
                'result_sha256': sha(directory / 'out/result.json'), 'status': result['status'],
                'checks': len(result['checks']), 'selected_library': paths['NARWHAL_SELECTION_CORE']})
        assert verify.source_inventory() == inputs and all(sha(ROOT / p) == h for p, h in extras.items()), 'qualification input changed after activation'
        assert all(sha(ROOT / item['path']) == item['sha256'] for item in installed)
        assert sha(args.gui_host) == receipt['gui_host']['sha256']
        receipt['source_publication'] = require_publication_inventory()
        receipt['public_bundle_manifest'] = {'path': str((bundle / verify.MANIFEST_NAME).relative_to(ROOT)),
                                           'sha256': sha(bundle / verify.MANIFEST_NAME),
                                           'source_inventory_matches': verify.verify_directory(bundle)['source_files'] == inputs}
        receipt.update(status='passed', finished_unix=time.time())
        save()
        print('SELECTION_ACTIVATION_PASSED', path, flush=True)
        for item in installed:
            print(item['sha256'], item['path'], flush=True)
    except BaseException as error:
        receipt.update(status='failed', error=repr(error))
        for item in reversed(installed):
            destination = ROOT / item['path']
            if not destination.exists() or sha(destination) != item['sha256']:
                item['rollback'] = 'refused: destination changed externally'
                continue
            if item['backup']:
                atomic_copy(output / item['backup'], destination)
            else:
                destination.unlink()
            item['rollback'] = 'restored'
        save()
        raise
    finally:
        lock.close()


if __name__ == '__main__':
    main()
