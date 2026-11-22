// SPDX-License-Identifier: GPL-3.0-or-later
// Separate from local-edit reconstruction: no cap, fuse, surface approximation,
// shape healing or boundary extension is permitted here.
#include <Geom2dConvert.hxx>
#include <Geom2d_TrimmedCurve.hxx>
namespace {
struct JoinRefusal {};
struct JoinTrace {
  std::vector<std::vector<std::uint32_t>> groups;
  std::vector<std::uint32_t> closed;
  std::uint32_t input_count=0;
};
struct JoinReport {
  std::uint32_t struct_size,version;
  std::int32_t status;
  std::uint32_t input_count,group_count;
  std::uint32_t offsets[257],members[256],closed[256];
};
std::string join_binding_bytes(TopoDS_Shape shape) {
  exact_shape_budget(shape,4096);
  // BinTools includes non-geometric ownership and analysis cache flags. Clear
  // only these on private decoded/rebuilt shapes; retain closure/orientation,
  // incidence, tolerances, geometry and every numeric coefficient exactly.
  ShapeMap parts; TopExp::MapShapes(shape,parts);
  for (int i=1;i<=parts.Extent();++i) {
    auto part=parts.FindKey(i); part.Free(false); part.Checked(false); part.Modified(false);
  }
  return serialize_shape(shape);
}
bool join_same_trims(const TopoDS_Face& a,const TopoDS_Face& b) {
  std::vector<TopoDS_Edge> edges;
  for (TopExp_Explorer ex(b,TopAbs_EDGE);ex.More();ex.Next()) edges.push_back(TopoDS::Edge(ex.Current()));
  std::vector<bool> used(edges.size(),false);
  std::size_t count=0;
  for (TopExp_Explorer ex(a,TopAbs_EDGE);ex.More();ex.Next()) {
    ++count; const auto old=TopoDS::Edge(ex.Current());
    double first,last; const auto curve=BRep_Tool::CurveOnSurface(old,a,first,last);
    if (curve.IsNull()) return false;
    bool found=false;
    for (std::size_t i=0;i<edges.size();++i) {
      if (used[i]) continue;
      double f,l; const auto c=BRep_Tool::CurveOnSurface(edges[i],b,f,l);
      bool same=curve==c;
      if (!same && !c.IsNull() && first==f && last==l) {
        const auto x=Geom2dConvert::CurveToBSplineCurve(new Geom2d_TrimmedCurve(curve,first,last));
        const auto y=Geom2dConvert::CurveToBSplineCurve(new Geom2d_TrimmedCurve(c,f,l));
        same=x->Degree()==y->Degree() && x->NbPoles()==y->NbPoles() && x->NbKnots()==y->NbKnots();
        for (int k=1;same && k<=x->NbPoles();++k)
          same=x->Pole(k).SquareDistance(y->Pole(k))==0. && x->Weight(k)==y->Weight(k);
        for (int k=1;same && k<=x->NbKnots();++k)
          same=x->Knot(k)==y->Knot(k) && x->Multiplicity(k)==y->Multiplicity(k);
      }
      if (same && first==f && last==l && old.Orientation()==edges[i].Orientation()) {
        used[i]=true; found=true; break;
      }
    }
    if (!found) return false;
  }
  return count==edges.size();
}
// OURS conservative, unit-covariant Join guard, not reference-application semantic equivalence.
// Keep a sewing neighborhood smaller than the operand's features. Arc length is
// OCCT numerical integration, not a formal interval proof. Surface/trim identity
// and validity gates below remain mandatory regardless of this admission bound.
void join_check_feature_budget(const TopoDS_Shape& part,double tolerance) {
  for (TopExp_Explorer ex(part,TopAbs_FACE);ex.More();ex.Next()) {
    Bnd_Box bounds; BRepBndLib::Add(ex.Current(),bounds,false);
    if (bounds.IsVoid() || bounds.IsOpen()) throw JoinRefusal{};
    double a,b,c,x,y,z; bounds.Get(a,b,c,x,y,z);
    const double scale=std::max({x-a,y-b,z-c});
    if (!std::isfinite(scale) || scale<=0. || tolerance>scale/4.) throw JoinRefusal{};
  }
  for (TopExp_Explorer ex(part,TopAbs_EDGE);ex.More();ex.Next()) {
    const auto edge=TopoDS::Edge(ex.Current());
    if (BRep_Tool::Degenerated(edge)) continue;
    GProp_GProps properties; BRepGProp::LinearProperties(edge,properties);
    const double length=properties.Mass();
    if (!std::isfinite(length) || length<=0. || tolerance>length/4.) throw JoinRefusal{};
  }
}
TopoDS_Shape join_exact_candidate(const TopoDS_Shape& input, double tolerance, JoinTrace& trace) {
  exact_shape_budget(input,4096);
  if (input.ShapeType()!=TopAbs_COMPOUND) throw JoinRefusal{};
  BRep_Builder builder;
  TopoDS_Compound output; builder.MakeCompound(output);
  std::vector<TopoDS_Face> source;
  std::vector<std::uint32_t> source_owners;
  std::size_t part_count=0;
  std::vector<TopoDS_Shape> originals;
  std::size_t edge_budget=0,vertex_budget=0,face_budget=0;
  BRepBuilderAPI_Sewing sewing(tolerance, true, true, true, true);
  sewing.SetLocalTolerancesMode(false);
  sewing.SetMaxTolerance(tolerance);
  sewing.SetMinTolerance(std::min(Precision::Confusion(),tolerance));
  for (TopoDS_Iterator it(input);it.More();it.Next()) {
    if (++part_count>256) throw JoinRefusal{};
    for (const auto& previous:originals) if (previous.IsSame(it.Value())) throw JoinRefusal{};
    originals.push_back(it.Value());
    ShapeMap edge_map,vertex_map,face_map;
    TopExp::MapShapes(it.Value(),TopAbs_EDGE,edge_map);
    TopExp::MapShapes(it.Value(),TopAbs_VERTEX,vertex_map);
    TopExp::MapShapes(it.Value(),TopAbs_FACE,face_map);
    edge_budget+=edge_map.Extent(); vertex_budget+=vertex_map.Extent(); face_budget+=face_map.Extent();
    if (edge_budget>4096 || vertex_budget>8192 || face_budget>4096) throw JoinRefusal{};
    // Isolate source topology/geometry per operand. This also removes incidental
    // inter-operand handle sharing from the exact persistent binding payload.
    const auto part=BRepBuilderAPI_Copy(it.Value(),true,false).Shape();
    for (TopExp_Explorer ex(part,TopAbs_EDGE);ex.More();ex.Next())
      if (BRep_Tool::Tolerance(TopoDS::Edge(ex.Current()))>tolerance) throw JoinRefusal{};
    for (TopExp_Explorer ex(part,TopAbs_VERTEX);ex.More();ex.Next())
      if (BRep_Tool::Tolerance(TopoDS::Vertex(ex.Current()))>tolerance) throw JoinRefusal{};
    Bnd_Box bounds; BRepBndLib::Add(part,bounds,false);
    if (bounds.IsVoid() || bounds.IsOpen()) throw JoinRefusal{};
    double xmin,ymin,zmin,xmax,ymax,zmax; bounds.Get(xmin,ymin,zmin,xmax,ymax,zmax);
    for (double value:{xmin,ymin,zmin,xmax,ymax,zmax}) if (!std::isfinite(value)) throw JoinRefusal{};
    for (TopExp_Explorer ex(part,TopAbs_FACE);ex.More();ex.Next())
      if (BRep_Tool::Tolerance(TopoDS::Face(ex.Current()))>tolerance) throw JoinRefusal{};
    join_check_feature_budget(part,tolerance);
    if (!BRepCheck_Analyzer(part,true).IsValid()) throw JoinRefusal{};
    if (part.ShapeType()==TopAbs_SOLID) {
      if (!is_airtight(part) || !BRepAlgoAPI_Check(part).IsValid()) throw JoinRefusal{};
      trace.groups.push_back({static_cast<std::uint32_t>(part_count-1)});
      trace.closed.push_back(1);
      builder.Add(output,part); // Join is not BooleanUnion, even for touching solids.
      continue;
    }
    if (part.ShapeType()!=TopAbs_FACE && part.ShapeType()!=TopAbs_SHELL) throw JoinRefusal{};
    for (TopExp_Explorer ex(part,TopAbs_FACE);ex.More();ex.Next()) {
      const auto face=TopoDS::Face(ex.Current());
      if (source.size()>=256) throw JoinRefusal{};
      for (const auto& old:source) if (old.IsSame(face)) throw JoinRefusal{};
      source.push_back(face);
      source_owners.push_back(static_cast<std::uint32_t>(part_count-1));
      sewing.Add(face);
    }
  }
  trace.input_count=static_cast<std::uint32_t>(part_count);
  if (part_count==0) throw JoinRefusal{};
  if (source.empty()) return output;
  sewing.Perform();
  if (sewing.NbMultipleEdges()!=0 || sewing.NbDeletedFaces()!=0) throw JoinRefusal{};
  const auto result=sewing.SewedShape();
  if (result.IsNull()) throw JoinRefusal{};
  exact_shape_budget(result,4096);
  std::vector<TopoDS_Face> faces;
  for (TopExp_Explorer ex(result,TopAbs_FACE);ex.More();ex.Next()) faces.push_back(TopoDS::Face(ex.Current()));
  if (faces.size()!=source.size()) throw JoinRefusal{};
  // One-to-one unchanged underlying surfaces, locations, UV bounds and orientation.
  // Sewing may share boundary topology but must not reconstruct a surface or trim domain.
  std::vector<bool> used(faces.size(),false);
  std::vector<std::uint32_t> face_owners(faces.size(),256);
  for (std::size_t source_index=0;source_index<source.size();++source_index) {
    const auto& old=source[source_index];
    bool matched=false;
    for (std::size_t i=0;i<faces.size();++i) {
      if (used[i]) continue;
      TopLoc_Location a,b;
      if (BRep_Tool::Surface(old,a)!=BRep_Tool::Surface(faces[i],b) || a!=b) continue;
      double u0,u1,v0,v1,x0,x1,y0,y1;
      BRepTools::UVBounds(old,u0,u1,v0,v1); BRepTools::UVBounds(faces[i],x0,x1,y0,y1);
      if (u0!=x0 || u1!=x1 || v0!=y0 || v1!=y1 || old.Orientation()!=faces[i].Orientation()) continue;
      if (!join_same_trims(old,faces[i])) continue;
      used[i]=true; face_owners[i]=source_owners[source_index]; matched=true; break;
    }
    if (!matched) throw JoinRefusal{};
  }
  auto publish=[&](const TopoDS_Shape& group) {
    std::vector<std::uint32_t> members;
    for (TopExp_Explorer ex(group,TopAbs_FACE);ex.More();ex.Next()) {
      bool found=false;
      for (std::size_t i=0;i<faces.size();++i) if (faces[i].IsSame(ex.Current())) {
        members.push_back(face_owners[i]); found=true; break;
      }
      if (!found) throw JoinRefusal{};
    }
    std::sort(members.begin(),members.end());
    members.erase(std::unique(members.begin(),members.end()),members.end());
    if (members.empty()) throw JoinRefusal{};
    for (auto id:members) for (const auto& previous:trace.groups)
      if (std::find(previous.begin(),previous.end(),id)!=previous.end()) throw JoinRefusal{};
    trace.groups.push_back(members);

    if (!BRepCheck_Analyzer(group,true).IsValid() || !BRepAlgoAPI_Check(group).IsValid()) throw JoinRefusal{};
    TopoDS_Shell shell;
    if (group.ShapeType()==TopAbs_FACE) {
      builder.MakeShell(shell); builder.Add(shell,group);
    } else if (group.ShapeType()==TopAbs_SHELL) shell=TopoDS::Shell(group);
    else throw JoinRefusal{};
    if (BRep_Tool::IsClosed(shell)) {
      TopoDS_Shape solid=BRepBuilderAPI_MakeSolid(shell).Solid();
      if (shape_volume(solid)<0.) solid.Reverse();
      if (!is_airtight(solid) || !BRepAlgoAPI_Check(solid).IsValid()) throw JoinRefusal{};
      builder.Add(output,solid); trace.closed.push_back(1);
    } else { builder.Add(output,group); trace.closed.push_back(0); }
  };
  if (result.ShapeType()==TopAbs_COMPOUND) {
    for (TopoDS_Iterator it(result);it.More();it.Next()) publish(it.Value());
  } else publish(result);
  return output;
}
}
extern "C" std::int32_t narwhal_occt_adapter_join_exact(
    const std::uint8_t* input,std::size_t length,double tolerance,
    std::uint8_t* output,std::size_t capacity,std::size_t* required) noexcept {
  try {
    const auto shape=deserialize_shape(input,length);
    JoinTrace trace;
    const auto result=join_exact_candidate(shape,tolerance,trace);
    return copy_serialized(serialize_shape(result),output,capacity,required);
  } catch (const ExactBudgetExceeded&) { return kPolicyRefused;
  } catch (const JoinRefusal&) { return kPolicyRefused;
  } catch (const std::invalid_argument&) { return kMalformedBrep;
  } catch (const Standard_Failure&) { return kKernelFailure;
  } catch (...) { return kUnexpectedFailure; }
}
// Actual OCCT producers, serialized through the same boundary as SDK inputs.
extern "C" std::int32_t narwhal_occt_adapter_join_fixture(
    std::uint32_t kind,std::uint8_t* output,std::size_t capacity,std::size_t* required) noexcept {
  try {
    BRep_Builder b; TopoDS_Compound root; b.MakeCompound(root);
    if (kind==17) {
      BRepBuilderAPI_MakeWire wire;
      constexpr int count=4097;
      const double pi=std::acos(-1.);
      std::vector<gp_Pnt> vertices;
      for (int i=0;i<count;++i) vertices.emplace_back(std::cos(2.*pi*i/count),std::sin(2.*pi*i/count),0.);
      for (int i=0;i<count;++i) wire.Add(BRepBuilderAPI_MakeEdge(vertices[i],vertices[(i+1)%count]).Edge());
      b.Add(root,BRepBuilderAPI_MakeFace(wire.Wire(),true).Face());
      return copy_serialized(serialize_shape(root),output,capacity,required);
    }
    TopoDS_Shape shape=kind==1 ? BRepPrimAPI_MakeCylinder(2.,3.).Shape() : BRepPrimAPI_MakeBox(2.,3.,4.).Shape();
    if (kind==13) {
      gp_Trsf scale; scale.SetScale(gp_Pnt(0.,0.,0.),25.4);
      shape=BRepBuilderAPI_Transform(shape,scale,true).Shape();
    }
    if (kind==14) ShapeFix_ShapeTolerance().SetTolerance(shape,1e-9);
    int n=0;
    for (TopExp_Explorer ex(shape,TopAbs_FACE);ex.More();ex.Next(),++n) {
      if (kind==2 && n>=2) break;
      auto face=ex.Current();
      if (kind==5 && n==0) face.Reverse();
      if (kind<=2) face=BRepBuilderAPI_Copy(face,true,false).Shape();
      b.Add(root,face);
      if (kind==4 && n==0) b.Add(root,face);
    }
    if (kind==10) {
      b.MakeCompound(root);
      for (TopExp_Explorer ex(BRepPrimAPI_MakeTorus(3.,1.).Shape(),TopAbs_FACE);ex.More();ex.Next()) b.Add(root,ex.Current());
    }
    if (kind==6 || kind==11 || kind==12 || kind==15 || kind==16) {
      b.MakeCompound(root);
      b.Add(root,BRepBuilderAPI_MakeFace(gp_Pln(gp::XOY()),0.,1.,0.,1.).Face());
      const double gap=kind==11 ? .0199 : kind==12 ? .0201 : kind==15 ? .9e-9 : kind==16 ? 1.1e-9 : .00005;
      b.Add(root,BRepBuilderAPI_MakeFace(gp_Pln(gp::XOY()),1.+gap,2.,0.,1.).Face());
      if (kind==15 || kind==16) ShapeFix_ShapeTolerance().SetTolerance(root,1e-12);
    }
    if (kind==7) {
      b.MakeCompound(root);
      b.Add(root,BRepBuilderAPI_MakeFace(gp_Pln(gp::XOY()),0.,1.,0.,1.).Face());
      b.Add(root,BRepBuilderAPI_MakeFace(gp_Pln(gp::XOY()),1.,2.,0.,1.).Face());
      b.Add(root,BRepBuilderAPI_MakeFace(gp_Pln(gp_Ax3(gp_Pnt(1.,0.,0.),gp_Dir(1.,0.,0.),gp_Dir(0.,1.,0.))),0.,1.,0.,1.).Face());
    }
    if (kind==8) {
      b.MakeCompound(root);
      auto face=TopoDS::Face(TopExp_Explorer(shape,TopAbs_FACE).Current());
      b.Add(root,face); b.Add(root,BRepBuilderAPI_Copy(face,true,false).Shape());
    }
    if (kind==3 || kind==9) {
      // Existing unrelated solids must remain separate, not fused or sewn.
      b.MakeCompound(root);
      b.Add(root,shape); gp_Trsf t; t.SetTranslation(gp_Vec(kind==9 ? 2. : 10.,0.,0.));
      b.Add(root,BRepBuilderAPI_Transform(shape,t,true).Shape());
    }
    return copy_serialized(serialize_shape(root),output,capacity,required);
  } catch (...) { return kUnexpectedFailure; }
}

