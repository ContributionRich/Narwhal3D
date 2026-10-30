//! Port of `narwhal3d/aci_colors.py`: AutoCAD Color Index (ACI) <-> RGB for DXF import/export.
//!
//! Core reviewed by Main; nearest-color acceleration is exposed through metadata_ffi.
//! Standalone parity evidence remains in `spike/rust_ports_overnight/`.
//!
//! Parity notes (measured against CPython 3.13 with the unchanged reference):
//! * only the nine standard colours exist. ACI 10-255 is refused as `NotImplemented` with the
//!   reference's text; 0 (ByBlock) and 256 (ByLayer) are refused by name. No palette is invented;
//! * `aci_to_rgb` refuses every non-`int`, `bool` included, and names the type in its message;
//! * `rgb_to_aci` raises only `ValueError` (`RgbError`). Checks run in the reference's order:
//!   container, then arity, then each component in turn (type, then range);
//! * the distance is a float, `(r - tr) ** 2 + (g - tg) ** 2 + (b - tb) ** 2`. CPython computes
//!   `** 2` with the C library `pow`, which is NOT always equal to `x * x` (MEASURED: about
//!   0.085% of random differences differ in the last bit). `sq_diff` calls the same libm `pow`
//!   through `powf` with an opaque exponent, so LLVM cannot rewrite it as a multiply. The
//!   `aci_colors_pow` fixture checks it bit for bit;
//! * an exact match is checked before any distance; equal distances keep the first index
//!   (`d < best`);
//! * the out-of-range message prints a float the way Python's `str()` does (`py_repr_float`).
//!
//! Integration seam (not wired): the Python layer maps each object to a `Value` (an `int` within
//! i128 becomes `Int`, a larger one `BigInt` with its decimal text, `bool`/`float` keep their
//! kinds, anything else `Other` with its `type(x).__name__`), and passes `tuple` and `list` as
//! `Rgb::Items`. Subclasses of `int`, `tuple` or `list` are the caller's decision.

/// A Python value, reduced to what the reference's checks can tell apart.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Value<'a> {
    /// `int` inside i128.
    Int(i128),
    /// `int` outside i128, as its canonical decimal text (`str(n)`). Always out of range.
    BigInt(&'a str),
    /// `float`, NaN and infinities included.
    Float(f64),
    /// `bool`: never accepted where a number is required.
    Bool(bool),
    /// Any other object, named by `type(x).__name__` (code points).
    Other(&'a [u32]),
}

/// The argument of `rgb_to_aci`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Rgb<'a> {
    /// A `tuple` or a `list` (the reference accepts both).
    Items(&'a [Value<'a>]),
    /// Any other object, named by `type(x).__name__` (code points).
    Other(&'a [u32]),
}

/// A refusal from `aci_to_rgb`. The reference returns `(None, message)`; the port returns `Err`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AciError {
    /// Not an `int`: `ACI index must be an integer, got <type>`.
    NotInteger(Vec<u32>),
    /// `ACI 0 is ByBlock, not a concrete colour`.
    ByBlock,
    /// `ACI 256 is ByLayer, not a concrete colour`.
    ByLayer,
    /// `ACI 10-255 not implemented yet`.
    NotImplemented,
    /// `ACI index out of range (0-256): <decimal>`.
    OutOfRange(String),
}

impl AciError {
    /// The reference's message text, as code points.
    pub fn message(&self) -> Vec<u32> {
        match self {
            Self::NotInteger(name) => {
                [cps("ACI index must be an integer, got "), name.clone()].concat()
            }
            Self::ByBlock => cps("ACI 0 is ByBlock, not a concrete colour"),
            Self::ByLayer => cps("ACI 256 is ByLayer, not a concrete colour"),
            Self::NotImplemented => cps("ACI 10-255 not implemented yet"),
            Self::OutOfRange(text) => cps(&format!("ACI index out of range (0-256): {text}")),
        }
    }
}

/// A `ValueError` from `rgb_to_aci`. The reference never raises any other exception type here.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RgbError {
    /// `rgb must be a 3-tuple, got <type>`.
    NotTupleOrList(Vec<u32>),
    /// `rgb must have exactly 3 components, got <n>`.
    WrongArity(usize),
    /// `rgb components must be numbers, got <type>`.
    NotNumber(Vec<u32>),
    /// `rgb component out of range (0-255): <value>`, with the value as Python `str()` prints it.
    OutOfRange(String),
}

impl RgbError {
    /// The reference's message text, as code points.
    pub fn message(&self) -> Vec<u32> {
        match self {
            Self::NotTupleOrList(name) => {
                [cps("rgb must be a 3-tuple, got "), name.clone()].concat()
            }
            Self::WrongArity(n) => cps(&format!("rgb must have exactly 3 components, got {n}")),
            Self::NotNumber(name) => {
                [cps("rgb components must be numbers, got "), name.clone()].concat()
            }
            Self::OutOfRange(text) => cps(&format!("rgb component out of range (0-255): {text}")),
        }
    }
}

/// The nine standard colours, index `i` holds ACI `i + 1`.
const STANDARD: [[u8; 3]; 9] = [
    [255, 0, 0],
    [255, 255, 0],
    [0, 255, 0],
    [0, 255, 255],
    [0, 0, 255],
    [255, 0, 255],
    [255, 255, 255],
    [128, 128, 128],
    [192, 192, 192],
];

