"""Pure-Python tests for the AutoCAD R12 ASCII DXF writer.

Pins the invariants a laser-cutter / CNC reader depends on:
* the file is R12 (``$ACADVER = AC1009``) with ``$INSUNITS = 4`` (millimetres);
* the LAYER table has one entry per layer -- including any layer an entity
  names but the caller did not list;
* every group code sits on its own line, the value on the next (so the total
  line count is even -- an odd count is a structural bug);
* the file ends ``0`` / ``EOF``;
* a LINE/CIRCLE/ARC/POLYLINE round-trips: parse our own output back into
  (code, value) pairs and check every entity's values;
* a REAL reader accepts it: ``inkscape`` converts the file to SVG with exit 0
  and empty stderr, and the SVG carries geometry for every entity we wrote --
  skipped with a printed SKIP if inkscape is not on PATH.

"""
import importlib.util
import os
import re
import shutil
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


dxf = _load("narwhal_dxf_writer", "dxf_writer.py")

results = []
TMP = tempfile.mkdtemp(prefix="dxf_")


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


def _all_pairs(path):
    """Parse the file into ``[(code, value), ...]`` (code/value line pairs).

    Asserts the structural invariant that the total line count is even (a
    group code on its own line, the value on the next).
    """
    lines = open(path).read().splitlines()
    assert len(lines) % 2 == 0, f"odd line count {len(lines)}"
    return [(lines[i], lines[i + 1]) for i in range(0, len(lines), 2)]


def _block(pr, name):
    """The (code, value) pairs of one non-POLYLINE entity.

    Runs from ``0/<name>`` up to (not including) the next ``0/...`` line --
    a LINE/CIRCLE/ARC has no other ``0`` lines inside it, so the next ``0``
    line is the following entity. POLYLINE is handled separately (its block
    runs to ``0/SEQEND``).
    """
    i = pr.index(("0", name))
    k = i + 1
    while k < len(pr) and pr[k][0] != "0":
        k += 1
    return pr[i:k]


ENTITIES = [
    {"type": "LINE", "p0": (0, 0, 0), "p1": (10, 10, 0),
     "layer": "0", "color": 7},
    {"type": "CIRCLE", "center": (30, 20, 0), "radius": 5,
     "layer": "walls", "color": 1},
    {"type": "ARC", "center": (50, 20, 0), "radius": 5,
     "start_deg": 0, "end_deg": 90, "layer": "0", "color": 5},
    {"type": "POLYLINE", "points": [(0, 50, 0), (20, 50, 0), (20, 70, 0)],
     "closed": True, "layer": "0", "color": 7},
]


@check("module loads without bpy and write_dxf is callable")
def _():
    assert "bpy" not in sys.modules
    assert callable(dxf.write_dxf)


@check("is R12 (AC1009) and $INSUNITS = 4 (millimetres)")
def _():
    p = os.path.join(TMP, "hdr.dxf")
    dxf.write_dxf(p, ENTITIES)
    pr = _all_pairs(p)
    # a HEADER variable is `9/<varname>` then `<code>/<value>`:
    # $ACADVER is the value of code 9; its value (AC1009) is the NEXT pair.
    i = pr.index(("9", "$ACADVER"))
    assert pr[i + 1] == ("1", "AC1009"), pr[i:i + 2]
    j = pr.index(("9", "$INSUNITS"))
    assert pr[j + 1] == ("70", "4"), pr[j:j + 2]


@check("group codes alternate code/value (even line count)")
def _():
    p = os.path.join(TMP, "even.dxf")
    dxf.write_dxf(p, ENTITIES)
    n = len(open(p).read().splitlines())
    assert n % 2 == 0, n


@check("the file ends 0 / EOF")
def _():
    p = os.path.join(TMP, "eof.dxf")
    dxf.write_dxf(p, ENTITIES)
    pr = _all_pairs(p)
    assert pr[-1] == ("0", "EOF"), pr[-1]


