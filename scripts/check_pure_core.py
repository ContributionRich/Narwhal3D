#!/usr/bin/env python3
"""Mandatory source gate for the ``native/pure_core`` crate.

This is the fast, source-level gate that keeps the pure-core crate honest in
the working tree. It runs, in order:

1. ``cargo fmt --check`` — the committed source is rustfmt-clean.
2. ``cargo clippy --all-targets -- -D warnings`` — every lint is an error.
3. ``cargo test --offline --locked`` — the crate's own tests pass.
4. one Linux release build — the ``cdylib`` compiles for the shipped target.
5. a direct ctypes ABI probe — the built library answers the stable C ABI
   (version + a real encode) without going through the Python adapter.
6. the *dedicated* pure-core Python suites — adapters, route activation,
   compatibility paths, native-only parser and packaging contracts.

The Python suites are an **explicit manifest**, never a ``test_native_*.py``
glob: a glob would also capture the selection-core and CDR suites, which are a
different crate and a different agent's write set.

The gate builds its own Linux release library and **does not require a
committed retained artifact**. The Windows GNU ``cargo check`` runs only when
the pinned target is installed (no dual-platform reproducibility yet).

A mutation of the source must fail this gate. The self-test in
``spike/test_pure_core_gate.py`` proves that on a **temporary copied crate** —
never the live tree — and proves a skipped clippy step would fail the self-test.
"""
from __future__ import annotations

import argparse
import array
import ctypes
import json
import os
import shutil
import re
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CRATE = "native/pure_core"
ABI_VERSION = 1
RUST_TOOLCHAIN = "1.95.0"
LINUX_TARGET = "x86_64-unknown-linux-gnu"
WINDOWS_TARGET = "x86_64-pc-windows-gnu"
LIB_FILENAME = "libnarwhal_pure_core.so"

# The dedicated pure-core Python test manifest. An explicit list, deliberately
# not a glob: ``test_native_*.py`` would also match the selection-core and CDR
# suites, which belong to another crate and another agent's write set.
PURE_CORE_PYTESTS = (
    "spike/test_stl_writer.py",
    "spike/test_native_stl.py",
    "spike/test_obj_writer.py",
    "spike/test_obj_reader.py",
    "spike/test_native_obj.py",
    "spike/test_dxf_writer.py",
    "spike/test_dxf_reader.py",
    "spike/test_native_dxf.py",
    "spike/test_numparse.py",
    "spike/test_numparse_owned.py",
    "spike/test_native_numparse.py",
    "spike/test_pure_core_packaging.py",
    "spike/test_calc.py",
    "spike/test_native_calc.py",
    "spike/test_aci_colors.py",
    "spike/test_printwidths_contract.py",
    "spike/test_printwidths_owned.py",
    "spike/test_unitformat_contract.py",
    "spike/test_unitformat_owned.py",

    "spike/portfolio_review/test_metadata.py",
    "spike/test_bearing.py",
    "spike/test_native_bearing.py",
    "spike/test_coords.py",
    "spike/test_native_coords.py",
    "spike/test_native_pickconstraint.py",
    "spike/test_native_blend.py",
    "spike/test_macro.py",
    "spike/test_native_macro.py",
    "spike/test_macro_owned.py",
    "spike/test_macro_plan.py",
    "spike/test_native_macro_plan.py",
    "spike/test_svgpath.py",
    "spike/test_native_svgpath.py",
    "spike/test_vector_export.py",
    "spike/test_native_vector_export.py",
    "spike/test_native_command_search.py",
    "spike/test_native_search02_contract.py",
    "spike/test_native_search02_adapter.py",
    "spike/test_native_search02_ffi.py",
    "spike/test_native_search02_consumers.py",
    "spike/test_native_coil.py",
    "spike/test_native_intersect.py",
    "spike/test_pure_core_curve_hits_gate.py",
    "spike/test_native_coil_preview.py",
    "spike/test_preview_curve_native.py",
    "spike/test_native_tessellate.py",
    "spike/test_native_unit_table.py",
    "spike/test_native_nearest.py",
    "spike/test_pure_core_nearest_batch_gate.py",
    "spike/test_rebuild_performance.py",
    "spike/test_native_curve_samples.py",
    "spike/test_native_display_samples.py",
    "spike/test_native_rebuild_fit.py",
    "spike/test_native_arclength.py",
    "spike/test_native_interpolate.py",
    "spike/test_native_tween.py",
)


