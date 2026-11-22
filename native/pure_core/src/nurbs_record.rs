//! One `ON_NurbsCurve` object record, as rhino3dm's `curve.Encode()` writes it.
//!
//! The Rust adapters hand Rust a curve's knots and homogeneous control points. Reading
//! them through rhino3dm's `curve.Points` costs one pybind call per point and per
//! coordinate, so `narwhal3d/nurbs_data.py` asks openNURBS for the curve once, through
//! `curve.Encode()`, and passes the decoded bytes here. This module checks the whole
//! record and copies out its doubles. It allocates nothing and writes nothing until the
//! record has been accepted in full.
//!
//! The record is `ON_BinaryArchive::WriteObject` (opennurbs_archive.cpp and
//! opennurbs_3dm.h at eb92af3, the commit rhino3dm 8.35.0 pins) with 8-byte chunk
//! lengths (3dm >= 5). Every byte belongs to exactly one of these chunks:
//!
//! * `TCODE_OPENNURBS_CLASS`, whose length is the rest of the record;
//! * `TCODE_OPENNURBS_CLASS_UUID`, length 20: `ON_NurbsCurve`'s class id and its CRC-32;
//! * `TCODE_OPENNURBS_CLASS_DATA`: the `ON_NurbsCurve::Write` payload and its CRC-32;
//! * `TCODE_OPENNURBS_CLASS_END`, a short chunk whose value is 0.
//!
//! The payload (opennurbs_nurbscurve.cpp) is chunk version 1.0 or 1.1, dim, is_rat,
//! order, cv_count, two reserved zeros, a placeholder bounding box, the knot count and
//! knots, the CV count and CVs, and from version 1.1 the SubD-friendly bool. A record
//! that differs anywhere (another class, user data chunks, a 2-D curve, a bad CRC,
//! trailing or missing bytes) is not recognised, and Python reads the curve point by
//! point instead. The doubles are copied bit for bit, so they are the same bits
//! `Knots.ToList()` and `Points` return.
//!
//! `read_polyline` reads an `ON_PolylineCurve` record in the same framing, for the
//! curve/curve intersection (`curvex`), which needs the curve's parameters `m_t`:
//! rhino3dm exposes the points but not the parameters. Its payload
//! (opennurbs_polylinecurve.cpp, `ON_PolylineCurve::Write`) is chunk version 1.0, the
//! point array (an int count and 3 doubles per point), the parameter array (an int count
//! and the doubles) and `m_dim`. Only a 3-D curve with as many parameters as points is
//! recognised.

use std::mem::align_of;

const ERR_NULL: i32 = -1;
const ERR_SIZE: i32 = -3;
/// A well-formed call whose bytes are not a record this module reads.
const NOT_RECOGNISED: i32 = 1;

const TCODE_CLASS: u32 = 0x0002_7FFA;
const TCODE_CLASS_UUID: u32 = 0x0002_FFFB;
const TCODE_CLASS_DATA: u32 = 0x0002_FFFC;
const TCODE_CLASS_END: u32 = 0x8002_7FFF;
/// 4ED7D4DD-E947-11d3-BFE5-0010830122F0, as the archive stores it.
const NURBS_CURVE_UUID: [u8; 16] = [
    0xdd, 0xd4, 0xd7, 0x4e, 0x47, 0xe9, 0xd3, 0x11, 0xbf, 0xe5, 0x00, 0x10, 0x83, 0x01, 0x22, 0xf0,
];
/// 4ED7D4E6-E947-11d3-BFE5-0010830122F0, as the archive stores it.
const POLYLINE_CURVE_UUID: [u8; 16] = [
    0xe6, 0xd4, 0xd7, 0x4e, 0x47, 0xe9, 0xd3, 0x11, 0xbf, 0xe5, 0x00, 0x10, 0x83, 0x01, 0x22, 0xf0,
];
const HEADER: usize = 12; // uint32 typecode + int64 length

/// A recognised record: the curve's shape and the raw little-endian doubles.
#[derive(Debug, PartialEq)]
pub struct Record<'a> {
    pub order: usize,
    pub rational: bool,
    pub knot_count: usize,
    pub cv_count: usize,
    knots: &'a [u8],
    cvs: &'a [u8],
}

impl Record<'_> {
    /// Homogeneous control points, 4 doubles each (`w` is 1.0 on a non-rational curve).
    pub fn cv_doubles(&self) -> usize {
        4 * self.cv_count
    }

    /// Copy the knots to `knots[..knot_count]` and the homogeneous control points to
    /// `cvs[..cv_doubles()]`.
    pub(crate) fn write(&self, knots: &mut [f64], cvs: &mut [f64]) {
        for (k, b) in knots.iter_mut().zip(self.knots.chunks_exact(8)) {
            *k = f64::from_le_bytes(b.try_into().unwrap());
        }
        let size = if self.rational { 4 } else { 3 };
        for (cv, b) in cvs.chunks_exact_mut(4).zip(self.cvs.chunks_exact(8 * size)) {
            for (v, d) in cv.iter_mut().zip(b.chunks_exact(8)) {
                *v = f64::from_le_bytes(d.try_into().unwrap());
            }
            if !self.rational {
                cv[3] = 1.0;
            }
        }
    }
}

