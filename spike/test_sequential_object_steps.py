"""Sequential single-object prompts are clickable after command start."""
import importlib.util,os,sys,types
import rhino3dm as r3
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));pkg=types.ModuleType("narwhal3d");pkg.__path__=[os.path.join(ROOT,"narwhal3d")];sys.modules["narwhal3d"]=pkg
def load(n):
 s=importlib.util.spec_from_file_location("narwhal3d."+n,os.path.join(ROOT,"narwhal3d",n+".py"));m=importlib.util.module_from_spec(s);sys.modules["narwhal3d."+n]=m;setattr(pkg,n,m);s.loader.exec_module(m);return m
load("cplane");load("bridge");model=load("model");load("options");load("tween");commands=load("commands");checks=[]
def check(v,l):checks.append((bool(v),l))
class Obj:
 def __init__(self,n):self.name=n
class Ctx:
 def __init__(self,selected=()):
  self.model=model.NarwhalModel();self.selected=list(selected);self.made=[]
  for name,y in (("A",0),("B",10)):
   c=r3.NurbsCurve.CreateControlPointCurve([r3.Point3d(i,y,0) for i in range(4)],3);self.model.add_object(c,name,0)
 def selected_objects(self):return [Obj(n) for n in self.selected]
 def select_by_names(self,names):self.selected=list(names);return self.selected
 def geom_by_name(self,n):
  oid=self.model.object_id_by_name(n);return self.model.file.Objects.FindId(oid).Geometry if oid else None
 def add_geometry(self,g,b,layer_index=None):self.made.append(g);return "id","Tween_1"
 def note_change(self,_=None):pass
ctx=Ctx();cmd=commands.TweenCurvesCmd();msg,done=cmd.begin(ctx)
check(not done and cmd.prompt.startswith("Select first curve"),"TweenCurves starts by asking for the first curve with no preselection")
msg,done=cmd.feed("A",ctx)
check(not done and cmd.args==["A"] and cmd.prompt.startswith("Select second curve"),"first clicked curve advances immediately to the second prompt")
msg,done=cmd.feed("B",ctx)
check(done and len(ctx.made)==1,"second clicked curve completes TweenCurves")
ctx_both=Ctx(["A","B"]);cmd_both=commands.TweenCurvesCmd();_msg,done_both=cmd_both.begin(ctx_both)
check(done_both and cmd_both.args==["A","B"],"two preselected curves are consumed once each in selection order")
ctx=Ctx(["A"]);cmd=commands.TweenCurvesCmd();msg,done=cmd.begin(ctx)
check(not done and cmd.args==["A"] and cmd.step_index==1 and ctx.selected==[],"one preselected curve fills only first step and clears before second pick")
cmd.feed("B",ctx);check(len(ctx.made)==1,"second curve can be selected after a preselected first curve")
check([kind for _prompt,kind in commands.TweenCurvesCmd.steps]==[commands.OBJECTS,commands.OBJECTS],"TweenCurves uses two clickable OBJECTS steps")
check(commands.CurveThroughSrfControlPtCmd.steps[0][1]==commands.OBJECTS,"surface-control command first selection is clickable")
check(commands.OffsetCmd.steps[0][1]==commands.OBJECTS,"Offset no longer requires preselection or a typed name")
for ok,label in checks:print(f"[{'PASS' if ok else 'FAIL'}] {label}")
f=sum(not x for x,_ in checks);print(f"\n{len(checks)-f}/{len(checks)} passed");raise SystemExit(1 if f else 0)
