//! RUST-CALC-12: complete native formula ownership, including rejection paths.
//!
//! MEASURED grammar/error precedence is frozen in spike/calc_reference.py.
//! Tokens and error ranges are Python code-point offsets, never UTF-8 offsets.
//! OURS safety policy: <=4096 code points per input, <=64 nested primary/call
//! expressions, finite public results. Iterative operator/sign/argument loops
//! bound stack use independently of token count. No AST, foreign callbacks,
//! dependencies, or Python error replay. Intermediate IEEE infinities/NaNs are
//! retained to preserve Python math-function error precedence.
use crate::numparse_owned::{normalized, owned_literal, LIMIT};
use std::f64::consts::PI;

// ABI 2 error identities. See native_calc.py for host-only presentation.
pub const SPACE: u32 = 1;
pub const BAD_AT: u32 = 2;
pub const BAD_NUMBER: u32 = 3;
pub const UNIT: u32 = 4;
pub const CHARACTER: u32 = 5;
pub const EMPTY: u32 = 6;
pub const OPERAND: u32 = 7;
pub const NAME: u32 = 8;
pub const TOKEN: u32 = 9;
pub const NUMBER_TOKEN: u32 = 10;
pub const PARENTHESES: u32 = 11;
pub const ARGUMENT: u32 = 12;
pub const MISSING: u32 = 13;
pub const ARITY: u32 = 14;
pub const UNBALANCED: u32 = 15;
pub const DIVISION: u32 = 16;
pub const FINITE: u32 = 17;
pub const DOMAIN: u32 = 18;
pub const MATH_RANGE: u32 = 19;
pub const COMPLEX: u32 = 20;
pub const COMPLEX_RANGE: u32 = 21;
pub const ZERO_POWER: u32 = 22;
pub const POWER_RANGE: u32 = 23;
pub const MODEL_TYPE: u32 = 24;
pub const MODEL: u32 = 25;
pub const LENGTH: u32 = 26;
pub const LITERAL_RANGE: u32 = 27;
pub const BOUND: u32 = 28;
pub const DEPTH: u32 = 29;

