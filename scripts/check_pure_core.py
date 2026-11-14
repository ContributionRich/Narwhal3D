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
   optional compatibility paths where retained, mandatory native-only ownership
   routes, and packaging contracts (including stable core-ABI-1 rebuild/blend).

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
import ast
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
    "spike/test_calc_owned.py",
    "spike/test_native_parser_cache.py",
    "spike/test_aci_colors.py",
    "spike/test_printwidths_contract.py",
    "spike/test_printwidths_owned.py",
    "spike/test_unitformat_contract.py",
    "spike/test_unitformat_owned.py",

    "spike/portfolio_review/test_metadata.py",
    "spike/test_bearing.py",
    "spike/test_native_bearing.py",
    "spike/test_bearing_owned.py",
    "spike/test_coords.py",
    "spike/test_native_coords.py",
    "spike/test_coords_owned.py", "spike/test_native_joinchain.py",
    "spike/test_joinchain_owned.py",
    "spike/test_native_pickconstraint.py",
    "spike/test_native_blend.py",
    "spike/test_macro.py",
    "spike/test_native_macro.py",
    "spike/test_macro_owned.py",
    "spike/test_macro_plan.py",
    "spike/test_native_macro_plan.py",
    "spike/test_macro_plan_owned.py",
    "spike/test_svgpath.py",
    "spike/test_native_svgpath.py",
    "spike/test_svgpath_owned.py", "spike/test_alias_owned.py",
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
    "spike/test_unit_table_owned.py", "spike/test_unit_ops_owned.py",
    "spike/test_native_nearest.py",
    "spike/test_pure_core_nearest_batch_gate.py",
    "spike/test_rebuild_performance.py",
    "spike/test_native_curve_samples.py",
    "spike/test_native_display_samples.py",
    "spike/test_native_rebuild_fit.py", "spike/test_rebuild_owned.py",
    "spike/test_native_arclength.py",
    "spike/test_native_interpolate.py",
    "spike/test_native_tween.py",
)


