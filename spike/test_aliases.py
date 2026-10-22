"""Focused tests for built-in and editable command aliases."""

import importlib.util
import os
import sys
import types
from types import MappingProxyType


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "narwhal3d")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Stand up the package without importing its UI entry point. This keeps the
# resolver checks headless while loading the same modules used at runtime.
_package = types.ModuleType("narwhal3d")
_package.__path__ = [ADDON]
_package.__package__ = "narwhal3d"
sys.modules["narwhal3d"] = _package

for _name in ("bridge", "options", "cplane", "coords", "cmdbar", "model"):
    _load(f"narwhal3d.{_name}", os.path.join(ADDON, f"{_name}.py"))
commands = _load("narwhal3d.commands", os.path.join(ADDON, "commands.py"))
alias_table = _load("narwhal3d.alias_table", os.path.join(ADDON, "alias_table.py"))
state = _load("narwhal3d.state", os.path.join(ADDON, "state.py"))


EXPECTED_MACROS = {
    "Z": "'_Zoom",
    "ZE": "'_Zoom _Extents",
    "ZT": "'_Zoom _Target",
    "ZEA": "'_Zoom _All _Extents",
    "ZS": "'_Zoom _Selected",
    "ZSA": "'_Zoom _All _Selected",
    "S": "'_Snap",
    "O": "'_Ortho",
    "P": "'_Planar",
    "M": "! _Move",
    "U": "_Undo",
    "POn": "! _PointsOn",
    "POff": "! _PointsOff",
    "C": "'_SelCrossing",
    "W": "'_SelWindow",
    "COn": "'_CurvatureGraph",
    "COff": "'_CurvatureGraphOff",
    "Break": "! _DeleteSubCrv",
    "SelPolysurface": "'_SelPolysrf",
    "PlugInManager": "! _OptionsPage _PlugIns",
    "AdvancedDisplay": "! _OptionsPage _DisplayModes",
    "DisplayAttrsMgr": "! _OptionsPage _DisplayModes",
}

EXPECTED_PARTS = {
    "z": ("'", "Zoom", ()),
    "ze": ("'", "Zoom", ("Extents",)),
    "zt": ("'", "Zoom", ("Target",)),
    "zea": ("'", "Zoom", ("All", "Extents")),
    "zs": ("'", "Zoom", ("Selected",)),
    "zsa": ("'", "Zoom", ("All", "Selected")),
    "s": ("'", "Snap", ()),
    "o": ("'", "Ortho", ()),
    "p": ("'", "Planar", ()),
    "m": ("!", "Move", ()),
    "u": ("_", "Undo", ()),
    "pon": ("!", "PointsOn", ()),
    "poff": ("!", "PointsOff", ()),
    "c": ("'", "SelCrossing", ()),
    "w": ("'", "SelWindow", ()),
    "con": ("'", "CurvatureGraph", ()),
    "coff": ("'", "CurvatureGraphOff", ()),
    "break": ("!", "DeleteSubCrv", ()),
    "selpolysurface": ("'", "SelPolysrf", ()),
    "pluginmanager": ("!", "OptionsPage", ("PlugIns",)),
    "advanceddisplay": ("!", "OptionsPage", ("DisplayModes",)),
    "displayattrsmgr": ("!", "OptionsPage", ("DisplayModes",)),
}

IMPLEMENTED_TARGETS = {
    "Zoom",
    "Move",
    "Undo",
    "PointsOn",
    "PointsOff",
    "SelPolysrf",
    "Ortho",
    "Snap",
}

results = []


def check(name):
    def decorate(function):
        try:
            function()
            results.append((name, "PASS", ""))
        except Exception as exc:  # noqa: BLE001 - each check must be reported
            results.append((name, "FAIL", repr(exc)))
        return function
    return decorate


def assert_raises(exception_type, function, *args):
    try:
        function(*args)
    except exception_type as exc:
        return exc
    except Exception as exc:  # noqa: BLE001 - produce a useful assertion failure
        raise AssertionError(
            f"expected {exception_type.__name__}, got {type(exc).__name__}: {exc}"
        ) from exc
    raise AssertionError(f"expected {exception_type.__name__}")


