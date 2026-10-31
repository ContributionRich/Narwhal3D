"""Meaningful popup mutants in a private disk-backed copy, never shared sources.

Requires NARWHAL_UI_CORE_LIBRARY for the initial/final control. One compiler job,
1 GiB/no swap and heavy lock for each build/test. No target/third-party copies.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUST = [
    ('fractional_gutter', 'popup.rs', 'content - body_height > 1e-6 * s.max(1.0)', 'content > body_height'),
    ('anchor', 'popup.rs', 'self.anchor.1 - 3.0 * s', 'self.anchor.1 + 3.0 * s'),
    ('closed_release', 'popup.rs', 'consumed: guarded,', 'consumed: false,'),
    ('release_clear', 'popup.rs', 'self.guards.remove(key);', 'self.guards.contains(key);'),
    ('matching_press', 'popup.rs', 'p.0 == key && Some(p.1) == hit', 'p.0 == key'),
    ('resize_press', 'popup.rs', 'self.size = (w, h);\n            self.pressed = None;', 'self.size = (w, h);'),
    ('scroll', 'popup.rs', 'clamp(p.offset + delta, 0.0, p.max_scroll)', 'clamp(p.offset + delta, 0.0, 0.0)'),
    ('submenu_flip', 'popup.rs', 'parent.rect[0] - width', 'parent.rect[2]'),
    ('metric_replay', 'popup.rs', 'let label = get(i as i64, 0)?;', 'let _ = get(i as i64, 0)?; let label = get(i as i64, 0)?;'),
    ('layout_revision', 'popup.rs', 'self.layout_revision += 1;', 'self.layout_revision += 0;'),
    ('stale_read', 'popup_ffi.rs', 'if p.revision != revision {', 'if false {'),
    ('reply_capacity', 'popup_ffi.rs', 'output_bytes != std::mem::size_of::<Reply>()', 'false'),
]
PYTHON = [
    ('reentrant_disposal', 'native_popup.py',
     "if self._busy:\n            raise RuntimeError('recursive popup disposal during measurement; not replayed')",
     'if False: pass'),
    ('retained_payload', 'native_popup.py', "self._flat, self._title = (), ''", 'pass'),
    ('row_reprojection_copy', 'native_popup.py', 'self.rows = owner._row_bindings[key]',
     'self.rows = owner._flat[state.start:state.start + state.count]'),
    ('native_disposal', 'native_popup.py', 'self._finalizer()', 'pass'),
    ('ffi_exception_live_owner', 'native_popup.py',
     'except BaseException:\n            self._busy = False\n            self.failed = True\n            self.dispose()\n            raise',
     'except BaseException:\n            raise'),
    ('factory_legacy', 'native_popup.py', 'return NativePopup(window_size, scale, measure, lib=lib)',
     'return legacy(window_size, scale, measure)'),
    ('disabled_dispatch', 'native_popup.py', "not row.get('enabled', True) or action is None", 'action is None'),
    ('callback_twice', 'native_popup.py', 'callback(None if unavailable else action, explanation)',
     'callback(None if unavailable else action, explanation); callback(None if unavailable else action, explanation)'),
    ('resize_request', 'native_popup.py', 'self._apply(3, x=w, y=h)', 'self._apply(5)'),
    ('selected_projection', 'native_popup.py', 'return None if p.selected == -1 else p.selected', 'return None'),
    ('guard_transfer', 'native_popup.py', 'self._apply(7, source=other.handle)', 'pass'),
]


def main():
    out = Path(tempfile.mkdtemp(prefix='pm-', dir='/var/tmp/nwp'))
    print('POPUP_MUTATION_EVIDENCE', out, flush=True)
    crate = out / 'ui_core'
    shutil.copytree(ROOT / 'native/ui_core', crate, ignore=shutil.ignore_patterns('target', '__pycache__'))
    source = out / 'narwhal3d'
    source.mkdir()
    for file in ('native_popup.py', 'native_textfield.py', 'shell_popup.py'):
        shutil.copyfile(ROOT / 'narwhal3d' / file, source / file)
    env = dict(os.environ, TMPDIR=str(out), UI_CORE_POPUP_SOURCE=str(out), PYTHONDONTWRITEBYTECODE='1')
    control = env['NARWHAL_UI_CORE_LIBRARY']
    target = out / 'target'
    common = ['--release', '--manifest-path', str(crate / 'Cargo.toml'), '--target-dir', str(target), '-j', '1']
    compiler = ['systemd-run', '--user', '--scope', '--quiet', '-p', 'MemoryMax=1G',
                '-p', 'MemorySwapMax=0', 'flock', '-w', '60', '/tmp/narwhal3d-heavy-build.lock', 'cargo']
    test = [sys.executable, '-B', str(ROOT / 'spike/test_ui_core_popup.py')]
    receipts = []

    def run(name, command):
        with (out / (name + '.log')).open('w') as log:
            rc = subprocess.run(command, env=env, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=120).returncode
        return rc, (out / (name + '.log')).read_text()

    assert run('initial', test)[0] == 0, out
    for name, file, old, new in RUST:
        path = crate / 'src' / file
        original = path.read_text()
        assert original.count(old) == 1, (name, 'mutation target not unique')
        path.write_text(original.replace(old, new))
        try:
            assert run(name + '-build', compiler + ['build'] + common)[0] == 0, (name, 'compile is NOT a kill')
            env['NARWHAL_UI_CORE_LIBRARY'] = str(target / 'release/libnarwhal_ui_core.so')
            py_rc, py_log = run(name + '-python', test)
            rust_rc, rust_log = run(name + '-rust', compiler + ['test'] + common + ['--lib'])
            assert (py_rc and 'FAIL:' in py_log) or (rust_rc and 'assertion' in rust_log), (name, 'no assertion kill', out)
            receipts.append(dict(name=name, result='RED', python=py_rc, rust=rust_rc))
        finally:
            path.write_text(original)
        print(receipts[-1], flush=True)
    env['NARWHAL_UI_CORE_LIBRARY'] = control
    for name, file, old, new in PYTHON:
        path = source / file
        original = path.read_text()
        assert original.count(old) == 1, (name, 'mutation target not unique')
        path.write_text(original.replace(old, new))
        try:
            rc, log = run(name + '-python', test)
            assert rc and 'FAIL:' in log, (name, 'no assertion kill', out)
            receipts.append(dict(name=name, result='RED', python=rc))
        finally:
            path.write_text(original)
        print(receipts[-1], flush=True)
    assert run('restored-build', compiler + ['build'] + common)[0] == 0
    env['NARWHAL_UI_CORE_LIBRARY'] = str(target / 'release/libnarwhal_ui_core.so')
    assert run('restored-rust', compiler + ['test'] + common)[0] == 0
    assert run('restored-python', test)[0] == 0
    (out / 'results.json').write_text(json.dumps(receipts, indent=2))
    print(len(receipts), 'mutants RED; restored Rust/Python GREEN', out, flush=True)


def failure_mutations():
    """Small Python integration mutants only; reuse the supplied native artifact."""
    out = Path(tempfile.mkdtemp(prefix='pfm-', dir='/var/tmp/nwp'))
    source = out / 'narwhal3d'
    source.mkdir()
    for file in ('native_popup.py', 'native_textfield.py', 'shell_popup.py'):
        shutil.copyfile(ROOT / 'narwhal3d' / file, source / file)
    env = dict(os.environ, TMPDIR=str(out), UI_CORE_POPUP_SOURCE=str(out), PYTHONDONTWRITEBYTECODE='1')
    test = [sys.executable, '-B', str(ROOT / 'spike/test_ui_core_popup_failure.py')]
    mutants = [
        ('identity', 'shell_popup.py', 'if _model is model:', 'if True:'),
        ('pending_guards', 'shell_popup.py', '_failure_releases.update(releases)', 'pass'),
        ('failing_press', 'shell_popup.py', 'releases.add(event.type)', 'pass'),
        ('one_shot_release', 'shell_popup.py', '_failure_releases.discard(event.type)', 'pass'),
        ('release_in_flight', 'shell_popup.py', 'releases.discard(event.type)', 'pass'),
        ('native_guard_snapshot', 'shell_popup.py', 'releases = set(model._releases)', 'releases = set()'),
        ('draw_handler', 'shell_popup.py', '_safe_draw(bpy.context)', 'draw(bpy.context)'),
        ('window_draw_handler', 'shell_popup.py', '_safe_draw(bpy.context, window_level=True)', 'draw(bpy.context, window_level=True)'),
        ('event_escapes', 'shell_popup.py', '_detach_failed(model, releases, exc)\n        return True', 'raise'),
        ('failure_clickthrough', 'shell_popup.py', '_detach_failed(model, releases, exc)\n        return True', '_detach_failed(model, releases, exc)\n        return False'),
        ('report', 'shell_popup.py', 'ui.app_state.log(message)', 'pass'),
        ('row_bound', 'native_popup.py', 'if start + len(copied) >= MAX_ROWS:', 'if False:'),
    ]
    receipts = []
    def run(name):
        with (out / (name + '.log')).open('w') as log:
            rc = subprocess.run(test, env=env, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=20).returncode
        return rc, (out / (name + '.log')).read_text()
    print('POPUP_FAILURE_MUTATIONS', out, flush=True)
    assert run('initial')[0] == 0, out
    for name, file, old, new in mutants:
        path = source / file
        original = path.read_text()
        assert old in original, (name, 'missing target')
        path.write_text(original.replace(old, new))
        try:
            rc, log = run(name)
            assert rc and 'FAIL:' in log, (name, 'no assertion kill', out)
            receipts.append(dict(name=name, result='RED'))
        finally:
            path.write_text(original)
        print(receipts[-1], flush=True)
    assert run('restored')[0] == 0, out
    (out / 'results.json').write_text(json.dumps(receipts, indent=2))
    print(len(receipts), 'failure mutants RED; restored GREEN', out, flush=True)


if __name__ == '__main__':
    if '--failure-only' in sys.argv:
        failure_mutations()
    else:
        main()
