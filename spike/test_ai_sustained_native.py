#!/usr/bin/env python3
"""Focused sustained-lane native checks/mutations; no staging or full product gate."""
import argparse
import fcntl
from pathlib import Path
import shutil
import sys
import tempfile
from test_automation_native import ROOT, CRATE, compile_crate, checked


def run(target, group, mutate):
    assert not target.is_relative_to(ROOT)
    cases = {
        'terminal': ['test_01_terminal_paths_discard_same_replay_and_abandoned_turn',
                     'test_02_terminal_paths_revoke_box_but_do_not_reset_budgets_or_call_ids'],
        'capacity': ['test_03_near_capacity_result_terminates_instead_of_leaving_calls_pending'],
        'matrix': ['test_04_budgets_and_invalid_provider_output_end_whole_exchange'],
        'ids': ['test_05_result_id_errors_are_precise_retryable_and_do_not_consume_new_requests', 'test_05_old_box_callback_cannot_reset_new_context'],
        'diagnostics': ['test_07_public_diagnostics_are_bounded_precise_and_not_connection_state'],
        'catalog': ['test_12_catalog_matches_executor_and_instruction_boundary', 'test_10_prepared_recipient_cannot_follow_later_config_mutation'],
    }[group]
    variants = {
        'terminal': [('completion_expiry', 'let pending = self.pending.take().unwrap();\n        if Instant::now() >= pending.deadline {\n            self.abandon_turn();',
                      'let pending = self.pending.take().unwrap();\n        if Instant::now() >= pending.deadline {', cases[0]),
                     ('transport_terminal', 'if failure.is_some() {\n            self.abandon_turn();',
                      'if failure.is_some() {', cases[0])],
        'capacity': [('result_capacity', 'self.abandon_turn();\n            return Err("context_budget");',
                      'return Err("context_budget");', cases[0])],
        'matrix': [('failed_reply_cleanup','Err(code) => {\n                self.abandon_turn();','Err(code) => {',cases[0])],
        'ids': [('old_box_binding', '// A late decision belongs', 'if binding != self.binding { self.rebind(binding.clone())?; }\n        // A late decision belongs', 'test_05_old_box_callback_cannot_reset_new_context')],
        'diagnostics': [('private_state', '"connection_state":"stateless_http"', '"connection_state":"stateless_http","history":self.history', cases[0])],
        'catalog': [('box_truth', 'Only the separately approved Box/Cube action can currently create geometry in the in-app assistant;', 'Production modeling dispatch is currently unavailable;', cases[0])],
    }[group]
    with open('/tmp/narwhal3d-heavy-build.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        compile_crate(CRATE,target,'test');compile_crate(CRATE,target,'build')
        client=[sys.executable,'-B',str(ROOT/'spike/test_ai_sustained_lifecycle.py'),'--library',str(target/'debug/libnarwhal_automation.so')]
        for case in cases: checked(client+['--test',case],timeout=30)
        if mutate:
            mutant_target=target/'sustained-mutations'
            for label,old,new,case in variants:
                with tempfile.TemporaryDirectory(prefix='narwhal-ai-sustained-mutation-') as tmp:
                    crate=Path(tmp);shutil.copytree(CRATE/'src',crate/'src')
                    for name in ('Cargo.toml','Cargo.lock'):shutil.copy2(CRATE/name,crate/name)
                    source=crate/'src/conversation.rs';text=source.read_text()
                    assert text.count(old)==1,(label,'mutation anchor changed')
                    source.write_text(text.replace(old,new,1))
                    compile_crate(crate,mutant_target,'build')
                    checked(client[:-1]+[str(mutant_target/'debug/libnarwhal_automation.so'),'--test',case],timeout=30,expect_failure=True)
                    print('RED confirmed:',label,flush=True)
            for case in cases: checked(client+['--test',case],timeout=30)
        print('Sustained packet group GREEN:',group)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--target-dir',type=Path,required=True)
    p.add_argument('--group',choices=('terminal','capacity','matrix','ids','diagnostics','catalog'),required=True)
    p.add_argument('--mutations',action='store_true')
    a=p.parse_args();run(a.target_dir.resolve(),a.group,a.mutations)
