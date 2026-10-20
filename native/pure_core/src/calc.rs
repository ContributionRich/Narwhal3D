//! Native calculator parser/evaluator (Q106-09).
//!
//! A transparent accelerator for `narwhal3d/calc.py::evaluate`. It implements
//! the SAME recursive-descent grammar as the Python reference (precedence,
//! left-associativity, unary signs, functions, angle/length units) and REUSES
//! the Q106-08 numparse number/length internals (`parse_signed_bare`,
//! `suffix_mm`, `model_mm`, `feet_inches_mm`) so the two cannot drift: a
//! number token is valued by the exact same code that values `parse_number`
//! and `parse_length`.
//!
//! Error contract: `CALC_OK` (0) with the value in the out-param on success;
//! `CALC_ERR` on any rejection. On a rejection the Python layer re-runs its
//! UNCHANGED evaluator, so the exact public error string (which embeds the
//! input, e.g. `"bad number at: %r"`) is always produced by the original code.
//! A fallback is therefore a correctness fallback, not a slower one: the
//! measured success path never touches the Python evaluator.
//!
//! Robustness bounds (a typed CAD formula is short): the input length, the
//! token count, and the parenthesis nesting depth are all capped. An over-limit
//! input is a rejection (→ Python fallback, which processes it correctly),
//! never a mis-parse and never an unbounded allocation/recursion.

use crate::numparse;

/// Success (the value is in the out-param).
pub const CALC_OK: i32 = 0;
/// A rejection (the exact error string comes from the Python fallback).
pub const CALC_ERR: i32 = -1;
/// `out` is null and a value was produced.
pub const CALC_OUT: i32 = -2;

/// A typed CAD formula is short; cap the input so the tokenizer is bounded.
const CALC_MAX_INPUT_BYTES: usize = 1024;
const CALC_MAX_MODEL_BYTES: usize = 64;
/// Cap the token count (a formula is a handful of tokens at most).
const CALC_MAX_TOKENS: usize = 512;
/// Cap parenthesis nesting (bounds the recursive-descent depth).
const CALC_MAX_DEPTH: usize = 64;

/// One token (mirrors `calc.py`: `("NUM", value)`, `("NAME", name)`, `(op,)`).
#[derive(Clone, PartialEq)]
enum Tok {
    Num(f64),
    Name(String),
    Op(char),
}

// ---------------------------------------------------------------------------
// Bare-number scanning (mirrors numparse's `_BARE` shape, but also returns the
// END index so the caller can find where the unit suffix begins).
// ---------------------------------------------------------------------------

fn is_digit_b(b: u8) -> bool {
    b.is_ascii_digit()
}

/// Consume an ASCII digit run from `i`; return the end index.
fn scan_digits(text: &str, i: usize) -> usize {
    let b = text.as_bytes();
    let n = b.len();
    let mut j = i;
    while j < n && is_digit_b(b[j]) {
        j += 1;
    }
    j
}

/// Match a bare number (`_BARE`) at `i`; return the end index, or `None`.
///
/// The shapes are tried in `_BARE` order: mixed (`\d+-\d+/\d+`), fraction
/// (`\d+/\d+`), then decimal/scientific. The mixed and fraction shapes matter
/// because `-` and `/` are also operators: `1-3/4` and `5/16` must be a single
/// number token, not the expression `1 - 3 / 4` or `5 / 16` (they happen to
/// coincide numerically, but the tokenization must match the reference).
fn scan_bare(text: &str, i: usize) -> Option<usize> {
    let b = text.as_bytes();
    let n = b.len();
    if i >= n {
        return None;
    }
    let c0 = b[i];
    if c0.is_ascii_digit() {
        let j1 = scan_digits(text, i);
        if j1 == i {
            return None;
        }
        // Shape 1: \d+-\d+/\d+  (a mixed number such as 1-3/4).
        if j1 < n && b[j1] == b'-' {
            let j2 = scan_digits(text, j1 + 1);
            if j2 > j1 + 1 && j2 < n && b[j2] == b'/' {
                let j3 = scan_digits(text, j2 + 1);
                if j3 > j2 + 1 {
                    return Some(j3);
                }
            }
        }
        // Shape 2: \d+/\d+  (a fraction such as 5/16).
        if j1 < n && b[j1] == b'/' {
            let j2 = scan_digits(text, j1 + 1);
            if j2 > j1 + 1 {
                return Some(j2);
            }
        }
        // Shape 3: \d+(?:\.\d+)?  (the integer part, optionally fractional).
        let mut j = j1;
        if j < n && b[j] == b'.' {
            let j2 = scan_digits(text, j + 1);
            if j2 > j + 1 {
                j = j2;
            }
            // else: a bare `1.` is not a valid decimal shape; the dot is left
            // unconsumed (the reference `(?:\.\d+)?` is optional).
        }
        // Shape 3 (cont.): optional scientific notation [eE][+-]?\d+.
        if j < n && (b[j] == b'e' || b[j] == b'E') {
            let mut k = j + 1;
            if k < n && (b[k] == b'+' || b[k] == b'-') {
                k += 1;
            }
            let k2 = scan_digits(text, k);
            if k2 > k {
                j = k2;
            }
        }
        return Some(j);
    }
    // Shape 3 (leading-dot): \.\d+  (e.g. .456).
    if c0 == b'.' {
        let j1 = scan_digits(text, i + 1);
        if j1 > i + 1 {
            let mut j = j1;
            if j < n && (b[j] == b'e' || b[j] == b'E') {
                let mut k = j + 1;
                if k < n && (b[k] == b'+' || b[k] == b'-') {
                    k += 1;
                }
                let k2 = scan_digits(text, k);
                if k2 > k {
                    j = k2;
                }
            }
            return Some(j);
        }
        return None;
    }
    None
}

