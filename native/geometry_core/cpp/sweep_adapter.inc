// SPDX-License-Identifier: GPL-3.0-or-later
// Included in the existing adapter translation unit to reuse its BinTools boundary.
// Rust owns control-net construction, validation and the supported-domain policy.
#include <TColStd_Array2OfReal.hxx>

extern "C" std::int32_t narwhal_occt_adapter_sweep_parallel(
    const double* poles, double middle_weight, double tolerance,
    std::uint8_t* output, std::size_t capacity, std::size_t* required) noexcept {
  *required = 0;
  try {
    TColgp_Array2OfPnt points(1, 3, 1, 4);
    TColStd_Array2OfReal weights(1, 3, 1, 4);
    for (int u = 0; u < 3; ++u) {
      for (int v = 0; v < 4; ++v) {
        const double* p = poles + 3 * (4 * u + v);
        points.SetValue(u + 1, v + 1, gp_Pnt(p[0], p[1], p[2]));
        weights.SetValue(u + 1, v + 1, u == 1 ? middle_weight : 1.0);
      }
    }
    TColStd_Array1OfReal knots(1, 2);
    knots.SetValue(1, 0.0); knots.SetValue(2, 1.0);
    TColStd_Array1OfInteger um(1, 2), vm(1, 2);
    um.Init(3); vm.Init(4);
    Handle(Geom_BSplineSurface) surface = new Geom_BSplineSurface(
        points, weights, knots, knots, um, vm, 2, 3, false, false);
    BRepBuilderAPI_MakeFace maker(surface, tolerance);
    if (!maker.IsDone()) return kKernelFailure;
    const TopoDS_Face face = maker.Face();
    if (!BRepCheck_Analyzer(face, true).IsValid()) return kKernelFailure;
    return copy_serialized(serialize_shape(face), output, capacity, required);
  } catch (const Standard_Failure&) {
    return kKernelFailure;
  } catch (const std::exception&) {
    return kUnexpectedFailure;
  } catch (...) {
    return kUnexpectedFailure;
  }
}
