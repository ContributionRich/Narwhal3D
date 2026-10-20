"""Native OBJ encoder: direct ABI proof + route activation + fallbacks.

Complements ``spike/test_obj_writer.py`` (which pins the file-format
invariants on the Python path) by proving the *native* route is the one
actually running:

* the Rust ABI is executed directly (not through the Python fallback), and
  ``available()`` is true;
* ``write_obj`` uses the native route -- the ``_native_obj_calls`` counter
  must move, and the file must equal a direct native encode AND equal the
  unchanged Python writer (byte-identity);
* the cumulative-index invariant holds natively (the second mesh's face is
  written ``f 5 6 7 8``, never ``f 1 2 3 4``);
* every fallback scenario (missing library, old library without the OBJ
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


obj = _load("narwhal_obj_writer", "obj_writer.py")
# Resolve the SAME native module the writer uses, so we can inspect its
# counter and monkeypatch it in the malformed-output test.
native = obj._native_obj()
rdr = _load("narwhal_obj_reader", "obj_reader.py")

results = []
TMP = tempfile.mkdtemp(prefix="native_obj_")


def _two_meshes():
    """Mesh A: a square (4 verts, two tris). Mesh B: a square (4 verts, one
    quad). B's faces use 0-based indices into ITS OWN vertex list. Identical
    to ``spike/test_obj_writer.py::_two_meshes``."""
    a = ("floor", [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)],
         [(0, 1, 2), (0, 2, 3)])
    b = ("roof", [(0, 0, 5), (10, 0, 5), (10, 10, 5), (0, 10, 5)],
         [(0, 1, 2, 3)])
    return [a, b]


_BIG_SEED = 117


def _big_two_meshes():
    """A deterministic two-mesh input large enough to clear the one-pass
    crossover (>= ``_OBJ_NATIVE_MIN_VERTS`` total vertices), so production
    ``write_obj`` runs the native route. Mesh A: 30k verts / 10k tris; mesh B:
    30k verts / 10k tris (60k total, 20k tris -- the task's named workload).
    B's faces use 0-based indices into ITS OWN vertex list (the cumulative-
    offset invariant: B's first face is global ``30000+1 ..``)."""
    import random
    rng = random.Random(_BIG_SEED)
    nv, nf = 30_000, 10_000
    a_verts = [(rng.random() * 100 - 50, rng.random() * 100 - 50,
                rng.random() * 100 - 50) for _ in range(nv)]
    a_faces = [(rng.randrange(nv), rng.randrange(nv), rng.randrange(nv))
               for _ in range(nf)]
    b_verts = [(rng.random() * 100 - 50, rng.random() * 100 - 50,
                rng.random() * 100 - 50) for _ in range(nv)]
    b_faces = [(rng.randrange(nv), rng.randrange(nv), rng.randrange(nv))
               for _ in range(nf)]
    return [("floor", a_verts, a_faces), ("roof", b_verts, b_faces)]


def _py_reference(meshes):
    """The unchanged Python writer's output for the same input (a reference).

    Mirrors ``obj_writer.write_obj`` exactly (the byte identity is the
    whole point of the native accelerator).
    """
    out = ["# Narwhal 3D"]
    offset = 0
    for name, vertices, faces in meshes:
        for face in faces:
            if len(face) < 3:
                raise ValueError("face %r has fewer than 3 vertices" % (face,))
            for idx in face:
                if idx < 0 or idx >= len(vertices):
                    raise ValueError("face index %d out of range" % idx)
        out.append("o %s" % (name,))
        for x, y, z in vertices:
            out.append("v %s %s %s" % ("%.9g" % x, "%.9g" % y, "%.9g" % z))
        for face in faces:
            out.append("f " + " ".join(str(i + 1 + offset) for i in face))
        offset += len(vertices)
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


def _assert_fallback(meshes, label):
    """A fallback check: output equals the Python reference and the native
    counter is NOT incremented (proving the Python writer ran)."""
    p = os.path.join(TMP, "fb_" + label + ".obj")
    before = native._native_obj_calls
    n = obj.write_obj(p, meshes)
    got = open(p, "rb").read()
    want = _py_reference(meshes)
    assert got == want, (label, "fallback output differs from Python",
                         len(got), len(want))
    assert native._native_obj_calls == before, \
        (label, "native counter must not increment on fallback",
         before, native._native_obj_calls)
    assert n == len(want), (label, n, len(want))


@check("native library is built, discovered, and ABI-compatible (OBJ exports)")
def _():
    assert _LIB is not None, "pure_core .so not present and build failed"
    assert native is not None, "adapter did not resolve the native module"
    assert native.available() is True, native.backend_status()
    assert "ready" in native.backend_status(), native.backend_status()
    # The OBJ exports are resolvable through the adapter (not just STL).
    binding = native._binding()
    assert binding is not None
    obj_size, obj_enc = binding[4], binding[5]
    assert callable(obj_size), "OBJ size export missing"
    assert callable(obj_enc), "OBJ encode export missing"
    assert callable(native.encode_obj)


@check("direct Rust ABI encode of two meshes returns real bytes (not fallback)")
def _():
    b = native.encode_obj(_two_meshes())
    assert isinstance(b, (bytes, bytearray)), type(b)
    assert b == _py_reference(_two_meshes()), "direct native differs from Python"
    # And a direct call that must fail (a 2-index face) returns None, not bytes.
    _reset()
    before = native._native_obj_calls
    bad = native.encode_obj([("m", [(0, 0, 0), (1, 0, 0)], [(0, 1)])])
    assert bad is None, "a 2-index face must be a domain error, not a fallback"
    assert native._native_obj_calls == before, \
        "counter must not increment on a rejected input"


@check("production OBJ writing uses the one-pass native encoder (counter moves)")
def _():
    _reset()
    assert native._obj_encode_into_binding() is not None, \
        "one-pass OBJ export must be resolvable"
    big = _big_two_meshes()
    assert native._obj_native_active(big) is True, \
        "a >=1024-vertex input must clear the crossover"
    before = native._native_obj_calls
    p = os.path.join(TMP, "rt.obj")
    n = obj.write_obj(p, big)
    got = open(p, "rb").read()
    assert native._native_obj_calls == before + 1, \
        "production write must run the one-pass native route"
    assert got == _py_reference(big), \
        "production output must equal the Python reference"
    assert n == len(got)


@check("one-pass native encode_into is byte-identical to the Python writer")
def _():
    _reset()
    for meshes in (_two_meshes(),
                   [("one", [(0.5, -1.25, 3.0), (7, 8, 9)], [(0, 1, 0)])],
                   [("empty-verts", [], []),
                    ("tri", [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)]),
                    ("quad", [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
                     [(0, 1, 2, 3)])]):
        result = native._native_obj_encode_into(meshes)
        assert result is not None, "one-pass encode must succeed for %r" % meshes
        out, written = result
        assert isinstance(out, bytearray) and 0 < written <= len(out), (out, written)
        assert bytes(out[:written]) == _py_reference(meshes), \
            "one-pass output differs from the Python writer: %r" % meshes


@check("one-pass native encode_into rejects a domain error (2-index face)")
def _():
    _reset()
    bad = [("m", [(0, 0, 0), (1, 0, 0)], [(0, 1)])]
    assert native._native_obj_encode_into(bad) is None, \
        "a 2-index face must be a domain error, not a fallback"
    # And an over-cap input (a face index beyond the vertex count) too.
    assert native._native_obj_encode_into(
        [("m", [(0, 0, 0), (1, 0, 0)], [(0, 1, 5)])]) is None


@check("production OBJ below the crossover stays on Python (counter unchanged)")
def _():
    _reset()
    # A 3-vertex input is below the 1024-vertex crossover: Python must run.
    tiny = [("tiny", [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])]
    assert native._obj_native_active(tiny) is False, \
        "a sub-crossover input must stay on Python"
    before = native._native_obj_calls
    p = os.path.join(TMP, "crossover.obj")
    n = obj.write_obj(p, tiny)
    got = open(p, "rb").read()
    assert native._native_obj_calls == before, \
        "sub-crossover production write must not use native"
    assert got == _py_reference(tiny)
    assert n == len(got)


@check("cumulative index is not reset per mesh (native)")
def _():
    _reset()
    b = native.encode_obj(_two_meshes())
    text = b.decode("latin-1")
    # Mesh A has 4 vertices, so mesh B's face (0,1,2,3) is global 5 6 7 8.
    assert "f 5 6 7 8\n" in text, "cumulative offset lost natively: %r" % text
    assert "f 1 2 3 4" not in text, \
        "second mesh must not restart at 1: %r" % text
    assert text.startswith("# Narwhal 3D\n"), text[:20]
    assert "\n\n" not in text, "no blank lines between meshes: %r" % text
    assert text.endswith("\n"), "file must end with a newline: %r" % text[-20:]


@check("one-pass cumulative offset: direct and production both keep the second mesh's offset")
def _():
    # Q117-01 mutation target: a reset of the second mesh's vertex offset
    # (mesh B's first face written as ``f 1 2 3 ...`` instead of
    # ``f (len(A_verts)+1) ...``) must go RED in BOTH the direct one-pass
    # encode and the production ``write_obj`` output, because both share the
    # single ``build_into`` serializer. The legacy ``encode_obj`` shares it
    # too, so all three routes must agree byte-for-byte.
    _reset()
    big = _big_two_meshes()
    a_nverts = len(big[0][1])
    b_first_face = big[1][2][0]
    # Mesh B's first face, 1-based, shifted by mesh A's vertex count. Mesh A's
    # indices are 1..a_nverts, so this line (all indices > a_nverts) can only
    # come from mesh B -- a robust positive check that the offset was applied.
    b_first = "f " + " ".join(str(i + 1 + a_nverts) for i in b_first_face) + "\n"
    direct = native._native_obj_encode_into(big)
    assert direct is not None, "one-pass encode must succeed"
    direct_bytes = bytes(direct[0][:direct[1]])
    legacy = native.encode_obj(big)
    p = os.path.join(TMP, "mutation.obj")
    obj.write_obj(p, big)
    prod = open(p, "rb").read()
    ref = _py_reference(big)
    # The mutation (resetting mesh B's offset) makes BOTH the direct one-pass
    # and the production output DIFFER from the Python reference (which keeps
    # the correct offset), so the byte-identity assertion below goes RED for
    # both. The positive b_first check makes the failure unambiguous.
    assert direct_bytes == legacy == prod == ref, \
        "direct / legacy / production / python must all agree"
    for label, text in (("direct", direct_bytes), ("legacy", legacy),
                        ("production", prod)):
        assert (b_first in text.decode("latin-1")), \
            ("%s output lost the second mesh's offset" % label)


@check("an empty mesh list is just the 13-byte header (native)")
def _():
    _reset()
    b = native.encode_obj([])
    assert b == b"# Narwhal 3D\n", ("empty list", b)
    assert len(b) == 13, len(b)


@check("fallback: missing library uses the unchanged Python writer")
def _():
    _reset()
    os.environ["NARWHAL_PURE_CORE"] = os.path.join(TMP, "no_such_lib.so")
    assert native.available() is False, \
        "a missing library must be unavailable, got %r" % native.backend_status()
    assert native.encode_obj(_two_meshes()) is None
    _assert_fallback(_two_meshes(), "missing-bin")
    if _LIB is not None:
        os.environ["NARWHAL_PURE_CORE"] = _LIB


@check("fallback: a library without the OBJ exports uses Python")
def _():
    # An empty .so models "old symbol": it has neither the STL nor the OBJ
    # exports, so discovery must fail and the Python writer must run.
    _reset()
    assert _LIB is not None
    empty = os.path.join(TMP, "empty.so")
    open(empty, "wb").close()
    os.environ["NARWHAL_PURE_CORE"] = empty
    assert native.available() is False, \
        "a library without the OBJ exports must be unavailable"
    _assert_fallback(_two_meshes(), "oldsym")
    if _LIB is not None:
        os.environ["NARWHAL_PURE_CORE"] = _LIB


@check("fallback: a native domain error (2-index face) uses the Python writer")
def _():
    _reset()
    # Ensure the native path is active so this is a genuine native->fallback.
    assert native.available() is True, native.backend_status()
    bad = [("m", [(0, 0, 0), (1, 0, 0)], [(0, 1)])]
    # encode_obj itself must report the domain error as None (not bytes).
    assert native.encode_obj(bad) is None
    # write_obj must fall back to the Python writer, which rejects the face
    # with ValueError (never a silent write).
    p = os.path.join(TMP, "nativeerr.obj")
    try:
        obj.write_obj(p, bad)
        raise AssertionError("a 2-index face did not raise via write_obj")
    except ValueError:
        pass
    assert not os.path.exists(p), "no partial file may be left behind"


@check("fallback: a malformed native output uses the Python writer")
def _():
    _reset()
    assert native.available() is True, native.backend_status()
    real = native.encode_obj
    native.encode_obj = lambda *a, **k: None  # simulate a failed encode
    try:
        _assert_fallback(_two_meshes(), "malformed")
    finally:
        native.encode_obj = real


@check("production OBJ writing consumes one-shot generators exactly once")
def _():
    _reset()
    expected = _two_meshes()
    seen = {"meshes": 0, "vertices": 0, "faces": 0}

    def mesh_stream():
        for name, vertices, faces in expected:
            seen["meshes"] += 1

            def vertex_stream(values=vertices):
                for value in values:
                    seen["vertices"] += 1
                    yield value

            def face_stream(values=faces):
                for value in values:
                    seen["faces"] += 1
                    yield (index for index in value)

            yield name, vertex_stream(), face_stream()

    p = os.path.join(TMP, "generator-production.obj")
    before = native._native_obj_calls
    obj.write_obj(p, mesh_stream())
    assert open(p, "rb").read() == _py_reference(expected)
    assert seen == {"meshes": 2, "vertices": 8, "faces": 3}, seen
    assert native._native_obj_calls == before


@check("native decode of a single mesh returns (meshes, ignored) and moves the counter")
def _():
    _reset()
    assert native.available() is True, native.backend_status()
    data = b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
    before = native._native_decode_obj_calls
    got = native.decode_obj(data, "m")
    assert native._native_decode_obj_calls == before + 1, \
        ("counter must move on a successful native decode",
         before, native._native_decode_obj_calls)
    assert got == ([("m", [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
                     [(0, 1, 2)])], {}), got


@check("native decode of a two-mesh file is struct-identical to the Python reader")
def _():
    _reset()
    assert native.available() is True, native.backend_status()
    # A file the paired writer would emit (mesh A: 4 verts / 2 tris; mesh B:
    # 4 verts / 1 quad, written with GLOBAL indices 5 6 7 8).
    data = (b"o floor\n"
            b"v 0 0 0\nv 10 0 0\nv 10 10 0\nv 0 10 0\n"
            b"f 1 2 3\nf 1 3 4\n"
            b"o roof\n"
            b"v 0 0 5\nv 10 0 5\nv 10 10 5\nv 0 10 5\n"
            b"f 5 6 7 8\n")
    meshes, ignored = native.decode_obj(data, "model")
    assert ignored == {}, ignored
    assert [m[0] for m in meshes] == ["floor", "roof"], [m[0] for m in meshes]
    # Mesh A: two triangles. Mesh B: one quad (kept at its polygon degree).
    assert meshes[0][1] == [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0),
                            (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)], meshes[0][1]
    assert meshes[0][2] == [(0, 1, 2), (0, 2, 3)], meshes[0][2]
    assert meshes[1][2] == [(0, 1, 2, 3)], meshes[1][2]


@check("native decode keeps negative indices and all four face forms")
def _():
    _reset()
    assert native.available() is True, native.backend_status()
    data = (b"v 0 0 0\nv 1 0 0\nv 0 1 0\nv 1 1 0\n"
            b"f -4 -3 -2 -1\n"
            b"f 1 2 3\n"
            b"f 1/2 2/3 3/4\n"
            b"f 1//2 2//3 3//4\n"
            b"f 1/2/3 2/3/4 3/4/5\n")
    meshes, _ = native.decode_obj(data, "m")
    assert meshes[0][2] == [(0, 1, 2, 3), (0, 1, 2), (0, 1, 2),
                            (0, 1, 2), (0, 1, 2)], meshes[0][2]


@check("native decode handles CRLF and CR line endings")
def _():
    _reset()
    assert native.available() is True, native.backend_status()
    for data in (b"v 0 0 0\r\nv 1 0 0\r\nv 0 1 0\r\nf 1 2 3\r\n",
                 b"v 0 0 0\rv 1 0 0\rv 0 1 0\rf 1 2 3\r"):
        meshes, _ = native.decode_obj(data, "m")
        assert meshes[0][1] == [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
                                (0.0, 1.0, 0.0)], meshes[0][1]
        assert meshes[0][2] == [(0, 1, 2)], meshes[0][2]


@check("native decode counts the ignored kinds (vt vn vp s mtllib usemtl)")
def _():
    _reset()
    assert native.available() is True, native.backend_status()
    data = (b"mtllib mat.mtl\nusemtl steel\n"
            b"v 0 0 0\nv 1 0 0\nv 0 1 0\n"
            b"vt 0 0\nvt 1 0\n"
            b"vn 0 0 1\n"
            b"vp 0 0 0 1\n"
            b"s 1\n"
            b"f 1 2 3\n")
    meshes, ignored = native.decode_obj(data, "m")
    assert ignored == {"mtllib": 1, "usemtl": 1, "vt": 2, "vn": 1, "vp": 1,
                       "s": 1}, ignored


@check("read_obj activates the native decode route (counter moves, output matches Python)")
def _():
    _reset()
    assert native.available() is True, native.backend_status()
    text = ("# comment\n"
            "o A\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
            "g B\nv 5 5 5\nv 6 5 5\nv 5 6 5\nf 4 5 6\n"
            "mtllib m.mtl\nvt 0 0\nvn 0 0 1\ns 1\n")
    p = os.path.join(TMP, "route.obj")
    with open(p, "w", encoding="latin-1") as fh:
        fh.write(text)
    before = native._native_decode_obj_calls
    got = rdr.read_obj(p)
    assert native._native_decode_obj_calls > before, \
        ("route did not use native decode (counter %d -> %d)" %
         (before, native._native_decode_obj_calls))
    assert [m[0] for m in got] == ["A", "B"], [m[0] for m in got]
    assert got[0][2] == [(0, 1, 2)] and got[1][2] == [(0, 1, 2)], got
    kinds = rdr.ignored_kinds()
    assert kinds.get("mtllib") == 1, kinds
    assert kinds.get("vt") == 1, kinds
    assert kinds.get("vn") == 1, kinds
    assert kinds.get("s") == 1, kinds


@check("read_obj on an empty file returns [] via native decode")
def _():
    _reset()
    assert native.available() is True, native.backend_status()
    p = os.path.join(TMP, "empty.obj")
    with open(p, "w", encoding="latin-1") as fh:
        fh.write("")
    before = native._native_decode_obj_calls
    got = rdr.read_obj(p)
    assert native._native_decode_obj_calls > before, \
        ("empty file must go through native decode (counter %d -> %d)" %
         (before, native._native_decode_obj_calls))
    assert got == [], got
    assert rdr.ignored_kinds() == {}, rdr.ignored_kinds()


@check("native decode rejects a v with too few coordinates")
def _():
    _reset()
    assert native.available() is True, native.backend_status()
    data = b"v 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
    before = native._native_decode_obj_calls
    got = native.decode_obj(data, "m")
    assert got is None, got
    assert native._native_decode_obj_calls == before, \
        "counter must not move on a rejected file"


@check("native decode rejects a face index beyond the vertex count")
def _():
    _reset()
    assert native.available() is True, native.backend_status()
    data = b"v 0 0 0\nv 1 0 0\nf 1 2 99\n"
    before = native._native_decode_obj_calls
    got = native.decode_obj(data, "m")
    assert got is None, got
    assert native._native_decode_obj_calls == before, \
        "counter must not move on a rejected file"


@check("fallback: missing library -> Python reader, counter NOT moved")
def _():
    _reset()
    text = "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
    p = os.path.join(TMP, "fb_missing.obj")
    with open(p, "w", encoding="latin-1") as fh:
        fh.write(text)
    os.environ["NARWHAL_PURE_CORE"] = os.path.join(TMP, "no_such_lib.so")
    _reset()
    before = native._native_decode_obj_calls
    got = rdr.read_obj(p)
    assert native._native_decode_obj_calls == before, \
        "counter must not move on a missing-library fallback"
    assert got == [("fb_missing", [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
                                  (0.0, 1.0, 0.0)], [(0, 1, 2)])], got
    if _LIB is not None:
        os.environ["NARWHAL_PURE_CORE"] = _LIB


@check("fallback: a library without the OBJ decode exports -> Python reader")
def _():
    _reset()
    assert _LIB is not None
    assert native.available() is True, native.backend_status()
    real_binding = native._binding

    def _binding_no_decode():
        b = real_binding()
        if b is None:
            return None
        # The binding is the 13-tuple
        # (stl_size, stl_encode, stl_decode_size, stl_decode,
        #  obj_size, obj_encode, obj_decode_size, obj_decode,
        #  dxf_size, dxf_encode, dxf_decode_size, dxf_decode, path).
        # Null out only the OBJ decode slots (6, 7); keep the DXF slots
        # (8-11) and the path (12).
        return (b[0], b[1], b[2], b[3], b[4], b[5], None, None,
                b[8], b[9], b[10], b[11], b[12])

    native._binding = _binding_no_decode
    try:
        data = b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
        before = native._native_decode_obj_calls
        got = native.decode_obj(data, "m")
        assert got is None, got
        assert native._native_decode_obj_calls == before, \
            "counter must not move when the decode exports are absent"
    finally:
        native._binding = real_binding


@check("fallback: a malformed native packed output -> Python reader")
def _():
    _reset()
    assert native.available() is True, native.backend_status()
    real_parse = native._parse_obj_packed

    def _raise(*_a, **_k):
        raise ValueError("bad packed")

    native._parse_obj_packed = _raise
    try:
        data = b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
        before = native._native_decode_obj_calls
        got = native.decode_obj(data, "m")
        assert got is None, got
        assert native._native_decode_obj_calls == before, \
            "counter must not move on a malformed packed output"
    finally:
        native._parse_obj_packed = real_parse


def _obj_packed(*, vcount=9, fcount=1, arity=3, indices=(0, 1, 2),
                reserved=b"\0" * 8, trailing=b""):
    return b"".join([
        struct.pack("<IIII", 0x33445741, 1, 1, 0), reserved,
        struct.pack("<q", 1), b"m",
        struct.pack("<q", vcount), struct.pack("<q", fcount),
        struct.pack("<q", arity),
        struct.pack("<9d", 0, 0, 0, 1, 0, 0, 0, 1, 0),
        struct.pack("<%dq" % len(indices), *indices), trailing,
    ])


@check("strict OBJ packed validation rejects counts, arity, indices, and trailing bytes")
def _():
    assert native._parse_obj_packed(_obj_packed(), "m")[0][0][2] == [(0, 1, 2)]
    bad_payloads = [
        _obj_packed(vcount=8),
        _obj_packed(fcount=-1),
        _obj_packed(arity=2, indices=(0, 1)),
        _obj_packed(indices=(0, 1, 3)),
        _obj_packed(reserved=b"\0" * 7 + b"x"),
        _obj_packed(trailing=b"x"),
    ]
    for payload in bad_payloads:
        try:
            native._parse_obj_packed(payload, "m")
            raise AssertionError("malformed packed OBJ was accepted: %r" % payload[:40])
        except ValueError:
            pass


@check("malformed successful OBJ packed output returns None without counting")
def _():
    _reset()
    real_binding = native._binding
    base = real_binding()
    payload = _obj_packed(indices=(0, 1, 3))

    def size_fn(_data, _length):
        return len(payload)

    def decode_fn(_data, _length, out, out_len):
        assert out_len == len(payload)
        ctypes.memmove(out, payload, len(payload))
        return 0

    native._binding = lambda: (*base[:6], size_fn, decode_fn, *base[8:])
    try:
        before = native._native_decode_obj_calls
        assert native.decode_obj(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", "m") is None
        assert native._native_decode_obj_calls == before
    finally:
        native._binding = real_binding


@check("OBJ i32 packing overflow declines to the Python error contract")
def _():
    _reset()
    verts = [(float(i), 0.0, 0.0) for i in range(1024)]
    meshes = [("m", verts, [(0, 1, 2**40)])]
    assert native._native_obj_encode_into(meshes) is None
    p = os.path.join(TMP, "overflow.obj")
    try:
        obj.write_obj(p, meshes)
    except ValueError as exc:
        assert "out of range" in str(exc)
    else:
        raise AssertionError("Python fallback must preserve its ValueError")


# ---------------------------------------------------------------------------
failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
