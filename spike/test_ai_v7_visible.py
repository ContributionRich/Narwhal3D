#!/usr/bin/env python3
"""Round06 owned visible GUI events + source-bound cfg(test) native review fixture.

Build the accepted-review fixture explicitly first. --launch opens one disposable
GUI in a loopback-only namespace; it never controls another window or stages code.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(Path(__file__).resolve().parent))


def sources(executable):
    paths=[ROOT/'narwhal3d'/name for name in ('ui.py','ai_action_review.py','ai_assistant.py','ai_primitives.py','ai_box.py','ai_presentation.py',
        'model.py','commands.py','undo.py','state.py','textfield.py','native_textfield.py','bridge.py','kernel/__init__.py','kernel/occt_backend.py')]
    paths += list((ROOT/'native/automation_core/src').glob('*.rs'))
    paths += [Path(__file__).resolve(),ROOT/'spike/test_ai_v7_roundtrip.py',ROOT/'spike/test_ai_accepted_review_native.py',executable]
    return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def launch():
    import fcntl
    import subprocess
    import tempfile
    import argparse
    from test_ai_accepted_review_native import discover_executable,_headroom,TARGET
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--launch',action='store_true')
    parser.add_argument('--target-dir',type=Path,default=TARGET)
    args=parser.parse_args()
    executable=discover_executable(args.target_dir)
    out=Path(tempfile.mkdtemp(prefix='narwhal-review06-'))
    env={k:v for k,v in os.environ.items() if k in ('PATH','DISPLAY','XAUTHORITY','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS','LANG')}
    paths=subprocess.check_output(['bash',str(ROOT/'narwhal3d.sh')],cwd=ROOT,env=dict(os.environ,NARWHAL3D_LAUNCHER_TEST='native-paths'),text=True,timeout=10)
    for row in paths.splitlines():
        key,value=row.split('=',1)
        if value:env[key]=value
    for key in ('HOME','XDG_CONFIG_HOME','XDG_CACHE_HOME','XDG_DATA_HOME','TMPDIR'):
        folder=out/key.lower();folder.mkdir();env[key]=str(folder)
    env.update(AI_REVIEW_OUT=str(out),AI_REVIEW_EXECUTABLE=str(executable),
        NARWHAL3D_SETTINGS_FILE=str(out/'preferences.json'),PYTHONDONTWRITEBYTECODE='1',
        NARWHAL3D_NO_DIALOGS='1',NARWHAL3D_DEFER_FIRST_PRESENT='1',NARWHAL3D_STATUSBAR_HEIGHT='37',
        PYTHONPATH=str(ROOT/'vendor/occt'),GHOST_BACKEND='X11',WAYLAND_DISPLAY='review06-no-wayland',
        OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',NARWHAL3D_OPEN_WORKERS='0')
    loopback=('import socket,fcntl,struct,os,sys; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); '
        'fcntl.ioctl(s,0x8914,struct.pack("16sh",b"lo",0x49)); s.close(); os.execv(sys.argv[1],sys.argv[1:])')
    command=['systemd-run','--user','--scope','--quiet','-p','MemoryMax=2048M','-p','MemorySwapMax=0',
        'timeout','--signal=TERM','--kill-after=5s','210s','bwrap','--ro-bind','/','/','--dev-bind','/dev','/dev',
        '--proc','/proc','--bind',str(out),str(out),'--unshare-net','--cap-add','CAP_NET_ADMIN','--die-with-parent',
        'python3','-c',loopback,str(ROOT/'tools/blender-patched/blender'),'--factory-startup','-t','1','-p','40','40','1440','1000',
        '--enable-event-simulate','--python-exit-code','1','--python',str(Path(__file__).resolve()),'--python',str(ROOT/'launch/bootstrap.py')]
    print('REVIEW06_EVIDENCE',out,flush=True)
    with open('/tmp/narwhal3d_gui.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);_headroom(2048)
        before=sources(executable)
        (out/'launch.json').write_text(json.dumps(dict(command=command,source_sha256=before),indent=2))
        with (out/'run.log').open('w') as log:
            result=subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=220)
        text=(out/'run.log').read_text();print(text[-9000:])
        report=json.loads((out/'result.json').read_text()) if (out/'result.json').exists() else {}
        if result.returncode==0 and report.get('status')=='passed':
            _headroom(2048)
            follow=command[:command.index(str(ROOT/'tools/blender-patched/blender'))]+[
                str(ROOT/'tools/blender-patched/blender'),'-b','--factory-startup','-t','1','--python-exit-code','1',
                '--python',str(ROOT/'spike/test_ai_v7_roundtrip.py'),'--',str(out/'result.json')]
            with (out/'roundtrip.log').open('w') as log:
                reopened=subprocess.run(follow,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=220)
            replay=(out/'roundtrip.log').read_text();print(replay[-3000:])
            report['fresh_process_roundtrip']=reopened.returncode==0 and 'REVIEW06_FRESH_ROUNDTRIP_PASS' in replay
            (out/'result.json').write_text(json.dumps(report,indent=2))
        after=sources(executable);(out/'source_check.json').write_text(json.dumps(dict(before=before,after=after,unchanged=before==after),indent=2))
        return result.returncode or int(report.get('status')!='passed' or not report.get('fresh_process_roundtrip') or before!=after or 'Error in draw callback' in text)


if '--launch' in sys.argv:
    raise SystemExit(launch())

import bpy
import traceback
import threading
import inspect
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from test_ai_accepted_review_native import NativeBridge
sys.dont_write_bytecode=True
OUT=Path(os.environ['AI_REVIEW_OUT'])
REPORT={'status':'running','checks':[],'captures':[],'requests':[],'owned_pid':os.getpid(),'clock_injections':[]}
START=time.monotonic();POSITION=(0,0);NATIVE=None;UI=None;SERVER=None;GENERATOR=None
CONSENT={'draw':0,'execute':0};BOX={'draw':0}


def check(value,label):
    REPORT['checks'].append({'ok':bool(value),'label':label});print('REVIEW06',bool(value),label,flush=True)
    if not value:raise AssertionError(label)


def finish(error=None):
    REPORT.update(status='failed' if error else 'passed',error=error,elapsed_s=time.monotonic()-START)
    if UI is not None:
        REPORT['last_ui_state']={'active_command':str(getattr(UI.app_state.active_command,'name',None)),
            'undo_names':UI.app_state.ctx.undo.undo_names(),'can_undo':UI.app_state.ctx.undo.can_undo,
            'ai_focus':UI.ai_assistant_state().focus,'command_input':getattr(UI.app_state,'input_text','')}
        UI._ai_shutdown()
    if NATIVE is not None: NATIVE.close()
    (OUT/'result.json').write_text(json.dumps(REPORT,indent=2))
    print('REVIEW06_RESULT',REPORT['status'],str(OUT),error or '',flush=True)
    sys.stdout.flush();sys.stderr.flush();os._exit(int(bool(error)))


def payload(text='Provider continuation complete.',calls=()):
    message={'role':'assistant','content':text or None}
    if calls:message['tool_calls']=[{'id':id,'type':'function','function':{'name':name,'arguments':json.dumps(args)}} for id,name,args in calls]
    return {'choices':[{'message':message,'finish_reason':'tool_calls' if calls else 'stop'}]}


PLAN_PROMPTS=('plan','enclosure','lofted_cover','pipe_plan','selected_plan','unrelated_plan')


def plan_fixture(prompt='plan'):
    def step(id,op,args,kind,refs=()):
        return dict(id=id,depends_on=list(refs),inputs=[dict(kind='step',id=r) for r in refs],action=dict(operation=op,arguments=args),
            expected=dict(kind=kind,count=1),verify=[dict(kind='object_count',count=1)]+([] if kind=='metadata' else [dict(kind='exact_geometry')])+([dict(kind='airtight')] if kind=='solid' else []))
    if prompt!='plan':
        data=dict(version=1,goal=prompt,units='Millimeters',intent='manufacturable',questions=[],steps=[])
        if prompt=='enclosure':
            data['steps']=[step('body','box',dict(origin=[0,0,0],size=[50,30,20]),'solid'),step('holes','unsupported',dict(need='Hollowing and exact hole/cutter executors are not integrated'),'solid',('body',))]
            data['questions']=['What wall thickness and hole pattern are required?']
        elif prompt=='lofted_cover':
            data['steps']=[step('section1','curve',dict(points=[[0,0,0],[0,5,5],[0,10,0]]),'curve'),step('section2','curve',dict(points=[[20,0,0],[20,5,5],[20,10,0]]),'curve'),
                step('cover','loft',dict(style='Normal'),'surface',('section1','section2')),step('closure','unsupported',dict(need='Thickness and watertight closure executors are not integrated'),'solid',('cover',))]
        elif prompt=='pipe_plan':
            data['steps']=[step('rail','curve',dict(points=[[0,0,0],[10,0,5],[20,10,5]]),'curve'),step('pipe','pipe',dict(radius=2,cap='Flat'),'solid',('rail',))]
        elif prompt=='selected_plan':
            data['steps']=[step('change','unsupported',dict(need='Selected-object modification is not integrated; selection alone is not permission'),'solid')]
            data['questions']=['Which dimensional change do you want?']
        else:
            data['intent']='advice';data['steps']=[step('inspect','read',dict(tool='narwhal.workspace',arguments={}), 'metadata')]
        return data
    return dict(version=1,goal='A 300 Millimeters dolphin visual study, not a completed solid',units='Millimeters',intent='visual_study',questions=[],steps=[
        step('sections','curve',dict(points=[[0,0,0],[100,20,30],[200,0,0]]),'curve'),
        step('fins','unsupported',dict(need='Additional section curves, lofted body, fins/tail, blend and closure executors are not integrated'),'surface',('sections',))])


class Provider(BaseHTTPRequestHandler):
    def log_message(self,*_a):pass
    def do_POST(self):
        try:
            length=int(self.headers['Content-Length']);assert length<=262144
            request=json.loads(self.rfile.read(length));REPORT['requests'].append(request)
            mode='responses' if 'input' in request else 'anthropic' if self.headers.get('anthropic-version') else 'chat'
            messages=request.get('input',request.get('messages'))
            tools=[m for m in messages if m.get('role')=='tool' or m.get('type')=='function_call_output']
            if mode=='anthropic':tools=[part for m in messages for part in (m.get('content') if isinstance(m.get('content'),list) else []) if part.get('type')=='tool_result']
            prompt=next(m['content'] for m in reversed(messages) if m.get('role')=='user' and isinstance(m.get('content'),str) and not m['content'].startswith('Untrusted workspace data'))
            continued=messages[-1].get('role')=='tool' or messages[-1].get('type')=='function_call_output' or (mode=='anthropic' and isinstance(messages[-1].get('content'),list))
            if continued:
                if prompt in PLAN_PROMPTS and not any(m.get('tool_call_id',m.get('call_id',m.get('tool_use_id')))=='plan1' for m in tools):reply=payload('',[('plan1','narwhal_plan',plan_fixture(prompt))])
                else:reply=payload()
            elif prompt=='batch':reply=payload('',[('p1','narwhal_create_sphere',{'center':[1,2,3],'radius':4}),('read1','narwhal_document_summary',{}),('p2','narwhal_create_cylinder',{'center':[20,0,0],'radius':2,'height':5}),('box1','narwhal_create_box',{'origin':[30,0,0],'size':[2,3,4]})])
            elif prompt in PLAN_PROMPTS:reply=payload('',[('workspace','narwhal_workspace',{}),('operations','narwhal_operations',{})])
            else:
                shape=prompt if prompt in ('sphere','cylinder','cone') else 'sphere'
                args={'center':[1,2,3],'radius':4}
                if shape!='sphere':args['height']=7
                reply=payload('',[('p1','narwhal_create_'+shape,args)])
            message=reply['choices'][0]['message'];calls=message.get('tool_calls',[])
            if mode=='anthropic':
                content=([{'type':'text','text':message['content']}] if message.get('content') else [])
                content += [{'type':'tool_use','id':call['id'],'name':call['function']['name'],'input':json.loads(call['function']['arguments'])} for call in calls]
                reply={'role':'assistant','content':content,'stop_reason':'tool_use' if calls else 'end_turn'}
            elif mode=='responses':
                output=[{'type':'reasoning','id':'reason-'+str(len(REPORT['requests'])),'summary':[],'encrypted_content':'visible-review-opaque'}]
                if message.get('content'):output.append({'type':'message','id':'message-'+str(len(REPORT['requests'])),'role':'assistant','status':'completed','content':[{'type':'output_text','text':message['content'],'annotations':[]}]})
                output += [{'type':'function_call','id':'item-'+call['id'],'call_id':call['id'],'name':call['function']['name'],'arguments':call['function']['arguments']} for call in calls]
                reply={'id':'response-'+str(len(REPORT['requests'])),'object':'response','status':'completed','output':output}
            data=json.dumps(reply).encode();self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        except BaseException: finish(traceback.format_exc())


def event(kind,value='PRESS',point=None,**mods):
    global POSITION
    if point is not None:POSITION=tuple(round(v) for v in point)
    bpy.context.window_manager.windows[0].event_simulate(type=kind,value=value,x=POSITION[0],y=POSITION[1],**mods)


def tap(kind,point=None,**mods):
    if point is not None:event('MOUSEMOVE','NOTHING',point);yield .05
    event(kind,'PRESS',**mods);yield .05;event(kind,'RELEASE',**mods);yield .18


def center(rect):return ((rect[0]+rect[2])/2,(rect[1]+rect[3])/2)
def button(id):
    rect=next(rect for name,rect in UI._ai['lay']['buttons'] if name==id);x,y=center(rect);rx,ry=UI._ai['region'];return x+rx,y+ry

def review_button(id):
    x,y=center(UI._ai['review_view'][id]);rx,ry=UI._ai['region'];return x+rx,y+ry


def wait(predicate,label,seconds=8):
    deadline=time.monotonic()+seconds
    while not predicate():
        if time.monotonic()>deadline:raise AssertionError('timeout: '+label)
        yield .04
    check(True,label)


def capture(name):
    path=OUT/(name+'.png');check(bpy.ops.screen.screenshot(filepath=str(path))=={'FINISHED'},name+' screenshot');REPORT['captures'].append(str(path))


def send(prompt,consent=False):
    st=UI.ai_assistant_state();st.input_text=prompt;UI.ai_assistant.field_of(st).set_text(prompt)
    consent=consent or UI.preferences.get('ai_metadata_permission')!=UI.ai_assistant.metadata_permission(st.provider)
    before=CONSENT['draw'];executed=CONSENT['execute'];yield from tap('LEFTMOUSE',button('send'))
    if consent:
        yield from wait(lambda:CONSENT['draw']>before,'real read-consent dialog drawn')
        yield from tap('RET');check(CONSENT['execute']==executed+1,'consent is a real keyboard event')
    if prompt not in PLAN_PROMPTS:yield from wait(lambda:UI._ai.get('action_pending') and UI._ai.get('review_drawn_pending') is UI._ai['action_pending'],'native accepted action visibly drawn')


def fresh():
    yield from tap('LEFTMOUSE',button('new'))
    check(UI._ai.get('action_pending') is None and UI._ai.get('review_batch') is None,'New Chat clears pending review/batch')


@contextmanager
def ui_mutant(name,old,new):
    original=getattr(UI,name);source=inspect.getsource(original)
    assert source.count(old)==1,(name,'mutation anchor changed')
    namespace=dict(UI.__dict__);exec(compile(source.replace(old,new,1),'<review-ui-mutation>','exec'),namespace)
    setattr(UI,name,namespace[name])
    try:yield
    finally:setattr(UI,name,original)


def run():
    global NATIVE,SERVER
    yield from wait(lambda:os.environ.get('NARWHAL3D_UI_READY')=='1' and UI._preferences_ready and UI.is_input_active() and len(UI._right_tab_boxes)==5,'normal owned GUI ready',50)
    check(not bpy.app.background and UI.app_state.model.object_count()==0,'real visible empty disposable document')
    NATIVE=NativeBridge(Path(os.environ['AI_REVIEW_EXECUTABLE'])).__enter__()
    UI.ai_assistant._load_workflow_library=lambda *_a,**_kw:NATIVE.library
    for cls,key,name in [(UI.NARWHAL_OT_ai_read_consent,'draw','consent'),(UI.NARWHAL_OT_ai_read_consent,'execute','consent'),(UI.NARWHAL_OT_ai_box_approval,'draw','box')]:
        def instrument(original,counter,key):
            def track(self,context):
                counter[key]+=1
                return original(self,context)
            return track
        setattr(cls,key,instrument(getattr(cls,key),CONSENT if name=='consent' else BOX,key))
    SERVER=ThreadingHTTPServer(('127.0.0.1',0),Provider);threading.Thread(target=SERVER.serve_forever,daemon=True).start()
    endpoint='http://127.0.0.1:'+str(SERVER.server_port)+'/v1'
    check(UI._apply_ai_settings(UI.ai_assistant.set_provider('openai_compatible','visible-review-fixture',endpoint)) is None,'fixture recipient configured in disposable settings')
    yield from tap('LEFTMOUSE',center(UI._right_tab_boxes[4]));yield .2
    check(UI.ai_panel_open(),'AI panel opened by actual event')
    yield from send('sphere',consent=True)
    check(UI.app_state.model.object_count()==0,'no creation before human approval')
    check(UI._ai['review_view']['fits'],'all review fields and buttons fit')
    text=' '.join(' '.join(UI._ai['review_view']['lines']).split())
    check(all(value in text for value in ('Radius: '+str(UI._ai['action_pending']['review']['action']['arguments']['radius']),'Millimeters','unavailable','OFF in normal catalogs')),'painted review contains native dimensions/units and unavailable authority')
    capture('sphere_review')
    waiting=UI._ai['action_pending'];UI.set_shell_theme('dark');yield .2
    check(UI._ai.get('action_pending') is waiting,'theme change does not authorize the review')
    capture('sphere_review_dark');UI.set_shell_theme('light');yield .2
    point=review_button('approve');yield from tap('LEFTMOUSE',point)
    yield from wait(lambda:UI.ai_assistant_state().status=='connected','canonical result continued after click')
    check(UI.app_state.model.object_count()==1,'Approve click created exactly one sphere')
    yield from tap('LEFTMOUSE',point);check(UI.app_state.model.object_count()==1,'repeated click cannot duplicate publication')
    from narwhal3d import kernel
    from narwhal3d.kernel import occt_backend as occt
    geom=UI.app_state.model.file.Objects[0].Geometry
    check(occt.face_types(kernel.master_of(geom))=={'SPHERE':1} and occt.is_airtight(kernel.master_of(geom)),'actual analytic sphere master and airtightness')
    check(UI.app_state.ctx.undo.can_undo,'publication produced an undo record')
    REPORT['undo_before']=UI.app_state.ctx.undo.undo_names()
    area=UI.active_pane_area();yield from tap('LEFTMOUSE',(area.x+area.width*.4,area.y+area.height*.4));yield .15
    event('LEFT_CTRL');yield .05;yield from tap('Z',ctrl=True);event('LEFT_CTRL','RELEASE')
    yield from wait(lambda:UI.app_state.model.object_count()==0,'actual Ctrl+Z undoes one publication')
    event('LEFT_CTRL');yield .05;yield from tap('Y',ctrl=True);event('LEFT_CTRL','RELEASE')
    yield from wait(lambda:UI.app_state.model.object_count()==1,'actual Ctrl+Y restores publication')
    capture('sphere_published')
    for prompt in ('cylinder','cone'):
        yield from fresh();before=UI.app_state.model.object_count();yield from send(prompt)
        yield from tap('LEFTMOUSE',review_button('approve'))
        yield from wait(lambda:UI.ai_assistant_state().status=='connected',prompt+' canonical continuation')
        check(UI.app_state.model.object_count()==before+1,prompt+' visible publication')
        geometry=UI.app_state.model.file.Objects[before].Geometry;master=kernel.master_of(geometry)
        faces={'CYLINDER':1,'PLANE':2} if prompt=='cylinder' else {'CONE':1,'PLANE':1}
        import math
        volume=math.pi*16*7/(3 if prompt=='cone' else 1)
        check(occt.face_types(master)==faces and occt.is_airtight(master),prompt+' exact authoritative faces/caps')
        check(abs(occt.volume(master)-volume)<1e-8,prompt+' exact volume')
        bounds=occt.tight_bbox(master)
        check(all(abs(a-b)<1e-8 for row,expected in zip(bounds,((-3,-2,3),(5,6,10))) for a,b in zip(row,expected)),prompt+' world dimensions')
    yield from fresh();before=UI.app_state.model.object_count();yield from send('reject')
    yield from tap('LEFTMOUSE',review_button('reject'));yield from wait(lambda:UI.ai_assistant_state().status=='connected','Reject canonical continuation')
    check(UI.app_state.model.object_count()==before,'Reject created nothing')
    yield from fresh();yield from send('settings_wait');pending=UI._ai['action_pending']
    from narwhal3d import ai_settings_popup
    yield from tap('LEFTMOUSE',button('settings'));yield from wait(ai_settings_popup.is_open,'Settings opens while review waits')
    yield from tap('ESC');yield from wait(lambda:not ai_settings_popup.is_open(),'Escape closes Settings without a decision')
    check(UI._ai.get('action_pending') is pending,'closing Settings did not approve or reject')
    yield from tap('LEFTMOUSE',center(UI._right_tab_boxes[0]));yield .2
    check(UI._ai.get('action_pending') is pending,'tab blur retains pending review without approval')
    yield from tap('LEFTMOUSE',center(UI._right_tab_boxes[4]));yield .2
    yield from tap('ESC');yield from wait(lambda:UI.ai_assistant_state().status=='connected','Escape in review rejects via native result')
    yield from fresh();yield from send('changed_pins');old_tol=UI.app_state.model.file.Settings.ModelAbsoluteTolerance
    UI.app_state.model.file.Settings.ModelAbsoluteTolerance=old_tol*2
    yield from wait(lambda:UI.ai_assistant_state().status=='connected','changed document tolerance invalidates waiting review')
    check(UI.app_state.model.object_count()==before,'changed pins cannot publish')
    UI.app_state.model.file.Settings.ModelAbsoluteTolerance=old_tol
    yield from fresh();yield from send('changed_recipient');session=UI.ai_assistant_state().extra['workflow']
    check(UI._apply_ai_settings(UI.ai_assistant.set_provider('openai_compatible','other-model',endpoint)) is None,'trusted settings API changes pending recipient configuration')
    check(session._handle==0 and UI._ai.get('action_pending') is None,'configuration change retires visible review')
    check(UI._apply_ai_settings(UI.ai_assistant.set_provider('openai_compatible','visible-review-fixture',endpoint)) is None,'restore fixture configuration')
    yield from fresh();yield from send('changed_document');session=UI.ai_assistant_state().extra['workflow'];old_model=UI.app_state.model
    UI.app_state.model=type(old_model)()
    yield from wait(lambda:session._handle==0,'document replacement closes old review')
    UI.app_state.model=old_model
    check(UI._ai.get('action_pending') is None,'no old review remains after document replacement')
    yield from fresh();yield from send('revoke');session=UI.ai_assistant_state().extra['workflow']
    UI._ai_forget_metadata_permission()
    check(session._handle==0 and UI._ai.get('action_pending') is None,'read-consent revocation retires review')
    yield from fresh();yield from send('expire');pending=UI._ai['action_pending']
    NATIVE.rpc(pending['batch']['workflow']._handle,{'op':'fixture_advance_review_ms','delta_ms':300001});REPORT['clock_injections'].append('review expiry only')
    yield from wait(lambda:UI.ai_assistant_state().status=='connected','native fake-clock expiry clears visible review and continues')
    check(UI.app_state.model.object_count()==before,'expiry created nothing')
    yield from fresh();yield from send('cancel');old=UI._ai['action_pending'];yield from tap('LEFTMOUSE',button('send'))
    check(UI._ai.get('action_pending') is None and UI.ai_assistant_state().extra.get('workflow') is None,'Cancel button retires review and native session')
    check(UI._ai_decide_action_review(True,old) is False,'injected stale callback cannot act')
    yield from fresh();yield from send('new');old=UI._ai['action_pending'];yield from fresh()
    check(UI._ai_decide_action_review(True,old) is False,'New Chat rejects queued old decision')
    yield from send('batch');before=UI.app_state.model.object_count()
    first=UI._ai['action_pending'];yield from tap('LEFTMOUSE',review_button('approve'))
    yield from wait(lambda:UI._ai.get('action_pending') is not None and UI._ai['action_pending'] is not first,'second sibling separately reviewed')
    calls=NATIVE.messages
    check(UI._ai_decide_action_review(True,first) is False,'old sibling callback cannot approve new sibling')
    check(NATIVE.messages==calls,'stale sibling causes no backend traffic')
    with ui_mutant('_ai_decide_action_review',"if pending is None or pending is not _ai.get('action_pending') or pending['deciding']:","if pending is None:"):
        calls=NATIVE.messages;UI._ai_decide_action_review(True,first)
        check(NATIVE.messages>calls,'MUTATION RED: stale event reached native before rejection')
    calls=NATIVE.messages;check(UI._ai_decide_action_review(True,first) is False and NATIVE.messages==calls,'stale-event guard restored GREEN')
    yield .2;box_draws=BOX['draw'];yield from tap('LEFTMOUSE',review_button('reject'))
    yield from wait(lambda:BOX['draw']>box_draws,'existing Box dialog drawn in mixed batch')
    yield from tap('RET');yield from wait(lambda:UI.ai_assistant_state().status=='connected','mixed Box/read/primitive canonical continuation')
    check(UI.app_state.model.object_count()==before+2,'batch creates only separately approved sphere and Box')
    yield from fresh();yield from send('plan');yield from wait(lambda:UI.ai_assistant_state().status=='connected','complex CAD plan continued')
    check(any('PLAN ONLY' in m.text and 'not integrated' in m.text for m in UI.ai_assistant_state().messages),'native blocked plan presented separately from prose')
    capture('dolphin_plan')
    check(UI._ai.get('action_pending') is None,'plan does not create an approval')
    for prompt in PLAN_PROMPTS[1:]:
        yield from fresh();before=UI.app_state.model.object_count();yield from send(prompt)
        yield from wait(lambda:UI.ai_assistant_state().status=='connected',prompt+' native plan continuation')
        check(any('PLAN ONLY' in m.text and 'not been checked' in m.text for m in UI.ai_assistant_state().messages),prompt+' truthful verification limits shown')
        check(UI.app_state.model.object_count()==before and UI._ai.get('action_pending') is None,prompt+' is not execution or approval')
        capture(prompt)
    # Publication failures are exercised through real review clicks, not direct decisions.
    yield from fresh();yield from send('lost_receipt');before=UI.app_state.model.object_count()
    original=UI.ai_assistant.WorkflowSession.record_review_publication
    def lost(*_a,**_kw):raise UI.ai_assistant.WorkflowUnavailable('fixture_lost_receipt')
    UI.ai_assistant.WorkflowSession.record_review_publication=lost
    try:
        yield from tap('LEFTMOUSE',review_button('approve'))
        yield from wait(lambda:UI.ai_assistant_state().status=='error','lost receipt produces explicit unavailable outcome')
    finally:UI.ai_assistant.WorkflowSession.record_review_publication=original
    check(UI.app_state.model.object_count()==before+1,'lost receipt does not imply no object exists')
    check('outcome unavailable' in UI.ai_assistant_state().error,'lost receipt is not fabricated success')
    yield from fresh();yield from send('display_failure');before=UI.app_state.model.object_count()
    before_ids={str(obj.Attributes.Id) for obj in UI.app_state.model.file.Objects}
    ctx=UI.app_state.ctx;original=ctx._make_display_object
    def failed_display(*_a,**_kw):raise RuntimeError('owned test display failure')
    ctx._make_display_object=failed_display
    try:
        yield from tap('LEFTMOUSE',review_button('approve'))
        yield from wait(lambda:UI.ai_assistant_state().status=='connected','failed publication has canonical continuation')
    finally:ctx._make_display_object=original
    check(UI.app_state.model.object_count()==before,'scoped rollback removes only failed action')
    check({str(obj.Attributes.Id) for obj in UI.app_state.model.file.Objects}==before_ids,'rollback preserves all pre-existing object UUIDs')
    check(any('UNKNOWN' in m.text for m in UI.ai_assistant_state().messages),'rollback attempt is not false certainty')
    # Mutant can no longer clear the visible pending review on a real New Chat event.
    yield from fresh();yield from send('mutate_cleanup')
    with ui_mutant('_ai_invalidate','"action_pending", ',''):
        yield from tap('LEFTMOUSE',button('new'))
        check(UI._ai.get('action_pending') is not None,'MUTATION RED: visible review retained after New Chat')
    UI._ai_invalidate('test_cleanup')
    yield from fresh();yield from send('restored_cleanup');yield from fresh()
    check(UI._ai.get('action_pending') is None,'New Chat cleanup restored GREEN')
    # Canonical result recording cannot be skipped merely to start another turn.
    yield from send('mutate_result')
    with ui_mutant('_ai_finish_action_review',"batch['results'][pending['index']] = result", "batch['results'][pending['index']] = {'call_id':result['call_id'],'output':{'created':True},'is_error':False}"):
        yield from tap('LEFTMOUSE',review_button('approve'))
        yield from wait(lambda:UI.ai_assistant_state().status=='error','MUTATION RED: forged canonical result refused')
    yield from fresh();yield from send('restored_result');yield from tap('LEFTMOUSE',review_button('approve'))
    yield from wait(lambda:UI.ai_assistant_state().status=='connected','canonical continuation restored GREEN')
    os.environ['TEST_KEY']='local-test-only'
    for provider in ('anthropic','openai_responses'):
        yield from fresh()
        check(UI._apply_ai_settings(UI.ai_assistant.set_provider(provider,'visible-fixture',endpoint,credential_env='TEST_KEY')) is None,provider+' local fixture configured')
        before=UI.app_state.model.object_count();yield from send('sphere')
        yield from tap('LEFTMOUSE',review_button('approve'))
        yield from wait(lambda:UI.ai_assistant_state().status=='connected',provider+' real visible approval/continuation')
        check(UI.app_state.model.object_count()==before+1,provider+' one approved object')
        if provider=='openai_responses':
            wire=REPORT['requests'][-1]['input']
            check(any(item.get('encrypted_content')=='visible-review-opaque' for item in wire),'Responses opaque replay retained through human wait')
            check(sum(item.get('type')=='function_call_output' for item in wire)==1,'Responses has exactly one canonical result')
        capture(provider+'_review_result')
    saved=OUT/'visible_actions.3dm';check(UI.app_state.model.save(str(saved)),'visible-created geometry saved')
    check(not UI.app_state.model.last_save_warnings,'save has no faceting warning')
    REPORT['saved_model']=str(saved)
    REPORT['saved_geometry']={str(obj.Attributes.Id):{'kind':kernel.recipe_of(obj.Geometry)['kind'],
        'faces':occt.face_types(kernel.master_of(obj.Geometry)),'volume':occt.volume(kernel.master_of(obj.Geometry)),
        'bounds':occt.tight_bbox(kernel.master_of(obj.Geometry))} for obj in UI.app_state.model.file.Objects}
    yield from fresh();yield from send('shutdown_pending');handle=UI.ai_assistant_state().extra['workflow']._handle
    UI._ai_shutdown()
    check(UI._ai.get('action_pending') is None and UI.ai_assistant_state().extra.get('workflow') is None,'UI teardown hook clears pending review')
    check(not bpy.app.timers.is_registered(UI._ai_poll_transport),'UI teardown unregisters review/transport polling')
    check(NATIVE.rpc(handle,{'op':'state'})['code']=='unknown_session','UI teardown closed actual native session')
    REPORT['shutdown_evidence']='direct application shutdown hook while visible review pending; not an OS close-button event'
    REPORT['native_messages']=NATIVE.messages
    finish()


def tick():
    global UI,GENERATOR
    try:
        if UI is None:
            UI=sys.modules.get('narwhal3d.ui')
            if UI is None:return .1
            GENERATOR=run()
        return next(GENERATOR)
    except StopIteration:return None
    except BaseException:finish(traceback.format_exc())


bpy.app.timers.register(tick,first_interval=.2)
