"""Native DXF encoder: direct ABI proof + route activation + fallbacks.

Complements ``spike/test_dxf_writer.py`` (which pins the file-format
invariants on the Python path) by proving the *native* route is the one
actually running:

* the Rust ABI is executed directly (not through the Python fallback), and
  ``available()`` is true;
* ``write_dxf`` uses the native route -- the ``_native_dxf_calls`` counter
  must move, and the file must equal a direct native encode AND equal the
  unchanged Python writer (byte-identity);
* the POLYLINE closed flag is correct natively (``70/1`` when closed,
  ``70/0`` when open);
* every fallback scenario (missing library, a library without the DXF
  exports, a native domain error, a malformed native output) all run the
  *unchanged Python writer* with correct output and with the counter NOT
  incremented.

The suite is green with or without a prebuilt ``.so``: if the library is not
present it builds the crate once (offline, pinned toolchain) and rechecks.
"""
import ctypes  # noqa: F401 -- reserved for a direct ctypes ABI probe if needed
import importlib.util
import os
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_CANDIDATES = [
    os.environ.get("NARWHAL_PURE_CORE", ""),
    os.path.join(ROOT, "native", "pure_core", "target",
                 "x86_64-unknown-linux-gnu", "release",
                 "libnarwhal_pure_core.so"),
    os.path.join(ROOT, "native", "pure_core", "target", "release",
                 "libnarwhal_pure_core.so"),
]


def _locate_lib():
    for cand in LIB_CANDIDATES:
        if cand and os.path.isfile(cand):
            return cand
    return None


def _ensure_lib():
    """Return the ``.so`` path, building the crate once if it is not present."""
    lib = _locate_lib()
    if lib is not None:
        return lib
    manifest = os.path.join(ROOT, "native", "pure_core", "Cargo.toml")
    if not os.path.isfile(manifest):
        return None
    # Offline, pinned toolchain, target triple matching the runtime.
    subprocess.run(
        ["cargo", "+1.95.0", "build", "--release", "--locked", "--offline",
         "--manifest-path", manifest, "--target", "x86_64-unknown-linux-gnu"],
        cwd=ROOT, check=True,
    )
    return _locate_lib()


# Make the native library discoverable before the adapter is first imported.
_LIB = _ensure_lib()
if _LIB is not None:
    os.environ["NARWHAL_PURE_CORE"] = _LIB


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, "narwhal3d", filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


dxf = _load("narwhal_dxf_writer", "dxf_writer.py")
rdr = _load("narwhal_dxf_reader", "dxf_reader.py")
# Resolve the SAME native module the writer uses, so we can inspect its
# counter and monkeypatch it in the malformed-output test. (The reader shares
# this cached module, so ``rdr._native_dxf()`` is the very same object.)
native = dxf._native_dxf()

results = []
TMP = tempfile.mkdtemp(prefix="native_dxf_")


# The same 4-entity fixture as ``spike/test_dxf_writer.py`` (LINE, CIRCLE,
# ARC, closed POLYLINE). The byte-identity between the native encoder and
# the Python writer is the whole point of the accelerator.
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


def _py_reference(entities, layers=None):
    """The unchanged Python writer's output (the byte-identity reference)."""
    return dxf.build_dxf_bytes(entities, layers)


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


def _reset():
    """Forget discovery + counter state so a check can re-point the binding."""
    native._reset_for_tests()


def _assert_fallback(entities, label, layers=None):
    """A fallback check: output equals the Python reference and the native
    counter is NOT incremented (proving the Python writer ran)."""
    p = os.path.join(TMP, "fb_" + label + ".dxf")
    before = native._native_dxf_calls
    n = dxf.write_dxf(p, entities, layers)
    got = open(p, "rb").read()
    want = _py_reference(entities, layers)
    assert got == want, (label, "fallback output differs from Python",
                         len(got), len(want))
    assert native._native_dxf_calls == before, \
        (label, "native counter must not increment on fallback",
         before, native._native_dxf_calls)
    assert n == len(want), (label, n, len(want))


