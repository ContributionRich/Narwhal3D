//! `coco-cutline`: write the Coco Production cut for an image as SVG or JSON.
use coco_cutline::{ArtworkSize, Background, Cut, CutSettings, Image, generate_cut};
use serde_json::json;
use std::path::PathBuf;

const USAGE: &str = "Usage: coco-cutline IMAGE [options]

Writes closed cut lines around the artwork in IMAGE (PNG, JPEG, WebP, GIF, TIFF, BMP).

Size (pick one; default --dpi 300):
  --dpi N                    image pixels per inch
  --width-in W               artwork width in inches (keeps aspect ratio)
  --height-in H              artwork height in inches (keeps aspect ratio)
                             (artwork = the image without fully transparent padding,
                             as Coco Production sizes it)

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
  --sample-pixel X Y         print only the #rrggbb colour of pixel (X, Y) (0,0 = top-left)
                             and exit; used to pick the background colour to remove

Units are points (1/72 inch); the origin is the image's top-left corner, y down.";

struct Options {
    input: PathBuf,
    output: Option<PathBuf>,
    cleaned: Option<PathBuf>,
    json: bool,
    dpi: Option<f64>,
    width_in: Option<f64>,
    height_in: Option<f64>,
    sample: Option<(u32, u32)>,
    settings: CutSettings,
}

fn pixel_index(value: Option<&String>, flag: &str) -> Result<u32, String> {
    value
        .and_then(|value| value.parse::<u32>().ok())
        .ok_or_else(|| format!("{flag} needs two whole pixel coordinates"))
}

/// The `#rrggbb` colour of pixel (x, y), y down from the top row.
fn sample_pixel(image: &Image, x: u32, y: u32) -> Result<String, String> {
    if x >= image.width() || y >= image.height() {
        return Err(format!(
            "pixel ({x}, {y}) is outside the {}x{} image",
            image.width(),
            image.height()
        ));
    }
    let at = (y as usize * image.width() as usize + x as usize) * 4;
    let rgba = image.rgba().get(at..at + 3).ok_or("pixel out of range")?;
    Ok(format!("#{:02x}{:02x}{:02x}", rgba[0], rgba[1], rgba[2]))
}

fn number(value: Option<&String>, flag: &str) -> Result<f64, String> {
    value
        .and_then(|value| value.parse::<f64>().ok())
        .filter(|value| value.is_finite())
        .ok_or_else(|| format!("{flag} needs a number"))
}

fn parse(args: &[String]) -> Result<Options, String> {
    let mut input = None;
    let mut options = Options {
        input: PathBuf::new(),
        output: None,
        cleaned: None,
        json: false,
        dpi: None,
        width_in: None,
        height_in: None,
        sample: None,
        settings: CutSettings::default(),
    };
    let mut tolerance = None;
    let mut iter = args.iter();
    while let Some(arg) = iter.next() {
        match arg.as_str() {
            "-h" | "--help" => return Err(String::new()),
            "-o" | "--output" => {
                options.output = Some(iter.next().ok_or("--output needs a file")?.into());
            }
            "--cleaned-image" => {
                options.cleaned = Some(iter.next().ok_or("--cleaned-image needs a file")?.into());
            }
            "--format" => match iter.next().map(String::as_str) {
                Some("svg") => options.json = false,
                Some("json") => options.json = true,
                _ => return Err("--format must be svg or json".into()),
            },
            "--sample-pixel" => {
                let x = pixel_index(iter.next(), arg)?;
                options.sample = Some((x, pixel_index(iter.next(), arg)?));
            }
            "--dpi" => options.dpi = Some(number(iter.next(), arg)?),
            "--width-in" => options.width_in = Some(number(iter.next(), arg)?),
            "--height-in" => options.height_in = Some(number(iter.next(), arg)?),
            "--offset-in" => options.settings.offset_pt = number(iter.next(), arg)? * 72.0,
            "--tolerance-pt" => tolerance = Some(number(iter.next(), arg)?),
            "--no-inner-cuts" => options.settings.keep_inner_cuts = false,
            "--ignore-holes-under-in2" => {
                options.settings.ignore_holes_under_pt2 = number(iter.next(), arg)? * 72.0 * 72.0;
            }
            "--background-tolerance" => {
                options.settings.background_tolerance_percent = number(iter.next(), arg)?;
            }
            "--smoothness" => {
                let smoothness = number(iter.next(), arg)?;
                if !(0.0..=100.0).contains(&smoothness) {
                    return Err("--smoothness must be 0 to 100".into());
                }
                options.settings = options.settings.with_smoothness(smoothness / 100.0);
            }
            "--background" => {
                let value = iter.next().ok_or("--background needs a value")?;
                options.settings.background = match value.as_str() {
                    "auto" => Background::Auto,
                    "transparent" | "none" => Background::TransparencyOnly,
                    hex => Background::Color(parse_hex(hex)?),
                };
            }
            flag if flag.starts_with('-') => return Err(format!("unknown option {flag}")),
            path if input.is_none() => input = Some(PathBuf::from(path)),
            _ => return Err("only one input image is allowed".into()),
        }
    }
    options.input = input.ok_or("missing input image")?;
    if let Some(tolerance) = tolerance {
        if tolerance <= 0.0 {
            return Err("--tolerance-pt must be positive".into());
        }
        options.settings.tolerance_pt = tolerance;
    }
    if [options.dpi, options.width_in, options.height_in]
        .iter()
        .filter(|value| value.is_some())
        .count()
        > 1
    {
        return Err("use only one of --dpi, --width-in and --height-in".into());
    }
    if [options.dpi, options.width_in, options.height_in]
        .iter()
        .flatten()
        .any(|value| *value <= 0.0)
    {
        return Err("sizes must be positive".into());
    }
    Ok(options)
}