/// The angle-unit factors (from `calc.py::_ANGLE_UNITS`), case-insensitive.
fn angle_factor(suffix: &str) -> Option<f64> {
    use std::f64::consts::PI;
    Some(match suffix.to_ascii_lowercase().as_str() {
        "d" | "degree" | "degrees" => PI / 180.0,
        "radian" | "radians" => 1.0,
        "grade" | "gradians" => PI / 200.0,
        _ => return None,
    })
}

/// A number token at `i` (feet-inches compound first, then a bare number with
/// an optional unit suffix). Returns `(value, end_index)` or `None` on a bad
/// number. Reuses the Q106-08 numparse internals so the value cannot drift
/// from `parse_number`/`parse_length`.
fn number_token(text: &str, i: usize, model: &str) -> Option<(f64, usize)> {
    let b = text.as_bytes();
    // Feet-inches compound (16'5", 1'2-3/4"). `feet_inches_mm` anchors the
    // closing quote at the END of the input; the reference's `_FEET_INCH_RE`
    // is left-anchored only, so a compound is also accepted mid-formula when
    // the character after the closing quote would end the number token there
    // (an operator, a digit, or end-of-input). A letter after it would extend
    // the token as a unit suffix in the reference, so the compound is refused
    // there and the bare-number path is tried instead. The native acceptance
    // set is therefore EXACTLY the reference's, never a superset (accepting
    // more would risk a divergence; accepting less is safe because the
    // Python fallback is unchanged).
    // The reference's `_FEET_INCH_RE` is left-anchored only, so a compound is
    // accepted mid-formula whenever the character after the closing quote would
    // END the number token there (an operator, a digit, or end-of-input). A
    // letter after it would extend the token as a unit suffix in the reference
    // (and the reference then rejects the whole thing), so the compound is
    // refused there and the bare-number path is tried instead. The native
    // acceptance set is therefore EXACTLY the reference's, never a superset
    // (accepting more would risk a divergence; accepting less is safe because
    // the Python fallback is unchanged).
    {
        let quote = b[i..].iter().position(|&c| c == b'\'');
        let inch = quote.and_then(|q| b[i + q + 1..].iter().position(|&c| c == b'"'));
        if let (Some(q), Some(inch)) = (quote, inch) {
            let after_inch = i + q + 1 + inch + 1;
            let next_ok = after_inch >= text.len() || {
                let ch = text[after_inch..].chars().next().unwrap();
                ch.is_ascii_digit() || matches!(ch, '+' | '-' | '*' | '/' | '(' | ')')
            };
            if next_ok {
                if let Some(mm) = numparse::feet_inches_mm(&text[i..after_inch]) {
                    let mu = numparse::model_mm(model)?;
                    return Some((mm / mu, after_inch));
                }
            }
        }
    }
    // A bare number plus an optional unit suffix.
    let bare_end = scan_bare(text, i)?;
    let bare = &text[i..bare_end];
    // The suffix is a run of letters / quote characters (a length unit may be
    // spelled `mm`, `in`, `'`, `"`, ...).
    let mut k = bare_end;
    let n = text.len();
    while k < n {
        let ch = text[k..].chars().next().unwrap();
        if ch.is_alphabetic() || ch == '"' || ch == '\'' {
            k += ch.len_utf8();
        } else {
            break;
        }
    }
    let suffix = &text[bare_end..k];
    let base = numparse::parse_signed_bare(bare)?;
    if suffix.is_empty() {
        return Some((base, k));
    }
    if let Some(factor) = angle_factor(suffix) {
        return Some((base * factor, k));
    }
    let smm = numparse::suffix_mm(suffix)?;
    let mu = numparse::model_mm(model)?;
    Some((base * smm / mu, k))
}