const CRC_TABLE: [u32; 256] = {
    let mut table = [0u32; 256];
    let mut i = 0;
    while i < 256 {
        let mut c = i as u32;
        let mut k = 0;
        while k < 8 {
            c = if c & 1 != 0 {
                0xEDB8_8320 ^ (c >> 1)
            } else {
                c >> 1
            };
            k += 1;
        }
        table[i] = c;
        i += 1;
    }
    table
};

/// `ON_CRC32(0, ...)`: zlib's CRC-32.
fn crc32(data: &[u8]) -> u32 {
    !data.iter().fold(!0u32, |c, &b| {
        CRC_TABLE[((c ^ b as u32) & 0xFF) as usize] ^ (c >> 8)
    })
}

/// Bounds-checked little-endian reads over the record.
struct Cursor<'a> {
    data: &'a [u8],
    at: usize,
}

impl<'a> Cursor<'a> {
    fn bytes(&mut self, n: usize) -> Option<&'a [u8]> {
        let end = self.at.checked_add(n)?;
        let out = self.data.get(self.at..end)?;
        self.at = end;
        Some(out)
    }

    fn u8(&mut self) -> Option<u8> {
        Some(self.bytes(1)?[0])
    }

    fn u32(&mut self) -> Option<u32> {
        Some(u32::from_le_bytes(self.bytes(4)?.try_into().ok()?))
    }

    fn i32(&mut self) -> Option<i32> {
        Some(i32::from_le_bytes(self.bytes(4)?.try_into().ok()?))
    }

    fn i64(&mut self) -> Option<i64> {
        Some(i64::from_le_bytes(self.bytes(8)?.try_into().ok()?))
    }

    /// A non-negative ON int as a count.
    fn count(&mut self) -> Option<usize> {
        usize::try_from(self.i32()?).ok()
    }

    /// A chunk header with typecode `tcode`; its non-negative length.
    fn chunk(&mut self, tcode: u32) -> Option<usize> {
        if self.u32()? != tcode {
            return None;
        }
        usize::try_from(self.i64()?).ok()
    }

    /// The body of a CRC chunk, `length` bytes ending in the CRC-32 of the rest.
    fn crc_body(&mut self, length: usize) -> Option<&'a [u8]> {
        let body = self.bytes(length)?;
        let (data, crc) = body.split_at(body.len().checked_sub(4)?);
        (crc32(data) == u32::from_le_bytes(crc.try_into().ok()?)).then_some(data)
    }

    fn done(&self) -> bool {
        self.at == self.data.len()
    }
}

/// The curve in one encoded record, or None for anything this does not recognise.
pub fn read(data: &[u8]) -> Option<Record<'_>> {
    read_payload(class_payload(data, &NURBS_CURVE_UUID)?)
}

/// The checked `TCODE_OPENNURBS_CLASS_DATA` payload of a whole record of class `uuid`.
fn class_payload<'a>(data: &'a [u8], uuid: &[u8; 16]) -> Option<&'a [u8]> {
    let mut r = Cursor { data, at: 0 };
    if r.chunk(TCODE_CLASS)? != data.len().checked_sub(HEADER)? {
        return None;
    }
    let length = r.chunk(TCODE_CLASS_UUID)?;
    if length != 20 || r.crc_body(length)? != uuid {
        return None;
    }
    let length = r.chunk(TCODE_CLASS_DATA)?;
    let payload = r.crc_body(length)?;
    if r.chunk(TCODE_CLASS_END)? != 0 || !r.done() {
        return None;
    }
    Some(payload)
}

/// A recognised `ON_PolylineCurve` record: its points and parameters, raw little-endian.
#[derive(Debug, PartialEq)]
pub struct PolylineRecord<'a> {
    pub point_count: usize,
    points: &'a [u8],
    params: &'a [u8],
}

impl PolylineRecord<'_> {
    /// `m_pline` as `[x, y, z]` and `m_t`, bit for bit.
    pub(crate) fn arrays(&self) -> (Vec<[f64; 3]>, Vec<f64>) {
        let double = |b: &[u8]| f64::from_le_bytes(b.try_into().unwrap());
        let points = self
            .points
            .chunks_exact(24)
            .map(|p| [double(&p[..8]), double(&p[8..16]), double(&p[16..])])
            .collect();
        let params = self.params.chunks_exact(8).map(double).collect();
        (points, params)
    }
}

