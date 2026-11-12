"""Resolver and supervised process adapter for the built-in Narwhal document core."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from . import converter_process

PROTOCOL = "narwhal-document-core 1"
ENV_VAR = "NARWHAL_DOCUMENT_CORE"


class DocumentCoreError(RuntimeError):
    """The built-in container tool was absent, failed, or violated its protocol."""


def _name() -> str:
    return "narwhal-document-core.exe" if sys.platform == "win32" else "narwhal-document-core"


def _candidates() -> tuple[Path, ...]:
    explicit = os.environ.get(ENV_VAR)
    root = Path(__file__).resolve().parents[1]
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    platform_key = "windows-x86_64" if sys.platform == "win32" else "linux-x86_64"
    candidates.extend((
        Path(__file__).resolve().parent / "native" / _name(),
        root / "runtime" / platform_key / "bin" / _name(),
        root / "artifacts" / "document-core" / platform_key / _name(),
        root / "native" / "document_core" / "target" / "release" / _name(),
    ))
    return tuple(candidates)


_probe_cache: dict[tuple[str, int, str | None], bool] = {}


def executable(feature: str | None = None) -> Path | None:
    """Return the first executable that identifies the exact v1 protocol."""
    for path in _candidates():
        try:
            if not path.is_file() or not os.access(path, os.X_OK):
                continue
            stamp = path.stat().st_mtime_ns
        except OSError:
            continue
        key = (str(path), stamp, feature)
        valid = _probe_cache.get(key)
        if valid is None:
            result = converter_process.run(
                (str(path), "--version"), timeout=10, max_output=4096)
            valid = (result.returncode == 0 and not result.timed_out
                     and result.stdout.strip() == PROTOCOL)
            if valid and feature is not None:
                capabilities = converter_process.run(
                    (str(path), "--capabilities"), timeout=10, max_output=4096)
                valid = (capabilities.returncode == 0 and not capabilities.timed_out
                         and feature in capabilities.stdout.splitlines())
            _probe_cache[key] = valid
        if valid:
            return path
    return None


def require(feature: str | None = None) -> Path:
    path = executable(feature)
    if path is None:
        raise DocumentCoreError(
            "the built-in Narwhal document core is unavailable; reinstall the application "
            f"or set {ENV_VAR} to a compatible {PROTOCOL!r} executable"
            + (f" with {feature}" if feature else ""))
    return path


def _run(arguments: tuple[str, ...], timeout: float, feature: str | None = None) -> str:
    path = require(feature)
    result = converter_process.run(
        (str(path), *arguments), timeout=timeout, max_output=65536)
    if result.returncode != 0 or result.timed_out or result.error:
        detail = result.error or result.stderr.strip() or f"exit status {result.returncode}"
        raise DocumentCoreError(detail)
    return result.stdout


def pack(staging: str | os.PathLike, target: str | os.PathLike) -> None:
    """Atomically pack one validated staging directory into ``target``."""
    _run(("pack", os.fspath(staging), os.fspath(target)), timeout=300)


def unpack(source: str | os.PathLike, output: str | os.PathLike) -> None:
    """Validate and atomically create the previously absent output directory."""
    _run(("unpack", os.fspath(source), os.fspath(output)), timeout=300)


def inspect(source: str | os.PathLike) -> tuple[tuple[str, int, str], ...]:
    """Return every validated ``(sha256, size, member)`` row from a container."""
    path = require()
    try:
        result = subprocess.run(
            (str(path), "inspect", os.fspath(source)), check=False,
            capture_output=True, text=True, errors="replace", timeout=300,
            env=converter_process._environment(None))
    except (OSError, subprocess.SubprocessError) as exc:
        raise DocumentCoreError(f"could not inspect native document: {exc}") from exc
    if result.returncode != 0:
        raise DocumentCoreError(result.stderr.strip() or f"exit status {result.returncode}")
    text = result.stdout
    if len(text.encode("utf-8")) > 16 * 1024 * 1024:
        raise DocumentCoreError("document inventory exceeds 16 MiB")
    rows = []
    for line in text.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3 or len(parts[0]) != 64:
            raise DocumentCoreError("document core returned malformed inspect output")
        try:
            size = int(parts[1])
        except ValueError as exc:
            raise DocumentCoreError("document core returned a non-numeric member size") from exc
        if size < 0:
            raise DocumentCoreError("document core returned a negative member size")
        rows.append((parts[0], size, parts[2]))
    return tuple(rows)


def compare_display_proxies(left: Path, right: Path) -> bool:
    """Qualified Rust comparison of schema-1 display surfaces, not manufacturing geometry."""
    answer = _run(("compare-display-proxies", os.fspath(left), os.fspath(right)),
                  timeout=120, feature="display-proxy-equivalence-v1").strip()
    if answer not in ("equal", "different"):
        raise DocumentCoreError("invalid display-proxy comparison response")
    return answer == "equal"


class _FileJobs:
    """Opaque ABI adapter; Rust owns every queue transition and commit decision."""
    def __init__(self, kind):
        import ctypes as c
        from .native_document_session import load_library
        self.library = load_library()
        try:
            abi = getattr(self.library, f'narwhal_{kind}_jobs_abi')
            abi.argtypes, abi.restype = [], c.c_uint32
            if abi() != 1:
                raise DocumentCoreError(f'native {kind}-jobs ABI1 is required')
            self.call = getattr(self.library, f'narwhal_{kind}_jobs_json')
            self.call.argtypes = [c.POINTER(c.c_ubyte), c.c_size_t,
                                  c.POINTER(c.c_ubyte), c.c_size_t]
            self.call.restype = c.c_ssize_t
        except AttributeError as exc:
            raise DocumentCoreError(f'matching application-core {kind}-jobs ABI1 is required; rebuild the native core') from exc
        self.queue = None
        self.queue = self.request('create')['queue']

    def request(self, op, **arguments):
        import ctypes as c
        import json
        request = dict(arguments, op=op)
        if self.queue is not None:
            request['queue'] = self.queue
        data = json.dumps(request, allow_nan=False).encode()
        if len(data)>65536:
            raise DocumentCoreError('native load request exceeds 64KiB')
        source = (c.c_ubyte*len(data)).from_buffer_copy(data)
        output = (c.c_ubyte*4096)()
        size = self.call(source,len(data),output,len(output))
        if not 0<size<=len(output):
            raise DocumentCoreError('invalid native load reply size; request was not replayed')
        try:
            reply = json.loads(bytes(output[:size]))
        except ValueError as exc:
            raise DocumentCoreError('invalid native load reply; request was not replayed') from exc
        if not isinstance(reply,dict) or reply.get('status')!='ok':
            raise DocumentCoreError(str(reply.get('code','invalid native load reply')) if isinstance(reply,dict) else 'invalid native load reply')
        if set(reply)!={'status','action','queue','job','replaced'} or reply['queue']!=self.queue and op!='create':
            raise DocumentCoreError('unexpected native file-job reply; request was not replayed')
        if (type(reply['queue']) is not int or not 0<reply['queue']<1<<64
                or any(value is not None and (type(value) is not int or not 0<value<1<<64)
                       for value in (reply['job'],reply['replaced']))
                or reply['action'] not in ('created','queued','start','wait','hydrate','hydrated','commit',
                    'cancel','error','idle','completed','discard','cancelled','closed','declined','finished','busy','intended')):
            raise DocumentCoreError('invalid native file-job reply values; request was not replayed')
        return reply


class LoadJobs(_FileJobs):
    def __init__(self):
        super().__init__('load')


class SaveJobs(_FileJobs):
    def __init__(self):
        super().__init__('save')
