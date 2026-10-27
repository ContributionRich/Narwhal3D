#!/usr/bin/env python3
"""Light Windows build-driver fixtures: no VM, network, Blender or native build.

Python is required to exercise the Python orchestration embedded in PowerShell.
PowerShell wiring is checked structurally; this is NOT a PowerShell/MSVC execution test.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
NAMES = (
    'apply_narwhal3d_branding.py', 'apply_app_drawn.py', 'apply_cad_boundary_wires.py',
    'apply_deferred_first_present.py', 'apply_statusbar_height.py',
    'narwhal3d_app_icon.svg', 'apply_window_overlay.py',
)


def embedded(script, variable):
    match = re.search(r"^@'\n(.*?)\n'@ \| Set-Content -LiteralPath \$" + variable + r'\b',
                      script, re.M | re.S)
    # Start at the nearest here-string, not an earlier block in the script.
    assert match, variable
    return match[1].rsplit("\n@'\n", 1)[-1]


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def execute(code, argv=(), modules=None):
    namespace = {'__name__': '__main__'}
    with patch.object(sys, 'argv', ['fixture.py', *map(str, argv)]), \
            patch.dict(sys.modules, modules or {}), redirect_stdout(io.StringIO()):
        exec(compile(code, 'embedded_windows_driver.py', 'exec'), namespace)
    return namespace


def disk_allows(script, free_bytes, mode):
    """Interpret only the explicit PowerShell disk policy; never run the builder."""
    policy = re.search(r'\$MinimumFreeGiB = if \(\$(\w+)\) \{ (\d+) \} else \{ (\d+) \}', script)
    assert policy, 'explicit mode-specific disk reservation is missing'
    guard = re.search(r'if \(\$SystemDrive\.FreeSpace -(lt|le) \(\$MinimumFreeGiB \* 1GB\)\) \{ throw ', script)
    assert guard, 'disk guard must compare unrounded bytes and throw on insufficient space'
    flag, when_true, when_false = policy.groups()
    flags = {'ResumePackaging': mode == 'resume', 'RebuildRuntime': mode == 'rebuild'}
    required = int(when_true if flags[flag] else when_false) * 1024**3
    return free_bytes >= required if guard[1] == 'lt' else free_bytes > required


def run_checks(check):
    script = (ROOT / 'scripts/build_windows_preview.ps1').read_text()
    linux = (ROOT / 'scripts/build_blender_linux.sh').read_text()
    names = re.search(r'\$NativeInputNames = @\((.*?)\n\)', script, re.S)[1]
    check(tuple(re.findall(r'"([^"]+)"', names)) == NAMES,
          'Windows native provenance uses exactly the seven current inputs')
    for name in NAMES:
        check('blender_patches/' + name in linux, 'native input also attested by Linux: ' + name)
    for token in (
        'if ($ResumePackaging -and $RebuildRuntime)',
        'if ($SourceOverlay -and -not $RebuildRuntime)',
        'if (-not $RebuildRuntime -and (Test-Path -LiteralPath $BuildDirectory))',
        "CMAKE_HOME_DIRECTORY", "CMAKE_GENERATOR", "CMAKE_BUILD_TYPE",
        "CMAKE_C_COMPILER", "CMAKE_CXX_COMPILER", "CMAKE_VS_WINDOWS_TARGET_PLATFORM_VERSION",
        'Incremental Windows SDK mismatch', 'Assert-NoReparseAncestor $Workspace',
        '[IO.File]::ReadAllText($WorkspaceMarker).Trim()',
        '[IO.FileShare]::None', 'Refusing Git reset outside the marked workspace checkout',
        "-version '[17.0,18.0)'", '-vcvars_ver=$MsvcToolsetVersion',
        'call "$Vcvars64" $WindowsSdkVersion -vcvars_ver=$MsvcToolsetVersion',
        '"$Ninja" -C "$BuildDirectory" -j $Jobs install',
        "'require current compiled runtime before resuming packaging'",
        "'verify packaged runtime matches compiled input provenance'",
        "'verify packaged native UI APIs'",
    ):
        check(token in script, 'PowerShell safety/integration guard: ' + token)
    check(script.index('Incremental CMake source/generator/configuration mismatch') <
          script.index('"reset script-owned Narwhal clone"'),
          'incremental cache validation precedes any app clone reset')
    check(script.index('Remove-Item -LiteralPath $RuntimeMarker') <
          script.index('"apply Narwhal native patches"'),
          'failed patch/build invalidates the old compiled attestation')
    check('skipping source updates, patches, configuration, and compilation' in script,
          'ResumePackaging retains documented native-skip semantics')

    gib = 1024**3
    disk_cases = [(mode, free, allowed) for mode in ('clean', 'rebuild', 'resume')
                  for free, allowed in ((16 * gib - 1, False),
                                        (16 * gib, mode == 'resume'),
                                        (49 * gib, mode == 'resume'),
                                        (60 * gib - 1, mode == 'resume'),
                                        (60 * gib, True))]
    for mode, free, allowed in disk_cases:
        check(disk_allows(script, free, mode) == allowed,
              f'disk policy: {mode} at {free} bytes accepted={allowed}')
    for label, broken in (
        ('wrong mode', script.replace('$MinimumFreeGiB = if ($ResumePackaging)',
                                      '$MinimumFreeGiB = if ($RebuildRuntime)')),
        ('insufficient packaging space accepted', script.replace('{ 16 } else { 60 }', '{ 1 } else { 60 }')),
        ('compile reservation weakened', script.replace('{ 16 } else { 60 }', '{ 16 } else { 16 }')),
        ('disk guard removed', script.replace('if ($SystemDrive.FreeSpace -lt ($MinimumFreeGiB * 1GB))', 'if ($false)')),
    ):
        try:
            assert all(disk_allows(broken, free, mode) == allowed for mode, free, allowed in disk_cases)
        except AssertionError:
            check(True, 'MUTATION RED: ' + label)
        else:
            check(False, 'MUTATION did not fail: ' + label)

    codes = {name: embedded(script, name) for name in (
        'PatchDriver', 'ProvenanceDriver', 'RuntimeApiProbe', 'OverlayDriver', 'RuntimeBuildRunner',
        'CacheCheckDriver')}
    for name, code in codes.items():
        ast.parse(code)
        check(True, 'embedded Python parses: ' + name)

    cache_namespace = {'__name__': 'fixture'}
    exec(codes['CacheCheckDriver'], cache_namespace)
    validate = cache_namespace['validate_cache']
    source_path = r'C:\Narwhal3D-build\blender-source'
    compiler = 'C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Tools/MSVC/14.44.35207/bin/Hostx64/x64/cl.exe'
    sdk = '10.0.26100.0'
    cache_text = ('CMAKE_HOME_DIRECTORY:INTERNAL=C:/Narwhal3D-build/blender-source\n'
                  'CMAKE_GENERATOR:INTERNAL=Ninja\nCMAKE_BUILD_TYPE:STRING=Release\n'
                  f'CMAKE_C_COMPILER:FILEPATH={compiler}\nCMAKE_CXX_COMPILER:FILEPATH={compiler}\n'
                  f'CMAKE_VS_WINDOWS_TARGET_PLATFORM_VERSION:STRING={sdk}\n')
    check(validate(cache_text, source_path, '14.44', '', lambda p: p == compiler) == {'windows_sdk': sdk},
          'VS2022/MSVC14.44 fixture preserves cached SDK and accepts equivalent Windows path separators')
    for label, fixture, requested_sdk, exists in (
        ('wrong source', cache_text.replace('C:/Narwhal3D-build', 'C:/unowned'), sdk, True),
        ('wrong generator', cache_text.replace('Ninja', 'Visual Studio 17 2022'), sdk, True),
        ('wrong config', cache_text.replace('Release', 'Debug'), sdk, True),
        ('wrong compiler', cache_text.replace('14.44.35207', '14.43.1'), sdk, True),
        ('missing compiler', cache_text, sdk, False),
        ('wrong SDK', cache_text, '10.0.22621.0', True),
    ):
        try:
            validate(fixture, source_path, '14.44', requested_sdk, lambda p: exists)
        except SystemExit:
            check(True, 'MUTATION RED: retained cache rejects ' + label)
        else:
            check(False, 'MUTATION RED: retained cache rejects ' + label)

    with tempfile.TemporaryDirectory(prefix='windows_build_fixture_') as temporary:
        root = Path(temporary)
        source = root / 'blender-source'
        source.mkdir()
        patches = root / 'patches'
        patches.mkdir()
        calls = root / 'calls.jsonl'
        prelude = ('from pathlib import Path\nimport json\n'
                   f'LOG = Path({str(calls)!r})\n'
                   'def log(*args):\n'
                   '    with LOG.open("a") as out: out.write(json.dumps(args) + "\\n")\n')
        for name in NAMES:
            if name.endswith('.svg'):
                (patches / name).write_text('<svg/>')
                continue
            if name == 'apply_narwhal3d_branding.py':
                body = 'def apply(source, icon):\n    log("branding", source, icon)\n    return []\n'
            elif name in ('apply_statusbar_height.py', 'apply_window_overlay.py'):
                body = (f'def apply(source):\n    log({name!r}, str(source))\n    return []\n'
                        'def main():\n    raise AssertionError("argparse main must not be called")\n')
            else:
                body = f'def main():\n    log({name!r}, SRC)\n'
            (patches / name).write_text(prelude + body)
        execute(codes['PatchDriver'], (source, patches))
        expected = [['branding', str(source), str(patches / NAMES[5])]] + [
            [name, str(source / 'source/blender')] for name in (NAMES[1], NAMES[2], NAMES[3], NAMES[4], NAMES[6])]
        check([json.loads(line) for line in calls.read_text().splitlines()] == expected,
              'driver applies all six patches in order with correct checkout/source-blender roots')
        # Run the original missing-deferred-patch bug against the same assertion.
        calls.unlink()
        broken = codes['PatchDriver'].replace(', "apply_deferred_first_present.py"', '')
        execute(broken, (source, patches))
        check([json.loads(line) for line in calls.read_text().splitlines()] != expected,
              'MUTATION RED: dropping deferred presentation fails the patch-call contract')

        # Real current patch metadata, synthetic patched source. These fixtures test
        # provenance orchestration, not native patch correctness or compilation.
        contents = defaultdict(list)
        for name in NAMES:
            real = ROOT / 'blender_patches' / name
            (patches / name).write_bytes(real.read_bytes())
            if name.endswith('.py') and name != NAMES[0]:
                module = load(real)
                for relative, _old, new in module.EDITS:
                    contents[relative].append(new)
                if name == NAMES[-1]:
                    contents[module.HEADER].append(module.HEADER_TEXT)
        for relative, chunks in contents.items():
            path = source / 'source/blender' / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(''.join(chunks))
        runtime = root / 'runtime'
        runtime.mkdir()
        namespace = {'__name__': 'fixture'}
        exec(codes['ProvenanceDriver'], namespace)
        binary = runtime / 'blender.exe'
        binary.write_bytes(b'MZ' + b'\0'.join(namespace['NATIVE_MARKERS']))
        args = (source, patches, runtime, 'd13f752e3b9c4f8c261cda552b1021f8bcc0382c', *NAMES)

        def refused(mode, label):
            try:
                execute(codes['ProvenanceDriver'], (mode, *args))
            except SystemExit:
                check(True, label)
            else:
                check(False, label)

        refused('check', 'ResumePackaging refuses missing compiled provenance')
        execute(codes['ProvenanceDriver'], ('record', *args))
        execute(codes['ProvenanceDriver'], ('check', *args))
        check(True, 'record then resume accepts matching binary and seven current inputs')
        for name in NAMES:
            path = patches / name
            original = path.read_bytes()
            path.write_bytes(original + b'\n')
            refused('check', 'MUTATION RED: stale compiled input rejected: ' + name)
            path.write_bytes(original)
        original = binary.read_bytes()
        binary.write_bytes(original + b'new binary')
        refused('check', 'MUTATION RED: different compiled binary rejected')
        binary.write_bytes(original)
        header = source / 'source/blender/python/BPY_window_draw.hh'
        text = header.read_text()
        header.write_text(text + '// stale')
        refused('record', 'MUTATION RED: stale overlay header cannot be attested')
        header.write_text(text)
        execute(codes['ProvenanceDriver'], ('check', *args))
        check(True, 'restored current inputs return GREEN')

        overlay = root / 'export'
        overlay.mkdir()
        (overlay / 'new-source.py').write_text('current source')
        target = root / 'app'
        (target / 'native/target').mkdir(parents=True)
        cache = target / 'native/target/cached.obj'
        cache.write_bytes(b'keep')
        execute(codes['OverlayDriver'], (overlay, target))
        check((target / 'new-source.py').read_text() == 'current source' and cache.read_bytes() == b'keep',
              'public source overlay merges without deleting native build caches')
        (overlay / '.git').mkdir()
        try:
            execute(codes['OverlayDriver'], (overlay, target))
        except SystemExit:
            check(True, 'overlay rejects Git metadata')
        else:
            check(False, 'overlay rejects Git metadata')

    # Deliberately fake bpy: only tests fail-closed API validation, not real drawing.
    handles = []
    def add(*args):
        handles.append(args)
        return object()
    def remove(*args):
        pass
    space = SimpleNamespace(draw_handler_add=add, draw_handler_remove=remove)
    window = SimpleNamespace(draw_handler_add=add, draw_handler_remove=remove)
    system = SimpleNamespace(ui_scale=1.25)
    types = SimpleNamespace(
        Collection=SimpleNamespace(bl_rna=SimpleNamespace(properties={'narwhal_app_drawn'})),
        Mesh=SimpleNamespace(bl_rna=SimpleNamespace(properties={'narwhal_boundary_wires'})),
        STATUSBAR_HT_header=SimpleNamespace(draw=lambda: None),
        SpaceView3D=space, SpaceInfo=space, Window=window)
    bpy = SimpleNamespace(types=types, app=SimpleNamespace(background=False),
                          context=SimpleNamespace(preferences=SimpleNamespace(system=system)))
    execute(codes['RuntimeApiProbe'], modules={'bpy': bpy})
    check([call[2:] for call in handles] == [('HEADER', 'POST_PIXEL'), ('WINDOW', 'POST_PIXEL'), ('POST_PIXEL',)],
          'UI probe exercises header, Info and Window callback signatures')
    def probe_allows(code, scale, background):
        system.ui_scale = scale
        bpy.app.background = background
        try:
            execute(code, modules={'bpy': bpy})
        except SystemExit:
            return False
        return True

    scale_cases = [(scale, background, allowed)
                   for background in (False, True)
                   for scale, allowed in ((0.0, background), (1.25, True),
                                          (-1.0, False), (-0.01, False),
                                          (float('nan'), False), (float('inf'), False),
                                          (float('-inf'), False))]
    for scale, background, allowed in scale_cases:
        check(probe_allows(codes['RuntimeApiProbe'], scale, background) == allowed,
              f'UI scale {scale}, background={background}, accepted={allowed}')
    probe = codes['RuntimeApiProbe']
    for label, broken in (
        ('missing background guard', probe.replace('bpy.app.background and scale == 0', 'scale == 0')),
        ('nonfinite scale allowed', probe.replace('math.isfinite(scale) and ', '')),
        ('negative background scale allowed', probe.replace('scale == 0', 'scale <= 0')),
        ('NaN silently normalized', probe.replace('scale = bpy.context.preferences.system.ui_scale',
             'scale = bpy.context.preferences.system.ui_scale\nscale = 1.0 if math.isnan(scale) else scale')),
        ('background zero rejected again', probe.replace(' or (bpy.app.background and scale == 0)', '')),
    ):
        check(any(probe_allows(broken, scale, background) != allowed
                  for scale, background, allowed in scale_cases), 'MUTATION RED: ' + label)
    system.ui_scale = 1.25
    bpy.app.background = False

    for mutation in ('window', 'scale', 'header'):
        if mutation == 'window':
            window.draw_handler_add = None
        elif mutation == 'scale':
            system.ui_scale = float('nan')
        else:
            types.STATUSBAR_HT_header.draw = None
        try:
            execute(codes['RuntimeApiProbe'], modules={'bpy': bpy})
        except SystemExit:
            check(True, 'MUTATION RED: missing/invalid native UI API refused: ' + mutation)
        else:
            check(False, 'MUTATION RED: missing/invalid native UI API refused: ' + mutation)
        window.draw_handler_add = add
        system.ui_scale = 1.25
        types.STATUSBAR_HT_header.draw = lambda: None

    process = SimpleNamespace(pid=123, wait=lambda **kwargs: 0)
    with patch.object(subprocess, 'Popen', return_value=process) as spawn:
        try:
            execute(codes['RuntimeBuildRunner'], ('180', 'cmd.exe', '/d', '/c', 'build.cmd'))
        except SystemExit as exc:
            check(exc.code == 0, 'bounded runtime runner preserves successful exit code')
        check(spawn.call_args.args[0] == ['cmd.exe', '/d', '/c', 'build.cmd'],
              'bounded runner preserves MSVC wrapper arguments without shell quoting')
    with patch.object(subprocess, 'Popen', return_value=process), \
            patch.object(subprocess, 'run') as kill, \
            patch.object(process, 'wait', side_effect=[subprocess.TimeoutExpired('cmd', 60), 1]):
        try:
            execute(codes['RuntimeBuildRunner'], ('1', 'cmd.exe'))
        except SystemExit as exc:
            check('timed out' in str(exc), 'runtime timeout fails rather than packaging an old binary')
        check(kill.call_args.args[0] == ['taskkill.exe', '/PID', '123', '/T', '/F'],
              'runtime timeout kills compiler descendants, not just the wrapper')


if __name__ == '__main__':
    count = 0
    def check(condition, label):
        global count
        assert condition, label
        count += 1
        print('  PASS  ' + label)
    run_checks(check)
    print(f'\n{count}/{count} Windows build checks passed')