# Additive parser ABI: core ABI 1 and all its existing exports remain unchanged.
PURE_CORE_PARSE_EXPORTS = (
    "narwhal_pure_core_parse_abi", "narwhal_pure_core_parse_v2",
    "narwhal_pure_core_printwidths",
    "narwhal_pure_core_macro_size", "narwhal_pure_core_macro_encode",
    "narwhal_pure_core_unitformat_resolve_mode", "narwhal_pure_core_unitformat_format",
)
PURE_CORE_PARSE_IDENTITY = {
    "abi": 1, "parse_abi": 2, "reply_bytes": 24,
    "parser_exports": list(PURE_CORE_PARSE_EXPORTS),
}
# Packaging orchestration only: no Python grammar/fallback. Run the real C ABI
# in a bounded child so an incompatible library cannot crash the verifier host.
PURE_CORE_PARSE_PROBE = r'''
import ctypes as c, json, sys
lib = c.CDLL(sys.argv[1])
for symbol, expected in (("narwhal_pure_core_abi_version", 1),
                         ("narwhal_pure_core_parse_abi", 2)):
    fn = getattr(lib, symbol)
    fn.argtypes = []
    fn.restype = c.c_uint32
    if fn() != expected:
        raise RuntimeError(symbol + " ABI mismatch")
class Reply(c.Structure):
    _fields_ = [("code", c.c_int32), ("reserved", c.c_uint32),
                ("value", c.c_double), ("start", c.c_uint32), ("end", c.c_uint32)]
if c.sizeof(Reply) != 24:
    raise RuntimeError("pure-core parser Reply must be 24 bytes")
parse = lib.narwhal_pure_core_parse_v2
parse.argtypes = [c.POINTER(c.c_uint32), c.c_size_t, c.POINTER(c.c_uint32),
                  c.c_size_t, c.c_uint32, c.c_uint32, c.POINTER(Reply), c.c_size_t]
parse.restype = c.c_uint32
# Independent ABI smoke witnesses, not a second implementation of the parser.
for text, model, length, code, value in (("1/2", "", 0, 0, 0.5),
                                        ("2cm", "Millimeters", 1, 0, 20.0),
                                        ("bad", "", 0, -2, 0.0)):
    points = (c.c_uint32 * len(text))(*map(ord, text))
    units = (c.c_uint32 * len(model))(*map(ord, model))
    reply = Reply(99, 99, 99.0, 99, 99)
    status = parse(points, len(text), units, len(model), length, 160,
                   c.byref(reply), c.sizeof(reply))
    if status != 0 or reply.code != code or reply.reserved != 0 or reply.value != value:
        raise RuntimeError("pure-core parser semantic probe failed: " + repr(text))
macro_size = lib.narwhal_pure_core_macro_size
macro_size.argtypes = [c.c_char_p, c.c_size_t, c.c_ubyte]
macro_size.restype = c.c_int64
macro_encode = lib.narwhal_pure_core_macro_encode
macro_encode.argtypes = [c.c_char_p, c.c_size_t, c.c_ubyte, c.c_void_p, c.c_size_t]
macro_encode.restype = c.c_int32
for text, expected in ((b"!123", [{"kind": "text", "value": "!123"}]),
                       (b"a\x1cb", [{"kind": "text", "value": "a"}, {"kind": "text", "value": "b"}])):
    needed = macro_size(text, len(text), 0)
    if not 0 < needed <= 4096:
        raise RuntimeError("macro size witness differs")
    output = c.create_string_buffer(needed)
    if macro_encode(text, len(text), 0, output, needed) != 0 or json.loads(output.raw) != expected:
        raise RuntimeError("macro witness differs")
resolve = lib.narwhal_pure_core_unitformat_resolve_mode
resolve.argtypes = [c.c_char_p, c.c_size_t]
resolve.restype = c.c_int32
fmt = lib.narwhal_pure_core_unitformat_format
fmt.argtypes = [c.c_double, c.c_uint32, c.c_int64, c.c_uint32,
                c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t)]
fmt.restype = c.c_int32
if resolve(b" Fractional ", 12) != 1 or resolve(b"bogus", 5) != 13:
    raise RuntimeError("unit-format mode probe failed")
for value, mode, prec, ctx, expected in ((1.75, 1, 16, 28, b"1-3/4"),
                                        (0.0936, 1, 16, 2, b"1/8"),
                                        (30.5, 2, 16, 28, b"2'-6 1/2\"")):
    output, needed = c.create_string_buffer(4096), c.c_size_t(9999)
    if fmt(value, mode, prec, ctx, output, len(output), c.byref(needed)) != 0:
        raise RuntimeError("unit-format witness refused")
    if output.raw[:needed.value] != expected or needed.value != len(expected):
        raise RuntimeError("unit-format witness differs")
widths = lib.narwhal_pure_core_printwidths
widths.argtypes = [c.c_char_p, c.c_size_t, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t)]
widths.restype = c.c_int32
import struct
text = b"2\n1\n2"
output, needed = c.create_string_buffer(64), c.c_size_t()
if widths(text, len(text), output, len(output), c.byref(needed)) != 0 or output.raw[:needed.value] != struct.pack("<IddI", 2, 1.0, 2.0, 0):
    raise RuntimeError("print-widths witness differs")
print(json.dumps({"abi": 1, "parse_abi": 2, "reply_bytes": c.sizeof(Reply),
                  "parser_exports": ["narwhal_pure_core_parse_abi", "narwhal_pure_core_parse_v2", "narwhal_pure_core_printwidths", "narwhal_pure_core_macro_size", "narwhal_pure_core_macro_encode", "narwhal_pure_core_unitformat_resolve_mode", "narwhal_pure_core_unitformat_format"]}, sort_keys=True))
'''


def probe_pure_core_parser(path: Path, python: Path | str = sys.executable, *, env=None) -> dict:
    """Require the additive parser ABI and real number/length/rejection behavior."""
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"missing regular pure-core parser library: {path}")
    try:
        completed = subprocess.run(
            [str(python), "-I", "-B", "-c", PURE_CORE_PARSE_PROBE, str(path.resolve())],
            env=env, text=True, capture_output=True, check=True, timeout=30)
        identity = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError(f"cannot verify pure-core parser ABI/semantics: {getattr(error, 'stderr', '') or error}") from error
    if identity != PURE_CORE_PARSE_IDENTITY:
        raise ValueError(f"pure-core parser ABI/export evidence differs: {identity!r}")
    return identity


