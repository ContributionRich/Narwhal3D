"""Disposable OCCT display jobs, never document geometry or a parallel geometry engine.

Python is required only for the existing OCP/Blender SDK handles and packed-array
interop. Rust's converter supervisor owns timeout, memory and process-tree teardown.
No thread touches a live master; the worker reads an exact BinTools serialization.
"""
from pathlib import Path
import json
import hashlib
import struct
import subprocess
import sys
import tempfile

# OURS: refusal budgets for interactive display preparation, not geometry tolerances.
TIMEOUT = 30.0
# Address-space cap (like the qualified document worker), not a resident-memory promise.
# OCP's shared libraries/reservations alone can exceed a 1.5 GiB virtual limit.
MEMORY_MIB = 4096
MAX_INPUT = 32 * 1024 * 1024
MAX_OUTPUT = 64 * 1024 * 1024
MAX_BATCH_OUTPUT = 128 * 1024 * 1024
HEADER = struct.Struct('=8sIII')
MAGIC = b'NWDISP01'


class DisplayPreparationError(RuntimeError):
    """Must propagate, never trigger another synchronous/faceted display path."""


class PreparedDisplay:
    def __init__(self, master, data, master_digest):
        self.master = master  # strong binding, not an id-based cache
        self.master_digest = master_digest
        self._check_master(master)
        self.data = data
        if not HEADER.size <= len(data) <= MAX_OUTPUT:
            raise DisplayPreparationError('display_result_size')
        magic, nv, nt, ne = HEADER.unpack_from(data)
        if magic != MAGIC or not nv or not nt or HEADER.size + nv*24 + nt*12 + ne*8 != len(data):
            raise DisplayPreparationError('display_result_invalid')
        start = HEADER.size
        self.vertices = memoryview(data)[start:start+nv*24].cast('d')
        start += nv*24
        self.triangles = memoryview(data)[start:start+nt*12].cast('I')
        self.edges = memoryview(data)[start+nt*12:].cast('I')

    def _check_master(self, master):
        from . import bridge
        if master is not self.master:
            raise DisplayPreparationError('display_master_changed')
        # OCP handles are mutable: identity alone cannot bind a display packet.
        # Compare against the exact input submitted before the worker launched.
        try:
            data = bridge._occt().shape_to_bytes(master, triangles=False)
        except Exception as exc:
            raise DisplayPreparationError('display_master_changed') from exc
        if hashlib.sha256(data).digest() != self.master_digest:
            raise DisplayPreparationError('display_master_changed')

    def bmesh(self, master):
        from . import bridge
        self._check_master(master)
        v, t, e = self.vertices, self.triangles, self.edges
        return bridge._tessellation_bmesh(zip(v[0::3], v[1::3], v[2::3]),
            zip(t[0::3], t[1::3], t[2::3]), zip(e[0::2], e[1::2]))


def _worker_command(root):
    # Same qualified bundled runtime selection as native document validation.
    from .narwhal_document import _worker_command as document_command
    args = list(document_command(root))
    script = str(Path(__file__).resolve())
    args[args.index(str(Path(__file__).with_name('document_worker.py').resolve()))] = script
    return args


class DisplayJob:
    def __init__(self, master, *, timeout=None, memory_mib=None):
        from . import bridge, converter_process
        self.master = master
        self.process = None
        self.folder = None
        self.streams = []
        # Fail closed on unsupported/unqualified runtimes; no in-process fallback.
        if sys.platform == 'darwin':
            raise DisplayPreparationError('display_worker_macos_containment_not_qualified')
        supervisor = converter_process._supervisor()
        if supervisor is None:
            raise DisplayPreparationError('display_supervisor_unavailable')
        try:
            self.folder = tempfile.TemporaryDirectory(prefix='narwhal-display-')
            root = Path(self.folder.name)
            data = bridge._occt().shape_to_bytes(master, triangles=False)
            if not 0 < len(data) <= MAX_INPUT:
                raise DisplayPreparationError('display_input_size')
            self.master_digest = hashlib.sha256(data).digest()
            (root/'input.brep').write_bytes(data)
            command = [supervisor, '--timeout-ms', str(max(1, round((TIMEOUT if timeout is None else timeout)*1000))),
                '--memory-mib', str(MEMORY_MIB if memory_mib is None else memory_mib),
                '--max-output', '4096', '--cancel-on-stdin-close']
            for key, value in sorted(converter_process._environment({'PYTHONDONTWRITEBYTECODE':'1'}).items()):
                command.extend(('--env', key+'='+value))
            command.extend(('--', *_worker_command(root)))
            self.streams = [open(root/'report.json', 'wb'), open(root/'supervisor.log', 'wb')]
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE,
                stdout=self.streams[0], stderr=self.streams[1])
        except BaseException:
            self.close()
            raise

    def poll(self):
        """None while running; a validated display packet on success; explicit failure otherwise."""
        if self.process.poll() is None:
            return None
        root = Path(self.folder.name)
        if self.process.returncode != 0:
            log = root/'supervisor.log'
            if log.stat().st_size <= 65536 and 'unknown supervisor argument: --cancel-on-stdin-close' in log.read_text():
                raise DisplayPreparationError('display_supervisor_upgrade_required')
            raise DisplayPreparationError('display_supervisor_failed')
        path = root/'report.json'
        if path.stat().st_size > 65536:
            raise DisplayPreparationError('display_report_size')
        report = json.loads(path.read_text())
        if report.get('timed_out'):
            raise DisplayPreparationError('display_timeout')
        if report.get('returncode') != 0 or report.get('error'):
            raise DisplayPreparationError('display_worker_failed: exit='+str(report.get('returncode'))+
                            ' '+str(report.get('error') or report.get('stderr') or report.get('stdout'))[-1024:])
        path = root/'display.bin'
        if not HEADER.size <= path.stat().st_size <= MAX_OUTPUT:
            raise DisplayPreparationError('display_result_size')
        return PreparedDisplay(self.master, path.read_bytes(), self.master_digest)

    def close(self):
        if self.process is not None:
            if self.process.stdin is not None:
                self.process.stdin.close()  # Rust kills/reaps the entire worker group/job.
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                # A broken supervisor must not make the UI wait indefinitely.
                self.process.kill()
                self.process.wait(timeout=1)
        for stream in self.streams:
            stream.close()
        self.streams.clear()
        if self.folder is not None:
            self.folder.cleanup()
            self.folder = None


def _worker(root):
    # Only OCP's Python bindings can receive these SDK handles today. Meshing and
    # welding reuse the production display implementation, with unchanged tolerances.
    from array import array
    from narwhal3d import bridge
    from narwhal3d.kernel import occt_backend as ob
    bridge._occt()
    ob.document_validation_threads()
    path = root/'input.brep'
    if not 0 < path.stat().st_size <= MAX_INPUT:
        raise DisplayPreparationError('display_input_size')
    shape = ob.shape_from_bytes(path.read_bytes())
    import os
    (root/'meshing').write_text(str(os.getpid()))
    verts, tris, edges = bridge.display_tessellation(shape)
    size = HEADER.size + len(verts)*24 + len(tris)*12 + len(edges)*8
    if not HEADER.size < size <= MAX_OUTPUT:
        raise DisplayPreparationError('display_result_size')
    with open(root/'display.bin', 'wb') as output:
        output.write(HEADER.pack(MAGIC, len(verts), len(tris), len(edges)))
        for kind, rows in (('d', verts), ('I', tris), ('I', edges)):
            array(kind, (value for row in rows for value in row)).tofile(output)


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    _worker(Path(sys.argv[sys.argv.index('--')+1]))
