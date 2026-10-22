"""Minimal ctypes transport for platform_core ABI 1."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys
import threading

ABI_VERSION = 1
OK = "ok"
CANCEL = "cancel"
UNAVAILABLE = "unavailable"
ERROR = "error"

_STATUS_OK = 0
_STATUS_CANCEL = 1
_STATUS_UNAVAILABLE = 2
_STATUS_ERROR = 3
_DIALOG_OPEN = 1
_DIALOG_SAVE = 2
_CHOICE_YES = 1
_CHOICE_NO = 2
_CHOICE_CANCEL = 3
_MAX_OUTPUT = 512 * 1024
_ENV = "NARWHAL_PLATFORM_CORE"
_ByteP = ctypes.POINTER(ctypes.c_uint8)


class _Utf8Slice(ctypes.Structure):
    _fields_ = [("data", _ByteP), ("length", ctypes.c_size_t)]


class _FilterPair(ctypes.Structure):
    _fields_ = [("struct_size", ctypes.c_uint32), ("abi_version", ctypes.c_uint32),
                ("description", _Utf8Slice), ("glob", _Utf8Slice)]


class _FileRequest(ctypes.Structure):
    _fields_ = [("struct_size", ctypes.c_uint32), ("abi_version", ctypes.c_uint32),
                ("dialog_kind", ctypes.c_uint32), ("reserved", ctypes.c_uint32),
                ("owner_window", ctypes.c_size_t), ("title", _Utf8Slice),
                ("start_path", _Utf8Slice), ("filters", ctypes.POINTER(_FilterPair)),
                ("filter_count", ctypes.c_size_t), ("output", _ByteP),
                ("output_capacity", ctypes.c_size_t)]


class _DialogResult(ctypes.Structure):
    _fields_ = [("struct_size", ctypes.c_uint32), ("abi_version", ctypes.c_uint32),
                ("status", ctypes.c_int32), ("error_code", ctypes.c_uint32),
                ("system_error", ctypes.c_uint32),
                ("selected_filter_index", ctypes.c_uint32),
                ("required_capacity", ctypes.c_size_t),
                ("written_length", ctypes.c_size_t), ("retry_token", ctypes.c_uint64)]


class _MessageRequest(ctypes.Structure):
    _fields_ = [("struct_size", ctypes.c_uint32), ("abi_version", ctypes.c_uint32),
                ("reserved0", ctypes.c_uint32), ("reserved1", ctypes.c_uint32),
                ("owner_window", ctypes.c_size_t), ("title", _Utf8Slice),
                ("message", _Utf8Slice)]


class _MessageResult(ctypes.Structure):
    _fields_ = [("struct_size", ctypes.c_uint32), ("abi_version", ctypes.c_uint32),
                ("status", ctypes.c_int32), ("choice", ctypes.c_uint32),
                ("error_code", ctypes.c_uint32), ("system_error", ctypes.c_uint32)]


class _Binding:
    def __init__(self, library):
        self.library = library
        self.file = library.narwhal_platform_file_dialog_v1
        self.discard = library.narwhal_platform_dialog_result_discard_v1
        self.message = library.narwhal_platform_message_box_v1
        self.file.argtypes = [ctypes.POINTER(_FileRequest), ctypes.POINTER(_DialogResult)]
        self.file.restype = ctypes.c_int32
        self.discard.argtypes = [ctypes.c_uint64]
        self.discard.restype = ctypes.c_int32
        self.message.argtypes = [ctypes.POINTER(_MessageRequest), ctypes.POINTER(_MessageResult)]
        self.message.restype = ctypes.c_int32


_lock = threading.Lock()
_signature = None
_binding = None
_cdll_factory = ctypes.CDLL


def _library_name():
    if sys.platform == "win32":
        return "narwhal_platform_core.dll"
    if sys.platform == "darwin":
        return "libnarwhal_platform_core.dylib"
    return "libnarwhal_platform_core.so"


def _platform_key():
    if sys.platform == "win32":
        return "windows-x86_64"
    if sys.platform == "darwin":
        return "macos-arm64" if os.uname().machine.lower() in ("arm64", "aarch64") else "macos-x86_64"
    return "linux-x86_64"


def _candidates():
    explicit = os.environ.get(_ENV)
    if explicit:
        return (Path(explicit).expanduser(),)
    package = Path(__file__).resolve().parent
    root = package.parent
    name = _library_name()
    platform = _platform_key()
    return (package / "native" / name, root / "runtime" / platform / "bin" / name,
            root / "artifacts" / "platform-core" / platform / name)


def _load():
    global _signature, _binding
    signature = (os.environ.get(_ENV) or "<packaged>", sys.platform)
    if signature == _signature:
        return _binding
    with _lock:
        if signature == _signature:
            return _binding
        _signature, _binding = signature, None
        for path in _candidates():
            try:
                if not path.is_file():
                    continue
                library = _cdll_factory(str(path))
                version = library.narwhal_platform_core_abi_version
                version.argtypes = []
                version.restype = ctypes.c_uint32
                if int(version()) != ABI_VERSION:
                    continue
                _binding = _Binding(library)
                break
            except (AttributeError, OSError, TypeError, ValueError):
                continue
        return _binding


def _utf8(value):
    data = str(value).encode("utf-8", errors="strict")
    storage = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
    return storage, _Utf8Slice(ctypes.cast(storage, _ByteP), len(data))


def _file_dialog(kind, title, start, filters):
    binding = _load()
    if binding is None:
        return UNAVAILABLE, None
    token = 0
    try:
        title_buf, title_slice = _utf8(title)
        start_buf, start_slice = _utf8(start)
        keepalive = [title_buf, start_buf]
        pairs = (_FilterPair * len(filters))()
        for index, (description, glob) in enumerate(filters):
            desc_buf, desc_slice = _utf8(description)
            glob_buf, glob_slice = _utf8(glob)
            keepalive.extend((desc_buf, glob_buf))
            pairs[index] = _FilterPair(ctypes.sizeof(_FilterPair), ABI_VERSION,
                                       desc_slice, glob_slice)
        output = (ctypes.c_uint8 * _MAX_OUTPUT)()
        request = _FileRequest(ctypes.sizeof(_FileRequest), ABI_VERSION, kind, 0, 0,
                               title_slice, start_slice, pairs, len(filters), output,
                               _MAX_OUTPUT)
        result = _DialogResult(ctypes.sizeof(_DialogResult), ABI_VERSION, _STATUS_ERROR,
                               0, 0, 0, 0, 0, 0)
        returned = int(binding.file(ctypes.byref(request), ctypes.byref(result)))
        token = int(result.retry_token)
        if (returned != result.status or result.struct_size != ctypes.sizeof(_DialogResult)
                or result.abi_version != ABI_VERSION):
            return ERROR, None
        if result.status == _STATUS_CANCEL:
            return CANCEL, None
        if result.status == _STATUS_UNAVAILABLE:
            return UNAVAILABLE, None
        if result.status != _STATUS_OK:
            return ERROR, None
        length = int(result.written_length)
        if (length >= _MAX_OUTPUT or result.required_capacity != length + 1
                or output[length] != 0):
            return ERROR, None
        raw = bytes(output[:length])
        if b"\0" in raw:
            return ERROR, None
        return OK, raw.decode("utf-8", errors="strict")
    except (TypeError, ValueError, OverflowError, UnicodeError, ctypes.ArgumentError):
        return ERROR, None
    except Exception:
        return ERROR, None
    finally:
        if token:
            try:
                binding.discard(token)
            except Exception:
                pass


def open_file(title, start, filters):
    return _file_dialog(_DIALOG_OPEN, title, start, filters)


def save_file(title, start, filters):
    return _file_dialog(_DIALOG_SAVE, title, start, filters)


def ask_yes_no_cancel(title, message):
    binding = _load()
    if binding is None:
        return UNAVAILABLE, None
    try:
        title_buf, title_slice = _utf8(title)
        message_buf, message_slice = _utf8(message)
        request = _MessageRequest(ctypes.sizeof(_MessageRequest), ABI_VERSION, 0, 0, 0,
                                  title_slice, message_slice)
        result = _MessageResult(ctypes.sizeof(_MessageResult), ABI_VERSION,
                                _STATUS_ERROR, 0, 0, 0)
        returned = int(binding.message(ctypes.byref(request), ctypes.byref(result)))
        if (returned != result.status or result.struct_size != ctypes.sizeof(_MessageResult)
                or result.abi_version != ABI_VERSION):
            return ERROR, None
        if result.status == _STATUS_CANCEL and result.choice == _CHOICE_CANCEL:
            return CANCEL, None
        if result.status == _STATUS_UNAVAILABLE:
            return UNAVAILABLE, None
        if result.status == _STATUS_OK and result.choice in (_CHOICE_YES, _CHOICE_NO):
            return OK, "yes" if result.choice == _CHOICE_YES else "no"
        return ERROR, None
    except (TypeError, ValueError, OverflowError, UnicodeError, ctypes.ArgumentError):
        return ERROR, None
    except Exception:
        return ERROR, None


def _reset_for_tests(cdll_factory=None):
    global _signature, _binding, _cdll_factory
    with _lock:
        _signature = None
        _binding = None
        _cdll_factory = ctypes.CDLL if cdll_factory is None else cdll_factory
