//! SPDX-License-Identifier: GPL-3.0-or-later
//!
//! Native OBJ encoding for the Narwhal 3D interchange layer.
//!
//! This module turns caller-owned mesh data (packed vertex coordinates,
//! concatenated mesh names with per-mesh lengths, per-mesh vertex/face counts,
//! per-face arity, and a flat array of local face indices) into a complete
//! OBJ file. Python owns the path I/O and is the unchanged fallback.
//!
//! The output is byte-for-byte identical to `obj_writer.write_obj` for the
//! same input:
//!
//! * the file begins with the comment line `# Narwhal 3D`;
//! * each mesh is `o <name>`, then one `v <x> <y> <z>` per vertex, then one
//!   `f <i1> <i2> …` per face;
//! * every line ends in exactly one `\n` (no blank line between meshes);
//! * vertex coordinates are `%.9g`;
//! * face indices are 1-based and **cumulative across meshes**: the second
//!   mesh's first vertex is global index `1 + (vertices in the first mesh)`,
//!   so a face that references local vertex 0 of the second mesh is written
//!   as `1 + first_mesh_vertices`.
//!
//! A face with fewer than 3 indices, or a face index outside
//! `0..len(vertices) - 1`, is a domain error: the size query returns a
//! negative error code and the encode returns a non-zero status. No partial
//! output is ever produced.
//!
//! The size query and the encode both call the same `build_bytes` helper, so
//! the two passes structurally cannot drift apart.

const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const ERR_SIZE: i32 = -3;
const HEADER: &[u8] = b"# Narwhal 3D\n";

/// Format an `f64` exactly as CPython's `"%.9g" % value`.
///
/// CPython's `%g` prints the value at 9 significant digits (IEEE double
/// rounding), strips trailing zeros, and switches to `%e` when the decimal
/// exponent is `< -4` or `>= precision` (9). The exponent is signed and
/// zero-padded to at least two digits (`e-05`, `e+09`). This reproduces that:
/// it takes the 9 significant digits and exponent from Rust's `{:.8e}` (which
/// uses the same IEEE rounding) and re-emits them in the `%g` shape.
/// Public so other pure-core modules (e.g. the DXF encoder) can reuse the
/// exact same CPython-identical number formatting without a second copy.
pub(crate) fn format_9g(value: f64) -> String {
    if value.is_nan() {
        return "nan".to_string();
    }
    if value.is_infinite() {
        return if value < 0.0 {
            "-inf".to_string()
        } else {
            "inf".to_string()
        };
    }
    let negative = value.is_sign_negative();
    let v = value.abs();
    if v == 0.0 {
        return if negative {
            "-0".to_string()
        } else {
            "0".to_string()
        };
    }
    // "d.ddddddde±EXP" with 9 significant digits (IEEE double rounding).
    let s = format!("{v:.8e}");
    let (mantissa, exp_str) = s.split_once('e').expect("exponent is present");
    let e: i32 = exp_str.parse().expect("exponent is a small integer");
    // The 9 significant digits: strip the leading digit, decimal point.
    let digits: String = mantissa.chars().filter(|c| c.is_ascii_digit()).collect();
    let mut out = String::with_capacity(24);
    if negative {
        out.push('-');
    }
    if !(-4..9).contains(&e) {
        // Scientific: `d.ddd e±EXP`, trailing fractional zeros stripped.
        let mut frac = digits[1..].to_string();
        while frac.ends_with('0') {
            frac.pop();
        }
        out.push(digits.as_bytes()[0] as char);
        if !frac.is_empty() {
            out.push('.');
            out.push_str(&frac);
        }
        out.push('e');
        out.push_str(&format!("{e:+03}"));
    } else {
        // Fixed: place the decimal point at the 10^e place, then strip zeros.
        let int_len = (e + 1).max(0) as usize;
        let (int_part, frac_part) = if int_len > 0 {
            (digits[..int_len].to_string(), digits[int_len..].to_string())
        } else {
            let lead = (-e - 1).max(0) as usize;
            (String::from("0"), format!("{}{}", "0".repeat(lead), digits))
        };
        // Only the fractional part may lose trailing zeros; the integer
        // part must keep every digit (`1e8` is `100000000`, not `1`).
        let mut frac = frac_part;
        while frac.ends_with('0') {
            frac.pop();
        }
        let mut fixed = int_part;
        if !frac.is_empty() {
            fixed.push('.');
            fixed.push_str(&frac);
        }
        out.push_str(&fixed);
    }
    out
}

/// A checked view over `len` `f64` values, or `None` when the pointer is
/// null (for a non-empty span) or the length would overflow `isize`.
fn f64_span<'a>(ptr: *const f64, len: usize) -> Option<&'a [f64]> {
    unsafe { crate::checked_slice(ptr, len) }
}

/// A checked view over `len` `i64` values, or `None` on null / overflow.
fn i64_span<'a>(ptr: *const i64, len: usize) -> Option<&'a [i64]> {
    unsafe { crate::checked_slice(ptr, len) }
}

/// A checked view over `len` `i32` values, or `None` on null / overflow.
fn i32_span<'a>(ptr: *const i32, len: usize) -> Option<&'a [i32]> {
    unsafe { crate::checked_slice(ptr, len) }
}

/// The raw OBJ encoder input, exactly as the C caller passes it: pointers
/// and signed counts. Both ABI exports build one of these and hand it to
/// `prepare`, so the two passes share a single validation path.
struct RawInput {
    vertices: *const f64,
    total_v: i64,
    names_concat: *const u8,
    names_len: i64,
    name_lens: *const i64,
    vcounts: *const i64,
    fcounts: *const i64,
    face_arity: *const i64,
    total_faces: i64,
    face_idx: *const i32,
    total_face_idx: i64,
    mesh_count: i64,
}

/// The validated view over the caller-owned OBJ input: checked slices plus
/// the (non-negative) counts they were built from.
struct ObjInput<'a> {
    verts: &'a [f64],
    names: &'a [u8],
    name_lens: &'a [i64],
    vcounts: &'a [i64],
    fcounts: &'a [i64],
    face_arity: &'a [i64],
    face_idx: &'a [i32],
    total_v: usize,
    total_faces: usize,
    total_face_idx: usize,
    mesh_count: usize,
}

/// Validate that all the OBJ encoder inputs are internally consistent.
///
/// This is the single source of truth for domain validation: the size query
/// and the encode both call it (via `prepare`), so the two passes can never
/// disagree about whether an input is valid.
fn validate(input: &ObjInput) -> bool {
    let ObjInput {
        verts,
        names,
        name_lens,
        vcounts,
        fcounts,
        face_arity,
        face_idx,
        total_v,
        total_faces,
        total_face_idx,
        mesh_count,
    } = input;
    let (mesh_count, total_v, total_faces, total_face_idx) =
        (*mesh_count, *total_v, *total_faces, *total_face_idx);
    // The three per-mesh arrays must all have the mesh count as their length
    // (and be empty when there are no meshes).
    if name_lens.len() != mesh_count || vcounts.len() != mesh_count || fcounts.len() != mesh_count {
        return false;
    }
    // The two per-face arrays must have the face count and the total index
    // count as their lengths (and be empty when there are no faces).
    if face_arity.len() != total_faces || face_idx.len() != total_face_idx {
        return false;
    }

    // Every name length is non-negative and the complete run must be consumed:
    // neither trailing slack nor an overrun may reach `build_bytes` slicing.
    let mut name_sum = 0usize;
    for &len in name_lens.iter() {
        let Ok(len) = usize::try_from(len) else {
            return false;
        };
        let Some(sum) = name_sum.checked_add(len) else {
            return false;
        };
        name_sum = sum;
    }
    if name_sum != names.len() {
        return false;
    }

    // The vertex span must be exactly `total_v * 3` f64 values and every
    // coordinate must be finite (a NaN or inf would be written as `nan`/`inf`,
    // which is not a valid OBJ coordinate).
    if total_v.checked_mul(3) != Some(verts.len()) {
        return false;
    }
    if !verts.iter().all(|x| x.is_finite()) {
        return false;
    }

    if mesh_count == 0 {
        return total_v == 0 && total_faces == 0 && total_face_idx == 0;
    }

    // The per-mesh vertex counts must sum to `total_v`, the per-mesh face
    // counts to `total_faces`, and every face must have >= 3 in-range indices.
    let mut v_sum = 0usize;
    for &v in vcounts.iter() {
        let Ok(v) = usize::try_from(v) else {
            return false;
        };
        let Some(sum) = v_sum.checked_add(v) else {
            return false;
        };
        v_sum = sum;
    }
    if v_sum != total_v {
        return false;
    }

    let mut f_sum = 0usize;
    for &f in fcounts.iter() {
        let Ok(f) = usize::try_from(f) else {
            return false;
        };
        let Some(sum) = f_sum.checked_add(f) else {
            return false;
        };
        f_sum = sum;
    }
    if f_sum != total_faces {
        return false;
    }

    let mut idx_sum = 0usize;
    for &a in face_arity.iter() {
        if a < 3 {
            return false;
        }
        let Ok(a) = usize::try_from(a) else {
            return false;
        };
        let Some(sum) = idx_sum.checked_add(a) else {
            return false;
        };
        idx_sum = sum;
    }
    if idx_sum != total_face_idx {
        return false;
    }

    // Walk the faces in order; each index must lie in `0..mesh_vcount - 1`.
    // `face_no` indexes into `face_arity` (one entry per face); `idx_off`
    // is the cumulative offset into the flat `face_idx` array.
    let mut face_no = 0usize;
    let mut idx_off = 0usize;
    for m in 0..mesh_count {
        let Ok(mvc) = usize::try_from(vcounts[m]) else {
            return false;
        };
        let Ok(mesh_faces) = usize::try_from(fcounts[m]) else {
            return false;
        };
        for _ in 0..mesh_faces {
            let Some(&fi) = face_arity.get(face_no) else {
                return false;
            };
            let Ok(fi) = usize::try_from(fi) else {
                return false;
            };
            let Some(end) = idx_off.checked_add(fi) else {
                return false;
            };
            let Some(indices) = face_idx.get(idx_off..end) else {
                return false;
            };
            for &idx in indices {
                if idx < 0 || usize::try_from(idx).map_or(true, |idx| idx >= mvc) {
                    return false;
                }
            }
            let Some(next_face) = face_no.checked_add(1) else {
                return false;
            };
            face_no = next_face;
            idx_off = end;
        }
    }
    face_no == total_faces && idx_off == total_face_idx
}

