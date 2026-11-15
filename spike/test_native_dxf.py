"""Required DXF writer ABI proof plus preserved, independent reader checks.

No writer oracle, auto-build, crossover, or Python writer replay. Decoder
fallback checks remain unchanged in purpose; decoder retirement is not this lease.
"""
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = Path(ROOT) / "native/pure_core/tests/fixtures/dxf_writer23"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(ROOT) / "narwhal3d" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


dxf = _load("narwhal_dxf_writer", "dxf_writer.py")
rdr = _load("narwhal_dxf_reader", "dxf_reader.py")
writer_native = dxf._native_dxf()
native = rdr._native_dxf()  # reader-only optional adapter, not a writer dependency
results = []
TMP = tempfile.mkdtemp(prefix="native_dxf_")


def check(name):
    def decorator(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as exc:
            results.append((name, "FAIL", repr(exc)))
    return decorator


def _reset():
    native._reset_for_tests()


def _captured_writer_bytes():
    # Immutable original bytes, not a call back into the now-native writer.
    return (FIXTURES / "decoder-mixed.dxf").read_bytes()


@check("required writer binds real typed Rust exports independently of reader adapter")
def _():
    binding = writer_native._binding()
    assert all(isinstance(binding[name], ctypes._CFuncPtr) for name in ("capacity", "encode", "colors", "units", "abi_version"))


@check("all original finite entity/header/name bytes match independent captures")
def _():
    captures = json.loads((FIXTURES / "capture.json").read_text())
    for row in captures["cases"]:
        before = writer_native._native_dxf_calls
        expected = (FIXTURES / row["expected_file"]).read_bytes()
        actual = dxf.build_dxf_bytes(row["entities"], row["layers"], row["units"])
        assert actual == expected, row["name"]
        assert writer_native._native_dxf_calls == before + 1


@check("file API always uses required Rust including explicit units and Latin1")
def _():
    entity = {"type": "CIRCLE", "center": (1, 2, 3), "radius": 4, "layer": "café", "true_color": 0}
    for unit in (None, "Inches", "Millimeters", "Unknown"):
        before = writer_native._native_dxf_calls
        path = os.path.join(TMP, "required.dxf")
        n = dxf.write_dxf(path, [entity], {"café": 0x1000000}, unit)
        assert writer_native._native_dxf_calls == before + 1
        data = Path(path).read_bytes()
        assert len(data) == n and b"caf\xe9" in data and b"0\nCIRCLE\n" in data
        assert b"POLYLINE" not in data


@check("capacity counts every repeated long layer name without truncation")
def _():
    import hashlib
    layer = "L" * 4096
    entities = [{"type": "POLYLINE", "layer": layer, "color": 7,
                 "points": [(float(i), 0., 0.) for i in range(200)], "closed": True} for _ in range(8)]
    expected = json.loads((FIXTURES / "capture.json").read_text())["long_names"]
    data = dxf.build_dxf_bytes(entities, {layer: 7})
    assert len(data) == expected["bytes"]
    assert hashlib.sha256(data).hexdigest() == expected["sha256"]


def _write_text(name, text):
    p = os.path.join(TMP, name)
    with open(p, "w", encoding="latin-1") as fh:
        fh.write(text)
    return p


def _py_read(path):
    """The unchanged Python reader's output (the reference), by forcing the
    native decode to ``None`` so ``read_dxf`` falls through to Python."""
    real = native.decode_dxf
    native.decode_dxf = lambda *a, **k: None
    try:
        return rdr.read_dxf(path)
    finally:
        native.decode_dxf = real


def _native_read(path):
    """``read_dxf`` with the native route active; assert the decode counter moved."""
    _reset()
    before = native._native_decode_dxf_calls
    out = rdr.read_dxf(path)
    assert native._native_decode_dxf_calls > before, \
        ("route did not use native decode (counter %d -> %d)" %
         (before, native._native_decode_dxf_calls))
    return out


@check("read_dxf activates the native decode route (dict matches the Python reader)")
def _():
    # Write the file with the PYTHON writer (isolate the decoder under test --
    # the encoder route is a separate, already-proven accelerator).
    p = os.path.join(TMP, "rt.dxf")
    with open(p, "wb") as fh:
        fh.write(_captured_writer_bytes())
    want = _py_read(p)          # the unchanged Python reader's dict
    got = _native_read(p)       # native route; asserts the counter moved
    assert got == want, (got, want)
    # Spot-check the key fields so a silent field-drop is not missed.
    assert got["insunits"] == 4
    assert got["layers"] == {"0": 7, "walls": 1}
    assert [e["type"] for e in got["entities"]] == \
        ["LINE", "CIRCLE", "ARC", "POLYLINE"]
    assert got["skipped"] == {}


@check("native decode: LWPOLYLINE closed flag (70 bit 0) + points in order")
def _():
    p = _write_text("lw_closed.dxf",
                    "0\nSECTION\n2\nENTITIES\n"
                    "0\nLWPOLYLINE\n8\nlayer1\n62\n5\n70\n1\n"
                    "10\n0.0\n20\n0.0\n30\n0.0\n"
                    "10\n10.0\n20\n0.0\n30\n0.0\n"
                    "10\n10.0\n20\n10.0\n30\n0.0\n"
                    "10\n0.0\n20\n10.0\n30\n0.0\n"
                    "0\nENDSEC\n")
    want = _py_read(p)
    got = _native_read(p)
    assert got == want, (got, want)
    e = got["entities"][0]
    assert e["type"] == "LWPOLYLINE"
    assert e["closed"] is True, got
    assert e["points"] == [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0),
                           (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)], got
    assert e["layer"] == "layer1" and e["color"] == 5
    # A LAYER-free file: the entity names a free-string layer, not an index.
    assert got["layers"] == {}


