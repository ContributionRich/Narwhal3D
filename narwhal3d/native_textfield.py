"""Typed persistent editor ABI. No Blender, clipboard, font or layout dependencies.

Python owns FFI/lifetime management and immutable read projections only. Exceptional
Python values transfer ownership to the unchanged compatibility model before running
user coercions. A native error is never retried through Python after possible mutation.
"""
import ctypes as C
import os
import sys
import unicodedata
import weakref
from pathlib import Path
from typing import TypeGuard

LIMIT = 16 * 1024 * 1024
I64_MIN, I64_MAX = -(1 << 63), (1 << 63) - 1


class State(C.Structure):
    _fields_ = [("handle", C.c_uint64), ("revision", C.c_uint64),
                ("text_revision", C.c_uint64), ("length", C.c_uint64),
                ("caret", C.c_uint64), ("anchor", C.c_int64),
                ("max_chars", C.c_int64), ("flags", C.c_uint64),
                ("change_start", C.c_uint64), ("change_end", C.c_uint64),
                ("inserted", C.c_uint64)]


_loaded_library: C.CDLL | None = None


def library() -> C.CDLL | None:
    global _loaded_library
    if _loaded_library is not None:
        return _loaded_library
    configured = os.environ.get("NARWHAL_UI_CORE_LIBRARY")
    name = ("narwhal_ui_core.dll" if sys.platform == "win32" else
            "libnarwhal_ui_core.dylib" if sys.platform == "darwin" else
            "libnarwhal_ui_core.so")
    path = Path(configured) if configured else Path(__file__).with_name("native") / name
    if configured is None and not path.is_file():
        import platform
        system = {'win32': 'windows', 'darwin': 'macos'}.get(sys.platform, sys.platform)
        machine = {'amd64': 'x86_64', 'aarch64': 'arm64'}.get(platform.machine().lower(), platform.machine().lower())
        path = Path(__file__).resolve().parents[1] / 'artifacts/ui-core' / (system + '-' + machine) / name
    if configured is None and not path.is_file():
        return None
    if configured == '':
        raise RuntimeError('empty NARWHAL_UI_CORE_LIBRARY path')
    try:
        lib = C.CDLL(str(path))
        lib.narwhal_editor_abi.argtypes = []
        lib.narwhal_editor_abi.restype = C.c_uint32
        if lib.narwhal_editor_abi() != 1:
            raise RuntimeError("editor ABI mismatch")
        lib.narwhal_editor_edit.argtypes = [C.c_uint64, C.c_uint32, C.c_int64,
            C.c_uint32, C.c_void_p, C.c_size_t, C.POINTER(State), C.c_size_t]
        lib.narwhal_editor_edit.restype = C.c_uint32
        lib.narwhal_editor_read.argtypes = [C.c_uint64, C.c_uint64, C.c_size_t, C.c_void_p, C.c_size_t]
        lib.narwhal_editor_read.restype = C.c_uint32
        lib.narwhal_editor_close.argtypes = [C.c_uint64]
        lib.narwhal_editor_close.restype = C.c_uint32
    except (OSError, AttributeError, RuntimeError) as exc:
        raise RuntimeError("cannot load configured/installed editor core") from exc
    _loaded_library = lib
    return lib


def integer(value: object) -> TypeGuard[int]:
    return type(value) is int and I64_MIN <= value <= I64_MAX


