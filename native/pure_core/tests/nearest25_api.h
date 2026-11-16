/* NEAREST-CURVE-RETIRE-25 export/admission fragment for Main.
 * Existing public nearest module already exports these symbols; no lib.rs edit
 * needed. Preserve existing scalar/batch/sample signatures and global ABI=1.
 */
#ifndef NARWHAL_NEAREST25_API_H
#define NARWHAL_NEAREST25_API_H
#include <stddef.h>
#include <stdint.h>
/* Synchronous original SDK evaluation, no NURBS conversion. order=0 writes
 * PointAt xyz (3 doubles); order=2 writes C,C',C'' (9 doubles).
 * Return 0 success; 1 derivative unavailable/raised/short (order2 ONLY), which
 * stops Newton at current t; any other code aborts with -4. Host retains original
 * exception and rethrows AFTER C returns. Never unwind across C; never retain
 * pointers or modify input spans. Write only the provided 3/9 output slots.
 */
typedef int32_t (*NarwhalCurveEvaluateV1)(void *context, double t,
    uint32_t order, double *output);
/* Original metadata, NOT a different curve's NURBS parameterization. Empty
 * knots uses span_count uniform spans. Nonempty knots use old grain-filter,
 * sorted/multiplicity-collapse order; span_count then does not generate cuts.
 * -1 pointer/alignment/overlap; -2 nonfinite/invalid/resource/overflow;
 * -4 callback failure. Both output buffers unchanged on ANY failure.
 * All nonempty spans live/aligned; outputs disjoint from inputs/each other.
 * Callback and opaque context remain alive for this call only. No ownership
 * crosses the ABI. Context validity and callback runtime are host obligations.
 * Limits: 100000 knots, 20000 spans, 1000000 actual SDK evaluations per search.
 * Existing NURBS exports: <=100000 targets, <=20000 spans, <=500000000 estimated
 * actual weighted evaluations; mandatory-sample preflight, original evaluator max order32 unchanged.
 * Legacy batch nonfinite-target NaN slots remain; required adapter refuses them.
 * Qualify matching actual Windows scalar/batch/callback path before activation;
 * do NOT lift BATCH_QUALIFIED_PLATFORMS={linux} from Linux-only results.
 */
int32_t narwhal_pure_core_nearest_on_callbacks_v1(double t0, double t1,
    const double *knots, size_t knot_count, size_t span_count, uint8_t closed,
    NarwhalCurveEvaluateV1 callback, void *context, const double target[3],
    double out_values[5], uint8_t *out_at_end);
#endif
