"""Refresh cached Blender/UI colours without reloading modules or document state.

The draw handlers cache plain tuples at import. Explicit semantic bindings avoid
replacing arbitrary equal-valued tuples (e.g. white text versus a white surface).
Only Blender UI adapters consume this small Python table; no geometry is changed.
"""
from . import design

VIEW_BINDINGS = {
    'ACTIVE_TITLE_FILL': 'ACCENT_TITLE', 'ACTIVE_TITLE_TEXT': 'ACTIVE_TITLE_TEXT',
    'INACTIVE_TITLE_FILL': 'TITLE_CHIP_BG', 'INACTIVE_TITLE_TEXT': 'CHIP_TEXT',
    'TITLE_ARROW_FILL': 'TITLE_CHIP_BG', 'TITLE_ARROW_BORDER': 'BORDER_STRONG',
    'TAB_BAND_BG': 'PANEL_HEADER_BG', 'TAB_ACTIVE_BG': 'ACCENT_SOFT',
    'TAB_BORDER': 'BORDER', 'TAB_TEXT': 'TEXT', 'TAB_TEXT_ACTIVE': 'TEXT_STRONG',
    'CHROME_BG': 'BAR_BG', 'CHROME_TEXT': 'BAR_TEXT', 'MENU_BG': 'MENU_BG',
    'MENU_BORDER': 'BORDER_STRONG', 'MENU_SEPARATOR': 'BORDER_STRONG',
    'MENU_HOVER': 'ACCENT', 'MENU_HOVER_TEXT': 'BAR_TEXT_STRONG',
    'MENU_TEXT': 'TEXT', 'MENU_TEXT_DISABLED': 'TEXT_DISABLED', 'TOOL_PRESSED': 'ACCENT',
    'OSNAP_PANEL_BG': 'PANEL_BG', 'OSNAP_PANEL_BORDER': 'BORDER',
    'OSNAP_BOX_BG': 'CHIP_BG', 'OSNAP_PANEL_TEXT': 'TEXT_MUTED',
    'LAYER_LIST_BG': 'LIST_BG', 'LAYER_GRID': 'BORDER',
}
UI_BINDINGS = {
    'LABEL_COLOR': 'VIEWPORT_TEXT', 'PREVIEW_COLOR': 'PREVIEW',
    'CMD_BG': 'COMMAND_FRAME', 'CMD_BOX_BG': 'CONSOLE_BG',
    'CMD_BOX_BORDER': 'CONSOLE_BORDER', 'CMD_TEXT': 'PROMPT',
    'CMD_HISTORY': 'CONSOLE_TEXT', 'CMD_CURSOR': 'CONSOLE_INPUT',
    'CMD_OPTION': 'PROMPT_OPTION', 'CMD_OPTION_KEY': 'PROMPT',
    'CMD_COMPLETION_HL': 'ACCENT', 'CMD_COMPLETION_BG': 'MENU_BG',
    'CMD_COMPLETION': 'TEXT', 'CMD_COMPLETION_SEL': 'BAR_TEXT_STRONG',
    'SIDEBAR_MARK_COLOR': 'TEXT_MUTED', 'SIDEBAR_HOVER_COLOR': 'TOOL_CELL_HOVER',
    'SIDEBAR_FLYOUT_HOVER_COLOR': 'ACCENT_SOFT', 'SIDEBAR_FLYOUT_PRESSED_COLOR': 'BORDER',
    'SIDEBAR_TOOLTIP_BG': 'MENU_BG', 'SIDEBAR_TOOLTIP_BORDER': 'BORDER_STRONG',
    'SIDEBAR_TOOLTIP_TEXT': 'TEXT',
    'TAB_ROW_BG': 'RIBBON_BG', 'TAB_ROW_ACTIVE_BG': 'ACCENT_DEEP',
    'TAB_ROW_TEXT': 'BAR_TEXT', 'TAB_ROW_ACTIVE_TEXT': 'BAR_TEXT_STRONG',
    'POINT_OBJECT_COLOR': 'POINT', 'CONTROL_POINT_COLOR': 'CONTROL_POINT',
    'CONTROL_POLYGON_COLOR': 'CONTROL_POLYGON',
}


def refresh(ui, views):
    for name, token in VIEW_BINDINGS.items():
        setattr(views, name, getattr(design, token))
    for name, token in UI_BINDINGS.items():
        alpha = .9 if name == 'CONTROL_POLYGON_COLOR' else 1.0
        setattr(ui, name, design.rgb01(getattr(design, token), alpha))
    for name, token in {'VIEWPORT_BG': 'VIEWPORT_BG', 'GRID_COLOR': 'GRID_MINOR',
                        'GRID_MAJOR': 'GRID_MAJOR'}.items():
        setattr(ui, name, design.linear(getattr(design, token)))
    ui.WIRE_COLOR = design.rgb01(design.WIRE)[:3]
    ui.WIRE_THEME = design.linear(design.WIRE)[:3]
    ui.GRID_MINOR_RGB, ui.GRID_MAJOR_RGB, ui.GRID_BG_RGB = design.GRID_MINOR, design.GRID_MAJOR, design.VIEWPORT_BG
