// SPDX-License-Identifier: GPL-3.0-or-later
// OCCT-only boundary. Admission/transport policy lives in cap.rs. No remeshing,
// surface fitting, healing or replacement of source faces is allowed.
#include <BRepBuilderAPI_FindPlane.hxx>
#include <ShapeAnalysis_FreeBounds.hxx>
#include <Geom_Plane.hxx>
namespace {
struct CapRefusal {};
struct CapLimit {};
TopoDS_Shape cap_exact_candidate(const TopoDS_Shape& input, double tolerance) {
  if (input.ShapeType()!=TopAbs_FACE && input.ShapeType()!=TopAbs_SHELL && input.ShapeType()!=TopAbs_SOLID)
    throw CapRefusal{};
  exact_shape_budget(input);
  ShapeMap fm,em,vm;
  TopExp::MapShapes(input,TopAbs_FACE,fm); TopExp::MapShapes(input,TopAbs_EDGE,em);
  TopExp::MapShapes(input,TopAbs_VERTEX,vm);
  if (fm.Extent()<1) throw CapRefusal{};
  if (fm.Extent()>256 || em.Extent()>4096 || vm.Extent()>8192) throw CapLimit{};
  for (int i=1;i<=fm.Extent();++i)
    if (BRep_Tool::Tolerance(TopoDS::Face(fm(i)))>tolerance) throw CapRefusal{};
  for (int i=1;i<=em.Extent();++i)
    if (BRep_Tool::Tolerance(TopoDS::Edge(em(i)))>tolerance) throw CapRefusal{};
  for (int i=1;i<=vm.Extent();++i)
    if (BRep_Tool::Tolerance(TopoDS::Vertex(vm(i)))>tolerance) throw CapRefusal{};
  join_check_feature_budget(input,tolerance);
  // All kernel operations are confined to a private decoded/copied shape.
  const auto source=BRepBuilderAPI_Copy(input,true,false).Shape();
  if (!BRepCheck_Analyzer(source,true).IsValid() || !BRepAlgoAPI_Check(source).IsValid()) throw CapRefusal{};
  if (source.ShapeType()==TopAbs_SOLID) {
    if (!is_airtight(source) || shape_volume(source)<=0.) throw CapRefusal{};
    return source;
  }
  ShapeAnalysis_FreeBounds bounds(source);
  if (TopExp_Explorer(bounds.GetOpenWires(),TopAbs_WIRE).More()) throw CapRefusal{};
  std::vector<TopoDS_Wire> wires;
  std::vector<TopoDS_Face> disks;
  for (TopExp_Explorer ex(bounds.GetClosedWires(),TopAbs_WIRE);ex.More();ex.Next()) {
    if (wires.size()>=64) throw CapLimit{};
    auto wire=TopoDS::Wire(ex.Current());
    BRepBuilderAPI_FindPlane plane(wire,tolerance);
    if (!plane.Found()) throw CapRefusal{};
    BRepBuilderAPI_MakeFace maker(plane.Plane()->Pln(),wire,true);
    if (!maker.IsDone() || !BRepCheck_Analyzer(maker.Face(),true).IsValid()
        || !BRepAlgoAPI_Check(maker.Face()).IsValid()) throw CapRefusal{};
    wires.push_back(BRepTools::OuterWire(maker.Face())); disks.push_back(maker.Face());
  }
  if (wires.empty()) throw CapRefusal{};
  // Strictly disjoint coplanar loops form a containment forest. ON, touching,
  // crossing, coincident or otherwise ambiguous regions are never guessed.
  const auto n=wires.size();
  std::vector<std::vector<bool>> inside(n,std::vector<bool>(n,false));
  BRep_Builder builder;
  for (std::size_t i=0;i<n;++i) for (std::size_t j=i+1;j<n;++j) {
    TopoDS_Compound pair; builder.MakeCompound(pair); builder.Add(pair,wires[i]); builder.Add(pair,wires[j]);
    BRepBuilderAPI_FindPlane common(pair,tolerance);
    if (!common.Found()) continue;
    BRepExtrema_DistShapeShape distance(wires[i],wires[j]); distance.Perform();
    if (!distance.IsDone() || !std::isfinite(distance.Value()) || distance.Value()<=tolerance) throw CapRefusal{};
    auto contained=[&](std::size_t a,std::size_t b) {
      TopExp_Explorer v(wires[a],TopAbs_VERTEX); if (!v.More()) throw CapRefusal{};
      BRepClass_FaceClassifier classifier(disks[b],BRep_Tool::Pnt(TopoDS::Vertex(v.Current())),tolerance);
      if (classifier.State()!=TopAbs_IN && classifier.State()!=TopAbs_OUT) throw CapRefusal{};
      return classifier.State()==TopAbs_IN;
    };
    inside[i][j]=contained(i,j); inside[j][i]=contained(j,i);
    if (inside[i][j] && inside[j][i]) throw CapRefusal{};
  }
  std::vector<int> depth(n,0),parent(n,-1);
  for (std::size_t i=0;i<n;++i) for (std::size_t j=0;j<n;++j) if (inside[i][j]) ++depth[i];
  for (std::size_t i=0;i<n;++i) if (depth[i]) {
    for (std::size_t j=0;j<n;++j) if (inside[i][j] && depth[j]==depth[i]-1) {
      if (parent[i]!=-1) throw CapRefusal{};
      parent[i]=static_cast<int>(j);
    }
    if (parent[i]==-1) throw CapRefusal{};
  }
  std::vector<TopoDS_Face> lids;
  for (std::size_t i=0;i<n;++i) if (depth[i]%2==0) {
    BRepAdaptor_Surface surface(disks[i]);
    BRepBuilderAPI_MakeFace maker(surface.Plane(),wires[i],true);
    for (std::size_t j=0;j<n;++j) if (parent[j]==static_cast<int>(i)) {
      // Rebuild each inner wire on the SAME plane before reversing, rather
      // than relying on the arbitrary normal found for that wire in isolation.
      BRepBuilderAPI_MakeFace inner(surface.Plane(),wires[j],true);
      if (!inner.IsDone()) throw CapRefusal{};
      maker.Add(TopoDS::Wire(BRepTools::OuterWire(inner.Face()).Reversed()));
    }
    if (!maker.IsDone() || !BRepCheck_Analyzer(maker.Face(),true).IsValid()
        || !BRepAlgoAPI_Check(maker.Face()).IsValid()) throw CapRefusal{};
    lids.push_back(maker.Face());
  }
  BRepBuilderAPI_Sewing sew(tolerance,true,true,true,false);
  sew.SetLocalTolerancesMode(false); sew.SetMaxTolerance(tolerance);
  sew.SetMinTolerance(std::min(Precision::Confusion(),tolerance)); sew.Add(source);
  for (const auto& lid:lids) sew.Add(lid);
  sew.Perform();
  const auto joined=sew.SewedShape();
  if (joined.IsNull() || joined.ShapeType()!=TopAbs_SHELL || sew.NbFreeEdges()!=0
      || sew.NbMultipleEdges()!=0 || sew.NbDeletedFaces()!=0) throw CapRefusal{};
  exact_shape_budget(joined);
  std::vector<TopoDS_Face> faces;
  for (TopExp_Explorer ex(joined,TopAbs_FACE);ex.More();ex.Next()) faces.push_back(TopoDS::Face(ex.Current()));
  if (faces.size()!=static_cast<std::size_t>(fm.Extent())+lids.size()) throw CapRefusal{};
  std::vector<bool> used(faces.size(),false);
  for (TopExp_Explorer ex(source,TopAbs_FACE);ex.More();ex.Next()) {
    const auto old=TopoDS::Face(ex.Current()); bool found=false;
    for (std::size_t i=0;i<faces.size();++i) if (!used[i]) {
      TopLoc_Location a,b;
      if (BRep_Tool::Surface(old,a)!=BRep_Tool::Surface(faces[i],b) || a!=b) continue;
      double u,v,s,t,x,y,z,w;
      BRepTools::UVBounds(old,u,v,s,t); BRepTools::UVBounds(faces[i],x,y,z,w);
      if (u!=x || v!=y || s!=z || t!=w || !join_same_trims(old,faces[i])) continue;
      used[i]=true; found=true; break;
    }
    if (!found) throw CapRefusal{};
  }
  for (std::size_t i=0;i<faces.size();++i)
    if (!used[i] && BRepAdaptor_Surface(faces[i]).GetType()!=GeomAbs_Plane) throw CapRefusal{};
  TopoDS_Shape solid=BRepBuilderAPI_MakeSolid(TopoDS::Shell(joined)).Solid();
  if (shape_volume(solid)<0.) solid.Reverse();
  if (!is_airtight(solid) || !BRepCheck_Analyzer(solid,true).IsValid()
      || !BRepAlgoAPI_Check(solid).IsValid() || !std::isfinite(shape_volume(solid)) || shape_volume(solid)<=0.)
    throw CapRefusal{};
  return solid;
}
}
extern "C" std::int32_t narwhal_occt_adapter_cap_exact(
    const std::uint8_t* input,std::size_t length,double tolerance,
    std::uint8_t* output,std::size_t capacity,std::size_t* required) noexcept {
  try {
    return copy_serialized(serialize_shape(cap_exact_candidate(deserialize_shape(input,length),tolerance)),output,capacity,required);
  } catch (const ExactBudgetExceeded&) {return kLimitExceeded;
  } catch (const CapLimit&) {return kLimitExceeded;
  } catch (const CapRefusal&) {return kPolicyRefused;
  } catch (const JoinRefusal&) {return kPolicyRefused;
  } catch (const std::invalid_argument&) {return kMalformedBrep;
  } catch (const Standard_Failure&) {return kKernelFailure;
  } catch (...) {return kUnexpectedFailure;}
}
extern "C" std::int32_t narwhal_occt_adapter_verify_cap(
    const std::uint8_t* input,std::size_t length,const std::uint8_t* snapshot,std::size_t snapshot_length,
    const std::uint8_t* target,std::size_t target_length,double tolerance) noexcept {
  try {
    const auto source=deserialize_shape(input,length);
    const auto frozen_source=deserialize_shape(snapshot,snapshot_length);
    const auto frozen_result=deserialize_shape(target,target_length);
    exact_shape_budget(source); exact_shape_budget(frozen_source); exact_shape_budget(frozen_result);
    if (join_binding_bytes(source)!=join_binding_bytes(frozen_source)) throw CapRefusal{};
    if (join_binding_bytes(cap_exact_candidate(source,tolerance))!=join_binding_bytes(frozen_result)) throw CapRefusal{};
    return kOk;
  } catch (const ExactBudgetExceeded&) {return kLimitExceeded;
  } catch (const CapLimit&) {return kLimitExceeded;
  } catch (const CapRefusal&) {return kPolicyRefused;
  } catch (const JoinRefusal&) {return kPolicyRefused;
  } catch (const std::invalid_argument&) {return kMalformedBrep;
  } catch (const Standard_Failure&) {return kKernelFailure;
  } catch (...) {return kUnexpectedFailure;}
}

