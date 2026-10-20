//! SPDX-License-Identifier: GPL-3.0-or-later
//!
//! Native SVG path parsing (Q106-14). A faithful port of the pure-Python
//! `narwhal3d/svgpath.py`: the same command tokenizer (absolute/relative,
//! implicit command repetition, the exact number grammar), the same line and
//! quadratic elevations, and the same endpoint-to-centre elliptical-arc
//! approximation (SVG spec appendix F.6), emitting the EXACT same cubic-Bézier
//! subpath structure the Python parser produces.
//!
//! The output is NESTED (a list of subpath dicts, each with a list of 4-point
//! cubic Béziers and a `closed` flag), so — like the STL/OBJ/DXF/macro routes —
//! the result is serialized to bounded JSON and handed back with the crate's
//! standard two-pass "size query + encode into a caller-owned buffer" pattern.
//! The Python hook `json.loads` the string and converts the nested lists back
//! into the exact Python contract (each Bézier a 4-tuple of 2-tuples), so a
//! successful native route is byte-for-byte the Python result.
//!
//! A malformed path (a number outside a command, a command before any moveto,
//! or a command with too few parameters) is a REJECTION (`ERR_PARSE`), not an
//! output: the Python hook then runs its unchanged parser and raises the exact
//! `ValueError` (whose message embeds the command name), so the error itself
//! never crosses the ABI.
//!
//! The crate stays dependency-free: the JSON emitter and the float formatting
//! are hand-rolled.

const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const ERR_SIZE: i32 = -3;
/// The path is malformed (the Python parser raises the exact `ValueError`).
const ERR_PARSE: i32 = -4;

/// A path longer than this (in input bytes) is refused by the native route
/// (a Python fallback), not truncated. Real SVG paths are a few KB at most.
const SVG_MAX_INPUT_BYTES: usize = 1 << 20; // 1 MiB

fn is_separator(b: u8) -> bool {
    matches!(b, b' ' | b'\t' | b'\r' | b'\n' | b',' | b';')
}

fn is_cmd(b: u8) -> bool {
    matches!(
        b,
        b'M' | b'm'
            | b'L'
            | b'l'
            | b'H'
            | b'h'
            | b'V'
            | b'v'
            | b'C'
            | b'c'
            | b'S'
            | b's'
            | b'Q'
            | b'q'
            | b'T'
            | b't'
            | b'A'
            | b'a'
            | b'Z'
            | b'z'
    )
}

fn is_sign(b: u8) -> bool {
    b == b'+' || b == b'-'
}

/// Split raw SVG path data into command letters (original case) and floats.
///
/// Mirrors the Python `_tokenize` byte-for-byte: the same separators, the same
/// command-letter set, and the same number grammar (optional sign, digits,
/// an optional `.digits` — a dot NOT followed by a digit does not belong to
/// this number, and an optional `e/E [sign] digits` — an `e` with no exponent
/// digits does not belong to this number). A character that starts neither a
/// separator, a command, nor a number is skipped (as the Python tokenizer
/// does), and a number that fails to parse is a hard error (the Python
/// tokenizer would raise the same `ValueError`).
fn tokenize(b: &[u8]) -> Result<Vec<Tok>, ()> {
    let mut toks = Vec::new();
    let n = b.len();
    let mut i = 0usize;
    while i < n {
        let ch = b[i];
        if is_separator(ch) {
            i += 1;
        } else if is_cmd(ch) {
            toks.push(Tok::Cmd(ch));
            i += 1;
        } else {
            let start = i;
            let mut j = i;
            if j < n && is_sign(b[j]) {
                j += 1;
            }
            while j < n && b[j].is_ascii_digit() {
                j += 1;
            }
            // optional fractional part: "." followed by at least one digit
            if j < n && b[j] == b'.' && j + 1 < n && b[j + 1].is_ascii_digit() {
                j += 1;
                while j < n && b[j].is_ascii_digit() {
                    j += 1;
                }
            }
            // optional exponent: e/E [sign] digits (no digits -> not part of it)
            if j < n && (b[j] == b'e' || b[j] == b'E') {
                let mut k = j + 1;
                if k < n && is_sign(b[k]) {
                    k += 1;
                }
                if k < n && b[k].is_ascii_digit() {
                    while k < n && b[k].is_ascii_digit() {
                        k += 1;
                    }
                    j = k;
                }
            }
            if j == start {
                // no number was consumed -- skip the character (mirrors Python)
                i += 1;
                continue;
            }
            let s = std::str::from_utf8(&b[start..j]).map_err(|_| ())?;
            toks.push(Tok::Num(s.parse::<f64>().map_err(|_| ())?));
            i = j;
        }
    }
    Ok(toks)
}

#[derive(Debug, Clone, Copy)]
enum Tok {
    Cmd(u8),
    Num(f64),
}

/// A parsed subpath: its cubic Béziers (each a flat 8-float `p0 c1 c2 p1`)
/// and whether an explicit `Z` closed it.
#[derive(Debug, Clone)]
struct Sub {
    beziers: Vec<[f64; 8]>,
    closed: bool,
}

/// The parameter count each command consumes (upper-case form).
fn arity(eff: u8) -> Option<usize> {
    Some(match eff {
        b'M' | b'L' | b'T' => 2,
        b'H' | b'V' => 1,
        b'C' => 6,
        b'S' | b'Q' => 4,
        b'A' => 7,
        b'Z' => 0,
        _ => return None,
    })
}

/// Append a line (as a cubic) from `from` to `to` to the open subpath.
fn push_line(sub: &mut Option<Sub>, from: (f64, f64), to: (f64, f64)) {
    sub.as_mut()
        .expect("an open subpath")
        .beziers
        .push(line_cubic(from, to));
}

/// Flush the open subpath (if any) into the finished list.
fn flush(subpaths: &mut Vec<Sub>, cur_sub: &mut Option<Sub>) {
    if let Some(s) = cur_sub.take() {
        subpaths.push(s);
    }
}

