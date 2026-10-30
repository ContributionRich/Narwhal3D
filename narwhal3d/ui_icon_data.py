"""Approved v4.4 presentation overrides; imported toolbar data stays untouched.

MEASURED: ICON_MAPPING.csv in Narwhal3D_UI_Implementation_Handoff_v4_4.
Indices count non-separator context tools; Standard means the favorites sidebar.
This is static Python host-UI glue, not geometry or performance-sensitive work.
"""

# (category, visible row): (original label, original PNG, approved PNG).
# Guard both original fields: a moved/replaced tool must not inherit another icon.
TOOL_ICONS = {
    ("Standard", 20): ("Group", "narwhal-group.png", "generated-group.png"),
    ("Standard", 21): ("Ungroup", "narwhal-group.png", "generated-ungroup.png"),
    ("Standard", 26): ("Copy", "narwhal-copy.png", "generated-copy.png"),
    ("CPlanes", 0): ("Set CPlane origin", "narwhal-cplane.png", "generated-set-cplane-origin.png"),
    ("CPlanes", 1): ("Set CPlane elevation", "narwhal-cplane.png", "generated-set-cplane-elevation.png"),
    ("CPlanes", 2): ("Set CPlane to object", "narwhal-cplane.png", "generated-set-cplane-to-object.png"),
    ("CPlanes", 3): ("Set CPlane to surface", "narwhal-cplane.png", "generated-set-cplane-to-surface.png"),
    ("CPlanes", 4): ("Set CPlane perpendicular to curve", "narwhal-cplane.png", "generated-set-cplane-perpendicular-to-curve.png"),
    ("CPlanes", 5): ("Rotate CPlane", "narwhal-cplane.png", "generated-rotate-cplane.png"),
    ("CPlanes", 6): ("Set CPlane:vertical", "narwhal-cplane.png", "generated-set-cplane-vertical.png"),
    ("CPlanes", 7): ("Set CPlane by 3 points", "narwhal-cplane.png", "generated-set-cplane-by-3-points.png"),
    ("CPlanes", 8): ("Set CPlane by X-axis", "narwhal-cplane.png", "generated-set-cplane-by-x-axis.png"),
    ("CPlanes", 9): ("Set CPlane by Z-axis", "narwhal-cplane.png", "generated-set-cplane-by-z-axis.png"),
    ("CPlanes", 10): ("Previous CPlane", "narwhal-cplane.png", "generated-previous-cplane.png"),
    ("CPlanes", 16): ("Set CPlane to view", "narwhal-cplane.png", "generated-set-cplane-to-view.png"),
    ("CPlanes", 17): ("Set CPlane World Top", "narwhal-cplane.png", "generated-set-cplane-world-top.png"),
    ("CPlanes", 18): ("Set CPlane World Bottom", "narwhal-cplane.png", "generated-set-cplane-world-bottom.png"),
    ("CPlanes", 19): ("Set CPlane World Front", "narwhal-cplane.png", "generated-set-cplane-world-front.png"),
    ("CPlanes", 20): ("Set CPlane World Back", "narwhal-cplane.png", "generated-set-cplane-world-back.png"),
    ("CPlanes", 21): ("Set CPlane World Right", "narwhal-cplane.png", "generated-set-cplane-world-right.png"),
    ("CPlanes", 22): ("Set CPlane World Left", "narwhal-cplane.png", "generated-set-cplane-world-left.png"),
    ("Set View", 2): ("Zoom dynamic    Ctrl+RMB", "narwhal-zoom.png", "generated-zoom-dynamic-ctrl-rmb.png"),
    ("Set View", 3): ("Zoom extents", "narwhal-zoom.png", "generated-zoom-extents.png"),
    ("Set View", 4): ("Zoom window", "narwhal-zoom.png", "generated-zoom-window.png"),
    ("Set View", 5): ("Zoom selected", "narwhal-zoom.png", "generated-zoom-selected.png"),
    ("Set View", 9): ("Top view", "narwhal-setview_top.png", "generated-top-view.png"),
    ("Set View", 10): ("Bottom view", "narwhal-setview_top.png", "generated-bottom-view.png"),
    ("Set View", 11): ("Front view", "narwhal-setview_top.png", "generated-front-view.png"),
    ("Set View", 12): ("Right view", "narwhal-setview_top.png", "generated-right-view.png"),
    ("Set View", 13): ("Right view", "narwhal-setview_top.png", "generated-left-view.png"),
    ("Set View", 14): ("Back view", "narwhal-setview_top.png", "generated-back-view.png"),
    ("Set View", 15): ("Perspective view", "narwhal-setview_top.png", "generated-perspective-view.png"),
    ("Set View", 24): ("Plan view of CPlane", "narwhal-setview_top.png", "generated-plan-view-of-cplane.png"),
    ("Set View", 27): ("Bottom view of CPlane", "narwhal-setview_top.png", "generated-bottom-view-of-cplane.png"),
    ("Set View", 28): ("Top view of CPlane", "narwhal-setview_top.png", "generated-top-view-of-cplane.png"),
    ("Set View", 29): ("Front view of CPlane", "narwhal-setview_top.png", "generated-front-view-of-cplane.png"),
    ("Set View", 30): ("Back view of CPlane", "narwhal-setview_top.png", "generated-back-view-of-cplane.png"),
    ("Set View", 31): ("Right view of CPlane", "narwhal-setview_top.png", "generated-right-view-of-cplane.png"),
    ("Set View", 32): ("Left view of CPlane", "narwhal-setview_top.png", "generated-left-view-of-cplane.png"),
    ("Display", 2): ("Shade", "narwhal-shade.png", "generated-shade.png"),
    ("Display", 15): ("Toggle shade selected mode", "narwhal-shade.png", "generated-toggle-shade-selected-mode.png"),
    ("Display", 18): ("Add Clipping plane", "narwhal-clippingplane.png", "generated-add-clipping-plane.png"),
    ("Display", 19): ("Disable clipping plane", "narwhal-clippingplane.png", "generated-disable-clipping-plane.png"),
    ("Viewport Layout", 13): ("New layout: 4 details", "narwhal-layout.png", "generated-new-layout-4-details.png"),
    ("Visibility", 0): ("Hide", "narwhal-hide.png", "generated-hide.png"),
    ("Visibility", 1): ("Show", "narwhal-hide.png", "generated-show.png"),
    ("Visibility", 2): ("Show selected objects", "narwhal-hide.png", "generated-show-selected-objects.png"),
    ("Visibility", 3): ("Invert Hide", "narwhal-isolate.png", "generated-invert-hide.png"),
    ("Visibility", 4): ("Swap hidden and visible objects", "narwhal-hide.png", "generated-swap-hidden-and-visible-objects.png"),
    ("Visibility", 6): ("Show selected objects in active detail", "narwhal-detail_add.png", "generated-show-selected-objects-in-active-detail.png"),
    ("Visibility", 7): ("Show layers in detail", "narwhal-detail_add.png", "generated-show-layers-in-detail.png"),
    ("Visibility", 8): ("Lock", "narwhal-lock.png", "generated-lock.png"),
    ("Visibility", 9): ("Unlock", "narwhal-lock.png", "generated-unlock.png"),
    ("Visibility", 10): ("Unlock selected objects", "narwhal-lock.png", "generated-unlock-selected-objects.png"),
    ("Visibility", 11): ("Invert Lock", "narwhal-isolate.png", "generated-invert-lock.png"),
    ("Visibility", 12): ("Swap locked and unlocked objects", "narwhal-lock.png", "generated-swap-locked-and-unlocked-objects.png"),
    ("Visibility", 14): ("Hide control points", "narwhal-hide.png", "generated-hide-control-points.png"),
    ("Visibility", 15): ("Add Clipping plane", "narwhal-clippingplane.png", "generated-add-clipping-plane.png"),
    ("Visibility", 16): ("Disable clipping plane", "narwhal-clippingplane.png", "generated-disable-clipping-plane.png"),
    ("Transform", 0): ("Copy", "narwhal-move.png", "generated-copy.png"),
    ("Transform", 1): ("Move vertical", "narwhal-move.png", "generated-move-vertical.png"),
    ("Transform", 28): ("Move X+", "narwhal-move.png", "generated-move-x.png"),
    ("Solid Tools", 10): ("Edit FilletEdge", "narwhal-filletedge.png", "generated-edit-filletedge.png"),
    ("Solid Tools", 11): ("Variable radius fillet", "narwhal-filletedge.png", "generated-variable-radius-fillet.png"),
    ("Solid Tools", 14): ("Move face", "narwhal-moveface.png", "generated-move-face.png"),
    ("Solid Tools", 15): ("Move face to a boundary", "narwhal-moveface.png", "generated-move-face-to-a-boundary.png"),
    ("Solid Tools", 16): ("Extrude face", "narwhal-extrudesrf.png", "generated-extrude-face.png"),
    ("Solid Tools", 17): ("Extrude face to a boundary", "narwhal-extrudesrf.png", "generated-extrude-face-to-a-boundary.png"),
    ("Render", 0): ("Render", "narwhal-render.png", "generated-render.png"),
    ("Render", 1): ("Render preview", "narwhal-render.png", "generated-render-preview.png"),
    ("Render", 34): ("Preview animation", "narwhal-setturntableanimation.png", "generated-preview-animation.png"),
    ("Render", 35): ("Record animation", "narwhal-setturntableanimation.png", "generated-record-animation.png"),
    ("Drafting", 0): ("New layout: 4 details", "narwhal-layout.png", "generated-new-layout-4-details.png"),
    ("Drafting", 1): ("New layout", "narwhal-layout.png", "generated-new-layout.png"),
    ("Drafting", 3): ("Layout properties", "narwhal-layout.png", "generated-layout-properties.png"),
    ("Drafting", 5): ("Linear dimension", "narwhal-dim.png", "generated-linear-dimension.png"),
    ("Drafting", 6): ("Horizontal dimension", "narwhal-dim.png", "generated-horizontal-dimension.png"),
    ("Drafting", 7): ("Vertical dimension", "narwhal-dim.png", "generated-vertical-dimension.png"),
    ("Drafting", 19): ("Hatch", "narwhal-hatch.png", "generated-hatch.png"),
    ("Drafting", 20): ("Hatch Base", "narwhal-hatch.png", "generated-hatch-base.png"),
    ("Drafting", 21): ("Hatch scaling", "narwhal-hatch.png", "generated-hatch-scaling.png"),
}

