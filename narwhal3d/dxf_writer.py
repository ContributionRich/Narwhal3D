"""DXF writer (AutoCAD R12 ASCII). Pure Python, standard library only.

DXF is commonly consumed by laser cutters and CNC workflows. Upstream geometry
code converts exact geometry into the entities below; this module only writes them.

``entities`` is a sequence of dicts:

    LINE     {"type":"LINE","p0":(x,y,z),"p1":(x,y,z),"layer":...,"color":...}
    CIRCLE   {"type":"CIRCLE","center":(x,y,z),"radius":r,...}
    ARC      {"type":"ARC","center":(x,y,z),"radius":r,"start_deg":a0,
              "end_deg":a1,...}
    POLYLINE {"type":"POLYLINE","points":[(x,y,z),...],"closed":bool,...}

``layers`` is an optional ``{"name": aci_color}`` map. Any layer an entity
names is added to the table if it is missing (with the default color 7).

R12 (``AC1009``) is an old, widely supported DXF format. Every group code sits
on its own line with the value on
the next. ``$INSUNITS = 4`` (millimetres). ARC angles are degrees,
counter-clockwise from +X. Colors are AutoCAD Color Index (1 red, 5 blue,
7 black/white); the default is 7.
"""

import os


def _num(x):
    """A number with up to 9 significant digits (``%.9g``)."""
    return "%.9g" % (x,)


def _merged_layers(entities, layers):
    """Return a list of ``(name, color)`` pairs for the LAYER table.

    The caller's layers come first (in their order, with their colors); any
    layer an entity names but the caller did not list is added after, with
    the default color 7. Order is stable: the first time a name is seen is
    the position it keeps.
    """
    out = {}
    if layers:
        for name, color in layers.items():
            out[name] = color
    for e in entities:
        name = e.get("layer", "0")
        if name not in out:
            out[name] = 7
    return list(out.items())


def build_dxf_bytes(entities, layers=None) -> bytes:
    """Build the complete R12 ASCII DXF file bytes for *entities*.

    Pure byte building (no I/O): the same HEADER / TABLES / ENTITIES / EOF
    layout ``write_dxf`` writes. The native accelerator
    (``native_pure.encode_dxf``) and the Python fallback both produce the
    exact same bytes for the same input; this function is the single
    source of truth for the Python side's layout.
    """
    merged = _merged_layers(entities, layers)
    lines = []

    def emit(code, value):
        lines.append(str(code))
        lines.append(str(value))

    # --- HEADER ----------------------------------------------------------
    emit(0, "SECTION")
    emit(2, "HEADER")
    emit(9, "$ACADVER")
    emit(1, "AC1009")
    emit(9, "$INSUNITS")
    emit(70, 4)  # 4 = millimetres
    emit(0, "ENDSEC")

    # --- TABLES ----------------------------------------------------------
    emit(0, "SECTION")
    emit(2, "TABLES")
    emit(0, "TABLE")
    emit(2, "LAYER")
    emit(70, len(merged))
    for name, color in merged:
        emit(0, "LAYER")
        emit(2, name)
        emit(70, 0)
        emit(62, color)
        emit(6, "CONTINUOUS")
    emit(0, "ENDTAB")
    emit(0, "ENDSEC")

    # --- ENTITIES --------------------------------------------------------
    emit(0, "SECTION")
    emit(2, "ENTITIES")
    for e in entities:
        _entity(emit, e)
    emit(0, "ENDSEC")

    # --- EOF -------------------------------------------------------------
    emit(0, "EOF")

    data = ("\n".join(lines) + "\n").encode("latin-1")
    return data


