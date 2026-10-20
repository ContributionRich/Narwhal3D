"""Bug #70: fast, unlit Picture display in Shaded and Rendered.

Runs in background Blender because the cache gate uses real mesh, UV, image and material
RNA, but replaces only the final GPU allocation with a counter.

Run: tools/blender-patched/blender -b --factory-startup --python \
         spike/test_picture_display.py
"""
import inspect
import os
import sys

import bpy
from mathutils import Matrix

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from narwhal3d import ui  # noqa: E402

checks = 0
failures = []


def check(ok, label):
    global checks
    checks += 1
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")
    if not ok:
        failures.append(label)


class Bag:
    pass


def fake_area():
    sh = Bag()
    sh.type = "SOLID"
    sh.light = "STUDIO"
    sh.studio_light = "studio.sl"
    sh.show_shadows = False
    sh.color_type = "TEXTURE"
    sh.single_color = (0.8, 0.8, 0.8)
    sh.shadow_intensity = 0.0
    sh.background_type = "VIEWPORT"
    sh.background_color = (0.5, 0.5, 0.5)
    ov = Bag()
    ov.wireframe_threshold = 1.0
    ov.show_floor = True
    ov.show_ortho_grid = True
    ov.show_axis_x = True
    ov.show_axis_y = True
    ov.show_wireframes = False
    ov.wireframe_opacity = 0.0
    sp = Bag()
    sp.type = "VIEW_3D"
    sp.shading = sh
    sp.overlay = ov
    area = Bag()
    area.spaces = [sp]
    area.redraws = 0
    area.tag_redraw = lambda: setattr(area, "redraws", area.redraws + 1)
    return area, sp


print("-- Rendered uses our cheap custom shadow, not Blender's duplicate pass --")
area, space = fake_area()
real_label = ui.pane_label
ui.pane_label = lambda _area: "Perspective"
try:
    ok, _msg = ui.set_display_mode(area, "Rendered")
finally:
    ui.pane_label = real_label
check(ok and space.shading.type == "SOLID" and space.shading.color_type == "SINGLE",
      "Rendered is marked by SOLID + SINGLE white")
check(not space.shading.show_shadows and ui._is_rendered_space(space)
      and ui.display_mode_of_space(space) == "Rendered",
      "Blender's expensive shadow pass stays OFF while Rendered is still identified")
ui.pane_label = lambda _area: "Perspective"
try:
    ok, _msg = ui.set_display_mode(area, "Shaded")
finally:
    ui.pane_label = real_label
check(ok and space.shading.color_type == "TEXTURE" and not ui._is_rendered_space(space)
      and ui.display_mode_of_space(space) == "Shaded",
      "Shaded remains the distinct TEXTURE mode")

print("\n-- cached ground shadows stay visible during camera motion --")
space.shading.color_type = "SINGLE"
check(ui._ground_shadow_should_draw(space),
      "Rendered always draws its cached ground shadow instead of flickering it off")
space.shading.color_type = "TEXTURE"
check(not ui._ground_shadow_should_draw(space),
      "Shaded still has no Rendered ground-shadow pass")
space.shading.color_type = "SINGLE"

wheel_calls = []
real_note = ui.note_user_interaction
ui.note_user_interaction = lambda: wheel_calls.append(True)
try:
    check(ui._note_view_wheel(False) and len(wheel_calls) == 1,
          "a viewport wheel marks navigation BEFORE Blender draws the first zoom frame")
    check(not ui._note_view_wheel(True) and len(wheel_calls) == 1,
          "a tab-strip wheel is consumed as tab cycling, not view navigation")
finally:
    ui.note_user_interaction = real_note

