// SPDX-License-Identifier: GPL-3.0-or-later

#include <BinTools.hxx>
#include <BinTools_FormatVersion.hxx>
#include <BRepAdaptor_Curve.hxx>
#include <BRepAdaptor_Surface.hxx>
#include <BRepAlgoAPI_Check.hxx>
#include <BRepAlgoAPI_Fuse.hxx>
#include <BRepBndLib.hxx>
#include <BRepBuilderAPI_Copy.hxx>
#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakeSolid.hxx>
#include <BRepBuilderAPI_MakeVertex.hxx>
#include <BRepBuilderAPI_MakeWire.hxx>
#include <BRepBuilderAPI_Sewing.hxx>
#include <BRepBuilderAPI_Transform.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRepClass_FaceClassifier.hxx>
#include <BRepExtrema_DistShapeShape.hxx>
#include <BRepFilletAPI_MakeFillet.hxx>
#include <BRepGProp.hxx>
#include <BRepGProp_Face.hxx>
#include <BRepPrimAPI_MakeBox.hxx>
#include <BRepPrimAPI_MakeCone.hxx>
#include <BRepPrimAPI_MakeCylinder.hxx>
#include <BRepPrimAPI_MakeTorus.hxx>
#include <BRepTools.hxx>
#include <BRepTools_WireExplorer.hxx>
#include <BRep_Builder.hxx>
#include <BRep_Tool.hxx>
#include <Bnd_Box.hxx>
#include <GProp_GProps.hxx>
#include <GeomAbs_CurveType.hxx>
#include <GeomAbs_SurfaceType.hxx>
#include <GeomConvert.hxx>
#include <GeomConvert_CompCurveToBSplineCurve.hxx>
#include <GeomConvert_CurveToAnaCurve.hxx>
#include <GeomFill_BSplineCurves.hxx>
#include <GeomFill_FillingStyle.hxx>
#include <GeomAPI_ProjectPointOnCurve.hxx>
#include <Geom2d_BSplineCurve.hxx>
#include <gp_Dir2d.hxx>
#include <gp_Ax22d.hxx>
#include <Geom_Line.hxx>
#include <Geom_Circle.hxx>
#include <Geom2d_Circle.hxx>
#include <Geom2d_Curve.hxx>
#include <Geom_BSplineCurve.hxx>
#include <Geom_BSplineSurface.hxx>
#include <Geom_Curve.hxx>
#include <Geom_Surface.hxx>
#include <Geom_TrimmedCurve.hxx>
#include <Precision.hxx>
#include <ShapeFix_Solid.hxx>
#include <Standard_Failure.hxx>
#include <TColStd_Array1OfInteger.hxx>
#include <TColStd_Array1OfReal.hxx>
#include <TColgp_Array1OfPnt2d.hxx>
#include <TColgp_Array2OfPnt.hxx>
#include <TopAbs_Orientation.hxx>
#include <TopAbs_ShapeEnum.hxx>
#include <TopAbs_State.hxx>
#include <TopExp.hxx>
#include <TopExp_Explorer.hxx>
#include <NCollection_IndexedMap.hxx>
#include <TopTools_ShapeMapHasher.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Edge.hxx>
#include <TopoDS_Face.hxx>
#include <TopoDS_Shape.hxx>
#include <TopoDS_Shell.hxx>
#include <TopoDS_Solid.hxx>
#include <TopoDS_Vertex.hxx>
#include <TopoDS_Wire.hxx>
#include <gp_Ax2.hxx>
#include <gp_Ax3.hxx>
#include <gp_Circ.hxx>
#include <gp_Cylinder.hxx>
#include <gp_Dir.hxx>
#include <gp_Pnt.hxx>
#include <gp_Pnt2d.hxx>
#include <gp_Pln.hxx>
#include <gp_Trsf.hxx>
#include <gp_Vec.hxx>

#include <algorithm>
#include <array>
#include <atomic>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <exception>
#include <functional>
#include <future>
#include <ios>
#include <limits>
#include <numeric>
#include <optional>
#include <set>
#include <sstream>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

// The height-field solver lives in Rust (`height_field.rs`). Rust registers it before
// any move, as a pointer rather than an exported symbol, so the DLL's public ABI is
// unchanged.
using HeightFieldSolver = std::int32_t (*)(const double* samples, std::size_t sample_count,
                                           double smoothing, double target_error,
                                           std::uint32_t max_spans, double bias, double* heights,
                                           std::size_t capacity, void* result);
static std::atomic<HeightFieldSolver> g_height_field_solver{nullptr};

extern "C" void narwhal_occt_adapter_set_height_field_solver(HeightFieldSolver solver) noexcept {
  g_height_field_solver.store(solver);
}