# Explicit Rust oracle-retirement suites and their source/fixture closure.
RUST_INTEGRATION_SUITES = ('alias_golden', 'unit_golden', 'interpolate_golden', 'svgpath_golden', 'bearing_golden', 'calc_golden', 'coords_golden', 'unitformat_golden', 'macro_golden', 'joinchain_golden', 'rebuild_golden', 'blend_golden', 'norm_golden', 'nurbs_golden', 'arclength_golden')
RUST_INTEGRATION_FILES = (
    'native/pure_core/tests/nurbs_golden.rs',
    'native/pure_core/tests/arclength_golden.rs',
    'native/pure_core/tests/fixtures/nurbs_curves.txt',
    'native/pure_core/tests/fixtures/nurbs_nearest.txt',
    'native/pure_core/tests/fixtures/nurbs_sample.txt',
    'native/pure_core/tests/fixtures/nurbs_display.txt',
    'native/pure_core/tests/fixtures/nurbs_host.txt',
    'native/pure_core/tests/fixtures/nurbs_provenance.txt',
    'native/pure_core/tests/fixtures/arclength_curves.txt',
    'native/pure_core/tests/fixtures/arclength_golden.txt',
    'native/pure_core/tests/blend_golden.rs',
    'native/pure_core/tests/norm_golden.rs',
    'native/pure_core/tests/fixtures/blend_numeric.txt',
    'native/pure_core/tests/fixtures/blend_curves.txt',
    'native/pure_core/tests/fixtures/blend_provenance.txt',
    'native/pure_core/tests/fixtures/norm_rows_2.txt',
    'native/pure_core/tests/fixtures/norm_rows_3.txt',
    'native/pure_core/tests/fixtures/norm_rows_6.txt',
    'native/pure_core/tests/fixtures/norm_thresholds.txt',
    'native/pure_core/tests/fixtures/norm_witness.txt',
    'native/pure_core/tests/alias_golden.rs',
    'native/pure_core/tests/bearing_golden.rs',
    'native/pure_core/tests/calc_golden.rs',
    'native/pure_core/tests/coords_golden.rs',
    'native/pure_core/tests/fixtures/alias_golden.txt',
    'native/pure_core/tests/fixtures/bearing_golden.txt',
    'native/pure_core/tests/fixtures/calc_boundary.txt',
    'native/pure_core/tests/fixtures/calc_golden.txt',
    'native/pure_core/tests/fixtures/calc_host.txt',
    'native/pure_core/tests/fixtures/calc_limits.txt',
    'native/pure_core/tests/fixtures/calc_presentation.txt',
    'native/pure_core/tests/fixtures/calc_provenance.txt',
    'native/pure_core/tests/fixtures/coords_examples.txt',
    'native/pure_core/tests/fixtures/coords_golden.txt',
    'native/pure_core/tests/fixtures/coords_host.txt',
    'native/pure_core/tests/fixtures/coords_legacy_nonfinite.txt',
    'native/pure_core/tests/fixtures/coords_policy.txt',
    'native/pure_core/tests/fixtures/coords_presentation.txt',
    'native/pure_core/tests/fixtures/coords_provenance.txt',
    'native/pure_core/tests/fixtures/coords_split.txt',
    'native/pure_core/tests/fixtures/interpolate_host.txt',
    'native/pure_core/tests/fixtures/interpolate_math.txt',
    'native/pure_core/tests/fixtures/interpolate_performance_baseline.txt',
    'native/pure_core/tests/fixtures/interpolate_performance_inputs.txt',
    'native/pure_core/tests/fixtures/interpolate_provenance.txt',
    'native/pure_core/tests/fixtures/joinchain_golden.txt',
    'native/pure_core/tests/fixtures/macro_golden.txt',
    'native/pure_core/tests/fixtures/macro_host.txt',
    'native/pure_core/tests/fixtures/macro_limits.txt',
    'native/pure_core/tests/fixtures/macro_provenance.txt',
    'native/pure_core/tests/fixtures/rebuild_e2e.txt',
    'native/pure_core/tests/fixtures/rebuild_golden.txt',
    'native/pure_core/tests/fixtures/svgpath_golden.txt',
    'native/pure_core/tests/fixtures/svgpath_host.txt',
    'native/pure_core/tests/fixtures/svgpath_provenance.txt',
    'native/pure_core/tests/fixtures/unit_angle.txt',
    'native/pure_core/tests/fixtures/unit_angle_grid.txt',
    'native/pure_core/tests/fixtures/unit_host.txt',
    'native/pure_core/tests/fixtures/unit_live_inputs.txt',
    'native/pure_core/tests/fixtures/unit_manifest.txt',
    'native/pure_core/tests/fixtures/unit_parse.txt',
    'native/pure_core/tests/fixtures/unit_scale.txt',
    'native/pure_core/tests/fixtures/unit_table.txt',
    'native/pure_core/tests/fixtures/unit_unicode.txt',
    'native/pure_core/tests/fixtures/unit_whitespace.txt',
    'native/pure_core/tests/fixtures/unitformat_golden.txt',
    'native/pure_core/tests/interpolate_golden.rs',
    'native/pure_core/tests/joinchain_golden.rs',
    'native/pure_core/tests/macro_golden.rs',
    'native/pure_core/tests/rebuild_golden.rs',
    'native/pure_core/tests/svgpath_golden.rs',
    'native/pure_core/tests/unit_golden.rs',
    'native/pure_core/tests/unitformat_golden.rs',
)


