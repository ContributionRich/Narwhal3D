"""Native STL encoder: direct ABI proof + route activation + fallbacks.

Complements ``spike/test_stl_writer.py`` (which pins the file-format invariants
on the Python path) by proving the *native* route is the one actually running:

* the Rust ABI is executed directly (not through the Python fallback), and
  ``available()`` is true;
* ``write_stl`` uses the native route -- the ``_native_calls`` counter must
  move, and the file must equal a direct native encode (a mutation that
  silently forces the Python path leaves the counter at 0 -> RED);
* every unit-cube facet normal points OUTWARD (mutating the cross product to
  reverse it must make this RED);
* the four fallback scenarios (missing library, missing old symbol, native
  error, malformed output) all run the *unchanged Python writer* with correct
  output and with the counter NOT incremented.

The suite is green with or without a prebuilt ``.so``: if the library is not
present it builds the crate once (offline, pinned toolchain) and rechecks.
"""
import ctypes
import importlib.util
import os
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = os.path.join(ROOT, "tools", "blender-patched", "5.2", "python",
                      "bin", "python3.13")
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


stl = _load("narwhal_stl_writer", "stl_writer.py")
# Resolve the SAME native module the writer uses, so we can inspect its
# counter and monkeypatch it in the malformed-output test.
native = stl._native_stl()

results = []
TMP = tempfile.mkdtemp(prefix="native_stl_")
# The proven all-outward unit cube, identical to ``spike/test_stl_writer.py``
# (12 triangles, each wound CCW seen from outside). Reusing it keeps the
# outward-normal mutation target on a known-good fixture.
CUBE = [

    ((0, 0, 1), (1, 0, 1), (1, 1, 1)), ((0, 0, 1), (1, 1, 1), (0, 1, 1)),   # +z

    ((1, 0, 0), (0, 0, 0), (0, 1, 0)), ((1, 1, 0), (1, 0, 0), (0, 1, 0)),   # -z

    ((1, 0, 0), (1, 1, 0), (1, 1, 1)), ((1, 1, 1), (1, 0, 1), (1, 0, 0)),   # +x

    ((0, 1, 0), (0, 0, 0), (0, 0, 1)), ((0, 1, 1), (0, 1, 0), (0, 0, 1)),   # -x

    ((1, 1, 0), (0, 1, 0), (0, 1, 1)), ((0, 1, 1), (1, 1, 1), (1, 1, 0)),   # +y

    ((0, 0, 0), (1, 0, 0), (1, 0, 1)), ((1, 0, 1), (0, 0, 1), (0, 0, 0)),   # -y

]


def _py_reference(tris, name, binary):
    """The unchanged Python writer's output for the same input (a reference)."""
    tris = list(tris)
    if binary:
        if str(name).strip().lower().startswith("solid"):
            name = "Narwhal 3D"
        hdr = (str(name)[:80] + " " * 80).encode("latin-1")[:80]
        parts = [hdr, struct.pack("<I", len(tris))]
        for a, b, c in tris:
            nx, ny, nz = stl._facet_normal(a, b, c)
            parts.append(struct.pack("<12fH", nx, ny, nz,
                                     a[0], a[1], a[2], b[0], b[1], b[2],
                                     c[0], c[1], c[2], 0))
        return b"".join(parts)
    out = ["solid %s" % (name or "Narwhal 3D")]
    for a, b, c in tris:
        nx, ny, nz = stl._facet_normal(a, b, c)
        out.append("  facet normal %s %s %s" % (stl._f(nx), stl._f(ny), stl._f(nz)))
        out.append("  outer loop")
        for p in (a, b, c):
            out.append("    vertex %s %s %s" % (stl._f(p[0]), stl._f(p[1]), stl._f(p[2])))
        out.append("  endloop")
        out.append("  endfacet")
    out.append("endsolid %s" % (name or "Narwhal 3D"))
    return ("\n".join(out) + "\n").encode("latin-1")


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