def empty_table():
    table = alias_table.AliasTable()
    for alias in table.aliases:
        assert table.delete(alias.name)
    return table


def fresh_state():
    return state.AppState()


@check("the application provides exactly the expected built-in macros")
def _():
    assert isinstance(alias_table.DEFAULT_MACROS, MappingProxyType)
    assert dict(alias_table.DEFAULT_MACROS) == EXPECTED_MACROS
    assert len(alias_table.DEFAULT_MACROS) == 22
    assert set(alias_table.ALIAS_TABLE) == {
        name.casefold() for name in EXPECTED_MACROS
    }


@check("every built-in macro preserves and parses its command behavior")
def _():
    for name, macro in EXPECTED_MACROS.items():
        alias = alias_table.resolve_alias(name)
        expected_prefix, expected_command, expected_options = EXPECTED_PARTS[
            name.casefold()
        ]
        assert alias is not None, name
        assert alias.name == name, name
        assert alias.macro == macro, name
        assert alias.prefix == expected_prefix, name
        assert alias.command == expected_command, name
        assert alias.options == expected_options, name


@check("built-in lookup is exact and case-insensitive")
def _():
    for name in EXPECTED_MACROS:
        expected = alias_table.resolve_alias(name)
        for typed in (name.lower(), name.upper(), name.swapcase(), f" {name} "):
            assert alias_table.resolve_alias(typed) is expected, (name, typed)
    assert alias_table.resolve_alias("ZEX") is None
    assert alias_table.resolve_alias("") is None
    assert alias_table.resolve_alias(None) is None


@check("ZS is nested Zoom Selected and reaches the command resolver")
def _():
    alias = alias_table.resolve_alias("zS")
    assert alias.macro == "'_Zoom _Selected"
    assert alias.prefix == "'"
    assert alias.command == "Zoom"
    assert alias.options == ("Selected",)

    app = fresh_state()
    key, command = app.resolve_command("ZS")
    assert key == "zoom"
    assert command is not None and command.name == "Zoom"
    assert app.alias_inline_options("zS") == "Selected"


@check("the inline resolver feeds the option carried by ZS")
def _():
    original = state.COMMANDS["zoom"]

    class CapturingZoom:
        name = "Zoom"
        description = "Capture an inline option."

        def __init__(self):
            self.received = []

        def feed(self, text, context):
            self.received.append(text)
            return None, False

    state.COMMANDS["zoom"] = CapturingZoom()
    try:
        app = fresh_state()
        app.start_command("ZS")
        assert app.active_command is not None
        assert app.active_command.received == ["Selected"]
        assert app.last_command == "zoom"
    finally:
        state.COMMANDS["zoom"] = original


@check("implemented statuses agree with the live command registry")
def _():
    aliases = tuple(alias_table.ALIAS_TABLE.values())
    marked = {alias.command for alias in aliases if alias.implemented}
    registered = {
        alias.command
        for alias in aliases
        if alias.command.casefold() in commands.COMMANDS
    }
    assert marked == IMPLEMENTED_TARGETS
    assert registered == IMPLEMENTED_TARGETS
    for alias in aliases:
        assert alias.implemented == (alias.command in IMPLEMENTED_TARGETS), alias.name
        expected_status = "implemented" if alias.implemented else "not implemented yet"
        assert alias.status == expected_status, alias.name


@check("every implemented built-in alias resolves to its target")
def _():
    app = fresh_state()
    for alias in alias_table.ALIAS_TABLE.values():
        if not alias.implemented:
            continue
        key, command = app.resolve_command(alias.name.swapcase())
        assert key == alias.command.casefold(), alias.name
        assert command is not None and command.name == alias.command, alias.name


