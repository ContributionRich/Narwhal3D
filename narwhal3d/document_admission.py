"""Process-local reuse of exact worker admission for unchanged serialized shape pairs.

These are worker-issued receipts, never file-supplied claims or a persistent cache.
Open always validates fully. Save still parses its staged metadata in the isolated
worker; only identical master bytes and exact proxy/recipe/summary validation inputs
can reuse earlier work.
Python bridges the openNURBS serializer and supervised worker; geometry stays in OCCT.
"""
from collections import OrderedDict
from contextlib import contextmanager
import hashlib
import importlib
import json
import os
from pathlib import Path
import tempfile
import sys

MAX_RECEIPTS = 256
MAX_HINT_BYTES = 32768
ENVIRONMENT = "NARWHAL_DOCUMENT_ADMISSION_HINTS"
OUTPUT = "admission.json"
_DIGEST_KEY = "Narwhal3D:NativeMasterSHA256"
_receipts = OrderedDict()


def _tokens(value):
    if not isinstance(value, list) or len(value) > MAX_RECEIPTS:
        return ()
    return tuple(token for token in value if isinstance(token, str) and len(token) == 64
                 and all(c in "0123456789abcdef" for c in token))


# Stat fields are cache guards, not part of the cross-process identity. In particular,
# ctime and inode catch normal replacement even when size and mtime are preserved.
_binary_hashes = OrderedDict()
_loaded_hashes = {}
MAX_BINARY_HASHES = 512


def _stamp(value):
    # Windows st_ctime is not a change time, and MEASURED (CPython 3.13, Windows 11)
    # os.stat() and os.fstat() of the same unchanged DLL disagree on it by ~67 ms, so
    # every packaged Save refused with "runtime binary changed". The real ChangeTime is
    # compared separately there (`_change_time`), so Windows leaves st_ctime out.
    ctime = None if sys.platform == "win32" else value.st_ctime_ns
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, ctime)


