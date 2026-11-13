#!/usr/bin/env python3
"""Isolated CPython norm bit parity proof; no activation or shared-file edits.
Run with bundled Python 3.13; --mutant builds ONLY a copied defective module.
"""
import ctypes as C
import hashlib
import math
import os
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile
import atexit

ROOT = Path(__file__).resolve().parents[1]


def atomic(path, text):
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text)
    os.replace(temp, path)


def build(mutant):
    if os.environ.get("NARWHAL_PURE_CORE") and not mutant:
        lib = C.CDLL(os.environ["NARWHAL_PURE_CORE"])
        f = lib.narwhal_pure_core_norm_v1
        f.argtypes = [C.POINTER(C.c_double), C.c_size_t, C.c_uint32, C.POINTER(C.c_double)]
        f.restype = C.c_int32
        return f
    temporary = tempfile.TemporaryDirectory(prefix="native-norm-proof-")
    atexit.register(temporary.cleanup)
    scratch = Path(temporary.name)
    source = (ROOT / "native/pure_core/src/python_norm.rs").read_text()
    if mutant:
        assert "    h / scale" in source
        source = source.replace("    h / scale", "    values.iter().fold(0.0_f64, |a, x| a.hypot(*x))")
    atomic(scratch / "python_norm.rs", source)
    root = (ROOT / "native/pure_core/src/lib.rs").read_text()
    # Compile the repository's actual ABI validation helpers, not imitations.
    helpers = root[root.index("use std::mem::{"):root.index("/// Return the stable ABI")]
    atomic(scratch / "lib.rs", helpers + "\nmod python_norm;\n")
    atomic(scratch / "Cargo.toml", '[package]\nname="norm-proof"\nversion="0.0.0"\nedition="2021"\n[workspace]\n[lib]\npath="lib.rs"\ncrate-type=["cdylib"]\n')
    subprocess.run(["cargo", "+1.95.0", "build", "--offline", "--release", "--manifest-path", str(scratch / "Cargo.toml")], check=True, timeout=90)
    lib = C.CDLL(str(scratch / "target/release/libnorm_proof.so"))
    f = lib.narwhal_pure_core_norm_v1
    f.argtypes = [C.POINTER(C.c_double), C.c_size_t, C.c_uint32, C.POINTER(C.c_double)]
    f.restype = C.c_int32
    return f


def bits(x):
    return struct.pack("=d", x)


def check(f, mode, rows):
    flat = [v for row in rows for v in row]
    values = (C.c_double * len(flat))(*flat)
    out = (C.c_double * len(rows))()
    assert f(values, len(rows), mode, out) == 0
    for i, (row, got) in enumerate(zip(rows, out)):
        expected = math.dist(row[:3], row[3:]) if mode == 6 else math.hypot(*row)
        assert (math.isnan(expected) and math.isnan(got)) or bits(expected) == bits(got), (
            mode, i, row, expected.hex(), got.hex())
    return len(rows)


def main():
    assert sys.version_info[:2] == (3, 13), sys.version
    f = build("--mutant" in sys.argv)
    witness = (-.4702396670038951, -.5067449846120331, .12273626832630158)
    total = check(f, 6, [(0., 0., 0., *witness)])
    r = random.Random(42)
    special = [0., -0., math.ulp(0.), -math.ulp(0.), sys.float_info.min,
               math.nextafter(sys.float_info.min, 0.), sys.float_info.max,
               -sys.float_info.max, 1., -1., math.inf, -math.inf, math.nan]
    def wide():
        while True:
            x = struct.unpack("=d", struct.pack("=Q", r.getrandbits(64)))[0]
            if math.isfinite(x):
                return x
    for mode in (2, 3, 6):
        rows = []
        for i in range(60000):
            if i % 3 == 0:
                row = tuple(wide() for _ in range(mode))
            elif i % 3 == 1:
                # Similar exponents exercise compensation, unlike dominated norms.
                e = r.randrange(-1074, 1024)
                row = tuple(math.ldexp(r.uniform(-1., 1.), e) for _ in range(mode))
            else:
                x = wide()
                row = tuple(r.choice((x, math.nextafter(x, -math.inf), math.nextafter(x, math.inf), 0.)) for _ in range(mode))
            rows.append(row)
        for x in special:
            for y in special:
                rows.append((x, y, *([0.] * (mode - 2))))
                rows.append((y, *([x] * (mode - 1))))
        if mode == 6:
            # Full 3D join fixture distances at three signed scales and exact
            # threshold neighbours: never introduce a tolerance in the proof.
            for scale in (1., -1., 1.e100):
                for _ in range(1000):
                    d = tuple(scale * r.uniform(-1., 1.) for _ in range(3))
                    rows.append((0., 0., 0., *d))
        total += check(f, mode, rows)
    # Full-length segments around the gap, with Python's exact and adjacent
    # thresholds. This proves the scalar decision, not unregistered Join wiring.
    for scale in (1., -1., 1.e100):
        for _ in range(1000):
            d = tuple(scale * r.uniform(-1., 1.) for _ in range(3))
            origin = (0., 0., 0.)
            start = (-10. * scale,) * 3
            end = (10. * scale,) * 3
            tol = math.dist(origin, d)
            assert math.dist(start, origin) > tol
            assert math.dist(d, end) > tol
            a = (C.c_double * 6)(*origin, *d)
            distance = C.c_double()
            assert f(a, 1, 6, C.byref(distance)) == 0
            for threshold in (math.nextafter(tol, -math.inf), tol,
                              math.nextafter(tol, math.inf)):
                assert (distance.value <= threshold) == (tol <= threshold)
    # Invalid metadata must return before touching output; empty null is valid.
    out = C.c_double(123.)
    a = (C.c_double * 6)(1., 2., 3., 4., 5., 6.)
    for ptr, n, mode, target in [(None, 1, 2, C.byref(out)),
            (a, 1, 2, None), (a, 1, 7, C.byref(out)),
            (a, C.c_size_t(-1).value, 6, C.byref(out)),
            (C.cast(C.c_void_p(1), C.POINTER(C.c_double)), 1, 2, C.byref(out)),
            (a, sys.maxsize // 8 + 1, 2, C.byref(out))]:
        assert f(ptr, n, mode, target) == -1
        assert out.value == 123.
    assert f(None, 0, 3, None) == 0
    print(f"PASS {total} exact-bit norm/distance cases; ABI error checks; {sys.version}")
    print("helper SHA256", hashlib.sha256((ROOT / "native/pure_core/src/python_norm.rs").read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
