"""Required DXF writer ABI proof plus preserved, independent reader checks.

No runtime reference parser, auto-build, crossover, or Python replay.
Reader expected values are independent captures of the original implementation.
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
native = rdr._native_dxf()  # required reader adapter, independent of writer
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
    native._cache = None


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
    # Frozen input hashes identify exact LF/Latin1 bytes on every host.
    Path(p).write_bytes(text.encode("latin-1"))
    return p


def _captured_read(path):
    from dxf_reader24_fixtures import expected_file
    return expected_file(path)


def _native_read(path):
    """``read_dxf`` with the native route active; assert the decode counter moved."""
    _reset()
    before = native._native_decode_dxf_calls
    out = rdr.read_dxf(path)
    assert native._native_decode_dxf_calls > before, \
        ("route did not use native decode (counter %d -> %d)" %
         (before, native._native_decode_dxf_calls))
    return out


@check("read_dxf activates the native decode route (dict matches frozen original reader)")
def _():
    # Frozen input bytes; expected reader result was independently captured.
    p = os.path.join(TMP, "rt.dxf")
    with open(p, "wb") as fh:
        fh.write(_captured_writer_bytes())
    want = _captured_read(p)          # frozen original-reader dict
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
    want = _captured_read(p)
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


@check("native decode: modern ASCII SPLINE is structurally identical to frozen original")
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
    want = _captured_read(p)
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
    want = _captured_read(p)
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
    want = _captured_read(p)
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
    want = _captured_read(p)
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
    want = _captured_read(p)
    got = _native_read(p)
    assert got == want, (got, want)
    assert [e["type"] for e in got["entities"]] == ["LINE"], got
    assert got["skipped"] == {"INSERT": 1, "POINT": 1, "TEXT": 1}, got


def _assert_decode_raises(path, label, exc_substr):
    """Bad-file rejection is distinct from a missing required native library."""
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


@check("required decoder: binary DXF is refused without success counting")
def _():
    p = os.path.join(TMP, "bin_sentinel.dxf")
    with open(p, "wb") as fh:
        fh.write(b"AutoCAD Binary DXF\r\n\x1a\0")
    _assert_decode_raises(p, "binary", "binary")


@check("required decoder: truncated DXF is refused without success counting")
def _():
    p = _write_text("trunc.dxf", "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\n")
    _assert_decode_raises(p, "truncated", "truncated")


@check("required decoder missing library refuses explicitly without replay")
def _():
    old = os.environ.get("NARWHAL_PURE_CORE")
    before = native._native_decode_dxf_calls
    try:
        os.environ["NARWHAL_PURE_CORE"] = os.path.join(TMP, "missing-library")
        native._cache = None
        try:
            native.decode_dxf(_captured_writer_bytes())
        except native.NativeDXFReaderError as exc:
            assert "missing or stale" in str(exc)
        else:
            raise AssertionError("missing required decoder was accepted")
        assert native._native_decode_dxf_calls == before
    finally:
        if old is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = old
        native._cache = None


@check("required decoder malformed successful output fails without counting")
def _():
    real = native._binding
    before = native._native_decode_dxf_calls
    def decode(_data, _length, _limit, out, _capacity, written, _diag):
        ctypes.memmove(out, b"badmagic", 8)
        ctypes.cast(written, ctypes.POINTER(ctypes.c_size_t))[0] = 8
        return 0
    native._binding = lambda: (None, decode)
    try:
        try:
            native.decode_dxf(_captured_writer_bytes())
        except native.NativeDXFReaderError as exc:
            assert "magic" in str(exc)
        else:
            raise AssertionError("malformed output was accepted")
        assert native._native_decode_dxf_calls == before
    finally:
        native._binding = real


failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f" -- {detail}" if detail else ""))
    failed += status == "FAIL"
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