# Additive batch closest-point export. Qualified separately from the parser ABI above, so a
# library without it (or on a platform not yet qualified) loses only the batch route.
PURE_CORE_NEAREST_BATCH_EXPORT = "narwhal_pure_core_nearest_on_nurbs_many"
# The platforms whose artifact has passed this qualification. narwhal3d/native_nearest.py must
# carry the same set (spike/test_pure_core_nearest_batch_gate.py); Windows joins only with its own record.
PURE_CORE_BATCH_QUALIFIED_PLATFORMS = frozenset({"linux"})
# The digest pins the exact bits of the witness answers: a qualifying platform must produce
# the same answers as every other, not merely close ones.
PURE_CORE_NEAREST_BATCH_IDENTITY = {
    "single_export": "narwhal_pure_core_nearest_on_nurbs",
    "batch_export": PURE_CORE_NEAREST_BATCH_EXPORT,
    "targets": 5, "matches_single": True, "non_finite_target": "nan_filled",
    "null_out_status": -1, "bad_order_status": -2,
    "answers_sha256": "e03299994401c7cb1e53f9dd071c429b470c34aed4b7493f405922c67cb2a35b",
}
PURE_CORE_NEAREST_BATCH_PROBE = r'''
import ctypes as c, hashlib, json, math, struct, sys
lib = c.CDLL(sys.argv[1])
D, U = c.POINTER(c.c_double), c.POINTER(c.c_ubyte)
single = lib.narwhal_pure_core_nearest_on_nurbs
batch = lib.narwhal_pure_core_nearest_on_nurbs_many
single.argtypes = [c.c_size_t, D, c.c_size_t, D, c.c_size_t, c.c_ubyte, c.c_ubyte, D, D, U]
single.restype = c.c_int32
batch.argtypes = [c.c_size_t, D, c.c_size_t, D, c.c_size_t, c.c_ubyte, c.c_ubyte, D, c.c_size_t, D, U]
batch.restype = c.c_int32
# One clamped cubic Bezier span: order 4, 4 homogeneous control points, 6 knots.
knots = (c.c_double * 6)(0, 0, 0, 1, 1, 1)
cvs = (c.c_double * 16)(0, 0, 0, 1, 1, 0, 0, 1, 0, 1, 0, 1, 1, 1, 0, 1)
targets = [(0.5, 0.2, 0.1), (2.0, 3.0, 4.0), (float("nan"), 0.0, 0.0), (-1.0, 0.0, 0.5), (0.5, 0.5, 0.5)]
count = len(targets)
flat = (c.c_double * (3 * count))(*[v for t in targets for v in t])
out = (c.c_double * (5 * count))()
ends = (c.c_ubyte * count)()
if batch(4, knots, 6, cvs, 4, 0, 0, flat, count, out, ends) != 0:
    raise RuntimeError("batch export refused the witness")
for i, target in enumerate(targets):
    slot = [out[5 * i + k] for k in range(5)]
    if any(math.isnan(v) for v in target):
        if not all(math.isnan(v) for v in slot) or ends[i] != 0:
            raise RuntimeError("non-finite target must be NaN-filled with end flag 0")
        if single(4, knots, 6, cvs, 4, 0, 0, (c.c_double * 3)(*target), (c.c_double * 5)(), c.byref(c.c_ubyte())) == 0:
            raise RuntimeError("single export accepted a non-finite target")
        continue
    one, end = (c.c_double * 5)(), c.c_ubyte()
    if single(4, knots, 6, cvs, 4, 0, 0, (c.c_double * 3)(*target), one, c.byref(end)) != 0:
        raise RuntimeError("single export refused a witness target")
    if struct.pack("<5d", *one) != struct.pack("<5d", *slot) or end.value != ends[i]:
        raise RuntimeError("batch answer differs from the single export")
if batch(4, knots, 6, cvs, 4, 0, 0, flat, 1, None, None) != -1:
    raise RuntimeError("null output must be ERR_NULL")
if batch(33, knots, 6, cvs, 4, 0, 0, flat, 1, out, ends) != -2:
    raise RuntimeError("order 33 must be ERR_INPUT")
digest = hashlib.sha256(struct.pack("<%dd" % len(out), *out) + bytes(ends)).hexdigest()
print(json.dumps({"single_export": "narwhal_pure_core_nearest_on_nurbs",
                  "batch_export": "narwhal_pure_core_nearest_on_nurbs_many", "targets": count,
                  "matches_single": True, "non_finite_target": "nan_filled",
                  "null_out_status": -1, "bad_order_status": -2, "answers_sha256": digest}, sort_keys=True))
'''


def probe_pure_core_nearest_batch(path: Path, python: Path | str = sys.executable, *, env=None) -> dict:
    """Require the batch export to answer exactly as the single export does, bit for bit."""
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"missing regular pure-core library: {path}")
    try:
        completed = subprocess.run(
            [str(python), "-I", "-B", "-c", PURE_CORE_NEAREST_BATCH_PROBE, str(path.resolve())],
            env=env, text=True, capture_output=True, check=True, timeout=30)
        identity = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError(f"cannot verify pure-core batch nearest export: {getattr(error, 'stderr', '') or error}") from error
    if identity != PURE_CORE_NEAREST_BATCH_IDENTITY:
        raise ValueError(f"pure-core batch nearest evidence differs: {identity!r}")
    return identity


