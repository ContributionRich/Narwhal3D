#!/usr/bin/env python3
"""Isolated compiled mutations of the additive-box gate. Never edits live source."""
import argparse
import fcntl
from pathlib import Path
import shutil
import subprocess
import tempfile

import test_automation_native as driver

ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT / 'native/automation_core'
MUTATIONS = (
    ('explicit_approval', 'if !accepted {', 'if false {'),
    ('exact_context', 'if proposal.expected != current {', 'if false {'),
    ('exact_proposal', 'if self.pending.as_ref() != Some(&proposal) {', 'if false {'),
    ('positive_size', '*v <= 0.0', '*v < 0.0'),
)


def run(target):
    assert not target.is_relative_to(ROOT), 'mutation build target must be outside the project'
    def check(crate, build_target, red=False):
        command = driver.compile_command(crate, build_target, 'test', ('--lib', 'box_action::tests'))
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=180)
        output = result.stdout + result.stderr
        if red:
            assert result.returncode != 0 and 'test result: FAILED' in output, output[-2400:]
        else:
            assert result.returncode == 0 and '3 passed' in output, output[-2400:]
    with open('/tmp/narwhal3d-heavy-build.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        check(CRATE, target)
        source = (CRATE/'src/box_action.rs').read_text()
        with tempfile.TemporaryDirectory(prefix='narwhal-box-mutations-') as folder:
            for index, (label, old, new) in enumerate(MUTATIONS):
                assert source.count(old) == 1, 'mutation anchor changed: ' + label
                copy = Path(folder)/('crate-'+str(index))
                shutil.copytree(CRATE, copy, ignore=shutil.ignore_patterns('target', '.git'))
                (copy/'src/box_action.rs').write_text(source.replace(old, new, 1))
                check(copy, target/'mutation-artifacts', red=True)
                print(label + ': compiled RED', flush=True)
        check(CRATE, target)
        print('4 compiled box mutations RED -> source restored GREEN', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target-dir', type=Path, required=True)
    run(parser.parse_args().target_dir.resolve())
