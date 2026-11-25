"""Fast parser/STL/helper packaging contracts; no Cargo, GUI or product imports.

ctypes fixtures model only this smoke ABI, not a parser. Negative fixtures and
in-memory guard mutants must be rejected. Real-library qualification is separate.
"""
from __future__ import annotations

import ast
import hashlib
import functools
import importlib.util
import os
from contextlib import redirect_stdout
import ctypes as c
import io
import inspect
import json
import struct
import textwrap
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_pure_core as gate
import build_linux_native as linux_build
import build_macos_native as mac_build
import assemble_linux_release as linux_assemble
import assemble_macos_release as mac_assemble
import assemble_windows_release as win_assemble
import verify_linux_release as linux_verify
import verify_windows_release as win_verify


ROUND23_ADDED_EXPORTS = tuple("narwhal_pure_core_"+name for name in ('vector_encode_v1', 'vector_len_v1', 'vector_copy_v1', 'vector_free_v1', 'vector_validate_v1', 'dxf_owned_abi_version', 'dxf_owned_colors', 'dxf_owned_units', 'dxf_owned_capacity', 'dxf_owned_encode', 'tween_plan_v1', 'tween_knots_v1', 'tween_lengths_v1'))
ROUND24_ADDED_EXPORTS = tuple("narwhal_pure_core_"+name for name in ('ear_clip_display', 'earclip_signed_area_v1', 'control_point_curve_preview', 'preview_sample_v1', 'dxf_reader24_abi_version', 'dxf_reader24_decode'))
ROUND24_NATIVE_FILES = {
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
}
ROUND24_MODULES = {"native/pure_core/src/preview_primitives.rs"}
NEAREST25_ADDED_EXPORTS = tuple("narwhal_pure_core_"+name for name in
    ("nearest_on_nurbs", "nearest_on_callbacks_v1"))
NEAREST25_NATIVE_FILES = {
    'native/pure_core/tests/nearest25_api.h',
    'native/pure_core/tests/nearest25_golden.rs',
    'native/pure_core/tests/fixtures/nearest25_callbacks_linux.tsv',
    'native/pure_core/tests/fixtures/nearest25_host_linux.jsonl',
    'native/pure_core/tests/fixtures/nearest25_provenance.json',
}


class Reply(c.Structure):
    _fields_ = [("code", c.c_int32), ("reserved", c.c_uint32),
                ("value", c.c_double), ("start", c.c_uint32), ("end", c.c_uint32)]


def library(mutant=""):
    """Independent fixed witnesses; validate flags/lengths/layout on every call."""
    def parse(text, length, model, model_length, is_length, unicode, out, size):
        assert size == 24 and unicode == 160
        word = "".join(chr(text[i]) for i in range(length))
        unit = "".join(chr(model[i]) for i in range(model_length))
        code, value = {
            ("1/2", "", 0): (0, 0.5),
            ("2cm", "Millimeters", 1): (0, 20.0),
            ("bad", "", 0): (-2, 0.0),
        }[(word, unit, is_length)]
        if mutant == "no-write":
            return 0
        reply = c.cast(out, c.POINTER(Reply)).contents
        reply.code = 0 if mutant == "accept-invalid" else code
        reply.reserved = 1 if mutant == "reserved" else 0
        reply.value = value + 1 if mutant == "wrong-value" else value
        reply.start, reply.end = 0, length
        return 1 if mutant == "transport" else 0
    def calc(text, length, model, model_length, model_kind, unicode, out, size):
        class CReply(c.Structure):
            _fields_ = [("code", c.c_uint32), ("aux", c.c_uint32), ("value", c.c_double),
                        ("start", c.c_uint32), ("end", c.c_uint32), ("reserved", c.c_uint64)]
        assert size == 32 and unicode == 151 and model_kind == 1 and model_length == 0
        word = ''.join(chr(text[i]) for i in range(length))
        code, value, start, end = {"1+2": (0, 3., 0, 0), "sqrt(-1)": (18, 0., 0, 4)}[word]
        reply = c.cast(out, c.POINTER(CReply)).contents
        reply.code, reply.value, reply.start, reply.end, reply.aux, reply.reserved = code, value, start, end, 0, 0
        if mutant == "calc-value": reply.value += 1
        if mutant == "calc-error": reply.code = 0
        return 0
    def bearing(text, length, mode, unicode, out, size):
        class BReply(c.Structure):
            _fields_ = [("code", c.c_uint32), ("reserved", c.c_uint32), ("value", c.c_double),
                        ("distance", c.c_double), ("start", c.c_uint32), ("end", c.c_uint32)]
        assert size == 32 and unicode == 151 and mode == 0
        word = ''.join(chr(text[i]) for i in range(length))
        code, value, start, end = {"N30E": (0, 60., 0, 0), "NbadE": (3, 0., 1, 4)}[word]
        reply = c.cast(out, c.POINTER(BReply)).contents
        reply.code, reply.value, reply.start, reply.end, reply.distance, reply.reserved = code, value, start, end, 0, 0
        if mutant == "bearing-value": reply.value += 1
        if mutant == "bearing-error": reply.code = 0
        return 0
    def checked_plan(text, length, output, capacity, needed):
        code, witness = {b"'_Box": (-4, b'"Box"'), b"=value": (-5, b''),
                                 b"!=-15": (-5, b''),
                                 b"!_Mode=x": (0, b'[{"action":"option","name":"Mode","value":"x","prefixes":["!","_"]}]')}[text[:length]]
        if mutant == 'planner-option-cancel' and text[:length] == b'!_Mode=x':
            witness = b'[{"action":"cancel","prefixes":["!"]},' + witness[1:]
        if mutant == 'planner-option-error' and text[:length] == b'!=-15':
            code, witness = 0, b'[{"action":"cancel"}]'
        c.memmove(output, witness, len(witness))
        c.cast(needed, c.POINTER(c.c_int64))[0] = len(witness)
        return 0 if mutant == "planner-error" else code
    def coords(text, length, model, model_length, model_kind, mode, unicode, out, size):
        class CReply(c.Structure):
            _fields_ = [("code", c.c_uint32), ("aux", c.c_uint32), ("value", c.c_double), ("start", c.c_uint32), ("end", c.c_uint32), ("reserved", c.c_uint64)]
        class BReply(c.Structure):
            _fields_ = [("code", c.c_uint32), ("reserved", c.c_uint32), ("value", c.c_double), ("distance", c.c_double), ("start", c.c_uint32), ("end", c.c_uint32)]
        class Reply(c.Structure):
            _fields_ = [("code", c.c_uint32), ("flags", c.c_uint32), ("values", c.c_double*3), ("start", c.c_uint32), ("end", c.c_uint32), ("calc",CReply),("bearing",BReply)]
        assert size == 104 and mode == 0 and unicode == 151
        word = ''.join(chr(text[i]) for i in range(length))
        c.memset(out,0,size)
        result = c.cast(out,c.POINTER(Reply)).contents
        if word == 'rw1,2': result.flags=3; result.values[:]=[1.,2.,0.]
        else: assert word == '5'; result.code=10; result.end=1
        if mutant == 'coords-value': result.values[0] += 1
        if mutant == 'coords-error': result.code=0
        return 0
    def split(text,length,sep,kind,spans,capacity,count):
        assert ''.join(chr(text[i]) for i in range(length)) == 'pow(2,3),4'
        assert (sep,kind,capacity)==(44,1,4)
        spans[:]=[0,8,9,10]
        c.cast(count,c.POINTER(c.c_uint32))[0]=2
        return 0
    def norm(values,count,mode,out):
        assert count==1 and mode==6
        c.cast(out,c.POINTER(c.c_double))[0]=.702125355913066 + (1 if mutant=='norm-value' else 0)
        return 0
    def join(n,starts,ends,tol,off,length,closed,*rest):
        axes, members, reverse, chains, total = rest[:6], rest[6], rest[7], rest[8], rest[9]
        assert n==2
        c.cast(chains,c.POINTER(c.c_int64))[0] = 2 if mutant=='join-boundary' or tol < .702125355913066 else 1
        c.cast(total,c.POINTER(c.c_int64))[0]=2
        members[:]=[0,1]
        return 0
    def macro_payload(text, length, flag):
        assert flag == 0
        witness = {b"!123": b'[{"kind":"text","value":"!123"}]',
                   b"!=-15": b'[{"kind":"option","name":"","value":"15"}]',
                   b"!_Mode=x": b'[{"kind":"option","name":"Mode","value":"x"}]',
                   b"!_Box": b'[{"kind":"cancel"},{"kind":"command","name":"Box","dash":false,"underscore":true,"transparent":false}]',
                   b"a\x1cb": b'[{"kind":"text","value":"a"},{"kind":"text","value":"b"}]'}[text[:length]]
        if mutant == 'token-option-cancel' and text[:length] in (b'!=-15', b'!_Mode=x'):
            witness = b'[{"kind":"cancel"},' + witness[1:]
        if mutant == 'token-command-no-cancel' and text[:length] == b'!_Box':
            witness = witness.replace(b'{"kind":"cancel"},', b'')
        return witness
    def macro_encode(text, length, flag, output, capacity):
        witness = macro_payload(text, length, flag)
        assert capacity == len(witness)
        c.memmove(output, witness, len(witness))
        return 0
    def resolve(text, length):
        return {b" Fractional ": 1, b"bogus": 13}[text[:length]]
    def fmt(value, mode, precision, context, output, capacity, needed):
        witness = {(1.75, 1, 16, 28): b"1-3/4", (0.0936, 1, 16, 2): b"1/8",
                   (30.5, 2, 16, 28): b"2'-6 1/2\""}[(value, mode, precision, context)]
        c.memmove(output, witness, len(witness))
        c.cast(needed, c.POINTER(c.c_size_t))[0] = len(witness)
        return 0
    def widths(text, length, output, capacity, needed):
        assert text[:length] == b"2\n1\n2"
        witness = struct.pack("<IddI", 2, 1.0, 2.0, 0)
        c.memmove(output, witness, len(witness))
        c.cast(needed, c.POINTER(c.c_size_t))[0] = len(witness)
        return 0
    def svg(text, length, output, capacity, offsets, ocap, flags, fcap, report, error):
        assert (capacity, ocap, fcap) == (8,1,1)
        word = ''.join(chr(text[i]) for i in range(length))
        if word == 'M0 0 L3 0':
            output[:] = [0.,0.,1.,0.,2.,0.,3.,0.]
            report[:] = [8,1]
            if mutant == 'svg-geometry': output[2] = 9
            return 0
        assert word == 'M0'
        fields = c.cast(error,c.POINTER(c.c_uint32))
        fields[0],fields[1],fields[2],fields[3] = 3,77,2,1
        return 0 if mutant == 'svg-error' else 2
    def alias(text,length,unicode,out,size,spans,capacity):
        assert (unicode,size,capacity) == (151,28,4)
        word = ''.join(chr(text[i]) for i in range(length))
        reply = c.cast(out,c.POINTER(c.c_uint32))
        values = [0,0,0,1,2,6,2] if word == "'_Zoom _Extents __" else [1,0,0,0,0,0,0]
        if mutant == 'alias-prefix': values[3] = 0
        if mutant == 'alias-error': values[0] = 0
        for i,v in enumerate(values): reply[i] = v
        return 0
    def alias_options(text,length,count,spans,capacity):
        assert (count,capacity) == (2,4)
        spans[:] = [8,15,18,18]
        if mutant == 'alias-options': spans[0] = 7
        return 0
    def fit(samples,params,sample_count,n,degree,closed,tangent,ends,ctrl,knots,count):
        assert tangent == 0
        c.cast(count,c.POINTER(c.c_size_t))[0] = 0
        expected = {(3,5,1,21):-2, (1366,3,1,21):-5, (4,3,0,1):-4}.get((n,degree,closed,sample_count))
        if expected is not None:
            if mutant == 'rebuild-refusal-write': ctrl[0] = 0
            return 0 if mutant == 'rebuild-refusal' else expected
        assert (sample_count,n,degree,closed) == (21,4,3,0)
        assert list(ends) == [0,0,0,3,6,-3]
        ctrl[:12] = [0,0,0,1,2,-1,2,4,-2,3,6,-3]
        knots[:6] = [0,0,0,1,1,1]
        c.cast(count,c.POINTER(c.c_size_t))[0] = 3 if mutant == 'rebuild-malformed' else 4
        if mutant == 'rebuild-value': ctrl[4] = 9
        if mutant == 'rebuild-nan': ctrl[4] = float('nan')
        if mutant == 'rebuild-knots': knots[3] = 2
        return 0
    def blend(p0,l0,p1,l1,k0,k1,scale0,scale1,out,capacity):
        assert (l1,k0,k1,scale1) == (6,1,1,1.)
        if mutant == 'blend-refusal-write': out[0] = 0
        if capacity == 29:
            return 0 if mutant == 'blend-refusal' else -2
        if l0 == 5 or scale0 != scale0:
            return -2 if mutant == 'blend-input' else -1
        assert list(p0) == [0,0,0,3,6,0] and list(p1) == [6,3,0,3,-3,0]
        out[:] = [0.,0.,0.,2.,4.,0.,5.,4.,0.,6.,3.,0.]+[0.]*18
        if mutant == 'blend-value': out[4] = 9
        if mutant == 'blend-malformed': out[29] = 1
        return 0
    def unit_table(names,capacity,values,vcap,nlen,count):
        payload = b"Angstroms\nNanometers\nMicrons\nMillimeters\nCentimeters\nDecimeters\nMeters\nDekameters\nHectometers\nKilometers\nMegameters\nGigameters\nMicroinches\nMils\nInches\nFeet\nYards\nMiles\nPrinterPoints\nPrinterPicas\nNauticalMiles\nAstronomicalUnits\nLightYears\nParsecs"
        scales = [1e-7,1e-6,1e-3,1.,10.,100.,1000.,10000.,100000.,1000000.,1e9,1e12,25.4e-6,25.4e-3,25.4,304.8,914.4,1609344.,25.4/72.,25.4/12.,1852000.,1.495978707e11,9.4607304725808e15,3.085677581491367e16]
        c.cast(nlen,c.POINTER(c.c_uint64))[0] = len(payload)
        c.cast(count,c.POINTER(c.c_uint64))[0] = 25 if mutant == 'unit-table-count' else 24
        if capacity == 0:
            assert vcap == 0
            return 0 if mutant == 'unit-table-query' else -3
        assert capacity == len(payload) and vcap == 24
        names[:] = payload
        values[:] = scales
        if mutant == 'unit-table-name': names[0] = 88
        if mutant == 'unit-table-order': values[0],values[1] = values[1],values[0]
        if mutant == 'unit-table-nan': values[0] = float('nan')
        return 1 if mutant == 'unit-table-status' else 0
    def unit_parse(text,length,uid,mask,raw,capacity,nlen):
        word = text[:length]
        if capacity == 1 or word == b"\xff":
            if mutant == 'unit-parse-refusal-write': raw[0] = 0
            return 2 if mutant == 'unit-parse-abi' else 5
        assert capacity == 32
        status,i,m,payload = {
            ' Km '.encode(): (0,9,0,'Km'.encode()),
            b'm i': (3,0,143372,b'm i'),
            b'bogus': (2,0,0,b'bogus'), b' ': (1,0,0,b''),
        }[word]
        c.cast(uid,c.POINTER(c.c_uint32))[0] = i + (1 if mutant == 'unit-parse-id' else 0)
        c.cast(mask,c.POINTER(c.c_uint32))[0] = m + (1 if mutant == 'unit-parse-mask' else 0)
        c.cast(nlen,c.POINTER(c.c_size_t))[0] = len(payload) + (1 if mutant == 'unit-parse-span' else 0)
        for j,v in enumerate(payload): raw[j] = v
        if mutant == 'unit-parse-raw': raw[0] = 0
        return 0 if mutant == 'unit-parse-status' else status
    def unit_angle(text,length,out):
        if text[:length] == b'bad':
            if mutant == 'unit-angle-write': c.cast(out,c.POINTER(c.c_double))[0] = 0
            return 0 if mutant == 'unit-angle-refusal' else 2
        import math
        assert text[:length] == b'gradians'
        c.cast(out,c.POINTER(c.c_double))[0] = math.nextafter((1./100.)*(math.pi/2.),0.) if mutant == 'unit-angle-value' else (1./100.)*(math.pi/2.)
        return 0
    def unit_scale(value,sf,st,out):
        import math
        if out is None:
            return 0 if mutant == 'unit-scale-abi' else 5
        answer = c.cast(out,c.POINTER(c.c_double))
        if st == 0:
            if mutant == 'unit-zero-write': answer[0] = 0
            return 0 if mutant == 'unit-zero-status' else 6
        result = value*(sf/st) if mutant == 'unit-scale-order' else (value*sf)/st
        if mutant == 'unit-scale-nonfinite' and not math.isfinite(result): result = 0
        answer[0] = result
        return 1 if mutant == 'unit-scale-status' else 0
    def interpolate(points,n,degree,style,controls,cc,knots,kc,od,on,ok):
        assert (n,degree,style,cc,kc) == (8,5,1,24,14)
        c.cast(od,c.POINTER(c.c_uint64))[0] = 5
        c.cast(on,c.POINTER(c.c_uint64))[0] = 8
        c.cast(ok,c.POINTER(c.c_uint64))[0] = 14
        knots[6] = float.fromhex('0x1.47ae147ae147bp-2')
        knots[7] = float.fromhex('0x1.d70a3d70a3d70p-2')
        if mutant == 'interpolate-sum': knots[6] = float.fromhex('0x1.47ae147ae147ap-2')
        return 1 if mutant == 'interpolate-status' else 0
    result = SimpleNamespace(
        narwhal_pure_core_abi_version=Mock(return_value=9 if mutant == "old-abi" else 1),
        narwhal_pure_core_parse_abi=Mock(return_value=1 if mutant == "parser-abi" else 2),
        narwhal_pure_core_parse_v2=Mock(side_effect=parse),
        narwhal_pure_core_printwidths=Mock(side_effect=widths),
        narwhal_pure_core_macro_size=Mock(side_effect=lambda *args: len(macro_payload(*args))),
        narwhal_pure_core_macro_encode=Mock(side_effect=macro_encode),
        narwhal_pure_core_unitformat_resolve_mode=Mock(side_effect=resolve),
        narwhal_pure_core_unitformat_format=Mock(side_effect=fmt),
        narwhal_pure_core_calc_abi2=Mock(return_value=1 if mutant == "calc-abi" else 2),
        narwhal_pure_core_calc_v2=Mock(side_effect=calc),
        narwhal_pure_core_bearing_abi2=Mock(return_value=1 if mutant == "bearing-abi" else 2),
        narwhal_pure_core_bearing_v2=Mock(side_effect=bearing),
        narwhal_pure_core_macro_plan_checked=Mock(side_effect=checked_plan),
        narwhal_pure_core_coords_owned_abi1=Mock(return_value=1),
        narwhal_pure_core_coords_owned_v1=Mock(side_effect=coords),
        narwhal_pure_core_coords_split_v1=Mock(side_effect=split),
        narwhal_pure_core_norm_v1=Mock(side_effect=norm),
        narwhal_pure_core_joinchain=Mock(side_effect=join),
        narwhal_pure_core_svgpath_owned_version=Mock(return_value=1 if mutant == 'svg-abi' else 14),
        narwhal_pure_core_svgpath_owned=Mock(side_effect=svg),
        narwhal_pure_core_alias_identity=Mock(return_value=14 if mutant == 'alias-abi' else 1),
        narwhal_pure_core_alias_parse=Mock(side_effect=alias),
        narwhal_pure_core_alias_options=Mock(side_effect=alias_options),
        narwhal_pure_core_rebuild_fit=Mock(side_effect=fit),
        narwhal_pure_core_blend_bezier_points=Mock(side_effect=blend),
        narwhal_pure_core_unit_table=Mock(side_effect=unit_table),
        narwhal_pure_core_unit_ops_parse=Mock(side_effect=unit_parse),
        narwhal_pure_core_unit_ops_angle=Mock(side_effect=unit_angle),
        narwhal_pure_core_unit_ops_scale=Mock(side_effect=unit_scale),
        narwhal_pure_core_interpolate=Mock(side_effect=interpolate))
    if mutant in gate.PURE_CORE_PARSE_EXPORTS:
        delattr(result, mutant)
    return result


