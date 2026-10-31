"""Assertion-killed mutations in external copies only; driver owns compiler locking."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUST = [
    ('point_alignment', 'transcript.rs', '!input.len().is_multiple_of(4)', 'false'),
    ('release_owner', 'transcript_ffi.rs', 'reg.snapshots.get(&token).is_none_or(|s| s.owner != handle)', '!reg.snapshots.contains_key(&token)'),
    ('order', 'transcript.rs', 'self.lines.extend(incoming);', 'self.lines.extend(incoming.into_iter().rev());'),
    ('normalize', 'transcript.rs', 'if self.normalize {', 'if false {'),
    ('cap', 'transcript.rs', 'let drop_old = discard.min(self.lines.len());', 'let drop_old = 0;'),
    ('prefix', 'transcript.rs', '0..count.min(self.lines.len())', '0..count.saturating_sub(1).min(self.lines.len())'),
    ('sigma', 'transcript.rs', 'if final_sigma { 0x3c2 } else { 0x3c3 }', 'if false { 0x3c2 } else { 0x3c3 }'),
    ('revision', 'transcript.rs', 'self.revision += 1;', 'self.revision += 0;'),
    ('last_command', 'transcript.rs', '.rev()\n            .find(|line|', '.find(|line|'),
    ('fixed_reply', 'transcript_ffi.rs', 'output_bytes != std::mem::size_of::<Reply>()', 'false'),
    ('stale_mutation', 'transcript_ffi.rs', 'owner.revision != r.revision', 'false'),
    ('snapshot_owner', 'transcript_ffi.rs', 'snapshot.owner != handle || snapshot.revision != revision', 'snapshot.revision != revision'),
    ('snapshot_revision', 'transcript_ffi.rs', 'snapshot.owner != handle || snapshot.revision != revision', 'snapshot.owner != handle'),
    ('close_owner', 'transcript_ffi.rs', 'reg.owners.remove(&handle).is_none()', '!reg.owners.contains_key(&handle)'),
    ('close_snapshots', 'transcript_ffi.rs', 'reg.snapshots.retain(|_, s| s.owner != handle);', 'reg.snapshots.retain(|_, _| true);'),
]
PYTHON = [
    ('construction_cleanup', 'native_transcript.py', 'lib.narwhal_transcript_close(self.reply.handle)\n            raise', 'None\n            raise'),
    ('clone_cleanup', 'native_transcript.py', 'self.lib.narwhal_transcript_close(result.handle)\n            raise', 'None\n            raise'),
    ('required_native', 'native_transcript.py', "raise RuntimeError('transcript requires the native UI core')", 'return None'),
    ('required_unicode', 'command_transcript.py', "raise RuntimeError('transcript requires Unicode 15.1 or 16 native support')", 'pass'),
    ('close_status', 'native_transcript.py', "if rc not in (0, 2):\n                self.failed = True", "if rc not in (0, 2):\n                pass"),
    ('raised_ffi', 'native_transcript.py', 'except BaseException:\n            self.failed = True\n            raise  # The native mutation may already have committed.', 'except BaseException:\n            raise  # The native mutation may already have committed.'),
    ('raised_disposal', 'native_transcript.py', 'except BaseException:\n                self.failed = True\n                raise', 'except BaseException:\n                raise'),
    ('no_freeze', 'native_transcript.py', 'if self.failed:', 'if False:'),
    ('old_library', 'native_transcript.py', "raise RuntimeError('UI core lacks supported transcript ABI; rebuild the additive development library') from exc", 'return lib'),
    ('cache_revision', 'native_transcript.py', 'self.cache.clear()', 'None'),
    ('no_bounds', 'native_transcript.py', 'out.len <= MAX_LINES', 'True'),
    ('read_claim', 'native_transcript.py', 'rc != 0 or needed.value != snapshot.bytes or needed.value > len(buffer)', 'rc != 0'),
    ('clone_owner', 'command_transcript.py', 'other._native = self._native.clone()', 'other._native = self._native'),
    ('line_ffi_loop', 'command_transcript.py', 'return iter(self[:])', 'return (self[i] for i in range(len(self)))'),
    ('ignore_raw', 'command_transcript.py', 'self._native.command(1, data=native.encode_lines([line]))', "self._native.command(1, data=native.encode_lines([line.rstrip('\\r\\n')]))"),
]


def main():
    evidence = Path(tempfile.mkdtemp(prefix='tm-', dir='/var/tmp/nwp'))
    crate = evidence / 'ui_core'
    shutil.copytree(ROOT / 'native/ui_core', crate, ignore=shutil.ignore_patterns('target', '__pycache__', '.git'))
    source = evidence / 'narwhal3d'
    source.mkdir()
    for name in ('native_textfield.py', 'native_transcript.py', 'command_transcript.py', 'command_history.py'):
        shutil.copyfile(ROOT / 'narwhal3d' / name, source / name)
    env = dict(os.environ, TMPDIR=str(evidence), UI_TRANSCRIPT_SOURCE=str(evidence))
    control_lib = env['NARWHAL_UI_CORE_LIBRARY']
    results = []
    def run(label, command):
        if 'cargo' in command:
            available = int(next(row.split()[1] for row in Path('/proc/meminfo').read_text().splitlines()
                                 if row.startswith('MemAvailable:')))
            if available < 5 * 1024**2:
                raise RuntimeError('need 4 GiB desktop reserve + 1 GiB compiler allowance')
        with (evidence / (label+'.log')).open('w') as log:
            proc = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=120)
        return proc.returncode, (evidence / (label+'.log')).read_text(errors='replace')
    test = [sys.executable, '-B', str(ROOT / 'spike/test_ui_transcript_model.py')]
    assert run('control', test)[0] == 0
    common = ['--manifest-path', str(crate / 'Cargo.toml'), '--target-dir', str(evidence / 'target'), '-j', '1']
    compiler = ['systemd-run', '--user', '--scope', '--quiet', '-p', 'MemoryMax=1G', '-p', 'MemorySwapMax=0',
                'flock', '-w', '60', '/tmp/narwhal3d-heavy-build.lock', 'cargo']
    for label, filename, old, new in ([] if '--python-only' in sys.argv else RUST):
        path = crate / 'src' / filename
        original = path.read_text()
        assert old in original, (label, old)
        path.write_text(original.replace(old, new))
        try:
            rc, _ = run(label+'-build', compiler+['build', '--release']+common)
            assert rc == 0, f'{label}: compile failure is not a kill'
            env['NARWHAL_UI_CORE_LIBRARY'] = str(evidence / 'target/release/libnarwhal_ui_core.so')
            rust_rc, rust_log = run(label+'-rust', compiler+['test']+common+['transcript_'])
            py_rc, py_log = run(label+'-python', test)
            assertion = (rust_rc != 0 and 'panicked at' in rust_log and 'assertion' in rust_log) or (py_rc != 0 and 'FAIL:' in py_log and 'AssertionError' in py_log)
            assert assertion, f'{label}: survived or only infrastructure/unexpected exception; see {evidence}'
            results.append(dict(name=label, assertion_killed=True, rust_exit=rust_rc, python_exit=py_rc))
        finally:
            path.write_text(original)
        print(results[-1], flush=True)
    env['NARWHAL_UI_CORE_LIBRARY'] = control_lib
    for label, filename, old, new in PYTHON:
        path = source / filename
        original = path.read_text()
        assert old in original, (label, old)
        path.write_text(original.replace(old, new))
        try:
            rc, log = run(label+'-python', test)
            assert rc != 0 and 'FAIL:' in log and 'AssertionError' in log, f'{label}: no assertion kill; see {evidence}'
            results.append(dict(name=label, assertion_killed=True, python_exit=rc))
        finally:
            path.write_text(original)
        print(results[-1], flush=True)
    assert run('restored-python', test)[0] == 0
    if '--python-only' not in sys.argv:
        assert run('restored-rust', compiler+['test']+common+['transcript_'])[0] == 0
    (evidence / 'results.json').write_text(json.dumps(results, indent=2))
    print(len(results), 'assertion-killed; restored controls GREEN;', evidence)


if __name__ == '__main__':
    main()
