"""Pure-Python tests for binary and ASCII STL output.

Pins the file-format invariants a 3-D printer / slicer depends on:
* the binary layout is exactly 84 + 50*n bytes (80-byte header + uint32 count +
  50-byte facets), and the 80-byte header does NOT begin with the word `solid`
  (some readers then take a binary file for ASCII);
* the triangle count and every vertex round-trip a file we just wrote back to
  float32 precision;
* every facet normal is normalised and points OUTWARD (for a known cube);
* a degenerate (zero-area) triangle is KEPT with a zero normal, never dropped;
* the ASCII dialect round-trips too and carries `solid`/`endsolid`.

"""
import importlib.util
import os
import struct
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


stl = _load("narwhal_stl_writer", "stl_writer.py")

results = []
TMP = tempfile.mkdtemp(prefix="stl_")


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


def _f32_ok(a, b):
    """Two floats agree to float32 precision (~7 significant digits)."""
    return abs(a - b) <= 1e-6 * (1.0 + abs(b))


def _tris_match(got, want):
    if len(got) != len(want):
        return False
    for g, w in zip(got, want):
        if len(g) != 3 or len(w) != 3:
            return False
        for gp, wp in zip(g, w):
            for a, b in zip(gp, wp):
                if not _f32_ok(a, b):
                    return False
    return True


def _unit_cube():
    """A 0..1 cube as 12 triangles, wound CCW seen from outside."""
    v000 = (0, 0, 0); v100 = (1, 0, 0); v010 = (0, 1, 0); v110 = (1, 1, 0)
    v001 = (0, 0, 1); v101 = (1, 0, 1); v011 = (0, 1, 1); v111 = (1, 1, 1)
    return [
        (v001, v101, v111), (v001, v111, v011),   # +z
        (v100, v000, v010), (v110, v100, v010),   # -z
        (v100, v110, v111), (v111, v101, v100),   # +x
        (v010, v000, v001), (v011, v010, v001),   # -x
        (v110, v010, v011), (v011, v111, v110),   # +y
        (v000, v100, v101), (v101, v001, v000),   # -y
    ]


CUBE = _unit_cube()


@check("module loads without bpy and both functions are callable")
def _():
    assert "bpy" not in sys.modules
    assert callable(stl.write_stl) and callable(stl.read_stl)


@check("binary unit cube is exactly 84 + 12*50 bytes")
def _():
    p = os.path.join(TMP, "cube.bin.stl")
    n = stl.write_stl(p, CUBE, binary=True)
    assert n == os.path.getsize(p) == 84 + 12 * 50, (n, os.path.getsize(p))


@check("binary 80-byte header defaults to Narwhal 3D and not `solid`")
def _():
    p = os.path.join(TMP, "hdr.bin.stl")
    stl.write_stl(p, CUBE, binary=True)
    with open(p, "rb") as fh:
        hdr = fh.read(80)
    assert len(hdr) == 80
    assert hdr.rstrip() == b"Narwhal 3D", hdr[:20]
    assert hdr.lstrip().startswith(b"solid") is False, hdr[:20]


@check("a binary name starting with `solid` falls back to Narwhal 3D")
def _():
    p = os.path.join(TMP, "solidd.bin.stl")
    stl.write_stl(p, CUBE, binary=True, name="solid cube")
    with open(p, "rb") as fh:
        hdr = fh.read(80)
    assert hdr.rstrip() == b"Narwhal 3D", hdr[:20]
    assert hdr.lstrip().startswith(b"solid") is False, hdr[:20]


@check("binary read-back equals the input to float32 precision")
def _():
    p = os.path.join(TMP, "rt.bin.stl")
    stl.write_stl(p, CUBE, binary=True)
    got = stl.read_stl(p)
    assert _tris_match(got, CUBE), got[:2]


@check("every unit-cube facet normal points OUTWARD")
def _():
    p = os.path.join(TMP, "norm.bin.stl")
    stl.write_stl(p, CUBE, binary=True)
    with open(p, "rb") as fh:
        data = fh.read()
    count = struct.unpack_from("<I", data, 80)[0]
    assert count == 12, count
    cx = cy = cz = 0.5  # cube centre
    off = 84
    for i in range(count):
        nx, ny, nz = struct.unpack_from("<3f", data, off)
        # face centre = mean of the facet's 3 vertices (12 bytes after the normal)
        v = struct.unpack_from("<9f", data, off + 12)
        off += 50
        fx = (v[0] + v[3] + v[6]) / 3.0
        fy = (v[1] + v[4] + v[7]) / 3.0
        fz = (v[2] + v[5] + v[8]) / 3.0
        dot = (fx - cx) * nx + (fy - cy) * ny + (fz - cz) * nz
        assert dot > 0.0, (i, (nx, ny, nz), (fx, fy, fz), dot)


@check("ASCII round-trips and defaults to the Narwhal 3D solid name")
def _():
    p = os.path.join(TMP, "cube.ascii.stl")
    stl.write_stl(p, CUBE, binary=False)
    text = open(p).read()
    assert text.startswith("solid Narwhal 3D\n"), text[:30]
    assert text.rstrip().splitlines()[-1] == "endsolid Narwhal 3D", text[-30:]
    got = stl.read_stl(p)
    assert _tris_match(got, CUBE), got[:2]


@check("a degenerate (zero-area) triangle is kept with a zero normal")
def _():
    deg = [((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 0.0, 0.0))]
    p = os.path.join(TMP, "deg.bin.stl")
    n = stl.write_stl(p, deg, binary=True)
    assert n == 84 + 1 * 50, n
    with open(p, "rb") as fh:
        data = fh.read()
    assert struct.unpack_from("<I", data, 80)[0] == 1  # not dropped
    nx, ny, nz = struct.unpack_from("<3f", data, 84)
    assert (nx, ny, nz) == (0.0, 0.0, 0.0), (nx, ny, nz)
    # and it still reads back (the vertex data is present)
    assert len(stl.read_stl(p)) == 1


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
