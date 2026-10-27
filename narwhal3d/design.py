"""Narwhal 3D design tokens -- the single source of the modern interface.

* ``light`` (default) -- v4 palette: slate chrome, light panels and grey viewport.
* ``dark`` -- v7 UI design: charcoal chrome and panels, blue accents.

MEASURED: v4 light colours and v7 charcoal dark colours, including final body CSS.
The requested light-palette override retains the grey viewport; v7 behavior is shared.

These are Blender/UI integration data, not geometry or computational logic.
OURS: release 0.4.7 readability overrides darken only the light rail ink/title fills
and use dark ink on dark-theme accents; surfaces retain the approved v4/v7 palettes.
Small-text contrast is checked independently in spike/test_design.py.

The initial theme comes from ``NARWHAL3D_THEME`` (``light`` or ``dark``).
``set_theme`` changes the exported tokens; callers refresh their own cached colours.
Colours are sRGB bytes; sizes are logical pixels before the interface scale. Every chrome
surface should read its semantic token: in particular the console must not borrow
BAR_TEXT or CONTROL_BG. Pure data: no Blender import.
"""
import os

THEMES = {
    "light": {
        # MEASURED: v4 light tokens, retained by the requested palette override.
        # surfaces
        "APP_BG": (0xD6, 0xDE, 0xE8),          # shell: gaps between panels/viewports
        "BAR_BG": (0x33, 0x41, 0x51),          # menu bar
        "STATUS_BG": (0x35, 0x42, 0x52),       # status bar
        "RIBBON_BG": (0x45, 0x53, 0x64),       # flat category strip, not a gradient
        "RIBBON_HOVER": (0x58, 0x6A, 0x80),    # hovered category button
        "BAR_BORDER": (0x66, 0x77, 0x8C),      # dark chrome borders, NOT the console
        "BAR_CHIP": (0x45, 0x56, 0x6B),        # status chips (off)
        "CONTROL_BG": (0xF2, 0xF5, 0xFA),      # base token; console CSS overrides it
        "COMMAND_FRAME": (0xCF, 0xD9, 0xE5),   # around the console
        "PANEL_BG": (0xF2, 0xF5, 0xFA),        # left tools, inspector panel
        "PANEL_HEADER_BG": (0xE5, 0xEC, 0xF5),
        "RAIL_BG": (0xE8, 0xEE, 0xF6),         # inspector tab rail
        "LIST_BG": (0xF8, 0xFA, 0xFD),
        "CHIP_BG": (0xE5, 0xEB, 0xF4),         # unchecked boxes, buttons on panels
        "FIELD_BG": (0xEF, 0xF3, 0xF9),
        "MENU_BG": (0xFF, 0xFF, 0xFF),
        "TITLE_CHIP_BG": (0x68, 0x78, 0x8A),   # OURS: uniform RGB scale 138/151; white >= 4.5:1
        "VIEWPORT_BORDER": (0x9A, 0xA7, 0xB6),
        "BORDER": (0xD0, 0xD9, 0xE5),
        "BORDER_STRONG": (0x99, 0xAA, 0xBD),
        # accents
        "ACCENT": (0x26, 0x6C, 0xE2),
        "ACCENT_DEEP": (0x26, 0x6C, 0xE2),     # selected category
        "ACCENT_TITLE": (0x25, 0x73, 0xE5),    # OURS: uniform RGB scale 229/239; white >= 4.5:1
        "ACCENT_SOFT": (0xD9, 0xE7, 0xFF),     # selected layer row
        "RAIL_ACTIVE_TEXT": (0x23, 0x62, 0xCE),  # OURS: uniform RGB scale 206/226; ACCENT_SOFT >= 4.5:1
        "TOOL_HOVER": (0xDD, 0xEB, 0xFF),      # headers; tool cells override this
        "TOOL_ICON": (0x23, 0x65, 0xBF),
        # text
        "TEXT": (0x25, 0x33, 0x43),            # ink on panels
        "TEXT_STRONG": (0x20, 0x2B, 0x3A),
        "TEXT_MUTED": (0x52, 0x62, 0x75),
        "TEXT_DISABLED": (0xA3, 0xAE, 0xBB),
        "BAR_TEXT": (0xED, 0xF3, 0xFC),
        "BAR_TEXT_STRONG": (0xFF, 0xFF, 0xFF),
        "BAR_TEXT_MUTED": (0xB8, 0xC4, 0xD2),
        "CHIP_TEXT": (0xFF, 0xFF, 0xFF),
        "ACTIVE_TITLE_TEXT": (0xFF, 0xFF, 0xFF),
        # MEASURED: final prototype_overrides.css, not the base token JSON.
        "CONSOLE_BG": (0xF7, 0xF9, 0xFC),
        "CONSOLE_TEXT": (0x34, 0x44, 0x56),    # history / console-line inherited text
        "CONSOLE_INPUT": (0x16, 0x25, 0x3A),
        "PROMPT": (0x25, 0x39, 0x50),
        "PROMPT_OPTION": (0x25, 0x39, 0x50),   # same ink as prompt; accent only on hover
        "CONSOLE_BORDER": (0xB5, 0xC3, 0xD3),
        "CONSOLE_FRAME_BORDER": (0xA9, 0xB8, 0xCB),  # v4 console bottom edge
        "CONSOLE_FOCUS": (0x32, 0x79, 0xDF),
        "PANEL_OUTLINE": (0xBF, 0xCB, 0xD9),    # v4 panel outline
        "TOOL_CELL_HOVER": (0xD7, 0xE7, 0xFF),
        "OSNAP_BG": (0xF4, 0xF7, 0xFB),
        "LAYER_HOVER": (0xEE, 0xF4, 0xFC),      # non-current rows only
        # viewport and geometry
        "VIEWPORT_BG": (0xAA, 0xB5, 0xC1),
        "VIEWPORT_TEXT": (0x20, 0x2B, 0x3A),
        "GRID_MINOR": (0x9D, 0xA8, 0xB4),      # darker than the surface: MULTIPLY
        "GRID_MAJOR": (0x8F, 0x9B, 0xA8),
        "AXIS_X": (0xDB, 0x59, 0x63),
        "AXIS_Y": (0x68, 0xA5, 0x80),
        "WIRE": (0x00, 0x00, 0x00),
        "POINT": (0x0D, 0x0D, 0x0D),
        "PREVIEW": (0x1A, 0x1A, 0x1A),
        "CONTROL_POINT": (0x1A, 0x1A, 0x8C),
        "CONTROL_POLYGON": (0x38, 0x38, 0x40),
        "SUBOBJECT_PREVIEW": (0x38, 0x3D, 0x45),
    },
    "dark": {
        # MEASURED: charcoal JSON overrides, then final body CSS semantics.
        "APP_BG": (0x2B, 0x2B, 0x2B),
        "BAR_BG": (0x3A, 0x3A, 0x3A),
        "STATUS_BG": (0x3A, 0x3A, 0x3A),
        "RIBBON_BG": (0x4A, 0x4A, 0x4A),
        "RIBBON_HOVER": (0x5A, 0x5A, 0x5A),
        "BAR_BORDER": (0x5F, 0x5F, 0x5F),
        "BAR_CHIP": (0x4A, 0x4A, 0x4A),
        "CONTROL_BG": (0x2F, 0x2F, 0x2F),
        "COMMAND_FRAME": (0x24, 0x24, 0x24),
        "PANEL_BG": (0x33, 0x33, 0x33),
        "PANEL_HEADER_BG": (0x3B, 0x3B, 0x3B),
        "RAIL_BG": (0x31, 0x31, 0x31),
        "LIST_BG": (0x2F, 0x2F, 0x2F),
        "CHIP_BG": (0x44, 0x44, 0x44),
        "FIELD_BG": (0x2B, 0x2B, 0x2B),
        "MENU_BG": (0x3A, 0x3A, 0x3A),
        "TITLE_CHIP_BG": (0x5E, 0x5E, 0x5E),
        "VIEWPORT_BORDER": (0x6C, 0x6C, 0x6C),
        "BORDER": (0x4B, 0x4B, 0x4B),
        "BORDER_STRONG": (0x5E, 0x5E, 0x5E),
        "ACCENT": (0x4E, 0xA1, 0xFF),
        "ACCENT_DEEP": (0x4E, 0xA1, 0xFF),
        "ACCENT_TITLE": (0x6B, 0xB3, 0xFF),
        "ACCENT_SOFT": (0x20, 0x32, 0x48),
        "RAIL_ACTIVE_TEXT": (0x4E, 0xA1, 0xFF),  # CSS ACCENT, not white
        "TOOL_HOVER": (0x45, 0x45, 0x45),
        "TOOL_ICON": (0x12, 0x5B, 0xF0),
        "TEXT": (0xD0, 0xD0, 0xD0),
        "TEXT_STRONG": (0xF0, 0xF0, 0xF0),
        "TEXT_MUTED": (0xA3, 0xA3, 0xA3),
        "TEXT_DISABLED": (0x70, 0x70, 0x70),
        "BAR_TEXT": (0xD8, 0xD8, 0xD8),
        "BAR_TEXT_STRONG": (0x15, 0x25, 0x3A),  # OURS: ink on the preserved bright accents
        "BAR_TEXT_MUTED": (0xA2, 0xA8, 0xB3),
        "CHIP_TEXT": (0xFF, 0xFF, 0xFF),
        "ACTIVE_TITLE_TEXT": (0x15, 0x25, 0x3A),  # OURS: not the inactive charcoal title's white
        # MEASURED: body[data-theme="dark"] rules override the base console tokens.
        "CONSOLE_BG": (0x26, 0x26, 0x26),
        "CONSOLE_TEXT": (0xD9, 0xD9, 0xD9),
        "CONSOLE_INPUT": (0xF0, 0xF0, 0xF0),
        "PROMPT": (0xE6, 0xE6, 0xE6),
        "PROMPT_OPTION": (0xE6, 0xE6, 0xE6),   # base .prompt .opt uses BAR_TEXT_STRONG
        "CONSOLE_BORDER": (0x51, 0x51, 0x51),
        "CONSOLE_FRAME_BORDER": (0x5E, 0x5E, 0x5E),  # CSS BORDER_STRONG
        "CONSOLE_FOCUS": (0x4E, 0xA1, 0xFF),
        "PANEL_OUTLINE": (0x4B, 0x4B, 0x4B),    # CSS BORDER
        "TOOL_CELL_HOVER": (0x45, 0x45, 0x45),
        "OSNAP_BG": (0x30, 0x30, 0x30),
        "LAYER_HOVER": (0x3A, 0x3A, 0x3A),
        "VIEWPORT_BG": (0x2C, 0x2C, 0x2C),
        "VIEWPORT_TEXT": (0xD7, 0xDE, 0xE7),
        "GRID_MINOR": (0x3A, 0x3A, 0x3A),      # lighter than the surface: ADDITIVE
        "GRID_MAJOR": (0x51, 0x51, 0x51),
        "AXIS_X": (0xC8, 0x3A, 0x3A),
        "AXIS_Y": (0x3A, 0xB8, 0x4A),
        "WIRE": (0xC9, 0xD1, 0xDB),
        "POINT": (0xE6, 0xEA, 0xF0),
        "PREVIEW": (0xFF, 0xFF, 0xFF),
        "CONTROL_POINT": (0x73, 0xA6, 0xFF),
        "CONTROL_POLYGON": (0x9E, 0xAD, 0xC2),
        "SUBOBJECT_PREVIEW": (0xC7, 0xD1, 0xE0),
    },
}

