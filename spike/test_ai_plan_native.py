#!/usr/bin/env python3
"""Bounded native plan build/mutations under the shared actual-build lock."""
import argparse
import fcntl
from pathlib import Path
import shutil
import sys
import tempfile
from test_automation_native import ROOT, CRATE, checked, compile_crate

SCHEMA_MUTATIONS = [
    ('dependencies', 'if !prior.contains_key(dependency) || !dependencies.insert(dependency)',
     'if !dependencies.insert(dependency)', 'test_dependencies_are_ordered_unique_and_typed'),
    ('manufacturing', 'self.intent == Intent::Manufacturable',
     'false && self.intent == Intent::Manufacturable',
     'test_manufacturing_requires_exactness_and_airtightness_predicates'),
    ('budget', 'self.steps.len() > 16', 'self.steps.len() > 17', 'test_budgets_and_scalar_domains'),
    ('execution_claim', '"executed":false', '"executed":true', 'test_schema_is_not_execution_or_approval'),
    ('unknown_fields', '#[serde(deny_unknown_fields)]', '', 'test_schema_is_not_execution_or_approval'),
    ('failed_read_authority', 'if result.is_error {', 'if false {', 'test_preflight_failed_reads_and_unit_mismatch_cannot_supply_authority'),
    ('executor_availability', 'let available =', 'let available = true ||', 'test_preflight_unsupported_and_unseen_objects_stay_blocked'),
    ('honest_summary', 'No geometry created or verified.', 'Geometry created and verified.', 'test_dolphin_inspect_plan_loop_across_all_provider_eras'),
    ('clarification', 'if !self.questions.is_empty() || self.intent == Intent::Unspecified {', 'if false {', 'test_ambiguous_dolphin_asks_consequential_questions_without_box_substitution'),
    ('unit_scope', 'evidence.units.as_deref() != Some(self.units.as_str())', 'false', 'test_preflight_failed_reads_and_unit_mismatch_cannot_supply_authority'),
]


def run(target, mutations, mutation_filter=None):
    assert not target.is_relative_to(ROOT)
    with open('/tmp/narwhal3d-heavy-build.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        compile_crate(CRATE,target,'test');compile_crate(CRATE,target,'build')
        client=[sys.executable,'-B',str(ROOT/'spike/test_ai_plan.py'),'--library',str(target/'debug/libnarwhal_automation.so')]
        checked(client,timeout=30)
        if mutations:
            variants=[('plan.rs', mutation) for mutation in SCHEMA_MUTATIONS] + [
                ('verification.rs', ('public_prose_trust','"provider_text_is_geometry_evidence":false','"provider_text_is_geometry_evidence":true','test_provider_success_text_cannot_become_authoritative_action_evidence')),
                ('catalog.rs', ('disabled_tool', 'let name = canonical(&call.name).ok_or("unsupported_tool")?;', 'if call.name == "narwhal_create_sphere" { return Ok(()); }\n    let name = canonical(&call.name).ok_or("unsupported_tool")?;', 'test_new_primitive_and_direct_backend_names_cannot_be_called_by_provider')),
                ('verification.rs', ('prose_success', 'let confirmed = matches && receipt.is_some_and(|r| r.confirmed);', 'let confirmed = true;', 'test_provider_success_text_cannot_become_authoritative_action_evidence')),
                ('verification.rs', ('approval_receipt', 'let confirmed = matches && receipt.is_some_and(|r| r.confirmed);', 'let confirmed = matches;', 'test_verification_denied_failed_noop_and_forged_measurements_are_not_success')),
                ('verification.rs', ('post_action_read', '*read > r.request', '*read >= r.request', 'test_verification_requires_later_read_of_the_exact_created_object')),
                ('verification.rs', ('public_geometry_claim', '"current_geometry_verified":false', '"current_geometry_verified":true', 'test_verification_correlates_receipt_but_never_fabricates_fresh_geometry')),
                ('verification.rs', ('fresh_geometry_claim', '"post_action_object_observed":observed,\n            "current_geometry_verified":false', '"post_action_object_observed":observed,\n            "current_geometry_verified":true', 'test_verification_correlates_receipt_but_never_fabricates_fresh_geometry')),
            ]
            if mutation_filter:
                variants=[variant for variant in variants if mutation_filter in variant[1][0]]
                assert variants,'no matching mutation'
            for filename,(label,old,new,test) in variants:
                with tempfile.TemporaryDirectory(prefix='narwhal-ai-plan-mutation-') as tmp:
                    crate=Path(tmp);shutil.copytree(CRATE/'src',crate/'src')
                    for name in ('Cargo.toml','Cargo.lock'):shutil.copy2(CRATE/name,crate/name)
                    source=crate/'src'/filename;text=source.read_text()
                    assert old in text,(label,'anchor changed')
                    source.write_text(text.replace(old,new,1))
                    compile_crate(crate,target/'plan-mutations','build')
                    checked(client[:-1]+[str(target/'plan-mutations/debug/libnarwhal_automation.so'),'--test',test],timeout=30,expect_failure=True)
                    print('RED confirmed:',label,flush=True)
            checked(client,timeout=30)
        print('Native plan GREEN; no live mutation activation')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--target-dir',type=Path,required=True)
    p.add_argument('--mutations',action='store_true')
    p.add_argument('--mutation-filter')
    args=p.parse_args();run(args.target_dir.resolve(),args.mutations,args.mutation_filter)