namespace {

constexpr std::int32_t kOk = 0;
constexpr std::int32_t kBufferTooSmall = 1;
constexpr std::int32_t kInvalidArgument = -1;
constexpr std::int32_t kLimitExceeded = -2;
constexpr std::int32_t kKernelFailure = -4;
constexpr std::int32_t kMalformedBrep = -5;
constexpr std::int32_t kUnexpectedFailure = -6;
constexpr std::int32_t kPolicyRefused = -7;
constexpr std::int32_t kSmoothnessCrease = -8;

constexpr std::uint32_t kReasonNone = 0;
constexpr std::uint32_t kReasonInputNotAirtight = 1;
constexpr std::uint32_t kReasonIndexOutside = 2;
constexpr std::uint32_t kReasonDegenerate = 3;
constexpr std::uint32_t kReasonUnsupportedBoundary = 4;
constexpr std::uint32_t kReasonContainment = 5;
constexpr std::uint32_t kReasonTopology = 6;
constexpr std::uint32_t kReasonNoCandidate = 7;
constexpr std::uint32_t kReasonAmbiguous = 8;
constexpr std::uint32_t kReasonSmoothness = 9;
constexpr std::uint32_t kReasonTolerance = 10;
constexpr std::uint32_t kReasonSelfIntersection = 11;
constexpr std::uint32_t kReasonResourceLimit = 12;

constexpr std::uint32_t kQualityReconstructedFaces = 1U << 0;
constexpr std::uint32_t kQualityBsplineBoundary = 1U << 1;
constexpr std::uint32_t kQualityInternalKnot = 1U << 2;
constexpr std::uint32_t kQualityMovedVertexMapping = 1U << 3;
constexpr std::uint32_t kQualityMappedIncidence = 1U << 4;
constexpr std::uint32_t kQualityUnchangedGeometry = 1U << 5;

std::atomic<std::size_t> g_live_sessions{0};

struct AdapterInspection {
  std::uint32_t struct_size;
  std::uint32_t abi_version;
  std::uint32_t valid;
  std::uint32_t airtight;
  std::uint64_t face_count;
  std::uint64_t edge_count;
  std::uint64_t vertex_count;
  double volume;
  std::uint64_t plane_faces;
  std::uint64_t cylinder_faces;
  std::uint64_t cone_faces;
  std::uint64_t sphere_faces;
  std::uint64_t torus_faces;
  std::uint64_t bezier_faces;
  std::uint64_t bspline_faces;
  std::uint64_t revolution_faces;
  std::uint64_t extrusion_faces;
  std::uint64_t offset_faces;
  std::uint64_t other_faces;
};

struct AdapterMovePolicy {
  double zero_translation;
  double max_normal_jump;
  std::uint32_t max_candidates;
  std::uint32_t edge_samples;
  std::uint64_t max_faces;
  std::uint64_t max_edges;
  std::uint64_t max_vertices;
};

struct AdapterMoveCertificate {
  std::uint32_t struct_size;
  std::uint32_t abi_version;
  std::int32_t status;
  std::uint32_t refusal_reason;
  std::uint32_t valid;
  std::uint32_t airtight;
  std::uint32_t self_intersection_free;
  std::uint32_t topology_counts_preserved;
  std::uint32_t moved_vertex_mapping_verified;
  std::uint32_t mapped_incidence_preserved;
  std::uint32_t unchanged_geometry_preserved;
  std::uint32_t selected_match_count;
  std::uint32_t component_kind;
  std::uint32_t quality_scope_bits;
  std::uint32_t quality_checked_bits;
  std::uint32_t reconstructed_face_count;
  std::uint32_t reconstructed_bspline_face_count;
  std::uint32_t internal_knot_face_count;
  std::uint32_t verification_failure_stage;
  std::uint32_t verification_failure_detail;
  std::uint64_t face_count;
  std::uint64_t edge_count;
  std::uint64_t vertex_count;
  double volume;
  double selected_translation_error;
  double max_boundary_error;
  double max_internal_normal_jump;
  double comparison_tolerance;
  double build_tolerance;
  double sewing_tolerance;
  std::uint64_t plane_faces;
  std::uint64_t cylinder_faces;
  std::uint64_t cone_faces;
  std::uint64_t sphere_faces;
  std::uint64_t torus_faces;
  std::uint64_t bezier_faces;
  std::uint64_t bspline_faces;
  std::uint64_t revolution_faces;
  std::uint64_t extrusion_faces;
  std::uint64_t offset_faces;
  std::uint64_t other_faces;
};

static_assert(sizeof(AdapterInspection) == 136, "Rust/C++ inspection layout changed");
static_assert(sizeof(AdapterMovePolicy) == 48, "Rust/C++ policy layout changed");
static_assert(sizeof(AdapterMoveCertificate) == 248, "Rust/C++ certificate layout changed");

struct Refusal {
  std::int32_t status;
  std::uint32_t reason;
};

[[noreturn]] void refuse(const std::uint32_t reason,
                         const std::int32_t status = kPolicyRefused) {
  throw Refusal{status, reason};
}

struct Tolerances {
  double build;
  double sew;
  double compare;
  double extent;
  double input;
};

struct FaceOption {
  TopoDS_Face face;
  std::array<double, 5> rank{};
  bool reconstructed = false;
  bool reconstructed_bspline = false;
  bool internal_knot_checked = false;
  int merge_choice = -1;
  double boundary_error = 0.0;
  double normal_jump = 0.0;
};

using Point = std::array<double, 3>;
using ShapeMap = NCollection_IndexedMap<TopoDS_Shape, TopTools_ShapeMapHasher>;

Point point_of(const TopoDS_Vertex& vertex) {
  const gp_Pnt point = BRep_Tool::Pnt(vertex);
  return {point.X(), point.Y(), point.Z()};
}

Point translated(const Point& point, const Point& vector) {
  return {point[0] + vector[0], point[1] + vector[1], point[2] + vector[2]};
}

double point_distance(const Point& first, const Point& second) {
  const double dx = first[0] - second[0];
  const double dy = first[1] - second[1];
  const double dz = first[2] - second[2];
  return std::sqrt(dx * dx + dy * dy + dz * dz);
}

gp_Trsf translation_transform(const Point& vector) {
  gp_Trsf transform;
  transform.SetTranslation(gp_Vec(vector[0], vector[1], vector[2]));
  return transform;
}

void map_shapes(const TopoDS_Shape& shape, const TopAbs_ShapeEnum type, ShapeMap& map) {
  TopExp::MapShapes(shape, type, map);
}

bool within_resource_caps(const TopoDS_Shape& shape,
                          const std::uint64_t max_faces,
                          const std::uint64_t max_edges,
                          const std::uint64_t max_vertices) {
  ShapeMap faces;
  ShapeMap edges;
  ShapeMap vertices;
  map_shapes(shape, TopAbs_FACE, faces);
  if (static_cast<std::uint64_t>(faces.Extent()) > max_faces) {
    return false;
  }
  map_shapes(shape, TopAbs_EDGE, edges);
  if (static_cast<std::uint64_t>(edges.Extent()) > max_edges) {
    return false;
  }
  map_shapes(shape, TopAbs_VERTEX, vertices);
  return static_cast<std::uint64_t>(vertices.Extent()) <= max_vertices;
}

bool all_shells_are_closed(const TopoDS_Shape& shape) {
  ShapeMap shells;
  map_shapes(shape, TopAbs_SHELL, shells);
  if (shells.IsEmpty()) {
    return false;
  }
  for (int index = 1; index <= shells.Extent(); ++index) {
    if (!BRep_Tool::IsClosed(shells.FindKey(index))) {
      return false;
    }
  }
  return true;
}

bool is_airtight(const TopoDS_Shape& shape) {
  if (shape.IsNull() || !BRepCheck_Analyzer(shape, true).IsValid()) {
    return false;
  }
  ShapeMap solids;
  map_shapes(shape, TopAbs_SOLID, solids);
  return solids.Extent() == 1 && all_shells_are_closed(shape);
}

double shape_volume(const TopoDS_Shape& shape) {
  GProp_GProps properties;
  BRepGProp::VolumeProperties(shape, properties, true, false, false);
  return properties.Mass();
}

void increment_surface_count(AdapterInspection& result, const GeomAbs_SurfaceType type) {
  switch (type) {
    case GeomAbs_Plane: ++result.plane_faces; break;
    case GeomAbs_Cylinder: ++result.cylinder_faces; break;
    case GeomAbs_Cone: ++result.cone_faces; break;
    case GeomAbs_Sphere: ++result.sphere_faces; break;
    case GeomAbs_Torus: ++result.torus_faces; break;
    case GeomAbs_BezierSurface: ++result.bezier_faces; break;
    case GeomAbs_BSplineSurface: ++result.bspline_faces; break;
    case GeomAbs_SurfaceOfRevolution: ++result.revolution_faces; break;
    case GeomAbs_SurfaceOfExtrusion: ++result.extrusion_faces; break;
    case GeomAbs_OffsetSurface: ++result.offset_faces; break;
    case GeomAbs_OtherSurface:
    default: ++result.other_faces; break;
  }
}

AdapterInspection inspect_shape_impl(const TopoDS_Shape& shape,
                                     const bool already_certified) {
  AdapterInspection result{};
  result.struct_size = sizeof(AdapterInspection);
  result.abi_version = 1;
  result.valid = already_certified || BRepCheck_Analyzer(shape, true).IsValid() ? 1U : 0U;
  if (result.valid == 0U) {
    return result;
  }
  ShapeMap faces;
  ShapeMap edges;
  ShapeMap vertices;
  map_shapes(shape, TopAbs_FACE, faces);
  map_shapes(shape, TopAbs_EDGE, edges);
  map_shapes(shape, TopAbs_VERTEX, vertices);
  result.face_count = static_cast<std::uint64_t>(faces.Extent());
  result.edge_count = static_cast<std::uint64_t>(edges.Extent());
  result.vertex_count = static_cast<std::uint64_t>(vertices.Extent());
  result.airtight = already_certified || is_airtight(shape) ? 1U : 0U;
  for (int index = 1; index <= faces.Extent(); ++index) {
    increment_surface_count(result, BRepAdaptor_Surface(TopoDS::Face(faces.FindKey(index)), true).GetType());
  }
  if (result.airtight != 0U) {
    result.volume = shape_volume(shape);
  }
  return result;
}

AdapterInspection inspect_shape(const TopoDS_Shape& shape) {
  return inspect_shape_impl(shape, false);
}

AdapterInspection inspect_certified_shape(const TopoDS_Shape& shape) {
  return inspect_shape_impl(shape, true);
}

std::string serialize_shape(const TopoDS_Shape& shape) {
  std::ostringstream stream(std::ios::out | std::ios::binary);
  BinTools::Write(shape, stream, false, false, BinTools_FormatVersion_CURRENT);
  if (!stream.good()) {
    throw std::runtime_error("BinTools write failed");
  }
  return stream.str();
}

TopoDS_Shape deserialize_shape(const std::uint8_t* input, const std::size_t length) {
  const std::string bytes(reinterpret_cast<const char*>(input), length);
  std::istringstream stream(bytes, std::ios::in | std::ios::binary);
  TopoDS_Shape shape;
  BinTools::Read(shape, stream);
  if (shape.IsNull() || stream.bad() ||
      stream.peek() != std::char_traits<char>::eof()) {
    throw std::invalid_argument("malformed or trailing BinTools shape data");
  }
  return shape;
}

std::int32_t copy_serialized(const std::string& bytes,
                             std::uint8_t* output,
                             const std::size_t capacity,
                             std::size_t* required) {
  *required = bytes.size();
  if (capacity < bytes.size()) {
    return kBufferTooSmall;
  }
  if (!bytes.empty()) {
    std::memcpy(output, bytes.data(), bytes.size());
  }
  return kOk;
}

Tolerances local_tolerances(const TopoDS_Shape& shape) {
  Bnd_Box box;
  box.SetGap(0.0);
  BRepBndLib::AddOptimal(shape, box, false, false);
  double xmin = 0.0;
  double ymin = 0.0;
  double zmin = 0.0;
  double xmax = 0.0;
  double ymax = 0.0;
  double zmax = 0.0;
  box.Get(xmin, ymin, zmin, xmax, ymax, zmax);
  const double extent = std::max({xmax - xmin, ymax - ymin, zmax - zmin, Precision::Confusion()});
  const double coordinate = std::max({1.0, std::abs(xmin), std::abs(ymin), std::abs(zmin),
                                      std::abs(xmax), std::abs(ymax), std::abs(zmax)});
  const double ulp = std::nextafter(coordinate, std::numeric_limits<double>::infinity()) - coordinate;
  const double input_tolerance = std::max({
      BRep_Tool::MaxTolerance(shape, TopAbs_VERTEX),
      BRep_Tool::MaxTolerance(shape, TopAbs_EDGE),
      BRep_Tool::MaxTolerance(shape, TopAbs_FACE)});
  const double loose_limit = std::max(1000.0 * Precision::Confusion(), extent * 1.0e-4);
  if (input_tolerance > loose_limit) {
    refuse(kReasonTolerance);
  }
  const double comparison = std::max({Precision::PConfusion(), 64.0 * ulp,
      std::min(input_tolerance / 8.0, extent * 1.0e-10)});
  const double build = std::max({Precision::Confusion(), input_tolerance, 8.0 * comparison});
  return {build, std::min(4.0 * build, loose_limit), comparison, extent, input_tolerance};
}

std::vector<int> edge_vertex_indices(const TopoDS_Edge& edge, const ShapeMap& vertices) {
  std::vector<int> result;
  for (TopExp_Explorer explorer(edge, TopAbs_VERTEX); explorer.More(); explorer.Next()) {
    const int index = vertices.FindIndex(explorer.Current()) - 1;
    if (index < 0) {
      refuse(kReasonTopology);
    }
    if (std::find(result.begin(), result.end(), index) == result.end()) {
      result.push_back(index);
    }
  }
  return result;
}

std::vector<int> face_edge_indices(const TopoDS_Face& face, const ShapeMap& edges) {
  std::vector<int> result;
  for (TopExp_Explorer explorer(face, TopAbs_EDGE); explorer.More(); explorer.Next()) {
    const int index = edges.FindIndex(explorer.Current()) - 1;
    if (index < 0) {
      refuse(kReasonTopology);
    }
    if (std::find(result.begin(), result.end(), index) == result.end()) {
      result.push_back(index);
    }
  }
  return result;
}

TopoDS_Edge orient_edge(const TopoDS_Edge& edge, const Point& start, const double tolerance) {
  const Point first = point_of(TopExp::FirstVertex(edge, true));
  const Point last = point_of(TopExp::LastVertex(edge, true));
  const double first_distance = point_distance(first, start);
  const double last_distance = point_distance(last, start);
  if (std::min(first_distance, last_distance) > tolerance) {
    refuse(kReasonTopology);
  }
  return last_distance < first_distance ? TopoDS::Edge(edge.Reversed()) : edge;
}

std::pair<TopoDS_Edge, bool> rebuild_edge(const TopoDS_Edge& edge,
                                           const std::vector<int>& endpoint_indices,
                                           const ShapeMap& vertices,
                                           const std::set<int>& moved,
                                           const Point& vector,
                                           const double tolerance) {
  if (endpoint_indices.empty()) {
    refuse(kReasonTopology);
  }
  if (std::all_of(endpoint_indices.begin(), endpoint_indices.end(),
                  [&](const int index) { return moved.count(index) != 0; })) {
    const TopoDS_Shape transformed = BRepBuilderAPI_Transform(edge, translation_transform(vector), true).Shape();
    return {TopoDS::Edge(transformed), true};
  }
  if (endpoint_indices.size() != 2) {
    refuse(kReasonDegenerate);
  }
  std::array<Point, 2> old_points{point_of(TopoDS::Vertex(vertices.FindKey(endpoint_indices[0] + 1))),
                                  point_of(TopoDS::Vertex(vertices.FindKey(endpoint_indices[1] + 1)))};
  std::array<Point, 2> new_points = old_points;
  for (int endpoint = 0; endpoint < 2; ++endpoint) {
    if (moved.count(endpoint_indices[endpoint]) != 0) {
      new_points[endpoint] = translated(old_points[endpoint], vector);
    }
  }
  BRepAdaptor_Curve adaptor(edge);
  if (adaptor.GetType() == GeomAbs_Line) {
    BRepBuilderAPI_MakeEdge maker(gp_Pnt(new_points[0][0], new_points[0][1], new_points[0][2]),
                                  gp_Pnt(new_points[1][0], new_points[1][1], new_points[1][2]));
    if (!maker.IsDone()) {
      refuse(kReasonNoCandidate);
    }
    return {maker.Edge(), false};
  }
  double first = 0.0;
  double last = 0.0;
  Handle(Geom_Curve) curve = BRep_Tool::Curve(edge, first, last);
  if (curve.IsNull()) {
    refuse(kReasonUnsupportedBoundary);
  }
  Handle(Geom_BSplineCurve) bspline = GeomConvert::CurveToBSplineCurve(
      new Geom_TrimmedCurve(curve, first, last));
  std::set<int> changed_poles;
  for (const int pole_index : {1, bspline->NbPoles()}) {
    const gp_Pnt pole = bspline->Pole(pole_index);
    const Point pole_point{pole.X(), pole.Y(), pole.Z()};
    for (int endpoint = 0; endpoint < 2; ++endpoint) {
      if (moved.count(endpoint_indices[endpoint]) != 0 &&
          point_distance(pole_point, old_points[endpoint]) <= tolerance) {
        const Point value = translated(pole_point, vector);
        bspline->SetPole(pole_index, gp_Pnt(value[0], value[1], value[2]));
        changed_poles.insert(pole_index);
      }
    }
  }
  const int moved_count = static_cast<int>(moved.count(endpoint_indices[0]) != 0) +
                          static_cast<int>(moved.count(endpoint_indices[1]) != 0);
  if (static_cast<int>(changed_poles.size()) != moved_count) {
    refuse(kReasonUnsupportedBoundary);
  }
  if (changed_poles.size() == 1 && bspline->NbKnots() > 2) {
    // MEASURED (bug #108, reference vertex move on an extrusion cap): the reference does not drag
    // only the end control point of a multi-span edge -- that kinks the curve in its last
    // span and folds the adjacent faces. It adds a ramp V*s(t) along the whole edge, s
    // going 0 -> 1 from the fixed end to the moved end; its pole offsets equal the
    // curve's normalized Greville abscissae. Adding V*s_i to each pole is exact (same
    // degree and knots). A single-span (Bezier) edge has no last span to kink: dragging
    // its end pole already bends it globally, so that certified behaviour is kept.
    const int pole_count = bspline->NbPoles();
    const int degree = bspline->Degree();
    const bool moved_at_end = changed_poles.count(pole_count) != 0;
    TColStd_Array1OfReal knots(1, pole_count + degree + 1);
    bspline->KnotSequence(knots);
    std::vector<double> greville(static_cast<std::size_t>(pole_count), 0.0);
    for (int pole = 1; pole <= pole_count; ++pole) {
      double sum = 0.0;
      for (int offset = 1; offset <= degree; ++offset) {
        sum += knots(knots.Lower() + pole - 1 + offset);
      }
      greville[static_cast<std::size_t>(pole - 1)] = sum / degree;
    }
    const double span = greville.back() - greville.front();
    if (!(span > 0.0) || bspline->IsPeriodic()) {
      refuse(kReasonUnsupportedBoundary);
    }
    for (int pole = 2; pole < pole_count; ++pole) {
      const double ramp = (greville[static_cast<std::size_t>(pole - 1)] - greville.front()) / span;
      const double weight = moved_at_end ? ramp : 1.0 - ramp;
      const gp_Pnt old_pole = bspline->Pole(pole);
      bspline->SetPole(pole, gp_Pnt(old_pole.X() + vector[0] * weight,
                                    old_pole.Y() + vector[1] * weight,
                                    old_pole.Z() + vector[2] * weight));
    }
  }
  BRepBuilderAPI_MakeEdge maker(bspline);
  if (!maker.IsDone()) {
    refuse(kReasonNoCandidate);
  }
  return {maker.Edge(), false};
}

std::vector<TopoDS_Edge> boundary_edges(const TopoDS_Face& face,
                                         const ShapeMap& edges,
                                         const std::vector<std::optional<TopoDS_Edge>>& replacements,
                                         const ShapeMap& vertices,
                                         const std::set<int>& moved,
                                         const Point& vector,
                                         const double tolerance,
                                         const bool isolate_source_edges) {
  int wire_count = 0;
  for (TopExp_Explorer explorer(face, TopAbs_WIRE); explorer.More(); explorer.Next()) {
    ++wire_count;
  }
  if (wire_count != 1) {
    refuse(kReasonUnsupportedBoundary);
  }
  const TopoDS_Wire outer = BRepTools::OuterWire(face);
  std::vector<TopoDS_Edge> boundary;
  std::vector<int> occurrences(edges.Extent(), 0);
  for (BRepTools_WireExplorer explorer(outer, face); explorer.More(); explorer.Next()) {
    const TopoDS_Edge old_edge = explorer.Current();
    const int edge_index = edges.FindIndex(old_edge) - 1;
    const int vertex_index = vertices.FindIndex(explorer.CurrentVertex()) - 1;
    if (edge_index < 0 || vertex_index < 0) {
      refuse(kReasonTopology);
    }
    ++occurrences[edge_index];
    if (BRep_Tool::Degenerated(old_edge)) {
      if (replacements[edge_index].has_value()) {
        refuse(kReasonDegenerate);
      }
      continue;
    }
    if (occurrences[edge_index] > 1 || BRep_Tool::IsClosed(old_edge, face)) {
      refuse(kReasonUnsupportedBoundary);
    }
    Point start = point_of(explorer.CurrentVertex());
    if (moved.count(vertex_index) != 0) {
      start = translated(start, vector);
    }
    TopoDS_Edge boundary_edge = replacements[edge_index].value_or(old_edge);
    if (isolate_source_edges && !replacements[edge_index].has_value()) {
      BRepBuilderAPI_Copy copy(boundary_edge, false, false);
      if (!copy.IsDone()) {
        refuse(kReasonNoCandidate);
      }
      boundary_edge = TopoDS::Edge(copy.Shape());
    }
    boundary.push_back(orient_edge(boundary_edge, start, tolerance));
  }
  if (boundary.size() < 3) {
    refuse(kReasonUnsupportedBoundary);
  }
  return boundary;
}

TopoDS_Wire make_wire(const std::vector<TopoDS_Edge>& boundary) {
  BRepBuilderAPI_MakeWire maker;
  for (const TopoDS_Edge& edge : boundary) {
    maker.Add(edge);
  }
  if (!maker.IsDone()) {
    refuse(kReasonNoCandidate);
  }
  return maker.Wire();
}

std::optional<TopoDS_Face> planar_face(const std::vector<TopoDS_Edge>& boundary) {
  try {
    BRepBuilderAPI_MakeFace maker(make_wire(boundary), true);
    if (maker.IsDone() && BRepCheck_Analyzer(maker.Face(), true).IsValid()) {
      return maker.Face();
    }
  } catch (...) {
  }
  return std::nullopt;
}

Handle(Geom_BSplineCurve) bspline_edge(const TopoDS_Edge& edge, const double tolerance) {
  BRepAdaptor_Curve adaptor(edge);
  const double first = adaptor.FirstParameter();
  const double last = adaptor.LastParameter();
  double curve_first = first;
  double curve_last = last;
  Handle(Geom_Curve) curve = BRep_Tool::Curve(edge, curve_first, curve_last);
  if (curve.IsNull()) {
    refuse(kReasonUnsupportedBoundary);
  }
  Handle(Geom_BSplineCurve) bspline = GeomConvert::CurveToBSplineCurve(
      new Geom_TrimmedCurve(curve, first, last));
  const Point start = point_of(TopExp::FirstVertex(edge, true));
  const gp_Pnt first_point = bspline->StartPoint();
  const gp_Pnt last_point = bspline->EndPoint();
  const double first_distance = point_distance({first_point.X(), first_point.Y(), first_point.Z()}, start);
  const double last_distance = point_distance({last_point.X(), last_point.Y(), last_point.Z()}, start);
  if (std::min(first_distance, last_distance) > tolerance) {
    refuse(kReasonTopology);
  }
  if (last_distance < first_distance) {
    bspline->Reverse();
  }
  Handle(Geom_BSplineCurve) detached = Handle(Geom_BSplineCurve)::DownCast(bspline->Copy());
  if (detached.IsNull()) {
    refuse(kReasonNoCandidate);
  }
  return detached;
}

void normalize_bezier_weights(const Handle(Geom_BSplineCurve)& bspline) {
  // GeomFill combines boundary homogeneous coordinates directly. A trimmed rational
  // Bezier may have unequal endpoint weights, so coincident geometric corners are not
  // compatible in homogeneous space and the generated surface misses its boundaries.
  // Scaling homogeneous pole i by factor^i is the exact projective substitution
  // t = factor*u / (1-u+factor*u): poles stay fixed, only the parameterization changes.
  // Normalize only clamped single-span curves; strict boundary certification remains the
  // authority for every other rational B-spline.
  if (!bspline->IsRational() || bspline->Degree() < 1 || bspline->NbKnots() != 2 ||
      bspline->NbPoles() != bspline->Degree() + 1) {
    return;
  }
  const double first_weight = bspline->Weight(1);
  const double last_weight = bspline->Weight(bspline->NbPoles());
  if (first_weight <= 0.0 || last_weight <= 0.0) {
    return;
  }
  const double factor = std::pow(first_weight / last_weight, 1.0 / bspline->Degree());
  for (int index = 1; index <= bspline->NbPoles(); ++index) {
    bspline->SetWeight(
        index, bspline->Weight(index) * std::pow(factor, index - 1) / first_weight);
  }
}

std::pair<TopoDS_Face, Handle(Geom_BSplineSurface)> coons_face(
    const TopoDS_Face& source,
    const std::vector<Handle(Geom_BSplineCurve)>& curves,
    const double tolerance,
    const bool normalize_weights = true) {
  std::vector<Handle(Geom_BSplineCurve)> private_curves;
  private_curves.reserve(curves.size());
  for (const Handle(Geom_BSplineCurve)& curve : curves) {
    private_curves.push_back(Handle(Geom_BSplineCurve)::DownCast(curve->Copy()));
    if (private_curves.back().IsNull()) {
      refuse(kReasonNoCandidate);
    }
  }

  if (normalize_weights) {
    for (const Handle(Geom_BSplineCurve)& curve : private_curves) {
      normalize_bezier_weights(curve);
    }
  }
  Handle(Geom_BSplineSurface) surface;
  if (private_curves.size() == 3) {
    surface = GeomFill_BSplineCurves(private_curves[0], private_curves[1], private_curves[2],
                                     GeomFill_StretchStyle).Surface();
  } else if (private_curves.size() == 4) {
    surface = GeomFill_BSplineCurves(private_curves[0], private_curves[1], private_curves[2],
                                     private_curves[3], GeomFill_StretchStyle).Surface();
  } else {
    refuse(kReasonUnsupportedBoundary);
  }
  BRepBuilderAPI_MakeFace maker(surface, tolerance);
  if (!maker.IsDone() || !BRepCheck_Analyzer(maker.Face(), true).IsValid()) {
    refuse(kReasonNoCandidate);
  }
  TopoDS_Face rebuilt = maker.Face();
  // Bounded exact subset. GeomFill interpolates every supplied analytic boundary;
  // later certification still checks boundary error, topology, containment, validity,
  // airtightness and (for composite joins) normal continuity.
  if (surface->UDegree() > 8 || surface->VDegree() > 8) {
    refuse(kReasonUnsupportedBoundary);
  }
  if (source.Orientation() == TopAbs_REVERSED) {
    rebuilt = TopoDS::Face(rebuilt.Reversed());
  }
  return {rebuilt, surface};
}

std::vector<Point> edge_points(const TopoDS_Edge& edge, const std::uint32_t count) {
  BRepAdaptor_Curve adaptor(edge);
  const double first = adaptor.FirstParameter();
  const double last = adaptor.LastParameter();
  std::vector<Point> points;
  points.reserve(count);
  for (std::uint32_t index = 0; index < count; ++index) {
    const double parameter = first + (last - first) * static_cast<double>(index) /
        static_cast<double>(count - 1);
    const gp_Pnt point = adaptor.Value(parameter);
    points.push_back({point.X(), point.Y(), point.Z()});
  }
  return points;
}

double point_shape_distance(const Point& point, const TopoDS_Shape& shape) {
  BRepBuilderAPI_MakeVertex maker(gp_Pnt(point[0], point[1], point[2]));
  BRepExtrema_DistShapeShape extrema(maker.Vertex(), shape);
  extrema.Perform();
  return extrema.IsDone() ? extrema.Value() : std::numeric_limits<double>::infinity();
}

double point_curve_distance(const Point& point, const TopoDS_Edge& edge) {
  BRepAdaptor_Curve adaptor(edge);
  const double first = adaptor.FirstParameter();
  const double last = adaptor.LastParameter();
  double curve_first = first;
  double curve_last = last;
  Handle(Geom_Curve) curve = BRep_Tool::Curve(edge, curve_first, curve_last);
  if (curve.IsNull()) {
    return std::numeric_limits<double>::infinity();
  }
  GeomAPI_ProjectPointOnCurve projection(
      gp_Pnt(point[0], point[1], point[2]), curve, first, last);
  return projection.NbPoints() == 0
      ? std::numeric_limits<double>::infinity()
      : projection.LowerDistance();
}

double boundary_error(const TopoDS_Face& face,
                      const std::vector<TopoDS_Edge>& boundary,
                      const std::uint32_t sample_count) {
  std::vector<TopoDS_Edge> result_edges;
  for (TopExp_Explorer explorer(BRepTools::OuterWire(face), TopAbs_EDGE);
       explorer.More(); explorer.Next()) {
    result_edges.push_back(TopoDS::Edge(explorer.Current()));
  }
  double worst = 0.0;
  for (const TopoDS_Edge& edge : boundary) {
    for (const Point& point : edge_points(edge, sample_count)) {
      double nearest = std::numeric_limits<double>::infinity();
      for (const TopoDS_Edge& candidate : result_edges) {
        nearest = std::min(nearest, point_curve_distance(point, candidate));
      }
      worst = std::max(worst, nearest);
    }
  }
  for (const TopoDS_Edge& edge : result_edges) {
    for (const Point& point : edge_points(edge, sample_count)) {
      double nearest = std::numeric_limits<double>::infinity();
      for (const TopoDS_Edge& candidate : boundary) {
        nearest = std::min(nearest, point_curve_distance(point, candidate));
      }
      worst = std::max(worst, nearest);
    }
  }
  return worst;
}

gp_Vec unit_normal(const Handle(Geom_BSplineSurface)& surface, const double u, const double v) {
  gp_Pnt point;
  gp_Vec du;
  gp_Vec dv;
  surface->D1(u, v, point, du, dv);
  gp_Vec normal = du.Crossed(dv);
  if (normal.Magnitude() <= 1.0e-15) {
    refuse(kReasonSmoothness, kSmoothnessCrease);
  }
  normal.Normalize();
  return normal;
}

std::optional<double> full_knot_normal_jump(const Handle(Geom_BSplineSurface)& surface) {
  double u0 = 0.0;
  double u1 = 0.0;
  double v0 = 0.0;
  double v1 = 0.0;
  surface->Bounds(u0, u1, v0, v1);
  double maximum = -1.0;
  for (int axis = 0; axis < 2; ++axis) {
    const int degree = axis == 0 ? surface->UDegree() : surface->VDegree();
    const int count = axis == 0 ? surface->NbUKnots() : surface->NbVKnots();
    for (int index = 2; index < count; ++index) {
      const int multiplicity = axis == 0 ? surface->UMultiplicity(index) : surface->VMultiplicity(index);
      if (multiplicity < degree) {
        continue;
      }
      const double value = axis == 0 ? surface->UKnot(index) : surface->VKnot(index);
      const double previous = axis == 0 ? surface->UKnot(index - 1) : surface->VKnot(index - 1);
      const double next = axis == 0 ? surface->UKnot(index + 1) : surface->VKnot(index + 1);
      const double delta = std::min(value - previous, next - value) * 1.0e-6;
      if (delta <= 0.0) {
        refuse(kReasonSmoothness, kSmoothnessCrease);
      }
      double worst = 0.0;
      for (int sample = 1; sample < 10; ++sample) {
        const double cross = axis == 0 ? v0 + (v1 - v0) * sample / 10.0
                                        : u0 + (u1 - u0) * sample / 10.0;
        const gp_Vec before = axis == 0 ? unit_normal(surface, value - delta, cross)
                                         : unit_normal(surface, cross, value - delta);
        const gp_Vec after = axis == 0 ? unit_normal(surface, value + delta, cross)
                                        : unit_normal(surface, cross, value + delta);
        const double cosine = std::clamp(before.Dot(after), -1.0, 1.0);
        worst = std::max(worst, std::acos(cosine));
      }
      maximum = std::max(maximum, worst);
    }
  }
  if (maximum < 0.0) {
    return std::nullopt;
  }
  return maximum;
}

std::vector<gp_Pnt> face_interior_samples(const TopoDS_Face& face, const double tolerance) {
  BRepAdaptor_Surface adaptor(face);
  double u0 = 0.0;
  double u1 = 0.0;
  double v0 = 0.0;
  double v1 = 0.0;
  BRepTools::UVBounds(face, u0, u1, v0, v1);
  std::vector<gp_Pnt> samples;
  for (int i = 1; i < 8; ++i) {
    for (int j = 1; j < 8; ++j) {
      const double u = u0 + (u1 - u0) * i / 8.0;
      const double v = v0 + (v1 - v0) * j / 8.0;
      if (BRepClass_FaceClassifier(face, gp_Pnt2d(u, v), tolerance).State() == TopAbs_IN) {
        samples.push_back(adaptor.Value(u, v));
      }
    }
  }
  if (samples.empty()) {
    refuse(kReasonNoCandidate);
  }
  return samples;
}

double face_deformation(const TopoDS_Face& old_face,
                        const TopoDS_Face& new_face,
                        const double tolerance) {
  std::vector<double> squared;
  for (const gp_Pnt& sample : face_interior_samples(old_face, tolerance)) {
    const double distance = point_shape_distance({sample.X(), sample.Y(), sample.Z()}, new_face);
    squared.push_back(distance * distance);
  }
  for (const gp_Pnt& sample : face_interior_samples(new_face, tolerance)) {
    const double distance = point_shape_distance({sample.X(), sample.Y(), sample.Z()}, old_face);
    squared.push_back(distance * distance);
  }
  return std::sqrt(std::accumulate(squared.begin(), squared.end(), 0.0) / squared.size());
}

// Layout shared with `height_field::NarwhalHeightFieldV1` in Rust.
struct HeightFieldInfo {
  std::uint32_t u_pole_count;
  std::uint32_t v_pole_count;
  std::uint32_t pinned_sides;
  std::uint32_t reserved;
  double x_min;
  double x_max;
  double y_min;
  double y_max;
  double max_error;
};
static_assert(sizeof(HeightFieldInfo) == 56, "Rust/C++ height-field layout changed");

// MEASURED on bug #108: the cap's shape converges towards the thin-plate limit as the
// grid refines (24 spans: area within 0.1% of the reference), but BRepAlgoAPI_Check on the
// rebuilt solid took 0.12 s at 24 spans and 80 s at 32. A single 24-span grid keeps the
// edit interactive; a boundary it cannot meet is refused, not widened.
constexpr double kPi = 3.14159265358979323846;
constexpr int kHeightFieldChoice = -2;
constexpr double kHeightFieldSmoothing = 1.0e-12;
// The fit sits this many target errors outward of every unpinned edge, so the gap to a
// shared edge never changes sign (bug #109); the true gap is then 1-3 target errors,
// inside the 4x refusal limit below.
constexpr double kHeightFieldBias = 2.0;
constexpr std::uint32_t kHeightFieldMaxSpans = 24;
constexpr int kHeightFieldFitSamples = 128;
constexpr int kHeightFieldCheckSamples = 2048;

Handle(Geom_BSplineSurface) height_field_surface(const HeightFieldInfo& info,
                                                 const std::vector<double>& heights,
                                                 const gp_Trsf& to_global) {
  const int nu = static_cast<int>(info.u_pole_count);
  const int nv = static_cast<int>(info.v_pole_count);
  const auto knot_values = [](const double start, const double end, const int spans) {
    TColStd_Array1OfReal knots(1, spans + 1);
    for (int index = 0; index <= spans; ++index) {
      knots.SetValue(index + 1, index == spans ? end : start + (end - start) * index / spans);
    }
    return knots;
  };
  const auto multiplicities = [](const int spans) {
    TColStd_Array1OfInteger values(1, spans + 1);
    for (int index = 0; index <= spans; ++index) {
      values.SetValue(index + 1, index == 0 || index == spans ? 4 : 1);
    }
    return values;
  };
  // Greville abscissae: with these pole coordinates the surface parameter is the plane
  // coordinate itself, so p-curves are the exact planar projections of the edges.
  const auto greville = [](const double start, const double end, const int poles, const int i) {
    const int spans = poles - 3;
    double sum = 0.0;
    for (int offset = 1; offset <= 3; ++offset) {
      const int knot = std::clamp(i + offset - 3, 0, spans);
      sum += knot == spans ? end : start + (end - start) * knot / spans;
    }
    return sum / 3.0;
  };
  TColgp_Array2OfPnt poles(1, nu, 1, nv);
  for (int i = 0; i < nu; ++i) {
    const double x = greville(info.x_min, info.x_max, nu, i);
    for (int j = 0; j < nv; ++j) {
      const double y = greville(info.y_min, info.y_max, nv, j);
      poles.SetValue(i + 1, j + 1,
                     gp_Pnt(x, y, heights[static_cast<std::size_t>(i * nv + j)]).Transformed(to_global));
    }
  }
  return new Geom_BSplineSurface(poles, knot_values(info.x_min, info.x_max, nu - 3),
                                 knot_values(info.y_min, info.y_max, nv - 3),
                                 multiplicities(nu - 3), multiplicities(nv - 3), 3, 3);
}

Handle(Geom2d_BSplineCurve) planar_pcurve(const Handle(Geom_BSplineCurve)& curve,
                                          const gp_Trsf& to_local) {
  TColgp_Array1OfPnt2d poles(1, curve->NbPoles());
  for (int index = 1; index <= curve->NbPoles(); ++index) {
    const gp_Pnt local = curve->Pole(index).Transformed(to_local);
    poles.SetValue(index, gp_Pnt2d(local.X(), local.Y()));
  }
  TColStd_Array1OfReal knots(1, curve->NbKnots());
  TColStd_Array1OfInteger multiplicities(1, curve->NbKnots());
  curve->Knots(knots);
  curve->Multiplicities(multiplicities);
  if (curve->IsRational()) {
    TColStd_Array1OfReal weights(1, curve->NbPoles());
    curve->Weights(weights);
    return new Geom2d_BSplineCurve(poles, weights, knots, multiplicities, curve->Degree(),
                                   curve->IsPeriodic());
  }
  return new Geom2d_BSplineCurve(poles, knots, multiplicities, curve->Degree(),
                                 curve->IsPeriodic());
}

// Exact p-curve of a 3D edge curve on a height-field surface whose parameters are the
// frame's plane coordinates, sharing the edge's parameter. Null when the projection is
// not representable with that parameter (the caller then uses a B-spline copy).
Handle(Geom2d_Curve) exact_planar_pcurve(const Handle(Geom_Curve)& curve,
                                         const double first,
                                         const double last,
                                         const gp_Trsf& to_local) {
  Handle(Geom_Curve) basis = curve;
  while (const Handle(Geom_TrimmedCurve) trimmed = Handle(Geom_TrimmedCurve)::DownCast(basis)) {
    basis = trimmed->BasisCurve();
  }
  const auto flat = [&](const gp_Pnt& point) {
    const gp_Pnt local = point.Transformed(to_local);
    return gp_Pnt2d(local.X(), local.Y());
  };
  if (Handle(Geom_Line)::DownCast(basis)) {
    TColgp_Array1OfPnt2d poles(1, 2);
    poles.SetValue(1, flat(basis->Value(first)));
    poles.SetValue(2, flat(basis->Value(last)));
    if (poles(1).Distance(poles(2)) <= Precision::Confusion()) {
      return nullptr;
    }
    TColStd_Array1OfReal knots(1, 2);
    knots.SetValue(1, first);
    knots.SetValue(2, last);
    TColStd_Array1OfInteger multiplicities(1, 2);
    multiplicities.SetValue(1, 2);
    multiplicities.SetValue(2, 2);
    return new Geom2d_BSplineCurve(poles, knots, multiplicities, 1);
  }
  if (const Handle(Geom_Circle) circle = Handle(Geom_Circle)::DownCast(basis)) {
    const gp_Ax2 position = circle->Position();
    const gp_Dir axis = position.Direction().Transformed(to_local);
    if (std::abs(std::abs(axis.Z()) - 1.0) > 1.0e-12) {
      return nullptr;
    }
    const gp_Dir x = position.XDirection().Transformed(to_local);
    const gp_Dir y = position.YDirection().Transformed(to_local);
    return new Geom2d_Circle(gp_Ax22d(flat(position.Location()), gp_Dir2d(x.X(), x.Y()),
                                      gp_Dir2d(y.X(), y.Y())),
                             circle->Radius());
  }
  if (const Handle(Geom_BSplineCurve) bspline = Handle(Geom_BSplineCurve)::DownCast(basis)) {
    if (bspline->IsPeriodic()) {
      return nullptr;
    }
    return planar_pcurve(bspline, to_local);
  }
  return nullptr;
}

// Base frame for a height-field face, with +Z along the face's outward side.
//
// MEASURED (bug #109): a second edit of a cap that is already a fitted height field
// (or a first edit that moves another vertex of it) must reuse the ORIGINAL cap plane.
// Otherwise the unmoved flat sides stop being exact, and the old shape can only go
// through the composite route, which folded the cap (Jacobian ratio 4e-3) and took
// 7-15 s. The original plane is the plane through two coplanar, non-parallel straight
// boundary edges that contains the most boundary samples. A planar source uses its own
// plane. Curved sources other than near-flat B-spline graphs are refused.
std::optional<gp_Ax3> height_field_frame(const TopoDS_Face& source,
                                         const std::vector<TopoDS_Edge>& boundary,
                                         const std::vector<Handle(Geom_BSplineCurve)>& curves,
                                         const std::vector<gp_Pnt>& points,
                                         const double tolerance) {
  BRepGProp_Face properties(source);
  double u0 = 0.0;
  double u1 = 0.0;
  double v0 = 0.0;
  double v1 = 0.0;
  BRepTools::UVBounds(source, u0, u1, v0, v1);
  std::vector<gp_Dir> normals;
  gp_Vec outward(0.0, 0.0, 0.0);
  for (int i = 1; i < 10; ++i) {
    for (int j = 1; j < 10; ++j) {
      const double u = u0 + (u1 - u0) * i / 10.0;
      const double v = v0 + (v1 - v0) * j / 10.0;
      if (BRepClass_FaceClassifier(source, gp_Pnt2d(u, v), tolerance).State() != TopAbs_IN) {
        continue;
      }
      gp_Pnt point;
      gp_Vec normal;
      properties.Normal(u, v, point, normal);
      if (normal.Magnitude() > 1.0e-12) {
        normals.emplace_back(normal);
        outward += gp_Vec(normals.back());
      }
    }
  }
  if (normals.empty() || outward.Magnitude() <= 1.0e-12) {
    return std::nullopt;
  }
  outward.Normalize();
  const auto facing = [&](const gp_Dir& direction) {
    return gp_Vec(direction).Dot(outward) < 0.0 ? direction.Reversed() : direction;
  };
  BRepAdaptor_Surface adaptor(source, true);
  if (adaptor.GetType() == GeomAbs_Plane) {
    const gp_Ax3 plane = adaptor.Plane().Position();
    return gp_Ax3(plane.Location(), facing(plane.Direction()), plane.XDirection());
  }
  if (adaptor.GetType() != GeomAbs_BSplineSurface) {
    return std::nullopt;
  }
  std::optional<gp_Ax3> best;
  std::size_t best_count = 0;
  for (std::size_t a = 0; a < boundary.size(); ++a) {
    if (BRepAdaptor_Curve(boundary[a]).GetType() != GeomAbs_Line) {
      continue;
    }
    const gp_Pnt origin = curves[a]->StartPoint();
    const gp_Vec first(origin, curves[a]->EndPoint());
    for (std::size_t b = a + 1; b < boundary.size(); ++b) {
      if (BRepAdaptor_Curve(boundary[b]).GetType() != GeomAbs_Line) {
        continue;
      }
      const gp_Vec second(curves[b]->StartPoint(), curves[b]->EndPoint());
      const gp_Vec cross = first.Crossed(second);
      if (cross.Magnitude() <= 1.0e-6 * first.Magnitude() * second.Magnitude()) {
        continue;
      }
      const gp_Dir normal = facing(gp_Dir(cross));
      if (std::abs(gp_Vec(origin, curves[b]->StartPoint()).Dot(gp_Vec(normal))) > tolerance) {
        continue;
      }
      // The old face must be a graph over the candidate plane (no steep or folded
      // region): a vertical plane through two coplanar side lines is not the cap plane.
      if (std::any_of(normals.begin(), normals.end(), [&](const gp_Dir& face_normal) {
            return face_normal.Dot(normal) < 0.2;
          })) {
        continue;
      }
      const std::size_t count = static_cast<std::size_t>(std::count_if(
          points.begin(), points.end(), [&](const gp_Pnt& point) {
            return std::abs(gp_Vec(origin, point).Dot(gp_Vec(normal))) <= tolerance;
          }));
      if (count > best_count) {
        best_count = count;
        best = gp_Ax3(origin, normal, gp_Dir(first));
      }
    }
  }
  if (!best.has_value()) {
    return std::nullopt;
  }
  return best;
}

// MEASURED (bug #108): when a vertex leaves a planar cap, the reference rebuilds the cap as a
// smooth bicubic height field over the original plane. OURS: the fit itself is in Rust
// (`height_field.rs`); here the exact boundary edges keep their 3D curves, get exact
// planar p-curves, and carry the measured surface gap as their tolerance -- refused
// outright when that gap exceeds the limit, never silently widened.
std::optional<FaceOption> height_field_option(
    const TopoDS_Face& source,
    const std::vector<TopoDS_Edge>& boundary,
    const Tolerances& tolerances,
    const AdapterMovePolicy& policy) {
  try {
    std::vector<Handle(Geom_BSplineCurve)> curves;
    std::vector<gp_Pnt> points;
    points.reserve(boundary.size() * kHeightFieldFitSamples);
    for (const TopoDS_Edge& edge : boundary) {
      const Handle(Geom_BSplineCurve) curve = bspline_edge(edge, tolerances.build);
      curves.push_back(curve);
      const double first = curve->FirstParameter();
      const double last = curve->LastParameter();
      for (int index = 0; index < kHeightFieldFitSamples; ++index) {
        // Cosine spacing: corners are where a smooth height field bends hardest.
        const double fraction =
            0.5 - 0.5 * std::cos(kPi * static_cast<double>(index) / kHeightFieldFitSamples);
        points.push_back(curve->Value(first + (last - first) * fraction));
      }
    }
    const std::optional<gp_Ax3> frame = height_field_frame(
        source, boundary, curves, points,
        std::max(tolerances.build, tolerances.extent * 1.0e-9));
    if (!frame.has_value()) {
      return std::nullopt;
    }
    gp_Trsf to_local;
    to_local.SetTransformation(*frame);
    const gp_Trsf to_global = to_local.Inverted();

    std::vector<double> samples;
    samples.reserve(points.size() * 3);
    double twice_area = 0.0;
    gp_Pnt previous;
    bool have_previous = false;
    for (const gp_Pnt& point : points) {
      const gp_Pnt local = point.Transformed(to_local);
      samples.insert(samples.end(), {local.X(), local.Y(), local.Z()});
      if (have_previous) {
        twice_area += previous.X() * local.Y() - local.X() * previous.Y();
      }
      previous = local;
      have_previous = true;
    }
    twice_area += previous.X() * samples[1] - samples[0] * previous.Y();

    // Relative to the part only, never to the input's tolerance: an input that already
    // carries a fitted cap's edge tolerance would otherwise loosen the target on every
    // edit (MEASURED bug #109: 1.2e-4 -> 5.6e-4 -> 2e-3 mm over two edits).
    const double target = std::max(Precision::Confusion(), tolerances.extent * 1.0e-6);
    const std::size_t capacity = static_cast<std::size_t>(kHeightFieldMaxSpans + 3) *
                                 static_cast<std::size_t>(kHeightFieldMaxSpans + 3);
    std::vector<double> heights(capacity, 0.0);
    HeightFieldInfo info{};
    const HeightFieldSolver solver = g_height_field_solver.load();
    if (solver == nullptr) {
      return std::nullopt;
    }
    if (solver(
            samples.data(), samples.size() / 3, kHeightFieldSmoothing, target,
            kHeightFieldMaxSpans, kHeightFieldBias * target, heights.data(), heights.size(),
            &info) != 0) {
      return std::nullopt;
    }
    const Handle(Geom_BSplineSurface) surface = height_field_surface(info, heights, to_global);

    BRep_Builder builder;
    TopoDS_Face face;
    builder.MakeFace(face, surface, tolerances.build);
    std::vector<TopoDS_Vertex> corners(curves.size());
    for (std::size_t index = 0; index < curves.size(); ++index) {
      builder.MakeVertex(corners[index], curves[index]->StartPoint(), tolerances.build);
    }
    std::vector<TopoDS_Edge> edges;
    std::vector<double> gaps;
    double worst = 0.0;
    for (std::size_t index = 0; index < curves.size(); ++index) {
      // Keep the edge's own analytic curve (line, circle, B-spline) when its planar
      // projection is exactly representable with the same parameter. MEASURED (bug #109):
      // with B-spline copies on the cap, sewing kept either representation depending on
      // face order, so a straight edge silently became a degree-1 B-spline on some edits.
      const TopoDS_Edge& source_edge = boundary[index];
      double first = 0.0;
      double last = 0.0;
      Handle(Geom_Curve) curve = BRep_Tool::Curve(source_edge, first, last);
      bool reversed = source_edge.Orientation() == TopAbs_REVERSED;
      Handle(Geom2d_Curve) pcurve =
          curve.IsNull() ? Handle(Geom2d_Curve)() : exact_planar_pcurve(curve, first, last, to_local);
      if (pcurve.IsNull()) {
        curve = curves[index];
        first = curves[index]->FirstParameter();
        last = curves[index]->LastParameter();
        reversed = false;
        pcurve = planar_pcurve(curves[index], to_local);
      }
      double gap = 0.0;
      for (int sample = 0; sample <= kHeightFieldCheckSamples; ++sample) {
        const double fraction =
            0.5 - 0.5 * std::cos(kPi * static_cast<double>(sample) / kHeightFieldCheckSamples);
        const double parameter = first + (last - first) * fraction;
        const gp_Pnt2d uv = pcurve->Value(parameter);
        gap = std::max(gap, curve->Value(parameter).Distance(surface->Value(uv.X(), uv.Y())));
      }
      worst = std::max(worst, gap);
      gaps.push_back(gap);
      const TopoDS_Vertex& loop_start = corners[index];
      const TopoDS_Vertex& loop_end = corners[(index + 1) % corners.size()];
      const TopoDS_Vertex start =
          TopoDS::Vertex((reversed ? loop_end : loop_start).Oriented(TopAbs_FORWARD));
      const TopoDS_Vertex end =
          TopoDS::Vertex((reversed ? loop_start : loop_end).Oriented(TopAbs_REVERSED));
      if (curve->Value(first).Distance(BRep_Tool::Pnt(start)) > tolerances.build ||
          curve->Value(last).Distance(BRep_Tool::Pnt(end)) > tolerances.build) {
        return std::nullopt;
      }
      TopoDS_Edge edge;
      builder.MakeEdge(edge, curve, std::max(tolerances.build, gap * 1.5));
      builder.Add(edge, start);
      builder.Add(edge, end);
      builder.Range(edge, first, last);
      builder.UpdateEdge(edge, pcurve, face, std::max(tolerances.build, gap * 1.5));
      builder.Range(edge, face, first, last);
      builder.SameParameter(edge, true);
      builder.SameRange(edge, true);
      edges.push_back(reversed ? TopoDS::Edge(edge.Reversed()) : edge);
    }
    if (worst > 4.0 * target) {
      return std::nullopt;
    }
    for (std::size_t index = 0; index < corners.size(); ++index) {
      const double gap = std::max(gaps[index], gaps[(index + corners.size() - 1) % corners.size()]);
      builder.UpdateVertex(corners[index], std::max(tolerances.build, gap * 1.5));
    }
    TopoDS_Wire wire;
    builder.MakeWire(wire);
    for (const TopoDS_Edge& edge : edges) {
      builder.Add(wire, edge);
    }
    wire.Closed(true);
    // The frame's +Z is the source's outward side, so the natural face orientation is
    // already the outward one.
    builder.Add(face, twice_area < 0.0 ? wire.Reversed() : wire);
    if (!BRepCheck_Analyzer(face, true).IsValid()) {
      return std::nullopt;
    }
    FaceOption option;
    option.face = face;
    option.reconstructed = true;
    option.reconstructed_bspline = true;
    option.boundary_error = boundary_error(face, boundary, policy.edge_samples);
    option.merge_choice = kHeightFieldChoice;
    option.rank = {
        static_cast<double>(std::max(surface->UDegree(), surface->VDegree())),
        static_cast<double>(surface->UDegree() + surface->VDegree()),
        static_cast<double>(surface->NbUPoles() * surface->NbVPoles()),
        static_cast<double>(surface->NbUPoles() + surface->NbVPoles()),
        face_deformation(source, face, tolerances.compare)};
    return option;
  } catch (const Standard_Failure&) {
    return std::nullopt;
  } catch (const Refusal&) {
    return std::nullopt;
  }
}

// Sewing re-derives edge tolerances from a coarse sample, which can sit below the real
// surface-to-edge gap of a fitted face. Re-measure every edge on every face densely and
// only ever RAISE a tolerance, so the stored value bounds the true gap.
void raise_tolerances_to_true_gaps(const TopoDS_Shape& shape) {
  constexpr int kSamples = 512;
  BRep_Builder builder;
  for (TopExp_Explorer faces(shape, TopAbs_FACE); faces.More(); faces.Next()) {
    const TopoDS_Face face = TopoDS::Face(faces.Current());
    const Handle(Geom_Surface) surface = BRep_Tool::Surface(face);
    if (surface.IsNull()) {
      continue;
    }
    for (TopExp_Explorer edges(face, TopAbs_EDGE); edges.More(); edges.Next()) {
      const TopoDS_Edge edge = TopoDS::Edge(edges.Current());
      if (BRep_Tool::Degenerated(edge) || !BRep_Tool::SameParameter(edge)) {
        continue;
      }
      double first = 0.0;
      double last = 0.0;
      const Handle(Geom2d_Curve) pcurve = BRep_Tool::CurveOnSurface(edge, face, first, last);
      double curve_first = 0.0;
      double curve_last = 0.0;
      const Handle(Geom_Curve) curve = BRep_Tool::Curve(edge, curve_first, curve_last);
      if (pcurve.IsNull() || curve.IsNull()) {
        continue;
      }
      double gap = 0.0;
      for (int sample = 0; sample <= kSamples; ++sample) {
        const double fraction = 0.5 - 0.5 * std::cos(kPi * static_cast<double>(sample) / kSamples);
        const double parameter = first + (last - first) * fraction;
        const gp_Pnt2d uv = pcurve->Value(parameter);
        gap = std::max(gap, curve->Value(parameter).Distance(surface->Value(uv.X(), uv.Y())));
      }
      const double tolerance = gap * 1.5;
      if (tolerance > BRep_Tool::Tolerance(edge)) {
        builder.UpdateEdge(edge, tolerance);
      }
      for (TopExp_Explorer vertices(edge, TopAbs_VERTEX); vertices.More(); vertices.Next()) {
        const TopoDS_Vertex vertex = TopoDS::Vertex(vertices.Current());
        if (tolerance > BRep_Tool::Tolerance(vertex)) {
          builder.UpdateVertex(vertex, tolerance);
        }
      }
    }
  }
}

std::vector<FaceOption> composite_options(const TopoDS_Face& face,
                                          const std::vector<TopoDS_Edge>& boundary,
                                          const Tolerances& tolerances,
                                          const AdapterMovePolicy& policy,
                                          const bool prefer_deformed_chain,
                                          bool& preferred_filter_used) {
  const int edge_count = static_cast<int>(boundary.size());
  if (edge_count < 5 || edge_count > 6) {
    refuse(kReasonUnsupportedBoundary);
  }
  std::vector<Handle(Geom_BSplineCurve)> curves;
  for (const TopoDS_Edge& edge : boundary) {
    curves.push_back(bspline_edge(edge, tolerances.build));
  }
  std::vector<FaceOption> candidates;
  std::vector<std::array<int, 3>> preferred_triples;
  if (edge_count == 6) {
    BRepAdaptor_Surface source_surface(face, true);
    if (source_surface.GetType() == GeomAbs_Plane) {
      const gp_Pln plane = source_surface.Plane();
      std::vector<int> deformed_edges;
      for (int index = 0; index < edge_count; ++index) {
        const gp_Pnt first = BRep_Tool::Pnt(TopExp::FirstVertex(boundary[index], true));
        const gp_Pnt last = BRep_Tool::Pnt(TopExp::LastVertex(boundary[index], true));
        if (plane.Distance(first) > tolerances.build ||
            plane.Distance(last) > tolerances.build) {
          deformed_edges.push_back(index);
        }
      }
      if (prefer_deformed_chain && deformed_edges.size() == 2) {
        for (const int first : deformed_edges) {
          const int second = (first + 1) % edge_count;
          if (std::find(deformed_edges.begin(), deformed_edges.end(), second) !=
              deformed_edges.end()) {
            preferred_triples.push_back({first, second, (second + 1) % edge_count});
            preferred_filter_used = true;
            break;
          }
        }
      }
    }
  }
  int partition_index = 0;
  // Choose four cyclic group starts. This enumerates C(5,4)=5 or C(6,4)=15 exact
  // partitions, safely below the 25-candidate policy ceiling. When one vertex leaves a
  // planar six-edge face, first try the oriented partition that keeps both consecutive
  // deformed edges and their following neighbor in one three-edge composite. If that
  // candidate fails full certification, move_component retries all 15 partitions. Every source edge remains on
  // one composite boundary with full-multiplicity joins; sewing and the later
  // bidirectional boundary/topology certificates remain authoritative.
  for (int a = 0; a < edge_count - 3; ++a) {
    for (int b = a + 1; b < edge_count - 2; ++b) {
      for (int c = b + 1; c < edge_count - 1; ++c) {
        for (int d = c + 1; d < edge_count; ++d, ++partition_index) {
          const std::array<int, 4> starts{a, b, c, d};
          std::array<std::vector<int>, 4> groups;
          for (int group = 0; group < 4; ++group) {
            const int stop = starts[(group + 1) % 4];
            int index = starts[group];
            do {
              groups[group].push_back(index);
              index = (index + 1) % edge_count;
            } while (index != stop);
          }
          if (!preferred_triples.empty()) {
            const bool preferred = std::any_of(
                groups.begin(), groups.end(), [&](const std::vector<int>& group) {
                  return group.size() == 3 && std::any_of(
                      preferred_triples.begin(), preferred_triples.end(),
                      [&](const std::array<int, 3>& triple) {
                        return std::equal(group.begin(), group.end(), triple.begin());
                      });
                });
            if (!preferred) {
              continue;
            }
          }
          std::vector<Handle(Geom_BSplineCurve)> composite;
          bool joined = true;
          for (const std::vector<int>& group : groups) {
            Handle(Geom_BSplineCurve) first = Handle(Geom_BSplineCurve)::DownCast(
                curves[group.front()]->Copy());
            if (first.IsNull()) {
              joined = false;
              break;
            }
            GeomConvert_CompCurveToBSplineCurve joiner(first);
            for (std::size_t member = 1; member < group.size(); ++member) {
              Handle(Geom_BSplineCurve) next = Handle(Geom_BSplineCurve)::DownCast(
                  curves[group[member]]->Copy());
              if (next.IsNull()) {
                joined = false;
                break;
              }
              const int full_multiplicity =
                  std::max(joiner.BSplineCurve()->Degree(), next->Degree());
              if (!joiner.Add(next, tolerances.build, true, true, full_multiplicity)) {
                joined = false;
                break;
              }
            }
            if (joined) {
              composite.push_back(joiner.BSplineCurve());
            }
          }
          if (!joined || composite.size() != 4) {
            continue;
          }
          try {
            auto rebuilt = coons_face(face, composite, tolerances.build, false);
            FaceOption option;
            option.face = rebuilt.first;
            option.reconstructed = true;
            option.reconstructed_bspline = true;
            option.boundary_error = boundary_error(option.face, boundary, policy.edge_samples);
            const std::optional<double> jump = full_knot_normal_jump(rebuilt.second);
            if (!jump.has_value()) {
              continue;
            }
            option.internal_knot_checked = true;
            option.merge_choice = partition_index;
            option.normal_jump = *jump;
            option.rank = {
                static_cast<double>(std::max(rebuilt.second->UDegree(), rebuilt.second->VDegree())),
                static_cast<double>(rebuilt.second->UDegree() + rebuilt.second->VDegree()),
                static_cast<double>(rebuilt.second->NbUPoles() * rebuilt.second->NbVPoles()),
                static_cast<double>(rebuilt.second->NbUPoles() + rebuilt.second->NbVPoles()),
                face_deformation(face, option.face, tolerances.compare)};
            candidates.push_back(option);
          } catch (const Refusal&) {
          } catch (const Standard_Failure&) {
          }
        }
      }
    }
  }
  if (candidates.empty()) {
    refuse(kReasonNoCandidate);
  }
  return candidates;
}

std::vector<FaceOption> rebuild_face_options(const TopoDS_Face& face,
                                              const std::vector<TopoDS_Edge>& boundary,
                                              const Tolerances& tolerances,
                                              const AdapterMovePolicy& policy,
                                              const int cached_strategy,
                                              const bool prefer_deformed_chain,
                                              bool& preferred_filter_used) {
  if (cached_strategy != 2 && cached_strategy != 3) {
    if (const auto planar = planar_face(boundary)) {
      FaceOption option;
      option.face = *planar;
      option.reconstructed = true;
      return {option};
    }
    if (cached_strategy == 1) {
      refuse(kReasonNoCandidate);
    }
  }
  if (boundary.size() == 6 && prefer_deformed_chain) {
    if (const std::optional<FaceOption> smooth =
            height_field_option(face, boundary, tolerances, policy)) {
      preferred_filter_used = true;
      return {*smooth};
    }
  }
  if (boundary.size() == 5 || boundary.size() == 6) {
    return composite_options(face, boundary, tolerances, policy,
                             prefer_deformed_chain, preferred_filter_used);
  }
  if (boundary.size() != 3 && boundary.size() != 4) {
    refuse(kReasonUnsupportedBoundary);
  }
  std::vector<Handle(Geom_BSplineCurve)> curves;
  for (const TopoDS_Edge& edge : boundary) {
    curves.push_back(bspline_edge(edge, tolerances.build));
  }
  auto rebuilt = coons_face(face, curves, tolerances.build);
  FaceOption option;
  option.face = rebuilt.first;
  option.reconstructed = true;
  option.reconstructed_bspline = true;
  option.boundary_error = boundary_error(option.face, boundary, policy.edge_samples);
  if (const std::optional<double> jump = full_knot_normal_jump(rebuilt.second)) {
    option.internal_knot_checked = true;
    option.normal_jump = *jump;
  }
  option.rank = {static_cast<double>(std::max(rebuilt.second->UDegree(), rebuilt.second->VDegree())),
                 static_cast<double>(rebuilt.second->UDegree() + rebuilt.second->VDegree()),
                 static_cast<double>(rebuilt.second->NbUPoles() * rebuilt.second->NbVPoles()),
                 static_cast<double>(rebuilt.second->NbUPoles() + rebuilt.second->NbVPoles()), 0.0};
  return {option};
}

std::vector<gp_Pnt> part_samples(const TopoDS_Shape& part, const std::uint32_t kind) {
  if (kind == 2) {
    std::vector<gp_Pnt> samples;
    for (const Point& point : edge_points(TopoDS::Edge(part), 21)) {
      samples.emplace_back(point[0], point[1], point[2]);
    }
    return samples;
  }
  std::vector<gp_Pnt> samples = face_interior_samples(TopoDS::Face(part), 1.0e-9);
  std::vector<TopoDS_Edge> seen;
  for (TopExp_Explorer explorer(part, TopAbs_EDGE); explorer.More(); explorer.Next()) {
    const TopoDS_Edge edge = TopoDS::Edge(explorer.Current());
    if (BRep_Tool::Degenerated(edge) ||
        std::any_of(seen.begin(), seen.end(), [&](const TopoDS_Edge& old) { return old.IsSame(edge); })) {
      continue;
    }
    seen.push_back(edge);
    for (const Point& point : edge_points(edge, 21)) {
      samples.emplace_back(point[0], point[1], point[2]);
    }
  }
  for (TopExp_Explorer explorer(part, TopAbs_VERTEX); explorer.More(); explorer.Next()) {
    samples.push_back(BRep_Tool::Pnt(TopoDS::Vertex(explorer.Current())));
  }
  return samples;
}

double part_measure(const TopoDS_Shape& part, const std::uint32_t kind) {
  GProp_GProps properties;
  if (kind == 2) {
    BRepGProp::LinearProperties(part, properties);
  } else {
    BRepGProp::SurfaceProperties(part, properties, 1.0e-10);
  }
  return properties.Mass();
}

enum class EdgeSemantic : std::uint32_t {
  Line = 1,
  Circle = 2,
  Ellipse = 3,
  Bspline = 4,
  Degenerated = 5,
};

EdgeSemantic edge_semantic(const TopoDS_Edge& edge) {
  if (BRep_Tool::Degenerated(edge)) {
    return EdgeSemantic::Degenerated;
  }
  BRepAdaptor_Curve adaptor(edge);
  switch (adaptor.GetType()) {
    case GeomAbs_Line: return EdgeSemantic::Line;
    case GeomAbs_Circle: return EdgeSemantic::Circle;
    case GeomAbs_Ellipse: return EdgeSemantic::Ellipse;
    default: break;
  }
  if (adaptor.GetType() == GeomAbs_BSplineCurve) {
    double first = adaptor.FirstParameter();
    double last = adaptor.LastParameter();
    Handle(Geom_Curve) curve = BRep_Tool::Curve(edge, first, last);
    if (!curve.IsNull()) {
      Handle(Geom_Curve) trimmed = new Geom_TrimmedCurve(curve, first, last);
      const gp_Pnt start = adaptor.Value(first);
      const gp_Pnt finish = adaptor.Value(last);
      const gp_Vec chord(start, finish);
      bool collinear = chord.Magnitude() > Precision::Confusion();
      for (int sample = 1; collinear && sample < 9; ++sample) {
        const gp_Pnt value = adaptor.Value(first + (last - first) * sample / 9.0);
        collinear = gp_Vec(start, value).Crossed(chord).Magnitude() /
                        chord.Magnitude() <= Precision::PConfusion();
      }
      if (collinear) {
        GeomConvert_CurveToAnaCurve line_converter(trimmed);
        line_converter.SetTarget(GeomAbs_Line);
        Handle(Geom_Curve) converted;
        double converted_first = first;
        double converted_last = last;
        if (line_converter.ConvertToAnalytical(
                Precision::PConfusion(), converted, first, last,
                converted_first, converted_last) &&
            line_converter.Gap() <= Precision::PConfusion()) {
          return EdgeSemantic::Line;
        }
      }
      for (const auto target : {GeomAbs_Circle, GeomAbs_Ellipse}) {
        GeomConvert_CurveToAnaCurve converter(trimmed);
        converter.SetTarget(target);
        Handle(Geom_Curve) converted;
        double converted_first = first;
        double converted_last = last;
        if (converter.ConvertToAnalytical(Precision::PConfusion(), converted, first, last,
                                          converted_first, converted_last) &&
            converter.Gap() <= Precision::PConfusion()) {
          if (target == GeomAbs_Circle) {
            return EdgeSemantic::Circle;
          }
          return EdgeSemantic::Ellipse;
        }
      }
    }
  }
  return EdgeSemantic::Bspline;
}

bool same_part_type(const TopoDS_Shape& first,
                    const TopoDS_Shape& second,
                    const std::uint32_t kind) {
  if (kind == 1) {
    return BRepAdaptor_Surface(TopoDS::Face(first)).GetType() ==
           BRepAdaptor_Surface(TopoDS::Face(second)).GetType();
  }
  return edge_semantic(TopoDS::Edge(first)) == edge_semantic(TopoDS::Edge(second));
}

bool translated_match(const TopoDS_Shape& old_part,
                      const TopoDS_Shape& candidate,
                      const std::uint32_t kind,
                      const Point& vector,
                      const double tolerance,
                      double& maximum_error,
                      const bool require_same_type = true) {
  if (require_same_type && !same_part_type(old_part, candidate, kind)) {
    return false;
  }
  const double old_mass = part_measure(old_part, kind);
  const double new_mass = part_measure(candidate, kind);
  if (std::abs(new_mass - old_mass) > 2.5e-7 * std::max(tolerance, std::abs(old_mass))) {
    return false;
  }
  maximum_error = 0.0;
  for (const gp_Pnt& sample : part_samples(old_part, kind)) {
    const Point moved = translated({sample.X(), sample.Y(), sample.Z()}, vector);
    const double error = kind == 2
        ? point_curve_distance(moved, TopoDS::Edge(candidate))
        : point_shape_distance(moved, candidate);
    maximum_error = std::max(maximum_error, error);
    if (error > tolerance) {
      return false;
    }
  }
  const Point reverse{-vector[0], -vector[1], -vector[2]};
  for (const gp_Pnt& sample : part_samples(candidate, kind)) {
    const Point restored = translated({sample.X(), sample.Y(), sample.Z()}, reverse);
    const double error = kind == 2
        ? point_curve_distance(restored, TopoDS::Edge(old_part))
        : point_shape_distance(restored, old_part);
    maximum_error = std::max(maximum_error, error);
    if (error > tolerance) {
      return false;
    }
  }
  return true;
}

struct ComponentSignature {
  std::uint32_t type = 0;
  double measure = 0.0;
  Point centroid{};
};

struct MoveSource {
  TopoDS_Shape shape;
  Tolerances tolerances;
  bool requires_airtight = true;
  ShapeMap faces;
  ShapeMap edges;
  ShapeMap vertices;
  std::vector<std::vector<int>> edge_vertices;
  std::vector<std::vector<int>> face_edges;
  std::vector<ComponentSignature> face_signatures;
  std::vector<ComponentSignature> edge_signatures;
  std::vector<Point> vertex_signatures;
};

ComponentSignature component_signature(const TopoDS_Shape& part,
                                       const std::uint32_t kind) {
  GProp_GProps properties;
  if (kind == 1) {
    BRepGProp::SurfaceProperties(part, properties, 1.0e-10);
  } else {
    BRepGProp::LinearProperties(part, properties);
  }
  const gp_Pnt center = properties.CentreOfMass();
  const std::uint32_t type = kind == 1
      ? static_cast<std::uint32_t>(BRepAdaptor_Surface(TopoDS::Face(part)).GetType())
      : static_cast<std::uint32_t>(edge_semantic(TopoDS::Edge(part)));
  return {type, properties.Mass(), {center.X(), center.Y(), center.Z()}};
}

MoveSource prepare_move_source(const TopoDS_Shape& shape,
                               const AdapterMovePolicy& policy) {
  if (!within_resource_caps(shape, policy.max_faces, policy.max_edges, policy.max_vertices)) {
    refuse(kReasonResourceLimit, kLimitExceeded);
  }
  const bool airtight = is_airtight(shape);
  const bool single_face = shape.ShapeType() == TopAbs_FACE &&
      BRepCheck_Analyzer(shape, true).IsValid();
  if (!airtight && !single_face) {
    refuse(kReasonInputNotAirtight);
  }
  MoveSource source{shape, local_tolerances(shape), airtight};
  map_shapes(shape, TopAbs_FACE, source.faces);
  map_shapes(shape, TopAbs_EDGE, source.edges);
  map_shapes(shape, TopAbs_VERTEX, source.vertices);
  source.edge_vertices.reserve(source.edges.Extent());
  source.edge_signatures.reserve(source.edges.Extent());
  for (int index = 1; index <= source.edges.Extent(); ++index) {
    const TopoDS_Edge edge = TopoDS::Edge(source.edges.FindKey(index));
    source.edge_vertices.push_back(edge_vertex_indices(edge, source.vertices));
    source.edge_signatures.push_back(component_signature(edge, 2));
  }
  source.face_edges.reserve(source.faces.Extent());
  source.face_signatures.reserve(source.faces.Extent());
  for (int index = 1; index <= source.faces.Extent(); ++index) {
    const TopoDS_Face face = TopoDS::Face(source.faces.FindKey(index));
    source.face_edges.push_back(face_edge_indices(face, source.edges));
    source.face_signatures.push_back(component_signature(face, 1));
  }
  source.vertex_signatures.reserve(source.vertices.Extent());
  for (int index = 1; index <= source.vertices.Extent(); ++index) {
    source.vertex_signatures.push_back(point_of(TopoDS::Vertex(source.vertices.FindKey(index))));
  }
  return source;
}

MoveSource isolated_move_source(const MoveSource& cached) {
  BRepBuilderAPI_Copy copier(cached.shape, false, false);
  if (!copier.IsDone()) {
    refuse(kReasonNoCandidate);
  }
  MoveSource working{copier.Shape(), cached.tolerances, cached.requires_airtight};
  for (int index = 1; index <= cached.faces.Extent(); ++index) {
    const TopoDS_Shape copied = copier.ModifiedShape(cached.faces.FindKey(index));
    if (copied.IsNull()) {
      refuse(kReasonTopology);
    }
    working.faces.Add(copied);
  }
  for (int index = 1; index <= cached.edges.Extent(); ++index) {
    const TopoDS_Shape copied = copier.ModifiedShape(cached.edges.FindKey(index));
    if (copied.IsNull()) {
      refuse(kReasonTopology);
    }
    working.edges.Add(copied);
  }
  for (int index = 1; index <= cached.vertices.Extent(); ++index) {
    const TopoDS_Shape copied = copier.ModifiedShape(cached.vertices.FindKey(index));
    if (copied.IsNull()) {
      refuse(kReasonTopology);
    }
    working.vertices.Add(copied);
  }
  working.edge_vertices = cached.edge_vertices;
  working.face_edges = cached.face_edges;
  working.face_signatures = cached.face_signatures;
  working.edge_signatures = cached.edge_signatures;
  working.vertex_signatures = cached.vertex_signatures;
  return working;
}

struct MappingChecks {
  bool moved_vertices = false;
  bool incidence = false;
  bool unchanged_geometry = false;
  std::uint32_t failure_stage = 0;
  std::uint32_t failure_detail = 0;
};

MappingChecks verify_mapped_topology(
    const ShapeMap& old_faces,
    const ShapeMap& old_edges,
    const ShapeMap& old_vertices,
    const ShapeMap& new_faces,
    const ShapeMap& new_edges,
    const ShapeMap& new_vertices,
    const std::vector<std::vector<int>>& old_edge_vertices,
    const std::vector<std::vector<int>>& old_face_edges,
    const std::vector<std::optional<TopoDS_Edge>>& replacements,
    const std::set<int>& changed_edges,
    const std::set<int>& changed_faces,
    const std::set<int>& moved,
    const Point& vector,
    const double tolerance) {
  MappingChecks checks;
  if (old_vertices.Extent() != new_vertices.Extent() ||
      old_edges.Extent() != new_edges.Extent() || old_faces.Extent() != new_faces.Extent()) {
    return checks;
  }

  std::vector<int> vertex_map(old_vertices.Extent(), -1);
  std::set<int> available_vertices;
  for (int index = 0; index < new_vertices.Extent(); ++index) {
    available_vertices.insert(index);
  }
  for (int index = 0; index < old_vertices.Extent(); ++index) {
    Point expected = point_of(TopoDS::Vertex(old_vertices.FindKey(index + 1)));
    if (moved.count(index) != 0) {
      expected = translated(expected, vector);
    }
    std::vector<int> matches;
    for (const int candidate : available_vertices) {
      if (point_distance(expected, point_of(TopoDS::Vertex(new_vertices.FindKey(candidate + 1)))) <=
          tolerance) {
        matches.push_back(candidate);
      }
    }
    if (matches.size() != 1) {
      return checks;
    }
    vertex_map[index] = matches.front();
    available_vertices.erase(matches.front());
  }
  if (!available_vertices.empty()) {
    return checks;
  }
  checks.moved_vertices = true;

  std::vector<std::vector<int>> new_edge_vertices;
  for (int index = 1; index <= new_edges.Extent(); ++index) {
    new_edge_vertices.push_back(
        edge_vertex_indices(TopoDS::Edge(new_edges.FindKey(index)), new_vertices));
  }
  std::vector<std::vector<int>> edge_candidates(old_edges.Extent());
  for (int old_index = 0; old_index < old_edges.Extent(); ++old_index) {
    std::vector<int> expected;
    for (const int vertex : old_edge_vertices[old_index]) {
      expected.push_back(vertex_map[vertex]);
    }
    std::sort(expected.begin(), expected.end());
    const TopoDS_Edge expected_edge = replacements[old_index].value_or(
        TopoDS::Edge(old_edges.FindKey(old_index + 1)));
    const EdgeSemantic old_semantic = edge_semantic(expected_edge);
    bool endpoint_match = false;
    bool semantic_match = false;
    bool geometry_match = false;
    for (int candidate = 0; candidate < new_edges.Extent(); ++candidate) {
      std::vector<int> actual = new_edge_vertices[candidate];
      std::sort(actual.begin(), actual.end());
      const TopoDS_Edge candidate_edge = TopoDS::Edge(new_edges.FindKey(candidate + 1));
      if (actual == expected) {
        endpoint_match = true;
      }
      if (changed_edges.count(old_index) != 0) {
        if (actual == expected) {
          edge_candidates[old_index].push_back(candidate);
          geometry_match = true;
        }
        continue;
      }
      const EdgeSemantic candidate_semantic = edge_semantic(candidate_edge);
      if (actual == expected && old_semantic == candidate_semantic) {
        semantic_match = true;
      }
      if (old_semantic == EdgeSemantic::Degenerated &&
          candidate_semantic == EdgeSemantic::Degenerated) {
        edge_candidates[old_index].push_back(candidate);
        continue;
      }
      if (actual != expected) {
        continue;
      }
      if (expected_edge.IsSame(candidate_edge)) {
        edge_candidates[old_index].push_back(candidate);
        geometry_match = true;
        continue;
      }
      double error = 0.0;
      if (translated_match(expected_edge, candidate_edge, 2,
                           {0.0, 0.0, 0.0},
                           std::max(tolerance, Precision::Confusion()), error,
                           false)) {
        edge_candidates[old_index].push_back(candidate);
        geometry_match = true;
      }
    }
    if (edge_candidates[old_index].empty()) {
      checks.failure_stage = 2;
      checks.failure_detail = static_cast<std::uint32_t>(old_index + 1) |
          (static_cast<std::uint32_t>(changed_edges.count(old_index) != 0) << 31) |
          (static_cast<std::uint32_t>(endpoint_match) << 30) |
          (static_cast<std::uint32_t>(semantic_match) << 29) |
          (static_cast<std::uint32_t>(geometry_match) << 28);
      return checks;
    }
  }
  std::vector<int> order(old_edges.Extent());
  std::iota(order.begin(), order.end(), 0);
  std::sort(order.begin(), order.end(), [&](const int first, const int second) {
    return edge_candidates[first].size() < edge_candidates[second].size();
  });
  std::vector<int> edge_map(old_edges.Extent(), -1);
  std::vector<int> unique_edge_map;
  std::set<int> used_edges;
  int edge_mapping_count = 0;
  std::function<void(std::size_t)> assign_edge = [&](const std::size_t position) {
    if (edge_mapping_count > 1) {
      return;
    }
    if (position == order.size()) {
      ++edge_mapping_count;
      unique_edge_map = edge_map;
      return;
    }
    const int old_index = order[position];
    for (const int candidate : edge_candidates[old_index]) {
      if (used_edges.count(candidate) != 0) {
        continue;
      }
      used_edges.insert(candidate);
      edge_map[old_index] = candidate;
      assign_edge(position + 1);
      used_edges.erase(candidate);
      edge_map[old_index] = -1;
    }
  };
  assign_edge(0);
  if (edge_mapping_count != 1) {
    checks.failure_stage = 3;
    checks.failure_detail = static_cast<std::uint32_t>(edge_mapping_count);
    return checks;
  }
  edge_map = std::move(unique_edge_map);

  std::vector<std::vector<int>> new_face_edges;
  for (int index = 1; index <= new_faces.Extent(); ++index) {
    new_face_edges.push_back(face_edge_indices(TopoDS::Face(new_faces.FindKey(index)), new_edges));
  }
  std::set<int> available_faces;
  for (int index = 0; index < new_faces.Extent(); ++index) {
    available_faces.insert(index);
  }
  for (int old_index = 0; old_index < old_faces.Extent(); ++old_index) {
    std::vector<int> expected;
    for (const int edge : old_face_edges[old_index]) {
      expected.push_back(edge_map[edge]);
    }
    std::sort(expected.begin(), expected.end());
    std::vector<int> matches;
    for (const int candidate : available_faces) {
      std::vector<int> actual = new_face_edges[candidate];
      std::sort(actual.begin(), actual.end());
      if (actual == expected) {
        matches.push_back(candidate);
      }
    }
    if (matches.size() != 1) {
      checks.failure_stage = 4;
      checks.failure_detail = static_cast<std::uint32_t>(old_index + 1) |
          (static_cast<std::uint32_t>(matches.size()) << 16);
      return checks;
    }
    available_faces.erase(matches.front());
    if (changed_faces.count(old_index) == 0) {
      const TopoDS_Shape& old_face = old_faces.FindKey(old_index + 1);
      const TopoDS_Shape& new_face = new_faces.FindKey(matches.front() + 1);
      if (!old_face.IsSame(new_face)) {
        double error = 0.0;
        if (!translated_match(old_face, new_face, 1,
                              {0.0, 0.0, 0.0}, tolerance, error)) {
          checks.failure_stage = 5;
          checks.failure_detail = static_cast<std::uint32_t>(old_index + 1);
          return checks;
        }
      }
    }
  }
  checks.incidence = edge_mapping_count == 1 && available_faces.empty();
  checks.unchanged_geometry = checks.incidence;
  return checks;
}

struct CylinderCapPlan {
  int cap_face = -1;
  int lateral_face = -1;
  FaceOption option;
};

std::optional<CylinderCapPlan> analytic_cylinder_cap_plan(
    const ShapeMap& faces,
    const ShapeMap& edges,
    const std::vector<std::vector<int>>& face_edges,
    const int selected_face,
    const Point& vector,
    const Tolerances& tolerances) {
  if (faces.Extent() != 3 || edges.Extent() != 3 || selected_face < 0 ||
      selected_face >= faces.Extent() || face_edges[selected_face].size() != 1) {
    return std::nullopt;
  }
  const TopoDS_Face cap = TopoDS::Face(faces.FindKey(selected_face + 1));
  if (BRepAdaptor_Surface(cap, true).GetType() != GeomAbs_Plane) {
    return std::nullopt;
  }
  const int selected_rim = face_edges[selected_face].front();
  BRepAdaptor_Curve rim(TopoDS::Edge(edges.FindKey(selected_rim + 1)));
  if (rim.GetType() != GeomAbs_Circle) {
    return std::nullopt;
  }

  int lateral_index = -1;
  int plane_count = 0;
  for (int index = 0; index < faces.Extent(); ++index) {
    const GeomAbs_SurfaceType type =
        BRepAdaptor_Surface(TopoDS::Face(faces.FindKey(index + 1)), true).GetType();
    if (type == GeomAbs_Plane) {
      ++plane_count;
    } else if (type == GeomAbs_Cylinder && lateral_index < 0) {
      lateral_index = index;
    } else {
      return std::nullopt;
    }
  }
  if (plane_count != 2 || lateral_index < 0 || face_edges[lateral_index].size() != 3 ||
      std::find(face_edges[lateral_index].begin(), face_edges[lateral_index].end(),
                selected_rim) == face_edges[lateral_index].end()) {
    return std::nullopt;
  }

  const TopoDS_Face lateral = TopoDS::Face(faces.FindKey(lateral_index + 1));
  int circle_count = 0;
  int closed_line_count = 0;
  for (const int edge_index : face_edges[lateral_index]) {
    const TopoDS_Edge edge = TopoDS::Edge(edges.FindKey(edge_index + 1));
    const GeomAbs_CurveType type = BRepAdaptor_Curve(edge).GetType();
    if (type == GeomAbs_Circle) {
      ++circle_count;
    } else if (type == GeomAbs_Line && BRep_Tool::IsClosed(edge, lateral)) {
      ++closed_line_count;
    } else {
      return std::nullopt;
    }
  }
  if (circle_count != 2 || closed_line_count != 1) {
    return std::nullopt;
  }

  BRepAdaptor_Surface surface(lateral, true);
  const gp_Cylinder cylinder = surface.Cylinder();
  const gp_Dir axis = cylinder.Axis().Direction();
  const gp_Vec axis_vector(axis);
  const gp_Vec translation(vector[0], vector[1], vector[2]);
  const double axial = translation.Dot(axis_vector);
  const gp_Vec residual = translation - axial * axis_vector;
  if (residual.Magnitude() > tolerances.compare) {
    return std::nullopt;
  }

  const gp_Circ circle = rim.Circle();
  if (std::abs(circle.Radius() - cylinder.Radius()) > tolerances.compare ||
      std::abs(std::abs(circle.Axis().Direction().Dot(axis)) - 1.0) > 1.0e-10) {
    return std::nullopt;
  }
  const double selected_v = gp_Vec(cylinder.Location(), circle.Location()).Dot(axis_vector);
  const double first_v = surface.FirstVParameter();
  const double last_v = surface.LastVParameter();
  const bool at_first = std::abs(selected_v - first_v) <= tolerances.compare;
  const bool at_last = std::abs(selected_v - last_v) <= tolerances.compare;
  if (at_first == at_last) {
    return std::nullopt;
  }
  const double new_first = at_first ? first_v + axial : first_v;
  const double new_last = at_last ? last_v + axial : last_v;
  if (new_last - new_first <= tolerances.build) {
    refuse(kReasonContainment);
  }

  BRepBuilderAPI_MakeFace maker(cylinder, surface.FirstUParameter(),
                                surface.LastUParameter(), new_first, new_last);
  if (!maker.IsDone()) {
    refuse(kReasonNoCandidate);
  }
  TopoDS_Face rebuilt = maker.Face();
  rebuilt.Orientation(lateral.Orientation());
  FaceOption option;
  option.face = rebuilt;
  option.reconstructed = true;
  return CylinderCapPlan{selected_face, lateral_index, std::move(option)};
}

std::optional<CylinderCapPlan> analytic_cylinder_rim_plan(
    const ShapeMap& faces,
    const ShapeMap& edges,
    const std::vector<std::vector<int>>& face_edges,
    const int selected_edge,
    const Point& vector,
    const Tolerances& tolerances) {
  if (selected_edge < 0 || selected_edge >= edges.Extent() ||
      BRepAdaptor_Curve(TopoDS::Edge(edges.FindKey(selected_edge + 1))).GetType() !=
          GeomAbs_Circle) {
    return std::nullopt;
  }
  int cap_face = -1;
  for (int index = 0; index < faces.Extent(); ++index) {
    if (face_edges[index].size() == 1 && face_edges[index].front() == selected_edge &&
        BRepAdaptor_Surface(TopoDS::Face(faces.FindKey(index + 1)), true).GetType() ==
            GeomAbs_Plane) {
      if (cap_face >= 0) {
        return std::nullopt;
      }
      cap_face = index;
    }
  }
  return cap_face < 0 ? std::nullopt : analytic_cylinder_cap_plan(
      faces, edges, face_edges, cap_face, vector, tolerances);
}

void certificate_from_inspection(const AdapterInspection& inspection,
                                 AdapterMoveCertificate& certificate) {
  certificate.valid = inspection.valid;
  certificate.airtight = inspection.airtight;
  certificate.face_count = inspection.face_count;
  certificate.edge_count = inspection.edge_count;
  certificate.vertex_count = inspection.vertex_count;
  certificate.volume = inspection.volume;
  certificate.plane_faces = inspection.plane_faces;
  certificate.cylinder_faces = inspection.cylinder_faces;
  certificate.cone_faces = inspection.cone_faces;
  certificate.sphere_faces = inspection.sphere_faces;
  certificate.torus_faces = inspection.torus_faces;
  certificate.bezier_faces = inspection.bezier_faces;
  certificate.bspline_faces = inspection.bspline_faces;
  certificate.revolution_faces = inspection.revolution_faces;
  certificate.extrusion_faces = inspection.extrusion_faces;
  certificate.offset_faces = inspection.offset_faces;
  certificate.other_faces = inspection.other_faces;
}

TopoDS_Shape move_component(const MoveSource& source,
                            const std::uint32_t kind,
                            const std::uint64_t one_based_index,
                            const Point& vector,
                            const AdapterMovePolicy& policy,
                            const std::uint32_t validation_mode,
                            const std::set<int>* const planned_selected_edges,
                            const std::set<int>* const planned_moved,
                            const std::set<int>* const planned_changed_edges,
                            const std::set<int>* const planned_changed_faces,
                            std::vector<int>* const face_strategies,
                            std::vector<int>* const certified_merges,
                            AdapterMoveCertificate& certificate,
                            const bool prefer_deformed_chain = true) {
  const TopoDS_Shape& shape = source.shape;
  const Tolerances& tolerances = source.tolerances;
  const ShapeMap& faces = source.faces;
  const ShapeMap& edges = source.edges;
  const ShapeMap& vertices = source.vertices;
  const std::vector<std::vector<int>>& edge_vertices = source.edge_vertices;
  const std::vector<std::vector<int>>& face_edges = source.face_edges;
  certificate.comparison_tolerance = tolerances.compare;
  certificate.build_tolerance = tolerances.build;
  certificate.sewing_tolerance = tolerances.sew;

  const ShapeMap* selected_map = kind == 1 ? &faces : kind == 2 ? &edges : &vertices;
  if (one_based_index == 0 || one_based_index > static_cast<std::uint64_t>(selected_map->Extent())) {
    refuse(kReasonIndexOutside);
  }
  const int selected_index = static_cast<int>(one_based_index - 1);
  const TopoDS_Shape selected = selected_map->FindKey(selected_index + 1);
  if (kind == 2 && BRep_Tool::Degenerated(TopoDS::Edge(selected))) {
    refuse(kReasonDegenerate);
  }

  std::set<int> selected_edges;
  std::set<int> moved;
  if (planned_selected_edges != nullptr && planned_moved != nullptr) {
    selected_edges = *planned_selected_edges;
    moved = *planned_moved;
  } else if (kind == 1) {
    selected_edges.insert(face_edges[selected_index].begin(), face_edges[selected_index].end());
    for (const int edge : selected_edges) {
      moved.insert(edge_vertices[edge].begin(), edge_vertices[edge].end());
    }
  } else if (kind == 2) {
    selected_edges.insert(selected_index);
    moved.insert(edge_vertices[selected_index].begin(), edge_vertices[selected_index].end());
  } else {
    moved.insert(selected_index);
  }
  if (moved.empty()) {
    refuse(kReasonTopology);
  }

  std::set<int> changed_edges;
  if (planned_changed_edges != nullptr) {
    changed_edges = *planned_changed_edges;
  } else {
    for (int index = 0; index < static_cast<int>(edge_vertices.size()); ++index) {
      if (std::any_of(edge_vertices[index].begin(), edge_vertices[index].end(),
                      [&](const int vertex) { return moved.count(vertex) != 0; })) {
        changed_edges.insert(index);
      }
    }
  }
  std::vector<std::optional<TopoDS_Edge>> replacements(edges.Extent());
  for (const int edge_index : changed_edges) {
    const TopoDS_Edge edge = TopoDS::Edge(edges.FindKey(edge_index + 1));
    if (BRep_Tool::Degenerated(edge)) {
      refuse(kReasonDegenerate);
    }
    auto rebuilt = rebuild_edge(edge, edge_vertices[edge_index], vertices, moved, vector,
                                tolerances.build);
    if (rebuilt.second != (selected_edges.count(edge_index) != 0)) {
      refuse(kReasonContainment);
    }
    replacements[edge_index] = rebuilt.first;
  }

  std::set<int> changed_faces;
  if (planned_changed_faces != nullptr) {
    changed_faces = *planned_changed_faces;
  } else {
    for (int index = 0; index < static_cast<int>(face_edges.size()); ++index) {
      if (std::any_of(face_edges[index].begin(), face_edges[index].end(),
                      [&](const int edge) { return changed_edges.count(edge) != 0; })) {
        changed_faces.insert(index);
      }
    }
  }

  const std::optional<CylinderCapPlan> cylinder_cap = kind == 1
      ? analytic_cylinder_cap_plan(faces, edges, face_edges, selected_index, vector, tolerances)
      : kind == 2
          ? analytic_cylinder_rim_plan(
                faces, edges, face_edges, selected_index, vector, tolerances)
          : std::nullopt;

  std::vector<std::vector<FaceOption>> face_options;
  std::uint64_t candidate_count = 1;
  bool preferred_filter_used = false;
  for (int face_index = 0; face_index < faces.Extent(); ++face_index) {
    const TopoDS_Face face = TopoDS::Face(faces.FindKey(face_index + 1));
    std::vector<FaceOption> options;
    if ((kind == 1 && face_index == selected_index) ||
        (cylinder_cap.has_value() && face_index == cylinder_cap->cap_face)) {
      const TopoDS_Shape moved_face = BRepBuilderAPI_Transform(
          face, translation_transform(vector), true).Shape();
      options.push_back(FaceOption{TopoDS::Face(moved_face)});
    } else if (cylinder_cap.has_value() &&
               face_index == cylinder_cap->lateral_face) {
      options.push_back(cylinder_cap->option);
    } else if (changed_faces.count(face_index) != 0) {
      const std::vector<TopoDS_Edge> boundary = boundary_edges(
          face, edges, replacements, vertices, moved, vector, tolerances.build,
          validation_mode == 1);

      const int cached_strategy = face_strategies == nullptr
          ? -1
          : (*face_strategies)[face_index];
      options = rebuild_face_options(
          face, boundary, tolerances, policy, cached_strategy,
          prefer_deformed_chain, preferred_filter_used);
      if (face_strategies != nullptr && cached_strategy < 0) {
        (*face_strategies)[face_index] = !options.front().reconstructed_bspline
            ? 1
            : boundary.size() >= 5 ? 3 : 2;
      }
      if (boundary.size() >= 5 && certified_merges != nullptr &&
          (*certified_merges)[face_index] >= 0) {
        const int required_choice = (*certified_merges)[face_index];
        options.erase(
            std::remove_if(options.begin(), options.end(),
                           [&](const FaceOption& option) {
                             return option.merge_choice != required_choice;
                           }),
            options.end());
        if (options.empty()) {
          refuse(kReasonNoCandidate);
        }
      }
    } else {
      if (validation_mode == 1) {
        BRepBuilderAPI_Copy copy(face, false, false);
        if (!copy.IsDone()) {
          refuse(kReasonNoCandidate);
        }
        options.push_back(FaceOption{TopoDS::Face(copy.Shape())});
      } else {
        options.push_back(FaceOption{face});
      }
    }
    candidate_count *= options.size();
    if (candidate_count > policy.max_candidates) {
      refuse(kReasonAmbiguous);
    }
    face_options.push_back(std::move(options));
  }

  struct Accepted {
    TopoDS_Shape result;
    std::array<double, 5> rank{};
    double max_boundary = 0.0;
    double max_jump = 0.0;
    double selected_error = 0.0;
    std::uint32_t reconstructed_count = 0;
    std::uint32_t reconstructed_bspline_count = 0;
    std::uint32_t internal_knot_count = 0;
    std::vector<int> merge_choices;
  };
  std::vector<Accepted> accepted;

  double observed_boundary = 0.0;
  double observed_jump = 0.0;
  std::uint32_t observed_reconstructed_count = 0;
  std::uint32_t observed_reconstructed_bspline_count = 0;
  std::uint32_t observed_internal_knot_count = 0;

  for (std::uint64_t combination = 0; combination < candidate_count; ++combination) {
    std::uint64_t cursor = combination;
    std::vector<const FaceOption*> choices;
    for (const auto& options : face_options) {
      choices.push_back(&options[cursor % options.size()]);
      cursor /= options.size();
    }
    bool boundary_exact = true;

    double max_boundary = 0.0;
    double max_jump = 0.0;

    std::uint32_t reconstructed_count = 0;
    std::uint32_t reconstructed_bspline_count = 0;
    std::uint32_t internal_knot_count = 0;
    for (const FaceOption* choice : choices) {
      if (choice->reconstructed) {
        ++reconstructed_count;
      }
      if (choice->reconstructed_bspline) {
        ++reconstructed_bspline_count;
        max_boundary = std::max(max_boundary, choice->boundary_error);
        boundary_exact = boundary_exact && choice->boundary_error <= tolerances.compare;
      }
      if (choice->internal_knot_checked) {
        ++internal_knot_count;
        max_jump = std::max(max_jump, choice->normal_jump);

      }
    }
    observed_boundary = std::max(observed_boundary, max_boundary);
    observed_jump = std::max(observed_jump, max_jump);
    observed_reconstructed_count = std::max(observed_reconstructed_count, reconstructed_count);
    observed_reconstructed_bspline_count =
        std::max(observed_reconstructed_bspline_count, reconstructed_bspline_count);
    observed_internal_knot_count = std::max(observed_internal_knot_count, internal_knot_count);

    TopoDS_Shape result;
    bool self_intersection_free = false;
    if (source.requires_airtight) {
      BRepBuilderAPI_Sewing sewing(tolerances.sew);
      for (const FaceOption* choice : choices) {
        sewing.Add(choice->face);
      }
      sewing.Perform();
      if (sewing.NbFreeEdges() != 0 || sewing.NbMultipleEdges() != 0) {
        continue;
      }
      std::vector<TopoDS_Shell> shells;
      for (TopExp_Explorer explorer(sewing.SewedShape(), TopAbs_SHELL);
           explorer.More(); explorer.Next()) {
        shells.push_back(TopoDS::Shell(explorer.Current()));
      }
      if (shells.size() != 1) {
        continue;
      }
      const TopoDS_Solid solid = BRepBuilderAPI_MakeSolid(shells[0]).Solid();
      result = solid;
      if (validation_mode == 2) {
        ShapeFix_Solid fixer(solid);
        fixer.Perform();
        result = fixer.Solid();
      }
      if (shape_volume(result) < 0.0) {
        result = result.Reversed();
      }
      const bool tolerant_face = std::any_of(
          choices.begin(), choices.end(),
          [](const FaceOption* choice) { return choice->merge_choice == kHeightFieldChoice; });
      if (tolerant_face) {
        raise_tolerances_to_true_gaps(result);
      }
      std::future<bool> airtight_future = std::async(
          std::launch::async, [&result]() { return is_airtight(result); });
      self_intersection_free = BRepAlgoAPI_Check(result).IsValid();
      if (!airtight_future.get() || !self_intersection_free) {
        continue;
      }
    } else {
      if (choices.size() != 1) {
        continue;
      }
      result = choices.front()->face;
      self_intersection_free = BRepAlgoAPI_Check(result).IsValid();
      if (!BRepCheck_Analyzer(result, true).IsValid() || !self_intersection_free) {
        continue;
      }
    }
    ShapeMap result_faces;
    ShapeMap result_edges;
    ShapeMap result_vertices;
    map_shapes(result, TopAbs_FACE, result_faces);
    map_shapes(result, TopAbs_EDGE, result_edges);
    map_shapes(result, TopAbs_VERTEX, result_vertices);
    if (result_faces.Extent() != faces.Extent() || result_edges.Extent() != edges.Extent() ||
        result_vertices.Extent() != vertices.Extent()) {
      continue;
    }

    if (!boundary_exact) {
      continue;
    }
    if (validation_mode == 2) {
      const MappingChecks mapping = verify_mapped_topology(
          faces, edges, vertices, result_faces, result_edges, result_vertices,
          edge_vertices, face_edges, replacements, changed_edges, changed_faces,
          moved, vector, tolerances.compare);
      certificate.moved_vertex_mapping_verified =
          std::max(certificate.moved_vertex_mapping_verified,
                   static_cast<std::uint32_t>(mapping.moved_vertices));
      certificate.mapped_incidence_preserved =
          std::max(certificate.mapped_incidence_preserved,
                   static_cast<std::uint32_t>(mapping.incidence));
      certificate.unchanged_geometry_preserved =
          std::max(certificate.unchanged_geometry_preserved,
                   static_cast<std::uint32_t>(mapping.unchanged_geometry));
      certificate.verification_failure_stage = mapping.failure_stage;
      certificate.verification_failure_detail = mapping.failure_detail;
      if (!mapping.moved_vertices || !mapping.incidence || !mapping.unchanged_geometry) {
        continue;
      }
    }

    int match_count = 0;
    double selected_error = 0.0;
    if (kind == 3) {
      const Point target = translated(point_of(TopoDS::Vertex(selected)), vector);
      for (int index = 1; index <= result_vertices.Extent(); ++index) {
        const double error = point_distance(target, point_of(TopoDS::Vertex(result_vertices.FindKey(index))));
        if (error <= tolerances.compare) {
          ++match_count;
          selected_error = std::max(selected_error, error);
        }
      }
    } else {
      const ShapeMap& result_parts = kind == 1 ? result_faces : result_edges;
      for (int index = 1; index <= result_parts.Extent(); ++index) {
        double error = 0.0;
        if (translated_match(selected, result_parts.FindKey(index), kind, vector,
                             tolerances.compare, error)) {
          ++match_count;
          selected_error = std::max(selected_error, error);
        }
      }
    }
    if (match_count != 1) {
      continue;
    }

    Accepted candidate;
    candidate.result = result;
    candidate.max_boundary = max_boundary;
    candidate.max_jump = max_jump;
    candidate.selected_error = selected_error;
    candidate.reconstructed_count = reconstructed_count;
    candidate.reconstructed_bspline_count = reconstructed_bspline_count;
    candidate.internal_knot_count = internal_knot_count;
    candidate.merge_choices.assign(faces.Extent(), -1);
    for (int face_index = 0; face_index < static_cast<int>(choices.size()); ++face_index) {
      candidate.merge_choices[face_index] = choices[face_index]->merge_choice;
    }
    for (const FaceOption* choice : choices) {
      for (int rank_index = 0; rank_index < 5; ++rank_index) {
        candidate.rank[rank_index] += choice->rank[rank_index];
      }
    }
    accepted.push_back(candidate);
  }

  if (accepted.empty() && preferred_filter_used) {
    return move_component(
        source, kind, one_based_index, vector, policy, validation_mode,
        planned_selected_edges, planned_moved, planned_changed_edges,
        planned_changed_faces, face_strategies, certified_merges, certificate, false);
  }
  if (accepted.empty()) {
    certificate.reconstructed_face_count = observed_reconstructed_count;
    certificate.reconstructed_bspline_face_count = observed_reconstructed_bspline_count;
    certificate.internal_knot_face_count = observed_internal_knot_count;
    certificate.max_boundary_error = observed_boundary;
    certificate.max_internal_normal_jump = observed_jump;
    if (observed_reconstructed_count != 0) {
      certificate.quality_scope_bits |= kQualityReconstructedFaces;
      certificate.quality_checked_bits |= kQualityReconstructedFaces;
    }
    if (observed_reconstructed_bspline_count != 0) {
      certificate.quality_scope_bits |= kQualityBsplineBoundary;
      certificate.quality_checked_bits |= kQualityBsplineBoundary;
    }
    if (observed_internal_knot_count != 0) {
      certificate.quality_scope_bits |= kQualityInternalKnot;
      certificate.quality_checked_bits |= kQualityInternalKnot;
    }

    refuse(kReasonNoCandidate);
  }
  const bool has_smooth_candidate = std::any_of(
      accepted.begin(), accepted.end(), [&](const Accepted& candidate) {
        return candidate.max_jump <= policy.max_normal_jump;
      });
  if (has_smooth_candidate) {
    accepted.erase(
        std::remove_if(accepted.begin(), accepted.end(), [&](const Accepted& candidate) {
          return candidate.max_jump > policy.max_normal_jump;
        }),
        accepted.end());
  }
  std::sort(accepted.begin(), accepted.end(), [](const Accepted& first, const Accepted& second) {
    return first.rank < second.rank;
  });
  if (accepted.size() > 1 &&
      std::equal(accepted[0].rank.begin(), accepted[0].rank.begin() + 4, accepted[1].rank.begin()) &&
      std::abs(accepted[0].rank[4] - accepted[1].rank[4]) <= tolerances.compare) {
    refuse(kReasonAmbiguous);
  }

  const Accepted& best = accepted.front();
  certificate.topology_counts_preserved = 1;
  if (validation_mode == 2) {
    certificate.moved_vertex_mapping_verified = 1;
    certificate.mapped_incidence_preserved = 1;
    certificate.unchanged_geometry_preserved = 1;
  }
  certificate.selected_match_count = 1;
  certificate.self_intersection_free = 1;
  certificate.reconstructed_face_count = best.reconstructed_count;
  certificate.reconstructed_bspline_face_count = best.reconstructed_bspline_count;
  certificate.internal_knot_face_count = best.internal_knot_count;
  certificate.quality_scope_bits = kQualityReconstructedFaces;
  certificate.quality_checked_bits = certificate.quality_scope_bits;
  if (validation_mode == 2) {
    certificate.quality_scope_bits |= kQualityMovedVertexMapping |
        kQualityMappedIncidence | kQualityUnchangedGeometry;
    certificate.quality_checked_bits = certificate.quality_scope_bits;
  }
  if (best.reconstructed_bspline_count != 0) {
    certificate.quality_scope_bits |= kQualityBsplineBoundary;
    certificate.quality_checked_bits |= kQualityBsplineBoundary;
  }
  if (best.internal_knot_count != 0) {
    certificate.quality_scope_bits |= kQualityInternalKnot;
    certificate.quality_checked_bits |= kQualityInternalKnot;
  }
  certificate.selected_translation_error = best.selected_error;
  certificate.max_boundary_error = best.max_boundary;
  certificate.max_internal_normal_jump = best.max_jump;
  if (certified_merges != nullptr) {
    *certified_merges = best.merge_choices;
  }
  certificate.verification_failure_stage = 0;
  certificate.verification_failure_detail = 0;
  return best.result;
}

std::tuple<TopoDS_Shape, std::uint64_t, std::uint64_t,
           std::uint64_t, std::uint64_t> make_cylinder_fixture() {
  const gp_Pnt origin(3.0, -2.0, 1.0);
  const gp_Dir direction(1.0, 2.0, 3.0);
  const TopoDS_Shape shape =
      BRepPrimAPI_MakeCylinder(gp_Ax2(origin, direction), 2.0, 6.0).Shape();
  if (!is_airtight(shape)) {
    throw std::runtime_error("cylinder fixture is not airtight");
  }
  ShapeMap faces;
  ShapeMap edges;
  map_shapes(shape, TopAbs_FACE, faces);
  map_shapes(shape, TopAbs_EDGE, edges);
  std::uint64_t lower = 0;
  std::uint64_t upper = 0;
  double minimum = std::numeric_limits<double>::infinity();
  double maximum = -std::numeric_limits<double>::infinity();
  for (int index = 1; index <= faces.Extent(); ++index) {
    const TopoDS_Face face = TopoDS::Face(faces.FindKey(index));
    if (BRepAdaptor_Surface(face, true).GetType() != GeomAbs_Plane) {
      continue;
    }
    GProp_GProps properties;
    BRepGProp::SurfaceProperties(face, properties, 1.0e-10);
    const double along = gp_Vec(origin, properties.CentreOfMass()).Dot(gp_Vec(direction));
    if (along < minimum) {
      minimum = along;
      lower = static_cast<std::uint64_t>(index);
    }
    if (along > maximum) {
      maximum = along;
      upper = static_cast<std::uint64_t>(index);
    }
  }
  if (lower == 0 || upper == 0 || lower == upper) {
    throw std::runtime_error("cylinder fixture cap faces are unavailable");
  }
  const std::vector<int> lower_edges = face_edge_indices(
      TopoDS::Face(faces.FindKey(static_cast<int>(lower))), edges);
  const std::vector<int> upper_edges = face_edge_indices(
      TopoDS::Face(faces.FindKey(static_cast<int>(upper))), edges);
  if (lower_edges.size() != 1 || upper_edges.size() != 1) {
    throw std::runtime_error("cylinder fixture rim edges are unavailable");
  }
  return {shape, lower, upper,
          static_cast<std::uint64_t>(lower_edges.front() + 1),
          static_cast<std::uint64_t>(upper_edges.front() + 1)};
}

std::pair<TopoDS_Shape, std::uint64_t> make_surface_family_fixture(
    const std::uint32_t family) {
  const TopoDS_Shape box = BRepPrimAPI_MakeBox(20.0, 20.0, 4.0).Shape();
  const gp_Ax2 axis(gp_Pnt(10.0, 10.0, 4.0), gp_Dir(0.0, 0.0, 1.0));
  TopoDS_Shape feature;
  if (family == 1) {
    feature = BRepPrimAPI_MakeCone(axis, 3.0, 1.0, 5.0).Shape();
  } else if (family == 2) {
    feature = BRepPrimAPI_MakeTorus(axis, 4.0, 1.0).Shape();
  } else {
    throw std::invalid_argument("unknown surface fixture family");
  }
  BRepAlgoAPI_Fuse fuse(box, feature);
  fuse.Build();
  if (!fuse.IsDone() || !is_airtight(fuse.Shape())) {
    throw std::runtime_error("surface-family fixture fuse failed");
  }
  const TopoDS_Shape result = fuse.Shape();
  ShapeMap faces;
  map_shapes(result, TopAbs_FACE, faces);
  std::uint64_t selected = 0;
  double lowest_z = std::numeric_limits<double>::infinity();
  for (int index = 1; index <= faces.Extent(); ++index) {
    const TopoDS_Face face = TopoDS::Face(faces.FindKey(index));
    if (BRepAdaptor_Surface(face).GetType() != GeomAbs_Plane) {
      continue;
    }
    GProp_GProps properties;
    BRepGProp::SurfaceProperties(face, properties, 1.0e-10);
    if (properties.CentreOfMass().Z() < lowest_z) {
      lowest_z = properties.CentreOfMass().Z();
      selected = static_cast<std::uint64_t>(index);
    }
  }
  if (selected == 0) {
    throw std::runtime_error("surface-family fixture has no planar move face");
  }
  return {result, selected};
}

TopoDS_Shape make_open_face_fixture() {
  const TopoDS_Shape box = BRepPrimAPI_MakeBox(8.0, 6.0, 4.0).Shape();
  ShapeMap faces;
  map_shapes(box, TopAbs_FACE, faces);
  if (faces.IsEmpty()) {
    throw std::runtime_error("open-face fixture has no face");
  }
  const TopoDS_Face face = TopoDS::Face(faces.FindKey(1));
  if (!BRepCheck_Analyzer(face, true).IsValid()) {
    throw std::runtime_error("open-face fixture is invalid");
  }
  return face;
}

TopoDS_Shape make_fixture() {
  TopoDS_Shape shape = BRepPrimAPI_MakeBox(20.0, 10.0, 33.8393).Shape();
  for (const int edge_index : {4, 13}) {
    ShapeMap edges;
    map_shapes(shape, TopAbs_EDGE, edges);
    if (edge_index > edges.Extent()) {
      throw std::runtime_error("fixture edge index unavailable");
    }
    BRepFilletAPI_MakeFillet fillet(shape);
    fillet.Add(1.0, TopoDS::Edge(edges.FindKey(edge_index)));
    fillet.Build();
    if (!fillet.IsDone()) {
      throw std::runtime_error("fixture fillet failed");
    }
    shape = fillet.Shape();
  }
  if (!is_airtight(shape)) {
    throw std::runtime_error("fixture is not airtight");
  }
  return shape;
}

struct LocalMoveSession {
  AdapterMovePolicy policy;
  MoveSource source;
  MoveSource live_source;
  bool result_cached = false;
  std::uint32_t cached_kind = 0;
  std::uint64_t cached_index = 0;
  std::uint32_t cached_mode = 0;
  Point cached_vector{};
  std::string cached_bytes;
  AdapterMoveCertificate cached_certificate{};
  bool direction_plan_cached = false;
  std::uint32_t plan_kind = 0;
  std::uint64_t plan_index = 0;
  std::uint32_t plan_active_axes = 0;
  Point plan_direction{};
  std::set<int> plan_selected_edges;
  std::set<int> plan_moved_vertices;
  std::set<int> plan_changed_edges;
  std::set<int> plan_changed_faces;
  std::vector<int> face_strategies;
  std::vector<int> certified_merge_choices;
};

}  // namespace

