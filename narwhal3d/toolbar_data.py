"""Original temporary toolbar content for Narwhal 3D.

This intentionally small model contains only commands implemented by this project.
The labels, grouping, descriptions, pairings, and icon names were written for
Narwhal 3D and may be replaced as the product's visual design matures.
"""
from __future__ import annotations

CONTAINER = "Narwhal 3D"

# Active command icons replaced by the labelled in-progress artwork. Commands not in
# this set still use deterministic generated placeholders.
CURATED_ICON_COMMANDS = frozenset({
    "arc", "array", "arraypolar", "booleandifference", "booleanintersection",
    "booleanunion", "box", "chamfer", "circle", "closecrv", "cone", "copy",
    "cplane", "curve", "cylinder", "ellipse", "explode", "extrudecrv", "fillet",
    "grid", "gumball", "hide", "interpcrv", "join", "layer", "line", "lock",
    "loft", "mirror", "move", "offsetsrf", "open", "orient", "ortho", "pipe",
    "planarsrf", "plane", "point", "pointsoff", "pointson", "polygon", "polyline",
    "purge", "rectangle", "redo", "revolve", "rotate", "rotate3d", "save", "scale",
    "scale1d", "scale2d", "selall", "selnone", "shell", "show", "sphere", "split",
    "trim", "undo", "unlock", "zoom",
})


def _button(text, command, description):
    """Build one project-owned toolbar record."""
    return {
        "text": text,
        "tooltip": f"{text}: {description}",
        "command": command,
        "right_command": None,
        "right_tooltip": "",
        "icon": f"narwhal-{command}.png",
        "link": None,
    }


TABS = (
    {
        "name": "File",
        "buttons": (
            _button("Open", "open", "open a 3D model"),
            _button("Save", "save", "save the current 3D model"),
            _button("Layer", "layer", "create a document layer"),
            _button("Purge", "purge", "remove unused document data"),
        ),
    },
    {
        "name": "Draw",
        "buttons": (
            _button("Point", "point", "place one point object"),
            _button("Line", "line", "draw one line segment"),
            _button("Polyline", "polyline", "draw connected straight segments"),
            _button("Curve", "curve", "draw a smooth control-point curve"),
            _button("Interpolated Curve", "interpcrv", "draw a smooth curve through points"),
            _button("Circle", "circle", "draw a circle from its center and radius"),
            _button("Arc", "arc", "draw an arc through picked points"),
            _button("Rectangle", "rectangle", "draw a rectangular closed curve"),
            _button("Ellipse", "ellipse", "draw an ellipse from its axes"),
            _button("Polygon", "polygon", "draw a regular polygon"),
            _button("Plane", "plane", "draw a rectangular planar surface"),
        ),
    },
    {
        "name": "Edit",
        "buttons": (
            _button("Undo", "undo", "reverse the last document change"),
            _button("Redo", "redo", "restore the last undone document change"),
            _button("Delete", "delete", "remove selected objects"),
            _button("Join", "join", "join compatible selected objects"),
            _button("Explode", "explode", "separate a joined object"),
            _button("Trim", "trim", "trim an object with cutting geometry"),
            _button("Split", "split", "split an object with cutting geometry"),
            _button("Fillet", "fillet", "round the meeting point of two curves"),
            _button("Chamfer", "chamfer", "bevel the meeting point of two curves"),
            _button("Close Curve", "closecrv", "close an open curve"),
            _button("Control Points On", "pointson", "show editable curve control points"),
            _button("Control Points Off", "pointsoff", "hide editable curve control points"),
        ),
    },
    {
        "name": "Transform",
        "buttons": (
            _button("Move", "move", "move selected objects between two points"),
            _button("Copy", "copy", "copy selected objects between two points"),
            _button("Rotate", "rotate", "rotate selected objects in the construction plane"),
            _button("Rotate 3D", "rotate3d", "rotate selected objects around a 3D axis"),
            _button("Scale", "scale", "scale selected objects uniformly"),
            _button("Scale 1D", "scale1d", "scale selected objects along one axis"),
            _button("Scale 2D", "scale2d", "scale selected objects in a plane"),
            _button("Mirror", "mirror", "mirror selected objects across a line"),
            _button("Linear Array", "array", "make evenly spaced copies"),
            _button("Polar Array", "arraypolar", "make copies around a center"),
            _button("Orient", "orient", "reposition and align selected objects"),
        ),
    },
    {
        "name": "Solid",
        "buttons": (
            _button("Box", "box", "create an analytic box solid"),
            _button("Sphere", "sphere", "create an analytic sphere solid"),
            _button("Cylinder", "cylinder", "create an analytic cylinder solid"),
            _button("Cone", "cone", "create an analytic cone solid"),
            _button("Extrude Curve", "extrudecrv", "extrude a curve into a surface or solid"),
            _button("Revolve", "revolve", "revolve a profile around an axis"),
            _button("Loft", "loft", "build a surface through profile curves"),
            _button("Planar Surface", "planarsrf", "build a surface from planar boundaries"),
            _button("Boolean Union", "booleanunion", "combine intersecting solids"),
            _button("Boolean Difference", "booleandifference", "subtract one solid from another"),
            _button("Boolean Intersection", "booleanintersection", "keep the shared solid volume"),
            _button("Shell", "shell", "hollow a solid by removing selected faces"),
            _button("Offset Surface", "offsetsrf", "offset a surface or solid face"),
            _button("Pipe", "pipe", "build an exact constant-radius pipe along a curve"),
        ),
    },
    {
        "name": "View",
        "buttons": (
            _button("Zoom", "zoom", "change the active viewport magnification"),
            _button("Construction Plane", "cplane", "set the active construction plane"),
            _button("Named Construction Plane", "namedcplane", "manage saved construction planes"),
            _button("Grid", "grid", "show or hide the construction grid"),
            _button("Gumball", "gumball", "show or hide the transform manipulator"),
            _button("Ortho", "ortho", "toggle orthogonal cursor constraint"),
            _button("Hide", "hide", "hide selected objects"),
            _button("Show", "show", "show hidden objects"),
            _button("Lock", "lock", "lock selected objects"),
            _button("Unlock", "unlock", "unlock locked objects"),
            _button("Select All", "selall", "select every selectable object"),
            _button("Select None", "selnone", "clear the current selection"),
        ),
    },
)