def _native_dxf():
    """Lazily resolve the native DXF adapter, or ``None``.

    Returns the ``native_pure`` module when it can be imported (production,
    where this file is ``narwhal3d.dxf_writer``) or loaded from this directory
    (spike tests load this module by file path, so ``narwhal3d`` is not an
    importable package there). ``None`` means "use the unchanged Python
    writer" -- the native library is absent, it predates the DXF exports, or
    the adapter cannot be imported.
    """
    try:
        from . import native_pure as _mod  # production package context
        return _mod
    except ImportError:
        pass
    try:
        import importlib.util
        import sys
        _canon = "narwhal3d_native_pure"
        if _canon in sys.modules:
            return sys.modules[_canon]
        _path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "native_pure.py")
        if not os.path.isfile(_path):
            return None
        _spec = importlib.util.spec_from_file_location(_canon, _path)
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules[_canon] = _mod
        _spec.loader.exec_module(_mod)
        return _mod
    except Exception:  # noqa: BLE001 -- any failure degrades to the fallback
        return None


def write_dxf(path, entities, layers=None) -> int:
    """Write *entities* to *path* as an AutoCAD R12 ASCII DXF file.

    Structure: HEADER (``$ACADVER = AC1009``, ``$INSUNITS = 4``), a TABLES
    section holding a LAYER table (one entry per layer), an ENTITIES section
    with the entities, then ``EOF``. Returns the byte count written.

    The one-pass native encoder (Q117-02) is the production fast path when it
    is available and the input is at/above the measured crossover: it
    serializes the file exactly once into a Python-owned ``bytearray`` (which
    is written directly, no ``bytes`` copy). Measured, native is never >=10%
    faster than the Python writer at any tested size, so the crossover is set
    above every realistic input and this path is inactive in practice -- the
    unchanged Python writer below is the active route (the complete fallback,
    and also the byte-exact path for non-ASCII Latin-1 layer names).
    """
    # Snapshot the outer stream and POLYLINE point streams once, up front. A
    # one-shot generator is consumed HERE (not by the native packing or the
    # crossover check), so neither the native route nor the Python fallback
    # ever sees an exhausted iterator; both operate on the same materialized
    # list.
    materialized = []
    for entity in entities:
        if entity.get("type") == "POLYLINE":
            entity = dict(entity)
            entity["points"] = list(entity["points"])
        materialized.append(entity)
    # One-pass native route (Q117-02): active only when the one-pass export is
    # available and the materialized input clears the measured crossover.
    _native = _native_dxf()
    if _native is not None and _native._dxf_native_active(materialized):
        result = _native._native_dxf_encode_into(materialized, layers)
        if result is not None:
            out, written = result
            with open(path, "wb") as fh:
                fh.write(memoryview(out)[:written])
            return written
    data = build_dxf_bytes(materialized, layers)
    with open(path, "wb") as fh:
        fh.write(data)
    return len(data)


def _point(emit, p):
    emit(10, _num(p[0]))
    emit(20, _num(p[1]))
    emit(30, _num(p[2]))


def _entity(emit, e):
    t = e["type"]
    layer = e.get("layer", "0")
    color = e.get("color", 7)

    if t == "LINE":
        emit(0, "LINE")
        emit(8, layer)
        emit(62, color)
        _point(emit, e["p0"])
        emit(11, _num(e["p1"][0]))
        emit(21, _num(e["p1"][1]))
        emit(31, _num(e["p1"][2]))
    elif t == "CIRCLE":
        emit(0, "CIRCLE")
        emit(8, layer)
        emit(62, color)
        _point(emit, e["center"])
        emit(40, _num(e["radius"]))
    elif t == "ARC":
        emit(0, "ARC")
        emit(8, layer)
        emit(62, color)
        _point(emit, e["center"])
        emit(40, _num(e["radius"]))
        emit(50, _num(e["start_deg"]))
        emit(51, _num(e["end_deg"]))
    elif t == "POLYLINE":
        emit(0, "POLYLINE")
        emit(8, layer)
        emit(62, color)
        emit(66, 1)
        emit(70, 1 if e.get("closed") else 0)
        for p in e["points"]:
            emit(0, "VERTEX")
            emit(8, layer)
            _point(emit, p)
        emit(0, "SEQEND")
    else:
        raise ValueError("unknown entity type %r" % (t,))
