use super::*;
use tracedraw_core::{
    document::{Layer, Page, ParagraphStyle},
    geometry::Size,
};

fn doc_with(shapes: Vec<Shape>) -> Document {
    let mut doc = Document::new("w", Size::new(200.0, 100.0));
    doc.pages[0].layers[0].name = "Ink".into();
    doc.pages[0].layers[0].shapes = shapes;
    doc
}

#[test]
fn rectangle_ellipse_curve_and_text_round_trip() {
    let mut doc = Document::new("w", Size::new(200.0, 100.0));
    let mut ids = doc.ids().clone();
    let mut r = Shape::new(
        ids.shape(),
        ShapeKind::Rect {
            rect: Rect::new(10.0, 20.0, 60.0, 50.0),
            radius: 2.0,
            corners: None,
        },
    );
    r.fill = Fill::Solid(Color::rgb8(255, 0, 0));
    let mut st = Stroke::new(Color::rgb8(0, 0, 255), 1.5);
    st.cap = LineCap::Round;
    st.join = LineJoin::Bevel;
    st.dash = vec![3.0, 1.0];
    st.scale_with_object = true;
    r.stroke = Some(st.clone());
    r.name = Some("box".into());
    r.opacity = 0.5;
    let mut e = Shape::new(
        ids.shape(),
        ShapeKind::Ellipse {
            rect: Rect::new(100.0, 10.0, 140.0, 40.0),
            arc: None,
        },
    );
    e.fill = Fill::Fountain(tracedraw_core::Fountain::two(
        FountainKind::Radial,
        Color::BLACK,
        Color::WHITE,
        0.0,
    ));
    e.stroke = None;
    let mut p = BezPath::new();
    p.move_to((10.0, 60.0));
    p.curve_to((20.0, 90.0), (40.0, 90.0), (50.0, 60.0));
    p.line_to((30.0, 55.0));
    p.close_path();
    let mut c = Shape::new(
        ids.shape(),
        ShapeKind::Path {
            path: p,
            closed: true,
        },
    );
    c.fill = Fill::Solid(Color::cmyk_pct(0.0, 100.0, 100.0, 0.0));
    c.stroke = Some(Stroke::hairline(Color::BLACK));
    c.transform = Affine::translate((100.0, 0.0));
    let mut t = Shape::new(
        ids.shape(),
        ShapeKind::Text {
            spans: vec![{
                let mut s = TextSpan::new("Hello", "Verdana", 24.0);
                s.bold = true;
                s
            }],
            origin: Point::new(20.0, 80.0),
            frame: None,
            align: TextAlign::Left,
            para: ParagraphStyle::default(),
            on_path: None,
        },
    );
    t.fill = Fill::Solid(Color::rgb8(0, 128, 0));
    t.stroke = None;
    doc.pages[0].layers[0].name = "Ink".into();
    doc.pages[0].layers[0].shapes = vec![r, e, c, t];
    doc.set_ids(ids);
    for v in versions() {
        shapes_round_trip(&doc, v);
    }
}

fn shapes_round_trip(doc: &Document, v: u16) {
    let bytes = document_to_cdr_version(doc, v);
    let (back, rep) = crate::open_bytes(&bytes, "back").expect("reads back");
    assert_eq!(rep.version.map(|v| v.0), Some(v));
    assert_eq!(rep.skipped_objects, 0, "{v}: {:?}", rep.warnings);
    assert_eq!(back.pages.len(), 1);
    let page = &back.pages[0];
    assert!((page.size.width - 200.0).abs() < 0.01 && (page.size.height - 100.0).abs() < 0.01);
    assert_eq!(page.layers.len(), 1);
    assert_eq!(page.layers[0].name, "Ink");
    let shapes = &page.layers[0].shapes;
    assert_eq!(shapes.len(), 4, "{v}: {:?}", rep.warnings);
    // Order and geometry.
    let rb = shapes[0].bounds();
    assert!(
        (rb.x0 - 10.0).abs() < 0.01 && (rb.y1 - 50.0).abs() < 0.01,
        "{v}: {rb:?}"
    );
    if v < 15 {
        assert!(matches!(shapes[0].kind, ShapeKind::Rect { .. }), "{v}");
        if let ShapeKind::Rect { radius, .. } = shapes[0].kind {
            assert!((radius - 2.0).abs() < 0.01, "{v}");
        }
    } else {
        // Later rectangles are written as the curve they draw.
        assert!(
            matches!(shapes[0].kind, ShapeKind::Path { closed: true, .. }),
            "{v}"
        );
        let area = shapes[0].page_path().area().abs();
        let rounded = 50.0 * 30.0 - (4.0 - std::f64::consts::PI) * 4.0;
        assert!((area - rounded).abs() < 0.5, "{v}: {area}");
    }
    assert_eq!(shapes[0].fill, Fill::Solid(Color::rgb8(255, 0, 0)), "{v}");
    let s0 = shapes[0].stroke.as_ref().expect("stroke");
    assert_eq!(s0.color, Color::rgb8(0, 0, 255));
    assert!((s0.width - 1.5).abs() < 0.01);
    assert_eq!(s0.cap, LineCap::Round);
    assert_eq!(s0.join, LineJoin::Bevel);
    assert_eq!(s0.dash, vec![3.0, 1.0]);
    assert!(s0.scale_with_object, "{v}");
    assert_eq!(shapes[0].name.as_deref(), Some("box"), "{v}");
    assert!((shapes[0].opacity - 0.5).abs() < 0.01, "{v}");
    let eb = shapes[1].bounds();
    assert!(
        (eb.x0 - 100.0).abs() < 0.01 && (eb.y0 - 10.0).abs() < 0.01,
        "{eb:?}"
    );
    assert!(
        matches!(shapes[1].fill, Fill::Fountain(_)),
        "{v}: {:?}",
        shapes[1].fill
    );
    if let Fill::Fountain(f) = &shapes[1].fill {
        assert_eq!(f.kind, FountainKind::Radial, "{v}");
        assert_eq!(f.stops.len(), 2, "{v}");
        assert_eq!(f.stops[1].color, Color::WHITE, "{v}");
    }
    assert!(shapes[1].stroke.is_none());
    let cb = shapes[2].bounds();
    assert!(
        (cb.x0 - 110.0).abs() < 0.05 && (cb.x1 - 150.0).abs() < 0.05,
        "{cb:?}"
    );
    assert!(matches!(
        shapes[2].kind,
        ShapeKind::Path { closed: true, .. }
    ));
    assert_eq!(
        shapes[2].fill,
        Fill::Solid(Color::cmyk_pct(0.0, 100.0, 100.0, 0.0))
    );
    assert!((shapes[2].stroke.as_ref().expect("hairline").width - Stroke::HAIRLINE).abs() < 1e-9);
    match &shapes[3].kind {
        ShapeKind::Text { spans, .. } => {
            assert_eq!(spans.len(), 1, "{v}: {spans:?}");
            assert_eq!(spans[0].text, "Hello", "{v}");
            assert_eq!(spans[0].font_family, "Verdana", "{v}");
            assert!((spans[0].size_pt - 24.0).abs() < 0.01, "{v}");
            assert!(spans[0].bold, "{v}");
        }
        k => panic!("{v}: {k:?}"),
    }
    let o = shapes[3].transform * Point::ZERO;
    let origin = match &shapes[3].kind {
        ShapeKind::Text { origin, .. } => *origin,
        _ => unreachable!(),
    };
    let at = shapes[3].transform * origin;
    assert!(
        (at.x - 20.0).abs() < 0.05 && (at.y - 80.0).abs() < 0.05,
        "{v}: {o:?} {at:?}"
    );
    assert_eq!(shapes[3].fill, Fill::Solid(Color::rgb8(0, 128, 0)), "{v}");
}

#[test]
fn groups_bitmaps_and_several_pages_round_trip() {
    let mut doc = doc_with(Vec::new());
    let mut ids = doc.ids().clone();
    let mut a = Shape::new(
        ids.shape(),
        ShapeKind::Rect {
            rect: Rect::new(0.0, 0.0, 10.0, 10.0),
            radius: 0.0,
            corners: None,
        },
    );
    a.fill = Fill::Solid(Color::BLACK);
    let mut b = a.clone();
    b.id = ids.shape();
    b.transform = Affine::translate((20.0, 0.0));
    let mut g = Shape::new(
        ids.shape(),
        ShapeKind::Group {
            children: vec![a, b],
        },
    );
    g.transform = Affine::translate((5.0, 5.0));
    let png = {
        let mut pm = tiny_skia::Pixmap::new(2, 2).expect("pixmap");
        let px = pm.pixels_mut();
        px[0] = tiny_skia::ColorU8::from_rgba(255, 0, 0, 255).premultiply();
        px[1] = tiny_skia::ColorU8::from_rgba(0, 255, 0, 255).premultiply();
        px[2] = tiny_skia::ColorU8::from_rgba(0, 0, 255, 255).premultiply();
        px[3] = tiny_skia::ColorU8::from_rgba(255, 255, 255, 255).premultiply();
        pm.encode_png().expect("png")
    };
    let mut bm = Shape::new(
        ids.shape(),
        ShapeKind::Bitmap {
            rect: Rect::new(100.0, 50.0, 140.0, 90.0),
            width_px: 2,
            height_px: 2,
            png,
            fx: None,
        },
    );
    bm.fill = Fill::None;
    bm.stroke = None;
    doc.pages[0].layers[0].shapes = vec![g, bm];
    let pid = ids.page();
    let lid = ids.layer();
    let mut second = Page {
        id: pid,
        name: "Back".into(),
        size: Size::new(200.0, 100.0),
        layers: vec![Layer::new(lid, "Second")],
        guides: Vec::new(),
        background: None,
    };
    let mut c = Shape::new(
        ids.shape(),
        ShapeKind::Ellipse {
            rect: Rect::new(10.0, 10.0, 30.0, 30.0),
            arc: None,
        },
    );
    c.fill = Fill::Solid(Color::rgb8(1, 2, 3));
    second.layers[0].shapes.push(c);
    doc.pages.push(second);
    doc.set_ids(ids);
    for v in versions() {
        groups_bitmaps_and_pages(&doc, v);
    }
}

