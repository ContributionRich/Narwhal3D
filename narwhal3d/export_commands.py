"""Export selected objects to CAD, mesh, and 2-D vector formats.

The Export command saves selected objects to a new file without changing the active
model. Save / Save As may also route a whole model here when given another supported
format's extension.

Formats, and how exact each is:

* **.3dm** -- the selected objects, their layers and the model units, in a new file.
  Geometry is duplicated with its user strings, which carry the build recipes,
  so a solid reopens as the same analytic solid.
* **.step / .stp** -- the analytic OCCT solids -- the master, or one rebuilt from the
  recipe (`STEPControl_AsIs`: a cylinder stays a
  CYLINDRICAL_SURFACE). Anything without a master, or tagged mesh-derived, is REFUSED
  by name (`kernel.require_analytic`) -- a STEP file is what a shop machines from.
  The export is unit-aware: the file declares the model's unit and holds its numbers
  (MEASURED: an inch box writes `CONVERSION_BASED_UNIT('INCH')` with coordinates 1.0).
* **.stl / .obj** -- an export mesh of each solid's exact shape, made by OCCT's
  `BRepMesh` at an explicit chord TOLERANCE (the most a triangle may stray from the
  surface) and ANGLE. The document keeps its exact solid
  (this mesh is an output, never the document geometry). Model units; STL/OBJ carry none.
* **.dxf** -- the curves: lines, circles and arcs EXACT as LINE / CIRCLE / ARC,
  polylines as POLYLINE; anything else as a POLYLINE within the tolerance, and the
  message NAMES it approximate. Written in millimetres (the writer's `$INSUNITS` = 4),
  so an inch model is scaled by 25.4 rather than mislabelled.
* **.ai / .pdf / .svg** -- a 2-D snapshot of
  the ACTIVE parallel viewport at 1:1 on a page the size of the drawing, through the
  same pipeline as Print (`print2d`; exact for polynomial curves, <= 0.001 mm for
  circles; silhouettes and perspective refused by name). A perspective viewport cannot
  preserve drawing scale and units.

Not built: the per-format options dialogs, .dwg, IGES; DXF of surfaces and solids.
"""
from __future__ import annotations

import math
import os

from . import commands
from . import filedialog
from . import print2d

TEXT = commands.TEXT
OBJECTS_MULTI = commands.OBJECTS_MULTI
opts = commands.opts

EXPORT_FILTERS = (("3DM Models", "*.3dm"), ("STEP", "*.step *.stp"),
                  ("STL (Stereolithography)", "*.stl"), ("Wavefront OBJ", "*.obj"),
                  ("AutoCAD DXF", "*.dxf"), ("Adobe Illustrator", "*.ai"),
                  ("PDF", "*.pdf"), ("SVG", "*.svg"))
FORMATS = (".3dm", ".step", ".stp", ".stl", ".obj", ".dxf", ".ai", ".pdf", ".svg")

#: The export mesh's maximum angle between adjacent triangle normals. Tolerance
#: is the maximum distance between the exact object and the polygon mesh and defaults to
#: the document's absolute tolerance; the angle defaults to this value.
DEFAULT_MESH_ANGLE_DEG = 15.0


def _app():
    from .state import app_state

    return app_state


def _export_3dm(model, objs, path):
    import rhino3dm as r3

    out = r3.File3dm()
    try:
        out.Settings.ModelUnitSystem = model.file.Settings.ModelUnitSystem
    except Exception:  # noqa: BLE001
        pass
    # every layer, in order, so each object's LayerIndex still points at its layer
    for i in range(len(model.file.Layers)):
        out.Layers.Add(model.file.Layers[i])
    for obj in objs:
        geom = obj.Geometry.Duplicate()
        out.Objects.Add(geom, obj.Attributes)
    tmp = f"{path}.saving-{os.getpid()}.tmp"
    if not out.Write(tmp, 8):
        raise OSError("the 3dm writer reported failure")
    os.replace(tmp, path)
    return f"Exported {len(objs)} object(s) to {path}"


