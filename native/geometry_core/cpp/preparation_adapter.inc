// SPDX-License-Identifier: GPL-3.0-or-later
// Private OCCT extraction only; Rust owns station resolution and tolerances.
#include <GeomAPI_ProjectPointOnCurve.hxx>
#include <new>

namespace {
bool preparation_finite(const gp_XYZ& p) {
  return std::isfinite(p.X()) && std::isfinite(p.Y()) && std::isfinite(p.Z());
}

void preparation_maximum(double& maximum, double measured) {
  if (!std::isfinite(measured)) throw FreeformRefusal{4};
  maximum=std::max(maximum,measured);
}

void preparation_candidate(const FreeformCurve& rail,
                           const Handle(Geom_BSplineCurve)& profile,
                           const gp_Pnt& intent, double parameter, double* out) {
  if (!std::isfinite(parameter) || parameter<rail.first || parameter>rail.last)
    throw FreeformRefusal{3};
  gp_Pnt foot;
  gp_Vec derivative;
  rail.curve->D1(parameter,foot,derivative);
  if (!preparation_finite(foot.XYZ()) || !preparation_finite(derivative.XYZ()))
    throw FreeformRefusal{4};
  const double magnitude=std::hypot(derivative.X(),derivative.Y(),derivative.Z());
  if (!std::isfinite(magnitude) || magnitude<=0.) throw FreeformRefusal{4};
  const gp_Vec tangent(derivative.X()/magnitude,derivative.Y()/magnitude,
                       derivative.Z()/magnitude);
  if (!preparation_finite(tangent.XYZ())) throw FreeformRefusal{4};
  const double distance=intent.Distance(foot);
  if (!std::isfinite(distance)) throw FreeformRefusal{4};
  const gp_Pnt start=profile->StartPoint();
  if (!preparation_finite(start.XYZ())) throw FreeformRefusal{4};
  double placement=0.;
  preparation_maximum(placement,start.Distance(intent));
  preparation_maximum(placement,start.Distance(foot));
  for (int u=1;u<=3;++u) {
    const gp_Vec offset(foot,profile->Pole(u));
    if (!preparation_finite(offset.XYZ())) throw FreeformRefusal{4};
    preparation_maximum(placement,std::abs(offset.Dot(tangent)));
  }
  out[0]=parameter; out[1]=distance;
  out[2]=foot.X(); out[3]=foot.Y(); out[4]=foot.Z(); out[5]=placement;
}
} // namespace

extern "C" std::int32_t narwhal_occt_adapter_prepare_stations(
    const std::uint8_t* input, std::size_t length, const double* points,
    const std::uint8_t* reverse, std::size_t count, double* domain,
    double* candidates, std::uint32_t* counts, std::uint32_t* reason) noexcept {
  if (!reason) return kInvalidArgument;
  *reason=0;
  try {
    if (!input || !length || !points || !reverse || !domain || !candidates || !counts
        || count<2 || count>4) throw FreeformRefusal{1};
    domain[0]=domain[1]=0.;
    std::fill_n(counts,count,0U);
    for (std::size_t i=0;i<count;++i) {
      if (reverse[i]>1 || !std::isfinite(points[3*i])
          || !std::isfinite(points[3*i+1]) || !std::isfinite(points[3*i+2]))
        throw FreeformRefusal{1};
    }
    const auto root=deserialize_shape(input,length);
    if (root.ShapeType()!=TopAbs_COMPOUND) throw FreeformRefusal{2};
    std::vector<FreeformCurve> inputs;
    for (TopoDS_Iterator it(root);it.More();it.Next()) {
      if (inputs.size()>=count+1) throw FreeformRefusal{2};
      const auto curve=freeform_curve(it.Value());
      if (!std::isfinite(curve.last-curve.first)) throw FreeformRefusal{3};
      if (!preparation_finite(curve.curve->Value(curve.first).XYZ())
          || !preparation_finite(curve.curve->Value(curve.last).XYZ()))
        throw FreeformRefusal{4};
      inputs.push_back(curve);
    }
    if (inputs.size()!=count+1) throw FreeformRefusal{2};
    const auto& rail=inputs[0];
    Handle(GeomAdaptor_Curve) adaptor=new GeomAdaptor_Curve(rail.curve,rail.first,rail.last);
    if (adaptor->Continuity()<GeomAbs_C2) throw FreeformRefusal{4};
    const auto type=adaptor->GetType();
    if (type!=GeomAbs_Line && type!=GeomAbs_Circle && type!=GeomAbs_Ellipse
        && type!=GeomAbs_BezierCurve && type!=GeomAbs_BSplineCurve)
      throw FreeformRefusal{2};
    if ((type==GeomAbs_BSplineCurve || type==GeomAbs_BezierCurve)
        && (adaptor->Degree()>8 || adaptor->NbPoles()>32)) throw FreeformRefusal{2};

    // Publish only a complete extraction; refusal must not expose partial counts.
    std::array<double,4*64*6> extracted{};
    std::array<std::uint32_t,4> extracted_counts{};
    std::array<double,3> weights{};
    for (std::size_t i=0;i<count;++i) {
      const auto profile=freeform_profile(inputs[i+1],reverse[i]!=0);
      const double base_weight=profile->Weight(1);
      if (!std::isfinite(base_weight) || base_weight<=0.) throw FreeformRefusal{5};
      for (int u=1;u<=3;++u) {
        const double w=profile->Weight(u)/base_weight;
        if (!std::isfinite(w) || w<0.1 || w>10.) throw FreeformRefusal{5};
        if (i==0) weights[u-1]=w;
        else if (std::abs(w-weights[u-1])>1e-14) throw FreeformRefusal{5};
        if (!preparation_finite(profile->Pole(u).XYZ())) throw FreeformRefusal{4};
      }
      const gp_Pnt intent(points[3*i],points[3*i+1],points[3*i+2]);
      double* station=extracted.data()+i*64*6;
      // Endpoints need not be stationary distance roots. Keep their exact native
      // bounds first so Rust can resolve endpoint intent without losing them.
      preparation_candidate(rail,profile,intent,rail.first,station);
      preparation_candidate(rail,profile,intent,rail.last,station+6);
      // Recognition of an exact rational arc can change parameters non-affinely.
      // Project physical intent on the real native curve, never remap its domain.
      GeomAPI_ProjectPointOnCurve projector(intent,rail.curve,rail.first,rail.last);
      const int roots=projector.NbPoints();
      if (roots>62) throw FreeformRefusal{8};
      if (roots<0) throw FreeformRefusal{4};
      for (int j=1;j<=roots;++j)
        preparation_candidate(rail,profile,intent,projector.Parameter(j),station+(j+1)*6);
      extracted_counts[i]=static_cast<std::uint32_t>(roots+2);
    }
    std::copy_n(extracted.data(),count*64*6,candidates);
    std::copy_n(extracted_counts.data(),count,counts);
    domain[0]=rail.first; domain[1]=rail.last;
    return kOk;
  } catch (const FreeformRefusal& e) { *reason=e.reason; return kPolicyRefused;
  } catch (const std::bad_alloc&) { *reason=8; return kPolicyRefused;
  } catch (const std::invalid_argument&) { *reason=1; return kPolicyRefused;
  } catch (const Standard_Failure&) { return kKernelFailure;
  } catch (...) { return kUnexpectedFailure; }
}
