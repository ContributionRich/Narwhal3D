"""Scoped transcript/real consumer timing and allocation receipts (no GUI)."""
import ctypes as C
import gc
import hashlib
import importlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
import types

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType('narwhal3d'); pkg.__path__ = [str(ROOT / 'narwhal3d')]
sys.modules['narwhal3d'] = pkg
module = importlib.import_module('narwhal3d.command_transcript')
cmdbar = importlib.import_module('narwhal3d.cmdbar')
state = importlib.import_module('narwhal3d.state')


def counts(field):
    n = getattr(field, '_native', None)
    return (n.calls, n.uploaded, n.downloaded) if n else (0, 0, 0)


def timing(field, op, iterations=300):
    values = []
    before = counts(field)
    for _ in range(iterations):
        t = time.perf_counter_ns(); op(); values.append((time.perf_counter_ns()-t)/1000)
    after = counts(field)
    return dict(p50_us=round(statistics.median(values), 3), p95_us=round(sorted(values)[int(.95*len(values))], 3),
                native_calls=after[0]-before[0], upload_bytes=after[1]-before[1], download_bytes=after[2]-before[2])


def main():
    out = Path(tempfile.mkdtemp(prefix='tb-', dir='/var/tmp/nwp'))
    rows = []
    for n, length in [(8, 12), (200, 12), (500, 12), (20000, 12), (200, 4096)]:
        seed = ['> '+str(i)+('x'*length) for i in range(n)]
        for backend in ('legacy', 'native'):
            field = list(seed) if backend == 'legacy' else module.Transcript()
            if backend == 'native':
                assert field._native is not None
                field.append_many(seed)
            # Actual unchanged consumer function; warm read then time stable repaint.
            cmdbar.visible_history(field, 5)
            hot = timing(field, lambda: cmdbar.visible_history(field, 5))
            if backend == 'native': assert hot['native_calls'] == 0
            rows.append(dict(n=n, line_payload=length, backend=backend, workload='unchanged_visible_tail', **hot))
            def cycle():
                field.append('> command')
                cmdbar.visible_history(field, 5)
                if len(field) > n: del field[:len(field)-n]
                _ = field[-15:]
            rows.append(dict(n=n, line_payload=length, backend=backend, workload='bounded_append_paint_prune_context', **timing(field, cycle)))
            if backend == 'native': field.close()
    # The actual unmodified AppState.log, not a reimplementation of its policy.
    for backend in ('legacy', 'native'):
        st = state.AppState() if backend == 'native' else types.SimpleNamespace(history=[])
        for i in range(200): st.history.append(str(i))
        rows.append(dict(n=200, backend=backend, workload='AppState.log',
                         **timing(st.history, lambda: state.AppState.log(st, 'result'))))
        if backend == 'native': st.history.close()
    gc.collect()
    tracemalloc.start()
    f = module.Transcript(); f.append_many(['x'*100 for _ in range(2000)])
    _ = f[-15:]; f.clear(); f.close()
    gc.collect()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    payload = dict(rows=rows, python_tracemalloc_current=current, python_tracemalloc_peak=peak,
            source_sha256={p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in (
                'narwhal3d/state.py', 'narwhal3d/command_transcript.py', 'narwhal3d/native_transcript.py',
                'native/ui_core/src/transcript.rs', 'native/ui_core/src/transcript_ffi.rs')},
            artifact_sha256=hashlib.sha256(Path(os.environ['NARWHAL_UI_CORE_LIBRARY']).read_bytes()).hexdigest())
    (out/'python.json').write_text(json.dumps(payload, indent=2))
    # Same native source/ABI, standalone allocator-instrumented executable; no
    # editor allocator collision, production feature flag or source change.
    probe = out/'allocations.rs'
    code = r'''
use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering::SeqCst};
static LIVE: AtomicUsize = AtomicUsize::new(0);
static CALLS: AtomicUsize = AtomicUsize::new(0);
static BYTES: AtomicUsize = AtomicUsize::new(0);
static ACTIVE: AtomicBool = AtomicBool::new(false);
struct Counter;
#[global_allocator] static ALLOC: Counter = Counter;
fn record(n: usize) { if ACTIVE.load(SeqCst) { CALLS.fetch_add(1, SeqCst); BYTES.fetch_add(n, SeqCst); } }
unsafe impl GlobalAlloc for Counter {
    unsafe fn alloc(&self, l: Layout) -> *mut u8 { let p=System.alloc(l); if !p.is_null() { LIVE.fetch_add(l.size(),SeqCst); record(l.size()); } p }
    unsafe fn dealloc(&self,p:*mut u8,l:Layout) { LIVE.fetch_sub(l.size(),SeqCst); System.dealloc(p,l); }
    unsafe fn realloc(&self,p:*mut u8,l:Layout,n:usize)->*mut u8 { let q=System.realloc(p,l,n); if !q.is_null() { LIVE.fetch_sub(l.size(),SeqCst); LIVE.fetch_add(n,SeqCst); record(n); } q }
}
#[path="@MODEL@"] mod transcript;
#[path="@FFI@"] mod transcript_ffi;
use transcript_ffi::*;
fn call(s: Reply, op:u32, a:i64, b:i64, c:i64, data:&[u8])->Reply {
    let r=Request {op,flags:0,revision:s.revision,a,b,c}; let mut out=Reply::default();
    assert_eq!(unsafe { narwhal_transcript_apply(s.handle,&r,40,data.as_ptr(),data.len(),&mut out,56) },0); out
}
fn main() {
    println!("native allocator probe (same source, separate instrumented executable)");
    let data=transcript::encode_lines(&[&[65,66,67]]).unwrap();
    let mut s=call(Reply::default(),0,0,0,0,&[]);
    s=call(s,1,0,0,0,&data);
    let snap=call(s,4,0,1,1,&[]); narwhal_transcript_release(s.handle,snap.token); narwhal_transcript_close(s.handle);
    let baseline=LIVE.load(SeqCst);
    ACTIVE.store(true,SeqCst);
    for _ in 0..100 {
        s=call(Reply::default(),0,0,0,0,&[]);
        for _ in 0..200 { s=call(s,1,0,0,0,&data); }
        let snap=call(s,4,185,200,1,&[]);
        narwhal_transcript_release(s.handle,snap.token);
        s=call(s,3,0,0,0,&[]);
        narwhal_transcript_close(s.handle);
    }
    ACTIVE.store(false,SeqCst);
    let live=LIVE.load(SeqCst); let calls=CALLS.load(SeqCst); let bytes=BYTES.load(SeqCst);
    println!("allocation_calls={calls} requested_bytes={bytes} warmed_live_before={baseline} live_after={live}");
    assert_eq!(live,baseline,"native owned memory must return to warmed baseline");
}
'''
    probe.write_text(code.replace('@MODEL@', str(ROOT/'native/ui_core/src/transcript.rs')).replace('@FFI@', str(ROOT/'native/ui_core/src/transcript_ffi.rs')))
    command = ['systemd-run', '--user', '--scope', '--quiet', '-p', 'MemoryMax=1G', '-p', 'MemorySwapMax=0',
               'flock', '-w', '60', '/tmp/narwhal3d-heavy-build.lock', 'rustc', '--edition', '2021', '-O', str(probe), '-o', str(out/'allocations')]
    with (out/'build.log').open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=90)
    result = subprocess.run([str(out/'allocations')], capture_output=True, text=True, timeout=30)
    (out/'native.txt').write_text(result.stdout+result.stderr)
    assert result.returncode == 0, result.stdout+result.stderr
    print(json.dumps(payload, indent=2))
    print(result.stdout)
    print('BENCHMARK_EVIDENCE',out)


if __name__ == '__main__': main()
