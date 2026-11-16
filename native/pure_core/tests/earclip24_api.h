/* Required planar-loop helpers; stable core ABI remains 1.
 * Legacy ear_clip_display signature/status/count semantics remain unchanged.
 * Maximum 1<<20 points. Live/aligned/nonoverlapping spans; output capacity
 * counts u64 indices, not triangles. Clip requires finite coordinates.
 * Refusal clears count to zero without writing payload. A successful stalled
 * loop returns its exact partial triples; it is not a native-support error.
 * Signed area preserves original IEEE accumulation, including nonfinite
 * results; it is not an analytic manufacturing-area API. Its count is one
 * on success (including empty loops), zero on refusal.
 * Status: 0 success; -1 invalid pointer span; -2 input/bound; -3 clip capacity.
 */
#ifndef NARWHAL_EARCLIP24_API_H
#define NARWHAL_EARCLIP24_API_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
int32_t narwhal_pure_core_ear_clip_display(const double *, size_t, uint64_t *, size_t, uint64_t *);
int32_t narwhal_pure_core_earclip_signed_area_v1(const double *, size_t, double *, uint64_t *);

#ifdef __cplusplus
}
#endif
#endif