def _export_step(model, objs, path):
    from . import kernel
    from .kernel import occt_backend as ob

    _mm, unit = print2d.model_unit_mm(model)
    step_unit = ob.STEP_UNITS.get(unit)
    if step_unit is None:
        raise ValueError(f"STEP export of a {unit} model is not implemented yet (STEP "
                         f"has no unit for it here) -- nothing was written")
    if not ob.available():
        raise ValueError("STEP export needs the OCCT kernel (vendor/occt), which is "
                         "not available")
    shapes, refused = [], []
    for obj in objs:
        geom = obj.Geometry
        name = obj.Attributes.Name or "?"
        try:
            kernel.require_analytic(geom, "STEP export")
        except Exception as e:  # noqa: BLE001
            refused.append(f"{name} ({e})")
            continue
        # the master when there is one, else rebuilt from the RECIPE -- a primitive
        # (Box, Cylinder, ...) is an exact rhino3dm brep with a recipe and no master,
        # MEASURED; the booleans take operands the same way
        from . import bridge

        shape = bridge._occt_operand(geom)
        if shape is None:
            refused.append(f"{name} (no analytic solid behind it -- curves and "
                           f"plain surfaces are not exported to STEP yet)")
            continue
        shapes.append(shape)
    if refused:
        raise ValueError("STEP export REFUSED, nothing written: " + "; ".join(refused))
    if not shapes:
        raise ValueError("nothing to export")
    shape = ob.compound(shapes)
    tmp = f"{path}.saving-{os.getpid()}.tmp.step"
    ob.export_step(shape, tmp, unit=step_unit)
    os.replace(tmp, path)
    return f"Exported {len(shapes)} solid(s) to {path} (analytic STEP, {unit})"


def _export_meshes(model, objs, tolerance, angle_deg):
    """``([(name, vertices, triangles)], skipped)`` -- an EXPORT mesh per solid/surface
    from its exact OCCT shape at `tolerance` / `angle_deg`; a document Mesh as itself.
    Raises ValueError naming any brep with no exact shape (never a silent facet soup)."""
    import rhino3dm as r3

    from . import bridge
    from .kernel import occt_backend as ob

    if not tolerance > 0:
        raise ValueError("the export mesh tolerance must be greater than zero")
    meshes, refused, skipped = [], [], []
    for obj in objs:
        geom = obj.Geometry
        name = obj.Attributes.Name or "object"
        if isinstance(geom, r3.Mesh):
            verts = [(v.X, v.Y, v.Z) for v in geom.Vertices]
            tris = []
            for i in range(len(geom.Faces)):
                f = geom.Faces[i]
                tris.append((f[0], f[1], f[2]))
                if f[2] != f[3]:
                    tris.append((f[0], f[2], f[3]))
            meshes.append((name, verts, tris))
            continue
        if not isinstance(geom, (r3.Brep, r3.Extrusion)):
            skipped.append(name)
            continue
        shape = bridge._occt_operand(geom) if ob.available() else None
        if shape is None:
            refused.append(name)
            continue
        verts, tris = bridge._weld_tessellation(
            *ob.tessellate(shape, float(tolerance), math.radians(float(angle_deg))))
        meshes.append((name, verts, tris))
    if refused:
        raise ValueError("mesh export REFUSED, nothing written: no exact shape behind "
                         + ", ".join(refused))
    if not meshes:
        raise ValueError("nothing to export -- STL and OBJ hold meshes, and no solid, "
                         "surface or mesh was selected")
    return meshes, skipped


def _export_stl(model, objs, path, tolerance, angle_deg):
    from . import stl_writer

    meshes, skipped = _export_meshes(model, objs, tolerance, angle_deg)
    tris = [(v[a], v[b], v[c]) for _n, v, t in meshes for a, b, c in t]
    tmp = f"{path}.saving-{os.getpid()}.tmp"
    stl_writer.write_stl(tmp, tris, binary=True)
    os.replace(tmp, path)
    return _mesh_message("STL", path, len(meshes), len(tris), tolerance, angle_deg,
                         model, skipped)


def _export_obj(model, objs, path, tolerance, angle_deg):
    from . import obj_writer

    meshes, skipped = _export_meshes(model, objs, tolerance, angle_deg)
    tmp = f"{path}.saving-{os.getpid()}.tmp"
    obj_writer.write_obj(tmp, meshes)
    os.replace(tmp, path)
    return _mesh_message("OBJ", path, len(meshes), sum(len(t) for _n, _v, t in meshes),
                         tolerance, angle_deg, model, skipped)


def _mesh_message(kind, path, n, ntri, tolerance, angle_deg, model, skipped):
    _mm, unit = print2d.model_unit_mm(model)
    msg = (f"Exported {n} object(s) to {path} ({kind}, {ntri} triangles, mesh tolerance "
           f"{tolerance:g} {unit.lower()}, angle {angle_deg:g} deg; the model keeps its "
           f"exact geometry)")
    if skipped:
        msg += f". NOT written ({kind} holds meshes only): " + ", ".join(skipped)
    return msg


