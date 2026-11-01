#ifndef NARWHAL_APPLICATION_CORE_H
#define NARWHAL_APPLICATION_CORE_H
#include <stddef.h>
#include <stdint.h>
/* ptrdiff_t is the C counterpart of Rust isize on supported platforms. */
#ifdef __cplusplus
extern "C" {
#endif
uint32_t narwhal_session_abi(void); /* 1 */
/* UTF-8 JSON, no terminating NUL. No ownership/pointers retained by the library.
 * input: length readable bytes; output: capacity writable bytes, both caller-owned.
 * Callers must prevent concurrent buffer access; input/output may overlap.
 * Normal callers MUST supply capacity >=4096 and <=512 MiB. Input <=128 MiB.
 *
 * >=0: bytes written, or required bytes (>capacity) with NO copy. Only READ
 * replies can require a larger valid (>=4096) buffer: retry that read, not a
 * mutation. Even close/snapshot/prepare/release/abort are mutations.
 * capacity <4096: no request execution; output_too_small JSON if it fits, otherwise
 * its required length and no copy. NULL input: null_input JSON if output is valid.
 * -1: NULL output or capacity >512 MiB; no dereference/mutation. No JSON can be
 * safely delivered for these invalid output descriptors.
 * Every other error: {"status":"error","code":"..."} (including caught panics).
 * Read-only {"op":"document_user_text_count","session":s} returns
 * {"status":"ok","count":u64}: rows with ASCII backslash in the key, not all rows.
 * This ABI cannot detect dangling pointers. See README.md for exact protocol.
 */
ptrdiff_t narwhal_session_json(const uint8_t *input, size_t length,
                               uint8_t *output, size_t capacity);
/* Persistent history ABI1, separate from and additive to metadata ABI1.
 * Same descriptors/preflight as above, but history input is limited to 1 MiB.
 * Exact requests, result/header fields and ownership: ../HISTORY.md.
 */
uint32_t narwhal_history_abi(void); /* 1 */
ptrdiff_t narwhal_history_json(const uint8_t *input, size_t length,
                              uint8_t *output, size_t capacity);
/* Object registry ABI1: additive, no second Undo history. Input <=32 MiB.
 * Same buffer/minimum-output/no-mutation-retry contract as session ABI1 above.
 * Geometry remains host-owned. See ../OBJECT_REGISTRY.md for scope and states.
 */
uint32_t narwhal_object_registry_abi(void); /* 1 */
ptrdiff_t narwhal_object_registry_json(const uint8_t *input, size_t length,
                                      uint8_t *output, size_t capacity);
/* Additive observation boundary: header is strict JSON prepare/check/commit;
 * observation is a bounded NWOBS01 binary batch. Every operation may mutate
 * (check invalidates on mismatch), replies fit4096 bytes, NEVER retry unknown
 * outcomes. Header <=32 MiB, observation <=128 MiB. Same pointer contract above.
 */
uint32_t narwhal_object_observation_abi(void); /* 1 */
ptrdiff_t narwhal_object_registry_observe(const uint8_t *header, size_t header_length,
                                         const uint8_t *observation, size_t observation_length,
                                         uint8_t *output, size_t capacity);
#ifdef __cplusplus
}
#endif
#endif
