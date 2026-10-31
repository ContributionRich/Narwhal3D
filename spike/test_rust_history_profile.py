#!/usr/bin/env python3
"""Bounded history-control measurements; excludes geometry snapshot/render cost.

Select the reviewed application/history core via its environment override. Python
is only the measurement/orchestration host. Never compare these numbers to whole
application latency or serialize opaque payloads to manufacture a native benchmark.
"""
import importlib.util
import json
from pathlib import Path
import statistics
import time

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('history_profile_undo', ROOT / 'narwhal3d/undo.py')
undo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(undo)


def sample(fn, count=200):
    for _ in range(10):
        fn()
    samples = []
    for _ in range(count):
        start = time.perf_counter_ns()
        fn()
        samples.append((time.perf_counter_ns() - start) / 1000)
    return {'median_us': statistics.median(samples), 'p95_us': sorted(samples)[int(.95 * count) - 1]}


def main():
    results = []
    for size in (0, 8 * 1024 * 1024):
        payload = bytearray(size)
        native = undo.UndoStack()
        if native.backend != 'rust':
            raise RuntimeError('select a reviewed history-enabled application core')
        legacy = undo.LegacyUndoStack()
        for label, stack in [('rust', native), ('legacy', legacy)]:
            def command():
                stack.begin('Model edit')
                stack.capture(payload)
                stack.end()
            results.append({'backend': label, 'payload_bytes': size, 'operation': 'begin/capture/end',
                            **sample(command)})
            results.append({'backend': label, 'payload_bytes': size, 'operation': '100 history names',
                            **sample(stack.undo_names)})
            def navigate():
                assert stack.undo(payload).before is payload
                assert stack.redo(payload).after is payload
            results.append({'backend': label, 'payload_bytes': size, 'operation': 'undo/redo',
                            **sample(navigate)})
        native.close()
        legacy.clear()
    print(json.dumps({'scope': 'host history controls only; opaque payload reused, no geometry computation',
                      'samples_per_case': 200, 'results': results}, indent=2))


if __name__ == '__main__': main()
