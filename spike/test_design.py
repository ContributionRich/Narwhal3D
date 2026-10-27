"""Theme contracts and accessibility, independent of exact v7 fidelity checks.

Normal text (including small secondary/title text) needs 4.5:1. The 3:1 threshold
is for nontext indicators, or genuinely large text (24 CSS px, or 18.67 px bold),
NOT v7's 11-14 px labels. Exact tokens/artwork are tested in test_v4_assets.py.
The owner-authorized 0.4.7 readability adjustments must pass the same gate without
exceptions. Active titles have their own foreground; inactive titles stay white.
Python exercises Blender/UI palette data, not production computation.
"""

import os
from pathlib import Path
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
design_path = Path(ROOT) / "narwhal3d" / "design.py"
design = types.ModuleType("narwhal_design_test")
# Compile source directly: mutation checks must never read stale path-import .pyc.
exec(compile(design_path.read_text(), str(design_path), "exec"), design.__dict__)

failures, checks = [], 0


def check(cond, label):
    global checks
    checks += 1
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        failures.append(label)


def luminance(rgb):
    return (0.2126 * design.srgb_to_linear(rgb[0]) + 0.7152 * design.srgb_to_linear(rgb[1])
            + 0.0722 * design.srgb_to_linear(rgb[2]))


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


check(set(design.THEMES) == {"light", "dark"}, "two themes: light and dark")
check(set(design.THEMES["light"]) == set(design.THEMES["dark"]),
      "both themes define exactly the same tokens")
check(design.DEFAULT_THEME == "light" and design.theme_name({}) == "light",
      "light is the default theme")
check(design.theme_name({"NARWHAL3D_THEME": " Dark "}) == "dark",
      "NARWHAL3D_THEME=dark selects the dark theme")
check(design.theme_name({"NARWHAL3D_THEME": "neon"}) == "light",
      "an unknown theme name falls back to the default")

# Runtime switching is Python UI orchestration, not a renderer/cache refresh.
initial_theme = design.THEME
initial_environ = dict(os.environ)
initial_palettes = {name: dict(tokens) for name, tokens in design.THEMES.items()}
try:
    for name in ("dark", "light", "dark", "light"):
        previous = design.THEME
        changed = design.set_theme(" " + name.upper() + " ")
        check(changed is (previous != name), f"{name}: switch returns a changed bool")
        check(design.THEME == name, f"{name}: switch updates THEME")
        check(all(getattr(design, key) == value for key, value in design.THEMES[name].items()),
              f"{name}: switch refreshes every exported token")
        check(design.set_theme(name) is False, f"{name}: repeated switch is a no-op bool")
        check(design.grid_blend() == ("MULTIPLY" if name == "light" else "ADDITIVE"),
              f"{name}: grid blend follows switched viewport")
        check(design.blender_theme()["wcol_menu_item"]["text"] == design.rgb01(design.TEXT),
              f"{name}: widget theme follows switched tokens")
        check(design.space_colours()["statusbar"] ==
              (design.STATUS_BG, design.BAR_TEXT, design.BAR_TEXT_STRONG),
              f"{name}: editor space colours follow switched tokens")
    for invalid in ("neon", "", "   ", None, 1, [], {}):
        before = dict(design.__dict__)
        try:
            design.set_theme(invalid)
        except ValueError:
            check(True, f"invalid theme {invalid!r} raises ValueError")
        else:
            check(False, f"invalid theme {invalid!r} raises ValueError")
        check(design.__dict__ == before, f"invalid theme {invalid!r} leaves exports unchanged")
    check(design.THEMES == initial_palettes, "switching never mutates the palette tables")
    check(dict(os.environ) == initial_environ, "runtime switching does not change the environment")
finally:
    design.set_theme(initial_theme)

