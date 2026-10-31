"""Bounded microbenchmarks; reports actual ABI traffic, p50/p95 and Python peaks.

Not a speed gate: compare absolute UI latency and asymptotic traffic. Allocation
peaks are tracemalloc Python-only; native allocations are documented separately.
"""
import gc
import importlib.util
import json
from pathlib import Path
import statistics
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ui_core_bench", ROOT / "narwhal3d/textfield.py")
tf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tf)


def trial(cls, size, kind):
    f = cls("a" * size, multiline=True)
    if cls is tf.TextField:
        assert f._native is not None
    _ = f.text
    n = getattr(f, "_native", None)
    before = (n.calls, n.uploaded, n.downloaded) if n else (0, 0, 0)
    times = []
    payload = "😀e\u0301\r\n" * 200
    for _ in range(300):
        begin = time.perf_counter_ns()
        if kind == "caret":
            f.move(-1)
            _ = f.text, f.caret, f.selection()
        elif kind == "type":
            f.insert("Z")
            _ = f.text
            f.backspace()
            _ = f.text
        else:
            f.set_caret(size // 2)
            f.insert(payload)
            _ = f.text
            f.set_caret(size // 2, True)
            f.delete()
            _ = f.text
        times.append((time.perf_counter_ns() - begin) / 1000)
    after = (n.calls, n.uploaded, n.downloaded) if n else (0, 0, 0)
    return {"p50_us": round(statistics.median(times), 3),
            "p95_us": round(sorted(times)[int(len(times)*.95)], 3),
            "calls": after[0]-before[0], "upload_bytes": after[1]-before[1],
            "download_bytes": after[2]-before[2]}


for size in (8, 256, 100000):
    for kind in ("caret", "type", "paste"):
        gc.collect()
        native = trial(tf.TextField, size, kind)
        print(json.dumps(dict(size=size, action=kind, native=native)))
# Same unchanged shell handler as production; setup is excluded from event latency.
import importlib
import sys
import types
pkg = types.ModuleType("_ui_bench_callers")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules[pkg.__name__] = pkg
shell = importlib.import_module(pkg.__name__ + ".shell_input")
for size in (8, 256, 100000):
    for kind in ("caret", "type", "paste"):
        result = {}
        for name, cls in (("native", tf.TextField),):
            field = cls()
            shell._field = field
            n = getattr(field, "_native", None)
            durations, calls, uploaded, downloaded = [], 0, 0, 0
            event = types.SimpleNamespace(type={"caret": "LEFT_ARROW", "type": "A", "paste": "V"}[kind],
                                          value="PRESS", unicode="Z" if kind == "type" else "",
                                          ascii="", ctrl=kind == "paste", alt=False, oskey=False, shift=False)
            clipboard = "😀e\u0301\r\n" * 200
            for _ in range(300):
                field.set_text("a" * size)
                text = shell._last_text = field.text
                before = (n.calls, n.uploaded, n.downloaded) if n else (0, 0, 0)
                begin = time.perf_counter_ns()
                reply = shell.handle(event, text, clipboard)
                durations.append((time.perf_counter_ns() - begin) / 1000)
                assert reply["consumed"]
                if n:
                    calls += n.calls - before[0]
                    uploaded += n.uploaded - before[1]
                    downloaded += n.downloaded - before[2]
            result[name] = dict(p50_us=round(statistics.median(durations), 3),
                                p95_us=round(sorted(durations)[285], 3), calls=calls,
                                upload_bytes=uploaded, download_bytes=downloaded)
        print(json.dumps(dict(caller="shell_input.handle", size=size, action=kind, **result)))

tracemalloc.start()
trial(tf.TextField, 100000, "caret")
print(json.dumps({"python_peak_bytes_large_caret_including_construction": tracemalloc.get_traced_memory()[1]}))