extern "C" std::int32_t narwhal_occt_adapter_make_box(
    const double dx, const double dy, const double dz, std::uint8_t* const output,
    const std::size_t capacity, std::size_t* const required) noexcept {
  if (required == nullptr || (capacity != 0 && output == nullptr)) {
    return kInvalidArgument;
  }
  *required = 0;
  try {
    const TopoDS_Shape shape = BRepPrimAPI_MakeBox(dx, dy, dz).Shape();
    if (shape.IsNull()) {
      return kKernelFailure;
    }
    return copy_serialized(serialize_shape(shape), output, capacity, required);
  } catch (const Standard_Failure&) {
    return kKernelFailure;
  } catch (const std::exception&) {
    return kUnexpectedFailure;
  } catch (...) {
    return kUnexpectedFailure;
  }
}

extern "C" std::int32_t narwhal_occt_adapter_inspect_brep(
    const std::uint8_t* const input, const std::size_t input_length,
    const std::uint64_t max_faces, const std::uint64_t max_edges,
    const std::uint64_t max_vertices, AdapterInspection* const output) noexcept {
  if (input == nullptr || input_length == 0 || output == nullptr) {
    return kInvalidArgument;
  }
  try {
    const TopoDS_Shape shape = deserialize_shape(input, input_length);
    if (!within_resource_caps(shape, max_faces, max_edges, max_vertices)) {
      return kLimitExceeded;
    }
    *output = inspect_shape(shape);
    return kOk;
  } catch (const Standard_Failure&) {
    return kMalformedBrep;
  } catch (const std::invalid_argument&) {
    return kMalformedBrep;
  } catch (const std::exception&) {
    return kUnexpectedFailure;
  } catch (...) {
    return kUnexpectedFailure;
  }
}

