//! One-pass Rust PDF and SVG vector serializers (Q117-07/Q117-08).
//!
//! Serializes the complete single-page PDF 1.4 EXACTLY ONCE into a caller-owned
//! buffer, byte-for-byte identical to the Python `vector_export._build_pdf`. The
//! content stream (the dominant cost -- one formatted coordinate per cubic
//! control point) is formatted once, not twice, which is what makes the native
//! route faster than the Python writer. The Python layer owns file I/O and is
//! the complete fallback: library absent, export missing, call error, or output
//! malformed all degrade to the unchanged Python serializer.
//!
//! Byte-exact contract preserved: fixed-4 float formatting (ties-to-even,
//! matching Python `f"{v:.4f}"`), Latin-1 title/layer literal-string escaping
//! (`\` -> `\\`, `(` -> `\(`, `)` -> `\)`), object order (catalog, pages, page,
//! contents, info, then one OCG per layer), the OCG/OCProperties wiring, the
//! exact `stream`/`endstream` framing and `/Length`, and the xref table (true
//! byte offsets, 20-byte entries, `startxref`, `%%EOF`).

const MM_TO_PT: f64 = 72.0 / 25.4;

const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const ERR_SIZE: i32 = -3;

/// Format a value (already in the correct unit -- points for coordinates,
/// 0..1 for colour) with exactly four decimals, matching Python `f"{v:.4f}"`
/// including ties-to-even rounding.
fn f4(v: f64) -> String {
    format!("{:.4}", v)
}

/// Escape a PDF literal string (the content of ``(...)``): backslash and both
/// parentheses. Mirrors `vector_export._pdf_escape` exactly.
fn pdf_escape(text: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(text.len());
    for &b in text {
        match b {
            b'\\' => out.extend_from_slice(b"\\\\"),
            b'(' => out.extend_from_slice(b"\\("),
            b')' => out.extend_from_slice(b"\\)"),
            _ => out.push(b),
        }
    }
    out
}

/// Append a fixed-4 decimal float (already in the right unit) to *out*,
/// matching Python `f"{v:.4f}"` (ties-to-even) via `write!` + `Display`.
/// Writing straight into the single content buffer (no per-line `String`)
/// is what keeps the one-pass serializer fast at 50k+ strokes.
fn f4_into(out: &mut String, v: f64) {
    use std::fmt::Write as _;
    let _ = write!(out, "{:.*}", 4, v);
}

/// Append one operator line (no trailing newline -- the caller inserts `\n`
/// between lines, exactly as Python's `"\n".join(ops)` does).
fn line_rg(out: &mut String, r: f64, g: f64, b: f64) {
    f4_into(out, r);
    out.push(' ');
    f4_into(out, g);
    out.push(' ');
    f4_into(out, b);
    out.push_str(" RG");
}

fn line_w(out: &mut String, width_mm: f64) {
    f4_into(out, width_mm * MM_TO_PT);
    out.push_str(" w");
}

/// One stroke's operator lines (no layer markers), mirroring
/// `vector_export._stroke_ops`, appended to the shared content buffer.
fn line_stroke(
    out: &mut String,
    rgb: &[f64],
    width_mm: f64,
    beziers: &[f64],
    nseg: usize,
    seg_base: usize,
    closed: bool,
) {
    line_rg(out, rgb[0], rgb[1], rgb[2]);
    out.push('\n');
    line_w(out, width_mm);
    if nseg == 0 {
        // Python `_stroke_ops` returns [RG, w] with NO `S` for an empty stroke.
        return;
    }
    let o0 = seg_base * 8;
    out.push('\n');
    f4_into(out, beziers[o0] * MM_TO_PT);
    out.push(' ');
    f4_into(out, beziers[o0 + 1] * MM_TO_PT);
    out.push_str(" m");
    for k in 0..nseg {
        let o = (seg_base + k) * 8;
        out.push('\n');
        f4_into(out, beziers[o + 2] * MM_TO_PT);
        out.push(' ');
        f4_into(out, beziers[o + 3] * MM_TO_PT);
        out.push(' ');
        f4_into(out, beziers[o + 4] * MM_TO_PT);
        out.push(' ');
        f4_into(out, beziers[o + 5] * MM_TO_PT);
        out.push(' ');
        f4_into(out, beziers[o + 6] * MM_TO_PT);
        out.push(' ');
        f4_into(out, beziers[o + 7] * MM_TO_PT);
        out.push_str(" c");
    }
    if closed {
        out.push_str("\nh");
    }
    out.push_str("\nS");
}

/// The content stream for every stroke, mirroring
/// `vector_export._content_ops` (layered or not) then `"\n".join(ops)`. This
/// is the single format pass -- the dominant cost of the serializer -- and it
/// writes into ONE buffer (no per-line allocation, no intermediate `join`).
fn build_content(
    stroke_count: usize,
    seg_counts: &[i64],
    closed: &[i32],
    rgb: &[f64],
    widths: &[f64],
    beziers: &[f64],
    layer_idx: &[Option<usize>],
) -> Vec<u8> {
    let mut out = String::new();
    let mut first = true;
    let mut seg_off = 0usize;
    for s in 0..stroke_count {
        let nseg = seg_counts[s] as usize;
        if !first {
            out.push('\n');
        } else {
            first = false;
        }
        match layer_idx[s] {
            None => line_stroke(
                &mut out,
                &rgb[3 * s..3 * s + 3],
                widths[s],
                beziers,
                nseg,
                seg_off,
                closed[s] != 0,
            ),
            Some(k) => {
                let _ = std::fmt::Write::write_fmt(&mut out, format_args!("/OC /L{} BDC\n", k));
                line_stroke(
                    &mut out,
                    &rgb[3 * s..3 * s + 3],
                    widths[s],
                    beziers,
                    nseg,
                    seg_off,
                    closed[s] != 0,
                );
                out.push_str("\nEMC");
            }
        }
        seg_off += nseg;
    }
    out.into_bytes()
}