/// Tokenize a formula into a token list, mirroring `calc.py::_tokenize`.
fn tokenize(text: &str, model: &str) -> Result<Vec<Tok>, ()> {
    let n = text.len();
    let mut toks: Vec<Tok> = Vec::new();
    let mut i = 0usize;
    while i < n {
        let c = text[i..].chars().next().unwrap();
        // "no spaces in formulas" (checked per character, as in the reference).
        if c == ' ' || c == '\t' || c == '\n' || c == '\r' {
            return Err(());
        }
        if c == '+' || c == '-' || c == '*' || c == '/' || c == '(' || c == ')' || c == ',' {
            if toks.len() >= CALC_MAX_TOKENS {
                return Err(());
            }
            toks.push(Tok::Op(c));
            i += c.len_utf8();
            continue;
        }
        if c.is_ascii_digit() || c == '.' || c == '\'' || c == '"' {
            if toks.len() >= CALC_MAX_TOKENS {
                return Err(());
            }
            let (value, ni) = number_token(text, i, model).ok_or(())?;
            toks.push(Tok::Num(value));
            i = ni;
            continue;
        }
        if c.is_alphabetic() || c == '_' {
            if toks.len() >= CALC_MAX_TOKENS {
                return Err(());
            }
            let mut j = i;
            while j < n {
                let ch = text[j..].chars().next().unwrap();
                if ch.is_alphanumeric() || ch == '_' {
                    j += ch.len_utf8();
                } else {
                    break;
                }
            }
            toks.push(Tok::Name(text[i..j].to_string()));
            i = j;
            continue;
        }
        return Err(()); // "unexpected character: %r"
    }
    Ok(toks)
}

// ---------------------------------------------------------------------------
// Recursive-descent parser (the SAME grammar as `calc.py`:
//   expr := term (('+'|'-') term)*
//   term := factor (('*'|'/') factor)*
//   factor := ('+'|'-')* primary
//   primary := NUM | NAME | '(' expr ')'
// with a nesting-depth cap.
// ---------------------------------------------------------------------------

fn tok_op(tok: &Tok) -> Option<char> {
    match tok {
        Tok::Op(c) => Some(*c),
        _ => None,
    }
}

/// A function name's arity, or `None` when it is not a known function.
fn func_arity(name: &str) -> Option<usize> {
    Some(match name {
        "sin" | "cos" | "tan" | "asin" | "acos" | "atan" | "ln" | "log10" | "exp" | "sinh"
        | "cosh" | "tanh" | "sqrt" => 1,
        "atan2" | "pow" => 2,
        _ => return None,
    })
}

/// Apply a known function, mirroring `calc.py::_apply_func` (domain errors are
/// a rejection, never a NaN/inf leaking out).
fn apply_func(name: &str, a: &[f64]) -> Result<f64, ()> {
    match name {
        "sin" => Ok(a[0].sin()),
        "cos" => Ok(a[0].cos()),
        "tan" => Ok(a[0].tan()),
        "asin" => {
            if a[0].abs() > 1.0 {
                return Err(());
            }
            Ok(a[0].asin())
        }
        "acos" => {
            if a[0].abs() > 1.0 {
                return Err(());
            }
            Ok(a[0].acos())
        }
        "atan" => Ok(a[0].atan()),
        "atan2" => Ok(a[0].atan2(a[1])),
        "ln" => {
            if a[0] <= 0.0 {
                return Err(());
            }
            Ok(a[0].ln())
        }
        "log10" => {
            if a[0] <= 0.0 {
                return Err(());
            }
            Ok(a[0].log10())
        }
        "exp" => {
            let r = a[0].exp();
            if !r.is_finite() {
                return Err(());
            }
            Ok(r)
        }
        "sinh" => Ok(a[0].sinh()),
        "cosh" => Ok(a[0].cosh()),
        "tanh" => Ok(a[0].tanh()),
        "sqrt" => {
            if a[0] < 0.0 {
                return Err(());
            }
            Ok(a[0].sqrt())
        }
        "pow" => {
            // A negative base with a non-integer exponent is a domain error in
            // both languages (Python raises ValueError before computing; Rust's
            // powf returns NaN, which the finiteness check below would also
            // reject -- but the explicit check keeps the rejection visible).
            if a[0] < 0.0 && a[1].fract() != 0.0 {
                return Err(());
            }
            let r = a[0].powf(a[1]);
            if !r.is_finite() {
                return Err(());
            }
            Ok(r)
        }
        _ => Err(()),
    }
}

