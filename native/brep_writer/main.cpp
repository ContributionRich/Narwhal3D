// SPDX-License-Identifier: GPL-3.0-or-later
//
// Narwhal exact BRep archive helper. This program accepts the public ONBREP 1
// text protocol and constructs an openNURBS ON_Brep without converting curved
// geometry to a polygonal representation.

#include "opennurbs.h"

#include <cerrno>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <iostream>
#include <limits>
#include <memory>
#include <new>
#include <string>

#if defined(_WIN32)
#include <fcntl.h>
#include <io.h>
#endif

namespace {

constexpr std::size_t kMaxVertices = 100000;
constexpr std::size_t kMaxEdges = 100000;
constexpr std::size_t kMaxFaces = 50000;
constexpr std::size_t kMaxLoopsPerFace = 4096;
constexpr std::size_t kMaxTotalLoops = 100000;
constexpr std::size_t kMaxTrimsPerLoop = 100000;
constexpr std::size_t kMaxTotalTrims = 100000;

constexpr std::size_t kMaxOrder = 64;
constexpr std::size_t kMaxCurveControlPoints = 262144;
constexpr std::size_t kMaxCurveKnots = 262206;
constexpr std::size_t kMaxSurfaceControlPointsPerDirection = 4096;
constexpr std::size_t kMaxSurfaceControlPoints = 262144;
constexpr std::size_t kMaxSurfaceKnotsPerDirection = 4158;
constexpr std::size_t kMaxNumericValues = 8000000;
constexpr std::size_t kMaxTokenLength = 128;
constexpr std::size_t kMaxArchiveBytes = 512 * 1024 * 1024;

bool checked_add(std::size_t left, std::size_t right, std::size_t& result) {
  if (right > std::numeric_limits<std::size_t>::max() - left) return false;
  result = left + right;
  return true;
}

bool checked_multiply(std::size_t left, std::size_t right, std::size_t& result) {
  if (left != 0 && right > std::numeric_limits<std::size_t>::max() / left) return false;
  result = left * right;
  return true;
}

class ProtocolReader {
 public:
  explicit ProtocolReader(std::istream& input) : input_(input) {}

  bool token(std::string& value, const char* description) {
    value.clear();
    input_.width(static_cast<std::streamsize>(kMaxTokenLength + 1));
    if (!(input_ >> value))
      return reject(std::string("truncated while reading ") + description);
    if (value.size() > kMaxTokenLength)
      return reject(std::string("token is too long for ") + description);
    return true;
  }

  bool expect(const char* expected) {
    std::string value;
    if (!token(value, expected)) return false;
    if (value == expected) return true;
    return reject(std::string("expected section key '") + expected + "'");
  }

  bool integer(std::int64_t& value, const char* description) {
    std::string text;
    if (!token(text, description)) return false;
    errno = 0;
    char* end = nullptr;
    const long long parsed = std::strtoll(text.c_str(), &end, 10);
    if (errno == ERANGE || end == text.c_str() || end == nullptr || *end != '\0' ||
        parsed < std::numeric_limits<std::int64_t>::min() ||
        parsed > std::numeric_limits<std::int64_t>::max())
      return reject(std::string("invalid integer for ") + description);
    value = static_cast<std::int64_t>(parsed);
    return true;
  }

  bool finite(double& value, const char* description) {
    std::string text;
    if (!token(text, description)) return false;
    errno = 0;
    char* end = nullptr;
    const double parsed = std::strtod(text.c_str(), &end);
    if (errno == ERANGE || end == text.c_str() || end == nullptr || *end != '\0')
      return reject(std::string("invalid number for ") + description);
    if (!std::isfinite(parsed))
      return reject(std::string("non-finite number for ") + description);
    value = parsed;
    return true;
  }

  bool flag(bool& value, const char* description) {
    std::int64_t raw = 0;
    if (!integer(raw, description)) return false;
    if (raw != 0 && raw != 1)
      return reject(std::string(description) + " must be 0 or 1");
    value = raw != 0;
    return true;
  }

  bool count(std::size_t& value, std::size_t maximum, const char* description) {
    std::int64_t raw = 0;
    if (!integer(raw, description)) return false;
    if (raw < 0 || static_cast<std::uint64_t>(raw) > maximum)
      return reject(std::string(description) + " is outside the supported limit");
    value = static_cast<std::size_t>(raw);
    return true;
  }

