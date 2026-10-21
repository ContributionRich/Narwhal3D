"""Concise real-window acceptance report for the public Narwhal 3D interface."""
from __future__ import annotations

import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTIFACTS = os.path.join(ROOT, "artifacts")
IMAGE_PATH = os.path.join(ARTIFACTS, "ui.png")
SIDEBAR_HOVER_IMAGE = os.path.join(ARTIFACTS, "ui_sidebar_hover.png")
SIDEBAR_FLYOUT_IMAGE = os.path.join(ARTIFACTS, "ui_sidebar_flyout.png")
VIEWPORT_ARROW_IMAGE = os.path.join(ARTIFACTS, "ui_viewport_arrow.png")
PRINT_SELECTION_IMAGE = os.path.join(ARTIFACTS, "ui_print_selection.png")
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
EXPECTED_RIGHT_TABS = ("Layers", "Properties", "Display", "Bug Report", "AI Assistant")
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
    from narwhal3d import rightpanel, ui

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
        and len(getattr(ui, "_right_tab_boxes", ())) == len(rightpanel.TABS)
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


def _capture_path(bpy, path):
    os.makedirs(ARTIFACTS, exist_ok=True)
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
        result = bpy.ops.screen.screenshot(filepath=path)
        return ("FINISHED" in result and os.path.isfile(path)
                and os.path.getsize(path) > 0)
    except Exception as exc:  # noqa: BLE001
        print(f"[public-ui] screenshot failed: {exc!r}")
        return False


def _capture(bpy):
    return _capture_path(bpy, IMAGE_PATH)


def _crop_pixels(bpy, path, box):
    """Return RGB pixels from a bottom-up window-coordinate crop."""
    if not os.path.isfile(path):
        return []
    image = None
    try:
        image = bpy.data.images.load(path, check_existing=False)
        width, height = (int(value) for value in image.size)
        x0 = max(0, min(width, int(box[0])))
        y0 = max(0, min(height, int(box[1])))
        x1 = max(x0, min(width, int(box[2] + 0.999)))
        y1 = max(y0, min(height, int(box[3] + 0.999)))
        rgba = image.pixels[:]
        return [tuple(rgba[(y * width + x) * 4:(y * width + x) * 4 + 3])
                for y in range(y0, y1) for x in range(x0, x1)]
    except Exception as exc:  # noqa: BLE001
        print(f"[public-ui] screenshot crop failed: {exc!r}")
        return []
    finally:
        if image is not None:
            bpy.data.images.remove(image)


def _luma_counts(pixels, dark=0.35, light=0.65):
    luminances = [0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
                  for rgb in pixels]
    return (sum(value <= dark for value in luminances),
            sum(value >= light for value in luminances))


def _distinct_colors(pixels):
    return len({tuple(round(channel * 255) for channel in rgb) for rgb in pixels})


