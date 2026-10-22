"""Original, complete Narwhal CAD menu definitions.

``MENUS`` keeps the row shape consumed by the application renderer:

* ``("item", label, shortcut, command)``
* ``("sep", "", "", None)``
* ``("popup", label, shortcut, children, command)``

Every registered command appears exactly once. Future-facing command rows are explicit,
clickable TODO actions: their labels end in ``(Not implemented)`` and their payloads are
canonical command keys which are intentionally absent from the live registry. The menu
renderer sends those leaves to ``narwhal.menu_todo``, which explains the missing feature.
Labels, grouping, and order are original project-authored information architecture.
"""

from . import toolbar_data

TODO_SUFFIX = " (Not implemented)"
SPECIAL_COMMANDS = frozenset({"__recent_files__"})


def _item(label, command):
    return ("item", label, "", command)


def _todo(label, command):
    return _item(label + TODO_SUFFIX, command)


def _sep():
    return ("sep", "", "", None)


def _popup(label, rows):
    return ("popup", label, "", tuple(rows), None)


_CORE_MENUS = (
    ("File", (
        _popup("Document", (
            _item("New Model", "new"),
            _item("Open Model", "open"),
            _item("Import into Model", "import"),
        )),
        _popup("Save and Exchange", (
            _item("Save", "save"),
            _item("Save As", "saveas"),
            _item("Export Selected", "export"),
        )),
        _item("Print / Vector Output", "print"),
        _sep(),
        _item("Exit", "exit"),
    )),
    ("Edit", (
        _popup("History", (
            _item("Undo", "undo"),
            _item("Redo", "redo"),
        )),
        _item("Cut", "cut"),
        _item("Copy", "copytoclipboard"),
        _item("Paste", "paste"),
        _sep(),
        _item("Delete", "delete"),
        _popup("Select", (
            _popup("General", (
                _item("Selection Command", "select"),
                _item("Select All", "selall"),
                _item("Select None", "selnone"),
                _item("Invert Selection", "invert"),
                _item("Select Last Created", "sellast"),
                _item("Restore Previous Selection", "selprev"),
            )),
            _popup("By Organization or Identity", (
                _item("By Object Name", "selname"),
                _item("By Layer", "sellayer"),
                _item("By Group", "selgroup"),
                _item("Duplicate Objects", "seldup"),
            )),
            _popup("By Geometry Type", (
                _item("Points", "selpt"),
                _item("Curves", "selcrv"),
                _item("Closed Curves", "selclosedcrv"),
                _item("Open Curves", "selopencrv"),
                _item("Surfaces", "selsrf"),
                _item("Polysurfaces", "selpolysrf"),
            )),
            _popup("By Attached Data", (
                _item("By User-Text Key", "selkey"),
                _item("By User-Text Value", "selvalue"),
                _item("By User-Text Key and Value", "selkeyvalue"),
            )),
        )),
        _popup("Visibility and Access", (
            _item("Hide Selected", "hide"),
            _item("Show All", "show"),
            _item("Show Selected Hidden Objects", "showselected"),
            _item("Isolate Selected", "isolate"),
            _item("End Isolation", "unisolate"),
            _sep(),
            _item("Lock Selected", "lock"),
            _item("Unlock All", "unlock"),
            _item("Unlock Selected", "unlockselected"),
        )),
        _popup("Groups", (
            _item("Group", "group"),
            _item("Ungroup", "ungroup"),
            _item("Ungroup Including Nested Groups", "ungroupall"),
            _item("Add to Group", "addtogroup"),
            _item("Remove from Group", "removefromgroup"),
            _item("Set Group Name", "setgroupname"),
        )),
        _popup("Object Data", (
            _item("Name Selected Objects", "setobjectname"),
            _item("Rename One Object", "rename"),
            _item("Change Object Layer", "changelayer"),
            _item("Set Object User Text", "setusertext"),
            _item("Read Object User Text", "getusertext"),
        )),
        _popup("Structure and Direction", (
            _item("Join", "join"),
            _item("Explode", "explode"),
            _item("Reverse Geometry Direction", "flip"),
        )),
    )),
    ("View", (
        _item("Zoom / Frame", "zoom"),
        _item("Display Mode", "setdisplaymode"),
        _popup("Construction Plane and Grid", (
            _item("Set Construction Plane", "cplane"),
            _item("Named Construction Planes", "namedcplane"),
            _item("Grid Settings", "grid"),
            _item("Grid Snap Spacing", "snapsize"),
        )),
        _popup("Modeling Aids", (
            _item("Object Snaps", "osnap"),
            _item("Suspend Object Snaps", "disableosnap"),
            _item("Project Snaps to Construction Plane", "projectosnap"),
            _item("Snap to Locked Objects", "snaptolocked"),
            _item("Toggle Grid Snap", "snap"),
            _item("Set Grid Snap", "setsnap"),
            _item("Toggle Orthogonal Constraint", "ortho"),
            _item("Set Orthogonal Constraint", "setortho"),
        )),
        _todo("Named Views and Viewport Layouts", "namedview"),
    )),
    ("Curve", (
        _popup("Points and Basic Curves", (
            _item("Point", "point"),
            _item("Multiple Points", "points"),
            _item("Line Segment", "line"),
            _item("Separate Line Segments", "lines"),
            _item("Polyline", "polyline"),
            _item("Control-Point Curve", "curve"),
            _item("Handle Curve", "handlecurve"),
            _item("Interpolated Curve", "interpcrv"),
            _item("Curve Through Polyline", "curvethroughpolyline"),
            _item("Curve Through Point Objects", "curvethroughpt"),
            _item("Curve Through Surface Controls", "curvethroughsrfcontrolpt"),
            _item("Arc", "arc"),
            _item("Circle", "circle"),
            _item("Ellipse", "ellipse"),
            _item("Rectangle", "rectangle"),
            _item("Polygon", "polygon"),
            _item("Conic", "conic"),
            _item("Parabola", "parabola"),
            _item("Parabola Through 3 Points", "parabola3pt"),
            _item("Hyperbola", "hyperbola"),
            _item("Catenary", "catenary"),
            _item("Helix", "helix"),
            _item("Spiral", "spiral"),
        )),
        _popup("Transitions", (
            _item("Blend Curves", "blendcrv"),
            _item("Tween Curves", "tweencurves"),
        )),
        _popup("Modify", (
            _item("Close Curve", "closecrv"),
            _item("Offset Curve", "offset"),
            _item("Extend Curve", "extend"),
            _item("Divide Curve", "divide"),
            _item("Fillet Curves", "fillet"),
            _item("Chamfer Curves", "chamfer"),
            _item("Split Curves", "split"),
            _item("Trim Curves", "trim"),
            _item("Rebuild Curve", "rebuild"),
            _item("Change Degree", "changedegree"),
            _item("Insert Knot", "insertknot"),
        )),
        _popup("Derive and Extract", (
            _item("Project Curve or Point", "project"),
            _item("Duplicate Surface Edge", "dupedge"),
            _item("Duplicate Surface Border", "dupborder"),
            _item("Extract Wireframe", "extractwireframe"),
            _item("Extract Curve Start Points", "crvstart"),
            _item("Extract Curve End Points", "crvend"),
            _item("Extract Control Points", "extractpt"),
            _item("Extract Control Polygon", "extractcontrolpolygon"),
            _item("Curve Intersections", "intersect"),
        )),
        _popup("Control-Point Display", (
            _item("Control Points On", "pointson"),
            _item("Control Points Off", "pointsoff"),
        )),
        _todo("Match, Fair, and Simplify Curves", "matchcrv"),
    )),
    ("Surface", (
        _popup("Create Directly or from Curves", (
            _item("Plane Surface", "plane"),
            _item("Planar Surface from Boundaries", "planarsrf"),
            _item("Surface from Edge Curves", "edgesrf"),
            _item("Loft", "loft"),
            _item("One-Rail Sweep", "sweep1"),
            _item("Two-Rail Sweep", "sweep2"),
            _item("Revolve", "revolve"),
            _item("Extrude Curve", "extrudecrv"),
            _item("Tapered Curve Extrusion", "extrudecrvtapered"),
        )),
        _popup("Modify and Close", (
            _item("Extrude Surface", "extrudesrf"),
            _item("Offset Surface", "offsetsrf"),
            _item("Cap Planar Openings", "cap"),
        )),
        _todo("Advanced Surface Construction and Matching", "networksrf"),
    )),
    ("SubD", (
        _todo("Create, Convert, and Edit SubD", "subd"),
    )),
    ("Solid", (
        _popup("Primitives", (
            _item("Box", "box"),
            _item("Sphere", "sphere"),
            _item("Cylinder", "cylinder"),
            _item("Cone", "cone"),
            _item("Torus", "torus"),
            _item("Ellipsoid", "ellipsoid"),
            _item("Truncated Cone", "truncatedcone"),
            _item("Tube", "tube"),
            _item("Pyramid", "pyramid"),
        )),
        _popup("Create and Edit", (
            _item("Pipe", "pipe"),
            _item("Shell", "shell"),
            _item("Move Face", "moveface"),
            _item("Move Edge", "moveedge"),
            _item("Fillet Edges", "filletedge"),
            _item("Chamfer Edges", "chamferedge"),
        )),
        _popup("Boolean", (
            _item("Union", "booleanunion"),
            _item("Difference", "booleandifference"),
            _item("Intersection", "booleanintersection"),
        )),
        _todo("Solid Repair and Validation", "solidrepair"),
    )),
    ("Mesh", (
        _todo("Create and Convert Meshes", "mesh"),
        _todo("Edit, Repair, Reduce, and Boolean Meshes", "repairmesh"),
    )),
    ("Drafting", (
        _popup("Reference", (
            _item("Place Picture", "picture"),
        )),
        _todo("Text, Dimensions, and Leaders", "annotation"),
        _todo("Hatches, Layouts, and Detail Views", "layout"),
    )),
    ("Transform", (
        _popup("Move and Duplicate", (
            _item("Move", "move"),
            _item("Copy Objects", "copy"),
        )),
        _popup("Rotate", (
            _item("Rotate in Construction Plane", "rotate"),
            _item("Rotate Around 3D Axis", "rotate3d"),
        )),
        _popup("Scale", (
            _item("Uniform Scale", "scale"),
            _item("One-Dimensional Scale", "scale1d"),
            _item("Two-Dimensional Scale", "scale2d"),
        )),
        _popup("Mirror and Array", (
            _item("Mirror", "mirror"),
            _item("Linear Array", "array"),
            _item("Polar Array", "arraypolar"),
        )),
        _popup("Orient", (
            _item("Orient with Two Points", "orient"),
            _item("Orient with Three Points", "orient3pt"),
        )),
        _popup("Interactive Manipulator", (
            _item("Transform Widget On / Off", "gumball"),
            _item("Transform Widget Alignment", "gumballalignment"),
        )),
        _item("Flatten to Construction Plane", "projecttocplane"),
        _todo("Align, Distribute, and Deform", "align"),
    )),
    ("Analyze", (
        _popup("Measure", (
            _item("Distance", "distance"),
            _item("Angle", "angle"),
            _item("Curve Length", "length"),
            _item("Radius", "radius"),
            _item("Diameter", "diameter"),
            _item("Area", "area"),
            _item("Volume", "volume"),
        )),
        _popup("Centers", (
            _item("Area Centroid", "areacentroid"),
            _item("Volume Centroid", "volumecentroid"),
        )),
        _popup("Inspect", (
            _item("Evaluate Point Coordinates", "evaluatept"),
            _item("Create and Report Bounding Box", "boundingbox"),
            _item("Object Details", "what"),
            _item("Direction and Surface Normal", "dir"),
        )),
        _todo("Curvature, Continuity, and Edge Diagnostics", "curvaturegraph"),
    )),
    ("Render", (
        _todo("Materials, Textures, Lights, and Environments", "material"),
        _todo("Render Settings and Output", "render"),
    )),
    ("Tools", (
        _popup("Document Settings and Maintenance", (
            _item("Units and Tolerance", "units"),
            _item("Purge Unused Data", "purge"),
        )),
        _popup("Layers", (
            _item("Create or Manage Layer", "layer"),
            _item("Layer State Manager", "layerstatemanager"),
        )),
        _popup("Alias Transfer", (
            _item("Import Command Aliases", "optionsimport"),
            _item("Export Command Aliases", "optionsexport"),
        )),
        _popup("Document Data", (
            _item("Set Document User Text", "setdocumentusertext"),
            _item("Read Document User Text", "getdocumentusertext"),
        )),
        _todo("Preferences, Extensions, and Scripting", "options"),
    )),
    ("Window", (
        _todo("Viewport Layouts and Named Views", "viewportlayout"),
        _todo("Workspace Management", "workspace"),
    )),
    ("Help", (
        _item("Command Guide", "help"),
    )),
)