@check("native decode: modern ASCII SPLINE is structurally identical to Python")
def _():
    p = _write_text(
        "modern_spline.dxf",
        "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1027\n0\nENDSEC\n"
        "0\nSECTION\n2\nENTITIES\n"
        "0\nSPLINE\n8\ncut\n62\n1\n420\n16711680\n"
        "70\n9\n71\n3\n72\n8\n73\n4\n74\n0\n"
        "40\n0\n40\n0\n40\n0\n40\n0\n40\n1\n40\n1\n40\n1\n40\n1\n"
        "10\n0\n20\n0\n30\n0\n10\n1\n20\n0\n30\n0\n"
        "10\n1\n20\n1\n30\n0\n10\n0\n20\n0\n30\n0\n"
        "0\nHATCH\n8\ncut\n0\nENDSEC\n")
    want = _py_read(p)
    got = _native_read(p)
    assert got == want, (got, want)
    spline = got["entities"][0]
    assert spline["type"] == "SPLINE" and spline["degree"] == 3
    assert spline["closed"] and len(spline["knots"]) == 8
    assert spline["color"] == 7 and spline["true_color"] == 0xFF0000
    assert spline["points"][0] == spline["points"][-1]
    assert got["skipped"] == {"HATCH": 1}


@check("native decode: LINE group 420 true color survives packed decoding")
def _():
    p = _write_text(
        "true_color_line.dxf",
        "0\nSECTION\n2\nENTITIES\n"
        "0\nLINE\n8\nRegister\n62\n7\n420\n0\n"
        "10\n0\n20\n0\n30\n0\n11\n1\n21\n0\n31\n0\n"
        "0\nENDSEC\n")
    want = _py_read(p)
    got = _native_read(p)
    assert got == want, (got, want)
    assert got["entities"][0]["color"] == 7
    assert got["entities"][0]["true_color"] == 0x000000


@check("native decode: LWPOLYLINE bulge (code 42) dropped but counted")
def _():
    p = _write_text("lw_bulge.dxf",
                    "0\nSECTION\n2\nENTITIES\n"
                    "0\nLWPOLYLINE\n8\nlayer1\n70\n1\n"
                    "10\n0.0\n20\n0.0\n42\n0.5\n"
                    "10\n10.0\n20\n0.0\n42\n-0.25\n"
                    "10\n10.0\n20\n10.0\n"
                    "0\nENDSEC\n")
    want = _py_read(p)
    got = _native_read(p)
    assert got == want, (got, want)
    assert got["skipped"] == {"bulge": 2}, got
    assert got["entities"][0]["points"] == [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0),
                                             (10.0, 10.0, 0.0)], got


@check("native decode: insunits None when the header has no $INSUNITS")
def _():
    p = _write_text("nouns.dxf",
                    "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1009\n0\nENDSEC\n"
                    "0\nSECTION\n2\nENTITIES\n"
                    "0\nLINE\n10\n0.0\n20\n0.0\n30\n0.0\n11\n1.0\n21\n1.0\n31\n0.0\n"
                    "0\nENDSEC\n")
    want = _py_read(p)
    got = _native_read(p)
    assert got == want, (got, want)
    assert got["insunits"] is None, got


@check("native decode: INSERT/TEXT/POINT counted in skipped[type]")
def _():
    p = _write_text("skip.dxf",
                    "0\nSECTION\n2\nENTITIES\n"
                    "0\nLINE\n8\ncut\n62\n1\n"
                    "10\n0.0\n20\n0.0\n30\n0.0\n"
                    "11\n10.0\n21\n0.0\n31\n0.0\n"
                    "0\nINSERT\n8\nl1\n2\nb1\n10\n5.0\n20\n5.0\n"
                    "0\nTEXT\n8\nl1\n10\n1.0\n20\n1.0\n1\nhi\n"
                    "0\nPOINT\n10\n2.0\n20\n2.0\n"
                    "0\nENDSEC\n")
    want = _py_read(p)
    got = _native_read(p)
    assert got == want, (got, want)
    assert [e["type"] for e in got["entities"]] == ["LINE"], got
    assert got["skipped"] == {"INSERT": 1, "POINT": 1, "TEXT": 1}, got


