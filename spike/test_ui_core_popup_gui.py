"""Run Main's unchanged selection visibility body with an explicit popup backend.

NARWHAL_UI_CORE_LIBRARY=... python3 -B spike/test_ui_core_popup_gui.py --launch
The retired --legacy mode is refused. One owned GUI, both
shared locks, 2 GiB/no swap, >=4 GiB desktop reserve, private disk-backed output.
The wrapper qualifies backend/lifetime/real BLF identity; Main's original test
qualifies actual events, row/geometry hover pixels and release shielding.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def launch(script=None):
    import fcntl
    import subprocess
    if '--legacy' in sys.argv:
        raise ValueError('--legacy was retired; the required native core has no fallback')
    script = Path(script or __file__).resolve()
    out = Path(tempfile.mkdtemp(prefix='pg-', dir='/var/tmp/nwp'))
    library = Path(os.environ['NARWHAL_UI_CORE_LIBRARY']).resolve(strict=True)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('NARWHAL', 'RHI' + 'NO', 'BLENDER_')) and k not in ('PYTHONPATH', 'LD_LIBRARY_PATH')}
    paths = subprocess.check_output([str(ROOT / 'narwhal3d.sh')], cwd=ROOT,
        env=dict(env, NARWHAL3D_LAUNCHER_TEST='native-paths'), text=True, timeout=10)
    for row in paths.splitlines():
        key, value = row.split('=', 1)
        if value:
            env[key] = value
    for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
        folder = out / key.lower()
        folder.mkdir()
        env[key] = str(folder)
    env.update(TMPDIR=str(out), PYTHONDONTWRITEBYTECODE='1',
        PYTHONPATH=str(ROOT / 'vendor/occt'),
        LD_LIBRARY_PATH=str(ROOT / 'tools/blender-patched/5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs'),
        NARWHAL3D_SETTINGS_FILE=str(out / 'preferences.json'), NARWHAL3D_DEFER_FIRST_PRESENT='1',
        NARWHAL3D_NO_DIALOGS='1', NARWHAL_UI_CORE_LIBRARY=str(library),
        OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
        VISIBILITY_07_OUTPUT=str(out), VISIBILITY_07_FIRST='object',
        VISIBILITY_07_MUTATION='', VISIBILITY_07_DIAGNOSTIC_SCROLL='0')
    command = ['systemd-run', '--user', '--scope', '--quiet', '-p', 'MemoryMax=2G',
        '-p', 'MemorySwapMax=0', 'timeout', '--signal=TERM', '--kill-after=5s', '140s',
        'bwrap', '--ro-bind', '/', '/', '--dev-bind', '/dev', '/dev', '--proc', '/proc',
        '--bind', str(out), str(out), '--unshare-net', '--die-with-parent',
        str(ROOT / 'tools/blender-patched/blender'), '--threads', '1', '--factory-startup',
        '--window-geometry', '0', '0', '1600', '1000', '--enable-event-simulate',
        '--python-exit-code', '1', '--python', str(script),
        '--python', str(ROOT / 'launch/bootstrap.py')]
    sources = [ROOT / p for p in ('spike/test_selection_visibility_07.py',
        'narwhal3d/shell_popup.py', 'narwhal3d/native_popup.py', 'narwhal3d/ui.py',
        'narwhal3d/selection_menu.py', 'narwhal3d/native_textfield.py')]
    sources.extend((library, script))
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    locks = []
    print('POPUP_GUI_EVIDENCE', out, flush=True)
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                for name in ('/tmp/narwhal3d-heavy-build.lock', '/tmp/narwhal3d_gui.lock'):
                    lock = open(name, 'a')
                    locks.append(lock)
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                for lock in locks:
                    lock.close()
                locks.clear()
                if time.monotonic() >= deadline:
                    raise TimeoutError('GUI/heavy lock busy; nothing launched')
                time.sleep(.25)
        available = int(next(row.split()[1] for row in Path('/proc/meminfo').read_text().splitlines()
                             if row.startswith('MemAvailable:')))
        if available < 6 * 1024 * 1024:
            raise RuntimeError('Need 4 GiB reserve + 2 GiB GUI; nothing launched')
        (out / 'launch.json').write_text(json.dumps(dict(command=command, source_sha256=before,
            mem_available_kib=available, native_paths=paths, backend='native'), indent=2))
        with (out / 'run.log').open('w') as log:
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=155)
        text = (out / 'run.log').read_text(errors='replace')
        print(text[-16000:], flush=True)
        after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
        (out / 'source_check.json').write_text(json.dumps(dict(before=before, after=after,
                                                            unchanged=before == after), indent=2))
        report = json.loads((out / 'result.json').read_text()) if (out / 'result.json').exists() else {}
        return result.returncode or int(report.get('status') != 'passed' or before != after or
            'Traceback (most recent call last)' in text or 'Error in draw callback' in text)
    finally:
        for lock in locks:
            lock.close()


def run():
    import ctypes as C
    import runpy
    import traceback

    # Main's script sees a genuinely fresh process, then installs its observers/timer.
    main = runpy.run_path(str(ROOT / 'spike/test_selection_visibility_07.py'))
    try:
        from narwhal3d import native_popup, native_textfield, shell_popup, ui
        lib = native_popup.library()
        main['check'](lib.narwhal_editor_abi() == 1 and C.sizeof(native_textfield.State) == 88,
                      'popup addition preserves editor ABI 1 / State88')
        observed = shell_popup.open

        def open_popup(*args, **kwargs):
            model = observed(*args, **kwargs)
            main['check'](isinstance(model, native_popup.NativePopup),
                          'runtime backend is persistent native')
            main['check'](model.measure is ui._text_width, 'runtime whole-string BLF measurer retained')
            record = dict(backend=model._backend_reason, handle=getattr(model, 'handle', None),
                          events=0, title=args[2])
            main['RESULT'].setdefault('popup_backends', []).append(record)
            original_event = model.handle_event

            def persistent_event(event):
                before = getattr(model, 'handle', None)
                result = original_event(event)
                record['events'] += 1
                assert model.handle == before == record['handle'], 'native popup owner changed during event'
                record.update(uploaded_rows=model.uploaded_rows, downloaded_rows=model.downloaded_rows,
                              mutation_reply_bytes=model.mutation_reply_bytes)
                return result
            model.handle_event = persistent_event
            return model
        shell_popup.open = open_popup
    except BaseException:
        main['finish'](traceback.format_exc())


if __name__ == '__main__':
    if '--launch' in sys.argv:
        raise SystemExit(launch())
    run()