/// Caller-owned fixed 32-byte reply. `value` is only nonzero on success or
/// NUMBER_TOKEN (the already-evaluated numeric trailing token for Python repr).
/// `aux` packs (expected arity << 16) | received arity for ARITY, zero
/// otherwise. Reserved stays zero.
#[repr(C)]
#[derive(Debug, Default, Clone, Copy)]
pub struct Reply {
    pub code: u32,
    pub aux: u32,
    pub value: f64,
    pub start: u32,
    pub end: u32,
    pub reserved: u64,
}
fn err(code: u32, start: usize, end: usize) -> Reply {
    Reply {
        code,
        start: start as u32,
        end: end as u32,
        ..Reply::default()
    }
}
type ResultV = Result<f64, Reply>;
#[derive(Clone, Copy, Debug)]
enum Kind {
    Num(f64),
    Name,
    Op(u32),
}
#[derive(Clone, Copy, Debug)]
struct Token {
    kind: Kind,
    start: usize,
    end: usize,
}
impl Token {
    fn error(self, code: u32) -> Reply {
        err(code, self.start, self.end)
    }
    fn op(self, cp: u32) -> bool {
        matches!(self.kind, Kind::Op(c) if c == cp)
    }
}
fn alpha(cp: u32) -> bool {
    char::from_u32(cp).is_some_and(crate::python_text::is_alpha)
}
fn in_ranges(cp: u32, table: &[(u32, u32)]) -> bool {
    table
        .binary_search_by(|&(lo, hi)| {
            if hi < cp {
                std::cmp::Ordering::Less
            } else if lo > cp {
                std::cmp::Ordering::Greater
            } else {
                std::cmp::Ordering::Equal
            }
        })
        .is_ok()
}
fn digit(cp: u32) -> bool {
    in_ranges(cp, DIGITS)
}
fn alnum(cp: u32) -> bool {
    alpha(cp) || in_ranges(cp, NUMERIC)
}
fn text(cps: &[u32]) -> String {
    cps.iter().map(|&c| char::from_u32(c).unwrap()).collect()
}
fn run_digits(s: &[u8], mut i: usize) -> usize {
    while i < s.len() && s[i].is_ascii_digit() {
        i += 1;
    }
    i
}
fn scan(s: &[u8], i: usize) -> Option<usize> {
    let first = *s.get(i)?;
    let mut j;
    if first.is_ascii_digit() {
        j = run_digits(s, i);
        if s.get(j) == Some(&b'-') {
            let k = run_digits(s, j + 1);
            if k > j + 1 && s.get(k) == Some(&b'/') {
                let e = run_digits(s, k + 1);
                if e > k + 1 {
                    return Some(e);
                }
            }
        }
        if s.get(j) == Some(&b'/') {
            let k = run_digits(s, j + 1);
            if k > j + 1 {
                return Some(k);
            }
        }
        if s.get(j) == Some(&b'.') {
            let k = run_digits(s, j + 1);
            if k > j + 1 {
                j = k;
            }
        }
    } else if first == b'.' {
        j = run_digits(s, i + 1);
        if j == i + 1 {
            return None;
        }
    } else {
        return None;
    }
    if matches!(s.get(j), Some(b'e' | b'E')) {
        let mut k = j + 1;
        if matches!(s.get(k), Some(b'+' | b'-')) {
            k += 1;
        }
        let e = run_digits(s, k);
        if e > k {
            j = e;
        }
    }
    Some(j)
}
fn angle(s: &str) -> Option<f64> {
    Some(match s {
        "d" | "degree" | "degrees" => PI / 180.0,
        "radian" | "radians" => 1.0,
        "grade" | "gradians" => PI / 200.0,
        _ => return None,
    })
}
fn tokenize(cps: &[u32], model: &[u32], model_string: bool) -> Result<Vec<Token>, Reply> {
    let norm = normalized(cps, 151);
    let bytes = norm.as_bytes(); // normalization is deliberately one ASCII byte per code point
    let mut tokens = Vec::with_capacity(cps.len());
    let mut i = 0;
    while i < cps.len() {
        let cp = cps[i];
        if matches!(cp, 32 | 9 | 10 | 13) {
            return Err(err(SPACE, i, i + 1));
        }
        let start = i;
        let kind;
        if matches!(cp, 43 | 45 | 42 | 47 | 40 | 41 | 44) {
            kind = Kind::Op(cp);
            i += 1;
        } else if digit(cp) || matches!(cp, 46 | 39 | 34) {
            let Some(j) = scan(bytes, i) else {
                return Err(err(BAD_AT, i, cps.len()));
            };
            // The compound pattern is left-anchored only (even before letters).
            let compound = if bytes.get(j) == Some(&b'\'') {
                scan(bytes, j + 1)
                    .filter(|&end| bytes.get(end) == Some(&b'"'))
                    .map(|e| e + 1)
            } else {
                None
            };
            if let Some(end) = compound {
                if !model_string {
                    return Err(err(MODEL_TYPE, i, end));
                }
                let r = owned_literal(&cps[i..end], model, true, 151);
                if r.code != 0 {
                    let code = match r.code {
                        -5 => MODEL,
                        -8 => LITERAL_RANGE,
                        _ => LENGTH,
                    };
                    return Err(err(code, i + r.start as usize, i + r.end as usize));
                }
                kind = Kind::Num(r.value);
                i = end;
            } else {
                let r = owned_literal(&cps[i..j], &[], false, 151);
                if r.code != 0 {
                    return Err(err(BAD_NUMBER, i, j));
                }
                let mut end = j;
                while end < cps.len() && (alpha(cps[end]) || matches!(cps[end], 39 | 34)) {
                    end += 1;
                }
                let mut v = r.value;
                if end > j {
                    let suffix = text(&cps[j..end]).to_lowercase();
                    if let Some(f) = angle(&suffix) {
                        v *= f;
                    } else {
                        if !model_string {
                            return Err(err(UNIT, j, end));
                        }
                        let r = owned_literal(&cps[i..end], model, true, 151);
                        if r.code != 0 {
                            return Err(err(UNIT, j, end));
                        }
                        v = r.value;
                    }
                }
                kind = Kind::Num(v);
                i = end;
            }
        } else if alpha(cp) || cp == 95 {
            i += 1;
            while i < cps.len() && (alnum(cps[i]) || cps[i] == 95) {
                i += 1;
            }
            kind = Kind::Name;
        } else {
            return Err(err(CHARACTER, i, i + 1));
        }
        tokens.push(Token {
            kind,
            start,
            end: i,
        });
    }
    Ok(tokens)
}
fn arity(name: &str) -> Option<usize> {
    Some(match name {
        "atan2" | "pow" => 2,
        "sin" | "cos" | "tan" | "asin" | "acos" | "atan" | "ln" | "log10" | "exp" | "sinh"
        | "cosh" | "tanh" | "sqrt" => 1,
        _ => return None,
    })
}
fn apply(name: &str, a: f64, b: f64, token: Token) -> ResultV {
    let fail = |code| Err(token.error(code));
    let v = match name {
        "sin" | "cos" | "tan" if a.is_infinite() => return fail(DOMAIN),
        "sin" => a.sin(),
        "cos" => a.cos(),
        "tan" => a.tan(),
        "asin" | "acos" if a.abs() > 1.0 => return fail(DOMAIN),
        "asin" => a.asin(),
        "acos" => a.acos(),
        "atan" => a.atan(),
        "atan2" => a.atan2(b),
        "ln" | "log10" if a <= 0.0 => return fail(DOMAIN),
        "ln" => a.ln(),
        "log10" => a.log10(),
        "sqrt" if a < 0.0 => return fail(DOMAIN),
        "sqrt" => a.sqrt(),
        "exp" | "sinh" | "cosh" => {
            let r = match name {
                "exp" => a.exp(),
                "sinh" => a.sinh(),
                _ => a.cosh(),
            };
            if r.is_infinite() && a.is_finite() {
                return fail(MATH_RANGE);
            }
            r
        }
        "tanh" => a.tanh(),
        "pow" => {
            // CPython float_pow order matters: zero/one before NaN; infinite
            // exponent/base before the negative finite base complex branch.
            if b == 0.0 || a == 1.0 {
                1.0
            } else if a.is_nan() || b.is_nan() {
                f64::NAN
            } else if b.is_infinite() {
                if a.abs() == 1.0 {
                    1.0
                } else if (a.abs() > 1.0) == b.is_sign_positive() {
                    f64::INFINITY
                } else {
                    0.0
                }
            } else if a.is_infinite() {
                let r = if b > 0.0 { f64::INFINITY } else { 0.0 };
                if a.is_sign_negative() && b % 2.0 != 0.0 && b.fract() == 0.0 {
                    -r
                } else {
                    r
                }
            } else if a == 0.0 {
                if b < 0.0 {
                    return fail(ZERO_POWER);
                }
                if a.is_sign_negative() && b % 2.0 != 0.0 && b.fract() == 0.0 {
                    -0.0
                } else {
                    0.0
                }
            } else if a < 0.0 && b.fract() != 0.0 {
                if (-a).powf(b).is_infinite() {
                    return fail(COMPLEX_RANGE);
                }
                return fail(COMPLEX);
            } else {
                let r = a.powf(b);
                if r.is_infinite() {
                    return fail(POWER_RANGE);
                }
                r
            }
        }
        _ => unreachable!(),
    };
    Ok(v)
}
struct Parser<'a> {
    tokens: &'a [Token],
    cps: &'a [u32],
    pos: usize,
}
impl Parser<'_> {
    fn op(&self, c: u8) -> bool {
        self.tokens.get(self.pos).is_some_and(|t| t.op(c as u32))
    }
    fn expr(&mut self, depth: usize) -> ResultV {
        let mut v = self.term(depth)?;
        while self.op(b'+') || self.op(b'-') {
            let add = self.op(b'+');
            self.pos += 1;
            let r = self.term(depth)?;
            v = if add { v + r } else { v - r };
        }
        Ok(v)
    }
    fn term(&mut self, depth: usize) -> ResultV {
        let mut v = self.factor(depth)?;
        while self.op(b'*') || self.op(b'/') {
            let mul = self.op(b'*');
            self.pos += 1;
            let r = self.factor(depth)?;
            if !mul && r == 0.0 {
                return Err(err(DIVISION, 0, 0));
            }
            v = if mul { v * r } else { v / r };
        }
        Ok(v)
    }
    fn factor(&mut self, depth: usize) -> ResultV {
        let mut sign = 1.0;
        while self.op(b'+') || self.op(b'-') {
            if self.op(b'-') {
                sign = -sign;
            }
            self.pos += 1;
        }
        Ok(sign * self.primary(depth)?)
    }
    fn primary(&mut self, depth: usize) -> ResultV {
        let Some(&t) = self.tokens.get(self.pos) else {
            return Err(err(OPERAND, 0, 0));
        };
        self.pos += 1;
        match t.kind {
            Kind::Num(v) => Ok(v),
            Kind::Name => {
                let name = text(&self.cps[t.start..t.end]);
                if name == "pi" || name == "π" {
                    return Ok(PI);
                }
                let Some(expected) = arity(&name) else {
                    return Err(t.error(NAME));
                };
                if !self.op(b'(') {
                    return Err(t.error(PARENTHESES));
                }
                self.pos += 1;
                if self.op(b')') {
                    return Err(t.error(ARGUMENT));
                }
                if depth >= 64 {
                    return Err(t.error(DEPTH));
                }
                let a = self.expr(depth + 1)?;
                let mut b = 0.0;
                let mut count = 1;
                while self.op(b',') {
                    self.pos += 1;
                    let v = self.expr(depth + 1)?;
                    if count == 1 {
                        b = v;
                    }
                    count += 1;
                }
                if !self.op(b')') {
                    return Err(t.error(MISSING));
                }
                self.pos += 1;
                if count != expected {
                    let mut e = t.error(ARITY);
                    e.aux = ((expected as u32) << 16) | count as u32;
                    return Err(e);
                }
                apply(&name, a, b, t)
            }
            Kind::Op(40) => {
                if depth >= 64 {
                    return Err(t.error(DEPTH));
                }
                let v = self.expr(depth + 1)?;
                if !self.op(b')') {
                    return Err(t.error(UNBALANCED));
                }
                self.pos += 1;
                Ok(v)
            }
            Kind::Op(_) => Err(t.error(TOKEN)),
        }
    }
}
fn evaluate(cps: &[u32], model: &[u32], model_string: bool) -> Reply {
    let tokens = match tokenize(cps, model, model_string) {
        Ok(t) => t,
        Err(e) => return e,
    };
    if tokens.is_empty() {
        return err(EMPTY, 0, 0);
    }
    let mut parser = Parser {
        tokens: &tokens,
        cps,
        pos: 0,
    };
    let value = match parser.expr(0) {
        Ok(v) => v,
        Err(e) => return e,
    };
    if let Some(t) = tokens.get(parser.pos) {
        let mut e = t.error(TOKEN);
        if let Kind::Num(v) = t.kind {
            e.code = NUMBER_TOKEN;
            e.value = v;
        }
        return e;
    }
    if !value.is_finite() {
        return err(FINITE, 0, 0);
    }
    Reply {
        value,
        ..Reply::default()
    }
}
#[no_mangle]
pub extern "C" fn narwhal_pure_core_calc_abi2() -> u32 {
    2
}
/// Transport result: 0 valid reply (including grammar errors), 1 bad spans or
/// output size, 2 unsupported model_kind/Unicode flags. Bound errors are replies.
///
/// # Safety
/// Nonempty spans must be readable u32 arrays; output must be aligned, writable,
/// exclusive, and not overlap either input. No pointer is retained by this call.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_calc_v2(
    input: *const u32,
    input_len: usize,
    model: *const u32,
    model_len: usize,
    model_kind: u32,
    unicode: u32,
    out: *mut Reply,
    out_size: usize,
) -> u32 {
    if out_size != std::mem::size_of::<Reply>() {
        return 1;
    }
    let Some(output) = (unsafe { crate::checked_slice_mut(out, 1) }) else {
        return 1;
    };
    output[0] = Reply::default();
    if model_kind > 1 || unicode != 151 {
        return 2;
    }
    if input_len > LIMIT || model_len > LIMIT {
        output[0] = err(BOUND, 0, 0);
        return 0;
    }
    let (Some(input), Some(model)) = (unsafe { crate::checked_slice(input, input_len) }, unsafe {
        crate::checked_slice(model, model_len)
    }) else {
        return 1;
    };
    if model_kind == 0 && model_len != 0 {
        return 2;
    }
    output[0] = evaluate(input, model, model_kind == 1);
    0
}

