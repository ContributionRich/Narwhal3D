#!/usr/bin/env python3
"""OURS: shell orchestration regression; Python drives disposable process fixtures.

No application imports, GUI, compiler, Cargo build, or production-source mutations.
Both real shell runners execute against fake runtimes in an external temporary tree.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PURE_ADDITIONS = (
    'test_curve_inputs.py', 'test_curve_consumer_inputs.py',
    'test_variable_edge_fillet.py', 'test_solid_commands.py',
    'test_colordialog.py', 'test_preferences.py', 'test_user_settings.py', 'test_window_overlay_patch.py',
    'test_windows_build.py', 'test_release_harness.py',
)
LIVE_SUITES = (
    'test_v4_live.py', 'test_shell_fixes_gui.py', 'test_render_quality_gui.py',
    'test_component_fillet_gui.py', 'test_preferences_gui.py',
    'test_preferences_gui.py', 'test_window_overlay_gui.py',
)
CORES = {
    'NARWHAL_PURE_CORE': 'native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so',
    'NARWHAL_GEOMETRY_CORE': 'native/geometry_core/target/release/libnarwhal_geometry_core.so',
    'NARWHAL_SELECTION_CORE': 'artifacts/selection-core/linux-x86_64/libnarwhal_selection_core.so',
    'NARWHAL_PLATFORM_CORE': 'native/platform_core/target/release/libnarwhal_platform_core.so',
    'NARWHAL_DOCUMENT_CORE': 'native/document_core/target/release/narwhal-document-core',
    'NARWHAL_CDR_CORE': 'native/cdr_core/target/release/libnarwhal_cdr_core.so',
    'NARWHAL_CDR_MULTIVERSION': 'native/cdr_multiversion/target/release/narwhal-cdr-multiversion',
    'NARWHAL_CONVERTER_SUPERVISOR': 'native/converter_supervisor/target/release/narwhal-converter-supervisor',
}
FAKE_RUNTIME = r'''
import fcntl
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
env = dict(os.environ)
row = dict(executable=sys.argv[0], args=args, env=env, cwd=os.getcwd())
live = '--enable-event-simulate' in args
if live:
    scripts = [args[i+1] for i, value in enumerate(args) if value == '--python']
    suite = next(Path(p).name for p in scripts if Path(p).name != 'bootstrap.py')
    phase = env.get('NARWHAL_PREF_TEST_PHASE')
    prefs = Path(env['NARWHAL3D_SETTINGS_FILE'])
    row.update(suite=suite, phase=phase, prefs=str(prefs), existed=prefs.exists(),
               previous=prefs.read_text() if prefs.exists() else None)
    with open(Path(env['TMPDIR']) / 'narwhal3d_gui.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            row['locked'] = False
        except BlockingIOError:
            row['locked'] = True
    prefs.write_text(json.dumps(dict(suite=suite, phase=phase)))
with open(env['HARNESS_CALLS'], 'a') as log:
    log.write(json.dumps(row) + '\n')
if live:
    marker = {
        'test_v4_live.py': 'V4_LIVE_RESULT',
        'test_shell_fixes_gui.py': 'SHELL_FIXES_RESULT',
        'test_render_quality_gui.py': 'RENDER_QUALITY_RESULT',
        'test_component_fillet_gui.py': 'COMPONENT_FILLET_RESULT',
        'test_preferences_gui.py': 'PREFERENCES_GUI_RESULT',
        'test_window_overlay_gui.py': 'WINDOW_OVERLAY_RESULT',
    }[suite]
    mode = env.get('HARNESS_FAILURE', '') if suite == env.get('HARNESS_FAIL_SUITE') else ''
    if mode != 'missing-marker':
        print(marker + ' ' + json.dumps(dict(phase=phase, status='failed' if mode == 'failed-result' else 'passed')))
    if mode == 'draw-error':
        print('panel failed: asynchronous fixture failure')
    if mode == 'viewport-error':
        print('viewport setup fixture failed')
    sys.exit(7 if mode == 'exit-error' else 0)
'''


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='narwhal-harness-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'fixture project'
        for name in ('spike', 'scripts', 'narwhal3d', 'launch', 'bin', 'scratch'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.calls = self.root / 'calls.jsonl'
        for rel in ('run_tests.sh', 'spike/v4_live_gui.sh'):
            shutil.copyfile(ROOT / rel, self.root / rel)
        fake = '#!' + sys.executable + '\n' + FAKE_RUNTIME
        for rel in ('tools/blender-patched/blender',
                    'tools/blender-patched/5.2/python/bin/python3.13',
                    'bin/cargo', 'scripts/bootstrap_dev_linux.sh', 'scripts/build_blender_linux.sh'):
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(fake)
            path.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(('NARWHAL', 'HARNESS_'))}
        self.env.update(PATH=str(self.root / 'bin') + os.pathsep + os.environ['PATH'],
                        TMPDIR=str(self.root / 'scratch'), HARNESS_CALLS=str(self.calls),
                        DISPLAY='fixture-only', WAYLAND_DISPLAY='', PYTHONDONTWRITEBYTECODE='1',
                        PYTHONPATH='/fixture/python', LD_LIBRARY_PATH='/fixture/libs',
                        NARWHAL3D_THEME='dark', NARWHAL3D_UI_READY='stale',
                        NARWHAL3D_WELCOME_REQUEST='stale', NARWHAL3D_PRESENT_WATCHDOG='stale',
                        NARWHAL_PREF_TEST_PHASE='read', NARWHAL3D_SETTINGS_FILE='/never/use/user.json')

    def execute(self, rel='spike/v4_live_gui.sh', **env):
        self.calls.unlink(missing_ok=True)
        result = subprocess.run(['bash', str(self.root / rel)], cwd=self.temp.name,
                                env=dict(self.env, **env), text=True, capture_output=True, timeout=30)
        rows = [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []
        return result, rows

    def mutate(self, rel, old, new):
        path = self.root / rel
        original = path.read_text()
        self.assertIn(old, original)
        path.write_text(original.replace(old, new, 1))
        return original

    def assert_geometry_env(self, env):
        self.assertEqual(env['PYTHONPATH'].split(os.pathsep)[0], str(self.root / 'vendor/occt'))
        self.assertEqual(env['LD_LIBRARY_PATH'].split(os.pathsep)[0], str(self.root /
            'tools/blender-patched/5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs'))
        for key in ('NARWHAL_PURE_CORE', 'NARWHAL_GEOMETRY_CORE'):
            self.assertEqual(env[key], str(self.root / CORES[key]))
        self.assertEqual(env['PYTHONDONTWRITEBYTECODE'], '1')

    def assert_live_contract(self, result, rows):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([r['suite'] for r in rows], list(LIVE_SUITES))
        for i, row in enumerate(rows):
            self.assertTrue(row['locked'], 'every launch must hold the shared GUI lock')
            self.assertEqual(row['cwd'], str(self.root))
            args = row['args']
            self.assertEqual(args[args.index('--python-exit-code') + 1], '1')
            scripts = [Path(args[j+1]).name for j, value in enumerate(args) if value == '--python']
            self.assertEqual(scripts, ['bootstrap.py', row['suite']] if i < 3 else [row['suite'], 'bootstrap.py'])
            self.assertEqual(row['phase'], {4: 'write', 5: 'read'}.get(i))
            self.assertEqual(row['existed'], i == 5, 'only preferences read may see previous settings')
            self.assertTrue(Path(row['prefs']).is_relative_to(self.root / 'scratch'))
            self.assertFalse(Path(row['prefs']).parent.exists(), 'disposable preferences must be cleaned up')
            for key in ('NARWHAL3D_THEME', 'NARWHAL3D_UI_READY', 'NARWHAL3D_WELCOME_REQUEST',
                        'NARWHAL3D_PRESENT_WATCHDOG'):
                self.assertNotIn(key, row['env'])
            self.assert_geometry_env(row['env'])
            for key, rel in CORES.items():
                self.assertEqual(row['env'][key], str(self.root / rel))
        self.assertEqual(rows[4]['prefs'], rows[5]['prefs'])
        self.assertEqual(json.loads(rows[5]['previous']), dict(suite='test_preferences_gui.py', phase='write'))
        self.assertFalse(list((self.root / 'scratch').glob('*.log')))

    def assert_pure_contract(self, result, rows):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in PURE_ADDITIONS:
            matches = [row for row in rows if row['args'] == ['spike/' + name]]
            self.assertEqual(len(matches), 1, name + ' must run exactly once with bundled Python')
            self.assertEqual(matches[0]['executable'], str(self.root / 'tools/blender-patched/5.2/python/bin/python3.13'))
            self.assert_geometry_env(matches[0]['env'])

    def test_live_dispatch_lock_environment_and_persistence(self):
        self.assert_live_contract(*self.execute())

    def test_pure_dispatch(self):
        self.assert_pure_contract(*self.execute('run_tests.sh', DISPLAY='', WAYLAND_DISPLAY=''))

    def test_no_display_never_launches(self):
        result, rows = self.execute(DISPLAY='', WAYLAND_DISPLAY='')
        self.assertEqual(result.returncode, 0)
        self.assertIn('SKIP:', result.stdout)
        self.assertEqual(rows, [])

    def test_live_failures_stop_and_clean_up(self):
        for mode in ('exit-error', 'missing-marker', 'failed-result', 'draw-error', 'viewport-error'):
            with self.subTest(mode=mode):
                result, rows = self.execute(HARNESS_FAILURE=mode, HARNESS_FAIL_SUITE='test_shell_fixes_gui.py')
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual([r['suite'] for r in rows], list(LIVE_SUITES[:2]))
                self.assertFalse(Path(rows[-1]['prefs']).parent.exists())

    def test_runner_mutations_go_red_then_restore(self):
        rel = 'spike/v4_live_gui.sh'
        mutations = (
            ('flock 9\n', ': # lock removed\n'),
            ('COMPONENT_FILLET_RESULT before-bootstrap', 'COMPONENT_FILLET_RESULT after-bootstrap'),
            ('PREFERENCES_GUI_RESULT before-bootstrap', 'PREFERENCES_GUI_RESULT after-bootstrap'),
            ('export NARWHAL_PREF_TEST_PHASE=read', 'rm -f "$NARWHAL3D_SETTINGS_FILE"\nexport NARWHAL_PREF_TEST_PHASE=read'),
            ('unset NARWHAL3D_UI_READY NARWHAL3D_PRESENT_WATCHDOG NARWHAL3D_THEME NARWHAL3D_WELCOME_REQUEST || true', ':'),
            ('export NARWHAL_GEOMETRY_CORE=', 'export WRONG_GEOMETRY_CORE='),
        )
        for old, new in mutations:
            with self.subTest(mutation=old):
                original = self.mutate(rel, old, new)
                try:
                    with self.assertRaises((AssertionError, KeyError)):
                        self.assert_live_contract(*self.execute())
                finally:
                    (self.root / rel).write_text(original)
        self.assert_live_contract(*self.execute())
        original = self.mutate('run_tests.sh', '    spike/test_curve_inputs.py\n', '')
        try:
            with self.assertRaises(AssertionError):
                self.assert_pure_contract(*self.execute('run_tests.sh', DISPLAY='', WAYLAND_DISPLAY=''))
        finally:
            (self.root / 'run_tests.sh').write_text(original)
        self.assert_pure_contract(*self.execute('run_tests.sh', DISPLAY='', WAYLAND_DISPLAY=''))


if __name__ == '__main__':
    unittest.main(verbosity=2)