fn parse_path(toks: &[Tok]) -> Result<Vec<Sub>, ()> {
    let mut subpaths: Vec<Sub> = Vec::new();
    let mut cur: Option<(f64, f64)> = None;
    let mut start: Option<(f64, f64)> = None;
    let mut last_c2: Option<(f64, f64)> = None;
    let mut last_q: Option<(f64, f64)> = None;
    let mut cur_sub: Option<Sub> = None;

    let mut i = 0usize;
    let n = toks.len();
    while i < n {
        let cmd = match toks[i] {
            Tok::Cmd(c) => c,
            Tok::Num(_) => return Err(()), // a number outside a command
        };
        let eff = cmd.to_ascii_uppercase();
        let rel = cmd.is_ascii_lowercase();
        let arity = arity(eff).ok_or(())?;
        i += 1;

        // --- Z / z: zero-arity -- close the current subpath --------------
        if eff == b'Z' {
            if cur_sub.is_some() {
                if cur != start {
                    push_line(&mut cur_sub, cur.expect("set"), start.expect("set"));
                }
                cur = start;
                cur_sub.as_mut().expect("set").closed = true;
                flush(&mut subpaths, &mut cur_sub);
                last_c2 = None;
                last_q = None;
            }
            continue;
        }

        // --- every other command consumes one or more parameter groups ----
        if cur.is_none() && eff != b'M' {
            return Err(()); // a command before any moveto
        }

        let mut first = true;
        while i < n && matches!(toks[i], Tok::Num(_)) {
            let mut params: Vec<f64> = Vec::with_capacity(arity);
            while params.len() < arity && i < n && matches!(toks[i], Tok::Num(_)) {
                params.push(match toks[i] {
                    Tok::Num(v) => v,
                    Tok::Cmd(_) => return Err(()),
                });
                i += 1;
            }
            if params.len() < arity {
                return Err(()); // too few parameters
            }

            if eff == b'M' && first {
                // moveto: open a new subpath. A relative `m` with no current
                // point is treated as absolute (SVG spec).
                let (mut x, mut y) = (params[0], params[1]);
                if rel {
                    if let Some((cx, cy)) = cur {
                        x += cx;
                        y += cy;
                    }
                }
                flush(&mut subpaths, &mut cur_sub);
                cur = Some((x, y));
                start = Some((x, y));
                cur_sub = Some(Sub {
                    beziers: Vec::new(),
                    closed: false,
                });
                last_c2 = None;
                last_q = None;
                first = false;
            } else {
                // a segment after a Z (cur_sub None, cur set) opens a fresh
                // subpath at the current point.
                if cur_sub.is_none() {
                    let c = cur.expect("set");
                    start = Some(c);
                    cur_sub = Some(Sub {
                        beziers: Vec::new(),
                        closed: false,
                    });
                    last_c2 = None;
                    last_q = None;
                }
                let c = cur.expect("set");
                match eff {
                    b'M' => {
                        // further M groups are implicit linetos (SVG spec)
                        let (mut x, mut y) = (params[0], params[1]);
                        if rel {
                            x += c.0;
                            y += c.1;
                        }
                        push_line(&mut cur_sub, c, (x, y));
                        cur = Some((x, y));
                    }
                    b'L' => {
                        let (mut x, mut y) = (params[0], params[1]);
                        if rel {
                            x += c.0;
                            y += c.1;
                        }
                        push_line(&mut cur_sub, c, (x, y));
                        cur = Some((x, y));
                    }
                    b'H' => {
                        let mut x = params[0];
                        if rel {
                            x += c.0;
                        }
                        push_line(&mut cur_sub, c, (x, c.1));
                        cur = Some((x, c.1));
                    }
                    b'V' => {
                        let mut y = params[0];
                        if rel {
                            y += c.1;
                        }
                        push_line(&mut cur_sub, c, (c.0, y));
                        cur = Some((c.0, y));
                    }
                    b'C' => {
                        let (mut x1, mut y1) = (params[0], params[1]);
                        let (mut x2, mut y2) = (params[2], params[3]);
                        let (mut x, mut y) = (params[4], params[5]);
                        if rel {
                            x1 += c.0;
                            y1 += c.1;
                            x2 += c.0;
                            y2 += c.1;
                            x += c.0;
                            y += c.1;
                        }
                        cur_sub
                            .as_mut()
                            .expect("set")
                            .beziers
                            .push([c.0, c.1, x1, y1, x2, y2, x, y]);
                        last_c2 = Some((x2, y2));
                        last_q = None;
                        cur = Some((x, y));
                    }
                    b'S' => {
                        let (mut x2, mut y2) = (params[0], params[1]);
                        let (mut x, mut y) = (params[2], params[3]);
                        if rel {
                            x2 += c.0;
                            y2 += c.1;
                            x += c.0;
                            y += c.1;
                        }
                        let (x1, y1) = match last_c2 {
                            Some((lx, ly)) => (2.0 * c.0 - lx, 2.0 * c.1 - ly),
                            None => (c.0, c.1),
                        };
                        cur_sub
                            .as_mut()
                            .expect("set")
                            .beziers
                            .push([c.0, c.1, x1, y1, x2, y2, x, y]);
                        last_c2 = Some((x2, y2));
                        last_q = None;
                        cur = Some((x, y));
                    }
                    b'Q' => {
                        let (mut x1, mut y1) = (params[0], params[1]);
                        let (mut x, mut y) = (params[2], params[3]);
                        if rel {
                            x1 += c.0;
                            y1 += c.1;
                            x += c.0;
                            y += c.1;
                        }
                        cur_sub.as_mut().expect("set").beziers.push(quad_cubic(
                            c,
                            (x1, y1),
                            (x, y),
                        ));
                        last_q = Some((x1, y1));
                        last_c2 = None;
                        cur = Some((x, y));
                    }
                    b'T' => {
                        let (mut x, mut y) = (params[0], params[1]);
                        if rel {
                            x += c.0;
                            y += c.1;
                        }
                        let (qx, qy) = match last_q {
                            Some((lx, ly)) => (2.0 * c.0 - lx, 2.0 * c.1 - ly),
                            None => (c.0, c.1),
                        };
                        cur_sub.as_mut().expect("set").beziers.push(quad_cubic(
                            c,
                            (qx, qy),
                            (x, y),
                        ));
                        last_q = Some((qx, qy));
                        last_c2 = None;
                        cur = Some((x, y));
                    }
                    b'A' => {
                        let rx = params[0];
                        let ry = params[1];
                        let rot = params[2];
                        let large = params[3];
                        let sweep = params[4];
                        let (mut x, mut y) = (params[5], params[6]);
                        if rel {
                            x += c.0;
                            y += c.1;
                        }
                        if rx == 0.0 || ry == 0.0 {
                            push_line(&mut cur_sub, c, (x, y));
                            cur = Some((x, y));
                        } else if (x, y) != c {
                            cur_sub.as_mut().expect("set").beziers.extend(arc_cubic(
                                c,
                                (x, y),
                                rx,
                                ry,
                                rot,
                                large != 0.0,
                                sweep != 0.0,
                            ));
                            cur = Some((x, y));
                        }
                        last_c2 = None;
                        last_q = None;
                    }
                    _ => return Err(()),
                }
            }
        }
    }

    flush(&mut subpaths, &mut cur_sub);
    Ok(subpaths)
}

