"""Original Narwhal 3D viewport-title context menu.

The application expects a mapping from viewport kind to four-field menu rows.  Narwhal
3D currently exposes only the action it can complete directly; numeric dispatch IDs,
compatibility-only entries, and unavailable placeholders are intentionally absent.
"""

KINDS = ("Viewport",)
MODEL_KIND = "Viewport"

MENUS = {
    MODEL_KIND: (
        ("item", "Maximize Viewport", "", None),
    ),
}

REPORT = {
    "kinds": len(KINDS),
    "items_in_model_kind": 1,
    "source": "original",
}