/// A validated PDF serialization input (caller-owned slices).
struct PdfInput<'a> {
    beziers: &'a [f64],     // 8 per cubic segment
    seg_counts: &'a [i64],  // per stroke
    closed: &'a [i32],      // per stroke, 0/1
    rgb: &'a [f64],         // 3 per stroke
    widths: &'a [f64],      // 1 per stroke
    layer_concat: &'a [u8], // concatenated layer names
    layer_lens: &'a [i64],  // per stroke, 0 when empty
    title: &'a [u8],
    page_w_mm: f64,
    page_h_mm: f64,
    stroke_count: usize,
}

/// An SVG mm value printed with 4 decimals (SVG coordinates are already in mm,
/// no pt conversion). Matches Python `f"{mm:.4f}"` (both use round-half-to-even
/// on the IEEE-754 double, so the decimal expansion is identical).
fn f4_svg(val: f64) -> String {
    format!("{:.4}", val)
}

/// Escape a value for use inside an XML attribute value. Mirrors
/// `vector_export._xml_escape` exactly: `&` first, then `<`, `>`, `"`. The
/// order matters: replacing `&` first ensures the `&` of `&lt;` is not
/// double-escaped.
fn xml_escape(text: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(text.len() + 8);
    for &b in text {
        match b {
            b'&' => out.extend_from_slice(b"&amp;"),
            b'<' => out.extend_from_slice(b"&lt;"),
            b'>' => out.extend_from_slice(b"&gt;"),
            b'"' => out.extend_from_slice(b"&quot;"),
            _ => out.push(b),
        }
    }
    out
}

/// Round a 0.0–1.0 float to 0–255, matching Python `int(round(x * 255))`
/// (banker's rounding). The result is always an integer in [0, 255].
fn rgb_255(c: f64) -> u32 {
    // Python `round()` is round-half-to-even. Rust's plain `round()` is
    // half-away-from-zero, so use the explicit ties-even operation.
    let v = (c * 255.0).round_ties_even().clamp(0.0, 255.0);
    v as u32
}

/// Build an SVG path `d` for one stroke. Mirrors
/// `vector_export._svg_path`: `M x0 y0 C x1 y1 x2 y2 x3 y3 ... [Z]` with the
/// y-flip `y_svg = H - y` applied to every point, 4-decimal formatting.
/// `seg_base` is the per-stroke offset into the flat `beziers` array (each
/// stroke occupies `seg_count * 8` floats, in the same layout the PDF
/// serializer uses: p0, p1, p2, p3 per segment).
fn svg_path(
    beziers: &[f64],
    seg_base: usize,
    seg_count: usize,
    closed: bool,
    page_h_mm: f64,
) -> String {
    if seg_count == 0 {
        return String::new();
    }
    let h = page_h_mm;
    let xy = |x: f64, y: f64| format!("{} {}", f4_svg(x), f4_svg(h - y));
    let x0 = beziers[seg_base * 8];
    let y0 = beziers[seg_base * 8 + 1];
    let mut parts = Vec::with_capacity(seg_count + 2);
    parts.push(format!("M {}", xy(x0, y0)));
    for s in 0..seg_count {
        let o = (seg_base + s) * 8;
        // beziers[o..o+2] = p0 (the segment's start, already emitted as the
        // previous segment's p3 or the M point); [o+2..o+6] = p1, p2;
        // [o+6..o+8] = p3.
        let p1x = beziers[o + 2];
        let p1y = beziers[o + 3];
        let p2x = beziers[o + 4];
        let p2y = beziers[o + 5];
        let p3x = beziers[o + 6];
        let p3y = beziers[o + 7];
        parts.push(format!(
            "C {} {} {}",
            xy(p1x, p1y),
            xy(p2x, p2y),
            xy(p3x, p3y),
        ));
    }
    if closed {
        parts.push("Z".to_string());
    }
    parts.join(" ")
}

/// One `<path .../>` line for a stroke (two-space indent). Mirrors
/// `vector_export._svg_path_element` byte-for-byte.
fn svg_path_element(
    beziers: &[f64],
    seg_base: usize,
    seg_count: usize,
    closed: bool,
    rgb: &[f64],
    width_mm: f64,
    page_h_mm: f64,
) -> String {
    let d = svg_path(beziers, seg_base, seg_count, closed, page_h_mm);
    format!(
        "  <path d=\"{}\" stroke=\"rgb({},{},{})\" stroke-width=\"{}\" fill=\"none\"/>\n",
        d,
        rgb_255(rgb[0]),
        rgb_255(rgb[1]),
        rgb_255(rgb[2]),
        f4_svg(width_mm),
    )
}