# Additive curve/curve intersection export (intersect.curve_curve's recursion), qualified like
# the batch export above: a library without it, or a platform not yet qualified, keeps the
# Python recursion.
PURE_CORE_CURVE_HITS_EXPORT = "narwhal_pure_core_curve_curve_hits"
# narwhal3d/native_intersect.py must carry the same set (spike/test_pure_core_curve_hits_gate.py).
# The export reaches libm only through sqrt and fma; Windows joins with its own record.
PURE_CORE_CURVE_HITS_QUALIFIED_PLATFORMS = frozenset({"linux"})
# The digest pins the exact bits of every witness hit; it is also the digest of the Python
# recursion's hits (`_dedupe(_subdivide(...))`) on the same curves.
PURE_CORE_CURVE_HITS_IDENTITY = {
    "export": PURE_CORE_CURVE_HITS_EXPORT,
    "hits": [1, 2, 1], "small_buffer_status": -3, "empty_domain_status": -2,
    "deep_request_status": -2, "null_count_status": -1,
    "hits_sha256": "2026c8b8c27d50195edbca80fdcf337725e50254662e0dd219112c0add05c8c0",
}
PURE_CORE_CURVE_HITS_PROBE = r'''
import ctypes as c, hashlib, json, struct, sys
fn = c.CDLL(sys.argv[1]).narwhal_pure_core_curve_curve_hits
D, Z = c.POINTER(c.c_double), c.c_size_t
curve = [Z, D, Z, D, Z, c.c_ubyte]
fn.argtypes = curve + curve + [c.c_double, Z, D, Z, c.POINTER(Z)]
fn.restype = c.c_int32
def arr(values): return (c.c_double * len(values))(*values)
def nurbs(order, knots, cvs, rational=0):
    return (order, arr(knots), len(knots), arr([float(v) for v in cvs]), len(cvs) // 4, rational)
# Crossing lines; a cubic Bezier crossing a horizontal line twice; a rational quadratic arc
# (a quarter circle of radius 2) crossed once by the line y = x. Control points are homogeneous.
w = 0.5 ** 0.5
pairs = [
    (nurbs(2, [0, 1], [0, -1, 0, 1, 0, 1, 0, 1]), nurbs(2, [0, 1], [-1, 0, 0, 1, 1, 0, 0, 1])),
    (nurbs(4, [0, 0, 0, 1, 1, 1], [0, 0, 0, 1, 2, 4, 0, 1, 4, -4, 0, 1, 6, 0, 0, 1]),
     nurbs(2, [0, 1], [-1, 0.5, 0, 1, 7, 0.5, 0, 1])),
    (nurbs(3, [0, 0, 1, 1], [2, 0, 0, 1, 2 * w, 2 * w, 0, w, 0, 2, 0, 1], 1),
     nurbs(2, [0, 1], [0, 0, 0, 1, 2, 2, 0, 1])),
]
counts, bits = [], b""
for a, b in pairs:
    out, n = (c.c_double * 50)(), Z()
    if fn(*a, *b, 1e-6, 40, out, 10, c.byref(n)) != 0:
        raise RuntimeError("the export refused a witness pair")
    counts.append(n.value)
    bits += struct.pack("<%dd" % (5 * n.value), *out[:5 * n.value])
a, b = pairs[1]
n = Z()
small = fn(*a, *b, 1e-6, 40, (c.c_double * 5)(), 1, c.byref(n))
if n.value != 2:
    raise RuntimeError("a full buffer must still report the hit count")
empty = nurbs(2, [1, 1], [0, -1, 0, 1, 0, 1, 0, 1])
print(json.dumps({"export": "narwhal_pure_core_curve_curve_hits", "hits": counts,
                  "small_buffer_status": small,
                  "empty_domain_status": fn(*empty, *b, 1e-6, 40, (c.c_double * 50)(), 10, c.byref(n)),
                  "deep_request_status": fn(*a, *b, 1e-6, 65, (c.c_double * 50)(), 10, c.byref(n)),
                  "null_count_status": fn(*a, *b, 1e-6, 40, (c.c_double * 50)(), 10, None),
                  "hits_sha256": hashlib.sha256(bits).hexdigest()}, sort_keys=True))
'''


def probe_pure_core_curve_hits(path: Path, python: Path | str = sys.executable, *, env=None) -> dict:
    """Require the curve/curve export to give the pinned witness hits, bit for bit."""
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"missing regular pure-core library: {path}")
    try:
        completed = subprocess.run(
            [str(python), "-I", "-B", "-c", PURE_CORE_CURVE_HITS_PROBE, str(path.resolve())],
            env=env, text=True, capture_output=True, check=True, timeout=30)
        identity = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError(f"cannot verify pure-core curve/curve export: {getattr(error, 'stderr', '') or error}") from error
    if identity != PURE_CORE_CURVE_HITS_IDENTITY:
        raise ValueError(f"pure-core curve/curve evidence differs: {identity!r}")
    return identity


