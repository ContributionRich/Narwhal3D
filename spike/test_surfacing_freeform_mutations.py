"""Native source mutations in an external temporary crate; never touch product files.

Linux development harness. Acquires the shared build lock and a 2 GiB/no-swap scope.
Each mutant must fail the intended focused geometry test; finish on original GREEN.
"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
CRATE=ROOT/'native/geometry_core'
TEST=ROOT/'spike/test_surfacing_freeform.py'

MUTANTS=[
    ('arc-length-stations','cpp/freeform_adapter.inc',
     'normalized[i]=(t-rail.first)/(rail.last-rail.first);',
     'normalized[i]=GCPnts_AbscissaPoint::Length(*adaptor,rail.first,t)/GCPnts_AbscissaPoint::Length(*adaptor,rail.first,rail.last);',
     'test_nonplanar_accuracy','exceeds tolerance'),
    ('discard-rational-weights','cpp/freeform_adapter.inc',
     'return s->IsURational() || s->IsVRational();','return s->IsURational();',
     'test_nonplanar_accuracy','exceeds tolerance'),
    ('ignore-middle-section','src/freeform.rs',
     'data[9 * i + 3 * u + k]','data[9 * (if i == 1 { 0 } else { i }) + 3 * u + k]',
     'test_nonplanar_accuracy','exceeds tolerance'),
    ('ignore-orientation','cpp/freeform_adapter.inc',
     'freeform_profile(inputs[i+1],stations[2*i+1]!=0.)','freeform_profile(inputs[i+1],false)',
     'test_reversal_and_order','placement'),
    ('lose-affine-domain','cpp/freeform_adapter.inc',
     'normalized[i]=(t-rail.first)/(rail.last-rail.first);','normalized[i]=t;',
     'test_exact_arcs_and_domains','exceeds tolerance'),
    ('fold-gate-open','src/freeform.rs','Err(_) => -7,','Err(_) => 0,',
     'test_fold_refusal','KernelError not raised'),
    ('lose-c2','cpp/freeform_adapter.inc',
     'sweep.Build(sections,GeomFill_Section,GeomAbs_C2,8,64);',
     'sweep.Build(sections,GeomFill_Section,GeomAbs_C0,8,64);',
     'test_true_moving_frame_and_c2','C2/complexity'),
    ('finite-range-overflow','src/freeform.rs',
     '!range.is_finite()', 'false',
     'rust:overflowing_finite_inputs_fail_before_occt','assertion'),
    ('finite-interpolation-overflow','src/freeform.rs',
     'if out.iter().any(|v| !v.is_finite()) {', 'if false {',
     'rust:overflowing_finite_inputs_fail_before_occt','assertion'),
]


def invoke(args,env,timeout=120):
    return subprocess.run(args,cwd=ROOT,env=env,text=True,capture_output=True,timeout=timeout)


def worker():
    available=int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))
    assert available>=6*1024*1024,'insufficient desktop memory reserve'
    selected=os.environ['NARWHAL_GEOMETRY_CORE']
    source_files=[p for d in ['src','cpp','include'] for p in (CRATE/d).rglob('*') if p.is_file()]
    source_files += [CRATE/'Cargo.toml',CRATE/'Cargo.lock',CRATE/'build.rs']
    hashes={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
    with tempfile.TemporaryDirectory(prefix='narwhal-freeform-mutants-') as tmp:
        base=Path(tmp); crate=base/'crate'; crate.mkdir()
        for folder in ['src','cpp','include']: shutil.copytree(CRATE/folder,crate/folder)
        for name in ['Cargo.toml','Cargo.lock','build.rs']: shutil.copy2(CRATE/name,crate/name)
        env=os.environ.copy(); env['CARGO_TARGET_DIR']=str(base/'target')
        env['NARWHAL_OCCT_SOURCE_DIR']=str(ROOT/'vendor/occt-src')
        env['NARWHAL_OCCT_LIB_DIR']=str(ROOT/'tools/blender-patched/5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs')
        env['LD_LIBRARY_PATH']=env['NARWHAL_OCCT_LIB_DIR']
        env['NARWHAL_GEOMETRY_CORE']=str(base/'target/debug/libnarwhal_geometry_core.so')
        for name,path,old,new,test,reason in MUTANTS:
            if '--only-overflow' in sys.argv and not name.startswith('finite-'):
                continue
            file=crate/path; original=file.read_text(); assert original.count(old)==1,(name,'mutation anchor changed')
            file.write_text(original.replace(old,new,1))
            try:
                run=invoke(['cargo','build','--offline','--manifest-path',str(crate/'Cargo.toml'),'-j','2'],env)
                assert run.returncode==0,(name,'BUILD FAILED',run.stdout,run.stderr)
                if test.startswith('rust:'):
                    run=invoke(['cargo','test','--offline','--manifest-path',str(crate/'Cargo.toml'),'-j','2',test.split(':',1)[1],'--','--nocapture'],env)
                    assert run.returncode!=0 and 'FAILED' in run.stdout and reason in run.stderr,(name,run.stdout,run.stderr)
                else:
                    run=invoke([sys.executable,'-B',str(TEST),'FreeformTests.'+test],env)
                    assert run.returncode!=0 and 'FAILED (' in run.stderr and reason in run.stderr,(name,run.stdout,run.stderr)
                print('RED observed native:',name,reason,flush=True)
            finally:
                file.write_text(original)
        env['NARWHAL_GEOMETRY_CORE']=selected
        for name,mutation in [
            ('recipe-loss','t.sb.replay_freeform=lambda recipe: t.ob.make_box(1,1,1)'),
            ('stale-master','t.bridge.transform_geometry=lambda geom,xf: (geom.Transform(xf) and geom)'),
        ]:
            code='import sys,unittest; sys.path.insert(0,"spike"); import test_surfacing_freeform as t; '+mutation+'; unittest.main(module=t,argv=["mutation","FreeformTests.test_recipe_transforms_and_sources"])'
            run=invoke([sys.executable,'-B','-c',code],env)
            assert run.returncode!=0 and 'FAILED (' in run.stderr and 'AssertionError' in run.stderr,(name,run.stdout,run.stderr)
            print('RED observed process:',name,flush=True)
        run=invoke([sys.executable,'-B',str(TEST)],env,180)
        assert run.returncode==0,(run.stdout,run.stderr)
        print(run.stderr,flush=True)
        print('Restored original Freeform suite GREEN',flush=True)
    assert hashes=={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files},'product sources changed during mutation run'


if __name__=='__main__':
    if '--worker' in sys.argv: worker()
    else:
        args=['flock','-w','120','/tmp/narwhal3d-heavy-build.lock','systemd-run','--user','--scope',
              '-p','MemoryMax=2G','-p','MemorySwapMax=0','-p','CPUQuota=200%',sys.executable,'-B',__file__,'--worker'] + sys.argv[1:]
        raise SystemExit(subprocess.run(args,cwd=ROOT,timeout=900).returncode)
