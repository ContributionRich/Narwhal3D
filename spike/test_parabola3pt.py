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
# MEASURED (research_preview_14 P50, R5): start, point on parabola, end, then the direction;
# the axis runs parallel to end -> direction, so (5,2,0) gives the symmetric y^2 = 4x.
ctx,cmd,msg,done=run(["1,-2,0","0,0,0","1,2,0","5,2,0"])
check(done and ctx.made and exact(ctx.made[0][1]) and "exact quadratic" in msg,
      "default start/point/end/direction builds the exact parabola")
ctx,cmd,msg,done=run(["PickOrder=EndsFirst","1,-2,0","1,2,0","0,0,0","5,2,0"])
check(done and exact(ctx.made[0][1]), "EndsFirst accepts both endpoints before the vertex")
ctx,cmd,msg,done=run(["PickOrder=EndsLast","0,0,0","1,-2,0","1,2,0","5,2,0"])
check(done and exact(ctx.made[0][1]), "EndsLast accepts the vertex before both endpoints")
ctx,cmd,msg,done=run(["MarkFocus=Yes","1,-2,0","0,0,0","1,2,0","5,2,0"])
points=[g.Location for kind,g in ctx.made if kind=="Point"]
check(done and len(points)==1 and points[0].DistanceTo(r3.Point3d(1,0,0))<1e-10,
      "MarkFocus adds the analytically derived focus")
# Mode=Focus (#39): the Parabola command's focus / direction / end construction.
F,D,E=(1,2,0),(3,5,0),(6,1,0)
ctx,cmd,msg,done=run(["Mode=Focus"])
check(not done and not ctx.made and "not implemented" not in msg
      and [p for p,_k in cmd.steps]==["Parabola focus:","Parabola direction:","Parabola end:"],
      f"Mode=Focus asks for focus, direction and end ({[p for p,_k in cmd.steps]})")
def fmt(p): return ",".join(str(v) for v in p)
ctx,cmd,msg,done=run(["Mode=Focus",fmt(F),fmt(D),fmt(E)])
made=[g for kind,g in ctx.made if kind=="Parabola"]
pctx=Ctx(); pcmd=commands.ParabolaCmd(); pmsg,pdone=pcmd.begin(pctx)
for line in (fmt(F),fmt(D),fmt(E)): pmsg,pdone=pcmd.feed(line,pctx)
ref=[g for kind,g in pctx.made if kind=="Parabola"]
def cps(c): return [(p.X,p.Y,p.Z,p.W) for p in c.Points]
check(done and made and ref and len(cps(made[0]))==len(cps(ref[0]))
      and all(abs(a-b)<=1e-9 for pa,pb in zip(cps(made[0]),cps(ref[0])) for a,b in zip(pa,pb)),
      "Mode=Focus matches Parabola's Focus control points within 1e-9")
check(done and msg.startswith("Created ") and msg.endswith(" (exact quadratic)"),
      f"Mode=Focus reports the exact quadratic ({msg!r})")
check(made and made[0].PointAtEnd.DistanceTo(r3.Point3d(*E))<1e-9,
      "the curve ends at the picked end")
import math
def focus_of(c):
    # vertex = the curve's mid-parameter point; focus at distance a along the axis,
    # with a recovered from the end point: transverse^2 = 4 a axial.
    v=c.PointAt((c.Domain.T0+c.Domain.T1)/2)
    ax=[D[i]-F[i] for i in range(3)]; n=math.sqrt(sum(x*x for x in ax)); ax=[x/n for x in ax]
    e=c.PointAtEnd; d=[e.X-v.X,e.Y-v.Y,e.Z-v.Z]
    axial=sum(d[i]*ax[i] for i in range(3)); tr2=sum(x*x for x in d)-axial*axial
    a=tr2/(4*axial)
    return r3.Point3d(v.X+a*ax[0],v.Y+a*ax[1],v.Z+a*ax[2])
check(made and focus_of(made[0]).DistanceTo(r3.Point3d(*F))<1e-9,
      "the created parabola's focus is the picked focus")
ctx,cmd,msg,done=run(["Mode=Focus","MarkFocus=Yes",fmt(F),fmt(D),fmt(E)])
points=[g.Location for kind,g in ctx.made if kind=="Point"]
check(done and len(points)==1 and points[0].DistanceTo(r3.Point3d(*F))<1e-9,
      "MarkFocus=Yes in Focus mode adds a point at the picked focus")
ctx,cmd,msg,done=run(["Mode=Focus","PickOrder=EndsFirst"])
check(not done and "PickOrder" in msg and "Focus" in msg and cmd.opt["PickOrder"]=="Default"
      and len(cmd.steps)==3, f"PickOrder with Mode=Focus is refused by name ({msg!r})")
ctx,cmd,msg,done=run(["PickOrder=EndsLast","Mode=Focus"])
check(cmd.opt["PickOrder"]=="Default" and "PickOrder reset" in msg
      and cmd.steps[0][0]=="Parabola focus:",
      f"a PickOrder chosen before Focus is reset, not silently ignored ({msg!r})")
fcmd=commands.Parabola3PtCmd(); fcmd.opt["Mode"]="Focus"
frows=preview.segments("Parabola3Pt",[F,D],E,commands.cplane.WORLD_TOP,cmd=fcmd)
pcmd2=commands.ParabolaCmd()
prows=preview.segments("Parabola",[F,D],E,commands.cplane.WORLD_TOP,cmd=pcmd2)
check(frows and frows==prows, "the Focus-mode live preview is the Parabola Focus preview")
check("parabola3pt" in commands.COMMANDS,"Parabola3Pt is registered")
rows=preview.segments("Parabola3Pt",[(1,-2,0),(0,0,0),(1,2,0)],(5,2,0),commands.cplane.WORLD_TOP,cmd=commands.Parabola3PtCmd())
check(len(rows)==3 and len(rows[0])==65 and abs(rows[0][32][0])<1e-10 and rows[1]==[(5,2,0),(1.0,-2.0,0.0)],
      "live Parabola3Pt preview: the exact parabola for the cursor direction, rubber lines to the ends")
ctx,cmd,msg,done=run(["Mode=Vertex","1,-2,0","0,0,0","1,2,0"])
check(done and exact(ctx.made[0][1]), "Mode=Vertex keeps the symmetric start/vertex/end route")
for passed,label in checks: print(f"[{'PASS' if passed else 'FAIL'}] {label}")
failed=sum(not p for p,_ in checks); print(f"\n{len(checks)-failed}/{len(checks)} passed"); raise SystemExit(1 if failed else 0)
