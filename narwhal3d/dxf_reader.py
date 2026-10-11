"""Read a DXF (AutoCAD R12 ASCII) back into the writer's entity dicts.

This is the companion reader for ``dxf_writer``: a file written by one reads
back into the same ``entities`` dicts (``LINE`` / ``CIRCLE`` / ``ARC`` /
``POLYLINE``), the same ``layers`` ``{name: aci}`` table, and the same
``insunits`` integer, enabling a true drawing round trip.

``read_dxf(path) -> dict``::

    {
      "entities": [ {"type","p0","p1","layer","color"},   # LINE
                    {"type","center","radius","layer","color"},  # CIRCLE
                    {"type","center","radius","start_deg","end_deg","layer","color"},
                    {"type","points":[(x,y,z),...],"closed","layer","color"},
                    ... ],
      "layers":   {name: aci},
      "insunits": int or None,
      "skipped":  {type: count},   # BLOCK / INSERT / TEXT / "bulge" / ...
    }

Entity dicts are exactly the shapes the writer consumes, so a read can be fed
straight back into ``write_dxf``.

Also handled (the writer does not emit these, but a real DXF has them):

* ``LWPOLYLINE`` -> ``{"type":"LWPOLYLINE","points":[(x,y,z),...],
  "closed": bool, "layer","color"}``.  ``closed`` is flag 70 bit 1 (value 2);
  a bulge (code 42) is read and DROPPED but COUNTED in ``skipped["bulge"]`` --
  we never silently lose the fact that one was there (the writer has no bulge
  output).
* ``BLOCK`` / ``INSERT`` / ``TEXT`` / ``POINT`` and any other non-geometry
  entity type are not parsed; each is counted in ``skipped[type]`` so nothing
  vanishes.

``insunits`` is the ``$INSUNITS`` integer (``4`` = millimetres); ``None`` when
the header has no ``$INSUNITS``.

REFUSED BY NAME: a BINARY DXF (``$ACADVER`` = ``AC1004`` / ``AC1006`` / ... --
anything that is not the ASCII ``AC1009`` the writer produces) raises
:class:`FileFormatError` rather than being mis-parsed byte-for-byte.  R12
(``AC1009``) is always ASCII; the binary encodings began with R13.

The entity, layer, and ``insunits`` contract mirrors the writer exactly so the
two modules form a true round-trip pair.
"""
from __future__ import annotations


class FileFormatError(ValueError):
    """The file is not a DXF we can read (e.g. a binary DXF)."""



def _pairs(lines):
    """Pair the alternating code / value lines -> ``[(int(code), str(value))]``.

    A DXF line is a group code; the NEXT line is its value.  A trailing
    code with no value (a truncated file) raises :class:`FileFormatError`.
    """
    pairs = []
    i = 0
    while i < len(lines):
        if i + 1 >= len(lines):
            raise FileFormatError("truncated DXF: group code with no value")
        pairs.append((int(lines[i]), lines[i + 1]))
        i += 2
    return pairs


def _section_range(pairs, name):
    """Return ``[start, end)`` index range of the section *name* (or ``(0,0)``).

    A section is ``0 SECTION`` / ``2 <name>`` ... ``0 ENDSEC``.  We only need
    the bounds so we can scan the ENTITIES (and LAYER) contents by index.
    """
    for i, (code, val) in enumerate(pairs):
        if code == 0 and val == "SECTION" and i + 1 < len(pairs) \
                and pairs[i + 1][1] == name:
            j = i
            while j < len(pairs) and not (pairs[j][0] == 0
                                          and pairs[j][1] == "ENDSEC"):
                j += 1
            return i, j
    return 0, 0


def _next_code0(pairs, start, hi):
    """First index j (start <= j < hi) with group code 0, else *hi*.

    In the ENTITIES section every entity -- geometry or not, including the
    VERTEX / SEQEND children of a POLYLINE -- begins with a group-code-0 line,
    so an entity's body is exactly the lines up to (not including) the next
    code-0 line.
    """
    j = start
    while j < hi and pairs[j][0] != 0:
        j += 1
    return j


_BINARY_ACADVERS = frozenset((
    "AC1004", "AC1006", "AC1008", "AC1010", "AC1012",
    "AC1014", "AC1015", "AC1018", "AC1021", "AC1024", "AC1027"))


