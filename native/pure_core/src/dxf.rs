//! Native ASCII DXF encoder and decoder.
//!
//! Q106-06 (encoder). Two-pass checked encode (`narwhal_pure_core_dxf_size` /
//! `narwhal_pure_core_dxf_encode`) that reproduces `dxf_writer.write_dxf`
//! byte-for-byte: HEADER (`$ACADVER = AC1009`, or `AC1027` when exact cubic
//! SPLINE chains are present; `$INSUNITS = 4`), a LAYER table (one entry per
//! layer, in the caller's order), an ENTITIES section (LINE / CIRCLE / ARC /
//! POLYLINE / SPLINE), then `EOF`. Every group code sits on its own line, the value on the next, the
//! file is joined with `\n` and ends with a final `\n`, latin-1.
//!
//! Q106-07 (decoder). Two-pass checked decode (`narwhal_pure_core_dxf_decode_
//! size` / `narwhal_pure_core_dxf_decode`) that mirrors `dxf_reader.read_dxf`
//! exactly: LINE / CIRCLE / ARC / POLYLINE / LWPOLYLINE entities (a POLYLINE's
//! points live in its VERTEX/SEQEND children), the LAYER table, the `$INSUNITS`
//! integer, and the skipped kinds (bulges, BLOCK/INSERT/TEXT/...). A binary
//! sentinel, a non-`AC1009` `$ACADVER`, a truncated (odd-line) file, or an
//! unparseable value is a stable error code so the Python reader runs instead.
//! The decoded result is a self-describing packed buffer (an entity's `layer`
//! is a free string, NOT an index into the LAYER table).
//!
//! The legacy optional ABI receives premerged layers from its old adapter.
//! The required owned writer API also owns layer merging, unit/version metadata,
//! finite-input validation and Latin-1 serialization. Python only snapshots host
//! iterables, marshals opaque mapping-key identities and frames Unicode errors.
//! Both APIs share the serializer; the original C ABI remains compatible.

const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const ERR_SIZE: i32 = -3;

const T_LINE: i64 = 0;
const T_CIRCLE: i64 = 1;
const T_ARC: i64 = 2;
const T_POLYLINE: i64 = 3;
const T_SPLINE: i64 = 4;

/// A checked view over `len` bytes, or `None` on null / overflow.
fn byte_span<'a>(ptr: *const u8, len: usize) -> Option<&'a [u8]> {
    unsafe { crate::checked_slice(ptr, len) }
}

/// A checked view over `len` `f64` values, or `None` on null / overflow.
fn f64_span<'a>(ptr: *const f64, len: usize) -> Option<&'a [f64]> {
    unsafe { crate::checked_slice(ptr, len) }
}

/// A checked view over `len` `i64` values, or `None` on null / overflow.
fn i64_span<'a>(ptr: *const i64, len: usize) -> Option<&'a [i64]> {
    unsafe { crate::checked_slice(ptr, len) }
}

/// The raw DXF encoder input, exactly as the C caller passes it: pointers
/// and signed counts. Both ABI exports build one of these and hand it to
/// `validate`, so the two passes share a single validation path.
struct RawInput {
    layer_names: *const u8,
    layer_names_len: i64,
    layer_name_lens: *const i64,
    layer_colors: *const i64,
    layer_count: i64,
    entity_types: *const i64,
    layer_ids: *const i64,
    colors: *const i64,
    nums: *const f64,
    nums_len: i64,
    point_counts: *const i64,
    closed_flags: *const i64,
    entity_count: i64,
}

/// The validated view over the caller-owned DXF input.
struct Dxf<'a> {
    layer_names: &'a [u8],
    layer_name_lens: &'a [i64],
    layer_colors: &'a [i64],
    entity_types: &'a [i64],
    layer_ids: &'a [i64],
    colors: &'a [i64],
    nums: &'a [f64],
    point_counts: &'a [i64],
    closed_flags: &'a [i64],
    header: Option<(i64, Option<i64>, bool)>,
    short_polylines: bool,
    entity_names: Option<(&'a [u8], &'a [i64])>,
}

/// The per-entity coordinate arity (the count of `f64`s this entity consumes
/// from the packed `nums` buffer).
/// The per-entity coordinate arity (the count of `f64`s this entity consumes
/// from the packed `nums` buffer): LINE 6 (two points), CIRCLE 4 (center +
/// radius), ARC 6 (center + radius + start + end), and POLYLINE/SPLINE
/// 3*points. SPLINE is a connected cubic Bezier chain, so it has 3n+1 controls.
fn entity_arity(t: i64, point_count: i64) -> Option<i64> {
    match t {
        T_LINE => Some(6),
        T_CIRCLE => Some(4),
        T_ARC => Some(6),
        T_POLYLINE => {
            if point_count < 2 {
                return None;
            }
            point_count.checked_mul(3)
        }
        T_SPLINE => {
            if point_count < 4 || (point_count - 1) % 3 != 0 {
                return None;
            }
            point_count.checked_mul(3)
        }
        _ => None,
    }
}

/// Validate that all the DXF encoder inputs are internally consistent and
/// every layer id is in range (so `build_bytes` cannot panic on a bad index).
fn validate(r: &RawInput) -> Option<Dxf<'_>> {
    if r.layer_names_len < 0 || r.layer_count < 0 || r.entity_count < 0 || r.nums_len < 0 {
        return None;
    }
    let lc = usize::try_from(r.layer_count).ok()?;
    let ec = usize::try_from(r.entity_count).ok()?;
    let nlen = usize::try_from(r.nums_len).ok()?;
    let names_len = usize::try_from(r.layer_names_len).ok()?;
    let ln = byte_span(r.layer_names, names_len)?;
    let lll = i64_span(r.layer_name_lens, lc)?;
    let lco = i64_span(r.layer_colors, lc)?;
    let et = i64_span(r.entity_types, ec)?;
    let lid = i64_span(r.layer_ids, ec)?;
    let col = i64_span(r.colors, ec)?;
    let nums = f64_span(r.nums, nlen)?;
    let pc = i64_span(r.point_counts, ec)?;
    let cf = i64_span(r.closed_flags, ec)?;
    if lll.len() != lc || lco.len() != lc {
        return None;
    }
    if et.len() != ec || lid.len() != ec || col.len() != ec || pc.len() != ec || cf.len() != ec {
        return None;
    }
    // The concatenated layer names must line up exactly with the per-name
    // lengths (no slack, no over-read).
    let mut off = 0usize;
    for l in lll {
        if *l < 0 {
            return None;
        }
        let len = usize::try_from(*l).ok()?;
        off = off.checked_add(len)?;
    }
    if off != ln.len() {
        return None;
    }
    // Walk the entities in order, checking each one's arity fits the buffer
    // and its layer id is in range. This is the single place that decides the
    // flat layout of `nums`, so `build_bytes` can index it safely.
    let mut cursor = 0usize;
    for e in 0..ec {
        let arity = usize::try_from(entity_arity(et[e], pc[e])?).ok()?;
        let end = cursor.checked_add(arity)?;
        if end > nlen {
            return None;
        }
        if lid[e] < 0 || lid[e] as usize >= lc {
            return None;
        }
        cursor = end;
    }
    if cursor != nlen {
        return None;
    }
    // Every coordinate must be finite (mirrors the Python writer, which would
    // otherwise emit `nan`/`inf` that a downstream reader rejects).
    for v in nums {
        if !v.is_finite() {
            return None;
        }
    }
    // Colors are either ordinary ACI 0..255 or the adapter's unambiguous
    // 0x1000000 + 0xRRGGBB true-color sentinel.
    if lco
        .iter()
        .chain(col.iter())
        .any(|value| !((0..=255).contains(value) || (0x100_0000..=0x1FF_FFFF).contains(value)))
    {
        return None;
    }
    Some(Dxf {
        layer_names: ln,
        layer_name_lens: lll,
        layer_colors: lco,
        entity_types: et,
        layer_ids: lid,
        colors: col,
        nums,
        point_counts: pc,
        closed_flags: cf,
        header: None,
        short_polylines: false,
        entity_names: None,
    })
}

/// A conservative upper bound on the encoded DXF file's byte length for a
/// validated input. It is cheap to compute (no per-coordinate formatting) and
/// is guaranteed to be >= the actual length, so a caller-owned buffer of this
/// size is always large enough for `build_into`. The per-coordinate bound of
/// 64 bytes is well above the maximum `format_9g` output (~24 bytes), so the
/// bound cannot be exceeded for any finite coordinate.
fn capacity_bound(d: &Dxf) -> usize {
    let lc = d.layer_name_lens.len();
    let ec = d.entity_types.len();
    let nc = d.nums.len();
    let names: usize = d.layer_name_lens.iter().map(|l| *l as usize).sum();
    // Layer names are emitted once in the table, once per entity, and once for
    // every POLYLINE vertex. Count those repetitions explicitly: a single long
    // layer name reused by many entities must not under-allocate the FFI output.
    let mut emitted_names = names;
    for e in 0..ec {
        let lid = d.layer_ids[e] as usize;
        let repeats = if d.entity_types[e] == T_POLYLINE {
            1usize.saturating_add(d.point_counts[e] as usize)
        } else {
            1
        };
        let length = d
            .entity_names
            .map_or(d.layer_name_lens[lid], |(_, lengths)| lengths[e]);
        emitted_names = emitted_names.saturating_add((length as usize).saturating_mul(repeats));
    }
    // All arithmetic saturates rather than wrapping. Valid adapter-bounded
    // inputs remain far below usize::MAX; a hostile oversized ABI call is
    // rejected by the caller-capacity check instead of receiving a short buffer.
    512usize
        .saturating_add(128usize.saturating_mul(lc))
        .saturating_add(256usize.saturating_mul(ec))
        .saturating_add(64usize.saturating_mul(nc))
        .saturating_add(emitted_names)
}

