"""Persistent transcript ABI glue. Reuses the existing editor library handle.

No list is replayed into Rust. Python performs FFI, immutable decoding, lifecycle
and allocation-bound checks; native code owns mutations, normalization and search.
"""
import ctypes as C
import importlib.util

from pathlib import Path
import struct
import sys
import unicodedata
import weakref
from collections import OrderedDict

if __package__:
    from . import native_textfield as _editor
else:
    _name = '_narwhal_native_textfield'
    if _name not in sys.modules:
        _spec = importlib.util.spec_from_file_location(_name, Path(__file__).with_name('native_textfield.py'))
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules[_name] = _mod
        _spec.loader.exec_module(_mod)
    _editor = sys.modules[_name]

MAX_LINES = 262144
MAX_POINTS = 16 * 1024 * 1024
MAX_WIRE = 4 + MAX_LINES * 4 + MAX_POINTS * 4
I64_MAX = (1 << 63) - 1

class Request(C.Structure):
    _fields_ = [('op', C.c_uint32), ('flags', C.c_uint32), ('revision', C.c_uint64),
                ('a', C.c_int64), ('b', C.c_int64), ('c', C.c_int64)]

class Reply(C.Structure):
    _fields_ = [(name, C.c_uint64) for name in ('handle', 'revision', 'len', 'token', 'count', 'bytes', 'kind')]


