"""Minimal ctypes boundary for ABI-4 exact-geometry edit sessions.

Geometry creation, traversal, selection, and policy stay in the native core.  Callers
lazily supply opaque BinTools bytes only when a session must be imported.
"""
from __future__ import annotations

from collections import OrderedDict
import ctypes
import os
from pathlib import Path
import sys
import threading

ABI_VERSION = 4
MAX_BUFFER_BYTES = 512 * 1024 * 1024
VALIDATION_LIVE = 1
VALIDATION_COMMIT = 2
STATUS_OK = 0
STATUS_LIMIT_EXCEEDED = -2
STATUS_POLICY_REFUSED = -7
STATUS_SMOOTHNESS_CREASE = -8
CAP_EDIT_SESSIONS = 1 << 5
_REFUSAL_DETAILS = {
    1: "input is not one airtight solid",
    2: "component index is outside the solid",
    3: "selected or incident topology is degenerate",
    4: "affected boundary topology is not supported exactly",
    5: "strict selected-component containment was not preserved",
    6: "the requested move changed topology",
    7: "no valid exact local-move candidate exists",
    8: "more than one exact local-move candidate remains",
    9: "the exact local move would introduce a smoothness crease",
    10: "the exact local move exceeded its tolerance budget",
    11: "the requested move would self-intersect",
    12: "the requested move exceeded native resource limits",
}

_ENV = "NARWHAL_GEOMETRY_CORE"
_MAX_SESSIONS = 8
_ByteP = ctypes.POINTER(ctypes.c_ubyte)
_Certificate = ctypes.c_uint64 * 31  # 248 bytes, naturally aligned to 8 bytes.
_CertificateP = ctypes.POINTER(_Certificate)
_lock = threading.RLock()
_sessions = OrderedDict()
_signature = None
_binding_cache = None
_backend_detail = "not checked"
_cdll_factory = ctypes.CDLL
_windows_dll_directory_handles = {}


class MoveResult:
    """One explicit native outcome: ``ok``, ``unavailable``, ``refused``, or ``error``."""

    __slots__ = ("kind", "data", "status", "refusal_reason", "detail")

    def __init__(self, kind, data=None, status=None, refusal_reason=0, detail=""):
        self.kind = kind
        self.data = data
        self.status = status
        self.refusal_reason = refusal_reason
        self.detail = detail

    @property
    def ok(self):
        return self.kind == "ok"

    @property
    def unavailable(self):
        return self.kind == "unavailable"

    @property
    def refused(self):
        return self.kind == "refused"


class _Binding:
    __slots__ = ("library", "path", "import_session", "move_mode", "release")

    def __init__(self, library, path, import_session, move_mode, release):
        self.library = library
        self.path = path
        self.import_session = import_session
        self.move_mode = move_mode
        self.release = release


class _Session:
    __slots__ = ("source_identity", "handle", "binding")

    def __init__(self, source_identity, handle, binding):
        self.source_identity = source_identity
        self.handle = handle
        self.binding = binding


def _library_name():
    if sys.platform == "win32":
        return "narwhal_geometry_core.dll"
    if sys.platform == "darwin":
        return "libnarwhal_geometry_core.dylib"
    return "libnarwhal_geometry_core.so"


def _platform_key():
    if sys.platform == "win32":
        return "windows-x86_64"
    if sys.platform == "darwin":
        machine = os.uname().machine.lower()
        return "macos-arm64" if machine in ("arm64", "aarch64") else "macos-x86_64"
    return "linux-x86_64"


def _candidates():
    explicit = os.environ.get(_ENV)
    if explicit:
        return (Path(explicit).expanduser(),)
    root = Path(__file__).resolve().parents[1]
    name = _library_name()
    platform_key = _platform_key()
    return (
        Path(__file__).resolve().parent / "native" / name,
        root / "runtime" / platform_key / "bin" / name,
        root / "artifacts" / "geometry-core" / platform_key / name,
    )


def _windows_occt_dll_directories(search_paths=None, platform=None):
    """Return bounded active-wheel DLL directories for Windows dependency loading."""
    if (sys.platform if platform is None else platform) != "win32":
        return ()
    paths = sys.path if search_paths is None else search_paths
    found = []
    seen = set()
    for value in paths:
        if not value:
            continue
        base = Path(value)
        candidate = (base if base.name.lower() == "cadquery_ocp_novtk.libs"
                     else base / "cadquery_ocp_novtk.libs")
        try:
            if not candidate.is_dir():
                continue
            key = os.path.normcase(str(candidate.resolve()))
        except (OSError, RuntimeError):
            continue
        if key not in seen:
            seen.add(key)
            found.append(candidate)
    return tuple(found)


