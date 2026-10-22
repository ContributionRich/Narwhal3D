"""Explicit owner-authored icon mapping for linked-toolbar variants.

The generated toolbar source historically chose an icon from only the first command verb,
which collapses macros such as ``Circle Diameter`` and ``Circle 3Point`` onto one bitmap.
These stable ``(flyout, text)`` keys preserve each visible symbol without guessing from a
filename or importing private source data at runtime. This is Blender UI integration data;
geometry and computation do not pass through it.
"""
from __future__ import annotations


FLYOUT_SOURCES = {
    ("Point", "Closest point"): "closestpt.png",
    ("Point", "Closest points between two objects"): "closestpt_object.png",
    ("Point", "Point cloud"): "pointcloud.png",
    ("Point", "Add to point cloud"): "pointcloud_add.png",

    ("Lines", "Line"): "line.png",
    ("Lines", "Line: from midpoint"): "line_frommidpoint.png",
    ("Lines", "Line: Surface normal"): "line_normal.png",
    ("Lines", "Line: Vertical to CPlane"): "line_vertical.png",
    ("Lines", "Line: by 4 points"): "line_4pt.png",
    ("Lines", "Line: Bisector"): "line_bisector.png",
    ("Lines", "Line: Angled"): "line_angled.png",
    ("Lines", "Line: Perpendicular from curve"): "line_perpfromcrv.png",
    ("Lines", "Line: Perpendicular to 2 curves"): "line_perpperp.png",
    ("Lines", "Line: Tangent and perpendicular to curves"): "line_tanperp.png",
    ("Lines", "Line: Tangent from curve"): "line_tanfrom.png",
    ("Lines", "Line: Tangent to 2 curves"): "line_tantan.png",

    ("Curve", "Sketch"): "sketch.png",
    ("Curve", "Sketch on polygon mesh"): "sketch_onmesh.png",
    ("Curve", "Conic"): "conic.png",
    ("Curve", "Conic: tangent at start"): "conic_tangentatstart.png",

    ("Circle", "Circle: center, radius"): "circle.png",
    ("Circle", "Circle: diameter"): "circle_diameter.png",
    ("Circle", "Circle: 3 points"): "circle_3pt.png",
    ("Circle", "Circle around curve"): "circle_aroundcurve.png",
    ("Circle", "Circle: tangent, tangent, radius"): "circle_ttr.png",
    ("Circle", "Circle tangent to 3 curves"): "circle_tantantan.png",
    ("Circle", "Circle vertical to CPlane: center, radius"): "circle_vertical_radius.png",
    ("Circle", "Circle vertical to CPlane: diameter"): "circle_vertical_diameter.png",
    ("Circle", "Circle: deformable"): "circle_deformable.png",
    ("Circle", "Circle: fit points"): "circle_fitpoints.png",

    ("Ellipse", "Ellipse: from center"): "ellipse.png",
    ("Ellipse", "Ellipse: diameter"): "ellipse_diameter.png",
    ("Ellipse", "Ellipse: from foci"): "ellipse_foci.png",
    ("Ellipse", "Ellipse: around curve"): "ellipse_aroundcurve.png",
    ("Ellipse", "Ellipse: by corners"): "ellipse_corners.png",
    ("Ellipse", "Ellipse: Deformable"): "ellipse_deformable.png",

    ("Arc", "Arc: center, start, angle"): "arc.png",
    ("Arc", "Arc: start, end, point on arc"): "arc_sep.png",
    ("Arc", "Arc: start, end, direction at start"): "arc_sed.png",
    ("Arc", "Arc: start, end, radius"): "arc_ser.png",
    ("Arc", "Arc: tangent to curves"): "arc_tan.png",

    ("Rectangle", "Rectangle: corner to corner"): "rectangle_corner.png",
    ("Rectangle", "Rectangle: center, corner"): "rectangle_centercorner.png",
    ("Rectangle", "Rectangle: 3 points"): "rectangle_3pt.png",
    ("Rectangle", "Rectangle: vertical"): "rectangle_vertical.png",
    ("Rectangle", "Rounded rectangle"): "rectangle_round.png",

    ("Polygon", "Polygon: Center, Radius"): "polygon.png",
    ("Polygon", "Circumscribed polygon: Center, Radius"): "polygon_circumscribed.png",
    ("Polygon", "Polygon: Edge"): "polygon_edge.png",
    ("Polygon", "Square: Center, Corner"): "polygon_square_center.png",
    ("Polygon", "Circumscribed square: Center, Radius"): "polygon_square_circumscribed.png",
    ("Polygon", "Square: Edge"): "polygon_square_edge.png",
    ("Polygon", "Polygon: Star"): "polygon_star.png",

    ("Surface Creation", "Rectangular plane: Corner to corner"): "plane.png",
    ("Surface Creation", "Rectangular plane: 3 points"): "plane_3pt.png",
    ("Surface Creation", "Vertical plane"): "plane_vertical.png",

    ("Solid Creation", "Sphere: Center, Radius"): "sphere.png",
    ("Solid Creation", "Sphere: Diameter"): "sphere_diameter.png",
    ("Solid Creation", "Pipe: Flat caps"): "pipe.png",
    ("Solid Creation", "Pipe: Round caps"): "pipe_roundcaps.png",

    ("Solid Tools", "Edit FilletEdge"): "filletedge_edit.png",
    ("Solid Tools", "Variable radius fillet"): "filletedge.png",
    ("Solid Tools", "Move face"): "moveface.png",
    ("Solid Tools", "Move face to a boundary"): "moveface_boundary.png",
    ("Solid Tools", "Extrude face"): "extrudesrf.png",
    ("Solid Tools", "Extrude face to a boundary"): "extrude_facetoboundary.png",

    ("Grouping", "Group"): "group.png",
    ("Grouping", "Ungroup"): "ungroup.png",
    ("Grouping", "Add to group"): "addtogroup.png",
    ("Grouping", "Remove from group"): "removefromgroup.png",
    ("Grouping", "Set group name"): "setgroupname.png",

    ("Point Edit", "Insert a control point"): "insertcontrolpoint.png",
    ("Point Edit", "Remove a control point"): "removecontrolpoint.png",
    ("Point Edit", "Remove knot"): "removeknot.png",

    ("Transform", "Copy"): "move.png",
    ("Transform", "Move vertical"): "move_vertical.png",
    ("Transform", "Move X+"): "move_x+.png",

    ("Align and Distribute", "Align top"): "align_top.png",
    ("Align and Distribute", "Align bottom"): "align_bottom.png",
    ("Align and Distribute", "Align left"): "align_left.png",
    ("Align and Distribute", "Align right"): "align_right.png",
    ("Align and Distribute", "Align horizontal centers"): "align_horiz_centers.png",
    ("Align and Distribute", "Align vertical centers"): "align_vertical_centers.png",
    ("Align and Distribute", "Align centers"): "align_centers.png",

    ("History", "History settings"): "history.png",
    ("History", "Record history"): "history_record.png",
    ("History", "Update history on selected objects"): "historyupdate.png",
    ("History", "Stop history recording"): "history_record_no.png",
    ("History", "Lock objects with history"): "history_lock.png",
    ("History", "Purge history"): "historypurge.png",
}


