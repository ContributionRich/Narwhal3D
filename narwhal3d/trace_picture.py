"""`TracePicture` support: run the cut-line tracer on a placed Picture and map its result.

OURS: the reference CAD application has no command that traces a picture. The tracing itself
is Rust (`native/cutline`, the `coco-cutline` helper); this module only builds the helper's
arguments, runs it, and maps its answer onto the Picture's frame. Python is used here because
the helper is a separate process (crash-isolated, like the exact-save helper) and the mapping
is a handful of affine operations per control point.

The helper returns closed loops of cubic Beziers in points (1/72 in), origin at the image's
top-left, y down. An affine map preserves a Bezier exactly, so each loop becomes ONE closed
degree-3 NURBS curve whose spans ARE the cubics (knots of multiplicity 3) -- exact geometry,
never a polyline.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import platform
import subprocess

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_S = 120

#: The helper's own defaults, in inches (Coco Production's values).
DEFAULT_OFFSET_IN = 0.05
DEFAULT_MIN_HOLE_IN2 = 0.07
DEFAULT_TOLERANCE_PCT = 12.0
DEFAULT_SMOOTHNESS = 60.0
BACKGROUNDS = ("Auto", "Transparent", "Color")


class TraceError(ValueError):
    """The helper is missing, refused the image, or returned something unreadable."""


def _platform_tag():
    system = {"Linux": "linux", "Windows": "windows", "Darwin": "macos"}.get(
        platform.system(), platform.system().lower())
    machine = platform.machine().lower()
    arch = {"amd64": "x86_64", "x64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}.get(
        machine, machine)
    return f"{system}-{arch}"


def helper_path():
    """The `coco-cutline` executable, or None when it has not been built."""
    configured = os.environ.get("NARWHAL3D_CUTLINE")
    if configured:
        return Path(configured) if Path(configured).is_file() else None
    tag = _platform_tag()
    exe = "coco-cutline.exe" if tag.startswith("windows") else "coco-cutline"
    for candidate in (ROOT / "artifacts" / "cutline" / tag / exe,
                      ROOT / "runtime" / tag / "bin" / exe,
                      ROOT / "native" / "cutline" / "target" / "release" / exe):
        if candidate.is_file():
            return candidate
    return None


def hex_color(text):
    """`#rrggbb` (a leading # optional, any case), or None when `text` is not a colour."""
    raw = str(text or "").strip().lstrip("#")
    if len(raw) != 6:
        return None
    try:
        int(raw, 16)
    except ValueError:
        return None
    return "#" + raw.lower()


def frame_size(frame):
    """``(|u|, |v|)``: the Picture's width and height in model units."""
    return (math.sqrt(sum(float(c) ** 2 for c in frame["u"])),
            math.sqrt(sum(float(c) ** 2 for c in frame["v"])))


def helper_args(image, pixel_width, frame, model_to_inches, settings):
    """The helper's command line (after the executable) for `settings`.

    `settings` holds model-unit values: ``offset``, ``min_hole`` (an area), ``inner_cuts``,
    ``background`` (one of `BACKGROUNDS`), ``color`` (#rrggbb), ``color_tolerance`` (%) and
    ``smoothness`` (0-100). `model_to_inches` converts one model unit to inches. The image is
    sized so its full width spans the Picture's width, so physical sizes (the offset, the
    smallest hole, the speck filter) mean the same thing on the placed picture."""
    width_in = frame_size(frame)[0] * model_to_inches
    if not width_in > 0 or not pixel_width > 0:
        raise TraceError("the picture has no size")
    args = [str(image), "--format", "json",
            "--dpi", repr(pixel_width / width_in),
            "--offset-in", repr(float(settings["offset"]) * model_to_inches),
            "--ignore-holes-under-in2",
            repr(max(0.0, float(settings["min_hole"])) * model_to_inches ** 2),
            "--background-tolerance", repr(float(settings["color_tolerance"])),
            "--smoothness", repr(float(settings["smoothness"]))]
    background = settings["background"]
    if background == "Color":
        color = hex_color(settings.get("color"))
        if color is None:
            raise TraceError("Background=Color needs a colour like #ffffff")
        args += ["--background", color]
    else:
        args += ["--background", background.lower()]
    if not settings["inner_cuts"]:
        args.append("--no-inner-cuts")
    return args


def run(helper, args):
    """Run the helper; its stdout as text. Raises TraceError with the helper's own reason."""
    try:
        done = subprocess.run([str(helper), *args], capture_output=True, text=True,
                              timeout=TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired as exc:
        raise TraceError(f"tracing took longer than {TIMEOUT_S} s") from exc
    except OSError as exc:
        raise TraceError(f"the trace helper could not start ({exc})") from exc
    if done.returncode != 0:
        reason = [line for line in (done.stderr or "").splitlines() if line.strip()]
        text = reason[0].removeprefix("coco-cutline:").strip() if reason else ""
        raise TraceError(text or f"the trace helper failed ({done.returncode})")
    return done.stdout


def trace(helper, args):
    """The helper's JSON answer as a dict with at least ``artwork`` and ``loops``."""
    text = run(helper, args)
    try:
        cut = json.loads(text)
        cut["artwork"]["width"], cut["artwork"]["height"], cut["loops"]  # noqa: B018
    except (ValueError, KeyError, TypeError) as exc:
        raise TraceError("the trace helper returned an unreadable answer") from exc
    return cut


def sample_color(helper, image, x, y):
    """The `#rrggbb` colour of pixel (x, y) of `image` (y down from the top row)."""
    color = hex_color(run(helper, [str(image), "--sample-pixel", str(int(x)), str(int(y))]))
    if color is None:
        raise TraceError("the trace helper returned no colour")
    return color


def pixel_at(frame, uv, pixel_size):
    """The image pixel ``(x, y)`` (y down) under texture coordinate `uv`, or None outside."""
    u, v = uv
    if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
        return None
    w, h = pixel_size
    return min(w - 1, int(u * w)), min(h - 1, int((1.0 - v) * h))


def _map(frame, artwork, point):
    """Image point (pt, y down) -> world point on the Picture (origin lower-left, v up)."""
    fx = float(point[0]) / artwork[0]
    fy = 1.0 - float(point[1]) / artwork[1]
    o, u, v = (frame[k] for k in ("origin", "u", "v"))
    return tuple(float(o[i]) + fx * float(u[i]) + fy * float(v[i]) for i in range(3))


def loop_controls(cut, frame):
    """Each loop's control points in world space: ``A0, O0, I1, A1, ..., In-1... I0, A0``
    (3n + 1 points for n cubic spans; the curve closes on its first point)."""
    artwork = (float(cut["artwork"]["width"]), float(cut["artwork"]["height"]))
    if not (artwork[0] > 0 and artwork[1] > 0):
        raise TraceError("the trace helper reported an empty image")
    out = []
    for loop in cut["loops"]:
        nodes = loop.get("nodes") or []
        if len(nodes) < 1:
            continue
        ctrl = [_map(frame, artwork, nodes[0]["anchor"])]
        for i, node in enumerate(nodes):
            nxt = nodes[(i + 1) % len(nodes)]
            ctrl += [_map(frame, artwork, node["out"]), _map(frame, artwork, nxt["in"]),
                     _map(frame, artwork, nxt["anchor"])]
        out.append(ctrl)
    return out


def knots(span_count):
    """The degree-3 knot vector (no end superfluous knots) whose spans are the cubics."""
    return [float(i) for i in range(span_count + 1) for _ in range(3)]


def bezier_points(ctrl, per_span=12):
    """Display-only samples of a control list from `loop_controls` (preview polyline)."""
    pts = [ctrl[0]]
    for s in range(0, len(ctrl) - 1, 3):
        p0, p1, p2, p3 = ctrl[s:s + 4]
        for k in range(1, per_span + 1):
            t = k / per_span
            a, b, c, d = (1 - t) ** 3, 3 * t * (1 - t) ** 2, 3 * t * t * (1 - t), t ** 3
            pts.append(tuple(a * p0[i] + b * p1[i] + c * p2[i] + d * p3[i] for i in range(3)))
    return pts


def nurbs_curve(r3, ctrl):
    """ONE closed degree-3 `r3.NurbsCurve` whose spans are the cubic Beziers of `ctrl`."""
    spans = (len(ctrl) - 1) // 3
    curve = r3.NurbsCurve(3, False, 4, len(ctrl))
    for i, p in enumerate(ctrl):
        curve.Points[i] = r3.Point4d(p[0], p[1], p[2], 1.0)
    for i, k in enumerate(knots(spans)):
        curve.Knots[i] = k
    return curve


def bezier_loop(r3, ctrl):
    """Preserve a traced loop's exact cubic segments as an explodable PolyCurve."""
    curve = r3.PolyCurve()
    for start in range(0, len(ctrl) - 1, 3):
        if not curve.Append(nurbs_curve(r3, ctrl[start:start + 4])):
            raise TraceError("could not assemble the traced cubic segments")
    return curve


def legacy_bezier_segments(r3, curve):
    """Exact compatibility for the packed, clamped cubic chains used by older traces.

    OURS: only full-multiplicity internal knots qualify, not ordinary smooth NURBS
    curves. Python only coordinates native openNURBS trimming; no curve fitting.
    """
    if not isinstance(curve, r3.NurbsCurve) or curve.Degree != 3:
        return None
    knots_ = list(curve.Knots)
    if len(knots_) < 9 or len(knots_) % 3:
        return None
    breaks = knots_[::3]
    if any(knots_[i:i + 3] != [knots_[i]] * 3 for i in range(0, len(knots_), 3)):
        return None
    if any(a >= b for a, b in zip(breaks, breaks[1:])):
        return None
    parts = [curve.Trim(a, b) for a, b in zip(breaks, breaks[1:])]
    return parts if all(part is not None and part.IsValid for part in parts) else None