// Native fixtures are actual analytic OCCT topology, not Python geometry models.
#include <BRepAlgoAPI_Cut.hxx>
#include <Geom_BezierSurface.hxx>
#include <TColgp_Array2OfPnt.hxx>
#include <BRepBuilderAPI_MakePolygon.hxx>
#include <NCollection_Array1.hxx>
#include <BRep_PointOnCurveOnSurface.hxx>
extern "C" std::int32_t narwhal_occt_adapter_cap_fixture(
    std::uint32_t kind,std::uint8_t* output,std::size_t capacity,std::size_t* required) noexcept {
  try {
    TopoDS_Shape source;
    const auto cylinder=BRepPrimAPI_MakeCylinder(3.,5.).Shape();
    if (kind==7) {
      TColgp_Array2OfPnt poles(1,257,1,257);
      TColStd_Array1OfReal knots(1,257); TColStd_Array1OfInteger mults(1,257);
      for (int i=1;i<=257;++i) {
        knots.SetValue(i,i-1.); mults.SetValue(i,i==1 || i==257 ? 2 : 1);
        for (int j=1;j<=257;++j) poles.SetValue(i,j,gp_Pnt(i-1.,j-1.,0.));
      }
      source=BRepBuilderAPI_MakeFace(new Geom_BSplineSurface(poles,knots,knots,mults,mults,1,1),1e-7).Face();
    } else if (kind==6) {
      BRepBuilderAPI_MakePolygon polygon;
      for (const auto& p:{gp_Pnt(0,0,0),gp_Pnt(2,2,0),gp_Pnt(0,2,0),gp_Pnt(2,0,0)}) polygon.Add(p);
      polygon.Close(); source=BRepBuilderAPI_MakeFace(polygon.Wire(),true).Face();
    } else if (kind==3) source=cylinder;
    else if (kind==2) {
      TColgp_Array2OfPnt poles(1,2,1,2);
      poles.SetValue(1,1,gp_Pnt(0,0,0)); poles.SetValue(1,2,gp_Pnt(0,2,0));
      poles.SetValue(2,1,gp_Pnt(2,0,0)); poles.SetValue(2,2,gp_Pnt(2,2,1));
      source=BRepBuilderAPI_MakeFace(new Geom_BezierSurface(poles),1e-7).Face();
    } else if (kind==1) {
      const auto hollow=BRepAlgoAPI_Cut(cylinder,BRepPrimAPI_MakeCylinder(1.,5.).Shape()).Shape();
      BRepBuilderAPI_Sewing sew(1e-7);
      for (TopExp_Explorer ex(hollow,TopAbs_FACE);ex.More();ex.Next()) {
        GProp_GProps props; BRepGProp::SurfaceProperties(ex.Current(),props);
        if (BRepAdaptor_Surface(TopoDS::Face(ex.Current())).GetType()==GeomAbs_Plane
            && props.CentreOfMass().Z()>4.) continue;
        sew.Add(ex.Current());
      }
      sew.Perform(); source=sew.SewedShape();
    } else {
      for (TopExp_Explorer ex(cylinder,TopAbs_FACE);ex.More();ex.Next())
        if (BRepAdaptor_Surface(TopoDS::Face(ex.Current())).GetType()==GeomAbs_Cylinder) source=ex.Current();
      if (kind==4) {
        BRep_Builder b;
        for (TopExp_Explorer ex(source,TopAbs_VERTEX);ex.More();ex.Next()) b.UpdateVertex(TopoDS::Vertex(ex.Current()),.005);
      }
      if (kind==5) {
        TopoDS_Shell shell; BRep_Builder b; b.MakeShell(shell);
        for (int i=0;i<257;++i) b.Add(shell,BRepBuilderAPI_Copy(source,true,false).Shape());
        source=shell;
      }
    }
    if (kind==8 || kind==11 || kind==12) {
      NCollection_Array1<gp_Pnt2d> poles(1,5000);
      NCollection_Array1<double> knots(1,5000); NCollection_Array1<int> mults(1,5000);
      for (int i=1;i<=5000;++i) {poles.SetValue(i,gp_Pnt2d(0.,i-1.)); knots.SetValue(i,i-1.); mults.SetValue(i,i==1 || i==5000 ? 2 : 1);}
      const Handle(Geom2d_Curve) huge=new Geom2d_BSplineCurve(poles,knots,mults,1);
      const auto face=TopoDS::Face(source); BRep_Builder b;
      if (kind==12) {
        TopExp_Explorer vertices(face,TopAbs_VERTEX); TopLoc_Location loc;
        const auto surface=BRep_Tool::Surface(face,loc);
        Handle(BRep_TVertex)::DownCast(vertices.Current().TShape())->ChangePoints().Append(
            new BRep_PointOnCurveOnSurface(0.,huge,surface,loc));
      }
      for (TopExp_Explorer ex(face,TopAbs_EDGE);ex.More();ex.Next()) {
        const auto edge=TopoDS::Edge(ex.Current());
        if (!BRep_Tool::IsClosed(edge,face)) continue;
        double first,last; const auto small=BRep_Tool::CurveOnSurface(edge,face,first,last);
        if (kind==11) b.UpdateEdge(edge,small,huge,face,1e-7);
        else if (kind==8) b.UpdateEdge(edge,huge,small,face,1e-7);
        break;
      }
    }
    if (kind==13) {
      const auto face=TopoDS::Face(source); TopExp_Explorer vertices(face,TopAbs_VERTEX); TopLoc_Location loc;
      const auto surface=BRep_Tool::Surface(face,loc);
      auto vertex=Handle(BRep_TVertex)::DownCast(vertices.Current().TShape());
      for (int j=0;j<40;++j) {
        NCollection_Array1<gp_Pnt2d> poles(1,500); NCollection_Array1<double> knots(1,500); NCollection_Array1<int> mults(1,500);
        for (int i=1;i<=500;++i) {poles.SetValue(i,gp_Pnt2d(j,i-1.)); knots.SetValue(i,i-1.); mults.SetValue(i,i==1 || i==500 ? 2 : 1);}
        vertex->ChangePoints().Append(new BRep_PointOnCurveOnSurface(0.,new Geom2d_BSplineCurve(poles,knots,mults,1),surface,loc));
      }
    }
    if (kind==9 || kind==10) {
      const auto original=TopoDS::Face(source); BRep_Builder b; TopoDS_Face face;
      TopLoc_Location location; b.MakeFace(face,BRep_Tool::Surface(original,location),location,1e-7);
      TopExp_Explorer ex(original,TopAbs_WIRE); const auto wire=ex.Current();
      if (kind==9) {for (int i=0;i<4097;++i) b.Add(face,wire);}
      else {
        TopoDS_Wire repeated; b.MakeWire(repeated); TopExp_Explorer edge(wire,TopAbs_EDGE);
        for (int i=0;i<17000;++i) b.Add(repeated,edge.Current());
        b.Add(face,repeated);
      }
      source=face;
    }
    return copy_serialized(serialize_shape(source),output,capacity,required);
  } catch (...) {return kUnexpectedFailure;}
}
extern "C" std::int32_t narwhal_occt_adapter_cap_stats(
    const std::uint8_t* input,std::size_t length,double* stats) noexcept {
  try {
    const auto shape=deserialize_shape(input,length); ShapeMap faces;
    TopExp::MapShapes(shape,TopAbs_FACE,faces);
    stats[0]=faces.Extent(); stats[1]=shape_volume(shape);
    stats[2]=is_airtight(shape) && BRepCheck_Analyzer(shape,true).IsValid() && BRepAlgoAPI_Check(shape).IsValid();
    return kOk;
  } catch (...) {return kUnexpectedFailure;}
}