def execute_probe(mutant="", code=None):
    output = io.StringIO()
    with patch.object(c, "CDLL", return_value=library(mutant)), \
            patch.object(sys, "argv", ["probe", "fixture"]), redirect_stdout(output):
        exec(compile(code or gate.PURE_CORE_PARSE_PROBE, "<parser-probe>", "exec"), {})
    return json.loads(output.getvalue())


POLYLINE_EXPORTS = ("narwhal_pure_core_curve_curve_record_hits", "narwhal_pure_core_nearest_on_polyline", "narwhal_pure_core_polyline_samples", "narwhal_pure_core_polycurve_samples", "narwhal_pure_core_remove_join_cv_v1",
                    "narwhal_pure_core_offset_fit", "narwhal_pure_core_surface_offset")


def pe_fixture(*, parser_abi=2, missing="", svg_abi=14, alias_abi=1, dxf_abi=2, reader_abi=1):
    """Minimal PE export/import tables; data fixture, never executable native code."""
    data = bytearray(8192)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3c, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", data, 0x84, 0x8664, 1, 0, 0, 0, 240, 0x2002)
    optional = 0x98
    struct.pack_into("<H", data, optional, 0x20b)
    struct.pack_into("<I", data, optional + 60, 0x200)
    struct.pack_into("<II", data, optional + 112, 0x1200, 0x600)
    struct.pack_into("<II", data, optional + 120, 0x2600, 40)
    struct.pack_into("<IIII", data, optional + 240 + 8, 0x1e00, 0x1000, 0x1e00, 0x200)
    names = [name for name in ("narwhal_pure_core_abi_version", *gate.PURE_CORE_PARSE_EXPORTS, *gate.PURE_CORE_RETIREMENT_EXPORTS, *POLYLINE_EXPORTS) if name != missing]
    assert len(names) <= 128, "PE fixture export table capacity exceeded"
    offset = lambda rva: rva - 0x1000 + 0x200
    struct.pack_into("<IIIII", data, offset(0x1200) + 20,
                     len(names), len(names), 0x1240, 0x1440, 0x1640)
    cursor = 0x1800
    for i, name in enumerate(names):
        struct.pack_into("<I", data, offset(0x1240) + i * 4, 0x2800 + i * 16)
        struct.pack_into("<I", data, offset(0x1440) + i * 4, cursor)
        struct.pack_into("<H", data, offset(0x1640) + i * 2, i)
        encoded = name.encode() + b"\0"
        data[offset(cursor):offset(cursor) + len(encoded)] = encoded
        cursor += len(encoded)
        assert cursor <= 0x2600, "PE export names overlap the import table"
        value = parser_abi if name == "narwhal_pure_core_parse_abi" else (2 if name in {"narwhal_pure_core_calc_abi2", "narwhal_pure_core_bearing_abi2"} else 1)
        if name == "narwhal_pure_core_svgpath_owned_version": value = svg_abi
        if name == "narwhal_pure_core_alias_identity": value = alias_abi
        if name == "narwhal_pure_core_dxf_owned_abi_version": value = dxf_abi
        if name == "narwhal_pure_core_dxf_reader24_abi_version": value = reader_abi
        data[offset(0x2800) + i * 16:offset(0x2800) + i * 16 + 6] = b"\xb8" + struct.pack("<I", value) + b"\xc3"
    struct.pack_into("<IIIII", data, offset(0x2600), 0x2680, 0, 0, 0x2640, 0x2680)
    data[offset(0x2640):offset(0x2640) + 13] = b"kernel32.dll\0"
    struct.pack_into("<Q", data, offset(0x2680), (1 << 63) | 1)
    return bytes(data)


class ParserProbeTests(unittest.TestCase):
    def test_fixed_abi_and_real_semantic_calls(self):
        self.assertEqual(execute_probe(), gate.PURE_CORE_PARSE_IDENTITY)
        self.assertEqual(gate.ABI_VERSION, 1)

    def test_missing_exports_versions_and_semantic_mutants(self):
        for mutant in (*gate.PURE_CORE_PARSE_EXPORTS, "parser-abi", "old-abi",
                       "wrong-value", "accept-invalid", "transport", "reserved", "no-write",
                       "calc-value", "calc-error", "bearing-value", "bearing-error",
                       "planner-error", "planner-option-cancel", "planner-option-error",
                                              "token-option-cancel", "token-command-no-cancel", "calc-abi", "bearing-abi",
                       "coords-value", "coords-error", "norm-value", "join-boundary",
                       "svg-abi", "alias-abi", "svg-geometry", "svg-error",
                       "alias-prefix", "alias-error", "alias-options",
                       "rebuild-malformed", "rebuild-value", "rebuild-nan", "rebuild-knots", "rebuild-refusal", "rebuild-refusal-write",
                       "blend-value", "blend-malformed", "blend-input", "blend-refusal", "blend-refusal-write",
                       "unit-table-count", "unit-table-query", "unit-table-name", "unit-table-order", "unit-table-nan", "unit-table-status",
                       "unit-parse-refusal-write", "unit-parse-abi", "unit-parse-mask", "unit-parse-id", "unit-parse-span", "unit-parse-raw", "unit-parse-status",
                       "unit-angle-write", "unit-angle-refusal", "unit-angle-value",
                       "unit-scale-abi", "unit-scale-order", "unit-scale-nonfinite", "unit-scale-status", "unit-zero-write", "unit-zero-status",
                       *(["interpolate-sum", "interpolate-status"] if sys.platform.startswith("linux") else [])):
            with self.subTest(mutant=mutant), self.assertRaises((AttributeError, RuntimeError)):
                execute_probe(mutant)
        self.assertEqual(execute_probe(), gate.PURE_CORE_PARSE_IDENTITY)

    def test_tests_detect_disabled_version_and_semantic_guards(self):
        for original, changed, mutant in (
            ("if fn() != expected:", "if False:", "parser-abi"),
            ("if status != 0 or reply.code != code or reply.reserved != 0 or reply.value != value:",
             "if False:", "wrong-value"),
        ):
            self.assertIn(original, gate.PURE_CORE_PARSE_PROBE)
            broken = gate.PURE_CORE_PARSE_PROBE.replace(original, changed)
            # This is the RED assertion: the negative contract fails on a disabled guard.
            with self.assertRaises(AssertionError):
                with self.assertRaises(RuntimeError):
                    execute_probe(mutant, broken)
            with self.assertRaises(RuntimeError):
                execute_probe(mutant)

    def test_owned_semantic_guards_are_mutation_sensitive(self):
        guards = (
                    ('planner-option-cancel', 'if checked_plan(text,'),
                    ('planner-option-error', 'if checked_plan(text,'),
                    ('token-option-cancel', 'if macro_encode(text,'),
                    ('token-command-no-cancel', 'if macro_encode(text,'),
            ('unit-table-query', 'if status != -3 or (name_len.value'),
            ('unit-table-order', 'if status != 0 or (name_len.value'),
            ('unit-parse-status', 'if status != expected_status'),
            ('unit-parse-refusal-write', 'if status != 5 or (uid.value'),
            ('unit-angle-value', 'if status != 0 or factor.value'),
            ('unit-angle-write', 'if status != 2 or factor.value'),
            ('unit-scale-order', 'if status != 0 or not (math.isnan'),
            ('unit-zero-write', 'if status != 6 or answer.value'),
            ('unit-scale-abi', 'if scale(1.,2.,3.,None)'),
            ('rebuild-value', 'if status != 0 or count.value'),
            ('rebuild-refusal', 'if status != expected or count.value'),
            ('norm-value', 'if norm(row, 1, 6,'),
            ('blend-value', 'if status != 0 or list(output)'),
            ('blend-refusal', 'if status != expected or list(output)'),
            ('svg-geometry', 'if status != 0 or list(report)'),
            ('svg-error', 'elif status != 2 or (error.kind'),
            ('alias-prefix', 'if alias(points,len(text)'),
            ('alias-options', 'if options(points,len(text)'),
        )
        if sys.platform.startswith("linux"):
            guards += (('interpolate-sum', 'if status != 0 or (degree.value'),)
        for mutant, prefix in guards:
            with self.subTest(mutant=mutant):
                line = next(line for line in gate.PURE_CORE_PARSE_PROBE.splitlines() if line.strip().startswith(prefix))
                replacement = line[:len(line)-len(line.lstrip())] + ('elif False:' if prefix.startswith('elif') else 'if False:')
                broken = gate.PURE_CORE_PARSE_PROBE.replace(line, replacement)
                # The negative test itself must turn RED when its guard is removed.
                with self.assertRaises(AssertionError):
                    with self.assertRaises(RuntimeError):
                        execute_probe(mutant, broken)
                with self.assertRaises(RuntimeError):
                    execute_probe(mutant)

    def test_child_is_isolated_bounded_and_bad_evidence_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "core.so"
            path.write_bytes(b"ABI fixture only")
            with patch.object(gate.subprocess, "run", return_value=SimpleNamespace(
                    stdout=json.dumps(gate.PURE_CORE_PARSE_IDENTITY))) as run:
                self.assertEqual(gate.probe_pure_core_parser(path), gate.PURE_CORE_PARSE_IDENTITY)
                args, kwargs = run.call_args
                self.assertEqual(args[0][1:4], ["-I", "-B", "-c"])
                self.assertEqual(args[0][-1], str(path))
                self.assertEqual(kwargs["timeout"], 30)
                self.assertTrue(kwargs["check"])
            for result in ({}, {**gate.PURE_CORE_PARSE_IDENTITY, "parse_abi": 1}):
                with patch.object(gate.subprocess, "run", return_value=SimpleNamespace(stdout=json.dumps(result))):
                    with self.assertRaisesRegex(ValueError, "evidence differs"):
                        gate.probe_pure_core_parser(path)
            with patch.object(gate.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "probe", stderr="missing parser")):
                with self.assertRaisesRegex(ValueError, "missing parser"):
                    gate.probe_pure_core_parser(path)
            path.unlink()
            with self.assertRaisesRegex(ValueError, "missing regular"):
                gate.probe_pure_core_parser(path)

    def test_source_gate_cannot_skip_parser(self):
        with patch.object(gate, "probe_pure_core_parser", side_effect=ValueError("parser refused")) as probe:
            with self.assertRaisesRegex(gate.GateError, "parser refused"):
                gate.step_direct_abi(Path("core.so"))
            probe.assert_called_once()
        self.assertIn("spike/test_numparse_owned.py", gate.PURE_CORE_PYTESTS)
        self.assertTrue((ROOT / "spike/fixtures/numparse_golden.json").is_file())


