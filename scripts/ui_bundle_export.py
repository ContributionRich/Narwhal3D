"""Export the shell's REAL content as JSON for the UI bundle (runs under ``blender -b``).

    tools/blender-patched/blender -b --factory-startup --python scripts/ui_bundle_export.py \\
        -- OUT_DIR

Writes ``ui_data.json`` and ``ui_data.js`` (the same object assigned to
``window.NARWHAL_UI``, so a page opened from disk can load it without a server).
Everything comes from the live registries the app draws from -- categories, tools,
flyouts, menus, status panes, inspector tabs, Osnap rows, display modes, every command
and the design tokens -- with each action's real availability.
"""
from dataclasses import asdict
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import narwhal3d  # noqa: E402,F401
from narwhal3d import design, osnap, rightpanel, toolbars, ui, views  # noqa: E402
from narwhal3d import menubar_resource_data, toolbar_data, viewport_menu_data  # noqa: E402
from narwhal3d import ai_assistant, ai_settings_popup, bugreport, preferences  # noqa: E402
from narwhal3d.object_properties import ObjectProperties  # noqa: E402
from narwhal3d.commands import COMMANDS  # noqa: E402

IMPLEMENTED = set(COMMANDS)


def _hex(rgb):
    return "#%02X%02X%02X" % tuple(rgb[:3])


def _button(b):
    if b.is_separator:
        return {"separator": True}
    out = {"text": b.text, "tooltip": b.tooltip, "icon": b.icon,
           "command": ui._sidebar_command_name(b) if b.left else None,
           "enabled": b.enabled}
    if b.right:
        out.update(right_tooltip=b.right_tooltip or b.right, right_enabled=b.right_enabled,
                           right_command=b.right)
    if b.link:
        out["flyout"] = b.link
    return out


def _menu_rows(rows):
    out = []
    for row in rows:
        kind = row[0]
        if kind == "sep":
            out.append({"separator": True})
        elif kind == "popup":
            out.append({"label": row[1], "children": _menu_rows(row[3])})
        else:
            command = row[3]
            label = row[1]
            todo = label.endswith(menubar_resource_data.TODO_SUFFIX)
            enabled = bool(command) and (command in menubar_resource_data.SPECIAL_COMMANDS
                                         or str(command).lower() in IMPLEMENTED)
            out.append({"label": label.replace(menubar_resource_data.TODO_SUFFIX, ""),
                        "shortcut": row[2] or "", "enabled": enabled and not todo,
                                                "command": command})
    return out


def _caption_rows(rows, path=()):
    out = []
    for kind, label, accel, payload in rows:
        if kind == "sep":
            out.append({"separator": True})
        elif kind == "popup":
            out.append({"label": views.strip_accel(label),
                        "children": _caption_rows(payload, path + (views.strip_accel(label),))})
        else:
            shown = label
            if label == views.DISPLAY_MODE_PLACEHOLDER:
                out.append({"display_modes": [
                    {"label": name, "enabled": bool(impl), "shortcut": f"Ctrl+Alt+{key}"}
                    for name, key, impl, _shade in views.DISPLAY_MODES]})
                continue
            out.append({"label": views.strip_accel(shown), "shortcut": accel or "",
                        "enabled": views.caption_action(path, label) is not None})
    return out


def _flyouts():
    out = {}
    for name in toolbar_data.FLYOUTS:
        probe = toolbars.Button("", "", None, None, "", None, False, False, link=name)
        rows = toolbars.sidebar_flyout_rows(probe, IMPLEMENTED)
        out[name] = [_button(b) for b in rows]
    return out


