#!/usr/bin/env python3
"""Workflow adapter controls and in-memory RED mutations; no UI/native/network dependency."""
import dataclasses
import hashlib
import importlib
import inspect
import json
import io
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, BaseHandler, build_opener
from urllib.response import addinfourl
from email.message import Message

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg
ai = importlib.import_module("narwhal3d.ai_assistant")
transport = importlib.import_module("narwhal3d.ai_transport")
SOURCE = (ROOT / "narwhal3d/ai_transport.py").read_text()
SECRET = "credential-must-never-escape"


class Response:
    def __init__(self, raw=b"raw model prose", during_read=lambda: None, error=None):
        self.raw, self.during_read, self.error = raw, during_read, error
        self.closed, self.reads = 0, []

    def read(self, amount):
        self.reads.append(amount)
        self.during_read()
        if self.error:
            raise self.error
        return self.raw[:amount]

    def close(self):
        self.closed += 1


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


class Trickle(Response):
    def __init__(self, clock, chunks, step=0.0):
        super().__init__()
        self.clock, self.chunks, self.step = clock, iter(chunks), step

    def read1(self, amount):
        self.reads.append(amount)
        self.clock.now += self.step
        chunk = next(self.chunks, b'')
        assert len(chunk) <= amount, 'fixture must honor requested byte bound'
        return chunk

    def read(self, amount):
        raise AssertionError('read1 was available')


