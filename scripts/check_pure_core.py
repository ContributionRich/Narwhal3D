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
import tempfile
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
    "spike/test_stl_import_boundary.py",
    "spike/test_obj_writer.py",
    "spike/test_obj_reader.py",
    "spike/test_native_obj.py",
    "spike/test_dxf_writer.py",
    "spike/test_dxf_reader.py",
    "spike/test_native_dxf.py",
    "spike/test_dxf_reader24_boundaries.py",
    "spike/test_dxf_writer_owned.py",
    "spike/test_native_admission_owned.py",
    "spike/test_numparse.py",
    "spike/test_numparse_owned.py",
    "spike/test_native_numparse.py",
    "spike/test_pure_core_packaging.py",
    "spike/test_calc.py",
    "spike/test_native_calc.py",
    "spike/test_calc_owned.py", "spike/test_native_math_error_format.py",
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
    "spike/test_coil_owned.py",
    "spike/test_catenary.py",
    "spike/test_catenary_owned.py",
    "spike/test_native_intersect.py",
    "spike/test_pure_core_curve_hits_gate.py",
    "spike/test_native_coil_preview.py",
    "spike/test_preview_curve_native.py",
    "spike/test_preview24_boundaries.py",
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
    "spike/test_interpolate.py",
    "spike/test_interpolate_owned_boundary.py",
    "spike/test_native_tween.py",
)


