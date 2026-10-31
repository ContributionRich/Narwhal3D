"""Command History facade on required persistent Rust transcript storage.

MEASURED: CommandHistory lists the last 500 command lines of the session.
OURS: strip all trailing CR/LF, lower-substring search (not casefold), detached
lines(last), and sibling-temp/write/os.replace save. This does not add an F2 UI.
Python owns validation, sequence coercion, copying and filesystem integration;
Rust owns retention, normalization, ordering, search and text projections.
String-subclass callbacks are explicitly unsupported, never a backend switch.
"""

__all__ = ["CommandHistory"]


# Host-only import compatibility for file-path test consumers.
if __package__:
    from .command_transcript import Transcript as _Transcript
else:
    import importlib.util as _importlib_util
    import pathlib as _pathlib
    import sys as _sys
    _name = '_narwhal_command_transcript'
    if _name not in _sys.modules:
        _spec = _importlib_util.spec_from_file_location(
            _name, _pathlib.Path(__file__).with_name('command_transcript.py'))
        _module = _importlib_util.module_from_spec(_spec)
        _sys.modules[_name] = _module
        _spec.loader.exec_module(_module)
    _Transcript = _sys.modules[_name].Transcript


class CommandHistory:
    """Standalone normalized/capped policy, distinct from raw AppState history."""
    def __init__(self, limit=500):
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be an int >= 1; got %r" % (limit,))
        # Preserve the public value, but admit only its integer payload to Rust.
        self._limit = limit
        self._lines = _Transcript(limit=int.__index__(limit), normalize=True)

    @property
    def limit(self):
        return self._limit

    def __len__(self):
        return len(self._lines)

    def append(self, line):
        if not isinstance(line, str):
            raise ValueError("a history line is a string; got %s" % type(line).__name__)
        self._lines.append(line)

    def lines(self, last=None):
        if last is None:
            return self._lines[:]
        if not isinstance(last, int) or isinstance(last, bool) or last < 0:
            raise ValueError("lines(last) takes None or an int >= 0; got %r" % (last,))
        return self._lines.tail(int.__index__(last))

    def search(self, text):
        if not isinstance(text, str):
            raise ValueError("search(text) takes a string; got %s" % type(text).__name__)
        if not text:
            raise ValueError("search text must be non-empty")
        return self._lines.search(text)

    def clear(self):
        self._lines.clear()

    def save(self, path):
        # Rust supplies one immutable, revision-bound text projection. Filesystem
        # failures preserve the old destination; existing temp-file policy is unchanged.
        import os
        data = self._lines.to_text()
        directory = os.path.dirname(os.path.abspath(path))
        tmp = os.path.join(directory, '.' + os.path.basename(path) + '.tmp')
        with open(tmp, 'w', encoding='utf-8') as fh:
            fh.write(data)
        os.replace(tmp, path)

    def close(self):
        self._lines.close()

    def __copy__(self):
        import copy
        other = object.__new__(type(self))
        other.__dict__ = self.__dict__.copy()
        other._lines = copy.copy(self._lines)
        return other

    def __deepcopy__(self, memo):
        import copy
        other = object.__new__(type(self))
        memo[id(self)] = other
        other.__dict__ = copy.deepcopy(self.__dict__, memo)
        return other