/// Serialize the complete SVG document byte-for-byte, mirroring
/// `vector_export.write_svg`'s body (the bytes it writes to disk). The
/// first-seen layer bucket is computed once (not per-layer scans), and the
/// whole document is built in a single `String` allocation. `seg_base` is
/// tracked as a running offset (the same pattern `build_content` uses) so
/// each stroke's per-segment control points are read from the correct slice
/// of the flat `beziers` array.
fn build_svg(input: &PdfInput) -> Vec<u8> {
    let page_h_mm = input.page_h_mm;
    let page_w_mm = input.page_w_mm;
    let stroke_count = input.stroke_count;
    let (layer_names, layer_idx) =
        discover_layers(stroke_count, input.layer_concat, input.layer_lens);
    let n_layers = layer_names.len();

    // Per-stroke (seg_base, seg_count) so the per-layer scans don't re-derive
    // the offset.
    let mut seg_bases = vec![0usize; stroke_count];
    let mut acc = 0usize;
    for (s, base) in seg_bases.iter_mut().enumerate() {
        *base = acc;
        acc += input.seg_counts[s] as usize;
    }

    let mut out = String::new();
    out.push_str("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n");
    if n_layers > 0 {
        out.push_str(&format!(
            "<svg xmlns=\"http://www.w3.org/2000/svg\" xmlns:inkscape=\"http://www.inkscape.org/namespaces/inkscape\" width=\"{}\" height=\"{}\" viewBox=\"0 0 {} {}\">\n",
            f4_svg(page_w_mm), f4_svg(page_h_mm), f4_svg(page_w_mm), f4_svg(page_h_mm),
        ));
        for (i, name) in layer_names.iter().enumerate() {
            let name_escaped = xml_escape(name);
            out.push_str(&format!(
                "  <g id=\"layer-{}\" inkscape:groupmode=\"layer\" inkscape:label=\"{}\">\n",
                i,
                String::from_utf8_lossy(&name_escaped),
            ));
            for s in 0..stroke_count {
                if layer_idx[s] == Some(i) {
                    let seg_count = input.seg_counts[s] as usize;
                    out.push_str(&svg_path_element(
                        input.beziers,
                        seg_bases[s],
                        seg_count,
                        input.closed[s] != 0,
                        &input.rgb[s * 3..s * 3 + 3],
                        input.widths[s],
                        page_h_mm,
                    ));
                }
            }
            out.push_str("  </g>\n");
        }
        // Strokes with an empty layer stay outside any group (after all <g>s).
        for s in 0..stroke_count {
            if layer_idx[s].is_none() {
                let seg_count = input.seg_counts[s] as usize;
                out.push_str(&svg_path_element(
                    input.beziers,
                    seg_bases[s],
                    seg_count,
                    input.closed[s] != 0,
                    &input.rgb[s * 3..s * 3 + 3],
                    input.widths[s],
                    page_h_mm,
                ));
            }
        }
    } else {
        out.push_str(&format!(
            "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{}\" height=\"{}\" viewBox=\"0 0 {} {}\">\n",
            f4_svg(page_w_mm), f4_svg(page_h_mm), f4_svg(page_w_mm), f4_svg(page_h_mm),
        ));
        for (s, &seg_base) in seg_bases.iter().enumerate() {
            let seg_count = input.seg_counts[s] as usize;
            out.push_str(&svg_path_element(
                input.beziers,
                seg_base,
                seg_count,
                input.closed[s] != 0,
                &input.rgb[s * 3..s * 3 + 3],
                input.widths[s],
                page_h_mm,
            ));
        }
    }
    out.push_str("</svg>\n");
    out.into_bytes()
}

/// Conservative arithmetic-only bound for the SVG serializer. Native SVG
/// declines malformed UTF-8 layer names; XML escaping can expand one byte to
/// at most six (`&quot;`). Thirty-two bytes safely bounds fixed-4 formatting for
/// the practical coordinate range; if an extreme value exceeds the bound, the
/// export returns ERR_SIZE and Python remains the complete fallback.
fn svg_capacity_bound(input: &PdfInput) -> usize {
    const FLOAT_MAX: usize = 32;
    let total_seg = input
        .seg_counts
        .iter()
        .fold(0usize, |a, n| a.saturating_add(*n as usize));
    2048usize
        .saturating_add(input.stroke_count.saturating_mul(256))
        .saturating_add(total_seg.saturating_mul(8 * FLOAT_MAX + 32))
        .saturating_add(input.layer_concat.len().saturating_mul(6))
}

/// First-seen (non-empty) layer names + the per-stroke index into that list,
/// mirroring `vector_export._build_pdf`'s layer discovery.
fn discover_layers(
    stroke_count: usize,
    layer_concat: &[u8],
    layer_lens: &[i64],
) -> (Vec<Vec<u8>>, Vec<Option<usize>>) {
    let mut names: Vec<Vec<u8>> = Vec::new();
    let mut idx: Vec<Option<usize>> = Vec::with_capacity(stroke_count);
    let mut off = 0usize;
    for &l in layer_lens.iter() {
        let l = l as usize;
        if l == 0 {
            idx.push(None);
        } else {
            let name_bytes = &layer_concat[off..off + l];
            match names.iter().position(|n| n.as_slice() == name_bytes) {
                Some(k) => idx.push(Some(k)),
                None => {
                    names.push(name_bytes.to_vec());
                    idx.push(Some(names.len() - 1));
                }
            }
        }
        off += l;
    }
    (names, idx)
}