  bool claim_values(std::size_t amount, const char* description) {
    std::size_t next = 0;
    if (!checked_add(numeric_values_, amount, next) || next > kMaxNumericValues)
      return reject(std::string(description) + " exceeds the numeric-value budget");
    numeric_values_ = next;
    return true;
  }

  bool finish() {
    input_ >> std::ws;
    if (input_.bad()) return reject("input error after the faces section");
    if (input_.peek() != std::char_traits<char>::eof())
      return reject("unexpected data after the faces section");
    return true;
  }

  bool reject(const std::string& message) {
    if (error_.empty()) error_ = message;
    return false;
  }

  const std::string& error() const { return error_; }

 private:
  std::istream& input_;
  std::string error_;
  std::size_t numeric_values_ = 0;
};

bool curve_layout(std::size_t order, std::size_t control_points,
                  std::size_t& knot_count) {
  std::size_t sum = 0;
  if (!checked_add(order, control_points, sum) || sum < 2) return false;
  knot_count = sum - 2;
  return knot_count <= kMaxCurveKnots;
}

bool read_curve(ProtocolReader& reader, int expected_dimension, ON_NurbsCurve& curve) {
  std::int64_t dimension_raw = 0;
  std::int64_t rational_raw = 0;
  std::int64_t order_raw = 0;
  std::int64_t control_points_raw = 0;
  if (!reader.integer(dimension_raw, "curve dimension") ||
      !reader.integer(rational_raw, "curve rational flag") ||
      !reader.integer(order_raw, "curve order") ||
      !reader.integer(control_points_raw, "curve control-point count"))
    return false;

  if (dimension_raw != expected_dimension)
    return reader.reject("curve dimension does not match its protocol position");
  if (rational_raw != 0 && rational_raw != 1)
    return reader.reject("curve rational flag must be 0 or 1");
  if (order_raw < 2 || static_cast<std::uint64_t>(order_raw) > kMaxOrder)
    return reader.reject("curve order is outside the supported limit");
  if (control_points_raw < order_raw ||
      static_cast<std::uint64_t>(control_points_raw) > kMaxCurveControlPoints)
    return reader.reject("curve control-point count is outside the supported limit");

  const std::size_t order = static_cast<std::size_t>(order_raw);
  const std::size_t control_points = static_cast<std::size_t>(control_points_raw);
  const std::size_t components =
      static_cast<std::size_t>(expected_dimension) + (rational_raw != 0 ? 1U : 0U);
  std::size_t control_values = 0;
  std::size_t knot_count = 0;
  std::size_t total_values = 0;
  if (!checked_multiply(control_points, components, control_values) ||
      !curve_layout(order, control_points, knot_count) ||
      !checked_add(control_values, knot_count, total_values))
    return reader.reject("curve allocation arithmetic overflow");
  if (!reader.claim_values(total_values, "curve data")) return false;

  if (!curve.Create(expected_dimension, rational_raw != 0, static_cast<int>(order),
                    static_cast<int>(control_points)))
    return reader.reject("openNURBS refused curve allocation");

  for (std::size_t point_index = 0; point_index < control_points; ++point_index) {
    double* control_point = curve.CV(static_cast<int>(point_index));
    if (control_point == nullptr)
      return reader.reject("openNURBS returned a null curve control point");
    for (std::size_t component = 0; component < components; ++component) {
      double value = 0.0;
      if (!reader.finite(value, "curve control-point coordinate")) return false;
      control_point[component] = value;
    }
  }

  if (knot_count != 0 && curve.m_knot == nullptr)
    return reader.reject("openNURBS returned null curve knot storage");
  for (std::size_t knot_index = 0; knot_index < knot_count; ++knot_index) {
    double value = 0.0;
    if (!reader.finite(value, "curve knot")) return false;
    curve.m_knot[knot_index] = value;
  }
  return true;
}

bool surface_knot_count(std::size_t order, std::size_t control_points,
                        std::size_t& knot_count) {
  std::size_t sum = 0;
  if (!checked_add(order, control_points, sum) || sum < 2) return false;
  knot_count = sum - 2;
  return knot_count <= kMaxSurfaceKnotsPerDirection;
}

bool read_surface(ProtocolReader& reader, ON_NurbsSurface& surface) {
  std::int64_t rational_raw = 0;
  std::int64_t order_u_raw = 0;
  std::int64_t order_v_raw = 0;
  std::int64_t control_points_u_raw = 0;
  std::int64_t control_points_v_raw = 0;
  if (!reader.integer(rational_raw, "surface rational flag") ||
      !reader.integer(order_u_raw, "surface U order") ||
      !reader.integer(order_v_raw, "surface V order") ||
      !reader.integer(control_points_u_raw, "surface U control-point count") ||
      !reader.integer(control_points_v_raw, "surface V control-point count"))
    return false;

  if (rational_raw != 0 && rational_raw != 1)
    return reader.reject("surface rational flag must be 0 or 1");
  if (order_u_raw < 2 || order_v_raw < 2 ||
      static_cast<std::uint64_t>(order_u_raw) > kMaxOrder ||
      static_cast<std::uint64_t>(order_v_raw) > kMaxOrder)
    return reader.reject("surface order is outside the supported limit");
  if (control_points_u_raw < order_u_raw || control_points_v_raw < order_v_raw ||
      static_cast<std::uint64_t>(control_points_u_raw) >
          kMaxSurfaceControlPointsPerDirection ||
      static_cast<std::uint64_t>(control_points_v_raw) >
          kMaxSurfaceControlPointsPerDirection)
    return reader.reject("surface control-point count is outside the supported limit");

  const std::size_t order_u = static_cast<std::size_t>(order_u_raw);
  const std::size_t order_v = static_cast<std::size_t>(order_v_raw);
  const std::size_t control_points_u = static_cast<std::size_t>(control_points_u_raw);
  const std::size_t control_points_v = static_cast<std::size_t>(control_points_v_raw);
  std::size_t control_point_count = 0;
  if (!checked_multiply(control_points_u, control_points_v, control_point_count) ||
      control_point_count > kMaxSurfaceControlPoints)
    return reader.reject("surface control-point grid exceeds the supported limit");

  const std::size_t components = rational_raw != 0 ? 4U : 3U;
  std::size_t control_values = 0;
  std::size_t knot_count_u = 0;
  std::size_t knot_count_v = 0;
  std::size_t knot_values = 0;
  std::size_t total_values = 0;
  if (!checked_multiply(control_point_count, components, control_values) ||
      !surface_knot_count(order_u, control_points_u, knot_count_u) ||
      !surface_knot_count(order_v, control_points_v, knot_count_v) ||
      !checked_add(knot_count_u, knot_count_v, knot_values) ||
      !checked_add(control_values, knot_values, total_values))
    return reader.reject("surface allocation arithmetic overflow");
  if (!reader.claim_values(total_values, "surface data")) return false;

  if (!surface.Create(3, rational_raw != 0, static_cast<int>(order_u),
                      static_cast<int>(order_v), static_cast<int>(control_points_u),
                      static_cast<int>(control_points_v)))
    return reader.reject("openNURBS refused surface allocation");

  for (std::size_t u = 0; u < control_points_u; ++u) {
    for (std::size_t v = 0; v < control_points_v; ++v) {
      double* control_point = surface.CV(static_cast<int>(u), static_cast<int>(v));
      if (control_point == nullptr)
        return reader.reject("openNURBS returned a null surface control point");
      for (std::size_t component = 0; component < components; ++component) {
        double value = 0.0;
        if (!reader.finite(value, "surface control-point coordinate")) return false;
        control_point[component] = value;
      }
    }
  }

  if ((knot_count_u != 0 && surface.m_knot[0] == nullptr) ||
      (knot_count_v != 0 && surface.m_knot[1] == nullptr))
    return reader.reject("openNURBS returned null surface knot storage");
  for (std::size_t knot_index = 0; knot_index < knot_count_u; ++knot_index) {
    double value = 0.0;
    if (!reader.finite(value, "surface U knot")) return false;
    surface.m_knot[0][knot_index] = value;
  }
  for (std::size_t knot_index = 0; knot_index < knot_count_v; ++knot_index) {
    double value = 0.0;
    if (!reader.finite(value, "surface V knot")) return false;
    surface.m_knot[1][knot_index] = value;
  }
  return true;
}

bool valid_index(std::int64_t index, std::size_t count) {
  return index >= 0 && static_cast<std::uint64_t>(index) < count;
}

bool parse_description(std::istream& input, ON_Brep& brep, std::string& error) {
  ProtocolReader reader(input);
  std::string protocol;
  std::int64_t version = 0;
  if (!reader.token(protocol, "protocol name") ||
      !reader.integer(version, "protocol version")) {
    error = reader.error();
    return false;
  }
  if (protocol != "ONBREP" || version != 1) {
    error = "not an ONBREP 1 description";
    return false;
  }

  double tolerance = 0.0;
  if (!reader.expect("tol") || !reader.finite(tolerance, "tolerance")) {
    error = reader.error();
    return false;
  }
  if (tolerance < 0.0) {
    error = "tolerance must be non-negative";
    return false;
  }

  std::size_t vertex_count = 0;
  if (!reader.expect("vertices") ||
      !reader.count(vertex_count, kMaxVertices, "vertex count")) {
    error = reader.error();
    return false;
  }
  std::size_t vertex_values = 0;
  if (!checked_multiply(vertex_count, 3U, vertex_values) ||
      !reader.claim_values(vertex_values, "vertex coordinates")) {
    error = reader.error().empty() ? "vertex allocation arithmetic overflow" : reader.error();
    return false;
  }
  for (std::size_t index = 0; index < vertex_count; ++index) {
    double x = 0.0;
    double y = 0.0;
    double z = 0.0;
    if (!reader.finite(x, "vertex X coordinate") ||
        !reader.finite(y, "vertex Y coordinate") ||
        !reader.finite(z, "vertex Z coordinate")) {
      error = reader.error();
      return false;
    }
    brep.NewVertex(ON_3dPoint(x, y, z), tolerance);
    if (brep.m_V.Count() != static_cast<int>(index + 1)) {
      error = "openNURBS refused a vertex";
      return false;
    }
  }

  std::size_t edge_count = 0;
  if (!reader.expect("edges") || !reader.count(edge_count, kMaxEdges, "edge count")) {
    error = reader.error();
    return false;
  }
  for (std::size_t index = 0; index < edge_count; ++index) {
    std::int64_t vertex_0 = -1;
    std::int64_t vertex_1 = -1;
    if (!reader.integer(vertex_0, "edge start vertex index") ||
        !reader.integer(vertex_1, "edge end vertex index")) {
      error = reader.error();
      return false;
    }
    if (!valid_index(vertex_0, vertex_count) || !valid_index(vertex_1, vertex_count)) {
      error = "edge vertex index is out of range";
      return false;
    }

    std::unique_ptr<ON_NurbsCurve> curve(new (std::nothrow) ON_NurbsCurve());
    if (!curve) {
      error = "unable to allocate an edge curve";
      return false;
    }
    if (!read_curve(reader, 3, *curve)) {
      error = reader.error();
      return false;
    }
    const int curve_index = brep.AddEdgeCurve(curve.get());
    if (curve_index < 0 || curve_index >= brep.m_C3.Count()) {
      error = "openNURBS refused an edge curve";
      return false;
    }
    curve.release();
    ON_BrepEdge& edge = brep.NewEdge(
        brep.m_V[static_cast<int>(vertex_0)], brep.m_V[static_cast<int>(vertex_1)],
        curve_index, nullptr, tolerance);
    edge.m_tolerance = tolerance;
    if (brep.m_E.Count() != static_cast<int>(index + 1)) {
      error = "openNURBS refused an edge";
      return false;
    }
  }

  std::size_t face_count = 0;
  if (!reader.expect("faces") || !reader.count(face_count, kMaxFaces, "face count")) {
    error = reader.error();
    return false;
  }
  std::size_t total_loops = 0;
  std::size_t total_trims = 0;
  for (std::size_t face_index = 0; face_index < face_count; ++face_index) {
    bool reversed = false;
    if (!reader.flag(reversed, "face reversed flag")) {
      error = reader.error();
      return false;
    }

    std::unique_ptr<ON_NurbsSurface> surface(new (std::nothrow) ON_NurbsSurface());
    if (!surface) {
      error = "unable to allocate a face surface";
      return false;
    }
    if (!read_surface(reader, *surface)) {
      error = reader.error();
      return false;
    }
    ON_NurbsSurface* surface_pointer = surface.get();
    const int surface_index = brep.AddSurface(surface_pointer);
    if (surface_index < 0 || surface_index >= brep.m_S.Count()) {
      error = "openNURBS refused a face surface";
      return false;
    }
    surface.release();
    ON_BrepFace& face = brep.NewFace(surface_index);
    face.m_bRev = reversed;
    if (brep.m_F.Count() != static_cast<int>(face_index + 1)) {
      error = "openNURBS refused a face";
      return false;
    }

    std::size_t loop_count = 0;
    if (!reader.count(loop_count, kMaxLoopsPerFace, "face loop count")) {
      error = reader.error();
      return false;
    }
    std::size_t next_total_loops = 0;
    if (!checked_add(total_loops, loop_count, next_total_loops) ||
        next_total_loops > kMaxTotalLoops) {
      error = "total loop count exceeds the supported limit";
      return false;
    }
    total_loops = next_total_loops;

    for (std::size_t loop_index = 0; loop_index < loop_count; ++loop_index) {
      std::int64_t loop_type_raw = 0;
      std::size_t trim_count = 0;
      if (!reader.integer(loop_type_raw, "loop type") ||
          !reader.count(trim_count, kMaxTrimsPerLoop, "loop trim count")) {
        error = reader.error();
        return false;
      }
      if (loop_type_raw != 1 && loop_type_raw != 2) {
        error = "loop type must be 1 (outer) or 2 (inner)";
        return false;
      }
      std::size_t next_total_trims = 0;
      if (!checked_add(total_trims, trim_count, next_total_trims) ||
          next_total_trims > kMaxTotalTrims) {
        error = "total trim count exceeds the supported limit";
        return false;
      }
      total_trims = next_total_trims;

      const ON_BrepLoop::TYPE loop_type =
          loop_type_raw == 1 ? ON_BrepLoop::outer : ON_BrepLoop::inner;
      ON_BrepLoop& loop = brep.NewLoop(loop_type, face);

      for (std::size_t trim_index = 0; trim_index < trim_count; ++trim_index) {
        std::int64_t edge_index = -1;
        std::int64_t reversed_3d_raw = 0;
        std::int64_t singular_vertex_index = -1;
        if (!reader.integer(edge_index, "trim edge index") ||
            !reader.integer(reversed_3d_raw, "trim reversed flag") ||
            !reader.integer(singular_vertex_index, "trim singular vertex index")) {
          error = reader.error();
          return false;
        }
        if (reversed_3d_raw != 0 && reversed_3d_raw != 1) {
          error = "trim reversed flag must be 0 or 1";
          return false;
        }
        if (edge_index < -1) {
          error = "trim edge index must be -1 or a valid edge index";
          return false;
        }
        if (edge_index == -1) {
          if (reversed_3d_raw != 0) {
            error = "a singular trim cannot reverse a 3-D edge";
            return false;
          }
          if (!valid_index(singular_vertex_index, vertex_count)) {
            error = "singular trim vertex index is out of range";
            return false;
          }
        } else {
          if (!valid_index(edge_index, edge_count)) {
            error = "trim edge index is out of range";
            return false;
          }
          if (singular_vertex_index != -1) {
            error = "a non-singular trim must use vertex index -1";
            return false;
          }
        }

        std::unique_ptr<ON_NurbsCurve> trim_curve(new (std::nothrow) ON_NurbsCurve());
        if (!trim_curve) {
          error = "unable to allocate a trim curve";
          return false;
        }
        if (!read_curve(reader, 2, *trim_curve)) {
          error = reader.error();
          return false;
        }
        ON_NurbsCurve* trim_curve_pointer = trim_curve.get();
        const int trim_curve_index = brep.AddTrimCurve(trim_curve_pointer);
        if (trim_curve_index < 0 || trim_curve_index >= brep.m_C2.Count()) {
          error = "openNURBS refused a trim curve";
          return false;
        }
        trim_curve.release();

        ON_BrepTrim* trim = nullptr;
        if (edge_index == -1) {
          const ON_Surface::ISO iso =
              surface_pointer->IsIsoparametric(*trim_curve_pointer);
          trim = &brep.NewSingularTrim(
              brep.m_V[static_cast<int>(singular_vertex_index)], loop, iso,
              trim_curve_index);
        } else {
          trim = &brep.NewTrim(brep.m_E[static_cast<int>(edge_index)],
                               reversed_3d_raw != 0, loop, trim_curve_index);
        }
        trim->m_tolerance[0] = tolerance;
        trim->m_tolerance[1] = tolerance;
      }
    }
  }

  if (!reader.finish()) {
    error = reader.error();
    return false;
  }
  return true;
}

int fail(const std::string& reason) {
  std::fprintf(stderr, "brep_writer: %s\n", reason.c_str());
  return 2;
}

bool set_binary_stdout() {
#if defined(_WIN32)
  return _setmode(_fileno(stdout), _O_BINARY) != -1;
#else
  return true;
#endif
}

int write_archive(const ON_Brep& brep) {
  if (!set_binary_stdout()) return fail("unable to set stdout to binary mode");

  ON_Write3dmBufferArchive archive(0, kMaxArchiveBytes, 60, ON::Version());
  if (!archive.WriteObject(brep)) return fail("WriteObject failed");
  const std::size_t archive_size =
      static_cast<std::size_t>(archive.SizeOfArchive());
  if (archive_size != 0 && archive.Buffer() == nullptr)
    return fail("archive buffer is unavailable");
  if (archive_size != 0 &&
      std::fwrite(archive.Buffer(), 1, archive_size, stdout) != archive_size)
    return fail("unable to write the complete archive");
  if (std::fflush(stdout) != 0) return fail("unable to flush the archive");
  std::fprintf(stderr, "opennurbs %u\n", ON::Version());
  return 0;
}

int run(int argc, char** argv) {
  if (argc == 2 && std::string(argv[1]) == "--version") {
    if (std::printf("brep_writer ONBREP 1 opennurbs %u\n", ON::Version()) < 0 ||
        std::fflush(stdout) != 0)
      return fail("unable to write version information");
    return 0;
  }

  if (argc == 2 && std::string(argv[1]) == "box") {
    ON_3dPoint corners[8] = {
        ON_3dPoint(0, 0, 0), ON_3dPoint(1, 0, 0), ON_3dPoint(1, 1, 0),
        ON_3dPoint(0, 1, 0), ON_3dPoint(0, 0, 1), ON_3dPoint(1, 0, 1),
        ON_3dPoint(1, 1, 1), ON_3dPoint(0, 1, 1)};
    std::unique_ptr<ON_Brep> box(ON_BrepBox(corners));
    return box ? write_archive(*box) : fail("ON_BrepBox failed");
  }

  if (argc != 1) return fail("usage: brep_writer [--version|box]");

  ON_Brep brep;
  std::string error;
  if (!parse_description(std::cin, brep, error)) return fail(error);

  // The protocol supplies loop orientation through ordered trims and face reversal.
  // openNURBS derives the remaining trim classifications before final validation.
  brep.SetTrimTypeFlags(true);
  brep.SetTrimIsoFlags();
  brep.SetTolerancesBoxesAndFlags(false, false, false, false, false, false, false,
                                  false);

  ON_wString validation_text;
  ON_TextLog validation_log(validation_text);
  if (!brep.IsValid(&validation_log)) {
    const ON_String narrow_text(validation_text);
    const char* details = narrow_text.Array();
    std::fprintf(stderr, "brep_writer: the brep is NOT valid:\n%s\n",
                 details != nullptr ? details : "(no validation details)");
    return 2;
  }
  return write_archive(brep);
}

}  // namespace

int main(int argc, char** argv) {
  try {
    ON::Begin();
    return run(argc, argv);
  } catch (const std::bad_alloc&) {
    return fail("resource allocation failed");
  } catch (const std::exception&) {
    return fail("unexpected runtime failure");
  } catch (...) {
    return fail("unexpected non-standard failure");
  }
}