// ---------------------------------------------------------------------------
// segment -> cubic conversions (exact ports of the Python helpers)
// ---------------------------------------------------------------------------

/// A straight line as a cubic Bézier (control points at 1/3 and 2/3).
fn line_cubic(p0: (f64, f64), p1: (f64, f64)) -> [f64; 8] {
    let (x0, y0) = p0;
    let (x1, y1) = p1;
    [
        x0,
        y0,
        x0 + (x1 - x0) / 3.0,
        y0 + (y1 - y0) / 3.0,
        x0 + 2.0 * (x1 - x0) / 3.0,
        y0 + 2.0 * (y1 - y0) / 3.0,
        x1,
        y1,
    ]
}

/// Elevate a quadratic Bézier (control `qc`) EXACTLY to a cubic.
fn quad_cubic(p0: (f64, f64), qc: (f64, f64), p1: (f64, f64)) -> [f64; 8] {
    let (x0, y0) = p0;
    let (x1, y1) = qc;
    let (x2, y2) = p1;
    let c1 = (x0 + (2.0 / 3.0) * (x1 - x0), y0 + (2.0 / 3.0) * (y1 - y0));
    let c2 = (x2 + (2.0 / 3.0) * (x1 - x2), y2 + (2.0 / 3.0) * (y1 - y2));
    [x0, y0, c1.0, c1.1, c2.0, c2.1, x2, y2]
}

/// Elliptical arc (p0 -> p1) as a list of cubic Béziers.
///
/// Per the SVG spec's endpoint-to-centre parametrization (appendix F.6). The
/// arc is split into segments of at most 90°; each is approximated by a cubic
/// with handle length `k = (4/3)·tan(|step|/4)` (signed by the sweep
/// direction). The per-segment error for a 90° arc of radius `r` is
/// `< 2.7e-4 · r` (see the Python module docstring, `# OURS`).
fn arc_cubic(
    p0: (f64, f64),
    p1: (f64, f64),
    rx_in: f64,
    ry_in: f64,
    rot_deg: f64,
    large: bool,
    sweep: bool,
) -> Vec<[f64; 8]> {
    let (x1, y1) = p0;
    let (x2, y2) = p1;
    if (x1, y1) == (x2, y2) {
        return Vec::new(); // no-op arc (endpoint == start)
    }

    let phi = rot_deg.to_radians();
    let cp = phi.cos();
    let sp = phi.sin();

    // --- F.6: endpoint-to-centre conversion ------------------------------
    // half chord in the rotated frame
    let dx = (x1 - x2) / 2.0;
    let dy = (y1 - y2) / 2.0;
    let x1p = cp * dx + sp * dy;
    let y1p = -sp * dx + cp * dy;

    // ensure radii are large enough for the chord (SVG spec)
    let mut rx = rx_in;
    let mut ry = ry_in;
    let lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry);
    if lam > 1.0 {
        let s = lam.sqrt();
        rx *= s;
        ry *= s;
    }

    let num = (rx * rx) * (ry * ry) - (rx * rx) * (y1p * y1p) - (ry * ry) * (x1p * x1p);
    let den = (rx * rx) * (y1p * y1p) + (ry * ry) * (x1p * x1p);
    let mut co = if den > 0.0 {
        (num.max(0.0) / den).sqrt()
    } else {
        0.0
    };
    if large == sweep {
        co = -co;
    }
    let cxp = co * (rx * y1p) / ry;
    let cyp = -co * (ry * x1p) / rx;

    // centre in the model frame (F.6 step 3 includes the chord midpoint)
    let cx = cp * cxp - sp * cyp + (x1 + x2) / 2.0;
    let cy = sp * cxp + cp * cyp + (y1 + y2) / 2.0;

    // angles (in the rotated frame, on the unit circle scaled by rx, ry)
    let ux = (cp * (x1 - cx) + sp * (y1 - cy)) / rx;
    let uy = -(sp * (x1 - cx) - cp * (y1 - cy)) / ry;
    let vx = (cp * (x2 - cx) + sp * (y2 - cy)) / rx;
    let vy = -(sp * (x2 - cx) - cp * (y2 - cy)) / ry;
    let mut a = uy.atan2(ux);
    let mut dtheta = vy.atan2(vx) - a;
    // the large flag is already encoded in the sign of `co`; only the sweep
    // direction needs adjusting here (F.6 step 4).
    if !sweep && dtheta > 0.0 {
        dtheta -= 2.0 * std::f64::consts::PI;
    } else if sweep && dtheta < 0.0 {
        dtheta += 2.0 * std::f64::consts::PI;
    }

    // --- split into <= 90° segments, each -> one cubic -------------------
    let mut beziers: Vec<[f64; 8]> = Vec::new();
    let mut remaining = dtheta;
    while remaining.abs() > 1e-12 {
        let step = if remaining.abs() > std::f64::consts::PI / 2.0 {
            (std::f64::consts::PI / 2.0) * if remaining > 0.0 { 1.0 } else { -1.0 }
        } else {
            remaining
        };
        let a_s = a;
        let a_e = a + step;
        let k = (4.0 / 3.0) * (step.abs() / 4.0).tan();
        // points on the ellipse (rotated frame)
        let sx = rx * a_s.cos();
        let sy = ry * a_s.sin();
        let ex = rx * a_e.cos();
        let ey = ry * a_e.sin();
        // tangent directions (rotated frame)
        let tx0 = -rx * a_s.sin();
        let ty0 = ry * a_s.cos();
        let tx1 = -rx * a_e.sin();
        let ty1 = ry * a_e.cos();
        // control points (rotated frame); handle length k, signed by the sweep
        // direction so a clockwise arc mirrors a counter-clockwise one
        let sgn = if step > 0.0 { 1.0 } else { -1.0 };
        let c1 = (sx + k * sgn * tx0, sy + k * sgn * ty0);
        let c2 = (ex - k * sgn * tx1, ey - k * sgn * ty1);

        let rot_pt =
            |p: (f64, f64)| -> (f64, f64) { (cp * p.0 - sp * p.1 + cx, sp * p.0 + cp * p.1 + cy) };
        let (rsx, rsy) = rot_pt((sx, sy));
        let (rc1x, rc1y) = rot_pt(c1);
        let (rc2x, rc2y) = rot_pt(c2);
        let (rex, rey) = rot_pt((ex, ey));
        beziers.push([rsx, rsy, rc1x, rc1y, rc2x, rc2y, rex, rey]);

        a = a_e;
        remaining -= step;
    }
    beziers
}