/// Extract the checked slices from the raw pointers and validate the input.
/// Shared by the size query and the encode so the two passes can never
/// diverge.
fn prepare(raw: &RawInput) -> Result<ObjInput<'_>, i32> {
    if raw.total_v < 0
        || raw.names_len < 0
        || raw.total_faces < 0
        || raw.total_face_idx < 0
        || raw.mesh_count < 0
    {
        return Err(ERR_INPUT);
    }
    let total_v = usize::try_from(raw.total_v).map_err(|_| ERR_INPUT)?;
    let names_len = usize::try_from(raw.names_len).map_err(|_| ERR_INPUT)?;
    let total_faces = usize::try_from(raw.total_faces).map_err(|_| ERR_INPUT)?;
    let total_face_idx = usize::try_from(raw.total_face_idx).map_err(|_| ERR_INPUT)?;
    let mesh_count = usize::try_from(raw.mesh_count).map_err(|_| ERR_INPUT)?;

    let vertex_values = total_v.checked_mul(3).ok_or(ERR_INPUT)?;
    let verts = f64_span(raw.vertices, vertex_values).ok_or(ERR_NULL)?;
    let names = unsafe { crate::checked_slice(raw.names_concat, names_len) }.ok_or(ERR_NULL)?;
    let name_lens = i64_span(raw.name_lens, mesh_count).ok_or(ERR_NULL)?;
    let vcounts = i64_span(raw.vcounts, mesh_count).ok_or(ERR_NULL)?;
    let fcounts = i64_span(raw.fcounts, mesh_count).ok_or(ERR_NULL)?;
    let face_arity = i64_span(raw.face_arity, total_faces).ok_or(ERR_NULL)?;
    let face_idx = i32_span(raw.face_idx, total_face_idx).ok_or(ERR_NULL)?;

    let input = ObjInput {
        verts,
        names,
        name_lens,
        vcounts,
        fcounts,
        face_arity,
        face_idx,
        total_v,
        total_faces,
        total_face_idx,
        mesh_count,
    };
    if !validate(&input) {
        return Err(ERR_INPUT);
    }
    Ok(input)
}

/// A conservative upper bound on the encoded OBJ file's byte length for a
/// validated input. It is cheap to compute (no per-coordinate formatting) and
/// is guaranteed to be >= the actual length, so a caller-owned buffer of this
/// size is always large enough for `build_into`. Mirrors the adapter's `derived`
/// bound exactly so the two agree on the capacity.
fn capacity_bound(input: &ObjInput) -> usize {
    let names: usize = input.name_lens.iter().map(|l| *l as usize).sum();
    // Python computes `max_index_chars = len(str(max(total_v, 1))) + 1` and
    // uses `(max_index_chars + 1) * total_face_idx`. `digits(n) = n.ilog10()+1`,
    // so the per-index bound is `digits(max(total_v,1)) + 2`.
    let max_index_chars = (input.total_v as u64).max(1).ilog10() as usize + 2;
    // Saturating arithmetic is conservative and cannot wrap to a short FFI
    // buffer. Valid adapter-bounded inputs remain far below usize::MAX.
    HEADER
        .len()
        .saturating_add(4usize.saturating_mul(input.name_lens.len()))
        .saturating_add(names)
        .saturating_add(80usize.saturating_mul(input.total_v))
        .saturating_add(3usize.saturating_mul(input.total_faces))
        .saturating_add(max_index_chars.saturating_mul(input.total_face_idx))
}

/// Serialize the complete OBJ file into a caller-owned buffer, writing it
/// EXACTLY ONCE and returning the exact byte count. This is the single source
/// of truth for the output: the legacy encode and the one-pass
/// `obj_encode_into` both call it, so they structurally cannot drift apart.
///
/// Requires `out.len() >= capacity_bound(input)` (the caller guarantees this;
/// `capacity_bound >= actual`, so the buffer is always large enough). The
/// caller slices to the returned count.
fn build_into(input: &ObjInput, out: &mut [u8]) -> usize {
    let ObjInput {
        verts,
        names: names_concat,
        name_lens,
        vcounts,
        fcounts,
        face_arity,
        face_idx,
        ..
    } = input;
    let mut p = 0usize;
    let mut name_off = 0usize;
    let mut v_off = 0usize;
    // `face_no` indexes into `face_arity` (one entry per face); `idx_off` is
    // the cumulative offset into the flat `face_idx` array.
    let mut face_no = 0usize;
    let mut idx_off = 0usize;
    // `vert_off` is the cumulative count of vertices of all earlier meshes:
    // the 1-based global index of mesh m's local vertex j is j + 1 + vert_off.
    let mut vert_off = 0usize;
    // `put` writes `src` into `out` at position `p` and returns the new
    // position (`copy_from_slice` returns `()`, so the length is advanced
    // here). A nested function (not a closure) so it cannot capture `p`/`out`
    // and conflict with the reassignment below.
    fn put(out: &mut [u8], p: usize, src: &[u8]) -> usize {
        out[p..p + src.len()].copy_from_slice(src);
        p + src.len()
    }
    p = put(out, p, HEADER);
    for m in 0..name_lens.len() {
        let nl = name_lens[m] as usize;
        p = put(out, p, b"o ");
        p = put(out, p, &names_concat[name_off..name_off + nl]);
        name_off += nl;
        out[p] = b'\n';
        p += 1;
        for _ in 0..(vcounts[m] as usize) {
            p = put(out, p, b"v ");
            p = put(out, p, format_9g(verts[v_off]).as_bytes());
            out[p] = b' ';
            p += 1;
            p = put(out, p, format_9g(verts[v_off + 1]).as_bytes());
            out[p] = b' ';
            p += 1;
            p = put(out, p, format_9g(verts[v_off + 2]).as_bytes());
            out[p] = b'\n';
            p += 1;
            v_off += 3;
        }
        for _ in 0..(fcounts[m] as usize) {
            let fi = face_arity[face_no] as usize;
            p = put(out, p, b"f ");
            for k in 0..fi {
                if k > 0 {
                    out[p] = b' ';
                    p += 1;
                }
                // 1-based, and shifted by the vertices of every earlier mesh.
                let global = (face_idx[idx_off + k] as usize) + 1 + vert_off;
                p = put(out, p, global.to_string().as_bytes());
            }
            face_no += 1;
            idx_off += fi;
            out[p] = b'\n';
            p += 1;
        }
        vert_off += vcounts[m] as usize;
    }
    p
}

/// Build the complete OBJ file's bytes from validated input (the legacy
/// two-pass helper, now a thin wrapper over the single `build_into` so the
/// size query and both encodes share one serialization path).
fn build_bytes(input: &ObjInput) -> Vec<u8> {
    let cap = capacity_bound(input);
    let mut buf = vec![0u8; cap];
    let n = build_into(input, &mut buf);
    buf.truncate(n);
    buf
}

/// Two-pass size query: the exact byte length of the encoded OBJ file for a
/// valid input. Same validation as the encode export, no output.
///
/// # Safety
///
/// All pointer arguments must be either null (only valid when the
/// corresponding count is zero) or readable for the declared length.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_obj_size(
    vertices: *const f64,
    total_v: i64,
    names_concat: *const u8,
    names_len: i64,
    name_lens: *const i64,
    vcounts: *const i64,
    fcounts: *const i64,
    face_arity: *const i64,
    total_faces: i64,
    face_idx: *const i32,
    total_face_idx: i64,
    mesh_count: i64,
) -> i64 {
    if total_v < 0 || names_len < 0 || total_faces < 0 || total_face_idx < 0 || mesh_count < 0 {
        return ERR_INPUT as i64;
    }
    let raw = RawInput {
        vertices,
        total_v,
        names_concat,
        names_len,
        name_lens,
        vcounts,
        fcounts,
        face_arity,
        total_faces,
        face_idx,
        total_face_idx,
        mesh_count,
    };
    match prepare(&raw) {
        Ok(input) => i64::try_from(build_bytes(&input).len()).unwrap_or(ERR_SIZE as i64),
        Err(code) => code as i64,
    }
}

