#!/usr/bin/env python3
"""Native inheritance regressions with isolated mutant artifacts, no GUI/staging."""
import argparse
import fcntl
from pathlib import Path
import shutil
import sys
import tempfile
from test_automation_native import ROOT, CRATE, compile_crate, checked


def run(target, mutations):
    assert not target.is_relative_to(ROOT)
    with open("/tmp/narwhal3d-heavy-build.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        compile_crate(CRATE,target,"test")
        compile_crate(CRATE,target,"build")
        client=[sys.executable,"-B",str(ROOT/"spike/test_ai_workspace.py"),"--library",str(target/"debug/libnarwhal_automation.so")]
        checked(client+["--mutations"],timeout=30)
        if mutations:
            mutant_target=target/"workspace-mutations"
            for label,old,new in (("inherited_visibility","visible &= layer.visible;","visible |= layer.visible;"),
                                  ("inherited_locking","locked |= layer.locked;","locked &= layer.locked;")):
                with tempfile.TemporaryDirectory(prefix="narwhal-workspace-mutation-") as tmp:
                    crate=Path(tmp)
                    shutil.copytree(CRATE/"src",crate/"src")
                    for name in ("Cargo.toml","Cargo.lock"): shutil.copy2(CRATE/name,crate/name)
                    source=crate/"src/workspace.rs";text=source.read_text()
                    assert text.count(old)==1,label
                    source.write_text(text.replace(old,new))
                    compile_crate(crate,mutant_target,"build")
                    checked(client[:-1]+[str(mutant_target/"debug/libnarwhal_automation.so")],timeout=30,expect_failure=True)
                    print("RED confirmed:",label)
            checked(client,timeout=30)
        print("Workspace native GREEN")


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--target-dir",type=Path,required=True);p.add_argument("--mutations",action="store_true")
    a=p.parse_args();run(a.target_dir.resolve(),a.mutations)
