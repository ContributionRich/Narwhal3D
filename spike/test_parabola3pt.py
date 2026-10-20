"""Parabola3Pt exact symmetric construction and pick orders."""
import importlib.util
import os
import sys
import types

import rhino3dm as r3
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("narwhal3d"); pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg

def load(name):
    spec = importlib.util.spec_from_file_location("narwhal3d."+name, os.path.join(ROOT,"narwhal3d",name+".py"))
    mod = importlib.util.module_from_spec(spec); sys.modules["narwhal3d."+name]=mod
    setattr(pkg,name,mod); spec.loader.exec_module(mod); return mod

load("cplane"); load("bridge"); load("conics"); model=load("model"); load("options"); preview=load("preview"); commands=load("commands")
checks=[]
def check(value,label): checks.append((bool(value),label))
class Ctx:
    def __init__(self): self.model=model.NarwhalModel(); self.made=[]
    def add_geometry(self,g,base_name,layer_index=None):
        name=f"{base_name}_{len(self.made)+1}"; self.model.add_object(g,name,0); self.made.append((base_name,g)); return name,name
    def note_change(self,_label=None): pass
    def selected_objects(self): return []
def run(lines):
    ctx=Ctx(); cmd=commands.Parabola3PtCmd(); message,done=cmd.begin(ctx)
    for line in lines:
        if done: break
        message,done=cmd.feed(line,ctx)
    return ctx,cmd,message,done

def exact(curve):
    mid=curve.PointAt((curve.Domain.T0+curve.Domain.T1)/2)
    return (curve.Degree==2 and not curve.IsRational
            and mid.DistanceTo(r3.Point3d(0,0,0))<1e-10
            and curve.PointAtStart.DistanceTo(r3.Point3d(1,-2,0))<1e-10
            and curve.PointAtEnd.DistanceTo(r3.Point3d(1,2,0))<1e-10)
ctx,cmd,msg,done=run(["1,-2,0","0,0,0","1,2,0"])
check(done and ctx.made and exact(ctx.made[0][1]) and "exact quadratic" in msg,
      "default start/vertex/end builds the exact symmetric parabola")
ctx,cmd,msg,done=run(["PickOrder=EndsFirst","1,-2,0","1,2,0","0,0,0"])
check(done and exact(ctx.made[0][1]), "EndsFirst accepts both endpoints before the vertex")
ctx,cmd,msg,done=run(["PickOrder=EndsLast","0,0,0","1,-2,0","1,2,0"])
check(done and exact(ctx.made[0][1]), "EndsLast accepts the vertex before both endpoints")
ctx,cmd,msg,done=run(["MarkFocus=Yes","1,-2,0","0,0,0","1,2,0"])
points=[g.Location for kind,g in ctx.made if kind=="Point"]
check(done and len(points)==1 and points[0].DistanceTo(r3.Point3d(1,0,0))<1e-10,
      "MarkFocus adds the analytically derived focus")
ctx,cmd,msg,done=run(["Mode=Focus"])
check(not done and not ctx.made and "not implemented yet" in msg, "unsupported Focus mode refuses explicitly")
check("parabola3pt" in commands.COMMANDS,"Parabola3Pt is registered")
rows=preview.segments("Parabola3Pt",[(1,-2,0),(0,0,0)],(1,2,0),commands.cplane.WORLD_TOP,cmd=commands.Parabola3PtCmd())
check(len(rows)==1 and len(rows[0])==65 and abs(rows[0][32][0])<1e-10,
      "live Parabola3Pt preview follows the exact quadratic")
for passed,label in checks: print(f"[{'PASS' if passed else 'FAIL'}] {label}")
failed=sum(not p for p,_ in checks); print(f"\n{len(checks)-failed}/{len(checks)} passed"); raise SystemExit(1 if failed else 0)