fn groups_bitmaps_and_pages(doc: &Document, v: u16) {
    let bytes = document_to_cdr_version(doc, v);
    let (back, rep) = crate::open_bytes(&bytes, "back").expect("reads back");
    assert_eq!(back.pages.len(), 2, "{v}: {:?}", rep.warnings);
    assert_eq!(rep.skipped_objects, 0, "{:?}", rep.warnings);
    let l = &back.pages[0].layers[0];
    assert_eq!(l.shapes.len(), 2, "{v}: {:?}", rep.warnings);
    match &l.shapes[0].kind {
        ShapeKind::Group { children } => {
            assert_eq!(children.len(), 2);
            let gb = l.shapes[0].bounds();
            assert!(
                (gb.x0 - 5.0).abs() < 0.01 && (gb.x1 - 35.0).abs() < 0.01,
                "{gb:?}"
            );
        }
        k => panic!("{k:?}"),
    }
    match &l.shapes[1].kind {
        ShapeKind::Bitmap {
            width_px,
            height_px,
            png,
            ..
        } => {
            assert_eq!((*width_px, *height_px), (2, 2));
            let pm = tiny_skia::Pixmap::decode_png(png).expect("png");
            let p = pm.pixels();
            assert_eq!((p[0].red(), p[0].green(), p[0].blue()), (255, 0, 0));
            assert_eq!((p[3].red(), p[3].green(), p[3].blue()), (255, 255, 255));
        }
        k => panic!("{k:?}"),
    }
    let bb = l.shapes[1].bounds();
    assert!(
        (bb.x0 - 100.0).abs() < 0.01 && (bb.y1 - 90.0).abs() < 0.01,
        "{bb:?}"
    );
    assert_eq!(back.pages[1].layers[0].name, "Second");
    assert_eq!(back.pages[1].layers[0].shapes.len(), 1);
}

#[test]
fn a_clipped_bitmap_round_trips_as_a_crop_path() {
    let mut doc = doc_with(Vec::new());
    let mut ids = doc.ids().clone();
    let png = {
        let mut pm = tiny_skia::Pixmap::new(4, 4).expect("pixmap");
        pm.fill(tiny_skia::Color::from_rgba8(10, 20, 30, 255));
        pm.encode_png().expect("png")
    };
    let mut bm = Shape::new(
        ids.shape(),
        ShapeKind::Bitmap {
            rect: Rect::new(0.0, 0.0, 100.0, 50.0),
            width_px: 4,
            height_px: 4,
            png,
            fx: None,
        },
    );
    bm.fill = Fill::None;
    bm.stroke = None;
    bm.transform = Affine::translate((20.0, 20.0));
    let mut frame = Shape::new(
        ids.shape(),
        ShapeKind::Rect {
            rect: Rect::new(40.0, 30.0, 80.0, 60.0),
            radius: 0.0,
            corners: None,
        },
    );
    frame.fill = Fill::None;
    frame.stroke = None;
    let mut pc = Shape::new(
        ids.shape(),
        ShapeKind::ClipFrame {
            frame: Box::new(frame),
            contents: vec![bm],
        },
    );
    pc.fill = Fill::None;
    pc.stroke = None;
    doc.pages[0].layers[0].shapes = vec![pc];
    doc.set_ids(ids);
    for v in versions() {
        let bytes = document_to_cdr_version(&doc, v);
        let (back, rep) = crate::open_bytes(&bytes, "back").expect("reads back");
        clipped_bitmap_reads_back(&back, &rep, v);
    }
}

fn clipped_bitmap_reads_back(back: &Document, rep: &crate::ParseReport, v: u16) {
    let l = &back.pages[0].layers[0];
    assert_eq!(l.shapes.len(), 1, "{v}: {:?}", rep.warnings);
    match &l.shapes[0].kind {
        ShapeKind::ClipFrame { frame, contents } => {
            // Frame and contents live in the clip frame's own space.
            let outer = l.shapes[0].transform;
            let fb = (outer * frame.page_path()).bounding_box();
            assert!(
                (fb.x0 - 40.0).abs() < 0.05 && (fb.x1 - 80.0).abs() < 0.05,
                "{fb:?}"
            );
            assert!(
                (fb.y0 - 30.0).abs() < 0.05 && (fb.y1 - 60.0).abs() < 0.05,
                "{fb:?}"
            );
            assert_eq!(contents.len(), 1);
            let cb = (outer * contents[0].page_path()).bounding_box();
            assert!(
                (cb.x0 - 20.0).abs() < 0.05 && (cb.x1 - 120.0).abs() < 0.05,
                "{cb:?}"
            );
        }
        k => panic!("{k:?}"),
    }
}

#[test]
fn written_files_survive_mutation() {
    let mut doc = doc_with(Vec::new());
    let mut ids = doc.ids().clone();
    let mut r = Shape::new(
        ids.shape(),
        ShapeKind::Rect {
            rect: Rect::new(0.0, 0.0, 10.0, 10.0),
            radius: 0.0,
            corners: None,
        },
    );
    r.fill = Fill::Solid(Color::BLACK);
    doc.pages[0].layers[0].shapes = vec![r];
    doc.set_ids(ids);
    for v in [7, 12, 13, 15, 21] {
        let bytes = document_to_cdr_version(&doc, v);
        for cut in (0..bytes.len()).step_by(13) {
            let _ = crate::open_bytes(&bytes[..cut], "cut");
        }
        for i in (0..bytes.len()).step_by(7) {
            let mut m = bytes.clone();
            m[i] ^= 0x5a;
            let _ = crate::open_bytes(&m, "flip");
        }
    }
}

#[test]
fn corners_one_radius_cannot_hold_are_written_as_curves() {
    use tracedraw_core::{CornerKind, Corners};
    let mut doc = doc_with(Vec::new());
    let mut ids = doc.ids().clone();
    let rect = Rect::new(0.0, 0.0, 40.0, 20.0);
    let mut per_corner = Corners::uniform(2.0, CornerKind::Round);
    per_corner.radii[Corners::TOP_LEFT] = 8.0;
    let mut fixed = Corners::uniform(3.0, CornerKind::Round);
    fixed.fixed = true;
    let mut shapes = Vec::new();
    for (corners, scale) in [
        (Corners::uniform(4.0, CornerKind::Scallop), (1.0, 1.0)),
        (per_corner, (1.0, 1.0)),
        (fixed, (2.0, 1.0)),
        (fixed, (2.0, 2.0)),
    ] {
        let mut s = Shape::new(ids.shape(), ShapeKind::rect_with_corners(rect, corners));
        s.transform = Affine::translate((20.0, 20.0)) * Affine::scale_non_uniform(scale.0, scale.1);
        s.fill = Fill::Solid(Color::BLACK);
        shapes.push(s);
    }
    doc.pages[0].layers[0].shapes = shapes.clone();
    doc.set_ids(ids);
    for v in versions() {
        let (back, rep) =
            crate::open_bytes(&document_to_cdr_version(&doc, v), "back").expect("reads back");
        let read = &back.pages[0].layers[0].shapes;
        assert_eq!(read.len(), 4, "{v}: {:?}", rep.warnings);
        for (i, (a, b)) in shapes.iter().zip(read).enumerate() {
            let (aa, ab) = (a.page_path().area().abs(), b.page_path().area().abs());
            assert!((aa - ab).abs() < 0.5, "{v} {i}: {aa} vs {ab}");
            let (ba, bb) = (a.bounds(), b.bounds());
            assert!(
                (ba.x0 - bb.x0).abs() < 0.01 && (ba.y1 - bb.y1).abs() < 0.01,
                "{v} {i}"
            );
        }
        assert!(matches!(read[0].kind, ShapeKind::Path { closed: true, .. }));
        assert!(matches!(read[1].kind, ShapeKind::Path { closed: true, .. }));
        assert!(matches!(read[2].kind, ShapeKind::Path { closed: true, .. }));
        if v < 15 {
            // A fixed radius on an evenly scaled rectangle is still a
            // rectangle.
            assert!(
                matches!(read[3].kind, ShapeKind::Rect { radius, .. } if (radius - 1.5).abs() < 0.01),
                "{v}"
            );
        }
    }
}

