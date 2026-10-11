"""Pure-Python tests for the PDF, SVG, and AI vector writers.

The module only WRITES strokes the caller has already flattened to cubic
Beziers; these tests pin the file-format invariants:

* the PDF xref table holds the TRUE byte offset of every object (so a reader
  can seek straight to ``N 0 obj``), and ``startxref`` points at ``xref``;
* a known millimetre coordinate appears in the content stream converted to
  points (10 mm -> ``28.3465``);
* the MediaBox for A4 (210x297 mm) is ``595.2756 x 841.8898`` points;
* the file survives ``pdfinfo`` (Pages: 1, right Page size) and ``gs``
  (exit 0, empty stderr) -- skipped with a printed SKIP if either is missing;
* the SVG parses, carries the mm viewBox, and a point at y=0 mm lands at
  y=H in the path (y is flipped);
* ``write_ai`` produces a file ``pdfinfo`` accepts (the .ai IS a PDF -- OURS).

"""
import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, "narwhal3d", filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ve = _load("narwhal_vector_export", "vector_export.py")
MM_TO_PT = ve.MM_TO_PT

results = []
TMP = tempfile.mkdtemp(prefix="vecexp_")


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


def _read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def _read_text(path):
    return _read_bytes(path).decode("latin-1")


def _content_stream(data: bytes) -> str:
    """Return the bytes between ``stream`` and ``endstream``."""
    i = data.find(b"stream\n")
    assert i != -1, "no `stream` keyword"
    i += len(b"stream\n")
    j = data.find(b"\nendstream", i)
    assert j != -1, "no `endstream` keyword"
    return data[i:j].decode("latin-1")


def _xref_offsets(data: bytes):
    """Parse the xref table; return the (n, {obj#: offset}) mapping."""
    i = data.rfind(b"\nxref\n")
    assert i != -1, "no xref table"
    j = data.find(b"trailer", i)
    block = data[i + 1:j].decode("latin-1")
    lines = block.splitlines()
    assert lines[0] == "xref", lines[0]
    m = re.match(r"^0 (\d+)$", lines[1])
    assert m, lines[1]
    n = int(m.group(1))
    offs = {}
    for k in range(n):
        entry = lines[2 + k]
        off = int(entry[:10])
        gen = entry[11:16]
        flag = entry[17]
        assert flag in ("n", "f")
        if flag == "n":
            assert gen == "00000", entry
            offs[k] = off
    return n, offs


def _startxref(data: bytes) -> int:
    i = data.rfind(b"startxref\n")
    assert i != -1, "no startxref"
    return int(data[i + len(b"startxref\n"):].split(b"\n")[0])


A4W, A4H = 210.0, 297.0
# A known stroke: one cubic bezier, closed, red, 0.5 mm wide.
STROKE = ve.Stroke(
    beziers=(((10.0, 20.0), (30.0, 40.0), (50.0, 60.0), (70.0, 80.0)),),
    closed=True,
    rgb=(1.0, 0.0, 0.0),
    width_mm=0.5,
)


@check("module loads without bpy and Stroke is frozen")
def _():
    assert "bpy" not in sys.modules
    import dataclasses
    assert dataclasses.is_dataclass(ve.Stroke)
    s = ve.Stroke(beziers=())
    try:
        s.rgb = (1.0, 1.0, 1.0)  # frozen -> must raise
        raise AssertionError("frozen dataclass accepted a mutation")
    except dataclasses.FrozenInstanceError:
        pass


@check("write_pdf returns the byte count actually written")
def _():
    p = os.path.join(TMP, "count.pdf")
    n = ve.write_pdf(p, [STROKE], A4W, A4H, "T")
    assert n == os.path.getsize(p) and n > 0


# ---------------------------------------------------------------------------
# 1. xref offsets + startxref are TRUE
# ---------------------------------------------------------------------------
@check("every xref offset points at the bytes `N 0 obj`")
def _():
    p = os.path.join(TMP, "xref.pdf")
    ve.write_pdf(p, [STROKE], A4W, A4H, "T")
    data = _read_bytes(p)
    n, offs = _xref_offsets(data)
    assert 1 in offs and 5 in offs, offs
    for objnum, off in offs.items():
        tail = data[off:off + len(str(objnum)) + len(" 0 obj")]
        assert tail.decode("latin-1") == f"{objnum} 0 obj", \
            f"object {objnum} offset {off} -> {tail!r}"


