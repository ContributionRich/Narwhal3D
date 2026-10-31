"""List-compatible live transcript with a persistent Rust owner.

Raw by default: only AppState.log decides when to prune. The private normalized/
limited policy is shared by CommandHistory without imposing it on live UI callers.
Python is limited to sequence/coercion glue; native support is required.
"""
import importlib.util
import operator
from pathlib import Path
import sys
import unicodedata

if __package__:
    from . import native_transcript as native
else:
    _name = '_narwhal_native_transcript'
    if _name not in sys.modules:
        _spec = importlib.util.spec_from_file_location(_name, Path(__file__).with_name('native_transcript.py'))
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules[_name] = _mod
        _spec.loader.exec_module(_mod)
    native = sys.modules[_name]


class Transcript:
    def __init__(self, *, limit=0, normalize=False):
        if type(limit) is not int or limit < 0 or type(normalize) is not bool:
            raise TypeError('Transcript policy requires a nonnegative int limit and bool normalize')
        self._limit, self._normalize = limit, normalize
        self._closed = False
        if unicodedata.unidata_version not in ('15.1.0', '16.0.0'):
            raise RuntimeError('transcript requires Unicode 15.1 or 16 native support')
        lib = native.library()
        if lib is None:
            raise RuntimeError('transcript requires the native UI core')
        self._native = native.Owner(lib, limit, normalize)

    def _check(self):
        if self._closed:
            raise RuntimeError('transcript is closed')
        self._native.check()

    def __len__(self):
        self._check()
        return self._native.reply.len

    def __getitem__(self, index):
        self._check()

        if isinstance(index, slice):
            a, b, c = index.indices(len(self))
            c = max(-native.I64_MAX, min(native.I64_MAX, c))
            return list(self._native.projection(4, a, b, c))
        index = operator.index(index)
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError('list index out of range')
        return self._native.projection(4, index, index + 1, 1)[0]

    def __iter__(self):
        # An immutable batched read, never a native call for each yielded line.
        return iter(self[:])

    def __delitem__(self, index):
        self._check()
        if not isinstance(index, slice) or index.start not in (None, 0) or index.step not in (None, 1):
            raise NotImplementedError('Transcript supports prefix slice deletion only')
        _, stop, _ = index.indices(len(self))
        self._native.command(2, a=stop)

    def __setitem__(self, index, value):
        raise NotImplementedError('Transcript item replacement is not supported; append or clear explicitly')

    def append(self, line):
        self._check()
        if type(line) is not str:
            raise TypeError('Transcript.append requires an exact string; subclass callbacks are not supported')
        self._native.command(1, data=native.encode_lines([line]))

    def append_many(self, lines):
        """Atomic batch admission: realize/validate first; exact strings only."""
        self._check()
        batch = list(lines)
        if any(type(line) is not str for line in batch):
            raise TypeError('Transcript.append_many requires exact strings')
        self._native.command(1, data=native.encode_lines(batch))

    def clear(self):
        self._check()
        self._native.command(3)

    def tail(self, count):
        if type(count) is not int or count < 0:
            raise ValueError('Transcript.tail requires a nonnegative integer')
        return self[-count:] if count else self[0:0]

    def last_command(self):
        """Last '> ' line without its two-character prefix, or None; never execute."""
        self._check()
        rows = self._native.projection(8)
        return rows[0] if rows else None

    def search(self, text):
        self._check()
        if not isinstance(text, str) or not text:
            raise ValueError('Transcript.search requires a nonempty string')
        if type(text) is not str:
            raise TypeError('Transcript.search requires an exact string; subclass callbacks are not supported')
        return list(self._native.projection(5, data=text.encode('utf-32-le', 'surrogatepass')))

    def to_text(self):
        self._check()
        return self._native.projection(7)

    def close(self):
        if not self._closed:
            self._native.close()
            self._closed = True

    def __copy__(self):
        self._check()
        other = object.__new__(type(self))
        other.__dict__ = self.__dict__.copy()
        other._native = self._native.clone()
        return other

    def __deepcopy__(self, memo):
        other = self.__copy__()
        memo[id(self)] = other

        return other

    def copy(self):
        return self[:]

    def __eq__(self, other):
        return self[:] == (other[:] if isinstance(other, Transcript) else other)

    def __repr__(self):
        return repr(self[:])