def _assert_fallback(tris, name, binary, label):
    """A fallback check: output equals the Python reference and the native
    counter is NOT incremented (proving the Python writer ran)."""
    p = os.path.join(TMP, "fb_" + label + (".bin.stl" if binary else ".stl"))
    before = native._native_calls
    n = stl.write_stl(p, tris, binary=binary, name=name)
    got = open(p, "rb").read()
    want = _py_reference(tris, name, binary)
    assert got == want, (label, "fallback output differs from Python",
                         len(got), len(want))
    assert native._native_calls == before, \
        (label, "native counter must not increment on fallback",
         before, native._native_calls)
    assert n == len(want), (label, n, len(want))


@check("native library is built, discovered, and ABI-compatible")
def _():
    assert _LIB is not None, "pure_core .so not present and build failed"
    assert native is not None, "adapter did not resolve the native module"
    assert native.available() is True, native.backend_status()
    assert "ready" in native.backend_status(), native.backend_status()
    # The two ABI exports are resolvable through the adapter.
    assert callable(native.encode_stl)


@check("direct Rust ABI encode of the cube returns real bytes (not fallback)")
def _():
    b = native.encode_stl(CUBE, "Narwhal 3D", True)
    assert isinstance(b, (bytes, bytearray)), type(b)
    assert len(b) == 84 + 12 * 50, len(b)
    assert struct.unpack_from("<I", b, 80)[0] == 12
    # And a direct call that must fail (NaN) returns None, not a fallback.
    _reset()
    before = native._native_calls
    bad = native.encode_stl([((0, 0, 0), (1, 0, 0), (float("nan"), 1, 0))],
                            "Narwhal 3D", True)
    assert bad is None, "NaN input must be a domain error, not a fallback"
    assert native._native_calls == before, \
        "counter must not increment on a rejected input"


@check("write_stl activates the native route (counter moves, bytes match)")
def _():
    _reset()
    before = native._native_calls
    p = os.path.join(TMP, "rt.bin.stl")
    n = stl.write_stl(p, CUBE, binary=True)
    got = open(p, "rb").read()
    # The route-activation tripwire: the counter MUST move.
    assert native._native_calls > before, \
        ("route did not use native (counter %d -> %d)" %
         (before, native._native_calls))
    # And the file must equal a direct native encode (not the Python path).
    direct = native.encode_stl(CUBE, "Narwhal 3D", True)
    assert got == direct, "write_stl bytes differ from a direct native encode"
    assert n == len(got) == len(direct)


@check("every cube facet normal points OUTWARD (cross product orientation)")
def _():
    _reset()
    b = native.encode_stl(CUBE, "Narwhal 3D", True)
    count = struct.unpack_from("<I", b, 80)[0]
    assert count == 12, count
    cx = cy = cz = 0.5
    off = 84
    for i in range(count):
        nx, ny, nz = struct.unpack_from("<3f", b, off)
        v = struct.unpack_from("<9f", b, off + 12)
        off += 50
        fx = (v[0] + v[3] + v[6]) / 3.0
        fy = (v[1] + v[4] + v[7]) / 3.0
        fz = (v[2] + v[5] + v[8]) / 3.0
        dot = (fx - cx) * nx + (fy - cy) * ny + (fz - cz) * nz
        assert dot > 0.0, (i, (nx, ny, nz), (fx, fy, fz), dot)


@check("fallback: missing library uses the unchanged Python writer")
def _():
    _reset()
    os.environ["NARWHAL_PURE_CORE"] = os.path.join(TMP, "no_such_lib.so")
    assert native.available() is False, \
        "a missing library must be unavailable, got %r" % native.backend_status()
    _assert_fallback(CUBE, "Narwhal 3D", True, "missing-bin")
    _assert_fallback(CUBE, "Narwhal 3D", False, "missing-asc")
    if _LIB is not None:
        os.environ["NARWHAL_PURE_CORE"] = _LIB