def runtime_name(source_name: str) -> str:
    """Return a safe project runtime name for one explicit source basename."""
    return "narwhal-" + source_name.replace("+", "plus")


def icon_for(flyout: str, text: str) -> str | None:
    """Return the explicit runtime icon for a stable flyout slot, if mapped."""
    source = FLYOUT_SOURCES.get((flyout, text))
    return runtime_name(source) if source else None


ICON_SOURCES = {runtime_name(source): source for source in FLYOUT_SOURCES.values()}
ICON_NAMES = frozenset(ICON_SOURCES)

# A few separately named contributor files currently contain byte-identical artwork. Runtime
# badges preserve the original symbol while making every choice in the same flyout visibly
# distinguishable. Values are compact binary marker IDs rendered by build_toolbar_icons.py.
ICON_BADGES = {
    runtime_name("sketch.png"): 1,
    runtime_name("sketch_onmesh.png"): 2,
    runtime_name("line_normal.png"): 1,
    runtime_name("line_perpfromcrv.png"): 2,
    runtime_name("line_perpperp.png"): 3,
    runtime_name("line_tanperp.png"): 4,
    runtime_name("line_tanfrom.png"): 1,
    runtime_name("line_tantan.png"): 2,
    runtime_name("filletedge_edit.png"): 1,
    runtime_name("filletedge.png"): 2,
    runtime_name("extrudesrf.png"): 1,
    runtime_name("extrude_facetoboundary.png"): 2,
}