/// Serialize the complete PDF byte-for-byte, mirroring
/// `_base_objects` + `_finalize`. Returns the exact file bytes.
fn build_pdf(input: &PdfInput) -> Vec<u8> {
    let stroke_count = input.stroke_count;
    let (layer_names, layer_idx) =
        discover_layers(stroke_count, input.layer_concat, input.layer_lens);
    let n_layers = layer_names.len();
    let content = build_content(
        stroke_count,
        input.seg_counts,
        input.closed,
        input.rgb,
        input.widths,
        input.beziers,
        &layer_idx,
    );

    let w = input.page_w_mm * MM_TO_PT;
    let h = input.page_h_mm * MM_TO_PT;

    let (catalog, page_res): (Vec<u8>, Vec<u8>) = if n_layers == 0 {
        (
            b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
            b"/Resources << /ProcSet [/PDF] >>".to_vec(),
        )
    } else {
        let refs: Vec<String> = (0..n_layers).map(|i| format!("{} 0 R", 6 + i)).collect();
        let refs = refs.join(" ");
        let props: Vec<String> = (0..n_layers)
            .map(|i| format!("/L{} {} 0 R", i, 6 + i))
            .collect();
        let props = props.join(" ");
        (
            format!(
                "<< /Type /Catalog /Pages 2 0 R /OCProperties << /OCGs [{}] /D << /Order [{}] >> >> >>",
                refs, refs
            )
            .into_bytes(),
            format!(
                "/Resources << /ProcSet [/PDF] /Properties << {} >> >>",
                props
            )
            .into_bytes(),
        )
    };

    let obj1: Vec<u8> = {
        let mut v = Vec::with_capacity(catalog.len() + 24);
        v.extend_from_slice(b"1 0 obj\n");
        v.extend_from_slice(&catalog);
        v.extend_from_slice(b"\nendobj\n");
        v
    };
    let obj2: Vec<u8> = b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n".to_vec();
    let obj3: Vec<u8> = {
        let mut v = Vec::with_capacity(96 + page_res.len());
        v.extend_from_slice(
            format!(
                "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {} {}] /Contents 4 0 R ",
                f4(w),
                f4(h)
            )
            .as_bytes(),
        );
        v.extend_from_slice(&page_res);
        v.extend_from_slice(b" >>\nendobj\n");
        v
    };
    let obj4: Vec<u8> = {
        let mut v = Vec::with_capacity(40 + content.len());
        v.extend_from_slice(
            format!("4 0 obj\n<< /Length {} >>\nstream\n", content.len()).as_bytes(),
        );
        v.extend_from_slice(&content);
        v.extend_from_slice(b"\nendstream\nendobj\n");
        v
    };
    let title_escaped = pdf_escape(input.title);
    let obj5: Vec<u8> = {
        let mut v = Vec::with_capacity(32 + title_escaped.len());
        v.extend_from_slice(b"5 0 obj\n<< /Title (");
        v.extend_from_slice(&title_escaped);
        v.extend_from_slice(b") /Producer (Narwhal 3D) >>\nendobj\n");
        v
    };
    let mut objects: Vec<Vec<u8>> = Vec::with_capacity(5 + n_layers);
    objects.push(obj1);
    objects.push(obj2);
    objects.push(obj3);
    objects.push(obj4);
    objects.push(obj5);
    for (i, name) in layer_names.iter().enumerate() {
        let name_escaped = pdf_escape(name);
        let mut v = Vec::with_capacity(32 + name_escaped.len());
        v.extend_from_slice(format!("{} 0 obj\n<< /Type /OCG /Name (", 6 + i).as_bytes());
        v.extend_from_slice(&name_escaped);
        v.extend_from_slice(b") >>\nendobj\n");
        objects.push(v);
    }

    let header: Vec<u8> = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n".to_vec();
    let n = 6 + n_layers; // object numbers 1..(5 + n_layers), so n = 6 + n_layers entries
    let mut offsets = vec![0usize; n];
    let mut pos = header.len();
    for (i, body) in objects.iter().enumerate() {
        offsets[i + 1] = pos;
        pos += body.len();
    }
    let xref_pos = pos;
    let mut xref = String::with_capacity(64 + n * 20);
    xref.push_str("xref\n");
    xref.push_str(&format!("0 {}\n", n));
    xref.push_str("0000000000 65535 f \n");
    for off in &offsets[1..n] {
        xref.push_str(&format!("{:010} 00000 n \n", off));
    }
    xref.push_str("trailer\n");
    xref.push_str(&format!("<< /Size {} /Root 1 0 R /Info 5 0 R >>\n", n));
    xref.push_str("startxref\n");
    xref.push_str(&format!("{}\n", xref_pos));
    xref.push_str("%%EOF\n");
    let xref_bytes = xref.into_bytes();

    let total = header.len() + objects.iter().map(|o| o.len()).sum::<usize>() + xref_bytes.len();
    let mut out = Vec::with_capacity(total);
    out.extend_from_slice(&header);
    for o in &objects {
        out.extend_from_slice(o);
    }
    out.extend_from_slice(&xref_bytes);
    out
}

/// A conservative upper bound on `build_pdf`'s byte length, computable from the
/// input counts alone (no per-coordinate formatting). Guaranteed >= the actual
/// length, so a caller-owned buffer of this size is always large enough. The
/// Python adapter mirrors this bound when it allocates the output buffer.
fn capacity_bound(input: &PdfInput) -> usize {
    const FLOAT_MAX: usize = 24; // sign + 18 integer digits + '.' + 4 decimals + margin
    let total_seg: usize = input.seg_counts.iter().map(|c| *c as usize).sum();
    let content = input.stroke_count
        * (3 * FLOAT_MAX + 8 // colour
            + FLOAT_MAX + 2 // width
            + 2 * FLOAT_MAX + 2 // move
            + 2 // h, S
            + 40) // layer markers + newlines margin
        + total_seg * (6 * FLOAT_MAX + 2 + 1);
    let n_layers_upper = input.stroke_count + 1;
    let objects = 2048 + n_layers_upper * 128 + (6 + n_layers_upper) * 32;
    let strings = input.title.len() * 2 + input.layer_concat.len() * 2;
    content + objects + strings
}

/// The raw ABI input (caller-owned pointers + scalars), mirroring `obj::RawInput`.
struct RawInput {
    beziers: *const f64,
    total_segments: i64,
    seg_counts: *const i64,
    closed: *const i32,
    rgb: *const f64,
    widths: *const f64,
    layer_concat: *const u8,
    layer_lens: *const i64,
    title: *const u8,
    title_len: i64,
    page_w_mm: f64,
    page_h_mm: f64,
    stroke_count: i64,
}

/// Prepare + validate the raw ABI input into a `PdfInput`. The slices borrow
/// for the lifetime of the `&RawInput` (the data lives as long as the caller
/// guarantees, exactly as in `obj::prepare`).
fn prepare(raw: &RawInput) -> Result<PdfInput<'_>, i32> {
    if raw.total_segments < 0
        || raw.title_len < 0
        || raw.stroke_count < 0
        || raw.page_w_mm.is_nan()
        || raw.page_h_mm.is_nan()
    {
        return Err(ERR_INPUT);
    }
    let strokes = raw.stroke_count as usize;
    let segs = raw.total_segments as usize;
    let title_bytes = raw.title_len as usize;
    let beziers_len = segs.checked_mul(8).ok_or(ERR_INPUT)?;
    let beziers = unsafe { crate::checked_slice(raw.beziers, beziers_len) }.ok_or(ERR_NULL)?;
    let seg_counts = unsafe { crate::checked_slice(raw.seg_counts, strokes) }.ok_or(ERR_NULL)?;
    let closed = unsafe { crate::checked_slice(raw.closed, strokes) }.ok_or(ERR_NULL)?;
    let rgb = unsafe { crate::checked_slice(raw.rgb, strokes * 3) }.ok_or(ERR_NULL)?;
    let widths = unsafe { crate::checked_slice(raw.widths, strokes) }.ok_or(ERR_NULL)?;
    let layer_lens = unsafe { crate::checked_slice(raw.layer_lens, strokes) }.ok_or(ERR_NULL)?;
    let title = unsafe { crate::checked_slice(raw.title, title_bytes) }.ok_or(ERR_NULL)?;
    // layer_concat length must equal the sum of layer_lens (validated below).
    let mut concat_len = 0usize;
    let mut seg_sum = 0usize;
    for (sc, ll) in seg_counts.iter().zip(layer_lens.iter()) {
        if *sc < 0 || *ll < 0 {
            return Err(ERR_INPUT);
        }
        concat_len = concat_len.checked_add(*ll as usize).ok_or(ERR_INPUT)?;
        seg_sum = seg_sum.checked_add(*sc as usize).ok_or(ERR_INPUT)?;
    }
    if seg_sum != segs {
        return Err(ERR_INPUT);
    }
    let layer_concat =
        unsafe { crate::checked_slice(raw.layer_concat, concat_len) }.ok_or(ERR_NULL)?;
    Ok(PdfInput {
        beziers,
        seg_counts,
        closed,
        rgb,
        widths,
        layer_concat,
        layer_lens,
        title,
        page_w_mm: raw.page_w_mm,
        page_h_mm: raw.page_h_mm,
        stroke_count: strokes,
    })
}

