//! Tagged-value binary decoder for the frozen popup golden corpus
//! (`data/popup28_golden.bin`).
//!
//! Local copy of `editor27_fixture.rs`, extended per the popup provenance
//! schema ("editor27_fixture.rs format + tag 3") with:
//!   * tag 3 = IEEE-754 binary64 float, 8 bytes little-endian,
//!   * `PartialEq` on `Value` (the corpus contains no NaN; floats compare
//!     bit-exactly).
//!
//!   0 = Null
//!   1 = False
//!   2 = True
//!   3 = Float (8 bytes LE)
//!   4 = UTF-8 string (u32 LE byte-length + bytes)
//!   5 = i128 (16 bytes LE)
//!   6 = Array (u32 LE element count + recursive elements)
//!   7 = Map (u32 LE pair count + recursive key/value pairs; keys are strings)
//!
//! Top level: `[source_commit: str, 900: int, popup: map{rows, steps}]`.

#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    Null,
    Bool(bool),
    I128(i128),
    F64(f64),
    Str(String),
    Array(Vec<Value>),
    Map(Vec<(String, Value)>),
}

impl Value {
    pub fn as_bool(&self) -> Option<bool> {
        match self {
            Value::Bool(b) => Some(*b),
            _ => None,
        }
    }
    pub fn as_i128(&self) -> Option<i128> {
        match self {
            Value::I128(i) => Some(*i),
            _ => None,
        }
    }
    #[allow(dead_code)]
    pub fn as_f64(&self) -> Option<f64> {
        match self {
            Value::F64(f) => Some(*f),
            _ => None,
        }
    }
    pub fn as_str(&self) -> Option<&str> {
        match self {
            Value::Str(s) => Some(s),
            _ => None,
        }
    }
    pub fn as_list(&self) -> Option<&[Value]> {
        match self {
            Value::Array(a) => Some(a),
            _ => None,
        }
    }
    /// Map lookup; panics if the receiver is not a map with `key`
    /// (use `get_opt` for keys that may be absent).
    pub fn get(&self, key: &str) -> &Value {
        match self {
            Value::Map(pairs) => pairs
                .iter()
                .find(|(k, _)| k == key)
                .map(|(_, v)| v)
                .unwrap_or_else(|| panic!("key '{}' not found in map", key)),
            _ => panic!("expected Map, got {:?}", std::mem::discriminant(self)),
        }
    }
    pub fn get_opt(&self, key: &str) -> Option<&Value> {
        match self {
            Value::Map(pairs) => pairs.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }
}

/// Parse a complete tagged-value binary blob. Panics on malformed input or
/// trailing bytes.
pub fn parse(data: &[u8]) -> Value {
    let mut pos = 0usize;
    let v = parse_value(data, &mut pos).expect("binary parse error");
    assert_eq!(
        pos,
        data.len(),
        "trailing bytes: parsed {} of {} bytes",
        pos,
        data.len()
    );
    v
}

fn parse_value(data: &[u8], pos: &mut usize) -> Result<Value, String> {
    if *pos >= data.len() {
        return Err("unexpected EOF at tag byte".into());
    }
    let tag = data[*pos];
    *pos += 1;
    Ok(match tag {
        0 => Value::Null,
        1 => Value::Bool(false),
        2 => Value::Bool(true),
        3 => {
            let b = data.get(*pos..*pos + 8).ok_or("truncated f64")?;
            let v = f64::from_bits(u64::from_le_bytes(b.try_into().unwrap()));
            *pos += 8;
            Value::F64(v)
        }
        4 => {
            let lb = data.get(*pos..*pos + 4).ok_or("truncated string length")?;
            let len = u32::from_le_bytes(lb.try_into().unwrap()) as usize;
            *pos += 4;
            let b = data.get(*pos..*pos + len).ok_or("truncated string bytes")?;
            let s = String::from_utf8(b.to_vec()).map_err(|e| format!("invalid UTF-8: {}", e))?;
            *pos += len;
            Value::Str(s)
        }
        5 => {
            let b = data.get(*pos..*pos + 16).ok_or("truncated i128")?;
            let v = i128::from_le_bytes(b.try_into().unwrap());
            *pos += 16;
            Value::I128(v)
        }
        6 => {
            let lb = data.get(*pos..*pos + 4).ok_or("truncated array length")?;
            let len = u32::from_le_bytes(lb.try_into().unwrap()) as usize;
            *pos += 4;
            let mut items = Vec::with_capacity(len.min(8192));
            for _ in 0..len {
                items.push(parse_value(data, pos)?);
            }
            Value::Array(items)
        }
        7 => {
            let lb = data.get(*pos..*pos + 4).ok_or("truncated map length")?;
            let len = u32::from_le_bytes(lb.try_into().unwrap()) as usize;
            *pos += 4;
            let mut pairs = Vec::with_capacity(len.min(8192));
            for _ in 0..len {
                let k = parse_value(data, pos)?;
                let v = parse_value(data, pos)?;
                let k = match k {
                    Value::Str(s) => s,
                    _ => return Err("map key must be a string".into()),
                };
                pairs.push((k, v));
            }
            Value::Map(pairs)
        }
        t => return Err(format!("unknown tag {}", t)),
    })
}
