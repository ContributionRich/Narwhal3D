"""Tests for read_widths. Pure Python, no bpy.

``read_widths(path) -> (widths, warnings)`` reads a ``printwidths.txt``:
``;`` / ``//`` comment lines ignored, values larger than 0 kept, the result
sorted + de-duplicated, one warning per bad line (named by line number).
A self-contained standard-width fixture must give exactly 0.13 ... 2.00, 14.
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


pw = _load("narwhal_printwidths", "printwidths.py")
read_widths = pw.read_widths
_widths_from_text = pw._widths_from_text

results = []


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


def _write(content):
    """Write ``content`` to a temp file and return its path.

    ``mkdtemp`` (not ``TemporaryDirectory``) so the file outlives this helper:
    a ``TemporaryDirectory`` object that is dereferenced is garbage-collected
    and its ``__del__`` deletes the dir before ``read_widths`` reads the file.
    """
    p = os.path.join(tempfile.mkdtemp(), "printwidths.txt")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(content)
    return p


# ---------------------------------------------------------------------------
# module surface
# ---------------------------------------------------------------------------

@check("module loads without bpy and exposes read_widths")
def _():
    assert "bpy" not in sys.modules
    assert callable(read_widths)


# ---------------------------------------------------------------------------
# standard-width acceptance fixture
# ---------------------------------------------------------------------------

@check("standard fixture: exactly 0.13 ... 2.00, 14 values, no warnings")
def _():
    with tempfile.TemporaryDirectory() as directory:
        fixture = os.path.join(directory, "printwidths.txt")
        with open(fixture, "w", encoding="utf-8") as output:
            output.write("; Standard widths\n0.13\n0.18\n0.25\n0.30\n0.35\n0.50\n"
                         "0.60\n0.70\n0.80\n0.90\n1.00\n1.20\n1.40\n2.00\n")
        widths, warnings = read_widths(fixture)
    assert warnings == [], warnings
    assert len(widths) == 14, widths
    assert widths == [0.13, 0.18, 0.25, 0.30, 0.35, 0.50,
                      0.60, 0.70, 0.80, 0.90, 1.00, 1.20, 1.40, 2.00], widths
    # sorted ascending and unique
    assert widths == sorted(set(widths))


# ---------------------------------------------------------------------------
# comments and blanks (the help's own rules)
# ---------------------------------------------------------------------------

@check("comments: ';' and '//' leading lines are ignored")
def _():
    w, warn = _widths_from_text("; comment\n// another\n0.13\n0.18\n")
    assert w == [0.13, 0.18] and warn == []


@check("blank / whitespace-only lines are skipped silently")
def _():
    w, warn = _widths_from_text("\n   \n0.13\n\t\n0.18\n")
    assert w == [0.13, 0.18] and warn == []


@check("comment with leading whitespace is still a comment")
def _():
    w, warn = _widths_from_text("   // indented comment\n  ; also a comment\n0.13\n")
    assert w == [0.13] and warn == []


# ---------------------------------------------------------------------------
# the value rules
# ---------------------------------------------------------------------------

@check("sorted + de-duplicated (order and repeats in the file don't matter)")
def _():
    w, warn = _widths_from_text("2.00\n0.13\n1.00\n0.13\n1.00\n")
    assert w == [0.13, 1.00, 2.00] and warn == []


@check("a value larger than 0 is kept; 0 / negative are bad lines")
def _():
    w, warn = _widths_from_text("0\n-0.5\n0.0\n0.13\n")
    assert w == [0.13]
    assert len(warn) == 3, warn
    assert warn[0] == "line 1: 0.0 is not a finite number larger than 0"
    assert warn[1] == "line 2: -0.5 is not a finite number larger than 0"
    assert warn[2] == "line 3: 0.0 is not a finite number larger than 0"


@check("inf / nan are not widths (bad lines, not crashes)")
def _():
    w, warn = _widths_from_text("inf\nnan\n0.13\n")
    assert w == [0.13]
    assert len(warn) == 2, warn


@check("a non-number is a bad line (named by line number), never a crash")
def _():
    w, warn = _widths_from_text("0.13\nabc\n0.18\n")
    assert w == [0.13, 0.18]
    assert warn == ['line 2: \'abc\' is not a number'], warn


@check("a line with two numbers is one bad line, not two values")
def _():
    w, warn = _widths_from_text("0.13\n0.13 0.18\n0.25\n")
    assert w == [0.13, 0.25]
    assert warn == ['line 2: \'0.13 0.18\' is not a number'], warn


@check("a trailing ';' on a value line is NOT a comment (bad line)")
def _():
    w, warn = _widths_from_text("0.13;\n0.18\n")
    assert w == [0.18]
    assert warn == ['line 1: \'0.13;\' is not a number'], warn


# ---------------------------------------------------------------------------
# file I/O
# ---------------------------------------------------------------------------

@check("read_widths reads a file (round-trips the text parse)")
def _():
    p = _write("// widths\n0.25\n0.13\n")
    w, warn = read_widths(p)
    assert w == [0.13, 0.25] and warn == []


@check("read_widths: an empty file -> ([], []) (no crash)")
def _():
    p = _write("")
    assert read_widths(p) == ([], [])


@check("read_widths: a missing file raises FileNotFoundError (caller error)")
def _():
    missing = os.path.join(tempfile.gettempdir(), "no_such_printwidths_9999.txt")
    try:
        read_widths(missing)
        raise AssertionError("expected FileNotFoundError for a missing file")
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
