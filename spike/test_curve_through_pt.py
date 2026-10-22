"""CurveThroughPt selected point-object conversion."""
import importlib.util, os, sys, types
import rhino3dm as r3
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg=types.ModuleType("narwhal3d"); pkg.__path__=[os.path.join(ROOT,"narwhal3d")]; sys.modules["narwhal3d"]=pkg
def load(name):
    spec=importlib.util.spec_from_file_location("narwhal3d."+name,os.path.join(ROOT,"narwhal3d",name+".py")); mod=importlib.util.module_from_spec(spec); sys.modules["narwhal3d."+name]=mod; setattr(pkg,name,mod); spec.loader.exec_module(mod); return mod
load("cplane"); bridge=load("bridge"); interpolate=load("interpolate"); model=load("model"); load("options"); commands=load("commands")
checks=[]
def check(v,label): checks.append((bool(v),label))
POINTS=[(0.,0.,0.),(1.,2.,0.),(4.,1.,0.),(7.,3.,0.)]
class Ctx:
    def __init__(self):
        self.model=model.NarwhalModel(); self.made=[]
        for i,p in enumerate(POINTS): self.model.add_object(bridge.make_point(p),f"P{i}",0)
        self.model.add_object(bridge.make_point(POINTS[1]),"Duplicate",0)
        self.model.add_object(bridge.make_line((0,0,0),(1,0,0)),"NotPoint",0)
    def geom_by_name(self,name):
        oid=self.model.object_id_by_name(name); item=self.model.file.Objects.FindId(oid) if oid else None; return item.Geometry if item else None
    def add_geometry(self,g,base_name,layer_index=None):
        name=f"{base_name}_{len(self.made)+1}"; self.model.add_object(g,name,0); self.made.append(g); return name,name
    def note_change(self,_label=None): pass

def execute(kind="ControlPoint"):
    ctx=Ctx(); cmd=commands.CurveThroughPtCmd(); cmd.args=[["P0","P1","Duplicate","P2","P3"]]; cmd.opt["CurveType"]=kind; msg,done=cmd.execute(ctx); return ctx,cmd,msg,done
ctx,cmd,msg,done=execute(); curve=ctx.made[0]
controls=[(curve.Points[i].X,curve.Points[i].Y,curve.Points[i].Z) for i in range(len(curve.Points))]
check(done and controls==POINTS and "4 distinct points" in msg,"ControlPoint output preserves order and ignores duplicate point locations")
ctx,cmd,msg,done=execute("Interpolated"); curve=ctx.made[0]; params=interpolate.parameters(POINTS,"Chord"); d=curve.Domain
errors=[curve.PointAt(d.T0+t*(d.T1-d.T0)).DistanceTo(r3.Point3d(*p)) for t,p in zip(params,POINTS)]
check(done and max(errors)<1e-9,"Interpolated output passes through every distinct point")
ctx=Ctx(); cmd=commands.CurveThroughPtCmd(); cmd.args=[["P0","NotPoint"]]; msg,done=cmd.execute(ctx)
check(done and not ctx.made and "not a point object" in msg,"non-point selections refuse explicitly")
ctx=Ctx(); cmd=commands.CurveThroughPtCmd(); cmd.args=[["P1","Duplicate"]]; msg,done=cmd.execute(ctx)
check(done and not ctx.made and "at least two distinct" in msg,"all-duplicate selection refuses instead of making a degenerate curve")
check("curvethroughpt" in commands.COMMANDS,"CurveThroughPt is registered")
for ok,label in checks: print(f"[{'PASS' if ok else 'FAIL'}] {label}")
failed=sum(not x for x,_ in checks); print(f"\n{len(checks)-failed}/{len(checks)} passed"); raise SystemExit(1 if failed else 0)
