"""Thin object-registry ABI1 marshalling/lifetimes; no Python registry engine.

Not activated by model constructors until the installed core is qualified.
Python is required for ctypes, host payload lifetimes and strict reply validation.
Rust owns identities/order, associations, transactions and immutable snapshots.
"""
from __future__ import annotations

import ctypes
import importlib.util
import json
from pathlib import Path
import sys
import threading
import uuid
import weakref

_NAME = 'narwhal3d.native_document_session'
native = sys.modules.get(_NAME)
if native is None:
    spec = importlib.util.spec_from_file_location(_NAME, Path(__file__).with_name('native_document_session.py'))
    native = importlib.util.module_from_spec(spec)
    sys.modules[_NAME] = native
    spec.loader.exec_module(native)

_MIN_OUTPUT = 4096
_MAX_OUTPUT = 512 * 1024 * 1024
_MAX_REQUEST = 32 * 1024 * 1024
_READ = frozenset(('info', 'rows', 'get', 'find_name', 'dirty', 'ticket_rows', 'ticket_diff', 'order_pending', 'normalize_plan'))
_OPS = _READ | frozenset(('create', 'close', 'snapshot', 'release_snapshot', 'prepare',
                          'prepare_restore', 'prepare_edits', 'reserve_payloads', 'begin_apply', 'commit', 'abort', 'invalidate',
                          'track', 'mark', 'mark_all', 'bind_display'))


class RegistryError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(f'object registry: {code}')


class RegistryUnavailable(RegistryError):
    pass


def _uint(value):
    return type(value) is int and 0 <= value <= (1 << 64) - 1


def _handle(value):
    return _uint(value) and value > 0


def _uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value and uuid.UUID(value).int != 0
    except (ValueError, AttributeError):
        return False


def _row(value):
    return (type(value) is dict and set(value) == {'id', 'name', 'display', 'payload'}
            and _uuid(value['id']) and isinstance(value['name'], str)
            and (value['display'] is None or isinstance(value['display'], str))
            and (value['payload'] is None or _handle(value['payload'])))


def _validate(op, reply):
    if type(reply) is not dict:
        raise RegistryError('invalid_reply')
    if reply.get('status') == 'error':
        if set(reply) != {'status', 'code'} or not isinstance(reply['code'], str) or not reply['code']:
            raise RegistryError('invalid_reply')
        raise RegistryError(reply['code'])
    if reply.pop('status', None) != 'ok':
        raise RegistryError('invalid_reply')
    keys = set(reply)
    if op in ('create', 'info', 'commit', 'invalidate', 'bind_display'):
        expected = {'revision', 'count', 'authoritative', 'pending'}
        if op == 'create':
            expected.add('owner')
        valid = (keys == expected and _uint(reply.get('revision')) and _uint(reply.get('count'))
                 and reply['count'] <= 100_000 and type(reply.get('authoritative')) is bool
                 and type(reply.get('pending')) is bool)
        if op == 'create':
            valid = valid and _handle(reply['owner']) and reply['revision'] == 0
    elif op in ('close', 'release_snapshot', 'begin_apply', 'abort', 'track', 'mark', 'mark_all'):
        valid = not reply
    elif op in ('prepare', 'prepare_restore', 'prepare_edits'):
        valid = keys == {'ticket'} and _handle(reply['ticket'])
    elif op == 'reserve_payloads':
        valid = (keys == {'first', 'count'} and _handle(reply['first']) and _handle(reply['count'])
                 and reply['count'] <= 100_000 and _uint(reply['first'] + reply['count'] - 1))
    elif op == 'snapshot':
        valid = keys == {'snapshot', 'revision'} and _handle(reply['snapshot']) and _uint(reply['revision'])
    elif op in ('rows', 'ticket_rows'):
        valid = (keys == {'revision', 'rows', 'next'} and _uint(reply['revision'])
                 and _uint(reply['next']) and reply['next'] <= 100_000 and type(reply['rows']) is list
                 and len(reply['rows']) <= 1024 and all(_row(row) for row in reply['rows']))
    elif op == 'get':
        valid = (keys == {'revision', 'row'} and _uint(reply['revision'])
                 and (reply['row'] is None or _row(reply['row'])))
    elif op == 'ticket_diff':
        valid = (keys == {'removed', 'added'} and type(reply['removed']) is list and type(reply['added']) is list
                 and len(reply['removed']) <= 100_000 and len(reply['added']) <= 100_000
                 and all(_row(row) for row in reply['removed']) and all(_uuid(oid) for oid in reply['added']))
    elif op == 'normalize_plan':
        valid = (keys == {'ids'} and type(reply['ids']) is list and len(reply['ids']) <= 100_000
                 and all(_uuid(oid) for oid in reply['ids']) and len(set(reply['ids'])) == len(reply['ids']))
    elif op == 'order_pending':
        valid = keys == {'pending'} and type(reply['pending']) is bool
    elif op == 'dirty':
        valid = (keys == {'all', 'rows'} and type(reply['all']) is bool and type(reply['rows']) is list
                 and len(reply['rows']) <= 100_000 and all(type(row) is list and len(row) == 2
                     and _uuid(row[0]) and type(row[1]) is bool for row in reply['rows']))
    elif op == 'check_observation':
        valid = (keys == {'revision', 'matches'} and _uint(reply['revision'])
                 and type(reply['matches']) is bool)
    elif op == 'find_name':
        valid = (keys == {'revision', 'id'} and _uint(reply['revision'])
                 and (reply['id'] is None or _uuid(reply['id'])))
    else:
        valid = False
    if not valid:
        raise RegistryError('invalid_reply')
    return reply


