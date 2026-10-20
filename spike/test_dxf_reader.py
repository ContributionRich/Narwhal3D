r"""Pure-Python tests for the DXF reader.

Pins the import contract:
* a file written by the paired writer reads back to the same entities,
  layers, and units;
* a hand-written LWPOLYLINE: closed (flag 70 bit 1) and open both read to
  (type, points, closed, layer, color); a bulge (code 42) is dropped but
  COUNTED in skipped["bulge"];
* BLOCK / INSERT / TEXT are not parsed but COUNTED in skipped[type];
* insunits is the $INSUNITS int (4 = mm), None when the header has none;
* a BINARY DXF ($ACADVER != AC1009) is REFUSED BY NAME (FileFormatError);
* a truncated file (code with no value) raises FileFormatError.

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


rdr = _load("narwhal_dxf_reader", "dxf_reader.py")
wtr = _load("narwhal_dxf_writer", "dxf_writer.py")

results = []
TMP = tempfile.mkdtemp(prefix="dxfreader_")


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


def _write(name, text):
    p = os.path.join(TMP, name)
    with open(p, "w", encoding="latin-1") as fh:
        fh.write(text)
    return p


ENTITIES = [
    {"type": "LINE", "p0": (0, 0, 0), "p1": (10, 0, 0), "layer": "cut", "color": 1},
    {"type": "CIRCLE", "center": (5, 5, 0), "radius": 3.5, "layer": "drill", "color": 5},
    {"type": "ARC", "center": (0, 0, 0), "radius": 2.0,
     "start_deg": 30.0, "end_deg": 120.0, "layer": "arc", "color": 3},
    {"type": "POLYLINE", "points": [(0, 0, 0), (4, 0, 0), (4, 4, 0)],
     "closed": False, "layer": "poly", "color": 7},
    {"type": "POLYLINE", "points": [(1, 1, 0), (3, 1, 0), (3, 3, 0), (1, 3, 0)],
     "closed": True, "layer": "box", "color": 2},
]
LAYERS = {"cut": 1, "drill": 5, "arc": 3, "poly": 7, "box": 2, "hidden": 8}


@check("module loads without bpy and read_dxf + FileFormatError exist")
def _():
    assert "bpy" not in sys.modules
    assert callable(rdr.read_dxf)
    assert issubclass(rdr.FileFormatError, ValueError)


@check("round-trips a writer-produced file (all 4 types, exact)")
def _():
    p = os.path.join(TMP, "rt.dxf")
    wtr.write_dxf(p, ENTITIES, LAYERS)
    got = rdr.read_dxf(p)
    assert got["entities"] == ENTITIES, got["entities"]
    assert got["layers"] == LAYERS, got["layers"]
    assert got["insunits"] == 4, got["insunits"]
    assert got["skipped"] == {}, got["skipped"]


@check("POLYLINE open vs closed flag both survive the round-trip")
def _():
    p = os.path.join(TMP, "poly.dxf")
    wtr.write_dxf(p, [ENTITIES[3], ENTITIES[4]])
    got = rdr.read_dxf(p)
    assert [e["closed"] for e in got["entities"]] == [False, True], got["entities"]
    assert got["entities"][0]["points"] == [(0, 0, 0), (4, 0, 0), (4, 4, 0)]
    assert got["entities"][1]["points"] == [(1, 1, 0), (3, 1, 0), (3, 3, 0), (1, 3, 0)]


@check("LWPOLYLINE closed: flag 70 value 1 (bit 0) -> closed, points in order")
def _():
    p = _write("lw_closed.dxf",
               "0\nSECTION\n2\nENTITIES\n"
               "0\nLWPOLYLINE\n8\nlayer1\n62\n5\n70\n1\n"
               "10\n0.0\n20\n0.0\n30\n0.0\n"
               "10\n10.0\n20\n0.0\n30\n0.0\n"
               "10\n10.0\n20\n10.0\n30\n0.0\n"
               "10\n0.0\n20\n10.0\n30\n0.0\n"
               "0\nENDSEC\n")
    got = rdr.read_dxf(p)
    assert got["entities"][0]["type"] == "LWPOLYLINE"
    assert got["entities"][0]["closed"] is True, got["entities"]
    assert got["entities"][0]["points"] == [
        (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)]
    assert got["entities"][0]["layer"] == "layer1"
    assert got["entities"][0]["color"] == 5
    assert got["skipped"] == {}, got["skipped"]


@check("LWPOLYLINE open: flag 70 bit 0 clear (value 0) -> not closed, z defaults to 0")
def _():
    p = _write("lw_open.dxf",
               "0\nSECTION\n2\nENTITIES\n"
               "0\nLWPOLYLINE\n8\nlayer1\n70\n0\n"
               "10\n0.0\n20\n0.0\n"
               "10\n5.0\n20\n0.0\n"
               "10\n5.0\n20\n3.0\n30\n2.5\n"
               "0\nENDSEC\n")
    got = rdr.read_dxf(p)
    assert got["entities"][0]["closed"] is False, got["entities"]
    assert got["entities"][0]["points"] == [
        (0.0, 0.0, 0.0), (5.0, 0.0, 0.0), (5.0, 3.0, 2.5)]
    assert got["skipped"] == {}, got["skipped"]


@check("LWPOLYLINE bulge (code 42) is dropped but COUNTED in skipped['bulge']")
def _():
    p = _write("lw_bulge.dxf",
               "0\nSECTION\n2\nENTITIES\n"
               "0\nLWPOLYLINE\n8\nlayer1\n70\n1\n"
               "10\n0.0\n20\n0.0\n42\n0.5\n"
               "10\n10.0\n20\n0.0\n42\n-0.25\n"
               "10\n10.0\n20\n10.0\n"
               "0\nENDSEC\n")
    got = rdr.read_dxf(p)
    assert got["entities"][0]["points"] == [
        (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0)]
    assert got["skipped"] == {"bulge": 2}, got["skipped"]


@check("BLOCK / INSERT / TEXT are not parsed but counted in skipped[type]")
def _():
    p = _write("skip.dxf",
               "0\nSECTION\n2\nENTITIES\n"
               "0\nLINE\n8\ncut\n62\n1\n"
               "10\n0.0\n20\n0.0\n30\n0.0\n"
               "11\n10.0\n21\n0.0\n31\n0.0\n"
               "0\nINSERT\n8\nlayer1\n2\nmyblock\n10\n5.0\n20\n5.0\n30\n0.0\n"
               "0\nTEXT\n8\nlayer1\n10\n1.0\n20\n1.0\n1\nhi\n"
               "0\nENDSEC\n")
    got = rdr.read_dxf(p)
    assert [e["type"] for e in got["entities"]] == ["LINE"], got["entities"]
    assert got["entities"][0]["p1"] == (10.0, 0.0, 0.0)
    assert got["skipped"] == {"INSERT": 1, "TEXT": 1}, got["skipped"]


@check("insunits is None when the header has no $INSUNITS")
def _():
    p = _write("nouns.dxf",
               "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1009\n0\nENDSEC\n"
               "0\nSECTION\n2\nENTITIES\n"
               "0\nLINE\n10\n0.0\n20\n0.0\n30\n0.0\n11\n1.0\n21\n1.0\n31\n0.0\n"
               "0\nENDSEC\n")
    got = rdr.read_dxf(p)
    assert got["insunits"] is None, got["insunits"]
    assert [e["type"] for e in got["entities"]] == ["LINE"]


@check("a BINARY DXF (AutoCAD Binary DXF sentinel) is refused")
def _():
    # The real binary DXF magic header, written as raw bytes.
    p = os.path.join(TMP, "bin_sentinel.dxf")
    with open(p, "wb") as fh:
        fh.write(b"AutoCAD Binary DXF\r\n\x1a\0")
    try:
        rdr.read_dxf(p)
        raise AssertionError("expected FileFormatError for a binary DXF")
    except rdr.FileFormatError as e:
        assert "binary" in str(e).lower(), str(e)


@check("a newer ASCII $ACADVER (AC1012) is refused as unsupported version")
def _():
    p = _write("bin.dxf",
               "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1012\n0\nENDSEC\n"
               "0\nSECTION\n2\nENTITIES\n0\nENDSEC\n")
    try:
        rdr.read_dxf(p)
        raise AssertionError("expected FileFormatError for unsupported version")
    except rdr.FileFormatError as e:
        assert "AC1012" in str(e), str(e)
        assert "unsupported" in str(e).lower(), str(e)


@check("a truncated file (group code with no value) raises FileFormatError")
def _():
    p = _write("trunc.dxf", "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\n")
    try:
        rdr.read_dxf(p)
        raise AssertionError("expected FileFormatError for a truncated DXF")
    except rdr.FileFormatError as e:
        assert "truncated" in str(e).lower(), str(e)


# ---------------------------------------------------------------------------
failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
import shutil  # noqa: E402
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