# Additive unit-aware SVG export (vector_export.write_svg(unit=...)): ABI 1 and the legacy
# narwhal_pure_core_svg_encode_into are unchanged, so a library without it loses only the
# native route for unit SVGs. Formatting is IEEE division + Rust's exact decimal printing, no
# libm, so no per-platform qualification set is kept (the same posture as the legacy SVG).
PURE_CORE_SVG_UNIT_EXPORT = "narwhal_pure_core_svg_encode_unit_into"
# The digest pins the exact bytes of the inch and millimetre witness documents; it is also
# the digest of the Python writer (`vector_export._write_svg_in_unit`) on the same strokes.
PURE_CORE_SVG_UNIT_IDENTITY = {
    "export": PURE_CORE_SVG_UNIT_EXPORT,
    "legacy_identical": True, "inch_header": True,
    "bad_suffix_status": -2, "bad_digits_status": -2, "bad_scale_status": -2,
    "small_buffer_status": -3, "null_written_status": -1,
    "documents_sha256": "4e117558b35790976103f235f7c5d12a141b1c4e190ee12f45001f283f250c97",
}
PURE_CORE_SVG_UNIT_PROBE = r'''
import ctypes as c, hashlib, json, sys
lib = c.CDLL(sys.argv[1])
D, Q, I, B = c.POINTER(c.c_double), c.POINTER(c.c_int64), c.POINTER(c.c_int32), c.POINTER(c.c_ubyte)
strokes = c.c_int64
head = [D, c.c_int64, Q, I, D, D, B, Q, c.c_double, c.c_double, c.c_int64]
legacy = lib.narwhal_pure_core_svg_encode_into
legacy.argtypes = head + [B, c.c_size_t, Q]
legacy.restype = c.c_int32
unit = lib.narwhal_pure_core_svg_encode_unit_into
unit.argtypes = head + [B, c.c_int64, c.c_double, c.c_int32, B, c.c_size_t, Q]
unit.restype = c.c_int32
# Two strokes: a closed cubic on layer "cut" and an open one with no layer, on Letter landscape.
bez = (c.c_double * 16)(0, 0, 25.4, 50.8, 50.8, 50.8, 76.2, 0,
                        10, 20, 30, 40, 50, 60, 70, 80)
segs, closed = (c.c_int64 * 2)(1, 1), (c.c_int32 * 2)(1, 0)
rgb, widths = (c.c_double * 6)(1, 0, 0, 0.1, 0.2, 0.3), (c.c_double * 2)(0.254, 0.13)
layers, lens = (c.c_ubyte * 3)(*b"cut"), (c.c_int64 * 2)(3, 0)
args = [bez, 2, segs, closed, rgb, widths, layers, lens, 279.4, 215.9, 2]
def run(suffix, mm, digits, cap=1 << 16, written=True):
    out, n = (c.c_ubyte * cap)(), c.c_int64(-1)
    s = (c.c_ubyte * max(len(suffix), 1))(*(suffix or b"\0"))
    rc = unit(*args, s, len(suffix), mm, digits, out, cap, c.byref(n) if written else None)
    return rc, bytes(out[:max(n.value, 0)])
out, n = (c.c_ubyte * (1 << 16))(), c.c_int64(-1)
if legacy(*args, out, 1 << 16, c.byref(n)) != 0:
    raise RuntimeError("the legacy SVG export refused the witness")
old = bytes(out[:n.value])
rc_mm, plain = run(b"", 1.0, 4)
rc_in, inch = run(b"in", 25.4, 6)
rc_m, mm = run(b"mm", 1.0, 4)
if (rc_mm, rc_in, rc_m) != (0, 0, 0):
    raise RuntimeError("the unit SVG export refused a witness unit")
print(json.dumps({"export": "narwhal_pure_core_svg_encode_unit_into",
                  "legacy_identical": plain == old,
                  "inch_header": b'width="11.000000in" height="8.500000in" viewBox="0 0 11.000000 8.500000"' in inch,
                  "bad_suffix_status": run(b"IN", 25.4, 6)[0],
                  "bad_digits_status": run(b"in", 25.4, 13)[0],
                  "bad_scale_status": run(b"in", 0.0, 6)[0],
                  "small_buffer_status": run(b"in", 25.4, 6, cap=16)[0],
                  "null_written_status": run(b"in", 25.4, 6, written=False)[0],
                  "documents_sha256": hashlib.sha256(inch + b"\0" + mm).hexdigest()},
                 sort_keys=True))
'''


def probe_pure_core_svg_unit(path: Path, python: Path | str = sys.executable, *, env=None) -> dict:
    """Require the unit SVG export to write the pinned witness bytes and refusals."""
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"missing regular pure-core library: {path}")
    try:
        completed = subprocess.run(
            [str(python), "-I", "-B", "-c", PURE_CORE_SVG_UNIT_PROBE, str(path.resolve())],
            env=env, text=True, capture_output=True, check=True, timeout=30)
        identity = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError(f"cannot verify pure-core unit SVG export: {getattr(error, 'stderr', '') or error}") from error
    if identity != PURE_CORE_SVG_UNIT_IDENTITY:
        raise ValueError(f"pure-core unit SVG evidence differs: {identity!r}")
    return identity


class GateError(RuntimeError):
    """A mandatory pure-core source check failed."""


def _run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> tuple[int, str]:
    """Run *cmd* and return ``(returncode, combined_output)`` without raising."""
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
    except OSError as exc:
        return 127, f"cannot run {cmd[0]!r}: {exc}"
    return proc.returncode, proc.stdout


def _cargo(*args: str, crate: Path) -> list[str]:
    return ["cargo", f"+{RUST_TOOLCHAIN}", *args,
            "--manifest-path", str(crate / "Cargo.toml")]


def _bundled_python() -> str:
    """The CPython the app ships (matches ``run_tests.sh``); fall back to the
    interpreter running the gate. The pure-core suites need only the stdlib."""
    sys.path.insert(0, str(ROOT / "spike"))
    import runtime_paths
    candidate = runtime_paths.python(ROOT)
    return str(candidate) if candidate.is_file() else sys.executable


def _release_lib(crate: Path) -> Path:
    return (crate / "target" / LINUX_TARGET / "release" / LIB_FILENAME)


# ---------------------------------------------------------------------------
# Individual steps. Each returns a short status dict for the report.
# ---------------------------------------------------------------------------

def step_fmt(crate: Path) -> dict[str, object]:
    rc, out = _run(_cargo("fmt", "--check", crate=crate), ROOT)
    if rc != 0:
        raise GateError(f"cargo fmt --check failed:\n{out.strip()}")
    return {"status": "ok", "command": "cargo fmt --check"}


def step_clippy(crate: Path) -> dict[str, object]:
    # -D warnings is forwarded to the compiler via ``--``.
    rc, out = _run(
        _cargo("clippy", "--all-targets", "--locked", "--offline", crate=crate)
        + ["--", "-D", "warnings"],
        ROOT,
    )
    if rc != 0:
        raise GateError(f"cargo clippy -D warnings failed:\n{out.strip()}")
    # The self-test asserts this ran (a skipped clippy would not reach here).
    return {"status": "ok", "ran": True, "command": "cargo clippy -D warnings"}


