//! Conservative qualification of exact rational Bezier patches, not a sampler.
//!
//! OURS: this is a sufficient, deliberately incomplete Freeform Sweep1 policy.
//! Positive principal minors of the projected Jacobian on a parameter rectangle
//! imply global univalence (the P-matrix criterion). The caller must supply all
//! patches of the surface, consistent fixed projection vectors, positive knot
//! parameter scales, and global C1 continuity before applying that conclusion to
//! the whole surface. This module certifies only the supplied patch.
//!
//! All input floats are treated as exact real numbers. Every arithmetic operation
//! used in the proof is enclosed with outward rounding, including centering,
//! projection and homogeneous subdivision. No mesh, fitted replacement or sampled
//! geometry is produced. IEEE-754 gradual underflow is required; do not compile
//! this proof with fast-math or run it with flush-to-zero enabled.

const MAX_DEPTH: u32 = 10;
const MAX_CELLS: u32 = 4096;
const NONFINITE: &str = "nonfinite interval arithmetic";
const NONPOSITIVE: &str = "projected derivative or determinant is nonpositive";
const DEPTH_LIMIT: &str = "patch could not be certified within the depth limit";
const CELL_LIMIT: &str = "patch could not be certified within the cell budget";

/// A certificate for the entire closed parameter rectangle of one patch.
#[derive(Clone, Copy, Debug)]
pub struct PatchQuality {
    /// Number of examined cells, including internal subdivision nodes.
    pub cells: u32,
    /// Minimum certified lower bound of `Axu * Azv - Axv * Azu` over
    /// accepted leaf cells, where `Aqt = Nqt * W - Nq * Wt`.
    ///
    /// Derivatives are with respect to each leaf's LOCAL unit-square coordinates.
    /// This is a positive homogeneous numerator bound, NOT a global-parameter or
    /// physical Jacobian bound. Each midpoint split scales both derivatives by
    /// 1/2, hence its numerator determinant by 1/4. The projected Jacobian also
    /// has a denominator W^4. Rescaling all input weights changes this number.
    pub min_determinant: f64,
}

/// Prove `Xu > 0`, `Zv > 0` and `Xu*Zv - Xv*Zu > 0` everywhere,
/// with `X = S.across` and `Z = S.axis`.
///
/// The Bezier control net has U degree 2 and V degree `v_degree` (2..=12).
/// Poles and positive weights are u-major: index `u * (v_degree + 1) + v`.
/// Projection vectors need not be unit length or orthogonal; their supplied
/// orientation and scale are used unchanged. Zero/dependent vectors cannot
/// yield a certificate. Coordinates are centered at the first pole before
/// projection, which leaves all projected derivatives unchanged.
///
/// An error means invalid input, a proved violation, or inability to certify;
/// it does not in general prove that the surface folds. Work is bounded by
/// 10 quadtree subdivision levels and 4096 examined cells. No partial certificate
/// is returned when even one cell remains unresolved.
pub fn qualify_patch(
    poles: &[[f64; 3]],
    weights: &[f64],
    v_degree: usize,
    axis: [f64; 3],
    across: [f64; 3],
) -> Result<PatchQuality, &'static str> {
    if !(2..=12).contains(&v_degree) {
        return Err("V degree must be in 2..=12");
    }
    let count = 3 * (v_degree + 1);
    if poles.len() != count || weights.len() != count {
        return Err("invalid u-major Bezier control net size");
    }
    if weights.iter().any(|w| !w.is_finite() || *w <= 0.0) {
        return Err("weights must be finite and strictly positive");
    }
    if poles
        .iter()
        .flatten()
        .chain(axis.iter())
        .chain(across.iter())
        .any(|x| !x.is_finite())
    {
        return Err("poles and projection vectors must be finite");
    }

    let origin = poles[0];
    let mut controls = Vec::with_capacity(count);
    for (pole, &weight) in poles.iter().zip(weights) {
        let centered = std::array::from_fn::<_, 3, _>(|k| {
            Interval::point(pole[k]).sub(Interval::point(origin[k]))
        });
        let w = Interval::point(weight);
        let h = [
            project(centered, across).mul(w),
            project(centered, axis).mul(w),
            w,
        ];
        if !h.iter().all(|x| x.finite()) {
            return Err(NONFINITE);
        }
        controls.push(h);
    }
    certify(Cell { controls, v_degree }, MAX_DEPTH, MAX_CELLS)
}

