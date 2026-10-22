//! SPDX-License-Identifier: GPL-3.0-or-later
//!
//! End-to-end chaining for the reference CAD application's `Join`, in the
//! native core. `narwhal3d/joinchain.py` is the complete fallback and the
//! reference for the exact ordering, reversal and closure semantics; this
//! module replaces only its Θ(N²) "scan every segment to find the next one"
//! loop with an **ordered spatial index**, so a 20,000-segment join that takes
//! the Python route tens of seconds resolves in a fraction of a second.
//!
//! The *decision* is byte-for-byte the Python algorithm: lowest unused seed;
//! grow off the end (append) then off the start (prepend); forward-orientation
//! (no-reversal) candidates before reverse; the lowest selection index within
//! each orientation; strict caller order; `distance <= tolerance`; an
//! already-closed singleton is not grown; every input appears exactly once.
//! Only the per-step *lookup* is faster.
//!
//! The spatial index is a `BTreeMap` keyed by the tolerance cell (an ordered
//! index, not a hash): for a positive tolerance a point falls in one of at
//! most 27 neighbouring cells, so each "find the next segment" step is a
//! bounded neighbourhood scan instead of a full N scan. The index is built in
//! O(N log N) and each of the N member steps touches a constant-size
//! neighbourhood, giving the O(N log N) typical behaviour the task targets.

use std::collections::{BTreeMap, VecDeque};

const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const ERR_SIZE: i32 = -3;

/// A sane ceiling on the number of segments the native route will handle; above
/// it the caller falls back to the unchanged Python implementation.
pub const MAX_SEGMENTS: i64 = 1_000_000;

#[derive(Clone, Copy, Debug)]
struct Chain {
    start: i64, // offset into the flattened member list
    len: i64,   // number of members
    closed: bool,
    head: [f64; 3],
    tail: [f64; 3],
}

#[derive(Clone, Copy, Debug)]
struct Member {
    seg: i64,  // segment index (keys stay Python; this maps by index)
    rev: bool, // must be reversed before appending
}

/// A spatial cell. For a positive tolerance the coordinates are
/// `floor(coord * 1/tol)`; for zero tolerance the canonical IEEE bits are used
/// (`-0.0 == +0.0`, matching Python tuple equality and `math.dist`).
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug)]
struct Cell(i64, i64, i64);

#[inline]
fn cell_of(p: (f64, f64, f64), exact: bool, inv_tol: f64) -> Option<Cell> {
    if exact {
        let bits = |v: f64| if v == 0.0 { 0 } else { v.to_bits() as i64 };
        return Some(Cell(bits(p.0), bits(p.1), bits(p.2)));
    }
    let cx = (p.0 * inv_tol).floor();
    let cy = (p.1 * inv_tol).floor();
    let cz = (p.2 * inv_tol).floor();
    // A floor magnitude beyond this would overflow the i64 cell coordinate;
    // such coordinates are far outside any real model, so decline to Python.
    const MAG: f64 = 4.5e15;
    if cx.abs() > MAG || cy.abs() > MAG || cz.abs() > MAG {
        return None;
    }
    Some(Cell(cx as i64, cy as i64, cz as i64))
}

