"""Tests for the native Command History facade. Python orchestration, no bpy.

The model is a capped, in-order ring buffer (default 500, MEASURED from
``commands/commandhistory``: "lists the last 500 command lines") with
``append`` / ``lines(last)`` / ``search(text)`` / ``save(path)`` / ``clear``.
"""
import importlib.util
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, "narwhal3d", filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ch = _load("command_history_test", "command_history.py")
CommandHistory = ch.CommandHistory

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
# module surface + construction
# ---------------------------------------------------------------------------

@check("module loads without bpy and exposes CommandHistory")
def _():
    assert "bpy" not in sys.modules
    assert callable(CommandHistory)


@check("default limit is 500 (MEASURED: 'last 500 command lines')")
def _():
    assert CommandHistory().limit == 500
    assert len(CommandHistory()) == 0


@check("limit must be an int >= 1 (a 0/1-line 'history' is refused)")
def _():
    for bad in (0, -1, 1.0, "500", None, True):
        try:
            CommandHistory(limit=bad)
            raise AssertionError(f"expected ValueError for limit={bad!r}")
        except ValueError:
            pass
    # 1 is the smallest valid history
    assert CommandHistory(limit=1).limit == 1


# ---------------------------------------------------------------------------
# append
# ---------------------------------------------------------------------------

@check("append: appends in order, len grows")
def _():
    h = CommandHistory(limit=10)
    h.append("_Line")
    h.append("_Circle")
    h.append("_Box")
    assert h.lines() == ["_Line", "_Circle", "_Box"]
    assert len(h) == 3


@check("append: a non-string line is refused (never a silent entry)")
def _():
    h = CommandHistory()
    for bad in (None, 5, ["_Line"], b"_Line"):
        try:
            h.append(bad)
            raise AssertionError(f"expected ValueError for line={bad!r}")
        except ValueError:
            pass
    assert h.lines() == []


@check("append: a single trailing newline is stripped (no blank-line gaps)")
def _():
    h = CommandHistory()
    h.append("_Line\n")
    h.append("_Circle\r")
    assert h.lines() == ["_Line", "_Circle"]


# ---------------------------------------------------------------------------
# the 500-line cap (ring behaviour)
# ---------------------------------------------------------------------------

@check("cap: at 500 the oldest drops, keeping the last 500 (MEASURED cap)")
def _():
    h = CommandHistory(limit=500)
    for i in range(501):
        h.append(f"line{i}")
    lines = h.lines()
    assert len(lines) == 500
    assert lines[0] == "line1"      # line0 dropped (oldest)
    assert lines[-1] == "line500"   # newest kept
    assert "line0" not in lines


@check("cap: a small limit keeps the most recent N")
def _():
    h = CommandHistory(limit=3)
    for i in range(5):
        h.append(f"c{i}")
    assert h.lines() == ["c2", "c3", "c4"]


@check("cap: overwriting the same name never breaks the ring")
def _():
    h = CommandHistory(limit=2)
    h.append("a")
    h.append("b")
    h.append("a")
    assert h.lines() == ["b", "a"]


# ---------------------------------------------------------------------------
# lines(last)
# ---------------------------------------------------------------------------

@check("lines(None) -> full copy; lines(n) -> the last n")
def _():
    h = CommandHistory(limit=10)
    for i in range(4):
        h.append(f"x{i}")
    assert h.lines(None) == ["x0", "x1", "x2", "x3"]
    assert h.lines(1) == ["x3"]
    assert h.lines(2) == ["x2", "x3"]
    # asking for more than exist gives all
    assert h.lines(99) == ["x0", "x1", "x2", "x3"]


@check("lines(0) -> []; a bad last is refused")
def _():
    h = CommandHistory()
    h.append("a")
    assert h.lines(0) == []
    for bad in (-1, 1.5, "2", True):
        try:
            h.lines(last=bad)
            raise AssertionError(f"expected ValueError for last={bad!r}")
        except ValueError:
            pass


@check("lines() returns a COPY (mutating it does not change the buffer)")
def _():
    h = CommandHistory()
    h.append("a")
    h.append("b")
    view = h.lines()
    view.append("c")
    view[0] = "ZZZ"
    assert h.lines() == ["a", "b"]


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

@check("search: case-insensitive substring, returns line indices")
def _():
    h = CommandHistory(limit=10)
    h.append("_Line 0,0,0 1,1,0")
    h.append("_Circle 5,5 10")
    h.append("_Line 0,0,0 -1,0,0")
    h.append("_SelNone")
    assert h.search("line") == [0, 2]        # case-insensitive
    assert h.search("LINE") == [0, 2]
    assert h.search("circle") == [1]
    assert h.search("0,0,0") == [0, 2]
    assert h.search("SelNone") == [3]


@check("search: no match -> []; an empty/non-string text is refused")
def _():
    h = CommandHistory()
    h.append("_Line")
    assert h.search("zzz") == []
    for bad in ("", None, 3, ["a"]):
        try:
            h.search(bad)
            raise AssertionError(f"expected ValueError for search={bad!r}")
        except ValueError:
            pass


@check("search: indices track the ring (after drops they re-index)")
def _():
    h = CommandHistory(limit=2)
    h.append("_Line")   # 0
    h.append("_Box")    # 1
    h.append("_Line")   # drops _Line(0); now [Box, Line] -> _Line at index 1
    assert h.search("Line") == [1]


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------

@check("save: writes the whole buffer as plain text (one line each)")
def _():
    h = CommandHistory(limit=10)
    h.append("_Line 0,0,0 1,1,0")
    h.append("_Circle 5,5 10")
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "hist.txt")
        h.save(p)
        with open(p, encoding="utf-8") as fh:
            content = fh.read()
    assert content == "_Line 0,0,0 1,1,0\n_Circle 5,5 10\n"
    assert content.splitlines() == ["_Line 0,0,0 1,1,0", "_Circle 5,5 10"]


@check("save: an empty buffer writes an empty file")
def _():
    h = CommandHistory()
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "empty.txt")
        h.save(p)
        with open(p, encoding="utf-8") as fh:
            assert fh.read() == ""


@check("save: overwrites an existing file atomically (no half-file left)")
def _():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "h.txt")
        # seed with stale content
        with open(p, "w") as fh:
            fh.write("stale\n")
        h = CommandHistory(limit=10)
        h.append("fresh")
        h.save(p)
        with open(p, encoding="utf-8") as fh:
            assert fh.read() == "fresh\n"
        # no temp file left behind
        leftovers = [f for f in os.listdir(d) if f not in ("h.txt",)]
        assert leftovers == []


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------

@check("clear: drops every line (the documented 'Clear')")
def _():
    h = CommandHistory(limit=10)
    for i in range(5):
        h.append(f"a{i}")
    h.clear()
    assert h.lines() == []
    assert len(h) == 0
    # usable again after clearing
    h.append("again")
    assert h.lines() == ["again"]


# ---------------------------------------------------------------------------
failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
