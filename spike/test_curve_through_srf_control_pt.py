"""CurveThroughSrfControlPt U/V control-polygon extraction."""
import importlib.util,os,sys,types
import rhino3dm as r3
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));pkg=types.ModuleType("narwhal3d");pkg.__path__=[os.path.join(ROOT,"narwhal3d")];sys.modules["narwhal3d"]=pkg
def load(n):
 s=importlib.util.spec_from_file_location("narwhal3d."+n,os.path.join(ROOT,"narwhal3d",n+".py"));m=importlib.util.module_from_spec(s);sys.modules["narwhal3d."+n]=m;setattr(pkg,n,m);s.loader.exec_module(m);return m
load("cplane");load("bridge");model=load("model");load("options");commands=load("commands");checks=[]
def check(v,l):checks.append((bool(v),l))
def surface():
 s=r3.NurbsSurface.Create(3,False,4,3,4,3)
 for i,v in enumerate((0,0,0,1,1,1)):s.KnotsU[i]=v
 for i,v in enumerate((0,0,1,1)):s.KnotsV[i]=v
 for u in range(4):
  for v in range(3):s.Points[u,v]=r3.Point4d(u,v,u*v*.25,1)
 return s
class Ctx:
 def __init__(self):self.model=model.NarwhalModel();self.model.add_object(surface(),"S",0);self.made=[]
 def geom_by_name(self,n):
  oid=self.model.object_id_by_name(n);return self.model.file.Objects.FindId(oid).Geometry if oid else None
 def add_geometry(self,g,b,layer_index=None):self.made.append(g);return "id",f"{b}_{len(self.made)}"
 def note_change(self,_=None):pass
def execute(direction="U",kind="ControlPoint"):
 c=Ctx();cmd=commands.CurveThroughSrfControlPtCmd();cmd.args=["S",(1.05,1.02,.24)];cmd.opt["Direction"]=direction;cmd.opt["CurveType"]=kind;msg,done=cmd.execute(c);return c,cmd,msg,done
ctx,cmd,msg,done=execute();curve=ctx.made[0];controls=[(curve.Points[i].X,curve.Points[i].Y,curve.Points[i].Z) for i in range(len(curve.Points))]
check(done and controls==[(u,1,u*.25) for u in range(4)],"nearest control selects the exact U polygon")
ctx,cmd,msg,done=execute("V");curve=ctx.made[0];controls=[(curve.Points[i].X,curve.Points[i].Y,curve.Points[i].Z) for i in range(len(curve.Points))]
check(done and controls==[(1,v,v*.25) for v in range(3)],"Direction=V extracts the orthogonal control polygon")
ctx,cmd,msg,done=execute("Both");check(done and len(ctx.made)==2 and "2 surface-control curves" in msg,"Direction=Both creates both control polygons")
ctx,cmd,msg,done=execute("U","Interpolated");curve=ctx.made[0]
check(done and all(curve.PointAt(curve.Domain.T0+(curve.Domain.T1-curve.Domain.T0)*u/3).DistanceTo(r3.Point3d(u,1,u*.25))<1e-8 for u in range(4)),"Interpolated output passes through the selected control row")
ctx=Ctx();cmd=commands.CurveThroughSrfControlPtCmd();msg,done=cmd.on_mode("Toggle",ctx);check(not done and cmd.opt["Direction"]=="V","Toggle switches U to V")
check("curvethroughsrfcontrolpt" in commands.COMMANDS,"CurveThroughSrfControlPt is registered")
for ok,label in checks:print(f"[{'PASS' if ok else 'FAIL'}] {label}")
f=sum(not x for x,_ in checks);print(f"\n{len(checks)-f}/{len(checks)} passed");raise SystemExit(1 if f else 0)
