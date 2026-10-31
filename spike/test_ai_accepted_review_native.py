#!/usr/bin/env python3
"""Test-only persistent V4 native adapter; no HTTP, Blender launch or product switch.

Build/discover + focused local checks (never the full suite):
    python3 -B spike/test_ai_accepted_review_native.py --build --self-test
Discovery only, after building:
    python3 -B spike/test_ai_accepted_review_native.py --discover

Main's Blender tests import this helper (Python is only ctypes/process glue):
    with NativeBridge(executable) as bridge:
        with patch.object(ai, '_load_workflow_library', return_value=bridge.library):
            with ai.WorkflowSession(state, document_id, allow_reads=True) as session:
                ...  # unchanged binding/main-thread/_exchange and actual publisher

No automatic build on import/discovery/context entry. The executable comes from
Cargo's compiler-artifact JSON. Discovery binds all Rust/Cargo inputs and both
native artifact hashes and revalidates explicit executables before launch. Old
unhashed records require an explicit rebuild. Build holds the shared heavy lock;
existing-executable clients do not acquire it, so a lock-owning Main can use them.
The native process has a 120s external lifetime, 10s RPC deadline, 4096 messages,
512 MiB address-space cap, bounded frames and fail-closed pipe cleanup. Context
exit sends EOF and reaps it. Only the Rust test + explicit env + exact selector
can enable the fixture. Catalog requests still return the production catalog.
"""
from __future__ import annotations

import argparse
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import threading
import tempfile
import time
from unittest.mock import patch

from test_automation_native import ROOT, CRATE, compile_command, checked

TARGET = Path('/tmp/narwhal3d-ai-sustained-v4')
TEST_NAME = 'v4_tests::v4_main_publisher_bridge'
FIXTURE_ENV = 'NARWHAL_TEST_V4_PUBLISHER_BRIDGE'
PREFIX = b'NARWHAL_V4_PUBLISHER_JSON:'
MAX_INPUT = 1_048_576
OUTPUT_CAPACITY = 524_288
MAX_FRAME = MAX_INPUT * 4 + 128
MAX_RESPONSE = OUTPUT_CAPACITY * 6 + 256
MAX_MESSAGES = 4096


def _target(path):
    path = Path(path).resolve()
    if path.is_relative_to(ROOT):
        raise ValueError('Build output must be outside the project')
    return path


def _headroom(mib):
    available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                         if line.startswith('MemAvailable:')))
    if available < (4096 + mib) * 1024:
        raise RuntimeError(f'Need 4 GiB desktop reserve + {mib} MiB process headroom')


def build_bridge(target=TARGET):
    """One-job, 1024 MiB, offline, locked library/test-only builds. Return exact artifact."""
    target = _target(target)
    with open('/tmp/narwhal3d-heavy-build.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        source_hash = _source_hash()
        with patch.dict(os.environ, NARWHAL_AI_BUILD_MIB='1024'):
            command = compile_command(CRATE, target, 'test',
                                      ('--lib', '--no-run', '--message-format=json'))
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                    stdin=subprocess.DEVNULL, timeout=180)
            if result.returncode:
                raise RuntimeError((result.stdout + result.stderr)[-8000:])
            artifacts = []
            for line in result.stdout.splitlines():
                if not line.startswith('{'):
                    continue
                row = json.loads(line)
                if (row.get('reason') == 'compiler-artifact' and row.get('profile', {}).get('test')
                        and row.get('executable')
                        and row.get('target', {}).get('name') == 'narwhal_automation'
                        and 'rlib' in row.get('target', {}).get('kind', [])):
                    artifacts.append(Path(row['executable']).resolve())
            if len(artifacts) != 1 or not artifacts[0].is_relative_to(target):
                raise RuntimeError(f'Expected one library test executable, got {artifacts}')
            checked(compile_command(CRATE, target, 'build', ('--lib',)))
        executable = artifacts[0]
        row = dict(version=1, executable=str(executable), test=TEST_NAME,
                   source_sha256=source_hash, executable_sha256=_file_hash(executable),
                   library_sha256=_file_hash(target / 'debug/libnarwhal_automation.so'))
        if _source_hash() != source_hash:
            raise RuntimeError('Native inputs changed during build; rebuild explicitly')
        # Atomic publication prevents readers from accepting a partially written record.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=target, prefix='.publisher-', delete=False) as output:
                temporary = Path(output.name)
                output.write(json.dumps(row, sort_keys=True, indent=2) + '\n')
            os.replace(temporary, target / 'v4-publisher-bridge.json')
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return discover_executable(target)


