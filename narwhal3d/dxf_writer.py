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


def _num(x):
    """A number with up to 9 significant digits (``%.9g``)."""
    return "%.9g" % (x,)


def write_dxf(path, entities, layers=None) -> int:
    """Write *entities* to *path* as an AutoCAD R12 ASCII DXF file.

    Structure: HEADER (``$ACADVER = AC1009``, ``$INSUNITS = 4``), a TABLES
    section holding a LAYER table (one entry per layer), an ENTITIES section
    with the entities, then ``EOF``. Returns the byte count written.
    """
    layers = dict(layers) if layers else {}
    # any layer an entity names must appear in the table (default color 7)
    for e in entities:
        name = e.get("layer", "0")
        if name not in layers:
            layers[name] = 7

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
    emit(70, len(layers))
    for name, color in layers.items():
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