// ---------------------------------------------------------------------------
// JSON encoding + two-pass exports
// ---------------------------------------------------------------------------

/// Append a finite `f64` to `out` as a valid JSON number. Rust's `Display` for
/// `f64` is the shortest decimal that round-trips exactly, so Python's
/// `json.loads` recovers the identical float.
fn fmt_num(v: f64, out: &mut String) {
    out.push_str(&format!("{}", v));
}

fn encode_json(subs: &[Sub]) -> String {
    let mut s = String::from("[");
    for (si, sub) in subs.iter().enumerate() {
        if si > 0 {
            s.push(',');
        }
        s.push_str("{\"beziers\":[");
        for (bi, bez) in sub.beziers.iter().enumerate() {
            if bi > 0 {
                s.push(',');
            }
            // one Bézier: the four (x, y) points
            s.push('[');
            for pi in 0..4 {
                if pi > 0 {
                    s.push(',');
                }
                s.push('[');
                fmt_num(bez[2 * pi], &mut s);
                s.push(',');
                fmt_num(bez[2 * pi + 1], &mut s);
                s.push(']');
            }
            s.push(']');
        }
        s.push_str("],\"closed\":");
        s.push_str(if sub.closed { "true" } else { "false" });
        s.push('}');
    }
    s.push(']');
    s
}

/// Parse the path and emit the bounded JSON subpath list, or `Err(())` for a
/// malformed path / a non-serializable (non-finite) coordinate.
fn svgpath_json(s: &str) -> Result<String, ()> {
    let toks = tokenize(s.as_bytes())?;
    let subs = parse_path(&toks)?;
    // Every coordinate must be a finite, JSON-serializable number; otherwise
    // hand the input back to the Python fallback rather than emit invalid JSON.
    for sub in &subs {
        for bez in &sub.beziers {
            for v in bez {
                if !v.is_finite() {
                    return Err(());
                }
            }
        }
    }
    Ok(encode_json(&subs))
}

fn span<'a>(ptr: *const u8, len: usize) -> Option<&'a [u8]> {
    unsafe { crate::checked_slice(ptr, len) }
}

/// Validate the input and return the exact byte length of the encoded subpath
/// JSON, or a negative stable error code. No output.
///
/// # Safety
///
/// `text` must be readable for `text_len` bytes (null only when `text_len == 0`).
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_svgpath_size(text: *const u8, text_len: usize) -> i64 {
    if text_len > SVG_MAX_INPUT_BYTES {
        return ERR_INPUT as i64;
    }
    let slice = match span(text, text_len) {
        Some(s) => s,
        None => return ERR_NULL as i64,
    };
    let s = match std::str::from_utf8(slice) {
        Ok(s) => s,
        Err(_) => return ERR_INPUT as i64,
    };
    match svgpath_json(s) {
        Ok(json) => json.len() as i64,
        Err(_) => ERR_PARSE as i64,
    }
}

/// Encode the parsed subpath list as bounded JSON into a caller-owned buffer.
///
/// `out` must hold exactly the bytes the encoder will produce (the same length
/// `narwhal_pure_core_svgpath_size` returns). Returns `0` on success, otherwise
/// a negative stable error code.
///
/// # Safety
///
/// `text` must be readable for `text_len` bytes (null only when `text_len == 0`)
/// and `out` writable for the exact encoded length.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_svgpath_encode(
    text: *const u8,
    text_len: usize,
    out: *mut u8,
    out_len: usize,
) -> i32 {
    if text_len > SVG_MAX_INPUT_BYTES {
        return ERR_INPUT;
    }
    let slice = match span(text, text_len) {
        Some(s) => s,
        None => return ERR_NULL,
    };
    let s = match std::str::from_utf8(slice) {
        Ok(s) => s,
        Err(_) => return ERR_INPUT,
    };
    let json = match svgpath_json(s) {
        Ok(j) => j,
        Err(_) => return ERR_PARSE,
    };
    if out_len != json.len() {
        return ERR_SIZE;
    }
    if out.is_null() {
        return ERR_NULL;
    }
    let out = match unsafe { crate::checked_slice_mut(out, out_len) } {
        Some(out) => out,
        None => return ERR_SIZE,
    };
    out.copy_from_slice(json.as_bytes());
    0
}

