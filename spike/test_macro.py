"""Tests for the documented command-macro tokenizer. Pure Python, no bpy.

The tokenizer maps the locally held reference sections "Writing Command Macros"
and "Special scripting commands" onto the step
contract in the task entry.  Every example macro in those pages is pasted in as
a fixture and asserted against its expected steps.
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, "narwhal3d", filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


m = _load("narwhal_test_macro", "macro.py")
tokenize = m.tokenize
MacroError = m.MacroError

results = []


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _cmd(name, dash=False, underscore=False, transparent=False, **extra):
    """Build the exact command-step dict the contract calls for."""
    d = {"kind": "command", "name": name,
         "dash": dash, "underscore": underscore, "transparent": transparent}
    d.update(extra)
    return d


def _text(value):
    return {"kind": "text", "value": value}


def _option(name, value):
    return {"kind": "option", "name": name, "value": value}


# ---------------------------------------------------------------------------
# module surface
# ---------------------------------------------------------------------------

@check("module loads without bpy and exposes tokenize + MacroError")
def _():
    assert "bpy" not in sys.modules
    assert callable(tokenize)
    assert issubclass(MacroError, ValueError)


# ---------------------------------------------------------------------------
# the contract examples (task entry)
# ---------------------------------------------------------------------------

@check("contract: '! _Line _Pause 0,0 _Enter'")
def _():
    got = tokenize("! _Line _Pause 0,0 _Enter")
    assert got == [
        {"kind": "cancel"},
        _cmd("Line", underscore=True),
        {"kind": "pause"},
        _text("0,0"),
        {"kind": "enter"},
    ], got


@check("contract: '-_SetDisplayMode _Mode=_Shaded'")
def _():
    got = tokenize("-_SetDisplayMode _Mode=_Shaded")
    assert got == [
        _cmd("SetDisplayMode", dash=True, underscore=True),
        _option("Mode", "Shaded"),
    ], got


@check("contract: \"'_Zoom\"  (transparent ' AND underscore _)")
def _():
    got = tokenize("'_Zoom")
    assert got == [_cmd("Zoom", underscore=True, transparent=True)], got


# ---------------------------------------------------------------------------
# documented special-character flags
# ---------------------------------------------------------------------------

@check("flags: '!&_Delete' -> cancel + command(history_lock_off)")
def _():
    got = tokenize("!&_Delete")
    assert got == [
        {"kind": "cancel"},
        _cmd("Delete", underscore=True, history_lock_off=True),
    ], got


@check("flags: repeat(*) / suppress(~) / history(#,%) are recorded when set")
def _():
    r = tokenize("*_Circle")
    assert r == [_cmd("Circle", underscore=True, repeat=True)], r
    s = tokenize("~_Circle")
    assert s == [_cmd("Circle", underscore=True, suppress_options=True)], s
    h = tokenize("#Circle")
    assert h == [_cmd("Circle", history_on=True)], h
    o = tokenize("%Circle")
    assert o == [_cmd("Circle", history_off=True)], o


@check("flagged word whose name starts with a digit is TEXT, not a command")
def _():
    # _3Point is a keyword argument, not a command (name '3Point' starts w/ 3)
    assert tokenize("_3Point") == [_text("_3Point")]
    # -15,0,0 is a negative value, not a command (name starts with a digit)
    assert tokenize("-15,0,0") == [_text("-15,0,0")]
    # _Points=10 is an option (the '=' makes it so regardless of the digit)
    assert tokenize("_Points=10") == [_option("Points", "10")]


# ---------------------------------------------------------------------------
# quoted strings / comments
# ---------------------------------------------------------------------------

@check('quotes: "My Mode" is one text token (help: quoted name)')
def _():
    assert tokenize('"My Mode"') == [_text("My Mode")]
    # the documented good/bad pair (the command carries BOTH _ and -)
    assert tokenize('_-SelName "Table 01"') == [
        _cmd("SelName", dash=True, underscore=True), _text("Table 01")]
    # the bad (unquoted) form splits into two text steps
    assert tokenize("_-SelName Table 01") == [
        _cmd("SelName", dash=True, underscore=True), _text("Table"), _text("01")]


@check('option value: "_Mode=_Shaded" strips the value prefix')
def _():
    assert tokenize("_Mode=_Shaded") == [_option("Mode", "Shaded")]
    assert tokenize("_Mode=\"Shaded\"") == [_option("Mode", "Shaded")]


@check('comments: ";" runs to end of line (help: ; comment)')
def _():
    # trailing comment dropped; bare lowercase 'pause' is the reserved Pause cmd
    assert tokenize("_Line 0,0,0 pause ;15,0,0") == [
        _cmd("Line", underscore=True), _text("0,0,0"), {"kind": "pause"}]
    # full-line comment dropped
    assert tokenize("; This is a test macro\n_Circle 0,0,0 15") == [
        _cmd("Circle", underscore=True), _text("0,0,0"), _text("15")]


# ---------------------------------------------------------------------------
# special scripting commands -> pause / enter / command
# ---------------------------------------------------------------------------

@check("specials: Pause/MultiPause->pause, Enter/EnterEnd->enter")
def _():
    assert tokenize("_Pause") == [{"kind": "pause"}]
    assert tokenize("_MultiPause") == [{"kind": "pause"}]
    assert tokenize("_Enter") == [{"kind": "enter"}]
    assert tokenize("_EnterEnd") == [{"kind": "enter"}]


@check("specials: NoEcho/Echo/SetRedraw*/Run are commands, name preserved")
def _():
    assert tokenize("NoEcho") == [_cmd("NoEcho")]
    assert tokenize("Echo") == [_cmd("Echo")]
    assert tokenize("SetRedrawOff") == [_cmd("SetRedrawOff")]
    assert tokenize("SetRedrawOn") == [_cmd("SetRedrawOn")]
    assert tokenize("Run") == [_cmd("Run")]
    # the documented rule: NoEcho must be the very first word
    got = tokenize("NoEcho _Circle 5,5 10")
    assert got[0] == _cmd("NoEcho") and got[1] == _cmd("Circle", underscore=True), got


# ---------------------------------------------------------------------------
# every EXAMPLE MACRO in the help pages, pasted verbatim
# ---------------------------------------------------------------------------

@check('example: "Draw a circle"  --  ! _-Circle 5,5 10')
def _():
    assert tokenize("! _-Circle 5,5 10") == [
        {"kind": "cancel"}, _cmd("Circle", dash=True, underscore=True),
        _text("5,5"), _text("10")]


@check('example: "Deselect and start Move"  --  ! _SelNone _Move')
def _():
    assert tokenize("! _SelNone _Move") == [
        {"kind": "cancel"}, _cmd("SelNone", underscore=True),
        _cmd("Move", underscore=True)]


@check('example: "Create a curve through points"')
def _():
    macro = ("! _SelAll _Points _Pause _Pause _Pause _Enter "
             "_Invert _CurveThroughPt _EnterEnd")
    assert tokenize(macro) == [
        {"kind": "cancel"},
        _cmd("SelAll", underscore=True),
        _cmd("Points", underscore=True),
        {"kind": "pause"}, {"kind": "pause"}, {"kind": "pause"},
        {"kind": "enter"},
        _cmd("Invert", underscore=True),
        _cmd("CurveThroughPt", underscore=True),
        {"kind": "enter"},
    ]


@check('example: "Bypass a dialog box"  --  ! -_Rebuild _Pause _Points=10 _Degree=3 _Enter')
def _():
    assert tokenize("! -_Rebuild _Pause _Points=10 _Degree=3 _Enter") == [
        {"kind": "cancel"},
        _cmd("Rebuild", dash=True, underscore=True),
        {"kind": "pause"},
        _option("Points", "10"),
        _option("Degree", "3"),
        {"kind": "enter"},
    ]


@check('example: "layer nesting"  --  ! _-Layer New "Layer 01:: New Layer" Enter')
def _():
    assert tokenize('! _-Layer New "Layer 01:: New Layer" Enter') == [
        {"kind": "cancel"},
        _cmd("Layer", dash=True, underscore=True),
        _text("New"),
        _text("Layer 01:: New Layer"),
        {"kind": "enter"},
    ]


@check('example: "Pause stops for user input"  --  ! _Circle _Pause 50')
def _():
    assert tokenize("! _Circle _Pause 50") == [
        {"kind": "cancel"}, _cmd("Circle", underscore=True),
        {"kind": "pause"}, _text("50")]


@check('example: "MultiPause"  --  ! _Join _MultiPause')
def _():
    assert tokenize("! _Join _MultiPause") == [
        {"kind": "cancel"}, _cmd("Join", underscore=True), {"kind": "pause"}]


@check('example: "CPlane by three picked points"  --  \'_CPlane _3Point / _Pause x3 / _Enter')
def _():
    assert tokenize("'_CPlane _3Point\n_Pause _Pause _Pause _Enter") == [
        _cmd("CPlane", underscore=True, transparent=True),
        _text("_3Point"),
        {"kind": "pause"}, {"kind": "pause"}, {"kind": "pause"},
        {"kind": "enter"},
    ]


@check('example: "EnterEnd exits a deep command"  --  ! _-DocumentProperties _Mesh _Custom _MaxEdgeSrf .01 _EnterEnd')
def _():
    assert tokenize(
        "! _-DocumentProperties _Mesh _Custom _MaxEdgeSrf .01 _EnterEnd") == [
        {"kind": "cancel"},
        _cmd("DocumentProperties", dash=True, underscore=True),
        _cmd("Mesh", underscore=True),
        _cmd("Custom", underscore=True),
        _cmd("MaxEdgeSrf", underscore=True),
        _text(".01"),
        {"kind": "enter"},
    ]


@check('example: "ReadCommandFile"  --  -ReadCommandFile "my file.txt"')
def _():
    assert tokenize('-ReadCommandFile "my file.txt"') == [
        _cmd("ReadCommandFile", dash=True), _text("my file.txt")]


@check('example: "multi-line with trailing comments"  --  _Line ... pause ;15,0,0')
def _():
    macro = (
        "; This is a test macro\n"
        "_Circle 0,0,0 15\n"
        "_Line 0,0,0 pause ;15,0,0\n"
        "; Line 0,0,0 0,15,0\n"
        "_Line 0,0,0 -15,0,0")
    assert tokenize(macro) == [
        _cmd("Circle", underscore=True), _text("0,0,0"), _text("15"),
        _cmd("Line", underscore=True), _text("0,0,0"), {"kind": "pause"},
        _cmd("Line", underscore=True), _text("0,0,0"), _text("-15,0,0"),
    ]


# ---------------------------------------------------------------------------
# robustness
# ---------------------------------------------------------------------------

@check("robust: empty/whitespace macro -> [] (no crash)")
def _():
    assert tokenize("") == []
    assert tokenize("   \n\t ") == []


@check("robust: a standalone '!' anywhere is a cancel")
def _():
    assert tokenize("!") == [{"kind": "cancel"}]
    assert tokenize("_Move !") == [_cmd("Move", underscore=True), {"kind": "cancel"}]
    assert tokenize("!_Move") == [
        {"kind": "cancel"}, _cmd("Move", underscore=True)]


@check("robust: a non-string macro is refused (never a silent token)")
def _():
    for bad in (None, 5, ["_Move"], b"_Move"):
        try:
            tokenize(bad)
            raise AssertionError(f"expected MacroError for {bad!r}")
        except MacroError:
            pass


# ---------------------------------------------------------------------------
failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
