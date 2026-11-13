"""Native-owned coordinate grammar. CPlane callbacks and text presentation only.

resolve's CPlane object is a host API: its callbacks cannot be invoked inside the
pure Rust crate. format_world retains Python float repr/format for lossless host
text interchange; neither is an alternative coordinate parser.
"""
MODEL_UNIT = 'Millimeters'
UNSUPPORTED = 'not supported yet'
_native = None


def _adapter():
    global _native
    if _native is None:
        try:
            from . import native_coords
        except ImportError:
            import importlib.util
            import sys
            from pathlib import Path
            canonical = 'narwhal3d_native_coords'
            native_coords = sys.modules.get(canonical)
            if native_coords is None:
                here = Path(__file__).resolve().parent
                path = here / 'native_coords.py'
                if here.name == 'tmp' and not path.exists():
                    # Inactive candidate only; Main removes before activation.
                    path = here.parent / 'narwhal3d' / 'native_coords.py'
                spec = importlib.util.spec_from_file_location(canonical, path)
                native_coords = importlib.util.module_from_spec(spec)
                sys.modules[canonical] = native_coords
                try:
                    spec.loader.exec_module(native_coords)
                except BaseException:
                    sys.modules.pop(canonical, None)
                    raise
        _native = native_coords
    return _native


def set_model_unit(name):
    global MODEL_UNIT
    MODEL_UNIT = name or 'Millimeters'


def parse(text):
    return _adapter().parse(text, MODEL_UNIT)


def parse_scalar(text):
    return _adapter().parse_scalar(text, MODEL_UNIT)


def parse_angle(text):
    return _adapter().parse_angle(text)


def split_top_level(text, sep=','):
    return _adapter().split_top_level(text, sep)


def resolve(text, cplane, last_point=None):
    """Typed text -> an ``(x, y, z)`` WORLD point. Returns ``(point, error)``.

    `cplane` is the ACTIVE viewport's construction plane (a `cplane.CPlane`);
    `last_point` is the previously picked world point, needed by the relative
    forms. Exactly one of `point` / `error` is None.
    """
    coords, is_world, is_relative, err = parse(text)
    if err:
        return None, err
    u, v, w = coords

    if is_relative:
        if last_point is None:
            return None, "there is no previous point for a relative coordinate"
        if is_world:
            delta = (u, v, w)
        else:
            # relative CPlane: offset along the CPlane's AXES, without moving to
            # its origin -- "r2,3 ... 2 units in the x direction and 3 units in
            # the y direction from the previous point".
            delta = cplane.vector_to_world(u, v, w)
        return (last_point[0] + delta[0],
                last_point[1] + delta[1],
                last_point[2] + delta[2]), None

    if is_world:
        return (u, v, w), None
    return cplane.to_world(u, v, w), None


def format_world(point, precision=None):
    """A WORLD point -> text :func:`resolve` will read back as the same point.

    Use this for any point the program already knows in world coordinates -- a
    mouse pick, a snap, a computed location -- before feeding it to a command.

    WHY THE WORLD PREFIX IS REQUIRED
    --------------------------------
    A viewport click is converted to a WORLD point, then handed to the command as
    text. It used to be formatted bare (``"5.0000,0.0000,3.0000"``), and bare text
    is read in the **CPlane's** coordinates -- which is correct for something a
    *user* typed and wrong for something we already resolved. The point was
    therefore transformed twice:

        click at world (5, 0, 3) in Front  ->  fed "5,0,3"  ->  (5, -3, 0)

    so the centre dropped onto z = 0 ("it always goes down"), and in Right the
    components scrambled to (3, 0, 5). In Top the CPlane *is* world XY, so the
    double transform was the identity and the bug was invisible -- which is why it
    only showed up in Front and Right.

    The ``w`` prefix is the reference CAD application's own documented way to say "these are world
    coordinates" (``user_interface/accurate_modeling.htm``: "Type ``w`` in front of
    the coordinates, to use the world coordinate system"), so this is fidelity,
    not a workaround.

    The required invariant for every CPlane is:

        resolve(format_world(p), any_plane) == p

    **EXACTLY**, not to 4 decimals. This used to format with ``:.4f``, so every
    click and every SNAP was rounded to 1e-4 on its way into the command: an End
    snap onto a point at x = 1/3 landed at 0.3333, and a distance-constrained pick
    of exactly 3 at 45 degrees came out 2.1213. For geometry that goes to a machine shop that is a defect, not a
    display nicety. The old round-trip test could not see it because every test
    point had four decimals or fewer. ``repr`` is Python's shortest string that
    reads back as the identical float, so nothing is lost. `precision` is kept for a
    caller that wants a short, human-readable echo, and must ask for it.
    """
    x, y, z = (float(c) for c in point)
    if precision is None:
        return f"w{x!r},{y!r},{z!r}"
    return f"w{x:.{precision}f},{y:.{precision}f},{z:.{precision}f}"