DEFAULT_THEME = "light"


def theme_name(environ=None):
    """The configured theme, falling back to the default for an unknown value."""
    name = (environ if environ is not None else os.environ).get("NARWHAL3D_THEME", "")
    name = name.strip().lower()
    if name in THEMES:
        return name
    if environ is None:
        try:
            from . import preferences
            return preferences.get('theme')
        except ImportError:
            pass  # Standalone token probes have no application package.
    return DEFAULT_THEME


THEME = theme_name()
globals().update(THEMES[THEME])


def set_theme(name):
    """OURS: select light/dark at runtime; return whether the theme changed.

    Names are normalized like the startup setting. Invalid names raise ValueError
    without changing state. This updates only this module's exports, not the
    environment or other modules' caches; the UI caller must refresh those.
    """
    global THEME
    if not isinstance(name, str) or name.strip().lower() not in THEMES:
        raise ValueError("theme must be 'light' or 'dark'")
    name = name.strip().lower()
    changed = name != THEME
    globals().update(THEMES[name])
    THEME = name
    return changed

# ---------------------------------------------------------------------------
# sizes (logical px) -- identical in both themes
# ---------------------------------------------------------------------------

# MEASURED: v7 metrics.design plus the final CSS. The console is 65 px INSIDE
# its two 5 px margins, hence a 75 px outer grid row.
MENUBAR_H = 26
CATEGORY_H = 78            # compact category buttons, icon over label -- NOT a command ribbon
CATEGORY_BUTTON_W = 80     # flex 80..120 px
COMMAND_BOX_H = 65
COMMAND_MARGIN = 5
GAP = 5                    # final .workspace gap AND padding
VIEWPORT_GAP = 4           # .viewports still uses the base --gap (not workspace CSS)
RADIUS = 6                 # console
PANEL_RADIUS = 4           # side panels
VIEWPORT_RADIUS = 5
VIEWPORT_BORDER_W = 2      # every viewport is outlined; the active one in the accent
CHIP_RADIUS = 4            # status chips, title chips, menu rows
LEFT_PANEL_W = 126
TOOL_CELL = 35             # left palette cell (3 columns, 3 px gaps)
RIGHT_RAIL_W = 72          # icon + label inspector tabs, min height 63
RIGHT_PANEL_W = 208        # inspector 280 px = 72 rail + 208 panel
TITLE_CHIP_INSET = 6       # from the viewport's top-left corner
TITLE_CHIP_PAD_X = 10
TITLE_CHIP_PAD_Y = 6
STATUS_H = 37
STATUS_CHIP_PAD = 8

