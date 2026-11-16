/* PREVIEW-SAMPLING-RETIRE-24 registration fragment. Display only.
 * Main: add `pub mod preview_primitives;` to src/lib.rs, register the export below.
 * Existing global ABI remains 1; existing control-point preview export retained.
 * No native binary was staged by this worker. Qualify Windows before activation.
 */
#ifndef NARWHAL_PREVIEW24_API_H
#define NARWHAL_PREVIEW24_API_H
#include <stddef.h>
#include <stdint.h>
typedef struct NarwhalPreviewMetadataV1 {
    size_t values;
    size_t rows;
    size_t lengths[3];
} NarwhalPreviewMetadataV1;
/* op 1 circle/2 ring: center[3], x[3], y[3], radius; flags=0.
 * op 3 polygon: same + start_angle,direction; flags=1 inscribed,0 circumscribed.
 * op 4 ellipse: center,axis1,axis2-or-cursor,cursor; flags=1..3 pick stage.
 * op 5 ellipsoid: center,axis1,cursor,normal,second_radius; flags=1..3 stage.
 * op 6 rectangle: two LOCAL corners; segments=0; callbacks remain in Python.
 * All arrays xyz triples. segments 1..100000 except rectangle.
 * 0 success; -1 invalid pointer/layout/overlap; -2 input/resource/nonfinite;
 * -3 capacity. Outputs untouched on any error; metadata written only on 0/-3.
 * Null output accepted with capacity=0, including a size query. All nonempty
 * spans valid/aligned and disjoint from each other and metadata. Caller owns
 * all storage; there are no allocations/handles crossing the ABI.
 */
int32_t narwhal_pure_core_preview_sample_v1(uint32_t op, const double *data,
    size_t data_len, size_t segments, uint32_t flags, double *output,
    size_t capacity, NarwhalPreviewMetadataV1 *metadata);
#endif
