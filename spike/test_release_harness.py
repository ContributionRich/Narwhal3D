#!/usr/bin/env python3
"""OURS: shell orchestration regression; Python drives disposable process fixtures.

No application imports, GUI, compiler, Cargo build, or production-source mutations.
Both real shell runners execute against fake runtimes in an external temporary tree.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PURE_ADDITIONS = (
    'test_curve_inputs.py', 'test_curve_consumer_inputs.py',
    'test_variable_edge_fillet.py', 'test_solid_commands.py', 'test_fillet_handle_input.py',
    'test_colordialog.py', 'test_preferences.py', 'test_user_settings.py', 'test_window_overlay_patch.py',
    'test_windows_build.py', 'test_release_harness.py', 'test_meshed_master_transform.py',
    'test_automation_bridge.py', 'test_automation_host.py', 'test_ai_settings_popup.py',
    'test_extrude_preview.py', 'test_fillet_preview_latency.py',
    'test_surfacing_parallel.py', 'test_surfacing_probe.py',
)
LIVE_SUITES = (
    'test_v4_live.py', 'test_shell_fixes_gui.py', 'test_render_quality_gui.py',
    'test_component_fillet_gui.py', 'test_fillet_mouse_gui.py', 'test_preferences_gui.py',
    'test_preferences_gui.py', 'test_window_overlay_gui.py', 'test_gumball_extrude_scale_gui.py',
)
GPU_SUITE = 'test_extrude_preview_gpu.py'
HOST_SUITES = (
    ['spike/test_ai_settings_popup_gui.py', '--launch'],
    ['spike/test_preview_responsiveness_gui.py', '--run', 'integration'],
    ['spike/test_trim_box_gui.py', '--launch', '--baseline'],
)
NEW_TESTS = (
    'automation_bridge', 'automation_mcp', 'automation_live', 'automation_native',
    'automation_host', 'automation_startup', 'surfacing_parallel', 'surfacing_probe',
    'surfacing_integration', 'ai_settings_popup', 'ai_settings_popup_gui',
    'extrude_preview', 'extrude_preview_gpu', 'fillet_preview_latency',
    'preview_responsiveness_gui', 'trim_box_gui',
)
NEW_SOURCES = (
    'native/automation_core/Cargo.toml', 'native/automation_core/Cargo.lock',
    'native/automation_core/src/main.rs', 'native/automation_core/src/protocol.rs',
    'native/geometry_core/src/surfacing.rs', 'native/geometry_core/cpp/sweep_adapter.inc',
    'narwhal3d/automation_tools.py', 'narwhal3d/automation_bridge.py',
    'narwhal3d/automation_host.py', 'narwhal3d/ai_settings_popup.py',
    'narwhal3d/extrude_preview.py', 'narwhal3d/surfacing_backend.py', 'scripts/narwhal_mcp.py',
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
gpu = any(Path(p).name == 'test_extrude_preview_gpu.py' for p in args)
host = any(Path(p).name in ('test_ai_settings_popup_gui.py',
    'test_preview_responsiveness_gui.py', 'test_trim_box_gui.py') for p in args)
if host:
    row['locks_free'] = []
    for name in ('narwhal3d_gui.lock', 'narwhal3d-heavy-build.lock'):
        with open(Path(env['TMPDIR']) / name, 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                row['locks_free'].append(True)
            except BlockingIOError:
                row['locks_free'].append(False)
if live or gpu:
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
if live or gpu:
    marker = {
        'test_extrude_preview_gpu.py': 'EXTRUDE PREVIEW GPU PASSED',
        'test_v4_live.py': 'V4_LIVE_RESULT',
        'test_shell_fixes_gui.py': 'SHELL_FIXES_RESULT',
        'test_render_quality_gui.py': 'RENDER_QUALITY_RESULT',
        'test_component_fillet_gui.py': 'COMPONENT_FILLET_RESULT',
        'test_fillet_mouse_gui.py': 'FILLET_MOUSE_RESULT',
        'test_preferences_gui.py': 'PREFERENCES_GUI_RESULT',
        'test_window_overlay_gui.py': 'WINDOW_OVERLAY_RESULT',
        'test_gumball_extrude_scale_gui.py': 'GUMBALL_GUI_RESULT',
    }[suite]
    mode = env.get('HARNESS_FAILURE', '') if suite == env.get('HARNESS_FAIL_SUITE') else ''
    if mode != 'missing-marker':
        print((marker if mode != 'failed-result' else 'GPU FAILED') if gpu else
              marker + ' ' + json.dumps(dict(phase=phase, status='failed' if mode == 'failed-result' else 'passed')))
    if mode == 'draw-error':
        print('panel failed: asynchronous fixture failure')
    if mode == 'viewport-error':
        print('viewport setup fixture failed')
    sys.exit(7 if mode == 'exit-error' else 0)
if env.get('HARNESS_FAIL_SUITE') in args:
    sys.exit(7)
if Path(sys.argv[0]).name == 'rustc' and '-o' in args:
    output = Path(args[args.index('-o') + 1])
    output.write_text(Path(sys.argv[0]).read_text())
    output.chmod(0o755)
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
                    'bin/cargo', 'bin/rustc', 'scripts/bootstrap_dev_linux.sh',
                    'scripts/build_blender_linux.sh', 'uishot.sh', 'spike/startup_layout_gui.sh'):
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(fake)
            path.chmod(0o755)
        for rel in ('native/ui_core', 'vendor/occt-src/src',
                    'tools/blender-patched/5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs'):
            (self.root / rel).mkdir(parents=True, exist_ok=True)
        self.staged = self.root / 'artifacts/automation/linux-x86_64/narwhal-automation'
        self.staged.parent.mkdir(parents=True)
        self.staged.write_text('preserve the staged product server')
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(('NARWHAL', 'HARNESS_'))}
        self.env.update(PATH=str(self.root / 'bin') + os.pathsep + os.environ['PATH'],
                        TMPDIR=str(self.root / 'scratch'), HARNESS_CALLS=str(self.calls),
                        DISPLAY='fixture-only', WAYLAND_DISPLAY='', PYTHONDONTWRITEBYTECODE='1',
                        PYTHONPATH='/fixture/python', LD_LIBRARY_PATH='/fixture/libs',
                        NARWHAL3D_THEME='dark', NARWHAL3D_UI_READY='stale',
                        NARWHAL3D_WELCOME_REQUEST='stale', NARWHAL3D_PRESENT_WATCHDOG='stale',
                        NARWHAL_PREF_TEST_PHASE='read', NARWHAL3D_SETTINGS_FILE='/never/use/user.json',
                        NARWHAL_UI_CORE_GATE_TARGET=str(self.root / 'scratch/ui-core/target'))

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
        self.assertEqual([r['suite'] for r in rows], [*LIVE_SUITES, GPU_SUITE])
        for i, row in enumerate(rows):
            self.assertTrue(row['locked'], 'every launch must hold the shared GUI lock')
            self.assertEqual(row['cwd'], str(self.root))
            args = row['args']
            self.assertEqual(args[args.index('--python-exit-code') + 1], '1')
            scripts = [Path(args[j+1]).name for j, value in enumerate(args) if value == '--python']
            if row['suite'] == GPU_SUITE:
                self.assertEqual(scripts, [GPU_SUITE], 'GPU oracle must not bootstrap the app')
                self.assertNotIn('--enable-event-simulate', args)
                self.assertNotIn('-b', args)
                self.assertEqual(args[args.index('--threads') + 1], '2')
            else:
                self.assertEqual(scripts, ['bootstrap.py', row['suite']] if i < 3 else [row['suite'], 'bootstrap.py'])
            self.assertEqual(row['phase'], {5: 'write', 6: 'read'}.get(i))
            self.assertEqual(row['existed'], i == 6, 'only preferences read may see previous settings')
            self.assertTrue(Path(row['prefs']).is_relative_to(self.root / 'scratch'))
            self.assertFalse(Path(row['prefs']).parent.exists(), 'disposable preferences must be cleaned up')
            for key in ('NARWHAL3D_THEME', 'NARWHAL3D_UI_READY', 'NARWHAL3D_WELCOME_REQUEST',
                        'NARWHAL3D_PRESENT_WATCHDOG'):
                self.assertNotIn(key, row['env'])
            self.assert_geometry_env(row['env'])
            for key, rel in CORES.items():
                self.assertEqual(row['env'][key], str(self.root / rel))
        self.assertEqual(rows[5]['prefs'], rows[6]['prefs'])
        self.assertEqual(json.loads(rows[6]['previous']), dict(suite='test_preferences_gui.py', phase='write'))
        self.assertFalse(list((self.root / 'scratch').glob('*.log')))

    def assert_pure_contract(self, result, rows):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in PURE_ADDITIONS:
            matches = [row for row in rows if row['args'] == ['spike/' + name]]
            self.assertEqual(len(matches), 1, name + ' must run exactly once with bundled Python')
            self.assertEqual(matches[0]['executable'], str(self.root / 'tools/blender-patched/5.2/python/bin/python3.13'))
            self.assert_geometry_env(matches[0]['env'])

    def assert_source_contract(self, result, rows):
        self.assert_pure_contract(result, rows)
        commands = [row['args'] for row in rows]
        target = str(self.root / 'native/automation_core/target')
        server = target + '/debug/narwhal-automation'
        native = [args for args in commands if 'native/automation_core/Cargo.toml' in args]
        self.assertEqual(len(native), 2)
        ui_target = str(self.root / 'scratch/ui-core/target')
        ui_library = ui_target + '/release/libnarwhal_ui_core.so'
        gate = ['scripts/check_ui_core.py', '--target-dir', ui_target]
        self.assertEqual(commands.count(gate), 1)
        self.assertEqual(commands.count(['scripts/check_pure_core.py']), 1)
        self.assertLess(commands.index(['scripts/check_pure_core.py']), commands.index(gate))
        self.assertLess(commands.index(['scripts/check_application_core.py', '--stage-dev']), commands.index(gate))
        consumers = [row for row in rows if any(arg.startswith(('spike/test_ui_core_', 'spike/test_ui_transcript_',
                                                              'spike/test_desktop_native_startup.py'))
                                               and not arg.endswith('test_ui_core_gate.py') for arg in row['args'])]
        self.assertTrue(consumers)
        for row in consumers:
            self.assertGreater(commands.index(row['args']), commands.index(gate))
            self.assertEqual(row['env'].get('NARWHAL_UI_CORE_LIBRARY'), ui_library)
            application = str(self.root / 'artifacts/application-core/linux-x86_64/libnarwhal_application_core.so')
            self.assertEqual(row['env'].get('NARWHAL_APPLICATION_CORE'), application)
            self.assertEqual(row['env'].get('NARWHAL_HISTORY_CORE'), application)
        self.assertEqual([args[1] for args in native], ['test', 'build'])
        for args in native:
            self.assertIn('--locked', args)
            self.assertIn('--offline', args)
            self.assertEqual(args[args.index('--jobs') + 1], '2')
            self.assertEqual(args[args.index('--target-dir') + 1], target)
        protocol = ['spike/test_automation_mcp.py', '--server', server]
        live = [*protocol, '--live', str(self.root / 'tools/blender-patched/blender')]
        for args in (protocol, live):
            self.assertEqual(commands.count(args), 1)
            self.assertLess(commands.index(native[-1]), commands.index(args))
        integration = ['spike/test_surfacing_integration.py', '--require-native']
        self.assertEqual(commands.count(integration), 1)
        geometry = next(args for args in commands if args[:2] == ['build', '--release']
                        and 'native/geometry_core/Cargo.toml' in args)
        for args in (integration, ['spike/test_surfacing_parallel.py'], ['spike/test_surfacing_probe.py']):
            self.assertLess(commands.index(geometry), commands.index(args))
        forbidden = {'--mutations', '--timings', 'spike/test_automation_native.py',
                     'spike/test_automation_startup.py', 'spike/test_automation_live.py'}
        self.assertFalse(any(forbidden.intersection(args) for args in commands))
        self.assertEqual(self.staged.read_text(), 'preserve the staged product server')
        source = (self.root / 'run_tests.sh').read_text()
        self.assertIn('timeout --kill-after=5s 180s cargo', source)
        self.assertIn('timeout --kill-after=5s 60s "$PYTHON"', source)
        self.assertIn('timeout --kill-after=5s 120s "$PYTHON"', source)

    def assert_host_contract(self, result, rows):
        self.assert_source_contract(result, rows)
        calls = [row['args'] for row in rows]
        gpu_index = next(i for i, row in enumerate(rows) if row.get('suite') == GPU_SUITE)
        for args in HOST_SUITES:
            self.assertEqual(calls.count(args), 1)
            index = calls.index(args)
            self.assertGreater(index, gpu_index)
            self.assertEqual(rows[index]['locks_free'], [True, True], 'host must acquire its own locks')
            self.assertEqual(rows[index]['executable'], str(self.root / 'tools/blender-patched/5.2/python/bin/python3.13'))
        self.assertIn('SKIP: staged-product automation startup', result.stdout)

    def test_source_build_protocol_and_required_native_dispatch(self):
        self.assert_source_contract(*self.execute('run_tests.sh', DISPLAY='', WAYLAND_DISPLAY='', CARGO_BUILD_JOBS='2'))

    def test_standalone_hosts_run_after_v4_unlocks(self):
        self.assert_host_contract(*self.execute('run_tests.sh', CARGO_BUILD_JOBS='2'))

    def test_source_failures_do_not_claim_success(self):
        for name in ('scripts/check_pure_core.py', 'scripts/check_application_core.py', 'scripts/check_ui_core.py',
                     'native/automation_core/Cargo.toml', 'spike/test_automation_mcp.py',
                     'spike/test_surfacing_integration.py'):
            with self.subTest(name=name):
                result, rows = self.execute('run_tests.sh', DISPLAY='', WAYLAND_DISPLAY='', HARNESS_FAIL_SUITE=name)
                self.assertEqual(result.returncode, 7)
                self.assertNotIn('All public tests passed.', result.stdout)
                self.assertNotIn(['spike/test_trim_box_gui.py', '--launch', '--baseline'], [r['args'] for r in rows])

    def test_missing_cargo_is_explicit_refusal(self):
        self.mutate('run_tests.sh', 'command -v cargo >/dev/null 2>&1 ||', 'false ||')
        result, rows = self.execute('run_tests.sh', DISPLAY='', WAYLAND_DISPLAY='')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('ERROR: required native source compiler unavailable', result.stderr)
        self.assertNotIn('All public tests passed.', result.stdout)
        for name in ('scripts/check_pure_core.py', 'scripts/check_ui_core.py',
                     'spike/test_automation_mcp.py', 'spike/test_desktop_native_startup.py'):
            self.assertFalse(any(name in row['args'] for row in rows), name)

    def test_missing_ui_source_is_explicit_refusal(self):
        (self.root / 'native/ui_core').rmdir()
        result, rows = self.execute('run_tests.sh', DISPLAY='', WAYLAND_DISPLAY='')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('ERROR: required UI-core source/compiler unavailable', result.stderr)
        self.assertNotIn('All public tests passed.', result.stdout)
        self.assertFalse(any('spike/test_desktop_native_startup.py' in row['args'] for row in rows))

    def test_new_dispatch_mutations_go_red_then_restore(self):
        mutations = (
            ('export NARWHAL_UI_CORE_LIBRARY="$UI_CORE_LIBRARY"', 'export NARWHAL_UI_CORE_LIBRARY=/wrong/core.so'),
            ('run "Rust UI-core source gate" "$PYTHON" scripts/check_ui_core.py --target-dir "$UI_CORE_TARGET"', ':'),
            ('    spike/test_extrude_preview.py\n', ''),
            ('spike/test_surfacing_integration.py --require-native', 'spike/test_surfacing_integration.py'),
            ('--server "$AUTOMATION_SERVER"', '--server "$ROOT/artifacts/automation/linux-x86_64/narwhal-automation"'),
            ('spike/test_ai_settings_popup_gui.py --launch', 'spike/test_ai_settings_popup_gui.py'),
            ('    # These drivers acquire', '    exec 9>"${TMPDIR:-/tmp}/narwhal3d_gui.lock"\n    flock 9\n    # These drivers acquire'),
        )
        for old, new in mutations:
            with self.subTest(mutation=old):
                original = self.mutate('run_tests.sh', old, new)
                try:
                    with self.assertRaises(AssertionError):
                        self.assert_host_contract(*self.execute('run_tests.sh', CARGO_BUILD_JOBS='2'))
                finally:
                    (self.root / 'run_tests.sh').write_text(original)
        self.assert_host_contract(*self.execute('run_tests.sh', CARGO_BUILD_JOBS='2'))

    def test_gpu_verdict_is_required(self):
        for mode in ('exit-error', 'missing-marker', 'failed-result', 'draw-error'):
            with self.subTest(mode=mode):
                result, rows = self.execute(HARNESS_FAILURE=mode, HARNESS_FAIL_SUITE=GPU_SUITE)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(rows[-1]['suite'], GPU_SUITE)
                self.assertFalse(Path(rows[-1]['prefs']).parent.exists())

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


class PublicManifestTests(unittest.TestCase):
    def test_new_sources_tests_and_every_runner_payload_are_public(self):
        # runpy does not establish the script's sibling-import directory.
        scripts = str(ROOT / 'scripts')
        sys.path.insert(0, scripts)
        self.addCleanup(sys.path.remove, scripts)
        public = runpy.run_path(str(ROOT / 'scripts/create_public_branch.py'))
        audit = runpy.run_path(str(ROOT / 'scripts/audit_public_tree.py'))
        paths = set(public['public_files']())
        expected = {*NEW_SOURCES, *('spike/test_' + name + '.py' for name in NEW_TESTS)}
        self.assertTrue(expected <= paths, sorted(expected - paths))
        self.assertTrue(expected <= audit['REQUIRED'], sorted(expected - audit['REQUIRED']))
        for runner in ('run_tests.sh', 'spike/v4_live_gui.sh'):
            payloads = set(re.findall(r'spike/[a-zA-Z0-9_]+\.(?:py|sh)', (ROOT / runner).read_text()))
            self.assertTrue(payloads <= paths, sorted(payloads - paths))
        self.assertFalse(any('/target/' in path or path.startswith('artifacts/') for path in paths))
        self.assertNotIn('native/automation_core/README.md', paths)
        self.assertFalse(any(path.endswith('SWEEP1_PARALLEL.md') for path in paths))
        # Delete each required new source/test from a synthetic manifest: every omission is RED.
        entries = {name: ('100644', b'') for name in audit['REQUIRED']}
        for missing in sorted(expected):
            with self.subTest(missing=missing):
                candidate = dict(entries)
                del candidate[missing]
                self.assertIn('missing required public file: ' + missing, audit['audit_entries'](candidate))
        # The allowlist contract detects a dropped native include without editing live source.
        public['public_files'].__globals__['NATIVE_FILES'] = tuple(
            path for path in public['NATIVE_FILES'] if path != 'native/geometry_core/cpp/sweep_adapter.inc')
        with self.assertRaises(AssertionError):
            self.assertTrue(expected <= set(public['public_files']()))


if __name__ == '__main__':
    unittest.main(verbosity=2)
