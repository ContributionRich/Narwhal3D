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

* ``SPLINE`` -> exact NURBS degree, full DXF knot vector, control points, and
  optional rational weights. No fit-point or polyline approximation is substituted.
* ``LWPOLYLINE`` -> ``{"type":"LWPOLYLINE","points":[(x,y,z),...],
  "closed": bool, "layer","color"}``.  ``closed`` is flag 70 bit 0 (value 1);
  a bulge (code 42) is read and DROPPED but COUNTED in ``skipped["bulge"]`` --
  we never silently lose the fact that one was there (the writer has no bulge
  output).
* ``BLOCK`` / ``INSERT`` / ``TEXT`` / ``POINT`` and any other non-geometry
  entity type are not parsed; each is counted in ``skipped[type]`` so nothing
  vanishes.

``insunits`` is the ``$INSUNITS`` integer (``4`` = millimetres); ``None`` when
the header has no ``$INSUNITS``.

REFUSED: a BINARY DXF (the byte sentinel ``AutoCAD Binary DXF\r\n\x1a\0``)
raises :class:`FileFormatError` *before* any line splitting -- a text reader
would mis-parse those bytes. ASCII ``$ACADVER`` generations retain the same
group-code framing; this reader imports only explicitly supported entity records
and counts every other type, while malformed version markers are refused.

The entity, layer, and ``insunits`` contract mirrors the writer exactly so the
two modules form a true round-trip pair.
"""
from __future__ import annotations

import os


class FileFormatError(ValueError):
    """The file is not a DXF we can read (e.g. a binary DXF)."""


def _native_dxf():
    """Lazily resolve the native DXF adapter, or ``None``.

    Returns the ``native_pure`` module when it can be imported (production,
    where this file is ``narwhal3d.dxf_reader``) or loaded from this directory
    (spike tests load this module by file path, so ``narwhal3d`` is not an
    importable package there). ``None`` means "use the unchanged Python
    reader" -- the native library is absent, it predates the DXF decode
    exports, or the adapter cannot be imported. (Identical to the accessor in
    ``dxf_writer``; the two share the same cached ``native_pure`` module.)
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


# The real binary DXF magic header: this exact byte string marks a binary
# file (the sentinel is the AutoCAD spec's binary signature). Detected before
# any line splitting, because a text reader would mis-parse these bytes.
_BINARY_SENTINEL = b"AutoCAD Binary DXF\r\n\x1a\0"


