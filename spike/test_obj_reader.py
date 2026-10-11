r"""Pure-Python tests for the OBJ reader.

Pins the import contract:
* a file written by the paired writer reads back to the same meshes;
* NEGATIVE (relative) indices: -1 = the last vertex defined so far;
* all four face forms i, i/t, i//n, i/t/n yield the same vertex index;
* a face that references a missing vertex raises ValueError NAMING the line;
* `o` and `g` each start a new mesh; no `o`/`g` -> one mesh named after the file;
* comments / blank lines ignored; `\` continuations join; a 4th `v` coord (w)
  is read and ignored; the ignored kinds (vt vn vp s mtllib usemtl) are counted.

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


rdr = _load("rhino_obj_reader", "obj_reader.py")
wtr = _load("rhino_obj_writer", "obj_writer.py")

results = []
TMP = tempfile.mkdtemp(prefix="objreader_")


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


@check("module loads without bpy and read_obj + ignored_kinds exist")
def _():
    assert "bpy" not in sys.modules
    assert callable(rdr.read_obj) and callable(rdr.ignored_kinds)


@check("round-trips a writer-produced file (2 meshes, exact)")
def _():
    meshes_in = [
        ("quad", [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
         [(0, 1, 2), (1, 3, 2)]),
        ("tri", [(0, 0, 0), (2, 0, 0), (0, 3, 0)],
         [(0, 1, 2)]),
    ]
    p = os.path.join(TMP, "rt.obj")
    wtr.write_obj(p, meshes_in)
    got = rdr.read_obj(p)
    assert [m[0] for m in got] == ["quad", "tri"], [m[0] for m in got]
    for (gn, gv, gf), (wn, wv, wf) in zip(got, meshes_in):
        assert gv == wv, (gn, gv, wv)
        assert gf == wf, (gn, gf, wf)


@check("negative indices: -1 is the last vertex defined so far")
def _():
    p = _write("neg.obj",
               "v 0 0 0\nv 1 0 0\nv 0 1 0\nv 1 1 0\nf -4 -3 -2 -1\n")
    got = rdr.read_obj(p)
    assert got[0][2] == [(0, 1, 2, 3)], got[0][2]


@check("all four face forms (i, i/t, i//n, i/t/n) give the same vertex index")
def _():
    p = _write("forms.obj",
               "v 0 0 0\nv 1 0 0\nv 0 1 0\n"
               "f 1 2 3\nf 1/2 2/3 3/4\nf 1//2 2//3 3//4\nf 1/2/3 2/3/4 3/4/5\n")
    got = rdr.read_obj(p)
    assert got[0][2] == [(0, 1, 2), (0, 1, 2), (0, 1, 2), (0, 1, 2)], got[0][2]


@check("a face referencing a missing vertex raises ValueError naming the line")
def _():
    p = _write("bad.obj", "v 0 0 0\nv 1 0 0\nf 1 2 99\n")
    try:
        rdr.read_obj(p)
        raise AssertionError("expected ValueError for a missing vertex")
    except ValueError as e:
        msg = str(e)
        assert "line 3" in msg, msg
        assert "99" in msg, msg


@check("a face index that is negative beyond the vertex count is refused")
def _():
    p = _write("badneg.obj", "v 0 0 0\nv 1 0 0\nf -3 -2 -1\n")
    try:
        rdr.read_obj(p)
        raise AssertionError("expected ValueError for an out-of-range negative index")
    except ValueError as e:
        assert "line 3" in str(e), str(e)


@check("o and g each start a new mesh")
def _():
    p = _write("og.obj",
               "o A\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
               "g B\nv 5 5 5\nv 6 5 5\nv 5 6 5\nf 4 5 6\n")
    got = rdr.read_obj(p)
    assert [m[0] for m in got] == ["A", "B"], [m[0] for m in got]
    assert got[0][2] == [(0, 1, 2)] and got[1][2] == [(0, 1, 2)], got


@check("no o/g -> one mesh named after the file")
def _():
    p = _write("mypart.obj",
               "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    got = rdr.read_obj(p)
    assert len(got) == 1, got
    assert got[0][0] == "mypart", got[0][0]


@check("comments and blank lines are ignored")
def _():
    p = _write("cmt.obj",
               "# a comment\n\nv 0 0 0\n# another\n\nv 1 0 0\nv 0 1 0\n\nf 1 2 3\n")
    got = rdr.read_obj(p)
    assert got[0][1] == [(0, 0, 0), (1, 0, 0), (0, 1, 0)], got[0][1]
    assert got[0][2] == [(0, 1, 2)], got[0][2]


@check("a backslash line continuation joins the next physical line")
def _():
    p = _write("cont.obj", "v 0 0 0\nv 1 0 0\nv 0 1 0\nv 1 1 0\nf 1 2 \\\n3 4\n")
    got = rdr.read_obj(p)
    assert got[0][2] == [(0, 1, 2, 3)], got[0][2]


@check("a 4th vertex coordinate (w) is read and ignored")
def _():
    p = _write("w.obj", "v 0 0 0 1\nv 1 0 0 1\nv 0 1 0 1\nf 1 2 3\n")
    got = rdr.read_obj(p)
    assert got[0][1] == [(0, 0, 0), (1, 0, 0), (0, 1, 0)], got[0][1]
    assert got[0][2] == [(0, 1, 2)], got[0][2]


@check("the ignored kinds (vt vn vp s mtllib usemtl) are counted")
def _():
    p = _write("ign.obj",
               "mtllib mat.mtl\nusemtl steel\n"
               "v 0 0 0\nv 1 0 0\nv 0 1 0\n"
               "vt 0 0\nvt 1 0\n"
               "vn 0 0 1\n"
               "vp 0 0 0 1\n"
               "s 1\n"
               "f 1 2 3\n")
    got = rdr.read_obj(p)
    assert got[0][2] == [(0, 1, 2)], got[0][2]
    kinds = rdr.ignored_kinds()
    assert kinds.get("mtllib") == 1, kinds
    assert kinds.get("usemtl") == 1, kinds
    assert kinds.get("vt") == 2, kinds
    assert kinds.get("vn") == 1, kinds
    assert kinds.get("vp") == 1, kinds
    assert kinds.get("s") == 1, kinds


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