def step_tests(crate: Path) -> dict[str, object]:
    rc, out = _run(_cargo("test", "--offline", "--locked", crate=crate), ROOT)
    if rc != 0:
        raise GateError(f"cargo test failed:\n{out.strip()}")
    match = re.search(r"test result: ok\.\s+(\d+) passed", out)
    if match is None:
        raise GateError("cargo test ran but reported no passing tests")
    return {"status": "ok", "passed": int(match.group(1)), "command": "cargo test"}


def step_release_build(crate: Path) -> dict[str, object]:
    rc, out = _run(
        _cargo("build", "--release", "--locked", "--offline",
               "--target", LINUX_TARGET, crate=crate),
        ROOT,
    )
    if rc != 0:
        raise GateError(f"cargo build --release (Linux) failed:\n{out.strip()}")
    lib = _release_lib(crate)
    if not lib.is_file():
        raise GateError(f"release build produced no library at {lib}")
    return {"status": "ok", "artifact": str(lib), "command": "cargo build --release"}


def _probe_sample_nurbs(lib) -> str:
    """Known answers from `narwhal_pure_core_sample_nurbs`: a degree-1 segment from
    (0, 0, 0) to (2, 0, 0) sampled at quarter parameters, and zero segments refused."""
    name = "narwhal_pure_core_sample_nurbs"
    D = ctypes.POINTER(ctypes.c_double)
    fn = getattr(lib, name)
    fn.argtypes = [ctypes.c_size_t, D, ctypes.c_size_t, D, ctypes.c_size_t, ctypes.c_ubyte,
                   ctypes.c_double, ctypes.c_double, ctypes.c_size_t, D, ctypes.c_size_t]
    fn.restype = ctypes.c_int32
    knots = (ctypes.c_double * 2)(0.0, 1.0)
    cvs = (ctypes.c_double * 8)(0.0, 0.0, 0.0, 1.0, 2.0, 0.0, 0.0, 1.0)
    out = (ctypes.c_double * 15)()
    if fn(2, knots, 2, cvs, 2, 0, 0.0, 1.0, 4, out, 15) != 0:
        raise GateError(f"{name}: sampling a segment failed")
    if [out[3 * i] for i in range(5)] != [0.0, 0.5, 1.0, 1.5, 2.0]:
        raise GateError(f"{name}: the segment's samples are wrong ({list(out)})")
    if fn(2, knots, 2, cvs, 2, 0, 0.0, 1.0, 0, out, 15) == 0:
        raise GateError(f"{name}: zero segments were not refused")
    return name


