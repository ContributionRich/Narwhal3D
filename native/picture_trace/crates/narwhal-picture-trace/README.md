# narwhal-picture-trace

The Narwhal 3D picture tracer behind the **TracePicture** command, packaged so another
program can use it.
Give it an image and its printed size. It returns closed Bézier cut lines around the
artwork, offset outward, ready for a vinyl, laser or print-and-cut machine.

Narwhal 3D runs this binary for TracePicture and maps its Bézier loops onto the picture.
The `pipeline` module exposes the same steps to callers that render artwork themselves.

You can use it two ways:

- **Command line** (any language): `narwhal-picture-trace image.png --width-in 4 -o cut.svg`
- **Rust library**: `narwhal_picture_trace::generate_cut(&image, size, &settings)`

## What it does

1. **Trims** fully transparent padding.
2. **Finds the background**: transparency, plus a solid border colour that is detected
   automatically, or one you give it.
3. **Normalizes** the art so its longest side is processed as 5 inches at 300 ppi
   (capped at 32 megapixels). Small and large art therefore trace with the same detail.
4. **Cleans the mask**: drops specks under 0.02 in², fills or keeps holes, and joins nearby
   separate islands (up to 0.15 in apart) into one rounded envelope.
5. **Traces and fits** smooth cubic Bézier curves (60° corners, 75° spike rule) and repairs
   any self-crossing curves.
6. **Offsets** the cut at real size with round joins. The default offset is 0.05 in.

## Units and coordinates

Every number is in **points** (1/72 inch). The origin is the **top-left corner of the image
you passed in** (before trimming), and y points **down**, as in SVG.

A cut is a list of closed **loops**: outer outlines, plus holes when `keep_inner_cuts` is on.
Each loop is a list of nodes. Each node has an `anchor`, an `in` handle and an `out` handle.
The curve from node *i* to node *i+1* is the cubic Bézier
`anchor[i], out[i], in[i+1], anchor[i+1]`. The last node connects back to the first.
Outer loops and holes wind in opposite directions, so either the nonzero or the even-odd
fill rule gives the cut shape.

## Command line

```text
narwhal-picture-trace IMAGE [options]

Size (pick one; default --dpi 300):
  --dpi N                    image pixels per inch
  --width-in W               artwork width in inches (keeps aspect ratio)
  --height-in H              artwork height in inches (keeps aspect ratio)
                             (artwork = the image without fully transparent padding,
                             the size TracePicture uses)
Cut:
  --offset-in D              distance from the art to the cut (default 0.05; negative = inside)
  --smoothness P             0 (detailed: closest fit, more points) to 100 (smooth: fewer
                             points); default 40. Also rounds bridges joining islands
  --tolerance-pt T           exact curve-fitting tolerance in points (overrides --smoothness)
  --no-inner-cuts            do not cut holes inside the art
  --ignore-holes-under-in2 A skip holes smaller than A square inches (default 0.07)
  --background MODE          auto (default), transparent, or a colour like #ffffff
  --background-tolerance P   colour match tolerance in percent, 0-60 (default 12)

Output:
  -o, --output FILE          write here instead of standard output
  --format svg|json          default svg; json lists every Bezier node in points
  --cleaned-image FILE       also write the image with its background removed (PNG)
```

It reads PNG, JPEG, WebP, GIF, TIFF and BMP images up to 64 megapixels. The exit code is 0
on success and 2 on any error, with the reason printed to standard error. A one-line summary
also goes to standard error, so standard output carries only the SVG or JSON.

**SVG output** is sized in inches with a `viewBox` in points, and draws the cut as a red
0.5 pt hairline. It opens 1:1 in Illustrator, Inkscape or CorelDRAW.

**JSON output:**

```json
{
  "units": "pt",
  "points_per_inch": 72,
  "artwork": {"width": 288.0, "height": 274.4},
  "bounds": [x0, y0, x1, y1],
  "unified_join_pt": null,
  "svg_path": "M… C… Z",
  "loops": [
    {"closed": true,
     "nodes": [{"anchor": [x, y], "in": [x, y], "out": [x, y], "corner": false}, …]}
  ]
}
```

`artwork` is the size of the whole input image in points. `unified_join_pt` is the bridge
radius when separate islands were joined into one cut, and `null` otherwise.

Example, from another program:

```python
import json, subprocess
out = subprocess.run(["narwhal-picture-trace", "sticker.png", "--width-in", "3", "--format", "json"],
                     check=True, capture_output=True, text=True).stdout
cut = json.loads(out)
```

## Rust library

Add it as a path dependency (it and its dependencies are in this source tree):

```toml
[dependencies]
narwhal-picture-trace = { path = "crates/narwhal-picture-trace" }
```