@check("startxref points at `xref`")
def _():
    p = os.path.join(TMP, "sxr.pdf")
    ve.write_pdf(p, [STROKE], A4W, A4H, "T")
    data = _read_bytes(p)
    assert data[_startxref(data):_startxref(data) + 4] == b"xref"


@check("xref entry lines are exactly 20 bytes incl. terminator")
def _():
    p = os.path.join(TMP, "xlen.pdf")
    ve.write_pdf(p, [STROKE], A4W, A4H, "T")
    data = _read_bytes(p)
    i = data.rfind(b"\nxref\n")
    j = data.find(b"trailer", i)
    block = data[i + 1:j]
    lines = block.splitlines(keepends=True)
    entries = lines[2:2 + 5]  # objects 0..4 (five objects)
    for line in entries:
        assert len(line) == 20, (line, len(line))


@check("trailer /Size and /Root are present")
def _():
    p = os.path.join(TMP, "trail.pdf")
    ve.write_pdf(p, [STROKE], A4W, A4H, "T")
    data = _read_bytes(p)
    n, _ = _xref_offsets(data)
    text = data.decode("latin-1")
    assert f"/Size {n}" in text
    assert "/Root 1 0 R" in text
    assert "/Info 5 0 R" in text


# ---------------------------------------------------------------------------
# 2. a known coordinate is converted to points in the content stream
# ---------------------------------------------------------------------------
@check("10 mm appears in the content stream as 28.3465 points")
def _():
    p = os.path.join(TMP, "pts.pdf")
    ve.write_pdf(p, [STROKE], A4W, A4H, "T")
    cs = _content_stream(_read_bytes(p))
    assert "28.3465" in cs, cs  # 10 mm * 72/25.4
    # the move-to carries both start coordinates
    assert "28.3465 56.6929 m" in cs, cs  # (10,20) mm


@check("content stream has one `c` per bezier, `h` when closed, ends `S`")
def _():
    p = os.path.join(TMP, "ops.pdf")
    ve.write_pdf(p, [STROKE], A4W, A4H, "T")
    cs = _content_stream(_read_bytes(p)).splitlines()
    # one cubic operator (the `c` is the last token of its line)
    assert sum(1 for ln in cs if ln.endswith(" c")) == 1
    assert "h" in cs
    assert cs[-1] == "S"


@check("RGB + width + stroke operators are present")
def _():
    p = os.path.join(TMP, "ops2.pdf")
    ve.write_pdf(p, [STROKE], A4W, A4H, "T")
    cs = _content_stream(_read_bytes(p))
    assert "1.0000 0.0000 0.0000 RG" in cs
    # 0.5 mm * 72/25.4 = 1.4173 points
    assert "1.4173 w" in cs


# ---------------------------------------------------------------------------
# 3. MediaBox for A4
# ---------------------------------------------------------------------------
@check("A4 (210x297 mm) MediaBox is 595.2756 x 841.8898 points")
def _():
    p = os.path.join(TMP, "a4.pdf")
    ve.write_pdf(p, [STROKE], A4W, A4H, "T")
    text = _read_text(p)
    m = re.search(r"/MediaBox \[0 0 ([0-9.]+) ([0-9.]+)\]", text)
    assert m, text
    w, h = float(m.group(1)), float(m.group(2))
    assert abs(w - A4W * MM_TO_PT) <= 1e-3, w
    assert abs(h - A4H * MM_TO_PT) <= 1e-3, h


@check("Info dict carries /Title and /Producer (Narwhal 3D)")
def _():
    p = os.path.join(TMP, "info.pdf")
    ve.write_pdf(p, [STROKE], A4W, A4H, "A Draw")
    text = _read_text(p)
    assert "/Title (A Draw)" in text
    assert "/Producer (Narwhal 3D)" in text


