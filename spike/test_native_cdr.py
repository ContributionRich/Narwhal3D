#!/usr/bin/env python3
"""Direct ABI, routing, and fallback tests for the Rust CDR v15 codec."""
from __future__ import annotations

import ctypes
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
crate = ROOT / "native" / "cdr_core"
build = subprocess.run(
    ["cargo", "+1.95.0", "build", "--release", "--locked", "--offline"],
    cwd=crate, text=True, capture_output=True,
)
LIB = crate / "target" / "release" / "libnarwhal_cdr_core.so"

pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg
native = importlib.import_module("narwhal3d.native_cdr")
writer = importlib.import_module("narwhal3d.cdr_writer")
reader = importlib.import_module("narwhal3d.cdr_reader")
vector = importlib.import_module("narwhal3d.vector_export")

checks = 0


def check(condition, label):
    global checks
    checks += 1
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        raise AssertionError(label)


check(build.returncode == 0 and LIB.is_file(), "Rust CDR core builds from tracked source")
prior = os.environ.get("NARWHAL_CDR_CORE")
os.environ["NARWHAL_CDR_CORE"] = str(LIB)
native.reset_for_tests()
try:
    strokes = [
        vector.Stroke(
            beziers=(((10.0, 20.0), (20.0, 35.0), (30.0, 5.0), (40.0, 20.0)),),
            rgb=(1.0, 0.0, 0.0), width_mm=0.25, layer="Cut"),
        vector.Stroke(
            beziers=(
                ((55.0, 15.0), (60.0, 15.0), (65.0, 15.0), (70.0, 15.0)),
                ((70.0, 15.0), (70.0, 25.0), (70.0, 35.0), (70.0, 45.0)),
                ((70.0, 45.0), (65.0, 45.0), (60.0, 45.0), (55.0, 45.0)),
                ((55.0, 45.0), (55.0, 35.0), (55.0, 25.0), (55.0, 15.0)),
            ),
            closed=True, rgb=(0.0, 0.0, 1.0), width_mm=0.5, layer="Outline"),
    ]
    check(native.status() == f"ready: {LIB}",
          "adapter verifies ABI 1 on the exact source-built library")

    old_fallback = writer._riff_python
    old_reader_fallback = reader._read_cdr_python
    writer._riff_python = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("Python RIFF fallback was used"))
    try:
        with tempfile.TemporaryDirectory(prefix="narwhal_native_cdr_") as temporary:
            path = Path(temporary) / "native.cdr"
            writer.write_cdr(str(path), strokes, 100.0, 80.0, "Native", hairline=True)
            with zipfile.ZipFile(path) as archive:
                sample_riff = archive.read("content/riffData.cdr")
                sample_metadata = archive.read("metadata/metadata.xml")
            python_drawing = old_reader_fallback(sample_riff, sample_metadata)
            reader._read_cdr_python = lambda *_a, **_k: (_ for _ in ()).throw(
                AssertionError("Python CDR reader fallback was used"))
            try:
                drawing = reader.read_cdr(str(path))
                ordinary = Path(temporary) / "ordinary.cdr"
                writer.write_cdr(str(ordinary), strokes, 100.0, 80.0, "Widths")
                ordinary_drawing = reader.read_cdr(str(ordinary))
            finally:
                reader._read_cdr_python = old_reader_fallback
            check(drawing == python_drawing,
                  "source-built Rust reader exactly matches the complete Python drawing")
            check(len(drawing.objects) == 2 and drawing.objects[1].paths[0].closed,
                  "production writer and reader execute Rust and preserve closed paths")
            expected = [point for bezier in strokes[0].beziers for point in bezier]
            actual = [point for bezier in drawing.objects[0].paths[0].beziers
                      for point in bezier]
            check(len(actual) == len(expected)
                  and max(abs(a - b) for pair_a, pair_b in zip(actual, expected)
                          for a, b in zip(pair_a, pair_b)) <= 0.0001,
                  "native coordinates round-trip exactly within one CDR unit")
            check([obj.layer for obj in drawing.objects] == ["Cut", "Outline"]
                  and [obj.rgb for obj in drawing.objects] == [(255, 0, 0), (0, 0, 255)],
                  "native route preserves layer grouping and RGB styles")
            check(all(obj.hairline for obj in drawing.objects),
                  "native Hairline remains exactly 0.003 inch")
            check([round(obj.width_mm, 6) for obj in ordinary_drawing.objects]
                  == [0.25, 0.5],
                  "native route preserves distinct ordinary outline widths")
    finally:
        writer._riff_python = old_fallback
        reader._read_cdr_python = old_reader_fallback

    library = ctypes.CDLL(str(LIB))
    function = library.narwhal_cdr_v15_riff
    function.argtypes = [
        ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    function.restype = ctypes.c_int32
    read_function = library.narwhal_cdr_v15_read
    read_function.argtypes = [
        ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    read_function.restype = ctypes.c_int32
    bad = (ctypes.c_uint8 * 1)(0xff)
    required = ctypes.c_size_t(123)
    check(function(bad, 1, None, 0, ctypes.byref(required)) == -2,
          "raw ABI refuses malformed JSON")
    check(function(None, 0, None, 0, ctypes.byref(required)) == -1,
          "raw ABI refuses null input")

    riff_buffer = (ctypes.c_uint8 * len(sample_riff)).from_buffer_copy(sample_riff)
    metadata_buffer = (ctypes.c_uint8 * len(sample_metadata)).from_buffer_copy(sample_metadata)
    read_required = ctypes.c_size_t(999)
    check(read_function(riff_buffer, len(sample_riff), metadata_buffer,
                        len(sample_metadata), None, 0,
                        ctypes.byref(read_required)) == 1
          and 2 <= read_required.value <= 512 * 1024 * 1024,
          "reader ABI reports a bounded caller-owned output size")
    canary = (ctypes.c_uint8 * 16)(*([0xa5] * 16))
    check(read_function(riff_buffer, len(sample_riff), metadata_buffer,
                        len(sample_metadata), canary, len(canary),
                        ctypes.byref(read_required)) == 1
          and bytes(canary) == bytes([0xa5] * 16),
          "short reader output remains untouched")
    check(read_function(None, 1, metadata_buffer, len(sample_metadata),
                        None, 0, ctypes.byref(read_required)) == -1
          and read_required.value == 0,
          "reader rejects null non-empty input and clears required size")
    check(read_function(riff_buffer, len(sample_riff), None, 1,
                        None, 0, ctypes.byref(read_required)) == -1
          and read_required.value == 0,
          "reader rejects null non-empty metadata")
    check(read_function(riff_buffer, len(sample_riff), metadata_buffer,
                        len(sample_metadata), None, 1,
                        ctypes.byref(read_required)) == -1,
          "reader rejects a null output with nonzero capacity")
    check(read_function(riff_buffer, len(sample_riff), metadata_buffer,
                        len(sample_metadata), None, 0, None) == -1,
          "reader rejects a null required-size pointer")
    truncated = sample_riff[:-1]
    truncated_buffer = (ctypes.c_uint8 * len(truncated)).from_buffer_copy(truncated)
    check(read_function(truncated_buffer, len(truncated), metadata_buffer,
                        len(sample_metadata), None, 0,
                        ctypes.byref(read_required)) == -2
          and read_required.value == 0,
          "reader rejects truncated RIFF without allocating")
    unsupported = bytearray(sample_riff)
    unsupported[20:22] = (1600).to_bytes(2, "little")
    unsupported_buffer = (ctypes.c_uint8 * len(unsupported)).from_buffer_copy(unsupported)
    check(read_function(unsupported_buffer, len(unsupported), metadata_buffer,
                        len(sample_metadata), None, 0,
                        ctypes.byref(read_required)) == -4
          and read_required.value == 0,
          "reader distinguishes an unsupported CDR generation")

    invalid_loda = bytearray(sample_riff)
    search_at = 0
    while True:
        loda_at = invalid_loda.find(b"loda", search_at)
        if loda_at < 0:
            raise AssertionError("generated CDR has no object loda")
        payload_at = loda_at + 8
        if int.from_bytes(invalid_loda[payload_at + 16:payload_at + 20], "little") == 3:
            total = int.from_bytes(invalid_loda[payload_at:payload_at + 4], "little")
            offsets_at = int.from_bytes(
                invalid_loda[payload_at + 8:payload_at + 12], "little")
            invalid_loda[payload_at + offsets_at:payload_at + offsets_at + 4] = (
                total + 1).to_bytes(4, "little")
            break
        search_at = loda_at + 4
    invalid_loda_buffer = (ctypes.c_uint8 * len(invalid_loda)).from_buffer_copy(invalid_loda)
    check(read_function(invalid_loda_buffer, len(invalid_loda), metadata_buffer,
                        len(sample_metadata), None, 0,
                        ctypes.byref(read_required)) == -2,
          "reader rejects invalid loda argument offsets")
    try:
        old_reader_fallback(bytes(invalid_loda), sample_metadata)
    except reader.CDRReadError:
        bounded_fallback_rejected = True
    else:
        bounded_fallback_rejected = False
    check(bounded_fallback_rejected,
          "Python fallback cannot weaken native malformed-loda rejection")

    overlap_loda = bytearray(sample_riff)
    overlap_loda[payload_at + 12:payload_at + 16] = (20).to_bytes(4, "little")
    overlap_buffer = (ctypes.c_uint8 * len(overlap_loda)).from_buffer_copy(overlap_loda)
    check(read_function(overlap_buffer, len(overlap_loda), metadata_buffer,
                        len(sample_metadata), None, 0,
                        ctypes.byref(read_required)) == -2,
          "reader rejects overlapping loda argument tables")

    odd_version = b"vrsn" + (3).to_bytes(4, "little") + (1500).to_bytes(2, "little") + b"x"
    odd_body = b"CDRF" + odd_version
    missing_padding = b"RIFF" + len(odd_body).to_bytes(4, "little") + odd_body
    missing_padding_buffer = (
        ctypes.c_uint8 * len(missing_padding)).from_buffer_copy(missing_padding)
    check(read_function(missing_padding_buffer, len(missing_padding), metadata_buffer,
                        len(sample_metadata), None, 0,
                        ctypes.byref(read_required)) == -2,
          "reader rejects an odd final record with no RIFF padding byte")

    def chunk(tag, payload):
        value = tag + len(payload).to_bytes(4, "little") + payload
        return value + (b"\0" if len(payload) & 1 else b"")

    nested = chunk(b"JUNK", b"")
    for _ in range(reader.MAX_DEPTH + 2):
        nested = chunk(b"LIST", b"nest" + nested)
    nested_body = b"CDRF" + chunk(b"vrsn", (1500).to_bytes(2, "little")) + nested
    nested_riff = b"RIFF" + len(nested_body).to_bytes(4, "little") + nested_body
    nested_buffer = (ctypes.c_uint8 * len(nested_riff)).from_buffer_copy(nested_riff)
    check(read_function(nested_buffer, len(nested_riff), metadata_buffer,
                        len(sample_metadata), None, 0,
                        ctypes.byref(read_required)) == -2,
          "reader enforces the RIFF nesting-depth limit")
    try:
        old_reader_fallback(nested_riff, sample_metadata)
    except reader.CDRReadError:
        fallback_depth_rejected = True
    else:
        fallback_depth_rejected = False
    check(fallback_depth_rejected,
          "Python fallback enforces the same RIFF nesting-depth limit")
    try:
        reader._inflate(zlib.compress(b"x" * 4096), 1024)
    except reader.CDRReadError:
        fallback_bomb_rejected = True
    else:
        fallback_bomb_rejected = False
    check(fallback_bomb_rejected,
          "Python fallback bounds compressed-stream expansion")

    oversized_name = b"a" * (reader.MAX_LAYER_NAME_BYTES + 2)
    loda_total = 28 + len(oversized_name)
    oversized_loda = b"".join([
        loda_total.to_bytes(4, "little"),
        (1).to_bytes(4, "little"),
        (20).to_bytes(4, "little"),
        (24).to_bytes(4, "little"),
        (0).to_bytes(4, "little"),
        (28).to_bytes(4, "little"),
        (0x03e8).to_bytes(4, "little"),
        oversized_name,
    ])
    try:
        reader._args(oversized_loda)
    except reader.CDRReadError:
        fallback_name_rejected = True
    else:
        fallback_name_rejected = False
    check(fallback_name_rejected,
          "Python fallback rejects oversized layer names before decoding them")

    with tempfile.TemporaryDirectory(prefix="narwhal_cdr_metadata_") as temporary:
        malformed_metadata_path = Path(temporary) / "malformed-metadata.cdr"
        with zipfile.ZipFile(malformed_metadata_path, "w") as archive:
            archive.writestr("content/riffData.cdr", sample_riff)
            archive.writestr("metadata/metadata.xml", b"\xff")
        try:
            reader.read_cdr(str(malformed_metadata_path))
        except reader.CDRReadError:
            malformed_metadata_rejected = True
        else:
            malformed_metadata_rejected = False
        check(malformed_metadata_rejected,
              "malformed UTF-8 metadata cannot fail open through Python fallback")

    def find_layer(data, start, end, ancestors=()):
        pos = start
        while pos + 8 <= end:
            length = int.from_bytes(data[pos + 4:pos + 8], "little")
            stop = pos + 8 + length
            if data[pos:pos + 4] == b"LIST" and length >= 4:
                current = ancestors + (pos,)
                if data[pos + 8:pos + 12] == b"layr":
                    return stop, current
                found = find_layer(data, pos + 12, stop, current)
                if found is not None:
                    return found
            pos = stop + (length & 1)
        return None

    insertion, ancestors = find_layer(sample_riff, 12, len(sample_riff))
    mixed = bytearray(sample_riff)
    unsupported_object = chunk(b"LIST", b"obj ")
    mixed[insertion:insertion] = unsupported_object
    mixed[4:8] = (int.from_bytes(mixed[4:8], "little")
                  + len(unsupported_object)).to_bytes(4, "little")
    for record_at in ancestors:
        adjusted_at = record_at + (len(unsupported_object) if record_at >= insertion else 0)
        length_at = adjusted_at + 4
        mixed[length_at:length_at + 4] = (
            int.from_bytes(mixed[length_at:length_at + 4], "little")
            + len(unsupported_object)).to_bytes(4, "little")
    mixed_buffer = (ctypes.c_uint8 * len(mixed)).from_buffer_copy(mixed)
    check(read_function(mixed_buffer, len(mixed), metadata_buffer,
                        len(sample_metadata), None, 0,
                        ctypes.byref(read_required)) == -2,
          "mixed supported and unsupported objects fail instead of importing partially")
    try:
        old_reader_fallback(bytes(mixed), sample_metadata)
    except reader.CDRReadError:
        fallback_mixed_rejected = True
    else:
        fallback_mixed_rejected = False
    check(fallback_mixed_rejected,
          "Python fallback also refuses partial mixed-object reconstruction")

    with tempfile.TemporaryDirectory(prefix="narwhal_cdr_member_limit_") as temporary:
        limited_path = Path(temporary) / "limited.cdr"
        with zipfile.ZipFile(limited_path, "w") as archive:
            archive.writestr("content/riffData.cdr", sample_riff)
            archive.writestr("metadata/metadata.xml", sample_metadata)
        saved_limit = reader.MAX_RIFF_BYTES
        reader.MAX_RIFF_BYTES = len(sample_riff) - 1
        try:
            try:
                reader.read_cdr(str(limited_path))
            except reader.CDRReadError:
                member_limit_rejected = True
            else:
                member_limit_rejected = False
        finally:
            reader.MAX_RIFF_BYTES = saved_limit
        check(member_limit_rejected,
              "package member limits apply before native or Python decoding")

    with tempfile.TemporaryDirectory(prefix="narwhal_unsupported_cdr_") as temporary:
        unsupported_path = Path(temporary) / "unsupported.cdr"
        with zipfile.ZipFile(unsupported_path, "w") as archive:
            archive.writestr("content/riffData.cdr", unsupported)
            archive.writestr("metadata/metadata.xml", sample_metadata)
        try:
            reader.read_cdr(str(unsupported_path))
        except reader.CDRReadError as exc:
            explicit_unsupported = "version 15, not 1600" in str(exc)
        else:
            explicit_unsupported = False
        check(explicit_unsupported,
              "production reader reports unsupported versions explicitly")

    class MalformedFunction:
        def __call__(self, _source, _length, output, _capacity, required_pointer):
            version = b"vrsn" + (2).to_bytes(4, "little") + (1500).to_bytes(2, "little")
            body = b"CDRF" + version + b"LIST" + (0).to_bytes(4, "little")
            malformed = b"RIFF" + len(body).to_bytes(4, "little") + body
            ctypes.cast(required_pointer, ctypes.POINTER(ctypes.c_size_t))[0] = len(malformed)
            if output is None:
                return 1
            ctypes.memmove(output, malformed, len(malformed))
            return 0

    class MalformedLibrary:
        narwhal_cdr_v15_riff = MalformedFunction()

    saved_library, saved_checked = native._library, native._checked
    native._library, native._checked = MalformedLibrary(), True
    try:
        check(native.riff_v15(strokes, 100.0, 80.0) is None,
              "adapter rejects kindless native LISTs with no document/page")
    finally:
        native._library, native._checked = saved_library, saved_checked

    class MalformedReadFunction:
        def __call__(self, _riff, _riff_len, _metadata, _metadata_len,
                     output, _capacity, required_pointer):
            malformed = b'{"version":1500,"objects":[]}'
            ctypes.cast(required_pointer, ctypes.POINTER(ctypes.c_size_t))[0] = len(malformed)
            if output is None:
                return 1
            ctypes.memmove(output, malformed, len(malformed))
            return 0

    class MalformedReadLibrary:
        narwhal_cdr_v15_read = MalformedReadFunction()

    native._library, native._checked = MalformedReadLibrary(), True
    try:
        check(native.read_v15(sample_riff, sample_metadata) is None,
              "adapter rejects malformed or incomplete native reader JSON")
    finally:
        native._library, native._checked = saved_library, saved_checked

    inconsistent = json.dumps({
        "page_w_mm": 100.0, "page_h_mm": 80.0, "hairline": False,
        "strokes": [{
            "beziers": strokes[0].beziers, "closed": False,
            "rgb": strokes[0].rgb, "width_mm": 0.25, "layer": "Absent",
        }],
        "layers": [{"name": "Other", "guid_le": [0] * 16}],
    }, separators=(",", ":")).encode()
    inconsistent_buffer = (ctypes.c_uint8 * len(inconsistent)).from_buffer_copy(inconsistent)
    required.value = 999
    check(function(inconsistent_buffer, len(inconsistent), None, 0,
                   ctypes.byref(required)) == -3 and required.value == 0,
          "raw ABI rejects absent layers before allocation and clears required size")

    calls = []
    original_native = native.riff_v15
    native.riff_v15 = lambda *_a, **_k: None
    writer._riff_python = lambda *_a, **_k: calls.append(True) or b"python"
    try:
        check(writer._riff(strokes, 100.0, 80.0) == b"python" and calls == [True],
              "malformed or unavailable native output runs complete Python fallback")
    finally:
        native.riff_v15 = original_native
        writer._riff_python = old_fallback

    close_widths = [
        vector.Stroke(beziers=strokes[0].beziers, rgb=(0.0, 0.0, 0.0),
                      width_mm=0.25001, layer="Cut"),
        vector.Stroke(beziers=strokes[0].beziers, rgb=(0.0, 0.0, 0.0),
                      width_mm=0.25002, layer="Cut"),
    ]
    close_data = native.riff_v15(close_widths, 100.0, 80.0, False)
    check(close_data is not None and close_data.count(b"outl") == 2,
          "native style identity matches Python before coordinate quantization")


    many = strokes * 250
    started = time.perf_counter()
    python_data = old_fallback(many, 100.0, 80.0, False)
    python_write_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    rust_data = native.riff_v15(many, 100.0, 80.0, False)
    rust_write_ms = (time.perf_counter() - started) * 1000
    check(rust_data is not None and rust_data[:12] == python_data[:12],
          "large native and Python outputs share the genuine RIFF/CDRF envelope")
    print(f"  measured 500-stroke RIFF: Python {python_write_ms:.3f} ms, "
          f"native adapter {rust_write_ms:.3f} ms")

    with tempfile.TemporaryDirectory(prefix="narwhal_native_cdr_bench_") as temporary:
        many_path = Path(temporary) / "many.cdr"
        writer.write_cdr(str(many_path), many, 100.0, 80.0, "Reader benchmark")
        with zipfile.ZipFile(many_path) as archive:
            many_riff = archive.read("content/riffData.cdr")
            many_metadata = archive.read("metadata/metadata.xml")
        started = time.perf_counter()
        python_many = old_reader_fallback(many_riff, many_metadata)
        python_read_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        native_many = native.read_v15(many_riff, many_metadata)
        rust_read_ms = (time.perf_counter() - started) * 1000
        check(native_many is not None
              and len(native_many["objects"]) == len(python_many.objects) == 500,
              "large source-built Rust reader returns every object")
        print(f"  measured 500-object read: Python {python_read_ms:.3f} ms, "
              f"native adapter {rust_read_ms:.3f} ms")
finally:
    if prior is None:
        os.environ.pop("NARWHAL_CDR_CORE", None)
    else:
        os.environ["NARWHAL_CDR_CORE"] = prior
    native.reset_for_tests()

print(f"\n{checks}/{checks} native CDR checks passed")
