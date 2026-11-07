#!/usr/bin/env python3
"""Compile registry mutants in a private copied crate; never break shared source."""
from __future__ import annotations
import sys
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'spike'))
import memory_bound  # noqa: E402
CRATE=ROOT/'native/application_core'
SCRATCH=CRATE/'target/main09'


def run(observation=False, witness=False, live=False, sharing=False):
    SCRATCH.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='mutations-',dir=SCRATCH))
    shutil.copytree(CRATE/'src',work/'src')
    shutil.copytree(CRATE/'tests',work/'tests')
    for name in ('Cargo.toml','Cargo.lock'):
        shutil.copyfile(CRATE/name,work/name)
    relative=('src/observation.rs' if observation else 'src/live_objects.rs' if live
              else 'src/object_witness.rs' if sharing else 'src/object_registry.rs')
    path=work/relative
    baseline=path.read_text()
    mutations=[
        ('duplicate-uuid','if index.insert(row.id.clone(), i).is_some() {','if false {'),
        ('stale-revision','if self.revision != revision {','if false {'),
        ('no-generation','let changed = t.rebuild\n                    || o.state.rows != t.state.rows\n                    || (o.state.observation != t.state.observation && !o.live);','let changed = false;'),
        ('false-noop-generation','let changed = t.rebuild\n                    || o.state.rows != t.state.rows\n                    || (o.state.observation != t.state.observation && !o.live);','let changed = true;'),
        ('applied-abort','if t.applying {','if false {'),
        ('cross-owner-snapshot','.get(&snapshot)','.get(&snapshot).or_else(|| self.owners.values().find_map(|other| other.snapshots.get(&snapshot)))'),
        ('replacement-order','if append {','if false {'),
        ('zero-payload','if row.payload == Some(0) {','if false {'),
        ('retained-budget','*n <= MAX_RETAINED','true'),
        ('text-bound','if s.len() > MAX_TEXT {','if false {'),
        ('snapshot-bound','>= MAX_SNAPSHOTS','> MAX_SNAPSHOTS'),
    ]
    if witness:
        mutations=[
            ('witness-confirmation','if t.needs_confirmation {','if false {'),
            ('payload-reservation-limit','if count == 0 || count > MAX_ROWS as u64 {','if false {'),
            ('payload-reservation-reuse','self.next = last;','self.next = first;'),
            ('witness-payload-reuse','&& old.state.rows[*position].payload == row.payload','&& false && old.state.rows[*position].payload == row.payload'),
            ('witness-commit-match','if matched != Ok(true) {','if false {'),
            ('witness-check-invalidation','if matched {','if true {'),
            ('witness-downgrade','if o.state.observation.is_some() {\n                    return Err("observation_required");\n                }','if false {\n                    return Err("observation_required");\n                }'),
        ]
    if live:
        mutations=[
            ('live-unreported-write','if unknown {','if false {'),
            ('live-dirty-mark','*o.dirty.entry(id).or_default() |= moved;','let _ = id;'),
        ]
    if sharing:
        mutations=[
            ('witness-sharing','.filter(|record| record.matches(&object))','.filter(|_| false)'),
            ('witness-geometry-equality','self.geometry == other.geometry','true'),
            ('witness-release','self.retained.fetch_sub(self.bytes, Ordering::Relaxed);','let _ = self.bytes;'),
        ]
    if observation:
        mutations=[
            ('observation-protocol','if input.take(8)? != expected {','if input.take(8)?.is_empty() {'),
            ('observation-batch','if bytes.len() > MAX_BATCH {','if false {'),
            ('observation-row-limit','if count > MAX_ROWS {','if false {'),
            ('observation-field-limit','if size > max {','if false {'),
            ('observation-duplicate','if !seen.insert(id) {','if !seen.insert(id) && false {'),
            ('observation-empty','if geometry.is_empty() || attributes.is_empty() {','if false {'),
            ('observation-nul',"if name.contains('\\0') {",'if false {'),
            ('observation-trailing','if !input.remaining.is_empty() {','if false {'),
        ]
    env=dict(os.environ,CARGO_TARGET_DIR=str(SCRATCH/'mutation-build'),CARGO_BUILD_JOBS='1')
    available=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                       if line.startswith('MemAvailable:')))
    if available < 5*1024**2:raise RuntimeError('need 4 GiB desktop reserve plus compiler allowance')
    command=['flock','-w','120','/tmp/narwhal3d-heavy-build.lock',
             *memory_bound.prefix('1024M'),
             'timeout','--kill-after=5s','120s',
             'cargo','test','--offline','--lib',
                          'observation::tests' if observation else 'object_registry::tests','--','--test-threads=1']
    rows=[]
    for label,source in [('baseline',baseline)]+[(name,baseline.replace(old,new)) for name,old,new in mutations]+[('restored',baseline)]:
        if label not in ('baseline','restored'):
            old=next(old for name,old,new in mutations if name==label)
            expected=2 if label in ('applied-abort','live-unreported-write','witness-geometry-equality') else 1
            if baseline.count(old)!=expected:
                raise RuntimeError(f'stale mutation {label}: expected {expected}, got {baseline.count(old)}')
        path.write_text(source)
        result=subprocess.run(command,cwd=work,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                              text=True,timeout=180)
        (work/f'{label}.log').write_text(result.stdout)
        negative=label not in ('baseline','restored')
        if negative:
            valid=(result.returncode!=0 and 'test result: FAILED' in result.stdout
                   and 'panicked at' in result.stdout and 'assertion' in result.stdout
                   and 'could not compile' not in result.stdout)
        else:
            valid=result.returncode==0 and 'test result: ok.' in result.stdout
        rows.append(dict(name=label,valid=valid,returncode=result.returncode,
                         source_sha256=hashlib.sha256(source.encode()).hexdigest(),log=f'{label}.log'))
        (work/'receipt.json').write_text(json.dumps(rows,indent=2))
        print(f'{label}: {"RED assertion" if negative and valid else "GREEN" if valid else "UNEXPECTED"}',flush=True)
        if not valid:
            raise RuntimeError(f'not a qualified mutation outcome; inspect {work/label}.log')
    if (CRATE/relative).read_text()!=baseline:
        raise RuntimeError('shared source changed during mutation run; rerun against current source')
    print(f'Evidence: {work}/receipt.json')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observation',action='store_true')
    parser.add_argument('--witness',action='store_true')
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--sharing',action='store_true')
    args=parser.parse_args()
    if sum((args.observation,args.witness,args.live,args.sharing)) > 1:
        parser.error('choose one mutation group')
    run(observation=args.observation,witness=args.witness,live=args.live,sharing=args.sharing)