@check("unavailable built-in targets report honestly")
def _():
    unavailable = [
        alias for alias in alias_table.ALIAS_TABLE.values() if not alias.implemented
    ]
    assert unavailable
    for alias in unavailable:
        assert alias.status == "not implemented yet", alias.name
        assert "not implemented yet" in alias.message, alias.name
        assert alias.command in alias.message, alias.name

    app = fresh_state()
    alias = alias_table.resolve_alias("PlugInManager")
    assert app.resolve_command(alias.name) == (None, None)
    assert app.alias_message(alias.name) == alias.message
    app.feed_line(alias.name)
    joined = " ".join(app.history)
    assert "not implemented yet" in joined
    assert alias.command in joined
    assert "Unknown command" not in joined


@check("DEFAULT_MACROS is immutable")
def _():
    def attempt_mutation():
        alias_table.DEFAULT_MACROS["Quick"] = "_Move"

    assert_raises(TypeError, attempt_mutation)
    assert dict(alias_table.DEFAULT_MACROS) == EXPECTED_MACROS


@check("a new editable table starts with independent built-in defaults")
def _():
    table = alias_table.AliasTable()
    assert len(table.aliases) == 22
    assert table.export_text()
    assert table.lookup("z").macro == "'_Zoom"
    table.delete("Z")
    assert table.lookup("z") is None
    assert alias_table.resolve_alias("z") is not None


@check("supported macro forms parse into prefix, command, and options")
def _():
    cases = {
        "'_Inspect _All _Details": ("'", "Inspect", ("All", "Details")),
        "! _Move": ("!", "Move", ()),
        "_Undo": ("_", "Undo", ()),
        "Review _Selected": (None, "Review", ("Selected",)),
    }
    for macro, expected in cases.items():
        assert alias_table.parse_macro(macro) == expected, macro


@check("invalid macros are rejected instead of partially parsed")
def _():
    for macro in ("", "!!!", "?Command", None):
        assert alias_table.parse_macro(macro) == (None, None, None), macro


@check("create preserves fields and lookup is exact and case-insensitive")
def _():
    table = alias_table.AliasTable()
    created = table.create("QuickInspect", "'_Inspect _Selected")
    assert created.name == "QuickInspect"
    assert created.macro == "'_Inspect _Selected"
    assert created.prefix == "'"
    assert created.command == "Inspect"
    assert created.options == ("Selected",)
    for typed in ("quickinspect", "QUICKINSPECT", " QuickInspect "):
        assert table.lookup(typed) is created, typed
        assert table.resolve_alias(typed) is created, typed
    assert table.lookup("Quick") is None
    assert table.lookup("") is None
    assert table.lookup(None) is None


@check("create rejects case-insensitive collisions without replacing the alias")
def _():
    table = alias_table.AliasTable()
    original = table.create("Review", "_Move")
    error = assert_raises(ValueError, table.create, "rEvIeW", "_Undo")
    assert "collision" in str(error)
    assert table.lookup("review") is original
    assert len(table.aliases) == 23


@check("create validates names and macros")
def _():
    table = alias_table.AliasTable()
    invalid_entries = (
        ("", "_Move"),
        ("two words", "_Move"),
        ("bad=name", "_Move"),
        ("bad\nname", "_Move"),
        (None, "_Move"),
        ("EmptyMacro", ""),
        ("LineMacro", "_Move\n_Undo"),
        ("UnknownSyntax", "!!!"),
        ("NonTextMacro", None),
    )
    for name, macro in invalid_entries:
        assert_raises(ValueError, table.create, name, macro)
    assert len(table.aliases) == 22


@check("aliases are sorted deterministically and exposed as a tuple")
def _():
    table = empty_table()
    table.create("zulu", "_Move")
    table.create("Alpha", "_Undo")
    table.create("middle", "_Inspect")
    assert isinstance(table.aliases, tuple)
    assert [alias.name for alias in table.aliases] == ["Alpha", "middle", "zulu"]


@check("delete is case-insensitive and reports whether an alias existed")
def _():
    table = alias_table.AliasTable()
    table.create("Temporary", "_Move")
    assert table.delete(" temporary ") is True
    assert table.lookup("Temporary") is None
    assert table.delete("TEMPORARY") is False
    assert table.delete(None) is False


