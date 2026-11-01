#!/usr/bin/env python3
"""Bounded real-model registry workload; absolute latency/memory/FFI evidence.

Run with bundled Python. No alternate engine, geometry approximation, GUI or user files.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import resource
import statistics
import sys
import time
import tracemalloc
import types

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType('narwhal3d')
package.__path__ = [str(ROOT / 'narwhal3d')]
sys.modules['narwhal3d'] = package
from narwhal3d.model import NarwhalModel
import rhino3dm as r3


def summary(samples):
    values = sorted(samples)
    return {'p50_ms': statistics.median(values),
            'p95_ms': values[max(0, (95 * len(values) + 99) // 100 - 1)],
            'samples_ms': samples}


def run(count, retained):
    model = NarwhalModel()
    sphere = r3.Sphere(r3.Point3d(0, 0, 0), 2).ToBrep()
    ids = [model.add_object(sphere)[0] for _ in range(count)]
    owner = model._object_registry.native
    observer = model._object_registry.observer
    calls = Counter()
    byte_counts = Counter()
    original_request, original_observed = owner.request, owner._observed
    original_observe, original_index, original_copy = observer._observe, observer._index, observer._copy_attributes

    def request(op, **fields):
        calls['registry_json:' + op] += 1
        return original_request(op, **fields)

    def observed(op, data, **fields):
        calls['registry_binary:' + op] += 1
        calls['registry_observation_abi'] += 1
        byte_counts['registry_binary_input'] += len(data)
        return original_observed(op, data, **fields)

    def observing(file):
        calls['host_full_observation'] += 1
        data = original_observe(file)
        byte_counts['host_full_output'] += len(data)
        return data

    def indexing(file):
        calls['host_index'] += 1
        data = original_index(file)
        byte_counts['host_index_output'] += len(data)
        return data

    def copying(*args):
        calls['host_attribute_copy'] += 1
        return original_copy(*args)

    owner.request, owner._observed = request, observed
    observer._observe, observer._index, observer._copy_attributes = observing, indexing, copying
    tracemalloc.start()
    start = time.perf_counter()
    snapshots = [model.snapshot()]
    initial_ms = (time.perf_counter() - start) * 1000
    samples = []
    for n in range(1, retained):
        model.replace_geometry(ids[0], r3.Sphere(r3.Point3d(n, 0, 0), 2).ToBrep())
        start = time.perf_counter()
        snapshots.append(model.snapshot())
        samples.append((time.perf_counter() - start) * 1000)
        assert model.last_snapshot == ('shared', 1), model.last_snapshot
    before = calls['host_full_observation']
    query_times = []
    for _ in range(100):
        start = time.perf_counter()
        assert model.object_count() == count
        assert model.object_id_by_name('') is not None
        query_times.append((time.perf_counter() - start) * 1000)
    assert calls['host_full_observation'] == before, 'queries serialized full geometry'
    restore_times = []
    for target in (snapshots[0], snapshots[-1]):
        start = time.perf_counter()
        model.restore(target)
        restore_times.append((time.perf_counter() - start) * 1000)
        assert model.object_count() == count
        assert model.last_restore is not None and len(model.last_restore['added']) == 1
    center = model.file.Objects.FindId(ids[0]).Geometry.GetBoundingBox().Center
    assert abs(center.X - (retained - 1)) < 1e-9
    assert len(model.file.Objects.FindId(ids[0]).Geometry.Faces) == 1
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    result = dict(objects=count, retained_snapshots=len(snapshots), initial_snapshot_ms=initial_ms,
                  edits=summary(samples), count_and_name_query=summary(query_times),
                  restores=summary(restore_times), ffi_calls=dict(calls), ffi_bytes=dict(byte_counts),
                  python_retained_bytes=current, python_peak_bytes=peak,
                  process_peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  scope='actual NarwhalModel, exact openNURBS spheres; tracemalloc enabled; no GUI or OCCT master workload')
    owner.close()
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--objects', type=int, default=1000)
    parser.add_argument('--snapshots', type=int, default=20)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if not 2 <= args.objects <= 100_000 or not 2 <= args.snapshots <= 100:
        parser.error('bounded workload requires2..100000 objects and2..100 snapshots')
    result = run(args.objects, args.snapshots)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: value for key, value in result.items() if key not in ('edits', 'count_and_name_query', 'restores')}))
    print('EDIT_P50_P95_MS', result['edits']['p50_ms'], result['edits']['p95_ms'])
    print('REGISTRY_WORKLOAD_PASSED', args.output)