// MEASURED CPython 3.13 Unicode 15.1 digit/numeric predicates.
const DIGITS: &[(u32, u32)] = &[
    (0x30, 0x39),
    (0xb2, 0xb3),
    (0xb9, 0xb9),
    (0x660, 0x669),
    (0x6f0, 0x6f9),
    (0x7c0, 0x7c9),
    (0x966, 0x96f),
    (0x9e6, 0x9ef),
    (0xa66, 0xa6f),
    (0xae6, 0xaef),
    (0xb66, 0xb6f),
    (0xbe6, 0xbef),
    (0xc66, 0xc6f),
    (0xce6, 0xcef),
    (0xd66, 0xd6f),
    (0xde6, 0xdef),
    (0xe50, 0xe59),
    (0xed0, 0xed9),
    (0xf20, 0xf29),
    (0x1040, 0x1049),
    (0x1090, 0x1099),
    (0x1369, 0x1371),
    (0x17e0, 0x17e9),
    (0x1810, 0x1819),
    (0x1946, 0x194f),
    (0x19d0, 0x19da),
    (0x1a80, 0x1a89),
    (0x1a90, 0x1a99),
    (0x1b50, 0x1b59),
    (0x1bb0, 0x1bb9),
    (0x1c40, 0x1c49),
    (0x1c50, 0x1c59),
    (0x2070, 0x2070),
    (0x2074, 0x2079),
    (0x2080, 0x2089),
    (0x2460, 0x2468),
    (0x2474, 0x247c),
    (0x2488, 0x2490),
    (0x24ea, 0x24ea),
    (0x24f5, 0x24fd),
    (0x24ff, 0x24ff),
    (0x2776, 0x277e),
    (0x2780, 0x2788),
    (0x278a, 0x2792),
    (0xa620, 0xa629),
    (0xa8d0, 0xa8d9),
    (0xa900, 0xa909),
    (0xa9d0, 0xa9d9),
    (0xa9f0, 0xa9f9),
    (0xaa50, 0xaa59),
    (0xabf0, 0xabf9),
    (0xff10, 0xff19),
    (0x104a0, 0x104a9),
    (0x10a40, 0x10a43),
    (0x10d30, 0x10d39),
    (0x10e60, 0x10e68),
    (0x11052, 0x1105a),
    (0x11066, 0x1106f),
    (0x110f0, 0x110f9),
    (0x11136, 0x1113f),
    (0x111d0, 0x111d9),
    (0x112f0, 0x112f9),
    (0x11450, 0x11459),
    (0x114d0, 0x114d9),
    (0x11650, 0x11659),
    (0x116c0, 0x116c9),
    (0x11730, 0x11739),
    (0x118e0, 0x118e9),
    (0x11950, 0x11959),
    (0x11c50, 0x11c59),
    (0x11d50, 0x11d59),
    (0x11da0, 0x11da9),
    (0x11f50, 0x11f59),
    (0x16a60, 0x16a69),
    (0x16ac0, 0x16ac9),
    (0x16b50, 0x16b59),
    (0x1d7ce, 0x1d7ff),
    (0x1e140, 0x1e149),
    (0x1e2f0, 0x1e2f9),
    (0x1e4f0, 0x1e4f9),
    (0x1e950, 0x1e959),
    (0x1f100, 0x1f10a),
    (0x1fbf0, 0x1fbf9),
];
const NUMERIC: &[(u32, u32)] = &[
    (0x30, 0x39),
    (0xb2, 0xb3),
    (0xb9, 0xb9),
    (0xbc, 0xbe),
    (0x660, 0x669),
    (0x6f0, 0x6f9),
    (0x7c0, 0x7c9),
    (0x966, 0x96f),
    (0x9e6, 0x9ef),
    (0x9f4, 0x9f9),
    (0xa66, 0xa6f),
    (0xae6, 0xaef),
    (0xb66, 0xb6f),
    (0xb72, 0xb77),
    (0xbe6, 0xbf2),
    (0xc66, 0xc6f),
    (0xc78, 0xc7e),
    (0xce6, 0xcef),
    (0xd58, 0xd5e),
    (0xd66, 0xd78),
    (0xde6, 0xdef),
    (0xe50, 0xe59),
    (0xed0, 0xed9),
    (0xf20, 0xf33),
    (0x1040, 0x1049),
    (0x1090, 0x1099),
    (0x1369, 0x137c),
    (0x16ee, 0x16f0),
    (0x17e0, 0x17e9),
    (0x17f0, 0x17f9),
    (0x1810, 0x1819),
    (0x1946, 0x194f),
    (0x19d0, 0x19da),
    (0x1a80, 0x1a89),
    (0x1a90, 0x1a99),
    (0x1b50, 0x1b59),
    (0x1bb0, 0x1bb9),
    (0x1c40, 0x1c49),
    (0x1c50, 0x1c59),
    (0x2070, 0x2070),
    (0x2074, 0x2079),
    (0x2080, 0x2089),
    (0x2150, 0x2182),
    (0x2185, 0x2189),
    (0x2460, 0x249b),
    (0x24ea, 0x24ff),
    (0x2776, 0x2793),
    (0x2cfd, 0x2cfd),
    (0x3007, 0x3007),
    (0x3021, 0x3029),
    (0x3038, 0x303a),
    (0x3192, 0x3195),
    (0x3220, 0x3229),
    (0x3248, 0x324f),
    (0x3251, 0x325f),
    (0x3280, 0x3289),
    (0x32b1, 0x32bf),
    (0xa620, 0xa629),
    (0xa6e6, 0xa6ef),
    (0xa830, 0xa835),
    (0xa8d0, 0xa8d9),
    (0xa900, 0xa909),
    (0xa9d0, 0xa9d9),
    (0xa9f0, 0xa9f9),
    (0xaa50, 0xaa59),
    (0xabf0, 0xabf9),
    (0xff10, 0xff19),
    (0x10107, 0x10133),
    (0x10140, 0x10178),
    (0x1018a, 0x1018b),
    (0x102e1, 0x102fb),
    (0x10320, 0x10323),
    (0x10341, 0x10341),
    (0x1034a, 0x1034a),
    (0x103d1, 0x103d5),
    (0x104a0, 0x104a9),
    (0x10858, 0x1085f),
    (0x10879, 0x1087f),
    (0x108a7, 0x108af),
    (0x108fb, 0x108ff),
    (0x10916, 0x1091b),
    (0x109bc, 0x109bd),
    (0x109c0, 0x109cf),
    (0x109d2, 0x109ff),
    (0x10a40, 0x10a48),
    (0x10a7d, 0x10a7e),
    (0x10a9d, 0x10a9f),
    (0x10aeb, 0x10aef),
    (0x10b58, 0x10b5f),
    (0x10b78, 0x10b7f),
    (0x10ba9, 0x10baf),
    (0x10cfa, 0x10cff),
    (0x10d30, 0x10d39),
    (0x10e60, 0x10e7e),
    (0x10f1d, 0x10f26),
    (0x10f51, 0x10f54),
    (0x10fc5, 0x10fcb),
    (0x11052, 0x1106f),
    (0x110f0, 0x110f9),
    (0x11136, 0x1113f),
    (0x111d0, 0x111d9),
    (0x111e1, 0x111f4),
    (0x112f0, 0x112f9),
    (0x11450, 0x11459),
    (0x114d0, 0x114d9),
    (0x11650, 0x11659),
    (0x116c0, 0x116c9),
    (0x11730, 0x1173b),
    (0x118e0, 0x118f2),
    (0x11950, 0x11959),
    (0x11c50, 0x11c6c),
    (0x11d50, 0x11d59),
    (0x11da0, 0x11da9),
    (0x11f50, 0x11f59),
    (0x11fc0, 0x11fd4),
    (0x12400, 0x1246e),
    (0x16a60, 0x16a69),
    (0x16ac0, 0x16ac9),
    (0x16b50, 0x16b59),
    (0x16b5b, 0x16b61),
    (0x16e80, 0x16e96),
    (0x1d2c0, 0x1d2d3),
    (0x1d2e0, 0x1d2f3),
    (0x1d360, 0x1d378),
    (0x1d7ce, 0x1d7ff),
    (0x1e140, 0x1e149),
    (0x1e2f0, 0x1e2f9),
    (0x1e4f0, 0x1e4f9),
    (0x1e8c7, 0x1e8cf),
    (0x1e950, 0x1e959),
    (0x1ec71, 0x1ecab),
    (0x1ecad, 0x1ecaf),
    (0x1ecb1, 0x1ecb4),
    (0x1ed01, 0x1ed2d),
    (0x1ed2f, 0x1ed3d),
    (0x1f100, 0x1f10c),
    (0x1fbf0, 0x1fbf9),
];