/// Serialize the complete R12 ASCII DXF file into a caller-owned buffer,
/// writing it EXACTLY ONCE and returning the exact byte count. This is the
/// single source of truth for the output: the legacy encode and the one-pass
/// `dxf_encode_into` both call it, so they structurally cannot drift apart.
///
/// Requires `out.len() >= capacity_bound(d)` (the caller guarantees this; the
/// bound is conservative, so the buffer is always large enough). The caller
/// slices to the returned count.
fn build_into(d: &Dxf, out: &mut [u8]) -> usize {
    let mut p = 0usize;
    // `put` writes `src` into `out` at position `p` and returns the new
    // position (`copy_from_slice` returns `()`, so the length is advanced
    // here). A nested function (not a closure) so it cannot capture `p`/`out`
    // and conflict with the reassignment below.
    fn put(out: &mut [u8], p: usize, src: &[u8]) -> usize {
        out[p..p + src.len()].copy_from_slice(src);
        p + src.len()
    }
    // `emit` writes one DXF group pair: the code line, then the value line.
    fn emit_raw(out: &mut [u8], p: usize, code: &str, value: &[u8]) -> usize {
        let mut p = put(out, p, code.as_bytes());
        out[p] = b'\n';
        p += 1;
        p = put(out, p, value);
        out[p] = b'\n';
        p += 1;
        p
    }
    fn emit(out: &mut [u8], p: usize, code: &str, value: &str) -> usize {
        emit_raw(out, p, code, value.as_bytes())
    }
    // --- HEADER ---------------------------------------------------------
    p = emit(out, p, "0", "SECTION");
    p = emit(out, p, "2", "HEADER");
    p = emit(out, p, "9", "$ACADVER");
    let modern = d.entity_types.contains(&T_SPLINE)
        || d.layer_colors.iter().any(|color| *color > 0xFF_FFFF)
        || d.colors.iter().any(|color| *color > 0xFF_FFFF);
    let (insunits, measurement, modern) = d.header.unwrap_or((4, None, modern));
    let version = if modern { "AC1027" } else { "AC1009" };
    p = emit(out, p, "1", version);
    p = emit(out, p, "9", "$INSUNITS");
    p = emit(out, p, "70", &insunits.to_string());
    if let Some(measurement) = measurement {
        p = emit(out, p, "9", "$MEASUREMENT");
        p = emit(out, p, "70", &measurement.to_string());
    }
    p = emit(out, p, "0", "ENDSEC");

    // --- TABLES (LAYER) -------------------------------------------------
    p = emit(out, p, "0", "SECTION");
    p = emit(out, p, "2", "TABLES");
    p = emit(out, p, "0", "TABLE");
    p = emit(out, p, "2", "LAYER");
    p = emit(out, p, "70", &d.layer_name_lens.len().to_string());
    let mut noff = 0usize;
    for i in 0..d.layer_name_lens.len() {
        let len = d.layer_name_lens[i] as usize;
        let raw = &d.layer_names[noff..noff + len];
        let name = if d.header.is_some() {
            raw
        } else {
            std::str::from_utf8(raw).unwrap_or("").as_bytes()
        };
        p = emit(out, p, "0", "LAYER");
        p = emit_raw(out, p, "2", name);
        p = emit(out, p, "70", "0");
        let is_true_color = d.layer_colors[i] > 0xFF_FFFF;
        let layer_color = if is_true_color {
            d.layer_colors[i] - 0x100_0000
        } else {
            d.layer_colors[i]
        }
        .to_string();
        p = emit(
            out,
            p,
            if is_true_color { "420" } else { "62" },
            &layer_color,
        );
        p = emit(out, p, "6", "CONTINUOUS");
        noff += len;
    }
    p = emit(out, p, "0", "ENDTAB");
    p = emit(out, p, "0", "ENDSEC");

    // --- ENTITIES -------------------------------------------------------
    p = emit(out, p, "0", "SECTION");
    p = emit(out, p, "2", "ENTITIES");
    let mut offsets = Vec::with_capacity(d.layer_name_lens.len());
    let mut offset = 0usize;
    for length in d.layer_name_lens {
        offsets.push(offset);
        offset += *length as usize;
    }
    let mut off = 0usize;
    let mut entity_name_offset = 0usize;
    for e in 0..d.entity_types.len() {
        let t = d.entity_types[e];
        let lid = d.layer_ids[e] as usize;
        // Recompute the layer name from the concatenated buffer (the layer
        // ids were validated in range, so this is safe).
        let loff = offsets[lid];
        let ll = d.layer_name_lens[lid] as usize;
        let raw = if let Some((names, lengths)) = d.entity_names {
            let end = entity_name_offset + lengths[e] as usize;
            let name = &names[entity_name_offset..end];
            entity_name_offset = end;
            name
        } else {
            &d.layer_names[loff..loff + ll]
        };
        let lname = if d.header.is_some() {
            raw
        } else {
            std::str::from_utf8(raw).unwrap_or("").as_bytes()
        };
        let is_true_color = d.colors[e] > 0xFF_FFFF;
        let color = if is_true_color {
            d.colors[e] - 0x100_0000
        } else {
            d.colors[e]
        }
        .to_string();
        let color_code = if is_true_color { "420" } else { "62" };
        let n = d.nums;
        let fmt = |i: usize| crate::obj::format_9g(n[i]);

        if t == T_LINE {
            p = emit(out, p, "0", "LINE");
            p = emit_raw(out, p, "8", lname);
            p = emit(out, p, color_code, &color);
            p = emit(out, p, "10", &fmt(off));
            p = emit(out, p, "20", &fmt(off + 1));
            p = emit(out, p, "30", &fmt(off + 2));
            p = emit(out, p, "11", &fmt(off + 3));
            p = emit(out, p, "21", &fmt(off + 4));
            p = emit(out, p, "31", &fmt(off + 5));
        } else if t == T_CIRCLE {
            p = emit(out, p, "0", "CIRCLE");
            p = emit_raw(out, p, "8", lname);
            p = emit(out, p, color_code, &color);
            p = emit(out, p, "10", &fmt(off));
            p = emit(out, p, "20", &fmt(off + 1));
            p = emit(out, p, "30", &fmt(off + 2));
            p = emit(out, p, "40", &fmt(off + 3));
        } else if t == T_ARC {
            p = emit(out, p, "0", "ARC");
            p = emit_raw(out, p, "8", lname);
            p = emit(out, p, color_code, &color);
            p = emit(out, p, "10", &fmt(off));
            p = emit(out, p, "20", &fmt(off + 1));
            p = emit(out, p, "30", &fmt(off + 2));
            p = emit(out, p, "40", &fmt(off + 3));
            p = emit(out, p, "50", &fmt(off + 4));
            p = emit(out, p, "51", &fmt(off + 5));
        } else if t == T_POLYLINE {
            let pc = d.point_counts[e] as usize;
            p = emit(out, p, "0", "POLYLINE");
            p = emit_raw(out, p, "8", lname);
            p = emit(out, p, color_code, &color);
            p = emit(out, p, "66", "1");

            p = emit(out, p, "70", if d.closed_flags[e] != 0 { "1" } else { "0" });
            for k in 0..pc {
                p = emit(out, p, "0", "VERTEX");
                p = emit_raw(out, p, "8", lname);
                p = emit(out, p, "10", &fmt(off + 3 * k));
                p = emit(out, p, "20", &fmt(off + 3 * k + 1));
                p = emit(out, p, "30", &fmt(off + 3 * k + 2));
            }
            p = emit(out, p, "0", "SEQEND");
        } else if t == T_SPLINE {
            let pc = d.point_counts[e] as usize;
            let spans = (pc - 1) / 3;
            let knot_count = pc + 4;
            p = emit(out, p, "0", "SPLINE");
            p = emit_raw(out, p, "8", lname);
            p = emit(out, p, color_code, &color);
            p = emit(out, p, "70", if d.closed_flags[e] != 0 { "1" } else { "0" });
            p = emit(out, p, "71", "3");
            p = emit(out, p, "72", &knot_count.to_string());
            p = emit(out, p, "73", &pc.to_string());
            p = emit(out, p, "74", "0");
            for _ in 0..4 {
                p = emit(out, p, "40", "0");
            }
            for span in 1..spans {
                let knot = span.to_string();
                for _ in 0..3 {
                    p = emit(out, p, "40", &knot);
                }
            }
            let final_knot = spans.to_string();
            for _ in 0..4 {
                p = emit(out, p, "40", &final_knot);
            }
            for k in 0..pc {
                p = emit(out, p, "10", &fmt(off + 3 * k));
                p = emit(out, p, "20", &fmt(off + 3 * k + 1));
                p = emit(out, p, "30", &fmt(off + 3 * k + 2));
            }
        }
        off += if d.short_polylines && t == T_POLYLINE {
            3 * d.point_counts[e] as usize
        } else {
            entity_arity(t, d.point_counts[e]).unwrap_or(0) as usize
        };
    }
    p = emit(out, p, "0", "ENDSEC");

    // --- EOF ------------------------------------------------------------
    p = emit(out, p, "0", "EOF");

    p
}

/// Build the complete DXF file's bytes from validated input (the legacy
/// two-pass helper, now a thin wrapper over the single `build_into` so the
/// size query and both encodes share one serialization path).
fn build_bytes(d: &Dxf) -> Vec<u8> {
    let cap = capacity_bound(d);
    let mut buf = vec![0u8; cap];
    let n = build_into(d, &mut buf);
    buf.truncate(n);
    buf
}

/// Two-pass size query: the exact byte length of the encoded DXF file for a
/// valid input. Same validation as the encode export, no output.
///
/// # Safety
///
/// All pointer arguments must be either null (only valid when the
/// corresponding count is zero) or readable for the declared length.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_dxf_size(
    layer_names: *const u8,
    layer_names_len: i64,
    layer_name_lens: *const i64,
    layer_colors: *const i64,
    layer_count: i64,
    entity_types: *const i64,
    layer_ids: *const i64,
    colors: *const i64,
    nums: *const f64,
    nums_len: i64,
    point_counts: *const i64,
    closed_flags: *const i64,
    entity_count: i64,
) -> i64 {
    let r = RawInput {
        layer_names,
        layer_names_len,
        layer_name_lens,
        layer_colors,
        layer_count,
        entity_types,
        layer_ids,
        colors,
        nums,
        nums_len,
        point_counts,
        closed_flags,
        entity_count,
    };
    match validate(&r) {
        Some(d) => i64::try_from(build_bytes(&d).len()).unwrap_or(ERR_SIZE as i64),
        None => ERR_INPUT as i64,
    }
}

/// Encode a complete R12 ASCII DXF file into a caller-owned buffer.
///
/// `out` must hold exactly the bytes the encoder will produce (the same
/// length `narwhal_pure_core_dxf_size` returns). Returns `0` on success,
/// otherwise a negative stable error code.
///
/// # Safety
///
/// All pointer arguments must satisfy the same invariants as the size query,
/// and `out` must be writable for the exact encoded length.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_dxf_encode(
    layer_names: *const u8,
    layer_names_len: i64,
    layer_name_lens: *const i64,
    layer_colors: *const i64,
    layer_count: i64,
    entity_types: *const i64,
    layer_ids: *const i64,
    colors: *const i64,
    nums: *const f64,
    nums_len: i64,
    point_counts: *const i64,
    closed_flags: *const i64,
    entity_count: i64,
    out: *mut u8,
    out_len: usize,
) -> i32 {
    let r = RawInput {
        layer_names,
        layer_names_len,
        layer_name_lens,
        layer_colors,
        layer_count,
        entity_types,
        layer_ids,
        colors,
        nums,
        nums_len,
        point_counts,
        closed_flags,
        entity_count,
    };
    let d = match validate(&r) {
        Some(d) => d,
        None => return ERR_INPUT,
    };
    let bytes = build_bytes(&d);
    if out_len != bytes.len() {
        return ERR_SIZE;
    }
    if out.is_null() {
        return ERR_NULL;
    }
    let out_slice = match unsafe { crate::checked_slice_mut(out, bytes.len()) } {
        Some(out) => out,
        None => return ERR_SIZE,
    };
    out_slice.copy_from_slice(&bytes);
    0
}

/// One-pass DXF encode: serialize the complete R12 ASCII file EXACTLY ONCE
/// into a caller-owned buffer (Q117-02). The legacy two-pass route
/// (`narwhal_pure_core_dxf_size` + `narwhal_pure_core_dxf_encode`) formatted
/// every coordinate twice; this export validates/prepares once and writes the
/// file once, publishing the exact byte count only on complete success.
///
/// `out` must be writable for at least `capacity_bound` bytes (the caller
/// guarantees this); the exact used length is published in `*out_written`
/// ONLY when the encode completes. On any failure `*out_written` is left
/// untouched and no output is produced. Returns `0` on success, otherwise a
/// negative stable error code.
///
/// # Safety
///
/// All pointer arguments must satisfy the same invariants as the size query,
/// and `out` must be writable for at least `out_capacity` bytes.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_dxf_encode_into(
    layer_names: *const u8,
    layer_names_len: i64,
    layer_name_lens: *const i64,
    layer_colors: *const i64,
    layer_count: i64,
    entity_types: *const i64,
    layer_ids: *const i64,
    colors: *const i64,
    nums: *const f64,
    nums_len: i64,
    point_counts: *const i64,
    closed_flags: *const i64,
    entity_count: i64,
    out: *mut u8,
    out_capacity: usize,
    out_written: *mut i64,
) -> i32 {
    if out_written.is_null() {
        return ERR_NULL;
    }
    let r = RawInput {
        layer_names,
        layer_names_len,
        layer_name_lens,
        layer_colors,
        layer_count,
        entity_types,
        layer_ids,
        colors,
        nums,
        nums_len,
        point_counts,
        closed_flags,
        entity_count,
    };
    let d = match validate(&r) {
        Some(d) => d,
        None => return ERR_INPUT,
    };
    if out_capacity < capacity_bound(&d) {
        return ERR_SIZE;
    }
    if out.is_null() {
        return ERR_NULL;
    }
    let out_slice = match unsafe { crate::checked_slice_mut(out, out_capacity) } {
        Some(out) => out,
        None => return ERR_SIZE,
    };
    let written = build_into(&d, out_slice);
    *out_written = i64::try_from(written).unwrap_or(ERR_SIZE as i64);
    0
}