_BY_COMMAND = {
    button["command"]: button
    for tab in TABS
    for button in tab["buttons"]
}


def _shortcut(command, right_command=None, link=None):
    """Copy a tab action into the compact favorites sidebar."""
    left = _BY_COMMAND[command]
    right = _BY_COMMAND.get(right_command)
    item = dict(left)
    item["right_command"] = right_command
    item["right_tooltip"] = right["tooltip"] if right else ""
    item["link"] = link
    return item


SIDEBAR = {
    "name": "Narwhal Tools",
    "buttons": (
        _shortcut("open", "save", "File"),
        _shortcut("line", "polyline", "Draw"),
        _shortcut("undo", "redo", "Edit"),
        _shortcut("move", "copy", "Transform"),
        _shortcut("box", "sphere", "Solid"),
        _shortcut("zoom", "cplane", "View"),
        _shortcut("circle", "arc"),
        _shortcut("curve", "interpcrv"),
        _shortcut("rectangle", "ellipse"),
        _shortcut("delete", "join"),
        _shortcut("trim", "split"),
        _shortcut("rotate", "mirror"),
        _shortcut("scale", "scale1d"),
        _shortcut("cylinder", "cone"),
        _shortcut("extrudecrv", "revolve"),
        _shortcut("booleanunion", "booleandifference"),
        _shortcut("hide", "show"),
        _shortcut("lock", "unlock"),
        _shortcut("gumball", "ortho"),
        _shortcut("selall", "selnone"),
    ),
}

# A linked sidebar cell opens the corresponding original category.
FLYOUTS = {tab["name"]: tab["buttons"] for tab in TABS}

TOOLBAR_ICON_NAMES = frozenset(
    button["icon"]
    for tab in TABS
    for button in tab["buttons"]
)
PANEL_ICON_NAMES = frozenset({
    "narwhal-panel-layers.png",
    "narwhal-panel-properties.png",
    "narwhal-panel-display.png",
    "narwhal-panel-report.png",
})
ICON_NAMES = TOOLBAR_ICON_NAMES | PANEL_ICON_NAMES
