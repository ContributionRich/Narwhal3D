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
        ("item", "display_modes_don't_localize", "", None),
        ("sep", "", "", None),
        ("item", "Maximize Viewport", "", None),
    ),
}

REPORT = {
    "kinds": len(KINDS),
    "items_in_model_kind": 2,
    "source": "original",
}
