"""Exact synthetic CAD lifetime regression through the installed, qualified Linux boundary.

Run with bundled Python, PYTHONPATH=vendor/occt, and optional --fixture PRIVATE_COPY. No GUI,
source mutation, installed swap, native Save, or second Undo stack. This is a focused headless ownership test, not a GUI or close-time benchmark.
"""
import argparse
import gc
import json
from pathlib import Path
import statistics
import sys
import time
import tempfile
from lifetime_fixture import (bootstrap, observer_arguments, admitted_observer,
                              make_file, target_object, SYNTHETIC_COUNT)

parser = argparse.ArgumentParser(description=__doc__)
observer_arguments(parser)
parser.add_argument('--fixture')
parser.add_argument('--cycles', type=int, default=200)
parser.add_argument('--output')
parser.add_argument('--mutation', choices=('retain', 'premature', 'rollback-owner',
                                          'publication-clear', 'drop-shared'))
args = parser.parse_args()
if args.cycles < 2: parser.error('--cycles must be at least 2')
if args.output is None:
    args.output = str(Path(tempfile.mkdtemp(prefix='product-master-lifetime-')) / 'result.json')
bootstrap()
checks = []
def admission_check(ok,label):
    checks.append({'ok':bool(ok),'label':label})
    if not ok: raise AssertionError(label)
observer, native = admitted_observer(args, admission_check)
if observer is None: raise SystemExit(0)

import rhino3dm as r3
from narwhal3d import bridge, commands, kernel, model
from narwhal3d.kernel import occt_backend as occt

rows = []
started = time.perf_counter()


def check(ok, label):
    checks.append({'ok': bool(ok), 'label': label})
    if not ok:
        raise AssertionError(label)


def collect():
    gc.collect()
    return kernel.collect_masters()


def geometry(file, oid):
    return file.Objects.FindId(oid).Geometry


def exact(proxy, faces, volume):
    shape = kernel.master_of(proxy)
    check(shape is not None, 'legitimate native owner retains master')
    check(occt.is_airtight(shape), 'retained master remains airtight')
    check(occt.face_types(shape) == faces, 'retained master keeps analytic faces')
    check(abs(occt.volume(shape) - volume) < 1e-8, 'retained master keeps exact volume')


status = kernel.master_lifetime_status()
if not status['available']:
    print('NOT_QUALIFIED: conservative strong ownership; lifetime workload not activated', status)
    raise SystemExit(0)