fn parse_expr(toks: &[Tok], i: usize, depth: usize) -> Result<(f64, usize), ()> {
    if depth > CALC_MAX_DEPTH {
        return Err(());
    }
    let (mut value, mut i) = parse_term(toks, i, depth)?;
    while i < toks.len() {
        match tok_op(&toks[i]) {
            Some('+') => {
                let (rhs, ni) = parse_term(toks, i + 1, depth)?;
                value += rhs;
                i = ni;
            }
            Some('-') => {
                let (rhs, ni) = parse_term(toks, i + 1, depth)?;
                value -= rhs;
                i = ni;
            }
            _ => break,
        }
    }
    Ok((value, i))
}

fn parse_term(toks: &[Tok], i: usize, depth: usize) -> Result<(f64, usize), ()> {
    let (mut value, mut i) = parse_factor(toks, i, depth)?;
    while i < toks.len() {
        match toks[i] {
            Tok::Op('*') => {
                let (rhs, ni) = parse_factor(toks, i + 1, depth)?;
                value *= rhs;
                i = ni;
            }
            Tok::Op('/') => {
                let (rhs, ni) = parse_factor(toks, i + 1, depth)?;
                if rhs == 0.0 {
                    return Err(()); // "division by zero"
                }
                value /= rhs;
                i = ni;
            }
            _ => break,
        }
    }
    Ok((value, i))
}

fn parse_factor(toks: &[Tok], i: usize, depth: usize) -> Result<(f64, usize), ()> {
    let mut sign = 1.0;
    let mut i = i;
    while i < toks.len() {
        match toks[i] {
            Tok::Op('+') => i += 1,
            Tok::Op('-') => {
                sign = -sign;
                i += 1;
            }
            _ => break,
        }
    }
    let (value, ni) = parse_primary(toks, i, depth)?;
    Ok((sign * value, ni))
}

fn parse_call(toks: &[Tok], i: usize, name: &str, depth: usize) -> Result<(f64, usize), ()> {
    let arity = func_arity(name).ok_or(())?; // "unknown function: %s"
    if i >= toks.len() || !matches!(toks[i], Tok::Op('(')) {
        return Err(()); // "%s requires parentheses"
    }
    let mut i = i + 1;
    let mut args: Vec<f64> = Vec::new();
    if i < toks.len() && matches!(toks[i], Tok::Op(')')) {
        return Err(()); // "%s requires at least one argument"
    }
    let (val, ni) = parse_expr(toks, i, depth)?;
    args.push(val);
    i = ni;
    while i < toks.len() && matches!(toks[i], Tok::Op(',')) {
        i += 1;
        let (val, ni) = parse_expr(toks, i, depth)?;
        args.push(val);
        i = ni;
    }
    if i >= toks.len() || !matches!(toks[i], Tok::Op(')')) {
        return Err(()); // "missing ) after %s"
    }
    i += 1;
    if args.len() != arity {
        return Err(()); // "%s takes %d argument(s), got %d"
    }
    let v = apply_func(name, &args)?;
    Ok((v, i))
}

fn parse_primary(toks: &[Tok], i: usize, depth: usize) -> Result<(f64, usize), ()> {
    if i >= toks.len() {
        return Err(()); // "expected an operand"
    }
    match &toks[i] {
        Tok::Num(v) => Ok((*v, i + 1)),
        Tok::Name(name) => {
            if name == "pi" || name == "\u{03c0}" {
                return Ok((std::f64::consts::PI, i + 1));
            }
            if func_arity(name).is_some() {
                return parse_call(toks, i + 1, name, depth);
            }
            Err(()) // "unknown name: %s"
        }
        Tok::Op('(') => {
            let (value, ni) = parse_expr(toks, i + 1, depth + 1)?;
            if ni >= toks.len() || !matches!(toks[ni], Tok::Op(')')) {
                return Err(()); // "unbalanced parentheses"
            }
            Ok((value, ni + 1))
        }
        Tok::Op(_) => Err(()), // "unexpected token: %r"
    }
}