/// The polyline in one encoded `ON_PolylineCurve` record, or None for anything this does
/// not recognise.
pub fn read_polyline(data: &[u8]) -> Option<PolylineRecord<'_>> {
    let mut p = Cursor {
        data: class_payload(data, &POLYLINE_CURVE_UUID)?,
        at: 0,
    };
    if p.u8()? != 0x10 {
        return None;
    }
    let point_count = p.count()?;
    let points = p.bytes(point_count.checked_mul(24)?)?;
    if p.count()? != point_count {
        return None;
    }
    let params = p.bytes(point_count.checked_mul(8)?)?;
    if p.i32()? != 3 || !p.done() {
        return None;
    }
    Some(PolylineRecord {
        point_count,
        points,
        params,
    })
}

/// `ON_NurbsCurve::Write`'s payload, every byte of it.
fn read_payload(payload: &[u8]) -> Option<Record<'_>> {
    let mut p = Cursor {
        data: payload,
        at: 0,
    };
    let version = p.u8()?;
    if version != 0x10 && version != 0x11 {
        return None;
    }
    let (dim, is_rat) = (p.i32()?, p.i32()?);
    let (order, cv_count) = (p.count()?, p.count()?);
    if dim != 3 || !(is_rat == 0 || is_rat == 1) || order < 2 || cv_count < order {
        return None;
    }
    if p.i32()? != 0 || p.i32()? != 0 {
        return None; // the reserved legacy flags
    }
    p.bytes(48)?; // the placeholder bounding box
    let knot_count = p.count()?;
    if knot_count != order.checked_add(cv_count)?.checked_sub(2)? {
        return None;
    }
    let knots = p.bytes(knot_count.checked_mul(8)?)?;
    if p.count()? != cv_count {
        return None;
    }
    let rational = is_rat == 1;
    let size = if rational { 4 } else { 3 };
    let cvs = p.bytes(cv_count.checked_mul(8 * size)?)?;
    if version == 0x11 && p.u8()? > 1 {
        return None; // the SubD-friendly bool
    }
    p.done().then_some(Record {
        order,
        rational,
        knot_count,
        cv_count,
        knots,
        cvs,
    })
}