fn parse_hex(value: &str) -> Result<[u8; 3], String> {
    let hex = value.strip_prefix('#').unwrap_or(value);
    let channel = |range: std::ops::Range<usize>| {
        hex.get(range)
            .and_then(|digits| u8::from_str_radix(digits, 16).ok())
    };
    match (hex.len(), channel(0..2), channel(2..4), channel(4..6)) {
        (6, Some(r), Some(g), Some(b)) => Ok([r, g, b]),
        _ => Err(format!(
            "--background must be auto, transparent or #rrggbb, not {value}"
        )),
    }
}

fn to_json(cut: &Cut) -> String {
    let point = |p: coco_cutline::Point| json!([p.x, p.y]);
    json!({
        "units": "pt",
        "points_per_inch": 72,
        "artwork": {"width": cut.artwork.width_pt, "height": cut.artwork.height_pt},
        "bounds": cut.bounds(),
        "unified_join_pt": cut.unified_join_pt,
        "svg_path": cut.to_svg_path_data(),
        "loops": cut.loops.iter().map(|cut_loop| json!({
            "closed": true,
            "nodes": cut_loop.nodes.iter().map(|node| json!({
                "anchor": point(node.anchor),
                "in": point(node.in_handle),
                "out": point(node.out_handle),
                "corner": node.corner,
            })).collect::<Vec<_>>(),
        })).collect::<Vec<_>>(),
    })
    .to_string()
}

fn run(args: &[String]) -> Result<(), String> {
    let options = parse(args)?;
    let bytes = std::fs::read(&options.input)
        .map_err(|e| format!("cannot read {}: {e}", options.input.display()))?;
    let image = Image::decode(&bytes)?;
    if let Some((x, y)) = options.sample {
        println!("{}", sample_pixel(&image, x, y)?);
        return Ok(());
    }
    let size = match (options.dpi, options.width_in, options.height_in) {
        (_, Some(width), _) => ArtworkSize::from_artwork_width_inches(&image, width),
        (_, _, Some(height)) => ArtworkSize::from_artwork_height_inches(&image, height),
        (dpi, _, _) => ArtworkSize::from_dpi(&image, dpi.unwrap_or(300.0)),
    };
    let cut = generate_cut(&image, size, &options.settings)?;
    let text = if options.json {
        to_json(&cut)
    } else {
        cut.to_svg_document()
    };
    match &options.output {
        Some(path) => std::fs::write(path, text)
            .map_err(|e| format!("cannot write {}: {e}", path.display()))?,
        None => print!("{text}"),
    }
    if let Some(path) = &options.cleaned {
        let cleaned = coco_cutline::remove_background(&image, &options.settings)?;
        image::save_buffer(
            path,
            cleaned.rgba(),
            cleaned.width(),
            cleaned.height(),
            image::ExtendedColorType::Rgba8,
        )
        .map_err(|e| format!("cannot write {}: {e}", path.display()))?;
    }
    let (art_width, art_height) = image
        .visible_box()
        .map_or((image.width(), image.height()), |(_, _, w, h)| (w, h));
    eprintln!(
        "coco-cutline: {} loop(s) for {:.3} x {:.3} in artwork",
        cut.loops.len(),
        size.width_pt / f64::from(image.width()) * f64::from(art_width) / 72.0,
        size.height_pt / f64::from(image.height()) * f64::from(art_height) / 72.0
    );
    Ok(())
}

fn main() -> std::process::ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match run(&args) {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(error) if error.is_empty() => {
            println!("{USAGE}");
            std::process::ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("coco-cutline: {error}\n\n{USAGE}");
            std::process::ExitCode::from(2)
        }
    }
}

#[cfg(test)]
mod sample_tests {
    use super::*;

    #[test]
    fn sample_pixel_reads_the_row_from_the_top() {
        // 2x2: top row red, green; bottom row blue, white.
        let rgba = vec![
            255, 0, 0, 255, 0, 255, 0, 255, //
            0, 0, 255, 255, 255, 255, 255, 255,
        ];
        let image = Image::new(2, 2, rgba).ok();
        let image = image.as_ref();
        assert_eq!(image.map(|i| sample_pixel(i, 1, 0)), Some(Ok("#00ff00".into())));
        assert_eq!(image.map(|i| sample_pixel(i, 0, 1)), Some(Ok("#0000ff".into())));
        assert!(image.map(|i| sample_pixel(i, 2, 0).is_err()).unwrap_or(false));
    }
}
