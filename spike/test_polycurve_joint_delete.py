"""Actual host/native policy parity with measured reference CAD application 8.35 joint deletion.

SDK degree elevation/concatenation and Blender Undo require Python orchestration.
Numeric deletion/validation is Rust. Optional private input is never public data.
"""
from pathlib import Path
import math
import sys
import tempfile
import types
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
pkg=types.ModuleType('narwhal3d');pkg.__path__=[str(ROOT/'narwhal3d')]
sys.modules['narwhal3d']=pkg;sys.path.insert(0,str(ROOT))
import bpy
import rhino3dm as r3
from narwhal3d import bridge,commands,native_pure,ui
from narwhal3d.model import NarwhalModel
from narwhal3d.state import app_state

checks=0

def check(ok,message):
    global checks
    checks+=1
    assert ok,message

def nc(points,degree,domain):
    curve=r3.NurbsCurve.Create(False,degree,[r3.Point3d(*p) for p in points])
    curve.Domain=r3.Interval(*domain)
    return curve

def joined(*leaves):
    out=r3.PolyCurve()
    for leaf in leaves:check(out.Append(leaf),'append exact source segment')
    return out

def data(curve):
    n=curve.ToNurbsCurve()
    return (n.Degree,n.IsRational,tuple(n.Knots),tuple((p.X,p.Y,p.Z,p.W) for p in n.Points))

def signature(curve):
    leaves=bridge._polycurve_leaves(curve) if isinstance(curve,r3.PolyCurve) else [curve]
    return (type(curve).__name__,curve.IsClosed,tuple(data(lf) for lf in leaves))

def near_sequence(a,b):
    return len(a)==len(b) and all(math.isclose(x,y,rel_tol=2e-14,abs_tol=2e-14) for x,y in zip(a,b))

def golden_case(curve,index,cvs,knots,closed=False):
    before=curve.Encode()
    result,message=bridge.remove_control_points(curve,[index])
    check(result is not None,f'measured joint must delete: {message}')
    check(curve.Encode()==before,'producer never modifies original geometry')
    check(isinstance(result,r3.PolyCurve) and result.SegmentCount==1,'reference CAD application retains one-segment PolyCurve')
    n=result.SegmentCurve(0).ToNurbsCurve()
    check(n.IsValid and n.Degree==3 and n.IsRational,'exact cubic homogeneous result remains valid')
    check(result.IsClosed==closed,'closed-seam edit remains closed; open edit remains open')
    check(near_sequence(list(n.Knots),knots),'knots match native reference CAD application List, not a refit')
    check(near_sequence([v for p in n.Points for v in (p.X,p.Y,p.Z,p.W)],
                        [v for p in cvs for v in p]),'homogeneous CVs match measured reference CAD application')
    return result

