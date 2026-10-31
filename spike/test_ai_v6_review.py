#!/usr/bin/env python3
"""Focused V6 callback mutations and explicit legacy-fixture controls; no activation.

Python only orchestrates the existing locked, memory-bounded Rust driver and test
CLI. Mutations compile copied Rust sources outside the project, never live files.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys

from test_automation_native import ROOT
import test_ai_v4_native as native
from test_ai_accepted_review_native import TARGET, discover_executable

MUTATIONS = [
    ('v6_terminal_scope', 'conversation.rs', 'if active\n', 'if true\n',
     'v6_terminal_review_callbacks'),
    ('v6_active_scope', 'conversation.rs', 'if active\n', 'if false\n',
     'v6_active_review_owner'),
    ('v6_circle_pending', 'conversation.rs',
     '        self.proposals.check_pending(proposal.clone())?;\n', '',
     'v6_spent_circle_callback'),
    ('v6_circle_exact', 'actions.rs', 'if pending.proposal != proposal {',
     'if false {', 'v6_forged_circle'),
]


def fixture_controls(target, legacy_library=None):
    executable = discover_executable(target)
    command = [sys.executable, '-B', str(ROOT / 'spike/test_ai_accepted_review.py'),
               '--native-executable', str(executable), '--test',
               'test_old_library_reports_explicit_unavailability']
    env = os.environ.copy()
    env.pop('NARWHAL_AI_LEGACY_LIBRARY', None)

    def invoke(extra=(), environment=env):
        result = subprocess.run(command + list(extra), cwd=ROOT, env=environment,
                                capture_output=True, text=True, timeout=20)
        return result.returncode, result.stdout + result.stderr

    code, output = invoke()
    assert code == 0 and 'skipped=1' in output and '--legacy-library' in output, output
    print('PASS: absent legacy fixture explicitly skips only compatibility case', flush=True)
    # An explicitly selected current library is NOT a legacy fixture. This must
    # fail the compatibility assertion, never skip or consult installed artifacts.
    current = str(Path(target) / 'debug/libnarwhal_automation.so')
    code, output = invoke(('--legacy-library', current))
    assert code != 0 and 'FAIL:' in output and 'accepted_review_call_required' in output, output
    assert 'skipped=' not in output, output
    print('RED confirmed: current backend cannot masquerade as legacy fixture', flush=True)
    if legacy_library is not None:
        legacy = str(Path(legacy_library).resolve())
        for extra, environment in (
                (('--legacy-library', legacy), env),
                ((), dict(env, NARWHAL_AI_LEGACY_LIBRARY=legacy)),
                (('--legacy-library', legacy), dict(env, NARWHAL_AI_LEGACY_LIBRARY=current))):
            code, output = invoke(extra, environment)
            assert code == 0 and 'Ran 1 test' in output and 'skipped=' not in output, output
        print('PASS: explicit legacy CLI, environment and CLI precedence', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target-dir', type=Path, default=TARGET)
    parser.add_argument('--legacy-library', type=Path)
    parser.add_argument('--mutations', action='store_true')
    parser.add_argument('--fixture-only', action='store_true')
    args = parser.parse_args()
    target = args.target_dir.resolve()
    if not args.fixture_only:
        # The reused driver owns the lock; never acquire an outer copy of it.
        os.environ['NARWHAL_AI_BUILD_MIB'] = '1024'
        native.MUTATIONS = MUTATIONS
        native.run(target, 'v6_', args.mutations)
    fixture_controls(target, args.legacy_library)