/// `aci_to_rgb(i)`: the standard colour for ACI 1-9, otherwise the reference's refusal.
pub fn aci_to_rgb(index: Value) -> Result<[u8; 3], AciError> {
    match index {
        Value::Int(i) => match i {
            1..=9 => Ok(STANDARD[(i - 1) as usize]),
            0 => Err(AciError::ByBlock),
            256 => Err(AciError::ByLayer),
            10..=255 => Err(AciError::NotImplemented),
            _ => Err(AciError::OutOfRange(i.to_string())),
        },
        Value::BigInt(text) => Err(AciError::OutOfRange(text.to_string())),
        other => Err(AciError::NotInteger(type_name(other))),
    }
}

/// `rgb_to_aci(rgb)`: `(index, exact)`. `exact` is true only for an exact match to one of the nine
/// standard colours; otherwise the index is the NEAREST of 1-9 by Euclidean RGB distance.
pub fn rgb_to_aci(rgb: Rgb) -> Result<(u8, bool), RgbError> {
    let items = match rgb {
        Rgb::Items(items) => items,
        Rgb::Other(name) => return Err(RgbError::NotTupleOrList(name.to_vec())),
    };
    if items.len() != 3 {
        return Err(RgbError::WrongArity(items.len()));
    }
    let mut c = [0.0f64; 3];
    for (slot, item) in c.iter_mut().zip(items) {
        *slot = component(*item)?;
    }
    if let Some(i) = STANDARD.iter().position(|s| c == s.map(f64::from)) {
        return Ok((i as u8 + 1, true));
    }
    let mut best_idx = 1u8;
    let mut best_d = f64::INFINITY;
    for (i, s) in STANDARD.iter().enumerate() {
        let d = sq_diff(c[0] - f64::from(s[0]))
            + sq_diff(c[1] - f64::from(s[1]))
            + sq_diff(c[2] - f64::from(s[2]));
        if d < best_d {
            best_d = d;
            best_idx = i as u8 + 1;
        }
    }
    Ok((best_idx, false))
}

/// `x ** 2` as CPython computes it: a call to the C library `pow`. `powf` with an opaque exponent
/// keeps LLVM from folding it to `x * x`, which differs from `pow` in the last bit for some
/// inputs. Exposed so the differential driver can check it bit for bit.
pub fn sq_diff(x: f64) -> f64 {
    x.powf(std::hint::black_box(2.0))
}

fn component(value: Value) -> Result<f64, RgbError> {
    match value {
        Value::Int(n) if (0..=255).contains(&n) => Ok(n as f64),
        Value::Int(n) => Err(RgbError::OutOfRange(n.to_string())),
        Value::BigInt(text) => Err(RgbError::OutOfRange(text.to_string())),
        Value::Float(f) if (0.0..=255.0).contains(&f) => Ok(f),
        Value::Float(f) => Err(RgbError::OutOfRange(py_repr_float(f))),
        Value::Bool(_) | Value::Other(_) => Err(RgbError::NotNumber(type_name(value))),
    }
}

fn type_name(value: Value) -> Vec<u32> {
    match value {
        Value::Int(_) | Value::BigInt(_) => cps("int"),
        Value::Float(_) => cps("float"),
        Value::Bool(_) => cps("bool"),
        Value::Other(name) => name.to_vec(),
    }
}

fn cps(s: &str) -> Vec<u32> {
    s.chars().map(|c| c as u32).collect()
}

/// Python's `repr(float)`: shortest round-trip digits, fixed notation for decimal exponents in
/// `-4..16`, scientific otherwise. Copied from `printwidths::py_repr_float`, which is not
/// importable from this unregistered module.
fn py_repr_float(value: f64) -> String {
    if value.is_nan() {
        return "nan".to_string();
    }
    if value.is_infinite() {
        return if value > 0.0 {
            "inf".into()
        } else {
            "-inf".into()
        };
    }
    let sign = if value.is_sign_negative() { "-" } else { "" };
    let magnitude = value.abs();
    let shortest = format!("{:e}", magnitude);
    let shortest_digits = shortest
        .split_once('e')
        .expect("`{:e}` always has an exponent")
        .0;
    let digit_count = shortest_digits
        .chars()
        .filter(|c| c.is_ascii_digit())
        .count();
    // Shortest round-trip digits can tie between two candidates; CPython resolves the tie
    // half-to-even, which is the correctly rounded `digit_count`-digit form when it round-trips.
    let exact = format!("{:.*e}", digit_count - 1, magnitude);
    let formatted = if exact.parse::<f64>() == Ok(magnitude) {
        exact
    } else {
        shortest
    };
    let (mantissa, exponent) = formatted
        .split_once('e')
        .expect("`{:e}` always has an exponent");
    let exponent: i32 = exponent.parse().expect("exponent is an integer");
    let digits: String = mantissa.chars().filter(|&c| c != '.').collect();
    let decpt = exponent + 1;
    let body = if decpt <= -4 || decpt > 16 {
        let exp = decpt - 1;
        let mut text = digits[..1].to_string();
        if digits.len() > 1 {
            text.push('.');
            text.push_str(&digits[1..]);
        }
        format!(
            "{text}e{}{:02}",
            if exp < 0 { '-' } else { '+' },
            exp.unsigned_abs()
        )
    } else if decpt <= 0 {
        format!("0.{}{}", "0".repeat((-decpt) as usize), digits)
    } else if decpt as usize >= digits.len() {
        format!("{}{}.0", digits, "0".repeat(decpt as usize - digits.len()))
    } else {
        let split = decpt as usize;
        format!("{}.{}", &digits[..split], &digits[split..])
    };
    format!("{sign}{body}")
}