/// Encode a complete OBJ file into a caller-owned buffer.
///
/// `out` must hold exactly the bytes the encoder will produce (the same
/// length `narwhal_pure_core_obj_size` returns).
///
/// Returns `0` on success, otherwise a negative stable error code.
///
/// # Safety
///
/// All pointer arguments must satisfy the same invariants as the size query,
/// and `out` must be writable for the exact encoded length.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_obj_encode(
    vertices: *const f64,
    total_v: i64,
    names_concat: *const u8,
    names_len: i64,
    name_lens: *const i64,
    vcounts: *const i64,
    fcounts: *const i64,
    face_arity: *const i64,
    total_faces: i64,
    face_idx: *const i32,
    total_face_idx: i64,
    mesh_count: i64,
    out: *mut u8,
    out_len: usize,
) -> i32 {
    if total_v < 0 || names_len < 0 || total_faces < 0 || total_face_idx < 0 || mesh_count < 0 {
        return ERR_INPUT;
    }
    let raw = RawInput {
        vertices,
        total_v,
        names_concat,
        names_len,
        name_lens,
        vcounts,
        fcounts,
        face_arity,
        total_faces,
        face_idx,
        total_face_idx,
        mesh_count,
    };
    let input = match prepare(&raw) {
        Ok(input) => input,
        Err(code) => return code,
    };

    let bytes = build_bytes(&input);
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

/// One-pass OBJ encode into a caller-owned buffer with a conservative
/// capacity (Q117-01).
///
/// Unlike `narwhal_pure_core_obj_encode`, this serializes the file EXACTLY
/// ONCE: it validates/prepares the input once, serializes once into the
/// caller buffer, and publishes `*out_written` (the exact byte count) only on
/// complete success. The caller allocates `out_capacity >= capacity_bound`
/// (mirrored by the Python adapter's `derived` bound) and then slices the
/// buffer to `*out_written`. This removes the two-pass duplication in which
/// both the size query and the encode formatted every coordinate, which made
/// the native route slower than Python.
///
/// `*out_written` is left untouched on any failure (the caller must treat the
/// buffer as garbage and fall back to Python). No partial output is produced.
///
/// Returns `0` on success, otherwise a negative stable error code.
///
/// # Safety
///
/// All pointer arguments satisfy the same invariants as the size query, and
/// `out` is writable for at least `out_capacity` bytes (with
/// `out_capacity >= capacity_bound` of a valid input). `out_written` is a
/// valid, initialized `i64` slot.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_obj_encode_into(
    vertices: *const f64,
    total_v: i64,
    names_concat: *const u8,
    names_len: i64,
    name_lens: *const i64,
    vcounts: *const i64,
    fcounts: *const i64,
    face_arity: *const i64,
    total_faces: i64,
    face_idx: *const i32,
    total_face_idx: i64,
    mesh_count: i64,
    out: *mut u8,
    out_capacity: usize,
    out_written: *mut i64,
) -> i32 {
    if total_v < 0 || names_len < 0 || total_faces < 0 || total_face_idx < 0 || mesh_count < 0 {
        return ERR_INPUT;
    }
    if out_written.is_null() {
        return ERR_NULL;
    }
    let raw = RawInput {
        vertices,
        total_v,
        names_concat,
        names_len,
        name_lens,
        vcounts,
        fcounts,
        face_arity,
        total_faces,
        face_idx,
        total_face_idx,
        mesh_count,
    };
    let input = match prepare(&raw) {
        Ok(input) => input,
        Err(code) => return code,
    };
    if out_capacity < capacity_bound(&input) {
        return ERR_SIZE;
    }
    if out.is_null() {
        return ERR_NULL;
    }
    let out_slice = match unsafe { crate::checked_slice_mut(out, out_capacity) } {
        Some(out) => out,
        None => return ERR_SIZE,
    };
    let written = build_into(&input, out_slice);
    *out_written = i64::try_from(written).unwrap_or(ERR_SIZE as i64);
    0
}

// ---------------------------------------------------------------------------
// Native OBJ decoding (the inverse of the encode above).
//
// This is a faithful port of `obj_reader.read_obj`: it parses a complete OBJ
// file -- continuation lines, `v x y z [w]`, `o`/`g` mesh boundaries, `f` in
// the `i`, `i/t`, `i//n` and `i/t/n` forms (only the vertex index is used),
// NEGATIVE (relative) indices, and the ignored kinds (`vt` `vn` `vp` `s`
// `mtllib` `usemtl` plus any other non-geometry command, each counted) -- and
// returns the meshes as a caller-owned packed buffer. The size query and the
// decode both call the same `parse_obj`, so the two passes structurally cannot
// drift apart, exactly like the encoder's `prepare`/`build_bytes` pair.
//
// `int` and `float` are parsed with CPython semantics (underscores as digit
// separators, `inf`/`nan`, the sign rules) so a face index or coordinate that
// Python would accept or reject is treated the same way here.
// ---------------------------------------------------------------------------

const OBJ_OUT_MAGIC: u32 = 0x33445741; // `ADWA`, little-endian
const OBJ_OUT_VERSION: u32 = 1;

/// A non-zero return means the caller must run the unchanged Python reader; a
/// non-zero return is never a partial result (no output is ever produced).
const ERR_OBJ_FACE: i32 = -10; // a face index does not resolve to a real vertex
const ERR_OBJ_V: i32 = -11; // a `v` carries fewer than three coordinates
const ERR_OBJ_FACE_TOKEN: i32 = -12; // a face token is not an integer

/// Split a byte slice on runs of whitespace (space, tab, vertical tab, form
/// feed, carriage return -- the ASCII set), yielding no empty token. This
/// matches `str.split()` on the logical line (whose line terminators the
/// caller's split has already removed). The previous `c == b' ' || c == b't'
/// ` split on the *letter* `t` (a typo for tab), which shattered `mtllib` into
/// `m`, `usemtl` into `usem`, and `vt` into a bogus `v` command.
fn split_tokens(b: &[u8]) -> Vec<&[u8]> {
    let mut out = Vec::new();
    let mut start: Option<usize> = None;
    for (i, &c) in b.iter().enumerate() {
        if c.is_ascii_whitespace() {
            if let Some(s) = start {
                out.push(&b[s..i]);
                start = None;
            }
        } else if start.is_none() {
            start = Some(i);
        }
    }
    if let Some(s) = start {
        out.push(&b[s..]);
    }
    out
}

/// Lowercase ASCII letters in a byte slice, leaving every other byte
/// unchanged. This matches `str.lower()` for the ASCII command keywords and is
/// lossless for non-ASCII latin-1 bytes (which are only ever used as ignored
/// kinds or mesh names, never as geometry commands).
fn latin1_lower(b: &[u8]) -> Vec<u8> {
    b.iter()
        .map(|&c| {
            if c.is_ascii_uppercase() {
                c.to_ascii_lowercase()
            } else {
                c
            }
        })
        .collect()
}

/// Parse a face-index integer. Rust's `i64` grammar (optional sign, decimal
/// digits) is a strict subset of CPython's `int()`, so every token accepted
/// here is also accepted by `obj_reader.read_obj` and parses to the same
/// value; tokens CPython accepts but Rust rejects (underscores, `0x`/`0b`
/// prefixes) are refused, which safely falls back to the Python reader.
fn parse_i64(tok: &[u8]) -> Option<i64> {
    std::str::from_utf8(tok).ok()?.trim().parse::<i64>().ok()
}

/// Parse a vertex coordinate. Rust's `f64` grammar is a strict subset of
/// CPython's `float()` (it does not accept `inf`/`nan` or underscores), and
/// for every token it does accept the IEEE-754 value is identical; the tokens
/// CPython accepts but Rust rejects are refused, safely falling back to the
/// Python reader.
fn parse_f64(tok: &[u8]) -> Option<f64> {
    std::str::from_utf8(tok).ok()?.trim().parse::<f64>().ok()
}

/// The decoded OBJ as a packed buffer. The layout is **self-describing** -- a
/// consumer needs no knowledge of the input. All integers are little-endian.
///
/// ```text
/// 24-byte header: magic(u32) version(u32) mesh_count(u32) ignored_count(u32)
/// name_lens   (i64 x mesh_count)          -- per-mesh name byte lengths
/// name_run    (bytes)                     -- concatenated mesh names
/// vcounts     (i64 x mesh_count)          -- per-mesh vertex counts
/// fcounts     (i64 x mesh_count)          -- per-mesh face counts
/// face_arity  (i64 x total_faces)         -- index count of each face
/// verts       (f64 x total_v)             -- flat vertex coordinates
/// face_idx    (i64 x total_face_idx)      -- flat 0-based face indices
/// ignored_lens(i64 x ignored_count)       -- per-kind byte lengths
/// ignored_run (bytes)                     -- concatenated ignored kinds
/// ignored_counts(i64 x ignored_count)     -- per-kind counts
/// ```
///
/// The lengths always precede their byte runs, and `face_arity` precedes the
/// flat index run, so a face's index tuple is recoverable exactly (a quad and
/// two triangles are distinct). The producer (`write_to`) and the test
/// consumer (`decode`) must agree on this order byte for byte.
struct ObjOut {
    names: Vec<Vec<u8>>,
    /// The vertex count of each mesh, in mesh order (the vertex span is
    /// grouped per mesh in file order).
    vcounts: Vec<i64>,
    /// The face count of each mesh, in mesh order (the face-index run is
    /// grouped per face, which is grouped per mesh).
    fcounts: Vec<i64>,
    verts: Vec<f64>,
    faces: Vec<Vec<i64>>,
    ignored: Vec<(Vec<u8>, i64)>,
}

