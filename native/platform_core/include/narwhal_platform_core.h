/* SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef NARWHAL_PLATFORM_CORE_H
#define NARWHAL_PLATFORM_CORE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum { NARWHAL_PLATFORM_CORE_ABI_VERSION = 1 };

enum NarwhalPlatformStatus {
  NARWHAL_PLATFORM_OK = 0,
  NARWHAL_PLATFORM_CANCEL = 1,
  NARWHAL_PLATFORM_UNAVAILABLE = 2,
  NARWHAL_PLATFORM_ERROR = 3
};

enum NarwhalPlatformError {
  NARWHAL_PLATFORM_ERROR_NONE = 0,
  NARWHAL_PLATFORM_ERROR_INVALID_ARGUMENT = 1,
  NARWHAL_PLATFORM_ERROR_LIMIT_EXCEEDED = 2,
  NARWHAL_PLATFORM_ERROR_INVALID_UTF8 = 3,
  NARWHAL_PLATFORM_ERROR_EMBEDDED_NUL = 4,
  NARWHAL_PLATFORM_ERROR_INVALID_OWNER = 5,
  NARWHAL_PLATFORM_ERROR_SYSTEM = 6,
  NARWHAL_PLATFORM_ERROR_INVALID_TOKEN = 7,
  NARWHAL_PLATFORM_ERROR_PENDING_FULL = 8,
  NARWHAL_PLATFORM_ERROR_INVALID_PATH_ENCODING = 9,
  NARWHAL_PLATFORM_ERROR_PANIC = 10
};

enum NarwhalFileDialogKind {
  NARWHAL_FILE_DIALOG_OPEN = 1,
  NARWHAL_FILE_DIALOG_SAVE = 2
};

enum NarwhalMessageBoxChoice {
  NARWHAL_MESSAGE_CHOICE_NONE = 0,
  NARWHAL_MESSAGE_CHOICE_YES = 1,
  NARWHAL_MESSAGE_CHOICE_NO = 2,
  NARWHAL_MESSAGE_CHOICE_CANCEL = 3
};

enum {
  NARWHAL_PLATFORM_MAX_TITLE_BYTES = 4 * 1024,
  NARWHAL_PLATFORM_MAX_MESSAGE_BYTES = 64 * 1024,
  NARWHAL_PLATFORM_MAX_START_PATH_BYTES = 128 * 1024,
  NARWHAL_PLATFORM_MAX_FILTERS = 64,
  NARWHAL_PLATFORM_MAX_FILTER_FIELD_BYTES = 4 * 1024,
  NARWHAL_PLATFORM_MAX_OUTPUT_BYTES = 512 * 1024
};

typedef struct NarwhalUtf8Slice {
  const uint8_t *data;
  size_t length;
} NarwhalUtf8Slice;

typedef struct NarwhalFilterPairV1 {
  uint32_t struct_size;
  uint32_t abi_version;
  NarwhalUtf8Slice description;
  NarwhalUtf8Slice glob;
} NarwhalFilterPairV1;

typedef struct NarwhalFileDialogRequestV1 {
  uint32_t struct_size;
  uint32_t abi_version;
  uint32_t dialog_kind;
  uint32_t reserved;
  uintptr_t owner_window;
  NarwhalUtf8Slice title;
  NarwhalUtf8Slice start_path;
  const NarwhalFilterPairV1 *filters;
  size_t filter_count;
  uint8_t *output;
  size_t output_capacity;
} NarwhalFileDialogRequestV1;

typedef struct NarwhalDialogResultV1 {
  uint32_t struct_size;
  uint32_t abi_version;
  int32_t status;
  uint32_t error_code;
  uint32_t system_error;
  uint32_t selected_filter_index;
  size_t required_capacity;
  size_t written_length;
  uint64_t retry_token;
} NarwhalDialogResultV1;

typedef struct NarwhalMessageBoxRequestV1 {
  uint32_t struct_size;
  uint32_t abi_version;
  uint32_t reserved0;
  uint32_t reserved1;
  uintptr_t owner_window;
  NarwhalUtf8Slice title;
  NarwhalUtf8Slice message;
} NarwhalMessageBoxRequestV1;

typedef struct NarwhalMessageBoxResultV1 {
  uint32_t struct_size;
  uint32_t abi_version;
  int32_t status;
  uint32_t choice;
  uint32_t error_code;
  uint32_t system_error;
} NarwhalMessageBoxResultV1;

uint32_t narwhal_platform_core_abi_version(void);

/*
 * Shows one Explorer-style native picker on Windows. Inputs are strict,
 * length-delimited UTF-8 and need not be NUL terminated. `owner_window` is zero
 * or an HWND value that must pass IsWindow. `output` is caller-owned UTF-8.
 *
 * On OK, required_capacity includes the trailing NUL and written_length does
 * not. If output_capacity is too small (including the ordinary NULL/0 first
 * pass), status remains OK and retry_token is nonzero. Copy that retained path
 * with narwhal_platform_dialog_result_copy_v1; the picker is not opened again.
 */
int32_t narwhal_platform_file_dialog_v1(
    const NarwhalFileDialogRequestV1 *request,
    NarwhalDialogResultV1 *result);

/*
 * Copies a retained selected path. A NULL/0 query reports required_capacity
 * while retaining the token. A successful copy consumes the token. An
 * undersized non-null destination also retains it. At most 32 results may be
 * retained concurrently; callers must copy or discard every nonzero token.
 */
int32_t narwhal_platform_dialog_result_copy_v1(
    uint64_t token, uint8_t *output, size_t capacity,
    NarwhalDialogResultV1 *result);
int32_t narwhal_platform_dialog_result_discard_v1(uint64_t token);

/* Shows MessageBoxW with Yes, No and Cancel buttons on Windows. */
int32_t narwhal_platform_message_box_v1(
    const NarwhalMessageBoxRequestV1 *request,
    NarwhalMessageBoxResultV1 *result);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* NARWHAL_PLATFORM_CORE_H */