@check("fallback: an old library without the STL exports uses Python")
def _():
    # The selection-core library has a different ABI (its own exports) and
    # lacks narwhal_pure_core_stl_size/_encode, so it models "old symbol".
    old = os.path.join(ROOT, "artifacts", "selection-core", "linux-x86_64",
                       "libnarwhal_selection_core.so")
    if not os.path.isfile(old):
        # No selection-core artifact in this tree: simulate a missing symbol by
        # pointing at a real .so that is not the pure-core one.
        assert _LIB is not None
        old = os.path.join(TMP, "empty.so")
        open(old, "wb").close()
    _reset()
    os.environ["NARWHAL_PURE_CORE"] = old
    assert native.available() is False, \
        "a library without the STL exports must be unavailable"
    _assert_fallback(CUBE, "Narwhal 3D", True, "oldsym-bin")
    if _LIB is not None:
        os.environ["NARWHAL_PURE_CORE"] = _LIB


@check("fallback: a native input error (NaN) uses the Python writer")
def _():
    _reset()
    # Ensure the native path is active so this is a genuine native->fallback.
    assert native.available() is True, native.backend_status()
    tris = [((0, 0, 0), (1, 0, 0), (float("nan"), 1.0, 0.0))]
    _assert_fallback(tris, "Narwhal 3D", True, "nativeerr")
    # encode_stl itself must report the domain error as None (not a fallback).
    assert native.encode_stl(tris, "Narwhal 3D", True) is None


@check("fallback: a malformed native output uses the Python writer")
def _():
    _reset()
    assert native.available() is True, native.backend_status()
    real = native.encode_stl
    native.encode_stl = lambda *a, **k: None  # simulate a malformed/failed encode
    try:
        _assert_fallback(CUBE, "Narwhal 3D", True, "malformed")
    finally:
        native.encode_stl = real


@check("ABI-1 STL encode-only library keeps encode while decode falls back")
def _():
    _reset()
    assert _LIB is not None
    real_cdll = native.ctypes.CDLL
    real_env = os.environ.get("NARWHAL_PURE_CORE")
    loaded = real_cdll(_LIB)

    class EncodeOnly:
        narwhal_pure_core_abi_version = loaded.narwhal_pure_core_abi_version
        narwhal_pure_core_stl_size = loaded.narwhal_pure_core_stl_size
        narwhal_pure_core_stl_encode = loaded.narwhal_pure_core_stl_encode

    native.ctypes.CDLL = lambda _path: EncodeOnly()
    os.environ["NARWHAL_PURE_CORE"] = _LIB
    try:
        binding = native._binding()
        assert binding is not None
        assert callable(binding[0]) and callable(binding[1])
        assert binding[2] is None and binding[3] is None
        assert native.encode_stl(CUBE, "Narwhal 3D", True) is not None
        before = native._native_decode_calls
        assert native.decode_stl(_py_reference(CUBE, "Narwhal 3D", True)) is None
        assert native._native_decode_calls == before
    finally:
        native.ctypes.CDLL = real_cdll
        if real_env is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = real_env
        _reset()


@check("one-shot STL generator survives a consuming native failure")
def _():
    _reset()
    seen = []

    def triangles():
        for i, triangle in enumerate(CUBE):
            seen.append(i)
            yield triangle

    real = native.encode_stl

    def consume_then_fail(values, *_args):
        assert list(values) == CUBE
        return None

    native.encode_stl = consume_then_fail
    try:
        p = os.path.join(TMP, "generator-fallback.stl")
        stl.write_stl(p, triangles(), binary=True)
        assert open(p, "rb").read() == _py_reference(CUBE, "Narwhal 3D", True)
        assert seen == list(range(len(CUBE))), seen
    finally:
        native.encode_stl = real


@check("untrusted STL output size is bounded before allocation")
def _():
    _reset()
    real_binding = native._binding
    base = real_binding()
    called = []

    def huge_size(*_args):
        return native._MAX_NATIVE_OUTPUT_BYTES + 1

    def must_not_encode(*_args):
        called.append(True)
        return 0

    native._binding = lambda: (huge_size, must_not_encode, *base[2:])
    try:
        before = native._native_calls
        assert native.encode_stl(CUBE, "Narwhal 3D", True) is None
        assert called == []
        assert native._native_calls == before
    finally:
        native._binding = real_binding