// ---------------------------------------------------------------------------
// Q106-07: native ASCII DXF decoder (the read side of the round trip).
// ---------------------------------------------------------------------------

const DFX_OUT_MAGIC: u32 = 0x44584631; // distinct from OBJ's 0x33445741
const DFX_OUT_VERSION: u32 = 1;

/// The decoded entity type codes (the encoder's four plus LWPOLYLINE and exact
/// SPLINE, which only the reader models -- the writer never emits them).
const DT_LINE: i64 = 0;
const DT_CIRCLE: i64 = 1;
const DT_ARC: i64 = 2;
const DT_POLYLINE: i64 = 3;
const DT_LWPOLYLINE: i64 = 4;
const DT_SPLINE: i64 = 5;
const TRUE_COLOR_FLAG: i64 = 1 << 24;

/// Stable, distinct-from-the-encoder error codes. A non-zero return from the
/// size query or decode means the caller must run the unchanged Python reader;
/// it is never a partial result (no output is ever produced).
const ERR_DXF_TRUNCATED: i32 = -20; // odd line count / code with no value
const ERR_DXF_VERSION: i32 = -21; // binary sentinel or malformed $ACADVER
const ERR_DXF_BADVALUE: i32 = -22; // an integer/float value will not parse

/// One decoded entity. `layer` is the entity's own group-code-8 string (a
/// free value, not an index into the LAYER table); `coords` is the flat
/// coordinate run for this entity (the count is carried separately so the
/// consumer can slice it back out by type).
struct DxfEnt {
    typ: i64,
    layer: Vec<u8>,
    color: i64,
    closed: i64, // 0/1 (POLYLINE / LWPOLYLINE); 0 otherwise
    coords: Vec<f64>,
}

/// The decoded DXF as a packed buffer (see `write_to` for the exact layout).
struct DxfOut {
    insunits: Option<i64>,       // None -> the packed -1 sentinel
    layers: Vec<(Vec<u8>, i64)>, // (name, aci color) in file order
    ents: Vec<DxfEnt>,
    skipped: Vec<(Vec<u8>, i64)>, // (kind, count), sorted by kind
}

impl DxfOut {
    /// The exact packed length this result writes (the size query and the
    /// decode both derive it from the same parsed state).
    fn packed_len(&self) -> Option<usize> {
        let lc = self.layers.len();
        let ec = self.ents.len();
        let sk = self.skipped.len();
        let layer_name_bytes = self
            .layers
            .iter()
            .try_fold(0usize, |sum, (n, _)| sum.checked_add(n.len()))?;
        let ent_name_bytes = self
            .ents
            .iter()
            .try_fold(0usize, |sum, e| sum.checked_add(e.layer.len()))?;
        let sk_name_bytes = self
            .skipped
            .iter()
            .try_fold(0usize, |sum, (n, _)| sum.checked_add(n.len()))?;
        let total_coords = self
            .ents
            .iter()
            .try_fold(0usize, |sum, e| sum.checked_add(e.coords.len()))?;
        let mut total = 40usize;
        for amount in [
            lc.checked_mul(8)?,
            layer_name_bytes,
            lc.checked_mul(8)?,
            ec.checked_mul(8)?,
            ec.checked_mul(8)?,
            ent_name_bytes,
            ec.checked_mul(8)?,
            ec.checked_mul(8)?,
            ec.checked_mul(8)?,
            total_coords.checked_mul(8)?,
            sk.checked_mul(8)?,
            sk_name_bytes,
            sk.checked_mul(8)?,
        ] {
            total = total.checked_add(amount)?;
        }
        (total <= isize::MAX as usize).then_some(total)
    }

    /// Write the packed buffer into `out`, which must be exactly `packed_len`
    /// bytes. All integers are little-endian; every length run precedes the
    /// byte run it describes, and `entity_coord_counts` precedes the flat
    /// `point_run`, so an entity's coordinates are recoverable exactly.
    fn write_to(&self, out: &mut [u8]) {
        debug_assert_eq!(Some(out.len()), self.packed_len());
        let lc = self.layers.len();
        let ec = self.ents.len();
        let sk = self.skipped.len();
        out[0..4].copy_from_slice(&DFX_OUT_MAGIC.to_le_bytes());
        out[4..8].copy_from_slice(&DFX_OUT_VERSION.to_le_bytes());
        let mut o = 8usize;
        out[o..o + 8].copy_from_slice(&match self.insunits {
            Some(v) => v.to_le_bytes(),
            None => (-1i64).to_le_bytes(),
        });
        o += 8;
        out[o..o + 8].copy_from_slice(&(lc as i64).to_le_bytes());
        o += 8;
        for (name, _) in &self.layers {
            out[o..o + 8].copy_from_slice(&(name.len() as i64).to_le_bytes());
            o += 8;
        }
        for (name, _) in &self.layers {
            out[o..o + name.len()].copy_from_slice(name);
            o += name.len();
        }
        for (_, color) in &self.layers {
            out[o..o + 8].copy_from_slice(&color.to_le_bytes());
            o += 8;
        }
        out[o..o + 8].copy_from_slice(&(ec as i64).to_le_bytes());
        o += 8;
        for e in &self.ents {
            out[o..o + 8].copy_from_slice(&e.typ.to_le_bytes());
            o += 8;
        }
        for e in &self.ents {
            out[o..o + 8].copy_from_slice(&(e.layer.len() as i64).to_le_bytes());
            o += 8;
        }
        for e in &self.ents {
            out[o..o + e.layer.len()].copy_from_slice(&e.layer);
            o += e.layer.len();
        }
        for e in &self.ents {
            out[o..o + 8].copy_from_slice(&e.color.to_le_bytes());
            o += 8;
        }
        for e in &self.ents {
            out[o..o + 8].copy_from_slice(&e.closed.to_le_bytes());
            o += 8;
        }
        for e in &self.ents {
            out[o..o + 8].copy_from_slice(&(e.coords.len() as i64).to_le_bytes());
            o += 8;
        }
        for e in &self.ents {
            for v in &e.coords {
                out[o..o + 8].copy_from_slice(&v.to_le_bytes());
                o += 8;
            }
        }
        out[o..o + 8].copy_from_slice(&(sk as i64).to_le_bytes());
        o += 8;
        for (name, _) in &self.skipped {
            out[o..o + 8].copy_from_slice(&(name.len() as i64).to_le_bytes());
            o += 8;
        }
        for (name, _) in &self.skipped {
            out[o..o + name.len()].copy_from_slice(name);
            o += name.len();
        }
        for (_, count) in &self.skipped {
            out[o..o + 8].copy_from_slice(&count.to_le_bytes());
            o += 8;
        }
    }
}

/// Parse an integer group code or value (CPython `int` grammar is a superset
/// of Rust's, so every token Rust accepts is accepted identically by the
/// Python reader; tokens Rust rejects fall back to Python).
fn parse_i64_tok(tok: &[u8]) -> Option<i64> {
    std::str::from_utf8(tok).ok()?.trim().parse::<i64>().ok()
}

/// Parse a coordinate (CPython `float` grammar is a superset of Rust's).
fn parse_f64_tok(tok: &[u8]) -> Option<f64> {
    std::str::from_utf8(tok).ok()?.trim().parse::<f64>().ok()
}

fn packed_true_color(tok: &[u8]) -> Result<i64, i32> {
    let value = parse_i64_tok(tok).ok_or(ERR_DXF_BADVALUE)?;
    if !(0..=0xFF_FFFF).contains(&value) {
        return Err(ERR_DXF_BADVALUE);
    }
    Ok(TRUE_COLOR_FLAG + value)
}

/// Split a byte slice into lines the way Python text-mode iteration does
/// (universal newlines: `\n`, `\r`, and `\r\n` each end one line), WITHOUT
/// the spurious trailing empty line that a file ending in a newline would
/// otherwise gain. That guard matters here -- unlike the OBJ reader, DXF
/// pairing is position-based, so an extra empty line would turn an even line
/// count into an odd one and be mis-reported as a truncated file.
fn dxf_lines(data: &[u8]) -> Vec<&[u8]> {
    let mut out = Vec::new();
    let mut start = 0usize;
    let mut i = 0usize;
    while i < data.len() {
        match data[i] {
            b'\n' => {
                out.push(&data[start..i]);
                i += 1;
                start = i;
            }
            b'\r' => {
                out.push(&data[start..i]);
                i += 1;
                if i < data.len() && data[i] == b'\n' {
                    i += 1; // `\r\n` is one line ending, not two
                }
                start = i;
            }
            _ => i += 1,
        }
    }
    if start < data.len() {
        out.push(&data[start..]);
    }
    out
}

/// Return `[start, end)` index range of the section *name* (or `(0,0)`),
/// mirroring `dxf_reader._section_range`.
fn section_range(pairs: &[(i64, &[u8])], name: &[u8]) -> (usize, usize) {
    for i in 0..pairs.len() {
        if pairs[i].0 == 0
            && pairs[i].1 == b"SECTION"
            && i + 1 < pairs.len()
            && pairs[i + 1].1 == name
        {
            let mut j = i;
            while j < pairs.len() && !(pairs[j].0 == 0 && pairs[j].1 == b"ENDSEC") {
                j += 1;
            }
            return (i, j);
        }
    }
    (0, 0)
}

/// First index j (start <= j < hi) with group code 0, else *hi*, mirroring
/// `dxf_reader._next_code0`.
fn next_code0(pairs: &[(i64, &[u8])], start: usize, hi: usize) -> usize {
    let mut j = start;
    while j < hi && pairs[j].0 != 0 {
        j += 1;
    }
    j
}

type GeomBody = (Vec<u8>, i64, i64, Vec<f64>, i64);

/// Parse the body `[a,b)` of a LINE / CIRCLE / ARC / LWPOLYLINE entity: its
/// layer (code 8), color (code 62), and -- for LWPOLYLINE -- its closed flag
/// (code 70 bit 0), bulge count (code 42), and its ordered vertices (a new
/// vertex starts at each code 10). For the other three types, the required
/// coordinate codes in file order (the first value of each code wins, exactly
/// as the Python reader's `have[c][0]` does). The parsed body is a fixed
/// 5-tuple -- named `GeomBody` so this signature does not trip clippy's
/// `type_complexity` gate -- and the function returns it in a `Result`.
fn parse_geom_body(pairs: &[(i64, &[u8])], a: usize, b: usize, typ: i64) -> Result<GeomBody, i32> {
    let mut layer: Vec<u8> = b"0".to_vec();
    let mut color: i64 = 7;
    let mut closed: i64 = 0;
    let mut bulge: i64 = 0;
    if typ == DT_LWPOLYLINE {
        let mut pts: Vec<(f64, f64, f64)> = Vec::new();
        let mut vx = 0.0f64;
        let mut vy = 0.0f64;
        let mut vz = 0.0f64;
        let mut started = false;

        for &(c, v) in &pairs[a..b] {
            if c == 8 {
                layer = v.to_vec();
            } else if c == 62 {
                color = parse_i64_tok(v).ok_or(ERR_DXF_BADVALUE)?;
            } else if c == 420 {
                color = packed_true_color(v)?;
            } else if c == 70 {
                closed = parse_i64_tok(v).ok_or(ERR_DXF_BADVALUE)? & 1;
            } else if c == 42 {
                bulge = bulge.checked_add(1).ok_or(ERR_DXF_BADVALUE)?;
            } else if c == 10 {
                if started {
                    pts.push((vx, vy, vz));
                }
                vx = parse_f64_tok(v).ok_or(ERR_DXF_BADVALUE)?;
                vy = 0.0;
                vz = 0.0;
                started = true;
            } else if c == 20 {
                vy = parse_f64_tok(v).ok_or(ERR_DXF_BADVALUE)?;
            } else if c == 30 {
                vz = parse_f64_tok(v).ok_or(ERR_DXF_BADVALUE)?;
            }
        }
        if started {
            pts.push((vx, vy, vz));
        }
        let coord_count = pts.len().checked_mul(3).ok_or(ERR_DXF_BADVALUE)?;
        let mut coords = Vec::with_capacity(coord_count);
        for (x, y, z) in pts {
            coords.extend_from_slice(&[x, y, z]);
        }
        Ok((layer, color, closed, coords, bulge))
    } else {
        let mut have: std::collections::HashMap<i64, f64> = std::collections::HashMap::new();

        for &(c, v) in &pairs[a..b] {
            if c == 8 {
                layer = v.to_vec();
            } else if c == 62 {
                color = parse_i64_tok(v).ok_or(ERR_DXF_BADVALUE)?;
            } else if c == 420 {
                color = packed_true_color(v)?;
            } else if (c == 10
                || c == 20
                || c == 30
                || c == 11
                || c == 21
                || c == 31
                || c == 40
                || c == 50
                || c == 51)
                && !have.contains_key(&c)
            {
                have.insert(c, parse_f64_tok(v).ok_or(ERR_DXF_BADVALUE)?);
            }
        }
        let need: &[i64] = match typ {
            DT_LINE => &[10, 20, 30, 11, 21, 31],
            DT_CIRCLE => &[10, 20, 30, 40],
            DT_ARC => &[10, 20, 30, 40, 50, 51],
            _ => unreachable!("parse_geom_body called for a non-geometry type"),
        };
        let mut coords = Vec::with_capacity(need.len());
        for &code in need {
            coords.push(have.get(&code).copied().ok_or(ERR_DXF_BADVALUE)?);
        }
        Ok((layer, color, closed, coords, bulge))
    }
}

