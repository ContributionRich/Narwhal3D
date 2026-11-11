//! Bounded live pick/Ortho solver. Display/prompt state stays in the host.
//! Wire v1: 32 finite doubles; operations APPLY=0, FIXED=1, UNIT=2,
//! RADIUS=3, ORTHO=4. Output is four doubles (xyz, radius).
//! Input offsets: base 0..3, cursor 3..6, plane origin 6..9,
//! plane X/Y/Z 9..18, locked direction 18..21; 21/22 reserved;
//! distance 23, has-distance 24, angle degrees 25, has-angle 26,
//! incremental 27, lock (none/direction/circle = 0/1/2) 28,
//! circle radius 29, Ortho degrees 30, reserved 31. Flags are 0/1.
//! Reserved values must be finite, and are otherwise ignored.
//! Status 0=point, 1=no direction/fixed point, negative=invalid; no writes
//! on errors or status 1. Null, misaligned and overlapping buffers are refused.
//! Caller remains responsible for actual allocation validity and exclusive output access.
use std::slice;
type V = [f64; 3];
fn v(a: &[f64], i: usize) -> V {
    [a[i], a[i + 1], a[i + 2]]
}
fn add(a: V, b: V) -> V {
    [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
}
fn sub(a: V, b: V) -> V {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}
fn scale(a: V, s: f64) -> V {
    [a[0] * s, a[1] * s, a[2] * s]
}
fn dot(a: V, b: V) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}
fn unit(a: V) -> Option<V> {
    let n = dot(a, a).sqrt();
    if n < 1e-12 {
        None
    } else {
        Some(scale_div(a, n))
    }
}
fn scale_div(a: V, n: f64) -> V {
    [a[0] / n, a[1] / n, a[2] / n]
}
fn angle(a: &[f64]) -> Option<V> {
    let t = a[25].to_radians();
    let x = v(a, 9);
    let y = v(a, 12);
    let z = v(a, 15);
    unit(add(
        add(scale(x, t.cos()), scale(y, t.sin())),
        scale(z, 0.0),
    ))
}
fn fixed(a: &[f64]) -> Option<V> {
    if a[24] != 1.0 || a[27] != 0.0 {
        return None;
    }
    let d = if a[26] == 1.0 {
        angle(a)
    } else if a[28] == 1.0 {
        Some(v(a, 18))
    } else {
        None
    };
    d.map(|d| add(v(a, 0), scale(d, a[23])))
}
fn solve(op: u32, a: &[f64]) -> Result<Option<[f64; 4]>, i32> {
    if !a.iter().all(|x| x.is_finite()) {
        return Err(-2);
    }
    if ![a[24], a[26], a[27]].iter().all(|x| *x == 0.0 || *x == 1.0)
        || ![0.0, 1.0, 2.0].contains(&a[28])
        || a[23] < 0.0
        || a[29] < 0.0
    {
        return Err(-2);
    }
    let b = v(a, 0);
    let cursor = v(a, 3);
    let p = match op {
        0 => {
            if let Some(p) = fixed(a) {
                p
            } else {
                let mut p = cursor;
                if a[26] == 1.0 || a[28] == 1.0 {
                    let d = if a[26] == 1.0 {
                        angle(a).ok_or(-2)?
                    } else {
                        v(a, 18)
                    };
                    p = add(b, scale(d, dot(sub(p, b), d)));
                } else if a[28] == 2.0 {
                    let n = v(a, 15);
                    let d = sub(p, b);
                    let d = sub(d, scale(n, dot(d, n)));
                    match unit(d) {
                        Some(u) => p = add(b, scale(u, a[29])),
                        None => return Ok(Some([p[0], p[1], p[2], 0.0])),
                    }
                }
                if a[24] == 1.0 {
                    let d = sub(p, b);
                    let length = dot(d, d).sqrt();
                    if let Some(u) = unit(d) {
                        let mut r = a[23];
                        if a[27] == 1.0 {
                            if r == 0.0 {
                                return Err(-2);
                            }
                            r *= (length / r).round_ties_even().max(1.0);
                        }
                        p = add(b, scale(u, r));
                    }
                }
                p
            }
        }
        1 => match fixed(a) {
            Some(p) => p,
            None => return Ok(None),
        },
        2 => match unit(sub(cursor, b)) {
            Some(p) => p,
            None => return Ok(None),
        },
        3 => {
            let d = sub(cursor, b);
            let r = d[0].hypot(d[1]).hypot(d[2]);
            if r < 1e-12 {
                return Ok(None);
            }
            return Ok(Some([0.0, 0.0, 0.0, r]));
        }
        4 => {
            let step = a[30].to_radians();
            if step <= 0.0 {
                cursor
            } else {
                let o = v(a, 6);
                let x = v(a, 9);
                let y = v(a, 12);
                let z = v(a, 15);
                let db = sub(b, o);
                let dp = sub(cursor, o);
                let bu = dot(db, x);
                let bv = dot(db, y);
                let bw = dot(db, z);
                let du = dot(dp, x) - bu;
                let dv = dot(dp, y) - bv;
                if du == 0.0 && dv == 0.0 {
                    cursor
                } else {
                    let t = (dv.atan2(du) / step).round_ties_even() * step;
                    let snap = |q: f64| {
                        if q.abs() < 1e-12 {
                            0.0
                        } else if (q.abs() - 1.0).abs() < 1e-12 {
                            1.0_f64.copysign(q)
                        } else {
                            q
                        }
                    };
                    let cu = snap(t.cos());
                    let cv = snap(t.sin());
                    let t = du * cu + dv * cv;
                    // Preserve CPlane.to_world's left-associative arithmetic.
                    let u = bu + t * cu;
                    let v = bv + t * cv;
                    [
                        o[0] + x[0] * u + y[0] * v + z[0] * bw,
                        o[1] + x[1] * u + y[1] * v + z[1] * bw,
                        o[2] + x[2] * u + y[2] * v + z[2] * bw,
                    ]
                }
            }
        }
        _ => return Err(-1),
    };
    Ok(Some([p[0], p[1], p[2], 0.0]))
}
#[no_mangle]
pub extern "C" fn narwhal_pure_core_pickconstraint_v1_capability() -> u32 {
    1
}
/// # Safety
/// Non-null, accepted pointers must designate readable/writable allocations of
/// the specified lengths with exclusive output access. Alignment, address overflow
/// and overlap are checked before creating a slice. Nothing is retained.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_pickconstraint_v1(
    op: u32,
    input: *const f64,
    input_len: usize,
    output: *mut f64,
    output_len: usize,
) -> i32 {
    if input.is_null() || output.is_null() || input_len != 32 || output_len != 4 || op > 4 {
        return -1;
    }
    let start = input as usize;
    let dest = output as usize;
    let align = std::mem::align_of::<f64>();
    if !start.is_multiple_of(align) || !dest.is_multiple_of(align) {
        return -1;
    }
    let Some(end) = start.checked_add(32 * std::mem::size_of::<f64>()) else {
        return -1;
    };
    let Some(dest_end) = dest.checked_add(4 * std::mem::size_of::<f64>()) else {
        return -1;
    };
    if start < dest_end && dest < end {
        return -1;
    }
    let a = unsafe { slice::from_raw_parts(input, 32) };
    match solve(op, a) {
        Ok(Some(p)) if p.iter().all(|v| v.is_finite()) => {
            unsafe { std::ptr::copy_nonoverlapping(p.as_ptr(), output, 4) };
            0
        }
        Ok(Some(_)) => -3,
        Ok(None) => 1,
        Err(e) => e,
    }
}
/// Fixed typed event packet; header is checked before input is read. Input and
/// output have disjoint layout by construction; no state or pointers are retained.
#[repr(C)]
pub struct PickEventV1 {
    pub byte_size: u32,
    pub operation: u32,
    pub input: [f64; 32],
    pub output: [f64; 4],
}
#[no_mangle]
pub extern "C" fn narwhal_pure_core_pickconstraint_event_v1_capability() -> u32 {
    std::mem::size_of::<PickEventV1>() as u32
}
/// # Safety
/// An aligned non-null pointer must reference at least a readable 8-byte header;
/// a header declaring the correct byte_size must reference a complete live packet
/// with exclusive writable output access. Misalignment/null/invalid headers are
/// refused. No writes occur on error or no-result status.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_pickconstraint_event_v1(
    packet: *mut PickEventV1,
) -> i32 {
    let start = packet as usize;
    if packet.is_null()
        || !start.is_multiple_of(std::mem::align_of::<PickEventV1>())
        || start
            .checked_add(std::mem::size_of::<PickEventV1>())
            .is_none()
    {
        return -1;
    }
    let size = unsafe { std::ptr::addr_of!((*packet).byte_size).read() };
    let op = unsafe { std::ptr::addr_of!((*packet).operation).read() };
    if size != std::mem::size_of::<PickEventV1>() as u32 || op > 4 {
        return -1;
    }
    // Copy before writing: no live input reference can alias output, even when
    // this typed event entry point is called from a non-Rust host.
    let input = unsafe { std::ptr::addr_of!((*packet).input).read() };
    match solve(op, &input) {
        Ok(Some(p)) if p.iter().all(|v| v.is_finite()) => {
            unsafe { std::ptr::addr_of_mut!((*packet).output).write(p) };
            0
        }
        Ok(Some(_)) => -3,
        Ok(None) => 1,
        Err(e) => e,
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn ties_and_axes() {
        let mut a = [0.0; 32];
        a[9] = 1.0;
        a[13] = 1.0;
        a[17] = 1.0;
        a[3] = 2.5;
        a[23] = 1.0;
        a[24] = 1.0;
        a[27] = 1.0;
        assert_eq!(solve(0, &a).unwrap().unwrap()[0], 2.0);
        a[3] = 3.5;
        assert_eq!(solve(0, &a).unwrap().unwrap()[0], 4.0);
        a[3] = 1.0;
        a[4] = 5.0;
        a[30] = 90.0;
        assert_eq!(solve(4, &a).unwrap().unwrap(), [0.0, 5.0, 0.0, 0.0]);
    }
    #[test]
    fn errors_do_not_write() {
        let a = [0.0; 32];
        let mut out = [123.0; 4];
        assert_eq!(
            unsafe { narwhal_pure_core_pickconstraint_v1(99, a.as_ptr(), 32, out.as_mut_ptr(), 4) },
            -1
        );
        assert_eq!(out, [123.0; 4]);
    }
}