def _probe_curve_display_samples(lib) -> str:
    """Known answers from `narwhal_pure_core_curve_display_samples` that hold on every
    libm: a straight cubic is its two end points, and a short buffer reports the count."""
    name = "narwhal_pure_core_curve_display_samples"
    D = ctypes.POINTER(ctypes.c_double)
    fn = getattr(lib, name)
    fn.argtypes = [ctypes.c_size_t, D, ctypes.c_size_t, D, ctypes.c_size_t, ctypes.c_ubyte,
                   ctypes.c_double, ctypes.c_double, ctypes.c_double, D, D, ctypes.c_size_t,
                   ctypes.POINTER(ctypes.c_size_t)]
    fn.restype = ctypes.c_int32
    knots = (ctypes.c_double * 6)(0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
    cvs = (ctypes.c_double * 16)(*[c for i in range(4) for c in (float(i), 0.0, 0.0, 1.0)])
    params, points, count = (ctypes.c_double * 8)(), (ctypes.c_double * 24)(), ctypes.c_size_t()
    if fn(4, knots, 6, cvs, 4, 0, 0.0, 1.0, 0.01, params, points, 8, ctypes.byref(count)) != 0:
        raise GateError(f"{name}: a straight cubic failed")
    if count.value != 2 or list(params[:2]) != [0.0, 1.0] or list(points[:6]) != [0.0, 0.0, 0.0, 3.0, 0.0, 0.0]:
        raise GateError(f"{name}: a straight cubic is not its two end points")
    if fn(4, knots, 6, cvs, 4, 0, 0.0, 1.0, 0.01, params, points, 1, ctypes.byref(count)) != -3 \
            or count.value != 2:
        raise GateError(f"{name}: a short buffer did not report the vertex count")
    return name


def _probe_rebuild_fit(lib) -> str:
    """Known answers from `narwhal_pure_core_rebuild_fit`: a line is fitted exactly, and
    a closed degree-5 fit of 3 points and an over-bound fit are refused, not crashed."""
    name = "narwhal_pure_core_rebuild_fit"
    D = ctypes.POINTER(ctypes.c_double)
    fn = getattr(lib, name)
    fn.argtypes = [D, D, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_ubyte,
                   ctypes.c_ubyte, D, D, D, ctypes.POINTER(ctypes.c_size_t)]
    fn.restype = ctypes.c_int32
    params = [i / 20 for i in range(21)]
    pts = (ctypes.c_double * 63)(*[c for u in params for c in (3.0 * u, 0.0, 0.0)])
    prm = (ctypes.c_double * 21)(*params)
    ends = (ctypes.c_double * 6)(0.0, 0.0, 0.0, 3.0, 0.0, 0.0)
    ctrl, knots, count = (ctypes.c_double * 24)(), (ctypes.c_double * 10)(), ctypes.c_size_t()
    if fn(pts, prm, 21, 4, 3, 0, 0, ends, ctrl, knots, ctypes.byref(count)) != 0 or count.value != 4:
        raise GateError(f"{name}: the line fit failed")
    if any(abs(ctrl[3 * i] - i) > 1e-12 for i in range(4)):
        raise GateError(f"{name}: the line fit's control points are wrong")
    if fn(pts, prm, 21, 3, 5, 1, 0, ends, ctrl, knots, ctypes.byref(count)) != -2:
        raise GateError(f"{name}: a closed fit with degree above point count was not refused")
    if fn(pts, prm, 21, 1366, 3, 1, 0, ends, None, None, ctypes.byref(count)) != -5:
        raise GateError(f"{name}: an over-bound fit was not refused before allocating")
    return name


def _probe_parameters_at_lengths(lib) -> str:
    """Known answers from `narwhal_pure_core_parameters_at_lengths`: on a cubic that runs
    along x at unit speed, length s is parameter s, and an out-of-range length is
    handed back to Python."""
    name = "narwhal_pure_core_parameters_at_lengths"
    D = ctypes.POINTER(ctypes.c_double)
    fn = getattr(lib, name)
    fn.argtypes = [ctypes.c_size_t, D, ctypes.c_size_t, D, ctypes.c_size_t, ctypes.c_ubyte,
                   ctypes.c_double, ctypes.c_double, D, ctypes.c_size_t, D, ctypes.c_double,
                   D, D, ctypes.c_size_t, ctypes.c_double, D, ctypes.c_size_t, D,
                   ctypes.POINTER(ctypes.c_ubyte)]
    fn.restype = ctypes.c_int32
    knots = (ctypes.c_double * 7)(0.0, 0.0, 0.0, 1.0, 2.0, 2.0, 2.0)
    xs = (0.0, 1.0 / 3.0, 1.0, 5.0 / 3.0, 2.0)
    cvs = (ctypes.c_double * 20)(*[c for x in xs for c in (x, 0.0, 0.0, 1.0)])
    node = 1.0 / 3.0 ** 0.5
    nodes, weights = (ctypes.c_double * 2)(-node, node), (ctypes.c_double * 2)(1.0, 1.0)
    table, cum = (ctypes.c_double * 1)(1.0), (ctypes.c_double * 3)(0.0, 1.0, 2.0)
    lengths = (ctypes.c_double * 3)(0.5, 1.25, 9.0)
    out, status = (ctypes.c_double * 3)(), (ctypes.c_ubyte * 3)()
    code = fn(4, knots, 7, cvs, 5, 0, 0.0, 2.0, table, 1, cum, 2.0, nodes, weights, 2,
              1e-9, lengths, 3, out, status)
    if code != 0 or list(status) != [0, 0, 1]:
        raise GateError(f"{name}: unexpected status {code} {list(status)}")
    if abs(out[0] - 0.5) > 1e-9 or abs(out[1] - 1.25) > 1e-9:
        raise GateError(f"{name}: unit-speed lengths are not their parameters ({out[0]}, {out[1]})")
    return name


def step_direct_abi(lib_path: Path) -> dict[str, object]:
    """Load the built library and exercise both stable and additive C ABIs."""
    try:
        parser_identity = probe_pure_core_parser(lib_path)
    except ValueError as exc:
        raise GateError(str(exc)) from exc
    try:
        lib = ctypes.CDLL(str(lib_path))
        version = lib.narwhal_pure_core_abi_version
        version.argtypes = []
        version.restype = ctypes.c_uint32
        if int(version()) != ABI_VERSION:
            raise GateError(f"ABI version is {version()}, expected {ABI_VERSION}")

        size = lib.narwhal_pure_core_stl_size
        size.argtypes = [
            ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t, ctypes.c_ubyte,
        ]
        size.restype = ctypes.c_int64
        enc = lib.narwhal_pure_core_stl_encode
        enc.argtypes = [
            ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t, ctypes.c_ubyte,
            ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
        ]
        enc.restype = ctypes.c_int32
    except (AttributeError, OSError) as exc:
        raise GateError(f"cannot bind the pure-core ABI: {exc}") from exc

    # One binary STL facet: 84-byte header + 50-byte record.
    flat = array.array("d", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0])
    coords = (ctypes.c_double * 9).from_buffer(flat)
    name = b"Narwhal 3D"
    name_arr = (ctypes.c_ubyte * len(name))(*name)
    nbytes = int(size(coords, 1, name_arr, len(name), 1))
    if nbytes != 84 + 50:
        raise GateError(f"direct ABI size query returned {nbytes}, expected 134")
    buf = (ctypes.c_ubyte * nbytes)()
    if int(enc(coords, 1, name_arr, len(name), 1, buf, nbytes)) != 0:
        raise GateError("direct ABI encode returned a non-zero status")
    body = bytes(buf)
    if struct.unpack_from("<I", body, 80)[0] != 1:
        raise GateError("direct ABI encode: triangle count is not 1")
    if body[:80].strip().decode("latin-1") != "Narwhal 3D":
        raise GateError("direct ABI encode: header name is wrong")

    # A NaN coordinate must be a domain rejection, not a silent encode.
    bad = array.array("d", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, float("nan"), 1.0, 0.0])
    bad_ptr = (ctypes.c_double * 9).from_buffer(bad)
    if int(size(bad_ptr, 1, name_arr, len(name), 1)) >= 0:
        raise GateError("direct ABI size query accepted a NaN coordinate")
    try:
        geometry = [_probe_sample_nurbs(lib), _probe_curve_display_samples(lib),
                    _probe_rebuild_fit(lib), _probe_parameters_at_lengths(lib)]
    except (AttributeError, OSError) as exc:
        raise GateError(f"cannot bind a pure-core geometry export: {exc}") from exc
    return {"status": "ok", "abi_version": ABI_VERSION, "parser": parser_identity,
                "geometry_exports": geometry, "command": "ctypes ABI probe"}


