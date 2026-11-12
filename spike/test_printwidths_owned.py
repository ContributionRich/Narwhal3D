"""Native-only tests for the RUST-PRINTWIDTHS printwidths.py (no bpy).

``read_widths`` keeps host file I/O in Python and delegates the parse to the
qualified Rust export ``narwhal_pure_core_printwidths`` (adapter
``native_metadata.widths_from_text``).  There is no Python parser and no
small-file bypass: a ``None`` adapter result raises ``RuntimeError``.  These
tests pin down the native-route requirement (small AND large files), the
explicit-refusal behaviour (missing library, refused report, size bound),
and that the parse result and warning strings stay exact.
"""
import importlib.util
import os
import subprocess
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


pw = _load("owned_printwidths", "printwidths.py")
nm = pw._native_metadata()
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
    p = os.path.join(tempfile.mkdtemp(), "printwidths.txt")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(content)
    return p


def _calls():
    return nm.status()["calls"]["widths"]


# ---------------------------------------------------------------------------
# native route, both small and large
# ---------------------------------------------------------------------------

@check("a 1-byte file takes the native route (no small-file Python bypass)")
def _():
    p = _write("7")
    before = _calls()
    w, warn = read_widths(p)
    assert (w, warn) == ([7.0], [])
    assert nm.status()["routes"]["widths"] == "native"
    assert _calls() == before + 1, "1-byte file did not call the native export"


@check("an ~800 KB file takes the native route with an identical result")
def _():
    body = "0.13\n0.18\n" * 25000          # 200000 lines, ~1.2 MB of text
    p = _write(body)
    before = _calls()
    w, warn = read_widths(p)
    assert w == [0.13, 0.18] and warn == []
    assert nm.status()["routes"]["widths"] == "native"
    assert _calls() == before + 1


# ---------------------------------------------------------------------------
# explicit refusal: never a Python fallback
# ---------------------------------------------------------------------------

@check("an unavailable native library raises RuntimeError (subprocess, no fallback)")
def _():
    code = (
        "import importlib.util, os, sys, tempfile\n"
        "sys.path.insert(0, %r)\n"
        "spec = importlib.util.spec_from_file_location("
        "'pw_missing', os.path.join(%r, 'narwhal3d', 'printwidths.py'))\n"
        "m = importlib.util.module_from_spec(spec); sys.modules['pw_missing'] = m; "
        "spec.loader.exec_module(m)\n"
        "p = os.path.join(tempfile.mkdtemp(), 'w.txt'); open(p, 'w').write('0.13\\n')\n"
        "try:\n"
        "    m.read_widths(p)\n"
        "except RuntimeError as e:\n"
        "    print('OK', e)\n"
        "else:\n"
        "    sys.exit(3)\n"
    ) % (ROOT, ROOT)
    env = dict(os.environ)
    env["NARWHAL_PURE_CORE"] = "/nonexistent/libnarwhal_pure_core.so"
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert out.returncode == 0, (out.returncode, out.stdout, out.stderr)
    assert "OK" in out.stdout and "native export unavailable" in out.stdout


@check("a missing native export raises RuntimeError (monkeypatched adapter)")
def _():
    from functools import lru_cache

    def _dead(path):
        return None, None, None
    _dead.cache_info = lambda: None
    _dead.cache_clear = lambda: None
    real = nm._load
    nm._load = _dead
    try:
        try:
            read_widths(_write("0.13\n"))
        except RuntimeError as e:
            assert "native export unavailable" in str(e), repr(e)
        else:
            raise AssertionError("expected RuntimeError for a missing native export")
    finally:
        nm._load = real
        _dead.cache_clear()


@check("a rejected native report raises RuntimeError (bogus needed length)")
def _():
    real = nm._load
    loaded = real(os.environ.get("NARWHAL_PURE_CORE") or nm._PACKAGED_LIBRARY)
    library, rgb, widths = loaded
    assert widths is not None, "native widths export missing from the library"

    class Bogus:
        def __call__(self, data, length, output, capacity, needed):
            return 1
    nm._load = lambda path: (library, rgb, Bogus())
    try:
        try:
            read_widths(_write("0.13\n"))
        except RuntimeError as e:
            assert "rejected native report" in str(e), repr(e)
        else:
            raise AssertionError("expected RuntimeError for a rejected native report")
    finally:
        nm._load = real