// ---------------------------------------------------------------------------
// Packed cubic output (Q117-03)
// ---------------------------------------------------------------------------
//
// The JSON exports above are the legacy two-pass ABI (kept byte-identical for
// the existing direct-ABI tests). This is the replacement: the parsed
// subpaths are written DIRECTLY into caller-owned arrays -- one f64 per
// coordinate, one i64 per subpath -- with no JSON formatting and no
// Rust-owned pointer crossing the boundary. Both routes call the SAME
// `tokenize` + `parse_path` (the exact structural identity the JSON route
// already guarantees), so a successful packed result is the Python parser's
// subpath list, re-laid-out.
//
// Capacity bound (proved, not heuristic): every non-closing Bézier is
// produced by a command with a letter and at least one numeric byte, and
// implicit-repetition adds at least two numeric bytes per Bézier, so those
// cost >= 2 input bytes each. A `Z`-closing Bézier costs one byte but
// requires a preceding segment that moved the current point off the subpath
// start, so per subpath `bytes >= 2 * beziers` (an open subpath: `3 + 2k
// >= 2k`; a closed one: `4 + 2k >= 2(k+1)`). Hence `total_beziers <=
// input_len / 2`. Every subpath is opened by an `M`/`m` (>= 3 bytes), so
// `subpaths <= input_len / 3 <= input_len / 2`. The caller therefore needs
// at most `input_len / 2` Béziers, i.e. `4 * input_len` f64s and
// `input_len / 2` i64s per subpath array -- for the 1 MiB input cap that
// is 32 MiB of f64s, well within the adapter's output bound. No formatting
// pass, no size query, one parse, one write.

/// Parse and write the subpaths into the caller-owned packed arrays.
///
/// On complete success: `pts` holds `8 * n` floats (each Bézier is
/// `p0 c1 c2 p1`, two coordinates each, in parse order), `offs[s]` is the
/// Bézier index where subpath `s` begins, `flags[s]` is `1` when subpath
/// `s` was closed by an explicit `Z`/`z`, and `(n, m)` are the exact Bézier
/// and subpath counts. Every failure path writes nothing past the capacity
/// pre-check (the capacity check runs before the first store), so no partial
/// result is ever produced.
fn svgpath_packed(
    s: &str,
    pts: &mut [f64],
    offs: &mut [i64],
    flags: &mut [i64],
) -> Result<(usize, usize), i32> {
    let toks = tokenize(s.as_bytes()).map_err(|_| ERR_PARSE)?;
    let subs = parse_path(&toks).map_err(|_| ERR_PARSE)?;
    let total_beziers: usize = subs.iter().map(|sub| sub.beziers.len()).sum();
    // Every coordinate must be finite; otherwise hand the input back to the
    // Python fallback rather than emit a non-representable value (the same
    // check `svgpath_json` performs before serializing).
    for sub in &subs {
        for bez in &sub.beziers {
            for v in bez {
                if !v.is_finite() {
                    return Err(ERR_PARSE);
                }
            }
        }
    }
    // Capacity pre-check: reject before writing anything, so a mis-sized
    // buffer is a clean rejection rather than a partial write.
    let pts_needed = 8 * total_beziers;
    if pts.len() < pts_needed || offs.len() < subs.len() || flags.len() < subs.len() {
        return Err(ERR_SIZE);
    }
    let mut bez = 0usize;
    for (si, sub) in subs.iter().enumerate() {
        offs[si] = bez as i64;
        flags[si] = if sub.closed { 1 } else { 0 };
        for b in &sub.beziers {
            pts[bez * 8..bez * 8 + 8].copy_from_slice(b);
            bez += 1;
        }
    }
    Ok((total_beziers, subs.len()))
}