#[derive(Clone, Copy, Debug)]
struct Interval {
    lo: f64,
    hi: f64,
}

impl Interval {
    const ZERO: Self = Self { lo: 0.0, hi: 0.0 };
    const WHOLE: Self = Self {
        lo: f64::NEG_INFINITY,
        hi: f64::INFINITY,
    };

    fn point(x: f64) -> Self {
        Self { lo: x, hi: x }
    }

    fn finite(self) -> bool {
        self.lo.is_finite() && self.hi.is_finite() && self.lo <= self.hi
    }

    fn rounded(lo: f64, hi: f64) -> Self {
        // Never let NaN disappear through f64::min/max. Overflow is an explicit
        // failure of the proof, not a usable one-sided bound.
        if !lo.is_finite() || !hi.is_finite() {
            return Self::WHOLE;
        }
        Self {
            lo: lo.next_down(),
            hi: hi.next_up(),
        }
    }

    fn add(self, rhs: Self) -> Self {
        if !self.finite() || !rhs.finite() {
            return Self::WHOLE;
        }
        Self::rounded(self.lo + rhs.lo, self.hi + rhs.hi)
    }

    fn sub(self, rhs: Self) -> Self {
        if !self.finite() || !rhs.finite() {
            return Self::WHOLE;
        }
        Self::rounded(self.lo - rhs.hi, self.hi - rhs.lo)
    }

    fn mul(self, rhs: Self) -> Self {
        if !self.finite() || !rhs.finite() {
            return Self::WHOLE;
        }
        let products = [
            self.lo * rhs.lo,
            self.lo * rhs.hi,
            self.hi * rhs.lo,
            self.hi * rhs.hi,
        ];
        if products.iter().any(|p| !p.is_finite()) {
            return Self::WHOLE;
        }
        let lo = products.iter().copied().fold(f64::INFINITY, f64::min);
        let hi = products.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        Self::rounded(lo, hi)
    }

    fn hull(self, rhs: Self) -> Self {
        if !self.finite() || !rhs.finite() {
            return Self::WHOLE;
        }
        Self {
            lo: self.lo.min(rhs.lo),
            hi: self.hi.max(rhs.hi),
        }
    }

    fn midpoint(self, rhs: Self) -> Self {
        // Halve before adding so a finite convex average need not overflow.
        self.mul(Self::point(0.5)).add(rhs.mul(Self::point(0.5)))
    }
}

fn project(p: [Interval; 3], direction: [f64; 3]) -> Interval {
    (0..3).fold(Interval::ZERO, |sum, k| {
        sum.add(p[k].mul(Interval::point(direction[k])))
    })
}

// Components are homogeneous projected Nx, Nz, W, not Euclidean poles.
type Homogeneous = [Interval; 3];

struct Cell {
    controls: Vec<Homogeneous>,
    v_degree: usize,
}

impl Cell {
    fn at(&self, u: usize, v: usize) -> Homogeneous {
        self.controls[u * (self.v_degree + 1) + v]
    }

    fn bounds(&self) -> [Interval; 3] {
        let mut bounds = self.controls[0];
        for h in &self.controls[1..] {
            for k in 0..3 {
                bounds[k] = bounds[k].hull(h[k]);
            }
        }
        bounds
    }

    fn derivative_bounds(&self, along_u: bool) -> [Interval; 3] {
        let degree = if along_u { 2 } else { self.v_degree };
        let nu = if along_u { 2 } else { 3 };
        let nv = if along_u {
            self.v_degree + 1
        } else {
            self.v_degree
        };
        let mut bounds = [Interval::ZERO; 3];
        for u in 0..nu {
            for v in 0..nv {
                let a = self.at(u, v);
                let b = if along_u {
                    self.at(u + 1, v)
                } else {
                    self.at(u, v + 1)
                };
                for k in 0..3 {
                    let d = b[k].sub(a[k]).mul(Interval::point(degree as f64));
                    bounds[k] = if u == 0 && v == 0 {
                        d
                    } else {
                        bounds[k].hull(d)
                    };
                }
            }
        }
        bounds
    }