extern "C" std::int32_t narwhal_occt_adapter_session_import(
    const std::uint8_t* const input, const std::size_t input_length,
    const AdapterMovePolicy* const policy, void** const session) noexcept {
  if (input == nullptr || input_length == 0 || policy == nullptr || session == nullptr) {
    return kInvalidArgument;
  }
  *session = nullptr;
  try {
    const TopoDS_Shape shape = deserialize_shape(input, input_length);
    MoveSource live = prepare_move_source(shape, *policy);
    MoveSource commit_template = isolated_move_source(live);
    *session = new LocalMoveSession{
        *policy, std::move(commit_template), std::move(live)};
    g_live_sessions.fetch_add(1, std::memory_order_relaxed);
    return kOk;
  } catch (const Refusal& refusal) {
    return refusal.status;
  } catch (const Standard_Failure&) {
    return kMalformedBrep;
  } catch (const std::invalid_argument&) {
    return kMalformedBrep;
  } catch (const std::exception&) {
    return kUnexpectedFailure;
  } catch (...) {
    return kUnexpectedFailure;
  }
}

extern "C" std::int32_t narwhal_occt_adapter_session_move(
    void* const session, const std::uint32_t validation_mode,
    const std::uint32_t component_kind, const std::uint64_t component_index,
    const double dx, const double dy,
    const double dz, std::uint8_t* const output, const std::size_t capacity,
    std::size_t* const required, AdapterMoveCertificate* const certificate) noexcept {
  if (session == nullptr || required == nullptr || certificate == nullptr ||
      (capacity != 0 && output == nullptr)) {
    return kInvalidArgument;
  }
  *required = 0;
  *certificate = AdapterMoveCertificate{};
  certificate->struct_size = sizeof(AdapterMoveCertificate);
  certificate->abi_version = 3;
  certificate->component_kind = component_kind;
  try {
    LocalMoveSession& cached = *static_cast<LocalMoveSession*>(session);
    const Point vector{dx, dy, dz};
    if (cached.result_cached && cached.cached_mode == validation_mode &&
        cached.cached_kind == component_kind && cached.cached_index == component_index &&
        cached.cached_vector == vector) {
      *certificate = cached.cached_certificate;
      return copy_serialized(cached.cached_bytes, output, capacity, required);
    }
    const double magnitude = std::sqrt(dx * dx + dy * dy + dz * dz);
    const Point normalized{dx / magnitude, dy / magnitude, dz / magnitude};
    std::uint32_t active_axes = 0;
    for (int axis = 0; axis < 3; ++axis) {
      if (std::abs(vector[axis]) > cached.policy.zero_translation) {
        active_axes |= 1U << axis;
      }
    }
    if (!cached.direction_plan_cached || cached.plan_kind != component_kind ||
        cached.plan_index != component_index || cached.plan_active_axes != active_axes ||
        cached.plan_direction != normalized) {
      cached.direction_plan_cached = true;
      cached.plan_kind = component_kind;
      cached.plan_index = component_index;
      cached.plan_active_axes = active_axes;
      cached.plan_direction = normalized;
      cached.plan_selected_edges.clear();
      cached.plan_moved_vertices.clear();
      cached.plan_changed_edges.clear();
      cached.plan_changed_faces.clear();
      const int selected_index = static_cast<int>(component_index - 1);
      if (component_kind == 1 && selected_index >= 0 &&
          selected_index < static_cast<int>(cached.source.face_edges.size())) {
        cached.plan_selected_edges.insert(
            cached.source.face_edges[selected_index].begin(),
            cached.source.face_edges[selected_index].end());
        for (const int edge : cached.plan_selected_edges) {
          cached.plan_moved_vertices.insert(
              cached.source.edge_vertices[edge].begin(),
              cached.source.edge_vertices[edge].end());
        }
      } else if (component_kind == 2 && selected_index >= 0 &&
                 selected_index < static_cast<int>(cached.source.edge_vertices.size())) {
        cached.plan_selected_edges.insert(selected_index);
        cached.plan_moved_vertices.insert(
            cached.source.edge_vertices[selected_index].begin(),
            cached.source.edge_vertices[selected_index].end());
      } else if (component_kind == 3 && selected_index >= 0 &&
                 selected_index < cached.source.vertices.Extent()) {
        cached.plan_moved_vertices.insert(selected_index);
      }
      for (int edge = 0; edge < static_cast<int>(cached.source.edge_vertices.size()); ++edge) {
        if (std::any_of(
                cached.source.edge_vertices[edge].begin(),
                cached.source.edge_vertices[edge].end(),
                [&](const int vertex) {
                  return cached.plan_moved_vertices.count(vertex) != 0;
                })) {
          cached.plan_changed_edges.insert(edge);
        }
      }
      for (int face = 0; face < static_cast<int>(cached.source.face_edges.size()); ++face) {
        if (std::any_of(
                cached.source.face_edges[face].begin(),
                cached.source.face_edges[face].end(),
                [&](const int edge) {
                  return cached.plan_changed_edges.count(edge) != 0;
                })) {
          cached.plan_changed_faces.insert(face);
        }
      }
      cached.face_strategies.assign(cached.source.faces.Extent(), -1);
      cached.certified_merge_choices.assign(cached.source.faces.Extent(), -1);
    }
    std::optional<MoveSource> commit_source;
    const MoveSource* working = nullptr;
    if (validation_mode == 2) {
      commit_source = isolated_move_source(cached.source);
      working = &*commit_source;
    } else {
      working = &cached.live_source;
    }
    const TopoDS_Shape result = move_component(
        *working, component_kind, component_index, vector,
        cached.policy, validation_mode, &cached.plan_selected_edges,
        &cached.plan_moved_vertices, &cached.plan_changed_edges,
        &cached.plan_changed_faces, &cached.face_strategies,
        &cached.certified_merge_choices, *certificate);
    certificate_from_inspection(
        working->requires_airtight ? inspect_certified_shape(result) : inspect_shape(result),
        *certificate);
    certificate->status = kOk;
    cached.cached_kind = component_kind;
    cached.cached_index = component_index;
    cached.cached_mode = validation_mode;
    cached.cached_vector = vector;
    cached.cached_bytes = serialize_shape(result);
    cached.cached_certificate = *certificate;
    cached.result_cached = true;
    return copy_serialized(cached.cached_bytes, output, capacity, required);
  } catch (const Refusal& refusal) {
    certificate->status = refusal.status;
    certificate->refusal_reason = refusal.reason;
    return refusal.status;
  } catch (const Standard_Failure&) {
    certificate->status = kKernelFailure;
    return kKernelFailure;
  } catch (const std::exception&) {
    certificate->status = kUnexpectedFailure;
    return kUnexpectedFailure;
  } catch (...) {
    certificate->status = kUnexpectedFailure;
    return kUnexpectedFailure;
  }
}