/// Parse one exact DXF SPLINE into a self-describing numeric block:
/// degree, knot/control/weight counts, full knots, xyz controls, then weights.
fn parse_spline(pairs: &[(i64, &[u8])], a: usize, b: usize) -> Result<DxfEnt, i32> {
    let mut layer = b"0".to_vec();
    let mut color = 7i64;
    let mut flags = 0i64;
    let mut degree: Option<i64> = None;
    let mut declared_knots: Option<usize> = None;
    let mut declared_points: Option<usize> = None;
    let mut fit_count = 0i64;
    let mut knots = Vec::new();
    let mut weights = Vec::new();
    let mut points: Vec<(f64, f64, f64)> = Vec::new();
    let (mut x, mut y, mut z, mut started) = (0.0, 0.0, 0.0, false);
    for &(code, value) in &pairs[a..b] {
        match code {
            8 => layer = value.to_vec(),
            62 => color = parse_i64_tok(value).ok_or(ERR_DXF_BADVALUE)?,
            420 => color = packed_true_color(value)?,
            70 => flags = parse_i64_tok(value).ok_or(ERR_DXF_BADVALUE)?,
            71 => degree = Some(parse_i64_tok(value).ok_or(ERR_DXF_BADVALUE)?),
            72 => {
                declared_knots = Some(
                    usize::try_from(parse_i64_tok(value).ok_or(ERR_DXF_BADVALUE)?)
                        .map_err(|_| ERR_DXF_BADVALUE)?,
                );
            }
            73 => {
                declared_points = Some(
                    usize::try_from(parse_i64_tok(value).ok_or(ERR_DXF_BADVALUE)?)
                        .map_err(|_| ERR_DXF_BADVALUE)?,
                );
            }
            74 => fit_count = parse_i64_tok(value).ok_or(ERR_DXF_BADVALUE)?,
            40 => knots.push(parse_f64_tok(value).ok_or(ERR_DXF_BADVALUE)?),
            41 => weights.push(parse_f64_tok(value).ok_or(ERR_DXF_BADVALUE)?),
            10 => {
                if started {
                    points.push((x, y, z));
                }
                x = parse_f64_tok(value).ok_or(ERR_DXF_BADVALUE)?;
                y = 0.0;
                z = 0.0;
                started = true;
            }
            20 => y = parse_f64_tok(value).ok_or(ERR_DXF_BADVALUE)?,
            30 => z = parse_f64_tok(value).ok_or(ERR_DXF_BADVALUE)?,
            _ => {}
        }
    }
    if started {
        points.push((x, y, z));
    }
    let degree = degree.ok_or(ERR_DXF_BADVALUE)?;
    let knot_count = declared_knots.ok_or(ERR_DXF_BADVALUE)?;
    let point_count = declared_points.ok_or(ERR_DXF_BADVALUE)?;
    if !(1..=11).contains(&degree)
        || flags & 2 != 0
        || fit_count < 0
        || point_count <= degree as usize
        || knot_count != knots.len()
        || point_count != points.len()
        || knot_count != point_count + degree as usize + 1
        || (!weights.is_empty() && weights.len() != point_count)
        || weights
            .iter()
            .any(|value| !value.is_finite() || *value <= 0.0)
        || knots.iter().any(|value| !value.is_finite())
        || knots.windows(2).any(|pair| pair[0] > pair[1])
        || points
            .iter()
            .any(|point| !point.0.is_finite() || !point.1.is_finite() || !point.2.is_finite())
    {
        return Err(ERR_DXF_BADVALUE);
    }
    let capacity = 4usize
        .checked_add(knot_count)
        .and_then(|count| count.checked_add(point_count.checked_mul(3)?))
        .and_then(|count| count.checked_add(weights.len()))
        .ok_or(ERR_DXF_BADVALUE)?;
    let mut coords = Vec::with_capacity(capacity);
    coords.extend_from_slice(&[
        degree as f64,
        knot_count as f64,
        point_count as f64,
        weights.len() as f64,
    ]);
    coords.extend_from_slice(&knots);
    for point in points {
        coords.extend_from_slice(&[point.0, point.1, point.2]);
    }
    coords.extend_from_slice(&weights);
    Ok(DxfEnt {
        typ: DT_SPLINE,
        layer,
        color,
        closed: flags & 1,
        coords,
    })
}

/// Parse a POLYLINE entity: its header (layer / color / closed flag up to the
/// first code-0), then its VERTEX children (one point each, terminated by
/// SEQEND or a non-VERTEX code-0), returning the entity and the index to
/// continue from (just past the SEQEND, mirroring `dxf_reader.read_dxf`).
fn parse_polyline(pairs: &[(i64, &[u8])], i: usize, hi: usize) -> Result<(DxfEnt, usize), i32> {
    let j = next_code0(pairs, i + 1, hi);
    let mut layer: Vec<u8> = b"0".to_vec();
    let mut color: i64 = 7;
    let mut closed: i64 = 0;

    for &(c, v) in &pairs[i + 1..j] {
        if c == 8 {
            layer = v.to_vec();
        } else if c == 62 {
            color = parse_i64_tok(v).ok_or(ERR_DXF_BADVALUE)?;
        } else if c == 420 {
            color = packed_true_color(v)?;
        } else if c == 70 {
            closed = parse_i64_tok(v).ok_or(ERR_DXF_BADVALUE)? & 1;
        }
    }
    let mut cur = j;
    let mut pts: Vec<(f64, f64, f64)> = Vec::new();
    while cur < hi && !(pairs[cur].0 == 0 && pairs[cur].1 == b"SEQEND") {
        if pairs[cur].0 == 0 && pairs[cur].1 == b"VERTEX" {
            let m = next_code0(pairs, cur + 1, hi);
            let mut cx = 0.0f64;
            let mut cy = 0.0f64;
            let mut cz = 0.0f64;
            for &(c, v) in &pairs[cur + 1..m] {
                if c == 10 {
                    cx = parse_f64_tok(v).ok_or(ERR_DXF_BADVALUE)?;
                } else if c == 20 {
                    cy = parse_f64_tok(v).ok_or(ERR_DXF_BADVALUE)?;
                } else if c == 30 {
                    cz = parse_f64_tok(v).ok_or(ERR_DXF_BADVALUE)?;
                }
            }
            pts.push((cx, cy, cz));
            cur = m;
        } else if pairs[cur].0 == 0 {
            break;
        } else {
            cur += 1;
        }
    }
    let coord_count = pts.len().checked_mul(3).ok_or(ERR_DXF_BADVALUE)?;
    let mut coords = Vec::with_capacity(coord_count);
    for (x, y, z) in pts {
        coords.extend_from_slice(&[x, y, z]);
    }
    let mut next_i = cur;
    if next_i < hi && pairs[next_i].1 == b"SEQEND" {
        next_i += 1;
    }
    Ok((
        DxfEnt {
            typ: DT_POLYLINE,
            layer,
            color,
            closed,
            coords,
        },
        next_i,
    ))
}

/// Parse a complete ASCII DXF file into its packed form, or a stable error code
/// for a binary sentinel, malformed version, truncated input, or invalid exact
/// entity. Unknown entity types remain counted rather than silently dropped.
fn parse_dxf(data: &[u8]) -> Result<DxfOut, i32> {
    // REFUSE A BINARY DXF BY ITS MAGIC SENTINEL -- before any line splitting.
    if data.starts_with(b"AutoCAD Binary DXF\r\n\x1a\0") {
        return Err(ERR_DXF_VERSION);
    }
    // Split into lines and pair code/value. An odd line count is a truncated
    // file (a group code with no value) -- the same error the Python reader
    // raises in `_pairs`.
    let lines = dxf_lines(data);
    if !lines.len().is_multiple_of(2) {
        return Err(ERR_DXF_TRUNCATED);
    }
    let mut pairs: Vec<(i64, &[u8])> = Vec::with_capacity(lines.len() / 2);
    for i in (0..lines.len()).step_by(2) {
        let code = parse_i64_tok(lines[i]).ok_or(ERR_DXF_BADVALUE)?;
        pairs.push((code, lines[i + 1]));
    }
    // ASCII generations retain group-code framing; reject only malformed names.
    for i in 0..pairs.len() {
        if pairs[i].0 == 9
            && pairs[i].1 == b"$ACADVER"
            && i + 1 < pairs.len()
            && pairs[i + 1].0 == 1
        {
            let value = pairs[i + 1].1;
            if value.len() != 6
                || !value.starts_with(b"AC")
                || !value[2..].iter().all(u8::is_ascii_digit)
            {
                return Err(ERR_DXF_VERSION);
            }
            break;
        }
    }
    // $INSUNITS -> int, or None when the header has none.
    let mut insunits: Option<i64> = None;
    for i in 0..pairs.len() {
        if pairs[i].0 == 9
            && pairs[i].1 == b"$INSUNITS"
            && i + 1 < pairs.len()
            && pairs[i + 1].0 == 70
        {
            insunits = Some(parse_i64_tok(pairs[i + 1].1).ok_or(ERR_DXF_BADVALUE)?);
            break;
        }
    }
    // LAYER table (name -> aci color 62, default 7) in file order.
    let (lo, hi) = section_range(&pairs, b"TABLES");
    let mut layers: Vec<(Vec<u8>, i64)> = Vec::new();
    let mut i = lo;
    while i < hi {
        if pairs[i].0 == 0 && pairs[i].1 == b"LAYER" {
            let mut name: Option<&[u8]> = None;
            let mut color: i64 = 7;
            let j = next_code0(&pairs, i + 1, hi);
            for &(code, value) in &pairs[i + 1..j] {
                if code == 2 {
                    name = Some(value);
                } else if code == 62 {
                    color = parse_i64_tok(value).ok_or(ERR_DXF_BADVALUE)?;
                } else if code == 420 {
                    color = packed_true_color(value)?;
                }
            }
            if let Some(n) = name {
                layers.push((n.to_vec(), color));
            }
            i = j;
        } else {
            i += 1;
        }
    }
    // ENTITIES: geometry or skipped-and-counted.
    let (lo, hi) = section_range(&pairs, b"ENTITIES");
    let mut ents: Vec<DxfEnt> = Vec::new();
    let mut skipped: std::collections::BTreeMap<Vec<u8>, i64> = std::collections::BTreeMap::new();
    let mut i = lo;
    while i < hi {
        if pairs[i].0 != 0 {
            i += 1;
            continue;
        }
        let val = pairs[i].1;
        if val == b"SECTION" || val == b"ENDSEC" || val == b"TABLE" || val == b"ENDTAB" {
            i += 1;
            continue;
        }
        if val == b"LINE" || val == b"CIRCLE" || val == b"ARC" || val == b"LWPOLYLINE" {
            let typ = if val == b"LINE" {
                DT_LINE
            } else if val == b"CIRCLE" {
                DT_CIRCLE
            } else if val == b"ARC" {
                DT_ARC
            } else {
                DT_LWPOLYLINE
            };
            let j = next_code0(&pairs, i + 1, hi);
            let (layer, color, closed, coords, bulge) = parse_geom_body(&pairs, i + 1, j, typ)?;
            if bulge > 0 {
                let count = skipped.entry(b"bulge".to_vec()).or_insert(0i64);
                *count = count.checked_add(bulge).ok_or(ERR_DXF_BADVALUE)?;
            }
            ents.push(DxfEnt {
                typ,
                layer,
                color,
                closed,
                coords,
            });
            i = j;
        } else if val == b"POLYLINE" {
            let (ent, next_i) = parse_polyline(&pairs, i, hi)?;
            ents.push(ent);
            i = next_i;
        } else if val == b"SPLINE" {
            let j = next_code0(&pairs, i + 1, hi);
            ents.push(parse_spline(&pairs, i + 1, j)?);
            i = j;
        } else {
            // BLOCK / INSERT / TEXT / POINT / ... -- count it, skip its body.
            let count = skipped.entry(val.to_vec()).or_insert(0i64);
            *count = count.checked_add(1).ok_or(ERR_DXF_BADVALUE)?;
            i = next_code0(&pairs, i + 1, hi);
        }
    }
    Ok(DxfOut {
        insunits,
        layers,
        ents,
        skipped: skipped.into_iter().collect(),
    })
}

