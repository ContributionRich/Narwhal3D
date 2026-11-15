/* Required DXF writer ABI v2. All input/output spans must be valid, aligned,
 * nonoverlapping, and live for the call. Rust retains no pointers.
 * Names are concatenated UTF-8 (surrogatepass/WTF-8 solely for exception frames).
 * Python mapping key identities are opaque name IDs, not serialized spellings.
 * Entity name is the mapping identity; label is its own text ID in the same
 * name pool (equal Python keys can have different spellings: True versus 1).
 * Layer merge, color/coordinate validation and serialization belong to Rust.
 */
#ifndef NARWHAL_DXF_OWNED_H
#define NARWHAL_DXF_OWNED_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

typedef struct { int64_t name, color; } NarwhalDxfOwnedLayer;
typedef struct {
    int64_t kind, name, color, points, closed, true_color, label;
} NarwhalDxfOwnedEntity;
typedef struct {
    uint64_t version; /* must be 2; entity text has a separate label ID */
    const uint8_t *names;
    size_t names_len;
    const int64_t *lengths;
    size_t names_count;
    const NarwhalDxfOwnedLayer *layers;
    size_t layer_count;
    const NarwhalDxfOwnedEntity *entities;
    size_t entity_count;
    const double *nums;
    size_t nums_len;
    const uint8_t *unit;
    size_t unit_len;
    uint8_t unit_mode; /* 0 legacy/default, 1 explicit name (including unknown) */
} NarwhalDxfOwnedInput;

/* Error diagnostics: [negative return code, field/entity/scalar index, 0].
 * -1 pointer, -2 protocol, -3 limits/capacity; domain codes -40 color,
 * -41 explicit RGB, -42 type, -43 cubic chain, -44 nonfinite, -45 name framing,
 * -46 flags, -47 nonpositive radius. Failure leaves payload/counts untouched.
 */
uint32_t narwhal_pure_core_dxf_owned_abi_version(void); /* must return 2 */
int32_t narwhal_pure_core_dxf_owned_colors(const int64_t *, size_t, int64_t diagnostics[3]);
int32_t narwhal_pure_core_dxf_owned_units(const uint8_t *, size_t, uint8_t, int64_t out[2]);
int32_t narwhal_pure_core_dxf_owned_capacity(const NarwhalDxfOwnedInput *, size_t *, int64_t diagnostics[3]);
/* encoding 0: final Latin-1 file bytes. encoding 1: full original UTF-8/WTF-8
 * text for CPython UnicodeEncodeError framing ONLY; never write it as a DXF.
 * Counts/encoding published only after complete successful serialization.
 */
int32_t narwhal_pure_core_dxf_owned_encode(const NarwhalDxfOwnedInput *, uint8_t *, size_t, int64_t *, int64_t *, int64_t diagnostics[3]);
#ifdef __cplusplus
}
#endif
#endif