#[test]
fn complex_stars_are_written_as_curves() {
    let mut doc = doc_with(Vec::new());
    let mut ids = doc.ids().clone();
    let mut star = Shape::new(
        ids.shape(),
        ShapeKind::Polygon {
            rect: Rect::new(10.0, 10.0, 50.0, 50.0),
            points: 6,
            sharpness: 0.0,
            complex: Some(1),
            twist: 0.0,
        },
    );
    star.fill = Fill::Solid(Color::BLACK);
    doc.pages[0].layers[0].shapes = vec![star.clone()];
    doc.set_ids(ids);
    for v in [7, 12, 16, 22] {
        complex_star_reads_back(&doc, &star, v);
    }
}

fn complex_star_reads_back(doc: &Document, star: &Shape, v: u16) {
    let (back, rep) =
        crate::open_bytes(&document_to_cdr_version(doc, v), "back").expect("reads back");
    let read = &back.pages[0].layers[0].shapes;
    assert_eq!(read.len(), 1, "{:?}", rep.warnings);
    let ShapeKind::Path { path, .. } = &read[0].kind else {
        panic!("{:?}", read[0].kind);
    };
    // Two triangles, as drawn.
    let moves = path
        .elements()
        .iter()
        .filter(|e| matches!(e, tracedraw_core::geometry::PathEl::MoveTo(_)))
        .count();
    assert_eq!(moves, 2);
    let (a, b) = (
        star.page_path().area().abs(),
        read[0].page_path().area().abs(),
    );
    assert!((a - b).abs() < 0.5, "{a} vs {b}");
}

fn text_shape(
    ids: &mut tracedraw_core::id::IdSource,
    spans: Vec<TextSpan>,
    align: TextAlign,
) -> Shape {
    let mut t = Shape::new(
        ids.shape(),
        ShapeKind::Text {
            spans,
            origin: Point::new(0.0, 0.0),
            frame: None,
            align,
            para: tracedraw_core::document::ParagraphStyle::default(),
            on_path: None,
        },
    );
    t.transform = Affine::translate((50.0, 50.0));
    t.fill = Fill::Solid(Color::BLACK);
    t.stroke = None;
    t
}

/// Several runs and lines, each text with its alignment, read back the
/// same from every version.
#[test]
fn runs_lines_and_alignment_round_trip() {
    let mut doc = doc_with(Vec::new());
    let mut ids = doc.ids().clone();
    let mut red = TextSpan::new("Red ", "Georgia", 18.0);
    red.fill = Some(Fill::Solid(Color::rgb8(255, 0, 0)));
    red.italic = true;
    let mut big = TextSpan::new("big\nline two", "Verdana", 30.0);
    big.underline = true;
    let shapes = vec![
        text_shape(&mut ids, vec![red, big], TextAlign::Center),
        text_shape(
            &mut ids,
            vec![TextSpan::new("right", "Arial", 12.0)],
            TextAlign::Right,
        ),
    ];
    doc.pages[0].layers[0].shapes = shapes;
    doc.set_ids(ids);
    for v in versions() {
        let (back, rep) =
            crate::open_bytes(&document_to_cdr_version(&doc, v), "back").expect("reads back");
        let read = &back.pages[0].layers[0].shapes;
        assert_eq!(read.len(), 2, "{v}: {:?}", rep.warnings);
        let ShapeKind::Text { spans, align, .. } = &read[0].kind else {
            panic!("{v}: {:?}", read[0].kind);
        };
        assert_eq!(*align, TextAlign::Center, "{v}");
        let text: String = spans.iter().map(|s| s.text.as_str()).collect();
        assert_eq!(text, "Red big\nline two", "{v}: {spans:?}");
        let first = &spans[0];
        assert_eq!(first.font_family, "Georgia", "{v}");
        assert!(first.italic && !first.underline, "{v}");
        assert_eq!(first.fill, Some(Fill::Solid(Color::rgb8(255, 0, 0))), "{v}");
        let last = spans.last().expect("span");
        assert_eq!(last.font_family, "Verdana", "{v}");
        assert!((last.size_pt - 30.0).abs() < 0.01, "{v}");
        assert!(last.underline && !last.italic, "{v}");
        let ShapeKind::Text { align, .. } = &read[1].kind else {
            panic!("{v}: {:?}", read[1].kind);
        };
        assert_eq!(*align, TextAlign::Right, "{v}");
    }
}

/// Text fitted to a path goes out as the outline of its letters.
#[test]
fn text_on_a_path_is_written_as_its_outline() {
    let mut doc = doc_with(Vec::new());
    let mut ids = doc.ids().clone();
    let mut t = text_shape(
        &mut ids,
        vec![TextSpan::new("arc", "Arial", 12.0)],
        TextAlign::Left,
    );
    let mut arc = BezPath::new();
    arc.move_to((0.0, 0.0));
    arc.curve_to((10.0, 20.0), (30.0, 20.0), (40.0, 0.0));
    if let ShapeKind::Text { on_path, .. } = &mut t.kind {
        *on_path = Some(tracedraw_core::TextOnPath {
            path: arc,
            offset: 0.0,
            distance: 0.0,
            mirror: false,
        });
    }
    doc.pages[0].layers[0].shapes = vec![t];
    doc.set_ids(ids);
    for v in [7, 15, 21] {
        let (back, rep) =
            crate::open_bytes(&document_to_cdr_version(&doc, v), "back").expect("reads back");
        let read = &back.pages[0].layers[0].shapes;
        assert_eq!(read.len(), 1, "{v}: {:?}", rep.warnings);
        assert!(
            matches!(read[0].kind, ShapeKind::Path { .. }),
            "{v}: {:?}",
            read[0].kind
        );
        assert_eq!(read[0].fill, Fill::Solid(Color::BLACK), "{v}");
    }
}

/// The chunks around one rectangle and one text, as files of the version
/// have them.
fn chunk_ids(bytes: &[u8]) -> (Vec<String>, crate::riff::Tree, Vec<u8>) {
    let c = crate::container::detect(bytes).expect("container");
    let riff = crate::container::riff_stream(bytes, &c).expect("riff");
    let ext = crate::container::external_streams(bytes, &c);
    let tree = crate::riff::parse_with_externals(&riff, ext, c.version.0).expect("tree");
    let mut ids = Vec::new();
    tree.root.walk(&mut |ch, depth| {
        let name = match ch.list_type_str() {
            Some(t) => format!("{}{}", "  ".repeat(depth), t),
            None => format!("{}{}", "  ".repeat(depth), ch.id_str()),
        };
        ids.push(name);
    });
    (ids, tree, riff)
}

#[test]
fn files_are_laid_out_as_the_versions_have_them() {
    let mut doc = doc_with(Vec::new());
    let mut ids = doc.ids().clone();
    let mut r = Shape::new(
        ids.shape(),
        ShapeKind::Rect {
            rect: Rect::new(0.0, 0.0, 10.0, 10.0),
            radius: 0.0,
            corners: None,
        },
    );
    r.fill = Fill::Solid(Color::BLACK);
    let t = text_shape(
        &mut ids,
        vec![TextSpan::new("Hi", "Arial", 12.0)],
        TextAlign::Left,
    );
    doc.pages[0].layers[0].shapes = vec![r, t];
    doc.set_ids(ids);
    for v in versions() {
        let bytes = document_to_cdr_version(&doc, v);
        // Containers: RIFF, then ZIP with the RIFF stream, then ZIP with
        // the chunk tree and data files.
        match v {
            7..=13 => assert_eq!(&bytes[..4], b"RIFF", "{v}"),
            _ => assert_eq!(&bytes[..4], b"PK\x03\x04", "{v}"),
        }
        if v >= 14 {
            let z = zip::ZipArchive::new(std::io::Cursor::new(&bytes)).expect("zip");
            let names: Vec<&str> = z.file_names().collect();
            if v < 16 {
                // The RIFF stream first and stored; without pictures the
                // summary, names and link list follow.
                assert_eq!(
                    names,
                    [
                        "content/riffData.cdr",
                        "metadata/metadata.xml",
                        "metadata/textinfo.xml",
                        "links.xml"
                    ],
                    "{v}"
                );
            } else {
                for n in [
                    "content/root.dat",
                    "content/dataFileList.dat",
                    "content/data/data1.dat",
                    "content/data/masterPage.dat",
                    "content/data/page1.dat",
                ] {
                    assert!(names.contains(&n), "{v}: {names:?}");
                }
            }
        }
        let (ids, tree, riff) = chunk_ids(&bytes);
        let form = Ver(v).form();
        assert_eq!(tree.root.list_type, Some(form), "{v}");
        assert_eq!(&riff[8..12], &form, "{v}");
        let at = |name: &str| ids.iter().position(|i| i.trim() == name);
        // Version chunks, then the document tables, the master page and
        // the page.
        if v >= 16 {
            assert_eq!(ids[1].trim(), "fver", "{v}");
        }
        assert!(at("vrsn") < at("doc"), "{v}: {ids:?}");
        assert!(
            at("mcfg") < at("ptrt") && at("ptrt") < at("filt"),
            "{v}: {ids:?}"
        );
        assert!(
            at("filc").is_some() && at("otlt").is_some() && at("stlt").is_some(),
            "{v}"
        );
        let pages = ids.iter().filter(|i| i.trim() == "page").count();
        assert_eq!(pages, 2, "{v}: master page and page");
        // A page lists the master's guides and desktop layers, from 14 its
        // own guides, then the drawing's layer and the master's grid.
        let layers = ids.iter().filter(|i| i.trim() == "layr").count();
        assert_eq!(layers, if v >= 14 { 3 + 5 } else { 3 + 4 }, "{v}: {ids:?}");
        // Every object: flags, boxes, static id, attributes with the
        // transform; the text starts with its number and ends with its
        // record.
        let obj = ids.iter().position(|i| i.trim() == "obj").expect("object");
        let next: Vec<&str> = ids[obj + 1..obj + 9].iter().map(|s| s.trim()).collect();
        assert_eq!(
            next,
            ["spnd", "flgs", "bbox", "obbx", "usdn", "lgob", "loda", "ftil"],
            "{v}: the text comes first, front to back"
        );
        assert!(ids.iter().any(|i| i.trim() == "txsm"), "{v}");
        // From 11 the text record is followed by its line breaking tables.
        let txsm = at("txsm").expect("txsm");
        assert_eq!(
            ids.get(txsm + 1).map(|i| i.trim()) == Some("txtj"),
            v >= 11,
            "{v}: {ids:?}"
        );
        let mut flags = Vec::new();
        tree.root.walk(&mut |ch, _| {
            if ch.is(b"flgs") {
                flags.push(tree.data(&riff, ch).to_vec());
            }
        });
        // Text is flagged 9 before version 18, 8 (as other objects) after.
        assert_eq!(flags.contains(&vec![0, 2, 0, 9]), v < 18, "{v}: text flags");
        assert!(flags.contains(&vec![0, 2, 0, 8]), "{v}: object flags");
        assert!(
            flags.iter().any(|f| f.get(2..4) == Some(&[1, 0x90])),
            "{v}: master page"
        );
    }
}

