"""Unit tests for ZoomCmd options: 1to1 and Dynamic (#64).

Tests ZoomCmd option handling, parallel projection checking, 1to1 distance
calculation, and refusal behaviour without modifying document state or Undo.

Run: python3 spike/test_zoom_options.py
"""
import importlib.util
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Load narwhal3d package safely without running package __init__.py (which needs real Blender)
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg

# Mock rhino3dm module if not installed in plain python environment
if "rhino3dm" not in sys.modules:
    r3_mock = types.ModuleType("rhino3dm")
    r3_mock.ObjectColorSource = type("ObjectColorSource", (), {})
    r3_mock.ObjectMode = type("ObjectMode", (), {
        "Normal": 0,
        "Hidden": 1,
        "Locked": 2,
        "InstanceDefinitionObject": 3,
    })
    r3_mock.ObjectMaterialSource = type("ObjectMaterialSource", (), {
        "MaterialFromObject": 0,
    })
    sys.modules["rhino3dm"] = r3_mock


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


options_mod = _load("narwhal3d.options", "narwhal3d/options.py")
views_mod = _load("narwhal3d.views", "narwhal3d/views.py")
units_mod = _load("narwhal3d.units", "narwhal3d/units.py")
commands_mod = _load("narwhal3d.commands", "narwhal3d/commands.py")

results = []


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


class FakeModel:
    def __init__(self, unit="Millimeters"):
        self._unit = unit

    def unit_system(self):
        return self._unit


class FakeContext:
    def __init__(self, unit="Millimeters"):
        self.model = FakeModel(unit)


class FakeRegion:
    def __init__(self, width=1920, height=1000):
        self.type = "WINDOW"
        self.alignment = "NONE"
        self.width = width
        self.height = height


class FakeRegion3D:
    def __init__(self, perspective="ORTHO", distance=31.494):
        self.view_perspective = perspective
        self.is_perspective = (perspective == "PERSP")
        self.view_distance = distance


class FakeSpace:
    def __init__(self, perspective="ORTHO", distance=31.494, lens=50.0):
        self.region_3d = FakeRegion3D(perspective, distance)
        self.lens = lens


class FakeArea:
    def __init__(self, width=1920, height=1000, perspective="ORTHO", distance=31.494, lens=50.0):
        self.type = "VIEW_3D"
        self.redraws = 0
        self.regions = [FakeRegion(width, height)]
        sp = FakeSpace(perspective, distance, lens)
        self.spaces = types.SimpleNamespace(active=sp)

    def tag_redraw(self):
        self.redraws += 1


class FakeBpy:
    def __init__(self, areas, dpi=96.0):
        self.context = types.SimpleNamespace(
            screen=types.SimpleNamespace(areas=areas),
            preferences=types.SimpleNamespace(
                system=types.SimpleNamespace(dpi=dpi)
            )
        )


# --- ZoomCmd contract checks ----------------------------------------------
@check("ZoomCmd options include Extents, Selected, All, 1to1, Dynamic")
def _():
    cmd = commands_mod.ZoomCmd()
    opt_names = [o.name for o in cmd.options]
    assert opt_names == ["Extents", "Selected", "All", "1to1", "Dynamic"], opt_names


@check("ZoomCmd records_undo is False (display change only)")
def _():
    assert commands_mod.ZoomCmd.records_undo is False


@check("ZoomCmd description documents that 1to1 is implemented")
def _():
    desc = commands_mod.ZoomCmd.description
    assert "1to1 implemented" in desc, desc


# --- Dynamic option checks ------------------------------------------------
@check("Zoom Dynamic explicitly refuses by name without modifying view distance")
def _():
    area = FakeArea(1920, 1080, "ORTHO", 31.494)
    fake_bpy = FakeBpy([area])
    sys.modules["bpy"] = fake_bpy
    try:
        cmd = commands_mod.ZoomCmd()
        ctx = FakeContext("Millimeters")
        msg, done = cmd.on_mode("Dynamic", ctx)
        assert done is True
        assert "Dynamic option is not implemented yet" in msg, msg
        # View distance must remain unchanged
        assert abs(area.spaces.active.region_3d.view_distance - 31.494) < 1e-9
    finally:
        sys.modules.pop("bpy", None)