impl ObjOut {
    /// The exact packed length this result writes (the size query and the
    /// decode both derive it from the same parsed state).
    fn packed_len(&self) -> Option<usize> {
        let mesh = self.names.len();
        let ign = self.ignored.len();
        if mesh > u32::MAX as usize || ign > u32::MAX as usize {
            return None;
        }
        let name_bytes = self
            .names
            .iter()
            .try_fold(0usize, |sum, n| sum.checked_add(n.len()))?;
        let ign_bytes = self
            .ignored
            .iter()
            .try_fold(0usize, |sum, (n, _)| sum.checked_add(n.len()))?;
        let total_face_idx = self
            .faces
            .iter()
            .try_fold(0usize, |sum, f| sum.checked_add(f.len()))?;
        let mut total = 24usize;
        for amount in [
            mesh.checked_mul(8)?,
            name_bytes,
            mesh.checked_mul(8)?,
            mesh.checked_mul(8)?,
            self.faces.len().checked_mul(8)?,
            self.verts.len().checked_mul(8)?,
            total_face_idx.checked_mul(8)?,
            ign.checked_mul(8)?,
            ign_bytes,
            ign.checked_mul(8)?,
        ] {
            total = total.checked_add(amount)?;
        }
        (total <= isize::MAX as usize).then_some(total)
    }

    /// Write the packed buffer into `out`, which must be exactly `packed_len`
    /// bytes (the caller sized it from the size query). Emits the
    /// self-describing layout documented on `ObjOut`.
    fn write_to(&self, out: &mut [u8]) {
        debug_assert_eq!(Some(out.len()), self.packed_len());
        let mesh = self.names.len();
        let ign = self.ignored.len();
        out[0..4].copy_from_slice(&OBJ_OUT_MAGIC.to_le_bytes());
        out[4..8].copy_from_slice(&OBJ_OUT_VERSION.to_le_bytes());
        out[8..12].copy_from_slice(&(mesh as u32).to_le_bytes());
        out[12..16].copy_from_slice(&(ign as u32).to_le_bytes());
        let mut o = 24usize;
        // name_lens, then the name run (lengths precede the run).
        for name in &self.names {
            out[o..o + 8].copy_from_slice(&(name.len() as i64).to_le_bytes());
            o += 8;
        }
        for name in &self.names {
            out[o..o + name.len()].copy_from_slice(name);
            o += name.len();
        }
        for &vc in &self.vcounts {
            out[o..o + 8].copy_from_slice(&vc.to_le_bytes());
            o += 8;
        }
        for &fc in &self.fcounts {
            out[o..o + 8].copy_from_slice(&fc.to_le_bytes());
            o += 8;
        }
        // face_arity (one i64 per face), then the flat index run.
        for face in &self.faces {
            out[o..o + 8].copy_from_slice(&(face.len() as i64).to_le_bytes());
            o += 8;
        }
        for v in &self.verts {
            out[o..o + 8].copy_from_slice(&v.to_le_bytes());
            o += 8;
        }
        for face in &self.faces {
            for idx in face {
                out[o..o + 8].copy_from_slice(&idx.to_le_bytes());
                o += 8;
            }
        }
        // ignored_lens, then the kind run, then the counts.
        for (name, _) in &self.ignored {
            out[o..o + 8].copy_from_slice(&(name.len() as i64).to_le_bytes());
            o += 8;
        }
        for (name, _) in &self.ignored {
            out[o..o + name.len()].copy_from_slice(name);
            o += name.len();
        }
        for (_, count) in &self.ignored {
            out[o..o + 8].copy_from_slice(&count.to_le_bytes());
            o += 8;
        }
    }
}

/// Parse a complete OBJ file into its packed form, or a stable error code when
/// the file is malformed (a `v` with too few coordinates, a face index that
/// does not resolve to a real vertex, or a non-latin-1 mesh name). A dangling
/// `\` continuation at end-of-file is still emitted (matching `read_obj`).
fn parse_obj(data: &[u8]) -> Result<ObjOut, i32> {
    let mut out = ObjOut {
        names: Vec::new(),
        vcounts: Vec::new(),
        fcounts: Vec::new(),
        verts: Vec::new(),
        faces: Vec::new(),
        ignored: Vec::new(),
    };
    let mut cur_verts: Vec<f64> = Vec::new();
    let mut cur_faces: Vec<Vec<i64>> = Vec::new();
    let mut cur_name: Vec<u8> = Vec::new();
    let mut mesh_voffset = 0i64;
    let mut global_vcount = 0i64;
    let mut ignored = std::collections::BTreeMap::new();

    let flush = |out: &mut ObjOut,
                 cur_name: &mut Vec<u8>,
                 cur_verts: &mut Vec<f64>,
                 cur_faces: &mut Vec<Vec<i64>>,
                 mesh_voffset: &mut i64,
                 global_vcount: i64| {
        if !cur_verts.is_empty() || !cur_faces.is_empty() {
            let nverts = cur_verts.len() as i64;
            let nfaces = cur_faces.len() as i64;
            out.names.push(std::mem::take(cur_name));
            out.vcounts.push(nverts);
            out.fcounts.push(nfaces);
            out.verts.extend(std::mem::take(cur_verts).iter().copied());
            out.faces.extend(std::mem::take(cur_faces));
        }
        *mesh_voffset = global_vcount;
    };

    // Split the file into physical lines exactly as `read_obj` sees them
    // (Python universal newlines: `\r\n` and a lone `\r` each end a line, and
    // neither terminator byte survives into the line). The previous split on
    // every `\r` OR `\n` byte inserted a spurious empty segment between the
    // two bytes of a `\r\n`, which both terminated a continuation early and
    // added a blank logical line that `read_obj` does not see; a trailing `\r
    // ` is stripped by Python but not by `trim_ascii`, so the last line's
    // `endswith("\\")` test could diverge. Coalescing first makes the two
    // agree.
    let mut phys: Vec<&[u8]> = Vec::new();
    let mut start = 0usize;
    let mut i = 0usize;
    while i < data.len() {
        match data[i] {
            b'\n' => {
                phys.push(&data[start..i]);
                i += 1;
                start = i;
            }
            b'\r' => {
                phys.push(&data[start..i]);
                i += 1;
                if i < data.len() && data[i] == b'\n' {
                    i += 1; // `\r\n` is one line ending, not two lines
                }
                start = i;
            }
            _ => i += 1,
        }
    }
    phys.push(&data[start..]); // the final line (possibly empty, even at EOF)

    // Join `\\` continuations exactly as `read_obj` does. The first physical
    // line is kept verbatim (mirroring `rawline.rstrip("\r\n")`, which the
    // split above already performed); each continuation line is stripped and
    // joined with one space. A buffer whose last byte is a backslash is a
    // continuation: the backslash is dropped, the trailing whitespace
    // rstripped (`trim_ascii_end` == Python's `str.rstrip()` whitespace set),
    // and the next physical line is joined on. A dangling continuation at EOF
    // is still emitted.
    let mut logical: Vec<Vec<u8>> = Vec::new();
    let mut buf: Option<Vec<u8>> = None;
    for raw in phys {
        match buf.take() {
            None => buf = Some(raw.to_vec()),
            Some(prev) => {
                let mut b = prev;
                b.push(b' ');
                b.extend_from_slice(raw.trim_ascii());
                buf = Some(b);
            }
        }
        let is_cont = buf.as_ref().is_some_and(|b| b.last() == Some(&b'\\'));
        if is_cont {
            let mut b = buf.take().expect("continuation buffer is set");
            b.pop();
            b = b.trim_ascii_end().to_vec();
            buf = Some(b);
            continue;
        }
        logical.push(buf.take().expect("buffer is set"));
    }
    if let Some(b) = buf {
        logical.push(b);
    }

    for line in &logical {
        let toks = split_tokens(line);
        if toks.is_empty() {
            continue;
        }
        let first = toks[0];
        if first.starts_with(b"#") {
            continue;
        }
        let cmd = latin1_lower(first);
        if cmd == b"v" {
            if toks.len() < 4 {
                return Err(ERR_OBJ_V);
            }
            let x = parse_f64(toks[1]).ok_or(ERR_OBJ_V)?;
            let y = parse_f64(toks[2]).ok_or(ERR_OBJ_V)?;
            let z = parse_f64(toks[3]).ok_or(ERR_OBJ_V)?;
            cur_verts.extend_from_slice(&[x, y, z]);
            global_vcount = global_vcount.checked_add(1).ok_or(ERR_OBJ_V)?;
        } else if cmd == b"f" {
            if toks.len() < 4 {
                return Err(ERR_OBJ_FACE_TOKEN);
            }
            let mut idxs: Vec<i64> = Vec::new();
            for token in &toks[1..] {
                let first_part = token.split(|&c| c == b'/').next().unwrap_or(token);
                let val = parse_i64(first_part).ok_or(ERR_OBJ_FACE_TOKEN)?;
                let g: i64 = if val > 0 {
                    val
                } else {
                    global_vcount
                        .checked_add(val)
                        .and_then(|n| n.checked_add(1))
                        .ok_or(ERR_OBJ_FACE)?
                };
                let local = g
                    .checked_sub(1)
                    .and_then(|n| n.checked_sub(mesh_voffset))
                    .ok_or(ERR_OBJ_FACE)?;
                // `cur_verts` holds three f64s per vertex, so the vertex
                // count is `len / 3` -- using `len` directly would let a face
                // index in `[count, 3*count)` resolve to a real local index and
                // be wrongly accepted, where `read_obj` rejects it.
                if local < 0 || local >= cur_verts.len() as i64 / 3 {
                    return Err(ERR_OBJ_FACE);
                }
                idxs.push(local);
            }
            cur_faces.push(idxs);
        } else if cmd == b"o" || cmd == b"g" {
            flush(
                &mut out,
                &mut cur_name,
                &mut cur_verts,
                &mut cur_faces,
                &mut mesh_voffset,
                global_vcount,
            );
            cur_name = toks.get(1).map(|t| t.to_vec()).unwrap_or_default();
        } else {
            let count = ignored.entry(cmd).or_insert(0i64);
            *count = count.checked_add(1).ok_or(ERR_INPUT)?;
        }
    }
    flush(
        &mut out,
        &mut cur_name,
        &mut cur_verts,
        &mut cur_faces,
        &mut mesh_voffset,
        global_vcount,
    );
    out.ignored = ignored.into_iter().collect();
    Ok(out)
}