@check("native library is built, discovered, and ABI-compatible (DXF exports)")
def _():
    assert _LIB is not None, "pure_core .so not present and build failed"
    assert native is not None, "adapter did not resolve the native module"
    assert native.available() is True, native.backend_status()
    assert "ready" in native.backend_status(), native.backend_status()
    # The DXF exports are resolvable through the adapter (not just STL/OBJ).
    binding = native._binding()
    assert binding is not None
    dxf_size, dxf_enc = binding[8], binding[9]
    assert callable(dxf_size), "DXF size export missing"
    assert callable(dxf_enc), "DXF encode export missing"
    assert callable(native.encode_dxf)


@check("direct Rust ABI encode returns real bytes (byte-identity with Python)")
def _():
    b = native.encode_dxf(ENTITIES)
    assert isinstance(b, (bytes, bytearray)), type(b)
    assert b == _py_reference(ENTITIES), "direct native differs from Python"
    # With an explicit layers dict (the LAYER table order and colors must
    # match the Python writer exactly).
    b2 = native.encode_dxf(ENTITIES, {"walls": 1})
    assert b2 == _py_reference(ENTITIES, {"walls": 1}), \
        "direct native differs from Python (with layers dict)"


@check("native encoder writes exact SPLINE chains and group-420 colors byte-identically")
def _():
    spline = [{"type": "SPLINE",
               "control_points": [(0, 0, 0), (1, 2, 0), (2, 2, 0), (3, 0, 0),
                                  (4, -2, 0), (5, -2, 0), (6, 0, 0)],
               "closed": False, "layer": "Cut", "true_color": 0}]
    layers = {"Cut": 0x1000000 + 0xFF0000}
    encoded = native.encode_dxf(spline, layers)
    assert encoded == _py_reference(spline, layers)
    text = encoded.decode("latin-1")
    assert "1\nAC1027\n" in text and "0\nSPLINE\n" in text
    assert "420\n16711680\n" in text and "420\n0\n" in text
    assert "71\n3\n72\n11\n73\n7\n" in text


@check("one-pass dxf_encode_into: byte-identity with the Python writer (direct)")
def _():
    _reset()
    before = native._native_dxf_calls
    result = native._native_dxf_encode_into(ENTITIES)
    assert result is not None, \
        "the one-pass route must succeed for a valid entity list"
    out, written = result
    assert isinstance(out, bytearray), type(out)
    assert isinstance(written, int) and 0 < written <= len(out), \
        (written, len(out))
    assert bytes(out[:written]) == _py_reference(ENTITIES), \
        "one-pass native differs from the Python writer"
    assert native._native_dxf_calls == before + 1, \
        "a one-pass success must increment the route-activation counter"
    # With an explicit layers dict the LAYER table order and colors must match
    # the Python writer exactly (the same invariant the two-pass route pins).
    result2 = native._native_dxf_encode_into(ENTITIES, {"walls": 1})
    assert result2 is not None
    out2, written2 = result2
    assert bytes(out2[:written2]) == _py_reference(ENTITIES, {"walls": 1}), \
        "one-pass native differs from the Python writer (with layers dict)"
    # `written` is the EXACT byte count (the tail of the caller-owned buffer is
    # unspecified and must not leak into the file).
    assert len(out) >= written and len(out2) >= written2


@check("one-pass dxf_encode_into: a NaN coordinate is a domain error (None, counter NOT moved)")
def _():
    _reset()
    before = native._native_dxf_calls
    bad = [{"type": "LINE", "p0": (float("nan"), 0, 0), "p1": (1, 0, 0),
            "layer": "0", "color": 7}]
    got = native._native_dxf_encode_into(bad)
    assert got is None, \
        "a NaN coordinate must be a one-pass domain error (None): %r" % (got,)
    assert native._native_dxf_calls == before, \
        "the counter must not increment on a rejected one-pass input"


@check("production DXF writing stays on Python until the native encoder wins")
def _():
    _reset()
    before = native._native_dxf_calls
    p = os.path.join(TMP, "rt.dxf")
    n = dxf.write_dxf(p, ENTITIES, {"walls": 1})
    got = open(p, "rb").read()
    assert native._native_dxf_calls == before, \
        "the measured-slower two-pass native encoder must not run in production"
    direct = native.encode_dxf(ENTITIES, {"walls": 1})
    assert got == direct == _py_reference(ENTITIES, {"walls": 1})
    assert n == len(got) == len(direct)


