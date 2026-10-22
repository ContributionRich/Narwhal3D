/* SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef NARWHAL_GEOMETRY_CORE_H
#define NARWHAL_GEOMETRY_CORE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum {
  NARWHAL_GEOMETRY_CORE_ABI_VERSION = 4
};

#define NARWHAL_GEOMETRY_MAX_BUFFER_BYTES (UINT64_C(512) * UINT64_C(1024) * UINT64_C(1024))
#define NARWHAL_GEOMETRY_LOCAL_MOVE_ZERO_TRANSLATION (1.0e-14)
#define NARWHAL_GEOMETRY_LOCAL_MOVE_MAX_NORMAL_JUMP (2.0e-6)
#define NARWHAL_GEOMETRY_LOCAL_MOVE_MAX_CANDIDATES UINT32_C(25)
#define NARWHAL_GEOMETRY_LOCAL_MOVE_EDGE_SAMPLES UINT32_C(33)
#define NARWHAL_GEOMETRY_MAX_DECODED_FACES UINT64_C(100000)
#define NARWHAL_GEOMETRY_MAX_DECODED_EDGES UINT64_C(500000)
#define NARWHAL_GEOMETRY_MAX_DECODED_VERTICES UINT64_C(500000)

enum NarwhalGeometryCapability {
  NARWHAL_GEOMETRY_CAP_MAKE_BOX_BREP = UINT64_C(1) << 0,
  NARWHAL_GEOMETRY_CAP_INSPECT_BREP = UINT64_C(1) << 1,
  NARWHAL_GEOMETRY_CAP_BINTOOLS_NO_TRIANGULATION = UINT64_C(1) << 2,
  NARWHAL_GEOMETRY_CAP_EXACT_MASS_PROPERTIES = UINT64_C(1) << 3,
  NARWHAL_GEOMETRY_CAP_TOPOLOGY_LOCAL_MOVE = UINT64_C(1) << 4,
  NARWHAL_GEOMETRY_CAP_EDIT_SESSIONS = UINT64_C(1) << 5
};

enum NarwhalGeometryComponentKind {
  NARWHAL_GEOMETRY_COMPONENT_FACE = 1,
  NARWHAL_GEOMETRY_COMPONENT_EDGE = 2,
  NARWHAL_GEOMETRY_COMPONENT_VERTEX = 3
};

enum NarwhalGeometryValidationMode {
  NARWHAL_GEOMETRY_VALIDATION_LIVE = 1,
  NARWHAL_GEOMETRY_VALIDATION_COMMIT = 2
};

enum NarwhalGeometryStatus {
  NARWHAL_GEOMETRY_OK = 0,
  NARWHAL_GEOMETRY_ERROR_INVALID_ARGUMENT = -1,
  NARWHAL_GEOMETRY_ERROR_LIMIT_EXCEEDED = -2,
  NARWHAL_GEOMETRY_ERROR_BUFFER_TOO_SMALL = -3,
  NARWHAL_GEOMETRY_ERROR_KERNEL_FAILURE = -4,
  NARWHAL_GEOMETRY_ERROR_MALFORMED_BREP = -5,
  NARWHAL_GEOMETRY_ERROR_UNEXPECTED_FAILURE = -6,
  NARWHAL_GEOMETRY_ERROR_POLICY_REFUSED = -7,
  NARWHAL_GEOMETRY_ERROR_SMOOTHNESS_CREASE = -8,
  NARWHAL_GEOMETRY_ERROR_INVALID_HANDLE = -9
};

enum NarwhalGeometryRefusalReason {
  NARWHAL_GEOMETRY_REFUSAL_NONE = 0,
  NARWHAL_GEOMETRY_REFUSAL_INPUT_NOT_AIRTIGHT = 1,
  NARWHAL_GEOMETRY_REFUSAL_INDEX_OUTSIDE = 2,
  NARWHAL_GEOMETRY_REFUSAL_DEGENERATE = 3,
  NARWHAL_GEOMETRY_REFUSAL_UNSUPPORTED_BOUNDARY = 4,
  NARWHAL_GEOMETRY_REFUSAL_STRICT_CONTAINMENT = 5,
  NARWHAL_GEOMETRY_REFUSAL_TOPOLOGY = 6,
  NARWHAL_GEOMETRY_REFUSAL_NO_CANDIDATE = 7,
  NARWHAL_GEOMETRY_REFUSAL_AMBIGUOUS = 8,
  NARWHAL_GEOMETRY_REFUSAL_SMOOTHNESS = 9,
  NARWHAL_GEOMETRY_REFUSAL_TOLERANCE = 10,
  NARWHAL_GEOMETRY_REFUSAL_SELF_INTERSECTION = 11,
  NARWHAL_GEOMETRY_REFUSAL_RESOURCE_LIMIT = 12
};

enum NarwhalGeometryQualityScope {
  NARWHAL_GEOMETRY_QUALITY_RECONSTRUCTED_FACES = 1U << 0,
  NARWHAL_GEOMETRY_QUALITY_BSPLINE_BOUNDARY = 1U << 1,
  NARWHAL_GEOMETRY_QUALITY_INTERNAL_KNOT = 1U << 2,
  NARWHAL_GEOMETRY_QUALITY_MOVED_VERTEX_MAPPING = 1U << 3,
  NARWHAL_GEOMETRY_QUALITY_MAPPED_INCIDENCE = 1U << 4,
  NARWHAL_GEOMETRY_QUALITY_UNCHANGED_GEOMETRY = 1U << 5
};

typedef uint64_t NarwhalEditSessionHandle;

typedef struct NarwhalBrepInspectionV1 {
  uint32_t struct_size;
  uint32_t abi_version;
  uint32_t valid;
  uint32_t airtight;
  uint64_t face_count;
  uint64_t edge_count;
  uint64_t vertex_count;
  double volume;
  uint64_t plane_faces;
  uint64_t cylinder_faces;
  uint64_t cone_faces;
  uint64_t sphere_faces;
  uint64_t torus_faces;
  uint64_t bezier_faces;
  uint64_t bspline_faces;
  uint64_t revolution_faces;
  uint64_t extrusion_faces;
  uint64_t offset_faces;
  uint64_t other_faces;
} NarwhalBrepInspectionV1;

typedef struct NarwhalLocalMoveCertificateV3 {
  uint32_t struct_size;
  uint32_t abi_version;
  int32_t status;
  uint32_t refusal_reason;
  uint32_t valid;
  uint32_t airtight;
  uint32_t self_intersection_free;
  uint32_t topology_counts_preserved;
  uint32_t moved_vertex_mapping_verified;
  uint32_t mapped_incidence_preserved;
  uint32_t unchanged_geometry_preserved;
  uint32_t selected_match_count;
  uint32_t component_kind;
  uint32_t quality_scope_bits;
  uint32_t quality_checked_bits;
  uint32_t reconstructed_face_count;
  uint32_t reconstructed_bspline_face_count;
  uint32_t internal_knot_face_count;
  uint32_t verification_failure_stage;
  uint32_t verification_failure_detail;
  uint64_t face_count;
  uint64_t edge_count;
  uint64_t vertex_count;
  double volume;
  double selected_translation_error;
  double max_boundary_error;
  double max_internal_normal_jump;
  double comparison_tolerance;
  double build_tolerance;
  double sewing_tolerance;
  uint64_t plane_faces;
  uint64_t cylinder_faces;
  uint64_t cone_faces;
  uint64_t sphere_faces;
  uint64_t torus_faces;
  uint64_t bezier_faces;
  uint64_t bspline_faces;
  uint64_t revolution_faces;
  uint64_t extrusion_faces;
  uint64_t offset_faces;
  uint64_t other_faces;
} NarwhalLocalMoveCertificateV3;

uint32_t narwhal_geometry_core_abi_version(void);
uint64_t narwhal_geometry_core_capabilities(void);
int64_t narwhal_geometry_core_make_box_brep(
    double dx, double dy, double dz, uint8_t *output, size_t capacity);
int32_t narwhal_geometry_core_inspect_brep(
    const uint8_t *input, size_t input_length, NarwhalBrepInspectionV1 *output);
int64_t narwhal_geometry_core_move_component_brep(
    const uint8_t *input, size_t input_length, uint32_t component_kind,
    uint64_t component_index, double dx, double dy, double dz,
    uint8_t *output, size_t capacity, NarwhalLocalMoveCertificateV3 *certificate);
int32_t narwhal_geometry_core_edit_session_import(
    const uint8_t *input, size_t input_length, NarwhalEditSessionHandle *output_handle);
int64_t narwhal_geometry_core_edit_session_move_mode(
    NarwhalEditSessionHandle handle, uint32_t validation_mode,
    uint32_t component_kind, uint64_t component_index,
    double dx, double dy, double dz, uint8_t *output, size_t capacity,
    NarwhalLocalMoveCertificateV3 *certificate);
int64_t narwhal_geometry_core_edit_session_move(
    NarwhalEditSessionHandle handle, uint32_t component_kind,
    uint64_t component_index, double dx, double dy, double dz,
    uint8_t *output, size_t capacity, NarwhalLocalMoveCertificateV3 *certificate);
int32_t narwhal_geometry_core_edit_session_release(NarwhalEditSessionHandle handle);

#ifdef __cplusplus
} /* extern "C" */
static_assert(sizeof(NarwhalBrepInspectionV1) == 136,
              "NarwhalBrepInspectionV1 ABI size changed");
static_assert(sizeof(NarwhalLocalMoveCertificateV3) == 248,
              "NarwhalLocalMoveCertificateV3 ABI size changed");
#else
_Static_assert(sizeof(NarwhalBrepInspectionV1) == 136,
               "NarwhalBrepInspectionV1 ABI size changed");
_Static_assert(sizeof(NarwhalLocalMoveCertificateV3) == 248,
               "NarwhalLocalMoveCertificateV3 ABI size changed");
#endif

#endif /* NARWHAL_GEOMETRY_CORE_H */
