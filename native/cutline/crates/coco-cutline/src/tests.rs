use super::*;

type TestResult = Result<(), Box<dyn std::error::Error>>;

/// `size`-pixel image with discs `(cx, cy, r)` of `ink` on `paper`.
fn discs(size: u32, paper: [u8; 4], ink: [u8; 4], shapes: &[(f64, f64, f64)]) -> Image {
    let mut rgba = Vec::with_capacity((size * size * 4) as usize);
    for y in 0..size {
        for x in 0..size {
            let (px, py) = (f64::from(x) + 0.5, f64::from(y) + 0.5);
            let inside = shapes
                .iter()
                .any(|(cx, cy, r)| (px - cx).hypot(py - cy) <= *r);
            rgba.extend_from_slice(if inside { &ink } else { &paper });
        }
    }
    Image::new(size, size, rgba).unwrap_or_else(|_| Image {
        width: 1,
        height: 1,
        rgba: vec![0; 4],
    })
}

const CLEAR: [u8; 4] = [0, 0, 0, 0];
const WHITE: [u8; 4] = [255, 255, 255, 255];
const RED: [u8; 4] = [200, 20, 20, 255];

#[test]
fn disc_on_white_gives_one_round_loop_offset_outward() -> TestResult {
    // 600 px at 300 dpi = 2 in; disc of radius 150 px = 0.5 in = 36 pt.
    let image = discs(600, WHITE, RED, &[(300.0, 300.0, 150.0)]);
    let size = ArtworkSize::from_dpi(&image, 300.0);
    let cut = generate_cut(&image, size, &CutSettings::default())?;
    assert_eq!(cut.loops.len(), 1);
    let [x0, y0, x1, y1] = cut.bounds().ok_or("no bounds")?;
    let expected = 2.0 * (36.0 + 3.6);
    assert!(((x1 - x0) - expected).abs() < 1.0, "width {}", x1 - x0);
    assert!(((y1 - y0) - expected).abs() < 1.0, "height {}", y1 - y0);
    assert!((((x0 + x1) / 2.0) - 72.0).abs() < 0.5);
    assert!(cut.loops[0].nodes.len() < 40, "a disc needs few nodes");
    let svg = cut.to_svg_document();
    assert!(svg.contains("width=\"2in\"") && svg.contains("stroke=\"#ff0000\""));
    Ok(())
}

#[test]
fn offset_and_size_are_physical_not_pixels() -> TestResult {
    let image = discs(400, CLEAR, RED, &[(200.0, 200.0, 100.0)]);
    let settings = CutSettings {
        offset_pt: 0.0,
        ..CutSettings::default()
    };
    for inches in [1.0, 8.0] {
        let cut = generate_cut(
            &image,
            ArtworkSize::from_width_inches(&image, inches),
            &settings,
        )?;
        let [x0, _, x1, _] = cut.bounds().ok_or("no bounds")?;
        let expected = inches * 72.0 / 2.0;
        assert!(
            ((x1 - x0) - expected).abs() < expected * 0.01 + 0.3,
            "{inches} in: {}",
            x1 - x0
        );
    }
    Ok(())
}

#[test]
fn nearby_islands_become_one_unified_cut_and_far_islands_stay_apart() -> TestResult {
    let near = discs(
        600,
        CLEAR,
        RED,
        &[(200.0, 300.0, 90.0), (400.0, 300.0, 90.0)],
    );
    let size = ArtworkSize::from_dpi(&near, 300.0);
    let cut = generate_cut(&near, size, &CutSettings::default())?;
    assert_eq!(cut.loops.len(), 1);
    assert!(cut.unified_join_pt.is_some());

    let far = discs(
        1200,
        CLEAR,
        RED,
        &[(200.0, 600.0, 90.0), (1000.0, 600.0, 90.0)],
    );
    let size = ArtworkSize::from_dpi(&far, 300.0);
    let cut = generate_cut(&far, size, &CutSettings::default())?;
    assert_eq!(cut.loops.len(), 2);
    assert!(cut.unified_join_pt.is_none());
    Ok(())
}