#[cfg(test)]
mod tests {
    use super::*;
    fn run(s: &str) -> Reply {
        evaluate(
            &s.chars().map(|c| c as u32).collect::<Vec<_>>(),
            &"Millimeters".chars().map(|c| c as u32).collect::<Vec<_>>(),
            true,
        )
    }
    #[test]
    fn grammar_and_exact_literal_core() {
        for (s, want) in [
            ("1+2*3", 7.0),
            ("(1+2)*3", 9.0),
            ("1-3/4", 1.75),
            ("9007199254740993/9007199254740992", 1.0),
            ("sin(30d)", 0.49999999999999994),
            ("pow(2,10)", 1024.0),
            ("١٢+３", 15.0),
            ("1'2-3/4\"", 374.65),
        ] {
            let r = run(s);
            assert_eq!(r.code, 0, "{s}: {r:?}");
            assert_eq!(r.value, want, "{s}");
        }
        assert_eq!(run("-0").value.to_bits(), (-0.0f64).to_bits());
        let r = run("pi(2)");
        assert_eq!((r.code, r.start, r.end), (TOKEN, 2, 3));
    }
    #[test]
    fn classified_rejections_and_precedence() {
        for (s, code) in [
            ("", EMPTY),
            ("1/0", BAD_NUMBER),
            ("1/(0)", DIVISION),
            ("1.", BAD_AT),
            ("1.2.3", NUMBER_TOKEN),
            ("12xyz", UNIT),
            ("abc", NAME),
            ("sin", PARENTHESES),
            ("sin()", ARGUMENT),
            ("sin(1,2)", ARITY),
            ("sin(1", MISSING),
            ("(1", UNBALANCED),
            ("sin(1/0)@", BAD_NUMBER),
            ("sqrt(-1)@", CHARACTER),
            ("sqrt(-1)", DOMAIN),
            ("exp(999)", MATH_RANGE),
            ("sinh(999)", MATH_RANGE),
            ("cosh(999)", MATH_RANGE),
            ("pow(-1,.5)", COMPLEX),
            ("pow(-1e308,1.5)", COMPLEX_RANGE),
            ("pow(0,-1)", ZERO_POWER),
            ("pow(1e308,2)", POWER_RANGE),
            ("sin(1e308*2)", DOMAIN),
            ("ln(1e308*2)", FINITE),
            ("1+*2", TOKEN),
            ("1+", OPERAND),
            ("1 @", SPACE),
        ] {
            assert_eq!(run(s).code, code, "{s}");
        }
        let r = run("sin(1,2)");
        assert_eq!(r.aux, (1 << 16) | 2);
        let r = run("１２foo");
        assert_eq!((r.start, r.end), (2, 5));
        let r = run("1.2.3");
        assert_eq!(r.value, 0.3);
    }
    #[test]
    fn intermediate_specials_and_depth() {
        assert_eq!(run("atan(1e308*2)").value, PI / 2.0);
        assert_eq!(run("pow(1e308*2,0)").value, 1.0);
        assert_eq!(run("pow(1,(1e308*2)-(1e308*2))").value, 1.0);
        assert_eq!(
            run(&format!("{}1{}", "(".repeat(64), ")".repeat(64))).code,
            0
        );
        assert_eq!(
            run(&format!("{}1{}", "(".repeat(65), ")".repeat(65))).code,
            DEPTH
        );
        assert_eq!(
            run(&format!("{}0{}", "sin(".repeat(65), ")".repeat(65))).code,
            DEPTH
        );
        assert_eq!(run(&format!("{}1", "-".repeat(4095))).code, 0);
    }
    #[test]
    fn abi_admission_and_ranges() {
        assert_eq!(std::mem::size_of::<Reply>(), 32);
        let mut r = Reply {
            code: 99,
            ..Reply::default()
        };
        let null = std::ptr::null();
        let call = |p, n, kind, unicode, out| unsafe {
            narwhal_pure_core_calc_v2(p, n, null, 0, kind, unicode, out, 32)
        };
        assert_eq!(call(null, 1, 1, 151, &mut r), 1);
        assert_eq!(r.code, 0);
        assert_eq!(call(null, 4097, 1, 151, &mut r), 0);
        assert_eq!(r.code, BOUND);
        assert_eq!(call(null, usize::MAX, 1, 151, &mut r), 0);
        assert_eq!(r.code, BOUND);
        assert_eq!(call(null, 0, 2, 151, &mut r), 2);
        assert_eq!(call(null, 0, 1, 160, &mut r), 2);
        assert_eq!(call(null, 0, 1, 151, std::ptr::null_mut()), 1);
        let bad = std::ptr::NonNull::<u32>::dangling()
            .as_ptr()
            .cast::<u8>()
            .wrapping_add(1)
            .cast::<u32>();
        assert_eq!(call(bad, 1, 1, 151, &mut r), 1);
        let input = [0xd800];
        assert_eq!(call(input.as_ptr(), 1, 1, 151, &mut r), 0);
        assert_eq!((r.code, r.start, r.end), (CHARACTER, 0, 1));
        let model = [0xd800];
        let input = [49, 39, 50, 34];
        assert_eq!(
            unsafe {
                narwhal_pure_core_calc_v2(input.as_ptr(), 4, model.as_ptr(), 1, 1, 151, &mut r, 32)
            },
            0
        );
        assert_eq!(r.code, MODEL);
    }
}
