"""OCP/ctypes boundary only. Rust owns Cap worker time/memory/process containment.

Untrusted BinTools and recipe rebuilds NEVER run in the application process.
The parent decodes only freshly serialized output from a successful private worker
whose native admission checked all source/snapshot/result geometry and occurrences.
There is no hash-only provenance, env-variable bypass or in-process fallback.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

TIMEOUT = 30.0
MEMORY_MIB = 4096
MAX_INPUT = 32 * 1024 * 1024
MAX_OUTPUT = 64 * 1024 * 1024
# Set only by the canonical module's worker entry, never by request/environment.
_ACTIVE = False


def _command(root):
    from .narwhal_document import _worker_command
    args = list(_worker_command(root))
    old = str(Path(__file__).with_name('document_worker.py').resolve())
    args[args.index(old)] = str(Path(__file__).resolve())
    return args


def submit(mode, *, recipe=None, tolerance=1e-6, source=None, snapshot=None, target=None):
    """Transport exact inputs into a mandatory Rust-supervised private process."""
    from . import converter_process
    from .kernel import occt_backend as ob
    if sys.platform == 'darwin':
        raise ob.KernelError('cap_worker_macos_containment_not_qualified')
    supervisor = converter_process._supervisor()
    if supervisor is None:
        raise ob.KernelError('cap_supervisor_unavailable')
    # Pin the ABI/capability-qualified parent's actual library, not a child search
    # result or any path supplied by a document. No BinTools is decoded here.
    library = ob._cap_native_binding()
    env = converter_process._environment({'PYTHONDONTWRITEBYTECODE': '1',
                                         'NARWHAL_GEOMETRY_CORE': str(library._name)})
    with tempfile.TemporaryDirectory(prefix='narwhal-cap-') as tmp:
        root = Path(tmp)
        request = json.dumps({'mode': mode, 'recipe': recipe, 'tolerance': tolerance},
                             allow_nan=False).encode()
        total = len(request)
        for name, data in (('source', source), ('snapshot', snapshot), ('target', target)):
            if data is not None:
                total += len(data)
                if not 0 < len(data) <= MAX_INPUT or total > MAX_INPUT:
                    raise ob.KernelError('cap_input_size')
                (root/(name+'.brep')).write_bytes(data)
        if total > MAX_INPUT:
            raise ob.KernelError('cap_input_size')
        (root/'request.json').write_bytes(request)
        command = [supervisor, '--timeout-ms', str(round(TIMEOUT*1000)),
                   '--memory-mib', str(MEMORY_MIB), '--max-output', '4096']
        for key, value in sorted(env.items()):
            command.extend(('--env', key+'='+value))
        command.extend(('--', *_command(root)))
        # Invoke the pinned Rust executable directly: converter_process.run's
        # compatibility Python fallback must never be reachable for Cap.
        try:
            done = subprocess.run(command, capture_output=True, text=True,
                                  timeout=TIMEOUT+10, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ob.KernelError('cap_supervisor_timeout') from exc
        if done.returncode != 0:
            raise ob.KernelError('cap_supervisor_failed: '+done.stderr[-1024:])
        try:
            packet = json.loads(done.stdout)
            if not isinstance(packet, dict):
                raise ValueError('not a report object')
        except (ValueError, TypeError) as exc:
            raise ob.KernelError('cap_supervisor_report_invalid') from exc
        if packet.get('timed_out'):
            raise ob.KernelError('cap_timeout')
        if packet.get('returncode') != 0 or packet.get('error'):
            raise ob.KernelError('cap_worker_refused: '+str(packet.get('error') or packet.get('stderr') or packet.get('stdout'))[-2048:])
        report_path = root/'result.json'
        output_path = root/'result.brep'
        if not report_path.is_file() or report_path.stat().st_size > MAX_OUTPUT:
            raise ob.KernelError('cap_result_report_size')
        try:
            report = json.loads(report_path.read_bytes())
            if not isinstance(report, dict):
                raise ValueError('not a result object')
        except (ValueError, TypeError) as exc:
            raise ob.KernelError('cap_result_protocol') from exc
        if report.get('protocol') != 1 or report.get('mode') != mode:
            raise ob.KernelError('cap_result_protocol')
        if mode == 'verify':
            return None, None
        if not output_path.is_file() or not 0 < output_path.stat().st_size <= MAX_INPUT:
            raise ob.KernelError('cap_result_size')
        # This is not an arbitrary snapshot: the file was freshly emitted only
        # after successful native admission/verification in our private worker.
        return ob.shape_from_bytes(output_path.read_bytes()), report.get('recipe')


def _worker(root):
    global _ACTIVE
    _ACTIVE = True
    from .kernel import occt_backend as ob
    ob._need()
    ob.document_validation_threads()
    request_path = root/'request.json'
    if request_path.stat().st_size > MAX_INPUT:
        raise ob.KernelError('cap_input_size')
    request = json.loads(request_path.read_bytes())
    mode, recipe, tolerance = request['mode'], request['recipe'], request['tolerance']
    def read(name):
        path = root/(name+'.brep')
        if not path.exists():
            return None
        if not 0 < path.stat().st_size <= MAX_INPUT:
            raise ob.KernelError('cap_input_size')
        return path.read_bytes()
    report = {'protocol': 1, 'mode': mode}
    if mode == 'verify':
        ob._verify_cap_native_bytes(read('source'), read('snapshot'), read('target'), tolerance)
    elif mode in ('cap', 'create'):
        data = read('source')
        if data is None:
            if mode != 'create' or recipe is None:
                raise ob.KernelError('cap_missing_source')
            data = ob.shape_to_bytes(ob.from_recipe(recipe), triangles=False)
        output = ob._cap_native_bytes(data, tolerance)
        if mode == 'create':
            # Source decode here is safe only AFTER the identical bytes passed
            # native cap's occurrence/geometry budgets and exact validity checks.
            source = ob.shape_from_bytes(data)
            shape = ob.shape_from_bytes(output)
            base = recipe or ob.native_master_recipe(source)
            tree = {'kind': 'cap', 'version': 1, 'a': base, 'tolerance': tolerance,
                    'source': ob.native_master_recipe(source), 'result': ob.native_master_recipe(shape)}
            ob.verify_cap_recipe(ob.from_recipe(base), ob.native_master_bytes(tree['source']),
                                 ob.native_master_bytes(tree['result']), tolerance)
            report['recipe'] = tree
    elif mode == 'replay':
        if not isinstance(recipe, dict) or recipe.get('kind') != 'cap':
            raise ob.KernelError('cap_invalid_replay')
        output = ob.shape_to_bytes(ob.from_recipe(recipe), triangles=False)
    else:
        raise ob.KernelError('cap_invalid_operation')
    if mode != 'verify':
        if not 0 < len(output) <= MAX_INPUT:
            raise ob.KernelError('cap_result_size')
        (root/'result.brep').write_bytes(output)
    encoded = json.dumps(report, allow_nan=False).encode()
    if len(encoded) > MAX_OUTPUT:
        raise ob.KernelError('cap_result_report_size')
    (root/'result.json').write_bytes(encoded)


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    # Import canonical module explicitly so nested cap recipes stay within this
    # one already-contained worker, without an environment-controlled bypass.
    from narwhal3d import cap_worker
    try:
        cap_worker._worker(Path(sys.argv[sys.argv.index('--')+1]))
    except BaseException as exc:
        print(f'cap_worker_refused: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
        raise SystemExit(1)