def _binding(library=None):
    library = library if library is not None else native.load_library()
    try:
        abi = library.narwhal_object_registry_abi
        abi.argtypes, abi.restype = [], ctypes.c_uint32
        if abi() != 1:
            raise RegistryUnavailable('native ABI1 required; wrong ABI')
        call = library.narwhal_object_registry_json
        call.argtypes = [native._ByteP, ctypes.c_size_t, native._ByteP, ctypes.c_size_t]
        call.restype = ctypes.c_ssize_t
    except (AttributeError, OSError, ctypes.ArgumentError) as exc:
        raise RegistryUnavailable('matching application-core object registry ABI1 required') from exc
    return library, call


def _call(function, request):
    op = request.get('op')
    if op not in _OPS:
        raise RegistryError('invalid_request')
    data = json.dumps(request, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    if len(data) > _MAX_REQUEST:
        raise RegistryError('request_too_large')
    input_buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    capacity = _MIN_OUTPUT
    for _ in range(3):
        output = (ctypes.c_ubyte * capacity)()
        try:
            size = function(input_buffer, len(data), output, capacity)
        except (OSError, ctypes.ArgumentError) as exc:
            raise RegistryError('call_outcome_unknown') from exc
        if type(size) is not int or size <= 0 or size > _MAX_OUTPUT:
            raise RegistryError('invalid_reply')
        if size > capacity:
            if op not in _READ:
                raise RegistryError('mutation_reply_overflow')
            capacity = size
            continue
        try:
            reply = json.loads(bytes(output[:size]).decode('utf-8'),
                               object_pairs_hook=native._object, parse_constant=native._reject_constant)
        except (UnicodeError, ValueError) as exc:
            raise RegistryError('invalid_reply') from exc
        return _validate(op, reply)
    raise RegistryError('unstable_reply_size')


def _close(function, owner):
    try:
        _call(function, {'op': 'close', 'owner': owner})
    except Exception:
        pass  # finalizers cannot establish successful cleanup after native failure


class Registry:
    def __init__(self, rows=(), *, library=None):
        self._library, self._function = _binding(library)
        self._lock = threading.RLock()
        self._uncertain = False
        self._closed = False
        reply = _call(self._function, {'op': 'create', 'rows': list(rows)})
        self.handle = reply['owner']
        self._finalizer = weakref.finalize(self, _close, self._function, self.handle)

    def request(self, op, **fields):
        with self._lock:
            if self._closed:
                raise RegistryError('owner_closed')
            if self._uncertain and op not in ('invalidate', 'close', 'info'):
                raise RegistryError('outcome_unknown_requires_invalidation')
            try:
                reply = _call(self._function, dict(fields, op=op, owner=self.handle))
            except RegistryError as exc:
                if exc.code in ('invalid_reply', 'call_outcome_unknown', 'mutation_reply_overflow',
                                'panic', 'registry_poisoned'):
                    self._uncertain = True
                raise
            except BaseException:
                # An interruption/allocation error may arrive after native dispatch.
                # Keep the owner quarantined rather than silently allowing a retry.
                self._uncertain = True
                raise
            if op == 'invalidate':
                self._uncertain = False
            return reply

    def info(self):
        reply = self.request('info')
        if self._uncertain:
            reply['authoritative'] = False
        return reply

    def rows(self, *, ticket=None):
        with self._lock:
            out, offset, revision = [], 0, None
            seen = set()
            while True:
                fields = dict(offset=offset, limit=1024)
                if ticket is not None:
                    fields['ticket'] = ticket
                reply = self.request('rows' if ticket is None else 'ticket_rows', **fields)
                if revision is not None and revision != reply['revision']:
                    raise RegistryError('stale_revision')
                revision = reply['revision']
                rows = reply['rows']
                if reply['next'] != offset + len(rows):
                    self._uncertain = True
                    raise RegistryError('invalid_reply')
                ids = [row['id'] for row in rows]
                if len(set(ids)) != len(ids) or seen.intersection(ids):
                    self._uncertain = True
                    raise RegistryError('invalid_reply')
                seen.update(ids)
                out.extend(rows)
                if len(rows) < 1024:
                    return out
                offset = reply['next']

    def get(self, object_id):
        row = self.request('get', id=str(object_id))['row']
        if row is not None and row['id'] != str(object_id):
            self._uncertain = True
            raise RegistryError('invalid_reply')
        return row

    def find_name(self, name):
        return self.request('find_name', name=name)['id']

    def prepare(self, rows, *, revision=None, rebuild=False):
        with self._lock:
            if revision is None:
                revision = self.info()['revision']
            result = self.request('prepare', revision=revision, rows=list(rows), rebuild=rebuild)
            return Ticket(self, result['ticket'])

    def prepare_edits(self, edits, *, revision=None):
        with self._lock:
            if revision is None:
                revision = self.info()['revision']
            result = self.request('prepare_edits', revision=revision, edits=list(edits))
            return Ticket(self, result['ticket'])

    def reserve_payloads(self, count):
        reply = self.request('reserve_payloads', count=count)
        if reply['count'] != count:
            self._uncertain = True
            raise RegistryError('invalid_reply')
        return range(reply['first'], reply['first'] + count)

    def _observed(self, op, observation, **fields):
        with self._lock:
            if self._closed:
                raise RegistryError('owner_closed')
            if self._uncertain:
                raise RegistryError('outcome_unknown_requires_invalidation')
            if type(observation) is not bytes or len(observation) > 128 * 1024 * 1024:
                raise RegistryError('invalid_observation_buffer')
            try:
                abi = self._library.narwhal_object_observation_abi
                abi.argtypes, abi.restype = [], ctypes.c_uint32
                if abi() != 1:
                    raise RegistryUnavailable('observation ABI1 required')
                function = self._library.narwhal_object_registry_observe
            except AttributeError as error:
                raise RegistryUnavailable('matching observation-capable application core required') from error
            function.argtypes = [native._ByteP, ctypes.c_size_t, native._ByteP, ctypes.c_size_t,
                                 native._ByteP, ctypes.c_size_t]
            function.restype = ctypes.c_ssize_t
            header = json.dumps(dict(fields, op=op, owner=self.handle), ensure_ascii=False,
                                allow_nan=False, separators=(',', ':')).encode('utf-8')
            if len(header) > _MAX_REQUEST:
                raise RegistryError('request_too_large')
            input_header = (ctypes.c_ubyte * len(header)).from_buffer_copy(header)
            input_data = (ctypes.c_ubyte * max(1, len(observation))).from_buffer_copy(observation or b'\0')
            output = (ctypes.c_ubyte * _MIN_OUTPUT)()
            try:
                size = function(input_header, len(header), input_data, len(observation), output, _MIN_OUTPUT)
                if type(size) is not int or not 0 < size <= _MIN_OUTPUT:
                    raise RegistryError('invalid_reply')
                try:
                    reply = json.loads(bytes(output[:size]).decode('utf-8'),
                                       object_pairs_hook=native._object, parse_constant=native._reject_constant)
                except (UnicodeError, ValueError) as error:
                    raise RegistryError('invalid_reply') from error
                return _validate({'check': 'check_observation', 'index': 'info', 'capture': 'prepare'}.get(op, op), reply)
            except RegistryError as error:
                if error.code in ('invalid_reply', 'panic', 'registry_poisoned'):
                    self._uncertain = True
                raise
            except BaseException:
                self._uncertain = True
                raise

    def track(self):
        return self.request('track')

    def mark(self, ids, *, moved=False):
        return self.request('mark', ids=list(ids), moved=moved)

    def mark_all(self):
        return self.request('mark_all')

    def dirty(self):
        return self.request('dirty')

    def restore_changes(self, ticket):
        reply = self.request('ticket_diff', ticket=ticket)
        removed_ids = [row['id'] for row in reply['removed']]
        if len(set(removed_ids)) != len(removed_ids) or len(set(reply['added'])) != len(reply['added']):
            self._uncertain = True
            raise RegistryError('invalid_reply')
        return {'removed': {row['id']: row['display'] for row in reply['removed']}, 'added': reply['added']}

    def normalization_plan(self):
        return self.request('normalize_plan')['ids']

    def order_pending(self):
        return self.request('order_pending')['pending']

    def bind_display(self, object_id, display):
        return self.request('bind_display', id=str(object_id), display=display)

    def sync_index(self, data):
        return self._observed('index', data, revision=self.info()['revision'])

    def capture(self, observation, *, rebuild=False):
        reply = self._observed('capture', observation, revision=self.info()['revision'], rebuild=rebuild)
        return Ticket(self, reply['ticket'])

    def prepare_observed(self, rows, observation, *, revision=None, rebuild=False):
        with self._lock:
            if revision is None:
                revision = self.info()['revision']
            reply = self._observed('prepare', observation, revision=revision, rows=list(rows), rebuild=rebuild)
            return Ticket(self, reply['ticket'])

    def check_observation(self, observation, *, revision=None):
        with self._lock:
            if revision is None:
                revision = self.info()['revision']
            return self._observed('check', observation, revision=revision)

    def snapshot(self):
        reply = self.request('snapshot')
        return Snapshot(self, reply['snapshot'])

    def prepare_restore(self, snapshot, *, revision=None):
        if not isinstance(snapshot, Snapshot) or snapshot.owner is not self or snapshot.closed:
            raise RegistryError('invalid_snapshot')
        with self._lock:
            if revision is None:
                revision = self.info()['revision']
            reply = self.request('prepare_restore', revision=revision, snapshot=snapshot.handle)
            return Ticket(self, reply['ticket'])

    def invalidate(self):
        return self.request('invalidate')

    def close(self):
        with self._lock:
            if self._closed:
                return
            self.request('close')
            self._closed = True
            self._finalizer.detach()


def _release_snapshot(owner, handle):
    try:
        owner.request('release_snapshot', snapshot=handle)
    except Exception:
        pass


class Snapshot:
    def __init__(self, owner, handle):
        self.owner, self.handle, self.closed = owner, handle, False
        self._finalizer = weakref.finalize(self, _release_snapshot, owner, handle)

    def close(self):
        if not self.closed:
            self.owner.request('release_snapshot', snapshot=self.handle)
            self.closed = True
            self._finalizer.detach()


def _abandon_ticket(owner, handle, state):
    try:
        if state[0]:
            owner.invalidate()
        else:
            owner.request('abort', ticket=handle)
    except Exception:
        owner._uncertain = True


class Ticket:
    def __init__(self, owner, handle):
        self.owner, self.handle, self.closed = owner, handle, False
        self._applying = [False]
        self._finalizer = weakref.finalize(self, _abandon_ticket, owner, handle, self._applying)

    def begin_apply(self):
        if self.closed:
            raise RegistryError('invalid_ticket')
        self.owner.request('begin_apply', ticket=self.handle)
        self._applying[0] = True

    def commit(self):
        if self.closed:
            raise RegistryError('invalid_ticket')
        reply = self.owner.request('commit', ticket=self.handle)
        self.closed = True
        self._finalizer.detach()
        return reply

    def commit_observed(self, observation):
        if self.closed:
            raise RegistryError('invalid_ticket')
        reply = self.owner._observed('commit', observation, ticket=self.handle)
        self.closed = True
        self._finalizer.detach()
        return reply

    def abort(self):
        if self.closed:
            raise RegistryError('invalid_ticket')
        self.owner.request('abort', ticket=self.handle)
        self.closed = True
        self._finalizer.detach()
