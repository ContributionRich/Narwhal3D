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
#ifdef __cplusplus
}
#endif
#endif
