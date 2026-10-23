"""Bug #102: smooth display approximation during drag, exact native release."""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from narwhal3d import bridge, kernel, ui  # noqa: E402
from narwhal3d.state import app_state  # noqa: E402

checks = []


def check(ok, label):
    checks.append(bool(ok))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


real_move = bridge.move_subobject
real_document_object = ui._document_object
real_rebuild_display = ui.rebuild_display_keeping_selection
real_invalidate = ui.invalidate_snap_cache
real_redraw = ui._tag_redraw_viewports
real_view_direction = ui._view_direction
real_ray_plane_point = ui._ray_plane_point
real_pick_point = ui.pick_point
real_ctx = app_state.ctx
real_model = app_state.model
real_drag = ui._gumball_drag
real_subobjects = set(ui._selected_subobjects)

display_source = bridge.make_box((0, 0, 0), (4, 4, 0), 4)
display_recipe = kernel.recipe_of(display_source)
display_occt = bridge._occt()
# Build the fixture directly in OCCT: this Blender-only gate intentionally does not
# load the Rust core that normally rehydrates the rhino3dm box's analytic master.
display_shape = display_occt.make_box(4, 4, 4)
display_vector = (0.5, 0.2, 1.0)
display_target = display_occt.local_subobject_signature(
    display_shape, "vertex", 1)
display_master = display_occt.move_subobject_local(
    display_shape, "vertex", 1, display_vector, display_target)
display_solid = bridge.occt_backed(display_master, {
    "kind": "local_move", "a": display_recipe, "component": "vertex",
    "index": 1, "vector": list(display_vector), "target": display_target})
display_mesh = bridge.geometry_to_bmesh(display_solid)
loose_edges = sum(not edge.link_faces for edge in display_mesh.edges)
display_mesh.free()
check(loose_edges == 0,
      "post-edit display contains only real boundary geometry, not artificial isocurves")

calls = []
redraws = []


def slow_move(_geom, _kind, _sub_id, vector, *, live=False):
    calls.append((tuple(vector), live))
    time.sleep(0.12)
    return "commit-validated"