@check("editable tables do not share custom aliases")
def _():
    first = alias_table.AliasTable()
    second = alias_table.AliasTable()
    first.create("OnlyHere", "_Move")
    assert first.lookup("onlyhere") is not None
    assert second.lookup("onlyhere") is None
    assert alias_table.resolve_alias("onlyhere") is None


@check("export is sorted and preserves custom macro text")
def _():
    table = empty_table()
    table.create("Second", "! _Move")
    table.create("first", "'_Inspect _All")
    assert table.export_text() == "first='_Inspect _All\nSecond=! _Move\n"


@check("valid text import ignores comments and blank lines")
def _():
    table = alias_table.AliasTable()
    report = table.import_text(
        "# local aliases\n\n Inspect = '_Inspect _Selected \nRun=_Move\n"
    )
    assert report.ok
    assert bool(report)
    assert report.imported == ("Inspect", "Run")
    assert report.errors == ()
    assert report.collisions == ()
    assert table.lookup("INSPECT").options == ("Selected",)
    assert table.lookup("run").command == "Move"


@check("import collisions are case-insensitive and atomic")
def _():
    table = alias_table.AliasTable()
    existing = table.create("Keep", "_Undo")
    report = table.import_text("New=_Move\nKEEP=_Move\nnew=_Undo\n")
    assert not report.ok
    assert not report
    assert report.errors == ()
    assert len(report.collisions) == 2, report.collisions
    assert table.lookup("keep") is existing
    assert table.lookup("new") is None


@check("malformed import is atomic even when another line is valid")
def _():
    table = empty_table()
    report = table.import_text("Good=_Move\nmissing separator\nBad=!!!\n")
    assert not report.ok
    assert len(report.errors) == 2, report.errors
    assert report.collisions == ()
    assert table.aliases == ()


@check("non-text import returns an error report without changing the table")
def _():
    table = alias_table.AliasTable()
    table.create("Keep", "_Move")
    before = table.export_text()
    report = table.import_text(None)
    assert not report.ok
    assert report.errors == ("input is not text",)
    assert table.export_text() == before


@check("exported custom aliases round-trip through import")
def _():
    source = empty_table()
    source.create("Inspect", "'_Inspect _All")
    source.create("Reposition", "! _Move")
    exported = source.export_text()

    destination = empty_table()
    report = destination.import_text(exported)
    assert report.ok
    assert destination.export_text() == exported
    for name in ("Inspect", "Reposition"):
        left = source.lookup(name)
        right = destination.lookup(name.swapcase())
        assert (right.name, right.macro, right.prefix, right.command, right.options) == (
            left.name,
            left.macro,
            left.prefix,
            left.command,
            left.options,
        )


@check("restore_defaults discards custom aliases and restores the built-ins")
def _():
    table = alias_table.AliasTable()
    table.delete("Z")
    table.create("One", "_Move")
    table.import_text("Two=_Undo\nThree='_Inspect\n")
    assert len(table.aliases) == 24
    table.restore_defaults()
    assert len(table.aliases) == 22
    assert table.lookup("one") is None
    assert table.lookup("z").macro == "'_Zoom"
    assert dict(alias_table.DEFAULT_MACROS) == EXPECTED_MACROS


@check("implemented and unavailable custom targets report honest status")
def _():
    table = alias_table.AliasTable()
    available = table.create("Shift", "_Move")
    unavailable = table.create("Examine", "_Inspect")
    assert available.implemented
    assert available.status == "implemented"
    assert available.message == "Move: already implemented"
    assert not unavailable.implemented
    assert unavailable.status == "not implemented yet"
    assert "not implemented yet" in unavailable.message
    assert "Inspect" in unavailable.message


for test_name, status, detail in results:
    if status == "FAIL":
        print(f"FAIL: {test_name}: {detail}")

failures = [result for result in results if result[1] == "FAIL"]
if failures:
    print(f"FAILED: {len(failures)} of {len(results)} alias checks")
    raise SystemExit(1)

print(f"PASS: {len(results)} alias checks")