# These additions are authored for Narwhal itself. They restore the breadth expected
# from a desktop CAD menu without importing excluded menu resources: the command names
# come from the public toolbar manifest, and the service gaps come from the public
# roadmap. Unavailable leaves remain explicit and clickable.
_SERVICE_GROUPS = {
    "File": (
        _popup("Recent and Recovery", (
            _item("Recent Files", "__recent_files__"),
            _todo("Recovery Settings", "recoverysettings"),
        )),
        _popup("Templates and Document", (
            _todo("New from Template", "newfromtemplate"),
            _todo("Save a Copy", "saveacopy"),
            _todo("Document Notes", "documentnotes"),
        )),
    ),
    "Help": (
        _popup("Learning and Reference", (
            _todo("Getting Started", "gettingstarted"),
            _todo("Keyboard and Mouse Reference", "inputreference"),
            _todo("Online Documentation", "onlinedocumentation"),
        )),
        _popup("Support", (
            _todo("Report a Bug", "showbugreport"),
            _todo("About Narwhal 3D", "aboutnarwhal3d"),
        )),
    ),
}

_TAB_MENU_GROUPS = {
    "CPlanes": ("View", "Construction Plane Commands"),
    "Set View": ("View", "View Commands"),
    "Display": ("View", "Display Commands"),
    "Select": ("Edit", "Selection Commands"),
    "Viewport Layout": ("View", "Viewport Layout Commands"),
    "Visibility": ("Edit", "Visibility Commands"),
    "Transform": ("Transform", "Additional Transform Commands"),
    "Curve Tools": ("Curve", "Additional Curve Commands"),
    "Surface Tools": ("Surface", "Additional Surface Commands"),
    "Solid Tools": ("Solid", "Additional Solid Commands"),
    "SubD Tools": ("SubD", "SubD Commands"),
    "Mesh Tools": ("Mesh", "Mesh Commands"),
    "Render": ("Render", "Render Commands"),
    "Drafting": ("Drafting", "Drafting Commands"),
}

