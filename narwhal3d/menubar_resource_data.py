"""Original, compact Narwhal 3D menu definitions.

``MENUS`` keeps the row shape consumed by the application renderer:

* ``("item", label, shortcut, command)``
* ``("sep", "", "", None)``
* ``("popup", label, shortcut, children, command)``

Shortcuts are intentionally empty.  Labels, grouping, and order are maintained here as
Narwhal 3D product design rather than imported compatibility data.  Every item names a
command implemented by the application; unavailable placeholders do not belong in this
menu.
"""


def _item(label, command):
    return ("item", label, "", command)


def _sep():
    return ("sep", "", "", None)


def _popup(label, rows):
    return ("popup", label, "", tuple(rows), None)


MENUS = (
    ("File", (
        _item("Open Model", "open"),
        _item("Save Model", "save"),
    )),
    ("Edit", (
        _item("Undo", "undo"),
        _item("Redo", "redo"),
        _sep(),
        _item("Delete", "delete"),
        _item("Join", "join"),
        _item("Explode", "explode"),
    )),
    ("View", (
        _item("Zoom", "zoom"),
        _item("Construction Plane", "cplane"),
        _item("Grid Settings", "grid"),
        _item("Transform Widget", "gumball"),
        _item("Orthographic Mode", "ortho"),
    )),
    ("Draw", (
        _popup("Points", (
            _item("Point", "point"),
            _item("Multiple Points", "points"),
        )),
        _popup("Lines", (
            _item("Line Segment", "line"),
            _item("Connected Segments", "polyline"),
            _item("Separate Segments", "lines"),
        )),
        _popup("Shapes", (
            _item("Circle", "circle"),
            _item("Rectangle", "rectangle"),
            _item("Ellipse", "ellipse"),
            _item("Polygon", "polygon"),
            _item("Arc", "arc"),
        )),
        _item("Place Image", "picture"),
    )),
    ("Curves", (
        _popup("Create", (
            _item("Control-Point Curve", "curve"),
            _item("Interpolated Curve", "interpcrv"),
            _item("Blend Curves", "blendcrv"),
        )),
        _popup("Edit", (
            _item("Close Curve", "closecrv"),
            _item("Offset Curve", "offset"),
            _item("Extend Curve", "extend"),
            _item("Divide Curve", "divide"),
            _item("Split Curve", "split"),
            _item("Trim Curve", "trim"),
            _item("Round Corner", "fillet"),
            _item("Bevel Corner", "chamfer"),
            _item("Rebuild Curve", "rebuild"),
            _item("Change Degree", "changedegree"),
            _item("Insert Knot", "insertknot"),
        )),
        _popup("Derive", (
            _item("Duplicate Edge", "dupedge"),
            _item("Duplicate Border", "dupborder"),
            _item("Project", "project"),
            _item("Project to Construction Plane", "projecttocplane"),
            _item("Extract Wireframe", "extractwireframe"),
        )),
    )),
    ("Surfaces", (
        _item("Plane Surface", "plane"),
        _item("Surface from Planar Curves", "planarsrf"),
        _item("Surface from Edges", "edgesrf"),
        _item("Loft", "loft"),
        _item("Revolve", "revolve"),
        _item("Extrude Surface", "extrudesrf"),
        _item("Offset Surface", "offsetsrf"),
    )),
    ("Solids", (
        _popup("Primitives", (
            _item("Box", "box"),
            _item("Sphere", "sphere"),
            _item("Cylinder", "cylinder"),
            _item("Cone", "cone"),
        )),
        _popup("Build", (
            _item("Extrude Curve", "extrudecrv"),
            _item("Pipe", "pipe"),
            _item("Shell", "shell"),
        )),
        _popup("Combine", (
            _item("Union", "booleanunion"),
            _item("Difference", "booleandifference"),
            _item("Intersection", "booleanintersection"),
        )),
    )),
    ("Transform", (
        _item("Move", "move"),
        _item("Copy", "copy"),
        _item("Rotate", "rotate"),
        _item("Rotate in 3D", "rotate3d"),
        _popup("Scale", (
            _item("Uniform Scale", "scale"),
            _item("Scale Along One Axis", "scale1d"),
            _item("Scale Along Two Axes", "scale2d"),
        )),
        _item("Mirror", "mirror"),
        _popup("Array", (
            _item("Linear Array", "array"),
            _item("Polar Array", "arraypolar"),
        )),
        _popup("Orient", (
            _item("Orient with Two Points", "orient"),
            _item("Orient with Three Points", "orient3pt"),
        )),
    )),
    ("Analyze", (
        _popup("Measure", (
            _item("Distance", "distance"),
            _item("Angle", "angle"),
            _item("Length", "length"),
            _item("Radius", "radius"),
            _item("Diameter", "diameter"),
            _item("Area", "area"),
            _item("Volume", "volume"),
        )),
        _popup("Centers", (
            _item("Area Center", "areacentroid"),
            _item("Volume Center", "volumecentroid"),
        )),
        _item("Bounding Box", "boundingbox"),
        _item("Evaluate Point", "evaluatept"),
        _item("Direction", "dir"),
        _item("Object Details", "what"),
    )),
    ("Help", (
        _item("Command Guide", "help"),
    )),
)


def _counts():
    counts = {"items": 0, "separators": 0, "popups": 0}

    def walk(rows):
        for row in rows:
            counts[{"item": "items", "sep": "separators", "popup": "popups"}[row[0]]] += 1
            if row[0] == "popup":
                walk(row[3])

    for _label, rows in MENUS:
        walk(rows)
    return counts


REPORT = {**_counts(), "menus": len(MENUS), "source": "original"}