# Additive parser ABI: core ABI 1 and all its existing exports remain unchanged.
PURE_CORE_PARSE_EXPORTS = (
    "narwhal_pure_core_parse_abi", "narwhal_pure_core_parse_v2",
    "narwhal_pure_core_printwidths",
    "narwhal_pure_core_macro_size", "narwhal_pure_core_macro_encode",
    "narwhal_pure_core_unitformat_resolve_mode", "narwhal_pure_core_unitformat_format",
    "narwhal_pure_core_calc_abi2", "narwhal_pure_core_calc_v2",
    "narwhal_pure_core_bearing_abi2", "narwhal_pure_core_bearing_v2",
    "narwhal_pure_core_macro_plan_checked",
    "narwhal_pure_core_coords_owned_abi1", "narwhal_pure_core_coords_owned_v1",
    "narwhal_pure_core_svgpath_owned_version", "narwhal_pure_core_svgpath_owned",
    "narwhal_pure_core_alias_identity", "narwhal_pure_core_alias_parse", "narwhal_pure_core_alias_options",
    "narwhal_pure_core_unit_table", "narwhal_pure_core_unit_ops_parse",
    "narwhal_pure_core_unit_ops_angle", "narwhal_pure_core_unit_ops_scale",
    "narwhal_pure_core_rebuild_fit", "narwhal_pure_core_blend_bezier_points",
    "narwhal_pure_core_coords_split_v1", "narwhal_pure_core_norm_v1", "narwhal_pure_core_joinchain",
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
class CalcReply(c.Structure):
    _fields_ = [("code", c.c_uint32), ("aux", c.c_uint32), ("value", c.c_double),
                ("start", c.c_uint32), ("end", c.c_uint32), ("reserved", c.c_uint64)]
class BearingReply(c.Structure):
    _fields_ = [("code", c.c_uint32), ("reserved", c.c_uint32), ("value", c.c_double),
                ("distance", c.c_double), ("start", c.c_uint32), ("end", c.c_uint32)]
for symbol in ("narwhal_pure_core_calc_abi2", "narwhal_pure_core_bearing_abi2"):
    identity = getattr(lib, symbol)
    identity.argtypes, identity.restype = [], c.c_uint32
    if identity() != 2:
        raise RuntimeError(symbol + " ABI mismatch")
if c.sizeof(CalcReply) != 32 or c.sizeof(BearingReply) != 32:
    raise RuntimeError("owned parser reply layout mismatch")
calc = lib.narwhal_pure_core_calc_v2
calc.argtypes = [c.POINTER(c.c_uint32), c.c_size_t, c.POINTER(c.c_uint32), c.c_size_t,
                 c.c_uint32, c.c_uint32, c.POINTER(CalcReply), c.c_size_t]
calc.restype = c.c_uint32
for text, code, value, start, end in (("1+2", 0, 3.0, 0, 0), ("sqrt(-1)", 18, 0.0, 0, 4)):
    points = (c.c_uint32 * len(text))(*map(ord, text))
    reply = CalcReply(99, 99, 99, 99, 99, 99)
    if calc(points, len(text), None, 0, 1, 151, c.byref(reply), 32) != 0 or (reply.code, reply.value, reply.start, reply.end, reply.aux, reply.reserved) != (code, value, start, end, 0, 0):
        raise RuntimeError("owned calculator semantic witness differs")
bearing = lib.narwhal_pure_core_bearing_v2
bearing.argtypes = [c.POINTER(c.c_uint32), c.c_size_t, c.c_uint32, c.c_uint32,
                    c.POINTER(BearingReply), c.c_size_t]
bearing.restype = c.c_uint32
for text, code, value, start, end in (("N30E", 0, 60.0, 0, 0), ("NbadE", 3, 0.0, 1, 4)):
    points = (c.c_uint32 * len(text))(*map(ord, text))
    reply = BearingReply(99, 99, 99, 99, 99, 99)
    if bearing(points, len(text), 0, 151, c.byref(reply), 32) != 0 or (reply.code, reply.value, reply.start, reply.end, reply.distance, reply.reserved) != (code, value, start, end, 0, 0):
        raise RuntimeError("owned bearing semantic witness differs")
checked_plan = lib.narwhal_pure_core_macro_plan_checked
checked_plan.argtypes = [c.c_char_p, c.c_size_t, c.c_void_p, c.c_size_t, c.POINTER(c.c_int64)]
checked_plan.restype = c.c_int32
# MEASURED: option ! prefixes must not synthesize Cancel; command ! still must.
for text, code, payload in ((b"'_Box", -4, b'"Box"'), (b"=value", -5, b''),
                            (b"!=-15", -5, b''),
                            (b"!_Mode=x", 0, b'[{"action":"option","name":"Mode","value":"x","prefixes":["!","_"]}]')):
    output, needed = c.create_string_buffer(1024), c.c_int64(-1)
    if checked_plan(text, len(text), output, len(output), c.byref(needed)) != code or needed.value != len(payload) or output.raw[:needed.value] != payload:
        raise RuntimeError("checked planner typed refusal witness differs")
class CoordsReply(c.Structure):
    _fields_ = [("code", c.c_uint32), ("flags", c.c_uint32), ("values", c.c_double * 3),
                ("start", c.c_uint32), ("end", c.c_uint32), ("calc", CalcReply), ("bearing", BearingReply)]
identity = lib.narwhal_pure_core_coords_owned_abi1
identity.argtypes, identity.restype = [], c.c_uint32
if identity() != 1 or c.sizeof(CoordsReply) != 104:
    raise RuntimeError("owned coordinate ABI/layout mismatch")
coords = lib.narwhal_pure_core_coords_owned_v1
coords.argtypes = [c.POINTER(c.c_uint32), c.c_size_t, c.POINTER(c.c_uint32), c.c_size_t,
                   c.c_uint32, c.c_uint32, c.c_uint32, c.POINTER(CoordsReply), c.c_size_t]
coords.restype = c.c_uint32
for text, code, flags, values, start, end in (("rw1,2", 0, 3, (1.,2.,0.), 0, 0), ("5", 10, 0, (0.,0.,0.), 0, 1)):
    points = (c.c_uint32 * len(text))(*map(ord, text))
    reply = CoordsReply()
    reply.code = 99
    if coords(points, len(text), None, 0, 1, 0, 151, c.byref(reply), 104) != 0 or (reply.code, reply.flags, tuple(reply.values), reply.start, reply.end) != (code, flags, values, start, end) or bytes(reply.calc) != bytes(CalcReply()) or bytes(reply.bearing) != bytes(BearingReply()):
        raise RuntimeError("owned coordinate semantic witness differs")
split = lib.narwhal_pure_core_coords_split_v1
split.argtypes = [c.POINTER(c.c_uint32), c.c_size_t, c.c_uint32, c.c_uint32,
                  c.POINTER(c.c_uint32), c.c_size_t, c.POINTER(c.c_uint32)]
split.restype = c.c_uint32
word = "pow(2,3),4"
points = (c.c_uint32 * len(word))(*map(ord, word))
spans, count = (c.c_uint32 * 4)(), c.c_uint32()
if split(points, len(word), 44, 1, spans, 4, c.byref(count)) != 0 or count.value != 2 or list(spans) != [0, 8, 9, 10]:
    raise RuntimeError("owned coordinate split witness differs")
D, Q, B = c.POINTER(c.c_double), c.POINTER(c.c_int64), c.POINTER(c.c_ubyte)
norm = lib.narwhal_pure_core_norm_v1
norm.argtypes, norm.restype = [D, c.c_size_t, c.c_uint32, D], c.c_int32
delta = (-.4702396670038951, -.5067449846120331, .12273626832630158)
expected = .702125355913066
row, answer = (c.c_double * 6)(0.,0.,0.,*delta), c.c_double()
if norm(row, 1, 6, c.byref(answer)) != 0 or answer.value != expected:
    raise RuntimeError("compensated norm exact witness differs")
join = lib.narwhal_pure_core_joinchain
join.argtypes = [c.c_int64,D,D,c.c_double,Q,Q,B,D,D,D,D,D,D,Q,B,Q,Q]
join.restype = c.c_int32
starts = (c.c_double * 6)(-10.,-10.,-10.,*delta)
ends = (c.c_double * 6)(0.,0.,0.,10.,10.,10.)
import math
for threshold, expected_count in ((math.nextafter(expected,-math.inf),2),(expected,1),(math.nextafter(expected,math.inf),1)):
    off, length, closed, members, reverse = (c.c_int64*2)(), (c.c_int64*2)(), (c.c_ubyte*2)(), (c.c_int64*2)(), (c.c_ubyte*2)()
    axes = [(c.c_double*2)() for _ in range(6)]
    chains, total = c.c_int64(), c.c_int64()
    if join(2, starts, ends, threshold, off, length, closed, *axes, members, reverse, c.byref(chains), c.byref(total)) != 0 or chains.value != expected_count or total.value != 2 or list(members) != [0,1]:
        raise RuntimeError("Join tolerance boundary witness differs")
# Required batch14 ownership identities and independent semantic witnesses.
for symbol, expected in (("narwhal_pure_core_svgpath_owned_version", 14), ("narwhal_pure_core_alias_identity", 1)):
    fn = getattr(lib, symbol)
    fn.argtypes, fn.restype = [], c.c_uint32
    if fn() != expected:
        raise RuntimeError(symbol + " ABI mismatch")
class SvgError(c.Structure):
    _fields_ = [("kind", c.c_uint32), ("command", c.c_uint32), ("expected", c.c_uint32), ("actual", c.c_uint32), ("start", c.c_size_t), ("end", c.c_size_t), ("value", c.c_double)]
svg = lib.narwhal_pure_core_svgpath_owned
svg.argtypes = [c.POINTER(c.c_uint32), c.c_size_t, c.POINTER(c.c_double), c.c_size_t, c.POINTER(c.c_size_t), c.c_size_t, c.POINTER(c.c_ubyte), c.c_size_t, c.POINTER(c.c_size_t), c.POINTER(SvgError)]
svg.restype = c.c_int32
for text in ("M0 0 L3 0", "M0"):
    points = (c.c_uint32 * len(text))(*map(ord, text))
    output, offsets, flags, report, error = (c.c_double*8)(), (c.c_size_t*1)(), (c.c_ubyte*1)(), (c.c_size_t*2)(), SvgError()
    status = svg(points, len(text), output, 8, offsets, 1, flags, 1, report, c.byref(error))
    if text == "M0 0 L3 0":
        if status != 0 or list(report) != [8,1] or list(output) != [0.,0.,1.,0.,2.,0.,3.,0.] or list(offsets) != [0] or list(flags) != [0] or bytes(error) != bytes(SvgError()):
            raise RuntimeError("owned SVG geometry witness differs")
    elif status != 2 or (error.kind, error.command, error.expected, error.actual) != (3,77,2,1) or list(report) != [0,0]:
        raise RuntimeError("owned SVG error witness differs")
class AliasReply(c.Structure):
    _fields_ = [(name,c.c_uint32) for name in ("code","reserved","prefix_start","prefix_end","command_start","command_end","option_count")]
alias = lib.narwhal_pure_core_alias_parse
alias.argtypes = [c.POINTER(c.c_uint32),c.c_size_t,c.c_uint32,c.POINTER(AliasReply),c.c_size_t,c.POINTER(c.c_uint32),c.c_size_t]
alias.restype = c.c_uint32
options = lib.narwhal_pure_core_alias_options
options.argtypes = [c.POINTER(c.c_uint32),c.c_size_t,c.c_size_t,c.POINTER(c.c_uint32),c.c_size_t]
options.restype = c.c_uint32
for text, expected in (("'_Zoom _Extents __", (0,0,0,1,2,6,2)), ("!!", (1,0,0,0,0,0,0))):
    points = (c.c_uint32*len(text))(*map(ord,text))
    reply, parsed_spans = AliasReply(), (c.c_uint32*4)()
    if alias(points,len(text),151,c.byref(reply),28,parsed_spans,4) != 0 or tuple(getattr(reply,name) for name,_ in AliasReply._fields_) != expected:
        raise RuntimeError("owned alias prefix/error witness differs")
    if reply.code == 0:
        spans = (c.c_uint32*4)()
        if options(points,len(text),2,spans,4) != 0 or list(spans) != [8,15,18,18]:
            raise RuntimeError("owned alias options witness differs")
# Stable core-ABI-1 ownership: no invented per-export identity markers.
fit = lib.narwhal_pure_core_rebuild_fit
fit.argtypes = [D,D,c.c_size_t,c.c_size_t,c.c_size_t,c.c_ubyte,c.c_ubyte,D,D,D,c.POINTER(c.c_size_t)]
fit.restype = c.c_int32
params = (c.c_double*21)(*[i/20 for i in range(21)])
samples = (c.c_double*63)(*[v for u in params for v in (3*u,6*u,-3*u)])
ends = (c.c_double*6)(0,0,0,3,6,-3)
ctrl, knots, count = (c.c_double*21)(), (c.c_double*9)(), c.c_size_t()
status = fit(samples,params,21,4,3,0,0,ends,ctrl,knots,c.byref(count))
if status != 0 or count.value != 4 or any(not math.isfinite(ctrl[j]) or abs(ctrl[j]-v)>1e-12 for j,v in enumerate([0,0,0,1,2,-1,2,4,-2,3,6,-3])) or list(knots[:6]) != [0,0,0,1,1,1]:
    raise RuntimeError("owned rebuild numeric witness differs")
for n, degree, closed, sample_count, expected in ((3,5,1,21,-2),(1366,3,1,21,-5),(4,3,0,1,-4)):
    ctrl[:] = [99.]*21
    knots[:] = [99.]*9
    count.value = 99
    status = fit(samples,params,sample_count,n,degree,closed,0,ends,ctrl,knots,c.byref(count))
    if status != expected or count.value != 0 or list(ctrl) != [99.]*21 or list(knots) != [99.]*9:
        raise RuntimeError("owned rebuild classified refusal differs")
blend = lib.narwhal_pure_core_blend_bezier_points
blend.argtypes = [D,c.c_size_t,D,c.c_size_t,c.c_uint32,c.c_uint32,c.c_double,c.c_double,D,c.c_size_t]
blend.restype = c.c_int32
p0, p1 = (c.c_double*6)(0,0,0,3,6,0), (c.c_double*6)(6,3,0,3,-3,0)
output = (c.c_double*30)()
status = blend(p0,6,p1,6,1,1,2.,1.,output,30)
if status != 0 or list(output) != [0.,0.,0.,2.,4.,0.,5.,4.,0.,6.,3.,0.]+[0.]*18:
    raise RuntimeError("owned blend numeric witness differs")
for length, scale, capacity, expected in ((5,2.,30,-1),(6,float('nan'),30,-1),(6,2.,29,-2)):
    output[:] = [99.]*30
    status = blend(p0,length,p1,6,1,1,scale,1.,output,capacity)
    if status != expected or list(output) != [99.]*30:
        raise RuntimeError("owned blend classified refusal differs")
# Stable core-ABI-1 unit ownership: fixed independent ordering/scale witnesses.
unit_names = b"Angstroms\nNanometers\nMicrons\nMillimeters\nCentimeters\nDecimeters\nMeters\nDekameters\nHectometers\nKilometers\nMegameters\nGigameters\nMicroinches\nMils\nInches\nFeet\nYards\nMiles\nPrinterPoints\nPrinterPicas\nNauticalMiles\nAstronomicalUnits\nLightYears\nParsecs"
unit_scales = [1e-7,1e-6,1e-3,1.,10.,100.,1000.,10000.,100000.,1000000.,1e9,1e12,25.4e-6,25.4e-3,25.4,304.8,914.4,1609344.,25.4/72.,25.4/12.,1852000.,1.495978707e11,9.4607304725808e15,3.085677581491367e16]
table = lib.narwhal_pure_core_unit_table
table.argtypes = [c.POINTER(c.c_ubyte),c.c_size_t,D,c.c_size_t,c.POINTER(c.c_uint64),c.POINTER(c.c_uint64)]
table.restype = c.c_int32
names, scales, name_len, table_count = (c.c_ubyte*len(unit_names))(), (c.c_double*24)(), c.c_uint64(), c.c_uint64()
status = table(names,0,scales,0,c.byref(name_len),c.byref(table_count))
if status != -3 or (name_len.value,table_count.value) != (len(unit_names),24):
    raise RuntimeError("owned unit table query metadata differs")
status = table(names,len(names),scales,24,c.byref(name_len),c.byref(table_count))
if status != 0 or (name_len.value,table_count.value) != (len(unit_names),24) or bytes(names) != unit_names or list(scales) != unit_scales:
    raise RuntimeError("owned unit table values/order differs")
uparse = lib.narwhal_pure_core_unit_ops_parse
uparse.argtypes = [c.c_char_p,c.c_size_t,c.POINTER(c.c_uint32),c.POINTER(c.c_uint32),c.POINTER(c.c_ubyte),c.c_size_t,c.POINTER(c.c_size_t)]
uparse.restype = c.c_int32
# Space removal is after alias matching: 'm i' ambiguously prefixes Microns,
# Millimeters, Microinches, Mils and Miles rather than using the 'mi' alias.
for text, expected_status, expected_id, expected_mask, expected_raw in ((" Km ",0,9,0,"Km".encode()),("m i",3,0,143372,b"m i"),("bogus",2,0,0,b"bogus"),(" ",1,0,0,b"")):
    word = text.encode()
    uid, mask, raw_len, raw = c.c_uint32(99), c.c_uint32(99), c.c_size_t(99), (c.c_ubyte*32)(*[85]*32)
    status = uparse(word,len(word),c.byref(uid),c.byref(mask),raw,32,c.byref(raw_len))
    if status != expected_status or (uid.value,mask.value,raw_len.value) != (expected_id,expected_mask,len(expected_raw)) or bytes(raw[:raw_len.value]) != expected_raw:
        raise RuntimeError("owned unit parse status/span witness differs")
# Reject short output and malformed UTF-8 without writing metadata or bytes.
for word, capacity in ((b"mm",1),(b"\xff",32)):
    uid, mask, raw_len, raw = c.c_uint32(99), c.c_uint32(99), c.c_size_t(99), (c.c_ubyte*32)(*[85]*32)
    status = uparse(word,len(word),c.byref(uid),c.byref(mask),raw,capacity,c.byref(raw_len))
    if status != 5 or (uid.value,mask.value,raw_len.value) != (99,99,99) or bytes(raw) != b"U"*32:
        raise RuntimeError("owned unit parse ABI refusal differs")
angle = lib.narwhal_pure_core_unit_ops_angle
angle.argtypes, angle.restype = [c.c_char_p,c.c_size_t,D], c.c_int32
factor = c.c_double(99)
status = angle(b"gradians",8,c.byref(factor))
if status != 0 or factor.value != (1./100.)*(math.pi/2.):
    raise RuntimeError("owned unit angle exact witness differs")
factor.value = 99
status = angle(b"bad",3,c.byref(factor))
if status != 2 or factor.value != 99:
    raise RuntimeError("owned unit angle refusal differs")
scale = lib.narwhal_pure_core_unit_ops_scale
scale.argtypes, scale.restype = [c.c_double,c.c_double,c.c_double,D], c.c_int32
for value, sf, st, expected in ((3.,2.,4.,1.5),(1e308,2.,2.,math.inf),(math.inf,1.,math.inf,math.nan)):
    answer = c.c_double(99)
    status = scale(value,sf,st,c.byref(answer))
    if status != 0 or not (math.isnan(answer.value) if math.isnan(expected) else answer.value == expected):
        raise RuntimeError("owned unit scale order/nonfinite witness differs")
for zero in (0.,-0.):
    answer = c.c_double(99)
    status = scale(1.,2.,zero,c.byref(answer))
    if status != 6 or answer.value != 99:
        raise RuntimeError("owned unit zero division refusal differs")
if scale(1.,2.,3.,None) != 5:
    raise RuntimeError("owned unit scalar ABI refusal differs")
# Linux qualification only: interpolation's libm power route has not been
# qualified on Windows. Do not turn this into a portable bit-identity claim.
if sys.platform.startswith("linux"):
    interpolate = lib.narwhal_pure_core_interpolate
    interpolate.argtypes = [D,c.c_size_t,c.c_int64,c.c_ubyte,D,c.c_size_t,D,c.c_size_t,c.POINTER(c.c_uint64),c.POINTER(c.c_uint64),c.POINTER(c.c_uint64)]
    interpolate.restype = c.c_int32
    samples = (c.c_double*24)(*[v for x in (0.,.1,.2,.3,.4,.6,.8,1.) for v in (x,0.,0.)])
    controls, iknots = (c.c_double*24)(), (c.c_double*14)()
    degree, nc, nk = c.c_uint64(), c.c_uint64(), c.c_uint64()
    status = interpolate(samples,8,5,1,controls,24,iknots,14,c.byref(degree),c.byref(nc),c.byref(nk))
    if status != 0 or (degree.value,nc.value,nk.value) != (5,8,14) or iknots[6].hex() != "0x1.47ae147ae147bp-2" or iknots[7].hex() != "0x1.d70a3d70a3d70p-2":
        raise RuntimeError("qualified interpolation compensated-sum witness differs")
macro_size = lib.narwhal_pure_core_macro_size
macro_size.argtypes = [c.c_char_p, c.c_size_t, c.c_ubyte]
macro_size.restype = c.c_int64
macro_encode = lib.narwhal_pure_core_macro_encode
macro_encode.argtypes = [c.c_char_p, c.c_size_t, c.c_ubyte, c.c_void_p, c.c_size_t]
macro_encode.restype = c.c_int32
for text, expected in ((b"!=-15", [{"kind": "option", "name": "", "value": "15"}]),
                       (b"!_Mode=x", [{"kind": "option", "name": "Mode", "value": "x"}]),
                       (b"!_Box", [{"kind": "cancel"}, {"kind": "command", "name": "Box", "dash": False, "underscore": True, "transparent": False}]),
                       (b"!123", [{"kind": "text", "value": "!123"}]),
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
                  "parser_exports": ["narwhal_pure_core_parse_abi", "narwhal_pure_core_parse_v2", "narwhal_pure_core_printwidths", "narwhal_pure_core_macro_size", "narwhal_pure_core_macro_encode", "narwhal_pure_core_unitformat_resolve_mode", "narwhal_pure_core_unitformat_format", "narwhal_pure_core_calc_abi2", "narwhal_pure_core_calc_v2", "narwhal_pure_core_bearing_abi2", "narwhal_pure_core_bearing_v2", "narwhal_pure_core_macro_plan_checked", "narwhal_pure_core_coords_owned_abi1", "narwhal_pure_core_coords_owned_v1", "narwhal_pure_core_svgpath_owned_version", "narwhal_pure_core_svgpath_owned", "narwhal_pure_core_alias_identity", "narwhal_pure_core_alias_parse", "narwhal_pure_core_alias_options", "narwhal_pure_core_unit_table", "narwhal_pure_core_unit_ops_parse", "narwhal_pure_core_unit_ops_angle", "narwhal_pure_core_unit_ops_scale", "narwhal_pure_core_rebuild_fit", "narwhal_pure_core_blend_bezier_points", "narwhal_pure_core_coords_split_v1", "narwhal_pure_core_norm_v1", "narwhal_pure_core_joinchain"]}, sort_keys=True))
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


