"""A finite construction-plane grid for each viewport.

Unlike Blender's infinite, screen-aligned floor and orthographic grids, this grid occupies
a bounded portion of the viewport's construction plane. ``line_count`` controls the number
of minor lines from the origin to each edge, so the grid spans
``[-line_count * spacing, +line_count * spacing]`` in both plane axes. Major lines occur at
the configured interval.

The positive halves of the lines through the origin are drawn as the coloured X and Y axes;
the halves remain ordinary major lines. Grid lines are depth-tested and placed
slightly behind the construction plane so geometry on the plane remains visible.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Construction-axis colours in sRGB 0..255.
X_AXIS_RGB = (151, 89, 90)
Y_AXIS_RGB = (86, 151, 88)


@dataclass
class GridSettings:
    """One viewport's grid with the standard spacing and line-count defaults."""
    spacing: float = 1.0
    line_count: int = 200
    major_every: int = 10
    show_grid: bool = True
    show_axes: bool = True

    def extent(self):
        """Half the grid's width: the distance from the CPlane origin to an edge."""
        return self.spacing * self.line_count


def grid_segments(plane, settings=None):
    """World-space segments for `plane`'s grid: ``{"minor", "major", "xaxis", "yaxis"}``.

    Each value is a list of ``(start, end)`` point pairs. The lines through the origin are
    split there: the positive halves become the coloured axes (when `show_axes`), the
    halves major lines. Empty lists when the grid is hidden.
    """
    s = settings or GridSettings()
    out = {"minor": [], "major": [], "xaxis": [], "yaxis": []}
    if not s.show_grid and not s.show_axes:
        return out
    n = max(1, int(s.line_count))
    step = float(s.spacing)
    if step <= 0.0:
        return out
    L = n * step
    every = max(1, int(s.major_every))
    w = plane.to_world
    if s.show_grid:
        for i in range(-n, n + 1):
            if i == 0:
                continue
            c = i * step
            kind = "major" if i % every == 0 else "minor"
            out[kind].append((w(c, -L), w(c, L)))       # a line of constant u
            out[kind].append((w(-L, c), w(L, c)))       # a line of constant v
        # the halves of the two lines through the origin
        out["major"].append((w(-L, 0.0), w(0.0, 0.0)))
        out["major"].append((w(0.0, -L), w(0.0, 0.0)))
    positive = [((w(0.0, 0.0), w(L, 0.0)), "xaxis"), ((w(0.0, 0.0), w(0.0, L)), "yaxis")]
    for seg, kind in positive:
        if s.show_axes:
            out[kind].append(seg)
        elif s.show_grid:
            out["major"].append(seg)
    return out