ROUND24_READER_INPUT = bytes.fromhex('300a53454354494f4e0a320a454e5449544945530a300a4c494e450a380a436166e90a31300a300a32300a300a33300a300a31310a310a32310a320a33310a330a300a454e445345430a300a454f460a')
ROUND24_READER_WIRE = bytes.fromhex('44584652323400010b0500000000000000060800000000000000656e7469746965730701000000000000000b0500000000000000060400000000000000747970650604000000000000004c494e450605000000000000006c61796572060400000000000000436166e9060500000000000000636f6c6f72030700000000000000060200000000000000703008000000000000000000000000000000000000000000000000060200000000000000703108000000000000f03f000000000000004000000000000008400606000000000000006c61796572730b00000000000000000611000000000000006c617965725f747275655f636f6c6f72730b0000000000000000060800000000000000696e73756e69747300060700000000000000736b69707065640b0000000000000000')


@functools.lru_cache(maxsize=1)
def round24_preview_answers():
    # Fixed authored original data, not an implementation of any sampler.
    wanted = {932, 940, 976, 50, 54, 55}
    result = {}
    name = 'preview24_windows_msvc.tsv' if sys.platform == 'win32' else 'preview24_linux.tsv'
    with (ROOT/'native/pure_core/tests/data'/name).open() as handle:
        for index,line in enumerate(handle):
            if index not in wanted: continue
            op,n,flags,bits,lengths,out = line.rstrip('\n').split('\t')
            values = [struct.unpack('>d',bytes.fromhex(v))[0] for v in bits.split(',')]
            lengths = tuple(int(v) for v in lengths.split(','))
            payload = b''.join(bytes.fromhex(v)[::-1] for v in out.split(','))
            result[int(op)] = {'segments':int(n),'flags':int(flags),'input':values,
                'metadata':(len(payload)//8,len(lengths),*lengths,*([0]*(3-len(lengths)))),
                'output':payload}
    assert set(result)=={1,2,3,4,5,6}
    return result


def _nearest_scalar_mock(args):
    # Correct tiny-line answer; wrong- mutant swaps in a status-only impl.
    out, at_end = args[8], args[9]
    if args[2] != 2: return -2  # reject invalid knot_count
    out[0], out[1], out[2], out[3], out[4] = 0.25, 0.25, 0., 0., 2.
    at_end[0] = 0
    return 0


def _nearest_cb_mock(args):
    # Correct tiny-line answer; wrong- mutant swaps in a status-only impl.
    out, at_end = args[9], args[10]
    if args[4] != 1: return -2  # reject invalid span_count
    out[0], out[1], out[2], out[3], out[4] = 0.25, 0.25, 0., 0., 2.
    at_end[0] = 0
    return 0
def retirement_library(mutant=""):
    """Independent fixed ABI replies; no parser/solver/codec implementation."""
    xyz = [0.,0.,0.,1.,0.,0.,0.,1.,0.]
    binary = b"probe".ljust(80,b" ") + struct.pack("<I12fH",1,0,0,1,*xyz,0)
    ascii_stl = (b"solid probe\n  facet normal 0 0 1\n  outer loop\n"
                 b"    vertex 0 0 0\n    vertex 1 0 0\n    vertex 0 1 0\n"
                 b"  endloop\n  endfacet\nendsolid probe\n")
    def normal(data,out):
        assert list(data) == xyz
        out[:] = [0.,0.,1.]
        return 0
    def size(data,count,name,length,mode):
        assert list(data) == xyz and count == 1 and name == b"probe" and length == 5
        return len(binary if mode else ascii_stl)
    def encode(data,count,name,length,mode,out,capacity):
        expected = binary if mode else ascii_stl
        if capacity < len(expected):
            if mutant == "encode-partial": c.memset(out,0,1)
            return -3
        c.memmove(out,expected,len(expected))
        return 0
    def file_size(data,length,mode,diag):
        assert length == len(data)
        diag[:] = [-20,0,0] if data == b"x" else [0,0,0]
        return -20 if data == b"x" else 9
    def decode(data,length,mode,out,capacity):
        assert data in (binary,ascii_stl) and capacity == 9
        out[:] = xyz
        return 0
    def mesh(data,length,verts,vc,indices,ic,nv,nf,diag):
        assert vc == ic == 3
        if b"nan" in data or b"1e100" in data:
            if mutant == "import-partial": verts[0] = 0.
            if mutant == "import-accept-unsafe": return 0
            diag[:] = [-30,0,1 if b"nan" in data else 2]
            return -30
        verts[:] = xyz
        indices[:] = [0,1,2]
        c.cast(nv,c.POINTER(c.c_int64))[0] = 3
        c.cast(nf,c.POINTER(c.c_int64))[0] = 1
        diag[:] = [0,0,0]
        return 0
    answers = {1:[0.,.25,1.],2:[0.,0.,.5,1.,1.],3:[1.],4:[.75,.25],
               5:[1.,0.,0.,1.],6:[2.,3.,0.,1.,1.,0.],7:[1.,1.],
               8:[0.,.25,.5,.75,1.],9:[-.5,0.,.5,1.,1.5],10:[1.,2.,3.]}
    def helper(request,out,capacity,used,detail):
        req = request._obj
        u,d = c.cast(used,c.POINTER(c.c_uint64)),c.cast(detail,c.POINTER(c.c_uint64))
        assert c.sizeof(req) == 64
        u[0],d[0] = 0,0
        if req.operation == 99:
            if mutant == "helper-accept-invalid": return 0
            return -2
        if capacity == 1:
            if mutant == "helper-partial": out[0] = 0.
            return -3
        if req.operation == 1 and req.rows == 2: return -5
        if req.operation == 6 and req.data[0] == req.data[1] == req.data[2] == req.data[3] == 0: return -4
        answer = answers[req.operation]
        u[0] = len(answer)
        for i,value in enumerate(answer): out[i] = value
        if mutant == "helper-op-" + str(req.operation): out[0] += 1
        return 0
    def catenary(start,end,axis,through,tolerance,poles,pc,knots,kc,np,nk,ns,dev,reason):
        u = [c.cast(ptr,c.POINTER(c.c_uint64)) for ptr in (np,nk,ns)]
        d,r = c.cast(dev,c.POINTER(c.c_double)),c.cast(reason,c.POINTER(c.c_uint32))
        for ptr in u: ptr[0] = 0
        d[0],r[0] = 0.,0
        why = 0
        if start is None: return -1
        if start[0] != start[0]: why = 1
        elif axis[2] == 3.: why = 2
        elif end[2] == 4.: why = 3
        elif through[0] == 7.: why = 4
        elif tolerance == 1e-30: why = 5
        elif start[0] == 1e308: why = 6
        if why:
            r[0] = why + (1 if mutant == "cat-reason-"+str(why) else 0)
            if mutant == "cat-partial": poles[0] = 0.
            if mutant == "cat-status-"+str(why): return 0
            return -4 if why == 5 else -2
        assert list(start)==[1.,2.,3.] and list(end)==[11.,2.,3.]
        if pc < 75 or kc < 27:
            if mutant == "cat-capacity": u[0][0] = 75
            return -3
        for i in range(75): poles[i] = 2. if i%3==1 else 0.
        poles[:3],poles[72:75] = [1.,2.,3.],[11.,2.,3.]
        poles[36],poles[38] = 6.,1.
        knots[:27] = [j/8. for j in range(9) for _ in range(3)]
        u[0][0],u[1][0],u[2][0],d[0] = 75,27,8,.0001
        if mutant == "cat-frame": poles[74] += 1
        if mutant == "cat-tail": poles[75] = 0.
        if mutant == "cat-count": u[0][0] += 1
        if mutant == "cat-deviation": d[0] = float("nan")
        return 0
    def obj(vertices,nv,names,nbytes,nl,vc,fc,arity,nf,indices,ni,nm,out,capacity,written):
        assert nv==6 and names==b"ab" and nbytes==2 and nf==2 and ni==6 and nm==2
        assert list(nl)==[1,1] and list(vc)==[3,3] and list(fc)==[1,1]
        bad = "nonfinite" if vertices[0]!=vertices[0] else "index" if indices[0]==3 else "arity" if arity[0]==2 else ""
        if bad or capacity==1:
            if mutant == "obj-partial": c.memset(out,0,1)
            if mutant == "obj-accept-"+bad: return 0
            return -2 if bad else -3
        expected = b"# Narwhal 3D\no a\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\no b\nv 2 0 0\nv 3 0 0\nv 2 1 0\nf 4 6 5\n"
        if mutant == "obj-indices": expected = expected.replace(b"f 4 6 5",b"f 1 3 2")
        c.memmove(out,expected,len(expected))
        c.cast(written,c.POINTER(c.c_int64))[0] = len(expected) - (1 if mutant=="obj-count" else 0)
        if mutant == "obj-tail": c.memset(c.addressof(out)+1023,0,1)
        return 0
    vector_rows = [bytes.fromhex(row.split('|')[1]) for row in
                   (ROOT/'native/pure_core/tests/fixtures/vector_legacy23.txt').read_text().splitlines()]
    handles = {}
    def vector_encode(request, result):
        r = request._obj
        handle = c.cast(result,c.POINTER(c.c_void_p))
        handle[0] = None
        if r.page_w_mm != r.page_w_mm: return -4
        if r.unit_mm == 0: return -5
        if r.digits == 4097: return -3
        if r.segments == 2: return -2
        if mutant == 'vector-aliased-handle' and handles:
            handle[0] = next(iter(handles))
            return 0
        key = len(handles)+1
        data = vector_rows[0 if r.format == 0 else 2 if r.unit.len else 1]
        if mutant == 'vector-overwritten-owner':
            for live in handles: handles[live] = data
        handles[key] = data
        handle[0] = key
        return 0
    def vector_len(handle):
        return 0 if handle is None else len(handles[handle.value])
    def vector_copy(handle,out,cap,written):
        data = handles[handle.value]
        if cap < len(data):
            if mutant == 'vector-copy-partial': c.memset(out,0,1)
            return -3
        c.memmove(out,data,len(data))
        c.cast(written,c.POINTER(c.c_size_t))[0] = len(data) + (1 if mutant=='vector-count' else 0)
        if mutant == 'vector-bytes': c.memset(out,0,1)
        if mutant == 'vector-tail' and cap>len(data): c.memset(c.addressof(out)+len(data),0,1)
        return 0
    def vector_free(handle):
        if handle is not None: del handles[handle.value]
    def vector_validate(fmt,out,length):
        return -6 if out == b'broken' else 0
    def dxf_colors(values,count,diag):
        diag[:] = [-40,1,0] if values[1] == 256 else [0,0,0]
        return diag[0]
    def dxf_units(name,length,mode,out):
        out[:] = [4,-1] if mode == 0 else [1,0] if name == b'Inches' else [0,-1]
        return 0
    def dxf_domain(r,diag):
        status,index = (-40,0) if r.entities[0].color==256 else (-42,0) if r.entities[0].kind==99 else (-44,2) if r.nums[2]!=r.nums[2] else (0,0)
        diag[:] = [status,index,0]
        if mutant == 'dxf-reason' and status: diag[1] += 1
        return status
    def dxf_capacity(request,size,diag):
        rc = dxf_domain(request._obj,diag)
        if rc: return rc
        c.cast(size,c.POINTER(c.c_size_t))[0] = 1024
        return 0
    def dxf_encode(request,out,cap,written,encoding,diag):
        rc = dxf_domain(request._obj,diag)
        if cap == 1: diag[:],rc = [-3,0,0],-3
        if rc:
            if mutant == 'dxf-partial': c.memset(out,0,1)
            return rc
        expected = (b'0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1009\n9\n$INSUNITS\n70\n4\n0\nENDSEC\n'
                    b'0\nSECTION\n2\nTABLES\n0\nTABLE\n2\nLAYER\n70\n1\n0\nLAYER\n2\nCut\n70\n0\n62\n7\n6\nCONTINUOUS\n0\nENDTAB\n0\nENDSEC\n'
                    b'0\nSECTION\n2\nENTITIES\n0\nLINE\n8\nCut\n62\n7\n10\n0\n20\n0\n30\n0\n11\n1\n21\n2\n31\n3\n0\nENDSEC\n0\nEOF\n')
        c.memmove(out,expected,len(expected))
        c.cast(written,c.POINTER(c.c_int64))[0] = len(expected)
        c.cast(encoding,c.POINTER(c.c_int64))[0] = 1 if mutant=='dxf-encoding' else 0
        return 0
    def tween_plan(a,b,count,number,mode,flags,out,cap,used):
        status = -3 if cap == 1 else -2 if mode == 99 else -12 if a[0]!=a[0] else -10 if a[3]==0 and flags==3 else 0
        if status:
            if mutant == 'tween-partial': out[0] = 0.
            if mutant == 'tween-status': return 0
            return status
        answer = [1.,3.,5.] if mode == 2 else [1.,3.,5.,1.]
        for i,value in enumerate(answer): out[i] = value
        c.cast(used,c.POINTER(c.c_uint64))[0] = len(answer)
        if mutant == 'tween-values': out[0] += 1
        return 0
    def tween_knots(a,b,count):
        return 0 if a[2]==b[2] else -20
    def tween_lengths(total,count,out,cap,used):
        status = -2 if total!=total or count==1 else -3 if cap==1 else 0
        if status: return status
        out[:3] = [0.,4.,8.]
        c.cast(used,c.POINTER(c.c_uint64))[0] = 3
        return 0
    preview_answers = round24_preview_answers()
    def ear_clip(points,n,out,capacity,count):
        c.cast(count,c.POINTER(c.c_uint64))[0] = 0
        if n > 1<<20 or points is not None and points[0]!=points[0]:
            return -2
        if n == 0: return 0
        if capacity < 3*(n-2):
            if mutant == 'ear-capacity-partial': out[0] = 0
            return -3
        expected = [0,1,2,0,2,3] if n==4 else [5,0,1,1,2,3]
        out[:6] = expected
        c.cast(count,c.POINTER(c.c_uint64))[0] = 4 if mutant=='ear-partial' else 2
        if mutant=='ear-indices': out[2] = 99
        return 0
    def ear_area(points,n,out,count):
        c.cast(count,c.POINTER(c.c_uint64))[0] = 0
        if n > 1<<20: return -2
        c.cast(out,c.POINTER(c.c_double))[0] = 0. if n==0 else 1.
        c.cast(count,c.POINTER(c.c_uint64))[0] = 2 if mutant=='ear-area-count' else 1
        return 0
    def control_preview(points,n,degree,span,out,capacity,count):
        assert (n,degree,span)==(3,2,2)
        c.cast(count,c.POINTER(c.c_uint64))[0] = 0
        if points[0]!=points[0]: return -2
        c.cast(count,c.POINTER(c.c_uint64))[0] = 8 if mutant=='control-count' else 9
        if capacity < 9: return -3
        out[:9] = [0.,0.,0.,2.,2.,0.,4.,0.,0.]
        if mutant=='control-value': out[3] = 3.
        return 0
    def preview_sample(op,data,length,segments,flags,out,capacity,metadata):
        meta = metadata._obj
        if op==99 or data[0]!=data[0]:
            if mutant=='preview-error-partial': meta.values=0
            return -2
        answer = preview_answers[op]
        assert (segments,flags,length)==(answer['segments'],answer['flags'],len(answer['input']))
        assert list(data)==answer['input']
        values,rows,*lengths = answer['metadata']
        meta.values,meta.rows = values,rows
        meta.lengths[:] = lengths
        if mutant=='preview-metadata': meta.values += 1
        if capacity < values:
            if mutant=='preview-short-write' and out is not None: out[0]=0.
            return -3
        c.memmove(out,answer['output'],len(answer['output']))
        if mutant=='preview-op-'+str(op):
            byte = c.cast(out,c.POINTER(c.c_ubyte))
            byte[0] ^= 1
        if mutant=='preview-tail': out[values]=0.
        return 0
    def reader_decode(data,length,int_limit,out,capacity,written,diagnostic):
        assert int_limit==4300
        diag = diagnostic._obj
        diag.code,diag.start,diag.len,diag.detail,diag.required = 0,0,0,0,0
        status,start,n,detail = 0,0,0,0
        errors = {b'AutoCAD Binary DXF\r\n\x1a\0':(-100,0,0,0),
                  b'0\nSECTION\n2':(-101,0,0,0),b'bad\nvalue\n':(-110,0,3,0),
                  ROUND24_READER_INPUT.replace(b'10\n0\n',b'10\nnope\n'):
                      (-111,ROUND24_READER_INPUT.index(b'10\n0\n')+3,4,0),
                  b'0\nSECTION\n2\nENTITIES\n0\nLINE\n0\nENDSEC\n0\nEOF\n':(-112,0,0,10)}
        if length>512*1024*1024: status=-3
        elif data in errors: status,start,n,detail=errors[data]
        else: assert data==ROUND24_READER_INPUT and length==len(data)
        if status:
            diag.code,diag.start,diag.len,diag.detail = status,start,n,detail
            if mutant=='reader-reason': diag.detail += 1
            if mutant=='reader-partial' and out is not None: c.memset(out,0,1)
            return 0 if mutant=='reader-status' else status
        if capacity < len(ROUND24_READER_WIRE):
            diag.code,diag.required = 1,len(ROUND24_READER_WIRE)
            if mutant=='reader-retry': c.cast(written,c.POINTER(c.c_size_t))[0]=diag.required
            return 1
        c.memmove(out,ROUND24_READER_WIRE,len(ROUND24_READER_WIRE))
        c.cast(written,c.POINTER(c.c_size_t))[0] = len(ROUND24_READER_WIRE)+(1 if mutant=='reader-written' else 0)
        if mutant=='reader-bytes': c.memset(out,0,1)
        if mutant=='reader-tail': c.memset(c.addressof(out)+len(ROUND24_READER_WIRE),0,1)
        return 0
    calls = {"abi_version": lambda: 1,"catenary_plan_v2":catenary,"obj_encode_into":obj,
             "stl_size_owned":size,"stl_encode_owned":encode,
             "stl_file_size":file_size,"stl_file_decode":decode,
             "stl_import_mesh_decode":mesh,"stl_normal":normal,"interpolate_helper_v1":helper,
             "vector_encode_v1":vector_encode,"vector_len_v1":vector_len,"vector_copy_v1":vector_copy,
             "vector_free_v1":vector_free,"vector_validate_v1":vector_validate,
             "dxf_owned_abi_version":lambda:2,"dxf_owned_colors":dxf_colors,"dxf_owned_units":dxf_units,
             "dxf_owned_capacity":dxf_capacity,"dxf_owned_encode":dxf_encode,
             "tween_plan_v1":tween_plan,"tween_knots_v1":tween_knots,"tween_lengths_v1":tween_lengths,
             "ear_clip_display":ear_clip,"earclip_signed_area_v1":ear_area,
             "control_point_curve_preview":control_preview,"preview_sample_v1":preview_sample,
             "dxf_reader24_abi_version":lambda:1,"dxf_reader24_decode":reader_decode,
             "nearest_on_nurbs":lambda *args: _nearest_scalar_mock(args),
             "nearest_on_callbacks_v1":lambda *args: _nearest_cb_mock(args)}
    functions = {}
    for name,fn in calls.items():
        full = "narwhal_pure_core_" + name
        if mutant == full: continue
        if mutant == "wrong-" + name:
            fn = lambda *args: 0  # status-only/no-write implementations must fail
        functions[full] = Mock(side_effect=fn)
    return SimpleNamespace(**functions)


def execute_retirement_probe(mutant="", code=None, lib=None):
    output = io.StringIO()
    with patch.object(c,"CDLL",return_value=lib or retirement_library(mutant)), \
            patch.object(sys,"argv",["probe","fixture"]), redirect_stdout(output):
        exec(compile(code or gate.PURE_CORE_RETIREMENT_PROBE,"<retirement-probe>","exec"),{})
    return json.loads(output.getvalue())


class PreviewPlatformWitnessTests(unittest.TestCase):
    def selection(self, system, code=None, machine='AMD64', compiler='MSC v.1944 64 bit (AMD64)'):
        source = code or gate.PURE_CORE_RETIREMENT_PROBE
        start = source.index('preview_witnesses = ')
        end = source.index('for op,segments,flags,bits,expected,digest in preview_witnesses:')
        def require(ok, message):
            if not ok: raise RuntimeError(message)
        namespace = {'sys':SimpleNamespace(platform=system,byteorder='little'), 'c':c, 'require':require}
        platform = SimpleNamespace(system=lambda:'Windows', machine=lambda:machine,
            python_implementation=lambda:'CPython', python_compiler=lambda:compiler)
        with patch.dict(sys.modules, {'platform':platform}):
            exec(compile(source[start:end], '<preview-platform-selection>', 'exec'), namespace)
        return namespace['preview_witnesses']

    def assert_matches_original(self, system, code=None):
        rows = self.selection(system, code)
        filename = 'preview24_windows_msvc.tsv' if system=='win32' else 'preview24_linux.tsv'
        wanted = {row[0]:row for row in rows}
        selected = {54,55,50,932,940,976}
        seen = set()
        with (ROOT/'native/pure_core/tests/data'/filename).open() as handle:
            for index,line in enumerate(handle):
                if index not in selected: continue
                op,n,flags,bits,lengths,output = line.rstrip('\n').split('\t')
                row = wanted[int(op)]
                self.assertEqual(row[:4], (int(op),int(n),int(flags),bits))
                sizes = tuple(map(int,lengths.split(',')))
                payload = b''.join(bytes.fromhex(v)[::-1] for v in output.split(','))
                self.assertEqual(row[4], (len(payload)//8,len(sizes),*sizes,*([0]*(3-len(sizes)))))
                self.assertEqual(row[5],hashlib.sha256(payload).hexdigest())
                seen.add(int(op))
        self.assertEqual(seen, {1,2,3,4,5,6})

    def test_each_platform_selects_exact_independent_original_bits(self):
        for system in ('linux','win32'):
            self.assert_matches_original(system)
        self.assertNotEqual(self.selection('linux')[0][-1],self.selection('win32')[0][-1])
        provenance = json.loads((ROOT/'native/pure_core/tests/data/preview24_windows_msvc.provenance.json').read_text())
        fixture = ROOT/'native/pure_core/tests/data/preview24_windows_msvc.tsv'
        self.assertEqual(provenance['fixture_sha256'], 'ff1fffae85ec59d0016b61eedb97cc316e3d279b4da90155195fb12815cdddbe')
        self.assertEqual(hashlib.sha256(fixture.read_bytes()).hexdigest(),provenance['fixture_sha256'])
        self.assertEqual(provenance['cases'],978)
        self.assertEqual(provenance['native_windows_discrepancies'],0)

    def test_unqualified_platforms_are_refused(self):
        for system,machine,compiler in (('darwin','AMD64','MSC'),('win32','ARM64','MSC'),('win32','AMD64','GCC')):
            with self.subTest(system=system,machine=machine,compiler=compiler), self.assertRaisesRegex(RuntimeError,'not qualified'):
                self.selection(system,machine=machine,compiler=compiler)

    def test_each_platform_hash_and_selection_mutation_is_red_then_green(self):
        original = gate.PURE_CORE_RETIREMENT_PROBE
        for system in ('linux','win32'):
            for row in self.selection(system):
                digest = row[-1]
                mutant = original.replace(digest, '0'*64)
                with self.subTest(system=system,op=row[0]), self.assertRaises(AssertionError):
                    self.assert_matches_original(system,mutant)
                self.assert_matches_original(system)
        mutant = original.replace('if sys.platform == "win32":','if False:')
        with self.assertRaises(RuntimeError): self.selection('win32',mutant)
        self.assert_matches_original('win32')


class RetirementProbeTests(unittest.TestCase):
    def test_independent_fixed_abi_and_each_missing_or_incorrect_export(self):
        self.assertEqual(execute_retirement_probe(),gate.PURE_CORE_RETIREMENT_IDENTITY)
        for symbol in gate.PURE_CORE_RETIREMENT_EXPORTS:
            with self.subTest(symbol=symbol), self.assertRaises((AttributeError,RuntimeError)):
                execute_retirement_probe(symbol)
        # Void free has no reply to corrupt: resolve it and exercise live/null
        # lifecycles, rather than claiming an integer-zero mutant exposes leaks.
        for name in ("abi_version","stl_size_owned","stl_encode_owned","stl_file_size",
                     "stl_file_decode","stl_import_mesh_decode","stl_normal","interpolate_helper_v1",
                     "catenary_plan_v2","obj_encode_into",
                     "vector_encode_v1","vector_len_v1","vector_copy_v1","vector_validate_v1",
                     "dxf_owned_abi_version","dxf_owned_colors","dxf_owned_units","dxf_owned_capacity","dxf_owned_encode",
                     "tween_plan_v1","tween_knots_v1","tween_lengths_v1",
                     "ear_clip_display","earclip_signed_area_v1","control_point_curve_preview",
                     "preview_sample_v1","dxf_reader24_abi_version","dxf_reader24_decode",
             "nearest_on_nurbs","nearest_on_callbacks_v1"):
            with self.subTest(wrong=name), self.assertRaises(RuntimeError):
                execute_retirement_probe("wrong-"+name)
        for mutant in ("encode-partial","import-partial","import-accept-unsafe",
                       "helper-partial","helper-accept-invalid",
                       "vector-copy-partial","vector-count","vector-bytes","vector-tail",
                       "vector-aliased-handle","vector-overwritten-owner",
                       "dxf-reason","dxf-partial","dxf-encoding","tween-partial","tween-status","tween-values",
                       "ear-capacity-partial","ear-partial","ear-indices","ear-area-count",
                       "control-count","control-value","preview-metadata","preview-short-write",
                       "preview-error-partial","preview-tail",*("preview-op-"+str(i) for i in range(1,7)),
                       "reader-reason","reader-partial","reader-status","reader-retry",
                       "reader-written","reader-bytes","reader-tail"):
            with self.subTest(mutant=mutant), self.assertRaises(RuntimeError):
                execute_retirement_probe(mutant)
        for mutant in ("cat-frame","cat-tail","cat-count","cat-deviation","cat-partial","cat-capacity",
                       "obj-indices","obj-count","obj-tail","obj-partial",
                       "obj-accept-nonfinite","obj-accept-index","obj-accept-arity",
                       *("cat-reason-"+str(i) for i in range(1,7)),
                       *("cat-status-"+str(i) for i in range(1,7))):
            with self.subTest(mutant=mutant), self.assertRaises(RuntimeError):
                execute_retirement_probe(mutant)
        for op in range(1,11):
            with self.subTest(operation=op), self.assertRaises(RuntimeError):
                execute_retirement_probe("helper-op-"+str(op))

    def test_owned_vector_lifecycle_closes_each_live_result(self):
        lib = retirement_library()
        self.assertEqual(execute_retirement_probe(lib=lib),gate.PURE_CORE_RETIREMENT_IDENTITY)
        self.assertEqual(lib.narwhal_pure_core_vector_free_v1.call_count,7)
        self.assertEqual(lib.narwhal_pure_core_vector_encode_v1.call_count,10)
        self.assertEqual(lib.narwhal_pure_core_vector_copy_v1.call_count,15)
        self.assertIsNone(lib.narwhal_pure_core_vector_free_v1.call_args.args[0])

    def test_semantic_assertions_are_load_bearing(self):
        # Re-break the guard in a copied AST, not in the live script. The
        # incorrect-result tests above must turn RED; restoring must pass.
        tree = ast.parse(gate.PURE_CORE_RETIREMENT_PROBE)
        require = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name == "require")
        require.body = [ast.Pass()]
        code = ast.unparse(ast.fix_missing_locations(tree))
        for mutant in ("wrong-stl_normal","helper-op-4","helper-op-10",
                       "cat-frame","cat-reason-3","obj-indices","vector-bytes","dxf-encoding","tween-values",
                       "ear-indices","control-value","preview-op-4","reader-bytes"):
            with self.subTest(mutant=mutant):
                self.assertEqual(execute_retirement_probe(mutant,code),gate.PURE_CORE_RETIREMENT_IDENTITY)
                with self.assertRaises(RuntimeError): execute_retirement_probe(mutant)

    def test_child_is_bounded_and_rejects_bad_missing_stale_or_crashing_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"core.so"
            path.write_bytes(b"fixture")
            scripts = []
            def child(command, **kwargs):
                self.assertEqual(command[1:3], ["-I", "-B"])
                self.assertEqual(len(command), 5)
                script = Path(command[3])
                scripts.append(script)
                self.assertEqual(script.read_bytes(), gate.PURE_CORE_RETIREMENT_PROBE.encode("utf-8"))
                return SimpleNamespace(stdout=json.dumps(gate.PURE_CORE_RETIREMENT_IDENTITY))
            with patch.object(gate.subprocess,"run",side_effect=child) as run:
                self.assertEqual(gate.probe_pure_core_retirement(path),gate.PURE_CORE_RETIREMENT_IDENTITY)
                args,kwargs = run.call_args
                self.assertEqual(args[0][-1],str(path.resolve()))
                self.assertEqual(kwargs["timeout"],30)
                self.assertTrue(kwargs["check"])
            self.assertTrue(scripts)
            self.assertTrue(all(not script.parent.exists() for script in scripts))
            for packet in ({}, {**gate.PURE_CORE_RETIREMENT_IDENTITY,"retirement_exports":[]},
                           {**gate.PURE_CORE_RETIREMENT_IDENTITY,"retirement_abi":22},
                           {**gate.PURE_CORE_RETIREMENT_IDENTITY,"retirement_abi":23}):
                with patch.object(gate.subprocess,"run",return_value=SimpleNamespace(stdout=json.dumps(packet))):
                    with self.assertRaisesRegex(ValueError,"evidence differs"): gate.probe_pure_core_retirement(path)
            for error in (subprocess.CalledProcessError(-11,"probe",stderr="crashed"),
                          subprocess.TimeoutExpired("probe",30),OSError("loader refused")):
                with patch.object(gate.subprocess,"run",side_effect=error):
                    with self.assertRaisesRegex(ValueError,"required retirement"): gate.probe_pure_core_retirement(path)
            alias = path.with_name("alias.so")
            alias.symlink_to(path)
            with self.assertRaisesRegex(ValueError,"missing regular"): gate.probe_pure_core_retirement(alias)
            path.unlink()
            with self.assertRaisesRegex(ValueError,"missing regular"): gate.probe_pure_core_retirement(path)


class RetirementTransportTests(unittest.TestCase):
    def test_long_exact_payload_uses_isolated_file_env_and_cleanup(self):
        identity = gate.PURE_CORE_RETIREMENT_IDENTITY
        with tempfile.TemporaryDirectory() as directory:
            library = Path(directory)/"selected core with spaces.dll"
            library.write_bytes(b"transport fixture, not loaded")
            payload = ("import json, os, sys\n"
                "assert sys.flags.isolated == 1 and sys.flags.dont_write_bytecode == 1\n"
                "assert os.environ['NARWHAL_TRANSPORT_TEST'] == 'preserved'\n"
                "assert sys.argv[1] == " + repr(str(library.resolve())) + "\n"
                "print(" + repr(json.dumps(identity)) + ")\n# exact UTF-8: café\n#" + "x"*40000)
            env = dict(os.environ, NARWHAL_TRANSPORT_TEST='preserved')
            run = subprocess.run
            scripts = []
            def bounded(command, **kwargs):
                # Deterministic Windows command-line bound on every test OS.
                if len(subprocess.list2cmdline(command)) >= 32767:
                    raise OSError(206, 'command line too long')
                self.assertEqual(command[1:3], ['-I','-B'])
                self.assertEqual(len(command), 5)
                self.assertEqual(command[-1], str(library.resolve()))
                script = Path(command[3])
                scripts.append(script)
                self.assertEqual(script.read_bytes(), payload.encode('utf-8'))
                self.assertIs(kwargs['env'],env)
                self.assertEqual(kwargs['timeout'],30)
                self.assertTrue(kwargs['text'] and kwargs['capture_output'] and kwargs['check'])
                return run(command, **kwargs)
            with patch.object(gate,'PURE_CORE_RETIREMENT_PROBE',payload), patch.object(gate.subprocess,'run',side_effect=bounded):
                self.assertEqual(gate.probe_pure_core_retirement(library,sys.executable,env=env),identity)
            self.assertEqual(len(scripts),1)
            self.assertFalse(scripts[0].parent.exists())

    def test_cleanup_on_process_json_identity_and_write_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            library = Path(directory)/'core.dll'
            library.write_bytes(b'fixture')
            cases = (subprocess.CalledProcessError(-11,'probe',stderr='native crash detail'),
                     subprocess.TimeoutExpired('probe',30), OSError('loader refused'),
                     SimpleNamespace(stdout='not JSON'), SimpleNamespace(stdout='{}'))
            for result in cases:
                scripts = []
                def child(command, **kwargs):
                    script = Path(command[3]); scripts.append(script)
                    self.assertTrue(script.is_file())
                    if isinstance(result,Exception): raise result
                    return result
                with self.subTest(result=result), patch.object(gate.subprocess,'run',side_effect=child):
                    with self.assertRaises(ValueError) as caught:
                        gate.probe_pure_core_retirement(library)
                    if isinstance(result,subprocess.CalledProcessError):
                        self.assertIn('native crash detail',str(caught.exception))
                self.assertEqual(len(scripts),1)
                self.assertFalse(scripts[0].parent.exists())
            write = Path.write_bytes
            parents = []
            def refused(path, data):
                if path.name=='probe.py':
                    parents.append(path.parent)
                    raise OSError('probe write refused')
                return write(path,data)
            with patch.object(Path,'write_bytes',refused), patch.object(gate.subprocess,'run') as run:
                with self.assertRaisesRegex(ValueError,'probe write refused'):
                    gate.probe_pure_core_retirement(library)
                run.assert_not_called()
            self.assertTrue(parents)
            self.assertTrue(all(not parent.exists() for parent in parents))

    def test_long_transport_and_isolation_mutations_are_red_then_green(self):
        source = inspect.getsource(gate.probe_pure_core_retirement)
        cases = (
            ('[str(python), "-I", "-B", str(script), str(path.resolve())]',
             '[str(python), "-I", "-B", "-c", PURE_CORE_RETIREMENT_PROBE, str(path.resolve())]'),
            ('"-I", "-B", str(script)', '"-B", str(script)'),
            ('env=env, text=True', 'env=None, text=True'),
            ('PURE_CORE_RETIREMENT_PROBE.encode("utf-8")', 'PURE_CORE_RETIREMENT_PROBE.encode("utf-8") + b" "'),
        )
        method = 'test_long_exact_payload_uses_isolated_file_env_and_cleanup'
        for old,new in cases:
            self.assertIn(old,source)
            namespace = dict(vars(gate))
            exec(compile(source.replace(old,new),'<retirement-transport-mutant>','exec'),namespace)
            mutant = type(gate.probe_pure_core_retirement)(namespace['probe_pure_core_retirement'].__code__, vars(gate))
            with self.subTest(mutation=old), patch.object(gate,'probe_pure_core_retirement',mutant):
                broken = unittest.TestResult()
                RetirementTransportTests(method).run(broken)
                self.assertFalse(broken.wasSuccessful(),'transport mutation survived')
            restored = unittest.TestResult()
            RetirementTransportTests(method).run(restored)
            self.assertTrue(restored.wasSuccessful(),restored.errors or restored.failures)


class PackageTests(unittest.TestCase):
    def test_round20_public_closure_retains_host_suites(self):
        # Path/AST metadata only: never load the golden corpora into this test.
        closure = gate.require_rust_public_closure(ROOT / "native/pure_core")
        self.assertEqual(set(closure), set(gate.RUST_INTEGRATION_FILES))
        self.assertEqual(len(gate.RUST_INTEGRATION_SUITES), 40)
        for suite in ("interpolate32_properties", "options29_engine", "options29_ffi", "options29_prompt",
                      "options29_prompt_ffi", "options29_registration"):
            self.assertIn(suite, gate.RUST_INTEGRATION_SUITES)
        source = ast.parse((ROOT / "scripts/create_public_branch.py").read_text())
        public = set()
        for node in source.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in {"NATIVE_FILES", "TEST_FILES"}
                for target in node.targets
            ):
                public.update(ast.literal_eval(node.value))
        for host in ("spike/test_native_nearest.py", "spike/test_native_display_samples.py",
                     "spike/test_native_curve_samples.py", "spike/test_native_arclength.py",
                     "spike/test_coil_owned.py", "spike/test_interpolate.py",
                     "spike/test_interpolate_owned_boundary.py", "spike/test_stl_import_boundary.py",
                     "spike/test_catenary.py", "spike/test_catenary_owned.py",
                     "spike/test_native_obj.py", "spike/test_obj_writer.py",
                     "spike/test_dxf_writer_owned.py", "spike/test_native_admission_owned.py"):
            self.assertIn(host, gate.PURE_CORE_PYTESTS)
            self.assertIn(host, public)
            self.assertTrue((ROOT / host).is_file(), host)

    def test_round22_required_obj_adapter_and_host_routes_are_public(self):
        import create_public_branch as public
        self.assertTrue((ROOT / "narwhal3d/native_obj.py").is_file())
        self.assertIn("narwhal3d/native_obj.py", public.recursive_files("narwhal3d"))
        for host in ("spike/test_catenary.py", "spike/test_catenary_owned.py",
                     "spike/test_obj_writer.py", "spike/test_native_obj.py"):
            self.assertIn(host, gate.PURE_CORE_PYTESTS)
        self.assertEqual(gate.PURE_CORE_RETIREMENT_IDENTITY["retirement_abi"], 26)
        for export in ("narwhal_pure_core_catenary_plan_v2", "narwhal_pure_core_obj_encode_into"):
            self.assertIn(export, gate.PURE_CORE_RETIREMENT_EXPORTS)

    def test_independent_binary_stl_capture_needs_no_public_audit_exception(self):
        import audit_public_tree
        path = "native/pure_core/tests/fixtures/stl_prefix_headers.bin"
        self.assertEqual((ROOT / path).stat().st_size, 40960)
        data = (ROOT / path).read_bytes()
        self.assertEqual(len(data), 40960)
        self.assertEqual(hashlib.sha256(data).hexdigest(),
                         "c8ca18b3fb9048eb7a6a116e7aafdb7d36361ceded90aa2abd58de851df00b8a")
        self.assertEqual(audit_public_tree.audit_entries({path: ("100644", data)}, require_manifest=False), [])
        self.assertIn(path, gate.RUST_INTEGRATION_FILES)

    def test_linux_build_inventory_tracks_new_header_and_capture_bytes(self):
        paths = ("native/pure_core/tests/catenary_v2.h",
                 "native/pure_core/tests/fixtures/stl_prefix_headers.bin",
                 "native/pure_core/tests/fixtures/catenary_windows_msvc.provenance.json",
                 "native/pure_core/tests/vector23_api.h", "native/pure_core/tests/dxf_owned.h",
                 "native/pure_core/tests/tween_v1.h",
                 "native/pure_core/tests/fixtures/dxf_writer23/capture.json",
                 "native/pure_core/tests/fixtures/dxf_writer23/units-default.dxf",
                 "native/pure_core/tests/fixtures/dxf_writer23/random-coordinates.bin",
                 *sorted(ROUND24_NATIVE_FILES | ROUND24_MODULES))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in paths:
                file = root / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(b"fixture input")
            before = linux_build.inventory(root, "pure_core")
            self.assertEqual(set(before), set(paths))
            for name in paths:
                (root / name).write_bytes(b"changed input")
                after = linux_build.inventory(root, "pure_core")
                self.assertNotEqual(before[name], after[name])
                self.assertEqual(after[name], hashlib.sha256(b"changed input").hexdigest())
                (root / name).write_bytes(b"fixture input")
            self.assertEqual(linux_build.inventory(root, "pure_core"), before)

    def test_linux_build_refuses_before_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, output = base / "source", base / "output"
            for path in (source / "native/pure_core", output / "pure_core-target/release",
                         base / "seed", base / "occt", base / "libs"):
                path.mkdir(parents=True)
            (output / "pure_core-target/release/libnarwhal_pure_core.so").write_bytes(b"old core")
            argv = ["build", "--source-root", str(source), "--output", str(output),
                    "--seed-target", str(base / "seed"), "--occt-source", str(base / "occt"),
                    "--occt-libs", str(base / "libs"), "--crate", "pure_core"]
            with patch.object(sys, "argv", argv), patch.object(linux_build.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
                    patch.object(linux_build, "probe_pure_core_parser", side_effect=ValueError("parser refused")):
                with self.assertRaisesRegex(ValueError, "parser refused"):
                    linux_build.main()
            self.assertFalse((output / "bin/libnarwhal_pure_core.so").exists())

    def test_linux_assembler_rejects_parser_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for name in linux_assemble.NATIVE_COMPONENTS:
                (directory / name).write_bytes(b"fixture")
                (directory / name).chmod(0o755)
            (directory / "narwhal-welcome").write_bytes(b" ".join((
                b"--runtime is required", b"--bootstrap is required", b"NARWHAL3D_WELCOME_REQUEST",
                b"NARWHAL3D_VERSION", b"releases?per_page=20", b"download sha256 does not match",
                b"could not atomically replace current AppImage")))
            doc = directory / "narwhal-document-core"
            import verify_document_core
            manifest = {"artifacts": [{"platform": "linux-x86_64", "size": doc.stat().st_size,
                                      "sha256": linux_assemble.sha256_file(doc)}], "source_aggregate_sha256": "fixture"}
            with patch.object(verify_document_core, "verify_directory", return_value=manifest), \
                    patch.object(linux_assemble, "probe_supervisor_memory"), \
                    patch.object(linux_assemble, "scan_runtime_glibc", return_value=((2, 28), "fixture")), \
                    patch.object(linux_assemble, "probe_native_library_abi", return_value=1), \
                    patch.object(linux_assemble, "probe_pure_core_parser", side_effect=ValueError("parser refused")):
                with self.assertRaisesRegex(linux_assemble.ReleaseError, "parser refused"):
                    linux_assemble.validate_native_components(directory, directory, directory)

    def test_linux_verifier_executes_adjacent_parser(self):
        with patch.object(linux_verify, "probe_pure_core_parser", side_effect=ValueError("parser refused")) as probe:
            with self.assertRaisesRegex(linux_verify.VerificationError, "parser refused"):
                linux_verify.verify_native_library_abis(Path("app"), Path("python"), {"ENV": "value"})
            probe.assert_called_once_with(Path("app/narwhal3d/native/libnarwhal_pure_core.so"),
                                          Path("python"), env={"ENV": "value"})

    def test_linux_manifest_requires_parser_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / linux_verify.APP_ROOT_RELATIVE
            runtime, adjacent = app / linux_verify.NATIVE_COMPONENT_RELATIVE, app / "narwhal3d/native"
            runtime.mkdir(parents=True)
            adjacent.mkdir(parents=True)
            records = {"required_glibc": "2.28"}
            for name in linux_verify.NATIVE_COMPONENTS:
                file = runtime / name
                file.write_bytes(name.encode())
                records[name] = {"size": file.stat().st_size, "sha256": linux_verify.sha256_file(file)}
                if name in linux_verify.PACKAGED_ACCELERATORS:
                    (adjacent / name).write_bytes(file.read_bytes())
            for name, (_, abi) in linux_verify.NATIVE_LIBRARY_ABIS.items():
                records[name]["abi"] = abi
            records["libnarwhal_application_core.so"].update(history_abi=1, exports=list(linux_verify.APPLICATION_CORE_EXPORTS))
            records["narwhal-welcome"]["launch_contract"] = 1
            pure = records["libnarwhal_pure_core.so"]
            pure.update(gate.PURE_CORE_PARSE_IDENTITY)
            observer = adjacent / "document-observer"
            observer.mkdir()
            (observer / "_narwhal_document_observer.so").write_bytes(b"observer")
            (observer / "build.json").write_text("{}\n")
            records["document-observer"] = {"sha256": linux_verify.sha256_file(observer / "_narwhal_document_observer.so"),
                                            "qualification": "fixture"}
            linux_verify.verify_native_manifest(root, records)
            for key in ("parse_abi", "parser_exports", "reply_bytes"):
                saved = pure.pop(key)
                with self.assertRaisesRegex(linux_verify.VerificationError, "parser"):
                    linux_verify.verify_native_manifest(root, records)
                pure[key] = saved
            linux_verify.verify_native_manifest(root, records)

    def test_windows_static_identity_and_native_execution(self):
        exports = ("narwhal_pure_core_abi_version", *gate.PURE_CORE_PARSE_EXPORTS, *gate.PURE_CORE_RETIREMENT_EXPORTS, *POLYLINE_EXPORTS)
        identity = {"machine": "pe32plus-x86-64", "exports": exports}
        with patch.object(win_verify, "inspect_pe_dll", return_value=identity) as inspect:
            with patch.object(sys, "platform", "linux"), patch.object(win_verify, "probe_pure_core_parser") as probe:
                record = win_verify.pure_core_record(Path("core.dll"))
                self.assertEqual(record["parse_abi"], 2)
                probe.assert_not_called()
            self.assertEqual(inspect.call_args.kwargs["constant_exports"],
                             {"narwhal_pure_core_abi_version": 1, "narwhal_pure_core_parse_abi": 2,
                                  "narwhal_pure_core_calc_abi2": 2, "narwhal_pure_core_bearing_abi2": 2,
                                  "narwhal_pure_core_coords_owned_abi1": 1,
                                  "narwhal_pure_core_svgpath_owned_version": 14, "narwhal_pure_core_alias_identity": 1,
                          "narwhal_pure_core_dxf_owned_abi_version": 2,
                          "narwhal_pure_core_dxf_reader24_abi_version": 1})
            with patch.object(sys, "platform", "win32"), patch.object(win_verify, "probe_pure_core_parser", side_effect=ValueError("semantic mutant")):
                with self.assertRaisesRegex(win_verify.VerificationError, "semantic mutant"):
                    win_verify.pure_core_record(Path("core.dll"))
        for missing in gate.PURE_CORE_PARSE_EXPORTS:
            with patch.object(win_verify, "inspect_pe_dll", return_value={**identity, "exports": tuple(e for e in exports if e != missing)}):
                with self.assertRaisesRegex(win_verify.VerificationError, "parser ABI exports"):
                    win_verify.verify_pure_core_identity(Path("old.dll"))

    def test_windows_pe_missing_exports_and_wrong_parser_abi(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(sys, "platform", "linux"):
            path = Path(tmp) / "core.dll"
            path.write_bytes(pe_fixture())
            self.assertEqual(win_verify.pure_core_record(path)["parse_abi"], 2)
            for kwargs in ({"parser_abi": 1}, {"svg_abi": 1}, {"alias_abi": 14}, {"dxf_abi": 1}, {"reader_abi": 2},
                           *({"missing": name} for name in (*gate.PURE_CORE_PARSE_EXPORTS,*gate.PURE_CORE_RETIREMENT_EXPORTS, *POLYLINE_EXPORTS)),
                           {"missing": "narwhal_pure_core_parse_abi"},
                           {"missing": "narwhal_pure_core_parse_v2"}):
                path.write_bytes(pe_fixture(**kwargs))
                with self.assertRaises(win_verify.VerificationError):
                    win_verify.pure_core_record(path)
            path.write_bytes(pe_fixture())
            self.assertEqual(win_verify.pure_core_record(path)["parse_abi"], 2)

    def test_windows_manifest_parser_identity_is_rechecked(self):
        tree = ast.parse(inspect.getsource(win_verify.verify_directory))
        branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                      and ast.unparse(node.test) == "filename == 'narwhal_pure_core.dll'")
        code = compile(ast.Module(body=[branch], type_ignores=[]), "<windows-parser-record>", "exec")
        expected = {"abi": 1, "parse_abi": 2, "exports": list(gate.PURE_CORE_PARSE_EXPORTS),
                    "machine": "pe32plus-x86-64"}
        with patch.object(win_verify, "pure_core_record", return_value=expected) as probe:
            for field in (None, "parse_abi", "exports"):
                record = dict(expected)
                if field:
                    del record[field]
                scope = dict(vars(win_verify), filename="narwhal_pure_core.dll", path=Path("core.dll"), record=record)
                if field:
                    with self.assertRaisesRegex(win_verify.VerificationError, "parser manifest"):
                        exec(code, scope)
                else:
                    exec(code, scope)
            self.assertEqual(probe.call_count, 3)

    def test_windows_assembly_calls_parser_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "core.dll").write_bytes(b"fixture")
            with patch.dict(win_verify.NATIVE_COMPONENT_PATHS, {"narwhal_pure_core.dll": "core.dll"}, clear=True), \
                    patch.object(win_verify, "verify_application_core_copies"), patch.object(win_verify, "verify_application_core_source"), \
                    patch.object(win_verify, "pure_core_record", side_effect=win_verify.VerificationError("parser refused")):
                with self.assertRaisesRegex(win_verify.VerificationError, "parser refused"):
                    win_assemble.native_records(root)

    def test_macos_package_executes_runtime_parser(self):
        with patch.object(mac_assemble, "probe_pure_core_parser", side_effect=ValueError("parser refused")) as probe:
            with self.assertRaisesRegex(ValueError, "parser refused"):
                mac_assemble.verify_pure_core(Path("app"), Path("python"))
            probe.assert_called_once_with(Path("app/runtime/macos-arm64/bin/libnarwhal_pure_core.dylib"), Path("python"))

    def test_platform_entrypoints_retain_parser_and_existing_cores(self):
        # Source wiring coverage for build/DMG entrypoints that require native OS tools.
        for module, function, count in ((mac_build, "probe_pure_core_parser", 2),
                                        (mac_assemble, "verify_pure_core", 2)):
            tree = ast.parse(Path(module.__file__).read_text())
            calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Name) and n.func.id == function]
            self.assertEqual(len(calls), count)
        script = (ROOT / "scripts/build_windows_preview.ps1").read_text()
        self.assertIn("from verify_windows_release import pure_core_record", script)
        self.assertIn("print(pure_core_record(Path(sys.argv[2])))", script)
        self.assertEqual(script.count("$PureParserProbe, $NarwhalSource, $PureCoreTarget)"), 2)
        self.assertIn("libnarwhal_ui_core.so", linux_verify.NATIVE_COMPONENTS)
        self.assertIn("libnarwhal_application_core.so", linux_verify.NATIVE_COMPONENTS)
        self.assertIn("narwhal_ui_core.dll", win_verify.NATIVE_COMPONENT_PATHS)
        self.assertIn("narwhal_application_core.dll", win_verify.NATIVE_COMPONENT_PATHS)