class Controls(unittest.TestCase):
    module = transport

    def prepared(self, **updates):
        data = {"token": {"session": "s1", "turn": 7, "nested": {"revision": 2}},
                "binding": {"provider_id": "openai_compatible", "model": "rust-model"},
                "body": {"model": "rust-model", "messages": [], "stream": False,
                         "tools": [{"type": "function", "function": {"name": "inspect"}}]},
                "timeout_ms": 1234}
        data.update(updates)
        return self.module.PreparedTurn(ai.ProviderConfig(model="config-model"), data,
                                        threading.Event())

    def send(self, prepared=None, response=None, environ=None):
        self.opened = []
        self.response = response if response is not None else Response()
        def opener(request, timeout):
            self.opened.append((request, timeout))
            return self.response
        return self.module.send_turn(prepared or self.prepared(), environ or {}, opener)

    def test_raw_and_prepared_body_pass_through(self):
        for raw in (b'{"choices":[{"message":{"content":" text "}}]}',
                    b'{"choices":[{"message":{"content":null,"tool_calls":[{}]}}]}',
                    b'{"content":[{"type":"tool_use","input":{}}]}',
                    b'not JSON: do not execute Python or shell', b'', ' CAD \u2603 '.encode()):
            prepared = self.prepared()
            result = self.send(prepared, Response(raw))
            self.assertEqual(result.raw, raw.decode())
            self.assertIsNone(result.failure)
            self.assertFalse(result.physical_http_cancelled)
            request, timeout = self.opened[0]
            self.assertEqual(len(self.opened), 1)
            self.assertEqual(json.loads(request.data), prepared.data["body"])
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 1.234)  # Opener receives remaining elapsed budget.
            self.assertEqual(request.full_url, 'http://127.0.0.1:1234/v1/chat/completions')
            self.assertEqual(request.get_method(), 'POST')
            self.assertEqual(self.response.reads, [512 * 1024 + 1])
            self.assertEqual(self.response.closed, 1)

    def test_stdlib_redirect_reproduction(self):
        for target in ('https://other.invalid/collect', 'http://other.invalid/collect'):
            request = Request('https://provider.invalid/chat', data=b'{}', headers={
                'Authorization': 'Bearer ' + SECRET, 'x-api-key': SECRET})
            redirected = HTTPRedirectHandler().redirect_request(
                request, None, 302, 'Found', {}, target)
            self.assertEqual(redirected.full_url, target)
            self.assertTrue(SECRET in redirected.get_header('Authorization'))
            self.assertTrue(SECRET in redirected.get_header('X-api-key'))

    def test_redirect_refusal(self):
        handler = self.module.NoRedirectHandler()
        for target in ('https://provider.invalid/next', 'https://other.invalid/collect',
                       'http://other.invalid/collect'):
            for code in (301, 302, 303, 307, 308):
                response = io.BytesIO(SECRET.encode())
                request = Request('https://provider.invalid/chat', data=b'{}', headers={
                    'Authorization': SECRET, 'x-api-key': SECRET})
                with self.assertRaises(HTTPError) as caught:
                    handler.redirect_request(request, response, code, SECRET, {}, target)
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn(SECRET, str(caught.exception))
                self.assertTrue(response.closed)

    def test_default_openers_refuse_redirects(self):
        for api in (self.module.send_chat, self.module.discover_models, self.module.send_turn):
            self.assertIs(inspect.signature(api).parameters['opener'].default,
                          self.module._provider_open)
        for target in ('https://other.invalid/collect', 'http://other.invalid/collect'):
            for provider in ('openai', 'anthropic'):
                for name in ('send_chat', 'discover_models', 'send_turn'):
                    calls, responses = [], []
                    register_cleanup = self.addCleanup
                    class Wire(BaseHandler):
                        handler_order = 100  # Intercept before any real HTTP/HTTPS handler.
                        def http_open(self, request):
                            calls.append(request)
                            headers = Message()
                            headers['Location'] = target
                            response = addinfourl(io.BytesIO(b'{}'), headers,
                                                  request.full_url, 302 if len(calls) == 1 else 200)
                            response.msg = 'Found'
                            responses.append(response)
                            register_cleanup(response.close)
                            return response
                        https_open = http_open
                    def fake_build(*handlers):
                        return build_opener(Wire(), *handlers)
                    prepared = dataclasses.replace(self.prepared(), config=ai.set_provider(
                        provider, 'model', credential_env='TEST_KEY'))
                    with patch.object(self.module, 'build_opener', fake_build):
                        if name == 'send_turn':
                            result = self.module.send_turn(prepared, {'TEST_KEY': SECRET})
                            self.assertEqual((result.failure, result.http_status), ('http_error', 302))
                            self.assertIsNone(result.raw)
                        else:
                            args = (prepared.config, {'TEST_KEY': SECRET})
                            if name == 'send_chat':
                                args = (prepared.config, {'messages': []}, {'TEST_KEY': SECRET})
                            with self.assertRaises(self.module.TransportError) as caught:
                                getattr(self.module, name)(*args)
                            self.assertIn('HTTP error 302', str(caught.exception))
                            self.assertNotIn(SECRET, str(caught.exception))
                    self.assertEqual(len(calls), 1, 'no redirected request may be opened')
                    header = 'Authorization' if provider == 'openai' else 'X-api-key'
                    self.assertTrue(SECRET in calls[0].get_header(header))
                    self.assertTrue(responses[0].closed)

    def test_elapsed_deadline(self):
        clock = Clock()
        with patch.object(self.module, 'monotonic', clock):
            # Each read is shorter than a socket inactivity timeout, but total time is not.
            response = Trickle(clock, [b'a', b'b', b'c', b'd'], step=0.4)
            result = self.send(self.prepared(timeout_ms=1000), response)
            self.assertEqual(result.failure, 'timeout')
            self.assertIsNone(result.raw)
            self.assertEqual(len(response.reads), 3)
            self.assertEqual(response.closed, 1)
            self.assertFalse(result.physical_http_cancelled)
            # The fallback reader also discards a late return.
            clock.now = 0.0
            response = Response(during_read=lambda: setattr(clock, 'now', 1.0))
            result = self.send(self.prepared(timeout_ms=1000), response)
            self.assertEqual(result.failure, 'timeout')
            self.assertIsNone(result.raw)
            self.assertEqual(response.closed, 1)

    def test_deadline_before_and_after_open(self):
        clock, response, calls = Clock(), Response(), []
        prepared = self.prepared(timeout_ms=1000)
        def late_opener(request, timeout):
            calls.append(timeout)
            clock.now = 1.0
            return response
        with patch.object(self.module, 'monotonic', clock):
            result = self.module.send_turn(prepared, {}, late_opener)
            self.assertEqual(result.failure, 'timeout')
            self.assertIsNone(result.raw)
            self.assertEqual(calls, [1.0])
            self.assertEqual(response.reads, [])
            self.assertEqual(response.closed, 1)
            self.assertEqual(result.token, prepared.data['token'])
            self.assertFalse(result.physical_http_cancelled)
            class SlowEnviron(dict):
                def get(self, key, default=None):
                    clock.now += 1.0
                    return default
            clock.now = 0.0
            calls.clear()
            result = self.module.send_turn(prepared, SlowEnviron(), late_opener)
            self.assertEqual(result.failure, 'timeout')
            self.assertEqual(calls, [])
            # Partial preflight cost is deducted from the opener's timeout.
            class PartialEnviron(dict):
                def get(self, key, default=None):
                    clock.now += 0.25
                    return default
            clock.now = 0.0
            self.module.send_turn(prepared, PartialEnviron(), late_opener)
            self.assertEqual(calls, [0.75])

    def test_deadline_before_read(self):
        clock = Clock()
        class LateReader(Response):
            @property
            def read1(self):
                clock.now = 1.0  # Time elapsed between opener's return and first read.
                return self.read
        response = LateReader()
        with patch.object(self.module, 'monotonic', clock):
            result = self.send(self.prepared(timeout_ms=1000), response)
        self.assertEqual(result.failure, 'timeout')
        self.assertIsNone(result.raw)
        self.assertEqual(response.reads, [])
        self.assertEqual(response.closed, 1)

    def test_incremental_bounds(self):
        clock = Clock()
        with patch.object(self.module, 'monotonic', clock):
            for extra in (b'', b'x'):
                response = Trickle(clock, [b'x' * 65536] * 8 + [extra])
                result = self.send(response=response)
                self.assertEqual(result.failure, 'response_too_large' if extra else None)
                self.assertEqual(result.raw, None if extra else 'x' * (512 * 1024))
                self.assertEqual(response.reads, [65536] * 8 + [1])
                self.assertEqual(response.closed, 1)
            # UTF-8 may straddle reads and must be decoded only after collection.
            response = Trickle(clock, [b'\xe2', b'\x98', b'\x83'])
            self.assertEqual(self.send(response=response).raw, '\u2603')
            response = Trickle(clock, [b'a'] * 1024)
            result = self.send(response=response)
            self.assertEqual(result.failure, 'response_read_limit')
            self.assertIsNone(result.raw)
            self.assertEqual(len(response.reads), 1024)
            self.assertEqual(response.closed, 1)

    def test_global_admission(self):
        entered = [threading.Event(), threading.Event()]
        release, lock = threading.Event(), threading.Lock()
        calls, expired, results = [], [], [None, None]
        prepared = [self.prepared(token={'turn': i}) for i in range(2)]
        responses = [Response(), Response()]
        def opener(request, timeout):
            with lock:
                index = len(calls)
                calls.append(index)
            if index >= 2:
                return Response()  # A broken admission guard must not hang the test.
            entered[index].set()
            if not release.wait(1):
                expired.append(index)
            return responses[index]
        def worker(index):
            results[index] = self.module.send_turn(prepared[index], {}, opener)
        workers = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for thread in workers:
            thread.start()
        try:
            for event in entered:
                self.assertTrue(event.wait(1), 'both physical requests must enter')
            for turn in prepared:
                turn.cancel_event.set()  # Logical cancellation cannot free physical slots.
            result = self.module.send_turn(self.prepared(), {}, opener)
            self.assertEqual(result.failure, 'transport_busy')
            self.assertEqual(calls, [0, 1])
            busy_turn = self.prepared(token={'turn': 3, 'nested': {'revision': 2}})
            class NoBodyCopy(dict):
                def __deepcopy__(self, memo):
                    raise AssertionError('busy request copied its body')
            busy_turn.data['body'] = NoBodyCopy()
            result = self.module.send_turn(busy_turn, {}, opener)
            self.assertEqual(result.failure, 'transport_busy')
            self.assertEqual(result.token, busy_turn.data['token'])
            busy_turn.data['token']['nested']['revision'] = 999
            self.assertEqual(result.token['nested']['revision'], 2)
            self.assertIsNone(result.raw)
            self.assertFalse(result.physical_http_cancelled)
            self.assertEqual(calls, [0, 1])
        finally:
            release.set()
            for thread in workers:
                thread.join(1)
        self.assertFalse(any(thread.is_alive() for thread in workers))
        self.assertEqual(expired, [])
        self.assertEqual([result.failure for result in results], ['cancelled', 'cancelled'])
        self.assertEqual([response.closed for response in responses], [1, 1])
        self.assertIsNone(self.module.send_turn(self.prepared(), {}, opener).failure)
        self.assertEqual(calls, [0, 1, 2])

    def test_provider_headers(self):
        for provider, header in (("openai", "Authorization"), ("anthropic", "X-api-key")):
            prepared = dataclasses.replace(self.prepared(), config=ai.set_provider(
                provider, "model", credential_env="TEST_KEY"))
            self.send(prepared, environ={"TEST_KEY": SECRET})
            self.assertIn(SECRET, self.opened[0][0].get_header(header))

    def test_request_bound(self):
        for extra in (0, 1):
            prepared = self.prepared(body={"x": "x" * (256 * 1024 - 8 + extra)})
            result = self.send(prepared)
            self.assertEqual(result.failure, "request_too_large" if extra else None)
            self.assertEqual(len(self.opened), 0 if extra else 1)
        self.assertEqual(self.send(self.prepared(body={"x": "\u00e9" * (128 * 1024)})).failure,
                         "request_too_large")

    def test_response_bound_and_utf8(self):
        for raw, failure in ((b'x' * (512 * 1024), None),
                             (b'x' * (512 * 1024 + 1), 'response_too_large'),
                             (b'\xff', 'invalid_utf8'), (b'\xe2\x82', 'invalid_utf8')):
            result = self.send(response=Response(raw))
            self.assertEqual(result.failure, failure)
            self.assertEqual(result.raw, None if failure else raw.decode())
            self.assertEqual(self.response.closed, 1)

    def test_pre_cancel(self):
        prepared = self.prepared()
        prepared.cancel_event.set()
        result = self.send(prepared)
        self.assertEqual(self.opened, [])
        self.assertEqual(result.failure, 'cancelled')
        self.assertIsNone(result.raw)
        self.assertEqual(result.token, prepared.data['token'])
        self.assertFalse(result.physical_http_cancelled)

    def test_post_open_cancel(self):
        prepared, response = self.prepared(), Response()
        def opener(request, timeout):
            prepared.cancel_event.set()
            return response
        result = self.module.send_turn(prepared, {}, opener)
        self.assertEqual(result.failure, 'cancelled')
        self.assertIsNone(result.raw)
        self.assertFalse(result.physical_http_cancelled)
        self.assertEqual(response.reads, [])
        self.assertEqual(response.closed, 1)

    def test_post_read_cancel(self):
        prepared = self.prepared()
        result = self.send(prepared, Response(during_read=prepared.cancel_event.set))
        self.assertEqual(result.failure, 'cancelled')
        self.assertIsNone(result.raw)
        self.assertFalse(result.physical_http_cancelled)
        self.assertEqual(self.response.closed, 1)

    def test_snapshot_and_frozen_repr(self):
        prepared = self.prepared()
        expected = json.loads(json.dumps(prepared.data['token']))
        def mutate_input():
            prepared.data['token']['nested']['revision'] = 999
            prepared.data['body']['model'] = SECRET
        result = self.send(prepared, Response(SECRET.encode(), mutate_input))
        self.assertEqual(result.token, expected)
        self.assertIsNot(result.token, prepared.data['token'])
        self.assertEqual(json.loads(self.opened[0][0].data)['model'], 'rust-model')
        for value in (prepared, result):
            self.assertNotIn(SECRET, repr(value))
            with self.assertRaises(dataclasses.FrozenInstanceError):
                value.raw = 'changed'
        for cls, fields in ((self.module.PreparedTurn, ('config', 'data', 'cancel_event')),
                            (self.module.TurnTransportResult, ('token', 'raw'))):
            for field in fields:
                self.assertFalse(cls.__dataclass_fields__[field].repr)

    def test_invalid_preflight(self):
        for updates in ({'body': None}, {'body': []}, {'token': None}, {'binding': None},
                        {'body': {'stream': True}}, {'body': {'stream': 'false'}}):
            self.assertEqual(self.send(self.prepared(**updates)).failure, 'invalid_ready')
            self.assertEqual(self.opened, [])
        for body in ({'x': object()}, {'x': float('nan')}, {'x': '\ud800'}):
            self.assertEqual(self.send(self.prepared(body=body)).failure, 'invalid_body')
            self.assertEqual(self.opened, [])
        for data in (None, [], {}):
            self.assertEqual(self.send(dataclasses.replace(self.prepared(), data=data)).failure,
                             'invalid_ready')
            self.assertEqual(self.opened, [])
        for timeout in (None, True, 0, -1, 30001, 1.5, '1000'):
            self.assertEqual(self.send(self.prepared(timeout_ms=timeout)).failure, 'invalid_timeout')
            self.assertEqual(self.opened, [])
        for timeout in (1, 30000):
            self.assertIsNone(self.send(self.prepared(timeout_ms=timeout)).failure)
            self.assertGreater(self.opened[0][1], 0)
            self.assertLessEqual(self.opened[0][1], timeout / 1000)

    def test_static_failures_and_close(self):
        for error, failure in ((TimeoutError(SECRET), 'timeout'),
                               (URLError(SECRET), 'transport_failed'),
                               (ValueError(SECRET), 'transport_failed')):
            for during_read in (False, True):
                prepared, response, calls = self.prepared(), Response(error=error), []
                def opener(request, timeout):
                    calls.append(1)
                    if not during_read:
                        raise error
                    return response
                result = self.module.send_turn(prepared, {}, opener)
                self.assertEqual(result.failure, failure)
                self.assertIsNone(result.raw)
                self.assertEqual(result.token, prepared.data['token'])
                self.assertNotIn(SECRET, repr(result))
                self.assertEqual(calls, [1])
                self.assertEqual(response.closed, int(during_read))
        prepared = dataclasses.replace(self.prepared(), config=ai.ProviderConfig(
            endpoint='https://user:' + SECRET + '@example.com'))
        self.assertEqual(self.send(prepared).failure, 'transport_failed')
        self.assertEqual(self.opened, [])
        for status in (429, SECRET, True, 999):
            response = Response(SECRET.encode())
            error = HTTPError('https://example.com', status, SECRET, {'key': SECRET}, response)
            def opener(request, timeout):
                raise error
            result = self.module.send_turn(self.prepared(), {}, opener)
            self.assertEqual(result.failure, 'http_error')
            self.assertEqual(result.http_status, 429 if status == 429 else None)
            self.assertIsNone(result.raw)
            self.assertNotIn(SECRET, repr(result))
            self.assertEqual(response.closed, 1)
        response = Response()
        def bad_close():
            raise RuntimeError(SECRET)
        response.close = bad_close
        self.assertIsNone(self.send(response=response).failure)
        response.close = None
        self.assertIsNone(self.send(response=response).failure)