def _check_executable(executable):
    result = subprocess.run([str(executable), '--list', '--exact', TEST_NAME],
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=5)
    if result.returncode or f'{TEST_NAME}: test'.encode() not in result.stdout:
        raise RuntimeError('Not the V4 publisher library test executable')


def _file_hash(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def _source_hash():
    # Relative names and hashes bind additions/removals/renames, not just contents.
    paths = {CRATE / 'Cargo.toml', CRATE / 'Cargo.lock',
             *CRATE.glob('*.rs'), *(CRATE / 'src').rglob('*.rs')}
    inventory = {path.relative_to(CRATE).as_posix(): _file_hash(path) for path in paths}
    return hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _record_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate discovery record field')
        result[key] = value
    return result


def _validate_discovery(target, expected=None):
    """Check trusted local build provenance without executing or rebuilding anything.

    This detects stale/corrupt local outputs, not malicious rewrites of both the
    artifacts and their writable receipt. It is not a signed release attestation.
    """
    target = _target(target)
    with (target / 'v4-publisher-bridge.json').open('rb') as source:
        raw = source.read(8193)
    if len(raw) > 8192:
        raise ValueError('Discovery record exceeds size limit')
    row = json.loads(raw, object_pairs_hook=_record_object)
    keys = {'version', 'test', 'executable', 'source_sha256', 'executable_sha256', 'library_sha256'}
    if (type(row) is not dict or set(row) != keys or type(row['version']) is not int
            or row['version'] != 1 or row['test'] != TEST_NAME or type(row['executable']) is not str):
        raise ValueError('Invalid or obsolete discovery record; rebuild explicitly')
    for key in ('source_sha256', 'executable_sha256', 'library_sha256'):
        digest = row[key]
        if type(digest) is not str or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Invalid discovery hash: ' + key)
    executable = Path(row['executable']).resolve()
    library = target / 'debug/libnarwhal_automation.so'
    if (str(executable) != row['executable'] or executable.parent != target / 'debug/deps'
            or library.resolve() != library or (expected is not None and executable != expected)):
        raise ValueError('Discovery artifact path mismatch')
    if row['source_sha256'] != _source_hash():
        raise RuntimeError('Stale native source/Cargo discovery hash; rebuild explicitly')
    if row['executable_sha256'] != _file_hash(executable):
        raise RuntimeError('Stale native test executable discovery hash; rebuild explicitly')
    if row['library_sha256'] != _file_hash(library):
        raise RuntimeError('Stale normal library discovery hash; rebuild explicitly')
    return executable


def discover_executable(target=TARGET):
    executable = _validate_discovery(target)
    _check_executable(executable)
    return executable


def _integer(value):
    return int(value.value if hasattr(value, 'value') else value)


class NativeLibrary:
    """Trusted, test-only CDLL-shaped adapter. No workflow methods are replaced."""
    def __init__(self, bridge):
        self.bridge = bridge

    @staticmethod
    def narwhal_workflow_abi():
        return 1

    def narwhal_workflow_json(self, handle, input, length, output, capacity):
        handle, length, capacity = map(_integer, (handle, length, capacity))
        # Exactly the native preflight: never consume an operation on a small buffer.
        # Callers still own valid, nonoverlapping memory, as for the real C ABI.
        if (input is None or output is None or length < 0 or length > MAX_INPUT
                or capacity < OUTPUT_CAPACITY
                or not ctypes.cast(input, ctypes.c_void_p).value
                or not ctypes.cast(output, ctypes.c_void_p).value):
            return -1
        raw = ctypes.string_at(input, length)
        size, data = self.bridge._exchange_bytes(handle, raw)
        if size < 0:
            return size
        if not 0 < size <= OUTPUT_CAPACITY or len(data) != size or size > capacity:
            raise RuntimeError('Invalid native bridge output size')
        ctypes.memmove(output, data, size)
        return size


class NativeBridge:
    """Context manager owning one persistent native process and its library adapter."""
    def __init__(self, executable=None, *, target=TARGET, timeout=120, rpc_timeout=10,
                 max_messages=MAX_MESSAGES):
        if not 0 < rpc_timeout <= timeout <= 120 or not 0 < max_messages <= MAX_MESSAGES:
            raise ValueError('Bridge bounds may only be tightened')
        self.executable = Path(executable) if executable is not None else None
        self.target = target
        self.timeout, self.rpc_timeout, self.max_messages = timeout, rpc_timeout, max_messages
        self.library = NativeLibrary(self)
        self.process = None
        self.messages = 0
        self._buffer = bytearray()
        self._lock = threading.Lock()

    def __enter__(self):
        if self.process is not None:
            raise RuntimeError('Bridge context cannot be entered twice')
        if self.executable is None:
            executable = _validate_discovery(self.target)
        else:
            expected = self.executable.resolve()
            # Explicit Cargo artifacts retain the existing custom-target API: infer
            # their target root, then require that root's exact discovery receipt.
            if len(expected.parents) < 3:
                raise ValueError('Invalid native test executable path')
            executable = _validate_discovery(expected.parents[2], expected=expected)
        _check_executable(executable)
        _headroom(512)
        env = os.environ.copy()
        env[FIXTURE_ENV] = '1'
        command = ['timeout', '--signal=TERM', '--kill-after=2s', f'{self.timeout}s',
                   'prlimit', '--as=536870912', '--cpu=30', '--', str(executable),
                   '--exact', TEST_NAME, '--nocapture', '--test-threads=1']
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, env=env, bufsize=0)
        os.set_blocking(self.process.stdin.fileno(), False)
        os.set_blocking(self.process.stdout.fileno(), False)
        self._deadline = time.monotonic() + self.timeout
        return self

    def _remaining(self, deadline):
        remaining = min(deadline, self._deadline) - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Native bridge deadline exceeded; request must not be retried')
        return remaining

    def _line(self, deadline):
        while True:
            newline = self._buffer.find(b'\n')
            if newline >= 0:
                if newline > MAX_RESPONSE:
                    raise RuntimeError('Native bridge response frame too large')
                line = bytes(self._buffer[:newline])
                del self._buffer[:newline + 1]
                return line
            if len(self._buffer) > MAX_RESPONSE:
                raise RuntimeError('Native bridge response frame too large')
            if not select.select([self.process.stdout], [], [], self._remaining(deadline))[0]:
                raise TimeoutError('Native bridge response timeout')
            data = os.read(self.process.stdout.fileno(), 65536)
            if not data:
                raise RuntimeError('Native bridge exited before a response')
            self._buffer.extend(data)

    def _exchange_bytes(self, handle, raw):
        with self._lock:
            if self.process is None or self.process.poll() is not None:
                raise RuntimeError('Native bridge is not running')
            if len(raw) > MAX_INPUT or not 0 <= handle < 2**64:
                raise ValueError('Native bridge input bound')
            if self.messages >= self.max_messages:
                raise RuntimeError('Native bridge message budget exhausted')
            frame = json.dumps({'handle': handle, 'data': list(raw)}, separators=(',', ':')).encode() + b'\n'
            if len(frame) > MAX_FRAME:
                raise ValueError('Native bridge frame bound')
            deadline = time.monotonic() + self.rpc_timeout
            try:
                self.messages += 1
                pending = memoryview(frame)
                while pending:
                    if not select.select([], [self.process.stdin], [], self._remaining(deadline))[1]:
                        raise TimeoutError('Native bridge write timeout')
                    pending = pending[os.write(self.process.stdin.fileno(), pending):]
                # libtest progress is not protocol. Bound even nonprotocol chatter.
                for _ in range(32):
                    line = self._line(deadline)
                    if line.startswith(PREFIX):
                        reply = json.loads(line[len(PREFIX):])
                        size, data = reply['size'], reply['data'].encode('utf-8')
                        if (type(size) is not int or size < -1 or size > OUTPUT_CAPACITY
                                or (size == -1 and data) or (size >= 0 and size != len(data))):
                            raise RuntimeError('Malformed native response')
                        return size, data
                raise RuntimeError('Native bridge protocol prefix missing')
            except BaseException:
                self.close()
                raise

    def rpc(self, handle, data):
        """Low-level diagnostic convenience; application tests should use .library."""
        if not isinstance(data, (bytes, str)):
            data = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        raw = data.encode('utf-8') if isinstance(data, str) else data
        size, result = self._exchange_bytes(_integer(handle), raw)
        if size < 0:
            raise RuntimeError('Native ABI rejected request')
        return json.loads(result)

    def close(self):
        process = self.process
        if process is None:
            return
        if not process.stdin.closed:
            process.stdin.close()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            # GNU timeout forwards TERM to its owned process group.
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        process.stdout.close()

    def __exit__(self, exc_type, exc, traceback):
        self.close()


