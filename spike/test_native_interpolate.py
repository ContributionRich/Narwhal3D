"""Rust global-interpolation parity, routing and complete-call performance."""
import importlib.util, math, os, sys, time, types
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); LIB=os.path.join(ROOT,"native","pure_core","target","x86_64-unknown-linux-gnu","release","libnarwhal_pure_core.so"); os.environ["NARWHAL_PURE_CORE"]=LIB
pkg=types.ModuleType("narwhal3d"); pkg.__path__=[os.path.join(ROOT,"narwhal3d")]; sys.modules["narwhal3d"]=pkg
def load(name):
 spec=importlib.util.spec_from_file_location("narwhal3d."+name,os.path.join(ROOT,"narwhal3d",name+".py")); mod=importlib.util.module_from_spec(spec); sys.modules["narwhal3d."+name]=mod; setattr(pkg,name,mod); spec.loader.exec_module(mod); return mod
native=load("native_interpolate"); interp=load("interpolate"); checks=[]
def check(v,label):checks.append((bool(v),label))
def points(n):return [(float(i),math.sin(i*.37)*3,math.cos(i*.19)) for i in range(n)]
for style in interp.KNOT_STYLES:
 p=points(24); old_threshold=interp.NATIVE_INTERPOLATE_MIN_POINTS; interp.NATIVE_INTERPOLATE_MIN_POINTS=10**9; want=interp.interpolate_points(p,3,style); interp.NATIVE_INTERPOLATE_MIN_POINTS=old_threshold; got=native.interpolate(p,3,style)
 error=max(abs(a-b) for ra,rb in zip(want[0],got[0]) for a,b in zip(ra,rb)) if got else math.inf
 knot_error=max(abs(a-b) for a,b in zip(want[1],got[1])) if got else math.inf
 if got is not None:
  print(f"  [parity] {style} degree={got[2]}/{want[2]} knot_error={knot_error:.3g} control_error={error:.3g}")
 check(got is not None and got[2]==want[2] and knot_error<1e-15 and error<1e-9,f"native {style} interpolation matches Python controls and knots")
native._reset_for_tests(); interp.NATIVE_INTERPOLATE_MIN_POINTS=16; before=native._native_calls; routed=interp.interpolate_points(points(32),3,"Chord")
check(native._native_calls==before+1 and len(routed[0])==32,"production interpolation routes through Rust above crossover")
def best(fn,reps):
 out=float("inf")
 for _ in range(5):
  start=time.perf_counter()
  for _ in range(reps):fn()
  out=min(out,time.perf_counter()-start)
 return out
for n,reps in ((16,100),(32,40),(64,10)):
 p=points(n); interp.NATIVE_INTERPOLATE_MIN_POINTS=0; nt=best(lambda:interp.interpolate_points(p,3,"Chord"),reps); interp.NATIVE_INTERPOLATE_MIN_POINTS=10**9; pt=best(lambda:interp.interpolate_points(p,3,"Chord"),reps); ratio=nt/pt; print(f"  [perf] interpolate n={n} native={nt:.5f}s python={pt:.5f}s ratio={ratio:.3f}"); check(ratio<=.90,f"native interpolation wins by 10% at {n} points")
interp.NATIVE_INTERPOLATE_MIN_POINTS=16
for ok,label in checks:print(f"[{'PASS' if ok else 'FAIL'}] {label}")
failed=sum(not x for x,_ in checks);print(f"\n{len(checks)-failed}/{len(checks)} passed");raise SystemExit(1 if failed else 0)