#[test]
fn ring_keeps_or_drops_its_inner_cut() -> TestResult {
    let mut image = discs(600, WHITE, RED, &[(300.0, 300.0, 250.0)]);
    let hole = discs(600, CLEAR, WHITE, &[(300.0, 300.0, 120.0)]);
    for (pixel, hole) in image
        .rgba
        .chunks_exact_mut(4)
        .zip(hole.rgba.chunks_exact(4))
    {
        if hole[3] != 0 {
            pixel.copy_from_slice(hole);
        }
    }
    let size = ArtworkSize::from_dpi(&image, 300.0);
    let with_holes = generate_cut(&image, size, &CutSettings::default())?;
    assert_eq!(with_holes.loops.len(), 2);
    let without = CutSettings {
        keep_inner_cuts: false,
        ..CutSettings::default()
    };
    assert_eq!(generate_cut(&image, size, &without)?.loops.len(), 1);
    Ok(())
}

#[test]
fn background_modes_and_cleaning() -> TestResult {
    let blue = [30, 60, 200, 255];
    let image = discs(300, blue, RED, &[(150.0, 150.0, 60.0)]);
    assert_eq!(
        pipeline::detect_background(image.rgba(), 300, 300)?,
        Some([30, 60, 200])
    );
    let cleaned = remove_background(&image, &CutSettings::default())?;
    assert_eq!(&cleaned.rgba()[..4], &[0, 0, 0, 0]);
    let center = (150 * 300 + 150) * 4;
    assert_eq!(&cleaned.rgba()[center..center + 4], &RED);
    let size = ArtworkSize::from_dpi(&image, 300.0);
    let cut = generate_cut(&image, size, &CutSettings::default())?;
    let [x0, _, x1, _] = cut.bounds().ok_or("no bounds")?;
    assert!(
        x1 - x0 < 40.0,
        "the blue background must not be cut: {}",
        x1 - x0
    );
    Ok(())
}

#[test]
fn invalid_input_is_an_error_not_a_panic() {
    assert!(Image::new(2, 2, vec![0; 3]).is_err());
    assert!(Image::new(0, 2, vec![]).is_err());
    assert!(Image::decode(b"not an image").is_err());
    let empty = discs(50, CLEAR, CLEAR, &[]);
    let size = ArtworkSize::from_dpi(&empty, 300.0);
    assert_eq!(
        generate_cut(&empty, size, &CutSettings::default()),
        Err(CutError::NothingToTrace)
    );
    let ink = discs(50, CLEAR, RED, &[(25.0, 25.0, 10.0)]);
    for size in [
        ArtworkSize::points(0.0, 1.0),
        ArtworkSize::points(f64::NAN, 1.0),
    ] {
        assert_eq!(
            generate_cut(&ink, size, &CutSettings::default()),
            Err(CutError::InvalidSize)
        );
    }
    assert!(pipeline::foreground_mask(&[0; 7], 1, 2, None, 12.0).is_err());
}

#[test]
fn transparent_padding_is_trimmed_but_coordinates_stay_in_the_full_image() -> TestResult {
    // The same disc, alone and with wide transparent padding on the left and top.
    let alone = discs(300, CLEAR, RED, &[(150.0, 150.0, 140.0)]);
    let padded = discs(900, CLEAR, RED, &[(750.0, 750.0, 140.0)]);
    let settings = CutSettings::default();
    let a = generate_cut(&alone, ArtworkSize::from_dpi(&alone, 300.0), &settings)?;
    let b = generate_cut(&padded, ArtworkSize::from_dpi(&padded, 300.0), &settings)?;
    let [ax0, ay0, ax1, ay1] = a.bounds().ok_or("no bounds")?;
    let [bx0, by0, bx1, by1] = b.bounds().ok_or("no bounds")?;
    let shift = 600.0 * 72.0 / 300.0;
    for (padded, alone) in [(bx0, ax0), (by0, ay0), (bx1, ax1), (by1, ay1)] {
        assert!(
            (padded - shift - alone).abs() < 1.0e-6,
            "{padded} vs {alone}"
        );
    }
    Ok(())
}

