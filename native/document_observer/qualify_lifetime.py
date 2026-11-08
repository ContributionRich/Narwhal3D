#!/usr/bin/env python3
"""Isolated Linux lifetime qualification, then ordinary production-admission tests.

The initial subprocesses explicitly exercise the native capability under qualification
(AFTER ordinary pinned-host admission), not a production activation or invented proof.
Only observed GREEN/RED results issue a lifetime receipt. The unmodified public suites
then must pass production admission against that receipt before it can be staged.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import tempfile
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--receipt', type=Path, required=True)
    p.add_argument('--library', type=Path, required=True)
    p.add_argument('--python', type=Path, required=True)
    p.add_argument('--python-include', type=Path, required=True)
    p.add_argument('--core', type=Path, required=True)
    p.add_argument('--rust-check', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--stage-dev', action='store_true')
    a = p.parse_args()
    if sys.platform != 'linux':
        p.error('Windows lifetime NOT_QUALIFIED; no proof or installation produced')
    a.output.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(ROOT / 'scripts'))
    import stage_document_observer as staging
    record = json.loads(a.receipt.read_text())
    # The ordinary build qualifier already verified the pinned sdist inventory,
    # archives, host ABI, observer mutations, loader and registry integration.
    staging.validate(ROOT, a.core.parent, 'linux-x86_64',
                     observer_directory=a.library.parent, core_path=a.core)
    if 'master_lifetime' in record:
        raise RuntimeError('start with a fresh ordinary build qualification, not a lifetime proof')
    watched = [HERE / name for name in ('observer.cpp', 'lifetime_fixture.py',
               'test_master_lifetime.py', 'test_master_lifetime_product.py',
               'test_master_lifetime_admission.py')]
    watched += [Path(__file__), ROOT/'scripts/stage_document_observer.py']
    if sha(a.rust_check) != record['rust_check_sha256']:
        raise RuntimeError('Rust decoder differs from ordinary qualification')
    watched += [ROOT / 'narwhal3d' / name for name in
                ('native_document_observer.py', 'kernel/__init__.py', 'model.py')]
    frozen = {str(path): sha(path) for path in watched}
    env = dict(os.environ, NARWHAL_APPLICATION_CORE=str(a.core.resolve()),
               NARWHAL_HISTORY_CORE=str(a.core.resolve()))
    logs = {}

    def run(label, script, flags=(), *, red=None, crash=False):
        command = [str(a.python), '-B', str(script), '--library', str(a.library),
                   '--receipt', str(a.receipt), *flags]
        if script.name == 'test_master_lifetime_admission.py':
            command = [str(a.python), '-B', str(script)]
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=120)
        text = result.stdout + result.stderr
        path = a.output / (label + '.log')
        path.write_text(text)
        if red:
            good = result.returncode == 1 and 'AssertionError: ' + red in text
            if crash:
                good = result.returncode == -11 and 'explicit loader close request exercised' in text
        else:
            good = result.returncode == 0
            if script.name != 'test_master_lifetime_admission.py':
                good = good and ('passed' in text or 'PASS explicit unload' in text)
        if not good:
            raise RuntimeError(f'{label} unexpected result {result.returncode}: {path}')
        logs[label] = {'status': 'RED' if red else 'GREEN', 'returncode': result.returncode,
                       'log_sha256': sha(path)}
        print(label, logs[label]['status'], flush=True)

    # A scratch-only qualification adapter exposes the native candidate explicitly.
    # No production source, runtime admission function, or receipt is patched.
    prefix = f'''import sys\nsys.path.insert(0,{str(HERE)!r})\n'''
    admission = '''
from narwhal3d import native_document_observer as loader
from unittest.mock import patch
ordinary = loader.load_candidate(args.library, args.receipt)
module = sys.modules[loader._MODULE]
capability = loader.Observer(module, lifetime=True)
check(capability.master_lifetime() is not None, 'isolated pinned Linux native qualification entry points')
routing = patch.object(loader, 'load_observer', return_value=capability)
routing.start()
return capability, module
'''
    qualification_function = 'def qualify_candidate(args, check):\n' + ''.join('    '+line+'\n' for line in admission.strip().splitlines())
    probes = {}
    for name in ('test_master_lifetime.py', 'test_master_lifetime_product.py'):
        source = (HERE / name).read_text()
        anchor = 'observer,n=admitted_observer(a,check)' if name == 'test_master_lifetime.py' else 'observer, native = admitted_observer(args, admission_check)'
        replacement = 'observer,n=qualify_candidate(a,check)' if name == 'test_master_lifetime.py' else 'observer, native = qualify_candidate(args, admission_check)'
        if source.count(anchor) != 1:
            raise RuntimeError('qualification probe anchor changed')
        # Insert after imports but before admission, so child processes use the same
        # isolated qualification route. The real suites remain entirely unchanged.
        source = source.replace(anchor, qualification_function+'\n'+replacement)
        path = a.output / name
        path.write_text(prefix + source)
        probes[name] = path
    native = probes['test_master_lifetime.py']
    product = probes['test_master_lifetime_product.py']
    run('control', native, ['--output', str(a.output/'native')])
    run('history', product, ['--output', str(a.output/'product.json')])
    for mutation, witness in [('unconditional-retention','20 pruned redo cycles bound retained master count'),
                               ('premature-eviction','legitimate native owner still resolves exact master')]:
        run('mutation-'+mutation, native, ['--cycles','20','--mutation',mutation,
            '--output',str(a.output/mutation)], red=witness)
    for mutation, witness in [('rollback-owner','opaque rollback payload explicitly owns master'),
                             ('publication-clear','legitimate native owner retains master'),
                             ('drop-shared','legitimate native owner retains master')]:
        run('history-'+mutation, product, ['--cycles','20','--mutation',mutation,
             '--output',str(a.output/(mutation+'.json'))], red=witness)
    run('capability', HERE/'test_master_lifetime_admission.py')
    spec = importlib.util.spec_from_file_location('observer_build', HERE/'qualify.py')
    build = importlib.util.module_from_spec(spec); spec.loader.exec_module(build)
    source = (HERE/'observer.cpp').read_text()
    original_library, original_receipt = a.library, a.receipt
    anchors = [('copy', 'return host_userdata_class;', 'return ON_UserData::ClassId();'),
               ('unload', 'if(pinned->count(address))return;', 'return;')]
    for label, old, new in anchors:
        if source.count(old) != 1: raise RuntimeError('native mutation anchor changed')
        directory = a.output/label; directory.mkdir()
        candidate = directory/'observer.cpp';candidate.write_text(source.replace(old,new))
        library = directory/'_narwhal_document_observer.so'
        command = build.linux_compile_command(candidate,library,python_include=a.python_include,
            source_tree=Path(record['source_tree']),archives=[Path(x) for x in record['pic_inputs']])
        if build.execute(command,timeout=150,log=directory/'build.log'):
            raise RuntimeError('native mutation build failure')
        # Ordinary qualification admits a disposable compiled variant by its
        # tested build hash; it contains NO lifetime proof and is never staged.
        observer_log = directory/'observer.log'
        observer_command = [str(a.python), '-B', str(ROOT/'spike/test_document_registry_observer.py'),
                            '--library', str(library), '--rust-check', str(a.rust_check)]
        if build.execute(observer_command, timeout=60, log=observer_log):
            raise RuntimeError('ordinary native mutation observer control failed')
        mutated = dict(record, entries=[dict(label='control',status='GREEN',sha256=sha(library))])
        receipt = directory/'build.json'; receipt.write_text(json.dumps(mutated))
        a.library,a.receipt=library,receipt
        flags = ['--cycles','20','--output',str(directory/'result')]
        if label == 'unload': flags += ['--unload-child']
        probe = native
        if label == 'copy':
            probe = directory/'copy_probe.py'
            probe.write_text(prefix + '''
import argparse
from lifetime_fixture import bootstrap
bootstrap()
from narwhal3d import native_document_observer as loader
import rhino3dm as r
p=argparse.ArgumentParser();p.add_argument('--library');p.add_argument('--receipt');args,_=p.parse_known_args()
loader.load_candidate(args.library,args.receipt)
n=sys.modules[loader._MODULE]
g=r.Sphere(r.Point3d(0,0,0),2).ToBrep()
g.SetUserString('qualification','copy')
n.lease_bind(g,'isolated-copy-qualification')
c=r.GeometryBase.Duplicate(g)
assert n.lease_count(g)==2 and n.lease_count(c)==2, 'saved original wheel Duplicate retains lease'
print('passed')
''')
        run('mutation-'+label,probe,flags,red='saved original wheel Duplicate retains lease',crash=label=='unload')
        if label == 'copy':
            a.library,a.receipt = original_library,original_receipt
            run('restored-copy',probe)
    a.library,a.receipt = original_library,original_receipt
    if any(sha(path)!=digest for path,digest in frozen.items()):
        raise RuntimeError('qualification inputs changed')
    proof = dict(abi=1,platform='linux-x86_64',library_sha256=sha(a.library),
                 host_sha256=record['host']['sha256'],source_sha256=record['source_sha256'],
                 loader_sha256=record['integration']['loader_sha256'],
                 checks=dict(staging._LIFETIME_CHECKS), qualified_inputs=frozen, evidence=logs)
    record['master_lifetime']=proof
    qualified=a.output/'build.json';qualified.write_text(json.dumps(record,indent=2)+'\n')
    a.receipt=qualified
    # Mandatory second phase: unmodified tests and actual production admission.
    run('production-native', HERE/'test_master_lifetime.py', ['--output',str(a.output/'production-native')])
    run('production-product', HERE/'test_master_lifetime_product.py', ['--output',str(a.output/'production-product.json')])
    (a.output/'results.json').write_text(json.dumps(logs,indent=2)+'\n')
    if any(sha(path)!=digest for path,digest in frozen.items()):
        raise RuntimeError('qualification inputs changed before publication')
    if a.stage_dev:
        destination = ROOT/'artifacts/document-observer/linux-x86_64'
        if (ROOT/'narwhal3d/native/document-observer').exists():
            raise RuntimeError('packaged observer shadows development discovery; refusing ambiguous staging')
        destination.parent.mkdir(parents=True,exist_ok=True)
        pair = a.output/'qualified-pair'; pair.mkdir()
        shutil.copy2(a.library,pair/a.library.name)
        shutil.copy2(qualified,pair/'build.json')
        assembly = Path(tempfile.mkdtemp(prefix='.lifetime-assembly-',dir=destination.parent))
        backup = None
        try:
            staging.stage(ROOT,a.core.parent,assembly,'linux-x86_64',
                          observer_directory=pair,core_path=a.core)
            staged = assembly/'narwhal3d/native/document-observer'
            if destination.exists():
                backup = Path(tempfile.mkdtemp(prefix='preserved-before-lifetime-',dir=destination.parent))
                backup.rmdir(); destination.rename(backup)
            try:
                staged.rename(destination)
            except BaseException:
                if backup is not None and not destination.exists(): backup.rename(destination)
                raise
        finally:
            shutil.rmtree(assembly)
        print('STAGED_LIFETIME',destination,'BACKUP',backup)
    print('QUALIFIED_LIFETIME_RECEIPT',qualified)

if __name__ == '__main__': main()
