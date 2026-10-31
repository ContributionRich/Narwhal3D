#!/usr/bin/env python3
"""A40: run Main's unchanged actual publisher test under its required resource limits.

No build on launch: run test_ai_accepted_review_native.py --build --self-test first.
The builder owns its own heavy lock. This runner owns only the actual GUI/test lock.
"""
import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import time
from test_automation_native import ROOT
from test_ai_accepted_review_native import TARGET, discover_executable, _headroom


def run(target, legacy=False):
    executable=discover_executable(target)
    protected=[ROOT/'narwhal3d/ai_assistant.py',ROOT/'narwhal3d/ai_primitives.py',
               ROOT/'native/automation_core/src/lib.rs',ROOT/'spike/test_ai_accepted_review.py',
               ROOT/'spike/test_ai_accepted_review_native.py']
    hashes={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
    with open('/tmp/narwhal3d_gui.lock','a') as lock, tempfile.TemporaryDirectory(prefix='narwhal-ai-v5-publisher-') as settings:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        _headroom(2048)
        env=os.environ.copy()
        env.update(PYTHONPATH=str(ROOT/'vendor/occt'),
                   LD_LIBRARY_PATH=str(ROOT/'tools/blender-patched/5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs'),
                   BLENDER_USER_CONFIG=settings,NARWHAL3D_SETTINGS_FILE=settings+'/preferences.json',
                   PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',NARWHAL3D_OPEN_WORKERS='0')
        script=ROOT/'spike'/('test_ai_primitives.py' if legacy else 'test_ai_accepted_review.py')
        args=['--library',str(Path(target)/'debug/libnarwhal_automation.so'),'--geometry','--publisher-mutations'] if legacy else ['--native-executable',str(executable),'--mutations']
        command=['systemd-run','--user','--scope','-p','MemoryMax=2048M','-p','MemorySwapMax=0',
                 'timeout','--signal=TERM','--kill-after=5s','150s',
                 str(ROOT/'tools/blender-patched/blender'),'-b','--factory-startup','-t','1',
                 '--python-exit-code','1','--python',str(script),'--',*args]
        started=time.monotonic()
        result=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True,timeout=165)
        output=result.stdout+result.stderr
        for path,digest in hashes.items():
            assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest,'Main source changed during qualification: '+path
        print(output[-10000:])
        print('A40 elapsed_seconds=',round(time.monotonic()-started,3),'legacy=',legacy,flush=True)
        if result.returncode: raise SystemExit(result.returncode)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--target-dir',type=Path,default=TARGET)
    p.add_argument('--legacy',action='store_true')
    args=p.parse_args();run(args.target_dir.resolve(),args.legacy)