def _approx_points(curve, tol, t0, t1, depth=0):
    """Points along `curve` from t0 to t1 whose chords stay within `tol` (checked at the
    quarter, half and three-quarter points of every chord)."""
    a, b = curve.PointAt(t0), curve.PointAt(t1)
    worst = 0.0
    for f in (0.25, 0.5, 0.75):
        p = curve.PointAt(t0 + (t1 - t0) * f)
        # distance from p to the chord a-b
        ab = (b.X - a.X, b.Y - a.Y, b.Z - a.Z)
        ap = (p.X - a.X, p.Y - a.Y, p.Z - a.Z)
        L2 = ab[0] ** 2 + ab[1] ** 2 + ab[2] ** 2
        s = 0.0 if L2 == 0 else max(0.0, min(1.0, sum(x * y for x, y in zip(ab, ap)) / L2))
        worst = max(worst, math.dist((p.X, p.Y, p.Z),
                                     (a.X + ab[0] * s, a.Y + ab[1] * s, a.Z + ab[2] * s)))
    if worst <= tol or depth >= 18:
        return [(a.X, a.Y, a.Z), (b.X, b.Y, b.Z)]
    tm = (t0 + t1) / 2.0
    left = _approx_points(curve, tol, t0, tm, depth + 1)
    return left[:-1] + _approx_points(curve, tol, tm, t1, depth + 1)


def _dxf_entities(curve, tol, k):
    """``(entities, exact)`` for one curve, coordinates scaled by `k` (to millimetres)."""
    import rhino3dm as r3

    def P(p):
        return (p.X * k, p.Y * k, p.Z * k)

    if isinstance(curve, r3.PolyCurve):
        out, exact = [], True
        for seg in curve.Explode():
            e, ex = _dxf_entities(seg, tol, k)
            out += e
            exact = exact and ex
        return out, exact
    if curve.IsLinear() and not curve.IsClosed:
        return [{"type": "LINE", "p0": P(curve.PointAtStart),
                 "p1": P(curve.PointAtEnd)}], True
    pl = curve.TryGetPolyline()
    if pl is not None and len(pl) >= 2:
        pts = [P(pl[i]) for i in range(len(pl))]
        closed = bool(curve.IsClosed)
        if closed and len(pts) > 2:
            pts = pts[:-1]
        return [{"type": "POLYLINE", "points": pts, "closed": closed}], True
    # R12 CIRCLE / ARC lie in the World XY plane here (no extrusion direction is
    # written), so only an arc whose plane IS horizontal travels exactly.
    if curve.IsCircle() or curve.IsArc():
        if curve.IsCircle():
            c = curve.TryGetCircle()
            plane, radius = c.Plane, c.Radius
        else:
            a = curve.TryGetArc()
            plane, radius = a.Plane, a.Radius
        z = plane.ZAxis
        if abs(abs(z.Z) - 1.0) < 1e-12:
            ctr = P(plane.Origin)
            if curve.IsCircle():
                return [{"type": "CIRCLE", "center": ctr, "radius": radius * k}], True
            s, e = curve.PointAtStart, curve.PointAtEnd
            a0 = math.degrees(math.atan2(s.Y - plane.Origin.Y, s.X - plane.Origin.X))
            a1 = math.degrees(math.atan2(e.Y - plane.Origin.Y, e.X - plane.Origin.X))
            if z.Z < 0:                    # clockwise seen from +Z: DXF runs CCW
                a0, a1 = a1, a0
            return [{"type": "ARC", "center": ctr, "radius": radius * k,
                     "start_deg": a0 % 360.0, "end_deg": a1 % 360.0}], True
    d = curve.Domain
    pts = [(x * k, y * k, zz * k) for x, y, zz in _approx_points(curve, tol, d.T0, d.T1)]
    closed = bool(curve.IsClosed)
    if closed and len(pts) > 2:
        pts = pts[:-1]
    return [{"type": "POLYLINE", "points": pts, "closed": closed}], False


def _export_dxf(model, objs, path, tolerance):
    import rhino3dm as r3

    from . import dxf_writer

    unit_mm, unit = print2d.model_unit_mm(model)
    entities, approx, skipped = [], [], []
    for obj in objs:
        geom = obj.Geometry
        name = obj.Attributes.Name or "object"
        if not isinstance(geom, r3.Curve):
            skipped.append(name)
            continue
        es, exact = _dxf_entities(geom, float(tolerance), unit_mm)
        try:
            layer = model.file.Layers[int(obj.Attributes.LayerIndex)].Name or "0"
        except Exception:  # noqa: BLE001
            layer = "0"
        for e in es:
            e["layer"] = layer
        entities += es
        if not exact:
            approx.append(name)
    if not entities:
        raise ValueError("nothing to export -- DXF export writes curves, and no curve "
                         "was selected (surfaces and solids are not written to DXF yet)")
    tmp = f"{path}.saving-{os.getpid()}.tmp"
    dxf_writer.write_dxf(tmp, entities)
    os.replace(tmp, path)
    msg = f"Exported {len(entities)} DXF entit{'y' if len(entities) == 1 else 'ies'} to {path}"
    if unit != "Millimeters":
        msg += f" (in millimetres: {unit} scaled by {unit_mm:g})"
    if approx:
        msg += (f". APPROXIMATE (polyline within {tolerance:g} {unit.lower()}): "
                + ", ".join(approx))
    if skipped:
        msg += ". NOT written (DXF export writes curves only): " + ", ".join(skipped)
    return msg


