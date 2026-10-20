#!/usr/bin/env python3
"""Helper for audit gate 6: write and reopen documents in FRESH processes.

Usage:
  python3 _roundtrip_reader.py write TARGET.3dm OTHER.3dm   (build + save; prints the truth)
  python3 _roundtrip_reader.py read  OTHER.3dm TARGET.3dm   (reopen; prints what came back)

Each prints one ``ROUNDTRIP-JSON {...}`` line mapping object name -> master face types.

Both writer and reader run in fresh processes so process-local master identifiers cannot
accidentally survive the test. The reader opens an unrelated document first to populate its
registry, then opens the target; this exposes identifier collisions that an in-process reset
cannot reproduce.
"""
import importlib.util
import json
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def _load(name, rel, is_pkg=False):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    if is_pkg:
        mod.__path__ = [os.path.dirname(os.path.join(ROOT, rel))]
    spec.loader.exec_module(mod)
    return mod


kernel = _load("narwhal3d.kernel", "narwhal3d/kernel/__init__.py", is_pkg=True)
_load("narwhal3d.interpolate", "narwhal3d/interpolate.py")
_load("narwhal3d.edgepick", "narwhal3d/edgepick.py")
bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")
import rhino3dm as r3  # noqa: E402


def _describe(occt, master):
    """Return face types and centroid so a misplaced solid cannot pass by topology alone."""
    if master is None:
        return None
    return {"faces": occt.face_types(master),
            "centroid": [round(v, 6) for v in occt.surface_centroid(master)]}


def _open(path):
    f = r3.File3dm.Read(path)
    named = {(o.Attributes.Name or ""): o.Geometry for o in f.Objects}
    bridge.rehydrate_document(list(named.values()))
    return named


def _write(target, other):
    """Build one solid per master kind, save them, and save an unrelated document."""
    ef = _load("narwhal3d.edgefillet", "narwhal3d/edgefillet.py")
    sweep = _load("narwhal3d.sweep", "narwhal3d/sweep.py")
    kinds = {
        "fillet": lambda: ef.fillet_edges(bridge.make_box((0, 0, 0), (4, 4, 0), 4.0), 0.5),
        "chamfer": lambda: ef.chamfer_edges(bridge.make_box((0, 0, 0), (4, 4, 0), 4.0), 0.5),
        "pipe+cap": lambda: sweep.pipe(
            bridge.make_arc((6, 0, 0), (0, 6, 0), (-6, 0, 0)), 1.5, cap="Flat"),
        "boolean": lambda: bridge.boolean_brep(
            bridge.make_box((0, 0, 0), (4, 4, 0), 4.0),
            bridge.make_cylinder((2, 2, 0), 1.5, 6.0), "UNION"),
        # A capped loft and sweep exercise recipe-based master reconstruction.
        "loft+cap": lambda: bridge.cap_planar_holes_occt(bridge.loft_brep(
            [bridge.make_circle((20, 0, 0), 5.0), bridge.make_circle((20, 0, 6), 3.0)],
            style="Straight")),
        "sweep1": lambda: sweep.sweep1(
            bridge.make_arc((10, 0, 30), (7.0710678118654755, 7.0710678118654755, 30),
                            (0, 10, 30)),
            bridge.make_line((10, 0, 30), (11, 0, 31))),
        # The moved compound proves reconstruction retains its final placement.
        "moved boolean": lambda: bridge.transform_geometry(
            bridge.boolean_brep(bridge.make_box((0, 0, 0), (4, 4, 0), 4.0),
                                bridge.make_cylinder((2, 2, 0), 1.5, 6.0), "UNION"),
            r3.Transform.Translation(100.0, 0.0, 0.0)),
    }
    occt = bridge._occt()
    truth = {}
    doc = r3.File3dm()
    for name, build in kinds.items():
        geom = build()
        master = kernel.master_of(geom)
        truth[name] = _describe(occt, master)
        attr = r3.ObjectAttributes()
        attr.Name = name
        doc.Objects.AddBrep(geom, attr)
    doc.Write(target, 8)
    # The unrelated document is a single boolean. Reopened FIRST, it is rehydrated into the
    # reader's very first id -- which, with a counter, is the id the target's fillet was saved
    # under. That is the collision this gate exists to catch.
    o = r3.File3dm()
    o.Objects.AddBrep(bridge.boolean_brep(
        bridge.make_box((10, 0, 0), (13, 3, 0), 3.0),
        bridge.make_sphere((11.5, 1.5, 3.0), 1.0), "DIFFERENCE"))
    o.Write(other, 8)
    return truth


# Run under `blender -b --python THIS -- MODE A B` (edge fillets import bmesh), so the script's
# own arguments are the ones after "--".
args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
mode = args[0]
if mode == "write":
    print("ROUNDTRIP-JSON " + json.dumps(_write(args[1], args[2]), sort_keys=True))
else:
    first, target = args[1], args[2]
    _open(first)                   # populate the registry with an unrelated document
    occt = bridge._occt()
    out = {}
    for name, geom in _open(target).items():
        master = kernel.master_of(geom)
        out[name] = _describe(occt, master)
    print("ROUNDTRIP-JSON " + json.dumps(out, sort_keys=True))