class Editor:
    """One native owner; the cached State/text are read-only projections."""
    def __init__(self, lib: C.CDLL, text: str, multiline: bool, maximum: int | None):
        self.lib = lib
        self._state: State | None = None
        self.failed = False
        self.text_cache = ""
        self.text_revision = 0
        self.patch: tuple[int, int, int] | None = None
        self.calls = self.uploaded = self.downloaded = 0
        flags = int(multiline) | (2 if maximum is not None else 0)
        if unicodedata.unidata_version == "16.0.0":
            flags |= 8
        self.apply(0, maximum or 0, flags, text)
        self.finalizer = weakref.finalize(self, lib.narwhal_editor_close, self.state.handle)

    @property
    def state(self) -> State:
        if self._state is None:
            raise RuntimeError("editor has not been created")
        return self._state

    def apply(self, op: int, value: int = 0, flags: int = 0, text: str | None = None) -> State | bool:
        if self.failed:
            raise RuntimeError("editor unavailable after a native failure")
        if hasattr(self, "finalizer") and not self.finalizer.alive:
            raise RuntimeError("editor is closed")
        # UTF-32 is the Python code-point domain, including raw assigned surrogates.
        data = None if text is None else text.encode("utf-32-le" if sys.byteorder == "little"
                                                    else "utf-32-be", "surrogatepass")
        length = 0 if data is None else len(data) // 4
        if length > LIMIT:
            raise OverflowError("editor input exceeds native bound")
        data = None if data is None else (C.c_uint32 * length).from_buffer_copy(data)
        out = State()
        handle = 0 if self._state is None else self.state.handle
        self.calls += 1
        self.uploaded += length * 4
        rc = self.lib.narwhal_editor_edit(handle, op, value, flags, data, length,
                                         C.byref(out), C.sizeof(out))
        if rc != 0:
            self.failed = True
            raise RuntimeError(f"editor operation failed ({rc}); not replayed")
        if (not out.handle or (handle and op != 10 and out.handle != handle)
                or (op == 10 and out.handle == handle)
                or out.length > LIMIT or out.caret > out.length
                or out.anchor < -1 or out.anchor > out.length or out.flags & ~15
                or not out.revision or not 0 < out.text_revision <= out.revision
                or (self._state is not None and op != 10 and
                    (out.revision <= self.state.revision or
                     out.text_revision < self.state.text_revision))):
            self.failed = True
            raise RuntimeError("invalid editor projection; not replayed")
        if op == 10:
            return out
        if (self._state is not None and out.text_revision == self.state.text_revision
                and (out.length != self.state.length or out.change_start or out.change_end or out.inserted)):
            self.failed = True
            raise RuntimeError("inconsistent editor text revision; not replayed")
        if self._state is not None and out.text_revision != self.state.text_revision:
            if (not 0 <= out.change_start <= out.change_end <= self.state.length
                    or out.inserted > out.length
                    or out.length != self.state.length - (out.change_end - out.change_start) + out.inserted):
                self.failed = True
                raise RuntimeError("invalid editor change range; not replayed")
            self.patch = ((out.change_start, out.change_end, out.inserted)
                          if self.text_revision == self.state.text_revision else None)
        self._state = out
        return bool(out.flags & 4)

    def text(self) -> str:
        if self.failed or not self.finalizer.alive:
            raise RuntimeError("editor is unavailable")
        s = self.state
        if self.text_revision != s.text_revision:
            # Length is validated before allocation; no native-provided sizing retry.
            start, end, count = self.patch if self.patch is not None else (0, 0, s.length)
            buf = (C.c_uint32 * max(1, count))()
            self.calls += 1
            rc = self.lib.narwhal_editor_read(s.handle, s.text_revision, start, buf, count)
            if rc != 0:
                self.failed = True
                raise RuntimeError(f"editor read failed ({rc}); not replayed")
            data = C.string_at(buf, count * 4)
            self.downloaded += len(data)
            try:
                inserted = data.decode("utf-32-le" if sys.byteorder == "little"
                                              else "utf-32-be", "surrogatepass")
            except UnicodeError:
                self.failed = True
                raise RuntimeError("invalid editor code point") from None
            self.text_cache = (self.text_cache[:start] + inserted + self.text_cache[end:]
                               if self.patch is not None else inserted)
            self.text_revision = s.text_revision
            self.patch = None
        return self.text_cache

    def clone(self):
        state = self.apply(10)
        assert isinstance(state, State)
        other = object.__new__(Editor)
        other.__dict__ = self.__dict__.copy()
        other._state = state
        other.finalizer = weakref.finalize(other, self.lib.narwhal_editor_close, state.handle)
        return other

    def close(self):
        self.finalizer()