def _change_time(path):
    # Windows st_ctime is creation time, not POSIX metadata-change time. Query the
    # actual ChangeTime so an in-place same-size/mtime rewrite cannot hit the cache.
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    class BasicInfo(ctypes.Structure):
        _fields_ = [(name, ctypes.c_int64) for name in
                    ("creation", "access", "write", "change")] + [("attributes", wintypes.DWORD)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    info = kernel.GetFileInformationByHandleEx
    info.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    info.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(str(path), 0x80, 7, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "cannot inspect runtime binary " + str(path))
    try:
        value = BasicInfo()
        if not info(handle, 0, ctypes.byref(value), ctypes.sizeof(value)):
            raise OSError(ctypes.get_last_error(), "cannot inspect runtime change time")
        return value.change
    finally:
        close(handle)


def _binary_identity(path):
    path = Path(path).resolve(strict=True)
    before = path.stat()
    change_time = _change_time(path)
    cache_key = (str(path), _stamp(before), change_time)
    cached = _binary_hashes.get(cache_key)
    if cached is not None:
        _binary_hashes.move_to_end(cache_key)
        return (str(path), cached)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        if _stamp(os.fstat(stream.fileno())) != _stamp(before):
            raise RuntimeError("runtime binary changed before hashing: " + str(path))
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        if (_stamp(os.fstat(stream.fileno())) != _stamp(before)
                or _stamp(path.stat()) != _stamp(before)
                or _change_time(path) != change_time):
            raise RuntimeError("runtime binary changed while hashing: " + str(path))
    result = digest.hexdigest()
    _binary_hashes[cache_key] = result
    while len(_binary_hashes) > MAX_BINARY_HASHES:
        _binary_hashes.popitem(last=False)
    return (str(path), result)


def _loaded_identity(path):
    # Retain the first observed loaded image as well as current disk bytes. A parent
    # retaining an old mapping must not impersonate a fresh worker after replacement.
    current = _binary_identity(path)
    original = _loaded_hashes.setdefault(current[0], current[1])
    return (current[0], original, current[1])


def _loaded_library_paths():
    """OS loader inventory, not a search through PATH/PYTHONPATH or vendor folders."""
    import ctypes
    if sys.platform.startswith("linux"):
        paths = set()
        with open("/proc/self/maps", encoding="utf-8") as stream:
            for line in stream:
                fields = line.rstrip("\n").split(None, 5)
                if len(fields) == 6 and fields[5].startswith("/"):
                    path = fields[5]
                    if path.endswith(" (deleted)"):
                        # Only runtime images below are refused; unrelated deleted
                        # mappings (e.g. Blender scratch files) do not affect identity.
                        path = path[:-10]
                        if _runtime_image(path):
                            raise RuntimeError("loaded runtime library was removed: " + path)
                    if _runtime_image(path):
                        current = os.stat(path)
                        # MEASURED: maps and stat device numbers differ on this
                        # mounted filesystem for the same unchanged image. Compare
                        # inode here; the hash cache still guards stat device too.
                        if current.st_ino != int(fields[4]):
                            raise RuntimeError("loaded runtime library was replaced: " + path)
                    paths.add(path)
        return paths
    if sys.platform == "darwin":
        loader = ctypes.CDLL(None)
        count = loader._dyld_image_count
        count.restype = ctypes.c_uint32
        name = loader._dyld_get_image_name
        name.argtypes = [ctypes.c_uint32]
        name.restype = ctypes.c_char_p
        return {os.fsdecode(name(i)) for i in range(count()) if name(i)}
    if sys.platform == "win32":
        from ctypes import wintypes
        process = ctypes.WinDLL("kernel32", use_last_error=True)
        process.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        enum = psapi.EnumProcessModules
        enum.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE),
                         wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        enum.restype = wintypes.BOOL
        filename = psapi.GetModuleFileNameExW
        filename.argtypes = [wintypes.HANDLE, wintypes.HMODULE,
                             wintypes.LPWSTR, wintypes.DWORD]
        filename.restype = wintypes.DWORD
        handle = process.GetCurrentProcess()
        capacity = 256
        while True:
            modules = (wintypes.HMODULE * capacity)()
            needed = wintypes.DWORD()
            if not enum(handle, modules, ctypes.sizeof(modules), ctypes.byref(needed)):
                raise OSError(ctypes.get_last_error(), "cannot enumerate runtime libraries")
            if needed.value <= ctypes.sizeof(modules):
                break
            capacity = needed.value // ctypes.sizeof(wintypes.HMODULE) + 1
        paths = set()
        for module in modules[:needed.value // ctypes.sizeof(wintypes.HMODULE)]:
            buffer = ctypes.create_unicode_buffer(32768)
            size = filename(handle, module, buffer, len(buffer))
            if not size or size >= len(buffer):
                raise OSError(ctypes.get_last_error(), "cannot identify runtime library")
            paths.add(buffer.value)
        return paths
    raise RuntimeError("runtime library identity unsupported on " + sys.platform)


def _kernel_library(path):
    name = Path(path).name.lower()
    return name.startswith(("libtk", "tk", "libtbb", "tbb"))


def _runtime_image(path):
    name = Path(path).name.lower()
    return (_kernel_library(path) or name.startswith(
        ("_rhino3dm.", "ocp.", "libnarwhal_geometry_core.", "narwhal_geometry_core.")))


WORKER_IMAGES_ENV = "NARWHAL_DOCUMENT_RUNTIME_IMAGES"


def worker_runtime_images():
    """Host-loader witnesses for Blender's TBB images, not document-supplied paths.

    A standalone bundled Python lacks Blender's preloaded threading/allocator
    images. Loading the same images preserves the full existing runtime identity;
    removing them from the identity would silently weaken admission.
    """
    images = []
    seen = set()
    for path in sorted(_loaded_library_paths()):
        if Path(path).name.lower().startswith(('libtbb', 'tbb')):
            absolute = str(Path(path).resolve(strict=True))
            if absolute in seen:
                continue
            seen.add(absolute)
            _path, loaded, current = _loaded_identity(absolute)
            if loaded != current:
                raise RuntimeError('loaded threading runtime changed on disk')
            images.append([absolute,current])
    if len(images)>16:
        raise RuntimeError('threading runtime image inventory exceeds limit')
    return json.dumps(images,separators=(',',':'))


def runtime_identity():
    """Hash actual decoder/kernel/replay binaries for admission and Save summaries.

    The result is a deterministic, hashable tuple for equivalent parent/worker runtimes.
    Python is only OS-loader/serializer orchestration here; no geometry computation.
    Missing required binaries or an unreadable loader inventory fail closed. This is
    replacement detection, not attestation against a malicious host or memory patching.
    """
    import rhino3dm
    from . import native_geometry
    from .kernel import occt_backend
    decoder = importlib.import_module("rhino3dm._rhino3dm")
    kernel_version, binding_path = occt_backend.document_kernel_binding()
    # Use precisely the ABI/capability-aware resolver used by recipe replay, including
    # its cached loaded binding. Candidate witnesses also cover newly installed defaults
    # when the parent has cached unavailability and a new worker can now load them.
    with native_geometry._lock:
        selected = native_geometry._load_locked()
        geometry = _loaded_identity(selected.path) if selected is not None else None
        candidates = []
        for path in native_geometry._candidates():
            if path.is_file():
                candidates.append(_binary_identity(path))
            else:
                candidates.append((str(path.resolve()), None))
    libraries = tuple(sorted({_loaded_identity(path) for path in _loaded_library_paths()
                              if _kernel_library(path)}))
    return ("document-runtime-v2", str(rhino3dm.__version__),
            _loaded_identity(decoder.__file__), kernel_version,
            _loaded_identity(binding_path), libraries,
            _binary_identity(__file__), _binary_identity(native_geometry.__file__),
            geometry, tuple(candidates))


def contract():
    """Invalidate receipts on decoder, serializer, kernel or native-helper changes."""
    from . import native_document
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256(b"narwhal-exact-admission-v2\0")
    for name in ("document_admission.py", "document_worker.py", "document_compat.py",
                 "document_proxy.py", "narwhal_document.py", "bridge.py",
                 "kernel/__init__.py", "kernel/occt_backend.py", "native_document.py"):
        digest.update((root / name).read_bytes())
    digest.update(repr(runtime_identity()).encode())
    executable = native_document.executable("display-proxy-equivalence-v1")
    digest.update(repr(_binary_identity(executable) if executable is not None else None).encode())
    return digest.hexdigest()


def key(proxy, record, tolerance, binary_format, decoder, *, native_digest=None):
    """Bind every input the exact reader checks, without editing the candidate.

    openNURBS user-data serialization carries a modification counter, so Encode() is
    not a stable geometry witness. Use the same exact NURBS faces/edges/loops that the
    validator reads, plus raw bounds, validity, closure and all user strings. Only the
    process-local kernel handle is omitted. No surface or curve is sampled.
    """
    from . import bridge, kernel
    if not hasattr(proxy, "Faces"):
        return None
    strings = [tuple(pair) for pair in proxy.GetUserStrings()]
    handles = [pair for pair in strings if pair[0].casefold() == kernel.MASTER_KEY.casefold()]
    if len(handles) != 1 or handles[0][0] != kernel.MASTER_KEY:
        return None  # Ambiguous metadata is never normalized for reuse.
    strings = [pair for pair in strings if pair[0] != kernel.MASTER_KEY]
    if native_digest is not None:
        digests = [pair for pair in strings if pair[0].casefold() == _DIGEST_KEY.casefold()]
        if len(digests) != 1 or digests[0][0] != _DIGEST_KEY:
            return None
        strings = [(name, native_digest if name == _DIGEST_KEY else value)
                   for name, value in strings]
    box = proxy.GetBoundingBox()
    encoded = (type(proxy).__name__, bridge.brep_data(proxy), strings,
               bool(proxy.IsValid), bool(proxy.IsSolid),
               (box.Min.X, box.Min.Y, box.Min.Z, box.Max.X, box.Max.Y, box.Max.Z))
    value = (decoder, binary_format, tolerance, record, encoded)
    try:
        packed = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError):
        return None  # Invalid summaries still go through the full reader's refusal path.
    return hashlib.sha256(packed.encode()).hexdigest()


