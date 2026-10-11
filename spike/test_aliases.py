"""Focused tests for the pure command-alias table.

The application default set is empty. These checks exercise user-created aliases,
macro parsing, case-insensitive CRUD, atomic text import, deterministic export,
and restoration to the empty defaults.

"""

import importlib.util
import os
import sys
from types import MappingProxyType


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_PATH = os.path.join(ROOT, "narwhal3d", "alias_table.py")


def _load():
    spec = importlib.util.spec_from_file_location("alias_table_under_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load alias table from {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


alias_table = _load()
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


@check("application defaults are empty")
def _():
    assert alias_table.DEFAULT_MACROS == {}
    assert len(alias_table.DEFAULT_MACROS) == 0
    assert alias_table.ALIAS_TABLE == {}
    assert alias_table.resolve_alias("anything") is None


@check("DEFAULT_MACROS is immutable")
def _():
    assert isinstance(alias_table.DEFAULT_MACROS, MappingProxyType)

    def attempt_mutation():
        alias_table.DEFAULT_MACROS["Quick"] = "_Move"

    assert_raises(TypeError, attempt_mutation)
    assert alias_table.DEFAULT_MACROS == {}


@check("a new editable table starts empty")
def _():
    table = alias_table.AliasTable()
    assert table.aliases == ()
    assert table.export_text() == ""
    assert table.lookup("anything") is None


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
    assert len(table.aliases) == 1


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
    assert table.aliases == ()


@check("aliases are sorted deterministically and exposed as a tuple")
def _():
    table = alias_table.AliasTable()
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
    table = alias_table.AliasTable()
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
    table = alias_table.AliasTable()
    report = table.import_text("Good=_Move\nmissing separator\nBad=!!!\n")
    assert not report.ok
    assert len(report.errors) == 2, report.errors
    assert report.collisions == ()
    assert table.aliases == ()


@check("non-text import returns an error report without changing the table")
def _():
    table = alias_table.AliasTable()
    table.create("Keep", "_Move")
    report = table.import_text(None)
    assert not report.ok
    assert report.errors == ("input is not text",)
    assert table.lookup("keep") is not None


@check("exported custom aliases round-trip through import")
def _():
    source = alias_table.AliasTable()
    source.create("Inspect", "'_Inspect _All")
    source.create("Reposition", "! _Move")
    exported = source.export_text()

    destination = alias_table.AliasTable()
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


@check("restore_defaults discards all custom aliases and returns to empty")
def _():
    table = alias_table.AliasTable()
    table.create("One", "_Move")
    table.import_text("Two=_Undo\nThree='_Inspect\n")
    assert len(table.aliases) == 3
    table.restore_defaults()
    assert table.aliases == ()
    assert table.export_text() == ""
    assert table.lookup("one") is None
    assert alias_table.DEFAULT_MACROS == {}


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