INSPECTOR_ICONS = {
    "Layers": "generated-panel-layers.png",
    "Properties": "generated-panel-properties.png",
    "Display": "generated-panel-display.png",
    "Bug Report": "generated-panel-bug-report.png",
    "AI Assistant": "generated-panel-ai-assistant.png",
}
AI_REPLY_ICON = "narwhal-ai-mark.png"
PNG_NAMES = frozenset(row[2] for row in TOOL_ICONS.values()) | frozenset(
    INSPECTOR_ICONS.values()) | {AI_REPLY_ICON}
SVG_NAMES = frozenset(name[:-4] + ".svg" for name in PNG_NAMES)


def presentation(category, index, record):
    """Return approved ``(text, PNG)`` only for an unchanged authored slot.

    Never route commands or copy availability from the browser fixture. If a
    later functional edit changes the slot identity, retain its current art.
    """
    match = TOOL_ICONS.get((category, index))
    if match is None:
        return None
    original_text, original_icon, icon = match
    if record.get("text") != original_text or record.get("icon") != original_icon:
        return None
    text = original_text
    if (category, index) == ("Set View", 13):
        # The two authored Right labels are identical; the Left tooltip is decisive.
        if record.get("tooltip") != "Left view" or record.get("command") != "setview":
            return None
        text = "Left view"
    return text, icon
