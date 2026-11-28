"""Temporary Python caller adapter to the Rust publication/audit CLI.

Only host representation conversion and subprocess transport live here. Rust owns
selection, traversal, parsing via the compiler C SDK, privacy and closure policy.
"""
import base64
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / 'scripts/source_tools.sh'


class AuditError(RuntimeError):
    pass


PublicBranchError = AuditError


def _encode(value):
    if isinstance(value, bytes):
        return {'$bytes': base64.b64encode(value).decode('ascii')}
    if isinstance(value, re.Pattern):
        return {'regex': _encode(value.pattern), 'flags': value.flags}
    if isinstance(value, (set, frozenset)):
        return {'$set': [_encode(x) for x in sorted(value)]}
    if isinstance(value, tuple):
        return {'$tuple': [_encode(x) for x in value]}
    if isinstance(value, list):
        return [_encode(x) for x in value]
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    return str(value) if isinstance(value, Path) else value


def _decode(value):
    if isinstance(value, list):
        return [_decode(x) for x in value]
    if isinstance(value, dict):
        if '$bytes' in value:
            return base64.b64decode(value['$bytes'])
        if '$set' in value:
            return set(map(_decode, value['$set']))
        if '$tuple' in value:
            return tuple(map(_decode, value['$tuple']))
        if '$regex' in value:
            return re.compile(_decode(value['$regex']), value['flags'])
        return {k: _decode(v) for k, v in value.items()}
    return value


def _rpc(op, _process_timeout=300, **arguments):
    request = {'op': op, 'root': str(ROOT), **arguments}
    if op != 'config':
        request['config'] = {k: globals()[k] for k in _CONFIG}
    command = [str(LAUNCHER), 'rpc']
    if sys.platform == 'win32':
        # Windows cannot execute the POSIX launcher. Cargo performs source-bound
        # native builds; this adapter still owns no probe/audit implementation.
        command = [str(Path.home() / '.cargo/bin/rustup.exe'), 'run', '1.95.0',
                   'cargo', 'run', '--manifest-path', str(ROOT / 'native/source_tools/Cargo.toml'),
                   '--target-dir', str(ROOT / 'native/source_tools/target/transport-host'),
                   '--bin', 'narwhal-source-tools', '--release', '--locked', '--offline',
                   '--target', 'x86_64-pc-windows-msvc',
                   '-j', '2', '--', 'rpc']
    result = subprocess.run(command, input=json.dumps(_encode(request)).encode(),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=_process_timeout, cwd=ROOT)
    if result.returncode:
        raise AuditError(result.stderr.decode('utf-8', 'replace').strip())
    reply = json.loads(result.stdout)
    if not reply['ok']:
        raise AuditError(reply['error'])
    return _decode(reply['value'])


_CONFIG = _rpc('config')
globals().update(_CONFIG)


def public_files():
    return _rpc('public_files')


def recursive_files(directory):
    return _rpc('recursive_files', path=directory)


def audit_entries(entries, require_manifest=True):
    # Wire entries are lists, not tuple-tagged host values.
    return _rpc('audit_entries', entries={k: list(v) for k, v in entries.items()},
                require_manifest=require_manifest)


def required_public_paths(entries):
    return _rpc('required_public_paths', entries={k: list(v) for k, v in entries.items()})


def retirement_path_problem(path):
    return _rpc('retirement_path_problem', path=path)


def path_problem(path):
    return _rpc('path_problem', path=path)


def runtime_archive_problem(blob):
    return _rpc('runtime_archive_problem', blob=blob)


def _scan(name, path, blob):
    try:
        return _rpc(name, path=path, blob=blob)
    except AuditError as error:
        raise ValueError(str(error)) from error


def legacy_scan_bytes(path, blob):
    return _scan('legacy_scan_bytes', path, blob)


def code_provenance_scan(path, blob):
    return _scan('code_provenance_scan', path, blob)


def frozen_dxf23_scan(path, blob):
    return _scan('frozen_dxf23_scan', path, blob)


def frozen_preview24_scan(path, blob):
    return _scan('frozen_preview24_scan', path, blob)


def frozen_selection_scan(path, blob):
    return _scan('frozen_selection_scan', path, blob)


def frozen_nearest_scan(path, blob):
    return _scan('frozen_nearest_scan', path, blob)


def frozen_json_scan(path, blob):
    return _scan('frozen_json_scan', path, blob)


def frozen_data_scan(path, blob):
    return _scan('frozen_data_scan', path, blob)


def require_root_commit(root, ref):
    return _rpc('require_root_commit', root=str(root), reference=ref)


def commit_root(paths, message, timestamp):
    return _rpc('commit_root', paths=list(paths), message=message, timestamp=timestamp)