    // Bernstein bases are nonnegative and sum to one on the closed rectangle;
    // these hulls therefore enclose N, W and their polynomial derivatives.
    fn principal_minors(&self) -> Result<[Interval; 3], &'static str> {
        let n = self.bounds();
        let du = self.derivative_bounds(true);
        let dv = self.derivative_bounds(false);
        if !n
            .iter()
            .chain(du.iter())
            .chain(dv.iter())
            .all(|x| x.finite())
        {
            return Err(NONFINITE);
        }
        if n[2].lo <= 0.0 {
            return Err("positive homogeneous denominator could not be certified");
        }
        let axu = du[0].mul(n[2]).sub(n[0].mul(du[2]));
        let axv = dv[0].mul(n[2]).sub(n[0].mul(dv[2]));
        let azu = du[1].mul(n[2]).sub(n[1].mul(du[2]));
        let azv = dv[1].mul(n[2]).sub(n[1].mul(dv[2]));
        let det = axu.mul(azv).sub(axv.mul(azu));
        if ![axu, axv, azu, azv, det].iter().all(|x| x.finite()) {
            return Err(NONFINITE);
        }
        // Actual principal minors divide these numerators by W^2 or W^4.
        Ok([axu, azv, det])
    }

    fn split(&self, along_u: bool) -> (Self, Self) {
        let mut left = vec![[Interval::ZERO; 3]; self.controls.len()];
        let mut right = left.clone();
        let degree = if along_u { 2 } else { self.v_degree };
        let lines = if along_u { self.v_degree + 1 } else { 3 };
        for line in 0..lines {
            let index = |i: usize| {
                if along_u {
                    i * (self.v_degree + 1) + line
                } else {
                    line * (self.v_degree + 1) + i
                }
            };
            let mut work: Vec<_> = (0..=degree).map(|i| self.controls[index(i)]).collect();
            left[index(0)] = work[0];
            right[index(degree)] = work[degree];
            for level in 1..=degree {
                for i in 0..=degree - level {
                    work[i] = std::array::from_fn(|k| work[i][k].midpoint(work[i + 1][k]));
                }
                left[index(level)] = work[0];
                right[index(degree - level)] = work[degree - level];
            }
        }
        (
            Self {
                controls: left,
                v_degree: self.v_degree,
            },
            Self {
                controls: right,
                v_degree: self.v_degree,
            },
        )
    }
}

fn certify(root: Cell, max_depth: u32, max_cells: u32) -> Result<PatchQuality, &'static str> {
    let mut pending = vec![(root, 0)];
    let mut quality = PatchQuality {
        cells: 0,
        min_determinant: f64::INFINITY,
    };
    while let Some((cell, depth)) = pending.pop() {
        if quality.cells >= max_cells {
            return Err(CELL_LIMIT);
        }
        quality.cells += 1;
        let minors = cell.principal_minors()?;
        if minors.iter().any(|m| m.hi <= 0.0) {
            return Err(NONPOSITIVE);
        }
        if minors.iter().all(|m| m.lo > 0.0) {
            quality.min_determinant = quality.min_determinant.min(minors[2].lo);
            continue;
        }
        if depth >= max_depth {
            return Err(DEPTH_LIMIT);
        }
        let (u0, u1) = cell.split(true);
        let (c00, c01) = u0.split(false);
        let (c10, c11) = u1.split(false);
        // Depth-first traversal keeps live storage proportional to depth.
        for child in [c11, c10, c01, c00] {
            pending.push((child, depth + 1));
        }
    }
    Ok(quality)
}

#[cfg(test)]
mod tests {
    use super::*;

    const AXIS: [f64; 3] = [0.0, 0.0, 1.0];
    const ACROSS: [f64; 3] = [1.0, 0.0, 0.0];

    fn net(degree: usize, f: impl Fn(usize, usize) -> [f64; 3]) -> Vec<[f64; 3]> {
        let mut poles = Vec::new();
        for u in 0..3 {
            for v in 0..=degree {
                poles.push(f(u, v));
            }
        }
        poles
    }