def read_dxf(path):
    """Read *path* as a DXF -> the dict described in the module docstring.

    Raises :class:`FileFormatError` for a binary DXF (by name, from
    ``$ACADVER``) or a truncated file.
    """
    with open(path, "r", encoding="latin-1") as fh:
        lines = [ln.rstrip("\r\n") for ln in fh]

    pairs = _pairs(lines)

    # REFUSE A BINARY DXF BY NAME -- the writer is ASCII R12 (AC1009); any
    # other $ACADVER we recognise is a binary encoding and would be mis-parsed.
    for i, (code, val) in enumerate(pairs):
        if code == 9 and val == "$ACADVER" and i + 1 < len(pairs) \
                and pairs[i + 1][0] == 1:
            acadver = pairs[i + 1][1]
            if acadver in _BINARY_ACADVERS:
                raise FileFormatError(
                    "binary DXF ($ACADVER=%s) -- this reader is ASCII R12 only"
                    % acadver)
            break

    # --- insunits ($INSUNITS -> int or None) ------------------------------
    insunits = None
    for i, (code, val) in enumerate(pairs):
        if code == 9 and val == "$INSUNITS" and i + 1 < len(pairs) \
                and pairs[i + 1][0] == 70:
            insunits = int(pairs[i + 1][1])
            break

    # --- layers (LAYER table: name -> aci color 62) -----------------------
    layers = {}
    lo, hi = _section_range(pairs, "TABLES")
    i = lo
    while i < hi:
        if pairs[i][0] == 0 and pairs[i][1] == "LAYER":
            name = None
            color = 7
            j = i + 1
            while j < hi and not (pairs[j][0] == 0 and pairs[j][1] == "LAYER"):
                if pairs[j][0] == 2:
                    name = pairs[j][1]
                elif pairs[j][0] == 62:
                    color = int(pairs[j][1])
                j += 1
            if name is not None:
                layers[name] = color
            i = j
        else:
            i += 1

    # --- entities ----------------------------------------------------------
    entities = []
    skipped = {}
    lo, hi = _section_range(pairs, "ENTITIES")
    i = lo
    while i < hi:
        code, val = pairs[i]
        if code != 0:
            i += 1
            continue

        # structural section markers (the section range includes the leading
        # ``0 SECTION`` / ``2 <name>`` lines) -- not entities, skip without counting
        if val in ("SECTION", "ENDSEC", "TABLE", "ENDTAB"):
            i += 1
            continue

        t = val

        if t in ("LINE", "CIRCLE", "ARC", "LWPOLYLINE"):
            ent = {"type": t, "layer": "0", "color": 7}
            have = {}          # group code -> [values in order]
            bulge = 0
            closed = False
            j = _next_code0(pairs, i + 1, hi)
            for k in range(i + 1, j):
                c, v = pairs[k]
                if c == 8:
                    ent["layer"] = v
                elif c == 62:
                    ent["color"] = int(v)
                elif c == 70 and t == "LWPOLYLINE":
                    closed = (int(v) & 2) != 0       # bit 1 = closed
                elif c == 42 and t == "LWPOLYLINE":
                    bulge += 1
                elif c in (10, 20, 30, 11, 21, 31, 40, 50, 51):
                    have.setdefault(c, []).append(float(v))
            if bulge:
                skipped["bulge"] = skipped.get("bulge", 0) + bulge
            if t == "LINE":
                ent["p0"] = (have[10][0], have[20][0], have[30][0])
                ent["p1"] = (have[11][0], have[21][0], have[31][0])
            elif t == "CIRCLE":
                ent["center"] = (have[10][0], have[20][0], have[30][0])
                ent["radius"] = have[40][0]
            elif t == "ARC":
                ent["center"] = (have[10][0], have[20][0], have[30][0])
                ent["radius"] = have[40][0]
                ent["start_deg"] = have[50][0]
                ent["end_deg"] = have[51][0]
            elif t == "LWPOLYLINE":
                # vertices are ordered 10,20,[42],[30]; a new vertex starts at 10
                # (z is per-vertex, so it must be walked in order, not grouped).
                pts = []
                vx = vy = vz = 0.0
                started = False
                for k in range(i + 1, j):
                    c, v = pairs[k]
                    if c == 10:
                        if started:
                            pts.append((vx, vy, vz))
                        vx, vy, vz = float(v), 0.0, 0.0
                        started = True
                    elif c == 20:
                        vy = float(v)
                    elif c == 30:
                        vz = float(v)
                if started:
                    pts.append((vx, vy, vz))
                ent["points"] = pts
                ent["closed"] = closed
            entities.append(ent)
            i = j

        elif t == "POLYLINE":
            # POLYLINE: header flags here, then its points live in the
            # VERTEX entities that follow, terminated by SEQEND.
            ent = {"type": "POLYLINE", "layer": "0", "color": 7, "closed": False}
            j = _next_code0(pairs, i + 1, hi)      # stop at the first VERTEX
            for k in range(i + 1, j):
                c, v = pairs[k]
                if c == 8:
                    ent["layer"] = v
                elif c == 62:
                    ent["color"] = int(v)
                elif c == 70:
                    ent["closed"] = (int(v) & 1) != 0    # bit 0 = closed
            # walk VERTEX ... SEQEND, collecting one point per VERTEX
            points = []
            while j < hi and not (pairs[j][0] == 0 and pairs[j][1] == "SEQEND"):
                if pairs[j][0] == 0 and pairs[j][1] == "VERTEX":
                    cx = cy = cz = 0.0
                    m = _next_code0(pairs, j + 1, hi)   # VERTEX body ends at next code-0
                    for k in range(j + 1, m):
                        c, v = pairs[k]
                        if c == 10:
                            cx = float(v)
                        elif c == 20:
                            cy = float(v)
                        elif c == 30:
                            cz = float(v)
                    points.append((cx, cy, cz))
                    j = m
                elif pairs[j][0] == 0:
                    break        # next entity with no SEQEND -- stop here
                else:
                    j += 1
            ent["points"] = points
            entities.append(ent)
            # step past the SEQEND line itself
            if j < hi and pairs[j][1] == "SEQEND":
                j += 1
            i = j

        else:
            # BLOCK / INSERT / TEXT / POINT / ... -- not geometry we model;
            # count it so nothing is silently dropped, and skip its body.
            skipped[t] = skipped.get(t, 0) + 1
            i = _next_code0(pairs, i + 1, hi)

    return {"entities": entities, "layers": layers,
            "insunits": insunits, "skipped": skipped}
