#!/usr/bin/env python3
"""V4 safe Rust tests/isolated mutations; test-only accepted-call seam never in dylib."""
import argparse
import fcntl
import hashlib
import json
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from test_automation_native import ROOT,CRATE,compile_command,compile_crate,checked

MUTATIONS=[
    ('a38_expired_sibling','approval_lifecycle.rs','            Self::expire_record(record, now)?;','            /* mutation: leave expired siblings active */','v5_a38'),
    ('a37_timeout_origin','approval_lifecycle.rs','"source":"native_publication_timeout","decision_token":record.token','"source":"trusted_publisher_event","decision_token":record.token','v5_a37_timeout'),
    ('a37_mismatch_token','approval_lifecycle.rs','"source":"native_receipt_validation","decision_token":record.token','"source":"native_receipt_validation","decision_token":null','v5_a37_mismatch'),
    ('a36_box_pending_owner','conversation.rs','        self.box_gate.check_pending(&proposal)?;\n','', 'v5_a36'),
    ('late_review_rebind','conversation.rs','self.review_book.invalidate_call(owner, call_id)?;','self.rebind(binding)?;','v4_a35'),
    ('forged_decision_scope','approval_lifecycle.rs','if self.get(&token.owner, &token.call_id)?.token != *token {','if false {','v4_a35'),
    ('summary_master_bound','evidence_contract.rs','.is_some_and(|s| s.is_empty() || s.len() > 256 || s.chars().any(char::is_control))','.is_some_and(|_s| false)','v4_a19'),
    ('summary_units','evidence_contract.rs','if !crate::plan::fixed_scale_unit(&self.units) {','if false {','v4_a19'),
    ('descriptor_activation','approval_lifecycle.rs','enabled: false,','enabled: true,','v4_a33'),
    ('retention_boundary','approval_lifecycle.rs','MAX_REVIEWS: usize = 8','MAX_REVIEWS: usize = 7','v4_a30_retention'),
    ('close_review_session','lib.rs','sessions.items.remove(&handle).ok_or("unknown_session")?;','sessions.items.get(&handle).ok_or("unknown_session")?;','v4_a27_close'),
    ('review_capacity','conversation.rs','self.abandon_turn();\n            return Err("context_budget");','return Err("context_budget");','v4_a26'),
    ('host_event_alias','catalog.rs','let name = canonical(&call.name).ok_or("unsupported_tool")?;', 'if call.name=="record_review_publication" { return Ok(()); }\n    let name = canonical(&call.name).ok_or("unsupported_tool")?;','v4_a25'),
    ('plan_edit_approval','conversation.rs','let mut retained = std::mem::take(&mut self.plan_draft);\n        self.abandon_turn();','let mut retained = std::mem::take(&mut self.plan_draft);','v4_a24'),
    ('predicate_feasibility','plan.rs','"verification_available":false','"verification_available":true','v4_a22'),
    ('unknown_unit_scale','plan.rs','if !fixed_scale_unit(&self.units) {','if false {','v4_a21'),
    ('upstream_block','plan.rs','available && !blocked_dependency','available','v4_a20'),
    ('duplicate_plan_input','plan.rs','if !inputs.insert(key) {','if !inputs.insert(key) && false {','v4_a20'),
    ('cancelled_summary','evidence_contract.rs','|| self.cancelled','|| false','v4_a19'),
    ('fake_geometry_producer','evidence_contract.rs','"producer":{"available":false','"producer":{"available":true','v4_a19'),
    ('failed_read_evidence','approval_lifecycle.rs','|| result.is_error','|| false','v4_a18'),
    ('evidence_stage','evidence_contract.rs','"overall":EvidenceStage::Unverifiable','"overall":EvidenceStage::CurrentlyMeasured','v4_a17'),
    ('selection_requirement','plan.rs','if self.scope_requirement == ScopeRequirement::CurrentSelection {','if false {','v4_a16'),
    ('unknown_identity','evidence_contract.rs','        Err("authoritative_identity_unavailable")\n    }','        Ok(())\n    }','v4_a15'),
    ('production_catalog','catalog.rs','    tools\n}\npub fn schema', '    tools.push(ToolDefinition {name:"narwhal_create_sphere".into(),description:"BAD LIVE TOOL".into(),parameters:crate::actions::circle_schema()});\n    tools\n}\npub fn schema','v4_a14'),
    ('canonical_result','conversation.rs','if result.output != expected.output || result.is_error != expected.is_error {','if false {','v4_a12'),
    ('result_reuse','approval_lifecycle.rs','record.result_recorded = true;','record.result_recorded = false;','v4_a12'),
    ('evidence_scope','approval_lifecycle.rs','filter(|record| record.scope_current)','filter(|_record| true)','v4_a11'),
    ('publication_timeout','approval_lifecycle.rs','PUBLICATION_TTL_MS: u64 = 60_000','PUBLICATION_TTL_MS: u64 = 60_001','v4_a10'),
    ('review_one_use','approval_lifecycle.rs','record.state = record.state.transition(Event::Consume)?;','/* mutation: keep approval reusable */','v4_a09'),
    ('review_invalidation_order','conversation.rs','if owner.conversation != self.id || owner.generation != self.generation {\n            return Err("stale_review");','if binding != self.binding { self.rebind(binding.clone())?; }\n        if owner.conversation != self.id || owner.generation != self.generation {\n            return Err("stale_review");','v4_a08'),
    ('review_invalidation_cancel','conversation.rs','fn abandon_turn(&mut self) {\n        self.plan_draft = crate::plan_review::Store::default();\n        self.review_book.invalidate_all();','fn abandon_turn(&mut self) {\n        self.plan_draft = crate::plan_review::Store::default();','v4_a08'),
    ('review_expiry_boundary','approval_lifecycle.rs','now >= expiry','now > expiry','v4_a07'),
    ('human_deny','approval_lifecycle.rs','if allow { Event::Allow } else { Event::Deny }','Event::Allow','v4_a06'),
    ('human_action','approval_lifecycle.rs','if record.action != action {','if false {','v4_a06'),
    ('early_result','conversation.rs','self.review_book.owns_call(&token, &call.id)','false','v4_a05'),
    ('http_review_timer','approval_lifecycle.rs','REVIEW_TTL_MS: u64 = 300_000','REVIEW_TTL_MS: u64 = 30_000','v4_a05'),
    ('accepted_owner','approval_lifecycle.rs','if record.token.owner != *owner {','if false {','v4_a04'),
    ('canonical_action','approval_lifecycle.rs','let action = Arguments::from_accepted_call(call)?;', 'let mut action = Arguments::from_accepted_call(call)?; if let Arguments::Cylinder {ref mut height,..}=action { *height=1.; }','v4_a04'),
    ('human_transition','approval_lifecycle.rs','(AwaitingHuman, Allow) => Approved,','(AwaitingHuman, Allow) => Consumed,','v4_a03'),
    ('catalog_seam','conversation.rs','if self.fixture_primitives\n            && matches!(', 'if matches!(', 'v4_a02'),
    ('completed_http','conversation.rs','Ok(reply) => {\n                json!', 'Ok(reply) => {\n                self.pending=Some(pending);\n                json!', 'v4_a02'),
]


