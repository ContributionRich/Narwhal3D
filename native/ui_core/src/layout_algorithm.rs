//! Port of `narwhal3d/textfield.py` lines 165-271: the layout helpers for multi-line text
//! (`_wrap_paragraph`, `wrap_spans`, `caret_xy`, `index_at`, `selection_rects`).
//! RUST-PORTS-02 packet 2.
//!
//! TEST-ONLY reuse for layout measurement-contract qualification. Not in the
//! production ABI; no rasterizer, callbacks across FFI, or replay protocol.
//! This module does not depend on `textfield_model.rs`: its inputs are plain code points,
//! spans and a measure callback.
//!
//! Domain: `text` is a slice of Python code points (`&[u32]`). Indices are Python codepoint
//! indices. Spans and indices are `i64` because the reference accepts any Python int and
//! slices them with Python semantics (negative indices wrap, out-of-range bounds clamp).
//!
//! The measure callback is the only font-dependent input. The reference calls it on specific
//! substrings, in a specific order. This port makes the same calls in the same order, so a
//! stateful or non-additive measure gives the same result. Widths are NOT assumed to be
//! additive: kerning makes `measure("AV") != measure("A") + measure("V")`. Caching
//! requires an additional purity/epoch contract; public Python callbacks have none.
//!
//! Errors (the reference raises or never returns; these are not silently changed):
//! * `NoSpans`: `caret_xy` and `index_at` index `spans[line]` on an empty list (`IndexError`).
//! * `IndexOutOfRange`: `selection_rects` evaluates `text[end]` for `end < -len(text)`
//!   (`IndexError`).
//! * `NoProgress`: non-empty paragraph exhausted while measure("") exceeds width.
//!   The current Python implementation also explicitly refuses this case, rather
//!   than the historical infinite loop. No fallback or measurement restart is used.
//!
//! `line_height` (the last parameter of the reference `caret_xy`) is never read there, so it is
//! not a parameter here. The adapter must ignore it, whatever its Python type.

/// `(start, end)` index pair. `text[start:end]` uses Python slice semantics.
pub type Span = (i64, i64);

const SPACE: u32 = 0x20;
const LF: u32 = 0x0A;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LayoutError {
    NoSpans,
    IndexOutOfRange,
    NoProgress,
}

/// Python `lo if v < lo else hi if v > hi else v`, with no assumption that `lo <= hi`.
/// (`i64::clamp` panics when `lo > hi`, and the reference relies on that case for empty spans.)
fn py_clamp(v: i64, lo: i64, hi: i64) -> i64 {
    if v < lo {
        lo
    } else if v > hi {
        hi
    } else {
        v
    }
}

/// `text[start:end]` for step 1: negative bounds add the length, bounds clamp to `[0, len]`,
/// and an end before the start gives an empty slice.
fn py_slice(text: &[u32], start: i64, end: i64) -> &[u32] {
    let n = text.len() as i64;
    let adjust = |i: i64| if i < 0 { (i + n).max(0) } else { i.min(n) };
    let s = adjust(start);
    let e = adjust(end).max(s);
    &text[s as usize..e as usize]
}

/// `text[i]` for an in-range (possibly negative) index, or `None` where Python raises IndexError.
fn py_index(text: &[u32], i: i64) -> Option<u32> {
    let n = text.len() as i64;
    let j = if i < 0 { i + n } else { i };
    if j < 0 || j >= n {
        None
    } else {
        Some(text[j as usize])
    }
}

/// `str.rstrip(" ")`: trailing ASCII spaces only (not tabs, not other whitespace).
fn rstrip_spaces(s: &[u32]) -> &[u32] {
    let mut end = s.len();
    while end > 0 && s[end - 1] == SPACE {
        end -= 1;
    }
    &s[..end]
}

/// `_wrap_paragraph`: append the visual lines of `text[p0:p1]` (no newline inside) to `out`.
fn wrap_paragraph<F: FnMut(&[u32]) -> f64>(
    text: &[u32],
    p0: i64,
    p1: i64,
    width: f64,
    measure: &mut F,
    out: &mut Vec<Span>,
) -> Result<(), LayoutError> {
    let mut start = p0;
    loop {
        if measure(rstrip_spaces(py_slice(text, start, p1))) <= width {
            out.push((start, p1));
            return Ok(());
        }
        if start >= p1 {
            return Err(LayoutError::NoProgress);
        }
        // Word breaks: just after a run of spaces. The fit test ignores trailing spaces.
        let mut best: Option<i64> = None;
        for j in (start + 1)..p1 {
            if text[(j - 1) as usize] == SPACE && text[j as usize] != SPACE {
                if measure(rstrip_spaces(py_slice(text, start, j))) <= width {
                    best = Some(j);
                } else {
                    break;
                }
            }
        }
        // A word longer than a line: break by characters, at least one per line.
        let best = match best {
            Some(b) => b,
            None => {
                let mut b = start + 1;
                for k in (start + 2)..=p1 {
                    if measure(py_slice(text, start, k)) <= width {
                        b = k;
                    } else {
                        break;
                    }
                }
                b
            }
        };
        out.push((start, best));
        start = best;
    }
}

