"""Preparation fault injection: wrong affine map, stale snapshots, native ambiguity.

Runs under the shared build lock and a 2 GiB/no-swap scope. Native mutations use
an external temporary crate through the existing mutation harness; no source edits.
"""
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def worker():
    assert int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))>=6144*1024
    mutations=[
        ('wrong-affine-mapping',
         'real=t.ng.prepare_freeform_stations\ndef wrong(*args,**kw):\n result,report=real(*args,**kw)\n if result.ok:\n  a,b=report["domain"]; report["parameters"]=(a,a+.27*(b-a),b)\n return result,report\nt.ng.prepare_freeform_stations=wrong',
         'test_non_affine_arc_point_fidelity','physical station mapping error'),
        ('stale-source-check-removed',
         't.sb.validate_prepared_sources=lambda *a: None',
         'test_change_during_build_and_cancel_leave_no_publication','KernelError not raised'),
        ('snapshot-copy-check-removed',
         'import inspect,textwrap\nsource=textwrap.dedent(inspect.getsource(t.sb.prepare_freeform))\nold="if _source_snapshot(copies, source_ids) != hashes:"\nassert source.count(old)==1\nsource=source.replace(old,"if False:",1)\nexec(compile(source,t.sb.__file__,"exec"),t.sb.__dict__)',
         'test_snapshot_copy_matches_content_fingerprint','KernelError not raised'),
        ('post-build-check-removed',
         'import inspect,textwrap\nsource=textwrap.dedent(inspect.getsource(t.sb.build_prepared_freeform))\nold="    check()\\n    result.recipe"\nassert source.count(old)==1\nsource=source.replace(old,"    result.recipe",1)\nexec(compile(source,t.sb.__file__,"exec"),t.sb.__dict__)',
         'test_change_during_build_and_cancel_leave_no_publication','KernelError not raised'),
    ]
    for name,mutation,case,reason in mutations:
        code='import sys,unittest; sys.path.insert(0,"spike"); import test_surfacing_preparation as t\n'+mutation+'\nunittest.main(module=t,argv=["mutation","PreparationTests.'+case+'"])'
        run=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,text=True,capture_output=True,timeout=90)
        assert run.returncode!=0 and 'FAILED (' in run.stderr and reason in run.stderr,(name,run.stderr[-3000:])
        print('RED preparation:',name,reason,flush=True)
    import test_surfacing_freeform_mutations as native
    native.MUTANTS=[('prep-nearest-only','src/preparation.rs','return Err(6);','continue;',
                     'rust:unique_physical_parameter_not_nearest_only','assertion')]
    native.worker()
    run=subprocess.run([sys.executable,'-B','spike/test_surfacing_preparation.py'],cwd=ROOT,text=True,capture_output=True,timeout=120)
    assert run.returncode==0,run.stderr[-4000:]
    print(run.stderr,flush=True)
    print('GREEN restored physical preparation',flush=True)


if __name__=='__main__':
    if '--worker' in sys.argv: worker()
    else:
        args=['flock','-w','120','/tmp/narwhal3d-heavy-build.lock','systemd-run','--user','--scope',
              '-p','MemoryMax=2G','-p','MemorySwapMax=0','-p','CPUQuota=200%',sys.executable,'-B',__file__,'--worker']
        raise SystemExit(subprocess.run(args,cwd=ROOT,timeout=480).returncode)