result = {'status': 'failed', 'checks': checks, 'rows': rows, 'mutation': args.mutation}
try:
    document = model.NarwhalModel()
    document.file = make_file(args.fixture)
    check(document.object_count() == (len(document.file.Objects) if args.fixture else SYNTHETIC_COUNT),
          'representative exact document loaded')
    for obj in document.file.Objects:
        bridge.rehydrate_master(obj.Geometry)
    del obj
    target = target_object(document.file)
    source = target.Geometry
    oid = str(target.Attributes.Id)
    del target
    faces = occt.face_types(kernel.master_of(source))
    volume = occt.volume(kernel.master_of(source))
    original_duplicate = r3.GeometryBase.Duplicate
    detached = original_duplicate(source)
    other = model.NarwhalModel()
    other_id = other.file.Objects.AddBrep(detached, r3.ObjectAttributes())
    context = commands.NarwhalContext(document)
    base = len(kernel._MASTERS)
    if args.mutation == 'retain':
        kernel.collect_masters = lambda: 0
    elif args.mutation == 'rollback-owner':
        kernel.master_registry_snapshot = lambda: dict(kernel._MASTERS)
    elif args.mutation == 'publication-clear':
        publish = kernel.replace_document_masters

        def clearing_publish(bindings):
            count = publish(bindings)
            keep = {kernel.master_key_of(proxy) for proxy, _shape in bindings}
            for key in tuple(kernel._MASTERS):
                if key not in keep:
                    del kernel._MASTERS[key]
            return count

        kernel.replace_document_masters = clearing_publish
    elif args.mutation == 'drop-shared':
        detach = kernel.drop_master

        def dropping_shared(proxy):
            key = kernel.master_key_of(proxy)
            result = detach(proxy)
            kernel._MASTERS.pop(key, None)
            return result

        kernel.drop_master = dropping_shared
    for index in range(args.cycles):
        tick = time.perf_counter()
        context.begin_undo('Move')
        context.note_change()
        moved = original_duplicate(geometry(document.file, oid))
        moved = bridge.transform_geometry(moved, r3.Transform.Translation(.125, 0, 0))
        check(document.replace_geometry(oid, moved), 'real model replacement succeeds')
        del moved
        context.end_undo()
        check(context.undo.undo(document.snapshot(), restore=document.restore) is not None,
              'existing single native Undo restores snapshot')
        if index in (0, args.cycles - 1):
            check(context.undo.redo(document.snapshot(), restore=document.restore) is not None,
                  'existing native Redo restores snapshot')
            check(context.undo.undo(document.snapshot(), restore=document.restore) is not None,
                  'same native Undo reverses Redo')
        collect()
        rows.append({'cycle': index + 1, 'masters': len(kernel._MASTERS),
                     'lifetime': kernel.master_lifetime_status(),
                     'history': dict(context.undo._info()),
                     'seconds': time.perf_counter() - tick})
    check(max(row['masters'] for row in rows) <= base + 2,
          'pruned native redo history bounds retained masters')
    check(rows[-1]['history']['record_count'] == 1
          and rows[-1]['history']['payload_count'] == 2,
          'only one native record and two payloads retained')
    context.undo.clear()
    collect()
    if args.mutation == 'premature':
        kernel._MASTERS.pop(kernel.master_key_of(detached), None)
    for proxy in (source, detached, geometry(other.file, other_id)):
        exact(proxy, faces, volume)

    # Rollback snapshots are explicit owners, even if geometry owners disappear.
    temporary = original_duplicate(source)
    temporary = bridge.transform_geometry(temporary, r3.Transform.Translation(2, 0, 0))
    key = kernel.master_key_of(temporary)
    rollback = kernel.master_registry_snapshot()
    del temporary
    collect()
    check(key in kernel._MASTERS, 'opaque rollback payload explicitly owns master')
    del rollback
    collect()
    check(key not in kernel._MASTERS, 'released rollback payload permits reclamation')

    # Publishing/restoring another registry must not invalidate other models/copies.
    temporary = original_duplicate(source)
    rollback = kernel.master_registry_snapshot()
    kernel.replace_document_masters([(temporary, kernel.master_of(source))])
    for proxy in (source, detached, geometry(other.file, other_id)):
        exact(proxy, faces, volume)
    kernel.restore_master_registry(rollback)
    for proxy in (source, detached, geometry(other.file, other_id), temporary):
        exact(proxy, faces, volume)
    kernel.drop_master(temporary)
    for proxy in (source, detached, geometry(other.file, other_id)):
        exact(proxy, faces, volume)
    del temporary, rollback
    collect()
    alias = original_duplicate(source)
    kernel.drop_master(alias)
    for proxy in (source, detached, geometry(other.file, other_id)):
        exact(proxy, faces, volume)
    del alias
    collect()
    binary = occt.shape_to_bytes(kernel.master_of(detached), triangles=False)
    restored = occt.shape_from_bytes(binary)
    check(occt.is_airtight(restored) and occt.face_types(restored) == faces,
          'post-reclamation exact binary roundtrip preserves manufacturing geometry')
    result['status'] = 'passed'
    result['baseline'] = {'masters': base, 'faces': faces, 'volume': volume}
finally:
    result['elapsed_s'] = time.perf_counter() - started
    if rows:
        result['median_seconds'] = statistics.median(row['seconds'] for row in rows)
    Path(args.output).write_text(json.dumps(result, indent=2))
    print('MASTER_LIFETIME_PRODUCT', result['status'], 'checks', len(checks),
          'cycles', len(rows), 'elapsed_s', result['elapsed_s'])
