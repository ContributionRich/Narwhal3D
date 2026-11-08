"""Window-overlay coordinates for modal UI across the visible modeling panes.

Python only reads Blender's region rectangles and window identity. It never splits,
resizes or rearranges areas; document geometry and the layout state machine are untouched.
The existing native Window POST_PIXEL bridge paints above editor divider pixels.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Workspace:
    x: int
    y: int
    width: int
    height: int
    type: str = "WINDOW"


def window_key():
    import bpy
    window = getattr(bpy.context, "window", None)
    return window.as_pointer() if window is not None else None


def belongs_to(key):
    return key is None or key == window_key()


def region(ui):
    """The union of visible 3D WINDOW regions, in native window pixels."""
    regions = [r for area in ui._view3d_areas() for r in area.regions
               if r.type == "WINDOW" and r.width > 0 and r.height > 0]
    if not regions:
        return None
    x = min(r.x for r in regions)
    y = min(r.y for r in regions)
    right = max(r.x + r.width for r in regions)
    top = max(r.y + r.height for r in regions)
    return Workspace(x, y, right - x, top - y)