boxes = [([(0, 0), (1, 0), (1, 1), (0, 1)], 0.0, 1.0)]
shadow_builds = []
real_shadow_builder = ui._build_ground_shadow_draw
ui._shadow_batch_cache.update(key=None, z=None, draw=None)
ui._build_ground_shadow_draw = lambda shader, pos, alpha: shadow_builds.append(len(pos)) or pos
try:
    first_shadow = ui._ground_shadow_draw(boxes, ("scene-a",), object())
    second_shadow = ui._ground_shadow_draw(boxes, ("scene-a",), object())
    check(len(shadow_builds) == 1 and first_shadow == second_shadow,
          "a settled unchanged scene reuses its ground-shadow GPU batch")
    ui._ground_shadow_draw(boxes, ("scene-b",), object())
    check(len(shadow_builds) == 2,
          "a changed scene signature invalidates the ground-shadow batch")
    cached = ui._shadow_batch_cache["draw"]
    ui._gumball_drag = {"live": True}
    frozen = ui._ground_shadow_draw(boxes, ("drag-key-changed",), object())
    check(len(shadow_builds) == 2 and frozen[1] is cached,
          "a live Gumball drag reuses the existing shadow batch instead of rebuilding per move")
    ui._gumball_drag = None
    ui._ground_shadow_draw(boxes, ("after-release",), object())
    check(len(shadow_builds) == 3,
          "after Gumball release the changed shadow rebuilds exactly once")
finally:
    ui._gumball_drag = None
    ui._build_ground_shadow_draw = real_shadow_builder
    ui._shadow_batch_cache.update(key=None, z=None, draw=None)


def picture_object(name, image, x):
    me = bpy.data.meshes.new(name + "Mesh")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    uv = me.uv_layers.new(name="UVMap")
    for loop, value in zip(uv.data, ((0, 0), (1, 0), (1, 1), (0, 1))):
        loop.uv = value
    mat = bpy.data.materials.new("narwhal_picture:" + name)
    mat.use_nodes = True
    tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = image
    me.materials.append(mat)
    ob = bpy.data.objects.new(name, me)
    ob["narwhal_picture"] = True
    ob.matrix_world = Matrix.Translation((x, 0, 0))
    bpy.context.collection.objects.link(ob)
    return ob


print("\n-- Pictures use cached unlit overlay batches --")
order_src = inspect.getsource(ui._attach_preview_handler)
check("draw_rendered_pictures" in order_src
      and order_src.index("draw_rendered_pictures") < order_src.index("draw_command_preview")
      < order_src.index("draw_curves"),
      "the Picture image pass is behind both live previews and completed tracing curves")
check("draw_rendered_pictures" not in inspect.getsource(ui._attach_point_handler),
      "Picture is not registered a second time after curves by the point-marker pass")
image = bpy.data.images.new("bug70_source", width=2, height=2)
one = picture_object("Bug70PictureA", image, 0)
two = picture_object("Bug70PictureB", image, 2)
space.shading.type = "SOLID"
space.shading.color_type = "TEXTURE"
obs = ui.rendered_picture_objects(space)
check(one in obs and two in obs,
      "Shaded includes Pictures in the unlit IMAGE overlay (not only the lit material pass)")
space.shading.color_type = "SINGLE"
check(one in ui.rendered_picture_objects(space) and two in ui.rendered_picture_objects(space),
      "Rendered includes the same unlit Picture overlay")
space.shading.type = "WIREFRAME"
check(one not in ui.rendered_picture_objects(space) and two not in ui.rendered_picture_objects(space),
      "Wireframe does not paint image fills")

built = []
real_builder = ui._build_picture_draw
ui._picture_batch_cache.clear()
ui._build_picture_draw = lambda shader, img, pos, uvs: built.append((len(pos), len(uvs))) or (img, pos)
try:
    first = ui._picture_draws([one, two], object())
    second = ui._picture_draws([one, two], object())
    check(len(built) == 2 and first == second,
          "an unchanged frame reuses both per-Picture GPU batches")
    one.data.vertices[0].co.x = -0.25
    one.data.update()
    # Production's depsgraph/bridge listener emits this serial for same-pointer mesh edits.
    ui._curve_display_changed([one.name])
    third = ui._picture_draws([one, two], object())
    check(len(built) == 3 and third != second,
          "editing Picture vertices invalidates only its batch even when counts/pointers match")
    two.matrix_world.translation.x = 3.0
    fourth = ui._picture_draws([one, two], object())
    check(len(built) == 4 and fourth != third,
          "moving a Picture invalidates only its cached world-space batch")
finally:
    ui._build_picture_draw = real_builder
    ui._picture_batch_cache.clear()

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    for failure in failures:
        print("FAIL:", failure)
    sys.exit(1)
print("PICTURE DISPLAY (#70) PASSED")