def discovery_self_test(mutations=False):
    """Disposable source/artifact fixtures only; never mutate qualified outputs."""
    import unittest

    class DiscoveryControls(unittest.TestCase):
        def setUp(self):
            scratch = tempfile.TemporaryDirectory(prefix='narwhal-ai-discovery-')
            self.addCleanup(scratch.cleanup)
            self.crate = Path(scratch.name) / 'crate'
            self.target = Path(scratch.name) / 'target'
            self.inputs = {'Cargo.toml': b'manifest', 'Cargo.lock': b'lockfile',
                           'src/lib.rs': b'library source', 'src/nested/review.rs': b'review source'}
            for name, data in self.inputs.items():
                path = self.crate / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            self.exe = self.target / 'debug/deps/narwhal_automation-fixture'
            self.exe.parent.mkdir(parents=True)
            self.exe.write_bytes(b'test executable')
            self.library = self.target / 'debug/libnarwhal_automation.so'
            self.library.write_bytes(b'normal library')
            inventory = {name: hashlib.sha256(data).hexdigest() for name, data in self.inputs.items()}
            self.row = dict(version=1, test=TEST_NAME, executable=str(self.exe),
                            source_sha256=hashlib.sha256(json.dumps(inventory, sort_keys=True,
                                separators=(',', ':')).encode()).hexdigest(),
                            executable_sha256=hashlib.sha256(self.exe.read_bytes()).hexdigest(),
                            library_sha256=hashlib.sha256(self.library.read_bytes()).hexdigest())
            self.record = self.target / 'v4-publisher-bridge.json'
            self.record.write_text(json.dumps(self.row))
            self.enterContext(patch(__name__ + '.CRATE', self.crate))
            self.check = self.enterContext(patch(__name__ + '._check_executable'))
            self.build = self.enterContext(patch(__name__ + '.build_bridge', side_effect=AssertionError('automatic build')))
            self.launch = self.enterContext(patch.object(subprocess, 'Popen', side_effect=AssertionError('unverified launch')))

        def refused(self, explicit=False):
            self.check.reset_mock()
            with self.assertRaises((RuntimeError, ValueError, OSError)):
                if explicit:
                    NativeBridge(self.exe).__enter__()
                else:
                    discover_executable(self.target)
            self.check.assert_not_called()
            self.build.assert_not_called()
            self.launch.assert_not_called()

        def test_valid_discovery_is_read_only_and_never_builds(self):
            before = self.record.read_bytes()
            self.assertEqual(discover_executable(self.target), self.exe)
            self.assertEqual(self.record.read_bytes(), before)
            self.check.assert_called_once_with(self.exe)
            self.build.assert_not_called()
            self.launch.assert_not_called()

        def test_source_and_cargo_content_changes_are_rejected(self):
            for name, data in self.inputs.items():
                with self.subTest(name=name):
                    path = self.crate / name
                    path.write_bytes(bytes([data[0] ^ 1]) + data[1:])
                    self.refused()
                    path.write_bytes(data)

        def test_added_removed_and_renamed_rust_sources_are_rejected(self):
            path = self.crate / 'src/nested/review.rs'
            renamed = path.with_name('renamed.rs')
            path.rename(renamed)
            self.refused()
            renamed.rename(path)
            data = path.read_bytes()
            path.unlink()
            self.refused()
            path.write_bytes(data)
            extra = path.with_name('extra.rs')
            extra.write_bytes(b'new source')
            self.refused()

        def test_executable_bytes_are_verified_before_any_execution(self):
            self.exe.write_bytes(b'Test executable')
            self.refused()

        def test_normal_library_bytes_are_verified_before_any_execution(self):
            self.library.write_bytes(b'Normal library')
            self.refused()

        def test_missing_or_redirected_artifacts_are_rejected(self):
            for path in (self.exe, self.library):
                with self.subTest(path=path):
                    data = path.read_bytes()
                    path.unlink()
                    self.refused()
                    outside = self.crate / 'replacement'
                    outside.write_bytes(data)
                    path.symlink_to(outside)
                    self.refused()
                    path.unlink()
                    path.write_bytes(data)

        def test_explicit_path_must_match_record_before_execution(self):
            other = self.exe.with_name('other-test-executable')
            other.write_bytes(self.exe.read_bytes())
            with self.assertRaises(ValueError):
                NativeBridge(other).__enter__()
            self.check.assert_not_called()
            self.launch.assert_not_called()
            self.build.assert_not_called()

        def test_records_are_strict_and_bound_to_expected_paths(self):
            variants = [dict(executable=str(self.exe), test=TEST_NAME), [],
                        dict(self.row, version=True), dict(self.row, extra='untrusted'),
                        dict(self.row, test='another_test'), dict(self.row, source_sha256='0' * 64),
                        dict(self.row, executable_sha256='0' * 64), dict(self.row, library_sha256='0' * 64),
                        dict(self.row, executable=str(self.library)),
                        dict(self.row, executable=str(self.crate / 'src/lib.rs'))]
            for row in variants:
                with self.subTest(row=row):
                    self.record.write_text(json.dumps(row))
                    self.refused()
            for raw in ('{', json.dumps(self.row)[:-1] + ',"version":1}', ' ' * 8193):
                self.record.write_text(raw)
                self.refused()
            self.record.unlink()
            self.refused()

        def test_explicit_executable_revalidates_after_client_construction(self):
            bridge = NativeBridge(self.exe)
            self.library.write_bytes(b'Normal library')
            with self.assertRaises((RuntimeError, ValueError, OSError)):
                bridge.__enter__()
            self.check.assert_not_called()
            self.launch.assert_not_called()
            self.build.assert_not_called()

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DiscoveryControls)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise AssertionError('Discovery controls failed')
    if mutations:
        import inspect
        source = inspect.getsource(_validate_discovery)
        for field, test in (
                ('source_sha256', 'test_source_and_cargo_content_changes_are_rejected'),
                ('executable_sha256', 'test_executable_bytes_are_verified_before_any_execution'),
                ('library_sha256', 'test_normal_library_bytes_are_verified_before_any_execution')):
            line = next(line for line in source.splitlines() if line.strip().startswith(f"if row['{field}'] !="))
            namespace = dict(globals())
            exec(source.replace(line, '    if False:', 1), namespace)
            with patch(__name__ + '._validate_discovery', namespace['_validate_discovery']):
                red = unittest.TestResult()
                DiscoveryControls(test).run(red)
            assert red.failures and not red.errors, (field, 'mutation survived or infrastructure failure', red.errors)
            print('KILLED discovery hash mutation:', field, flush=True)
        restored = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(DiscoveryControls))
        assert restored.wasSuccessful(), 'Restored discovery controls failed'