# ---------------------------------------------------------------------------
# Native STL DECODING (Q106-03): the inverse of the encode above, proving the
# native decode route actually runs and matches the Python reader.
# ---------------------------------------------------------------------------

def _tris_match(got, want):
    if len(got) != len(want):
        return False
    for g, w in zip(got, want):
        if len(g) != 3 or len(w) != 3:
            return False
        for gp, wp in zip(g, w):
            for a, b in zip(gp, wp):
                if abs(a - b) > 1e-6 * (1.0 + abs(b)):
                    return False
    return True


@check("fused STL mesh decode welds to first-seen indexed vertices")
def _():
    _reset()
    p = os.path.join(TMP, "mesh.bin.stl")
    stl.write_stl(p, CUBE, binary=True)
    data = open(p, "rb").read()
    before = native._native_stl_mesh_calls
    packed = native.decode_stl_mesh(data)
    assert packed is not None
    vertices, indices, nv, nf = packed
    assert nv == 8 and nf == 12, (nv, nf)
    assert tuple(vertices[:3]) == tuple(CUBE[0][0])
    assert tuple(indices[:3]) == (0, 1, 2)
    assert native._native_stl_mesh_calls == before + 1


@check("binary STL production read stays Python while direct Rust decode remains exact")
def _():
    _reset()
    assert native.available() is True, native.backend_status()
    p = os.path.join(TMP, "rt.bin.stl")
    stl.write_stl(p, CUBE, binary=True)
    before = native._native_decode_calls
    got = stl.read_stl(p)
    assert native._native_decode_calls == before
    assert _tris_match(got, CUBE), got[:2]
    data = open(p, "rb").read()
    direct = native.decode_stl(data)
    assert native._native_decode_calls == before + 1
    assert _tris_match(direct, CUBE)


@check("direct native decode round-trips an ASCII cube")
def _():
    _reset()
    assert native.available() is True, native.backend_status()
    p = os.path.join(TMP, "rt.ascii.stl")
    stl.write_stl(p, CUBE, binary=False)
    data = open(p, "rb").read()
    before = native._native_decode_calls
    got = native.decode_stl(data)
    assert native._native_decode_calls == before + 1
    assert _tris_match(got, CUBE), got[:2]


@check("a binary STL whose header begins `solid` is still read as binary")
def _():
    _reset()
    assert native.available() is True, native.backend_status()
    # A one-facet binary STL with an 80-byte header starting `solid`.
    hdr = b"solid" + b" " * 75
    facet = struct.pack("<12fH", 0, 0, 0,
                        0, 0, 0, 1, 0, 0, 0, 1, 0, 0)
    data = hdr + struct.pack("<I", 1) + facet
    got = native.decode_stl(data)
    assert got is not None, "a binary file must not be misread as ASCII"
    assert len(got) == 1, got
    assert _tris_match(got, [((0, 0, 0), (1, 0, 0), (0, 1, 0))])


@check("a truncated binary STL falls back to the Python reader (which rejects)")
def _():
    _reset()
    assert native.available() is True, native.backend_status()
    # The native decode_size returns a negative code for a truncated header, so
    # decode_stl returns None and read_stl falls back to the Python reader,
    # which must reject the truncated file rather than silently read less.
    p = os.path.join(TMP, "full.bin.stl")
    stl.write_stl(p, CUBE, binary=True)
    with open(p, "rb") as fh:
        full = fh.read()
    short = os.path.join(TMP, "short.bin.stl")
    with open(short, "wb") as fh:
        fh.write(full[:len(full) - 50])
    # Force the native decode to see the file, then fall back:
    assert native.decode_stl(full[:len(full) - 50]) is None, \
        "a truncated binary file must be rejected by the native decoder"
    try:
        stl.read_stl(short)
        raise AssertionError("a truncated binary file must be rejected")
    except ValueError:
        pass


# ---------------------------------------------------------------------------
failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