def _register_windows_dependencies():
    if sys.platform != "win32" or not hasattr(os, "add_dll_directory"):
        return
    for directory in _windows_occt_dll_directories():
        key = os.path.normcase(str(directory.resolve()))
        if key in _windows_dll_directory_handles:
            continue
        _windows_dll_directory_handles[key] = os.add_dll_directory(str(directory))


def _release_all_locked():
    while _sessions:
        _key, session = _sessions.popitem(last=False)
        try:
            session.binding.release(session.handle)
        except Exception:
            pass


def _load_locked():
    global _signature, _binding_cache, _backend_detail
    explicit = os.environ.get(_ENV)
    signature = explicit or "<packaged>"
    if signature == _signature:
        return _binding_cache
    _release_all_locked()
    _signature = signature
    _binding_cache = None
    _backend_detail = "native geometry core unavailable"
    for path in _candidates():
        try:
            if not path.is_file():
                continue
            _register_windows_dependencies()
            library = _cdll_factory(str(path))
            version = library.narwhal_geometry_core_abi_version
            version.argtypes = []
            version.restype = ctypes.c_uint32
            capabilities = library.narwhal_geometry_core_capabilities
            capabilities.argtypes = []
            capabilities.restype = ctypes.c_uint64
            actual_abi = int(version())
            if actual_abi != ABI_VERSION:
                _backend_detail = f"native geometry ABI {actual_abi}, expected {ABI_VERSION}: {path}"
                continue
            if not (int(capabilities()) & CAP_EDIT_SESSIONS):
                _backend_detail = f"native geometry core lacks edit sessions: {path}"
                continue
            import_session = library.narwhal_geometry_core_edit_session_import
            import_session.argtypes = [_ByteP, ctypes.c_size_t, ctypes.POINTER(ctypes.c_uint64)]
            import_session.restype = ctypes.c_int32
            move_mode = library.narwhal_geometry_core_edit_session_move_mode
            move_mode.argtypes = [
                ctypes.c_uint64, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint64,
                ctypes.c_double, ctypes.c_double, ctypes.c_double,
                _ByteP, ctypes.c_size_t, _CertificateP,
            ]
            move_mode.restype = ctypes.c_int64
            release = library.narwhal_geometry_core_edit_session_release
            release.argtypes = [ctypes.c_uint64]
            release.restype = ctypes.c_int32
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            _backend_detail = f"could not load native geometry core {path}: {exc}"
            continue
        _binding_cache = _Binding(library, path, import_session, move_mode, release)
        _backend_detail = f"native geometry ABI {ABI_VERSION}: {path}"
        return _binding_cache
    return None


def backend_status():
    with _lock:
        _load_locked()
        return _backend_detail


def available():
    with _lock:
        return _load_locked() is not None


def _outcome(status, certificate=None, detail=""):
    refusal_reason = 0
    certificate_status = status
    failure_stage = 0
    failure_detail = 0
    diagnostics = ""
    if certificate is not None:
        certificate_status = ctypes.c_int32.from_buffer(certificate, 8).value
        refusal_reason = ctypes.c_uint32.from_buffer(certificate, 12).value
        failure_stage = ctypes.c_uint32.from_buffer(certificate, 72).value
        failure_detail = ctypes.c_uint32.from_buffer(certificate, 76).value
        reconstructed = ctypes.c_uint32.from_buffer(certificate, 60).value
        reconstructed_bspline = ctypes.c_uint32.from_buffer(certificate, 64).value
        internal_knots = ctypes.c_uint32.from_buffer(certificate, 68).value
        max_boundary = ctypes.c_double.from_buffer(certificate, 120).value
        max_jump = ctypes.c_double.from_buffer(certificate, 128).value
        if any((failure_stage, reconstructed, reconstructed_bspline, internal_knots,
                max_boundary, max_jump)):
            verify_detail = str(failure_detail)
            if failure_stage == 2:
                edge_index = failure_detail & 0x0FFFFFFF
                flags = (
                    f"edge {edge_index}, changed={bool(failure_detail & 0x80000000)}, "
                    f"endpoints={bool(failure_detail & 0x40000000)}, "
                    f"semantic={bool(failure_detail & 0x20000000)}, "
                    f"geometry={bool(failure_detail & 0x10000000)}")
                verify_detail = flags
            diagnostics = (
                f"; verify={failure_stage}:{verify_detail}, rebuilt={reconstructed}/"
                f"{reconstructed_bspline}, knots={internal_knots}, "
                f"boundary={max_boundary:.3g}, normal_jump={max_jump:.3g}")
        if status >= 0 and certificate_status < 0:
            status = certificate_status
    kind = ("refused" if refusal_reason or status in
            (STATUS_POLICY_REFUSED, STATUS_SMOOTHNESS_CREASE) else "error")
    if kind == "refused":
        reason = _REFUSAL_DETAILS.get(refusal_reason, "native exact local move was refused")
        detail = f"native local move refused: {reason}{diagnostics}"
    return MoveResult(kind, status=status, refusal_reason=refusal_reason, detail=detail)