def self_test(executable, target=TARGET):
    """Transport/parser controls only; no fabricated publication or human approval."""
    import unittest
    import test_ai_sustained_lifecycle as lifecycle
    ai = lifecycle.base.ai
    discovery_self_test(mutations=True)
    _validate_discovery(target, expected=Path(executable).resolve())

    class Controls(unittest.TestCase):
        def setUp(self):
            self.bridge = NativeBridge(executable).__enter__()
            self.addCleanup(self.bridge.close)

        def test_buffer_preflight_before_consumption(self):
            raw = b'{"op":"catalog"}'
            output = ctypes.create_string_buffer(OUTPUT_CAPACITY)
            call = self.bridge.library.narwhal_workflow_json
            for source, length, dest, capacity in (
                    (raw, len(raw), output, OUTPUT_CAPACITY - 1),
                    (raw, MAX_INPUT + 1, output, OUTPUT_CAPACITY),
                    (None, 0, output, OUTPUT_CAPACITY),
                    (raw, len(raw), None, OUTPUT_CAPACITY)):
                self.assertEqual(call(0, source, length, dest, capacity), -1)
                self.assertEqual(self.bridge.messages, 0)
            self.assertGreater(call(ctypes.c_uint64(0), raw, ctypes.c_size_t(len(raw)),
                                    output, ctypes.c_size_t(OUTPUT_CAPACITY)), 0)
            self.assertEqual(json.loads(output.value)['status'], 'catalog')

        def test_raw_data_handles_diagnostics_and_budget(self):
            for raw, code in ((b'{"op":"state","op":"catalog"}', 'invalid_json'),
                              (b'\xff', 'invalid_utf8'),
                              (b'{"op":"state"}', 'unknown_session')):
                response = self.bridge.rpc(2**63, raw)
                self.assertEqual(response['code'], code)
                self.assertIn('diagnostic', response)
            self.bridge.max_messages = self.bridge.messages
            with self.assertRaisesRegex(RuntimeError, 'budget'):
                self.bridge.rpc(0, {'op': 'catalog'})

        def test_idle_and_partial_frame_lifetime_is_externally_bounded(self):
            for partial in (b'', b'{"handle":0,"data":['):
                with NativeBridge(executable, timeout=.2, rpc_timeout=.1) as bounded:
                    if partial:
                        os.write(bounded.process.stdin.fileno(), partial)
                    self.assertNotEqual(bounded.process.wait(timeout=3), 0)
                    with self.assertRaisesRegex(RuntimeError, 'not running'):
                        bounded.rpc(0, {'op': 'catalog'})
                self.assertIsNotNone(bounded.process.returncode)

        def test_normal_library_ignores_test_env_and_rejects_primitive_calls(self):
            with patch.dict(os.environ, {FIXTURE_ENV: '1'}):
                for shape in ('sphere', 'cylinder', 'cone'):
                    state = ai.AssistantState(provider=ai.set_provider(
                        'openai_compatible', 'fixture', credential_env='TEST_KEY'))
                    with ai.WorkflowSession(state, lifecycle.DOC, allow_reads=True,
                            library_path=Path(target) / 'debug/libnarwhal_automation.so') as w:
                        p = w.prepare(lifecycle.DOC, user_text='local rejection fixture')
                        args = dict(center=[11, 23, 37], radius=4)
                        if shape != 'sphere':
                            args['height'] = 7
                        reply = lifecycle.finish(w, p, 'openai_compatible', text='', calls=[
                            ('disabled-call', 'narwhal_create_' + shape, args)])
                        self.assertEqual(reply['status'], 'failed', reply)
                        self.assertEqual(reply['code'], 'unsupported_tool', reply)

        def test_actual_parsers_review_and_unchanged_workflow_gate(self):
            with patch.object(ai, '_load_workflow_library', return_value=self.bridge.library):
                for provider in lifecycle.PROVIDERS:
                    for shape in ('sphere', 'cylinder', 'cone'):
                        with lifecycle.session(provider) as (_state, w):
                            p = w.prepare(lifecycle.DOC, user_text='local parser fixture only')
                            args = dict(center=[11, 23, 37], radius=4)
                            if shape != 'sphere':
                                args['height'] = 7
                            reply = lifecycle.finish(w, p, provider, text='', calls=[
                                ('actual-call', 'narwhal_create_' + shape, args)])
                            self.assertEqual(reply['status'], 'tool_calls', reply)
                            current = dict(append=lifecycle.box_context(), undo_top_serial=0, undo_next_serial=1)
                            with self.assertRaises(ai.WorkflowUnavailable):
                                w._exchange(dict(op='stage_accepted_review', binding=w._binding(),
                                    owner=p.data['token'], call_id='never-parsed', context=current))
                            review = w._exchange(dict(op='stage_accepted_review', binding=w._binding(),
                                owner=p.data['token'], call_id='actual-call', context=current))['review']
                            self.assertEqual(review['action']['operation'], shape)
                            self.assertEqual(review['action']['arguments'], args)
                            with self.assertRaisesRegex(ai.WorkflowUnavailable, 'review_approval_required'):
                                w._exchange(dict(op='consume_accepted_review', binding=w._binding(),
                                    token=review['token'], current=current))
                            self.assertNotIn('narwhal_create_sphere',
                                             {t['name'] for t in w.operation_catalog()['tools']})
                            errors = []
                            before = self.bridge.messages
                            def worker():
                                try:
                                    w.public_workflow_state()
                                except ai.WorkflowUnavailable as exc:
                                    errors.append(str(exc))
                            thread = threading.Thread(target=worker)
                            thread.start(); thread.join(timeout=2)
                            self.assertEqual(errors, ['workflow_requires_main_thread'])
                            self.assertEqual(self.bridge.messages, before)
                        self.assertEqual(self.bridge.rpc(p.data['token']['conversation'],
                                                         {'op': 'state'})['code'], 'unknown_session')

    # An open pipe (not just EOF) must never block an ordinary suite invocation.
    for args, enabled in ((['--exact', TEST_NAME, '--nocapture'], False),
                          (['v4_main_publisher', '--nocapture'], True),
                          (['--exact', TEST_NAME, '--nocapture'], True)):
        env = os.environ.copy()
        env.pop(FIXTURE_ENV, None)
        if enabled:
            env[FIXTURE_ENV] = '1'
        _validate_discovery(target, expected=Path(executable).resolve())
        with subprocess.Popen([str(executable), *args], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env) as proc:
            if enabled and '--exact' in args:
                proc.stdin.close()  # active fixture exits cleanly on EOF
            proc.wait(timeout=5)
            output = proc.stdout.read()
            assert proc.returncode == 0 and PREFIX not in output, output
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Controls)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise AssertionError('Bridge controls failed')
    # In-memory mutation only: safe full allocation, but lie about caller capacity.
    # The preflight regression must catch the operation being consumed.
    original = NativeLibrary.narwhal_workflow_json
    def ignore_capacity(self, handle, source, length, dest, capacity):
        return original(self, handle, source, length, dest, OUTPUT_CAPACITY)
    with patch.object(NativeLibrary, 'narwhal_workflow_json', ignore_capacity):
        red = unittest.TestResult()
        Controls('test_buffer_preflight_before_consumption').run(red)
        assert len(red.failures) == 1 and not red.errors, 'Capacity mutation survived/infra failure'
    print('RED confirmed: ignored output capacity; no source mutation')
    restored = unittest.TextTestRunner(verbosity=2).run(Controls('test_buffer_preflight_before_consumption'))
    assert restored.wasSuccessful()
    checked([sys.executable, '-B', str(ROOT / 'spike/test_ai_primitives.py'), '--library',
             str(Path(target) / 'debug/libnarwhal_automation.so'), '--test',
             'test_live_catalog_unchanged_and_one_use_approval'], timeout=30)
    print('PASS: bridge controls, real parsed calls, normal library disabled; no geometry/publication claim')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target-dir', type=Path, default=TARGET)
    parser.add_argument('--build', action='store_true')
    parser.add_argument('--discover', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if not (args.build or args.discover or args.self_test):
        parser.error('Choose --build, --discover or --self-test (no implicit build)')
    executable = build_bridge(args.target_dir) if args.build else discover_executable(args.target_dir)
    print(json.dumps({'test': TEST_NAME, 'executable': str(executable)}, indent=2), flush=True)
    if args.self_test:
        self_test(executable, args.target_dir)


if __name__ == '__main__':
    main()