extern "C" std::int32_t narwhal_occt_adapter_join_stats(const std::uint8_t* input,std::size_t length,double* stats) noexcept {
  try {
    const auto shape=deserialize_shape(input,length);
    stats[0]=stats[1]=stats[2]=0.;
    for (TopoDS_Iterator it(shape);it.More();it.Next()) ++stats[0];
    for (TopExp_Explorer ex(shape,TopAbs_SOLID);ex.More();ex.Next()) ++stats[1];
    GProp_GProps props; BRepGProp::SurfaceProperties(shape,props); stats[2]=props.Mass();
    return 0;
  } catch (...) { return kUnexpectedFailure; }
}

// Qualify the exact serialized result after a fresh decode and native transform.
extern "C" std::int32_t narwhal_occt_adapter_join_roundtrip(const std::uint8_t* input,std::size_t length) noexcept {
  try {
    auto root=deserialize_shape(input,length);
    TopoDS_Iterator it(root);
    if (!it.More() || it.Value().ShapeType()!=TopAbs_SOLID) return kPolicyRefused;
    auto solid=it.Value(); it.Next(); if (it.More()) return kPolicyRefused;
    gp_Trsf transform; transform.SetTranslation(gp_Vec(100.,0.,0.));
    auto moved=BRepBuilderAPI_Transform(solid,transform,true).Shape();
    const auto encoded=serialize_shape(moved);
    moved=deserialize_shape(reinterpret_cast<const std::uint8_t*>(encoded.data()),encoded.size());
    const auto inspection=inspect_shape(moved);
    GProp_GProps properties; BRepGProp::VolumeProperties(moved,properties);
    if (!inspection.valid || !inspection.airtight || inspection.face_count!=3
        || inspection.cylinder_faces!=1 || inspection.plane_faces!=2
        || std::abs(inspection.volume-12.*std::acos(-1.))>1e-9
        || std::abs(properties.CentreOfMass().X()-100.)>1e-10) return kPolicyRefused;
    // A downstream exact boolean must consume the solid, not a display proxy.
    BRepAlgoAPI_Fuse fuse(moved,BRepPrimAPI_MakeBox(gp_Pnt(100.,0.,0.5),1.,1.,1.).Shape());
    fuse.Build();
    if (!fuse.IsDone() || !is_airtight(fuse.Shape()) || !BRepAlgoAPI_Check(fuse.Shape()).IsValid()) return kPolicyRefused;
    const auto after=inspect_shape(fuse.Shape());
    return after.cylinder_faces==1 && after.face_count==3 ? kOk : kPolicyRefused;
  } catch (...) { return kUnexpectedFailure; }
}