def _metrics():
    def pick(mod, names):
        return {n: getattr(mod, n) for n in names}
    return {
        "design": pick(design, ["MENUBAR_H", "CATEGORY_H", "CATEGORY_BUTTON_W", "COMMAND_BOX_H",
                                "COMMAND_MARGIN", "GAP", "RADIUS", "PANEL_RADIUS",
                                "VIEWPORT_RADIUS", "VIEWPORT_BORDER_W", "CHIP_RADIUS",
                                "LEFT_PANEL_W", "TOOL_CELL", "RIGHT_RAIL_W", "RIGHT_PANEL_W",
                                "TITLE_CHIP_INSET", "TITLE_CHIP_PAD_X", "TITLE_CHIP_PAD_Y",
                                "STATUS_H", "STATUS_CHIP_PAD"]),
        "toolbars": pick(toolbars, ["CATEGORY_ICON", "CATEGORY_LABEL_FONT", "CONTEXT_COLUMNS",
                                    "CONTEXT_HEADER_H", "SIDEBAR_ICON", "FLYOUT_COLUMNS",
                                    "FLYOUT_CELL"]),
        "rightpanel": pick(rightpanel, ["BAND_W", "TAB_STRIP_W", "TAB_PITCH", "TAB_ICON",
                                        "PANEL_TITLE_H", "HEADER_H", "ROW_H", "NAME_COL_W",
                                        "NARROW_COL_W", "SEARCH_H", "TITLE_BUTTON"]),
        "osnap": pick(views, ["OSNAP_PANEL_W", "OSNAP_ROW_H", "OSNAP_BOX", "OSNAP_PAD",
                              "OSNAP_TITLE_H", "OSNAP_FONT_SIZE"]),
        "note": "logical pixels at interface scale 1.0 (a maximised 1920x1080 window); "
                "the app multiplies every size by its interface scale",
    }


def panel_data():
    """Source-backed UI models with ONLY synthetic inputs, never live session state.

    Python is export/UI orchestration around existing models, not a new compute path.
    Rectangles come from the actual pure layout functions; text width is approximate.
    """
    config = ai_assistant.ProviderConfig()
    popup = ai_settings_popup.PopupModel(
        config, anchor=(1640, 1000), window_size=(1920, 1080), scale=1,
        on_save=lambda _config: None, on_refresh=lambda _config: False)
    sample = ObjectProperties(type="Closed surface", name="Sample object", layer="Default",
                              display_color_source="layer", display_color=(38, 116, 223),
                              linetype="Continuous", linetype_source="layer", linetype_scale=1,
                              print_color_source="display", print_width_source="default",
                              print_color=(0, 0, 0), print_width=0, no_print=False)
    return {
        "provenance": "Real pure layout/content models, synthetic document and chat inputs; NOT a live session capture",
        "layout_note": "Sample body 208x700 logical px; origin bottom-left, y up. Popup text measure is approximate, not BLF.",
        "properties": {"empty": rightpanel.property_rows([]),
                       "sample": rightpanel.property_rows([sample])},
        "display": {"layout": rightpanel.display_panel_layout(
            (0, 0, 208, 700), ("Top", "Perspective", "Front", "Right"),
            tuple(name for name, _, impl, _ in views.DISPLAY_MODES if impl))},
        "bug": {"categories": list(bugreport.CATEGORIES), "checks": list(bugreport.CHECKS),
                "layout": bugreport.layout((0, 0, 208, 700), 1)},
        "ai": {"providers": list(ai_assistant.PROVIDERS),
               "statuses": sorted(ai_assistant.STATUSES),
               "defaults": asdict(config),
               "default_endpoints": ai_assistant.DEFAULT_ENDPOINTS,
               "credential_env_names": ai_assistant.DEFAULT_CREDENTIAL_ENVS,
               "layout": ai_assistant.panel_layout((0, 0, 208, 700)),
               "tool_actions": "disabled in the real panel; never simulate execution",
               "messages": [{"role": "user", "text": "What can I check on this sample object?"},
                            {"role": "assistant", "text": "Synthetic design sample: inspect its layer, units and display mode. No provider was contacted."}]},
        "ai_settings": {"palettes": ai_settings_popup.PALETTES,
                        "order": list(ai_settings_popup.ORDER), "labels": ai_settings_popup.LABELS,
                        "rect": popup.rect, "body": popup.body,
                        "items": [asdict(item) for item in popup.items],
                        "fixed": [asdict(item) for item in popup.fixed]},
        "preference_defaults": preferences.DEFAULTS,
    }