# --- 1to1 option checks ---------------------------------------------------
@check("Zoom 1to1 on parallel view sets the assumed-DPI pixel scale and requests redraw")
def _():
    # 1920x1000, 96 DPI, Millimeters, default 50mm lens and 36mm sensor
    # expected distance: (1920 / (2 * (96 / 25.4))) * (50 / 36) = 352.7777...
    area = FakeArea(1920, 1000, "ORTHO", 31.494, lens=50.0)
    fake_bpy = FakeBpy([area], dpi=96.0)
    sys.modules["bpy"] = fake_bpy
    try:
        cmd = commands_mod.ZoomCmd()
        ctx = FakeContext("Millimeters")
        msg, done = cmd.on_mode("1to1", ctx)
        assert done is True
        assert msg == "Zoomed to 1:1 (assumed 96 DPI; not monitor-calibrated)", msg
        assert area.redraws == 1
        expected = (1920.0 / (2.0 * (96.0 / 25.4))) * (50.0 / 36.0)
        actual = area.spaces.active.region_3d.view_distance
        assert abs(actual - expected) < 1e-9, f"expected {expected}, got {actual}"
    finally:
        sys.modules.pop("bpy", None)


@check("Zoom 1to1 accounts for viewport lens focal length")
def _():
    # 1920x1000, 96 DPI, Millimeters, 35mm lens
    area = FakeArea(1920, 1000, "ORTHO", 31.494, lens=35.0)
    fake_bpy = FakeBpy([area], dpi=96.0)
    sys.modules["bpy"] = fake_bpy
    try:
        cmd = commands_mod.ZoomCmd()
        ctx = FakeContext("Millimeters")
        msg, done = cmd.on_mode("1to1", ctx)
        assert done is True
        assert msg == "Zoomed to 1:1 (assumed 96 DPI; not monitor-calibrated)", msg
        expected = (1920.0 / (2.0 * (96.0 / 25.4))) * (35.0 / 36.0)
        actual = area.spaces.active.region_3d.view_distance
        assert abs(actual - expected) < 1e-9, f"expected {expected}, got {actual}"
    finally:
        sys.modules.pop("bpy", None)


@check("Zoom 1to1 converts model units (Inches) correctly")
def _():
    # 1920x1000, 96 DPI, Inches, 50mm lens
    # In Inches: scale_to_mm = 25.4, so pixels_per_unit = 25.4 * (96/25.4) = 96
    # expected distance = (1920 / (2 * 96)) * (50 / 36) = 13.8888...
    area = FakeArea(1920, 1000, "ORTHO", 31.494, lens=50.0)
    fake_bpy = FakeBpy([area], dpi=96.0)
    sys.modules["bpy"] = fake_bpy
    try:
        cmd = commands_mod.ZoomCmd()
        ctx = FakeContext("Inches")
        msg, done = cmd.on_mode("1to1", ctx)
        assert done is True
        assert msg == "Zoomed to 1:1 (assumed 96 DPI; not monitor-calibrated)", msg
        expected = (1920.0 / (2.0 * 96.0)) * (50.0 / 36.0)
        actual = area.spaces.active.region_3d.view_distance
        assert abs(actual - expected) < 1e-9, f"expected {expected}, got {actual}"
    finally:
        sys.modules.pop("bpy", None)


@check("Zoom 1to1 analytical pixel prediction at two lens settings and aspect ratios")
def _():
    # Test projected pixel separation in an orthographic viewport for two lens settings
    # (50mm and 35mm) and two aspect ratios (landscape 16:9 and portrait 9:16).
    for winx, winy in ((1920, 1080), (1080, 1920)):
        for lens in (50.0, 35.0):
            area = FakeArea(winx, winy, "ORTHO", 31.494, lens=lens)
            fake_bpy = FakeBpy([area], dpi=96.0)
            sys.modules["bpy"] = fake_bpy
            try:
                cmd = commands_mod.ZoomCmd()
                ctx = FakeContext("Millimeters")
                msg, done = cmd.on_mode("1to1", ctx)
                assert done is True
                dist = area.spaces.active.region_3d.view_distance

                # Project two points separated by 25.4 mm (1 physical inch)
                p1_x, p1_y = views_mod.project_ortho_point(0.0, 0.0, 0.0, winx, winy, dist, lens_mm=lens)
                p2_x, p2_y = views_mod.project_ortho_point(25.4, 0.0, 0.0, winx, winy, dist, lens_mm=lens)
                sep_x = abs(p2_x - p1_x)
                assert abs(sep_x - 96.0) < 1e-9, f"win={winx}x{winy} lens={lens}: expected 96px, got {sep_x}"

                p3_x, p3_y = views_mod.project_ortho_point(0.0, 25.4, 0.0, winx, winy, dist, lens_mm=lens)
                sep_y = abs(p3_y - p1_y)
                assert abs(sep_y - 96.0) < 1e-9, f"win={winx}x{winy} lens={lens}: expected 96px, got {sep_y}"
            finally:
                sys.modules.pop("bpy", None)