class RetirementPackageTests(unittest.TestCase):
    def assert_entrypoint_refuses(self, module, method):
        # Reuse the same minimal package/build inputs as the established parser
        # refusal tests, but allow parser success and fail only the new probe.
        original = getattr(PackageTests,method)
        class SelectRetirement(ast.NodeTransformer):
            def visit_Constant(self,node):
                if node.value == "probe_pure_core_parser":
                    return ast.copy_location(ast.Constant("probe_pure_core_retirement"),node)
                return node
        tree = ast.fix_missing_locations(SelectRetirement().visit(ast.parse(textwrap.dedent(inspect.getsource(original)))))
        namespace = {}
        exec(compile(tree,"<retirement-entrypoint-test>","exec"),globals(),namespace)
        with patch.object(module,"probe_pure_core_parser",return_value=gate.PURE_CORE_PARSE_IDENTITY):
            if module is linux_build:
                with patch.object(module,"probe_pure_core_nearest_batch",return_value={}), \
                        patch.object(module,"probe_pure_core_curve_hits",return_value={}), \
                        patch.object(module,"probe_pure_core_curve_record_hits",return_value={}), \
                        patch.object(module,"probe_pure_core_polyline_nearest",return_value={}), \
                        patch.object(module,"probe_pure_core_nurbs_record",return_value={}), \
                        patch.object(module,"probe_pure_core_line_samples",return_value={}), \
                        patch.object(module,"probe_pure_core_polyline_samples",return_value={}), \
                        patch.object(module,"probe_pure_core_polycurve_samples",return_value={}), \
                        patch.object(module,"probe_pure_core_offset_fit",return_value={}), \
                        patch.object(module,"probe_pure_core_surface_offset",return_value={}), \
                        patch.object(module,"probe_pure_core_remove_join_cv",return_value={}):
                    namespace[method](self)
            else:
                namespace[method](self)

    def test_linux_entrypoints_and_source_gate_refuse_retirement_failure(self):
        for module,method in (
            (linux_build,"test_linux_build_refuses_before_staging"),
            (linux_assemble,"test_linux_assembler_rejects_parser_failure"),
            (linux_verify,"test_linux_verifier_executes_adjacent_parser"),
        ):
            with self.subTest(module=module.__name__): self.assert_entrypoint_refuses(module,method)
        with patch.object(gate,"probe_pure_core_parser",return_value=gate.PURE_CORE_PARSE_IDENTITY), \
                patch.object(gate,"probe_pure_core_retirement",side_effect=ValueError("retirement refused")) as probe:
            with self.assertRaisesRegex(gate.GateError,"retirement refused"): gate.step_direct_abi(Path("core.so"))
            probe.assert_called_once()

    def test_windows_static_and_native_retirement_refusal(self):
        exports = ("narwhal_pure_core_abi_version",*gate.PURE_CORE_PARSE_EXPORTS,*gate.PURE_CORE_RETIREMENT_EXPORTS, *POLYLINE_EXPORTS)
        identity = {"machine":"pe32plus-x86-64","exports":exports}
        for missing in gate.PURE_CORE_RETIREMENT_EXPORTS:
            with self.subTest(missing=missing), patch.object(win_verify,"inspect_pe_dll",return_value={
                    **identity,"exports":tuple(e for e in exports if e != missing)}):
                with self.assertRaisesRegex(win_verify.VerificationError,"retirement"):
                    win_verify.verify_pure_core_identity(Path("core.dll"))
        with patch.object(win_verify,"inspect_pe_dll",return_value=identity), \
                patch.object(win_verify,"probe_pure_core_parser",return_value=gate.PURE_CORE_PARSE_IDENTITY), \
                patch.object(win_verify,"probe_pure_core_retirement",side_effect=ValueError("retirement refused")) as probe:
            with patch.object(sys,"platform","linux"):
                win_verify.pure_core_record(Path("core.dll"))
                probe.assert_not_called() # no fake cross-host semantic claim
            with patch.object(sys,"platform","win32"):
                with self.assertRaisesRegex(win_verify.VerificationError,"retirement refused"):
                    win_verify.pure_core_record(Path("core.dll"))
                probe.assert_called_once_with(Path("core.dll"))

    def test_windows_assembler_rejects_actual_pe_missing_new_owners_and_wrong_dxf_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.dict(win_verify.NATIVE_COMPONENT_PATHS,{'narwhal_pure_core.dll':'core.dll'},clear=True), \
                    patch.object(win_verify,'verify_application_core_copies'), \
                    patch.object(win_verify,'verify_application_core_source'), patch.object(sys,'platform','linux'):
                for missing in ROUND23_ADDED_EXPORTS + ROUND24_ADDED_EXPORTS:
                    (root/'core.dll').write_bytes(pe_fixture(missing=missing))
                    with self.subTest(missing=missing), self.assertRaises(win_verify.VerificationError):
                        win_assemble.native_records(root)
                (root/'core.dll').write_bytes(pe_fixture(dxf_abi=1))
                with self.assertRaises(win_verify.VerificationError):
                    win_assemble.native_records(root)

    def test_windows_actual_pe_admission_bypass_is_red_then_green(self):
        method = 'test_windows_assembler_rejects_actual_pe_missing_new_owners_and_wrong_dxf_version'
        with patch.object(win_verify,'pure_core_record',return_value={}):
            result = unittest.TestResult()
            RetirementPackageTests(method).run(result)
            self.assertFalse(result.wasSuccessful())
        restored = unittest.TestResult()
        RetirementPackageTests(method).run(restored)
        self.assertTrue(restored.wasSuccessful(),restored.errors or restored.failures)

    def test_required_probe_bypass_mutants_turn_refusal_tests_red(self):
        cases = ((linux_build,"main","test_linux_build_refuses_before_staging"),
                 (linux_assemble,"validate_native_components","test_linux_assembler_rejects_parser_failure"),
                 (linux_verify,"verify_native_library_abis","test_linux_verifier_executes_adjacent_parser"))
        for module,function,method in cases:
            class Bypass(ast.NodeTransformer):
                def visit_Call(self,node):
                    if isinstance(node.func,ast.Name) and node.func.id == "probe_pure_core_retirement":
                        return ast.copy_location(ast.parse(repr(gate.PURE_CORE_RETIREMENT_IDENTITY),mode="eval").body,node)
                    return self.generic_visit(node)
            tree = ast.fix_missing_locations(Bypass().visit(ast.parse(inspect.getsource(getattr(module,function)))))
            namespace = {}
            exec(compile(tree,"<retirement-bypass-mutant>","exec"),vars(module),namespace)
            with self.subTest(module=module.__name__), patch.object(module,function,namespace[function]), redirect_stdout(io.StringIO()):
                with self.assertRaises(AssertionError): self.assert_entrypoint_refuses(module,method)
            self.assert_entrypoint_refuses(module,method)
        # Native Windows semantic execution is independently load-bearing too.
        tree = ast.parse(inspect.getsource(win_verify.verify_pure_core_identity))
        tree = ast.fix_missing_locations(Bypass().visit(tree))
        namespace = {}
        exec(compile(tree,"<windows-retirement-bypass>","exec"),vars(win_verify),namespace)
        with patch.object(win_verify,"verify_pure_core_identity",namespace["verify_pure_core_identity"]):
            result = unittest.TestResult()
            RetirementPackageTests("test_windows_static_and_native_retirement_refusal").run(result)
            self.assertFalse(result.wasSuccessful(),"Windows retirement bypass survived")
        restored = unittest.TestResult()
        RetirementPackageTests("test_windows_static_and_native_retirement_refusal").run(restored)
        self.assertTrue(restored.wasSuccessful(),restored.errors or restored.failures)


