/* Stable pre-reader24 DXF decode ABI. No caller pointer is retained. */
#ifndef NARWHAL_DXF_LEGACY_ABI_H
#define NARWHAL_DXF_LEGACY_ABI_H
#include <stdint.h>
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Null data with len0 returns0; nonnull empty input has a40-byte packed result.
 * Size and decode errors:-1 null,-3 size,-20 truncated,-21 binary/version,
 * -22 bad numeric/entity data. Decode needs exact capacity, not just >=size.
 * All pointers must designate valid nonoverlapping spans of the stated sizes.
 * Failure leaves the output unchanged. Header/layout:fixtures/dxf_legacy_abi.
 */
int64_t narwhal_pure_core_dxf_decode_size(const uint8_t *data, size_t len);
int32_t narwhal_pure_core_dxf_decode(const uint8_t *data, size_t len,
                                   uint8_t *output, size_t exact_size);
#ifdef __cplusplus
}
#endif
#endif
