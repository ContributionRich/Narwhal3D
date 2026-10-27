#!/usr/bin/env python3
"""Apply Narwhal's window POST_PIXEL Python bridge (Blender 5.2.2).

Python orchestrates source patching only; registry/lifetime/drawing glue is native C++.
apply(source) accepts source/blender, validates EVERY file before any write, and
returns changed relative paths. No build, runtime, UI, or provenance integration.

OURS API: bpy.types.Window.draw_handler_add(callback, args, 'POST_PIXEL') -> capsule;
bpy.types.Window.draw_handler_remove(handle). Both are classmethods, positional-only.
Keep the handle alive: its destructor unregisters. Removal is idempotent for a valid
Window handle, including after WM cleanup. Wrong-kind capsules raise ValueError.
Registrations are global, nonpersistent across WM destruction/file load; callers must
remove stale handles and re-register after load. No persistent callback option.

Callbacks receive only *args; bpy.context.window is the drawing window and area/region
are None. Draw-only: no operators, recursive captures, framebuffer rebinding/clearing,
or freeing the incoming shader. Matrices must remain stack-balanced. Native pixel
size comes from gpu.state.viewport_get(), NOT necessarily Window.width/height.
The pass is after native area edges/WM callbacks but BEFORE native floating menus.
It also executes for offscreen window capture/stereo composition, not once per event.
Request redraw on overlay changes/removal; registration is not a continuous timer.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "tools/blender-src/source/blender"
DRAW = "windowmanager/intern/wm_draw.cc"
CALLBACK = "python/intern/bpy_rna_callback.cc"
CAPI = "python/intern/bpy_rna_types_capi.cc"
HEADER = "python/BPY_window_draw.hh"
NATIVE_INPUTS = (DRAW, CALLBACK, CAPI, HEADER)

HEADER_TEXT = '''/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once

namespace blender {
struct bContext;
struct wmWindow;

/* Narwhal: global, nonpersistent Python overlays after editor composition. */
void BPY_callback_window_draw(bContext *C, wmWindow *win);
}  // namespace blender
'''

CALLBACK_INCLUDES = '''#include <algorithm>
#include <atomic>
#include <cstring>
#include <vector>

#include "BKE_context.hh"
#include "BLI_threads.h"
#include "MEM_guardedalloc.h"
#include "GPU_matrix.hh"
#include "GPU_shader.hh"
#include "GPU_state.hh"
#include "BPY_window_draw.hh"
'''

# Kept as one exact block so an interrupted/modified installation is rejected,
# not mistaken for a successfully applied patch just because a marker exists.
NATIVE = r'''/* Narwhal window overlay bridge: capsule owns one reference, dispatch pins records.
 * The registry deliberately does NOT own the capsule (dropping it unregisters). */
static constexpr const char *window_capsule_id = "RNA_WINDOW_DRAW_HANDLE";
struct PyWindowDrawRecord {
  PyObject *callback = nullptr;
  PyObject *args = nullptr;
  unsigned int users = 1;
  bool active = false;
};
static std::vector<PyWindowDrawRecord *> window_draw_records;
/* A capsule may be decref'd by a Python worker while the main thread has no GIL.
 * Only this count is read without the GIL; the registry itself is GIL-protected. */
static std::atomic<size_t> window_draw_count{0};
static bool window_draw_clearing = false;
static bool window_draw_dispatching = false;

static void window_draw_release(PyWindowDrawRecord *record)
{
  if (--record->users == 0) {
    MEM_delete(record);
  }
}

static void window_draw_detach(PyWindowDrawRecord *record)
{
  if (record->active) {
    record->active = false;
    auto &records = window_draw_records;
    records.erase(std::remove(records.begin(), records.end(), record), records.end());
    window_draw_count.store(records.size());
  }
  /* Py_CLEAR may run destructors which remove other handles or re-enter cleanup. */
  Py_CLEAR(record->callback);
  Py_CLEAR(record->args);
}

static void window_draw_capsule_destructor(PyObject *capsule)
{
  auto *record = static_cast<PyWindowDrawRecord *>(
      PyCapsule_GetPointer(capsule, window_capsule_id));
  if (record == nullptr) {
    PyErr_Clear();
    return;
  }
  window_draw_detach(record);
  window_draw_release(record);
}

static PyObject *window_draw_add(PyObject *args)
{
  PyObject *cls, *callback, *callback_args;
  const char *stage;
  if (!PyArg_ParseTuple(args, "OOO!s:Window.draw_handler_add",
                        &cls, &callback, &PyTuple_Type, &callback_args, &stage))
  {
    return nullptr;
  }
  if (!PyCallable_Check(callback)) {
    PyErr_SetString(PyExc_TypeError, "callback must be callable");
    return nullptr;
  }
  if (std::strcmp(stage, "POST_PIXEL") != 0) {
    PyErr_SetString(PyExc_ValueError, "Window draw_type must be 'POST_PIXEL'");
    return nullptr;
  }
  if (!BLI_thread_is_main() || window_draw_clearing || Py_IsFinalizing()) {
    PyErr_SetString(PyExc_RuntimeError, "Window handlers require the main thread outside cleanup");
    return nullptr;
  }
  auto *record = MEM_new<PyWindowDrawRecord>(__func__);
  PyObject *capsule = PyCapsule_New(record, window_capsule_id, window_draw_capsule_destructor);
  if (capsule == nullptr) {
    MEM_delete(record);
    return nullptr;
  }
  record->callback = Py_NewRef(callback);
  record->args = Py_NewRef(callback_args);
  record->active = true;
  window_draw_records.push_back(record);
  window_draw_count.store(window_draw_records.size());
  return capsule;
}

static PyObject *window_draw_remove(PyObject *args)
{
  PyObject *cls, *capsule;
  if (!PyArg_ParseTuple(args, "OO:Window.draw_handler_remove", &cls, &capsule)) {
    return nullptr;
  }
  if (!BLI_thread_is_main()) {
    PyErr_SetString(PyExc_RuntimeError, "Window handlers require the main thread");
    return nullptr;
  }
  auto *record = static_cast<PyWindowDrawRecord *>(
      PyCapsule_GetPointer(capsule, window_capsule_id));
  if (record == nullptr) {
    return nullptr;
  }
  window_draw_detach(record);
  Py_RETURN_NONE;
}

static void window_draw_clear()
{
  if (window_draw_clearing || window_draw_records.empty()) {
    return;
  }
  window_draw_clearing = true;
  std::vector<PyWindowDrawRecord *> records;
  records.swap(window_draw_records);
  window_draw_count.store(0);
  /* Invalidate and pin ALL records before releasing any Python references. */
  for (auto *record : records) {
    ++record->users;
    record->active = false;
  }
  for (auto *record : records) {
    window_draw_detach(record);
    window_draw_release(record);
  }
  window_draw_clearing = false;
}

/* The composition-stage contract has scissor testing disabled on exit. There is
 * no public GPU scissor-enable getter; do not pretend to snapshot that state.
 * Other state below is saved per callback, including the incoming bound shader.
 * The current framebuffer is intentionally NEVER rebound (offscreen capture). */
class WindowDrawGPUState {
  GPUBlend blend_ = GPU_blend_get();
  GPUDepthTest depth_ = GPU_depth_test_get();
  GPUWriteMask mask_ = GPU_write_mask_get();
  GPUFaceCullTest cull_ = GPU_face_culling_get();
  GPUStencilTest stencil_ = GPU_stencil_test_get();
  gpu::Shader *shader_ = GPU_shader_get_bound();
  int viewport_[4], scissor_[4];

 public:
  explicit WindowDrawGPUState(wmWindow *win)
  {
    GPU_viewport_size_get_i(viewport_);
    GPU_scissor_get(scissor_);
    GPU_matrix_push();
    GPU_matrix_push_projection();
    wmWindowViewport_ex(win, 0.0f);
    GPU_scissor_test(false);
    GPU_shader_unbind();
    GPU_blend(GPU_BLEND_ALPHA);
    GPU_depth_test(GPU_DEPTH_NONE);
    GPU_write_mask(GPU_WRITE_COLOR);
    GPU_face_culling(GPU_CULL_NONE);
    GPU_stencil_test(GPU_STENCIL_NONE);
  }
  ~WindowDrawGPUState()
  {
    GPU_shader_unbind();
    GPU_matrix_pop_projection();
    GPU_matrix_pop();
    GPU_viewport(viewport_[0], viewport_[1], viewport_[2], viewport_[3]);
    GPU_scissor(scissor_[0], scissor_[1], scissor_[2], scissor_[3]);
    GPU_scissor_test(false);
    GPU_blend(blend_);
    GPU_depth_test(depth_);
    GPU_write_mask(mask_);
    GPU_face_culling(cull_);
    GPU_stencil_test(stencil_);
    if (shader_ != nullptr) {
      GPU_shader_bind(shader_);
    }
  }
};

void BPY_callback_window_draw(bContext *C, wmWindow *win)
{
  /* Main-thread registry: the empty fast path does not acquire the GIL. Never
   * enter Python during/after interpreter teardown or recursively via capture. */
  if (!BLI_thread_is_main() || !Py_IsInitialized() || Py_IsFinalizing() ||
      window_draw_count.load() == 0 || window_draw_dispatching)
  {
    return;
  }
  window_draw_dispatching = true;
  PyGILState_STATE gilstate;
  bpy_context_set(C, &gilstate);

  /* Hide both kinds of temporary override BEFORE reading the real C context.
   * Restore them last, so setters cannot mutate the caller's Python override. */
  bContext_PyState py_state;
  CTX_py_state_push(C, &py_state, nullptr);
  const bContextStore *store = CTX_store_get(C);
  CTX_store_set(C, nullptr);
  wmWindow *old_window = CTX_wm_window(C);
  bScreen *old_screen = CTX_wm_screen(C);
  ScrArea *old_area = CTX_wm_area(C);
  ARegion *old_region = CTX_wm_region(C);
  Scene *old_scene = CTX_data_scene(C);
  CTX_wm_window_set(C, win);
  CTX_wm_area_set(C, nullptr);
  CTX_wm_region_set(C, nullptr);

  /* Newly registered callbacks wait for the next composition. A snapshot pins
   * records, not capsules: dropping a pending capsule still cancels its call. */
  const auto records = window_draw_records;
  for (auto *record : records) {
    ++record->users;
  }
  for (auto *record : records) {
    if (record->active) {
      WindowDrawGPUState gpu_state(win);
      /* Self-removal/WM cleanup may clear the record while the callable runs. */
      PyObject *callback = Py_NewRef(record->callback);
      PyObject *args = Py_NewRef(record->args);
      PyObject *result = PyObject_CallObject(callback, args);
      Py_DECREF(args);
      Py_DECREF(callback);
      if (result != nullptr) {
        Py_DECREF(result);
      }
      else {
        /* PyErr_Print treats SystemExit as process termination; draw errors must
         * instead remain diagnostics and leave later callbacks/native menus alive. */
        if (PyErr_ExceptionMatches(PyExc_SystemExit)) {
          PyErr_SetString(PyExc_RuntimeError, "SystemExit in Window draw callback");
        }
        PyErr_Print();
      }
    }
    window_draw_release(record);
  }

  CTX_wm_window_set(C, old_window);
  CTX_wm_screen_set(C, old_screen);
  CTX_data_scene_set(C, old_scene);
  CTX_wm_area_set(C, old_area);
  CTX_wm_region_set(C, old_region);
  CTX_store_set(C, store);
  CTX_py_state_pop(C, &py_state);
  window_draw_dispatching = false;
  bpy_context_clear(C, &gilstate);
}

'''

WINDOW_METHODS = '''PyDoc_STRVAR(
    pyrna_window_draw_add_doc,
    ".. classmethod:: draw_handler_add(callback, args, draw_type, /)\\n"
    "\\n"
    "   Register a global window overlay; only 'POST_PIXEL' is supported.\\n"
    "   Calls callback(*args) after editor edges, before native floating menus.\\n"
    "   args must be a tuple. Context window is the drawing window; area/region are None.\\n"
    "   Coordinates are native pixels; use gpu.state.viewport_get() for dimensions.\\n"
    "   Keep the returned capsule alive: its destruction unregisters the callback.\\n"
    "   Nonpersistent: WM destruction/file load clears registrations; re-register after load.\\n"
    "   Draw-only: do not rebind/clear framebuffers or invoke operators/capture.\\n"
    "   Keep matrix stacks balanced and preserve resources owned by other callbacks.\\n"
    "   May run repeatedly for stereo/capture. Request redraw when overlay state changes.\\n");
PyDoc_STRVAR(
    pyrna_window_draw_remove_doc,
    ".. classmethod:: draw_handler_remove(handler, /)\\n"
    "\\n"
    "   Detach a Window overlay handle; repeated removal/WM-cleared handles are harmless.\\n"
    "   Other capsule kinds are rejected. Main thread only. Request redraw after removal.\\n");

static PyMethodDef pyrna_window_methods[] = {
    {nullptr, nullptr, 0, nullptr}, /* #BPY_rna_window_screenshot_method_def */
    {"draw_handler_add",
     static_cast<PyCFunction>(pyrna_callback_classmethod_add),
     METH_VARARGS | METH_CLASS,
     pyrna_window_draw_add_doc},
    {"draw_handler_remove",
     static_cast<PyCFunction>(pyrna_callback_classmethod_remove),
     METH_VARARGS | METH_CLASS,
     pyrna_window_draw_remove_doc},
    {nullptr, nullptr, 0, nullptr},
};
'''

EDITS = (
    (DRAW, '#include "WM_api.hh"\n',
     '#ifdef WITH_PYTHON\n#  include "BPY_window_draw.hh"\n#endif\n\n#include "WM_api.hh"\n'),
    (DRAW, '  wm_draw_callbacks(win);\n  wmWindowViewport(win);\n',
     '  wm_draw_callbacks(win);\n#ifdef WITH_PYTHON\n  BPY_callback_window_draw(C, win);\n#endif\n  wmWindowViewport(win);\n'),
    (CALLBACK, '#include <Python.h>\n', '#include <Python.h>\n\n' + CALLBACK_INCLUDES),
    (CALLBACK, 'static const char *rna_capsule_id_invalid = "RNA_HANDLE_REMOVED";\n\n',
     'static const char *rna_capsule_id_invalid = "RNA_HANDLE_REMOVED";\n\n' + NATIVE),
    (CALLBACK, '  /* class specific callbacks */\n',
     '  /* class specific callbacks */\n\n  if (srna == RNA_Window) {\n    return window_draw_add(args);\n  }\n'),
    (CALLBACK, '  py_handle = PyTuple_GET_ITEM(args, 1);\n  handle = PyCapsule_GetPointer(py_handle, rna_capsule_id);\n',
     '  if (srna == RNA_Window) {\n    return window_draw_remove(args);\n  }\n  py_handle = PyTuple_GET_ITEM(args, 1);\n  handle = PyCapsule_GetPointer(py_handle, rna_capsule_id);\n'),
    (CALLBACK, 'void BPY_callback_wm_free(wmWindowManager *wm)\n{\n',
     'void BPY_callback_wm_free(wmWindowManager *wm)\n{\n'
     '  if (Py_IsInitialized() && !Py_IsFinalizing()) {\n'
     '    PyGILState_STATE gilstate = PyGILState_Ensure();\n'
     '    window_draw_clear();\n'
     '    PyGILState_Release(gilstate);\n'
     '  }\n'),
    (CAPI, '''static PyMethodDef pyrna_window_methods[] = {
    {nullptr, nullptr, 0, nullptr}, /* #BPY_rna_window_screenshot_method_def */
    {nullptr, nullptr, 0, nullptr},
};
''', WINDOW_METHODS),
    (CAPI, '  ARRAY_SET_ITEMS(pyrna_window_methods, BPY_rna_window_screenshot_method_def);\n'
     '  BLI_STATIC_ASSERT(ARRAY_SIZE(pyrna_window_methods) == 2, "Unexpected number of methods")\n',
     '  pyrna_window_methods[0] = BPY_rna_window_screenshot_method_def;\n'
     '  BLI_STATIC_ASSERT(ARRAY_SIZE(pyrna_window_methods) == 4, "Unexpected number of methods")\n'),
)


def apply(source: str | os.PathLike[str] | None = None) -> list[str]:
    """Validate the complete four-path plan before writing any file.

    Anchor/header mismatches never write partial output. This is not a filesystem
    transaction against I/O failure or concurrent modification: apply while no
    other process is editing these four native inputs.
    """
    root = Path(source) if source is not None else SRC
    originals = {name: (root / name).read_text(encoding="utf-8")
                 for name in NATIVE_INPUTS if name != HEADER}
    texts = dict(originals)
    markers = {DRAW: "BPY_window_draw.hh", CALLBACK: "window_draw_records",
               CAPI: "pyrna_window_draw_add_doc"}
    for name, original in originals.items():
        edits = [(old, new) for file, old, new in EDITS if file == name]
        installed = markers[name] in original or any(new in original for _, new in edits)
        for old, new in edits:
            text = texts[name]
            if installed:
                valid = text.count(new) == 1 and text.count(old) == new.count(old)
            else:
                valid = text.count(old) == 1
            if not valid:
                raise RuntimeError(f"window-overlay patch anchor mismatch: {name}: {old.splitlines()[0]}")
            if not installed:
                texts[name] = text.replace(old, new, 1)
    header = root / HEADER
    if not header.parent.is_dir():
        raise RuntimeError(f"window-overlay header parent missing: {header.parent}")
    if header.exists() and header.read_text(encoding="utf-8") != HEADER_TEXT:
        raise RuntimeError(f"window-overlay header mismatch: {HEADER}")
    texts[HEADER] = HEADER_TEXT
    changed = [name for name in NATIVE_INPUTS
               if (name == HEADER and not header.exists())
               or (name != HEADER and texts[name] != originals[name])]
    for name in changed:
        (root / name).write_text(texts[name], encoding="utf-8")
    return changed


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Blender source/blender directory")
    args = parser.parse_args(argv)
    for name in apply(args.source):
        print(f"patched: {name}")


if __name__ == "__main__":
    main()