def _source_bytes(supplier):
    try:
        raw = supplier()
    except Exception as exc:
        return None, MoveResult("error", detail=f"BRep supplier failed: {exc}")
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        return None, MoveResult("error", detail="BRep supplier must return bytes-like data")
    try:
        data = bytes(raw)
    except (TypeError, ValueError) as exc:
        return None, MoveResult("error", detail=f"invalid BRep bytes: {exc}")
    if not data:
        return None, MoveResult("error", status=-1, detail="BRep bytes are empty")
    if len(data) > MAX_BUFFER_BYTES:
        return None, MoveResult("error", status=STATUS_LIMIT_EXCEEDED,
                                detail="BRep bytes exceed 512 MiB")
    return data, None


def move_component(source_identity, encoded_brep_supplier, kind, index, vector, live=False):
    """Move one native component without implementing or falling back to geometry in Python."""
    with _lock:
        binding = _load_locked()
        if binding is None:
            return MoveResult("unavailable", detail=_backend_detail)
        try:
            component_kind = int(kind)
            component_index = int(index)
            dx, dy, dz = float(vector[0]), float(vector[1]), float(vector[2])
        except (IndexError, TypeError, ValueError, OverflowError) as exc:
            return MoveResult("error", status=-1, detail=f"invalid move arguments: {exc}")

        key = id(source_identity)
        session = _sessions.get(key)
        if session is not None and session.source_identity is not source_identity:
            _sessions.pop(key)
            try:
                session.binding.release(session.handle)
            except Exception:
                pass
            session = None
        if session is None:
            data, error = _source_bytes(encoded_brep_supplier)
            if error is not None:
                return error
            if data is None:  # Defensive narrowing; _source_bytes always returns one side.
                return MoveResult("error", status=-6, detail="BRep supplier returned no result")
            source = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
            handle = ctypes.c_uint64()
            try:
                status = int(binding.import_session(source, len(data), ctypes.byref(handle)))
            except Exception as exc:
                return MoveResult("error", detail=f"native session import failed: {exc}")
            if status != STATUS_OK or not handle.value:
                return _outcome(status, detail="native session import refused or failed")
            session = _Session(source_identity, int(handle.value), binding)
            _sessions[key] = session
            while len(_sessions) > _MAX_SESSIONS:
                _old_key, old = _sessions.popitem(last=False)
                try:
                    old.binding.release(old.handle)
                except Exception:
                    pass
        else:
            _sessions.move_to_end(key)

        mode = VALIDATION_LIVE if live else VALIDATION_COMMIT
        certificate = _Certificate()
        arguments = (session.handle, mode, component_kind, component_index,
                     dx, dy, dz)
        try:
            required = int(binding.move_mode(
                *arguments, None, 0, ctypes.byref(certificate)))
        except Exception as exc:
            return MoveResult("error", detail=f"native move failed: {exc}")
        if required < 0:
            return _outcome(required, certificate, "native move refused or failed")
        if required <= 0 or required > MAX_BUFFER_BYTES:
            status = STATUS_LIMIT_EXCEEDED if required > MAX_BUFFER_BYTES else -6
            return MoveResult("error", status=status,
                              detail=f"invalid native output size: {required}")
        output = (ctypes.c_ubyte * required)()
        certificate = _Certificate()
        try:
            written = int(binding.move_mode(
                *arguments, output, required, ctypes.byref(certificate)))
        except Exception as exc:
            return MoveResult("error", detail=f"native output copy failed: {exc}")
        if written < 0:
            return _outcome(written, certificate, "native output copy refused or failed")
        if written != required:
            return MoveResult("error", status=-6,
                              detail=f"native output size changed from {required} to {written}")
        certificate_status = ctypes.c_int32.from_buffer(certificate, 8).value
        if certificate_status != STATUS_OK:
            return _outcome(certificate_status, certificate,
                            "native certificate reported failure")
        return MoveResult("ok", data=bytes(output), status=STATUS_OK)


def reset_sessions():
    """Release all cached native handles."""
    with _lock:
        _release_all_locked()


def _reset_for_tests(cdll_factory=None):
    global _signature, _binding_cache, _backend_detail, _cdll_factory
    with _lock:
        _release_all_locked()
        _signature = None
        _binding_cache = None
        _backend_detail = "not checked"
        _cdll_factory = ctypes.CDLL if cdll_factory is None else cdll_factory
