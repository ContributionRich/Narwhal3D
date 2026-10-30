#!/usr/bin/env python3
"""Bounded explicit-Responses regression/mutation runner; no artifact staging or GUI."""
import argparse
import fcntl
from pathlib import Path
import shutil
import sys
import tempfile
from test_automation_native import ROOT, CRATE, compile_crate, checked

MUTATIONS = (
    ("retention", "provider.rs", '"input":messages, "stream":false, "store":false', '"input":messages, "stream":false, "store":true', "test_real_http_mixed_replay_keeps_order_phase_and_encrypted_state"),
    ("call_id", "provider.rs", 'string(item, "call_id")?', 'string(item, "id")?', "test_tool_only_uses_call_id_not_replay_item_id"),
    ("item_duplicates", "provider.rs", "if !ids.insert(id.as_str())", "if false && !ids.insert(id.as_str())", "test_duplicate_item_and_call_ids_and_completed_call_replay_are_refused"),
    ("replay_privacy", "provider.rs", "#[serde(default, skip_serializing, skip_deserializing)]", "#[serde(default, skip_deserializing)]", "test_real_http_mixed_replay_keeps_order_phase_and_encrypted_state"),
    ("replay_budget", "conversation.rs", ".max(replay_bytes)", "", "test_opaque_replay_counts_toward_individual_and_total_output_bytes"),
    ("result_reservation", "conversation.rs", "messages.len() + result_slots > 60", "messages.len() > 60", "test_reply_reserves_space_for_correlated_tool_results"),
    ("input_nulls", "provider.rs", "if fields.get(*key).is_some_and(Value::is_null)", "if false && fields.get(*key).is_some_and(Value::is_null)", "test_output_nulls_are_omitted_only_where_input_schema_requires"),
    ("incomplete", "provider.rs", '"incomplete" => return Err("truncated_response"),', '"incomplete" => {},', "test_refusal_nonfinal_and_provider_errors_accept_no_calls_or_text"),
)


def run(target, mutations):
    assert not target.is_relative_to(ROOT)
    with open("/tmp/narwhal3d-heavy-build.lock", "a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        compile_crate(CRATE,target,"test");compile_crate(CRATE,target,"build")
        client=[sys.executable,"-B",str(ROOT/"spike/test_ai_responses.py"),"--library",str(target/"debug/libnarwhal_automation.so")]
        checked(client,timeout=45)
        if mutations:
            mutant_target=target/"responses-mutations"
            for label,file,old,new,test in MUTATIONS:
                with tempfile.TemporaryDirectory(prefix="narwhal-responses-mutation-") as tmp:
                    crate=Path(tmp);shutil.copytree(CRATE/"src",crate/"src")
                    for name in ("Cargo.toml","Cargo.lock"):shutil.copy2(CRATE/name,crate/name)
                    source=crate/"src"/file;text=source.read_text()
                    assert text.count(old)==1,(label,"mutation anchor changed")
                    source.write_text(text.replace(old,new,1))
                    compile_crate(crate,mutant_target,"build")
                    checked(client[:-1]+[str(mutant_target/"debug/libnarwhal_automation.so"),"--test",test],timeout=45,expect_failure=True)
                    print("RED confirmed:",label,flush=True)
            checked(client,timeout=45)
        print("Responses native GREEN; mutations",len(MUTATIONS) if mutations else 0)


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--target-dir",type=Path,required=True);p.add_argument("--mutations",action="store_true")
    a=p.parse_args();run(a.target_dir.resolve(),a.mutations)
