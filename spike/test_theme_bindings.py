"""Lightweight UI-adapter contracts; no bpy, addon registration or geometry imports.

Python is required to exercise the Python UI namespace bindings. Run directly, or
with --mutations to re-run these assertions against in-memory broken bindings.
assert_palette is also used against the REAL namespaces by test_v4_live.py.
"""
import ast
import copy
import io
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

# Independent semantic expectations, not a copy read from the implementation at runtime.
UI_RGB = {
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
    'SIDEBAR_TOOLTIP_TEXT': 'TEXT', 'TAB_ROW_BG': 'RIBBON_BG',
    'TAB_ROW_ACTIVE_BG': 'ACCENT_DEEP', 'TAB_ROW_TEXT': 'BAR_TEXT',
    'TAB_ROW_ACTIVE_TEXT': 'BAR_TEXT_STRONG', 'POINT_OBJECT_COLOR': 'POINT',
    'CONTROL_POINT_COLOR': 'CONTROL_POINT', 'CONTROL_POLYGON_COLOR': 'CONTROL_POLYGON',
}
VIEW_RGB = {
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


def assert_palette(ui, views, design):
    """Check caches independently, including GPU sRGB versus viewport linear arity."""
    for name, token in UI_RGB.items():
        expected = design.rgb01(getattr(design, token), .9 if name == 'CONTROL_POLYGON_COLOR' else 1)
        assert getattr(ui, name) == expected, (design.THEME, name, expected, getattr(ui, name))
    for name, token in VIEW_RGB.items():
        assert getattr(views, name) == getattr(design, token), (design.THEME, name, token)
    for name, token in {'VIEWPORT_BG': 'VIEWPORT_BG', 'GRID_COLOR': 'GRID_MINOR', 'GRID_MAJOR': 'GRID_MAJOR'}.items():
        assert getattr(ui, name) == design.linear(getattr(design, token)), (design.THEME, name)
    assert ui.WIRE_COLOR == design.rgb01(design.WIRE)[:3], 'wire GPU sRGB'
    assert ui.WIRE_THEME == design.linear(design.WIRE)[:3], 'wire theme linear RGB'
    assert (ui.GRID_MINOR_RGB, ui.GRID_MAJOR_RGB, ui.GRID_BG_RGB) == (design.GRID_MINOR, design.GRID_MAJOR, design.VIEWPORT_BG)
    assert design.VIEWPORT_BG == ((170, 181, 193) if design.THEME == 'light' else (44, 44, 44)), 'v4 grey / charcoal viewport'


def filtered_caption_rows(rows):
    return [{**row, **({'children': filtered_caption_rows(row['children'])} if 'children' in row else {})}
            for row in rows if row.get('enabled', True)]


MUTATION = None


def source_for(name):
    source = (ROOT / 'narwhal3d' / (name + '.py')).read_text()
    if MUTATION and MUTATION[0] == name:
        _, old, new = MUTATION
        assert old in source, 'mutation target has drifted: ' + old
        source = source.replace(old, new, 1)
    return source


def load_modules():
    """Fresh source compilation avoids addon __init__ and stale mutation bytecode."""
    package = types.ModuleType('_theme_binding_test')
    package.__path__ = []
    modules = {package.__name__: package}
    for name in ('design', 'theme_bindings'):
        path = ROOT / 'narwhal3d' / (name + '.py')
        source = source_for(name)
        module = types.ModuleType(package.__name__ + '.' + name)
        module.__package__ = package.__name__
        modules[module.__name__] = module
        setattr(package, name, module)
        with patch.dict(sys.modules, modules):
            exec(compile(source, str(path), 'exec'), module.__dict__)
    return package.design, package.theme_bindings


class ThemeBindingsTests(unittest.TestCase):
    def test_round_trip_semantics_and_noncolour_state(self):
        design, bindings = load_modules()
        # Equal-valued non-colours must not be rewritten by a value-based sweep.
        ui = types.SimpleNamespace(input_buffer='abcéd', active_category='Curve', panel_tab='Layers',
                                   search='V4-row-063', cameras={'Front': ('Wireframe', (1, 2, 3))},
                                   equal_colour=(1., 1., 1., 1.))
        views = types.SimpleNamespace(TAB_H=29, camera=(1., 1., 1.), state=['not a palette'])
        original_ui, original_views = copy.deepcopy(vars(ui)), copy.deepcopy(vars(views))
        camera_identity = ui.cameras
        for theme in ('light', 'dark', 'light'):
            design.set_theme(theme)
            bindings.refresh(ui, views)
            assert_palette(ui, views, design)
            self.assertEqual({k: getattr(ui, k) for k in original_ui}, original_ui)
            self.assertEqual({k: getattr(views, k) for k in original_views}, original_views)
            self.assertIs(ui.cameras, camera_identity)
            before = (vars(ui).copy(), vars(views).copy())
            bindings.refresh(ui, views)
            self.assertEqual((vars(ui), vars(views)), before, 'refresh is idempotent')

    def test_semantics_do_not_depend_on_current_equal_colours(self):
        design, bindings = load_modules()
        # Distinct synthetic tokens expose a wrong alias even when approved tokens coincide.
        for index, token in enumerate(design.THEMES['light']):
            setattr(design, token, (index, index + 1, index + 2))
        design.VIEWPORT_BG = (170, 181, 193)
        design.THEME = 'light'
        ui, views = types.SimpleNamespace(), types.SimpleNamespace()
        bindings.refresh(ui, views)
        assert_palette(ui, views, design)

    def test_startup_title_aliases(self):
        design, _ = load_modules()
        names = ('ACTIVE_TITLE_FILL', 'ACTIVE_TITLE_TEXT',
                 'INACTIVE_TITLE_FILL', 'INACTIVE_TITLE_TEXT')
        tree = ast.parse(source_for('views'))
        aliases = [node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id in names
                           for target in node.targets)]
        code = compile(ast.Module(body=aliases, type_ignores=[]), 'views.py', 'exec')
        for theme in ('light', 'dark'):
            design.set_theme(theme)
            namespace = {'design': design}
            exec(code, namespace)
            for name in names:
                self.assertEqual(namespace[name], getattr(design, VIEW_RGB[name]),
                                 (theme, name))

    def test_view_menu_filtered_checkmark(self):
        # Execute only this small adapter with a recording layout; never import bpy.
        tree = ast.parse(source_for('ui'))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_draw_view_preferences')
        namespace = {}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'ui.py', 'exec'), namespace)
        for show in (True, False):
            calls = []
            layout = types.SimpleNamespace(separator=lambda: None,
                menu=lambda *a, **kw: calls.append((a, kw)),
                operator=lambda *a, **kw: calls.append((a, kw)))
            namespace['_show_unavailable'] = show
            namespace['_draw_view_preferences'](layout)
            self.assertEqual(calls, [(('NARWHAL_MT_shell_theme',), {'text': 'Theme'}),
                (('narwhal.show_unavailable',), {'text': 'Show Unavailable', 'icon': 'BLANK1' if show else 'CHECKMARK'})])

    def test_status_draw_retains_disabled_cells(self):
        design, _ = load_modules()
        sbar = types.ModuleType('status_model')
        exec(compile(source_for('statusbar'), 'statusbar.py', 'exec'), sbar.__dict__)
        tree = ast.parse(source_for('ui'))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_draw_narwhal_statusbar')
        namespace = dict(design=design, sbar=sbar, statusbar_state=lambda: {},
                         STATUSBAR_UNIMPLEMENTED={'smarttrack', 'record_history'},
                         STATUSBAR_UNITS_PER_CHAR=.5, _fpx=lambda x: x, _clip_text=lambda text, *a: text)
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'ui.py', 'exec'), namespace)

        class Layout:
            def __init__(self):
                self.children = []
                self.calls = []

            def row(self, **kw):
                child = Layout()
                self.children.append(child)
                return child

            def operator(self, *a, **kw):
                op = types.SimpleNamespace()
                self.calls.append((a, kw, op))
                return op

            menu = label = operator

        for show in (True, False):
            namespace['_show_unavailable'] = show
            layout = Layout()
            namespace['_draw_narwhal_statusbar'](types.SimpleNamespace(layout=layout), None)
            cells = layout.children[0].children
            self.assertEqual(len(cells), len(sbar.panes()))
            for pane, cell in zip(sbar.panes(), cells):
                self.assertTrue(cell.calls, pane.id + ' was not rendered')
                self.assertEqual(cell.active, pane.id not in namespace['STATUSBAR_UNIMPLEMENTED'])
                if pane.id == 'coordinates':
                    self.assertEqual(cell.ui_units_x, 242 / 20)
                elif pane.id in ('units', 'tolerance'):
                    expected = 'NARWHAL_MT_status_units' if pane.id == 'units' else 'NARWHAL_MT_status_tolerance'
                    self.assertEqual(cell.calls[0][0], (expected,))
                elif pane.clickable:
                    self.assertEqual(cell.calls[0][2].pane, pane.id)