def read_dxf(path):
    """Read *path* as a DXF -> the dict described in the module docstring.

    Raises :class:`FileFormatError` for a binary DXF (by its byte sentinel),
    malformed ASCII version markers, malformed exact entities, or a truncated file.

    When an ABI-compatible native decoder is available its (struct-identical)
    output is used; on any native unavailability or rejection the unchanged
    Python reader below runs (and raises the same ``FileFormatError`` for the
    same inputs, so the error contract is preserved).
    """
    # Try the native decoder first (a transparent accelerator). It returns None
    # for exactly the inputs the Python reader below would raise on (a binary
    # sentinel, a non-AC1009 $ACADVER, a truncated line count, or an
    # unparseable value), so falling through preserves the error contract.
    native = _native_dxf()
    if native is not None and hasattr(native, "decode_dxf"):
        try:
            with open(path, "rb") as fh:
                raw_bytes = fh.read()
            decoded = native.decode_dxf(raw_bytes)
            if decoded is not None:
                return decoded
        except Exception:  # noqa: BLE001 -- any failure degrades to the fallback
            pass

    # REFUSE A BINARY DXF BY ITS MAGIC SENTINEL -- the real binary header is
    # b"AutoCAD Binary DXF\r\n\x1a\0". Detected from a small raw read, BEFORE
    # any line splitting, so the bytes are never mis-parsed as ASCII group
    # codes.
    with open(path, "rb") as fh:
        head = fh.read(32)
    if head.startswith(_BINARY_SENTINEL):
        raise FileFormatError(
            "binary DXF (AutoCAD Binary DXF sentinel) -- this reader is "
            "ASCII R12 only")

    # Read in TEXT MODE, exactly as the original reader did: universal-newline
    # line iteration splits only on \n, \r and \r\n.  (str.splitlines() would
    # also split on \v, \f, \x1c-\x1e and \x85 -- it could split a value
    # mid-group-code and is NOT line-iteration.)
    with open(path, "r", encoding="latin-1") as fh:
        lines = [ln.rstrip("\r\n") for ln in fh]
    pairs = _pairs(lines)

    # ASCII DXF preserves group-code framing across versions. The reader imports
    # only explicitly supported entity records and reports every other type, so a
    # newer ASCII producer is safe; malformed/non-ASCII version names still fail.
    for i, (code, val) in enumerate(pairs):
        if code == 9 and val == "$ACADVER" and i + 1 < len(pairs) \
                and pairs[i + 1][0] == 1:
            acadver = pairs[i + 1][1]
            if not (len(acadver) == 6 and acadver.startswith("AC")
                    and acadver[2:].isdigit()):
                raise FileFormatError(
                    "malformed ASCII DXF version ($ACADVER=%s)" % acadver)
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
    layer_true_colors = {}
    lo, hi = _section_range(pairs, "TABLES")
    i = lo
    while i < hi:
        if pairs[i][0] == 0 and pairs[i][1] == "LAYER":
            name = None
            color = 7
            true_color = None
            j = _next_code0(pairs, i + 1, hi)
            for code, value in pairs[i + 1:j]:
                if code == 2:
                    name = value
                elif code == 62:
                    color = int(value)
                elif code == 420:
                    true_color = int(value)
                    color = 7
                    if not 0 <= true_color <= 0xFFFFFF:
                        raise FileFormatError("DXF layer true color is outside 0x000000..0xFFFFFF")
            if name is not None:
                layers[name] = color
                if true_color is not None:
                    layer_true_colors[name] = true_color
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

        if t in ("LINE", "CIRCLE", "ARC", "LWPOLYLINE", "SPLINE"):
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
                elif c == 420:
                    true_color = int(v)
                    ent["color"] = 7
                    if not 0 <= true_color <= 0xFFFFFF:
                        raise FileFormatError(
                            "DXF entity true color is outside 0x000000..0xFFFFFF")
                    ent["true_color"] = true_color
                elif c == 70 and t in ("LWPOLYLINE", "SPLINE"):
                    closed = (int(v) & 1) != 0       # bit 0 (value 1) = closed
                    have.setdefault(c, []).append(float(v))
                elif c == 42 and t == "LWPOLYLINE":
                    bulge += 1
                elif c in (10, 20, 30, 11, 21, 31, 40, 41, 50, 51,
                           71, 72, 73, 74):
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
            elif t == "SPLINE":
                flags = int(have.get(70, [0])[0])
                degree = int(have.get(71, [0])[0])
                knot_count = int(have.get(72, [0])[0])
                point_count = int(have.get(73, [0])[0])
                fit_count = int(have.get(74, [0])[0])
                knots = have.get(40, [])
                xs, ys = have.get(10, []), have.get(20, [])
                zs = have.get(30, [0.0] * len(xs))
                weights = have.get(41, [])
                if flags & 2:
                    raise FileFormatError("periodic DXF SPLINE is not implemented exactly")
                if not 1 <= degree <= 11 or point_count <= degree:
                    raise FileFormatError("DXF SPLINE has an invalid degree/control count")
                if (knot_count != len(knots) or point_count != len(xs)
                        or len(ys) != point_count or len(zs) != point_count
                        or fit_count < 0 or knot_count != point_count + degree + 1
                        or weights and (len(weights) != point_count
                                           or any(weight <= 0 for weight in weights))
                        or any(knots[n] > knots[n + 1] for n in range(len(knots) - 1))):
                    raise FileFormatError("DXF SPLINE has inconsistent exact NURBS data")
                ent.update({
                    "degree": degree, "knots": knots,
                    "points": list(zip(xs, ys, zs)), "weights": weights,
                    "closed": closed,
                })
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
                elif c == 420:
                    true_color = int(v)
                    ent["color"] = 7
                    if not 0 <= true_color <= 0xFFFFFF:
                        raise FileFormatError(
                            "DXF entity true color is outside 0x000000..0xFFFFFF")
                    ent["true_color"] = true_color
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
            "layer_true_colors": layer_true_colors,
            "insunits": insunits, "skipped": skipped}