/// The lowest-index unused segment in `map` whose relevant endpoint is within
/// tolerance of `at` -- the exact "first match in input order" the Python
/// `_find` / `_find_before` implement, over a bounded neighbourhood of cells
/// (27 for a positive tolerance, 1 for exact) instead of a full scan.
#[allow(clippy::too_many_arguments)]
#[inline]
fn find_in(
    map: &BTreeMap<Cell, Vec<usize>>,
    starts: &[f64],
    ends: &[f64],
    at: (f64, f64, f64),
    cell: &Cell,
    radius: i64,
    used: &[bool],
    from_start: bool,
    exact: bool,
    tol: f64,
) -> Option<usize> {
    let near = |p: (f64, f64, f64)| {
        if exact {
            p == at
        } else {
            let dx = p.0 - at.0;
            let dy = p.1 - at.1;
            let dz = p.2 - at.2;
            dx.hypot(dy).hypot(dz) <= tol
        }
    };
    let endpoint = |i: usize| {
        if from_start {
            (starts[3 * i], starts[3 * i + 1], starts[3 * i + 2])
        } else {
            (ends[3 * i], ends[3 * i + 1], ends[3 * i + 2])
        }
    };
    let lo = cell.0 - radius;
    let hi = cell.0 + radius;
    let lo1 = cell.1 - radius;
    let hi1 = cell.1 + radius;
    let lo2 = cell.2 - radius;
    let hi2 = cell.2 + radius;
    let mut best: Option<usize> = None;
    for dx in lo..=hi {
        for dy in lo1..=hi1 {
            for dz in lo2..=hi2 {
                let key = Cell(dx, dy, dz);
                let Some(list) = map.get(&key) else {
                    continue;
                };
                for &i in list {
                    if used[i] {
                        continue;
                    }
                    if near(endpoint(i)) && best.is_none_or(|b| i < b) {
                        best = Some(i);
                    }
                }
            }
        }
    }
    best
}

/// Solve the chaining exactly as `joinchain.chains` does, but with an ordered
/// spatial index for the per-step "find the next segment" lookup.
fn solve(starts: &[f64], ends: &[f64], tol: f64) -> Option<(Vec<Chain>, Vec<Member>)> {
    let n = starts.len() / 3;
    if ends.len() != starts.len() || !ends.len().is_multiple_of(3) {
        return None;
    }
    if n == 0 {
        return Some((Vec::new(), Vec::new()));
    }
    if !tol.is_finite() || tol < 0.0 {
        return None;
    }
    for v in starts.iter().chain(ends.iter()) {
        if !v.is_finite() {
            return None;
        }
    }

    let exact = tol == 0.0;
    let inv_tol = if exact { 0.0 } else { 1.0 / tol };
    let radius = if exact { 0 } else { 1 };

    let seg = |i: usize| (starts[3 * i], starts[3 * i + 1], starts[3 * i + 2]);
    let seg_end = |i: usize| (ends[3 * i], ends[3 * i + 1], ends[3 * i + 2]);

    // Build the two ordered spatial indexes (start endpoint / end endpoint).
    // The per-cell index lists are pushed in increasing segment order, so they
    // are already selection-ordered; the lowest-index candidate wins.
    let mut start_map: BTreeMap<Cell, Vec<usize>> = BTreeMap::new();
    let mut end_map: BTreeMap<Cell, Vec<usize>> = BTreeMap::new();
    for i in 0..n {
        let cs = cell_of(seg(i), exact, inv_tol)?;
        let ce = cell_of(seg_end(i), exact, inv_tol)?;
        start_map.entry(cs).or_default().push(i);
        end_map.entry(ce).or_default().push(i);
    }

    let near = |a: (f64, f64, f64), b: (f64, f64, f64)| {
        if exact {
            a == b
        } else {
            let dx = a.0 - b.0;
            let dy = a.1 - b.1;
            let dz = a.2 - b.2;
            dx.hypot(dy).hypot(dz) <= tol
        }
    };

    let mut used = vec![false; n];
    let mut chains: Vec<Chain> = Vec::new();
    let mut members: Vec<Member> = Vec::new();

    for seed in 0..n {
        if used[seed] {
            continue;
        }
        used[seed] = true;
        let mut cm: VecDeque<Member> = VecDeque::new();
        cm.push_back(Member {
            seg: seed as i64,
            rev: false,
        });
        let mut head = seg(seed);
        let mut tail = seg_end(seed);

        if !near(head, tail) {
            // Grow off the END (append): the next segment STARTS at the tail
            // (no reversal, preferred) or ENDs at it (reversed).
            loop {
                if near(tail, head) {
                    break;
                }
                let at = tail;
                let c = cell_of(at, exact, inv_tol)?;
                let c = &c;
                let nxt = find_in(
                    &start_map, starts, ends, at, c, radius, &used, true, exact, tol,
                )
                .map(|i| (i, false))
                .or_else(|| {
                    find_in(
                        &end_map, starts, ends, at, c, radius, &used, false, exact, tol,
                    )
                    .map(|i| (i, true))
                });
                let Some((i, rev)) = nxt else {
                    break;
                };
                used[i] = true;
                cm.push_back(Member { seg: i as i64, rev });
                tail = if rev { seg(i) } else { seg_end(i) };
            }
            // Grow off the START (prepend): the next segment ENDs at the head
            // (no reversal, preferred) or STARTs at it (reversed).
            loop {
                if near(tail, head) {
                    break;
                }
                let at = head;
                let c = cell_of(at, exact, inv_tol)?;
                let c = &c;
                let prv = find_in(
                    &end_map, starts, ends, at, c, radius, &used, false, exact, tol,
                )
                .map(|i| (i, false))
                .or_else(|| {
                    find_in(
                        &start_map, starts, ends, at, c, radius, &used, true, exact, tol,
                    )
                    .map(|i| (i, true))
                });
                let Some((i, rev)) = prv else {
                    break;
                };
                used[i] = true;
                cm.push_front(Member { seg: i as i64, rev });
                head = if rev { seg_end(i) } else { seg(i) };
            }
        }

        let off = members.len() as i64;
        let len = cm.len() as i64;
        let closed = near(head, tail);
        let head = [head.0, head.1, head.2];
        let tail = [tail.0, tail.1, tail.2];
        chains.push(Chain {
            start: off,
            len,
            closed,
            head,
            tail,
        });
        members.extend(cm);
    }

    Some((chains, members))
}