# Final CSS / base metrics needed by the v7 shell integrator (logical px).
CONSOLE_H = COMMAND_BOX_H + 2 * COMMAND_MARGIN
CONSOLE_INPUT_FONT = 14
PROMPT_FONT = 13
CONSOLE_HISTORY_WEIGHT = 500
PROMPT_WEIGHT = 650
CONSOLE_FOCUS_W = 2
TOOL_GAP = 3
CATEGORY_GAP = 3
CATEGORY_PAD_X = 7
CATEGORY_PAD_Y = 5
CATEGORY_RADIUS = 6
RAIL_ACTIVE_WEIGHT = 650
RAIL_ICON_RADIUS = 8
RAIL_ICON_SIZE = 20        # metrics.rightpanel.TAB_ICON
VIEWPORT_CONTROL_SIZE = 20
VIEWPORT_CONTROL_ICON_SIZE = 13
FLYOUT_CELL = 32           # v7 openFlyout cell width/height
FLYOUT_ICON = 22           # owner-approved v7final compact flyout icon
COORDINATE_SLOT_W = 242    # owner-approved v7final fixed status readout slot


def rgb01(rgb, alpha=1.0):
    """An sRGB byte triple as 0..1 floats -- Blender UI widget theme colours and
    immediate-mode `gpu` drawing (neither is colour-managed)."""
    return (rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0, alpha)