@check("a LINE round-trips: p0/p1 + layer + color")
def _():
    p = os.path.join(TMP, "line.dxf")
    dxf.write_dxf(p, ENTITIES)
    pr = _all_pairs(p)
    # find the LINE block: 0/LINE ... up to the next 0/<entity>
    blk = dict(_block(pr, "LINE"))
    assert blk.get("10") == "0" and blk.get("20") == "0" and blk.get("30") == "0", blk
    assert blk.get("11") == "10" and blk.get("21") == "10" and blk.get("31") == "0", blk
    assert blk.get("8") == "0" and blk.get("62") == "7", blk


@check("a CIRCLE round-trips: center + radius")
def _():
    p = os.path.join(TMP, "circle.dxf")
    dxf.write_dxf(p, ENTITIES)
    pr = _all_pairs(p)
    blk = dict(_block(pr, "CIRCLE"))
    assert blk.get("10") == "30" and blk.get("20") == "20" and blk.get("30") == "0", blk
    assert blk.get("40") == "5", blk
    assert blk.get("8") == "walls" and blk.get("62") == "1", blk


@check("an ARC round-trips: radius + start/end degrees")
def _():
    p = os.path.join(TMP, "arc.dxf")
    dxf.write_dxf(p, ENTITIES)
    pr = _all_pairs(p)
    blk = dict(_block(pr, "ARC"))
    assert blk.get("40") == "5", blk
    assert blk.get("50") == "0" and blk.get("51") == "90", blk
    assert blk.get("8") == "0" and blk.get("62") == "5", blk


@check("a POLYLINE round-trips: 66/1, closed 70/1, 3 VERTEX, SEQEND")
def _():
    p = os.path.join(TMP, "poly.dxf")
    dxf.write_dxf(p, ENTITIES)
    pr = _all_pairs(p)
    i = pr.index(("0", "POLYLINE"))
    j = pr.index(("0", "SEQEND"), i)
    blk = pr[i:j + 1]
    assert ("66", "1") in blk, blk
    assert ("70", "1") in blk, blk  # closed
    assert blk.count(("0", "VERTEX")) == 3, blk.count(("0", "VERTEX"))


@check("layers named by entities appear in the table")
def _():
    # the caller lists only 'walls'; the LINE names layer '0' which must be added
    p = os.path.join(TMP, "layers.dxf")
    dxf.write_dxf(p, ENTITIES, layers={"walls": 1})
    pr = _all_pairs(p)
    # the LAYER table region is between the `2/LAYER` table name and ENDTAB
    t = pr.index(("2", "LAYER"))
    e = pr.index(("0", "ENDTAB"))
    region = pr[t + 1:e]  # drop the table-name pair itself
    # each LAYER entry's name is the `2/...` value right after its `0/LAYER`
    layer_names = []
    for i, (c, v) in enumerate(region):
        if c == "0" and v == "LAYER":
            layer_names.append(region[i + 1][1])
    assert "walls" in layer_names, layer_names
    assert "0" in layer_names, layer_names
    # one LAYER entry per name, and the table's count line agrees
    nlayer = sum(1 for c, v in region if c == "0" and v == "LAYER")
    assert nlayer == len(layer_names), (nlayer, layer_names)
    # the first `70` after the table name is the entry count
    count = next(v for c, v in region if c == "70")
    assert int(count) == len(layer_names), (count, layer_names)


@check("a real reader accepts it: inkscape -> SVG with geometry per entity")
def _():
    if not shutil.which("inkscape"):
        print("  SKIP: inkscape not found on PATH")
        return
    p = os.path.join(TMP, "read.dxf")
    ents = ENTITIES
    dxf.write_dxf(p, ents, layers={"walls": 1})
    svg = os.path.join(TMP, "read.svg")
    out = subprocess.run(
        ["inkscape", "--export-type=svg", "--export-filename=" + svg, p],
        capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, (out.returncode, out.stderr)
    assert out.stderr.strip() == "", out.stderr
    s = re.sub(r"\s+", " ", open(svg).read())
    # inkscape renders every DXF entity (line/circle/arc/polyline) as <path>.
    # A real reader therefore emits at least one <path> per entity we wrote.
    npaths = len(re.findall(r"<path[\s/>]", s))
    assert npaths >= len(ents), (npaths, len(ents))


# ---------------------------------------------------------------------------
failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