def host_delete(curve,index,units=None):
    bpy.data.batch_remove(list(bpy.data.objects))
    app_state.set_model(NarwhalModel());ctx=app_state.ctx
    if units is not None:ctx.model.file.Settings.ModelUnitSystem=units
    source_units=ctx.model.file.Settings.ModelUnitSystem
    layer=r3.Layer();layer.Name='JointLayer';ctx.model.file.Layers.Add(layer)
    _,name=ctx.add_geometry(curve.Duplicate(),'Joint',1)
    obj=lambda:ctx.model.file.Objects.FindId(ctx.model.object_id_by_name(name))
    group=r3.Group();group.Name='JointGroup';ctx.model.file.Groups.Add(group)
    obj().Attributes.AddToGroup(ctx.model.file.Groups[0].Index)
    obj().Attributes.SetUserString('joint-test','preserved')
    identity=str(obj().Attributes.Id);before=signature(obj().Geometry)
    ctx.select_by_names([name]);ctx.undo.clear();ui._points_on.add(name)
    ui._selected_cps.clear();ui._selected_cps.add((name,index))
    check(ui.selected_control_points()==[(name,index)],'actual host grip selection is live')
    response=commands.DeleteCmd().begin(ctx)
    check('Deleted 1 control point.' in str(response),'real Delete dispatch edits grip, not object')
    check(len(list(ctx.model.file.Objects))==1 and str(obj().Attributes.Id)==identity,'Delete retains object UUID')
    after=signature(obj().Geometry)
    check(after!=before and obj().Geometry.IsValid,'actual Delete changes exact valid geometry')
    check(obj().Attributes.LayerIndex==1 and obj().Attributes.GetGroupList2()==[0]
          and obj().Attributes.GetUserString('joint-test')=='preserved','layer/group/user attributes survive')
    check(ctx.undo.undo_names()==['Delete'],'one Delete creates exactly one Undo record')
    check(ctx.undo_last()=='Delete' and signature(obj().Geometry)==before,'Undo restores exact original')
    check(ctx.redo_last()=='Delete' and signature(obj().Geometry)==after,'Redo restores exact edited curve')
    check(ctx.model.file.Settings.ModelUnitSystem==source_units,'Delete/Undo/Redo preserve document units')
    with tempfile.TemporaryDirectory(prefix='narwhal-joint-delete-') as temp:
        path=str(Path(temp)/'edited.3dm');ctx.model.save(path)
        reopened=NarwhalModel.load(path)
        restored=next(o.Geometry for o in reopened.file.Objects if o.Attributes.Name==name)
        check(signature(restored)==after,'save/reload retains exact edited NURBS, not display samples')
        check(reopened.file.Settings.ModelUnitSystem==source_units,'save/reload preserves source units')
    return ctx,name