def srgb_to_linear(component):
    c = component / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def linear(rgb, alpha=1.0):
    """An sRGB byte triple as LINEAR floats -- colour-managed viewport colours."""
    return (srgb_to_linear(rgb[0]), srgb_to_linear(rgb[1]), srgb_to_linear(rgb[2]), alpha)


def grid_blend():
    """`MULTIPLY` when the grid is darker than the viewport, else `ADDITIVE`: each paints
    the background exactly the grid colour while barely changing a curve on top."""
    darker = all(g <= b for g, b in zip(GRID_MINOR, VIEWPORT_BG))
    return "MULTIPLY" if darker else "ADDITIVE"


# ---------------------------------------------------------------------------
# Blender widget theme (pure data; `ui.apply_menu_theme` writes it)
# ---------------------------------------------------------------------------

def _widget(inner, inner_sel, text, text_sel, outline=None, item=None, roundness=0.3,
            inner_alpha=1.0):
    outline = BORDER if outline is None else outline
    return {"inner": rgb01(inner, inner_alpha), "inner_sel": rgb01(inner_sel),
            "outline": rgb01(outline), "outline_sel": rgb01(outline),
            "item": rgb01(item or text), "text": rgb01(text), "text_sel": rgb01(text_sel),
            "roundness": roundness, "show_shaded": False}