@check("POLYLINE closed flag: 70/1 when closed, 70/0 when open (native)")
def _():
    _reset()
    # The closed flag is the `70` value that follows `66/1` inside the
    # POLYLINE block. `66` is emitted only by POLYLINE entities (never by the
    # HEADER or the LAYER table, which also carry `70` values), so anchoring on
    # `66/1/70/` isolates the flag from every other `70` in the file. A bare
    # `"70\n1\n" in text` would be non-discriminating: the LAYER table's
    # one-entry count is `70/1` and each layer entry's flags are `70/0`.
    # Closed polyline.
    closed_ents = [
        {"type": "POLYLINE", "points": [(0, 0, 0), (10, 0, 0), (10, 10, 0)],
         "closed": True, "layer": "0", "color": 7},
    ]
    text = native.encode_dxf(closed_ents).decode("latin-1")
    assert "66\n1\n70\n1\n" in text, \
        "closed POLYLINE must emit 66/1/70/1: %r" % text
    # Open polyline.
    open_ents = [
        {"type": "POLYLINE", "points": [(0, 0, 0), (10, 0, 0), (10, 10, 0)],
         "closed": False, "layer": "0", "color": 7},
    ]
    text = native.encode_dxf(open_ents).decode("latin-1")
    assert "66\n1\n70\n0\n" in text, \
        "open POLYLINE must emit 66/1/70/0: %r" % text
    # And the byte-identity holds for both.
    assert native.encode_dxf(closed_ents) == _py_reference(closed_ents)
    assert native.encode_dxf(open_ents) == _py_reference(open_ents)


@check("malformed SPLINE is a domain error (None, counter NOT moved)")
def _():
    _reset()
    before = native._native_dxf_calls
    bad = [{"type": "SPLINE", "control_points": [(0, 0, 0)] * 5, "layer": "0"}]
    got = native.encode_dxf(bad)
    assert got is None, \
        "a non-3n+1 SPLINE must be a domain error, not bytes: %r" % got
    assert native._native_dxf_calls == before, \
        "counter must not increment on a rejected input"


@check("fallback: missing library uses the unchanged Python writer")
def _():
    _reset()
    os.environ["NARWHAL_PURE_CORE"] = os.path.join(TMP, "no_such_lib.so")
    assert native.available() is False, \
        "a missing library must be unavailable, got %r" % native.backend_status()
    assert native.encode_dxf(ENTITIES) is None
    _assert_fallback(ENTITIES, "missing-bin")
    if _LIB is not None:
        os.environ["NARWHAL_PURE_CORE"] = _LIB


@check("fallback: a library without the DXF exports uses Python")
def _():
    # Model a library that has the STL+OBJ exports but predates the DXF
    # exports: the DXF slots in the binding are None, so encode_dxf returns
    # None and the Python writer must run.
    _reset()
    assert _LIB is not None
    assert native.available() is True, native.backend_status()
    real_binding = native._binding

    def _binding_no_dxf():
        b = real_binding()
        if b is None:
            return None
        # The binding is the 13-tuple; slots 8-11 are the DXF size/encode/
        # decode-size/decode. Null all four; keep the path (12).
        return (b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7],
                None, None, None, None, b[12])

    native._binding = _binding_no_dxf
    try:
        assert native.encode_dxf(ENTITIES) is None, \
            "encode_dxf must return None when the DXF exports are absent"
        _assert_fallback(ENTITIES, "no-dxf-exports")
    finally:
        native._binding = real_binding


@check("fallback: a native domain error (NaN coordinate) uses the Python writer")
def _():
    _reset()
    assert native.available() is True, native.backend_status()
    # A LINE with a NaN coordinate is a native domain error.
    bad = [{"type": "LINE", "p0": (float("nan"), 0, 0), "p1": (1, 0, 0),
            "layer": "0", "color": 7}]
    assert native.encode_dxf(bad) is None, \
        "a NaN coordinate must be a native domain error (None)"
    before = native._native_dxf_calls
    # write_dxf must fall back to the Python writer (which emits 'nan').
    _assert_fallback(bad, "nan-coord")