def require_rust_public_closure(crate: Path) -> list[str]:
    """Check path metadata only; never load the large golden fixture contents."""
    expected = set(RUST_INTEGRATION_FILES)
    actual = {"native/pure_core/" + p.relative_to(crate).as_posix()
              for p in (crate / "tests").rglob("*") if p.is_file()}
    tree = ast.parse((ROOT / "scripts/create_public_branch.py").read_text())
    public = next(ast.literal_eval(node.value) for node in tree.body
                  if isinstance(node, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == "NATIVE_FILES" for t in node.targets))
    if actual != expected or not expected.issubset(public):
        raise GateError("Rust integration source/fixture public closure differs: "
                        + repr(sorted((actual ^ expected) | (expected - set(public)))))
    return sorted(expected)


def step_tests(crate: Path) -> dict[str, object]:
    closure = require_rust_public_closure(crate)
    rc, out = _run(_cargo("test", "--offline", "--locked", crate=crate), ROOT)
    if rc != 0:
        raise GateError(f"cargo test failed:\n{out.strip()}")
    match = re.search(r"test result: ok\.\s+(\d+) passed", out)
    if match is None or int(match.group(1)) == 0:
        raise GateError("cargo test ran but reported no passing tests")
    evidence = {}
    # Explicit targets prevent --lib/discovery-only runs from claiming ownership
    # coverage. Cargo's artifact record proves the selected integration executable;
    # the unfiltered harness summary proves its tests actually ran successfully.
    for suite in RUST_INTEGRATION_SUITES:
        rc, output = _run(_cargo("test", "--offline", "--locked", "--test", suite,
                                 "--message-format=json", crate=crate), ROOT)
        artifacts = []
        for line in output.splitlines():
            if line.startswith("{"):
                try:
                    artifacts.append(json.loads(line))
                except ValueError:
                    pass
        compiled = any(a.get("reason") == "compiler-artifact"
                       and a.get("target", {}).get("name") == suite
                       and a.get("target", {}).get("kind") == ["test"]
                       and a.get("profile", {}).get("test") is True
                       and a.get("executable") for a in artifacts)
        summary = re.search(r"test result: ok\.\s+(\d+) passed; 0 failed; 0 ignored; 0 measured; 0 filtered out", output)
        if rc != 0 or not compiled or summary is None or int(summary.group(1)) == 0:
            raise GateError(f"required Rust integration suite {suite} lacks compiled/executed evidence:\n{output.strip()}")
        evidence[suite] = {"compiled": True, "passed": int(summary.group(1))}
    return {"status": "ok", "passed": int(match.group(1)), "command": "cargo test",
            "integration_suites": evidence, "public_fixture_closure": closure}


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