/// `loda` arguments: (type, body).
type Args = Vec<(u32, Vec<u8>)>;

/// The arguments of a `loda` record, in the order its offsets table lists
/// them.
fn loda_args(loda: &[u8]) -> Args {
    let u = |at: usize| {
        loda.get(at..at + 4)
            .and_then(|b| b.try_into().ok())
            .map_or(0, u32::from_le_bytes) as usize
    };
    let (n, offsets, types) = (u(4), u(8), u(12));
    (0..n)
        .map(|i| {
            let ty = u(types + 4 * (n - 1 - i)) as u32;
            let body = loda.get(u(offsets + 4 * i)..u(offsets + 4 * i + 4));
            (ty, body.unwrap_or(&[]).to_vec())
        })
        .collect()
}

/// A layer list's flags and its `loda` arguments.
fn layer_args(
    tree: &crate::riff::Tree,
    riff: &[u8],
    layer: &crate::riff::Chunk,
) -> ([u8; 4], Args) {
    let flags = layer
        .find(b"flgs")
        .and_then(|f| tree.data(riff, f).try_into().ok())
        .unwrap_or([0; 4]);
    let loda = layer
        .find(b"lgob")
        .and_then(|l| l.find(b"loda"))
        .map(|c| tree.data(riff, c))
        .unwrap_or(&[]);
    (flags, loda_args(loda))
}

/// Pages list the master page's guides, desktop and grid layers by the
/// master's GUIDs, as every sample file of versions 14 to 22 does (entries
/// with GUIDs of their own are the likeliest reason earlier files closed
/// the program that opened them; not confirmed yet).
#[test]
fn pages_refer_to_the_master_layers_by_their_guids() {
    let mut doc = doc_with(Vec::new());
    let mut second = doc.pages[0].clone();
    second.layers[0].name = "Second".into();
    doc.pages.push(second);
    for v in versions() {
        let bytes = document_to_cdr_version(&doc, v);
        let (_, tree, riff) = chunk_ids(&bytes);
        let mut pages = Vec::new();
        tree.root.walk(&mut |c, _| {
            if c.is(b"page") {
                pages.push(c);
            }
        });
        assert_eq!(pages.len(), 3, "{v}: the master page and two pages");
        let entries = |page: &crate::riff::Chunk| -> Vec<([u8; 4], Args)> {
            page.find(b"gobj")
                .map(|g| {
                    g.find_all(b"layr")
                        .map(|l| layer_args(&tree, &riff, l))
                        .collect()
                })
                .unwrap_or_default()
        };
        let arg = |args: &[(u32, Vec<u8>)], ty: u32| {
            args.iter().find(|(t, _)| *t == ty).map(|(_, b)| b.clone())
        };
        let master = entries(pages[0]);
        let master_flags: Vec<[u8; 4]> = master.iter().map(|(f, _)| *f).collect();
        assert_eq!(
            master_flags,
            [[0x0a, 0, 1, 0x98], [0x08, 0, 1, 0x98], [0x1a, 1, 1, 0x98]],
            "{v}"
        );
        // The master page closes with an empty object list from 16; pages
        // do not.
        assert_eq!(pages[0].find(b"lgob").is_some(), v >= 16, "{v}");
        let mut own_guids = Vec::new();
        for page in &pages[1..] {
            assert!(page.find(b"lgob").is_none(), "{v}");
            let layers = entries(page);
            let flags: Vec<[u8; 4]> = layers.iter().map(|(f, _)| *f).collect();
            let mut expect = vec![[0x0a, 0, 1, 0x98], [0x08, 0, 1, 0x98]];
            if v >= 14 {
                expect.push([0x0a, 0, 0, 0x98]);
            }
            expect.push([0, 0, 0, 0x98]);
            expect.push([0x1a, 1, 1, 0x98]);
            assert_eq!(flags, expect, "{v}");
            for (f, args) in &layers {
                let guid = arg(args, ARG_GUID);
                let name = arg(args, ARG_NAME);
                match master.iter().find(|(mf, _)| mf == f) {
                    // An entry for a master layer: its GUID, from 14 no name.
                    Some((_, m)) => {
                        assert_eq!(guid, arg(m, ARG_GUID), "{v}: {f:?}");
                        assert_eq!(name.is_none(), v >= 14, "{v}: {f:?}");
                    }
                    // A layer of the page's own: a GUID no other layer has.
                    None => {
                        assert!(name.is_some(), "{v}");
                        if let Some(g) = guid.clone() {
                            assert_eq!(g.len(), 16);
                            assert_eq!(g[7] >> 4, 4, "version 4 GUID");
                            assert_eq!(g[8] >> 6, 2, "variant bits");
                            assert!(!master
                                .iter()
                                .any(|(_, m)| arg(m, ARG_GUID) == Some(g.clone())));
                            assert!(!own_guids.contains(&g), "{v}: GUIDs differ");
                            own_guids.push(g);
                        }
                    }
                }
                assert_eq!(guid.is_some(), v >= 14, "{v}");
                // Guides layers carry 16001 = 2 from 17, every layer 12000.
                if v >= 17 {
                    assert_eq!(arg(args, ARG_PROPS), Some(vec![0; 4]), "{v}");
                }
                assert_eq!(
                    arg(args, ARG_GUIDES_MODE),
                    (v >= 17 && f[0] == 0x0a).then(|| vec![2, 0, 0, 0]),
                    "{v}: {f:?}"
                );
            }
        }
        // It still reads back as two pages of one layer each.
        let (back, _) = crate::open_bytes(&bytes, "t").expect("opens");
        assert_eq!(back.pages.len(), 2, "{v}");
        let names: Vec<&str> = back
            .pages
            .iter()
            .map(|p| p.layers[0].name.as_str())
            .collect();
        assert_eq!(names, ["Ink", "Second"], "{v}");
        assert!(back.pages.iter().all(|p| p.layers.len() == 1), "{v}");
    }
}