def run_tests(stream):
    return unittest.TextTestRunner(stream=stream, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(ThemeBindingsTests))


if __name__ == '__main__':
    result = run_tests(sys.stdout)
    if not result.wasSuccessful():
        sys.exit(1)
    if '--mutations' in sys.argv:
        for label, target, old, new in (
            ('console borrowed chrome', 'theme_bindings', "'CMD_TEXT': 'PROMPT'", "'CMD_TEXT': 'BAR_TEXT'"),
            ('equal-valued semantic mixup', 'theme_bindings', "'ACTIVE_TITLE_TEXT': 'ACTIVE_TITLE_TEXT'", "'ACTIVE_TITLE_TEXT': 'BAR_TEXT_STRONG'"),
                        ('active title uses inactive white', 'theme_bindings', "'ACTIVE_TITLE_TEXT': 'ACTIVE_TITLE_TEXT'", "'ACTIVE_TITLE_TEXT': 'CHIP_TEXT'"),
                        ('startup active title uses inactive white', 'views', 'ACTIVE_TITLE_TEXT = design.ACTIVE_TITLE_TEXT', 'ACTIVE_TITLE_TEXT = design.CHIP_TEXT'),
            ('stale view aliases', 'theme_bindings', 'setattr(views, name, getattr(design, token))', 'setattr(views, name, design.THEMES["light"][token])'),
            ('viewport wrong colour space', 'theme_bindings', 'design.linear(getattr(design, token))', 'design.rgb01(getattr(design, token))'),
            ('command state overwritten', 'theme_bindings', 'def refresh(ui, views):', "def refresh(ui, views):\n    ui.input_buffer = ''"),
            ('checkbox polarity reversed', 'ui', "icon='CHECKMARK' if not _show_unavailable", "icon='CHECKMARK' if _show_unavailable"),
            ('unavailable status rows hidden', 'ui', 'for pane in sbar.panes():',
             'for pane in sbar.panes():\n        if not _show_unavailable and pane.id in STATUSBAR_UNIMPLEMENTED:\n            continue'),
        ):
            MUTATION = (target, old, new)
            log = io.StringIO()
            broken = run_tests(log)
            if not broken.failures or broken.errors:
                print(log.getvalue())
                raise AssertionError('mutation must fail assertions (not crash): ' + label)
            print('RED confirmed: ' + label)
        MUTATION = None
        if not run_tests(sys.stdout).wasSuccessful():
            sys.exit(1)
