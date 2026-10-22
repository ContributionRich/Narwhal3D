"""Native one-pass PDF vector serializer (Q117-07): direct ABI proof, route
activation posture, byte-identity with the Python writer, and every fallback
scenario.

Complements ``spike/test_vector_export.py`` (which pins the Python writer's
file-format invariants) by proving the *native* route:

* the Rust ``narwhal_pure_core_pdf_encode_into`` export is resolvable through
  the adapter;
* a direct native encode is byte-identical to the unchanged
  ``vector_export._build_pdf`` (no-layer, layered, empty, empty-stroke);
* the production crossover is **disabled** -- ``_PDF_NATIVE_MIN_STROKES`` is
  above any real document, so ``write_pdf`` stays on the Python writer
  (Q117-07 measured finding: native is slower at every size; the policy says
  "leave production Python active if no winning workload is demonstrated");
* every fallback scenario (missing library, empty library, malformed native
  output) runs the *unchanged Python writer* with correct output and with the
  ``_native_pdf_calls`` counter NOT incremented;
* the ``write_pdf`` hook degrades cleanly to Python when the native route is
  disabled (which is the production posture).

The suite is green with or without a prebuilt ``.so``: if the library is not
present it builds the crate once (offline, pinned toolchain) and rechecks.
"""
import importlib.util
import os
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
    import subprocess
    subprocess.run(
        ["cargo", "+1.95.0", "build", "--release", "--locked", "--offline",
         "--manifest-path", manifest, "--target", "x86_64-unknown-linux-gnu"],
        cwd=ROOT, check=True,
    )
    return _locate_lib()


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


ve = _load("narwhal_vector_export", "vector_export.py")
native = ve._native_pdf_module()

results = []
TMP = tempfile.mkdtemp(prefix="native_pdf_")


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


def _reset():
    native._reset_for_tests()


def _strokes_simple():
    return [
        ve.Stroke(beziers=(((0, 0), (3.5, 1.5), (6.5, 1.5), (10, 0)),
                           ((10, 0), (11, 2), (9, 4), (10, 6))),
                  closed=False, rgb=(0.1, 0.2, 0.3), width_mm=0.25, layer=""),
    ]


def _strokes_layered():
    return [
        ve.Stroke(beziers=(((0, 0), (2, 2), (4, 2), (6, 0)),),
                  closed=True, rgb=(1, 0, 0), width_mm=0.5, layer="cut"),
        ve.Stroke(beziers=(((1, 1), (2, 2), (3, 3), (4, 4)),),
                  closed=False, rgb=(0, 1, 0), width_mm=0.3, layer="drill"),
        ve.Stroke(beziers=(((5, 5), (6, 6), (7, 7), (8, 8)),),
                  closed=False, rgb=(0, 0, 1), width_mm=0.7, layer="cut"),
    ]


def _strokes_empty():
    return []


def _strokes_empty_stroke():
    # A stroke with no beziers -- Python emits [RG, w] with NO `S`.
    return [ve.Stroke(beziers=(), closed=False, rgb=(1, 0, 0),
                      width_mm=0.5, layer="")]


def _py_reference(strokes, page_w, page_h, title):
    return ve._build_pdf(strokes, page_w, page_h, title)


def _assert_fallback(strokes, label, page_w=210, page_h=297, title="T"):
    p = os.path.join(TMP, "fb_" + label + ".pdf")
    before = native._native_pdf_calls
    n = ve.write_pdf(p, strokes, page_w, page_h, title)
    got = open(p, "rb").read()
    want = _py_reference(strokes, page_w, page_h, title)
    assert got == want, (label, "fallback output differs from Python",
                         len(got), len(want))
    assert native._native_pdf_calls == before, \
        (label, "native counter must not increment on fallback",
         before, native._native_pdf_calls)
    assert n == len(want), (label, n, len(want))


@check("native library is built, discovered, and the PDF export resolves")
def _():
    assert _LIB is not None, "pure_core .so not present and build failed"
    assert native is not None, "adapter did not resolve the native module"
    assert native.available() is True, native.backend_status()
    assert "ready" in native.backend_status(), native.backend_status()
    assert native._pdf_encode_into_binding() is not None, \
        "one-pass PDF export must be resolvable"
    assert callable(native.encode_pdf)


@check("direct native encode is byte-identical to the Python writer (all shapes)")
def _():
    for label, strokes, title in (
        ("no-layer", _strokes_simple(), "A (B) \\ title"),
        ("layered", _strokes_layered(), "T"),
        ("empty", _strokes_empty(), ""),
        ("empty-stroke", _strokes_empty_stroke(), "E"),
    ):
        _reset()
        native_bytes = native.encode_pdf(strokes, 210, 297, title)
        ref = _py_reference(strokes, 210, 297, title)
        assert native_bytes is not None, (label, "native returned None")
        assert native_bytes == ref, (label, "byte identity broken",
                                    len(native_bytes), len(ref))


@check("native PDF validator rejects malformed successful output")
def _():
    assert native._valid_pdf_bytes(b"not a pdf") is False
    good = native.encode_pdf(_strokes_simple(), 210, 297, "T")
    assert good is not None and native._valid_pdf_bytes(good)
    bad = good.replace(b"0000000015 00000 n", b"0000000016 00000 n", 1)
    assert native._valid_pdf_bytes(bad) is False