extern "C" std::size_t narwhal_occt_adapter_session_live_count() noexcept {
  return g_live_sessions.load(std::memory_order_relaxed);
}

extern "C" std::int32_t narwhal_occt_adapter_session_destroy(
    void* const session) noexcept {
  if (session == nullptr) {
    return kInvalidArgument;
  }
  try {
    delete static_cast<LocalMoveSession*>(session);
    g_live_sessions.fetch_sub(1, std::memory_order_relaxed);
    return kOk;
  } catch (const std::exception&) {
    return kUnexpectedFailure;
  } catch (...) {
    return kUnexpectedFailure;
  }
}

extern "C" std::int32_t narwhal_occt_adapter_move_component(
    const std::uint8_t* const input, const std::size_t input_length,
    const std::uint32_t component_kind, const std::uint64_t component_index,
    const double dx, const double dy, const double dz,
    const AdapterMovePolicy* const policy, std::uint8_t* const output,
    const std::size_t capacity, std::size_t* const required,
    AdapterMoveCertificate* const certificate) noexcept {
  if (input == nullptr || input_length == 0 || policy == nullptr || required == nullptr ||
      certificate == nullptr || (capacity != 0 && output == nullptr)) {
    return kInvalidArgument;
  }
  *required = 0;
  *certificate = AdapterMoveCertificate{};
  certificate->struct_size = sizeof(AdapterMoveCertificate);
  certificate->abi_version = 3;
  certificate->component_kind = component_kind;
  TopoDS_Shape source;
  try {
    source = deserialize_shape(input, input_length);
  } catch (const Standard_Failure&) {
    certificate->status = kMalformedBrep;
    return kMalformedBrep;
  } catch (const std::exception&) {
    certificate->status = kMalformedBrep;
    return kMalformedBrep;
  } catch (...) {
    certificate->status = kMalformedBrep;
    return kMalformedBrep;
  }
  try {
    const MoveSource prepared = prepare_move_source(source, *policy);
    const TopoDS_Shape result = move_component(
        prepared, component_kind, component_index, {dx, dy, dz}, *policy, 2,
        nullptr, nullptr, nullptr, nullptr, nullptr, nullptr, *certificate);
    const AdapterInspection inspection = inspect_shape(result);
    certificate_from_inspection(inspection, *certificate);
    certificate->status = kOk;
    return copy_serialized(serialize_shape(result), output, capacity, required);
  } catch (const Refusal& refusal) {
    certificate->status = refusal.status;
    certificate->refusal_reason = refusal.reason;
    return refusal.status;
  } catch (const Standard_Failure&) {
    certificate->status = kKernelFailure;
    return kKernelFailure;
  } catch (const std::invalid_argument&) {
    certificate->status = kMalformedBrep;
    return kMalformedBrep;
  } catch (const std::exception&) {
    certificate->status = kUnexpectedFailure;
    return kUnexpectedFailure;
  } catch (...) {
    certificate->status = kUnexpectedFailure;
    return kUnexpectedFailure;
  }
}