def build():
    tabs = toolbars.tab_names()
    categories = [{"name": n, "icon": toolbars.category_icon(n),
                   "tools": [_button(b) for b in toolbars.context_buttons(n, IMPLEMENTED)]}
                  for n in tabs]
    commands = sorted({c.name for c in COMMANDS.values()}, key=str.casefold)
    unimplemented_panes = getattr(ui, "STATUSBAR_UNIMPLEMENTED", frozenset())
    data = {
        "app": "Narwhal 3D",
        "categories": categories,
        "standard_palette_name": toolbars.sidebar_name(),
        "flyouts": _flyouts(),
        "menus": [{"label": label.replace("&", ""), "rows": _menu_rows(rows)}
                  for label, rows in menubar_resource_data.MENUS],
        "viewport_title_menu": _caption_rows(
            viewport_menu_data.MENUS.get(viewport_menu_data.MODEL_KIND, ())),
        "viewports": [{"name": "Top", "slot": "top-left", "projection": "parallel"},
                      {"name": "Perspective", "slot": "top-right", "projection": "perspective"},
                      {"name": "Front", "slot": "bottom-left", "projection": "parallel"},
                      {"name": "Right", "slot": "bottom-right", "projection": "parallel"}],
        "status_panes": [{"id": p.id, "label": p.label, "kind": p.kind,
                          "clickable": p.clickable,
                          "implemented": p.id not in unimplemented_panes,
                          "sample_text": ui.sbar.render(p.id, ui.sbar.screenshot_state())
                          if p.id != "tolerance" else "Absolute Tolerance: 0.001"}
                         for p in ui.sbar.panes()],
        "inspector_tabs": [{"name": t.name, "icon": t.icon, "built": t.built}
                           for t in rightpanel.TABS],
        "gumball_menu": [{"label": label, "key": key, "enabled": bool(implemented)}
                         for label, key, implemented in ui.gumball.MENU_ENTRIES],
        "context_menus": {kind: [{"separator": True} if item is ui.nav.SEPARATOR else
                                  {"label": ui.nav.display_label(item.label), "command": item.command,
                                   "action": item.action,
                                   "enabled": bool(item.command and item.command.lower() in IMPLEMENTED)}
                                 for item in ui.nav.context_menu_items(kind, last_command="Line")]
                          for kind in ui.nav.CONTEXT_MENUS},
        "viewport_tab_menu": [{"label": label, "command": command,
                               "enabled": bool(command and command.lower() in IMPLEMENTED)}
                              for label, command in views.TAB_MENU],
        "viewport_tab_add_menu": [{"label": label, "command": command,
                                   "enabled": bool(command and command.lower() in IMPLEMENTED)}
                                  for label, command in views.TAB_ADD_MENU],
        "layer_columns": list(rightpanel.COLUMNS),
        "layer_menu": [None if e is None else {"label": e[0], "enabled": e[1] is not None}
                       for e in ui.LAYER_MENU],
        "osnap_rows": [{"label": n, "computed": n in osnap.IMPLEMENTED or
                        n in views.OSNAP_PANEL_FLAGS, "flag": n in views.OSNAP_PANEL_FLAGS}
                       for n in views.OSNAP_PANEL_ORDER],
        "osnap_default_on": ["End", "Point", "Mid", "Cen", "Quad"],
        "display_modes": [{"name": n, "shortcut": f"Ctrl+Alt+{k}", "implemented": bool(i)}
                          for n, k, i, _s in views.DISPLAY_MODES],
        "commands": commands,
        "command_catalog": [{"name": cls.name, "description": getattr(cls, "description", ""),
                             "steps": list(getattr(cls, "steps", ())),
                             "options": [option.name for option in getattr(cls, "options", ())
                                         if hasattr(option, "name")]}
                            for cls in {cls.name: cls for cls in COMMANDS.values()}.values()],
        "themes": {name: {k: _hex(v) for k, v in theme.items()}
                   for name, theme in design.THEMES.items()},
        "default_theme": design.DEFAULT_THEME,
        "metrics": _metrics(),
        "panels": panel_data(),
        "export_kind": "real registries + synthetic reference state; browser is a mock",
        "screenshots": [],
    }
    return data


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else os.path.join(ROOT, "dist", "ui_bundle_data")
    os.makedirs(out, exist_ok=True)
    data = build()
    with open(os.path.join(out, "ui_data.json"), "w") as f:
        json.dump(data, f, indent=1)
    with open(os.path.join(out, "ui_data.js"), "w") as f:
        f.write("window.NARWHAL_UI = ")
        json.dump(data, f)
        f.write(";\n")
    n_tools = sum(len(c["tools"]) for c in data["categories"])
    print(f"UI DATA EXPORTED: {len(data['categories'])} categories, {n_tools} tools, "
          f"{len(data['flyouts'])} flyouts, {len(data['menus'])} menus, "
          f"{len(data['commands'])} commands -> {out}", flush=True)


if __name__ == "__main__":
    main()
