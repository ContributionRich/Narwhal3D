"""Pure bounding-box arithmetic for the ``BoundingBox`` command.

The functions here deliberately know nothing about Blender or rhino3dm.  A box is
represented by its eight world-space corners, projected into an orthonormal frame;
the command layer supplies the geometry's world bounding-box corners and constructs
the resulting exact box.
"""
from __future__ import annotations


def corners(minimum, maximum):
    """Return all eight corners of an axis-aligned box."""
    return tuple(
        (x, y, z)
        for x in (float(minimum[0]), float(maximum[0]))
        for y in (float(minimum[1]), float(maximum[1]))
        for z in (float(minimum[2]), float(maximum[2]))
    )


def frame_bounds(minimum, maximum, frame):
    """Return ``(min_u, min_v, min_w), (max_u, max_v, max_w)`` in ``frame``."""
    points = [frame.from_world(point) for point in corners(minimum, maximum)]
    return (tuple(min(point[i] for point in points) for i in range(3)),
            tuple(max(point[i] for point in points) for i in range(3)))


def union_bounds(bounds):
    """Union an iterable of ``(minimum, maximum)`` triples."""
    values = list(bounds)
    if not values:
        raise ValueError("at least one bound is required")
    return (tuple(min(item[0][i] for item in values) for i in range(3)),
            tuple(max(item[1][i] for item in values) for i in range(3)))


def box_points(bounds, frame):
    """Return the two world points and normal height for an exact box producer.

    ``frame`` is expected to have ``to_world(u, v, w)``.  The returned base points
    share the frame's minimum normal coordinate; the producer can therefore use a
    frame whose origin is shifted to that normal coordinate.
    """
    minimum, maximum = bounds
    return (frame.to_world(minimum[0], minimum[1], minimum[2]),
            frame.to_world(maximum[0], maximum[1], minimum[2]),
            float(maximum[2] - minimum[2]))


def report(label, bounds):
    """Format the measured extreme corners and dimensions for one output box."""
    minimum, maximum = bounds
    size = tuple(maximum[i] - minimum[i] for i in range(3))
    fmt = lambda point: "(" + ", ".join(f"{value:.6g}" for value in point) + ")"
    return (f"{label}: min {fmt(minimum)} max {fmt(maximum)} "
            f"size ({size[0]:.6g} x {size[1]:.6g} x {size[2]:.6g})")
