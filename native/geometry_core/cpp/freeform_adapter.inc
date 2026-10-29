// SPDX-License-Identifier: GPL-3.0-or-later
// Exact OCCT handle interop. Rust owns interpolation and interval fold qualification.
#include "../include/narwhal_geometry_core.h"
#include <GeomAdaptor_Curve.hxx>
#include <GeomFill_CorrectedFrenet.hxx>
#include <GeomFill_CurveAndTrihedron.hxx>
#include <GeomFill_NSections.hxx>
#include <GeomFill_Sweep.hxx>
#include <GeomConvert_BSplineSurfaceToBezierSurface.hxx>
#include <Geom_BezierSurface.hxx>
#include <TopoDS_Iterator.hxx>

namespace {
struct FreeformRefusal { unsigned reason; };
using FreeformInterpolate = std::int32_t (*)(const double*, const double*, std::size_t, double*);
using FreeformQualify = std::int32_t (*)(const double*, const double*, std::size_t,
                                        const double*, const double*, NarwhalFreeformReportV1*);

// OCCT 8.0.1 NSections tests IsURational() (variation along V). For common
// profile weights varying only along U that wrongly says polynomial, and the
// approximator discards weights. Minimal-weight reporting has the same defect.
// Restrict this adapter to constant-along-V weights; inherited D1/D2 then agree.
class FreeformSections final : public GeomFill_NSections {
 public:
  using GeomFill_NSections::GeomFill_NSections;
  bool IsRational() const override {
    const auto s = BSplineSurface();
    return s->IsURational() || s->IsVRational();
  }
  void GetMinimalWeight(NCollection_Array1<double>& out) const override {
    const auto s = BSplineSurface();
    for (int u=1; u<=s->NbUPoles(); ++u) {
      double w = s->Weight(u,1);
      for (int v=2; v<=s->NbVPoles(); ++v) w = std::min(w,s->Weight(u,v));
      out(u)=w;
    }
  }
};

struct FreeformCurve { Handle(Geom_Curve) curve; double first, last; };
FreeformCurve freeform_curve(const TopoDS_Shape& shape) {
  if (shape.ShapeType()!=TopAbs_WIRE) throw FreeformRefusal{2};
  TopExp_Explorer edges(shape,TopAbs_EDGE);
  if (!edges.More()) throw FreeformRefusal{2};
  const auto edge=TopoDS::Edge(edges.Current()); edges.Next();
  if (edges.More() || BRep_Tool::Degenerated(edge)) throw FreeformRefusal{2};
  double first,last;
  auto curve=BRep_Tool::Curve(edge,first,last);
  if (curve.IsNull() || !std::isfinite(first) || !std::isfinite(last) || last<=first)
    throw FreeformRefusal{2};
  if (curve->Value(first).Distance(curve->Value(last))<=1e-10) throw FreeformRefusal{4};
  return {curve,first,last};
}

Handle(Geom_BSplineCurve) freeform_profile(const FreeformCurve& c, bool reverse) {
  auto bs = GeomConvert::CurveToBSplineCurve(new Geom_TrimmedCurve(c.curve,c.first,c.last));
  bs = Handle(Geom_BSplineCurve)::DownCast(bs->Copy());
  if (bs->Degree()!=2 || bs->NbPoles()!=3 || bs->NbKnots()!=2 || bs->IsPeriodic())
    throw FreeformRefusal{2};
  if (reverse) bs->Reverse();
  // Exact affine reparameterization of the full Bezier domain, not a refit.
  TColStd_Array1OfReal knots(1,2); knots.SetValue(1,0.); knots.SetValue(2,1.);
  bs->SetKnots(knots);
  return bs;
}

void freeform_error(double& maximum, double measured) {
  if (!std::isfinite(measured)) throw FreeformRefusal{7};
  maximum=std::max(maximum,measured);
}

void freeform_frame(const Handle(GeomFill_CurveAndTrihedron)& law,double t,gp_Mat& m,gp_Vec& v) {
  if (!law->D0(t,m,v) || !std::isfinite(m.Determinant()) || std::abs(m.Determinant())<0.5)
    throw FreeformRefusal{4};
}
} // namespace