@check("a text above the native 1 MiB bound raises RuntimeError (not a silent parse)")
def _():
    p = _write("0.13\n" * 250000)            # > 1 MiB decoded
    try:
        read_widths(p)
    except RuntimeError as e:
        assert "native size bound" in str(e), repr(e)
    else:
        raise AssertionError("expected RuntimeError for a text above the native bound")


# ---------------------------------------------------------------------------
# exact warnings and values (the Rust port of CPython float/repr)
# ---------------------------------------------------------------------------

@check("malformed lines: exact warning strings, line numbers and repr")
def _():
    w, warn = _widths_from_text("0\n-0.5\n0.0\n0.13\n")
    assert w == [0.13], w
    assert warn == [
        "line 1: 0.0 is not a finite number larger than 0",
        "line 2: -0.5 is not a finite number larger than 0",
        "line 3: 0.0 is not a finite number larger than 0",
    ], warn


@check("inf / nan are bad lines with the exact warning")
def _():
    w, warn = _widths_from_text("inf\nnan\n0.13\n")
    assert w == [0.13]
    assert warn == [
        "line 1: inf is not a finite number larger than 0",
        "line 2: nan is not a finite number larger than 0",
    ], warn


@check("non-number and two-number lines keep their exact repr in the warning")
def _():
    w, warn = _widths_from_text("0.13\nabc\n0.13 0.18\n0.25\n")
    assert w == [0.13, 0.25]
    assert warn == [
        "line 2: 'abc' is not a number",
        "line 3: '0.13 0.18' is not a number",
    ], warn


@check("comments and blank lines are skipped silently")
def _():
    w, warn = _widths_from_text("; comment\n// another\n\n   \t\n0.13\n")
    assert w == [0.13] and warn == []


@check("a trailing ';' on a value line is a bad line, not a comment")
def _():
    w, warn = _widths_from_text("0.13;\n0.18\n")
    assert w == [0.18]
    assert warn == ["line 1: '0.13;' is not a number"], warn


@check("Unicode: a non-ASCII digit line parses; a junk line warns with exact repr")
def _():
    w, warn = _widths_from_text("\u0661\u0663\n\u03b1\u03b2\u03b3\n0.13\n")
    assert w == [0.13, 13.0], w
    assert warn == ["line 2: '\u03b1\u03b2\u03b3' is not a number"], warn


@check("duplicates and order: the result is sorted and de-duplicated")
def _():
    w, warn = _widths_from_text("2.00\n0.13\n1.00\n0.13\n1.00\n")
    assert w == [0.13, 1.00, 2.00] and warn == []


@check("standard width-file fixture: exactly 0.13 ... 2.00, 14 values")
def _():
    fixture = _write("; Standard drawing widths\n0.13\n0.18\n0.25\n0.30\n0.35\n0.50\n"
                     "0.60\n0.70\n0.80\n0.90\n1.00\n1.20\n1.40\n2.00\n")
    w, warn = read_widths(fixture)
    assert warn == []
    assert w == [0.13, 0.18, 0.25, 0.30, 0.35, 0.50,
                 0.60, 0.70, 0.80, 0.90, 1.00, 1.20, 1.40, 2.00]


# ---------------------------------------------------------------------------
# host I/O stays Python-owned
# ---------------------------------------------------------------------------

@check("a missing file raises FileNotFoundError (caller error)")
def _():
    missing = os.path.join(tempfile.gettempdir(), "no_such_printwidths_9999.txt")
    try:
        read_widths(missing)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected FileNotFoundError for a missing file")


@check("an invalid UTF-8 file raises UnicodeDecodeError (strict I/O)")
def _():
    p = os.path.join(tempfile.mkdtemp(), "printwidths.txt")
    with open(p, "wb") as fh:
        fh.write(b"0.13\n\xff\xfe\n")
    try:
        read_widths(p)
    except UnicodeDecodeError:
        pass
    else:
        raise AssertionError("expected UnicodeDecodeError for invalid UTF-8")


# ---------------------------------------------------------------------------
failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
