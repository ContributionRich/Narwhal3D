#!/usr/bin/env python3
"""Visible ordered-pointer Sweep1 acceptance through real bootstrap/window events.

python3 -B spike/test_sweep1_interaction_gui.py --launch [--mutations]
Fixture/camera setup uses APIs; selection, commands, confirmation, Undo and Redo
use actual pointer/keyboard events. No simulated selected-set chronology.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ('launch/bootstrap.py', 'narwhal3d/ui.py', 'narwhal3d/state.py',
           'narwhal3d/commands.py', 'narwhal3d/solid_commands.py', 'narwhal3d/bridge.py',
           'narwhal3d/kernel/__init__.py', 'narwhal3d/surfacing_backend.py',
           'spike/test_surfacing_preparation.py', 'spike/test_sweep1_command_integration.py',
           'spike/test_sweep1_interaction_gui.py', 'narwhal3d/curve_inputs.py',
           'native/geometry_core/target/release/libnarwhal_geometry_core.so')
EXPECTED_RED = 'accepted UUID chronology matches actual pointer order'
EXPECTED_MUTATIONS = {'sort-by-name': EXPECTED_RED,
                      'drop-reversal': 'mouse Sweep1 publishes exactly one result',
                      'uuid-recapture': 'mouse Sweep1 publishes exactly one result',
                      'ignore-cancel': 'real Escape discards command-local picks'}


def launch():
    import fcntl
    import subprocess

    out = Path(tempfile.mkdtemp(prefix='narwhal-sweep1-interaction-'))
    env = {k: v for k, v in os.environ.items() if k in (
        'PATH', 'DISPLAY', 'XAUTHORITY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS', 'LANG',
        'NARWHAL3D_BREP_WRITER')}
    # Source-only exact-document test dependency; never stage it into a release.
    if env.get('NARWHAL3D_BREP_WRITER'):
        writer = Path(env['NARWHAL3D_BREP_WRITER']).resolve(strict=True)
        (out/'exact-writer-input.json').write_text(json.dumps({
            'path': str(writer), 'sha256': hashlib.sha256(writer.read_bytes()).hexdigest()}))
    paths = subprocess.check_output(['bash', str(ROOT/'narwhal3d.sh')], cwd=ROOT,
        env=dict(os.environ, NARWHAL3D_LAUNCHER_TEST='native-paths'), text=True, timeout=10)
    for row in paths.splitlines():
        key, value = row.split('=', 1)
        if value:
            env[key] = value
    locks = []
    print('SWEEP1_GUI_RECEIPT', out, flush=True)
    try:
        deadline = time.monotonic()+90
        while True:
            try:
                for name in ('/tmp/narwhal3d-heavy-build.lock', '/tmp/narwhal3d_gui.lock'):
                    lock = open(name, 'a'); locks.append(lock)
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                for lock in locks: lock.close()
                locks.clear()
                if time.monotonic() >= deadline:
                    raise TimeoutError('heavy/GUI lock busy; no window launched')
                time.sleep(.25)
        receipts = []
        modes = ('baseline', 'sort-by-name', 'drop-reversal', 'uuid-recapture', 'ignore-cancel', 'restored') if '--mutations' in sys.argv else ('baseline',)
        for mode in modes:
            case = out/mode; case.mkdir()
            child_env = dict(env)
            for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'TMPDIR'):
                folder = case/key.lower(); folder.mkdir(); child_env[key] = str(folder)
            child_env.update(SWEEP1_GUI_OUT=str(case), SWEEP1_GUI_MODE=mode,
                NARWHAL3D_SETTINGS_FILE=str(case/'preferences.json'), PYTHONDONTWRITEBYTECODE='1',
                PYTHONPYCACHEPREFIX=str(case/'bytecode'), PYTHONPATH=str(ROOT/'vendor/occt'),
                LD_LIBRARY_PATH=str(ROOT/'tools/blender-patched/5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs'),
                NARWHAL3D_NO_DIALOGS='1', NARWHAL3D_DEFER_FIRST_PRESENT='1',
                NARWHAL3D_STATUSBAR_HEIGHT='37', GHOST_BACKEND='X11',
                OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
            available = int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines()
                                 if l.startswith('MemAvailable:')))
            assert available >= 5*1024*1024, 'Need 4 GiB desktop reserve plus 1 GiB test cap'
            cmd = ['systemd-run', '--user', '--scope', '--quiet', '-p', 'MemoryMax=1024M',
                '-p', 'MemorySwapMax=0', '-p', 'CPUQuota=100%', 'timeout', '--signal=TERM',
                '--kill-after=5s', '180s', 'bwrap', '--ro-bind', '/', '/', '--dev-bind', '/dev', '/dev',
                '--proc', '/proc', '--bind', str(case), str(case), '--unshare-net', '--die-with-parent',
                str(ROOT/'tools/blender-patched/blender'), '--factory-startup', '-t', '1',
                '-p', '40', '40', '1440', '1000', '--enable-event-simulate', '--python-exit-code', '1',
                '--python', str(Path(__file__).resolve()), '--python', str(ROOT/'launch/bootstrap.py')]
            before = {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in SOURCES}
            (case/'launch.json').write_text(json.dumps(dict(command=cmd, native_paths=paths,
                mem_available_kib=available, source_sha256=before), indent=2))
            with (case/'run.log').open('w') as log:
                run = subprocess.run(cmd, cwd=ROOT, env=child_env, stdout=log,
                                     stderr=subprocess.STDOUT, timeout=190)
            text = (case/'run.log').read_text()
            print(text[-5000:], flush=True)
            after = {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in SOURCES}
            (case/'source_check.json').write_text(json.dumps(dict(before=before, after=after,
                                                                unchanged=before == after), indent=2))
            report = json.loads((case/'result.json').read_text()) if (case/'result.json').exists() else {}
            bad_log = any(s in text for s in ('Error in draw callback', 'Traceback (most recent call last)'))
            expected = (run.returncode == 1 and report.get('error') == EXPECTED_MUTATIONS[mode]
                        if mode in EXPECTED_MUTATIONS else
                        run.returncode == 0 and report.get('status') == 'passed')
            readers = []
            if mode not in EXPECTED_MUTATIONS and expected:
                prefix = cmd[:cmd.index(str(ROOT/'tools/blender-patched/blender'))]
                for suffix in ('.narwhal', '.3dm'):
                    reader = prefix + [str(ROOT/'tools/blender-patched/5.2/python/bin/python3.13'),
                        '-B', str(ROOT/'spike/test_sweep1_command_integration.py'), '--read', str(case/('mouse'+suffix))]
                    with (case/('reader'+suffix+'.log')).open('w') as log:
                        read = subprocess.run(reader,cwd=ROOT,env=child_env,stdout=log,stderr=subprocess.STDOUT,timeout=190)
                    readers.append(dict(format=suffix,exit=read.returncode))
                    expected = expected and read.returncode == 0
                (case/'readers.json').write_text(json.dumps(readers,indent=2))
            receipts.append(dict(mode=mode, exit=run.returncode, status=report.get('status'),
                expected_result=expected, source_unchanged=before == after, bad_log=bad_log,
                checks=len(report.get('checks', [])), readers=readers, path=str(case)))
            (out/'receipt.json').write_text(json.dumps(receipts, indent=2))
            if not expected or before != after or bad_log:
                return 1
        print('SWEEP1_GUI_COMPLETE', 'GREEN -> intended RED -> GREEN' if len(modes) > 1 else 'GREEN', out, flush=True)
        return 0
    finally:
        for lock in locks: lock.close()


if '--launch' in sys.argv:
    raise SystemExit(launch())

import threading
import traceback
from collections import deque
import bpy

OUT = Path(os.environ['SWEEP1_GUI_OUT'])
MODE = os.environ['SWEEP1_GUI_MODE']
START = time.monotonic()
RESULT = dict(status='running', mode=MODE, checks=[], events=[], states=[], captures=[], owned_pid=os.getpid())
FINISH_LOCK = threading.Lock()
POSITION = (0, 0)
PAINT = deque(maxlen=8)


def check(ok, label):
    RESULT['checks'].append(dict(ok=bool(ok), label=label))
    print('SWEEP1_GUI', 'PASS' if ok else 'FAIL', label, flush=True)
    if not ok:
        raise AssertionError(label)


def finish(error=None):
    if not FINISH_LOCK.acquire(False):
        return
    RESULT.update(status='failed' if error else 'passed', error=error,
                  elapsed_s=time.monotonic()-START)
    try:
        cgroup = Path('/sys/fs/cgroup'+Path('/proc/self/cgroup').read_text().strip().split(':', 2)[2])
        RESULT['cgroup'] = {name: (cgroup/name).read_text().strip()
                            for name in ('memory.max', 'memory.swap.max', 'memory.peak')}
    except OSError:
        pass
    (OUT/'result.json').write_text(json.dumps(RESULT, indent=2))
    print('SWEEP1_GUI_RESULT', RESULT['status'], error or '', flush=True)
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(int(bool(error)))


def window():
    return bpy.context.window_manager.windows[0]


def event(key, value, text='', **modifiers):
    RESULT['events'].append(dict(type=key, value=value, unicode=text, position=POSITION, modifiers=modifiers))
    window().event_simulate(type=key, value=value, x=POSITION[0], y=POSITION[1],
                           **({'unicode': text} if text else {}), **modifiers)


def tap(key, point=None, **modifiers):
    global POSITION
    if point is not None:
        POSITION = tuple(round(v) for v in point)
        event('MOUSEMOVE', 'NOTHING', **modifiers); yield .12
    event(key, 'PRESS', **modifiers); yield .06
    event(key, 'RELEASE', **modifiers); yield .25


def wait(predicate, label, seconds=6):
    deadline = time.monotonic()+seconds
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError('timed out: '+label)
        yield .06
    check(True, label)


def line(text):
    check(not st.input_buffer, 'empty keyboard buffer before '+text)
    digits = ('ZERO', 'ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX', 'SEVEN', 'EIGHT', 'NINE')
    for char in text:
        key = digits[int(char)] if char.isdigit() else char.upper()
        event(key, 'PRESS', char); yield .04
        event(key, 'RELEASE'); yield .04
    check(st.input_buffer == text, 'window keyboard populated '+text)
    yield from tap('RET')


def observe_text(x, y, text, *args, **kwargs):
    result = ORIGINAL_DRAW(x, y, text, *args, **kwargs)
    frame = sys._getframe(1)
    if frame.f_code is ui.draw_command_bar.__code__ and y == frame.f_locals.get('prompt_y'):
        if x == frame.f_locals.get('pad_x'):
            PAINT.append([])
        if PAINT:
            PAINT[-1].append(str(text))
    return result


def painted(label, expected=None, shot=False):
    PAINT.clear()
    ui.redraw_cmdbar(); ui._tag_redraw_viewports()
    yield .4
    text = ''.join(PAINT[-1]) if PAINT else ''
    check(bool(text) and (expected is None or expected in text), 'visible command bar: '+label)
    RESULT['states'].append(dict(label=label, painted_prompt=text, input=st.input_buffer,
        command=getattr(st.active_command, 'name', None), undo=st.ctx.undo.undo_names(),
        redo=st.ctx.undo.redo_names(), object_count=st.model.object_count(), history=st.history[-6:]))
    if shot:
        path = OUT/(label+'.png')
        check(bpy.ops.screen.screenshot(filepath=str(path)) == {'FINISHED'}, label+' screenshot')
        RESULT['captures'].append(str(path))


def unchanged():
    return all(st.model.file.Objects.FindId(oid).Geometry.Encode() == encoded
               for oid, encoded in SOURCE_CONTENT.items())


def run():
    global st, SOURCE_CONTENT
    yield from wait(lambda: os.environ.get('NARWHAL3D_UI_READY') == '1'
                    and ui._preferences_ready and not ui._viewports_in_progress
                    and ui._viewport_count() == 4 and ui.is_input_active(), 'visible bootstrap ready', 50)
    st = ui.app_state
    check(not bpy.app.background and window().width > 800 and window().height > 500,
          'actual visible window, not background Blender')
    check(st.model.object_count() == 0 and st.active_command is None, 'empty disposable startup')
    # Load validated synthetic source construction only after the real package/bootstrap.
    sys.path.insert(0, str(ROOT/'spike'))
    from test_surfacing_preparation import sources
    from test_sweep1_command_integration import verify_document
    from narwhal3d import solid_commands, kernel, native_geometry as ng
    from mathutils import Vector
    from narwhal3d.model import NarwhalModel
    from narwhal3d import curve_inputs
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    case = sources()
    original = case[2]()[1]
    # Only fixture setup reverses this curve. The command must use an explicit
    # direction flag and leave the reversed source untouched.
    check(original[2].Reverse(), 'fixture middle section initially reversed')
    model = NarwhalModel()
    labels = ('Rail', 'Zulu', 'Alpha', 'Mike')
    for i in (0, 3, 1, 2):
        model.add_object(original[i], labels[i], 0)
    by_name = {o.Attributes.Name: str(o.Attributes.Id) for o in model.file.Objects}
    ids = tuple(by_name[name] for name in labels)
    real_select = ui._select_object_hit
    def observe_select(*args, **kwargs):
        result = real_select(*args, **kwargs)
        cmd = st.active_command
        entry = dict(hit=args[0], shift=kwargs.get('shift'), ctrl=kwargs.get('ctrl'),
                     selected=ui.selection_names(),
                     picked=[p.object_id for p in getattr(getattr(cmd,'_mouse_sequence',None),'picks',[])])
        RESULT.setdefault('pick_trace',[]).append(entry)
        print('POINTER_TRACE',json.dumps(entry),flush=True)
        return result
    ui._select_object_hit = observe_select
    st.set_model(model); st.ctx.rebuild_display(); st.ctx.undo.clear(); st.mark_saved()

    SOURCE_CONTENT = {oid: model.file.Objects.FindId(oid).Geometry.Encode() for oid in ids}
    source_attributes = {oid: model.file.Objects.FindId(oid).Attributes.Encode() for oid in ids}
    RESULT['sources'] = dict(ids=list(ids), storage_order=[o.Attributes.Name for o in model.file.Objects])
    check(ng.prepare_freeform_available() and ng.sweep1_freeform_available(), 'real native preparation/build capabilities')
    area = ui._pane_by_label('Top'); space = area.spaces.active; region = ui._window_region(area)
    space.region_3d.view_location = Vector((6, 6, .6)); space.region_3d.view_distance = 23
    space.region_3d.update(); ui.set_active_pane(area); ui.set_gumball(False)
    def point(index):
        curve = model.file.Objects.FindId(ids[index]).Geometry
        p = curve.PointAt(curve.Domain.T0 + .57*(curve.Domain.T1-curve.Domain.T0))
        q = location_3d_to_region_2d(region,space.region_3d,Vector((p.X,p.Y,p.Z)))
        check(q is not None, 'source projects into actual viewport')
        return region.x+q.x,region.y+q.y
    def focus():
        bar = ui._window_region(ui._cmdbar_area())
        yield from tap('LEFTMOUSE',(bar.x+bar.width*.7,bar.y+15))
    def pick(index, modifier=None):
        if modifier: event(modifier,'PRESS'); yield .08
        yield from tap('LEFTMOUSE',point(index), shift=modifier=='LEFT_SHIFT', ctrl=modifier=='LEFT_CTRL')
        if modifier: event(modifier,'RELEASE'); yield .08
    counts = {'prepare': 0, 'build': 0}
    def instrument(name, key):
        real = getattr(ng, name)
        def call(*args, **kw):
            counts[key] += 1
            return real(*args, **kw)
        setattr(ng, name, call)
    instrument('prepare_freeform_stations','prepare')
    instrument('sweep1_freeform_approx','build')
    calls_at_start = {}
    actual_draw = ui._draw_sweep_orientation_guides
    def observe_guides(*args):
        count = actual_draw(*args)
        if count: RESULT['guide_draws'] = RESULT.get('guide_draws',0)+1
        return count
    ui._draw_sweep_orientation_guides = observe_guides
    if MODE == 'ignore-cancel':
        st.cancel = lambda: None  # Process-local negative control; no source edit.
    if MODE == 'uuid-recapture':
        real_resolve = solid_commands.Sweep1Cmd._resolve_accepted_sources
        def late_name(self, ctx, refs, **kw):
            for ref in refs:
                if isinstance(ref, curve_inputs.WholeCurvePick):
                    self._capture_source(ctx, ref.label)
                    self._accepted_sources[ref] = self._accepted_sources.get(ref.label)
            return real_resolve(self,ctx,refs,**kw)
        solid_commands.Sweep1Cmd._resolve_accepted_sources = late_name
    if MODE == 'drop-reversal':
        real_builder = ui.bridge.sweep1_freeform_from_sources
        def wrong_direction(*args, **kw):
            kw['reverse'] = [False]*3
            return real_builder(*args, **kw)
        ui.bridge.sweep1_freeform_from_sources = wrong_direction
    def start():
        calls_at_start.clear(); calls_at_start.update(counts)
        yield from focus(); yield from line('Sweep1')
        yield from pick(0)
        check(st.active_command is not None and st.active_command.step_index == 1, 'actual rail pointer advances to sections')
        check(st.active_command.args[0].object_id == ids[0], 'rail captured by document UUID at pick')
    def commit(flip=False):
        yield from tap('RET')
        if getattr(st.active_command,'_aligning',False):
            command = st.active_command
            check(counts == calls_at_start, 'no native preparation/construction before alignment confirmation')
            yield from painted('alignment','Align cross sections',shot=flip)
            check(RESULT.get('guide_draws',0)>0,'actual orientation guide draw submissions')
            if flip:
                marker = next(m for m in ui._sweep_direction_markers(region,space.region_3d,command) if m[0]==1)
                yield from tap('LEFTMOUSE',(region.x+marker[1],region.y+marker[2]))
                check(command._section_reverse == [False,True,False], 'real direction-marker click reverses only middle section')
                check(unchanged(), 'direction interaction does not mutate source curves')
                yield from painted('aligned','Align cross sections',shot=True)
                check(counts == calls_at_start, 'cached direction changes/draw do not run native construction')
            yield from tap('RET')
    if MODE == 'sort-by-name':
        real = curve_inputs.PickSequence.accepted
        curve_inputs.PickSequence.accepted = lambda self,*a: sorted(real(self,*a),key=lambda p:p.label)
        RESULT['mutation'] = 'replace accepted chronology by name sorting'
    yield from start()
    cmd = st.active_command
    for i in (1,2,3):
        yield from pick(i,'LEFT_SHIFT' if i != 1 else None)
    getter_names = [o.name for o in st.ctx.selected_objects()]
    check(getter_names != list(labels[1:]), 'Blender getter/storage order differs from pointer order')
    refs = cmd._selection_refs(st.ctx)
    check([r.object_id for r in refs] == list(ids[1:]), EXPECTED_RED)
    # Removal is explicit; reselection appends, not a name/distance sort.
    yield from pick(2,'LEFT_CTRL'); yield from pick(2,'LEFT_SHIFT')
    check([r.object_id for r in cmd._selection_refs(st.ctx)] == [ids[1],ids[3],ids[2]], 'Ctrl removal and reselection retain real chronology')
    yield from pick(3,'LEFT_CTRL'); yield from pick(3,'LEFT_SHIFT')
    check([r.object_id for r in cmd._selection_refs(st.ctx)] == list(ids[1:]), 'explicit reselection restores traversal order')
    check(model.object_count() == 4 and not st.ctx.undo.undo_names() and unchanged(), 'picks alone do not construct or mutate sources')
    # Deliberate external rename after acceptance, with legitimate prior history.
    # Selection/display mapping stays coherent; commit must use the captured UUID.
    st.ctx.begin_undo('Rename'); st.ctx.note_change('Rename')
    model.file.Objects.FindId(ids[1]).Attributes.Name = 'Renamed'
    bpy.data.objects['Zulu'].name = 'Renamed'
    model.register_blender_object(ids[1],'Renamed'); st.ctx.end_undo()
    source_attributes[ids[1]] = model.file.Objects.FindId(ids[1]).Attributes.Encode()
    history_before = st.ctx.undo.undo_names()
    check(history_before == ['Rename'], 'external rename keeps its own legitimate Undo')
    yield from painted('before_commit','cross-section',shot=True)
    yield from commit(flip=True)
    yield from wait(lambda: st.active_command is None,'mouse command finishes')
    check(model.object_count() == 5,'mouse Sweep1 publishes exactly one result')
    check(counts == {'prepare': calls_at_start['prepare']+1, 'build': calls_at_start['build']+1},
          'commit uses one preparation and one existing construction API call')
    geom = verify_document(model)
    check(kernel.recipe_of(geom)['reverse'] == [False,True,False], 'recipe retains explicit orientation flags')
    check(tuple(kernel.recipe_of(geom)['source_provenance']['ids']) == ids,'recipe/source provenance follows pointer chronology')
    check(st.ctx.undo.undo_names() == ['Sweep1']+history_before and unchanged(),'one Sweep1 Undo and unchanged source geometry')
    check(all(model.file.Objects.FindId(oid).Attributes.Encode() == attrs for oid,attrs in source_attributes.items()),'source attributes unchanged')
    yield from painted('result',shot=True)
    from narwhal3d import narwhal_document
    check(narwhal_document.save_model(model,str(OUT/'mouse.narwhal')), 'actual mouse result saved as native document')
    check(model.file.Write(str(OUT/'mouse.3dm'),8), 'actual mouse result saved as 3dm')
    yield from focus(); yield from line('Undo')
    check(model.object_count() == 4 and unchanged(),'real keyboard Undo removes only result')
    yield from line('Redo'); verify_document(model)
    check(model.object_count() == 5 and unchanged(),'real keyboard Redo restores exact interior')
    yield from painted('redo',shot=True)
    yield from line('Undo')
    # Wrong pointer order must remain wrong, not get repaired by sorting.
    yield from start()
    for i in (3,2,1): yield from pick(i,'LEFT_SHIFT' if i != 3 else None)
    yield from commit(flip=True)
    check(model.object_count() == 4 and 'endpoints in order' in '\n'.join(st.history[-4:]),'wrong pointer order is explicitly refused')
    check(st.ctx.undo.undo_names() == history_before and st.ctx.undo.redo_names() == ['Sweep1'],'wrong-order refusal preserves prior real Redo')
    # A real rectangle is a batch, never a chronology source.
    yield from start()
    global POSITION
    a=(region.x+20,region.y+20); b=(region.x+region.width-20,region.y+region.height-20)
    POSITION=tuple(map(round,a)); event('MOUSEMOVE','NOTHING'); yield .1
    event('LEFTMOUSE','PRESS'); yield .1
    POSITION=tuple(map(round,b)); event('MOUSEMOVE','NOTHING'); yield .2
    event('LEFTMOUSE','RELEASE'); yield .3
    yield from tap('RET')
    check(st.active_command is not None and 'order is ambiguous' in '\n'.join(st.history[-4:]),'real rectangle selection retains ambiguity refusal')
    yield from tap('ESC')
    check(st.active_command is None and model.object_count()==4,'real Escape discards command-local picks')
    yield from painted('batch_cancel',shot=True)
    # A CPlane edit is an invalidation, not a reason to silently reuse old picks.
    yield from start()
    for i in (1,2,3): yield from pick(i,'LEFT_SHIFT' if i != 1 else None)
    plane = ui.get_cplane(area)
    ui.set_cplane(area,ui.cplane.CPlane(origin=(1.,0.,0.),xaxis=plane.xaxis,yaxis=plane.yaxis))
    yield from tap('RET')
    check(model.object_count()==4 and 'CPlane/viewport changed' in '\n'.join(st.history[-4:]), 'CPlane change invalidates accepted mouse input')
    ui.set_cplane(area,plane)
    # Escape while alignment guides are visible must not construct anything.
    yield from start()
    for i in (1,2,3): yield from pick(i,'LEFT_SHIFT' if i != 1 else None)
    yield from tap('RET')
    check(getattr(st.active_command,'_aligning',False), 'alignment active before cancellation')
    yield from tap('ESC'); yield .2
    check(st.active_command is None and model.object_count()==4 and counts==calls_at_start,
          'alignment Escape creates no result and runs no native constructor')
    check(not ui._sweep_guide_cache, 'cancelled orientation guides are not retained/drawn')
    yield from painted('alignment_cancel',shot=True)
    yield from start()
    for i in (1,2,3): yield from pick(i,'LEFT_SHIFT' if i != 1 else None)
    yield from tap('RET')
    check(getattr(st.active_command,'_aligning',False), 'alignment active before set restoration')
    st.ctx.select_by_names([])  # Same membership, real restored-set API stamp.
    yield from tap('RET')
    check(model.object_count()==4 and counts==calls_at_start
          and 'selection changed or was restored' in '\n'.join(st.history[-4:]),
          'same-membership restored selection invalidates alignment before native work')


def tick():
    try:
        with bpy.context.temp_override(window=window()):
            return next(STEPS)
    except StopIteration:
        finish()
    except BaseException as exc:
        RESULT['traceback'] = traceback.format_exc()
        finish(str(exc))


try:
    check(Path(os.environ['HOME']).is_relative_to(OUT)
          and not Path(os.environ['NARWHAL3D_SETTINGS_FILE']).exists(), 'fresh disposable HOME/preferences')
    check('narwhal3d' not in sys.modules and not os.environ.get('NARWHAL3D_UI_READY'), 'fixture precedes actual bootstrap')
    sys.path.insert(0, str(ROOT))
    from narwhal3d import ui
    ORIGINAL_DRAW = ui._draw_text
    ui._draw_text = observe_text  # Passive observer; always forwards real drawing.
    STEPS = run()
    threading.Thread(target=lambda: (time.sleep(170), finish('170s GUI watchdog')), daemon=True).start()
    bpy.app.timers.register(tick, first_interval=.1)
except BaseException as exc:
    RESULT['traceback'] = traceback.format_exc()
    finish(str(exc))