try:
    bridge.move_subobject = slow_move
    ui._tag_redraw_viewports = lambda: redraws.append(True)
    state = {
        "brep_part": ("Box1", "Vertex", 3),
        "brep_geom": object(),
        "brep_vec": (0.0, 0.0, 0.0),
    }
    ui._gumball_drag = state
    ui._selected_subobjects.clear()
    check(("Box1", "Vertex", 3) in ui._subobject_highlight_keys(),
          "the frozen drag part keeps its translated preview after undo invalidates selection")

    cube_vertices = [
        (1, (0.0, 0.0, 0.0)), (2, (1.0, 0.0, 0.0)),
        (3, (0.0, 1.0, 0.0)), (4, (1.0, 1.0, 0.0)),
        (5, (0.0, 0.0, 1.0)), (6, (1.0, 0.0, 1.0)),
        (7, (0.0, 1.0, 1.0)), (8, (1.0, 1.0, 1.0)),
    ]
    cube_edge_ids = ((1, 2), (1, 3), (1, 5), (2, 4), (2, 6), (3, 4),
                     (3, 7), (4, 8), (5, 6), (5, 7), (6, 8), (7, 8))
    by_id = dict(cube_vertices)
    cube_edges = [(index, [by_id[a], by_id[b]])
                  for index, (a, b) in enumerate(cube_edge_ids, 1)]
    preview = ui._brep_preview_wireframe(
        {"vertices": cube_vertices, "edges": cube_edges},
        ("Box1", "Vertex", 1), (2.0, 3.0, 4.0))
    changed = [line for line, (_edge_id, source) in zip(preview, cube_edges)
               if line != source]
    check(len(preview) == 12 and len(changed) == 3
          and all((2.0, 3.0, 4.0) in line for line in changed),
          "vertex preview draws all 12 prospective edges and stretches its 3 incident edges")
    edge_preview = ui._brep_preview_wireframe(
        {"vertices": cube_vertices, "edges": cube_edges},
        ("Box1", "Edge", 1), (2.0, 3.0, 4.0))
    edge_changed = [line for line, (_edge_id, source) in zip(edge_preview, cube_edges)
                    if line != source]
    check(len(edge_preview) == 12 and len(edge_changed) == 5
          and edge_preview[0] == [(2.0, 3.0, 4.0), (3.0, 3.0, 4.0)],
          "edge preview moves the curve and stretches all four incident edges")

    class Region3D:
        is_perspective = True

    class Space:
        region_3d = Region3D()

    class Region:
        x = 10.0
        y = 20.0

    ray_calls = []
    ui._view_direction = lambda _rv3d: (0.2, -0.4, -0.8)
    ui._ray_plane_point = lambda *args: ray_calls.append(args) or (4.0, 5.0, 6.0)
    ui.pick_point = lambda *_args: (_ for _ in ()).throw(
        AssertionError("Perspective free drag flattened to the CPlane"))
    free_point = ui._gumball_free_drag_point(
        object(), Space(), Region(), 100.0, 200.0, (7.0, 8.0, 9.0))
    check(free_point == (4.0, 5.0, 6.0)
          and ray_calls[0][-2:] == ((7.0, 8.0, 9.0), (0.2, -0.4, -0.8)),
          "Perspective free drag tracks on the camera-facing plane through the part")
    started = time.perf_counter()
    moved = ui._gumball_apply(bridge.translation_xform((1.0, 2.0, 3.0)))
    elapsed = time.perf_counter() - started
    check(moved and elapsed < 0.03 and calls == [],
          f"mouse update performs zero geometry solves and returns immediately "
          f"({elapsed * 1000:.2f} ms, calls={calls!r})")
    check(state["brep_vec"] == (1.0, 2.0, 3.0)
          and ui._brep_part_offset() == (1.0, 2.0, 3.0) and redraws,
          "the exact accumulated vector drives the smooth translated subobject overlay")
    check(not any(key.startswith("brep_worker") or key.startswith("brep_pending")
                  for key in state),
          "the approximate drag starts no worker that can race a following scale")

    class Attrs:
        Id = "box-id"

    class FileObject:
        Attributes = Attrs()
        Geometry = object()

    class Context:
        def note_change(self, _label):
            pass

    class Model:
        def __init__(self):
            self.replaced = []

        def replace_geometry(self, ident, geom):
            self.replaced.append((ident, geom))

    ui._document_object = lambda _name: FileObject()
    ui.rebuild_display_keeping_selection = lambda: None
    ui.invalidate_snap_cache = lambda: None
    app_state.ctx = Context()
    app_state.model = Model()
    started = time.perf_counter()
    message = ui._commit_brep_part_move(state)
    commit_elapsed = time.perf_counter() - started
    check(calls == [((1.0, 2.0, 3.0), False)]
          and app_state.model.replaced == [("box-id", "commit-validated")]
          and "moved vertex 3" in message and commit_elapsed >= 0.09,
          f"release performs exactly one mandatory native COMMIT solve "
          f"({commit_elapsed * 1000:.2f} ms, calls={calls!r})")
finally:
    bridge.move_subobject = real_move
    ui._document_object = real_document_object
    ui.rebuild_display_keeping_selection = real_rebuild_display
    ui.invalidate_snap_cache = real_invalidate
    ui._tag_redraw_viewports = real_redraw
    ui._view_direction = real_view_direction
    ui._ray_plane_point = real_ray_plane_point
    ui.pick_point = real_pick_point
    app_state.ctx = real_ctx
    app_state.model = real_model
    ui._gumball_drag = real_drag
    ui._selected_subobjects.clear()
    ui._selected_subobjects.update(real_subobjects)

passed = sum(checks)
print(f"{passed}/{len(checks)} bug #102 approximate-preview checks passed")
if passed != len(checks):
    raise SystemExit(1)
