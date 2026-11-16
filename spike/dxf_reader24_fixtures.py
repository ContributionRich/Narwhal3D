"""Independent original-reader answers, never a Python DXF reference engine."""
import hashlib
import json
from pathlib import Path
import struct

DIRECTORY = Path(__file__).resolve().parents[1] / "native/pure_core/tests/fixtures/dxf_reader24"

def freeze(value):
    if type(value) is float:
        return {"f64le": struct.pack("<d", value).hex()}
    if type(value) is int:
        return {"integer": hex(value)}
    if type(value) is tuple:
        return {"tuple": [freeze(x) for x in value]}
    if isinstance(value, list):
        return [freeze(x) for x in value]
    if isinstance(value, dict):
        return {"dict": [[k, freeze(v)] for k, v in value.items()]}
    return value

def thaw(value):
    if isinstance(value, list):
        return [thaw(x) for x in value]
    if isinstance(value, dict):
        if "f64le" in value:
            return struct.unpack("<d", bytes.fromhex(value["f64le"]))[0]
        if "integer" in value:
            return int(value["integer"], 16)
        if "tuple" in value:
            return tuple(thaw(x) for x in value["tuple"])
        return {k: thaw(v) for k, v in value["dict"]}
    return value

def cases():
    metadata = json.loads((DIRECTORY / "capture.json").read_text())
    data = memoryview((DIRECTORY / "corpus.bin").read_bytes())
    offset = 0
    def take(n):
        nonlocal offset
        out = data[offset:offset+n].tobytes()
        assert len(out) == n
        offset += n
        return out
    def number(fmt):
        return struct.unpack(fmt, take(struct.calcsize(fmt)))[0]
    assert take(8) == b"DXFCAP24"
    assert number("<I") == len(metadata["cases"]) == 606
    result = []
    for row in metadata["cases"]:
        assert take(number("<I")).decode() == row["name"]
        assert number("<I") == row["int_limit"]
        raw = take(number("<Q"))
        answer = take(number("<Q"))
        assert number("<q") == row.get("code", 0)
        assert hashlib.sha256(raw).hexdigest() == row["input_sha256"]
        result.append((row, raw, answer))
    assert offset == len(data)
    return result

def expected_file(path):
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    for row, _, _ in cases():
        if row["input_sha256"] == digest and row["kind"] == "ok":
            return thaw(row["value"])
    raise AssertionError("reader input has no independently frozen original outcome: " + digest)