ROUND23_NATIVE_FILES = {
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
}


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux copied-ELF fixtures; native Windows semantics run in its verifier")
class ActualRound23ProbeTests(unittest.TestCase):
    """Execute the real ABI; malformed libraries are private temporary ELF copies."""
    def setUp(self):
        self.library = Path(os.environ.get('NARWHAL_PURE_CORE',
            ROOT/'native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so'))
        self.assertTrue(self.library.is_file(), 'build a matching qualification candidate first')
        self.original = self.library.read_bytes()
        spec = importlib.util.spec_from_file_location('qualification23_elf_fixture',ROOT/'spike/test_desktop_native_startup.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.entries = module.elf_export_entries(self.original)
        self.temp = tempfile.TemporaryDirectory(prefix='pure-owned23-')
        self.addCleanup(self.temp.cleanup)
        self.copied = Path(self.temp.name)/'core.so'

    def assert_refused_with_parser_intact(self, data):
        self.copied.write_bytes(data)
        self.assertEqual(gate.probe_pure_core_parser(self.copied), gate.PURE_CORE_PARSE_IDENTITY)
        with self.assertRaisesRegex(ValueError,'required retirement'):
            gate.probe_pure_core_retirement(self.copied)

    def test_matching_real_library_has_stable_parser_and_round23_semantics(self):
        self.assertEqual(gate.probe_pure_core_parser(self.library),gate.PURE_CORE_PARSE_IDENTITY)
        self.assertEqual(gate.probe_pure_core_retirement(self.library),gate.PURE_CORE_RETIREMENT_IDENTITY)
        self.assertEqual(gate.PURE_CORE_RETIREMENT_IDENTITY['retirement_abi'],26)
        self.assertEqual(len(gate.PURE_CORE_RETIREMENT_EXPORTS),30)

    def test_stale22_and_each_new_missing_export_refused_with_real_parser(self):
        old = {'stl_size_owned','stl_encode_owned','stl_file_size','stl_file_decode',
               'stl_import_mesh_decode','stl_normal','interpolate_helper_v1','catenary_plan_v2','obj_encode_into'}
        new = list(ROUND23_ADDED_EXPORTS)
        self.assertTrue(set(new) <= set(gate.PURE_CORE_RETIREMENT_EXPORTS))
        self.assertEqual(len(new),13)
        for missing in [[name] for name in new]+[new]:
            with self.subTest(missing=missing):
                data = bytearray(self.original)
                for name in missing:
                    data[self.entries[name][0]+4] &= 0x0f
                self.assert_refused_with_parser_intact(data)

    def test_linux_builder_real_stale_library_refused_before_staging(self):
        data = bytearray(self.original)
        for name in ROUND23_ADDED_EXPORTS:
            data[self.entries[name][0]+4] &= 0x0f
        base = Path(self.temp.name)
        source,output = base/'source',base/'output'
        for path in (source/'native/pure_core',output/'pure_core-target/release',
                     base/'seed',base/'occt',base/'libs'):
            path.mkdir(parents=True,exist_ok=True)
        library = output/'pure_core-target/release/libnarwhal_pure_core.so'
        library.write_bytes(data)
        real_run = subprocess.run
        def run(command,*args,**kwargs):
            if command[0] == 'podman': return SimpleNamespace(returncode=0)
            return real_run(command,*args,**kwargs)
        argv = ['build','--source-root',str(source),'--output',str(output),
                '--seed-target',str(base/'seed'),'--occt-source',str(base/'occt'),
                '--occt-libs',str(base/'libs'),'--crate','pure_core']
        with patch.object(sys,'argv',argv), patch.object(linux_build.subprocess,'run',side_effect=run):
            with self.assertRaisesRegex(ValueError,'required retirement'):
                linux_build.main()
        self.assertFalse((output/'bin/libnarwhal_pure_core.so').exists())
        receipt = json.loads((output/'pure_core-build-receipt.json').read_text())
        self.assertNotIn('pure_core_retirement',receipt)

    def test_no_write_new_semantic_exports_and_wrong_owned_version_refused(self):
        for suffix in ('vector_encode_v1','vector_len_v1','vector_copy_v1','vector_validate_v1',
                       'dxf_owned_colors','dxf_owned_units','dxf_owned_capacity','dxf_owned_encode',
                       'tween_plan_v1','tween_knots_v1','tween_lengths_v1'):
            with self.subTest(export=suffix):
                data = bytearray(self.original)
                _entry,value,size,section = self.entries['narwhal_pure_core_'+suffix]
                self.assertGreaterEqual(size,3)
                self.assertEqual(struct.unpack_from('<H',data,18)[0],62)
                offset = section[4]+value-section[3]
                data[offset:offset+3] = b'\x31\xc0\xc3'
                self.assert_refused_with_parser_intact(data)
        data = bytearray(self.original)
        entry = self.entries['narwhal_pure_core_dxf_owned_abi_version'][0]
        _,value,size,_ = self.entries['narwhal_pure_core_abi_version']
        struct.pack_into('<QQ',data,entry+8,value,size)
        self.assert_refused_with_parser_intact(data)


class Round23ClosureTests(unittest.TestCase):
    def test_new_sources_headers_data_hosts_and_required_exports_are_public(self):
        import create_public_branch as public
        self.assertTrue(ROUND23_NATIVE_FILES <= set(gate.require_rust_public_closure(ROOT/'native/pure_core')))
        self.assertTrue(ROUND23_NATIVE_FILES <= set(public.NATIVE_FILES))
        self.assertTrue({'dxf_owned','vector23_golden','tween_golden','obj_legacy_abi','encoder_legacy_abi'}
                        <= set(gate.RUST_INTEGRATION_SUITES))
        for host in ('spike/test_dxf_writer_owned.py','spike/test_native_admission_owned.py'):
            self.assertIn(host,gate.PURE_CORE_PYTESTS)
            self.assertIn(host,public.TEST_FILES)
        for module in ('narwhal3d/native_dxf.py','narwhal3d/native_vector_export.py'):
            self.assertIn(module,public.recursive_files('narwhal3d'))
        new = ('vector_encode_v1','vector_len_v1','vector_copy_v1','vector_free_v1','vector_validate_v1',
               'dxf_owned_abi_version','dxf_owned_colors','dxf_owned_units','dxf_owned_capacity','dxf_owned_encode',
               'tween_plan_v1','tween_knots_v1','tween_lengths_v1')
        self.assertEqual(gate.PURE_CORE_RETIREMENT_IDENTITY['retirement_abi'],26)
        for name in new:
            self.assertIn('narwhal_pure_core_'+name,gate.PURE_CORE_RETIREMENT_EXPORTS)

    def test_exact_dxf_capture_audit_preserves_path_hash_and_raw_secret_guards(self):
        import audit_public_tree as audit
        paths = {path for path in ROUND23_NATIVE_FILES if '/dxf_writer23/' in path}
        self.assertEqual(set(audit.FROZEN_DXF23_FILES),paths)
        for path in sorted(paths):
            data = (ROOT/path).read_bytes()
            with self.subTest(path=path):
                self.assertEqual(audit.audit_entries({path:('100644',data)},require_manifest=False),[])
                self.assertTrue(audit.audit_entries({path:('120000',data)},require_manifest=False))
                changed = bytes([data[0]^1])+data[1:]
                problems = audit.audit_entries({path:('100644',changed)},require_manifest=False)
                self.assertTrue(any('provenance hash mismatch' in error for error in problems),problems)
                self.assertNotEqual(audit.frozen_dxf23_scan('unregistered.dxf',data),audit.frozen_dxf23_scan(path,data))
        path = 'native/pure_core/tests/fixtures/dxf_writer23/units-default.dxf'
        data = (ROOT/path).read_bytes()
        self.assertTrue(audit.audit_entries({path+'.unregistered':('100644',data)},require_manifest=False))
        secret = audit.SECRET_MARKERS[0]
        changed = data+secret
        with patch.dict(audit.FROZEN_DXF23_FILES,{path:(len(changed),hashlib.sha256(changed).hexdigest())}):
            errors = audit.audit_entries({path:('100644',changed)},require_manifest=False)
            self.assertTrue(any('possible secret marker' in error for error in errors),errors)

    def test_exact_capture_audit_guard_mutation_is_red_then_green(self):
        import audit_public_tree as audit
        method = 'test_exact_dxf_capture_audit_preserves_path_hash_and_raw_secret_guards'
        with patch.object(audit,'frozen_dxf23_scan',return_value=b'claimed frozen data'):
            result = unittest.TestResult()
            Round23ClosureTests(method).run(result)
            self.assertFalse(result.wasSuccessful())
        restored = unittest.TestResult()
        Round23ClosureTests(method).run(restored)
        self.assertTrue(restored.wasSuccessful(),restored.errors or restored.failures)

    def test_import_fixture_respects_qualified_library_and_root_default(self):
        tree = ast.parse((ROOT/'spike/test_import.py').read_text())
        assignment = next(n for n in tree.body if isinstance(n,ast.Assign)
                          and any(isinstance(t,ast.Name) and t.id=='PURE_LIB' for t in n.targets))
        def require_environment(node):
            code = compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),
                           '<import-library-fixture>','exec')
            scope = {'os':os,'ROOT':'/owned/test-root'}
            with patch.dict(os.environ,{'NARWHAL_PURE_CORE':'/qualified/round23.so'}):
                exec(code,scope)
                self.assertEqual(scope['PURE_LIB'],'/qualified/round23.so')
            with patch.dict(os.environ,clear=True):
                exec(code,scope)
                self.assertEqual(scope['PURE_LIB'],'/owned/test-root/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so')
        require_environment(assignment)
        mutant = ast.parse(ast.unparse(assignment)).body[0]
        self.assertIsInstance(mutant.value,ast.BoolOp)
        mutant.value = mutant.value.values[1]
        with self.assertRaises(AssertionError): require_environment(mutant)
        require_environment(assignment)

    def test_new_closure_checks_are_load_bearing(self):
        method = 'test_new_sources_headers_data_hosts_and_required_exports_are_public'
        for host in ('spike/test_dxf_writer_owned.py','spike/test_native_admission_owned.py'):
            with patch.object(gate,'PURE_CORE_PYTESTS',tuple(p for p in gate.PURE_CORE_PYTESTS if p!=host)):
                result = unittest.TestResult()
                Round23ClosureTests(method).run(result)
                self.assertFalse(result.wasSuccessful())
        for export in ROUND23_ADDED_EXPORTS:
            with patch.object(gate,'PURE_CORE_RETIREMENT_EXPORTS',tuple(e for e in gate.PURE_CORE_RETIREMENT_EXPORTS if e!=export)):
                result = unittest.TestResult()
                Round23ClosureTests(method).run(result)
                self.assertFalse(result.wasSuccessful())
        restored = unittest.TestResult()
        Round23ClosureTests(method).run(restored)
        self.assertTrue(restored.wasSuccessful(),restored.errors or restored.failures)