# Explicit Rust oracle-retirement suites and their source/fixture closure.
RUST_INTEGRATION_SUITES = ('alias_golden', 'unit_golden', 'interpolate_golden', 'svgpath_golden', 'bearing_golden', 'calc_golden', 'coords_golden', 'unitformat_golden', 'macro_golden', 'joinchain_golden', 'rebuild_golden', 'blend_golden', 'norm_golden', 'nurbs_golden', 'arclength_golden', 'coil_golden', 'interpolate_helpers_golden', 'stl_owned', 'stl_import_safety', 'catenary_boundary', 'catenary_golden', 'obj_golden', 'stl_legacy_abi', 'stl_name_prefix', 'dxf_owned', 'vector23_golden', 'tween_golden', 'obj_legacy_abi', 'encoder_legacy_abi', 'earclip24_golden', 'preview24_golden', 'dxf_reader24')
RUST_INTEGRATION_FILES = (
    'native/pure_core/tests/data/.gitattributes',
    'native/pure_core/tests/data/preview24_linux.tsv',
    'native/pure_core/tests/data/preview24_windows_msvc.tsv',
    'native/pure_core/tests/data/preview24_windows_msvc.provenance.json',
    'native/pure_core/tests/data/preview24_provenance.txt',
    'native/pure_core/tests/dxf_reader24.h',
    'native/pure_core/tests/dxf_reader24.rs',
    'native/pure_core/tests/earclip24_api.h',
    'native/pure_core/tests/earclip24_golden.rs',
    'native/pure_core/tests/fixtures/dxf_reader24/README.md',
    'native/pure_core/tests/fixtures/dxf_reader24/capture.json',
    'native/pure_core/tests/fixtures/dxf_reader24/corpus.bin',
    'native/pure_core/tests/fixtures/dxf_reader24/manifest.tsv',
    'native/pure_core/tests/fixtures/earclip24_original.txt',
    'native/pure_core/tests/fixtures/earclip24_provenance.json',
    'native/pure_core/tests/fixtures/earclip24_safety.json',
    'native/pure_core/tests/preview24_api.h',
    'native/pure_core/tests/preview24_golden.rs',
    'native/pure_core/tests/dxf_owned.h',
    'native/pure_core/tests/dxf_owned.rs',
    'native/pure_core/tests/encoder_legacy_abi.rs',
    'native/pure_core/tests/fixtures/dxf_legacy23.txt',
    'native/pure_core/tests/fixtures/dxf_writer23/all-aci.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/capture.json',
    'native/pure_core/tests/fixtures/dxf_writer23/color-sentinel.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/decoder-mixed.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/decoder-no-layers.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/empty.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/key-alias.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/latin1.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/.gitattributes',
    'native/pure_core/tests/fixtures/dxf_writer23/mixed-closed.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/poly-one.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/poly-open.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/poly-zero.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/random-coordinates.bin',
    'native/pure_core/tests/fixtures/dxf_writer23/random-floats.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/short-poly-chain.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/signed-exponents.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/spline-closed.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/unicode-frame.utf8',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Angstroms.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Centimeters.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Decimeters.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Dekameters.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Feet.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Gigameters.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Hectometers.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Inches.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Kilometers.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Meters.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Microinches.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Microns.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Miles.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Millimeters.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Mils.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Nanometers.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-UnknownUnit.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-Yards.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-default.dxf',
    'native/pure_core/tests/fixtures/dxf_writer23/units-empty.dxf',
    'native/pure_core/tests/fixtures/encoder_legacy23_provenance.txt',
    'native/pure_core/tests/fixtures/tween_host23.json',
    'native/pure_core/tests/fixtures/tween_lengths23.txt',
    'native/pure_core/tests/fixtures/tween_original23.txt',
    'native/pure_core/tests/fixtures/tween_provenance23.txt',
    'native/pure_core/tests/fixtures/vector23_golden.txt',
    'native/pure_core/tests/fixtures/vector23_provenance.txt',
    'native/pure_core/tests/fixtures/vector23_titles.txt',
    'native/pure_core/tests/fixtures/vector_legacy23.txt',
    'native/pure_core/tests/obj_legacy_abi.rs',
    'native/pure_core/tests/tween_golden.rs',
    'native/pure_core/tests/tween_v1.h',
    'native/pure_core/tests/vector23_api.h',
    'native/pure_core/tests/vector23_golden.rs',
    'native/pure_core/tests/fixtures/catenary_numeric_windows_msvc.txt',
    'native/pure_core/tests/fixtures/catenary_host_windows_msvc.txt',
    'native/pure_core/tests/fixtures/catenary_windows_msvc.provenance.json',

    'native/pure_core/tests/catenary_boundary.rs',
    'native/pure_core/tests/catenary_golden.rs',
    'native/pure_core/tests/catenary_v2.h',
    'native/pure_core/tests/obj_golden.rs',
    'native/pure_core/tests/stl_legacy_abi.rs',
    'native/pure_core/tests/stl_name_prefix.rs',
    'native/pure_core/tests/fixtures/catenary_baseline_linux.json',
    'native/pure_core/tests/fixtures/catenary_host_linux.txt',
    'native/pure_core/tests/fixtures/catenary_numeric_linux.txt',
    'native/pure_core/tests/fixtures/obj_original22.txt',
    'native/pure_core/tests/fixtures/obj_original22_provenance.txt',
    'native/pure_core/tests/fixtures/stl_prefix_headers.bin',
    'native/pure_core/tests/fixtures/stl_prefix_headers.txt',

    'native/pure_core/tests/coil_golden.rs',
    'native/pure_core/tests/interpolate_helpers_golden.rs',
    'native/pure_core/tests/stl_owned.rs',
    'native/pure_core/tests/stl_import_safety.rs',
    'native/pure_core/tests/interpolate_helpers.h',
    'native/pure_core/tests/fixtures/coil_numeric.txt',
    'native/pure_core/tests/fixtures/coil_windows_msvc.txt',
    'native/pure_core/tests/fixtures/coil_windows_msvc_provenance.txt',
    'native/pure_core/tests/fixtures/coil_baseline_linux.json',
    'native/pure_core/tests/fixtures/interpolate_helpers_linux.txt',
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
    'native/pure_core/tests/fixtures/calc_windows_msvc.txt',
    'native/pure_core/tests/fixtures/coords_windows_msvc.txt',
    'native/pure_core/tests/fixtures/interpolate_windows_msvc.txt',
    'native/pure_core/tests/fixtures/svgpath_windows_msvc.txt',
    'native/pure_core/tests/fixtures/math_error_windows_msvc.txt',
    'native/pure_core/tests/fixtures/windows_msvc_provenance.txt',
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


# New producer modules must survive the public source copy as well as the tests.
RUST_REQUIRED_MODULE_FILES = ("native/pure_core/src/preview_primitives.rs",)


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


# Required round-24 retirement ABIs (additive to all prior requirements). Kept separate from the older parser
# identity so existing optional ABI consumers retain their compatibility contract.
PURE_CORE_RETIREMENT_EXPORTS = (
    "narwhal_pure_core_stl_size_owned", "narwhal_pure_core_stl_encode_owned",
    "narwhal_pure_core_stl_file_size", "narwhal_pure_core_stl_file_decode",
    "narwhal_pure_core_stl_import_mesh_decode", "narwhal_pure_core_stl_normal",
    "narwhal_pure_core_interpolate_helper_v1",
    "narwhal_pure_core_catenary_plan_v2", "narwhal_pure_core_obj_encode_into",
    "narwhal_pure_core_vector_encode_v1",
    "narwhal_pure_core_vector_len_v1",
    "narwhal_pure_core_vector_copy_v1",
    "narwhal_pure_core_vector_free_v1",
    "narwhal_pure_core_vector_validate_v1",
    "narwhal_pure_core_dxf_owned_abi_version",
    "narwhal_pure_core_dxf_owned_colors",
    "narwhal_pure_core_dxf_owned_units",
    "narwhal_pure_core_dxf_owned_capacity",
    "narwhal_pure_core_dxf_owned_encode",
    "narwhal_pure_core_tween_plan_v1",
    "narwhal_pure_core_tween_knots_v1",
    "narwhal_pure_core_tween_lengths_v1",
    "narwhal_pure_core_ear_clip_display",
    "narwhal_pure_core_earclip_signed_area_v1",
    "narwhal_pure_core_control_point_curve_preview",
    "narwhal_pure_core_preview_sample_v1",
    "narwhal_pure_core_dxf_reader24_abi_version",
    "narwhal_pure_core_dxf_reader24_decode",
)
PURE_CORE_RETIREMENT_IDENTITY = {"retirement_abi": 24,
                                 "retirement_exports": list(PURE_CORE_RETIREMENT_EXPORTS)}
PURE_CORE_RETIREMENT_PROBE = r'''
import ctypes as c, json, math, struct, sys
lib = c.CDLL(sys.argv[1])
def bind(name, args, result=c.c_int32):
    fn = getattr(lib, "narwhal_pure_core_" + name)
    fn.argtypes, fn.restype = args, result
    return fn
if bind("abi_version", [], c.c_uint32)() != 1:
    raise RuntimeError("retirement core ABI mismatch")
D, I, U = c.POINTER(c.c_double), c.POINTER(c.c_int64), c.POINTER(c.c_uint64)
size = bind("stl_size_owned", [D,c.c_size_t,c.c_void_p,c.c_size_t,c.c_ubyte], c.c_int64)
encode = bind("stl_encode_owned", [D,c.c_size_t,c.c_void_p,c.c_size_t,c.c_ubyte,c.c_void_p,c.c_size_t])
file_size = bind("stl_file_size", [c.c_void_p,c.c_size_t,c.c_ubyte,I], c.c_int64)
decode = bind("stl_file_decode", [c.c_void_p,c.c_size_t,c.c_ubyte,D,c.c_size_t])
mesh = bind("stl_import_mesh_decode", [c.c_void_p,c.c_size_t,D,c.c_size_t,c.POINTER(c.c_uint32),c.c_size_t,I,I,I])
normal = bind("stl_normal", [D,D])
def require(ok, message):
    if not ok:
        raise RuntimeError("required retirement semantics: " + message)
xyz = (c.c_double*9)(0,0,0,1,0,0,0,1,0)
n = (c.c_double*3)(99,99,99)
require(normal(xyz,n) == 0 and list(n) == [0.,0.,1.], "normal")
expected_binary = b"probe".ljust(80,b" ") + struct.pack("<I12fH",1,0,0,1,*xyz,0)
expected_ascii = (b"solid probe\n  facet normal 0 0 1\n  outer loop\n"
                  b"    vertex 0 0 0\n    vertex 1 0 0\n    vertex 0 1 0\n"
                  b"  endloop\n  endfacet\nendsolid probe\n")
for binary, expected in ((1,expected_binary),(0,expected_ascii)):
    count = size(xyz,1,b"probe",5,binary)
    require(count == len(expected), "owned size")
    out = c.create_string_buffer(count)
    require(encode(xyz,1,b"probe",5,binary,out,count) == 0 and out.raw == expected, "owned encoding")
    short = c.create_string_buffer(b"!"*count, count)
    require(encode(xyz,1,b"probe",5,binary,short,count-1) != 0 and short.raw == b"!"*count, "encode capacity atomicity")
    diag = (c.c_int64*3)(99,99,99)
    require(file_size(expected,len(expected),0,diag) == 9 and list(diag) == [0,0,0], "decode size")
    coords = (c.c_double*9)(*[99.]*9)
    require(decode(expected,len(expected),0,coords,9) == 0 and list(coords) == list(xyz), "decode coordinates")
    verts, indices = (c.c_double*9)(), (c.c_uint32*3)()
    nv, nf = c.c_int64(-1), c.c_int64(-1)
    require(mesh(expected,len(expected),verts,3,indices,3,c.byref(nv),c.byref(nf),diag) == 0
            and (nv.value,nf.value) == (3,1) and list(verts) == list(xyz)
            and list(indices) == [0,1,2] and list(diag) == [0,0,0], "import mesh")
# File codecs preserve explicit nonfinite data; geometry import must refuse it
# (and values that overflow the actual destination f32) without publishing output.
for token, reason in ((b"nan",1),(b"1e100",2)):
    data = expected_ascii.replace(b"vertex 0 0 0", b"vertex " + token + b" 0 0")
    diag = (c.c_int64*3)()
    require(file_size(data,len(data),3,diag) == 9, "unsafe file size")
    verts, indices = (c.c_double*9)(*[123.]*9), (c.c_uint32*3)(77,77,77)
    nv, nf = c.c_int64(-1), c.c_int64(-1)
    require(mesh(data,len(data),verts,3,indices,3,c.byref(nv),c.byref(nf),diag) == -30
            and list(diag) == [-30,0,reason] and (nv.value,nf.value) == (-1,-1)
            and list(verts) == [123.]*9 and list(indices) == [77]*3, "unsafe import atomic refusal")
diag = (c.c_int64*3)()
require(file_size(b"x",1,1,diag) == -20 and list(diag) == [-20,0,0], "truncated binary diagnostic")
class Request(c.Structure):
    _fields_ = [("operation",c.c_uint32),("style",c.c_uint32),
                ("rows",c.c_uint64),("columns",c.c_uint64),
                ("degree",c.c_int64),("index",c.c_int64),
                ("parameter",c.c_double),("data",D),("data_len",c.c_size_t)]
require(c.sizeof(Request) == 64, "helper request layout")
helper = bind("interpolate_helper_v1", [c.POINTER(Request),D,c.c_size_t,U,U])
def call(op, data, rows, columns=0, degree=0, index=0, parameter=0., style=0, capacity=32):
    values = (c.c_double*len(data))(*data)
    request = Request(op,style,rows,columns,degree,index,parameter,values,len(data))
    output = (c.c_double*32)(*[123.]*32)
    used, detail = c.c_uint64(99), c.c_uint64(99)
    rc = helper(c.byref(request),output,capacity,c.byref(used),c.byref(detail))
    return rc, used.value, detail.value, list(output)
# Exact simple witnesses exercise every operation, including pivot/permutation.
knots = [0.,0.,1.,1.]
for op,data,rows,cols,degree,index,param,style,answer in (
    (1,[0,0,0,1,0,0,4,0,0],3,0,0,0,0.,1,[0.,.25,1.]),
    (2,[0,.5,1],3,0,1,0,0.,0,[0.,0.,.5,1.,1.]),
    (3,knots,2,0,1,0,.25,0,[1.]),
    (4,knots,2,0,1,1,.25,0,[.75,.25]),
    (5,[0,1]+knots,2,2,1,0,0.,0,[1.,0.,0.,1.]),
    (6,[0,1,2,3],2,0,0,0,0.,0,[2.,3.,0.,1.,1.,0.]),
    (7,[2,3,0,1,1,0,1,5],2,1,0,0,0.,0,[1.,1.]),
    (8,[0,0,0,1,0,0,1,1,0,0,1,0],4,0,0,0,0.,0,[0.,.25,.5,.75,1.]),
    (9,[0,.5,1],3,0,1,0,0.,0,[-.5,0.,.5,1.,1.5]),
    (10,[0,0,0,2,2,2,0,0,0,2,4,6],3,1,0,0,0.,0,[1.,2.,3.]),
):
    rc,used,detail,out = call(op,data,rows,cols,degree,index,param,style)
    require(rc == 0 and used == len(answer) and detail == 0 and out[:used] == answer
            and out[used:] == [123.]*(32-used), "helper operation " + str(op))
for args, status, detail in (((1,[0,0,0,0,0,0],2,0,0,0,0.,1),-5,0),
                              ((6,[0,0,0,0],2),-4,0),
                              ((99,[],0),-2,0)):
    rc,used,got_detail,out = call(*args)
    require((rc,used,got_detail) == (status,0,detail) and out == [123.]*32, "helper refusal")
rc,used,detail,out = call(1,[0,0,0,1,0,0],2,capacity=1)
require((rc,used,detail) == (-3,0,0) and out == [123.]*32, "helper capacity atomicity")
# Catenary/OBJ ownership: exact caller-owned catenary v2 metadata and reason enum from
# tests/catenary_v2.h; no Python planner or production adapter is imported.
cat = bind("catenary_plan_v2", [D,D,D,D,c.c_double,D,c.c_size_t,D,c.c_size_t,U,U,U,D,c.POINTER(c.c_uint32)])
def catenary(points=None, tolerance=.001, pole_capacity=1155, knot_capacity=387, null_input=False):
    points = points or ((1.,2.,3.),(11.,2.,3.),(6.,2.,2.),(6.,2.,1.))
    inputs = [(c.c_double*3)(*point) for point in points]
    poles, knots = (c.c_double*1155)(*[123.]*1155), (c.c_double*387)(*[456.]*387)
    np,nk,ns,dev,reason = c.c_uint64(99),c.c_uint64(99),c.c_uint64(99),c.c_double(99.),c.c_uint32(99)
    args = [None if null_input else inputs[0],*inputs[1:]]
    rc = cat(*args,tolerance,poles,pole_capacity,knots,knot_capacity,
             c.byref(np),c.byref(nk),c.byref(ns),c.byref(dev),c.byref(reason))
    return rc,reason.value,np.value,nk.value,ns.value,dev.value,list(poles),list(knots)
rc,reason,np,nk,ns,dev,poles,knots = catenary()
require((rc,reason,np,nk,ns) == (0,0,75,27,8) and math.isfinite(dev) and 0 <= dev <= .001,
        "catenary v2 counts/deviation")
require(poles[:3] == [1.,2.,3.] and poles[np-3:np] == [11.,2.,3.]
        and all(poles[i] == 2. for i in range(1,np,3))
        and abs(poles[36]-6.) <= 1e-12 and abs(poles[38]-1.) <= 1e-12
        and all(math.isfinite(x) for x in poles[:np])
        and knots[:nk] == [j/8. for j in range(9) for _ in range(3)]
        and poles[np:] == [123.]*(1155-np) and knots[nk:] == [456.]*(387-nk),
        "catenary v2 world frame/knots/tail atomicity")
base = ((1.,2.,3.),(11.,2.,3.),(6.,2.,2.),(6.,2.,1.))
for points,tolerance,status,why in (
    (((float("nan"),2.,3.),*base[1:]),.001,-2,1),
    ((base[0],base[1],(6.,2.,3.),base[3]),.001,-2,2),
    ((base[0],(11.,2.,4.),base[2],base[3]),.001,-2,3),
    ((base[0],base[1],base[2],(7.,2.,1.)),.001,-2,4),
    (base,1e-30,-4,5),
    (((1e308,0.,0.),(1e308,1.,0.),(1e308,0.,1.),(0.,0.,0.)),.001,-2,6),
):
    rc,reason,np,nk,ns,dev,poles,knots = catenary(points,tolerance)
    require((rc,reason,np,nk,ns,dev) == (status,why,0,0,0,0.)
            and poles == [123.]*1155 and knots == [456.]*387,
            "catenary v2 named reason " + str(why))
for kwargs, status in (({"pole_capacity":74},-3),({"knot_capacity":26},-3),({"null_input":True},-1)):
    rc,reason,np,nk,ns,dev,poles,knots = catenary(**kwargs)
    require((rc,reason,np,nk,ns,dev) == (status,0,0,0,0,0.)
            and poles == [123.]*1155 and knots == [456.]*387,
            "catenary v2 infrastructure atomic refusal")
# Existing OBJ export is now required to do actual serialization, not merely
# exist in the DLL's export table. Match native_obj.py's exact packed signature.
I32 = c.POINTER(c.c_int32)
obj = bind("obj_encode_into", [D,c.c_int64,c.c_void_p,c.c_int64,I,I,I,I,c.c_int64,I32,c.c_int64,c.c_int64,c.c_void_p,c.c_size_t,I])
vertices = (c.c_double*18)(0,0,0,1,0,0,0,1,0,2,0,0,3,0,0,2,1,0)
names, vc, fc, arity = (c.c_int64*2)(1,1),(c.c_int64*2)(3,3),(c.c_int64*2)(1,1),(c.c_int64*2)(3,3)
indices = (c.c_int32*6)(0,1,2,0,2,1)
expected = b"# Narwhal 3D\no a\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\no b\nv 2 0 0\nv 3 0 0\nv 2 1 0\nf 4 6 5\n"
def obj_call(capacity=1024):
    output,written = c.create_string_buffer(b"!"*1024,1024),c.c_int64(-1)
    rc = obj(vertices,6,b"ab",2,names,vc,fc,arity,2,indices,6,2,output,capacity,c.byref(written))
    return rc,written.value,output.raw
rc,written,output = obj_call()
require(rc == 0 and written == len(expected) and output[:written] == expected
        and output[written:] == b"!"*(1024-written), "required OBJ serialization/global indices")
rc,written,output = obj_call(1)
require((rc,written) == (-3,-1) and output == b"!"*1024, "required OBJ capacity atomicity")
for damage in ("nonfinite","index","arity"):
    vertices[0],indices[0],arity[0] = 0.,0,3
    if damage == "nonfinite": vertices[0] = float("nan")
    if damage == "index": indices[0] = 3
    if damage == "arity": arity[0] = 2
    rc,written,output = obj_call()
    require((rc,written) == (-2,-1) and output == b"!"*1024, "required OBJ domain atomicity: " + damage)
# Layouts/signatures mirror vector23_api.h, dxf_owned.h, tween_v1.h. These
# direct calls intentionally do not import adapters or a retired Python engine.
import hashlib
class VDouble(c.Structure):
    _fields_ = [("data",D),("len",c.c_size_t)]
class VInt(c.Structure):
    _fields_ = [("data",I),("len",c.c_size_t)]
class VFlag(c.Structure):
    _fields_ = [("data",c.POINTER(c.c_int32)),("len",c.c_size_t)]
class VByte(c.Structure):
    _fields_ = [("data",c.c_void_p),("len",c.c_size_t)]
class VectorRequest(c.Structure):
    _fields_ = [("size",c.c_size_t),("format",c.c_uint32),("digits",c.c_int32),
                ("page_w_mm",c.c_double),("page_h_mm",c.c_double),("unit_mm",c.c_double),
                ("segments",c.c_size_t),("beziers",VDouble),("counts",VInt),
                ("closed",VFlag),("rgb",VDouble),("widths",VDouble),
                ("layers",VByte),("layer_lengths",VInt),("title",VByte),("unit",VByte),("title_parts",VInt)]
vector_encode = bind("vector_encode_v1",[c.POINTER(VectorRequest),c.POINTER(c.c_void_p)])
vector_len = bind("vector_len_v1",[c.c_void_p],c.c_size_t)
vector_copy = bind("vector_copy_v1",[c.c_void_p,c.c_void_p,c.c_size_t,c.POINTER(c.c_size_t)])
vector_free = bind("vector_free_v1",[c.c_void_p],None)
vector_validate = bind("vector_validate_v1",[c.c_uint32,c.c_void_p,c.c_size_t])
require(c.sizeof(VectorRequest) == 208, "vector request layout")
bz = (c.c_double*8)(1,2,3,4,5,6,7,8)
counts,closed,rgb,widths,lens = (c.c_int64*1)(1),(c.c_int32*1)(0),(c.c_double*3)(1,0,0),(c.c_double*1)(.5),(c.c_int64*1)(7)
layer,title,unit = c.create_string_buffer(b"Startup"),c.create_string_buffer(b"startup"),c.create_string_buffer(b"in")
request = VectorRequest(c.sizeof(VectorRequest),0,4,100.,80.,1.,1,
                        VDouble(bz,8),VInt(counts,1),VFlag(closed,1),VDouble(rgb,3),VDouble(widths,1),
                        VByte(c.addressof(layer),7),VInt(lens,1),VByte(c.addressof(title),7),VByte(None,0),VInt(None,0))
# Independently captured original Python bytes (vector_legacy23.txt), not a
# second serializer. Hashes pin complete metadata, operators, units and xrefs.
for fmt,scale,suffix,length,digest in (
    (0,1.,False,827,"65521d66a71bc1932a8aea9c251362910553c30596249f51aa92a3f55def64f6"),
    (1,1.,False,425,"2997d78f4f626f6b3221d404906a09d98c9f479bf8d6f851cbb81399d9294949"),
    (1,25.4,True,419,"c94568a5d99506b93520b158382b7b2af37cdb197818462087e75715eb604f11")):
    request.format,request.unit_mm = fmt,scale
    request.title = VByte(c.addressof(title),7) if fmt == 0 else VByte(None,0)
    request.unit = VByte(c.addressof(unit),2) if suffix else VByte(None,0)
    handle = c.c_void_p()
    require(vector_encode(c.byref(request),c.byref(handle)) == 0 and handle.value, "vector owned handle")
    try:
        require(vector_len(handle) == length, "vector owned length")
        output,written = c.create_string_buffer(b"!"*(length+8),length+8),c.c_size_t(777)
        require(vector_copy(handle,output,length-1,c.byref(written)) == -3
                and written.value == 777 and output.raw == b"!"*(length+8), "vector short-copy atomicity")
        require(vector_copy(handle,output,length+8,c.byref(written)) == 0 and written.value == length
                and hashlib.sha256(output.raw[:length]).hexdigest() == digest
                and output.raw[length:] == b"!"*8, "vector complete captured bytes")
        require(vector_validate(fmt,output,length) == 0, "vector native framing validation")
        second = c.create_string_buffer(length)
        written.value = 777
        require(vector_copy(handle,second,length,c.byref(written)) == 0
                and written.value == length and second.raw == output.raw[:length], "vector repeat owned copy")
        sibling_request = VectorRequest.from_buffer_copy(request)
        sibling_request.format, sibling_request.unit_mm = 1-fmt,1.
        sibling_request.unit = VByte(None,0)
        sibling_request.title = VByte(c.addressof(title),7) if fmt == 1 else VByte(None,0)
        sibling = c.c_void_p()
        require(vector_encode(c.byref(sibling_request),c.byref(sibling)) == 0
                and sibling.value and sibling.value != handle.value, "vector simultaneous ownership")
        try:
            other_length = 425 if fmt == 0 else 827
            other_digest = ("2997d78f4f626f6b3221d404906a09d98c9f479bf8d6f851cbb81399d9294949" if fmt == 0 else
                            "65521d66a71bc1932a8aea9c251362910553c30596249f51aa92a3f55def64f6")
            require(vector_len(sibling) == other_length, "vector sibling length")
            other = c.create_string_buffer(other_length)
            require(vector_copy(sibling,other,other_length,c.byref(written)) == 0
                    and written.value == other_length and hashlib.sha256(other.raw).hexdigest() == other_digest,
                    "vector sibling complete bytes")
            written.value = 777
            require(vector_copy(handle,second,length,c.byref(written)) == 0 and written.value == length
                    and hashlib.sha256(second.raw).hexdigest() == digest, "vector first ownership survives another encode")
        finally:
            vector_free(sibling)
    finally:
        vector_free(handle)
require(vector_len(None) == 0, "vector null length")
vector_free(None)
require(vector_validate(1,b"broken",6) == -6, "vector malformed framing refusal")
for field,bad,expected in (("page_w_mm",float("nan"),-4),("unit_mm",0.,-5),("digits",4097,-3),("segments",2,-2)):
    saved = getattr(request,field)
    setattr(request,field,bad)
    handle = c.c_void_p(1)
    try:
        require(vector_encode(c.byref(request),c.byref(handle)) == expected and handle.value is None,
                "vector typed refusal clears ownership: " + field)
    finally:
        setattr(request,field,saved)
class DXFLayer(c.Structure):
    _fields_ = [("name",c.c_int64),("color",c.c_int64)]
class DXFEntity(c.Structure):
    _fields_ = [(name,c.c_int64) for name in ("kind","name","color","points","closed","true_color","label")]
class DXFInput(c.Structure):
    _fields_ = [("version",c.c_uint64),("names",c.c_void_p),("names_len",c.c_size_t),
                ("lengths",I),("names_count",c.c_size_t),("layers",c.POINTER(DXFLayer)),("layer_count",c.c_size_t),
                ("entities",c.POINTER(DXFEntity)),("entity_count",c.c_size_t),("nums",D),("nums_len",c.c_size_t),
                ("unit",c.c_void_p),("unit_len",c.c_size_t),("unit_mode",c.c_ubyte)]
require(c.sizeof(DXFInput) == 112 and c.sizeof(DXFEntity) == 56, "DXF typed input layout")
require(bind("dxf_owned_abi_version",[],c.c_uint32)() == 2, "DXF owned version")
dxf_colors = bind("dxf_owned_colors",[I,c.c_size_t,I])
dxf_units = bind("dxf_owned_units",[c.c_void_p,c.c_size_t,c.c_ubyte,I])
dxf_capacity = bind("dxf_owned_capacity",[c.POINTER(DXFInput),c.POINTER(c.c_size_t),I])
dxf_encode = bind("dxf_owned_encode",[c.POINTER(DXFInput),c.c_void_p,c.c_size_t,I,I,I])
diag = (c.c_int64*3)(99,99,99)
require(dxf_colors((c.c_int64*2)(7,0x1000000),2,diag) == 0 and list(diag) == [0,0,0], "DXF valid colors")
require(dxf_colors((c.c_int64*2)(7,256),2,diag) == -40 and list(diag) == [-40,1,0], "DXF color typed reason")
for name,mode,answer in ((b"",0,[4,-1]),(b"Inches",1,[1,0]),(b"UnknownUnit",1,[0,-1])):
    out = (c.c_int64*2)(99,99)
    require(dxf_units(name,len(name),mode,out) == 0 and list(out) == answer, "DXF unit metadata")
names, name_lengths = c.create_string_buffer(b"Cut"),(c.c_int64*1)(3)
entities,nums = (DXFEntity*1)(DXFEntity(0,0,7,0,0,0,0)),(c.c_double*6)(0,0,0,1,2,3)
dxf_request = DXFInput(2,c.addressof(names),3,name_lengths,1,None,0,entities,1,nums,6,None,0,0)
expected_dxf = b'0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1009\n9\n$INSUNITS\n70\n4\n0\nENDSEC\n0\nSECTION\n2\nTABLES\n0\nTABLE\n2\nLAYER\n70\n1\n0\nLAYER\n2\nCut\n70\n0\n62\n7\n6\nCONTINUOUS\n0\nENDTAB\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n0\nLINE\n8\nCut\n62\n7\n10\n0\n20\n0\n30\n0\n11\n1\n21\n2\n31\n3\n0\nENDSEC\n0\nEOF\n'
capacity = c.c_size_t(777)
require(dxf_capacity(c.byref(dxf_request),c.byref(capacity),diag) == 0 and list(diag) == [0,0,0]
        and len(expected_dxf) <= capacity.value <= 1024*1024, "DXF bounded capacity")
output = c.create_string_buffer(b"!"*capacity.value,capacity.value)
written,encoding = c.c_int64(777),c.c_int64(777)
require(dxf_encode(c.byref(dxf_request),output,1,c.byref(written),c.byref(encoding),diag) == -3
        and list(diag) == [-3,0,0] and (written.value,encoding.value) == (777,777)
        and output.raw == b"!"*capacity.value, "DXF short output atomicity")
require(dxf_encode(c.byref(dxf_request),output,capacity.value,c.byref(written),c.byref(encoding),diag) == 0
        and list(diag) == [0,0,0] and (written.value,encoding.value) == (len(expected_dxf),0)
        and output.raw[:written.value] == expected_dxf
        and output.raw[written.value:] == b"!"*(capacity.value-written.value), "DXF exact file bytes")
for damage,status,index in (("color",-40,0),("type",-42,0),("nonfinite",-44,2)):
    entities[0].color,entities[0].kind,nums[2] = 7,0,0.
    if damage == "color": entities[0].color = 256
    if damage == "type": entities[0].kind = 99
    if damage == "nonfinite": nums[2] = float("nan")
    capacity.value = 777
    require(dxf_capacity(c.byref(dxf_request),c.byref(capacity),diag) == status and capacity.value == 777
            and list(diag) == [status,index,0], "DXF capacity typed refusal: " + damage)
    c.memset(output,ord("!"),len(output))
    written.value,encoding.value = 777,777
    require(dxf_encode(c.byref(dxf_request),output,len(output),c.byref(written),c.byref(encoding),diag) == status
            and list(diag) == [status,index,0] and (written.value,encoding.value) == (777,777)
            and output.raw == b"!"*len(output), "DXF domain atomic refusal: " + damage)
tween_plan = bind("tween_plan_v1",[D,D,c.c_size_t,c.c_size_t,c.c_uint32,c.c_uint32,D,c.c_size_t,U])
tween_knots = bind("tween_knots_v1",[D,D,c.c_size_t])
tween_lengths = bind("tween_lengths_v1",[c.c_double,c.c_size_t,D,c.c_size_t,U])
a,b = (c.c_double*4)(0,2,4,1),(c.c_double*4)(2,4,6,1)
for mode,answer in ((0,[1.,3.,5.,1.]),(1,[1.,3.,5.,1.]),(2,[1.,3.,5.])):
    output,used = (c.c_double*8)(*[123.]*8),c.c_uint64(777)
    require(tween_plan(a,b,1,1,mode,0,output,8,c.byref(used)) == 0
            and used.value == len(answer) and list(output)[:used.value] == answer
            and list(output)[used.value:] == [123.]*(8-used.value), "Tween mode semantics: " + str(mode))
for damage,status in (("capacity",-3),("mode",-2),("nonfinite",-12),("weight",-10)):
    a[0],a[3] = 0.,1.
    if damage == "nonfinite": a[0] = float("nan")
    if damage == "weight": a[3] = 0.
    output,used = (c.c_double*8)(*[123.]*8),c.c_uint64(777)
    require(tween_plan(a,b,1,1,99 if damage == "mode" else 0,3 if damage == "weight" else 0,
                       output,1 if damage == "capacity" else 8,c.byref(used)) == status
            and used.value == 777 and list(output) == [123.]*8, "Tween typed atomic refusal: " + damage)
knots,other = (c.c_double*4)(0,0,1,1),(c.c_double*4)(0,0,.5,1)
require(tween_knots(knots,knots,4) == 0 and tween_knots(knots,other,4) == -20, "Tween knot compatibility")
output,used = (c.c_double*5)(*[123.]*5),c.c_uint64(777)
require(tween_lengths(8.,3,output,5,c.byref(used)) == 0 and used.value == 3
        and list(output) == [0.,4.,8.,123.,123.], "Tween equal-length targets")
for total,samples,cap,status in ((8.,3,1,-3),(float("nan"),3,5,-2),(8.,1,5,-2)):
    output,used = (c.c_double*5)(*[123.]*5),c.c_uint64(777)
    require(tween_lengths(total,samples,output,cap,c.byref(used)) == status
            and used.value == 777 and list(output) == [123.]*5, "Tween lengths atomic refusal")
# Round-24 planar-loop, preview and reader layouts mirror the qualified headers.
ear_clip = bind("ear_clip_display",[D,c.c_size_t,U,c.c_size_t,U])
ear_area = bind("earclip_signed_area_v1",[D,c.c_size_t,D,U])
ear_square = (c.c_double*8)(0,0,1,0,1,1,0,1)
ear_partial = (c.c_double*12)(0,0,2,2,4,0,4,2,2,0,0,2)
for points,n,expected in ((ear_square,4,[0,1,2,0,2,3]),(ear_partial,6,[5,0,1,1,2,3])):
    output,count = (c.c_uint64*16)(*[777]*16),c.c_uint64(999)
    require(ear_clip(points,n,output,16,c.byref(count)) == 0 and count.value == 2
            and list(output)[:6] == expected and list(output)[6:] == [777]*10,
            "ear clip exact ordered/partial triangles")
for points,n,capacity,status in ((ear_square,4,5,-3),((c.c_double*8)(float("nan"),0,1,0,1,1,0,1),4,16,-2),
                                 (None,(1<<20)+1,16,-2)):
    output,count = (c.c_uint64*16)(*[777]*16),c.c_uint64(999)
    require(ear_clip(points,n,output,capacity,c.byref(count)) == status
            and count.value == 0 and list(output) == [777]*16,"ear clip atomic refusal")
count = c.c_uint64(999)
require(ear_clip(None,0,None,0,c.byref(count)) == 0 and count.value == 0,"ear clip empty loop")
for points,n,answer in ((ear_square,4,1.),(None,0,0.)):
    output,count = c.c_double(123.),c.c_uint64(999)
    require(ear_area(points,n,c.byref(output),c.byref(count)) == 0
            and count.value == 1 and struct.pack("<d",output.value) == struct.pack("<d",answer),
            "ear clip signed area value/count")
output,count = c.c_double(123.),c.c_uint64(999)
require(ear_area(None,(1<<20)+1,c.byref(output),c.byref(count)) == -2
        and output.value == 123. and count.value == 0,"ear clip signed area resource refusal")
control_preview = bind("control_point_curve_preview",[D,c.c_size_t,c.c_uint32,c.c_uint32,D,c.c_size_t,U])
control = (c.c_double*9)(0,0,0,2,4,0,4,0,0)
used = c.c_uint64(999)
require(control_preview(control,3,2,2,None,0,c.byref(used)) == -3 and used.value == 9,
        "control preview required size query")
output = (c.c_double*12)(*[123.]*12)
require(control_preview(control,3,2,2,output,9,c.byref(used)) == 0 and used.value == 9
        and list(output) == [0.,0.,0.,2.,2.,0.,4.,0.,0.,123.,123.,123.],"control preview exact quadratic")
output = (c.c_double*12)(*[123.]*12)
used.value = 999
require(control_preview(control,3,2,2,output,8,c.byref(used)) == -3 and used.value == 9
        and list(output) == [123.]*12,"control preview capacity count/payload contract")
control[0] = float("nan")
require(control_preview(control,3,2,2,output,9,c.byref(used)) == -2 and used.value == 0
        and list(output) == [123.]*12,"control preview nonfinite refusal")
class PreviewMetadata(c.Structure):
    _fields_ = [("values",c.c_size_t),("rows",c.c_size_t),("lengths",c.c_size_t*3)]
require(c.sizeof(PreviewMetadata) == 40,"preview metadata layout")
preview_sample = bind("preview_sample_v1",[c.c_uint32,D,c.c_size_t,c.c_size_t,c.c_uint32,D,c.c_size_t,c.POINTER(PreviewMetadata)])
def preview_meta(meta):
    return (meta.values,meta.rows,*meta.lengths)
# Complete original bits from six nonempty authored preview24 TSV requests;
# no tolerance widening or regenerated platform-dependent trigonometric inputs.
preview_witnesses = ((1, 3, 0, '0000000000000000,8000000000000000,0000000000000000,3ff0000000000000,0000000000000000,0000000000000000,0000000000000000,3ff0000000000000,0000000000000000,3fc0000000000000', (12, 1, 4, 0, 0), '4bc6d766e868e962dd796bf50ecdf2aaea4b9ade0453bd06ffd515e7af88d63e'), (2, 3, 0, '0000000000000000,8000000000000000,0000000000000000,3ff0000000000000,0000000000000000,0000000000000000,0000000000000000,3ff0000000000000,0000000000000000,3fc0000000000000', (12, 1, 4, 0, 0), '4bc6d766e868e962dd796bf50ecdf2aaea4b9ade0453bd06ffd515e7af88d63e'), (3, 3, 0, '0000000000000000,8000000000000000,0000000000000000,3ff0000000000000,0000000000000000,0000000000000000,0000000000000000,3ff0000000000000,0000000000000000,3fc0000000000000,0000000000000000,3ff0000000000000', (12, 1, 4, 0, 0), 'bf7bd904d0fae8419b291727ee437a69c3e85abe861ed82a433af661cbac8f90'), (4, 64, 3, '0000000000000000,0000000000000000,0000000000000000,4008000000000000,3ff0000000000000,4000000000000000,4000000000000000,c010000000000000,4008000000000000,4010000000000000,4008000000000000,c000000000000000', (195, 1, 65, 0, 0), '1675a22df51426d453c201e3c36e1b135016c1a59a02a4568a11e63a71564e72'), (5, 64, 3, '3ff0000000000000,4000000000000000,4008000000000000,4010000000000000,4014000000000000,4018000000000000,4010000000000000,4018000000000000,4020000000000000,0000000000000000,0000000000000000,3ff0000000000000,4004000000000000', (585, 3, 65, 65, 65), '896a308f22f9f7a0fd42710fc1dc9624f4ace8208f2d9d959c98abeccaaee733'), (6, 0, 0, '0000000000000000,8000000000000000,3ff0000000000000,4008000000000000,4010000000000000,4022000000000000', (15, 1, 5, 0, 0), 'aa697d93f99ef38ac3b6597de5a1b88df75158ef3f236175075b58b5fd4c2e06'))
# Select exactly one independently captured platform oracle, never a union.
import platform
if sys.platform == "win32":
    require(platform.system() == "Windows" and platform.machine().lower() in ("amd64", "x86_64")
            and platform.python_implementation() == "CPython"
            and platform.python_compiler().startswith("MSC")
            and sys.byteorder == "little" and c.sizeof(c.c_void_p) == 8,
            "preview witness platform is not qualified Windows MSVC x64")
    preview_digests = ('516e11b4a3ba726be50731fa16b9c77956f62b2921ff29c5dc55ea45b4b690d7', '516e11b4a3ba726be50731fa16b9c77956f62b2921ff29c5dc55ea45b4b690d7', 'd12405ae3fc0d966c0437c0084e17c5c62a22b695e5879c5675214ff3a2c5f89', '610f86cbe8d7dbf14ed21538ee0489c23a334eb3cc89c1384591c38382ab4779', '866247c56332ecac9b604b8673b8d04f032adae170010ecd4c3500abf9998c37', 'aa697d93f99ef38ac3b6597de5a1b88df75158ef3f236175075b58b5fd4c2e06')
elif sys.platform.startswith("linux"):
    require(sys.byteorder == "little" and c.sizeof(c.c_void_p) == 8
            and platform.machine().lower() in ("amd64", "x86_64"),
            "preview witness platform is not qualified Linux x64")
    preview_digests = tuple(row[-1] for row in preview_witnesses)
else:
    raise RuntimeError("preview witness platform is not qualified")
preview_witnesses = tuple((*row[:-1], digest) for row, digest in zip(preview_witnesses, preview_digests))
for op,segments,flags,bits,expected,digest in preview_witnesses:
    values = [struct.unpack(">d",bytes.fromhex(v))[0] for v in bits.split(",")]
    data = (c.c_double*len(values))(*values)
    meta = PreviewMetadata(17,19,(c.c_size_t*3)(23,23,23))
    require(preview_sample(op,data,len(data),segments,flags,None,0,c.byref(meta)) == -3
            and preview_meta(meta) == expected,"primitive preview size/rows " + str(op))
    n = expected[0]
    output = (c.c_double*(n+8))(*([123.]*(n+8)))
    require(preview_sample(op,data,len(data),segments,flags,output,n-1,c.byref(meta)) == -3
            and preview_meta(meta) == expected and list(output) == [123.]*(n+8),
            "primitive preview short output atomicity " + str(op))
    require(preview_sample(op,data,len(data),segments,flags,output,n+8,c.byref(meta)) == 0
            and preview_meta(meta) == expected
            and hashlib.sha256(bytes(output)[:n*8]).hexdigest() == digest
            and list(output)[n:] == [123.]*8,"primitive preview complete original bits " + str(op))
    data[0] = float("nan")
    for bad_op in (op,99):
        refused = PreviewMetadata(17,19,(c.c_size_t*3)(23,23,23))
        untouched = (c.c_double*(n+8))(*([123.]*(n+8)))
        require(preview_sample(bad_op,data,len(data),segments,flags,untouched,n+8,c.byref(refused)) == -2
                and preview_meta(refused) == (17,19,23,23,23)
                and list(untouched) == [123.]*(n+8),"primitive preview typed atomic refusal " + str(op))
class ReaderDiagnostic(c.Structure):
    _fields_ = [("code",c.c_int64),("start",c.c_size_t),("len",c.c_size_t),
                ("detail",c.c_int64),("required",c.c_size_t)]
require(c.sizeof(ReaderDiagnostic) == 40,"DXF reader diagnostic layout")
require(bind("dxf_reader24_abi_version",[],c.c_uint32)() == 1,"DXF reader owned ABI identity")
reader_decode = bind("dxf_reader24_decode",[c.c_void_p,c.c_size_t,c.c_uint32,c.c_void_p,c.c_size_t,
                                         c.POINTER(c.c_size_t),c.POINTER(ReaderDiagnostic)])
def reader_diag(diag):
    return (diag.code,diag.start,diag.len,diag.detail,diag.required)
reader_input = bytes.fromhex('300a53454354494f4e0a320a454e5449544945530a300a4c494e450a380a436166e90a31300a300a32300a300a33300a300a31310a310a32310a320a33310a330a300a454e445345430a300a454f460a')
reader_expected = bytes.fromhex('44584652323400010b0500000000000000060800000000000000656e7469746965730701000000000000000b0500000000000000060400000000000000747970650604000000000000004c494e450605000000000000006c61796572060400000000000000436166e9060500000000000000636f6c6f72030700000000000000060200000000000000703008000000000000000000000000000000000000000000000000060200000000000000703108000000000000f03f000000000000004000000000000008400606000000000000006c61796572730b00000000000000000611000000000000006c617965725f747275655f636f6c6f72730b0000000000000000060800000000000000696e73756e69747300060700000000000000736b69707065640b0000000000000000')
written,diag = c.c_size_t(777),ReaderDiagnostic()
require(reader_decode(reader_input,len(reader_input),4300,None,0,c.byref(written),c.byref(diag)) == 1
        and written.value == 777 and reader_diag(diag) == (1,0,0,0,len(reader_expected)),
        "DXF reader typed capacity retry")
output = c.create_string_buffer(b"!"*(len(reader_expected)+16),len(reader_expected)+16)
require(reader_decode(reader_input,len(reader_input),4300,output,len(output),c.byref(written),c.byref(diag)) == 0
        and written.value == len(reader_expected) and reader_diag(diag) == (0,0,0,0,0)
        and output.raw == reader_expected+b"!"*16,"DXF reader complete Latin1/typed entity and order")
for data,status,start,length,detail in (
    (b"AutoCAD Binary DXF\r\n\x1a\0",-100,0,0,0),
    (b"0\nSECTION\n2",-101,0,0,0),
    (b"bad\nvalue\n",-110,0,3,0),
    (reader_input.replace(b"10\n0\n",b"10\nnope\n"),-111,reader_input.index(b"10\n0\n")+3,4,0),
    (b"0\nSECTION\n2\nENTITIES\n0\nLINE\n0\nENDSEC\n0\nEOF\n",-112,0,0,10)):
    written.value = 777
    refused = c.create_string_buffer(b"!"*1024,1024)
    require(reader_decode(data,len(data),4300,refused,1024,c.byref(written),c.byref(diag)) == status
            and reader_diag(diag) == (status,start,length,detail,0)
            and written.value == 777 and refused.raw == b"!"*1024,"DXF reader typed diagnostic/payload atomicity " + str(status))
written.value = 777
require(reader_decode(None,512*1024*1024+1,4300,None,0,c.byref(written),c.byref(diag)) == -3
        and reader_diag(diag) == (-3,0,0,0,0) and written.value == 777,"DXF reader bounded resource refusal")
print(json.dumps({"retirement_abi":24,"retirement_exports":["narwhal_pure_core_"+s for s in
    ("stl_size_owned","stl_encode_owned","stl_file_size","stl_file_decode",
     "stl_import_mesh_decode","stl_normal","interpolate_helper_v1","catenary_plan_v2","obj_encode_into",
     'vector_encode_v1',
     'vector_len_v1',
     'vector_copy_v1',
     'vector_free_v1',
     'vector_validate_v1',
     'dxf_owned_abi_version',
     'dxf_owned_colors',
     'dxf_owned_units',
     'dxf_owned_capacity',
     'dxf_owned_encode',
     'tween_plan_v1',
     'tween_knots_v1',
     'tween_lengths_v1',
     'ear_clip_display',
     'earclip_signed_area_v1',
     'control_point_curve_preview',
     'preview_sample_v1',
     'dxf_reader24_abi_version',
     'dxf_reader24_decode')]}))
'''


def probe_pure_core_retirement(path: Path, python: Path | str = sys.executable, *, env=None) -> dict:
    """Require actual STL/helper/catenary/OBJ results and failure atomicity."""
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"missing regular pure-core retirement library: {path}")
    try:
        # The exact probe exceeds Windows' command-line limit. Close the file
        # before executing it so Windows can open it, and clean up on all exits.
        with tempfile.TemporaryDirectory(prefix="narwhal-retirement-probe-") as directory:
            script = Path(directory) / "probe.py"
            script.write_bytes(PURE_CORE_RETIREMENT_PROBE.encode("utf-8"))
            completed = subprocess.run(
                [str(python), "-I", "-B", str(script), str(path.resolve())],
                env=env, text=True, capture_output=True, check=True, timeout=30)
            identity = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError(f"cannot verify pure-core required retirement ABI/semantics: {getattr(error, 'stderr', '') or error}") from error
    if identity != PURE_CORE_RETIREMENT_IDENTITY:
        raise ValueError(f"pure-core retirement ABI/export evidence differs: {identity!r}")
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
    modules = set(RUST_REQUIRED_MODULE_FILES)
    missing_modules = {name for name in modules
                       if not (crate / name.removeprefix("native/pure_core/")).is_file()}
    if actual != expected or not (expected | modules).issubset(public) or missing_modules:
        raise GateError("Rust integration source/fixture public closure differs: "
                        + repr(sorted((actual ^ expected) | ((expected | modules) - set(public))
                                      | missing_modules)))
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
        retirement_identity = probe_pure_core_retirement(lib_path)
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
                "retirement": retirement_identity,
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