def _assert_decode_fallback_raises(path, label, exc_substr):
    """A rejection check: the native route refuses the file (so the counter is
    NOT incremented) and the unchanged Python reader raises FileFormatError."""
    _reset()
    before = native._native_decode_dxf_calls
    try:
        rdr.read_dxf(path)
        raise AssertionError((label, "expected FileFormatError"))
    except rdr.FileFormatError as e:
        assert exc_substr in str(e).lower(), (label, str(e))
    assert native._native_decode_dxf_calls == before, \
        (label, "native decode counter must not increment on a rejection",
         before, native._native_decode_dxf_calls)


@check("decoder fallback: a binary DXF is refused (Python raises, counter NOT moved)")
def _():
    p = os.path.join(TMP, "bin_sentinel.dxf")
    with open(p, "wb") as fh:
        fh.write(b"AutoCAD Binary DXF\r\n\x1a\0")
    _assert_decode_fallback_raises(p, "binary", "binary")


@check("decoder fallback: a truncated file is refused (Python raises, counter NOT moved)")
def _():
    p = _write_text("trunc.dxf", "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\n")
    _assert_decode_fallback_raises(p, "truncated", "truncated")


@check("decoder fallback: a library without the DXF decode exports -> Python reader")
def _():
    _reset()
    assert native.available() is True, native.backend_status()
    p = os.path.join(TMP, "fb_nodecode.dxf")
    with open(p, "wb") as fh:
        fh.write(_captured_writer_bytes())
    real_binding = native._binding

    def _binding_no_decode_dxf():
        b = real_binding()
        if b is None:
            return None
        # Decode handles remain the last two slots before the library path.
        return (*b[:-3], None, None, b[-1])

    native._binding = _binding_no_decode_dxf
    try:
        before = native._native_decode_dxf_calls
        got = rdr.read_dxf(p)
        want = _py_read(p)
        assert got == want, (got, want)
        assert native._native_decode_dxf_calls == before, \
            "counter must not move when the decode exports are absent"
    finally:
        native._binding = real_binding


@check("decoder fallback: a malformed native output -> Python reader")
def _():
    _reset()
    assert native.available() is True, native.backend_status()
    p = os.path.join(TMP, "fb_malformed.dxf")
    with open(p, "wb") as fh:
        fh.write(_captured_writer_bytes())
    real = native.decode_dxf
    native.decode_dxf = lambda *a, **k: None  # simulate a failed decode
    try:
        before = native._native_decode_dxf_calls
        got = rdr.read_dxf(p)
        want = _py_read(p)
        assert got == want, (got, want)
        assert native._native_decode_dxf_calls == before, \
            "counter must not move when the native decode fails"
    finally:
        native.decode_dxf = real


def _dxf_packed(*, typ=0, closed=0, coord_count=6, coords=None,
                entity_count=1, trailing=b""):
    if coords is None:
        coords = (0.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    parts = [struct.pack("<IIq", 0x44584631, 1, 4), struct.pack("<q", 0),
             struct.pack("<q", entity_count)]
    if entity_count == 1:
        parts.extend([
            struct.pack("<q", typ), struct.pack("<q", 1), b"0",
            struct.pack("<q", 7), struct.pack("<q", closed),
            struct.pack("<q", coord_count),
            struct.pack("<%dd" % len(coords), *coords),
        ])
    parts.extend([struct.pack("<q", 0), trailing])
    return b"".join(parts)


@check("strict DXF packed validation rejects counts, arity, bools, and trailing bytes")
def _():
    assert native._parse_dxf_packed(_dxf_packed())["entities"][0]["type"] == "LINE"
    bad_payloads = [
        _dxf_packed(entity_count=-1),
        _dxf_packed(typ=9),
        _dxf_packed(closed=2),
        _dxf_packed(coord_count=5, coords=(0, 0, 0, 1, 0)),
        _dxf_packed(coord_count=-1, coords=()),
        _dxf_packed(trailing=b"x"),
    ]
    for payload in bad_payloads:
        try:
            native._parse_dxf_packed(payload)
            raise AssertionError("malformed packed DXF was accepted: %r" % payload[:40])
        except ValueError:
            pass


@check("malformed successful DXF packed output returns None without counting")
def _():
    _reset()
    real_binding = native._binding
    base = real_binding()
    payload = _dxf_packed(closed=2)

    def size_fn(_data, _length):
        return len(payload)

    def decode_fn(_data, _length, out, out_len):
        assert out_len == len(payload)
        ctypes.memmove(out, payload, len(payload))
        return 0

    native._binding = lambda: (*base[:-3], size_fn, decode_fn, base[-1])
    try:
        before = native._native_decode_dxf_calls
        assert native.decode_dxf((FIXTURES / "decoder-no-layers.dxf").read_bytes()) is None
        assert native._native_decode_dxf_calls == before
    finally:
        native._binding = real_binding



failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f" -- {detail}" if detail else ""))
    failed += status == "FAIL"
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
