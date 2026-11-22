// SPDX-License-Identifier: GPL-3.0-or-later
// Post-decode admission, NOT a BinTools allocation firewall. Untrusted BinTools
// decoding must run in the Rust-supervised process (see cap_worker.py).
#include <BRep_TEdge.hxx>
#include <BRep_TVertex.hxx>
#include <BRep_PointRepresentation.hxx>
#include <BRep_CurveRepresentation.hxx>
#include <Geom2d_BSplineCurve.hxx>
#include <Geom2d_BezierCurve.hxx>
#include <Geom2d_TrimmedCurve.hxx>
#include <Geom2d_OffsetCurve.hxx>
#include <Geom2d_Conic.hxx>
#include <Geom2d_Line.hxx>
#include <Geom_TrimmedCurve.hxx>
#include <Geom_OffsetCurve.hxx>
#include <Geom_Conic.hxx>
#include <Geom_Line.hxx>
#include <Geom_RectangularTrimmedSurface.hxx>
#include <Geom_OffsetSurface.hxx>
#include <Geom_SweptSurface.hxx>
#include <Geom_ElementarySurface.hxx>
#include <set>
namespace {
struct ExactBudgetExceeded {};
struct ExactBudget {
  std::uint64_t poles=0,knots=0,representations=0;
  std::set<const void*> seen;
  void spline(std::uint64_t p,std::uint64_t k,int degree) {
    poles+=p; knots+=k;
    if (p>4096 || poles>65536 || knots>16384 || degree>25) throw ExactBudgetExceeded{};
  }
  void curve2d(const Handle(Geom2d_Curve)& c,unsigned depth=0) {
    if (c.IsNull() || depth>8) throw ExactBudgetExceeded{};
    if (!seen.insert(c.get()).second) return;
    if (auto b=Handle(Geom2d_BSplineCurve)::DownCast(c); !b.IsNull()) spline(b->NbPoles(),b->NbKnots(),b->Degree());
    else if (auto b=Handle(Geom2d_BezierCurve)::DownCast(c); !b.IsNull()) spline(b->NbPoles(),0,b->Degree());
    else if (auto b=Handle(Geom2d_TrimmedCurve)::DownCast(c); !b.IsNull()) curve2d(b->BasisCurve(),depth+1);
    else if (auto b=Handle(Geom2d_OffsetCurve)::DownCast(c); !b.IsNull()) curve2d(b->BasisCurve(),depth+1);
    else if (!c->IsKind(STANDARD_TYPE(Geom2d_Conic)) && !c->IsKind(STANDARD_TYPE(Geom2d_Line))) throw ExactBudgetExceeded{};
  }
  void curve3d(const Handle(Geom_Curve)& c,unsigned depth=0) {
    if (c.IsNull() || depth>8) throw ExactBudgetExceeded{};
    if (!seen.insert(c.get()).second) return;
    if (auto b=Handle(Geom_BSplineCurve)::DownCast(c); !b.IsNull()) spline(b->NbPoles(),b->NbKnots(),b->Degree());
    else if (auto b=Handle(Geom_BezierCurve)::DownCast(c); !b.IsNull()) spline(b->NbPoles(),0,b->Degree());
    else if (auto b=Handle(Geom_TrimmedCurve)::DownCast(c); !b.IsNull()) curve3d(b->BasisCurve(),depth+1);
    else if (auto b=Handle(Geom_OffsetCurve)::DownCast(c); !b.IsNull()) curve3d(b->BasisCurve(),depth+1);
    else if (!c->IsKind(STANDARD_TYPE(Geom_Conic)) && !c->IsKind(STANDARD_TYPE(Geom_Line))) throw ExactBudgetExceeded{};
  }
  void surface(const Handle(Geom_Surface)& c,unsigned depth=0) {
    if (c.IsNull() || depth>8) throw ExactBudgetExceeded{};
    if (!seen.insert(c.get()).second) return;
    if (auto b=Handle(Geom_BSplineSurface)::DownCast(c); !b.IsNull()) {
      poles+=static_cast<std::uint64_t>(b->NbUPoles())*b->NbVPoles(); knots+=b->NbUKnots()+b->NbVKnots();
      if (b->NbUPoles()>4096 || b->NbVPoles()>4096 || b->UDegree()>25 || b->VDegree()>25) throw ExactBudgetExceeded{};
    } else if (auto b=Handle(Geom_BezierSurface)::DownCast(c); !b.IsNull()) {
      poles+=static_cast<std::uint64_t>(b->NbUPoles())*b->NbVPoles();
      if (b->UDegree()>25 || b->VDegree()>25) throw ExactBudgetExceeded{};
    } else if (auto b=Handle(Geom_RectangularTrimmedSurface)::DownCast(c); !b.IsNull()) surface(b->BasisSurface(),depth+1);
    else if (auto b=Handle(Geom_OffsetSurface)::DownCast(c); !b.IsNull()) surface(b->BasisSurface(),depth+1);
    else if (auto b=Handle(Geom_SweptSurface)::DownCast(c); !b.IsNull()) curve3d(b->BasisCurve(),depth+1);
    else if (!c->IsKind(STANDARD_TYPE(Geom_ElementarySurface))) throw ExactBudgetExceeded{};
    if (poles>65536 || knots>16384) throw ExactBudgetExceeded{};
  }
};
void exact_shape_budget(const TopoDS_Shape& root,std::size_t face_limit=256) {
  // Count OCCURRENCES before MapShapes, explorers or geometry adaptation. Never
  // recursively expand a DAG before checking its repeated-child budget.
  struct Frame { TopoDS_Iterator children; unsigned depth; };
  std::vector<Frame> stack;
  std::size_t nodes=0,faces=0,wires=0,edges=0;
  auto admit=[&](const TopoDS_Shape& s,unsigned depth) {
    if (depth>32 || ++nodes>32768) throw ExactBudgetExceeded{};
    if (s.ShapeType()==TopAbs_FACE && ++faces>face_limit) throw ExactBudgetExceeded{};
    if (s.ShapeType()==TopAbs_WIRE && ++wires>4096) throw ExactBudgetExceeded{};
    if (s.ShapeType()==TopAbs_EDGE && ++edges>16384) throw ExactBudgetExceeded{};
  };
  admit(root,0); stack.push_back({TopoDS_Iterator(root),0});
  while (!stack.empty()) {
    auto& frame=stack.back();
    if (!frame.children.More()) {stack.pop_back(); continue;}
    const auto child=frame.children.Value(); const auto depth=frame.depth+1;
    frame.children.Next(); admit(child,depth); stack.push_back({TopoDS_Iterator(child),depth});
  }
  ShapeMap fm,em,vm;
  TopExp::MapShapes(root,TopAbs_FACE,fm); TopExp::MapShapes(root,TopAbs_EDGE,em); TopExp::MapShapes(root,TopAbs_VERTEX,vm);
  if (static_cast<std::size_t>(fm.Extent())>face_limit || em.Extent()>4096 || vm.Extent()>8192) throw ExactBudgetExceeded{};
  ExactBudget budget;
  for (int i=1;i<=fm.Extent();++i) {
    TopLoc_Location location;
    budget.surface(BRep_Tool::Surface(TopoDS::Face(fm(i)),location));
  }
  for (int i=1;i<=em.Extent();++i) {
    const auto te=Handle(BRep_TEdge)::DownCast(em(i).TShape());
    if (te.IsNull()) throw ExactBudgetExceeded{};
    for (const auto& representation:te->Curves()) {
      if (++budget.representations>16384 || representation.IsNull()) throw ExactBudgetExceeded{};
      if (representation->IsCurve3D()) budget.curve3d(representation->Curve3D());
      if (representation->IsRegularity()) {
        budget.surface(representation->Surface()); budget.surface(representation->Surface2());
      }
      if (representation->IsCurveOnSurface()) {
        budget.surface(representation->Surface()); budget.curve2d(representation->PCurve());
        if (representation->IsCurveOnClosedSurface()) budget.curve2d(representation->PCurve2());
      }
    }
  }
  for (int i=1;i<=vm.Extent();++i) {
    const auto tv=Handle(BRep_TVertex)::DownCast(vm(i).TShape());
    if (tv.IsNull()) throw ExactBudgetExceeded{};
    for (const auto& point:tv->Points()) {
      if (++budget.representations>16384 || point.IsNull()) throw ExactBudgetExceeded{};
      if (point->IsPointOnCurve()) budget.curve3d(point->Curve());
      if (point->IsPointOnCurveOnSurface()) budget.curve2d(point->PCurve());
      if (point->IsPointOnCurveOnSurface() || point->IsPointOnSurface()) budget.surface(point->Surface());
    }
  }
}
}
