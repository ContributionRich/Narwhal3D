# coco-cutline

The cut generator from **Coco Production**, packaged so another program can use it.
Give it an image and its printed size. It returns closed Bézier cut lines around the
artwork, offset outward, ready for a vinyl, laser or print-and-cut machine.

It is the same code Coco Production runs for **Generate Cut**. Coco Production calls the
`pipeline` module of this crate directly, and its test suite checks that this package and
the app produce the same cut for real artwork at several sizes.

You can use it two ways:

- **Command line** (any language): `coco-cutline image.png --width-in 4 -o cut.svg`
- **Rust library**: `coco_cutline::generate_cut(&image, size, &settings)`

## What it does

1. **Trims** fully transparent padding (as Coco Production does when artwork is opened).
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
coco-cutline IMAGE [options]

Size (pick one; default --dpi 300):
  --dpi N                    image pixels per inch
  --width-in W               artwork width in inches (keeps aspect ratio)
  --height-in H              artwork height in inches (keeps aspect ratio)
                             (artwork = the image without fully transparent padding,
                             as Coco Production sizes it)
Cut:
  --offset-in D              distance from the art to the cut (default 0.05; negative = inside)
  --tolerance-pt T           curve-fitting tolerance in points (default 0.576)
  --no-inner-cuts            do not cut holes inside the art
  --ignore-holes-under-in2 A skip holes smaller than A square inches (default 0.07)
  --background MODE          auto (default), transparent, or a colour like #ffffff
  --background-tolerance P   colour match tolerance in percent, 0-60 (default 12)
  --smoothness P             rounding of bridges joining separate islands, 0-100 (default 60)
Output:
  -o, --output FILE          write here instead of standard output
  --format svg|json          default svg; json lists every Bezier node in points
  --cleaned-image FILE       also write the image with its background removed (PNG)
```

It reads PNG, JPEG, WebP, GIF, TIFF and BMP images up to 64 megapixels. The exit code is 0
on success and 2 on any error, with the reason printed to standard error. A one-line summary
also goes to standard error, so standard output carries only the SVG or JSON.

**SVG output** is sized in inches with a `viewBox` in points, and draws the cut as a red
0.5 pt hairline, as Coco Production does. It opens 1:1 in Illustrator, Inkscape or CorelDRAW.

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
out = subprocess.run(["coco-cutline", "sticker.png", "--width-in", "3", "--format", "json"],
                     check=True, capture_output=True, text=True).stdout
cut = json.loads(out)
```

## Rust library

Add it as a path dependency (it and its dependencies are in this source tree):

```toml
[dependencies]
coco-cutline = { path = "crates/coco-cutline" }
```

```rust
use coco_cutline::{ArtworkSize, Background, CutSettings, Image, generate_cut};

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
let clean = coco_cutline::remove_background(&image, &settings)?; // art with background removed
```

The other ways to size art are `ArtworkSize::points`, `from_dpi`, `from_width_inches` and
`from_height_inches` (the last two measure the whole image, padding included), plus
`from_artwork_height_inches`.

### Settings (defaults match Coco Production)

| Field | Default | Meaning |
|---|---|---|
| `offset_pt` | 3.6 (0.05 in) | Distance from art to cut; negative cuts inside the art |
| `tolerance_pt` | 0.576 | Maximum curve-fitting error |
| `keep_inner_cuts` | `true` | Also cut holes inside the art |
| `ignore_holes_under_pt2` | 362.88 (0.07 in²) | Smaller holes are not cut |
| `background` | `Auto` | `Auto`, `TransparencyOnly` or `Color([r, g, b])` |
| `background_tolerance_percent` | 12 | Colour match tolerance, 0–60 |
| `outline_smoothness` | 0.6 | 0–1, rounding of bridges between joined islands |

`Auto` detects a solid border colour, as Coco Production does when an image is opened.
In every mode, transparent pixels and a near-uniform opaque border region connected to
the image edge count as background.

Errors are returned as `CutError`, never as a panic: an invalid image, a non-positive size,
no artwork found, or an inset offset that leaves nothing.

### Lower-level steps

`coco_cutline::pipeline` exposes the individual steps (`foreground_mask`,
`detect_background`, `trace_base`, `apply_vector_offset`, `fit_loop`, …) that Coco
Production calls with artwork it renders itself. They use the VectorCraft geometry types.
Most programs only need `generate_cut`.

## Building

Rust 1.95 or newer:

```sh
cargo build --release -p coco-cutline     # binary at target/release/coco-cutline
cargo test -p coco-cutline
```

It builds for Linux, Windows and macOS. Image resampling uses a bilinear filter.
Coco Production renders placed images with VectorCraft's renderer instead, so a cut can
differ from the app's by a fraction of a point along anti-aliased edges.

## Dependencies and licences

- `coco-cut`: Coco Production's mask and curve-fitting code, with no dependencies.
- `vectorcraft-geom`, `vectorcraft-pathops`, `vectorcraft-trace`: from VectorCraft 0.10.0,
  under MIT OR Apache-2.0 (see `LICENSES/`). They bring in `kurbo`, `linesweeper` and `image`.

The Coco crates are part of Coco Production and carry no separate licence. Add one before
distributing them outside your own projects.