/// Two-pass size query for the decoder: the exact packed length *data* decodes
/// to, or a negative error code when the file is one the Python reader must
/// handle. A null pointer with a zero length is an empty file (a valid result);
/// a null pointer with a positive length is a domain error.
///
/// # Safety
///
/// `data` must be readable for `data_len` bytes (null only when `data_len == 0`).
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_dxf_decode_size(
    data: *const u8,
    data_len: usize,
) -> i64 {
    if data.is_null() {
        return if data_len == 0 { 0 } else { ERR_NULL as i64 };
    }
    let bytes = match unsafe { crate::checked_slice(data, data_len) } {
        Some(bytes) => bytes,
        None => return ERR_SIZE as i64,
    };
    match parse_dxf(bytes) {
        Ok(out) => out
            .packed_len()
            .and_then(|n| i64::try_from(n).ok())
            .unwrap_or(ERR_SIZE as i64),
        Err(code) => code as i64,
    }
}

/// Decode *data* (a complete ASCII R12 DXF file) into a caller-owned packed
/// buffer of exactly the length `narwhal_pure_core_dxf_decode_size` returns.
/// Returns `0` on success, a negative error code otherwise. A null `data` with
/// a zero length is an empty file (a valid zero-entity result); a null `out`
/// is an error unless the result is empty.
///
/// # Safety
///
/// `data` must be readable for `data_len` bytes and `out` writable for
/// `out_len` bytes (null only when `out_len == 0`).
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_dxf_decode(
    data: *const u8,
    data_len: usize,
    out: *mut u8,
    out_len: usize,
) -> i32 {
    if data.is_null() {
        return if data_len == 0 { 0 } else { ERR_NULL };
    }
    let bytes = match unsafe { crate::checked_slice(data, data_len) } {
        Some(bytes) => bytes,
        None => return ERR_SIZE,
    };
    let result = match parse_dxf(bytes) {
        Ok(out) => out,
        Err(code) => return code,
    };
    let size = match result.packed_len() {
        Some(size) => size,
        None => return ERR_SIZE,
    };
    if out.is_null() {
        return if size == 0 { 0 } else { ERR_NULL };
    }
    if out_len != size {
        return ERR_SIZE;
    }
    let out_slice = match unsafe { crate::checked_slice_mut(out, size) } {
        Some(out) => out,
        None => return ERR_SIZE,
    };
    result.write_to(out_slice);
    0
}

