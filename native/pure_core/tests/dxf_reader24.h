/* Reader24 ABI1: original ASCII/Latin1 file semantics, not geometry validation.
 * No old decoder/writer ABI is changed. Rust retains no caller-owned pointers.
 */
#ifndef NARWHAL_DXF_READER24_H
#define NARWHAL_DXF_READER24_H
#include <stdint.h>
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct {
    int64_t code;
    size_t start, len;
    int64_t detail;
    size_t required;
} NarwhalDxfReader24Diagnostic;
uint32_t narwhal_pure_core_dxf_reader24_abi_version(void); /* must be1 */
/* data/outputs must be valid aligned nonoverlapping spans, written one size_t,
 * diagnostic one struct. int_limit is CPython get_int_max_str_digits() (0=off).
 * rc0: complete typed result; rc1: capacity retry, diagnostic.required set.
 * All other rc values are negative; no payload/count is published on failure.
 * -1 pointers, -2 ABI, -3 resource admission. -100..-107 FileFormatError;
 * -110 int grammar, -111 float grammar, -112 KeyError(detail), -113 infinity
 * float->int OverflowError, -114 NaN float->int ValueError, -115 int digit limit.
 * Error start/len address the original Latin1 token for host exception framing.
 * Resource bounds:512MiB input/output,4M pairs,16384 decimal digits when the
 * interpreter's own limit permits it,1MiB numeric float token. Never replay.
 */
int32_t narwhal_pure_core_dxf_reader24_decode(const uint8_t *, size_t, uint32_t,
    uint8_t *, size_t, size_t *, NarwhalDxfReader24Diagnostic *);
/* Output:8-byte DXFR24,0,1 magic then typed nodes. Integer/counts little-endian.
 * Tags:0 None,1 false,2 true,3 i64,4 sign-u8 + u64-byte-count + LE magnitude,
 * 5 f64,6 u64-byte-count + Latin1 text,7 u64-count + list nodes,
 * 8 three f64 tuple,9 u64-count + f64 list,10 u64-count + xyz f64 tuples,
 * 11 u64-count + (text-key,node) dictionary entries in insertion order.
 * Nonfinite file values are not claimed to be safe manufacturing geometry.
 */
#ifdef __cplusplus
}
#endif
#endif
