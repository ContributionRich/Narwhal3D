/* GPL-3.0-or-later. Development ABI 1; see README.md for safety/lifetime rules. */
#ifndef NARWHAL_UI_CORE_H
#define NARWHAL_UI_CORE_H
#include <stdint.h>
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif

typedef struct NarwhalEditorState {
    uint64_t handle, revision, text_revision, length, caret;
    int64_t anchor, max_chars;
    uint64_t flags, change_start, change_end, inserted;
} NarwhalEditorState;

uint32_t narwhal_editor_abi(void);
uint32_t narwhal_editor_edit(uint64_t handle, uint32_t op, int64_t value,
    uint32_t flags, const uint32_t *input, size_t length,
    NarwhalEditorState *output, size_t output_bytes);
uint32_t narwhal_editor_read(uint64_t handle, uint64_t text_revision,
    size_t start, uint32_t *output, size_t count);
uint32_t narwhal_editor_close(uint64_t handle);

/* Independent, additive popup ABI 1. The editor ABI/State88 above is unchanged.
 * Buffers are caller-owned, valid/aligned and nonaliasing. See POPUP.md. */
typedef struct NarwhalPopupRow {
    uint64_t child_start, child_count, separator;
} NarwhalPopupRow;
typedef struct NarwhalPopupRequest {
    uint32_t op, value;
    int64_t index;
    double x, y, scale;
    uint64_t source, root_count, title;
} NarwhalPopupRequest;
typedef struct NarwhalPopupReply {
    uint64_t handle, revision, layout_revision, panels;
    int64_t action;
    uint64_t consumed;
} NarwhalPopupReply;
typedef struct NarwhalPopupPanel {
    uint64_t start, count;
    int64_t parent, selected;
    double rect[4], body[4], up[4], down[4];
    double offset, max_scroll, width;
    uint64_t scrolling;
} NarwhalPopupPanel;
/* ids -1/-2 request root/empty-submenu title. Nonnegative ids identify rows;
 * kind 0=label, 1=shortcut. Return 0 and a finite whole-string width, else error.
 * Called synchronously, exactly once per request, outside the registry lock. */
typedef uint32_t (*NarwhalPopupMeasure)(int64_t id, uint32_t kind,
    double size, double *width);
uint32_t narwhal_popup_abi(void);
uint32_t narwhal_popup_apply(uint64_t handle, const NarwhalPopupRequest *request,
    size_t request_bytes, const uint8_t *key, size_t key_len,
    const NarwhalPopupRow *rows, size_t row_count, NarwhalPopupMeasure measure,
    NarwhalPopupReply *output, size_t output_bytes);
uint32_t narwhal_popup_destroy(uint64_t handle);
uint32_t narwhal_popup_panel(uint64_t handle, uint64_t revision, size_t depth,
    NarwhalPopupPanel *output, size_t output_bytes);
uint32_t narwhal_popup_rows(uint64_t handle, uint64_t revision, size_t depth,
    size_t start, double (*output)[4], size_t count);
/* hit depth -1=outside; part -1=border, -2=up, -3=down, otherwise row index. */
uint32_t narwhal_popup_hit(uint64_t handle, uint64_t revision, double x, double y,
    int64_t *depth, int64_t *part);
/* Pure read of sorted NUL-terminated key names; capacity 512*65 always suffices. */
uint32_t narwhal_popup_guards(uint64_t handle, uint64_t revision, uint8_t *output,
    size_t capacity, size_t *written);

#ifdef __cplusplus
}
#endif
#endif