# ---------------------------------------------------------------------------
# 4. pdfinfo: exits 0, Pages: 1, right Page size
# ---------------------------------------------------------------------------
@check("pdfinfo reports Pages: 1 and the A4 page size")
def _():
    if not shutil.which("pdfinfo"):
        print("  SKIP: pdfinfo not found on PATH")
        return
    p = os.path.join(TMP, "pi.pdf")
    ve.write_pdf(p, [STROKE], A4W, A4H, "T")
    out = subprocess.run(["pdfinfo", p], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "Pages:           1" in out.stdout, out.stdout
    # A4 page size in points, as pdfinfo rounds it.
    m = re.search(r"Page size:\s+([0-9.]+) x ([0-9.]+)", out.stdout)
    assert m, out.stdout
    assert abs(float(m.group(1)) - A4W * MM_TO_PT) <= 1e-3
    assert abs(float(m.group(2)) - A4H * MM_TO_PT) <= 1e-3
    assert "Producer:        Narwhal 3D" in out.stdout


# ---------------------------------------------------------------------------
# 5. ghostscript renders with exit 0 and empty stderr
# ---------------------------------------------------------------------------
@check("gs renders the PDF with exit 0 and empty stderr")
def _():
    if not shutil.which("gs"):
        print("  SKIP: gs not found on PATH")
        return
    p = os.path.join(TMP, "gs.pdf")
    ve.write_pdf(p, [STROKE], A4W, A4H, "T")
    out = subprocess.run(
        ["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=nullpage", p],
        capture_output=True, text=True)
    assert out.returncode == 0, (out.returncode, out.stderr)
    assert out.stderr.strip() == "", out.stderr


# ---------------------------------------------------------------------------
# 6. SVG parses, has the viewBox, and y is flipped
# ---------------------------------------------------------------------------
@check("SVG parses, has the mm viewBox, and a y=0 point lands at y=H")
def _():
    p = os.path.join(TMP, "flip.svg")
    s = ve.Stroke(beziers=(((0.0, 0.0), (0.0, 50.0), (100.0, 50.0), (100.0, 0.0)),),
                  closed=False)
    ve.write_svg(p, [s], A4W, A4H)
    root = ET.parse(p).getroot()
    assert root.tag == "{http://www.w3.org/2000/svg}svg", root.tag
    assert root.get("width") == "210.0000"
    assert root.get("height") == "297.0000"
    assert root.get("viewBox") == "0 0 210.0000 297.0000"
    path = root.find(".//{http://www.w3.org/2000/svg}path")
    assert path is not None
    d = path.get("d")
    # start point (0,0) mm -> y = H - 0 = 297 in the path
    assert d.startswith("M 0.0000 297.0000"), d
    assert path.get("fill") == "none"
    assert path.get("stroke-width") == "0.2500"


@check("SVG one <path> per stroke with stroke colour")
def _():
    p = os.path.join(TMP, "two.svg")
    a = ve.Stroke(beziers=(((0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0)),),
                  rgb=(0.0, 0.0, 1.0))
    b = ve.Stroke(beziers=(((0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0)),),
                  rgb=(0.0, 1.0, 0.0))
    ve.write_svg(p, [a, b], A4W, A4H)
    root = ET.parse(p).getroot()
    ns = "{http://www.w3.org/2000/svg}"
    paths = root.findall(f".//{ns}path")
    assert len(paths) == 2
    assert paths[0].get("stroke") == "rgb(0,0,255)"
    assert paths[1].get("stroke") == "rgb(0,255,0)"


# ---------------------------------------------------------------------------
# 7. write_ai is a PDF pdfinfo accepts
# ---------------------------------------------------------------------------
@check("write_ai produces a file pdfinfo accepts")
def _():
    if not shutil.which("pdfinfo"):
        print("  SKIP: pdfinfo not found on PATH")
        return
    p = os.path.join(TMP, "ai.ai")
    ve.write_ai(p, [STROKE], A4W, A4H, "T")
    out = subprocess.run(["pdfinfo", p], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "Pages:           1" in out.stdout


@check("empty strokes is a valid blank page")
def _():
    p = os.path.join(TMP, "blank.pdf")
    n = ve.write_pdf(p, [], A4W, A4H, "")
    assert n > 0
    data = _read_bytes(p)
    assert data.startswith(b"%PDF-1.4")
    # still parses; the content stream may be empty
    assert b"stream\n" in data and b"endstream" in data


# ---------------------------------------------------------------------------
# 8. PDF / SVG layers
# ---------------------------------------------------------------------------
# Byte-identity guard. These digests pin the no-layer output after the approved
# product-identity change to Narwhal 3D; any later drift is a real regression, not
# a test artifact. (Comparing two outputs of the NEW code would prove nothing:
# both would carry the same change.)
REF_PDF_SHA256 = "f27a33fce8f37c0d85e7d4e2c7f9daac8a705945211c3918fcb7788a2157b81f"
REF_SVG_SHA256 = "1698c19afe809ccba1700789f9b370d758711af57cf5396dc7506eee5bcacb4a"


def _ref_strokes():
    """The fixed two-stroke list whose no-layer output the hashes pin."""
    return [
        ve.Stroke(beziers=(((10, 20), (30, 40), (50, 60), (70, 80)),),
                  closed=False, rgb=(1, 0, 0), width_mm=0.5),
        ve.Stroke(beziers=(((0, 0), (20, 10), (40, 10), (60, 0)),
                           ((60, 0), (80, 10), (100, 10), (120, 0)),),
                  closed=True, rgb=(0, 0.5, 1), width_mm=1.0),
    ]


def _layered_strokes():
    """Two distinct layers, first-seen order Top -> Walls (x2)."""
    return [
        ve.Stroke(beziers=(((10, 20), (30, 40), (50, 60), (70, 80)),),
                  closed=False, rgb=(1, 0, 0), width_mm=0.5, layer="Top"),
        ve.Stroke(beziers=(((0, 0), (20, 10), (40, 10), (60, 0)),),
                  closed=True, rgb=(0, 1, 0), width_mm=1.0,
                  layer="Walls (x2)"),
        ve.Stroke(beziers=(((5, 5), (15, 15), (25, 15), (35, 5)),),
                  closed=False, rgb=(0, 0, 1), width_mm=0.5, layer="Top"),
    ]


@check("no-layer PDF is byte-identical to the reference hash")
def _():
    p = os.path.join(TMP, "ref.pdf")
    ve.write_pdf(p, _ref_strokes(), A4W, A4H, title="ref")
    got = hashlib.sha256(_read_bytes(p)).hexdigest()
    assert got == REF_PDF_SHA256, got


@check("no-layer SVG is byte-identical to the reference hash")
def _():
    p = os.path.join(TMP, "ref.svg")
    ve.write_svg(p, _ref_strokes(), A4W, A4H)
    got = hashlib.sha256(_read_bytes(p)).hexdigest()
    assert got == REF_SVG_SHA256, got


@check("Stroke.layer defaults to empty string as the last field")
def _():
    import dataclasses
    s = ve.Stroke(beziers=())
    assert s.layer == ""
    fields = [f.name for f in dataclasses.fields(ve.Stroke)]
    assert fields[-1] == "layer", fields


@check("two layers produce two OCG objects and preserve names")
def _():
    p = os.path.join(TMP, "ocg.pdf")
    ve.write_pdf(p, _layered_strokes(), A4W, A4H, title="layers")
    text = _read_text(p)
    # exactly two Optional Content Group objects
    assert text.count("/Type /OCG") == 2, text.count("/Type /OCG")
    # the names survive the PDF literal-string escaping and round-trip back
    assert "/Name (Top)" in text, text
    assert "/Name (Walls \\(x2\\))" in text, text
    # and the RAW (unescaped) form must NOT be present -- proves the escape ran
    assert "/Name (Walls (x2))" not in text


@check("every BDC has a matching EMC")
def _():
    p = os.path.join(TMP, "bdc.pdf")
    ve.write_pdf(p, _layered_strokes(), A4W, A4H, title="layers")
    cs = _content_stream(_read_bytes(p))
    # PDF property resource names are Name objects and require the slash:
    # `/OC /L0 BDC`. Without it Poppler reads `L0` as an unknown operator and
    # the entire layered drawing converts/imports as a blank page.
    bdc = len(re.findall(r"/OC /L\d+ BDC", cs))
    emc = cs.count("EMC")
    assert bdc == emc and bdc == 3, (bdc, emc)  # 3 layered strokes, 2 distinct layers


@check("OCProperties /OCGs and /Order are present")
def _():
    p = os.path.join(TMP, "ocp.pdf")
    ve.write_pdf(p, _layered_strokes(), A4W, A4H, title="layers")
    text = _read_text(p)
    assert "/OCProperties" in text
    assert "/OCGs [" in text
    assert "/Order [" in text
    # the page /Resources carries the L0/L1 names
    assert "/Properties << /L0 6 0 R /L1 7 0 R >>" in text, text


@check("xref offsets remain correct with layers present")
def _():
    p = os.path.join(TMP, "xrefl.pdf")
    ve.write_pdf(p, _layered_strokes(), A4W, A4H, title="layers")
    data = _read_bytes(p)
    n, offs = _xref_offsets(data)
    # 5 base objects + 2 OCG objects = objects 1..7, so the table lists
    # entries 0..7 -> `0 8` (n is the ENTRY count, not the highest object#).
    assert n == 8, n
    assert 6 in offs and 7 in offs, offs
    for objnum, off in offs.items():
        tail = data[off:off + len(str(objnum)) + len(" 0 obj")]
        assert tail.decode("latin-1") == f"{objnum} 0 obj", \
            f"object {objnum} offset {off} -> {tail!r}"


@check("layered PDF is accepted by pdfinfo and gs")
def _():
    if not (shutil.which("pdfinfo") and shutil.which("gs")):
        print("  SKIP: pdfinfo/gs not found on PATH")
        return
    p = os.path.join(TMP, "layer_ok.pdf")
    ve.write_pdf(p, _layered_strokes(), A4W, A4H, title="layers")
    info = subprocess.run(["pdfinfo", p], capture_output=True, text=True)
    assert info.returncode == 0, info.stderr
    assert "Pages:           1" in info.stdout, info.stdout
    gs = subprocess.run(
        ["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=nullpage", p],
        capture_output=True, text=True)
    assert gs.returncode == 0, (gs.returncode, gs.stderr)
    assert gs.stderr.strip() == "", gs.stderr


@check("SVG has one labelled group per layer in first-seen order")
def _():
    p = os.path.join(TMP, "layer.svg")
    ve.write_svg(p, _layered_strokes(), A4W, A4H)
    root = ET.parse(p).getroot()
    ns = "{http://www.w3.org/2000/svg}"
    inks = "{http://www.inkscape.org/namespaces/inkscape}"
    # the inkscape namespace is declared on the root svg element. ElementTree
    # does not surface `xmlns` declarations in `root.attrib`, so check the raw
    # bytes (the namespace IS there -- the label lookups below prove it).
    raw = _read_text(p)
    assert 'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"' \
        in raw, raw[:200]
    groups = root.findall(f"{ns}g")
    assert len(groups) == 2, len(groups)
    assert [g.get(inks + "label") for g in groups] == ["Top", "Walls (x2)"], \
        [g.get(inks + "label") for g in groups]
    assert [g.get("id") for g in groups] == ["layer-0", "layer-1"], \
        [g.get("id") for g in groups]
    assert all(g.get(inks + "groupmode") == "layer" for g in groups)
    # each layer's own paths: Top has 2 strokes, Walls (x2) has 1
    top_paths = groups[0].findall(f"{ns}path")
    walls_paths = groups[1].findall(f"{ns}path")
    assert len(top_paths) == 2 and len(walls_paths) == 1, \
        (len(top_paths), len(walls_paths))


@check("SVG escapes XML-special characters in layer labels")
def _():
    p = os.path.join(TMP, "xmlesc.svg")
    s = ve.Stroke(beziers=(((0, 0), (1, 1), (2, 2), (3, 3)),),
                  rgb=(0, 0, 1), layer='A & B <"c">')
    ve.write_svg(p, [s], A4W, A4H)
    # the raw attribute value must carry the escaped form
    raw = _read_text(p)
    assert 'inkscape:label="A &amp; B &lt;&quot;c&quot;&gt;"' in raw, raw[:300]
    # ...and the parser decodes it back to the original name
    root = ET.parse(p).getroot()
    ns = "{http://www.w3.org/2000/svg}"
    inks = "{http://www.inkscape.org/namespaces/inkscape}"
    g = root.find(f"{ns}g")
    assert g is not None
    assert g.get(inks + "label") == 'A & B <"c">', g.get(inks + "label")


@check("SVG keeps an unlayered stroke outside layer groups")
def _():
    p = os.path.join(TMP, "mix.svg")
    a = ve.Stroke(beziers=(((0, 0), (1, 1), (2, 2), (3, 3)),),
                  rgb=(0, 0, 1), layer="Top")
    b = ve.Stroke(beziers=(((4, 4), (5, 5), (6, 6), (7, 7)),),
                  rgb=(1, 1, 0))  # no layer -> outside any group
    ve.write_svg(p, [a, b], A4W, A4H)
    root = ET.parse(p).getroot()
    ns = "{http://www.w3.org/2000/svg}"
    # the unlayered path is a DIRECT child of the root svg
    direct = [c for c in root if c.tag == ns + "path"]
    assert len(direct) == 1, len(direct)
    # the layered path is inside its group
    assert root.find(f"{ns}g/{ns}path") is not None


# ---------------------------------------------------------------------------
failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
