"""Optional Rust global interpolation adapter with caller-owned output."""
from __future__ import annotations
import array, ctypes, math, os, sys, threading
from pathlib import Path
_ABI=1; _ENV="NARWHAL_PURE_CORE"; _MAX_POINTS=2048
_lock=threading.Lock(); _signature=None; _binding=None; _skin_signature=None; _skin_binding=None; _native_calls=0; _native_skin_calls=0
STYLE={"Uniform":0,"Chord":1,"SqrtChrd":2}
def _name():
    return "narwhal_pure_core.dll" if sys.platform=="win32" else "libnarwhal_pure_core.dylib" if sys.platform=="darwin" else "libnarwhal_pure_core.so"
def _fn():
    global _signature,_binding
    explicit=os.environ.get(_ENV); signature=explicit or "<packaged>"
    if _signature==signature:return _binding
    with _lock:
        if _signature==signature:return _binding
        _signature,_binding=signature,None
        packaged=Path(__file__).resolve().parent/"native"/_name()
        for path in ((Path(explicit).expanduser(),) if explicit else (packaged,)):
            if not path.is_file():continue
            try:
                lib=ctypes.CDLL(str(path)); version=lib.narwhal_pure_core_abi_version; version.argtypes=[]; version.restype=ctypes.c_uint32
                if version()!=_ABI:continue
                fn=lib.narwhal_pure_core_interpolate
                fn.argtypes=[ctypes.POINTER(ctypes.c_double),ctypes.c_size_t,ctypes.c_int64,ctypes.c_ubyte,ctypes.POINTER(ctypes.c_double),ctypes.c_size_t,ctypes.POINTER(ctypes.c_double),ctypes.c_size_t,ctypes.POINTER(ctypes.c_uint64),ctypes.POINTER(ctypes.c_uint64),ctypes.POINTER(ctypes.c_uint64)]; fn.restype=ctypes.c_int32
            except (AttributeError,OSError):continue
            _binding=fn;return fn
        return None
def _skin_fn():
 global _skin_signature,_skin_binding
 explicit=os.environ.get(_ENV);signature=explicit or "<packaged>"
 if _skin_signature==signature:return _skin_binding
 with _lock:
  if _skin_signature==signature:return _skin_binding
  _skin_signature,_skin_binding=signature,None;packaged=Path(__file__).resolve().parent/"native"/_name()
  for path in ((Path(explicit).expanduser(),) if explicit else (packaged,)):
   if not path.is_file():continue
   try:
    lib=ctypes.CDLL(str(path));fn=lib.narwhal_pure_core_skin_control_net;fn.argtypes=[ctypes.POINTER(ctypes.c_double),ctypes.c_size_t,ctypes.c_size_t,ctypes.c_int64,ctypes.c_ubyte,ctypes.POINTER(ctypes.c_double),ctypes.c_size_t,ctypes.POINTER(ctypes.c_double),ctypes.c_size_t,ctypes.POINTER(ctypes.c_uint64),ctypes.POINTER(ctypes.c_uint64),ctypes.POINTER(ctypes.c_uint64)];fn.restype=ctypes.c_int32
   except (AttributeError,OSError):continue
   _skin_binding=fn;return fn
  return None

def interpolate(points,degree,style):
    global _native_calls
    fn=_fn(); points=list(points); n=len(points)
    if fn is None or n<2 or n>_MAX_POINTS or style not in STYLE:return None
    try:
        flat=array.array("d",(float(c) for p in points for c in p[:3]))
        if len(flat)!=3*n or any(not math.isfinite(v) for v in flat):return None
        p=int(degree); controls=array.array("d",[0.0])*(3*n); knots=array.array("d",[0.0])*(n+max(1,p)+1)
        fv=(ctypes.c_double*len(flat)).from_buffer(flat); cv=(ctypes.c_double*len(controls)).from_buffer(controls); kv=(ctypes.c_double*len(knots)).from_buffer(knots)
        od=ctypes.c_uint64(); oc=ctypes.c_uint64(); ok=ctypes.c_uint64()
        code=fn(fv,n,p,STYLE[style],cv,len(controls),kv,len(knots),ctypes.byref(od),ctypes.byref(oc),ctypes.byref(ok))
    except Exception:return None
    actual,count,kcount=int(od.value),int(oc.value),int(ok.value)
    if code!=0 or count!=n or actual<1 or kcount!=n+actual+1:return None
    ctrl=[tuple(controls[3*i:3*i+3]) for i in range(n)]; full=list(knots[:kcount])
    if any(not math.isfinite(v) for row in ctrl for v in row) or full!=sorted(full):return None
    _native_calls+=1;return ctrl,full,actual
def skin_control_net(sections,degree,style):
 global _native_skin_calls
 fn=_skin_fn();sections=[list(row) for row in sections];k=len(sections);width=len(sections[0]) if sections else 0
 if fn is None or k<2 or width<1 or any(len(row)!=width for row in sections) or style not in STYLE:return None
 try:
  flat=array.array("d",(float(c) for row in sections for p in row for c in p[:3]));coords=3*k*width
  if len(flat)!=coords or any(not math.isfinite(v) for v in flat):return None
  out=array.array("d",[0.0])*coords;knots=array.array("d",[0.0])*(k+max(1,int(degree))+1);fv=(ctypes.c_double*coords).from_buffer(flat);ov=(ctypes.c_double*coords).from_buffer(out);kv=(ctypes.c_double*len(knots)).from_buffer(knots);od=ctypes.c_uint64();used=ctypes.c_uint64();nk=ctypes.c_uint64();code=fn(fv,k,width,int(degree),STYLE[style],ov,coords,kv,len(knots),ctypes.byref(od),ctypes.byref(used),ctypes.byref(nk))
 except Exception:return None
 q,nvalues,nknots=int(od.value),int(used.value),int(nk.value)
 if code!=0 or nvalues!=coords or nknots!=k+q+1:return None
 net=[]
 for i in range(width):net.append([tuple(out[3*(i*k+j):3*(i*k+j)+3]) for j in range(k)])
 _native_skin_calls+=1;return net,list(knots[:nknots]),q

def _reset_for_tests():
    global _signature,_binding,_skin_signature,_skin_binding,_native_calls,_native_skin_calls
    with _lock:_signature=None;_binding=None;_skin_signature=None;_skin_binding=None;_native_calls=0;_native_skin_calls=0
