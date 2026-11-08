"""Workspace UI coordinates and offset painting; no document or area mutations."""
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg
from narwhal3d import workspace_overlay as overlay, rebuild_popup  # noqa: E402


def pane(x, y, width, height):
    return SimpleNamespace(regions=[SimpleNamespace(type="WINDOW", x=x, y=y,
                                                   width=width, height=height)])


panes = [pane(108, 441, 627, 408), pane(736, 441, 626, 408),
         pane(108, 30, 627, 410), pane(736, 30, 626, 410)]
ui = SimpleNamespace(_view3d_areas=lambda: panes)
before = [vars(a.regions[0]).copy() for a in panes]
region = overlay.region(ui)
assert (region.x, region.y, region.width, region.height) == (108, 30, 1254, 819)
assert region.x + region.width / 2 == 735
assert region.y + region.height / 2 == 439.5
assert overlay.region(SimpleNamespace(_view3d_areas=lambda: panes[::-1])) == region
assert [vars(a.regions[0]) for a in panes] == before
assert overlay.region(SimpleNamespace(_view3d_areas=lambda: [])) is None
single = overlay.region(SimpleNamespace(_view3d_areas=lambda: panes[:1]))
assert (single.x, single.y, single.width, single.height) == (108, 441, 627, 408)
print("PASS: all modeling panes define the workspace; single-pane and empty layouts stay intact")

fake_bpy = SimpleNamespace(context=SimpleNamespace(window=SimpleNamespace(as_pointer=lambda: 42)))
with patch.dict(sys.modules, bpy=fake_bpy):
    assert overlay.window_key() == 42 and overlay.belongs_to(42)
    assert not overlay.belongs_to(43)
    fake_bpy.context.window = None
    assert overlay.window_key() is None and not overlay.belongs_to(42)
print("PASS: native window ownership prevents painting or consuming events in another window")

calls = {}
shader = SimpleNamespace(bind=lambda: None)
fake_ui = SimpleNamespace(_uniform_color_shader=lambda: shader)
for name in ("_draw_rect", "_draw_round_rect", "_draw_glyph", "_draw_text"):
    setattr(fake_ui, name, lambda *args, key=name: calls.__setitem__(key, args))
fake_gpu = SimpleNamespace(state=SimpleNamespace(
    blend_set=lambda _mode: None, scissor_test_set=lambda _enabled: None,
    scissor_set=lambda *args: calls.__setitem__("clip", args)))
with patch.dict(sys.modules, gpu=fake_gpu):
    painter = rebuild_popup._GpuPainter(fake_ui, (108, 30))
    color = (1, 1, 1, 1)
    painter.rect(1, 2, 3, 4, color)
    assert calls["_draw_rect"][1:5] == (109, 32, 111, 34)
    painter.round(1, 2, 3, 4, 2, color)
    assert calls["_draw_round_rect"][1:5] == (109, 32, 111, 34)
    painter.text(1, 2, "Current: 6", 12, color)
    assert calls["_draw_text"][:2] == (109, 32)
    painter.glyph([(0, 0), (2, 0), (0, 2)], color)
    assert calls["_draw_glyph"][1] == [(108, 30), (110, 30), (108, 32)]
    painter.clip(1, 2, 3, 4)
    assert calls["clip"] == (109, 32, 2, 2)
print("PASS: shapes, text, icons and clipping share the same native-window offset")
