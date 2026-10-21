"""HandleCurve chained Bézier command and C1 continuity."""
import importlib.util,math,os,sys,types
import rhino3dm as r3
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));pkg=types.ModuleType("narwhal3d");pkg.__path__=[os.path.join(ROOT,"narwhal3d")];sys.modules["narwhal3d"]=pkg
def load(n):
 s=importlib.util.spec_from_file_location("narwhal3d."+n,os.path.join(ROOT,"narwhal3d",n+".py"));m=importlib.util.module_from_spec(s);sys.modules["narwhal3d."+n]=m;setattr(pkg,n,m);s.loader.exec_module(m);return m
load("cplane");load("bridge");load("handlecurve");model=load("model");load("options");preview=load("preview");commands=load("commands");checks=[]
def check(v,l):checks.append((bool(v),l))
class Ctx:
 def __init__(self):self.model=model.NarwhalModel();self.made=[]
 def add_geometry(self,g,b,layer_index=None):self.made.append(g);return "id",f"{b}_1"
 def note_change(self,_=None):pass
 def selected_objects(self):return []
def run(lines):
 c=Ctx();cmd=commands.HandleCurveCmd();msg,done=cmd.begin(c)
 for line in lines:
  if done:break
  msg,done=cmd.feed(line,c)
 return c,cmd,msg,done
values=["0,0,0","1,0,0","3,2,0","4,2,0","6,0,0","7,0,0",""]
ctx,cmd,msg,done=run(values);curve=ctx.made[0] if ctx.made else None
check(done and curve and curve.Degree==3 and not curve.IsPolyline() and "C1 cubic" in msg,"HandleCurve commits one genuine cubic NURBS")
check(curve and curve.PointAtStart.DistanceTo(r3.Point3d(0,0,0))<1e-12 and curve.PointAtEnd.DistanceTo(r3.Point3d(6,0,0))<1e-12,"curve starts and ends on picked anchors")
left=curve.DerivativeAt(.5-1e-8,1)[1];right=curve.DerivativeAt(.5+1e-8,1)[1];dot=left.X*right.X+left.Y*right.Y+left.Z*right.Z;ll=math.sqrt(left.X**2+left.Y**2+left.Z**2);rr=math.sqrt(right.X**2+right.Y**2+right.Z**2)
check(dot/(ll*rr)>1-1e-8 and abs(ll-rr)/max(ll,rr)<1e-6,"shared handle makes the internal join parametrically C1")
ctx,cmd,msg,done=run(["0,0,0","1,0,0","3,2,0","4,2,0","Undo",""])
check(done and not ctx.made and "handle for every" in msg,"Undo reverses the last pick and incomplete pairs refuse")
ctx,cmd,msg,done=run(["0,0,0","1,0,0","3,2,0","4,2,0","Close"])
check(done and ctx.made and ctx.made[0].IsClosed,"Close creates a closed handle curve")
ctx,cmd,msg,done=run(["PersistentClose"]);check(not done and "not implemented yet" in msg,"PersistentClose refuses explicitly")
check("handlecurve" in commands.COMMANDS,"HandleCurve is registered")
rows=preview.segments("HandleCurve",[(0,0,0),(1,0,0),(3,2,0),(4,2,0)],(6,0,0),commands.cplane.WORLD_TOP)
check(len(rows)==2 and len(rows[1])==17 and rows[1][0]==(0.0,0.0,0.0),"live HandleCurve preview shows the committed Bézier span and next pick guide")
for ok,label in checks:print(f"[{'PASS' if ok else 'FAIL'}] {label}")
f=sum(not x for x,_ in checks);print(f"\n{len(checks)-f}/{len(checks)} passed");raise SystemExit(1 if f else 0)