/// One packed call: N segment endpoints (6 f64) in, the chain / member layout
/// and chain ends out, into caller-owned arrays. There are at most N chains
/// and exactly N members (the members partition the N segments), so every out
/// array must have capacity at least N. Capacity is validated before any write
/// and the counts are published only on success; a domain rejection (non-finite
/// input, negative tolerance, or a coordinate too large for the index) returns
/// `ERR_INPUT` so the caller runs the unchanged Python implementation.
///
/// # Safety
///
/// `starts` and `ends` must each be readable for `3 * n_segments` f64s (or be
/// null when `n_segments` is zero), and every out array must be writable for
/// at least `n_segments` elements (or be null when `n_segments` is zero). The
/// caller must therefore own buffers of at least N, which the ABI contract
/// guarantees; there is no separate capacity query to satisfy first.
#[allow(clippy::too_many_arguments)]
pub unsafe fn joinchain_packed(
    n_segments: i64,
    starts: *const f64,
    ends: *const f64,
    tol: f64,
    chain_off: *mut i64,
    chain_len: *mut i64,
    chain_closed: *mut u8,
    chain_sx: *mut f64,
    chain_sy: *mut f64,
    chain_sz: *mut f64,
    chain_ex: *mut f64,
    chain_ey: *mut f64,
    chain_ez: *mut f64,
    member_seg: *mut i64,
    member_rev: *mut u8,
    out_n_chains: *mut i64,
    out_n_members: *mut i64,
) -> i32 {
    if !(0..=MAX_SEGMENTS).contains(&n_segments) {
        return ERR_INPUT;
    }
    if out_n_chains.is_null() || out_n_members.is_null() {
        return ERR_NULL;
    }
    let n = n_segments as usize;
    if n == 0 {
        unsafe {
            *out_n_chains = 0;
            *out_n_members = 0;
        }
        return 0;
    }
    let starts_slice = match unsafe { crate::checked_slice(starts, 3 * n) } {
        Some(s) => s,
        None => return if starts.is_null() { ERR_NULL } else { ERR_SIZE },
    };
    let ends_slice = match unsafe { crate::checked_slice(ends, 3 * n) } {
        Some(s) => s,
        None => return if ends.is_null() { ERR_NULL } else { ERR_SIZE },
    };
    let cs = match unsafe { crate::checked_slice_mut(chain_off, n) } {
        Some(s) => s,
        None => {
            return if chain_off.is_null() {
                ERR_NULL
            } else {
                ERR_SIZE
            }
        }
    };
    let cl = match unsafe { crate::checked_slice_mut(chain_len, n) } {
        Some(s) => s,
        None => {
            return if chain_len.is_null() {
                ERR_NULL
            } else {
                ERR_SIZE
            }
        }
    };
    let cc = match unsafe { crate::checked_slice_mut(chain_closed, n) } {
        Some(s) => s,
        None => {
            return if chain_closed.is_null() {
                ERR_NULL
            } else {
                ERR_SIZE
            }
        }
    };
    let csx = match unsafe { crate::checked_slice_mut(chain_sx, n) } {
        Some(s) => s,
        None => {
            return if chain_sx.is_null() {
                ERR_NULL
            } else {
                ERR_SIZE
            }
        }
    };
    let csy = match unsafe { crate::checked_slice_mut(chain_sy, n) } {
        Some(s) => s,
        None => {
            return if chain_sy.is_null() {
                ERR_NULL
            } else {
                ERR_SIZE
            }
        }
    };
    let csz = match unsafe { crate::checked_slice_mut(chain_sz, n) } {
        Some(s) => s,
        None => {
            return if chain_sz.is_null() {
                ERR_NULL
            } else {
                ERR_SIZE
            }
        }
    };
    let cex = match unsafe { crate::checked_slice_mut(chain_ex, n) } {
        Some(s) => s,
        None => {
            return if chain_ex.is_null() {
                ERR_NULL
            } else {
                ERR_SIZE
            }
        }
    };
    let cey = match unsafe { crate::checked_slice_mut(chain_ey, n) } {
        Some(s) => s,
        None => {
            return if chain_ey.is_null() {
                ERR_NULL
            } else {
                ERR_SIZE
            }
        }
    };
    let cez = match unsafe { crate::checked_slice_mut(chain_ez, n) } {
        Some(s) => s,
        None => {
            return if chain_ez.is_null() {
                ERR_NULL
            } else {
                ERR_SIZE
            }
        }
    };
    let ms = match unsafe { crate::checked_slice_mut(member_seg, n) } {
        Some(s) => s,
        None => {
            return if member_seg.is_null() {
                ERR_NULL
            } else {
                ERR_SIZE
            }
        }
    };
    let mr = match unsafe { crate::checked_slice_mut(member_rev, n) } {
        Some(s) => s,
        None => {
            return if member_rev.is_null() {
                ERR_NULL
            } else {
                ERR_SIZE
            }
        }
    };

    let Some((ch, mem)) = solve(starts_slice, ends_slice, tol) else {
        return ERR_INPUT;
    };
    if ch.len() > n || mem.len() != n {
        return ERR_INPUT;
    }
    for (i, c) in ch.iter().enumerate() {
        cs[i] = c.start;
        cl[i] = c.len;
        cc[i] = u8::from(c.closed);
        csx[i] = c.head[0];
        csy[i] = c.head[1];
        csz[i] = c.head[2];
        cex[i] = c.tail[0];
        cey[i] = c.tail[1];
        cez[i] = c.tail[2];
    }
    for (i, m) in mem.iter().enumerate() {
        ms[i] = m.seg;
        mr[i] = u8::from(m.rev);
    }
    unsafe {
        *out_n_chains = ch.len() as i64;
        *out_n_members = mem.len() as i64;
    }
    0
}