@check("Zoom 1to1 on perspective view reports refusal and leaves view untouched")
def _():
    area = FakeArea(1920, 1000, "PERSP", 146.058)
    fake_bpy = FakeBpy([area], dpi=96.0)
    sys.modules["bpy"] = fake_bpy
    try:
        cmd = commands_mod.ZoomCmd()
        ctx = FakeContext("Millimeters")
        msg, done = cmd.on_mode("1to1", ctx)
        assert done is True
        assert "active view is perspective" in msg, msg
        # Camera distance must not be modified
        assert abs(area.spaces.active.region_3d.view_distance - 146.058) < 1e-9
    finally:
        sys.modules.pop("bpy", None)


@check("Zoom All 1to1 zooms all parallel views and skips perspective views")
def _():
    top_area = FakeArea(960, 500, "ORTHO", 31.494, lens=50.0)
    front_area = FakeArea(960, 500, "ORTHO", 31.494, lens=35.0)
    persp_area = FakeArea(960, 500, "PERSP", 146.058)
    fake_bpy = FakeBpy([top_area, front_area, persp_area], dpi=96.0)
    sys.modules["bpy"] = fake_bpy
    try:
        cmd = commands_mod.ZoomCmd()
        ctx = FakeContext("Millimeters")
        cmd.on_mode("All", ctx)
        msg, done = cmd.on_mode("1to1", ctx)
        assert done is True
        assert msg == "Zoomed all parallel viewports to 1:1 (assumed 96 DPI; not monitor-calibrated)", msg
        expected_top = (960.0 / (2.0 * (96.0 / 25.4))) * (50.0 / 36.0)
        expected_front = (960.0 / (2.0 * (96.0 / 25.4))) * (35.0 / 36.0)
        assert abs(top_area.spaces.active.region_3d.view_distance - expected_top) < 1e-9
        assert abs(front_area.spaces.active.region_3d.view_distance - expected_front) < 1e-9
        assert abs(persp_area.spaces.active.region_3d.view_distance - 146.058) < 1e-9
        assert (top_area.redraws, front_area.redraws, persp_area.redraws) == (1, 1, 0)
    finally:
        sys.modules.pop("bpy", None)


@check("Zoom 1to1 refuses non-finite lens, DPI and unit scales without changing or redrawing the view")
def _():
    from unittest.mock import patch
    for value in (float("nan"), float("inf"), -float("inf")):
        for field in ("lens", "dpi", "units"):
            area = FakeArea(lens=value if field == "lens" else 50.0)
            with patch.dict(sys.modules, {"bpy": FakeBpy([area])}), \
                    patch.object(views_mod, "ASSUMED_SCREEN_DPI", value if field == "dpi" else 96.0), \
                    patch.object(units_mod, "scale_factor", return_value=value if field == "units" else 1.0):
                msg, done = commands_mod.ZoomCmd().on_mode("1to1", FakeContext())
            assert done and "invalid" in msg, (field, value, msg)
            assert area.spaces.active.region_3d.view_distance == 31.494
            assert area.redraws == 0


@check("Zoom 1to1 reports assumed reference density, ignoring UI preference DPI")
def _():
    from unittest.mock import patch
    area = FakeArea()
    with patch.dict(sys.modules, {"bpy": FakeBpy([area], dpi=192.0)}):
        msg, done = commands_mod.ZoomCmd().on_mode("1to1", FakeContext())
    assert done and "assumed 96 DPI" in msg and "not monitor-calibrated" in msg
    assert abs(area.spaces.active.region_3d.view_distance - 352.77777777777777) < 1e-9


@check("Zoom 1to1 refuses camera views instead of pretending view_distance changes camera scale")
def _():
    from unittest.mock import patch
    area = FakeArea(perspective="CAMERA")
    with patch.dict(sys.modules, {"bpy": FakeBpy([area])}):
        msg, done = commands_mod.ZoomCmd().on_mode("1to1", FakeContext())
    assert done and "camera views are not implemented yet" in msg
    assert area.spaces.active.region_3d.view_distance == 31.494 and area.redraws == 0


# ---------------------------------------------------------------- report
print(f"\n{'=' * 54}")
print(" Zoom options tests (1to1 and Dynamic, #64)")
print(f"{'=' * 54}")
widths = max(len(n) for n, _, _ in results)
for name, status, detail in results:
    mark = "OK " if status == "PASS" else "FAIL"
    print(f" [{mark}] {name.ljust(widths)}  {detail}")

fails = [r for r in results if r[1] != "PASS"]
print(f"\n {len(results) - len(fails)}/{len(results)} passed")
sys.exit(1 if fails else 0)
