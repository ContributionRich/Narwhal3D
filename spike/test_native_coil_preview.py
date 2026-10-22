"""Rust analytic coil-preview parity and crossover."""
import importlib.util,os,sys,time,types
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));os.environ["NARWHAL_PURE_CORE"]=os.path.join(ROOT,"native","pure_core","target","x86_64-unknown-linux-gnu","release","libnarwhal_pure_core.so")
pkg=types.ModuleType("narwhal3d");pkg.__path__=[os.path.join(ROOT,"narwhal3d")];sys.modules["narwhal3d"]=pkg
def load(n):
 s=importlib.util.spec_from_file_location("narwhal3d."+n,os.path.join(ROOT,"narwhal3d",n+".py"));m=importlib.util.module_from_spec(s);sys.modules["narwhal3d."+n]=m;setattr(pkg,n,m);s.loader.exec_module(m);return m
cplane=load("cplane");native=load("native_coil");preview=load("preview");checks=[]
def check(v,l):checks.append((bool(v),l))
class Cmd:
 _vertical=False;_flat=False;_reverse_twist=False;_coil_mode="Turns";size_mode="radius"
 def __init__(self,turns):self.opt={"Turns":turns,"Pitch":1}
def call(turns,threshold):
 preview.NATIVE_COIL_PREVIEW_MIN_POINTS=threshold;return preview.segments("Helix",[(0,0,0),(0,0,20)],(3,0,0),cplane.WORLD_TOP,cmd=Cmd(turns))
py=call(20,10**9);native_rows=call(20,0);error=max(abs(a-b) for pa,pb in zip(py[1],native_rows[1]) for a,b in zip(pa,pb));check(error<1e-12 and len(py[1])==len(native_rows[1]),"native preview points match the Python analytic locus")
reverse=native.preview_points((0,0,0),(0,0,5),(1,0,0),2,2,1,True,False,17)
check(reverse is not None and reverse[1][1]<0,"native preview preserves ReverseTwist winding")
def best(fn,reps):
 out=float("inf")
 for _ in range(5):
  s=time.perf_counter()
  for _ in range(reps):fn()
  out=min(out,time.perf_counter()-s)
 return out
for turns,reps in ((10,200),(40,80),(100,30)):
 nt=best(lambda:call(turns,0),reps);pt=best(lambda:call(turns,10**9),reps);ratio=nt/pt;count=len(call(turns,0)[1]);print(f"  [perf] coil preview points={count} native={nt:.5f}s python={pt:.5f}s ratio={ratio:.3f}");check(ratio<=.90,f"native preview wins by 10% at {count} points")
preview.NATIVE_COIL_PREVIEW_MIN_POINTS=241;check(len(call(10,241)[1])==241,"production preview gate activates at measured crossover")
for ok,label in checks:print(f"[{'PASS' if ok else 'FAIL'}] {label}")
f=sum(not x for x,_ in checks);print(f"\n{len(checks)-f}/{len(checks)} passed");raise SystemExit(1 if f else 0)
