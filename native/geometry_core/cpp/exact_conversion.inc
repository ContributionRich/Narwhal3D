// SPDX-License-Identifier: GPL-3.0-or-later
#include <BRepBuilderAPI_NurbsConvert.hxx>
#include <BRepTools_NurbsConvertModification.hxx>
#include <Convert_ConeToBSplineSurface.hxx>
#include <Convert_CylinderToBSplineSurface.hxx>
#include <Convert_SphereToBSplineSurface.hxx>
#include <Convert_TorusToBSplineSurface.hxx>
#include <Geom_ConicalSurface.hxx>
#include <Geom_CylindricalSurface.hxx>
#include <Geom_SphericalSurface.hxx>
#include <Geom_ToroidalSurface.hxx>
namespace {
Handle(Geom_BSplineSurface) bounded_elementary_nurbs(const Convert_ElementarySurfaceToBSplineSurface& c) {
  return new Geom_BSplineSurface(c.Poles(),c.Weights(),c.UKnots(),c.VKnots(),
                                c.UMultiplicities(),c.VMultiplicities(),c.UDegree(),c.VDegree(),
                                c.IsUPeriodic(),c.IsVPeriodic());
}
class SeamSafeNurbsModification final : public BRepTools_NurbsConvertModification {
 public:
  bool NewSurface(const TopoDS_Face& face,Handle(Geom_Surface)& surface,TopLoc_Location& location,
                  double& tolerance,bool& reverse_wires,bool& reverse_face) override {
    const auto original=BRep_Tool::Surface(face,location);
    auto basis=original;
    while (!Handle(Geom_RectangularTrimmedSurface)::DownCast(basis).IsNull())
      basis=Handle(Geom_RectangularTrimmedSurface)::DownCast(basis)->BasisSurface();
    double u0,u1,v0,v1; BRepTools::UVBounds(face,u0,u1,v0,v1);
    // MEASURED OCCT 8.0.1 GeomConvert_1.cxx: a closed elementary U patch
    // uses a periodic rational surface followed by CheckAndSegment(U0,U1).
    // Rational U is NOT angle U. At a shifted seam that cuts the wrong 3-D
    // locus; subsequent pcurve projection moves the seam off the UV boundary.
    // Explicit bounded constructors anchor the rational control net at the
    // actual angular endpoints instead. No fitted map or trim/iso flag repair.
    if (u0!=0. && std::abs((u1-u0)-2.*M_PI)<=Precision::PConfusion()) {
      // Match stock NewSurface's fundamental-period bound for roundoff at 2pi.
      const double last=std::min(u1,u0+2.*M_PI);
      if (auto s=Handle(Geom_ConicalSurface)::DownCast(basis); !s.IsNull())
        surface=bounded_elementary_nurbs(Convert_ConeToBSplineSurface(s->Cone(),u0,last,v0,v1));
      else if (auto s=Handle(Geom_CylindricalSurface)::DownCast(basis); !s.IsNull())
        surface=bounded_elementary_nurbs(Convert_CylinderToBSplineSurface(s->Cylinder(),u0,last,v0,v1));
      else if (auto s=Handle(Geom_SphericalSurface)::DownCast(basis); !s.IsNull())
        surface=bounded_elementary_nurbs(Convert_SphereToBSplineSurface(s->Sphere(),u0,last,v0,v1));
      else if (auto s=Handle(Geom_ToroidalSurface)::DownCast(basis); !s.IsNull())
        surface=bounded_elementary_nurbs(Convert_TorusToBSplineSurface(s->Torus(),u0,last,v0,v1));
      else return BRepTools_NurbsConvertModification::NewSurface(face,surface,location,tolerance,reverse_wires,reverse_face);
      tolerance=BRep_Tool::Tolerance(face); reverse_wires=false; reverse_face=false;
      return true;
    }
    return BRepTools_NurbsConvertModification::NewSurface(face,surface,location,tolerance,reverse_wires,reverse_face);
  }
};
class SeamSafeNurbsConvert final : public BRepBuilderAPI_NurbsConvert {
 public:
  explicit SeamSafeNurbsConvert(const TopoDS_Shape& shape) {
    myModification=new SeamSafeNurbsModification();
    Perform(shape,true);
  }
};
}
extern "C" std::int32_t narwhal_occt_adapter_nurbs_convert_seam_safe(
    const std::uint8_t* input,std::size_t length,std::uint8_t* output,std::size_t capacity,std::size_t* required) noexcept {
  try {
    // Caller supplies a trusted live-shape serialization, or runs under a hard
    // decoder supervisor. Transport size is not a BinTools allocation bound.
    const auto source=deserialize_shape(input,length);
    const auto clean=BRepBuilderAPI_Copy(source,true,false).Shape();
    // No copied mesh/polygons: the base modification's private surface map is
    // used only for mesh UV updates, never this untriangulated conversion.
    BRepTools::Clean(clean);
    const auto result=SeamSafeNurbsConvert(clean).Shape();
    return copy_serialized(serialize_shape(result),output,capacity,required);
  } catch (const std::invalid_argument&) {return kMalformedBrep;
  } catch (const Standard_Failure&) {return kKernelFailure;
  } catch (...) {return kUnexpectedFailure;}
}

extern "C" std::int32_t narwhal_occt_adapter_conversion_fixture(
    std::uint32_t kind,std::uint8_t* output,std::size_t capacity,std::size_t* required) noexcept {
  try {
    Handle(Geom_Surface) surface;
    double v0=0.,v1=3.;
    const gp_Ax3 axes(gp_Pnt(20.,-7.,2.),gp::DZ());
    if (kind==0) surface=new Geom_ConicalSurface(axes,.3,2.);
    else if (kind==1) surface=new Geom_CylindricalSurface(axes,2.);
    else if (kind==2) {surface=new Geom_SphericalSurface(axes,2.);v0=-.9;v1=.9;}
    else {surface=new Geom_ToroidalSurface(axes,4.,1.);v0=-.7;v1=.9;}
    const auto face=BRepBuilderAPI_MakeFace(surface,.37,.37+2.*M_PI,v0,v1,1e-7).Face();
    return copy_serialized(serialize_shape(face),output,capacity,required);
  } catch (...) {return kUnexpectedFailure;}
}
extern "C" std::int32_t narwhal_occt_adapter_conversion_corner_error(
    const std::uint8_t* input,std::size_t length,const std::uint8_t* converted,std::size_t converted_length,double* error) noexcept {
  try {
    const auto original=deserialize_shape(input,length), result=deserialize_shape(converted,converted_length);
    const auto old=TopoDS::Face(TopExp_Explorer(original,TopAbs_FACE).Current());
    const auto now=TopoDS::Face(TopExp_Explorer(result,TopAbs_FACE).Current());
    double u0,u1,v0,v1; BRepTools::UVBounds(old,u0,u1,v0,v1);
    const auto a=BRep_Tool::Surface(old),b=BRep_Tool::Surface(now);
    *error=0.;
    for (double u:{u0,u1}) for (double v:{v0,v1})
      *error=std::max(*error,a->Value(u,v).Distance(b->Value(u,v)));
    return kOk;
  } catch (...) {return kUnexpectedFailure;}
}
