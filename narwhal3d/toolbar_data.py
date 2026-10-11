"""Original Narwhal 3D toolbar and favorites-palette content.

The toolbar is organized by CAD workflow rather than by implementation module. Runnable
commands and compact future-facing actions share the same data model; unavailable actions
carry explicit command keys and labels ending in ``(Not implemented)`` so hover and click
feedback remain honest.
"""
from __future__ import annotations

CONTAINER = "Narwhal 3D"
TODO_SUFFIX = " (Not implemented)"


def _button(text, command, description, *, right_command=None, right_tooltip="", link=None):
    """Build one toolbar record with a stable project-owned icon name."""
    return {
        "text": text,
        "tooltip": f"{text}: {description}",
        "command": command,
        "right_command": right_command,
        "right_tooltip": right_tooltip,
        "icon": f"narwhal-{command}.png",
        "link": link,
    }


def _todo(text, command, description):
    return _button(text + TODO_SUFFIX, command, description)


TABS = (
    {
        "name": "Standard",
        "buttons": (
            _button("Open", "open", "open a 3D model"),
            _button("Save", "save", "save the current model"),
            _button("Save As", "saveas", "save to a new path"),
            _button("Import", "import", "import supported geometry"),
            _button("Export", "export", "export selected geometry"),
            _button("Print", "print", "write vector output"),
            _button("Undo", "undo", "reverse the last document change"),
            _button("Redo", "redo", "restore the last undone change"),
            _button("Delete", "delete", "remove selected objects"),
            _button("Join", "join", "join compatible selected objects"),
            _button("Explode", "explode", "separate joined geometry"),
            _button("Layer", "layer", "create or manage document layers"),
            _button("Purge", "purge", "remove unused document data"),
            _button("Units", "units", "set model units and tolerance"),
            _button("Help", "help", "show the command guide"),
        ),
    },
    {
        "name": "CPlanes",
        "buttons": (
            _button("Construction Plane", "cplane", "set the active construction plane"),
            _button("Named CPlanes", "namedcplane", "manage saved construction planes"),
            _button("Grid", "grid", "configure the construction grid"),
        ),
    },
    {
        "name": "Set View",
        "buttons": (
            _button("Zoom", "zoom", "frame or magnify the active viewport"),
            _todo("Top View", "setviewtop", "switch the active viewport to Top"),
            _todo("Front View", "setviewfront", "switch the active viewport to Front"),
            _todo("Right View", "setviewright", "switch the active viewport to Right"),
            _todo("Perspective View", "setviewperspective", "switch to Perspective"),
            _todo("Viewport Layout", "viewportlayout", "change the viewport arrangement"),
        ),
    },
    {
        "name": "Display",
        "buttons": (
            _button("Display Mode", "setdisplaymode", "choose Wireframe, Shaded or Rendered"),
            _button("Gumball", "gumball", "show or hide the transform manipulator"),
            _button("Gumball Alignment", "gumballalignment", "align the transform manipulator"),
            _button("Ortho", "ortho", "toggle orthogonal cursor constraint"),
            _button("Object Snaps", "osnap", "configure persistent object snaps"),
            _button("Suspend Snaps", "disableosnap", "temporarily suspend object snaps"),
            _button("Project Snaps", "projectosnap", "project snaps to the construction plane"),
            _button("Snap to Locked", "snaptolocked", "include locked objects in snapping"),
        ),
    },
    {
        "name": "Select",
        "buttons": (
            _button("Select All", "selall", "select every selectable object"),
            _button("Select None", "selnone", "clear the selection"),
            _button("Invert", "invert", "invert the current selection"),
            _button("Select Last", "sellast", "select the last created objects"),
            _button("Select Previous", "selprev", "restore the previous selection"),
            _button("By Name", "selname", "select objects by name"),
            _button("By Layer", "sellayer", "select objects on a layer"),
            _button("By Group", "selgroup", "select a named group"),
            _button("Duplicates", "seldup", "select duplicate geometry"),
            _button("Points", "selpt", "select point objects"),
            _button("Curves", "selcrv", "select curve objects"),
            _button("Closed Curves", "selclosedcrv", "select closed curves"),
            _button("Open Curves", "selopencrv", "select open curves"),
            _button("Surfaces", "selsrf", "select surfaces"),
            _button("Polysurfaces", "selpolysrf", "select polysurfaces"),
            _button("By User-Text Key", "selkey", "select by metadata key"),
            _button("By User-Text Value", "selvalue", "select by metadata value"),
            _button("By Key and Value", "selkeyvalue", "select by metadata row"),
        ),
    },
    {
        "name": "Visibility",
        "buttons": (
            _button("Hide", "hide", "hide selected objects"),
            _button("Show", "show", "show hidden objects"),
            _button("Show Selected", "showselected", "show selected hidden objects"),
            _button("Isolate", "isolate", "hide everything except the selection"),
            _button("End Isolation", "unisolate", "restore objects hidden by Isolate"),
            _button("Lock", "lock", "lock selected objects"),
            _button("Unlock", "unlock", "unlock all locked objects"),
            _button("Unlock Selected", "unlockselected", "unlock selected objects"),
        ),
    },
    {
        "name": "Transform",
        "buttons": (
            _button("Move", "move", "move selected objects between two points"),
            _button("Copy", "copy", "copy selected objects between two points"),
            _button("Rotate", "rotate", "rotate in the construction plane"),
            _button("Rotate 3D", "rotate3d", "rotate around a 3D axis"),
            _button("Scale", "scale", "scale uniformly"),
            _button("Scale 1D", "scale1d", "scale along one axis"),
            _button("Scale 2D", "scale2d", "scale in a plane"),
            _button("Mirror", "mirror", "mirror across a line"),
            _button("Linear Array", "array", "create a linear array"),
            _button("Polar Array", "arraypolar", "create a polar array"),
            _button("Orient", "orient", "orient with two points"),
            _button("Orient 3 Point", "orient3pt", "orient with three points"),
            _button("Project to CPlane", "projecttocplane", "flatten to the construction plane"),
        ),
    },
    {
        "name": "Curve Tools",
        "buttons": (
            _button("Point", "point", "place one point"),
            _button("Points", "points", "place multiple points"),
            _button("Line", "line", "draw one line segment"),
            _button("Lines", "lines", "draw separate line segments"),
            _button("Polyline", "polyline", "draw connected segments"),
            _button("Curve", "curve", "draw a control-point curve"),
            _button("Interpolated Curve", "interpcrv", "draw through points"),
            _button("Arc", "arc", "draw an arc"),
            _button("Circle", "circle", "draw a circle"),
            _button("Ellipse", "ellipse", "draw an ellipse"),
            _button("Rectangle", "rectangle", "draw a rectangle"),
            _button("Polygon", "polygon", "draw a regular polygon"),
            _button("Blend Curves", "blendcrv", "blend between curves"),
            _button("Close Curve", "closecrv", "close an open curve"),
            _button("Offset Curve", "offset", "offset a curve"),
            _button("Extend Curve", "extend", "extend a curve end"),
            _button("Divide Curve", "divide", "divide a curve"),
            _button("Fillet Curves", "fillet", "round a curve corner"),
            _button("Chamfer Curves", "chamfer", "bevel a curve corner"),
            _button("Split", "split", "split geometry"),
            _button("Trim", "trim", "trim geometry"),
            _button("Rebuild", "rebuild", "rebuild a curve"),
            _button("Change Degree", "changedegree", "change curve degree"),
            _button("Insert Knot", "insertknot", "insert a NURBS knot"),
            _button("Project", "project", "project curve or point geometry"),
            _button("Duplicate Edge", "dupedge", "duplicate a surface edge"),
            _button("Duplicate Border", "dupborder", "duplicate a surface border"),
            _button("Extract Wireframe", "extractwireframe", "extract boundary curves"),
            _button("Curve Start", "crvstart", "extract curve start points"),
            _button("Curve End", "crvend", "extract curve end points"),
            _button("Extract Points", "extractpt", "extract control points"),
            _button("Control Polygon", "extractcontrolpolygon", "extract a control polygon"),
            _button("Intersect", "intersect", "intersect curves"),
            _button("Points On", "pointson", "show control points"),
            _button("Points Off", "pointsoff", "hide control points"),
        ),
    },
    {
        "name": "Surface Tools",
        "buttons": (
            _button("Plane Surface", "plane", "create a plane surface"),
            _button("Planar Surface", "planarsrf", "create from planar boundaries"),
            _button("Edge Surface", "edgesrf", "create from edge curves"),
            _button("Loft", "loft", "loft through profile curves"),
            _button("Sweep 1 Rail", "sweep1", "sweep along one rail"),
            _button("Sweep 2 Rail", "sweep2", "sweep along two rails"),
            _button("Revolve", "revolve", "revolve a profile"),
            _button("Extrude Curve", "extrudecrv", "extrude a curve"),
            _button("Tapered Extrusion", "extrudecrvtapered", "extrude with taper"),
            _button("Extrude Surface", "extrudesrf", "extrude a surface"),
            _button("Offset Surface", "offsetsrf", "offset a surface"),
            _button("Cap", "cap", "cap planar openings"),
        ),
    },
    {
        "name": "Solid Tools",
        "buttons": (
            _button("Box", "box", "create an analytic box"),
            _button("Sphere", "sphere", "create an analytic sphere"),
            _button("Cylinder", "cylinder", "create an analytic cylinder"),
            _button("Cone", "cone", "create an analytic cone"),
            _button("Torus", "torus", "create an analytic torus"),
            _button("Ellipsoid", "ellipsoid", "create an ellipsoid"),
            _button("Truncated Cone", "truncatedcone", "create a truncated cone"),
            _button("Tube", "tube", "create a tube"),
            _button("Pyramid", "pyramid", "create a pyramid"),
            _button("Pipe", "pipe", "pipe a curve"),
            _button("Shell", "shell", "hollow a solid"),
            _button("Move Face", "moveface", "move a solid face"),
            _button("Move Edge", "moveedge", "move a solid edge"),
            _button("Fillet Edge", "filletedge", "round solid edges"),
            _button("Chamfer Edge", "chamferedge", "bevel solid edges"),
            _button("Boolean Union", "booleanunion", "combine solids"),
            _button("Boolean Difference", "booleandifference", "subtract solids"),
            _button("Boolean Intersection", "booleanintersection", "keep shared volume"),
        ),
    },
    {
        "name": "SubD Tools",
        "buttons": (
            _todo("Create SubD", "subd", "create SubD geometry"),
            _todo("SubD Box", "subdbox", "create a SubD box"),
            _todo("SubD Sphere", "subdsphere", "create a SubD sphere"),
            _todo("SubD Loft", "subdloft", "loft SubD profiles"),
            _todo("Convert to SubD", "subdconvert", "convert compatible geometry to SubD"),
        ),
    },
    {
        "name": "Mesh Tools",
        "buttons": (
            _todo("Create Mesh", "mesh", "create or convert mesh geometry"),
            _todo("Mesh Box", "meshbox", "create a mesh box"),
            _todo("Mesh Sphere", "meshsphere", "create a mesh sphere"),
            _todo("Mesh Cylinder", "meshcylinder", "create a mesh cylinder"),
            _todo("Mesh Plane", "meshplane", "create a mesh plane"),
            _todo("Mesh Torus", "meshtorus", "create a mesh torus"),
            _todo("Repair Mesh", "repairmesh", "repair mesh topology"),
            _todo("Reduce Mesh", "reducemesh", "reduce polygon count"),
            _todo("Quad Remesh", "quadremesh", "build a quad-dominant mesh"),
            _todo("Mesh Union", "meshbooleanunion", "union mesh objects"),
            _todo("Mesh Difference", "meshbooleandifference", "subtract mesh objects"),
        ),
    },
    {
        "name": "Drafting",
        "buttons": (
            _button("Picture", "picture", "place a reference image"),
            _todo("Text", "text", "create annotation text"),
            _todo("Dimension", "dimension", "create dimensions"),
            _todo("Leader", "leader", "create a leader"),
            _todo("Hatch", "hatch", "create a hatch"),
            _todo("Layout", "layout", "create a page layout"),
        ),
    },
    {
        "name": "Analyze",
        "buttons": (
            _button("Distance", "distance", "measure distance"),
            _button("Angle", "angle", "measure angle"),
            _button("Length", "length", "measure curve length"),
            _button("Radius", "radius", "measure radius"),
            _button("Diameter", "diameter", "measure diameter"),
            _button("Area", "area", "measure area"),
            _button("Volume", "volume", "measure volume"),
            _button("Area Centroid", "areacentroid", "find an area centroid"),
            _button("Volume Centroid", "volumecentroid", "find a volume centroid"),
            _button("Evaluate Point", "evaluatept", "report point coordinates"),
            _button("Bounding Box", "boundingbox", "create and report a bounding box"),
            _button("Object Details", "what", "report object details"),
            _button("Direction", "dir", "inspect direction and normals"),
        ),
    },
    {
        "name": "Render",
        "buttons": (
            _todo("Materials", "material", "manage render materials"),
            _todo("Textures", "texture", "manage image textures"),
            _todo("Lights", "light", "create and manage lights"),
            _todo("Environment", "environment", "manage render environments"),
            _todo("Render", "render", "produce a rendered image"),
        ),
    },
)