def step_nearest_batch(lib_path: Path) -> dict[str, object]:
    """Qualify the batch closest-point export; required for the library to carry it."""
    try:
        identity = probe_pure_core_nearest_batch(lib_path)
    except ValueError as exc:
        raise GateError(str(exc)) from exc
    return {"status": "ok", "nearest_batch": identity, "command": "ctypes batch probe"}


def step_curve_hits(lib_path: Path) -> dict[str, object]:
    """Qualify the curve/curve intersection export; required for the library to carry it."""
    try:
        identity = probe_pure_core_curve_hits(lib_path)
    except ValueError as exc:
        raise GateError(str(exc)) from exc
    return {"status": "ok", "curve_hits": identity, "command": "ctypes curve/curve probe"}


def step_svg_unit(lib_path: Path) -> dict[str, object]:
    """Qualify the unit-aware SVG export; required for the library to carry it."""
    try:
        identity = probe_pure_core_svg_unit(lib_path)
    except ValueError as exc:
        raise GateError(str(exc)) from exc
    return {"status": "ok", "svg_unit": identity, "command": "ctypes unit SVG probe"}


def step_adapter(crate: Path, lib_path: Path) -> dict[str, object]:
    """Run the dedicated pure-core Python suites against the built library."""
    python = _bundled_python()
    env = {**os.environ, "NARWHAL_PURE_CORE": str(lib_path),
           "PYTHONDONTWRITEBYTECODE": "1"}
    for suite in PURE_CORE_PYTESTS:
        suite_path = ROOT / suite
        if not suite_path.is_file():
            raise GateError(f"missing pure-core suite {suite}")
        command = [python, suite]
        if suite in ('spike/test_native_search02_ffi.py', 'spike/test_native_search02_consumers.py'):
            command += ['--library', str(lib_path)]
        rc, out = _run(command, ROOT, env)
        if rc != 0:
            raise GateError(f"pure-core suite {suite} failed:\n{out.strip()}")
    return {"status": "ok", "suites": list(PURE_CORE_PYTESTS), "python": python}


def _windows_target_installed() -> bool | None:
    """True/False when rustup can tell us; None when rustup is unavailable."""
    if not shutil.which("rustup"):
        return None
    rc, out = _run(
        ["rustup", "target", "list", "--toolchain", RUST_TOOLCHAIN, "--installed"],
        ROOT,
    )
    if rc != 0:
        return None
    return WINDOWS_TARGET in out.split()


def step_windows_check(crate: Path) -> dict[str, object]:
    installed = _windows_target_installed()
    if installed is False:
        return {"status": "skipped", "reason": "Windows GNU target not installed"}
    rc, out = _run(
        _cargo("check", "--locked", "--offline",
               "--target", WINDOWS_TARGET, crate=crate),
        ROOT,
    )
    if rc != 0:
        # If rustup could not report the target (installed is None) and the
        # failure is an "install the target" diagnostic, treat it as skipped.
        if installed is None and (
            "target not installed" in out or "component not installed" in out
        ):
            return {"status": "skipped", "reason": "Windows GNU target not installed"}
        raise GateError(f"cargo check (Windows GNU) failed:\n{out.strip()}")
    return {"status": "ok", "command": "cargo check --target x86_64-pc-windows-gnu"}


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run_gate(crate_rel: str) -> dict[str, object]:
    crate = (ROOT / crate_rel).resolve()
    if not (crate / "Cargo.toml").is_file():
        raise GateError(f"not a pure-core crate (no Cargo.toml): {crate}")
    if not shutil.which("cargo"):
        raise GateError("cargo is not on PATH; the pure-core source gate cannot run")

    steps: dict[str, object] = {}
    steps["fmt"] = step_fmt(crate)
    clippy = step_clippy(crate)
    # A skipped or absent clippy step must fail the gate: it is the only step
    # that turns lints into errors, so a silent skip would let a regression
    # through. The self-test exercises this by stubbing the step to "skipped".
    if clippy.get("status") != "ok" or clippy.get("ran") is not True:
        raise GateError("clippy did not run with -D warnings; a skipped clippy "
                        "must fail the gate")
    steps["clippy"] = clippy
    steps["tests"] = step_tests(crate)
    steps["release_build"] = step_release_build(crate)
    lib = _release_lib(crate)
    steps["direct_abi"] = step_direct_abi(lib)
    steps["nearest_batch"] = step_nearest_batch(lib)
    steps["curve_hits"] = step_curve_hits(lib)
    steps["svg_unit"] = step_svg_unit(lib)
    steps["adapter"] = step_adapter(crate, lib)
    steps["windows_check"] = step_windows_check(crate)

    report = {
        "gate": "pure-core-source",
        "crate": str(crate),
        "abi_version": ABI_VERSION,
        "rust_toolchain": RUST_TOOLCHAIN,
        "python_manifest": list(PURE_CORE_PYTESTS),
        "steps": steps,
        "ok": True,
    }
    return report


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crate", default=DEFAULT_CRATE,
                        help="path to the pure-core crate (default: %(default)s)")
    parser.add_argument("--json", action="store_true",
                        help="emit a machine-readable JSON report")
    args = parser.parse_args(argv)

    try:
        report = run_gate(args.crate)
    except GateError as exc:
        if args.json:
            print(json.dumps({"gate": "pure-core-source", "ok": False,
                              "error": str(exc)}, indent=2))
        else:
            print(f"pure-core source gate FAILED:\n{exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"pure-core source gate: OK (crate {report['crate']})")
        for name in ("fmt", "clippy", "tests", "release_build",
                     "direct_abi", "nearest_batch", "curve_hits", "svg_unit", "adapter",
                     "windows_check"):
            step = report["steps"][name]
            detail = step.get("passed", step.get("suites", step.get("reason", "")))
            print(f"  [{name}] {step['status']}"
                  + (f"  -- {detail}" if detail else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