@check("fallback: a malformed native output uses the Python writer")
def _():
    _reset()
    assert native.available() is True, native.backend_status()
    real = native.encode_dxf
    native.encode_dxf = lambda *a, **k: None  # simulate a failed encode
    try:
        _assert_fallback(ENTITIES, "malformed")
    finally:
        native.encode_dxf = real


@check("production DXF writing consumes one-shot generators exactly once")
def _():
    _reset()
    expected = [{"type": "POLYLINE", "points": [(0, 0, 0), (1, 0, 0), (1, 1, 0)],
                 "closed": True, "layer": "cut", "color": 1}]
    seen = {"entities": 0, "points": 0}

    def points():
        for point in expected[0]["points"]:
            seen["points"] += 1
            yield point

    def entities():
        seen["entities"] += 1
        yield {**expected[0], "points": points()}

    p = os.path.join(TMP, "generator-production.dxf")
    before = native._native_dxf_calls
    dxf.write_dxf(p, entities())
    assert open(p, "rb").read() == _py_reference(expected)
    assert seen == {"entities": 1, "points": 3}, seen
    assert native._native_dxf_calls == before


@check("Latin-1 DXF layer names decline native and preserve exact bytes")
def _():
    _reset()
    for layer, expected_bytes in (("café", b"caf\xe9"), ("ÿ", b"\xff")):
        entities = [{"type": "LINE", "p0": (0, 0, 0), "p1": (1, 0, 0),
                     "layer": layer, "color": 7}]
        before = native._native_dxf_calls
        assert native.encode_dxf(entities) is None
        p = os.path.join(TMP, "latin-%d.dxf" % expected_bytes[0])
        dxf.write_dxf(p, entities)
        got = open(p, "rb").read()
        assert got == _py_reference(entities)
        assert expected_bytes in got
        assert native._native_dxf_calls == before


# ---------------------------------------------------------------------------
# Q106-07: the native DECODE route (the read side of the round trip).
#
# These prove the native decoder is the route actually running (the
# ``_native_decode_dxf_calls`` counter must move) and that its dict is
# struct-identical to the unchanged Python reader for the same file. The
# writer never emits LWPOLYLINE / bulges / skipped types, so those paths are
# driven by hand-written DXF text (the same fixtures ``test_dxf_reader.py``
# pins on the Python route).
# ---------------------------------------------------------------------------


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
        fh.write(_py_reference(ENTITIES, {"walls": 1}))
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
        fh.write(_py_reference(ENTITIES, {"walls": 1}))
    real_binding = native._binding

    def _binding_no_decode_dxf():
        b = real_binding()
        if b is None:
            return None
        # The binding is the 13-tuple; null the DXF decode slots (10, 11),
        # keep the encode (8, 9) and the path (12).
        return (b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7],
                b[8], b[9], None, None, b[12])

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
        fh.write(_py_reference(ENTITIES, {"walls": 1}))
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

    native._binding = lambda: (*base[:10], size_fn, decode_fn, base[12])
    try:
        before = native._native_decode_dxf_calls
        assert native.decode_dxf(_py_reference(ENTITIES)) is None
        assert native._native_decode_dxf_calls == before
    finally:
        native._binding = real_binding


@check("one-pass capacity counts every repeated long layer name")
def _():
    _reset()
    layer = "L" * 4096
    entities = [{
        "type": "POLYLINE", "layer": layer, "color": 7,
        "points": [(float(i), 0.0, 0.0) for i in range(200)],
        "closed": True,
    } for _ in range(8)]
    got = native._native_dxf_encode_into(entities, {layer: 7})
    assert got is not None, "long repeated names must not under-allocate"
    out, written = got
    assert bytes(memoryview(out)[:written]) == _py_reference(entities, {layer: 7})


# ---------------------------------------------------------------------------
failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