def canonical_key(proxy, record, canonical, tolerance, binary_format, decoder):
    """Also recognize the exact bytes that a successful Open publishes for next Save."""
    digest = hashlib.sha256(canonical).hexdigest()
    return key(proxy, dict(record, sha256=digest), tolerance, binary_format, decoder,
               native_digest=digest)


def read_hints():
    path = os.environ.get(ENVIRONMENT)
    if not path:
        return frozenset()
    candidate = Path(path)
    try:
        if candidate.is_symlink() or candidate.stat().st_size > MAX_HINT_BYTES:
            return frozenset()
        return frozenset(_tokens(json.loads(candidate.read_text())))
    except (OSError, ValueError, TypeError):
        return frozenset()  # No usable receipt means full validation, never admission.


@contextmanager
def hints(reuse):
    if not reuse or not _receipts:
        yield {ENVIRONMENT: ""}
        return
    with tempfile.TemporaryDirectory(prefix="narwhal-admission-") as folder:
        path = Path(folder) / "receipts.json"
        path.write_text(json.dumps(list(_receipts)))
        path.chmod(0o600)
        yield {ENVIRONMENT: str(path)}


def publish(output, receipts):
    tokens = list(dict.fromkeys(token for token in receipts if token))[:MAX_RECEIPTS]
    (output / OUTPUT).write_text(json.dumps(tokens))


def remember(output):
    """Called only after the supervised worker has accepted the COMPLETE candidate."""
    path = output / OUTPUT
    try:
        if path.is_symlink() or path.stat().st_size > MAX_HINT_BYTES:
            return
        tokens = _tokens(json.loads(path.read_text()))
    except (OSError, ValueError, TypeError):
        return
    for token in tokens:
        _receipts[token] = None
        _receipts.move_to_end(token)
        while len(_receipts) > MAX_RECEIPTS:
            _receipts.popitem(last=False)
