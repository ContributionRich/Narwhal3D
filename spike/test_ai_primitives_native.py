#!/usr/bin/env python3
"""Bounded prototype build, compiled mutations and optional actual-Blender acceptance."""
import argparse
import fcntl
import os
from pathlib import Path
import shutil
import sys
import tempfile
from test_automation_native import ROOT, CRATE, checked, compile_crate

MUTATIONS=[
    ('approval','if !pending.approved {','if false {','test_live_catalog_unchanged_and_one_use_approval'),
    ('consume','self.clear();\n        current.validate()?;', 'current.validate()?;', 'test_live_catalog_unchanged_and_one_use_approval'),
    ('dimensions','*radius <= tolerance','*radius < 0.', 'test_dimensions_unknown_fields_and_boolean_numbers'),
    ('geometry','!facts.airtight','false','test_rust_geometry_acceptance_checks_real_invariants'),
    ('context','current != proposal.expected || current.validate().is_err()', 'false','test_undo_and_document_context_are_exact'),
    ('height_domain','h <= tolerance','h < 0.','test_shared_validation_for_all_prototypes'),
    ('cone_face','("CONE".into(), 1)','("CYLINDER".into(), 1)','test_cone_requires_base_cap_exact_recipe_and_conical_face'),
    ('cylinder_caps','("PLANE".into(), 2)','("PLANE".into(), 1)','test_cylinder_requires_positive_height_and_both_caps'),
]


def run(target, mutations, geometry, mutation_filter=None):
    assert not target.is_relative_to(ROOT)
    with open('/tmp/narwhal3d-heavy-build.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        compile_crate(CRATE,target,'test');compile_crate(CRATE,target,'build')
        library=str(target/'debug/libnarwhal_automation.so')
        client=[sys.executable,'-B',str(ROOT/'spike/test_ai_primitives.py'),'--library',library]
        checked(client,timeout=30)
        if mutations:
            variants=[('primitives.rs',m) for m in MUTATIONS]+[
                ('actions.rs',('prototype_catalog','"prototype_implemented":true,"execution_available":false','"prototype_implemented":true,"execution_available":true','test_live_catalog_unchanged_and_one_use_approval')),
                ('conversation.rs',('stale_document','if context.append.document_session != self.binding.document_context {\n            self.primitive_gate.clear();','if context.append.document_session != self.binding.document_context {','test_approval_context_changes_retire_each_prototype')),
                ('conversation.rs',('new_turn','if fresh_user_turn {\n            self.primitive_gate.clear();','if fresh_user_turn {','test_new_user_turn_cannot_retain_prototype_approval'))]
            if mutation_filter:
                variants=[variant for variant in variants if mutation_filter in variant[1][0]]
                assert variants,'no matching mutation'
            for filename,(label,old,new,test) in variants:
                with tempfile.TemporaryDirectory(prefix='narwhal-ai-primitive-mutation-') as tmp:
                    crate=Path(tmp);shutil.copytree(CRATE/'src',crate/'src')
                    for name in ('Cargo.toml','Cargo.lock'):shutil.copy2(CRATE/name,crate/name)
                    source=crate/'src'/filename;text=source.read_text()
                    assert old in text,(label,'anchor changed')
                    source.write_text(text.replace(old,new,1))
                    compile_crate(crate,target/'primitive-mutations','build')
                    checked(client[:-1]+[str(target/'primitive-mutations/debug/libnarwhal_automation.so'),'--test',test],timeout=30,expect_failure=True)
                    print('RED confirmed:',label,flush=True)
            checked(client,timeout=30)
        if geometry:
            memory=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))
            assert memory >= (4096+1536)*1024,'desktop reserve plus Blender cap not available'
            with open('/tmp/narwhal3d_gui.lock','a') as gui, tempfile.TemporaryDirectory(prefix='narwhal-ai-prototype-settings-') as settings:
                fcntl.flock(gui,fcntl.LOCK_EX|fcntl.LOCK_NB)
                checked(['systemd-run','--user','--scope','-p','MemoryMax=1536M','-p','MemorySwapMax=0',
                    'timeout','--kill-after=5s','90s','env','PYTHONDONTWRITEBYTECODE=1','OMP_NUM_THREADS=1','NARWHAL3D_OPEN_WORKERS=0',
                    'PYTHONPATH='+str(ROOT/'vendor/occt'),
                    'LD_LIBRARY_PATH='+str(ROOT/'tools/blender-patched/5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs'),
                    'BLENDER_USER_CONFIG='+settings,'NARWHAL3D_SETTINGS_FILE='+settings+'/preferences.json',
                    str(ROOT/'tools/blender-patched/blender'),'-b','--factory-startup','--threads','1','--python-exit-code','1',
                    '--python',str(ROOT/'spike/test_ai_primitives.py'),'--','--library',library,'--geometry','--publisher-mutations'],timeout=105)
        print('Prototype gate GREEN; live activation remains OFF')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--target-dir',type=Path,required=True);p.add_argument('--mutations',action='store_true');p.add_argument('--geometry',action='store_true')
    p.add_argument('--mutation-filter')
    args=p.parse_args();run(args.target_dir.resolve(),args.mutations,args.geometry,args.mutation_filter)