/// One-pass PDF encode into a caller-owned buffer (Q117-07).
///
/// Serializes the complete file EXACTLY ONCE (the content stream -- the
/// dominant cost -- is formatted a single time) and writes it into the
/// caller buffer, publishing `*out_written` (the exact byte count) only on
/// complete success. The caller allocates `out_capacity` from a conservative
/// arithmetic bound (mirrored by the Python adapter) that is guaranteed >= the
/// actual length. `*out_written` is left untouched on any failure (the caller
/// falls back to Python). No partial output is produced.
///
/// Returns `0` on success, otherwise a negative stable error code.
///
/// # Safety
///
/// All pointer arguments satisfy the crate's slice invariants (null allowed
/// only for a zero count), `out` is writable for `out_capacity` bytes, and
/// `out_written` is a valid initialized `i64` slot.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_pdf_encode_into(
    beziers: *const f64,
    total_segments: i64,
    seg_counts: *const i64,
    closed: *const i32,
    rgb: *const f64,
    widths: *const f64,
    layer_concat: *const u8,
    layer_lens: *const i64,
    title: *const u8,
    title_len: i64,
    page_w_mm: f64,
    page_h_mm: f64,
    stroke_count: i64,
    out: *mut u8,
    out_capacity: usize,
    out_written: *mut i64,
) -> i32 {
    if out_written.is_null() {
        return ERR_NULL;
    }
    let raw = RawInput {
        beziers,
        total_segments,
        seg_counts,
        closed,
        rgb,
        widths,
        layer_concat,
        layer_lens,
        title,
        title_len,
        page_w_mm,
        page_h_mm,
        stroke_count,
    };
    let input = match prepare(&raw) {
        Ok(input) => input,
        Err(code) => return code,
    };
    // The capacity bound must hold (the caller guarantees it); it also bounds
    // the allocation below so a malformed/oversized input fails closed.
    let bound = capacity_bound(&input);
    if out_capacity < bound {
        return ERR_SIZE;
    }
    let bytes = build_pdf(&input);
    if bytes.len() > out_capacity {
        return ERR_SIZE;
    }
    if out.is_null() {
        return ERR_NULL;
    }
    let out_slice = match unsafe { crate::checked_slice_mut(out, bytes.len()) } {
        Some(o) => o,
        None => return ERR_SIZE,
    };
    out_slice.copy_from_slice(&bytes);
    *out_written = bytes.len() as i64;
    0
}