/// The style table numbers the entries of all its tables in one sequence,
/// and every style names entries that exist, with the three script fonts
/// filled in, as the sample files of versions 11 to 22 have them (styles
/// naming entry 0 or one id in two tables are the second likeliest reason
/// earlier files closed the program that opened them; not confirmed yet).
#[test]
fn the_style_table_numbers_its_entries_once_and_styles_name_them() {
    let uses = StyleUses {
        text_fill: 7,
        graphic_fill: 8,
        outline: 9,
        font: 3,
    };
    for v in versions() {
        let d = stlt(Ver(v), uses);
        let at = std::cell::Cell::new(0usize);
        let mut u = || {
            let x = d
                .get(at.get()..at.get() + 4)
                .and_then(|b| b.try_into().ok())
                .map_or(u32::MAX, u32::from_le_bytes);
            at.set(at.get() + 4);
            x
        };
        let records = u();
        assert_eq!(records, 5, "{v}");
        let mut ids: Vec<u32> = Vec::new();
        // Fills, outlines: id, 0, record (fills from 13 with a matrix).
        let entry = |n: u32, tail: usize, ids: &mut Vec<u32>, u: &mut dyn FnMut() -> u32| {
            let mut values = Vec::new();
            for _ in 0..n {
                ids.push(u());
                assert_eq!(u(), 0);
                values.push(u());
                for _ in 0..tail / 4 {
                    u();
                }
            }
            values
        };
        let n = u();
        let fills = entry(n, if v >= 13 { 48 } else { 0 }, &mut ids, &mut u);
        assert_eq!(fills, [7, 8], "{v}");
        let n = u();
        assert_eq!(entry(n, 0, &mut ids, &mut u), [9], "{v}");
        // One font: three scripts in font 3 at 12 pt.
        assert_eq!(u(), 1, "{v}");
        ids.push(u());
        let mask: Vec<u32> = (0..if v >= 10 { 5 } else { 3 }).map(|_| u()).collect();
        assert_eq!(mask[0], 0, "{v}");
        assert_eq!(u(), 3, "{v}: font 3, character set 0");
        assert_eq!((u(), u()), (3, 3), "{v}: the other scripts' fonts");
        assert_eq!((u(), u(), u()), (42333, 42333, 42333), "{v}: sizes");
        for _ in 0..if v >= 10 { 3 } else { 1 } {
            assert_eq!(u(), 0x40, "{v}");
        }
        let n = u();
        assert_eq!(
            entry(n, 0, &mut ids, &mut u),
            [1, 2, 3, 4],
            "{v}: alignments"
        );
        // One spacing entry: 100 % lines.
        assert_eq!(u(), 1, "{v}");
        ids.push(u());
        let spacing: Vec<u32> = (0..12).map(|_| u()).collect();
        assert_eq!(spacing[5], 1_000_000, "{v}: line spacing");
        assert_eq!(spacing[2], 0, "{v}: character spacing");
        // One set of decoration lines.
        assert_eq!(u(), 1, "{v}");
        ids.push(u());
        assert_eq!(u(), 0);
        for (pos, thick) in DECORATION_LINES {
            assert_eq!((u(), u() as i32, u() as i32), (0, pos, thick), "{v}");
        }
        // No tabs, bullets, indents, hyphenation or drop caps; set11 from 9.
        for _ in 0..5 {
            assert_eq!(u(), 0, "{v}");
        }
        if v >= 9 {
            assert_eq!(u(), 1, "{v}");
            ids.push(u());
            assert_eq!((u(), u()), (0, 0));
        }
        // One sequence from 1, no id twice.
        let expect: Vec<u32> = (1..=ids.len() as u32).collect();
        assert_eq!(ids, expect, "{v}: entry ids");
        // Styles: four for text and one for graphics, naming entries that
        // exist (never 0).
        for style in 1..=5u32 {
            let sections = u();
            assert_eq!(sections, if style < 5 { 2 } else { 1 }, "{v}");
            assert_eq!(u(), style, "{v}");
            assert_eq!(u(), 0, "{v}: no parent");
            u();
            u();
            let len = u() as usize;
            if v >= 16 {
                assert_eq!(len, 0, "{v}: no names from 16");
            }
            at.set(at.get() + len * if v >= 12 { 2 } else { 1 });
            let refs = if style < 5 {
                6 + usize::from(v >= 9)
            } else {
                2
            };
            for _ in 0..refs {
                let r = u();
                assert!(ids.contains(&r), "{v}: style {style} names entry {r}");
            }
        }
        assert_eq!(at.get(), d.len(), "{v}: nothing after the styles");
        // The reader resolves every style.
        let styles = crate::text::read_stlt(&d, v, &mut |w| panic!("{w}"));
        assert_eq!(styles.len(), 5, "{v}");
        for (id, rec) in &styles {
            let s = &rec.style;
            if *id == GRAPHIC_STYLE_ID {
                assert_eq!((s.fill_id, s.outl_id), (Some(8), Some(9)), "{v}");
            } else {
                assert_eq!(
                    (s.fill_id, s.outl_id, s.font_id),
                    (Some(7), Some(9), Some(3)),
                    "{v}"
                );
                assert!((s.size_pt.unwrap_or(0.0) - 12.0).abs() < 0.01, "{v}");
                assert_eq!(s.line_spacing, Some(1.0), "{v}");
                let code = STYLE_ALIGNS[(*id - STYLE_ID) as usize].1;
                assert_eq!(s.align, Some(code), "{v}");
            }
        }
    }
}

/// Graphics refer to the graphic style (before version 16) and text
/// objects carry numbers of their own, as files have them.
#[test]
fn graphics_refer_to_the_graphic_style_and_texts_are_numbered() {
    let mut doc = doc_with(Vec::new());
    let mut ids = doc.ids().clone();
    let r = Shape::new(
        ids.shape(),
        ShapeKind::Rect {
            rect: Rect::new(0.0, 0.0, 10.0, 10.0),
            radius: 0.0,
            corners: None,
        },
    );
    let texts: Vec<Shape> = (0..3)
        .map(|_| {
            text_shape(
                &mut ids,
                vec![TextSpan::new("Hi", "Arial", 12.0)],
                TextAlign::Left,
            )
        })
        .collect();
    doc.pages[0].layers[0].shapes = std::iter::once(r).chain(texts).collect();
    doc.set_ids(ids);
    for v in versions() {
        let bytes = document_to_cdr_version(&doc, v);
        let (_, tree, riff) = chunk_ids(&bytes);
        let mut spnd = Vec::new();
        let mut styles = Vec::new();
        tree.root.walk(&mut |c, _| {
            if c.is(b"obj ") {
                if let Some(s) = c.find(b"spnd") {
                    spnd.push(tree.data(&riff, s).to_vec());
                }
                let loda = c.find(b"lgob").and_then(|l| l.find(b"loda"));
                if let Some(l) = loda {
                    let d = tree.data(&riff, l);
                    let kind = d.get(16).copied();
                    if let Some((_, s)) = loda_args(d).into_iter().find(|(t, _)| *t == ARG_STYLE) {
                        styles.push((kind, s));
                    }
                }
            }
        });
        let numbers: Vec<Vec<u8>> = (1u32..=3).map(|n| n.to_le_bytes().to_vec()).collect();
        assert_eq!(spnd, numbers, "{v}: one number per text, in order");
        if v < 16 {
            for (kind, s) in &styles {
                let want = if *kind == Some(OBJ_ARTISTIC_TEXT as u8) {
                    style_for(TextAlign::Left)
                } else {
                    GRAPHIC_STYLE_ID
                };
                assert_eq!(s, &want.to_le_bytes().to_vec(), "{v}: kind {kind:?}");
            }
            assert_eq!(styles.len(), 4, "{v}");
        } else {
            assert!(styles.is_empty(), "{v}: style strings from 16");
        }
    }
}

/// Fonts, style table, fill and outline records keep the sizes files of
/// the version have.
#[test]
fn records_have_the_sizes_of_the_versions_files() {
    let s = Stroke::new(Color::BLACK, 0.5);
    for (v, outline, solid, none) in [
        (11u16, 140usize, 36usize, 8usize),
        (14, 192, 76, 22),
        (15, 269, 97, 22),
        (17, 213, 97, 22),
        (21, 213, 97, 22),
        (22, 213, 97, 22),
    ] {
        assert_eq!(outl(Ver(v), 1, Some(&s)).len(), outline, "{v}");
        assert_eq!(
            fild(Ver(v), 1, &Fill::Solid(Color::BLACK)).len(),
            solid,
            "{v}"
        );
        assert_eq!(fild(Ver(v), 1, &Fill::None).len(), none, "{v}");
        assert_eq!(
            font_record(Ver(v), 1, "Arial").len(),
            if v >= 12 { 296 } else { 92 },
            "{v}"
        );
        assert_eq!(
            trfd(Ver(v), Affine::IDENTITY).len(),
            if v >= 13 { 96 } else { 80 }
        );
    }
    // Two-stop gradients: stops of 16 bytes before version 13, 24 in 13
    // and 14, 45 from 15.
    let f = Fill::Fountain(tracedraw_core::Fountain::two(
        FountainKind::Linear,
        Color::BLACK,
        Color::WHITE,
        0.0,
    ));
    let len = |v: u16| fild(Ver(v), 1, &f).len();
    assert_eq!(len(12), 56 + 2 * 16, "version 12");
    assert_eq!(
        len(15) - len(14),
        2 * (45 - 24),
        "a stop grows by 21 bytes in 15"
    );
}

#[test]
fn the_version_21_configuration_record_has_its_fields() {
    let rec = mcfg(Ver(21), 297.0, 210.0);
    assert_eq!(rec.len(), 7944);
    let u32_at = |at: usize| u32::from_le_bytes(rec[at..at + 4].try_into().unwrap_or([0; 4]));
    let i32_at = |at: usize| i32::from_le_bytes(rec[at..at + 4].try_into().unwrap_or([0; 4]));
    let u16_at = |at: usize| u16::from_le_bytes(rec[at..at + 2].try_into().unwrap_or([0; 2]));
    let f64_at = |at: usize| f64::from_le_bytes(rec[at..at + 8].try_into().unwrap_or([0; 8]));
    assert_eq!((u32_at(0), u32_at(4)), (2100, 7936));
    assert_eq!((i32_at(12), i32_at(16)), (2_970_000, 2_100_000));
    assert_eq!(u16_at(22), 1, "landscape");
    assert_eq!((i32_at(670), i32_at(674)), (-1_485_000, -1_050_000));
    assert_eq!((f64_at(678), f64_at(686)), (4.0, 4.0));
    assert_eq!((u16_at(704), u16_at(706)), (2, 2), "millimetres");
    assert_eq!(f64_at(696), 1.0);
    assert_eq!(
        (i32_at(831), i32_at(835), i32_at(839)),
        (50_000, 50_000, 1_000)
    );
    assert!((0..4).all(|k| f64_at(1019 + 8 * k) == 1.0));
    assert_eq!(f64_at(7360), 12.0);
    assert_eq!(&rec[7416..7418], b"I\0");
    // The same layout in version 22; earlier versions keep theirs.
    assert_eq!(mcfg(Ver(22), 210.0, 297.0).len(), 7944);
    assert_eq!(mcfg(Ver(20), 210.0, 297.0).len(), 8323);
    // The page size reads back from it.
    let doc = Document::new("t", tracedraw_core::geometry::Size::new(297.0, 210.0));
    let bytes = document_to_cdr_version(&doc, 21);
    let (back, _) = crate::open_bytes(&bytes, "t").expect("opens");
    assert!((back.pages[0].size.width - 297.0).abs() < 0.01);
}

