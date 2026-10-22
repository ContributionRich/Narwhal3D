"""Rust compatible-tween control batching parity and performance."""
import importlib.util,math,os,sys,time,types
import rhino3dm as r3
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));LIB=os.path.join(ROOT,"native","pure_core","target","x86_64-unknown-linux-gnu","release","libnarwhal_pure_core.so");os.environ["NARWHAL_PURE_CORE"]=LIB
pkg=types.ModuleType("narwhal3d");pkg.__path__=[os.path.join(ROOT,"narwhal3d")];sys.modules["narwhal3d"]=pkg
def load(name):
 spec=importlib.util.spec_from_file_location("narwhal3d."+name,os.path.join(ROOT,"narwhal3d",name+".py"));mod=importlib.util.module_from_spec(spec);sys.modules["narwhal3d."+name]=mod;setattr(pkg,name,mod);spec.loader.exec_module(mod);return mod
native=load("native_tween");tween=load("tween");checks=[]
def check(v,label):checks.append((bool(v),label))
def controls(n,offset):return [(float(i),math.sin(i*.1)+offset,math.cos(i*.07),1.0) for i in range(n)]
a,b=controls(32,0),controls(32,10);got=native.interpolate(a,b,3);want=[]
for j in range(1,4):
 t=j/4;want.append([tuple(pa[k]+t*(pb[k]-pa[k]) for k in range(4)) for pa,pb in zip(a,b)])
check(got==want,"native tween controls are exactly the Python arithmetic contract")
def curve(n,y):return r3.NurbsCurve.CreateControlPointCurve([r3.Point3d(i,y+math.sin(i*.1),0) for i in range(n)],3)
def best(fn,reps):
 out=float("inf")
 for _ in range(5):
  s=time.perf_counter()
  for _ in range(reps):fn()
  out=min(out,time.perf_counter()-s)
 return out
ratios=[]
for n,num,reps in ((32,32,10),(128,32,3),(256,64,1)):
 first,second=curve(n,0),curve(n,10);tween.NATIVE_TWEEN_MIN_VALUES=0;nt=best(lambda:tween.compatible_tweens(first,second,num),reps);tween.NATIVE_TWEEN_MIN_VALUES=50_000_001;pt=best(lambda:tween.compatible_tweens(first,second,num),reps);ratio=nt/pt;ratios.append(ratio);print(f"  [perf] tween values={n*num} native={nt:.5f}s python={pt:.5f}s ratio={ratio:.3f}")
tween.NATIVE_TWEEN_MIN_VALUES=50_000_001;native._reset_for_tests();before=native._native_calls;tween.compatible_tweens(curve(32,0),curve(32,10),32);check(native._native_calls==before,"production tween stays Python without a measured 10% win")
check(tween.NATIVE_TWEEN_MIN_VALUES>50_000_000,"native tween gate is above the validated output cap")
for ok,label in checks:print(f"[{'PASS' if ok else 'FAIL'}] {label}")
failed=sum(not x for x,_ in checks);print(f"\n{len(checks)-failed}/{len(checks)} passed");raise SystemExit(1 if failed else 0)
