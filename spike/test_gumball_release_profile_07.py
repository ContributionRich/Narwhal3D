"""MAIN-REGRESSIONS-07: real mouse-release profiling, no product edits/builds.

  python3 -B spike/test_gumball_release_profile_07.py --run
  python3 -B spike/test_gumball_release_profile_07.py --run --profile
  python3 -B spike/test_gumball_release_profile_07.py --run --case circle_warm --enforce-budget
  python3 -B spike/test_gumball_release_profile_07.py --run --case all --enforce-budget
  python3 -B spike/test_gumball_release_profile_07.py --run --case circle_cold --mutation warmup_off --enforce-budget
  python3 -B spike/test_gumball_release_profile_07.py --run --case circle_96_solids --mutation full_rebuild --enforce-budget
  python3 -B spike/test_gumball_release_profile_07.py --run --case part_96_solids --mutation replacement_full_rebuild --enforce-budget

Python is required here for Blender event/RNA instrumentation, not geometry logic.
Forwarding observers retain exact production calls. Cases: cold/warm circle,
annulus, circle with 24/96 unrelated exact cylinders, and existing box-face
extrusions with 0/96 unrelated cylinders. OURS latency budgets:
250 ms synchronous release, 500 ms event enqueue to first completed viewport draw.
POST_PIXEL is an application draw boundary, NOT compositor/physical display time.
Readback occurs AFTER the latency timestamp. This probe does not claim to replay
an unspecified owner document. Mutations reintroduce the OLD warmup/rebuild code
in memory before bootstrap; no artificial delay. --enforce-budget must go RED.
Acceptance gates ALL seven scenarios, including replacement. --case additive is
an optional diagnostic-only latency filter. A named case records its predecessors
but gates only that case. Wrapper, incremental refresh and full document rebuild
are separate spans/counters; only ctx.rebuild_display is a full rebuild.
Disk scratch is private/short under /var/tmp.
No product source mutations, artifact staging, VM or full-suite runs.
"""
import cProfile
import functools
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import pstats
import sys
import tempfile
import threading
import time
import traceback
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MARKER = 'GUMBALL_RELEASE_07_RESULT'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def public_run():
    import argparse
    import fcntl
    import subprocess
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--profile', action='store_true')
    parser.add_argument('--mutation', choices=('warmup_off', 'full_rebuild', 'replacement_full_rebuild'), default='')
    parser.add_argument('--enforce-budget', action='store_true')
    parser.add_argument('--case', choices=('all', 'additive', 'circle_cold', 'circle_warm',
                                          'circle_96_solids', 'part_empty', 'part_96_solids'), default='all')
    args = parser.parse_args()
    output = Path(tempfile.mkdtemp(prefix='nh7-', dir='/var/tmp'))
    print('RELEASE_07_OUTPUT', output, flush=True)
    with open('/tmp/narwhal3d_gui.lock', 'a') as lock:
        until = time.monotonic() + 90
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > until:
                    raise RuntimeError('GUI lock busy; no process launched')
                time.sleep(.25)
        # Fail closed: retain at least 4 GiB host reserve AFTER the 2 GiB GUI cap.
        mem = {line.split(':')[0]: int(line.split()[1])
               for line in Path('/proc/meminfo').read_text().splitlines()}
        if mem['MemAvailable'] < 6 * 1024 * 1024:
            raise RuntimeError('Need 6 GiB available: 4 GiB reserve + 2 GiB GUI cap')
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(('NARWHAL', 'RHI' + 'NO', 'BLENDER_'))
               and k not in ('PYTHONPATH', 'LD_LIBRARY_PATH')}
        native = subprocess.run([str(ROOT / 'narwhal3d.sh')], cwd=ROOT,
                                env=dict(env, NARWHAL3D_LAUNCHER_TEST='native-paths'),
                                capture_output=True, text=True, check=True, timeout=10)
        for line in native.stdout.splitlines():
            key, value = line.split('=', 1)
            if value:
                env[key] = value
        with tempfile.TemporaryDirectory(prefix='settings-', dir=output) as tmp:
            scratch = Path(tmp)
            for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
                directory = scratch / key.lower()
                directory.mkdir()
                env[key] = str(directory)
            env.update(TMPDIR=tmp, PYTHONDONTWRITEBYTECODE='1',
                       PYTHONPATH=str(ROOT / 'vendor/occt'),
                       LD_LIBRARY_PATH=str(ROOT / 'tools/blender-patched/5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs'),
                       NARWHAL3D_SETTINGS_FILE=str(scratch / 'preferences.json'),
                       NARWHAL3D_DEFER_FIRST_PRESENT='1', NARWHAL3D_NO_DIALOGS='1',
                       OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2',
                       RELEASE_07_OUTPUT=str(output), RELEASE_07_PROFILE=str(int(args.profile)),
                       RELEASE_07_MUTATION=args.mutation, RELEASE_07_CASE=args.case,
                       RELEASE_07_ENFORCE=str(int(args.enforce_budget)))
            cmd = ['systemd-run', '--user', '--scope', '--quiet',
                   '-p', 'MemoryMax=2G', '-p', 'MemorySwapMax=0',
                   'timeout', '--kill-after=3', '140s',
                   str(ROOT / 'tools/blender-patched/blender'), '--threads', '2',
                   '--factory-startup', '--window-geometry', '0', '0', '1600', '1000',
                   '--enable-event-simulate', '--python-exit-code', '1',
                   '--python', str(Path(__file__).resolve()),
                   '--python', str(ROOT / 'launch/bootstrap.py')]
            (output / 'launch.json').write_text(json.dumps({
                'command': cmd, 'native_paths': native.stdout,
                'mem_available_kib': mem['MemAvailable'], 'args': vars(args)}, indent=2))
            with (output / 'stdout.log').open('w') as log:
                completed = subprocess.run(cmd, cwd=ROOT, env=env, stdout=log,
                                           stderr=subprocess.STDOUT, timeout=150, check=False)
            text = (output / 'stdout.log').read_text(errors='replace')
            print(text, flush=True)
            rows = [json.loads(line[len(MARKER) + 1:]) for line in text.splitlines()
                    if line.startswith(MARKER + ' ')]
            if (completed.returncode or not rows or rows[-1]['status'] != 'measured'
                    or 'Traceback (most recent call last)' in text):
                return 1
            return 0


