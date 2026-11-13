"""Fast parser packaging contracts; no Cargo, platform builds, GUI or product imports.

ctypes fixtures model only this smoke ABI, not a parser. Negative fixtures and
in-memory guard mutants must be rejected. Real-library qualification is separate.
"""
from __future__ import annotations

import ast
from contextlib import redirect_stdout
import ctypes as c
import io
import inspect
import json
import struct
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
        code, witness = {b"'_Box": (-4, b'"Box"'), b"=value": (-5, b'')}[text[:length]]
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
        return {b"!123": b'[{"kind":"text","value":"!123"}]',
                b"a\x1cb": b'[{"kind":"text","value":"a"},{"kind":"text","value":"b"}]'}[text[:length]]
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
        narwhal_pure_core_blend_bezier_points=Mock(side_effect=blend))
    if mutant in gate.PURE_CORE_PARSE_EXPORTS:
        delattr(result, mutant)
    return result


def execute_probe(mutant="", code=None):
    output = io.StringIO()
    with patch.object(c, "CDLL", return_value=library(mutant)), \
            patch.object(sys, "argv", ["probe", "fixture"]), redirect_stdout(output):
        exec(compile(code or gate.PURE_CORE_PARSE_PROBE, "<parser-probe>", "exec"), {})
    return json.loads(output.getvalue())


def pe_fixture(*, parser_abi=2, missing="", svg_abi=14, alias_abi=1):
    """Minimal PE export/import tables; data fixture, never executable native code."""
    data = bytearray(4096)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3c, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", data, 0x84, 0x8664, 1, 0, 0, 0, 240, 0x2002)
    optional = 0x98
    struct.pack_into("<H", data, optional, 0x20b)
    struct.pack_into("<I", data, optional + 60, 0x200)
    struct.pack_into("<II", data, optional + 112, 0x1200, 0x200)
    struct.pack_into("<II", data, optional + 120, 0x1a00, 40)
    struct.pack_into("<IIII", data, optional + 240 + 8, 0xe00, 0x1000, 0xe00, 0x200)
    names = [name for name in ("narwhal_pure_core_abi_version", *gate.PURE_CORE_PARSE_EXPORTS) if name != missing]
    assert len(names) <= 32, "PE fixture export table capacity exceeded"
    offset = lambda rva: rva - 0x1000 + 0x200
    struct.pack_into("<IIIII", data, offset(0x1200) + 20,
                     len(names), len(names), 0x1240, 0x12c0, 0x1340)
    cursor = 0x1400
    for i, name in enumerate(names):
        struct.pack_into("<I", data, offset(0x1240) + i * 4, 0x1c00 + i * 16)
        struct.pack_into("<I", data, offset(0x12c0) + i * 4, cursor)
        struct.pack_into("<H", data, offset(0x1340) + i * 2, i)
        encoded = name.encode() + b"\0"
        data[offset(cursor):offset(cursor) + len(encoded)] = encoded
        cursor += len(encoded)
        assert cursor <= 0x1a00, "PE export names overlap the import table"
        value = parser_abi if name == "narwhal_pure_core_parse_abi" else (2 if name in {"narwhal_pure_core_calc_abi2", "narwhal_pure_core_bearing_abi2"} else 1)
        if name == "narwhal_pure_core_svgpath_owned_version": value = svg_abi
        if name == "narwhal_pure_core_alias_identity": value = alias_abi
        data[offset(0x1c00) + i * 16:offset(0x1c00) + i * 16 + 6] = b"\xb8" + struct.pack("<I", value) + b"\xc3"
    struct.pack_into("<IIIII", data, offset(0x1a00), 0x1a80, 0, 0, 0x1a40, 0x1a80)
    data[offset(0x1a40):offset(0x1a40) + 13] = b"kernel32.dll\0"
    struct.pack_into("<Q", data, offset(0x1a80), (1 << 63) | 1)
    return bytes(data)


class ParserProbeTests(unittest.TestCase):
    def test_fixed_abi_and_real_semantic_calls(self):
        self.assertEqual(execute_probe(), gate.PURE_CORE_PARSE_IDENTITY)
        self.assertEqual(gate.ABI_VERSION, 1)

    def test_missing_exports_versions_and_semantic_mutants(self):
        for mutant in (*gate.PURE_CORE_PARSE_EXPORTS, "parser-abi", "old-abi",
                       "wrong-value", "accept-invalid", "transport", "reserved", "no-write",
                       "calc-value", "calc-error", "bearing-value", "bearing-error",
                       "planner-error", "calc-abi", "bearing-abi",
                       "coords-value", "coords-error", "norm-value", "join-boundary",
                       "svg-abi", "alias-abi", "svg-geometry", "svg-error",
                       "alias-prefix", "alias-error", "alias-options",
                       "rebuild-malformed", "rebuild-value", "rebuild-nan", "rebuild-knots", "rebuild-refusal", "rebuild-refusal-write",
                       "blend-value", "blend-malformed", "blend-input", "blend-refusal", "blend-refusal-write"):
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
            ('rebuild-value', 'if status != 0 or count.value'),
            ('rebuild-refusal', 'if status != expected or count.value'),
            ('blend-value', 'if status != 0 or list(output)'),
            ('blend-refusal', 'if status != expected or list(output)'),
            ('svg-geometry', 'if status != 0 or list(report)'),
            ('svg-error', 'elif status != 2 or (error.kind'),
            ('alias-prefix', 'if alias(points,len(text)'),
            ('alias-options', 'if options(points,len(text)'),
        )
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


class PackageTests(unittest.TestCase):
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
        exports = ("narwhal_pure_core_abi_version", *gate.PURE_CORE_PARSE_EXPORTS)
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
                                  "narwhal_pure_core_svgpath_owned_version": 14, "narwhal_pure_core_alias_identity": 1})
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
            for kwargs in ({"parser_abi": 1}, {"svg_abi": 1}, {"alias_abi": 14},
                           *({"missing": name} for name in gate.PURE_CORE_PARSE_EXPORTS),
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


class GuardMutationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
