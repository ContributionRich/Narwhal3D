"""Narwhal 3D viewport-title context menu.

The layout follows a reference screenshot of the open viewport-title menu: Maximize at
the top, the display modes listed INLINE with the current one marked, then grouped view
actions. Rows whose payload is ``None`` and that `views.caption_action` cannot route are
drawn greyed and explain on click that they are not implemented yet.
"""

KINDS = ("Viewport",)
MODEL_KIND = "Viewport"

MENUS = {
    MODEL_KIND: (
        ("item", "Maximize", "", None),
        ("item", "Restore Named View", "", None),
        ("sep", "", "", None),
        ("item", "display_modes_don't_localize", "", None),
        ("item", "Technical", "", None),
        ("item", "Artistic", "", None),
        ("item", "Pen", "", None),
        ("item", "Arctic", "", None),
        ("item", "Raytraced", "", None),
        ("sep", "", "", None),
        ("item", "Print Preview", "", None),
        ("item", "Flat Shade", "", None),
        ("item", "SubD Display Toggle", "", None),
        ("item", "Shade Selected Objects Only", "", None),
        ("item", "Locked", "", None),
        ("sep", "", "", None),
        ("popup", "Pan, Zoom, and Rotate", "", (
            ("item", "Zoom", "", None),
        )),
        ("sep", "", "", None),
        ("item", "Set View", "", None),
        ("item", "Set CPlane", "", None),
        ("item", "Set Camera", "", None),
        ("sep", "", "", None),
        ("item", "Active Viewport", "", None),
        ("item", "Viewport Layout", "", None),
        ("sep", "", "", None),
        ("item", "Background Bitmap", "", None),
        ("item", "Grid Options...", "", None),
        ("item", "Capture", "", None),
        ("sep", "", "", None),
        ("item", "Close Viewport", "", None),
        ("sep", "", "", None),
        ("item", "Clipping plane", "", None),
        ("item", "Refresh Shade", "", None),
        ("item", "Display Options...", "", None),
        ("item", "Viewport Properties...", "", None),
    ),
}

REPORT = {
    "kinds": len(KINDS),
    "items_in_model_kind": 27,
    "source": "reference layout",
}
