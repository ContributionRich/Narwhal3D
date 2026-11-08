"""Isolated native lifetime qualification; no installed swap or live UI hooks.

Default fixture is generated exact CAD without artwork. --fixture is optional.
Select a qualified candidate with --library and optional --receipt.
This deliberately tests the original host APIs, including copies that bypass Python
hooks. Product integration and fail-closed receipt admission have separate tests.
"""
import argparse,gc,hashlib,json,os,subprocess,sys,tempfile,threading,time,weakref
from pathlib import Path
from lifetime_fixture import (bootstrap, observer_arguments, admitted_observer,
                              make_file, target_object, child_observer_arguments)
P=argparse.ArgumentParser();observer_arguments(P)
P.add_argument('--fixture');P.add_argument('--cycles',type=int,default=200)
P.add_argument('--mutation',default='');P.add_argument('--output',default=None)
P.add_argument('--shutdown-child',action='store_true');P.add_argument('--unload-child',action='store_true')
P.add_argument('--roundtrip-child');a=P.parse_args()
if a.cycles < 2: P.error('--cycles must be at least 2')
if a.output is None: a.output=tempfile.mkdtemp(prefix='native-master-lifetime-')
Path(a.output).mkdir(parents=True,exist_ok=True)
bootstrap()
import rhino3dm as r
checks=[];serial=0;WORKLOAD=False

def check(ok,label):
    checks.append({'label':label,'ok':bool(ok)});print(('PASS ' if ok else 'FAIL ')+label,flush=True)
    if not ok:raise AssertionError(label)

observer,n=admitted_observer(a,check)
if observer is None: raise SystemExit(0)
from narwhal3d import bridge,kernel
from narwhal3d.kernel import occt_backend as occt
ORIGINAL_DUPLICATE=r.GeometryBase.Duplicate
def new_fixture():
    f=make_file(a.fixture)
    g=target_object(f,n.file_owner).Geometry
    check(kernel.master_of(g) is not None or bridge.rehydrate_master(g),
          'complex fixture exact master reconstructed')
    return f,g

if a.roundtrip_child:
    f=r.File3dm.Read(a.roundtrip_child)
    g=target_object(f,n.file_owner).Geometry
    check(n.lease_count(g)==0,'fresh document read has no native token')
    check(kernel.master_of(g) is None,'fresh process has no inherited registry master')
    check(bridge.rehydrate_master(g),'fresh process reconstructs exact master from recipe')
    shape=kernel.master_of(g)
    check(occt.is_airtight(shape),'fresh recipe reconstruction is airtight')
    print('ROUNDTRIP_EXACT',json.dumps({'faces':occt.face_types(shape),'volume':occt.volume(shape)}),flush=True)
    raise SystemExit(0)

def geometry(f,oid):
    o=f.Objects.FindId(oid);n.file_owner(o);return o.Geometry

def bind(g):
    key=kernel.master_key_of(g);check(kernel.master_of(g) is not None,'binding has an exact master')
    n.lease_bind(g,key);return key

def collect():
    gc.collect();released=n.lease_drain()
    if a.mutation!='unconditional-retention' or not WORKLOAD:
        for key in released:kernel._MASTERS.pop(key,None)
    return released

def exact(g,expected):
    shape=kernel.master_of(g)
    check(shape is not None,'legitimate native owner still resolves exact master')
    check(occt.is_airtight(shape),'retained complex master is airtight')
    check(occt.face_types(shape)==expected,'retained analytic face types unchanged')
    return shape