/// The stable C ABI export for the indexed Join-chain solver.
///
/// # Safety
///
/// As `joinchain_packed`: the endpoint and out buffers must be sized for
/// `n_segments` (zero allows nulls), and the out counts must be writable.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_joinchain(
    n_segments: i64,
    starts: *const f64,
    ends: *const f64,
    tol: f64,
    chain_off: *mut i64,
    chain_len: *mut i64,
    chain_closed: *mut u8,
    chain_sx: *mut f64,
    chain_sy: *mut f64,
    chain_sz: *mut f64,
    chain_ex: *mut f64,
    chain_ey: *mut f64,
    chain_ez: *mut f64,
    member_seg: *mut i64,
    member_rev: *mut u8,
    out_n_chains: *mut i64,
    out_n_members: *mut i64,
) -> i32 {
    joinchain_packed(
        n_segments,
        starts,
        ends,
        tol,
        chain_off,
        chain_len,
        chain_closed,
        chain_sx,
        chain_sy,
        chain_sz,
        chain_ex,
        chain_ey,
        chain_ez,
        member_seg,
        member_rev,
        out_n_chains,
        out_n_members,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    type Seg = ((f64, f64, f64), (f64, f64, f64));
    // (chain_off, chain_len, closed, [sx,sy,sz,ex,ey,ez])
    type ChainOut = (i64, i64, bool, [f64; 6]);
    type MemberOut = (i64, bool);

    /// Run the packed ABI (out arrays sized at N, the caller's obligation) and
    /// decode the results. `cap` is the array capacity; `None` sizes them at N.
    fn run(segs: &[Seg], tol: f64, cap: Option<usize>) -> (i32, Vec<ChainOut>, Vec<MemberOut>) {
        let n = segs.len();
        let cap = cap.unwrap_or(n.max(1));
        let starts: Vec<f64> = segs.iter().flat_map(|(s, _)| [s.0, s.1, s.2]).collect();
        let ends: Vec<f64> = segs.iter().flat_map(|(_, e)| [e.0, e.1, e.2]).collect();
        let mut chain_off = vec![0i64; cap];
        let mut chain_len = vec![0i64; cap];
        let mut chain_closed = vec![0u8; cap];
        let mut chain_sx = vec![0f64; cap];
        let mut chain_sy = vec![0f64; cap];
        let mut chain_sz = vec![0f64; cap];
        let mut chain_ex = vec![0f64; cap];
        let mut chain_ey = vec![0f64; cap];
        let mut chain_ez = vec![0f64; cap];
        let mut member_seg = vec![0i64; cap];
        let mut member_rev = vec![0u8; cap];
        let mut n_chains = -999;
        let mut n_members = -999;
        let rc = unsafe {
            joinchain_packed(
                n as i64,
                starts.as_ptr(),
                ends.as_ptr(),
                tol,
                chain_off.as_mut_ptr(),
                chain_len.as_mut_ptr(),
                chain_closed.as_mut_ptr(),
                chain_sx.as_mut_ptr(),
                chain_sy.as_mut_ptr(),
                chain_sz.as_mut_ptr(),
                chain_ex.as_mut_ptr(),
                chain_ey.as_mut_ptr(),
                chain_ez.as_mut_ptr(),
                member_seg.as_mut_ptr(),
                member_rev.as_mut_ptr(),
                &mut n_chains,
                &mut n_members,
            )
        };
        let nc = (n_chains.max(0) as usize).min(cap);
        let nm = (n_members.max(0) as usize).min(cap);
        let chains = (0..nc)
            .map(|i| {
                (
                    chain_off[i],
                    chain_len[i],
                    chain_closed[i] != 0,
                    [
                        chain_sx[i],
                        chain_sy[i],
                        chain_sz[i],
                        chain_ex[i],
                        chain_ey[i],
                        chain_ez[i],
                    ],
                )
            })
            .collect();
        let members = (0..nm)
            .map(|i| (member_seg[i], member_rev[i] != 0))
            .collect();
        (rc, chains, members)
    }

    fn keys(members: &[(i64, bool)]) -> Vec<i64> {
        members.iter().map(|(s, _)| *s).collect()
    }

    fn once_all(members: &[(i64, bool)], n: usize) {
        let mut all = keys(members);
        all.sort();
        assert_eq!(
            all,
            (0..n as i64).collect::<Vec<_>>(),
            "every input exactly once"
        );
    }

    #[test]
    fn three_touching_lines_are_one_open_chain_in_order_with_ends() {
        let segs = [
            ((0.0, 0.0, 0.0), (5.0, 0.0, 0.0)),
            ((5.0, 0.0, 0.0), (5.0, 4.0, 0.0)),
            ((5.0, 4.0, 0.0), (0.0, 4.0, 0.0)),
        ];
        let (rc, chains, members) = run(&segs, 0.01, None);
        assert_eq!(rc, 0, "the solver must succeed");
        assert_eq!(chains, vec![(0, 3, false, [0.0, 0.0, 0.0, 0.0, 4.0, 0.0])]);
        assert_eq!(keys(&members), vec![0, 1, 2]);
        assert!(!members.iter().any(|(_, r)| *r), "none needs reversing");
    }

    #[test]
    fn a_backwards_middle_segment_is_flagged_for_reversal() {
        let segs = [
            ((0.0, 0.0, 0.0), (5.0, 0.0, 0.0)),
            ((5.0, 4.0, 0.0), (5.0, 0.0, 0.0)), // backwards
            ((5.0, 4.0, 0.0), (0.0, 4.0, 0.0)),
        ];
        let (rc, chains, members) = run(&segs, 0.01, None);
        assert_eq!(rc, 0);
        assert_eq!(chains, vec![(0, 3, false, [0.0, 0.0, 0.0, 0.0, 4.0, 0.0])]);
        assert_eq!(members, vec![(0, false), (1, true), (2, false)]);
    }

    #[test]
    fn a_run_seeded_from_the_middle_comes_back_in_geometric_order() {
        let segs = [
            ((5.0, 0.0, 0.0), (5.0, 4.0, 0.0)), // mid (seed)
            ((0.0, 0.0, 0.0), (5.0, 0.0, 0.0)), // first
            ((5.0, 4.0, 0.0), (0.0, 4.0, 0.0)), // last
        ];
        let (rc, chains, members) = run(&segs, 0.01, None);
        assert_eq!(rc, 0);
        assert_eq!(chains.len(), 1);
        assert_eq!(chains[0].1, 3);
        assert_eq!(
            keys(&members),
            vec![1, 0, 2],
            "geometric order first, mid, last"
        );
    }

    #[test]
    fn a_loop_is_reported_closed_with_all_members_once() {
        let segs = [
            ((0.0, 0.0, 0.0), (5.0, 0.0, 0.0)),
            ((5.0, 0.0, 0.0), (5.0, 4.0, 0.0)),
            ((5.0, 4.0, 0.0), (0.0, 4.0, 0.0)),
            ((0.0, 4.0, 0.0), (0.0, 0.0, 0.0)),
        ];
        let (rc, chains, members) = run(&segs, 0.01, None);
        assert_eq!(rc, 0);
        assert_eq!(chains, vec![(0, 4, true, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0])]);
        once_all(&members, 4);
    }

    #[test]
    fn two_disjoint_runs_are_two_chains_and_every_input_once() {
        let segs = [
            ((0.0, 0.0, 0.0), (5.0, 0.0, 0.0)),
            ((5.0, 0.0, 0.0), (5.0, 4.0, 0.0)),
            ((50.0, 0.0, 0.0), (55.0, 0.0, 0.0)),
            ((55.0, 0.0, 0.0), (55.0, 4.0, 0.0)),
        ];
        let (rc, chains, members) = run(&segs, 0.01, None);
        assert_eq!(rc, 0);
        assert_eq!(
            chains,
            vec![
                (0, 2, false, [0.0, 0.0, 0.0, 5.0, 4.0, 0.0]),
                (2, 2, false, [50.0, 0.0, 0.0, 55.0, 4.0, 0.0]),
            ]
        );
        once_all(&members, 4);
    }

    #[test]
    fn a_lone_curve_is_a_chain_of_one() {
        let segs = [((0.0, 0.0, 0.0), (1.0, 1.0, 0.0))];
        let (rc, chains, members) = run(&segs, 0.01, None);
        assert_eq!(rc, 0);
        assert_eq!(chains, vec![(0, 1, false, [0.0, 0.0, 0.0, 1.0, 1.0, 0.0])]);
        assert_eq!(members, vec![(0, false)]);
    }

    #[test]
    fn a_closed_curve_is_not_spliced_into() {
        let segs = [
            ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)), // closed ring (seed)
            ((0.0, 0.0, 0.0), (9.0, 0.0, 0.0)), // tail
        ];
        let (rc, chains, _members) = run(&segs, 0.01, None);
        assert_eq!(rc, 0);
        // Both are single-curve chains (neither joins anything), so the
        // `joinable` filter (len >= 2) drops them both.
        assert_eq!(
            chains,
            vec![
                (0, 1, true, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
                (1, 1, false, [0.0, 0.0, 0.0, 9.0, 0.0, 0.0])
            ]
        );
    }

    #[test]
    fn tolerance_is_respected_in_both_directions() {
        let inside = [
            ((0.0, 0.0, 0.0), (5.0, 0.0, 0.0)),
            ((5.0, 0.005, 0.0), (5.0, 4.0, 0.0)),
        ];
        let outside = [
            ((0.0, 0.0, 0.0), (5.0, 0.0, 0.0)),
            ((5.0, 0.5, 0.0), (5.0, 4.0, 0.0)),
        ];
        let (rc, chains, _) = run(&inside, 0.01, None);
        assert_eq!(rc, 0);
        assert_eq!(
            chains,
            vec![(0, 2, false, [0.0, 0.0, 0.0, 5.0, 4.0, 0.0])],
            "inside joins"
        );
        let (rc, chains, _) = run(&outside, 0.01, None);
        assert_eq!(rc, 0);
        assert_eq!(
            chains,
            vec![
                (0, 1, false, [0.0, 0.0, 0.0, 5.0, 0.0, 0.0]),
                (1, 1, false, [5.0, 0.5, 0.0, 5.0, 4.0, 0.0])
            ],
            "outside does not"
        );
    }

    #[test]
    fn ambiguous_junction_takes_the_lowest_index_within_an_orientation() {
        // Two unused segments both START at the junction; the lower index wins.
        let segs = [
            ((0.0, 0.0, 0.0), (5.0, 0.0, 0.0)),
            ((5.0, 0.0, 0.0), (10.0, 0.0, 0.0)),
            ((5.0, 0.0, 0.0), (5.0, 5.0, 0.0)), // same junction, higher index
        ];
        let (rc, chains, members) = run(&segs, 0.01, None);
        assert_eq!(rc, 0);
        // The seed grows to index 1 (the first candidate); index 2 is a lone chain.
        assert_eq!(
            chains,
            vec![
                (0, 2, false, [0.0, 0.0, 0.0, 10.0, 0.0, 0.0]),
                (2, 1, false, [5.0, 0.0, 0.0, 5.0, 5.0, 0.0])
            ]
        );
        assert_eq!(keys(&members), vec![0, 1, 2]);
    }

    /// The MUTATION TARGET (Q117-06): at the junction J the seed can take
    /// index 1 (which ENDS at J -- a REVERSED candidate, the LOWER index) or
    /// index 2 (which STARTS at J -- a no-reversal candidate, the higher
    /// index). The orientation preference (forward before reverse) must pick
    /// index 2, NOT the lower-index reversed index 1. Reversing the preference
    /// would pick index 1 reversed and this assertion goes RED.
    #[test]
    fn t_junction_prefers_no_reversal_over_lower_index_reversed() {
        let segs = [
            // index 0 (the seed): grows to the junction J at its end
            ((0.0, 0.0, 0.0), (5.0, 0.0, 0.0)),
            // index 1: ENDS at J -- a reversed candidate, the LOWER index
            ((9.0, 0.0, 0.0), (5.0, 0.0, 0.0)),
            // index 2: STARTS at J -- a no-reversal candidate, higher index
            ((5.0, 0.0, 0.0), (5.0, 4.0, 0.0)),
        ];
        let (rc, _chains, members) = run(&segs, 0.01, None);
        assert_eq!(rc, 0);
        // The seed is index 0. Off its end (J) it must take index 2 (no reversal)
        // BEFORE the lower-index reversed index 1. So the seed's chain is
        // [0, 2] and index 1 is the leftover lone chain.
        let m: Vec<MemberOut> = members;
        assert_eq!(m[0], (0, false), "the seed");
        assert_eq!(
            m[1],
            (2, false),
            "no-reversal candidate beats lower-index reversed"
        );
    }

    #[test]
    fn exact_matching_at_zero_tolerance() {
        let exact = [
            ((0.0, 0.0, 0.0), (5.0, 0.0, 0.0)),
            ((5.0, 0.0, 0.0), (5.0, 4.0, 0.0)),
        ];
        let near_only = [
            ((0.0, 0.0, 0.0), (5.0, 0.0, 0.0)),
            ((5.0, 1e-9, 0.0), (5.0, 4.0, 0.0)),
        ];
        let (rc, chains, _) = run(&exact, 0.0, None);
        assert_eq!(rc, 0);
        assert_eq!(
            chains,
            vec![(0, 2, false, [0.0, 0.0, 0.0, 5.0, 4.0, 0.0])],
            "exact joins at tol 0"
        );
        let (rc, chains, _) = run(&near_only, 0.0, None);
        assert_eq!(rc, 0);
        assert_eq!(
            chains,
            vec![
                (0, 1, false, [0.0, 0.0, 0.0, 5.0, 0.0, 0.0]),
                (1, 1, false, [5.0, 1e-9, 0.0, 5.0, 4.0, 0.0])
            ],
            "a 1e-9 gap does not, at tol 0"
        );
    }

    #[test]
    fn zero_tolerance_canonicalizes_signed_zero() {
        let segs = [
            ((-1.0, 0.0, 0.0), (-0.0, 0.0, 0.0)),
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
        ];
        let (rc, chains, members) = run(&segs, 0.0, None);
        assert_eq!(rc, 0);
        assert_eq!(chains.len(), 1, "-0.0 and +0.0 are equal in Python");
        assert_eq!(keys(&members), vec![0, 1]);
    }

    #[test]
    fn large_finite_tolerance_does_not_overflow_into_a_false_join() {
        let segs = [
            ((-9.0e307, 0.0, 0.0), (-9.0e307, 1.0, 0.0)),
            ((9.0e307, 1.0, 0.0), (9.0e307, 2.0, 0.0)),
        ];
        let (rc, chains, _) = run(&segs, 1.0e308, None);
        assert_eq!(rc, 0);
        assert_eq!(chains.len(), 2, "distance exceeds the finite tolerance");
    }

    #[test]
    fn rejects_non_finite_and_negative_tolerance() {
        let segs = [
            ((0.0, 0.0, 0.0), (5.0, 0.0, 0.0)),
            ((5.0, 0.0, 0.0), (5.0, 4.0, 0.0)),
        ];
        assert_eq!(
            run(&segs, -1.0, None).0,
            ERR_INPUT,
            "negative tol is a domain rejection"
        );
        assert_eq!(
            run(&segs, f64::NAN, None).0,
            ERR_INPUT,
            "NaN tol is a domain rejection"
        );
        let nan_seg = [
            ((0.0, 0.0, 0.0), (5.0, 0.0, 0.0)),
            ((5.0, f64::NAN, 0.0), (5.0, 4.0, 0.0)),
        ];
        assert_eq!(
            run(&nan_seg, 0.01, None).0,
            ERR_INPUT,
            "a NaN coordinate is a domain rejection"
        );
    }

    #[test]
    fn empty_input_succeeds_with_zero_counts() {
        let (rc, chains, members) = run(&[], 0.01, None);
        assert_eq!(rc, 0);
        assert!(chains.is_empty() && members.is_empty());
    }

    #[test]
    fn large_disjoint_set_is_fast_and_every_input_once() {
        // 2,000 disjoint pairs far apart -- the Θ(N²) case that the index fixes.
        let n = 2000;
        let mut segs: Vec<Seg> = Vec::with_capacity(n * 2);
        for i in 0..n {
            let base = (i * 1000) as f64;
            segs.push(((base, 0.0, 0.0), (base + 1.0, 0.0, 0.0)));
            segs.push(((base + 1.0, 0.0, 0.0), (base + 2.0, 0.0, 0.0)));
        }
        let (rc, chains, members) = run(&segs, 0.01, None);
        assert_eq!(rc, 0);
        assert_eq!(chains.len(), n, "each pair is its own chain");
        once_all(&members, n * 2);
    }
}