#[test]
fn chosen_node_types_round_trip_in_the_continuity_bits() {
    use tracedraw_core::nodes::{self, NodeType};
    // Node 1 has aligned handles of equal length (reads as symmetrical) but is
    // chosen as a cusp; node 2 reads as symmetrical and is chosen smooth.
    let mut p = BezPath::new();
    p.move_to((0.0, 0.0));
    p.curve_to((10.0, 10.0), (20.0, 10.0), (30.0, 0.0));
    p.curve_to((40.0, -10.0), (50.0, -10.0), (60.0, 0.0));
    p.curve_to((70.0, 10.0), (80.0, 10.0), (90.0, 0.0));
    let mut doc = Document::new("w", Size::new(200.0, 100.0));
    let mut ids = doc.ids().clone();
    let mut c = Shape::new(
        ids.shape(),
        ShapeKind::Path {
            path: p.clone(),
            closed: false,
        },
    );
    assert_eq!(nodes::node_type(&p, 1), NodeType::Symmetrical);
    c.node_types = [(1, NodeType::Cusp), (2, NodeType::Smooth)].into();
    doc.pages[0].layers[0].shapes = vec![c];
    doc.set_ids(ids);
    for v in versions() {
        let bytes = document_to_cdr_version(&doc, v);
        let (back, rep) = crate::open_bytes(&bytes, "back").expect("reads back");
        let s = &back.pages[0].layers[0].shapes[0];
        assert!(
            matches!(s.kind, ShapeKind::Path { .. }),
            "{v}: {:?}",
            rep.warnings
        );
        assert_eq!(
            s.node_types,
            [(1, NodeType::Cusp), (2, NodeType::Smooth)].into(),
            "{v}"
        );
    }
    // Without chosen types nothing is stored on reading.
    let mut plain = doc.clone();
    plain.pages[0].layers[0].shapes[0].node_types.clear();
    let (back, _) = crate::open_bytes(&document_to_cdr(&plain), "back").expect("reads back");
    assert!(back.pages[0].layers[0].shapes[0].node_types.is_empty());
}

