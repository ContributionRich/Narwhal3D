"""Host/process glue for the Rust library authority. No Python storage fallback."""
from __future__ import annotations
import functools
import json
import os
from pathlib import Path
import subprocess
import sys


class LibraryError(RuntimeError):
    pass


def binary():
    explicit = os.environ.get('NARWHAL_LIBRARY_CORE')
    if explicit:
        candidates = [Path(explicit).expanduser()]
    else:
        root = Path(__file__).resolve().parents[1]
        name = 'narwhal-library-core.exe' if sys.platform == 'win32' else 'narwhal-library-core'
        platform = 'windows-x86_64' if sys.platform == 'win32' else 'linux-x86_64'
        candidates = [root / 'runtime' / platform / 'bin' / name,
                      root / 'native/library_core/target/release' / name]
    for path in candidates:
        if path.is_file():
            return path
    raise LibraryError('Library engine is unavailable. Build it with python3 scripts/build_library_core.py.')


@functools.lru_cache(maxsize=8)
def _protocol(path, modified, size):
    try:
        version = subprocess.run([path, '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, timeout=10, check=True,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise LibraryError(f'Library engine qualification failed: {exc}') from exc
    if version != 'narwhal-library-core 1':
        raise LibraryError(f'Unsupported Library engine protocol: {version!r}')


def default_root():
    explicit = os.environ.get('NARWHAL_LIBRARY_ROOT')
    if explicit:
        return Path(explicit).expanduser().absolute()
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local')))
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library/Application Support'
    else:
        base = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share')))
    return base / 'Narwhal3D/library'


class LibraryStore:
    def __init__(self, root=None):
        self.root = str(Path(root).expanduser().absolute() if root else default_root())

    def _request(self, op, **kwargs):
        payload = json.dumps(dict(kwargs, op=op, root=self.root), allow_nan=False).encode()
        if len(payload) > 65536:
            raise LibraryError('Library request is too large')
        try:
            executable = binary()
            signature = executable.stat()
            _protocol(str(executable), signature.st_mtime_ns, signature.st_size)
            result = subprocess.run([str(executable)], input=payload, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=120,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            reply = json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            raise LibraryError(f'Library engine failed: {exc}') from exc
        if result.returncode or not isinstance(reply, dict) or reply.get('ok') is not True:
            raise LibraryError(reply.get('error', 'Library engine rejected the operation')
                               if isinstance(reply, dict) else 'Invalid library reply')
        return reply['result']

    def catalog(self, query='', category_id=None):
        return self._request('catalog', query=query, category_id=category_id)

    @functools.lru_cache(maxsize=64)
    def layout(self, width, height, scale=1.0, scroll=0.0, count=0):
        return self._request('layout', width=width, height=height, scale=scale, scroll=scroll, count=count)

    @functools.lru_cache(maxsize=64)
    def tooltip_layout(self, width, height, mouse_x, mouse_y, text_width, lines, scale=1.0):
        return self._request('tooltip_layout', width=width, height=height, mouse_x=mouse_x,
                             mouse_y=mouse_y, text_width=text_width, lines=lines, scale=scale)

    def create_category(self, name): return self._request('create_category', name=name)
    def rename_category(self, id, name): return self._request('rename_category', id=id, name=name)
    def delete_category(self, id): return self._request('delete_category', id=id)
    def rename_asset(self, id, name): return self._request('rename_asset', id=id, name=name)
    def move_asset(self, id, category_id): return self._request('move_asset', id=id, category_id=category_id)
    def remove_asset(self, id): return self._request('remove_asset', id=id)
    def refresh_thumbnail(self, id, thumbnail_path):
        return self._request('refresh_thumbnail', id=id, thumbnail_path=os.fspath(thumbnail_path))
    def add_asset(self, name, category_id, source_path, thumbnail_path=None, tags=None):
        return self._request('add_asset', name=name, category_id=category_id,
                             source_path=os.fspath(source_path),
                             thumbnail_path=os.fspath(thumbnail_path) if thumbnail_path else None,
                             tags=tags or [])
    def export_library(self, path, category_id=None):
        return self._request('export_library', path=os.fspath(path), category_id=category_id)
    def import_library(self, path, mode='merge'):
        return self._request('import_library', path=os.fspath(path), mode=mode)