extern "C" std::int32_t narwhal_occt_adapter_join_mapped(
    const std::uint8_t* input,std::size_t length,double tolerance,
    std::uint8_t* output,std::size_t capacity,std::size_t* required,JoinReport* report) noexcept {
  try {
    JoinTrace trace;
    const auto result=join_exact_candidate(deserialize_shape(input,length),tolerance,trace);
    JoinReport info{}; info.struct_size=sizeof(info); info.version=1;
    info.input_count=trace.input_count; info.group_count=static_cast<std::uint32_t>(trace.groups.size());
    std::size_t n=0;
    if (trace.groups.size()>256) throw JoinRefusal{};
    for (std::size_t g=0;g<trace.groups.size();++g) {
      info.offsets[g]=static_cast<std::uint32_t>(n); info.closed[g]=trace.closed[g];
      for (auto member:trace.groups[g]) {
        if (n>=256) throw JoinRefusal{};
        info.members[n++]=member;
      }
    }
    info.offsets[trace.groups.size()]=static_cast<std::uint32_t>(n);
    if (n!=trace.input_count) throw JoinRefusal{};
    const auto status=copy_serialized(serialize_shape(result),output,capacity,required);
    *report=info; return status;
  } catch (const ExactBudgetExceeded&) { return kPolicyRefused;
  } catch (const JoinRefusal&) { return kPolicyRefused;
  } catch (const std::invalid_argument&) { return kMalformedBrep;
  } catch (const Standard_Failure&) { return kKernelFailure;
  } catch (...) { return kUnexpectedFailure; }
}
extern "C" std::int32_t narwhal_occt_adapter_verify_join(
    const std::uint8_t* input,std::size_t length,const std::uint8_t* target,std::size_t target_length,double tolerance) noexcept {
  try {
    JoinTrace trace;
    const auto candidate=join_exact_candidate(deserialize_shape(input,length),tolerance,trace);
    if (trace.groups.size()!=1 || trace.groups[0].size()!=trace.input_count || trace.input_count<2) throw JoinRefusal{};
    TopoDS_Iterator it(candidate);
    if (!it.More()) throw JoinRefusal{};
    const auto expected=deserialize_shape(target,target_length);
    // A frozen result is accepted only when the rebuilt operand tree reproduces
    // exactly its canonical BinTools payload. No nearest face/group ordinal matching.
    if (join_binding_bytes(it.Value())!=join_binding_bytes(expected)) throw JoinRefusal{};
    return kOk;
  } catch (const ExactBudgetExceeded&) { return kPolicyRefused;
  } catch (const JoinRefusal&) { return kPolicyRefused;
  } catch (const std::invalid_argument&) { return kMalformedBrep;
  } catch (const Standard_Failure&) { return kKernelFailure;
  } catch (...) { return kUnexpectedFailure; }
}

extern "C" std::int32_t narwhal_occt_adapter_join_first_group(
    const std::uint8_t* input,std::size_t length,std::uint8_t* output,std::size_t capacity,std::size_t* required) noexcept {
  try {
    const auto root=deserialize_shape(input,length); TopoDS_Iterator it(root);
    if (!it.More()) return kInvalidArgument;
    return copy_serialized(serialize_shape(it.Value()),output,capacity,required);
  } catch (...) {return kUnexpectedFailure;}
}