def blender_theme():
    """{user_interface widget: {property: value}} for the active theme."""
    menu_row = _widget(MENU_BG, ACCENT, TEXT, BAR_TEXT_STRONG, outline=MENU_BG,
                       roundness=0.2, inner_alpha=0.0)
    menu_row["outline"] = rgb01(MENU_BG, 0.0)
    menu_row["outline_sel"] = rgb01(MENU_BG, 0.0)
    # menu-bar titles sit on the dark bar: light text, accent when open
    pulldown = _widget(BAR_BG, ACCENT_DEEP, BAR_TEXT, BAR_TEXT_STRONG, outline=BAR_BG,
                       roundness=0.2, inner_alpha=0.0)
    pulldown["outline"] = rgb01(BAR_BG, 0.0)
    pulldown["outline_sel"] = rgb01(BAR_BG, 0.0)
    return {
        # drop-down / popup panel: separators use `item`, disabled rows `text`
        "wcol_menu_back": _widget(MENU_BG, ACCENT, TEXT, BAR_TEXT_STRONG,
                                  outline=BORDER_STRONG, item=BORDER, roundness=0.15),
        "wcol_menu_item": menu_row,
        "wcol_pulldown": pulldown,
        "wcol_menu": _widget(TITLE_CHIP_BG, ACCENT, CHIP_TEXT, BAR_TEXT_STRONG),
        # chips on the dark chrome: status bar panes, toolbar-strip buttons
        "wcol_tool": _widget(BAR_CHIP, ACCENT, BAR_TEXT, BAR_TEXT_STRONG, outline=BAR_BORDER),
        "wcol_toolbar_item": _widget(RIBBON_BG, ACCENT_DEEP, BAR_TEXT, BAR_TEXT_STRONG,
                                     outline=RIBBON_BG),
        # controls on the panels
        "wcol_regular": _widget(CHIP_BG, ACCENT, TEXT, BAR_TEXT_STRONG),
        "wcol_toggle": _widget(CHIP_BG, ACCENT, TEXT, BAR_TEXT_STRONG),
        "wcol_radio": _widget(CHIP_BG, ACCENT, TEXT, BAR_TEXT_STRONG),
        "wcol_option": _widget(CHIP_BG, ACCENT, TEXT_MUTED, TEXT, item=BAR_TEXT_STRONG),
        "wcol_text": _widget(FIELD_BG, FIELD_BG, TEXT, TEXT_STRONG),
        "wcol_num": _widget(FIELD_BG, ACCENT, TEXT, BAR_TEXT_STRONG),
        "wcol_numslider": _widget(FIELD_BG, ACCENT, TEXT, BAR_TEXT_STRONG),
        "wcol_box": _widget(PANEL_BG, PANEL_BG, TEXT, TEXT_STRONG),
        "wcol_list_item": _widget(LIST_BG, ACCENT_SOFT, TEXT, TEXT_STRONG),
        "wcol_tooltip": _widget(MENU_BG, MENU_BG, TEXT, TEXT_STRONG,
                                outline=BORDER_STRONG, roundness=0.2),
    }


def space_colours():
    """{Blender editor theme space: (background, text, strong text)}."""
    return {
        "topbar": (BAR_BG, BAR_TEXT, BAR_TEXT_STRONG),        # menu bar
        "info": (RIBBON_BG, BAR_TEXT, BAR_TEXT_STRONG),       # toolbar strip
        "statusbar": (STATUS_BG, BAR_TEXT, BAR_TEXT_STRONG),
        "view_3d": (APP_BG, TEXT, TEXT_STRONG),
    }
