"""Explicit candidate loader for the read-only NWOBS01 native host boundary.

Python is necessary for extension loading, provenance admission and host lifetimes.
No model imports this yet. No automatic build, install, fallback or archive decoding.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import platform
import sys
import sysconfig
import threading
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import rhino3dm as r3
import rhino3dm._rhino3dm as host

_MODULE = '_narwhal_document_observer'
_SDIST = '8315ec0c177ad4c69e8cc4e6d331c584110e8e8cc9bad294f121f18b4b7ec81a'
# MEASURED: installed extension is byte-identical to the official8.35.0 CPython313
# manylinux x86_64 wheel (wheel SHA256783db09d...61229c7a4). The Windows entry is the
# cp313-win_amd64 host extension from the pinned 8.35.0 wheel. Other pairs must qualify.
_HOSTS = {
    'linux-x86_64': 'd5fed4447838a0645bfc82cf6ccc684e1c6ca6d4568533e044adf9754de169d4',
    'windows-x86_64': '1b59225812361ffd89e48eb1caf64a9f505aa1613da2058479b0b766fa976ce7',
}
_LIBRARY_SUFFIX = {'linux-x86_64': '.so', 'windows-x86_64': '.pyd'}
_LOCK = threading.RLock()
_loaded: tuple[str, ModuleType] | None = None


class ObservationUnavailable(RuntimeError):
    pass


def _sha(path: str | Path) -> str:
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ObservationUnavailable('duplicate qualification receipt field')
        value[key] = item
    return value


def _platform_tag() -> str:
    system = 'windows' if sys.platform == 'win32' else sys.platform
    machine = platform.machine().lower()
    return f"{system}-{'x86_64' if machine == 'amd64' else machine}"


def load_observer() -> Observer:
    """Load a qualified installed pair; never search scratch directories or build."""
    root = Path(__file__).resolve().parents[1]
    tag = _platform_tag()
    suffix = _LIBRARY_SUFFIX.get(tag)
    if suffix is None:
        raise ObservationUnavailable('qualified native document observer is not installed')
    for directory in (root / 'narwhal3d/native/document-observer',
                      root / 'artifacts/document-observer' / tag):
        library = directory / f'_narwhal_document_observer{suffix}'
        receipt = directory / 'build.json'
        if library.exists() or receipt.exists():
            return load_candidate(library, receipt)
    raise ObservationUnavailable('qualified native document observer is not installed')


def _lifetime_allowed(evidence, digest, tag):
    """Separate opt-in admission: ordinary observer receipts never enable cleanup.

    Windows retains conservative strong ownership until its own pinned-runtime
    lifetime qualification exists. A MinGW object probe is not that qualification.
    """
    if tag != 'linux-x86_64':
        return False
    proof = evidence.get('master_lifetime')
    if not isinstance(proof, dict):
        return False
    expected = {
        'control': 'GREEN', 'history': 'GREEN', 'observer': 'GREEN',
        'loader': 'GREEN', 'registry': 'GREEN', 'unload': 'GREEN',
        'capability': 'GREEN', 'mutation-retention': 'RED',
        'mutation-premature': 'RED', 'mutation-copy': 'RED', 'mutation-unload': 'RED',
    }
    root = Path(__file__).resolve().parents[1]
    try:
        return (proof.get('abi') == 1 and proof.get('platform') == tag
                and proof.get('library_sha256') == digest
                and proof.get('host_sha256') == _HOSTS[tag]
                and proof.get('source_sha256') == evidence.get('source_sha256')
                and proof.get('source_sha256') == _sha(root / 'native/document_observer/observer.cpp')
                and proof.get('loader_sha256') == _sha(__file__)
                and proof.get('checks') == expected)
    except OSError:
        return False


def load_candidate(library: str | Path, receipt: str | Path) -> Observer:
    """Admit one explicitly selected tested binary pair.

    A receipt is local build evidence, not a signature against a malicious user.
    Refuse before loading C++ on a different host binary, ABI or candidate hash.
    """
    global _loaded
    with _LOCK:
        library, receipt = Path(library).resolve(), Path(receipt).resolve()
        try:
            tag = _platform_tag()
            if tag not in _HOSTS:
                raise ObservationUnavailable('native document observer is not qualified for this platform')
            if receipt.stat().st_size > 4 * 1024 * 1024:
                raise ObservationUnavailable('qualification receipt too large')
            evidence = json.loads(receipt.read_text(), object_pairs_hook=_unique)
            runtime = evidence['host']
            host_path = host.__file__
            if not isinstance(host_path, str):
                raise ObservationUnavailable('rhino3dm extension path unavailable')
            if (evidence['upstream_sdist_sha256'] != _SDIST or getattr(r3, '__version__', None) != '8.35.0'
                    or runtime['version'] != getattr(r3, '__version__', None)
                    or runtime['cache_tag'] != sys.implementation.cache_tag
                    or runtime['soabi'] != sysconfig.get_config_var('SOABI')
                    or runtime['sha256'] != _HOSTS[tag] or _sha(host_path) != _HOSTS[tag]):
                raise ObservationUnavailable('unqualified rhino3dm binary/ABI pair')
            expected = _sha(library)
            controls = [entry for entry in evidence['entries'] if entry['label'] in ('control', 'restored')
                        and entry['status'] == 'GREEN' and entry['sha256'] == expected]
            if not controls:
                raise ObservationUnavailable('observer does not match a qualified control')
            if _loaded is not None:
                digest, module = _loaded
                if digest != expected:
                    raise ObservationUnavailable('different observer already loaded; use a fresh process')
                return Observer(module, lifetime=_lifetime_allowed(evidence, expected, tag))
            if _MODULE in sys.modules:
                raise ObservationUnavailable('observer was loaded outside the qualified boundary')
            spec = importlib.util.spec_from_file_location(_MODULE, library)
            if spec is None or spec.loader is None:
                raise ObservationUnavailable('observer extension cannot be loaded')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if not all(callable(getattr(module, name, None)) for name in ('observe', 'index', 'copy_attributes')):
                raise ObservationUnavailable('observer entry point missing')
            sys.modules[_MODULE] = module
            _loaded = (expected, module)
            return Observer(module, lifetime=_lifetime_allowed(evidence, expected, tag))
        except ObservationUnavailable:
            raise
        except (OSError, ValueError, KeyError, TypeError, ImportError) as error:
            raise ObservationUnavailable('valid qualified observer pair required') from error


class MasterLifetime:
    """Opaque host ownership glue; no geometry, timeline or eviction policy here."""

    def __init__(self, module):
        self.bind = module.lease_bind
        self.key = module.lease_key
        self.unbind = module.lease_unbind
        self.hold = module.lease_hold
        self.hold_key = module.lease_hold_key
        self.drain = module.lease_drain
        self.file_owner = module.file_owner
        self.stats = module.lease_stats


class Observer:
    def __init__(self, module: ModuleType, *, lifetime=False) -> None:
        self._observe: Callable[[r3.File3dm], bytes] = module.observe
        self._index: Callable[[r3.File3dm], bytes] = module.index
        self._copy_attributes = module.copy_attributes
        self._master_lifetime = None
        required = ('lease_bind', 'lease_key', 'lease_unbind', 'lease_hold',
                    'lease_hold_key', 'lease_drain', 'file_owner', 'lease_stats')
        if (lifetime and _platform_tag() == 'linux-x86_64'
                and all(callable(getattr(module, name, None)) for name in required)
                and callable(getattr(module, 'master_lifetime_abi', None))
                and module.master_lifetime_abi() == 1):
            self._master_lifetime = MasterLifetime(module)

    def master_lifetime(self):
        """None means retain strong masters and never activate native cleanup."""
        return self._master_lifetime

    def master_lifetime_status(self):
        return {'available': self._master_lifetime is not None,
                'platform': _platform_tag(),
                'reason': ('qualified pinned Linux wheel' if self._master_lifetime is not None
                           else 'NOT_QUALIFIED: conservative strong master ownership')}

    def index(self, file: r3.File3dm) -> bytes:
        if type(file) is not r3.File3dm:
            raise TypeError('observer requires an exact rhino3dm File3dm')
        data = self._index(file)
        if type(data) is not bytes or not 12 <= len(data) <= 128 * 1024 * 1024 or data[:8] != b'NWIDX01\0':
            raise ObservationUnavailable('malformed native index reply')
        return data

    def copy_attributes(self, file, object_id, holder):
        if type(file) is not r3.File3dm or type(holder) is not r3.File3dm or file is holder:
            raise TypeError('source document and separate attribute holder required')
        return self._copy_attributes(file, str(object_id), holder)

    def observe(self, file: r3.File3dm) -> bytes:
        if type(file) is not r3.File3dm:
            raise TypeError('observer requires an exact rhino3dm File3dm, not a conduit proxy')
        data = self._observe(file)
        if (type(data) is not bytes or not 12 <= len(data) <= 128 * 1024 * 1024
                or data[:8] != b'NWOBS01\0'):
            raise ObservationUnavailable('malformed native observation reply')
        # Structural/UUID/archive-field validation belongs to the Rust decoder.
        return data