def main():
    # MEASURED in real reference CAD application 8.35 via guarded native List, 2026-11-24.
    a=nc([(0,0,0),(1,0,0),(1,1,0),(2,1,0)],3,(0,3))
    b=nc([(2,1,0),(3,1,0),(3,0,0),(4,0,0)],3,(3,6))
    curve=joined(a,b)
    expected=[(0,0,0,1),(1,0,0,1),(1,1,0,1),(3,1,0,1),(3,0,0,1),(4,0,0,1)]
    golden_case(curve,3,expected,[0,0,0,3,3,6,6,6])
    host_delete(curve,3)
    end=6.6502815398728847
    quadratic=nc([(2,1,0),(3,2,0),(4,0,0)],2,(3,end))
    mixed=joined(a,quadratic)
    expected=[(0,0,0,1),(1,0,0,1),(1,1,0,1),
              (2.6666666666666665,1.6666666666666667,0,1),
              (3.3333333333333335,1.3333333333333333,0,1),(4,0,0,1)]
    golden_case(mixed,3,expected,[0,0,0,3,3,end,end,end])
    host_delete(mixed,3)
    arc=r3.Arc(r3.Point3d(2,1,0),r3.Point3d(3,0,0),r3.Point3d(4,1,0)).ToNurbsCurve()
    arc.Domain=r3.Interval(3,3+math.pi)
    rational=joined(a,arc)
    w=0.80473785412436494
    expected=[(0,0,0,1),(1,0,0,1),(1,1,0,1),
              (1.6094757082487299,1/3,0,w),(1.9428090415820634,0,0,w),(3,0,0,1),
              (2.8856180831641267,0,0,w),(3.2189514164974597,1/3,0,w),(4,1,0,1)]
    golden_case(rational,3,expected,[0,0,0,3,3,3+math.pi/2,3+math.pi/2,3+math.pi/2,
                                  3+math.pi,3+math.pi,3+math.pi])
    host_delete(rational,3)
    left=nc([(0,0,0),(0,1,0),(2,1,0),(2,0,0)],3,(0,4))
    right=nc([(2,0,0),(2,-1,0),(0,-1,0),(0,0,0)],3,(4,8))
    seam=joined(left,right)
    expected=[(2,0,0,1),(2,-1,0,1),(0,-1,0,1),(0,1,0,1),(2,1,0,1),(2,0,0,1)]
    edited=golden_case(seam,0,expected,[4,4,4,8,8,12,12,12],closed=True)
    check((edited.Domain.T0,edited.Domain.T1)==(4,8),'closed seam retains measured native outer slot')
    expected_again,_=bridge.remove_control_points(edited.SegmentCurve(0),[0])
    again,_=bridge.remove_control_points(edited,[0])
    check(again is not None and data(again)==data(expected_again),
          'one-leaf closed seam uses existing leaf policy, never concatenates the loop to itself')
    host_delete(seam,0)
    # Existing tiny endpoint gaps must not cause unrelated leaves to be matched/refitted.
    third=nc([(4+1e-14,0,0),(5,0,0),(5,1,0),(6,1,0)],3,(17,20))
    three=r3.PolyCurve()
    for part in (a,b,third):check(three.AppendSegment(part),'append independent near-coincident segments')
    unchanged=data(three.SegmentCurve(2))
    result,message=bridge.remove_control_points(three,[3])
    check(result is not None and data(result.SegmentCurve(1))==unchanged,
          'near-coincident untouched segment retains every native CV/knot exactly')
    before=mixed.Encode()
    result,message=bridge.remove_control_points(mixed,[2,3])
    check(result is None and 'one at a time' in message and mixed.Encode()==before,
          'unqualified mixed-degree batch mapping is explicitly refused, never guessed')
    straight=joined(r3.LineCurve(r3.Point3d(0,0,0),r3.Point3d(1,1,0)),
                    r3.LineCurve(r3.Point3d(1,1,0),r3.Point3d(2,0,0)))
    result,_=bridge.remove_control_points(straight,[1])
    check(isinstance(result,r3.LineCurve) and result.PointAtEnd==r3.Point3d(2,0,0),
          'existing straight-joint behavior is unchanged')
    result,_=bridge.remove_control_points(a,[1])
    check(result is not None and result.Degree==2 and len(result.Points)==3,
          'existing non-joint clamped NURBS deletion is unchanged')
    with patch.object(native_pure,'remove_join_cv',return_value=None):
        result,message=bridge.remove_control_points(curve,[3])
        check(result is None and 'native' in message,'missing/refused Rust policy does not fall back to Python')
    if '--fixture-file' in sys.argv:
        path=sys.argv[sys.argv.index('--fixture-file')+1]
        file=r3.File3dm.Read(path)
        private=next(o.Geometry for o in file.Objects if o.Attributes.Name=='Copy134')
        leaves,grips=bridge._polycurve_grips(private)
        joints=[i for i,owners in enumerate(grips) if len(owners)==2
                and any(not bridge._straight_leaf(leaves[k]) for k,_ in owners)]
        check(bool(joints),'private fixture actually contains curved joints')
        for index in joints:
            old=private.Encode();result,message=bridge.remove_control_points(private,[index])
            check(result is not None,f'private curved joint {index}: {message}')
            check(result.IsValid and result.IsClosed==private.IsClosed,'private joint remains exact, valid and closed')
            check(private.Encode()==old,'private source is protected')
            owners=grips[index];changed={k for k,_ in owners}
            unchanged=[data(lf) for k,lf in enumerate(leaves) if k not in changed]
            result_data=[data(lf) for lf in bridge._polycurve_leaves(result)]
            check(all(record in result_data for record in unchanged),f'every untouched private leaf stays exact at joint {index}; first missing={next((r[:3] for r in unchanged if r not in result_data),None)}')
            host_delete(private,index,units=file.Settings.ModelUnitSystem)
        print(f'PRIVATE Copy134: {len(joints)} curved joints, actual Delete/Undo/Redo/save GREEN',flush=True)
    print(f'POLYCURVE JOINT DELETE PASSED ({checks} checks)',flush=True)

if __name__=='__main__':main()
