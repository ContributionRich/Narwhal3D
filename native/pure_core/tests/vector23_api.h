/* Additive vector ownership ABI v1. Main owns shared-header registration.
 * Old PDF/SVG/unit encode-into exports and their admission rules remain stable.
 * New format: 0=PDF (AI alias), 1=SVG; unit is UTF8, title PDF Latin1 bytes.
 * title_parts is empty for string titles; otherwise per-original-iterable atom
 * Latin1 lengths (including empty atoms). A one-byte atom is PDF escaped; longer
 * atoms are verbatim, preserving original non-string title semantics exactly.
 * All spans are caller-owned/live/aligned, immutable for encode; NULL only for
 * zero length. Each layer has its own length and SVG layers must each be UTF8.
 * Cubics are packed p0.x,p0.y,...,p3.x,p3.y; counts are per-stroke segment counts.
 * Input/output metadata may not alias. Scalars, complete lengths/sums, ranges,
 * finite arithmetic and explicit bounds are checked before payload writes.
 * On valid-metadata encode refusal: result=NULL. Invalid pointer/alias metadata
 * is untouched. No payload or written count is changed by a failed copy.
 * Encode once -> opaque owned handle -> length/copy -> free exactly once.
 * Handle must be live from this library, not forged/reused after free. NULL free
 * is valid; NULL length is 0. Copy target cannot alias handle storage/count.
 * Limits: 1M strokes, 2M segments, 64MiB names/title, 1MiB UTF8 unit suffix,
 * precision 0..4096, conservative complete formatted-output budget 512MiB.
 * Status: 0=success; -1=pointer; -2=shape/metadata/text/flags;
 * -3=resource/precision; -4=nonfinite/overflowed converted numeric value;
 * -5=nonpositive/nonfinite unit scale; -6=document protocol/framing failure. No Python codec replay on any status.
 */
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct { const double *data; size_t len; } NarwhalVectorDoublesV1;
typedef struct { const int64_t *data; size_t len; } NarwhalVectorIntsV1;
typedef struct { const int32_t *data; size_t len; } NarwhalVectorFlagsV1;
typedef struct { const uint8_t *data; size_t len; } NarwhalVectorBytesV1;
typedef struct {
 size_t size; uint32_t format; int32_t digits;
 double page_w_mm, page_h_mm, unit_mm; size_t segments;
 NarwhalVectorDoublesV1 beziers; NarwhalVectorIntsV1 counts;
 NarwhalVectorFlagsV1 closed; NarwhalVectorDoublesV1 rgb, widths;
 NarwhalVectorBytesV1 layers; NarwhalVectorIntsV1 layer_lengths;
 NarwhalVectorBytesV1 title, unit; NarwhalVectorIntsV1 title_parts;
} NarwhalVectorRequestV1;
typedef struct NarwhalVectorEncodedV1 NarwhalVectorEncodedV1;
int32_t narwhal_pure_core_vector_encode_v1(const NarwhalVectorRequestV1*, NarwhalVectorEncodedV1**);
size_t narwhal_pure_core_vector_len_v1(const NarwhalVectorEncodedV1*);
int32_t narwhal_pure_core_vector_copy_v1(const NarwhalVectorEncodedV1*, uint8_t*, size_t, size_t*);
void narwhal_pure_core_vector_free_v1(NarwhalVectorEncodedV1*);

int32_t narwhal_pure_core_vector_validate_v1(uint32_t format, const uint8_t*, size_t len);

#ifdef __cplusplus
}
#endif