_BY_COMMAND = {
    button["command"]: button
    for tab in TABS
    for button in tab["buttons"]
}


def _shortcut(command, right_command=None, link=None):
    """Copy a tab action into the dense favorites sidebar."""
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
        _shortcut("open", "save", "Standard"),
        _shortcut("cplane", "namedcplane", "CPlanes"),
        _shortcut("zoom", None, "Set View"),
        _shortcut("setdisplaymode", None, "Display"),
        _shortcut("selall", "selnone", "Select"),
        _shortcut("hide", "show", "Visibility"),
        _shortcut("move", "copy", "Transform"),
        _shortcut("line", "polyline", "Curve Tools"),
        _shortcut("plane", "planarsrf", "Surface Tools"),
        _shortcut("box", "sphere", "Solid Tools"),
        _shortcut("subd", None, "SubD Tools"),
        _shortcut("mesh", None, "Mesh Tools"),
        _shortcut("picture", None, "Drafting"),
        _shortcut("distance", "angle", "Analyze"),
        _shortcut("render", None, "Render"),
        _shortcut("save"), _shortcut("undo"), _shortcut("redo"), _shortcut("delete"),
        _shortcut("circle"), _shortcut("arc"), _shortcut("polyline"), _shortcut("curve"),
        _shortcut("interpcrv"), _shortcut("rectangle"), _shortcut("ellipse"),
        _shortcut("polygon"), _shortcut("trim"), _shortcut("split"), _shortcut("fillet"),
        _shortcut("chamfer"), _shortcut("offset"), _shortcut("extend"), _shortcut("rebuild"),
        _shortcut("extrudecrv"), _shortcut("loft"), _shortcut("sweep1"),
        _shortcut("revolve"), _shortcut("cylinder"), _shortcut("sphere"),
        _shortcut("cone"), _shortcut("pipe"), _shortcut("shell"),
        _shortcut("booleanunion"), _shortcut("booleandifference"),
        _shortcut("booleanintersection"), _shortcut("copy"), _shortcut("rotate"),
        _shortcut("scale"), _shortcut("filletedge"), _shortcut("chamferedge"),
        _shortcut("boundingbox"),
    ),
}

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