    fn qualify(poles: &[[f64; 3]], degree: usize) -> Result<PatchQuality, &'static str> {
        qualify_patch(poles, &vec![1.0; poles.len()], degree, AXIS, ACROSS)
    }

    fn polynomial_cell(poles: &[[f64; 3]], degree: usize) -> Cell {
        Cell {
            controls: poles
                .iter()
                .map(|p| {
                    [
                        Interval::point(p[0]),
                        Interval::point(p[2]),
                        Interval::point(1.0),
                    ]
                })
                .collect(),
            v_degree: degree,
        }
    }

    #[test]
    fn outward_rounding_encloses_lost_bits_and_underflow() {
        let one = Interval::point(1.0);
        let sum = one.add(Interval::point(f64::EPSILON / 2.0));
        assert!(sum.lo <= 1.0 && sum.hi >= 1.0_f64.next_up());
        let difference = one.sub(Interval::point(f64::EPSILON / 4.0));
        assert!(difference.lo <= 1.0_f64.next_down() && difference.hi >= 1.0);
        let product = Interval::point(1.0 + f64::EPSILON).mul(Interval::point(1.0 - f64::EPSILON));
        assert!(product.lo < 1.0 && product.hi >= 1.0);
        let tiny = f64::from_bits(1);
        let underflow = Interval::point(tiny).mul(Interval::point(0.5));
        assert!(underflow.lo <= 0.0 && underflow.hi >= tiny);
        let signed = Interval { lo: -2.0, hi: 3.0 }.mul(Interval { lo: -5.0, hi: 7.0 });
        assert!(signed.lo <= -15.0 && signed.hi >= 21.0);
        assert!(!Interval::point(f64::MAX).mul(Interval::point(2.0)).finite());
    }

    #[test]
    fn regular_rational_curved_patches_with_weights_varying_in_both_directions() {
        for degree in [2, 3, 12] {
            let poles = net(degree, |u, v| {
                [
                    u as f64 / 2.0,
                    if u == 1 { 0.75 } else { 0.0 },
                    v as f64 / degree as f64,
                ]
            });
            // The cross term makes these non-separable rational weights.
            let weights: Vec<_> = (0..3)
                .flat_map(|u| (0..=degree).map(move |v| 1.0 + 0.01 * (u + v + u * v) as f64))
                .collect();
            let q = qualify_patch(&poles, &weights, degree, AXIS, ACROSS).unwrap();
            assert!(q.min_determinant.is_finite() && q.min_determinant > 0.0);
            assert!((1..=MAX_CELLS).contains(&q.cells));
        }
    }

    #[test]
    fn tilted_rotated_frame_and_large_translation() {
        // Exact orthogonal vectors with lengths 5 and 25, neither world-aligned.
        let across = [3.0, 4.0, 0.0];
        let axis = [-12.0, 9.0, 20.0];
        let normal = [16.0, -12.0, 15.0];
        let origin = [1e12, -2e12, 3e12];
        let poles = net(2, |u, v| {
            std::array::from_fn(|k| {
                origin[k]
                    + across[k] * u as f64
                    + axis[k] * v as f64
                    + normal[k] * if u == 1 { 0.25 } else { 0.0 }
            })
        });
        let q = qualify_patch(&poles, &[1.0; 9], 2, axis, across).unwrap();
        assert!(q.min_determinant > 0.0);
        // Using hardcoded world axes would also miss the reversed frame.
        let reversed = across.map(|x| -x);
        assert!(qualify_patch(&poles, &[1.0; 9], 2, axis, reversed).is_err());
    }

    #[test]
    fn subdivision_certifies_positive_derivative_with_negative_control_coefficient() {
        // Z'(v) = (v - 1/2)^2 + 0.01: strictly positive, but its
        // Bernstein derivative net [0.26, -0.24, 0.26] straddles zero.
        let z = [0.0, 0.26 / 3.0, 0.02 / 3.0, 0.01 + 1.0 / 12.0];
        let poles = net(3, |u, v| [u as f64 / 2.0, 0.0, z[v]]);
        let q = qualify(&poles, 3).unwrap();
        assert!(q.cells > 1);
        // At v=1/2 the determinant is 0.01 globally and at most 0.0025
        // in a subdivided leaf's local coordinates (both axes are halved).
        assert!(q.min_determinant > 0.0 && q.min_determinant <= 0.0025 + 1e-14);
        assert_eq!(
            certify(polynomial_cell(&poles, 3), 0, MAX_CELLS).unwrap_err(),
            DEPTH_LIMIT
        );
        assert_eq!(
            certify(polynomial_cell(&poles, 3), MAX_DEPTH, 1).unwrap_err(),
            CELL_LIMIT
        );
    }

    #[test]
    fn positive_diagonals_do_not_hide_negative_or_singular_determinant() {
        for shear in [1.0, 2.0] {
            // Xu = Zv = 1, determinant = 1 - shear^2.
            let poles = net(2, |u, v| {
                let u = u as f64 / 2.0;
                let v = v as f64 / 2.0;
                [u + shear * v, 0.0, shear * u + v]
            });
            assert!(qualify(&poles, 2).is_err());
        }
    }

    #[test]
    fn positive_determinant_does_not_hide_negative_diagonals() {
        let poles = net(2, |u, v| [-(u as f64), 0.0, -(v as f64)]);
        assert_eq!(qualify(&poles, 2).unwrap_err(), NONPOSITIVE);
    }

    #[test]
    fn interior_fold_and_singularity_are_not_hidden_by_regular_corners() {
        for (center, dip) in [(0.5, 0.01), (0.5, 0.0), (0.371, 1e-8)] {
            // Z'(v) = (v-center)^2 - dip. Corners have positive
            // derivatives; the interior is singular or genuinely reversed.
            let a = center * center - dip;
            let z = [
                0.0,
                a / 3.0,
                (2.0 * a - center) / 3.0,
                a - center + 1.0 / 3.0,
            ];
            let poles = net(3, |u, v| [u as f64 / 2.0, 0.0, z[v]]);
            assert!(a > 0.0 && (1.0 - center) * (1.0 - center) - dip > 0.0);
            assert!(qualify(&poles, 3).is_err(), "center={center}, dip={dip}");
        }
    }

    #[test]
    fn rational_weight_derivatives_cannot_be_ignored() {
        // In U, Nx has increasing controls [0, 2, 10], but X has poles
        // [0, 2, 1] and weights [1, 1, 10]. At u=1, Xu=-1/5 while
        // Nxu=16: ignoring the quotient's W derivative hides the reversal.
        // Tensor weights vary in V too, with a regular increasing Z map.
        let poles = net(2, |u, v| [[0.0, 2.0, 1.0][u], 0.0, v as f64 / 2.0]);
        let weights: Vec<_> = (0..3)
            .flat_map(|u| (0..3).map(move |v| [1.0, 1.0, 10.0][u] * (v + 1) as f64))
            .collect();
        assert!(qualify_patch(&poles, &weights, 2, AXIS, ACROSS).is_err());
    }

    #[test]
    fn invalid_inputs_and_uncertifiable_extremes_are_refused() {
        let poles = net(2, |u, v| [u as f64, 0.0, v as f64]);
        for invalid in [0.0, -0.0, -1.0, f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            let mut weights = [1.0; 9];
            weights[4] = invalid;
            assert!(qualify_patch(&poles, &weights, 2, AXIS, ACROSS).is_err());
        }
        for degree in [0, 1, 13, usize::MAX] {
            assert!(qualify_patch(&poles, &[1.0; 9], degree, AXIS, ACROSS).is_err());
        }
        assert!(qualify_patch(&[], &[], 2, AXIS, ACROSS).is_err());
        assert!(qualify_patch(&poles[..8], &[1.0; 9], 2, AXIS, ACROSS).is_err());
        assert!(qualify_patch(&poles, &[1.0; 8], 2, AXIS, ACROSS).is_err());
        let mut bad = poles.clone();
        bad[4][1] = f64::NAN;
        assert!(qualify(&bad, 2).is_err());
        assert!(qualify_patch(&poles, &[1.0; 9], 2, [0.0; 3], ACROSS).is_err());
        assert!(qualify_patch(&poles, &[1.0; 9], 2, ACROSS, ACROSS).is_err());
        assert!(qualify_patch(&poles, &[1.0; 9], 2, [f64::INFINITY; 3], ACROSS).is_err());
        assert!(qualify_patch(&poles, &[f64::MAX; 9], 2, AXIS, ACROSS).is_err());
        assert!(qualify_patch(&poles, &[f64::from_bits(1); 9], 2, AXIS, ACROSS).is_err());
    }
}
