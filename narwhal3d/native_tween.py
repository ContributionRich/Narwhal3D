"""Optional Rust batched compatible-tween control interpolation."""
from __future__ import annotations
import array,ctypes,math,os,sys,threading
from pathlib import Path
_ABI=1;_ENV="NARWHAL_PURE_CORE";_lock=threading.Lock();_signature=None;_binding=None;_native_calls=0
def _name():return "narwhal_pure_core.dll" if sys.platform=="win32" else "libnarwhal_pure_core.dylib" if sys.platform=="darwin" else "libnarwhal_pure_core.so"
def _fn():
 global _signature,_binding
 explicit=os.environ.get(_ENV);signature=explicit or "<packaged>"
 if _signature==signature:return _binding
 with _lock:
  if _signature==signature:return _binding
  _signature,_binding=signature,None;packaged=Path(__file__).resolve().parent/"native"/_name()
  for path in ((Path(explicit).expanduser(),) if explicit else (packaged,)):
   if not path.is_file():continue
   try:
    lib=ctypes.CDLL(str(path));v=lib.narwhal_pure_core_abi_version;v.argtypes=[];v.restype=ctypes.c_uint32
    if v()!=_ABI:continue
    fn=lib.narwhal_pure_core_tween_controls;fn.argtypes=[ctypes.POINTER(ctypes.c_double),ctypes.POINTER(ctypes.c_double),ctypes.c_size_t,ctypes.c_size_t,ctypes.POINTER(ctypes.c_double),ctypes.c_size_t,ctypes.POINTER(ctypes.c_uint64)];fn.restype=ctypes.c_int32
   except (AttributeError,OSError):continue
   _binding=fn;return fn
  return None
def interpolate(first,second,number):
 global _native_calls
 fn=_fn();n=len(first)
 if fn is None or n==0 or len(second)!=n or number<1:return None
 try:
  a=array.array("d",(float(v) for p in first for v in p));b=array.array("d",(float(v) for p in second for v in p));total=4*n*int(number)
  if len(a)!=4*n or len(b)!=4*n or total>50_000_000 or any(not math.isfinite(v) for v in a+b):return None
  out=array.array("d",[0.0])*total;av=(ctypes.c_double*len(a)).from_buffer(a);bv=(ctypes.c_double*len(b)).from_buffer(b);ov=(ctypes.c_double*len(out)).from_buffer(out);used=ctypes.c_uint64()
  code=fn(av,bv,n,int(number),ov,total,ctypes.byref(used))
 except Exception:return None
 if code!=0 or int(used.value)!=total:return None
 rows=[]
 for curve in range(number):
  base=curve*4*n;rows.append([tuple(out[base+4*i:base+4*i+4]) for i in range(n)])
 if any(abs(p[3])<=1e-15 for row in rows for p in row):return None
 _native_calls+=1;return rows
def _reset_for_tests():
 global _signature,_binding,_native_calls
 with _lock:_signature=None;_binding=None;_native_calls=0