/// One-pass SVG encode into a caller-owned buffer (Q117-08). Uses the same
/// packed stroke layout as PDF, buckets first-seen layers once, and publishes
/// the exact byte count only after a complete successful serialization.
///
/// # Safety
///
/// Input pointers satisfy the crate slice invariants, `out` is writable for
/// `out_capacity` bytes, output regions do not overlap input regions, and
/// `out_written` is a valid initialized `i64` slot.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_svg_encode_into(
    beziers: *const f64,
    total_segments: i64,
    seg_counts: *const i64,
    closed: *const i32,
    rgb: *const f64,
    widths: *const f64,
    layer_concat: *const u8,
    layer_lens: *const i64,
    page_w_mm: f64,
    page_h_mm: f64,
    stroke_count: i64,
    out: *mut u8,
    out_capacity: usize,
    out_written: *mut i64,
) -> i32 {
    if out_written.is_null() {
        return ERR_NULL;
    }
    let raw = RawInput {
        beziers,
        total_segments,
        seg_counts,
        closed,
        rgb,
        widths,
        layer_concat,
        layer_lens,
        title: std::ptr::null(),
        title_len: 0,
        page_w_mm,
        page_h_mm,
        stroke_count,
    };
    let input = match prepare(&raw) {
        Ok(input) => input,
        Err(code) => return code,
    };
    if std::str::from_utf8(input.layer_concat).is_err() {
        return ERR_INPUT;
    }
    let bound = svg_capacity_bound(&input);
    if out_capacity < bound {
        return ERR_SIZE;
    }
    let bytes = build_svg(&input);
    if bytes.len() > out_capacity {
        return ERR_SIZE;
    }
    if out.is_null() {
        return ERR_NULL;
    }
    let out_slice = match unsafe { crate::checked_slice_mut(out, bytes.len()) } {
        Some(o) => o,
        None => return ERR_SIZE,
    };
    out_slice.copy_from_slice(&bytes);
    *out_written = bytes.len() as i64;
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    // A small helper to build a PdfInput from a stroke list of
    // (nseg, closed, rgb, width_mm, layer) plus per-segment control points.
    struct StrokeSpec {
        segs: Vec<[f64; 8]>,
        closed: bool,
        rgb: [f64; 3],
        width_mm: f64,
        layer: &'static str,
    }

    fn input(strokes: &[StrokeSpec], title: &[u8], w: f64, h: f64) -> PdfInput<'static> {
        let stroke_count = strokes.len();
        let total_seg: usize = strokes.iter().map(|s| s.segs.len()).sum();
        let mut beziers = Vec::with_capacity(total_seg * 8);
        let mut seg_counts = Vec::with_capacity(stroke_count);
        let mut closed = Vec::with_capacity(stroke_count);
        let mut rgb = Vec::with_capacity(stroke_count * 3);
        let mut widths = Vec::with_capacity(stroke_count);
        let mut layer_concat = Vec::new();
        let mut layer_lens = Vec::with_capacity(stroke_count);
        for s in strokes {
            for seg in &s.segs {
                beziers.extend_from_slice(&seg[..]);
            }
            seg_counts.push(s.segs.len() as i64);
            closed.push(if s.closed { 1 } else { 0 });
            rgb.extend_from_slice(&s.rgb);
            widths.push(s.width_mm);
            layer_concat.extend_from_slice(s.layer.as_bytes());
            layer_lens.push(s.layer.len() as i64);
        }
        let beziers = Box::leak(beziers.into_boxed_slice());
        let seg_counts = Box::leak(seg_counts.into_boxed_slice());
        let closed = Box::leak(closed.into_boxed_slice());
        let rgb = Box::leak(rgb.into_boxed_slice());
        let widths = Box::leak(widths.into_boxed_slice());
        let layer_concat = Box::leak(layer_concat.into_boxed_slice());
        let layer_lens = Box::leak(layer_lens.into_boxed_slice());
        let title = Box::leak(title.to_vec().into_boxed_slice());
        PdfInput {
            beziers,
            seg_counts,
            closed,
            rgb,
            widths,
            layer_concat,
            layer_lens,
            title,
            page_w_mm: w,
            page_h_mm: h,
            stroke_count,
        }
    }

    // The fixed no-layer two-stroke reference, pinned to the Python
    // `vector_export` output (REF_PDF_SHA256, 817 bytes).
    fn ref_strokes() -> Vec<StrokeSpec> {
        vec![
            StrokeSpec {
                segs: vec![[10., 20., 30., 40., 50., 60., 70., 80.]],
                closed: false,
                rgb: [1., 0., 0.],
                width_mm: 0.5,
                layer: "",
            },
            StrokeSpec {
                segs: vec![
                    [0., 0., 20., 10., 40., 10., 60., 0.],
                    [60., 0., 80., 10., 100., 10., 120., 0.],
                ],
                closed: true,
                rgb: [0., 0.5, 1.],
                width_mm: 1.0,
                layer: "",
            },
        ]
    }

    #[test]
    fn no_layer_pdf_matches_the_python_reference_byte_for_byte() {
        let got = build_pdf(&input(&ref_strokes(), b"ref", 210.0, 297.0));
        let want: &[u8] = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595.2756 841.8898] /Contents 4 0 R /Resources << /ProcSet [/PDF] >> >>\nendobj\n4 0 obj\n<< /Length 263 >>\nstream\n1.0000 0.0000 0.0000 RG\n1.4173 w\n28.3465 56.6929 m\n85.0394 113.3858 141.7323 170.0787 198.4252 226.7717 c\nS\n0.0000 0.5000 1.0000 RG\n2.8346 w\n0.0000 0.0000 m\n56.6929 28.3465 113.3858 28.3465 170.0787 0.0000 c\n226.7717 28.3465 283.4646 28.3465 340.1575 0.0000 c\nh\nS\nendstream\nendobj\n5 0 obj\n<< /Title (ref) /Producer (Narwhal 3D) >>\nendobj\nxref\n0 6\n0000000000 65535 f \n0000000015 00000 n \n0000000064 00000 n \n0000000121 00000 n \n0000000251 00000 n \n0000000565 00000 n \ntrailer\n<< /Size 6 /Root 1 0 R /Info 5 0 R >>\nstartxref\n622\n%%EOF\n";
        assert_eq!(
            got.len(),
            want.len(),
            "length {} vs {}",
            got.len(),
            want.len()
        );
        assert_eq!(&got, want, "byte-for-byte mismatch");
    }

    #[test]
    fn layered_pdf_matches_the_python_reference_byte_for_byte() {
        let strokes = vec![
            StrokeSpec {
                segs: vec![[10., 20., 30., 40., 50., 60., 70., 80.]],
                closed: false,
                rgb: [1., 0., 0.],
                width_mm: 0.5,
                layer: "Top",
            },
            StrokeSpec {
                segs: vec![[0., 0., 20., 10., 40., 10., 60., 0.]],
                closed: true,
                rgb: [0., 1., 0.],
                width_mm: 1.0,
                layer: "Walls (x2)",
            },
            StrokeSpec {
                segs: vec![[5., 5., 15., 15., 25., 15., 35., 5.]],
                closed: false,
                rgb: [0., 0., 1.],
                width_mm: 0.5,
                layer: "Top",
            },
        ];
        let got = build_pdf(&input(&strokes, b"layers", 210.0, 297.0));
        let want: &[u8] = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R /OCProperties << /OCGs [6 0 R 7 0 R] /D << /Order [6 0 R 7 0 R] >> >> >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595.2756 841.8898] /Contents 4 0 R /Resources << /ProcSet [/PDF] /Properties << /L0 6 0 R /L1 7 0 R >> >> >>\nendobj\n4 0 obj\n<< /Length 362 >>\nstream\n/OC /L0 BDC\n1.0000 0.0000 0.0000 RG\n1.4173 w\n28.3465 56.6929 m\n85.0394 113.3858 141.7323 170.0787 198.4252 226.7717 c\nS\nEMC\n/OC /L1 BDC\n0.0000 1.0000 0.0000 RG\n2.8346 w\n0.0000 0.0000 m\n56.6929 28.3465 113.3858 28.3465 170.0787 0.0000 c\nh\nS\nEMC\n/OC /L0 BDC\n0.0000 0.0000 1.0000 RG\n1.4173 w\n14.1732 14.1732 m\n42.5197 42.5197 70.8661 42.5197 99.2126 14.1732 c\nS\nEMC\nendstream\nendobj\n5 0 obj\n<< /Title (layers) /Producer (Narwhal 3D) >>\nendobj\n6 0 obj\n<< /Type /OCG /Name (Top) >>\nendobj\n7 0 obj\n<< /Type /OCG /Name (Walls \\(x2\\)) >>\nendobj\nxref\n0 8\n0000000000 65535 f \n0000000015 00000 n \n0000000134 00000 n \n0000000191 00000 n \n0000000359 00000 n \n0000000772 00000 n \n0000000832 00000 n \n0000000876 00000 n \ntrailer\n<< /Size 8 /Root 1 0 R /Info 5 0 R >>\nstartxref\n929\n%%EOF\n";
        assert_eq!(
            got.len(),
            want.len(),
            "length {} vs {}",
            got.len(),
            want.len()
        );
        assert_eq!(&got, want, "byte-for-byte mismatch");
    }

    #[test]
    fn empty_strokes_produce_a_blank_page() {
        let got = build_pdf(&input(&[], b"", 210.0, 297.0));
        // 549 bytes, matching the Python empty case; the content stream is
        // empty (/Length 0) but the object layout and xref are intact. (The
        // header carries the Latin-1 binary marker bytes 0xe2 0xe3 0xcf 0xd3,
        // so the whole file is not valid UTF-8 -- check the ASCII parts as
        // bytes, not via a str.)
        assert_eq!(got.len(), 549, "empty PDF length");
        fn has(haystack: &[u8], needle: &[u8]) -> bool {
            haystack.windows(needle.len()).any(|w| w == needle)
        }
        assert!(
            has(&got, b"<< /Length 0 >>"),
            "content stream must be empty"
        );
        assert!(has(&got, b"startxref"), "xref present");
        assert!(got.ends_with(b"%%EOF\n"), "ends with %%EOF");
    }

    /// Q117-07 named-invariant mutation target: "offset one xref entry; reader/xref
    /// checks must go RED." This test reproduces the Python reader's xref check
    /// (``spike/test_vector_export.py::_xref_offsets`` + "every xref offset points
    /// at the bytes `N 0 obj`") inside Rust so a mutation of the xref offset
    /// table is caught here without a full PDF reader.
    #[test]
    fn xref_offsets_point_at_the_correct_object_marker() {
        // Two strokes, two layers: 7 objects total (5 base + 2 OCGs).
        let got = build_pdf(&input(
            &[
                StrokeSpec {
                    segs: vec![[2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0, 256.0]],
                    closed: false,
                    rgb: [1.0, 0.0, 0.0],
                    width_mm: 0.5,
                    layer: "cut",
                },
                StrokeSpec {
                    segs: vec![[1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0]],
                    closed: true,
                    rgb: [0.0, 1.0, 0.0],
                    width_mm: 0.3,
                    layer: "drill",
                },
            ],
            b"T",
            210.0,
            297.0,
        ));
        // Find the xref table (after the last \\nxref\\n).
        let xref_idx = got
            .windows(6)
            .position(|w| w == b"\nxref\n")
            .expect("no xref table");
        let xref = &got[xref_idx + 1..];
        // Parse "N M" header and collect the N object offsets (in order).
        let header_end = xref.windows(1).position(|w| w == b"\n").unwrap() + 1;
        let count_line_end = xref[header_end..]
            .windows(1)
            .position(|w| w == b"\n")
            .unwrap()
            + 1;
        let count_line =
            std::str::from_utf8(&xref[header_end..header_end + count_line_end - 1]).unwrap();
        let count: usize = count_line
            .split_once(' ')
            .unwrap()
            .1
            .parse()
            .expect("xref count line");
        // Walk the entries: each is exactly 20 bytes, "0000000000 65535 f \n" or
        // "0000000000 00000 n \n". The first entry is the free-object entry
        // (object 0). The rest are object 1..count.
        let mut entries = Vec::new();
        let mut i = header_end + count_line_end;
        for _ in 0..count {
            let entry = &xref[i..i + 20];
            let off: usize = std::str::from_utf8(&entry[..10])
                .unwrap()
                .parse()
                .expect("offset");
            entries.push(off);
            i += 20;
        }
        assert_eq!(entries.len(), count, "entry count");
        // entries[0] is object 0 (free, offset 0).
        assert_eq!(entries[0], 0, "object 0 must be the free entry at offset 0");
        // For each object number 1..count, the xref entry's offset must point
        // at the bytes "<N> 0 obj" — this is exactly the invariant the Python
        // reader (``spike/test_vector_export.py``) asserts.
        for (obj_num, off) in entries[1..].iter().enumerate() {
            let expected = format!("{} 0 obj", obj_num + 1);
            let at = &got[*off..*off + expected.len()];
            assert_eq!(
                at,
                expected.as_bytes(),
                "xref entry for object {} points at {:?}, expected {:?}",
                obj_num + 1,
                std::str::from_utf8(at).unwrap_or("<non-utf8>"),
                expected
            );
        }
    }

    #[test]
    fn pdf_escape_matches_python() {
        assert_eq!(pdf_escape(b"abc"), b"abc");
        assert_eq!(pdf_escape(b"a(b)c"), b"a\\(b\\)c");
        assert_eq!(pdf_escape(b"\\\\"), b"\\\\\\\\");
        assert_eq!(pdf_escape(b"\\("), b"\\\\\\(");
        assert_eq!(pdf_escape(b"Walls (x2)"), b"Walls \\(x2\\)");
    }

    #[test]
    fn f4_matches_python_ties_to_even() {
        assert_eq!(f4(0.00005), "0.0001");
        assert_eq!(f4(9999.99995), "9999.9999");
        assert_eq!(f4(-1.00005), "-1.0001");
        assert_eq!(f4(2.5), "2.5000");
        assert_eq!(f4(0.0), "0.0000");
    }

    #[test]
    fn capacity_bound_is_at_least_the_actual_length() {
        for strokes in [
            ref_strokes(),
            vec![],
            (0..200)
                .map(|i| StrokeSpec {
                    segs: vec![[
                        i as f64,
                        0.,
                        (i + 1) as f64,
                        1.,
                        (i + 2) as f64,
                        2.,
                        (i + 3) as f64,
                        3.,
                    ]],
                    closed: i % 3 == 0,
                    rgb: [
                        (i % 7) as f64 / 7.,
                        (i % 5) as f64 / 5.,
                        (i % 11) as f64 / 11.,
                    ],
                    width_mm: 0.25 + (i % 4) as f64 * 0.5,
                    layer: if i % 2 == 0 { "L" } else { "" },
                })
                .collect::<Vec<_>>(),
        ] {
            let inp = input(&strokes, b"title (with \\ parens)", 210.0, 297.0);
            let actual = build_pdf(&inp).len();
            let bound = capacity_bound(&inp);
            assert!(
                bound >= actual,
                "capacity_bound {} < actual {} (n_strokes={})",
                bound,
                actual,
                strokes.len()
            );
        }
    }

    #[test]
    fn one_pass_encode_publishes_exact_count_and_byte_matches() {
        let strokes = ref_strokes();
        let inp = input(&strokes, b"ref", 210.0, 297.0);
        let bound = capacity_bound(&inp);
        let mut buf = vec![0u8; bound];
        let mut written = 0i64;
        let rc = unsafe {
            narwhal_pure_core_pdf_encode_into(
                inp.beziers.as_ptr(),
                inp.seg_counts.iter().sum::<i64>(),
                inp.seg_counts.as_ptr(),
                inp.closed.as_ptr(),
                inp.rgb.as_ptr(),
                inp.widths.as_ptr(),
                inp.layer_concat.as_ptr(),
                inp.layer_lens.as_ptr(),
                inp.title.as_ptr(),
                inp.title.len() as i64,
                210.0,
                297.0,
                inp.stroke_count as i64,
                buf.as_mut_ptr(),
                buf.len(),
                &mut written,
            )
        };
        assert_eq!(rc, 0);
        assert_eq!(written as usize, 817);
        let want = build_pdf(&inp);
        assert_eq!(&buf[..written as usize], want.as_slice());
    }

    #[test]
    fn insufficient_capacity_rejects_and_leaves_written_untouched() {
        let strokes = ref_strokes();
        let inp = input(&strokes, b"ref", 210.0, 297.0);
        let mut buf = vec![0u8; 10];
        let mut written = 12345i64;
        let rc = unsafe {
            narwhal_pure_core_pdf_encode_into(
                inp.beziers.as_ptr(),
                inp.seg_counts.iter().sum::<i64>(),
                inp.seg_counts.as_ptr(),
                inp.closed.as_ptr(),
                inp.rgb.as_ptr(),
                inp.widths.as_ptr(),
                inp.layer_concat.as_ptr(),
                inp.layer_lens.as_ptr(),
                inp.title.as_ptr(),
                inp.title.len() as i64,
                210.0,
                297.0,
                inp.stroke_count as i64,
                buf.as_mut_ptr(),
                buf.len(),
                &mut written,
            )
        };
        assert_eq!(rc, ERR_SIZE);
        assert_eq!(written, 12345, "written must be untouched on failure");
    }

    #[test]
    fn mismatched_segment_sum_rejects() {
        // seg_counts sum (4) != total_segments (3) -> ERR_INPUT from prepare.
        // Call `prepare` directly so the export's null-buffer guard (which
        // would return ERR_NULL first) is not in the way. Every array is bound
        // to a local so its backing store outlives the `prepare` call.
        let beziers = [0f64; 32];
        let seg_counts = [2i64, 2];
        let closed = [0i32, 0];
        let rgb = [0f64; 6];
        let widths = [0.25f64, 0.25];
        let layer_lens = [0i64, 0];
        let raw = RawInput {
            beziers: beziers.as_ptr(),
            total_segments: 3,
            seg_counts: seg_counts.as_ptr(),
            closed: closed.as_ptr(),
            rgb: rgb.as_ptr(),
            widths: widths.as_ptr(),
            layer_concat: std::ptr::null(),
            layer_lens: layer_lens.as_ptr(),
            title: std::ptr::null(),
            title_len: 0,
            page_w_mm: 210.0,
            page_h_mm: 297.0,
            stroke_count: 2,
        };
        assert!(matches!(prepare(&raw), Err(ERR_INPUT)));
    }

    #[test]
    fn svg_escapes_ampersands_and_matches_document_contract() {
        let strokes = vec![StrokeSpec {
            segs: vec![[0., 0., 1., 2., 3., 4., 5., 6.]],
            closed: true,
            rgb: [0.5, 0.0, 1.0],
            width_mm: 0.25,
            layer: "cut & etch",
        }];
        let got = build_svg(&input(&strokes, b"", 210.0, 297.0));
        let text = std::str::from_utf8(&got).unwrap();
        assert!(text.contains("inkscape:label=\"cut &amp; etch\""));
        assert!(
            text.contains("M 0.0000 297.0000 C 1.0000 295.0000 3.0000 293.0000 5.0000 291.0000 Z")
        );
        assert!(text.ends_with("</svg>\n"));
    }

    #[test]
    fn svg_encode_into_publishes_exact_count() {
        let strokes = ref_strokes();
        let inp = input(&strokes, b"", 210.0, 297.0);
        let mut buf = vec![0u8; svg_capacity_bound(&inp)];
        let mut written = -1i64;
        let rc = unsafe {
            narwhal_pure_core_svg_encode_into(
                inp.beziers.as_ptr(),
                inp.seg_counts.iter().sum(),
                inp.seg_counts.as_ptr(),
                inp.closed.as_ptr(),
                inp.rgb.as_ptr(),
                inp.widths.as_ptr(),
                inp.layer_concat.as_ptr(),
                inp.layer_lens.as_ptr(),
                210.0,
                297.0,
                inp.stroke_count as i64,
                buf.as_mut_ptr(),
                buf.len(),
                &mut written,
            )
        };
        assert_eq!(rc, 0);
        assert_eq!(&buf[..written as usize], build_svg(&inp));
    }
}