/// Two-pass size query for the decoder: the exact packed length *data* decodes
/// to, or a negative error code when the file is malformed. A null pointer with
/// a zero length is an empty file (length 0); a null pointer with a positive
/// length is a domain error.
///
/// # Safety
///
/// `data` must be readable for `data_len` bytes (null only when `data_len == 0`).
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_obj_decode_size(
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
    match parse_obj(bytes) {
        Ok(out) => out
            .packed_len()
            .and_then(|n| i64::try_from(n).ok())
            .unwrap_or(ERR_SIZE as i64),
        Err(code) => code as i64,
    }
}

/// Decode *data* (a complete OBJ file) into a caller-owned packed buffer of
/// exactly the length `narwhal_pure_core_obj_decode_size` returns. Returns 0
/// on success, a negative error code otherwise. A null `data` with a zero
/// length is an empty file (a valid zero-length result); a null `out` is an
/// error unless the result is empty.
///
/// # Safety
///
/// `data` must be readable for `data_len` bytes and `out` writable for
/// `out_len` bytes (null only when `out_len == 0`).
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_obj_decode(
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
    let result = match parse_obj(bytes) {
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    // -- decode helpers ----------------------------------------------------

    /// The decoded OBJ as a structured result, for asserting against.
    struct Decoded {
        names: Vec<Vec<u8>>,
        verts: Vec<f64>,
        faces: Vec<Vec<i64>>,
        ignored: BTreeMap<Vec<u8>, i64>,
    }

    /// Decode *data* via the two-pass C ABI into a structured result.
    /// Returns ``None`` when the size query reports an error (the caller runs
    /// the Python reader) or when the buffer is not a well-formed packed
    /// result (bad magic/version, or a length field that overruns the
    /// buffer). The layout is self-describing, so this helper is a genuine
    /// consumer of the ABI -- it knows nothing about the input file.
    unsafe fn decode(data: &[u8]) -> Option<Decoded> {
        let size = narwhal_pure_core_obj_decode_size(data.as_ptr(), data.len());
        if size < 0 {
            return None;
        }
        let size = size as usize;
        let mut out = vec![0u8; size];
        let rc =
            narwhal_pure_core_obj_decode(data.as_ptr(), data.len(), out.as_mut_ptr(), out.len());
        if rc != 0 {
            return None;
        }
        fn i64_at(b: &[u8], off: usize) -> Option<i64> {
            let w = b.get(off..off + 8)?;
            let arr: [u8; 8] = w.try_into().ok()?;
            Some(i64::from_le_bytes(arr))
        }
        fn u32_at(b: &[u8], off: usize) -> Option<u32> {
            let w = b.get(off..off + 4)?;
            let arr: [u8; 4] = w.try_into().ok()?;
            Some(u32::from_le_bytes(arr))
        }
        let magic = u32_at(&out, 0)?;
        let version = u32_at(&out, 4)?;
        if magic != OBJ_OUT_MAGIC || version != OBJ_OUT_VERSION {
            return None;
        }
        let mesh_count = u32_at(&out, 8)? as usize;
        let ignored_count = u32_at(&out, 12)? as usize;
        let mut o = 24usize;
        // Name lengths (i64, mesh order), then the name run.
        let name_lens: Vec<usize> = (0..mesh_count)
            .map(|i| {
                let v = i64_at(&out, o + i * 8)?;
                if v < 0 {
                    return None;
                }
                Some(v as usize)
            })
            .collect::<Option<Vec<usize>>>()?;
        o += mesh_count * 8;
        let mut names = Vec::with_capacity(mesh_count);
        for &len in &name_lens {
            if o + len > out.len() {
                return None;
            }
            names.push(out[o..o + len].to_vec());
            o += len;
        }
        // Vertex and face counts (i64, mesh order).
        let vcounts: Vec<i64> = (0..mesh_count)
            .map(|i| i64_at(&out, o + i * 8))
            .collect::<Option<Vec<i64>>>()?;
        o += mesh_count * 8;
        let fcounts: Vec<i64> = (0..mesh_count)
            .map(|i| i64_at(&out, o + i * 8))
            .collect::<Option<Vec<i64>>>()?;
        o += mesh_count * 8;
        if vcounts.iter().any(|&v| v < 0) || fcounts.iter().any(|&f| f < 0) {
            return None;
        }
        let total_v = vcounts.iter().sum::<i64>() as usize;
        let total_f = fcounts.iter().sum::<i64>() as usize;
        // Face arity (one i64 per face, in file order), before the vertex and
        // index runs.
        let face_arity: Vec<i64> = (0..total_f)
            .map(|i| {
                let v = i64_at(&out, o + i * 8)?;
                if v < 0 {
                    return None;
                }
                Some(v)
            })
            .collect::<Option<Vec<i64>>>()?;
        o += total_f * 8;
        let total_face_idx: usize = face_arity.iter().map(|a| *a as usize).sum();
        // The vertex f64s, flat in file order (grouped per mesh by `vcounts`).
        if o + total_v * 8 > out.len() {
            return None;
        }
        let mut verts = Vec::with_capacity(total_v);
        for i in 0..total_v {
            verts.push(f64::from_le_bytes(
                out[o + i * 8..o + i * 8 + 8].try_into().ok()?,
            ));
        }
        o += total_v * 8;
        // The face i64s, flat in file order, grouped per face by `face_arity`
        // (so a quad and two triangles stay distinct).
        if o + total_face_idx * 8 > out.len() {
            return None;
        }
        let mut faces = Vec::with_capacity(total_f);
        for &arity in &face_arity {
            let arity = arity as usize;
            let mut face = Vec::with_capacity(arity);
            for _ in 0..arity {
                face.push(i64_at(&out, o)?);
                o += 8;
            }
            faces.push(face);
        }
        // The ignored block: per-kind lengths, the kind run, the counts.
        let ign_lens: Vec<usize> = (0..ignored_count)
            .map(|i| {
                let v = i64_at(&out, o + i * 8)?;
                if v < 0 {
                    return None;
                }
                Some(v as usize)
            })
            .collect::<Option<Vec<usize>>>()?;
        o += ignored_count * 8;
        let mut ign_names = Vec::with_capacity(ignored_count);
        for &len in &ign_lens {
            if o + len > out.len() {
                return None;
            }
            ign_names.push(out[o..o + len].to_vec());
            o += len;
        }
        let mut ignored = BTreeMap::new();
        for (i, name) in ign_names.iter().enumerate() {
            let count = i64_at(&out, o + i * 8)?;
            eprintln!(
                "DBG ign[{i}] name={:?} len={} count={:?}",
                name,
                name.len(),
                count
            );
            ignored.insert(name.clone(), count);
        }
        o += ignored_count * 8;
        if o != out.len() {
            return None;
        }
        Some(Decoded {
            names,
            verts,
            faces,
            ignored,
        })
    }

    // -- decode tests ------------------------------------------------------

    #[test]
    fn decode_two_mesh_round_trips() {
        let out = encode_fixture();
        let decoded = unsafe { decode(&out) }.expect("decode");
        assert_eq!(decoded.names, vec![b"floor".to_vec(), b"roof".to_vec()]);
        // Per-face grouping in file order: floor's two tris then roof's quad.
        // (A per-mesh flattening would have been `[[0,1,2,0,2,3],[0,1,2,3]]`;
        // `face_arity` keeps the faces distinct instead.)
        assert_eq!(
            decoded.faces,
            vec![vec![0, 1, 2], vec![0, 2, 3], vec![0, 1, 2, 3]]
        );
    }

    #[test]
    fn decode_negative_indices() {
        let data = b"v 0 0 0\nv 1 0 0\nv 0 1 0\nv 1 1 0\nf -4 -3 -2 -1\n";
        let decoded = unsafe { decode(data) }.expect("decode");
        assert_eq!(decoded.faces, vec![vec![0, 1, 2, 3]]);
    }

    #[test]
    fn decode_all_face_forms() {
        let data = b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\nf 1/2 2/3 3/4\nf 1//2 2//3 3//4\nf 1/2/3 2/3/4 3/4/5\n";
        let decoded = unsafe { decode(data) }.expect("decode");
        assert_eq!(decoded.faces, vec![vec![0, 1, 2]; 4]);
    }

    #[test]
    fn decode_ignored_kinds_counted() {
        let data = b"mtllib mat.mtl\nusemtl steel\nv 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvn 0 0 1\nvp 0 0 0 1\ns 1\nf 1 2 3\n";
        let decoded = unsafe { decode(data) }.expect("decode");
        let mut want = BTreeMap::new();
        want.insert(b"mtllib".to_vec(), 1);
        want.insert(b"usemtl".to_vec(), 1);
        want.insert(b"vt".to_vec(), 2);
        want.insert(b"vn".to_vec(), 1);
        want.insert(b"vp".to_vec(), 1);
        want.insert(b"s".to_vec(), 1);
        assert_eq!(decoded.ignored, want);
    }

    #[test]
    fn decode_v_with_too_few_coords_is_rejected() {
        let data = b"v 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n";
        assert!(unsafe { decode(data) }.is_none());
    }

    #[test]
    fn decode_face_index_beyond_vertex_count_is_rejected() {
        let data = b"v 0 0 0\nv 1 0 0\nf 1 2 99\n";
        assert!(unsafe { decode(data) }.is_none());
    }

    // A face index in `[vertex_count, 3*vertex_count)` resolves to a real
    // local index when the bound is measured against the *coordinate* count
    // (the old bug) but references a missing vertex in `read_obj`. Four
    // vertices, index 5: `read_obj` raises (local 4 == 4 vertices); the
    // decoder must reject, not accept.
    #[test]
    fn decode_face_index_in_coordinate_window_is_rejected() {
        let data = b"v 0 0 0\nv 1 0 0\nv 0 1 0\nv 1 1 0\nf 1 2 5\n";
        assert!(unsafe { decode(data) }.is_none());
    }

    #[test]
    fn decode_negative_face_index_beyond_vertex_count_is_rejected() {
        let data = b"v 0 0 0\nv 1 0 0\nf -3 -2 -1\n";
        assert!(unsafe { decode(data) }.is_none());
    }

    #[test]
    fn decode_non_integer_face_token_is_rejected() {
        let data = b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 x\n";
        assert!(unsafe { decode(data) }.is_none());
    }

    #[test]
    fn decode_face_with_fewer_than_3_indices_is_rejected() {
        let data = b"v 0 0 0\nv 1 0 0\nf 1 2\n";
        assert!(unsafe { decode(data) }.is_none());
    }

    #[test]
    fn decode_empty_file_is_zero_meshes() {
        let decoded = unsafe { decode(b"") }.expect("empty decode");
        assert!(decoded.names.is_empty());
        assert!(decoded.verts.is_empty());
        assert!(decoded.faces.is_empty());
        assert!(decoded.ignored.is_empty());
    }

    #[test]
    fn decode_comments_only_is_zero_meshes() {
        let data = b"# a comment\n\n# another\n";
        let decoded = unsafe { decode(data) }.expect("decode");
        assert!(decoded.names.is_empty());
        assert!(decoded.verts.is_empty());
    }

    #[test]
    fn decode_ignored_only_is_zero_meshes_but_counts() {
        let data = b"mtllib mat.mtl\nusemtl steel\ns 1\n";
        let decoded = unsafe { decode(data) }.expect("decode");
        assert!(decoded.names.is_empty());
        assert_eq!(decoded.ignored.get(b"mtllib".as_slice()), Some(&1));
        assert_eq!(decoded.ignored.get(b"usemtl".as_slice()), Some(&1));
        assert_eq!(decoded.ignored.get(b"s".as_slice()), Some(&1));
    }

    #[test]
    fn decode_size_and_decode_agree() {
        let data = b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n";
        let size = unsafe { narwhal_pure_core_obj_decode_size(data.as_ptr(), data.len()) };
        assert!(size > 0);
        let decoded = unsafe { decode(data) }.expect("decode");
        assert_eq!(decoded.verts.len(), 9);
        assert_eq!(decoded.faces.len(), 1);
    }

    #[test]
    fn decode_output_length_matches_size_query() {
        let data = b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n";
        let size = unsafe { narwhal_pure_core_obj_decode_size(data.as_ptr(), data.len()) };
        assert!(size > 0);
        let mut out = vec![0u8; size as usize];
        let rc = unsafe {
            narwhal_pure_core_obj_decode(data.as_ptr(), data.len(), out.as_mut_ptr(), out.len())
        };
        assert_eq!(rc, 0);
        // The 24-byte header: magic, version, mesh_count, ignored_count.
        let magic = u32::from_le_bytes(out[0..4].try_into().unwrap());
        assert_eq!(magic, OBJ_OUT_MAGIC);
        let mesh_count = u32::from_le_bytes(out[8..12].try_into().unwrap());
        assert_eq!(mesh_count, 1);
        let ignored_count = u32::from_le_bytes(out[12..16].try_into().unwrap());
        assert_eq!(ignored_count, 0);
    }

    #[test]
    fn decode_crlf_line_endings() {
        let data = b"v 0 0 0\r\nv 1 0 0\r\nv 0 1 0\r\nf 1 2 3\r\n";
        let decoded = unsafe { decode(data) }.expect("decode");
        assert_eq!(decoded.verts.len(), 9);
        assert_eq!(decoded.faces, vec![vec![0, 1, 2]]);
    }

    #[test]
    fn decode_backslash_continuation_joins_lines() {
        let data = b"v 0 0 0\nv 1 0 0\nv 0 1 0\nv 1 1 0\nf 1 2 \\\n3 4\n";
        let decoded = unsafe { decode(data) }.expect("decode");
        assert_eq!(decoded.faces, vec![vec![0, 1, 2, 3]]);
    }

    // CR-only and CRLF line endings are each ONE line ending (Python universal
    // newlines), and a `\` continuation must survive across them exactly as
    // `read_obj` sees it (both measured in `spike/test_obj_reader.py`).

    #[test]
    fn decode_cr_line_endings() {
        let data = b"v 0 0 0\rv 1 0 0\rv 0 1 0\rf 1 2 3\r";
        let decoded = unsafe { decode(data) }.expect("decode");
        assert_eq!(decoded.verts.len(), 9);
        assert_eq!(decoded.faces, vec![vec![0, 1, 2]]);
    }

    #[test]
    fn decode_crlf_continuation_joins_lines() {
        let data = b"v 0 0 0\r\nv 1 0 0\r\nv 0 1 0\r\nv 1 1 0\r\nf 1 2 \\\r\n3 4\r\n";
        let decoded = unsafe { decode(data) }.expect("decode");
        assert_eq!(decoded.faces, vec![vec![0, 1, 2, 3]]);
    }

    /// The two-mesh fixture as flat arrays, mirroring
    /// `spike/test_obj_writer.py::_two_meshes`.
    /// Mesh A "floor": 4 verts, 2 tris (0,1,2) (0,2,3).
    /// Mesh B "roof":  4 verts, 1 quad (0,1,2,3) -> written as global 5 6 7 8.
    struct TwoMesh {
        verts: [f64; 24],
        names: [u8; 9],
        name_lens: [i64; 2],
        vcounts: [i64; 2],
        fcounts: [i64; 2],
        face_arity: [i64; 3],
        face_idx: [i32; 10],
    }

    fn two_mesh_fixture() -> TwoMesh {
        TwoMesh {
            verts: [
                0.0, 0.0, 0.0, 10.0, 0.0, 0.0, 10.0, 10.0, 0.0, 0.0, 10.0, 0.0, 0.0, 0.0, 5.0,
                10.0, 0.0, 5.0, 10.0, 10.0, 5.0, 0.0, 10.0, 5.0,
            ],
            names: *b"floorroof",
            name_lens: [5, 4],
            vcounts: [4, 4],
            fcounts: [2, 1],
            face_arity: [3, 3, 4],
            face_idx: [0, 1, 2, 0, 2, 3, 0, 1, 2, 3],
        }
    }

    fn encode_fixture() -> Vec<u8> {
        let TwoMesh {
            verts,
            names,
            name_lens,
            vcounts,
            fcounts,
            face_arity,
            face_idx,
        } = two_mesh_fixture();
        unsafe {
            let nbytes = narwhal_pure_core_obj_size(
                verts.as_ptr(),
                8,
                names.as_ptr(),
                names.len() as i64,
                name_lens.as_ptr(),
                vcounts.as_ptr(),
                fcounts.as_ptr(),
                face_arity.as_ptr(),
                3,
                face_idx.as_ptr(),
                10,
                2,
            );
            assert!(nbytes > 0, "size query returned {nbytes}");
            let mut buf = vec![0u8; nbytes as usize];
            let rc = narwhal_pure_core_obj_encode(
                verts.as_ptr(),
                8,
                names.as_ptr(),
                names.len() as i64,
                name_lens.as_ptr(),
                vcounts.as_ptr(),
                fcounts.as_ptr(),
                face_arity.as_ptr(),
                3,
                face_idx.as_ptr(),
                10,
                2,
                buf.as_mut_ptr(),
                buf.len(),
            );
            assert_eq!(rc, 0, "encode returned {rc}");
            buf
        }
    }

    #[test]
    fn two_mesh_fixture_matches_python_reference() {
        let got = String::from_utf8(encode_fixture()).expect("valid UTF-8");
        assert!(got.starts_with("# Narwhal 3D\n"), "{got:?}");
        assert!(got.contains("\no floor\n"), "{got:?}");
        assert!(got.contains("v 0 0 0\n"), "{got:?}");
        assert!(got.contains("v 10 0 0\n"), "{got:?}");
        assert!(got.contains("\no roof\n"), "{got:?}");
        assert!(got.contains("f 1 2 3\n"), "{got:?}");
        assert!(got.contains("f 1 3 4\n"), "{got:?}");
        // Cumulative offset: mesh B's local face (0,1,2,3) is global 5 6 7 8.
        assert!(
            got.contains("f 5 6 7 8\n"),
            "cumulative offset lost: {got:?}"
        );
        assert!(
            !got.contains("f 1 2 3 4\n"),
            "second mesh must not restart at 1: {got:?}"
        );
        assert!(got.ends_with('\n'), "file must end with a newline: {got:?}");
        assert!(
            !got.contains("\n\n"),
            "no blank lines between meshes: {got:?}"
        );
    }

    #[test]
    fn cumulative_offset_is_not_reset_per_mesh() {
        // A mutation that resets the cumulative vertex offset at each mesh
        // would write the second mesh's face as `f 1 2 3 4` instead of
        // `f 5 6 7 8`. This test is the mutation target named in Q106-04.
        let got = String::from_utf8(encode_fixture()).expect("valid UTF-8");
        assert!(
            got.contains("f 5 6 7 8"),
            "second mesh face must use offset 4: {got:?}"
        );
        assert!(
            !got.contains("f 1 2 3 4"),
            "second mesh face must not restart at 1: {got:?}"
        );
    }

    #[test]
    fn face_with_fewer_than_3_indices_is_rejected() {
        let verts = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0];
        let names = b"m";
        let name_lens = [1];
        let vcounts = [3];
        let fcounts = [1];
        let face_arity = [2];
        let face_idx = [0, 1];
        unsafe {
            let rc = narwhal_pure_core_obj_size(
                verts.as_ptr(),
                3,
                names.as_ptr(),
                1,
                name_lens.as_ptr(),
                vcounts.as_ptr(),
                fcounts.as_ptr(),
                face_arity.as_ptr(),
                1,
                face_idx.as_ptr(),
                2,
                1,
            );
            assert_eq!(rc, ERR_INPUT as i64, "a 2-index face must be rejected");
        }
    }

    #[test]
    fn out_of_range_face_index_is_rejected() {
        let verts = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0];
        let names = b"m";
        let name_lens = [1];
        let vcounts = [2];
        let fcounts = [1];
        let face_arity = [3];
        let face_idx = [0, 1, 5];
        unsafe {
            let rc = narwhal_pure_core_obj_size(
                verts.as_ptr(),
                2,
                names.as_ptr(),
                1,
                name_lens.as_ptr(),
                vcounts.as_ptr(),
                fcounts.as_ptr(),
                face_arity.as_ptr(),
                1,
                face_idx.as_ptr(),
                3,
                1,
            );
            assert_eq!(
                rc, ERR_INPUT as i64,
                "an out-of-range index must be rejected"
            );
        }
    }

    #[test]
    fn negative_face_index_is_rejected() {
        let verts = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0];
        let names = b"m";
        let name_lens = [1];
        let vcounts = [3];
        let fcounts = [1];
        let face_arity = [3];
        let face_idx = [0, -1, 2];
        unsafe {
            let rc = narwhal_pure_core_obj_size(
                verts.as_ptr(),
                3,
                names.as_ptr(),
                1,
                name_lens.as_ptr(),
                vcounts.as_ptr(),
                fcounts.as_ptr(),
                face_arity.as_ptr(),
                1,
                face_idx.as_ptr(),
                3,
                1,
            );
            assert_eq!(rc, ERR_INPUT as i64, "a negative index must be rejected");
        }
    }

    #[test]
    fn null_vertices_pointer_is_rejected() {
        let names = b"m";
        let name_lens = [1];
        let vcounts = [3];
        let fcounts = [1];
        let face_arity = [3];
        let face_idx = [0, 1, 2];
        unsafe {
            let rc = narwhal_pure_core_obj_size(
                std::ptr::null(),
                3,
                names.as_ptr(),
                1,
                name_lens.as_ptr(),
                vcounts.as_ptr(),
                fcounts.as_ptr(),
                face_arity.as_ptr(),
                1,
                face_idx.as_ptr(),
                3,
                1,
            );
            assert!(rc < 0, "a null vertices pointer must be rejected, got {rc}");
        }
    }

    #[test]
    fn nan_vertex_is_rejected() {
        let verts = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, f64::NAN, 1.0, 0.0];
        let names = b"m";
        let name_lens = [1];
        let vcounts = [3];
        let fcounts = [1];
        let face_arity = [3];
        let face_idx = [0, 1, 2];
        unsafe {
            let rc = narwhal_pure_core_obj_size(
                verts.as_ptr(),
                3,
                names.as_ptr(),
                1,
                name_lens.as_ptr(),
                vcounts.as_ptr(),
                fcounts.as_ptr(),
                face_arity.as_ptr(),
                1,
                face_idx.as_ptr(),
                3,
                1,
            );
            assert_eq!(rc, ERR_INPUT as i64, "a NaN vertex must be rejected");
        }
    }

    #[test]
    fn empty_mesh_list_is_header_only() {
        unsafe {
            let rc = narwhal_pure_core_obj_size(
                std::ptr::null(),
                0,
                std::ptr::null(),
                0,
                std::ptr::null(),
                std::ptr::null(),
                std::ptr::null(),
                std::ptr::null(),
                0,
                std::ptr::null(),
                0,
                0,
            );
            assert_eq!(rc, 13, "an empty mesh list is just the header, got {rc}");
            let mut buf = [0u8; 13];
            let rc = narwhal_pure_core_obj_encode(
                std::ptr::null(),
                0,
                std::ptr::null(),
                0,
                std::ptr::null(),
                std::ptr::null(),
                std::ptr::null(),
                std::ptr::null(),
                0,
                std::ptr::null(),
                0,
                0,
                buf.as_mut_ptr(),
                buf.len(),
            );
            assert_eq!(rc, 0);
            assert_eq!(buf, *b"# Narwhal 3D\n");
        }
    }

    #[test]
    fn size_query_and_encode_agree() {
        let TwoMesh {
            verts,
            names,
            name_lens,
            vcounts,
            fcounts,
            face_arity,
            face_idx,
        } = two_mesh_fixture();
        unsafe {
            let nbytes = narwhal_pure_core_obj_size(
                verts.as_ptr(),
                8,
                names.as_ptr(),
                names.len() as i64,
                name_lens.as_ptr(),
                vcounts.as_ptr(),
                fcounts.as_ptr(),
                face_arity.as_ptr(),
                3,
                face_idx.as_ptr(),
                10,
                2,
            );
            assert_eq!(
                nbytes as usize,
                encode_fixture().len(),
                "size != encoded length"
            );
        }
    }

    #[test]
    fn encode_into_matches_legacy_encode_byte_for_byte() {
        // Q117-01: the one-pass encode_into must produce EXACTLY the same
        // bytes the legacy two-pass encode produces, including the
        // cumulative offset (mesh B's face written `f 5 6 7 8`). A reset of
        // the second mesh's vertex offset would make this RED.
        let TwoMesh {
            verts,
            names,
            name_lens,
            vcounts,
            fcounts,
            face_arity,
            face_idx,
        } = two_mesh_fixture();
        unsafe {
            let mut written: i64 = -1;
            let mut buf = vec![0u8; 8192];
            let rc = narwhal_pure_core_obj_encode_into(
                verts.as_ptr(),
                8,
                names.as_ptr(),
                names.len() as i64,
                name_lens.as_ptr(),
                vcounts.as_ptr(),
                fcounts.as_ptr(),
                face_arity.as_ptr(),
                3,
                face_idx.as_ptr(),
                10,
                2,
                buf.as_mut_ptr(),
                buf.len(),
                &mut written as *mut i64,
            );
            assert_eq!(rc, 0, "encode_into returned {rc}");
            let got = buf[..written as usize].to_vec();
            assert_eq!(got, encode_fixture(), "encode_into differs from legacy");
            let text = String::from_utf8(got).expect("valid UTF-8");
            assert!(
                text.contains("f 5 6 7 8"),
                "cumulative offset lost: {text:?}"
            );
            assert!(
                !text.contains("f 1 2 3 4"),
                "second mesh restarted at 1: {text:?}"
            );
        }
    }

    #[test]
    fn encode_into_with_exact_capacity_succeeds() {
        // The caller may pass exactly the capacity bound (not an over-estimate):
        // encode_into must still succeed and report the exact length.
        let TwoMesh {
            verts,
            names,
            name_lens,
            vcounts,
            fcounts,
            face_arity,
            face_idx,
        } = two_mesh_fixture();
        unsafe {
            let raw = RawInput {
                vertices: verts.as_ptr(),
                total_v: 8,
                names_concat: names.as_ptr(),
                names_len: names.len() as i64,
                name_lens: name_lens.as_ptr(),
                vcounts: vcounts.as_ptr(),
                fcounts: fcounts.as_ptr(),
                face_arity: face_arity.as_ptr(),
                total_faces: 3,
                face_idx: face_idx.as_ptr(),
                total_face_idx: 10,
                mesh_count: 2,
            };
            let input = prepare(&raw).expect("fixture is valid");
            let exact = capacity_bound(&input);
            let mut written: i64 = -1;
            let mut buf = vec![0u8; exact];
            let rc = narwhal_pure_core_obj_encode_into(
                verts.as_ptr(),
                8,
                names.as_ptr(),
                names.len() as i64,
                name_lens.as_ptr(),
                vcounts.as_ptr(),
                fcounts.as_ptr(),
                face_arity.as_ptr(),
                3,
                face_idx.as_ptr(),
                10,
                2,
                buf.as_mut_ptr(),
                buf.len(),
                &mut written as *mut i64,
            );
            assert_eq!(rc, 0, "exact-capacity encode_into returned {rc}");
            let got = buf[..written as usize].to_vec();
            assert_eq!(got, encode_fixture(), "exact-capacity output differs");
            assert!(
                written as usize <= exact,
                "written {written} > capacity {exact}"
            );
        }
    }

    #[test]
    fn encode_into_with_insufficient_capacity_is_rejected_without_output() {
        // A capacity below the bound is an error (ERR_SIZE) and out_written
        // must be left untouched.
        let TwoMesh {
            verts,
            names,
            name_lens,
            vcounts,
            fcounts,
            face_arity,
            face_idx,
        } = two_mesh_fixture();
        unsafe {
            let mut written: i64 = 12345;
            let mut buf = vec![0u8; 1];
            let rc = narwhal_pure_core_obj_encode_into(
                verts.as_ptr(),
                8,
                names.as_ptr(),
                names.len() as i64,
                name_lens.as_ptr(),
                vcounts.as_ptr(),
                fcounts.as_ptr(),
                face_arity.as_ptr(),
                3,
                face_idx.as_ptr(),
                10,
                2,
                buf.as_mut_ptr(),
                buf.len(),
                &mut written as *mut i64,
            );
            assert_eq!(rc, ERR_SIZE, "small capacity must be ERR_SIZE, got {rc}");
            assert_eq!(written, 12345, "out_written must be untouched on failure");
        }
    }

    #[test]
    fn encode_into_rejects_domain_error_and_leaves_written_untouched() {
        // A 2-index face is a domain error: ERR_INPUT and out_written untouched.
        let verts = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0];
        let names = b"m";
        let name_lens = [1];
        let vcounts = [3];
        let fcounts = [1];
        let face_arity = [2];
        let face_idx = [0, 1];
        unsafe {
            let mut written: i64 = 999;
            let mut buf = vec![0u8; 64];
            let rc = narwhal_pure_core_obj_encode_into(
                verts.as_ptr(),
                3,
                names.as_ptr(),
                1,
                name_lens.as_ptr(),
                vcounts.as_ptr(),
                fcounts.as_ptr(),
                face_arity.as_ptr(),
                1,
                face_idx.as_ptr(),
                2,
                1,
                buf.as_mut_ptr(),
                buf.len(),
                &mut written as *mut i64,
            );
            assert_eq!(rc, ERR_INPUT, "a 2-index face must be ERR_INPUT, got {rc}");
            assert_eq!(written, 999, "out_written must be untouched on rejection");
        }
    }

    #[test]
    fn mismatched_name_spans_are_rejected_without_output() {
        let TwoMesh {
            verts,
            names,
            mut name_lens,
            vcounts,
            fcounts,
            face_arity,
            face_idx,
        } = two_mesh_fixture();
        name_lens[1] = 3;
        let size = unsafe {
            narwhal_pure_core_obj_size(
                verts.as_ptr(),
                8,
                names.as_ptr(),
                names.len() as i64,
                name_lens.as_ptr(),
                vcounts.as_ptr(),
                fcounts.as_ptr(),
                face_arity.as_ptr(),
                3,
                face_idx.as_ptr(),
                10,
                2,
            )
        };
        assert_eq!(size, ERR_INPUT as i64);
        let mut out = [0xA5u8; 32];
        let rc = unsafe {
            narwhal_pure_core_obj_encode(
                verts.as_ptr(),
                8,
                names.as_ptr(),
                names.len() as i64,
                name_lens.as_ptr(),
                vcounts.as_ptr(),
                fcounts.as_ptr(),
                face_arity.as_ptr(),
                3,
                face_idx.as_ptr(),
                10,
                2,
                out.as_mut_ptr(),
                out.len(),
            )
        };
        assert_eq!(rc, ERR_INPUT);
        assert_eq!(out, [0xA5; 32]);

        name_lens[1] = 5;
        assert_eq!(
            unsafe {
                narwhal_pure_core_obj_size(
                    verts.as_ptr(),
                    8,
                    names.as_ptr(),
                    names.len() as i64,
                    name_lens.as_ptr(),
                    vcounts.as_ptr(),
                    fcounts.as_ptr(),
                    face_arity.as_ptr(),
                    3,
                    face_idx.as_ptr(),
                    10,
                    2,
                )
            },
            ERR_INPUT as i64
        );
    }

    #[test]
    fn oversized_counts_and_raw_input_lengths_are_rejected() {
        let dangling_f64 = std::ptr::NonNull::<f64>::dangling().as_ptr();
        assert_eq!(
            unsafe {
                narwhal_pure_core_obj_size(
                    dangling_f64,
                    i64::MAX,
                    std::ptr::null(),
                    0,
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    0,
                    std::ptr::null(),
                    0,
                    0,
                )
            },
            ERR_INPUT as i64
        );

        let dangling_i64 = std::ptr::NonNull::<i64>::dangling().as_ptr();
        let oversized_elements = (isize::MAX as usize / std::mem::size_of::<i64>()) + 1;
        let oversized_meshes = i64::try_from(oversized_elements).expect("64-bit target");
        assert!(
            unsafe {
                narwhal_pure_core_obj_size(
                    std::ptr::null(),
                    0,
                    std::ptr::null(),
                    0,
                    dangling_i64,
                    dangling_i64,
                    dangling_i64,
                    std::ptr::null(),
                    0,
                    std::ptr::null(),
                    0,
                    oversized_meshes,
                )
            } < 0
        );

        let dangling_u8 = std::ptr::NonNull::<u8>::dangling().as_ptr();
        assert!(unsafe { narwhal_pure_core_obj_decode_size(dangling_u8, usize::MAX) } < 0);
        assert!(
            unsafe {
                narwhal_pure_core_obj_size(
                    std::ptr::null(),
                    0,
                    std::ptr::null(),
                    1,
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    0,
                    std::ptr::null(),
                    0,
                    0,
                )
            } < 0
        );
    }

    #[test]
    fn format_9g_matches_cpython_semantics() {
        assert_eq!(format_9g(0.0), "0");
        assert_eq!(format_9g(-0.0), "-0");
        assert_eq!(format_9g(1.0), "1");
        assert_eq!(format_9g(0.1), "0.1");
        assert_eq!(format_9g(0.001), "0.001");
        assert_eq!(format_9g(0.0001), "0.0001");
        assert_eq!(format_9g(0.00001), "1e-05");
        assert_eq!(format_9g(1e7), "10000000");
        assert_eq!(format_9g(1e8), "100000000");
        assert_eq!(format_9g(1e-4), "0.0001");
        assert_eq!(format_9g(9e-5), "9e-05");
        assert_eq!(format_9g(1e9), "1e+09");
        assert_eq!(format_9g(1e15), "1e+15");
        assert_eq!(format_9g(-1.5), "-1.5");
        assert_eq!(format_9g(123456.789), "123456.789");
        assert_eq!(format_9g(1.23456789e-07), "1.23456789e-07");
        assert_eq!(format_9g(1e308), "1e+308");
        assert_eq!(format_9g(f64::NAN), "nan");
        assert_eq!(format_9g(f64::INFINITY), "inf");
    }
}
