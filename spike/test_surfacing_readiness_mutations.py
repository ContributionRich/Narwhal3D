"""M1 process-isolated corruptions, bounded diagnostics, no product-source edits."""
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def worker():
    assert int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))>=6144*1024
    mutants=[
        ('omit-interior-check',
         't.assert_interior_equal=lambda *a,**k: None',
         'test_interior_only_corruption','AssertionError not raised'),
        ('corrupt-publication-interior',
         'real=t.publish\ndef bad(result):\n changed,_=t.change_only_interior(result.shape,t.ob)\n return real(t.sb.SweepSurface(changed,result.recipe,result.report))\nt.publish=bad',
         'test_recipe_transforms_and_sources','interior: pole error'),
        ('omit-prism-self-intersection-check',
         'import OCP.BRepAlgoAPI as api\nclass Blind:\n def __init__(self,*a): pass\n def IsValid(self): return True\napi.BRepAlgoAPI_Check=Blind',
         'test_nested_operations_and_step','KernelError not raised'),
    ]
    for name,mutation,case,reason in mutants:
        code='import sys,unittest; sys.path.insert(0,"spike"); import test_surfacing_freeform as t\n'+mutation+'\nunittest.main(module=t,argv=["mutation","FreeformTests.'+case+'"])'
        result=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,text=True,capture_output=True,timeout=90)
        assert result.returncode!=0 and 'FAILED (' in result.stderr and reason in result.stderr,(name,result.stderr[-3000:])
        print('RED observed:',name,reason,flush=True)
    for name,reason in [('origin','position/scale'),('scale','position/scale'),('units','units:'),('interior','correspondence: surface poles')]:
        result=subprocess.run([sys.executable,'-B','spike/test_surfacing_step_roundtrip.py','--mutation',name],cwd=ROOT,text=True,capture_output=True,timeout=90)
        assert result.returncode!=0 and reason in result.stderr,(name,result.stderr[-3000:])
        print('RED observed STEP:',name,reason,flush=True)
    for file in ['test_surfacing_freeform.py','test_surfacing_step_roundtrip.py','test_surfacing_parallel.py']:
        result=subprocess.run([sys.executable,'-B','spike/'+file],cwd=ROOT,text=True,capture_output=True,timeout=120)
        assert result.returncode==0,(file,result.stderr[-4000:])
        print('GREEN restored:',file,flush=True)


if __name__=='__main__':
    if '--worker' in sys.argv: worker()
    else:
        command=['flock','-w','120','/tmp/narwhal3d-heavy-build.lock','systemd-run','--user','--scope',
                 '-p','MemoryMax=2G','-p','MemorySwapMax=0','-p','CPUQuota=200%',sys.executable,'-B',__file__,'--worker']
        raise SystemExit(subprocess.run(command,cwd=ROOT,timeout=480).returncode)