if a.unload_child:
    import ctypes,_ctypes,resource
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    f,g=new_fixture();bind(g);held=n.lease_hold_native(g)
    for path in (n.__file__,r._rhino3dm.__file__):
        handle=_ctypes.dlopen(path,os.RTLD_NOW)
        closer=ctypes.CDLL(None).dlclose;closer.argtypes=[ctypes.c_void_p];closer.restype=ctypes.c_int
        # Deliberately exhaust loader references in THIS disposable process. The
        # pinned NODELETE mappings must survive even an explicit unload request.
        returns=[]
        for _ in range(32):
            value=closer(handle);returns.append(value)
            if value:break
        check(any(value==0 for value in returns),'explicit loader close request exercised')
    check(n.lease_count(g)==2,'native token API survives explicit unload requests')
    del g,f;collect();n.lease_thread_release(held);del held;collect()
    print('UNLOAD_SAFE',flush=True);sys.exit(0)

if a.shutdown_child:
    f,g=new_fixture();bind(g);c=ORIGINAL_DUPLICATE(g);h=r.File3dm();h.Objects.AddBrep(c,r.ObjectAttributes());held=n.lease_hold(c)
    # All native objects and a capsule deliberately survive until interpreter teardown.
    print('SHUTDOWN_ARMED',flush=True);sys.exit(0)