def _inspect(layout_was_ready):
    import bpy
    import narwhal3d
    from narwhal3d import commands
    from narwhal3d import menubar
    from narwhal3d import rightpanel
    from narwhal3d import toolbars
    from narwhal3d import ui
    from narwhal3d.state import app_state

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
          (menu_enabled, menu_total) == (181, 543), f"{menu_enabled}/{menu_total}")
    file_groups = {row[1] for row in menubar.resource_rows("File")
                   if row[0] == "popup"}
    help_groups = {row[1] for row in menubar.resource_rows("Help")
                   if row[0] == "popup"}
    check("File and Help expose the expanded desktop command groups",
          {"Document", "Save and Exchange", "Recent and Recovery",
           "Templates and Document", "Document Commands"} <= file_groups
          and {"Learning and Reference", "Support"} <= help_groups,
          f"File={len(file_groups)}, Help={len(help_groups)}")

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
          (toolbar_enabled, toolbar_total) == (119, 525),
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
    hover_redraws = []
    if first_sidebar is not None:
        first_center = ((first_sidebar[1] + first_sidebar[3]) * 0.5,
                        (first_sidebar[2] + first_sidebar[4]) * 0.5)
        ui.sidebar_hover_update(-100.0, -100.0)
        real_band_redraw = ui._tag_redraw_bands
        real_view_redraw = ui._tag_redraw_viewports
        ui._tag_redraw_bands = lambda: hover_redraws.append("band")
        ui._tag_redraw_viewports = lambda: hover_redraws.append("viewport")
        try:
            ui.sidebar_hover_redraw_update(*first_center)
        finally:
            ui._tag_redraw_bands = real_band_redraw
            ui._tag_redraw_viewports = real_view_redraw
    hovered = (ui.sidebar_action_at(*first_center) if first_sidebar is not None else None)
    hover_lines = (toolbars.sidebar_tooltip_lines(
        hovered[1], hovered[2], ui._sidebar_command_name(hovered[1]))
                   if hovered is not None else ())
    hover_card = ui.sidebar_tooltip_layout()
    hover_ok = (hovered is not None
                and hover_lines == (ui._sidebar_command_name(hovered[1]),)
                and hover_card is not None and hover_card["lines"] == hover_lines
                and hover_card["box"][0] >= ui._sidebar_region_box[2]
                and hover_redraws == ["band", "viewport"])
    check("hovering a left icon shows its command name beside the icon", hover_ok,
          hover_lines[0] if hover_lines else "no tooltip")
    hover_dark = hover_light = 0
    if hover_card is not None and _capture_path(bpy, SIDEBAR_HOVER_IMAGE):
        hover_dark, hover_light = _luma_counts(
            _crop_pixels(bpy, SIDEBAR_HOVER_IMAGE, hover_card["box"]))
    check("sidebar command-name tooltip is visibly rendered",
          hover_dark > 200 and hover_light > 20,
          f"dark={hover_dark}, light={hover_light}")
    linked_button = next((button for button in sidebar_buttons
                          if button.link), None)
    linked_index = sidebar_buttons.index(linked_button) if linked_button is not None else None
    rebuilt_sidebar = toolbars.sidebar_buttons(set(ui.COMMANDS))
    retained_button = (rebuilt_sidebar[linked_index] if linked_index is not None else None)
    opened = (ui.show_sidebar_flyout(retained_button, linked_index)
              if retained_button is not None else None)
    flyout = ui.sidebar_flyout_layout()
    flyout_hit = None
    flyout_border_dark = first_icon_colors = 0
    if flyout and flyout["cells"]:
        first = flyout["cells"][0]
        flyout_center = ((first[1] + first[3]) * 0.5,
                         (first[2] + first[4]) * 0.5)
        ui.sidebar_flyout_hover_update(*flyout_center)
        flyout_hit = ui.sidebar_flyout_action_at(*flyout_center)
        if _capture_path(bpy, SIDEBAR_FLYOUT_IMAGE):
            panel = flyout["panel"]
            flyout_border_dark, _unused = _luma_counts(_crop_pixels(
                bpy, SIDEBAR_FLYOUT_IMAGE,
                (panel[0] - 2.0, panel[1], panel[0] + 5.0, panel[3])))
            first_icon_colors = _distinct_colors(
                _crop_pixels(bpy, SIDEBAR_FLYOUT_IMAGE, first[1:]))
    flyout_reflowed = False
    if flyout is not None:
        original_cells = list(ui._sidebar_cells)
        original_region = ui._sidebar_region_box
        ui._sidebar_cells = [(i, x0 + 80.0, y0, x1 + 80.0, y1)
                             for i, x0, y0, x1, y1 in original_cells]
        ui._sidebar_region_box = (original_region[0], original_region[1],
                                  original_region[2] + 80.0, original_region[3])
        try:
            moved = ui.sidebar_flyout_reflow()
            resized = ui.sidebar_flyout_layout()
            window = bpy.context.window
            flyout_reflowed = (moved and resized is not None
                               and resized["panel"][0] >= ui._sidebar_region_box[2]
                               and resized["panel"][0] > flyout["panel"][0]
                               and resized["panel"][2] <= window.width
                               and resized["panel"][3] <= window.height)
        finally:
            ui._sidebar_cells = original_cells
            ui._sidebar_region_box = original_region
            ui.sidebar_flyout_reflow()
    flyout_ok = (opened == getattr(linked_button, "link", None)
                 and flyout is not None and len(flyout["buttons"]) > 1
                 and len({cell[1] for cell in flyout["cells"][:5]}) == 5
                 and flyout_hit is flyout["buttons"][0]
                 and flyout["panel"][0] >= ui._sidebar_overlay_left()
                 and flyout_border_dark > 10 and first_icon_colors > 10
                 and abs((flyout["cells"][0][3] - flyout["cells"][0][1])
                         - toolbars.FLYOUT_CELL) < 0.01
                 and flyout_reflowed)
    flyout_detail = "closed"
    if flyout:
        width = flyout["panel"][2] - flyout["panel"][0]
        cell_width = flyout["cells"][0][3] - flyout["cells"][0][1] if flyout["cells"] else 0
        flyout_detail = (f"{opened}: {len(flyout['buttons'])} icons, "
                          f"panel={width:.1f}, cell={cell_width:.1f}, "
                          f"border={flyout_border_dark}, colors={first_icon_colors}, "
                          f"constant={toolbars.FLYOUT_CELL:.1f}")
    check("sidebar arrow opens a five-column floating icon toolbar", flyout_ok,
          flyout_detail)
    ui.sidebar_flyout_close()
    check("UI report closes its temporary sidebar flyout",
          ui.sidebar_flyout_layout() is None)

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
                and drawn_tabs == len(EXPECTED_RIGHT_TABS)
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

    active_view = ui.active_pane_area()
    active_region = _window_region(active_view) if active_view is not None else None
    arrow_box = None
    arrow_hit = None
    arrow_dark = arrow_colors = 0
    if active_view is not None and active_region is not None:
        title_box = ui._title_hit_rect(active_view, active_region)
        local_arrow = ui.views.title_arrow_rect(title_box)
        if local_arrow is not None:
            arrow_box = (active_region.x + local_arrow[0],
                         active_region.y + local_arrow[1],
                         active_region.x + local_arrow[2],
                         active_region.y + local_arrow[3])
            center = ((arrow_box[0] + arrow_box[2]) * 0.5,
                      (arrow_box[1] + arrow_box[3]) * 0.5)
            arrow_hit = ui.pane_title_arrow_at(*center)
            if _capture_path(bpy, VIEWPORT_ARROW_IMAGE):
                arrow_pixels = _crop_pixels(bpy, VIEWPORT_ARROW_IMAGE, arrow_box)
                arrow_dark, _unused = _luma_counts(arrow_pixels, dark=0.45)
                arrow_colors = _distinct_colors(arrow_pixels)
    check("viewport title arrow is visible and owns its complete button target",
          arrow_box is not None and arrow_hit is not None
          and ui._pane_key(arrow_hit[0]) == ui._pane_key(active_view)
          and arrow_dark > 3 and arrow_colors >= 3,
          f"box={arrow_box}, dark={arrow_dark}, colors={arrow_colors}")

    # A selected curve automatically shows its control points. Those markers are a
    # later POST_PIXEL pass than the Print dialog and used to repaint over its white
    # sheet, making the preview look as if it contained extra objects. Put a circle
    # directly behind the sheet centre so this gate cannot pass by accidental layout.
    print_ok = False
    print_detail = "Top viewport unavailable"
    top_view = next((area for area in views if ui.pane_label(area) == "Top"), None)
    top_region = _window_region(top_view) if top_view is not None else None
    created_names = []
    if top_view is not None and top_region is not None:
        try:
            import bpy_extras.view3d_utils as view3d_utils
            from mathutils import Vector

            ui.set_active_pane(top_view)
            dialog_state = ui.printdialog.DialogState()
            dialog_layout = ui.printdialog.layout(
                top_region.width, top_region.height, ui._ui_scale(), dialog_state)
            page_w, page_h = ui.print2d.page_size(dialog_state.setup())
            sheet = ui.printdialog.page_rect(dialog_layout.preview, page_w, page_h)
            sheet_center = (sheet.x + sheet.w * 0.5, sheet.y + sheet.h * 0.5)
            top_space = next(space for space in top_view.spaces if space.type == "VIEW_3D")
            center = view3d_utils.region_2d_to_location_3d(
                top_region, top_space.region_3d, sheet_center, Vector((0.0, 0.0, 0.0)))
            before_names = {obj.name for obj in bpy.data.objects}
            if app_state.active_command is not None:
                app_state.cancel()
            for line in ("Circle", f"w{center.x},{center.y},0", "5"):
                app_state.feed_line(line)
            created_names = sorted({obj.name for obj in bpy.data.objects} - before_names)
            for obj in bpy.context.selected_objects:
                obj.select_set(False)
            for name in created_names:
                bpy.data.objects[name].select_set(True)
            opened_print = ui.open_print_dialog()
            selected_page = ui._print_dialog.get("page") if ui._print_dialog else None
            selected_strokes = len(selected_page.strokes) if selected_page is not None else -1
            captured = opened_print and _capture_path(bpy, PRINT_SELECTION_IMAGE)
            leaked_markers = -1
            if captured and selected_page is not None:
                live_layout = ui._print_dialog_layout(top_region)
                live_sheet = ui.printdialog.page_rect(
                    live_layout.preview, selected_page.width_mm, selected_page.height_mm)
                pixels = _crop_pixels(
                    bpy, PRINT_SELECTION_IMAGE,
                    (top_region.x + live_sheet.x, top_region.y + live_sheet.y,
                     top_region.x + live_sheet.x + live_sheet.w,
                     top_region.y + live_sheet.y + live_sheet.h))
                target = ui.CONTROL_POINT_COLOR[:3]
                leaked_markers = sum(
                    max(abs(rgb[i] - target[i]) for i in range(3)) <= 0.12
                    for rgb in pixels)
            for obj in bpy.context.selected_objects:
                obj.select_set(False)
            ui._print_dialog_refresh()
            clear_page = ui._print_dialog.get("page") if ui._print_dialog else None
            clear_strokes = len(clear_page.strokes) if clear_page is not None else -2
            print_ok = (opened_print and captured and selected_strokes == clear_strokes
                        and leaked_markers == 0)
            print_detail = (f"strokes selected/clear={selected_strokes}/{clear_strokes}, "
                            f"control-point pixels on sheet={leaked_markers}")
        except Exception as exc:  # noqa: BLE001
            print_detail = repr(exc)
        finally:
            ui.close_print_dialog()
            for obj in bpy.context.selected_objects:
                obj.select_set(False)
            if created_names:
                app_state.ctx.undo_last()
    check("selected curves do not add content or overlays to unchecked Print preview",
          print_ok, print_detail)

    # FilletEdge used to make the first click select the whole solid, then convert the
    # second edge click to a CPlane point. A real 3-D edge above that plane was therefore
    # rejected as "not on an edge". Drive the actual click-release route at a known exact
    # box edge: one ordinary click must identify both owner and stable edge id.
    fillet_ok = False
    fillet_detail = "Top viewport unavailable"
    fillet_names = []
    fillet_view = None
    fillet_executed = False
    if top_view is not None and top_region is not None:
        try:
            import bpy_extras.view3d_utils as fillet_view3d
            from mathutils import Vector as FilletVector

            if app_state.active_command is not None:
                app_state.cancel()
            app_state.feed_line("SelNone")
            ui.clear_subobject_selection()
            ui.set_active_pane(top_view)
            top_space = next(space for space in top_view.spaces if space.type == "VIEW_3D")
            fillet_view = (top_space.region_3d.view_location.copy(),
                           top_space.region_3d.view_distance)
            before_names = {obj.name for obj in bpy.data.objects}
            for line in ("Box", "w300,300,0", "w320,320,0", "10"):
                app_state.feed_line(line)
            fillet_names = sorted({obj.name for obj in bpy.data.objects} - before_names)
            box_name = fillet_names[0] if fillet_names else None
            top_space.region_3d.view_location = FilletVector((310.0, 310.0, 0.0))
            top_space.region_3d.view_distance = 60.0
            top_space.region_3d.update()
            parts = ui._brep_parts_all().get(box_name, {}) if box_name else {}
            top_edges = [
                (edge_id, polyline) for edge_id, polyline in parts.get("edges", ())
                if polyline and all(abs(point[2] - 10.0) < 1e-6 for point in polyline)
            ]
            edge_id, polyline = top_edges[0] if top_edges else (None, ())
            edge_point = (tuple((polyline[0][i] + polyline[1][i]) * 0.5
                                for i in range(3)) if len(polyline) == 2
                          else polyline[len(polyline) // 2] if polyline else None)
            screen_point = (fillet_view3d.location_3d_to_region_2d(
                top_region, top_space.region_3d, FilletVector(edge_point))
                            if edge_point is not None else None)
            screen_hit = (ui.pick_brep_part(top_view, top_space, top_region,
                                            float(screen_point.x), float(screen_point.y))
                          if screen_point is not None else None)
            app_state.feed_line("FilletEdge")
            helper_eligible = False
            if screen_point is not None:
                click = (float(screen_point.x), float(screen_point.y))
                helper_eligible = ui._answer_edge_prompt_with_click(
                    top_view, top_space, top_region, *click)
                if helper_eligible:
                    # Recreate the command so the click-release route, not the direct
                    # diagnostic call above, is what the assertion judges.
                    app_state.cancel()
                    app_state.feed_line("FilletEdge")
                ui._sel_drag = {"pane": ui._pane_key(top_view), "start": click,
                                "now": click, "shift": False, "ctrl": False,
                                "active": False}
                ui._sel_drag_end()
            fillet_cmd = app_state.active_command
            selected_whole = sorted(ui.selected_display_names())
            picked_edges = list(getattr(fillet_cmd, "_edges", ()))
            picked_args = list(getattr(fillet_cmd, "args", ()))
            selected_ok = (box_name is not None and edge_id is not None
                           and getattr(fillet_cmd, "name", None) == "FilletEdge"
                           and picked_args == [box_name] and picked_edges == [edge_id]
                           and not selected_whole)
            result_name = None
            result_types = {}
            airtight = False
            if selected_ok:
                from narwhal3d import kernel as fillet_kernel

                app_state.feed_line("")
                fillet_executed = True
                result_names = sorted({obj.name for obj in bpy.data.objects} - before_names)
                result_name = result_names[0] if len(result_names) == 1 else None
                result_geom = (app_state.ctx.geom_by_name(result_name)
                               if result_name is not None else None)
                result_master = fillet_kernel.master_of(result_geom)
                occt = ui.bridge._occt()
                if result_master is not None and occt is not None:
                    result_types = occt.face_types(result_master)
                    airtight = occt.is_airtight(result_master)
            fillet_ok = (selected_ok and app_state.active_command is None and airtight
                         and result_types.get("CYLINDER", 0) >= 1)
            fillet_detail = (f"box={box_name}, expected edge={edge_id}, "
                             f"screen hit={screen_hit}, helper={helper_eligible}, "
                             f"args={picked_args}, edges={picked_edges}, "
                             f"whole selection={selected_whole}, result={result_name}, "
                             f"types={result_types}, airtight={airtight}")
        except Exception as exc:  # noqa: BLE001
            fillet_detail = repr(exc)
        finally:
            if app_state.active_command is not None:
                app_state.cancel()
            app_state.feed_line("SelNone")
            if fillet_names:
                app_state.ctx.undo_last()
                if fillet_executed:
                    app_state.ctx.undo_last()
            if fillet_view is not None:
                top_space.region_3d.view_location = fillet_view[0]
                top_space.region_3d.view_distance = fillet_view[1]
                top_space.region_3d.update()
    check("FilletEdge selects a viable 3-D edge in one click without preselecting its solid",
          fillet_ok, fillet_detail)

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

    assistant_index = rightpanel.tab_names().index("AI Assistant")
    ui.set_right_tab(assistant_index)
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
    assistant_layout = ui._ai.get("lay")
    assistant_state = ui.ai_assistant_state()
    assistant_ok = (assistant_layout is not None
                    and "connection" in assistant_layout
                    and "providers" not in assistant_layout
                    and len(assistant_layout.get("buttons", ())) == 3
                    and assistant_layout["transcript"][3] > assistant_layout["transcript"][1]
                    and assistant_layout["input"][3] > assistant_layout["input"][1]
                    and assistant_state.include_selection
                    and assistant_state.status == "disconnected")
    check("AI Assistant tab draws its honest local chat shell", assistant_ok,
          "connection status, selection context, clipped transcript, input, controls")

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
