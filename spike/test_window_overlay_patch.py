#!/usr/bin/env python3
"""Source-fixture and CPU lifecycle tests, NOT Blender/GPU validation.

Independent upstream excerpts check patch anchors; when available, actual native
inputs are copied read-only to a temporary tree and patched there. The C++ payload
is also compiled as a tiny CPython extension with mocked context/GPU calls. No
Blender build, GUI, installed runtime edits, harness, or provenance changes.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "window_overlay_patch", ROOT / "blender_patches/apply_window_overlay.py")
assert spec and spec.loader
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)

# Independent excerpts from the inspected Blender source, not patch.EDITS.
DRAW = '''#include "WM_api.hh"
namespace blender {
void wm_draw_window_onscreen(bContext *C, wmWindow *win, int view)
{
  /* After area regions so we can do area 'overlay' drawing. */
  ui::theme::theme_set(0, 0);
  ED_screen_draw_edges(win);

  /* Needs zero offset here or it looks blurry. #128112. */
  wmWindowViewport_ex(win, 0.0f);

  wm_draw_callbacks(win);
  wmWindowViewport(win);

  /* Blend in floating regions (menus). */
  for (ARegion &region : screen->regionbase) {
    if (!region.runtime->visible) {
      continue;
    }
    wm_draw_region_blend(&region, 0, true);
  }
}
}
'''
CALLBACK = '''#include <Python.h>
namespace blender {
static const char *rna_capsule_id = "RNA_HANDLE";
static const char *rna_capsule_id_invalid = "RNA_HANDLE_REMOVED";

PyObject *pyrna_callback_classmethod_add(PyObject *self, PyObject *args)
{
  /* class specific callbacks */
  if (srna == RNA_WindowManager) {
    handle = WM_paint_cursor_activate(space, region, nullptr, cb_wm_cursor_draw, args);
  }
  else if (RNA_struct_is_a(srna, RNA_Space)) {
    handle = ED_region_draw_cb_activate(art, cb_region_draw, args, event);
  }
}
PyObject *pyrna_callback_classmethod_remove(PyObject *self, PyObject *args)
{
  py_handle = PyTuple_GET_ITEM(args, 1);
  handle = PyCapsule_GetPointer(py_handle, rna_capsule_id);
  if (handle == nullptr) {
    return nullptr;
  }
}
void BPY_callback_wm_free(wmWindowManager *wm)
{
  WM_paint_cursor_remove_by_type(
      wm, reinterpret_cast<void *>(cb_wm_cursor_draw), cb_customdata_free);
}
}
'''
CAPI = '''static PyMethodDef pyrna_window_methods[] = {
    {nullptr, nullptr, 0, nullptr}, /* #BPY_rna_window_screenshot_method_def */
    {nullptr, nullptr, 0, nullptr},
};
void init()
{
  /* Window */
  ARRAY_SET_ITEMS(pyrna_window_methods, BPY_rna_window_screenshot_method_def);
  BLI_STATIC_ASSERT(ARRAY_SIZE(pyrna_window_methods) == 2, "Unexpected number of methods")
  pyrna_struct_type_extend_capi(RNA_Window, pyrna_window_methods, nullptr);
}
'''
FIXTURES = {patch.DRAW: DRAW, patch.CALLBACK: CALLBACK, patch.CAPI: CAPI}


def fixture(root: Path, texts: dict[str, str] | None = None) -> None:
    for name, text in (FIXTURES if texts is None else texts).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def assert_glue(texts: dict[str, str]) -> None:
    draw = texts[patch.DRAW]
    assert (draw.index("ED_screen_draw_edges(win)")
            < draw.index("wm_draw_callbacks(win)")
            < draw.index("BPY_callback_window_draw(C, win)")
            < draw.index("/* Blend in floating regions (menus). */"))
    assert "#ifdef WITH_PYTHON\n  BPY_callback_window_draw(C, win);\n#endif" in draw
    cb = texts[patch.CALLBACK]
    cleanup = cb[cb.index("void BPY_callback_wm_free("):]
    assert "window_draw_clear();" in cleanup
    assert cleanup.index("PyGILState_Ensure()") < cleanup.index("window_draw_clear();")
    assert cleanup.index("window_draw_clear();") < cleanup.index("PyGILState_Release(gilstate)")
    assert "return window_draw_add(args);" in cb
    remove = cb[cb.index("PyObject *pyrna_callback_classmethod_remove("):]
    assert remove.index("return window_draw_remove(args);") < remove.index("rna_capsule_id)")
    capi = texts[patch.CAPI]
    assert capi.count("METH_VARARGS | METH_CLASS") >= 2
    assert 'ARRAY_SIZE(pyrna_window_methods) == 4' in capi
    assert 'pyrna_window_methods[0] = BPY_rna_window_screenshot_method_def;' in capi
    assert 'ARRAY_SET_ITEMS(pyrna_window_methods' not in capi
    assert texts[patch.HEADER].count("void BPY_callback_window_draw(") == 1
    assert "Python.h" not in texts[patch.HEADER]


# Real CPython reference counting/capsules/exceptions; mock only Blender and GPU.
# The state model is deliberately non-default on entry, with stale context
# overrides, so omissions cannot accidentally pass by restoring zero to zero.
STUBS = r'''
#include <Python.h>
#include <algorithm>
#include <atomic>
#include <cassert>
#include <cstring>
#include <vector>
#include <thread>

static const auto main_thread = std::this_thread::get_id();
static bool BLI_thread_is_main() { return std::this_thread::get_id() == main_thread; }
static int allocations = 0;
template<class T> T *MEM_new(const char *) { ++allocations; return new T; }
template<class T> void MEM_delete(T *p) { --allocations; delete p; }
struct Scene {};
struct bScreen {};
struct ScrArea {};
struct ARegion {};
struct bContextStore {};
struct wmWindow { Scene scene; bScreen screen; int width = 1600, height = 900; };
struct bContext_PyState { void *a; void *b; };
struct bContext {
  wmWindow *win; bScreen *screen; Scene *scene; ScrArea *area; ARegion *region;
  void *py; void *orig; const bContextStore *store;
};
static int gil_sets = 0, gil_clears = 0;
static void bpy_context_set(bContext *, PyGILState_STATE *g) { ++gil_sets; *g=PyGILState_Ensure(); }
static void bpy_context_clear(bContext *, PyGILState_STATE *g) { ++gil_clears; PyGILState_Release(*g); }
static void CTX_py_state_push(bContext *c, bContext_PyState *s, void *v) {
  s->a=c->py; s->b=c->orig; c->py=c->orig=v;
}
static void CTX_py_state_pop(bContext *c, bContext_PyState *s) { c->py=s->a; c->orig=s->b; }
static const bContextStore *CTX_store_get(bContext *c) { return c->store; }
static void CTX_store_set(bContext *c, const bContextStore *v) { c->store=v; }
static wmWindow *CTX_wm_window(bContext *c) { return c->win; }
static bScreen *CTX_wm_screen(bContext *c) { return c->screen; }
static ScrArea *CTX_wm_area(bContext *c) { return c->area; }
static ARegion *CTX_wm_region(bContext *c) { return c->region; }
static Scene *CTX_data_scene(bContext *c) { return c->scene; }
static void CTX_wm_window_set(bContext *c, wmWindow *w) {
  c->win=w; c->scene=&w->scene; c->screen=&w->screen; c->area=nullptr; c->region=nullptr;
}
static void CTX_wm_screen_set(bContext *c, bScreen *s) { c->screen=s; c->area=nullptr; c->region=nullptr; }
static void CTX_wm_area_set(bContext *c, ScrArea *a) { c->area=a; c->region=nullptr; }
static void CTX_wm_region_set(bContext *c, ARegion *r) { c->region=r; }
static void CTX_data_scene_set(bContext *c, Scene *s) { c->scene=s; }
using GPUBlend=int; using GPUDepthTest=int; using GPUWriteMask=int;
using GPUFaceCullTest=int; using GPUStencilTest=int;
constexpr int GPU_BLEND_ALPHA=1, GPU_DEPTH_NONE=0, GPU_WRITE_COLOR=15;
constexpr int GPU_CULL_NONE=0, GPU_STENCIL_NONE=0;
namespace gpu { struct Shader {}; }
static gpu::Shader old_shader, other_shader;
struct GPUState {
  int blend=7, depth=2, mask=31, cull=2, stencil=3;
  gpu::Shader *shader=&old_shader;
  int viewport[4]={3,4,640,480}, scissor[4]={5,6,700,500};
  bool scissor_test=false;
  int matrix=91, projection=92;
};
static GPUState gpu_state;
static std::vector<int> matrices, projections;
static GPUBlend GPU_blend_get() { return gpu_state.blend; }
static GPUDepthTest GPU_depth_test_get() { return gpu_state.depth; }
static GPUWriteMask GPU_write_mask_get() { return gpu_state.mask; }
static GPUFaceCullTest GPU_face_culling_get() { return gpu_state.cull; }
static GPUStencilTest GPU_stencil_test_get() { return gpu_state.stencil; }
static gpu::Shader *GPU_shader_get_bound() { return gpu_state.shader; }
static void GPU_blend(int v) { gpu_state.blend=v; }
static void GPU_depth_test(int v) { gpu_state.depth=v; }
static void GPU_write_mask(int v) { gpu_state.mask=v; }
static void GPU_face_culling(int v) { gpu_state.cull=v; }
static void GPU_stencil_test(int v) { gpu_state.stencil=v; }
static void GPU_shader_unbind() { gpu_state.shader=nullptr; }
static void GPU_shader_bind(gpu::Shader *s) { gpu_state.shader=s; }
static void GPU_viewport_size_get_i(int *v) { std::copy_n(gpu_state.viewport,4,v); }
static void GPU_scissor_get(int *v) { std::copy_n(gpu_state.scissor,4,v); }
static void GPU_viewport(int x,int y,int w,int h) {
  int v[]={x,y,w,h}; std::copy_n(v,4,gpu_state.viewport);
}
static void GPU_scissor(int x,int y,int w,int h) {
  int v[]={x,y,w,h}; std::copy_n(v,4,gpu_state.scissor);
}
static void GPU_scissor_test(bool v) { gpu_state.scissor_test=v; }
static void GPU_matrix_push() { matrices.push_back(gpu_state.matrix); }
static void GPU_matrix_push_projection() { projections.push_back(gpu_state.projection); }
static void GPU_matrix_pop() { gpu_state.matrix=matrices.back(); matrices.pop_back(); }
static void GPU_matrix_pop_projection() { gpu_state.projection=projections.back(); projections.pop_back(); }
static void wmWindowViewport_ex(wmWindow *w, float offset) {
  assert(offset==0); GPU_viewport(0,0,w->width,w->height); GPU_scissor(0,0,w->width,w->height);
  gpu_state.matrix=0; gpu_state.projection=1;
}
'''

HARNESS = r'''
static wmWindow old_window, drawing_window;
static ScrArea old_area; static ARegion old_region;
static Scene old_scene; static bScreen old_screen; static bContextStore old_store;
static int py_override;
static bContext ctx{&old_window,&old_screen,&old_scene,&old_area,&old_region,
                    &py_override,&old_store,&old_store};
static PyObject *fixture_draw(PyObject *, PyObject *) {
  const GPUState before=gpu_state;
  const bContext previous=ctx;
  BPY_callback_window_draw(&ctx, &drawing_window);
  const bool same = gpu_state.blend==before.blend && gpu_state.depth==before.depth &&
    gpu_state.mask==before.mask && gpu_state.cull==before.cull && gpu_state.stencil==before.stencil &&
    gpu_state.shader==before.shader && gpu_state.scissor_test==before.scissor_test &&
    gpu_state.matrix==before.matrix && gpu_state.projection==before.projection &&
    std::equal(before.viewport,before.viewport+4,gpu_state.viewport) &&
    std::equal(before.scissor,before.scissor+4,gpu_state.scissor) &&
    ctx.win==previous.win && ctx.screen==previous.screen && ctx.scene==previous.scene &&
    ctx.area==previous.area && ctx.region==previous.region && ctx.py==previous.py &&
    ctx.orig==previous.orig && ctx.store==previous.store;
  return PyBool_FromLong(same);
}
static PyObject *fixture_baseline(PyObject *, PyObject *) {
  const bool ok=ctx.win==&drawing_window && ctx.screen==&drawing_window.screen &&
    ctx.scene==&drawing_window.scene && ctx.area==nullptr && ctx.region==nullptr &&
    ctx.py==nullptr && ctx.orig==nullptr && ctx.store==nullptr &&
    gpu_state.blend==1 && gpu_state.depth==0 && gpu_state.mask==15 && gpu_state.cull==0 &&
    gpu_state.stencil==0 && gpu_state.shader==nullptr && !gpu_state.scissor_test &&
    gpu_state.viewport[0]==0 && gpu_state.viewport[1]==0 && gpu_state.viewport[2]==1600 &&
    gpu_state.viewport[3]==900 && gpu_state.matrix==0 && gpu_state.projection==1;
  return PyBool_FromLong(ok);
}
static PyObject *fixture_damage(PyObject *, PyObject *) {
  gpu_state.blend=90; gpu_state.depth=91; gpu_state.mask=0; gpu_state.cull=5; gpu_state.stencil=6;
  gpu_state.shader=&other_shader; gpu_state.scissor_test=true;
  GPU_viewport(1,2,3,4); GPU_scissor(6,7,8,9); gpu_state.matrix=100; gpu_state.projection=101;
  Py_RETURN_NONE;
}
static PyObject *fixture_clear(PyObject *, PyObject *) { window_draw_clear(); Py_RETURN_NONE; }
static PyObject *fixture_counts(PyObject *, PyObject *) {
  return Py_BuildValue("iiii", allocations, int(window_draw_count.load()), gil_sets, gil_clears);
}
static PyObject *fixture_foreign(PyObject *, PyObject *) {
  return PyCapsule_New(&old_window,"RNA_HANDLE",nullptr);
}
static PyObject *fixture_add(PyObject *, PyObject *args) { return window_draw_add(args); }
static PyObject *fixture_remove(PyObject *, PyObject *args) { return window_draw_remove(args); }
static PyMethodDef methods[]={
  {"add",fixture_add,METH_VARARGS,nullptr}, {"remove",fixture_remove,METH_VARARGS,nullptr},
  {"draw",fixture_draw,METH_NOARGS,nullptr}, {"baseline",fixture_baseline,METH_NOARGS,nullptr},
  {"damage",fixture_damage,METH_NOARGS,nullptr}, {"clear",fixture_clear,METH_NOARGS,nullptr},
  {"counts",fixture_counts,METH_NOARGS,nullptr}, {"foreign",fixture_foreign,METH_NOARGS,nullptr},
  {nullptr,nullptr,0,nullptr}};
static PyModuleDef module={PyModuleDef_HEAD_INIT,"_overlay_fixture",nullptr,-1,methods,
                          nullptr,nullptr,nullptr,nullptr};
PyMODINIT_FUNC PyInit__overlay_fixture() { return PyModule_Create(&module); }
'''

SCENARIOS = r'''
import gc, threading, weakref
import _overlay_fixture as m

def add(cb, args=()): return m.add(None, cb, args, 'POST_PIXEL')
def remove(h): return m.remove(None, h)
def raises(kind, fn):
    try: fn()
    except kind: pass
    else: raise AssertionError(f'expected {kind.__name__}')

a, n, sets, clears = m.counts()
assert (a,n)==(0,0) and m.draw()
assert m.counts()==(a,n,sets,clears), 'empty path acquired GIL'
raises(TypeError, lambda: add(42))
raises(TypeError, lambda: add(lambda:None, []))
raises(ValueError, lambda: m.add(None, lambda:None, (), 'POST_VIEW'))
raises(TypeError, lambda: m.add(None, lambda:None, ()))
raises(ValueError, lambda: remove(m.foreign()))
raises(ValueError, lambda: remove(None))
seen=[]
def good(value):
    assert m.baseline(), 'callback context/GPU baseline'
    seen.append(value)
h=add(good,(7,))
assert m.draw() and seen==[7]
remove(h); remove(h)
assert m.draw() and seen==[7] and m.counts()[1]==0
raises(TypeError, lambda: m.remove(None,h,42))
del h
assert m.counts()[0]==0

# Capsule destruction owns unregister; the registry must not retain the capsule.
class Callback:
    def __call__(self): pass
cb=Callback(); ref=weakref.ref(cb); h=add(cb); del cb
assert ref() is not None
del h; gc.collect()
assert ref() is None and m.counts()[:2]==(0,0), 'orphan callback'

# Removing self and a pending callback; adding a new one waits one composition.
handles={}
def first():
    assert m.baseline()
    seen.append('first')
    remove(handles['first']); remove(handles['later'])
    handles['new']=add(good,('new',))
handles['first']=add(first); handles['later']=add(good,('removed',))
assert m.draw() and seen[-1]=='first' and 'removed' not in seen
assert m.draw() and seen[-1]=='new'
handles.clear(); gc.collect()
assert m.counts()[:2]==(0,0)

# Dropping the last pending capsule during dispatch must cancel it too.
handles['dropper']=add(lambda: handles.pop('victim'))
handles['victim']=add(good,('victim',))
assert m.draw() and 'victim' not in seen
handles.clear(); gc.collect()
assert m.counts()[:2]==(0,0)

# Exception must not skip restoration or later handlers; SystemExit must not exit.
def broken():
    assert m.baseline()
    m.damage()
    raise RuntimeError('fixture draw failure')
def exits():
    m.damage()
    raise SystemExit(17)
handles['bad']=add(broken); handles['exit']=add(exits); handles['good']=add(good,('after-error',))
assert m.draw(), 'GPU/context not restored after exception'
assert seen[-1]=='after-error'
handles.clear(); gc.collect()
assert m.counts()[:2]==(0,0)

# Nested capture cannot recursively run callbacks.
def nested():
    seen.append('nested')
    assert m.draw()
h=add(nested)
assert m.draw() and seen.count('nested')==1
remove(h); del h

# WM clear during dispatch invalidates every pending record, including self.
handles['clear']=add(m.clear); handles['pending']=add(good,('cleared',))
assert m.draw() and 'cleared' not in seen and m.counts()[1]==0
for h in handles.values(): remove(h)
del h; handles.clear(); gc.collect()
assert m.counts()[:2]==(0,0)

# Cleanup decrefs may drop other capsules and attempt registration reentrantly.
handles={}
errors=[]
class CleanupCallback:
    def __call__(self): pass
    def __del__(self):
        try:
            handles.pop('other',None)
            raises(RuntimeError, lambda: add(lambda:None))
        except BaseException as error:
            errors.append(error)
handles['cleanup']=add(CleanupCallback()); handles['other']=add(lambda:None)
m.clear(); handles.clear(); gc.collect()
assert not errors, errors
assert m.counts()[:2]==(0,0)

# Capsule GC can happen on a Python worker: count fast-path is atomic, list is GIL-held.
handles['thread']=add(lambda:None)
errors=[]
def worker():
    try:
        raises(RuntimeError, lambda: add(lambda:None))
        raises(RuntimeError, lambda: remove(handles['thread']))
        handles.pop('thread')
    except BaseException as error:
        errors.append(error)
t=threading.Thread(target=worker); t.start(); t.join()
assert not errors, errors
assert m.counts()[:2]==(0,0)
assert m.counts()[2]==m.counts()[3], 'unbalanced Python context/GIL'
print('CPU lifecycle scenarios passed (mock GPU/context; not GPU validation)')
'''


def python_development() -> tuple[Path, Path]:
    include = Path(sysconfig.get_path("include"))
    if (include / "Python.h").exists() and sys.version_info >= (3, 13):
        return Path(sys.executable), include
    home = ROOT / "tools/blender-5.2.2-linux-x64/5.2/python"
    include = home / "include/python3.13"
    executable = home / "bin/python3.13"
    if not (include / "Python.h").exists():
        include = ROOT / "tools/blender-src/lib/linux_x64/python/include/python3.13"
    if executable.exists() and (include / "Python.h").exists():
        return executable, include
    raise unittest.SkipTest("CPU fixture needs CPython >=3.13 headers/interpreter")


def compile_payload(root: Path, payload: str, executable: Path,
                    include: Path) -> subprocess.CompletedProcess[str]:
    source = root / "payload.cc"
    binary = root / "_overlay_fixture.so"
    source.write_text(STUBS + payload + HARNESS, encoding="utf-8")
    result = subprocess.run(["c++", "-std=c++17", "-shared", "-fPIC", "-pthread",
                             "-Wall", "-Wextra", "-Werror", "-I" + str(include),
                             str(source), "-o", str(binary)],
                            capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise AssertionError(f"CPU fixture compile failed:\n{result.stdout}{result.stderr}")
    return subprocess.run([str(executable), "-B", "-c", SCENARIOS], cwd=root,
                          capture_output=True, text=True, timeout=30, check=False)


class PatchTests(unittest.TestCase):
    def __init__(self, methodName: str = 'runTest') -> None:
        super().__init__(methodName)
        self.tmp: tempfile.TemporaryDirectory[str] | None = None

    @property
    def root(self) -> Path:
        assert self.tmp is not None, 'fixture has not been set up'
        return Path(self.tmp.name)

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="narwhal-window-overlay-")
        self.addCleanup(self.tmp.cleanup)
        fixture(self.root)

    def test_apply_exact_paths_glue_and_idempotence(self):
        sibling = self.root / "editors/screen/screen_edit.cc"
        sibling.parent.mkdir(parents=True)
        sibling.write_text('// sibling status37 must remain untouched\n')
        self.assertEqual(patch.apply(self.root), list(patch.NATIVE_INPUTS))
        before = snapshot(self.root)
        assert_glue({n: (self.root / n).read_text() for n in patch.NATIVE_INPUTS})
        self.assertEqual(patch.apply(self.root), [])
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(sibling.read_text(), '// sibling status37 must remain untouched\n')

    def test_all_anchor_failures_are_prewrite(self):
        for name, old, _ in patch.EDITS:
            for replacement in ('/* missing anchor */\n', old + old):
                with self.subTest(name=name, anchor=old.splitlines()[0], duplicate=replacement==old+old):
                    fixture(self.root)
                    path = self.root / name
                    path.write_text(path.read_text().replace(old, replacement, 1))
                    before = snapshot(self.root)
                    with self.assertRaises(RuntimeError): patch.apply(self.root)
                    self.assertEqual(snapshot(self.root), before)

    def test_conflicting_header_is_prewrite(self):
        header = self.root / patch.HEADER
        header.write_text('// someone else owns this\n')
        before = snapshot(self.root)
        with self.assertRaisesRegex(RuntimeError, 'header mismatch'): patch.apply(self.root)
        self.assertEqual(snapshot(self.root), before)

    def test_damaged_installed_blocks_rejected_without_writes(self):
        patch.apply(self.root)
        originals = snapshot(self.root)
        for name, _, new in patch.EDITS:
            with self.subTest(name=name, block=new[:45]):
                for file, contents in originals.items(): (self.root / file).write_bytes(contents)
                path = self.root / name
                damaged = new.replace('\n', '\n/* damage */\n', 1)
                path.write_text(path.read_text().replace(new, damaged, 1))
                before = snapshot(self.root)
                with self.assertRaises(RuntimeError): patch.apply(self.root)
                self.assertEqual(snapshot(self.root), before)

    def test_source_glue_mutations_go_red(self):
        patch.apply(self.root)
        texts = {n: (self.root / n).read_text() for n in patch.NATIVE_INPUTS}
        mutations = [
            (patch.DRAW, '  BPY_callback_window_draw(C, win);\n', ''),
            (patch.CALLBACK, '    window_draw_clear();', '    /* no cleanup */'),
            (patch.CALLBACK, '    return window_draw_remove(args);', '    /* wrong capsule route */'),
            (patch.CAPI, 'ARRAY_SIZE(pyrna_window_methods) == 4', 'ARRAY_SIZE(pyrna_window_methods) == 2'),
        ]
        for name, old, new in mutations:
            with self.subTest(mutation=old):
                broken = dict(texts); broken[name] = broken[name].replace(old, new)
                with self.assertRaises((AssertionError, ValueError)): assert_glue(broken)
        broken = dict(texts)
        broken[patch.DRAW] = broken[patch.DRAW].replace('  BPY_callback_window_draw(C, win);\n','')
        broken[patch.DRAW] = broken[patch.DRAW].replace('  ED_screen_draw_edges(win);',
            '  BPY_callback_window_draw(C, win);\n  ED_screen_draw_edges(win);')
        with self.assertRaises(AssertionError): assert_glue(broken)
        assert_glue(texts)  # Restored fixture still green.

    def test_real_native_inputs_on_temporary_copy_only(self):
        if not all((patch.SRC / n).exists() for n in FIXTURES):
            self.skipTest('native source checkout not available')
        originals = {n: (patch.SRC / n).read_bytes() for n in FIXTURES}
        fixture(self.root, {n: b.decode() for n, b in originals.items()})
        header = patch.SRC / patch.HEADER
        if header.exists(): (self.root / patch.HEADER).write_bytes(header.read_bytes())
        patch.apply(self.root)
        assert_glue({n: (self.root / n).read_text() for n in patch.NATIVE_INPUTS})
        self.assertEqual(patch.apply(self.root), [])
        self.assertEqual(originals, {n: (patch.SRC / n).read_bytes() for n in FIXTURES})

    def test_no_teardown_python_entry(self):
        dispatch = patch.NATIVE[patch.NATIVE.index('void BPY_callback_window_draw('):]
        for guard in ('!BLI_thread_is_main()', '!Py_IsInitialized()', 'Py_IsFinalizing()',
                      'window_draw_count.load() == 0', 'window_draw_dispatching'):
            self.assertLess(dispatch.index(guard), dispatch.index('bpy_context_set(C, &gilstate)'))
        self.assertIn('std::atomic<size_t>', patch.NATIVE)
        self.assertNotIn('GPU_framebuffer_bind(', patch.NATIVE)
        self.assertNotIn('BKE_CB_EVT', ''.join(new for _, _, new in patch.EDITS))


class NativeCPUFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('c++'): raise unittest.SkipTest('c++ compiler unavailable')
        cls.executable, cls.include = python_development()

    def run_payload(self, payload: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(prefix='narwhal-overlay-cpu-') as directory:
            return compile_payload(Path(directory), payload, self.executable, self.include)

    def test_lifecycle_context_gpu_state_and_errors(self):
        result = self.run_payload(patch.NATIVE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('CPU lifecycle scenarios passed', result.stdout)
        self.assertIn('RuntimeError: fixture draw failure', result.stderr)
        self.assertIn('RuntimeError: SystemExit in Window draw callback', result.stderr)

    def test_native_mutations_go_red_then_restored_green(self):
        mutations = [
            ('  window_draw_detach(record);\n  window_draw_release(record);',
             '  window_draw_detach(record);\n  /* leak capsule record */'),
            ('    GPU_blend(blend_);', '    GPU_blend(GPU_BLEND_ALPHA);'),
            ('  CTX_py_state_pop(C, &py_state);',
             '  CTX_py_state_pop(C, &py_state); CTX_store_set(C, nullptr);'),
            ('!BLI_thread_is_main() || window_draw_clearing',
             'false || window_draw_clearing'),
        ]
        for old, new in mutations:
            with self.subTest(mutation=old):
                self.assertEqual(patch.NATIVE.count(old), 1)
                result = self.run_payload(patch.NATIVE.replace(old, new))
                self.assertNotEqual(result.returncode, 0, 'mutation escaped CPU fixture')
        restored = self.run_payload(patch.NATIVE)
        self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
