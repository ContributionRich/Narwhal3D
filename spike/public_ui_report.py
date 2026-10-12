"""Concise real-window acceptance report for the public Narwhal 3D interface."""
from __future__ import annotations

import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTIFACTS = os.path.join(ROOT, "artifacts")
IMAGE_PATH = os.path.join(ARTIFACTS, "ui.png")
REPORT_PATH = os.path.join(ARTIFACTS, "ui_report.txt")
POLL_SECONDS = 0.25
MAX_POLLS = 120

EXPECTED_MENUS = (
    "File", "Edit", "View", "Curve", "Surface", "SubD", "Solid", "Mesh", "Drafting",
    "Transform", "Analyze", "Render", "Tools", "Window", "Help",
)
EXPECTED_TOOLBARS = (
    "Standard", "CPlanes", "Set View", "Display", "Select", "Viewport Layout",
    "Visibility", "Transform", "Curve Tools", "Surface Tools", "Solid Tools",
    "SubD Tools", "Mesh Tools", "Render", "Drafting",
)
EXPECTED_RIGHT_TABS = ("Layers", "Properties", "Display", "Bug Report")
EXPECTED_VIEWS = ("Top", "Front", "Perspective", "Right")


def box_inside(inner, outer, tolerance=0.0):
    """Return whether one ``(x0, y0, x1, y1)`` box stays inside another."""
    ix0, iy0, ix1, iy1 = inner
    ox0, oy0, ox1, oy1 = outer
    return (ix0 >= ox0 - tolerance and iy0 >= oy0 - tolerance
            and ix1 <= ox1 + tolerance and iy1 <= oy1 + tolerance)


def _window_region(area):
    return next((region for region in area.regions if region.type == "WINDOW"), None)


def _layout_ready():
    import bpy
    from narwhal3d import ui

    screen = getattr(bpy.context, "screen", None)
    if screen is None:
        return False
    views = [area for area in screen.areas if area.type == "VIEW_3D"]
    return (
        len(views) == 4
        and not getattr(ui, "_viewports_in_progress", False)
        and ui.left_band_area() is not None
        and ui.right_band_area() is not None
        and len(getattr(ui, "_sidebar_cells", ())) == 37
        and bool(ui.get_osnap_panel_boxes())
        and len(getattr(ui, "_right_tab_boxes", ())) == 4
    )


def _registered_operator_state(ui, bpy):
    operators = [
        cls for cls in ui.CLASSES
        if isinstance(cls, type) and issubclass(cls, bpy.types.Operator)
    ]
    good = [
        cls for cls in operators
        if cls.bl_idname.startswith("narwhal.")
        and bool(getattr(cls, "is_registered", False))
    ]
    return len(good), len(operators)


def _capture(bpy):
    os.makedirs(ARTIFACTS, exist_ok=True)
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
        result = bpy.ops.screen.screenshot(filepath=IMAGE_PATH)
        return ("FINISHED" in result and os.path.isfile(IMAGE_PATH)
                and os.path.getsize(IMAGE_PATH) > 0)
    except Exception as exc:  # noqa: BLE001
        print(f"[public-ui] screenshot failed: {exc!r}")
        return False


