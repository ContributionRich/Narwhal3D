#!/usr/bin/env python3
"""Gate/registration regressions and in-memory mutants; no Cargo, GUI or deployment.

Python is required to exercise the Python orchestration boundary. Synthetic ctypes
functions execute the SAME packaging probe text used by the real gate; no source
checkout, retained library, compiler cache or live module is mutated.
"""
from contextlib import ExitStack, nullcontext, redirect_stdout
import ctypes
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/check_ui_core.py'
SOURCE = SCRIPT.read_text()
sys.path.insert(0, str(ROOT / 'scripts'))
from test_release_ui_core import add_transcript_fixture

EXPORTS = ('narwhal_editor_abi', 'narwhal_editor_close', 'narwhal_editor_edit', 'narwhal_editor_read',
           'narwhal_popup_abi', 'narwhal_popup_apply', 'narwhal_popup_destroy', 'narwhal_popup_guards',
           'narwhal_popup_hit', 'narwhal_popup_panel', 'narwhal_popup_rows',
           'narwhal_transcript_abi', 'narwhal_transcript_apply', 'narwhal_transcript_capabilities',
           'narwhal_transcript_close', 'narwhal_transcript_read', 'narwhal_transcript_release',
           'narwhal_transcript_stats')


def load(source=SOURCE):
    spec = importlib.util.spec_from_file_location('ui_core_gate_under_test', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    exec(compile(source, str(SCRIPT), 'exec'), module.__dict__)
    return module


def fake_library(editor=1, popup=1, size=88, missing=(), read_status=0, close_status=0,
                 transcript=1, capabilities=7, request_bytes=40, reply_bytes=56, transcript_fault=''):
    lib = SimpleNamespace(**{name: lambda *args: 0 for name in EXPORTS})
    lib.narwhal_editor_abi = lambda: editor
    lib.narwhal_popup_abi = lambda: popup
    def edit(handle, op, value, flags, text, length, output, capacity):
        if capacity != size:
            return 1
        state = output._obj
        state.handle, state.revision, state.text_revision, state.length = 7, 1, 1, length
        return 0
    def read(handle, revision, start, output, count):
        output[0] = 65
        return read_status
    lib.narwhal_editor_edit = edit
    lib.narwhal_editor_read = read
    lib.narwhal_editor_close = lambda handle: close_status
    add_transcript_fixture(lib, transcript, capabilities, request_bytes, reply_bytes, transcript_fault)
    for name in missing:
        delattr(lib, name)
    return lib


class Fixture:
    def __init__(self, source=SOURCE):
        self.gate = load(source)

    def __enter__(self):
        self.stack = ExitStack()
        base = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='uig-')))
        self.root, self.target = base / 'source', base / 'target'
        self.crate = self.root / 'native/ui_core'
        for name in self.gate.REQUIRED:
            path = self.crate / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic ' + name)
        self.library = self.target / 'release' / self.gate.LIBRARY
        self.manifest = self.library.parent / self.gate.common.UI_CORE_SOURCE
        self.calls, self.fail, self.after = [], None, lambda step: None
        self.lib = fake_library()
        self.stack.enter_context(patch.object(self.gate.sys, 'platform', 'linux'))
        self.stack.enter_context(patch.object(self.gate.platform, 'machine', return_value='x86_64'))
        self.slot = self.stack.enter_context(patch.object(self.gate, 'build_slot', side_effect=nullcontext))
        self.run = self.stack.enter_context(patch.object(self.gate.subprocess, 'run', side_effect=self.fake_run))
        self.stack.enter_context(patch.object(ctypes, 'CDLL', side_effect=lambda path: self.lib))
        self.output = self.stack.enter_context(redirect_stdout(io.StringIO()))
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def fake_run(self, command, **kwargs):
        if 'cargo' in command:
            step = command[command.index('cargo') + 2]
            self.calls.append((step, command, kwargs))
            if step == self.fail:
                raise subprocess.CalledProcessError(23, command)
            if step == 'build':
                self.library.parent.mkdir(parents=True, exist_ok=True)
                self.library.write_bytes(b'new synthetic binary')
            self.after(step)
            return subprocess.CompletedProcess(command, 0)
        self.calls.append(('abi', command, kwargs))
        if self.fail == 'abi':
            raise subprocess.CalledProcessError(1, command)
        index = command.index('-c')
        out = io.StringIO()
        with patch.object(sys, 'argv', ['-c', *command[index + 2:]]), redirect_stdout(out):
            try:
                exec(compile(command[index + 1], 'actual-ui-core-probe', 'exec'), {})
            except Exception as exc:
                raise subprocess.CalledProcessError(1, command, stderr=str(exc)) from exc
        self.after('abi')
        return subprocess.CompletedProcess(command, 0, stdout=out.getvalue())

    def build(self):
        return self.gate.build(self.root, self.target)

    def receipt(self):
        self.library.parent.mkdir(parents=True, exist_ok=True)
        self.library.write_bytes(b'synthetic binary')
        receipt = dict(inputs=self.gate.source_inventory(self.root), sha256=self.gate.common.digest(self.library))
        self.manifest.write_text(json.dumps(receipt))
        return receipt