/// Evaluate a formula, mirroring `calc.py::evaluate`'s parse phase.
/// `pub(crate)` so the Q106-11 coordinate parser reuses the exact same
/// arithmetic (fractions, unit suffixes, formulas) — the two cannot drift.
pub(crate) fn core_evaluate(text: &str, model: &str) -> Result<f64, ()> {
    if text.is_empty() {
        return Err(()); // "empty formula"
    }
    let toks = tokenize(text, model)?;
    if toks.is_empty() {
        return Err(()); // "empty formula"
    }
    let (value, pos) = parse_expr(&toks, 0, 0)?;
    if pos != toks.len() {
        return Err(()); // "unexpected token: %r"
    }
    if !value.is_finite() {
        return Err(()); // "result is not a finite number"
    }
    Ok(value)
}

/// Evaluate a the reference CAD application formula. `CALC_OK` + value in an
/// out-param on success; `CALC_ERR` on rejection (the Python layer re-runs its
/// unchanged evaluator to produce the exact error string).
///
/// # Safety
///
/// `text` must be readable for `text_len` bytes (null only when
/// `text_len == 0`), `model` must be readable for `model_len` bytes (null only
/// when `model_len == 0`), and `out` must be writable for one `f64` (null only
/// when the caller wants to read only the error code).
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_evaluate(
    text: *const u8,
    text_len: usize,
    model: *const u8,
    model_len: usize,
    out: *mut f64,
) -> i32 {
    let out = match unsafe { crate::checked_slice_mut(out, 1) } {
        Some(out) => &mut out[0],
        None => return CALC_OUT,
    };
    *out = 0.0;
    if text_len > CALC_MAX_INPUT_BYTES || model_len > CALC_MAX_MODEL_BYTES {
        return CALC_ERR;
    }
    let text_span = match unsafe { crate::checked_slice(text, text_len) } {
        Some(span) => span,
        None => return CALC_ERR,
    };
    let model_span = match unsafe { crate::checked_slice(model, model_len) } {
        Some(span) => span,
        None => return CALC_ERR,
    };
    let text = match std::str::from_utf8(text_span) {
        Ok(s) => s,
        Err(_) => return CALC_ERR,
    };
    let model = match std::str::from_utf8(model_span) {
        Ok(s) => s,
        Err(_) => return CALC_ERR,
    };
    match core_evaluate(text, model) {
        Ok(v) => {
            *out = v;
            CALC_OK
        }
        Err(_) => CALC_ERR,
    }
}

