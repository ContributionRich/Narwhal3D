"""Original Narwhal 3D viewport-title context menu.

The application expects a mapping from viewport kind to four-field menu rows.  Narwhal
3D exposes only actions it can complete directly plus the dynamic display-mode slot
expanded by the UI. Numeric dispatch IDs, compatibility-only entries, and unavailable
placeholders are intentionally absent.
"""

KINDS = ("Viewport",)
MODEL_KIND = "Viewport"

MENUS = {
    MODEL_KIND: (
        ("popup", "Display Mode", "", (
            ("item", "display_modes_don't_localize", "", None),
        )),
        ("sep", "", "", None),
        ("item", "Zoom", "", None),
        ("item", "Construction Plane", "", None),
        ("item", "Grid Settings", "", None),
        ("sep", "", "", None),
        ("item", "Maximize Viewport", "", None),
    ),
}

REPORT = {
    "kinds": len(KINDS),
    "items_in_model_kind": 5,
    "source": "original",
}