class Mutations(unittest.TestCase):
    def test_legacy_prefix_unchanged(self):
        prefix = SOURCE.split('\n\n# Workflow HTTP integration only;', 1)[0]
        # Mask only the reviewed redirect and explicit Responses routing additions;
        # the legacy bodies must still match their original fixed baseline hash.
        before, added = prefix.split('\n\nclass NoRedirectHandler', 1)
        prefix = before + '\n\nclass TransportError' + added.split('\n\nclass TransportError', 1)[1]
        prefix = prefix.replace('Request, urlopen, HTTPRedirectHandler, build_opener', 'Request, urlopen')
        prefix = prefix.replace('opener=_provider_open', 'opener=urlopen')
        prefix = prefix.replace('''    if config.provider_id == "openai_responses":
        if not key:
            raise TransportError(f"set {config.credential_env} before connecting")
        headers["Authorization"] = f"Bearer {key}"
        # The body and response schema are native-owned; this is only HTTP routing.
        return _endpoint(endpoint, "/responses"), headers, {}
''', '')
        prefix = prefix.replace('("openai", "openai_compatible", "openai_responses")', '("openai", "openai_compatible")')
        prefix = prefix.replace('config.provider_id in ("openai", "openai_responses") and not key', 'config.provider_id == "openai" and not key')
        prefix = prefix.replace('''    if config.provider_id == "openai_responses":
        raise TransportError("OpenAI Responses requires the native workflow API")
''', '')
        self.assertEqual(hashlib.sha256(prefix.encode()).hexdigest(),
                         'f228639cf4e284ebe037bd9364f0266bbe04e8c9db90554b354fadae5f45b4ea')

    def test_controls_kill_in_memory_mutations(self):
        mutations = [
            ('global_admission', 'if not acquired:', 'if False:', 1),
            ('pre_cancel', 'if cancel_event.is_set():', 'if False:', 5),
            # Disable post-open guards together: either guard alone is redundant.
            ('post_open_cancel', 'if cancel_event.is_set():', 'if False:', 5),
            ('post_read_cancel', 'chunk = reader(amount)\n            _turn_remaining(deadline)\n'
             '            if cancel_event.is_set():',
             'chunk = reader(amount)\n            _turn_remaining(deadline)\n            if False:', 1),
            ('redirect_refusal', 'raise error',
             'return super().redirect_request(req, fp, code, msg, headers, newurl)', 1),
            ('default_openers_refuse_redirects', 'build_opener(NoRedirectHandler())',
             'build_opener(HTTPRedirectHandler())', 1),
            ('elapsed_deadline', 'if remaining <= 0:', 'if False:', 1),
            ('elapsed_deadline', 'chunk = reader(amount)\n            _turn_remaining(deadline)',
             'chunk = reader(amount)', 1),
            ('deadline_before_read', '_turn_remaining(deadline)\n            amount =',
             'amount =', 1),
            ('deadline_before_and_after_open', 'timeout=_turn_remaining(deadline)',
             'timeout=timeout_ms / 1000.0', 1),
            ('incremental_bounds', 'TURN_MAX_READ_CALLS = 1024', 'TURN_MAX_READ_CALLS = 1025', 1),
            ('incremental_bounds', 'if len(raw) + len(chunk) > TURN_MAX_RESPONSE_BYTES:',
             'if False:', 1),
            ('request_bound', 'if len(encoded) > TURN_MAX_REQUEST_BYTES:', 'if False:', 1),
            ('response_bound_and_utf8', 'if len(raw) + len(chunk) > TURN_MAX_RESPONSE_BYTES:', 'if False:', 1),
            ('static_failures_and_close', 'except Exception:\n        return TurnTransportResult('
             'token, failure="transport_failed")', 'except Exception as exc:\n'
             '        return TurnTransportResult(token, failure=str(exc))', 1),
            ('snapshot_and_frozen_repr', 'raw: str | None = field(default=None, repr=False)',
             'raw: str | None = field(default=None, repr=True)', 1),
            ('snapshot_and_frozen_repr', 'token = deepcopy(data["token"])',
             'token = prepared.data["token"]', 1),
        ]
        for method, old, new, count in mutations:
            with self.subTest(mutation=method):
                self.assertIn(old, SOURCE)
                module = types.ModuleType('narwhal3d._workflow_mutation')
                module.__package__ = 'narwhal3d'
                sys.modules[module.__name__] = module
                try:
                    exec(compile(SOURCE.replace(old, new, count), '<memory-mutation>', 'exec'),
                         module.__dict__)
                    case = Controls('test_' + method)
                    case.module = module
                    result = unittest.TestResult()
                    case.run(result)
                    self.assertEqual(len(result.errors), 0, 'mutation must fail an assertion')
                    self.assertEqual(len(result.failures), 1, 'mutation survived')
                finally:
                    sys.modules.pop(module.__name__, None)
        print(f'Workflow transport: {len(mutations)} in-memory mutations RED '
                      '(controls run on unchanged source)')


if __name__ == '__main__':
    unittest.main(verbosity=1)