extern "C" std::int32_t narwhal_occt_adapter_freeform(
    const std::uint8_t* input, std::size_t length, const double* stations,
    std::size_t count, double tolerance, FreeformInterpolate interpolate,
    FreeformQualify qualify, std::uint8_t* output, std::size_t capacity,
    std::size_t* required, NarwhalFreeformReportV1* report) noexcept {
  *required=0;
  try {
    const auto root=deserialize_shape(input,length);
    if (root.ShapeType()!=TopAbs_COMPOUND) throw FreeformRefusal{2};
    std::vector<FreeformCurve> inputs;
    for (TopoDS_Iterator it(root);it.More();it.Next()) {
      if (inputs.size()>count) throw FreeformRefusal{2};
      inputs.push_back(freeform_curve(it.Value()));
    }
    if (inputs.size()!=count+1) throw FreeformRefusal{2};
    const auto& rail=inputs[0];
    Handle(GeomAdaptor_Curve) adaptor=new GeomAdaptor_Curve(rail.curve,rail.first,rail.last);
    if (adaptor->Continuity()<GeomAbs_C2) throw FreeformRefusal{4};
    const auto rail_type=adaptor->GetType();
    if (rail_type!=GeomAbs_Line && rail_type!=GeomAbs_Circle && rail_type!=GeomAbs_Ellipse
        && rail_type!=GeomAbs_BezierCurve && rail_type!=GeomAbs_BSplineCurve) throw FreeformRefusal{2};
    if ((rail_type==GeomAbs_BSplineCurve || rail_type==GeomAbs_BezierCurve)
        && (adaptor->Degree()>8 || adaptor->NbPoles()>32)) throw FreeformRefusal{2};
    if (std::abs(stations[0]-rail.first)>1e-12 || std::abs(stations[2*(count-1)]-rail.last)>1e-12)
      throw FreeformRefusal{3};
    Handle(GeomFill_CurveAndTrihedron) location = new GeomFill_CurveAndTrihedron(new GeomFill_CorrectedFrenet());
    // SetCurve false means corrected rather than pure Frenet; it is not failure.
    location->SetCurve(adaptor); location->SetInterval(rail.first,rail.last);
    std::vector<Handle(Geom_BSplineCurve)> profiles;
    NCollection_Sequence<Handle(Geom_Curve)> locals;
    NCollection_Sequence<double> parameters;
    NCollection_Sequence<gp_Trsf> transforms;
    std::vector<double> points(count*9), normalized(count);
    std::array<double,3> weights{};
    for (std::size_t i=0;i<count;++i) {
      const double t=stations[2*i];
      if (t<rail.first || t>rail.last) throw FreeformRefusal{3};
      auto profile=freeform_profile(inputs[i+1],stations[2*i+1]!=0.);
      profiles.push_back(profile);
      auto local=Handle(Geom_BSplineCurve)::DownCast(profile->Copy());
      gp_Mat frame; gp_Vec origin; freeform_frame(location,t,frame,origin);
      const gp_Mat inverse=frame.Inverted();
      const double scale=std::max(1.,origin.Magnitude());
      const double placement=std::max(1e-12,128.*std::numeric_limits<double>::epsilon()*scale);
      for (int u=1;u<=3;++u) {
        const double w=profile->Weight(u)/profile->Weight(1);
        if (!std::isfinite(w) || w<0.1 || w>10.) throw FreeformRefusal{5};
        if (i==0) weights[u-1]=w;
        else if (std::abs(w-weights[u-1])>1e-14) throw FreeformRefusal{5};
        const gp_XYZ p=inverse*(profile->Pole(u).XYZ()-origin.XYZ());
        if (std::abs(p.Z())>placement || (u==1 && p.Modulus()>placement))
          throw FreeformRefusal{5};
        for (int k=0;k<3;++k) points[i*9+(u-1)*3+k]=p.Coord(k+1);
        local->SetPole(u,gp_Pnt(p),weights[u-1]);
      }
      locals.Append(local); parameters.Append(t);
      normalized[i]=(t-rail.first)/(rail.last-rail.first);
    }
    std::array<double,36> control{};
    if (interpolate(points.data(),normalized.data(),count,control.data())!=0) throw FreeformRefusal{1};
    TColgp_Array2OfPnt poles(1,3,1,4); TColStd_Array2OfReal ws(1,3,1,4);
    for (int u=0;u<3;++u) for (int v=0;v<4;++v) {
      const double* p=control.data()+3*(u*4+v);
      poles.SetValue(u+1,v+1,gp_Pnt(p[0],p[1],p[2])); ws.SetValue(u+1,v+1,weights[u]);
    }
    TColStd_Array1OfReal uk(1,2),vk(1,2); TColStd_Array1OfInteger um(1,2),vm(1,2);
    uk.SetValue(1,0.); uk.SetValue(2,1.); vk.SetValue(1,rail.first); vk.SetValue(2,rail.last);
    um.Init(3); vm.Init(4);
    Handle(Geom_BSplineSurface) reference=new Geom_BSplineSurface(poles,ws,uk,vk,um,vm,2,3,false,false);
    Handle(GeomFill_SectionLaw) sections=new FreeformSections(locals,transforms,parameters,0.,1.,rail.first,rail.last,reference);
    GeomFill_Sweep sweep(location,false);
    sweep.SetDomain(rail.first,rail.last,rail.first,rail.last);
    sweep.SetTolerance(tolerance/32.,tolerance/32.,1e-8,1e-7);
    sweep.SetForceApproxC1(false);
    sweep.Build(sections,GeomFill_Section,GeomAbs_C2,8,64);
    if (!sweep.IsDone()) throw FreeformRefusal{7};
    auto surface=Handle(Geom_BSplineSurface)::DownCast(sweep.Surface());
    if (surface.IsNull() || surface->UDegree()!=2 || surface->VDegree()<2 || surface->NbVKnots()>65)
      throw FreeformRefusal{8};
    for (int i=2;i<surface->NbVKnots();++i)
      if (surface->VDegree()-surface->VMultiplicity(i)<2) throw FreeformRefusal{8};
    report->continuity=2;
    report->fit_error=sweep.ErrorOnSurface();
    location->SetInterval(rail.first,rail.last);
    // Independent target evaluation (not OCCT's approximation error metric).
    for (std::size_t i=0;i<count;++i) for (int j=0;j<=64;++j) {
      const double u=j/64.;
      freeform_error(report->section_error,profiles[i]->Value(u).Distance(surface->Value(u,stations[2*i])));
      ++report->samples;
    }
    std::vector<double> probes;
    for (int i=0;i<=256;++i) probes.push_back(rail.first+(rail.last-rail.first)*i/256.);
    for (int i=1;i<surface->NbVKnots();++i) for (int j=0;j<=16;++j)
      probes.push_back(surface->VKnot(i)+(surface->VKnot(i+1)-surface->VKnot(i))*j/16.);
    for (double t:probes) {
      gp_Mat frame; gp_Vec origin; freeform_frame(location,t,frame,origin);
      freeform_error(report->rail_error,rail.curve->Value(t).Distance(surface->Value(0.,t)));
      for (int j=0;j<=16;++j) {
        const double u=j/16.;
        const gp_Pnt target(frame*reference->Value(u,t).XYZ()+origin.XYZ());
        freeform_error(report->law_error,target.Distance(surface->Value(u,t)));
        ++report->samples;
      }
    }
    if (!std::isfinite(report->fit_error) || report->fit_error>tolerance || report->section_error>tolerance
        || report->rail_error>tolerance || report->law_error>tolerance) throw FreeformRefusal{7};
    // Fixed global projection: native rail chord and transverse section chord.
    gp_Vec axis(rail.curve->Value(rail.first),rail.curve->Value(rail.last)); axis.Normalize();
    gp_Vec across(profiles[0]->StartPoint(),profiles[0]->EndPoint());
    across-=axis.Multiplied(across.Dot(axis));
    if (across.Magnitude()<1e-10) throw FreeformRefusal{6};
    across.Normalize();
    const std::array<double,3> za{axis.X(),axis.Y(),axis.Z()}, xa{across.X(),across.Y(),across.Z()};
    GeomConvert_BSplineSurfaceToBezierSurface patches(surface);
    if (patches.NbUPatches()!=1 || patches.NbVPatches()>64) throw FreeformRefusal{8};
    for (int i=1;i<=patches.NbVPatches();++i) {
      auto patch=patches.Patch(1,i);
      std::vector<double> pp,ww;
      for (int u=1;u<=patch->NbUPoles();++u) for (int v=1;v<=patch->NbVPoles();++v) {
        auto p=patch->Pole(u,v); pp.insert(pp.end(),{p.X(),p.Y(),p.Z()}); ww.push_back(patch->Weight(u,v));
      }
      if (qualify(pp.data(),ww.data(),patch->VDegree(),za.data(),xa.data(),report)!=0) throw FreeformRefusal{6};
      ++report->patches;
    }
    BRepBuilderAPI_MakeFace maker(surface,tolerance);
    if (!maker.IsDone() || !BRepCheck_Analyzer(maker.Face(),true).IsValid()) throw FreeformRefusal{9};
    return copy_serialized(serialize_shape(maker.Face()),output,capacity,required);
  } catch (const FreeformRefusal& e) { report->reason=e.reason; return kPolicyRefused;
  } catch (const std::invalid_argument&) { return kMalformedBrep;
  } catch (const Standard_Failure&) { return kKernelFailure;
  } catch (...) { return kUnexpectedFailure; }
}