def _export_2d(model, objs, path):
    from . import print_commands

    view = print_commands._view()
    if view.perspective:
        raise ValueError("a 2-D export of a PERSPECTIVE view cannot preserve scale -- "
                         "export from Top, Front, Right or another parallel view")
    setup = print2d.PageSetup(size=print2d.DRAWING, area="Extents", scale=1.0,
                              color="PrintColor")
    page = print2d.build_page(model, view, setup, objects=objs)
    if not page.strokes:
        raise ValueError("nothing in the export prints as curves")
    print2d.write(path, page, os.path.splitext(os.path.basename(path))[0])
    msg = (f"Exported {len(page.strokes)} curve(s) to {path} at 1:1 "
           f"({page.width_mm:.2f} x {page.height_mm:.2f} mm)")

    if page.notes:
        msg += ". NOTE: " + "; ".join(page.notes)
    return msg


def export_objects(model, objs, path, tolerance=None, angle_deg=None):
    """Write `objs` to `path` by its extension. Returns ``(ok, message)``.

    `tolerance` (model units) and `angle_deg` drive the STL/OBJ export mesh and the DXF
    approximation; None = the document's absolute tolerance / `DEFAULT_MESH_ANGLE_DEG`."""
    path = os.path.abspath(os.path.expanduser((path or "").strip().strip('"')))
    ext = os.path.splitext(path)[1].lower()
    if ext not in FORMATS:
        return False, (f"exporting to {ext or 'a file with no extension'} is not "
                       f"implemented yet -- use " + ", ".join(FORMATS))
    if not os.path.isdir(os.path.dirname(path)):
        return False, f"the folder {os.path.dirname(path)!r} does not exist"
    if not objs:
        return False, "nothing to export"
    try:
        if ext == ".3dm":
            return True, _export_3dm(model, objs, path)
        if ext in (".step", ".stp"):
            return True, _export_step(model, objs, path)
        from . import units

        tol = float(tolerance) if tolerance else units.document_tolerance(model)
        ang = float(angle_deg) if angle_deg else DEFAULT_MESH_ANGLE_DEG
        if ext == ".stl":
            return True, _export_stl(model, objs, path, tol, ang)
        if ext == ".obj":
            return True, _export_obj(model, objs, path, tol, ang)
        if ext == ".dxf":
            return True, _export_dxf(model, objs, path, tol)
        return True, _export_2d(model, objs, path)
    except ValueError as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001
        return False, f"could not export {path}: {e}"


class ExportCmd(commands.Command):
    name = "Export"
    description = ("Export the selected objects to a .3dm, .step, .stl, .obj, .dxf, .ai, "
                   ".pdf or .svg file (the model itself is unchanged).")
    # Export-mesh and DXF approximation controls. Tolerance 0 uses the document's
    # absolute tolerance; Angle is in degrees.
    options = (
        opts.Option("Tolerance", opts.VALUE, default=0.0, value_kind="number",
                    help="export mesh tolerance (0 = the document's)"),
        opts.Option("Angle", opts.VALUE, default=DEFAULT_MESH_ANGLE_DEG,
                    value_kind="number", help="export mesh angle, degrees"),
    )
    records_undo = False
    variable_points = False
    steps = [("Select objects to export:", OBJECTS_MULTI),
             ("Press Enter to choose the file, or type a file name:", TEXT)]
    preselect_names = 1
    cancel_on_empty_set = True

    def _enter_default(self, ctx):
        return "" if self.step_index == 1 else None

    def execute(self, ctx):
        names = self.args[0] if self.args else []
        target = self.args[1] if len(self.args) > 1 else ""
        model = ctx.model
        wanted = {str(n).lower() for n in (names or [])}
        objs = [o for o in model.file.Objects
                if (o.Attributes.Name or "").lower() in wanted]
        if not target:
            app = _app()
            start = os.path.join(filedialog.start_dir(app.doc_path),
                                 os.path.splitext(app.doc_title())[0] + ".3dm")
            status, target = filedialog.ask_save_path("Export", start, EXPORT_FILTERS)
            if status == filedialog.CANCEL:
                return "Export cancelled", True
            if status == filedialog.UNAVAILABLE:
                self.args = self.args[:1]
                self.step_index = 1
                return ("No file dialog is available (kdialog / zenity) -- type the "
                        "file name (" + " ".join(FORMATS) + ")."), False
        ok, msg = export_objects(model, objs, target,
                                 tolerance=self.opt.get("Tolerance") or None,
                                 angle_deg=self.opt.get("Angle") or None)
        return msg, True


PROVIDED = (ExportCmd,)
for _cls in PROVIDED:
    _inst = _cls()
    commands.COMMANDS[_inst.name.lower()] = _inst