class GateTests(unittest.TestCase):
    source = SOURCE

    def fixture(self):
        return Fixture(self.source)

    def test_build_order_resources_and_manifest(self):
        with self.fixture() as f:
            library, identity = f.build()
            self.assertEqual([x[0] for x in f.calls], ['fmt', 'clippy', 'test', 'build', 'abi'])
            self.assertEqual(identity, dict(editor_abi=1, popup_abi=1, state_bytes=88,
                                            transcript_abi=1, transcript_capabilities=7,
                                            transcript_request_bytes=40, transcript_reply_bytes=56,
                                            exports=list(EXPORTS)))
            self.assertEqual(library, f.library)
            self.assertEqual(json.loads(f.manifest.read_text()),
                             dict(inputs=f.gate.source_inventory(f.root), sha256=f.gate.common.digest(library)))
            f.slot.assert_called_once()
            for step, command, kwargs in f.calls[:-1]:
                self.assertIn('MemoryMax=1G', command)
                self.assertIn('MemorySwapMax=0', command)
                self.assertIn('180s', command)
                self.assertIn('+1.95.0', command)
                self.assertTrue(kwargs['check'])
                self.assertEqual(kwargs['timeout'], 190)
                env = kwargs['env']
                self.assertEqual(env['CARGO_BUILD_JOBS'], '1')
                self.assertEqual(env['CARGO_INCREMENTAL'], '0')
                self.assertEqual(Path(env['TMPDIR']).parent, f.target.parent)
                self.assertNotIn('CARGO_BUILD_TARGET', env)
                if step != 'fmt':
                    for flag in ('--locked', '--offline', '--target-dir'):
                        self.assertIn(flag, command)
                    self.assertEqual(command[command.index('-j') + 1], '1')
                if step == 'clippy':
                    self.assertEqual(command[-3:], ['--', '-D', 'warnings'])
                    self.assertIn('--all-targets', command)
            self.assertIn('-I', f.calls[-1][1])
            self.assertIn('-B', f.calls[-1][1])
            self.assertEqual(f.calls[-1][2]['timeout'], 30)
            self.assertFalse((f.root / 'artifacts').exists())
            self.assertFalse((f.root / 'narwhal3d').exists())

    def test_all_failed_steps_invalidate_receipt(self):
        for step in ('fmt', 'clippy', 'test', 'build', 'abi'):
            with self.subTest(step=step), self.fixture() as f:
                f.receipt()
                f.fail = step
                with self.assertRaises((subprocess.SubprocessError, ValueError)):
                    f.build()
                self.assertFalse(f.manifest.exists())

    def test_every_missing_export_and_wrong_abi_state_rejected(self):
        cases = [dict(editor=2), dict(popup=2), dict(size=96), dict(read_status=1), dict(close_status=1),
                 dict(transcript=2), dict(capabilities=3), dict(request_bytes=48), dict(reply_bytes=64)]
        cases += [dict(transcript_fault=kind) for kind in ('append', 'bytes', 'release', 'close', 'huge-bytes')]
        cases += [dict(missing=(name,)) for name in EXPORTS]
        for options in cases:
            with self.subTest(options=options), self.fixture() as f:
                f.lib = fake_library(**options)
                with self.assertRaises(ValueError):
                    f.build()
                self.assertFalse(f.manifest.exists())

    def test_source_and_binary_drift_during_build(self):
        for kind in ('edit', 'add', 'delete', 'binary'):
            with self.subTest(kind=kind), self.fixture() as f:
                def drift(step):
                    if step == 'abi':
                        path = f.crate / 'src/editor.rs'
                        if kind == 'edit': path.write_text('changed')
                        elif kind == 'delete': path.unlink()
                        elif kind == 'add': (f.crate / 'src/new.rs').write_text('new')
                        else: f.library.write_bytes(b'changed')
                f.after = drift
                with self.assertRaises(ValueError): f.build()
                self.assertFalse(f.manifest.exists())

    def test_inventory_complete_and_ignores_only_build_caches(self):
        with self.fixture() as f:
            before = f.gate.source_inventory(f.root)
            self.assertEqual(len(before), 21)
            for name in ('EXTRA.md', 'EXTRA2.md', 'extra_generator.py', 'src/extra_tests.rs'):
                (f.crate / name).write_text('input')
            after = f.gate.source_inventory(f.root)
            self.assertEqual(len(after), len(before) + 4)
            for name in ('target/ignored.rs', '__pycache__/ignored.pyc', '.git/ignored'):
                path = f.crate / name
                path.parent.mkdir(exist_ok=True)
                path.write_text('ignored')
            self.assertEqual(after, f.gate.source_inventory(f.root))

    def test_every_required_source_is_mandatory_even_with_rehashed_receipt(self):
        required = load(self.source).REQUIRED
        self.assertTrue({'TRANSCRIPT.md', 'transcript_unicode.py', 'src/transcript.rs',
                         'src/transcript_ffi.rs', 'src/transcript_tests.rs'} <= set(required))
        for name in required:
            with self.subTest(source=name), self.fixture() as f:
                receipt = f.receipt()
                (f.crate / name).unlink()
                del receipt['inputs']['native/ui_core/' + name]
                f.manifest.write_text(json.dumps(receipt))
                with self.assertRaises(ValueError): f.gate.verify(f.root, f.library)
                with self.assertRaises(ValueError): f.build()
                self.assertEqual(f.calls, [])
                f.slot.assert_not_called()

    def test_missing_source_cannot_pass_even_with_empty_receipt(self):
        for empty in (False, True):
            with self.subTest(empty=empty), self.fixture() as f:
                receipt = f.receipt()
                shutil.rmtree(f.crate)
                if empty: f.crate.mkdir()
                receipt['inputs'] = {}
                f.manifest.write_text(json.dumps(receipt))
                with self.assertRaises(ValueError): f.gate.verify(f.root, f.library)
                with self.assertRaises(ValueError): f.build()
                self.assertEqual(f.calls, [])
                f.slot.assert_not_called()

    def test_bad_receipts_never_repaired_or_probed(self):
        for body in (None, '{broken', '{}', '[]', 'null', '{"inputs":{},"sha256":"wrong"}'):
            with self.subTest(body=body), self.fixture() as f:
                f.receipt()
                if body is None: f.manifest.unlink()
                else: f.manifest.write_text(body)
                with self.assertRaises(ValueError): f.gate.verify(f.root, f.library)
                self.assertEqual(f.calls, [])
                self.assertEqual(f.manifest.read_text() if f.manifest.exists() else None, body)

    def test_stale_source_and_binary_receipts_rejected(self):
        for kind in ('source', 'binary', 'extra'):
            with self.subTest(kind=kind), self.fixture() as f:
                receipt = f.receipt()
                if kind == 'source': (f.crate / 'src/popup.rs').write_text('changed')
                elif kind == 'binary': f.library.write_bytes(b'changed')
                else:
                    receipt['claimed_pass'] = True
                    f.manifest.write_text(json.dumps(receipt))
                with self.assertRaises(ValueError): f.gate.verify(f.root, f.library)
                self.assertEqual(f.calls, [])

    def test_verify_runs_probe_without_build_or_writes(self):
        with self.fixture() as f:
            f.receipt()
            before = f.manifest.read_bytes()
            self.assertEqual(f.gate.verify(f.root, f.library)['popup_abi'], 1)
            self.assertEqual([x[0] for x in f.calls], ['abi'])
            self.assertEqual(f.manifest.read_bytes(), before)
            f.slot.assert_not_called()

    def test_verify_drift_rejected(self):
        for kind in ('source', 'binary', 'receipt'):
            with self.subTest(kind=kind), self.fixture() as f:
                f.receipt()
                def drift(step):
                    path = {'source': f.crate / 'src/popup.rs', 'binary': f.library, 'receipt': f.manifest}[kind]
                    path.write_text('{}')
                f.after = drift
                with self.assertRaises(ValueError): f.gate.verify(f.root, f.library)

    def test_links_refused(self):
        for kind in ('source_file', 'source_dir', 'crate', 'manifest', 'library'):
            with self.subTest(kind=kind), self.fixture() as f:
                f.receipt()
                path = {'source_file': f.crate / 'src/editor.rs', 'source_dir': f.crate / 'src',
                        'crate': f.crate, 'manifest': f.manifest, 'library': f.library}[kind]
                moved = path.with_name(path.name + '-real')
                path.rename(moved)
                path.symlink_to(moved, target_is_directory=moved.is_dir())
                if kind in ('source_file', 'source_dir', 'crate'):
                    with self.assertRaises(ValueError): f.gate.source_inventory(f.root)
                with self.assertRaises(ValueError): f.gate.verify(f.root, f.library)

    def test_in_checkout_targets_refused_before_lock(self):
        with self.fixture() as f:
            for target in (f.root, f.crate / 'target', f.root.parent):
                with self.subTest(target=target), self.assertRaises(ValueError):
                    f.gate.build(f.root, target)
            f.slot.assert_not_called()
            self.assertEqual(f.calls, [])

    def test_atomic_receipt_failure_does_not_publish(self):
        with self.fixture() as f:
            f.receipt()
            with patch.object(f.gate.os, 'replace', side_effect=OSError('injected')):
                with self.assertRaises(OSError): f.build()
            self.assertEqual(list(f.library.parent.iterdir()), [f.library])

    def test_abi_only_explicitly_unqualified_and_source_free(self):
        with self.fixture() as f:
            f.receipt()
            shutil.rmtree(f.root)
            f.manifest.write_text('bad manifest intentionally ignored ONLY for ABI-only')
            before = f.manifest.read_text()
            self.assertEqual(f.gate.main(['--source-root', str(f.root), '--library', str(f.library), '--abi-only']), 0)
            self.assertIn('source NOT qualified', f.output.getvalue())
            self.assertNotIn('SOURCE GATE PASSED', f.output.getvalue())
            self.assertEqual(f.manifest.read_text(), before)
            self.assertEqual([x[0] for x in f.calls], ['abi'])
            f.slot.assert_not_called()

    def test_cli_build_verify_and_incompatible_flags(self):
        with self.fixture() as f:
            f.gate.main(['--source-root', str(f.root), '--target-dir', str(f.target)])
            self.assertIn('SOURCE GATE PASSED', f.output.getvalue())
            f.output.truncate(0); f.output.seek(0)
            f.gate.main(['--source-root', str(f.root), '--library', str(f.library)])
            self.assertIn('build/tests not rerun', f.output.getvalue())
            with patch.object(sys, 'stderr', io.StringIO()):
                for args in (['--abi-only'], ['--library', str(f.library), '--target-dir', str(f.target)], ['--stage-dev']):
                    with self.assertRaises(SystemExit) as exc: f.gate.main(args)
                    self.assertEqual(exc.exception.code, 2)