def library():
    lib = _editor.library()
    if lib is None:
        raise RuntimeError('transcript requires the native UI core')
    try:
        lib.narwhal_transcript_abi.argtypes = []
        lib.narwhal_transcript_abi.restype = C.c_uint32
        lib.narwhal_transcript_capabilities.argtypes = []
        lib.narwhal_transcript_capabilities.restype = C.c_uint64
        if lib.narwhal_transcript_abi() != 1 or lib.narwhal_transcript_capabilities() & 7 != 7:
            raise RuntimeError('unsupported transcript ABI/capabilities')
        lib.narwhal_transcript_apply.argtypes = [C.c_uint64, C.POINTER(Request), C.c_size_t,
                                               C.c_void_p, C.c_size_t, C.POINTER(Reply), C.c_size_t]
        lib.narwhal_transcript_apply.restype = C.c_uint32
        lib.narwhal_transcript_read.argtypes = [C.c_uint64, C.c_uint64, C.c_uint64,
                                              C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
        lib.narwhal_transcript_read.restype = C.c_uint32
        lib.narwhal_transcript_release.argtypes = [C.c_uint64, C.c_uint64]
        lib.narwhal_transcript_release.restype = C.c_uint32
        lib.narwhal_transcript_close.argtypes = [C.c_uint64]
        lib.narwhal_transcript_close.restype = C.c_uint32
        lib.narwhal_transcript_stats.argtypes = [C.POINTER(C.c_uint64), C.c_size_t]
        lib.narwhal_transcript_stats.restype = C.c_uint32
    except (AttributeError, RuntimeError) as exc:
        raise RuntimeError('UI core lacks supported transcript ABI; rebuild the additive development library') from exc
    return lib


def encode_lines(lines):
    if len(lines) > MAX_LINES or sum(map(len, lines)) > MAX_POINTS:
        raise OverflowError('transcript batch exceeds native resource bound')
    chunks = [struct.pack('<I', len(lines))]
    for line in lines:
        chunks.extend((struct.pack('<I', len(line)), line.encode('utf-32-le', 'surrogatepass')))
    return b''.join(chunks)


class Owner:
    def __init__(self, lib, limit, normalize):
        self.lib = lib
        self.reply = None
        self.failed = False
        self.cache = OrderedDict()
        self.calls = self.uploaded = self.downloaded = 0
        self.limit = limit
        self.command(0, a=min(I64_MAX, limit), flags=int(normalize) | (2 if unicodedata.unidata_version == '16.0.0' else 0))
        try:
            self.finalizer = weakref.finalize(self, lib.narwhal_transcript_close, self.reply.handle)
        except BaseException:
            lib.narwhal_transcript_close(self.reply.handle)
            raise

    def check(self):
        if self.failed:
            raise RuntimeError('transcript unavailable after native uncertainty; operation not replayed')
        if hasattr(self, 'finalizer') and not self.finalizer.alive:
            raise RuntimeError('transcript is closed')

    def command(self, op, *, a=0, b=0, c=0, flags=0, data=b''):
        self.check()
        if len(data) > MAX_WIRE:
            raise OverflowError('transcript request exceeds native bound')
        old = self.reply
        request = Request(op, flags, 0 if old is None else old.revision, a, b, c)
        out = Reply()
        self.calls += 1
        self.uploaded += len(data)
        try:
            rc = self.lib.narwhal_transcript_apply(0 if old is None else old.handle, C.byref(request),
                                                 C.sizeof(request), data, len(data), C.byref(out), C.sizeof(out))
        except BaseException:
            self.failed = True
            raise  # The native mutation may already have committed.
        if rc:
            self.failed = True
            raise RuntimeError(f'transcript operation failed ({rc}); not replayed')
        mutation = op in (0, 1, 2, 3, 6)
        expected_kind = 0 if mutation else 2 if op == 5 else 3 if op == 7 else 1
        valid = (out.handle != 0 and out.revision != 0 and out.len <= MAX_LINES
                 and out.kind == expected_kind and out.count <= out.len and out.bytes <= MAX_WIRE)
        if old is not None:
            valid = valid and ((out.handle != old.handle) if op == 6 else (out.handle == old.handle))
            expected_rev = old.revision + 1 if op in (1, 2, 3) else old.revision
            valid = valid and out.revision == expected_rev
        if mutation:
            valid = valid and out.token == out.count == out.bytes == out.kind == 0
            if op == 1:
                count = struct.unpack_from('<I', data)[0]
                expected_len = old.len + count
                if self.limit:
                    expected_len = min(self.limit, expected_len)
                valid = valid and out.len == expected_len
            elif op == 2:
                valid = valid and out.len == max(0, old.len - a)
            elif op in (0, 3):
                valid = valid and out.len == 0
        else:
            valid = valid and out.token != 0 and out.len == old.len
            if out.kind == 1:
                expected_count = 1 if op == 8 and out.count else len(range(a, b, c)) if op == 4 else 0
                valid = valid and out.count == expected_count and out.bytes >= 4 + 4 * out.count
            if out.kind == 2:
                valid = valid and out.bytes == out.count * 8
            if out.kind == 3:
                valid = valid and out.count == out.len and out.bytes % 4 == 0
        if not valid:
            self.failed = True
            raise RuntimeError('invalid transcript reply; not replayed')
        if op == 6:
            return out
        if mutation:
            self.reply = out
            self.cache.clear()
        return out

    def consume(self, snapshot):
        try:
            # Validate allocation bounds in command BEFORE using a reported size.
            buffer = C.create_string_buffer(max(1, snapshot.bytes))
            needed = C.c_size_t()
            self.calls += 1
            rc = self.lib.narwhal_transcript_read(self.reply.handle, snapshot.token, snapshot.revision,
                                                buffer, snapshot.bytes, C.byref(needed))
            if rc != 0 or needed.value != snapshot.bytes or needed.value > len(buffer):
                raise RuntimeError('invalid/stale transcript snapshot read; no retry')
            data = C.string_at(buffer, needed.value)
            self.downloaded += len(data)
            if snapshot.kind == 3:
                return data.decode('utf-32-le', 'surrogatepass')
            if snapshot.kind == 2:
                indices = tuple(value[0] for value in struct.iter_unpack('<Q', data))
                if any(i >= snapshot.len for i in indices) or any(a >= b for a, b in zip(indices, indices[1:])):
                    raise RuntimeError('invalid transcript search indices')
                return indices
            if len(data) < 4 or struct.unpack_from('<I', data)[0] != snapshot.count:
                raise RuntimeError('invalid transcript line count')
            at, lines = 4, []
            for _ in range(snapshot.count):
                if at + 4 > len(data):
                    raise RuntimeError('truncated transcript line header')
                length = struct.unpack_from('<I', data, at)[0]
                at += 4
                end = at + length * 4
                if end > len(data):
                    raise RuntimeError('truncated transcript line')
                lines.append(data[at:end].decode('utf-32-le', 'surrogatepass'))
                at = end
            if at != len(data):
                raise RuntimeError('trailing transcript bytes')
            return tuple(lines)
        except BaseException:
            self.failed = True
            raise
        finally:
            self.calls += 1
            try:
                rc = self.lib.narwhal_transcript_release(self.reply.handle, snapshot.token)
            except BaseException:
                self.failed = True
                raise
            if rc:
                self.failed = True
                raise RuntimeError('transcript snapshot release failed')

    def projection(self, op, a=0, b=0, c=0, data=b''):
        self.check()
        key = (op, a, b, c, data)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        value = self.consume(self.command(op, a=a, b=b, c=c, data=data))
        # Bound both entry count and retained content; don't pin a full long session
        # alongside every queried substring/search needle in the host cache.
        if len(data) <= 4096 and (len(value) if isinstance(value, str) else sum(len(v) if isinstance(v, str) else 1 for v in value)) <= 65536:
            self.cache[key] = value
            while len(self.cache) > 8:
                self.cache.popitem(last=False)
        return value

    def clone(self):
        result = self.command(6)
        try:
            other = object.__new__(Owner)
            other.__dict__ = self.__dict__.copy()
            other.reply = result
            other.cache = self.cache.copy()
            other.finalizer = weakref.finalize(other, self.lib.narwhal_transcript_close, result.handle)
            return other
        except BaseException:
            self.lib.narwhal_transcript_close(result.handle)
            raise

    def close(self):
        if self.finalizer.alive:
            try:
                rc = self.lib.narwhal_transcript_close(self.reply.handle)
            except BaseException:
                self.failed = True
                raise
            if rc not in (0, 2):
                self.failed = True
                raise RuntimeError(f'transcript close failed ({rc})')
            self.finalizer.detach()
            self.cache.clear()