/// Parse the path and write the packed subpath arrays (cubic points +
/// subpath offsets + closed flags) into caller-owned buffers. Returns `0`
/// on complete success (publishing the exact Bézier and subpath counts);
/// on any rejection (`ERR_NULL` / `ERR_INPUT` / `ERR_PARSE` / `ERR_SIZE`)
/// neither count output is touched and no usable partial output exists.
///
/// # Safety
///
/// `text` must be readable for `text_len` bytes (null only when
/// `text_len == 0`); `pts` writable for `pts_capacity` `f64`s; `offs` and
/// `flags` writable for their capacities; `out_pts` and `out_subs`
/// writable.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_svgpath_packed(
    text: *const u8,
    text_len: usize,
    pts: *mut f64,
    pts_capacity: usize,
    offs: *mut i64,
    offs_capacity: usize,
    flags: *mut i64,
    flags_capacity: usize,
    out_pts: *mut i64,
    out_subs: *mut i64,
) -> i32 {
    if text_len > SVG_MAX_INPUT_BYTES {
        return ERR_INPUT;
    }
    if out_pts.is_null() || out_subs.is_null() {
        return ERR_NULL;
    }
    let slice = match span(text, text_len) {
        Some(s) => s,
        None => return ERR_NULL,
    };
    let s = match std::str::from_utf8(slice) {
        Ok(s) => s,
        Err(_) => return ERR_INPUT,
    };
    let pts = match unsafe { crate::checked_slice_mut(pts, pts_capacity) } {
        Some(out) => out,
        None => return ERR_SIZE,
    };
    let offs = match unsafe { crate::checked_slice_mut(offs, offs_capacity) } {
        Some(out) => out,
        None => return ERR_SIZE,
    };
    let flags = match unsafe { crate::checked_slice_mut(flags, flags_capacity) } {
        Some(out) => out,
        None => return ERR_SIZE,
    };
    match svgpath_packed(s, pts, offs, flags) {
        Ok((beziers, subpaths)) => {
            let Some(out_beziers) = i64::try_from(beziers).ok() else {
                return ERR_SIZE;
            };
            let Some(out_subpaths) = i64::try_from(subpaths).ok() else {
                return ERR_SIZE;
            };
            *out_pts = out_beziers;
            *out_subs = out_subpaths;
            0
        }
        Err(code) => code,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn p(d: &str) -> Result<Vec<Sub>, ()> {
        let toks = tokenize(d.as_bytes())?;
        parse_path(&toks)
    }

    /// Evaluate a cubic Bézier `b` at `t` (mirrors the Python `_cubic` helper).
    fn cubic(b: &[f64; 8], t: f64) -> (f64, f64) {
        let u = 1.0 - t;
        (
            u * u * u * b[0] + 3.0 * u * u * t * b[2] + 3.0 * u * t * t * b[4] + t * t * t * b[6],
            u * u * u * b[1] + 3.0 * u * u * t * b[3] + 3.0 * u * t * t * b[5] + t * t * t * b[7],
        )
    }

    #[test]
    fn m_then_implicit_lines() {
        let subs = p("M 0 0 10 0 10 10").expect("parse");
        assert_eq!(subs.len(), 1);
        let bez = &subs[0].beziers;
        assert_eq!(bez.len(), 2, "expected two lines, got {}", bez.len());
        assert!(!subs[0].closed);
        // first line (0,0)->(10,0)
        let b0 = &bez[0];
        assert_eq!(b0[0], 0.0);
        assert_eq!(b0[1], 0.0);
        assert_eq!(b0[6], 10.0);
        assert_eq!(b0[7], 0.0);
        assert!((b0[2] - 10.0 / 3.0).abs() < 1e-12);
        assert!((b0[4] - 20.0 / 3.0).abs() < 1e-12);
        // second line (10,0)->(10,10)
        let b1 = &bez[1];
        assert_eq!(b1[0], 10.0);
        assert_eq!(b1[1], 0.0);
        assert_eq!(b1[6], 10.0);
        assert_eq!(b1[7], 10.0);
    }

    #[test]
    fn q_elevates_exactly() {
        let subs = p("M 0 0 Q 2 2 4 0").expect("parse");
        let b = &subs[0].beziers[0];
        assert_eq!(b[0], 0.0);
        assert_eq!(b[1], 0.0);
        assert!((b[2] - (0.0 + (2.0 / 3.0) * (2.0 - 0.0))).abs() < 1e-12);
        assert!((b[3] - (0.0 + (2.0 / 3.0) * (2.0 - 0.0))).abs() < 1e-12);
        assert!((b[4] - (4.0 + (2.0 / 3.0) * (2.0 - 4.0))).abs() < 1e-12);
        assert!((b[5] - (0.0 + (2.0 / 3.0) * (2.0 - 0.0))).abs() < 1e-12);
        assert_eq!(b[6], 4.0);
        assert_eq!(b[7], 0.0);
    }

    /// The named mutation target. The centre of this arc is (0,0), which the
    /// F.6 formula reaches ONLY via the chord-midpoint term
    /// `((x1+x2)/2, (y1+y2)/2) = (0.5, 0.5)`. Dropping that term moves the
    /// centre to (-0.5, -0.5) and the curve leaves the unit circle, so the
    /// on-circle / far-quarter assertions below go RED.
    #[test]
    fn off_origin_circle_quarter_is_on_unit_circle() {
        let subs = p("M 1 0 A 1 1 0 0 1 0 1").expect("parse");
        assert_eq!(subs.len(), 1);
        let bez = &subs[0].beziers;
        assert_eq!(
            bez.len(),
            1,
            "a 90-degree arc is one segment, got {}",
            bez.len()
        );
        assert_eq!(
            bez.len(),
            1,
            "a 90-degree arc is one segment, got {}",
            bez.len()
        );
        let b = &bez[0];
        // The endpoints are the input points; the arc math goes through trig
        // so compare with a small tolerance (the Python test uses 1e-9).
        assert!((b[0] - 1.0).abs() < 1e-9);
        assert!((b[1] - 0.0).abs() < 1e-9);
        assert!((b[6] - 0.0).abs() < 1e-9);
        assert!((b[7] - 1.0).abs() < 1e-9);
        for t in [0.25, 0.5, 0.75] {
            let (x, y) = cubic(b, t);
            let dev = (x * x + y * y).sqrt() - 1.0;
            assert!(dev.abs() <= 3e-4, "t={}: ({}, {}) dev={}", t, x, y, dev);
            assert!(x > 0.3 && y > 0.3, "t={}: ({}, {}) wrong side", t, x, y);
        }
        let (xm, ym) = cubic(b, 0.5);
        assert!(xm > 0.5 && ym > 0.5, "({}, {}) not the far quarter", xm, ym);
        // the sweep=0 arc is the OTHER quarter circle (centre (1,1)); its
        // midpoint is off the unit circle
        let other = p("M 1 0 A 1 1 0 0 0 0 1").expect("parse");
        let (ox, oy) = cubic(&other[0].beziers[0], 0.5);
        assert!(
            ((ox * ox + oy * oy).sqrt() - 1.0).abs() > 0.1,
            "({}, {}) should be off the unit circle",
            ox,
            oy
        );
    }

    #[test]
    fn full_circle_within_bound() {
        let r = 10.0;
        let d = format!(
            "M {} 0 A {} {} 0 1 1 {} 0 A {} {} 0 1 1 {} 0",
            r, r, r, -r, r, r, r
        );
        let subs = p(&d).expect("parse");
        assert_eq!(subs.len(), 1);
        let bez = &subs[0].beziers;
        assert_eq!(bez.len(), 4, "expected four arc beziers, got {}", bez.len());
        let mut worst: f64 = 0.0;
        for b in bez {
            for k in 0..51 {
                let t = k as f64 / 50.0;
                let (x, y) = cubic(b, t);
                let dev = (x * x + y * y).sqrt() - r;
                worst = worst.max(dev.abs());
            }
        }
        assert!(
            worst <= 3e-4 * r,
            "max radial deviation {} exceeds {}",
            worst,
            3e-4 * r
        );
    }

    #[test]
    fn large_sweep_flags_pick_the_correct_arc() {
        let up = p("M 1 0 A 1 1 0 0 1 -1 0").expect("parse");
        let low = p("M 1 0 A 1 1 0 0 0 -1 0").expect("parse");
        let bu = &up[0].beziers[0];
        let bl = &low[0].beziers[0];
        assert!((bu[6] - 0.0).abs() < 1e-9);
        assert!((bu[7] - 1.0).abs() < 1e-9);
        assert!((bl[6] - 0.0).abs() < 1e-9);
        assert!((bl[7] + 1.0).abs() < 1e-9);
    }

    #[test]
    fn zero_radius_arc_is_a_line() {
        let subs = p("M 0 0 A 0 0 0 0 0 4 3").expect("parse");
        let b = &subs[0].beziers[0];
        assert_eq!(b[0], 0.0);
        assert_eq!(b[1], 0.0);
        assert_eq!(b[6], 4.0);
        assert_eq!(b[7], 3.0);
        assert!((b[2] - 4.0 / 3.0).abs() < 1e-12);
        assert!((b[3] - 3.0 / 3.0).abs() < 1e-12);
        assert!((b[4] - 8.0 / 3.0).abs() < 1e-12);
        assert!((b[5] - 6.0 / 3.0).abs() < 1e-12);
    }

    #[test]
    fn relative_matches_absolute() {
        let a = p("M 0 0 L 10 0 H 20 V 10").expect("parse");
        let b = p("m 0 0 l 10 0 h 10 v 10").expect("parse");
        assert_eq!(a.len(), 1);
        assert_eq!(b.len(), 1);
        let ab = &a[0].beziers;
        let bb = &b[0].beziers;
        assert_eq!(ab.len(), bb.len());
        for (ai, bi) in ab.iter().zip(bb.iter()) {
            for (k, (va, vb)) in ai.iter().zip(bi.iter()).enumerate() {
                assert!((va - vb).abs() < 1e-12, "mismatch at seg {:?} {}", k, "rel");
            }
        }
    }

    #[test]
    fn z_closes_the_subpath() {
        let subs = p("M 0 0 L 10 0 L 10 10 Z").expect("parse");
        assert_eq!(subs.len(), 1);
        assert!(subs[0].closed);
        let bez = &subs[0].beziers;
        assert_eq!(bez.len(), 3, "expected three lines, got {}", bez.len());
        let last = &bez[2];
        assert_eq!(last[0], 10.0);
        assert_eq!(last[1], 10.0);
        assert_eq!(last[6], 0.0);
        assert_eq!(last[7], 0.0);
    }

    #[test]
    fn s_reflects_previous_c_control() {
        let subs = p("M 0 0 C 1 2 3 4 5 6 S 7 8 9 10").expect("parse");
        let bez = &subs[0].beziers;
        assert_eq!(bez.len(), 2);
        let b1 = &bez[1];
        // c1 = 2*(5,6) - (3,4) = (7,8); c2 = (7,8); end = (9,10)
        assert!((b1[2] - 7.0).abs() < 1e-12);
        assert!((b1[3] - 8.0).abs() < 1e-12);
        assert!((b1[4] - 7.0).abs() < 1e-12);
        assert!((b1[5] - 8.0).abs() < 1e-12);
        assert!((b1[6] - 9.0).abs() < 1e-12);
        assert!((b1[7] - 10.0).abs() < 1e-12);
    }

    #[test]
    fn rejects_malformed_paths() {
        // a command before any moveto
        assert!(p("L 10 0").is_err());
        // a command with too few parameters
        assert!(p("M 0 0 L 10").is_err());
        // a number outside a command
        assert!(p("M 0 0 5 L 1 2").is_err());
    }

    #[test]
    fn size_query_matches_encode() {
        let m = "M 0 0 L 10 0 10 10 Q 20 20 30 10";
        let bytes = m.as_bytes();
        let size = unsafe { narwhal_pure_core_svgpath_size(bytes.as_ptr(), bytes.len()) };
        assert!(size > 0, "size must be positive for {m:?}");
        let mut buf = vec![0u8; size as usize];
        let rc = unsafe {
            narwhal_pure_core_svgpath_encode(
                bytes.as_ptr(),
                bytes.len(),
                buf.as_mut_ptr(),
                buf.len(),
            )
        };
        assert_eq!(rc, 0, "encode must succeed for {m:?}");
        let json = std::str::from_utf8(&buf).expect("valid utf-8");
        assert_eq!(json, svgpath_json(m).expect("svgpath_json accepted it"));
        // a malformed path: both exports report the stable rejection code
        let bad = "L 10".as_bytes();
        assert_eq!(
            unsafe { narwhal_pure_core_svgpath_size(bad.as_ptr(), bad.len()) },
            ERR_PARSE as i64
        );
        let small = &mut [0u8; 3];
        assert_eq!(
            unsafe {
                narwhal_pure_core_svgpath_encode(
                    bad.as_ptr(),
                    bad.len(),
                    small.as_mut_ptr(),
                    small.len(),
                )
            },
            ERR_PARSE
        );
    }

    #[test]
    fn rejects_null_non_utf8_and_oversized() {
        assert_eq!(
            unsafe { narwhal_pure_core_svgpath_size(std::ptr::null(), 5) },
            ERR_NULL as i64
        );
        let bad = [0xFFu8, 0xFE, 0xFD];
        assert_eq!(
            unsafe { narwhal_pure_core_svgpath_size(bad.as_ptr(), bad.len()) },
            ERR_INPUT as i64
        );
        let big = vec![b'a'; SVG_MAX_INPUT_BYTES + 1];
        assert_eq!(
            unsafe { narwhal_pure_core_svgpath_size(big.as_ptr(), big.len()) },
            ERR_INPUT as i64
        );
        let dangling = std::ptr::NonNull::<u8>::dangling().as_ptr();
        assert_eq!(
            unsafe { narwhal_pure_core_svgpath_size(dangling, usize::MAX) },
            ERR_INPUT as i64
        );
        let mut out = [0xA5u8; 8];
        assert_eq!(
            unsafe {
                narwhal_pure_core_svgpath_encode(std::ptr::null(), 5, out.as_mut_ptr(), out.len())
            },
            ERR_NULL
        );
        assert_eq!(out, [0xA5; 8]);
    }

    // --- Q117-03 packed-output ABI ----------------------------------------

    /// Run the packed ABI with the given capacities and return the return
    /// code, the arrays and the published counts (sentinels stay -1 unless
    /// the call succeeded).
    fn packed_call(
        d: &str,
        pts_cap: usize,
        off_cap: usize,
    ) -> (i32, Vec<f64>, Vec<i64>, Vec<i64>, i64, i64) {
        let bytes = d.as_bytes();
        let mut pts = vec![0.0f64; pts_cap];
        let mut offs = vec![0i64; off_cap];
        let mut flags = vec![0i64; off_cap];
        let mut out_pts = -1i64;
        let mut out_subs = -1i64;
        let rc = unsafe {
            narwhal_pure_core_svgpath_packed(
                bytes.as_ptr(),
                bytes.len(),
                pts.as_mut_ptr(),
                pts_cap,
                offs.as_mut_ptr(),
                off_cap,
                flags.as_mut_ptr(),
                off_cap,
                &mut out_pts,
                &mut out_subs,
            )
        };
        (rc, pts, offs, flags, out_pts, out_subs)
    }

    #[test]
    fn packed_matches_the_json_route() {
        // Same parse core as the JSON route; the packed output must be the
        // exact subpath structure the JSON route emits (order, counts,
        // closedness).
        let d = "M 0 0 L 10 0 Z M 20 0 C 1 2 3 4 5 6 Q 7 8 9 10";
        let (rc, pts, offs, flags, n_pts, n_subs) = packed_call(d, 256, 16);
        assert_eq!(rc, 0, "packed call must succeed");
        assert_eq!(n_subs, 2, "two subpaths");
        assert_eq!(n_pts, 4, "two lines + two cubic/quad segments");
        assert_eq!(offs[0], 0, "first subpath starts at Bézier 0");
        assert_eq!(offs[1], 2, "second subpath starts at Bézier 2");
        assert_eq!(flags[0], 1, "first subpath was closed by Z");
        assert_eq!(flags[1], 0, "second subpath is open");
        // First Bézier: the line (0,0) -> (10,0).
        assert_eq!(pts[0], 0.0);
        assert_eq!(pts[1], 0.0);
        assert_eq!(pts[6], 10.0);
        assert_eq!(pts[7], 0.0);
        assert!((pts[2] - 10.0 / 3.0).abs() < 1e-12);
        assert!((pts[4] - 20.0 / 3.0).abs() < 1e-12);
        // Cross-check against the JSON route for the SAME input.
        let json = svgpath_json(d).expect("the JSON route accepts it");
        assert!(json.contains("\"closed\":true"));
        assert!(json.contains("\"closed\":false"));
    }

    #[test]
    fn packed_exact_capacity_succeeds() {
        // "M 0 0 L 10 0" -> 1 subpath, 1 Bézier -> exactly 8 floats and 1
        // offset/flag. The capacity pre-check must accept the exact bound.
        let (rc, pts, offs, flags, n_pts, n_subs) = packed_call("M 0 0 L 10 0", 8, 1);
        assert_eq!(rc, 0, "exact capacity must succeed");
        assert_eq!(n_pts, 1);
        assert_eq!(n_subs, 1);
        assert_eq!(offs[0], 0);
        assert_eq!(flags[0], 0);
        assert_eq!(pts[6], 10.0);
    }

    #[test]
    fn packed_insufficient_capacity_is_rejected_and_counts_untouched() {
        // Two Béziers need 16 floats; give 8 -> clean reject.
        let (rc, _pts, _offs, _flags, n_pts, n_subs) = packed_call("M 0 0 L 10 0 L 10 10", 8, 4);
        assert_eq!(rc, ERR_SIZE);
        assert_eq!(n_pts, -1, "no Bézier count may be published");
        assert_eq!(n_subs, -1, "no subpath count may be published");
        // Two subpaths need 2 offsets; give 1 -> clean reject.
        let (rc2, _p, _o, _f, n_pts2, n_subs2) =
            packed_call("M 0 0 L 10 0 Z M 20 0 L 30 0", 128, 1);
        assert_eq!(rc2, ERR_SIZE);
        assert_eq!(n_pts2, -1);
        assert_eq!(n_subs2, -1);
    }

    #[test]
    fn packed_malformed_is_rejected_and_counts_untouched() {
        for bad in ["L 10", "M 0 0 L 10", "M 0 0 5 L 1 2"] {
            let (rc, _p, _o, _f, n_pts, n_subs) = packed_call(bad, 64, 4);
            assert_eq!(rc, ERR_PARSE, "input {bad:?}");
            assert_eq!(n_pts, -1, "input {bad:?}");
            assert_eq!(n_subs, -1, "input {bad:?}");
        }
    }

    #[test]
    fn packed_empty_input_is_zero_subpaths() {
        let (rc, _pts, _offs, _flags, n_pts, n_subs) = packed_call("", 4, 1);
        assert_eq!(rc, 0);
        assert_eq!(n_pts, 0);
        assert_eq!(n_subs, 0);
    }

    #[test]
    fn packed_empty_subpaths_share_offset_zero() {
        // "M 0 0 Z M 1 1" -> two subpaths, zero Béziers: both offsets are 0
        // (an empty subpath starts where the previous one ends), both flags
        // reflect their own Z (or lack of one).
        let (rc, _pts, offs, flags, n_pts, n_subs) = packed_call("M 0 0 Z M 1 1", 4, 2);
        assert_eq!(rc, 0);
        assert_eq!(n_pts, 0);
        assert_eq!(n_subs, 2);
        assert_eq!(offs[0], 0);
        assert_eq!(offs[1], 0);
        assert_eq!(flags[0], 1);
        assert_eq!(flags[1], 0);
    }

    #[test]
    fn packed_refuses_null_text_and_oversized_input() {
        let mut out = [0i64; 8];
        let rc = unsafe {
            narwhal_pure_core_svgpath_packed(
                std::ptr::null(),
                5,
                std::ptr::null_mut(),
                0,
                std::ptr::null_mut(),
                0,
                std::ptr::null_mut(),
                0,
                out.as_mut_ptr(),
                out.as_mut_ptr(),
            )
        };
        assert_eq!(rc, ERR_NULL);
        assert_eq!(out[0], 0, "no count may be published");
        let big = vec![b'a'; SVG_MAX_INPUT_BYTES + 1];
        let rc2 = unsafe {
            narwhal_pure_core_svgpath_packed(
                big.as_ptr(),
                big.len(),
                std::ptr::null_mut(),
                0,
                std::ptr::null_mut(),
                0,
                std::ptr::null_mut(),
                0,
                out.as_mut_ptr(),
                out.as_mut_ptr(),
            )
        };
        assert_eq!(rc2, ERR_INPUT);
        assert_eq!(out[0], 0, "no count may be published");
    }
}