// Required writer API. The legacy exports above keep their original contracts;
// this typed boundary retains source metadata needed for exact legacy headers.
/// Owned descriptor ABI v2 distinguishes layer-key identity from entity text.
/// Admission must check this before passing any typed descriptor to the codec.
#[no_mangle]
pub extern "C" fn narwhal_pure_core_dxf_owned_abi_version() -> u32 {
    2
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct DxfOwnedLayer {
    pub name: i64,
    pub color: i64,
}
#[repr(C)]
#[derive(Clone, Copy)]
pub struct DxfOwnedEntity {
    pub kind: i64,
    pub name: i64,
    pub color: i64,
    pub points: i64,
    pub closed: i64,
    pub true_color: i64,
    pub label: i64,
}
#[repr(C)]
pub struct DxfOwnedInput {
    pub version: u64,
    pub names: *const u8,
    pub names_len: usize,
    pub lengths: *const i64,
    pub names_count: usize,
    pub layers: *const DxfOwnedLayer,
    pub layer_count: usize,
    pub entities: *const DxfOwnedEntity,
    pub entity_count: usize,
    pub nums: *const f64,
    pub nums_len: usize,
    pub unit: *const u8,
    pub unit_len: usize,
    pub unit_mode: u8,
}
#[derive(Debug)]
struct WriterError(i64, usize);
fn writer_wtf8(mut bytes: &[u8]) -> bool {
    while !bytes.is_empty() {
        match std::str::from_utf8(bytes) {
            Ok(_) => return true,
            Err(error) => {
                let rest = &bytes[error.valid_up_to()..];
                if rest.len() < 3
                    || rest[0] != 0xed
                    || !(0xa0..=0xbf).contains(&rest[1])
                    || !(0x80..=0xbf).contains(&rest[2])
                {
                    return false;
                }
                bytes = &rest[3..];
            }
        }
    }
    true
}
fn writer_color(value: i64) -> bool {
    (0..=255).contains(&value) || (0x1000000..=0x1ffffff).contains(&value)
}
fn writer_units(name: &[u8], mode: u8) -> Result<(i64, Option<i64>), WriterError> {
    if mode == 0 {
        return Ok((4, None));
    }
    if mode != 1 {
        return Err(WriterError(-2, 0));
    }
    let (code, english) = match name {
        b"Inches" => (1, true),
        b"Feet" => (2, true),
        b"Miles" => (3, true),
        b"Millimeters" => (4, false),
        b"Centimeters" => (5, false),
        b"Meters" => (6, false),
        b"Kilometers" => (7, false),
        b"Microinches" => (8, true),
        b"Mils" => (9, true),
        b"Yards" => (10, true),
        b"Angstroms" => (11, false),
        b"Nanometers" => (12, false),
        b"Microns" => (13, false),
        b"Decimeters" => (14, false),
        b"Dekameters" => (15, false),
        b"Hectometers" => (16, false),
        b"Gigameters" => (17, false),
        _ => return Ok((0, None)),
    };
    Ok((code, Some(if english { 0 } else { 1 })))
}
struct WriterPlan {
    names: Vec<u8>,
    lengths: Vec<i64>,
    layer_colors: Vec<i64>,
    kinds: Vec<i64>,
    lids: Vec<i64>,
    colors: Vec<i64>,
    points: Vec<i64>,
    closed: Vec<i64>,
    nums: Vec<f64>,
    units: (i64, Option<i64>),
    modern: bool,
    encoding: i64,
    entity_names: Vec<u8>,
    entity_lengths: Vec<i64>,
}
impl WriterPlan {
    fn view(&self) -> Dxf<'_> {
        Dxf {
            layer_names: &self.names,
            layer_name_lens: &self.lengths,
            layer_colors: &self.layer_colors,
            entity_types: &self.kinds,
            layer_ids: &self.lids,
            colors: &self.colors,
            nums: &self.nums,
            point_counts: &self.points,
            closed_flags: &self.closed,
            header: Some((self.units.0, self.units.1, self.modern)),
            short_polylines: true,
            entity_names: if self.entity_lengths.is_empty() {
                None
            } else {
                Some((&self.entity_names, &self.entity_lengths))
            },
        }
    }
}
unsafe fn writer_plan(input: *const DxfOwnedInput) -> Result<WriterPlan, WriterError> {
    use std::collections::HashMap;
    let r = unsafe { crate::checked_slice(input, 1) }.ok_or(WriterError(-1, 0))?;
    let r = &r[0];
    if r.version != 2 {
        return Err(WriterError(-2, 0));
    }
    if r.names_count > 1_000_000
        || r.layer_count > 1_000_000
        || r.entity_count > 5_000_000
        || r.nums_len > 30_000_000
        || r.names_len > 64 * 1024 * 1024
        || r.unit_len > 64 * 1024 * 1024
    {
        return Err(WriterError(-3, 0));
    }
    let names = unsafe { crate::checked_slice(r.names, r.names_len) }.ok_or(WriterError(-1, 0))?;
    let lengths =
        unsafe { crate::checked_slice(r.lengths, r.names_count) }.ok_or(WriterError(-1, 0))?;
    let layers =
        unsafe { crate::checked_slice(r.layers, r.layer_count) }.ok_or(WriterError(-1, 0))?;
    let entities =
        unsafe { crate::checked_slice(r.entities, r.entity_count) }.ok_or(WriterError(-1, 0))?;
    let nums = unsafe { crate::checked_slice(r.nums, r.nums_len) }.ok_or(WriterError(-1, 0))?;
    let unit = unsafe { crate::checked_slice(r.unit, r.unit_len) }.ok_or(WriterError(-1, 0))?;
    let mut strings = Vec::with_capacity(lengths.len());
    let mut offset = 0usize;
    for (i, n) in lengths.iter().enumerate() {
        let len = usize::try_from(*n).map_err(|_| WriterError(-2, i))?;
        let end = offset
            .checked_add(len)
            .filter(|n| *n <= names.len())
            .ok_or(WriterError(-2, i))?;
        let text = &names[offset..end];
        if !writer_wtf8(text) {
            return Err(WriterError(-2, i));
        }
        if text.iter().any(|b| matches!(*b, 0 | 10 | 13)) {
            return Err(WriterError(-45, i));
        }
        strings.push(text);
        offset = end;
    }
    if offset != names.len() {
        return Err(WriterError(-2, 0));
    }
    let mut order = Vec::new();
    let mut colors = Vec::new();
    let mut ids = HashMap::new();
    for (i, layer) in layers.iter().enumerate() {
        if !writer_color(layer.color) {
            return Err(WriterError(-40, i));
        }
        if layer.name < 0 || layer.name as usize >= strings.len() {
            return Err(WriterError(-2, i));
        }
        if let Some(index) = ids.get(&layer.name).copied() {
            colors[index] = layer.color;
        } else {
            ids.insert(layer.name, order.len());
            order.push(layer.name);
            colors.push(layer.color);
        }
    }
    let mut p = WriterPlan {
        names: Vec::new(),
        lengths: Vec::new(),
        layer_colors: Vec::new(),
        kinds: Vec::new(),
        lids: Vec::new(),
        colors: Vec::new(),
        points: Vec::new(),
        closed: Vec::new(),
        nums: nums.to_vec(),
        units: writer_units(unit, r.unit_mode)?,
        modern: false,
        encoding: 0,
        entity_names: Vec::new(),
        entity_lengths: Vec::new(),
    };
    let mut cursor = 0usize;
    for (i, e) in entities.iter().enumerate() {
        if e.name < 0
            || e.name as usize >= strings.len()
            || e.label < 0
            || e.label as usize >= strings.len()
        {
            return Err(WriterError(-2, i));
        }
        if !matches!(e.closed, 0 | 1) || !matches!(e.true_color, 0 | 1) {
            return Err(WriterError(-46, i));
        }
        let color = if e.true_color == 1 {
            if !(0..=0xffffff).contains(&e.color) {
                return Err(WriterError(-41, i));
            }
            p.modern = true;
            0x1000000 + e.color
        } else {
            if !writer_color(e.color) {
                return Err(WriterError(-40, i));
            }
            e.color
        };
        let arity = match e.kind {
            T_LINE | T_ARC => 6,
            T_CIRCLE => 4,
            T_POLYLINE if e.points >= 0 => usize::try_from(e.points)
                .ok()
                .and_then(|n| n.checked_mul(3))
                .ok_or(WriterError(-3, i))?,
            T_SPLINE => {
                p.modern = true;
                usize::try_from(entity_arity(e.kind, e.points).ok_or(WriterError(-43, i))?)
                    .map_err(|_| WriterError(-3, i))?
            }
            _ => return Err(WriterError(-42, i)),
        };
        let end = cursor
            .checked_add(arity)
            .filter(|n| *n <= nums.len())
            .ok_or(WriterError(-2, i))?;
        for (j, value) in nums[cursor..end].iter().enumerate() {
            if !value.is_finite() {
                return Err(WriterError(-44, cursor + j));
            }
        }
        if matches!(e.kind, T_CIRCLE | T_ARC) && nums[cursor + 3] <= 0. {
            return Err(WriterError(-47, i));
        }
        cursor = end;
        let lid = if let Some(index) = ids.get(&e.name) {
            *index
        } else {
            let index = order.len();
            ids.insert(e.name, index);
            order.push(e.name);
            colors.push(7);
            index
        };
        p.kinds.push(e.kind);
        p.lids.push(lid as i64);
        p.colors.push(color);
        p.points.push(e.points);
        p.closed.push(e.closed);
    }
    if cursor != nums.len() {
        return Err(WriterError(-2, 0));
    }
    let is_latin1 = |name: &[u8]| {
        std::str::from_utf8(name).is_ok_and(|text| text.chars().all(|ch| ch as u32 <= 255))
    };
    let latin1 = order.iter().all(|id| is_latin1(strings[*id as usize]))
        && entities
            .iter()
            .all(|e| is_latin1(strings[e.label as usize]));
    p.encoding = if latin1 { 0 } else { 1 };
    for (id, color) in order.iter().zip(colors) {
        let name = strings[*id as usize];
        let start = p.names.len();
        if latin1 {
            // The test above guarantees valid UTF-8 with Latin-1 code points.
            p.names.extend(
                std::str::from_utf8(name)
                    .map_err(|_| WriterError(-2, 0))?
                    .chars()
                    .map(|ch| ch as u8),
            );
        } else {
            p.names.extend_from_slice(name);
        }
        p.lengths.push((p.names.len() - start) as i64);
        p.layer_colors.push(color);
        p.modern |= color > 0xffffff;
    }
    // Equal host mapping keys can have different serialized spellings (True
    // and 1). Table identity controls colors/order, not per-entity label text.
    if entities
        .iter()
        .any(|e| strings[e.name as usize] != strings[e.label as usize])
    {
        for e in entities {
            let name = strings[e.label as usize];
            let start = p.entity_names.len();
            if latin1 {
                p.entity_names.extend(
                    std::str::from_utf8(name)
                        .map_err(|_| WriterError(-2, 0))?
                        .chars()
                        .map(|ch| ch as u8),
                );
            } else {
                p.entity_names.extend_from_slice(name);
            }
            p.entity_lengths.push((p.entity_names.len() - start) as i64);
        }
    }
    if capacity_bound(&p.view()) > 512 * 1024 * 1024 {
        return Err(WriterError(-3, 0));
    }
    Ok(p)
}
fn writer_result(result: Result<(), WriterError>, diag: &mut [i64]) -> i32 {
    match result {
        Ok(()) => 0,
        Err(WriterError(code, index)) => {
            diag.copy_from_slice(&[code, index as i64, 0]);
            code as i32
        }
    }
}
/// Validate caller layer colors before marshaling entity point objects. This
/// preserves the original error priority without a second Python validator.
/// # Safety
/// colors spans count i64s; diagnostic spans 3 exclusively writable i64s.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_dxf_owned_colors(
    colors: *const i64,
    count: usize,
    diagnostics: *mut i64,
) -> i32 {
    let Some(diag) = (unsafe { crate::checked_slice_mut(diagnostics, 3) }) else {
        return -1;
    };
    diag.fill(0);
    let result = (|| {
        if count > 1_000_000 {
            return Err(WriterError(-3, 0));
        }
        let colors = unsafe { crate::checked_slice(colors, count) }.ok_or(WriterError(-1, 0))?;
        for (i, c) in colors.iter().enumerate() {
            if !writer_color(*c) {
                return Err(WriterError(-40, i));
            }
        }
        Ok(())
    })();
    writer_result(result, diag)
}
/// Resolve explicit/default unit metadata, identically to the required writer.
/// # Safety
/// name spans len bytes; out spans 2 exclusively writable i64s.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_dxf_owned_units(
    name: *const u8,
    len: usize,
    mode: u8,
    out: *mut i64,
) -> i32 {
    let Some(bytes) = (unsafe { crate::checked_slice(name, len) }) else {
        return -1;
    };
    let Some(out) = (unsafe { crate::checked_slice_mut(out, 2) }) else {
        return -1;
    };
    match writer_units(bytes, mode) {
        Ok((code, measurement)) => {
            out[0] = code;
            out[1] = measurement.unwrap_or(-1);
            0
        }
        Err(e) => e.0 as i32,
    }
}
/// Compute a bounded capacity without formatting coordinates or publishing bytes.
/// # Safety
/// input points to one valid typed descriptor with valid nested spans. Output
/// size spans one usize; diagnostics spans 3 exclusively writable i64s.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_dxf_owned_capacity(
    input: *const DxfOwnedInput,
    size: *mut usize,
    diagnostics: *mut i64,
) -> i32 {
    let Some(diag) = (unsafe { crate::checked_slice_mut(diagnostics, 3) }) else {
        return -1;
    };
    diag.fill(0);
    let result = (|| {
        let out = unsafe { crate::checked_slice_mut(size, 1) }.ok_or(WriterError(-1, 0))?;
        let plan = unsafe { writer_plan(input) }?;
        out[0] = capacity_bound(&plan.view());
        Ok(())
    })();
    writer_result(result, diag)
}
/// Serialize once, atomically. encoding=0 is final Latin-1 bytes. encoding=1
/// publishes the original UTF-8 text solely for host UnicodeEncodeError framing;
/// the host must refuse it, never write it as a DXF. No pointers are retained.
/// # Safety
/// input/nested spans are readable; output spans capacity bytes; written and
/// encoding each span one i64, diagnostics 3 i64s. All regions are nonoverlapping.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_dxf_owned_encode(
    input: *const DxfOwnedInput,
    out: *mut u8,
    capacity: usize,
    written: *mut i64,
    encoding: *mut i64,
    diagnostics: *mut i64,
) -> i32 {
    let Some(diag) = (unsafe { crate::checked_slice_mut(diagnostics, 3) }) else {
        return -1;
    };
    diag.fill(0);
    let result = (|| {
        let n = unsafe { crate::checked_slice_mut(written, 1) }.ok_or(WriterError(-1, 0))?;
        let enc = unsafe { crate::checked_slice_mut(encoding, 1) }.ok_or(WriterError(-1, 0))?;
        let plan = unsafe { writer_plan(input) }?;
        let bound = capacity_bound(&plan.view());
        if capacity < bound {
            return Err(WriterError(-3, 0));
        }
        let dest = unsafe { crate::checked_slice_mut(out, capacity) }.ok_or(WriterError(-1, 0))?;
        // All validation and capacity checks are complete. Formatting and copy
        // cannot report a domain failure, so publish counts only after the one
        // serialization pass. No intermediate full-file buffer is necessary.
        let length = build_into(&plan.view(), dest);
        n[0] = length as i64;
        enc[0] = plan.encoding;
        Ok(())
    })();
    writer_result(result, diag)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A small fixture: two layers (0, walls) and one of each entity type
    /// (LINE, CIRCLE, ARC, POLYLINE), matching the Python writer's own
    /// `ENTITIES` test fixture so the byte identity is checkable directly.
    struct Fix {
        layer_names: Vec<u8>,
        layer_name_lens: Vec<i64>,
        layer_colors: Vec<i64>,
        entity_types: Vec<i64>,
        layer_ids: Vec<i64>,
        colors: Vec<i64>,
        nums: Vec<f64>,
        point_counts: Vec<i64>,
        closed_flags: Vec<i64>,
    }

    fn fixture() -> Fix {
        // layer 0 -> "0" (1 byte), layer 1 -> "walls" (5 bytes)
        let mut ln = Vec::new();
        ln.extend_from_slice(b"0walls");
        let lll = vec![1, 5];
        let lco = vec![7, 1];

        // Per-entity arities (the count of f64s each entity consumes):
        //   LINE     = 6  (p0 x,y,z ; p1 x,y,z)
        //   CIRCLE   = 4  (center x,y,z ; radius)
        //   ARC      = 6  (center x,y,z ; radius ; start ; end)
        //   POLYLINE = 3*3 (3 points x,y,z)
        let nums: Vec<f64> = vec![
            0.0, 0.0, 0.0, 10.0, 10.0, 0.0, // LINE
            30.0, 20.0, 0.0, 5.0, // CIRCLE
            50.0, 20.0, 0.0, 5.0, 0.0, 90.0, // ARC
            0.0, 50.0, 0.0, 20.0, 50.0, 0.0, 20.0, 70.0, 0.0, // POLYLINE
        ];
        Fix {
            layer_names: ln,
            layer_name_lens: lll,
            layer_colors: lco,
            entity_types: vec![T_LINE, T_CIRCLE, T_ARC, T_POLYLINE],
            layer_ids: vec![0, 1, 0, 0],
            colors: vec![7, 1, 5, 7],
            nums,
            point_counts: vec![0, 0, 0, 3],
            closed_flags: vec![0, 0, 0, 1],
        }
    }

    fn spline_fixture() -> Fix {
        Fix {
            layer_names: b"Cut".to_vec(),
            layer_name_lens: vec![3],
            layer_colors: vec![0x100_0000 + 0xFF_0000],
            entity_types: vec![T_SPLINE],
            layer_ids: vec![0],
            colors: vec![0x100_0000],
            nums: vec![
                0.0, 0.0, 0.0, 1.0, 2.0, 0.0, 2.0, 2.0, 0.0, 3.0, 0.0, 0.0, 4.0, -2.0, 0.0, 5.0,
                -2.0, 0.0, 6.0, 0.0, 0.0,
            ],
            point_counts: vec![7],
            closed_flags: vec![0],
        }
    }

    fn raw(f: &Fix) -> RawInput {
        RawInput {
            layer_names: f.layer_names.as_ptr(),
            layer_names_len: f.layer_names.len() as i64,
            layer_name_lens: f.layer_name_lens.as_ptr(),
            layer_colors: f.layer_colors.as_ptr(),
            layer_count: f.layer_name_lens.len() as i64,
            entity_types: f.entity_types.as_ptr(),
            layer_ids: f.layer_ids.as_ptr(),
            colors: f.colors.as_ptr(),
            nums: f.nums.as_ptr(),
            nums_len: f.nums.len() as i64,
            point_counts: f.point_counts.as_ptr(),
            closed_flags: f.closed_flags.as_ptr(),
            entity_count: f.entity_types.len() as i64,
        }
    }

    fn encode(f: &Fix) -> Vec<u8> {
        let r = raw(f);
        let d = validate(&r).expect("fixture must be valid");
        build_bytes(&d)
    }

    #[test]
    fn header_is_r12_and_millimetres() {
        let got = String::from_utf8(encode(&fixture())).unwrap();
        assert!(got.contains("AC1009"), "{got:?}");
        assert!(got.contains("$INSUNITS\n70\n4\n"), "{got:?}");
    }

    #[test]
    fn spline_selects_modern_header_and_preserves_cubic_knots_and_true_colors() {
        let got = String::from_utf8(encode(&spline_fixture())).unwrap();
        assert!(got.contains("1\nAC1027\n"), "{got:?}");
        assert!(got.contains("420\n16711680\n"), "{got:?}");
        assert!(got.contains("420\n0\n"), "{got:?}");
        assert!(got.contains("0\nSPLINE\n8\nCut\n"), "{got:?}");
        assert!(got.contains("71\n3\n72\n11\n73\n7\n74\n0\n"), "{got:?}");
        assert!(
            got.contains(
                "40\n0\n40\n0\n40\n0\n40\n0\n40\n1\n40\n1\n40\n1\n40\n2\n40\n2\n40\n2\n40\n2\n"
            ),
            "{got:?}"
        );
    }

    #[test]
    fn true_color_without_spline_still_selects_modern_header() {
        let mut f = fixture();
        f.layer_colors[0] = 0x100_0000 + 0x12_3456;
        f.colors[0] = 0x100_0000;
        let got = String::from_utf8(encode(&f)).unwrap();
        assert!(got.contains("1\nAC1027\n"), "{got:?}");
        assert!(got.contains("420\n1193046\n"), "{got:?}");
        assert!(got.contains("420\n0\n"), "{got:?}");
    }

    #[test]
    fn invalid_aci_and_true_color_sentinels_are_rejected() {
        for value in [-1, 256, 0xFF_FFFF, 0x200_0000] {
            let mut f = fixture();
            f.colors[0] = value;
            assert!(validate(&raw(&f)).is_none(), "accepted {value}");
        }
    }

    #[test]
    fn malformed_cubic_spline_control_count_is_rejected() {
        let mut f = spline_fixture();
        f.point_counts[0] = 5;
        f.nums.truncate(15);
        assert!(validate(&raw(&f)).is_none());
    }

    #[test]
    fn layer_table_has_both_layers_in_order() {
        let got = String::from_utf8(encode(&fixture())).unwrap();
        assert!(got.contains("LAYER\n2\n0\n"), "{got:?}");
        assert!(got.contains("LAYER\n2\nwalls\n"), "{got:?}");
        assert!(got.contains("70\n2\n"), "{got:?}");
    }

    #[test]
    fn polyline_closed_flag_is_one_when_closed() {
        let got = String::from_utf8(encode(&fixture())).unwrap();
        assert!(got.contains("POLYLINE"), "{got:?}");
        assert!(got.contains("66\n1\n"), "{got:?}");
        assert!(got.contains("70\n1\n"), "{got:?}");
        assert_eq!(got.matches("VERTEX").count(), 3);
        assert!(got.contains("SEQEND"), "{got:?}");
    }

    #[test]
    fn polyline_closed_flag_is_zero_when_open() {
        let mut f = fixture();
        f.closed_flags[3] = 0;
        let got = String::from_utf8(encode(&f)).unwrap();
        // The POLYLINE's own 70 flag is 0 (the LAYER table's 70 flags are
        // both "0" too, so look for the 66/70 pair inside the POLYLINE block).
        let i = got.find("POLYLINE").unwrap();
        let j = i + got[i..].find("SEQEND").unwrap();
        let blk = &got[i..j];
        assert!(blk.contains("66\n1\n70\n0\n"), "{blk:?}");
    }

    #[test]
    fn unknown_entity_type_is_rejected() {
        let mut f = fixture();
        f.entity_types[0] = 99;
        assert!(validate(&raw(&f)).is_none());
    }

    #[test]
    fn out_of_range_layer_id_is_rejected() {
        let mut f = fixture();
        f.layer_ids[0] = 5;
        assert!(validate(&raw(&f)).is_none());
    }

    #[test]
    fn nan_coordinate_is_rejected() {
        let mut f = fixture();
        f.nums[0] = f64::NAN;
        assert!(validate(&raw(&f)).is_none());
    }

    #[test]
    fn size_query_and_encode_agree() {
        let f = fixture();
        let r = raw(&f);
        let n = unsafe {
            narwhal_pure_core_dxf_size(
                r.layer_names,
                r.layer_names_len,
                r.layer_name_lens,
                r.layer_colors,
                r.layer_count,
                r.entity_types,
                r.layer_ids,
                r.colors,
                r.nums,
                r.nums_len,
                r.point_counts,
                r.closed_flags,
                r.entity_count,
            )
        };
        assert_eq!(n as usize, encode(&f).len());
    }

    /// The one-pass `dxf_encode_into` must produce EXACTLY the same bytes as
    /// the legacy two-pass route (`size_query` + `encode`) and as `build_bytes`,
    /// for the same input. This is the byte-identity invariant that the
    /// accelerator rests on.
    #[test]
    fn encode_into_matches_legacy_encode_byte_for_byte() {
        let f = fixture();
        let r = raw(&f);
        let d = validate(&r).expect("fixture must be valid");
        let want = build_bytes(&d);
        let mut buf = vec![0u8; capacity_bound(&d) + 16]; // slack
        let mut written: i64 = -1;
        let rc = unsafe {
            narwhal_pure_core_dxf_encode_into(
                r.layer_names,
                r.layer_names_len,
                r.layer_name_lens,
                r.layer_colors,
                r.layer_count,
                r.entity_types,
                r.layer_ids,
                r.colors,
                r.nums,
                r.nums_len,
                r.point_counts,
                r.closed_flags,
                r.entity_count,
                buf.as_mut_ptr(),
                buf.len(),
                std::ptr::addr_of_mut!(written),
            )
        };
        assert_eq!(rc, 0, "one-pass encode must succeed");
        assert_eq!(written as usize, want.len(), "written count must be exact");
        assert_eq!(
            &buf[..written as usize],
            &want[..],
            "one-pass must equal legacy bytes"
        );
    }

    /// A buffer of EXACTLY `capacity_bound` (no slack) is large enough for a
    /// valid input, so the one-pass call must succeed at exact capacity.
    #[test]
    fn encode_into_with_exact_capacity_succeeds() {
        let f = fixture();
        let r = raw(&f);
        let d = validate(&r).expect("fixture must be valid");
        let cap = capacity_bound(&d);
        let mut buf = vec![0u8; cap];
        let mut written: i64 = -1;
        let rc = unsafe {
            narwhal_pure_core_dxf_encode_into(
                r.layer_names,
                r.layer_names_len,
                r.layer_name_lens,
                r.layer_colors,
                r.layer_count,
                r.entity_types,
                r.layer_ids,
                r.colors,
                r.nums,
                r.nums_len,
                r.point_counts,
                r.closed_flags,
                r.entity_count,
                buf.as_mut_ptr(),
                cap,
                std::ptr::addr_of_mut!(written),
            )
        };
        assert_eq!(rc, 0, "exact-capacity encode must succeed");
        assert_eq!(written as usize, build_bytes(&d).len());
    }

    /// An insufficient buffer is a clean reject: a negative code, and
    /// `*out_written` left EXACTLY as the caller set it (no partial write is
    /// ever published).
    #[test]
    fn encode_into_with_insufficient_capacity_is_rejected_without_output() {
        let f = fixture();
        let r = raw(&f);
        let mut buf = vec![0u8; 1];
        let mut written: i64 = 12345;
        let rc = unsafe {
            narwhal_pure_core_dxf_encode_into(
                r.layer_names,
                r.layer_names_len,
                r.layer_name_lens,
                r.layer_colors,
                r.layer_count,
                r.entity_types,
                r.layer_ids,
                r.colors,
                r.nums,
                r.nums_len,
                r.point_counts,
                r.closed_flags,
                r.entity_count,
                buf.as_mut_ptr(),
                1,
                std::ptr::addr_of_mut!(written),
            )
        };
        assert!(rc < 0, "insufficient capacity must be a reject, got {rc}");
        assert_eq!(
            written, 12345,
            "out_written must be left untouched on reject"
        );
    }

    /// A domain error (NaN coordinate) is a clean reject with `*out_written`
    /// left untouched -- the same contract the OBJ one-pass export holds.
    #[test]
    fn encode_into_rejects_domain_error_and_leaves_written_untouched() {
        let mut f = fixture();
        f.nums[0] = f64::NAN;
        let r = raw(&f);
        let mut buf = vec![0u8; 64];
        let mut written: i64 = 999;
        let rc = unsafe {
            narwhal_pure_core_dxf_encode_into(
                r.layer_names,
                r.layer_names_len,
                r.layer_name_lens,
                r.layer_colors,
                r.layer_count,
                r.entity_types,
                r.layer_ids,
                r.colors,
                r.nums,
                r.nums_len,
                r.point_counts,
                r.closed_flags,
                r.entity_count,
                buf.as_mut_ptr(),
                64,
                std::ptr::addr_of_mut!(written),
            )
        };
        assert!(
            rc < 0,
            "a NaN coordinate must be a domain-error reject, got {rc}"
        );
        assert_eq!(written, 999, "out_written must be left untouched on reject");
    }

    #[test]
    fn abi_rejects_null_and_oversized_typed_spans() {
        assert!(
            unsafe {
                narwhal_pure_core_dxf_size(
                    std::ptr::null(),
                    1,
                    std::ptr::null(),
                    std::ptr::null(),
                    0,
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    0,
                    std::ptr::null(),
                    std::ptr::null(),
                    0,
                )
            } < 0
        );

        let dangling_f64 = std::ptr::NonNull::<f64>::dangling().as_ptr();
        assert!(
            unsafe {
                narwhal_pure_core_dxf_size(
                    std::ptr::null(),
                    0,
                    std::ptr::null(),
                    std::ptr::null(),
                    0,
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    dangling_f64,
                    i64::MAX,
                    std::ptr::null(),
                    std::ptr::null(),
                    0,
                )
            } < 0
        );

        let dangling_u8 = std::ptr::NonNull::<u8>::dangling().as_ptr();
        assert!(unsafe { narwhal_pure_core_dxf_decode_size(dangling_u8, usize::MAX) } < 0);
    }

    #[test]
    fn polyline_count_and_layer_name_sum_overflows_are_refused_without_output() {
        let layer_names = b"0";
        let layer_name_lens = [1i64];
        let layer_colors = [7i64];
        let entity_types = [T_POLYLINE];
        let layer_ids = [0i64];
        let colors = [7i64];
        let point_counts = [i64::MAX];
        let closed_flags = [0i64];
        let mut out = [0xA5u8; 32];
        let rc = unsafe {
            narwhal_pure_core_dxf_encode(
                layer_names.as_ptr(),
                1,
                layer_name_lens.as_ptr(),
                layer_colors.as_ptr(),
                1,
                entity_types.as_ptr(),
                layer_ids.as_ptr(),
                colors.as_ptr(),
                std::ptr::null(),
                0,
                point_counts.as_ptr(),
                closed_flags.as_ptr(),
                1,
                out.as_mut_ptr(),
                out.len(),
            )
        };
        assert_eq!(rc, ERR_INPUT);
        assert_eq!(out, [0xA5; 32]);

        let huge_name_lens = [i64::MAX, i64::MAX, i64::MAX];
        let layer_colors = [7i64; 3];
        assert!(
            unsafe {
                narwhal_pure_core_dxf_size(
                    std::ptr::null(),
                    0,
                    huge_name_lens.as_ptr(),
                    layer_colors.as_ptr(),
                    3,
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    0,
                    std::ptr::null(),
                    std::ptr::null(),
                    0,
                )
            } < 0
        );
    }

    // -----------------------------------------------------------------
    // Q106-07 decoder tests. These drive `parse_dxf` directly on hand-written
    // ASCII DXF text (the writer never emits LWPOLYLINE / bulges / skipped
    // types, so a text fixture is the only way to reach those paths), and also
    // prove the encode->decode round trip is structurally exact.
    // -----------------------------------------------------------------

    /// Decode a DXF text fixture into its packed `DxfOut` (panics on error so
    /// a test can assert on the structured result rather than the error code).
    fn decode_text(text: &str) -> DxfOut {
        parse_dxf(text.as_bytes()).expect("fixture must decode")
    }

    /// The round-trip reference: the encoder's own four-entity fixture (LINE,
    /// CIRCLE, ARC, closed POLYLINE) with two layers and `insunits = 4`.
    fn rt_fixture() -> (Vec<u8>, DxfOut) {
        let f = fixture();
        let bytes = encode(&f);
        let out = parse_dxf(&bytes).expect("encoder output must decode");
        (bytes, out)
    }

    #[test]
    fn decode_round_trip_is_exact() {
        let (bytes, out) = rt_fixture();
        // The size query and the decode agree on the packed length.
        let n = unsafe { narwhal_pure_core_dxf_decode_size(bytes.as_ptr(), bytes.len()) };
        assert_eq!(Some(n as usize), out.packed_len());
        // insunits is the header's $INSUNITS (the writer emits 4).
        assert_eq!(out.insunits, Some(4));
        // The LAYER table: "0" (color 7) and "walls" (color 1), in order.
        assert_eq!(out.layers.len(), 2);
        assert_eq!(out.layers[0].0, b"0".to_vec());
        assert_eq!(out.layers[0].1, 7);
        assert_eq!(out.layers[1].0, b"walls".to_vec());
        assert_eq!(out.layers[1].1, 1);
        // Four entities, in file order.
        assert_eq!(out.ents.len(), 4);
        let [a, b, c, d] = [
            (out.ents[0].typ, out.ents[0].coords.len()),
            (out.ents[1].typ, out.ents[1].coords.len()),
            (out.ents[2].typ, out.ents[2].coords.len()),
            (out.ents[3].typ, out.ents[3].coords.len()),
        ];
        assert_eq!(
            (a, b, c, d),
            ((DT_LINE, 6), (DT_CIRCLE, 4), (DT_ARC, 6), (DT_POLYLINE, 9))
        );
        // The POLYLINE is closed (a FREE STRING layer: the fixture's POLYLINE
        // names layer 0, while the CIRCLE names "walls").
        assert_eq!(out.ents[3].closed, 1);
        assert_eq!(out.ents[3].layer, b"0".to_vec());
        assert_eq!(out.ents[1].layer, b"walls".to_vec());
        // Nothing was skipped (the writer emits only geometry).
        assert!(out.skipped.is_empty());
    }

    #[test]
    fn decode_lwpolyline_closed_and_open() {
        // Closed: flag 70 value 1 (bit 0) -> closed == 1.
        let closed = decode_text(concat!(
            "0\nSECTION\n2\nENTITIES\n",
            "0\nLWPOLYLINE\n8\nl1\n62\n5\n70\n1\n",
            "10\n0.0\n20\n0.0\n30\n0.0\n",
            "10\n10.0\n20\n0.0\n30\n0.0\n",
            "0\nENDSEC\n"
        ));
        let e = &closed.ents[0];
        assert_eq!(e.typ, DT_LWPOLYLINE);
        assert_eq!(e.closed, 1);
        assert_eq!(e.layer, b"l1".to_vec());
        assert_eq!(e.color, 5);
        assert_eq!(e.coords, vec![0.0, 0.0, 0.0, 10.0, 0.0, 0.0]);

        // Open: flag 70 value 0 -> closed == 0, and the LAYER table is absent
        // (the entity still names a free-string layer).
        let open = decode_text(concat!(
            "0\nSECTION\n2\nENTITIES\n",
            "0\nLWPOLYLINE\n8\nl1\n70\n0\n",
            "10\n0.0\n20\n0.0\n",
            "10\n5.0\n20\n0.0\n",
            "0\nENDSEC\n"
        ));
        let e = &open.ents[0];
        assert_eq!(e.typ, DT_LWPOLYLINE);
        assert_eq!(e.closed, 0);
        assert_eq!(e.layer, b"l1".to_vec());
        assert_eq!(e.coords, vec![0.0, 0.0, 0.0, 5.0, 0.0, 0.0]);
        assert!(open.layers.is_empty(), "no TABLES section -> no layers");
    }

    #[test]
    fn decode_modern_ascii_spline_preserves_exact_nurbs_data() {
        let out = decode_text(concat!(
            "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1027\n0\nENDSEC\n",
            "0\nSECTION\n2\nTABLES\n0\nTABLE\n2\nLAYER\n",
            "0\nLAYER\n2\nRegister\n62\n51\n6\nContinuous\n",
            "0\nLAYER\n2\ncut\n62\n91\n6\nContinuous\n0\nENDTAB\n0\nENDSEC\n",
            "0\nSECTION\n2\nENTITIES\n",
            "0\nSPLINE\n8\ncut\n62\n1\n420\n16711680\n",
            "70\n9\n71\n3\n72\n8\n73\n4\n74\n0\n",
            "40\n0\n40\n0\n40\n0\n40\n0\n40\n1\n40\n1\n40\n1\n40\n1\n",
            "10\n0\n20\n0\n30\n0\n10\n1\n20\n0\n30\n0\n",
            "10\n1\n20\n1\n30\n0\n10\n0\n20\n0\n30\n0\n",
            "0\nHATCH\n8\ncut\n0\nENDSEC\n"
        ));
        assert_eq!(out.ents.len(), 1);
        let spline = &out.ents[0];
        assert_eq!(spline.typ, DT_SPLINE);
        assert_eq!(spline.layer, b"cut".to_vec());
        assert_eq!(spline.color, TRUE_COLOR_FLAG + 0xFF0000);
        assert_eq!(spline.closed, 1);
        assert_eq!(
            out.layers,
            vec![(b"Register".to_vec(), 51), (b"cut".to_vec(), 91)]
        );
        assert_eq!(&spline.coords[..4], &[3.0, 8.0, 4.0, 0.0]);
        assert_eq!(
            &spline.coords[4..12],
            &[0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0]
        );
        assert_eq!(
            &spline.coords[12..],
            &[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0]
        );
        assert_eq!(out.skipped, vec![(b"HATCH".to_vec(), 1)]);
    }

    #[test]
    fn decode_polyline_and_lwpolyline_both_closed() {
        // A POLYLINE (VERTEX/SEQEND) and an LWPOLYLINE in the same file, both
        // closed: each must carry its own closed flag and point run.
        let out = decode_text(concat!(
            "0\nSECTION\n2\nENTITIES\n",
            "0\nPOLYLINE\n8\np\n62\n1\n70\n1\n66\n1\n",
            "0\nVERTEX\n10\n0.0\n20\n0.0\n30\n0.0\n",
            "0\nVERTEX\n10\n4.0\n20\n0.0\n30\n0.0\n",
            "0\nVERTEX\n10\n4.0\n20\n4.0\n30\n0.0\n",
            "0\nSEQEND\n",
            "0\nLWPOLYLINE\n8\nw\n70\n1\n",
            "10\n0.0\n20\n0.0\n",
            "10\n2.0\n20\n2.0\n",
            "0\nENDSEC\n"
        ));
        assert_eq!(out.ents.len(), 2);
        assert_eq!(out.ents[0].typ, DT_POLYLINE);
        assert_eq!(out.ents[0].closed, 1);
        assert_eq!(out.ents[0].layer, b"p".to_vec());
        assert_eq!(out.ents[0].color, 1);
        assert_eq!(
            out.ents[0].coords,
            vec![0.0, 0.0, 0.0, 4.0, 0.0, 0.0, 4.0, 4.0, 0.0]
        );
        assert_eq!(out.ents[1].typ, DT_LWPOLYLINE);
        assert_eq!(out.ents[1].closed, 1);
        assert_eq!(out.ents[1].layer, b"w".to_vec());
        assert_eq!(out.ents[1].coords, vec![0.0, 0.0, 0.0, 2.0, 2.0, 0.0]);
    }

    #[test]
    fn decode_bulge_is_dropped_but_counted() {
        let out = decode_text(concat!(
            "0\nSECTION\n2\nENTITIES\n",
            "0\nLWPOLYLINE\n8\nl1\n70\n1\n",
            "10\n0.0\n20\n0.0\n42\n0.5\n",
            "10\n10.0\n20\n0.0\n42\n-0.25\n",
            "10\n10.0\n20\n10.0\n",
            "0\nENDSEC\n"
        ));
        // Three points, no 42 in the coordinate run.
        assert_eq!(out.ents[0].coords.len(), 9);
        assert_eq!(out.skipped, vec![(b"bulge".to_vec(), 2)]);
    }

    #[test]
    fn decode_unknown_types_are_counted_not_dropped() {
        let out = decode_text(concat!(
            "0\nSECTION\n2\nENTITIES\n",
            "0\nLINE\n8\ncut\n62\n1\n",
            "10\n0.0\n20\n0.0\n30\n0.0\n11\n10.0\n21\n0.0\n31\n0.0\n",
            "0\nINSERT\n8\nl\n2\nb1\n10\n5.0\n20\n5.0\n",
            "0\nTEXT\n8\nl\n10\n1.0\n20\n1.0\n1\nhi\n",
            "0\nPOINT\n10\n2.0\n20\n2.0\n",
            "0\nENDSEC\n"
        ));
        assert_eq!(out.ents.len(), 1);
        assert_eq!(out.ents[0].typ, DT_LINE);
        // INSERT / TEXT / POINT each counted once, sorted by kind (BTreeMap).
        assert_eq!(
            out.skipped,
            vec![
                (b"INSERT".to_vec(), 1),
                (b"POINT".to_vec(), 1),
                (b"TEXT".to_vec(), 1),
            ]
        );
    }

    #[test]
    fn decode_insunits_none_when_header_has_no_insunits() {
        let out = decode_text(concat!(
            "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1009\n0\nENDSEC\n",
            "0\nSECTION\n2\nENTITIES\n0\nENDSEC\n"
        ));
        assert_eq!(out.insunits, None);
        assert!(out.ents.is_empty());
    }

    #[test]
    fn decode_binary_sentinel_is_rejected() {
        let data = b"AutoCAD Binary DXF\r\n\x1a\0";
        assert_eq!(parse_dxf(data).err(), Some(ERR_DXF_VERSION));
    }

    #[test]
    fn decode_malformed_ascii_acadver_is_rejected() {
        let out = parse_dxf(
            concat!(
                "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nACBAD!\n0\nENDSEC\n",
                "0\nSECTION\n2\nENTITIES\n0\nENDSEC\n"
            )
            .as_bytes(),
        );
        assert_eq!(out.err(), Some(ERR_DXF_VERSION));
    }

    #[test]
    fn decode_truncated_file_is_rejected() {
        // An odd number of lines: the final group code has no value.
        let out = parse_dxf(b"0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\n");
        assert_eq!(out.err(), Some(ERR_DXF_TRUNCATED));
        // A group code that is not an integer is a bad value, not truncation
        // (even line count, so it is NOT the truncated case).
        let out2 = parse_dxf(b"foo\nbar\n");
        assert_eq!(out2.err(), Some(ERR_DXF_BADVALUE));
    }

    #[test]
    fn decode_size_and_decode_agree_on_error() {
        // Both entry points must classify the same file identically: a binary
        // sentinel is a version error on BOTH the size query and the decode.
        let data = b"AutoCAD Binary DXF\r\n\x1a\0";
        let s = unsafe { narwhal_pure_core_dxf_decode_size(data.as_ptr(), data.len()) };
        assert_eq!(s, ERR_DXF_VERSION as i64);
        let mut buf = [0u8; 8];
        let rc =
            unsafe { narwhal_pure_core_dxf_decode(data.as_ptr(), data.len(), buf.as_mut_ptr(), 8) };
        assert_eq!(rc, ERR_DXF_VERSION);
    }

    #[test]
    fn decode_empty_file_is_a_valid_zero_entity_result() {
        // An empty input is a valid (if empty) result, not an error: size 0,
        // decode into a zero-length buffer returns 0.
        let s = unsafe { narwhal_pure_core_dxf_decode_size(std::ptr::null(), 0) };
        assert_eq!(s, 0);
        let rc =
            unsafe { narwhal_pure_core_dxf_decode(std::ptr::null(), 0, std::ptr::null_mut(), 0) };
        assert_eq!(rc, 0);
    }
}
