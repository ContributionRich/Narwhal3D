"""Pure-Python tests for the OBJ writer.

Pins the invariants a mesh reader depends on:
* one ``o name`` per mesh, ``v`` lines then ``f`` lines, header comment
  ``# Narwhal 3D``;
* indices are 1-based: a face ``(0,1,2)`` is written ``f 1 2 3``;
* indices CONTINUE across meshes -- the second mesh's first face references
  vertices AFTER the first mesh's (the bug this guards: a restart at 1 would
  silently point at the previous mesh's geometry);
* the file round-trips: parse our own output back and it matches the input;
* a face with < 3 vertices or an out-of-range index raises ``ValueError``
  (never a silent write).

"""
import importlib.util
import os
import re
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


obj = _load("narwhal_obj_writer", "obj_writer.py")

results = []
TMP = tempfile.mkdtemp(prefix="obj_")


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


def _parse(text):
    """Parse OBJ text back into ``[(name, [vertices], [faces]), ...]``.

    Vertices are 3-tuples of floats; faces are 1-based index tuples (as
    written in the file).
    """
    meshes = []
    cur = None
    for line in text.splitlines():
        t = line.strip()
        if not t or t.startswith("#"):
            continue
        if t.startswith("o "):
            cur = (t[2:].strip(), [], [])
            meshes.append(cur)
        elif t.startswith("v "):
            assert cur is not None, "v before any o"
            cur[1].append(tuple(map(float, t.split()[1:4])))
        elif t.startswith("f "):
            assert cur is not None, "f before any o"
            cur[2].append(tuple(int(x) for x in t.split()[1:]))
    return meshes


def _two_meshes():
    """Mesh A: a square (4 verts, two tris). Mesh B: another square (4 verts,
    two quads). B's faces use 0-based indices into ITS OWN vertex list."""
    a = ("floor", [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)],
         [(0, 1, 2), (0, 2, 3)])
    b = ("roof", [(0, 0, 5), (10, 0, 5), (10, 10, 5), (0, 10, 5)],
         [(0, 1, 2, 3)])
    return [a, b]


@check("module loads without bpy and write_obj is callable")
def _():
    assert "bpy" not in sys.modules
    assert callable(obj.write_obj)


@check("two meshes: the second mesh's faces reference vertices after the first's")
def _():
    p = os.path.join(TMP, "two.obj")
    obj.write_obj(p, _two_meshes())
    meshes = _parse(open(p).read())
    assert len(meshes) == 2, len(meshes)
    # first mesh has 4 vertices, so the second mesh's indices must all be > 4
    for face in meshes[1][2]:
        for i in face:
            assert i > 4, (face, i)
    # specifically the second mesh's first face (0,1,2,3) is written 5 6 7 8
    assert meshes[1][2][0] == (5, 6, 7, 8), meshes[1][2]


@check("1-based: a face (0,1,2) is written `f 1 2 3`")
def _():
    p = os.path.join(TMP, "onebased.obj")
    one = ("m", [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])
    obj.write_obj(p, [one])
    text = open(p).read()
    assert "f 1 2 3" in text, text
    assert "f 0 " not in text, text  # never a 0 index


@check("round-trips: parsing our own output matches the input")
def _():
    p = os.path.join(TMP, "rt.obj")
    meshes = _two_meshes()
    obj.write_obj(p, meshes)
    got = _parse(open(p).read())
    assert len(got) == len(meshes), len(got)
    offset = 0  # cumulative vertices of all earlier meshes
    for (gn, gv, gf), (wn, wv, wf) in zip(got, meshes):
        assert gn == wn, (gn, wn)
        assert len(gv) == len(wv), (len(gv), len(wv))
        for a, b in zip(gv, wv):
            for x, y in zip(a, b):
                assert abs(x - y) <= 1e-6 * (1.0 + abs(y)), (a, b)
        # _parse returns GLOBAL 1-based indices; the input is 0-based LOCAL.
        assert len(gf) == len(wf), (gf, wf)
        for gface, wface in zip(gf, wf):
            expect = tuple((i + 1 + offset) for i in wface)
            assert gface == expect, (gface, wface)
        offset += len(wv)


@check("carries the `# Narwhal 3D` header comment")
def _():
    p = os.path.join(TMP, "hdr.obj")
    obj.write_obj(p, _two_meshes())
    first = open(p).read().splitlines()[0]
    assert first == "# Narwhal 3D", first


@check("a bad index raises ValueError (never a silent write)")
def _():
    p = os.path.join(TMP, "bad.obj")
    try:
        obj.write_obj(p, [("m", [(0, 0, 0), (1, 0, 0)], [(0, 1, 5)])])
        raise AssertionError("out-of-range index did not raise")
    except ValueError:
        pass
    # no partial file left behind that looks valid
    assert not os.path.exists(p)


@check("a face with fewer than 3 vertices raises ValueError")
def _():
    p = os.path.join(TMP, "shortface.obj")
    try:
        obj.write_obj(p, [("m", [(0, 0, 0), (1, 0, 0)], [(0, 1)])])
        raise AssertionError("a 2-vertex face did not raise")
    except ValueError:
        pass


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