#[test]
fn version_16_and_later_files_are_a_complete_package() {
    use std::io::Read;
    let mut doc = doc_with(Vec::new());
    doc.title = "Pack & go".into();
    let mut ids = doc.ids().clone();
    let mut r = Shape::new(
        ids.shape(),
        ShapeKind::Rect {
            rect: Rect::new(0.0, 0.0, 10.0, 10.0),
            radius: 0.0,
            corners: None,
        },
    );
    r.name = Some("Logo <main>".into());
    doc.pages[0].layers[0].shapes = vec![r];
    doc.set_ids(ids);
    let png = Png {
        data: vec![0x89, b'P', b'N', b'G'],
        width: 256,
        height: 128,
    };
    let previews = Previews {
        thumbnail: Some(png.clone()),
        pages: vec![png],
    };
    for v in [16u16, 17, 21, NEWEST_VERSION] {
        let bytes = document_to_cdr_version_with(&doc, v, &previews);
        let mut z = zip::ZipArchive::new(std::io::Cursor::new(&bytes)).expect("zip");
        // The media type first, stored, as packages of these versions
        // open with it.
        {
            let mut first = z.by_index(0).expect("first member");
            assert_eq!(first.name(), "mimetype", "{v}");
            assert_eq!(first.compression(), zip::CompressionMethod::Stored, "{v}");
            let mut t = String::new();
            first.read_to_string(&mut t).expect("text");
            assert_eq!(t, PACKAGE_MEDIA_TYPE);
        }
        let mut read = |name: &str| -> String {
            let mut t = String::new();
            z.by_name(name)
                .unwrap_or_else(|_| panic!("{v}: {name}"))
                .read_to_string(&mut t)
                .expect("text");
            t
        };
        let container = read("META-INF/container.xml");
        assert!(container.contains(r#"crl:file-kind="main" full-path="content/root.dat""#));
        assert!(container.contains(r#"full-path="previews/thumbnail.png""#));
        assert!(container.contains(r#"crl:image-height="128" crl:image-width="256" crl:index="1" full-path="previews/page1.png""#));
        let meta = read("META-INF/metadata.xml");
        assert!(meta.contains(&format!(
            "<cdr:CoreVersion>{}</cdr:CoreVersion>",
            u32::from(v) * 100
        )));
        assert!(meta.contains("Pack &amp; go"));
        let names = read("META-INF/textinfo.xml");
        assert!(
            names.contains("<rdf:li>Logo &lt;main&gt;</rdf:li>"),
            "{names}"
        );
        assert!(read("color/color.xml").starts_with("<Color>"));
        assert!(read("META-INF/links.xml").contains("<linkInfo />"));
        let mut png_bytes = Vec::new();
        z.by_name("previews/page1.png")
            .expect("page picture")
            .read_to_end(&mut png_bytes)
            .expect("bytes");
        assert_eq!(png_bytes[1..4], *b"PNG");
        // The drawing still reads back.
        let (back, _) = crate::open_bytes(&bytes, "back").expect("reads");
        assert_eq!(back.pages.len(), 1);
    }
    // Without pictures: no preview members, and the container names the
    // root file only.
    let bytes = document_to_cdr_version(&doc, 17);
    let z = zip::ZipArchive::new(std::io::Cursor::new(&bytes)).expect("zip");
    assert!(!z.file_names().any(|n| n.starts_with("previews/")));
}

#[test]
fn centred_gradients_are_written_from_their_edge_and_read_back() {
    use tracedraw_core::{Fountain, FountainKind, Stop};
    let red = Color::rgb8(255, 0, 0);
    let yellow = Color::rgb8(255, 255, 0);
    for kind in [
        FountainKind::Radial,
        FountainKind::Square,
        FountainKind::Linear,
    ] {
        let f = Fountain {
            kind,
            stops: vec![
                Stop {
                    pos: 0.0,
                    color: red,
                },
                Stop {
                    pos: 0.3,
                    color: Color::rgb8(255, 128, 0),
                },
                Stop {
                    pos: 1.0,
                    color: yellow,
                },
            ],
            angle: 0.0,
            offset: tracedraw_core::geometry::Point::ZERO,
            edge_pad: 0.0,
            ..Default::default()
        };
        let mut mirrored = f.clone();
        crate::mirror_centred_stops(&mut mirrored);
        if kind == FountainKind::Linear {
            assert_eq!(mirrored, f);
        } else {
            // Edge first: yellow at 0, the orange stop 0.7 from the edge.
            assert_eq!(mirrored.stops[0].color, yellow);
            assert!((mirrored.stops[1].pos - 0.7).abs() < 1e-9);
            let mut back = mirrored.clone();
            crate::mirror_centred_stops(&mut back);
            for (x, y) in back.stops.iter().zip(&f.stops) {
                assert_eq!(x.color, y.color);
                assert!((x.pos - y.pos).abs() < 1e-9);
            }
        }
    }
}

#[test]
fn every_version_writes_what_files_of_that_version_hold() {
    use std::io::Read;
    let mut doc = doc_with(Vec::new());
    let mut ids = doc.ids().clone();
    let r = Shape::new(
        ids.shape(),
        ShapeKind::Rect {
            rect: Rect::new(0.0, 0.0, 10.0, 10.0),
            radius: 0.0,
            corners: None,
        },
    );
    doc.pages[0].layers[0].shapes = vec![r];
    doc.set_ids(ids);
    // A 2 x 1 picture: red then blue.
    let mut pm = tiny_skia::Pixmap::new(2, 1).expect("pixmap");
    pm.pixels_mut()[0] = tiny_skia::PremultipliedColorU8::from_rgba(255, 0, 0, 255).expect("red");
    pm.pixels_mut()[1] = tiny_skia::PremultipliedColorU8::from_rgba(0, 0, 255, 255).expect("blue");
    let png = Png {
        data: pm.encode_png().expect("png"),
        width: 2,
        height: 1,
    };
    let previews = Previews {
        thumbnail: Some(png.clone()),
        pages: vec![png],
    };
    for v in versions() {
        let bytes = document_to_cdr_version_with(&doc, v, &previews);
        let (ids, _, _) = chunk_ids(&bytes);
        let has = |name: &str| ids.iter().any(|i| i.trim() == name);
        if v < 14 {
            // RIFF: the picture and the empty keywords and comment after
            // `vrsn`, an empty `uil` list among the document tables.
            assert_eq!(ids[1].trim(), "vrsn", "{v}: {ids:?}");
            assert_eq!(ids[2].trim(), "DISP", "{v}: {ids:?}");
            assert!(has("INFO") && has("IKEY") && has("ICMT"), "{v}");
            assert!(has("uil"), "{v}: {ids:?}");
            let disp = find_chunk(&bytes, b"DISP").expect("DISP");
            let side = if v >= 13 { 128 } else { 96 };
            assert_eq!(u32::from_le_bytes(disp[0..4].try_into().unwrap()), 8);
            assert_eq!(i32::from_le_bytes(disp[8..12].try_into().unwrap()), side);
            let bpp = u16::from_le_bytes(disp[18..20].try_into().unwrap());
            assert_eq!(bpp, if v >= 13 { 24 } else { 8 }, "{v}");
            assert!(!has("pref"));
            continue;
        }
        assert!(!has("DISP") && !has("uil"), "{v}");
        let mut z = zip::ZipArchive::new(std::io::Cursor::new(&bytes)).expect("zip");
        let names: Vec<String> = z.file_names().map(String::from).collect();
        if v < 16 {
            for n in [
                "content/riffData.cdr",
                "metadata/thumbnails/thumbnail.bmp",
                "metadata/thumbnails/page1.bmp",
                "metadata/metadata.xml",
                "metadata/textinfo.xml",
                "links.xml",
            ] {
                assert!(names.iter().any(|m| m == n), "{v}: {n} in {names:?}");
            }
            let mut bmp = Vec::new();
            z.by_name("metadata/thumbnails/page1.bmp")
                .expect("bmp")
                .read_to_end(&mut bmp)
                .expect("bytes");
            assert_eq!(&bmp[..2], b"BM");
            // Bottom-up, B G R: red first, then blue.
            assert_eq!(&bmp[54..60], &[0, 0, 255, 255, 0, 0]);
            assert!(!has("pref"));
        } else {
            for n in ["color/docPalette.xml", "styles/document.cdss"] {
                assert!(names.iter().any(|m| m == n), "{v}: {n}");
            }
            let mut cdss = String::new();
            z.by_name("styles/document.cdss")
                .expect("styles")
                .read_to_string(&mut cdss)
                .expect("text");
            assert!(cdss.contains(&format!("\"version\": {},", u32::from(v) * 100)));
            for style in ["~graphic~", "~artistic~", "~paragraph~", "~artisticmedia~"] {
                assert!(cdss.contains(style), "{v}: {style}");
            }
            // `pref` after `mcfg`.
            let at = |n: &str| ids.iter().position(|i| i.trim() == n);
            assert!(
                at("pref").is_some() && at("mcfg") < at("pref"),
                "{v}: {ids:?}"
            );
        }
    }
}

/// The payload of the first chunk `id` in a RIFF file.
fn find_chunk(bytes: &[u8], id: &[u8; 4]) -> Option<Vec<u8>> {
    let at = bytes.windows(4).position(|w| w == id)?;
    let len = u32::from_le_bytes(bytes.get(at + 4..at + 8)?.try_into().ok()?) as usize;
    bytes.get(at + 8..at + 8 + len).map(<[u8]>::to_vec)
}

#[test]
fn riff_streams_are_compressed_and_end_with_a_summary() {
    let mut doc = doc_with(Vec::new());
    let mut ids = doc.ids().clone();
    let r = Shape::new(
        ids.shape(),
        ShapeKind::Rect {
            rect: Rect::new(10.0, 20.0, 30.0, 50.0),
            radius: 0.0,
            corners: None,
        },
    );
    doc.pages[0].layers[0].shapes = vec![r];
    doc.set_ids(ids);
    for v in versions().into_iter().filter(|v| *v < 16) {
        let bytes = document_to_cdr_version(&doc, v);
        let (_, tree, riff) = chunk_ids(&bytes);
        let top: Vec<String> = tree
            .root
            .children
            .iter()
            .map(|c| {
                c.list_type_str()
                    .unwrap_or_else(|| c.id_str())
                    .trim()
                    .to_string()
            })
            .collect();
        let want: Vec<&str> = if v < 14 {
            vec!["vrsn", "INFO", "cmpr", "cmpr", "sumi"]
        } else {
            vec!["vrsn", "cmpr", "cmpr"]
        };
        // Without a picture there is no `DISP`.
        assert_eq!(top, want, "{v}");
        // The second compressed list holds the document and the pages.
        let second = &tree.root.children[top.len() - if v < 14 { 2 } else { 1 }];
        let inner = second.find(b"cmpr").expect("inflated stream");
        assert!(
            inner.find(b"doc ").is_some() && inner.find(b"page").is_some(),
            "{v}"
        );
        // It reads back with the rectangle.
        let (back, _) = crate::open_bytes(&bytes, "back").expect("reads");
        assert_eq!(back.pages[0].layers[0].shapes.len(), 1, "{v}");
        if v < 14 {
            let sumi = tree.root.find(b"sumi").expect("sumi");
            let d = tree.data(&riff, sumi);
            let len = match v {
                0..=9 => 44,
                10 | 11 => 56,
                _ => 60,
            };
            assert_eq!(d.len(), len, "{v}");
            let word = |i: usize| i32::from_le_bytes(d[i * 4..i * 4 + 4].try_into().unwrap());
            // The rectangle's box from the page centre, in units.
            let size = doc.pages[0].size;
            let (cx, cy) = (size.width / 2.0, size.height / 2.0);
            let u = |mm: f64| (mm * 10_000.0).round() as i32;
            assert_eq!(word(0), u(10.0 - cx), "{v}");
            assert_eq!(word(1), u(50.0 - cy), "{v}");
            assert_eq!(word(2), u(30.0 - cx), "{v}");
            assert_eq!(word(3), u(20.0 - cy), "{v}");
            assert_eq!(word(9), i32::from(v) * 100, "{v}");
        }
    }
}

#[test]
fn a_drawings_cmyk_profile_is_stored_and_named() {
    use std::io::Read;
    let mut doc = doc_with(Vec::new());
    let data =
        tracedraw_core::icc::test_profiles::Builder::new(b"prtr", b"CMYK", b"Lab ", 2).build();
    doc.cmyk_profile = Some(tracedraw_core::DocumentProfile::new(
        "Press & Proof v2",
        data.clone(),
    ));
    let bytes = document_to_cdr_version(&doc, 21);
    let mut z = zip::ZipArchive::new(std::io::Cursor::new(&bytes)).expect("zip");
    let mut color = String::new();
    z.by_name("color/color.xml")
        .expect("colour settings")
        .read_to_string(&mut color)
        .expect("text");
    assert!(
        color.starts_with("<Color><ColorContext><ColorProfile id=\"Cmyk\">Press &amp; Proof v2</ColorProfile><ColorModel>"),
        "{color}"
    );
    let mut stored = Vec::new();
    z.by_name("color/profiles/cmyk/pressproofv2.icc")
        .expect("profile")
        .read_to_end(&mut stored)
        .expect("bytes");
    assert_eq!(stored, data);
    // It comes back with the drawing.
    let (back, _) = crate::open_bytes(&bytes, "back").expect("reads");
    assert_eq!(back.cmyk_profile.map(|p| p.data.len()), Some(data.len()));
}

#[test]
fn txtj_holds_the_three_line_breaking_tables() {
    let t = txtj();
    assert_eq!(t.len(), 528);
    let hex =
        |r: std::ops::Range<usize>| -> String { t[r].iter().map(|b| format!("{b:02x}")).collect() };
    // The table bytes of files of versions 16 to 21.
    assert_eq!(
        hex(0..170),
        concat!(
            "0e000000510000002100250029002c002e003f005d007d00",
            "3f003f003f003f003f003f003f003f003f003f003f003f00",
            "3f003f003f003f003f003f002c002e00b7003a003b003f00",
            "21003f003f003f003f003f003f003f003f00920094002900",
            "3f005d007d003e00bb003f003f003f00b000270022003f00",
            "3f00250089003f003f003f003f003f003f003f003f003f00",
            "3f003f003f003f003f003f003f003f003f003f003f003f00",
            "3f00",
        )
    );
    assert_eq!(
        hex(312..356),
        "14000000240028005b005c007b003f009100930028003f005b007b003c00ab003f003f003f003f0024003f00"
    );
    assert_eq!(hex(420..440), "080000002c002e003f003f003f003f002c002e00");
    // Everything between the tables is zero.
    assert!(t[170..312]
        .iter()
        .chain(&t[356..420])
        .chain(&t[440..])
        .all(|&b| b == 0));
}

#[test]
fn transforms_carry_the_tag_of_their_version() {
    // Files of versions 13 to 17 open each `trfd` record with [1300, 0],
    // files of 18 and later with [1740, 56].
    let mut ids = Document::new("w", Size::new(200.0, 100.0)).ids().clone();
    let doc = doc_with(vec![Shape::new(
        ids.shape(),
        ShapeKind::Rect {
            rect: Rect::new(10.0, 10.0, 40.0, 30.0),
            radius: 0.0,
            corners: None,
        },
    )]);
    for v in 7u16..=22 {
        let bytes = document_to_cdr_version(&doc, v);
        let (_, tree, riff) = chunk_ids(&bytes);
        let mut tags = Vec::new();
        tree.root.walk(&mut |ch, _| {
            if ch.id_str() == "trfd" {
                let d = tree.data(&riff, ch);
                tags.push(d.get(24..32).map(|t| {
                    let w = |i: usize| u32::from_le_bytes([t[i], t[i + 1], t[i + 2], t[i + 3]]);
                    (w(0), w(4))
                }));
            }
        });
        assert!(!tags.is_empty(), "{v}");
        let want = match v {
            18.. => Some((1740, 56)),
            13..=17 => Some((1300, 0)),
            _ => None,
        };
        for t in tags {
            if let Some(w) = want {
                assert_eq!(t, Some(w), "{v}");
            }
        }
    }
}

#[test]
fn curve_nodes_carry_the_marks_of_version_16_files() {
    // A closed square, then an open line: move, three lines, the closing
    // line; move, line.
    let mut c = Buf::default();
    c.u32(7);
    for _ in 0..7 {
        c.i32(0).i32(0);
    }
    c.bytes(&[0x00, 0x40, 0x40, 0x40, 0x48, 0x00, 0x40]);
    let mut coords = c.done();
    mark_nodes(&mut coords);
    assert_eq!(
        &coords[4 + 8 * 7..],
        &[0x0c, 0x44, 0x44, 0x44, 0x48, 0x04, 0x44]
    );
    // Control points keep their byte.
    let mut c = Buf::default();
    c.u32(4);
    for _ in 0..4 {
        c.i32(0).i32(0);
    }
    c.bytes(&[0x00, 0xc0, 0xc0, 0x90]);
    let mut coords = c.done();
    mark_nodes(&mut coords);
    assert_eq!(&coords[4 + 8 * 4..], &[0x04, 0xc0, 0xc0, 0x94]);
}

#[test]
fn version_21_text_records_are_numbered_and_complete() {
    let mut ids = Document::new("w", Size::new(200.0, 100.0)).ids().clone();
    let a = text_shape(
        &mut ids,
        vec![TextSpan::new("Ab", "Arial", 12.0)],
        TextAlign::Left,
    );
    let b = text_shape(
        &mut ids,
        vec![TextSpan::new("Cde", "Arial", 12.0)],
        TextAlign::Left,
    );
    let doc = doc_with(vec![a, b]);
    let bytes = document_to_cdr_version(&doc, 21);
    let (_, tree, riff) = chunk_ids(&bytes);
    let mut records = Vec::new();
    let mut numbers = Vec::new();
    tree.root.walk(&mut |ch, _| {
        if ch.is(b"txsm") {
            records.push(tree.data(&riff, ch).to_vec());
        }
        if ch.is(b"spnd") {
            numbers.push(u32::from_le_bytes(
                tree.data(&riff, ch).try_into().expect("u32"),
            ));
        }
    });
    assert_eq!(records.len(), 2);
    for (t, n) in records.iter().zip(&numbers) {
        // The frame is numbered as its object.
        assert_eq!(&t[45..49], &n.to_le_bytes(), "frame number");
        let s = String::from_utf8_lossy(t);
        assert!(
            s.contains(r#""interLineSpacing":"1000000""#),
            "complete style"
        );
        assert!(s.contains(r#"{"character":{}}"#), "empty run style");
    }
    assert_ne!(numbers[0], numbers[1]);
}

#[test]
fn gradient_options_are_written_as_the_closest_plain_gradient() {
    use tracedraw_core::{Fountain, FountainKind, FountainRepeat};
    let red = Color::rgb8(255, 0, 0);
    let blue = Color::rgb8(0, 0, 255);
    let mut doc = doc_with(Vec::new());
    let mut ids = doc.ids().clone();
    let mut shape_with = |f: Fountain| {
        let mut s = Shape::new(
            ids.shape(),
            ShapeKind::Rect {
                rect: Rect::new(10.0, 10.0, 60.0, 40.0),
                radius: 0.0,
                corners: None,
            },
        );
        s.fill = Fill::Fountain(f);
        s.stroke = None;
        s
    };
    let mut inverted = Fountain::two(FountainKind::Linear, red, blue, 0.0);
    inverted.invert = true;
    let mut mirrored = Fountain::two(FountainKind::Linear, red, blue, 0.0);
    mirrored.repeat = FountainRepeat::Mirror;
    mirrored.repeats = 2;
    let shaped = Fountain::two(FountainKind::Shaped, red, blue, 0.0);
    let plain = Fountain::two(FountainKind::Radial, red, blue, 0.0);
    let shapes = vec![
        shape_with(inverted),
        shape_with(mirrored),
        shape_with(shaped),
        shape_with(plain.clone()),
    ];
    doc.pages[0].layers[0].shapes = shapes;
    doc.set_ids(ids);
    for v in [7u16, 13, 17] {
        let bytes = document_to_cdr_version(&doc, v);
        let (back, _) = crate::open_bytes(&bytes, "back").expect("reads back");
        let fills: Vec<Fountain> = back.pages[0].layers[0]
            .shapes
            .iter()
            .filter_map(|s| match &s.fill {
                Fill::Fountain(f) => Some(f.clone()),
                _ => None,
            })
            .collect();
        assert_eq!(fills.len(), 4, "{v}");
        // Inverted: blue first.
        assert_eq!(fills[0].first_color(), blue, "{v}");
        assert_eq!(fills[0].last_color(), red, "{v}");
        // Mirrored twice: red, blue, red.
        assert_eq!(fills[1].stops.len(), 3, "{v}");
        assert_eq!(fills[1].stops[1].color, blue, "{v}");
        assert!((fills[1].stops[1].pos - 0.5).abs() < 0.01, "{v}");
        assert_eq!(fills[1].last_color(), red, "{v}");
        // Shaped: a square gradient, blue (the inside) at the centre.
        assert_eq!(fills[2].kind, FountainKind::Square, "{v}");
        assert_eq!(fills[2].first_color(), blue, "{v}");
        // A plain gradient comes back as it was written.
        assert_eq!(fills[3].kind, plain.kind, "{v}");
        assert_eq!(fills[3].first_color(), red, "{v}");
        assert!(fills.iter().all(|f| f.runs_once()), "{v}");
    }
}

#[test]
fn records_of_versions_13_to_20_are_the_version_21_one_with_two_blocks() {
    let r21 = mcfg(Ver(21), 500.0, 500.0);
    for v in 13u16..=20 {
        let r = mcfg(Ver(v), 500.0, 500.0);
        assert_eq!(r.len(), 8323, "{v}");
        assert_eq!(
            &r[0..8],
            &[0x14, 0x05, 0, 0, 0x7b, 0x20, 0, 0],
            "{v}: tag and size"
        );
        // The page size and every field up to the first block.
        assert_eq!(&r[8..869], &r21[8..869], "{v}");
        // A scale of 1000 twice, for a metric drawing.
        assert_eq!(&r[869..877], &[0xe8, 3, 0, 0, 0xe8, 3, 0, 0], "{v}");
        assert_eq!(&r[877..890], &r21[869..882], "{v}");
        assert_eq!(&r[1261..], &r21[882..], "{v}");
    }
}

#[test]
fn the_version_12_record_keeps_the_page_and_settings_of_version_13() {
    let r12 = mcfg(Ver(12), 500.0, 500.0);
    let r13 = mcfg(Ver(13), 500.0, 500.0);
    assert_eq!(r12.len(), 8152);
    // No tag: the record opens with 4 zero bytes and the page size.
    assert_eq!(&r12[0..4], &[0, 0, 0, 0]);
    assert_eq!(&r12[4..12], &r13[12..20]);
    // The ruler origin and grid follow, moved by the two added bytes.
    assert_eq!(&r12[19..696], &r13[25..702]);
}

#[test]
fn version_16_text_is_one_byte_a_letter_and_reads_back() {
    let mut ids = Document::new("w", Size::new(200.0, 100.0)).ids().clone();
    let t = text_shape(
        &mut ids,
        vec![TextSpan::new("Kombi 2013 é", "Arial", 12.0)],
        TextAlign::Left,
    );
    let doc = doc_with(vec![t]);
    let bytes = document_to_cdr_version(&doc, 16);
    let (_, tree, riff) = chunk_ids(&bytes);
    let mut record = Vec::new();
    tree.root.walk(&mut |ch, _| {
        if ch.is(b"txsm") {
            record = tree.data(&riff, ch).to_vec();
        }
    });
    // The letters close the record: a u32 count of 12 bytes, the text in
    // Windows-1252 and a zero.
    let tail = &record[record.len() - 17..];
    assert_eq!(&tail[..4], &12u32.to_le_bytes());
    assert_eq!(&tail[4..16], b"Kombi 2013 \xe9");
    let (back, _) = crate::open_bytes(&bytes, "back").expect("reads");
    let text: String = back.pages[0].layers[0]
        .shapes
        .iter()
        .filter_map(|s| match &s.kind {
            ShapeKind::Text { spans, .. } => {
                Some(spans.iter().map(|sp| sp.text.clone()).collect::<String>())
            }
            _ => None,
        })
        .collect();
    assert_eq!(text, "Kombi 2013 é");
}