@check("production crossover is DISABLED (native slower at every size)")
def _():
    # The Q117-07 measured finding: native is slower than Python at every
    # tested size (best of 6; 50k strokes 308ms native vs 264ms Python).
    # Policy: "leave production Python active if no winning workload is
    # demonstrated." So the threshold is above any real document, and
    # _pdf_native_active must be False for every real input.
    assert native._PDF_NATIVE_MIN_STROKES >= 1_000_000, \
        ("production crossover must be disabled "
         "(>= 1,000,000 strokes)", native._PDF_NATIVE_MIN_STROKES)
    assert native._pdf_native_active(_strokes_simple()) is False
    assert native._pdf_native_active(_strokes_layered()) is False
    assert native._pdf_native_active(_strokes_empty()) is False


@check("production write_pdf stays on the Python writer (counter unmoved)")
def _():
    _reset()
    before = native._native_pdf_calls
    p = os.path.join(TMP, "prod.pdf")
    n = ve.write_pdf(p, _strokes_simple(), 210, 297, "T")
    got = open(p, "rb").read()
    want = _py_reference(_strokes_simple(), 210, 297, "T")
    assert got == want, "production output must equal the Python writer"
    assert n == len(got)
    assert native._native_pdf_calls == before, \
        "production write_pdf must not use the (disabled) native route"


@check("fallback: missing library uses the unchanged Python writer")
def _():
    _reset()
    os.environ["NARWHAL_PURE_CORE"] = os.path.join(TMP, "no_such_lib.so")
    assert native.available() is False, \
        "a missing library must be unavailable, got %r" % native.backend_status()
    assert native.encode_pdf(_strokes_simple(), 210, 297, "T") is None
    _assert_fallback(_strokes_simple(), "missing-bin")
    if _LIB is not None:
        os.environ["NARWHAL_PURE_CORE"] = _LIB


@check("fallback: a library without the PDF export uses Python")
def _():
    _reset()
    assert _LIB is not None
    empty = os.path.join(TMP, "empty.so")
    open(empty, "wb").close()
    os.environ["NARWHAL_PURE_CORE"] = empty
    assert native.available() is False, \
        "a library without the PDF export must be unavailable"
    _assert_fallback(_strokes_simple(), "oldsym")
    if _LIB is not None:
        os.environ["NARWHAL_PURE_CORE"] = _LIB


@check("fallback: a malformed native output uses the Python writer")
def _():
    _reset()
    assert native.available() is True, native.backend_status()
    real = native.encode_pdf
    native.encode_pdf = lambda *a, **k: None  # simulate a failed encode
    try:
        _assert_fallback(_strokes_simple(), "malformed")
    finally:
        native.encode_pdf = real


@check("native SVG export resolves and is byte-identical on all shapes")
def _():
    _reset()
    assert native._svg_encode_into_binding() is not None
    escaped = [ve.Stroke(
        beziers=(((0, 0), (1, 2), (3, 4), (5, 6)),),
        closed=True, rgb=(0.5, 0.0, 1.0), width_mm=0.25,
        layer='cut & <etch> "Ω"')]
    for label, strokes in (
        ("plain", _strokes_simple()),
        ("layered", _strokes_layered()),
        ("empty", []),
        ("escaped", escaped),
    ):
        p = os.path.join(TMP, "svg_ref_" + label + ".svg")
        ve.write_svg(p, strokes, 210, 297)
        want = open(p, "rb").read()
        before = native._native_svg_vector_calls
        got = native.encode_svg(strokes, 210, 297)
        assert got == want, (label, len(got or b""), len(want))
        assert native._native_svg_vector_calls == before + 1
    assert b'cut &amp; &lt;etch&gt; &quot;\xce\xa9&quot;' in got


@check("production SVG uses the measured crossover without slowing small files")
def _():
    _reset()
    assert native._SVG_VECTOR_NATIVE_MIN_STROKES == 1000
    before = native._native_svg_vector_calls
    small = os.path.join(TMP, "svg_small.svg")
    n = ve.write_svg(small, _strokes_simple(), 210, 297)
    assert n == os.path.getsize(small)
    assert native._native_svg_vector_calls == before

    strokes = [ve.Stroke(
        beziers=(((float(i), 0), (1, 2), (3, 4), (5, 6)),),
        rgb=(0.1, 0.5, 0.9), width_mm=0.25, layer="cut")
        for i in range(1000)]
    prod = os.path.join(TMP, "svg_prod.svg")
    n = ve.write_svg(prod, strokes, 210, 297)
    assert n == os.path.getsize(prod)
    assert native._native_svg_vector_calls == before + 1


if __name__ == "__main__":
    for name, status, err in results:
        print("[%s] %s%s" % (status, name, (" -- " + err) if err else ""))
    fails = [r for r in results if r[1] == "FAIL"]
    print("\n%d/%d passed" % (len(results) - len(fails), len(results)))
    sys.exit(1 if fails else 0)