start=time.perf_counter();result={'status':'running','checks':checks,'mutation':a.mutation}
try:
    f,g=new_fixture();key=bind(g);expected=occt.face_types(kernel.master_of(g));initial_volume=occt.volume(kernel.master_of(g))
    check(occt.is_airtight(kernel.master_of(g)),'fixture manufacturing baseline airtight')
    print('BASELINE',json.dumps({'faces':expected,'volume':initial_volume,'objects':len(f.Objects)}),flush=True)
    before=g.Encode()
    c=ORIGINAL_DUPLICATE(g)
    check(n.lease_count(g)==2 and n.lease_count(c)==2,'saved original wheel Duplicate retains lease')
    witness=n.observe(f)
    h=r.File3dm();oid=h.Objects.AddBrep(c,r.ObjectAttributes())
    check(n.lease_count(g)==3,'other native File3dm AddBrep retains lease')
    cw=weakref.ref(c);del c;collect()
    check(cw() is None and n.lease_count(g)==2,'temporary wrapper GC preserves native table owner')
    check(g.Encode()==before,'nonpersisted lease does not alter Encode wire data')
    check(n.observe(f)==witness,'lease copies leave NWOBS01 wire witness unchanged')
    check(n.lease_count(r.CommonObject.Decode(before))==0,'Encode Decode deliberately loses nonpersisted token')
    tmp=Path(tempfile.mkdtemp(prefix='lease-roundtrip-',dir=a.output))
    path=tmp/'copy.3dm';check(f.Write(str(path),8),'private 3dm roundtrip writes')
    decoded_file=r.File3dm.Read(str(path))
    decoded=target_object(decoded_file,n.file_owner).Geometry
    child=subprocess.run([sys.executable,'-B',__file__,*child_observer_arguments(a),
                          '--roundtrip-child',str(path)],capture_output=True,text=True,timeout=60)
    check(child.returncode==0 and 'ROUNDTRIP_EXACT' in child.stdout,
          'fresh process restores manufacturing master from persisted recipe: '+child.stderr)
    roundtrip_data=json.loads(child.stdout.split('ROUNDTRIP_EXACT ',1)[1].splitlines()[0])
    check(roundtrip_data['faces']==expected and abs(roundtrip_data['volume']-initial_volume)<1e-8,
          'fresh process roundtrip retains analytic topology and exact volume')
    check(n.lease_count(decoded)==0,'document read deliberately loses nonpersisted token')
    try:n.lease_hold(decoded)
    except RuntimeError:check(True,'tokenless geometry cannot silently acquire ownership')
    else:check(False,'tokenless geometry cannot silently acquire ownership')
    # A serialized history payload requires an explicit opaque owner alongside bytes.
    held=n.lease_hold(g);payload=g.Encode();fw=weakref.ref(f);gw=weakref.ref(g);del g,f;collect()
    check(fw() is None and gw() is None,'source file and temporary geometry wrappers collected')
    live=geometry(h,oid)
    if a.mutation=='premature-eviction':kernel._MASTERS.pop(key,None)
    exact(live,expected)
    check(n.lease_count(live)==2,'explicit serialized owner and other file retain exactly two leases')
    del live,h;collect()
    check(key in kernel._MASTERS,'explicit payload owner survives all native geometry owners')
    restored=r.CommonObject.Decode(payload);n.lease_bind(restored,key);del held;collect();exact(restored,expected)
    check(abs(occt.volume(kernel.master_of(restored))-initial_volume)<1e-8,'volume survives explicit serialized payload ownership')
    # Native destruction on a thread without the GIL, with the lease as final owner.
    held=n.lease_hold_native(restored);del restored;collect();n.lease_thread_release(held);del held
    check(key in collect(),'last owner destruction without GIL queues release for main thread')
    check(key not in kernel._MASTERS,'final owner release reclaims exact master')
    del decoded,decoded_file
    # Cross-library transforms/rebinds and pruned-redo-shaped workload. Detached
    # original, a second file, and current redo must each remain manufacturable.
    f,g=new_fixture();key=bind(g);other=ORIGINAL_DUPLICATE(g);h=r.File3dm();oid=h.Objects.AddBrep(other,r.ObjectAttributes());del other
    base=len(kernel._MASTERS);redo=None;counts=[];WORKLOAD=True
    for i in range(a.cycles):
        moved=ORIGINAL_DUPLICATE(g)
        moved=bridge.transform_geometry(moved,r.Transform.Translation(.125,0,0));bind(moved)
        redo=moved;del moved
        collect();counts.append(len(kernel._MASTERS))
        if i in (0,a.cycles//2,a.cycles-1):
            exact(g,expected);exact(geometry(h,oid),expected);exact(redo,expected)
    check(max(counts)<=base+1,f'{a.cycles} pruned redo cycles bound retained master count')
    check(n.lease_count(g)==2,'detached original and separate file remain native owners')
    check(abs(occt.volume(kernel.master_of(redo))-initial_volume)<1e-8,'transformed copy retains exact manufacturing volume')
    # Master binary serialization after reclamation still reconstructs analytic shape.
    blob=occt.shape_to_bytes(kernel.master_of(redo),triangles=False);roundtrip=occt.shape_from_bytes(blob)
    check(occt.is_airtight(roundtrip) and occt.face_types(roundtrip)==expected,'exact binary roundtrip after reclamation stays analytic and airtight')
    errors=[]
    def denied():
        try:n.lease_drain()
        except RuntimeError as e:errors.append(str(e))
    worker=threading.Thread(target=denied);worker.start();worker.join()
    check(len(errors)==1 and 'main thread' in errors[0],'background Python thread cannot drain releases')
    child=subprocess.run([sys.executable,'-B',__file__,*child_observer_arguments(a),*(['--fixture',a.fixture] if a.fixture else []),'--shutdown-child'],capture_output=True,text=True,timeout=30)
    check(child.returncode==0 and 'SHUTDOWN_ARMED' in child.stdout,'native owners and capsules survive interpreter shutdown without unload crash')
    unload=subprocess.run([sys.executable,'-B',__file__,*child_observer_arguments(a),*(['--fixture',a.fixture] if a.fixture else []),'--unload-child'],capture_output=True,text=True,timeout=30)
    check(unload.returncode==0 and 'UNLOAD_SAFE' in unload.stdout,'explicit unload requests preserve queued destruction and native calls')
    result.update(status='passed',master_counts=counts,baseline_faces=expected,baseline_volume=initial_volume)
finally:
    result['elapsed_s']=time.perf_counter()-start
    result['host_sha256']=hashlib.sha256(Path(r._rhino3dm.__file__).read_bytes()).hexdigest()
    result['library_sha256']=hashlib.sha256(Path(n.__file__).read_bytes()).hexdigest()
    (Path(a.output)/('qualification-'+(a.mutation or 'control')+'.json')).write_text(json.dumps(result,indent=2))