// ---------------------------------------------------------------------------
// Tests.
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn ev(s: &str, model: &str) -> (f64, i32) {
        let mut out = 0.0f64;
        let tb = s.as_bytes();
        let mb = model.as_bytes();
        let code = unsafe {
            narwhal_pure_core_evaluate(tb.as_ptr(), tb.len(), mb.as_ptr(), mb.len(), &mut out)
        };
        (out, code)
    }

    fn ok(s: &str, model: &str, expected: f64) {
        let (v, c) = ev(s, model);
        assert_eq!(c, CALC_OK, "{s} ({model}): {c}");
        assert!(
            (v - expected).abs() < 1e-9,
            "{s} ({model}): {v} != {expected}"
        );
    }

    fn err(s: &str, model: &str) {
        let (v, c) = ev(s, model);
        assert_ne!(c, CALC_OK, "{s} ({model}) should be an error");
        assert_eq!(v, 0.0, "{s} ({model}): out must be cleared");
    }

    #[test]
    fn arithmetic_and_precedence() {
        ok("1+2*3", "Millimeters", 7.0);
        ok("(1+2)*3", "Millimeters", 9.0);
        ok("10/4", "Millimeters", 2.5);
        ok("2-3-4", "Millimeters", -5.0);
        ok("-5+2", "Millimeters", -3.0);
        ok("+5", "Millimeters", 5.0);
        ok("--5", "Millimeters", 5.0);
        ok("5/16", "Millimeters", 5.0 / 16.0);
        ok("1-3/4+1", "Millimeters", 2.75);
        ok("pi", "Millimeters", std::f64::consts::PI);
        ok("1.23456e-3+1", "Millimeters", 1.00123456);
    }

    #[test]
    fn functions() {
        ok("sin(0)", "Millimeters", 0.0);
        ok("cos(pi)", "Millimeters", -1.0);
        ok("tan(pi/4)", "Millimeters", 1.0);
        ok("asin(1)", "Millimeters", std::f64::consts::PI / 2.0);
        ok("atan2(1,1)", "Millimeters", std::f64::consts::PI / 4.0);
        ok("ln(exp(1))", "Millimeters", 1.0);
        ok("log10(1000)", "Millimeters", 3.0);
        ok("sqrt(16)", "Millimeters", 4.0);
        ok("pow(2,10)", "Millimeters", 1024.0);
        ok("pow(9,1/2)", "Millimeters", 3.0);
    }

    #[test]
    fn angle_units() {
        ok("10*sin(30degrees)", "Millimeters", 5.0);
        ok("sin(pi/6)", "Millimeters", 0.5);
        ok("sin(30d)", "Millimeters", 0.5);
        ok("cos(90d)", "Millimeters", 0.0);
        ok("tan(45d)", "Millimeters", 1.0);
    }

    #[test]
    fn length_units() {
        ok("27cm", "Meters", 0.27);
        ok("27cm+10cm", "Meters", 0.37);
        ok("10m", "Millimeters", 10000.0);
        ok("1'6\"", "Feet", 1.5);
    }

    #[test]
    fn feet_inches_compound() {
        // A compound is accepted mid-formula when the next character would end
        // the number token (operator / digit / paren / end-of-input), matching
        // the reference's left-anchored `_FEET_INCH_RE` exactly.
        ok("16'5\"", "Millimeters", 5003.8);
        ok("16'5\"+1", "Millimeters", 5004.8);
        ok("10+2'6\"", "Millimeters", 772.0);
        ok("2.5'3\"", "Millimeters", 838.2);
        ok("1'2-3/4\"", "Millimeters", 374.65);
        ok("1'2-3/4\"+1", "Millimeters", 375.65);
        ok("(16'5\")", "Millimeters", 5003.8);
        ok("16'5\"*2", "Millimeters", 10007.6);
        // A letter after the closing quote is a unit suffix in the reference, so
        // the compound is refused and the whole token is a bad number.
        err("2'6\"mm", "Millimeters");
        err("2'6\"5", "Millimeters");
    }

    #[test]
    fn rejections() {
        err("1/0", "Millimeters");
        err("5/(3-3)", "Millimeters");
        err("sqrt(-1)", "Millimeters");
        err("asin(2)", "Millimeters");
        err("ln(0)", "Millimeters");
        err("acos(-2)", "Millimeters");
        err("abc", "Millimeters");
        err("(1+2", "Millimeters");
        err("1+2)", "Millimeters");
        err("1 + 2", "Millimeters");
        err("", "Millimeters");
        err("1+*2", "Millimeters");
        err("pow(2)", "Millimeters");
        err("foo(1)", "Millimeters");
        err("__import__('os')", "Millimeters");
    }

    #[test]
    fn over_limit_is_a_rejection() {
        // A formula longer than the input cap is a rejection (→ Python
        // fallback), never a mis-parse.
        let long = "1+".repeat(600);
        err(&long, "Millimeters");
        // Deeply nested parentheses exceed the nesting cap.
        let deep = format!("{}1{}", "(".repeat(80), ")".repeat(80));
        err(&deep, "Millimeters");
    }

    #[test]
    fn malformed_abi_spans_are_refused_and_output_is_cleared() {
        let mut out = 42.0f64;
        assert_eq!(
            unsafe {
                narwhal_pure_core_evaluate(
                    std::ptr::null(),
                    1,
                    b"Millimeters".as_ptr(),
                    11,
                    &mut out,
                )
            },
            CALC_ERR
        );
        assert_eq!(out, 0.0);

        out = 42.0;
        let dangling = std::ptr::NonNull::<u8>::dangling().as_ptr();
        assert_eq!(
            unsafe {
                narwhal_pure_core_evaluate(
                    dangling,
                    usize::MAX,
                    b"Millimeters".as_ptr(),
                    11,
                    &mut out,
                )
            },
            CALC_ERR
        );
        assert_eq!(out, 0.0);
        let mut storage = [0.0f64; 2];
        let misaligned = storage
            .as_mut_ptr()
            .cast::<u8>()
            .wrapping_add(1)
            .cast::<f64>();
        assert_eq!(
            unsafe {
                narwhal_pure_core_evaluate(
                    b"1".as_ptr(),
                    1,
                    b"Millimeters".as_ptr(),
                    11,
                    misaligned,
                )
            },
            CALC_OUT
        );
    }
}
