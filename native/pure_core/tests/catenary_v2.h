/* Catenary v2 fragment for Main's shared-header integration. ABI 1 unchanged.
 * Inputs: four readable xyz triples; disjoint outputs writable for capacities.
 * Counts are packed scalar counts, not point counts. At most 1155 pole values
 * and 387 knots. No arrays/counts are published on failure; every nonnull scalar
 * output is reset even when another scalar is null.
 * Status: 0 success, -1 null/invalid span, -2 input/frame, -3 capacity,
 * -4 tolerance. reason=0 on success/infrastructure error, otherwise enum below.
 * v1 remains available and delegates to this same planner without a reason.
 */
#ifndef NARWHAL_CATENARY_V2_H
#define NARWHAL_CATENARY_V2_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
enum narwhal_catenary_reason {
    NARWHAL_CATENARY_PARAMETERS = 1,
    NARWHAL_CATENARY_DIRECTION = 2,
    NARWHAL_CATENARY_HEIGHT = 3,
    NARWHAL_CATENARY_THROUGH = 4,
    NARWHAL_CATENARY_TOLERANCE = 5,
    NARWHAL_CATENARY_ARITHMETIC = 6
};
int32_t narwhal_pure_core_catenary_plan_v2(
    const double *start, const double *end, const double *axis_point,
    const double *through, double tolerance,
    double *poles, size_t pole_capacity, double *knots, size_t knot_capacity,
    uint64_t *out_poles, uint64_t *out_knots, uint64_t *out_spans,
    double *out_deviation, uint32_t *out_reason);
#ifdef __cplusplus
}
#endif
#endif
