"""Package only a source-bound, runtime-qualified document observer pair.

No builds or native imports on the assembly host. The qualified runtime tests run
before this step; existing platform ELF/runtime gates remain mandatory afterward.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

_HOSTS = {
    'linux-x86_64': 'd5fed4447838a0645bfc82cf6ccc684e1c6ca6d4568533e044adf9754de169d4',
    'windows-x86_64': '1b59225812361ffd89e48eb1caf64a9f505aa1613da2058479b0b766fa976ce7',
}
_SDIST = '8315ec0c177ad4c69e8cc4e6d331c584110e8e8cc9bad294f121f18b4b7ec81a'
_LIBRARY = {'linux-x86_64': '_narwhal_document_observer.so', 'windows-x86_64': '_narwhal_document_observer.pyd'}
_CORE = {'linux-x86_64': 'libnarwhal_application_core.so', 'windows-x86_64': 'narwhal_application_core.dll'}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate(source_root, native_directory, platform, *, observer_directory=None, core_path=None):
    if platform not in _HOSTS:
        raise ValueError(f'document observer pair is not runtime-qualified for {platform}; no incomplete package may be assembled')
    directory = Path(observer_directory) if observer_directory is not None else Path(native_directory) / 'document-observer'
    library = directory / _LIBRARY[platform]
    receipt = directory / 'build.json'
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('qualified document-observer directory is required')
    for path in (library, receipt):
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'missing regular document observer input: {path.name}')
    if receipt.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('observer receipt exceeds bound')
    record = json.loads(receipt.read_text())
    actual = digest(library)
    if record.get('upstream_sdist_sha256') != _SDIST or record.get('host', {}).get('sha256') != _HOSTS[platform]:
        raise ValueError('observer dependency/host pair is not qualified')
    if not any(item.get('label') in ('control', 'restored') and item.get('status') == 'GREEN'
               and item.get('sha256') == actual for item in record.get('entries', [])):
        raise ValueError('observer binary does not match a qualified control')
    integration = record.get('integration', {})
    if any(integration.get(name, {}).get('status') != 'GREEN' for name in ('loader', 'registry')):
        raise ValueError('observer integration qualification missing')
    expected = (
        ('source_sha256', record, 'native/document_observer/observer.cpp'),
        ('adapter_sha256', integration, 'narwhal3d/native_object_registry.py'),
        ('loader_sha256', integration, 'narwhal3d/native_document_observer.py'),
    )
    for key, fields, relative in expected:
        if fields.get(key) != digest(Path(source_root) / relative):
            raise ValueError(f'observer qualified source mismatch: {relative}')
    core = Path(core_path) if core_path is not None else Path(native_directory) / _CORE[platform]
    if not core.is_file() or core.is_symlink() or integration.get('core_sha256') != digest(core):
        raise ValueError('observer/application-core pair differs from qualified inputs')
    return library, record


def stage(source_root, native_directory, app_root, platform, *, observer_directory=None, core_path=None):
    library, record = validate(source_root, native_directory, platform,
                               observer_directory=observer_directory, core_path=core_path)
    destination = Path(app_root) / 'narwhal3d/native/document-observer'
    if destination.exists():
        raise ValueError('observer package destination already exists')
    destination.mkdir(parents=True)
    shutil.copy2(library, destination / library.name)
    # Keep relocation-safe qualification fields; do not ship private scratch paths
    # or the full third-party source inventory as an application runtime dependency.
    compact = {key: record[key] for key in ('upstream_sdist_sha256', 'source_sha256', 'integration')}
    compact['host'] = {key: record['host'][key] for key in ('version', 'sha256', 'cache_tag', 'soabi')}
    compact['entries'] = [{key: item[key] for key in ('label', 'status', 'sha256')} for item in record['entries']]
    (destination / 'build.json').write_text(json.dumps(compact, sort_keys=True, indent=2) + '\n')
    return {'sha256': digest(library), 'qualification': 'paired runtime/source qualification; not a new release approval'}