class LockTests(unittest.TestCase):
    def test_memory_and_lock_admission(self):
        import fcntl
        gate = load()
        for memory in ('1048576', '8388608'):
            with patch('builtins.open', return_value=io.StringIO()), patch.object(fcntl, 'flock') as lock, \
                 patch.object(Path, 'read_text', return_value=f'MemAvailable: {memory} kB\n'):
                if int(memory) < 5 * 1024**2:
                    with self.assertRaises(RuntimeError):
                        with gate.build_slot(): self.fail('low-memory build admitted')
                else:
                    with gate.build_slot(): pass
                lock.assert_called_once()
                self.assertEqual(lock.call_args.args[1], fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_lock_timeout_does_not_admit(self):
        import fcntl
        gate = load()
        with patch('builtins.open', return_value=io.StringIO()), \
             patch.object(fcntl, 'flock', side_effect=BlockingIOError), \
             patch.object(gate.time, 'monotonic', side_effect=[0, 61]):
            with self.assertRaises(TimeoutError):
                with gate.build_slot(): self.fail('busy lock admitted')

    def test_import_has_no_actions(self):
        with patch.object(subprocess, 'run', side_effect=AssertionError('spawn on import')):
            load()


class RegistrationTests(unittest.TestCase):
    def test_targeted_registration_and_required_core_are_executable(self):
        text = (ROOT / 'run_tests.sh').read_text()
        block = text.split('# UI core source qualification (no deployment).\n', 1)[1].split('# End UI core source qualification.\n', 1)[0]
        gui = text.split('    # UI core drivers own both locks; no outer flock.\n', 1)[1].split('    # End UI core GUI drivers.\n', 1)[0]
        with tempfile.TemporaryDirectory(prefix='uireg-') as directory:
            root = Path(directory)
            setup = 'set -eu\nunset NARWHAL_UI_CORE_LIBRARY\nNARWHAL_UI_CORE_GATE_TARGET=/var/tmp/nwug/target\nROOT=' + str(root) + '\nPYTHON=python-fixture\nrun() { printf "%s\\n" "$*"; }\ncargo() { :; }\n'
            for available, compiler in ((False, True), (True, False), (True, True)):
                if available: (root / 'native/ui_core').mkdir(parents=True, exist_ok=True)
                no_compiler = '' if compiler else 'command() { if [[ "$1" == "-v" && "$2" == "cargo" ]]; then return 1; fi; builtin command "$@"; }\n'
                result = subprocess.run(['bash', '-c', setup + no_compiler + block + '\nprintf "SOURCE_QUALIFIED\\n"\n' + gui + '\nprintf "CORE=%s\\n" "$NARWHAL_UI_CORE_LIBRARY"\n'],
                                        text=True, capture_output=True, timeout=10)
                output = result.stdout + result.stderr
                if not available or not compiler:
                    self.assertEqual(result.returncode, 2, output)
                    self.assertIn('ERROR: required UI-core', output)
                    self.assertNotIn('SKIP:', output)
                    self.assertNotIn('SOURCE_QUALIFIED', output)
                    self.assertNotIn('scripts/check_ui_core.py', output)
                    self.assertNotIn('--launch', output)
                else:
                    self.assertEqual(result.returncode, 0, output)
                    self.assertIn('SOURCE_QUALIFIED', output)
                    self.assertIn('CORE=/var/tmp/nwug/target/release/libnarwhal_ui_core.so', output)
                    for name in ('editor', 'popup', 'popup_failure', 'callers', 'layout'):
                        self.assertIn('spike/test_ui_core_' + name + '.py', output)
                    self.assertIn('scripts/check_ui_core.py --target-dir', output)
                    self.assertIn('scripts/check_ui_core.py --library', output)
                    self.assertEqual(output.count('--launch'), 3)
                    self.assertIn('spike/test_ui_core_popup_failure_gui.py --launch', output)
                    self.assertNotIn('--legacy', output)
                    self.assertNotIn('flock', output)
                    self.assertNotIn('--stage-dev', output)
        self.assertEqual(text.count('scripts/test_release_ui_core.py'), 1)
        self.assertEqual(text.count('spike/test_ui_core_gate.py'), 1)


class MutationTests(unittest.TestCase):
    def test_popup_failure_registration_mutants(self):
        harness = ROOT / 'run_tests.sh'
        text = harness.read_text()
        read = Path.read_text
        for old, new in (
            ('editor popup popup_failure callers layout', 'editor popup callers layout'),
            ('spike/test_ui_core_popup_failure_gui.py --launch', 'spike/test_ui_core_popup_gui.py --launch'),
            ('spike/test_ui_core_popup_gui.py --launch', 'spike/test_ui_core_popup_gui.py --launch --legacy'),
            ('export NARWHAL_UI_CORE_LIBRARY="$UI_CORE_LIBRARY"', ':'),
            ('    exit 2\nfi\n# End UI core source qualification.', '    :\nfi\n# End UI core source qualification.'),
        ):
            with self.subTest(registration=old):
                self.assertIn(old, text)
                mutated = text.replace(old, new)
                with patch.object(Path, 'read_text', lambda path, *a, **kw: mutated if path == harness else read(path, *a, **kw)):
                    result = unittest.TestResult()
                    RegistrationTests('test_targeted_registration_and_required_core_are_executable').run(result)
                self.assertTrue(result.failures, 'missing failure registration survived')
                self.assertFalse(result.errors)
                control = unittest.TestResult()
                RegistrationTests('test_targeted_registration_and_required_core_are_executable').run(control)
                self.assertTrue(control.wasSuccessful(), (control.errors, control.failures))
                print('RED registration mutant / GREEN restored:', old)

    def test_gate_mutants_are_red_then_restored_controls_green(self):
        mutants = (
            ('strict-clippy', "'--all-targets', '--', '-D', 'warnings'", "'--all-targets'", 'test_build_order_resources_and_manifest'),
            ('offline', "'--locked', '--offline', '--target-dir'", "'--locked', '--target-dir'", 'test_build_order_resources_and_manifest'),
            ('one-worker', "CARGO_BUILD_JOBS='1'", "CARGO_BUILD_JOBS='8'", 'test_build_order_resources_and_manifest'),
            ('no-swap', "'MemorySwapMax=0'", "'MemorySwapMax=1G'", 'test_build_order_resources_and_manifest'),
            ('source-pin', "if source_inventory(root) != receipt['inputs']:", 'if False:', 'test_source_and_binary_drift_during_build'),
            ('binary-pin', "if common.digest(library) != receipt['sha256']:", 'if False:', 'test_source_and_binary_drift_during_build'),
            ('probe-omitted', 'identity = abi_probe(library)', 'identity = common.UI_CORE_IDENTITY', 'test_every_missing_export_and_wrong_abi_state_rejected'),
            ('stale-receipt', 'receipt_path.unlink(missing_ok=True)', 'pass', 'test_all_failed_steps_invalidate_receipt'),
            ('missing-source', "if not (crate / name).is_file():", 'if False:', 'test_missing_source_cannot_pass_even_with_empty_receipt'),
            ('verify-drift', "    unchanged(root, library, receipt)\n    # Detect receipt replacement as well as source/binary drift during the probe.\n    if common.verify_ui_core_source(library.parent, root, library.name) != receipt:\n        raise ValueError('ui-core source receipt changed during qualification')", '    pass', 'test_verify_drift_rejected'),
            ('source-links', "if (Path(directory) / name).is_symlink():", 'if False:', 'test_links_refused'),
            ('false-provenance', 'source NOT qualified\'\n        else:', 'source qualified\'\n        else:', 'test_abi_only_explicitly_unqualified_and_source_free'),
        )
        for name, old, new, test in mutants:
            with self.subTest(mutant=name):
                self.assertIn(old, SOURCE)
                # Replace every occurrence when a guard is shared by build/verify.
                mutated = type('MutatedGate', (GateTests,), {'source': SOURCE.replace(old, new)})
                result = unittest.TestResult()
                mutated(test).run(result)
                self.assertTrue(result.failures, f'{name}: must die by assertion, not fixture/compile error; {result.errors}')
                control = unittest.TestResult()
                GateTests(test).run(control)
                self.assertTrue(control.wasSuccessful(), (name, control.errors, control.failures))
                print('RED mutant / GREEN restored:', name)


if __name__ == '__main__':
    unittest.main()
