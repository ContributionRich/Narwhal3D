"""Surface-creation commands that live outside ``commands.py``.

Same pattern as ``solid_commands``: every command here registers itself in
``commands.COMMANDS`` when this module is imported. All geometry comes from exact
``bridge`` producers; nothing here tessellates.
"""
from . import bridge, commands
from . import options as opts
from .commands import OBJECTS_MULTI, POINT, Command


class SrfPtCmd(Command):
    """`SrfPt` -- the command behavior: "Pick the first corner ... Pick the fourth
    corner. Or press Enter to create a triangular surface." Exact: four corners give
    the bilinear NURBS patch through them, three give the planar triangle
    (`bridge.make_srf_pt`)."""
    name = "SrfPt"
    description = "Create a surface from 3 or 4 corner points."
    steps = [("First corner of surface:", POINT), ("Second corner of surface:", POINT),
             ("Third corner of surface:", POINT),
             ("Fourth corner of surface. Press Enter for a triangular surface:", POINT)]

    def _feed(self, text, ctx):
        if self.step_index == 3 and not (text or "").strip():
            return self.execute(ctx)
        return super()._feed(text, ctx)

    def execute(self, ctx):
        try:
            geom = bridge.make_srf_pt(self.args[:4])
        except ValueError as e:
            return f"SrfPt: {e}. Nothing was created.", True
        _, display = ctx.add_geometry(geom, "Surface")
        corners = "triangular" if len(self.args) == 3 else "four-corner"
        return f"Created {display} ({corners} surface)", True


def _count_option(name, default, what):
    return opts.Option(name, opts.VALUE, default=default, value_kind="number",
                       help=f"The {what} of the cage in the {name[-1]} direction.")


class CageEditCmd(Command):
    """`CageEdit` -- the command behavior: "Select the captive objects (objects to
    edit). Select or create a control object ... Edit the cage points of the control
    objects." The BoundingBox control object is built: a box around the captives with
    PointCount and Degree per direction (a flat direction gets one point). The cage's
    points are then edited with the normal control-point tools, and every captive
    follows. The deformation is Rust (`pure_core::cage`); captives keep their exact
    NURBS degree, knots and weights because only their control points move.

    OURS: the defaults (4 points, degree 3) are not documented; Line, Rectangle and
    Box control objects and trimmed/solid captives are reported as not implemented."""
    name = "CageEdit"
    description = "Deform objects smoothly by editing the points of a control cage."
    steps = [("Select captive objects:", OBJECTS_MULTI)]
    preselect_names = 1
    cancel_on_empty_set = True
    options = (
        opts.Option("BoundingBox", opts.MODE,
                    help="Use the captives' bounding box as the cage (the default)."),
        opts.Option("Line", opts.MODE, help="Draw a line control object (not implemented yet)."),
        opts.Option("Rectangle", opts.MODE,
                    help="Draw a rectangle control object (not implemented yet)."),
        opts.Option("Box", opts.MODE, help="Draw a box control object (not implemented yet)."),
        _count_option("PointCountX", 4, "number of cage points"),
        _count_option("PointCountY", 4, "number of cage points"),
        _count_option("PointCountZ", 4, "number of cage points"),
        _count_option("DegreeX", 3, "degree"),
        _count_option("DegreeY", 3, "degree"),
        _count_option("DegreeZ", 3, "degree"),
    )

    def on_mode(self, name, ctx):
        if name == "BoundingBox":
            return "CageEdit: the cage is the captives' bounding box", False
        return super().on_mode(name, ctx)

    def active_modes(self):
        return {"BoundingBox"}

    def execute(self, ctx):
        from . import cage

        names = list(self.args[0]) if self.args else []
        captives = []
        for name in names:
            oid = ctx.model.object_id_by_name(name)
            geom = ctx.geom_by_name(name)
            if oid is None or geom is None:
                return f"CageEdit: {name!r} is not in the document", True
            if cage.binding_of(geom) is not None:
                return f"CageEdit: {name!r} is itself a cage", True
            captives.append((oid, geom))
        if not captives:
            return "CageEdit: select the objects to edit", True
        counts = [self.opt.get(f"PointCount{a}", 4) for a in "XYZ"]
        degrees = [self.opt.get(f"Degree{a}", 3) for a in "XYZ"]
        if any(float(v) != int(float(v)) for v in counts + degrees):
            return "CageEdit: PointCount and Degree must be whole numbers", True
        try:
            control = cage.make_cage(captives, [int(float(v)) for v in counts],
                                     [int(float(v)) for v in degrees])
        except cage.CageError as e:
            return f"CageEdit: {e}. Nothing was created.", True
        _, display = ctx.add_geometry(control, "Cage")
        binding = cage.binding_of(control)
        shape = " x ".join(str(c) for c in binding["counts"])
        try:
            from . import ui
            ui.set_points_on([display], True)
        except Exception:  # noqa: BLE001 -- headless: the cage still exists
            pass
        return (f"Created {display} ({shape} cage points) around {len(captives)} "
                f"object(s); edit its points to deform them"), True


PROVIDED = (SrfPtCmd, CageEditCmd)
for _cls in PROVIDED:
    _inst = _cls()
    commands.COMMANDS[_inst.name.lower()] = _inst