def rust_test(crate,target,name,red=False):
    command=compile_command(crate,target,'test',('--lib',name,'--','--nocapture','--test-threads=1'))
    result=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=180)
    out=result.stdout+result.stderr
    if red:
        if result.returncode and 'test result: FAILED' in out and 'panicked at' in out:
            print(out[-1600:],flush=True);return
        label='SURVIVED' if result.returncode==0 else 'INFRA_FAILURE'
        raise AssertionError(label+'\n'+out[-4000:])
    if result.returncode: raise AssertionError(out[-5000:])
    count=re.search(r'test result: ok\. (\d+) passed;',out)
    assert count and int(count.group(1))>0,'test filter matched no tests'
    print(out[-2200:],flush=True)


def run(target,name,mutations,mutation_filter=None,offset=0,limit=None):
    assert not target.is_relative_to(ROOT)
    with open('/tmp/narwhal3d-heavy-build.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        rust_test(CRATE,target,name)
        compile_crate(CRATE,target,'build')
        checked([sys.executable,'-B',str(ROOT/'spike/test_ai_primitives.py'),'--library',str(target/'debug/libnarwhal_automation.so'),
                 '--test','test_live_catalog_unchanged_and_one_use_approval'],timeout=30)
        if mutations:
            variants=[m for m in MUTATIONS if mutation_filter is None or mutation_filter in m[0]]
            variants=variants[offset:None if limit is None else offset+limit]
            assert variants,'no matching mutations'
            receipt=target/'v4-mutations.jsonl'
            source_hash=hashlib.sha256(b''.join(path.name.encode()+path.read_bytes() for path in sorted((CRATE/'src').glob('*.rs')))).hexdigest()
            for label,filename,old,new,test in variants:
                with tempfile.TemporaryDirectory(prefix='narwhal-ai-v4-mutant-') as tmp:
                    crate=Path(tmp);shutil.copytree(CRATE/'src',crate/'src')
                    for file in ('Cargo.toml','Cargo.lock'):shutil.copy2(CRATE/file,crate/file)
                    source=crate/'src'/filename;text=source.read_text()
                    record={'label':label,'test':test,'source_sha256':source_hash,'mutated_file':filename,'status':'INFRA_FAILURE'}
                    try:
                        assert text.count(old)==1,(label,'INFRA_FAILURE: anchor changed')
                        changed=text.replace(old,new,1);source.write_text(changed)
                        record['mutant_file_sha256']=hashlib.sha256(changed.encode()).hexdigest()
                        rust_test(crate,target/('mutant-'+label),test,red=True)
                        record['status']='KILLED'
                    except Exception as exc:
                        if str(exc).startswith('SURVIVED'):record['status']='SURVIVED'
                        raise
                    finally:
                        with receipt.open('a') as output:output.write(json.dumps(record,sort_keys=True)+'\n')
                    print('KILLED',label,flush=True)
            rust_test(CRATE,target,name)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--target-dir',type=Path,required=True);p.add_argument('--test',default='v4_')
    p.add_argument('--mutations',action='store_true')
    p.add_argument('--mutation-filter')
    p.add_argument('--offset',type=int,default=0);p.add_argument('--limit',type=int)
    args=p.parse_args();run(args.target_dir.resolve(),args.test,args.mutations,args.mutation_filter,args.offset,args.limit)
