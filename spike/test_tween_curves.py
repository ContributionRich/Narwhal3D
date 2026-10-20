"""TweenCurves compatible control-point interpolation."""
import importlib.util, os, sys, types
import rhino3dm as r3
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); pkg=types.ModuleType("narwhal3d"); pkg.__path__=[os.path.join(ROOT,"narwhal3d")]; sys.modules["narwhal3d"]=pkg
def load(name):
    spec=importlib.util.spec_from_file_location("narwhal3d."+name,os.path.join(ROOT,"narwhal3d",name+".py")); mod=importlib.util.module_from_spec(spec); sys.modules["narwhal3d."+name]=mod; setattr(pkg,name,mod); spec.loader.exec_module(mod); return mod
load("cplane"); bridge=load("bridge"); model=load("model"); load("options"); tween=load("tween"); commands=load("commands")
checks=[]
def check(v,label): checks.append((bool(v),label))
def curve(y,count=4): return r3.NurbsCurve.CreateControlPointCurve([r3.Point3d(i,y,(i%2)*0.5) for i in range(count)],3)
class Ctx:
    def __init__(self,incompatible=False):
        self.model=model.NarwhalModel(); self.model.add_layer("Start",(1,2,3)); self.model.add_layer("End",(4,5,6)); self.made=[]
        self.model.add_object(curve(0),"A",1); self.model.add_object(curve(10,5 if incompatible else 4),"B",2)
    def geom_by_name(self,name):
        oid=self.model.object_id_by_name(name); item=self.model.file.Objects.FindId(oid) if oid else None; return item.Geometry if item else None
    def add_geometry(self,g,base_name,layer_index=None): self.made.append((g,layer_index)); return f"Tween_{len(self.made)}",f"Tween_{len(self.made)}"
    def note_change(self,_label=None): pass

def execute(number=3,flip=False,layer="CurrentLayer",incompatible=False,method="ControlPoint"):
    ctx=Ctx(incompatible); cmd=commands.TweenCurvesCmd(); cmd.args=["A","B"]; cmd.opt["Number"]=number; cmd.opt["OutputLayer"]=layer; cmd.opt["MatchMethod"]=method; cmd._flip=flip; msg,done=cmd.execute(ctx); return ctx,cmd,msg,done
ctx,cmd,msg,done=execute();
check(done and len(ctx.made)==3 and "3 tween curves" in msg,"Number creates every requested compatible tween")
ys=[ctx.made[i][0].Points[0].Y for i in range(3)]
check(all(abs(a-b)<1e-12 for a,b in zip(ys,(2.5,5.0,7.5))),"control points interpolate at evenly spaced fractions")
ctx,cmd,msg,done=execute(number=1,flip=True); g=ctx.made[0][0]
check(done and g.PointAtEnd.X < g.PointAtStart.X+1.0,"Flip reverses second-curve correspondence before tweening")
ctx,cmd,msg,done=execute(number=1,layer="StartCrv")
check(done and ctx.made[0][1]==1,"OutputLayer=StartCrv inherits the first curve layer")
ctx,cmd,msg,done=execute(incompatible=True)
check(done and not ctx.made and "requires equal degree" in msg,"incompatible ControlPoint inputs refuse explicitly")
ctx,cmd,msg,done=execute(method="SamplePoints")
check(done and not ctx.made and "not implemented yet" in msg,"unbuilt SamplePoints method refuses explicitly")
check("tweencurves" in commands.COMMANDS,"TweenCurves is registered")
for ok,label in checks: print(f"[{'PASS' if ok else 'FAIL'}] {label}")
failed=sum(not x for x,_ in checks); print(f"\n{len(checks)-failed}/{len(checks)} passed"); raise SystemExit(1 if failed else 0)