@unittest.skipUnless(sys.platform.startswith('linux'), 'copied ELF faults; native Windows semantics run in its verifier')
class ActualRound24ProbeTests(unittest.TestCase):
    setUp = ActualRound23ProbeTests.setUp
    assert_refused_with_parser_intact = ActualRound23ProbeTests.assert_refused_with_parser_intact

    def test_stale23_and_each_new_required_export_refused_with_parser_working(self):
        newly_introduced = [name for name in ROUND24_ADDED_EXPORTS if name not in (
            'narwhal_pure_core_ear_clip_display','narwhal_pure_core_control_point_curve_preview')]
        self.assertEqual(len(newly_introduced),4)
        for missing in [[name] for name in ROUND24_ADDED_EXPORTS]+[newly_introduced]:
            with self.subTest(missing=missing):
                data = bytearray(self.original)
                for name in missing: data[self.entries[name][0]+4] &= 0x0f
                self.assert_refused_with_parser_intact(data)

    def test_new_no_write_exports_and_wrong_reader_identity_refused(self):
        for name in ROUND24_ADDED_EXPORTS:
            if name.endswith('abi_version'): continue
            with self.subTest(symbol=name):
                data = bytearray(self.original)
                _entry,value,size,section = self.entries[name]
                self.assertGreaterEqual(size,3)
                self.assertEqual(struct.unpack_from('<H',data,18)[0],62)
                offset = section[4]+value-section[3]
                data[offset:offset+3] = b'\x31\xc0\xc3'
                self.assert_refused_with_parser_intact(data)
        data = bytearray(self.original)
        entry = self.entries['narwhal_pure_core_dxf_reader24_abi_version'][0]
        _,value,size,_ = self.entries['narwhal_pure_core_dxf_owned_abi_version']
        struct.pack_into('<QQ',data,entry+8,value,size)
        self.assert_refused_with_parser_intact(data)

    def test_linux_builder_refuses_real_parser_working_stale23_before_staging(self):
        data = bytearray(self.original)
        for name in ROUND24_ADDED_EXPORTS:
            if name not in ('narwhal_pure_core_ear_clip_display','narwhal_pure_core_control_point_curve_preview'):
                data[self.entries[name][0]+4] &= 0x0f
        base = Path(self.temp.name)
        source,output = base/'source24',base/'output24'
        for path in (source/'native/pure_core',output/'pure_core-target/release',
                     base/'seed24',base/'occt24',base/'libs24'):
            path.mkdir(parents=True,exist_ok=True)
        library = output/'pure_core-target/release/libnarwhal_pure_core.so'
        library.write_bytes(data)
        real_run = subprocess.run
        def run(command,*args,**kwargs):
            return SimpleNamespace(returncode=0) if command[0]=='podman' else real_run(command,*args,**kwargs)
        argv = ['build','--source-root',str(source),'--output',str(output),
                '--seed-target',str(base/'seed24'),'--occt-source',str(base/'occt24'),
                '--occt-libs',str(base/'libs24'),'--crate','pure_core']
        with patch.object(sys,'argv',argv), patch.object(linux_build.subprocess,'run',side_effect=run):
            with self.assertRaisesRegex(ValueError,'required retirement'): linux_build.main()
        self.assertFalse((output/'bin/libnarwhal_pure_core.so').exists())