def _inspect(layout_was_ready):
    import bpy
    import narwhal3d
    from narwhal3d import commands
    from narwhal3d import menubar
    from narwhal3d import rightpanel
    from narwhal3d import toolbars
    from narwhal3d import ui

    checks = []

    def check(label, condition, detail=""):
        checks.append((bool(condition), label, str(detail)))

    screen = getattr(bpy.context, "screen", None)
    areas = list(screen.areas) if screen is not None else []
    views = [area for area in areas if area.type == "VIEW_3D"]
    labels = tuple(sorted((ui.pane_label(area) for area in views), key=str.casefold))
    expected_labels = tuple(sorted(EXPECTED_VIEWS, key=str.casefold))
    check("four named model viewports", layout_was_ready and len(views) == 4
          and labels == expected_labels, f"{len(views)}: {', '.join(labels)}")

    menu_names = menubar.resource_menu_names()
    check("Narwhal menu installed with exact authored groups",
          ui.menubar_installed() and menu_names == EXPECTED_MENUS,
          f"{len(menu_names)}: {', '.join(menu_names)}")
    menu_enabled, menu_total = menubar.coverage(set(ui.COMMANDS))
    check("menu actions are runnable or explicitly marked TODO",
          (menu_enabled, menu_total) == (166, 183), f"{menu_enabled}/{menu_total}")

    toolbar_names = toolbars.tab_names()
    check("toolbar installed with exact authored categories",
          ui.toolbar_installed() and toolbar_names == EXPECTED_TOOLBARS,
          f"{len(toolbar_names)}: {', '.join(toolbar_names)}")
    initial_tab = ui.toolbar_tab()
    switched = (ui.set_toolbar_tab("Curve Tools") == "Curve Tools"
                and ui.set_toolbar_tab("Standard") == "Standard")
    check("toolbar defaults to Standard and can switch",
          initial_tab == "Standard" and switched,
          f"initial={initial_tab!r}, final={ui.toolbar_tab()!r}")
    toolbar_enabled, toolbar_total = toolbars.coverage(set(ui.COMMANDS))
    check("toolbar actions are runnable or explicitly unavailable",
          (toolbar_enabled, toolbar_total) == (115, 525),
          f"{toolbar_enabled}/{toolbar_total}")

    orient = commands.OrientCmd()
    orient.pending_option = next(option for option in orient.options
                                 if option.name == "Scale")
    choice_segments = ui._prompt_segments_for(orient)
    choice_boxes = ui.cmdbar.option_boxes(choice_segments, lambda text: len(text) * 7,
                                           x0=0)
    choice_names = [name for name, _x0, _x1 in choice_boxes]
    choices_hit = all(
        ui.cmdbar.hit_option((x0 + x1) * 0.5, choice_boxes) == name
        and ui._option_click_line(orient, name) == name
        for name, x0, x1 in choice_boxes
    )
    check("Orient Scale values are clickable command-bar choices",
          choice_names == ["No", "1D", "3D"] and choices_hit,
          ", ".join(choice_names) if choice_names else "no choice hit boxes")

    sidebar_cells = list(getattr(ui, "_sidebar_cells", ()))
    check("left tool palette has 37 live cells", len(sidebar_cells) == 37,
          f"{len(sidebar_cells)} cells")
    sidebar_buttons = list(getattr(ui, "_sidebar_buttons", ()))
    first_sidebar = sidebar_cells[0] if sidebar_cells else None
    if first_sidebar is not None:
        first_center = ((first_sidebar[1] + first_sidebar[3]) * 0.5,
                        (first_sidebar[2] + first_sidebar[4]) * 0.5)
        ui.sidebar_hover_update(*first_center)
    hovered = (ui.sidebar_action_at(*first_center) if first_sidebar is not None else None)
    hover_lines = (toolbars.sidebar_tooltip_lines(hovered[1], hovered[2])
                   if hovered is not None else ())
    check("hovering a left icon identifies its action", bool(hover_lines),
          hover_lines[0] if hover_lines else "no tooltip")
    linked_button = next((button for button in sidebar_buttons
                          if button.link), None)
    linked_index = sidebar_buttons.index(linked_button) if linked_button is not None else None
    rebuilt_sidebar = toolbars.sidebar_buttons(set(ui.COMMANDS))
    retained_button = (rebuilt_sidebar[linked_index] if linked_index is not None else None)
    opened = (ui.show_sidebar_flyout(retained_button, linked_index)
              if retained_button is not None else None)
    flyout = ui.sidebar_flyout_layout()
    flyout_hit = None
    if flyout and flyout["cells"]:
        first = flyout["cells"][0]
        flyout_center = ((first[1] + first[3]) * 0.5,
                         (first[2] + first[4]) * 0.5)
        ui.sidebar_flyout_hover_update(*flyout_center)
        flyout_hit = ui.sidebar_flyout_action_at(*flyout_center)
    flyout_ok = (opened == getattr(linked_button, "link", None)
                 and flyout is not None and len(flyout["buttons"]) > 1
                 and len({cell[1] for cell in flyout["cells"][:5]}) == 5
                 and flyout_hit is flyout["buttons"][0]
                 and abs((flyout["cells"][0][3] - flyout["cells"][0][1])
                         - toolbars.FLYOUT_CELL) < 0.01)
    flyout_detail = "closed"
    if flyout:
        width = flyout["panel"][2] - flyout["panel"][0]
        cell_width = flyout["cells"][0][3] - flyout["cells"][0][1] if flyout["cells"] else 0
        flyout_detail = (f"{opened}: {len(flyout['buttons'])} icons, "
                          f"panel={width:.1f}, cell={cell_width:.1f}, "
                          f"constant={toolbars.FLYOUT_CELL:.1f}")
    check("sidebar arrow opens a five-column floating icon toolbar", flyout_ok,
          flyout_detail)

    icon_names = set(toolbars.toolbar_data.ICON_NAMES)
    icon_paths = [toolbars.icon_path(name) for name in icon_names]
    icon_root = os.path.realpath(os.path.join(ROOT, "narwhal3d", "icons"))
    confined = bool(icon_paths) and all(
        path is not None
        and os.path.commonpath((icon_root, os.path.realpath(path))) == icon_root
        for path in icon_paths
    )
    check("project icon manifest is complete and confined",
          toolbars.icons_available() and confined,
          f"{sum(path is not None for path in icon_paths)}/{len(icon_names)} in narwhal3d/icons")

    left = ui.left_band_area()
    right = ui.right_band_area()
    view_left = min((area.x for area in views), default=0)
    view_right = max((area.x + area.width for area in views), default=0)
    docks_correct = (left is not None and right is not None
                     and left.x < view_left and right.x >= view_right)
    check("left and right dock areas are on their authored sides", docks_correct,
          f"left={getattr(left, 'x', None)}, views={view_left}..{view_right}, right={getattr(right, 'x', None)}")

    osnap_boxes = ui.get_osnap_panel_boxes()
    left_box = ((left.x, left.y, left.x + left.width, left.y + left.height)
                if left is not None else (0, 0, 0, 0))
    osnap_inside = bool(osnap_boxes) and all(
        box_inside((box[1], box[2], box[3], box[4]), left_box, tolerance=2.0)
        for box in osnap_boxes
    )
    check("Osnap boxes stay inside the left dock", osnap_inside,
          f"{len(osnap_boxes)} boxes")

    right_region = _window_region(right) if right is not None else None
    right_layout = (rightpanel.layout(right_region.width, right_region.height, title_h=0)
                    if right_region is not None else None)
    body = right_layout["body"] if right_layout is not None else (0, 0, 0, 0)
    live_body_width = max(0, getattr(right_region, "width", 0) - rightpanel.TAB_STRIP_W)
    drawn_tabs = len(getattr(ui, "_right_tab_boxes", ()))
    right_ok = (rightpanel.tab_names() == EXPECTED_RIGHT_TABS
                and drawn_tabs == 4
                and body[2] - body[0] == live_body_width
                and live_body_width > 0)
    check("right dock has exact tabs and a live-width body", right_ok,
          f"{', '.join(rightpanel.tab_names())}; body={body[2] - body[0]}/{live_body_width}")

    caption_rows = tuple(ui._caption_tree())
    implemented_modes = tuple(
        name for name, _key, implemented, _shading in ui.views.DISPLAY_MODES if implemented)

    def has_mode_slot(rows):
        for row in rows:
            if row[0] == "item" and row[1] == ui.views.DISPLAY_MODE_PLACEHOLDER:
                return True
            if row[0] == "popup" and has_mode_slot(row[3]):
                return True
        return False

    mode_slot = has_mode_slot(caption_rows)
    check("viewport title menu exposes all implemented display modes",
          mode_slot and implemented_modes == ("Wireframe", "Shaded", "Rendered"),
          ", ".join(implemented_modes))

    registered, operator_total = _registered_operator_state(ui, bpy)
    check("Narwhal operator namespace is registered",
          operator_total > 0 and registered == operator_total,
          f"{registered}/{operator_total} narwhal.* operators")
    native_properties = (
        "narwhal_app_drawn" in bpy.types.Collection.bl_rna.properties
        and "narwhal_boundary_wires" in bpy.types.Mesh.bl_rna.properties
    )
    check("native Narwhal performance and boundary properties are available",
          native_properties)

    metadata = narwhal3d.bl_info
    metadata_ok = (
        metadata.get("name") == "Narwhal 3D"
        and "Narwhal 3D" in metadata.get("author", "")
        and "Narwhal 3D" in metadata.get("description", "")
        and toolbars.container_name() == "Narwhal 3D"
        and toolbars.sidebar_name() == "Narwhal Tools"
    )
    check("visible product and add-on metadata says Narwhal 3D", metadata_ok,
          metadata.get("name", "missing"))

    check("window screenshot captured", _capture(bpy), "artifacts/ui.png")

    passed = sum(ok for ok, _label, _detail in checks)
    total = len(checks)
    lines = ["Narwhal 3D public UI report"]
    for ok, label, detail in checks:
        suffix = f" — {detail}" if detail else ""
        lines.append(f"{'PASS' if ok else 'FAIL'} {label}{suffix}")
    lines.append(f"{passed}/{total} checks passed")
    report = "\n".join(lines) + "\n"
    os.makedirs(ARTIFACTS, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as stream:
        stream.write(report)
    print(report, end="")
    print("PUBLIC UI REPORT DONE")
    sys.stdout.flush()


def _finish(layout_was_ready):
    import bpy

    try:
        _inspect(layout_was_ready)
    except Exception as exc:  # noqa: BLE001
        os.makedirs(ARTIFACTS, exist_ok=True)
        report = ("Narwhal 3D public UI report\n"
                  f"FAIL reporter completed cleanly — {exc!r}\n"
                  "0/1 checks passed\n")
        with open(REPORT_PATH, "w", encoding="utf-8") as stream:
            stream.write(report)
        print(report, end="")
        print("PUBLIC UI REPORT DONE")
        sys.stdout.flush()
    finally:
        bpy.ops.wm.quit_blender()


def start():
    """Poll for the authored layout, report at readiness or at the bounded deadline."""
    import bpy

    state = {"polls": 0}

    def poll():
        state["polls"] += 1
        ready = _layout_ready()
        if ready or state["polls"] >= MAX_POLLS:
            _finish(ready)
            return None
        return POLL_SECONDS

    bpy.app.timers.register(poll, first_interval=POLL_SECONDS)


if __name__ == "__main__":
    start()