extern "C" std::int32_t narwhal_occt_adapter_make_cylinder_fixture(
    std::uint8_t* const output, const std::size_t capacity,
    std::size_t* const required, std::uint64_t* const lower_face,
    std::uint64_t* const upper_face, std::uint64_t* const lower_edge,
    std::uint64_t* const upper_edge) noexcept {
  if (required == nullptr || lower_face == nullptr || upper_face == nullptr ||
      lower_edge == nullptr || upper_edge == nullptr ||
      (capacity != 0 && output == nullptr)) {
    return kInvalidArgument;
  }
  *required = 0;
  *lower_face = 0;
  *upper_face = 0;
  *lower_edge = 0;
  *upper_edge = 0;
  try {
    auto [shape, lower, upper, lower_rim, upper_rim] = make_cylinder_fixture();
    *lower_face = lower;
    *upper_face = upper;
    *lower_edge = lower_rim;
    *upper_edge = upper_rim;
    return copy_serialized(serialize_shape(shape), output, capacity, required);
  } catch (const Standard_Failure&) {
    return kKernelFailure;
  } catch (const std::exception&) {
    return kUnexpectedFailure;
  } catch (...) {
    return kUnexpectedFailure;
  }
}

extern "C" std::int32_t narwhal_occt_adapter_make_surface_family_fixture(
    const std::uint32_t family, std::uint8_t* const output,
    const std::size_t capacity, std::size_t* const required,
    std::uint64_t* const selected_face) noexcept {
  if (required == nullptr || selected_face == nullptr ||
      (capacity != 0 && output == nullptr)) {
    return kInvalidArgument;
  }
  *required = 0;
  *selected_face = 0;
  try {
    const auto fixture = make_surface_family_fixture(family);
    *selected_face = fixture.second;
    return copy_serialized(serialize_shape(fixture.first), output, capacity, required);
  } catch (const Standard_Failure&) {
    return kKernelFailure;
  } catch (const std::exception&) {
    return kUnexpectedFailure;
  } catch (...) {
    return kUnexpectedFailure;
  }
}

extern "C" std::int32_t narwhal_occt_adapter_make_open_face_fixture(
    std::uint8_t* const output, const std::size_t capacity,
    std::size_t* const required) noexcept {
  if (required == nullptr || (capacity != 0 && output == nullptr)) {
    return kInvalidArgument;
  }
  *required = 0;
  try {
    return copy_serialized(serialize_shape(make_open_face_fixture()), output, capacity, required);
  } catch (const Standard_Failure&) {
    return kKernelFailure;
  } catch (...) {
    return kUnexpectedFailure;
  }
}

extern "C" std::int32_t narwhal_occt_adapter_make_local_move_fixture(
    std::uint8_t* const output, const std::size_t capacity,
    std::size_t* const required) noexcept {
  if (required == nullptr || (capacity != 0 && output == nullptr)) {
    return kInvalidArgument;
  }
  *required = 0;
  try {
    return copy_serialized(serialize_shape(make_fixture()), output, capacity, required);
  } catch (const Standard_Failure&) {
    return kKernelFailure;
  } catch (const std::exception&) {
    return kUnexpectedFailure;
  } catch (...) {
    return kUnexpectedFailure;
  }
}