class Round24ClosureTests(unittest.TestCase):
    def test_new_header_data_producer_and_host_closure_is_real(self):
        import create_public_branch as public
        self.assertTrue(ROUND24_NATIVE_FILES <= set(gate.require_rust_public_closure(ROOT/'native/pure_core')))
        self.assertTrue(ROUND24_NATIVE_FILES | ROUND24_MODULES <= set(public.NATIVE_FILES))
        self.assertTrue(ROUND24_MODULES <= set(gate.RUST_REQUIRED_MODULE_FILES))
        self.assertTrue({'earclip24_golden','preview24_golden','dxf_reader24'} <= set(gate.RUST_INTEGRATION_SUITES))
        for host in ('spike/test_preview24_boundaries.py','spike/test_dxf_reader24_boundaries.py'):
            self.assertIn(host,gate.PURE_CORE_PYTESTS)
            self.assertIn(host,public.TEST_FILES)
        for support in ('spike/dxf_reader24_fixtures.py',):
            self.assertIn(support,public.TEST_FILES)
        for module in ('narwhal3d/native_preview.py','narwhal3d/native_dxf_reader.py','narwhal3d/dxf_reader.py'):
            self.assertIn(module,public.recursive_files('narwhal3d'))
        self.assertTrue(set(ROUND23_ADDED_EXPORTS + ROUND24_ADDED_EXPORTS) <= set(gate.PURE_CORE_RETIREMENT_EXPORTS))
        self.assertEqual(gate.PURE_CORE_RETIREMENT_IDENTITY['retirement_abi'],26)
        self.assertEqual(len(gate.PURE_CORE_RETIREMENT_EXPORTS),30)

    def test_new_configuration_guards_are_load_bearing(self):
        method = 'test_new_header_data_producer_and_host_closure_is_real'
        mutants = [patch.object(gate,'PURE_CORE_RETIREMENT_EXPORTS',tuple(
                       name for name in gate.PURE_CORE_RETIREMENT_EXPORTS if name!=missing))
                   for missing in ROUND24_ADDED_EXPORTS]
        mutants += [patch.object(gate,'PURE_CORE_PYTESTS',tuple(
                        name for name in gate.PURE_CORE_PYTESTS if name!=missing))
                    for missing in ('spike/test_preview24_boundaries.py','spike/test_dxf_reader24_boundaries.py')]
        import create_public_branch as public
        recursive = public.recursive_files
        mutants += [patch.object(public,'TEST_FILES',tuple(
                        name for name in public.TEST_FILES if name!='spike/dxf_reader24_fixtures.py')),
                    patch.object(public,'recursive_files',side_effect=lambda *args: [
                        name for name in recursive(*args) if name!='narwhal3d/dxf_reader.py']),
                    patch.object(gate,'RUST_REQUIRED_MODULE_FILES',()),
                    patch.dict(gate.PURE_CORE_RETIREMENT_IDENTITY,{'retirement_abi':23})]
        for mutant in mutants:
            with mutant:
                broken = unittest.TestResult()
                Round24ClosureTests(method).run(broken)
                self.assertFalse(broken.wasSuccessful())
        restored = unittest.TestResult()
        Round24ClosureTests(method).run(restored)
        self.assertTrue(restored.wasSuccessful(),restored.errors or restored.failures)