/// Visual lines of `text` as `(start, end)` spans wrapped to `width`. Paragraphs split on
/// `\n`, which belongs to no span. Empty text gives `[(0, 0)]`; an empty paragraph gives `(i, i)`.
pub fn wrap_spans<F: FnMut(&[u32]) -> f64>(
    text: &[u32],
    width: f64,
    measure: &mut F,
) -> Result<Vec<Span>, LayoutError> {
    let mut spans = Vec::new();
    let mut p0: i64 = 0;
    loop {
        let nl = text[p0 as usize..]
            .iter()
            .position(|&c| c == LF)
            .map(|i| p0 + i as i64);
        let p1 = nl.unwrap_or(text.len() as i64);
        if p1 == p0 {
            spans.push((p0, p0));
        } else {
            wrap_paragraph(text, p0, p1, width, measure, &mut spans)?;
        }
        match nl {
            None => return Ok(spans),
            Some(nl) => p0 = nl + 1,
        }
    }
}

/// `(x, line)` of the caret at `index`. The caret goes on the last line whose start <= index.
pub fn caret_xy<F: FnMut(&[u32]) -> f64>(
    spans: &[Span],
    text: &[u32],
    index: i64,
    measure: &mut F,
) -> Result<(f64, usize), LayoutError> {
    let index = py_clamp(index, 0, text.len() as i64);
    let mut line = 0usize;
    for (i, &(start, _)) in spans.iter().enumerate() {
        if start <= index {
            line = i;
        } else {
            break;
        }
    }
    let &(start, end) = spans.get(line).ok_or(LayoutError::NoSpans)?;
    Ok((measure(py_slice(text, start, end.min(index))), line))
}

/// Text index whose boundary is nearest pixel `x` on visual `line`. The line index is clamped
/// to the valid range. Ties go to the earlier index. On a soft-wrapped line the line's end
/// index is excluded, because `caret_xy` draws that index at the start of the next line.
pub fn index_at<F: FnMut(&[u32]) -> f64>(
    spans: &[Span],
    text: &[u32],
    x: f64,
    line: i64,
    measure: &mut F,
) -> Result<i64, LayoutError> {
    let line = py_clamp(line, 0, spans.len() as i64 - 1);
    let line_idx = usize::try_from(line).map_err(|_| LayoutError::NoSpans)?;
    let &(start, end) = spans.get(line_idx).ok_or(LayoutError::NoSpans)?;
    let mut last = end;
    if let Some(&(next_start, _)) = spans.get(line_idx + 1) {
        if next_start == end && end > start {
            last = end - 1;
        }
    }
    let mut best = start;
    let mut best_d: Option<f64> = None;
    for k in start..=last {
        let d = (measure(py_slice(text, start, k)) - x).abs();
        if best_d.is_none_or(|bd| d < bd) {
            best = k;
            best_d = Some(d);
        }
    }
    Ok(best)
}

#[test]
fn nearest_boundary_keeps_first_tie_and_nan() {
    let spans = [(1, 3)];
    let text = [65, 66, 67];
    for (x, expected) in [
        (0.0, 1),
        (5.0, 1),
        (9.0, 2),
        (15.0, 2),
        (20.0, 3),
        (f64::NAN, 1),
    ] {
        let mut lengths = Vec::new();
        let actual = index_at(&spans, &text, x, 0, &mut |s| {
            lengths.push(s.len());
            s.len() as f64 * 10.0
        });
        assert_eq!(actual, Ok(expected));
        assert_eq!(lengths, [0, 1, 2]);
    }
}

/// Highlight rectangles `(line_number, x0, x1)`, one per visual line touched by `sel`.
/// `sel` is `None` for no selection. A line whose only selected character is its trailing
/// `\n` (including an empty paragraph inside the selection) gets a zero-width rect.
pub fn selection_rects<F: FnMut(&[u32]) -> f64>(
    spans: &[Span],
    text: &[u32],
    sel: Option<(i64, i64)>,
    measure: &mut F,
) -> Result<Vec<(usize, f64, f64)>, LayoutError> {
    let Some((lo, hi)) = sel else {
        return Ok(Vec::new());
    };
    let n = text.len() as i64;
    let mut rects = Vec::new();
    for (i, &(start, end)) in spans.iter().enumerate() {
        let a = lo.max(start);
        let b = hi.min(end);
        let newline_selected = if end < n {
            match py_index(text, end) {
                None => return Err(LayoutError::IndexOutOfRange),
                Some(c) => c == LF && lo <= end && end < hi,
            }
        } else {
            false
        };
        if a < b || newline_selected {
            let a = a.min(end);
            let b = a.max(b);
            let x0 = measure(py_slice(text, start, a));
            let x1 = measure(py_slice(text, start, b));
            rects.push((i, x0, x1));
        }
    }
    Ok(rects)
}