_STANDARD_COMMAND_GROUPS = {
    "4view": ("View", "Standard View Commands"),
    "setview": ("View", "Standard View Commands"),
    "pan": ("View", "Standard View Commands"),
    "rotateview": ("View", "Standard View Commands"),
    "shade": ("View", "Standard View Commands"),
    "undoview": ("View", "Standard View Commands"),
    "showosnap": ("View", "Standard View Commands"),
    "spotlight": ("Render", "Standard Render Commands"),
    "dim": ("Drafting", "Standard Drafting Commands"),
    "documentproperties": ("File", "Document Commands"),
    "properties": ("Tools", "Interface Commands"),
    "parametrictools": ("Tools", "Interface Commands"),
}


def _walk_commands(rows):
    for row in rows:
        if row[0] == "item" and row[3]:
            yield row[3]
        elif row[0] == "popup":
            yield from _walk_commands(row[3])


def _toolbar_groups():
    used = {command for _menu, rows in _CORE_MENUS
            for command in _walk_commands(rows)}
    used.update(command for rows in _SERVICE_GROUPS.values()
                for command in _walk_commands(rows))
    grouped = {}
    labels = {}
    for tab in toolbar_data.TABS:
        tab_name = tab["name"]
        default_group = _TAB_MENU_GROUPS.get(tab_name)
        for record in tab["buttons"]:
            command = record.get("command")
            if not command or command in used:
                continue
            group = (_STANDARD_COMMAND_GROUPS.get(command)
                     if tab_name == "Standard" else default_group)
            if group is None:
                continue
            label = (record.get("tooltip") or record.get("text") or command).strip()
            if not label:
                continue
            target, group_name = group
            label_key = (target, group_name)
            seen_labels = labels.setdefault(label_key, set())
            if label.casefold() in seen_labels:
                alternate = (record.get("text") or command).strip()
                label = alternate if alternate.casefold() not in seen_labels \
                    else f"{label} [{command}]"
            seen_labels.add(label.casefold())
            grouped.setdefault((target, group_name), []).append(_todo(label, command))
            used.add(command)
    return grouped


def _expanded_menus():
    toolbar_groups = _toolbar_groups()
    expanded = []
    for menu, rows in _CORE_MENUS:
        additions = list(_SERVICE_GROUPS.get(menu, ()))
        additions.extend(
            _popup(group_name, tuple(items))
            for (target, group_name), items in toolbar_groups.items()
            if target == menu
        )
        if additions:
            rows = tuple(rows) + (_sep(),) + tuple(additions)
        expanded.append((menu, tuple(rows)))
    return tuple(expanded)


MENUS = _expanded_menus()


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