# (text token, background token, minimum ratio, what it is)
PAIRS = [
    ("TEXT", "PANEL_BG", 4.5, "panel text"),
    ("TEXT", "MENU_BG", 4.5, "menu rows"),
    ("TEXT", "LIST_BG", 4.5, "layer rows"),
    ("BAR_TEXT", "BAR_BG", 4.5, "menu bar and status bar"),
    ("BAR_TEXT", "RIBBON_BG", 4.5, "ribbon labels"),
    ("BAR_TEXT", "RIBBON_HOVER", 4.5, "hovered category labels"),
    ("RAIL_ACTIVE_TEXT", "ACCENT_SOFT", 4.5, "the selected inspector tab label"),
    ("CONSOLE_INPUT", "CONSOLE_BG", 4.5, "command input"),
    ("CONSOLE_TEXT", "CONSOLE_BG", 4.5, "command history"),
    ("PROMPT", "CONSOLE_BG", 4.5, "command prompt"),
    ("PROMPT_OPTION", "CONSOLE_BG", 4.5, "command options (.prompt .opt)"),
    ("BAR_TEXT", "BAR_CHIP", 4.5, "status chips"),
    ("BAR_TEXT_STRONG", "ACCENT", 4.5, "hovered menu row, on-chips"),
    ("BAR_TEXT_STRONG", "ACCENT_DEEP", 4.5, "selected category labels"),
    ("ACTIVE_TITLE_TEXT", "ACCENT_TITLE", 4.5, "active viewport title"),
    ("CHIP_TEXT", "TITLE_CHIP_BG", 4.5, "inactive viewport title (12px is body text)"),
    ("TEXT_MUTED", "PANEL_BG", 4.5, "muted panel labels"),
    ("TEXT_MUTED", "OSNAP_BG", 4.5, "Osnap labels"),
    ("TEXT_STRONG", "ACCENT_SOFT", 4.5, "selected layer row"),
    ("VIEWPORT_TEXT", "VIEWPORT_BG", 4.5, "viewport labels"),
    ("WIRE", "VIEWPORT_BG", 3.0, "wireframe curves (nontext)"),
    ("CHIP_TEXT", "TITLE_CHIP_BG", 3.0, "maximize/restore icon (nontext)"),
    ("RAIL_ACTIVE_TEXT", "ACCENT_SOFT", 3.0, "selected rail indicator (nontext)"),
]
for theme, tokens in design.THEMES.items():
    for fg, bg, minimum, what in PAIRS:
        ratio = contrast(tokens[fg], tokens[bg])
        check(ratio >= minimum, f"{theme}: {what} {fg} on {bg} contrast {ratio:.2f} >= {minimum}")
    disabled = contrast(tokens["TEXT_DISABLED"], tokens["MENU_BG"])
    enabled = contrast(tokens["TEXT"], tokens["MENU_BG"])
    check(disabled < enabled, f"{theme}: disabled menu text is visibly weaker than enabled")

# OURS: lower HSV value in 8-bit steps, preserving hue/saturation before rounding.
# Uniform scaling uses round(channel * value / max(original)); select the highest
# passing value, not independent channel edits that can turn the slate purple.
for token, original, background in (
    ("RAIL_ACTIVE_TEXT", (0x26, 0x6C, 0xE2), "ACCENT_SOFT"),
    ("ACCENT_TITLE", (0x27, 0x78, 0xEF), "ACTIVE_TITLE_TEXT"),
    ("TITLE_CHIP_BG", (0x72, 0x83, 0x97), "CHIP_TEXT"),
):
    palette = design.THEMES["light"]
    chosen = palette[token]
    original_value = max(original)
    chosen_value = max(chosen)
    scaled = tuple(round(c * chosen_value / original_value) for c in original)
    brighter = (tuple(round(c * value / original_value) for c in original)
                for value in range(chosen_value + 1, original_value + 1))
    check(chosen_value <= original_value and chosen == scaled
          and contrast(chosen, palette[background]) >= 4.5
          and not any(contrast(rgb, palette[background]) >= 4.5 for rgb in brighter),
          f"light: {token} is the highest uniformly scaled 8-bit value passing 4.5:1")

# Check actual Blender selected-widget colours, not only the token names.
try:
    for name in design.THEMES:
        design.set_theme(name)
        for widget in ("wcol_menu_back", "wcol_menu_item", "wcol_pulldown", "wcol_menu",
                       "wcol_tool", "wcol_toolbar_item", "wcol_regular", "wcol_toggle",
                       "wcol_radio", "wcol_num", "wcol_numslider"):
            colours = design.blender_theme()[widget]
            fg, bg = (tuple(round(c * 255) for c in colours[key][:3])
                      for key in ("text_sel", "inner_sel"))
            check(contrast(fg, bg) >= 4.5, f"{name}: {widget} selected text contrast >= 4.5")
finally:
    design.set_theme(initial_theme)

grid_darker = all(g <= b for g, b in zip(design.THEMES["light"]["GRID_MINOR"],
                                          design.THEMES["light"]["VIEWPORT_BG"]))
grid_lighter = all(g >= b for g, b in zip(design.THEMES["dark"]["GRID_MINOR"],
                                           design.THEMES["dark"]["VIEWPORT_BG"]))
check(grid_darker and grid_lighter,
      "the light grid is darker than its viewport and the dark grid lighter")
theme = design.blender_theme()
check({"wcol_menu_back", "wcol_menu_item", "wcol_pulldown", "wcol_tool", "wcol_option",
       "wcol_text", "wcol_tooltip"} <= set(theme), "the Blender widget theme covers menus, "
      "chips, checkboxes, fields and tooltips")
check(all(isinstance(v, float) and 0.0 <= v <= 1.0
          for w in theme.values() for k, c in w.items() if isinstance(c, tuple) for v in c),
      "every widget colour is a 0..1 float tuple")

print(f"\n{checks - len(failures)}/{checks} design checks passed")
if failures:
    sys.exit(1)