if '--run' in sys.argv:
    raise SystemExit(public_run())

START = time.perf_counter()
RESULT: dict[str, Any] = {'status': 'running', 'cases': [], 'semantic_errors': [], 'budget_ms': {'release': 250, 'enqueue_to_draw': 500},
          'mutation': os.environ.get('RELEASE_07_MUTATION', ''),
          'case_filter': os.environ.get('RELEASE_07_CASE', 'all')}
OUT = Path(os.environ['RELEASE_07_OUTPUT'])
FINISH = threading.Lock()
LIVE: dict[str, Any] | None = None
STACK: list[dict[str, Any]] = []
POSITION = (0, 0)
DRAW_REGION = None
PATCH = None
BASE_PIXELS = None
DRAW_ERROR = None
PROFILE = None
MUTATED_CODES = {}


def require(ok, label):
    if not ok:
        raise AssertionError(label)


def finish(code, error=None):
    if not FINISH.acquire(blocking=False):
        os._exit(1)
    RESULT.update(status='measured' if code == 0 else 'failed', error=error,
                  elapsed_s=time.perf_counter() - START)
    (OUT / 'result.json').write_text(json.dumps(RESULT, indent=2))
    print(MARKER + ' ' + json.dumps({'status': RESULT['status'], 'error': error,
          'result': str(OUT / 'result.json'), 'elapsed_s': RESULT['elapsed_s']}), flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def watchdog():
    time.sleep(125)
    finish(1, '125 s watchdog expired')


def window():
    return bpy.context.window_manager.windows[0]


def event(kind, value, point=None):
    global POSITION
    if point is not None:
        POSITION = tuple(round(x) for x in point)
    window().event_simulate(type=kind, value=value, x=POSITION[0], y=POSITION[1])


def wait_for(predicate, label, seconds=40):
    until = time.perf_counter() + seconds
    while not predicate():
        require(time.perf_counter() < until, 'Timed out: ' + label)
        require(DRAW_ERROR is None, DRAW_ERROR)
        yield .01


def observe(owner, name, label=None):
    original = getattr(owner, name)
    label = label or owner.__name__ + '.' + name

    @functools.wraps(original)
    def timed(*args, **kwargs):
        if LIVE is None:
            return original(*args, **kwargs)
        row: dict[str, Any] = {'label': label, 'start_ms': (time.perf_counter() - LIVE['sent']) * 1000,
               'parent': STACK[-1]['label'] if STACK else None, 'child_ms': 0}
        if label == 'bridge.refresh_display_objects':
            changes = args[1]
            row['changes'] = None if changes is None else {
                'removed': dict(changes.get('removed') or {}), 'added': list(changes.get('added') or [])}
        if label == 'ui._gumball_end':
            LIVE['handler_start_ms'] = row['start_ms']
            if PROFILE:
                PROFILE.enable()
        STACK.append(row)
        start = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            row['ms'] = (time.perf_counter() - start) * 1000
            STACK.pop()
            row['self_ms'] = row['ms'] - row.pop('child_ms')
            if STACK:
                STACK[-1]['child_ms'] += row['ms']
            LIVE['spans'].append(row)
            if label == 'ui._gumball_end':
                if PROFILE:
                    PROFILE.disable()
                LIVE['release_ms'] = row['ms']
                LIVE['handler_end_ms'] = (time.perf_counter() - LIVE['sent']) * 1000
    setattr(owner, name, timed)


def install_observers():
    for name in ('_gumball_end', '_extrude_commit', '_extrude_compute',
                 'rebuild_display_keeping_selection'):
        observe(ui, name, 'ui.' + name)
    for name in ('gumball_extrude', 'extrude_curve', 'occt_backed', '_proxy_from_occt',
                 'subobject_parts', 'geometry_to_bmesh', 'display_tessellation', 'refresh_display_objects'):
        observe(bridge, name, 'bridge.' + name)
    for name in ('available', 'from_recipe', 'subobject_topology', 'tessellate_with_edges', 'brep_description'):
        observe(occt, name, 'occt.' + name)
    for name in ('add_geometry', 'note_change', 'end_undo', '_make_display_object', 'rebuild_display'):
        observe(commands.NarwhalContext, name, 'ctx.' + name)
    for name in ('snapshot', 'add_object', 'replace_geometry'):
        observe(NarwhalModel, name, 'model.' + name)
    # This callback is registered after bootstrap's actual app draw callbacks.
    bpy.types.SpaceView3D.draw_handler_add(after_draw, (), 'WINDOW', 'POST_PIXEL')


def after_draw():
    global BASE_PIXELS, DRAW_ERROR
    try:
        region = bpy.context.region
        if region is None or region.as_pointer() != DRAW_REGION or PATCH is None:
            return
        import gpu
        import numpy as np
        def pixels():
            x, y, _w, _h = gpu.state.viewport_get()
            buf = gpu.state.active_framebuffer_get().read_color(x + PATCH[0], y + PATCH[1], 5, 5, 3, 0, 'FLOAT')
            buf.dimensions = 75
            return np.array(buf, dtype=np.float32)
        if LIVE is None and BASE_PIXELS is None:
            BASE_PIXELS = pixels()
        elif LIVE is not None and 'handler_end_ms' in LIVE and 'draw_ms' not in LIVE:
            # No timer/poll interval in this boundary. Do not include readback cost.
            LIVE['draw_ms'] = (time.perf_counter() - LIVE['sent']) * 1000
            LIVE['post_handler_draw_ms'] = LIVE['draw_ms'] - LIVE['handler_end_ms']
            LIVE['pixel_difference'] = float(np.max(np.abs(pixels() - BASE_PIXELS)))
    except BaseException:
        DRAW_ERROR = traceback.format_exc()


def provenance():
    modules = (ui, bridge, commands, gumball, occt, model_module, undo_module)
    result: dict[str, Any] = {'python': sys.version, 'blender': bpy.app.version_string,
              'modules': {m.__name__: {'path': m.__file__, 'sha256': digest(m.__file__)} for m in modules},
              'history_backend': ui.app_state.ctx.undo.backend,
              'kernel_warmup': ui.kernel_warmup_stats(), 'ocp_loaded': occt._ocp is not None}
    # -B prevents writes, not stale bytecode READS. Check actual executed function
    # code against a fresh compile of disk source, before trusting any timings.
    result['executed_code_matches_source'] = {}
    for module, names in ((ui, ('_gumball_end', '_extrude_commit', '_extrude_compute', 'warm_geometry_kernel',
                               'rebuild_display_keeping_selection')),
                          (bridge, ('_occt', 'extrude_curve', 'gumball_extrude', 'refresh_display_objects')),
                          (occt, ('available', 'from_recipe'))):
        compiled = compile(Path(module.__file__).read_text(), module.__file__, 'exec')
        for name in names:
            code = next(c for c in compiled.co_consts if inspect.iscode(c) and c.co_name == name)
            matches = inspect.unwrap(getattr(module, name)).__code__ == code
            result['executed_code_matches_source'][module.__name__ + '.' + name] = matches
            target = module.__name__ + '.' + name
            expected = MUTATED_CODES.get(target, code)
            require(inspect.unwrap(getattr(module, name)).__code__ == expected,
                    'unexpected executed code/source mismatch: ' + target)
    result['declared_mutations'] = sorted(MUTATED_CODES)
    maps = Path('/proc/self/maps').read_text().splitlines()
    paths = sorted({line.split(maxsplit=5)[-1] for line in maps if 'libnarwhal_' in line})
    result['loaded_libraries'] = [{'path': p, 'sha256': digest(p)} for p in paths]
    metadata = ROOT / 'artifacts/application-core/linux-x86_64/build.json'
    if metadata.exists():
        data = json.loads(metadata.read_text())
        result['application_build'] = data
        result['application_source_mismatches'] = [p for p, h in data['source_inputs'].items()
                                                    if not (ROOT / p).exists() or digest(ROOT / p) != h]
    return result


def exact_copy_depth(before, after):
    """Exact encoding equality modulo actual openNURBS Duplicate operations.

    MEASURED here and spike/test_undo.py: tagged Brep Duplicate increments a
    user-data copy counter and its CRC. Compare whole encodings against real
    duplicate outputs, never mask bytes, round geometry or omit surfaces/trims.
    Eight is an OURS bounded diagnostic limit, not a geometry tolerance.
    """
    if before == after:
        return 0
    if before is None or after is None:
        return None
    geom = r3.CommonObject.Decode(before)
    for copies in range(1, 9):
        geom = geom.Duplicate()
        if geom.Encode() == after:
            return copies
    return None


def check_copy_oracle():
    box = bridge.make_box((-3, -3, 0), (3, 3, 0), 2)
    encoded = box.Encode()
    duplicate = box.Duplicate().Duplicate().Duplicate()
    require(exact_copy_depth(encoded, duplicate.Encode()) == 3, 'copy-counter oracle accepts real copies')
    changed = bridge.make_box((-3, -3, 0), (3, 3, 0), 3)
    require(exact_copy_depth(encoded, changed.Encode()) is None, 'copy oracle rejects changed exact geometry')
    duplicate.SetUserString('fixture', 'changed')
    require(exact_copy_depth(encoded, duplicate.Encode()) is None, 'copy oracle rejects changed user data')


def reset(population=0):
    for ob in list(bpy.data.objects):
        data = ob.data
        bpy.data.objects.remove(ob, do_unlink=True)
        if data is not None and data.users == 0 and isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
    ui.clear_subobject_selection()
    ui.app_state.set_model(NarwhalModel())
    ui.invalidate_snap_cache()
    ctx = ui.app_state.ctx
    for i in range(population):
        geom = bridge.make_cylinder((30 + (i % 6) * 8, (i // 6) * 8, 0), 2, 5)
        ctx.add_geometry(geom, 'Unrelated')
    return ctx


def run():
    global LIVE, DRAW_REGION, PATCH, BASE_PIXELS, PROFILE
    yield from wait_for(lambda: os.environ.get('NARWHAL3D_UI_READY') == '1'
                       and not ui._viewports_in_progress and ui._viewport_count() == 4
                       and ui.is_input_active(), 'four panes and real input ready')
    require(ui.app_state.ctx.model.object_count() == 0, 'refuse nonempty startup document')
    RESULT['provenance'] = provenance()
    check_copy_oracle()
    install_observers()
    for label, annulus, population in (('circle_cold', False, 0), ('circle_warm', False, 0),
                                       ('annulus', True, 0), ('circle_24_solids', False, 24),
                                       ('circle_96_solids', False, 96),
                                       ('part_empty', False, 0), ('part_96_solids', False, 96)):
        part_case = label.startswith('part_')
        ctx = reset(population)
        if part_case:
            names = [ctx.add_geometry(bridge.make_box((-3, -3, 0), (3, 3, 0), 2), 'Box')[1]]
            parts = bridge.subobject_parts(ctx.geom_by_name(names[0]))
            top = [fid for fid, tris in parts['faces']
                   if tris and all(abs(p[2] - 2) < 1e-7 for tri in tris for p in tri)]
            require(len(top) == 1, 'one exact top face for existing-part fixture')
        else:
            names = [ctx.add_geometry(bridge.make_circle((0, 0, 0), 3), 'Circle')[1]]
            if annulus:
                names.append(ctx.add_geometry(bridge.make_circle((0, 0, 0), 1), 'Circle')[1])
        ctx.select_by_names(names)
        ids_before = {f.Attributes.Name: str(f.Attributes.Id) for f in ctx.model.file.Objects}
        display_before = {ob.name: (ob.as_pointer(), ob.data.as_pointer()) for ob in bpy.data.objects}
        ctx.undo.clear()
        if part_case:
            # Real part picking refreshes this serial-keyed topology cache BEFORE
            # selecting a face. Selecting before undo.clear/cache refresh makes
            # the UI correctly discard the fixture's now-stale part selection.
            bpy.context.view_layer.update()
            require(names[0] in ui._brep_parts_all(), 'replacement target in current UI topology')
            ui.toggle_subobject((names[0], ui.subobject.FACE, top[0]))
        ui.set_gumball(True)
        ui.set_gumball_alignment('World')
        area = ui._pane_by_label('Front')
        region, space = ui._window_region(area), area.spaces.active
        ui.set_active_pane(area)
        space.region_3d.view_location = Vector((0, 0, 2.5))
        space.region_3d.view_distance = 22
        space.region_3d.update()
        # Witness on the final outer silhouette, away from the selected profile,
        # gumball axis and grid intersections. Works in Wireframe and Shaded.
        p = ui._project_2d(region, space.region_3d, (3, 0, 3.3 if part_case else 2.3))
        PATCH = (round(p[0]) - 2, round(p[1]) - 2)
        DRAW_REGION, BASE_PIXELS = region.as_pointer(), None
        ui._tag_redraw_viewports()
        yield from wait_for(lambda: BASE_PIXELS is not None, 'pre-gesture framebuffer witness')
        yield .15
        _frame, handles = ui._gumball_handles(area, space, region)
        matches = [h for h in handles if h.kind == gumball.EXTRUDE_HANDLE and h.axis == 2]
        require(bool(matches), 'extrude dot absent: ' + repr({
            'case': label, 'parts': ui.selected_subobjects(), 'targets': ui._extrude_targets(),
            'frame': _frame, 'handles': [(h.kind, h.axis) for h in handles]}))
        handle = matches[0]
        a, b = handle.hot[0]
        p = ui._project_2d(region, space.region_3d, tuple((a[i] + b[i]) / 2 for i in range(3)))
        point = (region.x + p[0], region.y + p[1])
        require(ui._gumball_hit(area, space, region, *point).key == handle.key, 'projected dot hit')
        event('MOUSEMOVE', 'NOTHING', point)
        yield .1
        # Capture the actual pre-gesture document after the UI has prepared its
        # bounding/topology caches, not the just-created pre-presentation Brep.
        before = {f.Attributes.Name: f.Geometry.Encode() for f in ctx.model.file.Objects}
        before_tags = {f.Attributes.Name: f.Geometry.GetUserStrings2() for f in ctx.model.file.Objects}
        event('LEFTMOUSE', 'PRESS')
        yield from wait_for(lambda: ui._gumball_drag is not None, 'real press starts drag')
        st = ui._gumball_drag
        require(st['kind'] == gumball.EXTRUDE_HANDLE, 'real extrusion drag')
        require(st['extrude'][0] == ('part' if part_case else 'objects'), 'gesture uses intended publication path')
        delta = tuple((st['axis_2d'][1][i] - st['axis_2d'][0][i]) * 5 / st['axis_len'] for i in range(2))
        event('MOUSEMOVE', 'NOTHING', tuple(st['mouse0'][i] + delta[i] for i in range(2)))
        yield .2
        require(abs(st['total'] - 5) < .06, 'five-unit drag within pixel rounding')
        require(not st.get('ext_cands'), 'no precomputed exact result hiding release work')
        distance = st['total']
        PROFILE = cProfile.Profile() if os.environ.get('RELEASE_07_PROFILE') == '1' else None
        LIVE = {'case': label, 'population': population, 'replacement': part_case,
                'history_backend': ctx.undo.backend,
                'sent': time.perf_counter(), 'spans': []}
        event('LEFTMOUSE', 'RELEASE')
        yield from wait_for(lambda: 'draw_ms' in LIVE, 'completed post-release viewport draw')
        row, LIVE = LIVE, None
        PATCH = None
        require(ui._gumball_drag is None and not ctx.undo.recording, 'release closes transaction')
        results = [f for f in ctx.model.file.Objects
                   if f.Attributes.Name == names[0]] if part_case else [
                       f for f in ctx.model.file.Objects if f.Attributes.Name.startswith('Extrusion')]
        require(len(results) == 1, 'one extrusion result in document')
        require(ctx.model.object_count() == len(before) + (0 if part_case else 1),
                'replacement does not add objects; additive extrusion adds exactly one')
        result_name = results[0].Attributes.Name
        geom = results[0].Geometry
        master = kernel.master_of(geom)
        require(master is not None and occt.is_airtight(master), 'airtight analytic master')
        types = occt.face_types(master)
        expected_types = ({'PLANE': 6} if part_case else
                          {'CYLINDER': 2, 'PLANE': 2} if annulus else {'CYLINDER': 1, 'PLANE': 2})
        require(types == expected_types, 'analytic face counts, not faceted geometry')
        expected_volume = 36 * (2 + distance) if part_case else math.pi * (8 if annulus else 9) * distance
        volume = occt.volume(master)
        require(abs(volume - expected_volume) < 1e-6, 'exact volume')
        recipe = kernel.recipe_of(geom)
        require(recipe is not None, 'replay recipe retained')
        replay = occt.from_recipe(recipe)
        require(occt.is_airtight(replay) and occt.face_types(replay) == expected_types
                and abs(occt.volume(replay) - expected_volume) < 1e-6, 'recipe rebuild remains exact')
        require(len(geom.Faces) == sum(expected_types.values()), 'document contains exact Brep, not triangulated proxy')
        unchanged = [n for n in before if not part_case or n != names[0]]
        require(all(ctx.geom_by_name(n).Encode() == before[n] for n in unchanged),
                'source profiles and unrelated document geometry unchanged')
        ids_after = {f.Attributes.Name: str(f.Attributes.Id) for f in ctx.model.file.Objects}
        require(all(ids_after[n] == oid for n, oid in ids_before.items()), 'existing object IDs preserved')
        require(set(ui.selection_names()) == set(names), 'whole-object selection preserved')
        require(not ui.selected_subobjects(), 'release clears completed face selection')
        require(len(ctx.undo.undo_names()) == 1, 'one undo record')
        ob = bpy.data.objects.get(results[0].Attributes.Name)
        require(ob is not None and len(ob.data.vertices) > 0, 'published display mesh exists')
        require(row['pixel_difference'] > .02, 'final extrusion silhouette changes real framebuffer')
        bpy.context.view_layer.update()
        displayed = [tuple(ob.matrix_world @ v.co) for v in ob.data.vertices]
        bounds = [tuple(f(p[i] for p in displayed) for i in range(3)) for f in (min, max)]
        expected_bounds = ((-3, -3, 0), (3, 3, distance + (2 if part_case else 0)))
        require(all(abs(a - b) < .03 for p, q in zip(bounds, expected_bounds) for a, b in zip(p, q)),
                'final display has exact result extents (within viewport tessellation tolerance)')
        row.update(face_types=types, volume=volume, document_faces=len(geom.Faces),
                   display_vertices=len(ob.data.vertices), last_snapshot=ctx.model.last_snapshot,
                   full_rebuild_calls=sum(s['label'] == 'ctx.rebuild_display' for s in row['spans']),
                   display_refresh_wrapper_calls=sum(s['label'] == 'ui.rebuild_display_keeping_selection'
                                                     for s in row['spans']),
                   incremental_refresh_calls=sum(s['label'] == 'bridge.refresh_display_objects'
                                                 and s['changes'] is not None for s in row['spans']),
                   refresh_changes=[s['changes'] for s in row['spans'] if s['label'] == 'bridge.refresh_display_objects'],
                   expected_refresh_changes=[{'removed': {ids_before[names[0]]: names[0]},
                                              'added': [ids_before[names[0]]]}] if part_case else [],
                   unchanged_display_objects=len(unchanged),
                   retained_unchanged_display_objects=sum(bpy.data.objects.get(n) is not None
                       and (bpy.data.objects[n].as_pointer(), bpy.data.objects[n].data.as_pointer()) == display_before[n]
                       for n in unchanged),
                   retained_display_objects=sum(bpy.data.objects.get(n) is not None
                       and (bpy.data.objects[n].as_pointer(), bpy.data.objects[n].data.as_pointer()) == handles
                       for n, handles in display_before.items()), before_display_objects=len(display_before))
        row['budget_violations'] = [key for key, value, budget in (
            ('release', row['release_ms'], 250), ('enqueue_to_draw', row['draw_ms'], 500)) if value > budget]
        if PROFILE:
            PROFILE.dump_stats(str(OUT / (label + '.prof')))
            stream = io.StringIO()
            pstats.Stats(PROFILE, stream=stream).strip_dirs().sort_stats('cumulative').print_stats(45)
            (OUT / (label + '-profile.txt')).write_text(stream.getvalue())
        row.pop('sent')
        RESULT['cases'].append(row)
        # History checks are outside release timing, including exact content/IDs,
        # not just an object count that would pass with the wrong restored geometry.
        ctx.undo_last()
        restored = {f.Attributes.Name: f.Geometry.Encode() for f in ctx.model.file.Objects}
        row['undo_difference'] = {n: {'before_tags': before_tags.get(n),
                                     'after_tags': ctx.geom_by_name(n).GetUserStrings2() if n in restored else None}
                                  for n in before.keys() | restored.keys() if before.get(n) != restored.get(n)}
        if restored != before:
            (OUT / (label + '-undo-difference.json')).write_text(json.dumps(
                {n: {'before': before.get(n), 'after': restored.get(n)} for n in row['undo_difference']}, indent=2))
        row['undo_copy_counts'] = {n: exact_copy_depth(before.get(n), restored.get(n))
                                  for n in row['undo_difference']}
        if any(count is None for count in row['undo_copy_counts'].values()):
            RESULT['semantic_errors'].append(label + ': Undo differs beyond actual exact Duplicate encodings')
        require({f.Attributes.Name: str(f.Attributes.Id) for f in ctx.model.file.Objects} == ids_before,
                'Undo restores original object IDs')
        ctx.redo_last()
        redone = ctx.geom_by_name(result_name)
        redone_master = bridge._occt_operand(redone)
        require(occt.is_airtight(redone_master) and occt.face_types(redone_master) == expected_types
                and abs(occt.volume(redone_master) - expected_volume) < 1e-6, 'Redo restores exact result')
        require(ctx.model.object_count() == len(before) + (0 if part_case else 1), 'Redo object count')
        row['semantic_checks'] = 'failed' if any(c is None for c in row['undo_copy_counts'].values()) else 'passed'
        print('RELEASE_07_CASE ' + json.dumps({k: v for k, v in row.items() if k != 'spans'}), flush=True)
        yield .1
        if RESULT['case_filter'] == label:
            break
    RESULT['final_provenance'] = provenance()
    require(RESULT['provenance']['modules'] == RESULT['final_provenance']['modules'], 'sources stable during run')
    if RESULT['mutation'] == 'warmup_off':
        require(not RESULT['provenance']['ocp_loaded'] and RESULT['final_provenance']['ocp_loaded'],
                'warmup mutant actually deferred OCP loading into interaction')
    if RESULT['mutation'] == 'full_rebuild':
        require(all(r['full_rebuild_calls'] == 1 for r in RESULT['cases']), 'old unconditional rebuild actually executed')
    if RESULT['mutation'] == 'replacement_full_rebuild':
        require(any(r['replacement'] for r in RESULT['cases']), 'replacement mutant was exercised')
        require(all(r['full_rebuild_calls'] == int(r['replacement']) and r['incremental_refresh_calls'] == 0
                    for r in RESULT['cases']), 'replacement mutant actually takes full instead of targeted refresh')
    require(not RESULT['semantic_errors'], 'Undo restores the complete exact pre-gesture document: ' + repr(RESULT['semantic_errors']))
    RESULT['semantic_checks'] = 'passed'
    if os.environ.get('RELEASE_07_ENFORCE') == '1':
        measured = [r for r in RESULT['cases'] if RESULT['case_filter'] in ('all', r['case'])
                    or RESULT['case_filter'] == 'additive' and not r['replacement']]
        require(bool(measured), 'requested budget case ran')
        require(not any(r['budget_violations'] for r in measured), 'release latency budget exceeded')
    if not RESULT['mutation']:
        require(RESULT['provenance']['kernel_warmup']['ready'] and RESULT['provenance']['ocp_loaded'],
                'kernel is actually loaded before the first gesture')
        require(all(r['full_rebuild_calls'] == 0 for r in RESULT['cases']), 'no extrusion release rebuilds the whole display')
        require(all(r['display_refresh_wrapper_calls'] == r['incremental_refresh_calls'] == int(r['replacement'])
                    for r in RESULT['cases']), 'replacement wrapper performs one incremental refresh; additive performs none')
        require(all(r['refresh_changes'] == r['expected_refresh_changes'] for r in RESULT['cases']),
                'incremental refresh receives only the replaced object IDs/names')
        require(all(r['retained_unchanged_display_objects'] == r['unchanged_display_objects']
                    for r in RESULT['cases']), 'release preserves object AND mesh identity for every unchanged object')
        require(all(r['replacement'] or r['retained_display_objects'] == r['before_display_objects']
                    for r in RESULT['cases']), 'additive release preserves all pre-existing display objects')
        RESULT['display_identity_checks'] = 'passed'


def install_mutation():
    mutation = RESULT['mutation']
    if not mutation:
        return
    name, old, new = {
        'warmup_off': ('warm_geometry_kernel',
                      'st["ready"] = backend is not None and backend.available()',
                      'st["ready"] = backend is not None'),
        'full_rebuild': ('_extrude_commit',
                         "if replaced:\n        rebuild_display_keeping_selection({'removed': replaced, 'added': list(replaced)})",
                         'rebuild_display_keeping_selection()'),
        'replacement_full_rebuild': ('_extrude_commit',
                                    "rebuild_display_keeping_selection({'removed': replaced, 'added': list(replaced)})",
                                    'rebuild_display_keeping_selection()'),
    }[mutation]
    original = getattr(ui, name)
    source = inspect.getsource(original)
    require(source.count(old) == 1, 'unique real regression mutation anchor')
    exec(compile(source.replace(old, new), '<release-07-' + mutation + '>', 'exec'), ui.__dict__)
    MUTATED_CODES['narwhal3d.ui.' + name] = getattr(ui, name).__code__
    RESULT['mutation_target'] = {'function': name, 'old': old, 'new': new}


def tick():
    try:
        with bpy.context.temp_override(window=window()):
            return next(STEPS)
    except StopIteration:
        finish(0)
    except BaseException:
        finish(1, traceback.format_exc())


threading.Thread(target=watchdog, daemon=True).start()
try:
    require('narwhal3d' not in sys.modules and not os.environ.get('NARWHAL3D_UI_READY'), 'load before bootstrap')
    settings = Path(os.environ['NARWHAL3D_SETTINGS_FILE'])
    require(settings.is_absolute() and settings.is_relative_to(Path(tempfile.gettempdir()))
            and not settings.exists(), 'fresh temporary settings')
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    import math
    import bpy
    import rhino3dm as r3
    from mathutils import Vector
    from narwhal3d import bridge, commands, gumball, kernel, model as model_module, ui, undo as undo_module
    from narwhal3d.kernel import occt_backend as occt
    from narwhal3d.model import NarwhalModel
    require(not bpy.app.background, 'real GUI required')
    install_mutation()
    STEPS = run()
    bpy.app.timers.register(tick, first_interval=.1)
except BaseException:
    finish(1, traceback.format_exc())
