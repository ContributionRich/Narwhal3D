"""Persistent native-document wire contracts, independent of application/kernel patch versions.

Keep old readers when adding a schema. Producer version is diagnostic metadata, not a
wire-format identifier. Each admitted BinTools format is exercised by the compatibility
suite with a real exact solid, and all payloads still pass the isolated kernel validator.
Unknown schemas/encodings and newer, untested binary formats must fail explicitly.
"""
SUPPORTED_SCHEMAS = frozenset({1})
SUPPORTED_BINTOOLS_FORMATS = frozenset({1, 2, 3, 4})


def supported_manifest(value, current_bintools_format):
    if not isinstance(value, dict):
        return False
    schema, binary_format = value.get("schema"), value.get("bintools_format")
    producer = value.get("occt_version")
    return (value.get("format") == "Narwhal 3D Document"
            and type(schema) is int and schema in SUPPORTED_SCHEMAS
            and value.get("document_member") == "document.3dm"
            and value.get("master_encoding") == "OCCT BinTools"
            and value.get("summary_codec", "schema1-v1") == "schema1-v1"
            and isinstance(producer, str) and 0 < len(producer) <= 128
            and type(binary_format) is int
            and binary_format in SUPPORTED_BINTOOLS_FORMATS
            and binary_format <= current_bintools_format
            and isinstance(value.get("masters"), list))


def writer_bintools_format(current_bintools_format):
    """Write a tested encoding even after the bundled kernel's CURRENT changes."""
    choices = [version for version in SUPPORTED_BINTOOLS_FORMATS if version <= current_bintools_format]
    if not choices:
        raise ValueError("no tested native binary encoding is writable by this kernel")
    return max(choices)


def schema1_volume(shape):
    """Frozen historical summary measurement, NOT the application's mass-properties API.

    Keep this explicit epsilon and representation when improving normal measurement
    commands. Exact-proxy correlation compares NURBS to NURBS separately. A future
    summary algorithm needs its own codec, not a reinterpretation of saved numbers.
    """
    from .kernel import occt_backend
    return occt_backend.document_schema1_volume(shape)