#[test]
fn artwork_sizes_ignore_transparent_padding() {
    let padded = discs(900, CLEAR, RED, &[(750.0, 750.0, 140.0)]);
    let size = ArtworkSize::from_artwork_width_inches(&padded, 2.0);
    let visible = padded.visible_box().map_or(0, |(_, _, w, _)| w);
    let art_width = size.width_pt / 900.0 * f64::from(visible);
    assert!((art_width - 144.0).abs() < 1.0e-9);
    let size = ArtworkSize::from_artwork_height_inches(&padded, 1.0);
    assert!((size.width_pt - size.height_pt).abs() < 1.0e-9);
}

/// Largest distance (pixels) from the cut to the nearest boundary of `inside`.
fn worst_deviation_px(
    cut: &Cut,
    width: usize,
    height: usize,
    inside: impl Fn(usize, usize) -> bool,
    px_per_pt: f64,
) -> f64 {
    let mut boundary = Vec::new();
    for y in 0..height {
        for x in 0..width {
            if inside(x, y)
                && (x == 0
                    || y == 0
                    || x + 1 == width
                    || y + 1 == height
                    || !inside(x - 1, y)
                    || !inside(x + 1, y)
                    || !inside(x, y - 1)
                    || !inside(x, y + 1))
            {
                boundary.push((x as f64 + 0.5, y as f64 + 0.5));
            }
        }
    }
    cut.polylines(0.25)
        .iter()
        .flatten()
        .map(|p| {
            let (px, py) = (p.x * px_per_pt, p.y * px_per_pt);
            boundary
                .iter()
                .map(|(bx, by)| (bx - px).hypot(by - py))
                .fold(f64::INFINITY, f64::min)
        })
        .fold(0.0, f64::max)
}

/// Bug report mw5rege5: where a long straight edge met a curve (a pineapple on a shelf), the
/// zero-offset cut bowed off the art, because straight boundary runs reached the curve fitter
/// as just their two end corners.
#[test]
fn zero_offset_cut_follows_straight_edges_next_to_curves() -> TestResult {
    // 1500 px at 300 dpi is exactly the processing size, so nothing is resampled.
    let (width, height) = (1500_usize, 1000_usize);
    let inside = |x: usize, y: usize| {
        let (px, py) = (x as f64 + 0.5, y as f64 + 0.5);
        // A shelf with rounded ends, so no corner splits the fit between its straight top
        // edge and the curve around its end (as in the reported artwork).
        let radius = 30.0;
        let cx = px.clamp(100.0 + radius, 1400.0 - radius);
        let cy = py.clamp(800.0 + radius, 900.0 - radius);
        let shelf = (px - cx).hypot(py - cy) <= radius;
        let body = ((px - 700.0) / 420.0).powi(2) + ((py - 420.0) / 420.0).powi(2) <= 1.0;
        shelf || (body && py < 800.0)
    };
    let mut rgba = Vec::with_capacity(width * height * 4);
    for y in 0..height {
        for x in 0..width {
            rgba.extend_from_slice(if inside(x, y) { &RED } else { &CLEAR });
        }
    }
    let image = Image::new(width as u32, height as u32, rgba)?;
    let settings = CutSettings {
        offset_pt: 0.0,
        ..CutSettings::default()
    };
    let cut = generate_cut(&image, ArtworkSize::from_dpi(&image, 300.0), &settings)?;
    assert_eq!(cut.loops.len(), 1);
    let px_per_pt = 300.0 / 72.0;
    let worst = worst_deviation_px(&cut, width, height, inside, px_per_pt);
    // Fitting tolerance plus the half pixel between a boundary pixel's centre and its edge.
    let allowed = settings.tolerance_pt * px_per_pt + 1.0;
    assert!(
        worst <= allowed,
        "cut strays {worst:.2} px from the art (allowed {allowed:.2})"
    );
    Ok(())
}

#[test]
fn polylines_follow_the_loops() -> TestResult {
    let image = discs(300, CLEAR, RED, &[(150.0, 150.0, 100.0)]);
    let cut = generate_cut(
        &image,
        ArtworkSize::from_dpi(&image, 300.0),
        &CutSettings::default(),
    )?;
    let lines = cut.polylines(1.0);
    assert_eq!(lines.len(), 1);
    assert!(lines[0].len() > 60);
    Ok(())
}