class DxfLegacyClosureTests(unittest.TestCase):
    def test_legacy_decoder_coverage_is_in_rust_and_public(self):
        import create_public_branch as public
        paths = {
            "native/pure_core/tests/dxf_legacy_abi.rs",
            "native/pure_core/tests/dxf_legacy_abi.h",
            "native/pure_core/tests/fixtures/dxf_legacy_abi/README.md",
            "native/pure_core/tests/fixtures/dxf_legacy_abi/analytic.dxf",
            "native/pure_core/tests/fixtures/dxf_legacy_abi/analytic.packed",
            "native/pure_core/tests/fixtures/dxf_legacy_abi/empty.packed",
            "native/pure_core/tests/fixtures/dxf_legacy_abi/specification.json",
        }
        self.assertTrue(paths <= set(gate.require_rust_public_closure(ROOT / "native/pure_core")))
        self.assertTrue(paths <= set(public.NATIVE_FILES))
        self.assertIn("dxf_legacy_abi", gate.RUST_INTEGRATION_SUITES)

    def test_legacy_decoder_suite_omission_is_red_then_green(self):
        method = "test_legacy_decoder_coverage_is_in_rust_and_public"
        with patch.object(gate, "RUST_INTEGRATION_SUITES", tuple(
                name for name in gate.RUST_INTEGRATION_SUITES if name != "dxf_legacy_abi")):
            broken = unittest.TestResult()
            DxfLegacyClosureTests(method).run(broken)
            self.assertFalse(broken.wasSuccessful(), "legacy decoder suite omission survived")
        restored = unittest.TestResult()
        DxfLegacyClosureTests(method).run(restored)
        self.assertTrue(restored.wasSuccessful(), restored.errors or restored.failures)


class GuardMutationTests(unittest.TestCase):
    def test_round20_host_retention_check_goes_red_when_manifest_is_trimmed(self):
        method = "test_round20_public_closure_retains_host_suites"
        for host in ("spike/test_native_nearest.py", "spike/test_native_display_samples.py",
                     "spike/test_native_curve_samples.py", "spike/test_native_arclength.py",
                     "spike/test_coil_owned.py", "spike/test_interpolate.py",
                     "spike/test_interpolate_owned_boundary.py", "spike/test_stl_import_boundary.py",
                     "spike/test_catenary.py", "spike/test_catenary_owned.py",
                     "spike/test_native_obj.py", "spike/test_obj_writer.py",
                     "spike/test_dxf_writer_owned.py", "spike/test_native_admission_owned.py"):
            with self.subTest(host=host), patch.object(
                gate, "PURE_CORE_PYTESTS", tuple(p for p in gate.PURE_CORE_PYTESTS if p != host)
            ):
                result = unittest.TestResult()
                PackageTests(method).run(result)
                self.assertFalse(result.wasSuccessful(), "host omission survived: " + host)
        restored = unittest.TestResult()
        PackageTests(method).run(restored)
        self.assertTrue(restored.wasSuccessful(), restored.errors or restored.failures)

    def test_round22_required_boundary_and_fixed_capture_guards_go_red(self):
        import create_public_branch as public
        method = "test_round22_required_obj_adapter_and_host_routes_are_public"
        cases = [(patch.object(gate, "PURE_CORE_RETIREMENT_EXPORTS", tuple(
                    e for e in gate.PURE_CORE_RETIREMENT_EXPORTS if e != missing)), method)
                 for missing in ("narwhal_pure_core_catenary_plan_v2", "narwhal_pure_core_obj_encode_into")]
        cases.extend([
            (patch.dict(gate.PURE_CORE_RETIREMENT_IDENTITY, {"retirement_abi":22}), method),
            (patch.object(public,"recursive_files",return_value=[]), method),
            (patch.object(gate,"PURE_CORE_PYTESTS",tuple(
                f for f in gate.PURE_CORE_PYTESTS if f != "spike/test_catenary_owned.py")), method),
        ])
        inventory = linux_build.inventory
        cases.append((patch.object(linux_build,"inventory",side_effect=lambda *args: {
            k:v for k,v in inventory(*args).items() if not k.endswith(".bin")}),
            "test_linux_build_inventory_tracks_new_header_and_capture_bytes"))
        original_read = Path.read_bytes
        capture = ROOT / "native/pure_core/tests/fixtures/stl_prefix_headers.bin"
        data = capture.read_bytes()
        def damaged(path):
            return bytes([data[0]^1])+data[1:] if path == capture else original_read(path)
        cases.append((patch.object(Path,"read_bytes",damaged),
                      "test_independent_binary_stl_capture_needs_no_public_audit_exception"))
        for mutant, method in cases:
            with mutant:
                result = unittest.TestResult()
                PackageTests(method).run(result)
                self.assertFalse(result.wasSuccessful(), "round22 qualification guard mutation survived")
            restored = unittest.TestResult()
            PackageTests(method).run(restored)
            self.assertTrue(restored.wasSuccessful(), restored.errors or restored.failures)

    def test_windows_additive_export_admission_is_load_bearing(self):
        original=win_verify.verify_pure_core_identity
        source=inspect.getsource(original)
        old='        if identity["exports"].count(name) != 1:'
        self.assertEqual(source.count(old),1)
        namespace={}
        exec(compile(source.replace(old,'        if False:',1),'<windows-additive-export-mutant>','exec'),
             vars(win_verify),namespace)
        with patch.object(win_verify,'verify_pure_core_identity',namespace['verify_pure_core_identity']):
            red=unittest.TestResult()
            PackageTests('test_windows_pe_missing_exports_and_wrong_parser_abi').run(red)
            self.assertTrue(red.failures,'omitted additive export admission must fail the actual PE refusal test')
            self.assertFalse(red.errors)
        green=unittest.TestResult()
        PackageTests('test_windows_pe_missing_exports_and_wrong_parser_abi').run(green)
        self.assertTrue(green.wasSuccessful(),green.errors or green.failures)

    def test_packaging_tests_go_red_when_parser_probes_are_bypassed(self):
        # Mutate copied function ASTs in memory: no live source/bytecode writes.
        cases = (
            (linux_build, "main", "probe_pure_core_parser", PackageTests, "test_linux_build_refuses_before_staging"),
            (linux_assemble, "validate_native_components", "probe_pure_core_parser", PackageTests, "test_linux_assembler_rejects_parser_failure"),
            (linux_verify, "verify_native_library_abis", "probe_pure_core_parser", PackageTests, "test_linux_verifier_executes_adjacent_parser"),
            (mac_assemble, "verify_pure_core", "probe_pure_core_parser", PackageTests, "test_macos_package_executes_runtime_parser"),
            (win_verify, "verify_pure_core_identity", "probe_pure_core_parser", PackageTests, "test_windows_static_identity_and_native_execution"),
        )
        for module, function, probe, case, method in cases:
            with self.subTest(function=module.__name__ + "." + function):
                class Bypass(ast.NodeTransformer):
                    def visit_Call(self, node):
                        if isinstance(node.func, ast.Name) and node.func.id == probe:
                            return ast.copy_location(ast.parse(repr(gate.PURE_CORE_PARSE_IDENTITY), mode="eval").body, node)
                        return self.generic_visit(node)
                original = getattr(module, function)
                tree = ast.fix_missing_locations(Bypass().visit(ast.parse(inspect.getsource(original))))
                namespace = {}
                exec(compile(tree, "<packaging-guard-mutant>", "exec"), vars(module), namespace)
                with patch.object(module, function, namespace[function]), redirect_stdout(io.StringIO()):
                    result = unittest.TestResult()
                    case(method).run(result)
                    self.assertFalse(result.wasSuccessful(), "mutant survived: " + function)
                restored = unittest.TestResult()
                case(method).run(restored)
                self.assertTrue(restored.wasSuccessful(), restored.errors or restored.failures)


class JointAdmissionTests(unittest.TestCase):
    def test_source_closure_and_all_package_entrypoints_require_joint(self):
        name = "native/pure_core/src/remove_join_cv.rs"
        self.assertIn(name, gate.RUST_REQUIRED_MODULE_FILES)
        for relative in ("scripts/create_public_branch.py", "scripts/audit_public_tree.py"):
            tree = ast.parse((ROOT / relative).read_text())
            literals = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)]
            self.assertIn(name, literals)
        for module, function in ((linux_build, "main"),
                                 (linux_assemble, "validate_native_components"),
                                 (linux_verify, "verify_native_library_abis")):
            tree = ast.parse(inspect.getsource(getattr(module, function)))
            calls = [node.func.id for node in ast.walk(tree)
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
            self.assertIn("probe_pure_core_remove_join_cv", calls)
            self.assertIn("probe_pure_core_polycurve_samples", calls)

    def test_actual_joint_probe_and_assertion_red_restored_green(self):
        library = Path(os.environ.get("NARWHAL_PURE_CORE") or
                       ROOT / "narwhal3d/native/libnarwhal_pure_core.so")
        self.assertTrue(gate.probe_pure_core_remove_join_cv(library)["retained_data"])
        original = gate.PURE_CORE_REMOVE_JOIN_PROBE
        broken = original.replace("fn(cvs,28,knots,9,3,3", "fn(cvs,28,knots,9,3,0", 1)
        self.assertNotEqual(original, broken)
        with patch.object(gate, "PURE_CORE_REMOVE_JOIN_PROBE", broken):
            with self.assertRaisesRegex(ValueError, "certified fixture"):
                gate.probe_pure_core_remove_join_cv(library)
        self.assertTrue(gate.probe_pure_core_remove_join_cv(library)["refusals_no_write"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