```rust
use narwhal_picture_trace::{ArtworkSize, Background, CutSettings, Image, generate_cut};

let image = Image::decode(&std::fs::read("sticker.png")?)?;
// Or from pixels you already have: Image::new(width, height, rgba_bytes)?
let size = ArtworkSize::from_artwork_width_inches(&image, 4.0);
let settings = CutSettings {
    offset_pt: 0.0625 * 72.0,
    background: Background::Color([255, 255, 255]),
    ..CutSettings::default()
};
let cut = generate_cut(&image, size, &settings)?;

for cut_loop in &cut.loops {
    for node in &cut_loop.nodes { /* node.anchor, node.in_handle, node.out_handle */ }
}
let svg_d = cut.to_svg_path_data();          // path data only
let svg = cut.to_svg_document();              // standalone SVG
let lines = cut.polylines(0.5);               // flattened, ~0.5 pt steps
let clean = narwhal_picture_trace::remove_background(&image, &settings)?; // art with background removed
```

The other ways to size art are `ArtworkSize::points`, `from_dpi`, `from_width_inches` and
`from_height_inches` (the last two measure the whole image, padding included), plus
`from_artwork_height_inches`.

### Settings and defaults

| Field | Default | Meaning |
|---|---|---|
| `offset_pt` | 3.6 (0.05 in) | Distance from art to cut; negative cuts inside the art |
| `tolerance_pt` | 0.205 | Maximum curve-fitting error (set by `with_smoothness`) |
| `keep_inner_cuts` | `true` | Also cut holes inside the art |
| `ignore_holes_under_pt2` | 362.88 (0.07 in²) | Smaller holes are not cut |
| `background` | `Auto` | `Auto`, `TransparencyOnly` or `Color([r, g, b])` |
| `background_tolerance_percent` | 12 | Colour match tolerance, 0–60 |
| `outline_smoothness` | 0.4 | 0 (Detailed) – 1 (Smooth); also rounds bridges between joined islands |

`Auto` detects a solid border colour, as when an image is opened.
In every mode, transparent pixels and a near-uniform opaque border region connected to
the image edge count as background.

Errors are returned as `CutError`, never as a panic: an invalid image, a non-positive size,
no artwork found, or an inset offset that leaves nothing.

### Lower-level steps

`narwhal_picture_trace::pipeline` exposes the individual steps (`foreground_mask`,
`detect_background`, `trace_base`, `apply_vector_offset`, `fit_loop`, …) for callers
that render artwork themselves. They use the VectorCraft geometry types.
Most programs only need `generate_cut`.

## Building

Rust 1.95 or newer:

```sh
cargo build --release -p narwhal-picture-trace     # binary at target/release/narwhal-picture-trace
cargo test -p narwhal-picture-trace
```

It builds for Linux, Windows and macOS. Image resampling uses a bilinear filter.
A caller that renders images with another renderer can see a cut differ by a fraction of
a point along anti-aliased edges.

## Changes

**Outline smoothness and closer fits, bug report mw6vn2tn (2026-11-07).**

- New `CutSettings::with_smoothness(0.0..=1.0)` and CLI `--smoothness 0-100`: 0 (Detailed)
  follows the art closely with more nodes, 1 (Smooth) uses few nodes and rounder curves.
  It sets `tolerance_pt` from 0.1 to 0.6 pt. Before, smoothness only changed how
  separate islands were joined. `--tolerance-pt` still sets an exact value.
- **The default changes** to smoothness 0.4 (tolerance 0.205 pt, was 0.576 pt). Cuts
  follow the art about twice as closely, with roughly 1.5-2x the nodes. Use
  `with_smoothness(1.0)` / `--smoothness 100` for output close to the old one.
- Straight edges stay exact lines at every smoothness (the straight-edge test no longer
  depends on the tolerance).
- The staircase smoothing no longer leaves pixel steps unsmoothed beside corners, which
  a tight fit traced as a zigzag at sharp tips.

**Fix for bug report mw5rege5 (2026-11-07).** Cuts now follow artwork that has long
straight edges next to curves.

- `fit_loop` (in `pipeline.rs`) now gives the curve fitter a sample at every pixel step.
  Before, a long straight edge reached it as only its two end corners, so the cut could
  bulge off the art where that edge met a curve (by up to 1.8 pt on the reported image).
- The curve fitter (`narwhal-trace-core/src/contour.rs`) now fits long straight edges, 160 pixel
  steps or more within about a pixel of a line, as exact straight segments, with smooth
  joins to the curves beside them. Flat edges such as a sticker's base no longer wobble
  by the full tolerance. On the reported image the base varied by 2.4 px before and
  0.44 px after.

The settings, API and output format are unchanged. Cuts can have a few more or fewer
nodes than before.

## Dependencies and licences

- `narwhal-trace-core`: the mask and curve-fitting code, with no dependencies.
- `vectorcraft-geom`, `vectorcraft-pathops`, `vectorcraft-trace`: from VectorCraft 0.10.0,
  under MIT OR Apache-2.0 (see `LICENSES/`). They bring in `kurbo`, `linesweeper` and `image`.

`narwhal-trace-core` and `narwhal-picture-trace` are contributed by their author under
Narwhal 3D's license (GPL-3.0-or-later).
