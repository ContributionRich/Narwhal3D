"""Real Library panel capture, insertion, drag cancellation and Undo smoke test.

Run python3 -B spike/test_library_gui.py --launch. Disposable settings/library;
actual Blender event loop and native store. Default fixture is a shaded box;
LIBRARY_GUI_ASSET=curve also exercises curve capture. No owner document is opened.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'spike'))

if '--launch' in sys.argv:
    import fcntl
    import subprocess
    import memory_bound
    import runtime_paths
    out=Path(tempfile.mkdtemp(prefix='library-gui-'))
    env=os.environ.copy()
    env.setdefault('LIBRARY_GUI_ASSET','box')
    for k in ('NARWHAL3D_UI_READY','NARWHAL3D_WELCOME_REQUEST'): env.pop(k,None)
    data=subprocess.check_output(['bash',str(ROOT/'narwhal3d.sh')],cwd=ROOT,
            env=dict(env,NARWHAL3D_LAUNCHER_TEST='native-paths'),text=True,timeout=10)
    for row in data.splitlines():
        k,v=row.split('=',1)
        if v: env[k]=v
    for key in ('HOME','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_CACHE_HOME','XDG_STATE_HOME'):
        p=out/key;p.mkdir();env[key]=str(p)
    env.update(NARWHAL3D_SETTINGS_FILE=str(out/'prefs.json'),NARWHAL_LIBRARY_ROOT=str(out/'library'),
               LIBRARY_GUI_OUTPUT=str(out),NARWHAL3D_NO_DIALOGS='1',PYTHONDONTWRITEBYTECODE='1',
               PYTHONPATH=str(ROOT/'vendor/occt'),NARWHAL3D_DEFER_FIRST_PRESENT='1')
    with open('/var/tmp/narwhal3d_gui.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        with (out/'run.log').open('w') as log:
            proc=subprocess.run([*memory_bound.prefix('3G'),'timeout','--kill-after=5s','150s',
                str(runtime_paths.blender(ROOT)),'--enable-event-simulate','--factory-startup','--window-geometry','0','0','1500','950',
                '--python-exit-code','1','--python',str(Path(__file__).resolve()),
                '--python',str(ROOT/'launch/bootstrap.py')],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=160)
    print((out/'run.log').read_text())
    print('LIBRARY_GUI_OUTPUT',out)
    result=json.loads((out/'result.json').read_text()) if (out/'result.json').exists() else {'error':'No result'}
    print(json.dumps(result,indent=2))
    raise SystemExit(0 if proc.returncode==0 and result.get('status')=='passed' else 1)

import threading
import traceback
import bpy
sys.path.insert(0,str(ROOT))
from narwhal3d import ui,bridge,library_panel as panel
OUT=Path(os.environ['LIBRARY_GUI_OUTPUT'])
checks=[]

def check(ok,label):
    checks.append((bool(ok),label))
    print('LIBRARY_GUI',bool(ok),label,flush=True)
    if not ok: raise AssertionError(label)

def finish(error=None):
    result=dict(status='failed' if error else 'passed',checks=checks,error=error)
    (OUT/'result.json').write_text(json.dumps(result,indent=2));sys.stdout.flush();os._exit(int(bool(error)))

def window(): return bpy.context.window_manager.windows[0]
def center(box): return round((box[0]+box[2])/2),round((box[1]+box[3])/2)
def mouse(kind,value,point): window().event_simulate(type=kind,value=value,x=point[0],y=point[1])
def tap(point):
    mouse('MOUSEMOVE','NOTHING',point);yield .1
    mouse('LEFTMOUSE','PRESS',point);yield .1
    mouse('LEFTMOUSE','RELEASE',point);yield .2

def run():
    deadline=time.monotonic()+45
    while not (os.environ.get('NARWHAL3D_UI_READY')=='1' and not ui._viewports_in_progress and ui.is_input_active()):
        if time.monotonic()>deadline: raise RuntimeError('Bootstrap timeout')
        yield .15
    st=ui.app_state
    names=ui.rightpanel.tab_names()
    check(names==('Layers','Properties','Display','Bug Report','Library','AI Assistant'),'approved six-tab order')
    fixture_geometry = (bridge.make_box((0,0,0),(10,10,0),10) if os.environ.get('LIBRARY_GUI_ASSET')=='box'
                        else bridge.make_circle((0,0,0),5))
    _id,name=st.ctx.add_geometry(fixture_geometry,'LibraryAsset')
    st.ctx.select_by_names([name])
    baseline=st.model.object_count();before_undo=list(st.ctx.undo.undo_names())
    yield from tap(center(ui._right_tab_boxes[names.index('Library')]))
    yield .3
    check(bool(panel._hits) and panel._loaded,'native Library panel draws')
    check(not panel._catalog['assets'],'fresh library has no demo assets')
    # Editor state is fixture setup; submission itself is a real button event.
    panel.open_form('capture');panel._form['field'].insert('Reusable Curve')
    ui._tag_redraw_bands();yield .3
    bpy.ops.screen.screenshot(filepath=str(OUT/'add-asset-editor.png'))
    yield from tap(center(panel._hits['category_add']))
    check(panel._form['mode']=='create_category','New category opens its editor from the add-asset form')
    panel._form['field'].insert('Workshop')
    ui._tag_redraw_bands();yield .2
    yield from tap(center(panel._hits['submit']))
    while panel._busy: yield .15
    check(panel._form and panel._form['mode']=='capture' and panel._form['field'].text=='Reusable Curve',
          'creating a category returns to the preserved asset draft')
    check(any(c['name']=='Workshop' for c in panel._catalog['categories']),'new category persists in native catalogue')
    heartbeat = {'active':True, 'ticks':0}
    def capture_heartbeat():
        if not heartbeat['active']: return None
        if panel._native_job is not None and not panel._native_job['done']:
            heartbeat['ticks'] += 1
        return .02
    bpy.app.timers.register(capture_heartbeat,first_interval=.01)
    yield from tap(center(panel._hits['submit']))
    deadline=time.monotonic()+50
    while panel._busy:
        if time.monotonic()>deadline: raise RuntimeError('Capture timeout')
        yield .2
    yield .3
    heartbeat['active'] = False
    check(heartbeat['ticks']>=5,f'UI event loop keeps running during exact capture validation ({heartbeat["ticks"]} ticks)')
    check(len(panel._catalog['assets'])==1,'real selection captured by native store')
    asset=panel._catalog['assets'][0]
    check(Path(asset['thumbnail']).read_bytes().startswith(b'\x89PNG'),'real thumbnail persisted')
    check(st.model.object_count()==baseline and list(st.ctx.undo.undo_names())==before_undo,'capture leaves document and Undo unchanged')
    original_thumbnail = asset['thumbnail']
    original_source = Path(asset['source_file']).read_bytes()
    tile_point = center(panel._tiles[0][1])
    mouse('RIGHTMOUSE','PRESS',tile_point);yield .1
    mouse('RIGHTMOUSE','RELEASE',tile_point);yield .1
    preview_menu = panel.shell_popup._model.panels[0]
    preview_index = next(i for i,row in enumerate(preview_menu.rows) if row['label']=='Refresh preview')
    yield from tap(center(preview_menu.row_rects[preview_index]))
    while panel._busy: yield .15
    asset = panel._catalog['assets'][0]
    check(asset['thumbnail']!=original_thumbnail,'real Refresh preview menu regenerates existing asset thumbnail')
    check(Path(asset['source_file']).read_bytes()==original_source and list(st.ctx.undo.undo_names())==before_undo,'refresh preserves exact source and document Undo')

    check(bool(panel._tiles),'visual asset tile drawn')
    # Hit boxes alone cannot establish visible output: a wrong scissor previously
    # allowed the whole test to click invisible tiles successfully.
    mouse('MOUSEMOVE','NOTHING',center(ui._right_tab_boxes[names.index('Library')]));yield .2
    shot = OUT/'visible-tile.png'
    original_image = panel._image
    try:
        panel._image = lambda *_args: None
        ui._tag_redraw_bands();yield .2
        bpy.ops.screen.screenshot(filepath=str(OUT/'without-thumbnail.png'))
    finally:
        panel._image = original_image
        ui._tag_redraw_bands()
    yield .2
    bpy.ops.screen.screenshot(filepath=str(shot))
    image = bpy.data.images.load(str(shot), check_existing=False)
    try:
        width, height = image.size
        pixels = list(image.pixels)
        bounds = panel._tiles[0][1]
        x0, y0, x1, y1 = map(int, bounds)
        colors = set()
        for y in range(y0+int(35*ui._ui_scale()), y1-5, 2):
            for x in range(x0+5, x1-5, 2):
                index = (y*width+x)*4
                colors.add(tuple(round(v*255) for v in pixels[index:index+3]))
        check(len(colors)>12, 'framebuffer contains visible thumbnail detail, not an invisible hit box')
        if os.environ.get('LIBRARY_GUI_ASSET')=='box':
            thumbnail = bpy.data.images.load(asset['thumbnail'],check_existing=False)
            try:
                source_pixels = list(thumbnail.pixels)
                tw, th = thumbnail.size
                scale = ui._ui_scale()
                art = (bounds[0]+4*scale,bounds[1]+34*scale,bounds[2]-4*scale,bounds[3]-4*scale)
                errors = []
                for u in (.2,.35,.5,.65,.8):
                    for v in (.3,.45,.6,.75,.85):
                        sx, sy = int(u*tw), int(v*th)
                        si = (sy*tw+sx)*4
                        if source_pixels[si+3]<.99: continue
                        expected = source_pixels[si:si+3]
                        neighbours = [(yy*tw+xx)*4 for yy in (sy-3,sy+3) for xx in (sx-3,sx+3)]
                        if any(max(abs(source_pixels[i+c]-expected[c]) for c in range(3))>.025 for i in neighbours): continue
                        x, y = round(art[0]+u*(art[2]-art[0])),round(art[1]+v*(art[3]-art[1]))
                        actual = pixels[(y*width+x)*4:(y*width+x)*4+3]
                        errors.append(max(abs(actual[c]-expected[c]) for c in range(3)))
                check(len(errors)>=10 and sum(errors)/len(errors)<.035,
                      f'visible shaded box matches saved PNG colour (samples={len(errors)}, mean linear error={sum(errors)/max(1,len(errors)):.4f})')
            finally:
                bpy.data.images.remove(thumbnail)
        blank = bpy.data.images.load(str(OUT/'without-thumbnail.png'), check_existing=False)
        try:
            blank_pixels = list(blank.pixels)
            changed = 0
            for y in range(y0+int(35*ui._ui_scale()), y1-5):
                for x in range(x0+5, x1-5):
                    index = (y*width+x)*4
                    if max(abs(pixels[index+c]-blank_pixels[index+c]) for c in range(3)) > .04:
                        changed += 1
            check(changed>20, 'actual thumbnail changes visible framebuffer pixels independently of tile decoration')
        finally:
            bpy.data.images.remove(blank)
    finally:
        bpy.data.images.remove(image)
    mouse('MOUSEMOVE','NOTHING',center(panel._tiles[0][1]))
    deadline = time.monotonic()+2
    while panel._hover_states.get(asset['id'],0)==0 and time.monotonic()<deadline: yield .02
    check(panel._hover is not None and 0<panel._hover_states.get(asset['id'],0)<9,
          f'real hover has intermediate animation frames instead of switching instantly: hover={bool(panel._hover)}, frames={panel._hover_states}')
    check(not panel._tooltip_ready and panel._tooltip_box is None,'name tooltip is delayed, not flashed immediately')
    bpy.ops.screen.screenshot(filepath=str(OUT/'hover-transition.png'))
    deadline = time.monotonic()+2
    while panel._hover_states.get(asset['id'])!=9 and time.monotonic()<deadline: yield .02
    check(panel._hover_states.get(asset['id'])==9,'hover lift and fade reach their finished state')
    deadline = time.monotonic()+2
    while panel._tooltip_box is None and time.monotonic()<deadline: yield .02
    check(panel._tooltip_ready and panel._tooltip_box is not None,'settled hover visibly shows the full-name/category tooltip')
    bpy.ops.screen.screenshot(filepath=str(OUT/'hover.png'))
    hovered_image = bpy.data.images.load(str(OUT/'hover.png'),check_existing=False)
    try:
        x = round(bounds[2]+2*ui._ui_scale())
        y = round(bounds[3]-20*ui._ui_scale())
        index = (y*width+x)*4
        actual = hovered_image.pixels[index:index+3]
        background = pixels[index:index+3]
        shadow_change = max(abs(actual[c]-background[c]) for c in range(3))
        check(.001<shadow_change<.15,'hover shadow is softly alpha-blended, not an opaque blue block')
    finally:
        bpy.data.images.remove(hovered_image)
    mouse('MOUSEMOVE','NOTHING',center(ui._right_tab_boxes[names.index('Library')]))
    deadline = time.monotonic()+2
    while panel._hover_states.get(asset['id'])==9 and time.monotonic()<deadline: yield .02
    check(panel._tooltip_box is None and not panel._tooltip_ready,'name tooltip disappears on leaving the tile')
    check(0<panel._hover_states.get(asset['id'],0)<9,'hover fades out smoothly on pointer leave')
    yield .25
    check(not panel._hover_states and not bpy.app.timers.is_registered(panel._hover_tick),'hover timer stops when idle')

    package=OUT/'captured.narwhallib'
    panel._background(lambda: panel.store().export_library(package), 'Test export completed')
    check(panel._busy,'native package transfer runs off the event thread')
    while panel._busy: yield .1
    check(package.is_file() and st.model.object_count()==baseline,'background export completes without changing geometry')
    from narwhal3d import library_geometry, file_commands
    library_geometry.clear_asset_cache()
    cold_load_ticks = {'active':True,'ticks':0}
    def load_heartbeat():
        if not cold_load_ticks['active']: return None
        if panel._asset_loading: cold_load_ticks['ticks'] += 1
        return .02
    bpy.app.timers.register(load_heartbeat,first_interval=.01)
    yield from tap(center(panel._tiles[0][1]))
    while panel._busy: yield .15
    check(cold_load_ticks['ticks']>=5,f'cold asset load keeps real UI alive ({cold_load_ticks["ticks"]} ticks)')
    yield .3
    check(st.model.object_count()==baseline+1,'real tile click inserts an independent copy')
    check(len(st.ctx.undo.undo_names())==len(before_undo)+1 and 'Insert library asset' in st.ctx.undo.undo_names(),f'insertion uses one existing Undo record: {st.ctx.undo.undo_names()}')
    check(st.ctx.undo_last()=='Insert library asset' and st.model.object_count()==baseline,'Undo removes inserted copy only')
    check(st.ctx.redo_last()=='Insert library asset' and st.model.object_count()==baseline+1,'Redo restores inserted copy')
    before=st.model.object_count()
    start=center(panel._tiles[0][1]);top=ui._pane_by_label('Top');r=ui._window_region(top)
    end=(r.x+r.width//2,r.y+r.height//2)
    mouse('LEFTMOUSE','PRESS',start);yield .1
    mouse('MOUSEMOVE','NOTHING',end);yield .2
    check(panel._drag is not None and panel._drag['moved'],'real drag shows placement ghost')
    window().event_simulate(type='ESC',value='PRESS',x=end[0],y=end[1]);yield .2
    check(panel._drag is None and st.model.object_count()==before,'Escape cancels without document mutation')
    mouse('LEFTMOUSE','RELEASE',end);yield .2
    library_geometry.clear_asset_cache()
    ticks_before_drop = cold_load_ticks['ticks']
    mouse('LEFTMOUSE','PRESS',start);yield .1
    mouse('MOUSEMOVE','NOTHING',end);yield .2
    mouse('LEFTMOUSE','RELEASE',end);yield .2
    while panel._busy: yield .15
    check(cold_load_ticks['ticks']-ticks_before_drop>=5,'cold drag/drop loads in background without freezing UI')
    cold_load_ticks['active'] = False
    check(st.model.object_count()==before+1,'real viewport drop places an asset')
    yield from tap(center(ui._right_tab_boxes[names.index('AI Assistant')]))
    check(ui.right_tab_index()==names.index('AI Assistant'),'Library does not trap rail events')
    yield from tap(center(ui._right_tab_boxes[names.index('Library')]))
    bpy.ops.screen.screenshot(filepath=str(OUT/'library.png'))
    check(not panel._busy,'no pending Library operation')
    # Open the real tile menu and removal editor, not a synthetic action call.
    tile_point = center(panel._tiles[0][1])
    mouse('RIGHTMOUSE','PRESS',tile_point);yield .15
    mouse('RIGHTMOUSE','RELEASE',tile_point);yield .15
    menu = panel.shell_popup._model.panels[0]
    check(menu.rows[-1].get('danger') is True,'removal menu carries destructive styling')
    yield from tap(center(menu.row_rects[-1]))
    yield .2
    check(panel._form and panel._form['mode']=='remove_asset','real menu opens removal confirmation editor')
    check('search' not in panel._hits and 'category' not in panel._hits,'removal editor has no irrelevant editable name/category controls')
    bpy.ops.screen.screenshot(filepath=str(OUT/'remove-confirmation.png'))
    image = bpy.data.images.load(str(OUT/'remove-confirmation.png'),check_existing=False)
    try:
        bounds = panel._hits['submit']
        x, y = int(bounds[0]+5), int((bounds[1]+bounds[3])/2)
        index = (y*image.size[0]+x)*4
        red, green, blue = image.pixels[index:index+3]
        check(red>green*1.5 and red>blue*1.3,'destructive confirmation button is visibly red in framebuffer')
    finally:
        bpy.data.images.remove(image)
    unchanged = st.model.object_count()
    yield from tap(center(panel._hits['cancel']))
    check(panel._form is None and len(panel._catalog['assets'])==1,'Cancel preserves library entry')
    mouse('RIGHTMOUSE','PRESS',tile_point);yield .1
    mouse('RIGHTMOUSE','RELEASE',tile_point);yield .1
    yield from tap(center(panel.shell_popup._model.panels[0].row_rects[-1]))
    yield from tap(center(panel._hits['submit']))
    while panel._busy: yield .15
    check(not panel._catalog['assets'] and st.model.object_count()==unchanged,'confirmed removal leaves inserted model copies intact')
    yield from tap(center(ui._right_tab_boxes[names.index('Layers')]))
    panel.close(); yield .2
    check(not panel._textures and not panel._pending_images,'owned preview images are released without touching document objects')
    previous_model = st.model
    st.mark_saved()
    message, finished = file_commands.OpenCmd()._open(asset['source_file'])
    check(finished and isinstance(message,str) and message.startswith('Loading') and st.model is previous_model,
          'Open returns immediately and keeps the old scene during file admission')
    ticks = 0
    while file_commands._load_host.polling:
        ticks += 1
        yield .02
    check(ticks>=5 and st.model is not previous_model and st.model.object_count()==1,
          'actual background Open installs a fully admitted document while UI keeps ticking')
    check(st.doc_path==asset['source_file'] and not st.doc_modified() and not st.ctx.undo.undo_names(),
          'background Open preserves path, saved state and the single fresh Undo history')


STEPS=run()
def tick():
    try:
        with bpy.context.temp_override(window=window()): return next(STEPS)
    except StopIteration: finish()
    except BaseException: finish(traceback.format_exc())
bpy.app.timers.register(tick,first_interval=.5)
