/* Owned Tween ABI v1 fragment for Main registration; old tween_controls unchanged.
 * mode 0: SDK homogeneous xyzw -> homogeneous xyzw (flags: rational A=1/B=2)
 * mode 1: Euclidean xyzw -> Euclidean xyzw; mode 2: xyz sample blending.
 * Outputs are ordered curve-major then point-major. out_values counts doubles.
 * Every error leaves output and out_values unchanged. Input/output spans must
 * not overlap. Value cap: 50,000,000 doubles. Core ABI remains 1.
 * -1 null, -2 input/mode, -3 capacity, -10 zero source weight,
 * -11 invalid/overflow result, -12 nonfinite source, -13 resource cap,
 * -20 mismatching knots. Never reconstruct/fallback after native failure.
 */
#include <stdint.h>
#include <stddef.h>
int32_t narwhal_pure_core_tween_plan_v1(const double*,const double*,size_t,size_t,uint32_t,uint32_t,double*,size_t,uint64_t*);
int32_t narwhal_pure_core_tween_knots_v1(const double*,const double*,size_t);
int32_t narwhal_pure_core_tween_lengths_v1(double,size_t,double*,size_t,uint64_t*);