def facade(Legacy):
    class TextField(Legacy):
        def __init__(self, text="", multiline=False, max_chars=None):
            self._native = None
            self._backend_reason = "legacy Python value/subclass"
            lib = library()
            if (type(self) is TextField and type(text) is str and type(multiline) is bool
                    and (max_chars is None or integer(max_chars)) and len(text) <= LIMIT
                    and unicodedata.unidata_version in ("15.1.0", "16.0.0") and lib is not None):
                self._native = Editor(lib, text, multiline, max_chars)
                self._backend_reason = "native"
            else:
                if lib is None:
                    self._backend_reason = "native library missing"
                elif unicodedata.unidata_version not in ("15.1.0", "16.0.0"):
                    self._backend_reason = "unsupported Unicode version"
                super().__init__(text, multiline, max_chars)

        def _handover(self):
            n = self._native
            if n is not None:
                # Obtain everything first. A failed read cannot leave split authority.
                state = dict(text=n.text(), caret=n.state.caret,
                             anchor=None if n.state.anchor == -1 else n.state.anchor,
                             multiline=bool(n.state.flags & 1),
                             max_chars=n.state.max_chars if n.state.flags & 2 else None)
                n.close()
                self.__dict__.update(state)
                self._native = None
                self._backend_reason = "atomic legacy handover"

        def _get(self, name):
            n = self._native
            if n is None:
                return self.__dict__[name]
            if n.failed or not n.finalizer.alive:
                raise RuntimeError("editor is unavailable")
            if name == "text":
                return n.text()
            if name == "caret":
                return n.state.caret
            if name == "anchor":
                return None if n.state.anchor == -1 else n.state.anchor
            if name == "multiline":
                return bool(n.state.flags & 1)
            return n.state.max_chars if n.state.flags & 2 else None

        def _set(self, name: str, value: object):
            n = self._native
            if n is not None:
                if name == "text" and type(value) is str and len(value) <= LIMIT:
                    if n.state.caret <= len(value) and n.state.anchor <= len(value):
                        n.apply(11, text=value)
                        return
                elif name in ("caret", "anchor"):
                    if name == "anchor" and value is None:
                        n.apply(13, flags=1)
                        return
                    if integer(value) and 0 <= value <= n.state.length:
                        n.apply(12 if name == "caret" else 13, value)
                        return
                elif name == "multiline" and type(value) is bool:
                    n.apply(14, int(value))
                    return
                elif name == "max_chars" and (value is None or integer(value)):
                    n.apply(15, value or 0, int(value is None))
                    return
                self._handover()
            self.__dict__[name] = value

        def set_text(self, text):
            if self._native is not None and type(text) is str and len(text) <= LIMIT:
                self._native.apply(1, text=text)
            else:
                self._handover()
                return Legacy.set_text(self, text)

        def insert(self, s):
            n = self._native
            if n is not None and type(s) is str and len(s) + n.state.length <= LIMIT:
                return n.apply(2, text=s)
            self._handover()
            return Legacy.insert(self, s)

        def set_caret(self, index, select=False):
            if self._native is not None and type(index) is int and type(select) is bool:
                self._native.apply(5, max(I64_MIN, min(I64_MAX, index)), int(select))
            else:
                self._handover()
                return Legacy.set_caret(self, index, select)

        def move(self, delta, select=False):
            if self._native is not None and type(delta) is int and type(select) is bool:
                self._native.apply(6, max(I64_MIN, min(I64_MAX, delta)), int(select))
            else:
                self._handover()
                return Legacy.move(self, delta, select)

        def home(self, select=False):
            if self._native is not None and type(select) is bool:
                self._native.apply(7, flags=int(select))
            else:
                self._handover()
                return Legacy.home(self, select)

        def end(self, select=False):
            if self._native is not None and type(select) is bool:
                self._native.apply(8, flags=int(select))
            else:
                self._handover()
                return Legacy.end(self, select)

        def backspace(self):
            if self._native is not None:
                self._native.apply(3)
            else:
                return Legacy.backspace(self)

        def delete(self):
            if self._native is not None:
                self._native.apply(4)
            else:
                return Legacy.delete(self)

        def select_all(self):
            if self._native is not None:
                self._native.apply(9)
            else:
                return Legacy.select_all(self)

        def cut(self):
            if self._native is None:
                return Legacy.cut(self)
            text = self.selected_text()
            if text:
                self._native.apply(3)
            return text

        def _delete_selection(self):
            if self._native is None:
                return Legacy._delete_selection(self)
            return self._native.apply(16)

        def __copy__(self):
            other = object.__new__(type(self))
            other.__dict__ = self.__dict__.copy()
            if self._native is not None:
                other._native = self._native.clone()
            return other

        def __deepcopy__(self, memo):
            import copy
            other = object.__new__(type(self))
            memo[id(self)] = other
            other.__dict__ = {k: (v.clone() if k == "_native" and v is not None
                                 else copy.deepcopy(v, memo)) for k, v in self.__dict__.items()}
            return other

    for name in ("text", "caret", "anchor", "multiline", "max_chars"):
        setattr(TextField, name, property(lambda self, key=name: self._get(key),
                                         lambda self, value, key=name: self._set(key, value)))
    TextField.__module__ = Legacy.__module__
    TextField.__qualname__ = "TextField"
    return TextField