/// Read one encoded `ON_NurbsCurve` record into caller buffers.
///
/// `data`/`len` is the record (`base64.b64decode(curve.Encode()["data"])`). On success
/// returns 0, writes `shape = [order, rational, knot_count, cv_count]`, the knots to
/// `knots[..knot_count]` and the homogeneous control points to `cvs[..4 * cv_count]`.
/// Returns 1, writing nothing, for a record this does not recognise; -3, writing only
/// `shape`, when a buffer is too small; -1 for a null `shape` or an invalid span.
///
/// # Safety
///
/// `data` must reference `len` readable bytes, `knots`/`cvs` `knot_capacity`/
/// `cv_capacity` writable doubles, and `shape` four writable `usize`s.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_nurbs_curve_record(
    data: *const u8,
    len: usize,
    knots: *mut f64,
    knot_capacity: usize,
    cvs: *mut f64,
    cv_capacity: usize,
    shape: *mut usize,
) -> i32 {
    if shape.is_null() || !(shape as usize).is_multiple_of(align_of::<usize>()) {
        return ERR_NULL;
    }
    let Some(data) = (unsafe { crate::checked_slice(data, len) }) else {
        return ERR_NULL;
    };
    let Some(record) = read(data) else {
        return NOT_RECOGNISED;
    };
    let values = [
        record.order,
        record.rational as usize,
        record.knot_count,
        record.cv_count,
    ];
    if record.knot_count > knot_capacity || record.cv_doubles() > cv_capacity {
        unsafe { std::ptr::copy_nonoverlapping(values.as_ptr(), shape, 4) };
        return ERR_SIZE;
    }
    let out_knots = unsafe { crate::checked_slice_mut(knots, record.knot_count) };
    let out_cvs = unsafe { crate::checked_slice_mut(cvs, record.cv_doubles()) };
    let (Some(out_knots), Some(out_cvs)) = (out_knots, out_cvs) else {
        return ERR_NULL;
    };
    record.write(out_knots, out_cvs);
    unsafe { std::ptr::copy_nonoverlapping(values.as_ptr(), shape, 4) };
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    // Records written by rhino3dm 8.35.0's `curve.Encode()`, with the bits its per-point
    // `Knots.ToList()` / `Points` read gives (spike/test_nurbs_data.py checks the same on
    // live curves). The cubic carries -0.0 and a 1e-300 coordinate.
    // CUBIC: order 4, rational False, 7 knots, 5 CVs, 330 bytes
    const CUBIC_RECORD: &str = "fa7f02003e01000000000000fbff02001400000000000000ddd4d74e47e9d311bfe50010830122f010f27870fcff0200060100000000000011030000000000000004000000050000000000000000000000000000000000f03f00000000000000000000000000000000000000000000f0bf0000000000000000000000000000000007000000000000000000008000000000000000000000000000000000000000000000f03f00000000000000400000000000000040000000000000004005000000000000000000008000000000000000000000000000000000000000000000f03f0000000000000840000000000000e03f0000000000000840000000000000f0bf59f3f8c21f6ea5010000000000001440000000000000004000000000000000000000000000001c40555555555555d53f00000000000000c00092372e48ff7f02800000000000000000";
    const CUBIC_KNOTS: [u64; 7] = [
        0x8000000000000000,
        0x0000000000000000,
        0x0000000000000000,
        0x3ff0000000000000,
        0x4000000000000000,
        0x4000000000000000,
        0x4000000000000000,
    ];
    const CUBIC_CVS: [u64; 20] = [
        0x8000000000000000,
        0x0000000000000000,
        0x0000000000000000,
        0x3ff0000000000000,
        0x3ff0000000000000,
        0x4008000000000000,
        0x3fe0000000000000,
        0x3ff0000000000000,
        0x4008000000000000,
        0xbff0000000000000,
        0x01a56e1fc2f8f359,
        0x3ff0000000000000,
        0x4014000000000000,
        0x4000000000000000,
        0x0000000000000000,
        0x3ff0000000000000,
        0x401c000000000000,
        0x3fd5555555555555,
        0xc000000000000000,
        0x3ff0000000000000,
    ];
    // CIRCLE: order 3, rational True, 10 knots, 9 CVs, 522 bytes
    const CIRCLE_RECORD: &str = "fa7f0200fe01000000000000fbff02001400000000000000ddd4d74e47e9d311bfe50010830122f010f27870fcff0200c60100000000000011030000000100000003000000090000000000000000000000000000000000f03f00000000000000000000000000000000000000000000f0bf000000000000000000000000000000000a00000000000000000000000000000000000000182d4454fb21f93f182d4454fb21f93f182d4454fb210940182d4454fb210940d221337f7cd91240d221337f7cd91240182d4454fb211940182d4454fb21194009000000000000000000184000000000000000400000000000000840000000000000f03fd96cdfcc76f810405254af998acc1340d96cdfcc76f80040cc3b7f669ea0e63f000000000000f03f0000000000001c400000000000000840000000000000f03fcc3b7f669ea006c05254af998acc1340d96cdfcc76f80040cc3b7f669ea0e63f00000000000010c000000000000000400000000000000840000000000000f03fcc3b7f669ea006c0d96cdfcc76f800c0d96cdfcc76f80040cc3b7f669ea0e63f000000000000f03f00000000000008c00000000000000840000000000000f03fd96cdfcc76f81040d96cdfcc76f800c0d96cdfcc76f80040cc3b7f669ea0e63f000000000000184000000000000000400000000000000840000000000000f03f009bfbc7bdff7f02800000000000000000";
    const CIRCLE_KNOTS: [u64; 10] = [
        0x0000000000000000,
        0x0000000000000000,
        0x3ff921fb54442d18,
        0x3ff921fb54442d18,
        0x400921fb54442d18,
        0x400921fb54442d18,
        0x4012d97c7f3321d2,
        0x4012d97c7f3321d2,
        0x401921fb54442d18,
        0x401921fb54442d18,
    ];
    const CIRCLE_CVS: [u64; 36] = [
        0x4018000000000000,
        0x4000000000000000,
        0x4008000000000000,
        0x3ff0000000000000,
        0x4010f876ccdf6cd9,
        0x4013cc8a99af5452,
        0x4000f876ccdf6cd9,
        0x3fe6a09e667f3bcc,
        0x3ff0000000000000,
        0x401c000000000000,
        0x4008000000000000,
        0x3ff0000000000000,
        0xc006a09e667f3bcc,
        0x4013cc8a99af5452,
        0x4000f876ccdf6cd9,
        0x3fe6a09e667f3bcc,
        0xc010000000000000,
        0x4000000000000000,
        0x4008000000000000,
        0x3ff0000000000000,
        0xc006a09e667f3bcc,
        0xc000f876ccdf6cd9,
        0x4000f876ccdf6cd9,
        0x3fe6a09e667f3bcc,
        0x3ff0000000000000,
        0xc008000000000000,
        0x4008000000000000,
        0x3ff0000000000000,
        0x4010f876ccdf6cd9,
        0xc000f876ccdf6cd9,
        0x4000f876ccdf6cd9,
        0x3fe6a09e667f3bcc,
        0x4018000000000000,
        0x4000000000000000,
        0x4008000000000000,
        0x3ff0000000000000,
    ];

    fn unhex(s: &str) -> Vec<u8> {
        (0..s.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
            .collect()
    }

    fn fixtures() -> [(Vec<u8>, &'static [u64], &'static [u64]); 2] {
        [
            (unhex(CUBIC_RECORD), &CUBIC_KNOTS, &CUBIC_CVS),
            (unhex(CIRCLE_RECORD), &CIRCLE_KNOTS, &CIRCLE_CVS),
        ]
    }

    fn chunk(tcode: u32, body: &[u8], crc: bool) -> Vec<u8> {
        let length = body.len() + if crc { 4 } else { 0 };
        let mut out = tcode.to_le_bytes().to_vec();
        out.extend((length as i64).to_le_bytes());
        out.extend(body);
        if crc {
            out.extend(crc32(body).to_le_bytes());
        }
        out
    }

    /// A record around `payload`, framed and checksummed as `WriteObject` frames it.
    fn build(payload: &[u8]) -> Vec<u8> {
        let mut inner = chunk(TCODE_CLASS_UUID, &NURBS_CURVE_UUID, true);
        inner.extend(chunk(TCODE_CLASS_DATA, payload, true));
        inner.extend(TCODE_CLASS_END.to_le_bytes());
        inner.extend(0i64.to_le_bytes());
        chunk(TCODE_CLASS, &inner, false)
    }

    fn payload(record: &[u8]) -> Vec<u8> {
        let length = i64::from_le_bytes(record[48..56].try_into().unwrap()) as usize;
        record[56..56 + length - 4].to_vec()
    }

    /// `(status, shape, knots, cvs)` of one export call, buffers sized the way
    /// nurbs_data.py sizes them from the record length and pre-filled with a sentinel.
    fn call(data: &[u8]) -> (i32, [usize; 4], Vec<f64>, Vec<f64>) {
        let mut shape = [7usize; 4];
        let mut knots = vec![-9.0; data.len() / 8];
        let mut cvs = vec![-9.0; data.len() / 6];
        let status = unsafe {
            narwhal_pure_core_nurbs_curve_record(
                data.as_ptr(),
                data.len(),
                knots.as_mut_ptr(),
                knots.len(),
                cvs.as_mut_ptr(),
                cvs.len(),
                shape.as_mut_ptr(),
            )
        };
        (status, shape, knots, cvs)
    }

    fn untouched(got: &(i32, [usize; 4], Vec<f64>, Vec<f64>)) -> bool {
        got.1 == [7; 4] && got.2.iter().chain(&got.3).all(|&v| v == -9.0)
    }

    #[test]
    fn a_record_gives_the_per_point_bits() {
        for (record, knots, cvs) in fixtures() {
            let (status, shape, got_knots, got_cvs) = call(&record);
            assert_eq!(status, 0);
            assert_eq!(shape[2..], [knots.len(), cvs.len() / 4]);
            let bits = |v: &[f64]| v.iter().map(|x| x.to_bits()).collect::<Vec<_>>();
            assert_eq!(bits(&got_knots[..knots.len()]), knots);
            assert_eq!(bits(&got_cvs[..cvs.len()]), cvs);
            assert!(got_knots[knots.len()..].iter().all(|&v| v == -9.0));
            assert!(got_cvs[cvs.len()..].iter().all(|&v| v == -9.0));
        }
        assert_eq!(call(&unhex(CUBIC_RECORD)).1, [4, 0, 7, 5]);
        assert_eq!(call(&unhex(CIRCLE_RECORD)).1, [3, 1, 10, 9]);
    }

    #[test]
    fn the_builder_frames_a_record_as_rhino3dm_does() {
        for (record, _, _) in fixtures() {
            assert_eq!(build(&payload(&record)), record);
        }
    }

    #[test]
    fn the_outer_record_boundary_is_exact() {
        for (record, _, _) in fixtures() {
            let mut zero_length = record.clone();
            zero_length[4..12].copy_from_slice(&0i64.to_le_bytes());
            let mut extra = record.clone();
            extra.extend(b"extra");
            let mut longer = record.clone(); // an outer length that covers the extra bytes
            longer.extend(b"extra");
            let n = (longer.len() - HEADER) as i64;
            longer[4..12].copy_from_slice(&n.to_le_bytes());
            let mut end_value = record.clone();
            let at = record.len() - 8;
            end_value[at..].copy_from_slice(&1i64.to_le_bytes());
            for bad in [
                zero_length,
                extra,
                longer,
                end_value,
                record[..record.len() - 4].to_vec(),
                record[..record.len() - 12].to_vec(), // no class-end chunk
            ] {
                assert_eq!(read(&bad), None);
                let got = call(&bad);
                assert_eq!(got.0, NOT_RECOGNISED);
                assert!(untouched(&got));
            }
        }
    }

    #[test]
    fn every_prefix_and_every_changed_byte_is_refused() {
        for (record, _, _) in fixtures() {
            for n in 0..record.len() {
                assert_eq!(read(&record[..n]), None, "prefix {n}");
            }
            for i in 0..record.len() {
                for flip in [0x01u8, 0x80, 0xFF] {
                    let mut bad = record.clone();
                    bad[i] ^= flip;
                    assert_eq!(read(&bad), None, "byte {i} ^ {flip:#x}");
                }
            }
        }
    }

    #[test]
    fn a_checksummed_payload_that_differs_is_refused() {
        let base = payload(&unhex(CUBIC_RECORD));
        assert!(read(&build(&base)).is_some());
        let set = |at: usize, bytes: &[u8]| {
            let mut p = base.clone();
            p[at..at + bytes.len()].copy_from_slice(bytes);
            p
        };
        let int = |v: i32| v.to_le_bytes();
        let cv_count_at = 77 + 8 * 7;
        let mut trailing = base.clone();
        trailing.push(0);
        let cases = [
            ("chunk version 2.0", set(0, &[0x20])),
            ("chunk version 1.2", set(0, &[0x12])),
            ("dimension 2", set(1, &int(2))),
            ("is_rat 2", set(5, &int(2))),
            ("order 1", set(9, &int(1))),
            ("cv_count below order", set(13, &int(3))),
            ("cv_count -1", set(13, &int(-1))),
            ("reserved flag 1", set(17, &int(1))),
            ("reserved flag 2", set(21, &int(1))),
            ("a knot count off by one", set(73, &int(8))),
            ("a negative knot count", set(73, &int(-7))),
            ("a CV count off by one", set(cv_count_at, &int(6))),
            ("a CV count of i32::MAX", set(cv_count_at, &int(i32::MAX))),
            ("SubD-friendly bool 2", set(base.len() - 1, &[2])),
            ("a trailing payload byte", trailing),
            (
                "no SubD-friendly bool in a 1.1 chunk",
                base[..base.len() - 1].to_vec(),
            ),
        ];
        for (name, p) in cases {
            assert_eq!(read(&build(&p)), None, "{name}");
        }
        // A 1.0 chunk has no SubD-friendly bool: that reads, with the same doubles.
        let mut v10 = base[..base.len() - 1].to_vec();
        v10[0] = 0x10;
        let (old, new) = (call(&unhex(CUBIC_RECORD)), call(&build(&v10)));
        assert_eq!((new.0, new.1), (0, old.1));
        assert_eq!(new.2[..7], old.2[..7]);
        assert_eq!(new.3[..20], old.3[..20]);
        let mut v10_with_bool = base.clone();
        v10_with_bool[0] = 0x10;
        assert_eq!(read(&build(&v10_with_bool)), None);
    }

    #[test]
    fn another_class_or_user_data_is_refused() {
        let record = unhex(CIRCLE_RECORD);
        let mut uuid = NURBS_CURVE_UUID;
        uuid[0] ^= 1;
        let mut inner = chunk(TCODE_CLASS_UUID, &uuid, true);
        inner.extend(chunk(TCODE_CLASS_DATA, &payload(&record), true));
        inner.extend(TCODE_CLASS_END.to_le_bytes());
        inner.extend(0i64.to_le_bytes());
        assert_eq!(read(&chunk(TCODE_CLASS, &inner, false)), None);
        // A TCODE_OPENNURBS_CLASS_USERDATA chunk between the data and the end.
        let mut inner = chunk(TCODE_CLASS_UUID, &NURBS_CURVE_UUID, true);
        inner.extend(chunk(TCODE_CLASS_DATA, &payload(&record), true));
        inner.extend(chunk(0x0002_7FFD, &[0x21, 0, 0, 0], false));
        inner.extend(TCODE_CLASS_END.to_le_bytes());
        inner.extend(0i64.to_le_bytes());
        assert_eq!(read(&chunk(TCODE_CLASS, &inner, false)), None);
    }

    #[test]
    fn refusals_at_the_abi_write_nothing() {
        let record = unhex(CIRCLE_RECORD);
        let mut shape = [7usize; 4];
        let mut knots = [-9.0; 10];
        let mut cvs = [-9.0; 36];
        let p = record.as_ptr();
        let n = record.len();
        let (k, c, s) = (knots.as_mut_ptr(), cvs.as_mut_ptr(), shape.as_mut_ptr());
        unsafe {
            assert_eq!(
                narwhal_pure_core_nurbs_curve_record(p, n, k, 10, c, 36, std::ptr::null_mut()),
                ERR_NULL
            );
            let misaligned = (s as *mut u8).add(1).cast::<usize>();
            assert_eq!(
                narwhal_pure_core_nurbs_curve_record(p, n, k, 10, c, 36, misaligned),
                ERR_NULL
            );
            assert_eq!(
                narwhal_pure_core_nurbs_curve_record(std::ptr::null(), n, k, 10, c, 36, s),
                ERR_NULL
            );
            assert_eq!(
                narwhal_pure_core_nurbs_curve_record(std::ptr::null(), 0, k, 10, c, 36, s),
                NOT_RECOGNISED
            );
            assert_eq!(
                narwhal_pure_core_nurbs_curve_record(p, n, std::ptr::null_mut(), 10, c, 36, s),
                ERR_NULL
            );
            assert_eq!(
                narwhal_pure_core_nurbs_curve_record(p, n, k, 10, std::ptr::null_mut(), 36, s),
                ERR_NULL
            );
        }
        assert_eq!(shape, [7; 4]);
        assert!(knots.iter().chain(&cvs).all(|&v| v == -9.0));
        unsafe {
            assert_eq!(
                narwhal_pure_core_nurbs_curve_record(p, n, k, 9, c, 36, s),
                ERR_SIZE
            );
            assert_eq!(shape, [3, 1, 10, 9]);
            assert_eq!(
                narwhal_pure_core_nurbs_curve_record(p, n, k, 10, c, 35, s),
                ERR_SIZE
            );
        }
        assert!(knots.iter().chain(&cvs).all(|&v| v == -9.0));
        assert_eq!(
            unsafe { narwhal_pure_core_nurbs_curve_record(p, n, k, 10, c, 36, s) },
            0
        );
    }

    // POLYLINE: rhino3dm 8.35.0's `PolylineCurve([(0,0,0), (1,2,3), (-0.0,5,1e-300)])
    // .Encode()`, 181 bytes; its parameters are 0, 1, 2.
    const POLYLINE_RECORD: &str = "fa7f0200a900000000000000fbff02001400000000000000e6d4d74e47e9d311bfe50010830122f04e9cbbdbfcff020071000000000000001003000000000000000000000000000000000000000000000000000000000000000000f03f000000000000004000000000000008400000000000000080000000000000144059f3f8c21f6ea501030000000000000000000000000000000000f03f000000000000004003000000731184daff7f02800000000000000000";
    const POLYLINE_POINTS: [[u64; 3]; 3] = [
        [0, 0, 0],
        [0x3ff0000000000000, 0x4000000000000000, 0x4008000000000000],
        [0x8000000000000000, 0x4014000000000000, 0x01a56e1fc2f8f359],
    ];
    const POLYLINE_PARAMS: [u64; 3] = [0, 0x3ff0000000000000, 0x4000000000000000];

    fn build_class(uuid: &[u8; 16], payload: &[u8]) -> Vec<u8> {
        let mut inner = chunk(TCODE_CLASS_UUID, uuid, true);
        inner.extend(chunk(TCODE_CLASS_DATA, payload, true));
        inner.extend(TCODE_CLASS_END.to_le_bytes());
        inner.extend(0i64.to_le_bytes());
        chunk(TCODE_CLASS, &inner, false)
    }

    fn polyline_bits(data: &[u8]) -> Option<(Vec<[u64; 3]>, Vec<u64>)> {
        let (points, params) = read_polyline(data)?.arrays();
        Some((
            points.iter().map(|p| p.map(f64::to_bits)).collect(),
            params.iter().map(|t| t.to_bits()).collect(),
        ))
    }

    #[test]
    fn a_polyline_record_gives_its_points_and_parameters() {
        let record = unhex(POLYLINE_RECORD);
        assert_eq!(read_polyline(&record).unwrap().point_count, 3);
        assert_eq!(
            polyline_bits(&record),
            Some((POLYLINE_POINTS.to_vec(), POLYLINE_PARAMS.to_vec()))
        );
        assert_eq!(build_class(&POLYLINE_CURVE_UUID, &payload(&record)), record);
        // each class is read only by its own reader
        assert_eq!(read(&record), None);
        for (nurbs, _, _) in fixtures() {
            assert_eq!(read_polyline(&nurbs), None);
        }
    }

    #[test]
    fn a_polyline_record_that_differs_is_refused() {
        let record = unhex(POLYLINE_RECORD);
        for n in 0..record.len() {
            assert_eq!(read_polyline(&record[..n]), None, "prefix {n}");
        }
        for i in 0..record.len() {
            for flip in [0x01u8, 0x80, 0xFF] {
                let mut bad = record.clone();
                bad[i] ^= flip;
                assert_eq!(read_polyline(&bad), None, "byte {i} ^ {flip:#x}");
            }
        }
        let base = payload(&record);
        let set = |at: usize, bytes: &[u8]| {
            let mut p = base.clone();
            p[at..at + bytes.len()].copy_from_slice(bytes);
            p
        };
        let int = |v: i32| v.to_le_bytes();
        let params_at = 5 + 24 * 3;
        let mut trailing = base.clone();
        trailing.push(0);
        let cases = [
            ("chunk version 1.1", set(0, &[0x11])),
            ("chunk version 2.0", set(0, &[0x20])),
            ("a point count off by one", set(1, &int(2))),
            ("a negative point count", set(1, &int(-3))),
            ("a point count of i32::MAX", set(1, &int(i32::MAX))),
            ("fewer parameters than points", set(params_at, &int(2))),
            ("more parameters than points", set(params_at, &int(4))),
            ("dimension 2", set(base.len() - 4, &int(2))),
            ("a trailing payload byte", trailing),
            ("no dimension", base[..base.len() - 4].to_vec()),
        ];
        for (name, p) in cases {
            assert_eq!(
                read_polyline(&build_class(&POLYLINE_CURVE_UUID, &p)),
                None,
                "{name}"
            );
        }
        assert!(read_polyline(&build_class(&POLYLINE_CURVE_UUID, &base)).is_some());
        // An empty polyline frames correctly and reads; `curvex` refuses fewer than 2 points.
        let mut empty = vec![0x10];
        empty.extend(int(0));
        empty.extend(int(0));
        empty.extend(int(3));
        assert_eq!(
            read_polyline(&build_class(&POLYLINE_CURVE_UUID, &empty)).map(|r| r.point_count),
            Some(0)
        );
    }

    /// A small deterministic generator for the malformed-record sweep.
    struct Lcg(u64);

    impl Lcg {
        fn next(&mut self) -> u64 {
            self.0 = self
                .0
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            self.0 >> 11
        }

        fn below(&mut self, n: usize) -> usize {
            (self.next() % n as u64) as usize
        }
    }

    #[test]
    fn malformed_records_are_refused_without_a_panic() {
        // Raw corruption, and checksummed payloads with corrupted counts, so the sweep
        // reaches the payload reader. Buffers are sized from the record length alone, as
        // nurbs_data.py sizes them: that must always be enough.
        let mut rng = Lcg(20261118);
        let mut answered = 0;
        for round in 0..20000 {
            let (record, _, _) = &fixtures()[round % 2];
            let data = if rng.below(2) == 0 {
                let mut d = record.clone();
                for _ in 0..1 + rng.below(4) {
                    match rng.below(3) {
                        0 => d.truncate(rng.below(d.len() + 1)),
                        1 => d.extend((0..rng.below(64)).map(|_| rng.next() as u8)),
                        _ if !d.is_empty() => {
                            let i = rng.below(d.len());
                            d[i] = rng.next() as u8;
                        }
                        _ => {}
                    }
                }
                d
            } else {
                let mut p = payload(record);
                let at = [1, 5, 9, 13, 17, 73, 77 + 8 * (rng.below(12))][rng.below(7)];
                if at + 4 <= p.len() {
                    let v = [0, 1, 2, 3, -1, i32::MAX, i32::MIN, rng.next() as i32][rng.below(8)];
                    p[at..at + 4].copy_from_slice(&v.to_le_bytes());
                }
                if rng.below(4) == 0 {
                    p.truncate(rng.below(p.len() + 1));
                }
                build(&p)
            };
            let got = call(&data);
            match got.0 {
                0 => {
                    let [order, rational, knot_count, cv_count] = got.1;
                    assert!(order >= 2 && cv_count >= order && rational <= 1);
                    assert_eq!(knot_count, order + cv_count - 2);
                    answered += 1;
                }
                NOT_RECOGNISED => assert!(untouched(&got)),
                status => panic!("status {status} for a {}-byte record", data.len()),
            }
        }
        assert!(answered > 0, "the sweep reaches an accepted record");
    }

    #[test]
    fn malformed_polyline_records_are_refused_without_a_panic() {
        let record = unhex(POLYLINE_RECORD);
        let mut rng = Lcg(20261121);
        let mut answered = 0;
        for _ in 0..20000 {
            let mut p = payload(&record);
            let at = [1, 5 + 24 * 3, p.len() - 4][rng.below(3)];
            let v = [0, 1, 2, 3, 4, -1, i32::MAX, i32::MIN, rng.next() as i32][rng.below(9)];
            p[at..at + 4].copy_from_slice(&v.to_le_bytes());
            match rng.below(4) {
                0 => p.truncate(rng.below(p.len() + 1)),
                1 => p.extend((0..rng.below(64)).map(|_| rng.next() as u8)),
                _ => {}
            }
            if let Some(r) = read_polyline(&build_class(&POLYLINE_CURVE_UUID, &p)) {
                let (points, params) = r.arrays();
                assert_eq!((points.len(), params.len()), (r.point_count, r.point_count));
                answered += 1;
            }
        }
        assert!(answered > 0, "the sweep reaches an accepted record");
    }
}
